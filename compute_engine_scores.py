"""
compute_engine_scores.py
------------------------
Score engine predictions (simulation_results, run_id='production') against
actuals using the same metrics and pooling logic as compute_baseline_scores.py.

Read-only on predictions/raw tables; writes only to engine_scores.
Prints side-by-side comparison vs baseline_scores.

With --note "text" the run is also appended to the permanent history layer
(runs / run_scores / a snapshot of simulation_results) and to docs/EXPERIMENTS.md.
History is append-only: nothing there is ever deleted or overwritten, and the
'production' predictions the dashboard reads are left untouched.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass

from compute_baseline_scores import (
    ALL_SEASONS,
    BACKTEST_SEASONS,
    METRIC_DIRECTION,
    METRIC_LABELS,
    PARTIAL_SEASON,
    POOLED_SECTIONS,
    SeasonMetrics,
    TEST_SEASONS,
    TRAIN_SEASONS,
    _fetch_champion_abbr,
    _fetch_games_played,
    _fetch_team_stats,
    _metrics_to_rows,
    _pearson_r,
    _title_rank,
    _top_k_by_prob,
    _zscore,
    compute_pooled as compute_baseline_pooled,
    compute_season_metrics as compute_baseline_season_metrics,
)
from run_monte_carlo import TEAM_CONFERENCE

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")
EXPERIMENTS_PATH = os.path.join(os.path.dirname(__file__), "docs", "EXPERIMENTS.md")
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

# Append-only history. Nothing here is ever deleted or updated: each logged run
# adds one `runs` row, its full score set, and a frozen copy of the predictions.
CREATE_HISTORY_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    note        TEXT NOT NULL,
    seasons     TEXT NOT NULL,
    git_commit  TEXT
);

CREATE TABLE IF NOT EXISTS run_scores (
    run     INTEGER NOT NULL,
    season  TEXT NOT NULL,
    metric  TEXT NOT NULL,
    value   REAL NOT NULL,
    PRIMARY KEY (run, season, metric)
);
"""

RUN_VS_BASELINE_VIEW_SQL = """
CREATE VIEW run_vs_baseline AS
SELECT
    rs.run                  AS run,
    rs.season               AS season,
    rs.metric               AS metric,
    rs.value                AS engine_value,
    bs.value                AS baseline_value,
    rs.value - bs.value     AS diff,
    CASE
        WHEN ABS(rs.value - bs.value) < 1e-9 THEN 'tie'
        WHEN rs.metric IN (
            'mae_wins', 'mae_win_pct', 'brier_champion', 'champion_rank'
        )
            THEN CASE WHEN rs.value < bs.value THEN 'engine' ELSE 'baseline' END
        ELSE CASE WHEN rs.value > bs.value THEN 'engine' ELSE 'baseline' END
    END                     AS winner
FROM run_scores rs
JOIN baseline_scores bs
  ON bs.season = rs.season
 AND bs.metric = rs.metric;
"""

SEED_COLS = [f"seed_{i}_pct" for i in range(1, 16)]
LOWER_IS_BETTER = {"mae_wins", "mae_win_pct", "brier_champion", "champion_rank"}
SEED_METRICS = ("seed_accuracy_exact", "seed_accuracy_pm1")
ORDERING_METRICS = ("win_order_r", "champion_rank")
EXPECTED_TEAMS_PER_CONFERENCE = 15
EXPECTED_SNAPSHOT_TEAMS = 30

# Conference ranking key. "avg_wins" = more simulated wins → better seed;
# "expected_seed" = sum_i i * seed_i_pct, lower (better) expected seed first.
# Picked expected_seed: on the k=1 snapshots (runs 3/5) it scored seed ±1
# 34.3% vs 32.9% for avg_wins — the comparison the modal 10.5/28.6 sat on.
# On current production (k=2.25) the ±1 ranking flips (avg_wins 34.3 vs 33.8).
SEED_RANK_AVG_WINS = "avg_wins"
SEED_RANK_EXPECTED = "expected_seed"
SEED_RANK_METHOD = SEED_RANK_EXPECTED


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


def _expected_seed(seed_pcts: tuple[float, ...]) -> float:
    """E[seed] up to a positive scale: sum over i of i * seed_i_pct."""
    return sum((i + 1) * pct for i, pct in enumerate(seed_pcts))


def _conference_of(team: str, season: str) -> str:
    conf = TEAM_CONFERENCE.get(team)
    if conf not in ("East", "West"):
        raise RuntimeError(
            f"{season}: team {team!r} does not resolve to East or West "
            f"(got {conf!r}). Aborting."
        )
    return conf


