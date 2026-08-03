"""
fetch_player_stats.py
----------------------
Script 1 of 2 — unified, season-parameterized core fetch for `player_stats_basic`
and `player_stats_advanced`.

Scope (per the fetch/fix blast-radius analysis):
  * Fetches the RELIABLE NBA API data (category (a)/(b) territory) and computes
    every formula-based column INLINE and CORRECTLY, so no separate cleanup pass
    is ever needed for these columns again.
  * Deliberately does NOT attempt any slow fallback-source lookups (PlayerCareerStats
    retry / basketball-reference scraping). Columns that historically needed those
    fallbacks are left NULL here and are Script 2's job. See LEFT_NULL_FOR_SCRIPT2
    below for the exact list and why.
  * SAFETY: never writes to the live `nba_data.db` unless the caller explicitly
    passes `--db nba_data.db --force-live`. Default output is a separate test
    database (`nba_data.test_fetch.db`).

Endpoints used (regular season; season string like "2022-23"):
  1. LeagueDashPlayerStats  PerGame   / Base      -> gp, gs, mpg, pts, reb, ast, tov,
                                                      stl, blk, fg_pct, fg3_pct, ft_pct,
                                                      total_minutes (derived), and OREB
                                                      (-> off_reb, see note below)
  2. LeagueDashPlayerStats  PerGame   / Advanced  -> usg_pct, def_rating, ts_pct, efg_pct
  3. LeagueDashPlayerBioStats PerGame             -> position (normalized), height,
                                                      weight, age, draft_year -> years_in_league
  4. LeagueDashPlayerStats  Per100Poss / Base     -> *_per100 counting stats;
                                                      ts_pct/efg_pct fallback (per100-derived)
  5. LeagueDashPlayerStats  PerGame   / Defense   -> def_reb
  6. LeagueDashPlayerShotLocations PerGame / Base -> fg_pct_rim, fg_pct_mid,
                                                      fg3_pct_corner, fg3_pct_above_break
  7. LeagueDashPtDefend "Less Than 6Ft" PerGame   -> opp_fg_pct_at_rim,
                                                      opp_fg_at_rim_contested
  8. LeagueDashPtDefend "3 Pointers"    PerGame   -> opp_fg3_contests_attempts,
                                                      opp_fg3_pct_contested
  9. LeagueHustleStatsPlayer PerGame              -> deflections ONLY (the Hustle-based
                                                      contested_shot_pct in the original
                                                      fetch_player_advanced.py was a bug —
                                                      total_contested / (2pt+3pt) is N/N by
                                                      construction; NOT reproduced here)
 10. LeagueDashPlayerPtShot Totals x 4 CloseDefDistRange buckets
                                                  -> contested_shot_pct, open_shot_pct
                                                     numerator (see shot-% note below)
 11. LeagueDashPlayerStats  Totals    / Base      -> total-season FGA, the DENOMINATOR
                                                      for open_shot_pct (matches
                                                      patch_open_shots.py / fix5_shots.py
                                                      exactly — player_stats_basic has no
                                                      `fga` column, so this dedicated call
                                                      is required, same as production)

Formula-based corrections baked in here (category a/b, from the analysis):
  * contested_shot_pct = (0-2ft "Very Tight" + 2-4ft "Tight" FGA) / (sum of all 4
    tracking buckets).  open_shot_pct = (6+ft "Wide Open" FGA ONLY) / (TOTAL SEASON
    FGA from endpoint #11). This is the STRICT 6-ft definition that is actually in
    production today (`archive/patch_open_shots.py`), confirmed by the analysis:
    AVG(contested + open) ~= 0.72 in the existing DB, NOT 1.0. We deliberately do
    NOT use the broader 4-ft "fix_shot_pct.py"/"fix_offensive_tracking.py" definition
    (which forces contested+open=1.0) — that was superseded and is not what's live.
  * off_reb — ***DEVIATION FROM THE LITERAL "reb - def_reb" INSTRUCTION, FLAGGED***:
    the analysis proved `off_reb == round(reb - def_reb, 1)` reproduces the DB
    exactly ONLY for the three backfilled seasons (2017-18..2019-20, 100% match);
    for 2020-21+ it matches only ~87-92% of rows, because `archive/fix_offensive_tracking.py`
    later overwrote off_reb with the REAL `OREB` field pulled directly from the
    Base-PerGame dash for those seasons. Since the stated goal is to "match the
    existing definitions exactly" and 2022-23 (the proof season) is in the
    2020-21+ group, this script fetches OREB directly (endpoint #1) instead of
    computing reb - def_reb. This is called out explicitly in the comparison
    report — flag it if you'd rather force literal reb - def_reb instead.
  * position — normalized via the exact POSITION_MAP from `heal_pass/fix2_position.py`
    / `hydrate_player_basic.py`, and the SAME normalized value is written to both
    `player_stats_basic.position` and `player_stats_advanced.position` (baking in
    what `sync_positions.py` does today as a separate copy step).
  * years_in_league = max(season_start_year - draft_year + 1, 1), computed inline
    from the bio dash's draft_year — same formula as fetch_player_basic.py today.

Columns intentionally left NULL here (category (c) — Script 2's job):
  * player_stats_basic.gs            — left NULL only on the rare row where the
                                        Base-PerGame dash itself returns no GS value
                                        (empirically ~1 row/season in the older
                                        seasons; ~0 in 2020-21+). Needs
                                        PlayerCareerStats -> basketball-reference
                                        fallback chain (archive/heal_games_started.py
                                        / heal_pass/fix4_gs.py) to fully close.
  * player_stats_basic.position /
    player_stats_advanced.position   — left NULL if the bio dash's raw position
                                        string doesn't resolve through POSITION_MAP
                                        (rare / new-to-league players not yet
                                        indexed). Needs PlayerIndex(historical_nullable=1)
                                        / basketball-reference fallback.
  * player_stats_basic.years_in_league — left NULL when draft_year is missing
                                        (undrafted players). Needs
                                        PlayerIndex FROM_YEAR fallback.
  * opp_fg_pct_at_rim / opp_fg_at_rim_contested /
    opp_fg3_contests_attempts / opp_fg3_pct_contested — left NULL when the
                                        NBA tracking endpoint has no row for that
                                        player (very-low-usage defenders). NOTE:
                                        the codebase's own `heal_pass/fix6_opp.py`
                                        only ever re-pulls the SAME endpoint for
                                        these gaps ("no fabrication, no substitute
                                        endpoint") — there is no real category-(c)
                                        fallback source for these today, so Script 2
                                        may simply confirm these gaps are permanent.

Usage:
    py fetch_player_stats.py --season 2022-23
    py fetch_player_stats.py --season 2022-23 2023-24
    py fetch_player_stats.py --all-seasons
    py fetch_player_stats.py --season 2022-23 --db nba_data.test_fetch.db   (default)
    py fetch_player_stats.py --season 2022-23 --polite                     (slow/sequential)

Safety: refuses to target a database file literally named "nba_data.db" unless
`--force-live` is also passed. Default `--db` is `nba_data.test_fetch.db`.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import (
    LeagueDashPlayerStats,
    LeagueDashPlayerBioStats,
    LeagueDashPlayerShotLocations,
    LeagueDashPtDefend,
    LeagueDashPlayerPtShot,
    LeagueHustleStatsPlayer,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ROOT = os.path.dirname(__file__)
LIVE_DB_PATH = os.path.join(ROOT, "nba_data.db")
DEFAULT_TEST_DB_PATH = os.path.join(ROOT, "nba_data.test_fetch.db")

# Convenience full historical span (matches current DB coverage). --season
# accepts any season string regardless of this list; this is only used by
# --all-seasons.
ALL_KNOWN_SEASONS = [
    "2017-18", "2018-19", "2019-20",
    "2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26",
]

TIMEOUT = 90
API_RETRIES = 3

PARALLEL_WORKERS_DEFAULT = 6
POLITE_SNOOZE_RANGE = (4.0, 8.0)
FAST_SNOOZE_RANGE = (0.25, 0.75)
SEASON_COOLDOWN_SEC_DEFAULT = 3.0

DIST_RANGES = [
    "0-2 Feet - Very Tight",
    "2-4 Feet - Tight",
    "4-6 Feet - Open",
    "6+ Feet - Wide Open",
]
CONTESTED_BUCKETS = {"0-2 Feet - Very Tight", "2-4 Feet - Tight"}
WIDE_OPEN_RANGE = "6+ Feet - Wide Open"

# Exact map from heal_pass/fix2_position.py / hydrate_player_basic.py — reproduces
# the existing DB's (coarse, 4-value: SG/SF/PF/C — "PG" never actually occurs in
# production because the bio dash returns generic "Guard"/"Forward" categories,
# not "Point Guard"/"Power Forward") position scheme exactly. Do NOT "fix" this to
# be more granular — that would diverge from the trusted existing data.
POSITION_MAP: dict[str, str] = {
    "point guard": "PG", "shooting guard": "SG", "small forward": "SF",
    "power forward": "PF", "center": "C",
    "guard": "SG", "forward": "SF", "forward-center": "PF",
    "center-forward": "C", "guard-forward": "SG", "forward-guard": "SF",
    "pg": "PG", "sg": "SG", "sf": "SF", "pf": "PF", "c": "C",
    "g": "SG", "f": "SF", "g-f": "SG", "f-g": "SF", "f-c": "PF", "c-f": "C",
}

# Live schema, copied verbatim from `nba_data.db` sqlite_master so the test DB
# is column-for-column identical (needed for a fair comparison).
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

    created_at          TEXT DEFAULT (datetime('now')), height REAL, weight REAL, age REAL, years_in_league INTEGER,
    UNIQUE (season, player_id, team_id)
);
"""

