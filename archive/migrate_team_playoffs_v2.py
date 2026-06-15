"""
migrate_team_playoffs_v2.py
---------------------------
Schema migration + data backfill for team_stats_playoffs.

Schema changes
--------------
  DROP  prev_season       (not meaningful for postseason table)
  DROP  made_playoffs     (always 1 by definition)
  ADD   playoff_result    TEXT  — how the team finished that postseason
  ADD   conference        TEXT  — "East" or "West"

Data fills
----------
  conference_seed   : regular-season PlayoffRank from LeagueStandingsV3
  conference        : "East" / "West" from LeagueStandingsV3
  playoff_result    : derived from playoff-only wins (LeagueDashTeamStats Totals "Playoffs")

      playoff_only_wins →  result
      ─────────────────────────────────────────────
      team not in Playoffs data  →  "Play-In Eliminated"
       0 – 3   →  "1st Round"
       4 – 7   →  "Conf. Semifinals"
       8 – 11  →  "Conf. Finals"
      12 – 15  →  "Finals"
          16   →  "Champion"

      NOTE: only "Playoffs" season-type wins are used for this mapping so
      that Play-In wins don't inflate the count for teams that went through
      the Play-In tournament.

  prev_seed           : previous season's conference_seed (SQL self-join, no API)
  prev_playoff_result : previous season's playoff_result  (SQL self-join, no API)

Anti-bot: random 4.5–8.2 s sleep between every API request.
"""

import os
import sys
import math
import time
import random
import sqlite3

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import LeagueStandingsV3, LeagueDashTeamStats

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

TIMEOUT = 90


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def snooze(label: str = "") -> None:
    t = random.uniform(4.5, 8.2)
    tag = f" [{label}]" if label else ""
    print(f"    [wait]  sleeping {t:.1f}s{tag} ...", flush=True)
    time.sleep(t)


def safe_int(val) -> "int | None":
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def wins_to_result(wins: "int | None") -> str:
    """Map playoff-bracket-only wins to a round label."""
    if wins is None:
        return "1st Round"
    if wins >= 16:
        return "Champion"
    if wins >= 12:
        return "Finals"
    if wins >= 8:
        return "Conf. Finals"
    if wins >= 4:
        return "Conf. Semifinals"
    return "1st Round"


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------

def migrate_schema(con: sqlite3.Connection) -> None:
    cur = con.cursor()
    existing = {row[1] for row in cur.execute(
        "PRAGMA table_info(team_stats_playoffs)"
    )}

    drops = [
        ("prev_season",   "DROP COLUMN prev_season"),
        ("made_playoffs", "DROP COLUMN made_playoffs"),
    ]
    for col, stmt in drops:
        if col in existing:
            cur.execute(f"ALTER TABLE team_stats_playoffs {stmt}")
            print(f"  [schema] {stmt} — done.", flush=True)
        else:
            print(f"  [schema] {col} already absent — skipped.", flush=True)

    adds = [
        ("playoff_result", "ADD COLUMN playoff_result TEXT"),
        ("conference",     "ADD COLUMN conference TEXT"),
    ]
    for col, stmt in adds:
        if col not in existing:
            cur.execute(f"ALTER TABLE team_stats_playoffs {stmt}")
            print(f"  [schema] {stmt} — done.", flush=True)
        else:
            print(f"  [schema] {col} already present — skipped.", flush=True)

    con.commit()


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

def fetch_standings(season: str) -> dict:
    """
    LeagueStandingsV3 (regular season) →
        {team_id: {"conference": "East"|"West", "conference_seed": int}}
    """
    print(f"    -> LeagueStandingsV3 [{season}] ...", flush=True)
    r = LeagueStandingsV3(season=season, timeout=TIMEOUT)
    df = r.get_data_frames()[0]
    out = {}
    for _, row in df.iterrows():
        tid  = safe_int(row.get("TeamID"))
        seed = safe_int(row.get("PlayoffRank"))
        conf = str(row.get("Conference", "")).strip()
        if tid is None:
            continue
        out[tid] = {
            "conference":      conf if conf else None,
            "conference_seed": seed,
        }
    print(f"       {len(out)} teams", flush=True)
    return out


def fetch_playoff_wins(season: str) -> dict:
    """
    LeagueDashTeamStats Totals Base SeasonType="Playoffs" →
        {team_id: playoff_only_wins}

    Using ONLY the Playoffs season type (not Play-In) so the win count
    reflects the actual bracket round the team was eliminated in.
    """
    print(f"    -> LeagueDashTeamStats Totals Playoffs [{season}] ...", flush=True)
    try:
        r = LeagueDashTeamStats(
            season=season,
            per_mode_detailed="Totals",
            measure_type_detailed_defense="Base",
            season_type_all_star="Playoffs",
            timeout=TIMEOUT,
        )
        df = r.get_data_frames()[0]
        if df is None or df.empty:
            print(f"       No data.", flush=True)
            return {}
    except Exception as exc:
        print(f"       [warn] {exc}", flush=True)
        return {}

    out = {}
    for _, row in df.iterrows():
        tid = safe_int(row.get("TEAM_ID"))
        w   = safe_int(row.get("W"))
        if tid is None:
            continue
        out[tid] = w or 0
    print(f"       {len(out)} teams", flush=True)
    return out


