"""
assign_team_playstyles.py
-------------------------
Classifies offensive playstyles with dual thresholds. Perfect requires **three or more**
identified systems, **net_rating ≥ 5.0**, and **team turnover metric ≤ 14.0** (see
`team_tov_pct` on team_stats when present, else a basic-stats proxy). There is no Elite Balanced tier;
teams that match zero systems map to Undefined / No Identity only.

Reads team_stats, player_stats_advanced, and player_stats_basic (usage + proxies
when team_totals columns are missing from team_stats).

Writes only to team_playstyle_data.
"""

from __future__ import annotations

import math
import os
import sqlite3
import sys
from collections import Counter
from typing import Any

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def pragma_columns(con: sqlite3.Connection, table: str) -> set[str]:
    cur = con.execute(f"PRAGMA table_info({table})")
    return {r[1] for r in cur.fetchall()}


def ensure_playstyle_table(con: sqlite3.Connection) -> None:
    """Create team_playstyle_data if missing; add all_playstyles on legacy DBs."""
    from init_db import CREATE_TEAM_PLAYSTYLE_DATA

    con.execute(CREATE_TEAM_PLAYSTYLE_DATA)
    cols = pragma_columns(con, "team_playstyle_data")
    if "all_playstyles" not in cols:
        con.execute(
            "ALTER TABLE team_playstyle_data ADD COLUMN all_playstyles TEXT NOT NULL DEFAULT ''"
        )


def max_usage_and_player_ts_advanced(
    con: sqlite3.Connection, season: str, team_id: int
) -> tuple[float | None, float | None]:
    """Highest roster usg_pct and that player's ts_pct from player_stats_advanced."""
    row = con.execute(
        """
        SELECT b.usg_pct AS u, a.ts_pct AS ts
        FROM player_stats_basic AS b
        LEFT JOIN player_stats_advanced AS a
          ON a.season = b.season
         AND a.player_id = b.player_id
         AND a.team_id = b.team_id
        WHERE b.team_id = ?
          AND b.season = ?
          AND b.usg_pct IS NOT NULL
        ORDER BY b.usg_pct DESC
        LIMIT 1
        """,
        (team_id, season),
    ).fetchone()
    if not row:
        return None, None

    mu: float | None = None
    if row["u"] is not None:
        fv = float(row["u"])
        mu = None if math.isnan(fv) else fv

    ts: float | None = None
    if row["ts"] is not None:
        tv = float(row["ts"])
        ts = None if math.isnan(tv) else tv
    return mu, ts


def fetch_basic_team_proxies(
    con: sqlite3.Connection, season: str
) -> dict[int, dict[str, float | None]]:
    """
    When team_stats omits totals: approximate team AST/G from basics and avg fg3%.

    Team assists per game ~= SUM(player_apg * gp) / MAX(gp) on roster (season-length
    denominator); not the GP-weighted mean of APG (~3).
    """
    out: dict[int, dict[str, float | None]] = {}
    for row in con.execute(
        """
        SELECT
            team_id,
            SUM(ast * COALESCE(gp, 0)) * 1.0 / NULLIF(MAX(gp), 0) AS ast_team_pg,
            AVG(CASE WHEN COALESCE(gp, 0) >= 5 AND fg3_pct IS NOT NULL
                THEN fg3_pct END) AS fg3_avg
        FROM player_stats_basic
        WHERE season = ? AND team_id IS NOT NULL
        GROUP BY team_id
        """,
        (season,),
    ):
        tid = int(row["team_id"])
        out[tid] = {"ast_proxy": row["ast_team_pg"], "fg3_proxy": row["fg3_avg"]}
    return out


