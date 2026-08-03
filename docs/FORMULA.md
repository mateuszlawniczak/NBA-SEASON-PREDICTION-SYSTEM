# EYEonPAPER — Formula & Tuning Reference

> **Phase 4 tuning cheat-sheet.** Every value below was read from source code on
> **2026-08-03**. Each entry cites `file.py:line`. If a knob lives outside the files
> listed here, it is marked **NOT FOUND — verify manually**.

**Formula chain:** Base PR → progression → playoff experience → special effects →
ULTIMATE_PR → pedigree boost → playoff PR (+ clutch) → durability → team RS/PO PR →
Monte Carlo.

---

## Most likely tuning targets (known weaknesses: champion top-1, seed accuracy)

These knobs directly affect playoff team strength, clutch weighting, and simulation
variance — prioritize them when attacking 0% champion top-1 and weak seed metrics.

| Knob | Current | Location | Why it matters |
|------|---------|----------|----------------|
| Clutch `PLAYOFF_MULTIPLIER` map (+3 → 1.15 … −3 → 0.75) | see table §6 | `build_composite_clutch_index.py:49-57` | Scales every player's `playoff_pr` |
| Playoff PR formula (`base × youth × clutch`) | youth 1.10 / 1.00 | `calculate_ultimate_playoff_pr.py:102-112` | PO rating fed to sim + team PO draft |
| PO star boost (top 3 × 1.15) | `STAR_BOOST = 1.15`, top 3 | `build_team_playoff_pr_25_26.py:46-47,416-418` | Inflates team `base_8man_pr` |
| PO coach amplify | `COACH_AMPLIFY = 1.5` | `build_team_playoff_pr_25_26.py:47,521` | `amp_coach = 1 + (coach−1)×1.5` |
| Continuity multipliers + overlap gates | 1.05 / 1.00 / 0.95; 0.70 / 0.50 | `build_team_playoff_pr_25_26.py:49-55` | RS early-season + full PO multiplier |
| MC PO star boost (in-series) | `STAR_BOOST_PO = 1.15`, top 3 | `run_monte_carlo.py:49-50,289-295` | Playoff game win probability |
| MC home / fatigue | 1.04 / 0.96 @ 13.5% | `run_monte_carlo.py:45-47` | Regular-season standings |
| MC continuity decay window | games 1–20 only (RS) | `run_monte_carlo.py:52,365-366` | Early-season RS only |
| Special-effect point bonuses | +2 to +6 | `calculate_final_simulation_pr.py:21-30` | Top-end player separation |
| Pedigree ROY boosts | 1.20 / 1.10 per player | `leakage_guards.py:29-43` | Applied in `apply_pedigree_trajectory_boost.py` |

---

## 1. Base Player Rating — `calculate_player_pr.py`

**Formula (docstring + code):**

```
pts_score  = (ts_pct / ERA_AVG_TS) * pts
ast_score  = ast + ((ast / max(tov, 1.0)) * 2.5)
def_score  = ((stl + blk) * 3.0) + deflections
reb_score  = (dreb + (oreb * 3.0)) * 0.7
raw_impact = pts_score + ast_score + def_score + reb_score
base_pr    = round(raw_impact * ((mpg / MPG_REF) ** MPG_CURVE_EXP))
```