def assign_conference_seeds(
    preds: dict[str, EngineRow],
    season: str,
    method: str | None = None,
) -> dict[str, int]:
    """Assign seeds 1..15 within each conference. Never depends on row order.

    Ties break on team abbreviation ascending. Aborts if any team has no
    East/West mapping, or if a conference is not a permutation of 1..15.
    """
    method = method or SEED_RANK_METHOD
    if method not in (SEED_RANK_AVG_WINS, SEED_RANK_EXPECTED):
        raise RuntimeError(f"unknown seed rank method {method!r}")

    unresolved = sorted(
        team
        for team in preds
        if TEAM_CONFERENCE.get(team) not in ("East", "West")
    )
    if unresolved:
        raise RuntimeError(
            f"{season}: {len(unresolved)} team(s) do not resolve to East or "
            f"West: {', '.join(unresolved)}. Aborting."
        )

    assigned: dict[str, int] = {}
    for conf in ("East", "West"):
        teams = [team for team in preds if TEAM_CONFERENCE[team] == conf]
        if method == SEED_RANK_AVG_WINS:
            ordered = sorted(teams, key=lambda t: (-preds[t].avg_wins, t))
        else:
            ordered = sorted(
                teams, key=lambda t: (_expected_seed(preds[t].seed_pcts), t)
            )
        if len(ordered) != EXPECTED_TEAMS_PER_CONFERENCE:
            raise RuntimeError(
                f"{season} {conf}: {len(ordered)} team(s), expected "
                f"{EXPECTED_TEAMS_PER_CONFERENCE}. Aborting."
            )
        for seed, team in enumerate(ordered, start=1):
            assigned[team] = seed
        got = sorted(assigned[team] for team in teams)
        expected = list(range(1, EXPECTED_TEAMS_PER_CONFERENCE + 1))
        if got != expected:
            raise RuntimeError(
                f"{season} {conf}: seeds {got} are not {expected}. Aborting."
            )
    return assigned


def _duplicate_seed_count(assigned: dict[str, int], season: str) -> int:
    """Team-seasons sharing a seed with a conference rival. Must be 0."""
    dupes = 0
    for conf in ("East", "West"):
        seeds: dict[int, int] = {}
        for team, seed in assigned.items():
            if _conference_of(team, season) != conf:
                continue
            seeds[seed] = seeds.get(seed, 0) + 1
        dupes += sum(n for n in seeds.values() if n > 1)
    return dupes


def _champion_probs_from_engine(preds: dict[str, EngineRow]) -> dict[str, float]:
    return {team: row.champion_pct / 100.0 for team, row in preds.items()}


def compute_season_metrics(
    con: sqlite3.Connection,
    target_season: str,
    notes: str = "",
    run_id: str = RUN_ID,
    seed_rank_method: str | None = None,
) -> SeasonMetrics | None:
    preds = _fetch_engine_predictions(con, target_season, run_id=run_id)
    actual_by_id = _fetch_team_stats(con, target_season)
    actual_by_abbr = {row.team_abbr: row for row in actual_by_id.values()}

    common_abbrs = sorted(set(preds) & set(actual_by_abbr))
    if not common_abbrs:
        return None

    assigned = assign_conference_seeds(
        preds, target_season, method=seed_rank_method
    )

    # run_monte_carlo plays each season's real number of games, so avg_wins is
    # already on the target season's scale — rescaling here would shrink it a
    # second time. games_simulated is only the win-rate denominator.
    games_simulated = _games_simulated(con, target_season)

    win_errors: list[float] = []
    win_pct_errors: list[float] = []
    pred_rates: list[float] = []
    act_rates: list[float] = []
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
        pred_rates.append(pred.avg_wins / games_simulated)
        act_rates.append(act.win_pct)

        pred_seed = assigned[abbr]
        if act.conference_seed is not None:
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

    title_scores = {abbr: preds[abbr].champion_pct for abbr in common_abbrs}
    champion_rank = _title_rank(title_scores, actual_champion)

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
        win_order_r=_pearson_r(pred_rates, act_rates),
        champion_rank=champion_rank,
        notes=notes,
    )


def compute_pooled(
    con: sqlite3.Connection,
    all_metrics: dict[str, SeasonMetrics],
    run_id: str = RUN_ID,
    seed_rank_method: str | None = None,
) -> SeasonMetrics:
    """Micro-average for continuous metrics; macro % for champion hits.

    win_order_r is NOT the mean of per-season r: predicted and actual win
    rates are z-scored within each season, then correlated across the pool.
    champion_rank is the mean of the per-season ranks.
    """
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
    z_pred: list[float] = []
    z_act: list[float] = []
    champion_rank_sum = 0.0

    for target, metrics in all_metrics.items():
        preds = _fetch_engine_predictions(con, target, run_id=run_id)
        actual_by_id = _fetch_team_stats(con, target)
        actual_by_abbr = {row.team_abbr: row for row in actual_by_id.values()}
        common_abbrs = sorted(set(preds) & set(actual_by_abbr))
        games_simulated = _games_simulated(con, target)
        assigned = assign_conference_seeds(
            preds, target, method=seed_rank_method
        )
        pred_rates: list[float] = []
        act_rates: list[float] = []

        for abbr in common_abbrs:
            pred = preds[abbr]
            act = actual_by_abbr[abbr]
            total_abs_error += abs(pred.avg_wins - act.wins)
            total_abs_pct_error += (
                abs(pred.avg_wins / games_simulated - act.win_pct) * 100.0
            )
            pred_rates.append(pred.avg_wins / games_simulated)
            act_rates.append(act.win_pct)

            pred_seed = assigned[abbr]
            if act.conference_seed is not None:
                seed_scored += 1
                if pred_seed == act.conference_seed:
                    seed_exact += 1
                if abs(pred_seed - act.conference_seed) <= 1:
                    seed_pm1 += 1

            pred_playoffs = 1 if pred.make_playoffs_pct >= 50.0 else 0
            if pred_playoffs == act.made_playoffs:
                playoff_hits += 1
            total_teams += 1

        z_pred.extend(_zscore(pred_rates))
        z_act.extend(_zscore(act_rates))
        champion_rank_sum += metrics.champion_rank
        brier_sum += metrics.brier_champion * len(common_abbrs)

        if metrics.champion_top1 >= 100.0:
            champion_top1_hits += 1
        if metrics.champion_top4 >= 100.0:
            champion_top4_hits += 1

    season_list = ", ".join(all_metrics.keys())
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
        win_order_r=_pearson_r(z_pred, z_act) if z_pred else 0.0,
        champion_rank=(champion_rank_sum / n_seasons) if n_seasons else 0.0,
        notes=f"pooled over {n_seasons} seasons ({season_list}); run_id={run_id}",
    )