CREATE_PLAYER_STATS_ADVANCED = """
CREATE TABLE IF NOT EXISTS player_stats_advanced (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    season                  TEXT    NOT NULL,
    player_id               INTEGER NOT NULL,
    player_name             TEXT    NOT NULL,
    team_id                 INTEGER,
    team_abbr               TEXT,
    position                TEXT,

    -- Per-100-possessions stats
    pts_per100              REAL,
    reb_per100              REAL,
    ast_per100              REAL,
    tov_per100              REAL,
    stl_per100              REAL,
    blk_per100              REAL,
    fga_per100              REAL,
    fg3a_per100             REAL,
    fta_per100              REAL,

    -- Defensive stats  (blk_pct / stl_pct removed)
    def_reb                 REAL,
    def_rating              REAL,
    deflections             REAL,
    opp_fg_pct_at_rim       REAL,
    opp_fg3_contests_attempts   REAL,

    -- Shooting splits & coverage
    ts_pct                  REAL,
    efg_pct                 REAL,
    fg_pct_rim              REAL,
    fg_pct_mid              REAL,
    fg3_pct_corner          REAL,
    fg3_pct_above_break     REAL,
    contested_shot_pct      REAL,
    open_shot_pct           REAL,

    -- New column
    off_reb                 REAL,

    created_at              TEXT DEFAULT (datetime('now')), opp_fg3_pct_contested REAL, opp_fg_at_rim_contested REAL,
    UNIQUE (season, player_id, team_id)
);
"""

