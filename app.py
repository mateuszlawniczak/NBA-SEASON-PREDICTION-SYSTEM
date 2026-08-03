
"""
EYE ON PAPER — NBA Predictive Engine (Streamlit MVP)
====================================================
 
A read-only, stateless dashboard over an existing Monte-Carlo backend.
 
Design notes
------------
* No native st.sidebar. Layout is a centered 3-column "Twitter-like" shell:
      [ col_nav (1) | col_main (2.5) | col_filters (1) ]
* The database (nba_data.db) is opened READ-ONLY. The app never writes to it.
* Tab 2 ("Adjust Variables") imports the real `TeamProfile` objects from
  run_monte_carlo, deep-copies them, mutates the copies in memory, and calls
  `run_one_full_sim` so the original profiles / DB stay untouched.
 
Run it from the same folder as run_monte_carlo.py and nba_data.db:
      streamlit run app.py
 
For a fully dark grid chrome you can also drop a .streamlit/config.toml with:
      [theme]
      base = "dark"
"""
 
from __future__ import annotations
 
import copy
import sqlite3
 
import numpy as np
import pandas as pd
import streamlit as st
 
# The backend module ships alongside this file. Importing it only defines
# functions / dataclasses (main() is guarded by __main__), so no simulation
# runs and nothing is written to the DB on import.
import run_monte_carlo as mc
 
 
# ---------------------------------------------------------------------------
# Page config + theme tokens
# ---------------------------------------------------------------------------
 
st.set_page_config(
    page_title="EYE ON PAPER",
    page_icon="🏀",
    layout="wide",
)
 
# Signature palette: dark slate court with a single hardwood-amber accent.
PALETTE = {
    "bg":       "#000000",
    "surface":  "#000000",
    "surface2": "#000000",
    "border":   "#ffffff",
    "text":     "#ffffff",
    "muted":    "#ffffff",
    "accent":   "#ffffff",
    "accent_d": "#cccccc",
    "good":     "#ffffff",
}
 
