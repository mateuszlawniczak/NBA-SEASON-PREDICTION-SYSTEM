"""Guardrail 1: safe backup of nba_data.db -> nba_data.pre_heal.db.

Uses the SQLite backup API so any WAL content is flushed into the copy.
Refuses to overwrite an existing backup unless --force is given.
"""
import os
import sqlite3
import sys

from config import DB_PATH, ROOT

BACKUP_PATH = os.path.join(ROOT, "nba_data.pre_heal.db")


def main() -> None:
    force = "--force" in sys.argv
    if os.path.exists(BACKUP_PATH) and not force:
        print(f"[backup] {BACKUP_PATH} already exists. Use --force to overwrite. Aborting.")
        sys.exit(1)

    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(BACKUP_PATH)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()

    size_src = os.path.getsize(DB_PATH)
    size_dst = os.path.getsize(BACKUP_PATH)
    print(f"[backup] source : {DB_PATH} ({size_src:,} bytes)")
    print(f"[backup] backup : {BACKUP_PATH} ({size_dst:,} bytes)")
    print("[backup] OK")


if __name__ == "__main__":
    main()
