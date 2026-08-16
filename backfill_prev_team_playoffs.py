"""
backfill_prev_team_playoffs.py
-------------------------------
Fills NULL prev_seed and prev_playoff_result in team_stats_playoffs.

Two-phase approach
------------------

Phase 1 — SQL only (seasons 2021-22 through 2025-26):
  prev_seed           : pulled from team_stats (regular-season conference_seed)
                        for the preceding season via a SQL correlated UPDATE.
  prev_playoff_result : set to "Missed Playoffs" — these teams are NULL
                        precisely because they were not in team_stats_playoffs
                        the previous year.

Phase 2 — API (season 2020-21 only):
  team_stats has no 2019-20 data, so we fetch it directly:
    1. LeagueStandingsV3("2019-20")                      → prev_seed
    2. LeagueDashTeamStats Totals Playoffs "2019-20"     → prev_playoff_result
       (same wins-to-round mapping used in migrate_team_playoffs_v2.py)

  Teams that played in the 2019-20 bubble playoffs get their round label;
  teams absent from that data get "Missed Playoffs".

Hydrate — DB only, one season (--season):
  Rebuilds every column fetch_team_playoffs.py does not produce: conference,
  conference_seed and prev_*. Used after a re-fetch to restore the derived
  columns before playoff_result is calculated from them.

Anti-bot: random 4.5–8.2 s sleep between API requests.
"""

import argparse
import os
import sys
import math
import time
import random
import sqlite3

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import LeagueStandingsV3, LeagueDashTeamStats

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")
TIMEOUT = 90

SEASON_PREV_MAP = {
    "2021-22": "2020-21",
    "2022-23": "2021-22",
    "2023-24": "2022-23",
    "2024-25": "2023-24",
    "2025-26": "2024-25",
}

MISSED_PLAYOFFS = "Missed Playoffs"

# Static East/West map, keyed by the immutable NBA team_id. Defined locally so
# the hydrate step has no dependency on run_monte_carlo.py; verified to agree
# with every non-NULL conference already stored in team_stats_playoffs.
CONFERENCE_BY_TEAM_ID = {
    1610612737: "East",  # ATL
    1610612738: "East",  # BOS
    1610612739: "East",  # CLE
    1610612741: "East",  # CHI
    1610612748: "East",  # MIA
    1610612749: "East",  # MIL
    1610612751: "East",  # BKN
    1610612752: "East",  # NYK
    1610612753: "East",  # ORL
    1610612754: "East",  # IND
    1610612755: "East",  # PHI
    1610612761: "East",  # TOR
    1610612764: "East",  # WAS
    1610612765: "East",  # DET
    1610612766: "East",  # CHA
    1610612740: "West",  # NOP
    1610612742: "West",  # DAL
    1610612743: "West",  # DEN
    1610612744: "West",  # GSW
    1610612745: "West",  # HOU
    1610612746: "West",  # LAC
    1610612747: "West",  # LAL
    1610612750: "West",  # MIN
    1610612756: "West",  # PHX
    1610612757: "West",  # POR
    1610612758: "West",  # SAC
    1610612759: "West",  # SAS
    1610612760: "West",  # OKC
    1610612762: "West",  # UTA
    1610612763: "West",  # MEM
}


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
    if wins is None:
        return "1st Round"
    if wins >= 16: return "Champion"
    if wins >= 12: return "Finals"
    if wins >= 8:  return "Conf. Finals"
    if wins >= 4:  return "Conf. Semifinals"
    return "1st Round"


# ---------------------------------------------------------------------------
# Hydrate — rebuild the derived columns for one season
# ---------------------------------------------------------------------------

def previous_season(season: str) -> str:
    """'2025-26' -> '2024-25'."""
    start = int(season.split("-")[0])
    return f"{start - 1}-{str(start)[-2:]}"


def _column_map(con: sqlite3.Connection, sql: str, params: tuple) -> dict:
    return {row[0]: row[1] for row in con.execute(sql, params).fetchall()}


