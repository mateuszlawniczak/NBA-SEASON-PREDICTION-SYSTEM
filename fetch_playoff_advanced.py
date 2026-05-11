"""
fetch_playoff_advanced.py
-------------------------
Builds and hydrates player_stats_advanced_playoffs in nba_data.db
for seasons 2020-21 through 2025-26.

Per SeasonType ("Playoffs" and "Play In"), each parallel worker runs:
    1. LeagueDashPlayerStats  — Per100 Base          → per-100 + TS%/EFG%
    2. LeagueDashPlayerStats  — Base PerGame         → off_reb (OREB)
    3. LeagueDashPlayerStats  — Advanced PerGame     → def_rating, TS%, EFG%, **USG_PCT**
    4. LeagueDashPlayerStats  — Defense PerGame      → def_reb
    5. LeagueDashPlayerShotLocations                 → zone FG%
    6. LeagueDashPtDefend Totals — Less Than 6Ft    → opp_fga_at_rim, opp_fg_pct_at_rim
    7. LeagueDashPtDefend Totals — 3 Pointers       → opp_fg3a_contested, opp_fg3_pct_contested
    8. LeagueDashPlayerStats  — Totals Base          → total_minutes (merge weights only)
    9. LeagueHustleStatsPlayer — PerGame             → deflections
  + LeagueDashPlayerPtShot (Totals, 4 buckets × both types) → contested/open shot %

Merge when a player has BOTH Play-In and Playoff rows:
    • SUM volumes: opp_fga_at_rim, opp_fg3a_contested
    • MINUTE-weighted rates: opp_fg_pct_at_rim, opp_fg3_pct_contested, usg_pct
          (s_po×min_po + s_pi×min_pi) / (min_po + min_pi)
    • Other stats stay GP-weighted; team/name favour Playoffs.

Anti-bot: ~0.35–1.0 s jitter between sequential calls (2 parallel workers).

Use  python fetch_playoff_advanced.py --force  to overwrite seasons already populated.
"""

import sqlite3
import time
import random
import math
import os
import sys
import argparse
import concurrent.futures

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import (
    LeagueDashPlayerStats,
    LeagueDashPlayerShotLocations,
    LeagueDashPtDefend,
    LeagueHustleStatsPlayer,
    LeagueDashPlayerPtShot,
)

from init_db import CREATE_PLAYER_STATS_ADVANCED_PLAYOFFS

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

SEASONS = [
    "2020-21", "2021-22", "2022-23",
    "2023-24", "2024-25", "2025-26",
]

# Seasons already committed — skip them on re-run
SKIP_IF_POPULATED = True

SEASON_TYPE_PLAYOFFS = "Playoffs"
SEASON_TYPE_PLAYIN   = "Play In"

TIMEOUT = 90

# CloseDefDistRange buckets for contested/open shot pct
# (mirrors fix_shot_pct.py — correct alternative to the broken hustle N/N math)
DIST_RANGES = [
    "0-2 Feet - Very Tight",
    "2-4 Feet - Tight",
    "4-6 Feet - Open",
    "6+ Feet - Wide Open",
]
CONTESTED_BUCKETS = {"0-2 Feet - Very Tight", "2-4 Feet - Tight"}

# GP-weighted merge across Play-In + Playoffs (when both exist).
# contested/open shot % come from PtShot buckets in upsert_season.
GP_WEIGHT_RATE_FIELDS = [
    "pts_per100", "reb_per100", "ast_per100", "tov_per100",
    "stl_per100", "blk_per100", "fga_per100", "fg3a_per100", "fta_per100",
    "off_reb", "def_reb", "def_rating", "deflections",
    "ts_pct", "efg_pct",
    "fg_pct_rim", "fg_pct_mid", "fg3_pct_corner", "fg3_pct_above_break",
]

# Volume stats — SUMMED across Play-In + Playoffs (Totals FGA counts).
SUM_MERGE_FIELDS = ["opp_fga_at_rim", "opp_fg3a_contested"]

