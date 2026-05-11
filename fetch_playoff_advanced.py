"""
fetch_playoff_advanced.py
-------------------------
Builds and hydrates player_stats_advanced_playoffs in nba_data.db
for seasons 2020-21 through 2025-26.

API calls per season (up to 12 — 6 endpoints × 2 season types):
  For each of SeasonType="Playoffs" and "PlayIn":
    1. LeagueDashPlayerStats  — Per100 Base       -> per-100 counting + ts%, efg%
    2. LeagueDashPlayerStats  — PerGame Advanced  -> def_rating, ts%, efg%
    3. LeagueDashPlayerStats  — PerGame Defense   -> def_reb
    4. LeagueDashPlayerShotLocations              -> shot-zone FG%
    5. LeagueDashPtDefend     — Less Than 6Ft     -> opp_fg_pct_at_rim
    6. LeagueHustleStatsPlayer                   -> deflections, contested_shot_pct

Merge rule for players appearing in BOTH season types:
  All rate / per-100 / per-game stats -> GP-weighted average.
  Identity (team, name) -> Playoffs preferred.

Anti-bot: random 5.2–8.8 s sleep between every API request.
"""

import sqlite3
import time
import random
import math
import os
import sys
import concurrent.futures

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
    "2020-21", "2021-22", "2022-23",
    "2023-24", "2024-25", "2025-26",
]

# Seasons already committed — skip them on re-run
SKIP_IF_POPULATED = True

SEASON_TYPE_PLAYOFFS = "Playoffs"
SEASON_TYPE_PLAYIN   = "Play In"

TIMEOUT = 90

# Rate-stat field names — all get GP-weighted merge
RATE_FIELDS = [
    "pts_per100", "reb_per100", "ast_per100", "tov_per100",
    "stl_per100", "blk_per100", "fga_per100", "fg3a_per100", "fta_per100",
    "def_reb", "def_rating", "deflections",
    "opp_fg_pct_at_rim", "opp_fg3_pct_contested",
    "ts_pct", "efg_pct",
    "fg_pct_rim", "fg_pct_mid", "fg3_pct_corner", "fg3_pct_above_break",
    "contested_shot_pct",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def snooze(label: str = "") -> None:
    """1.5–3 s — fast but respectful. Playoffs and PlayIn run in parallel
    threads so the effective wall-clock delay is already halved."""
    t = random.uniform(1.5, 3.0)
    tag = f" [{label}]" if label else ""
    print(f"    [wait]  {t:.1f}s{tag}", flush=True)
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
        }
        if pid in out:
            prev = out[pid]
            total_gp = prev["gp"] + gp
            for f in ["def_rating","ts_pct","efg_pct"]:
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


