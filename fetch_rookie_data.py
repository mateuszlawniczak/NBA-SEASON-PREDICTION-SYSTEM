"""
fetch_rookie_data.py
--------------------
Hydrates the rookie_data table for every distinct player found in
player_stats_basic UNION player_stats_basic_playoffs (~1 096 players).

FAST APPROACH — DraftHistory batch endpoint
--------------------------------------------
Instead of 1 096 individual CommonPlayerInfo calls (~2 hours), this script
fetches DraftHistory once per draft year (1994–2025 = 32 calls, ~3 minutes).

  DraftHistory per year → PERSON_ID, OVERALL_PICK (already absolute),
                           ROUND_NUMBER, TEAM_ABBREVIATION, ORGANIZATION

Players not found in any draft year → undrafted:
  is_drafted = 0, draft_pick = 61, draft_round = 'undrafted',
  drafting_signing_team NULL until hydrate_rookie_signing_teams.py (API / local DB / BBRef).

Anti-bot: random 2.5–5.0 s sleep between every API call.
Fully resumable — years already cached in memory, no partial-year issues.
"""

import os
import sys
import time
import random
import sqlite3

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import DraftHistory

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH  = os.path.join(os.path.dirname(__file__), "nba_data.db")
TIMEOUT  = 60
UNDRAFTED_PICK = 61

# Range of draft years to cover all players currently in the DB.
# 1994 safely covers even the oldest veterans (e.g. Udonis Haslem 2002).
DRAFT_YEARS = list(range(1994, 2026))   # 32 calls total


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def snooze(label: str = "") -> None:
    t = random.uniform(2.5, 5.0)
    tag = f" [{label}]" if label else ""
    print(f"    [wait]  sleeping {t:.1f}s{tag} ...", flush=True)
    time.sleep(t)


def safe_int(val) -> "int | None":
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def clean_str(val) -> "str | None":
    s = str(val).strip() if val is not None else ""
    return s if s and s.lower() not in ("none", "nan", "") else None


# ---------------------------------------------------------------------------
# Schema — add is_drafted if missing
# ---------------------------------------------------------------------------

def ensure_schema(con: sqlite3.Connection) -> None:
    cur = con.cursor()

    def table_cols() -> set[str]:
        return {r[1] for r in cur.execute("PRAGMA table_info(rookie_data)")}

    cols = table_cols()

    if "is_drafted" not in cols:
        cur.execute("ALTER TABLE rookie_data ADD COLUMN is_drafted INTEGER DEFAULT 0")
        con.commit()
        print("  [schema] Added column is_drafted.", flush=True)
        cols = table_cols()
    else:
        print("  [schema] is_drafted already present.", flush=True)

    if "drafting_signing_team" not in cols:
        if "drafting_team" in cols:
            cur.execute(
                "ALTER TABLE rookie_data RENAME COLUMN drafting_team TO drafting_signing_team"
            )
            con.commit()
            print("  [schema] Renamed drafting_team → drafting_signing_team.", flush=True)
        else:
            cur.execute(
                "ALTER TABLE rookie_data ADD COLUMN drafting_signing_team TEXT"
            )
            con.commit()
            print("  [schema] Added column drafting_signing_team.", flush=True)
        cols = table_cols()

    for dead in ("college", "country"):
        if dead in cols:
            try:
                cur.execute(f"ALTER TABLE rookie_data DROP COLUMN {dead}")
                con.commit()
                print(f"  [schema] Dropped column {dead}.", flush=True)
            except sqlite3.OperationalError as exc:
                print(f"  [schema] DROP COLUMN {dead} failed ({exc}); rebuild DB if needed.",
                      flush=True)
            cols = table_cols()

    cur.execute(
        """
        UPDATE rookie_data
        SET draft_round = 'undrafted'
        WHERE COALESCE(is_drafted, 0) = 0
        """
    )
    if cur.rowcount:
        con.commit()
        print(
            f"  [schema] Normalized draft_round='undrafted' for undrafted rows "
            f"({cur.rowcount} touched).",
            flush=True,
        )


# ---------------------------------------------------------------------------
# Build draft map from DraftHistory: {player_id: {...}}
# ---------------------------------------------------------------------------

