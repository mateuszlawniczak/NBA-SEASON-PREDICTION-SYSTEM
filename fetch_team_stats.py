"""
fetch_team_stats.py
-------------------
Fills team_stats in nba_data.db for seasons 2020-21 through 2025-26.

API calls per season (4 total):
  1. LeagueDashTeamStats  — PerGame Base       -> pts_per_game, wins, losses, win_pct
  2. LeagueDashTeamStats  — PerGame Opponent   -> opp_pts_per_game (OPP_PTS)
  3. LeagueDashTeamStats  — PerGame Advanced   -> off_rating, def_rating, net_rating,
                                                  adj_off/def/net (E_ prefix), pace
  4. LeagueStandingsV3                         -> conference_seed, made_playoffs

Team abbreviations come from nba_api's static teams module (no extra request).
Random delay of 4-8 s between every API request to stay under rate limits.

prev_playoff_result is left NULL — can be backfilled separately.
"""

import sqlite3
import time
import random
import math
import os
import sys

# Force UTF-8 on Windows consoles so ASCII-only print() never fails
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import (
    LeagueDashTeamStats,
    LeagueStandingsV3,
)
from nba_api.stats.static import teams as nba_teams

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

SEASONS = [
    "2020-21",
    "2021-22",
    "2022-23",
    "2023-24",
    "2024-25",
    "2025-26",
]

TIMEOUT = 90   # seconds per request before giving up

