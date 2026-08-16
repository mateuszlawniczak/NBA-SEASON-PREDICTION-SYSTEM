"""
build_team_playoff_pr_25_26.py
-------------------------------
8-man playoff rotation using ``ultimate_playoff_pr.playoff_pr`` and positional
draft rules, then coach / playstyle / continuity multipliers.

Writes ONLY ``team_playoff_projection`` in ``nba_data.db`` (idempotent per season).

Reads:
    ultimate_playoff_pr, player_starting_teams, coach_system_data,
    playstyle_multipliers, ULTIMATE_PR (continuity top-2 ranking),

plus (required to attach coaches and playstyles to teams — not present in the
named tables alone):

  team_coaches, team_playstyle_data (source season).
"""

from __future__ import annotations

import os
import sqlite3
import sys
from typing import Literal

import pandas as pd

from season_utils import SeasonPair, parse_cli_seasons

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

Top2Status = Literal["kept", "assumed_kept", "departed"]

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

STAR_BOOST = 1.15
COACH_AMPLIFY = 1.5

CONTINUITY_HIGH = 1.05
CONTINUITY_LOW = 0.95
CONTINUITY_DEFAULT = 1.00

# Top-2 gate + target-season roster overlap (all target seasons).
CONTINUITY_OVERLAP_HIGH = 0.70
CONTINUITY_OVERLAP_DEFAULT_MIN = 0.50

CONTINUITY_MULT = {
    "high": CONTINUITY_HIGH,
    "low": CONTINUITY_LOW,
    "default": CONTINUITY_DEFAULT,
}

# Legacy 2025-26 hardcoded tiers (review / comparison only).
LEGACY_HIGH_CONTINUITY_TEAMS = frozenset(
    {"BOS", "DEN", "OKC", "NYK", "MIN", "IND", "ORL", "SAC"}
)
LEGACY_LOW_CONTINUITY_TEAMS = frozenset({"PHI", "DAL", "SAS", "CHI", "DET"})


def _legacy_continuity_tier(team_abbr: str) -> str:
    if team_abbr in LEGACY_HIGH_CONTINUITY_TEAMS:
        return "high"
    if team_abbr in LEGACY_LOW_CONTINUITY_TEAMS:
        return "low"
    return "default"


def _roster_player_ids(
    con: sqlite3.Connection,
    season: str,
    team_abbr: str,
) -> dict[int, str]:
    rows = con.execute(
        """
        SELECT player_id, player_name
        FROM player_starting_teams
        WHERE season = ? AND team_abbr = ?
        """,
        (season, team_abbr),
    ).fetchall()
    return {int(r[0]): str(r[1]) for r in rows}


def _source_top_two(
    con: sqlite3.Connection,
    team_abbr: str,
    source_season: str,
    target_season: str,
) -> list[tuple[int, str, float]]:
    """Top-2 source-roster players by canonical ``ULTIMATE_PR.pr``.

    ``ULTIMATE_PR.season`` is the *target* year: veterans are
    ``final_simulation_pr`` from the source season (plus target rookies).
    Ranking the source roster against that table is "last year's two best
    by the pipeline PR", without mixing ``player_simulation_pr`` or the
    previous cycle's incomplete ``ULTIMATE_PR`` (which omits that year's
    rookies). Players with no ``ULTIMATE_PR`` row are excluded, not 0.
    """
    rows = con.execute(
        """
        SELECT pst.player_id, pst.player_name, up.pr AS pr
        FROM player_starting_teams AS pst
        INNER JOIN ULTIMATE_PR AS up
          ON up.player_name = pst.player_name
         AND up.season = ?
        WHERE pst.season = ?
          AND pst.team_abbr = ?
          AND up.pr IS NOT NULL
        ORDER BY up.pr DESC, pst.player_name ASC, pst.player_id ASC
        LIMIT 2
        """,
        (target_season, source_season, team_abbr),
    ).fetchall()
    return [(int(r[0]), str(r[1]), float(r[2])) for r in rows]


def _target_team_for_player(
    con: sqlite3.Connection,
    player_id: int,
    target_season: str,
) -> str | None:
    row = con.execute(
        """
        SELECT team_abbr
        FROM player_starting_teams
        WHERE season = ? AND player_id = ?
        ORDER BY team_abbr ASC, rowid ASC
        LIMIT 1
        """,
        (target_season, player_id),
    ).fetchone()
    return str(row[0]) if row else None