def compute_all_sections(
    con: sqlite3.Connection,
    run_id: str = RUN_ID,
    seed_rank_method: str | None = None,
) -> dict[str, SeasonMetrics]:
    """Per-season + all pooled sections for one prediction snapshot.

    Each pooled section is computed from the underlying team-seasons via
    compute_pooled (not by averaging season-level metric values).
    """
    season_metrics: dict[str, SeasonMetrics] = {}
    for season in ALL_SEASONS:
        notes = "caveated / partial data" if season == PARTIAL_SEASON else ""
        metrics = compute_season_metrics(
            con,
            season,
            notes=notes,
            run_id=run_id,
            seed_rank_method=seed_rank_method,
        )
        if metrics is None:
            print(f"[warn] skipping {season}: missing predictions or actuals")
            continue
        season_metrics[season] = metrics

    if not season_metrics:
        return {}

    pooled = compute_pooled(
        con, season_metrics, run_id=run_id, seed_rank_method=seed_rank_method
    )
    backtest = {s: m for s, m in season_metrics.items() if s != PARTIAL_SEASON}
    pooled_no_partial = compute_pooled(
        con, backtest, run_id=run_id, seed_rank_method=seed_rank_method
    )
    backtest_list = ", ".join(BACKTEST_SEASONS)
    pooled_no_partial.notes = (
        f"pooled over {len(BACKTEST_SEASONS)} full seasons "
        f"({backtest_list}); run_id={run_id}"
    )
    train = {s: m for s, m in season_metrics.items() if s in TRAIN_SEASONS}
    test = {s: m for s, m in season_metrics.items() if s in TEST_SEASONS}
    pooled_train = compute_pooled(
        con, train, run_id=run_id, seed_rank_method=seed_rank_method
    )
    pooled_test = compute_pooled(
        con, test, run_id=run_id, seed_rank_method=seed_rank_method
    )
    pooled_train.notes = (
        f"pooled over {len(TRAIN_SEASONS)} train seasons "
        f"({', '.join(TRAIN_SEASONS)}); run_id={run_id}"
    )
    pooled_test.notes = (
        f"pooled over {len(TEST_SEASONS)} holdout seasons "
        f"({', '.join(TEST_SEASONS)}); run_id={run_id}"
    )
    return {
        **season_metrics,
        "POOLED": pooled,
        "POOLED_NO_1819": pooled_no_partial,
        "POOLED_TRAIN": pooled_train,
        "POOLED_TEST": pooled_test,
    }


def count_duplicate_seed_assignments(
    con: sqlite3.Connection,
    run_id: str = RUN_ID,
    seed_rank_method: str | None = None,
    seasons: list[str] | None = None,
) -> int:
    """Team-seasons sharing a conference seed. Conference rank must yield 0."""
    total = 0
    for season in seasons or BACKTEST_SEASONS:
        preds = _fetch_engine_predictions(con, season, run_id=run_id)
        if not preds:
            continue
        assigned = assign_conference_seeds(
            preds, season, method=seed_rank_method
        )
        total += _duplicate_seed_count(assigned, season)
    return total


def _snapshot_skip_reason(
    con: sqlite3.Connection,
    run_id: str,
    seasons: list[str] | None = None,
) -> str | None:
    """None if the frozen snapshot is complete; otherwise why to skip.

    When `seasons` is set, only those seasons are required (extra seasons in
    the snapshot are allowed). The default still requires every ALL_SEASONS
    row and rejects unexpected seasons.
    """
    needed = list(seasons) if seasons is not None else list(ALL_SEASONS)
    rows = con.execute(
        """
        SELECT season, COUNT(*) AS n, COUNT(DISTINCT team) AS teams
        FROM simulation_results
        WHERE run_id = ?
        GROUP BY season
        """,
        (run_id,),
    ).fetchall()
    if not rows:
        return "no snapshot in simulation_results"
    by_season = {season: (n, teams) for season, n, teams in rows}
    missing = [season for season in needed if season not in by_season]
    if missing:
        return f"missing season(s): {', '.join(missing)}"
    if seasons is None:
        extra = sorted(set(by_season) - set(ALL_SEASONS))
        if extra:
            return f"unexpected season(s): {', '.join(extra)}"
    incomplete = [
        f"{season} has {n} row(s)/{teams} team(s), expected "
        f"{EXPECTED_SNAPSHOT_TEAMS}"
        for season in needed
        for n, teams in [by_season[season]]
        if n != EXPECTED_SNAPSHOT_TEAMS or teams != EXPECTED_SNAPSHOT_TEAMS
    ]
    if incomplete:
        return "incomplete: " + "; ".join(incomplete)
    seed_null_sql = " OR ".join(f"{col} IS NULL" for col in SEED_COLS)
    placeholders = ", ".join("?" for _ in needed)
    nulls = con.execute(
        f"""
        SELECT COUNT(1) FROM simulation_results
        WHERE run_id = ?
          AND season IN ({placeholders})
          AND (avg_wins IS NULL OR {seed_null_sql})
        """,
        (run_id, *needed),
    ).fetchone()[0]
    if nulls:
        return f"{nulls} row(s) missing avg_wins or a seed_i_pct column"
    return None


