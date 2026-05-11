"""
fetch_player_advanced.py
------------------------
Fills player_stats_advanced in nba_data.db for seasons 2020-21 through 2025-26.

API calls per season (7 total):
  1. LeagueDashPlayerStats  — Per100 Base       -> per-100 counting stats
  2. LeagueDashPlayerStats  — PerGame Advanced  -> def_rating, ts%, efg%
  3. LeagueDashPlayerStats  — PerGame Defense   -> def_reb, defensive counts
  4. LeagueDashPlayerShotLocations              -> shot-zone FG%
  5. LeagueDashPtDefend     — Less Than 6Ft     -> opp rim FG%, rim FGA defended / game
  6. LeagueDashPtDefend     — 3 Pointers        -> opp 3P% + opponent 3PA defended / game
  7. LeagueHustleStatsPlayer                   -> deflections

By default the seven season fetches run in parallel (modest worker count)
with no per-call sleep, plus a short pause between seasons. Use --polite for
the old sequential 4–8 s delay between each call if the API rate-limits you.
"""

import sqlite3
import time
import random
import os
import sys
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

# Force UTF-8 output on Windows consoles so ASCII-only print() never fails
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import (
    LeagueDashPlayerStats,
    LeagueDashPlayerShotLocations,
    LeagueDashPtDefend,
    LeagueHustleStatsPlayer,
)

from init_db import CREATE_PLAYER_STATS_ADVANCED

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

