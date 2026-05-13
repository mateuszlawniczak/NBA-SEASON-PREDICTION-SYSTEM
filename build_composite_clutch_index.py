"""
build_composite_clutch_index.py
-------------------------------
3-year window (2022-23, 2023-24, 2024-25): fetch playoff vs regular advanced
metrics, Q4 true shooting shifts, and elimination-game TS vs baseline; score
each player (-3..+3) and write ``playoff_riser_choker`` only (table replace).

Does not alter any other SQLite tables.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time
import unicodedata

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nba_api.stats.endpoints import LeagueDashPlayerStats, PlayerGameLogs
from nba_api.stats.library.parameters import Period

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

PLAYOFF_MULTIPLIER = {
    3: 1.15,
    2: 1.10,
    1: 1.05,
    0: 1.00,
    -1: 0.95,
    -2: 0.85,
    -3: 0.75,
}

# Championship DNA: Finals MVPs (incl. 2024–25); matched with ASCII-folded names.
FMVPS_ACTIVE = (
    "LeBron James",
    "Kevin Durant",
    "Kawhi Leonard",
    "Giannis Antetokounmpo",
    "Stephen Curry",
    "Nikola Jokic",
    "Jaylen Brown",
    "Shai Gilgeous-Alexander",
)


def _norm_player_name(name: str) -> str:
    return (
        unicodedata.normalize("NFKD", str(name))
        .encode("ascii", "ignore")
        .decode()
        .casefold()
    )


FMVPS_SET = {_norm_player_name(n) for n in FMVPS_ACTIVE}


def fetch_advanced_totals(
    season: str, season_type: str, *, period: str = Period.default
) -> pd.DataFrame:
    label = "All periods" if period == Period.default else f"Period {period}"
    print(f"  -> {season} [{season_type}] Advanced Totals ({label}) ...", flush=True)
    r = LeagueDashPlayerStats(
        season=season,
        per_mode_detailed="Totals",
        measure_type_detailed_defense="Advanced",
        season_type_all_star=season_type,
        period=period,
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    print(f"     rows={len(df)}", flush=True)
    return df


def concat_seasons(
    seasons: list[str], season_type: str, *, period: str = Period.default
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for i, season in enumerate(seasons):
        if i > 0:
            time.sleep(PAUSE_S)
        frames.append(fetch_advanced_totals(season, season_type, period=period))
    return pd.concat(frames, ignore_index=True)


def _prep_weight_cols(df: pd.DataFrame) -> pd.DataFrame:
    d = df[[NAME_COL, MIN_COL, GP_COL, USG_COL, TS_COL]].copy()
    d[MIN_COL] = pd.to_numeric(d[MIN_COL], errors="coerce").fillna(0.0)
    d[GP_COL] = pd.to_numeric(d[GP_COL], errors="coerce").fillna(0.0)
    d[USG_COL] = pd.to_numeric(d[USG_COL], errors="coerce")
    d[TS_COL] = pd.to_numeric(d[TS_COL], errors="coerce")
    d["_row_min"] = d[GP_COL] * d[MIN_COL]
    d["_usg_min"] = d[USG_COL] * d["_row_min"]
    d["_ts_min"] = d[TS_COL] * d["_row_min"]
    return d


def _prep_ts_only(df: pd.DataFrame) -> pd.DataFrame:
    d = df[[NAME_COL, MIN_COL, GP_COL, TS_COL]].copy()
    d[MIN_COL] = pd.to_numeric(d[MIN_COL], errors="coerce").fillna(0.0)
    d[GP_COL] = pd.to_numeric(d[GP_COL], errors="coerce").fillna(0.0)
    d[TS_COL] = pd.to_numeric(d[TS_COL], errors="coerce")
    d["_row_min"] = d[GP_COL] * d[MIN_COL]
    d["_ts_min"] = d[TS_COL] * d["_row_min"]
    return d


def aggregate_regular_baseline(df: pd.DataFrame) -> pd.DataFrame:
    d = _prep_weight_cols(df)
    g = d.groupby(NAME_COL, dropna=False, as_index=False).agg(
        reg_total_min=("_row_min", "sum"),
        _nu=("_usg_min", "sum"),
        _nt=("_ts_min", "sum"),
    )
    safe = g["reg_total_min"].replace(0, float("nan"))
    g["reg_usg"] = g["_nu"] / safe
    g["reg_ts"] = g["_nt"] / safe
    return g[[NAME_COL, "reg_usg", "reg_ts"]]


def aggregate_playoffs_baseline(df: pd.DataFrame) -> pd.DataFrame:
    d = _prep_weight_cols(df)
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
    return g[[NAME_COL, "ply_usg", "ply_ts", "ply_mpg", "ply_total_gp"]]


def aggregate_regular_q4_ts(df: pd.DataFrame) -> pd.DataFrame:
    d = _prep_ts_only(df)
    g = d.groupby(NAME_COL, dropna=False, as_index=False).agg(
        reg_q4_total_min=("_row_min", "sum"),
        _nt=("_ts_min", "sum"),
    )
    safe = g["reg_q4_total_min"].replace(0, float("nan"))
    g["reg_q4_ts"] = g["_nt"] / safe
    return g[[NAME_COL, "reg_q4_ts"]]


def aggregate_playoffs_q4_ts(df: pd.DataFrame) -> pd.DataFrame:
    d = _prep_ts_only(df)
    g = d.groupby(NAME_COL, dropna=False, as_index=False).agg(
        ply_q4_total_min=("_row_min", "sum"),
        ply_q4_gp=(GP_COL, "sum"),
        _nt=("_ts_min", "sum"),
    )
    safe = g["ply_q4_total_min"].replace(0, float("nan"))
    g["ply_q4_ts"] = g["_nt"] / safe
    g["ply_q4_mpg"] = g["ply_q4_total_min"] / g["ply_q4_gp"].replace(0, float("nan"))
    return g[[NAME_COL, "ply_q4_ts", "ply_q4_mpg", "ply_q4_gp"]]


def fetch_playoff_gamelogs(season: str) -> pd.DataFrame:
    print(f"  -> {season} [Playoffs] PlayerGameLogs ...", flush=True)
    r = PlayerGameLogs(
        season_nullable=season,
        season_type_nullable=SEASON_TYPE_PO,
        timeout=TIMEOUT,
    )
    df = r.get_data_frames()[0]
    print(f"     rows={len(df)}", flush=True)
    return df


def concat_playoff_gamelogs(seasons: list[str]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for i, season in enumerate(seasons):
        if i > 0:
            time.sleep(PAUSE_S)
        frames.append(fetch_playoff_gamelogs(season))
    return pd.concat(frames, ignore_index=True)


def aggregate_elimination_ts(game_logs: pd.DataFrame) -> pd.DataFrame:
    """Games 5–7 proxy: GAME_ID string ends in '5', '6', or '7'."""
    g = game_logs.copy()
    g["GAME_ID"] = g["GAME_ID"].astype(str)
    g = g[g["GAME_ID"].str.endswith(("5", "6", "7"))].copy()
    g["PTS"] = pd.to_numeric(g["PTS"], errors="coerce").fillna(0.0)
    g["FGA"] = pd.to_numeric(g["FGA"], errors="coerce").fillna(0.0)
    g["FTA"] = pd.to_numeric(g["FTA"], errors="coerce").fillna(0.0)
    u = (
        g.groupby(NAME_COL, dropna=False, as_index=False)[["PTS", "FGA", "FTA"]]
        .sum()
        .rename(columns={NAME_COL: NAME_COL})
    )
    den = 2.0 * (u["FGA"] + 0.44 * u["FTA"])
    u["elim_ts"] = np.where(
        den > 0,
        u["PTS"] / den,
        np.nan,
    )
    return u[[NAME_COL, "elim_ts"]]


def baseline_score_vec(reg_usg, reg_ts, ply_usg, ply_ts) -> np.ndarray:
    ru = np.asarray(reg_usg, dtype=float)
    rt = np.asarray(reg_ts, dtype=float)
    pu = np.asarray(ply_usg, dtype=float)
    pt = np.asarray(ply_ts, dtype=float)
    du = pu - ru
    dt = pt - rt
    valid_pi = (ru > 0) & (rt > 0) & ~np.isnan(pu) & ~np.isnan(pt)
    pi = np.full_like(ru, np.nan, dtype=float)
    pi[valid_pi] = (pu[valid_pi] / ru[valid_pi]) * (pt[valid_pi] / rt[valid_pi])
    choker_pi = valid_pi & (pi <= 0.70)
    choker_dt = (du <= 0) & ~np.isnan(dt) & (dt <= -0.06)
    choker = choker_pi | choker_dt
    riser = (du > 0) & ~np.isnan(dt) & (dt >= -0.06)
    return np.where(choker, -1, np.where(riser, 1, 0)).astype(int)


def q4_score_vec(ply_q4_ts, reg_q4_ts) -> np.ndarray:
    pq = np.asarray(ply_q4_ts, dtype=float)
    rq = np.asarray(reg_q4_ts, dtype=float)
    d = pq - rq
    return np.where(d >= 0.07, 1, np.where(d <= -0.07, -1, 0)).astype(int)


def elim_score_vec(elim_ts, reg_ts) -> np.ndarray:
    el = np.asarray(elim_ts, dtype=float)
    rt = np.asarray(reg_ts, dtype=float)
    d = el - rt
    return np.where(
        np.isnan(el),
        0,
        np.where(d >= 0.10, 1, np.where(d <= -0.10, -1, 0)),
    ).astype(int)


def main() -> None:
    print("Step 1: Baseline Advanced (full game) ...", flush=True)
    reg_full = concat_seasons(SEASONS, SEASON_TYPE_REG, period=Period.default)
    time.sleep(PAUSE_S)
    ply_full = concat_seasons(SEASONS, SEASON_TYPE_PO, period=Period.default)

    reg_a = aggregate_regular_baseline(reg_full)
    ply_a = aggregate_playoffs_baseline(ply_full)

    base = ply_a.merge(reg_a, on=NAME_COL, how="inner")
    qualified = base[
        (base["ply_mpg"] >= 22.0) & (base["ply_total_gp"] >= 10)
    ].copy()

    print("Step 2: 4th-quarter Advanced (Period=4) ...", flush=True)
    time.sleep(PAUSE_S)
    reg_q4 = concat_seasons(SEASONS, SEASON_TYPE_REG, period=Period.fourth)
    time.sleep(PAUSE_S)
    ply_q4 = concat_seasons(SEASONS, SEASON_TYPE_PO, period=Period.fourth)

    reg_q4_a = aggregate_regular_q4_ts(reg_q4)
    ply_q4_a = aggregate_playoffs_q4_ts(ply_q4)

    print("Step 3: Elimination-game logs (G5–G7 proxy) ...", flush=True)
    time.sleep(PAUSE_S)
    po_logs = concat_playoff_gamelogs(SEASONS)
    elim_a = aggregate_elimination_ts(po_logs)

    df = (
        qualified.merge(reg_q4_a, on=NAME_COL, how="left")
        .merge(ply_q4_a[[NAME_COL, "ply_q4_ts"]], on=NAME_COL, how="left")
        .merge(elim_a, on=NAME_COL, how="left")
    )

    b = baseline_score_vec(df["reg_usg"], df["reg_ts"], df["ply_usg"], df["ply_ts"])
    q = q4_score_vec(df["ply_q4_ts"], df["reg_q4_ts"])
    e = elim_score_vec(df["elim_ts"], df["reg_ts"])

    total = (b + q + e).astype(int)
    is_fmvp = (
        df[NAME_COL]
        .astype(str)
        .map(lambda n: _norm_player_name(n) in FMVPS_SET)
        .to_numpy()
    )
    total = np.where(is_fmvp & (total < 0), 0, total)

    pm = pd.Series(total).map(PLAYOFF_MULTIPLIER).astype(float)
    if pm.isna().any():
        raise ValueError(f"total_score out of mapped range: {pd.Series(total)[pm.isna()]}")

    out = pd.DataFrame(
        {
            "player_name": df[NAME_COL],
            "baseline_score": b.astype(int),
            "q4_score": q.astype(int),
            "elim_score": e.astype(int),
            "total_score": total.astype(int),
            "playoff_multiplier": pm,
        }
    )

    print(f"Saving {len(out)} rows to playoff_riser_choker ...", flush=True)
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("DROP TABLE IF EXISTS playoff_riser_choker")
        out.to_sql("playoff_riser_choker", con, index=False, if_exists="replace")
        con.commit()
    finally:
        con.close()

    print("", flush=True)
    risers = out[out["total_score"] >= 1].sort_values(
        "total_score", ascending=False
    )
    chokers = out[out["total_score"] <= -1].sort_values(
        "total_score", ascending=True
    )
    print("Risers (total_score >= 1):", flush=True)
    print(
        risers.to_string(index=False) if len(risers) else "  —",
        flush=True,
    )
    print("", flush=True)
    print("Chokers (total_score <= -1):", flush=True)
    print(
        chokers.to_string(index=False) if len(chokers) else "  —",
        flush=True,
    )


if __name__ == "__main__":
    main()
