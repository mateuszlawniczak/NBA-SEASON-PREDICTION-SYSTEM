"""
backfill_playoff_result_legacy.py
---------------------------------
Fill NULL playoff_result in team_stats_playoffs for 2017-18, 2018-19, 2019-20
using the playoff-bracket wins already stored in the table.

Same wins → label mapping as migrate_team_playoffs_v2.py / backfill_prev_team_playoffs.py.
No API calls — DB only.
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys

DB_PATH = "nba_data.db"
TARGET_SEASONS = ("2017-18", "2018-19", "2019-20")

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


if __name__ == "__main__":
    main()