| Constant / knob | Current value | File:line | What it controls |
|-----------------|---------------|-----------|------------------|
| `ERA_AVG_TS` (TS baseline) | `0.58` | `calculate_player_pr.py:34` | Normalizes scoring efficiency vs league avg TS |
| `MPG_REF` (MPG divisor) | `30.0` | `calculate_player_pr.py:35` | Reference minutes in MPG curve |
| `MPG_CURVE_EXP` | `0.65` | `calculate_player_pr.py:36` | Fractional minutes exponent (bench boost) |
| Playmaking TOV floor | `max(tov, 1.0)` | `calculate_player_pr.py:163` | Avoids divide-by-zero in ast term |
| Playmaking ast weight | `2.5` | `calculate_player_pr.py:163` | Weight on ast/TOV ratio term |
| Defense (stl+blk) weight | `3.0` | `calculate_player_pr.py:164` | Multiplier on steals + blocks |
| Offensive reb weight | `3.0` | `calculate_player_pr.py:165` | OREB premium inside reb term |
| Reb term scale | `0.7` | `calculate_player_pr.py:165` | Scales total rebound contribution |
| Missing/invalid TS fallback | uses `ERA_AVG_TS` (0.58) | `calculate_player_pr.py:159-160` | When `ts_pct` null or ≤ 0 |
| Minimum GP guard | skip if `gp <= 0` | `calculate_player_pr.py:134-135` | Row excluded from output |
| Minimum minutes guard | skip if no positive minutes | `calculate_player_pr.py:142-143` | Sets `mpg = 0` if no minutes (still computes) |
| `TOP_N` (audit only) | `40` | `calculate_player_pr.py:38` | Console audit row count |
| `BENCH_MPG_MAX` (audit only) | `22.0` | `calculate_player_pr.py:39` | Bench audit MPG cutoff |
| `BENCH_TOP_N` (audit only) | `10` | `calculate_player_pr.py:40` | Bench audit row count |

---

## 2. Age progression — `apply_progression.py`

**Formula:** `projected_pr = round(base_pr × aging_multiplier(age))`

| Constant / knob | Current value | File:line | What it controls |
|-----------------|---------------|-----------|------------------|
| Age ≤ 21 multiplier | `1.12` | `apply_progression.py:31-32` | Youngest bucket |
| Age 22–24 multiplier | `1.06` | `apply_progression.py:33-34` | Pre-peak bucket |
| Age 25–28 multiplier | `1.00` | `apply_progression.py:35-36` | Peak plateau |
| Age 29–31 multiplier | `0.97` | `apply_progression.py:37-38` | Early decline |
| Age 32–34 multiplier | `0.92` | `apply_progression.py:39-40` | Mid decline |
| Age ≥ 35 multiplier | `0.85` | `apply_progression.py:41` | Late decline |
| `TOP_RISERS` / `TOP_FALLERS` (audit) | `15` / `15` | `apply_progression.py:22-23` | Console audit only |

---

## 3. Playoff experience — `apply_playoff_experience_pr.py`

**Formula:** `adjusted_exp_pr = round(projected_pr × multiplier_for(playoff_result))`

| Constant / knob | Current value | File:line | What it controls |
|-----------------|---------------|-----------|------------------|
| Champion | `1.15` | `apply_playoff_experience_pr.py:24` | Prior-season champion team players |
| Finals | `1.12` | `apply_playoff_experience_pr.py:25` | Lost Finals |
| Conf. Finals | `1.08` | `apply_playoff_experience_pr.py:26` | Lost Conference Finals |
| Conf. Semifinals | `1.04` | `apply_playoff_experience_pr.py:27` | Lost Conf Semis |
| 1st Round | `1.02` | `apply_playoff_experience_pr.py:28` | Lost 1st Round |
| Play-In Eliminated | `1.00` | `apply_playoff_experience_pr.py:29` | Play-in out |
| Missed Playoffs | `0.98` | `apply_playoff_experience_pr.py:30` | No playoffs / no team row |
| No team row fallback | `Missed Playoffs` mult (0.98) | `apply_playoff_experience_pr.py:47-48` | LEFT JOIN miss |

---

## 4. Special effects stack — `calculate_final_simulation_pr.py`

**Formula:** `final_pr = adjusted_exp_pr + sum(effect_boosts)` (additive, not multiplicative)

| Constant / knob | Current value | File:line | What it controls |
|-----------------|---------------|-----------|------------------|
| `mvp_potential` boost | `+6` | `calculate_final_simulation_pr.py:22` | Largest single effect |
| `alien_effect` boost | `+5` | `calculate_final_simulation_pr.py:23` | |
| `shaq_effect` boost | `+4` | `calculate_final_simulation_pr.py:24` | |
| `nash_effect` boost | `+4` | `calculate_final_simulation_pr.py:25` | |
| `efficiency_god` boost | `+3` | `calculate_final_simulation_pr.py:26` | |
| `klay_effect` boost | `+3` | `calculate_final_simulation_pr.py:27` | |
| `defense_effect` boost | `+2` | `calculate_final_simulation_pr.py:28` | |
| `board_effect` boost | `+2` | `calculate_final_simulation_pr.py:29` | |
| Effect trigger | column value `"Yes"` | `calculate_final_simulation_pr.py:43-44` | Binary flag check |

