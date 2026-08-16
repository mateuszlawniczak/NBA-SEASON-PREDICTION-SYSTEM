"""
diagnose_continuity.py
----------------------
Read-only diagnosis of team_playoff_projection.continuity_mult flips.
Talks only to nba_data.diag.db (and the pre-batch backup, attached read-only).
Never opens the live nba_data.db.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import subprocess
import sys

from season_utils import source_season as prior_season

import build_team_playoff_pr as btp

HERE = os.path.dirname(os.path.abspath(__file__))
DIAG_DB = os.path.join(HERE, "nba_data.diag.db")
BACKUP_DB = os.path.join(HERE, "nba_data.backup_batch_20260816_230952.db")

FLIPPED = [
    ("2020-21", "BOS", 0.95, 1.05),
    ("2020-21", "CHA", 1.00, 0.95),
    ("2020-21", "MIA", 1.00, 0.95),
    ("2021-22", "CHA", 1.00, 0.95),
    ("2021-22", "SAC", 1.00, 0.95),
    ("2022-23", "CLE", 1.00, 0.95),
    ("2022-23", "DEN", 0.95, 1.00),
    ("2022-23", "MEM", 1.00, 0.95),
]

STABLE = [
    ("2018-19", "BOS"),
    ("2019-20", "LAL"),
    ("2023-24", "BOS"),
    ("2024-25", "OKC"),
    ("2025-26", "NYK"),
]


def connect(path: str) -> sqlite3.Connection:
    if os.path.abspath(path) == os.path.abspath(os.path.join(HERE, "nba_data.db")):
        raise RuntimeError("refusing to open the live database")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def dump_one(con: sqlite3.Connection, target: str, team: str) -> dict:
    source = prior_season(target)
    top_two = btp._source_top_two(con, team, source, target)
    source_ids = btp._roster_player_ids(con, source, team)
    target_ids = btp._roster_player_ids(con, target, team)
    both = set(source_ids) & set(target_ids)
    denom = len(target_ids)
    numer = len(both)
    overlap = (numer / denom) if denom else 0.0

    statuses = []
    for pid, name, pr in top_two:
        target_team = btp._target_team_for_player(con, pid, target)
        status = btp._top2_target_status(con, pid, team, target)
        statuses.append(
            {
                "player_id": pid,
                "player_name": name,
                "pr": pr,
                "target_team": target_team,
                "status": status,
            }
        )

    result = btp.evaluate_continuity(con, team, target, source)
    return {
        "team": team,
        "target": target,
        "source": source,
        "top_two": statuses,
        "overlap_numer": numer,
        "overlap_denom": denom,
        "overlap": overlap,
        "rule": result["rule"],
        "tier": result["tier"],
        "mult": result["mult"],
        "source_roster_n": len(source_ids),
        "target_roster_n": len(target_ids),
    }


def fmt_dump(d: dict) -> str:
    lines = [
        f"{d['target']} {d['team']}  (source {d['source']})",
        f"  top-2:",
    ]
    for p in d["top_two"]:
        lines.append(
            f"    id={p['player_id']}  {p['player_name']!r}  pr={p['pr']:.10f}  "
            f"target_team={p['target_team']!r}  status={p['status']}"
        )
    lines.append(
        f"  overlap = {d['overlap_numer']}/{d['overlap_denom']} = "
        f"{d['overlap']!r}  (float={d['overlap']:.17f})"
    )
    lines.append(
        f"  branch={d['rule']}  tier={d['tier']}  mult={d['mult']}"
    )
    return "\n".join(lines)


def dumps_equal(a: dict, b: dict) -> bool:
    return (
        a["top_two"] == b["top_two"]
        and a["overlap"] == b["overlap"]
        and a["rule"] == b["rule"]
        and a["mult"] == b["mult"]
    )


def step1_in_process() -> None:
    print("=" * 78)
    print("STEP 1 — evaluate_continuity intermediates (diag DB, twice in-process)")
    print("=" * 78)
    con = connect(DIAG_DB)
    first = []
    second = []
    for target, team, old, new in FLIPPED:
        d1 = dump_one(con, target, team)
        d2 = dump_one(con, target, team)
        first.append(d1)
        second.append(d2)
        print()
        print(fmt_dump(d1))
        print(f"  stored flip: {old} -> {new}")
        print(f"  second call identical: {dumps_equal(d1, d2)}")
    print()
    print(
        "in-process pair identical for all 8:",
        all(dumps_equal(a, b) for a, b in zip(first, second)),
    )
    con.close()


def step1_cross_process() -> None:
    print()
    print("=" * 78)
    print("STEP 1b — two separate processes")
    print("=" * 78)
    cmd = [sys.executable, os.path.abspath(__file__), "--dump-once"]
    p1 = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE)
    p2 = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE)
    print("--- process A ---")
    print(p1.stdout)
    print("--- process B ---")
    print(p2.stdout)
    print("stderr A:", p1.stderr.strip() or "(none)")
    print("stderr B:", p2.stderr.strip() or "(none)")
    print("stdout identical across processes:", p1.stdout == p2.stdout)


def dump_once() -> None:
    con = connect(DIAG_DB)
    for target, team, _old, _new in FLIPPED:
        print(fmt_dump(dump_one(con, target, team)))
        print()
    con.close()


def audit_duplicates(con: sqlite3.Connection, label: str) -> None:
    print()
    print("=" * 78)
    print(f"STEP 2a — ambiguous row selection  [{label}]")
    print("=" * 78)

    print("\nplayer_starting_teams: duplicate (season, player_id) rows")
    dups = con.execute(
        """
        SELECT season, player_id, player_name, COUNT(*) AS n,
               GROUP_CONCAT(DISTINCT team_abbr) AS teams
        FROM player_starting_teams
        GROUP BY season, player_id
        HAVING COUNT(*) > 1
        ORDER BY season, player_id
        """
    ).fetchall()
    print(f"  groups: {len(dups)}")
    for r in dups:
        print(f"    {r['season']} id={r['player_id']} {r['player_name']!r} "
              f"n={r['n']} teams={r['teams']}")

    print("\nplayer_starting_teams: duplicate (season, player_id, team_abbr)")
    d2 = con.execute(
        """
        SELECT season, player_id, team_abbr, COUNT(*) AS n
        FROM player_starting_teams
        GROUP BY season, player_id, team_abbr
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    print(f"  groups: {len(d2)}")
    for r in d2[:20]:
        print(f"    {dict(r)}")

    print("\nFor the 8 flipped team-seasons, any player with >1 starting-team row "
          "in source OR target season:")
    for target, team, _o, _n in FLIPPED:
        source = prior_season(target)
        rows = con.execute(
            """
            SELECT pst.season, pst.player_id, pst.player_name, pst.team_abbr
            FROM player_starting_teams pst
            WHERE pst.player_id IN (
                SELECT player_id FROM player_starting_teams
                WHERE (season = ? AND team_abbr = ?)
                   OR (season = ? AND team_abbr = ?)
            )
            AND pst.season IN (?, ?)
            ORDER BY pst.player_id, pst.season, pst.team_abbr
            """,
            (source, team, target, team, source, target),
        ).fetchall()
        by_id: dict[tuple, list] = {}
        for r in rows:
            by_id.setdefault((r["season"], r["player_id"]), []).append(r)
        multi = {k: v for k, v in by_id.items() if len(v) > 1}
        print(f"  {target} {team}: multi-row player-seasons = {len(multi)}")
        for (seas, pid), vs in multi.items():
            print(f"     {seas} id={pid} " +
                  ", ".join(f"{x['player_name']!r}@{x['team_abbr']}" for x in vs))

    print("\n_source_top_two join cardinality: name collisions in ULTIMATE_PR "
          "and player_simulation_pr for source season of each flip")
    for target, team, _o, _n in FLIPPED:
        source = prior_season(target)
        print(f"\n  {target} {team} source={source}")
        # raw join without LIMIT — how many rows, and is ORDER BY unique?
        rows = con.execute(
            """
            SELECT pst.player_id, pst.player_name,
                   COALESCE(up.pr, psp.base_pr, 0.0) AS pr,
                   up.pr AS up_pr, psp.base_pr AS psp_pr,
                   (SELECT COUNT(*) FROM ULTIMATE_PR u
                    WHERE u.player_name = pst.player_name AND u.season = ?) AS up_matches,
                   (SELECT COUNT(*) FROM player_simulation_pr p
                    WHERE p.player_name = pst.player_name AND p.season = ?) AS psp_matches
            FROM player_starting_teams AS pst
            LEFT JOIN ULTIMATE_PR AS up
              ON up.player_name = pst.player_name AND up.season = ?
            LEFT JOIN player_simulation_pr AS psp
              ON psp.player_name = pst.player_name AND psp.season = ?
            WHERE pst.season = ? AND pst.team_abbr = ?
            ORDER BY pr DESC, pst.player_name ASC
            """,
            (source, source, source, source, source, team),
        ).fetchall()
        print(f"    joined rows: {len(rows)}")
        multi_join = [r for r in rows if r["up_matches"] > 1 or r["psp_matches"] > 1]
        if multi_join:
            for r in multi_join:
                print(f"    COLLISION {r['player_name']!r} id={r['player_id']} "
                      f"up_matches={r['up_matches']} psp_matches={r['psp_matches']} "
                      f"pr={r['pr']}")
        else:
            print("    no name that matches >1 ULTIMATE_PR or player_simulation_pr row")

        # uniqueness of ORDER BY among top few
        print("    top 5 by (pr DESC, name ASC):")
        seen = []
        for r in rows[:5]:
            print(f"      pr={r['pr']:.10f}  {r['player_name']!r} id={r['player_id']}")
            seen.append((r["pr"], r["player_name"]))
        if len(seen) >= 2 and seen[1][0] == seen[0][0]:
            print("    NOTE: top-2 tied on PR; name is the only tiebreak")

    print("\n_target_team_for_player: players whose target-season starting-team "
          "query can return more than one row (no ORDER BY, LIMIT 1)")
    for target, team, _o, _n in FLIPPED:
        source = prior_season(target)
        top_two = btp._source_top_two(con, team, source, target)
        print(f"  {target} {team} top-2:")
        for pid, name, pr in top_two:
            hits = con.execute(
                """
                SELECT team_abbr, player_name, rowid
                FROM player_starting_teams
                WHERE season = ? AND player_id = ?
                ORDER BY rowid
                """,
                (target, pid),
            ).fetchall()
            unordered = con.execute(
                """
                SELECT team_abbr FROM player_starting_teams
                WHERE season = ? AND player_id = ? LIMIT 1
                """,
                (target, pid),
            ).fetchone()
            print(f"    {name!r} id={pid}  matching rows={len(hits)}  "
                  f"LIMIT 1 without ORDER BY -> {unordered[0] if unordered else None}  "
                  f"rowids={[r['rowid'] for r in hits]} teams={[r['team_abbr'] for r in hits]}")

    print("\n_unknown_player_reason: player_stats_basic ORDER BY gp DESC LIMIT 1 "
          "— GP ties for assumed_kept players")
    for target, team, _o, _n in FLIPPED:
        source = prior_season(target)
        d = dump_one(con, target, team)
        for p in d["top_two"]:
            if p["status"] != "assumed_kept":
                continue
            ties = con.execute(
                """
                SELECT player_id, player_name, team_abbr, gp, rowid
                FROM player_stats_basic
                WHERE season = ? AND (player_id = ? OR player_name = ?)
                ORDER BY gp DESC, rowid
                """,
                (target, p["player_id"], p["player_name"]),
            ).fetchall()
            print(f"  {target} {team} {p['player_name']!r}: {len(ties)} stats rows")
            for r in ties:
                print(f"     gp={r['gp']} {r['team_abbr']} rowid={r['rowid']}")


