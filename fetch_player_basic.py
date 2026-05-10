"""
fetch_player_basic.py
---------------------
Fills player_stats_basic in nba_data.db for seasons 2020-21 through 2025-26.

API calls per season (3 total):
  1. LeagueDashPlayerStats  — PerGame Base     -> gp, gs, mpg, pts, reb, ast,
                                                  tov, stl, blk, fg_pct, fg3_pct, ft_pct
  2. LeagueDashPlayerStats  — PerGame Advanced -> usg_pct
  3. LeagueDashPlayerBioStats                  -> height (inches), weight (lbs),
                                                  age, draft_year -> years_in_league,
                                                  position

Random delay of 4-8 s between every request to stay under NBA API rate limits.
Also extends the table with height, weight, age, years_in_league columns if missing
(Phase 3.5 additions).
"""

import sqlite3
import time
import random
import math
import os
import sys

# Force UTF-8 output on Windows consoles so ASCII-only print() never fails
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

TIMEOUT = 90   # seconds per request before giving up


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def snooze(label: str = "") -> None:
    """Sleep a random 4-8 seconds between API requests."""
    t = random.uniform(4.0, 8.0)
    tag = f" [{label}]" if label else ""
    print(f"    [wait]  sleeping {t:.1f}s{tag} ...", flush=True)
    time.sleep(t)


def safe_float(val) -> "float | None":
    """Return float or None for NaN / None / bad values."""
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
    """
    Convert NBA height string to total inches.
    '6-11' -> 83.0   |   '6-2' -> 74.0   |   None/'' -> None
    """
    if not raw:
        return None
    parts = str(raw).strip().split("-")
    if len(parts) == 2:
        try:
            return float(int(parts[0]) * 12 + int(parts[1]))
        except ValueError:
            return None
    return None


def ensure_extra_columns(cur: sqlite3.Cursor) -> None:
    """
    Add Phase-3.5 bio columns to player_stats_basic if they don't exist yet.
    Uses PRAGMA table_info so it is safe to run against an already-migrated DB.
    """
    existing = {row[1] for row in cur.execute("PRAGMA table_info(player_stats_basic)")}
    additions = [
        ("height",           "REAL"),    # total inches  (e.g. 83.0 for 6-11)
        ("weight",           "REAL"),    # pounds
        ("age",              "REAL"),    # age during that season
        ("years_in_league",  "INTEGER"), # computed from draft_year
    ]
    for col, col_type in additions:
        if col not in existing:
            cur.execute(f"ALTER TABLE player_stats_basic ADD COLUMN {col} {col_type};")
            print(f"  [schema] Added column: {col} {col_type}", flush=True)


# ---------------------------------------------------------------------------
# Fetch functions
# ---------------------------------------------------------------------------

