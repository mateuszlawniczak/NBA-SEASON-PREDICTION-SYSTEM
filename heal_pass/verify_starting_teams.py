"""Read-only verification for player_starting_teams table."""
import sqlite3
from collections import Counter

from config import DB_PATH, ALL_SEASONS

SPOT_CHECKS = [
    ("2017-18", "LeBron James", "CLE"),
    ("2018-19", "Kawhi Leonard", "TOR"),
    ("2017-18", "Blake Griffin", "LAC"),
]

OLD_TABLE = "player_starting_teams_25_26"
NEW_TABLE = "player_starting_teams"


def main():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # --- per-season counts ---
    print("=" * 72)
    print("PER-SEASON ROW COUNTS (new table vs player_stats_basic)")
    print("=" * 72)
    print(f"  {'season':<10} {'starting':>10} {'basic':>10} {'delta':>8} {'dup_pid':>8}")
    print("  " + "-" * 50)
    for s in ALL_SEASONS:
        n_start = cur.execute(
            f"SELECT COUNT(*) FROM {NEW_TABLE} WHERE season=?", (s,)
        ).fetchone()[0]
        n_basic = cur.execute(
            "SELECT COUNT(*) FROM player_stats_basic WHERE season=?", (s,)
        ).fetchone()[0]
        n_dup = cur.execute(
            f"""SELECT COUNT(*) FROM (
                SELECT player_id FROM {NEW_TABLE} WHERE season=?
                GROUP BY player_id HAVING COUNT(*)>1
            )""",
            (s,),
        ).fetchone()[0]
        print(f"  {s:<10} {n_start:>10} {n_basic:>10} {n_start-n_basic:>+8} {n_dup:>8}")

    # --- per-team roster sizes (2024-25 sample) ---
    print("\n" + "=" * 72)
    print("PER-TEAM ROSTER SIZES (2024-25, new table)")
    print("=" * 72)
    cur.execute(
        f"SELECT team_abbr, COUNT(*) FROM {NEW_TABLE} WHERE season='2024-25' "
        f"GROUP BY team_abbr ORDER BY team_abbr"
    )
    sizes = cur.fetchall()
    counts = [n for _, n in sizes]
    print(f"  teams={len(sizes)}  min={min(counts)}  max={max(counts)}  avg={sum(counts)/len(counts):.1f}")
    for abbr, n in sizes:
        flag = " !" if n < 10 or n > 25 else ""
        print(f"    {abbr:<5} {n:>3}{flag}")

    # --- era spot checks ---
    print("\n" + "=" * 72)
    print("ERA SPOT CHECKS (debut team)")
    print("=" * 72)
    for season, name, expected in SPOT_CHECKS:
        row = cur.execute(
            f"SELECT player_id, player_name, team_abbr FROM {NEW_TABLE} "
            f"WHERE season=? AND player_name=?",
            (season, name),
        ).fetchone()
        if not row:
            print(f"  {season} {name}: NOT FOUND  (expected {expected})")
        else:
            ok = row[2] == expected
            print(
                f"  {season} {name}: team={row[2]}  expected={expected}  "
                f"{'OK' if ok else 'MISMATCH'}  (pid={row[0]})"
            )

    # --- 2025-26 cross-check vs old table ---
    print("\n" + "=" * 72)
    print("2025-26 CROSS-CHECK (new vs player_starting_teams_25_26)")
    print("=" * 72)
    cur.execute(f"SELECT player_name, team_abbr FROM {OLD_TABLE}")
    old_by_name = {r[0]: r[1] for r in cur.fetchall()}
    cur.execute(
        f"SELECT player_id, player_name, team_abbr FROM {NEW_TABLE} WHERE season='2025-26'"
    )
    new_rows = cur.fetchall()
    new_by_name = {r[1]: r[2] for r in new_rows}

    matches = mismatches = old_only = new_only = 0
    mismatch_details = []
    for name, old_abbr in sorted(old_by_name.items()):
        new_abbr = new_by_name.get(name)
        if new_abbr is None:
            old_only += 1
            mismatch_details.append(("old_only", name, old_abbr, None))
        elif new_abbr == old_abbr:
            matches += 1
        else:
            mismatches += 1
            mismatch_details.append(("mismatch", name, old_abbr, new_abbr))

    for name, abbr in new_by_name.items():
        if name not in old_by_name:
            new_only += 1
            mismatch_details.append(("new_only", name, None, abbr))

    print(f"  old table rows : {len(old_by_name)}")
    print(f"  new table rows : {len(new_rows)}")
    print(f"  name matches   : {matches}")
    print(f"  name mismatches: {mismatches}")
    print(f"  old-only names : {old_only}")
    print(f"  new-only names : {new_only}")

    if mismatch_details:
        print("\n  Differences:")
        for kind, name, old_a, new_a in mismatch_details[:30]:
            print(f"    [{kind}] {name!r}: old={old_a} new={new_a}")
        if len(mismatch_details) > 30:
            print(f"    ... and {len(mismatch_details)-30} more")

    # --- ambiguous identities: same name, different player_ids across new table ---
    print("\n" + "=" * 72)
    print("AMBIGUOUS NAME CHECK (same player_name, different player_id in DB)")
    print("=" * 72)
    cur.execute(
        f"""
        SELECT player_name, COUNT(DISTINCT player_id)
        FROM {NEW_TABLE}
        GROUP BY player_name
        HAVING COUNT(DISTINCT player_id) > 1
        ORDER BY player_name
        """
    )
    amb = cur.fetchall()
    if not amb:
        print("  None — every player_name maps to exactly one player_id globally.")
    else:
        for name, n in amb:
            cur.execute(
                f"SELECT season, player_id, team_abbr FROM {NEW_TABLE} WHERE player_name=?",
                (name,),
            )
            print(f"  {name!r}: {n} distinct player_ids -> {cur.fetchall()}")

    # --- name collisions in old table that new table resolves ---
    print("\n" + "=" * 72)
    print("OLD TABLE NAME-COLLAPSE CHECK (names appearing once in old, multiple pids in new 2025-26)")
    print("=" * 72)
    name_pid = Counter(r[1] for r in new_rows)
    dup_names_new = {n for n, c in Counter(r[1] for r in new_rows).items() if c > 1}
    if dup_names_new:
        print(f"  Duplicate names in new 2025-26: {dup_names_new}")
    else:
        print("  No duplicate names in new 2025-26 (player_id keyed).")

    con.close()


if __name__ == "__main__":
    main()
