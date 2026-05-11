"""
fix_shot_pct_playoffs.py
------------------------
Corrects contested_shot_pct and populates open_shot_pct in
player_stats_advanced_playoffs using NBA tracking data.

Root cause (same bug as the regular-season table)
--------------------------------------------------
The original fetch used the Hustle endpoint and computed:
    contested_shot_pct = CONTESTED_SHOTS / (CONTESTED_SHOTS_2PT + CONTESTED_SHOTS_3PT)
which is literally N/N → values cluster around 1.0.
open_shot_pct was never populated (always NULL).

Correct methodology (this script)
----------------------------------
Pull LeagueDashPlayerPtShot with FGA broken out by CloseDefDistRange
for BOTH SeasonType="Playoffs" and "Play In". Sum raw FGA totals
(they are counts, so additive across season types). Per player:

    total_tracking_fga  = sum of FGA across all 4 buckets (both season types)
    contested_fga       = FGA(Very Tight 0-2ft) + FGA(Tight 2-4ft)
    open_fga            = FGA(Open 4-6ft)        + FGA(Wide Open 6+ft)

    contested_shot_pct  = contested_fga / total_tracking_fga
    open_shot_pct       = open_fga      / total_tracking_fga

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

SEASONS = [
    "2020-21",
    "2021-22",
    "2022-23",
    "2023-24",
    "2024-25",
    "2025-26",
]

SEASON_TYPE_PLAYOFFS = "Playoffs"
SEASON_TYPE_PLAYIN   = "Play In"

DIST_RANGES = [
    "0-2 Feet - Very Tight",
    "2-4 Feet - Tight",
    "4-6 Feet - Open",
    "6+ Feet - Wide Open",
]
CONTESTED_BUCKETS = {"0-2 Feet - Very Tight", "2-4 Feet - Tight"}

TIMEOUT = 90


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def snooze(label: str = "") -> None:
    t = random.uniform(4.5, 8.2)
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


# ---------------------------------------------------------------------------
# Fetch one distance-range bucket for a season + season type
# ---------------------------------------------------------------------------

def fetch_bucket(season: str, dist_range: str, season_type: str) -> dict:
    """Returns {player_id: fga} for a single bucket + season type combo."""
    print(f"      -> [{season_type}] {dist_range} ...", flush=True)

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
            print(f"      [warn]  [{st}] {dist_range}: {exc}", flush=True)

    if df is None or df.empty:
        return {}

    out: dict[int, float] = {}
    name_map: dict[int, str] = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("PLAYER_ID"))
        fga = safe_float(row.get("FGA"))
        if pid is None:
            continue
        out[pid]      = out.get(pid, 0.0) + (fga or 0.0)
        name_map[pid] = str(row.get("PLAYER_NAME", ""))

    out["_name_map"] = name_map  # type: ignore[assignment]
    return out


# ---------------------------------------------------------------------------
# Build the full season correction map
# ---------------------------------------------------------------------------

def build_season_corrections(season: str) -> dict:
    """
    Fetches all 4 distance buckets × 2 season types and returns:
        {player_id: {
            "name": str,
            "total_fga": float,
            "contested_fga": float,
            "open_fga": float,
            "contested_shot_pct": float | None,
            "open_shot_pct": float | None,
        }}
    """
    combined: dict[str, dict[int, float]] = {d: {} for d in DIST_RANGES}
    name_map: dict[int, str] = {}
    request_count = 0

    for season_type in [SEASON_TYPE_PLAYOFFS, SEASON_TYPE_PLAYIN]:
        for i, dist in enumerate(DIST_RANGES):
            raw = fetch_bucket(season, dist, season_type)
            nm  = raw.pop("_name_map", {})  # type: ignore[arg-type]
            name_map.update(nm)

            for pid, fga in raw.items():
                combined[dist][pid] = combined[dist].get(pid, 0.0) + fga

            request_count += 1
            is_last = (season_type == SEASON_TYPE_PLAYIN and i == len(DIST_RANGES) - 1)
            if not is_last:
                snooze("next bucket")

    all_pids: set[int] = set()
    for bdata in combined.values():
        all_pids.update(bdata.keys())

    corrections: dict = {}
    for pid in all_pids:
        fga_per_bucket = {d: combined[d].get(pid, 0.0) for d in DIST_RANGES}
        total_fga      = sum(fga_per_bucket.values())
        contested_fga  = sum(v for d, v in fga_per_bucket.items() if d in CONTESTED_BUCKETS)
        open_fga       = total_fga - contested_fga

        if total_fga > 0:
            contested_pct = round(contested_fga / total_fga, 4)
            open_pct      = round(open_fga      / total_fga, 4)
        else:
            contested_pct = None
            open_pct      = None

        corrections[pid] = {
            "name":               name_map.get(pid, f"PlayerID={pid}"),
            "total_fga":          total_fga,
            "contested_fga":      contested_fga,
            "open_fga":           open_fga,
            "contested_shot_pct": contested_pct,
            "open_shot_pct":      open_pct,
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
            UPDATE player_stats_advanced_playoffs
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
                f"(from {int(data['total_fga'])} tracking shots)",
                flush=True,
            )

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
            print("  Skipping season — sleeping 10s before next ...", flush=True)
            time.sleep(10)
            continue

        if i < len(SEASONS):
            print(f"\n  [cooldown between seasons: 10s]", flush=True)
            time.sleep(10)

    con.close()

    print(f"\n{'='*65}", flush=True)
    print(f"  DONE — {grand_total} DB rows updated across {len(SEASONS)} seasons.", flush=True)
    print(f"  Database: {DB_PATH}", flush=True)

    # Sanity check
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
        FROM player_stats_advanced_playoffs
    """)
    row = cur2.fetchone()
    con2.close()

    print(f"\n  Post-fix sanity check:", flush=True)
    print(f"    contested_shot_pct  non-null={row[1]}  >1.0={row[2]}  avg={row[4]}", flush=True)
    print(f"    open_shot_pct       non-null={row[3]}               avg={row[5]}", flush=True)


if __name__ == "__main__":
    main()
