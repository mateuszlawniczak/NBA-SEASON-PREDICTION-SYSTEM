"""
fix_playoff_rim_attempts.py
-----------------------------
Updates ONLY player_stats_advanced_playoffs.opp_fg_at_rim_contested.

Default (no flags): divide the **current** stored value by postseason games played
(player_stats_basic_playoffs.gp for matching season / player_id / team_id).

--api-totals: refetch Less Than 6Ft defended FGA totals from NBA (Playoffs + Play-In),
merge sums, then divide by merged **GP** from the same defend rows (dict key \"GP\").
FGA uses normalized \"FGA\" from FGA_LT_06 when needed — never FG_PCT, never by index.

Anti-bot (API mode only): time.sleep(random.uniform(2.1, 3.7)) between requests.
"""

from __future__ import annotations

import argparse
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
ADV = "player_stats_advanced_playoffs"
BASIC = "player_stats_basic_playoffs"
TIMEOUT = 90


def normalize_ptdefend_row_dict(row_dict: dict) -> dict:
    """Ensure \"FGA\" exists for reads (NBA uses FGA_LT_06 for Less Than 6Ft)."""
    d = dict(row_dict)
    if d.get("FGA") is None and d.get("FGA_LT_06") is not None:
        d["FGA"] = d["FGA_LT_06"]
    return d


def api_pause() -> None:
    time.sleep(random.uniform(2.1, 3.7))


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


def rim_fga_from_mapping(row_dict: dict) -> float | None:
    return safe_float(row_dict.get("FGA"))


def gp_from_mapping(row_dict: dict) -> int:
    g = safe_int(row_dict.get("GP"))
    return g if g is not None and g > 0 else 0


def fetch_less_than_6ft(season: str, season_type: str) -> tuple[dict[int, float], dict[int, int]]:
    alternates = [season_type]
    if season_type == "Play In":
        alternates.append("PlayIn")
    elif season_type == "PlayIn":
        alternates.append("Play In")

    last_err: Exception | None = None
    for st in alternates:
        try:
            r = LeagueDashPtDefend(
                season=season,
                per_mode_simple="Totals",
                defense_category="Less Than 6Ft",
                season_type_all_star=st,
                timeout=TIMEOUT,
            )
            df = r.get_data_frames()[0]
            if df is None or df.empty:
                continue
            acc_fga: dict[int, float] = {}
            acc_gp: dict[int, int] = {}
            for _, row in df.iterrows():
                row_dict = normalize_ptdefend_row_dict(row.to_dict())
                pid = safe_int(row_dict.get("CLOSE_DEF_PERSON_ID"))
                if pid is None:
                    continue
                fga = rim_fga_from_mapping(row_dict)
                if fga is not None:
                    acc_fga[pid] = acc_fga.get(pid, 0.0) + fga
                gpi = gp_from_mapping(row_dict)
                if gpi:
                    acc_gp[pid] = acc_gp.get(pid, 0) + gpi
            return acc_fga, acc_gp
        except Exception as exc:
            last_err = exc
            continue

    print(f"    [warn] Less Than 6Ft [{season_type}]: {last_err}", flush=True)
    return {}, {}


def merge_po_pi_fga(po: dict[int, float], pi: dict[int, float]) -> dict[int, float]:
    out: dict[int, float] = {}
    for pid in set(po) | set(pi):
        out[pid] = (po.get(pid) or 0.0) + (pi.get(pid) or 0.0)
    return out


def merge_po_pi_gp(po: dict[int, int], pi: dict[int, int]) -> dict[int, int]:
    out: dict[int, int] = {}
    for pid in set(po) | set(pi):
        out[pid] = (po.get(pid) or 0) + (pi.get(pid) or 0)
    return out


def format_attempts(x: float) -> str:
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:.4f}".rstrip("0").rstrip(".")


