"""
init_yearly_player_effects.py
------------------------------
Drops the legacy career-level player_special_effects table (if any), recreates
it keyed by (player_name, season), and seeds one row per distinct pair from
player_stats_basic with all effect flags set to 'No'.

Does not modify any other tables.
"""

import os
import sqlite3
import sys

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CREATE_YEARLY = """
CREATE TABLE player_special_effects (
    player_name TEXT    NOT NULL,
    season      TEXT    NOT NULL,
    shaq_effect TEXT    NOT NULL DEFAULT 'No',
    klay_effect TEXT    NOT NULL DEFAULT 'No',
    nash_effect TEXT    NOT NULL DEFAULT 'No',
    PRIMARY KEY (player_name, season)
);
"""

INSERT_ROW = """
INSERT INTO player_special_effects (
    player_name, season, shaq_effect, klay_effect, nash_effect
) VALUES (?, ?, 'No', 'No', 'No');
"""

UNIQUE_PLAYER_SEASONS_SQL = """
SELECT DISTINCT player_name, season
FROM player_stats_basic
ORDER BY season, player_name;
"""


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.cursor()
        cur.execute("DROP TABLE IF EXISTS player_special_effects;")
        cur.execute(CREATE_YEARLY)
        cur.execute(UNIQUE_PLAYER_SEASONS_SQL)
        for player_name, season in cur.fetchall():
            if player_name is None or str(player_name).strip() == "":
                continue
            if season is None or str(season).strip() == "":
                continue
            cur.execute(
                INSERT_ROW,
                (str(player_name).strip(), str(season).strip()),
            )
        con.commit()
        cur.execute("SELECT COUNT(*) FROM player_special_effects")
        (count,) = cur.fetchone()
        print(
            "Successfully created yearly player_special_effects table with "
            f"{count} player-seasons initialized to 'No'.",
            flush=True,
        )
    finally:
        con.close()


if __name__ == "__main__":
    main()
