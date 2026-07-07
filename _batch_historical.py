"""Batch historical pipeline with post-season verification."""
from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import sys
from pathlib import Path

from leakage_guards import draft_year_for_target, fmvp_names_before_target
from season_utils import parse_season_pair, prior_source_season, trailing_three_seasons

ROOT = Path(__file__).resolve().parent
DB = ROOT / "nba_data.db"
PRODUCTION_HASH = (
    "a6917b0b6ff8d22762aca72e7ce280c73cfe2888ca9021a8b1bfc361dfc20d0c"
)
SKIP_TARGETS = frozenset({"2022-23", "2025-26"})
REQUESTED = [
    "2017-18",
    "2018-19",
    "2019-20",
    "2020-21",
    "2021-22",
    "2023-24",
    "2024-25",
]


def sim_hash(con: sqlite3.Connection) -> str:
    rows = con.execute(
        """
        SELECT team, season, run_id, avg_wins,
               seed_1_pct, seed_2_pct, seed_3_pct, seed_4_pct, seed_5_pct,
               seed_6_pct, seed_7_pct, seed_8_pct, seed_9_pct, seed_10_pct,
               seed_11_pct, seed_12_pct, seed_13_pct, seed_14_pct, seed_15_pct,
               missed_playoffs_pct, first_round_pct, second_round_pct,
               conf_finals_pct, finals_pct, champion_pct
        FROM simulation_results
        WHERE season = '2025-26' AND run_id = 'production'
        ORDER BY team
        """
    ).fetchall()
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def db_seasons(con: sqlite3.Connection, table: str) -> set[str]:
    if not con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone():
        return set()
    return {r[0] for r in con.execute(f"SELECT DISTINCT season FROM {table}").fetchall()}


def feasibility_report(con: sqlite3.Connection) -> None:
    basic = db_seasons(con, "player_stats_basic")
    print("=" * 72)
    print("FEASIBILITY (DB seasons in player_stats_basic:", sorted(basic), ")")
    print("=" * 72)
    earliest_full: str | None = None
    for target in REQUESTED:
        pair = parse_season_pair(target)
        src = pair.source
        clutch = trailing_three_seasons(src)
        dur_prior = prior_source_season(src)
        issues: list[str] = []
        if src not in basic:
            issues.append(f"missing source {src}")
        if dur_prior not in basic:
            issues.append(f"durability prior {dur_prior} not in DB")
        before_db = [s for s in clutch if s not in basic]
        if before_db:
            issues.append(
                f"clutch window {clutch} — not in DB (API-only): {before_db}"
            )
        if not issues and earliest_full is None:
            earliest_full = target
        status = "RUNNABLE" if src in basic else "BLOCKED (no source)"
        print(f"  {target}: source={src} | {status}")
        for issue in issues:
            print(f"    - {issue}")
    print(f"\nEarliest target with source in DB: 2018-19 (source 2017-18)")
    print(
        f"Earliest target with full DB coverage for source + durability: "
        f"{earliest_full or 'none'}"
    )
    print()


def preflight(target: str, con: sqlite3.Connection) -> tuple[bool, str]:
    pair = parse_season_pair(target)
    basic = db_seasons(con, "player_stats_basic")
    if pair.source not in basic:
        return False, f"source season {pair.source!r} not in player_stats_basic"
    null_po = con.execute(
        """
        SELECT COUNT(1) FROM team_stats_playoffs
        WHERE season = ? AND playoff_result IS NULL
        """,
        (pair.source,),
    ).fetchone()[0]
    total_po = con.execute(
        "SELECT COUNT(1) FROM team_stats_playoffs WHERE season = ?",
        (pair.source,),
    ).fetchone()[0]
    if total_po > 0 and null_po == total_po:
        return (
            False,
            f"team_stats_playoffs.playoff_result all NULL for source {pair.source!r} "
            f"({total_po} rows) — pipeline stops at apply_playoff_experience_pr",
        )
    if null_po > 0:
        return (
            False,
            f"team_stats_playoffs has {null_po}/{total_po} NULL playoff_result rows "
            f"for source {pair.source!r}",
        )
    return True, "ok"


def verify_season(con: sqlite3.Connection, target: str) -> tuple[bool, str]:
    up = con.execute(
        "SELECT COUNT(*) FROM ULTIMATE_PR WHERE season=?", (target,)
    ).fetchone()[0]
    tp = con.execute(
        "SELECT COUNT(*) FROM team_projection WHERE season=?", (target,)
    ).fetchone()[0]
    tpp = con.execute(
        "SELECT COUNT(*) FROM team_playoff_projection WHERE season=?", (target,)
    ).fetchone()[0]
    sim = con.execute(
        "SELECT COUNT(*) FROM simulation_results WHERE season=? AND run_id='production'",
        (target,),
    ).fetchone()[0]
    h = sim_hash(con)
    if up <= 0 or tp != 30 or tpp != 30 or sim != 30:
        return False, (
            f"row check failed: ULTIMATE_PR={up}, team_projection={tp}, "
            f"team_playoff_projection={tpp}, simulation_results={sim}"
        )
    if h != PRODUCTION_HASH:
        return False, f"production hash changed: {h}"
    return True, f"rows OK (ULTIMATE_PR={up}); production hash OK"


