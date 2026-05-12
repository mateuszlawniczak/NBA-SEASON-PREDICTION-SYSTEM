"""
calculate_team_pr_base.py
-------------------------
Aggregates player_simulation_pr into team_simulation_pr.base_team_pr (no coach multipliers).

Uses a 9-man rotation ranked by **effective** PR: each player's contribution is
`base_pr * min(1.0, gp / 60)` so low-availability players are discounted before selecting the top 9.

- One row per (player_name, season) from player_simulation_pr (base_pr + season gp).
- Team assignment from player_stats_basic; traded / combined rows (TOT, 2TM, …, team_id=0)
  resolve to the stint with the most games played (ties: more total minutes, then team_abbr).
"""

from __future__ import annotations

import os
import sqlite3
import sys
from collections import defaultdict
from typing import Any

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

ROTATION_SIZE = 9
GP_REFERENCE = 60.0  # full reliability at or above this many games played

AGG_TEAM_ABBRS = frozenset({"TOT", "2TM", "3TM", "4TM"})

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def pragma_columns(con: sqlite3.Connection, table: str) -> set[str]:
    cur = con.execute(f"PRAGMA table_info({table})")
    return {r[1] for r in cur.fetchall()}


def is_aggregate_row(team_abbr: Any, team_id: Any) -> bool:
    if team_id is not None:
        try:
            if int(team_id) == 0:
                return True
        except (TypeError, ValueError):
            pass
    t = (str(team_abbr).strip().upper() if team_abbr is not None else "") or ""
    return t in AGG_TEAM_ABBRS


def reliability_weight(gp: int) -> float:
    if gp <= 0:
        return 0.0
    return min(1.0, float(gp) / GP_REFERENCE)


def effective_player_pr(base_pr: float, gp: int) -> float:
    return base_pr * reliability_weight(gp)


def _gint(v: Any) -> int:
    if v is None:
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def pick_team_for_player_season(stints: list[dict[str, Any]]) -> str | None:
    """Choose one team_abbr for aggregation; ignores aggregate (TOT) rows when stints exist."""
    if not stints:
        return None
    non_agg = [s for s in stints if not is_aggregate_row(s.get("team_abbr"), s.get("team_id"))]
    pool = non_agg if non_agg else []
    if not pool:
        return None
    best = max(
        pool,
        key=lambda s: (
            _gint(s.get("gp")),
            _gint(s.get("total_minutes")),
            str(s.get("team_abbr") or ""),
        ),
    )
    ta = best.get("team_abbr")
    return str(ta).strip() if ta else None


def ensure_team_simulation_pr(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS team_simulation_pr (
            team          TEXT NOT NULL,
            season        TEXT NOT NULL,
            base_team_pr  REAL NOT NULL,
            PRIMARY KEY (team, season)
        );
        """
    )


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        for tbl in ("player_simulation_pr", "player_stats_basic"):
            cur = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (tbl,),
            )
            if cur.fetchone() is None:
                print(f"Missing table {tbl}. Run prior pipelines first.", flush=True)
                return

        basic_cols = pragma_columns(con, "player_stats_basic")
        tmins = "total_minutes" if "total_minutes" in basic_cols else None

        ensure_team_simulation_pr(con)

        pr_map: dict[tuple[str, str], tuple[float, int]] = {}
        cur_pr = con.execute(
            "SELECT player_name, season, base_pr, gp FROM player_simulation_pr"
        )
        for pn, sn, bp, gp_raw in cur_pr.fetchall():
            try:
                pr_val = float(bp)
            except (TypeError, ValueError):
                continue
            g = _gint(gp_raw)
            pr_map[(str(pn).strip(), str(sn).strip())] = (pr_val, g)

        cols = ["player_name", "season", "team_abbr", "team_id", "gp"]
        if tmins:
            cols.append(tmins)
        col_sql = ", ".join(cols)
        con.row_factory = sqlite3.Row
        cur_b = con.execute(
            f"""
            SELECT {col_sql}
            FROM player_stats_basic AS b
            WHERE EXISTS (
                SELECT 1 FROM player_simulation_pr AS p
                WHERE p.player_name = b.player_name
                  AND p.season = b.season
            )
            """
        )
        basic_rows = [dict(r) for r in cur_b.fetchall()]
        con.row_factory = None

        by_ps: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in basic_rows:
            if tmins is None:
                row["total_minutes"] = None
            pn = str(row["player_name"]).strip()
            sn = str(row["season"]).strip()
            by_ps[(pn, sn)].append(row)

        merged: list[tuple[str, str, str, float, int]] = []
        skipped_no_team = 0
        missing_basic = 0
        for (pn, sn), (pr_val, gpv) in pr_map.items():
            stints = by_ps.get((pn, sn), [])
            if not stints:
                missing_basic += 1
                continue
            team = pick_team_for_player_season(stints)
            if team is None:
                skipped_no_team += 1
                continue
            merged.append((pn, sn, team, pr_val, gpv))

        if missing_basic:
            print(
                f"  [warn] {missing_basic} player-seasons in player_simulation_pr "
                f"had no player_stats_basic rows (omitted).",
                flush=True,
            )
        if skipped_no_team:
            print(
                f"  [warn] Skipped {skipped_no_team} player-seasons with no assignable team.",
                flush=True,
            )

        by_group: dict[tuple[str, str], list[tuple[str, float, int]]] = defaultdict(list)
        for pn, sn, team, pr_val, gpv in merged:
            by_group[(sn, team)].append((pn, pr_val, gpv))

        team_season_pr: dict[tuple[str, str], float] = {}
        for (sn, team), players in by_group.items():
            ranked = sorted(
                players,
                key=lambda x: (
                    -effective_player_pr(x[1], x[2]),
                    x[0],
                ),
            )
            top = ranked[:ROTATION_SIZE]
            team_season_pr[(sn, team)] = sum(effective_player_pr(pr, gp) for _, pr, gp in top)

        rows_db = [
            (team, sn, team_season_pr[(sn, team)])
            for (sn, team) in team_season_pr
        ]
        con.executemany(
            """
            INSERT INTO team_simulation_pr (team, season, base_team_pr)
            VALUES (?, ?, ?)
            ON CONFLICT(team, season) DO UPDATE SET
                base_team_pr = excluded.base_team_pr;
            """,
            rows_db,
        )
        con.commit()

        if not rows_db:
            print("No team-season rows written.", flush=True)
            return

        seasons = {sn for sn, _ in team_season_pr}
        latest = max(seasons)
        latest_ranked = sorted(
            (
                (team, team_season_pr[(latest, team)])
                for team in {t for s, t in team_season_pr if s == latest}
            ),
            key=lambda x: -x[1],
        )

        print("", flush=True)
        print(
            f">   * Top 5 teams by base_team_pr — season {latest} "
            f"({ROTATION_SIZE}-man rotation, gp reliability vs {GP_REFERENCE:.0f} games)",
            flush=True,
        )
        for i, (team, pr) in enumerate(latest_ranked[:5], start=1):
            print(f">   *   {i}. {team}  base_team_pr={pr:.2f}", flush=True)

        print("", flush=True)
        print(f">   * Bottom 5 teams — season {latest}", flush=True)
        bottom = (
            list(reversed(latest_ranked[-5:]))
            if len(latest_ranked) >= 5
            else list(reversed(latest_ranked))
        )
        for i, (team, pr) in enumerate(bottom, start=1):
            print(f">   *   {i}. {team}  base_team_pr={pr:.2f}", flush=True)
    finally:
        con.close()


if __name__ == "__main__":
    main()
