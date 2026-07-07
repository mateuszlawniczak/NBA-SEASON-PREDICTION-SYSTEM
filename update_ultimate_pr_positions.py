"""
update_ultimate_pr_positions.py
-------------------------------
Adds `mapped_position` (G / F / C) to ULTIMATE_PR via local join, NBA API, and
Sports-Reference CBB search fallback. Never drops ULTIMATE_PR — only ALTER + UPDATE.
"""

from __future__ import annotations

import os
import re
import sys
import time
import sqlite3
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

from season_utils import SeasonPair, parse_cli_seasons

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import CommonPlayerInfo
from nba_api.stats.static import players as nba_static_players

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nba_data.db")

NBA_API_SLEEP_SEC = 1.5
BBREF_SLEEP_SEC = 2.0
NBA_TIMEOUT = 60

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def normalize_whitespace(s: str) -> str:
    return " ".join(s.split())


def extract_primary_position_segment(raw: str) -> str:
    """Use first segment before '-' or '/' (NBA-style combos like PG-SG)."""
    s = normalize_whitespace(raw.strip())
    if not s:
        return ""
    cut_idx: list[int] = []
    for sep in "-", "/":
        i = s.find(sep)
        if i != -1:
            cut_idx.append(i)
    if cut_idx:
        s = s[: min(cut_idx)].strip()
    return s


def map_raw_position_to_gfc(raw: str | None) -> str | None:
    """
    Map any reasonable NBA/college position string strictly to 'G', 'F', or 'C'.
    """
    if raw is None:
        return None
    primary = extract_primary_position_segment(raw)
    if not primary:
        return None

    u = primary.upper()
    u_compact = re.sub(r"[\s\-_/]+", "", u)
    if u_compact in ("PG", "SG"):
        return "G"
    if u_compact in ("SF", "PF"):
        return "F"
    if u_compact == "C":
        return "C"

    # Explicit abbreviations (substring-safe order)
    if re.search(r"\b(PG|SG|POINT\s*GUARD|SHOOTING\s*GUARD)\b", u):
        return "G"
    if re.search(r"\b(SF|PF|SMALL\s*FORWARD|POWER\s*FORWARD)\b", u):
        return "F"
    if re.search(r"\b(C|CENTER)\b", u) or u_compact == "C":
        return "C"

    # Tokens (comma or space separated)
    tokens = re.split(r"[\s,]+", u.strip())
    for t in tokens:
        t = t.strip(".,")
        if not t:
            continue
        if t in ("PG", "SG"):
            return "G"
        if t in ("SF", "PF"):
            return "F"
        if t == "C":
            return "C"

    # Single-word guards / forwards / center
    if "GUARD" in u and "FORWARD" not in u:
        return "G"
    if "FORWARD" in u and "GUARD" not in u:
        return "F"
    if "CENTER" in u:
        return "C"

    # Bare "G", "F" sometimes appears
    if u.strip() in ("G", "GUARD"):
        return "G"
    if u.strip() in ("F", "FORWARD"):
        return "F"

    return None


def ultimate_pr_columns(con: sqlite3.Connection) -> set[str]:
    cur = con.cursor()
    cur.execute("PRAGMA table_info(ULTIMATE_PR)")
    return {row[1] for row in cur.fetchall()}


def ensure_mapped_position_column(con: sqlite3.Connection) -> None:
    cols = ultimate_pr_columns(con)
    if "mapped_position" in cols:
        print("[schema] Column mapped_position already exists — skipping ALTER.", flush=True)
        return
    con.execute("ALTER TABLE ULTIMATE_PR ADD COLUMN mapped_position TEXT")
    con.commit()
    print("[schema] Added column mapped_position (TEXT).", flush=True)


def pick_nba_player_match(matches: list[dict]) -> dict | None:
    if not matches:
        return None
    active = [m for m in matches if m.get("is_active")]
    return active[0] if active else matches[0]


def fetch_position_nba_api(player_name: str) -> str | None:
    try:
        matches = nba_static_players.find_players_by_full_name(player_name)
    except Exception as exc:
        print(f"    [nba_api] find_players_by_full_name error for {player_name!r}: {exc}", flush=True)
        return None

    pick = pick_nba_player_match(matches or [])
    if pick is None:
        return None

    pid = pick.get("id")
    if pid is None:
        return None

    try:
        r = CommonPlayerInfo(player_id=pid, timeout=NBA_TIMEOUT)
        df = r.get_data_frames()[0]
        if df is None or df.empty:
            return None
        raw = df.iloc[0].get("POSITION")
        if raw is None:
            return None
        return map_raw_position_to_gfc(str(raw))
    except Exception as exc:
        print(f"    [nba_api] CommonPlayerInfo error id={pid}: {exc}", flush=True)
        return None