# Parallel fetches per season (independent HTTP calls; cap to reduce 429s)
PARALLEL_WORKERS_DEFAULT = 4
SEASON_COOLDOWN_SEC = 3.0
POLITE_SNOOZE_RANGE = (4.0, 8.0)
FAST_SNOOZE_RANGE = (0.35, 1.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def snooze(
    label: str = "",
    *,
    lo: float | None = None,
    hi: float | None = None,
) -> None:
    """Sleep between API requests (used in --polite and sequential modes)."""
    lo = POLITE_SNOOZE_RANGE[0] if lo is None else lo
    hi = POLITE_SNOOZE_RANGE[1] if hi is None else hi
    t = random.uniform(lo, hi)
    tag = f" [{label}]" if label else ""
    print(f"    [wait]  sleeping {t:.1f}s{tag} ...", flush=True)
    time.sleep(t)


def pause_between_requests(polite: bool, label: str) -> None:
    if polite:
        snooze(label, lo=POLITE_SNOOZE_RANGE[0], hi=POLITE_SNOOZE_RANGE[1])
    else:
        snooze(label, lo=FAST_SNOOZE_RANGE[0], hi=FAST_SNOOZE_RANGE[1])


def safe_float(val):
    """Return float or None for NaN / None values."""
    try:
        f = float(val)
        import math
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def safe_int(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def ensure_regular_advanced_schema(con: sqlite3.Connection) -> None:
    """Align existing DBs with player_stats_advanced DDL (rename + new columns)."""
    cur = con.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='player_stats_advanced'"
    )
    if cur.fetchone() is None:
        cur.execute(CREATE_PLAYER_STATS_ADVANCED)
        con.commit()
        return

    def cols_set() -> set[str]:
        return {r[1] for r in cur.execute("PRAGMA table_info(player_stats_advanced)")}

    cols = cols_set()

    if "opp_fg3_contests_attempts" not in cols and "opp_fg3_pct_contested" in cols:
        cur.execute(
            "ALTER TABLE player_stats_advanced "
            "RENAME COLUMN opp_fg3_pct_contested TO opp_fg3_contests_attempts"
        )
        con.commit()
        print(
            "[schema] Renamed opp_fg3_pct_contested → opp_fg3_contests_attempts "
            "(re-fetch to repopulate from NBA tracking).",
            flush=True,
        )
        cur.execute(
            "UPDATE player_stats_advanced SET opp_fg3_contests_attempts = NULL"
        )
        con.commit()
        cols = cols_set()

    if "opp_fg3_pct_contested" not in cols:
        cur.execute(
            "ALTER TABLE player_stats_advanced ADD COLUMN opp_fg3_pct_contested REAL"
        )
        con.commit()
        print("[schema] Added column opp_fg3_pct_contested.", flush=True)
        cols = cols_set()

    if "opp_fg_at_rim_contested" not in cols:
        cur.execute(
            "ALTER TABLE player_stats_advanced ADD COLUMN opp_fg_at_rim_contested REAL"
        )
        con.commit()
        print("[schema] Added column opp_fg_at_rim_contested.", flush=True)


ADV_TABLE = "player_stats_advanced"
# SQLite fills `created_at` on first insert; do not overwrite on upsert.
_SKIP_UPSERT_COLS = frozenset({"id", "created_at"})
_CONFLICT_COLS = ("season", "player_id", "team_id")


def advanced_writable_columns(con: sqlite3.Connection) -> list[str]:
    """Columns we INSERT/UPDATE (must match the live table — PRAGMA is source of truth)."""
    cur = con.cursor()
    rows = cur.execute(f"PRAGMA table_info({ADV_TABLE})").fetchall()
    return [r[1] for r in rows if r[1] not in _SKIP_UPSERT_COLS]


def _build_advanced_upsert_sql(columns: list[str]) -> str:
    for c in _CONFLICT_COLS:
        if c not in columns:
            raise RuntimeError(
                f"Table {ADV_TABLE} is missing conflict column {c!r}; cannot upsert."
            )
    update_cols = [c for c in columns if c not in _CONFLICT_COLS]
    col_list = ", ".join(columns)
    placeholders = ", ".join(f":{c}" for c in columns)
    set_clause = ", ".join(f"{c} = excluded.{c}" for c in update_cols)
    conflict_list = ", ".join(_CONFLICT_COLS)
    return (
        f"INSERT INTO {ADV_TABLE} ({col_list}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict_list}) DO UPDATE SET {set_clause}"
    )


# ---------------------------------------------------------------------------
# Fetch functions
# ---------------------------------------------------------------------------


def fetch_per100(season: str) -> dict:
    """
    LeagueDashPlayerStats Per100 Base.
    Key: (player_id, team_id)
    Returns: pts/reb/ast/tov/stl/blk/fga/fg3a/fta per 100 poss.
    """
    print(f"    -> Per100 Base ...", flush=True)
    r = LeagueDashPlayerStats(
        season=season,
        per_mode_detailed="Per100Possessions",
        measure_type_detailed_defense="Base",
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    out = {}
    for _, row in df.iterrows():
        key = (int(row["PLAYER_ID"]), int(row["TEAM_ID"]))
        fgm   = safe_float(row["FGM"])
        fg3m  = safe_float(row["FG3M"])
        fga   = safe_float(row["FGA"])
        fta   = safe_float(row["FTA"])
        pts   = safe_float(row["PTS"])
        out[key] = {
            "player_name": row["PLAYER_NAME"],
            "team_abbr":   row["TEAM_ABBREVIATION"],
            "pts_per100":  pts,
            "reb_per100":  safe_float(row["REB"]),
            "ast_per100":  safe_float(row["AST"]),
            "tov_per100":  safe_float(row["TOV"]),
            "stl_per100":  safe_float(row["STL"]),
            "blk_per100":  safe_float(row["BLK"]),
            "fga_per100":  fga,
            "fg3a_per100": safe_float(row["FG3A"]),
            "fta_per100":  fta,
            # compute ts% and efg% from per-100 counts (same ratio as per-game)
            "ts_pct": round(pts / (2 * (fga + 0.44 * fta)), 4)
                      if pts and fga and fta and (fga + 0.44 * fta) > 0 else None,
            "efg_pct": round((fgm + 0.5 * fg3m) / fga, 4)
                       if fgm is not None and fg3m is not None and fga and fga > 0 else None,
        }
    return out


def fetch_advanced(season: str) -> dict:
    """
    LeagueDashPlayerStats PerGame Advanced.
    Key: (player_id, team_id)
    Returns: def_rating, ts_pct (override), efg_pct (override), dreb_pct.
    """
    print(f"    -> Advanced PerGame ...", flush=True)
    r = LeagueDashPlayerStats(
        season=season,
        per_mode_detailed="PerGame",
        measure_type_detailed_defense="Advanced",
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    out = {}
    for _, row in df.iterrows():
        key = (int(row["PLAYER_ID"]), int(row["TEAM_ID"]))
        out[key] = {
            "def_rating": safe_float(row["DEF_RATING"]),
            "ts_pct":     safe_float(row["TS_PCT"]),
            "efg_pct":    safe_float(row["EFG_PCT"]),
        }
    return out


def fetch_defense(season: str) -> dict:
    """
    LeagueDashPlayerStats PerGame Defense.
    Key: (player_id, team_id)
    Returns: def_reb per game.
    """
    print(f"    -> Defense PerGame ...", flush=True)
    r = LeagueDashPlayerStats(
        season=season,
        per_mode_detailed="PerGame",
        measure_type_detailed_defense="Defense",
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    out = {}
    for _, row in df.iterrows():
        key = (int(row["PLAYER_ID"]), int(row["TEAM_ID"]))
        out[key] = {
            "def_reb": safe_float(row.get("DREB")),
        }
    return out


def fetch_shot_locations(season: str) -> dict:
    """
    LeagueDashPlayerShotLocations PerGame Base.
    Key: (player_id, team_id)
    Returns: fg_pct_rim, fg_pct_mid, fg3_pct_corner, fg3_pct_above_break.
    Shot location columns have a MultiIndex (zone, stat).
    """
    print(f"    -> Shot Locations ...", flush=True)
    r = LeagueDashPlayerShotLocations(
        season=season,
        per_mode_detailed="PerGame",
        measure_type_simple="Base",
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]

    # Flatten MultiIndex columns — (zone, stat) -> "ZONE|STAT"
    if hasattr(df.columns, "levels"):
        df.columns = [
            f"{z}|{s}" if z else s
            for z, s in df.columns
        ]

    out = {}
    for _, row in df.iterrows():
        player_id = safe_int(row.get("PLAYER_ID"))
        team_id   = safe_int(row.get("TEAM_ID"))
        if player_id is None or team_id is None:
            continue
        key = (player_id, team_id)

        def zone_pct(zone_name: str) -> "float | None":
            col = f"{zone_name}|FG_PCT"
            return safe_float(row.get(col))

        # Corner 3 = combined Left + Right (the API also exposes a "Corner 3" group)
        corner3 = zone_pct("Corner 3")
        if corner3 is None:
            lc = zone_pct("Left Corner 3")
            rc = zone_pct("Right Corner 3")
            lc_a = safe_float(row.get("Left Corner 3|FGA"))
            rc_a = safe_float(row.get("Right Corner 3|FGA"))
            if lc_a and rc_a and (lc_a + rc_a) > 0:
                lc_m = safe_float(row.get("Left Corner 3|FGM")) or 0
                rc_m = safe_float(row.get("Right Corner 3|FGM")) or 0
                corner3 = round((lc_m + rc_m) / (lc_a + rc_a), 4)

        out[key] = {
            "fg_pct_rim":          zone_pct("Restricted Area"),
            "fg_pct_mid":          zone_pct("Mid-Range"),
            "fg3_pct_corner":      corner3,
            "fg3_pct_above_break": zone_pct("Above the Break 3"),
        }
    return out


def fetch_rim_defense(season: str) -> dict:
    """
    LeagueDashPtDefend — Less Than 6Ft, PerGame.
    Key: player_id (no team_id on this endpoint)
    Returns: opponent FG% and opponent FGA at the rim per game as primary defender.
    """
    print(f"    -> Rim Defense ...", flush=True)
    r = LeagueDashPtDefend(
        season=season,
        per_mode_simple="PerGame",
        defense_category="Less Than 6Ft",
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    out = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("CLOSE_DEF_PERSON_ID"))
        if pid is None:
            continue
        out[pid] = {
            "opp_fg_pct_at_rim":       safe_float(row.get("LT_06_PCT")),
            "opp_fg_at_rim_contested": safe_float(row.get("FGA_LT_06")),
        }
    return out


def fetch_three_pt_defense(season: str) -> dict:
    """
    LeagueDashPtDefend — 3 Pointers, PerGame.
    Key: player_id
    Returns: opponent 3P% and opponent 3PA per game when you are the matched defender.
    """
    print(f"    -> 3PT Defense ...", flush=True)
    r = LeagueDashPtDefend(
        season=season,
        per_mode_simple="PerGame",
        defense_category="3 Pointers",
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    out = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("CLOSE_DEF_PERSON_ID"))
        if pid is None:
            continue
        out[pid] = {
            "opp_fg3_contests_attempts": safe_float(row.get("FG3A")),
            "opp_fg3_pct_contested":     safe_float(row.get("FG3_PCT")),
        }
    return out


def fetch_hustle(season: str) -> dict:
    """
    LeagueHustleStatsPlayer PerGame.
    Key: (player_id, team_id)
    Returns: deflections, contested_shot_pct (2+3 breakdown).
    """
    print(f"    -> Hustle PerGame ...", flush=True)
    r = LeagueHustleStatsPlayer(
        season=season,
        per_mode_time="PerGame",
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    out = {}
    for _, row in df.iterrows():
        key = (safe_int(row["PLAYER_ID"]), safe_int(row["TEAM_ID"]))
        contested_2pt = safe_float(row.get("CONTESTED_SHOTS_2PT")) or 0.0
        contested_3pt = safe_float(row.get("CONTESTED_SHOTS_3PT")) or 0.0
        total_contested = safe_float(row.get("CONTESTED_SHOTS")) or 0.0
        out[key] = {
            "deflections": safe_float(row.get("DEFLECTIONS")),
            "contested_shot_pct": round(total_contested / (contested_2pt + contested_3pt), 4)
                                  if (contested_2pt + contested_3pt) > 0 else None,
        }
    return out


# ---------------------------------------------------------------------------
# Merge + upsert
# ---------------------------------------------------------------------------

def merge_season(
    season: str,
    *,
    workers: int = PARALLEL_WORKERS_DEFAULT,
    polite: bool = False,
) -> list[dict]:
    """
    Run all 7 fetches (parallel by default), merge into records keyed by
    (player_id, team_id).
    """
    workers = max(1, min(workers, 7))
    use_parallel = (not polite) and workers > 1

    if use_parallel:
        print(f"    [parallel]  up to {workers} workers for 7 endpoints ...", flush=True)
        futures_map = {
            "per100":  fetch_per100,
            "adv":     fetch_advanced,
            "defense": fetch_defense,
            "shotloc": fetch_shot_locations,
            "rim_def": fetch_rim_defense,
            "three_d": fetch_three_pt_defense,
            "hustle":  fetch_hustle,
        }
        results: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            future_to_key = {
                ex.submit(fn, season): key for key, fn in futures_map.items()
            }
            for fut in as_completed(future_to_key):
                key = future_to_key[fut]
                results[key] = fut.result()
                print(f"       [done] {key}", flush=True)

        per100  = results["per100"]
        adv     = results["adv"]
        defense = results["defense"]
        shotloc = results["shotloc"]
        rim_def = results["rim_def"]
        three_d = results["three_d"]
        hustle  = results["hustle"]
    else:
        # Sequential (--polite or --workers 1)
        per100 = fetch_per100(season)
        pause_between_requests(polite, "next: advanced")
        adv = fetch_advanced(season)
        pause_between_requests(polite, "next: defense")
        defense = fetch_defense(season)
        pause_between_requests(polite, "next: shot-loc")
        shotloc = fetch_shot_locations(season)
        pause_between_requests(polite, "next: rim-def")
        rim_def = fetch_rim_defense(season)
        pause_between_requests(polite, "next: 3pt-def")
        three_d = fetch_three_pt_defense(season)
        pause_between_requests(polite, "next: hustle")
        hustle = fetch_hustle(season)
        if polite:
            pause_between_requests(polite, "season done — writing DB")

    all_keys = set(per100) | set(adv) | set(defense) | set(shotloc) | set(hustle)
    records = []

    for key in all_keys:
        pid, tid = key
        p   = per100.get(key, {})
        a   = adv.get(key, {})
        d   = defense.get(key, {})
        sl  = shotloc.get(key, {})
        rd  = rim_def.get(pid, {})
        td  = three_d.get(pid, {})
        h   = hustle.get(key, {})

        rec = {
            "season":      season,
            "player_id":   pid,
            "player_name": p.get("player_name") or a.get("player_name", ""),
            "team_id":     tid,
            "team_abbr":   p.get("team_abbr") or "",
            "position":    None,                  # not exposed by league-dash endpoints

            # Per-100 possessions
            "pts_per100":  p.get("pts_per100"),
            "reb_per100":  p.get("reb_per100"),
            "ast_per100":  p.get("ast_per100"),
            "tov_per100":  p.get("tov_per100"),
            "stl_per100":  p.get("stl_per100"),
            "blk_per100":  p.get("blk_per100"),
            "fga_per100":  p.get("fga_per100"),
            "fg3a_per100": p.get("fg3a_per100"),
            "fta_per100":  p.get("fta_per100"),

            # Defensive counting
            "def_reb":    d.get("def_reb"),
            "def_rating": a.get("def_rating"),
            "deflections": h.get("deflections"),
            "opp_fg_pct_at_rim":        rd.get("opp_fg_pct_at_rim"),
            "opp_fg_at_rim_contested":  rd.get("opp_fg_at_rim_contested"),
            "opp_fg3_contests_attempts": td.get("opp_fg3_contests_attempts"),
            "opp_fg3_pct_contested":    td.get("opp_fg3_pct_contested"),

            # Shooting efficiency
            "ts_pct":  a.get("ts_pct") or p.get("ts_pct"),
            "efg_pct": a.get("efg_pct") or p.get("efg_pct"),

            # Shot zones
            "fg_pct_rim":          sl.get("fg_pct_rim"),
            "fg_pct_mid":          sl.get("fg_pct_mid"),
            "fg3_pct_corner":      sl.get("fg3_pct_corner"),
            "fg3_pct_above_break": sl.get("fg3_pct_above_break"),

            # Hustle split (2PT vs 3PT contested volume)
            "contested_shot_pct": h.get("contested_shot_pct"),
            "open_shot_pct":      None,   # populated by fix_shot_pct.py (tracking)
        }
        records.append(rec)

    return records


def upsert_records(con: sqlite3.Connection, records: list[dict]) -> int:
    columns = advanced_writable_columns(con)
    if not columns:
        raise RuntimeError(f"No writable columns found on {ADV_TABLE}.")
    sql = _build_advanced_upsert_sql(columns)
    rows = [{c: rec.get(c) for c in columns} for rec in records]
    cur = con.cursor()
    cur.executemany(sql, rows)
    con.commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hydrate player_stats_advanced from NBA Stats APIs.",
    )
    parser.add_argument(
        "--polite",
        action="store_true",
        help="Sequential requests with 4–8 s pause between each (if you hit rate limits).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=PARALLEL_WORKERS_DEFAULT,
        metavar="N",
        help=f"Parallel HTTP workers per season (default {PARALLEL_WORKERS_DEFAULT}; 1 = sequential with short pauses).",
    )
    parser.add_argument(
        "--season-gap",
        type=float,
        default=SEASON_COOLDOWN_SEC,
        metavar="SEC",
        help=f"Seconds to sleep between seasons (default {SEASON_COOLDOWN_SEC}).",
    )
    args = parser.parse_args()

    con = sqlite3.connect(DB_PATH)
    ensure_regular_advanced_schema(con)
    upsert_cols = advanced_writable_columns(con)
    print(
        f"[schema] {ADV_TABLE}: upsert uses {len(upsert_cols)} columns "
        f"(from DB PRAGMA): {', '.join(upsert_cols)}",
        flush=True,
    )
    total_rows = 0

    for i, season in enumerate(SEASONS, 1):
        print(f"\n{'='*60}", flush=True)
        print(f"  Season {season}  ({i}/{len(SEASONS)})", flush=True)
        print(f"{'='*60}", flush=True)

        try:
            records = merge_season(
                season,
                workers=args.workers,
                polite=args.polite,
            )
            n = upsert_records(con, records)
            total_rows += len(records)
            print(f"  [OK]  {len(records)} player records written for {season}", flush=True)

        except Exception as exc:
            print(f"  [ERR]  ERROR for season {season}: {exc}", flush=True)
            print(f"     Skipping this season and continuing ...", flush=True)
            time.sleep(10)

        if i < len(SEASONS) and args.season_gap > 0:
            print(f"  [cooldown between seasons: {args.season_gap:.1f} s]", flush=True)
            time.sleep(args.season_gap)

    con.close()
    print(f"\n{'='*60}", flush=True)
    print(f"  DONE — {total_rows} total records across {len(SEASONS)} seasons.", flush=True)
    print(f"  Database: {DB_PATH}", flush=True)


if __name__ == "__main__":
    main()
