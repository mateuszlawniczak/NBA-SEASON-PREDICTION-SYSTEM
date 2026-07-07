"""
Fetch each NBA player's debut team per season via LeagueGameLog (Regular Season,
player rows). Replicates fetch_player_starting_teams_25_26.py exactly, keyed by
PLAYER_ID instead of PLAYER_NAME.

Writes to player_starting_teams (season, player_id, player_name, team_abbr).
Does NOT touch player_starting_teams_25_26.
"""

from __future__ import annotations

import math
import random
import sqlite3
import time
from pathlib import Path

import pandas as pd
from nba_api.stats.endpoints import LeagueGameLog

DB_PATH = Path(__file__).resolve().parent / "nba_data.db"
TABLE_NAME = "player_starting_teams"

SEASONS = [
    "2017-18",
    "2018-19",
    "2019-20",
    "2020-21",
    "2021-22",
    "2022-23",
    "2023-24",
    "2024-25",
    "2025-26",
]

CUSTOM_HEADERS = {
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


def snooze(label: str = "") -> None:
    t = random.uniform(2.0, 4.0)
    tag = f" [{label}]" if label else ""
    print(f"    [wait] {t:.1f}s{tag}", flush=True)
    time.sleep(t)


def safe_int(val) -> int | None:
    try:
        f = float(val)
        return None if math.isnan(f) else int(f)
    except (TypeError, ValueError):
        return None


def fetch_debut_teams(season: str) -> pd.DataFrame:
    """Pull game log, stable-sort by GAME_DATE, first row per PLAYER_ID."""
    print(f"  -> LeagueGameLog Regular Season player rows ({season})", flush=True)
    game_log = LeagueGameLog(
        league_id="00",
        season=season,
        season_type_all_star="Regular Season",
        player_or_team_abbreviation="P",
        headers=CUSTOM_HEADERS,
    )
    df = game_log.get_data_frames()[0]
    if df.empty:
        raise RuntimeError(f"LeagueGameLog returned no rows for {season}")

    if "PLAYER_ID" not in df.columns:
        raise RuntimeError(f"LeagueGameLog missing PLAYER_ID for {season}")

    df = df.copy()
    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"], errors="coerce")
    if df["GAME_DATE"].isna().any():
        bad = int(df["GAME_DATE"].isna().sum())
        raise ValueError(f"{season}: could not parse {bad} GAME_DATE value(s)")

    df = df.sort_values("GAME_DATE", kind="mergesort").reset_index(drop=True)

    debut = df.drop_duplicates(subset=["PLAYER_ID"], keep="first")[
        ["PLAYER_ID", "PLAYER_NAME", "TEAM_ABBREVIATION"]
    ].rename(
        columns={
            "PLAYER_ID": "player_id",
            "PLAYER_NAME": "player_name",
            "TEAM_ABBREVIATION": "team_abbr",
        }
    )
    debut["season"] = season
    debut["player_id"] = debut["player_id"].apply(safe_int)
    debut = debut.dropna(subset=["player_id"])
    debut["player_id"] = debut["player_id"].astype(int)
    return debut[["season", "player_id", "player_name", "team_abbr"]]


def ensure_table(cur: sqlite3.Cursor) -> None:
    cur.execute(f"DROP TABLE IF EXISTS {TABLE_NAME}")
    cur.execute(
        f"""
        CREATE TABLE {TABLE_NAME} (
            season      TEXT    NOT NULL,
            player_id   INTEGER NOT NULL,
            player_name TEXT    NOT NULL,
            team_abbr   TEXT    NOT NULL,
            PRIMARY KEY (season, player_id)
        )
        """
    )


def main() -> None:
    time.sleep(2)
    all_rows: list[pd.DataFrame] = []

    for i, season in enumerate(SEASONS):
        if i:
            snooze(f"before {season}")
        debut = fetch_debut_teams(season)
        print(f"    {season}: {len(debut)} debut teams", flush=True)
        all_rows.append(debut)

    combined = pd.concat(all_rows, ignore_index=True)

    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        ensure_table(cur)
        conn.commit()
        combined.to_sql(TABLE_NAME, conn, if_exists="append", index=False)
        conn.commit()
    finally:
        conn.close()

    print(f"\nWrote {len(combined)} rows to {DB_PATH} table {TABLE_NAME!r}.")


if __name__ == "__main__":
    main()
