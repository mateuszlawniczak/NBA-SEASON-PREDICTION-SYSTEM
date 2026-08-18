# EYEonPAPER — The NBA Predictive Engine

### Putting the eye-test on paper

Most predictive models read the box score and stop there — so they miss what every fan sees 
while watching the game. Take Wembanyama: he averages three blocks, but that understates him,
because his presence scares players out of shots they never take. A box-score model misses 
that entirely. 

EYEonPAPER exists to close that gap — to quantify what the eye sees but the 
stat sheet doesn't, and simulate a season more accurately than the market by pricing in the 
effects other models ignore.

---

## How it works — the engineering

The engine runs **1,000 Monte Carlo simulations** of a full NBA season and playoffs. The
architecture is built to avoid the traps that break most simulations:

**The daily injury roll.** Before every game, each player's injury probability is rolled
from their real durability history — so bench depth becomes a survival necessity, not a
luxury. This defeats the "Iron Man" trap of assuming starters play all 82 games.

**"Next man up" positional backfill.** When a player goes down, the engine promotes the
highest-rated reserve *at that position* — a binomial positional backfill. If a team has no
backup center, it forces a small-ball penalty that mathematically reflects the drop-off in
rebounding and rim protection.

**9-man vs 8-man rotation logic.** The regular season uses a 9-man rotation for the depth an
82-game grind demands. The playoffs squeeze the rotation to 8 and weight the top three stars
by 1.15x — which is why deep, star-less teams can earn the #1 seed and still get upset in the
second round. This defeats the "rotation blindness" of models that use a flat 12-man average.

**Bradley-Terry win probability.** Every game is decided by:

```
home_term   = HOME_ODDS × F × (Rating_home ^ k)
P(Home Win) = home_term / (home_term + Rating_away ^ k)
```

Home advantage and fatigue are odds multipliers **outside** the exponent, so `k` can be
swept without silently rescaling either. Locked-in defaults (`run_monte_carlo.py`):
`k = 2.25`, `HOME_ODDS = 1.38`. On 13.5% of games the away team is fatigued and
`F = 1.0416667` (else `F = 1`). A continuity-decay multiplier still applies over the first
20 regular-season games to model new-roster gelling.

---

## Results

Backtest: 8 seasons (2018-19 → 2025-26), scored against a naive previous-season baseline,
with a train/test split (train 2019-20 → 2023-24; holdout 2024-25 and 2025-26). 2018-19 is
included in the pooled row below and excluded from train and test.

**Where the engine beats the baseline**

| Metric | Pooled (8 seasons) | Held-out test |
|--------|--------------------|---------------|
| Mean absolute error, team wins (↓) | **7.94** vs 8.51 | **8.80** vs 9.77 |
| Mean absolute error, win % (↓) | **9.91** vs 10.77 | **10.74** vs 11.91 |
| Win-order correlation (↑) | **0.587** vs 0.552 | **0.605** vs 0.540 |

**Where the baseline still wins**

| Metric | Engine | Baseline |
|--------|--------|----------|
| Exact seed accuracy (↑) | 13.3% | **16.3%** |
| Champion Brier score (↓) | 0.0385 | **0.0317** |
| Champion top-4 hit rate (↑) | 25% | **50%** |

The engine improves season win-total prediction out-of-sample. It does not yet beat the
baseline on seeding or champion prediction. Results are reproducible: every logged run is
tied to a git commit in the `runs` and `run_scores` tables.

---

## The eye-test effect library

This is the heart of the project: a growing library of modifiers that encode on-court
realities the box score can't. Currently modeled:

- **Defensive gravity (the "Wembanyama effect").** Credits rim-deterrence impact that a
  player's own block totals understate.
- **Named player archetypes** — effects modeled on specific players whose game breaks the
  standard curve (the "Shaq," "Klay," and other archetypes), applied to players who fit the
  same mold.
- **Pedigree trajectory (the "generational leap").** A 1.2x leap for high-pedigree young
  players (ROY winners/finalists) in their first five years, so the math can foresee
  non-linear rises like Wembanyama and Chet Holmgren instead of assuming a flat 5–8% curve.

It also standardizes the game's most *subjective* debates into float-value modifiers — the
Qualitative-to-Quantitative (Q2Q) layer:

- **Coaching multiplier** — tactical efficiency and roster maximization as a modifier on raw
  Player Ratings.
- **Playoff riser/choker index** — the efficiency some players lose (or the ~1.15x surge
  proven performers gain) when the lights get bright.
- **Clutch engine** — a late-game volatility factor tied to a team's performance in the final
  five minutes of close games.

---

## Live dashboard

A **Streamlit** app presents the current simulation results — projected standings, seeds,
playoff odds, and championship probabilities — in an interactive interface.

---

## Roadmap

Two major directions are in active development:

**Draft & talent projection.** In the second-apron era, contenders can no longer stack three
max-salary stars — title rosters increasingly depend on underpaid stars and cheap
rookie-scale talent (the Chet Holmgren / Dylan Harper type of contributor). That cheap-labor
tier is exactly the part of a roster the market misprices. Projecting draft talent better
therefore projects *contention* better — so a stronger draft model directly strengthens the
whole simulation.

**The what-if engine.** An interactive layer letting users reshape a roster — propose trades,
add injuries, sign free agents, or cut players — and watch the entire season simulation
re-run against the new reality. This turns the engine from a static forecast into a tool you
can experiment with.

---

## Tech stack

- **Language:** Python (NumPy, Pandas)
- **Database:** SQLite
- **Interface:** Streamlit
- **Math:** Bradley-Terry modeling, binomial distribution for injury logic, Monte Carlo
  simulation

---

## Version history

**v1 (May 2026)** — the Monte Carlo engine: 1,000 simulations, eye-test effect library,
injury logic, 9-man/8-man rotations. No validation.

**v2 (August 2026)** — validation and tuning: 8-season backtest vs baseline with a
train/test split, Bradley-Terry exponent sweep with locked-in defaults (`k = 2.25`,
home odds `1.38`), data-integrity healing for 2017–20, leakage guards, run logging tied
to git commits, and a Streamlit dashboard.

---

## Author

**Mateusz Lawniczak** — Aspiring Sports Data Analyst
[LinkedIn](https://www.linkedin.com/in/mateuszlawniczak/)
