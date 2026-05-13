"""
calculate_mvp_potential.py
--------------------------
Maintains player_special_effects: MVP + alien + efficiency_god + defense_effect +
board_effect (defense/board excluded when alien_effect applies).

Bonuses stack on base_score: Alien +12, efficiency_god +8, defense +1.5, board +1.0.

Does not modify any table except player_special_effects.
"""

from __future__ import annotations

import math
import os
import sqlite3
import sys
from collections import defaultdict
from typing import Any

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

TABLE = "player_special_effects"
MIN_GP_MVP = 55
MIN_MINUTES_MVP = 30.0
MIN_GP_ALIEN = 40
MIN_MINUTES_ALIEN = 28.0
ALIEN_MIN_PTS = 22.0
ALIEN_MIN_STOCKS = 3.4
TOP_N = 5
REB_MVP_WEIGHT = 0.6
TS_LEAGUE_AVG = 0.55
ALIEN_MVP_BONUS = 12.0
MIN_GP_EFF_GOD = 40
MIN_MINUTES_EFF_GOD = 30.0
EFF_GOD_MIN_PTS = 22.0
EFF_GUARD_WING_TS = 0.61
EFF_BIG_TS = 0.65
EFFICIENCY_GOD_BONUS = 8.0
MIN_GP_DEF_BOARD = 40
MIN_MINUTES_DEFENSE = 28.0
MIN_MINUTES_DEFENSE_ELITE_DR = 25.0
MIN_MINUTES_BOARD = 25.0
DEFENSE_MIN_STOCKS = 2.5
BOARD_MIN_TRB = 11.5
BOARD_MIN_OREB = 3.5
DEFENSE_MVP_BONUS = 1.5
BOARD_MVP_BONUS = 1.0

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def pragma_columns(con: sqlite3.Connection, table: str) -> set[str]:
    cur = con.execute(f"PRAGMA table_info({table})")
    return {r[1] for r in cur.fetchall()}


def _add_text_column(con: sqlite3.Connection, name: str, default: str = "No") -> None:
    cols = pragma_columns(con, TABLE)
    if name in cols:
        return
    try:
        con.execute(
            f"ALTER TABLE {TABLE} ADD COLUMN {name} TEXT DEFAULT '{default}';"
        )
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        if "duplicate column name" in msg or "already exists" in msg:
            return
        raise
    con.commit()


def migrate_jordan_to_alien(con: sqlite3.Connection) -> None:
    """Rename jordan_effect -> alien_effect, or add alien_effect if rename unsupported."""
    cols = pragma_columns(con, TABLE)
    if "alien_effect" in cols:
        return
    if "jordan_effect" in cols:
        try:
            con.execute(
                f"ALTER TABLE {TABLE} RENAME COLUMN jordan_effect TO alien_effect;"
            )
            con.commit()
            return
        except sqlite3.OperationalError:
            pass
    _add_text_column(con, "alien_effect")


def ensure_effect_columns(con: sqlite3.Connection) -> None:
    _add_text_column(con, "mvp_potential")
    migrate_jordan_to_alien(con)
    _add_text_column(con, "efficiency_god")
    _add_text_column(con, "defense_effect")
    _add_text_column(con, "board_effect")


def ffloat(v: Any) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
        return None if math.isnan(x) else x
    except (TypeError, ValueError):
        return None


def minutes_column_expr(basic_cols: set[str]) -> str:
    if "min" in basic_cols:
        return "b.min AS minutes_pg"
    if "mp" in basic_cols:
        return "b.mp AS minutes_pg"
    return "b.mpg AS minutes_pg"


def rebounds_column_expr(basic_cols: set[str]) -> str:
    if "trb" in basic_cols:
        return "b.trb AS reb_pg"
    return "b.reb AS reb_pg"


def col_or_null(tbl: str, col: str, alias: str, available: set[str]) -> str:
    if col in available:
        return f"{tbl}.{col} AS {alias}"
    return f"NULL AS {alias}"


