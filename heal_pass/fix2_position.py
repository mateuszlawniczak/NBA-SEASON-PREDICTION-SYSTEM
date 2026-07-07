"""Fix 2: position for the three NEW seasons.

Method (archive hydrate_player_basic.py + sync_positions.py / fix_advanced_positions.py):
  1. Source position from PlayerIndex(historical_nullable=1), normalized via the
     exact archive POSITION_MAP.  To keep the existing data as ground truth, for
     any player who also appears in the EXISTING seasons we reuse their already
     stored position rather than re-deriving it (position is constant per player).
  2. Fill player_stats_basic.position (new seasons, NULL only).
  3. Propagate basic -> advanced, basic -> playoff_basic, basic -> playoff_advanced
     via correlated subquery on (player_id, season).
  4. Mop up any residual NULLs in the propagated tables directly from the index map
     (covers playoff-only players with no regular-season row that year).

Writes only the `position` column. Scope: NEW_SEASONS only.
"""
import sqlite3
import sys

from config import DB_PATH, NEW_SEASONS, EXISTING_SEASONS

POSITION_MAP = {
    "point guard": "PG", "shooting guard": "SG", "small forward": "SF",
    "power forward": "PF", "center": "C",
    "guard": "SG", "forward": "SF", "forward-center": "PF",
    "center-forward": "C", "guard-forward": "SG", "forward-guard": "SF",
    "pg": "PG", "sg": "SG", "sf": "SF", "pf": "PF", "c": "C",
    "g": "SG", "f": "SF", "g-f": "SG", "f-g": "SF", "f-c": "PF", "c-f": "C",
}


def normalize_position(raw):
    if not raw:
        return None
    key = str(raw).strip().lower()
    result = POSITION_MAP.get(key)
    if result:
        return result
    parts = key.split()
    return POSITION_MAP.get(parts[0]) if parts else None


def safe_int(val):
    try:
        f = float(val)
        import math
        return None if math.isnan(f) else int(f)
    except (TypeError, ValueError):
        return None


def build_index_map():
    from nba_api.stats.endpoints import PlayerIndex
    print("  [PlayerIndex] fetching historical player index ...", flush=True)
    df = PlayerIndex(historical_nullable=1, timeout=90).get_data_frames()[0]
    idx = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("PERSON_ID"))
        if pid is None:
            continue
        idx[pid] = normalize_position(str(row.get("POSITION") or "").strip())
    print(f"  [PlayerIndex] {len(idx)} players indexed", flush=True)
    return idx


def existing_position_map(cur):
    ph = ",".join("?" for _ in EXISTING_SEASONS)
    cur.execute(
        f"SELECT DISTINCT player_id, position FROM player_stats_basic "
        f"WHERE season IN ({ph}) AND position IS NOT NULL",
        EXISTING_SEASONS,
    )
    return {pid: pos for pid, pos in cur.fetchall()}


def per_season_null(cur, table, seasons):
    out = {}
    for s in seasons:
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE season=? AND (position IS NULL OR position='')", (s,))
        out[s] = cur.fetchone()[0]
    return out


def main():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    existing_map = existing_position_map(cur)
    index_map = build_index_map()

    # Cross-check: does today's PlayerIndex agree with the stored existing positions?
    agree = disagree = 0
    for pid, stored in existing_map.items():
        derived = index_map.get(pid)
        if derived is None:
            continue
        if derived == stored:
            agree += 1
        else:
            disagree += 1
    print(f"  [cross-check] PlayerIndex vs stored existing position: agree={agree} disagree={disagree}")
    print("               (disagreements are why overlapping players reuse stored ground-truth values)")

    # Combined source map: existing (ground truth) wins over PlayerIndex.
    def resolve(pid):
        return existing_map.get(pid) or index_map.get(pid)

    # ---- Phase 2: fill basic.position for new seasons (NULL only) ----
    ph = ",".join("?" for _ in NEW_SEASONS)
    cur.execute(
        f"SELECT DISTINCT player_id FROM player_stats_basic "
        f"WHERE season IN ({ph}) AND (position IS NULL OR position='')",
        NEW_SEASONS,
    )
    pids = [r[0] for r in cur.fetchall()]
    basic_filled = 0
    no_source = []
    for pid in pids:
        pos = resolve(pid)
        if pos is None:
            no_source.append(pid)
            continue
        cur.execute(
            f"UPDATE player_stats_basic SET position=? "
            f"WHERE player_id=? AND season IN ({ph}) AND (position IS NULL OR position='')",
            (pos, pid, *NEW_SEASONS),
        )
        basic_filled += cur.rowcount
    con.commit()
    print(f"\n  [basic] rows filled: {basic_filled}; players with no position source: {len(no_source)}")

    # ---- Phase 3: propagate basic -> advanced / playoff tables ----
    def propagate(table):
        cur.execute(
            f"""
            UPDATE {table}
               SET position = (
                   SELECT b.position FROM player_stats_basic b
                    WHERE b.player_id = {table}.player_id
                      AND b.season    = {table}.season
                      AND b.position IS NOT NULL
                    LIMIT 1
               )
             WHERE season IN ({ph})
               AND (position IS NULL OR position='')
               AND EXISTS (
                   SELECT 1 FROM player_stats_basic b
                    WHERE b.player_id = {table}.player_id
                      AND b.season    = {table}.season
                      AND b.position IS NOT NULL
               )
            """,
            NEW_SEASONS,
        )
        return cur.rowcount

    prop = {t: propagate(t) for t in
            ["player_stats_advanced", "player_stats_basic_playoffs", "player_stats_advanced_playoffs"]}
    con.commit()

    # ---- Phase 4: mop up residual NULLs directly from the index map ----
    mop = {}
    for table in ["player_stats_advanced", "player_stats_basic_playoffs", "player_stats_advanced_playoffs"]:
        cur.execute(
            f"SELECT DISTINCT player_id FROM {table} "
            f"WHERE season IN ({ph}) AND (position IS NULL OR position='')",
            NEW_SEASONS,
        )
        rem = [r[0] for r in cur.fetchall()]
        n = 0
        for pid in rem:
            pos = resolve(pid)
            if pos is None:
                continue
            cur.execute(
                f"UPDATE {table} SET position=? "
                f"WHERE player_id=? AND season IN ({ph}) AND (position IS NULL OR position='')",
                (pos, pid, *NEW_SEASONS),
            )
            n += cur.rowcount
        mop[table] = n
    con.commit()

    print("\n  [propagate rows]")
    for t, n in prop.items():
        print(f"    {t:<34} {n} (+{mop[t]} mopped from index)")

    print("\n  [remaining NULL position per new season]")
    for table in ["player_stats_basic", "player_stats_advanced",
                  "player_stats_basic_playoffs", "player_stats_advanced_playoffs"]:
        nn = per_season_null(cur, table, NEW_SEASONS)
        print(f"    {table:<34} " + "  ".join(f"{s}={nn[s]}" for s in NEW_SEASONS))

    # Distinct positions now present in new seasons (sanity vs existing {C,PF,SF,SG})
    cur.execute(
        f"SELECT DISTINCT position FROM player_stats_basic WHERE season IN ({ph}) ORDER BY position",
        NEW_SEASONS,
    )
    print("\n  [distinct new-season basic.position]:", [r[0] for r in cur.fetchall()])
    con.close()


if __name__ == "__main__":
    main()
