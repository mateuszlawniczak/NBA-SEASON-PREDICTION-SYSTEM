"""
2025-26 continuity rule review — compute tables and champion-odds delta
without overwriting production simulation_results.
"""
from __future__ import annotations

import sqlite3
import sys

import run_monte_carlo
from build_team_playoff_pr import (
    Top2Status,
    _evaluate_neutral_handling,
    _legacy_continuity_tier,
    _target_team_for_player,
    evaluate_continuity,
    main as build_playoff_main,
)

DB = "nba_data.db"
TARGET = "2025-26"
SOURCE = "2024-25"
REVIEW_RUN_ID = "continuity_review"
FOCUS_TEAMS = ("BOS", "CHI", "DET", "SAC", "SAS")

_STATUS_LABEL = {"kept": "Y", "assumed_kept": "A", "departed": "D"}


def _all_teams(con: sqlite3.Connection) -> list[str]:
    rows = con.execute(
        """
        SELECT DISTINCT team_abbr
        FROM player_starting_teams
        WHERE season = ?
        ORDER BY team_abbr
        """,
        (TARGET,),
    ).fetchall()
    return [str(r[0]) for r in rows]


def _snapshot_playoff_projection(con: sqlite3.Connection) -> list[tuple]:
    return con.execute(
        """
        SELECT team, season, base_8man_pr, amplified_coach_mult,
               playstyle_mult, continuity_mult, final_playoff_pr
        FROM team_playoff_projection
        WHERE season = ?
        """,
        (TARGET,),
    ).fetchall()