---

## 5. ULTIMATE_PR merge — `build_ultimate_pr.py`

| Constant / knob | Current value | File:line | What it controls |
|-----------------|---------------|-----------|------------------|
| Veteran PR source | `final_simulation_pr.final_pr` | `build_ultimate_pr.py:51-55` | Pass-through, no scaling |
| Rookie PR source | `rookie_projection.rookie_pr` | `build_ultimate_pr.py:57-64` | Pass-through, no scaling |
| Rookie `applied_effects` | `'None'` (literal) | `build_ultimate_pr.py:62` | Display only |

No numeric multipliers in this step.

---

## 6. Pedigree trajectory boost — `apply_pedigree_trajectory_boost.py` + `leakage_guards.py`

**Formula:** `ULTIMATE_PR.pr *= mult`; same `mult` on `ultimate_playoff_pr.base_pr` and `playoff_pr`

| Constant / knob | Current value | File:line | What it controls |
|-----------------|---------------|-----------|------------------|
| ROY-tier boost (Wembanyama, Banchero, Barnes, Ball, Morant) | `1.20` each | `leakage_guards.py:30-34` | Full ROY pedigree |
| Podium-tier boost (Holmgren, Miller, J-Will, Kessler, Mobley, Cunningham, Edwards, Haliburton) | `1.10` each | `leakage_guards.py:35-42` | Secondary pedigree |
| Eligibility gate | `award_season < target_season` | `leakage_guards.py:68-74` | Leakage-safe filter |
| Round precision | `ROUND(pr * mult, 2)` | `apply_pedigree_trajectory_boost.py:72-84` | Stored PR precision |
| Re-apply warning | multiplies current values again | `apply_pedigree_trajectory_boost.py:8-9` | Must rebuild upstream before re-run |

Player ↔ season mapping: full table at `leakage_guards.py:29-43`.

---

## 7. Playoff Player Rating — `calculate_ultimate_playoff_pr.py`

**Formula:** `playoff_pr = round(base_pr × youth_multiplier × playoff_multiplier, 2)`

| Constant / knob | Current value | File:line | What it controls |
|-----------------|---------------|-----------|------------------|
| Missing clutch multiplier default | `1.00` | `calculate_ultimate_playoff_pr.py:102-104` | When no `playoff_riser_choker` row |
| Youth multiplier (Rookie or Sophomore) | `1.10` | `calculate_ultimate_playoff_pr.py:106-107` | Experience-level bump |
| Youth multiplier (else) | `1.00` | `calculate_ultimate_playoff_pr.py:106-107` | Veterans |
| Youth experience labels | `["Rookie", "Sophomore"]` | `calculate_ultimate_playoff_pr.py:106` | Case-sensitive set membership |
| Default experience level | `"Veteran"` | `calculate_ultimate_playoff_pr.py:100` | Fill NA |
| Clutch input column | `playoff_riser_choker.playoff_multiplier` | `calculate_ultimate_playoff_pr.py:74-81` | From clutch index step |

---

## 8. Composite clutch index — `build_composite_clutch_index.py`

**Pipeline:** 3-year trailing window → baseline + Q4 + elimination scores → `total_score` ∈ [−3, +3] → `PLAYOFF_MULTIPLIER[total_score]`

### Score → multiplier map

| Constant / knob | Current value | File:line | What it controls |
|-----------------|---------------|-----------|------------------|
| `total_score = +3` | mult `1.15` | `build_composite_clutch_index.py:50` | Max riser |
| `total_score = +2` | mult `1.10` | `build_composite_clutch_index.py:51` | |
| `total_score = +1` | mult `1.05` | `build_composite_clutch_index.py:52` | |
| `total_score = 0` | mult `1.00` | `build_composite_clutch_index.py:53` | Neutral |
| `total_score = −1` | mult `0.95` | `build_composite_clutch_index.py:54` | |
| `total_score = −2` | mult `0.85` | `build_composite_clutch_index.py:55` | |
| `total_score = −3` | mult `0.75` | `build_composite_clutch_index.py:56` | Max choker |