NAV_ITEMS = [
    "My System",
    "Adjust Variables",
    "Current Formula",
    "What If?",
    "AI Bot",
    "Creator",
]
 
 
def inject_css() -> None:
    """All custom styling lives here: forces dark mode, builds the Twitter-style
    nav out of a radio group, and themes the data tables."""
    st.markdown(
        f"""
        <style>
        /* ---- Google font for the brand ---- */
        @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700;800&display=swap');

        /* ---- Force dark surface regardless of user's base theme ---- */
        .stApp {{ background: {PALETTE['bg']}; color: {PALETTE['text']}; }}
        /* Lower the whole page so the brand clears Streamlit's native top-right
           menu (Deploy / ⋮ toolbar). */
        .block-container {{ padding-top: 4.5rem; padding-bottom: 3rem; max-width: 1240px; }}
        section.main > div {{ background: transparent; }}
 
        /* Kill the sidebar entirely in case anything tries to mount it */
        section[data-testid="stSidebar"] {{ display: none !important; }}

        /* Nuke Streamlit's default dark-gray top bar */
        header[data-testid="stHeader"] {{ background-color: #000000 !important; }}
 
        h1, h2, h3, h4 {{ color: {PALETTE['text']}; letter-spacing: -0.01em; }}
        p, li, label, span {{ color: {PALETTE['text']}; }}
        a {{ color: {PALETTE['accent']}; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
 
        /* ---- Brand header ---- */
        .brand {{
            font-family: 'Poppins', sans-serif;
            font-weight: 800; font-size: 3.92rem; line-height: 1;
            letter-spacing: -0.03em; margin: 0 0 .15rem 0;
            text-align: center;
        }}
        .brand .dot {{ color: {PALETTE['accent']}; }}
        .brand-sub {{
            color: #ffffff !important; font-size: 1.1rem; margin-bottom: 2.5rem;
            text-align: center;
        }}

        /* ---- Left sidebar: small logo above nav, note below ---- */
        .eop-logo {{
            font-family: 'Poppins', sans-serif;
            font-weight: 800; font-size: .82rem; letter-spacing: .12em;
            text-transform: uppercase; color: #ffffff;
            margin: 0 0 1.4rem 6px;
        }}
        .eop-note {{
            font-size: .74rem; line-height: 1.4; color: #ffffff; opacity: .55;
            margin: 1.4rem 0 0 6px;
        }}

        /* ---- Card wrapper used across panels ---- */
        .eop-card {{
            background: {PALETTE['surface']};
            border: 1px solid {PALETTE['border']};
            border-radius: 14px;
            padding: 16px 18px;
            margin-bottom: 14px;
        }}
        .eop-eyebrow {{
            text-transform: uppercase; letter-spacing: .14em;
            font-size: .68rem; color: #ffffff !important; opacity: 1 !important;
            margin-bottom: .5rem;
        }}
 
        /* ---- Left nav: clean, transparent text links stacked vertically ---- */
        div[role="radiogroup"] {{ gap: 2px; }}
        /* hide the actual radio circle (first child div of each label) */
        div[role="radiogroup"] > label > div:first-child {{ display: none !important; }}
        div[role="radiogroup"] > label {{
            display: flex; align-items: center;
            width: 100%;
            padding: 7px 6px;
            margin: 0;
            border: none;
            background: transparent;
            color: {PALETTE['muted']};
            font-weight: 500; font-size: .98rem;
            cursor: pointer;
            transition: color .12s ease;
        }}
        div[role="radiogroup"] > label:hover {{
            background: transparent;
            color: {PALETTE['text']};
        }}
        div[role="radiogroup"] > label:hover p {{
            color: #ffffff !important; font-weight: 900 !important;
        }}
        /* selected link (modern :has — Streamlit runs in evergreen browsers) */
        div[role="radiogroup"] > label:has(input:checked) {{
            background: transparent;
        }}
        div[role="radiogroup"] > label:has(input:checked) p {{
            color: #ffffff !important; font-weight: 900 !important;
        }}
        div[role="radiogroup"] label p {{
            margin: 0; color: {PALETTE['muted']}; font-weight: 500;
            transition: color .12s ease, font-weight .12s ease;
        }}
 
        /* ---- Right panel widgets ---- */
        .stCheckbox, .stSlider, .stSelectbox, .stRadio {{ margin-bottom: .35rem; }}

        /* ---- Checkboxes: stark black box, white border, white mark ---- */
        [data-baseweb="checkbox"] span[data-baseweb] {{
            background-color: #000000 !important;
            border: 1px solid #ffffff !important;
        }}
        [data-baseweb="checkbox"] input:checked + span[data-baseweb],
        [data-baseweb="checkbox"] span[aria-checked="true"] {{
            background-color: #ffffff !important;
            border: 1px solid #ffffff !important;
        }}
        /* the checkmark glyph turns black so it reads on the white box */
        [data-baseweb="checkbox"] input:checked + span[data-baseweb] svg,
        [data-baseweb="checkbox"] span[aria-checked="true"] svg {{
            color: #000000 !important; fill: #000000 !important; stroke: #000000 !important;
        }}
        /* Override Streamlit's primary (red) checkbox fill — force white box ---- */
        div[data-baseweb="checkbox"] > div {{ border-color: #ffffff !important; }}
        div[data-baseweb="checkbox"] > div[data-checked="true"] {{
            background-color: #ffffff !important; border-color: #ffffff !important;
        }}
        div[data-baseweb="checkbox"] > div[data-checked="true"] svg {{ fill: #000000 !important; }}

        /* ---- Right filters: force all checkbox / radio text to white ---- */
        .stCheckbox label, .stRadio label,
        .stCheckbox label *, .stRadio label *,
        .stCheckbox div, .stRadio div {{ color: #ffffff !important; }}

        /* ---- Strip gray from every interactive widget label ---- */
        .stCheckbox label p, .stRadio label p,
        div[role="radiogroup"] label p,
        .stSelectbox label p, .stSlider label p {{ color: #ffffff !important; }}

        /* ---- Right filters: bold the label on hover / active ---- */
        .stCheckbox label:hover,
        .stRadio label:hover,
        .stCheckbox label:hover *,
        .stRadio label:hover * {{
            color: #ffffff !important; font-weight: 900 !important;
        }}
        .stCheckbox label:has(input:checked),
        .stRadio label:has(input:checked),
        .stCheckbox label:has(input:checked) *,
        .stRadio label:has(input:checked) * {{
            color: #ffffff !important; font-weight: 900 !important;
        }}

        /* ---- Grouped filter boxes (st.container(border=True)) ---- */
        div[data-testid="stVerticalBlockBorderWrapper"] {{
            background: {PALETTE['surface']};
            border: 1px solid rgba(255, 255, 255, 0.16) !important;
            border-radius: 14px;
            padding: 14px 16px;
            margin-bottom: 14px;
        }}
        div[data-testid="stVerticalBlockBorderWrapper"] .stRadio {{ margin-bottom: 0; }}
 
        /* ---- Buttons ---- */
        .stButton > button {{
            background: {PALETTE['accent']}; color: #16110a;
            border: 0; border-radius: 10px; font-weight: 700;
            padding: .55rem 1rem; width: 100%;
            transition: background .12s ease;
        }}
        .stButton > button:hover {{ background: {PALETTE['accent_d']}; color: #fff; }}
 
        /* ---- Data tables: pure black cells, thin white gridlines ---- */
        [data-testid="stDataFrame"] {{
            border: 1px solid #ffffff !important;
            border-radius: 0; overflow: hidden;
            font-size: 1.15rem !important;
            background: #000000 !important;
        }}
        [data-testid="stDataFrame"] th,
        [data-testid="stDataFrame"] td,
        [data-testid="stDataFrame"] tr {{
            background-color: #000000 !important;
            color: #ffffff !important;
            text-align: center !important;
            border-bottom: 1px solid #ffffff !important;
            border-right: 1px solid #ffffff !important;
        }}
        [data-testid="stDataFrame"] th,
        [data-testid="stDataFrame"] th div {{
            background-color: #000000 !important;
            color: #ffffff !important;
            font-weight: 900 !important;
        }}
        [data-testid="stDataFrame"] td,
        [data-testid="stDataFrame"] td div {{
            background-color: #000000 !important;
            color: #ffffff !important;
            text-align: center !important;
        }}
        [data-testid="stMetricValue"] {{ color: {PALETTE['accent']}; }}
 
        /* ---- Team picker: Twitter/X-style pill multiselect ---- */
        /* The main selectbox control */
        div[data-testid="stMultiSelect"] div[data-baseweb="select"] > div {{
            background-color: #000000 !important;
            border: 1px solid #ffffff !important;
            border-radius: 9999px !important;
            box-shadow: none !important;
            color: #ffffff !important;
        }}
        div[data-testid="stMultiSelect"] div[data-baseweb="select"] > div:focus-within {{
            border: 1px solid #ffffff !important;
            box-shadow: none !important;
        }}
        /* Typed text + placeholder inside the control */
        div[data-testid="stMultiSelect"] input {{ color: #ffffff !important; }}
        div[data-testid="stMultiSelect"] [data-baseweb="select"] div {{ color: #ffffff !important; }}
        /* Selected team "pills" (tags) */
        div[data-testid="stMultiSelect"] span[data-baseweb="tag"] {{
            background-color: #000000 !important;
            border: 1px solid #ffffff !important;
            border-radius: 9999px !important;
            color: #ffffff !important;
        }}
        div[data-testid="stMultiSelect"] span[data-baseweb="tag"] span,
        div[data-testid="stMultiSelect"] span[data-baseweb="tag"] svg {{
            color: #ffffff !important; fill: #ffffff !important;
        }}
        /* Dropdown chevron + clear icons */
        div[data-testid="stMultiSelect"] svg {{ fill: #ffffff !important; color: #ffffff !important; }}
        /* Enlarge the "x" on each selected pill so it's easy to click */
        div[data-testid="stMultiSelect"] span[data-baseweb="tag"] [role="presentation"],
        div[data-testid="stMultiSelect"] span[data-baseweb="tag"] svg {{
            transform: scale(1.5);
            cursor: pointer;
        }}
        /* Enlarge the main clear-all "x" on the right of the input box */
        div[data-testid="stMultiSelect"] div[data-baseweb="select"] > div > div:last-child svg {{
            transform: scale(1.5);
            cursor: pointer;
        }}
        /* The popover dropdown menu of options */
        div[data-baseweb="popover"] ul[role="listbox"],
        div[data-baseweb="popover"] [data-baseweb="menu"] {{
            background-color: #000000 !important;
            border: 1px solid #ffffff !important;
            border-radius: 14px !important;
        }}
        div[data-baseweb="popover"] li[role="option"] {{
            background-color: #000000 !important;
            color: #ffffff !important;
        }}
        div[data-baseweb="popover"] li[role="option"]:hover,
        div[data-baseweb="popover"] li[aria-selected="true"] {{
            background-color: #ffffff !important;
            color: #000000 !important;
        }}

        /* tighten the three columns visually */
        div[data-testid="column"] {{ padding: 0 .35rem; }}
        </style>
        """,
        unsafe_allow_html=True,
    )
 
 
# ---------------------------------------------------------------------------
# Data access — strictly read-only, cached
# ---------------------------------------------------------------------------

def _target_draft_year(season: str) -> int:
    """Opening year of a season label, e.g. '2022-23' -> 2022."""
    return int(season.split("-")[0])


