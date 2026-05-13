"""
apply_progression.py
--------------------
Applies an age-based multiplier to ``player_simulation_pr.base_pr`` and stores
results in **new** table ``player_projected_pr`` only.

Does **not** alter ``player_simulation_pr`` or any other existing table.
"""

from __future__ import annotations

import math
import os
import sqlite3
import sys
from typing import Any

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

TARGET_SEASON = "2024-25"

TOP_RISERS = 15
TOP_FALLERS = 15

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def aging_multiplier(age_years: int) -> float:
    """Piecewise age curve for projected PR."""
    if age_years <= 21:
        return 1.12
    if age_years <= 24:
        return 1.06
    if age_years <= 28:
        return 1.00
    if age_years <= 31:
        return 0.97
    if age_years <= 34:
        return 0.92
    return 0.85


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
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def ensure_projected_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS player_projected_pr (
            player_name   TEXT    NOT NULL,
            team          TEXT,
            season        TEXT    NOT NULL,
            age           INTEGER NOT NULL,
            base_pr       INTEGER NOT NULL,
            multiplier    REAL    NOT NULL,
            projected_pr  INTEGER NOT NULL,
            PRIMARY KEY (player_name, season)
        );
        """
    )


def load_base_with_age(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """
    base_pr from player_simulation_pr; age from player_stats_basic for the
    highest-minute stint when a player has multiple team rows.
    """
    sql = """
      SELECT
        p.player_name AS player_name,
        p.team AS team,
        p.season AS season,
        CAST(p.base_pr AS INTEGER) AS base_pr,
        (
          SELECT b.age
          FROM player_stats_basic AS b
          WHERE b.season = p.season
            AND b.player_name = p.player_name
          ORDER BY COALESCE(b.total_minutes, 0) DESC, b.team_id
          LIMIT 1
        ) AS age
      FROM player_simulation_pr AS p
      WHERE p.season = ?
    """
    prev = con.row_factory
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(sql, (TARGET_SEASON,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        con.row_factory = prev


def print_table(
    title: str,
    rows: list[tuple[str, int, int, float, int]],
) -> None:
    """rows: (player_name, age, base_pr, multiplier, projected_pr)."""
    cw = (22, 4, 8, 11, 12)
    hdr = (
        f"{'Player':<{cw[0]}} | {'Age':>{cw[1]}} | "
        f"{'Base PR':>{cw[2]}} | {'Multiplier':>{cw[3]}} | "
        f"{'Projected PR':>{cw[4]}}"
    )
    print(title, flush=True)
    print(hdr, flush=True)
    print("-" * len(hdr), flush=True)
    for name, age, base, mult, proj in rows:
        print(
            f"{name:<{cw[0]}} | {age:>{cw[1]}} | {base:>{cw[2]}} | "
            f"{mult:>{cw[3]}.2f} | {proj:>{cw[4]}}",
            flush=True,
        )
    print(flush=True)


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        ensure_projected_table(con)

        raw = load_base_with_age(con)
        if not raw:
            print(f"No rows in player_simulation_pr for season {TARGET_SEASON}.", flush=True)
            return

        missing_age = 0
        out_rows: list[
            tuple[str, str, str, int, int, float, int, int]
        ] = []  # + delta for sort

        for row in raw:
            name = row.get("player_name")
            if name is None:
                continue
            name_s = str(name).strip()
            team = str(row.get("team") or "").strip()
            base = fint(row.get("base_pr"))
            if base is None:
                continue

            age_i = fint(row.get("age"))
            if age_i is None:
                missing_age += 1
                continue

            mult = aging_multiplier(age_i)
            projected = int(round(base * mult))
            delta = projected - base
            out_rows.append((name_s, team, TARGET_SEASON, age_i, base, mult, projected, delta))

        if not out_rows:
            print("No rows could be built (missing age on all players?).", flush=True)
            return

        con.execute(
            "DELETE FROM player_projected_pr WHERE season = ?",
            (TARGET_SEASON,),
        )
        con.executemany(
            """
            INSERT INTO player_projected_pr
                (player_name, team, season, age, base_pr, multiplier, projected_pr)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            [(a[0], a[1], a[2], a[3], a[4], a[5], a[6]) for a in out_rows],
        )
        con.commit()

        if missing_age:
            print(
                f"[warn] Skipped {missing_age} player(s) with no age in player_stats_basic.",
                flush=True,
            )

        risers = sorted(out_rows, key=lambda x: (-x[7], -x[6], x[0]))[:TOP_RISERS]
        fallers = sorted(out_rows, key=lambda x: (x[7], x[6], x[0]))[:TOP_FALLERS]

        print_table(
            f"Top {TOP_RISERS} risers (projected_pr − base_pr), {TARGET_SEASON}",
            [(r[0], r[3], r[4], r[5], r[6]) for r in risers],
        )
        print_table(
            f"Top {TOP_FALLERS} fallers (projected_pr − base_pr), {TARGET_SEASON}",
            [(r[0], r[3], r[4], r[5], r[6]) for r in fallers],
        )
    finally:
        con.close()


if __name__ == "__main__":
    main()