def compare_seed_rank_methods(run_id: str = RUN_ID) -> None:
    """Print POOLED_NO_1819 seed exact / ±1 for both ranking keys."""
    con = sqlite3.connect(DB_PATH)
    try:
        print(f"\n=== Seed rank key comparison (run_id={run_id!r}) ===\n")
        print(
            f"{'method':<16} {'exact %':>10} {'pm1 %':>10} "
            f"{'dupes (backtest)':>18}"
        )
        for method in (SEED_RANK_AVG_WINS, SEED_RANK_EXPECTED):
            sections = compute_all_sections(
                con, run_id=run_id, seed_rank_method=method
            )
            pooled = sections["POOLED_NO_1819"]
            dupes = count_duplicate_seed_assignments(
                con, run_id=run_id, seed_rank_method=method
            )
            print(
                f"{method:<16} {pooled.seed_accuracy_exact:10.1f} "
                f"{pooled.seed_accuracy_pm1:10.1f} {dupes:18d}"
            )
    finally:
        con.close()


def rescore_historical_seed_metrics() -> None:
    """Rewrite seed_accuracy_* in run_scores from each complete snapshot.

    Deliberate exception to run_scores being append-only: the old modal seed
    was not a valid permutation, so historical seed metrics are not comparable
    to the conference-rank definition. Every other metric is left untouched.
    """
    con = sqlite3.connect(DB_PATH, isolation_level=None)
    skipped: list[tuple[int, str]] = []
    changed: list[tuple[int, str, str, float, float]] = []
    before_all: dict[tuple[int, str, str], float] = {}
    try:
        run_ids = [
            int(row[0]) for row in con.execute("SELECT id FROM runs ORDER BY id")
        ]
        for row in con.execute("SELECT run, season, metric, value FROM run_scores"):
            before_all[(int(row[0]), str(row[1]), str(row[2]))] = float(row[3])

        con.execute("BEGIN IMMEDIATE")
        for run in run_ids:
            tag = str(run)
            reason = _snapshot_skip_reason(con, tag)
            if reason:
                skipped.append((run, reason))
                continue
            sections = compute_all_sections(con, run_id=tag)
            for season, metrics in sections.items():
                for metric in SEED_METRICS:
                    new_val = float(getattr(metrics, metric))
                    old = before_all.get((run, season, metric))
                    cur = con.execute(
                        "UPDATE run_scores SET value = ? "
                        "WHERE run = ? AND season = ? AND metric = ?",
                        (new_val, run, season, metric),
                    )
                    if cur.rowcount != 1:
                        raise RuntimeError(
                            f"run {run} {season} {metric}: UPDATE touched "
                            f"{cur.rowcount} row(s), expected 1."
                        )
                    if old is None:
                        raise RuntimeError(
                            f"run {run} {season} {metric}: no existing "
                            "run_scores row to update."
                        )
                    changed.append((run, season, metric, old, new_val))
        con.execute("COMMIT")

        after_all = {
            (int(row[0]), str(row[1]), str(row[2])): float(row[3])
            for row in con.execute(
                "SELECT run, season, metric, value FROM run_scores"
            )
        }
    except Exception:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()

    print("\n=== Historical seed re-score (run_scores) ===\n")
    print(f"Ranking key: {SEED_RANK_METHOD}")
    if skipped:
        print("Skipped:")
        for run, reason in skipped:
            print(f"  run {run}: {reason}")
    else:
        print("Skipped: none")

    print(
        f"\n{'run':>4}  {'section':<16}  {'metric':<22}  "
        f"{'before':>10}  {'after':>10}  {'delta':>10}"
    )
    for run, season, metric, old, new in changed:
        print(
            f"{run:4d}  {season:<16}  {metric:<22}  "
            f"{old:10.4f}  {new:10.4f}  {new - old:10.4f}"
        )

    moved = [
        (key, before_all[key], after_all[key])
        for key in before_all
        if key[2] not in SEED_METRICS and before_all[key] != after_all.get(key)
    ]
    extra = [key for key in after_all if key not in before_all]
    missing = [key for key in before_all if key not in after_all]
    if moved or extra or missing:
        raise RuntimeError(
            f"non-seed run_scores changed: moved={moved} extra={extra} "
            f"missing={missing}"
        )
    print(
        "\nNon-seed run_scores values: unchanged "
        f"({sum(1 for k in before_all if k[2] not in SEED_METRICS)} rows)."
    )


def _refresh_run_vs_baseline_view(con: sqlite3.Connection) -> None:
    """Recreate the view so newly registered lower-is-better metrics apply.

    Uses single-statement execute (not executescript) so it is safe inside
    an open transaction.
    """
    con.execute("DROP VIEW IF EXISTS run_vs_baseline")
    con.execute(RUN_VS_BASELINE_VIEW_SQL)


