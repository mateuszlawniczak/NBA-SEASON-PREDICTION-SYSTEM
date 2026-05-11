"""
hydrate_rookie_signing_teams.py
--------------------------------
Fills drafting_signing_team for undrafted rookie_data rows where it is NULL.

Resolution order (first hit wins):
  1. Local DB — earliest season in player_stats_basic ∪ player_stats_basic_playoffs (no wait)
  2. NBA Stats PlayerCareerStats — earliest season row TEAM_ABBREVIATION
  3. Basketball Reference — search → player page → first regular-season team_abbr cell

Anti-bot: random pause only immediately before each NBA Stats or BBRef request
(not between rows that resolve from the local DB). Default gap 0.85–1.85 s.

commit() after each successful UPDATE. Idempotent — skips rows that already have a team.

Run fetch_rookie_data.ensure_schema first (automatic when running fetch_rookie_data).
"""

from __future__ import annotations

import re
import sys
import time
import random
import sqlite3
from urllib.parse import quote_plus, urlparse

import requests
from bs4 import BeautifulSoup

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import PlayerCareerStats

from fetch_rookie_data import DB_PATH, ensure_schema

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TIMEOUT = 60
# Seconds to wait before each outbound NBA Stats / BBRef call (not between local-SQL hits).
HTTP_PAUSE_RANGE = (0.85, 1.85)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# BBRef table abbreviations → NBA abbreviations used elsewhere in this project
BBREF_ABBR_TO_NBA = {
    "ATL": "ATL",
    "BOS": "BOS",
    "BRK": "BKN",
    "CHO": "CHA",
    "CHI": "CHI",
    "CLE": "CLE",
    "DAL": "DAL",
    "DEN": "DEN",
    "DET": "DET",
    "GSW": "GSW",
    "HOU": "HOU",
    "IND": "IND",
    "LAC": "LAC",
    "LAL": "LAL",
    "MEM": "MEM",
    "MIA": "MIA",
    "MIL": "MIL",
    "MIN": "MIN",
    "NOP": "NOP",
    "NYK": "NYK",
    "OKC": "OKC",
    "ORL": "ORL",
    "PHI": "PHI",
    "PHO": "PHX",
    "POR": "POR",
    "SAC": "SAC",
    "SAS": "SAS",
    "TOR": "TOR",
    "UTA": "UTA",
    "WAS": "WAS",
}

PLAYER_LINK_RE = re.compile(r"^/players/[a-z]/[a-z0-9]+\.html$", re.I)


def normalize_bbref_abbr(raw: str) -> str:
    s = raw.strip().upper()
    return BBREF_ABBR_TO_NBA.get(s, s)


def http_pause(tag: str) -> None:
    t = random.uniform(*HTTP_PAUSE_RANGE)
    print(f"    [wait] {t:.1f}s ({tag})", flush=True)
    time.sleep(t)


def first_team_player_career(player_id: int) -> str | None:
    """Earliest NBA regular-season team from PlayerCareerStats."""
    season_re = re.compile(r"^\d{4}-\d{2}$")
    try:
        pcs = PlayerCareerStats(
            player_id=player_id,
            timeout=TIMEOUT,
            league_id_nullable="00",
        )
        for df in pcs.get_data_frames():
            if df is None or df.empty:
                continue
            if "TEAM_ABBREVIATION" not in df.columns or "SEASON_ID" not in df.columns:
                continue
            sub = df.dropna(subset=["SEASON_ID"]).copy()
            if sub.empty:
                continue
            sub = sub.sort_values("SEASON_ID", kind="stable")
            for _, row in sub.iterrows():
                sid = row.get("SEASON_ID")
                if sid is None or not season_re.match(str(sid).strip()):
                    continue
                team = row.get("TEAM_ABBREVIATION")
                if team is None:
                    continue
                t = str(team).strip().upper()
                if not t or t == "TOT":
                    continue
                return t
    except Exception as exc:
        print(f"    [api-err] PlayerCareerStats pid={player_id}: {exc}", flush=True)
    return None


