"""
hydrate_player_basic.py
-----------------------
Fills NULL values in player_stats_basic for three columns:
  • position        — all 3 407 rows NULL
  • gs              — all 3 407 rows NULL  (LeagueDashPlayerStats has no GS column)
  • years_in_league —     871 rows NULL    (undrafted / no draft_year in original fetch)

Optimised strategy
------------------
Phase 1 — 1 request:
    PlayerIndex(historical_nullable=1)
    Returns all 5 100+ historical NBA players with POSITION and FROM_YEAR.
    Covers all players in our 2020-26 dataset instantly.

Phase 2 — 1 request per unique player (~1 095 total):
    PlayerCareerStats  ->  GS per (season, team_id)
    Sleep 0.6-1.2 s between requests  (~15-20 min total runtime)

Fallback — basketball-reference scrape:
    Only triggered when PlayerCareerStats raises an exception.
    Also provides exact 5-position codes (PG/SG/SF/PF/C) if primary
    position mapping is generic.

Anti-bot
--------
• 0.6-1.2 s sleep between every PlayerCareerStats/bref request
• Rotating User-Agent pool for all web (requests) calls

Data integrity
--------------
• position  normalised to exactly PG / SG / SF / PF / C
  (PlayerIndex uses 3-position G/F/C codes; bref gives the exact 5-pos value)
• years_in_league = season_start_year - FROM_YEAR + 1  (floor 1)
• gs taken from PlayerCareerStats SeasonTotalsRegularSeason
• SQLite updated row-by-row -> progress saved on any interrupt

Summary of Updates printed to console at the end.

Modules
-------
  build_player_index()             -> dict {player_id: {position, from_year}}
  get_nba_api_data(player_id, ...) -> dict | None  (PlayerCareerStats for GS)
  get_bref_data(player_name, ...)  -> dict | None  (full fallback)
  update_sqlite(cur, ...)          -> int  (rows touched)
  run()                            -> orchestrates, prints summary
"""

import sqlite3
import time
import random
import math
import re
import os
import sys
import unicodedata

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests
from bs4 import BeautifulSoup