# ---------------------------------------------------------------------------
# Apply season data
# ---------------------------------------------------------------------------

def apply_season(con: sqlite3.Connection, season: str,
                 standings: dict, playoff_wins: dict) -> int:
    cur = con.cursor()

    # Get all teams currently in the table for this season
    cur.execute(
        "SELECT team_id FROM team_stats_playoffs WHERE season = ?", (season,)
    )
    tids = [row[0] for row in cur.fetchall()]
    updated = 0

    for tid in tids:
        s = standings.get(tid, {})
        conf  = s.get("conference")
        seed  = s.get("conference_seed")

        # Teams in Playoffs data get a round label; Play-In only → "Play-In Eliminated"
        if tid in playoff_wins:
            result = wins_to_result(playoff_wins[tid])
        else:
            result = "Play-In Eliminated"

        cur.execute(
            """
            UPDATE team_stats_playoffs
               SET conference      = ?,
                   conference_seed = ?,
                   playoff_result  = ?
             WHERE season  = ?
               AND team_id = ?
            """,
            (conf, seed, result, season, tid),
        )
        updated += cur.rowcount

    con.commit()
    return updated


# ---------------------------------------------------------------------------
# Backfill prev_seed and prev_playoff_result via SQL self-join
# ---------------------------------------------------------------------------

def backfill_prev(con: sqlite3.Connection) -> None:
    """
    For each row, look up the SAME team in the PREVIOUS postseason and
    copy conference_seed → prev_seed, playoff_result → prev_playoff_result.
    """
    season_pairs = list(zip(SEASONS[1:], SEASONS[:-1]))   # (current, previous)

    cur = con.cursor()
    total = 0
    for current, previous in season_pairs:
        cur.execute(
            """
            UPDATE team_stats_playoffs AS cur
               SET prev_seed           = (
                       SELECT prev.conference_seed
                         FROM team_stats_playoffs prev
                        WHERE prev.season  = ?
                          AND prev.team_id = cur.team_id
                   ),
                   prev_playoff_result = (
                       SELECT prev.playoff_result
                         FROM team_stats_playoffs prev
                        WHERE prev.season  = ?
                          AND prev.team_id = cur.team_id
                   )
             WHERE cur.season = ?
            """,
            (previous, previous, current),
        )
        n = cur.rowcount
        total += n
        print(f"  [prev backfill] {current} ← {previous}: {n} rows updated", flush=True)

    con.commit()
    print(f"  [prev backfill] {total} total rows updated.", flush=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    con = sqlite3.connect(DB_PATH)

    print("\n[migrate_team_playoffs_v2] Schema changes ...", flush=True)
    migrate_schema(con)

    grand_total = 0

    for i, season in enumerate(SEASONS, 1):
        print(f"\n{'='*60}", flush=True)
        print(f"  Season {season}  ({i}/{len(SEASONS)})", flush=True)
        print(f"{'='*60}", flush=True)

        try:
            standings = fetch_standings(season)
            snooze("standings -> playoff wins")
            po_wins = fetch_playoff_wins(season)

            n = apply_season(con, season, standings, po_wins)
            grand_total += n
            print(f"  [OK]  {n} rows updated for {season}", flush=True)

        except Exception as exc:
            print(f"  [ERR]  {season}: {exc}", flush=True)
            import traceback; traceback.print_exc()
            time.sleep(10)
            continue

        if i < len(SEASONS):
            snooze(f"cooldown before {SEASONS[i]}")

    print(f"\n{'='*60}", flush=True)
    print(f"  Backfilling prev_seed / prev_playoff_result ...", flush=True)
    backfill_prev(con)

    con.close()

    # Sanity check
    con2 = sqlite3.connect(DB_PATH)
    cur2 = con2.cursor()
    print(f"\n  Final sample (2023-24):", flush=True)
    cur2.execute("""
        SELECT team_abbr, conference, conference_seed, wins,
               playoff_result, prev_seed, prev_playoff_result
        FROM team_stats_playoffs
        WHERE season = '2023-24'
        ORDER BY conference, conference_seed
        LIMIT 10
    """)
    cols = [d[0] for d in cur2.description]
    for row in cur2.fetchall():
        print("   ", dict(zip(cols, row)), flush=True)
    con2.close()

    print(f"\n  DONE — {grand_total} rows updated across {len(SEASONS)} seasons.", flush=True)


if __name__ == "__main__":
    main()