# Rates merged with minute-weights: (s_po*min_po + s_pi*min_pi) / total_min
MIN_WEIGHT_RATE_FIELDS = [
    "opp_fg_pct_at_rim",
    "opp_fg3_pct_contested",
    "usg_pct",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def snooze(label: str = "") -> None:
    """Short jitter — parallel Playoffs/Play-In halves wall-clock sleep."""
    t = random.uniform(0.35, 1.0)
    tag = f" [{label}]" if label else ""
    print(f"    [wait]  {t:.2f}s{tag}", flush=True)
    time.sleep(t)


def safe_float(val) -> "float | None":
    try:
        f = float(val)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def safe_int(val) -> "int | None":
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def try_season_types(fn, season: str, season_type: str, label: str):
    """
    Call fn(season, season_type). If it raises, try the alternate spelling.
    Returns result or {} on complete failure.
    """
    alternates = [season_type]
    if season_type == SEASON_TYPE_PLAYIN:
        alternates.append("PlayIn")
    elif season_type == "PlayIn":
        alternates.append(SEASON_TYPE_PLAYIN)

    for st in alternates:
        try:
            result = fn(season, st)
            if result:
                return result
        except Exception as exc:
            print(f"      [warn] {label} [{st}]: {exc}", flush=True)

    print(f"      [skip] {label} [{season_type}] — no data.", flush=True)
    return {}


def ensure_playoffs_advanced_schema(con: sqlite3.Connection) -> None:
    """Create table from init_db DDL or migrate legacy column names."""
    cur = con.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='player_stats_advanced_playoffs'"
    )
    if cur.fetchone() is None:
        cur.execute(CREATE_PLAYER_STATS_ADVANCED_PLAYOFFS)
        con.commit()
        print("[schema] Created player_stats_advanced_playoffs.", flush=True)
        return

    def cols_set() -> set[str]:
        return {r[1] for r in cur.execute("PRAGMA table_info(player_stats_advanced_playoffs)")}

    cols = cols_set()
    if "opp_fga_at_rim" not in cols and "opp_fg_pct_at_rim" in cols:
        cur.execute(
            "ALTER TABLE player_stats_advanced_playoffs "
            "RENAME COLUMN opp_fg_pct_at_rim TO opp_fga_at_rim"
        )
        con.commit()
        print("[schema] Renamed opp_fg_pct_at_rim → opp_fga_at_rim.", flush=True)
        cols = cols_set()
    if "opp_fg3a_contested" not in cols and "opp_fg3_pct_contested" in cols:
        cur.execute(
            "ALTER TABLE player_stats_advanced_playoffs "
            "RENAME COLUMN opp_fg3_pct_contested TO opp_fg3a_contested"
        )
        con.commit()
        print("[schema] Renamed opp_fg3_pct_contested → opp_fg3a_contested.", flush=True)
        cols = cols_set()
    for add_col in ("opp_fg_pct_at_rim", "opp_fg3_pct_contested", "usg_pct"):
        if add_col not in cols:
            cur.execute(
                f"ALTER TABLE player_stats_advanced_playoffs ADD COLUMN {add_col} REAL"
            )
            con.commit()
            print(f"[schema] Added column {add_col}.", flush=True)
            cols = cols_set()


# ---------------------------------------------------------------------------
# Fetch functions — each returns {player_id: {..., "gp": int}}
# (keyed by player_id only; traded players within same season type are merged)
# ---------------------------------------------------------------------------

