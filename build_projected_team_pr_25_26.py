"""
build_projected_team_pr_25_26.py
---------------------------------
9-man rotation engine: per-team positional draft on ULTIMATE_PR, then coach and
playstyle multipliers. Writes ONLY ``projected_team_pr_25_26`` in nba_data.db;
no other tables are altered.

Reads:
  player_starting_teams_25_26, ULTIMATE_PR, player_positions,
  team_coaches_25_26, team_playstyle_data (season 2024-25), playstyle_multipliers
"""

from __future__ import annotations

import os
import sqlite3
import sys

import pandas as pd

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

REQUIRED_TABLES = (
    "player_starting_teams_25_26",
    "ULTIMATE_PR",
    "player_positions",
    "team_coaches_25_26",
    "team_playstyle_data",
    "playstyle_multipliers",
)

COACH_GRADE_MULT: dict[str, float] = {
    "S": 1.08,
    "A": 1.04,
    "B": 1.02,
    "C": 1.00,
    "D": 0.97,
    "F": 0.95,
}

PLAYSTYLE_FALLBACK_MULT = 0.90
PLAYSTYLE_FALLBACK_LABEL = "Undefined / No Identity"

POSITION_QUOTAS: tuple[tuple[str, int], ...] = (
    ("C", 2),
    ("F", 3),
    ("G", 3),
)


def _tables_present(con: sqlite3.Connection) -> list[str]:
    missing: list[str] = []
    for t in REQUIRED_TABLES:
        row = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (t,),
        ).fetchone()
        if row is None:
            missing.append(t)
    return missing


def _normalize_grade(raw: object) -> str | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = str(raw).strip().upper()
    return s if s in COACH_GRADE_MULT else None


def _coach_multiplier(grade_letter: str | None) -> float:
    if grade_letter is None:
        return 1.00
    return COACH_GRADE_MULT[grade_letter]


def _draft_rotation_for_team(roster_sorted: pd.DataFrame) -> tuple[list[str], float]:
    """
    roster_sorted: rows for one team only, sorted by pr descending (stable).
    Draft order: top 2 C, top 3 F, top 3 G, then highest-pr wildcard from remainder.
    """
    drafted: list[str] = []
    drafted_set: set[str] = set()
    for pos, quota in POSITION_QUOTAS:
        sub = roster_sorted[roster_sorted["mapped_position"] == pos]
        for name in sub["player_name"].head(quota).tolist():
            if name not in drafted_set:
                drafted.append(str(name))
                drafted_set.add(str(name))

    remainder = roster_sorted[~roster_sorted["player_name"].isin(drafted_set)]
    if not remainder.empty:
        wn = str(remainder.iloc[0]["player_name"])
        drafted.append(wn)

    pr_by_name = roster_sorted.set_index("player_name")["pr"]
    base = float(sum(pr_by_name.loc[n] for n in drafted if n in pr_by_name.index))
    return drafted, base


