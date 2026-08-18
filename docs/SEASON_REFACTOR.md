# EYEonPAPER — Season Parameterization, Orchestrator & Backtesting

> Steps 4, 5, 6 of the audit. The body below is the original specification snapshot.
> **Status (2026-08):** the parameterization and backtest harness are in place; several
> extras from this spec were never built.

| Item | Status |
|------|--------|
| `season_utils.py` + `--season` as target, source = target − 1 | ✅ done |
| Every pipeline step takes `(source_season, target_season)` | ✅ done |
| `pipeline.py --season <target> --stage {features,projection,simulation,all}` | ✅ done |
| Leakage guards (`leakage_guards.py`: FMVP, pedigree, summer hires, draft year) | ✅ done |
| Continuity data-derived (not hardcoded team lists) | ✅ done |
| Historical production sims 2018-19 → 2025-26 | ✅ done |
| Scoring: `compute_baseline_scores.py` + `compute_engine_scores.py` (not a single `backtest.py`) | ✅ done |
| `ingest` stage inside `pipeline.py` | ⬜ still spec — fetch stays offline |
| `pipeline.py --dry-run` and `--from` resume | ⬜ still spec |
| `requirements.txt` | ⬜ still spec |

End state that **did** land: `pipeline.py --season 2025-26 --stage all` runs projection +
simulation for any target season, and the two scoring scripts compare predictions to
actuals already in the DB.

---

---

## 4. Season-parameterization map

The projection/simulation layers hardcode the season in three ways: **constants**
(`TARGET_SEASON`), **string literals** in SQL/`WHERE`, and **table names** (`_25_26`).
The table-name renames are covered in `CLEANUP_PLAN.md` §3–4; this section maps the
**constants and literals** and, critically, the **silent "latest season" assumptions**.

### 4a. Mental model to fix first

Two different seasons are involved and the code conflates them:

- **`source_season`** = the season of actual stats used as input (currently `2024-25`).
- **`target_season`** = the season being predicted (currently `2025-26`; only lives in
  table names + the rookie/starting-team fetchers).

The refactor should make **`target_season` the single CLI parameter**, and derive
`source_season = target_season − 1` (the last completed season) explicitly. Rookies and
starting rosters use `target_season`; historical stats use `source_season`.

### 4b. Explicit reference map (file : line : literal → becomes)

**Constants (`TARGET_SEASON` / `SEASON` / `BASELINE_SEASON`):**

| File | Line | Current literal | Becomes |
|---|---|---|---|
| `calculate_base_pr.py` | 25 | `TARGET_SEASON = "2024-25"` | `args.source_season` |
| `calculate_base_pr.py` | 135 | `WHERE b.season = '2024-25'` | `WHERE b.season = ?` bind `source_season` |
| `calculate_player_pr.py` | 32 | `TARGET_SEASON = "2024-25"` | `args.source_season` |
| `apply_progression.py` | 20 | `TARGET_SEASON = "2024-25"` | `args.source_season` |
| `apply_playoff_experience_pr.py` | 21 | `TARGET_SEASON = "2024-25"` | `args.source_season` |
| `calculate_final_simulation_pr.py` | 17 | `TARGET_SEASON = "2024-25"` | `args.source_season` |
| `calculate_team_pr.py` | 26 | `TARGET_SEASON = "2024-25"` | (dead branch — archive; else `args.source_season`) |
| `calculate_rookie_projected_pr_25_26.py` | 20 | `SEASON = "2025-26"` | `args.target_season` |
| `fetch_player_starting_teams_25_26.py` | 45 | `season="2025-26"` | `args.target_season` |
| `fetch_team_coaches_25_26.py` | 29 | `BASELINE_SEASON = "2024-25"` | `args.source_season` |
| `create_player_positions.py` | 66 | `WHERE season = '2024-25'` | `WHERE season = ?` bind `source_season` |
| `build_projected_team_pr_25_26.py` | 141 | `WHERE season = '2024-25'` (playstyle) | bind `source_season` |
| `build_team_playoff_pr_25_26.py` | 40 | `PLAYSTYLE_SEASON = "2024-25"` | `args.source_season` |
| `build_composite_clutch_index.py` | 33 | `SEASONS = ["2022-23","2023-24","2024-25"]` | trailing 3-yr window ending at `source_season` |
| `build_player_durability_profiles.py` | 136,144,175,253 | `'2023-24'`,`'2024-25'` | `source_season` and `source_season − 1` |

