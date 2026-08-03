"""
build_projected_team_pr_25_26.py
--------------------------------
9-man rotation engine: per-team positional draft using ``ULTIMATE_PR`` (``pr`` +
``mapped_position``), then coach and playstyle multipliers. Writes ONLY
``team_projection`` in nba_data.db; no other tables are altered.

Reads:
  player_starting_teams, ULTIMATE_PR, team_coaches,
  team_playstyle_data (source season), playstyle_multipliers
"""

from __future__ import annotations

import os
import sqlite3
import sys

import pandas as pd

from season_utils import SeasonPair, parse_cli_seasons

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

REQUIRED_TABLES = (
    "player_starting_teams",
    "ULTIMATE_PR",
    "team_coaches",
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

DEFAULT_PR = 5.0
DEFAULT_POSITION = "F"


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


def _prepare_merged_roster(merged: pd.DataFrame) -> pd.DataFrame:
    """Fill missing ``pr`` / ``mapped_position`` so no starter rows are dropped."""
    out = merged.copy()
    out["pr"] = pd.to_numeric(out["pr"], errors="coerce").fillna(float(DEFAULT_PR))
    out["mapped_position"] = out["mapped_position"].fillna(DEFAULT_POSITION)
    out["mapped_position"] = out["mapped_position"].astype(str).str.strip().str.upper()
    return out


def _draft_rotation_for_team(roster_sorted: pd.DataFrame) -> tuple[pd.DataFrame, float, str]:
    """
    Core: top 2 C, top 3 F, top 3 G (by ``pr`` within each position).
    Backfill: from players not drafted, top (9 - N) by ``pr`` so the rotation
    targets nine players when the roster is large enough.
    """
    r = (
        roster_sorted.sort_values(["pr", "player_name"], ascending=[False, True], kind="mergesort")
        .drop_duplicates(subset=["player_name"], keep="first")
        .reset_index(drop=True)
    )

    drafted_chunks: list[pd.DataFrame] = []
    for pos, quota in POSITION_QUOTAS:
        sub = r[r["mapped_position"] == pos].copy()
        drafted_chunks.append(sub.head(quota))

    drafted_df = (
        pd.concat(drafted_chunks, ignore_index=True) if drafted_chunks else pd.DataFrame(columns=r.columns)
    )
    if not drafted_df.empty:
        drafted_df = drafted_df.drop_duplicates(subset=["player_name"], keep="first")

    n = len(drafted_df)
    drafted_set = set(drafted_df["player_name"].astype(str)) if n else set()
    remainder = r[~r["player_name"].astype(str).isin(drafted_set)]
    need = 9 - n

    if need > 0 and not remainder.empty:
        extra = remainder.head(need)
        drafted_df = pd.concat([drafted_df, extra], ignore_index=True)

    base_team_pr = float(drafted_df["pr"].sum()) if not drafted_df.empty else 0.0
    rotation_players = ",".join(drafted_df["player_name"].astype(str).tolist())
    return drafted_df, base_team_pr, rotation_players


def _load_frames(
    con: sqlite3.Connection, source_season: str, target_season: str
) -> tuple[pd.DataFrame, ...]:
    teams = pd.read_sql_query(
        """
        SELECT player_name, team_abbr
        FROM player_starting_teams
        WHERE season = ?
        """,
        con,
        params=(target_season,),
    )
    ultimate = pd.read_sql_query(
        """
        SELECT player_name, pr, mapped_position
        FROM ULTIMATE_PR
        WHERE season = ?
        """,
        con,
        params=(target_season,),
    )
    coaches = pd.read_sql_query(
        """
        SELECT team_abbr, grade AS coach_grade
        FROM team_coaches
        WHERE season = ?
        """,
        con,
        params=(target_season,),
    )
    playstyles = pd.read_sql_query(
        """
        SELECT team_abbr, playstyle
        FROM team_playstyle_data
        WHERE season = ?
        """,
        con,
        params=(source_season,),
    )
    mult_df = pd.read_sql_query(
        "SELECT playstyle, multiplier FROM playstyle_multipliers",
        con,
    )
    return teams, ultimate, coaches, playstyles, mult_df


def main(source_season: str | None = None, target_season: str | None = None) -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if source_season is None or target_season is None:
        pair: SeasonPair = parse_cli_seasons()
        source_season = pair.source
        target_season = pair.target

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

        teams, ultimate, coaches, playstyles, mult_df = _load_frames(
            con, source_season, target_season
        )

        merged = teams.merge(ultimate, on="player_name", how="left")
        merged = _prepare_merged_roster(merged)
        merged = merged.dropna(subset=["team_abbr"])
        merged = merged[merged["team_abbr"].astype(str).str.strip() != ""]

        mult_map = dict(zip(mult_df["playstyle"], mult_df["multiplier"]))
        coach_by_team = coaches.drop_duplicates(subset=["team_abbr"]).set_index("team_abbr")[
            "coach_grade"
        ]
        ps_by_team = playstyles.drop_duplicates(subset=["team_abbr"]).set_index("team_abbr")[
            "playstyle"
        ]

        out_rows: list[tuple] = []

        for team_abbr, g in merged.groupby("team_abbr", sort=True):
            tabbr = str(team_abbr)
            roster = g.reset_index(drop=True)
            _, base_team_pr, rotation_players = _draft_rotation_for_team(roster)

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
                    target_season,
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
            CREATE TABLE IF NOT EXISTS team_projection (
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
        cur.execute("DELETE FROM team_projection WHERE season = ?;", (target_season,))
        cur.executemany(
            """
            INSERT INTO team_projection (
                team_abbr, season, base_team_pr, coach_grade, playstyle,
                final_team_pr, rotation_players
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            out_rows,
        )
        con.commit()

        rank_df = pd.DataFrame(
            out_rows,
            columns=[
                "team_abbr",
                "season",
                "base_team_pr",
                "coach_grade",
                "playstyle",
                "final_team_pr",
                "rotation_players",
            ],
        ).sort_values("final_team_pr", ascending=False, kind="mergesort")

        top10 = rank_df.head(10)
        print(f"\nTop 10 teams by final_team_pr ({target_season} projection)", flush=True)
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
        print(
            f"\n[build_projected_team_pr_25_26] Wrote {len(out_rows)} team row(s) "
            f"for season {target_season!r}.",
            flush=True,
        )
    finally:
        con.close()


if __name__ == "__main__":
    main()
