"""
hydrate_prev_playoff_result.py
------------------------------
Backfills prev_playoff_result (and refreshes prev_seed) in team_stats
for seasons 2020-21 through 2025-26 by analysing the PRIOR season's
playoff bracket.

Two-endpoint strategy
---------------------
CommonPlayoffSeries returns only:
  GAME_ID, HOME_TEAM_ID, VISITOR_TEAM_ID, SERIES_ID, GAME_NUM
It contains NO score or cumulative-wins columns, so we cannot determine
the series winner from it alone.

LeagueGameFinder (season_type='Playoffs') returns one row per team per
game and includes a WL ('W'/'L') column.  We use GAME_ID to join:
  CommonPlayoffSeries   →  SERIES_ID per game (series structure + round)
  LeagueGameFinder      →  winning team per game
  join on GAME_ID       →  count wins per team per series → series winner

Outcome labels
--------------
  "Champion"        – Won the NBA Finals
  "Finals"          – Lost the NBA Finals
  "Conf. Finals"    – Lost the Conference Finals
  "2nd Round"       – Lost the Conference Semifinals
  "1st Round"       – Lost the First Round of the Playoffs
  "Play-In"         – Seeds 7-10 (Play-In era: 2020-21+) that did NOT
                      advance to the First Round
  "Missed Playoffs" – Seeded 11-15 in Play-In era,
                      or seeded 9-15 in the pre-Play-In 2019-20 season

Notes
-----
* The Play-In Tournament was introduced in 2020-21.  For the prior season
  2019-20 (which feeds the 2020-21 target rows) there is no Play-In:
  seeds 1-8 made the playoffs, seeds 9+ missed.
* The 2019-20 Western Conference had an informal "play-in" game between
  the 8th and 9th seeds (Portland vs Memphis).  Memphis did not appear in
  the standard playoff bracket, so with play_in_active=False they are
  labelled "Missed Playoffs", which is the correct pre-Play-In label.
* Round is extracted from position 7 (0-indexed) of the SERIES_ID string:
  e.g. '004190010' → '1' (Round 1), '004190040' → '4' (Finals).
  Series IDs are 9 characters for older seasons and may be 10 for newer;
  the code tries both positions 7 and 6 with a 1-4 range check.

Anti-bot: random 4.5-8.2 s sleep between every API request.
"""

import os
import sys
import time
import random
import sqlite3
import traceback
from collections import Counter

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import CommonPlayoffSeries, LeagueGameFinder

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")
TIMEOUT = 90  # seconds per request

# (target_season_to_update, prior_season_to_analyse)
SEASON_PAIRS = [
    ("2020-21", "2019-20"),
    ("2021-22", "2020-21"),
    ("2022-23", "2021-22"),
    ("2023-24", "2022-23"),
    ("2024-25", "2023-24"),
    ("2025-26", "2024-25"),
]

# The formal Play-In Tournament began in the 2020-21 season.
# Any prior_season >= this string activates Play-In logic (seeds 7-10).
PLAY_IN_SINCE = "2020-21"