def first_team_local_db(con: sqlite3.Connection, player_id: int) -> str | None:
    row = con.execute(
        """
        SELECT team_abbr FROM (
            SELECT season AS s, team_abbr FROM player_stats_basic
            WHERE player_id = ?
              AND team_abbr IS NOT NULL AND TRIM(team_abbr) != ''
            UNION ALL
            SELECT season, team_abbr FROM player_stats_basic_playoffs
            WHERE player_id = ?
              AND team_abbr IS NOT NULL AND TRIM(team_abbr) != ''
        )
        ORDER BY s
        LIMIT 1
        """,
        (player_id, player_id),
    ).fetchone()
    if not row or not row[0]:
        return None
    return str(row[0]).strip().upper()


def _bbref_player_page_url(sess: requests.Session, player_name: str) -> str | None:
    q = quote_plus(player_name.strip())
    search_url = f"https://www.basketball-reference.com/search/search.fcgi?search={q}"
    try:
        r = sess.get(search_url, headers=HEADERS, timeout=25, allow_redirects=True)
        if r.status_code != 200:
            print(f"    [bbref] HTTP {r.status_code} search", flush=True)
            return None
        final = r.url or ""
        path = urlparse(final).path
        if "/players/" in path and path.endswith(".html"):
            return final.split("?", 1)[0]

        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].split("?", 1)[0]
            if PLAYER_LINK_RE.match(href):
                if href.startswith("http"):
                    return href
                return f"https://www.basketball-reference.com{href}"
    except Exception as exc:
        print(f"    [bbref] search error: {exc}", flush=True)
    return None


def first_team_bbref(sess: requests.Session, player_name: str) -> str | None:
    url = _bbref_player_page_url(sess, player_name)
    if not url:
        return None
    try:
        r = sess.get(url, headers=HEADERS, timeout=25)
        if r.status_code != 200:
            print(f"    [bbref] HTTP {r.status_code} player page", flush=True)
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        selectors = (
            "#regularSeason_stats tbody tr",
            "#regularSeason tbody tr",
            "table#totals_stats tbody tr",
            "table#per_game_stats tbody tr",
        )
        for sel in selectors:
            for tr in soup.select(sel):
                td = tr.find("td", attrs={"data-stat": "team_name_abbr"})
                if not td:
                    continue
                txt = td.get_text(strip=True)
                if not txt or txt.upper() in ("TM", "TOT"):
                    continue
                return normalize_bbref_abbr(txt)
    except Exception as exc:
        print(f"    [bbref] page error: {exc}", flush=True)
    return None


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    ensure_schema(con)

    rows = con.execute(
        """
        SELECT player_id, player_name FROM rookie_data
        WHERE COALESCE(is_drafted, 0) = 0
          AND (drafting_signing_team IS NULL OR TRIM(drafting_signing_team) = '')
        ORDER BY player_id
        """
    ).fetchall()

    n = len(rows)
    print(f"\n[hydrate_rookie_signing_teams] {n} undrafted row(s) missing signing team.\n",
          flush=True)

    sess = requests.Session()
    ok = miss = 0

    for i, (player_id, player_name) in enumerate(rows):
        team = first_team_local_db(con, player_id)
        src: str | None = "local_DB" if team else None

        if not team:
            http_pause("NBA Stats")
            team = first_team_player_career(player_id)
            if team:
                src = "PlayerCareerStats"

        if not team:
            http_pause("BBRef")
            team = first_team_bbref(sess, player_name)
            if team:
                src = "BBRef"

        if team:
            con.execute(
                """
                UPDATE rookie_data
                SET drafting_signing_team = ?
                WHERE player_id = ?
                """,
                (team, player_id),
            )
            con.commit()
            ok += 1
            print(
                f"  [SIGN] {player_name} → {team} ({src})  [{i + 1}/{n}]",
                flush=True,
            )
        else:
            miss += 1
            print(
                f"  [MISS] {player_name} (player_id={player_id})  [{i + 1}/{n}]",
                flush=True,
            )

    con.close()
    print(f"\n{'='*60}", flush=True)
    print(f"  DONE — filled: {ok}, still missing: {miss}", flush=True)


if __name__ == "__main__":
    main()