**Table-name literals to season-key** (see `CLEANUP_PLAN.md` §4a for target names):
`projected_team_pr_25_26`, `team_playoff_pr_25_26`, `player_starting_teams_25_26`,
`rookie_projected_pr_25_26`, `team_coaches_25_26`, `simulation_results_25_26` — appear in
`run_monte_carlo.py:562,577,586,594,809-828`, `app.py:352,396`,
`build_projected_team_pr_25_26.py:126-232`, `build_team_playoff_pr_25_26.py:200-297`, etc.

### 4c. Silent "latest season" assumptions that WILL break a historical run

These do not use a literal — they compute the season at runtime as the max/latest, which
silently means "2025-26" today and would produce wrong output for a backtest unless
constrained to `≤ target_season`:

1. **`calculate_projected_team_pr.py:340` / `:460`** — `target_season = max(seasons)` over
   `player_simulation_pr`. For a 2023-24 backtest this must be pinned, not `max()`.
   (Script is on the dead branch — archive it, but the pattern is the risk.)
2. **`apply_coach_multipliers.py:132`** — `SELECT MAX(season) FROM team_simulation_pr` to
   pick which season to report; would grab the newest present. (Dead branch.)
3. **`assign_team_playstyles.py:595,645`** — iterates `SELECT DISTINCT season` / writes all
   seasons; fine for a feature table but confirm it does not silently skip a season absent
   from raw data.
4. **`fetch_team_coaches_25_26.py`** — baseline = "final coach per team for
   `source_season`" plus a hardcoded `summer_hires` override dict
   (`fetch_team_coaches_25_26.py:34-57`, e.g. `"NYK": "Mike Brown"`). For a **historical**
   target season this override dict is wrong — it encodes 2025 off-season news. A backtest
   must **disable summer overrides** and use the actual end-of-source-season coach.
   *This is the single most important correctness trap for backtesting.*
5. **`build_team_playoff_pr_25_26.py:49-50`** — `HIGH_CONTINUITY_TEAMS` /
   `LOW_CONTINUITY_TEAMS` are hardcoded 2025-26 rosters. For a historical run these
   continuity sets are wrong; they must become season-specific (or derived from roster
   turnover between `source_season−1` and `source_season`).

**Rule of thumb for the refactor:** replace every `max(season)` / "latest" with the
explicit `source_season` argument, and gate every hardcoded team list / summer-hire dict
behind "only when `target_season == 2025-26`", else compute from data.

---

## 5. Orchestrator + reproducibility (spec)

### 5a. `pipeline.py` — single entry point

```
python pipeline.py --season 2025-26 --stage all
python pipeline.py --season 2024-25 --stage projection   # re-run one stage
python pipeline.py --season 2025-26 --stage all --dry-run # plan only, no writes
```

- `--season` = **target season** (predicted). The pipeline derives
  `source_season = season − 1` internally.
- `--stage` ∈ `{ingest, features, projection, simulation, all}` (default `all`).
- Run order is **derived from the dependency graph in `ARCHITECTURE.md`**, not invented.
  Each stage runs its scripts in dependency order:

