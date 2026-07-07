"""Fix 4: gs (games started) in player_stats_basic for the three NEW seasons.

Replicates archive/heal_games_started.py logic exactly:
  * Primary : PlayerCareerStats (SeasonTotalsRegularSeason) -> GS per (season, team_id)
  * Fallback: basketball-reference Totals/Per-Game scrape when PCS fails
  * Polite rate limiting: random 4.1-7.8 s sleep after EVERY network request
  * Only ever writes the `gs` column.

Deduped to one PCS call (and at most one bref fetch) per distinct player instead
of per-row, so multi-season players are fetched once. Same endpoint, same data,
same rate-limiting, fewer redundant calls. Commits per player -> resumable.

Scope: NEW_SEASONS only, rows where gs IS NULL.
"""
import math
import os
import random
import re
import sqlite3
import sys
import time
import unicodedata

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests
from bs4 import BeautifulSoup
from nba_api.stats.endpoints import PlayerCareerStats

from config import DB_PATH, NEW_SEASONS

TIMEOUT = 90
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


def snooze(label=""):
    t = random.uniform(4.1, 7.8)
    print(f"    [wait] {t:.1f}s {('['+label+']') if label else ''}", flush=True)
    time.sleep(t)


def safe_int(val):
    try:
        f = float(val)
        return None if math.isnan(f) else int(f)
    except (TypeError, ValueError):
        return None


def random_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.basketball-reference.com/",
        "DNT": "1",
    }


def ascii_name(name):
    nfkd = unicodedata.normalize("NFKD", name)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def bref_slug(player_name, attempt=1):
    clean = ascii_name(player_name).lower()
    clean = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", clean).strip()
    parts = clean.split()
    if len(parts) < 2:
        return ""
    last = re.sub(r"[^a-z]", "", parts[-1])[:5]
    first = re.sub(r"[^a-z]", "", parts[0])[:2]
    return f"{last}{first}{attempt:02d}"


def fetch_pcs_df(player_id, player_name):
    try:
        dfs = PlayerCareerStats(player_id=player_id, timeout=TIMEOUT).get_data_frames()
    except Exception as exc:
        print(f"    [PCS ERR] {player_name}: {exc}", flush=True)
        return None
    if not dfs or dfs[0].empty or "SEASON_ID" not in dfs[0].columns:
        return None
    return dfs[0]


def gs_from_pcs(df, season, team_id):
    """Mirror heal_games_started.fetch_gs_from_career_stats lookup priority."""
    if df is None:
        return None
    season_rows = df[df["SEASON_ID"].astype(str).str.strip() == season]
    if season_rows.empty:
        return None
    if team_id is None or team_id == 0:
        tot = season_rows[
            (season_rows["TEAM_ID"].apply(safe_int) == 0)
            | (season_rows["TEAM_ABBREVIATION"].astype(str).str.upper() == "TOT")
        ]
        if not tot.empty:
            return safe_int(tot.iloc[0].get("GS"))
        return safe_int(season_rows.iloc[0].get("GS"))
    exact = season_rows[season_rows["TEAM_ID"].apply(safe_int) == team_id]
    if not exact.empty:
        return safe_int(exact.iloc[0].get("GS"))
    if len(season_rows) == 1:
        return safe_int(season_rows.iloc[0].get("GS"))
    return None


# NBA/DB abbreviations that differ from basketball-reference's.
ABBR_NORM = {"BKN": "BRK", "PHX": "PHO", "CHA": "CHO"}
COMBINED_TEAMS = {"TOT", "2TM", "3TM", "4TM"}


def _norm_abbr(a):
    if not a:
        return ""
    a = a.upper()
    return ABBR_NORM.get(a, a)


def fetch_bref_page(player_name):
    """Fetch bref page once; return {season: [(team_abbr, gs)]} or None.

    Current basketball-reference layout: table id 'per_game_stats' / 'totals_stats',
    season in <th data-stat='year_id'>, team in td 'team_name_abbr', GS in 'games_started'.
    """
    for attempt in range(1, 4):
        slug = bref_slug(player_name, attempt)
        if not slug:
            return None
        url = f"https://www.basketball-reference.com/players/{slug[0]}/{slug}.html"
        try:
            resp = requests.get(url, headers=random_headers(), timeout=25)
        except requests.RequestException as exc:
            print(f"    [bref REQ] {url}: {exc}", flush=True)
            return None
        if resp.status_code == 404:
            continue
        if resp.status_code != 200:
            print(f"    [bref HTTP {resp.status_code}] {url}", flush=True)
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        season_map = {}
        for table_id in ("per_game_stats", "totals_stats"):
            table = soup.find("table", id=table_id)
            if not table:
                continue
            tbody = table.find("tbody")
            if not tbody:
                continue
            for tr in tbody.find_all("tr"):
                th = tr.find("th", {"data-stat": "year_id"})
                if not th:
                    continue
                sid = th.get_text(strip=True)
                if sid not in NEW_SEASONS:
                    continue
                team_td = tr.find("td", {"data-stat": "team_name_abbr"})
                bteam = team_td.get_text(strip=True).upper() if team_td else ""
                gs_td = tr.find("td", {"data-stat": "games_started"})
                gs = safe_int(gs_td.get_text(strip=True)) if gs_td else None
                season_map.setdefault(sid, []).append((bteam, gs))
            if season_map:
                return season_map
        # Page loaded (200) but has no rows for our new seasons -> almost always a
        # namesake occupying this slug number. Try the next slug attempt (02, 03).
        continue
    return None


