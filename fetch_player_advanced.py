"""
fetch_player_advanced.py
------------------------
Fills player_stats_advanced in nba_data.db for seasons 2019-20 through 2025-26.

API calls per season (6 total):
  1. LeagueDashPlayerStats  — Per100 Base       -> per-100 counting stats
  2. LeagueDashPlayerStats  — PerGame Advanced  -> def_rating, ts%, efg%
  3. LeagueDashPlayerStats  — PerGame Defense   -> def_reb, defensive counts
  4. LeagueDashPlayerShotLocations              -> shot-zone FG%
  5. LeagueDashPtDefend     — Less Than 6Ft     -> opp FG% at rim
  6. LeagueHustleStatsPlayer                   -> deflections, contested shots

Random delay of 4–8 s between every request to stay under rate limits.
"""

import sqlite3
import time
import random
import os
import sys

# Force UTF-8 output on Windows consoles so ASCII-only print() never fails
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import (
    LeagueDashPlayerStats,
    LeagueDashPlayerShotLocations,
    LeagueDashPtDefend,
    LeagueHustleStatsPlayer,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

SEASONS = [
    "2019-20",
    "2020-21",
    "2021-22",
    "2022-23",
    "2023-24",
    "2024-25",
    "2025-26",
]

TIMEOUT = 90   # seconds per request before giving up


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def snooze(label: str = "") -> None:
    """Sleep a random 4–8 seconds between API requests."""
    t = random.uniform(4.0, 8.0)
    tag = f" [{label}]" if label else ""
    print(f"    [wait]  sleeping {t:.1f}s{tag} ...", flush=True)
    time.sleep(t)


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
    LeagueDashPtDefend — Less Than 6Ft.
    Key: player_id (no team_id available from this endpoint)
    Returns: opp_fg_pct_at_rim.
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
            "opp_fg_pct_at_rim": safe_float(row.get("LT_06_PCT")),
        }
    return out


