"""
calculate_base_pr.py
--------------------
Builds simulation-ready base Player Rating (PR) into player_simulation_pr.

Reads: player_stats_basic, player_stats_advanced, player_special_effects.
Creates/writes only player_simulation_pr (does not alter other tables).
"""

from __future__ import annotations

import math
import os
import sqlite3
import sys
from collections import defaultdict
from typing import Any

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

TS_NEUTRAL = 0.55
DUAL_GATE_CAP = 60.0
ALIEN_BONUS = 12.0
EFF_GOD_BONUS = 8.0
DEFENSE_BONUS = 1.5
BOARD_BONUS = 1.0

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def pragma_columns(con: sqlite3.Connection, table: str) -> set[str]:
    cur = con.execute(f"PRAGMA table_info({table})")
    return {r[1] for r in cur.fetchall()}


def ffloat(v: Any) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
        return None if math.isnan(x) else x
    except (TypeError, ValueError):
        return None


def minutes_select_expr(basic_cols: set[str]) -> str:
    if "min" in basic_cols:
        return "b.min AS minutes_pg"
    if "mp" in basic_cols:
        return "b.mp AS minutes_pg"
    return "b.mpg AS minutes_pg"


def rebounds_select_expr(basic_cols: set[str]) -> str:
    if "trb" in basic_cols:
        return "b.trb AS reb_pg"
    return "b.reb AS reb_pg"


def col_or_null(tbl: str, col: str, alias: str, available: set[str]) -> str:
    if col in available:
        return f"{tbl}.{col} AS {alias}"
    return f"NULL AS {alias}"


def is_yes(v: Any) -> bool:
    if v is None:
        return False
    return str(v).strip().lower() == "yes"


def load_stints(con: sqlite3.Connection) -> list[dict[str, Any]]:
    basic_cols = pragma_columns(con, "player_stats_basic")
    min_expr = minutes_select_expr(basic_cols)
    reb_expr = rebounds_select_expr(basic_cols)
    stl_expr = col_or_null("b", "stl", "stl", basic_cols)
    blk_expr = col_or_null("b", "blk", "blk", basic_cols)
    sql = f"""
      SELECT
        b.player_name AS player_name,
        b.season AS season,
        b.gp AS gp,
        {min_expr},
        b.pts AS pts,
        b.ast AS ast,
        {reb_expr},
        {stl_expr},
        {blk_expr},
        a.ts_pct AS ts_pct
      FROM player_stats_basic AS b
      INNER JOIN player_stats_advanced AS a
        ON a.season = b.season
       AND a.player_id = b.player_id
       AND a.team_id = b.team_id
    """
    prev = con.row_factory
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(sql)
        return [dict(r) for r in cur.fetchall()]
    finally:
        con.row_factory = prev


def stint_weights(row: dict[str, Any]) -> dict[str, Any] | None:
    w = row.get("gp")
    w = int(w) if w is not None else 0
    if w <= 0:
        return None
    return {
        "w": float(w),
        "minutes_pg": ffloat(row.get("minutes_pg")),
        "pts": ffloat(row.get("pts")),
        "ast": ffloat(row.get("ast")),
        "reb": ffloat(row.get("reb_pg")),
        "stl": ffloat(row.get("stl")),
        "blk": ffloat(row.get("blk")),
        "ts_pct": ffloat(row.get("ts_pct")),
    }


def weighted_season_aggregate(stints: list[dict[str, Any]]) -> dict[str, Any] | None:
    sum_w = sum(s["w"] for s in stints)
    if sum_w <= 0:
        return None

    def wavg(key: str) -> float | None:
        num = 0.0
        den = 0.0
        for s in stints:
            v = s.get(key)
            if v is None:
                continue
            num += v * s["w"]
            den += s["w"]
        if den <= 0:
            return None
        return num / den

    return {
        "gp": int(sum_w),
        "mpg": wavg("minutes_pg"),
        "pts": wavg("pts"),
        "ast": wavg("ast"),
        "reb": wavg("reb"),
        "stl": wavg("stl"),
        "blk": wavg("blk"),
        "ts_pct": wavg("ts_pct"),
    }


def load_special_effects(con: sqlite3.Connection) -> dict[tuple[str, str], dict[str, str]]:
    cur = con.execute(
        """
        SELECT player_name, season, alien_effect, efficiency_god,
               defense_effect, board_effect
        FROM player_special_effects
        """
    )
    out: dict[tuple[str, str], dict[str, str]] = {}
    for r in cur.fetchall():
        pn, sn = r[0], r[1]
        if pn is None or sn is None:
            continue
        key = (str(pn).strip(), str(sn).strip())
        out[key] = {
            "alien_effect": r[2],
            "efficiency_god": r[3],
            "defense_effect": r[4],
            "board_effect": r[5],
        }
    return out


def per36(stat: float | None, min_pg: float | None) -> float:
    if stat is None or min_pg is None or min_pg <= 0:
        return 0.0
    return (stat / min_pg) * 36.0


