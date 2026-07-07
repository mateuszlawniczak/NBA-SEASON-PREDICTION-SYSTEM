"""Strict re-verification of every bref-derived gs value (the 41 players that
needed the basketball-reference fallback).

For each player's new-season DB row, re-fetch bref (namesake-aware) and accept a
gs ONLY when a bref season row's normalized team abbreviation matches the DB
team_abbr. Otherwise set gs = NULL. This eliminates wrong-namesake values (e.g.
Marvin Williams' 78 leaking onto Matt Williams Jr.) without ever fabricating.

PCS-filled rows (the other 1542) are authoritative by player_id and untouched.
"""
import sqlite3
import time
import random

from config import DB_PATH, NEW_SEASONS
from fix4_gs import fetch_bref_page, _norm_abbr, COMBINED_TEAMS, safe_int

BREF_PLAYERS = [
    "Caris LeVert", "DJ Stephens", "DeVaughn Akoon-Purcell", "Deandre Ayton",
    "Duncan Robinson", "Dusty Hannahs", "Emanuel Terry", "Eric Mika", "Gian Clavell",
    "Isaiah Briscoe", "Jacob Pullen", "Jacob Wiley", "Jameel Warney", "Jamil Wilson",
    "Jarred Vanderbilt", "Jarrell Brantley", "Javonte Green", "Jaxson Hayes",
    "Jordan Bell", "Jordan Sibert", "Justin James", "Justin Robinson", "Kevin Huerter",
    "LeBron James", "London Perrantes", "Luka Dončić", "Luke Kennard", "Malik Newman",
    "Matt Williams Jr.", "Maxi Kleber", "Omari Johnson", "Shamorie Ponds", "Stanton Kidd",
    "Tahjere McCall", "Tobias Harris", "Trey McKinney-Jones", "Tyler Davis",
    "Vincent Hunter", "Xavier Rathan-Mayes", "Zach Lofton", "Zach Norvell Jr.",
]


def team_confirmed_gs(season_map, season, team_abbr):
    if not season_map or season not in season_map:
        return None
    want = _norm_abbr(team_abbr)
    for bteam, gs in season_map[season]:
        if bteam in COMBINED_TEAMS:
            continue
        if _norm_abbr(bteam) == want:
            return gs
    return None


def main():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    ph = ",".join("?" for _ in NEW_SEASONS)

    kept = reverted = confirmed_changed = 0
    for name in BREF_PLAYERS:
        cur.execute(
            f"SELECT player_id, season, team_id, team_abbr, gs FROM player_stats_basic "
            f"WHERE player_name=? AND season IN ({ph})",
            (name, *NEW_SEASONS),
        )
        rows = cur.fetchall()
        if not rows:
            continue
        time.sleep(random.uniform(4.1, 7.8))
        smap = fetch_bref_page(name)
        for pid, season, tid, tabbr, cur_gs in rows:
            confirmed = team_confirmed_gs(smap, season, tabbr)
            if confirmed is None:
                # cannot team-confirm -> do not keep a possibly-namesake value
                if cur_gs is not None:
                    cur.execute(
                        "UPDATE player_stats_basic SET gs=NULL WHERE player_id=? AND season=? AND team_id=?",
                        (pid, season, tid),
                    )
                    reverted += 1
                    print(f"  [REVERT->NULL] {name} {season} {tabbr}: had gs={cur_gs} (no team-confirmed bref row)")
            else:
                if cur_gs != confirmed:
                    cur.execute(
                        "UPDATE player_stats_basic SET gs=? WHERE player_id=? AND season=? AND team_id=?",
                        (confirmed, pid, season, tid),
                    )
                    confirmed_changed += 1
                    print(f"  [CORRECT] {name} {season} {tabbr}: gs {cur_gs} -> {confirmed}")
                else:
                    kept += 1
        con.commit()

    print(f"\n  kept(confirmed match)={kept}  corrected={confirmed_changed}  reverted_to_null={reverted}")
    for s in NEW_SEASONS:
        cur.execute("SELECT COUNT(*) FROM player_stats_basic WHERE season=? AND gs IS NULL", (s,))
        print(f"  remaining NULL gs {s}: {cur.fetchone()[0]}")
    con.close()


if __name__ == "__main__":
    main()
