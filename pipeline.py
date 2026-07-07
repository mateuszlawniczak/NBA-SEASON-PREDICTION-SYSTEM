"""
pipeline.py — season-parameterized orchestrator for EYEonPAPER.

Usage:
    py pipeline.py --season 2025-26
    py pipeline.py --season 2025-26 --stage projection
    py pipeline.py --season 2025-26 --stage simulation
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from season_utils import SeasonPair, parse_season_pair

import apply_pedigree_trajectory_boost
import apply_playoff_experience_pr
import apply_progression
import build_composite_clutch_index
import build_player_durability_profiles
import build_projected_team_pr_25_26
import build_team_playoff_pr_25_26
import build_ultimate_pr
import calculate_final_simulation_pr
import calculate_player_pr
import calculate_rookie_projected_pr_25_26
import calculate_ultimate_playoff_pr
import create_player_positions
import fetch_team_coaches_25_26
import run_monte_carlo
import update_ultimate_pr_positions

Step = tuple[str, Callable[..., None], str]

FEATURES: list[Step] = [
    ("create_player_positions", create_player_positions.main, "features"),
    ("fetch_team_coaches", fetch_team_coaches_25_26.main, "features"),
    ("calculate_rookie_projected_pr", calculate_rookie_projected_pr_25_26.main, "features"),
    ("build_composite_clutch_index", build_composite_clutch_index.main, "features"),
]

PROJECTION: list[Step] = [
    ("calculate_player_pr", calculate_player_pr.main, "projection"),
    ("apply_progression", apply_progression.main, "projection"),
    ("apply_playoff_experience_pr", apply_playoff_experience_pr.main, "projection"),
    ("calculate_final_simulation_pr", calculate_final_simulation_pr.main, "projection"),
    ("build_ultimate_pr", build_ultimate_pr.main, "projection"),
    ("update_ultimate_pr_positions", update_ultimate_pr_positions.main, "projection"),
    ("calculate_ultimate_playoff_pr", calculate_ultimate_playoff_pr.main, "projection"),
    ("apply_pedigree_trajectory_boost", apply_pedigree_trajectory_boost.main, "projection"),
    ("build_player_durability_profiles", build_player_durability_profiles.main, "projection"),
    ("build_projected_team_pr", build_projected_team_pr_25_26.main, "projection"),
    ("build_team_playoff_pr", build_team_playoff_pr_25_26.main, "projection"),
]

SIMULATION: list[Step] = [
    ("run_monte_carlo", run_monte_carlo.main, "simulation"),
]

STAGES: dict[str, list[Step]] = {
    "features": FEATURES,
    "projection": PROJECTION,
    "simulation": SIMULATION,
}


def _run_step(name: str, fn: Callable[..., None], pair: SeasonPair) -> None:
    print(f"\n=== [{name}] season target={pair.target!r} source={pair.source!r} ===", flush=True)
    fn(source_season=pair.source, target_season=pair.target)


def main() -> None:
    parser = argparse.ArgumentParser(description="EYEonPAPER season pipeline")
    parser.add_argument("--season", required=True, help="Target season, e.g. 2025-26")
    parser.add_argument(
        "--stage",
        default="all",
        choices=["all", "features", "projection", "simulation"],
        help="Pipeline stage to run (default: all)",
    )
    args = parser.parse_args()
    pair = parse_season_pair(args.season)

    if args.stage == "all":
        steps = FEATURES + PROJECTION + SIMULATION
    else:
        steps = STAGES[args.stage]

    for name, fn, _stage in steps:
        try:
            _run_step(name, fn, pair)
        except Exception as exc:
            print(f"\n[pipeline] FAILED at step {name!r}: {exc}", file=sys.stderr, flush=True)
            raise SystemExit(1) from exc

    print(f"\n[pipeline] Completed stage={args.stage!r} for season {pair.target!r}.")


if __name__ == "__main__":
    main()
