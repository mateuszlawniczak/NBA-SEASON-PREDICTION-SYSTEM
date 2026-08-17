"""
run_all_seasons.py
------------------
One command for a full backtest: every scored season, then score and log.

    py run_all_seasons.py --note "cut pedigree weight 30%"

Reuses pipeline.py's FEATURES / PROJECTION / SIMULATION step lists, so a
change there is picked up automatically. FEATURES is off by default because
it hits the NBA API and does not change when a formula is tuned.

If any season fails, the batch stops immediately — no later seasons, no
score, no history row. Scoring a half-finished batch would mix two formulas
into one number.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from datetime import datetime

from season_utils import parse_season_pair

import compute_engine_scores
import pipeline
from compute_engine_scores import ALL_SEASONS
from run_monte_carlo import BT_EXPONENT, FATIGUE_ODDS, HOME_ODDS, MASTER_SEED

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nba_data.db")


def _die_mixed(season: str, err: BaseException, backup_path: str) -> None:
    print(f"\n[run_all_seasons] FAILED during season {season!r}.", file=sys.stderr)
    print(f"[run_all_seasons] Underlying error: {err}", file=sys.stderr)
    print(f"[run_all_seasons] Backup: {backup_path}", file=sys.stderr)
    print(
        "[run_all_seasons] WARNING: the database is now in a mixed state — "
        "some seasons may hold the new formula and others the old one. "
        "Restore from that backup before anything is scored. "
        "No history row was written.",
        file=sys.stderr,
    )
    sys.exit(1)


def _backup_db() -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(
        os.path.dirname(DB_PATH), f"nba_data.backup_batch_{stamp}.db"
    )
    shutil.copy2(DB_PATH, dest)
    print(f"[run_all_seasons] DB backup: {dest}", flush=True)
    return dest


def _stages(with_features: bool) -> list[pipeline.Step]:
    stages: list[pipeline.Step] = []
    if with_features:
        stages.extend(pipeline.FEATURES)
    stages.extend(pipeline.PROJECTION)
    stages.extend(pipeline.SIMULATION)
    return stages


def _run_step(
    name: str,
    fn,
    pair,
    seed: int,
    k: float,
    home_odds: float,
    fatigue_odds: float,
) -> None:
    print(
        f"\n=== [{name}] season target={pair.target!r} source={pair.source!r} ===",
        flush=True,
    )
    if name == "run_monte_carlo":
        fn(
            source_season=pair.source,
            target_season=pair.target,
            seed=seed,
            k=k,
            home_odds=home_odds,
            fatigue_odds=fatigue_odds,
        )
    else:
        fn(source_season=pair.source, target_season=pair.target)


def _logged_note(
    note: str,
    seed: int,
    k: float = BT_EXPONENT,
    home_odds: float = HOME_ODDS,
    fatigue_odds: float = FATIGUE_ODDS,
) -> str:
    extras: list[str] = []
    if seed != MASTER_SEED:
        extras.append(f"seed={seed}")
    if k != BT_EXPONENT:
        extras.append(f"k={k}")
    if home_odds != HOME_ODDS:
        extras.append(f"home_odds={home_odds}")
    if fatigue_odds != FATIGUE_ODDS:
        extras.append(f"fatigue_odds={fatigue_odds}")
    if not extras:
        return note
    return f"{note} [{', '.join(extras)}]"


def _print_plan(
    seasons: list[str],
    stages: list[pipeline.Step],
    note: str,
    seed: int,
    with_features: bool,
    k: float,
    home_odds: float,
    fatigue_odds: float,
) -> None:
    logged = _logged_note(note, seed, k, home_odds, fatigue_odds)
    print("[run_all_seasons] DRY RUN — nothing will be written.", flush=True)
    print(f"  seasons : {', '.join(seasons)}  ({len(seasons)})", flush=True)
    print(
        f"  stages  : {', '.join(name for name, _, _ in stages)}"
        f"{'  (includes FEATURES)' if with_features else '  (FEATURES off)'}",
        flush=True,
    )
    print(f"  seed    : {seed}" + ("  (default)" if seed == MASTER_SEED else ""), flush=True)
    print(f"  k       : {k}" + ("  (default)" if k == BT_EXPONENT else ""), flush=True)
    print(
        f"  home_odds : {home_odds}"
        + ("  (default)" if home_odds == HOME_ODDS else ""),
        flush=True,
    )
    print(
        f"  fatigue_odds : {fatigue_odds}"
        + ("  (default)" if fatigue_odds == FATIGUE_ODDS else ""),
        flush=True,
    )
    print(f"  note    : {logged}", flush=True)
    print(f"  then    : compute_engine_scores.py --note {logged!r}", flush=True)


def run_batch(
    note: str,
    seed: int = MASTER_SEED,
    with_features: bool = False,
    k: float = BT_EXPONENT,
    home_odds: float = HOME_ODDS,
    fatigue_odds: float = FATIGUE_ODDS,
) -> None:
    if not note or not note.strip():
        print(
            "[error] --note is required and cannot be empty. "
            "The batch will not start without a loggable description.",
            file=sys.stderr,
        )
        sys.exit(2)

    seasons = list(ALL_SEASONS)
    stages = _stages(with_features)
    logged = _logged_note(note.strip(), seed, k, home_odds, fatigue_odds)

    backup_path = _backup_db()
    batch_t0 = time.perf_counter()

    for i, season in enumerate(seasons, 1):
        pair = parse_season_pair(season)
        print(f"\n{'=' * 70}", flush=True)
        print(
            f"[run_all_seasons] {i}/{len(seasons)}  {season}  "
            f"({len(stages)} steps)",
            flush=True,
        )
        print(f"{'=' * 70}", flush=True)
        t0 = time.perf_counter()
        try:
            for name, fn, _stage in stages:
                _run_step(name, fn, pair, seed, k, home_odds, fatigue_odds)
        except (Exception, SystemExit) as exc:
            _die_mixed(season, exc, backup_path)
        elapsed = time.perf_counter() - t0
        print(
            f"\n[run_all_seasons] {season} finished in {elapsed:.1f}s.",
            flush=True,
        )

    total = time.perf_counter() - batch_t0
    print(f"\n[run_all_seasons] All {len(seasons)} seasons finished in {total:.1f}s.", flush=True)
    print(f"[run_all_seasons] Scoring and logging with note={logged!r} ...", flush=True)
    compute_engine_scores.main(note=logged)
    print(f"\n[run_all_seasons] DONE. Backup kept at {backup_path}", flush=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run every scored season through projection + simulation, "
            "then score and log the run."
        )
    )
    parser.add_argument(
        "--note",
        required=True,
        help="Description passed to compute_engine_scores.py --note. Required.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=MASTER_SEED,
        help=f"Master RNG seed for run_monte_carlo (default {MASTER_SEED}).",
    )
    parser.add_argument(
        "--k",
        type=float,
        default=BT_EXPONENT,
        help=f"Bradley-Terry exponent (default {BT_EXPONENT}).",
    )
    parser.add_argument(
        "--home-odds",
        type=float,
        default=HOME_ODDS,
        help=(
            "Home-advantage odds multiplier, applied outside the exponent "
            f"(default {HOME_ODDS})."
        ),
    )
    parser.add_argument(
        "--fatigue-odds",
        type=float,
        default=FATIGUE_ODDS,
        help=(
            "Away back-to-back fatigue odds multiplier, applied outside "
            f"the exponent (default {FATIGUE_ODDS})."
        ),
    )
    parser.add_argument(
        "--with-features",
        action="store_true",
        help="Also run the FEATURES stage (hits the NBA API). Off by default.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan and exit without touching the database.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args(argv)
    if not args.note.strip():
        print(
            "[error] --note is required and cannot be empty. "
            "The batch will not start without a loggable description.",
            file=sys.stderr,
        )
        sys.exit(2)
    stages = _stages(args.with_features)
    if args.dry_run:
        _print_plan(
            list(ALL_SEASONS),
            stages,
            args.note.strip(),
            args.seed,
            args.with_features,
            args.k,
            args.home_odds,
            args.fatigue_odds,
        )
        return
    run_batch(
        note=args.note,
        seed=args.seed,
        with_features=args.with_features,
        k=args.k,
        home_odds=args.home_odds,
        fatigue_odds=args.fatigue_odds,
    )


if __name__ == "__main__":
    main()