def _season_display(season: str) -> str:
    """UI label: '2025-26' -> '2025–26'."""
    parts = season.split("-")
    if len(parts) == 2:
        return f"{parts[0]}–{parts[1]}"
    return season


def _ro_connect() -> sqlite3.Connection:
    """Open nba_data.db in read-only mode. Guarantees the app cannot mutate it."""
    uri = f"file:{mc.DB_PATH}?mode=ro"
    return sqlite3.connect(uri, uri=True)
 
 
@st.cache_data(show_spinner=False)
def load_available_seasons() -> list[str]:
    """Production simulation seasons, newest first."""
    con = _ro_connect()
    try:
        rows = con.execute(
            """
            SELECT DISTINCT season
            FROM simulation_results
            WHERE run_id = 'production'
            ORDER BY season DESC
            """
        ).fetchall()
    finally:
        con.close()
    return [str(r[0]) for r in rows]


@st.cache_data(show_spinner=False)
def load_sim_results(season: str) -> pd.DataFrame:
    """simulation_results — baseline championship odds + seed projections."""
    con = _ro_connect()
    try:
        df = pd.read_sql_query(
            """
            SELECT team, avg_wins,
                   seed_1_pct, seed_2_pct, seed_3_pct, seed_4_pct, seed_5_pct,
                   seed_6_pct, seed_7_pct, seed_8_pct, seed_9_pct, seed_10_pct,
                   seed_11_pct, seed_12_pct, seed_13_pct, seed_14_pct, seed_15_pct,
                   missed_playoffs_pct, first_round_pct, second_round_pct,
                   conf_finals_pct, finals_pct, champion_pct
            FROM simulation_results
            WHERE season = ? AND run_id = 'production'
            """,
            con,
            params=(season,),
        )
    finally:
        con.close()

    if df.empty:
        return df

    seed_cols = [c for c in df.columns if c.startswith("seed_") and c.endswith("_pct")]
    seed_cols = sorted(seed_cols, key=lambda c: int(c.split("_")[1]))

    # Projected seed = the most probable seed; expected seed = probability-weighted.
    if seed_cols:
        seed_nums = np.array([int(c.split("_")[1]) for c in seed_cols])
        probs = df[seed_cols].to_numpy(dtype=float)
        df["proj_seed"] = seed_nums[np.argmax(probs, axis=1)]
        with np.errstate(invalid="ignore"):
            df["exp_seed"] = (probs * seed_nums).sum(axis=1) / np.clip(
                probs.sum(axis=1), 1e-9, None
            )
 
    if "missed_playoffs_pct" in df.columns:
        df["make_playoffs_pct"] = (100.0 - df["missed_playoffs_pct"]).round(2)
    if {"finals_pct", "champion_pct"}.issubset(df.columns):
        df["made_finals_pct"] = (df["finals_pct"] + df["champion_pct"]).round(2)
 
    df["conference"] = df["team"].map(mc.TEAM_CONFERENCE).fillna("—")
    return df
 
 
@st.cache_data(show_spinner=False)
def load_player_pr(season: str) -> pd.DataFrame:
    """ULTIMATE_PR — player PR ratings and positions (drives Tab 1 filters).

    team_abbr is resolved in three passes so injured veterans and incoming
    rookies don't fall through as None:
      1. player_starting_teams — projected starters for this season.
      2. player_stats_basic         — team at or before the selected season.
      3. rookie_data                — drafting team for that season's draft class.
    A conference column is then derived so the Conference / Team filters apply to
    the player table just like the projections table."""
    con = _ro_connect()
    try:
        df = pd.read_sql_query(
            """
            SELECT player_name, pr, player_type, applied_effects, mapped_position
            FROM ULTIMATE_PR
            WHERE season = ?
            """,
            con,
            params=(season,),
        )
        if df.empty:
            return df

        # --- Primary: projected starting teams for the selected season ---
        try:
            teams = pd.read_sql_query(
                """
                SELECT player_name, team_abbr
                FROM player_starting_teams
                WHERE season = ?
                """,
                con,
                params=(season,),
            )
            df = df.merge(teams, on="player_name", how="left")
        except Exception:
            df["team_abbr"] = pd.NA

        def _missing() -> pd.Series:
            return df["team_abbr"].isna() | (df["team_abbr"].astype(str).str.strip() == "")

        # --- Fallback 1: team at selected season, or most recent season <= selected ---
        if _missing().any():
            try:
                hist = pd.read_sql_query(
                    "SELECT player_name, team_abbr, season FROM player_stats_basic",
                    con,
                )
                hist = hist[hist["season"] <= season]
                hist = (hist.sort_values("season", ascending=False)
                            .drop_duplicates(subset="player_name", keep="first"))
                hist_map = dict(zip(hist["player_name"], hist["team_abbr"]))
                mask = _missing()
                df.loc[mask, "team_abbr"] = df.loc[mask, "player_name"].map(hist_map)
            except Exception:
                pass

        # --- Fallback 2: drafting team for the selected season's draft class ---
        if _missing().any():
            try:
                draft_year = _target_draft_year(season)
                rookies = pd.read_sql_query(
                    "SELECT player_name, draft_year, drafting_signing_team "
                    "FROM rookie_data",
                    con,
                )
                rookies = rookies.sort_values(
                    "draft_year", ascending=False, na_position="last"
                )
                if (rookies["draft_year"] == draft_year).any():
                    pref = rookies[rookies["draft_year"] == draft_year]
                    rest = rookies[rookies["draft_year"] != draft_year]
                    rookies = pd.concat([pref, rest])
                rookies = rookies.drop_duplicates(subset="player_name", keep="first")
                rookie_map = dict(
                    zip(rookies["player_name"], rookies["drafting_signing_team"])
                )
                mask = _missing()
                df.loc[mask, "team_abbr"] = df.loc[mask, "player_name"].map(rookie_map)
            except Exception:
                pass
    finally:
        con.close()

    # --- Final cleanup: (re)derive conference from the resolved team_abbr ---
    df["conference"] = df["team_abbr"].map(mc.TEAM_CONFERENCE).fillna("—")
    return df
 
 
