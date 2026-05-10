"""
NBA Predictor — Database Initializer
Creates nba_data.db with all tables needed for Phases 3, 3.2, and 3.5.

Tables
------
  team_stats          — Phase 3.0
  league_stats        — Phase 3.0
  player_stats_basic  — Phase 3.0
  player_stats_advanced — Phase 3.2
  rookie_data         — Phase 3.5
  coach_system_data   — Phase 3.5
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")


# ---------------------------------------------------------------------------
# DDL statements
# ---------------------------------------------------------------------------

CREATE_TEAM_STATS = """
CREATE TABLE IF NOT EXISTS team_stats (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    season              TEXT    NOT NULL,          -- e.g. "2024-25"
    team_id             INTEGER NOT NULL,
    team_name           TEXT    NOT NULL,
    team_abbr           TEXT    NOT NULL,

    -- Scoring / Ratings
    pts_per_game        REAL,                      -- Points per game
    opp_pts_per_game    REAL,                      -- Opponent points per game
    off_rating          REAL,                      -- Offensive rating (per 100 poss)
    def_rating          REAL,                      -- Defensive rating (per 100 poss)
    net_rating          REAL,                      -- Off - Def rating
    adj_off_rating      REAL,                      -- Adjusted offensive rating
    adj_def_rating      REAL,                      -- Adjusted defensive rating
    adj_net_rating      REAL,                      -- Adjusted net rating
    pace                REAL,                      -- Pace (possessions per 48 min)

    -- Season record / seeding
    wins                INTEGER,
    losses              INTEGER,
    win_pct             REAL,
    conference_seed     INTEGER,                   -- End-of-season seed (1-8)
    made_playoffs       INTEGER DEFAULT 0,         -- 1 = yes, 0 = no

    -- Playoff outcome (previous season reference)
    prev_season         TEXT,
    prev_seed           INTEGER,
    prev_playoff_result TEXT,                      -- e.g. "1st Round", "Finals", "Champion"

    created_at          TEXT DEFAULT (datetime('now'))
);
"""

CREATE_LEAGUE_STATS = """
CREATE TABLE IF NOT EXISTS league_stats (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    season              TEXT    NOT NULL UNIQUE,   -- e.g. "2024-25"

    -- Per-game league averages
    pts                 REAL,                      -- Points
    reb                 REAL,                      -- Rebounds
    ast                 REAL,                      -- Assists
    tov                 REAL,                      -- Turnovers
    stl                 REAL,                      -- Steals
    blk                 REAL,                      -- Blocks
    off_rating          REAL,                      -- League avg offensive rating
    def_rating          REAL,                      -- League avg defensive rating
    pace                REAL,                      -- League avg pace

    created_at          TEXT DEFAULT (datetime('now'))
);
"""

CREATE_PLAYER_STATS_BASIC = """
CREATE TABLE IF NOT EXISTS player_stats_basic (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    season              TEXT    NOT NULL,
    player_id           INTEGER NOT NULL,
    player_name         TEXT    NOT NULL,
    team_id             INTEGER,
    team_abbr           TEXT,
    position            TEXT,                      -- PG, SG, SF, PF, C

    -- Simple per-game stats (Phase 3.0)
    gp                  INTEGER,                   -- Games played
    gs                  INTEGER,                   -- Games started
    mpg                 REAL,                      -- Minutes per game
    pts                 REAL,
    reb                 REAL,
    ast                 REAL,
    tov                 REAL,
    stl                 REAL,
    blk                 REAL,
    fg_pct              REAL,                      -- Field goal %
    fg3_pct             REAL,                      -- 3-point %
    ft_pct              REAL,                      -- Free throw %

    -- Usage / load (Phase 3.0)
    usg_pct             REAL,                      -- Usage rate %
    total_minutes       INTEGER,                   -- Total minutes played all season

    created_at          TEXT DEFAULT (datetime('now')),
    UNIQUE (season, player_id, team_id)
);
"""

CREATE_PLAYER_STATS_ADVANCED = """
CREATE TABLE IF NOT EXISTS player_stats_advanced (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    season              TEXT    NOT NULL,
    player_id           INTEGER NOT NULL,
    player_name         TEXT    NOT NULL,
    team_id             INTEGER,
    team_abbr           TEXT,
    position            TEXT,

    -- Per-100-possessions stats (Phase 3.2)
    pts_per100          REAL,
    reb_per100          REAL,
    ast_per100          REAL,
    tov_per100          REAL,
    stl_per100          REAL,
    blk_per100          REAL,
    fga_per100          REAL,
    fg3a_per100         REAL,
    fta_per100          REAL,

    -- Defensive stats (Phase 3.2)
    def_reb             REAL,                      -- Defensive rebounds
    def_rating          REAL,                      -- Individual defensive rating
    blk_pct             REAL,                      -- Block percentage
    stl_pct             REAL,                      -- Steal percentage
    deflections         REAL,                      -- Deflections per game
    opp_fg_pct_at_rim   REAL,                      -- Opponent FG% at rim when defending
    opp_fg3_pct_contested REAL,                    -- Opp 3P% on contested shots

    -- Shooting splits & coverage (Phase 3.2)
    ts_pct              REAL,                      -- True shooting %
    efg_pct             REAL,                      -- Effective FG%
    fg_pct_rim          REAL,                      -- FG% at the rim
    fg_pct_mid          REAL,                      -- Midrange FG%
    fg3_pct_corner      REAL,                      -- Corner 3 FG%
    fg3_pct_above_break REAL,                      -- Above-break 3 FG%
    contested_shot_pct  REAL,                      -- % of shots that are contested
    open_shot_pct       REAL,                      -- % of shots that are open

    created_at          TEXT DEFAULT (datetime('now')),
    UNIQUE (season, player_id, team_id)
);
"""

CREATE_ROOKIE_DATA = """
CREATE TABLE IF NOT EXISTS rookie_data (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id               INTEGER NOT NULL UNIQUE,
    player_name             TEXT    NOT NULL,

    -- Draft info
    draft_year              INTEGER,
    draft_round             INTEGER,
    draft_pick              INTEGER,               -- Overall pick number
    drafting_team           TEXT,

    -- Origin / background
    college                 TEXT,                  -- College or "International"
    country                 TEXT,
    years_pro_before_nba    INTEGER DEFAULT 0,     -- Overseas pro years before draft

    -- Projection / potential flags
    projected_ceiling       TEXT,                  -- e.g. "Star", "Role Player", "All-Star"
    rookie_season           TEXT,                  -- First NBA season, e.g. "2024-25"

    -- Special effect flags (Phase 3.5)
    chet_effect             INTEGER DEFAULT 0,     -- 1 = elite defensive/spacing combo
    harper_effect           INTEGER DEFAULT 0,     -- 1 = high-upside raw athlete
    notes                   TEXT,                  -- Free-text scouting notes

    created_at              TEXT DEFAULT (datetime('now'))
);
"""

CREATE_COACH_SYSTEM_DATA = """
CREATE TABLE IF NOT EXISTS coach_system_data (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    season                  TEXT    NOT NULL,
    team_id                 INTEGER NOT NULL,
    team_abbr               TEXT    NOT NULL,
    team_name               TEXT    NOT NULL,

    -- Coach info
    head_coach              TEXT    NOT NULL,
    coaching_experience_yrs INTEGER,              -- Total NBA head coaching years
    seasons_with_team       INTEGER,              -- Seasons with current team

    -- System / scheme
    primary_offense         TEXT,                 -- e.g. "Motion Offense", "Pick & Roll Heavy"
    primary_defense         TEXT,                 -- e.g. "Drop Coverage", "Switching Man"
    pace_tendency           TEXT,                 -- "Fast", "Average", "Slow"
    three_point_heavy       INTEGER DEFAULT 0,    -- 1 = high 3PA philosophy

    -- Fit rating (human-entered or model-derived, 1–10)
    system_fit_rating       REAL,

    -- Special effect flags (Phase 3.5 / Phase 5.0)
    spoelstra_effect        INTEGER DEFAULT 0,    -- 1 = elite player-development coach
    spurs_effect            INTEGER DEFAULT 0,    -- 1 = system continuity / culture
    bulls_effect            INTEGER DEFAULT 0,    -- 1 = strong continuity bonus
    suns_effect             INTEGER DEFAULT 0,    -- 1 = offensive role specialization
    pistons_effect          INTEGER DEFAULT 0,    -- 1 = defensive role specialization
    okc_effect              INTEGER DEFAULT 0,    -- 1 = age/experience curve boost

    notes                   TEXT,

    created_at              TEXT DEFAULT (datetime('now')),
    UNIQUE (season, team_id)
);
"""


# ---------------------------------------------------------------------------
# Index definitions (speed up common join / filter patterns)
# ---------------------------------------------------------------------------

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_team_stats_season    ON team_stats          (season);",
    "CREATE INDEX IF NOT EXISTS idx_team_stats_team      ON team_stats          (team_id);",
    "CREATE INDEX IF NOT EXISTS idx_league_stats_season  ON league_stats        (season);",
    "CREATE INDEX IF NOT EXISTS idx_pb_season            ON player_stats_basic  (season);",
    "CREATE INDEX IF NOT EXISTS idx_pb_player            ON player_stats_basic  (player_id);",
    "CREATE INDEX IF NOT EXISTS idx_pa_season            ON player_stats_advanced (season);",
    "CREATE INDEX IF NOT EXISTS idx_pa_player            ON player_stats_advanced (player_id);",
    "CREATE INDEX IF NOT EXISTS idx_rookie_player        ON rookie_data         (player_id);",
    "CREATE INDEX IF NOT EXISTS idx_coach_season         ON coach_system_data   (season);",
    "CREATE INDEX IF NOT EXISTS idx_coach_team           ON coach_system_data   (team_id);",
]


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_database(db_path: str = DB_PATH) -> None:
    print(f"[init_db] Building database at: {db_path}")

    con = sqlite3.connect(db_path)
    cur = con.cursor()

    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA foreign_keys=ON;")

    tables = {
        "team_stats":             CREATE_TEAM_STATS,
        "league_stats":           CREATE_LEAGUE_STATS,
        "player_stats_basic":     CREATE_PLAYER_STATS_BASIC,
        "player_stats_advanced":  CREATE_PLAYER_STATS_ADVANCED,
        "rookie_data":            CREATE_ROOKIE_DATA,
        "coach_system_data":      CREATE_COACH_SYSTEM_DATA,
    }

    for name, ddl in tables.items():
        cur.execute(ddl)
        print(f"  [OK] table created / verified: {name}")

    for idx_sql in INDEXES:
        cur.execute(idx_sql)

    print(f"  [OK] {len(INDEXES)} indexes created / verified")

    con.commit()
    con.close()
    print(f"[init_db] Done. Database ready at: {db_path}")


if __name__ == "__main__":
    build_database()