def fetch_team_tov_proxy(con: sqlite3.Connection, season: str) -> dict[int, float | None]:
    """SUM(tov×gp)/MAX(gp) from player_stats_basic — aligns with ~TOV/game scale."""
    out: dict[int, float | None] = {}
    for row in con.execute(
        """
        SELECT team_id,
            SUM(tov * COALESCE(gp, 0)) * 1.0 / NULLIF(MAX(gp), 0) AS tpg
        FROM player_stats_basic
        WHERE season = ? AND team_id IS NOT NULL
        GROUP BY team_id
        """,
        (season,),
    ):
        tid = int(row["team_id"])
        v = row["tpg"]
        if v is None:
            out[tid] = None
        else:
            fv = float(v)
            out[tid] = None if math.isnan(fv) else fv
    return out


def team_tov_pct_value(
    trow_dict: dict[str, Any],
    team_cols: set[str],
    basic_tov_proxy: dict[int, float | None],
    team_id: int,
) -> float | None:
    if "team_tov_pct" in team_cols and trow_dict.get("team_tov_pct") is not None:
        try:
            v = float(trow_dict["team_tov_pct"])
            return None if math.isnan(v) else v
        except (TypeError, ValueError):
            pass
    p = basic_tov_proxy.get(team_id)
    return p


def fetch_advanced_ts_proxy(con: sqlite3.Connection, season: str) -> dict[int, float | None]:
    """Mean player ts_pct on roster as fallback team true-shooting proxy."""
    out: dict[int, float | None] = {}
    for row in con.execute(
        """
        SELECT team_id, AVG(ts_pct) AS avg_ts
        FROM player_stats_advanced
        WHERE season = ? AND team_id IS NOT NULL AND ts_pct IS NOT NULL
        GROUP BY team_id
        """,
        (season,),
    ):
        tid = int(row["team_id"])
        av = row["avg_ts"]
        if av is None:
            out[tid] = None
        else:
            fv = float(av)
            out[tid] = None if math.isnan(fv) else fv
    return out


def team_assists_pg(
    trow_dict: dict[str, Any],
    team_cols: set[str],
    basic_proxy: dict[str, float | None] | None,
    team_id: int,
) -> float | None:
    if "ast" in team_cols and trow_dict.get("ast") is not None:
        try:
            v = float(trow_dict["ast"])
            return None if math.isnan(v) else v
        except (TypeError, ValueError):
            pass
    if basic_proxy:
        ax = basic_proxy.get("ast_proxy")
        if ax is not None:
            try:
                v = float(ax)
                return None if math.isnan(v) else v
            except (TypeError, ValueError):
                return None
    return None


def team_fg3_pct_value(
    trow_dict: dict[str, Any],
    team_cols: set[str],
    basic_proxy: dict[str, float | None] | None,
    team_id: int,
) -> float | None:
    if "fg3_pct" in team_cols and trow_dict.get("fg3_pct") is not None:
        try:
            v = float(trow_dict["fg3_pct"])
            return None if math.isnan(v) else v
        except (TypeError, ValueError):
            pass
    if basic_proxy:
        gx = basic_proxy.get("fg3_proxy")
        if gx is not None:
            try:
                v = float(gx)
                return None if math.isnan(v) else v
            except (TypeError, ValueError):
                return None
    return None


def team_ts_pct_value(
    trow_dict: dict[str, Any],
    team_cols: set[str],
    adv_ts_proxy: dict[int, float | None],
    team_id: int,
    team_pts_pg: float | None,
) -> float | None:
    if "ts_pct" in team_cols and trow_dict.get("ts_pct") is not None:
        try:
            v = float(trow_dict["ts_pct"])
            return None if math.isnan(v) else v
        except (TypeError, ValueError):
            pass
    if {"fga", "fta"} <= team_cols and team_pts_pg is not None:
        try:
            fga = float(trow_dict["fga"])  # type: ignore[arg-type]
            fta = float(trow_dict["fta"])  # type: ignore[arg-type]
        except (TypeError, ValueError):
            fga = fta = 0.0
        denom = 2.0 * (fga + 0.44 * fta)
        if denom > 0 and not math.isnan(team_pts_pg):
            tv = float(team_pts_pg) / denom
            return None if math.isnan(tv) else tv
    p = adv_ts_proxy.get(team_id)
    return p


