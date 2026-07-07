"""Fix 6: opp_* defensive tracking gaps for the three NEW seasons.

Targeted re-pull matching fetch_player_advanced.py exactly:
  LeagueDashPtDefend  "Less Than 6Ft" PerGame, keyed by CLOSE_DEF_PERSON_ID:
      opp_fg_pct_at_rim       <- LT_06_PCT
      opp_fg_at_rim_contested <- FGA_LT_06
  LeagueDashPtDefend  "3 Pointers"    PerGame, keyed by CLOSE_DEF_PERSON_ID:
      opp_fg3_contests_attempts <- FG3A
      opp_fg3_pct_contested     <- FG3_PCT
  LeagueDashPlayerStats PerGame/Defense (only if def_reb gaps exist):
      def_reb <- DREB   (keyed by player_id, team_id)

Only NULL cells are filled (same endpoint/params/mapping that produced the
existing values). If an endpoint returns nothing/partial for a season, report
exactly what's missing and move on - no fabrication, no substitute endpoint.

Scope: NEW_SEASONS only.
"""
import math
import random
import sqlite3
import sys
import time

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import LeagueDashPtDefend, LeagueDashPlayerStats

from config import DB_PATH, NEW_SEASONS

TIMEOUT = 90
API_RETRIES = 3
SLEEP_RANGE = (2.0, 3.5)


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


def _retry(fn, label):
    last = None
    for attempt in range(1, API_RETRIES + 1):
        try:
            return fn()
        except Exception as exc:
            last = exc
            print(f"       [retry {attempt}] {label}: {exc}", flush=True)
            snooze("retry")
    print(f"       [FAIL] {label}: {last}", flush=True)
    return None


def fetch_rim_defense(season):
    def _f():
        print(f"    -> Rim Defense (Less Than 6Ft) PerGame ({season})", flush=True)
        return LeagueDashPtDefend(
            season=season, per_mode_simple="PerGame",
            defense_category="Less Than 6Ft", timeout=TIMEOUT,
        ).get_data_frames()[0]
    df = _retry(_f, "rim def")
    out = {}
    if df is None:
        return out, 0
    for _, row in df.iterrows():
        pid = safe_int(row.get("CLOSE_DEF_PERSON_ID"))
        if pid is None:
            continue
        out[pid] = {
            "opp_fg_pct_at_rim": safe_float(row.get("LT_06_PCT")),
            "opp_fg_at_rim_contested": safe_float(row.get("FGA_LT_06")),
        }
    return out, len(df)


def fetch_three_pt_defense(season):
    def _f():
        print(f"    -> 3PT Defense (3 Pointers) PerGame ({season})", flush=True)
        return LeagueDashPtDefend(
            season=season, per_mode_simple="PerGame",
            defense_category="3 Pointers", timeout=TIMEOUT,
        ).get_data_frames()[0]
    df = _retry(_f, "3pt def")
    out = {}
    if df is None:
        return out, 0
    for _, row in df.iterrows():
        pid = safe_int(row.get("CLOSE_DEF_PERSON_ID"))
        if pid is None:
            continue
        out[pid] = {
            "opp_fg3_contests_attempts": safe_float(row.get("FG3A")),
            "opp_fg3_pct_contested": safe_float(row.get("FG3_PCT")),
        }
    return out, len(df)


def fetch_defense_dreb(season):
    def _f():
        print(f"    -> Defense PerGame [DREB] ({season})", flush=True)
        return LeagueDashPlayerStats(
            season=season, per_mode_detailed="PerGame",
            measure_type_detailed_defense="Defense", timeout=TIMEOUT,
        ).get_data_frames()[0]
    df = _retry(_f, "defense dreb")
    out = {}
    if df is None:
        return out
    for _, row in df.iterrows():
        key = (safe_int(row.get("PLAYER_ID")), safe_int(row.get("TEAM_ID")))
        out[key] = safe_float(row.get("DREB"))
    return out


def fill_column(cur, season, col, value_map):
    """Fill only NULL cells for `col`, keyed by player_id. Returns (filled, remaining_null)."""
    cur.execute(f"SELECT DISTINCT player_id FROM player_stats_advanced WHERE season=? AND {col} IS NULL", (season,))
    null_pids = [r[0] for r in cur.fetchall()]
    filled = 0
    for pid in null_pids:
        info = value_map.get(pid)
        if not info:
            continue
        v = info.get(col)
        if v is None:
            continue
        cur.execute(
            f"UPDATE player_stats_advanced SET {col}=? WHERE season=? AND player_id=? AND {col} IS NULL",
            (v, season, pid),
        )
        filled += cur.rowcount
    cur.execute(f"SELECT COUNT(*) FROM player_stats_advanced WHERE season=? AND {col} IS NULL", (season,))
    remaining = cur.fetchone()[0]
    return filled, remaining


def main():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    for season in NEW_SEASONS:
        print(f"\n{'='*56}\n  Season {season}\n{'='*56}", flush=True)
        rim, rim_n = fetch_rim_defense(season)
        snooze("next endpoint")
        three, three_n = fetch_three_pt_defense(season)
        print(f"    [coverage] rim-def rows={rim_n}  3pt-def rows={three_n}", flush=True)

        if rim_n == 0:
            print(f"    [MISSING] Rim-defense endpoint returned NOTHING for {season} "
                  f"-> opp_fg_pct_at_rim / opp_fg_at_rim_contested gaps NOT fillable.", flush=True)
        if three_n == 0:
            print(f"    [MISSING] 3PT-defense endpoint returned NOTHING for {season} "
                  f"-> opp_fg3_* gaps NOT fillable.", flush=True)

        for col in ("opp_fg_pct_at_rim", "opp_fg_at_rim_contested"):
            f, r = fill_column(cur, season, col, rim)
            print(f"    {col:<28} filled={f}  remaining_null={r}", flush=True)
        for col in ("opp_fg3_contests_attempts", "opp_fg3_pct_contested"):
            f, r = fill_column(cur, season, col, three)
            print(f"    {col:<28} filled={f}  remaining_null={r}", flush=True)
        con.commit()

        # def_reb gaps (regular season) - only if any exist
        cur.execute("SELECT COUNT(*) FROM player_stats_advanced WHERE season=? AND def_reb IS NULL", (season,))
        dreb_null = cur.fetchone()[0]
        if dreb_null:
            print(f"    def_reb has {dreb_null} nulls -> pulling Defense PerGame", flush=True)
            snooze("defense dreb")
            dmap = fetch_defense_dreb(season)
            cur.execute("SELECT player_id, team_id FROM player_stats_advanced WHERE season=? AND def_reb IS NULL", (season,))
            filled = 0
            for pid, tid in cur.fetchall():
                v = dmap.get((pid, tid))
                if v is None:
                    continue
                cur.execute(
                    "UPDATE player_stats_advanced SET def_reb=? WHERE season=? AND player_id=? AND team_id=? AND def_reb IS NULL",
                    (v, season, pid, tid),
                )
                filled += cur.rowcount
            con.commit()
            cur.execute("SELECT COUNT(*) FROM player_stats_advanced WHERE season=? AND def_reb IS NULL", (season,))
            print(f"    def_reb filled={filled} remaining_null={cur.fetchone()[0]}", flush=True)
        else:
            print(f"    def_reb: 0 nulls (no work)", flush=True)

        if season != NEW_SEASONS[-1]:
            snooze("season cooldown")

    con.close()


if __name__ == "__main__":
    main()
