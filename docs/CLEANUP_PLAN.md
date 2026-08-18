# EYEonPAPER — Cleanup & Consolidation Plan

> Steps 2 & 3 of the audit. Keep/merge/archive/delete calls for every table and script,
> then a leaner season-keyed schema. Evidence cited as `file.py:line`.

**Status (2026-08):** most of this spec has been executed (Phases 0–1). The body below is
the original audit snapshot and is left as historical spec. Completed vs still-open:

| Item | Status |
|------|--------|
| Dead `team_simulation_pr` writers / table | ✅ done — deleted from the live path |
| Canonical Base PR = `calculate_player_pr.py` (no GP penalty) | ✅ done — `calculate_base_pr.py` deleted |
| Canonical MC = `run_monte_carlo.py` | ✅ done — `monte_carlo_season_25_26.py` deleted |
| Canonical clutch = `build_composite_clutch_index.py` | ✅ done — `calculate_playoff_riser_choker.py` deleted |
| Canonical effects = `init_yearly_player_effects.py` | ✅ done — `init_player_effects.py` deleted |
| One-off scripts in `archive/` (off the live path) | ✅ done — `archive/` holds migration/fix scripts |
| SQLite databases not git-tracked | ✅ done — `.gitignore` has `*.db` |
| Season-keyed table names (`team_projection`, `simulation_results`, …) | ✅ done — Phase 1 |
| `pipeline.py` orchestrator | ✅ done |
| Fold `final_simulation_pr` into `ULTIMATE_PR` | ⬜ still spec — table still exists |
| `pipeline.py --dry-run` / `requirements.txt` | ⬜ still spec |
| Rename `player_starting_teams` → `roster_assignments` | ⬜ not done — kept `player_starting_teams` |

"Live" in the original text meant the path to `simulation_results_25_26` or `app.py`. Those
year-suffixed tables have since been renamed.

---

---

## 1. Verifying your redundancy hypotheses

### H1 — `calculate_team_pr` / `calculate_team_pr_base` / `calculate_projected_team_pr` write `team_simulation_pr`, but the MC reads `projected_team_pr_25_26` / `team_playoff_pr_25_26`, so they may be dead. **CONFIRMED (dead).**

- All three write `team_simulation_pr`: `calculate_team_pr.py:488`,
  `calculate_team_pr_base.py:205`, `calculate_projected_team_pr.py:395`.
- `apply_coach_multipliers.py:123` only `UPDATE`s `team_simulation_pr`.
- The only readers of `team_simulation_pr` are those same writers plus `reset_database.py`
  and `archive/cleanup_team_simulation_pr.py`. **No live consumer.** The Monte Carlo reads
  `projected_team_pr_25_26` and `team_playoff_pr_25_26` instead
  (`run_monte_carlo.py:577,594`), which are built from `ULTIMATE_PR` /
  `ultimate_playoff_pr` — not from `team_simulation_pr`.
- **Verdict:** `team_simulation_pr` and its four writers are a dead branch. ARCHIVE.

### H2 — `calculate_base_pr` and `calculate_player_pr` both write `player_simulation_pr`. **CONFIRMED (duplicate writers) — needs your decision.**

- Both create + delete-season + insert `player_simulation_pr` for `'2024-25'`
  (`calculate_base_pr.py:93,277,306`; `calculate_player_pr.py:82,233,257`).
- They use **different formulas**: `calculate_base_pr` applies a `gp/82` availability
  penalty and MPG tiers (`calculate_base_pr.py:81-87,186`); `calculate_player_pr`
  explicitly has **no** games-played penalty and a fractional MPG exponent
  (`calculate_player_pr.py:2-19,168`). Whichever runs **last** wins, silently.
- `player_simulation_pr` **is live**: `apply_progression.py:99` reads its `base_pr`.
- **Verdict:** keep exactly one as canonical. **This is a modeling choice only you can
  make.** To decide: check the current DB — run
  `SELECT gp, mpg FROM player_simulation_pr LIMIT 5;`. If `gp`/`mpg` are populated, the
  last writer was `calculate_player_pr` (it inserts those columns; `calculate_base_pr`
  does not). That tells you which formula currently feeds the sim.

### H3 — `run_monte_carlo.py` and `monte_carlo_season_25_26.py` are near-duplicates; `app.py` imports `run_monte_carlo`. **CONFIRMED.**

- `app.py:37` — `import run_monte_carlo as mc`. Nothing imports `monte_carlo_season_25_26`.
- Both write `simulation_results_25_26` (`run_monte_carlo.py:813`,
  `monte_carlo_season_25_26.py:702`).
- **Verdict:** `run_monte_carlo.py` = canonical; `monte_carlo_season_25_26.py` = superseded.

