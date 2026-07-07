"""Cross-season consistency audit — report only, no DB writes."""
import sqlite3
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB_PATH = Path(__file__).resolve().parent.parent / "nba_data.db"
SEASONS = [
    "2017-18", "2018-19", "2019-20",
    "2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26",
]
SEAM = ("2019-20", "2020-21")

BASIC_COLS = [
    "gp", "mpg", "total_minutes", "pts", "reb", "ast", "tov", "stl", "blk",
    "fg_pct", "fg3_pct", "ft_pct", "usg_pct",
]
SHOOT_COLS = ["ts_pct", "fg_pct_rim", "fg_pct_mid", "fg3_pct_corner", "fg3_pct_above_break"]
TRACK_COLS = ["contested_shot_pct", "open_shot_pct"]
DEF_COLS = [
    "deflections", "opp_fg_pct_at_rim", "opp_fg3_pct_contested",
    "opp_fg_at_rim_contested", "opp_fg3_contests_attempts",
]

# Fetcher provenance notes (from repo scripts / heal_pass)
PROVENANCE = {
    "player_stats_basic": (
        "fetch_player_basic.py — LeagueDashPlayerStats PerGame Base/Advanced + Bio. "
        "Official SEASONS: 2020-21 to 2025-26. 2017-20 loaded separately (same endpoints, "
        "not in script SEASONS list); gs/position/years_in_league backfilled via "
        "hydrate_player_basic.py + heal_pass/fix2-4."
    ),
    "player_stats_advanced": (
        "fetch_player_advanced.py — 7 endpoints (Per100 Base, PerGame Adv/Defense, "
        "ShotLocations, PtDefend rim+3PT, Hustle). Official SEASONS: 2020-21 to 2025-26. "
        "2017-20 loaded separately; contested/open re-healed via heal_pass/fix5_shots.py "
        "(patch_open_shots recipe); off_reb via fix1; opp_* gaps via fix6."
    ),
    "player_stats_basic_playoffs": (
        "fetch_playoff_basic.py — LeagueDashPlayerStats Totals Base + Bio, Playoffs+PlayIn merge. "
        "Official SEASONS: 2020-21 to 2025-26. 2017-20 loaded separately."
    ),
    "player_stats_advanced_playoffs": (
        "fetch_playoff_advanced.py — same 7-endpoint pattern as regular advanced. "
        "Official SEASONS: 2020-21 to 2025-26. 2017-20 loaded separately."
    ),
    "player_starting_teams": (
        "fetch_player_starting_teams.py — LeagueGameLog Regular Season player rows, "
        "all nine seasons from identical method."
    ),
    "team_stats": "fetch_team_stats.py — LeagueDashTeamStats + Standings. SEASONS: 2020-21 to 2025-26 only.",
    "league_stats": "fetch_league_stats.py — LeagueDashTeamStats league avgs. SEASONS: 2020-21 to 2025-26 only.",
    "team_stats_playoffs": "fetch_team_playoffs.py — team playoff stats. Check season list separately.",
}


def season_stats(cur, table, col, season):
    cur.execute(
        f"""
        SELECT
          COUNT(*) AS n,
          COUNT({col}) AS nn,
          ROUND(AVG({col}), 4),
          ROUND(MIN({col}), 4),
          ROUND(MAX({col}), 4),
          SUM(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END),
          SUM(CASE WHEN {col} > 1 THEN 1 ELSE 0 END),
          SUM(CASE WHEN {col} > 100 THEN 1 ELSE 0 END)
        FROM {table} WHERE season=?
        """,
        (season,),
    )
    return cur.fetchone()


def pct_null(n, nn):
    return round(100.0 * (n - nn) / n, 1) if n else 0.0


def seam_delta(stats_by_season, col_key):
    a = stats_by_season[SEAM[0]].get(col_key)
    b = stats_by_season[SEAM[1]].get(col_key)
    if a is None or b is None or a == 0:
        return None
    return round((b - a) / abs(a) * 100, 1)


def flag_seam_jump(mean_a, mean_b, threshold_pct=25):
    if mean_a is None or mean_b is None or mean_a == 0:
        return False
    return abs((mean_b - mean_a) / mean_a * 100) >= threshold_pct


