"""
cleanup_team_simulation_pr.py
-----------------------------
One-off cleanup: remove *-Projected rows from team_simulation_pr and drop
coach_grade / projected_team_pr columns; verify schema and row count.
"""

from __future__ import annotations

import os
import sqlite3
import sys

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

ORIGINAL_COLUMNS = (
    "team",
    "season",
    "base_team_pr",
    "coach_multiplier",
    "adjusted_team_pr",
)


def pragma_column_names(con: sqlite3.Connection) -> list[str]:
    cur = con.execute("PRAGMA table_info(team_simulation_pr)")
    return [r[1] for r in cur.fetchall()]


def rebuild_table_without_extra_columns(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE _team_simulation_pr_cleanup (
            team             TEXT    NOT NULL,
            season           TEXT    NOT NULL,
            base_team_pr     REAL    NOT NULL,
            coach_multiplier REAL,
            adjusted_team_pr REAL,
            PRIMARY KEY (team, season)
        );
        INSERT INTO _team_simulation_pr_cleanup
            (team, season, base_team_pr, coach_multiplier, adjusted_team_pr)
        SELECT team, season, base_team_pr, coach_multiplier, adjusted_team_pr
        FROM team_simulation_pr;
        DROP TABLE team_simulation_pr;
        ALTER TABLE _team_simulation_pr_cleanup RENAME TO team_simulation_pr;
        """
    )


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='team_simulation_pr'"
        )
        if cur.fetchone() is None:
            print("team_simulation_pr not found.", flush=True)
            return

        n_proj = con.execute(
            "SELECT COUNT(*) FROM team_simulation_pr WHERE season LIKE '%Projected%'"
        ).fetchone()[0]
        con.execute("DELETE FROM team_simulation_pr WHERE season LIKE '%Projected%';")
        con.commit()
        print(f"Deleted {n_proj} row(s) where season LIKE '%Projected%'.", flush=True)

        cols_before = set(pragma_column_names(con))
        try:
            if "coach_grade" in cols_before:
                con.execute("ALTER TABLE team_simulation_pr DROP COLUMN coach_grade;")
            if "projected_team_pr" in pragma_column_names(con):
                con.execute("ALTER TABLE team_simulation_pr DROP COLUMN projected_team_pr;")
            con.commit()
            print("Dropped coach_grade and projected_team_pr via ALTER TABLE DROP COLUMN.", flush=True)
        except sqlite3.OperationalError as e:
            con.rollback()
            print(f"DROP COLUMN failed ({e!r}); rebuilding table.", flush=True)
            rebuild_table_without_extra_columns(con)
            con.commit()
            print("Rebuilt team_simulation_pr with original 5 columns.", flush=True)

        names = pragma_column_names(con)
        n_rows = con.execute("SELECT COUNT(*) FROM team_simulation_pr").fetchone()[0]

        print("", flush=True)
        print("team_simulation_pr columns:", flush=True)
        for i, name in enumerate(names, start=1):
            print(f"  {i}. {name}", flush=True)

        print("", flush=True)
        if names == list(ORIGINAL_COLUMNS):
            print("Schema OK: exactly the original 5 columns.", flush=True)
        else:
            print(f"WARNING: expected {list(ORIGINAL_COLUMNS)}, got {names}", flush=True)

        print(f"Total rows: {n_rows}", flush=True)
        proj_left = con.execute(
            "SELECT COUNT(*) FROM team_simulation_pr WHERE season LIKE '%Projected%'"
        ).fetchone()[0]
        print(f"Rows with season LIKE '%Projected%': {proj_left}", flush=True)
    finally:
        con.close()


if __name__ == "__main__":
    main()
