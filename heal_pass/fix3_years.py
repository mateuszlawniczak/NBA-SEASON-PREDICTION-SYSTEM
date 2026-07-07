"""Fix 3: years_in_league in player_stats_basic for the three NEW seasons (NULL only).

Method (archive hydrate_player_basic.py experience fill):
    years_in_league = max(season_start_year - from_year + 1, 1)
where from_year is the player's first NBA season.

Ground-truth sourcing:
  * If the player appears in the EXISTING seasons with a known years_in_league,
    back out their from_year = existing_season_start - (yil - 1) and use it
    (existing data is the source of truth).
  * Otherwise use PlayerIndex(historical_nullable=1) FROM_YEAR.

--verify-only prints the reproduction check on the existing seasons.
Writes only the `years_in_league` column. Scope: NEW_SEASONS only.
"""
import sqlite3
import sys

from config import DB_PATH, NEW_SEASONS, EXISTING_SEASONS


def season_start_year(season):
    return int(season.split("-")[0])


def safe_int(val):
    try:
        f = float(val)
        import math
        return None if math.isnan(f) else int(f)
    except (TypeError, ValueError):
        return None


def build_index_from_year():
    from nba_api.stats.endpoints import PlayerIndex
    print("  [PlayerIndex] fetching FROM_YEAR ...", flush=True)
    df = PlayerIndex(historical_nullable=1, timeout=90).get_data_frames()[0]
    idx = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("PERSON_ID"))
        if pid is None:
            continue
        idx[pid] = safe_int(row.get("FROM_YEAR"))
    print(f"  [PlayerIndex] {len(idx)} players indexed", flush=True)
    return idx


def existing_from_year_map(cur):
    """Back out from_year from existing-season (season, years_in_league) pairs.
    Returns {player_id: from_year} and a set of players whose implied from_year is
    inconsistent across their existing seasons (for reporting)."""
    ph = ",".join("?" for _ in EXISTING_SEASONS)
    cur.execute(
        f"SELECT player_id, season, years_in_league FROM player_stats_basic "
        f"WHERE season IN ({ph}) AND years_in_league IS NOT NULL",
        EXISTING_SEASONS,
    )
    implied = {}
    inconsistent = set()
    for pid, season, yil in cur.fetchall():
        fy = season_start_year(season) - (int(yil) - 1)
        if pid in implied and implied[pid] != fy:
            inconsistent.add(pid)
            implied[pid] = min(implied[pid], fy)  # earliest rookie year
        else:
            implied.setdefault(pid, fy)
    return implied, inconsistent


def main():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    existing_fy, inconsistent = existing_from_year_map(cur)
    print(f"  [existing] implied from_year for {len(existing_fy)} players; "
          f"inconsistent across seasons: {len(inconsistent)}")

    index_fy = build_index_from_year()

    # Verify formula reproduces existing years_in_league using implied from_year.
    ph_e = ",".join("?" for _ in EXISTING_SEASONS)
    cur.execute(
        f"SELECT player_id, season, years_in_league FROM player_stats_basic "
        f"WHERE season IN ({ph_e}) AND years_in_league IS NOT NULL",
        EXISTING_SEASONS,
    )
    ok = bad = 0
    for pid, season, yil in cur.fetchall():
        fy = existing_fy.get(pid)
        if fy is None:
            continue
        calc = max(season_start_year(season) - fy + 1, 1)
        if calc == int(yil):
            ok += 1
        else:
            bad += 1
    print(f"  [verify] existing yil reproduced by formula: ok={ok} bad={bad}")

    # Cross-check implied-from-existing vs PlayerIndex from_year (informational).
    agree = disagree = 0
    for pid, fy in existing_fy.items():
        ify = index_fy.get(pid)
        if ify is None:
            continue
        if ify == fy:
            agree += 1
        else:
            disagree += 1
    print(f"  [cross-check] existing-implied vs PlayerIndex from_year: agree={agree} disagree={disagree}")

    if "--verify-only" in sys.argv:
        con.close()
        return

    def resolve_from_year(pid):
        return existing_fy.get(pid) if existing_fy.get(pid) is not None else index_fy.get(pid)

    ph = ",".join("?" for _ in NEW_SEASONS)
    cur.execute(
        f"SELECT rowid, player_id, season FROM player_stats_basic "
        f"WHERE season IN ({ph}) AND years_in_league IS NULL",
        NEW_SEASONS,
    )
    targets = cur.fetchall()
    per_season = {s: 0 for s in NEW_SEASONS}
    no_source = 0
    for rowid, pid, season in targets:
        fy = resolve_from_year(pid)
        if fy is None:
            no_source += 1
            continue
        yil = max(season_start_year(season) - fy + 1, 1)
        cur.execute(
            "UPDATE player_stats_basic SET years_in_league=? WHERE rowid=? AND years_in_league IS NULL",
            (yil, rowid),
        )
        per_season[season] += cur.rowcount
    con.commit()

    print("\n  [apply] years_in_league filled (new seasons, NULL only):")
    for s in NEW_SEASONS:
        cur.execute("SELECT COUNT(*) FROM player_stats_basic WHERE season=? AND years_in_league IS NULL", (s,))
        rem = cur.fetchone()[0]
        print(f"    {s}: filled={per_season[s]}  still_null={rem}")
    print(f"  players with no from_year source: {no_source}")
    con.close()


if __name__ == "__main__":
    main()