### H4 — `calculate_playoff_riser_choker` and `build_composite_clutch_index` both write `playoff_riser_choker`. **CONFIRMED — `build_composite_clutch_index` is canonical.**

- Both `DROP` + `to_sql(if_exists="replace")` `playoff_riser_choker`
  (`calculate_playoff_riser_choker.py:205-206`; `build_composite_clutch_index.py:326-327`).
- Decisive evidence — the **live table's columns match `build_composite_clutch_index`**,
  not `calculate_playoff_riser_choker`. Live schema is
  `baseline_score, q4_score, elim_score, total_score, playoff_multiplier` (from
  `sqlite_master`), which is the clutch-index output; `calculate_playoff_riser_choker`
  emits a different shape (`tag, reg_usg, ply_usg, delta_ts, ...`,
  `calculate_playoff_riser_choker.py:46-56`).
- The live consumer, `calculate_ultimate_playoff_pr.py:53-54`, reads
  `player_name, playoff_multiplier` — present in both, so it runs against whatever wrote
  last, but the stored columns prove `build_composite_clutch_index` wrote last.
- **Verdict:** `build_composite_clutch_index.py` = canonical; `calculate_playoff_riser_choker.py`
  = superseded. ARCHIVE the latter.

### Bonus finding not in your list — `init_player_effects` vs `init_yearly_player_effects`. **Superseded.**

- `init_player_effects.py:21` creates a **career-level** (no `season`)
  `player_special_effects` with `CREATE TABLE IF NOT EXISTS`.
- `init_yearly_player_effects.py:48` **drops** that and recreates a **season-keyed**
  version (`init_yearly_player_effects.py:21-39`). The live table has a `season` column
  (3,407 rows across six seasons), so `init_yearly_player_effects` is canonical.
- **Verdict:** `init_player_effects.py` = superseded. ARCHIVE.

### Bonus finding — `team_coaches` table is fully orphaned.

- Grep for `team_coaches\b` (excluding `_25_26`) returns **no reads and no writes** in any
  script. It holds 30 rows (`team, season, coach_grade`) but nothing produces or consumes
  it. Likely a pre-`team_coaches_25_26` relic.
- **Verdict:** ARCHIVE/DELETE after a final confirmation query (below).

---

## 2. Table verdicts (34 tables)

| Table | Rows | Status | Recommendation | Reason / evidence |
|---|---:|---|---|---|
| player_stats_basic | 3407 | Live (raw) | KEEP | Root of nearly everything; read by 10+ scripts |
| player_stats_advanced | 3407 | Live (raw) | KEEP | Joined for every PR calc (`calculate_base_pr.py:131`) |
| player_stats_basic_playoffs | 1601 | Live (raw) | KEEP | Durability (`build_player_durability_profiles.py:143`) |
| player_stats_advanced_playoffs | 1601 | Live (raw) | KEEP | Playoff feature source; fetched by `fetch_playoff_advanced` |
| team_stats | 180 | Live (raw) | KEEP | Playstyles + effects + prev-seed hydration |
| team_stats_playoffs | 120 | Live (raw) | KEEP | Playoff-experience multiplier (`apply_playoff_experience_pr.py:84`) **and** backtest ground truth |
| league_stats | 6 | Live (raw) | KEEP | Era baseline in `calculate_offensive_effects.py:108` |
| coach_data | 180 | Live (raw) | KEEP | Feeds `team_coaches_25_26` + `coach_system_data` |
| rookie_data | 608 | Live (raw) | KEEP | Rookie projection + `app.py:425` |
| rookie_redshirt_data | 608 | Live (feature) | KEEP | Redshirt flags for rookie projection |
| player_starting_teams_25_26 | 582 | Live (raw) | KEEP → rename | Season-scoped roster; rename to season-keyed `roster_assignments` (§3) |
| player_positions | 569 | Live (feature) | KEEP | Fills `ULTIMATE_PR.mapped_position` (`update_ultimate_pr_positions.py:278`) |
| player_special_effects | 3407 | Live (feature) | KEEP | Effect stacking (`calculate_final_simulation_pr.py:56`) |
| player_progression_curves | 140 | Live (feature) | KEEP | Static age/position curves |
| rookie_baselines | 7 | Live (feature) | KEEP | Static rookie tiers (`calculate_rookie_projected_pr_25_26.py:87`) |
| playstyle_multipliers | 7 | Live (feature) | KEEP | Static; read by both team builders + MC |
| team_playstyle_data | 180 | Live (feature) | KEEP | Team style labels (`build_projected_team_pr_25_26.py:141`) |
| coach_system_data | 49 | Live (feature) | KEEP | Coach name → grade |
| team_coaches_25_26 | 30 | Live (feature) | KEEP → merge | Fold into season-keyed `team_coaches` (§3) |
| rookie_projected_pr_25_26 | 100 | Live (proj) | KEEP → rename | Season-key it (§3) |
| playoff_riser_choker | 96 | Live (feature) | KEEP | Clutch multiplier for `ultimate_playoff_pr` |
| player_durability_profiles | 669 | Live (proj) | KEEP | Injury rolls in MC (`run_monte_carlo.py:556`) |
| player_simulation_pr | 569 | Live (proj) | KEEP (one writer) | Base PR; see H2 |
| player_projected_pr | 569 | Live (proj) | KEEP | Age-adjusted PR (`apply_playoff_experience_pr.py:83`) |
| player_experience_pr | 569 | Live (proj) | KEEP | Feeds final PR + playoff PR |
| final_simulation_pr | 569 | Live (proj) | KEEP → merge | Merge into `ULTIMATE_PR` build (§3) |
| ULTIMATE_PR | 669 | Live (proj) | KEEP | Core RS rating consumed by MC |
| ultimate_playoff_pr | 669 | Live (proj) | KEEP | Core PO rating consumed by MC |
| projected_team_pr_25_26 | 30 | Live (proj) | KEEP → rename | Season-key it (§3) |
| team_playoff_pr_25_26 | 30 | Live (proj) | KEEP → rename | Season-key it (§3) |
| simulation_results_25_26 | 30 | Live (sim) | KEEP → rename | Season-key it (§3) |
| **team_simulation_pr** | 30 | **Orphaned** | **ARCHIVE** | H1 — written, never read on live path |
| **team_coaches** | 30 | **Orphaned** | **DELETE** | No reader/writer anywhere |
| sqlite_sequence | 7 | Internal | KEEP | SQLite autoincrement bookkeeping |

