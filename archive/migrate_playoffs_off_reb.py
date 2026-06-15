"""
migrate_playoffs_off_reb.py
---------------------------
Schema migration for player_stats_advanced_playoffs:

  1. DROP blk_pct column
  2. DROP stl_pct column
  3. ADD  off_reb column  (offensive rebounds per game)
  4. Backfill off_reb via LeagueDashPlayerStats PerGame Base for each season,
     fetching both SeasonType="Playoffs" and "Play In", then GP-weighting
     players who appeared in both.

Anti-bot: random 4.5–8.5 s sleep between every API request.
"""

import os
import sys
import math
import time
import random
import sqlite3

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import LeagueDashPlayerStats

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

TIMEOUT = 90


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def snooze(label: str = "") -> None:
    t = random.uniform(4.5, 8.5)
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
# Schema migration
# ---------------------------------------------------------------------------

def migrate_schema(con: sqlite3.Connection) -> None:
    cur = con.cursor()

    existing_cols = {row[1] for row in cur.execute(
        "PRAGMA table_info(player_stats_advanced_playoffs)"
    )}

    if "blk_pct" in existing_cols:
        cur.execute("ALTER TABLE player_stats_advanced_playoffs DROP COLUMN blk_pct")
        print("  [schema] DROP COLUMN blk_pct — done.", flush=True)
    else:
        print("  [schema] blk_pct already absent — skipped.", flush=True)

    if "stl_pct" in existing_cols:
        cur.execute("ALTER TABLE player_stats_advanced_playoffs DROP COLUMN stl_pct")
        print("  [schema] DROP COLUMN stl_pct — done.", flush=True)
    else:
        print("  [schema] stl_pct already absent — skipped.", flush=True)

    if "off_reb" not in existing_cols:
        cur.execute("ALTER TABLE player_stats_advanced_playoffs ADD COLUMN off_reb REAL")
        print("  [schema] ADD COLUMN off_reb — done.", flush=True)
    else:
        print("  [schema] off_reb already present — skipped.", flush=True)

    con.commit()


# ---------------------------------------------------------------------------
# Fetch OREB per game for one season type
# ---------------------------------------------------------------------------

def fetch_oreb(season: str, season_type: str) -> dict:
    """
    Returns {player_id: {"oreb": float, "gp": int}} for one season type.
    For traded players (multiple rows): GP-weighted merge within the same type.
    """
    print(f"    -> OREB PerGame Base [{season_type}] ...", flush=True)

    alternates = [season_type]
    if season_type == SEASON_TYPE_PLAYIN:
        alternates.append("PlayIn")

    df = None
    for st in alternates:
        try:
            r = LeagueDashPlayerStats(
                season=season,
                per_mode_detailed="PerGame",
                measure_type_detailed_defense="Base",
                season_type_all_star=st,
                timeout=TIMEOUT,
            )
            df = r.get_data_frames()[0]
            if df is not None and not df.empty:
                break
        except Exception as exc:
            print(f"      [warn] [{st}]: {exc}", flush=True)

    if df is None or df.empty:
        print(f"      [skip] No data for [{season_type}].", flush=True)
        return {}

    out: dict = {}
    for _, row in df.iterrows():
        pid  = safe_int(row.get("PLAYER_ID"))
        if pid is None:
            continue
        gp   = safe_int(row.get("GP")) or 0
        oreb = safe_float(row.get("OREB"))

        if pid in out:
            prev      = out[pid]
            total_gp  = prev["gp"] + gp
            a, b      = prev.get("oreb"), oreb
            if a is not None and b is not None and total_gp > 0:
                prev["oreb"] = (a * prev["gp"] + b * gp) / total_gp
            elif b is not None:
                prev["oreb"] = b
            prev["gp"] = total_gp
        else:
            out[pid] = {"oreb": oreb, "gp": gp}

    print(f"      {len(out)} players", flush=True)
    return out


# ---------------------------------------------------------------------------
# Build GP-weighted off_reb map for a season (Playoffs + Play-In)
# ---------------------------------------------------------------------------

