"""
compute_engine_scores.py
------------------------
Score engine predictions (simulation_results, run_id='production') against
actuals using the same metrics and pooling logic as compute_baseline_scores.py.

Read-only on predictions/raw tables; writes only to engine_scores.
Prints side-by-side comparison vs baseline_scores.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass

from compute_baseline_scores import (
    ALL_SEASONS,
    BACKTEST_SEASONS,
    METRIC_DIRECTION,
    METRIC_LABELS,
    PARTIAL_SEASON,
    SeasonMetrics,
    _fetch_champion_abbr,
    _fetch_games_played,
    _fetch_team_stats,
    _metrics_to_rows,
    _top_k_by_prob,
    compute_pooled as compute_baseline_pooled,
    compute_season_metrics as compute_baseline_season_metrics,
)

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")
RUN_ID = "production"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS engine_scores (
    season  TEXT NOT NULL,
    metric  TEXT NOT NULL,
    value   REAL NOT NULL,
    notes   TEXT,
    PRIMARY KEY (season, metric)
);
"""

SEED_COLS = [f"seed_{i}_pct" for i in range(1, 16)]
LOWER_IS_BETTER = {"mae_wins", "mae_win_pct", "brier_champion"}


@dataclass(frozen=True)
class EngineRow:
    team: str
    avg_wins: float
    seed_pcts: tuple[float, ...]
    make_playoffs_pct: float
    champion_pct: float


def _fetch_engine_predictions(
    con: sqlite3.Connection, season: str, run_id: str = RUN_ID
) -> dict[str, EngineRow]:
    cols = (
        ["team", "avg_wins"]
        + SEED_COLS
        + ["missed_playoffs_pct", "champion_pct"]
    )
    rows = con.execute(
        f"""
        SELECT {", ".join(cols)}
        FROM simulation_results
        WHERE season = ? AND run_id = ?
        ORDER BY team
        """,
        (season, run_id),
    ).fetchall()
    out: dict[str, EngineRow] = {}
    for row in rows:
        team = row[0]
        avg_wins = float(row[1])
        seed_pcts = tuple(float(v or 0.0) for v in row[2:17])
        missed = float(row[17] or 0.0)
        champion_pct = float(row[18] or 0.0)
        out[team] = EngineRow(
            team=team,
            avg_wins=avg_wins,
            seed_pcts=seed_pcts,
            make_playoffs_pct=100.0 - missed,
            champion_pct=champion_pct,
        )
    return out


def _games_simulated(con: sqlite3.Connection, season: str) -> int:
    """Games per team the simulation played — mirrors the rounding that
    run_monte_carlo.season_games_per_team applies when it sizes the schedule."""
    return int(round(_fetch_games_played(con, season)))


def _predicted_seed(seed_pcts: tuple[float, ...]) -> int | None:
    if not seed_pcts:
        return None
    best_seed = 1
    best_pct = seed_pcts[0]
    for i, pct in enumerate(seed_pcts, start=1):
        if pct > best_pct:
            best_pct = pct
            best_seed = i
    return best_seed


def _champion_probs_from_engine(preds: dict[str, EngineRow]) -> dict[str, float]:
    return {team: row.champion_pct / 100.0 for team, row in preds.items()}


def compute_season_metrics(
    con: sqlite3.Connection, target_season: str, notes: str = ""
) -> SeasonMetrics | None:
    preds = _fetch_engine_predictions(con, target_season)
    actual_by_id = _fetch_team_stats(con, target_season)
    actual_by_abbr = {row.team_abbr: row for row in actual_by_id.values()}

    common_abbrs = sorted(set(preds) & set(actual_by_abbr))
    if not common_abbrs:
        return None

    # run_monte_carlo plays each season's real number of games, so avg_wins is
    # already on the target season's scale — rescaling here would shrink it a
    # second time. games_simulated is only the win-rate denominator.
    games_simulated = _games_simulated(con, target_season)

    win_errors: list[float] = []
    win_pct_errors: list[float] = []
    seed_exact = 0
    seed_pm1 = 0
    playoff_hits = 0
    seed_scored = 0

    for abbr in common_abbrs:
        pred = preds[abbr]
        act = actual_by_abbr[abbr]
        win_errors.append(abs(pred.avg_wins - act.wins))
        win_pct_errors.append(
            abs(pred.avg_wins / games_simulated - act.win_pct) * 100.0
        )

        pred_seed = _predicted_seed(pred.seed_pcts)
        if pred_seed is not None and act.conference_seed is not None:
            seed_scored += 1
            if pred_seed == act.conference_seed:
                seed_exact += 1
            if abs(pred_seed - act.conference_seed) <= 1:
                seed_pm1 += 1

        pred_playoffs = 1 if pred.make_playoffs_pct >= 50.0 else 0
        if pred_playoffs == act.made_playoffs:
            playoff_hits += 1

    champ_probs = _champion_probs_from_engine(
        {abbr: preds[abbr] for abbr in common_abbrs}
    )
    top1_abbr = _top_k_by_prob(champ_probs, 1)[0] if champ_probs else None
    top4_abbrs = set(_top_k_by_prob(champ_probs, 4))

    actual_champion = _fetch_champion_abbr(con, target_season)
    champion_top1 = (
        100.0 if actual_champion and actual_champion == top1_abbr else 0.0
    )
    champion_top4 = (
        100.0 if actual_champion and actual_champion in top4_abbrs else 0.0
    )

    brier_terms: list[float] = []
    for abbr in common_abbrs:
        p = champ_probs[abbr]
        y = 1.0 if actual_champion and abbr == actual_champion else 0.0
        brier_terms.append((p - y) ** 2)

    n = len(common_abbrs)
    return SeasonMetrics(
        mae_wins=sum(win_errors) / n,
        mae_win_pct=sum(win_pct_errors) / n,
        seed_accuracy_exact=(100.0 * seed_exact / seed_scored) if seed_scored else 0.0,
        seed_accuracy_pm1=(100.0 * seed_pm1 / seed_scored) if seed_scored else 0.0,
        playoff_berth_accuracy=100.0 * playoff_hits / n,
        champion_top1=champion_top1,
        champion_top4=champion_top4,
        brier_champion=sum(brier_terms) / n,
        notes=notes,
    )