def load_stints(con: sqlite3.Connection) -> list[dict[str, Any]]:
    basic_cols = pragma_columns(con, "player_stats_basic")
    min_e = minutes_column_expr(basic_cols)
    reb_e = rebounds_column_expr(basic_cols)
    stl_e = col_or_null("b", "stl", "stl", basic_cols)
    blk_e = col_or_null("b", "blk", "blk", basic_cols)
    oreb_e = col_or_null("b", "off_reb", "off_reb_pg", basic_cols)
    pos_e = col_or_null("b", "position", "position", basic_cols)
    sql = f"""
      SELECT
        b.player_name AS player_name,
        b.season AS season,
        b.gp AS gp,
        {min_e},
        b.pts AS pts,
        b.ast AS ast,
        {reb_e},
        {oreb_e},
        {stl_e},
        {blk_e},
        {pos_e},
        a.ts_pct AS ts_pct,
        a.def_rating AS def_rating
      FROM player_stats_basic AS b
      INNER JOIN player_stats_advanced AS a
        ON a.season = b.season
       AND a.player_id = b.player_id
       AND a.team_id = b.team_id
    """
    cur = con.execute(sql)
    return [dict(r) for r in cur.fetchall()]


def stint_row(row: dict[str, Any]) -> dict[str, Any] | None:
    w = row.get("gp")
    w = int(w) if w is not None else 0
    if w <= 0:
        return None
    return {
        "w": float(w),
        "minutes_pg": ffloat(row.get("minutes_pg")),
        "pts": ffloat(row.get("pts")),
        "ast": ffloat(row.get("ast")),
        "reb": ffloat(row.get("reb_pg")),
        "off_reb": ffloat(row.get("off_reb_pg")),
        "stl": ffloat(row.get("stl")),
        "blk": ffloat(row.get("blk")),
        "position": row.get("position"),
        "ts_pct": ffloat(row.get("ts_pct")),
        "def_rating": ffloat(row.get("def_rating")),
    }


def season_aggregate(stints: list[dict[str, Any]]) -> dict[str, Any] | None:
    sum_w = sum(s["w"] for s in stints)
    if sum_w <= 0:
        return None

    def wavg(key: str) -> float | None:
        num = 0.0
        den = 0.0
        for s in stints:
            v = s.get(key)
            if v is None:
                continue
            num += v * s["w"]
            den += s["w"]
        if den <= 0:
            return None
        return num / den

    return {
        "sum_w": sum_w,
        "pts_bar": wavg("pts"),
        "ast_bar": wavg("ast"),
        "reb_bar": wavg("reb"),
        "min_bar": wavg("minutes_pg"),
        "ts_bar": wavg("ts_pct"),
        "stl_bar": wavg("stl"),
        "blk_bar": wavg("blk"),
        "off_reb_bar": wavg("off_reb"),
        "def_rating_bar": wavg("def_rating"),
    }


def dominant_position(stints: list[dict[str, Any]]) -> str | None:
    best_w = -1.0
    best: str | None = None
    for s in stints:
        raw = s.get("position")
        if raw is None or str(raw).strip() == "":
            continue
        w = s["w"]
        if w > best_w:
            best_w = w
            best = str(raw).strip()
    return best


def _eff_god_big_and_perimeter(pos_raw: str) -> tuple[bool, bool]:
    """Returns (is_big, is_guard_wing) for positional TS thresholds."""
    u = pos_raw.upper().strip()
    segments = [p.strip() for p in u.replace("/", "-").split("-") if p.strip()]
    has_pure_c = any(seg == "C" for seg in segments)
    is_big = "PF" in u or has_pure_c
    is_guard_wing = any(k in u for k in ("PG", "SG", "SF"))
    return is_big, is_guard_wing


def efficiency_god_eligible(agg: dict[str, Any], pos_raw: str | None) -> bool:
    if pos_raw is None or str(pos_raw).strip() == "":
        return False
    if agg["sum_w"] < MIN_GP_EFF_GOD:
        return False
    mb = agg["min_bar"]
    if mb is None or mb < MIN_MINUTES_EFF_GOD:
        return False
    pts = agg["pts_bar"]
    if pts is None or pts < EFF_GOD_MIN_PTS:
        return False
    ts = agg["ts_bar"]
    if ts is None:
        return False
    is_big, is_gw = _eff_god_big_and_perimeter(str(pos_raw))
    if is_big:
        return ts >= EFF_BIG_TS
    if is_gw:
        return ts >= EFF_GUARD_WING_TS
    return False


