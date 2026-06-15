"""
patch_rookie_nulls.py
---------------------
Patches rookie_data.draft_year where NULL (typically undrafted players who were
inserted with draft_year NULL by fetch_rookie_data.py).

For each affected player_id, calls CommonPlayerInfo and sets draft_year to the
NBA FROM_YEAR (first season in the league).

Anti-bot: random 3.5–5.5 s sleep between successive API calls.
Commits after every successful UPDATE.
"""

import os
import sys
import time
import random
import sqlite3

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import CommonPlayerInfo

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")
TIMEOUT = 60


def safe_int(val) -> int | None:
    try:
        if val is None:
            return None
        return int(float(val))
    except (TypeError, ValueError):
        return None


def fetch_from_year(player_id: int) -> int | None:
    """FROM_YEAR from CommonPlayerInfo (first NBA season)."""
    try:
        r = CommonPlayerInfo(player_id=player_id, timeout=TIMEOUT)
        df = r.get_data_frames()[0]
        if df is None or df.empty:
            return None
        raw = df.iloc[0].get("FROM_YEAR")
        return safe_int(raw)
    except Exception as exc:
        print(f"    [api-err] player_id={player_id}: {exc}", flush=True)
        return None


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    rows = cur.execute(
        """
        SELECT player_id, player_name
        FROM rookie_data
        WHERE draft_year IS NULL
        ORDER BY player_id
        """
    ).fetchall()

    total = len(rows)
    print(f"\n[patch_rookie_nulls] {total} row(s) with draft_year IS NULL.\n", flush=True)

    patched = skipped = 0

    for i, (player_id, player_name) in enumerate(rows):
        if i > 0:
            time.sleep(random.uniform(3.5, 5.5))

        from_year = fetch_from_year(player_id)
        if from_year is None:
            skipped += 1
            print(
                f"  [SKIP] No FROM_YEAR for {player_name} (player_id={player_id})",
                flush=True,
            )
            continue

        cur.execute(
            "UPDATE rookie_data SET draft_year = ? WHERE player_id = ?",
            (from_year, player_id),
        )
        con.commit()
        patched += 1
        print(
            f"  [PATCH] Set draft_year to {from_year} for {player_name}",
            flush=True,
        )

    con.close()

    print(f"\n{'='*60}", flush=True)
    print(
        f"  DONE — patched: {patched}, skipped: {skipped}, total candidates: {total}",
        flush=True,
    )


if __name__ == "__main__":
    main()
