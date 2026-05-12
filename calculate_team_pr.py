"""
calculate_team_pr.py
--------------------
2024-25 team ``final_team_pr``: 9-man rotation (240 min), weighted ``base_team_pr``,
multiplicative synergy (weak-link, bench depth, defensive floor), flat system-identity
points from ``team_playstyle_data``, and coach-grade multiplier from ``coach_data``.

Reads: player_simulation_pr, player_stats_basic, player_stats_advanced,
       team_playstyle_data (optional), coach_data + coach_system_data (optional).
Writes only: team_simulation_pr.
"""

from __future__ import annotations

import math
import os
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

TARGET_SEASON = "2024-25"

GUARD_MINUTES = 96
FORWARD_MINUTES = 96
CENTER_MINUTES = 48
TOTAL_MINUTES = 240

TOP_GUARDS = 3
TOP_FORWARDS = 3
TOP_CENTERS = 2

WEAK_LINK_THRESHOLD = 14.0
WEAK_LINK_MULT_EACH = 0.96

ELITE_BENCH_THRESHOLD = 25.0
ELITE_BENCH_MULT = 1.05

DEF_SCORE_COEF = 2.5
DEF_FLOOR_THRESHOLD = 8.0
DEF_STARTERS_QUALIFIED = 4  # of 5 starters
DEF_FLOOR_MULT = 1.04

COACH_GRADE_MULT: dict[str, float] = {
    "S": 1.08,
    "A": 1.05,
    "B": 1.02,
    "C": 1.00,
    "D": 0.96,
    "F": 0.90,
}

GUARD_SET = frozenset({"PG", "SG", "G", "G-F"})
FORWARD_SET = frozenset({"SF", "PF", "F", "F-C", "F-G"})
CENTER_SET = frozenset({"C", "C-F"})

SPECIAL_AUDIT_TEAMS = ("OKC", "BOS", "SAC")
TOP_N = 15

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def pragma_columns(con: sqlite3.Connection, table: str) -> set[str]:
    cur = con.execute(f"PRAGMA table_info({table})")
    return {r[1] for r in cur.fetchall()}


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    cur = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    )
    return cur.fetchone() is not None


def ffloat(v: Any) -> float:
    if v is None:
        return 0.0
    try:
        x = float(v)
        return 0.0 if math.isnan(x) else x
    except (TypeError, ValueError):
        return 0.0


def normalize_position(raw: str | None) -> str:
    if raw is None:
        return ""
    p = str(raw).strip().upper()
    return re.sub(r"\s*-\s*", "-", p)


def pos_bucket(raw: str) -> str | None:
    p = normalize_position(raw)
    if not p:
        return None
    if p in GUARD_SET:
        return "G"
    if p in FORWARD_SET:
        return "F"
    if p in CENTER_SET:
        return "C"
    if "-" in p:
        parts = [x.strip() for x in p.split("-") if x.strip()]
        if len(parts) == 2:
            a, b = parts[0].upper(), parts[1].upper()
            if a == "G" and b == "F":
                return "G"
            if a == "F" and b == "G":
                return "F"
            if a == "F" and b == "C":
                return "F"
            if a == "C" and b == "F":
                return "C"
    return None


def allocate_minutes_int(weights: list[float], total: int) -> list[int]:
    if not weights:
        return []
    eps = 1e-9
    ws = [max(float(w), eps) for w in weights]
    s = sum(ws)
    raw = [total * w / s for w in ws]
    floors = [int(math.floor(r)) for r in raw]
    rem = total - sum(floors)
    order = sorted(
        range(len(raw)),
        key=lambda i: raw[i] - floors[i],
        reverse=True,
    )
    for k in range(rem):
        floors[order[k % len(order)]] += 1
    return floors


@dataclass(frozen=True)
class RosterLine:
    player_name: str
    team: str
    base_pr: float
    position_raw: str
    bucket: str
    stl: float
    blk: float
    deflections: float


@dataclass
class RotationPlayer:
    line: RosterLine
    alloc_min: int = 0