def step2b_name_joins(con: sqlite3.Connection) -> None:
    print()
    print("=" * 78)
    print("STEP 2b — name-based joins")
    print("=" * 78)
    for target, team, _o, _n in FLIPPED:
        source = prior_season(target)
        print(f"\n  {target} {team} source={source}")
        names = con.execute(
            """
            SELECT player_name, COUNT(*) AS n,
                   GROUP_CONCAT(player_id) AS ids
            FROM player_starting_teams
            WHERE season = ? AND team_abbr = ?
            GROUP BY player_name
            HAVING COUNT(*) > 1
            """,
            (source, team),
        ).fetchall()
        print(f"    duplicate names on source roster: {len(names)}")
        for r in names:
            print(f"      {dict(r)}")

        collisions = con.execute(
            """
            SELECT pst.player_name,
                   COUNT(DISTINCT pst.player_id) AS roster_ids,
                   COUNT(DISTINCT up.rowid) AS up_rows,
                   COUNT(DISTINCT psp.rowid) AS psp_rows
            FROM player_starting_teams pst
            LEFT JOIN ULTIMATE_PR up
              ON up.player_name = pst.player_name AND up.season = ?
            LEFT JOIN player_simulation_pr psp
              ON psp.player_name = pst.player_name AND psp.season = ?
            WHERE pst.season = ? AND pst.team_abbr = ?
            GROUP BY pst.player_name
            HAVING up_rows > 1 OR psp_rows > 1 OR roster_ids > 1
            """,
            (source, source, source, team),
        ).fetchall()
        print(f"    names with colliding PR join rows: {len(collisions)}")
        for r in collisions:
            print(f"      {dict(r)}")

        # league-wide duplicate names in source ULTIMATE_PR
        up_dups = con.execute(
            """
            SELECT player_name, COUNT(*) n
            FROM ULTIMATE_PR WHERE season = ?
            GROUP BY player_name HAVING COUNT(*) > 1
            """,
            (source,),
        ).fetchall()
        print(f"    ULTIMATE_PR duplicate names in {source}: {len(up_dups)}")
        if up_dups[:8]:
            for r in up_dups[:8]:
                print(f"      {r['player_name']!r} n={r['n']}")


