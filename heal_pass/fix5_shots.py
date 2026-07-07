"""Fix 5: contested_shot_pct + open_shot_pct for the three NEW seasons.

Detected methodology (see detect_def.py): the existing seasons hold the
patch_open_shots recipe (contested + open do NOT sum to 1; avg sum ~0.72).

Therefore, matching the existing pipeline:
  contested_shot_pct = (Very Tight + Tight FGA) / (all-4-bucket tracking FGA)
                       (fix_shot_pct / fix_offensive_tracking contested share)
  open_shot_pct      = (6+ ft Wide-Open FGA) / (total season FGA)
                       (patch_open_shots strict definition; denominator from
                        LeagueDashPlayerStats Totals/Base, the fallback used for
                        the existing seasons because player_stats_basic has no fga)

  contested NULL if 4-bucket tracking FGA == 0.
  open      NULL if total season FGA == 0.

Endpoints per season:
  LeagueDashPlayerPtShot Totals x 4 CloseDefDistRange buckets
  LeagueDashPlayerStats  Totals / Base  (season FGA denominator)

Modes:
  --verify SEASON   pull data for an EXISTING season, compare recomputed
                    contested/open against stored values (consistency proof).
                    Writes nothing.
  (default)         apply to NEW_SEASONS only.
"""
import math
import os
import random
import sqlite3
import sys
import time

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import LeagueDashPlayerPtShot, LeagueDashPlayerStats

from config import DB_PATH, NEW_SEASONS

TIMEOUT = 90
API_RETRIES = 3
SLEEP_RANGE = (2.0, 3.5)

DIST_RANGES = ["0-2 Feet - Very Tight", "2-4 Feet - Tight", "4-6 Feet - Open", "6+ Feet - Wide Open"]
CONTESTED_BUCKETS = {"0-2 Feet - Very Tight", "2-4 Feet - Tight"}
WIDE_OPEN = "6+ Feet - Wide Open"


def snooze(label=""):
    t = random.uniform(*SLEEP_RANGE)
    print(f"    [wait] {t:.1f}s {('['+label+']') if label else ''}", flush=True)
    time.sleep(t)


def safe_float(val):
    try:
        f = float(val)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def safe_int(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def fetch_bucket(season, dist_range):
    last = None
    for attempt in range(1, API_RETRIES + 1):
        try:
            print(f"    -> PtShot Totals [{dist_range}] ({season})", flush=True)
            df = LeagueDashPlayerPtShot(
                season=season, close_def_dist_range_nullable=dist_range,
                per_mode_simple="Totals", timeout=TIMEOUT,
            ).get_data_frames()[0]
            out = {}
            for _, row in df.iterrows():
                pid = safe_int(row.get("PLAYER_ID"))
                if pid is None:
                    continue
                out[pid] = out.get(pid, 0.0) + (safe_float(row.get("FGA")) or 0.0)
            return out
        except Exception as exc:
            last = exc
            print(f"       [retry {attempt}] {exc}", flush=True)
            snooze("retry")
    print(f"       [FAIL] bucket {dist_range} {season}: {last}", flush=True)
    return {}


def fetch_total_fga(season):
    last = None
    for attempt in range(1, API_RETRIES + 1):
        try:
            print(f"    -> LeagueDashPlayerStats Totals/Base [season FGA] ({season})", flush=True)
            df = LeagueDashPlayerStats(
                season=season, per_mode_detailed="Totals",
                measure_type_detailed_defense="Base", timeout=TIMEOUT,
            ).get_data_frames()[0]
            out = {}
            for _, row in df.iterrows():
                pid = safe_int(row.get("PLAYER_ID"))
                if pid is None:
                    continue
                out[pid] = out.get(pid, 0.0) + (safe_float(row.get("FGA")) or 0.0)
            return out
        except Exception as exc:
            last = exc
            print(f"       [retry {attempt}] {exc}", flush=True)
            snooze("retry")
    print(f"       [FAIL] total FGA {season}: {last}", flush=True)
    return {}


def compute_season(season):
    """Returns {player_id: (contested_pct|None, open_pct|None)}."""
    buckets = {}
    for i, dr in enumerate(DIST_RANGES):
        if i:
            snooze("next bucket")
        buckets[dr] = fetch_bucket(season, dr)
    snooze("before totals")
    total_fga = fetch_total_fga(season)

    all_pids = set(total_fga)
    for b in buckets.values():
        all_pids.update(b.keys())

    result = {}
    for pid in all_pids:
        vt = buckets[DIST_RANGES[0]].get(pid, 0.0)
        t = buckets[DIST_RANGES[1]].get(pid, 0.0)
        op = buckets[DIST_RANGES[2]].get(pid, 0.0)
        wo = buckets[DIST_RANGES[3]].get(pid, 0.0)
        track_total = vt + t + op + wo
        contested = round((vt + t) / track_total, 4) if track_total > 0 else None
        denom = total_fga.get(pid, 0.0)
        open_pct = round(wo / denom, 4) if denom and denom > 0 else None
        result[pid] = (contested, open_pct)
    return result


def do_verify(season):
    print(f"[verify] recomputing {season} and comparing to stored values (no writes)")
    comp = compute_season(season)
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "SELECT player_id, contested_shot_pct, open_shot_pct FROM player_stats_advanced WHERE season=?",
        (season,),
    )
    stored = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    con.close()

    c_ok = c_bad = o_ok = o_bad = both = 0
    c_diffs, o_diffs = [], []
    for pid, (sc, so) in stored.items():
        cc, co = comp.get(pid, (None, None))
        both += 1
        if sc is not None and cc is not None:
            if abs(sc - cc) <= 0.01:
                c_ok += 1
            else:
                c_bad += 1
                c_diffs.append((pid, sc, cc))
        if so is not None and co is not None:
            if abs(so - co) <= 0.01:
                o_ok += 1
            else:
                o_bad += 1
                o_diffs.append((pid, so, co))
    print(f"  contested: match={c_ok} mismatch={c_bad}")
    print(f"  open     : match={o_ok} mismatch={o_bad}")
    for lbl, diffs in (("contested", c_diffs), ("open", o_diffs)):
        for pid, s, c in diffs[:8]:
            print(f"    [{lbl} diff] pid={pid} stored={s} recomputed={c}")


def do_apply():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    for season in NEW_SEASONS:
        print(f"\n{'='*56}\n  Season {season}\n{'='*56}", flush=True)
        comp = compute_season(season)
        cur.execute("SELECT DISTINCT player_id FROM player_stats_advanced WHERE season=?", (season,))
        pids = [r[0] for r in cur.fetchall()]
        c_set = o_set = c_null = o_null = 0
        for pid in pids:
            contested, open_pct = comp.get(pid, (None, None))
            cur.execute(
                "UPDATE player_stats_advanced SET contested_shot_pct=?, open_shot_pct=? WHERE season=? AND player_id=?",
                (contested, open_pct, season, pid),
            )
            if contested is None:
                c_null += 1
            else:
                c_set += 1
            if open_pct is None:
                o_null += 1
            else:
                o_set += 1
        con.commit()
        print(f"  [{season}] contested set={c_set} null={c_null} | open set={o_set} null={o_null}")
    con.close()


def main():
    if "--verify" in sys.argv:
        i = sys.argv.index("--verify")
        season = sys.argv[i + 1]
        do_verify(season)
    else:
        do_apply()


if __name__ == "__main__":
    main()