def _top2_target_status(
    con: sqlite3.Connection,
    player_id: int,
    team_abbr: str,
    target_season: str,
) -> Top2Status:
    """
    kept         — same team in target ``player_starting_teams``
    assumed_kept — absent from target roster (not on any team); gate treats as kept
    departed     — on a different team in target roster table → triggers LOW gate
    """
    target_team = _target_team_for_player(con, player_id, target_season)
    if target_team is None:
        return "assumed_kept"
    if target_team == team_abbr:
        return "kept"
    return "departed"


def _unknown_player_reason(
    con: sqlite3.Connection,
    player_id: int,
    player_name: str,
    target_season: str,
) -> str:
    """Explain why a top-2 player is missing from the target roster table."""
    stats_row = con.execute(
        """
        SELECT team_abbr, gp
        FROM player_stats_basic
        WHERE season = ?
          AND (player_id = ? OR player_name = ?)
        ORDER BY gp DESC
        LIMIT 1
        """,
        (target_season, player_id, player_name),
    ).fetchone()
    if stats_row:
        team, gp = stats_row
        return (
            f"roster-table gap: {gp} GP for {team} in player_stats_basic "
            f"but no player_starting_teams row"
        )

    pst_any = con.execute(
        """
        SELECT 1
        FROM player_starting_teams
        WHERE season = ? AND (player_id = ? OR player_name = ?)
        LIMIT 1
        """,
        (target_season, player_id, player_name),
    ).fetchone()
    if pst_any:
        return "partial roster-table gap (player_id/name mismatch across tables)"

    return "not in player_stats_basic or player_starting_teams for target season"


def _target_roster_overlap_ratio(
    con: sqlite3.Connection,
    team_abbr: str,
    source_season: str,
    target_season: str,
) -> float:
    """(players on team in BOTH seasons) / (players on team in target season)."""
    source_ids = set(_roster_player_ids(con, source_season, team_abbr))
    target_ids = set(_roster_player_ids(con, target_season, team_abbr))
    if not target_ids:
        return 0.0
    return len(source_ids & target_ids) / len(target_ids)


def _tier_from_overlap(overlap: float) -> str:
    if overlap >= CONTINUITY_OVERLAP_HIGH:
        return "high"
    if overlap >= CONTINUITY_OVERLAP_DEFAULT_MIN:
        return "default"
    return "low"


def _evaluate_neutral_handling(
    con: sqlite3.Connection,
    team_abbr: str,
    target_season: str,
    source_season: str,
) -> str:
    """
    Previous rule: missing target-roster rows were neutral (gate ignored them).
    Only explicit departures triggered LOW; otherwise overlap decided tier.
    """
    top_two = _source_top_two(con, team_abbr, source_season, target_season)
    has_departed = False
    for pid, _name, _pr in top_two:
        target_team = _target_team_for_player(con, pid, target_season)
        if target_team is not None and target_team != team_abbr:
            has_departed = True
            break
    overlap = _target_roster_overlap_ratio(con, team_abbr, source_season, target_season)
    if has_departed:
        return "low"
    return _tier_from_overlap(overlap)


def evaluate_continuity(
    con: sqlite3.Connection,
    team_abbr: str,
    target_season: str,
    source_season: str,
) -> dict[str, object]:
    """
    Apply top-2 gate then target-season overlap rule.

    Top-2 is the source roster ranked by target-season ``ULTIMATE_PR.pr``
    only. The gate fires when one of those players appears on a *different*
    team in the target roster. Players absent from the target roster are
    treated as kept (not penalized); flag them for manual review.
    """
    top_two = _source_top_two(con, team_abbr, source_season, target_season)
    top_two_detail: list[tuple[int, str, float, Top2Status]] = []
    assumed_kept_players: list[dict[str, object]] = []

    for pid, name, pr in top_two:
        status = _top2_target_status(con, pid, team_abbr, target_season)
        top_two_detail.append((pid, name, pr, status))
        if status == "assumed_kept":
            assumed_kept_players.append(
                {
                    "player_id": pid,
                    "player_name": name,
                    "source_pr": pr,
                    "source_team": team_abbr,
                    "reason": _unknown_player_reason(con, pid, name, target_season),
                }
            )

    overlap = _target_roster_overlap_ratio(con, team_abbr, source_season, target_season)
    has_departed = any(s == "departed" for *_, s in top_two_detail)

    if has_departed:
        return {
            "tier": "low",
            "mult": CONTINUITY_LOW,
            "overlap": overlap,
            "top_two": top_two_detail,
            "assumed_kept_players": assumed_kept_players,
            "rule": "top2_gate",
            "flagged_assumed_kept": bool(assumed_kept_players),
        }

    tier = _tier_from_overlap(overlap)
    return {
        "tier": tier,
        "mult": CONTINUITY_MULT[tier],
        "overlap": overlap,
        "top_two": top_two_detail,
        "assumed_kept_players": assumed_kept_players,
        "rule": "overlap",
        "flagged_assumed_kept": bool(assumed_kept_players),
    }