def step2c_thresholds(con: sqlite3.Connection) -> None:
    print()
    print("=" * 78)
    print("STEP 2c — overlap vs 0.50 / 0.70 boundaries")
    print("=" * 78)
    print(f"  CONTINUITY_OVERLAP_HIGH = {btp.CONTINUITY_OVERLAP_HIGH}")
    print(f"  CONTINUITY_OVERLAP_DEFAULT_MIN = {btp.CONTINUITY_OVERLAP_DEFAULT_MIN}")
    for target, team, old, new in FLIPPED:
        d = dump_one(con, target, team)
        ov = d["overlap"]
        on_high = ov == btp.CONTINUITY_OVERLAP_HIGH
        on_def = ov == btp.CONTINUITY_OVERLAP_DEFAULT_MIN
        print(
            f"  {target} {team}: {d['overlap_numer']}/{d['overlap_denom']} = "
            f"{ov:.17f}  exact_0.70={on_high} exact_0.50={on_def}  "
            f"branch={d['rule']} mult={d['mult']}  stored {old}->{new}"
        )


def step2d_batch_order() -> None:
    print()
    print("=" * 78)
    print("STEP 2d — batch-order carry-over")
    print("=" * 78)
    print(
        "evaluate_continuity reads player_starting_teams / ULTIMATE_PR / "
        "player_simulation_pr. It does NOT read team_playoff_projection, so "
        "season N cannot inherit season N-1's continuity_mult directly."
    )
    print(
        "It DOES read ULTIMATE_PR(source_season). The batch rewrites "
        "ULTIMATE_PR for target=N-1 immediately before scoring target=N, "
        "so source PR for N is the just-rewritten table."
    )
    if not os.path.exists(BACKUP_DB):
        print(f"  backup not found: {BACKUP_DB}")
        return

    live = connect(DIAG_DB)
    bak = connect(BACKUP_DB)
    print("\n  evaluate_continuity on PRE-batch backup vs POST-batch diag:")
    for target, team, old, new in FLIPPED:
        a = dump_one(bak, target, team)
        b = dump_one(live, target, team)
        same = dumps_equal(a, b)
        print(
            f"  {target} {team}: backup mult={a['mult']} branch={a['rule']}  "
            f"diag mult={b['mult']} branch={b['rule']}  identical={same}  "
            f"stored {old}->{new}"
        )
        if not same:
            print("    backup top-2:")
            for p in a["top_two"]:
                print(f"      {p}")
            print("    diag top-2:")
            for p in b["top_two"]:
                print(f"      {p}")
            print(
                f"    backup overlap={a['overlap']!r}  diag overlap={b['overlap']!r}"
            )
    live.close()
    bak.close()

    print(
        "\n  Isolated vs in-batch: this script cannot re-run the pipeline "
        "(out of scope). Comparing backup vs diag already is 'after N-1 was "
        "rewritten' vs 'the tables as they were when run 1 was stored'. "
        "If evaluate_continuity agrees on both copies, the flip happened at "
        "WRITE time from a non-deterministic query, and both copies now "
        "settle on the same answer. If they disagree, contents that looked "
        "equal still change the result — i.e. physical order / join extras."
    )


