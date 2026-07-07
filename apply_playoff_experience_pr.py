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


def fint(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(round(float(v)))
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


def ensure_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS player_experience_pr (
            player_name     TEXT    NOT NULL,
            season          TEXT    NOT NULL,
            adjusted_exp_pr INTEGER NOT NULL,
            PRIMARY KEY (player_name, season)
        );
        """
    )


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

        out: list[tuple[str, str, int]] = []
        for player_name, projected_pr, playoff_result, joined_team in rows:
            pr = fint(projected_pr)
            if pr is None:
                raise ValueError(f"Invalid projected_pr for {player_name!r}: {projected_pr!r}")
            had_team_row = joined_team is not None
            mult = multiplier_for(playoff_result, had_team_row)
            adjusted = int(round(pr * mult))
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
