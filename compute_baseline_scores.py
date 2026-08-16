"""
compute_baseline_scores.py
----------------------------
Naive baseline for backtest seasons: predict season N from team_stats of N-1
(wins, conference_seed, made_playoffs). Score against actuals already in the DB.

Champion probability baseline: each team's p(champion) is its prior-season
win_pct share (normalized to sum to 1.0 across 30 teams). Brier and
champion top-1/top-4 use this same distribution (favorite = highest p).

Read-only on prediction/raw tables; writes only to baseline_scores.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass

from season_utils import source_season

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

BACKTEST_SEASONS = [
    "2019-20",
    "2020-21",
    "2021-22",
    "2022-23",
    "2023-24",
    "2024-25",
    "2025-26",
]
PARTIAL_SEASON = "2018-19"
ALL_SEASONS = [PARTIAL_SEASON] + BACKTEST_SEASONS

# Fallback season length for seasons with no actuals yet. Win totals are
# normalized as a rate (prior_wins / prior_games * target_games) so that the
# short 2019-20 and 2020-21 slates are neither inflated nor double-shrunk.
FULL_SEASON_GAMES = 82.0

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS baseline_scores (
    season  TEXT NOT NULL,
    metric  TEXT NOT NULL,
    value   REAL NOT NULL,
    notes   TEXT,
    PRIMARY KEY (season, metric)
);
"""

METRIC_DIRECTION = {
    "mae_wins": "lower is better",
    "mae_win_pct": "lower is better",
    "seed_accuracy_exact": "higher is better",
    "seed_accuracy_pm1": "higher is better",
    "playoff_berth_accuracy": "higher is better",
    "champion_top1": "higher is better",
    "champion_top4": "higher is better",
    "brier_champion": "lower is better",
}

METRIC_LABELS = {
    "mae_wins": "MAE wins",
    "mae_win_pct": "MAE win % (scaled)",
    "seed_accuracy_exact": "Seed exact %",
    "seed_accuracy_pm1": "Seed ±1 %",
    "playoff_berth_accuracy": "Playoff berth %",
    "champion_top1": "Champion top-1 %",
    "champion_top4": "Champion top-4 %",
    "brier_champion": "Brier (champion)",
}


@dataclass(frozen=True)
class TeamRow:
    team_id: int
    team_abbr: str
    wins: int
    losses: int
    conference_seed: int | None
    made_playoffs: int

    @property
    def win_pct(self) -> float:
        games = self.wins + self.losses
        return self.wins / games if games else 0.0


@dataclass
class SeasonMetrics:
    mae_wins: float
    mae_win_pct: float
    seed_accuracy_exact: float
    seed_accuracy_pm1: float
    playoff_berth_accuracy: float
    champion_top1: float
    champion_top4: float
    brier_champion: float
    notes: str = ""


def _fetch_team_stats(con: sqlite3.Connection, season: str) -> dict[int, TeamRow]:
    rows = con.execute(
        """
        SELECT team_id, team_abbr, wins, losses, conference_seed, made_playoffs
        FROM team_stats
        WHERE season = ?
        ORDER BY team_id
        """,
        (season,),
    ).fetchall()
    return {
        team_id: TeamRow(
            team_id=team_id,
            team_abbr=abbr,
            wins=int(wins),
            losses=int(losses or 0),
            conference_seed=seed,
            made_playoffs=int(made_playoffs or 0),
        )
        for team_id, abbr, wins, losses, seed, made_playoffs in rows
    }


def _fetch_games_played(con: sqlite3.Connection, season: str) -> float:
    """Average games actually played by a team in `season` (wins + losses)."""
    row = con.execute(
        "SELECT AVG(wins + losses) FROM team_stats WHERE season = ?",
        (season,),
    ).fetchone()
    if not row or row[0] is None or float(row[0]) <= 0:
        return FULL_SEASON_GAMES
    return float(row[0])


def _fetch_champion_abbr(con: sqlite3.Connection, season: str) -> str | None:
    row = con.execute(
        """
        SELECT team_abbr
        FROM team_stats_playoffs
        WHERE season = ? AND playoff_result = 'Champion'
        LIMIT 1
        """,
        (season,),
    ).fetchone()
    return row[0] if row else None


