"""
One-time Phase 1a schema migration: rename _25_26 tables, add season columns.

Run once against nba_data.db before using the season-parameterized pipeline.
Idempotent: skips steps when target tables already exist.
"""

from __future__ import annotations

import os
import sqlite3
import sys

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")
TARGET_SEASON = "2025-26"
SOURCE_SEASON = "2024-25"
DEFAULT_RUN_ID = "production"


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _migrate_simulation_results(con: sqlite3.Connection) -> None:
    if _table_exists(con, "simulation_results"):
        print("simulation_results: already exists, skip")
        return
    if not _table_exists(con, "simulation_results_25_26"):
        print("simulation_results_25_26: missing, skip")
        return
    con.execute(
        """
        CREATE TABLE simulation_results (
            team                  TEXT NOT NULL,
            season                TEXT NOT NULL,
            run_id                TEXT NOT NULL DEFAULT 'production',
            avg_wins              REAL NOT NULL,
            seed_1_pct            REAL NOT NULL,
            seed_2_pct            REAL NOT NULL,
            seed_3_pct            REAL NOT NULL,
            seed_4_pct            REAL NOT NULL,
            seed_5_pct            REAL NOT NULL,
            seed_6_pct            REAL NOT NULL,
            seed_7_pct            REAL NOT NULL,
            seed_8_pct            REAL NOT NULL,
            seed_9_pct            REAL NOT NULL,
            seed_10_pct           REAL NOT NULL,
            seed_11_pct           REAL NOT NULL,
            seed_12_pct           REAL NOT NULL,
            seed_13_pct           REAL NOT NULL,
            seed_14_pct           REAL NOT NULL,
            seed_15_pct           REAL NOT NULL,
            missed_playoffs_pct   REAL NOT NULL,
            first_round_pct       REAL NOT NULL,
            second_round_pct      REAL NOT NULL,
            conf_finals_pct       REAL NOT NULL,
            finals_pct            REAL NOT NULL,
            champion_pct          REAL NOT NULL,
            PRIMARY KEY (team, season, run_id)
        )
        """
    )
    con.execute(
        f"""
        INSERT INTO simulation_results (
            team, season, run_id, avg_wins,
            seed_1_pct, seed_2_pct, seed_3_pct, seed_4_pct, seed_5_pct,
            seed_6_pct, seed_7_pct, seed_8_pct, seed_9_pct, seed_10_pct,
            seed_11_pct, seed_12_pct, seed_13_pct, seed_14_pct, seed_15_pct,
            missed_playoffs_pct, first_round_pct, second_round_pct,
            conf_finals_pct, finals_pct, champion_pct
        )
        SELECT
            team, ?, ?, avg_wins,
            seed_1_pct, seed_2_pct, seed_3_pct, seed_4_pct, seed_5_pct,
            seed_6_pct, seed_7_pct, seed_8_pct, seed_9_pct, seed_10_pct,
            seed_11_pct, seed_12_pct, seed_13_pct, seed_14_pct, seed_15_pct,
            missed_playoffs_pct, first_round_pct, second_round_pct,
            conf_finals_pct, finals_pct, champion_pct
        FROM simulation_results_25_26
        """,
        (TARGET_SEASON, DEFAULT_RUN_ID),
    )
    con.execute("DROP TABLE simulation_results_25_26")
    print(f"simulation_results: migrated from simulation_results_25_26 ({TARGET_SEASON})")


def _migrate_team_projection(con: sqlite3.Connection) -> None:
    if _table_exists(con, "team_projection"):
        print("team_projection: already exists, skip")
        return
    if not _table_exists(con, "projected_team_pr_25_26"):
        print("projected_team_pr_25_26: missing, skip")
        return
    con.execute(
        """
        CREATE TABLE team_projection (
            team_abbr         TEXT NOT NULL,
            season            TEXT NOT NULL,
            base_team_pr      REAL NOT NULL,
            coach_grade       TEXT,
            playstyle         TEXT NOT NULL,
            final_team_pr     REAL NOT NULL,
            rotation_players  TEXT NOT NULL,
            PRIMARY KEY (team_abbr, season)
        )
        """
    )
    con.execute(
        """
        INSERT INTO team_projection (
            team_abbr, season, base_team_pr, coach_grade, playstyle,
            final_team_pr, rotation_players
        )
        SELECT team_abbr, ?, base_team_pr, coach_grade, playstyle,
               final_team_pr, rotation_players
        FROM projected_team_pr_25_26
        """,
        (TARGET_SEASON,),
    )
    con.execute("DROP TABLE projected_team_pr_25_26")
    print("team_projection: migrated from projected_team_pr_25_26")


def _migrate_team_playoff_projection(con: sqlite3.Connection) -> None:
    if _table_exists(con, "team_playoff_projection"):
        print("team_playoff_projection: already exists, skip")
        return
    if not _table_exists(con, "team_playoff_pr_25_26"):
        print("team_playoff_pr_25_26: missing, skip")
        return
    con.execute(
        """
        CREATE TABLE team_playoff_projection (
            team                 TEXT NOT NULL,
            season               TEXT NOT NULL,
            base_8man_pr         REAL NOT NULL,
            amplified_coach_mult REAL NOT NULL,
            playstyle_mult       REAL NOT NULL,
            continuity_mult      REAL NOT NULL,
            final_playoff_pr     REAL NOT NULL,
            PRIMARY KEY (team, season)
        )
        """
    )
    con.execute(
        """
        INSERT INTO team_playoff_projection (
            team, season, base_8man_pr, amplified_coach_mult,
            playstyle_mult, continuity_mult, final_playoff_pr
        )
        SELECT team, ?, base_8man_pr, amplified_coach_mult,
               playstyle_mult, continuity_mult, final_playoff_pr
        FROM team_playoff_pr_25_26
        """,
        (TARGET_SEASON,),
    )
    con.execute("DROP TABLE team_playoff_pr_25_26")
    print("team_playoff_projection: migrated from team_playoff_pr_25_26")