ROUND_LOSS_LABEL = {
    1: "1st Round",
    2: "2nd Round",
    3: "Conf. Finals",
    4: "Finals",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def snooze(label: str = "") -> None:
    """Random 4.5-8.2 s anti-bot delay."""
    t = random.uniform(4.5, 8.2)
    tag = f" [{label}]" if label else ""
    print(f"    [wait]  sleeping {t:.1f}s{tag} ...", flush=True)
    time.sleep(t)


def safe_int(val) -> int | None:
    try:
        return int(float(str(val)))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_prior_season_teams(con: sqlite3.Connection, prior_season: str) -> dict:
    """
    Pull every team row for prior_season from team_stats.
    Returns {team_id: {"conference_seed": int|None, "made_playoffs": int}}
    """
    cur = con.cursor()
    cur.execute(
        "SELECT team_id, conference_seed, made_playoffs "
        "FROM team_stats WHERE season = ?",
        (prior_season,),
    )
    return {
        row[0]: {"conference_seed": row[1], "made_playoffs": row[2]}
        for row in cur.fetchall()
    }


def update_db(
    con: sqlite3.Connection,
    target_season: str,
    team_results: dict,   # {team_id: result_str}
    prior_seeds: dict,    # {team_id: conference_seed}
) -> int:
    """
    UPDATE team_stats SET prev_playoff_result, prev_seed
    WHERE season = target_season AND team_id = X.
    Returns the count of rows actually updated.
    """
    cur = con.cursor()
    updated = 0
    for team_id, result in team_results.items():
        seed = prior_seeds.get(team_id)
        cur.execute(
            """
            UPDATE team_stats
               SET prev_playoff_result = ?,
                   prev_seed           = ?
             WHERE season  = ?
               AND team_id = ?
            """,
            (result, seed, target_season, team_id),
        )
        updated += cur.rowcount
    con.commit()
    return updated


# ---------------------------------------------------------------------------
# API fetches
# ---------------------------------------------------------------------------

def fetch_playoff_series(prior_season: str):
    """
    CommonPlayoffSeries → one row per playoff game.
    Columns available: GAME_ID, HOME_TEAM_ID, VISITOR_TEAM_ID, SERIES_ID, GAME_NUM.
    (No score or cumulative-wins columns are returned by this endpoint.)
    """
    print(f"    -> CommonPlayoffSeries ({prior_season}) ...", flush=True)
    resp = CommonPlayoffSeries(
        season=prior_season,
        league_id="00",
        timeout=TIMEOUT,
    )
    df = resp.get_data_frames()[0]
    print(f"       {len(df)} game rows | cols: {list(df.columns)}", flush=True)
    return df


def fetch_game_winners(prior_season: str) -> dict:
    """
    LeagueGameFinder (Playoffs) → {game_id_int: winning_team_id_int}.

    Returns one row per team per game; we keep only 'W' rows to build
    a game_id → winning_team mapping.
    """
    print(f"    -> LeagueGameFinder Playoffs ({prior_season}) ...", flush=True)
    resp = LeagueGameFinder(
        season_nullable=prior_season,
        season_type_nullable="Playoffs",
        league_id_nullable="00",
        timeout=TIMEOUT,
    )
    df = resp.get_data_frames()[0]
    print(f"       {len(df)} team-game rows | cols: {list(df.columns)}", flush=True)

    winners: dict[int, int] = {}
    for _, row in df.iterrows():
        wl = str(row.get("WL", "")).strip().upper()
        if wl != "W":
            continue
        gid = safe_int(row.get("GAME_ID"))
        tid = safe_int(row.get("TEAM_ID"))
        if gid is not None and tid is not None:
            winners[gid] = tid

    print(f"       {len(winners)} game winners resolved", flush=True)
    return winners


# ---------------------------------------------------------------------------
# Series parsing
# ---------------------------------------------------------------------------

def extract_round_from_series_id(series_id: str) -> int:
    """
    Round number lives at position 7 (0-indexed) of the SERIES_ID string.

    Observed formats:
      9-char  '004190010'  →  pos 7 = '1'  (Round 1, 2019-20 season)
      9-char  '004190040'  →  pos 7 = '4'  (Finals, 2019-20 season)

    Also tries position 6 as a fallback for non-standard lengths.
    Returns -1 if parsing fails.
    """
    sid = str(series_id)
    for pos in (7, 6):
        try:
            r = int(sid[pos])
            if 1 <= r <= 4:
                return r
        except (IndexError, ValueError):
            pass
    return -1


def build_series_results(cps_df, game_winners: dict) -> dict:
    """
    Join CommonPlayoffSeries (series structure) with game_winners (W/L per game)
    on GAME_ID to count wins per team per series.

    Returns {team_id: (max_round_reached: int, won_that_round: bool)}
    """
    results: dict[int, tuple[int, bool]] = {}

    for series_id, grp in cps_df.groupby("SERIES_ID"):
        round_num = extract_round_from_series_id(str(series_id))
        if round_num < 1:
            print(f"       [WARN] cannot parse round from SERIES_ID={series_id!r}", flush=True)
            continue

        team_wins: dict[int, int] = {}
        unmatched = 0

        for _, row in grp.iterrows():
            gid  = safe_int(row["GAME_ID"])
            h_id = safe_int(row["HOME_TEAM_ID"])
            v_id = safe_int(row["VISITOR_TEAM_ID"])

            if gid is None or h_id is None or v_id is None:
                continue

            # Seed both teams so even the loser appears in the dict
            team_wins.setdefault(h_id, 0)
            team_wins.setdefault(v_id, 0)

            winning_tid = game_winners.get(gid)
            if winning_tid is not None:
                team_wins[winning_tid] = team_wins.get(winning_tid, 0) + 1
            else:
                unmatched += 1

        if unmatched:
            print(f"       [INFO] {unmatched} game(s) in series {series_id} had no winner match", flush=True)

        if len(team_wins) < 2:
            print(f"       [WARN] < 2 teams found for series {series_id}", flush=True)
            continue

        # Series winner = team with the most game wins (should be 4)
        sorted_teams = sorted(team_wins.items(), key=lambda kv: kv[1], reverse=True)
        winner_id = sorted_teams[0][0]
        loser_id  = sorted_teams[1][0]

        print(
            f"       R{round_num} | {series_id} | "
            f"W={winner_id} ({team_wins[winner_id]}W) "
            f"L={loser_id} ({team_wins[loser_id]}W)",
            flush=True,
        )

        # Track only the furthest round for each team
        for team_id, won in ((winner_id, True), (loser_id, False)):
            existing = results.get(team_id)
            if existing is None or round_num > existing[0]:
                results[team_id] = (round_num, won)

    return results


# ---------------------------------------------------------------------------
# Result mapping
# ---------------------------------------------------------------------------

def classify_team(
    team_id: int,
    info: dict,
    series_results: dict,
    play_in_active: bool,
) -> str:
    """
    Return the prev_playoff_result string for a single team.

    Priority:
    1. If team appears in the playoff bracket → derive from round + win/loss.
    2. If play_in_active and conference_seed 7-10 → "Play-In".
    3. Everything else → "Missed Playoffs".
    """
    if team_id in series_results:
        round_num, won = series_results[team_id]
        if won and round_num == 4:
            return "Champion"
        return ROUND_LOSS_LABEL.get(round_num, "1st Round")

    seed = info.get("conference_seed")
    if play_in_active and seed is not None and 7 <= seed <= 10:
        return "Play-In"

    return "Missed Playoffs"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    con = sqlite3.connect(DB_PATH)
    grand_total = 0

    for idx, (target_season, prior_season) in enumerate(SEASON_PAIRS, 1):
        print(f"\n{'='*62}", flush=True)
        print(
            f"  [{idx}/{len(SEASON_PAIRS)}]  Target: {target_season}  "
            f"<-  Prior: {prior_season}",
            flush=True,
        )
        print(f"{'='*62}", flush=True)

        play_in_active = prior_season >= PLAY_IN_SINCE
        print(f"  Play-In era: {play_in_active}", flush=True)

        try:
            # 1. Pull prior-season rows from DB (no API request)
            prior_teams = get_prior_season_teams(con, prior_season)
            prior_seeds  = {tid: v["conference_seed"] for tid, v in prior_teams.items()}
            print(f"  Teams in DB for {prior_season}: {len(prior_teams)}", flush=True)

            if not prior_teams:
                print("  [SKIP]  No prior-season data found in DB.", flush=True)
                continue

            # 2a. CommonPlayoffSeries → series structure (SERIES_ID per game)
            cps_df = fetch_playoff_series(prior_season)
            snooze("next: game winners")

            # 2b. LeagueGameFinder (Playoffs) → per-game W/L
            game_winners = fetch_game_winners(prior_season)
            snooze("parse series")

            # 3. Join on GAME_ID → {team_id: (round, won)}
            series_results = build_series_results(cps_df, game_winners)
            print(f"  Teams resolved from bracket: {len(series_results)}", flush=True)

            # 4. Classify every team in the prior season
            team_results: dict[int, str] = {}
            for team_id, info in prior_teams.items():
                label = classify_team(team_id, info, series_results, play_in_active)
                team_results[team_id] = label

            # Summary distribution
            dist = dict(Counter(team_results.values()))
            print(f"  Distribution: {dist}", flush=True)

            # 5. Write to DB
            n = update_db(con, target_season, team_results, prior_seeds)
            grand_total += n
            print(f"  [OK]  {n} rows updated in team_stats for {target_season}", flush=True)

        except Exception as exc:
            print(f"  [ERR]  {exc}", flush=True)
            traceback.print_exc()
            print("  Skipping season pair — continuing ...", flush=True)
            time.sleep(10)
            continue

        if idx < len(SEASON_PAIRS):
            print(f"  [cooldown: 8 s between seasons]", flush=True)
            time.sleep(8)

    con.close()
    print(f"\n{'='*62}", flush=True)
    print(f"  DONE — {grand_total} total rows updated across {len(SEASON_PAIRS)} seasons.", flush=True)
    print(f"  Database: {DB_PATH}", flush=True)


if __name__ == "__main__":
    main()
