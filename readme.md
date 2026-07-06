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
P(Home Win) = (Rating_home × 1.04) / (Rating_home × 1.04 + Rating_away × Fatigue)
```

with a constant 1.04 home-court multiplier, a 13.5% chance of a -4% back-to-back fatigue
penalty, and a continuity-decay multiplier over the first 20 games to model new-roster
gelling.

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

## Author

**Mateusz Lawniczak** — Aspiring Sports Data Analyst
[LinkedIn](https://www.linkedin.com/in/mateuszlawniczak/)
