# EYEonPAPER — Progress & Decisions

> **Start here when you come back.** This file is the "you are here" pin: what to do next,
> the current score, what's decided, and where to find everything else. It gets overwritten
> as state changes — it holds the *present*, not history (history lives in git and
> `EXPERIMENTS.md`).

Last updated: 2026-08-18

---

## What to do next (read this first)

**v2 is in place.** The 8-season backtest (2018-19 → 2025-26) is complete, Bradley-Terry
defaults are locked at `k = 2.25` and home odds `1.38`, and every logged run is tied to a
git commit in `runs` / `run_scores`. The next phase is **v3**.

v2 already improved win-total prediction out-of-sample. It does **not** yet beat the
baseline on seeding or champion prediction — that is the work v3 has to do.

**Immediate next actions:**
1. Start v3 from the locked v2 defaults. Do not re-open `k` / home odds unless ranking
   quality moves enough to justify a re-sweep (see the noise-floor note in
   `docs/EXPERIMENTS.md`).
2. Attack seed accuracy and champion metrics (Brier, top-4). Those are where the baseline
   still wins.
3. Keep the existing loop: one change → backtest → log in `EXPERIMENTS.md` / `run_scores`
   → keep or revert.

---

## The number that matters

Current backtest, engine vs naive previous-season baseline. Pooled = all 8 seasons
(2018-19 → 2025-26). Test = holdout 2024-25 and 2025-26.

**Engine beats baseline**

| Metric | Pooled | Held-out test |
|---|---|---|
| MAE wins (↓) | **7.94** vs 8.51 | **8.80** vs 9.77 |
| MAE win % (↓) | **9.91** vs 10.77 | **10.74** vs 11.91 |
| Win-order r (↑) | **0.587** vs 0.552 | **0.605** vs 0.540 |

**Baseline still wins**

| Metric | Engine | Baseline |
|---|---|---|
| Exact seed % (↑) | 13.3% | **16.3%** |
| Champion Brier (↓) | 0.0385 | **0.0317** |
| Champion top-4 (↑) | 25% | **50%** |

Locked-in MC defaults: `k = 2.25`, home odds `1.38` (`run_monte_carlo.py`).

Scores are produced by `compute_baseline_scores.py` and `compute_engine_scores.py`
(tables `baseline_scores`, `engine_scores`; history in `runs` / `run_scores`).

---

## Current focus / open questions

- **Seeding and champion prediction are the remaining gaps.** The engine is ahead on
  win totals out-of-sample and behind on exact seed, champion Brier, and champion top-4.
- **v3 is the next phase** — formula work against those three losses, with the v2
  backtest harness and experiment log already in place.
- Run ablation on the eye-test effects remains useful: turn each effect off, re-run the
  backtest, see which actually improve accuracy vs which are dead weight or harmful.

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
- **Bradley-Terry defaults locked:** `k = 2.25`, home odds `1.38` (fatigue is an odds
  multiplier outside the exponent). Experiment history is append-only in `runs` /
  `run_scores` and `docs/EXPERIMENTS.md`.

---

## Deferred cleanup tickets (real, but not urgent — don't let them block formula work)

- **`_25_26` script rename — DONE.** `build_projected_team_pr_25_26.py`,
  `build_team_playoff_pr_25_26.py`, `calculate_rookie_projected_pr_25_26.py`, and
  `fetch_team_coaches_25_26.py` were renamed (suffix dropped) via `git mv`; `pipeline.py`
  imports updated; verified byte-identical `simulation_results` hash before/after for
  2025-26. `continuity_review_2025_26.py` intentionally kept its name (separate,
  still-open liveness question).
- **Fetch-layer overhaul: INVESTIGATED and DEFERRED.** Findings: (a) NBA API silently
  dropped the `gs` and `position` fields — current fetch scripts return NULL for these on
  new fetches, though existing stored data is intact and correct; (b) all formula-relevant
  columns (`mpg, gp, ast, ts_pct, fg3_pct, pts, total_minutes, deflections, off_reb,
  def_reb`) verified bit-for-bit correct across seasons vs a clean re-fetch — the data
  foundation is solid; (c) a true unified-fetch rebuild spans player + team + league +
  coach fetch scripts (13 scripts total, several feeding predictions), so it's a
  multi-session task that risks the backtest baseline — deferred until a genuinely new
  season needs fetching. `fetch_player_stats.py` (Script 1) exists as a starting point if
  resumed.
- **`fetch_player_starting_teams_25_26.py` deleted** (dead code, verified). Multi-season
  roster builds use `fetch_player_starting_teams.py`.

---

## Document map (which file answers which question)

- **README.md** — what the project is and why (the pitch), plus current results.
- **docs/progress.md** (this file) — where I am right now + next action + score + decisions.
- **docs/FORMULA.md** — the current formula constants/weights (what I can turn).
- **docs/EXPERIMENTS.md** — log of every formula change → its score effect → kept/reverted.
- **docs/ARCHITECTURE.md** — how the code/data is wired (the technical map).
- **docs/ROADMAP.md** — the full phased plan and where each phase stands.
- **docs/CLEANUP_PLAN.md**, **docs/SEASON_REFACTOR.md** — specs for completed work (historical).
