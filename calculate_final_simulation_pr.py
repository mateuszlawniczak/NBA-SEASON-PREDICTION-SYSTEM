"""
calculate_final_simulation_pr.py
---------------------------------
Reads ``player_experience_pr`` (2024-25 adjusted experience PR) and yearly
``player_special_effects`` for season ``2024-25``, applies stacking boosts,
and writes ``final_simulation_pr`` only (no other tables are altered).
"""

from __future__ import annotations

import os
import sqlite3
import sys
from typing import Any

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")
TARGET_SEASON = "2024-25"

# (column_name, boost_if_yes, label_for_applied_effects)
EFFECT_RULES: tuple[tuple[str, int, str], ...] = (
    ("mvp_potential", 6, "MVP Potential"),
    ("alien_effect", 5, "Alien Effect"),
    ("shaq_effect", 4, "Shaq Effect"),
    ("nash_effect", 4, "Nash Effect"),
    ("efficiency_god", 3, "Efficiency God"),
    ("klay_effect", 3, "Klay Effect"),
    ("defense_effect", 2, "Defense Effect"),
    ("board_effect", 2, "Board Effect"),
)

CREATE_FINAL_SIMULATION_PR = """
CREATE TABLE IF NOT EXISTS final_simulation_pr (
    player_name TEXT PRIMARY KEY,
    final_pr INTEGER NOT NULL,
    applied_effects TEXT NOT NULL
);
"""


def _yes(val: Any) -> bool:
    return str(val or "").strip() == "Yes"


if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    cols_sql = ", ".join(col for col, _, _ in EFFECT_RULES)
    query = f"""
        SELECT
            e.player_name,
            e.adjusted_exp_pr,
            {cols_sql}
        FROM player_experience_pr AS e
        LEFT JOIN player_special_effects AS s
          ON e.player_name = s.player_name AND s.season = ?
        ORDER BY e.player_name;
    """

    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.cursor()
        cur.execute(CREATE_FINAL_SIMULATION_PR)
        cur.execute("DELETE FROM final_simulation_pr;")

        cur.execute(query, (TARGET_SEASON,))
        rows_raw = cur.fetchall()

        out: list[tuple[str, int, str]] = []
        # SELECT columns: player_name, adjusted_exp_pr, then each effect in EFFECT_RULES order
        for tup in rows_raw:
            pname = str(tup[0]).strip()
            base = int(tup[1])
            effect_vals = tup[2:]
            applied: list[str] = []
            bonus = 0
            for i, (_, pts, label) in enumerate(EFFECT_RULES):
                if _yes(effect_vals[i]):
                    bonus += pts
                    applied.append(label)
            effects_str = ", ".join(applied) if applied else "None"
            out.append((pname, base + bonus, effects_str))

        cur.executemany(
            """
            INSERT INTO final_simulation_pr (player_name, final_pr, applied_effects)
            VALUES (?, ?, ?);
            """,
            out,
        )
        con.commit()
        print(
            f"[ok] final_simulation_pr: wrote {len(out)} rows "
            f"(base from player_experience_pr, effects from {TARGET_SEASON}).",
            flush=True,
        )
    finally:
        con.close()


if __name__ == "__main__":
    main()
