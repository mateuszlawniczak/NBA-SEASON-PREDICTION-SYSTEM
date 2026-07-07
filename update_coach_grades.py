"""
update_coach_grades.py
----------------------
Migrates coach_system_data to two columns (name | Grade), then assigns tier
grades via the NBA Stats API.

CommonCoachStats / CoachHistory are not published in ``nba_api`` or the usual
stats.nba.com catalog. This script uses official endpoints that together give
the same facts: LeagueGameFinder (playoff rounds, championships by team-season)
and CommonTeamRoster (head coach ↔ team ↔ season).

Only ``coach_system_data`` in ``nba_data.db`` is read or written.
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
import sys
import time
from collections import defaultdict
from typing import Any

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import CommonTeamRoster, LeagueGameFinder
from nba_api.stats.static import teams as nba_teams

from leakage_guards import target_start_year
from season_utils import SeasonPair, parse_cli_seasons

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")
CACHE_PATH = os.path.join(os.path.dirname(__file__), ".coach_grades_api_cache.json")
CACHE_VERSION = 2

TIMEOUT = 60
ROSTER_SNOOZE = (0.45, 0.95)
PLAYOFF_SNOOZE = (0.25, 0.55)

CURRENT_START_YEAR = 2025  # legacy default; overridden by --season when provided
CAREER_FIRST_START_YEAR = 2000


def _season_label(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def _seasons_inclusive(first_sy: int, last_sy: int) -> list[str]:
    return [_season_label(y) for y in range(first_sy, last_sy + 1)]


def migrate_coach_system_schema(con: sqlite3.Connection) -> None:
    cur = con.cursor()
    cur.execute("PRAGMA table_info(coach_system_data)")
    col_names = {row[1] for row in cur.fetchall()}
    if col_names == {"name", "Grade"}:
        return
    if not col_names:
        cur.execute(
            "CREATE TABLE coach_system_data (name TEXT PRIMARY KEY, Grade TEXT)"
        )
        con.commit()
        return

    cur.execute(
        "CREATE TABLE _coach_system_migrate (name TEXT PRIMARY KEY, Grade TEXT)"
    )
    if "coach_name" in col_names:
        cur.execute(
            """
            INSERT INTO _coach_system_migrate (name, Grade)
            SELECT coach_name, NULL FROM coach_system_data
            """
        )
    else:
        cur.execute(
            """
            INSERT INTO _coach_system_migrate (name, Grade)
            SELECT name, NULL FROM coach_system_data
            """
        )
    cur.execute("DROP TABLE coach_system_data")
    cur.execute("ALTER TABLE _coach_system_migrate RENAME TO coach_system_data")
    con.commit()


def _playoff_round(game_id: str) -> int:
    # e.g. 0042300101 (R1), 0042300201 (R2), 0042300405 (Finals) — round is digits 7–8 (1-based).
    if not game_id or len(game_id) < 8:
        return 0
    try:
        return int(game_id[6:8])
    except ValueError:
        return 0


def _snooze(lo_hi: tuple[float, float]) -> None:
    time.sleep(random.uniform(lo_hi[0], lo_hi[1]))


def _team_ids() -> list[int]:
    return sorted(t["id"] for t in nba_teams.get_teams())


def _fetch_playoff_team_facts(season: str) -> dict[int, dict[str, Any]]:
    try:
        g = LeagueGameFinder(
            player_or_team_abbreviation="T",
            season_nullable=season,
            season_type_nullable="Playoffs",
            timeout=TIMEOUT,
        )
        df = g.league_game_finder_results.get_data_frame()
    except Exception as exc:
        print(f"  [warn] playoffs {season}: {exc}", flush=True)
        return {}

    if df is None or df.empty:
        return {}

    by_team: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for _, row in df.iterrows():
        tid = int(row["TEAM_ID"])
        gid = str(row["GAME_ID"])
        wl = str(row["WL"])
        by_team[tid].append((gid, wl))

    out: dict[int, dict[str, Any]] = {}
    for tid, rows in by_team.items():
        rounds = {_playoff_round(g) for g, _ in rows if _playoff_round(g) > 0}
        max_r = max(rounds) if rounds else 0
        finals_rows = [(g, wl) for g, wl in rows if _playoff_round(g) == 4]
        wins = sum(1 for _, wl in finals_rows if wl == "W")
        champion = wins >= 4

        out[tid] = {
            "playoffs": True,
            "max_round": max_r,
            "second_round_plus": max_r >= 2,
            "conference_finals_plus": max_r >= 3,
            "champion": champion,
        }
    return out


def _fetch_head_coaches(team_id: int, season: str) -> str | None:
    try:
        r = CommonTeamRoster(team_id=team_id, season=season, timeout=TIMEOUT)
        cdf = r.coaches.get_data_frame()
    except Exception as exc:
        print(f"  [warn] roster {season} tid={team_id}: {exc}", flush=True)
        return None

    if cdf is None or cdf.empty:
        return None

    head = cdf[cdf["COACH_TYPE"] == "Head Coach"]
    if head.empty:
        head = cdf[cdf["IS_ASSISTANT"] == 1]
    if head.empty:
        return None

    names = head["COACH_NAME"].dropna().astype(str).str.strip().tolist()
    if not names:
        return None
    return " / ".join(names)


def _normalize_coach_key(name: str) -> str:
    return " ".join(name.strip().lower().split())


def _split_coach_names(cell: str) -> list[str]:
    return [c.strip() for c in cell.split("/") if c.strip()]


def _coach_cell_matches(cell: str, key: str) -> bool:
    parts = {_normalize_coach_key(p) for p in _split_coach_names(cell)}
    return key in parts


def _load_cache(current_start_year: int) -> dict[str, Any] | None:
    if not os.path.isfile(CACHE_PATH):
        return None
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("version") != CACHE_VERSION:
        return None
    if data.get("current_start_year") != current_start_year:
        return None
    if data.get("career_first") != CAREER_FIRST_START_YEAR:
        return None
    return data


def _save_cache(
    roster: dict[str, dict[int, str]],
    playoff: dict[str, dict[int, dict]],
    *,
    current_start_year: int,
) -> None:
    payload = {
        "version": CACHE_VERSION,
        "current_start_year": current_start_year,
        "career_first": CAREER_FIRST_START_YEAR,
        "roster_by_season": {
            s: {str(k): v for k, v in m.items()} for s, m in roster.items()
        },
        "playoff_by_season": {
            s: {str(k): v for k, v in m.items()} for s, m in playoff.items()
        },
    }
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except OSError as exc:
        print(f"  [warn] cache write: {exc}", flush=True)


def _cache_to_runtime(data: dict[str, Any]) -> tuple[dict[str, dict[int, str]], dict]:
    r_raw = data["roster_by_season"]
    p_raw = data["playoff_by_season"]
    roster = {s: {int(tid): n for tid, n in m.items()} for s, m in r_raw.items()}
    playoff = {s: {int(tid): d for tid, d in m.items()} for s, m in p_raw.items()}
    return roster, playoff


def build_or_load_indexes(
    career_seasons: list[str],
    *,
    current_start_year: int,
) -> tuple[dict[str, dict[int, str]], dict[str, dict[int, dict[str, Any]]]]:
    cached = _load_cache(current_start_year)
    if cached is not None:
        print("[cache] Using on-disk coach API index.", flush=True)
        return _cache_to_runtime(cached)

    print("[api] Building playoff + roster index (first run may take several minutes).", flush=True)
    roster_by_season: dict[str, dict[int, str]] = {}
    playoff_by_season: dict[str, dict[int, dict[str, Any]]] = {}
    team_ids = _team_ids()

    for i, season in enumerate(career_seasons, 1):
        print(f"  [playoffs] {season} ({i}/{len(career_seasons)})", flush=True)
        playoff_by_season[season] = _fetch_playoff_team_facts(season)
        _snooze(PLAYOFF_SNOOZE)

    total = len(career_seasons) * len(team_ids)
    n = 0
    for season in career_seasons:
        roster_by_season[season] = {}
        for tid in team_ids:
            n += 1
            if n == 1 or n % 40 == 0:
                print(f"  [roster] progress {n}/{total}", flush=True)
            nm = _fetch_head_coaches(tid, season)
            if nm:
                roster_by_season[season][tid] = nm
            _snooze(ROSTER_SNOOZE)

    _save_cache(roster_by_season, playoff_by_season, current_start_year=current_start_year)
    return roster_by_season, playoff_by_season


def collect_signals(
    display_name: str,
    last_5: set[str],
    last_10: set[str],
    last_15: set[str],
    career_seasons: list[str],
    roster_by_season: dict[str, dict[int, str]],
    playoff_by_season: dict[str, dict[int, dict[str, Any]]],
) -> dict[str, Any]:
    key = _normalize_coach_key(display_name)

    rings_total = 0
    ring_seasons: set[str] = set()
    playoff_visits_5 = 0
    cf_visits_5 = 0
    second_round_15 = 0
    playoff_visits_10 = 0
    ring_in_last_5 = False

    for season in career_seasons:
        roster = roster_by_season.get(season, {})
        team_tid: int | None = None
        for tid, cell in roster.items():
            if _coach_cell_matches(cell, key):
                team_tid = tid
                break
        if team_tid is None:
            continue

        pf = playoff_by_season.get(season, {}).get(team_tid)
        if not pf or not pf.get("playoffs"):
            continue

        if pf.get("champion"):
            rings_total += 1
            ring_seasons.add(season)

        if season in last_5:
            playoff_visits_5 += 1
            if pf.get("conference_finals_plus"):
                cf_visits_5 += 1
            if pf.get("champion"):
                ring_in_last_5 = True

        if season in last_10:
            playoff_visits_10 += 1

        if season in last_15 and pf.get("second_round_plus"):
            second_round_15 += 1

    return {
        "rings_total": rings_total,
        "ring_seasons": ring_seasons,
        "playoff_visits_5": playoff_visits_5,
        "cf_visits_5": cf_visits_5,
        "second_round_15": second_round_15,
        "playoff_visits_10": playoff_visits_10,
        "ring_in_last_5": ring_in_last_5,
    }


def tier_from_signals(sig: dict[str, Any], last_5: set[str]) -> str:
    if sig["ring_in_last_5"] or sig["rings_total"] >= 2:
        return "S"
    if sig["rings_total"] == 1:
        if not (sig["ring_seasons"] & last_5):
            return "A"
    if (
        sig["playoff_visits_5"] >= 4
        or sig["cf_visits_5"] >= 2
        or sig["second_round_15"] >= 10
    ):
        return "B"
    if sig["playoff_visits_5"] >= 2 or sig["playoff_visits_10"] >= 5:
        return "C"
    if sig["playoff_visits_5"] >= 1:
        return "D"
    return "F"


def print_grade_table(rows: list[tuple[str, str]]) -> None:
    w_name = max(len(r[0]) for r in rows) if rows else 4
    sep = "-" * (w_name + 3 + 5)
    hdr = f"{'Name'.ljust(w_name)} | Grade"
    print(sep)
    print(hdr)
    print(sep)
    for name, grade in sorted(rows, key=lambda x: x[0].lower()):
        print(f"{name.ljust(w_name)} | {grade}")
    print(sep)


def main(source_season: str | None = None, target_season: str | None = None) -> None:
    if source_season is None or target_season is None:
        pair: SeasonPair = parse_cli_seasons()
        source_season = pair.source
        target_season = pair.target
    _ = source_season

    current_start_year = target_start_year(target_season)
    last_5_sy = current_start_year - 4
    career_first_sy = CAREER_FIRST_START_YEAR
    last_5 = set(_seasons_inclusive(last_5_sy, current_start_year))
    last_10 = set(_seasons_inclusive(current_start_year - 9, current_start_year))
    last_15 = set(_seasons_inclusive(current_start_year - 14, current_start_year))
    career_seasons = _seasons_inclusive(career_first_sy, current_start_year)

    con = sqlite3.connect(DB_PATH)
    migrate_coach_system_schema(con)
    cur = con.cursor()
    names = [r[0] for r in cur.execute("SELECT name FROM coach_system_data").fetchall()]
    if not names:
        print("[done] coach_system_data is empty — nothing to grade.", flush=True)
        con.close()
        return

    roster_by_season, playoff_by_season = build_or_load_indexes(
        career_seasons, current_start_year=current_start_year
    )

    updates: list[tuple[str, str]] = []
    table_rows: list[tuple[str, str]] = []
    for display in names:
        sig = collect_signals(
            display,
            last_5,
            last_10,
            last_15,
            career_seasons,
            roster_by_season,
            playoff_by_season,
        )
        grade = tier_from_signals(sig, last_5)
        updates.append((grade, display))
        table_rows.append((display, grade))

    cur.executemany(
        "UPDATE coach_system_data SET Grade = ? WHERE name = ?",
        updates,
    )
    con.commit()
    con.close()

    print_grade_table(table_rows)
    print(f"\n[done] Updated {len(updates)} coaches in coach_system_data.", flush=True)


if __name__ == "__main__":
    main()