def compute_pooled(
    con: sqlite3.Connection, all_metrics: dict[str, SeasonMetrics]
) -> SeasonMetrics:
    """Micro-average for continuous metrics; macro % for champion hits."""
    total_abs_error = 0.0
    total_abs_pct_error = 0.0
    total_teams = 0
    seed_exact = 0
    seed_pm1 = 0
    seed_scored = 0
    playoff_hits = 0
    brier_sum = 0.0
    champion_top1_hits = 0
    champion_top4_hits = 0
    n_seasons = len(all_metrics)

    for target, metrics in all_metrics.items():
        preds = _fetch_engine_predictions(con, target)
        actual_by_id = _fetch_team_stats(con, target)
        actual_by_abbr = {row.team_abbr: row for row in actual_by_id.values()}
        common_abbrs = sorted(set(preds) & set(actual_by_abbr))
        games_simulated = _games_simulated(con, target)

        for abbr in common_abbrs:
            pred = preds[abbr]
            act = actual_by_abbr[abbr]
            total_abs_error += abs(pred.avg_wins - act.wins)
            total_abs_pct_error += (
                abs(pred.avg_wins / games_simulated - act.win_pct) * 100.0
            )

            pred_seed = _predicted_seed(pred.seed_pcts)
            if pred_seed is not None and act.conference_seed is not None:
                seed_scored += 1
                if pred_seed == act.conference_seed:
                    seed_exact += 1
                if abs(pred_seed - act.conference_seed) <= 1:
                    seed_pm1 += 1

            pred_playoffs = 1 if pred.make_playoffs_pct >= 50.0 else 0
            if pred_playoffs == act.made_playoffs:
                playoff_hits += 1
            total_teams += 1

        brier_sum += metrics.brier_champion * len(common_abbrs)

        if metrics.champion_top1 >= 100.0:
            champion_top1_hits += 1
        if metrics.champion_top4 >= 100.0:
            champion_top4_hits += 1

    return SeasonMetrics(
        mae_wins=total_abs_error / total_teams if total_teams else 0.0,
        mae_win_pct=total_abs_pct_error / total_teams if total_teams else 0.0,
        seed_accuracy_exact=(100.0 * seed_exact / seed_scored) if seed_scored else 0.0,
        seed_accuracy_pm1=(100.0 * seed_pm1 / seed_scored) if seed_scored else 0.0,
        playoff_berth_accuracy=(100.0 * playoff_hits / total_teams)
        if total_teams
        else 0.0,
        champion_top1=(100.0 * champion_top1_hits / n_seasons) if n_seasons else 0.0,
        champion_top4=(100.0 * champion_top4_hits / n_seasons) if n_seasons else 0.0,
        brier_champion=brier_sum / total_teams if total_teams else 0.0,
        notes=(
            f"pooled over {n_seasons} seasons ({', '.join(all_metrics.keys())}); "
            f"run_id={RUN_ID}"
        ),
    )


def write_engine_scores(rows: list[tuple[str, str, float, str]]) -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("PRAGMA foreign_keys = ON;")
        con.execute(CREATE_TABLE_SQL)
        con.execute("DELETE FROM engine_scores")
        con.executemany(
            "INSERT INTO engine_scores (season, metric, value, notes) VALUES (?, ?, ?, ?)",
            rows,
        )
        con.commit()
    finally:
        con.close()


