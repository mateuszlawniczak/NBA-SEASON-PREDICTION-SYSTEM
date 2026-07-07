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


CREATE_PLAYER_POSITIONS = """
CREATE TABLE IF NOT EXISTS player_positions (
    season          TEXT NOT NULL,
    player_name     TEXT NOT NULL,
    mapped_position TEXT NOT NULL,
    PRIMARY KEY (season, player_name)
);
"""

# Rows left by the Phase 2 2022-23 run before this migration (source = 2021-22).
_LEGACY_PLAYER_POSITIONS_SEASON = "2021-22"


def _ensure_player_positions_schema(cur: sqlite3.Cursor) -> None:
    row = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='player_positions' LIMIT 1"
    ).fetchone()
    if row is None:
        cur.execute(CREATE_PLAYER_POSITIONS)
        return

    cols = {r[1] for r in cur.execute("PRAGMA table_info(player_positions)").fetchall()}
    if "season" in cols:
        return

    cur.execute("ALTER TABLE player_positions RENAME TO _player_positions_legacy")
    cur.execute(CREATE_PLAYER_POSITIONS)
    cur.execute(
        """
        INSERT INTO player_positions (season, player_name, mapped_position)
        SELECT ?, player_name, mapped_position
        FROM _player_positions_legacy
        """,
        (_LEGACY_PLAYER_POSITIONS_SEASON,),
    )
    cur.execute("DROP TABLE _player_positions_legacy")


def main(source_season: str | None = None, target_season: str | None = None) -> None:
    if source_season is None or target_season is None:
        pair: SeasonPair = parse_cli_seasons()
        source_season = pair.source
        target_season = pair.target
    _ = target_season

    db = _db_path()
    con = sqlite3.connect(db)
    cur = con.cursor()

    _ensure_player_positions_schema(cur)
    cur.execute("DELETE FROM player_positions WHERE season = ?;", (source_season,))

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
            """
            INSERT INTO player_positions (season, player_name, mapped_position)
            VALUES (?, ?, ?);
            """,
            (source_season, player_name, mapped),
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
