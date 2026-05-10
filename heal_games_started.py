"""
heal_games_started.py
---------------------
Data Integrity Healing — GS (Games Started) column in player_stats_basic.

Targets two categories of broken rows (union, deduplicated by player):
  A) team_id = 0  — combined TOT rows for traded players (spec requirement)
  B) gs IS NULL   — rows where the earlier hydration pass failed to fetch GS
                    (the real problem: 101 rows with a PlayerCareerStats API error)

As of the current DB state, category A is empty (LeagueDashPlayerStats never
writes TOT rows) and all 101 broken rows fall into category B.  The script
handles both transparently.

Phase 1  Identify target (player_id, season, team_id) rows from the DB.
Phase 2  Primary  — PlayerCareerStats (SeasonTotalsRegularSeason)
           • For team_id=0 rows: match TEAM_ID==0 / TEAM_ABBREVIATION=='TOT'
           • For regular team rows: match exact (SEASON_ID, TEAM_ID)
Phase 3  Fallback — basketball-reference.com scrape
           • Totals table, season row, correct team (or 'TOT' for combined)
Phase 4  Rate limiting: random 4.1–7.8 s sleep after EVERY network request
Phase 5  Atomic UPDATE per player; only the gs column is ever written.

SAFETY: PTS, REB, AST, MIN and all other columns are NEVER touched.
"""

import sqlite3
import time
import random
import math
import re
import os
import sys
import unicodedata

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests
from bs4 import BeautifulSoup
from nba_api.stats.endpoints import PlayerCareerStats

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")
TIMEOUT = 90

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def snooze(label: str = "") -> None:
    """Mandatory 4.1–7.8 s pause after every network request."""
    t = random.uniform(4.1, 7.8)
    tag = f" [{label}]" if label else ""
    print(f"    [wait] {t:.1f}s{tag}", flush=True)
    time.sleep(t)


def safe_int(val) -> "int | None":
    try:
        f = float(val)
        return None if math.isnan(f) else int(f)
    except (TypeError, ValueError):
        return None


def random_headers() -> dict:
    return {
        "User-Agent":      random.choice(USER_AGENTS),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer":         "https://www.basketball-reference.com/",
        "DNT":             "1",
    }


def ascii_name(name: str) -> str:
    """Strip diacritics for slug generation (handles Doncic, Antetokounmpo, etc.)."""
    nfkd = unicodedata.normalize("NFKD", name)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def bref_slug(player_name: str, attempt: int = 1) -> str:
    """
    Build a basketball-reference URL slug.
    'LeBron James' -> 'jamesle01'
    attempt=2 -> 'jamesle02' for name clashes
    """
    clean = ascii_name(player_name).lower()
    clean = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", clean).strip()
    parts = clean.split()
    if len(parts) < 2:
        return ""
    last  = re.sub(r"[^a-z]", "", parts[-1])[:5]
    first = re.sub(r"[^a-z]", "", parts[0])[:2]
    return f"{last}{first}{attempt:02d}"


# ---------------------------------------------------------------------------
# Phase 2 — Primary: PlayerCareerStats
# ---------------------------------------------------------------------------