def alien_eligible(agg: dict[str, Any]) -> bool:
    if agg["sum_w"] < MIN_GP_ALIEN:
        return False
    mb = agg["min_bar"]
    if mb is None or mb < MIN_MINUTES_ALIEN:
        return False
    pts = agg["pts_bar"]
    if pts is None or pts < ALIEN_MIN_PTS:
        return False
    stl_b = agg["stl_bar"]
    blk_b = agg["blk_bar"]
    if stl_b is None or blk_b is None:
        return False
    return (stl_b + blk_b) >= ALIEN_MIN_STOCKS


def defense_effect_eligible(agg: dict[str, Any]) -> bool:
    if alien_eligible(agg):
        return False
    if agg["sum_w"] < MIN_GP_DEF_BOARD:
        return False
    mb = agg["min_bar"]
    if mb is None or mb < MIN_MINUTES_DEFENSE_ELITE_DR:
        return False
    stl_b = agg["stl_bar"]
    blk_b = agg["blk_bar"]
    stocks_path = (
        mb >= MIN_MINUTES_DEFENSE
        and stl_b is not None
        and blk_b is not None
        and (stl_b + blk_b) >= DEFENSE_MIN_STOCKS
    )
    elite_dr_path = agg.get("def_rating_bar") is not None and agg["def_rating_bar"] <= 110.0
    return stocks_path or elite_dr_path


def board_effect_eligible(agg: dict[str, Any]) -> bool:
    if alien_eligible(agg):
        return False
    if agg["sum_w"] < MIN_GP_DEF_BOARD:
        return False
    mb = agg["min_bar"]
    if mb is None or mb < MIN_MINUTES_BOARD:
        return False
    trb = agg["reb_bar"]
    oreb = agg.get("off_reb_bar")
    trb_ok = trb is not None and trb >= BOARD_MIN_TRB
    oreb_ok = oreb is not None and oreb >= BOARD_MIN_OREB
    return trb_ok or oreb_ok


def mvp_volume_ok(agg: dict[str, Any]) -> bool:
    if agg["sum_w"] < MIN_GP_MVP:
        return False
    if alien_eligible(agg):
        return True
    mb = agg["min_bar"]
    return mb is not None and mb >= MIN_MINUTES_MVP


def compute_base_score(agg: dict[str, Any]) -> float | None:
    p, a, r, ts = agg["pts_bar"], agg["ast_bar"], agg["reb_bar"], agg["ts_bar"]
    if p is None or a is None or r is None or ts is None:
        return None
    if TS_LEAGUE_AVG <= 0:
        return None
    return (p * (ts / TS_LEAGUE_AVG)) + (a * 1.5) + (r * REB_MVP_WEIGHT)


def compute_mvp_score(agg: dict[str, Any], pos_raw: str | None) -> float | None:
    if not mvp_volume_ok(agg):
        return None
    base = compute_base_score(agg)
    if base is None:
        return None
    total = base
    if alien_eligible(agg):
        total += ALIEN_MVP_BONUS
    if efficiency_god_eligible(agg, pos_raw):
        total += EFFICIENCY_GOD_BONUS
    if defense_effect_eligible(agg):
        total += DEFENSE_MVP_BONUS
    if board_effect_eligible(agg):
        total += BOARD_MVP_BONUS
    return total


