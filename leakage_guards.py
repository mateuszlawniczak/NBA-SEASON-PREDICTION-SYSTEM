"""
Leakage guards for backtest-safe season parameterization.

Each helper returns the same result as the pre-1b hardcoded 2025 facts when
``target_season == "2025-26"``, and uses only information knowable before the
target season for historical runs.
"""

from __future__ import annotations

# --- Finals MVP by season label (award announced at end of that season) ---
FINALS_MVP_BY_SEASON: dict[str, str] = {
    "2015-16": "LeBron James",
    "2016-17": "Kevin Durant",
    "2017-18": "Kevin Durant",
    "2018-19": "Kawhi Leonard",
    "2019-20": "LeBron James",
    "2020-21": "Giannis Antetokounmpo",
    "2021-22": "Stephen Curry",
    "2022-23": "Nikola Jokic",
    "2023-24": "Jaylen Brown",
    "2024-25": "Shai Gilgeous-Alexander",
}

# Pre-1b hardcoded FMVP list (2025-26 production set).
FMVPS_LEGACY_2025_26 = frozenset(FINALS_MVP_BY_SEASON.values())

# --- ROY / pedigree boosts: award season = season the honor was earned ---
ROY_PEDIGREE_AWARD_SEASON: dict[str, tuple[str, float]] = {
    "Victor Wembanyama": ("2023-24", 1.20),
    "Paolo Banchero": ("2022-23", 1.20),
    "Scottie Barnes": ("2021-22", 1.20),
    "LaMelo Ball": ("2020-21", 1.20),
    "Ja Morant": ("2019-20", 1.20),
    "Chet Holmgren": ("2022-23", 1.10),
    "Brandon Miller": ("2023-24", 1.10),
    "Jalen Williams": ("2023-24", 1.10),
    "Walker Kessler": ("2022-23", 1.10),
    "Evan Mobley": ("2021-22", 1.10),
    "Cade Cunningham": ("2021-22", 1.10),
    "Anthony Edwards": ("2020-21", 1.10),
    "Tyrese Haliburton": ("2020-21", 1.10),
}

# --- Summer coach overrides (2025 off-season only) ---
SUMMER_HIRES_2025_26: dict[str, str] = {
    "NYK": "Mike Brown",
}

def target_start_year(target_season: str) -> int:
    return int(target_season.split("-")[0])


def draft_year_for_target(target_season: str) -> int:
    return target_start_year(target_season)


def fmvp_names_before_target(target_season: str) -> frozenset[str]:
    """Finals MVPs from seasons strictly before the target season."""
    names = {
        name
        for season, name in FINALS_MVP_BY_SEASON.items()
        if season < target_season
    }
    return frozenset(names)


def pedigree_boosts_before_target(target_season: str) -> dict[str, float]:
    """ROY / podium boosts earned before the target season opens."""
    return {
        player: mult
        for player, (award_season, mult) in ROY_PEDIGREE_AWARD_SEASON.items()
        if award_season < target_season
    }


def summer_hires_for_target(target_season: str) -> dict[str, str]:
    if target_season == "2025-26":
        return SUMMER_HIRES_2025_26
    return {}

