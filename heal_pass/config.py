"""Shared config for the healing pass on the three new seasons."""
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_PATH = os.path.join(ROOT, "nba_data.db")

NEW_SEASONS = ["2017-18", "2018-19", "2019-20"]
EXISTING_SEASONS = ["2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
ALL_SEASONS = NEW_SEASONS + EXISTING_SEASONS

# Tables that any fix in this pass may write to. The existing six seasons'
# rows in exactly these tables must remain byte-for-byte identical.
AFFECTED_TABLES = [
    "player_stats_basic",
    "player_stats_advanced",
    "player_stats_basic_playoffs",
    "player_stats_advanced_playoffs",
]

# Columns healed in player_stats_advanced (regular season) for the null audit.
ADV_HEAL_COLS = [
    "off_reb",
    "position",
    "contested_shot_pct",
    "open_shot_pct",
    "opp_fg_pct_at_rim",
    "opp_fg_at_rim_contested",
    "opp_fg3_contests_attempts",
    "opp_fg3_pct_contested",
    "def_reb",
    "deflections",
    "ts_pct",
    "fg_pct_mid",
    "fg3_pct_corner",
    "fg3_pct_above_break",
]

BASIC_HEAL_COLS = ["position", "gs", "years_in_league"]
