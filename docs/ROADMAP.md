# EYEonPAPER — Phased Roadmap

> Step 7. Each phase leaves the repo in a **working state** (the current single-season
> flow keeps running until it is deliberately replaced). Do phases in order; do not start
> a phase until the previous "definition of done" is met. No code has been changed yet.

Legend: DoD = Definition of Done.

---

## Phase 0 — Cleanup & safety net (no behavior change)

**Goal:** remove dead weight, capture dependencies, and put a thin orchestrator over the
**current** single-season flow so nothing breaks and the run order stops living in your head.

**Files touched**
- Archive (via `git mv` into `archive/`, à la `archive.py`): `monte_carlo_season_25_26.py`,
  `calculate_playoff_riser_choker.py`, `init_player_effects.py`, `calculate_team_pr.py`,
  `calculate_team_pr_base.py`, `calculate_projected_team_pr.py`, `apply_coach_multipliers.py`,
  and the loser of `calculate_base_pr` vs `calculate_player_pr` (decide via
  `CLEANUP_PLAN.md` H2).
- Add `requirements.txt` (`SEASON_REFACTOR.md` §5b).
- Add `pipeline.py` that calls the **existing** scripts in dependency order (no
  parameterization yet — just `--stage`). Add `docs/` (this audit).
- Update `reset_database.py` to stop referencing `team_simulation_pr`.

**DB changes**
- Drop the orphaned `team_coaches` relic and the dead `team_simulation_pr` **after**
  confirming no references (queries in `CLEANUP_PLAN.md` §2). Take a `nba_data.db` backup
  first.

**Risk:** Low. Only dead/duplicate scripts move; the live path is untouched. Main risk is
archiving the *wrong* one of the two base-PR scripts — mitigate with the H2 query before
moving.

**DoD:** `python pipeline.py --stage all` reproduces today's `simulation_results_25_26`
(same 30 rows, champion odds within simulation noise); `pip install -r requirements.txt`
succeeds in a clean venv; `git status` shows the 8 archived scripts and 0 changes to live
logic.

---

## Phase 1 — Schema consolidation (season-keyed tables + migration)

**Goal:** make `season` a column, not a table-name suffix; delete/merge per
`CLEANUP_PLAN.md` §4. Still single-season in behavior.

**Files touched**
- One idempotent migration script (`migrations/001_season_key.py`): renames the six
  `_25_26` tables to season-keyed general tables, adds `season` columns + PKs to
  `player_experience_pr`, `final_simulation_pr`, `ULTIMATE_PR`, `ultimate_playoff_pr`,
  `player_positions`, `player_durability_profiles`, and backfills existing rows
  (`'2025-26'` for target-named tables; `'2024-25'` for source-named projection tables).
- Update writers/readers to the new table names + `WHERE season = ?` (mechanical):
  `run_monte_carlo.py`, `app.py`, `build_projected_team_pr*`, `build_team_playoff_pr*`,
  `calculate_rookie_projected_pr*`, `fetch_team_coaches*`, `fetch_player_starting_teams*`.
- (Optional) fold `final_simulation_pr` into `build_ultimate_pr` (`CLEANUP_PLAN.md` §4c).

**DB changes:** 34 → ~30 real tables; all projection/sim tables season-keyed. Reversible
via backup.

**Risk:** Medium. Rename + PK changes can silently break a `WHERE season` filter or a
join. Mitigate: run migration on a **copy** of `nba_data.db`, then diff
`simulation_results` before/after (must match Phase 0 output).

**DoD:** No table name contains `_25_26`; `pipeline.py --stage all` still reproduces the
baseline sim; a fresh `SELECT DISTINCT season` on every projection table returns the
expected single season.

---

## Phase 2 — Season parameterization (target_season end-to-end)

**Goal:** `pipeline.py --season 2024-25 --stage all` runs the full projection+sim for any
target season, deriving `source_season = season − 1`.

**Files touched**
- Every projection/feature script: replace `TARGET_SEASON`/`SEASON`/`BASELINE_SEASON`
  constants and literal `WHERE season = '...'` with function args
  (`SEASON_REFACTOR.md` §4b). Convert each `main()` to
  `main(source_season, target_season, dry_run)`.
- Eliminate the five silent "latest season" assumptions (`SEASON_REFACTOR.md` §4c),
  especially the `summer_hires` override and hardcoded continuity team sets — gate them
  behind `target_season == '2025-26'` or derive from data.
- `pipeline.py` gains `--season`, `--from`, `--dry-run`; threads `dry_run` to every step;
  adds post-stage row-count assertions.

**DB changes:** none structural — but the DB now holds multiple seasons of projection/sim
output side by side (that is the payoff of Phase 1's `season` columns).

**Risk:** Medium-High. The `WHERE season` guards are where a wrong season yields an empty
join and a silently empty table. Mitigate with the post-stage assertions and by first
re-running `--season 2025-26` and confirming it matches the Phase 1 baseline exactly.

**DoD:** `pipeline.py --season 2025-26` reproduces the baseline; `pipeline.py --season
2024-25` produces a full, non-empty projection + sim for 2024-25 with no code edits between
runs; `--dry-run` writes nothing.

---

## Phase 3 — Backtesting harness + first accuracy report

**Goal:** demonstrate predictive skill by predicting a known season with the future hidden
and scoring it — the headline CV feature.

**Files touched**
- New `backtest.py` (`SEASON_REFACTOR.md` §6c): runs the pipeline in no-leakage/historical
  mode (`season <= N`, future-knowledge overrides disabled), then scores.
- New `backtest_results` table (`season, metric, value, run_id, created_at`).
- `pipeline.py` learns a `backtest` stage; optional `app.py` tab to show the scorecard.

**DB changes:** add `backtest_results`; `simulation_results` gains `run_id`/`created_at`
(from Phase 1) to separate backtest runs from production.

**Risk:** Medium. The credibility of the whole exercise depends on **zero leakage** —
audit every input for `season <= N` and confirm summer-hire/continuity overrides are off.
A leak inflates accuracy and is worse than a lower honest score.

**DoD:** `python backtest.py --target 2024-25` outputs MAE/RMSE on wins, seed accuracy,
playoff-round hit rate, champion top-k, and champion Brier score
(`SEASON_REFACTOR.md` §6b), compared against a naive baseline, for at least the 2023-24 and
2024-25 targets; results persisted and reproducible.

---

## Phase 4 — Formula work (placeholder, out of scope for this plan)

**Goal (future):** revisit the rating formulas themselves — e.g. reconcile the two base-PR
philosophies (availability-penalized vs pure-rate), tune the coach/playstyle/continuity
multipliers, and calibrate champion probabilities — **using the Phase 3 backtest as the
objective function**. No spec here; scope it once backtesting gives you a baseline to beat.

**DoD:** deferred.

---

## Sequencing rationale (one line each)

- **P0 before P1:** don't migrate schema for scripts you're about to delete.
- **P1 before P2:** you can't parameterize a season that's still encoded in a table name.
- **P2 before P3:** backtesting *is* running the parameterized pipeline with the future hidden.
- **P3 before P4:** you can't tune formulas without a metric that says whether a change helped.
