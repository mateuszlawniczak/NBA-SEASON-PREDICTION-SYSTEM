"""
build_team_playoff_pr_25_26.py
-------------------------------
8-man playoff rotation using ``ultimate_playoff_pr.playoff_pr`` and positional
draft rules, then coach / playstyle / continuity multipliers.

Writes ONLY ``team_playoff_pr_25_26`` in ``nba_data.db`` (replaced each run).

Reads:
  ultimate_playoff_pr, player_starting_teams_25_26, coach_system_data,
  playstyle_multipliers,

plus (required to attach coaches and playstyles to teams — not present in the
four named tables alone):

  team_coaches_25_26, team_playstyle_data (season ``2024-25``).
"""

from __future__ import annotations

import os
import sqlite3
import sys

import pandas as pd

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

GRADE_TO_COACH_MULT: dict[str, float] = {
    "S": 1.08,
    "A": 1.04,
    "B": 1.02,
    "C": 1.00,
    "D": 0.97,
    "F": 0.95,
}

DEFAULT_COACH_MULT = 1.00
DEFAULT_PLAYSTYLE_MULT = 1.00
PLAYSTYLE_SEASON = "2024-25"

STAR_BOOST = 1.15
COACH_AMPLIFY = 1.5

CONTINUITY_HIGH = 1.05
CONTINUITY_LOW = 0.95
CONTINUITY_DEFAULT = 1.00

HIGH_CONTINUITY_TEAMS = frozenset({"BOS", "DEN", "OKC", "NYK", "MIN", "IND", "ORL", "SAC"})
LOW_CONTINUITY_TEAMS = frozenset({"PHI", "DAL", "SAS", "CHI", "DET"})

REQUIRED_TABLES = (
    "ultimate_playoff_pr",
    "player_starting_teams_25_26",
    "coach_system_data",
    "playstyle_multipliers",
    "team_coaches_25_26",
    "team_playstyle_data",
)


def _tables_missing(con: sqlite3.Connection) -> list[str]:
    missing: list[str] = []
    for t in REQUIRED_TABLES:
        row = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (t,),
        ).fetchone()
        if row is None:
            missing.append(t)
    return missing


def _normalize_grade(g: object | None) -> str | None:
    if g is None or (isinstance(g, float) and pd.isna(g)):
        return None
    s = str(g).strip().upper()
    return s if s in GRADE_TO_COACH_MULT else None


def _coach_mult_from_grade(g: object | None) -> float:
    letter = _normalize_grade(g)
    if letter is None:
        return DEFAULT_COACH_MULT
    return GRADE_TO_COACH_MULT[letter]


def _continuity_mult(team_abbr: str) -> float:
    t = str(team_abbr).strip().upper()
    if t in HIGH_CONTINUITY_TEAMS:
        return CONTINUITY_HIGH
    if t in LOW_CONTINUITY_TEAMS:
        return CONTINUITY_LOW
    return CONTINUITY_DEFAULT


