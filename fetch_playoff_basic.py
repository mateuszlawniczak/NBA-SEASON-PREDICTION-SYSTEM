"""
fetch_playoff_basic.py
----------------------
Builds and hydrates player_stats_basic_playoffs in nba_data.db
for seasons 2020-21 through 2025-26.

API calls per season (4 total, all with random 5.2-8.8 s delay):
  Pass 1 — SeasonType="Playoffs":
    1. LeagueDashPlayerStats  (PerMode=Totals, Base)
    2. LeagueDashPlayerBioStats
  Pass 2 — SeasonType="Play In":
    3. LeagueDashPlayerStats  (PerMode=Totals, Base)
    4. LeagueDashPlayerBioStats

Merge logic (players appearing in both PlayIn + Playoffs):
  - Sum counting stats: gp, gs, total_min, fgm, fga, fg3m, fg3a,
                        ftm, fta, oreb, dreb, reb, ast, stl, blk, tov, pf, pts
  - Recalculate rates from new sums:
      mpg     = total_min / gp
      pts/reb/ast/tov/stl/blk per game = total / gp
      fg_pct  = fgm  / fga
      fg3_pct = fg3m / fg3a
      ft_pct  = ftm  / fta
  - Bio data (height, weight, age, position): Playoffs preferred, PlayIn as fallback

Schema: identical columns to player_stats_basic (including Phase-3.5 bio additions).
usg_pct is stored as NULL — requires a separate Advanced endpoint not included in
the two-pass spec and cannot be simply summed across season types.
"""

import sqlite3
import time
import random
import math
import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import (
    LeagueDashPlayerStats,
    LeagueDashPlayerBioStats,
)

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

# nba_api accepted values for season_type_all_star
SEASON_TYPE_PLAYOFFS = "Playoffs"
SEASON_TYPE_PLAYIN   = "Play In"

TIMEOUT = 90  # seconds per request

# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

DDL_TABLE = """
CREATE TABLE IF NOT EXISTS player_stats_basic_playoffs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    season              TEXT    NOT NULL,
    player_id           INTEGER NOT NULL,
    player_name         TEXT    NOT NULL,
    team_id             INTEGER,
    team_abbr           TEXT,
    position            TEXT,

    gp                  INTEGER,
    gs                  INTEGER,
    mpg                 REAL,
    pts                 REAL,
    reb                 REAL,
    ast                 REAL,
    tov                 REAL,
    stl                 REAL,
    blk                 REAL,
    fg_pct              REAL,
    fg3_pct             REAL,
    ft_pct              REAL,
    usg_pct             REAL,
    total_minutes       INTEGER,

    height              REAL,
    weight              REAL,
    age                 REAL,
    years_in_league     INTEGER,

    created_at          TEXT DEFAULT (datetime('now')),
    UNIQUE (season, player_id, team_id)
);
"""

DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_psbp_season ON player_stats_basic_playoffs (season);",
    "CREATE INDEX IF NOT EXISTS idx_psbp_player ON player_stats_basic_playoffs (player_id);",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def snooze(label: str = "") -> None:
    """Random anti-bot sleep: 5.2–8.8 s between every API request."""
    t = random.uniform(5.2, 8.8)
    tag = f" [{label}]" if label else ""
    print(f"    [wait]  sleeping {t:.1f}s{tag} ...", flush=True)
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


def season_start_year(season: str) -> int:
    """'2022-23' -> 2022"""
    return int(season.split("-")[0])


def height_str_to_inches(raw) -> "float | None":
    """'6-11' -> 83.0  |  '6-2' -> 74.0  |  None/'' -> None"""
    if not raw:
        return None
    parts = str(raw).strip().split("-")
    if len(parts) == 2:
        try:
            return float(int(parts[0]) * 12 + int(parts[1]))
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Schema setup
# ---------------------------------------------------------------------------

def ensure_schema(cur: sqlite3.Cursor) -> None:
    cur.execute(DDL_TABLE)
    for idx in DDL_INDEXES:
        cur.execute(idx)
    print("  [schema] player_stats_basic_playoffs table ready.", flush=True)


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

