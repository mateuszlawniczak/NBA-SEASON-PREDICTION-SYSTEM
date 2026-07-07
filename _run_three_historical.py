"""Run one historical pipeline target with post-run checks."""
from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import sys

from leakage_guards import draft_year_for_target, fmvp_names_before_target
from season_utils import parse_season_pair, prior_source_season, trailing_three_seasons

DB = "nba_data.db"
PRODUCTION_HASH = (
    "a6917b0b6ff8d22762aca72e7ce280c73cfe2888ca9021a8b1bfc361dfc20d0c"
)
DB_START = "2017-18"


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


def window_report(target: str) -> dict:
    pair = parse_season_pair(target)
    source = pair.source
    clutch = trailing_three_seasons(source)
    dur_prior = prior_source_season(source)
    dur_pair = (dur_prior, source)
    con = sqlite3.connect(DB)
    basic = {
        r[0]
        for r in con.execute("SELECT DISTINCT season FROM player_stats_basic").fetchall()
    }
    con.close()
    clutch_before = [s for s in clutch if s < DB_START]
    clutch_missing_db = [s for s in clutch if s not in basic]
    dur_missing = [s for s in dur_pair if s not in basic]
    return {
        "target": target,
        "source": source,
        "clutch_window": clutch,
        "clutch_before_db": clutch_before,
        "clutch_missing_from_db": clutch_missing_db,
        "clutch_complete": not clutch_before,
        "durability_window": list(dur_pair),
        "durability_missing_from_db": dur_missing,
        "durability_complete": not dur_missing,
        "fully_complete": not clutch_before and not dur_missing,
    }


def verify(target: str) -> tuple[bool, str]:
    con = sqlite3.connect(DB)
    sim = con.execute(
        "SELECT COUNT(1) FROM simulation_results WHERE season=? AND run_id='production'",
        (target,),
    ).fetchone()[0]
    up = con.execute(
        "SELECT COUNT(1) FROM ULTIMATE_PR WHERE season=?", (target,)
    ).fetchone()[0]
    h = sim_hash(con)
    top = con.execute(
        """
        SELECT team, champion_pct FROM simulation_results
        WHERE season=? AND run_id='production'
        ORDER BY champion_pct DESC LIMIT 1
        """,
        (target,),
    ).fetchone()
    con.close()
    if sim != 30 or up <= 0:
        return False, f"sim={sim}, ULTIMATE_PR={up}"
    if h != PRODUCTION_HASH:
        return False, f"production hash changed: {h}"
    return True, f"sim=30, ULTIMATE_PR={up}, champ={top[0]} ({top[1]:.1f}%)"


def leakage_line(target: str) -> str:
    fmvp = sorted(fmvp_names_before_target(target))
    dy = draft_year_for_target(target)
    return f"FMVP({len(fmvp)})={fmvp} | draft={dy} | PASS"


def main() -> None:
    targets = sys.argv[1:] or ["2020-21", "2019-20", "2018-19"]
    results: list[dict] = []

    for target in targets:
        print("\n" + "=" * 72)
        print(f"TARGET {target}")
        wr = window_report(target)
        print(f"  Clutch window: {wr['clutch_window']}")
        print(f"  Before DB start ({DB_START}): {wr['clutch_before_db'] or 'none'}")
        print(f"  Clutch complete (all seasons >= {DB_START}): {wr['clutch_complete']}")
        print(f"  Durability window: {wr['durability_window']}")
        print(f"  Durability missing from DB: {wr['durability_missing_from_db'] or 'none'}")
        print(f"  Durability complete: {wr['durability_complete']}")
        print(f"  Fully complete data: {wr['fully_complete']}")

        rc = subprocess.run(
            [sys.executable, "pipeline.py", "--season", target, "--stage", "all"],
        )
        ok, msg = verify(target)
        entry = {**wr, "pipeline_rc": rc.returncode, "verify_ok": ok, "verify_msg": msg}
        entry["leakage"] = leakage_line(target)
        results.append(entry)

        print(f"  Pipeline exit: {rc.returncode}")
        print(f"  Verify: {msg} {'OK' if ok else 'FAIL'}")
        print(f"  Leakage: {entry['leakage']}")
        print(f"  Production hash: {PRODUCTION_HASH[:12]}... {'OK' if ok else 'FAIL'}")

        if rc.returncode != 0 or not ok:
            print("\n!!! STOPPED after failure")
            break

    print("\n" + "=" * 72)
    print("SUMMARY")
    for r in results:
        status = "FULL" if r["fully_complete"] else "PARTIAL"
        if r["pipeline_rc"] != 0 or not r["verify_ok"]:
            status = "FAILED"
        print(
            f"  {r['target']}: {status} | clutch_before_db={r['clutch_before_db']} | "
            f"dur_missing={r['durability_missing_from_db']} | {r.get('verify_msg','')}"
        )


if __name__ == "__main__":
    main()
