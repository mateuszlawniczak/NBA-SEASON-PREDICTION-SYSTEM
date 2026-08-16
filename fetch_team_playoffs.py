"""
fetch_team_playoffs.py
----------------------
Builds and hydrates team_stats_playoffs in nba_data.db
for seasons 2020-21 through 2025-26.

Schema mirrors team_stats exactly.

API calls per season (6 total — 3 endpoints × 2 season types):
  For each of SeasonType="Playoffs" and "Play In":
    1. LeagueDashTeamStats — Totals Base     -> W, L, total_pts (for exact per-game after merge)
    2. LeagueDashTeamStats — PerGame Opponent-> opp_pts_per_game
    3. LeagueDashTeamStats — PerGame Advanced-> off/def/net ratings (raw + adj), pace

Merge logic for teams appearing in BOTH season types:
  W, L, total_pts      : summed (counting stats)
  pts_per_game         : re-derived from total_pts / (W + L)
  opp_pts_per_game     : GP-weighted average  (GP = W + L)
  off/def/net ratings  : GP-weighted average
  adj_off/def/net      : GP-weighted average
  pace                 : GP-weighted average
  win_pct              : re-derived from W / (W + L)

Columns not applicable to postseason:
  conference_seed      : NULL
  made_playoffs        : 1  (all teams here are in the postseason)
  prev_season / prev_seed / prev_playoff_result : NULL

Anti-bot: random 4.5–8.2 s sleep between every API request.
"""

import argparse
import sqlite3
import time
import random
import math
import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import LeagueDashTeamStats
from nba_api.stats.static import teams as nba_teams

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

SEASONS = [
    "2020-21",
    "2021-22",
    "2022-23",
    "2023-24",
    "2024-25",
    "2025-26",
]

SEASON_TYPE_PLAYOFFS = "Playoffs"
SEASON_TYPE_PLAYIN   = "Play In"

TIMEOUT = 90

TEAM_ABBR: dict[int, str] = {
    t["id"]: t["abbreviation"]
    for t in nba_teams.get_teams()
}

# ---------------------------------------------------------------------------
# DDL — exact mirror of team_stats
# ---------------------------------------------------------------------------

DDL_TABLE = """
CREATE TABLE IF NOT EXISTS team_stats_playoffs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    season              TEXT    NOT NULL,
    team_id             INTEGER NOT NULL,
    team_name           TEXT    NOT NULL,
    team_abbr           TEXT    NOT NULL,

    pts_per_game        REAL,
    opp_pts_per_game    REAL,
    off_rating          REAL,
    def_rating          REAL,
    net_rating          REAL,
    adj_off_rating      REAL,
    adj_def_rating      REAL,
    adj_net_rating      REAL,
    pace                REAL,

    wins                INTEGER,
    losses              INTEGER,
    win_pct             REAL,
    conference_seed     INTEGER,
    made_playoffs       INTEGER DEFAULT 1,

    prev_season         TEXT,
    prev_seed           INTEGER,
    prev_playoff_result TEXT,

    created_at          TEXT DEFAULT (datetime('now')),
    UNIQUE (season, team_id)
);
"""

DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_tsp_season ON team_stats_playoffs (season);",
    "CREATE INDEX IF NOT EXISTS idx_tsp_team   ON team_stats_playoffs (team_id);",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def snooze(label: str = "") -> None:
    t = random.uniform(4.5, 8.2)
    tag = f" [{label}]" if label else ""
    print(f"    [wait]  sleeping {t:.1f}s{tag} ...", flush=True)
    time.sleep(t)


def safe_float(val) -> "float | None":
    try:
        f = float(val)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def safe_int(val) -> "int | None":
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def try_fetch(fn, season: str, season_type: str, label: str):
    """
    Call fn(season, season_type). If it fails, try alternate spelling
    for Play In ("PlayIn"). Returns empty dict on complete failure.
    """
    alternates = [season_type]
    if season_type == SEASON_TYPE_PLAYIN:
        alternates.append("PlayIn")

    for st in alternates:
        try:
            result = fn(season, st)
            if result:
                return result
        except Exception as exc:
            print(f"      [warn] {label} [{st}]: {exc}", flush=True)

    print(f"      [skip] {label} [{season_type}] — no data.", flush=True)
    return {}