def _empty_totals_row(player_name: str, team_id: int, team_abbr: str) -> dict:
    return {
        "player_name": player_name,
        "team_id":     team_id,
        "team_abbr":   team_abbr,
        "gp":          0,
        "gs":          0,
        "tot_min":     0.0,
        "fgm":         0.0,
        "fga":         0.0,
        "fg3m":        0.0,
        "fg3a":        0.0,
        "ftm":         0.0,
        "fta":         0.0,
        "oreb":        0.0,
        "dreb":        0.0,
        "reb":         0.0,
        "ast":         0.0,
        "stl":         0.0,
        "blk":         0.0,
        "tov":         0.0,
        "pf":          0.0,
        "pts":         0.0,
    }


COUNTING_FIELDS = [
    "gp", "gs", "tot_min",
    "fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
    "oreb", "dreb", "reb", "ast", "stl", "blk", "tov", "pf", "pts",
]


def _call_league_dash_totals(season: str, season_type: str):
    """Raw API call; raises on failure."""
    r = LeagueDashPlayerStats(
        season=season,
        per_mode_detailed="Totals",
        measure_type_detailed_defense="Base",
        season_type_all_star=season_type,
        timeout=TIMEOUT,
    )
    return r.get_data_frames()[0]


def fetch_totals(season: str, season_type: str) -> dict:
    """
    LeagueDashPlayerStats  PerMode=Totals, Base.
    Returns an empty dict (with a warning) if the API has no data for
    that season_type (e.g. Play-In not held or not indexed for that year).
    """
    label = season_type.replace(" ", "")
    print(f"    -> Totals [{season_type}] ...", flush=True)

    alternates = [season_type]
    if season_type == SEASON_TYPE_PLAYIN:
        alternates.append("PlayIn")
    elif season_type == "PlayIn":
        alternates.append(SEASON_TYPE_PLAYIN)

    df = None
    for st in alternates:
        try:
            df = _call_league_dash_totals(season, st)
            break
        except Exception as exc:
            print(f"    [warn]  Totals [{st}] returned no data: {exc}", flush=True)

    if df is None or df.empty:
        print(f"    [skip]  No Totals data for [{season_type}] — treating as empty.", flush=True)
        return {}

    out: dict = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("PLAYER_ID"))
        if pid is None:
            continue

        tid      = safe_int(row.get("TEAM_ID")) or 0
        name     = row.get("PLAYER_NAME", "")
        abbr     = row.get("TEAM_ABBREVIATION", "")
        gp       = safe_int(row.get("GP"))    or 0
        gs       = safe_int(row.get("GS"))    or 0
        tot_min  = safe_float(row.get("MIN")) or 0.0

        if pid in out:
            rec = out[pid]
            rec["team_id"]   = 0
            rec["team_abbr"] = "TOT"
            rec["gp"]       += gp
            rec["gs"]       += gs
            rec["tot_min"]  += tot_min
            for field, col in [
                ("fgm",  "FGM"),  ("fga",  "FGA"),
                ("fg3m", "FG3M"), ("fg3a", "FG3A"),
                ("ftm",  "FTM"),  ("fta",  "FTA"),
                ("oreb", "OREB"), ("dreb", "DREB"),
                ("reb",  "REB"),  ("ast",  "AST"),
                ("stl",  "STL"),  ("blk",  "BLK"),
                ("tov",  "TOV"),  ("pf",   "PF"),
                ("pts",  "PTS"),
            ]:
                rec[field] += safe_float(row.get(col)) or 0.0
        else:
            rec = _empty_totals_row(name, tid, abbr)
            rec["gp"]      = gp
            rec["gs"]      = gs
            rec["tot_min"] = tot_min
            for field, col in [
                ("fgm",  "FGM"),  ("fga",  "FGA"),
                ("fg3m", "FG3M"), ("fg3a", "FG3A"),
                ("ftm",  "FTM"),  ("fta",  "FTA"),
                ("oreb", "OREB"), ("dreb", "DREB"),
                ("reb",  "REB"),  ("ast",  "AST"),
                ("stl",  "STL"),  ("blk",  "BLK"),
                ("tov",  "TOV"),  ("pf",   "PF"),
                ("pts",  "PTS"),
            ]:
                rec[field] = safe_float(row.get(col)) or 0.0
            out[pid] = rec

    print(f"    -> {len(out)} unique players [{label}]", flush=True)
    return out


