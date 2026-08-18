"""
eval_pr.py
----------
Fast, read-only PR harness: team strength from ULTIMATE_PR vs actual win rate.

For each TRAIN season, draft each team's 9-man core the same way
build_projected_team_pr.py does (top 2 C / 3 F / 3 G, backfilled to 9),
sum those PRs, and correlate with team_stats win rate.

Pooled r is one Pearson over within-season z-scored values (same as
compute_engine_scores.compute_pooled win_order_r) — not the mean of
per-season r.

Never writes. Never reads TEST seasons.
"""

from __future__ import annotations

import os
import sqlite3
import sys

import pandas as pd

from build_projected_team_pr import _draft_rotation_for_team, _prepare_merged_roster
from compute_baseline_scores import (
    TRAIN_SEASONS,
    _fetch_team_stats,
    _pearson_r,
    _zscore,
)

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")
EXPECTED_TEAMS = 30
CALIBRATION_TARGET = 0.594
CALIBRATION_TOLERANCE = 0.03


def _ro_connect() -> sqlite3.Connection:
    uri = f"file:{DB_PATH}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _team_strengths(con: sqlite3.Connection, season: str) -> dict[str, float]:
    """Sum of the 9-man core PR for each team in `season`."""
    teams = pd.read_sql_query(
        """
        SELECT player_name, team_abbr
        FROM player_starting_teams
        WHERE season = ?
        """,
        con,
        params=(season,),
    )
    ultimate = pd.read_sql_query(
        """
        SELECT player_name, pr, mapped_position
        FROM ULTIMATE_PR
        WHERE season = ?
        """,
        con,
        params=(season,),
    )
    if teams.empty:
        raise RuntimeError(f"{season}: no player_starting_teams rows")
    if ultimate.empty:
        raise RuntimeError(f"{season}: no ULTIMATE_PR rows")

    merged = teams.merge(ultimate, on="player_name", how="left")
    merged = _prepare_merged_roster(merged)
    merged = merged.dropna(subset=["team_abbr"])
    merged = merged[merged["team_abbr"].astype(str).str.strip() != ""]

    strengths: dict[str, float] = {}
    for team_abbr, group in merged.groupby("team_abbr", sort=True):
        _, base_team_pr, _ = _draft_rotation_for_team(group.reset_index(drop=True))
        strengths[str(team_abbr)] = float(base_team_pr)
    return strengths


def _actual_win_rates(con: sqlite3.Connection, season: str) -> dict[str, float]:
    actual = _fetch_team_stats(con, season)
    return {row.team_abbr: row.win_pct for row in actual.values()}


def evaluate_season(
    con: sqlite3.Connection, season: str
) -> tuple[float, list[float], list[float]]:
    strengths = _team_strengths(con, season)
    win_rates = _actual_win_rates(con, season)
    common = sorted(set(strengths) & set(win_rates))
    if len(common) != EXPECTED_TEAMS:
        raise RuntimeError(
            f"{season}: {len(common)} team(s) with both strength and win rate, "
            f"expected {EXPECTED_TEAMS}."
        )
    xs = [strengths[abbr] for abbr in common]
    ys = [win_rates[abbr] for abbr in common]
    return _pearson_r(xs, ys), xs, ys


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    con = _ro_connect()
    try:
        print("=== PR evaluator (TRAIN only, read-only) ===\n")
        print(f"{'Season':<12} {'r':>8}  {'teams':>5}")
        z_pred: list[float] = []
        z_act: list[float] = []
        for season in TRAIN_SEASONS:
            r, xs, ys = evaluate_season(con, season)
            z_pred.extend(_zscore(xs))
            z_act.extend(_zscore(ys))
            print(f"{season:<12} {r:8.3f}  {len(xs):5d}")
        pooled = _pearson_r(z_pred, z_act) if z_pred else 0.0
        print(f"{'POOLED':<12} {pooled:8.3f}  {len(z_pred):5d}")
        print(
            "\nPooled r = Pearson over within-season z-scored "
            "(strength, win rate) pairs; not the mean of per-season r."
        )
        delta = pooled - CALIBRATION_TARGET
        print(
            f"Calibration vs TRAIN win_order_r {CALIBRATION_TARGET:.3f}: "
            f"delta={delta:+.3f}"
        )
        if abs(delta) > CALIBRATION_TOLERANCE:
            print(
                f"\n[stop] pooled r={pooled:.3f} is more than "
                f"{CALIBRATION_TOLERANCE:.2f} from {CALIBRATION_TARGET:.3f}. "
                "Harness is measuring something else — not proceeding."
            )
            sys.exit(2)
    finally:
        con.close()


if __name__ == "__main__":
    main()