def _restore_playoff_projection(con: sqlite3.Connection, rows: list[tuple]) -> None:
    con.execute("DELETE FROM team_playoff_projection WHERE season = ?", (TARGET,))
    con.executemany(
        """
        INSERT INTO team_playoff_projection (
            team, season, base_8man_pr, amplified_coach_mult,
            playstyle_mult, continuity_mult, final_playoff_pr
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    con.commit()


def _champion_odds(con: sqlite3.Connection, run_id: str) -> dict[str, float]:
    rows = con.execute(
        """
        SELECT team, champion_pct
        FROM simulation_results
        WHERE season = ? AND run_id = ?
        """,
        (TARGET, run_id),
    ).fetchall()
    return {str(r[0]): float(r[1]) for r in rows}


def print_assumed_kept_cases(con: sqlite3.Connection) -> None:
    print("\n=== Assumed-kept top-2 (missing from target roster, gate=kept) ===\n")
    any_row = False
    for team in _all_teams(con):
        ev = evaluate_continuity(con, team, TARGET, SOURCE)
        for item in ev["assumed_kept_players"]:  # type: ignore[union-attr]
            any_row = True
            print(
                f"  {item['player_name']} | source {item['source_team']} | "
                f"PR={float(item['source_pr']):.1f} | {item['reason']}"
            )
    if not any_row:
        print("  (none)")


def print_genuine_departures(con: sqlite3.Connection) -> None:
    print("\n=== Genuine top-2 departures (different team in target roster) ===\n")
    print(f"{'Team':<5} {'Player':<28} {'SrcPR':>7} {'Target team':<6}")
    print("-" * 55)
    any_row = False
    for team in _all_teams(con):
        ev = evaluate_continuity(con, team, TARGET, SOURCE)
        for pid, name, pr, status in ev["top_two"]:  # type: ignore[union-attr]
            if status != "departed":
                continue
            any_row = True
            tgt = _target_team_for_player(con, pid, TARGET) or "—"
            print(f"{team:<5} {name:<28} {pr:7.1f} {tgt:<6}")
    if not any_row:
        print("(none)")


def print_neutral_vs_assumed_kept_diff(con: sqlite3.Connection) -> list[str]:
    print("\n=== Tier changes vs neutral handling (previous review) ===\n")
    changed: list[str] = []
    for team in _all_teams(con):
        neutral = _evaluate_neutral_handling(con, team, TARGET, SOURCE)
        current = str(evaluate_continuity(con, team, TARGET, SOURCE)["tier"])
        if neutral != current:
            changed.append(team)
            print(f"  {team}: neutral={neutral} -> assumed_kept={current}")
    if not changed:
        print("  (none — assumed-kept and neutral handling yield identical tiers)")
    return changed


def print_continuity_table(con: sqlite3.Connection) -> list[str]:
    print("\n=== 2025-26 continuity classification (all 30 teams) ===\n")
    header = (
        f"{'Team':<5} {'Top-2 (source PR)':<42} {'Top2':<8} "
        f"{'Overlap':>8} {'New':<8} {'Old':<8} {'Rule':<18} {'Diff'}"
    )
    print(header)
    print("-" * len(header))

    changed_teams: list[str] = []
    trade_low: list[str] = []
    overlap_teams: list[str] = []

    for team in _all_teams(con):
        ev = evaluate_continuity(con, team, TARGET, SOURCE)
        new_tier = str(ev["tier"])
        old_tier = _legacy_continuity_tier(team)
        overlap = float(ev["overlap"])
        rule = str(ev["rule"])
        top_two = ev["top_two"]  # type: ignore[assignment]

        if rule == "top2_gate":
            trade_low.append(team)
        else:
            overlap_teams.append(f"{team} ({new_tier})")

        names_parts: list[str] = []
        status_parts: list[str] = []
        for _pid, name, pr, status in top_two:
            short = name if len(name) <= 18 else name[:16] + "…"
            names_parts.append(f"{short}({pr:.0f})")
            status_parts.append(_STATUS_LABEL[str(status)])
        while len(names_parts) < 2:
            names_parts.append("—")
            status_parts.append("—")

        top2_str = ", ".join(names_parts)
        status_str = "/".join(status_parts)
        diff = "YES" if new_tier != old_tier else ""
        flag = " *" if ev.get("flagged_assumed_kept") else ""
        if diff:
            changed_teams.append(team)
        print(
            f"{team:<5} {top2_str:<42} {status_str:<8} "
            f"{overlap * 100:7.1f}% {new_tier:<8} {old_tier:<8} {rule:<18} {diff}{flag}"
        )

    print("\n  Top2 codes: Y=kept, A=assumed kept (missing roster row), D=departed")
    print("  * = team has assumed-kept top-2 flagged for manual review")

    print("\n=== Tier assignment summary ===")
    print(f"  Genuine-trade LOW ({len(trade_low)}): {', '.join(trade_low) or '(none)'}")
    print(f"  Overlap-based ({len(overlap_teams)}):")
    for entry in overlap_teams:
        print(f"    {entry}")

    return changed_teams


def print_focus_teams(con: sqlite3.Connection) -> None:
    print("\n=== Focus: BOS, CHI, DET, SAC, SAS ===\n")
    for team in FOCUS_TEAMS:
        ev = evaluate_continuity(con, team, TARGET, SOURCE)
        old = _legacy_continuity_tier(team)
        new = str(ev["tier"])
        rule = str(ev["rule"])
        overlap = float(ev["overlap"])
        print(f"{team}: {old} -> {new}  (overlap={overlap:.1%}, rule={rule})")
        for pid, name, pr, status in ev["top_two"]:  # type: ignore[union-attr]
            st: Top2Status = status  # type: ignore[assignment]
            if st == "kept":
                detail = "kept"
            elif st == "assumed_kept":
                detail = "assumed kept (not on any target roster)"
            elif st == "departed":
                tgt = _target_team_for_player(con, pid, TARGET)
                detail = f"departed -> {tgt}"
            else:
                detail = str(st)
            print(f"  {name} PR={pr:.1f}: {detail}")
        for item in ev.get("assumed_kept_players", []):
            print(f"    [assumed kept] {item['reason']}")
        if rule == "top2_gate":
            print("  Why: top-2 departed to another team -> LOW (0.95)")
        elif overlap >= 0.70:
            print("  Why: no departed top-2; overlap >= 70% -> HIGH (1.05)")
        elif overlap >= 0.50:
            print("  Why: no departed top-2; 50% <= overlap < 70% -> DEFAULT (1.00)")
        else:
            print("  Why: no departed top-2; overlap < 50% -> LOW (0.95)")
        print()


def print_champion_delta(con: sqlite3.Connection, changed_teams: list[str]) -> None:
    baseline = _champion_odds(con, "production")
    review = _champion_odds(con, REVIEW_RUN_ID)
    if not review:
        print("\n(no continuity_review simulation results found)")
        return

    print("\n=== Champion-odds delta (continuity_review vs production baseline) ===\n")
    print(f"{'Team':<5} {'Old tier':<8} {'New tier':<8} {'Base %':>8} {'New %':>8} {'Delta':>8}")
    print("-" * 52)
    for team in sorted(changed_teams):
        old_t = _legacy_continuity_tier(team)
        new_t = str(evaluate_continuity(con, team, TARGET, SOURCE)["tier"])
        b = baseline.get(team, 0.0)
        n = review.get(team, 0.0)
        print(f"{team:<5} {old_t:<8} {new_t:<8} {b:8.2f} {n:8.2f} {n - b:+8.2f}")

    b_okc = baseline.get("OKC", 0.0)
    n_okc = review.get("OKC", 0.0)
    print(f"\nOKC champion %: baseline {b_okc:.2f} -> review {n_okc:.2f} ({n_okc - b_okc:+.2f})")


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    con = sqlite3.connect(DB)
    snapshot: list[tuple] = []
    try:
        print_assumed_kept_cases(con)
        print_genuine_departures(con)
        neutral_diff = print_neutral_vs_assumed_kept_diff(con)
        changed = print_continuity_table(con)
        print_focus_teams(con)

        if neutral_diff:
            print(
                f"\n[note] {len(neutral_diff)} team(s) changed tier vs neutral; "
                "re-running simulation."
            )
        else:
            print(
                "\n[note] No tier changes vs neutral — simulation unchanged from "
                "prior review; skipping MC re-run."
            )

        snapshot = _snapshot_playoff_projection(con)
        if not snapshot:
            print("\n[warn] No production team_playoff_projection rows to snapshot.")
            return

        if neutral_diff:
            print("\n=== Running review simulation (non-production) ===")
            build_playoff_main(source_season=SOURCE, target_season=TARGET, persist=True)
            run_monte_carlo.main(
                source_season=SOURCE,
                target_season=TARGET,
                run_id=REVIEW_RUN_ID,
            )
            print_champion_delta(con, changed)
    finally:
        if snapshot:
            _restore_playoff_projection(con, snapshot)
            print(
                "\n[restore] Production team_playoff_projection for 2025-26 restored "
                "(simulation_results production unchanged)."
            )
        con.close()


if __name__ == "__main__":
    main()