def fetch_bio(season: str, season_type: str) -> dict:
    """
    LeagueDashPlayerBioStats.
    Returns an empty dict (with a warning) if the API has no data.
    """
    label = season_type.replace(" ", "")
    print(f"    -> Bio [{season_type}] ...", flush=True)

    alternates = [season_type]
    if season_type == SEASON_TYPE_PLAYIN:
        alternates.append("PlayIn")
    elif season_type == "PlayIn":
        alternates.append(SEASON_TYPE_PLAYIN)

    df = None
    for st in alternates:
        try:
            r = LeagueDashPlayerBioStats(
                season=season,
                per_mode_simple="PerGame",
                season_type_all_star=st,
                timeout=TIMEOUT,
            )
            df = r.get_data_frames()[0]
            break
        except Exception as exc:
            print(f"    [warn]  Bio [{st}] returned no data: {exc}", flush=True)

    if df is None or df.empty:
        print(f"    [skip]  No Bio data for [{season_type}] — treating as empty.", flush=True)
        return {}

    out: dict = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("PLAYER_ID"))
        if pid is None:
            continue

        draft_raw = row.get("DRAFT_YEAR")
        try:
            draft_year = int(draft_raw) if draft_raw and str(draft_raw).strip().isdigit() else None
        except (TypeError, ValueError):
            draft_year = None

        pos = row.get("PLAYER_POSITION") or row.get("POSITION") or None

        out[pid] = {
            "position":   str(pos).strip() if pos else None,
            "height":     height_str_to_inches(row.get("PLAYER_HEIGHT")),
            "weight":     safe_float(row.get("PLAYER_WEIGHT")),
            "age":        safe_float(row.get("AGE")),
            "draft_year": draft_year,
        }

    print(f"    -> {len(out)} players [{label}]", flush=True)
    return out


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------

def merge_season_types(playoffs: dict, playin: dict) -> dict:
    all_pids = set(playoffs) | set(playin)
    merged: dict = {}

    for pid in all_pids:
        in_po = pid in playoffs
        in_pi = pid in playin

        if in_po and in_pi:
            rec = dict(playoffs[pid])
            for field in COUNTING_FIELDS:
                rec[field] = (playoffs[pid].get(field) or 0) + (playin[pid].get(field) or 0)
        elif in_po:
            rec = dict(playoffs[pid])
        else:
            rec = dict(playin[pid])

        merged[pid] = rec

    return merged


# ---------------------------------------------------------------------------
# DB upsert
# ---------------------------------------------------------------------------