def team_pts_per_game(team_row: dict[str, Any], team_cols: set[str]) -> float | None:
    ppg = team_row.get("pts_per_game")
    if ppg is not None and not (isinstance(ppg, float) and math.isnan(ppg)):
        return float(ppg)
    pts = team_row.get("pts")
    if pts is None or "pts" not in team_cols:
        return None
    w = team_row.get("wins") or 0
    l = team_row.get("losses") or 0
    g = w + l
    if not g:
        return None
    try:
        return float(pts) / float(g)
    except (TypeError, ValueError):
        return None


def team_net_rating(team_row: dict[str, Any], team_cols: set[str]) -> float | None:
    """Net rating from team_stats, or off_rating - def_rating when net is absent."""
    if "net_rating" in team_cols and team_row.get("net_rating") is not None:
        try:
            v = float(team_row["net_rating"])
            return None if math.isnan(v) else v
        except (TypeError, ValueError):
            pass
    if "off_rating" in team_cols and "def_rating" in team_cols:
        try:
            o = team_row.get("off_rating")
            d = team_row.get("def_rating")
            if o is None or d is None:
                return None
            ort = float(o)
            drt = float(d)
            if math.isnan(ort) or math.isnan(drt):
                return None
            return ort - drt
        except (TypeError, ValueError):
            return None
    return None


def three_pt_rate(team_row: dict[str, Any], team_cols: set[str]) -> float | None:
    if "fg3a" in team_cols and "fga" in team_cols:
        fg3 = team_row.get("fg3a")
        fa = team_row.get("fga")
        try:
            f3 = float(fg3) if fg3 is not None else None
            ftot = float(fa) if fa is not None else None
        except (TypeError, ValueError):
            return None
        if f3 is None or ftot is None or ftot <= 0:
            return None
        return f3 / ftot
    return None


def offensive_rebounding_pg(team_row: dict[str, Any], team_cols: set[str]) -> float | None:
    for col in ("off_reb", "oreb"):
        if col in team_cols:
            v = team_row.get(col)
            try:
                if v is None:
                    return None
                fv = float(v)
                return None if math.isnan(fv) else fv
            except (TypeError, ValueError):
                return None
    return None


def ok_ge(val: float | None, thresh: float) -> bool:
    if val is None:
        return False
    f = float(val)
    return not math.isnan(f) and f >= thresh


def ok_le(val: float | None, upper: float) -> bool:
    """True iff val is a finite float at or below upper (inclusive)."""
    if val is None:
        return False
    try:
        f = float(val)
    except (TypeError, ValueError):
        return False
    return not math.isnan(f) and f <= upper


def ok_strict_lt(val: float | None, upper: float) -> bool:
    """True iff val is a finite float strictly below upper (exclusive)."""
    if val is None:
        return False
    f = float(val)
    if math.isnan(f):
        return False
    return f < upper


def qualifies_perfect_net(net_rating: float | None, thresh: float = 5.0) -> bool:
    if net_rating is None:
        return False
    try:
        n = float(net_rating)
    except (TypeError, ValueError):
        return False
    return not math.isnan(n) and n >= thresh


def resolve_playstyles(
    *,
    net_rating: float | None,
    max_usg: float | None,
    player_ts_pct: float | None,
    three_pt: float | None,
    team_fg3_pct: float | None,
    avg_open_sq: float | None,
    team_ast: float | None,
    off_reb: float | None,
    team_ts_pct_metric: float | None,
    team_tov_pct: float | None,
) -> tuple[str, str, list[str]]:
    matched_styles: list[str] = []

    # Check order: Motion → Pace & Space → Paint & Pound → Heliocentric (primary = first match)
    if ok_ge(avg_open_sq, 0.28) and ok_ge(team_ast, 24.0):
        matched_styles.append("Motion")

    if ok_ge(three_pt, 0.35) and ok_ge(team_fg3_pct, 0.31):
        matched_styles.append("Pace & Space")

    if (
        ok_ge(off_reb, 11.2)
        and ok_ge(team_ts_pct_metric, 0.55)
        and ok_strict_lt(three_pt, 0.35)
    ):
        matched_styles.append("Paint & Pound")

    if ok_ge(max_usg, 0.32) and ok_ge(player_ts_pct, 0.55):
        matched_styles.append("Heliocentric")

    if (
        len(matched_styles) >= 3
        and qualifies_perfect_net(net_rating)
        and ok_le(team_tov_pct, 14.0)
    ):
        return "Perfect", ", ".join(matched_styles), matched_styles

    if len(matched_styles) >= 1:
        primary = matched_styles[0]
        return primary, ", ".join(matched_styles), matched_styles

    return "Undefined / No Identity", "Undefined / No Identity", matched_styles