def hydrate_season(con: sqlite3.Connection, season: str) -> int:
    """Refill the columns fetch_team_playoffs.py does not produce, for one season.

    conference          <- CONFERENCE_BY_TEAM_ID
    conference_seed     <- team_stats.conference_seed for the same season
    prev_seed           <- team_stats.conference_seed for the previous season
    prev_playoff_result <- the previous season's playoff_result, else Missed Playoffs

    Values are recomputed rather than only NULL-filled, so the step is
    idempotent and self-heals a season whose derived columns were damaged.
    Only `season` is touched.
    """
    rows = con.execute(
        "SELECT team_id, team_abbr, conference, conference_seed, prev_seed, "
        "prev_playoff_result FROM team_stats_playoffs WHERE season = ? "
        "ORDER BY team_abbr",
        (season,),
    ).fetchall()
    if not rows:
        print(f"  [hydrate] No team_stats_playoffs rows for {season}.", flush=True)
        return 0

    prev = previous_season(season)
    seeds = _column_map(
        con,
        "SELECT team_id, conference_seed FROM team_stats WHERE season = ?",
        (season,),
    )
    prev_seeds = _column_map(
        con,
        "SELECT team_id, conference_seed FROM team_stats WHERE season = ?",
        (prev,),
    )
    prev_results = _column_map(
        con,
        "SELECT team_id, playoff_result FROM team_stats_playoffs WHERE season = ?",
        (prev,),
    )

    print(f"  [hydrate] {season} — {len(rows)} rows, previous season {prev}", flush=True)
    if not seeds:
        print(
            f"    [warn] team_stats has no {season} rows; conference_seed cannot be filled.",
            flush=True,
        )
    if not prev_seeds:
        print(
            f"    [warn] team_stats has no {prev} rows; prev_seed left as-is "
            f"(run the API phase for that season instead).",
            flush=True,
        )
    if not prev_results:
        print(
            f"    [warn] team_stats_playoffs has no {prev} rows; "
            f"prev_playoff_result left as-is.",
            flush=True,
        )

    changed = 0
    for team_id, abbr, conference, seed, prev_seed, prev_result in rows:
        new_values: dict[str, object] = {
            "conference": CONFERENCE_BY_TEAM_ID.get(team_id, conference),
        }
        if seeds:
            new_values["conference_seed"] = seeds.get(team_id)
            if team_id not in seeds:
                print(
                    f"    [warn] {abbr} has no team_stats row for {season}; "
                    f"conference_seed stays NULL.",
                    flush=True,
                )
        if prev_seeds:
            new_values["prev_seed"] = prev_seeds.get(team_id)
        if prev_results:
            new_values["prev_playoff_result"] = prev_results.get(team_id, MISSED_PLAYOFFS)

        current = {
            "conference": conference,
            "conference_seed": seed,
            "prev_seed": prev_seed,
            "prev_playoff_result": prev_result,
        }
        diffs = {c: v for c, v in new_values.items() if current[c] != v}
        if not diffs:
            continue

        assignments = ", ".join(f"{c} = ?" for c in diffs)
        con.execute(
            f"UPDATE team_stats_playoffs SET {assignments} "
            f"WHERE season = ? AND team_id = ?",
            (*diffs.values(), season, team_id),
        )
        changed += 1
        detail = ", ".join(f"{c}: {current[c]!r} -> {v!r}" for c, v in diffs.items())
        print(f"    {abbr:<4} {detail}", flush=True)

    con.commit()
    print(f"  [hydrate] {season}: {changed} row(s) changed.", flush=True)
    return changed


# ---------------------------------------------------------------------------
# Phase 1 — SQL for 2021-22 onward
# ---------------------------------------------------------------------------

def phase1_sql(con: sqlite3.Connection) -> None:
    cur = con.cursor()
    total_seed = 0
    total_result = 0

    for season, prev_season in SEASON_PREV_MAP.items():
        # Fill prev_seed from team_stats (regular-season conference_seed)
        cur.execute("""
            UPDATE team_stats_playoffs
               SET prev_seed = (
                   SELECT ts.conference_seed
                     FROM team_stats ts
                    WHERE ts.team_id = team_stats_playoffs.team_id
                      AND ts.season  = ?
               )
             WHERE season     = ?
               AND prev_seed IS NULL
        """, (prev_season, season))
        n_seed = cur.rowcount
        total_seed += n_seed

        # Mark as "Missed Playoffs" — NULL means they weren't in the
        # previous postseason, which means they missed playoffs
        cur.execute("""
            UPDATE team_stats_playoffs
               SET prev_playoff_result = 'Missed Playoffs'
             WHERE season              = ?
               AND prev_playoff_result IS NULL
        """, (season,))
        n_result = cur.rowcount
        total_result += n_result

        print(
            f"  [SQL] {season} ← {prev_season}: "
            f"prev_seed={n_seed} rows  prev_playoff_result={n_result} rows",
            flush=True,
        )

    con.commit()
    print(f"  [SQL] Total: prev_seed={total_seed}  prev_playoff_result={total_result}", flush=True)


# ---------------------------------------------------------------------------
# Phase 2 — API for 2020-21 (prev season = 2019-20, not in DB)
# ---------------------------------------------------------------------------

def fetch_standings_2019(con: sqlite3.Connection) -> dict:
    """LeagueStandingsV3 for 2019-20 → {team_id: conference_seed}."""
    print(f"\n    -> LeagueStandingsV3 [2019-20] ...", flush=True)
    r = LeagueStandingsV3(season="2019-20", timeout=TIMEOUT)
    df = r.get_data_frames()[0]
    out = {}
    for _, row in df.iterrows():
        tid  = safe_int(row.get("TeamID"))
        seed = safe_int(row.get("PlayoffRank"))
        if tid is not None:
            out[tid] = seed
    print(f"       {len(out)} teams", flush=True)
    return out


