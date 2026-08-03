"""
calculate_rookie_projected_pr_25_26.py
--------------------------------------
Loads the 2025 draft class from rookie_data, joins ages from player_stats_basic
(target season), derives draft/age tiers, looks up rookie_baselines.year_1_base_pr,
and writes rookie_projection.

Does not ALTER or DROP any existing table except creating (if missing) and
deleting/repopulating rookie_projection rows for the target season only.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from typing import Any

from season_utils import SeasonPair, parse_cli_seasons
from leakage_guards import draft_year_for_target

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")
DEFAULT_AGE = 20.0

CREATE_ROOKIE_PROJECTED_PR = """
CREATE TABLE IF NOT EXISTS rookie_projection (
    player_name TEXT NOT NULL,
    season      TEXT NOT NULL,
    rookie_pr   REAL NOT NULL,
    PRIMARY KEY (player_name, season)
);
"""


def draft_tier(pick: Any) -> str:
    if pick is None:
        return "Second Round"
    try:
        p = int(pick)
    except (TypeError, ValueError):
        return "Second Round"
    if 1 <= p <= 3:
        return "Top 3"
    if 4 <= p <= 14:
        return "Lottery 4-14"
    if 15 <= p <= 30:
        return "Late First 15-30"
    return "Second Round"


def age_tier(dtier: str, age: float) -> str:
    if dtier == "Top 3":
        return "Under 21" if age < 21 else "21+"
    if dtier == "Lottery 4-14":
        if age < 20:
            return "Under 20"
        if age < 22:
            return "20-21"
        return "22+"
    return "Any Age"


if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main(source_season: str | None = None, target_season: str | None = None) -> None:
    if source_season is None or target_season is None:
        pair: SeasonPair = parse_cli_seasons()
        source_season = pair.source
        target_season = pair.target
    _ = source_season
    draft_year = draft_year_for_target(target_season)

    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.cursor()
        cur.execute("PRAGMA foreign_keys=ON;")

        cur.execute(CREATE_ROOKIE_PROJECTED_PR)
        cur.execute("DELETE FROM rookie_projection WHERE season = ?;", (target_season,))

        cur.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='rookie_baselines';
            """
        )
        if cur.fetchone() is None:
            raise SystemExit(
                "Missing table rookie_baselines. Run init_rookie_baselines.py first."
            )

        baseline_map: dict[tuple[str, str], float] = {}
        cur.execute(
            """
            SELECT draft_tier, age_tier, year_1_base_pr
            FROM rookie_baselines;
            """
        )
        for dtier, agtier, base_pr in cur.fetchall():
            baseline_map[str(dtier).strip(), str(agtier).strip()] = float(base_pr)

        cur.execute(
            """
            SELECT
                rd.player_name,
                rd.draft_pick,
                (
                    SELECT MAX(psb.age)
                    FROM player_stats_basic AS psb
                    WHERE psb.player_name = rd.player_name
                      AND psb.season = ?
                ) AS age_val
            FROM rookie_data AS rd
            WHERE rd.draft_year = ?
            ORDER BY rd.player_name;
            """,
            (target_season, draft_year),
        )

        rows_out: list[tuple[str, str, float]] = []

        for player_name_raw, draft_pick_raw, age_val in cur.fetchall():
            player_name = str(player_name_raw).strip()

            raw_age = age_val
            if raw_age is None:
                age = float(DEFAULT_AGE)
            else:
                try:
                    age = float(raw_age)
                except (TypeError, ValueError):
                    age = float(DEFAULT_AGE)

            dt = draft_tier(draft_pick_raw)
            at = age_tier(dt, age)
            key = (dt, at)
            if key not in baseline_map:
                raise KeyError(
                    f"No rookie_baselines row for {player_name!r}: "
                    f"draft_tier={dt!r}, age_tier={at!r}"
                )

            rows_out.append((player_name, target_season, baseline_map[key]))

        cur.executemany(
            """
            INSERT INTO rookie_projection (player_name, season, rookie_pr)
            VALUES (?, ?, ?);
            """,
            rows_out,
        )
        con.commit()
        print(f"rookie_projection refreshed: {len(rows_out)} row(s) for {target_season!r} (draft_year={draft_year}).")
    finally:
        con.close()


if __name__ == "__main__":
    main()
