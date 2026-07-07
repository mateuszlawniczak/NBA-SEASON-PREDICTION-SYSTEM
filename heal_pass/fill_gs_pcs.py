"""Targeted PlayerCareerStats fill for the last few gs nulls (authoritative by
player_id, so no namesake ambiguity). Generous retries to ride out throttling.
Writes only the gs column; scope NEW_SEASONS."""
import sqlite3
import sys
import time

from nba_api.stats.endpoints import PlayerCareerStats
from config import DB_PATH, NEW_SEASONS
from fix4_gs import gs_from_pcs, safe_int

MAX_ATTEMPTS = 6


def fetch_df(pid):
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            dfs = PlayerCareerStats(player_id=pid, timeout=90).get_data_frames()
            df = dfs[0] if dfs else None
            if df is not None and len(df) > 0 and "SEASON_ID" in df.columns:
                return df
            print(f"      attempt {attempt}: empty/throttled", flush=True)
        except Exception as exc:
            print(f"      attempt {attempt}: {exc}", flush=True)
        time.sleep(8 + attempt * 3)
    return None


def main():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    ph = ",".join("?" for _ in NEW_SEASONS)
    cur.execute(
        f"SELECT player_id, player_name, season, team_id FROM player_stats_basic "
        f"WHERE season IN ({ph}) AND gs IS NULL ORDER BY player_name, season",
        NEW_SEASONS,
    )
    rows = cur.fetchall()
    if not rows:
        print("No null gs rows remaining.")
        con.close()
        return

    by_player = {}
    for pid, name, season, tid in rows:
        by_player.setdefault((pid, name), []).append((season, tid))

    filled = 0
    for (pid, name), prows in by_player.items():
        print(f"\n  {name} ({pid})", flush=True)
        df = fetch_df(pid)
        if df is None:
            print("    still unavailable", flush=True)
            continue
        for season, tid in prows:
            gs = gs_from_pcs(df, season, tid)
            if gs is None:
                print(f"    {season} team_id={tid}: no PCS row match", flush=True)
                continue
            cur.execute(
                "UPDATE player_stats_basic SET gs=? WHERE player_id=? AND season=? AND team_id=? AND gs IS NULL",
                (gs, pid, season, tid),
            )
            if cur.rowcount:
                filled += 1
                print(f"    [FILLED-API] {season} team_id={tid}: gs={gs}", flush=True)
        con.commit()

    for s in NEW_SEASONS:
        cur.execute("SELECT COUNT(*) FROM player_stats_basic WHERE season=? AND gs IS NULL", (s,))
        print(f"  remaining NULL gs {s}: {cur.fetchone()[0]}")
    print(f"  filled this run: {filled}")
    con.close()


if __name__ == "__main__":
    main()
