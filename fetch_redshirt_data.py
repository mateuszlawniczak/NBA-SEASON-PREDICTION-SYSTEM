"""
fetch_redshirt_data.py
----------------------
Builds and hydrates the rookie_redshirt_data table.

Logic
-----
Step A — Local DB:
    For every player in rookie_data, find their chronological first season
    across player_stats_basic ∪ player_stats_basic_playoffs (rookie_season).
    Draft flags come from rookie_data so the table stays aligned with rookie_data
    (includes playoffs-only players such as Luca Vildoza).

Step B — Holmgren Effect:
    A player who was drafted but sat out their first NBA season due to injury,
    G-League assignment, or overseas obligation before eventually playing.
    Condition: is_drafted == 1
               AND draft_year >= HOLMGREN_MIN_DRAFT_YEAR (2019 — one year before our
               data starts — to avoid false-flagging veterans whose draft predates
               the dataset window)
               AND draft_year < start_year(rookie_season)
    Example: Chet Holmgren drafted 2022, first NBA game 2023-24 → holmgren_effect = 1
    Counter-example: LeBron James drafted 2003, appears in DB 2020-21 → NOT flagged

Step C — Harper Effect:
    A high lottery pick who played sparingly in their "debut" season —
    suggesting they were still in development / limited by load management.
    Condition: draft_pick <= 14 AND total_gp < 30 AND avg_mpg < 15.0
    (stats are aggregated across all team stints in the rookie_season)

Step D — Combine:
    is_redshirt = 1 if holmgren_effect OR harper_effect else 0

Source: entirely local DB (draft_year already populated from DraftHistory),
        no API calls needed.
"""

import os
import sys
import sqlite3

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

HARPER_MAX_PICK       = 14    # Only top-14 picks are eligible
HARPER_MAX_GP         = 30    # Fewer than 30 games
HARPER_MAX_MPG        = 15.0  # Fewer than 15 minutes per game

# Holmgren Effect only applies to players drafted within our dataset era.
# Our data starts 2020-21. Players from the 2019 draft class had their actual
# rookie year in 2019-20 (not in our DB), so they'd be falsely flagged.
# Setting floor to 2020 eliminates that noise. Any 2020+ draftee whose first
# appearance in our DB is the season AFTER their draft year is a genuine delay.
HOLMGREN_MIN_DRAFT_YEAR = 2020

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS rookie_redshirt_data (
    player_id       INTEGER PRIMARY KEY,
    player_name     TEXT    NOT NULL,
    rookie_season   TEXT    NOT NULL,
    holmgren_effect INTEGER DEFAULT 0,
    harper_effect   INTEGER DEFAULT 0,
    is_redshirt     INTEGER DEFAULT 0,
    created_at      TEXT    DEFAULT (datetime('now'))
)
"""

UPSERT_SQL = """
INSERT OR REPLACE INTO rookie_redshirt_data
    (player_id, player_name, rookie_season, holmgren_effect, harper_effect, is_redshirt)
VALUES
    (:player_id, :player_name, :rookie_season, :holmgren_effect, :harper_effect, :is_redshirt)
"""

# ---------------------------------------------------------------------------
# Step A: build player list with rookie season + draft info
# ---------------------------------------------------------------------------

PLAYER_QUERY = """
WITH seasons AS (
    SELECT player_id, season FROM player_stats_basic
    UNION ALL
    SELECT player_id, season FROM player_stats_basic_playoffs
),
first_season AS (
    SELECT player_id, MIN(season) AS rookie_season
    FROM seasons
    GROUP BY player_id
)
SELECT
    r.player_id,
    r.player_name,
    fs.rookie_season,
    COALESCE(r.draft_pick, 999)   AS draft_pick,
    COALESCE(r.is_drafted, 0)     AS is_drafted,
    r.draft_year
FROM rookie_data r
LEFT JOIN first_season fs ON fs.player_id = r.player_id
ORDER BY r.player_id
"""

# ---------------------------------------------------------------------------
# Step C: aggregate first-season game stats (handle traded players = multi-row)
# ---------------------------------------------------------------------------

ROOKIE_STATS_QUERY = """
SELECT
    COALESCE(SUM(gp), 0)                                      AS total_gp,
    CAST(COALESCE(SUM(total_minutes), 0) AS REAL)
        / NULLIF(COALESCE(SUM(gp), 0), 0)                     AS avg_mpg