def _fetch_per100(season: str, season_type: str) -> dict:
    r = LeagueDashPlayerStats(
        season=season,
        per_mode_detailed="Per100Possessions",
        measure_type_detailed_defense="Base",
        season_type_all_star=season_type,
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    if df.empty:
        return {}
    out = {}
    for _, row in df.iterrows():
        pid  = safe_int(row.get("PLAYER_ID"))
        tid  = safe_int(row.get("TEAM_ID")) or 0
        if pid is None:
            continue
        gp   = safe_int(row.get("GP")) or 0
        fgm  = safe_float(row.get("FGM"))
        fg3m = safe_float(row.get("FG3M"))
        fga  = safe_float(row.get("FGA"))
        fta  = safe_float(row.get("FTA"))
        pts  = safe_float(row.get("PTS"))
        ts   = (round(pts / (2 * (fga + 0.44 * fta)), 4)
                if pts and fga and fta and (fga + 0.44 * fta) > 0 else None)
        efg  = (round((fgm + 0.5 * fg3m) / fga, 4)
                if fgm is not None and fg3m is not None and fga and fga > 0 else None)
        rec = {
            "player_name": row.get("PLAYER_NAME", ""),
            "team_id":     tid,
            "team_abbr":   row.get("TEAM_ABBREVIATION", ""),
            "gp":          gp,
            "pts_per100":  pts,
            "reb_per100":  safe_float(row.get("REB")),
            "ast_per100":  safe_float(row.get("AST")),
            "tov_per100":  safe_float(row.get("TOV")),
            "stl_per100":  safe_float(row.get("STL")),
            "blk_per100":  safe_float(row.get("BLK")),
            "fga_per100":  fga,
            "fg3a_per100": safe_float(row.get("FG3A")),
            "fta_per100":  fta,
            "ts_pct":      ts,
            "efg_pct":     efg,
        }
        if pid in out:
            # traded player — GP-weight merge within same season type
            prev = out[pid]
            total_gp = prev["gp"] + gp
            for f in ["pts_per100","reb_per100","ast_per100","tov_per100",
                      "stl_per100","blk_per100","fga_per100","fg3a_per100",
                      "fta_per100","ts_pct","efg_pct"]:
                a, b = prev.get(f), rec.get(f)
                if a is not None and b is not None and total_gp > 0:
                    prev[f] = (a * prev["gp"] + b * gp) / total_gp
                elif b is not None:
                    prev[f] = b
            prev["gp"] = total_gp
            prev["team_id"]  = 0
            prev["team_abbr"] = "TOT"
        else:
            out[pid] = rec
    return out


def fetch_per100(season: str, season_type: str) -> dict:
    label = f"Per100 Base [{season_type}]"
    print(f"    -> {label} ...", flush=True)
    result = try_season_types(_fetch_per100, season, season_type, label)
    print(f"       {len(result)} players", flush=True)
    return result


def _fetch_base_pg(season: str, season_type: str) -> dict:
    """PerGame Base — used only for off_reb (OREB per game)."""
    r = LeagueDashPlayerStats(
        season=season,
        per_mode_detailed="PerGame",
        measure_type_detailed_defense="Base",
        season_type_all_star=season_type,
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    if df.empty:
        return {}
    out = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("PLAYER_ID"))
        if pid is None:
            continue
        gp   = safe_int(row.get("GP")) or 0
        oreb = safe_float(row.get("OREB"))
        rec  = {"gp": gp, "off_reb": oreb}
        if pid in out:
            prev     = out[pid]
            total_gp = prev["gp"] + gp
            a, b     = prev.get("off_reb"), oreb
            if a is not None and b is not None and total_gp > 0:
                prev["off_reb"] = (a * prev["gp"] + b * gp) / total_gp
            elif b is not None:
                prev["off_reb"] = b
            prev["gp"] = total_gp
        else:
            out[pid] = rec
    return out


def fetch_base_pg(season: str, season_type: str) -> dict:
    label = f"Base PerGame [{season_type}]"
    print(f"    -> {label} ...", flush=True)
    result = try_season_types(_fetch_base_pg, season, season_type, label)
    print(f"       {len(result)} players", flush=True)
    return result


def _fetch_advanced(season: str, season_type: str) -> dict:
    r = LeagueDashPlayerStats(
        season=season,
        per_mode_detailed="PerGame",
        measure_type_detailed_defense="Advanced",
        season_type_all_star=season_type,
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    if df.empty:
        return {}
    out = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("PLAYER_ID"))
        if pid is None:
            continue
        gp = safe_int(row.get("GP")) or 0
        rec = {
            "gp":         gp,
            "def_rating": safe_float(row.get("DEF_RATING")),
            "ts_pct":     safe_float(row.get("TS_PCT")),
            "efg_pct":    safe_float(row.get("EFG_PCT")),
            "usg_pct":    safe_float(row.get("USG_PCT")),
        }
        if pid in out:
            prev = out[pid]
            total_gp = prev["gp"] + gp
            for f in ["def_rating", "ts_pct", "efg_pct", "usg_pct"]:
                a, b = prev.get(f), rec.get(f)
                if a is not None and b is not None and total_gp > 0:
                    prev[f] = (a * prev["gp"] + b * gp) / total_gp
                elif b is not None:
                    prev[f] = b
            prev["gp"] = total_gp
        else:
            out[pid] = rec
    return out


def fetch_advanced_pg(season: str, season_type: str) -> dict:
    label = f"Advanced PerGame [{season_type}]"
    print(f"    -> {label} ...", flush=True)
    result = try_season_types(_fetch_advanced, season, season_type, label)
    print(f"       {len(result)} players", flush=True)
    return result


def _fetch_defense(season: str, season_type: str) -> dict:
    r = LeagueDashPlayerStats(
        season=season,
        per_mode_detailed="PerGame",
        measure_type_detailed_defense="Defense",
        season_type_all_star=season_type,
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    if df.empty:
        return {}
    out = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("PLAYER_ID"))
        if pid is None:
            continue
        gp = safe_int(row.get("GP")) or 0
        rec = {"gp": gp, "def_reb": safe_float(row.get("DREB"))}
        if pid in out:
            prev = out[pid]
            total_gp = prev["gp"] + gp
            a, b = prev.get("def_reb"), rec.get("def_reb")
            if a is not None and b is not None and total_gp > 0:
                prev["def_reb"] = (a * prev["gp"] + b * gp) / total_gp
            elif b is not None:
                prev["def_reb"] = b
            prev["gp"] = total_gp
        else:
            out[pid] = rec
    return out


def fetch_defense(season: str, season_type: str) -> dict:
    label = f"Defense PerGame [{season_type}]"
    print(f"    -> {label} ...", flush=True)
    result = try_season_types(_fetch_defense, season, season_type, label)
    print(f"       {len(result)} players", flush=True)
    return result


def _fetch_shot_locations(season: str, season_type: str) -> dict:
    r = LeagueDashPlayerShotLocations(
        season=season,
        per_mode_detailed="PerGame",
        measure_type_simple="Base",
        season_type_all_star=season_type,
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    if df.empty:
        return {}
    if hasattr(df.columns, "levels"):
        df.columns = [f"{z}|{s}" if z else s for z, s in df.columns]
    out = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("PLAYER_ID"))
        if pid is None:
            continue

        def zone_pct(zone: str):
            return safe_float(row.get(f"{zone}|FG_PCT"))

        corner3 = zone_pct("Corner 3")
        if corner3 is None:
            lc_a = safe_float(row.get("Left Corner 3|FGA"))
            rc_a = safe_float(row.get("Right Corner 3|FGA"))
            if lc_a and rc_a and (lc_a + rc_a) > 0:
                lc_m = safe_float(row.get("Left Corner 3|FGM")) or 0
                rc_m = safe_float(row.get("Right Corner 3|FGM")) or 0
                corner3 = round((lc_m + rc_m) / (lc_a + rc_a), 4)

        out[pid] = {
            "fg_pct_rim":          zone_pct("Restricted Area"),
            "fg_pct_mid":          zone_pct("Mid-Range"),
            "fg3_pct_corner":      corner3,
            "fg3_pct_above_break": zone_pct("Above the Break 3"),
        }
    return out


def fetch_shot_locations(season: str, season_type: str) -> dict:
    label = f"Shot Locations [{season_type}]"
    print(f"    -> {label} ...", flush=True)
    result = try_season_types(_fetch_shot_locations, season, season_type, label)
    print(f"       {len(result)} players", flush=True)
    return result


def _fetch_totals_minutes(season: str, season_type: str) -> dict:
    """Totals Base — PLAYER_ID → total NBA minutes (for minute-weighted merges)."""
    r = LeagueDashPlayerStats(
        season=season,
        per_mode_detailed="Totals",
        measure_type_detailed_defense="Base",
        season_type_all_star=season_type,
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    if df.empty:
        return {}
    out = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("PLAYER_ID"))
        if pid is None:
            continue
        out[pid] = {"total_min": safe_float(row.get("MIN"))}
    return out


def fetch_totals_minutes(season: str, season_type: str) -> dict:
    label = f"Totals MIN [{season_type}]"
    print(f"    -> {label} ...", flush=True)
    result = try_season_types(_fetch_totals_minutes, season, season_type, label)
    print(f"       {len(result)} players", flush=True)
    return result


def _fetch_rim_defense(season: str, season_type: str) -> dict:
    r = LeagueDashPtDefend(
        season=season,
        per_mode_simple="Totals",
        defense_category="Less Than 6Ft",
        season_type_all_star=season_type,
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    if df.empty:
        return {}
    out = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("CLOSE_DEF_PERSON_ID"))
        if pid is None:
            continue
        out[pid] = {
            "opp_fga_at_rim":     safe_float(row.get("FGA_LT_06")),
            "opp_fg_pct_at_rim": safe_float(row.get("LT_06_PCT")),
        }
    return out


def fetch_rim_defense(season: str, season_type: str) -> dict:
    label = f"Rim defense Totals [<6ft] [{season_type}]"
    print(f"    -> {label} ...", flush=True)
    result = try_season_types(_fetch_rim_defense, season, season_type, label)
    print(f"       {len(result)} players", flush=True)
    return result


def _fetch_three_pt_defense(season: str, season_type: str) -> dict:
    r = LeagueDashPtDefend(
        season=season,
        per_mode_simple="Totals",
        defense_category="3 Pointers",
        season_type_all_star=season_type,
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    if df.empty:
        return {}
    out = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("CLOSE_DEF_PERSON_ID"))
        if pid is None:
            continue
        out[pid] = {
            "opp_fg3a_contested":    safe_float(row.get("FG3A")),
            "opp_fg3_pct_contested": safe_float(row.get("FG3_PCT")),
        }
    return out


def fetch_three_pt_defense(season: str, season_type: str) -> dict:
    label = f"3PT defense Totals [{season_type}]"
    print(f"    -> {label} ...", flush=True)
    result = try_season_types(_fetch_three_pt_defense, season, season_type, label)
    print(f"       {len(result)} players", flush=True)
    return result


def _fetch_hustle(season: str, season_type: str) -> dict:
    r = LeagueHustleStatsPlayer(
        season=season,
        per_mode_time="PerGame",
        season_type_all_star=season_type,
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    if df.empty:
        return {}
    out = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("PLAYER_ID"))
        if pid is None:
            continue
        out[pid] = {
            "deflections": safe_float(row.get("DEFLECTIONS")),
        }
    return out


def fetch_hustle(season: str, season_type: str) -> dict:
    label = f"Hustle [{season_type}]"
    print(f"    -> {label} ...", flush=True)
    result = try_season_types(_fetch_hustle, season, season_type, label)
    print(f"       {len(result)} players", flush=True)
    return result


# ---------------------------------------------------------------------------
# Fetch one season type — returns {player_id: full_record}
# ---------------------------------------------------------------------------

def fetch_season_type(season: str, season_type: str) -> dict:
    per100   = fetch_per100(season, season_type);           snooze(f"per100 [{season_type}]")
    base_pg  = fetch_base_pg(season, season_type);          snooze(f"base [{season_type}]")
    adv      = fetch_advanced_pg(season, season_type);      snooze(f"adv [{season_type}]")
    defense  = fetch_defense(season, season_type);          snooze(f"def [{season_type}]")
    shotloc  = fetch_shot_locations(season, season_type);   snooze(f"shot [{season_type}]")
    rim_def  = fetch_rim_defense(season, season_type);      snooze(f"rim [{season_type}]")
    three_d  = fetch_three_pt_defense(season, season_type);  snooze(f"3pt [{season_type}]")
    totals_m = fetch_totals_minutes(season, season_type);    snooze(f"MIN [{season_type}]")
    hustle   = fetch_hustle(season, season_type)

    all_pids = (
        set(per100)
        | set(base_pg)
        | set(adv)
        | set(defense)
        | set(hustle)
        | set(rim_def)
        | set(three_d)
        | set(totals_m)
        | set(shotloc)
    )
    records = {}

    for pid in all_pids:
        p   = per100.get(pid, {})
        bp  = base_pg.get(pid, {})
        a   = adv.get(pid, {})
        d   = defense.get(pid, {})
        sl  = shotloc.get(pid, {})
        rd  = rim_def.get(pid, {})
        td  = three_d.get(pid, {})
        tm  = totals_m.get(pid, {})
        h   = hustle.get(pid, {})

        gp = p.get("gp") or a.get("gp") or d.get("gp") or 0

        records[pid] = {
            "player_name": p.get("player_name", ""),
            "team_id":     p.get("team_id", 0),
            "team_abbr":   p.get("team_abbr", ""),
            "gp":          gp,

            "pts_per100":  p.get("pts_per100"),
            "reb_per100":  p.get("reb_per100"),
            "ast_per100":  p.get("ast_per100"),
            "tov_per100":  p.get("tov_per100"),
            "stl_per100":  p.get("stl_per100"),
            "blk_per100":  p.get("blk_per100"),
            "fga_per100":  p.get("fga_per100"),
            "fg3a_per100": p.get("fg3a_per100"),
            "fta_per100":  p.get("fta_per100"),

            "off_reb":    bp.get("off_reb"),
            "def_reb":    d.get("def_reb"),
            "def_rating": a.get("def_rating"),
            "blk_pct":    None,
            "stl_pct":    None,
            "deflections": h.get("deflections"),

            "opp_fga_at_rim":        rd.get("opp_fga_at_rim"),
            "opp_fg_pct_at_rim":     rd.get("opp_fg_pct_at_rim"),
            "opp_fg3a_contested":    td.get("opp_fg3a_contested"),
            "opp_fg3_pct_contested": td.get("opp_fg3_pct_contested"),

            "usg_pct":    a.get("usg_pct"),
            "total_min":  tm.get("total_min"),

            "ts_pct":  a.get("ts_pct") or p.get("ts_pct"),
            "efg_pct": a.get("efg_pct") or p.get("efg_pct"),

            "fg_pct_rim":          sl.get("fg_pct_rim"),
            "fg_pct_mid":          sl.get("fg_pct_mid"),
            "fg3_pct_corner":      sl.get("fg3_pct_corner"),
            "fg3_pct_above_break": sl.get("fg3_pct_above_break"),

            "contested_shot_pct": None,
            "open_shot_pct":      None,
        }

    return records


# ---------------------------------------------------------------------------
# Merge Play-In + Playoffs (same calendar postseason year)
# ---------------------------------------------------------------------------

def merge_volume_field(va, vb) -> float | None:
    if va is None and vb is None:
        return None
    return (va or 0.0) + (vb or 0.0)


def merge_minute_weighted_rates(po: dict, pi: dict, field: str) -> float | None:
    m_po = float(po.get("total_min") or 0.0)
    m_pi = float(pi.get("total_min") or 0.0)
    tot_m = m_po + m_pi
    v_po = po.get(field)
    v_pi = pi.get(field)
    if tot_m <= 0:
        return v_po if v_po is not None else v_pi
    if v_po is None:
        return v_pi
    if v_pi is None:
        return v_po
    return (v_po * m_po + v_pi * m_pi) / tot_m


def merge_season_types(playoffs: dict, playin: dict) -> dict:
    all_pids = set(playoffs) | set(playin)
    merged = {}

    for pid in all_pids:
        in_po = pid in playoffs
        in_pi = pid in playin

        if in_po and in_pi:
            po = playoffs[pid]
            pi = playin[pid]
            gp_po = po.get("gp") or 0
            gp_pi = pi.get("gp") or 0
            total_gp = gp_po + gp_pi

            rec = dict(po)
            rec["gp"] = total_gp

            for f in GP_WEIGHT_RATE_FIELDS:
                a, b = po.get(f), pi.get(f)
                if a is not None and b is not None and total_gp > 0:
                    rec[f] = (a * gp_po + b * gp_pi) / total_gp
                elif a is not None:
                    rec[f] = a
                else:
                    rec[f] = b

            for f in SUM_MERGE_FIELDS:
                rec[f] = merge_volume_field(po.get(f), pi.get(f))

            for f in MIN_WEIGHT_RATE_FIELDS:
                rec[f] = merge_minute_weighted_rates(po, pi, f)

        elif in_po:
            rec = dict(playoffs[pid])
        else:
            rec = dict(playin[pid])

        rec.pop("total_min", None)
        merged[pid] = rec

    return merged


# ---------------------------------------------------------------------------
# Correct contested/open shot pct via LeagueDashPlayerPtShot buckets
# (same methodology as fix_shot_pct.py used for player_stats_advanced)
# ---------------------------------------------------------------------------

def _fetch_pt_shot_bucket(season: str, dist_range: str, season_type: str) -> dict:
    """
    Returns {player_id: total_fga} for one distance bucket + season type.
    Uses Totals so FGA counts are raw and additive across season types.
    """
    print(f"      -> PtShot [{season_type}] {dist_range} ...", flush=True)
    alternates = [season_type]
    if season_type == SEASON_TYPE_PLAYIN:
        alternates.append("PlayIn")

    df = None
    for st in alternates:
        try:
            r = LeagueDashPlayerPtShot(
                season=season,
                close_def_dist_range_nullable=dist_range,
                per_mode_simple="Totals",
                season_type_all_star=st,
                timeout=TIMEOUT,
            )
            df = r.get_data_frames()[0]
            if df is not None and not df.empty:
                break
        except Exception as exc:
            print(f"        [warn] PtShot [{st}] {dist_range}: {exc}", flush=True)

    if df is None or df.empty:
        return {}

    out: dict[int, float] = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("PLAYER_ID"))
        fga = safe_float(row.get("FGA"))
        if pid is None:
            continue
        out[pid] = out.get(pid, 0.0) + (fga or 0.0)
    return out