def _continuity_mult(
    con: sqlite3.Connection,
    team_abbr: str,
    target_season: str,
    source_season: str,
) -> float:
    return float(
        evaluate_continuity(con, team_abbr, target_season, source_season)["mult"]
    )

REQUIRED_TABLES = (
    "ultimate_playoff_pr",
    "ULTIMATE_PR",
    "player_starting_teams",
    "coach_system_data",
    "playstyle_multipliers",
    "team_coaches",
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


def main(
    source_season: str | None = None,
    target_season: str | None = None,
    *,
    persist: bool = True,
) -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if source_season is None or target_season is None:
        pair: SeasonPair = parse_cli_seasons()
        source_season = pair.source
        target_season = pair.target

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
            WHERE season = ?
            """,
            con,
            params=(target_season,),
        )
        teams_players = pd.read_sql_query(
            """
            SELECT player_name, team_abbr
            FROM player_starting_teams
            WHERE season = ?
              AND team_abbr IS NOT NULL AND TRIM(team_abbr) != ''
            """,
            con,
            params=(target_season,),
        )

        coach_grades = pd.read_sql_query(
            """
            SELECT
                tc.team_abbr,
                COALESCE(NULLIF(TRIM(cs.Grade), ''), NULLIF(TRIM(tc.grade), '')) AS coach_grade
            FROM team_coaches AS tc
            LEFT JOIN coach_system_data AS cs
              ON TRIM(tc.coach_name) = TRIM(cs.name)
            WHERE tc.season = ?
            """,
            con,
            params=(target_season,),
        )
        coach_mult_by_team = {
            str(r.team_abbr): _coach_mult_from_grade(r.coach_grade)
            for r in coach_grades.itertuples(index=False)
        }

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
        mult_map = dict(zip(mult_df["playstyle"], mult_df["multiplier"]))

        merged = teams_players.merge(playoff, on="player_name", how="inner")

        all_teams = sorted(coach_mult_by_team.keys())
        if len(all_teams) != 30:
            print(
                f"[warning] Expected 30 teams from team_coaches, got {len(all_teams)}.",
                flush=True,
            )

        ps_by_team = playstyles.drop_duplicates(subset=["team_abbr"]).set_index("team_abbr")[
            "playstyle"
        ]

        out_rows: list[tuple[str, str, float, float, float, float, float]] = []

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

            cont_mult = _continuity_mult(con, team, target_season, source_season)

            final_pr = base_8 * amp_coach * ps_mult * cont_mult

            out_rows.append(
                (
                    team,
                    target_season,
                    round(base_8, 2),
                    round(amp_coach, 2),
                    round(ps_mult, 2),
                    round(cont_mult, 2),
                    round(final_pr, 2),
                )
            )

        cur = con.cursor()
        if persist:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS team_playoff_projection (
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
            cur.execute(
                "DELETE FROM team_playoff_projection WHERE season = ?;",
                (target_season,),
            )
            cur.executemany(
                """
                INSERT INTO team_playoff_projection (
                    team,
                    season,
                    base_8man_pr,
                    amplified_coach_mult,
                    playstyle_mult,
                    continuity_mult,
                    final_playoff_pr
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                out_rows,
            )
            con.commit()

        rank_lines = sorted(out_rows, key=lambda x: (-x[6], x[0]))
        print("\nAll teams by final_playoff_pr (descending)\n", flush=True)
        print(
            f"  {'team':<5}  {'final':>10}  {'base8':>10}  {'coach*':>8}  "
            f"{'style':>7}  {'cont':>6}",
            flush=True,
        )
        for row in rank_lines:
            team, _season, b8, ac, ps, ct, fin = row
            print(
                f"  {team:<5}  {fin:10.2f}  {b8:10.2f}  {ac:8.2f}  "
                f"{ps:7.2f}  {ct:6.2f}",
                flush=True,
            )
        if persist:
            print(
                f"\n[build_team_playoff_pr_25_26] Wrote {len(out_rows)} row(s) to "
                f"team_playoff_projection for season {target_season!r}.",
                flush=True,
            )
        else:
            print(
                f"\n[build_team_playoff_pr_25_26] Computed {len(out_rows)} row(s) "
                f"for season {target_season!r} (persist=False).",
                flush=True,
            )
    finally:
        con.close()


if __name__ == "__main__":
    main()