def _continuity_label(value) -> "str | None":
    """1.05 -> HIGH, 1.00 -> DEFAULT, 0.95 -> LOW. Anything else (or NaN) passes
    through as the raw number so nothing is silently hidden."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if abs(value - 1.05) < 1e-6:
        return "HIGH"
    if abs(value - 1.00) < 1e-6:
        return "DEFAULT"
    if abs(value - 0.95) < 1e-6:
        return "LOW"
    return f"{value:g}"


@st.cache_data(show_spinner=False)
def load_formula_values(season: str) -> pd.DataFrame:
    """Team-level formula build-up — DISPLAY ONLY, nothing computed here.

    Reads three already-stored tables and joins them on team + season:
      * team_projection          -> base_team_pr, coach_grade, playstyle,
                                     final_team_pr, rotation_players  (RS)
      * team_playoff_projection  -> base_8man_pr, amplified_coach_mult,
                                     playstyle_mult, continuity_mult,
                                     final_playoff_pr                (PO)
      * team_coaches             -> coach_name

    Each table is read independently so a missing/renamed table in one layer
    doesn't blank out the other two — outer-joined on team so partial data
    still renders (blank cells, not a crash).
    """
    con = _ro_connect()
    empty_team_col = pd.DataFrame({"team": pd.Series(dtype="object")})
    try:
        try:
            tp = pd.read_sql_query(
                """
                SELECT team_abbr AS team, base_team_pr, coach_grade, playstyle,
                       final_team_pr, rotation_players
                FROM team_projection
                WHERE season = ?
                """,
                con, params=(season,),
            )
        except Exception:
            tp = empty_team_col.copy()

        try:
            tpp = pd.read_sql_query(
                """
                SELECT team, base_8man_pr, amplified_coach_mult, playstyle_mult,
                       continuity_mult, final_playoff_pr
                FROM team_playoff_projection
                WHERE season = ?
                """,
                con, params=(season,),
            )
        except Exception:
            tpp = empty_team_col.copy()

        try:
            tc = pd.read_sql_query(
                "SELECT team_abbr AS team, coach_name FROM team_coaches WHERE season = ?",
                con, params=(season,),
            )
        except Exception:
            tc = empty_team_col.copy()
    finally:
        con.close()

    df = tp.merge(tpp, on="team", how="outer").merge(tc, on="team", how="outer")
    if df.empty:
        return df

    df["conference"] = df["team"].map(mc.TEAM_CONFERENCE).fillna("—")
    if "continuity_mult" in df.columns:
        df["continuity_label"] = df["continuity_mult"].apply(_continuity_label)
    return df


@st.cache_data(show_spinner=False)
def list_tables() -> list[str]:
    con = _ro_connect()
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]
 
 
@st.cache_resource(show_spinner=False)
def load_base_profiles():
    """Load the real TeamProfile objects once. cache_resource because these are
    rich Python objects, not serializable frames. NEVER mutate the return value —
    Tab 2 deep-copies before touching anything."""
    con = _ro_connect()
    try:
        profiles, abbrs = mc.load_profiles(con)
    finally:
        con.close()
    return profiles, abbrs
 
 
# ---------------------------------------------------------------------------
# In-memory simulation for Tab 2 (no DB writes, ever)
# ---------------------------------------------------------------------------
 
def run_inmemory_sim(profiles, abbrs, n_sims: int, seed: int = 20260514) -> pd.DataFrame:
    """Aggregate `n_sims` calls to mc.run_one_full_sim over the supplied (already
    modified, in-memory) profiles. Mirrors the backend's aggregation but keeps
    everything in RAM."""
    n_teams = len(profiles)
    rng_master = np.random.default_rng(seed)
    win_sum = np.zeros(n_teams, dtype=np.float64)
    seed_counts = np.zeros((n_teams, 15), dtype=np.int32)
    exit_counts = np.zeros((n_teams, 6), dtype=np.int32)
 
    for _ in range(n_sims):
        sim_rng = np.random.default_rng(
            int(rng_master.integers(0, 2**63 - 1, dtype=np.int64))
        )
        wins, conf_seed, exit_code = mc.run_one_full_sim(profiles, abbrs, sim_rng)
        win_sum += wins.astype(np.float64)
        for tid, sd in conf_seed.items():
            if 1 <= sd <= 15:
                seed_counts[tid, sd - 1] += 1
        for tid in range(n_teams):
            ex = exit_code[tid]
            if 0 <= ex <= 5:
                exit_counts[tid, ex] += 1
 
    seed_nums = np.arange(1, 16)
    rows = []
    for i, ab in enumerate(abbrs):
        probs = seed_counts[i].astype(float)
        proj_seed = int(seed_nums[int(np.argmax(probs))]) if probs.sum() else 0
        rows.append(
            {
                "team": ab,
                "conference": mc.TEAM_CONFERENCE.get(ab, "—"),
                "avg_wins": round(float(win_sum[i]) / n_sims, 2),
                "proj_seed": proj_seed,
                "make_playoffs_pct": round(100.0 * (n_sims - exit_counts[i, 0]) / n_sims, 2),
                "champion_pct": round(100.0 * exit_counts[i, 5] / n_sims, 2),
                "made_finals_pct": round(
                    100.0 * (exit_counts[i, 4] + exit_counts[i, 5]) / n_sims, 2
                ),
            }
        )
    return pd.DataFrame(rows)
 
 
# ---------------------------------------------------------------------------
# Table styling helper (dark + amber heat on the headline column)
# ---------------------------------------------------------------------------
 
def style_table(df: pd.DataFrame, heat_col: str | None = None):
    # Stark black/white: flat black cells, white text, no colored heatmap.
    # `heat_col` is accepted for call-site compatibility but intentionally unused.
    sty = df.style.set_properties(
        **{
            "background-color": "#000000",
            "color": "#ffffff",
            "border-color": "#ffffff",
            "text-align": "center",
        }
    )
    # Also force black on header + index cells (th) so the frozen Team
    # column and the header row don't fall back to the gray default theme.
    sty = sty.set_table_styles(
        [
            {
                "selector": "th",
                "props": [
                    ("background-color", "#000000"),
                    ("color", "#ffffff"),
                    ("border-color", "#ffffff"),
                    ("text-align", "center"),
                ],
            }
        ]
    )
    fmt = {c: "{:.2f}" for c in df.select_dtypes("float").columns}
    if fmt:
        sty = sty.format(fmt)
    return sty
 
 
# ===========================================================================
# VIEWS
# ===========================================================================
 
_VIEW_TOGGLES = ["show_main", "show_precise", "show_players", "show_formula"]


def _exclusive_toggle(active_key: str) -> None:
    """Radio-like behaviour for the three top checkboxes: turning one on forces
    the other two off. Re-checking the only active box keeps it on."""
    if st.session_state.get(active_key):
        for k in _VIEW_TOGGLES:
            if k != active_key:
                st.session_state[k] = False
    else:
        # Don't allow zero selected — bounce the user back to "Main odds".
        if not any(st.session_state.get(k) for k in _VIEW_TOGGLES):
            st.session_state["show_main"] = True


_POS_KEYS = ["pos_g", "pos_f", "pos_c"]


def _pos_toggle(changed_key: str) -> None:
    """Cross-link the position checkboxes:
      * Checking "ALL" clears Guards / Forwards / Centers.
      * "ALL" is sticky — trying to uncheck it while no specific position is
        selected snaps it back on (never leaves the table blank).
      * Checking any specific position clears "ALL".
      * Unchecking the last specific position falls back to "ALL".
    """
    if changed_key == "pos_all":
        if st.session_state.get("pos_all"):
            for k in _POS_KEYS:
                st.session_state[k] = False
        elif not any(st.session_state.get(k) for k in _POS_KEYS):
            # Can't turn ALL off unless a specific position is active.
            st.session_state["pos_all"] = True
    else:
        if st.session_state.get(changed_key):
            st.session_state["pos_all"] = False
        elif not any(st.session_state.get(k) for k in _POS_KEYS):
            st.session_state["pos_all"] = True


def _conf_mismatch_msg(selected_teams: list[str]) -> str:
    """Snarky empty-state line when a team/conference filter combo yields nothing."""
    if selected_teams:
        team = selected_teams[0]
        actual = mc.TEAM_CONFERENCE.get(team, "Unknown")
        return f"{team} is in the {actual} conference broski."
    return "Nothing matches those filters broski."


def _render_formula_view(
    season: str,
    conf_pick: str,
    selected_teams: list[str],
    pos_all: bool,
    pos_g: bool,
    pos_f: bool,
    pos_c: bool,
) -> None:
    """DISPLAY ONLY. Renders the stored intermediate formula values — no new
    values are computed here, nothing is written to the database."""
    # ---- team-level build-up ----
    st.markdown('<div class="eop-eyebrow">Formula build-up · team ratings</div>',
                unsafe_allow_html=True)
    try:
        fdf = load_formula_values(season).copy()
    except Exception as e:
        st.info(
            f"Limited data for {_season_display(season)} — formula values "
            "could not be loaded."
        )
        st.caption(f"Details: {e}")
        fdf = pd.DataFrame()

    if fdf.empty:
        st.info(
            f"Limited data for {_season_display(season)} — no stored formula "
            "values found for this season."
        )
    else:
        if conf_pick != "Both" and "conference" in fdf.columns:
            fdf = fdf[fdf["conference"] == conf_pick]
        if selected_teams and "team" in fdf.columns:
            fdf = fdf[fdf["team"].isin(selected_teams)]

        if fdf.empty:
            st.markdown(
                '<div style="text-align: center; color: white; font-weight: bold; '
                f'font-size: 24px; margin-top: 50px;">{_conf_mismatch_msg(selected_teams)}'
                '</div>',
                unsafe_allow_html=True,
            )
        else:
            # Ordered so the RS build-up reads left-to-right (base -> inputs ->
            # final), then the PO build-up the same way.
            display_cols = {
                "team": "Team",
                "conference": "Conf",
                "coach_name": "Coach",
                "coach_grade": "Coach Grade",
                "playstyle": "Playstyle",
                "base_team_pr": "Base Team PR (RS)",
                "final_team_pr": "Final Team PR (RS)",
                "base_8man_pr": "Base 8-Man PR (PO)",
                "amplified_coach_mult": "Amp. Coach Mult (PO)",
                "playstyle_mult": "Playstyle Mult (PO)",
                "continuity_mult": "Continuity Mult (PO)",
                "continuity_label": "Continuity",
                "final_playoff_pr": "Final Playoff PR (PO)",
                "rotation_players": "Rotation (9-man, RS)",
            }
            cols = [c for c in display_cols if c in fdf.columns]
            out = fdf[cols].rename(columns=display_cols)
            sort_col = next(
                (c for c in ["Final Team PR (RS)", "Team"] if c in out.columns),
                out.columns[0],
            )
            out = out.sort_values(sort_col, ascending=False).reset_index(drop=True)
            out = out.set_index("Team")

            if selected_teams:
                row_px = 35
                dyn_height = int((len(out) + 1) * row_px + 3)
                st.dataframe(style_table(out), use_container_width=True, height=dyn_height)
            else:
                st.dataframe(style_table(out), use_container_width=True, height=560)

            st.caption(
                "RS = regular season (`team_projection`) · PO = playoffs "
                "(`team_playoff_projection`). Continuity: 1.05 = HIGH, "
                "1.00 = DEFAULT, 0.95 = LOW. Every value here is read directly "
                "from stored tables — nothing is recomputed on this page."
            )

    # ---- player-level build-up ----
    st.markdown('<div class="eop-eyebrow">Formula build-up · player ratings</div>',
                unsafe_allow_html=True)
    try:
        pdf = load_player_pr(season).copy()
    except Exception as e:
        st.info(
            f"Limited data for {_season_display(season)} — player formula "
            "values could not be loaded."
        )
        st.caption(f"Details: {e}")
        return

    if pdf.empty:
        st.info(
            f"Limited data for {_season_display(season)} — no player ratings "
            "stored for this season."
        )
        return

    if conf_pick != "Both" and "conference" in pdf.columns:
        pdf = pdf[pdf["conference"] == conf_pick]
    if selected_teams and "team_abbr" in pdf.columns:
        pdf = pdf[pdf["team_abbr"].isin(selected_teams)]

    pos_col = next((c for c in ["mapped_position", "position", "pos"]
                    if c in pdf.columns), None)
    selected_pos = [k for k, v in {"G": pos_g, "F": pos_f, "C": pos_c}.items() if v]
    if pos_all or not selected_pos:
        selected_pos = ["G", "F", "C"]
    if pos_col:
        pdf = pdf[pdf[pos_col].astype(str).str.strip().str.upper().isin(selected_pos)]

    if "pr" in pdf.columns:
        pdf = pdf.sort_values("pr", ascending=False)

    display_pcols = [c for c in ["player_name", "team_abbr", "conference", pos_col,
                                  "pr", "player_type", "applied_effects"]
                      if c in pdf.columns]
    pdisp = pdf[display_pcols].reset_index(drop=True).rename(columns={
        "player_name": "Player", "team_abbr": "Team", "conference": "Conf",
        "mapped_position": "Pos", "position": "Pos", "pos": "Pos",
        "pr": "PR", "player_type": "Player Type", "applied_effects": "Applied Effects",
    })
    if pdisp.empty:
        st.info(
            f"Limited data for {_season_display(season)} — no players match "
            "the current filters."
        )
        return
    st.dataframe(style_table(pdisp), use_container_width=True, hide_index=True, height=440)


def view_my_system(col_main, col_filters) -> None:
    st.markdown(
        "<style> [data-testid='stDataFrame'] th { font-size: 1.15rem !important; } </style>",
        unsafe_allow_html=True,
    )
    # Seed defaults once so the exclusive toggles start with "Main odds" on.
    if "show_main" not in st.session_state:
        st.session_state["show_main"] = True
        st.session_state["show_precise"] = False
        st.session_state["show_players"] = False
        st.session_state["show_formula"] = False

    try:
        season_options = load_available_seasons()
    except Exception:
        season_options = []
    if not season_options:
        with col_main:
            st.warning(
                "No production simulation seasons found. Run `run_monte_carlo.py` first."
            )
        return

    # ---- right panel: filters tied to this tab ----
    with col_filters:
        st.markdown('<div class="eop-eyebrow">Filters</div>', unsafe_allow_html=True)

        with st.container(border=True):
            selected_season = st.selectbox(
                "Season",
                options=season_options,
                index=0,
                format_func=_season_display,
                help="Production Monte-Carlo baseline for this season.",
            )

        # Box 1 — mutually exclusive view toggles + (conditional) position filters
        pos_all = True
        pos_g = pos_f = pos_c = False
        with st.container(border=True):
            show_main = st.checkbox(
                "Main odds", key="show_main",
                on_change=_exclusive_toggle, args=("show_main",),
                help="Championship / Finals / Conf Finals odds")
            show_precise = st.checkbox(
                "More precise", key="show_precise",
                on_change=_exclusive_toggle, args=("show_precise",),
                help="All seeds (1–15) and every playoff stage")
            show_players = st.checkbox(
                "Show players PR", key="show_players",
                on_change=_exclusive_toggle, args=("show_players",))
            show_formula = st.checkbox(
                "Formula values", key="show_formula",
                on_change=_exclusive_toggle, args=("show_formula",),
                help="Debug view: the stored intermediate values that build up "
                     "each team's (and player's) rating — read-only.")

            # Position filters live right under the players toggle and only
            # appear when a player table is active (plain PR or formula values).
            if show_players or show_formula:
                # Seed position state the moment the player section appears so
                # "ALL" starts checked instead of blank. Must run before the
                # widgets are instantiated below.
                for _k, _default in (("pos_all", True), ("pos_g", False),
                                     ("pos_f", False), ("pos_c", False)):
                    if _k not in st.session_state:
                        st.session_state[_k] = _default
                st.caption("Position")
                pos_all = st.checkbox("ALL", key="pos_all",
                                      on_change=_pos_toggle, args=("pos_all",))
                pos_g = st.checkbox("Guards", key="pos_g",
                                    on_change=_pos_toggle, args=("pos_g",))
                pos_f = st.checkbox("Forwards", key="pos_f",
                                    on_change=_pos_toggle, args=("pos_f",))
                pos_c = st.checkbox("Centers", key="pos_c",
                                    on_change=_pos_toggle, args=("pos_c",))

        # Box 2 — global conference filter (projections + players)
        with st.container(border=True):
            conf_pick = st.radio("Conference", ["Both", "East", "West"],
                                 horizontal=True)

        # Box 3 — Twitter/X-style team search (projections + players)
        st.markdown('<div class="eop-eyebrow">PICK SPECIFIC TEAM</div>',
                    unsafe_allow_html=True)
        try:
            team_options = sorted(
                load_sim_results(selected_season)["team"].dropna().astype(str).unique().tolist()
            )
        except Exception:
            team_options = []
        selected_teams = st.multiselect(
            "PICK SPECIFIC TEAM",
            options=team_options,
            default=[],
            placeholder="",
            label_visibility="collapsed",
        )
 
    # ---- main feed ----
    with col_main:
        season_label = _season_display(selected_season)
        st.markdown(
            f'<div class="brand">EYE ON PAPER<span class="dot">.</span></div>'
            f'<div class="brand-sub">{season_label} championship odds &amp; seed projections '
            '— Monte-Carlo baseline</div>',
            unsafe_allow_html=True,
        )

        if show_formula:
            _render_formula_view(
                selected_season, conf_pick, selected_teams,
                pos_all, pos_g, pos_f, pos_c,
            )
            return

        try:
            df = load_sim_results(selected_season).copy()
        except Exception as e:
            st.warning(
                "Couldn't read `simulation_results`. Generate it by running "
                "`run_monte_carlo.py` first."
            )
            st.caption(f"Details: {e}")
            return

        if df.empty:
            st.info(
                f"Limited data for {_season_display(selected_season)} — no production "
                "simulation results are stored for this season yet."
            )
            return
 
        if conf_pick != "Both":
            df = df[df["conference"] == conf_pick]
 
        # choose which columns to surface based on the checkboxes
        cols = ["team", "conference"]
        if show_precise:
            # Full detail: every seed (1–15) and every playoff stage.
            seed_cols = sorted(
                [c for c in df.columns
                 if c.startswith("seed_") and c.endswith("_pct")],
                key=lambda c: int(c.split("_")[1]),
            )
            stage_cols = [c for c in ["first_round_pct", "second_round_pct",
                                      "conf_finals_pct", "made_finals_pct",
                                      "champion_pct"] if c in df.columns]
            cols += [c for c in ["avg_wins", "make_playoffs_pct", "proj_seed"]
                     if c in df.columns]
            cols += seed_cols
            cols += stage_cols
        elif show_main:
            cols += [c for c in ["champion_pct", "made_finals_pct", "conf_finals_pct"]
                     if c in df.columns]
        else:
            cols += [c for c in ["champion_pct", "avg_wins", "proj_seed"] if c in df.columns]
 
        sort_key = "champion_pct" if "champion_pct" in df.columns else cols[-1]
        out = df[cols].sort_values(sort_key, ascending=False).reset_index(drop=True)
 
        rename = {
            "team": "Team", "conference": "Conf", "champion_pct": "Champ %",
            "made_finals_pct": "Finals %", "conf_finals_pct": "Conf Finals %",
            "avg_wins": "Avg Wins", "make_playoffs_pct": "Playoffs %",
            "proj_seed": "Proj Seed",
            "first_round_pct": "First Round %", "second_round_pct": "Second Round %",
        }
        rename.update({f"seed_{i}_pct": f"Seed {i} %" for i in range(1, 16)})
        out = out.rename(columns=rename)
        heat = "Champ %" if "Champ %" in out.columns else None

        # Team filter: keep only the explicitly selected team abbreviations.
        if selected_teams:
            out = out[out["Team"].isin(selected_teams)]

        # Unified empty state: a Conference + Team mismatch wipes the dataset for
        # BOTH tables, so handle it once here before any subheaders are drawn.
        if out.empty:
            st.markdown(
                '<div style="text-align: center; color: white; font-weight: bold; '
                f'font-size: 24px; margin-top: 50px;">{_conf_mismatch_msg(selected_teams)}'
                '</div>',
                unsafe_allow_html=True,
            )
            # st.image("assets/error_pic.png", use_container_width=True)
            return

        st.markdown('<div class="eop-eyebrow">Baseline projections</div>',
                    unsafe_allow_html=True)
        # Freeze the Team column (keep header sticky) by making Team the index.
        out = out.set_index("Team")
        if selected_teams:
            # Size the table to the visible rows so there are no empty cells.
            row_px = 35
            dyn_height = int((len(out) + 1) * row_px + 3)
            st.dataframe(style_table(out, heat_col=heat),
                         use_container_width=True, height=dyn_height)
        else:
            st.dataframe(style_table(out, heat_col=heat),
                         use_container_width=True, height=560)

        if show_players:
            st.markdown('<div class="eop-eyebrow">Player power ratings</div>',
                        unsafe_allow_html=True)
            try:
                pdf = load_player_pr(selected_season).copy()
            except Exception as e:
                st.info(
                    f"Limited data for {_season_display(selected_season)} — player ratings "
                    "could not be loaded."
                )
                st.caption(f"Details: {e}")
                return

            if pdf.empty:
                st.info(
                    f"Limited data for {_season_display(selected_season)} — no player "
                    "power ratings are stored for this season."
                )
                return

            # Apply the same global filters used on the projections table.
            if conf_pick != "Both" and "conference" in pdf.columns:
                pdf = pdf[pdf["conference"] == conf_pick]
            if selected_teams and "team_abbr" in pdf.columns:
                pdf = pdf[pdf["team_abbr"].isin(selected_teams)]

            pos_col = next((c for c in ["mapped_position", "position", "pos"]
                            if c in pdf.columns), None)
            selected_pos = [k for k, v in {"G": pos_g, "F": pos_f, "C": pos_c}.items() if v]
            # "ALL" (or nothing selected) → show every position.
            if pos_all or not selected_pos:
                selected_pos = ["G", "F", "C"]
            if pos_col:
                pdf = pdf[pdf[pos_col].astype(str).str.strip().str.upper().isin(selected_pos)]
 
            pr_col = "pr" if "pr" in pdf.columns else None
            if pr_col:
                pdf = pdf.sort_values(pr_col, ascending=False)
            display_cols = [c for c in ["player_name", pos_col, pr_col,
                                        "playoff_pr", "team_abbr", "conference"]
                            if c in pdf.columns]
            pdisp = pdf[display_cols].reset_index(drop=True).rename(
                columns={"player_name": "Player", "team_abbr": "Team",
                         "conference": "Conf", "pr": "PR", "playoff_pr": "Playoff PR",
                         "mapped_position": "Pos", "position": "Pos", "pos": "Pos"}
            )
            if pdisp.empty:
                st.info(
                    f"Limited data for {_season_display(selected_season)} — no players "
                    "match the current filters."
                )
                return
            st.dataframe(
                style_table(pdisp,
                            heat_col="PR" if "PR" in pdisp.columns else None),
                use_container_width=True, hide_index=True, height=440,
            )
 
 
# Interactive sim tabs stay pinned to the current (2025-26) production season.
INTERACTIVE_SIM_SEASON = "2025-26"


def view_adjust_variables(col_main, col_filters) -> None:
    try:
        base_profiles, abbrs = load_base_profiles()
        sim_ok = True
    except Exception as e:
        base_profiles, abbrs = [], []
        sim_ok = False
        load_err = e
 
    # ---- right panel: the engine knobs + the run button ----
    with col_filters:
        st.markdown('<div class="eop-eyebrow">Engine multipliers</div>',
                    unsafe_allow_html=True)
 
        scope = st.selectbox(
            "Apply to",
            ["All teams"] + abbrs if abbrs else ["All teams"],
            help="Scale knobs league-wide, or isolate one club to see its odds move.",
            index=(["All teams"] + abbrs).index("OKC") if "OKC" in abbrs else 0,
        )
        coach_scale = st.slider("Coach impact", 0.85, 1.20, 1.00, 0.01,
                                help="Scales coach_mult (RS) and amplified coach_mult (PO).")
        ps_scale = st.slider("Playstyle weight", 0.85, 1.20, 1.00, 0.01,
                             help="Scales rs_playstyle_mult and po_playstyle_mult.")
        eff_scale = st.slider("Effect value", 0.85, 1.20, 1.00, 0.01,
                              help="Scales the continuity multipliers (RS & PO).")
        n_sims = st.slider("Simulations", 20, 200, 50, 10,
                           help="Fewer = faster. The DB baseline uses 1000.")
        run = st.button("Run simulation")
 
    # ---- main feed ----
    with col_main:
        st.markdown(
            '<div class="brand" style="font-size:1.7rem;">Adjust Variables</div>'
            '<div class="brand-sub">Tweak the engine knobs, then re-run the season '
            'in memory. Your database is never touched.</div>',
            unsafe_allow_html=True,
        )
        st.caption(
            f"What-if analysis runs on the current "
            f"({_season_display(INTERACTIVE_SIM_SEASON)}) season only. "
            "Historical season browsing is available on My System."
        )
 
        if not sim_ok:
            st.warning("Couldn't load team profiles from the database.")
            st.caption(f"Details: {load_err}")
            return
 
        st.markdown(
            '<div class="eop-card"><div class="eop-eyebrow">How this works</div>'
            'Sliders multiply the live <code>TeamProfile</code> values on an '
            'in-memory copy, then the season + playoffs are re-simulated with '
            '<code>run_one_full_sim</code>. Press <b>Run simulation</b> to apply — '
            'nothing recomputes while you drag.</div>',
            unsafe_allow_html=True,
        )
 
        if not run:
            st.info("Set your multipliers on the right, then press **Run simulation**.")
            return
 
        # Deep-copy so cached base profiles stay pristine, then mutate the copy.
        profiles = copy.deepcopy(base_profiles)
        targets = set(abbrs) if scope == "All teams" else {scope}
        for p in profiles:
            if p.abbr in targets:
                p.coach_mult *= coach_scale
                p.amp_coach_mult *= coach_scale
                p.rs_playstyle_mult *= ps_scale
                p.po_playstyle_mult *= ps_scale
                p.continuity_mult_rs *= eff_scale
                p.continuity_mult_po *= eff_scale
 
        with st.spinner(f"Running {n_sims} full-season simulations…"):
            sim_df = run_inmemory_sim(profiles, abbrs, n_sims)
 
        # Compare against the stored baseline where available.
        try:
            base = load_sim_results(INTERACTIVE_SIM_SEASON)[
                ["team", "champion_pct", "avg_wins"]
            ].rename(
                columns={"champion_pct": "base_champ", "avg_wins": "base_wins"}
            )
            merged = sim_df.merge(base, on="team", how="left")
            merged["Δ Champ %"] = (merged["champion_pct"] - merged["base_champ"]).round(2)
            merged["Δ Wins"] = (merged["avg_wins"] - merged["base_wins"]).round(2)
        except Exception:
            merged = sim_df
            merged["Δ Champ %"] = np.nan
            merged["Δ Wins"] = np.nan
 
        out = merged[
            ["team", "conference", "avg_wins", "proj_seed",
             "make_playoffs_pct", "champion_pct", "made_finals_pct",
             "Δ Champ %", "Δ Wins"]
        ].sort_values("champion_pct", ascending=False).reset_index(drop=True)
        out = out.rename(columns={
            "team": "Team", "conference": "Conf", "avg_wins": "Avg Wins",
            "proj_seed": "Proj Seed", "make_playoffs_pct": "Playoffs %",
            "champion_pct": "Champ %", "made_finals_pct": "Finals %",
        })
 
        scope_label = "league-wide" if scope == "All teams" else f"on {scope}"
        st.markdown(
            f'<div class="eop-eyebrow">Scenario result · {n_sims} sims · {scope_label}'
            '</div>', unsafe_allow_html=True)
        st.dataframe(style_table(out, heat_col="Champ %"),
                     use_container_width=True, hide_index=True, height=560)
        st.caption(
            "Δ columns compare this scenario against the stored 1000-run baseline. "
            "Small sample sizes carry sampling noise — bump simulations up for "
            "steadier numbers."
        )
 
 
def view_current_formula(col_main, col_filters) -> None:
    with col_filters:
        st.markdown('<div class="eop-eyebrow">On this page</div>', unsafe_allow_html=True)
        st.markdown(
            "- The pipeline\n- Power ratings\n- Multiplier layers\n- "
            "Season &amp; playoff sim\n- What stays hidden"
        )
 
    with col_main:
        st.markdown(
            '<div class="brand" style="font-size:1.7rem;">Current Formula</div>'
            '<div class="brand-sub">How the engine thinks — the concepts, not the '
            'coefficients.</div>',
            unsafe_allow_html=True,
        )
        # NOTE: deliberately conceptual. No exact weights / equations are shown.
        st.markdown(
            """
