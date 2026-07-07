"""Cross-check gs values for the bref-namesake-risk players against the
authoritative PlayerCareerStats (by exact player_id) if PCS has recovered.
Also dumps what bref currently returns per season/team so we can judge matches."""
import sqlite3
from nba_api.stats.endpoints import PlayerCareerStats
from config import DB_PATH, NEW_SEASONS

SUSPECT_NAMES = ["Jamil Wilson", "Jaxson Hayes", "Matt Williams Jr.", "Maxi Kleber", "Tobias Harris"]

con = sqlite3.connect(DB_PATH)
cur = con.cursor()
suspect = {}
for nm in SUSPECT_NAMES:
    cur.execute("SELECT DISTINCT player_id FROM player_stats_basic WHERE player_name=?", (nm,))
    r = cur.fetchone()
    if r:
        suspect[r[0]] = nm
for pid, name in suspect.items():
    print(f"\n=== {name} (pid={pid}) ===")
    cur.execute(
        "SELECT season, team_id, team_abbr, gs FROM player_stats_basic WHERE player_id=? AND season IN (?,?,?) ORDER BY season",
        (pid, *NEW_SEASONS),
    )
    print("  DB rows (season, team_id, team_abbr, gs):")
    for row in cur.fetchall():
        print("   ", row)
    try:
        df = PlayerCareerStats(player_id=pid, timeout=60).get_data_frames()[0]
        if len(df) == 0:
            print("  PCS: THROTTLED (0 rows)")
        else:
            sub = df[df["SEASON_ID"].astype(str).str.strip().isin(NEW_SEASONS)]
            print("  PCS authoritative (SEASON_ID, TEAM_ID, TEAM_ABBREVIATION, GS):")
            for _, r in sub.iterrows():
                print("   ", r["SEASON_ID"], int(r["TEAM_ID"]), r["TEAM_ABBREVIATION"], "GS=", r["GS"])
    except Exception as e:
        print("  PCS ERR:", repr(e))
con.close()