### Qualification filters

| Constant / knob | Current value | File:line | What it controls |
|-----------------|---------------|-----------|------------------|
| Min playoff MPG | `22.0` | `build_composite_clutch_index.py:345` | Player included in index |
| Min playoff GP | `10` | `build_composite_clutch_index.py:345` | Player included in index |
| Trailing window length | 3 seasons | `build_composite_clutch_index.py:321` | Via `trailing_three_seasons(source)` |
| FMVP floor on negative total | `total = max(total, 0)` if FMVP | `build_composite_clutch_index.py:379` | Finals MVPs cannot go negative |

### Baseline score thresholds (`baseline_score_vec`)

| Constant / knob | Current value | File:line | What it controls |
|-----------------|---------------|-----------|------------------|
| Choker performance index ceiling | `pi <= 0.70` | `build_composite_clutch_index.py:290` | Choker flag (with USG×TS ratio) |
| Choker TS delta | `dt <= -0.06` | `build_composite_clutch_index.py:291` | Choker flag (with USG delta ≤ 0) |
| Riser USG delta | `du > 0` | `build_composite_clutch_index.py:293` | Riser flag |
| Riser TS delta floor | `dt >= -0.06` | `build_composite_clutch_index.py:293` | Riser flag |
| Baseline score values | `−1`, `0`, `+1` | `build_composite_clutch_index.py:294` | Per component |

### Q4 score thresholds (`q4_score_vec`)

| Constant / knob | Current value | File:line | What it controls |
|-----------------|---------------|-----------|------------------|
| Q4 riser TS delta | `d >= 0.07` → `+1` | `build_composite_clutch_index.py:301` | Q4 component |
| Q4 choker TS delta | `d <= -0.07` → `−1` | `build_composite_clutch_index.py:301` | Q4 component |
| Q4 neutral | else `0` | `build_composite_clutch_index.py:301` | |

### Elimination score thresholds (`elim_score_vec`)

| Constant / knob | Current value | File:line | What it controls |
|-----------------|---------------|-----------|------------------|
| Elim TS delta riser | `d >= 0.10` → `+1` | `build_composite_clutch_index.py:311` | G5–G7 proxy games |
| Elim TS delta choker | `d <= -0.10` → `−1` | `build_composite_clutch_index.py:311` | |
| Missing elim data | `0` | `build_composite_clutch_index.py:309-310` | |
| Elim TS formula denominator | `2.0 * (FGA + 0.44 * FTA)` | `build_composite_clutch_index.py:271` | True shooting on elimination games |

### API / timing (not formula weights)

| Constant / knob | Current value | File:line | What it controls |
|-----------------|---------------|-----------|------------------|
| `PAUSE_S` | `1.5` | `build_composite_clutch_index.py:40` | Sleep between API calls |
| `TIMEOUT` | `90` | `build_composite_clutch_index.py:41` | NBA API timeout (seconds) |

---

## 9. Durability profiles — `build_player_durability_profiles.py`

Feeds MC injury rolls (`rs_durability`, `po_durability`). Listed because it sits between
playoff PR and simulation in the pipeline.

| Constant / knob | Current value | File:line | What it controls |
|-----------------|---------------|-----------|------------------|
| RS durability clamp min | `0.40` | `build_player_durability_profiles.py:89` | Floor on RS availability |
| RS durability clamp max | `1.00` | `build_player_durability_profiles.py:89` | Ceiling on RS availability |
| Rookie RS default | `0.70` (clamped) | `build_player_durability_profiles.py:111,114` | Missing GP or rookie tier |
| Missing-both-seasons non-rookie | `0.75` | `build_player_durability_profiles.py:112` | Fallback availability |
| Sophomore RS formula | `gp_source / 82.0` (clamped) | `build_player_durability_profiles.py:115-117` | Second-year scale |
| Veteran two-season formula | `(gp_prior + gp_source) / 164.0` (clamped) | `build_player_durability_profiles.py:118-120` | Two full seasons |
| Veteran one-season formula | `total / 82.0` (clamped) | `build_player_durability_profiles.py:121-123` | Single season GP |
| Zero GP fallback | `clamp(0.0)` → `0.40` | `build_player_durability_profiles.py:124` | Minimum after clamp |
| Full RS season denominator | `82` | `build_player_durability_profiles.py:117,123` | Games in numerator scaling |
| Two-season denominator | `164` | `build_player_durability_profiles.py:120` | Veteran two-year scale |
| PO durability no-playoff default | `0.90` | `build_player_durability_profiles.py:227` | No playoff stint data |
| PO durability ratio cap | `min(1.0, pgp / team_po_gp)` | `build_player_durability_profiles.py:222` | Per-season playoff GP share |