def backfill_historical_ordering_metrics() -> None:
    """INSERT win_order_r and champion_rank into run_scores for each snapshot.

    Addition only: existing run_scores rows are never updated or deleted.
    Incomplete snapshots are skipped and reported.
    """
    con = sqlite3.connect(DB_PATH, isolation_level=None)
    skipped: list[tuple[int, str]] = []
    inserted: list[tuple[int, str, str, float]] = []
    before_all: dict[tuple[int, str, str], float] = {}
    try:
        run_ids = [
            int(row[0]) for row in con.execute("SELECT id FROM runs ORDER BY id")
        ]
        for row in con.execute("SELECT run, season, metric, value FROM run_scores"):
            before_all[(int(row[0]), str(row[1]), str(row[2]))] = float(row[3])

        con.executescript(CREATE_HISTORY_SQL)
        con.execute("BEGIN IMMEDIATE")
        _refresh_run_vs_baseline_view(con)
        for run in run_ids:
            tag = str(run)
            reason = _snapshot_skip_reason(con, tag)
            if reason:
                skipped.append((run, reason))
                continue
            sections = compute_all_sections(con, run_id=tag)
            for season, metrics in sections.items():
                for metric in ORDERING_METRICS:
                    key = (run, season, metric)
                    new_val = float(getattr(metrics, metric))
                    if key in before_all:
                        if before_all[key] != new_val:
                            raise RuntimeError(
                                f"run {run} {season} {metric}: row already "
                                f"exists with value {before_all[key]!r}, "
                                f"computed {new_val!r}."
                            )
                        continue
                    con.execute(
                        "INSERT INTO run_scores (run, season, metric, value) "
                        "VALUES (?, ?, ?, ?)",
                        (run, season, metric, new_val),
                    )
                    inserted.append((run, season, metric, new_val))
        con.execute("COMMIT")

        after_all = {
            (int(row[0]), str(row[1]), str(row[2])): float(row[3])
            for row in con.execute(
                "SELECT run, season, metric, value FROM run_scores"
            )
        }
    except Exception:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()

    print("\n=== Historical ordering-metric backfill (run_scores) ===\n")
    if skipped:
        print("Skipped:")
        for run, reason in skipped:
            print(f"  run {run}: {reason}")
    else:
        print("Skipped: none")

    print(f"\nInserted {len(inserted)} row(s).")
    print(
        f"{'run':>4}  {'section':<16}  {'metric':<16}  {'value':>10}"
    )
    for run, season, metric, value in inserted:
        print(f"{run:4d}  {season:<16}  {metric:<16}  {value:10.6f}")

    moved = [
        (key, before_all[key], after_all[key])
        for key in before_all
        if before_all[key] != after_all.get(key)
    ]
    missing = [key for key in before_all if key not in after_all]
    extra = [
        key
        for key in after_all
        if key not in before_all and key[2] not in ORDERING_METRICS
    ]
    if moved or extra or missing:
        raise RuntimeError(
            f"existing run_scores changed: moved={moved} extra={extra} "
            f"missing={missing}"
        )
    print(f"\nExisting run_scores values: unchanged ({len(before_all)} rows).")


HOLDOUT_SECTIONS = ("POOLED_TRAIN", "POOLED_TEST")
# Pooled values that are defined only from per-season metric values, so they
# can be rebuilt when a frozen snapshot is missing. Everything else needs
# team-season predictions (micro-average or a pooled Pearson r).
HOLDOUT_RECONSTRUCTABLE = ("champion_top1", "champion_top4", "champion_rank")


def _pooled_from_season_rows(
    per_season: dict[str, dict[str, float]],
    seasons: list[str],
    metric: str,
) -> float | None:
    """Exact reconstruction from stored per-season rows, or None.

    champion_top1 / champion_top4: macro % (share of seasons with a hit).
    champion_rank: mean of the per-season ranks.
    """
    if metric not in HOLDOUT_RECONSTRUCTABLE:
        return None
    values: list[float] = []
    for season in seasons:
        if season not in per_season or metric not in per_season[season]:
            return None
        values.append(per_season[season][metric])
    if not values:
        return None
    if metric in ("champion_top1", "champion_top4"):
        hits = sum(1 for value in values if value >= 100.0)
        return 100.0 * hits / len(values)
    return sum(values) / len(values)