# Static team_id -> abbreviation map (no network request needed)
TEAM_ABBR: dict[int, str] = {
    t["id"]: t["abbreviation"]
    for t in nba_teams.get_teams()
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def snooze(label: str = "") -> None:
    """Sleep a random 4-8 seconds between API requests."""
    t = random.uniform(4.0, 8.0)
    tag = f" [{label}]" if label else ""
    print(f"    [wait]  sleeping {t:.1f}s{tag} ...", flush=True)
    time.sleep(t)


def safe_float(val):
    """Return float or None for NaN / None values."""
    try:
        f = float(val)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def safe_int(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Fetch functions
# ---------------------------------------------------------------------------

def fetch_base(season: str) -> dict:
    """
    LeagueDashTeamStats PerGame Base.
    Key: team_id
    Returns: team_name, wins, losses, win_pct, pts_per_game.
    Note: abbreviation is pulled from the static TEAM_ABBR dict.
    """
    print(f"    -> Base PerGame ...", flush=True)
    r = LeagueDashTeamStats(
        season=season,
        per_mode_detailed="PerGame",
        measure_type_detailed_defense="Base",
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    out = {}
    for _, row in df.iterrows():
        tid = safe_int(row["TEAM_ID"])
        out[tid] = {
            "team_name":    str(row["TEAM_NAME"]),
            "team_abbr":    TEAM_ABBR.get(tid, ""),
            "wins":         safe_int(row["W"]),
            "losses":       safe_int(row["L"]),
            "win_pct":      safe_float(row["W_PCT"]),
            "pts_per_game": safe_float(row["PTS"]),
        }
    return out


def fetch_opponent(season: str) -> dict:
    """
    LeagueDashTeamStats PerGame Opponent.
    Key: team_id
    Returns: opp_pts_per_game.
    """
    print(f"    -> Opponent PerGame ...", flush=True)
    r = LeagueDashTeamStats(
        season=season,
        per_mode_detailed="PerGame",
        measure_type_detailed_defense="Opponent",
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    out = {}
    for _, row in df.iterrows():
        tid = safe_int(row["TEAM_ID"])
        out[tid] = {
            "opp_pts_per_game": safe_float(row.get("OPP_PTS")),
        }
    return out


def fetch_advanced(season: str) -> dict:
    """
    LeagueDashTeamStats PerGame Advanced.
    Key: team_id
    Returns: off/def/net ratings (raw + estimated/adjusted), pace.
    """
    print(f"    -> Advanced PerGame ...", flush=True)
    r = LeagueDashTeamStats(
        season=season,
        per_mode_detailed="PerGame",
        measure_type_detailed_defense="Advanced",
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    out = {}
    for _, row in df.iterrows():
        tid = safe_int(row["TEAM_ID"])
        out[tid] = {
            "off_rating":     safe_float(row.get("OFF_RATING")),
            "def_rating":     safe_float(row.get("DEF_RATING")),
            "net_rating":     safe_float(row.get("NET_RATING")),
            "adj_off_rating": safe_float(row.get("E_OFF_RATING")),
            "adj_def_rating": safe_float(row.get("E_DEF_RATING")),
            "adj_net_rating": safe_float(row.get("E_NET_RATING")),
            "pace":           safe_float(row.get("PACE")),
        }
    return out


def fetch_standings(season: str) -> dict:
    """
    LeagueStandingsV3.
    Key: team_id
    Returns: conference_seed (PlayoffRank), made_playoffs flag.
    made_playoffs = 1 if conference seed <= 8.
    """
    print(f"    -> LeagueStandingsV3 ...", flush=True)
    r = LeagueStandingsV3(
        season=season,
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    out = {}
    for _, row in df.iterrows():
        tid = safe_int(row.get("TeamID"))
        if tid is None:
            continue
        seed = safe_int(row.get("PlayoffRank"))
        made = 1 if seed is not None and seed <= 8 else 0
        out[tid] = {
            "conference_seed": seed,
            "made_playoffs":   made,
        }
    return out


# ---------------------------------------------------------------------------
# Merge + upsert
# ---------------------------------------------------------------------------

def merge_season(
    season: str,
    prev_season_str: str,
    prev_standings: dict,
) -> tuple[list[dict], dict]:
    """
    Run all 4 fetches with delays, merge into one record list keyed by team_id.
    Returns (records, current_standings) so caller can chain prev data.
    """
    base     = fetch_base(season);     snooze("next: opponent")
    opp      = fetch_opponent(season); snooze("next: advanced")
    adv      = fetch_advanced(season); snooze("next: standings")
    standing = fetch_standings(season); snooze("season done — writing DB")

    all_tids = set(base) | set(adv) | set(standing)
    records = []

    for tid in all_tids:
        b  = base.get(tid, {})
        o  = opp.get(tid, {})
        a  = adv.get(tid, {})
        s  = standing.get(tid, {})
        ps = prev_standings.get(tid, {})

        rec = {
            "season":    season,
            "team_id":   tid,
            "team_name": b.get("team_name", ""),
            "team_abbr": b.get("team_abbr", TEAM_ABBR.get(tid, "")),

            "pts_per_game":     b.get("pts_per_game"),
            "opp_pts_per_game": o.get("opp_pts_per_game"),

            "off_rating":     a.get("off_rating"),
            "def_rating":     a.get("def_rating"),
            "net_rating":     a.get("net_rating"),

            "adj_off_rating": a.get("adj_off_rating"),
            "adj_def_rating": a.get("adj_def_rating"),
            "adj_net_rating": a.get("adj_net_rating"),

            "pace": a.get("pace"),

            "wins":    b.get("wins"),
            "losses":  b.get("losses"),
            "win_pct": b.get("win_pct"),

            "conference_seed": s.get("conference_seed"),
            "made_playoffs":   s.get("made_playoffs", 0),

            "prev_season":         prev_season_str if prev_standings else None,
            "prev_seed":           ps.get("conference_seed"),
            "prev_playoff_result": None,
        }
        records.append(rec)

    return records, standing


def upsert_records(con: sqlite3.Connection, season: str, records: list[dict]) -> int:
    """Delete existing rows for this season then insert fresh (idempotent re-runs)."""
    cur = con.cursor()
    cur.execute("DELETE FROM team_stats WHERE season = ?", (season,))

    sql = """
    INSERT INTO team_stats (
        season, team_id, team_name, team_abbr,
        pts_per_game, opp_pts_per_game,
        off_rating, def_rating, net_rating,
        adj_off_rating, adj_def_rating, adj_net_rating,
        pace,
        wins, losses, win_pct,
        conference_seed, made_playoffs,
        prev_season, prev_seed, prev_playoff_result
    ) VALUES (
        :season, :team_id, :team_name, :team_abbr,
        :pts_per_game, :opp_pts_per_game,
        :off_rating, :def_rating, :net_rating,
        :adj_off_rating, :adj_def_rating, :adj_net_rating,
        :pace,
        :wins, :losses, :win_pct,
        :conference_seed, :made_playoffs,
        :prev_season, :prev_seed, :prev_playoff_result
    )
    """
    cur.executemany(sql, records)
    con.commit()
    return len(records)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    con = sqlite3.connect(DB_PATH)
    total_rows = 0
    prev_standings: dict = {}
    prev_season_str: str = ""

    for i, season in enumerate(SEASONS, 1):
        print(f"\n{'='*60}", flush=True)
        print(f"  Season {season}  ({i}/{len(SEASONS)})", flush=True)
        print(f"{'='*60}", flush=True)

        try:
            records, current_standings = merge_season(season, prev_season_str, prev_standings)
            n = upsert_records(con, season, records)
            total_rows += n
            print(f"  [OK]  {n} team records written for {season}", flush=True)

            prev_standings  = current_standings
            prev_season_str = season

        except Exception as exc:
            import traceback
            print(f"  [ERR]  ERROR for season {season}: {exc}", flush=True)
            traceback.print_exc()
            print(f"     Skipping this season and continuing ...", flush=True)
            time.sleep(10)
            prev_season_str = season

        if i < len(SEASONS):
            print(f"  [cooldown between seasons: 8 s]", flush=True)
            time.sleep(8)

    con.close()
    print(f"\n{'='*60}", flush=True)
    print(f"  DONE — {total_rows} total team records across {len(SEASONS)} seasons.", flush=True)
    print(f"  Database: {DB_PATH}", flush=True)


if __name__ == "__main__":
    main()