---

## 10. Team RS projection (9-man) — `build_projected_team_pr_25_26.py`

**Formula:** `final_team_pr = round(base_team_pr × coach_mult × playstyle_mult, 2)`

| Constant / knob | Current value | File:line | What it controls |
|-----------------|---------------|-----------|------------------|
| Rotation target size | `9` players | `build_projected_team_pr_25_26.py:115,121` | 9-man sum of PR |
| Position quota C | `2` | `build_projected_team_pr_25_26.py:46` | Positional draft |
| Position quota F | `3` | `build_projected_team_pr_25_26.py:47` | Positional draft |
| Position quota G | `3` | `build_projected_team_pr_25_26.py:48` | Positional draft |
| Coach grade S | `1.08` | `build_projected_team_pr_25_26.py:34` | Coach multiplier |
| Coach grade A | `1.04` | `build_projected_team_pr_25_26.py:35` | |
| Coach grade B | `1.02` | `build_projected_team_pr_25_26.py:36` | |
| Coach grade C | `1.00` | `build_projected_team_pr_25_26.py:37` | |
| Coach grade D | `0.97` | `build_projected_team_pr_25_26.py:38` | |
| Coach grade F | `0.95` | `build_projected_team_pr_25_26.py:39` | |
| Missing coach grade default | `1.00` | `build_projected_team_pr_25_26.py:75-76` | |
| `PLAYSTYLE_FALLBACK_MULT` | `0.90` | `build_projected_team_pr_25_26.py:42` | Unknown/missing playstyle |
| `DEFAULT_PR` (missing player PR) | `5.0` | `build_projected_team_pr_25_26.py:51` | Roster join fill |
| `DEFAULT_POSITION` | `"F"` | `build_projected_team_pr_25_26.py:52` | Missing position fill |

### Playstyle multipliers (DB table — populated by `create_playstyle_multipliers.py`)

Read at runtime from `playstyle_multipliers`; fallback `0.90` above if label missing.

| Playstyle label | Multiplier | File:line |
|-----------------|------------|-----------|
| Perfect | `1.15` | `create_playstyle_multipliers.py:12` |
| Heliocentric | `1.05` | `create_playstyle_multipliers.py:13` |
| Motion | `1.03` | `create_playstyle_multipliers.py:14` |
| Pace & Space | `1.03` | `create_playstyle_multipliers.py:15` |
| Elite Balanced | `1.03` | `create_playstyle_multipliers.py:16` |
| Paint & Pound | `1.02` | `create_playstyle_multipliers.py:17` |
| Undefined / No Identity | `0.90` | `create_playstyle_multipliers.py:18` |

---

## 11. Team PO projection (8-man) — `build_team_playoff_pr_25_26.py`

**Formula:** `final_playoff_pr = base_8 × amp_coach × playstyle_mult × continuity_mult`

where `amp_coach = 1.0 + (coach_mult − 1.0) × COACH_AMPLIFY`