def print_family(title, table, cols, extra_queries=None):
    print("\n" + "=" * 100)
    print(title)
    print("=" * 100)
    hdr = f"  {'col':<22} {'season':<10} {'mean':>8} {'min':>8} {'max':>8} {'null%':>7} {'cov%':>7}"
    print(hdr)
    print("  " + "-" * 78)

    anomalies = []
    stats_by_season = {s: {} for s in SEASONS}

    for col in cols:
        prev_mean = None
        for s in SEASONS:
            n, nn, mean, mn, mx, nulls, gt1, gt100 = season_stats(cur, table, col, s)
            null_pct = pct_null(n, nn)
            cov = round(100.0 * nn / n, 1) if n else 0
            mean_s = mean if mean is not None else None
            stats_by_season[s][col] = mean_s
            flags = []
            if gt1 and col not in ("gp", "mpg", "total_minutes", "pts", "reb", "ast", "tov", "stl", "blk",
                                   "deflections", "opp_fg_at_rim_contested", "opp_fg3_contests_attempts"):
                flags.append(f">{1}: {gt1}")
            if gt100:
                flags.append(f">100: {gt100}")
            if prev_mean is not None and mean_s is not None and prev_mean != 0:
                jump = abs((mean_s - prev_mean) / prev_mean)
                if jump > 0.5:
                    flags.append(f"step from prev {jump*100:.0f}%")
            if s == SEAM[1] and prev_mean is not None and flag_seam_jump(prev_mean, mean_s):
                flags.append("SEAM JUMP")
            flag_str = ("  ! " + ", ".join(flags)) if flags else ""
            print(f"  {col:<22} {s:<10} {str(mean_s):>8} {str(mn):>8} {str(mx):>8} {null_pct:>6.1f}% {cov:>6.1f}%{flag_str}")
            prev_mean = mean_s
            if flags:
                anomalies.append((col, s, flags))

    if extra_queries:
        extra_queries()

    # tracking sum
    if set(TRACK_COLS).issubset(cols) or cols == TRACK_COLS:
        print("\n  contested + open sum (both non-null rows):")
        for s in SEASONS:
            cur.execute(
                """
                SELECT ROUND(AVG(contested_shot_pct + open_shot_pct), 4), COUNT(*)
                FROM player_stats_advanced
                WHERE season=? AND contested_shot_pct IS NOT NULL AND open_shot_pct IS NOT NULL
                """,
                (s,),
            )
            avg_sum, cnt = cur.fetchone()
            print(f"    {s}: avg_sum={avg_sum}  n={cnt}")

    return anomalies