def _migrate_rookie_projection(con: sqlite3.Connection) -> None:
    if _table_exists(con, "rookie_projection"):
        print("rookie_projection: already exists, skip")
        return
    if not _table_exists(con, "rookie_projected_pr_25_26"):
        print("rookie_projected_pr_25_26: missing, skip")
        return
    con.execute(
        """
        CREATE TABLE rookie_projection (
            player_name TEXT NOT NULL,
            season      TEXT NOT NULL,
            rookie_pr   REAL NOT NULL,
            PRIMARY KEY (player_name, season)
        )
        """
    )
    con.execute(
        """
        INSERT INTO rookie_projection (player_name, season, rookie_pr)
        SELECT player_name, ?, rookie_pr
        FROM rookie_projected_pr_25_26
        """,
        (TARGET_SEASON,),
    )
    con.execute("DROP TABLE rookie_projected_pr_25_26")
    print("rookie_projection: migrated from rookie_projected_pr_25_26")


def _migrate_team_coaches(con: sqlite3.Connection) -> None:
    if _table_exists(con, "team_coaches") and not _table_exists(con, "team_coaches_25_26"):
        print("team_coaches: already migrated, skip")
        return
    if not _table_exists(con, "team_coaches_25_26"):
        print("team_coaches_25_26: missing, skip")
        return
    if _table_exists(con, "team_coaches"):
        con.execute("DROP TABLE team_coaches")
    con.execute(
        """
        CREATE TABLE team_coaches (
            team_abbr   TEXT NOT NULL,
            season      TEXT NOT NULL,
            coach_name  TEXT,
            grade       TEXT,
            PRIMARY KEY (team_abbr, season)
        )
        """
    )
    con.execute(
        """
        INSERT INTO team_coaches (team_abbr, season, coach_name, grade)
        SELECT team_abbr, ?, coach_name, grade
        FROM team_coaches_25_26
        """,
        (TARGET_SEASON,),
    )
    con.execute("DROP TABLE team_coaches_25_26")
    print("team_coaches: migrated from team_coaches_25_26")


def _add_season_column(
    con: sqlite3.Connection,
    table: str,
    backfill: str,
    pk_cols: tuple[str, ...],
) -> None:
    cols = con.execute(f"PRAGMA table_info({table})").fetchall()
    if any(c[1] == "season" for c in cols):
        print(f"{table}: season column already present, skip")
        return

    data_cols = cols  # include former PK columns; season joins the composite PK
    col_defs: list[str] = []
    for c in data_cols:
        name, ctype, notnull = c[1], c[2], c[3]
        nn = " NOT NULL" if notnull else ""
        col_defs.append(f"{name} {ctype}{nn}")

    pk_sql = ", ".join(pk_cols)
    data_names = [c[1] for c in data_cols]
    insert_cols = data_names + ["season"]
    select_cols = ", ".join(data_names)

    staging = f"{table}_pre1a"
    con.execute(f"ALTER TABLE {table} RENAME TO {staging}")
    con.execute(
        f"""
        CREATE TABLE {table} (
            {", ".join(col_defs)},
            season TEXT NOT NULL,
            PRIMARY KEY ({pk_sql})
        )
        """
    )
    con.execute(
        f"""
        INSERT INTO {table} ({", ".join(insert_cols)})
        SELECT {select_cols}, ?
        FROM {staging}
        """,
        (backfill,),
    )
    con.execute(f"DROP TABLE {staging}")
    print(f"{table}: added season column backfilled with {backfill!r}")


def _harmonize_starting_team_names(con: sqlite3.Connection) -> None:
    """Align player_starting_teams names with ULTIMATE_PR for roster joins."""
    if not _table_exists(con, "player_starting_teams"):
        return
    con.execute(
        """
        UPDATE player_starting_teams
        SET player_name = 'Darius Brown'
        WHERE season = '2025-26'
          AND player_name = 'Darius Brown II'
          AND team_abbr = 'CLE'
        """
    )
    print("player_starting_teams: harmonized Darius Brown II -> Darius Brown for 2025-26")


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        _migrate_simulation_results(con)
        _migrate_team_projection(con)
        _migrate_team_playoff_projection(con)
        _migrate_rookie_projection(con)
        _migrate_team_coaches(con)

        _add_season_column(
            con, "player_experience_pr", SOURCE_SEASON, ("player_name", "season")
        )
        _add_season_column(
            con, "final_simulation_pr", SOURCE_SEASON, ("player_name", "season")
        )
        _add_season_column(
            con, "ULTIMATE_PR", TARGET_SEASON, ("player_name", "season")
        )
        _add_season_column(
            con, "ultimate_playoff_pr", TARGET_SEASON, ("player_name", "season")
        )
        _add_season_column(
            con, "player_durability_profiles", TARGET_SEASON, ("player_name", "season")
        )
        _harmonize_starting_team_names(con)

        con.commit()
        print("\n[migrate_phase1a_schema] Done.")
    finally:
        con.close()


if __name__ == "__main__":
    main()