def _champion_probs_by_win_pct(
    prior: dict[int, TeamRow], team_ids: list[int]
) -> dict[str, float]:
    """Prior-season win_pct shares; sum to 1.0. Uniform fallback if all zero."""
    weights = {prior[tid].team_abbr: prior[tid].win_pct for tid in team_ids}
    total = sum(weights.values())
    if total <= 0:
        uniform = 1.0 / len(team_ids)
        return {prior[tid].team_abbr: uniform for tid in team_ids}
    return {abbr: w / total for abbr, w in weights.items()}


def _top_k_by_prob(probs: dict[str, float], k: int) -> list[str]:
    return [
        abbr
        for abbr, _ in sorted(probs.items(), key=lambda item: (-item[1], item[0]))
    ][:k]


def compute_season_metrics(
    con: sqlite3.Connection, target_season: str, notes: str = ""
) -> SeasonMetrics | None:
    source = source_season(target_season)
    prior = _fetch_team_stats(con, source)
    actual = _fetch_team_stats(con, target_season)

    common_ids = sorted(set(prior) & set(actual))
    if not common_ids:
        return None

    season_games = _fetch_games_played(con, target_season)

    win_errors: list[float] = []
    win_pct_errors: list[float] = []
    seed_exact = 0
    seed_pm1 = 0
    playoff_hits = 0
    seed_scored = 0

    for team_id in common_ids:
        pred = prior[team_id]
        act = actual[team_id]
        # Rate-based: the prediction is a win rate over the source season's own
        # games, projected onto the games this team actually played.
        target_games = (act.wins + act.losses) or season_games
        predicted_wins = pred.win_pct * target_games
        win_errors.append(abs(predicted_wins - act.wins))
        win_pct_errors.append(abs(pred.win_pct - act.win_pct) * 100.0)

        if pred.conference_seed is not None and act.conference_seed is not None:
            seed_scored += 1
            if pred.conference_seed == act.conference_seed:
                seed_exact += 1
            if abs(pred.conference_seed - act.conference_seed) <= 1:
                seed_pm1 += 1

        if pred.made_playoffs == act.made_playoffs:
            playoff_hits += 1

    ranked = _champion_probs_by_win_pct(prior, common_ids)
    top1_abbr = _top_k_by_prob(ranked, 1)[0] if ranked else None
    top4_abbrs = set(_top_k_by_prob(ranked, 4))

    actual_champion = _fetch_champion_abbr(con, target_season)
    champion_top1 = (
        100.0 if actual_champion and actual_champion == top1_abbr else 0.0
    )
    champion_top4 = (
        100.0 if actual_champion and actual_champion in top4_abbrs else 0.0
    )

    brier_terms: list[float] = []
    for team_id in common_ids:
        abbr = actual[team_id].team_abbr
        p = ranked[abbr]
        y = 1.0 if actual_champion and abbr == actual_champion else 0.0
        brier_terms.append((p - y) ** 2)

    n = len(common_ids)
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


def _metrics_to_rows(season: str, metrics: SeasonMetrics) -> list[tuple[str, str, float, str]]:
    note = metrics.notes
    return [
        (season, "mae_wins", metrics.mae_wins, note),
        (season, "mae_win_pct", metrics.mae_win_pct, note),
        (season, "seed_accuracy_exact", metrics.seed_accuracy_exact, note),
        (season, "seed_accuracy_pm1", metrics.seed_accuracy_pm1, note),
        (season, "playoff_berth_accuracy", metrics.playoff_berth_accuracy, note),
        (season, "champion_top1", metrics.champion_top1, note),
        (season, "champion_top4", metrics.champion_top4, note),
        (season, "brier_champion", metrics.brier_champion, note),
    ]


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
        source = source_season(target)
        prior = _fetch_team_stats(con, source)
        actual = _fetch_team_stats(con, target)
        common_ids = sorted(set(prior) & set(actual))
        season_games = _fetch_games_played(con, target)
        for team_id in common_ids:
            pred = prior[team_id]
            act = actual[team_id]
            target_games = (act.wins + act.losses) or season_games
            predicted_wins = pred.win_pct * target_games
            total_abs_error += abs(predicted_wins - act.wins)
            total_abs_pct_error += abs(pred.win_pct - act.win_pct) * 100.0
            if (
                prior[team_id].conference_seed is not None
                and actual[team_id].conference_seed is not None
            ):
                seed_scored += 1
                if prior[team_id].conference_seed == actual[team_id].conference_seed:
                    seed_exact += 1
                if (
                    abs(prior[team_id].conference_seed - actual[team_id].conference_seed)
                    <= 1
                ):
                    seed_pm1 += 1
            if prior[team_id].made_playoffs == actual[team_id].made_playoffs:
                playoff_hits += 1
            total_teams += 1
        brier_sum += metrics.brier_champion * len(common_ids)

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
            "champion p = prior win_pct share (sums to 100%)"
        ),
    )