def fetch_hustle(season: str) -> dict:
    """
    LeagueHustleStatsPlayer PerGame.
    Key: (player_id, team_id)
    Returns: deflections, contested_shots_3pt (proxy for opp_fg3_pct_contested).
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
            "deflections":           safe_float(row.get("DEFLECTIONS")),
            # store 3P contested shots per game in opp_fg3_pct_contested
            # (actual opp FG3% under contest requires shot-qualify data; stored as count for now)
            "contested_3pt_pg":      contested_3pt,
            "contested_shot_pct":    round(total_contested / (contested_2pt + contested_3pt), 4)
                                     if (contested_2pt + contested_3pt) > 0 else None,
        }
    return out


# ---------------------------------------------------------------------------
# Merge + upsert
# ---------------------------------------------------------------------------

def merge_season(season: str) -> list[dict]:
    """
    Run all 6 fetches with delays, merge into one record list keyed by
    (player_id, team_id).
    """
    per100  = fetch_per100(season);        snooze("next: advanced")
    adv     = fetch_advanced(season);      snooze("next: defense")
    defense = fetch_defense(season);       snooze("next: shot-loc")
    shotloc = fetch_shot_locations(season); snooze("next: rim-def")
    rim_def = fetch_rim_defense(season);   snooze("next: hustle")
    hustle  = fetch_hustle(season);        snooze("season done — writing DB")

    all_keys = set(per100) | set(adv) | set(defense) | set(shotloc) | set(hustle)
    records = []

    for key in all_keys:
        pid, tid = key
        p   = per100.get(key, {})
        a   = adv.get(key, {})
        d   = defense.get(key, {})
        sl  = shotloc.get(key, {})
        rd  = rim_def.get(pid, {})    # rim-defend is player-only key
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
            "blk_pct":    None,    # requires possession-model data; future phase
            "stl_pct":    None,    # same
            "deflections": h.get("deflections"),
            "opp_fg_pct_at_rim":       rd.get("opp_fg_pct_at_rim"),
            "opp_fg3_pct_contested":   h.get("contested_3pt_pg"),   # contests/game, see note

            # Shooting efficiency
            "ts_pct":  a.get("ts_pct") or p.get("ts_pct"),
            "efg_pct": a.get("efg_pct") or p.get("efg_pct"),

            # Shot zones
            "fg_pct_rim":          sl.get("fg_pct_rim"),
            "fg_pct_mid":          sl.get("fg_pct_mid"),
            "fg3_pct_corner":      sl.get("fg3_pct_corner"),
            "fg3_pct_above_break": sl.get("fg3_pct_above_break"),

            # Contest quality (from hustle)
            "contested_shot_pct": h.get("contested_shot_pct"),
            "open_shot_pct":      None,   # needs shot-quality endpoint; future phase
        }
        records.append(rec)

    return records


def upsert_records(con: sqlite3.Connection, records: list[dict]) -> int:
    sql = """
    INSERT INTO player_stats_advanced (
        season, player_id, player_name, team_id, team_abbr, position,
        pts_per100, reb_per100, ast_per100, tov_per100, stl_per100, blk_per100,
        fga_per100, fg3a_per100, fta_per100,
        def_reb, def_rating, blk_pct, stl_pct, deflections,
        opp_fg_pct_at_rim, opp_fg3_pct_contested,
        ts_pct, efg_pct,
        fg_pct_rim, fg_pct_mid, fg3_pct_corner, fg3_pct_above_break,
        contested_shot_pct, open_shot_pct
    ) VALUES (
        :season, :player_id, :player_name, :team_id, :team_abbr, :position,
        :pts_per100, :reb_per100, :ast_per100, :tov_per100, :stl_per100, :blk_per100,
        :fga_per100, :fg3a_per100, :fta_per100,
        :def_reb, :def_rating, :blk_pct, :stl_pct, :deflections,
        :opp_fg_pct_at_rim, :opp_fg3_pct_contested,
        :ts_pct, :efg_pct,
        :fg_pct_rim, :fg_pct_mid, :fg3_pct_corner, :fg3_pct_above_break,
        :contested_shot_pct, :open_shot_pct
    )
    ON CONFLICT (season, player_id, team_id) DO UPDATE SET
        player_name            = excluded.player_name,
        team_abbr              = excluded.team_abbr,
        pts_per100             = excluded.pts_per100,
        reb_per100             = excluded.reb_per100,
        ast_per100             = excluded.ast_per100,
        tov_per100             = excluded.tov_per100,
        stl_per100             = excluded.stl_per100,
        blk_per100             = excluded.blk_per100,
        fga_per100             = excluded.fga_per100,
        fg3a_per100            = excluded.fg3a_per100,
        fta_per100             = excluded.fta_per100,
        def_reb                = excluded.def_reb,
        def_rating             = excluded.def_rating,
        blk_pct                = excluded.blk_pct,
        stl_pct                = excluded.stl_pct,
        deflections            = excluded.deflections,
        opp_fg_pct_at_rim      = excluded.opp_fg_pct_at_rim,
        opp_fg3_pct_contested  = excluded.opp_fg3_pct_contested,
        ts_pct                 = excluded.ts_pct,
        efg_pct                = excluded.efg_pct,
        fg_pct_rim             = excluded.fg_pct_rim,
        fg_pct_mid             = excluded.fg_pct_mid,
        fg3_pct_corner         = excluded.fg3_pct_corner,
        fg3_pct_above_break    = excluded.fg3_pct_above_break,
        contested_shot_pct     = excluded.contested_shot_pct,
        open_shot_pct          = excluded.open_shot_pct
    """
    cur = con.cursor()
    cur.executemany(sql, records)
    con.commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    con = sqlite3.connect(DB_PATH)
    total_rows = 0

    for i, season in enumerate(SEASONS, 1):
        print(f"\n{'='*60}", flush=True)
        print(f"  Season {season}  ({i}/{len(SEASONS)})", flush=True)
        print(f"{'='*60}", flush=True)

        try:
            records = merge_season(season)
            n = upsert_records(con, records)
            total_rows += len(records)
            print(f"  [OK]  {len(records)} player records written for {season}", flush=True)

        except Exception as exc:
            print(f"  [ERR]  ERROR for season {season}: {exc}", flush=True)
            print(f"     Skipping this season and continuing ...", flush=True)
            # wait a bit extra on error before next season
            time.sleep(10)

        # Extra cooldown between seasons (not between calls within a season)
        if i < len(SEASONS):
            print(f"  [cooldown between seasons: 8 s]", flush=True)
            time.sleep(8)

    con.close()
    print(f"\n{'='*60}", flush=True)
    print(f"  DONE — {total_rows} total records across {len(SEASONS)} seasons.", flush=True)
    print(f"  Database: {DB_PATH}", flush=True)


if __name__ == "__main__":
    main()
