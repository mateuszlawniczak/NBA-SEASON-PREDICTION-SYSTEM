"""
init_player_effects.py
----------------------
Creates player_special_effects (if missing) and seeds one row per unique
player from player_stats_basic with all effect flags set to 'No'.

Does not modify any other tables. Safe to re-run: uses CREATE TABLE IF NOT EXISTS
and INSERT OR IGNORE so existing rows are left unchanged.
"""

import os
import sqlite3
import sys

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CREATE_PLAYER_SPECIAL_EFFECTS = """
CREATE TABLE IF NOT EXISTS player_special_effects (
    player_name TEXT    NOT NULL PRIMARY KEY,
    player_id   INTEGER,
    shaq_effect TEXT    NOT NULL DEFAULT 'No',
    klay_effect TEXT    NOT NULL DEFAULT 'No',
    nash_effect TEXT    NOT NULL DEFAULT 'No'
);
"""

INSERT_ROW = """
INSERT OR IGNORE INTO player_special_effects (
    player_name, player_id, shaq_effect, klay_effect, nash_effect
) VALUES (?, ?, 'No', 'No', 'No');
"""

UNIQUE_PLAYERS_SQL = """
SELECT player_id, MAX(player_name) AS player_name
FROM player_stats_basic
GROUP BY player_id
ORDER BY player_id;
"""


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.cursor()
        cur.execute(CREATE_PLAYER_SPECIAL_EFFECTS)
        cur.execute(UNIQUE_PLAYERS_SQL)
        rows = cur.fetchall()
        for player_id, player_name in rows:
            if player_name is None or str(player_name).strip() == "":
                continue
            cur.execute(INSERT_ROW, (str(player_name).strip(), player_id))
        con.commit()
        cur.execute("SELECT COUNT(*) FROM player_special_effects")
        (count,) = cur.fetchone()
        print(
            f"Successfully created player_special_effects table with {count} players initialized to 'No'.",
            flush=True,
        )
    finally:
        con.close()


if __name__ == "__main__":
    main()