def fetch_advanced_rollups(con: sqlite3.Connection, season: str) -> dict[int, dict[str, Any]]:
    selects = [
        "team_id",
        "AVG(CASE WHEN open_shot_pct IS NOT NULL THEN open_shot_pct END) AS avg_open",
        (
            "SUM(CASE WHEN fg3a_per100 IS NOT NULL THEN fg3a_per100 ELSE 0 END) "
            "AS sum_fg3_p100"
        ),
        (
            "SUM(CASE WHEN fga_per100 IS NOT NULL AND fga_per100 > 0 "
            "THEN fga_per100 ELSE 0 END) AS sum_fga_p100"
        ),
        (
            "SUM(CASE WHEN off_reb IS NOT NULL THEN off_reb ELSE 0 END) "
            "AS sum_oreb"
        ),
    ]

    sql = f"""
        SELECT {", ".join(selects)}
        FROM player_stats_advanced
        WHERE season = ? AND team_id IS NOT NULL
        GROUP BY team_id
    """
    out: dict[int, dict[str, Any]] = {}
    for row in con.execute(sql, (season,)).fetchall():
        tid = int(row["team_id"])
        out[tid] = {
            "avg_open": row["avg_open"],
            "sum_fg3_p100": row["sum_fg3_p100"],
            "sum_fga_p100": row["sum_fga_p100"],
            "sum_oreb": row["sum_oreb"],
        }
    return out


def print_motion_gap_diagnostics(con: sqlite3.Connection) -> None:
    """
    League-wide maxima for inputs used in Motion (wide-open / assists),
    to debug unreachable thresholds.
    """
    o_row = con.execute(
        """
        SELECT MAX(team_avg_open) AS mx
        FROM (
            SELECT
                AVG(CASE WHEN open_shot_pct IS NOT NULL THEN open_shot_pct END)
                    AS team_avg_open
            FROM player_stats_advanced
            WHERE team_id IS NOT NULL
            GROUP BY season, team_id
        )
        """
    ).fetchone()
    mx_team_avg_open = o_row["mx"] if o_row else None

    raw_row = con.execute(
        """
        SELECT MAX(open_shot_pct) AS mx
        FROM player_stats_advanced
        WHERE open_shot_pct IS NOT NULL
        """
    ).fetchone()
    mx_player_open = raw_row["mx"] if raw_row else None

    a_row = con.execute(
        """
        SELECT MAX(team_ast_pg) AS mx
        FROM (
            SELECT
                SUM(ast * COALESCE(gp, 0)) * 1.0 / NULLIF(MAX(gp), 0)
                    AS team_ast_pg
            FROM player_stats_basic
            WHERE team_id IS NOT NULL AND season IS NOT NULL
            GROUP BY season, team_id
        )
        """
    ).fetchone()
    mx_ast = a_row["mx"] if a_row else None

    ts_cols = pragma_columns(con, "team_stats")
    mx_team_tbl_ast = None
    if "ast" in ts_cols:
        ar = con.execute(
            """
            SELECT MAX(CAST(ast AS REAL)) AS mx
            FROM team_stats WHERE ast IS NOT NULL
            """
        ).fetchone()
        mx_team_tbl_ast = ar["mx"] if ar else None

    print("", flush=True)
    print(
        "[PLAYSTYLE DIAG] Motion-related maxima observed in loaded data:",
        flush=True,
    )
    print(
        f"  MAX(open_shot_pct) across all player_advanced rows ~ {mx_player_open}",
        flush=True,
    )
    print(
        f"  MAX(team roster AVG open_shot_pct), same aggregation as classifier ~ {mx_team_avg_open}",
        flush=True,
    )
    print(
        f"  MAX(team_ast_pg proxy: SUM(ast*gp)/MAX(gp) from player_stats_basic) ~ {mx_ast}",
        flush=True,
    )
    if mx_team_tbl_ast is not None:
        print(
            f"  MAX(team_stats.ast column) ~ {mx_team_tbl_ast}",
            flush=True,
        )
    print(
        "  (Motion rule: roster AVG open_shot_pct >= 0.28 AND team_ast >= 24.0.)",
        flush=True,
    )