def fetch_playoff_wins_2019() -> dict:
    """LeagueDashTeamStats Totals Playoffs 2019-20 → {team_id: wins}."""
    print(f"    -> LeagueDashTeamStats Totals Playoffs [2019-20] ...", flush=True)
    try:
        r = LeagueDashTeamStats(
            season="2019-20",
            per_mode_detailed="Totals",
            measure_type_detailed_defense="Base",
            season_type_all_star="Playoffs",
            timeout=TIMEOUT,
        )
        df = r.get_data_frames()[0]
        if df is None or df.empty:
            print(f"       No data.", flush=True)
            return {}
        out = {}
        for _, row in df.iterrows():
            tid = safe_int(row.get("TEAM_ID"))
            w   = safe_int(row.get("W"))
            if tid is not None:
                out[tid] = w or 0
        print(f"       {len(out)} teams", flush=True)
        return out
    except Exception as exc:
        print(f"       [warn] {exc}", flush=True)
        return {}


def phase2_api(con: sqlite3.Connection) -> None:
    """Fill 2020-21 rows using 2019-20 data fetched from the API."""
    seeds_2019    = fetch_standings_2019(con)
    snooze("standings -> playoff wins")
    po_wins_2019  = fetch_playoff_wins_2019()

    cur = con.cursor()
    cur.execute(
        "SELECT team_id FROM team_stats_playoffs WHERE season = '2020-21'"
    )
    tids = [row[0] for row in cur.fetchall()]

    updated_seed = updated_result = 0
    for tid in tids:
        seed   = seeds_2019.get(tid)
        result = (
            wins_to_result(po_wins_2019[tid])
            if tid in po_wins_2019
            else "Missed Playoffs"
        )

        cur.execute("""
            UPDATE team_stats_playoffs
               SET prev_seed           = ?,
                   prev_playoff_result = ?
             WHERE season  = '2020-21'
               AND team_id = ?
               AND (prev_seed IS NULL OR prev_playoff_result IS NULL)
        """, (seed, result, tid))

        if cur.rowcount:
            updated_seed   += 1
            updated_result += 1
            print(
                f"  [2020-21] {tid}: prev_seed={seed}  prev_result={result}",
                flush=True,
            )

    con.commit()
    print(
        f"\n  [API] 2020-21: prev_seed={updated_seed}  "
        f"prev_playoff_result={updated_result} rows updated.",
        flush=True,
    )


# ---------------------------------------------------------------------------
# Sanity check
# ---------------------------------------------------------------------------

def sanity_check(con: sqlite3.Connection) -> None:
    cur = con.cursor()
    cur.execute("""
        SELECT season,
               SUM(CASE WHEN prev_seed           IS NULL THEN 1 ELSE 0 END) as null_seed,
               SUM(CASE WHEN prev_playoff_result IS NULL THEN 1 ELSE 0 END) as null_result
        FROM team_stats_playoffs
        GROUP BY season ORDER BY season
    """)
    print("\n  Remaining NULLs per season (seed | result):", flush=True)
    for row in cur.fetchall():
        print(f"    {row[0]}: prev_seed={row[1]}  prev_playoff_result={row[2]}", flush=True)

    cur.execute("""
        SELECT team_abbr, season, conference_seed,
               prev_seed, prev_playoff_result, playoff_result
        FROM team_stats_playoffs
        WHERE season = '2023-24'
          AND (prev_seed < 7 OR prev_playoff_result = 'Missed Playoffs')
        ORDER BY conference, conference_seed
        LIMIT 8
    """)
    cols = [d[0] for d in cur.description]
    print("\n  Sample (teams that jumped from outside playoffs or low seed):", flush=True)
    for row in cur.fetchall():
        print("   ", dict(zip(cols, row)), flush=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    con = sqlite3.connect(DB_PATH)

    print("\n=== Phase 1: SQL fill for 2021-22 through 2025-26 ===", flush=True)
    phase1_sql(con)

    print("\n=== Phase 2: API fetch for 2019-20 data (2020-21 rows) ===", flush=True)
    phase2_api(con)

    print("\n=== Sanity check ===", flush=True)
    sanity_check(con)

    con.close()
    print("\n  DONE.", flush=True)


def parse_args(argv: "list[str] | None" = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fill the derived columns of team_stats_playoffs."
    )
    parser.add_argument(
        "--season",
        help=(
            "Hydrate conference, conference_seed and prev_* for a single season "
            "(no API calls). Omit to run the original two-phase backfill."
        ),
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    if args.season:
        connection = sqlite3.connect(DB_PATH)
        hydrate_season(connection, args.season)
        connection.close()
    else:
        main()
