"""
calculate_offensive_effects.py
------------------------------
Computes Nash / Klay / Shaq offensive effect flags from player_stats_basic and
player_stats_advanced (with team pace for per-100 → per-game conversion when
basic FGA volumes are absent) and writes only to player_special_effects.

Does not alter any table schema.

Effect eligibility requires total games played SUM(gp) >= MIN_GP across team
stints, plus a weighted season average of at least MIN_MPG_FLOOR minutes per
game (rotation staple).
"""

from __future__ import annotations

import math
import os
import sqlite3
import sys
from collections import defaultdict
from typing import Any

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

MIN_GP = 40
MIN_MPG_FLOOR = 25.0

NAME_SAMPLE_LIMIT = 10

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


def col_or_null(tbl: str, col: str, alias: str, available: set[str]) -> str:
    if col in available:
        return f"{tbl}.{col} AS {alias}"
    return f"NULL AS {alias}"


def per_game_from_per100(per100: float | None, poss: float | None) -> float | None:
    if per100 is None or poss is None or poss <= 0:
        return None
    return per100 * (poss / 100.0)


def minutes_select_expr(basic_cols: set[str]) -> str:
    if "min" in basic_cols:
        return "b.min AS minutes_pg"
    if "mp" in basic_cols:
        return "b.mp AS minutes_pg"
    return "b.mpg AS minutes_pg"


def load_stints(con: sqlite3.Connection) -> list[dict[str, Any]]:
    basic_cols = pragma_columns(con, "player_stats_basic")
    adv_cols = pragma_columns(con, "player_stats_advanced")
    min_expr = minutes_select_expr(basic_cols)
    usg_adv = col_or_null("a", "usg_pct", "adv_usg_pct", adv_cols)
    parts = [
        "b.player_name AS player_name",
        "b.season AS season",
        "b.gp AS gp",
        min_expr,
        "b.ast AS ast",
        "b.tov AS tov",
        "b.fg3_pct AS fg3_pct",
        "b.usg_pct AS basic_usg_pct",
        usg_adv,
        col_or_null("b", "fga", "basic_fga", basic_cols),
        col_or_null("b", "fgm", "basic_fgm", basic_cols),
        col_or_null("b", "fg3a", "basic_fg3a", basic_cols),
        col_or_null("b", "fg3m", "basic_fg3m", basic_cols),
        col_or_null("b", "fta", "basic_fta", basic_cols),
        col_or_null("b", "off_reb", "basic_off_reb", basic_cols),
        "a.ts_pct AS ts_pct",
        "a.efg_pct AS efg_pct",
        "a.fga_per100 AS fga_per100",
        "a.fg3a_per100 AS fg3a_per100",
        "a.fta_per100 AS fta_per100",
        "a.off_reb AS adv_off_reb",
        "COALESCE(t.pace, ls.pace) AS pace",
    ]
    sql = f"""
    SELECT {", ".join(parts)}
    FROM player_stats_basic AS b
    INNER JOIN player_stats_advanced AS a
      ON a.season = b.season
     AND a.player_id = b.player_id
     AND a.team_id = b.team_id
    LEFT JOIN team_stats AS t
      ON t.season = b.season AND t.team_id = b.team_id
    LEFT JOIN league_stats AS ls
      ON ls.season = b.season
    """
    cur = con.execute(sql)
    return [dict(r) for r in cur.fetchall()]


