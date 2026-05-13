"""
Fetch each NBA player's debut team for 2025-26 via a single LeagueGameLog call,
then persist results to SQLite (player_starting_teams_25_26).
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pandas as pd
from nba_api.stats.endpoints import LeagueGameLog


# Mimic a typical browser request to stats.nba.com
custom_headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}

DB_PATH = Path(__file__).resolve().parent / "nba_data.db"
TABLE_NAME = "player_starting_teams_25_26"


def main() -> None:
    time.sleep(2)

    game_log = LeagueGameLog(
        league_id="00",
        season="2025-26",
        season_type_all_star="Regular Season",
        player_or_team_abbreviation="P",
        headers=custom_headers,
    )

    df = game_log.get_data_frames()[0]
    if df.empty:
        raise RuntimeError("LeagueGameLog returned no rows; check season and connectivity.")

    df = df.copy()
    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"], errors="coerce")
    if df["GAME_DATE"].isna().any():
        bad = int(df["GAME_DATE"].isna().sum())
        raise ValueError(f"Could not parse {bad} GAME_DATE value(s) chronologically.")

    df = df.sort_values("GAME_DATE", kind="mergesort").reset_index(drop=True)

    debut = df.drop_duplicates(subset=["PLAYER_NAME"], keep="first")[
        ["PLAYER_NAME", "TEAM_ABBREVIATION"]
    ].rename(
        columns={
            "PLAYER_NAME": "player_name",
            "TEAM_ABBREVIATION": "team_abbr",
        }
    )

    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(f"DROP TABLE IF EXISTS {TABLE_NAME}")
        cur.execute(
            f"""
            CREATE TABLE {TABLE_NAME} (
                player_name TEXT PRIMARY KEY,
                team_abbr TEXT
            )
            """
        )
        conn.commit()

        debut.to_sql(TABLE_NAME, conn, if_exists="append", index=False)
        conn.commit()
    finally:
        conn.close()

    print(f"Wrote {len(debut)} rows to {DB_PATH} table {TABLE_NAME!r}.")


if __name__ == "__main__":
    main()