def backfill_holdout_sections() -> None:
    """INSERT POOLED_TRAIN / POOLED_TEST into run_scores for every run.

    Complete snapshots are rescored from team predictions. Missing snapshots
    get only the metrics that reconstruct exactly from stored per-season
    rows; the rest are skipped with a warning. Existing rows are never
    updated or deleted.
    """
    con = sqlite3.connect(DB_PATH, isolation_level=None)
    skipped_metrics: list[tuple[int, str, str]] = []
    inserted: list[tuple[int, str, str, float]] = []
    before_all: dict[tuple[int, str, str], float] = {}
    try:
        run_ids = [
            int(row[0]) for row in con.execute("SELECT id FROM runs ORDER BY id")
        ]
        for row in con.execute("SELECT run, season, metric, value FROM run_scores"):
            before_all[(int(row[0]), str(row[1]), str(row[2]))] = float(row[3])

        con.executescript(CREATE_HISTORY_SQL)
        con.execute("BEGIN IMMEDIATE")
        _refresh_run_vs_baseline_view(con)
        for run in run_ids:
            tag = str(run)
            stored: dict[str, dict[str, float]] = {}
            for season, metric, value in con.execute(
                "SELECT season, metric, value FROM run_scores WHERE run = ?",
                (run,),
            ):
                stored.setdefault(str(season), {})[str(metric)] = float(value)

            snapshot_reason = _snapshot_skip_reason(
                con, tag, seasons=TRAIN_SEASONS + TEST_SEASONS
            )
            if snapshot_reason is None:
                sections = compute_all_sections(con, run_id=tag)
                for section in HOLDOUT_SECTIONS:
                    metrics = sections.get(section)
                    if metrics is None:
                        for metric in METRIC_LABELS:
                            skipped_metrics.append((run, section, metric))
                            print(
                                f"[warn] run {run} {section} {metric}: "
                                "section missing after recompute; skipped."
                            )
                        continue
                    for metric in METRIC_LABELS:
                        key = (run, section, metric)
                        new_val = float(getattr(metrics, metric))
                        if key in before_all:
                            if before_all[key] != new_val:
                                raise RuntimeError(
                                    f"run {run} {section} {metric}: row already "
                                    f"exists with value {before_all[key]!r}, "
                                    f"computed {new_val!r}."
                                )
                            continue
                        con.execute(
                            "INSERT INTO run_scores (run, season, metric, value) "
                            "VALUES (?, ?, ?, ?)",
                            (run, section, metric, new_val),
                        )
                        inserted.append((run, section, metric, new_val))
                continue

            print(
                f"[warn] run {run}: no usable snapshot ({snapshot_reason}); "
                "reconstructing only exact per-season metrics."
            )
            for section, seasons in (
                ("POOLED_TRAIN", TRAIN_SEASONS),
                ("POOLED_TEST", TEST_SEASONS),
            ):
                for metric in METRIC_LABELS:
                    key = (run, section, metric)
                    rebuilt = _pooled_from_season_rows(stored, seasons, metric)
                    if rebuilt is None:
                        skipped_metrics.append((run, section, metric))
                        print(
                            f"[warn] run {run} {section} {metric}: not exactly "
                            "reconstructable from stored per-season rows; skipped."
                        )
                        continue
                    if key in before_all:
                        if before_all[key] != rebuilt:
                            raise RuntimeError(
                                f"run {run} {section} {metric}: row already "
                                f"exists with value {before_all[key]!r}, "
                                f"reconstructed {rebuilt!r}."
                            )
                        continue
                    con.execute(
                        "INSERT INTO run_scores (run, season, metric, value) "
                        "VALUES (?, ?, ?, ?)",
                        (run, section, metric, rebuilt),
                    )
                    inserted.append((run, section, metric, rebuilt))
        con.execute("COMMIT")

        after_all = {
            (int(row[0]), str(row[1]), str(row[2])): float(row[3])
            for row in con.execute(
                "SELECT run, season, metric, value FROM run_scores"
            )
        }
    except Exception:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()

    print("\n=== Holdout-section backfill (run_scores) ===\n")
    print(f"Inserted {len(inserted)} row(s).")
    print(f"{'run':>4}  {'section':<16}  {'metric':<22}  {'value':>10}")
    for run, season, metric, value in inserted:
        print(f"{run:4d}  {season:<16}  {metric:<22}  {value:10.6f}")

    if skipped_metrics:
        print(f"\nSkipped {len(skipped_metrics)} run/section/metric(s):")
        for run, section, metric in skipped_metrics:
            print(f"  run {run} {section} {metric}")
    else:
        print("\nSkipped metrics: none")

    moved = [
        (key, before_all[key], after_all[key])
        for key in before_all
        if before_all[key] != after_all.get(key)
    ]
    missing = [key for key in before_all if key not in after_all]
    extra = [
        key
        for key in after_all
        if key not in before_all and key[1] not in HOLDOUT_SECTIONS
    ]
    if moved or extra or missing:
        raise RuntimeError(
            f"existing run_scores changed: moved={moved} extra={extra} "
            f"missing={missing}"
        )
    print(
        f"\nExisting run_scores values: unchanged ({len(before_all)} rows). "
        f"Rows moved: {len(moved)}."
    )


def write_engine_scores(rows: list[tuple[str, str, float, str]]) -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("PRAGMA foreign_keys = ON;")
        con.execute(CREATE_TABLE_SQL)
        _refresh_run_vs_baseline_view(con)
        con.execute("DELETE FROM engine_scores")
        con.executemany(
            "INSERT INTO engine_scores (season, metric, value, notes) VALUES (?, ?, ?, ?)",
            rows,
        )
        con.commit()
    finally:
        con.close()