The engine turns a roster into a single, comparable team strength and then plays
out a full season thousands of times. Here's the shape of it, kept at the level
of ideas.
 
**1 · Power ratings (PR).** Every player carries two ratings — one for the regular
season and one for the playoffs. A separate durability profile estimates how
reliably they're available on any given night.
 
**2 · Depth before strength.** For each game the engine assembles a realistic
nine-man rotation: starters by position, then the bench, then the next man up.
If a starter is unavailable, the best like-for-like replacement steps in. Nobody
plays twice.
 
**3 · Context multipliers.** A team's raw rating is shaped by a few situational
layers — coaching quality, offensive playstyle fit, and early-season continuity.
The playoffs use amplified versions of these, plus a star-impact effect for a
club's best players.
 
**4 · Home court & fatigue.** Each matchup nudges the home side up and applies an
occasional road-fatigue penalty before a probabilistic winner is drawn.
 
**5 · The full picture.** An 82-game schedule produces standings; the play-in and
a seeded best-of-seven bracket decide each conference; the two champions meet for
the title. Repeat across many simulations and the frequencies become odds.
            """
        )
        st.markdown(
            '<div class="eop-card" style="border-color:%s;">'
            '<div class="eop-eyebrow" style="color:%s;">Black box</div>'
            'The exact equations, weights, and coefficients that turn these layers '
            'into a number are proprietary and intentionally not shown here. This '
            'page explains <i>how the system reasons</i>, not the recipe.</div>'
            % (PALETTE["accent"], PALETTE["accent"]),
            unsafe_allow_html=True,
        )
 
 
def view_coming_soon(col_main, col_filters, title: str, blurb: str) -> None:
    with col_filters:
        st.markdown('<div class="eop-eyebrow">Status</div>', unsafe_allow_html=True)
        st.markdown("🔒 Locked — **v2.0**")
 
    with col_main:
        st.markdown(
            f'<div class="brand" style="font-size:1.7rem;">{title}</div>'
            f'<div class="brand-sub">{blurb}</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="eop-card" style="text-align:center; padding:48px 18px;">'
            '<div style="font-size:2.4rem; margin-bottom:.4rem;">🚧</div>'
            '<div style="font-size:1.15rem; font-weight:700;">Coming in Version 2.0</div>'
            f'<div style="color:{PALETTE["muted"]}; margin-top:.4rem;">'
            'This module is under construction.</div></div>',
            unsafe_allow_html=True,
        )
 
 
def view_creator(col_main, col_filters) -> None:
    with col_filters:
        st.markdown('<div class="eop-eyebrow">Connect</div>', unsafe_allow_html=True)
        st.markdown(
            "- [GitHub](https://github.com/your-handle)\n"
            "- [LinkedIn](https://linkedin.com/in/your-handle)\n"
            "- [Email](mailto:you@example.com)"
        )
 
    with col_main:
        st.markdown(
            '<div class="brand" style="font-size:1.7rem;">Creator</div>'
            '<div class="brand-sub">The analyst behind EYE ON PAPER.</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            """