def gs_from_bref(season_map, season, team_id, team_abbr):
    if not season_map or season not in season_map:
        return None
    matches = season_map[season]
    if team_id is None or team_id == 0:
        for bteam, gs in matches:
            if bteam in COMBINED_TEAMS:
                return gs
        return matches[0][1]
    if team_abbr:
        want = _norm_abbr(team_abbr)
        for bteam, gs in matches:
            if _norm_abbr(bteam) == want:
                return gs
    # Single-stint season: the one non-combined row is unambiguous.
    non_combined = [(bt, gs) for bt, gs in matches if bt not in COMBINED_TEAMS]
    if len(non_combined) == 1:
        return non_combined[0][1]
    for bteam, gs in matches:
        if bteam in COMBINED_TEAMS:
            return gs
    return matches[0][1]


def main():
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL;")
    cur = con.cursor()

    ph = ",".join("?" for _ in NEW_SEASONS)
    cur.execute(
        f"""
        SELECT player_id, player_name, season, team_id, team_abbr
          FROM player_stats_basic
         WHERE season IN ({ph}) AND (gs IS NULL OR team_id = 0)
         ORDER BY player_name, season
        """,
        NEW_SEASONS,
    )
    targets = cur.fetchall()
    if not targets:
        print("  Nothing to heal.")
        con.close()
        return

    # Group rows by player
    by_player = {}
    for pid, pname, season, tid, tabbr in targets:
        by_player.setdefault((pid, pname), []).append((season, tid, tabbr))

    total_players = len(by_player)
    total_rows = len(targets)
    print(f"  Target rows: {total_rows}  across {total_players} distinct players", flush=True)

    fixed_api = fixed_scrape = skipped = 0
    for idx, ((pid, pname), rows) in enumerate(by_player.items(), 1):
        print(f"\n  [{idx}/{total_players}] {pname}  ({len(rows)} row(s))", flush=True)

        snooze(f"PCS {pname}")
        df = fetch_pcs_df(pid, pname)

        bref_map = None
        bref_fetched = False

        for season, tid, tabbr in rows:
            gs = gs_from_pcs(df, season, tid)
            source = "API"
            if gs is None:
                if not bref_fetched:
                    snooze(f"bref {pname}")
                    bref_map = fetch_bref_page(pname)
                    bref_fetched = True
                gs = gs_from_bref(bref_map, season, tid, tabbr)
                source = "SCRAPE"
            if gs is None:
                print(f"    [SKIP] {season} team_id={tid}: no GS data", flush=True)
                skipped += 1
                continue
            cur.execute(
                """
                UPDATE player_stats_basic SET gs = ?
                 WHERE player_id = ? AND season = ?
                   AND (team_id = ? OR (team_id IS NULL AND ? IS NULL))
                   AND gs IS NULL
                """,
                (gs, pid, season, tid, tid),
            )
            if cur.rowcount:
                if source == "API":
                    fixed_api += 1
                else:
                    fixed_scrape += 1
                print(f"    [FIXED-{source}] {season} team_id={tid}: gs={gs}", flush=True)
        con.commit()

    cur.execute(f"SELECT season, COUNT(*) FROM player_stats_basic WHERE season IN ({ph}) AND gs IS NULL GROUP BY season", NEW_SEASONS)
    remaining = dict(cur.fetchall())
    con.close()

    print("\n" + "=" * 50)
    print("  FIX 4 (gs) SUMMARY")
    print("=" * 50)
    print(f"  Rows targeted        : {total_rows}")
    print(f"  Fixed via API        : {fixed_api}")
    print(f"  Fixed via bref scrape: {fixed_scrape}")
    print(f"  Skipped (no data)    : {skipped}")
    for s in NEW_SEASONS:
        print(f"  Remaining NULL gs {s}: {remaining.get(s, 0)}")
    print("=" * 50)


if __name__ == "__main__":
    main()