def _draft_eight_playoff_pr(roster: pd.DataFrame) -> float:
    """Return base_8man_pr for one team (star-power premium applied)."""
    r = roster.drop_duplicates(subset=["player_name"], keep="first").copy()
    r["mapped_position"] = (
        r["mapped_position"].astype(str).str.strip().str.upper()
    )
    r["playoff_pr"] = pd.to_numeric(r["playoff_pr"], errors="coerce").fillna(0.0)
    r = r.sort_values(
        ["playoff_pr", "player_name"],
        ascending=[False, True],
        kind="mergesort",
        ignore_index=True,
    )

    buckets = {p: r[r["mapped_position"] == p].to_dict("records") for p in ("G", "F", "C")}
    g_list, f_list, c_list = buckets["G"], buckets["F"], buckets["C"]

    drafted: list[dict] = []
    drafted_names: set[str] = set()

    def add_player(rec: dict | None) -> None:
        if not rec:
            return
        name = str(rec["player_name"])
        if name not in drafted_names:
            drafted.append(rec)
            drafted_names.add(name)

    g_consumed = min(2, len(g_list))
    f_consumed = min(2, len(f_list))
    c_consumed = min(1, len(c_list))

    for i in range(g_consumed):
        add_player(g_list[i])
    for i in range(f_consumed):
        add_player(f_list[i])
    for i in range(c_consumed):
        add_player(c_list[i])

    ig, i_f, ic = g_consumed, f_consumed, c_consumed

    if ig < len(g_list):
        add_player(g_list[ig])
        ig += 1
    if i_f < len(f_list):
        add_player(f_list[i_f])
        i_f += 1
    if ic < len(c_list):
        add_player(c_list[ic])
        ic += 1
    else:
        if i_f < len(f_list):
            add_player(f_list[i_f])
            i_f += 1

    if len(drafted) < 8:
        for rec in r.to_dict("records"):
            if len(drafted) >= 8:
                break
            name = str(rec["player_name"])
            if name not in drafted_names:
                add_player(rec)

    if not drafted:
        return 0.0

    top = sorted(
        drafted,
        key=lambda x: (-float(x["playoff_pr"]), str(x["player_name"])),
    )[:8]
    pr_values = [float(x["playoff_pr"]) for x in top]
    n_boost = min(3, len(pr_values))
    for i in range(n_boost):
        pr_values[i] *= STAR_BOOST
    return float(sum(pr_values))


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    con = sqlite3.connect(DB_PATH)
    try:
        missing = _tables_missing(con)
        if missing:
            print(
                "Missing required table(s): "
                + ", ".join(missing)
                + ". Run upstream pipelines first.",
                flush=True,
            )
            return

        playoff = pd.read_sql_query(
            """
            SELECT player_name, mapped_position, playoff_pr
            FROM ultimate_playoff_pr
            """,
            con,
        )
        teams_players = pd.read_sql_query(
            """
            SELECT player_name, team_abbr
            FROM player_starting_teams_25_26
            WHERE team_abbr IS NOT NULL AND TRIM(team_abbr) != ''
            """,
            con,
        )

        coach_grades = pd.read_sql_query(
            """
            SELECT
                tc.team_abbr,
                COALESCE(NULLIF(TRIM(cs.Grade), ''), NULLIF(TRIM(tc.grade), '')) AS coach_grade
            FROM team_coaches_25_26 AS tc
            LEFT JOIN coach_system_data AS cs
              ON TRIM(tc.coach_name) = TRIM(cs.name)
            """,
            con,
        )
        coach_mult_by_team = {
            str(r.team_abbr): _coach_mult_from_grade(r.coach_grade)
            for r in coach_grades.itertuples(index=False)
        }

        playstyles = pd.read_sql_query(
            f"""
            SELECT team_abbr, playstyle
            FROM team_playstyle_data
            WHERE season = '{PLAYSTYLE_SEASON}'
            """,
            con,
        )
        mult_df = pd.read_sql_query(
            "SELECT playstyle, multiplier FROM playstyle_multipliers",
            con,
        )
        mult_map = dict(zip(mult_df["playstyle"], mult_df["multiplier"]))

        merged = teams_players.merge(playoff, on="player_name", how="inner")

        all_teams = sorted(coach_mult_by_team.keys())
        if len(all_teams) != 30:
            print(
                f"[warning] Expected 30 teams from team_coaches_25_26, got {len(all_teams)}.",
                flush=True,
            )

        ps_by_team = playstyles.drop_duplicates(subset=["team_abbr"]).set_index("team_abbr")[
            "playstyle"
        ]

        out_rows: list[tuple[str, float, float, float, float, float]] = []

        for team in all_teams:
            g = merged[merged["team_abbr"].astype(str) == team]
            base_8 = _draft_eight_playoff_pr(g)

            coach_mult = coach_mult_by_team.get(team, DEFAULT_COACH_MULT)
            amp_coach = 1.0 + (coach_mult - 1.0) * COACH_AMPLIFY

            raw_ps = ps_by_team.get(team, pd.NA)
            if raw_ps is pd.NA or raw_ps is None:
                ps_mult = DEFAULT_PLAYSTYLE_MULT
            else:
                ps_mult = float(mult_map.get(str(raw_ps), DEFAULT_PLAYSTYLE_MULT))

            cont_mult = _continuity_mult(team)

            final_pr = (
                base_8 * amp_coach * ps_mult * cont_mult
            )

            out_rows.append(
                (
                    team,
                    round(base_8, 2),
                    round(amp_coach, 2),
                    round(ps_mult, 2),
                    round(cont_mult, 2),
                    round(final_pr, 2),
                )
            )

        cur = con.cursor()
        cur.execute("DROP TABLE IF EXISTS team_playoff_pr_25_26")
        cur.execute(
            """
            CREATE TABLE team_playoff_pr_25_26 (
                team                 TEXT PRIMARY KEY,
                base_8man_pr         REAL NOT NULL,
                amplified_coach_mult REAL NOT NULL,
                playstyle_mult       REAL NOT NULL,
                continuity_mult      REAL NOT NULL,
                final_playoff_pr     REAL NOT NULL
            )
            """
        )
        cur.executemany(
            """
            INSERT INTO team_playoff_pr_25_26 (
                team,
                base_8man_pr,
                amplified_coach_mult,
                playstyle_mult,
                continuity_mult,
                final_playoff_pr
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            out_rows,
        )
        con.commit()

        rank_lines = sorted(out_rows, key=lambda x: (-x[5], x[0]))
        print("\nAll teams by final_playoff_pr (descending)\n", flush=True)
        print(
            f"  {'team':<5}  {'final':>10}  {'base8':>10}  {'coach*':>8}  "
            f"{'style':>7}  {'cont':>6}",
            flush=True,
        )
        for row in rank_lines:
            team, b8, ac, ps, ct, fin = row
            print(
                f"  {team:<5}  {fin:10.2f}  {b8:10.2f}  {ac:8.2f}  "
                f"{ps:7.2f}  {ct:6.2f}",
                flush=True,
            )
        print(
            f"\n[build_team_playoff_pr_25_26] Wrote {len(out_rows)} row(s) to team_playoff_pr_25_26.",
            flush=True,
        )
    finally:
        con.close()


if __name__ == "__main__":
    main()
