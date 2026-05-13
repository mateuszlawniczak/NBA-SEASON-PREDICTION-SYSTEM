"""
apply_coach_multipliers.py
--------------------------
Applies coaching grade multipliers to team_simulation_pr without changing base_team_pr.

Reads: team_simulation_pr, coach_data (team + season → coach_name), coach_system_data (name → Grade).
Writes: coach_multiplier, adjusted_team_pr on team_simulation_pr.

Missing or unknown grades use multiplier 1.00 (baseline).
"""

from __future__ import annotations

import os
import sqlite3
import sys

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

GRADE_MULTIPLIER: dict[str, float] = {
    "S": 1.08,
    "A": 1.04,
    "B": 1.02,
    "C": 1.00,
    "D": 0.97,
    "F": 0.95,
}

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def pragma_columns(con: sqlite3.Connection, table: str) -> set[str]:
    cur = con.execute(f"PRAGMA table_info({table})")
    return {r[1] for r in cur.fetchall()}


def ensure_team_simulation_pr_columns(con: sqlite3.Connection) -> None:
    cols = pragma_columns(con, "team_simulation_pr")
    if "coach_multiplier" not in cols:
        con.execute(
            "ALTER TABLE team_simulation_pr ADD COLUMN coach_multiplier REAL;"
        )
    if "adjusted_team_pr" not in cols:
        con.execute(
            "ALTER TABLE team_simulation_pr ADD COLUMN adjusted_team_pr REAL;"
        )


def normalize_grade_letter(g: object | None) -> str | None:
    if g is None:
        return None
    s = str(g).strip().upper()
    if not s:
        return None
    if s not in GRADE_MULTIPLIER:
        return None
    return s


def multiplier_for_grade_raw(g: object | None) -> float:
    letter = normalize_grade_letter(g)
    if letter is None:
        return 1.00
    return GRADE_MULTIPLIER[letter]


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            ("team_simulation_pr",),
        )
        if cur.fetchone() is None:
            print("Missing team_simulation_pr. Run calculate_team_pr_base.py first.", flush=True)
            return

        for tbl in ("coach_data", "coach_system_data"):
            cur = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (tbl,),
            )
            if cur.fetchone() is None:
                print(f"Missing {tbl}. Populate coach pipeline first.", flush=True)
                return

        ensure_team_simulation_pr_columns(con)

        sql = """
            SELECT
                t.team,
                t.season,
                t.base_team_pr,
                cs.Grade
            FROM team_simulation_pr AS t
            LEFT JOIN coach_data AS cd
              ON cd.season = t.season
             AND cd.team_abbr = t.team
            LEFT JOIN coach_system_data AS cs
              ON TRIM(cs.name) = TRIM(cd.coach_name)
        """
        pr_rows = con.execute(sql).fetchall()

        updates: list[tuple[float, float, str, str]] = []
        report: list[tuple[str, str, float, str, float, float]] = []
        for team, season, base_raw, grade_raw in pr_rows:
            key_t = str(team).strip().upper()
            key_s = str(season).strip()
            mult = multiplier_for_grade_raw(grade_raw)
            letter = normalize_grade_letter(grade_raw)
            grade_show = letter if letter is not None else "—"
            try:
                base = float(base_raw)
            except (TypeError, ValueError):
                base = 0.0
            adj = base * mult
            updates.append((mult, adj, str(team).strip(), str(season).strip()))
            report.append((key_t, key_s, base, grade_show, mult, adj))

        con.executemany(
            """
            UPDATE team_simulation_pr
               SET coach_multiplier = ?,
                   adjusted_team_pr = ?
             WHERE team = ? AND season = ?;
            """,
            updates,
        )
        con.commit()

        latest = con.execute("SELECT MAX(season) FROM team_simulation_pr").fetchone()[0]
        if latest is None:
            print("No rows in team_simulation_pr.", flush=True)
            return
        latest = str(latest).strip()

        latest_rows = [r for r in report if r[1] == latest]
        latest_rows.sort(key=lambda x: -x[5])
        top = latest_rows[:5]
        bottom = (
            list(reversed(latest_rows[-5:]))
            if len(latest_rows) >= 5
            else list(reversed(latest_rows))
        )

        col_team_w, col_base_w, col_grade_w, col_mult_w, col_adj_w = 22, 12, 13, 12, 14
        sep_len = col_team_w + col_base_w + col_grade_w + col_mult_w + col_adj_w + 16
        sep = "-" * sep_len

        def line(team: str, base: float, grade: str, mult: float, adj: float) -> str:
            return (
                f"  {team:<{col_team_w}} | {base:>{col_base_w}.2f} | "
                f"{grade:^{col_grade_w}} | {mult:>{col_mult_w}.2f} | {adj:>{col_adj_w}.2f}"
            )

        print("", flush=True)
        print(
            f"  Coaching impact — season {latest} "
            f"(grades from coach_system_data via coach_data)",
            flush=True,
        )
        print(sep, flush=True)
        print(
            f"  {'Team':<{col_team_w}} | {'Base PR':>{col_base_w}} | "
            f"{'Coach Grade':^{col_grade_w}} | {'Multiplier':>{col_mult_w}} | "
            f"{'Adj. PR':>{col_adj_w}}",
            flush=True,
        )
        print(sep, flush=True)
        print("  Top 5:", flush=True)
        for t, _sn, base, grade, mult, adj in top:
            print(line(t, base, grade, mult, adj), flush=True)
        print("", flush=True)
        print("  Bottom 5:", flush=True)
        for t, _sn, base, grade, mult, adj in bottom:
            print(line(t, base, grade, mult, adj), flush=True)
        print(sep, flush=True)
    finally:
        con.close()


if __name__ == "__main__":
    main()
