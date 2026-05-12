"""
calculate_base_pr.py
--------------------
Final 2024-25 base Player Rating (PR): era-adjusted scoring (TS vs league
baseline), playmaking from AST/TOV shape, **reduced** rebound weighting,
defensive stocks + deflections, optional rim bonus, tiered MPG workload, and
**gp / 82** availability (missed games directly lower output).

Rim bonus applies only if ``player_stats_basic.pf`` exists (``blk > pf``).

Reads: player_stats_basic, player_stats_advanced (joined).
Writes only: player_simulation_pr for season '2024-25' (after deleting that season).
"""

from __future__ import annotations

import math
import os
import sqlite3
import sys
from typing import Any

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

TARGET_SEASON = "2024-25"

# Era baseline — adjust when using another season/league snapshot.
ERA_AVG_TS = 0.58

MPG_DIVISOR = 32.0
GP_SEASON = 82.0
OREB_WEIGHT = 1.5
TRB_COEF = 0.72
DEF_COEF = 2.5
RIM_BONUS = 3

SPECIAL_AUDIT_PLAYERS = (
    "Stephen Curry",
    "Alperen Sengun",
    "Amen Thompson",
)

TOP_N = 25

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


def fint(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def season_minutes(mpg: float | None, gp: int, total_minutes: int | None) -> float | None:
    if total_minutes is not None and total_minutes > 0:
        return float(total_minutes)
    if mpg is not None and mpg > 0 and gp > 0:
        return mpg * float(gp)
    return None


def mpg_tier_modifier(mpg: float) -> float:
    r = mpg / MPG_DIVISOR
    if mpg >= 24:
        return r
    if mpg >= 16:
        return r**0.7
    return r**0.5


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
    cols = pragma_columns(con, "player_simulation_pr")
    if "team" not in cols:
        con.execute("ALTER TABLE player_simulation_pr ADD COLUMN team TEXT")


def load_rows(con: sqlite3.Connection) -> list[dict[str, Any]]:
    basic_cols = pragma_columns(con, "player_stats_basic")
    pf_expr = "b.pf AS pf" if "pf" in basic_cols else "NULL AS pf"

    sql = f"""
      SELECT
        b.player_name AS player_name,
        b.team_abbr AS team,
        b.season AS season,
        b.gp AS gp,
        b.total_minutes AS total_minutes,
        b.mpg AS mpg_pg,
        b.pts AS pts,
        b.ast AS ast,
        b.stl AS stl,
        b.blk AS blk,
        b.tov AS tov,
        {pf_expr},
        a.ts_pct AS ts_pct,
        a.deflections AS deflections,
        a.off_reb AS oreb,
        a.def_reb AS dreb
      FROM player_stats_basic AS b
      INNER JOIN player_stats_advanced AS a
        ON a.season = b.season
       AND a.player_id = b.player_id
       AND a.team_id = b.team_id
      WHERE b.season = '2024-25'
    """
    prev = con.row_factory
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(sql)
        return [dict(r) for r in cur.fetchall()]
    finally:
        con.row_factory = prev


def compute_row(
    row: dict[str, Any],
) -> tuple[int, float, int, float, float, float | None] | None:
    """Returns (base_pr, mpg, gp, final_val, pts, ts_pct_raw) or None if gp <= 0."""
    gp = fint(row.get("gp")) or 0
    if gp <= 0:
        return None

    mpg_pg = ffloat(row.get("mpg_pg"))
    tm = fint(row.get("total_minutes"))
    min_tot = season_minutes(mpg_pg, gp, tm)
    if min_tot is None or min_tot <= 0:
        mpg = 0.0
    else:
        mpg = min_tot / float(gp)

    pts = ffloat(row.get("pts")) or 0.0
    ast = ffloat(row.get("ast")) or 0.0
    oreb = ffloat(row.get("oreb")) or 0.0
    dreb = ffloat(row.get("dreb")) or 0.0
    stl = ffloat(row.get("stl")) or 0.0
    blk = ffloat(row.get("blk")) or 0.0
    pf = ffloat(row.get("pf"))
    tov = ffloat(row.get("tov"))
    tov = 0.0 if tov is None else tov
    dfl = ffloat(row.get("deflections"))
    dfl = 0.0 if dfl is None else dfl

    ts_raw = ffloat(row.get("ts_pct"))
    ts_use = ERA_AVG_TS if ts_raw is None or ts_raw <= 0 else ts_raw

    pts_score = pts + (pts * (ts_use - ERA_AVG_TS))
    playmaking_score = ast * ((ast / (tov + 1.0)) ** 0.5)
    trb_score = (dreb + (oreb * OREB_WEIGHT)) * TRB_COEF
    def_score = (stl + blk + dfl) * DEF_COEF
    rim_bonus = float(RIM_BONUS) if (pf is not None and blk > pf) else 0.0

    raw_box = pts_score + playmaking_score + trb_score + def_score + rim_bonus

    mpg_mod = mpg_tier_modifier(mpg)
    gp_mod = float(gp) / GP_SEASON
    final_val = raw_box * mpg_mod * gp_mod
    base_pr = int(round(final_val))
    return base_pr, mpg, gp, final_val, pts, ts_raw


def print_top_audit(
    title: str,
    rows: list[tuple[str, str, int, float, float, float | None, int]],
) -> None:
    """rows: (player, team, gp, mpg, pts, ts_frac_or_none, base_pr)."""
    col_w = (20, 4, 4, 5, 5, 6, 8)
    header = (
        f"{'Player':<{col_w[0]}} | {'Team':<{col_w[1]}} | {'GP':>{col_w[2]}} | "
        f"{'MPG':>{col_w[3]}} | {'PTS':>{col_w[4]}} | {'TS%':>{col_w[5]}} | "
        f"{'Base PR':>{col_w[6]}}"
    )
    print(title, flush=True)
    print(header, flush=True)
    print("-" * len(header), flush=True)
    for pn, team, gp, mpg, ppts, tsf, pr in rows:
        if tsf is not None and tsf > 0:
            ts_pct_disp = f"{tsf * 100.0:.1f}"
        else:
            ts_pct_disp = "—"
        print(
            f"{pn:<{col_w[0]}} | {team:<{col_w[1]}} | {gp:>{col_w[2]}} | "
            f"{mpg:>{col_w[3]}.1f} | {ppts:>{col_w[4]}.1f} | {ts_pct_disp:>{col_w[5]}} | "
            f"{pr:>{col_w[6]}}",
            flush=True,
        )
    print(flush=True)


def print_special_audit(
    report_by_name: dict[str, tuple[str, str, int, float, int]],
) -> None:
    """Maps normalized player_name -> (canonical_name, team, gp, mpg, base_pr)."""
    print(
        "Special audit — Curry vs Rockets (Sengun, Thompson): "
        "can elite efficiency offset higher opponent GP under gp/82?",
        flush=True,
    )
    print("-" * 76, flush=True)
    triple: list[tuple[str, str, int, float, int]] = []
    missing: list[str] = []
    for want in SPECIAL_AUDIT_PLAYERS:
        key = want.strip().lower()
        row = report_by_name.get(key)
        if row is None:
            missing.append(want)
            continue
        _, team, gp, mpg, pr = row
        triple.append((want, team, gp, mpg, pr))
    triple.sort(key=lambda x: -x[4])
    for want, team, gp, mpg, pr in triple:
        print(
            f"  {want} | {team} | GP={gp} | MPG={mpg:.1f} | base_pr={pr}",
            flush=True,
        )
    for w in missing:
        print(f"  (not found in 2024-25 join) {w}", flush=True)
    if triple:
        print(
            "Sorted by base_pr. gp_mod = gp/82 penalizes missed games; TS edge adds "
            "pts * (ts_pct - era_avg_ts) on top of raw points.",
            flush=True,
        )
    print(flush=True)


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        ensure_table(con)
        raw_rows = load_rows(con)
        if not raw_rows:
            print(
                "No player rows for season 2024-25 (basic + advanced join).",
                flush=True,
            )
            return

        if "pf" not in pragma_columns(con, "player_stats_basic"):
            print(
                "[calculate_base_pr] No `pf` on player_stats_basic — "
                "rim bonus (blk > pf) disabled.",
                flush=True,
            )

        con.execute(
            "DELETE FROM player_simulation_pr WHERE season = ?",
            (TARGET_SEASON,),
        )

        rows_out: list[tuple[str, str, str, int]] = []
        report: list[
            tuple[str, str, int, float, int, float, float, float | None]
        ] = []
        by_norm: dict[str, tuple[str, str, int, float, int]] = {}

        for row in raw_rows:
            pn = row.get("player_name")
            team = row.get("team") or ""
            if pn is None:
                continue
            pn = str(pn).strip()
            out = compute_row(row)
            if out is None:
                continue
            base_pr, mpg, gp, final_val, pts, ts_raw = out
            team_s = str(team).strip()
            rows_out.append((pn, team_s, TARGET_SEASON, base_pr))
            report.append(
                (pn, team_s, gp, mpg, base_pr, final_val, pts, ts_raw)
            )
            by_norm[pn.lower()] = (pn, team_s, gp, mpg, base_pr)

        con.executemany(
            """
            INSERT INTO player_simulation_pr
                (player_name, team, season, base_pr)
            VALUES (?, ?, ?, ?);
            """,
            rows_out,
        )
        con.commit()

        top = sorted(report, key=lambda x: (-x[4], -x[5]))[:TOP_N]
        print_top_audit(
            f"Top {TOP_N} — by rounded base_pr",
            [(r[0], r[1], r[2], r[3], r[6], r[7], r[4]) for r in top],
        )
        print_special_audit(by_norm)
    finally:
        con.close()


if __name__ == "__main__":
    main()
