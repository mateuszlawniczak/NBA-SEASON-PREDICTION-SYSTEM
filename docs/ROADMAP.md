# EYEonPAPER — Phased Roadmap

> Updated to reflect **actual** project status (2026-08-18). See `docs/ARCHITECTURE.md`
> for the live technical map and `docs/progress.md` for the present-state pin.

Each phase was designed to leave the repo in a **working state**. Phases 0–3 are complete;
**Phase 4 (formula tuning) is the active work.**

**Legend:** DoD = Definition of Done · ✅ DONE · 🔄 CURRENT · ⬜ FUTURE

| Phase | Status | Summary |
|-------|--------|---------|
| 0 — Cleanup & safety net | ✅ DONE | Dead scripts/tables removed; `pipeline.py` orchestrator |
| 1 — Schema consolidation | ✅ DONE | Season-keyed tables; `_25_26` names migrated |
| 2 — Season parameterization | ✅ DONE | `--season` end-to-end; leakage guards; historical sims |
| 3 — Backtesting harness | ✅ DONE | Baseline + engine scoring across 8 seasons |
| 4 — Formula work | 🔄 CURRENT | Tune formulas to beat baseline convincingly |
| 5 — Stretch (post-formula) | ⬜ FUTURE | UI polish, market comparison |

---

## Phase 0 — Cleanup & safety net ✅ DONE

**Goal:** remove dead weight, capture dependencies, and put a thin orchestrator over the
live projection + simulation path so the run order stops living in your head.

**What was actually done:** Superseded scripts deleted (`calculate_base_pr.py`,
`monte_carlo_season_25_26.py`, `calculate_playoff_riser_choker.py`, `init_player_effects.py`,
and the dead `team_simulation_pr` writers). `pipeline.py` added as the orchestrator
(initially single-season order; later gained `--season` in Phase 2). `reset_database.py`
retargeted to live season-keyed tables. `docs/` audit set established.

**DoD (met):** Live path runs through `pipeline.py`; dead scripts and the
`team_simulation_pr` branch are gone; engine still produces a full 30-team simulation for
2025-26.

> **Note:** Original plan called for `requirements.txt` and moving scripts into `archive/`;
> dead scripts were **deleted** (git history is the archive) per `docs/PROGRESS.md`.
> `archive/` remains for one-off migration scripts only — off the live path.

---

## Phase 1 — Schema consolidation (season-keyed tables) ✅ DONE

**Goal:** make `season` a column, not a table-name suffix; consolidate projection and
simulation tables for multi-season storage.

**What was actually done:** `migrate_phase1a_schema.py` renamed year-suffixed tables to
generic season-keyed names (`simulation_results`, `team_projection`,
`team_playoff_projection`, `rookie_projection`, `team_coaches`, `player_starting_teams`).
Writers and readers updated to `WHERE season = ?`. `simulation_results` gained
`(team, season, run_id)` primary key.

**DoD (met):** No live table name contains `_25_26`; `pipeline.py --stage all` writes to
season-keyed tables; multiple seasons can coexist in the same table.

---

## Phase 2 — Season parameterization + de-leaking + historical generation ✅ DONE

**Goal:** `pipeline.py --season <target> --stage all` runs the full projection + simulation
for any target season, deriving `source_season = target − 1`.

**What was actually done:** `season_utils.py` centralizes season parsing; every pipeline
step accepts `(source_season, target_season)`. `leakage_guards.py` gates Finals-MVP,
pedigree boosts, and summer coach hires so historical runs use only pre-target knowledge.
Continuity moved from hardcoded team lists to data-derived rules (top-2 gate + roster
overlap) in `build_team_playoff_pr_25_26.py`. `_batch_historical.py` batch-generated
production simulations for **2018-19 → 2025-26** (`run_id='production'`).

**DoD (met):** `pipeline.py --season 2025-26` reproduces the production baseline;
`pipeline.py --season 2019-20` (and other historical targets) produces full projection +
sim without code edits. Leakage spot-checks pass in `_batch_historical.py`.

> **Deferred from original spec:** `--dry-run` and per-step row-count assertions were not
> added to `pipeline.py`; validation lives in batch helpers instead.

---

## Phase 3 — Backtesting harness + first accuracy report ✅ DONE

**Goal:** score engine predictions against actual outcomes with zero leakage — the
objective function for formula work.

**What was actually done:** Instead of a monolithic `backtest.py`, scoring split into:

- `compute_baseline_scores.py` — naive baseline (prior-season `team_stats` → wins, seeds,
  playoff berths; champion prob ∝ prior win_pct) → `baseline_scores`
- `compute_engine_scores.py` — engine output from `simulation_results`
  (`run_id='production'`) → `engine_scores`, with side-by-side comparison

