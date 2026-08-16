"""
backfill_playoff_result_legacy.py
---------------------------------
Fill NULL playoff_result in team_stats_playoffs for 2017-18, 2018-19, 2019-20
using the playoff-bracket wins already stored in the table.

Same wins → label mapping as migrate_team_playoffs_v2.py / backfill_prev_team_playoffs.py.
No API calls — DB only.

With --season SEASON, re-derives that one season instead, overwriting existing
labels, stripping merged play-in wins back out first, and validating the
resulting bracket against REFERENCE_SEASON's shape.
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys

DB_PATH = "nba_data.db"
TARGET_SEASONS = ("2017-18", "2018-19", "2019-20")

# Season whose bracket shape a re-derived season is validated against.
REFERENCE_SEASON = "2024-25"

EXPECTED_VALUES = frozenset(
    {
        "Champion",
        "Finals",
        "Conf. Finals",
        "Conf. Semifinals",
        "1st Round",
        "Play-In Eliminated",
        "Missed Playoffs",
    }
)


def wins_to_result(wins: int | None) -> str:
    if wins is None:
        return "1st Round"
    if wins >= 16:
        return "Champion"
    if wins >= 12:
        return "Finals"
    if wins >= 8:
        return "Conf. Finals"
    if wins >= 4:
        return "Conf. Semifinals"
    return "1st Round"


def table_fingerprint(con: sqlite3.Connection, exclude_seasons: tuple[str, ...] = ()) -> str:
    q = """
        SELECT season, team_id, team_abbr, pts_per_game, opp_pts_per_game,
               off_rating, def_rating, net_rating, adj_off_rating, adj_def_rating,
               adj_net_rating, pace, wins, losses, win_pct, conference_seed,
               prev_seed, prev_playoff_result, playoff_result, conference
        FROM team_stats_playoffs
    """
    if exclude_seasons:
        placeholders = ",".join("?" * len(exclude_seasons))
        q += f" WHERE season NOT IN ({placeholders})"
        rows = con.execute(q + " ORDER BY season, team_id", exclude_seasons).fetchall()
    else:
        rows = con.execute(q + " ORDER BY season, team_id").fetchall()
    return hashlib.sha256(repr(rows).encode()).hexdigest()


# fetch_team_playoffs.py merges Play-In games into the same W/L totals, so the
# stored wins column mixes play-in wins with bracket wins. Seeds 7-10 are the
# play-in field: a 7/8 seed needs one win to reach the bracket, a 9/10 seed two.
# Only play-in-era seasons carry a conference_seed, so a NULL seed leaves the
# wins untouched and reproduces the legacy behaviour.
PLAYIN_FIRST_SEED = 7
MAX_PLAYIN_GAMES = 2


def playin_wins_carried(seed: int | None) -> int:
    if seed is None or seed < PLAYIN_FIRST_SEED:
        return 0
    return 1 if seed <= 8 else 2


def bracket_wins(wins: int | None, seed: int | None) -> int | None:
    """Playoff-only wins: the stored total minus any wins earned in the play-in."""
    if wins is None:
        return None
    return wins - playin_wins_carried(seed)


def eliminated_in_playin(wins: int | None, losses: int | None, seed: int | None) -> bool:
    """A play-in team that never reached the bracket plays at most two games."""
    if seed is None or seed < PLAYIN_FIRST_SEED:
        return False
    return (wins or 0) + (losses or 0) <= MAX_PLAYIN_GAMES


def bracket_counts(con: sqlite3.Connection, season: str) -> dict[str, int]:
    return {
        label: n
        for label, n in con.execute(
            """
            SELECT playoff_result, COUNT(1)
            FROM team_stats_playoffs
            WHERE season = ?
            GROUP BY playoff_result
            """,
            (season,),
        ).fetchall()
    }


def rederive_season(con: sqlite3.Connection, season: str) -> None:
    """Re-derive playoff_result for one season from its stored playoff wins.

    Unlike the legacy backfill this overwrites existing labels, because the
    source wins themselves may have been re-fetched. Play-in games are stripped
    out first so wins_to_result sees bracket wins only, and the resulting
    bracket is then checked against REFERENCE_SEASON's shape.
    """
    before_other = table_fingerprint(con, (season,))
    rows = con.execute(
        """
        SELECT team_id, team_abbr, wins, losses, conference_seed
        FROM team_stats_playoffs
        WHERE season = ?
        ORDER BY wins DESC, team_abbr
        """,
        (season,),
    ).fetchall()
    if not rows:
        print(f"[ERROR] No team_stats_playoffs rows for {season}.", file=sys.stderr)
        sys.exit(1)

    previous = {
        team_id: label
        for team_id, label in con.execute(
            "SELECT team_id, playoff_result FROM team_stats_playoffs WHERE season=?",
            (season,),
        ).fetchall()
    }

    print(f"[rederive] {season} — {len(rows)} rows from stored playoff wins\n", flush=True)
    for team_id, abbr, wins, losses, seed in rows:
        if eliminated_in_playin(wins, losses, seed):
            result = "Play-In Eliminated"
            po_wins = None
        else:
            po_wins = bracket_wins(wins, seed)
            result = wins_to_result(po_wins)
        con.execute(
            "UPDATE team_stats_playoffs SET playoff_result = ? WHERE season = ? AND team_id = ?",
            (result, season, team_id),
        )
        was = previous.get(team_id)
        change = "" if was == result else f"   (was {was!r})"
        bracket = "  —" if po_wins is None else f"{po_wins:>3}"
        print(
            f"  {abbr:<4} seed={str(seed):<4} {wins:>2}-{losses:<2} "
            f"bracket_wins={bracket} -> {result!r}{change}",
            flush=True,
        )
    con.commit()

    after_other = table_fingerprint(con, (season,))
    print(f"\nOther seasons fingerprint unchanged: {before_other == after_other}", flush=True)

    actual = bracket_counts(con, season)
    bad = [v for v in actual if v not in EXPECTED_VALUES]
    if bad:
        print(f"[ERROR] Unexpected playoff_result values in {season}: {bad}", file=sys.stderr)
        sys.exit(1)

    expected = bracket_counts(con, REFERENCE_SEASON)
    labels = sorted(set(expected) | set(actual))
    print(f"\nBracket shape — {season} vs {REFERENCE_SEASON}:", flush=True)
    for label in labels:
        exp = expected.get(label, 0)
        act = actual.get(label, 0)
        flag = "  <-- MISMATCH" if exp != act else ""
        print(f"  {label:<20} expected {exp:>2}   got {act:>2}{flag}", flush=True)

    if actual == expected:
        print(f"\n[OK] {season} bracket matches {REFERENCE_SEASON}.", flush=True)
        return

    print("\n" + "!" * 70, file=sys.stderr)
    print(
        f"BRACKET VALIDATION FAILED for {season} — shape does not match "
        f"{REFERENCE_SEASON}.",
        file=sys.stderr,
    )
    print("!" * 70, file=sys.stderr)
    for label in labels:
        exp = expected.get(label, 0)
        act = actual.get(label, 0)
        if exp == act:
            continue
        members = con.execute(
            """
            SELECT team_abbr, wins, losses, conference_seed
            FROM team_stats_playoffs
            WHERE season = ? AND playoff_result = ?
            ORDER BY wins ASC, team_abbr
            """,
            (season, label),
        ).fetchall()
        listed = (
            ", ".join(f"{a} ({w}-{l}, seed {s})" for a, w, l, s in members) or "none"
        )
        print(f"  {label}: expected {exp}, got {act} -> {listed}", file=sys.stderr)

    # The fetcher merges play-in games into the same W/L totals, so wins alone
    # cannot separate a play-in exit from a 1st-round exit, nor can it tell how
    # many of a team's wins came from the play-in. Name the affected teams.
    playin = con.execute(
        """
        SELECT team_abbr, wins, losses, conference_seed, playoff_result
        FROM team_stats_playoffs
        WHERE season = ? AND conference_seed >= 7
        ORDER BY conference_seed, team_abbr
        """,
        (season,),
    ).fetchall()
    if playin:
        print("\nPlay-in participants (seeds 7-10) — merged W/L:", file=sys.stderr)
        for abbr, wins, losses, seed, label in playin:
            if eliminated_in_playin(wins, losses, seed):
                note = "eliminated in the play-in"
            else:
                note = (
                    f"advanced carrying {playin_wins_carried(seed)} play-in win(s), "
                    f"so {bracket_wins(wins, seed)} bracket wins"
                )
            print(
                f"  seed {seed:>2}  {abbr:<4} {wins}-{losses}  labelled "
                f"{label!r}  — {note}",
                file=sys.stderr,
            )
    print(
        "\nThresholds were NOT adjusted. Fix the source wins or the label rule, "
        "then re-run.",
        file=sys.stderr,
    )
    sys.exit(1)


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    before_other = table_fingerprint(con, TARGET_SEASONS)
    null_total = con.execute(
        """
        SELECT COUNT(1) FROM team_stats_playoffs
        WHERE season IN (?, ?, ?) AND playoff_result IS NULL
        """,
        TARGET_SEASONS,
    ).fetchone()[0]
    if null_total == 0:
        print("[skip] All target seasons already have playoff_result populated.", flush=True)
        for season in TARGET_SEASONS:
            vals = [
                r[0]
                for r in con.execute(
                    "SELECT DISTINCT playoff_result FROM team_stats_playoffs WHERE season=? ORDER BY 1",
                    (season,),
                ).fetchall()
            ]
            total = con.execute(
                "SELECT COUNT(1) FROM team_stats_playoffs WHERE season=?", (season,)
            ).fetchone()[0]
            print(f"  {season}: {total} rows, values={vals}", flush=True)
        con.close()
        print("\n[OK] Already backfilled.")
        return

    before_full = con.execute(
        """
        SELECT season, team_id, team_abbr, pts_per_game, opp_pts_per_game,
               off_rating, def_rating, net_rating, adj_off_rating, adj_def_rating,
               adj_net_rating, pace, wins, losses, win_pct, conference_seed,
               prev_seed, prev_playoff_result, playoff_result, conference
        FROM team_stats_playoffs
        WHERE season IN (?, ?, ?)
        ORDER BY season, team_id
        """,
        TARGET_SEASONS,
    ).fetchall()

    updated = 0
    for season in TARGET_SEASONS:
        rows = con.execute(
            "SELECT team_id, team_abbr, wins FROM team_stats_playoffs WHERE season=?",
            (season,),
        ).fetchall()
        for team_id, abbr, wins in rows:
            result = wins_to_result(wins)
            con.execute(
                """
                UPDATE team_stats_playoffs
                SET playoff_result = ?
                WHERE season = ? AND team_id = ?
                """,
                (result, season, team_id),
            )
            updated += 1
            print(f"  {season} {abbr}: wins={wins} -> {result!r}", flush=True)
    con.commit()

    after_other = table_fingerprint(con, TARGET_SEASONS)
    print(f"\nUpdated {updated} row(s).", flush=True)
    print(f"Other seasons fingerprint unchanged: {before_other == after_other}")

    for season in TARGET_SEASONS:
        nulls = con.execute(
            "SELECT COUNT(1) FROM team_stats_playoffs WHERE season=? AND playoff_result IS NULL",
            (season,),
        ).fetchone()[0]
        vals = [
            r[0]
            for r in con.execute(
                "SELECT DISTINCT playoff_result FROM team_stats_playoffs WHERE season=? ORDER BY 1",
                (season,),
            ).fetchall()
        ]
        total = con.execute(
            "SELECT COUNT(1) FROM team_stats_playoffs WHERE season=?", (season,)
        ).fetchone()[0]
        print(f"  {season}: {total} rows, NULL={nulls}, values={vals}")
        bad = [v for v in vals if v not in EXPECTED_VALUES]
        if bad:
            print(f"    UNEXPECTED VALUES: {bad}", file=sys.stderr)
            sys.exit(1)
        if nulls:
            print(f"    STILL HAS NULLS", file=sys.stderr)
            sys.exit(1)

    # Ensure only playoff_result changed within target seasons
    after_full = con.execute(
        """
        SELECT season, team_id, team_abbr, pts_per_game, opp_pts_per_game,
               off_rating, def_rating, net_rating, adj_off_rating, adj_def_rating,
               adj_net_rating, pace, wins, losses, win_pct, conference_seed,
               prev_seed, prev_playoff_result, playoff_result, conference
        FROM team_stats_playoffs
        WHERE season IN (?, ?, ?)
        ORDER BY season, team_id
        """,
        TARGET_SEASONS,
    ).fetchall()
    for b, a in zip(before_full, after_full):
        if b[0:18] != a[0:18] or b[19] != a[19]:
            print(
                f"Non-playoff_result column changed: {b[0]} {b[2]}",
                file=sys.stderr,
            )
            sys.exit(1)
        if b[18] is not None:
            print(
                f"playoff_result was not NULL before backfill: {b[0]} {b[2]}",
                file=sys.stderr,
            )
            sys.exit(1)
        if a[18] is None:
            print(f"playoff_result still NULL: {a[0]} {a[2]}", file=sys.stderr)
            sys.exit(1)

    con.close()
    print("\n[OK] Backfill complete.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Derive playoff_result from stored playoff wins."
    )
    parser.add_argument(
        "--season",
        help=(
            "Re-derive a single season, e.g. 2025-26, overwriting existing "
            "labels and validating the bracket shape against "
            f"{REFERENCE_SEASON}. Omit to run the legacy NULL-only backfill "
            "for " + ", ".join(TARGET_SEASONS) + "."
        ),
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    if args.season:
        connection = sqlite3.connect(DB_PATH)
        rederive_season(connection, args.season)
        connection.close()
    else:
        main()