def effect_bonus(fx: dict[str, str] | None) -> float:
    if not fx:
        return 0.0
    b = 0.0
    if is_yes(fx.get("alien_effect")):
        b += ALIEN_BONUS
    if is_yes(fx.get("efficiency_god")):
        b += EFF_GOD_BONUS
    if is_yes(fx.get("defense_effect")):
        b += DEFENSE_BONUS
    if is_yes(fx.get("board_effect")):
        b += BOARD_BONUS
    return b


def compute_pr(
    agg: dict[str, Any],
    fx: dict[str, str] | None,
) -> tuple[float, float, bool]:
    """
    Returns (base_pr, final_pr, dual_gate_active).

    final_pr is raw_pr + effect bonuses. If dual_gate_active (gp < 20 or mpg*gp < 300),
    base_pr = min(final_pr, DUAL_GATE_CAP); otherwise base_pr = final_pr.
    """
    mpg = agg["mpg"]
    gp = agg["gp"]
    ts = agg["ts_pct"] if agg["ts_pct"] is not None else TS_NEUTRAL
    if ts <= 0:
        ts = TS_NEUTRAL

    pts_36 = per36(agg["pts"], mpg)
    ast_36 = per36(agg["ast"], mpg)
    trb_36 = per36(agg["reb"], mpg)
    stl_36 = per36(agg["stl"], mpg)
    blk_36 = per36(agg["blk"], mpg)

    prod_36 = (pts_36 * (ts / TS_NEUTRAL)) + (ast_36 * 1.5) + (trb_36 * 0.6) + (
        (stl_36 + blk_36) * 1.5
    )

    if mpg is None or mpg <= 0:
        raw_pr = 0.0
    else:
        modifier = (mpg / 36.0) ** 0.5
        raw_pr = prod_36 * modifier

    final_pr = raw_pr + effect_bonus(fx)

    total_mins = (mpg * float(gp)) if mpg is not None else None
    dual_gate = gp < 20 or (total_mins is not None and total_mins < 300.0)
    if dual_gate:
        base_pr = min(final_pr, DUAL_GATE_CAP)
    else:
        base_pr = final_pr
    return base_pr, final_pr, dual_gate


def ensure_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS player_simulation_pr (
            player_name TEXT NOT NULL,
            season      TEXT NOT NULL,
            gp          INTEGER,
            mpg         REAL,
            base_pr     REAL,
            PRIMARY KEY (player_name, season)
        );
        """
    )


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        ensure_table(con)
        effects = load_special_effects(con)

        by_ps: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in load_stints(con):
            st = stint_weights(row)
            if st is None:
                continue
            pn = row.get("player_name")
            sn = row.get("season")
            if pn is None or sn is None:
                continue
            key = (str(pn).strip(), str(sn).strip())
            by_ps[key].append(st)

        crushed_candidates: list[tuple[str, str, float]] = []
        seasons_seen: set[str] = set()

        rows_out: list[tuple[str, str, int, float | None, float]] = []
        for (pn, sn), stints in by_ps.items():
            agg = weighted_season_aggregate(stints)
            if agg is None:
                continue
            seasons_seen.add(sn)
            fx = effects.get((pn, sn))
            base_pr, pre_cap_final, dual_gate = compute_pr(agg, fx)
            rows_out.append((pn, sn, agg["gp"], agg["mpg"], base_pr))
            if (
                dual_gate
                and pre_cap_final > 75.0
                and base_pr < pre_cap_final
            ):
                crushed_candidates.append((pn, sn, pre_cap_final))

        con.executemany(
            """
            INSERT OR REPLACE INTO player_simulation_pr
                (player_name, season, gp, mpg, base_pr)
            VALUES (?, ?, ?, ?, ?);
            """,
            rows_out,
        )
        con.commit()

        if not seasons_seen:
            print("No player-season rows to write.", flush=True)
            return

        latest_season = max(seasons_seen)
        top15 = sorted(
            [r for r in rows_out if r[1] == latest_season],
            key=lambda x: x[4],
            reverse=True,
        )[:15]

        print(f"Top 15 by base_pr — season {latest_season}", flush=True)
        for i, (pn, sn, gp, mpg, pr) in enumerate(top15, start=1):
            print(
                f"  {i:2d}. {pn} | gp={gp} mpg={mpg:.1f} base_pr={pr:.2f}",
                flush=True,
            )

        crushed_candidates.sort(key=lambda x: -x[2])
        crushed = crushed_candidates[:5]
        print("", flush=True)
            print("  (none found)", flush=True)
        else:
            for pn, sn, pre in crushed:
                print(
                    f"  {pn} ({sn}) — uncapped final_pr={pre:.2f} -> base_pr={DUAL_GATE_CAP:.1f}",
                    flush=True,
                )
    finally:
        con.close()


if __name__ == "__main__":
    main()
