"""
calculate_player_pr.py
----------------------
Predictive Monte Carlo **Base PR** (v2): efficiency / value-added composite for
the injury simulator. **No** games-played penalties or GP scaling — only
per-game rates scaled to a **30 MPG** workload.

Reads: player_stats_basic, player_stats_advanced (inner join).
Writes **only**: ``player_simulation_pr`` for ``TARGET_SEASON`` (delete + replace).

Per-game pillars (``era_avg_ts`` = 0.58):
  pts_score  = (ts_pct / era_avg_ts) * pts
  ast_score  = ast + ((ast / max(tov, 1.0)) * 2.5)
  def_score  = ((stl + blk) * 3.0) + deflections
  reb_score  = (dreb + (oreb * 3.0)) * 0.7

  raw_impact = pts_score + ast_score + def_score + reb_score
  base_pr    = round(raw_impact * (mpg / 30.0))
"""

from __future__ import annotations

import math
import os
import sqlite3
import sys
from typing import Any

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

TARGET_SEASON = "2024-25"

ERA_AVG_TS = 0.58
TOP_N = 40

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


def season_minutes(total_minutes: int | None, mpg_field: float | None, gp: int) -> float | None:
    """Total season minutes (``min``); ``mpg`` = min / gp."""
    if total_minutes is not None and total_minutes > 0:
        return float(total_minutes)
    if mpg_field is not None and mpg_field > 0 and gp > 0:
        return mpg_field * float(gp)
    return None


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
    sql = """
      SELECT
        b.player_name AS player_name,
        b.team_abbr AS team,
        b.season AS season,
        b.gp AS gp,
        b.total_minutes AS min,
        b.mpg AS mpg_field,
        b.pts AS pts,
        b.ast AS ast,
        b.stl AS stl,
        b.blk AS blk,
        b.tov AS tov,
        a.ts_pct AS ts_pct,
        a.deflections AS deflections,
        a.off_reb AS oreb,
        a.def_reb AS dreb
      FROM player_stats_basic AS b
      INNER JOIN player_stats_advanced AS a
        ON a.season = b.season
       AND a.player_id = b.player_id
       AND a.team_id = b.team_id
      WHERE b.season = ?
    """
    prev = con.row_factory
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(sql, (TARGET_SEASON,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        con.row_factory = prev


def compute_row(row: dict[str, Any]) -> tuple[int, float, float, float] | None:
    """Returns (base_pr, mpg, pts_score, def_score) or None."""
    gp = fint(row.get("gp")) or 0
    if gp <= 0:
        return None

    min_tot = season_minutes(
        fint(row.get("min")),
        ffloat(row.get("mpg_field")),
        gp,
    )
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
    tov = ffloat(row.get("tov"))
    tov = 0.0 if tov is None else tov

    dfl = ffloat(row.get("deflections"))
    dfl = 0.0 if dfl is None else dfl

    ts_raw = ffloat(row.get("ts_pct"))
    ts_eff = ERA_AVG_TS if ts_raw is None or ts_raw <= 0 else ts_raw

    pts_score = (ts_eff / ERA_AVG_TS) * pts
    ast_score = ast + ((ast / max(tov, 1.0)) * 2.5)
    def_score = ((stl + blk) * 3.0) + dfl
    reb_score = (dreb + (oreb * 3.0)) * 0.7

    raw_impact = pts_score + ast_score + def_score + reb_score
    base_pr = int(round(raw_impact * (mpg / 30.0)))

    return base_pr, mpg, pts_score, def_score


def print_top_audit(rows: list[tuple[str, str, float, float, float, int]]) -> None:
    """rows: (player, team, mpg, pts_val, def_val, base_pr)."""
    col_w = (22, 5, 6, 8, 8, 8)
    header = (
        f"{'Player':<{col_w[0]}} | {'Team':<{col_w[1]}} | "
        f"{'MPG':>{col_w[2]}} | {'Pts_Val':>{col_w[3]}} | "
        f"{'Def_Val':>{col_w[4]}} | {'Base PR':>{col_w[5]}}"
    )
    print(
        f"Top {TOP_N} by Base PR ({TARGET_SEASON}) v2 — per-game raw × (MPG/30)",
        flush=True,
    )
    print(header, flush=True)
    print("-" * len(header), flush=True)
    for pn, tm, mpg, pv, dv, pr in rows:
        print(
            f"{pn:<{col_w[0]}} | {tm:<{col_w[1]}} | {mpg:>{col_w[2]}.1f} | "
            f"{pv:>{col_w[3]}.1f} | {dv:>{col_w[4]}.1f} | {pr:>{col_w[5]}}",
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
                f"No player rows for season {TARGET_SEASON} (basic + advanced join).",
                flush=True,
            )
            return

        con.execute(
            "DELETE FROM player_simulation_pr WHERE season = ?",
            (TARGET_SEASON,),
        )

        rows_out: list[tuple[str, str, str, int, int, float]] = []
        report: list[tuple[str, str, float, float, float, int]] = []

        for row in raw_rows:
            pn = row.get("player_name")
            team = row.get("team") or ""
            if pn is None:
                continue
            pn = str(pn).strip()
            out = compute_row(row)
            if out is None:
                continue
            base_pr, mpg, pts_score, def_score = out
            gp = fint(row.get("gp")) or 0
            team_s = str(team).strip()
            rows_out.append((pn, team_s, TARGET_SEASON, base_pr, gp, mpg))
            report.append((pn, team_s, mpg, pts_score, def_score, base_pr))

        con.executemany(
            """
            INSERT INTO player_simulation_pr
                (player_name, team, season, base_pr, gp, mpg)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            rows_out,
        )
        con.commit()

        report.sort(key=lambda x: (-x[5], -x[2], x[0]))
        print_top_audit(report[:TOP_N])
    finally:
        con.close()


if __name__ == "__main__":
    main()