def reset_flag_columns(cur: sqlite3.Cursor) -> None:
    parts = ["mvp_potential = 'No'"]
    cols = pragma_columns(cur.connection, TABLE)
    if "alien_effect" in cols:
        parts.append("alien_effect = 'No'")
    if "jordan_effect" in cols:
        parts.append("jordan_effect = 'No'")
    if "efficiency_god" in cols:
        parts.append("efficiency_god = 'No'")
    if "defense_effect" in cols:
        parts.append("defense_effect = 'No'")
    if "board_effect" in cols:
        parts.append("board_effect = 'No'")
    cur.execute(f"UPDATE {TABLE} SET {', '.join(parts)}")


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        ensure_effect_columns(con)

        stints_raw = load_stints(con)
        by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in stints_raw:
            name, season = row.get("player_name"), row.get("season")
            if not name or not season:
                continue
            key = (str(name).strip(), str(season).strip())
            s = stint_row(row)
            if s is not None:
                by_key[key].append(s)

        aggs: dict[tuple[str, str], dict[str, Any]] = {}
        dom_pos: dict[tuple[str, str], str | None] = {}
        for key, slist in by_key.items():
            agg = season_aggregate(slist)
            if agg is None:
                continue
            aggs[key] = agg
            dom_pos[key] = dominant_position(slist)

        alien_pairs: list[tuple[str, str]] = [
            (name, season)
            for (name, season), agg in aggs.items()
            if alien_eligible(agg)
        ]

        defense_pairs: list[tuple[str, str]] = [
            (name, season)
            for (name, season), agg in aggs.items()
            if defense_effect_eligible(agg)
        ]
        board_pairs: list[tuple[str, str]] = [
            (name, season)
            for (name, season), agg in aggs.items()
            if board_effect_eligible(agg)
        ]

        eff_god_rows: list[tuple[str, str, str, float | None]] = []
        for key, agg in aggs.items():
            name, season = key
            pr = dom_pos.get(key)
            if efficiency_god_eligible(agg, pr):
                eff_god_rows.append((name, season, pr or "?", agg["ts_bar"]))

        candidate_rows: list[tuple[str, str, float]] = []
        for key, agg in aggs.items():
            name, season = key
            sc = compute_mvp_score(agg, dom_pos.get(key))
            if sc is None:
                continue
            candidate_rows.append((name, season, sc))

        by_season: dict[str, list[tuple[str, str, float]]] = defaultdict(list)
        for name, season, sc in candidate_rows:
            by_season[season].append((name, season, sc))

        top_mvp_keys: set[tuple[str, str]] = set()
        for season, lst in by_season.items():
            lst.sort(key=lambda x: x[2], reverse=True)
            for name, sea, _sc in lst[:TOP_N]:
                top_mvp_keys.add((name, sea))

        cur = con.cursor()
        reset_flag_columns(cur)

        for name, season in sorted(alien_pairs):
            cur.execute(
                f"""
                UPDATE {TABLE}
                SET alien_effect = 'Yes'
                WHERE player_name = ? AND season = ?
                """,
                (name, season),
            )

        for name, season, _, _ in sorted(eff_god_rows, key=lambda x: (x[1], x[0])):
            cur.execute(
                f"""
                UPDATE {TABLE}
                SET efficiency_god = 'Yes'
                WHERE player_name = ? AND season = ?
                """,
                (name, season),
            )

        for name, season in sorted(defense_pairs, key=lambda x: (x[1], x[0])):
            cur.execute(
                f"""
                UPDATE {TABLE}
                SET defense_effect = 'Yes'
                WHERE player_name = ? AND season = ?
                """,
                (name, season),
            )

        for name, season in sorted(board_pairs, key=lambda x: (x[1], x[0])):
            cur.execute(
                f"""
                UPDATE {TABLE}
                SET board_effect = 'Yes'
                WHERE player_name = ? AND season = ?
                """,
                (name, season),
            )

        for name, season in top_mvp_keys:
            cur.execute(
                f"""
                UPDATE {TABLE}
                SET mvp_potential = 'Yes'
                WHERE player_name = ? AND season = ?
                """,
                (name, season),
            )

        con.commit()

        print(
            f"MVP Potential: top {TOP_N} per season "
            f"(gp>={MIN_GP_MVP}, minutes/game>={MIN_MINUTES_MVP} "
            f"unless Alien-qualified); "
            f"stacking bonuses: Alien +{ALIEN_MVP_BONUS}, "
            f"Efficiency god +{EFFICIENCY_GOD_BONUS}, "
            f"Defense +{DEFENSE_MVP_BONUS}, Board +{BOARD_MVP_BONUS}.",
            flush=True,
        )
        print(
            f"  Alien: gp>={MIN_GP_ALIEN}, min>={MIN_MINUTES_ALIEN}, "
            f"pts>={ALIEN_MIN_PTS}, stocks>={ALIEN_MIN_STOCKS}.",
            flush=True,
        )
        print(
            f"  Efficiency god: gp>={MIN_GP_EFF_GOD}, min>={MIN_MINUTES_EFF_GOD}, "
            f"pts>={EFF_GOD_MIN_PTS}; PG/SG/SF ts>={EFF_GUARD_WING_TS}, "
            f"PF/C ts>={EFF_BIG_TS}.",
            flush=True,
        )
        print(
            f"  Defense / Board: not Alien; def gp>={MIN_GP_DEF_BOARD}, "
            f"min>={MIN_MINUTES_DEFENSE} (stocks) / "
            f">={MIN_MINUTES_DEFENSE_ELITE_DR} (def_rating), stocks>={DEFENSE_MIN_STOCKS}; "
            f"board min>={MIN_MINUTES_BOARD}, "
            f"trb>={BOARD_MIN_TRB} or oreb>={BOARD_MIN_OREB}.",
            flush=True,
        )
        print(f"Seasons processed: {len(by_season)}.", flush=True)

        if by_season:
            latest = sorted(by_season.keys())[-1]
            tier = sorted(by_season[latest], key=lambda x: x[2], reverse=True)[
                :TOP_N
            ]
            print(
                f"\nMost recent season ({latest}) — top {TOP_N} by mvp_score:",
                flush=True,
            )
            for rank, (name, sea, sc) in enumerate(tier, 1):
                print(f"  {rank}. {name} — mvp_score={sc:.4f}", flush=True)
        else:
            print("\n(No MVP candidates with current filters.)", flush=True)

        a_print = sorted(alien_pairs, key=lambda x: (x[1], x[0]))
        print("\nAlien effect (full list of qualifying player-seasons):", flush=True)
        if not a_print:
            print("  (none)", flush=True)
        else:
            for name, season in a_print:
                print(f"  {season} — {name}", flush=True)

        print("\nEfficiency god (position & season-weighted ts_pct):", flush=True)
        if not eff_god_rows:
            print("  (none)", flush=True)
        else:
            for name, season, pos, tsb in sorted(
                eff_god_rows, key=lambda x: (x[1], x[0])
            ):
                ts_str = f"{tsb:.4f}" if tsb is not None else "n/a"
                print(
                    f"  {season} — {name} — position={pos} — ts_pct={ts_str}",
                    flush=True,
                )

        d_sample = sorted(defense_pairs, key=lambda x: (x[1], x[0]))[:20]
        print("\nDefense effect (sample up to 20; Alien excluded):", flush=True)
        if not defense_pairs:
            print("  (none)", flush=True)
        else:
            for name, season in d_sample:
                print(f"  {season} — {name}", flush=True)
            if len(defense_pairs) > len(d_sample):
                print(f"  … +{len(defense_pairs) - len(d_sample)} more", flush=True)

        b_sample = sorted(board_pairs, key=lambda x: (x[1], x[0]))[:20]
        print("\nBoard effect (sample up to 20; Alien excluded):", flush=True)
        if not board_pairs:
            print("  (none)", flush=True)
        else:
            for name, season in b_sample:
                print(f"  {season} — {name}", flush=True)
            if len(board_pairs) > len(b_sample):
                print(f"  … +{len(board_pairs) - len(b_sample)} more", flush=True)

        barnes = [
            (n, s)
            for n, s in defense_pairs
            if "Barnes" in n and "Scottie" in n
        ]
        if barnes:
            print("\nScottie Barnes defense_effect:", flush=True)
            for n, s in sorted(barnes, key=lambda x: (x[1], x[0])):
                print(f"  {s} — {n} — defense_effect=Yes", flush=True)
        else:
            print(
                "\nScottie Barnes: no defense_effect row (check minutes/stocks/gp).",
                flush=True,
            )

    finally:
        con.close()


if __name__ == "__main__":
    main()