def build_shot_contest_map(season: str) -> dict:
    """
    Fetches all 4 distance buckets for both Playoffs + Play-In, sums raw FGA
    totals (safe — counts are not rates), and returns per-player:
        {player_id: {"contested_shot_pct": float|None, "open_shot_pct": float|None}}

    Correct formula:
        contested_shot_pct = (Very Tight FGA + Tight FGA) / total_tracking_FGA
        open_shot_pct      = (Open FGA + Wide Open FGA)   / total_tracking_FGA
    """
    combined: dict[str, dict[int, float]] = {d: {} for d in DIST_RANGES}

    for season_type in [SEASON_TYPE_PLAYOFFS, SEASON_TYPE_PLAYIN]:
        for i, dist in enumerate(DIST_RANGES):
            bucket = _fetch_pt_shot_bucket(season, dist, season_type)
            for pid, fga in bucket.items():
                combined[dist][pid] = combined[dist].get(pid, 0.0) + fga

            # Sleep between every bucket request — skip only after the very last one
            is_last = (season_type == SEASON_TYPE_PLAYIN and i == len(DIST_RANGES) - 1)
            if not is_last:
                snooze("next bucket")

    all_pids: set[int] = set()
    for bdata in combined.values():
        all_pids.update(bdata.keys())

    result: dict[int, dict] = {}
    for pid in all_pids:
        fga_per_bucket = {d: combined[d].get(pid, 0.0) for d in DIST_RANGES}
        total_fga     = sum(fga_per_bucket.values())
        contested_fga = sum(v for d, v in fga_per_bucket.items() if d in CONTESTED_BUCKETS)
        open_fga      = total_fga - contested_fga

        if total_fga > 0:
            result[pid] = {
                "contested_shot_pct": round(contested_fga / total_fga, 4),
                "open_shot_pct":      round(open_fga      / total_fga, 4),
            }
        else:
            result[pid] = {"contested_shot_pct": None, "open_shot_pct": None}

    return result