def divide_existing_by_basic_gp() -> None:
    """opp_fg_at_rim_contested := current / gp from player_stats_basic_playoffs."""
    print(f"[fix_playoff_rim_attempts] DB: {DB_PATH} (divide by {BASIC}.gp)", flush=True)
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    rows = cur.execute(
        f"""
        SELECT a.rowid, a.player_name, a.season, a.player_id, a.team_id,
               a.opp_fg_at_rim_contested, b.gp
        FROM {ADV} AS a
        INNER JOIN {BASIC} AS b
          ON a.season = b.season
         AND a.player_id = b.player_id
         AND (
              a.team_id = b.team_id
              OR (a.team_id IS NULL AND b.team_id IS NULL)
         )
        WHERE b.gp IS NOT NULL AND b.gp > 0
          AND a.opp_fg_at_rim_contested IS NOT NULL
        ORDER BY a.season, a.player_name
        """
    ).fetchall()

    patched = 0
    for rowid, pname, season, pid, tid, cur_att, gp in rows:
        pg = float(cur_att) / float(gp)
        cur.execute(
            f"UPDATE {ADV} SET opp_fg_at_rim_contested = ? WHERE rowid = ?",
            (round(pg, 6), rowid),
        )
        print(
            f"[PATCH] {pname} ({season}) - New Rim Attempts: {format_attempts(pg)}",
            flush=True,
        )
        patched += 1

    con.commit()
    con.close()
    print(f"\n[fix_playoff_rim_attempts] Done. Rows updated: {patched}", flush=True)


def run_api_totals_then_per_game() -> None:
    print(f"[fix_playoff_rim_attempts] DB: {DB_PATH} (API totals / merged GP)", flush=True)
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    seasons = [
        r[0]
        for r in cur.execute(f"SELECT DISTINCT season FROM {ADV} ORDER BY season")
    ]
    if not seasons:
        print("[done] No seasons in table.", flush=True)
        con.close()
        return

    for season in seasons:
        print(f"\n--- Season {season} ---", flush=True)

        po_fga, po_gp = fetch_less_than_6ft(season, "Playoffs")
        api_pause()
        pi_fga, pi_gp = fetch_less_than_6ft(season, "Play In")
        api_pause()

        merged_fga = merge_po_pi_fga(po_fga, pi_fga)
        merged_gp = merge_po_pi_gp(po_gp, pi_gp)

        pids = {
            r[0]
            for r in cur.execute(
                f"SELECT DISTINCT player_id FROM {ADV} WHERE season = ?", (season,)
            )
        }

        patched = 0
        for pid in sorted(pids):
            if pid not in merged_fga or merged_gp.get(pid, 0) <= 0:
                continue
            attempts_pg = merged_fga[pid] / float(merged_gp[pid])

            name_row = cur.execute(
                f"SELECT player_name FROM {ADV} WHERE season = ? AND player_id = ? LIMIT 1",
                (season, pid),
            ).fetchone()
            display_name = name_row[0] if name_row else str(pid)

            cur.execute(
                f"""
                UPDATE {ADV}
                SET opp_fg_at_rim_contested = ?
                WHERE player_id = ? AND season = ?
                """,
                (round(attempts_pg, 6), pid, season),
            )

            print(
                f"[PATCH] {display_name} ({season}) - New Rim Attempts: {format_attempts(attempts_pg)}",
                flush=True,
            )
            patched += 1

        con.commit()
        print(f"  [season {season}] {patched} players patched.", flush=True)

    con.close()
    print("\n[fix_playoff_rim_attempts] Done.", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Patch opp_fg_at_rim_contested (per game).")
    ap.add_argument(
        "--api-totals",
        action="store_true",
        help="Refetch NBA defend totals and divide by merged GP from the feed",
    )
    args = ap.parse_args()
    if args.api_totals:
        run_api_totals_then_per_game()
    else:
        divide_existing_by_basic_gp()


if __name__ == "__main__":
    main()