**Metrics:** MAE wins, MAE win %, seed exact %, seed ±1 %, playoff berth %, champion top-1 %,
champion top-4 %, Brier (champion), win-order r, champion rank. Scored across **2018-19**
(partial) + **2019-20 → 2025-26** (8 seasons), with train (2019-20 → 2023-24) and holdout
test (2024-25, 2025-26) pooled sections.

**DoD (met):** Baseline and engine scores persisted and reproducible; pooled comparison
printed on every `compute_engine_scores.py` run. Logged runs are append-only in `runs` /
`run_scores`, each tied to a git commit.

**Headline result (POOLED, 8 seasons 2018-19 → 2025-26):**

| Metric | Baseline | Engine | Winner |
|--------|----------|--------|--------|
| MAE wins | 8.51 | **7.94** | engine |
| MAE win % | 10.77 | **9.91** | engine |
| Win-order r | 0.552 | **0.587** | engine |
| Exact seed % | **16.3** | 13.3 | baseline |
| Champion Brier | **0.0317** | 0.0385 | baseline |
| Champion top-4 % | **50.0** | 25.0 | baseline |

Held-out test (2024-25, 2025-26): MAE wins **8.80** vs 9.77; MAE win % **10.74** vs 11.91;
win-order r **0.605** vs 0.540.

The engine improves season win-total prediction out-of-sample. It does **not** yet beat
the baseline on seeding or champion prediction.

---

## Phase 4 — Formula work 🔄 CURRENT

**Goal:** improve projection and simulation formulas using the Phase 3 scoreboard as the
objective function — beat the naive baseline **convincingly**, not marginally.

**Current state:** Active tuning phase. Locked-in MC defaults: `k = 2.25`, home odds
`1.38`. Engine wins on pooled MAE wins (7.94 vs 8.51) and win-order r (0.587 vs 0.552),
including on held-out test seasons, but loses on exact seed (13.3% vs 16.3%), champion
Brier (0.0385 vs 0.0317), and champion top-4 (25% vs 50%).

**Sub-goals (in flight):**

1. **Beat the naive baseline clearly** — widen the MAE-wins gap; improve seed ±1 and
   playoff berth accuracy, not just tie or barely win one metric.
2. **Fix champion prediction** — address 0% champion top-1; improve Brier score and
   top-4 hit rate (likely PO multipliers, continuity, clutch weight, or sim variance).
3. **Ablation on eye-test effects** — toggle or reweight `player_special_effects`,
   pedigree boost, clutch index, coach/playstyle/continuity multipliers; measure each
   change via `compute_engine_scores.py`.
4. **Experiment logging** — ✅ done. Each logged run appends to `runs` / `run_scores`
   (git commit stored on `runs.git_commit`) and to `docs/EXPERIMENTS.md`. History is
   append-only.

**DoD:** Pooled engine scores beat baseline on MAE wins **and** at least two of: seed ±1,
playoff berth %, champion top-1, champion Brier — with no metric regressing by more than
a agreed tolerance. Champion top-1 must be **> 0%** on the 8-season window.

---

## Phase 5 — Stretch goals ⬜ FUTURE

**Goal:** polish and external validation after formula work stabilizes. Not started; do not
begin until Phase 4 DoD is met.

**Candidates:**

- **UI polish** — backtest scorecard tab in `app.py`; season selector beyond the fixed
  2025-26 interactive default; clearer champion/seed visualizations.
- **Market comparison (stretch)** — compare engine champion probabilities to betting
  markets or ELO-style benchmarks where historical odds exist.

**DoD:** deferred until Phase 4 completes.

---

## Deferred item — Fetch-layer overhaul (not scheduled, no phase assigned)

**Status:** INVESTIGATED and DEFERRED (2026-08-03/04). NBA API dropped the `gs`/`position`
fields upstream; all formula-relevant columns verified bit-for-bit correct vs a clean
re-fetch; a real fix spans 13 fetch scripts across player/team/league/coach layers, making
it a multi-session task that risks the backtest baseline. Deferred until a genuinely new
season needs fetching. `fetch_player_stats.py` (Script 1) exists as a starting point if
resumed. Full findings: `docs/progress.md`.

---

## Sequencing rationale (one line each)

- **P0 before P1:** don't migrate schema for scripts you're about to delete.
- **P1 before P2:** you can't parameterize a season that's still encoded in a table name.
- **P2 before P3:** backtesting *is* running the parameterized pipeline with the future hidden.
- **P3 before P4:** you can't tune formulas without a metric that says whether a change helped.
- **P4 before P5:** no UI/market work until the engine earns its numbers.

---

## Where to look next

| Need | Document / script |
|------|-------------------|
| Technical data flow | `docs/ARCHITECTURE.md` |
| Run a season | `py pipeline.py --season 2025-26` |
| Score predictions | `py compute_baseline_scores.py` then `py compute_engine_scores.py` |
| Batch historical sims | `_batch_historical.py` |
| Settled design decisions | `docs/PROGRESS.md` (decision log; phase status is this file) |
