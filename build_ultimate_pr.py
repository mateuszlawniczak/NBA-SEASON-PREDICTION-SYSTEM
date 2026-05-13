"""
build_ultimate_pr.py
--------------------
Merges veteran final_simulation_pr and rookie_projected_pr_25_26 into a single
ULTIMATE_PR table in nba_data.db.

Creates ULTIMATE_PR if missing, truncates it, then repopulates via one
INSERT ... SELECT ... UNION ALL SELECT ... statement.

Does not modify any existing table other than creating/truncating ULTIMATE_PR.
"""

from __future__ import annotations

import os
import sqlite3
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

CREATE_ULTIMATE_PR = """
CREATE TABLE IF NOT EXISTS ULTIMATE_PR (
    player_name     TEXT PRIMARY KEY,
    pr              REAL,
    player_type     TEXT,
    applied_effects TEXT
);
"""

MERGE_SQL = """
INSERT INTO ULTIMATE_PR (player_name, pr, player_type, applied_effects)
SELECT
    player_name,
    CAST(final_pr AS REAL) AS pr,
    'Veteran' AS player_type,
    applied_effects
FROM final_simulation_pr
UNION ALL
SELECT
    player_name,
    rookie_pr AS pr,
    'Rookie' AS player_type,
    'None' AS applied_effects
FROM rookie_projected_pr_25_26;
"""


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.cursor()
        cur.execute(CREATE_ULTIMATE_PR)
        cur.execute("DELETE FROM ULTIMATE_PR;")
        cur.execute(MERGE_SQL)
        con.commit()
        n = cur.execute("SELECT COUNT(*) FROM ULTIMATE_PR;").fetchone()[0]
        nv = cur.execute(
            "SELECT COUNT(*) FROM ULTIMATE_PR WHERE player_type = 'Veteran';"
        ).fetchone()[0]
        nr = cur.execute(
            "SELECT COUNT(*) FROM ULTIMATE_PR WHERE player_type = 'Rookie';"
        ).fetchone()[0]
        print(f"[build_ultimate_pr] ULTIMATE_PR rows: {n} (Veteran: {nv}, Rookie: {nr})")
    finally:
        con.close()


if __name__ == "__main__":
    main()
