"""
apply_pedigree_trajectory_boost.py
----------------------------------
Applies "Pedigree Trajectory Boost" multipliers to recent ROY standouts in
ULTIMATE_PR.pr and ultimate_playoff_pr (base_pr, playoff_pr).

Each run multiplies again from the current values. Rebuild ULTIMATE_PR /
ultimate_playoff_pr from your pipelines before re-applying if you need a clean base.
"""

from __future__ import annotations

import os
import sqlite3
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

# Award dictionary: specific player_name -> multiplier (must match DB spelling)
PEDIGREE_BOOST: dict[str, float] = {
    # ROY winners (1.20x)
    "Victor Wembanyama": 1.20,
    "Paolo Banchero": 1.20,
    "Scottie Barnes": 1.20,
    "LaMelo Ball": 1.20,
    "Ja Morant": 1.20,
    # ROY podium / top 3 (1.10x)
    "Chet Holmgren": 1.10,
    "Brandon Miller": 1.10,
    "Jalen Williams": 1.10,
    "Walker Kessler": 1.10,
    "Evan Mobley": 1.10,
    "Cade Cunningham": 1.10,
    "Anthony Edwards": 1.10,
    "Tyrese Haliburton": 1.10,
}

VERIFY_PLAYERS = ("Victor Wembanyama", "Chet Holmgren")


def _fetch_playoff_pr(con: sqlite3.Connection, player: str) -> float | None:
    row = con.execute(
        "SELECT playoff_pr FROM ultimate_playoff_pr WHERE player_name = ?;",
        (player,),
    ).fetchone()
    if row is None:
        return None
    return float(row[0])


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        before = {p: _fetch_playoff_pr(con, p) for p in VERIFY_PLAYERS}

        for player_name, mult in PEDIGREE_BOOST.items():
            con.execute(
                """
                UPDATE ULTIMATE_PR
                SET pr = ROUND(pr * ?, 2)
                WHERE player_name = ?;
                """,
                (mult, player_name),
            )

        for player_name, mult in PEDIGREE_BOOST.items():
            con.execute(
                """
                UPDATE ultimate_playoff_pr
                SET
                    base_pr = ROUND(base_pr * ?, 2),
                    playoff_pr = ROUND(playoff_pr * ?, 2)
                WHERE player_name = ?;
                """,
                (mult, mult, player_name),
            )

        con.commit()

        after = {p: _fetch_playoff_pr(con, p) for p in VERIFY_PLAYERS}

        print("Pedigree Trajectory Boost — playoff_pr verification")
        print("-" * 60)
        for p in VERIFY_PLAYERS:
            b = before[p]
            a = after[p]
            mult = PEDIGREE_BOOST.get(p)
            if b is None and a is None:
                print(f"  {p}: (not in ultimate_playoff_pr — skipped)")
            elif b is None:
                print(f"  {p}: Before = (missing), After = {a} (x{mult})")
            else:
                expected = round(b * mult, 2) if mult else None
                ok = "OK" if a is not None and expected is not None and a == expected else "check"
                print(
                    f"  {p}: Before = {b}, After = {a} "
                    f"(x{mult} → expected {expected}) [{ok}]"
                )
    finally:
        con.close()


if __name__ == "__main__":
    main()
