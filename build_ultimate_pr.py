"""
build_ultimate_pr.py
--------------------
Merges veteran final_simulation_pr and rookie_projection into a single
ULTIMATE_PR table in nba_data.db.

Creates ULTIMATE_PR if missing, deletes rows for the target season, then
repopulates via one INSERT ... SELECT ... UNION ALL SELECT ... statement.

Does not modify any existing table other than creating/truncating ULTIMATE_PR
rows for the requested target season.
"""

from __future__ import annotations

import os
import sqlite3
import sys

from season_utils import SeasonPair, parse_cli_seasons

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

CREATE_ULTIMATE_PR = """
CREATE TABLE IF NOT EXISTS ULTIMATE_PR (
    player_name     TEXT NOT NULL,
    season          TEXT NOT NULL,
    pr              REAL,
    player_type     TEXT,
    applied_effects TEXT,
    mapped_position TEXT,
    PRIMARY KEY (player_name, season)
);
"""


def main(source_season: str | None = None, target_season: str | None = None) -> None:
    if source_season is None or target_season is None:
        pair: SeasonPair = parse_cli_seasons()
        source_season = pair.source
        target_season = pair.target

    merge_sql = """
    INSERT INTO ULTIMATE_PR (player_name, season, pr, player_type, applied_effects)
    SELECT
        player_name,
        ? AS season,
        CAST(final_pr AS REAL) AS pr,
        'Veteran' AS player_type,
        applied_effects
    FROM final_simulation_pr
    WHERE season = ?
    UNION ALL
    SELECT
        player_name,
        ? AS season,
        rookie_pr AS pr,
        'Rookie' AS player_type,
        'None' AS applied_effects
    FROM rookie_projection
    WHERE season = ?
    """

    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.cursor()
        cur.execute(CREATE_ULTIMATE_PR)
        saved_positions = cur.execute(
            """
            SELECT player_name, mapped_position
            FROM ULTIMATE_PR
            WHERE season = ?
              AND mapped_position IS NOT NULL
              AND TRIM(mapped_position) != ''
            """,
            (target_season,),
        ).fetchall()
        cur.execute("DELETE FROM ULTIMATE_PR WHERE season = ?;", (target_season,))
        cur.execute(
            merge_sql,
            (target_season, source_season, target_season, target_season),
        )
        for player_name, mapped_position in saved_positions:
            cur.execute(
                """
                UPDATE ULTIMATE_PR
                SET mapped_position = ?
                WHERE player_name = ? AND season = ?
                """,
                (mapped_position, player_name, target_season),
            )
        cur.execute(
            """
            UPDATE ULTIMATE_PR
            SET mapped_position = (
                SELECT p.mapped_position
                FROM player_positions p
                WHERE p.player_name = ULTIMATE_PR.player_name
                LIMIT 1
            )
            WHERE season = ?
              AND (mapped_position IS NULL OR TRIM(mapped_position) = '')
              AND EXISTS (
                SELECT 1 FROM player_positions p
                WHERE p.player_name = ULTIMATE_PR.player_name
              )
            """,
            (target_season,),
        )
        con.commit()
        n = cur.execute(
            "SELECT COUNT(*) FROM ULTIMATE_PR WHERE season = ?;",
            (target_season,),
        ).fetchone()[0]
        nv = cur.execute(
            """
            SELECT COUNT(*) FROM ULTIMATE_PR
            WHERE season = ? AND player_type = 'Veteran';
            """,
            (target_season,),
        ).fetchone()[0]
        nr = cur.execute(
            """
            SELECT COUNT(*) FROM ULTIMATE_PR
            WHERE season = ? AND player_type = 'Rookie';
            """,
            (target_season,),
        ).fetchone()[0]
        print(
            f"[build_ultimate_pr] ULTIMATE_PR rows for {target_season!r}: "
            f"{n} (Veteran: {nv}, Rookie: {nr})"
        )
    finally:
        con.close()


if __name__ == "__main__":
    main()
