"""
apply_playoff_experience_pr.py
--------------------------------
Applies a postseason experience multiplier to ``player_projected_pr.projected_pr``
for a target season, keyed by each player's team and that team's ``playoff_result``.

Reads: player_projected_pr, team_stats_playoffs (LEFT JOIN on team / team_abbr).
Writes: ``player_experience_pr`` — idempotent DELETE for the source season followed by INSERT.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from typing import Any

from season_utils import SeasonPair, parse_cli_seasons

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

# Exact playoff_result strings -> multiplier
PLAYOFF_EXPERIENCE_MULTIPLIER: dict[str, float] = {
    "Champion": 1.15,
    "Finals": 1.12,
    "Conf. Finals": 1.08,
    "Conf. Semifinals": 1.04,
    "1st Round": 1.02,
    "Play-In Eliminated": 1.00,
    "Missed Playoffs": 0.98,
}

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def ffloat(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def multiplier_for(playoff_result: str | None, had_team_row: bool) -> float:
    if not had_team_row:
        return PLAYOFF_EXPERIENCE_MULTIPLIER["Missed Playoffs"]
    if playoff_result is None:
        raise ValueError("team_stats_playoffs row present but playoff_result is NULL")
    if playoff_result not in PLAYOFF_EXPERIENCE_MULTIPLIER:
        raise ValueError(
            f"Unknown playoff_result {playoff_result!r}; expected one of "
            f"{sorted(PLAYOFF_EXPERIENCE_MULTIPLIER)!r}"
        )
    return PLAYOFF_EXPERIENCE_MULTIPLIER[playoff_result]


CREATE_EXPERIENCE_PR = """
CREATE TABLE IF NOT EXISTS player_experience_pr (
    player_name     TEXT    NOT NULL,
    season          TEXT    NOT NULL,
    adjusted_exp_pr REAL    NOT NULL,
    PRIMARY KEY (player_name, season)
);
"""


def ensure_table(con: sqlite3.Connection) -> None:
    exists = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='player_experience_pr'"
    ).fetchone()
    if exists is not None:
        types = {
            str(row[1]): str(row[2] or "").upper()
            for row in con.execute("PRAGMA table_info(player_experience_pr)")
        }
        if types.get("adjusted_exp_pr", "").startswith("INT"):
            old_cols = [
                str(row[1])
                for row in con.execute("PRAGMA table_info(player_experience_pr)")
            ]
            con.execute(
                "ALTER TABLE player_experience_pr RENAME TO player_experience_pr__old"
            )
            con.execute(CREATE_EXPERIENCE_PR.replace("IF NOT EXISTS ", ""))
            new_cols = [
                str(row[1])
                for row in con.execute("PRAGMA table_info(player_experience_pr)")
            ]
            shared = [col for col in new_cols if col in old_cols]
            col_sql = ", ".join(shared)
            con.execute(
                f"INSERT INTO player_experience_pr ({col_sql}) "
                f"SELECT {col_sql} FROM player_experience_pr__old"
            )
            con.execute("DROP TABLE player_experience_pr__old")
            con.commit()
    con.execute(CREATE_EXPERIENCE_PR)


def main(source_season: str | None = None, target_season: str | None = None) -> None:
    if source_season is None or target_season is None:
        pair: SeasonPair = parse_cli_seasons()
        source_season = pair.source
        target_season = pair.target
    _ = target_season

    con = sqlite3.connect(DB_PATH)
    try:
        ensure_table(con)

        cur = con.execute(
            """
            SELECT
                p.player_name,
                p.projected_pr,
                t.playoff_result,
                t.team_abbr AS joined_team
            FROM player_projected_pr AS p
            LEFT JOIN team_stats_playoffs AS t
              ON t.team_abbr = p.team
             AND t.season = p.season
            WHERE p.season = ?
            ORDER BY p.player_name;
            """,
            (source_season,),
        )
        rows = cur.fetchall()

        out: list[tuple[str, str, float]] = []
        for player_name, projected_pr, playoff_result, joined_team in rows:
            pr = ffloat(projected_pr)
            if pr is None:
                raise ValueError(f"Invalid projected_pr for {player_name!r}: {projected_pr!r}")
            had_team_row = joined_team is not None
            mult = multiplier_for(playoff_result, had_team_row)
            adjusted = pr * mult
            out.append((player_name, source_season, adjusted))

        con.execute("BEGIN")
        con.execute(
            "DELETE FROM player_experience_pr WHERE season = ?;",
            (source_season,),
        )
        con.executemany(
            """
            INSERT INTO player_experience_pr (player_name, season, adjusted_exp_pr)
            VALUES (?, ?, ?);
            """,
            out,
        )
        con.commit()
        print(
            f"  [ok] player_experience_pr: wrote {len(out)} rows for season {source_season}.",
            flush=True,
        )
    finally:
        con.close()


if __name__ == "__main__":
    main()
