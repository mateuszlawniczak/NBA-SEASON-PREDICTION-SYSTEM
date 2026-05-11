"""
fetch_coach_data.py
-------------------
Builds and hydrates the coach_data table.

Source list: DISTINCT season, team_id, team_abbr from team_stats (180 rows).

For each row:
  1. Call CommonTeamRoster (NBA API) — extract rows where COACH_TYPE == 'Head Coach'.
  2. If multiple head coaches found (mid-season change), join with ' / '.
  3. Fallback: scrape Basketball Reference team page for that season if API fails.

Safety: time.sleep(random.uniform(4.5, 8.2)) between every API / scrape call.
Fully resumable — already completed (season, team_id) pairs are skipped.
commit() after every season.
"""

import os
import sys
import time
import random
import sqlite3

import requests
from bs4 import BeautifulSoup

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import CommonTeamRoster

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")
TIMEOUT = 60

# Basketball Reference team abbreviation map (bbref abbr → team_abbr in our DB)
# Used to build the fallback URL.
BBREF_ABBR = {
    "ATL": "ATL", "BOS": "BOS", "BKN": "BRK", "CHA": "CHO", "CHI": "CHI",
    "CLE": "CLE", "DAL": "DAL", "DEN": "DEN", "DET": "DET", "GSW": "GSW",
    "HOU": "HOU", "IND": "IND", "LAC": "LAC", "LAL": "LAL", "MEM": "MEM",
    "MIA": "MIA", "MIL": "MIL", "MIN": "MIN", "NOP": "NOP", "NYK": "NYK",
    "OKC": "OKC", "ORL": "ORL", "PHI": "PHI", "PHX": "PHO", "POR": "POR",
    "SAC": "SAC", "SAS": "SAS", "TOR": "TOR", "UTA": "UTA", "WAS": "WAS",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def snooze(note: str = "") -> None:
    t = random.uniform(4.5, 8.2)
    tag = f" [{note}]" if note else ""
    print(f"    [wait] {t:.1f}s{tag}", flush=True)
    time.sleep(t)


def season_start_year(season: str) -> int:
    """'2023-24' → 2023"""
    return int(season.split("-")[0])


# ---------------------------------------------------------------------------
# NBA API fetch
# ---------------------------------------------------------------------------

def fetch_via_api(team_id: int, season: str) -> "str | None":
    r = CommonTeamRoster(team_id=team_id, season=season, timeout=TIMEOUT)
    coaches_df = r.get_data_frames()[1]
    if coaches_df is None or coaches_df.empty:
        return None

    head_coaches = coaches_df[coaches_df["COACH_TYPE"] == "Head Coach"]
    if head_coaches.empty:
        # Fallback: IS_ASSISTANT == 1 sometimes used instead
        head_coaches = coaches_df[coaches_df["IS_ASSISTANT"] == 1]
    if head_coaches.empty:
        return None

    names = head_coaches["COACH_NAME"].dropna().str.strip().tolist()
    return " / ".join(names) if names else None


# ---------------------------------------------------------------------------
# Basketball Reference fallback
# ---------------------------------------------------------------------------

def fetch_via_bbref(team_abbr: str, season: str) -> "str | None":
    """
    Scrapes the BBRef team page for the given season.
    URL pattern: https://www.basketball-reference.com/teams/BOS/2024.html
    The start year of the season is used (2023-24 → 2024 because BBRef uses end year).
    """
    bbref = BBREF_ABBR.get(team_abbr)
    if not bbref:
        return None

    end_year = season_start_year(season) + 1       # 2023-24 → 2024
    url = f"https://www.basketball-reference.com/teams/{bbref}/{end_year}.html"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            print(f"    [bbref] HTTP {resp.status_code} for {url}", flush=True)
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # BBRef shows "Head Coach: NAME" in a <p> tag inside #info
        for p in soup.select("#info p"):
            text = p.get_text(" ", strip=True)
            if "Head Coach:" in text:
                coach = text.split("Head Coach:")[-1].strip().split("\n")[0].strip()
                return coach if coach else None

        return None

    except Exception as exc:
        print(f"    [bbref] Error: {exc}", flush=True)
        return None


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

CREATE_COACH_DATA = """
CREATE TABLE IF NOT EXISTS coach_data (
    season      TEXT    NOT NULL,
    team_id     INTEGER NOT NULL,
    team_abbr   TEXT    NOT NULL,
    coach_name  TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (season, team_id)
);
"""

UPSERT_SQL = """
INSERT OR REPLACE INTO coach_data (season, team_id, team_abbr, coach_name)
VALUES (:season, :team_id, :team_abbr, :coach_name)
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def get_targets(con: sqlite3.Connection) -> list[tuple]:
    """All distinct (season, team_id, team_abbr) from team_stats."""
    rows = con.execute(
        "SELECT DISTINCT season, team_id, team_abbr FROM team_stats ORDER BY season, team_abbr"
    ).fetchall()
    return rows


def get_done(con: sqlite3.Connection) -> set[tuple]:
    rows = con.execute(
        "SELECT season, team_id FROM coach_data WHERE coach_name IS NOT NULL"
    ).fetchall()
    return {(r[0], r[1]) for r in rows}


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    con.execute(CREATE_COACH_DATA)
    con.commit()

    targets  = get_targets(con)
    done     = get_done(con)
    pending  = [(s, tid, abbr) for s, tid, abbr in targets if (s, tid) not in done]
    total    = len(targets)

    print(
        f"\n[fetch_coach_data] {total} total rows, "
        f"{len(done)} already done, {len(pending)} to fetch.\n",
        flush=True,
    )

    cur             = con.cursor()
    current_season  = None
    success = errors = api_ok = bbref_ok = 0

    for i, (season, team_id, team_abbr) in enumerate(pending, 1):

        # Commit after each season flips
        if current_season and season != current_season:
            con.commit()
            print(f"  [commit] season {current_season} done.", flush=True)
        current_season = season

        coach_name = None
        source     = "—"

        # --- attempt 1: NBA API ---
        try:
            coach_name = fetch_via_api(team_id, season)
            if coach_name:
                source = "API"
                api_ok += 1
        except Exception as exc:
            print(f"    [api-err] {season} {team_abbr}: {exc}", flush=True)

        snooze(f"{season} {team_abbr} next")

        # --- attempt 2: Basketball Reference ---
        if not coach_name:
            try:
                coach_name = fetch_via_bbref(team_abbr, season)
                if coach_name:
                    source = "BBRef"
                    bbref_ok += 1
            except Exception as exc:
                print(f"    [bbref-err] {season} {team_abbr}: {exc}", flush=True)

            if not coach_name:
                errors += 1
                print(
                    f"  [MISS] {i}/{len(pending)}  {season} {team_abbr} — no coach found",
                    flush=True,
                )
            else:
                snooze(f"after bbref {team_abbr}")

        if coach_name:
            cur.execute(UPSERT_SQL, {
                "season":     season,
                "team_id":    team_id,
                "team_abbr":  team_abbr,
                "coach_name": coach_name,
            })
            success += 1
            print(
                f"  [COACH DATA] {season} {team_abbr:<4} - Coach: {coach_name:<30} "
                f"({source})  [{i}/{len(pending)}]",
                flush=True,
            )

    # Final commit
    con.commit()
    con.close()

    print(f"\n{'='*65}", flush=True)
    print(
        f"  DONE — {success} inserted, {errors} missing  "
        f"(API: {api_ok}, BBRef: {bbref_ok})",
        flush=True,
    )

    # Sanity check
    con2 = sqlite3.connect(DB_PATH)
    cur2 = con2.cursor()
    cur2.execute("SELECT COUNT(*), COUNT(coach_name) FROM coach_data")
    r = cur2.fetchone()
    con2.close()
    print(f"  DB — total rows: {r[0]}, rows with coach: {r[1]}", flush=True)


if __name__ == "__main__":
    main()
