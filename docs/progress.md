# EYEonPAPER — Progress & Decisions

> **Start here when you come back.** This file is the "you are here" pin: what to do next,
> the current score, what's decided, and where to find everything else. It gets overwritten
> as state changes — it holds the *present*, not history (history lives in git and
> `EXPERIMENTS.md`).

Last updated: 2026-08-03

---

## What to do next (read this first)

You are in **Phase 4 — formula tuning.** Everything before it is done: the engine is
multi-season, leakage-free, and scored against a baseline. The job now is to make the
formula *more accurate* — and you can finally measure whether any change helps, because the
backtest scorecard exists.

**Immediate next actions:**
1. Build `FORMULA.md` (the constants cheat-sheet) and `EXPERIMENTS.md` (the change log) — the
   two tracking files for this phase.
2. Then start the tuning loop: change one thing → re-run the backtest → log the score effect
   → keep or revert.
3. First targets to investigate (see "Current focus" below): the champion prediction (0%
   top-1) and the seed accuracy gap.

---

## The number that matters

Current backtest, engine vs naive baseline ("predict last season's result"), pooled over the
full-data seasons (2019-20 → 2024-25, 2018-19 excluded as thin-data):

| Metric | Engine | Baseline | Who wins |
|---|---|---|---|
| MAE wins (↓) | **8.68** | 8.82 | engine (barely) |
| Seed ±1 (↑) | 28.3% | 36.1% | baseline |
| Champion top-1 (↑) | **0%** | 16.7% | baseline |
| Champion top-4 (↑) | 33.3% | 50.0% | baseline |
| Brier champion (↓) | 0.0321 | 0.0318 | ~tie |

**Read:** the engine *barely* beats the baseline on win totals and loses on seeds and
champion. That's the honest starting line — the target is to pull these numbers clearly
past the baseline. Best single result so far: 2022-23 win MAE, −2.25 vs actual.

Scores are produced by `compute_baseline_scores.py` and `compute_engine_scores.py`
(tables `baseline_scores`, `engine_scores`).

---

## Current focus / open questions

- **Champion prediction is the biggest weakness (0% top-1).** Hypothesis: the model
  over-trusts regular-season strength and under-weights playoff variance / the eye-test
  effects (clutch, riser/choker, 8-man rotation) that are *supposed* to catch upsets. Test
  whether those effects actually move the champion metric — if not, they may be miscalibrated.
- **Seed accuracy below baseline.** Open question: is this a *metric-reading* problem (taking
  the single most-likely seed from a probability distribution is fuzzy, while "same as last
  year" is sharp) or a *formula* problem? Check the metric first — it may be a cheap fix.
- **Run ablation on the eye-test effects** — turn each effect off, re-run the backtest, see
  which actually improve accuracy vs which are dead weight or harmful.

---

## Decision log (settled — don't re-litigate)

- **Keep the engine, don't rebuild.** A working engine beats a clean blank page.
- **Base Player Rating: `calculate_player_pr.py`** (no games-played penalty; injuries handled
  in the Monte Carlo). `calculate_base_pr.py` deleted.
- **Riser/choker: `build_composite_clutch_index.py`** (superseded `calculate_playoff_riser_choker.py`, deleted).
- **Simulator: `run_monte_carlo.py`** (superseded `monte_carlo_season_25_26.py`, deleted).
- **Player effects: `init_yearly_player_effects.py`** (season-keyed).
- **Continuity is data-derived**, not hardcoded team lists: top-2-kept gate (by prior-season
  PR; a missing player is assumed kept unless he appears on another team) → then roster
  overlap ≥70% HIGH / 50–70% DEFAULT / <50% LOW. This deliberately changed the 2025-26
  baseline (the old hand-typed lists had teams like DET/SAC backwards).
- **Delete dead code, don't archive** — git history is the archive.

---

## Deferred cleanup tickets (real, but not urgent — don't let them block formula work)

- **Rename the misnamed `_25_26` scripts** (`build_projected_team_pr_25_26.py`,
  `build_team_playoff_pr_25_26.py`, `calculate_rookie_projected_pr_25_26.py`,
  `fetch_team_coaches_25_26.py`). They ARE season-parameterized and work for all years — only
  the names lie. Verify-then-rename; renaming means updating imports, so do it carefully as
  its own step. *(Planned next with Sonnet.)*
- **Data-provenance split.** Seasons 2017-18 → 2019-20 were loaded via `heal_pass/` + backfills;
  the standard `fetch_*` scripts only cover 2020-21 → 2025-26. So a naive data refresh would
  only touch the newer seasons. Real fix = season-parameterize the fetch scripts. Deferred to
  post-formula cleanup (dormant during tuning — you won't re-fetch while tuning).
- **`fetch_player_starting_teams_25_26.py` deleted** (dead code, verified). Deletion staged in
  git, commit pending.

---

## Document map (which file answers which question)

- **README.md** — what the project is and why (the pitch).
- **docs/progress.md** (this file) — where I am right now + next action + score + decisions.
- **docs/FORMULA.md** — the current formula constants/weights (what I can turn). *(to build)*
- **docs/EXPERIMENTS.md** — log of every formula change → its score effect → kept/reverted. *(to build)*
- **docs/ARCHITECTURE.md** — how the code/data is wired (the technical map).
- **docs/ROADMAP.md** — the full phased plan and where each phase stands.
- **docs/CLEANUP_PLAN.md**, **docs/SEASON_REFACTOR.md** — specs for completed work (historical).