"""
apply_pedigree_trajectory_boost.py
----------------------------------
Applies "Pedigree Trajectory Boost" multipliers to recent ROY standouts in
ULTIMATE_PR.pr and ultimate_playoff_pr (base_pr, playoff_pr).

Each run multiplies again from the current values. Rebuild ULTIMATE_PR /
ultimate_playoff_pr from your pipelines before re-applying if you need a clean base.
"""

from __future__ import annotations

import os
import sqlite3
import sys

from season_utils import SeasonPair, parse_cli_seasons
from leakage_guards import ROY_PEDIGREE_AWARD_SEASON, pedigree_boosts_before_target

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

LEGACY_PEDIGREE_BOOST: dict[str, float] = {
    player: mult for player, (_season, mult) in ROY_PEDIGREE_AWARD_SEASON.items()
}

VERIFY_PLAYERS = ("Victor Wembanyama", "Chet Holmgren")


def _fetch_playoff_pr(con: sqlite3.Connection, player: str, target_season: str) -> float | None:
    row = con.execute(
        """
        SELECT playoff_pr FROM ultimate_playoff_pr
        WHERE player_name = ? AND season = ?;
        """,
        (player, target_season),
    ).fetchone()
    if row is None:
        return None
    return float(row[0])


def main(source_season: str | None = None, target_season: str | None = None) -> None:
    if source_season is None or target_season is None:
        pair: SeasonPair = parse_cli_seasons()
        source_season = pair.source
        target_season = pair.target
    _ = source_season

    boosts = pedigree_boosts_before_target(target_season)
    if target_season == "2025-26" and boosts != LEGACY_PEDIGREE_BOOST:
        print(
            f"[warn] pedigree derive mismatch for {target_season!r}",
            flush=True,
        )
    else:
        print(
            f"[pedigree] applying {len(boosts)} boost(s) earned before {target_season!r}",
            flush=True,
        )

    con = sqlite3.connect(DB_PATH)
    try:
        before = {p: _fetch_playoff_pr(con, p, target_season) for p in VERIFY_PLAYERS}

        for player_name, mult in boosts.items():
            con.execute(
                """
                UPDATE ULTIMATE_PR
                SET pr = ROUND(pr * ?, 2)
                WHERE player_name = ? AND season = ?;
                """,
                (mult, player_name, target_season),
            )

        for player_name, mult in boosts.items():
            con.execute(
                """
                UPDATE ultimate_playoff_pr
                SET
                    base_pr = ROUND(base_pr * ?, 2),
                    playoff_pr = ROUND(playoff_pr * ?, 2)
                WHERE player_name = ? AND season = ?;
                """,
                (mult, mult, player_name, target_season),
            )

        con.commit()

        after = {p: _fetch_playoff_pr(con, p, target_season) for p in VERIFY_PLAYERS}

        print("Pedigree Trajectory Boost — playoff_pr verification")
        print("-" * 60)
        for p in VERIFY_PLAYERS:
            b = before[p]
            a = after[p]
            mult = boosts.get(p)
            if b is None and a is None:
                print(f"  {p}: (not in ultimate_playoff_pr — skipped)")
            elif b is None:
                print(f"  {p}: Before = (missing), After = {a} (x{mult})")
            else:
                expected = round(b * mult, 2) if mult else None
                ok = "OK" if a is not None and expected is not None and a == expected else "check"
                print(
                    f"  {p}: Before = {b}, After = {a} "
                    f"(x{mult} → expected {expected}) [{ok}]"
                )
    finally:
        con.close()


if __name__ == "__main__":
    main()
