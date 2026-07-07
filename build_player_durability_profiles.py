"""
build_player_durability_profiles.py
-----------------------------------
Builds ``player_durability_profiles`` in nba_data.db from regular-season and
playoff games played, using experience tiers from ``ultimate_playoff_pr``.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from typing import Any

import pandas as pd

from season_utils import SeasonPair, parse_cli_seasons, prior_source_season

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

AGG_TEAM_ABBRS = frozenset({"TOT", "2TM", "3TM", "4TM"})


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


def rs_season_gp(rows: pd.DataFrame) -> int:
    """True season GP: prefer aggregate (TOT) row when present, else sum stints."""
    if rows.empty:
        return 0
    agg_mask = rows.apply(
        lambda r: is_aggregate_row(r.get("team_abbr"), r.get("team_id")), axis=1
    )
    if agg_mask.any():
        return max(_gint(g) for g in rows.loc[agg_mask, "gp"])
    return sum(_gint(g) for g in rows["gp"])


def playoff_primary_stint(rows: pd.DataFrame) -> tuple[str, int] | None:
    """
    For playoffs, return (team_abbr, gp) for the non-aggregate stint with the
    most games (tie-break: total_minutes). If only aggregate rows exist, None.
    """
    if rows.empty:
        return None
    non = rows[
        ~rows.apply(
            lambda r: is_aggregate_row(r.get("team_abbr"), r.get("team_id")),
            axis=1,
        )
    ]
    if non.empty:
        return None
    non = non.copy()
    non["_gp"] = non["gp"].map(_gint)
    tmins = "total_minutes" if "total_minutes" in non.columns else None
    if tmins:
        non["_tm"] = non[tmins].map(_gint)
    else:
        non["_tm"] = 0
    best = non.sort_values(["_gp", "_tm"], ascending=False).iloc[0]
    ta = best.get("team_abbr")
    if ta is None or str(ta).strip() == "":
        return None
    return str(ta).strip(), _gint(best.get("gp"))


def clamp_rs(x: float) -> float:
    return max(0.40, min(1.00, float(x)))


def experience_tier(experience: str | None) -> str:
    """Map ``ultimate_playoff_pr.experience_level`` to rookie | sophomore | veteran (case-insensitive)."""
    t = (experience or "Veteran").strip().casefold()
    if t == "rookie":
        return "rookie"
    if t == "sophomore":
        return "sophomore"
    return "veteran"


def compute_rs_durability(
    experience: str | None,
    gp_prior: int,
    gp_source: int,
    missing_both_seasons: bool,
) -> float:
    tier = experience_tier(experience)
    if missing_both_seasons:
        if tier == "rookie":
            return clamp_rs(0.70)
        return 0.75
    if tier == "rookie":
        return clamp_rs(0.70)
    if tier == "sophomore":
        # Second-year players: use source-season GP only (relative to target).
        return clamp_rs(gp_source / 82.0)
    total = gp_prior + gp_source
    if gp_prior > 0 and gp_source > 0:
        return clamp_rs(total / 164.0)
    if gp_prior > 0 or gp_source > 0:
        # One season of GP (injury, overseas, missed year, etc.): full-season scale.
        return clamp_rs(total / 82.0)
    return clamp_rs(0.0)


def main(source_season: str | None = None, target_season: str | None = None) -> None:
    if source_season is None or target_season is None:
        pair: SeasonPair = parse_cli_seasons()
        source_season = pair.source
        target_season = pair.target

    prior_source = prior_source_season(source_season)
    season_pair = (prior_source, source_season)

    con = sqlite3.connect(DB_PATH)
    try:
        players = pd.read_sql_query(
            """
            SELECT player_name, experience_level
            FROM ultimate_playoff_pr
            WHERE season = ?;
            """,
            con,
            params=(target_season,),
        )
        basic = pd.read_sql_query(
            """
            SELECT season, player_id, player_name, team_id, team_abbr, gp, total_minutes
            FROM player_stats_basic
            WHERE season IN (?, ?);
            """,
            con,
            params=season_pair,
        )
        po = pd.read_sql_query(
            """
            SELECT season, player_id, player_name, team_id, team_abbr, gp, total_minutes
            FROM player_stats_basic_playoffs
            WHERE season IN (?, ?);
            """,
            con,
            params=season_pair,
        )
    finally:
        con.close()

    # --- Team playoff denominators: max GP on real teams ---
    po_real = po[
        ~po.apply(
            lambda r: is_aggregate_row(r.get("team_abbr"), r.get("team_id")), axis=1
        )
        & po["team_abbr"].notna()
        & (po["team_abbr"].astype(str).str.strip() != "")
    ].copy()
    po_real["_gp"] = po_real["gp"].map(_gint)
    team_denoms = (
        po_real.groupby(["season", "team_abbr"], as_index=False)["_gp"]
        .max()
        .rename(columns={"_gp": "team_po_gp"})
    )

    # --- Regular-season GP per (player_name, season) ---
    rs_gps: dict[tuple[str, str], int] = {}
    for (pname, season), grp in basic.groupby(["player_name", "season"]):
        rs_gps[(str(pname), str(season))] = rs_season_gp(grp)

    names = players["player_name"].astype(str)
    rs_rows = []
    for pname in names:
        g_prior = rs_gps.get((pname, prior_source), 0)
        g_source = rs_gps.get((pname, source_season), 0)
        miss = (pname, prior_source) not in rs_gps and (pname, source_season) not in rs_gps
        exp = players.loc[players["player_name"] == pname, "experience_level"].iloc[0]
        rs_rows.append(
            {
                "player_name": pname,
                "experience_level": exp,
                "rs_durability": compute_rs_durability(exp, g_prior, g_source, miss),
            }
        )
    rs_df = pd.DataFrame(rs_rows)
    rs_df.drop(columns=["experience_level"], inplace=True, errors="ignore")

    # --- Playoff ratios per player ---
    ratio_by_name: dict[str, list[float]] = {}
    for (pname, season), grp in po.groupby(["player_name", "season"]):
        stint = playoff_primary_stint(grp)
        if stint is None:
            continue
        team, pgp = stint
        if pgp <= 0:
            continue
        row = team_denoms[
            (team_denoms["season"] == season) & (team_denoms["team_abbr"] == team)
        ]
        if row.empty:
            continue
        denom = int(row["team_po_gp"].iloc[0])
        if denom <= 0:
            continue
        ratio_by_name.setdefault(str(pname), []).append(min(1.0, pgp / denom))

    def po_score(name: str) -> float:
        ratios = ratio_by_name.get(name, [])
        if not ratios:
            return 0.90
        return min(1.0, float(sum(ratios) / len(ratios)))

    out = rs_df.copy()
    out["po_durability"] = out["player_name"].map(po_score)
    out.insert(0, "season", target_season)

    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS player_durability_profiles (
                season        TEXT NOT NULL,
                player_name   TEXT NOT NULL,
                rs_durability REAL NOT NULL,
                po_durability REAL NOT NULL,
                PRIMARY KEY (player_name, season)
            );
            """
        )
        con.execute(
            "DELETE FROM player_durability_profiles WHERE season = ?;",
            (target_season,),
        )
        out.to_sql("player_durability_profiles", con, index=False, if_exists="append")
        con.commit()
    finally:
        con.close()

    # --- Console output ---
    verify_name = "Stephon Castle"
    castle = out.loc[out["player_name"] == verify_name]
    if castle.empty:
        print(f"{verify_name!r}: not found in player_durability_profiles.")
    else:
        r = castle.iloc[0]
        g_source = rs_gps.get((verify_name, source_season), 0)
        g_prior = rs_gps.get((verify_name, prior_source), 0)
        tier = experience_tier(
            players.loc[players["player_name"] == verify_name, "experience_level"].iloc[0]
        )
        print(
            f"{verify_name}: tier={tier}  GP({prior_source})={g_prior}  "
            f"GP({source_season})={g_source}"
        )
        print(f"  rs_durability={r['rs_durability']:.4f}   po_durability={r['po_durability']:.4f}")
        print(f"  check: clamp(GP_{source_season} / 82) = {clamp_rs(g_source / 82.0):.4f}")

    merged = players.merge(out, on="player_name", how="inner")
    soph = merged[
        merged["experience_level"].astype(str).str.strip().str.casefold() == "sophomore"
    ].copy()
    soph["gp_source"] = soph["player_name"].map(
        lambda n: rs_gps.get((str(n), source_season), 0)
    )
    soph = soph.sort_values(["rs_durability", "player_name"], ascending=[False, True]).head(5)

    print(
        f"\nTop 5 Sophomores (rs_durability; source season {source_season!r} GP / 82 only):"
    )
    if soph.empty:
        print("  (none: no players with experience_level = Sophomore in ultimate_playoff_pr.)")
    else:
        for _, r in soph.iterrows():
            gp_s = int(r["gp_source"])
            expect = clamp_rs(gp_s / 82.0)
            match = abs(float(r["rs_durability"]) - expect) < 1e-6
            print(
                f"  {r['player_name']:<26} GP={gp_s:>3}  gp/82={gp_s/82:.4f}  "
                f"rs={r['rs_durability']:.4f}  matches_gp_over_82={match}"
            )


if __name__ == "__main__":
    main()