_SKIP_UPSERT_COLS = frozenset({"id", "created_at"})
_CONFLICT_COLS = ("season", "player_id", "team_id")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def safe_float(val) -> "float | None":
    try:
        f = float(val)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def safe_int(val) -> "int | None":
    try:
        f = float(val)
        return None if math.isnan(f) else int(f)
    except (TypeError, ValueError):
        return None


def season_start_year(season: str) -> int:
    return int(season.split("-")[0])


def height_str_to_inches(raw) -> "float | None":
    if not raw:
        return None
    parts = str(raw).strip().split("-")
    if len(parts) == 2:
        try:
            return float(int(parts[0]) * 12 + int(parts[1]))
        except ValueError:
            return None
    return None


def normalize_position(raw: "str | None") -> "str | None":
    if not raw:
        return None
    key = str(raw).strip().lower()
    result = POSITION_MAP.get(key)
    if result:
        return result
    parts = key.split()
    return POSITION_MAP.get(parts[0]) if parts else None


def snooze(polite: bool, label: str = "") -> None:
    lo, hi = POLITE_SNOOZE_RANGE if polite else FAST_SNOOZE_RANGE
    t = random.uniform(lo, hi)
    tag = f" [{label}]" if label else ""
    print(f"    [wait]  sleeping {t:.1f}s{tag} ...", flush=True)
    time.sleep(t)