def main():
    global cur
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    print("=" * 100)
    print("CROSS-SEASON CONSISTENCY AUDIT — player_stats_basic / player_stats_advanced")
    print("=" * 100)

    # --- provenance ---
    print("\n## 3. FETCHER PROVENANCE\n")
    for table, note in PROVENANCE.items():
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        exists = cur.fetchone()
        if not exists:
            print(f"  {table}: NOT IN DB")
            continue
        ss = [r[0] for r in cur.execute(f"SELECT DISTINCT season FROM {table} ORDER BY season")]
        print(f"  {table}")
        print(f"    seasons in DB: {ss}")
        print(f"    source: {note}\n")

    all_anomalies = []

    # --- basic ---
    all_anomalies += print_family(
        "## 1a. BASIC (player_stats_basic) — expect per-game scale, low nulls",
        "player_stats_basic",
        BASIC_COLS,
    )

    # --- shooting ---
    all_anomalies += print_family(
        "## 1b. ADVANCED SHOOTING (player_stats_advanced) — expect 0–1 ratios",
        "player_stats_advanced",
        SHOOT_COLS,
    )

    # --- tracking ---
    all_anomalies += print_family(
        "## 1c. TRACKING SPLIT (player_stats_advanced)",
        "player_stats_advanced",
        TRACK_COLS,
    )

    # --- defensive ---
    all_anomalies += print_family(
        "## 1d. DEFENSIVE TRACKING (player_stats_advanced)",
        "player_stats_advanced",
        DEF_COLS,
    )

    # --- unit/scale explicit checks ---
    print("\n" + "=" * 100)
    print("## 2. UNIT / SCALE SANITY (whole DB, all nine seasons)")
    print("=" * 100)
    ratio_cols = SHOOT_COLS + TRACK_COLS + ["opp_fg_pct_at_rim", "opp_fg3_pct_contested", "fg_pct", "fg3_pct", "ft_pct", "usg_pct"]
    for col in ratio_cols:
        table = "player_stats_basic" if col in BASIC_COLS else "player_stats_advanced"
        cur.execute(
            f"""
            SELECT season,
                   ROUND(AVG({col}),4), ROUND(MAX({col}),4),
                   SUM(CASE WHEN {col}>1 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN {col}>10 THEN 1 ELSE 0 END)
            FROM {table}
            WHERE season IN ({','.join('?'*len(SEASONS))})
            GROUP BY season ORDER BY season
            """,
            SEASONS,
        )
        bad = []
        for s, avg, mx, gt1, gt10 in cur.fetchall():
            if gt1 or gt10:
                bad.append(f"{s}: max={mx} gt1={gt1} gt10={gt10}")
        status = "OK" if not bad else "ANOMALY: " + "; ".join(bad)
        print(f"  {col:<28} {status}")

    # --- seam test summary ---
    print("\n" + "=" * 100)
    print("## 4. SEAM TEST (2019-20 to 2020-21 mean % change)")
    print("=" * 100)
    seam_cols = BASIC_COLS + SHOOT_COLS + TRACK_COLS + DEF_COLS
    print(f"  {'column':<28} {'2019-20 mean':>14} {'2020-21 mean':>14} {'change%':>10} {'flag':>8}")
    print("  " + "-" * 78)
    seam_flags = []
    for col in seam_cols:
        table = "player_stats_basic" if col in BASIC_COLS else "player_stats_advanced"
        m = {}
        for s in SEAM:
            _, nn, mean, *_ = season_stats(cur, table, col, s)
            m[s] = mean
        ch = None
        if m[SEAM[0]] is not None and m[SEAM[1]] is not None and m[SEAM[0]] != 0:
            ch = round((m[SEAM[1]] - m[SEAM[0]]) / abs(m[SEAM[0]]) * 100, 1)
        flag = ""
        if ch is not None and abs(ch) >= 25:
            flag = "STEP"
            seam_flags.append((col, ch))
        print(f"  {col:<28} {str(m[SEAM[0]]):>14} {str(m[SEAM[1]]):>14} {str(ch):>10} {flag:>8}")

    # tracking sum at seam
    for s in SEAM:
        cur.execute(
            """
            SELECT ROUND(AVG(contested_shot_pct+open_shot_pct),4)
            FROM player_stats_advanced
            WHERE season=? AND contested_shot_pct IS NOT NULL AND open_shot_pct IS NOT NULL
            """,
            (s,),
        )
        print(f"\n  tracking sum avg {s}: {cur.fetchone()[0]}")

    # --- verdicts ---
    print("\n" + "=" * 100)
    print("VERDICTS")
    print("=" * 100)

    def family_verdict(name, cols, table="player_stats_advanced", cov_threshold=85):
        issues = []
        for col in cols:
            covs = []
            for s in SEASONS:
                n, nn, *_ = season_stats(cur, table, col, s)
                covs.append(100.0 * nn / n if n else 0)
            if min(covs) < cov_threshold and col in DEF_COLS:
                low_s = [SEASONS[i] for i, c in enumerate(covs) if c < cov_threshold]
                issues.append(f"{col} low coverage at {low_s}")
            # seam
            m0 = season_stats(cur, table, col, SEAM[0])[2]
            m1 = season_stats(cur, table, col, SEAM[1])[2]
            if m0 and m1 and abs((m1 - m0) / m0) >= 0.25:
                issues.append(f"{col} seam jump {m0}->{m1}")
        if issues:
            print(f"  {name}: anomalies — {'; '.join(issues)}")
        else:
            print(f"  {name}: uniform across all nine seasons")

    family_verdict("Basic counting/rates", BASIC_COLS, "player_stats_basic", 95)
    family_verdict("Advanced shooting", SHOOT_COLS)
    family_verdict("Tracking split", TRACK_COLS)
    family_verdict("Defensive tracking", DEF_COLS, cov_threshold=90)

    print("\n  Columns NOT consistently sourced (provenance):")
    print("    • player_stats_basic/adv 2017-20: same NBA endpoints as 2020-26 fetchers, but")
    print("      loaded outside official fetch script SEASONS lists; post-load heal_pass fixes")
    print("      applied to new seasons only (gs, contested/open, off_reb, opp_* gaps).")
    print("    • team_stats, league_stats: six seasons only (2020-21 to 2025-26), not nine.")
    print("    • player_starting_teams: all nine seasons, uniform method.")

    if seam_flags:
        print("\n  Seam step-changes (|change| >= 25%):")
        for col, ch in seam_flags:
            print(f"    {col}: {ch}%")

    con.close()


if __name__ == "__main__":
    main()
