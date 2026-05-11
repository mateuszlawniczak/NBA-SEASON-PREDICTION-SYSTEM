"""
patch_open_shots.py
-------------------
Recompute player_stats_advanced.open_shot_pct using a strict definition:
only closest-defender distance bucket "6+ Feet - Wide Open" counts as open.

    new_open_shot_pct = (Wide Open tracking FGA) / (Total season FGA)

Wide Open FGA comes from LeagueDashPlayerPtShot with
    CloseDefDistRange = "6+ Feet - Wide Open"
    per_mode_simple   = "Totals"

Total season FGA is read from the local DB when player_stats_basic.fga
exists (SUM across team rows for traded players). If that column is absent,
a single LeagueDashPlayerStats call per season (Totals / Base) supplies
league-wide FGA totals by player_id.

Only column written: open_shot_pct (UPDATE-only; no schema changes).
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

from nba_api.stats.endpoints import LeagueDashPlayerPtShot, LeagueDashPlayerStats

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

WIDE_OPEN_RANGE = "6+ Feet - Wide Open"

TIMEOUT = 90

API_SLEEP_RANGE = (2.1, 3.7)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def api_pause() -> None:
    t = random.uniform(API_SLEEP_RANGE[0], API_SLEEP_RANGE[1])
    print(f"    [wait]  sleeping {t:.1f}s before next API call ...", flush=True)
    time.sleep(t)


def table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    cur = con.cursor()
    return {r[1] for r in cur.execute(f"PRAGMA table_info({table})")}


def load_db_players_by_season(con: sqlite3.Connection) -> dict[str, list[tuple[int, str]]]:
    """
    season -> list of (player_id, player_name) from player_stats_advanced.
    """
    cur = con.cursor()
    cur.execute(
        """
        SELECT season, player_id, MAX(player_name) AS player_name
          FROM player_stats_advanced
         WHERE player_id IS NOT NULL
         GROUP BY season, player_id
         ORDER BY season, player_id
        """
    )
    out: dict[str, list[tuple[int, str]]] = {}
    for season, pid, pname in cur.fetchall():
        out.setdefault(season, []).append((int(pid), str(pname or f"PlayerID={pid}")))
    return out


def load_total_fga_from_basic(
    con: sqlite3.Connection, season: str
) -> dict[int, float] | None:
    cols = table_columns(con, "player_stats_basic")
    if "fga" not in cols:
        return None
    cur = con.cursor()
    cur.execute(
        """
        SELECT player_id, SUM(fga)
          FROM player_stats_basic
         WHERE season = ?
         GROUP BY player_id
        """,
        (season,),
    )
    out: dict[int, float] = {}
    for pid, s in cur.fetchall():
        if pid is None:
            continue
        v = safe_float(s)
        if v is not None and v > 0:
            out[int(pid)] = v
    return out


def fetch_league_totals_fga(season: str) -> dict[int, float]:
    """
    LeagueDashPlayerStats — Totals / Base. Sum FGA for traded players across teams.
    """
    print(f"    -> LeagueDashPlayerStats Totals (season FGA denominator) ...", flush=True)
    r = LeagueDashPlayerStats(
        season=season,
        per_mode_detailed="Totals",
        measure_type_detailed_defense="Base",
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    out: dict[int, float] = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("PLAYER_ID"))
        fga = safe_float(row.get("FGA"))
        if pid is None:
            continue
        out[pid] = out.get(pid, 0.0) + (fga or 0.0)
    return out


def fetch_wide_open_fga(season: str) -> dict[int, float]:
    print(f"    -> LeagueDashPlayerPtShot Totals [{WIDE_OPEN_RANGE}] ...", flush=True)
    r = LeagueDashPlayerPtShot(
        season=season,
        close_def_dist_range_nullable=WIDE_OPEN_RANGE,
        per_mode_simple="Totals",
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    out: dict[int, float] = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("PLAYER_ID"))
        fga = safe_float(row.get("FGA"))
        if pid is None:
            continue
        out[pid] = out.get(pid, 0.0) + (fga or 0.0)
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    players_by_season = load_db_players_by_season(con)
    if not players_by_season:
        print("[patch_open_shots] No rows in player_stats_advanced — nothing to do.", flush=True)
        con.close()
        return

    seasons = sorted(players_by_season.keys())
    print(
        f"[patch_open_shots] Seasons from DB: {', '.join(seasons)}",
        flush=True,
    )
    use_basic_fga = "fga" in table_columns(con, "player_stats_basic")
    if use_basic_fga:
        print(
            "[patch_open_shots] Using SUM(player_stats_basic.fga) as season FGA denominator.",
            flush=True,
        )
    else:
        print(
            "[patch_open_shots] No player_stats_basic.fga — using "
            "LeagueDashPlayerStats Totals per season for denominator.",
            flush=True,
        )

    cur = con.cursor()
    total_updates = 0
    need_pause_before_next_api = False

    def pause_before_api() -> None:
        nonlocal need_pause_before_next_api
        if need_pause_before_next_api:
            api_pause()
        need_pause_before_next_api = True

    for i, season in enumerate(seasons, 1):
        print(f"\n{'='*60}", flush=True)
        print(f"  Season {season}  ({i}/{len(seasons)})", flush=True)
        print(f"{'='*60}", flush=True)

        try:
            total_fga_map: dict[int, float] | None = None
            if use_basic_fga:
                total_fga_map = load_total_fga_from_basic(con, season)
            if total_fga_map is None:
                pause_before_api()
                total_fga_map = fetch_league_totals_fga(season)

            pause_before_api()
            wide_map = fetch_wide_open_fga(season)

            for player_id, player_name in players_by_season[season]:
                denom = total_fga_map.get(player_id, 0.0)
                num = wide_map.get(player_id, 0.0)
                pct: float | None
                if denom and denom > 0:
                    pct = round(float(num) / float(denom), 4)
                    cur.execute(
                        """
                        UPDATE player_stats_advanced
                           SET open_shot_pct = ?
                         WHERE player_id = ?
                           AND season = ?
                        """,
                        (pct, player_id, season),
                    )
                else:
                    pct = None
                    cur.execute(
                        """
                        UPDATE player_stats_advanced
                           SET open_shot_pct = NULL
                         WHERE player_id = ?
                           AND season = ?
                        """,
                        (player_id, season),
                    )
                con.commit()
                total_updates += cur.rowcount

                if pct is not None:
                    print(
                        f"[NERF APPLIED] {player_name} ({season}) - "
                        f"New Strict Open Shot Pct: {pct:.2f}",
                        flush=True,
                    )
                else:
                    print(
                        f"[NERF APPLIED] {player_name} ({season}) - "
                        f"New Strict Open Shot Pct: NULL (no season FGA)",
                        flush=True,
                    )

        except Exception as exc:
            print(f"  [ERR] Season {season} failed: {exc}", flush=True)
            import traceback

            traceback.print_exc()
            print("  Skipping season — wait 10s then continue ...", flush=True)
            time.sleep(10)

    con.close()
    print(f"\n{'='*60}", flush=True)
    print(f"  DONE — attempted updates on {total_updates} row(s).", flush=True)
    print(f"  Database: {DB_PATH}", flush=True)


if __name__ == "__main__":
    main()