def leakage_line(target: str) -> str:
    fmvp = sorted(fmvp_names_before_target(target))
    draft_yr = draft_year_for_target(target)
    excluded = [
        s
        for s, n in __import__("leakage_guards").FINALS_MVP_BY_SEASON.items()
        if s >= target
    ]
    ok = all(
        __import__("leakage_guards").FINALS_MVP_BY_SEASON[s]
        not in fmvp
        for s in excluded
    )
    return (
        f"  {target}: FMVP({len(fmvp)})={fmvp} | draft={draft_yr} | "
        f"{'PASS' if ok else 'FAIL'}"
    )


def run_pipeline(target: str) -> int:
    print(f"\n>>> Running pipeline --season {target} --stage all ...", flush=True)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "pipeline.py"), "--season", target, "--stage", "all"],
        cwd=ROOT,
    )
    return proc.returncode


def summary_table(con: sqlite3.Connection) -> None:
    seasons = [
        r[0]
        for r in con.execute(
            """
            SELECT DISTINCT season FROM simulation_results
            WHERE run_id='production'
            ORDER BY season
            """
        ).fetchall()
    ]
    print("\n" + "=" * 72)
    print("SUMMARY — all production simulation seasons in DB")
    print("=" * 72)
    print(f"{'Season':<10} {'Sim rows':>9} {'ULTIMATE_PR':>12} {'Champion (top-1)':<8} {'Champ %':>8}")
    print("-" * 52)
    for s in seasons:
        sim_n = con.execute(
            "SELECT COUNT(*) FROM simulation_results WHERE season=? AND run_id='production'",
            (s,),
        ).fetchone()[0]
        up_n = con.execute(
            "SELECT COUNT(*) FROM ULTIMATE_PR WHERE season=?", (s,)
        ).fetchone()[0]
        top = con.execute(
            """
            SELECT team, champion_pct FROM simulation_results
            WHERE season=? AND run_id='production'
            ORDER BY champion_pct DESC LIMIT 1
            """,
            (s,),
        ).fetchone()
        champ, pct = top if top else ("—", 0)
        print(f"{s:<10} {sim_n:>9} {up_n:>12} {champ:<8} {pct:>7.1f}")
    print(f"\n2025-26 production hash: {sim_hash(con)}")
    print(f"Expected:               {PRODUCTION_HASH}")
    print("Match:", sim_hash(con) == PRODUCTION_HASH)


def main() -> None:
    con = sqlite3.connect(DB)
    feasibility_report(con)

    completed: list[str] = []
    skipped: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []

    for target in REQUESTED:
        if target in SKIP_TARGETS:
            skipped.append((target, "already generated / production"))
            print(f"\n--- SKIP {target} (protected) ---")
            continue

        existing = con.execute(
            "SELECT COUNT(*) FROM simulation_results WHERE season=? AND run_id='production'",
            (target,),
        ).fetchone()[0]
        if existing == 30:
            skipped.append((target, "already has 30 simulation rows"))
            print(f"\n--- SKIP {target} (already complete) ---")
            ok, msg = verify_season(con, target)
            print(f"  verify: {msg}" if ok else f"  VERIFY FAIL: {msg}")
            if not ok:
                failed.append((target, msg))
                break
            print(leakage_line(target))
            completed.append(target)
            continue

        ok, reason = preflight(target, con)
        if not ok:
            skipped.append((target, reason))
            print(f"\n--- SKIP {target}: {reason} ---")
            continue

        rc = run_pipeline(target)
        con.close()
        con = sqlite3.connect(DB)
        if rc != 0:
            failed.append((target, f"pipeline exit code {rc}"))
            print(f"\n!!! STOP: pipeline failed for {target}")
            break

        ok, msg = verify_season(con, target)
        print(f"  Post-run verify: {msg}")
        if not ok:
            failed.append((target, msg))
            print(f"\n!!! STOP: verification failed for {target}")
            break

        print(leakage_line(target))
        completed.append(target)

    print("\n" + "=" * 72)
    print("BATCH RESULT")
    print("=" * 72)
    print(f"Completed: {completed}")
    print(f"Skipped:   {skipped}")
    if failed:
        print(f"Failed:    {failed}")

    print("\n--- Leakage spot-check (all completed/skipped-with-data) ---")
    all_targets = sorted(
        set(completed)
        | {t for t, _ in skipped if t not in SKIP_TARGETS}
        | {"2022-23"}
    )
    for t in all_targets:
        if con.execute(
            "SELECT 1 FROM simulation_results WHERE season=? AND run_id='production' LIMIT 1",
            (t,),
        ).fetchone():
            print(leakage_line(t))

    summary_table(con)
    con.close()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
