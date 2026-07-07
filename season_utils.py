"""
Shared season parsing for the EYEonPAPER pipeline.

``--season`` is the **target** (predicted) season. ``source_season`` is the prior
completed season (target minus one year).
"""

from __future__ import annotations

import argparse
from typing import NamedTuple


class SeasonPair(NamedTuple):
    source: str
    target: str


def source_season(target: str) -> str:
    """Derive source season from target, e.g. 2025-26 -> 2024-25."""
    start = int(target.split("-")[0])
    return f"{start - 1}-{str(start)[2:]}"


def prior_source_season(source: str) -> str:
    """Season before source, e.g. 2024-25 -> 2023-24."""
    return source_season(source)


def trailing_three_seasons(source: str) -> list[str]:
    """Three-year window ending at source season (inclusive)."""
    s2 = prior_source_season(source)
    s1 = prior_source_season(s2)
    return [s1, s2, source]


def parse_season_pair(target: str) -> SeasonPair:
    return SeasonPair(source=source_season(target), target=target)


def add_season_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--season",
        required=True,
        help="Target season to project/simulate (e.g. 2025-26).",
    )


def parse_cli_seasons(argv: list[str] | None = None) -> SeasonPair:
    parser = argparse.ArgumentParser(add_help=False)
    add_season_argument(parser)
    args, _ = parser.parse_known_args(argv)
    return parse_season_pair(args.season)


def season_arg_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    add_season_argument(parser)
    return parser
