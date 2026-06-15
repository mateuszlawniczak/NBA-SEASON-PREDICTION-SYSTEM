"""
fix_playoffs_adv_rim3_usg.py
----------------------------
player_stats_advanced_playoffs only:

  1. RENAME opp_fg_pct_at_rim -> opp_fg_pct_at_rim_contested (if needed)
  2. DROP COLUMN opp_fga_at_rim (SQLite 3.35+)
  3. SET opp_fg_pct_at_rim_contested = FGM_LT_06/FGA_LT_06 (Totals), Playoffs+Play-In summed
  4. SET opp_fg3_pct_contested = FG3M/FG3A

Percentages stored as decimals in [0, 1]. Other columns untouched.
Rate limiting: ~3.5–7.0 s jitter between NBA requests.
"""

from __future__ import annotations

import math
import os
import random
import sqlite3
import sys
import time

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import LeagueDashPtDefend

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")
TABLE = "player_stats_advanced_playoffs"
TIMEOUT = 90

SEASON_TYPE_PLAYOFFS = "Playoffs"
SEASON_TYPE_PLAYIN = "Play In"


def snooze(label: str = "") -> None:
    t = random.uniform(3.5, 7.0)
    tag = f" [{label}]" if label else ""
    print(f"  [wait] {t:.1f}s{tag}", flush=True)
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


def clamp_pct(x: float | None) -> float | None:
    if x is None:
        return None
    return max(0.0, min(1.0, x))


def try_season_types_fetch(fn, season: str, season_type: str, label: str):
    alternates = [season_type]
    if season_type == SEASON_TYPE_PLAYIN:
        alternates.append("PlayIn")
    elif season_type == "PlayIn":
        alternates.append(SEASON_TYPE_PLAYIN)

    for st in alternates:
        try:
            return fn(season, st)
        except Exception as exc:
            print(f"    [warn] {label} [{st}]: {exc}", flush=True)
    print(f"    [skip] {label} — no data.", flush=True)
    return {}


def _accum(bucket: dict[int, dict], pid: int, fgm_key: str, fga_key: str, row) -> None:
    fgm = safe_float(row.get(fgm_key))
    fga = safe_float(row.get(fga_key))
    if pid not in bucket:
        bucket[pid] = {"fgm": 0.0, "fga": 0.0}
    b = bucket[pid]
    if fgm is not None:
        b["fgm"] += fgm
    if fga is not None:
        b["fga"] += fga


def fetch_rim_buckets(season: str, season_type: str) -> dict[int, dict]:
    r = LeagueDashPtDefend(
        season=season,
        per_mode_simple="Totals",
        defense_category="Less Than 6Ft",
        season_type_all_star=season_type,
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    if df is None or df.empty:
        return {}
    out: dict[int, dict] = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("CLOSE_DEF_PERSON_ID"))
        if pid is None:
            continue
        _accum(out, pid, "FGM_LT_06", "FGA_LT_06", row)
    return out


def fetch_fg3_buckets(season: str, season_type: str) -> dict[int, dict]:
    r = LeagueDashPtDefend(
        season=season,
        per_mode_simple="Totals",
        defense_category="3 Pointers",
        season_type_all_star=season_type,
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    if df is None or df.empty:
        return {}
    out: dict[int, dict] = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("CLOSE_DEF_PERSON_ID"))
        if pid is None:
            continue
        _accum(out, pid, "FG3M", "FG3A", row)
    return out


def pct_merge(po: dict[int, dict], pi: dict[int, dict], pid: int) -> float | None:
    b_po = po.get(pid)
    b_pi = pi.get(pid)
    fgm = (b_po["fgm"] if b_po else 0.0) + (b_pi["fgm"] if b_pi else 0.0)
    fga = (b_po["fga"] if b_po else 0.0) + (b_pi["fga"] if b_pi else 0.0)
    if fga > 0:
        return clamp_pct(fgm / fga)
    return None


