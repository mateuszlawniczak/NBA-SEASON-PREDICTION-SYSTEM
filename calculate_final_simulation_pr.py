"""
calculate_final_simulation_pr.py
---------------------------------
Reads ``player_experience_pr`` (source-season adjusted experience PR) and yearly
``player_special_effects`` for the source season, applies stacking boosts,
and writes ``final_simulation_pr`` only (no other tables are altered).
"""

from __future__ import annotations

import os
import sqlite3
import sys
from typing import Any

from season_utils import SeasonPair, parse_cli_seasons

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

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
    player_name     TEXT NOT NULL,
    season          TEXT NOT NULL,
    final_pr        REAL NOT NULL,
    applied_effects TEXT NOT NULL,
    PRIMARY KEY (player_name, season)
);
"""


def _widen_final_pr(con: sqlite3.Connection) -> None:
    exists = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='final_simulation_pr'"
    ).fetchone()
    if exists is None:
        return
    types = {
        str(row[1]): str(row[2] or "").upper()
        for row in con.execute("PRAGMA table_info(final_simulation_pr)")
    }
    if not types.get("final_pr", "").startswith("INT"):
        return
    old_cols = [str(row[1]) for row in con.execute("PRAGMA table_info(final_simulation_pr)")]
    con.execute("ALTER TABLE final_simulation_pr RENAME TO final_simulation_pr__old")
    con.execute(CREATE_FINAL_SIMULATION_PR.replace("IF NOT EXISTS ", ""))
    new_cols = [str(row[1]) for row in con.execute("PRAGMA table_info(final_simulation_pr)")]
    shared = [col for col in new_cols if col in old_cols]
    col_sql = ", ".join(shared)
    con.execute(
        f"INSERT INTO final_simulation_pr ({col_sql}) "
        f"SELECT {col_sql} FROM final_simulation_pr__old"
    )
    con.execute("DROP TABLE final_simulation_pr__old")
    con.commit()


def _yes(val: Any) -> bool:
    return str(val or "").strip() == "Yes"


if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main(source_season: str | None = None, target_season: str | None = None) -> None:
    if source_season is None or target_season is None:
        pair: SeasonPair = parse_cli_seasons()
        source_season = pair.source
        target_season = pair.target
    _ = target_season

    cols_sql = ", ".join(col for col, _, _ in EFFECT_RULES)
    query = f"""
        SELECT
            e.player_name,
            e.adjusted_exp_pr,
            {cols_sql}
        FROM player_experience_pr AS e
        LEFT JOIN player_special_effects AS s
          ON e.player_name = s.player_name AND s.season = ?
        WHERE e.season = ?
        ORDER BY e.player_name;
    """

    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.cursor()
        _widen_final_pr(con)
        cur.execute(CREATE_FINAL_SIMULATION_PR)
        cur.execute(
            "DELETE FROM final_simulation_pr WHERE season = ?;",
            (source_season,),
        )

        cur.execute(query, (source_season, source_season))
        rows_raw = cur.fetchall()

        out: list[tuple[str, str, float, str]] = []
        for tup in rows_raw:
            pname = str(tup[0]).strip()
            base = float(tup[1])
            effect_vals = tup[2:]
            applied: list[str] = []
            bonus = 0
            for i, (_, pts, label) in enumerate(EFFECT_RULES):
                if _yes(effect_vals[i]):
                    bonus += pts
                    applied.append(label)
            effects_str = ", ".join(applied) if applied else "None"
            out.append((pname, source_season, base + bonus, effects_str))

        cur.executemany(
            """
            INSERT INTO final_simulation_pr (player_name, season, final_pr, applied_effects)
            VALUES (?, ?, ?, ?);
            """,
            out,
        )
        con.commit()
        print(
            f"[ok] final_simulation_pr: wrote {len(out)} rows "
            f"(base from player_experience_pr, effects from {source_season}).",
            flush=True,
        )
    finally:
        con.close()


if __name__ == "__main__":
    main()