def with_retries(fn, label: str):
    last_exc: Exception | None = None
    for attempt in range(1, API_RETRIES + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            print(f"       [retry {attempt}/{API_RETRIES}] {label}: {exc}", flush=True)
            time.sleep(random.uniform(2.0, 4.0))
    print(f"       [FAIL] {label}: {last_exc}", flush=True)
    return None


# ---------------------------------------------------------------------------
# Fetch functions — one per NBA API call
# ---------------------------------------------------------------------------

def fetch_base(season: str) -> dict:
    """LeagueDashPlayerStats PerGame/Base. Key (player_id, team_id).

    Includes the direct OREB pull used for off_reb (see module docstring for
    why this replaces the literal reb - def_reb formula for this season range).
    """
    def _call():
        r = LeagueDashPlayerStats(
            season=season, per_mode_detailed="PerGame",
            measure_type_detailed_defense="Base", timeout=TIMEOUT,
        )
        return r.get_data_frames()[0]

    df = with_retries(_call, "Base PerGame")
    out = {}
    if df is None:
        return out
    for _, row in df.iterrows():
        pid = safe_int(row.get("PLAYER_ID"))
        tid = safe_int(row.get("TEAM_ID"))
        if pid is None:
            continue
        gp = safe_int(row.get("GP"))
        mpg = safe_float(row.get("MIN"))
        oreb = safe_float(row.get("OREB"))
        out[(pid, tid)] = {
            "player_name": row.get("PLAYER_NAME"),
            "team_abbr": row.get("TEAM_ABBREVIATION"),
            "gp": gp,
            "gs": safe_int(row.get("GS")),
            "mpg": mpg,
            "pts": safe_float(row.get("PTS")),
            "reb": safe_float(row.get("REB")),
            "ast": safe_float(row.get("AST")),
            "tov": safe_float(row.get("TOV")),
            "stl": safe_float(row.get("STL")),
            "blk": safe_float(row.get("BLK")),
            "fg_pct": safe_float(row.get("FG_PCT")),
            "fg3_pct": safe_float(row.get("FG3_PCT")),
            "ft_pct": safe_float(row.get("FT_PCT")),
            "total_minutes": round(gp * mpg) if gp and mpg else None,
            "off_reb": round(oreb, 3) if oreb is not None else None,
        }
    return out


def fetch_advanced_core(season: str) -> dict:
    """LeagueDashPlayerStats PerGame/Advanced. Key (player_id, team_id).
    Feeds usg_pct (basic table) + def_rating/ts_pct/efg_pct (advanced table)."""
    def _call():
        r = LeagueDashPlayerStats(
            season=season, per_mode_detailed="PerGame",
            measure_type_detailed_defense="Advanced", timeout=TIMEOUT,
        )
        return r.get_data_frames()[0]

    df = with_retries(_call, "Advanced PerGame")
    out = {}
    if df is None:
        return out
    for _, row in df.iterrows():
        pid = safe_int(row.get("PLAYER_ID"))
        tid = safe_int(row.get("TEAM_ID"))
        if pid is None:
            continue
        out[(pid, tid)] = {
            "usg_pct": safe_float(row.get("USG_PCT")),
            "def_rating": safe_float(row.get("DEF_RATING")),
            "ts_pct": safe_float(row.get("TS_PCT")),
            "efg_pct": safe_float(row.get("EFG_PCT")),
        }
    return out


def fetch_bio(season: str) -> dict:
    """LeagueDashPlayerBioStats PerGame. Key player_id.
    position is normalized inline (category-b fix baked in)."""
    def _call():
        r = LeagueDashPlayerBioStats(season=season, per_mode_simple="PerGame", timeout=TIMEOUT)
        return r.get_data_frames()[0]

    df = with_retries(_call, "Bio Stats")
    out = {}
    if df is None:
        return out
    for _, row in df.iterrows():
        pid = safe_int(row.get("PLAYER_ID"))
        if pid is None:
            continue
        draft_raw = row.get("DRAFT_YEAR")
        try:
            draft_year = int(draft_raw) if draft_raw and str(draft_raw).strip().isdigit() else None
        except (TypeError, ValueError):
            draft_year = None

        raw_pos = row.get("PLAYER_POSITION") or row.get("POSITION") or None
        out[pid] = {
            "position": normalize_position(str(raw_pos).strip() if raw_pos else None),
            "height": height_str_to_inches(row.get("PLAYER_HEIGHT")),
            "weight": safe_float(row.get("PLAYER_WEIGHT")),
            "age": safe_float(row.get("AGE")),
            "draft_year": draft_year,
        }
    return out


def fetch_per100(season: str) -> dict:
    """LeagueDashPlayerStats Per100Possessions/Base. Key (player_id, team_id)."""
    def _call():
        r = LeagueDashPlayerStats(
            season=season, per_mode_detailed="Per100Possessions",
            measure_type_detailed_defense="Base", timeout=TIMEOUT,
        )
        return r.get_data_frames()[0]

    df = with_retries(_call, "Per100 Base")
    out = {}
    if df is None:
        return out
    for _, row in df.iterrows():
        pid = safe_int(row.get("PLAYER_ID"))
        tid = safe_int(row.get("TEAM_ID"))
        if pid is None:
            continue
        fgm, fg3m = safe_float(row.get("FGM")), safe_float(row.get("FG3M"))
        fga, fta, pts = safe_float(row.get("FGA")), safe_float(row.get("FTA")), safe_float(row.get("PTS"))
        out[(pid, tid)] = {
            "pts_per100": pts,
            "reb_per100": safe_float(row.get("REB")),
            "ast_per100": safe_float(row.get("AST")),
            "tov_per100": safe_float(row.get("TOV")),
            "stl_per100": safe_float(row.get("STL")),
            "blk_per100": safe_float(row.get("BLK")),
            "fga_per100": fga,
            "fg3a_per100": safe_float(row.get("FG3A")),
            "fta_per100": fta,
            "ts_pct": round(pts / (2 * (fga + 0.44 * fta)), 4)
                      if pts and fga and fta and (fga + 0.44 * fta) > 0 else None,
            "efg_pct": round((fgm + 0.5 * fg3m) / fga, 4)
                       if fgm is not None and fg3m is not None and fga and fga > 0 else None,
        }
    return out


def fetch_defense(season: str) -> dict:
    """LeagueDashPlayerStats PerGame/Defense. Key (player_id, team_id). -> def_reb."""
    def _call():
        r = LeagueDashPlayerStats(
            season=season, per_mode_detailed="PerGame",
            measure_type_detailed_defense="Defense", timeout=TIMEOUT,
        )
        return r.get_data_frames()[0]

    df = with_retries(_call, "Defense PerGame")
    out = {}
    if df is None:
        return out
    for _, row in df.iterrows():
        pid, tid = safe_int(row.get("PLAYER_ID")), safe_int(row.get("TEAM_ID"))
        if pid is None:
            continue
        out[(pid, tid)] = {"def_reb": safe_float(row.get("DREB"))}
    return out


def fetch_shot_locations(season: str) -> dict:
    """LeagueDashPlayerShotLocations PerGame/Base. Key (player_id, team_id)."""
    def _call():
        r = LeagueDashPlayerShotLocations(
            season=season, per_mode_detailed="PerGame",
            measure_type_simple="Base", timeout=TIMEOUT,
        )
        return r.get_data_frames()[0]

    df = with_retries(_call, "Shot Locations")
    out = {}
    if df is None:
        return out
    if hasattr(df.columns, "levels"):
        df.columns = [f"{z}|{s}" if z else s for z, s in df.columns]

    for _, row in df.iterrows():
        pid, tid = safe_int(row.get("PLAYER_ID")), safe_int(row.get("TEAM_ID"))
        if pid is None or tid is None:
            continue

        def zone_pct(zone_name: str) -> "float | None":
            return safe_float(row.get(f"{zone_name}|FG_PCT"))

        corner3 = zone_pct("Corner 3")
        if corner3 is None:
            lc_a = safe_float(row.get("Left Corner 3|FGA"))
            rc_a = safe_float(row.get("Right Corner 3|FGA"))
            if lc_a and rc_a and (lc_a + rc_a) > 0:
                lc_m = safe_float(row.get("Left Corner 3|FGM")) or 0
                rc_m = safe_float(row.get("Right Corner 3|FGM")) or 0
                corner3 = round((lc_m + rc_m) / (lc_a + rc_a), 4)

        out[(pid, tid)] = {
            "fg_pct_rim": zone_pct("Restricted Area"),
            "fg_pct_mid": zone_pct("Mid-Range"),
            "fg3_pct_corner": corner3,
            "fg3_pct_above_break": zone_pct("Above the Break 3"),
        }
    return out


def fetch_rim_defense(season: str) -> dict:
    """LeagueDashPtDefend 'Less Than 6Ft' PerGame. Key player_id."""
    def _call():
        r = LeagueDashPtDefend(
            season=season, per_mode_simple="PerGame",
            defense_category="Less Than 6Ft", timeout=TIMEOUT,
        )
        return r.get_data_frames()[0]

    df = with_retries(_call, "Rim Defense")
    out = {}
    if df is None:
        return out
    for _, row in df.iterrows():
        pid = safe_int(row.get("CLOSE_DEF_PERSON_ID"))
        if pid is None:
            continue
        out[pid] = {
            "opp_fg_pct_at_rim": safe_float(row.get("LT_06_PCT")),
            "opp_fg_at_rim_contested": safe_float(row.get("FGA_LT_06")),
        }
    return out


def fetch_three_pt_defense(season: str) -> dict:
    """LeagueDashPtDefend '3 Pointers' PerGame. Key player_id."""
    def _call():
        r = LeagueDashPtDefend(
            season=season, per_mode_simple="PerGame",
            defense_category="3 Pointers", timeout=TIMEOUT,
        )
        return r.get_data_frames()[0]

    df = with_retries(_call, "3PT Defense")
    out = {}
    if df is None:
        return out
    for _, row in df.iterrows():
        pid = safe_int(row.get("CLOSE_DEF_PERSON_ID"))
        if pid is None:
            continue
        out[pid] = {
            "opp_fg3_contests_attempts": safe_float(row.get("FG3A")),
            "opp_fg3_pct_contested": safe_float(row.get("FG3_PCT")),
        }
    return out


def fetch_hustle_deflections(season: str) -> dict:
    """LeagueHustleStatsPlayer PerGame. Key (player_id, team_id). deflections ONLY —
    the original fetch's contested_shot_pct from this endpoint is a known bug
    (N/N -> ~1.0) and is intentionally NOT reproduced here; see fetch_shot_pct_buckets."""
    def _call():
        r = LeagueHustleStatsPlayer(season=season, per_mode_time="PerGame", timeout=TIMEOUT)
        return r.get_data_frames()[0]

    df = with_retries(_call, "Hustle PerGame")
    out = {}
    if df is None:
        return out
    for _, row in df.iterrows():
        pid, tid = safe_int(row.get("PLAYER_ID")), safe_int(row.get("TEAM_ID"))
        if pid is None:
            continue
        out[(pid, tid)] = {"deflections": safe_float(row.get("DEFLECTIONS"))}
    return out


def _fetch_ptshot_bucket(season: str, dist_range: str) -> dict:
    """LeagueDashPlayerPtShot Totals for one CloseDefDistRange bucket. Key player_id -> FGA."""
    def _call():
        r = LeagueDashPlayerPtShot(
            season=season, close_def_dist_range_nullable=dist_range,
            per_mode_simple="Totals", timeout=TIMEOUT,
        )
        return r.get_data_frames()[0]

    df = with_retries(_call, f"PtShot[{dist_range}]")
    out: dict[int, float] = {}
    if df is None:
        return out
    for _, row in df.iterrows():
        pid = safe_int(row.get("PLAYER_ID"))
        if pid is None:
            continue
        out[pid] = out.get(pid, 0.0) + (safe_float(row.get("FGA")) or 0.0)
    return out


def fetch_total_season_fga(season: str) -> dict:
    """LeagueDashPlayerStats Totals/Base — denominator for open_shot_pct (strict
    6-ft definition). player_stats_basic has no `fga` column, so this dedicated
    call is required — same as production (patch_open_shots.py / fix5_shots.py)."""
    def _call():
        r = LeagueDashPlayerStats(
            season=season, per_mode_detailed="Totals",
            measure_type_detailed_defense="Base", timeout=TIMEOUT,
        )
        return r.get_data_frames()[0]

    df = with_retries(_call, "Totals/Base [season FGA]")
    out: dict[int, float] = {}
    if df is None:
        return out
    for _, row in df.iterrows():
        pid = safe_int(row.get("PLAYER_ID"))
        fga = safe_float(row.get("FGA"))
        if pid is None:
            continue
        out[pid] = out.get(pid, 0.0) + (fga or 0.0)
    return out


def compute_shot_pct(season: str, workers: int, polite: bool) -> dict:
    """Combines the 4 PtShot buckets + total-season FGA into the exact production
    definition: contested = 0-4ft tracking share; open = strict 6+ft share of
    TOTAL SEASON FGA (different denominator on purpose — matches patch_open_shots.py).
    Returns {player_id: (contested_shot_pct, open_shot_pct)}."""
    if polite or workers <= 1:
        buckets = {}
        for i, dr in enumerate(DIST_RANGES):
            if i:
                snooze(polite, "next bucket")
            buckets[dr] = _fetch_ptshot_bucket(season, dr)
        snooze(polite, "before totals")
        total_fga = fetch_total_season_fga(season)
    else:
        with ThreadPoolExecutor(max_workers=min(workers, 5)) as ex:
            futs = {ex.submit(_fetch_ptshot_bucket, season, dr): dr for dr in DIST_RANGES}
            futs[ex.submit(fetch_total_season_fga, season)] = "__total_fga__"
            results = {}
            for fut in as_completed(futs):
                key = futs[fut]
                results[key] = fut.result()
                print(f"       [done] shot-pct: {key}", flush=True)
        buckets = {dr: results[dr] for dr in DIST_RANGES}
        total_fga = results["__total_fga__"]

    all_pids = set(total_fga)
    for b in buckets.values():
        all_pids.update(b.keys())

    result = {}
    for pid in all_pids:
        vt = buckets[DIST_RANGES[0]].get(pid, 0.0)
        t = buckets[DIST_RANGES[1]].get(pid, 0.0)
        op = buckets[DIST_RANGES[2]].get(pid, 0.0)
        wo = buckets[DIST_RANGES[3]].get(pid, 0.0)
        track_total = vt + t + op + wo
        contested = round((vt + t) / track_total, 4) if track_total > 0 else None
        denom = total_fga.get(pid, 0.0)
        open_pct = round(wo / denom, 4) if denom and denom > 0 else None
        result[pid] = (contested, open_pct)
    return result


# ---------------------------------------------------------------------------
# Merge one season's worth of fetches into per-row records
# ---------------------------------------------------------------------------

def merge_season(season: str, *, workers: int, polite: bool) -> tuple[list[dict], list[dict]]:
    """Runs all fetches for one season and returns (basic_records, advanced_records)."""
    if polite or workers <= 1:
        base = fetch_base(season); snooze(polite, "base->advcore")
        adv_core = fetch_advanced_core(season); snooze(polite, "advcore->bio")
        bio = fetch_bio(season); snooze(polite, "bio->per100")
        per100 = fetch_per100(season); snooze(polite, "per100->defense")
        defense = fetch_defense(season); snooze(polite, "defense->shotloc")
        shotloc = fetch_shot_locations(season); snooze(polite, "shotloc->rimdef")
        rim_def = fetch_rim_defense(season); snooze(polite, "rimdef->3ptdef")
        three_d = fetch_three_pt_defense(season); snooze(polite, "3ptdef->hustle")
        hustle = fetch_hustle_deflections(season); snooze(polite, "hustle->shotpct")
        shot_pct = compute_shot_pct(season, workers=1, polite=True)
    else:
        print(f"    [parallel]  up to {workers} workers for core endpoints ...", flush=True)
        jobs = {
            "base": fetch_base, "adv_core": fetch_advanced_core, "bio": fetch_bio,
            "per100": fetch_per100, "defense": fetch_defense, "shotloc": fetch_shot_locations,
            "rim_def": fetch_rim_defense, "three_d": fetch_three_pt_defense,
            "hustle": fetch_hustle_deflections,
        }
        results = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(fn, season): key for key, fn in jobs.items()}
            for fut in as_completed(futs):
                key = futs[fut]
                results[key] = fut.result()
                print(f"       [done] {key}", flush=True)
        base, adv_core, bio = results["base"], results["adv_core"], results["bio"]
        per100, defense, shotloc = results["per100"], results["defense"], results["shotloc"]
        rim_def, three_d, hustle = results["rim_def"], results["three_d"], results["hustle"]
        shot_pct = compute_shot_pct(season, workers=workers, polite=False)

    start_year = season_start_year(season)
    basic_records: list[dict] = []
    advanced_records: list[dict] = []

    for (pid, tid), b in base.items():
        bio_row = bio.get(pid, {})
        position = bio_row.get("position")  # None if unresolved -> left NULL (category c)
        draft_year = bio_row.get("draft_year")
        years_in_league = max(start_year - draft_year + 1, 1) if draft_year else None

        basic_records.append({
            "season": season, "player_id": pid, "player_name": b.get("player_name"),
            "team_id": tid, "team_abbr": b.get("team_abbr"), "position": position,
            "gp": b.get("gp"), "gs": b.get("gs"), "mpg": b.get("mpg"),
            "pts": b.get("pts"), "reb": b.get("reb"), "ast": b.get("ast"),
            "tov": b.get("tov"), "stl": b.get("stl"), "blk": b.get("blk"),
            "fg_pct": b.get("fg_pct"), "fg3_pct": b.get("fg3_pct"), "ft_pct": b.get("ft_pct"),
            "usg_pct": adv_core.get((pid, tid), {}).get("usg_pct"),
            "total_minutes": b.get("total_minutes"),
            "height": bio_row.get("height"), "weight": bio_row.get("weight"),
            "age": bio_row.get("age"), "years_in_league": years_in_league,
        })

        a = adv_core.get((pid, tid), {})
        p = per100.get((pid, tid), {})
        d = defense.get((pid, tid), {})
        sl = shotloc.get((pid, tid), {})
        rd = rim_def.get(pid, {})
        td = three_d.get(pid, {})
        h = hustle.get((pid, tid), {})
        contested, open_pct = shot_pct.get(pid, (None, None))

        advanced_records.append({
            "season": season, "player_id": pid, "player_name": b.get("player_name"),
            "team_id": tid, "team_abbr": b.get("team_abbr"), "position": position,
            "pts_per100": p.get("pts_per100"), "reb_per100": p.get("reb_per100"),
            "ast_per100": p.get("ast_per100"), "tov_per100": p.get("tov_per100"),
            "stl_per100": p.get("stl_per100"), "blk_per100": p.get("blk_per100"),
            "fga_per100": p.get("fga_per100"), "fg3a_per100": p.get("fg3a_per100"),
            "fta_per100": p.get("fta_per100"),
            "def_reb": d.get("def_reb"), "def_rating": a.get("def_rating"),
            "deflections": h.get("deflections"),
            "opp_fg_pct_at_rim": rd.get("opp_fg_pct_at_rim"),
            "opp_fg_at_rim_contested": rd.get("opp_fg_at_rim_contested"),
            "opp_fg3_contests_attempts": td.get("opp_fg3_contests_attempts"),
            "opp_fg3_pct_contested": td.get("opp_fg3_pct_contested"),
            "ts_pct": a.get("ts_pct") or p.get("ts_pct"),
            "efg_pct": a.get("efg_pct") or p.get("efg_pct"),
            "fg_pct_rim": sl.get("fg_pct_rim"), "fg_pct_mid": sl.get("fg_pct_mid"),
            "fg3_pct_corner": sl.get("fg3_pct_corner"),
            "fg3_pct_above_break": sl.get("fg3_pct_above_break"),
            "contested_shot_pct": contested, "open_shot_pct": open_pct,
            "off_reb": b.get("off_reb"),
        })

    return basic_records, advanced_records


