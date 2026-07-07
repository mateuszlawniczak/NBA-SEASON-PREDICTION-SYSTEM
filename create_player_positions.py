"""
Build standardized player_positions (G / F / C) from player_stats_basic for the
source season.

Creates only the player_positions table; does not alter other tables.
"""

from __future__ import annotations

import os
import sqlite3

from season_utils import SeasonPair, parse_cli_seasons


def _db_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "nba_data.db")


def _primary_abbrev(position_raw: str | None) -> str | None:
    if position_raw is None:
        return None
    s = position_raw.strip()
    if not s:
        return None
    cuts = []
    for sep in "-", "/":
        i = s.find(sep)
        if i != -1:
            cuts.append(i)
    if cuts:
        s = s[: min(cuts)].strip()
    return s or None


def _map_position(abbrev: str | None) -> str | None:
    if abbrev is None:
        return None
    a = abbrev.upper()
    if a in ("PG", "SG"):
        return "G"
    if a in ("SF", "PF"):
        return "F"
    if a == "C":
        return "C"
    return None


def main(source_season: str | None = None, target_season: str | None = None) -> None:
    if source_season is None or target_season is None:
        pair: SeasonPair = parse_cli_seasons()
        source_season = pair.source
        target_season = pair.target
    _ = target_season

    db = _db_path()
    con = sqlite3.connect(db)
    cur = con.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS player_positions (
            player_name     TEXT PRIMARY KEY,
            mapped_position TEXT NOT NULL
        );
        """
    )

    cur.execute("DELETE FROM player_positions;")

    cur.execute(
        """
        SELECT player_name, position
        FROM player_stats_basic
        WHERE season = ?;
        """,
        (source_season,),
    )
    rows = cur.fetchall()

    inserted = 0
    skipped = 0
    for player_name, position in rows:
        mapped = _map_position(_primary_abbrev(position))
        if mapped is None:
            skipped += 1
            continue
        cur.execute(
            "INSERT INTO player_positions (player_name, mapped_position) VALUES (?, ?);",
            (player_name, mapped),
        )
        inserted += 1

    con.commit()
    con.close()
    print(
        f"player_positions: inserted {inserted}, skipped {skipped} "
        f"(source season {source_season!r})."
    )


if __name__ == "__main__":
    main()
