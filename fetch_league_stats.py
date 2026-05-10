"""
fetch_league_stats.py
---------------------
Fills league_stats in nba_data.db for seasons 2020-21 through 2025-26.

For each season, this script makes two NBA API requests:
  1) LeagueDashTeamStats (Base, PerGame)     -> pts, reb, ast, tov, stl, blk
  2) LeagueDashTeamStats (Advanced, PerGame) -> off_rating, def_rating, pace

It sleeps for a random 4-8 seconds between all requests to reduce rate-limit risk.
"""

import os
import random
import sqlite3
import time
from typing import Optional

from nba_api.stats.endpoints import LeagueDashTeamStats

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

SEASONS = [
    "2020-21",
    "2021-22",
    "2022-23",
    "2023-24",
    "2024-25",
    "2025-26",
]

TIMEOUT = 90
MAX_RETRIES = 3


def snooze(label: str = "") -> None:
    seconds = random.uniform(4.0, 8.0)
    tag = f" [{label}]" if label else ""
    print(f"    [wait] sleeping {seconds:.1f}s{tag} ...", flush=True)
    time.sleep(seconds)


def safe_float(value) -> Optional[float]:
    try:
        result = float(value)
        if result != result:
            return None
        return result
    except (TypeError, ValueError):
        return None


def mean_or_none(series) -> Optional[float]:
    values = [safe_float(v) for v in series]
    values = [v for v in values if v is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def fetch_with_retry(season: str, measure_type: str):
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(
                f"    -> Request: {measure_type} (attempt {attempt}/{MAX_RETRIES})",
                flush=True,
            )
            response = LeagueDashTeamStats(
                season=season,
                per_mode_detailed="PerGame",
                measure_type_detailed_defense=measure_type,
                timeout=TIMEOUT,
            )
            return response.get_data_frames()[0]
        except Exception as exc:  # noqa: BLE001 - keep script resilient
            last_error = exc
            print(f"       [warn] {measure_type} request failed: {exc}", flush=True)
            if attempt < MAX_RETRIES:
                snooze(f"retry {measure_type}")
    raise RuntimeError(
        f"Failed to fetch {measure_type} for {season} after {MAX_RETRIES} attempts: {last_error}"
    )


def build_league_row(season: str) -> dict:
    base_df = fetch_with_retry(season, "Base")
    snooze("next: advanced")
    adv_df = fetch_with_retry(season, "Advanced")

    row = {
        "season": season,
        "pts": mean_or_none(base_df["PTS"]),
        "reb": mean_or_none(base_df["REB"]),
        "ast": mean_or_none(base_df["AST"]),
        "tov": mean_or_none(base_df["TOV"]),
        "stl": mean_or_none(base_df["STL"]),
        "blk": mean_or_none(base_df["BLK"]),
        "off_rating": mean_or_none(adv_df["OFF_RATING"]),
        "def_rating": mean_or_none(adv_df["DEF_RATING"]),
        "pace": mean_or_none(adv_df["PACE"]),
    }
    return row


def upsert_league_row(con: sqlite3.Connection, row: dict) -> None:
    sql = """
    INSERT INTO league_stats (
        season, pts, reb, ast, tov, stl, blk, off_rating, def_rating, pace
    ) VALUES (
        :season, :pts, :reb, :ast, :tov, :stl, :blk, :off_rating, :def_rating, :pace
    )
    ON CONFLICT (season) DO UPDATE SET
        pts = excluded.pts,
        reb = excluded.reb,
        ast = excluded.ast,
        tov = excluded.tov,
        stl = excluded.stl,
        blk = excluded.blk,
        off_rating = excluded.off_rating,
        def_rating = excluded.def_rating,
        pace = excluded.pace
    """
    con.execute(sql, row)
    con.commit()


def main() -> None:
    print(f"[league_stats] Database: {DB_PATH}", flush=True)
    con = sqlite3.connect(DB_PATH)

    try:
        for i, season in enumerate(SEASONS, start=1):
            print(f"\n{'=' * 56}", flush=True)
            print(f"  Season {season} ({i}/{len(SEASONS)})", flush=True)
            print(f"{'=' * 56}", flush=True)

            try:
                row = build_league_row(season)
                upsert_league_row(con, row)
                print(
                    "  [OK] saved:",
                    {
                        "season": row["season"],
                        "pts": row["pts"],
                        "reb": row["reb"],
                        "ast": row["ast"],
                        "tov": row["tov"],
                        "stl": row["stl"],
                        "blk": row["blk"],
                        "off_rating": row["off_rating"],
                        "def_rating": row["def_rating"],
                        "pace": row["pace"],
                    },
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001 - keep long run alive
                print(f"  [ERR] season {season} failed: {exc}", flush=True)

            if i < len(SEASONS):
                snooze("between seasons")
    finally:
        con.close()

    print("\n[league_stats] Done.", flush=True)


if __name__ == "__main__":
    main()