def parse_position_from_cbb_player_html(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for p in soup.find_all("p"):
        strong = p.find("strong")
        if strong is None:
            continue
        label = strong.get_text(strip=True).rstrip(":").lower()
        if label != "position":
            continue
        rest_parts: list[str] = []
        for node in p.children:
            if getattr(node, "name", None) == "strong":
                continue
            if isinstance(node, str):
                t = node.strip()
                if t:
                    rest_parts.append(t)
            else:
                t = node.get_text(strip=True)
                if t:
                    rest_parts.append(t)
        blob = normalize_whitespace(" ".join(rest_parts))
        if not blob:
            blob = normalize_whitespace(p.get_text(strip=True))
            if ":" in blob:
                blob = blob.split(":", 1)[1].strip()
        mapped = map_raw_position_to_gfc(blob)
        if mapped:
            return mapped
    return None


def is_cbb_player_page_url(url: str) -> bool:
    return "/cbb/players/" in url and "search.fcgi" not in url


def fetch_position_bbref_cbb(player_name: str) -> str | None:
    """
    GET Sports-Reference CBB search; follow landing player page or first player hit.
    """
    q = quote_plus(player_name)
    url = f"https://www.sports-reference.com/cbb/search/search.fcgi?search={q}"
    try:
        resp = requests.get(
            url,
            headers=REQUEST_HEADERS,
            timeout=45,
            allow_redirects=True,
        )
        resp.raise_for_status()
    except Exception as exc:
        print(f"    [bbref] request failed for {player_name!r}: {exc}", flush=True)
        return None

    final = resp.url or ""
    html = resp.text or ""

    if is_cbb_player_page_url(final):
        return parse_position_from_cbb_player_html(html)

    soup = BeautifulSoup(html, "html.parser")
    for a in soup.select('a[href*="/cbb/players/"]'):
        href = a.get("href") or ""
        if "/cbb/players/" not in href:
            continue
        if href.startswith("/"):
            href = f"https://www.sports-reference.com{href}"
        try:
            pr = requests.get(href, headers=REQUEST_HEADERS, timeout=45)
            pr.raise_for_status()
            mapped = parse_position_from_cbb_player_html(pr.text)
            if mapped:
                return mapped
        except Exception as exc:
            print(f"    [bbref] follow-up GET failed {href}: {exc}", flush=True)
        break

    return None


def players_missing_position(con: sqlite3.Connection, target_season: str) -> list[str]:
    cur = con.cursor()
    rows = cur.execute(
        """
        SELECT player_name FROM ULTIMATE_PR
        WHERE season = ?
          AND (mapped_position IS NULL OR TRIM(mapped_position) = '')
        ORDER BY player_name
        """,
        (target_season,),
    ).fetchall()
    return [r[0] for r in rows]


def main(source_season: str | None = None, target_season: str | None = None) -> None:
    if source_season is None or target_season is None:
        pair: SeasonPair = parse_cli_seasons()
        source_season = pair.source
        target_season = pair.target
    _ = source_season

    con = sqlite3.connect(DB_PATH)
    try:
        ensure_mapped_position_column(con)

        cur = con.cursor()
        cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='player_positions' LIMIT 1"
        )
        has_pp = cur.fetchone() is not None

        updated_local: list[str] = []
        if has_pp:
            cur.execute(
                """
                SELECT u.player_name
                FROM ULTIMATE_PR u
                INNER JOIN player_positions p
                  ON p.player_name = u.player_name AND p.season = ?
                WHERE u.season = ?
                  AND (u.mapped_position IS NULL OR TRIM(IFNULL(u.mapped_position, '')) = '')
                """,
                (source_season, target_season),
            )
            updated_local = [r[0] for r in cur.fetchall()]

            cur.execute(
                """
                UPDATE ULTIMATE_PR
                SET mapped_position = (
                    SELECT p.mapped_position
                    FROM player_positions p
                    WHERE p.player_name = ULTIMATE_PR.player_name
                      AND p.season = ?
                    LIMIT 1
                )
                WHERE season = ?
                  AND (
                    mapped_position IS NULL OR TRIM(IFNULL(mapped_position, '')) = ''
                )
                AND EXISTS (
                    SELECT 1 FROM player_positions p
                    WHERE p.player_name = ULTIMATE_PR.player_name
                      AND p.season = ?
                )
                """,
                (source_season, target_season, source_season),
            )
            con.commit()
        else:
            print(
                "[warn] Table player_positions not found — skipping local JOIN fast-path.",
                flush=True,
            )

        missing_after_local = players_missing_position(con, target_season)

        nba_ok: list[str] = []
        bbref_ok: list[str] = []

        for name in missing_after_local:
            mapped = fetch_position_nba_api(name)
            time.sleep(NBA_API_SLEEP_SEC)
            if mapped:
                cur.execute(
                    "UPDATE ULTIMATE_PR SET mapped_position = ? WHERE player_name = ? AND season = ?",
                    (mapped, name, target_season),
                )
                con.commit()
                nba_ok.append(name)
                continue

            mapped = fetch_position_bbref_cbb(name)
            time.sleep(BBREF_SLEEP_SEC)
            if mapped:
                cur.execute(
                    "UPDATE ULTIMATE_PR SET mapped_position = ? WHERE player_name = ? AND season = ?",
                    (mapped, name, target_season),
                )
                con.commit()
                bbref_ok.append(name)

        still_missing = players_missing_position(con, target_season)

        print("\n" + "=" * 60, flush=True)
        print("ULTIMATE_PR mapped_position — run summary", flush=True)
        print("=" * 60, flush=True)

        print(f"\n[1] Local DB (joined player_positions): {len(updated_local)} player(s)", flush=True)
        for n in updated_local:
            print(f"    - {n}", flush=True)

        print(f"\n[2] NBA API (CommonPlayerInfo): {len(nba_ok)} player(s)", flush=True)
        for n in nba_ok:
            print(f"    - {n}", flush=True)

        print(f"\n[3] Basketball Reference (CBB search / scrape): {len(bbref_ok)} player(s)", flush=True)
        for n in bbref_ok:
            print(f"    - {n}", flush=True)

        print(f"\n[4] Still missing mapped_position: {len(still_missing)} player(s)", flush=True)
        if still_missing:
            for n in still_missing:
                print(f"    - {n}", flush=True)
        else:
            print("    (none)", flush=True)

        print("\nDone.", flush=True)
    finally:
        con.close()


if __name__ == "__main__":
    main()