def _fetch_rim_defense(season: str, season_type: str) -> dict:
    r = LeagueDashPtDefend(
        season=season,
        per_mode_simple="PerGame",
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
        out[pid] = {"opp_fg_pct_at_rim": safe_float(row.get("LT_06_PCT"))}
    return out


def fetch_rim_defense(season: str, season_type: str) -> dict:
    label = f"Rim Defense [{season_type}]"
    print(f"    -> {label} ...", flush=True)
    result = try_season_types(_fetch_rim_defense, season, season_type, label)
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
        c2  = safe_float(row.get("CONTESTED_SHOTS_2PT")) or 0.0
        c3  = safe_float(row.get("CONTESTED_SHOTS_3PT")) or 0.0
        tot = safe_float(row.get("CONTESTED_SHOTS")) or 0.0
        out[pid] = {
            "deflections":            safe_float(row.get("DEFLECTIONS")),
            "contested_3pt_pg":       c3,
            "contested_shot_pct":     round(tot / (c2 + c3), 4) if (c2 + c3) > 0 else None,
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
    per100  = fetch_per100(season, season_type);         snooze(f"per100->adv [{season_type}]")
    adv     = fetch_advanced_pg(season, season_type);    snooze(f"adv->def [{season_type}]")
    defense = fetch_defense(season, season_type);        snooze(f"def->shotloc [{season_type}]")
    shotloc = fetch_shot_locations(season, season_type); snooze(f"shotloc->rim [{season_type}]")
    rim_def = fetch_rim_defense(season, season_type);    snooze(f"rim->hustle [{season_type}]")
    hustle  = fetch_hustle(season, season_type)

    all_pids = set(per100) | set(adv) | set(defense) | set(hustle)
    records = {}

    for pid in all_pids:
        p  = per100.get(pid, {})
        a  = adv.get(pid, {})
        d  = defense.get(pid, {})
        sl = shotloc.get(pid, {})
        rd = rim_def.get(pid, {})
        h  = hustle.get(pid, {})

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

            "def_reb":    d.get("def_reb"),
            "def_rating": a.get("def_rating"),
            "blk_pct":    None,
            "stl_pct":    None,
            "deflections":              h.get("deflections"),
            "opp_fg_pct_at_rim":        rd.get("opp_fg_pct_at_rim"),
            "opp_fg3_pct_contested":    h.get("contested_3pt_pg"),

            "ts_pct":  a.get("ts_pct") or p.get("ts_pct"),
            "efg_pct": a.get("efg_pct") or p.get("efg_pct"),

            "fg_pct_rim":          sl.get("fg_pct_rim"),
            "fg_pct_mid":          sl.get("fg_pct_mid"),
            "fg3_pct_corner":      sl.get("fg3_pct_corner"),
            "fg3_pct_above_break": sl.get("fg3_pct_above_break"),

            "contested_shot_pct": h.get("contested_shot_pct"),
            "open_shot_pct":      None,
        }

    return records


# ---------------------------------------------------------------------------
# GP-weighted merge of Playoffs + PlayIn
# ---------------------------------------------------------------------------

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
            total = gp_po + gp_pi

            rec = dict(po)   # Playoffs identity (team, name) is primary
            rec["gp"] = total

            for f in RATE_FIELDS:
                a, b = po.get(f), pi.get(f)
                if a is not None and b is not None and total > 0:
                    rec[f] = (a * gp_po + b * gp_pi) / total
                elif a is not None:
                    rec[f] = a
                else:
                    rec[f] = b
        elif in_po:
            rec = dict(playoffs[pid])
        else:
            rec = dict(playin[pid])

        merged[pid] = rec

    return merged


# ---------------------------------------------------------------------------
# DB upsert
# ---------------------------------------------------------------------------

UPSERT_SQL = """
INSERT INTO player_stats_advanced_playoffs (
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
    position               = excluded.position,
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


def upsert_season(con: sqlite3.Connection, season: str, merged: dict) -> int:
    # Pull position from player_stats_basic_playoffs for the same player+season
    cur  = con.cursor()
    pos_map = {
        r[0]: r[1] for r in cur.execute(
            "SELECT player_id, position FROM player_stats_basic_playoffs WHERE season=?",
            (season,)
        )
    }

    rows = []
    for pid, rec in merged.items():
        rows.append({
            "season":      season,
            "player_id":   pid,
            "player_name": rec.get("player_name", ""),
            "team_id":     rec.get("team_id") or 0,
            "team_abbr":   rec.get("team_abbr", ""),
            "position":    pos_map.get(pid),
            **{f: rec.get(f) for f in RATE_FIELDS},
            "blk_pct":        None,
            "stl_pct":        None,
            "open_shot_pct":  None,
        })

    cur.executemany(UPSERT_SQL, rows)
    con.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> None:
    print(f"[fetch_playoff_advanced] DB: {DB_PATH}", flush=True)
    con = sqlite3.connect(DB_PATH)
    grand_total = 0

    for season in SEASONS:
        # Skip seasons already in the table (safe re-run)
        if SKIP_IF_POPULATED:
            existing = con.execute(
                "SELECT COUNT(*) FROM player_stats_advanced_playoffs WHERE season=?",
                (season,)
            ).fetchone()[0]
            if existing > 0:
                print(f"\n[SKIP] {season} already has {existing} rows — skipping.", flush=True)
                grand_total += existing
                continue

        print(f"\n{'=' * 60}", flush=True)
        print(f"[POSTSEASON {season}]", flush=True)
        print(f"{'=' * 60}", flush=True)

        try:
            # Fetch Playoffs and PlayIn in parallel threads
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
                fut_po = ex.submit(fetch_season_type, season, SEASON_TYPE_PLAYOFFS)
                fut_pi = ex.submit(fetch_season_type, season, SEASON_TYPE_PLAYIN)
                po_data = fut_po.result()
                pi_data = fut_pi.result()

            po_only = len(set(po_data) - set(pi_data))
            pi_only = len(set(pi_data) - set(po_data))
            both    = len(set(po_data) & set(pi_data))
            print(f"\n  [merge]  Playoffs-only={po_only}  PlayIn-only={pi_only}  Both={both}", flush=True)

            merged = merge_season_types(po_data, pi_data)
            n = upsert_season(con, season, merged)
            grand_total += n
            print(f"\n[POSTSEASON {season}] Inserted {n} players.", flush=True)

        except Exception as exc:
            print(f"  [ERROR] {season}: {exc}", flush=True)
            con.rollback()

        if season != SEASONS[-1]:
            snooze(f"between seasons: {season} -> next")

    con.close()
    print(f"\n[DONE] Total players inserted across all seasons: {grand_total}", flush=True)


if __name__ == "__main__":
    run()
