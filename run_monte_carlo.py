"""
Monte Carlo simulator for the 2025–26 NBA season (Specification run_monte_carlo).

Reads roster and ratings from nba_data.db, runs repeated full-season + playoff
simulations, writes simulation_results_25_26, and prints validation output.

RS multipliers:
  coach_mult and playstyle_mult are derived from projected_team_pr_25_26
  (coach_grade -> multiplier map; playstyle -> playstyle_multipliers lookup).
  continuity_mult is read from projected_team_pr_25_26 when that column exists;
  otherwise it falls back to team_playoff_pr_25_26.continuity_mult (schema in repo).

PO multipliers: amplified_coach_mult, playstyle_mult, continuity_mult from
team_playoff_pr_25_26.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")
N_SIMULATIONS = 1000
RS_GAMES_PER_TEAM = 82
N_TEAMS = 30

COACH_GRADE_MULT: dict[str, float] = {
    "S": 1.08,
    "A": 1.04,
    "B": 1.02,
    "C": 1.00,
    "D": 0.97,
    "F": 0.95,
}
PLAYSTYLE_FALLBACK_MULT = 0.90

HOME_MULT = 1.04
AWAY_FATIGUE_MULT = 0.96
AWAY_FATIGUE_PROB = 0.135

STAR_BOOST_PO = 1.15
TOP_BOOSTED_N = 3

CONTINUITY_CUTOFF_GAME = 20

TEAM_CONFERENCE: dict[str, str] = {
    "ATL": "East",
    "BKN": "East",
    "BOS": "East",
    "CHA": "East",
    "CHI": "East",
    "CLE": "East",
    "DET": "East",
    "IND": "East",
    "MIA": "East",
    "MIL": "East",
    "NYK": "East",
    "ORL": "East",
    "PHI": "East",
    "TOR": "East",
    "WAS": "East",
    "DAL": "West",
    "DEN": "West",
    "GSW": "West",
    "HOU": "West",
    "LAC": "West",
    "LAL": "West",
    "MEM": "West",
    "MIN": "West",
    "NOP": "West",
    "OKC": "West",
    "PHX": "West",
    "POR": "West",
    "SAC": "West",
    "SAS": "West",
    "UTA": "West",
}


@dataclass(frozen=True)
class PlayerRow:
    name: str
    pr_rs: float
    pr_po: float
    pos: str
    rs_dur: float
    po_dur: float


@dataclass
class TeamProfile:
    abbr: str
    conf: str
    coach_mult: float
    rs_playstyle_mult: float
    continuity_mult_rs: float
    amp_coach_mult: float
    po_playstyle_mult: float
    continuity_mult_po: float
    starters: tuple[PlayerRow, ...]
    bench: tuple[PlayerRow, ...]
    ninth: PlayerRow | None
    reserves: tuple[PlayerRow, ...]
    starters_po: tuple[PlayerRow, ...]
    bench_po: tuple[PlayerRow, ...]
    ninth_po: PlayerRow | None
    reserves_po: tuple[PlayerRow, ...]
    roster_all: tuple[PlayerRow, ...]


def _normalize_grade(raw: object) -> str | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = str(raw).strip().upper()
    return s if s in COACH_GRADE_MULT else None


def _coach_mult_from_grade(grade: object) -> float:
    g = _normalize_grade(grade)
    return COACH_GRADE_MULT[g] if g else 1.00


def _pr_key_rs(p: PlayerRow) -> tuple[float, str]:
    return (-p.pr_rs, p.name)


def _pr_key_po(p: PlayerRow) -> tuple[float, str]:
    return (-p.pr_po, p.name)


def stratify_depth(roster: list[PlayerRow], *, po: bool) -> tuple[list[PlayerRow], list[PlayerRow], PlayerRow | None, list[PlayerRow]]:
    key = _pr_key_po if po else _pr_key_rs

    def take_top_n(pos: str, n: int, pool: list[PlayerRow]) -> list[PlayerRow]:
        sub = [p for p in pool if p.pos == pos]
        sub.sort(key=key)
        return sub[:n]

    pool = list(roster)
    starters: list[PlayerRow] = []
    starters.extend(take_top_n("G", 2, pool))
    starters.extend(take_top_n("F", 2, pool))
    starters.extend(take_top_n("C", 1, pool))
    taken = {p.name for p in starters}
    rem = [p for p in pool if p.name not in taken]

    bench: list[PlayerRow] = []
    g1 = take_top_n("G", 1, rem)
    if g1:
        bench.append(g1[0])
        rem = [p for p in rem if p.name != g1[0].name]
    f1 = take_top_n("F", 1, rem)
    if f1:
        bench.append(f1[0])
        rem = [p for p in rem if p.name != f1[0].name]

    cands_c = [p for p in rem if p.pos == "C"]
    cands_f = [p for p in rem if p.pos == "F"]
    if cands_c:
        best = min(cands_c, key=key)
    elif cands_f:
        best = min(cands_f, key=key)
    else:
        best = None
    if best:
        bench.append(best)
        rem = [p for p in rem if p.name != best.name]

    ninth: PlayerRow | None = None
    if rem:
        ninth = min(rem, key=key)
        rem = [p for p in rem if p.name != ninth.name]

    by_pos: dict[str, list[PlayerRow]] = {"G": [], "F": [], "C": []}
    for p in rem:
        by_pos[p.pos].append(p)
    for lst in by_pos.values():
        lst.sort(key=key)
    reserves = by_pos["G"] + by_pos["F"] + by_pos["C"]
    return starters, bench, ninth, reserves


def _ensure_min_starters(
    starters: list[PlayerRow],
    alt_starters: list[PlayerRow],
    min_n: int,
) -> list[PlayerRow]:
    st_list = list(starters)
    k = 0
    while len(st_list) < min_n and k < len(alt_starters):
        if alt_starters[k] not in st_list:
            st_list.append(alt_starters[k])
        k += 1
    return st_list


def _pick_same_pos_then_best(pos: str, candidates: tuple[PlayerRow, ...], used: set[str], *, po: bool) -> PlayerRow | None:
    key = _pr_key_po if po else _pr_key_rs
    avail = [p for p in candidates if p.name not in used]
    same = [p for p in avail if p.pos == pos]
    if same:
        return min(same, key=key)
    if avail:
        return min(avail, key=key)
    return None


def _pick_best_any(candidates: tuple[PlayerRow, ...], used: set[str], *, po: bool) -> PlayerRow | None:
    key = _pr_key_po if po else _pr_key_rs
    avail = [p for p in candidates if p.name not in used]
    if not avail:
        return None
    return min(avail, key=key)


def _starter_replacement_pool(
    bench: tuple[PlayerRow, ...],
    reserves: tuple[PlayerRow, ...],
) -> tuple[PlayerRow, ...]:
    """Bench ∪ reserves (spec: replacements from Bench / Reserves)."""
    return bench + reserves


def build_nine_rs_daily(team: TeamProfile, rng: np.random.Generator) -> list[PlayerRow]:
    """
    Regular season: roll rs_durability only for the 5 starters; fill bench / 9th
    from the depth chart; if a slot is already used, substitute best available.
    No player appears twice.
    """
    starters = team.starters
    bench = team.bench
    ninth = team.ninth
    rep_pool = _starter_replacement_pool(team.bench, team.reserves)
    roster_all = team.roster_all

    used: set[str] = set()
    contributors: list[PlayerRow] = []

    for s in starters:
        if rng.random() < s.rs_dur:
            contributors.append(s)
            used.add(s.name)
        else:
            rep = _pick_same_pos_then_best(s.pos, rep_pool, used, po=False)
            if rep is None:
                rep = _pick_best_any(roster_all, used, po=False)
            if rep is not None:
                contributors.append(rep)
                used.add(rep.name)

    for b in bench:
        if b.name not in used:
            contributors.append(b)
            used.add(b.name)
        else:
            rep = _pick_best_any(roster_all, used, po=False)
            if rep is not None:
                contributors.append(rep)
                used.add(rep.name)

    if ninth:
        if ninth.name not in used:
            contributors.append(ninth)
            used.add(ninth.name)
        else:
            rep = _pick_best_any(roster_all, used, po=False)
            if rep is not None:
                contributors.append(rep)
                used.add(rep.name)

    while len(contributors) < 9:
        rep = _pick_best_any(roster_all, used, po=False)
        if rep is None:
            break
        contributors.append(rep)
        used.add(rep.name)

    return contributors[:9]


def sum_playoff_eight(pr_eight: list[PlayerRow]) -> float:
    """Top 3 playoff PR (among these 8) get STAR_BOOST_PO; rest straight sum."""
    ranked = sorted(pr_eight, key=lambda p: (-p.pr_po, p.name))
    n_b = min(TOP_BOOSTED_N, len(ranked))
    s = sum(p.pr_po * STAR_BOOST_PO for p in ranked[:n_b])
    s += sum(p.pr_po for p in ranked[n_b:])
    return float(s)


def freeze_nine_playoff_series(team: TeamProfile, rng: np.random.Generator) -> list[PlayerRow]:
    """
    Playoffs: roll po_durability once per nominal starter at series start;
    replacements persist for the series. Bench / 9th filled like RS (no extra roll).
    """
    starters = team.starters_po
    bench = team.bench_po
    ninth = team.ninth_po
    rep_pool = _starter_replacement_pool(team.bench_po, team.reserves_po)
    roster_all = team.roster_all

    used: set[str] = set()
    contributors: list[PlayerRow] = []

    for s in starters:
        if rng.random() < s.po_dur:
            contributors.append(s)
            used.add(s.name)
        else:
            rep = _pick_same_pos_then_best(s.pos, rep_pool, used, po=True)
            if rep is None:
                rep = _pick_best_any(roster_all, used, po=True)
            if rep is not None:
                contributors.append(rep)
                used.add(rep.name)

    for b in bench:
        if b.name not in used:
            contributors.append(b)
            used.add(b.name)
        else:
            rep = _pick_best_any(roster_all, used, po=True)
            if rep is not None:
                contributors.append(rep)
                used.add(rep.name)

    if ninth:
        if ninth.name not in used:
            contributors.append(ninth)
            used.add(ninth.name)
        else:
            rep = _pick_best_any(roster_all, used, po=True)
            if rep is not None:
                contributors.append(rep)
                used.add(rep.name)

    while len(contributors) < 9:
        rep = _pick_best_any(roster_all, used, po=True)
        if rep is None:
            break
        contributors.append(rep)
        used.add(rep.name)

    return contributors[:9]


def playoff_base_pr_sum_series(team: TeamProfile, rng: np.random.Generator) -> float:
    nine = freeze_nine_playoff_series(team, rng)
    ranked = sorted(nine, key=lambda p: (-p.pr_po, p.name))[:8]
    if not ranked:
        return 0.0
    return sum_playoff_eight(ranked)


def rating_rs(team: TeamProfile, team_game_number: int, rng: np.random.Generator) -> float:
    nine = build_nine_rs_daily(team, rng)
    base = float(sum(p.pr_rs for p in nine))
    cont = team.continuity_mult_rs if team_game_number <= CONTINUITY_CUTOFF_GAME else 1.0
    return base * team.coach_mult * team.rs_playstyle_mult * cont


def rating_po_series(team: TeamProfile, rng: np.random.Generator) -> float:
    base = playoff_base_pr_sum_series(team, rng)
    return base * team.amp_coach_mult * team.po_playstyle_mult * team.continuity_mult_po


def p_win_home(
    rating_home: float,
    rating_away: float,
    away_fatigue: bool,
    rng: np.random.Generator,
) -> bool:
    rh = rating_home * HOME_MULT
    ra = rating_away * (AWAY_FATIGUE_MULT if away_fatigue else 1.0)
    denom = rh + ra
    if denom <= 0:
        return rng.random() < 0.5
    return (rng.random() * denom) < rh


def simulate_series_po(
    ia: int,
    ib: int,
    seed_a: int,
    seed_b: int,
    profiles: list[TeamProfile],
    rng: np.random.Generator,
    losers_exit_level: dict[int, int],
    level: int,
) -> int:
    """Best-of-7; seed integers are 1-based (lower is better). Returns winner index."""
    if seed_a < seed_b:
        hi, lo = ia, ib
    else:
        hi, lo = ib, ia

    r_hi = rating_po_series(profiles[hi], rng)
    r_lo = rating_po_series(profiles[lo], rng)

    w_hi = w_lo = 0
    a_home_games = {0, 1, 4, 6}
    g = 0
    while w_hi < 4 and w_lo < 4:
        away_fatigue = rng.random() < AWAY_FATIGUE_PROB
        if g in a_home_games:
            hi_won = p_win_home(r_hi, r_lo, away_fatigue, rng)
        else:
            hi_won = not p_win_home(r_lo, r_hi, away_fatigue, rng)
        if hi_won:
            w_hi += 1
        else:
            w_lo += 1
        g += 1

    if w_hi == 4:
        loser = lo
        winner = hi
    else:
        loser = hi
        winner = lo
    if losers_exit_level.get(loser, 0) < level:
        losers_exit_level[loser] = level
    return winner


def run_regular_season(
    profiles: list[TeamProfile],
    rng: np.random.Generator,
) -> np.ndarray:
    n = len(profiles)
    wins = np.zeros(n, dtype=np.int32)
    games_played = np.zeros(n, dtype=np.int32)

    for rnd in range(RS_GAMES_PER_TEAM):
        perm = rng.permutation(n)
        for k in range(0, n, 2):
            ia, ib = int(perm[k]), int(perm[k + 1])

            ha, hb = games_played[ia], games_played[ib]
            if ha > hb:
                hi, ai = ib, ia
            elif hb > ha:
                hi, ai = ia, ib
            else:
                if rng.random() < 0.5:
                    hi, ai = ia, ib
                else:
                    hi, ai = ib, ia

            games_played[hi] += 1
            games_played[ai] += 1

            away_fatigue = rng.random() < AWAY_FATIGUE_PROB

            r_h = rating_rs(profiles[hi], int(games_played[hi]), rng)
            r_a = rating_rs(profiles[ai], int(games_played[ai]), rng)
            if p_win_home(r_h, r_a, away_fatigue, rng):
                wins[hi] += 1
            else:
                wins[ai] += 1

    return wins


def conf_indices(abbrs: list[str], conf: str) -> list[int]:
    return [i for i, a in enumerate(abbrs) if TEAM_CONFERENCE[a] == conf]


def standings_order(wins: np.ndarray, conf_idxs: list[int], tiebreak: np.ndarray) -> list[int]:
    keyed = [(-wins[i], -tiebreak[i], i) for i in conf_idxs]
    keyed.sort()
    return [t[2] for t in keyed]


def sim_playin_neutral(ia: int, ib: int, profiles: list[TeamProfile], rng: np.random.Generator) -> int:
    """Neutral win probability from RS ratings with continuity multiplier off."""
    r_a = rating_rs(profiles[ia], CONTINUITY_CUTOFF_GAME + 1, rng)
    r_b = rating_rs(profiles[ib], CONTINUITY_CUTOFF_GAME + 1, rng)
    denom = r_a + r_b
    if denom <= 0:
        return ia if rng.random() < 0.5 else ib
    return ia if rng.random() * denom < r_a else ib


def play_in_conference(ordered: list[int], profiles: list[TeamProfile], rng: np.random.Generator) -> tuple[list[int], set[int]]:
    """
    ordered: 15 team indices best→worst by RS wins.
    Play-In: (7 vs 8) winner seed 7; (9 vs 10) loser out;
    (7/8 loser vs 9/10 winner) winner seed 8.
    """
    t7, t8, t9, t10 = ordered[6], ordered[7], ordered[8], ordered[9]

    w_high_idx = sim_playin_neutral(t7, t8, profiles, rng)
    l_high_idx = t8 if w_high_idx == t7 else t7

    w_low_idx = sim_playin_neutral(t9, t10, profiles, rng)
    l_low_idx = t10 if w_low_idx == t9 else t9

    w_eight_idx = sim_playin_neutral(l_high_idx, w_low_idx, profiles, rng)
    l_last = w_low_idx if w_eight_idx == l_high_idx else l_high_idx

    qual = list(ordered[:6]) + [w_high_idx, w_eight_idx]
    eliminated = {l_low_idx, l_last}
    return qual, eliminated


def sort_playoff_seeds(qual8: list[int], wins: np.ndarray, tiebreak: np.ndarray) -> list[int]:
    return sorted(qual8, key=lambda i: (-wins[i], -tiebreak[i], i))


def conference_playoff_bracket(
    seeds: list[int],
    profiles: list[TeamProfile],
    rng: np.random.Generator,
    losers_exit: dict[int, int],
) -> int:
    """seeds: 8 team indices in order 1→8 by seed. Returns conference champion index."""
    s = seeds
    seed_no = list(range(1, 9))

    w1 = simulate_series_po(s[0], s[7], 1, 8, profiles, rng, losers_exit, 1)
    w2 = simulate_series_po(s[1], s[6], 2, 7, profiles, rng, losers_exit, 1)
    w3 = simulate_series_po(s[2], s[5], 3, 6, profiles, rng, losers_exit, 1)
    w4 = simulate_series_po(s[3], s[4], 4, 5, profiles, rng, losers_exit, 1)

    orig_seed: dict[int, int] = {s[i]: seed_no[i] for i in range(8)}

    def seed_of(team_idx: int) -> int:
        return orig_seed.get(team_idx, 99)

    u = simulate_series_po(w1, w4, seed_of(w1), seed_of(w4), profiles, rng, losers_exit, 2)
    l = simulate_series_po(w2, w3, seed_of(w2), seed_of(w3), profiles, rng, losers_exit, 2)

    champ = simulate_series_po(u, l, seed_of(u), seed_of(l), profiles, rng, losers_exit, 3)
    return champ


def _projected_column_names(con: sqlite3.Connection) -> set[str]:
    rows = con.execute("PRAGMA table_info(projected_team_pr_25_26)").fetchall()
    return {str(r[1]) for r in rows}


def load_profiles(con: sqlite3.Connection) -> tuple[list[TeamProfile], list[str]]:
    ultimate = pd.read_sql_query(
        "SELECT player_name, pr, mapped_position FROM ULTIMATE_PR",
        con,
    )
    playoff = pd.read_sql_query(
        "SELECT player_name, playoff_pr FROM ultimate_playoff_pr",
        con,
    )
    dur = pd.read_sql_query(
        "SELECT player_name, rs_durability, po_durability FROM player_durability_profiles",
        con,
    )
    teams = pd.read_sql_query(
        """
        SELECT player_name, team_abbr FROM player_starting_teams_25_26
        WHERE team_abbr IS NOT NULL AND TRIM(team_abbr) != ''
        """,
        con,
    )

    proj_cols = _projected_column_names(con)
    if "coach_mult" in proj_cols and "playstyle_mult" in proj_cols:
        proj_sql = """
            SELECT team_abbr, coach_mult, playstyle_mult
            """
        if "continuity_mult" in proj_cols:
            proj_sql += ", continuity_mult\n"
        else:
            proj_sql += ", NULL AS continuity_mult\n"
        proj_sql += "FROM projected_team_pr_25_26"
        projected = pd.read_sql_query(proj_sql, con)
    else:
        extra_cont = ""
        if "continuity_mult" in proj_cols:
            extra_cont = ", continuity_mult"
        projected = pd.read_sql_query(
            f"""
            SELECT team_abbr, coach_grade, playstyle{extra_cont}
            FROM projected_team_pr_25_26
            """,
            con,
        )

    po_team = pd.read_sql_query(
        """
        SELECT team, amplified_coach_mult, playstyle_mult, continuity_mult
        FROM team_playoff_pr_25_26
        """,
        con,
    )
    ps_mult_df = pd.read_sql_query(
        "SELECT playstyle, multiplier FROM playstyle_multipliers",
        con,
    )
    mult_map = dict(zip(ps_mult_df["playstyle"], ps_mult_df["multiplier"]))

    m = (
        teams.merge(ultimate, on="player_name", how="inner")
        .merge(playoff, on="player_name", how="left")
        .merge(dur, on="player_name", how="left")
    )
    m["pr"] = pd.to_numeric(m["pr"], errors="coerce").fillna(5.0)
    m["playoff_pr"] = pd.to_numeric(m["playoff_pr"], errors="coerce").fillna(m["pr"])
    m["mapped_position"] = (
        m["mapped_position"].fillna("F").astype(str).str.strip().str.upper()
    )
    m["rs_durability"] = pd.to_numeric(m["rs_durability"], errors="coerce").fillna(0.75)
    m["po_durability"] = pd.to_numeric(m["po_durability"], errors="coerce").fillna(0.75)

    proj_by = projected.set_index("team_abbr")
    po_by = po_team.set_index("team")

    has_proj_cont = "continuity_mult" in projected.columns

    profiles: list[TeamProfile] = []
    abbrs: list[str] = []

    for abbr in sorted(m["team_abbr"].unique()):
        tab = str(abbr)
        g = m[m["team_abbr"] == tab]
        roster: list[PlayerRow] = []
        for _, row in g.iterrows():
            roster.append(
                PlayerRow(
                    name=str(row["player_name"]),
                    pr_rs=float(row["pr"]),
                    pr_po=float(row["playoff_pr"]),
                    pos=str(row["mapped_position"]),
                    rs_dur=float(row["rs_durability"]),
                    po_dur=float(row["po_durability"]),
                )
            )
        roster_t = tuple(roster)

        st, bn, ni, res = stratify_depth(roster, po=False)
        stp, bnp, nip, resp = stratify_depth(roster, po=True)

        st_list = _ensure_min_starters(list(st), list(stp), 5)
        bn_list = _ensure_min_starters(list(bn), list(bnp), 3)
        if ni is None:
            ni = nip

        prow = proj_by.loc[tab]

        if "coach_mult" in projected.columns:
            coach_mult = float(prow["coach_mult"])
            rs_ps = float(prow["playstyle_mult"])
        else:
            coach_mult = _coach_mult_from_grade(prow["coach_grade"])
            ps_label = prow["playstyle"]
            if ps_label is None or (isinstance(ps_label, float) and pd.isna(ps_label)):
                rs_ps = PLAYSTYLE_FALLBACK_MULT
            else:
                rs_ps = float(mult_map.get(str(ps_label), PLAYSTYLE_FALLBACK_MULT))

        if has_proj_cont:
            cont_rs_raw = prow["continuity_mult"]
            if cont_rs_raw is None or (isinstance(cont_rs_raw, float) and pd.isna(cont_rs_raw)):
                continuity_rs = float(po_by.loc[tab]["continuity_mult"])
            else:
                continuity_rs = float(cont_rs_raw)
        else:
            continuity_rs = float(po_by.loc[tab]["continuity_mult"])

        por = po_by.loc[tab]

        profiles.append(
            TeamProfile(
                abbr=tab,
                conf=TEAM_CONFERENCE[tab],
                coach_mult=coach_mult,
                rs_playstyle_mult=rs_ps,
                continuity_mult_rs=continuity_rs,
                amp_coach_mult=float(por["amplified_coach_mult"]),
                po_playstyle_mult=float(por["playstyle_mult"]),
                continuity_mult_po=float(por["continuity_mult"]),
                starters=tuple(st_list[:5]),
                bench=tuple(bn_list[:3]),
                ninth=ni,
                reserves=tuple(res),
                starters_po=tuple(stp[:5]),
                bench_po=tuple(bnp[:3]),
                ninth_po=nip,
                reserves_po=tuple(resp),
                roster_all=roster_t,
            )
        )
        abbrs.append(tab)

    return profiles, abbrs


def run_one_full_sim(
    profiles: list[TeamProfile],
    abbrs: list[str],
    rng: np.random.Generator,
) -> tuple[np.ndarray, dict[int, int], dict[int, int]]:
    """
    Returns wins (30,), conference seed 1–15 per team, playoff exit code:
      0 = missed playoffs
      1 = lost R1
      2 = lost R2
      3 = lost CF
      4 = lost Finals
      5 = champion
    """
    wins = run_regular_season(profiles, rng)
    tiebreak = rng.random(N_TEAMS)

    east = conf_indices(abbrs, "East")
    west = conf_indices(abbrs, "West")
    ord_e = standings_order(wins, east, tiebreak)
    ord_w = standings_order(wins, west, tiebreak)

    conf_seed: dict[int, int] = {}
    for r, tid in enumerate(ord_e):
        conf_seed[tid] = r + 1
    for r, tid in enumerate(ord_w):
        conf_seed[tid] = r + 1

    qual_e, elim_e = play_in_conference(ord_e, profiles, rng)
    qual_w, elim_w = play_in_conference(ord_w, profiles, rng)

    seeds_e = sort_playoff_seeds(qual_e, wins, tiebreak)
    seeds_w = sort_playoff_seeds(qual_w, wins, tiebreak)

    losers_league: dict[int, int] = {}
    champ_e = conference_playoff_bracket(seeds_e, profiles, rng, losers_league)
    champ_w = conference_playoff_bracket(seeds_w, profiles, rng, losers_league)

    if wins[champ_e] > wins[champ_w] or (
        wins[champ_e] == wins[champ_w] and tiebreak[champ_e] > tiebreak[champ_w]
    ):
        nba_champ = simulate_series_po(
            champ_e, champ_w, 1, 2, profiles, rng, losers_league, 4
        )
    else:
        nba_champ = simulate_series_po(
            champ_w, champ_e, 1, 2, profiles, rng, losers_league, 4
        )

    exit_code: dict[int, int] = {}
    qualified = set(qual_e) | set(qual_w)
    for tid in range(N_TEAMS):
        exit_code[tid] = 1 if tid in qualified else 0
    for ei in elim_e | elim_w:
        exit_code[ei] = 0
    for tid, lv in losers_league.items():
        exit_code[tid] = max(exit_code[tid], lv)
    exit_code[nba_champ] = 5

    return wins, conf_seed, exit_code


def print_depth_validation(profiles: list[TeamProfile], labels: tuple[str, ...]) -> None:
    for lab in labels:
        p = next(x for x in profiles if x.abbr == lab)
        print(f"\n=== {lab} — starting 5 & 9th man (RS depth) ===")
        for i, s in enumerate(p.starters, 1):
            print(f"  Starter {i}: {s.name} ({s.pos})  PR={s.pr_rs:.2f}")
        if p.ninth:
            print(f"  9th man: {p.ninth.name} ({p.ninth.pos})  PR={p.ninth.pr_rs:.2f}")
        else:
            print("  9th man: (none)")


def write_results_table(
    con: sqlite3.Connection,
    abbrs: list[str],
    win_sum: np.ndarray,
    seed_counts: np.ndarray,
    exit_counts: np.ndarray,
) -> None:
    n = N_SIMULATIONS
    rows = []
    for i, ab in enumerate(abbrs):
        miss = int(exit_counts[i, 0])
        r1 = int(exit_counts[i, 1])
        r2 = int(exit_counts[i, 2])
        cf = int(exit_counts[i, 3])
        fn = int(exit_counts[i, 4])
        ch = int(exit_counts[i, 5])
        row = [
            ab,
            round(float(win_sum[i]) / n, 2),
        ]
        for s in range(15):
            row.append(round(100.0 * float(seed_counts[i, s]) / n, 4))
        row.extend(
            [
                round(100.0 * miss / n, 4),
                round(100.0 * r1 / n, 4),
                round(100.0 * r2 / n, 4),
                round(100.0 * cf / n, 4),
                round(100.0 * fn / n, 4),
                round(100.0 * ch / n, 4),
            ]
        )
        rows.append(tuple(row))

    cur = con.cursor()
    cur.execute("DROP TABLE IF EXISTS simulation_results_25_26")
    cols_seed = ", ".join([f"seed_{k}_pct REAL NOT NULL" for k in range(1, 16)])
    cur.execute(
        f"""
        CREATE TABLE simulation_results_25_26 (
            team TEXT PRIMARY KEY,
            avg_wins REAL NOT NULL,
            {cols_seed},
            missed_playoffs_pct REAL NOT NULL,
            first_round_pct REAL NOT NULL,
            second_round_pct REAL NOT NULL,
            conf_finals_pct REAL NOT NULL,
            finals_pct REAL NOT NULL,
            champion_pct REAL NOT NULL
        )
        """
    )
    qmarks = ",".join(["?"] * (2 + 15 + 6))
    cur.executemany(
        f"INSERT INTO simulation_results_25_26 VALUES ({qmarks})",
        rows,
    )
    con.commit()


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    con = sqlite3.connect(DB_PATH)
    try:
        profiles, abbrs = load_profiles(con)
    finally:
        con.close()

    print_depth_validation(profiles, ("SAS", "DET"))

    rng_master = np.random.default_rng(20260514)
    win_sum = np.zeros(N_TEAMS, dtype=np.float64)
    seed_counts = np.zeros((N_TEAMS, 15), dtype=np.int32)
    exit_counts = np.zeros((N_TEAMS, 6), dtype=np.int32)

    for _ in range(N_SIMULATIONS):
        sim_rng = np.random.default_rng(int(rng_master.integers(0, 2**63 - 1, dtype=np.int64)))
        wins, conf_seed, exit_code = run_one_full_sim(profiles, abbrs, sim_rng)
        win_sum += wins.astype(np.float64)
        for tid, sd in conf_seed.items():
            if 1 <= sd <= 15:
                seed_counts[tid, sd - 1] += 1
        for tid in range(N_TEAMS):
            ex = exit_code[tid]
            if 0 <= ex <= 5:
                exit_counts[tid, ex] += 1

    con = sqlite3.connect(DB_PATH)
    try:
        write_results_table(con, abbrs, win_sum, seed_counts, exit_counts)
    finally:
        con.close()

    with sqlite3.connect(DB_PATH) as c2:
        df = pd.read_sql_query(
            "SELECT team, champion_pct FROM simulation_results_25_26 "
            "ORDER BY champion_pct DESC LIMIT 10",
            c2,
        )
    print("\n=== Top 10 championship favorites (champion_pct) ===")
    print(df.to_string(index=False))
    print(f"\nWrote simulation_results_25_26 ({N_SIMULATIONS} simulations).")


if __name__ == "__main__":
    main()