def stint_derived(row: dict[str, Any]) -> dict[str, Any] | None:
    """Per-team stint: per-game volumes and 2P components. Returns None to skip stint."""
    w = row.get("gp")
    w = int(w) if w is not None else 0
    if w <= 0:
        return None

    mpg = ffloat(row.get("minutes_pg"))
    pace = ffloat(row.get("pace"))
    poss = (mpg / 48.0) * pace if pace is not None and mpg is not None else None

    basic_fga = ffloat(row.get("basic_fga"))
    basic_fgm = ffloat(row.get("basic_fgm"))
    basic_fg3a = ffloat(row.get("basic_fg3a"))
    basic_fg3m = ffloat(row.get("basic_fg3m"))
    basic_fta = ffloat(row.get("basic_fta"))

    fga_pg = basic_fga
    fg3a_pg = basic_fg3a
    fta_pg = basic_fta
    if fga_pg is None:
        fga_pg = per_game_from_per100(ffloat(row.get("fga_per100")), poss)
    if fg3a_pg is None:
        fg3a_pg = per_game_from_per100(ffloat(row.get("fg3a_per100")), poss)
    if fta_pg is None:
        fta_pg = per_game_from_per100(ffloat(row.get("fta_per100")), poss)

    fg3_pct = ffloat(row.get("fg3_pct"))
    efg = ffloat(row.get("efg_pct"))

    two_pa = None
    two_pm = None
    if fga_pg is not None and fg3a_pg is not None:
        two_pa = fga_pg - fg3a_pg
    if (
        basic_fgm is not None
        and basic_fg3m is not None
        and two_pa is not None
        and two_pa > 0
    ):
        two_pm = basic_fgm - basic_fg3m
    elif (
        efg is not None
        and fga_pg is not None
        and fg3_pct is not None
        and fg3a_pg is not None
        and two_pa is not None
        and two_pa > 0
    ):
        fg3m_est = fg3_pct * fg3a_pg
        fgm_est = efg * fga_pg - 0.5 * fg3m_est
        two_pm = fgm_est - fg3m_est

    oreb = ffloat(row.get("basic_off_reb"))
    if oreb is None:
        oreb = ffloat(row.get("adv_off_reb"))

    usg = ffloat(row.get("adv_usg_pct"))
    if usg is None:
        usg = ffloat(row.get("basic_usg_pct"))

    return {
        "w": float(w),
        "minutes_pg": mpg,
        "ast": ffloat(row.get("ast")),
        "tov": ffloat(row.get("tov")),
        "ts_pct": ffloat(row.get("ts_pct")),
        "fg3_pct": fg3_pct,
        "usg_pct": usg,
        "fg3a_pg": fg3a_pg,
        "fta_pg": fta_pg,
        "two_pa": two_pa,
        "two_pm": two_pm,
        "oreb": oreb,
    }


def weighted_season_metrics(
    stints: list[dict[str, Any]],
) -> dict[str, Any] | None:
    sum_w = sum(s["w"] for s in stints)
    if sum_w <= 0:
        return None

    def wavg(getter: str) -> float | None:
        num = 0.0
        den = 0.0
        for s in stints:
            v = s.get(getter)
            if v is None:
                continue
            num += v * s["w"]
            den += s["w"]
        if den <= 0:
            return None
        return num / den

    ast_bar = wavg("ast")
    tov_bar = wavg("tov")
    ts_bar = wavg("ts_pct")
    fg3_pct_bar = wavg("fg3_pct")
    usg_bar = wavg("usg_pct")
    fg3a_bar = wavg("fg3a_pg")
    fta_bar = wavg("fta_pg")
    oreb_bar = wavg("oreb")

    w_vol = sum(s["w"] for s in stints if s.get("two_pa") is not None)
    tot_2pa = sum(s["two_pa"] * s["w"] for s in stints if s.get("two_pa") is not None)
    inner_2p = [
        s
        for s in stints
        if s.get("two_pa") is not None
        and s.get("two_pm") is not None
        and s["two_pa"] > 0
    ]
    tot_2pm = sum(s["two_pm"] * s["w"] for s in inner_2p)
    tot_2pa_eff = sum(s["two_pa"] * s["w"] for s in inner_2p)
    two_pct = (tot_2pm / tot_2pa_eff) if tot_2pa_eff > 0 else None
    interior_vol_bar = (tot_2pa / w_vol) if w_vol > 0 else None
    min_bar = wavg("minutes_pg")

    return {
        "sum_w": sum_w,
        "min_bar": min_bar,
        "ast_bar": ast_bar,
        "tov_bar": tov_bar,
        "ts_bar": ts_bar,
        "fg3_pct_bar": fg3_pct_bar,
        "usg_bar": usg_bar,
        "fg3a_bar": fg3a_bar,
        "fta_bar": fta_bar,
        "oreb_bar": oreb_bar,
        "interior_vol_bar": interior_vol_bar,
        "two_pct": two_pct,
    }


def _meets_minutes(m: dict[str, Any]) -> bool:
    mb = m.get("min_bar")
    return mb is not None and mb >= MIN_MPG_FLOOR


def _meets_gp(m: dict[str, Any]) -> bool:
    tw = m.get("sum_w")
    if tw is None:
        return False
    return float(tw) >= MIN_GP


