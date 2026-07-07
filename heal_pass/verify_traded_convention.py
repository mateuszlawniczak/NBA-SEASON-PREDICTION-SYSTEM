"""Read-only verification: traded-player convention for new seasons."""
import sqlite3
from config import DB_PATH, NEW_SEASONS

TABLES = [
    "player_stats_basic",
    "player_stats_advanced",
    "player_stats_basic_playoffs",
    "player_stats_advanced_playoffs",
]

SPOT = [
    ("2017-18", "Blake Griffin", 58),       # LAC->DET, ~58 gp full season
    ("2017-18", "DeAndre Jordan", 77),      # LAC->DAL
    ("2018-19", "Tobias Harris", 73),       # LAC->PHI
    ("2018-19", "Marc Gasol", 70),          # MEM->TOR
    ("2019-20", "Andre Iguodala", 49),      # GSW->MIA (held out part of year)
    ("2019-20", "Robert Covington", 70),    # MIN->HOU
    ("2019-20", "Marcus Morris Sr.", 62),   # NYK->LAC
]

DERIVED = ["player_simulation_pr", "player_special_effects"]


def table_cols(cur, table):
    return [r[1] for r in cur.execute(f"PRAGMA table_info({table})")]


def main():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # --- 1. Duplicate check ---
    print("=" * 78)
    print("1. DUPLICATE CHECK (players with >1 row per season)")
    print("=" * 78)
    hdr = f"{'table':<38} {'season':<10} {'dup_players':>12} {'extra_rows':>12}"
    print(hdr)
    print("-" * len(hdr))
    dup_details = []
    dup_matrix = {}
    for table in TABLES:
        dup_matrix[table] = {}
        for s in NEW_SEASONS:
            cur.execute(
                f"""
                SELECT player_id, COUNT(*) FROM {table}
                WHERE season = ? GROUP BY season, player_id HAVING COUNT(*) > 1
                """,
                (s,),
            )
            dups = cur.fetchall()
            extra = sum(c - 1 for _, c in dups)
            dup_matrix[table][s] = len(dups)
            print(f"{table:<38} {s:<10} {len(dups):>12} {extra:>12}")
            for pid, cnt in dups:
                cols = table_cols(cur, table)
                sel = ["player_name", "team_id", "team_abbr"]
                if "gp" in cols:
                    sel.append("gp")
                cur.execute(
                    f"SELECT {', '.join(sel)} FROM {table} WHERE season=? AND player_id=? ORDER BY team_id",
                    (s, pid),
                )
                dup_details.append((table, s, pid, cur.fetchall(), sel))

    if dup_details:
        print("\n--- Duplicate player details ---")
        for table, s, pid, rows, sel in dup_details:
            print(f"\n  {table} | {s} | player_id={pid}")
            for r in rows:
                print(f"    {dict(zip(sel, r))}")
    else:
        print("\n  No duplicates found.")

    # --- 2. TOT/combined rows ---
    print("\n" + "=" * 78)
    print("2. TOT / COMBINED ROW CHECK")
    print("=" * 78)
    hdr2 = f"{'table':<38} {'season':<10} {'team_id=0':>10} {'abbr=TOT':>10} {'null_abbr':>10}"
    print(hdr2)
    print("-" * len(hdr2))
    tot_details = []
    tot_matrix = {}
    for table in TABLES:
        tot_matrix[table] = {}
        for s in NEW_SEASONS:
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE season=? AND team_id=0", (s,))
            tot0 = cur.fetchone()[0]
            cur.execute(
                f"SELECT COUNT(*) FROM {table} WHERE season=? AND UPPER(COALESCE(team_abbr,''))='TOT'",
                (s,),
            )
            tot_abbr = cur.fetchone()[0]
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE season=? AND team_abbr IS NULL", (s,))
            null_abbr = cur.fetchone()[0]
            tot_matrix[table][s] = tot0 + tot_abbr
            print(f"{table:<38} {s:<10} {tot0:>10} {tot_abbr:>10} {null_abbr:>10}")
            if tot0 or tot_abbr:
                cur.execute(
                    f"SELECT player_name, player_id, team_id, team_abbr FROM {table} "
                    f"WHERE season=? AND (team_id=0 OR UPPER(COALESCE(team_abbr,''))='TOT')",
                    (s,),
                )
                for r in cur.fetchall():
                    tot_details.append((table, s, r))

    if tot_details:
        print("\n--- TOT/combined row details ---")
        for table, s, r in tot_details:
            print(f"  {table} {s}: name={r[0]!r} pid={r[1]} team_id={r[2]} abbr={r[3]!r}")

    # --- 3. Cross-table consistency ---
    print("\n" + "=" * 78)
    print("3. CROSS-TABLE CONSISTENCY (basic vs advanced)")
    print("=" * 78)
    cross_ok = True
    for s in NEW_SEASONS:
        cur.execute("SELECT player_id FROM player_stats_basic WHERE season=?", (s,))
        basic = set(r[0] for r in cur.fetchall())
        cur.execute("SELECT player_id FROM player_stats_advanced WHERE season=?", (s,))
        adv = set(r[0] for r in cur.fetchall())
        only_basic = basic - adv
        only_adv = adv - basic
        ok = not only_basic and not only_adv
        cross_ok = cross_ok and ok
        print(f"  {s}: basic={len(basic)} advanced={len(adv)}  "
              f"basic-only={len(only_basic)} advanced-only={len(only_adv)}  "
              f"{'OK' if ok else 'MISMATCH'}")

    # --- 4. Spot checks ---
    print("\n" + "=" * 78)
    print("4. TRADED-PLAYER SPOT CHECK")
    print("=" * 78)
    spot_results = []
    for season, name, exp_gp in SPOT:
        print(f"\n--- {name} ({season}) — expected ~{exp_gp} gp full season ---")
        found_basic = None
        for table in TABLES:
            cols = table_cols(cur, table)
            base_cols = ["player_id", "player_name", "team_id", "team_abbr"]
            if "gp" in cols:
                base_cols.append("gp")
            if "pts" in cols:
                base_cols.extend(["pts", "reb", "ast"])
            cur.execute(
                f"SELECT {', '.join(base_cols)} FROM {table} WHERE season=? AND player_name=?",
                (season, name),
            )
            rows = cur.fetchall()
            if not rows:
                print(f"  {table}: NOT FOUND")
                continue
            print(f"  {table}: {len(rows)} row(s)")
            for r in rows:
                d = dict(zip(base_cols, r))
                print(f"    {d}")
                if table == "player_stats_basic":
                    found_basic = d

        if found_basic:
            n_rows = 1  # we only get here if found
            cur.execute(
                "SELECT COUNT(*) FROM player_stats_basic WHERE season=? AND player_name=?",
                (season, name),
            )
            n_rows = cur.fetchone()[0]
            gp = found_basic.get("gp")
            team = found_basic.get("team_abbr")
            spot_results.append(
                (season, name, n_rows, gp, team, exp_gp)
            )

    # --- 5. Derived tables ---
    print("\n" + "=" * 78)
    print("5. DERIVED TABLES (player_simulation_pr, player_special_effects)")
    print("=" * 78)
    derived_matrix = {}
    for t in DERIVED:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (t,))
        if not cur.fetchone():
            print(f"\n  {t}: table does not exist")
            continue
        cols = table_cols(cur, t)
        cur.execute(f"SELECT DISTINCT season FROM {t} ORDER BY season")
        seasons = [r[0] for r in cur.fetchall()]
        overlap = [s for s in NEW_SEASONS if s in seasons]
        print(f"\n  {t}: seasons={seasons}")
        derived_matrix[t] = {}
        if not overlap:
            print("    none of the three new seasons present yet")
            continue
        print(f"    {'season':<10} {'rows':>8} {'pid_dups':>10} {'name_dups':>10}")
        for s in overlap:
            cur.execute(f"SELECT COUNT(*) FROM {t} WHERE season=?", (s,))
            n = cur.fetchone()[0]
            cur.execute(
                f"SELECT COUNT(*) FROM (SELECT player_id FROM {t} WHERE season=? "
                f"GROUP BY player_id HAVING COUNT(*)>1)",
                (s,),
            )
            pid_dups = cur.fetchone()[0]
            name_dups = 0
            if "player_name" in cols:
                cur.execute(
                    f"SELECT COUNT(*) FROM (SELECT player_name FROM {t} WHERE season=? "
                    f"GROUP BY player_name HAVING COUNT(*)>1)",
                    (s,),
                )
                name_dups = cur.fetchone()[0]
            derived_matrix[t][s] = pid_dups
            print(f"    {s:<10} {n:>8} {pid_dups:>10} {name_dups:>10}")

    # --- Verdict ---
    print("\n" + "=" * 78)
    print("VERDICT")
    print("=" * 78)
    for s in NEW_SEASONS:
        devs = []
        for table in TABLES:
            if dup_matrix[table][s] > 0:
                devs.append(f"{table}: {dup_matrix[table][s]} duplicate players")
            if tot_matrix[table][s] > 0:
                devs.append(f"{table}: {tot_matrix[table][s]} TOT/combined rows")
        if not cross_ok:
            devs.append("basic/advanced player_id mismatch")
        if devs:
            print(f"  {s}: deviations found: {'; '.join(devs)}")
        else:
            print(f"  {s}: matches existing convention")

    con.close()


if __name__ == "__main__":
    main()