**Confirm-before-delete queries:**
- `team_coaches`: `SELECT COUNT(*) FROM team_coaches;` then confirm no script references it
  (already verified by grep). Safe to drop.
- `team_simulation_pr`: nothing on the live path reads it; safe to archive once the four
  writer scripts are archived.

---

## 3. Script verdicts (55 root scripts)

### Layer 0 — Raw ingestion (KEEP all; these are your data spine)
`init_db`, `fetch_player_basic`, `hydrate_player_basic`, `fetch_player_advanced`,
`sync_positions`, `fetch_playoff_basic`, `fetch_playoff_usg`, `fetch_playoff_advanced`,
`fetch_team_stats`, `hydrate_prev_playoff_result`, `fetch_team_playoffs`,
`backfill_prev_team_playoffs`, `fetch_league_stats`, `fetch_coach_data`,
`fetch_rookie_data`, `hydrate_rookie_signing_teams`, `fetch_redshirt_data`,
`sync_redshirts`, `fetch_player_starting_teams_25_26` — **KEEP** (season-parameterize the
last one; see `SEASON_REFACTOR.md`).

### Layer 1 — Features (KEEP, except noted)
`create_player_positions`, `init_yearly_player_effects`, `calculate_offensive_effects`,
`calculate_mvp_potential`, `init_progression_curves`, `init_rookie_baselines`,
`create_playstyle_multipliers`, `assign_team_playstyles`, `init_coach_systems`,
`update_coach_grades`, `fetch_team_coaches_25_26`, `calculate_rookie_projected_pr_25_26`,
`build_composite_clutch_index`, `build_player_durability_profiles` — **KEEP**.
- `init_player_effects` — **ARCHIVE** (superseded by `init_yearly_player_effects`).
- `calculate_playoff_riser_choker` — **ARCHIVE** (superseded by `build_composite_clutch_index`).

### Layer 2 — Projection (KEEP core; archive dead team branch)
`apply_progression`, `apply_playoff_experience_pr`, `calculate_final_simulation_pr`,
`build_ultimate_pr`, `update_ultimate_pr_positions`, `apply_pedigree_trajectory_boost`,
`calculate_ultimate_playoff_pr`, `build_projected_team_pr_25_26`,
`build_team_playoff_pr_25_26` — **KEEP**.
- `calculate_base_pr` **vs** `calculate_player_pr` — **KEEP ONE, ARCHIVE the other**
  (H2; your decision).
- `calculate_team_pr`, `calculate_team_pr_base`, `calculate_projected_team_pr`,
  `apply_coach_multipliers` — **ARCHIVE** (H1; dead `team_simulation_pr` branch).

### Layer 3 — Simulation
- `run_monte_carlo` — **KEEP** (canonical, imported by `app.py`).
- `monte_carlo_season_25_26` — **ARCHIVE** (H3; superseded).