from nba_api.stats.endpoints import (
    PlayerIndex,
    PlayerCareerStats,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")
TIMEOUT = 90

SEASONS_IN_DB = {
    "2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26",
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPad; CPU OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
]

# PlayerIndex returns 3-category codes (G/F/C and combos).
# We map these to the 5-position system; bref fallback gives exact values.
POSITION_MAP: dict[str, str] = {
    "point guard":    "PG",
    "shooting guard": "SG",
    "small forward":  "SF",
    "power forward":  "PF",
    "center":         "C",
    "guard":          "SG",
    "forward":        "SF",
    "forward-center": "PF",
    "center-forward": "C",
    "guard-forward":  "SG",
    "forward-guard":  "SF",
    "pg": "PG", "sg": "SG", "sf": "SF", "pf": "PF", "c": "C",
    "g": "SG",  "f": "SF",
    "g-f": "SG", "f-g": "SF", "f-c": "PF", "c-f": "C",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def snooze() -> None:
    """0.6-1.2 s pause — short enough to finish fast, safe enough to avoid bans."""
    time.sleep(random.uniform(0.6, 1.2))


def safe_int(val) -> "int | None":
    try:
        f = float(val)
        return None if math.isnan(f) else int(f)
    except (TypeError, ValueError):
        return None


def season_start_year(season: str) -> int:
    return int(season.split("-")[0])


def normalize_position(raw: "str | None") -> "str | None":
    if not raw:
        return None
    key = str(raw).strip().lower()
    result = POSITION_MAP.get(key)
    if result:
        return result
    return POSITION_MAP.get(key.split()[0]) if key.split() else None


def ascii_name(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def bref_slug(player_name: str, attempt: int = 1) -> str:
    clean = ascii_name(player_name).lower()
    clean = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", clean).strip()
    parts = clean.split()
    if len(parts) < 2:
        return ""
    last  = re.sub(r"[^a-z]", "", parts[-1])[:5]
    first = re.sub(r"[^a-z]", "", parts[0])[:2]
    return f"{last}{first}{attempt:02d}"


def random_headers() -> dict:
    return {
        "User-Agent":      random.choice(USER_AGENTS),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer":         "https://www.basketball-reference.com/",
        "DNT":             "1",
    }


# ---------------------------------------------------------------------------
# Phase 1 — bulk position + from_year via PlayerIndex
# ---------------------------------------------------------------------------

def build_player_index() -> dict:
    """
    Single API call.  Returns {player_id: {"position": str|None, "from_year": int|None}}
    for every historical NBA player (5 100+ rows).
    """
    print("[Phase 1] PlayerIndex(historical_nullable=1) ...", flush=True)
    r  = PlayerIndex(historical_nullable=1, timeout=TIMEOUT)
    df = r.get_data_frames()[0]

    index: dict = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("PERSON_ID"))
        if pid is None:
            continue
        index[pid] = {
            "position":  normalize_position(str(row.get("POSITION") or "").strip()),
            "from_year": safe_int(row.get("FROM_YEAR")),
        }

    print(f"  -> {len(index)} players indexed", flush=True)
    return index


# ---------------------------------------------------------------------------
# Phase 2 primary — PlayerCareerStats for GS
# ---------------------------------------------------------------------------

def get_nba_api_data(player_id: int, player_name: str) -> "dict | None":
    """
    Calls PlayerCareerStats for one player.
    Returns {"gs_map": {(season, team_id): gs_int}} or None on failure.
    """
    try:
        r   = PlayerCareerStats(player_id=player_id, timeout=TIMEOUT)
        dfs = r.get_data_frames()
        if not dfs or dfs[0].empty:
            return None
        df     = dfs[0]   # SeasonTotalsRegularSeason
        gs_map = {}
        for _, row in df.iterrows():
            sid = str(row.get("SEASON_ID", "")).strip()
            if sid not in SEASONS_IN_DB:
                continue
            tid = safe_int(row.get("TEAM_ID"))
            gs  = safe_int(row.get("GS"))
            gs_map[(sid, tid)] = gs
        return {"gs_map": gs_map}
    except Exception as exc:
        print(f"      [PCS ERR] {player_name}: {exc}", flush=True)
        return None


# ---------------------------------------------------------------------------
# Fallback — basketball-reference (position + GS + from_year)
# ---------------------------------------------------------------------------

def get_bref_data(player_name: str) -> "dict | None":
    """
    Scrapes bref for position (exact 5-pos), GS per season, and from_year.
    Used only when PlayerCareerStats fails.
    Tries slug suffixes 01-03.
    """
    for attempt in range(1, 4):
        slug = bref_slug(player_name, attempt)
        if not slug:
            return None
        url = (
            f"https://www.basketball-reference.com"
            f"/players/{slug[0]}/{slug}.html"
        )
        try:
            snooze()
            resp = requests.get(url, headers=random_headers(), timeout=25)
        except requests.RequestException as exc:
            print(f"      [bref REQ] {url}: {exc}", flush=True)
            return None

        if resp.status_code == 404:
            continue
        if resp.status_code != 200:
            print(f"      [bref HTTP {resp.status_code}] {url}", flush=True)
            return None

        soup   = BeautifulSoup(resp.text, "html.parser")
        result = {"position": None, "from_year": None, "gs_map": {}}

        # Position
        info_div = soup.find("div", id="info")
        if info_div:
            for p_tag in info_div.find_all("p"):
                text = p_tag.get_text(" ", strip=True)
                if "Position:" in text:
                    pos_raw  = text.split("Position:")[-1].split("\u25aa")[0].strip()
                    position = normalize_position(pos_raw)
                    if not position:
                        position = normalize_position(pos_raw.split()[0])
                    result["position"] = position
                    break

        # GS + from_year from per_game table
        table = soup.find("table", id="per_game")
        if table:
            tbody = table.find("tbody")
            if tbody:
                for tr in tbody.find_all("tr", class_=lambda c: c != "thead"):
                    th = tr.find("th", {"data-stat": "season"})
                    if not th or not th.a:
                        continue
                    sid = th.a.text.strip()
                    if sid not in SEASONS_IN_DB:
                        continue
                    yr = safe_int(sid.split("-")[0])
                    if yr and (result["from_year"] is None or yr < result["from_year"]):
                        result["from_year"] = yr
                    gs_td = tr.find("td", {"data-stat": "gs"})
                    gs    = safe_int(gs_td.get_text(strip=True)) if gs_td else None
                    result["gs_map"][(sid, None)] = gs

        if result["position"] or result["from_year"] or result["gs_map"]:
            return result

    return None


# ---------------------------------------------------------------------------
# Atomic SQLite update
# ---------------------------------------------------------------------------

def update_sqlite(
    cur: sqlite3.Cursor,
    player_id: int,
    season: str,
    team_id: "int | None",
    *,
    position: "str | None" = None,
    gs: "int | None" = None,
    years_in_league: "int | None" = None,
) -> int:
    """
    Updates one row, touching only columns that are still NULL.
    Returns rowcount (0 or 1).
    """
    cur.execute(
        """
        UPDATE player_stats_basic
           SET position        = CASE WHEN position        IS NULL THEN ? ELSE position        END,
               gs              = CASE WHEN gs              IS NULL THEN ? ELSE gs              END,
               years_in_league = CASE WHEN years_in_league IS NULL THEN ? ELSE years_in_league END
         WHERE season    = ?
           AND player_id = ?
           AND (team_id  = ? OR (team_id IS NULL AND ? IS NULL))
        """,
        (position, gs, years_in_league, season, player_id, team_id, team_id),
    )
    return cur.rowcount


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run() -> None:
    print(f"[hydrate_player_basic] DB: {DB_PATH}", flush=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL;")
    cur = con.cursor()

    # ── Phase 1: bulk position + from_year ───────────────────────────────────
    player_index = build_player_index()

    # ── Pre-flight: which players still need work? ────────────────────────────
    cur.execute(
        """
        SELECT DISTINCT player_id, player_name
          FROM player_stats_basic
         WHERE position IS NULL
            OR gs IS NULL
            OR years_in_league IS NULL
         ORDER BY player_name
        """
    )
    targets       = cur.fetchall()
    total_players = len(targets)
    print(f"\n  Players to hydrate: {total_players}", flush=True)

    # Cache per-player season rows for quick lookup
    cur.execute(
        "SELECT season, player_id, team_id FROM player_stats_basic"
    )
    all_db_rows = cur.fetchall()

    # ── Counters ──────────────────────────────────────────────────────────────
    rows_updated    = 0
    fallback_count  = 0
    skip_count      = 0
    position_filled = 0
    gs_filled       = 0
    yil_filled      = 0

    # ── Phase 2: per-player GS loop ───────────────────────────────────────────
    for idx, (player_id, player_name) in enumerate(targets, 1):
        print(
            f"  [{idx:4d}/{total_players}] {player_name}",
            flush=True,
        )

        # Pull bio from the bulk index
        bio      = player_index.get(player_id, {})
        position = bio.get("position")
        from_year = bio.get("from_year")

        # Fetch GS from PlayerCareerStats
        snooze()
        gs_data = get_nba_api_data(player_id, player_name)

        # Fallback to bref only when PCS failed
        used_fallback = False
        if gs_data is None:
            print(f"      [fallback -> bref]", flush=True)
            bref = get_bref_data(player_name)
            if bref:
                used_fallback = True
                fallback_count += 1
                gs_data   = bref
                # bref also gives better exact position if our index was generic
                if not position and bref.get("position"):
                    position = bref["position"]
                if not from_year and bref.get("from_year"):
                    from_year = bref["from_year"]

        gs_map = gs_data.get("gs_map", {}) if gs_data else {}

        if not position and not from_year and not gs_map:
            print(f"      [SKIP] no data found", flush=True)
            skip_count += 1
            continue

        # Find all DB rows for this player
        player_rows = [(s, t) for s, p, t in all_db_rows if p == player_id]

        for season, team_id in player_rows:
            yil = None
            if from_year:
                yil = max(season_start_year(season) - from_year + 1, 1)

            # GS lookup: exact (season, team_id) first, then (season, None) for bref
            gs = gs_map.get((season, team_id))
            if gs is None:
                gs = gs_map.get((season, None))

            n = update_sqlite(
                cur, player_id, season, team_id,
                position=position,
                gs=gs,
                years_in_league=yil,
            )
            rows_updated += n
            if n:
                if position  is not None: position_filled += 1
                if gs        is not None: gs_filled        += 1
                if yil       is not None: yil_filled       += 1

        # Commit per-player so progress survives an interrupt
        con.commit()

    # ── Remaining NULLs ───────────────────────────────────────────────────────
    cur.execute("SELECT COUNT(*) FROM player_stats_basic WHERE position IS NULL OR TRIM(position)=''")
    null_pos = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM player_stats_basic WHERE gs IS NULL")
    null_gs = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM player_stats_basic WHERE years_in_league IS NULL")
    null_yil = cur.fetchone()[0]

    con.close()

    # ── Summary of Updates ────────────────────────────────────────────────────
    print("\n" + "=" * 58)
    print("  SUMMARY OF UPDATES")
    print("=" * 58)
    print(f"  Players processed              : {total_players:>6}")
    print(f"  Players skipped (no data)      : {skip_count:>6}")
    print(f"  Fallbacks used (bref)          : {fallback_count:>6}")
    print(f"  DB rows touched                : {rows_updated:>6}")
    print(f"    position cells filled        : {position_filled:>6}")
    print(f"    gs cells filled              : {gs_filled:>6}")
    print(f"    years_in_league cells filled : {yil_filled:>6}")
    print("-" * 58)
    print(f"  Remaining NULLs")
    print(f"    position                     : {null_pos:>6}")
    print(f"    gs                           : {null_gs:>6}")
    print(f"    years_in_league              : {null_yil:>6}")
    print("=" * 58)


if __name__ == "__main__":
    run()