**[Your Name]** — Data Analyst
 
I build predictive systems that turn messy sports data into decisions you can
defend. EYE ON PAPER is my NBA engine: a Monte-Carlo simulator that fuses player
power ratings, durability, coaching, and playstyle into season-long championship
odds.
 
**What's under the hood**
- A SQLite data layer for ratings, rosters, and durability profiles
- A pure-Python / NumPy simulation that plays out thousands of full seasons
- This Streamlit front end — stateless, read-only, and shareable
 
**Stack:** Python · pandas · NumPy · SQLite · Streamlit
 
*Replace the links on the right and this bio with your own details before
shipping.*
            """
        )
 
 
# ===========================================================================
# APP SHELL
# ===========================================================================
 
def main() -> None:
    inject_css()

    # ---- Borderless 3-column shell: nav (left) | feed (center) | filters (right) ----
    col_nav, col_main, col_filters = st.columns([1, 3, 1], gap="large")

    with col_nav:
        st.markdown('<div class="eop-logo">EYE ON PAPER</div>',
                    unsafe_allow_html=True)
        nav = st.radio("nav", NAV_ITEMS, label_visibility="collapsed")
        st.markdown('<div class="eop-note">Read-only demo · database untouched</div>',
                    unsafe_allow_html=True)

    if nav == "My System":
        view_my_system(col_main, col_filters)
    elif nav == "Adjust Variables":
        view_adjust_variables(col_main, col_filters)
    elif nav == "Current Formula":
        view_current_formula(col_main, col_filters)
    elif nav == "What If?":
        view_coming_soon(col_main, col_filters, "What If?",
                         "Swap players between teams and re-run the season.")
    elif nav == "AI Bot":
        view_coming_soon(col_main, col_filters, "AI Bot",
                         "Ask the engine questions in plain language.")
    elif nav == "Creator":
        view_creator(col_main, col_filters)
 
 
if __name__ == "__main__":
    main()