def fetch_base(season: str) -> dict:
    """
    LeagueDashPlayerStats PerGame Base.
    Key: (player_id, team_id)
    Returns per-game counting stats.
    """
    print(f"    -> Base PerGame ...", flush=True)
    r = LeagueDashPlayerStats(
        season=season,
        per_mode_detailed="PerGame",
        measure_type_detailed_defense="Base",
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    out = {}
    for _, row in df.iterrows():
        pid = safe_int(row["PLAYER_ID"])
        tid = safe_int(row["TEAM_ID"])
        if pid is None:
            continue
        key = (pid, tid)
        gp  = safe_int(row.get("GP"))
        mpg = safe_float(row.get("MIN"))
        out[key] = {
            "player_name": row["PLAYER_NAME"],
            "team_abbr":   row["TEAM_ABBREVIATION"],
            "gp":          gp,
            "gs":          safe_int(row.get("GS")),
            "mpg":         mpg,
            "pts":         safe_float(row.get("PTS")),
            "reb":         safe_float(row.get("REB")),
            "ast":         safe_float(row.get("AST")),
            "tov":         safe_float(row.get("TOV")),
            "stl":         safe_float(row.get("STL")),
            "blk":         safe_float(row.get("BLK")),
            "fg_pct":      safe_float(row.get("FG_PCT")),
            "fg3_pct":     safe_float(row.get("FG3_PCT")),
            "ft_pct":      safe_float(row.get("FT_PCT")),
            # total_minutes derived here; will be rounded to int
            "total_minutes": round(gp * mpg) if gp and mpg else None,
        }
    print(f"    -> {len(out)} player-team rows", flush=True)
    return out


def fetch_advanced(season: str) -> dict:
    """
    LeagueDashPlayerStats PerGame Advanced.
    Key: (player_id, team_id)
    Returns usg_pct.
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
        pid = safe_int(row["PLAYER_ID"])
        tid = safe_int(row["TEAM_ID"])
        if pid is None:
            continue
        out[(pid, tid)] = {
            "usg_pct": safe_float(row.get("USG_PCT")),
        }
    print(f"    -> {len(out)} player-team rows", flush=True)
    return out


def fetch_bio(season: str) -> dict:
    """
    LeagueDashPlayerBioStats PerGame.
    Key: player_id  (bio data is player-level, not split by team)
    Returns height (inches), weight, age, draft_year, position.
    """
    print(f"    -> Bio Stats ...", flush=True)
    r = LeagueDashPlayerBioStats(
        season=season,
        per_mode_simple="PerGame",
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    out = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("PLAYER_ID"))
        if pid is None:
            continue

        draft_raw = row.get("DRAFT_YEAR")
        try:
            draft_year = int(draft_raw) if draft_raw and str(draft_raw).strip().isdigit() else None
        except (TypeError, ValueError):
            draft_year = None

        # position column name varies across nba_api versions
        pos = (
            row.get("PLAYER_POSITION")
            or row.get("POSITION")
            or None
        )

        out[pid] = {
            "position":   str(pos).strip() if pos else None,
            "height":     height_str_to_inches(row.get("PLAYER_HEIGHT")),
            "weight":     safe_float(row.get("PLAYER_WEIGHT")),
            "age":        safe_float(row.get("AGE")),
            "draft_year": draft_year,
        }
    print(f"    -> {len(out)} players", flush=True)
    return out


# ---------------------------------------------------------------------------
# DB upsert
# ---------------------------------------------------------------------------

def upsert_season(
    cur: sqlite3.Cursor,
    season: str,
    base: dict,
    adv: dict,
    bio: dict,
) -> int:
    """
    Merge the three result dicts and upsert every row into player_stats_basic.
    Returns the number of rows processed.
    """
    start_year = season_start_year(season)
    count = 0

    for (pid, tid), b in base.items():
        a  = adv.get((pid, tid), {})
        bo = bio.get(pid, {})

        draft_year = bo.get("draft_year")
        if draft_year:
            yil = start_year - draft_year + 1
            years_in_league = max(yil, 1)  # clamp to >= 1 in case of edge cases
        else:
            years_in_league = None

        cur.execute(
            """
            INSERT INTO player_stats_basic
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
                season, pid, b["player_name"], tid, b.get("team_abbr"), bo.get("position"),
                b.get("gp"), b.get("gs"), b.get("mpg"),
                b.get("pts"), b.get("reb"), b.get("ast"), b.get("tov"),
                b.get("stl"), b.get("blk"),
                b.get("fg_pct"), b.get("fg3_pct"), b.get("ft_pct"),
                a.get("usg_pct"), b.get("total_minutes"),
                bo.get("height"), bo.get("weight"), bo.get("age"), years_in_league,
            ),
        )
        count += 1

    return count


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run() -> None:
    print(f"[fetch_player_basic] DB path: {DB_PATH}", flush=True)

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # Ensure Phase-3.5 columns exist before writing
    ensure_extra_columns(cur)
    con.commit()

    grand_total = 0

    for season in SEASONS:
        print(f"\n[{season}] ============================", flush=True)

        try:
            base = fetch_base(season)
            snooze(f"{season} base->adv")

            adv = fetch_advanced(season)
            snooze(f"{season} adv->bio")

            bio = fetch_bio(season)

            n = upsert_season(cur, season, base, adv, bio)
            con.commit()
            grand_total += n
            print(f"  [OK] {n} rows upserted for {season}", flush=True)

        except Exception as exc:
            print(f"  [ERROR] {season} failed: {exc}", flush=True)
            con.rollback()

        # Sleep before moving to the next season (skip after the final season)
        if season != SEASONS[-1]:
            snooze(f"between seasons {season} -> next")

    con.close()
    print(f"\n[DONE] Total rows upserted across all seasons: {grand_total}", flush=True)


if __name__ == "__main__":
    run()