def print_top5_teams_by_team_ast(con: sqlite3.Connection) -> None:
    """
    Top 5 team-seasons by team_ast as the classifier resolves it:
    team_stats.ast when present, else SUM(ast*gp)/MAX(gp) from player_stats_basic.
    """
    team_cols = pragma_columns(con, "team_stats")
    if "ast" in team_cols:
        sql = """
            SELECT season, team_abbr, team_ast
            FROM (
                SELECT
                    s.season,
                    s.team_abbr,
                    CASE
                        WHEN s.ast IS NOT NULL THEN CAST(s.ast AS REAL)
                        ELSE (
                            SELECT SUM(b.ast * COALESCE(b.gp, 0)) * 1.0
                                / NULLIF(MAX(b.gp), 0)
                            FROM player_stats_basic AS b
                            WHERE b.season = s.season
                              AND b.team_id = s.team_id
                        )
                    END AS team_ast
                FROM team_stats AS s
            ) AS x
            WHERE x.team_ast IS NOT NULL
            ORDER BY x.team_ast DESC
            LIMIT 5
        """
    else:
        sql = """
            SELECT x.season, s.team_abbr, x.team_ast
            FROM (
                SELECT
                    b.season,
                    b.team_id,
                    SUM(b.ast * COALESCE(b.gp, 0)) * 1.0
                        / NULLIF(MAX(b.gp), 0) AS team_ast
                FROM player_stats_basic AS b
                WHERE b.team_id IS NOT NULL
                  AND b.season IS NOT NULL
                GROUP BY b.season, b.team_id
            ) AS x
            JOIN team_stats AS s
              ON s.season = x.season
             AND s.team_id = x.team_id
            WHERE x.team_ast IS NOT NULL
            ORDER BY x.team_ast DESC
            LIMIT 5
        """
    rows = con.execute(sql).fetchall()
    print("", flush=True)
    print(
        "[PLAYSTYLE] Top 5 team-seasons by team_ast (classifier resolution):",
        flush=True,
    )
    if not rows:
        print("  (no rows)", flush=True)
        return
    for i, row in enumerate(rows, start=1):
        ab = row["team_abbr"] or "?"
        print(
            f"  {i}. {row['season']} {ab} — team_ast={row['team_ast']:.4f}",
            flush=True,
        )


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    ensure_playstyle_table(con)

    team_cols = pragma_columns(con, "team_stats")
    advanced_cols = pragma_columns(con, "player_stats_advanced")
    seasons = [
        row[0]
        for row in con.execute(
            "SELECT DISTINCT season FROM team_stats ORDER BY season"
        ).fetchall()
    ]
    if not seasons:
        print("[PLAYSTYLE] No seasons found in team_stats.", flush=True)
        con.commit()
        con.close()
        return

    missing_team_ast_fg3_ts = (
        "ast" not in team_cols or "fg3_pct" not in team_cols or "ts_pct" not in team_cols
    )
    if missing_team_ast_fg3_ts:
        print(
            "[PLAYSTYLE] Notice: team_stats lacks ast and/or fg3_pct and/or ts_pct - "
            "using GP-weighted/basic and advanced AVG(ts_pct) proxies (read-only).",
            flush=True,
        )

    missing_ts_3pt = not ({"fg3a", "fga"} <= team_cols)
    if missing_ts_3pt:
        print(
            "[PLAYSTYLE] Notice: team_stats missing fg3a/fga - "
            "using SUM(fg3a_per100)/SUM(fga_per100) from "
            "player_stats_advanced per team (proxy).",
            flush=True,
        )
    if not ({*team_cols} & {"off_reb", "oreb"}) and "off_reb" in advanced_cols:
        print(
            "[PLAYSTYLE] Notice: team_stats missing offensive rebounds - "
            "using SUM(off_reb) from player_stats_advanced per team (proxy).",
            flush=True,
        )

    stats = Counter()
    perfect_teams: list[tuple[str, str, str]] = []

    insert_sql = """
        INSERT OR REPLACE INTO team_playstyle_data
            (season, team_id, team_abbr, playstyle, all_playstyles)
        VALUES (?, ?, ?, ?, ?)
    """

    for season in seasons:
        adv_by_team = fetch_advanced_rollups(con, season)
        basic_team_proxies = fetch_basic_team_proxies(con, season)
        basic_tov_proxy = fetch_team_tov_proxy(con, season)
        adv_ts_by_team = fetch_advanced_ts_proxy(con, season)

        teams = con.execute(
            "SELECT * FROM team_stats WHERE season = ? ORDER BY team_abbr",
            (season,),
        ).fetchall()

        for trow in teams:
            tid = int(trow["team_id"])
            team_abbr = trow["team_abbr"] or ""
            tr = dict(trow)

            oreb_pg = offensive_rebounding_pg(tr, team_cols)
            t3 = three_pt_rate(tr, team_cols)

            avg_open = None
            adv = adv_by_team.get(tid)
            bp = basic_team_proxies.get(tid)
            if adv:
                avg_open = adv["avg_open"]
                sf3 = adv["sum_fg3_p100"] or 0.0
                sfa = adv["sum_fga_p100"] or 0.0
                so = adv["sum_oreb"]
                if oreb_pg is None and so is not None:
                    try:
                        oreb_pg = float(so)
                    except (TypeError, ValueError):
                        oreb_pg = None
                if t3 is None and sfa > 0:
                    t3 = float(sf3) / float(sfa)

            max_usg, player_ts = max_usage_and_player_ts_advanced(con, season, tid)
            team_ast = team_assists_pg(tr, team_cols, bp, tid)
            team_fg3 = team_fg3_pct_value(tr, team_cols, bp, tid)

            t_pts = team_pts_per_game(tr, team_cols)
            team_ts_m = team_ts_pct_value(tr, team_cols, adv_ts_by_team, tid, t_pts)
            net_rtg = team_net_rating(tr, team_cols)
            team_tov = team_tov_pct_value(tr, team_cols, basic_tov_proxy, tid)

            if (
                ok_ge(oreb_pg, 11.2)
                and ok_ge(team_ts_m, 0.55)
                and t3 is not None
                and not math.isnan(t3)
                and t3 >= 0.35
            ):
                ab = team_abbr if team_abbr else f"team_id={tid}"
                rr = round(float(t3), 4)
                print(
                    f"[STYLE STRIPPED] {ab} ({season}) - High OffReb but 3PT Rate "
                    f"{rr} >= 0.35 (Paint & Pound cap).",
                    flush=True,
                )

            primary, all_styles, dual_list = resolve_playstyles(
                net_rating=net_rtg,
                max_usg=max_usg,
                player_ts_pct=player_ts,
                three_pt=t3,
                team_fg3_pct=team_fg3,
                avg_open_sq=avg_open,
                team_ast=team_ast,
                off_reb=oreb_pg,
                team_ts_pct_metric=team_ts_m,
                team_tov_pct=team_tov,
            )

            if len(dual_list) >= 3 and not (
                qualifies_perfect_net(net_rtg) and ok_le(team_tov, 14.0)
            ):
                ab = team_abbr if team_abbr else f"team_id={tid}"
                if not qualifies_perfect_net(net_rtg):
                    if net_rtg is None:
                        nr_str = "None"
                    else:
                        try:
                            nf = float(net_rtg)
                            nr_str = "NaN" if math.isnan(nf) else str(round(nf, 2))
                        except (TypeError, ValueError):
                            nr_str = str(net_rtg)
                    print(
                        f"[PERFECTION DENIED] {ab} - Hit {len(dual_list)} styles but "
                        f"Net Rating {nr_str} < 5.0. Assigned to {dual_list[0]}.",
                        flush=True,
                    )
                else:
                    tv_str = (
                        f"{float(team_tov):.4f}"
                        if team_tov is not None
                        and not (isinstance(team_tov, float) and math.isnan(team_tov))
                        else "None"
                    )
                    print(
                        f"[PERFECTION DENIED] {ab} - Hit {len(dual_list)} styles, "
                        f"Net OK, but team TOV metric {tv_str} > 14.0. "
                        f"Assigned to {dual_list[0]}.",
                        flush=True,
                    )

            stats[primary] += 1

            if primary == "Perfect":
                who_p = team_abbr if team_abbr else f"team_id={tid}"
                print(
                    f"[CELEBRATION] {season} {who_p} has achieved PERFECTION "
                    f"with {all_styles}!",
                    flush=True,
                )
                perfect_teams.append((season, who_p, all_styles))

            if "Heliocentric" in dual_list:
                who_h = team_abbr if team_abbr else f"team_id={tid}"
                mx_disp = round(max_usg, 2) if max_usg is not None else None
                print(
                    f"[HELIOCENTRIC FOUND] {season} {who_h} - Max Usage: {mx_disp}",
                    flush=True,
                )

            con.execute(
                insert_sql,
                (season, tid, team_abbr, primary, all_styles),
            )
            who = team_abbr if team_abbr else f"team_id={tid}"
            print(
                f"[PLAYSTYLE] {season} {who} - Primary: {primary} | All: {all_styles}",
                flush=True,
            )

        con.commit()
        print(f"[PLAYSTYLE] Committed classifications for season {season}.", flush=True)

    ordered_keys = (
        "Perfect",
        "Heliocentric",
        "Pace & Space",
        "Motion",
        "Paint & Pound",
        "Elite Balanced",
        "Undefined / No Identity",
    )
    print("", flush=True)
    print("[PLAYSTYLE SUMMARY] Primary tier counts across all classifications:", flush=True)
    grand = sum(stats.values())
    for label in ordered_keys:
        cnt = stats.get(label, 0)
        pct = round(100.0 * cnt / grand, 1) if grand else 0.0
        print(f"  • {label}: {cnt} ({pct}% of rows)", flush=True)
    leftovers = [(k, v) for k, v in stats.items() if k not in ordered_keys]
    for k, v in sorted(leftovers):
        print(f"  • {k}: {v}", flush=True)
    print(f"  ─ Total classified teams: {grand}", flush=True)

    print("", flush=True)
    print(
        "[PLAYSTYLE] Perfect tier (3+ styles, Net >= 5.0, TOV metric <= 14.0) — all teams:",
        flush=True,
    )
    if not perfect_teams:
        print("  (none)", flush=True)
    else:
        for se, ab, styles in perfect_teams:
            print(f"  • {se} {ab} — {styles}", flush=True)
        print("", flush=True)
        print(
            "[PLAYSTYLE] Perfect NET 5.0+ — celebration roll call:",
            flush=True,
        )
        for se, ab, _sty in perfect_teams:
            print(f"  [CELEBRATION] {se} {ab}", flush=True)

    if stats.get("Motion", 0) == 0:
        print_top5_teams_by_team_ast(con)
    if stats.get("Motion", 0) == 0 or stats.get("Perfect", 0) == 0:
        print_motion_gap_diagnostics(con)

    con.close()
    print(f"[PLAYSTYLE] Completed. Database: {DB_PATH}", flush=True)


if __name__ == "__main__":
    main()
