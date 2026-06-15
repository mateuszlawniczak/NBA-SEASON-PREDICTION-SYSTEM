"""
rename_effects_table.py
----------------------
Renames player_special_effects -> player_special_effects_offensive in nba_data.db.
Does not drop tables or modify other schemas.
"""

import os
import sqlite3
import sys

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.cursor()
        try:
            cur.execute(
                "ALTER TABLE player_special_effects RENAME TO player_special_effects_offensive;"
            )
        except sqlite3.Error as e:
            con.rollback()
            msg = str(e).lower()
            if "no such table" in msg and "player_special_effects" in msg:
                print(
                    "Rename skipped: player_special_effects does not exist.",
                    file=sys.stderr,
                    flush=True,
                )
            elif "already exists" in msg:
                print(
                    "Rename skipped: player_special_effects_offensive already exists.",
                    file=sys.stderr,
                    flush=True,
                )
            else:
                print(f"Rename failed: {e}", file=sys.stderr, flush=True)
            raise SystemExit(1)
        con.commit()
        print(
            "Successfully renamed table to player_special_effects_offensive.",
            flush=True,
        )
    finally:
        con.close()


if __name__ == "__main__":
    main()
