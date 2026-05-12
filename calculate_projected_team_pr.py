"""
calculate_projected_team_pr.py
--------------------------------
Predictive team PR: positional quota draft, 240-minute slate, starter's-burden
role modifier on pure base_pr, then coach grade multiplier.

Reads: player_simulation_pr, player_stats_basic, coach_data, coach_system_data.
Writes: ``team_simulation_pr`` rows for ``{target_season}-Projected`` using existing
columns only: ``base_team_pr`` (rotation sum), ``coach_multiplier``, ``adjusted_team_pr``.
Does not alter table schema.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

AGG_TEAM_ABBRS = frozenset({"TOT", "2TM", "3TM", "4TM"})

QUOTA_G = 4
QUOTA_F = 3
QUOTA_C = 2
ROTATION_TARGET = QUOTA_G + QUOTA_F + QUOTA_C

MINUTES_SLATE: list[float] = [34.0, 32.0, 30.0, 28.0, 26.0, 24.0, 24.0, 22.0, 20.0]

GRADE_MULTIPLIER_SIM: dict[str, float] = {
    "S": 1.08,
    "A": 1.04,
    "B": 1.00,
    "C": 0.96,
    "D": 0.92,
    "F": 0.88,
}

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


def _gint(v: Any) -> int:
    if v is None:
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def pick_stint_for_player_season(stints: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not stints:
        return None
    non_agg = [s for s in stints if not is_aggregate_row(s.get("team_abbr"), s.get("team_id"))]
    pool = non_agg if non_agg else []
    if not pool:
        return None
    return max(
        pool,
        key=lambda s: (
            _gint(s.get("gp")),
            _gint(s.get("total_minutes")),
            str(s.get("team_abbr") or ""),
        ),
    )


def simplify_position(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().upper()
    if not s:
        return None
    if "-" in s:
        s = s.split("-")[0].strip()
    if "/" in s:
        s = s.split("/")[0].strip()
    if s in ("PG", "SG"):
        return "G"
    if s in ("SF", "PF"):
        return "F"
    if s == "C":
        return "C"
    return None


def normalize_coach_grade(g: object | None) -> str | None:
    if g is None:
        return None
    s = str(g).strip().upper()
    if not s:
        return None
    return s if s in GRADE_MULTIPLIER_SIM else None


def coach_multiplier_for_grade(g: object | None) -> float:
    letter = normalize_coach_grade(g)
    if letter is None:
        return 1.00
    return GRADE_MULTIPLIER_SIM[letter]


@dataclass
class RosterPlayer:
    player_name: str
    season: str
    team: str
    base_pr: float
    raw_position: str | None
    bucket: str | None


@dataclass
class RotationPlayer:
    player_name: str
    raw_position: str | None
    bucket: str | None
    base_pr: float
    simulated_minutes: float
    effective_pr: float


def role_modifier(minutes: float) -> float:
    if minutes <= 0:
        return 0.0
    return (minutes / 36.0) ** 0.5


def draft_rotation(players: list[RosterPlayer]) -> list[RosterPlayer]:
    by_bucket: dict[str, list[RosterPlayer]] = {"G": [], "F": [], "C": []}
    for p in players:
        b = p.bucket
        if b in by_bucket:
            by_bucket[b].append(p)
    for k in by_bucket:
        by_bucket[k].sort(key=lambda x: (-x.base_pr, x.player_name))

    chosen: list[RosterPlayer] = []
    seen: set[str] = set()

    def take(bucket: str, n: int) -> None:
        for p in by_bucket[bucket][:n]:
            if p.player_name not in seen:
                chosen.append(p)
                seen.add(p.player_name)

    take("G", QUOTA_G)
    take("F", QUOTA_F)
    take("C", QUOTA_C)

    if len(chosen) < ROTATION_TARGET:
        remaining = [p for p in players if p.player_name not in seen]
        remaining.sort(key=lambda x: (-x.base_pr, x.player_name))
        for p in remaining:
            if len(chosen) >= ROTATION_TARGET:
                break
            chosen.append(p)
            seen.add(p.player_name)

    return chosen


def assign_minutes_and_effective(selected: list[RosterPlayer]) -> list[RotationPlayer]:
    ordered = sorted(selected, key=lambda x: (-x.base_pr, x.player_name))
    out: list[RotationPlayer] = []
    for i, p in enumerate(ordered):
        mins = MINUTES_SLATE[i] if i < len(MINUTES_SLATE) else 0.0
        rm = role_modifier(mins)
        eff = p.base_pr * rm
        out.append(
            RotationPlayer(
                player_name=p.player_name,
                raw_position=p.raw_position,
                bucket=p.bucket,
                base_pr=p.base_pr,
                simulated_minutes=mins,
                effective_pr=eff,
            )
        )
    return out


REQUIRED_TEAM_SIM_COLS = frozenset(
    {"team", "season", "base_team_pr", "coach_multiplier", "adjusted_team_pr"}
)


def projected_season_key(source_season: str) -> str:
    """e.g. ``2025-26`` -> ``2025-26-Projected`` (does not overwrite historical rows)."""
    return f"{source_season.strip()}-Projected"


def load_projection_roster(
    con: sqlite3.Connection,
    target_season: str,
) -> tuple[list[RosterPlayer], int]:
    """Build per-player roster rows for ``target_season`` (aligned with ``player_simulation_pr``)."""
    basic_cols = pragma_columns(con, "player_stats_basic")
    tmins = "total_minutes" if "total_minutes" in basic_cols else None

    pr_map: dict[str, tuple[str, float]] = {}
    cur_pr = con.execute(
        """
        SELECT player_name, season, base_pr
        FROM player_simulation_pr
        WHERE season = ?
        """,
        (target_season,),
    )
    for pn, sn, bp in cur_pr.fetchall():
        try:
            pr_map[str(pn).strip()] = (str(sn).strip(), float(bp))
        except (TypeError, ValueError):
            continue

    cols = ["player_name", "season", "team_abbr", "team_id", "gp", "position"]
    if tmins:
        cols.append(tmins)
    col_sql = ", ".join(cols)
    prev_rf = con.row_factory
    con.row_factory = sqlite3.Row
    cur_b = con.execute(
        f"""
        SELECT {col_sql}
        FROM player_stats_basic AS b
        WHERE b.season = ?
          AND EXISTS (
            SELECT 1 FROM player_simulation_pr AS p
            WHERE p.player_name = b.player_name
              AND p.season = b.season
          )
        """,
        (target_season,),
    )
    basic_rows = [dict(r) for r in cur_b.fetchall()]
    con.row_factory = prev_rf

    by_player: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in basic_rows:
        if tmins is None:
            row["total_minutes"] = None
        pn = str(row["player_name"]).strip()
        by_player[pn].append(row)

    roster: list[RosterPlayer] = []
    skipped = 0
    for pn, (src_season, base_pr) in pr_map.items():
        st = pick_stint_for_player_season(by_player.get(pn, []))
        if st is None:
            skipped += 1
            continue
        ta = st.get("team_abbr")
        team = str(ta).strip() if ta else None
        if not team:
            skipped += 1
            continue
        raw_pos = st.get("position")
        raw_str = str(raw_pos).strip() if raw_pos is not None else None
        if raw_str == "":
            raw_str = None
        bucket = simplify_position(raw_str)
        roster.append(
            RosterPlayer(
                player_name=pn,
                season=src_season,
                team=team,
                base_pr=base_pr,
                raw_position=raw_str,
                bucket=bucket,
            )
        )
    return roster, skipped


def team_rotations_from_roster(
    roster: list[RosterPlayer],
) -> dict[str, list[RotationPlayer]]:
    by_team: dict[str, list[RosterPlayer]] = defaultdict(list)
    for p in roster:
        by_team[p.team].append(p)
    rotations: dict[str, list[RotationPlayer]] = {}
    for team, plist in by_team.items():
        selected = draft_rotation(plist)
        rotations[team] = assign_minutes_and_effective(selected)
    return rotations


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        for tbl in (
            "player_simulation_pr",
            "player_stats_basic",
            "coach_data",
            "coach_system_data",
            "team_simulation_pr",
        ):
            cur = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (tbl,),
            )
            if cur.fetchone() is None:
                print(f"Missing table {tbl}. Run prior pipelines first.", flush=True)
                return

        sim_cols = pragma_columns(con, "team_simulation_pr")
        if not REQUIRED_TEAM_SIM_COLS.issubset(sim_cols):
            print(
                "team_simulation_pr must have columns: "
                f"{sorted(REQUIRED_TEAM_SIM_COLS)}",
                flush=True,
            )
            return

        cur_seasons = con.execute("SELECT DISTINCT season FROM player_simulation_pr").fetchall()
        seasons = {str(r[0]).strip() for r in cur_seasons if r[0] is not None}
        if not seasons:
            print("No seasons in player_simulation_pr.", flush=True)
            return
        target_season = max(seasons)
        write_season = projected_season_key(target_season)

        roster, skipped = load_projection_roster(con, target_season)
        if skipped:
            print(
                f"  [warn] Skipped {skipped} players with no assignable team stint.",
                flush=True,
            )

        rotations = team_rotations_from_roster(roster)
        team_base_pr = {t: sum(r.effective_pr for r in rot) for t, rot in rotations.items()}

        coach_lookup: dict[str, str | None] = {}
        cur_coach = con.execute(
            """
            SELECT team_abbr, coach_name
            FROM coach_data
            WHERE season = ?
            """,
            (target_season,),
        )
        for tabbr, cname in cur_coach.fetchall():
            if tabbr is None:
                continue
            coach_lookup[str(tabbr).strip()] = (
                str(cname).strip() if cname else None
            )

        grade_by_name: dict[str, str | None] = {}
        for row in con.execute("SELECT name, Grade FROM coach_system_data").fetchall():
            nm, gr = row[0], row[1]
            if nm is None:
                continue
            grade_by_name[str(nm).strip().lower()] = (
                str(gr).strip() if gr is not None else None
            )

        rows_db: list[tuple[str, str, float, float, float]] = []

        for team, team_sum in team_base_pr.items():
            cname_key = coach_lookup.get(team)
            grade_raw: str | None = None
            if cname_key:
                grade_raw = grade_by_name.get(cname_key.lower())
            mult = coach_multiplier_for_grade(grade_raw)
            adjusted = team_sum * mult
            rows_db.append((team, write_season, team_sum, mult, adjusted))

        if not rows_db:
            print("No team rows to write.", flush=True)
            return

        con.executemany(
            """
            INSERT INTO team_simulation_pr
                (team, season, base_team_pr, coach_multiplier, adjusted_team_pr)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(team, season) DO UPDATE SET
                base_team_pr = excluded.base_team_pr,
                coach_multiplier = excluded.coach_multiplier,
                adjusted_team_pr = excluded.adjusted_team_pr;
            """,
            rows_db,
        )
        con.commit()

        top10 = con.execute(
            """
            SELECT team, season, base_team_pr, coach_multiplier, adjusted_team_pr
            FROM team_simulation_pr
            WHERE season = ?
            ORDER BY adjusted_team_pr DESC
            LIMIT 10
            """,
            (write_season,),
        ).fetchall()

        print(f"\nProjection season: {write_season} (source stats: {target_season})", flush=True)
        print(
            "Top 10 by adjusted_team_pr:",
            flush=True,
        )
        print(
            f"  {'#':>2}  {'Team':<5}  {'base_team_pr':>12}  "
            f"{'coach_mult':>10}  {'adjusted_team_pr':>16}",
            flush=True,
        )
        for i, (team, sn, base_raw, mult_raw, adj_raw) in enumerate(top10, start=1):
            base_v = float(base_raw) if base_raw is not None else 0.0
            m_v = float(mult_raw) if mult_raw is not None else 1.0
            adj_v = float(adj_raw) if adj_raw is not None else 0.0
            print(
                f"  {i:2d}  {str(team):<5}  {base_v:12.2f}  {m_v:10.2f}  {adj_v:16.2f}",
                flush=True,
            )
    finally:
        con.close()


def print_all_projected_nine_man_rotations() -> None:
    """Print every team's 9-man rotation for the latest season in ``player_simulation_pr``."""
    con = sqlite3.connect(DB_PATH)
    try:
        for tbl in ("player_simulation_pr", "player_stats_basic"):
            if (
                con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (tbl,),
                ).fetchone()
                is None
            ):
                print(f"Missing table {tbl}.", flush=True)
                return

        cur_seasons = con.execute("SELECT DISTINCT season FROM player_simulation_pr").fetchall()
        seasons = {str(r[0]).strip() for r in cur_seasons if r[0] is not None}
        if not seasons:
            print("No seasons in player_simulation_pr.", flush=True)
            return
        target_season = max(seasons)

        roster, skipped = load_projection_roster(con, target_season)
        if skipped:
            print(
                f"[warn] Skipped {skipped} players with no assignable team stint.",
                flush=True,
            )

        rotations = team_rotations_from_roster(roster)
        team_sum = {t: sum(r.effective_pr for r in rot) for t, rot in rotations.items()}
        ranked_teams = sorted(team_sum.keys(), key=lambda t: -team_sum[t])

        print(
            f"\n9-man projected rotations (stats season {target_season}; "
            f"minutes slate {MINUTES_SLATE})",
            flush=True,
        )
        for team in ranked_teams:
            rot = rotations[team]
            print(f"\n{team}  (sum effective PR = {team_sum[team]:.2f})", flush=True)
            print(
                f"  {'#':>2}  {'Player':<26}  {'Pos':^5}  "
                f"{'base_pr':>8}  {'min':>6}  {'eff_pr':>8}",
                flush=True,
            )
            for i, r in enumerate(rot, start=1):
                pos = r.raw_position or (r.bucket or "—")
                print(
                    f"  {i:2d}  {r.player_name:<26}  {pos:^5}  "
                    f"{r.base_pr:8.2f}  {r.simulated_minutes:6.1f}  {r.effective_pr:8.2f}",
                    flush=True,
                )
    finally:
        con.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--rotations":
        print_all_projected_nine_man_rotations()
    else:
        main()