FROM (
    SELECT gp, total_minutes FROM player_stats_basic
    WHERE player_id = ? AND season = ?
    UNION ALL
    SELECT gp, total_minutes FROM player_stats_basic_playoffs
    WHERE player_id = ? AND season = ?
)
"""

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    con = sqlite3.connect(DB_PATH)
    con.execute(CREATE_SQL)
    con.commit()

    players = con.execute(PLAYER_QUERY).fetchall()
    total   = len(players)

    print(f"\n[fetch_redshirt_data] Evaluating {total} players (all local DB).\n",
          flush=True)

    cur = con.cursor()
    inserted = redshirts = 0

    for player_id, player_name, rookie_season, draft_pick, is_drafted, draft_year in players:

        if rookie_season is None:
            rookie_season = "unknown"

        try:
            rookie_start_year = int(str(rookie_season).split("-")[0])
        except ValueError:
            rookie_start_year = None

        # --- Step B: Holmgren Effect ---
        if (
            rookie_start_year is not None
            and is_drafted
            and draft_year is not None
            and int(draft_year) >= HOLMGREN_MIN_DRAFT_YEAR
            and int(draft_year) < rookie_start_year
        ):
            holmgren_effect = 1
        else:
            holmgren_effect = 0

        # --- Step C: Harper Effect ---
        if draft_pick <= HARPER_MAX_PICK and rookie_season != "unknown":
            row = con.execute(
                ROOKIE_STATS_QUERY,
                (player_id, rookie_season, player_id, rookie_season),
            ).fetchone()
            total_gp = row[0] or 0
            avg_mpg  = row[1] or 0.0
            harper_effect = (
                1 if total_gp < HARPER_MAX_GP and avg_mpg < HARPER_MAX_MPG else 0
            )
        else:
            harper_effect = 0
            total_gp      = None
            avg_mpg       = None

        # --- Step D: Combine ---
        is_redshirt = 1 if (holmgren_effect or harper_effect) else 0

        cur.execute(UPSERT_SQL, {
            "player_id":       player_id,
            "player_name":     player_name,
            "rookie_season":   rookie_season,
            "holmgren_effect": holmgren_effect,
            "harper_effect":   harper_effect,
            "is_redshirt":     is_redshirt,
        })

        inserted += 1
        if is_redshirt:
            redshirts += 1

        # Always log lottery picks and all redshirts; skip noise for regular players
        if draft_pick <= 14 or is_redshirt:
            gp_str  = f"gp={total_gp}" if total_gp is not None else ""
            mpg_str = f"mpg={avg_mpg:.1f}" if avg_mpg is not None else ""
            stats   = f"  [{gp_str} {mpg_str}]".strip("[]").strip()
            print(
                f"  [REDSHIRT EVAL] {player_name:<28} "
                f"Holmgren: {holmgren_effect}, Harper: {harper_effect}, "
                f"Redshirt: {is_redshirt}  {stats}",
                flush=True,
            )

        if inserted % 100 == 0:
            con.commit()
            print(f"  [commit] {inserted}/{total} processed.", flush=True)

    con.commit()
    con.close()

    print(f"\n{'='*65}", flush=True)
    print(f"  DONE — {inserted} players evaluated, {redshirts} flagged as redshirt.",
          flush=True)

    # Sanity check
    con2 = sqlite3.connect(DB_PATH)
    cur2 = con2.cursor()
    cur2.execute("""
        SELECT
            COUNT(*)                                        AS total,
            SUM(holmgren_effect)                           AS holmgren,
            SUM(harper_effect)                             AS harper,
            SUM(CASE WHEN holmgren_effect=1 AND harper_effect=1 THEN 1 ELSE 0 END) AS both,
            SUM(is_redshirt)                               AS redshirt
        FROM rookie_redshirt_data
    """)
    r = cur2.fetchone()
    con2.close()
    print(
        f"  DB — total={r[0]}  holmgren={r[1]}  harper={r[2]}  "
        f"both={r[3]}  redshirt={r[4]}",
        flush=True,
    )

    # Print all redshirt players for review
    con3 = sqlite3.connect(DB_PATH)
    redshirt_rows = con3.execute("""
        SELECT player_name, rookie_season, holmgren_effect, harper_effect
        FROM rookie_redshirt_data
        WHERE is_redshirt = 1
        ORDER BY rookie_season, player_name
    """).fetchall()
    con3.close()

    print(f"\n  Redshirt players ({len(redshirt_rows)}):", flush=True)
    for name, season, hg, hp in redshirt_rows:
        tags = []
        if hg: tags.append("Holmgren")
        if hp: tags.append("Harper")
        print(f"    {name:<28} {season}  [{', '.join(tags)}]", flush=True)


if __name__ == "__main__":
    main()
