"""
Remove season '2019-20' from every table that has a `season` column.
Run after dropping 2019-20 from fetch scripts so the DB stays aligned.
"""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall()]
    for t in tables:
        cols = [r[1] for r in cur.execute(f'PRAGMA table_info("{t}")')]
        if "season" not in cols:
            continue
        cur.execute(f'DELETE FROM "{t}" WHERE season = ?', ("2019-20",))
        n = cur.rowcount
        if n:
            print(f"Deleted {n} row(s) from {t} (season = 2019-20).")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
