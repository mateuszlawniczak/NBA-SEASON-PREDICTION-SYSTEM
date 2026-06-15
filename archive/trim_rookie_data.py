"""
trim_rookie_data.py
-------------------
Trims rookie_data to only include players who entered the NBA in 2020 or later.

FAST APPROACH — CommonAllPlayers batch endpoint
------------------------------------------------
One single API call fetches FROM_YEAR for all ~5 000 NBA players ever.
Build a {player_id: from_year} map, then do all deletes locally — no per-player
API calls needed.

Phase 1 — SQL purge (instant):
    DELETE all rows where draft_year IS NOT NULL AND draft_year < 2019.

Phase 2 — Batch fetch:
    Call CommonAllPlayers (1 API call).
    Build from_year map.

Phase 3 — Local delete pass:
    For each remaining row in rookie_data:
        If from_year found in map AND from_year < 2020 → DELETE.
        If not found in map → keep (benefit of the doubt).
"""

import os
import sys
import sqlite3

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import CommonAllPlayers

DB_PATH  = os.path.join(os.path.dirname(__file__), "nba_data.db")
TIMEOUT  = 60
MIN_YEAR = 2020


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # -----------------------------------------------------------------------
    # Phase 1: SQL purge
    # -----------------------------------------------------------------------
    cur.execute("""
        DELETE FROM rookie_data
        WHERE draft_year IS NOT NULL AND CAST(draft_year AS INTEGER) < 2019
    """)
    purged = cur.rowcount
    con.commit()
    print(f"\n[PHASE 1] SQL purge: removed {purged} pre-2019 draft veterans.",
          flush=True)

    # -----------------------------------------------------------------------
    # Phase 2: one batch call for all FROM_YEAR values
    # -----------------------------------------------------------------------
    print("\n[PHASE 2] Fetching CommonAllPlayers (1 API call)...", flush=True)
    df = CommonAllPlayers(
        is_only_current_season=0,
        league_id="00",
        season="2024-25",
        timeout=TIMEOUT,
    ).get_data_frames()[0]

    from_year_map: dict[int, int] = {}
    for _, row in df.iterrows():
        pid = int(row["PERSON_ID"])
        try:
            from_year_map[pid] = int(row["FROM_YEAR"])
        except (TypeError, ValueError):
            pass

    print(f"  Map built: {len(from_year_map)} players.", flush=True)

    # -----------------------------------------------------------------------
    # Phase 3: local delete pass
    # -----------------------------------------------------------------------
    rows = con.execute(
        "SELECT player_id, player_name FROM rookie_data ORDER BY player_id"
    ).fetchall()
    total = len(rows)

    print(f"\n[PHASE 3] Evaluating {total} remaining players...\n", flush=True)

    kept = deleted = not_found = 0

    for i, (player_id, player_name) in enumerate(rows, 1):
        from_year = from_year_map.get(player_id)

        if from_year is None:
            not_found += 1
            kept += 1
            print(
                f"  [TRIM] Kept    {player_name:<32} (From: N/A — not in map)  [{i}/{total}]",
                flush=True,
            )
        elif from_year < MIN_YEAR:
            cur.execute("DELETE FROM rookie_data WHERE player_id = ?", (player_id,))
            deleted += 1
            print(
                f"  [TRIM] Deleted {player_name:<32} (From: {from_year})  [{i}/{total}]",
                flush=True,
            )
        else:
            kept += 1
            print(
                f"  [TRIM] Kept    {player_name:<32} (From: {from_year})  [{i}/{total}]",
                flush=True,
            )

        if i % 50 == 0:
            con.commit()

    con.commit()
    con.close()

    print(f"\n{'='*65}", flush=True)
    print(
        f"  DONE — kept: {kept}  deleted (API): {deleted}  "
        f"SQL purge: {purged}  not-in-map: {not_found}",
        flush=True,
    )
    print(f"  Total removed: {purged + deleted}  |  Final table size: {kept}",
          flush=True)

    con2 = sqlite3.connect(DB_PATH)
    r = con2.execute(
        "SELECT COUNT(*), MIN(draft_year), MAX(draft_year) FROM rookie_data"
    ).fetchone()
    con2.close()
    print(f"  DB — rows={r[0]}  draft_year range={r[1]}–{r[2]}", flush=True)


if __name__ == "__main__":
    main()