def _git_commit_short() -> str | None:
    """Short HEAD hash, or None when git is unavailable. Never fatal."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _seasons_label() -> str:
    """e.g. '2018-19..2025-26 (8)' — the seasons this run scored."""
    return f"{ALL_SEASONS[0]}..{ALL_SEASONS[-1]} ({len(ALL_SEASONS)})"


def _expected_score_rows() -> int:
    """Every scored section times every metric."""
    return (len(ALL_SEASONS) + len(POOLED_SECTIONS)) * len(METRIC_LABELS)


def _snapshot_predictions(con: sqlite3.Connection, run: int) -> int:
    """Freeze the production predictions under run_id=str(run).

    The 'production' rows are copied, never moved: app.py keeps reading them.
    """
    tag = str(run)
    clash = con.execute(
        "SELECT COUNT(1) FROM simulation_results WHERE run_id = ?", (tag,)
    ).fetchone()[0]
    if clash:
        raise RuntimeError(
            f"simulation_results already has {clash} row(s) at run_id={tag!r}; "
            "refusing to overwrite a previous snapshot."
        )

    cols = [r[1] for r in con.execute("PRAGMA table_info(simulation_results)")]
    if "run_id" not in cols:
        raise RuntimeError("simulation_results has no run_id column.")
    selected = ", ".join("?" if c == "run_id" else c for c in cols)
    con.execute(
        f"INSERT INTO simulation_results ({', '.join(cols)}) "
        f"SELECT {selected} FROM simulation_results WHERE run_id = ?",
        (tag, RUN_ID),
    )

    copied = con.execute(
        "SELECT COUNT(1) FROM simulation_results WHERE run_id = ?", (tag,)
    ).fetchone()[0]
    source = con.execute(
        "SELECT COUNT(1) FROM simulation_results WHERE run_id = ?", (RUN_ID,)
    ).fetchone()[0]
    if copied != source:
        raise RuntimeError(
            f"snapshot copied {copied} row(s) but production has {source}."
        )
    return copied


def _previous_run_scores(
    con: sqlite3.Connection, run: int, section: str
) -> dict[str, float] | None:
    """Scores from the highest run id below `run`, or None if this is the first."""
    prev = con.execute(
        "SELECT MAX(run) FROM run_scores WHERE run < ?", (run,)
    ).fetchone()[0]
    if prev is None:
        return None
    rows = con.execute(
        "SELECT metric, value FROM run_scores WHERE run = ? AND season = ?",
        (prev, section),
    ).fetchall()
    return {m: float(v) for m, v in rows} or None


def _format_experiment_entry(
    run: int,
    date: str,
    commit: str | None,
    note: str,
    engine: dict[str, float],
    baseline: dict[str, float],
    previous: dict[str, float] | None,
    section: str = "POOLED_NO_1819",
) -> str:
    header = f"## Run {run} — {date}"
    if commit:
        header += f" — {commit}"

    lines = [header, note.strip(), "", f"{section} (vs baseline):"]
    for key, label in METRIC_LABELS.items():
        if key not in engine:
            continue
        value = _format_metric(key, engine[key])
        parts = [f"  {label:<18}{value:>8}"]
        if previous and key in previous:
            delta = engine[key] - previous[key]
            parts.append(
                f"   (prev run {_format_metric(key, previous[key])}, "
                f"{'+' if delta > 0 else ''}{_format_metric(key, delta)})"
            )
        if key in baseline:
            parts.append(f"   baseline {_format_metric(key, baseline[key])}")
            parts.append(f"   {_winner(key, baseline[key], engine[key])}")
        lines.append("".join(parts))
    return "\n".join(lines) + "\n"


def _append_experiments(entry: str) -> None:
    """Append one entry, leaving a blank line between it and whatever came
    before. Existing content is never read back in, rewritten or reordered."""
    with open(EXPERIMENTS_PATH, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        tail = b""
        if handle.tell():
            handle.seek(max(0, handle.tell() - 2))
            tail = handle.read()
    separator = "" if tail.endswith(b"\n\n") else "\n" if tail.endswith(b"\n") else "\n\n"
    with open(EXPERIMENTS_PATH, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(separator + entry)


def log_run(note: str, rows: list[tuple[str, str, float, str]]) -> int:
    """Append one run to the history, or leave everything untouched.

    The markdown entry is written before the commit so that a failure anywhere
    rolls back both the database and the file.
    """
    expected = _expected_score_rows()
    if len(rows) != expected:
        raise RuntimeError(
            f"expected {expected} score values "
            f"({len(ALL_SEASONS) + len(POOLED_SECTIONS)} sections "
            f"x {len(METRIC_LABELS)} metrics) but got {len(rows)}; refusing to log."
        )

    doc_size = os.path.getsize(EXPERIMENTS_PATH)
    con = sqlite3.connect(DB_PATH, isolation_level=None)
    appended = False
    try:
        con.executescript(CREATE_HISTORY_SQL)
        con.execute("BEGIN IMMEDIATE")
        _refresh_run_vs_baseline_view(con)
        cursor = con.execute(
            "INSERT INTO runs (created_at, note, seasons, git_commit) "
            "VALUES (datetime('now'), ?, ?, ?)",
            (note.strip(), _seasons_label(), _git_commit_short()),
        )
        run = int(cursor.lastrowid)

        con.executemany(
            "INSERT INTO run_scores (run, season, metric, value) VALUES (?, ?, ?, ?)",
            [(run, season, metric, value) for season, metric, value, _ in rows],
        )
        written = con.execute(
            "SELECT COUNT(1) FROM run_scores WHERE run = ?", (run,)
        ).fetchone()[0]
        if written != expected:
            raise RuntimeError(f"wrote {written} run_scores rows, expected {expected}.")

        snapshot = _snapshot_predictions(con, run)

        created_at, commit = con.execute(
            "SELECT created_at, git_commit FROM runs WHERE id = ?", (run,)
        ).fetchone()
        section = "POOLED_NO_1819"
        engine_section = {
            metric: value for season, metric, value, _ in rows if season == section
        }
        baseline_section = {
            metric: float(value)
            for metric, value in con.execute(
                "SELECT metric, value FROM baseline_scores WHERE season = ?", (section,)
            ).fetchall()
        }
        entry = _format_experiment_entry(
            run=run,
            date=str(created_at).split(" ")[0],
            commit=commit,
            note=note,
            engine=engine_section,
            baseline=baseline_section,
            previous=_previous_run_scores(con, run, section),
            section=section,
        )
        _append_experiments(entry)
        appended = True

        con.execute("COMMIT")
    except Exception:
        if con.in_transaction:
            con.execute("ROLLBACK")
        if appended:
            with open(EXPERIMENTS_PATH, "r+b") as handle:
                handle.truncate(doc_size)
        raise
    finally:
        con.close()

    print(
        f"\nLogged run {run}: {expected} score values, "
        f"{snapshot} prediction rows frozen at run_id='{run}', "
        f"entry appended to {os.path.relpath(EXPERIMENTS_PATH, os.path.dirname(DB_PATH))}."
    )
    return run


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
    if key == "win_order_r":
        return f"{value:.3f}"
    if key == "champion_rank":
        return f"{value:.2f}"
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
        elif section == "POOLED_TRAIN":
            label = f"POOLED_TRAIN ({', '.join(TRAIN_SEASONS)})"
        elif section == "POOLED_TEST":
            label = f"POOLED_TEST holdout ({', '.join(TEST_SEASONS)})"

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


def main(note: str | None = None) -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    con = sqlite3.connect(DB_PATH)
    try:
        sections = compute_all_sections(con)
        dupes = count_duplicate_seed_assignments(con)
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
        baseline_train = {
            s: m for s, m in baseline_backtest_metrics.items() if s in TRAIN_SEASONS
        }
        baseline_test = {
            s: m for s, m in baseline_backtest_metrics.items() if s in TEST_SEASONS
        }
        baseline_pooled_train = compute_baseline_pooled(con, baseline_train)
        baseline_pooled_test = compute_baseline_pooled(con, baseline_test)
        baseline_scores["POOLED_TRAIN"] = {
            k: _metric_value(baseline_pooled_train, k) for k in METRIC_LABELS
        }
        baseline_scores["POOLED_TEST"] = {
            k: _metric_value(baseline_pooled_test, k) for k in METRIC_LABELS
        }
    finally:
        con.close()

    if dupes:
        raise RuntimeError(
            f"conference ranking produced {dupes} duplicate seed assignment(s) "
            "on backtest seasons; aborting."
        )
    print(
        f"Seed rank method={SEED_RANK_METHOD}; "
        f"duplicate assignments on backtest seasons: {dupes}"
    )

    season_metrics = {
        season: metrics
        for season, metrics in sections.items()
        if season not in POOLED_SECTIONS
    }
    pooled = sections["POOLED"]
    pooled_no_partial = sections["POOLED_NO_1819"]
    pooled_train = sections["POOLED_TRAIN"]
    pooled_test = sections["POOLED_TEST"]

    rows: list[tuple[str, str, float, str]] = []
    for season, metrics in season_metrics.items():
        rows.extend(_metrics_to_rows(season, metrics))
    rows.extend(_metrics_to_rows("POOLED", pooled))
    rows.extend(_metrics_to_rows("POOLED_NO_1819", pooled_no_partial))
    rows.extend(_metrics_to_rows("POOLED_TRAIN", pooled_train))
    rows.extend(_metrics_to_rows("POOLED_TEST", pooled_test))

    write_engine_scores(rows)

    engine_scores = {
        season: {k: _metric_value(m, k) for k in METRIC_LABELS}
        for season, m in {
            **season_metrics,
            "POOLED": pooled,
            "POOLED_NO_1819": pooled_no_partial,
            "POOLED_TRAIN": pooled_train,
            "POOLED_TEST": pooled_test,
        }.items()
    }

    sections = ALL_SEASONS + list(POOLED_SECTIONS)
    print_comparison(baseline_scores, engine_scores, sections)
    print(f"Wrote {len(rows)} rows to engine_scores in {DB_PATH}")

    if note is not None:
        log_run(note, rows)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Score engine predictions against actuals and the naive baseline."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--note",
        help=(
            "Describe what changed and log this run to the permanent history "
            "(runs / run_scores / a frozen prediction snapshot / EXPERIMENTS.md)."
        ),
    )
    group.add_argument(
        "--no-log",
        action="store_true",
        help="Score and print only, writing no history record. This is the default.",
    )
    parser.add_argument(
        "--compare-seed-keys",
        action="store_true",
        help=(
            "Print seed exact / ±1 for avg_wins vs expected-seed ranking "
            "and exit without writing."
        ),
    )
    parser.add_argument(
        "--rescore-history",
        action="store_true",
        help=(
            "Rewrite seed_accuracy_exact and seed_accuracy_pm1 in run_scores "
            "from each complete frozen snapshot. Other metrics are untouched."
        ),
    )
    parser.add_argument(
        "--backfill-ordering-metrics",
        action="store_true",
        help=(
            "INSERT win_order_r and champion_rank into run_scores for each "
            "complete frozen snapshot. Existing rows are never updated."
        ),
    )
    parser.add_argument(
        "--backfill-holdout-sections",
        action="store_true",
        help=(
            "INSERT POOLED_TRAIN and POOLED_TEST into run_scores for each "
            "run. Existing rows are never updated."
        ),
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    if args.note is not None and not args.note.strip():
        print(
            "[error] --note cannot be empty. Describe what changed, or omit "
            "--note to score without logging.",
            file=sys.stderr,
        )
        sys.exit(2)
    if args.compare_seed_keys:
        compare_seed_rank_methods()
        sys.exit(0)
    if args.rescore_history and args.note is not None:
        print(
            "[error] --rescore-history cannot be combined with --note.",
            file=sys.stderr,
        )
        sys.exit(2)
    if args.backfill_ordering_metrics and args.note is not None:
        print(
            "[error] --backfill-ordering-metrics cannot be combined with --note.",
            file=sys.stderr,
        )
        sys.exit(2)
    if args.backfill_holdout_sections and args.note is not None:
        print(
            "[error] --backfill-holdout-sections cannot be combined with --note.",
            file=sys.stderr,
        )
        sys.exit(2)
    if args.rescore_history:
        rescore_historical_seed_metrics()
    if args.backfill_ordering_metrics:
        backfill_historical_ordering_metrics()
    if args.backfill_holdout_sections:
        backfill_holdout_sections()
    main(note=args.note)
