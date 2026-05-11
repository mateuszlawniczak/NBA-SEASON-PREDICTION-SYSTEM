"""
fetch_playoff_usg.py
--------------------
Fills usg_pct in player_stats_basic_playoffs for all seasons.

Per season, 2 API calls (Advanced Totals):
  1. LeagueDashPlayerStats Advanced — SeasonType="Playoffs"
  2. LeagueDashPlayerStats Advanced — SeasonType="PlayIn"  (fallback: "Play In")

Merge rule for players in BOTH season types:
  usg_pct = (usg_po * min_po + usg_pi * min_pi) / (min_po + min_pi)
  (minutes-weighted average — the only statistically correct way to combine USG%)

Anti-bot: random 5.2–8.8 s between every request.
"""

import sqlite3, time, random, math, os, sys

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import LeagueDashPlayerStats

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

SEASONS = ["2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
TIMEOUT = 90

SEASON_TYPE_PLAYOFFS = "Playoffs"
SEASON_TYPE_PLAYIN   = "Play In"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def snooze(label: str = "") -> None:
    t = random.uniform(5.2, 8.8)
    tag = f" [{label}]" if label else ""
    print(f"  [wait] {t:.1f}s{tag}", flush=True)
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


# ---------------------------------------------------------------------------
# Fetch Advanced Totals for one season + season_type
# Returns {player_id: {"usg_pct": float, "tot_min": float}}
# ---------------------------------------------------------------------------

def fetch_advanced(season: str, season_type: str) -> dict:
    label = season_type.replace(" ", "")
    print(f"  -> Advanced Totals [{season_type}] ...", flush=True)

    # Try primary spelling, then fallback
    alternates = [season_type]
    if season_type == SEASON_TYPE_PLAYIN:
        alternates.append("PlayIn")
    elif season_type == "PlayIn":
        alternates.append(SEASON_TYPE_PLAYIN)

    df = None
    for st in alternates:
        try:
            r = LeagueDashPlayerStats(
                season=season,
                per_mode_detailed="Totals",
                measure_type_detailed_defense="Advanced",
                season_type_all_star=st,
                timeout=TIMEOUT,
            )
            df = r.get_data_frames()[0]
            if not df.empty:
                break
        except Exception as exc:
            print(f"     [warn] [{st}] -> {exc}", flush=True)

    if df is None or df.empty:
        print(f"     [skip] No Advanced data for [{season_type}].", flush=True)
        return {}

    out: dict = {}
    for _, row in df.iterrows():
        pid = safe_int(row.get("PLAYER_ID"))
        if pid is None:
            continue
        usg = safe_float(row.get("USG_PCT"))
        # MIN in Totals mode = total minutes played this season type
        tot_min = safe_float(row.get("MIN")) or 0.0

        if pid in out:
            # Traded player — combine: weighted-avg usg, sum minutes
            prev     = out[pid]
            prev_min = prev["tot_min"]
            new_min  = prev_min + tot_min
            if new_min > 0 and usg is not None and prev["usg_pct"] is not None:
                prev["usg_pct"] = (prev["usg_pct"] * prev_min + usg * tot_min) / new_min
            elif usg is not None:
                prev["usg_pct"] = usg
            prev["tot_min"] = new_min
        else:
            out[pid] = {"usg_pct": usg, "tot_min": tot_min}

    print(f"     -> {len(out)} players [{label}]", flush=True)
    return out


# ---------------------------------------------------------------------------
# Merge Playoffs + PlayIn usg_pct (minutes-weighted average)
# ---------------------------------------------------------------------------

def merge_usg(playoffs: dict, playin: dict) -> dict:
    """
    Returns {player_id: usg_pct (float | None)} ready to write to DB.
    """
    all_pids = set(playoffs) | set(playin)
    result: dict = {}

    for pid in all_pids:
        in_po = pid in playoffs
        in_pi = pid in playin

        if in_po and in_pi:
            usg_po  = playoffs[pid]["usg_pct"]
            min_po  = playoffs[pid]["tot_min"] or 0.0
            usg_pi  = playin[pid]["usg_pct"]
            min_pi  = playin[pid]["tot_min"]  or 0.0
            total   = min_po + min_pi
            if total > 0 and usg_po is not None and usg_pi is not None:
                result[pid] = (usg_po * min_po + usg_pi * min_pi) / total
            elif usg_po is not None:
                result[pid] = usg_po
            else:
                result[pid] = usg_pi
        elif in_po:
            result[pid] = playoffs[pid]["usg_pct"]
        else:
            result[pid] = playin[pid]["usg_pct"]

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> None:
    print(f"[fetch_playoff_usg] DB: {DB_PATH}", flush=True)
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    grand_updated = 0

    for season in SEASONS:
        print(f"\n{'=' * 50}", flush=True)
        print(f"[{season}]", flush=True)
        print(f"{'=' * 50}", flush=True)

        try:
            po_adv = fetch_advanced(season, SEASON_TYPE_PLAYOFFS)
            snooze(f"{season} Playoffs -> PlayIn")

            pi_adv = fetch_advanced(season, SEASON_TYPE_PLAYIN)

            merged = merge_usg(po_adv, pi_adv)

            updated = 0
            for pid, usg in merged.items():
                if usg is None:
                    continue
                cur.execute("""
                    UPDATE player_stats_basic_playoffs
                    SET usg_pct = ?
                    WHERE player_id = ? AND season = ?
                """, (round(usg, 4), pid, season))
                updated += cur.rowcount

            con.commit()
            grand_updated += updated
            print(f"  [OK] {updated} rows updated for {season}.", flush=True)

        except Exception as exc:
            print(f"  [ERROR] {season}: {exc}", flush=True)
            con.rollback()

        if season != SEASONS[-1]:
            snooze(f"between seasons: {season} -> next")

    null_remaining = cur.execute(
        "SELECT COUNT(*) FROM player_stats_basic_playoffs WHERE usg_pct IS NULL"
    ).fetchone()[0]
    con.close()
    print(f"\n[DONE] Total rows updated: {grand_updated}")
    print(f"[DONE] NULL usg_pct remaining: {null_remaining}")


if __name__ == "__main__":
    run()