# ---------------------------------------------------------------------------
# Fetch functions (one season type at a time)
# ---------------------------------------------------------------------------

def _fetch_base_totals(season: str, season_type: str) -> dict:
    """
    Totals Base: W, L, total PTS.
    Totals mode ensures PTS is a raw count so we can sum across season types
    and re-derive pts_per_game accurately after merging.
    """
    r = LeagueDashTeamStats(
        season=season,
        per_mode_detailed="Totals",
        measure_type_detailed_defense="Base",
        season_type_all_star=season_type,
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    if df is None or df.empty:
        return {}

    out = {}
    for _, row in df.iterrows():
        tid = safe_int(row.get("TEAM_ID"))
        if tid is None:
            continue
        w = safe_int(row.get("W")) or 0
        l = safe_int(row.get("L")) or 0
        out[tid] = {
            "team_name":  str(row.get("TEAM_NAME", "")),
            "team_abbr":  TEAM_ABBR.get(tid, str(row.get("TEAM_ABBREVIATION", ""))),
            "wins":       w,
            "losses":     l,
            "gp":         w + l,
            "total_pts":  safe_float(row.get("PTS")) or 0.0,
        }
    return out


def fetch_base_totals(season: str, season_type: str) -> dict:
    label = f"Totals Base [{season_type}]"
    print(f"    -> {label} ...", flush=True)
    result = try_fetch(_fetch_base_totals, season, season_type, label)
    print(f"       {len(result)} teams", flush=True)
    return result


def _fetch_opponent(season: str, season_type: str) -> dict:
    """PerGame Opponent: opp_pts_per_game."""
    r = LeagueDashTeamStats(
        season=season,
        per_mode_detailed="PerGame",
        measure_type_detailed_defense="Opponent",
        season_type_all_star=season_type,
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    if df is None or df.empty:
        return {}

    out = {}
    for _, row in df.iterrows():
        tid = safe_int(row.get("TEAM_ID"))
        if tid is None:
            continue
        w  = safe_int(row.get("W")) or 0
        l  = safe_int(row.get("L")) or 0
        out[tid] = {
            "gp":               w + l,
            "opp_pts_per_game": safe_float(row.get("OPP_PTS")),
        }
    return out


def fetch_opponent(season: str, season_type: str) -> dict:
    label = f"Opponent PerGame [{season_type}]"
    print(f"    -> {label} ...", flush=True)
    result = try_fetch(_fetch_opponent, season, season_type, label)
    print(f"       {len(result)} teams", flush=True)
    return result


def _fetch_advanced(season: str, season_type: str) -> dict:
    """PerGame Advanced: off/def/net ratings (raw + adj), pace."""
    r = LeagueDashTeamStats(
        season=season,
        per_mode_detailed="PerGame",
        measure_type_detailed_defense="Advanced",
        season_type_all_star=season_type,
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    if df is None or df.empty:
        return {}

    out = {}
    for _, row in df.iterrows():
        tid = safe_int(row.get("TEAM_ID"))
        if tid is None:
            continue
        w = safe_int(row.get("W")) or 0
        l = safe_int(row.get("L")) or 0
        out[tid] = {
            "gp":             w + l,
            "off_rating":     safe_float(row.get("OFF_RATING")),
            "def_rating":     safe_float(row.get("DEF_RATING")),
            "net_rating":     safe_float(row.get("NET_RATING")),
            "adj_off_rating": safe_float(row.get("E_OFF_RATING")),
            "adj_def_rating": safe_float(row.get("E_DEF_RATING")),
            "adj_net_rating": safe_float(row.get("E_NET_RATING")),
            "pace":           safe_float(row.get("PACE")),
        }
    return out


def fetch_advanced(season: str, season_type: str) -> dict:
    label = f"Advanced PerGame [{season_type}]"
    print(f"    -> {label} ...", flush=True)
    result = try_fetch(_fetch_advanced, season, season_type, label)
    print(f"       {len(result)} teams", flush=True)
    return result


# ---------------------------------------------------------------------------
# Fetch all 3 endpoints for one season type
# ---------------------------------------------------------------------------

def fetch_season_type(season: str, season_type: str) -> dict:
    """
    Returns {team_id: record_dict} for one season type.
    Includes a 'gp' key used for GP-weighted merging.
    """
    base = fetch_base_totals(season, season_type); snooze(f"base->opp [{season_type}]")
    opp  = fetch_opponent(season, season_type);    snooze(f"opp->adv [{season_type}]")
    adv  = fetch_advanced(season, season_type)

    all_tids = set(base) | set(adv)
    records  = {}

    for tid in all_tids:
        b = base.get(tid, {})
        o = opp.get(tid, {})
        a = adv.get(tid, {})

        gp = b.get("gp") or a.get("gp") or 0
        w  = b.get("wins")   or 0
        l  = b.get("losses") or 0

        records[tid] = {
            "team_name":  b.get("team_name", TEAM_ABBR.get(tid, "")),
            "team_abbr":  b.get("team_abbr", TEAM_ABBR.get(tid, "")),
            "gp":         gp,
            "wins":       w,
            "losses":     l,
            "total_pts":  b.get("total_pts", 0.0),

            "opp_pts_per_game": o.get("opp_pts_per_game"),

            "off_rating":     a.get("off_rating"),
            "def_rating":     a.get("def_rating"),
            "net_rating":     a.get("net_rating"),
            "adj_off_rating": a.get("adj_off_rating"),
            "adj_def_rating": a.get("adj_def_rating"),
            "adj_net_rating": a.get("adj_net_rating"),
            "pace":           a.get("pace"),
        }

    return records


# ---------------------------------------------------------------------------
# GP-weighted merge of Playoffs + Play-In
# ---------------------------------------------------------------------------

RATE_FIELDS = [
    "opp_pts_per_game",
    "off_rating", "def_rating", "net_rating",
    "adj_off_rating", "adj_def_rating", "adj_net_rating",
    "pace",
]


def merge_season_types(playoffs: dict, playin: dict) -> dict:
    """
    Merge Play-In and Playoffs data per team.

    Counting stats (wins, losses, total_pts): summed.
    Rate stats: GP-weighted average.
    pts_per_game and win_pct: re-derived from merged totals.
    Identity (team_name, team_abbr): Playoffs preferred.
    """
    all_tids = set(playoffs) | set(playin)
    merged   = {}

    for tid in all_tids:
        in_po = tid in playoffs
        in_pi = tid in playin

        if in_po and in_pi:
            po    = playoffs[tid]
            pi    = playin[tid]
            gp_po = po.get("gp") or 0
            gp_pi = pi.get("gp") or 0
            total = gp_po + gp_pi

            # Sum counting stats
            wins      = (po.get("wins")      or 0) + (pi.get("wins")      or 0)
            losses    = (po.get("losses")    or 0) + (pi.get("losses")    or 0)
            total_pts = (po.get("total_pts") or 0) + (pi.get("total_pts") or 0)

            rec = {
                "team_name":  po.get("team_name", pi.get("team_name", "")),
                "team_abbr":  po.get("team_abbr", pi.get("team_abbr", "")),
                "gp":         total,
                "wins":       wins,
                "losses":     losses,
                "total_pts":  total_pts,
            }

            # GP-weight rate stats
            for f in RATE_FIELDS:
                a, b = po.get(f), pi.get(f)
                if a is not None and b is not None and total > 0:
                    rec[f] = (a * gp_po + b * gp_pi) / total
                elif a is not None:
                    rec[f] = a
                else:
                    rec[f] = b

        elif in_po:
            rec = dict(playoffs[tid])
        else:
            rec = dict(playin[tid])

        # Re-derive rate stats from merged counts
        gp = rec.get("gp") or 0
        w  = rec.get("wins")      or 0
        l  = rec.get("losses")    or 0
        tp = rec.get("total_pts") or 0.0

        rec["pts_per_game"] = round(tp / gp, 1) if gp > 0 else None
        rec["win_pct"]      = round(w / (w + l), 3) if (w + l) > 0 else None

        merged[tid] = rec

    return merged


# ---------------------------------------------------------------------------
# DB upsert
# ---------------------------------------------------------------------------

UPSERT_SQL = """
INSERT INTO team_stats_playoffs (
    season, team_id, team_name, team_abbr,
    pts_per_game, opp_pts_per_game,
    off_rating, def_rating, net_rating,
    adj_off_rating, adj_def_rating, adj_net_rating,
    pace,
    wins, losses, win_pct,
    conference_seed, made_playoffs,
    prev_season, prev_seed, prev_playoff_result
) VALUES (
    :season, :team_id, :team_name, :team_abbr,
    :pts_per_game, :opp_pts_per_game,
    :off_rating, :def_rating, :net_rating,
    :adj_off_rating, :adj_def_rating, :adj_net_rating,
    :pace,
    :wins, :losses, :win_pct,
    :conference_seed, :made_playoffs,
    :prev_season, :prev_seed, :prev_playoff_result
)
ON CONFLICT (season, team_id) DO UPDATE SET
    team_name           = excluded.team_name,
    team_abbr           = excluded.team_abbr,
    pts_per_game        = excluded.pts_per_game,
    opp_pts_per_game    = excluded.opp_pts_per_game,
    off_rating          = excluded.off_rating,
    def_rating          = excluded.def_rating,
    net_rating          = excluded.net_rating,
    adj_off_rating      = excluded.adj_off_rating,
    adj_def_rating      = excluded.adj_def_rating,
    adj_net_rating      = excluded.adj_net_rating,
    pace                = excluded.pace,
    wins                = excluded.wins,
    losses              = excluded.losses,
    win_pct             = excluded.win_pct,
    conference_seed     = excluded.conference_seed,
    made_playoffs       = excluded.made_playoffs
"""


def upsert_season(con: sqlite3.Connection, season: str, merged: dict) -> int:
    # Schema-aware write: the live team_stats_playoffs table may have drifted from
    # this script's DDL (e.g. migrations dropped made_playoffs/prev_season and added
    # playoff_result/conference). Insert only the intersection of columns this fetcher
    # produces and columns that actually exist, so a raw re-fetch stays compatible.
    live_cols = [r[1] for r in con.execute("PRAGMA table_info(team_stats_playoffs)")]
    produced = {
        "season", "team_id", "team_name", "team_abbr",
        "pts_per_game", "opp_pts_per_game",
        "off_rating", "def_rating", "net_rating",
        "adj_off_rating", "adj_def_rating", "adj_net_rating", "pace",
        "wins", "losses", "win_pct",
        "conference_seed", "made_playoffs",
        "prev_season", "prev_seed", "prev_playoff_result",
    }
    write_cols = [c for c in live_cols if c in produced]

    rows = []
    for tid, rec in merged.items():
        full = {
            "season":   season,
            "team_id":  tid,
            "team_name": rec.get("team_name", ""),
            "team_abbr": rec.get("team_abbr", TEAM_ABBR.get(tid, "")),

            "pts_per_game":     rec.get("pts_per_game"),
            "opp_pts_per_game": rec.get("opp_pts_per_game"),

            "off_rating":     rec.get("off_rating"),
            "def_rating":     rec.get("def_rating"),
            "net_rating":     rec.get("net_rating"),
            "adj_off_rating": rec.get("adj_off_rating"),
            "adj_def_rating": rec.get("adj_def_rating"),
            "adj_net_rating": rec.get("adj_net_rating"),
            "pace":           rec.get("pace"),

            "wins":    rec.get("wins"),
            "losses":  rec.get("losses"),
            "win_pct": rec.get("win_pct"),

            "conference_seed":     None,
            "made_playoffs":       1,
            "prev_season":         None,
            "prev_seed":           None,
            "prev_playoff_result": None,
        }
        rows.append({c: full.get(c) for c in write_cols})

    col_list     = ", ".join(write_cols)
    placeholders = ", ".join(f":{c}" for c in write_cols)
    sql = f"INSERT INTO team_stats_playoffs ({col_list}) VALUES ({placeholders})"

    cur = con.cursor()
    # Additive + idempotent for THIS season only; never touches other seasons.
    cur.execute("DELETE FROM team_stats_playoffs WHERE season = ?", (season,))
    cur.executemany(sql, rows)
    con.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Schema setup
# ---------------------------------------------------------------------------

def ensure_schema(con: sqlite3.Connection) -> None:
    cur = con.cursor()
    cur.execute(DDL_TABLE)
    for idx in DDL_INDEXES:
        cur.execute(idx)
    con.commit()
    print("  [schema] team_stats_playoffs table ready.", flush=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def print_win_loss_table(con: sqlite3.Connection, season: str) -> None:
    rows = con.execute(
        """
        SELECT team_abbr, wins, losses, win_pct
        FROM team_stats_playoffs
        WHERE season = ?
        ORDER BY wins DESC, losses ASC, team_abbr
        """,
        (season,),
    ).fetchall()
    print(f"\n  Postseason W/L — {season} ({len(rows)} teams):", flush=True)
    for abbr, wins, losses, win_pct in rows:
        pct = f"{win_pct:.3f}" if win_pct is not None else "  —  "
        print(f"    {abbr:<4} {wins:>2}-{losses:<2}  win_pct={pct}", flush=True)


def run(seasons: "list[str] | None" = None) -> None:
    # Default keeps the original behaviour (full SEASONS list); a caller or the
    # --season flag can narrow it to a single season without touching the rest.
    seasons = list(seasons) if seasons else list(SEASONS)

    print(f"[fetch_team_playoffs] DB: {DB_PATH}", flush=True)
    print(f"[fetch_team_playoffs] seasons: {', '.join(seasons)}", flush=True)
    con = sqlite3.connect(DB_PATH)
    ensure_schema(con)
    grand_total = 0

    for i, season in enumerate(seasons, 1):
        print(f"\n{'='*60}", flush=True)
        print(f"[TEAM POSTSEASON {season}]  ({i}/{len(seasons)})", flush=True)
        print(f"{'='*60}", flush=True)

        try:
            # --- Fetch Playoffs ---
            print(f"\n  -- Playoffs --", flush=True)
            po_data = fetch_season_type(season, SEASON_TYPE_PLAYOFFS)

            snooze(f"Playoffs done -> Play-In [{season}]")

            # --- Fetch Play-In ---
            print(f"\n  -- Play-In --", flush=True)
            pi_data = fetch_season_type(season, SEASON_TYPE_PLAYIN)

            # --- Merge ---
            po_only = len(set(po_data) - set(pi_data))
            pi_only = len(set(pi_data) - set(po_data))
            both    = len(set(po_data) & set(pi_data))
            print(
                f"\n  [merge]  Playoffs-only={po_only}  "
                f"PlayIn-only={pi_only}  Both={both}",
                flush=True,
            )

            merged = merge_season_types(po_data, pi_data)
            n = upsert_season(con, season, merged)
            grand_total += n
            print(f"\n[TEAM POSTSEASON {season}] Inserted {n} teams.", flush=True)
            print_win_loss_table(con, season)

        except Exception as exc:
            print(f"  [ERROR] {season}: {exc}", flush=True)
            import traceback
            traceback.print_exc()
            con.rollback()
            time.sleep(10)

        if i < len(seasons):
            print(f"  [cooldown between seasons: 10s]", flush=True)
            time.sleep(10)

    con.close()
    print(f"\n{'='*60}", flush=True)
    print(f"  DONE — {grand_total} total team records across {len(seasons)} seasons.", flush=True)
    print(f"  Database: {DB_PATH}", flush=True)

    # Sanity check
    con2 = sqlite3.connect(DB_PATH)
    cur2 = con2.cursor()
    cur2.execute("""
        SELECT season, COUNT(*) as teams,
               ROUND(AVG(wins), 1), ROUND(AVG(pts_per_game), 1),
               ROUND(AVG(net_rating), 2)
        FROM team_stats_playoffs
        GROUP BY season ORDER BY season
    """)
    print(f"\n  Season summary (teams | avg_wins | avg_pts | avg_net_rtg):", flush=True)
    for row in cur2.fetchall():
        print(f"    {row[0]}: {row[1]} teams | wins {row[2]} | pts {row[3]} | net {row[4]}", flush=True)
    con2.close()


def parse_args(argv: "list[str] | None" = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch team_stats_playoffs from the NBA API."
    )
    parser.add_argument(
        "--season",
        help=(
            "Fetch a single season, e.g. 2025-26. Only that season's rows are "
            "deleted and reinserted. Omit to fetch the full SEASONS list."
        ),
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    run([args.season] if args.season else None)
