"""
fetch_team_coaches_25_26.py
----------------------------
Builds opening-night head coaches for Monte Carlo use without
CommonTeamRoster (which reflects *current* staff, not season tip-off).

Pipeline:
  1) Baseline: final coach per team from local coach_data (source season).
  2) Overrides: summer_hires — off-season changes before target opening night.
  3) Grade: coach_system_data (TRIM name match), else default 'C'.

Only team_coaches rows for the target season are created/cleared/repopulated.
"""

from __future__ import annotations

import os
import sqlite3
import sys

from season_utils import SeasonPair, parse_cli_seasons
from leakage_guards import summer_hires_for_target

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")
DEFAULT_GRADE = "C"

CREATE_TEAM_COACHES = """
CREATE TABLE IF NOT EXISTS team_coaches (
    team_abbr   TEXT NOT NULL,
    season      TEXT NOT NULL,
    coach_name  TEXT,
    grade       TEXT,
    PRIMARY KEY (team_abbr, season)
);
"""


def resolve_grade(con: sqlite3.Connection, coach_name: str | None) -> str:
    if not coach_name or not str(coach_name).strip():
        return DEFAULT_GRADE
    row = con.execute(
        """
        SELECT Grade
        FROM coach_system_data
        WHERE TRIM(name) = TRIM(?)
        LIMIT 1
        """,
        (coach_name.strip(),),
    ).fetchone()
    if row is None:
        return DEFAULT_GRADE
    g = row[0]
    if g is None or (isinstance(g, str) and not g.strip()):
        return DEFAULT_GRADE
    return str(g).strip()


def load_baseline(con: sqlite3.Connection, source_season: str) -> list[tuple[str, str | None]]:
    rows = con.execute(
        """
        SELECT team_abbr, coach_name
        FROM coach_data
        WHERE season = ?
        ORDER BY team_abbr
        """,
        (source_season,),
    ).fetchall()
    return [(str(r[0]), r[1]) for r in rows]


def main(source_season: str | None = None, target_season: str | None = None) -> None:
    if source_season is None or target_season is None:
        pair: SeasonPair = parse_cli_seasons()
        source_season = pair.source
        target_season = pair.target

    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(CREATE_TEAM_COACHES)
        con.execute("DELETE FROM team_coaches WHERE season = ?", (target_season,))
        con.commit()

        baseline = load_baseline(con, source_season)
        if len(baseline) != 30:
            print(
                f"[warn] Expected 30 teams from coach_data ({source_season!r}), "
                f"got {len(baseline)}. Proceeding with available rows.",
                flush=True,
            )

        summer_hires = summer_hires_for_target(target_season)

        print(
            f"\n[fetch_team_coaches_25_26] Opening-night build for {target_season!r} "
            f"(baseline {source_season!r}, {len(summer_hires)} summer override(s))\n",
            flush=True,
        )

        for abbr, base_coach in baseline:
            coach_name = summer_hires.get(abbr, base_coach)
            if isinstance(coach_name, str):
                coach_name = coach_name.strip() or None
            grade = resolve_grade(con, coach_name)
            con.execute(
                """
                INSERT INTO team_coaches (team_abbr, season, coach_name, grade)
                VALUES (?, ?, ?, ?)
                """,
                (abbr, target_season, coach_name, grade),
            )
            src = "summer" if abbr in summer_hires else source_season
            label = coach_name or "—"
            print(f"  {abbr:<4}  {label:<36}  grade={grade}  ({src})", flush=True)

        con.commit()
        m = con.execute(
            "SELECT COUNT(*) FROM team_coaches WHERE season = ?",
            (target_season,),
        ).fetchone()[0]
        print(f"\n[done] team_coaches rows for {target_season!r}: {m}", flush=True)
    finally:
        con.close()


if __name__ == "__main__":
    main()