# ---------------------------------------------------------------------------
# DB — schema-driven upsert (mirrors fetch_player_advanced.py's approach)
# ---------------------------------------------------------------------------

def ensure_schema(con: sqlite3.Connection) -> None:
    cur = con.cursor()
    cur.execute(CREATE_PLAYER_STATS_BASIC)
    cur.execute(CREATE_PLAYER_STATS_ADVANCED)
    con.commit()


def writable_columns(con: sqlite3.Connection, table: str) -> list[str]:
    cur = con.cursor()
    rows = cur.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows if r[1] not in _SKIP_UPSERT_COLS]


def build_upsert_sql(table: str, columns: list[str]) -> str:
    for c in _CONFLICT_COLS:
        if c not in columns:
            raise RuntimeError(f"Table {table} missing conflict column {c!r}.")
    update_cols = [c for c in columns if c not in _CONFLICT_COLS]
    col_list = ", ".join(columns)
    placeholders = ", ".join(f":{c}" for c in columns)
    set_clause = ", ".join(f"{c} = excluded.{c}" for c in update_cols)
    conflict_list = ", ".join(_CONFLICT_COLS)
    return (
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict_list}) DO UPDATE SET {set_clause}"
    )


def upsert_records(con: sqlite3.Connection, table: str, records: list[dict]) -> int:
    if not records:
        return 0
    columns = writable_columns(con, table)
    sql = build_upsert_sql(table, columns)
    rows = [{c: rec.get(c) for c in columns} for rec in records]
    cur = con.cursor()
    cur.executemany(sql, rows)
    con.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def resolve_db_path(args) -> str:
    db_path = args.db
    basename = os.path.basename(os.path.abspath(db_path))
    if basename == "nba_data.db" and not args.force_live:
        print(
            "[SAFETY] Refusing to write to 'nba_data.db' (the live production database). "
            "Pass --force-live if you really mean to target it, after backing it up.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return db_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Script 1 — unified season-parameterized fetch for "
                    "player_stats_basic + player_stats_advanced (category a/b fixes baked in; "
                    "category c fallbacks left NULL for Script 2).",
    )
    parser.add_argument("--season", nargs="+", metavar="SEASON", help="One or more seasons, e.g. --season 2022-23")
    parser.add_argument("--all-seasons", action="store_true", help=f"Loop over {ALL_KNOWN_SEASONS}")
    parser.add_argument("--db", default=DEFAULT_TEST_DB_PATH, metavar="PATH",
                         help=f"Target SQLite DB (default: {DEFAULT_TEST_DB_PATH}).")
    parser.add_argument("--force-live", action="store_true",
                         help="Required in addition to --db nba_data.db to target the live DB.")
    parser.add_argument("--polite", action="store_true", help="Sequential requests, 4-8s pacing.")
    parser.add_argument("--workers", type=int, default=PARALLEL_WORKERS_DEFAULT, metavar="N")
    parser.add_argument("--season-gap", type=float, default=SEASON_COOLDOWN_SEC_DEFAULT, metavar="SEC")
    args = parser.parse_args()

    if not args.season and not args.all_seasons:
        parser.error("Provide --season SEASON [SEASON ...] or --all-seasons.")

    seasons = args.all_seasons and ALL_KNOWN_SEASONS or args.season
    db_path = resolve_db_path(args)

    print(f"[fetch_player_stats] Target DB : {db_path}", flush=True)
    print(f"[fetch_player_stats] Seasons   : {', '.join(seasons)}", flush=True)
    print(f"[fetch_player_stats] Mode      : {'polite/sequential' if args.polite else f'parallel ({args.workers} workers)'}", flush=True)

    con = sqlite3.connect(db_path)
    ensure_schema(con)

    for i, season in enumerate(seasons, 1):
        print(f"\n{'='*60}\n  Season {season}  ({i}/{len(seasons)})\n{'='*60}", flush=True)
        try:
            basic_records, advanced_records = merge_season(season, workers=args.workers, polite=args.polite)
            n_basic = upsert_records(con, "player_stats_basic", basic_records)
            n_adv = upsert_records(con, "player_stats_advanced", advanced_records)
            print(f"  [OK] {n_basic} basic row(s), {n_adv} advanced row(s) written for {season}", flush=True)

            gs_null = sum(1 for r in basic_records if r.get("gs") is None)
            pos_null = sum(1 for r in basic_records if r.get("position") is None)
            yil_null = sum(1 for r in basic_records if r.get("years_in_league") is None)
            print(f"  [left NULL for Script 2] gs={gs_null} position={pos_null} years_in_league={yil_null}", flush=True)
        except Exception as exc:
            print(f"  [ERROR] {season} failed: {exc}", flush=True)
            import traceback
            traceback.print_exc()

        if i < len(seasons) and args.season_gap > 0:
            print(f"  [cooldown between seasons: {args.season_gap:.1f}s]", flush=True)
            time.sleep(args.season_gap)

    con.close()
    print(f"\n[DONE] fetch_player_stats.py finished. DB: {db_path}", flush=True)


if __name__ == "__main__":
    main()
