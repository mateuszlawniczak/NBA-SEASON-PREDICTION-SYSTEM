"""
fix_offensive_tracking.py
-------------------------
Updates `player_stats_advanced` **in place** (UPDATE only — never INSERT OR REPLACE):

  • off_reb              — LeagueDashPlayerStats PerGame Base, OREB
  • contested_shot_pct   — Share of **tracking FGA** with closest defender 0–4 ft
                           (Very Tight + Tight), / sum of all four distance buckets.
  • open_shot_pct        — Share with defender 4+ ft (Open + Wide Open). **contested + open = 1**
                           (open is computed as 1 − contested after rounding so the pair sums exactly).

Those two columns are **not** field-goal percentage; they are the split of *attempt volume* by
contest level (each is between 0 and 1 and they add to 1).

Rows are matched by (season, player_id, team_id). Shot shares are player-season totals from
tracking (same value on each team row for that player); OREB matches TEAM_ID on the Base dash.

**Speed (default):** OREB + four PtShot buckets in parallel; short retry sleeps; 0.5 s between seasons.
Add `--verbose` for per-row logs. Use `--polite` for 4.5–8.2 s pacing between calls.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import LeagueDashPlayerStats, LeagueDashPlayerPtShot

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

TIMEOUT = 90
API_RETRIES = 3

POLITE_SNOOZE = (4.5, 8.2)
FAST_SNOOZE = (0.15, 0.45)  # retry / polite-gap pacing in fast mode
PARALLEL_WORKERS_DEFAULT = 8  # regular season: 5 API calls; extra headroom harmless
SEASON_GAP_FAST = 0.5
SEASON_GAP_POLITE = 6.0

CONTESTED_RANGES = (
    "0-2 Feet - Very Tight",
    "2-4 Feet - Tight",
)
OPEN_RANGES = (
    "4-6 Feet - Open",
    "6+ Feet - Wide Open",
)
ALL_DIST_RANGES: tuple[str, ...] = CONTESTED_RANGES + OPEN_RANGES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def api_snooze(polite: bool, label: str = "") -> None:
    lo, hi = POLITE_SNOOZE if polite else FAST_SNOOZE
    t = random.uniform(lo, hi)
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


def pct_label(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{x * 100:.1f}%"


def oreb_label(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{x:.1f}"


# ---------------------------------------------------------------------------
# NBA API (one-shot fetches + retries)
# ---------------------------------------------------------------------------


def _fetch_oreb_map_once(season: str) -> dict[tuple[int, int], float]:
    r = LeagueDashPlayerStats(
        season=season,
        per_mode_detailed="PerGame",
        measure_type_detailed_defense="Base",
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    out: dict[tuple[int, int], float] = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("PLAYER_ID"))
        tid = safe_int(row.get("TEAM_ID"))
        if pid is None or tid is None:
            continue
        o = safe_float(row.get("OREB"))
        if o is not None:
            out[(pid, tid)] = round(o, 3)
    return out


def fetch_oreb_map(season: str, polite: bool) -> dict[tuple[int, int], float]:
    last_exc: Exception | None = None
    for attempt in range(1, API_RETRIES + 1):
        try:
            print(f"    -> LeagueDashPlayerStats Base PerGame [OREB] ({season}) ...", flush=True)
            return _fetch_oreb_map_once(season)
        except Exception as exc:
            last_exc = exc
            print(f"       [retry {attempt}/{API_RETRIES}] {exc}", flush=True)
            api_snooze(polite, "retry OREB")
    print(f"       [FAIL] OREB fetch for {season}: {last_exc}", flush=True)
    return {}


def _fetch_ptshot_bucket_once(season: str, dist_range: str) -> dict[int, tuple[float, float]]:
    r = LeagueDashPlayerPtShot(
        season=season,
        close_def_dist_range_nullable=dist_range,
        per_mode_simple="Totals",
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    out: dict[int, tuple[float, float]] = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("PLAYER_ID"))
        if pid is None:
            continue
        fgm = safe_float(row.get("FGM")) or 0.0
        fga = safe_float(row.get("FGA")) or 0.0
        if pid in out:
            p_fgm, p_fga = out[pid]
            out[pid] = (p_fgm + fgm, p_fga + fga)
        else:
            out[pid] = (fgm, fga)
    return out


def fetch_ptshot_bucket(season: str, dist_range: str, polite: bool) -> dict[int, tuple[float, float]]:
    last_exc: Exception | None = None
    for attempt in range(1, API_RETRIES + 1):
        try:
            print(f"    -> LeagueDashPlayerPtShot Totals [{dist_range[:24]}…] ...", flush=True)
            return _fetch_ptshot_bucket_once(season, dist_range)
        except Exception as exc:
            last_exc = exc
            print(f"       [retry {attempt}/{API_RETRIES}] {exc}", flush=True)
            api_snooze(polite, "retry PtShot")
    print(f"       [FAIL] PtShot {dist_range!r} ({season}): {last_exc}", flush=True)
    return {}


def merge_bucket_lists_to_fga_shares(
    contested_maps: list[dict[int, tuple[float, float]]],
    open_maps: list[dict[int, tuple[float, float]]],
) -> dict[int, tuple[float | None, float | None]]:
    """
    Per player: contested_shot_pct = FGA (0–4 ft) / total FGA in the 4 buckets;
    open_shot_pct = 1 − contested after rounding so the two stored values sum to 1.0.
    """
    all_pids: set[int] = set()
    for m in contested_maps + open_maps:
        all_pids.update(m.keys())

    result: dict[int, tuple[float | None, float | None]] = {}
    for pid in all_pids:
        c_fga = 0.0
        for m in contested_maps:
            _fgm, fga = m.get(pid, (0.0, 0.0))
            c_fga += fga
        o_fga = 0.0
        for m in open_maps:
            _fgm, fga = m.get(pid, (0.0, 0.0))
            o_fga += fga

        total_fga = c_fga + o_fga
        if total_fga <= 0:
            result[pid] = (None, None)
            continue
        contested_share = round(c_fga / total_fga, 4)
        open_share = round(1.0 - contested_share, 4)
        result[pid] = (contested_share, open_share)

    return result


def build_fg_pct_polite(season: str, polite: bool) -> dict[int, tuple[float | None, float | None]]:
    contested_maps: list[dict[int, tuple[float, float]]] = []
    for i, dr in enumerate(CONTESTED_RANGES):
        if i > 0:
            api_snooze(polite, "next contested bucket")
        contested_maps.append(fetch_ptshot_bucket(season, dr, polite))

    open_maps: list[dict[int, tuple[float, float]]] = []
    for i, dr in enumerate(OPEN_RANGES):
        if i > 0:
            api_snooze(polite, "next open bucket")
        open_maps.append(fetch_ptshot_bucket(season, dr, polite))

    return merge_bucket_lists_to_fga_shares(contested_maps, open_maps)


def fetch_season_maps_parallel(
    season: str,
    workers: int,
    polite: bool,
) -> tuple[dict[tuple[int, int], float], dict[int, tuple[float | None, float | None]]]:
    """
    Run OREB + 4 PtShot buckets concurrently (when not polite).
    """
    workers = max(1, min(workers, 12))

    if polite or workers == 1:
        oreb_map = fetch_oreb_map(season, polite)
        api_snooze(polite, "before shooting tracking")
        fg_map = build_fg_pct_polite(season, polite)
        return oreb_map, fg_map

    print(
        f"    [parallel]  up to {workers} workers — OREB + {len(ALL_DIST_RANGES)} PtShot buckets ...",
        flush=True,
    )
    bucket_results: dict[str, dict[int, tuple[float, float]]] = {}

    def run_oreb() -> dict[tuple[int, int], float]:
        return fetch_oreb_map(season, polite=False)

    def run_bucket(dr: str) -> tuple[str, dict[int, tuple[float, float]]]:
        return dr, fetch_ptshot_bucket(season, dr, polite=False)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        f_oreb = ex.submit(run_oreb)
        f_bucks = {ex.submit(run_bucket, dr): dr for dr in ALL_DIST_RANGES}
        oreb_map = f_oreb.result()
        for fut in as_completed(f_bucks):
            dr, data = fut.result()
            bucket_results[dr] = data
            print(f"       [done] {dr[:28]}…", flush=True)

    contested_maps = [bucket_results[dr] for dr in CONTESTED_RANGES]
    open_maps = [bucket_results[dr] for dr in OPEN_RANGES]
    fg_map = merge_bucket_lists_to_fga_shares(contested_maps, open_maps)
    return oreb_map, fg_map


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------


def distinct_seasons(con: sqlite3.Connection) -> list[str]:
    cur = con.cursor()
    cur.execute(
        "SELECT DISTINCT season FROM player_stats_advanced ORDER BY season"
    )
    return [r[0] for r in cur.fetchall()]


def season_rows(con: sqlite3.Connection, season: str) -> list[tuple]:
    cur = con.cursor()
    cur.execute(
        """
        SELECT season, player_id, player_name, team_id, team_abbr
          FROM player_stats_advanced
         WHERE season = ?
         ORDER BY player_id, team_id
        """,
        (season,),
    )
    return cur.fetchall()


def apply_season(
    con: sqlite3.Connection,
    season: str,
    oreb_map: dict[tuple[int, int], float],
    fg_map: dict[int, tuple[float | None, float | None]],
    *,
    verbose: bool = False,
) -> int:
    rows = season_rows(con, season)
    cur = con.cursor()
    updated = 0

    for _s, pid, pname, tid, tabbr in rows:
        set_parts: list[str] = []
        args: list = []

        if pid in fg_map:
            c_pct, o_pct = fg_map[pid]
            set_parts.extend(["contested_shot_pct = ?", "open_shot_pct = ?"])
            args.extend([c_pct, o_pct])
        oreb = oreb_map.get((pid, tid))
        if oreb is not None:
            set_parts.append("off_reb = ?")
            args.append(oreb)

        if not set_parts:
            continue

        args.extend([season, pid, tid])
        sql = (
            f"UPDATE player_stats_advanced SET {', '.join(set_parts)} "
            "WHERE season = ? AND player_id = ? AND team_id = ?"
        )
        cur.execute(sql, tuple(args))
        if cur.rowcount > 0:
            updated += cur.rowcount
            if verbose:
                tag = (tabbr or "?").strip() or "?"
                c_pct, o_pct = fg_map.get(pid, (None, None))
                print(
                    f"  [FIX] Updated {pname} {tag} ({season}) - "
                    f"Contested: {pct_label(c_pct)}, Open: {pct_label(o_pct)}, "
                    f"OReb: {oreb_label(oreb)}",
                    flush=True,
                )

    con.commit()
    return updated


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fix off_reb and contested/open FGA **share** columns on player_stats_advanced.",
    )
    parser.add_argument(
        "--polite",
        action="store_true",
        help="Sequential API calls with 4.5–8.2 s between each (slower, fewer 429s).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=PARALLEL_WORKERS_DEFAULT,
        metavar="N",
        help=f"Max parallel NBA HTTP calls per season (default {PARALLEL_WORKERS_DEFAULT}; "
        "ignored with --polite).",
    )
    parser.add_argument(
        "--season-gap",
        type=float,
        default=None,
        metavar="SEC",
        help="Seconds between seasons (default: 0.5 fast / 6.0 polite if omitted).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Log every updated row (default: summaries only — less console I/O).",
    )
    args = parser.parse_args()

    polite = args.polite
    season_gap = (
        args.season_gap
        if args.season_gap is not None
        else (SEASON_GAP_POLITE if polite else SEASON_GAP_FAST)
    )

    con = sqlite3.connect(DB_PATH)
    seasons = distinct_seasons(con)
    if not seasons:
        print("[FIX] No seasons in player_stats_advanced.", flush=True)
        con.close()
        return

    mode = "polite / sequential" if polite else f"parallel ({args.workers} workers)"
    print(f"[FIX] Seasons from DB: {', '.join(seasons)}", flush=True)
    print(f"[FIX] Mode: {mode}; season gap: {season_gap:.1f}s; row log: {'verbose' if args.verbose else 'quiet'}", flush=True)

    grand = 0
    for i, season in enumerate(seasons, 1):
        print(f"\n{'='*60}", flush=True)
        print(f"  Season {season}  ({i}/{len(seasons)})", flush=True)
        print(f"{'='*60}", flush=True)

        try:
            oreb_map, fg_map = fetch_season_maps_parallel(
                season,
                workers=args.workers,
                polite=polite,
            )

            if not fg_map and not oreb_map:
                print(f"  [WARN] No API data for {season}; skipping updates.", flush=True)
                continue

            n = apply_season(con, season, oreb_map, fg_map, verbose=args.verbose)
            grand += n
            print(f"  [OK] {n} row(s) touched for {season}", flush=True)

            if i < len(seasons) and season_gap > 0:
                print(f"  [cooldown between seasons: {season_gap:.1f} s]", flush=True)
                time.sleep(season_gap)

        except Exception as exc:
            print(f"  [ERR] {season}: {exc}", flush=True)
            import traceback
            traceback.print_exc()
            con.rollback()
            api_snooze(polite, "after error")

    con.close()
    print(f"\n[DONE] Total rows updated: {grand}  |  DB: {DB_PATH}", flush=True)


if __name__ == "__main__":
    main()