| Constant / knob | Current value | File:line | What it controls |
|-----------------|---------------|-----------|------------------|
| Rotation target size | `8` players | `build_team_playoff_pr_25_26.py:400-414` | 8-man playoff sum |
| Positional draft G (initial) | `2` | `build_team_playoff_pr_25_26.py:373-374` | PO rotation draft |
| Positional draft F (initial) | `2` | `build_team_playoff_pr_25_26.py:374-375` | |
| Positional draft C (initial) | `1` | `build_team_playoff_pr_25_26.py:375-376` | |
| `STAR_BOOST` (top PR in 8-man) | `1.15` | `build_team_playoff_pr_25_26.py:46,416-418` | Top `min(3, n)` players |
| Top boosted count | `min(3, len(pr_values))` | `build_team_playoff_pr_25_26.py:416-418` | Star premium count |
| `COACH_AMPLIFY` | `1.5` | `build_team_playoff_pr_25_26.py:47,521` | PO coach multiplier stretch |
| `DEFAULT_COACH_MULT` | `1.00` | `build_team_playoff_pr_25_26.py:43` | Missing grade |
| `DEFAULT_PLAYSTYLE_MULT` | `1.00` | `build_team_playoff_pr_25_26.py:44,525` | Missing playstyle (PO path) |
| Coach grades S–F | same as RS (`1.08`…`0.95`) | `build_team_playoff_pr_25_26.py:34-41` | `GRADE_TO_COACH_MULT` |
| `CONTINUITY_HIGH` | `1.05` | `build_team_playoff_pr_25_26.py:49` | High overlap tier |
| `CONTINUITY_DEFAULT` | `1.00` | `build_team_playoff_pr_25_26.py:51` | Mid overlap tier |
| `CONTINUITY_LOW` | `0.95` | `build_team_playoff_pr_25_26.py:50` | Low overlap / top-2 departed |
| `CONTINUITY_OVERLAP_HIGH` | `0.70` | `build_team_playoff_pr_25_26.py:54` | Threshold → high tier |
| `CONTINUITY_OVERLAP_DEFAULT_MIN` | `0.50` | `build_team_playoff_pr_25_26.py:55` | Threshold → default tier |
| Top-2 gate trigger | source top-2 on different team → low | `build_team_playoff_pr_25_26.py:274-279` | Forces `0.95` |
| Top-2 count | `LIMIT 2` | `build_team_playoff_pr_25_26.py:114` | Gate input |
| `LEGACY_*_CONTINUITY_TEAMS` | hardcoded sets | `build_team_playoff_pr_25_26.py:64-67` | Review only — **not used in production path** |

Playstyle values: same DB table as §10 (`create_playstyle_multipliers.py:11-18`); PO
default when missing is `1.00` not `0.90` (`build_team_playoff_pr_25_26.py:44,525`).

---

## 12. Monte Carlo simulation — `run_monte_carlo.py`

### Global sim parameters

| Constant / knob | Current value | File:line | What it controls |
|-----------------|---------------|-----------|------------------|
| `N_SIMULATIONS` | `1000` | `run_monte_carlo.py:31` | MC run count |
| `RS_GAMES_PER_TEAM` | `82` | `run_monte_carlo.py:32` | Regular-season games per team |
| `N_TEAMS` | `30` | `run_monte_carlo.py:33` | League size |
| `DEFAULT_RUN_ID` | `"production"` | `run_monte_carlo.py:30` | Results table key |
| Master RNG seed | `20260514` | `run_monte_carlo.py:884` | Reproducible sim stream |

### Bradley-Terry / home-court (not labeled BT in code — ratio win draw)

| Constant / knob | Current value | File:line | What it controls |
|-----------------|---------------|-----------|------------------|
| `HOME_MULT` | `1.04` | `run_monte_carlo.py:45` | Home team rating multiplier |
| `AWAY_FATIGUE_MULT` | `0.96` | `run_monte_carlo.py:46` | Away team penalty when fatigued |
| `AWAY_FATIGUE_PROB` | `0.135` | `run_monte_carlo.py:47` | Per-game fatigue roll probability |
| Win probability tiebreak | `random * (rh + ra) < rh` | `run_monte_carlo.py:382-385` | Stochastic BT draw |
| Zero-rating tie | `50%` coin flip | `run_monte_carlo.py:383-384` | When `rh + ra <= 0` |

### RS team rating

**Formula:** `rating_rs = sum(9 daily PR) × coach_mult × rs_playstyle_mult × continuity` (continuity only if `team_game_number ≤ 20`)

