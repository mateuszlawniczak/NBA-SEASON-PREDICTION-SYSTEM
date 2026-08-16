"""
refresh_postseason.py
---------------------
One entry point for refreshing a postseason end to end:

    py refresh_postseason.py --season 2025-26

Runs, in order:
  1. fetch_team_playoffs            — final W/L, ratings, pace (API)
  2. hydrate_season                 — conference, conference_seed, prev_* (DB only)
  3. GUARD                          — no NULL conference_seed from 2020-21 onward
  4. rederive_season                — playoff_result from bracket wins
  5. VALIDATE                       — bracket shape must be 1/1/2/4/8/4

Step 3 exists because backfill_playoff_result_legacy.py infers play-in wins
from conference_seed. A NULL seed makes every play-in adjustment silently zero
and produces wrong labels with no error, so it must abort rather than proceed.

This module is glue only — every step lives in the script that owns it.
Running it twice in a row leaves the database unchanged the second time.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

from backfill_playoff_result_legacy import bracket_counts, rederive_season
from backfill_prev_team_playoffs import hydrate_season
from fetch_team_playoffs import run as fetch_playoffs

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

# Seasons before this one predate the play-in and have NULL conference_seed by
# design, so the guard does not apply to them.
PLAYIN_ERA_FROM = "2020-21"

EXPECTED_BRACKET = {
    "Champion": 1,
    "Finals": 1,
    "Conf. Finals": 2,
    "Conf. Semifinals": 4,
    "1st Round": 8,
    "Play-In Eliminated": 4,
}

BRACKET_ORDER = [
    "Champion",
    "Finals",
    "Conf. Finals",
    "Conf. Semifinals",
    "1st Round",
    "Play-In Eliminated",
]


def banner(step: int, title: str) -> None:
    print(f"\n{'=' * 70}", flush=True)
    print(f"[{step}/5] {title}", flush=True)
    print(f"{'=' * 70}", flush=True)


def die(message: str) -> None:
    print("\n" + "!" * 70, file=sys.stderr)
    print(message, file=sys.stderr)
    print("!" * 70, file=sys.stderr)
    sys.exit(1)


def guard_conference_seeds(con: sqlite3.Connection) -> None:
    """Abort if any play-in-era row lost its conference_seed."""
    missing = con.execute(
        """
        SELECT season, team_abbr
        FROM team_stats_playoffs
        WHERE season >= ? AND conference_seed IS NULL
        ORDER BY season, team_abbr
        """,
        (PLAYIN_ERA_FROM,),
    ).fetchall()
    if not missing:
        print(
            f"  [guard] OK — every row from {PLAYIN_ERA_FROM} onward has a "
            f"conference_seed.",
            flush=True,
        )
        return

    for season, abbr in missing:
        print(f"  NULL conference_seed: {season} {abbr}", file=sys.stderr)
    die(
        f"ABORTING: {len(missing)} row(s) from {PLAYIN_ERA_FROM} onward have a "
        "NULL conference_seed.\nplayoff_result is derived from that seed — "
        "deriving it now would silently produce wrong labels."
    )


def validate_bracket(con: sqlite3.Connection, season: str) -> None:
    actual = bracket_counts(con, season)
    print(f"  {'LABEL':<20} {'EXPECTED':>8} {'GOT':>5}", flush=True)
    for label in BRACKET_ORDER:
        expected = EXPECTED_BRACKET[label]
        got = actual.get(label, 0)
        flag = "" if expected == got else "   <-- MISMATCH"
        print(f"  {label:<20} {expected:>8} {got:>5}{flag}", flush=True)

    unexpected = sorted(set(actual) - set(EXPECTED_BRACKET))
    if actual == EXPECTED_BRACKET and not unexpected:
        print(f"\n  [validate] OK — {season} bracket shape is correct.", flush=True)
        return

    for label in unexpected:
        print(f"  unexpected label {label!r}: {actual[label]} row(s)", file=sys.stderr)
    for label in BRACKET_ORDER:
        expected = EXPECTED_BRACKET[label]
        got = actual.get(label, 0)
        if expected == got:
            continue
        members = con.execute(
            """
            SELECT team_abbr, wins, losses, conference_seed
            FROM team_stats_playoffs
            WHERE season = ? AND playoff_result = ?
            ORDER BY wins DESC, team_abbr
            """,
            (season, label),
        ).fetchall()
        listed = (
            ", ".join(f"{a} ({w}-{l}, seed {s})" for a, w, l, s in members) or "none"
        )
        print(
            f"  {label}: expected {expected}, got {got} -> {listed}",
            file=sys.stderr,
        )
    die(f"ABORTING: {season} bracket shape does not match {EXPECTED_BRACKET}.")


def refresh(season: str, skip_fetch: bool = False) -> None:
    banner(1, f"Fetch postseason W/L and ratings — {season}")
    if skip_fetch:
        print("  [skip] --skip-fetch given; using the stored W/L.", flush=True)
    else:
        fetch_playoffs([season])

    con = sqlite3.connect(DB_PATH)
    try:
        banner(2, f"Hydrate conference / conference_seed / prev_* — {season}")
        hydrate_season(con, season)

        banner(3, f"Guard — no NULL conference_seed from {PLAYIN_ERA_FROM} onward")
        guard_conference_seeds(con)

        banner(4, f"Derive playoff_result — {season}")
        rederive_season(con, season)

        banner(5, f"Validate bracket shape — {season}")
        validate_bracket(con, season)
    finally:
        con.close()

    print(f"\n{'=' * 70}", flush=True)
    print(f"  DONE — {season} postseason refreshed and validated.", flush=True)
    print(f"{'=' * 70}", flush=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh one postseason end to end: fetch, hydrate, derive, validate."
    )
    parser.add_argument("--season", required=True, help="Season to refresh, e.g. 2025-26.")
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Re-run the DB-only steps against the stored W/L, making no API calls.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    refresh(args.season, skip_fetch=args.skip_fetch)