# ---------------------------------------------------------------------------
# DB upsert
# ---------------------------------------------------------------------------

UPSERT_SQL = """
INSERT INTO player_stats_advanced_playoffs (
    season, player_id, player_name, team_id, team_abbr, position,
    pts_per100, reb_per100, ast_per100, tov_per100, stl_per100, blk_per100,
    fga_per100, fg3a_per100, fta_per100,
    off_reb, def_reb, def_rating, deflections,
    opp_fga_at_rim, opp_fg_pct_at_rim,
    opp_fg3a_contested, opp_fg3_pct_contested,
    usg_pct,
    ts_pct, efg_pct,
    fg_pct_rim, fg_pct_mid, fg3_pct_corner, fg3_pct_above_break,
    contested_shot_pct, open_shot_pct
) VALUES (
    :season, :player_id, :player_name, :team_id, :team_abbr, :position,
    :pts_per100, :reb_per100, :ast_per100, :tov_per100, :stl_per100, :blk_per100,
    :fga_per100, :fg3a_per100, :fta_per100,
    :off_reb, :def_reb, :def_rating, :deflections,
    :opp_fga_at_rim, :opp_fg_pct_at_rim,
    :opp_fg3a_contested, :opp_fg3_pct_contested,
    :usg_pct,
    :ts_pct, :efg_pct,
    :fg_pct_rim, :fg_pct_mid, :fg3_pct_corner, :fg3_pct_above_break,
    :contested_shot_pct, :open_shot_pct
)
ON CONFLICT (season, player_id, team_id) DO UPDATE SET
    player_name             = excluded.player_name,
    team_abbr               = excluded.team_abbr,
    position                = excluded.position,
    pts_per100              = excluded.pts_per100,
    reb_per100              = excluded.reb_per100,
    ast_per100              = excluded.ast_per100,
    tov_per100              = excluded.tov_per100,
    stl_per100              = excluded.stl_per100,
    blk_per100              = excluded.blk_per100,
    fga_per100              = excluded.fga_per100,
    fg3a_per100             = excluded.fg3a_per100,
    fta_per100              = excluded.fta_per100,
    off_reb                 = excluded.off_reb,
    def_reb                 = excluded.def_reb,
    def_rating              = excluded.def_rating,
    deflections             = excluded.deflections,
    opp_fga_at_rim          = excluded.opp_fga_at_rim,
    opp_fg_pct_at_rim       = excluded.opp_fg_pct_at_rim,
    opp_fg3a_contested      = excluded.opp_fg3a_contested,
    opp_fg3_pct_contested   = excluded.opp_fg3_pct_contested,
    usg_pct                 = excluded.usg_pct,
    ts_pct                  = excluded.ts_pct,
    efg_pct                 = excluded.efg_pct,
    fg_pct_rim              = excluded.fg_pct_rim,
    fg_pct_mid              = excluded.fg_pct_mid,
    fg3_pct_corner          = excluded.fg3_pct_corner,
    fg3_pct_above_break     = excluded.fg3_pct_above_break,
    contested_shot_pct      = excluded.contested_shot_pct,
    open_shot_pct           = excluded.open_shot_pct
"""