| Constant / knob | Current value | File:line | What it controls |
|-----------------|---------------|-----------|------------------|
| Daily rotation size | `9` contributors | `run_monte_carlo.py:279-286` | RS active players per game |
| `CONTINUITY_CUTOFF_GAME` | `20` | `run_monte_carlo.py:52` | Last RS game with continuity mult |
| Continuity after game 20 | `1.0` (off) | `run_monte_carlo.py:365-366` | RS late-season |
| Play-in neutral continuity | `CONTINUITY_CUTOFF_GAME + 1` (21) | `run_monte_carlo.py:484-485` | Continuity off for play-in |
| RS depth: starter slots | 2G + 2F + 1C | `run_monte_carlo.py:149-151` | Starting five by position |
| RS depth: bench slots | 1G + 1F + 1 (C or F) | `run_monte_carlo.py:156-175` | Bench three |
| RS depth: ninth man | best remaining | `run_monte_carlo.py:177-180` | 9th contributor |
| Min starters enforced | `5` (RS), `3` bench | `run_monte_carlo.py:663-664` | Depth chart floor |

### PO team rating

**Formula:** `rating_po = sum_playoff_eight(top 8 of frozen 9) × amp_coach × po_playstyle × continuity_po`

| Constant / knob | Current value | File:line | What it controls |
|-----------------|---------------|-----------|------------------|
| `STAR_BOOST_PO` | `1.15` | `run_monte_carlo.py:49` | Top playoff PR in 8-man sum |
| `TOP_BOOSTED_N` | `3` | `run_monte_carlo.py:50` | Count of PO star boosts |
| PO series rotation freeze | 9 players, roll once | `run_monte_carlo.py:298-351` | Series-long availability |
| Playoff series length | best-of-7, first to `4` wins | `run_monte_carlo.py:410-420` | Bracket rounds |
| Home game pattern (higher seed) | games `{0,1,4,6}` | `run_monte_carlo.py:408-415` | HCA schedule in series |

### Injury / availability rolls

| Constant / knob | Current value | File:line | What it controls |
|-----------------|---------------|-----------|------------------|
| RS starter plays | `rng.random() < rs_durability` | `run_monte_carlo.py:248` | Daily RS availability |
| PO starter plays (series) | `rng.random() < po_durability` | `run_monte_carlo.py:313` | Series-long PO availability |
| Missing `rs_durability` default | `0.75` | `run_monte_carlo.py:632` | Load-time fill |
| Missing `po_durability` default | `0.75` | `run_monte_carlo.py:633` | Load-time fill |
| Missing player `pr` default | `5.0` | `run_monte_carlo.py:627` | Roster join fill |
| Missing `playoff_pr` default | falls back to `pr` | `run_monte_carlo.py:628` | PO rating fill |
| Missing position default | `"F"` | `run_monte_carlo.py:629-630` | Position fill |

### Coach / playstyle in MC load path

| Constant / knob | Current value | File:line | What it controls |
|-----------------|---------------|-----------|------------------|
| `COACH_GRADE_MULT` S–F | `1.08` … `0.95` | `run_monte_carlo.py:35-42` | Same map as team builders |
| Missing coach grade in MC | `1.00` | `run_monte_carlo.py:128` | `_coach_mult_from_grade` |
| `PLAYSTYLE_FALLBACK_MULT` | `0.90` | `run_monte_carlo.py:43,677-679` | RS playstyle lookup miss |

---

## 13. Cross-file duplicates (change together)

These constants are **copied** in multiple files — a tune in one place may not take
effect unless all copies align:

| Constant | Files with same values |
|----------|------------------------|
| Coach grade map (S=1.08 … F=0.95) | `build_projected_team_pr_25_26.py:33-40`, `build_team_playoff_pr_25_26.py:34-41`, `run_monte_carlo.py:35-42` |
| PO star boost 1.15 / top 3 | `build_team_playoff_pr_25_26.py:46-47,416-418`, `run_monte_carlo.py:49-50,289-295` |
| Playstyle fallback 0.90 | `build_projected_team_pr_25_26.py:42`, `run_monte_carlo.py:43` |

---

## 14. NOT FOUND in assigned pipeline files

| Item | Notes |
|------|-------|
| Rookie PR formula coefficients | Computed in `calculate_rookie_projected_pr_25_26.py` — **not in assigned read list** |
| Special-effect trigger thresholds | Set in `calculate_offensive_effects.py`, `calculate_mvp_potential.py`, etc. — **not in assigned read list** |
| Progression curve table values | Stored in `player_progression_curves` via `init_progression_curves.py` — **not in assigned read list** |

Verify manually before tuning those layers.