```
STAGE ingest      (Layer 0)
  init_db
  fetch_player_basic → hydrate_player_basic
  fetch_player_advanced → sync_positions
  fetch_playoff_basic → fetch_playoff_usg
  fetch_playoff_advanced
  fetch_team_stats → hydrate_prev_playoff_result
  fetch_team_playoffs → backfill_prev_team_playoffs
  fetch_league_stats
  fetch_coach_data
  fetch_rookie_data → hydrate_rookie_signing_teams
  fetch_redshirt_data → sync_redshirts
  fetch_player_starting_teams(--season target)

STAGE features    (Layer 1)
  create_player_positions
  init_yearly_player_effects → calculate_offensive_effects → calculate_mvp_potential
  init_progression_curves
  init_rookie_baselines
  create_playstyle_multipliers
  assign_team_playstyles
  init_coach_systems → update_coach_grades
  fetch_team_coaches(--season target)
  calculate_rookie_projected_pr(--season target)
  build_composite_clutch_index

STAGE projection  (Layer 2)  [strict order — each reads the previous]
  calculate_base_pr  (the ONE chosen writer; see CLEANUP_PLAN H2)
  apply_progression
  apply_playoff_experience_pr
  calculate_final_simulation_pr
  build_ultimate_pr
  update_ultimate_pr_positions
  calculate_ultimate_playoff_pr
  apply_pedigree_trajectory_boost
  build_player_durability_profiles     # depends on ultimate_playoff_pr — MUST be here, not in features
  build_projected_team_pr(--season target)
  build_team_playoff_pr(--season target)

STAGE simulation  (Layer 3)
  run_monte_carlo(--season target)
```

- **Implementation shape:** a declarative list of `(name, callable, stage, depends_on)`.
  Each existing script exposes a `main(source_season, target_season, dry_run)` (small,
  mechanical refactor of the current `main()`), and `pipeline.py` calls them in-process
  (so a failure halts the run with a clear "stage X, step Y failed"). No shell glue.
- **Fail-fast + resume:** `--from calculate_base_pr` to resume mid-pipeline; each step
  logs `[stage/step] wrote N rows to <table> for season <target>`.

### 5b. `requirements.txt` (inferred from actual imports)

Imports found across the repo: `pandas`, `numpy`, `streamlit`, `nba_api`, `requests`,
`bs4` (BeautifulSoup4). `sqlite3` is stdlib. Code uses `X | None` syntax and
`from __future__ import annotations`, so **Python ≥ 3.10**.

```text
# EYEonPAPER runtime dependencies (pin majors; adjust to your resolved versions)
pandas>=2.0,<3.0
numpy>=1.26,<3.0
streamlit>=1.30,<2.0
nba_api>=1.4,<2.0
requests>=2.31,<3.0
beautifulsoup4>=4.12,<5.0
```

> Note: pin to the majors above, then run `pip freeze` once in your working venv to lock
> exact patch versions. `nba_api` transitively pulls `requests`; keep it explicit anyway.

### 5c. Where `--dry-run` / idempotency checks go

- **Idempotency already exists** in most writers: they either `DROP`+recreate
  (`build_team_playoff_pr_25_26.py:282`), `DELETE FROM t WHERE season=?`+insert
  (`calculate_base_pr.py:277`, `apply_progression.py:174`), or `INSERT ... ON CONFLICT ...
  DO UPDATE` (`fetch_league_stats.py:113`, `fetch_player_basic.py:281`). Re-running a
  season is therefore safe **per script** — preserve this contract in the refactor:
  every writer must be "delete-this-season-then-insert" or upsert, never blind append.
- **`--dry-run` contract:** each `main()` takes `dry_run: bool`; when true it runs SELECTs,
  prints "would write N rows to <table> for <season>", and performs **no** INSERT/UPDATE/
  DROP/DELETE and no `con.commit()`. `pipeline.py --dry-run` threads it to every step so
  you can validate ordering and row counts before committing.
