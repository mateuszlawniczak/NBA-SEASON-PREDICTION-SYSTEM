
EYEonPAPER — Progress & Decisions

Start here when you come back to the project. This file tells you where things stand, what each document is for, and every decision already made (so you don't re-litigate them).

Last updated: 2026-07-06

Where the project stands
The engine works and produces a full simulation (simulation_results_25_26, 30 teams) for the 2025-26 season, read by the app.py Streamlit dashboard. The raw data layer is clean and covers six seasons (2020-21 → 2025-26), verified with zero nulls in the columns the rating formula reads and zero lost rows in the basic↔advanced join.
Decision: keep the current engine — do not rebuild from scratch. A full v2 rebuild was considered and rejected; a working engine is an asset worth more than a cleaner blank page. The plan is to clean and extend the existing code.
Current phase: Phase 0 — cleanup (see docs/ROADMAP.md). The goal right now is only to remove dead and superseded files. No formula changes, no schema changes yet.
One free win waiting: the 2025-26 season is now complete in the raw tables, so once backtesting exists (Phase 3), the very first thing it can do is score the engine's own 2025-26 prediction against what actually happened.

Document map (what each file is for)
FileRead it when…README.mdYou want the pitch — what the engine does and why. The CV-facing front doordocs/PROGRESS.md (this file)You're returning to the project and need to reload contextdocs/ARCHITECTURE.mdYou need the technical map — how data flows and which script writes whatdocs/CLEANUP_PLAN.mdYou're doing the cleanup — the keep/delete verdict for every filedocs/ROADMAP.mdYou want the phased plan and what "done" means for each phasedocs/SEASON_REFACTOR.mdYou're starting the multi-season work (Phase 2+) — not before
Deleted as spent/obsolete: cursor_audit_prompt.md (scaffolding), PIPELINE_MAP.md (redundant with ARCHITECTURE), V2_BLUEPRINT.md (rebuild plan, abandoned).

Decision log
Every decision below is settled. Evidence is in docs/CLEANUP_PLAN.md.

Base Player Rating: keep calculate_player_pr.py, delete calculate_base_pr.py. player_simulation_pr has gp/mpg populated → calculate_player_pr is the current writer. It's also the architecturally correct one: it has no games-played penalty, so it won't double-count availability (injuries are handled separately in the Monte Carlo).
Riser/choker: keep build_composite_clutch_index.py, delete calculate_playoff_riser_choker.py. The live table's columns match the former.
Simulator: keep run_monte_carlo.py, delete monte_carlo_season_25_26.py. app.py imports run_monte_carlo; nothing imports the other.
Player effects: keep init_yearly_player_effects.py, delete init_player_effects.py. The live table is season-keyed, which only the former produces.
Dead team-rating branch: delete the table team_simulation_pr and its four writers (calculate_team_pr.py, calculate_team_pr_base.py, calculate_projected_team_pr.py, apply_coach_multipliers.py). Nothing on the live path reads that table.
Orphan table team_coaches: delete. No reader or writer anywhere.
Delete via git rm, not an archive/ folder. Git history is the archive. Also delete the existing archive/ folder for the same reason. ⚠️ docs/CLEANUP_PLAN.md still says "archive" in places — read that as "delete."
reset_database.py: retarget. It currently clears the dead team_simulation_pr; point it at the real live tables (or fold into the future orchestrator).


Phase 0 delete list (the cleanup, in one place)
Scripts to git rm (8): calculate_base_pr.py, calculate_playoff_riser_choker.py, monte_carlo_season_25_26.py, init_player_effects.py, calculate_team_pr.py, calculate_team_pr_base.py, calculate_projected_team_pr.py, apply_coach_multipliers.py.
Tables to drop (2, after a DB backup): team_simulation_pr, team_coaches.
Folder to remove: archive/.
Before deleting anything: cp nba_data.db nba_data.backup.db. Then verify the engine still runs and reproduces the same 30-row simulation_results_25_26.

Next steps (after Phase 0)

Phase 1 — make season a column, not a table-name suffix (rename the _25_26 tables). docs/ROADMAP.md + docs/CLEANUP_PLAN.md §4.
Phase 2 — one --season parameter runs the whole pipeline for any season. docs/SEASON_REFACTOR.md §4.
Phase 3 — backtesting + first accuracy numbers. docs/SEASON_REFACTOR.md §6.
Phase 4 — tune the formulas, using the backtest as the scoreboard.


Housekeeping note
After Phase 0 is done, do a 5-minute pass on docs/ARCHITECTURE.md to delete the "Dead / orphaned" section — once those files are gone, that part of the map is history.