def load_scores(con: sqlite3.Connection, table: str) -> dict[str, dict[str, float]]:
    rows = con.execute(
        f"SELECT season, metric, value FROM {table} ORDER BY season, metric"
    ).fetchall()
    out: dict[str, dict[str, float]] = {}
    for season, metric, value in rows:
        out.setdefault(season, {})[metric] = float(value)
    return out


def _metric_value(metrics: SeasonMetrics, key: str) -> float:
    return getattr(metrics, key)


def _format_metric(key: str, value: float) -> str:
    if key in ("mae_wins", "mae_win_pct"):
        return f"{value:.2f}"
    if key == "brier_champion":
        return f"{value:.4f}"
    return f"{value:.1f}"


def _winner(key: str, baseline: float, engine: float) -> str:
    if abs(engine - baseline) < 1e-9:
        return "tie"
    if key in LOWER_IS_BETTER:
        return "engine" if engine < baseline else "baseline"
    return "engine" if engine > baseline else "baseline"


def print_comparison(
    baseline: dict[str, dict[str, float]],
    engine: dict[str, dict[str, float]],
    sections: list[str],
) -> None:
    metric_keys = list(METRIC_LABELS.keys())
    print("\n=== Baseline vs engine (simulation_results, run_id='production') ===\n")

    for section in sections:
        if section not in baseline or section not in engine:
            print(f"[warn] missing scores for section {section}")
            continue

        label = section
        if section == PARTIAL_SEASON:
            label = f"{section}* (caveated / partial data)"
        elif section == "POOLED_NO_1819":
            label = "POOLED (excl. 2018-19)"

        print(f"--- {label} ---")
        header = (
            f"{'Metric':<22} | {'Baseline':>10} | {'Engine':>10} | "
            f"{'Diff':>10} | {'Winner':>8}"
        )
        print(header)
        print("-" * len(header))

        for key in metric_keys:
            b = baseline[section][key]
            e = engine[section][key]
            diff = e - b
            win = _winner(key, b, e)
            direction = "↓" if key in LOWER_IS_BETTER else "↑"
            print(
                f"{METRIC_LABELS[key]:<20}{direction} | "
                f"{_format_metric(key, b):>10} | "
                f"{_format_metric(key, e):>10} | "
                f"{_format_metric(key, diff):>10} | "
                f"{win:>8}"
            )
        print()

    print("* 2018-19 excluded from POOLED (excl. 2018-19) row above.")
    print("\nDiff = engine − baseline. Winner uses metric direction:")
    for key, direction in METRIC_DIRECTION.items():
        print(f"  {METRIC_LABELS[key]:<22} {direction}")


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    con = sqlite3.connect(DB_PATH)
    season_metrics: dict[str, SeasonMetrics] = {}
    try:
        partial_note = "caveated / partial data"
        for season in ALL_SEASONS:
            notes = partial_note if season == PARTIAL_SEASON else ""
            metrics = compute_season_metrics(con, season, notes=notes)
            if metrics is None:
                print(f"[warn] skipping {season}: missing predictions or actuals")
                continue
            season_metrics[season] = metrics

        pooled = compute_pooled(con, season_metrics)
        pooled_no_partial = compute_pooled(
            con, {s: m for s, m in season_metrics.items() if s != PARTIAL_SEASON}
        )
        pooled_no_partial.notes = (
            f"pooled over {len(BACKTEST_SEASONS)} full seasons "
            f"({', '.join(BACKTEST_SEASONS)}); run_id={RUN_ID}"
        )

        baseline_scores = load_scores(con, "baseline_scores")

        baseline_backtest_metrics: dict[str, SeasonMetrics] = {}
        for season in BACKTEST_SEASONS:
            metrics = compute_baseline_season_metrics(con, season)
            if metrics is not None:
                baseline_backtest_metrics[season] = metrics
        baseline_pooled_no_partial = compute_baseline_pooled(
            con, baseline_backtest_metrics
        )
        baseline_scores["POOLED_NO_1819"] = {
            k: _metric_value(baseline_pooled_no_partial, k) for k in METRIC_LABELS
        }
    finally:
        con.close()

    rows: list[tuple[str, str, float, str]] = []
    for season, metrics in season_metrics.items():
        rows.extend(_metrics_to_rows(season, metrics))
    rows.extend(_metrics_to_rows("POOLED", pooled))
    rows.extend(_metrics_to_rows("POOLED_NO_1819", pooled_no_partial))

    write_engine_scores(rows)

    engine_scores = {
        season: {k: _metric_value(m, k) for k in METRIC_LABELS}
        for season, m in {
            **season_metrics,
            "POOLED": pooled,
            "POOLED_NO_1819": pooled_no_partial,
        }.items()
    }

    sections = ALL_SEASONS + ["POOLED", "POOLED_NO_1819"]
    print_comparison(baseline_scores, engine_scores, sections)
    print(f"Wrote {len(rows)} rows to engine_scores in {DB_PATH}")


if __name__ == "__main__":
    main()