def rowid_fingerprint(con: sqlite3.Connection, sql: str, params: tuple = ()) -> str:
    rows = con.execute(sql, params).fetchall()
    return hashlib.sha256(repr([tuple(r) for r in rows]).encode()).hexdigest()


def step3_physical_order() -> None:
    print()
    print("=" * 78)
    print("STEP 3 — physical rowid order, not just contents")
    print("=" * 78)
    if not os.path.exists(BACKUP_DB):
        print("  backup not found")
        return
    live = connect(DIAG_DB)
    bak = connect(BACKUP_DB)

    checks = [
        (
            "player_starting_teams",
            "SELECT rowid, season, player_id, team_abbr, player_name "
            "FROM player_starting_teams ORDER BY rowid",
            "SELECT season, player_id, team_abbr, player_name "
            "FROM player_starting_teams ORDER BY season, player_id, team_abbr, player_name",
        ),
        (
            "ULTIMATE_PR",
            "SELECT rowid, season, player_name, pr FROM ULTIMATE_PR ORDER BY rowid",
            "SELECT season, player_name, pr FROM ULTIMATE_PR "
            "ORDER BY season, player_name, pr",
        ),
        (
            "player_simulation_pr",
            "SELECT rowid, * FROM player_simulation_pr ORDER BY rowid",
            "SELECT * FROM player_simulation_pr ORDER BY season, player_name",
        ),
        (
            "player_stats_basic",
            "SELECT rowid, season, player_id, player_name, team_abbr, gp "
            "FROM player_stats_basic ORDER BY rowid",
            "SELECT season, player_id, player_name, team_abbr, gp "
            "FROM player_stats_basic ORDER BY season, player_id, team_abbr, gp",
        ),
    ]

    for name, by_rowid, by_values in checks:
        live_v = rowid_fingerprint(live, by_values)
        bak_v = rowid_fingerprint(bak, by_values)
        live_r = rowid_fingerprint(live, by_rowid)
        bak_r = rowid_fingerprint(bak, by_rowid)
        print(f"\n  {name}")
        print(f"    contents (value-sorted) identical: {live_v == bak_v}")
        print(f"    rowid order identical:             {live_r == bak_r}")
        if live_v == bak_v and live_r != bak_r:
            print("    CONFIRMED: same data, different physical order")
            # show first differing rowid pair for relevant seasons
            a = bak.execute(by_rowid).fetchall()
            b = live.execute(by_rowid).fetchall()
            diffs = 0
            for x, y in zip(a, b):
                if tuple(x) != tuple(y):
                    diffs += 1
                    if diffs <= 3:
                        print(f"      backup {tuple(x)}")
                        print(f"      diag   {tuple(y)}")
            print(f"    pairwise rowid-stream diffs (zip, min length): {diffs}")
        elif live_v != bak_v:
            print("    contents themselves differ (not just order)")

    print("\n  Per source-season ULTIMATE_PR rowid stream for flipped sources:")
    for source in ("2019-20", "2020-21", "2021-22"):
        q = ("SELECT rowid, player_name, pr FROM ULTIMATE_PR "
             "WHERE season = ? ORDER BY rowid")
        qv = ("SELECT player_name, pr FROM ULTIMATE_PR "
              "WHERE season = ? ORDER BY player_name, pr")
        print(
            f"    {source}: contents={rowid_fingerprint(live, qv, (source,)) == rowid_fingerprint(bak, qv, (source,))}"
            f"  rowid_order={rowid_fingerprint(live, q, (source,)) == rowid_fingerprint(bak, q, (source,))}"
        )

    print("\n  Per-team player_starting_teams rowid stream for flipped teams:")
    for target, team, _o, _n in FLIPPED:
        for seas in (prior_season(target), target):
            q = ("SELECT rowid, player_id, team_abbr FROM player_starting_teams "
                 "WHERE season = ? AND team_abbr = ? ORDER BY rowid")
            qv = ("SELECT player_id, team_abbr FROM player_starting_teams "
                  "WHERE season = ? AND team_abbr = ? ORDER BY player_id, team_abbr")
            same_c = rowid_fingerprint(live, qv, (seas, team)) == rowid_fingerprint(
                bak, qv, (seas, team)
            )
            same_r = rowid_fingerprint(live, q, (seas, team)) == rowid_fingerprint(
                bak, q, (seas, team)
            )
            flag = "" if same_r else "  ORDER CHANGED"
            print(f"    {seas} {team}: contents={same_c} rowid_order={same_r}{flag}")

    live.close()
    bak.close()


