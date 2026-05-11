"""
fix_offensive_tracking_playoffs.py
-----------------------------------
Updates `player_stats_advanced_playoffs` **in place** (UPDATE only):

  • off_reb              — LeagueDashPlayerStats PerGame Base OREB, postseason only.
                           Playoffs and Play-In are **GP-weight merged** per player_id
                           (same idea as fetch_playoff_advanced).
  • contested_shot_pct   — Share of **tracking FGA** (0–4 ft vs 4+ ft defender distance),
                           summed across Playoffs + Play-In Totals in each bucket.
  • open_shot_pct        — Complement so contested + open = 1.0 after rounding.

Rows: (season, player_id, team_id). Shot shares and merged OREB are **per player_id** for that
postseason; the same values are written to the row matching your DB (one row per player-season
typical in this project).

Seasons: every distinct `season` present in `player_stats_advanced_playoffs`
(typically 2020-21 … 2025-26).

Defaults: fast parallel mode (10 concurrent NBA calls / season), shared short retry delays
from fix_offensive_tracking.py; `--polite` for slow pacing; `--verbose` for per-row DB logs.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import LeagueDashPlayerStats, LeagueDashPlayerPtShot

from fix_offensive_tracking import (
    ALL_DIST_RANGES,
    API_RETRIES,
    CONTESTED_RANGES,
    DB_PATH,
    OPEN_RANGES,
    SEASON_GAP_FAST,
    SEASON_GAP_POLITE,
    TIMEOUT,
    api_snooze,
    merge_bucket_lists_to_fga_shares,
    oreb_label,
    pct_label,
    safe_float,
    safe_int,
)

# Postseason needs 2 OREB + 8 PtShot calls per season; default higher than regular (5 calls).
PLAYOFF_PARALLEL_DEFAULT = 10

SEASON_TYPE_PLAYOFFS = "Playoffs"
SEASON_TYPE_PLAYIN = "Play In"

PLAYOFFS_TABLE = "player_stats_advanced_playoffs"


# ---------------------------------------------------------------------------
# NBA: Base PerGame OREB (one season type), trades merged within type
# ---------------------------------------------------------------------------


def _fetch_base_pg_oreb_st(season: str, season_type: str) -> dict[int, dict]:
    r = LeagueDashPlayerStats(
        season=season,
        per_mode_detailed="PerGame",
        measure_type_detailed_defense="Base",
        season_type_all_star=season_type,
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    if df.empty:
        return {}
    out: dict[int, dict] = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("PLAYER_ID"))
        if pid is None:
            continue
        gp = safe_int(row.get("GP")) or 0
        oreb = safe_float(row.get("OREB"))
        rec = {"gp": gp, "off_reb": oreb}
        if pid in out:
            prev = out[pid]
            total_gp = prev["gp"] + gp
            a, b = prev.get("off_reb"), oreb
            if a is not None and b is not None and total_gp > 0:
                prev["off_reb"] = (a * prev["gp"] + b * gp) / total_gp
            elif b is not None:
                prev["off_reb"] = b
            prev["gp"] = total_gp
        else:
            out[pid] = rec
    return out


def try_season_types_oreb(season: str, season_type: str, label: str) -> dict[int, dict]:
    alternates = [season_type]
    if season_type == SEASON_TYPE_PLAYIN:
        alternates.append("PlayIn")
    for st in alternates:
        try:
            print(f"    -> LeagueDashPlayerStats Base PerGame [OREB] ({season}) [{st}] ...", flush=True)
            return _fetch_base_pg_oreb_st(season, st)
        except Exception as exc:
            print(f"       [warn] {label} [{st}]: {exc}", flush=True)
    print(f"       [skip] {label} — no data.", flush=True)
    return {}


def fetch_oreb_side(season: str, season_type: str, polite: bool) -> dict[int, dict]:
    last_exc: Exception | None = None
    for attempt in range(1, API_RETRIES + 1):
        try:
            return try_season_types_oreb(season, season_type, f"OREB {season_type}")
        except Exception as exc:
            last_exc = exc
            print(f"       [retry {attempt}/{API_RETRIES}] {exc}", flush=True)
            api_snooze(polite, "retry OREB")
    print(f"       [FAIL] OREB {season_type} {season}: {last_exc}", flush=True)
    return {}


def merge_oreb_playoffs_playin(
    po: dict[int, dict],
    pi: dict[int, dict],
) -> dict[int, float | None]:
    """GP-weighted postseason OREB per game, one value per player_id."""
    out: dict[int, float | None] = {}
    for pid in set(po) | set(pi):
        in_po = pid in po
        in_pi = pid in pi
        if in_po and in_pi:
            gp_po = po[pid].get("gp") or 0
            gp_pi = pi[pid].get("gp") or 0
            ob_po = po[pid].get("off_reb")
            ob_pi = pi[pid].get("off_reb")
            tot = gp_po + gp_pi
            if tot > 0 and ob_po is not None and ob_pi is not None:
                out[pid] = round((ob_po * gp_po + ob_pi * gp_pi) / tot, 3)
            elif ob_po is not None:
                out[pid] = round(ob_po, 3)
            elif ob_pi is not None:
                out[pid] = round(ob_pi, 3)
            else:
                out[pid] = None
        elif in_po:
            v = po[pid].get("off_reb")
            out[pid] = round(v, 3) if v is not None else None
        else:
            v = pi[pid].get("off_reb")
            out[pid] = round(v, 3) if v is not None else None
    return out


# ---------------------------------------------------------------------------
# NBA: PtShot Totals (one season type)
# ---------------------------------------------------------------------------


def _ptshot_bucket_once(season: str, dist_range: str, season_type: str) -> dict[int, tuple[float, float]]:
    alternates = [season_type]
    if season_type == SEASON_TYPE_PLAYIN:
        alternates.append("PlayIn")
    last_err: Exception | None = None
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
            if df is None or df.empty:
                continue
            break
        except Exception as exc:
            last_err = exc
            continue
    else:
        print(f"       [skip] PtShot {dist_range[:20]}… [{season_type}]: {last_err}", flush=True)
        return {}

    out: dict[int, tuple[float, float]] = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("PLAYER_ID"))
        if pid is None:
            continue
        fgm = safe_float(row.get("FGM")) or 0.0
        fga = safe_float(row.get("FGA")) or 0.0
        if pid in out:
            a, b = out[pid]
            out[pid] = (a + fgm, b + fga)
        else:
            out[pid] = (fgm, fga)
    return out


def fetch_ptshot_side(
    season: str,
    dist_range: str,
    season_type: str,
    polite: bool,
) -> dict[int, tuple[float, float]]:
    label = f"{dist_range[:18]}… [{season_type}]"
    last_exc: Exception | None = None
    for attempt in range(1, API_RETRIES + 1):
        try:
            print(f"    -> LeagueDashPlayerPtShot Totals ({label}) ...", flush=True)
            return _ptshot_bucket_once(season, dist_range, season_type)
        except Exception as exc:
            last_exc = exc
            print(f"       [retry {attempt}/{API_RETRIES}] {exc}", flush=True)
            api_snooze(polite, "retry PtShot")
    print(f"       [FAIL] PtShot {label}: {last_exc}", flush=True)
    return {}


def add_fg_maps(
    a: dict[int, tuple[float, float]],
    b: dict[int, tuple[float, float]],
) -> dict[int, tuple[float, float]]:
    keys = set(a) | set(b)
    out: dict[int, tuple[float, float]] = {}
    for pid in keys:
        fgm_a, fga_a = a.get(pid, (0.0, 0.0))
        fgm_b, fga_b = b.get(pid, (0.0, 0.0))
        out[pid] = (fgm_a + fgm_b, fga_a + fga_b)
    return out


def combined_bucket_map(season: str, dist_range: str, polite: bool) -> dict[int, tuple[float, float]]:
    m_po = fetch_ptshot_side(season, dist_range, SEASON_TYPE_PLAYOFFS, polite)
    api_snooze(polite, "Play-In same bucket")
    m_pi = fetch_ptshot_side(season, dist_range, SEASON_TYPE_PLAYIN, polite)
    return add_fg_maps(m_po, m_pi)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def fetch_postseason_data_parallel(
    season: str,
    workers: int,
    polite: bool,
) -> tuple[dict[int, float | None], dict[int, tuple[float | None, float | None]]]:
    """
    Returns (oreb_by_player_id, fg_share_map).
    Fast mode: 10 concurrent NBA requests (2× OREB + 4×2 PtShot season types).
    """
    workers = max(1, min(workers, 12))

    if polite or workers == 1:
        po = fetch_oreb_side(season, SEASON_TYPE_PLAYOFFS, polite)
        api_snooze(polite, "OREB Play-In")
        pi = fetch_oreb_side(season, SEASON_TYPE_PLAYIN, polite)
        oreb_by_pid = merge_oreb_playoffs_playin(po, pi)

        contested_maps: list[dict[int, tuple[float, float]]] = []
        for i, dr in enumerate(CONTESTED_RANGES):
            if i > 0:
                api_snooze(polite, "next contested bucket combo")
            contested_maps.append(combined_bucket_map(season, dr, polite))
        open_maps: list[dict[int, tuple[float, float]]] = []
        for i, dr in enumerate(OPEN_RANGES):
            if i > 0:
                api_snooze(polite, "next open bucket combo")
            open_maps.append(combined_bucket_map(season, dr, polite))

        fg_map = merge_bucket_lists_to_fga_shares(contested_maps, open_maps)
        return oreb_by_pid, fg_map

    # Fast: 2 OREB + 8 PtShot (each bucket × season type) in one pool.
    print(
        f"    [parallel]  up to {workers} workers — 2 OREB + {len(ALL_DIST_RANGES) * 2} PtShot calls ...",
        flush=True,
    )

    Task = tuple  # ("o", st) | ("p", dr, st)

    def run_task(item: Task):
        if item[0] == "o":
            return ("o", item[1]), fetch_oreb_side(season, item[1], False)
        _, dr, st = item
        return ("p", dr, st), fetch_ptshot_side(season, dr, st, False)

    tasks: list[Task] = [
        ("o", SEASON_TYPE_PLAYOFFS),
        ("o", SEASON_TYPE_PLAYIN),
    ]
    for dr in ALL_DIST_RANGES:
        for st in (SEASON_TYPE_PLAYOFFS, SEASON_TYPE_PLAYIN):
            tasks.append(("p", dr, st))

    oreb_done: dict[str, dict[int, dict]] = {}
    pt_done: dict[tuple[str, str], dict[int, tuple[float, float]]] = {}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(run_task, t): t for t in tasks}
        for fut in as_completed(futures):
            tag, data = fut.result()
            if tag[0] == "o":
                _, st = tag
                oreb_done[st] = data
                print(f"       [done] OREB {st}", flush=True)
            else:
                _, dr, st = tag
                pt_done[(dr, st)] = data
                print(f"       [done] PtShot {dr[:12]}… {st}", flush=True)

    raw_po = oreb_done.get(SEASON_TYPE_PLAYOFFS, {})
    raw_pi = oreb_done.get(SEASON_TYPE_PLAYIN, {})
    oreb_by_pid = merge_oreb_playoffs_playin(raw_po, raw_pi)

    bucket_by_dr: dict[str, dict[int, tuple[float, float]]] = {}
    for dr in ALL_DIST_RANGES:
        m_po = pt_done.get((dr, SEASON_TYPE_PLAYOFFS), {})
        m_pi = pt_done.get((dr, SEASON_TYPE_PLAYIN), {})
        bucket_by_dr[dr] = add_fg_maps(m_po, m_pi)

    contested_maps = [bucket_by_dr[dr] for dr in CONTESTED_RANGES]
    open_maps = [bucket_by_dr[dr] for dr in OPEN_RANGES]
    fg_map = merge_bucket_lists_to_fga_shares(contested_maps, open_maps)
    return oreb_by_pid, fg_map


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------


def distinct_seasons(con: sqlite3.Connection) -> list[str]:
    cur = con.cursor()
    cur.execute(
        f"SELECT DISTINCT season FROM {PLAYOFFS_TABLE} ORDER BY season"
    )
    return [r[0] for r in cur.fetchall()]


def season_rows(con: sqlite3.Connection, season: str) -> list[tuple]:
    cur = con.cursor()
    cur.execute(
        f"""
        SELECT season, player_id, player_name, team_id, team_abbr
          FROM {PLAYOFFS_TABLE}
         WHERE season = ?
         ORDER BY player_id, team_id
        """,
        (season,),
    )
    return cur.fetchall()


def apply_season(
    con: sqlite3.Connection,
    season: str,
    oreb_by_pid: dict[int, float | None],
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

        oreb = oreb_by_pid.get(pid)
        if oreb is not None:
            set_parts.append("off_reb = ?")
            args.append(oreb)

        if not set_parts:
            continue

        args.extend([season, pid, tid])
        sql = (
            f"UPDATE {PLAYOFFS_TABLE} SET {', '.join(set_parts)} "
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
        description="Fix off_reb + contested/open FGA shares on player_stats_advanced_playoffs.",
    )
    parser.add_argument("--polite", action="store_true")
    parser.add_argument(
        "--workers",
        type=int,
        default=PLAYOFF_PARALLEL_DEFAULT,
        metavar="N",
        help=f"Parallel workers (default {PLAYOFF_PARALLEL_DEFAULT}, max 12; 10 NBA calls/season).",
    )
    parser.add_argument("--season-gap", type=float, default=None, metavar="SEC")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Log every updated row (default: quiet).",
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
        print(f"[FIX] No seasons in {PLAYOFFS_TABLE}.", flush=True)
        con.close()
        return

    mode = "polite / sequential" if polite else f"parallel ({args.workers} workers)"
    print(f"[FIX] Playoffs table: {PLAYOFFS_TABLE}", flush=True)
    print(f"[FIX] Seasons from DB: {', '.join(seasons)}", flush=True)
    print(f"[FIX] Mode: {mode}; season gap: {season_gap:.1f}s; row log: {'verbose' if args.verbose else 'quiet'}", flush=True)

    grand = 0
    for i, season in enumerate(seasons, 1):
        print(f"\n{'='*60}", flush=True)
        print(f"  Postseason {season}  ({i}/{len(seasons)})", flush=True)
        print(f"{'='*60}", flush=True)

        try:
            oreb_by_pid, fg_map = fetch_postseason_data_parallel(
                season,
                workers=args.workers,
                polite=polite,
            )

            if not fg_map and not oreb_by_pid:
                print(f"  [WARN] No API data for {season}; skipping.", flush=True)
                continue

            n = apply_season(con, season, oreb_by_pid, fg_map, verbose=args.verbose)
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