def _load_frames(con: sqlite3.Connection) -> tuple[pd.DataFrame, ...]:
    teams = pd.read_sql_query(
        "SELECT player_name, team_abbr FROM player_starting_teams_25_26",
        con,
    )
    pr_df = pd.read_sql_query(
        "SELECT player_name, pr FROM ULTIMATE_PR",
        con,
    )
    pos_df = pd.read_sql_query(
        "SELECT player_name, mapped_position FROM player_positions",
        con,
    )
    coaches = pd.read_sql_query(
        "SELECT team_abbr, grade AS coach_grade FROM team_coaches_25_26",
        con,
    )
    playstyles = pd.read_sql_query(
        """
        SELECT team_abbr, playstyle
        FROM team_playstyle_data
        WHERE season = '2024-25'
        """,
        con,
    )
    mult_df = pd.read_sql_query(
        "SELECT playstyle, multiplier FROM playstyle_multipliers",
        con,
    )
    return teams, pr_df, pos_df, coaches, playstyles, mult_df


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    con = sqlite3.connect(DB_PATH)
    try:
        missing = _tables_present(con)
        if missing:
            print(
                "Missing required table(s): "
                + ", ".join(missing)
                + ". Run upstream pipelines first.",
                flush=True,
            )
            return

        teams, pr_df, pos_df, coaches, playstyles, mult_df = _load_frames(con)

        merged = teams.merge(pr_df, on="player_name", how="inner").merge(
            pos_df, on="player_name", how="inner"
        )
        merged = merged.dropna(subset=["pr", "mapped_position", "team_abbr"])
        merged = merged[merged["mapped_position"].isin(["G", "F", "C"])]

        mult_map = dict(zip(mult_df["playstyle"], mult_df["multiplier"]))
        coach_by_team = coaches.drop_duplicates(subset=["team_abbr"]).set_index("team_abbr")[
            "coach_grade"
        ]
        ps_by_team = playstyles.drop_duplicates(subset=["team_abbr"]).set_index("team_abbr")[
            "playstyle"
        ]

        out_rows: list[tuple] = []

        # Strict isolation: iterate groupby('team_abbr') — sort ONLY inside each group.
        for team_abbr, g in merged.groupby("team_abbr", sort=True):
            tabbr = str(team_abbr)
            # Per-team roster sorted by PR descending (no global roster sort).
            roster = (
                g.sort_values(["pr", "player_name"], ascending=[False, True], kind="mergesort")
                .drop_duplicates(subset=["player_name"], keep="first")
                .reset_index(drop=True)
            )
            rotation_names, base_team_pr = _draft_rotation_for_team(roster)
            rotation_players = ",".join(rotation_names)

            raw_grade = coach_by_team.get(tabbr, pd.NA)
            grade_norm = _normalize_grade(raw_grade)
            if raw_grade is None or pd.isna(raw_grade):
                display_grade = None
            else:
                display_grade = str(raw_grade).strip() or None
            coach_mult = _coach_multiplier(grade_norm)

            ps = ps_by_team.get(tabbr, pd.NA)
            if ps is pd.NA or ps is None:
                playstyle_label = PLAYSTYLE_FALLBACK_LABEL
                ps_mult = PLAYSTYLE_FALLBACK_MULT
            else:
                playstyle_label = str(ps)
                ps_mult = float(mult_map.get(playstyle_label, PLAYSTYLE_FALLBACK_MULT))

            final_team_pr = round(base_team_pr * coach_mult * ps_mult, 2)

            out_rows.append(
                (
                    tabbr,
                    base_team_pr,
                    display_grade if display_grade else None,
                    playstyle_label,
                    final_team_pr,
                    rotation_players,
                )
            )

        cur = con.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS projected_team_pr_25_26 (
                team_abbr         TEXT PRIMARY KEY,
                base_team_pr      REAL NOT NULL,
                coach_grade       TEXT,
                playstyle         TEXT NOT NULL,
                final_team_pr     REAL NOT NULL,
                rotation_players  TEXT NOT NULL
            )
            """
        )
        cur.execute("DELETE FROM projected_team_pr_25_26;")
        cur.executemany(
            """
            INSERT INTO projected_team_pr_25_26 (
                team_abbr, base_team_pr, coach_grade, playstyle, final_team_pr, rotation_players
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            out_rows,
        )
        con.commit()

        rank_df = pd.DataFrame(
            out_rows,
            columns=[
                "team_abbr",
                "base_team_pr",
                "coach_grade",
                "playstyle",
                "final_team_pr",
                "rotation_players",
            ],
        ).sort_values("final_team_pr", ascending=False, kind="mergesort")

        top10 = rank_df.head(10)
        print("\nTop 10 teams by final_team_pr (2025-26 projection)", flush=True)
        print(
            f"  {'#':>2}  {'Team':<5}  {'final_team_pr':>12}  {'base_team_pr':>12}  "
            f"{'coach_grade':>11}  {'playstyle':<22}  rotation_players",
            flush=True,
        )
        for i, (_, row) in enumerate(top10.iterrows(), start=1):
            cg = row["coach_grade"] if pd.notna(row["coach_grade"]) else "—"
            ps = row["playstyle"][:20] + "…" if len(str(row["playstyle"])) > 20 else row["playstyle"]
            rot = row["rotation_players"]
            rot_short = rot[:56] + "…" if len(rot) > 56 else rot
            print(
                f"  {i:2d}  {row['team_abbr']:<5}  {row['final_team_pr']:12.2f}  "
                f"{row['base_team_pr']:12.2f}  {str(cg):>11}  {str(ps):<22}  {rot_short}",
                flush=True,
            )
        print(f"\n[build_projected_team_pr_25_26] Wrote {len(out_rows)} team row(s).", flush=True)
    finally:
        con.close()


if __name__ == "__main__":
    main()