### Layer 4 — Presentation
- `app.py` — **KEEP**.

### Utilities / meta
- `reset_database` — **KEEP** but **retarget**: it only clears `player_simulation_pr` and
  `team_simulation_pr` (`reset_database.py:22-23`); once `team_simulation_pr` is archived,
  update it to clear the real live projection tables (or fold into `--dry-run/--reset` of
  the orchestrator).
- `archive.py` — **KEEP as-is** (a PowerShell helper, not Python, that `git mv`s one-offs
  into `archive/`). Note the misleading `.py` extension.
- `archive/` (18 files) — **LEAVE**; already off the live path (one-off fix/migrate/trim
  scripts). Do not re-import.

**Net script effect:** archive 8 scripts (`init_player_effects`,
`calculate_playoff_riser_choker`, `monte_carlo_season_25_26`, `calculate_team_pr`,
`calculate_team_pr_base`, `calculate_projected_team_pr`, `apply_coach_multipliers`, and
one of `calculate_base_pr`/`calculate_player_pr`). 55 → ~47 live scripts.

---

## 4. Proposed consolidated schema (step 3)

Goal: cut table count and, more importantly, make season a **column, not a name**, without
losing any value the simulator consumes.

### 4a. Season-key the per-season-named tables (the multi-season unlock)

| Before (single-season name) | After (general, `season`-keyed) | Migration note |
|---|---|---|
| `projected_team_pr_25_26` | `team_projection` (+`season`) | Add `season` col; `INSERT ... SELECT *, '2025-26'`. Writer takes `--season`. |
| `team_playoff_pr_25_26` | `team_playoff_projection` (+`season`) | Same pattern; PK `(team, season)`. |
| `player_starting_teams_25_26` | `roster_assignments` (+`season`) | Backfill existing rows as `'2025-26'`. |
| `rookie_projected_pr_25_26` | `rookie_projection` (+`season`) | Backfill `'2025-26'`. |
| `team_coaches_25_26` | `team_coaches` (+`season`) | **Reuse the orphaned name** after dropping the dead relic; PK `(team_abbr, season)`. |
| `simulation_results_25_26` | `simulation_results` (+`season` and `run_id`/`created_at`) | Enables storing multiple seasons and backtest runs side by side. |

Each writer changes from a hardcoded table name to `INSERT ... WHERE season = ?` +
`DELETE FROM t WHERE season = ?` (idempotent per season). Consumers (`run_monte_carlo`,
`app.py`) add a `WHERE season = ?` filter.

### 4b. Season-key the projection PR tables consistently

`player_simulation_pr`, `player_projected_pr`, `player_special_effects` already have a
`season` column — good. But `player_experience_pr`, `final_simulation_pr`, `ULTIMATE_PR`,
and `ultimate_playoff_pr` do **not**. Because the pipeline overwrites them each run,
today they implicitly hold "whatever season last ran".

- **Add a `season` column** to `player_experience_pr`, `final_simulation_pr`,
  `ULTIMATE_PR`, `ultimate_playoff_pr`, `player_durability_profiles`, `player_positions`.
  Change PKs to `(player_name, season)`. This is what lets a backtest keep 2023-24 and
  2024-25 projections in the DB simultaneously.

### 4c. Merge overlapping PR concepts

- **`final_simulation_pr` → fold into the `ULTIMATE_PR` build.** `final_simulation_pr` is
  an intermediate consumed only by `build_ultimate_pr.py:40`. It can become a CTE/subquery
  inside a single "build regular-season PR" step, removing one table.
  *Migration:* keep the effect-stacking logic; drop the standalone table after
  `build_ultimate_pr` reads effects directly. (Optional — costs one refactor, saves one
  table. Defer to Phase 1 if risky.)
- **Keep `player_simulation_pr` and `player_projected_pr` separate** — they are distinct
  concepts (raw base vs age-adjusted) and both are read. Do **not** merge.
- **Delete the dead `team_simulation_pr`** entirely (H1); no merge needed since nothing
  reads it.

### 4d. Before/after table count

- **Before:** 34 tables (33 real + `sqlite_sequence`).
- **Deletions:** `team_simulation_pr`, `team_coaches` (relic). → −2.
- **Merges:** `final_simulation_pr` folded into `ULTIMATE_PR` build. → −1.
- **Renames (no count change):** the six `_25_26` tables become season-keyed general
  tables (reusing the freed `team_coaches` name).
- **After:** **~30 real tables** (31 incl. `sqlite_sequence`), all projection/sim tables
  season-keyed, zero single-season table names, zero dead tables.

The headline win is **not** the ~4-table reduction — it is that the schema stops encoding
"2025-26" in table names, which is the precondition for multi-season and backtesting.