- **Post-stage assertions** (cheap safety net): after each stage, assert expected row
  counts (e.g. `team_projection` has 30 rows for the target season; `ULTIMATE_PR` non-empty)
  and fail loudly if a season silently produced 0 rows (the classic "wrong season → empty
  join" failure, cf. `calculate_base_pr.py:262`).

---

## 6. Backtesting harness (the CV-credibility feature)

Once the pipeline is season-parameterized, backtesting is "run the pipeline with the
future hidden, then score against reality you already have in the DB".

### 6a. Protocol (no leakage)

For a target season **N+1**, backtest means:

1. **Train/project using only seasons ≤ N.** Concretely: run
   `pipeline.py --season N+1 --stage projection,simulation` but force every
   feature/projection input to filter `season <= N`. The raw tables already hold all six
   seasons, so leakage is prevented by the `WHERE season <= ?` guards, **not** by deleting
   data. Audit the five silent-latest traps in §4c so none reach into season N+1.
2. **Neutralize future-only knowledge:** disable the `summer_hires` override dict
   (`fetch_team_coaches_25_26.py:34`) and the hardcoded continuity sets
   (`build_team_playoff_pr_25_26.py:49-50`) for historical runs — otherwise you leak
   post-hoc information and inflate accuracy.
3. **Predict season N+1** → `simulation_results` rows tagged `season = 'N+1'`,
   `run_id = 'backtest'`.
4. **Compare to actuals**, which are already stored:
   - Actual wins & seed: `team_stats.wins`, `team_stats.conference_seed`,
     `team_stats.made_playoffs` for `season = 'N+1'`.
   - Actual playoff round & champion: `team_stats_playoffs.playoff_result` (values seen in
     `apply_playoff_experience_pr.py:24-32`: `Champion`, `Finals`, `Conf. Finals`,
     `Conf. Semifinals`, `1st Round`, `Play-In Eliminated`, `Missed Playoffs`).

Because raw data covers 2020-21 → 2025-26, you can backtest **at least 2022-23, 2023-24,
and 2024-25** as targets (each needs its N and N−1 windows for the 3-year clutch/durability
features).

### 6b. Metrics to report

| Metric | Definition | Source (pred vs actual) |
|---|---|---|
| **MAE (team wins)** | mean absolute error of `avg_wins` vs actual wins, 30 teams | `simulation_results.avg_wins` vs `team_stats.wins` |
| **RMSE (team wins)** | penalizes big misses | same |
| **Seed accuracy** | % of teams whose predicted modal seed == actual seed (and ±1 tolerance) | argmax of `seed_*_pct` vs `team_stats.conference_seed` |
| **Playoff-berth hit rate** | correct made/missed-playoffs classification | `1 - missed_playoffs_pct` threshold vs `team_stats.made_playoffs` |
| **Playoff-round hit rate** | for playoff teams, predicted furthest round vs actual | `first/second/conf_finals/finals_pct` vs `team_stats_playoffs.playoff_result` |
| **Champion top-k** | was the actual champion in the top-1 / top-4 by `champion_pct` | `simulation_results.champion_pct` vs actual `Champion` |
| **Brier score (champion)** | mean squared error of `champion_pct` (as prob) vs 1/0 outcome over 30 teams | `champion_pct/100` vs indicator |
| **Log-loss (champion)** | sharper probabilistic score; optional | same |
| **Spearman ρ (standings)** | rank correlation of predicted vs actual win order | ordered `avg_wins` vs `wins` |

Report per target season **and** pooled across backtested seasons, with a baseline
comparison (e.g. "predict last season's wins" or "predict league-average 41 wins") so the
numbers demonstrate the model beats naive baselines.

### 6c. Where `backtest.py` lives

- New top-level `backtest.py`, orchestrated by `pipeline.py`:
  `python backtest.py --target 2024-25` →
  (a) invokes `pipeline.py --season 2024-25 --stage projection,simulation` in
  "no-leakage / historical" mode (writes `simulation_results` with `run_id='backtest'`),
  then (b) reads actuals from `team_stats` / `team_stats_playoffs` for the target season,
  (c) computes the §6b metrics, (d) writes a `backtest_results` table
  (`season, metric, value, run_id, created_at`) and prints a scorecard.
- It sits **after** the `simulation` stage in the dependency graph and reuses the exact
  same code paths as production — the only difference is the `season <= N` guards and the
  disabled future-knowledge overrides. That reuse is what makes the backtest a credible
  proxy for real predictive skill.
