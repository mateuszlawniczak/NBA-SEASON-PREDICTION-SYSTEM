import traceback
from nba_api.stats.endpoints import PlayerCareerStats, LeagueDashPlayerPtShot

print("--- PlayerCareerStats(LeBron=2544) ---")
try:
    dfs = PlayerCareerStats(player_id=2544, timeout=60).get_data_frames()
    df = dfs[0]
    print("rows:", len(df), "cols:", list(df.columns)[:6])
    print(df[df["SEASON_ID"].astype(str).str.strip() == "2017-18"][["SEASON_ID", "TEAM_ID", "GS"]].to_string())
except Exception as exc:
    print("PCS FAILED:", repr(exc))
    traceback.print_exc()

print("\n--- LeagueDashPlayerPtShot(2019-20, Wide Open) ---")
try:
    df = LeagueDashPlayerPtShot(
        season="2019-20", close_def_dist_range_nullable="6+ Feet - Wide Open",
        per_mode_simple="Totals", timeout=60,
    ).get_data_frames()[0]
    print("rows:", len(df), "cols:", list(df.columns)[:6])
except Exception as exc:
    print("PtShot FAILED:", repr(exc))