def build_oreb_map(season: str) -> dict:
    """
    Returns {player_id: off_reb_per_game} merging Playoffs and Play-In
    via GP-weighted average.
    """
    po = fetch_oreb(season, SEASON_TYPE_PLAYOFFS)
    snooze("Playoffs -> Play-In")
    pi = fetch_oreb(season, SEASON_TYPE_PLAYIN)

    merged: dict = {}
    all_pids = set(po) | set(pi)

    for pid in all_pids:
        in_po = pid in po
        in_pi = pid in pi

        if in_po and in_pi:
            gp_po = po[pid].get("gp") or 0
            gp_pi = pi[pid].get("gp") or 0
            total = gp_po + gp_pi
            a = po[pid].get("oreb")
            b = pi[pid].get("oreb")
            if a is not None and b is not None and total > 0:
                merged[pid] = round((a * gp_po + b * gp_pi) / total, 1)
            elif a is not None:
                merged[pid] = round(a, 1) if a is not None else None
            else:
                merged[pid] = round(b, 1) if b is not None else None
        elif in_po:
            v = po[pid].get("oreb")
            merged[pid] = round(v, 1) if v is not None else None
        else:
            v = pi[pid].get("oreb")
            merged[pid] = round(v, 1) if v is not None else None

    return merged


# ---------------------------------------------------------------------------
# Apply off_reb to the database
# ---------------------------------------------------------------------------

def apply_oreb(con: sqlite3.Connection, season: str, oreb_map: dict) -> int:
    cur = con.cursor()
    updated = 0

    for pid, off_reb in oreb_map.items():
        cur.execute(
            """
            UPDATE player_stats_advanced_playoffs
               SET off_reb = ?
             WHERE season    = ?
               AND player_id = ?
            """,
            (off_reb, season, pid),
        )
        updated += cur.rowcount

    con.commit()
    return updated


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    con = sqlite3.connect(DB_PATH)

    print("\n[migrate_playoffs_off_reb] Schema changes ...", flush=True)
    migrate_schema(con)

    grand_total = 0

    for i, season in enumerate(SEASONS, 1):
        print(f"\n{'='*60}", flush=True)
        print(f"  Season {season}  ({i}/{len(SEASONS)})", flush=True)
        print(f"{'='*60}", flush=True)

        try:
            oreb_map = build_oreb_map(season)
            n = apply_oreb(con, season, oreb_map)
            grand_total += n
            print(f"  [OK]  {n} rows updated with off_reb for {season}", flush=True)

        except Exception as exc:
            print(f"  [ERR]  Season {season} failed: {exc}", flush=True)
            import traceback
            traceback.print_exc()
            time.sleep(10)
            continue

        if i < len(SEASONS):
            print(f"  [cooldown: 10s]", flush=True)
            time.sleep(10)

    con.close()

    print(f"\n{'='*60}", flush=True)
    print(f"  DONE — {grand_total} rows backfilled across {len(SEASONS)} seasons.", flush=True)

    # Sanity check
    con2 = sqlite3.connect(DB_PATH)
    cur2 = con2.cursor()
    cur2.execute("""
        SELECT
            COUNT(*) as total,
            COUNT(off_reb) as non_null,
            ROUND(AVG(off_reb), 2) as avg_oreb,
            ROUND(MAX(off_reb), 2) as max_oreb
        FROM player_stats_advanced_playoffs
    """)
    row = cur2.fetchone()

    cols = {r[1] for r in cur2.execute("PRAGMA table_info(player_stats_advanced_playoffs)")}
    con2.close()

    print(f"\n  Post-migration sanity check:", flush=True)
    print(f"    Total rows : {row[0]}", flush=True)
    print(f"    off_reb    non-null={row[1]}  avg={row[2]}  max={row[3]}", flush=True)
    print(f"    blk_pct present: {'blk_pct' in cols}  |  stl_pct present: {'stl_pct' in cols}", flush=True)


if __name__ == "__main__":
    main()
