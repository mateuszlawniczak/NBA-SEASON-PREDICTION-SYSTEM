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