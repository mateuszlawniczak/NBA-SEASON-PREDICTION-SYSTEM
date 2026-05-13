"""
calculate_playoff_riser_choker.py
---------------------------------
3-year window (2022-23, 2023-24, 2024-25): fetch LeagueDashPlayerStats
Advanced Totals for Regular Season and Playoffs, aggregate per specification,
classify Riser / Choker / Neutral, and replace playoff_riser_choker in
nba_data.db with auditable weighted metrics.

NBA Advanced Totals store per-game minutes in the MIN column per row (not stint
total minutes). Step~2 aggregates therefore use ``row_minutes = GP * MIN``
everywhere MIN drives a weighted sum or ``total_min`` / ``ply_mpg``, matching
audit-grade floor time across the concatenated slices.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time

import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import LeagueDashPlayerStats

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")
SEASONS = ["2022-23", "2023-24", "2024-25"]
SEASON_TYPE_REG = "Regular Season"
SEASON_TYPE_PO = "Playoffs"
PAUSE_S = 1.5
TIMEOUT = 90

MIN_COL = "MIN"
GP_COL = "GP"
USG_COL = "USG_PCT"
TS_COL = "TS_PCT"
NAME_COL = "PLAYER_NAME"

OUT_COLUMNS = [
    "player_name",
    "tag",
    "ply_total_gp",
    "reg_usg",
    "ply_usg",
    "delta_usg",
    "reg_ts",
    "ply_ts",
    "delta_ts",
]

ROUND_FLOATS = [
    "ply_total_gp",
    "reg_usg",
    "ply_usg",
    "delta_usg",
    "reg_ts",
    "ply_ts",
    "delta_ts",
]


def fetch_advanced_totals(season: str, season_type: str) -> pd.DataFrame:
    print(f"  -> {season} [{season_type}] Advanced Totals ...", flush=True)
    r = LeagueDashPlayerStats(
        season=season,
        per_mode_detailed="Totals",
        measure_type_detailed_defense="Advanced",
        season_type_all_star=season_type,
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    print(f"     rows={len(df)}", flush=True)
    return df


def concat_seasons(seasons: list[str], season_type: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for i, season in enumerate(seasons):
        if i > 0:
            time.sleep(PAUSE_S)
        frames.append(fetch_advanced_totals(season, season_type))
    return pd.concat(frames, ignore_index=True)


def _prep_stat_cols(df: pd.DataFrame) -> pd.DataFrame:
    d = df[[NAME_COL, MIN_COL, GP_COL, USG_COL, TS_COL]].copy()
    d[MIN_COL] = pd.to_numeric(d[MIN_COL], errors="coerce").fillna(0.0)
    d[GP_COL] = pd.to_numeric(d[GP_COL], errors="coerce").fillna(0.0)
    d[USG_COL] = pd.to_numeric(d[USG_COL], errors="coerce")
    d[TS_COL] = pd.to_numeric(d[TS_COL], errors="coerce")
    # Row-level floor time consistent with Stats API Totals Advanced (GP * MIN-column).
    d["_row_min"] = d[GP_COL] * d[MIN_COL]
    d["_usg_min"] = d[USG_COL] * d["_row_min"]
    d["_ts_min"] = d[TS_COL] * d["_row_min"]
    return d


def aggregate_regular(df: pd.DataFrame) -> pd.DataFrame:
    """
    reg_total_min = sum(row_minutes); row_minutes = GP * MIN (API MIN = MPG slice).
    reg_usg = sum(USG_PCT * row_minutes) / reg_total_min
    reg_ts = sum(TS_PCT * row_minutes) / reg_total_min
    """
    d = _prep_stat_cols(df)
    g = d.groupby(NAME_COL, dropna=False, as_index=False).agg(
        reg_total_min=("_row_min", "sum"),
        _nu=("_usg_min", "sum"),
        _nt=("_ts_min", "sum"),
    )
    safe = g["reg_total_min"].replace(0, float("nan"))
    g["reg_usg"] = g["_nu"] / safe
    g["reg_ts"] = g["_nt"] / safe
    return g[[NAME_COL, "reg_usg", "reg_ts"]]


def aggregate_playoffs(df: pd.DataFrame) -> pd.DataFrame:
    """
    ply_total_min = sum(row_minutes); row_minutes = GP * MIN (API MIN = MPG slice).
    ply_total_gp = sum(GP)
    ply_mpg = ply_total_min / ply_total_gp
    ply_usg = sum(USG_PCT * row_minutes) / ply_total_min
    ply_ts = sum(TS_PCT * row_minutes) / ply_total_min
    """
    d = _prep_stat_cols(df)
    g = d.groupby(NAME_COL, dropna=False, as_index=False).agg(
        ply_total_min=("_row_min", "sum"),
        ply_total_gp=(GP_COL, "sum"),
        _nu=("_usg_min", "sum"),
        _nt=("_ts_min", "sum"),
    )
    safe = g["ply_total_min"].replace(0, float("nan"))
    g["ply_usg"] = g["_nu"] / safe
    g["ply_ts"] = g["_nt"] / safe
    g["ply_mpg"] = g["ply_total_min"] / g["ply_total_gp"].replace(0, float("nan"))
    return g.drop(columns=["_nu", "_nt", "ply_total_min"])


def classify_row(row: pd.Series) -> str:
    delta_u = row["delta_usg"]
    delta_t = row["delta_ts"]
    ru, pu = row["reg_usg"], row["ply_usg"]
    rt, pt = row["reg_ts"], row["ply_ts"]

    valid_index = (
        pd.notna(pu)
        and pd.notna(ru)
        and pd.notna(pt)
        and pd.notna(rt)
        and ru > 0
        and rt > 0
    )
    if valid_index:
        potential_index = (pu / ru) * (pt / rt)
        if potential_index <= 0.70:
            return "Choker"

    if delta_u <= 0 and pd.notna(delta_t) and delta_t <= -0.06:
        return "Choker"
    if delta_u > 0 and pd.notna(delta_t) and delta_t >= -0.06:
        return "Riser"
    return "Neutral"


def round_float_cols(df: pd.DataFrame, cols: list[str], ndigits: int = 3) -> pd.DataFrame:
    out = df.copy()
    for c in cols:
        if c in out.columns:
            out[c] = out[c].round(ndigits)
    return out


def main() -> None:
    print("Fetching Regular Season ...", flush=True)
    reg_df = concat_seasons(SEASONS, SEASON_TYPE_REG)
    print("Fetching Playoffs ...", flush=True)
    ply_df = concat_seasons(SEASONS, SEASON_TYPE_PO)

    print("Weighted aggregation ...", flush=True)
    reg_a = aggregate_regular(reg_df)
    ply_a = aggregate_playoffs(ply_df)

    merged = ply_a.merge(reg_a, on=NAME_COL, how="inner")

    qualifying = merged[
        (merged["ply_mpg"] >= 22.0) & (merged["ply_total_gp"] >= 10)
    ].copy()

    qualifying["delta_usg"] = qualifying["ply_usg"] - qualifying["reg_usg"]
    qualifying["delta_ts"] = qualifying["ply_ts"] - qualifying["reg_ts"]
    qualifying["tag"] = qualifying.apply(classify_row, axis=1)

    out = qualifying.rename(columns={NAME_COL: "player_name"})[OUT_COLUMNS].copy()
    out = round_float_cols(out, ROUND_FLOATS, 3)

    print(f"Saving {len(out)} rows to playoff_riser_choker ...", flush=True)
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("DROP TABLE IF EXISTS playoff_riser_choker")
        out.to_sql("playoff_riser_choker", con, index=False, if_exists="replace")
        con.commit()
    finally:
        con.close()

    risers = out[out["tag"] == "Riser"]["player_name"].sort_values().tolist()
    chokers = out[out["tag"] == "Choker"]["player_name"].sort_values().tolist()

    print("", flush=True)
    print(f"Season window: {' / '.join(SEASONS)}  |  qualified: {len(out)} players", flush=True)
    print(f"Risers ({len(risers)}):", flush=True)
    print("  " + ", ".join(risers) if risers else "  —", flush=True)
    print(f"Chokers ({len(chokers)}):", flush=True)
    print("  " + ", ".join(chokers) if chokers else "  —", flush=True)


if __name__ == "__main__":
    main()