def migrate_schema(con: sqlite3.Connection) -> None:
    cur = con.cursor()
    cols = {r[1] for r in cur.execute(f"PRAGMA table_info({TABLE})")}

    if "opp_fg_pct_at_rim_contested" not in cols and "opp_fg_pct_at_rim" in cols:
        cur.execute(
            f"ALTER TABLE {TABLE} RENAME COLUMN opp_fg_pct_at_rim TO opp_fg_pct_at_rim_contested"
        )
        con.commit()
        print("[schema] Renamed opp_fg_pct_at_rim -> opp_fg_pct_at_rim_contested.", flush=True)
        cols = {r[1] for r in cur.execute(f"PRAGMA table_info({TABLE})")}

    if "opp_fg_at_rim_contested" not in cols:
        cur.execute(f"ALTER TABLE {TABLE} ADD COLUMN opp_fg_at_rim_contested REAL")
        con.commit()
        print("[schema] Added opp_fg_at_rim_contested.", flush=True)
        cols = {r[1] for r in cur.execute(f"PRAGMA table_info({TABLE})")}

    if "opp_fga_at_rim" in cols:
        cur.execute(
            f"UPDATE {TABLE} SET opp_fg_at_rim_contested = opp_fga_at_rim "
            f"WHERE opp_fga_at_rim IS NOT NULL AND opp_fg_at_rim_contested IS NULL"
        )
        con.commit()
        cur.execute(f"ALTER TABLE {TABLE} DROP COLUMN opp_fga_at_rim")
        con.commit()
        print("[schema] Dropped opp_fga_at_rim (after backfill guard).", flush=True)


def run() -> None:
    print(f"[fix_playoffs_adv_rim3_usg] DB: {DB_PATH}", flush=True)
    con = sqlite3.connect(DB_PATH)
    migrate_schema(con)
    cur = con.cursor()

    seasons = [
        r[0] for r in cur.execute(f"SELECT DISTINCT season FROM {TABLE} ORDER BY season")
    ]
    if not seasons:
        print("[done] Empty table.", flush=True)
        con.close()
        return

    total = 0
    for season in seasons:
        print(f"\n{'=' * 50}\n[{season}]", flush=True)

        rim_po = try_season_types_fetch(fetch_rim_buckets, season, SEASON_TYPE_PLAYOFFS, "rim PO")
        snooze("rim PI")
        rim_pi = try_season_types_fetch(fetch_rim_buckets, season, SEASON_TYPE_PLAYIN, "rim PI")
        snooze("3pt PO")
        t3_po = try_season_types_fetch(fetch_fg3_buckets, season, SEASON_TYPE_PLAYOFFS, "3pt PO")
        snooze("3pt PI")
        t3_pi = try_season_types_fetch(fetch_fg3_buckets, season, SEASON_TYPE_PLAYIN, "3pt PI")

        pids = {
            r[0]
            for r in cur.execute(
                f"SELECT DISTINCT player_id FROM {TABLE} WHERE season = ?", (season,)
            )
        }

        n = 0
        for pid in pids:
            rp = pct_merge(rim_po, rim_pi, pid)
            tp = pct_merge(t3_po, t3_pi, pid)
            cur.execute(
                f"""
                UPDATE {TABLE}
                SET opp_fg_pct_at_rim_contested = ?,
                    opp_fg3_pct_contested = ?
                WHERE season = ? AND player_id = ?
                """,
                (
                    round(rp, 4) if rp is not None else None,
                    round(tp, 4) if tp is not None else None,
                    season,
                    pid,
                ),
            )
            n += cur.rowcount

        con.commit()
        total += n
        print(f"  [OK] {n} rows updated.", flush=True)

        if season != seasons[-1]:
            snooze(f"after {season}")

    con.close()
    print(f"\n[DONE] Updates: {total}", flush=True)


if __name__ == "__main__":
    run()
