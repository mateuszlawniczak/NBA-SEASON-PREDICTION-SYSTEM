"""
fix_shot_pct.py
---------------
Corrects corrupted contested_shot_pct and populates open_shot_pct in
player_stats_advanced using NBA tracking data from LeagueDashPlayerPtShot.

Root cause of corruption
------------------------
The original fetch_player_advanced.py used Hustle endpoint data and computed:
    contested_shot_pct = total_contested / (contested_2pt + contested_3pt)
— which is literally N/N → values cluster around 1.0, and open_shot_pct was
never populated at all.

Correct methodology (this script)
----------------------------------
For each season, pull FGA broken out by CloseDefDistRange across 4 buckets:
    'Very Tight'  (0-2 ft)  → contested
    'Tight'       (2-4 ft)  → contested
    'Open'        (4-6 ft)  → open
    'Wide Open'   (6+ ft)   → open

Per player:
    total_tracking_fga  = sum of FGA across all 4 buckets
    contested_numerator = FGA(Very Tight) + FGA(Tight)
    open_numerator      = FGA(Open)       + FGA(Wide Open)

    contested_shot_pct  = contested_numerator / total_tracking_fga
    open_shot_pct       = open_numerator      / total_tracking_fga

    If total_tracking_fga == 0  →  both set to NULL.
"""

import os
import sys
import math
import time
import random
import sqlite3

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import LeagueDashPlayerPtShot

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

# Seasons present in the database
SEASONS = [
    "2020-21",
    "2021-22",
    "2022-23",
    "2023-24",
    "2024-25",
    "2025-26",
]

# Tracking distance buckets in the order the API expects
DIST_RANGES = [
    "0-2 Feet - Very Tight",
    "2-4 Feet - Tight",
    "4-6 Feet - Open",
    "6+ Feet - Wide Open",
]

# Buckets counted as "contested"
CONTESTED_BUCKETS = {"0-2 Feet - Very Tight", "2-4 Feet - Tight"}

TIMEOUT = 90   # seconds per API request


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def snooze(label: str = "") -> None:
    t = random.uniform(4.5, 8.2)
    tag = f" [{label}]" if label else ""
    print(f"    [wait]  sleeping {t:.1f}s{tag} ...", flush=True)
    time.sleep(t)


def safe_float(val) -> float | None:
    try:
        f = float(val)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def safe_int(val) -> int | None:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Fetch one distance-range bucket for a season
# ---------------------------------------------------------------------------

def fetch_bucket(season: str, dist_range: str) -> dict:
    """
    Returns {player_id: fga} for a single CloseDefDistRange category.
    Uses Totals so FGA is raw shot counts — no per-game scaling that would
    make the denominator meaningless when games played differs across players.
    """
    print(f"      -> {dist_range} ...", flush=True)
    r = LeagueDashPlayerPtShot(
        season=season,
        close_def_dist_range_nullable=dist_range,
        per_mode_simple="Totals",
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]

    out = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("PLAYER_ID"))
        fga = safe_float(row.get("FGA"))
        if pid is None:
            continue
        # If the same player appears twice (traded), sum their FGA
        out[pid] = out.get(pid, 0.0) + (fga or 0.0)

        # Capture player name for logging (last write wins, names are stable)
        if "_name_map" not in out:
            out["_name_map"] = {}
        out["_name_map"][pid] = str(row.get("PLAYER_NAME", ""))

    return out


# ---------------------------------------------------------------------------
# Build the full season correction map
# ---------------------------------------------------------------------------