def write_baseline_scores(rows: list[tuple[str, str, float, str]]) -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("PRAGMA foreign_keys = ON;")
        con.execute(CREATE_TABLE_SQL)
        con.execute("DELETE FROM baseline_scores")
        con.executemany(
            "INSERT INTO baseline_scores (season, metric, value, notes) VALUES (?, ?, ?, ?)",
            rows,
        )
        con.commit()
    finally:
        con.close()


def print_scorecard(
    season_metrics: dict[str, SeasonMetrics], pooled: SeasonMetrics
) -> None:
    metric_keys = list(METRIC_LABELS.keys())
    col_w = 12
    name_w = 10

    header = f"{'Season':<{name_w}} | " + " | ".join(
        f"{METRIC_LABELS[k]:>{col_w}}" for k in metric_keys
    )
    divider = "-" * len(header)

    print("\n=== Naive baseline scorecard (predict N from N-1 team_stats) ===\n")
    print(header)
    print(divider)

    def fmt(season: str, m: SeasonMetrics) -> str:
        values = {
            "mae_wins": f"{m.mae_wins:.2f}",
            "mae_win_pct": f"{m.mae_win_pct:.2f}",
            "seed_accuracy_exact": f"{m.seed_accuracy_exact:.1f}",
            "seed_accuracy_pm1": f"{m.seed_accuracy_pm1:.1f}",
            "playoff_berth_accuracy": f"{m.playoff_berth_accuracy:.1f}",
            "champion_top1": f"{m.champion_top1:.0f}",
            "champion_top4": f"{m.champion_top4:.0f}",
            "brier_champion": f"{m.brier_champion:.4f}",
        }
        label = f"{season}*" if season == PARTIAL_SEASON else season
        return f"{label:<{name_w}} | " + " | ".join(
            f"{values[k]:>{col_w}}" for k in metric_keys
        )

    for season in ALL_SEASONS:
        if season in season_metrics:
            print(fmt(season, season_metrics[season]))
    print(divider)
    print(fmt("POOLED", pooled))

    print(f"\n* {PARTIAL_SEASON}: caveated / partial data (included in POOLED)")
    print("\nMetric direction:")
    for key, direction in METRIC_DIRECTION.items():
        print(f"  {METRIC_LABELS[key]:<{22}} {direction}")
    print(
        "\nChampion p baseline: prior-season win_pct / sum(win_pct), "
        "summing to 100% across 30 teams."
    )
    print(
        "Champion top-1 / top-4: favorite(s) = highest p (top-4 by p ranking)."
    )


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
                print(f"[warn] skipping {season}: missing source or target team_stats")
                continue
            season_metrics[season] = metrics
        pooled = compute_pooled(con, season_metrics)
        # Mirrors the POOLED_NO_1819 section engine_scores writes, so the two
        # tables can be compared section-for-section.
        pooled_no_partial = compute_pooled(
            con, {s: m for s, m in season_metrics.items() if s != PARTIAL_SEASON}
        )
        pooled_no_partial.notes = (
            f"pooled over {len(BACKTEST_SEASONS)} full seasons "
            f"({', '.join(BACKTEST_SEASONS)}); "
            "champion p = prior win_pct share (sums to 100%)"
        )
    finally:
        con.close()

    rows: list[tuple[str, str, float, str]] = []
    for season, metrics in season_metrics.items():
        rows.extend(_metrics_to_rows(season, metrics))
    rows.extend(_metrics_to_rows("POOLED", pooled))
    rows.extend(_metrics_to_rows("POOLED_NO_1819", pooled_no_partial))

    write_baseline_scores(rows)
    print_scorecard(season_metrics, pooled)
    print(f"\nWrote {len(rows)} rows to baseline_scores in {DB_PATH}")


if __name__ == "__main__":
    main()