def other_nondeterminism(con: sqlite3.Connection) -> None:
    print()
    print("=" * 78)
    print("OTHER queries in this file with LIMIT / fetchone and no unique ORDER BY")
    print("=" * 78)
    print(
        "  _target_team_for_player: LIMIT 1, no ORDER BY  "
        "(player_starting_teams by season+player_id)"
    )
    print(
        "  _unknown_player_reason stats: ORDER BY gp DESC LIMIT 1  "
        "(tied GP is unordered)"
    )
    print(
        "  _unknown_player_reason pst: LIMIT 1, no ORDER BY"
    )
    print(
        "  _source_top_two: ORDER BY pr DESC, player_name ASC LIMIT 2  "
        "— unique iff (pr, name) is unique among joined rows. "
        "A name join that duplicates a row can insert a twin with the same "
        "(pr, name) and a different player_id; SQLite then picks arbitrarily."
    )
    print(
        "  main() coach join is by TRIM(coach_name)=TRIM(cs.name) with no "
        "dedup beyond pandas; playstyles.drop_duplicates(subset=['team_abbr']) "
        "keeps the first pandas row — physical order dependent if a team has "
        "two playstyle rows."
    )
    n = con.execute(
        """
        SELECT season, team_abbr, COUNT(*) n
        FROM team_playstyle_data
        GROUP BY season, team_abbr HAVING COUNT(*) > 1
        """
    ).fetchall()
    print(f"  team_playstyle_data duplicate (season, team): {len(n)}")
    n2 = con.execute(
        """
        SELECT season, team_abbr, COUNT(*) n
        FROM team_coaches
        GROUP BY season, team_abbr HAVING COUNT(*) > 1
        """
    ).fetchall()
    print(f"  team_coaches duplicate (season, team): {len(n2)}")
    n3 = con.execute(
        """
        SELECT season, player_name, COUNT(*) n
        FROM ultimate_playoff_pr
        GROUP BY season, player_name HAVING COUNT(*) > 1
        """
    ).fetchall()
    print(f"  ultimate_playoff_pr duplicate (season, player_name): {len(n3)}")
    for r in n3[:10]:
        print(f"    {dict(r)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump-once", action="store_true")
    args = parser.parse_args()
    if args.dump_once:
        dump_once()
        return

    if not os.path.exists(DIAG_DB):
        raise SystemExit(f"missing {DIAG_DB} — copy nba_data.db first")

    step1_in_process()
    step1_cross_process()
    con = connect(DIAG_DB)
    audit_duplicates(con, "diag")
    step2b_name_joins(con)
    step2c_thresholds(con)
    other_nondeterminism(con)
    con.close()
    step2d_batch_order()
    step3_physical_order()


if __name__ == "__main__":
    main()