def build_draft_map() -> dict:
    """
    Fetches DraftHistory for each year in DRAFT_YEARS.
    Returns {player_id: {draft_pick, draft_round, draft_year, drafting_signing_team}}
    """
    draft_map: dict = {}
    total = len(DRAFT_YEARS)

    for i, year in enumerate(DRAFT_YEARS, 1):
        print(f"  [DraftHistory] {year}  ({i}/{total}) ...", flush=True)
        try:
            r  = DraftHistory(season_year_nullable=str(year), timeout=TIMEOUT)
            df = r.get_data_frames()[0]

            if df is None or df.empty:
                print(f"    -> No data for {year}", flush=True)
            else:
                for _, row in df.iterrows():
                    pid = safe_int(row.get("PERSON_ID"))
                    if pid is None:
                        continue
                    overall = safe_int(row.get("OVERALL_PICK"))
                    rnd     = safe_int(row.get("ROUND_NUMBER"))
                    team    = clean_str(row.get("TEAM_ABBREVIATION"))
                    draft_map[pid] = {
                        "is_drafted":           1,
                        "draft_pick":           overall,
                        "draft_round":          rnd,
                        "draft_year":           year,
                        "drafting_signing_team": team,
                    }
                print(f"    -> {len(df)} picks  (map size: {len(draft_map)})", flush=True)

        except Exception as exc:
            print(f"    [warn] {year}: {exc}", flush=True)

        if i < total:
            snooze(f"next: {DRAFT_YEARS[i] if i < len(DRAFT_YEARS) else 'done'}")

    return draft_map


# ---------------------------------------------------------------------------
# Player list from DB
# ---------------------------------------------------------------------------

def get_player_list(con: sqlite3.Connection) -> list[tuple[int, str]]:
    cur = con.cursor()
    cur.execute("""
        SELECT player_id, player_name FROM (
            SELECT player_id, MAX(player_name) AS player_name
            FROM (
                SELECT player_id, player_name FROM player_stats_basic
                UNION ALL
                SELECT player_id, player_name FROM player_stats_basic_playoffs
            )
            GROUP BY player_id
        )
        ORDER BY player_id
    """)
    return cur.fetchall()


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

UPSERT_SQL = """
INSERT INTO rookie_data (
    player_id, player_name,
    is_drafted, draft_pick, draft_round, draft_year,
    drafting_signing_team
) VALUES (
    :player_id, :player_name,
    :is_drafted, :draft_pick, :draft_round, :draft_year,
    :drafting_signing_team
)
ON CONFLICT (player_id) DO UPDATE SET
    player_name            = excluded.player_name,
    is_drafted             = excluded.is_drafted,
    draft_pick             = excluded.draft_pick,
    draft_round            = excluded.draft_round,
    draft_year             = CASE
        WHEN excluded.is_drafted = 1 THEN excluded.draft_year
        ELSE COALESCE(rookie_data.draft_year, excluded.draft_year)
    END,
    drafting_signing_team  = CASE
        WHEN excluded.is_drafted = 1 THEN excluded.drafting_signing_team
        ELSE COALESCE(rookie_data.drafting_signing_team, excluded.drafting_signing_team)
    END
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    con = sqlite3.connect(DB_PATH)
    ensure_schema(con)

    # --- Step 1: build full draft map from batch API ---
    print(f"\n[fetch_rookie_data] Building draft map ({len(DRAFT_YEARS)} years)...\n",
          flush=True)
    draft_map = build_draft_map()
    print(f"\n  Draft map built: {len(draft_map)} drafted players found.\n", flush=True)

    # --- Step 2: match against our player list and insert ---
    players = get_player_list(con)
    total   = len(players)
    cur     = con.cursor()

    drafted = undrafted = 0

    for i, (pid, name) in enumerate(players, 1):
        info = draft_map.get(pid)

        if info:
            row = {"player_id": pid, "player_name": name, **info}
            drafted += 1
            label = f"Drafted: 1, Pick: {info['draft_pick']}"
        else:
            row = {
                "player_id":             pid,
                "player_name":           name,
                "is_drafted":            0,
                "draft_pick":            UNDRAFTED_PICK,
                "draft_round":           "undrafted",
                "draft_year":            None,
                "drafting_signing_team": None,
            }
            undrafted += 1
            label = "Drafted: 0, Pick: N/A"

        cur.execute(UPSERT_SQL, row)
        print(f"  [DRAFT DATA] {i}/{total}  {name:<30} — {label}", flush=True)

        if i % 50 == 0:
            con.commit()
            print(f"    [commit] {i} rows committed.", flush=True)

    con.commit()
    con.close()

    print(f"\n{'='*60}", flush=True)
    print(f"  DONE — {total} players: {drafted} drafted, {undrafted} undrafted.",
          flush=True)

    # Sanity check
    con2 = sqlite3.connect(DB_PATH)
    cur2 = con2.cursor()
    cur2.execute("""
        SELECT COUNT(*), SUM(is_drafted),
               COUNT(*) - SUM(is_drafted),
               MIN(CASE WHEN is_drafted=1 THEN draft_pick END),
               MAX(CASE WHEN is_drafted=1 THEN draft_pick END)
        FROM rookie_data
    """)
    r = cur2.fetchone()
    con2.close()
    print(f"  DB check — Total={r[0]}  Drafted={r[1]}  Undrafted={r[2]}  "
          f"Pick range={r[3]}–{r[4]}", flush=True)


if __name__ == "__main__":
    main()
