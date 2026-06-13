
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
    "bg":       "#0f1115",
    "surface":  "#161a22",
    "surface2": "#1c2230",
    "border":   "#232a3a",
    "text":     "#e6e9ef",
    "muted":    "#8b93a7",
    "accent":   "#e8913a",   # hardwood amber — the one bold note
    "accent_d": "#b76a22",
    "good":     "#56b89a",
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
        /* ---- Force dark surface regardless of user's base theme ---- */
        .stApp {{ background: {PALETTE['bg']}; color: {PALETTE['text']}; }}
        .block-container {{ padding-top: 1.4rem; padding-bottom: 3rem; max-width: 1240px; }}
        section.main > div {{ background: transparent; }}
 
        /* Kill the sidebar entirely in case anything tries to mount it */
        section[data-testid="stSidebar"] {{ display: none !important; }}
 
        h1, h2, h3, h4 {{ color: {PALETTE['text']}; letter-spacing: -0.01em; }}
        p, li, label, span {{ color: {PALETTE['text']}; }}
        a {{ color: {PALETTE['accent']}; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
 
        /* ---- Brand header ---- */
        .brand {{
            font-weight: 800; font-size: 2.1rem; line-height: 1;
            letter-spacing: -0.03em; margin: 0 0 .15rem 0;
        }}
        .brand .dot {{ color: {PALETTE['accent']}; }}
        .brand-sub {{ color: {PALETTE['muted']}; font-size: .9rem; margin-bottom: 1.1rem; }}
 
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
            font-size: .68rem; color: {PALETTE['muted']}; margin-bottom: .5rem;
        }}
 
        /* ---- Left nav: turn a radio group into clean menu blocks ---- */
        div[role="radiogroup"] {{ gap: 6px; }}
        /* hide the actual radio circle (first child div of each label) */
        div[role="radiogroup"] > label > div:first-child {{ display: none !important; }}
        div[role="radiogroup"] > label {{
            display: flex; align-items: center;
            width: 100%;
            padding: 11px 14px;
            margin: 0;
            border: 1px solid transparent;
            border-radius: 12px;
            background: {PALETTE['surface']};
            color: {PALETTE['text']};
            font-weight: 600; font-size: .98rem;
            cursor: pointer;
            transition: background .12s ease, border-color .12s ease, transform .04s ease;
        }}
        div[role="radiogroup"] > label:hover {{
            background: {PALETTE['surface2']};
            border-color: {PALETTE['border']};
        }}
        /* selected block (modern :has — Streamlit runs in evergreen browsers) */
        div[role="radiogroup"] > label:has(input:checked) {{
            background: {PALETTE['surface2']};
            border-color: {PALETTE['accent']};
            box-shadow: inset 3px 0 0 {PALETTE['accent']};
        }}
        div[role="radiogroup"] > label:has(input:checked) p {{ color: {PALETTE['accent']}; }}
        div[role="radiogroup"] label p {{ font-weight: 600; margin: 0; }}
 
        /* ---- Right panel widgets ---- */
        .stCheckbox, .stSlider, .stSelectbox, .stRadio {{ margin-bottom: .35rem; }}
 
        /* ---- Buttons ---- */
        .stButton > button {{
            background: {PALETTE['accent']}; color: #16110a;
            border: 0; border-radius: 10px; font-weight: 700;
            padding: .55rem 1rem; width: 100%;
            transition: background .12s ease;
        }}
        .stButton > button:hover {{ background: {PALETTE['accent_d']}; color: #fff; }}
 
        /* ---- Data tables ---- */
        [data-testid="stDataFrame"] {{
            border: 1px solid {PALETTE['border']};
            border-radius: 12px; overflow: hidden;
        }}
        [data-testid="stMetricValue"] {{ color: {PALETTE['accent']}; }}
 
        /* tighten the three columns visually */
        div[data-testid="column"] {{ padding: 0 .35rem; }}
        </style>
        """,
        unsafe_allow_html=True,
    )
 
 
# ---------------------------------------------------------------------------
# Data access — strictly read-only, cached
# ---------------------------------------------------------------------------
 
def _ro_connect() -> sqlite3.Connection:
    """Open nba_data.db in read-only mode. Guarantees the app cannot mutate it."""
    uri = f"file:{mc.DB_PATH}?mode=ro"
    return sqlite3.connect(uri, uri=True)
 
 
@st.cache_data(show_spinner=False)
def load_sim_results() -> pd.DataFrame:
    """simulation_results_25_26 — baseline championship odds + seed projections."""
    con = _ro_connect()
    try:
        df = pd.read_sql_query("SELECT * FROM simulation_results_25_26", con)
    finally:
        con.close()
 
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
def load_player_pr() -> pd.DataFrame:
    """ULTIMATE_PR — player PR ratings and positions (drives Tab 1 filters)."""
    con = _ro_connect()
    try:
        df = pd.read_sql_query("SELECT * FROM ULTIMATE_PR", con)
    finally:
        con.close()
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
    sty = df.style.set_properties(
        **{
            "background-color": PALETTE["surface"],
            "color": PALETTE["text"],
            "border-color": PALETTE["border"],
        }
    )
    if heat_col and heat_col in df.columns:
        try:
            sty = sty.background_gradient(cmap="YlOrBr", subset=[heat_col])
        except Exception:
            # matplotlib not present — degrade gracefully to flat dark cells
            pass
    fmt = {c: "{:.2f}" for c in df.select_dtypes("float").columns}
    if fmt:
        sty = sty.format(fmt)
    return sty
 
 
# ===========================================================================
# VIEWS
# ===========================================================================
 
def view_my_system(col_main, col_filters) -> None:
    # ---- right panel: filters tied to this tab ----
    with col_filters:
        st.markdown('<div class="eop-eyebrow">Filters</div>', unsafe_allow_html=True)
        show_main = st.checkbox("Main odds", value=True,
                                help="Championship / Finals / Conf Finals odds")
        show_personal = st.checkbox("Team personal odds", value=False,
                                    help="Avg wins, make-playoffs %, projected seed")
        conf_pick = st.radio("Conference", ["Both", "East", "West"], horizontal=True)
        st.markdown("---")
        show_players = st.checkbox("Show players PR", value=False)
        st.caption("Position")
        pos_g = st.checkbox("Guards", value=True)
        pos_f = st.checkbox("Forwards", value=True)
        pos_c = st.checkbox("Centers", value=True)
 
    # ---- main feed ----
    with col_main:
        st.markdown(
            '<div class="brand">EYE ON PAPER<span class="dot">.</span></div>'
            '<div class="brand-sub">2025–26 championship odds &amp; seed projections '
            '— Monte-Carlo baseline</div>',
            unsafe_allow_html=True,
        )
 
        try:
            df = load_sim_results().copy()
        except Exception as e:
            st.warning(
                "Couldn't read `simulation_results_25_26`. Generate it by running "
                "`run_monte_carlo.py` first."
            )
            st.caption(f"Details: {e}")
            return
 
        if conf_pick != "Both":
            df = df[df["conference"] == conf_pick]
 
        # choose which columns to surface based on the checkboxes
        cols = ["team", "conference"]
        if show_main:
            cols += [c for c in ["champion_pct", "made_finals_pct", "conf_finals_pct"]
                     if c in df.columns]
        if show_personal:
            cols += [c for c in ["avg_wins", "make_playoffs_pct", "proj_seed"]
                     if c in df.columns]
        if not show_main and not show_personal:
            cols += [c for c in ["champion_pct", "avg_wins", "proj_seed"] if c in df.columns]
 
        sort_key = "champion_pct" if "champion_pct" in df.columns else cols[-1]
        out = df[cols].sort_values(sort_key, ascending=False).reset_index(drop=True)
 
        rename = {
            "team": "Team", "conference": "Conf", "champion_pct": "Champ %",
            "made_finals_pct": "Finals %", "conf_finals_pct": "Conf Finals %",
            "avg_wins": "Avg Wins", "make_playoffs_pct": "Playoffs %",
            "proj_seed": "Proj Seed",
        }
        out = out.rename(columns=rename)
        heat = "Champ %" if "Champ %" in out.columns else None
 
        st.markdown('<div class="eop-eyebrow">Baseline projections</div>',
                    unsafe_allow_html=True)
        st.dataframe(style_table(out, heat_col=heat),
                     use_container_width=True, hide_index=True, height=560)
 
        if show_players:
            st.markdown('<div class="eop-eyebrow">Player power ratings</div>',
                        unsafe_allow_html=True)
            try:
                pdf = load_player_pr().copy()
            except Exception as e:
                st.caption(f"ULTIMATE_PR unavailable: {e}")
                return
 
            pos_col = next((c for c in ["mapped_position", "position", "pos"]
                            if c in pdf.columns), None)
            wanted = {"G": pos_g, "F": pos_f, "C": pos_c}
            keep = [k for k, v in wanted.items() if v]
            if pos_col and keep:
                pdf = pdf[pdf[pos_col].astype(str).str.strip().str.upper().isin(keep)]
 
            pr_col = "pr" if "pr" in pdf.columns else None
            if pr_col:
                pdf = pdf.sort_values(pr_col, ascending=False)
            display_cols = [c for c in ["player_name", pos_col, pr_col,
                                        "playoff_pr", "team_abbr"] if c in pdf.columns]
            st.dataframe(
                style_table(pdf[display_cols].reset_index(drop=True),
                            heat_col=pr_col if pr_col in display_cols else None),
                use_container_width=True, hide_index=True, height=440,
            )
 
 
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
            base = load_sim_results()[["team", "champion_pct", "avg_wins"]].rename(
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
 
    col_nav, col_main, col_filters = st.columns([1, 2.5, 1], gap="large")
 
    with col_nav:
        st.markdown('<div class="eop-eyebrow" style="margin-bottom:.7rem;">'
                    'EYE ON PAPER</div>', unsafe_allow_html=True)
        nav = st.radio("nav", NAV_ITEMS, label_visibility="collapsed")
        st.markdown(
            f'<div style="margin-top:1.4rem; color:{PALETTE["muted"]}; '
            'font-size:.72rem; line-height:1.5;">Read-only demo · '
            'database untouched</div>',
            unsafe_allow_html=True,
        )
 
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