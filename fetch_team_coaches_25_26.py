"""
fetch_team_coaches_25_26.py
----------------------------
Builds opening-night 2025-26 head coaches for Monte Carlo use without
CommonTeamRoster (which reflects *current* staff, not season tip-off).

Pipeline:
  1) Baseline: final coach per team from local coach_data (season = '2024-25').
  2) Overrides: summer_hires — off-season changes before 2025-26 opening night.
  3) Grade: coach_system_data (TRIM name match), else default 'C'.

Only team_coaches_25_26 is created/cleared/repopulated.
"""

from __future__ import annotations

import os
import sqlite3
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")
BASELINE_SEASON = "2024-25"
DEFAULT_GRADE = "C"

# Off-season head-coach changes after the 2024-25 season ends and before
# 2025-26 opening night. Keys must match coach_data.team_abbr (PHX, BRK→BKN, etc.).
# Uncomment or add entries as news breaks. Teams not listed keep their 2024-25
# coach_name from coach_data.
summer_hires = {
    # --- reference: keep baseline only unless you add a key above ---
    # "ATL": "Coach Name",  # optional override
    # "BKN": "Coach Name",
    # "BOS": "Coach Name",
    # "CHA": "Coach Name",
    # "CHI": "Coach Name",  # 2024-25 baseline: Billy Donovan
    # "CLE": "Coach Name",
    # "DAL": "Coach Name",
    # "DEN": "Coach Name",  # baseline already David Adelman if Malone was replaced mid-year
    # "DET": "Coach Name",
    # "GSW": "Coach Name",
    # "HOU": "Coach Name",
    # "IND": "Coach Name",
    # "LAC": "Coach Name",
    # "LAL": "Coach Name",
    # "MEM": "Coach Name",
    # "MIA": "Coach Name",
    # "MIL": "Coach Name",  # opening night 2025-26 was still Doc Rivers in most DBs; mid-2026 hire is not “summer” 2025
    # "MIN": "Coach Name",
    # "NOP": "Coach Name",  # Willie Green through opening night 2025-26; later interim = in-season, not this dict
    "NYK": "Mike Brown",  # Hired July 2025; replaces Tom Thibodeau on the 2024-25 year-end row
    # "OKC": "Coach Name",
    # "ORL": "Coach Name",  # Jamahl Mosley baseline unless you move him for your sim
    # "PHI": "Coach Name",
    # "PHX": "Coach Name",  # baseline often already Jordan Ott after spring 2025 hire
    # "POR": "Coach Name",  # Chauncey Billups baseline; mid-Oct 2025 events = judgment call for “opening night”
    # "SAC": "Coach Name",
    # "SAS": "Coach Name",
    # "TOR": "Coach Name",
    # "UTA": "Coach Name",
    # "WAS": "Coach Name",
}

CREATE_TEAM_COACHES_25_26 = """
CREATE TABLE IF NOT EXISTS team_coaches_25_26 (
    team_abbr   TEXT PRIMARY KEY,
    coach_name  TEXT,
    grade       TEXT
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


def load_baseline(con: sqlite3.Connection) -> list[tuple[str, str | None]]:
    rows = con.execute(
        """
        SELECT team_abbr, coach_name
        FROM coach_data
        WHERE season = ?
        ORDER BY team_abbr
        """,
        (BASELINE_SEASON,),
    ).fetchall()
    return [(str(r[0]), r[1]) for r in rows]


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(CREATE_TEAM_COACHES_25_26)
        con.execute("DELETE FROM team_coaches_25_26")
        con.commit()

        baseline = load_baseline(con)
        if len(baseline) != 30:
            print(
                f"[warn] Expected 30 teams from coach_data ({BASELINE_SEASON!r}), "
                f"got {len(baseline)}. Proceeding with available rows.",
                flush=True,
            )

        print(
            f"\n[fetch_team_coaches_25_26] Opening-night 2025-26 build "
            f"(baseline {BASELINE_SEASON!r}, {len(summer_hires)} summer override(s))\n",
            flush=True,
        )

        for abbr, base_coach in baseline:
            coach_name = summer_hires.get(abbr, base_coach)
            if isinstance(coach_name, str):
                coach_name = coach_name.strip() or None
            grade = resolve_grade(con, coach_name)
            con.execute(
                """
                INSERT INTO team_coaches_25_26 (team_abbr, coach_name, grade)
                VALUES (?, ?, ?)
                """,
                (abbr, coach_name, grade),
            )
            src = "summer" if abbr in summer_hires else "2024-25"
            label = coach_name or "—"
            print(f"  {abbr:<4}  {label:<36}  grade={grade}  ({src})", flush=True)

        con.commit()
        m = con.execute("SELECT COUNT(*) FROM team_coaches_25_26").fetchone()[0]
        print(f"\n[done] team_coaches_25_26 rows: {m}", flush=True)
    finally:
        con.close()


if __name__ == "__main__":
    main()