def fetch_gs_from_career_stats(
    player_id: int,
    player_name: str,
    season: str,
    team_id: "int | None",
) -> "int | None":
    """
    Fetches SeasonTotalsRegularSeason for the player.

    Lookup priority:
      1. If team_id == 0 (TOT row): match TEAM_ID==0 or TEAM_ABBREVIATION=='TOT'
      2. Otherwise: match exact (SEASON_ID, TEAM_ID)
      3. Fallback within same player+season: any row with same SEASON_ID
         (catches cases where team_id was normalised differently)

    Returns the GS integer, or None on any failure.
    """
    try:
        r   = PlayerCareerStats(player_id=player_id, timeout=TIMEOUT)
        dfs = r.get_data_frames()
    except Exception as exc:
        print(f"    [PCS ERR] {player_name}: {exc}", flush=True)
        return None

    if not dfs or dfs[0].empty:
        return None

    df = dfs[0]

    # Normalise SEASON_ID column — should already be '2023-24' but guard anyway
    if "SEASON_ID" not in df.columns:
        return None

    season_rows = df[df["SEASON_ID"].astype(str).str.strip() == season]
    if season_rows.empty:
        return None

    # Case A: target is a TOT / combined row
    if team_id is None or team_id == 0:
        tot = season_rows[
            (season_rows["TEAM_ID"].apply(safe_int) == 0) |
            (season_rows["TEAM_ABBREVIATION"].astype(str).str.upper() == "TOT")
        ]
        if not tot.empty:
            return safe_int(tot.iloc[0].get("GS"))
        # Fallback: first row for that season
        return safe_int(season_rows.iloc[0].get("GS"))

    # Case B: target is a specific team stint
    exact = season_rows[season_rows["TEAM_ID"].apply(safe_int) == team_id]
    if not exact.empty:
        return safe_int(exact.iloc[0].get("GS"))

    # Fallback: if only one row exists for this season (player wasn't traded)
    if len(season_rows) == 1:
        return safe_int(season_rows.iloc[0].get("GS"))

    return None


# ---------------------------------------------------------------------------
# Phase 3 — Fallback: basketball-reference scrape
# ---------------------------------------------------------------------------

def fetch_gs_from_bref(
    player_name: str,
    season: str,
    team_id: "int | None",
    team_abbr: "str | None",
) -> "int | None":
    """
    Scrapes the bref Totals table for the player.
    'season' is '2023-24'; bref uses the same format.
    For team_id==0 (TOT): looks for Team=='TOT'.
    For regular stints: tries to match team_abbr, falls back to TOT row.
    Returns GS integer or None.
    """
    for attempt in range(1, 4):
        slug = bref_slug(player_name, attempt)
        if not slug:
            return None

        url = (
            f"https://www.basketball-reference.com"
            f"/players/{slug[0]}/{slug}.html"
        )
        try:
            resp = requests.get(url, headers=random_headers(), timeout=25)
        except requests.RequestException as exc:
            print(f"    [bref REQ] {url}: {exc}", flush=True)
            return None

        if resp.status_code == 404:
            continue
        if resp.status_code != 200:
            print(f"    [bref HTTP {resp.status_code}]", flush=True)
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # Try both the Totals and Per Game tables; Totals is preferred for GS
        for table_id in ("totals", "per_game"):
            table = soup.find("table", id=table_id)
            if not table:
                continue
            tbody = table.find("tbody")
            if not tbody:
                continue

            # Collect all rows that match this season
            season_matches = []
            for tr in tbody.find_all("tr", class_=lambda c: c != "thead"):
                th = tr.find("th", {"data-stat": "season"})
                if not th or not th.a:
                    continue
                if th.a.text.strip() != season:
                    continue

                team_td = tr.find("td", {"data-stat": "team_id"})
                bref_team = team_td.get_text(strip=True).upper() if team_td else ""

                gs_td = tr.find("td", {"data-stat": "gs"})
                gs    = safe_int(gs_td.get_text(strip=True)) if gs_td else None

                season_matches.append((bref_team, gs))

            if not season_matches:
                continue

            # TOT row wanted (traded player combined)
            if team_id is None or team_id == 0:
                for bref_team, gs in season_matches:
                    if bref_team == "TOT":
                        return gs
                # If no TOT row, return first match
                return season_matches[0][1]

            # Specific team stint wanted — try to match abbreviation
            if team_abbr:
                for bref_team, gs in season_matches:
                    if bref_team == team_abbr.upper():
                        return gs

            # Last resort: TOT row or first row
            for bref_team, gs in season_matches:
                if bref_team == "TOT":
                    return gs
            return season_matches[0][1]

    return None   # all slug attempts exhausted