def build_season_corrections(season: str) -> dict:
    """
    Fetches all 4 distance buckets for a season and returns:
        {player_id: {
            "name": str,
            "total_fga": float,
            "contested_fga": float,
            "open_fga": float,
            "contested_shot_pct": float | None,
            "open_shot_pct": float | None,
        }}
    """
    bucket_data: dict[str, dict] = {}
    name_map: dict[int, str] = {}

    for i, dist in enumerate(DIST_RANGES):
        raw = fetch_bucket(season, dist)
        name_map_chunk = raw.pop("_name_map", {})
        name_map.update(name_map_chunk)
        bucket_data[dist] = raw

        if i < len(DIST_RANGES) - 1:
            snooze(f"next bucket")

    # Collect all player IDs seen across any bucket
    all_pids = set()
    for bdata in bucket_data.values():
        all_pids.update(bdata.keys())

    corrections = {}
    for pid in all_pids:
        fga_per_bucket = {
            dist: bucket_data[dist].get(pid, 0.0)
            for dist in DIST_RANGES
        }
        total_fga     = sum(fga_per_bucket.values())
        contested_fga = sum(v for d, v in fga_per_bucket.items() if d in CONTESTED_BUCKETS)
        open_fga      = total_fga - contested_fga

        if total_fga > 0:
            contested_pct = round(contested_fga / total_fga, 4)
            open_pct      = round(open_fga      / total_fga, 4)
        else:
            contested_pct = None
            open_pct      = None

        corrections[pid] = {
            "name":                name_map.get(pid, f"PlayerID={pid}"),
            "total_fga":           total_fga,
            "contested_fga":       contested_fga,
            "open_fga":            open_fga,
            "contested_shot_pct":  contested_pct,
            "open_shot_pct":       open_pct,
        }

    return corrections


# ---------------------------------------------------------------------------
# Apply corrections to the database
# ---------------------------------------------------------------------------

def apply_corrections(con: sqlite3.Connection, season: str, corrections: dict) -> int:
    cur = con.cursor()
    updated = 0

    for pid, data in corrections.items():
        cur.execute(
            """
            UPDATE player_stats_advanced
               SET contested_shot_pct = ?,
                   open_shot_pct      = ?
             WHERE season    = ?
               AND player_id = ?
            """,
            (
                data["contested_shot_pct"],
                data["open_shot_pct"],
                season,
                pid,
            ),
        )
        rows_hit = cur.rowcount
        updated += rows_hit

        if rows_hit > 0 and data["total_fga"] > 0:
            tag = "[FIXED]" if data["contested_shot_pct"] is not None else "[NULL ]"
            print(
                f"  {tag} {data['name']:<28} ({season}): "
                f"Contested {data['contested_shot_pct']:.4f}, "
                f"Open {data['open_shot_pct']:.4f}  "
                f"(Derived from {int(data['total_fga'])} total tracking shots)",
                flush=True,
            )

        # Commit per-player to preserve progress on interruption
        con.commit()

    return updated


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    con = sqlite3.connect(DB_PATH)
    grand_total = 0

    for i, season in enumerate(SEASONS, 1):
        print(f"\n{'='*65}", flush=True)
        print(f"  Season {season}  ({i}/{len(SEASONS)})", flush=True)
        print(f"{'='*65}", flush=True)

        try:
            corrections = build_season_corrections(season)
            print(f"\n  Applying {len(corrections)} player corrections ...\n", flush=True)
            n = apply_corrections(con, season, corrections)
            grand_total += n
            print(f"\n  [OK]  {n} DB rows updated for {season}", flush=True)

        except Exception as exc:
            print(f"  [ERR]  Season {season} failed: {exc}", flush=True)
            import traceback
            traceback.print_exc()
            print("  Skipping season — sleeping 10 s before next ...", flush=True)
            time.sleep(10)
            continue

        if i < len(SEASONS):
            print(f"\n  [cooldown between seasons: 10 s]", flush=True)
            time.sleep(10)

    con.close()

    print(f"\n{'='*65}", flush=True)
    print(f"  DONE — {grand_total} DB rows updated across {len(SEASONS)} seasons.", flush=True)
    print(f"  Database: {DB_PATH}", flush=True)

    # Quick sanity check
    con2 = sqlite3.connect(DB_PATH)
    cur2 = con2.cursor()
    cur2.execute("""
        SELECT
            COUNT(*) as total,
            COUNT(contested_shot_pct) as c_non_null,
            SUM(CASE WHEN contested_shot_pct > 1.0 THEN 1 ELSE 0 END) as c_gt1,
            COUNT(open_shot_pct) as o_non_null,
            ROUND(AVG(contested_shot_pct), 4),
            ROUND(AVG(open_shot_pct), 4)
        FROM player_stats_advanced
    """)
    row = cur2.fetchone()
    con2.close()

    print(f"\n  Post-fix sanity check:", flush=True)
    print(f"    contested_shot_pct  non-null={row[1]}  >1.0={row[2]}  avg={row[4]}", flush=True)
    print(f"    open_shot_pct       non-null={row[3]}               avg={row[5]}", flush=True)


if __name__ == "__main__":
    main()
