"""
sync_redshirts.py
-----------------
Drops orphan rows from rookie_redshirt_data so it only contains player_ids
that still exist in rookie_data.
"""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    before = cur.execute("SELECT COUNT(*) FROM rookie_redshirt_data").fetchone()[0]

    cur.execute("""
        DELETE FROM rookie_redshirt_data
        WHERE player_id NOT IN (SELECT player_id FROM rookie_data)
    """)
    deleted = cur.rowcount

    con.commit()
    after = cur.execute("SELECT COUNT(*) FROM rookie_redshirt_data").fetchone()[0]

    con.close()

    print(f"Deleted {deleted} orphan row(s) from rookie_redshirt_data.")
    print(f"New total row count: {after} (was {before} before delete).")


if __name__ == "__main__":
    main()