@dataclass
class TeamResult:
    team: str
    base_weighted: float
    weak_links: int
    bench_avg: float | None
    synergy_total: float
    system_pts: float
    system_label: str
    coach_grade: str
    coach_mult: float
    final_team_pr: int
    rotation: list[RotationPlayer]
    starters: set[str]


def ensure_team_columns(con: sqlite3.Connection) -> None:
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
    cols = pragma_columns(con, "team_simulation_pr")
    if "coach_multiplier" not in cols:
        con.execute(
            "ALTER TABLE team_simulation_pr ADD COLUMN coach_multiplier REAL;"
        )
    if "adjusted_team_pr" not in cols:
        con.execute(
            "ALTER TABLE team_simulation_pr ADD COLUMN adjusted_team_pr REAL;"
        )
    if "final_team_pr" not in cols:
        con.execute("ALTER TABLE team_simulation_pr ADD COLUMN final_team_pr REAL;")


def load_playstyle_map(con: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    if not table_exists(con, "team_playstyle_data"):
        return {}
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(
            """
            SELECT team_abbr, playstyle, all_playstyles
            FROM team_playstyle_data
            WHERE season = ?
            """,
            (TARGET_SEASON,),
        )
        out: dict[str, tuple[str, str]] = {}
        for r in cur:
            ta = r["team_abbr"]
            if ta is None:
                continue
            t = str(ta).strip()
            ps = str(r["playstyle"] or "").strip()
            al = str(r["all_playstyles"] or "").strip()
            out[t] = (ps, al)
        return out
    finally:
        con.row_factory = None


def load_coach_grade_map(con: sqlite3.Connection) -> dict[str, str]:
    if not table_exists(con, "coach_data"):
        return {}
    sql = """
        SELECT cd.team_abbr, cs.Grade
        FROM coach_data AS cd
        LEFT JOIN coach_system_data AS cs
          ON TRIM(COALESCE(cs.name, '')) = TRIM(COALESCE(cd.coach_name, ''))
        WHERE cd.season = ?
    """
    con.row_factory = sqlite3.Row
    try:
        out: dict[str, str] = {}
        for r in con.execute(sql, (TARGET_SEASON,)):
            ta = r["team_abbr"]
            if ta is None:
                continue
            g = r["Grade"]
            out[str(ta).strip()] = str(g).strip().upper() if g else ""
        return out
    finally:
        con.row_factory = None


def normalize_coach_letter(raw: str) -> str | None:
    if not raw:
        return None
    s = raw.strip().upper()
    if len(s) >= 1 and s[0] in COACH_GRADE_MULT:
        return s[0]
    return None


def coach_multiplier_for_team(grade_map: dict[str, str], team: str) -> tuple[str, float]:
    g = grade_map.get(team, "")
    letter = normalize_coach_letter(g)
    if letter is None:
        return ("—", COACH_GRADE_MULT["C"])
    return (letter, COACH_GRADE_MULT[letter])


def system_identity_points(playstyle: str, all_playstyles: str) -> tuple[float, str]:
    """
    Perfect +5 | Great +3 | Good +1.5 | Average 0 | Poor -3 | No Identity -6.
    DB labels from assign_team_playstyles: Perfect, Motion, ..., Undefined / No Identity.
    """
    p = (playstyle or "").strip()
    a = (all_playstyles or "").strip()
    if p == "Perfect":
        return 5.0, "Perfect"
    undef = (
        p == "Undefined / No Identity"
        or p == "Undefined"
        or (not p and not a)
    )
    if undef:
        return -6.0, "No Identity"

    tokens = [x.strip() for x in a.split(",") if x.strip()]
    named = {
        "Motion",
        "Pace & Space",
        "Paint & Pound",
        "Heliocentric",
    }
    if len(tokens) >= 2:
        return 3.0, "Great"
    if len(tokens) == 1:
        return 1.5, "Good"
    if p in named:
        return 1.5, "Good"

    # Unclassified label — treat as Average unless clearly negative
    if "poor" in p.lower():
        return -3.0, "Poor"
    return 0.0, "Average"


def identity_for_team(
    pmap: dict[str, tuple[str, str]], team: str
) -> tuple[float, str]:
    if team not in pmap:
        return -6.0, "No Identity"
    return system_identity_points(*pmap[team])


def load_roster(con: sqlite3.Connection) -> list[RosterLine]:
    sql = """
      SELECT
        p.player_name AS player_name,
        p.team AS team,
        p.base_pr AS base_pr,
        b.position AS position,
        b.stl AS stl,
        b.blk AS blk,
        a.deflections AS deflections
      FROM player_simulation_pr AS p
      INNER JOIN player_stats_basic AS b
        ON b.player_name = p.player_name
       AND b.season = p.season
       AND b.team_abbr = p.team
      INNER JOIN player_stats_advanced AS a
        ON a.player_id = b.player_id
       AND a.season = b.season
       AND a.team_id = b.team_id
      WHERE p.season = ?
    """
    rows: list[RosterLine] = []
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(sql, (TARGET_SEASON,))
        for r in cur:
            pn = r["player_name"]
            team = r["team"]
            if pn is None or team is None:
                continue
            pr = ffloat(r["base_pr"])
            pos_raw = str(r["position"] or "").strip()
            bkt = pos_bucket(pos_raw)
            if bkt is None:
                continue
            rows.append(
                RosterLine(
                    player_name=str(pn).strip(),
                    team=str(team).strip(),
                    base_pr=pr,
                    position_raw=pos_raw or "?",
                    bucket=bkt,
                    stl=ffloat(r["stl"]),
                    blk=ffloat(r["blk"]),
                    deflections=ffloat(r["deflections"]),
                )
            )
    finally:
        con.row_factory = None
    return rows


def select_nine(
    team_lines: list[RosterLine],
) -> tuple[list[RosterLine], list[RosterLine], list[RosterLine], RosterLine | None]:
    guards = [x for x in team_lines if x.bucket == "G"]
    forwards = [x for x in team_lines if x.bucket == "F"]
    centers = [x for x in team_lines if x.bucket == "C"]
    guards.sort(key=lambda x: (-x.base_pr, x.player_name))
    forwards.sort(key=lambda x: (-x.base_pr, x.player_name))
    centers.sort(key=lambda x: (-x.base_pr, x.player_name))
    cg = guards[:TOP_GUARDS]
    cf = forwards[:TOP_FORWARDS]
    cc = centers[:TOP_CENTERS]
    chosen = {x.player_name for x in cg + cf + cc}
    pool = [x for x in team_lines if x.player_name not in chosen and x.bucket in ("G", "F")]
    pool.sort(key=lambda x: (-x.base_pr, x.player_name))
    wild = pool[0] if pool else None
    if wild and wild.bucket == "G":
        final_g = cg + [wild]
        final_f = cf
    elif wild and wild.bucket == "F":
        final_g = cg
        final_f = cf + [wild]
    else:
        final_g = cg
        final_f = cf
    return final_g, final_f, cc, wild


def build_rotation_with_minutes(
    final_g: list[RosterLine], final_f: list[RosterLine], final_c: list[RosterLine]
) -> list[RotationPlayer]:
    out: list[RotationPlayer] = []
    g_tot = GUARD_MINUTES
    f_tot = FORWARD_MINUTES
    c_tot = CENTER_MINUTES
    if not final_c:
        g_tot += CENTER_MINUTES // 2
        f_tot += CENTER_MINUTES - CENTER_MINUTES // 2
        c_tot = 0
    if not final_g and final_f:
        f_tot += g_tot
        g_tot = 0
    if not final_f and final_g:
        g_tot += f_tot
        f_tot = 0

    if final_c and c_tot > 0:
        mins_c = allocate_minutes_int([p.base_pr for p in final_c], c_tot)
        for line, m in zip(final_c, mins_c):
            out.append(RotationPlayer(line=line, alloc_min=m))
    if final_g and g_tot > 0:
        mins_g = allocate_minutes_int([p.base_pr for p in final_g], g_tot)
        for line, m in zip(final_g, mins_g):
            out.append(RotationPlayer(line=line, alloc_min=m))
    if final_f and f_tot > 0:
        mins_f = allocate_minutes_int([p.base_pr for p in final_f], f_tot)
        for line, m in zip(final_f, mins_f):
            out.append(RotationPlayer(line=line, alloc_min=m))

    total = sum(p.alloc_min for p in out)
    if total != TOTAL_MINUTES:
        raise RuntimeError(f"{total} minutes != {TOTAL_MINUTES}")
    return out


def starter_names(final_g: list[RosterLine], final_f: list[RosterLine], final_c: list[RosterLine]) -> set[str]:
    sg = sorted(final_g, key=lambda x: (-x.base_pr, x.player_name))[:2]
    sf = sorted(final_f, key=lambda x: (-x.base_pr, x.player_name))[:2]
    sc = sorted(final_c, key=lambda x: (-x.base_pr, x.player_name))[:1]
    return {x.player_name for x in sg + sf + sc}


def player_def_score(line: RosterLine) -> float:
    return (line.stl + line.blk + line.deflections) * DEF_SCORE_COEF


def rotation_base_pr(rotation: list[RotationPlayer]) -> float:
    return sum(
        rp.line.base_pr * (rp.alloc_min / float(TOTAL_MINUTES)) for rp in rotation
    )


def synergy_multipliers(
    rotation: list[RotationPlayer], starters: set[str]
) -> tuple[int, float | None, float, float, float, float]:
    """
    Returns weak_count, bench_avg, weak_mult_product, bench_mod, def_mod, synergy_total.
    """
    nine = list(rotation)
    weak = sum(1 for rp in nine if rp.line.base_pr < WEAK_LINK_THRESHOLD)
    weak_mod = WEAK_LINK_MULT_EACH**weak

    bench_lines = [rp.line for rp in nine if rp.line.player_name not in starters]
    bench_avg: float | None = None
    bench_mod = 1.0
    if len(bench_lines) == 4:
        bench_avg = sum(x.base_pr for x in bench_lines) / 4.0
        if bench_avg > ELITE_BENCH_THRESHOLD:
            bench_mod = ELITE_BENCH_MULT

    def_mod = 1.0
    starter_rps = [rp for rp in nine if rp.line.player_name in starters]
    if len(starter_rps) == 5:
        scores = [player_def_score(rp.line) for rp in starter_rps]
        ok = sum(1 for s in scores if s > DEF_FLOOR_THRESHOLD)
        if ok >= DEF_STARTERS_QUALIFIED:
            def_mod = DEF_FLOOR_MULT

    base_w = rotation_base_pr(nine)
    synergy = base_w * weak_mod * bench_mod * def_mod
    return weak, bench_avg, weak_mod, bench_mod, def_mod, synergy


def upsert_team(
    con: sqlite3.Connection,
    team: str,
    base_team_pr: float,
    coach_mult: float,
    final_team_pr: int,
) -> None:
    adj = float(final_team_pr)
    con.execute(
        """
        INSERT INTO team_simulation_pr
            (team, season, base_team_pr, coach_multiplier, final_team_pr, adjusted_team_pr)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(team, season) DO UPDATE SET
            base_team_pr = excluded.base_team_pr,
            coach_multiplier = excluded.coach_multiplier,
            final_team_pr = excluded.final_team_pr,
            adjusted_team_pr = excluded.adjusted_team_pr;
        """,
        (team, TARGET_SEASON, base_team_pr, coach_mult, adj, adj),
    )


def print_special_rotation(team: str, result: TeamResult) -> None:
    print(
        f"  --- {team} (final_team_pr={result.final_team_pr}) ---",
        flush=True,
    )
    col = (20, 10, 8, 12)
    h = (
        f"{'Player':<{col[0]}} | {'Role':<{col[1]}} | {'PR':>{col[2]}} | "
        f"{'Alloc Mins':>{col[3]}}"
    )
    print(h, flush=True)
    print(f"  {'-' * (len(h) + 2)}", flush=True)

    def sort_key(rp: RotationPlayer) -> tuple[int, float, str]:
        st = 0 if rp.line.player_name in result.starters else 1
        return (st, -rp.line.base_pr, rp.line.player_name)

    for rp in sorted(result.rotation, key=sort_key):
        role = "Starter" if rp.line.player_name in result.starters else "Bench"
        print(
            f"  {rp.line.player_name:<{col[0]}} | {role:<{col[1]}} | "
            f"{rp.line.base_pr:>{col[2]}.0f} | {rp.alloc_min:>{col[3]}}",
            flush=True,
        )
    print(flush=True)


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            ("player_simulation_pr",),
        )
        if cur.fetchone() is None:
            print("Missing player_simulation_pr.", flush=True)
            return

        ensure_team_columns(con)
        pmap = load_playstyle_map(con)
        gmap = load_coach_grade_map(con)

        roster = load_roster(con)
        if not roster:
            print(
                f"No roster for {TARGET_SEASON} (sim + basic + advanced join).",
                flush=True,
            )
            return

        by_team: dict[str, list[RosterLine]] = defaultdict(list)
        for ln in roster:
            by_team[ln.team].append(ln)

        results: list[TeamResult] = []
        for team, lines in sorted(by_team.items()):
            fg, ff, fc, wild = select_nine(lines)
            if wild is None and len(fg) + len(ff) + len(fc) < 9:
                print(
                    f"[warn] {team}: only {len(fg)+len(ff)+len(fc)} players in rotation.",
                    flush=True,
                )
            rot = build_rotation_with_minutes(fg, ff, fc)
            starters = starter_names(fg, ff, fc)
            weak, bavg, _wm, _bm, _dm, synergy = synergy_multipliers(rot, starters)
            sys_pts, sys_lbl = identity_for_team(pmap, team)
            coach_ltr, coach_m = coach_multiplier_for_team(gmap, team)

            system_total = synergy + sys_pts
            final_int = int(round(system_total * coach_m))
            base_w = rotation_base_pr(rot)

            upsert_team(con, team, base_w, coach_m, final_int)
            results.append(
                TeamResult(
                    team=team,
                    base_weighted=base_w,
                    weak_links=weak,
                    bench_avg=bavg,
                    synergy_total=synergy,
                    system_pts=sys_pts,
                    system_label=sys_lbl,
                    coach_grade=coach_ltr,
                    coach_mult=coach_m,
                    final_team_pr=final_int,
                    rotation=rot,
                    starters=starters,
                )
            )

        con.commit()

        results.sort(key=lambda x: (-x.final_team_pr, x.team))
        print("", flush=True)
        print(f"Top {TOP_N} teams by final_team_pr — {TARGET_SEASON}", flush=True)
        cw = (5, 10, 10, 11, 7, 14, 16)
        hdr = (
            f"{'Team':<{cw[0]}} | {'Base PR':>{cw[1]}} | {'Bench Avg':>{cw[2]}} | "
            f"{'Weak Links':>{cw[3]}} | {'Coach':>{cw[4]}} | {'System':<{cw[5]}} | "
            f"{'Final Team PR':>{cw[6]}}"
        )
        print(hdr, flush=True)
        print("-" * len(hdr), flush=True)
        for tr in results[:TOP_N]:
            bstr = f"{tr.bench_avg:.2f}" if tr.bench_avg is not None else "—"
            ch = f"{tr.coach_grade}" if tr.coach_grade != "—" else "—"
            print(
                f"{tr.team:<{cw[0]}} | {tr.base_weighted:>{cw[1]}.2f} | {bstr:>{cw[2]}} | "
                f"{tr.weak_links:>{cw[3]}} | {ch:>{cw[4]}} | {tr.system_label:<{cw[5]}} | "
                f"{tr.final_team_pr:>{cw[6]}}",
                flush=True,
            )

        by_abbr = {tr.team: tr for tr in results}
        print("", flush=True)
        print("Special audit — 9-man rotations (OKC, BOS, SAC)", flush=True)
        for ab in SPECIAL_AUDIT_TEAMS:
            tr = by_abbr.get(ab)
            if tr is None:
                print(f"  (no row) {ab}", flush=True)
                continue
            print_special_rotation(ab, tr)
    finally:
        con.close()


if __name__ == "__main__":
    main()
