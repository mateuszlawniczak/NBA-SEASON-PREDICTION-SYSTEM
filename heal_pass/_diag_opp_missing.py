"""Confirm the remaining opp_* NULL players are genuinely absent from the
LeagueDashPtDefend endpoints (not a key-matching bug). Reports exactly who/what."""
import sqlite3
from nba_api.stats.endpoints import LeagueDashPtDefend
from config import DB_PATH, NEW_SEASONS


def endpoint_pids(season, category):
    df = LeagueDashPtDefend(season=season, per_mode_simple="PerGame",
                            defense_category=category, timeout=90).get_data_frames()[0]
    return {int(r) for r in df["CLOSE_DEF_PERSON_ID"].dropna().astype(int)}


con = sqlite3.connect(DB_PATH)
cur = con.cursor()
for season in NEW_SEASONS:
    rim_pids = endpoint_pids(season, "Less Than 6Ft")
    three_pids = endpoint_pids(season, "3 Pointers")
    print(f"\n=== {season} ===  rim_endpoint_pids={len(rim_pids)}  3pt_endpoint_pids={len(three_pids)}")

    cur.execute("SELECT player_id, player_name FROM player_stats_advanced WHERE season=? AND opp_fg_pct_at_rim IS NULL", (season,))
    rim_nulls = cur.fetchall()
    in_ep = [n for pid, n in rim_nulls if pid in rim_pids]
    print(f"  rim NULL players: {len(rim_nulls)}; of those PRESENT in endpoint (=matching bug if >0): {len(in_ep)}")
    print("    missing-from-endpoint:", ", ".join(n for pid, n in rim_nulls if pid not in rim_pids))
    if in_ep:
        print("    !!! present in endpoint but NULL:", in_ep)

    cur.execute("SELECT player_id, player_name FROM player_stats_advanced WHERE season=? AND opp_fg3_pct_contested IS NULL", (season,))
    three_nulls = cur.fetchall()
    in_ep3 = [n for pid, n in three_nulls if pid in three_pids]
    print(f"  3pt NULL players: {len(three_nulls)}; of those PRESENT in endpoint (=matching bug if >0): {len(in_ep3)}")
    print("    missing-from-endpoint:", ", ".join(n for pid, n in three_nulls if pid not in three_pids))
    if in_ep3:
        print("    !!! present in endpoint but NULL:", in_ep3)

con.close()