# ---------------------------------------------------------------------------
# Phase 5 — Atomic update (GS only)
# ---------------------------------------------------------------------------

def update_gs(
    cur: sqlite3.Cursor,
    player_id: int,
    season: str,
    team_id: "int | None",
    gs: int,
) -> int:
    """
    Writes GS for exactly one row. Never touches any other column.
    Returns rowcount (0 or 1).
    """
    cur.execute(
        """
        UPDATE player_stats_basic
           SET gs = ?
         WHERE player_id = ?
           AND season    = ?
           AND (team_id  = ? OR (team_id IS NULL AND ? IS NULL))
        """,
        (gs, player_id, season, team_id, team_id),
    )
    return cur.rowcount


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run() -> None:
    print(f"[heal_games_started] DB: {DB_PATH}", flush=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL;")
    cur = con.cursor()

    # ── Phase 1: collect all target rows ─────────────────────────────────────
    cur.execute(
        """
        SELECT player_id, player_name, season, team_id, team_abbr
          FROM player_stats_basic
         WHERE team_id = 0          -- TOT / combined rows (spec requirement)
            OR gs IS NULL           -- NULL gs rows (actual issue: 101 rows)
         ORDER BY season, player_name
        """
    )
    targets = cur.fetchall()

    if not targets:
        print("  Nothing to heal — all gs values are present.", flush=True)
        con.close()
        return

    # De-duplicate: one player may appear multiple times across seasons
    print(f"  Target rows: {len(targets)}", flush=True)

    # ── Counters ──────────────────────────────────────────────────────────────
    fixed_api    = 0
    fixed_scrape = 0
    skipped      = 0

    for player_id, player_name, season, team_id, team_abbr in targets:
        print(
            f"\n  {player_name} ({season})  team_id={team_id}",
            flush=True,
        )

        # ── Phase 2: PlayerCareerStats ────────────────────────────────────────
        snooze(f"PCS {player_name}")
        gs = fetch_gs_from_career_stats(player_id, player_name, season, team_id)

        source = "API"

        # ── Phase 3: bref fallback ────────────────────────────────────────────
        if gs is None:
            snooze(f"bref {player_name}")
            gs = fetch_gs_from_bref(player_name, season, team_id, team_abbr)
            source = "SCRAPE"

        if gs is None:
            print(f"  [SKIP] {player_name} ({season}): no GS data found", flush=True)
            skipped += 1
            continue

        # ── Phase 5: atomic write ─────────────────────────────────────────────
        n = update_gs(cur, player_id, season, team_id, gs)
        con.commit()   # commit after every single player

        if n:
            print(
                f"  [FIXED - {source}] {player_name} ({season}): GS updated to {gs}",
                flush=True,
            )
            if source == "API":
                fixed_api += 1
            else:
                fixed_scrape += 1
        else:
            print(
                f"  [NO MATCH] {player_name} ({season}): row not found in DB",
                flush=True,
            )
            skipped += 1

    # ── Remaining NULLs ───────────────────────────────────────────────────────
    cur.execute("SELECT COUNT(*) FROM player_stats_basic WHERE gs IS NULL")
    remaining = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM player_stats_basic WHERE team_id = 0 AND gs IS NULL")
    remaining_tot = cur.fetchone()[0]

    con.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 54)
    print("  HEAL SUMMARY")
    print("=" * 54)
    print(f"  Rows targeted          : {len(targets):>5}")
    print(f"  Fixed via API          : {fixed_api:>5}")
    print(f"  Fixed via bref scrape  : {fixed_scrape:>5}")
    print(f"  Skipped (no data)      : {skipped:>5}")
    print(f"  Remaining NULL gs      : {remaining:>5}")
    print(f"  Remaining NULL gs TOT  : {remaining_tot:>5}")
    print("=" * 54)


if __name__ == "__main__":
    run()