def upsert_season(
    con: sqlite3.Connection,
    season: str,
    merged: dict,
    shot_contest: dict,
) -> int:
    # Pull position from player_stats_basic_playoffs for the same player+season
    cur = con.cursor()
    pos_map = {
        r[0]: r[1] for r in cur.execute(
            "SELECT player_id, position FROM player_stats_basic_playoffs WHERE season=?",
            (season,)
        )
    }

    rows = []
    for pid, rec in merged.items():
        sc = shot_contest.get(pid, {})
        rows.append({
            "season":      season,
            "player_id":   pid,
            "player_name": rec.get("player_name", ""),
            "team_id":     rec.get("team_id") or 0,
            "team_abbr":   rec.get("team_abbr", ""),
            "position":    pos_map.get(pid),
            **{f: rec.get(f) for f in GP_WEIGHT_RATE_FIELDS},
            **{f: rec.get(f) for f in SUM_MERGE_FIELDS},
            **{f: rec.get(f) for f in MIN_WEIGHT_RATE_FIELDS},
            "contested_shot_pct": sc.get("contested_shot_pct"),
            "open_shot_pct":      sc.get("open_shot_pct"),
        })

    cur.executemany(UPSERT_SQL, rows)
    con.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(force: bool = False) -> None:
    print(f"[fetch_playoff_advanced] DB: {DB_PATH}", flush=True)
    if force:
        print("[fetch_playoff_advanced] --force  → re-fetching populated seasons.", flush=True)

    con = sqlite3.connect(DB_PATH)
    ensure_playoffs_advanced_schema(con)

    grand_total = 0
    skip_populated = SKIP_IF_POPULATED and not force

    for idx, season in enumerate(SEASONS, start=1):
        print(f"\n  >> Season {season}  ({idx}/{len(SEASONS)})", flush=True)

        if skip_populated:
            existing = con.execute(
                "SELECT COUNT(*) FROM player_stats_advanced_playoffs WHERE season=?",
                (season,)
            ).fetchone()[0]
            if existing > 0:
                print(
                    f"[SKIP] {season} already has {existing} rows — "
                    f"use --force to refresh.",
                    flush=True,
                )
                grand_total += existing
                continue

        print(f"\n{'=' * 60}", flush=True)
        print(f"[POSTSEASON {season}]", flush=True)
        print(f"{'=' * 60}", flush=True)

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
                fut_po = ex.submit(fetch_season_type, season, SEASON_TYPE_PLAYOFFS)
                fut_pi = ex.submit(fetch_season_type, season, SEASON_TYPE_PLAYIN)
                po_data = fut_po.result()
                pi_data = fut_pi.result()

            po_only = len(set(po_data) - set(pi_data))
            pi_only = len(set(pi_data) - set(po_data))
            both    = len(set(po_data) & set(pi_data))
            print(f"\n  [merge]  Playoffs-only={po_only}  PlayIn-only={pi_only}  Both={both}", flush=True)
            print(
                "  [merge]  Volumes SUM (rim FGA, 3PA contested); "
                "rates MIN-weighted (rim FG%, 3P%, USG%).",
                flush=True,
            )

            merged = merge_season_types(po_data, pi_data)

            print(f"\n  [pt-shot] fetching 4 distance buckets × 2 season types ...", flush=True)
            shot_contest = build_shot_contest_map(season)
            print(f"  [pt-shot] {len(shot_contest)} players with tracking data", flush=True)

            n = upsert_season(con, season, merged, shot_contest)
            grand_total += n
            print(f"\n[POSTSEASON {season}] Upserted {n} player rows.", flush=True)

        except Exception as exc:
            print(f"  [ERROR] {season}: {exc}", flush=True)
            import traceback
            traceback.print_exc()
            con.rollback()

        if season != SEASONS[-1]:
            snooze(f"between seasons: {season} -> next")

    con.close()
    print(f"\n[DONE] Total player-rows touched (insert/update/skip count): {grand_total}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Hydrate player_stats_advanced_playoffs from NBA Stats APIs.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch seasons even when rows already exist (recommended after schema fix).",
    )
    args = parser.parse_args()
    run(force=args.force)