def eval_nash(m: dict[str, Any]) -> bool:
    if not _meets_gp(m) or not _meets_minutes(m):
        return False
    if m["ast_bar"] is None or m["ts_bar"] is None:
        return False
    if m["ast_bar"] < 7.5 or m["ts_bar"] < 0.58:
        return False
    tov = m["tov_bar"]
    ast = m["ast_bar"]
    if tov is None:
        return False
    if tov <= 1e-9:
        return ast >= 7.5
    return (ast / tov) >= 3.2


def eval_klay(m: dict[str, Any]) -> bool:
    if not _meets_gp(m) or not _meets_minutes(m):
        return False
    if (
        m["fg3a_bar"] is None
        or m["fg3_pct_bar"] is None
        or m["usg_bar"] is None
        or m["ast_bar"] is None
    ):
        return False
    if m["fg3a_bar"] < 7.0 or m["fg3_pct_bar"] < 0.39:
        return False
    return (m["usg_bar"] <= 0.24) or (m["ast_bar"] <= 3.0)


def eval_shaq(m: dict[str, Any]) -> bool:
    if not _meets_gp(m) or not _meets_minutes(m):
        return False
    if (
        m["interior_vol_bar"] is None
        or m["two_pct"] is None
        or m["fta_bar"] is None
        or m["oreb_bar"] is None
    ):
        return False
    if m["interior_vol_bar"] < 11.0:
        return False
    if m["two_pct"] < 0.55:
        return False
    if m["fta_bar"] < 6.5:
        return False
    if m["oreb_bar"] < 2.5:
        return False
    return True


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        stints_raw = load_stints(con)
        by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in stints_raw:
            name = row.get("player_name")
            season = row.get("season")
            if not name or not season:
                continue
            key = (str(name).strip(), str(season).strip())
            d = stint_derived(row)
            if d is not None:
                by_key[key].append(d)

        results: dict[tuple[str, str], tuple[bool, bool, bool]] = {}
        for key, stint_list in by_key.items():
            m = weighted_season_metrics(stint_list)
            if m is None:
                continue
            results[key] = (eval_nash(m), eval_klay(m), eval_shaq(m))

        cur = con.cursor()
        cur.execute(
            """
            UPDATE player_special_effects
            SET shaq_effect = 'No', klay_effect = 'No', nash_effect = 'No'
            """
        )

        nash_names: list[str] = []
        klay_names: list[str] = []
        shaq_names: list[str] = []
        nash_c = klay_c = shaq_c = 0

        upd = []
        for (name, season), (nash, klay, shaq) in results.items():
            nash_v = "Yes" if nash else "No"
            klay_v = "Yes" if klay else "No"
            shaq_v = "Yes" if shaq else "No"
            if nash:
                nash_c += 1
                nash_names.append(name)
            if klay:
                klay_c += 1
                klay_names.append(name)
            if shaq:
                shaq_c += 1
                shaq_names.append(name)
            if nash or klay or shaq:
                upd.append((nash_v, klay_v, shaq_v, name, season))

        cur.executemany(
            """
            UPDATE player_special_effects
            SET nash_effect = ?, klay_effect = ?, shaq_effect = ?
            WHERE player_name = ? AND season = ?
            """,
            upd,
        )
        con.commit()

        print(
            f"Nash Effects: {nash_c}, Klay Effects: {klay_c}, Shaq Effects: {shaq_c}",
            flush=True,
        )
        print(
            f"(GP floor: {MIN_GP}+ season games; MPG floor: {MIN_MPG_FLOOR}+ "
            "weighted average minutes per game)",
            flush=True,
        )

        def print_name_sample(label: str, names: list[str]) -> None:
            uniq = sorted(set(names))
            if not uniq:
                print(f"  {label} (sample): (none)", flush=True)
                return
            head = uniq[:NAME_SAMPLE_LIMIT]
            joined = ", ".join(head)
            more = len(uniq) - len(head)
            suffix = f" … (+{more} more)" if more > 0 else ""
            print(f"  {label} (sample): {joined}{suffix}", flush=True)

        print_name_sample("Nash", nash_names)
        print_name_sample("Klay", klay_names)
        print_name_sample("Shaq", shaq_names)

    finally:
        con.close()


if __name__ == "__main__":
    main()
