# EYEonPAPER — Formula Experiments Log

> The lab notebook for Phase 4. **Append-only** — never delete or overwrite past entries;
> this is history. Every formula change gets one entry: what you changed, what the score
> did, and whether you kept it. After ~20 of these you have a map of what helps, what hurts,
> and what does nothing — which is worth as much as the model itself.

---

## The rules (read before your first experiment)

1. **Change ONE thing at a time.** If you change two factors and the score moves, you don't
   know which one did it. One change → one backtest → one entry. (Testing two factors
   *together* on purpose is allowed — but label it "A+B combined" so you know that entry
   can't isolate either.)

2. **Log BEFORE moving on.** Write the entry the moment you have the score, before starting
   the next experiment. You will not remember "was 8.71 the continuity change or the fatigue
   change" three experiments later. No new experiment until the last one is logged.

3. **The backtest score is the judge — but the log keeps the ideas.** If a change hurts the
   score, **revert the code** (git checkout the file) but **keep the entry** with a note if
   you suspect it could work combined with something else. Later, when you touch that other
   factor, check this log and test the combination deliberately. Revert the code, keep the
   note — don't keep a hurting change hoping it helps someday.

4. **Git pairs with this log.** Before an experiment you're on a clean commit. Make the
   change, run the backtest, log it here. If REVERT → `git checkout <file>` (instant, clean).
   If KEEP → commit with a message matching the entry. The log says *why* and *what the score
   did*; git holds the actual code states.

5. **What "the score" means:** run `compute_engine_scores.py`, use the **pooled, full-data**
   row (2019-20 → 2024-25; 2018-19 excluded as thin data). Record the metrics that moved.

---

## Entry template (copy this for each experiment)

```
## [DATE] — [one-line description of the change]
Hypothesis: [why you think this will help — what you're betting on]
Changed: [exact change — constant/formula, old → new, file:line]
Backtest (pooled, no 2018-19):
  MAE wins:      [before] → [after]   ([better/worse/same])
  Seed ±1:       [before] → [after]   (...)
  Champ top-1:   [before] → [after]   (...)
  Champ top-4:   [before] → [after]   (...)
  Brier:         [before] → [after]   (...)
Verdict: [KEEP / REVERT] — [one line: why, and any "revisit combined with X" note]
```

Only record metrics relevant to the change (e.g. a continuity tweak → focus on champion/seed
metrics), but always record MAE wins as the overall anchor.

---

## Experiment 0 — Baseline (the starting line)

This is not a change — it's where you start. Every future entry compares against this.

```
## 2026-08-03 — BASELINE (no changes; current formula as of end of Phase 3)
Engine vs naive baseline, pooled, full-data seasons (2019-20 → 2024-25):
  MAE wins:      engine 8.68  | baseline 8.82   → engine barely wins
  Seed exact:    engine 11.7% | baseline 13.9%  → baseline wins
  Seed ±1:       engine 28.3% | baseline 36.1%  → baseline wins
  Playoff berth: engine 70.6% | baseline 70.0%  → ~tie
  Champ top-1:   engine 0%    | baseline 16.7%  → baseline wins (biggest gap)
  Champ top-4:   engine 33.3% | baseline 50.0%  → baseline wins
  Brier champ:   engine 0.0321| baseline 0.0318 → ~tie
Notes:
  - Engine only clearly beats baseline on MAE wins, and only barely.
  - Biggest weakness: champion prediction (0% top-1). Priority target.
  - Seed gap may be a metric-reading issue (modal seed from a distribution vs sharp
    "same as last year") — check the metric before touching the formula.
  - Best individual result: 2022-23 MAE, −2.25 vs actual.
Goal for Phase 4: pull MAE clearly below 8.82, and get champion top-4 above the baseline's 50%.
```

---

## Experiments

<!-- Add each new experiment below, newest at the bottom. Copy the template above. -->

## Noise floor (measured runs 12-15, k=2.25, seeds 20260514/111/222/333)

Max-min spread across four identical-formula runs. A change smaller than its
threshold is dice, not signal:

  MAE wins              0.029
  MAE win %             0.034
  Seed exact %          1.4
  Seed ±1 %             2.4
  Playoff berth %       1.0
  Brier                 0.0004
  Champion top-1/top-4  move only in 14.3% steps (7 seasons)
  pooled predicted sd   0.037

k must be re-swept after any change that improves ranking quality, because the
optimal spread is r x actual spread and r is currently 0.589.

## Run 1 — 2026-08-16 — d358a2a
original formula, pre-tuning baseline

POOLED_NO_1819 (vs baseline):
  MAE wins              8.57   baseline 8.65   engine
  MAE win % (scaled)   10.71   baseline 10.99   engine
  Seed exact %          11.0   baseline 15.2   baseline
  Seed ±1 %             30.0   baseline 36.2   baseline
  Playoff berth %       69.5   baseline 69.5   tie
  Champion top-1 %       0.0   baseline 14.3   baseline
  Champion top-4 %      28.6   baseline 42.9   baseline
  Brier (champion)    0.0326   baseline 0.0318   baseline

## Run 2 — 2026-08-16 — eb8588c
determinism check, no formula change

POOLED_NO_1819 (vs baseline):
  MAE wins              8.57   (prev run 8.57, +0.00)   baseline 8.65   engine
  MAE win % (scaled)   10.71   (prev run 10.71, +0.00)   baseline 10.99   engine
  Seed exact %          10.5   (prev run 11.0, -0.5)   baseline 15.2   baseline
  Seed ±1 %             29.5   (prev run 30.0, -0.5)   baseline 36.2   baseline
  Playoff berth %       69.5   (prev run 69.5, 0.0)   baseline 69.5   tie
  Champion top-1 %       0.0   (prev run 0.0, 0.0)   baseline 14.3   baseline
  Champion top-4 %      28.6   (prev run 28.6, 0.0)   baseline 42.9   baseline
  Brier (champion)    0.0324   (prev run 0.0326, -0.0001)   baseline 0.0318   baseline

## Run 3 — 2026-08-16 — eb8588c
UP-only continuity top-2; determinism run 1

POOLED_NO_1819 (vs baseline):
  MAE wins              8.56   (prev run 8.57, -0.00)   baseline 8.65   engine
  MAE win % (scaled)   10.71   (prev run 10.71, -0.01)   baseline 10.99   engine
  Seed exact %          10.5   (prev run 10.5, 0.0)   baseline 15.2   baseline
  Seed ±1 %             28.6   (prev run 29.5, -1.0)   baseline 36.2   baseline
  Playoff berth %       69.5   (prev run 69.5, 0.0)   baseline 69.5   tie
  Champion top-1 %       0.0   (prev run 0.0, 0.0)   baseline 14.3   baseline
  Champion top-4 %      28.6   (prev run 28.6, 0.0)   baseline 42.9   baseline
  Brier (champion)    0.0324   (prev run 0.0324, +0.0000)   baseline 0.0318   baseline

## Run 4 — 2026-08-16 — eb8588c
UP-only continuity top-2; determinism run 2

POOLED_NO_1819 (vs baseline):
  MAE wins              8.56   (prev run 8.56, 0.00)   baseline 8.65   engine
  MAE win % (scaled)   10.71   (prev run 10.71, 0.00)   baseline 10.99   engine
  Seed exact %          10.5   (prev run 10.5, 0.0)   baseline 15.2   baseline
  Seed ±1 %             28.6   (prev run 28.6, 0.0)   baseline 36.2   baseline
  Playoff berth %       69.5   (prev run 69.5, 0.0)   baseline 69.5   tie
  Champion top-1 %       0.0   (prev run 0.0, 0.0)   baseline 14.3   baseline
  Champion top-4 %      28.6   (prev run 28.6, 0.0)   baseline 42.9   baseline
  Brier (champion)    0.0324   (prev run 0.0324, 0.0000)   baseline 0.0318   baseline

## Run 5 — 2026-08-17 — 7eb8942
BT refactor, defaults = old behaviour, regression check

POOLED_NO_1819 (vs baseline):
  MAE wins              8.56   (prev run 8.56, 0.00)   baseline 8.65   engine
  MAE win % (scaled)   10.71   (prev run 10.71, 0.00)   baseline 10.99   engine
  Seed exact %          10.5   (prev run 10.5, 0.0)   baseline 15.2   baseline
  Seed ±1 %             28.6   (prev run 28.6, 0.0)   baseline 36.2   baseline
  Playoff berth %       69.5   (prev run 69.5, 0.0)   baseline 69.5   tie
  Champion top-1 %       0.0   (prev run 0.0, 0.0)   baseline 14.3   baseline
  Champion top-4 %      28.6   (prev run 28.6, 0.0)   baseline 42.9   baseline
  Brier (champion)    0.0324   (prev run 0.0324, 0.0000)   baseline 0.0318   baseline

## Run 6 — 2026-08-17 — 7eb8942
control: home advantage only, k=1 [home_odds=1.38]

POOLED_NO_1819 (vs baseline):
  MAE wins              8.59   (prev run 8.56, +0.02)   baseline 8.65   engine
  MAE win % (scaled)   10.73   (prev run 10.71, +0.03)   baseline 10.99   engine
  Seed exact %          13.3   (prev run 10.5, +2.9)   baseline 15.2   baseline
  Seed ±1 %             32.4   (prev run 28.6, +3.8)   baseline 36.2   baseline
  Playoff berth %       70.5   (prev run 69.5, +1.0)   baseline 69.5   engine
  Champion top-1 %       0.0   (prev run 0.0, 0.0)   baseline 14.3   baseline
  Champion top-4 %      28.6   (prev run 28.6, 0.0)   baseline 42.9   baseline
  Brier (champion)    0.0329   (prev run 0.0324, +0.0005)   baseline 0.0318   baseline

## Run 7 — 2026-08-17 — 7eb8942
BT exponent sweep [k=2.5, home_odds=1.38]

POOLED_NO_1819 (vs baseline):
  MAE wins              7.81   (prev run 8.59, -0.78)   baseline 8.65   engine
  MAE win % (scaled)    9.80   (prev run 10.73, -0.94)   baseline 10.99   engine
  Seed exact %          13.3   (prev run 13.3, 0.0)   baseline 15.2   baseline
  Seed ±1 %             31.4   (prev run 32.4, -1.0)   baseline 36.2   baseline
  Playoff berth %       70.5   (prev run 70.5, 0.0)   baseline 69.5   engine
  Champion top-1 %       0.0   (prev run 0.0, 0.0)   baseline 14.3   baseline
  Champion top-4 %      28.6   (prev run 28.6, 0.0)   baseline 42.9   baseline
  Brier (champion)    0.0382   (prev run 0.0329, +0.0053)   baseline 0.0318   baseline

## Run 8 — 2026-08-17 — 7eb8942
BT exponent sweep [k=3.0, home_odds=1.38]

POOLED_NO_1819 (vs baseline):
  MAE wins              7.89   (prev run 7.81, +0.08)   baseline 8.65   engine
  MAE win % (scaled)    9.92   (prev run 9.80, +0.12)   baseline 10.99   engine
  Seed exact %          12.4   (prev run 13.3, -1.0)   baseline 15.2   baseline
  Seed ±1 %             32.9   (prev run 31.4, +1.4)   baseline 36.2   baseline
  Playoff berth %       71.0   (prev run 70.5, +0.5)   baseline 69.5   engine
  Champion top-1 %       0.0   (prev run 0.0, 0.0)   baseline 14.3   baseline
  Champion top-4 %      28.6   (prev run 28.6, 0.0)   baseline 42.9   baseline
  Brier (champion)    0.0396   (prev run 0.0382, +0.0014)   baseline 0.0318   baseline

## Run 9 — 2026-08-17 — 7eb8942
BT exponent sweep [k=3.5, home_odds=1.38]

POOLED_NO_1819 (vs baseline):
  MAE wins              8.01   (prev run 7.89, +0.12)   baseline 8.65   engine
  MAE win % (scaled)   10.09   (prev run 9.92, +0.18)   baseline 10.99   engine
  Seed exact %          12.4   (prev run 12.4, 0.0)   baseline 15.2   baseline
  Seed ±1 %             32.9   (prev run 32.9, 0.0)   baseline 36.2   baseline
  Playoff berth %       71.9   (prev run 71.0, +1.0)   baseline 69.5   engine
  Champion top-1 %       0.0   (prev run 0.0, 0.0)   baseline 14.3   baseline
  Champion top-4 %      28.6   (prev run 28.6, 0.0)   baseline 42.9   baseline
  Brier (champion)    0.0414   (prev run 0.0396, +0.0017)   baseline 0.0318   baseline

## Run 10 — 2026-08-17 — 7eb8942
BT exponent sweep [k=4.0, home_odds=1.38]

POOLED_NO_1819 (vs baseline):
  MAE wins              8.21   (prev run 8.01, +0.20)   baseline 8.65   engine
  MAE win % (scaled)   10.37   (prev run 10.09, +0.28)   baseline 10.99   engine
  Seed exact %          11.0   (prev run 12.4, -1.4)   baseline 15.2   baseline
  Seed ±1 %             31.4   (prev run 32.9, -1.4)   baseline 36.2   baseline
  Playoff berth %       71.4   (prev run 71.9, -0.5)   baseline 69.5   engine
  Champion top-1 %       0.0   (prev run 0.0, 0.0)   baseline 14.3   baseline
  Champion top-4 %      28.6   (prev run 28.6, 0.0)   baseline 42.9   baseline
  Brier (champion)    0.0429   (prev run 0.0414, +0.0015)   baseline 0.0318   baseline

## Run 11 — 2026-08-17 — 7eb8942
BT exponent sweep [k=2.0, home_odds=1.38]

POOLED_NO_1819 (vs baseline):
  MAE wins              7.89   (prev run 8.21, -0.31)   baseline 8.65   engine
  MAE win % (scaled)    9.89   (prev run 10.37, -0.48)   baseline 10.99   engine
  Seed exact %          12.9   (prev run 11.0, +1.9)   baseline 15.2   baseline
  Seed ±1 %             33.3   (prev run 31.4, +1.9)   baseline 36.2   baseline
  Playoff berth %       70.5   (prev run 71.4, -1.0)   baseline 69.5   engine
  Champion top-1 %       0.0   (prev run 0.0, 0.0)   baseline 14.3   baseline
  Champion top-4 %      28.6   (prev run 28.6, 0.0)   baseline 42.9   baseline
  Brier (champion)    0.0359   (prev run 0.0429, -0.0069)   baseline 0.0318   baseline

## Run 12 — 2026-08-17 — 7eb8942
BT exponent sweep [k=2.25, home_odds=1.38]

POOLED_NO_1819 (vs baseline):
  MAE wins              7.83   (prev run 7.89, -0.06)   baseline 8.65   engine
  MAE win % (scaled)    9.81   (prev run 9.89, -0.07)   baseline 10.99   engine
  Seed exact %          13.8   (prev run 12.9, +1.0)   baseline 15.2   baseline
  Seed ±1 %             31.4   (prev run 33.3, -1.9)   baseline 36.2   baseline
  Playoff berth %       70.5   (prev run 70.5, 0.0)   baseline 69.5   engine
  Champion top-1 %       0.0   (prev run 0.0, 0.0)   baseline 14.3   baseline
  Champion top-4 %      28.6   (prev run 28.6, 0.0)   baseline 42.9   baseline
  Brier (champion)    0.0372   (prev run 0.0359, +0.0013)   baseline 0.0318   baseline

## Run 13 — 2026-08-17 — 7eb8942
noise floor [seed=111, k=2.25, home_odds=1.38]

POOLED_NO_1819 (vs baseline):
  MAE wins              7.85   (prev run 7.83, +0.02)   baseline 8.65   engine
  MAE win % (scaled)    9.84   (prev run 9.81, +0.02)   baseline 10.99   engine
  Seed exact %          12.4   (prev run 13.8, -1.4)   baseline 15.2   baseline
  Seed ±1 %             33.8   (prev run 31.4, +2.4)   baseline 36.2   baseline
  Playoff berth %       70.5   (prev run 70.5, 0.0)   baseline 69.5   engine
  Champion top-1 %       0.0   (prev run 0.0, 0.0)   baseline 14.3   baseline
  Champion top-4 %      28.6   (prev run 28.6, 0.0)   baseline 42.9   baseline
  Brier (champion)    0.0368   (prev run 0.0372, -0.0004)   baseline 0.0318   baseline

## Run 14 — 2026-08-17 — 7eb8942
noise floor [seed=222, k=2.25, home_odds=1.38]

POOLED_NO_1819 (vs baseline):
  MAE wins              7.82   (prev run 7.85, -0.03)   baseline 8.65   engine
  MAE win % (scaled)    9.80   (prev run 9.84, -0.03)   baseline 10.99   engine
  Seed exact %          13.3   (prev run 12.4, +1.0)   baseline 15.2   baseline
  Seed ±1 %             31.9   (prev run 33.8, -1.9)   baseline 36.2   baseline
  Playoff berth %       71.4   (prev run 70.5, +1.0)   baseline 69.5   engine
  Champion top-1 %       0.0   (prev run 0.0, 0.0)   baseline 14.3   baseline
  Champion top-4 %      28.6   (prev run 28.6, 0.0)   baseline 42.9   baseline
  Brier (champion)    0.0371   (prev run 0.0368, +0.0003)   baseline 0.0318   baseline

## Run 15 — 2026-08-17 — 7eb8942
noise floor [seed=333, k=2.25, home_odds=1.38]

POOLED_NO_1819 (vs baseline):
  MAE wins              7.83   (prev run 7.82, +0.01)   baseline 8.65   engine
  MAE win % (scaled)    9.81   (prev run 9.80, +0.01)   baseline 10.99   engine
  Seed exact %          12.4   (prev run 13.3, -1.0)   baseline 15.2   baseline
  Seed ±1 %             33.3   (prev run 31.9, +1.4)   baseline 36.2   baseline
  Playoff berth %       70.5   (prev run 71.4, -1.0)   baseline 69.5   engine
  Champion top-1 %       0.0   (prev run 0.0, 0.0)   baseline 14.3   baseline
  Champion top-4 %      28.6   (prev run 28.6, 0.0)   baseline 42.9   baseline
  Brier (champion)    0.0371   (prev run 0.0371, -0.0000)   baseline 0.0318   baseline

## Run 16 — 2026-08-17 — cc42273
locked in k=2.25, home odds 1.38 as defaults

POOLED_NO_1819 (vs baseline):
  MAE wins              7.83   (prev run 7.83, +0.00)   baseline 8.65   engine
  MAE win % (scaled)    9.81   (prev run 9.81, +0.00)   baseline 10.99   engine
  Seed exact %          13.8   (prev run 12.4, +1.4)   baseline 15.2   baseline
  Seed ±1 %             31.4   (prev run 33.3, -1.9)   baseline 36.2   baseline
  Playoff berth %       70.5   (prev run 70.5, 0.0)   baseline 69.5   engine
  Champion top-1 %       0.0   (prev run 0.0, 0.0)   baseline 14.3   baseline
  Champion top-4 %      28.6   (prev run 28.6, 0.0)   baseline 42.9   baseline
  Brier (champion)    0.0372   (prev run 0.0371, +0.0001)   baseline 0.0318   baseline