def upsert_season(
    cur: sqlite3.Cursor,
    season: str,
    merged: dict,
    bio: dict,
) -> int:
    start_year = season_start_year(season)
    count = 0

    for pid, rec in merged.items():
        gp = rec.get("gp") or 0
        if gp == 0:
            continue

        bo = bio.get(pid, {})

        tot_min = rec.get("tot_min") or 0.0
        fgm     = rec.get("fgm")    or 0.0
        fga     = rec.get("fga")    or 0.0
        fg3m    = rec.get("fg3m")   or 0.0
        fg3a    = rec.get("fg3a")   or 0.0
        ftm     = rec.get("ftm")    or 0.0
        fta     = rec.get("fta")    or 0.0
        pts     = rec.get("pts")    or 0.0
        reb     = rec.get("reb")    or 0.0
        ast     = rec.get("ast")    or 0.0
        tov     = rec.get("tov")    or 0.0
        stl     = rec.get("stl")    or 0.0
        blk     = rec.get("blk")    or 0.0

        mpg     = round(tot_min / gp,  1) if gp   > 0 else None
        pts_pg  = round(pts     / gp,  1) if gp   > 0 else None
        reb_pg  = round(reb     / gp,  1) if gp   > 0 else None
        ast_pg  = round(ast     / gp,  1) if gp   > 0 else None
        tov_pg  = round(tov     / gp,  1) if gp   > 0 else None
        stl_pg  = round(stl     / gp,  1) if gp   > 0 else None
        blk_pg  = round(blk     / gp,  1) if gp   > 0 else None
        fg_pct  = round(fgm     / fga, 3) if fga  > 0 else None
        fg3_pct = round(fg3m    / fg3a,3) if fg3a > 0 else None
        ft_pct  = round(ftm     / fta, 3) if fta  > 0 else None
        total_minutes = round(tot_min)

        draft_year = bo.get("draft_year")
        if draft_year:
            yil = start_year - draft_year + 1
            years_in_league = max(yil, 1)
        else:
            years_in_league = None

        cur.execute(
            """
            INSERT INTO player_stats_basic_playoffs
                (season, player_id, player_name, team_id, team_abbr, position,
                 gp, gs, mpg, pts, reb, ast, tov, stl, blk,
                 fg_pct, fg3_pct, ft_pct, usg_pct, total_minutes,
                 height, weight, age, years_in_league)
            VALUES
                (?, ?, ?, ?, ?, ?,
                 ?, ?, ?, ?, ?, ?, ?, ?, ?,
                 ?, ?, ?, ?, ?,
                 ?, ?, ?, ?)
            ON CONFLICT (season, player_id, team_id)
            DO UPDATE SET
                player_name     = excluded.player_name,
                team_abbr       = excluded.team_abbr,
                position        = excluded.position,
                gp              = excluded.gp,
                gs              = excluded.gs,
                mpg             = excluded.mpg,
                pts             = excluded.pts,
                reb             = excluded.reb,
                ast             = excluded.ast,
                tov             = excluded.tov,
                stl             = excluded.stl,
                blk             = excluded.blk,
                fg_pct          = excluded.fg_pct,
                fg3_pct         = excluded.fg3_pct,
                ft_pct          = excluded.ft_pct,
                usg_pct         = excluded.usg_pct,
                total_minutes   = excluded.total_minutes,
                height          = excluded.height,
                weight          = excluded.weight,
                age             = excluded.age,
                years_in_league = excluded.years_in_league
            """,
            (
                season, pid, rec.get("player_name"), rec.get("team_id"),
                rec.get("team_abbr"), bo.get("position"),
                gp, rec.get("gs"), mpg,
                pts_pg, reb_pg, ast_pg, tov_pg, stl_pg, blk_pg,
                fg_pct, fg3_pct, ft_pct,
                None,
                total_minutes,
                bo.get("height"), bo.get("weight"), bo.get("age"), years_in_league,
            ),
        )
        count += 1

    return count


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run() -> None:
    print(f"[fetch_playoff_basic] DB path: {DB_PATH}", flush=True)

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    ensure_schema(cur)
    con.commit()

    grand_total = 0

    for season in SEASONS:
        print(f"\n{'=' * 50}", flush=True)
        print(f"[POSTSEASON {season}]", flush=True)
        print(f"{'=' * 50}", flush=True)

        try:
            playoffs_totals = fetch_totals(season, SEASON_TYPE_PLAYOFFS)
            snooze(f"{season} Playoffs totals -> bio")

            playoffs_bio = fetch_bio(season, SEASON_TYPE_PLAYOFFS)
            snooze(f"{season} Playoffs bio -> PlayIn totals")

            playin_totals = fetch_totals(season, SEASON_TYPE_PLAYIN)
            snooze(f"{season} PlayIn totals -> bio")

            playin_bio = fetch_bio(season, SEASON_TYPE_PLAYIN)

            bio: dict = dict(playin_bio)
            bio.update(playoffs_bio)

            merged = merge_season_types(playoffs_totals, playin_totals)

            playin_only  = len(set(playin_totals)  - set(playoffs_totals))
            playoff_only = len(set(playoffs_totals) - set(playin_totals))
            both         = len(set(playin_totals)  & set(playoffs_totals))
            print(
                f"  [merge]  Playoffs-only={playoff_only}  "
                f"PlayIn-only={playin_only}  Both={both}",
                flush=True,
            )

            n = upsert_season(cur, season, merged, bio)
            con.commit()
            grand_total += n
            print(f"\n[POSTSEASON {season}] Merged and inserted {n} players.", flush=True)

        except Exception as exc:
            print(f"  [ERROR] {season} failed: {exc}", flush=True)
            con.rollback()

        if season != SEASONS[-1]:
            snooze(f"between seasons: {season} -> next")

    con.close()
    print(f"\n[DONE] Total players inserted across all seasons: {grand_total}", flush=True)


if __name__ == "__main__":
    run()
