"""
calculate_ultimate_playoff_pr.py
---------------------------------
Builds ``playoff_pr`` from ULTIMATE_PR (base PR), player_experience_pr
(experience tier), and playoff_riser_choker (playoff_multiplier).

Writes only ``ultimate_playoff_pr`` rows for the target season (idempotent per season).
"""

from __future__ import annotations

import os
import sqlite3
import sys

import pandas as pd

from season_utils import SeasonPair, parse_cli_seasons

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")


def _experience_columns(con: sqlite3.Connection) -> list[str]:
    cur = con.execute("PRAGMA table_info(player_experience_pr)")
    return [row[1] for row in cur.fetchall()]


def load_player_experience_pr(
    con: sqlite3.Connection, source_season: str
) -> pd.DataFrame:
    cols = _experience_columns(con)
    if "experience_level" in cols:
        return pd.read_sql_query(
            """
            SELECT player_name, experience_level
            FROM player_experience_pr
            WHERE season = ?;
            """,
            con,
            params=(source_season,),
        )
    return pd.read_sql_query(
        """
        SELECT player_name
        FROM player_experience_pr
        WHERE season = ?;
        """,
        con,
        params=(source_season,),
    )


def main(source_season: str | None = None, target_season: str | None = None) -> None:
    if source_season is None or target_season is None:
        pair: SeasonPair = parse_cli_seasons()
        source_season = pair.source
        target_season = pair.target

    con = sqlite3.connect(DB_PATH)
    try:
        ultimate = pd.read_sql_query(
            """
            SELECT player_name, pr AS base_pr, mapped_position, player_type
            FROM ULTIMATE_PR
            WHERE season = ?;
            """,
            con,
            params=(target_season,),
        )
        experience = load_player_experience_pr(con, source_season)
        riser = pd.read_sql_query(
            "SELECT player_name, playoff_multiplier FROM playoff_riser_choker;",
            con,
        )
    finally:
        con.close()

    df = ultimate.merge(experience, on="player_name", how="left")
    df = df.merge(riser, on="player_name", how="left")

    def map_player_type(t: object) -> str:
        return "Rookie" if str(t) == "Rookie" else "Veteran"

    if "experience_level" not in df.columns:
        df["experience_level"] = df["player_type"].map(map_player_type)
    else:
        df["experience_level"] = df["experience_level"].fillna(
            df["player_type"].map(map_player_type)
        )

    df.drop(columns=["player_type"], inplace=True)
    df["experience_level"] = df["experience_level"].fillna("Veteran")

    df["playoff_multiplier"] = pd.to_numeric(df["playoff_multiplier"], errors="coerce").fillna(
        1.00
    )

    youth_mask = df["experience_level"].isin(["Rookie", "Sophomore"])
    df["youth_multiplier"] = youth_mask.map({True: 1.10, False: 1.00})

    base = pd.to_numeric(df["base_pr"], errors="coerce")
    df["playoff_pr"] = (
        base.astype(float) * df["youth_multiplier"].astype(float) * df["playoff_multiplier"].astype(float)
    ).round(2)

    out_df = df[
        [
            "player_name",
            "mapped_position",
            "base_pr",
            "experience_level",
            "youth_multiplier",
            "playoff_multiplier",
            "playoff_pr",
        ]
    ].sort_values("playoff_pr", ascending=False)
    out_df.insert(0, "season", target_season)

    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS ultimate_playoff_pr (
                season             TEXT NOT NULL,
                player_name        TEXT NOT NULL,
                mapped_position    TEXT,
                base_pr            REAL NOT NULL,
                experience_level   TEXT NOT NULL,
                youth_multiplier   REAL NOT NULL,
                playoff_multiplier REAL NOT NULL,
                playoff_pr         REAL NOT NULL,
                PRIMARY KEY (player_name, season)
            );
            """
        )
        con.execute(
            "DELETE FROM ultimate_playoff_pr WHERE season = ?;",
            (target_season,),
        )
        out_df.to_sql("ultimate_playoff_pr", con, index=False, if_exists="append")
        con.commit()
    finally:
        con.close()

    _print_top15(out_df.drop(columns=["season"]))


def _print_top15(out_df: pd.DataFrame) -> None:
    top = out_df.head(15).copy()
    top["base_pr"] = pd.to_numeric(top["base_pr"], errors="coerce").round(2)

    w_rank, w_name = 4, 26
    w_base, w_youth, w_po, w_final = 8, 8, 10, 14

    sep = (
        "+"
        + "-" * w_rank
        + "+"
        + "-" * w_name
        + "+"
        + "-" * w_base
        + "+"
        + "-" * w_youth
        + "+"
        + "-" * w_po
        + "+"
        + "-" * w_final
        + "+"
    )
    header = (
        f"|{'Rank':^{w_rank}}"
        f"|{'Name':^{w_name}}"
        f"|{'Base PR':^{w_base}}"
        f"|{'Youth x':^{w_youth}}"
        f"|{'Playoff x':^{w_po}}"
        f"|{'Playoff PR':^{w_final}}"
        "|"
    )

    inner = len(sep) - 2
    print(sep, flush=True)
    print("|" + " TOP 15 BY PLAYOFF_PR ".center(inner) + "|", flush=True)
    print(sep, flush=True)
    print(header, flush=True)
    print(sep, flush=True)

    for i, row in enumerate(top.itertuples(index=False), start=1):
        name = str(row.player_name)[: w_name - 2]
        line = (
            f"|{i:^{w_rank}}"
            f"| {name:<{w_name - 1}}"
            f"|{row.base_pr:>{w_base}.2f}"
            f"|{row.youth_multiplier:>{w_youth}.2f}"
            f"|{row.playoff_multiplier:>{w_po}.2f}"
            f"|{row.playoff_pr:>{w_final}.2f}"
            "|"
        )
        print(line, flush=True)

    print(sep, flush=True)


if __name__ == "__main__":
    main()
