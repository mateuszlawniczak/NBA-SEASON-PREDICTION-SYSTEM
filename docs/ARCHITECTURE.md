# EYEonPAPER — Architecture & Data-Flow (Current State)

> Audit snapshot. **Read-only** analysis of the repo as it exists today. No code or
> data was changed to produce this document. Evidence is cited as `file.py:line`.

## 1. What this system is

EYEonPAPER is a Python + SQLite Monte Carlo engine that projects an NBA season and
simulates standings, seeds, playoff rounds, and a champion. It has four moving parts:

- **Raw layer** (multi-season, 2020-21 → 2025-26): scraped/fetched box, advanced,
  playoff, team, league, coach, and rookie data.
- **Feature layer**: positions, special effects, progression curves, durability,
  playstyles, coach grades, rookie baselines, and the playoff riser/choker index.
- **Projection layer**: turns one season of stats into a forward-looking Player Rating
  (`ULTIMATE_PR`), a playoff-weighted rating (`ultimate_playoff_pr`), and team ratings
  (`projected_team_pr_25_26`, `team_playoff_pr_25_26`).
- **Simulation layer**: `run_monte_carlo.py` runs N seasons + playoffs and writes
  `simulation_results_25_26`, which the `app.py` Streamlit dashboard reads.

**Scale:** 34 tables (one is the internal `sqlite_sequence`), 55 root scripts, plus an
`archive/` folder of 18 one-off migration/fix scripts (kept for history, off the live
path). Counts verified via `sqlite_master` and the file listing.

## 2. The single most important structural fact

The **raw + feature layers are multi-season**, but the **projection + simulation layers
are single-season and hardcoded**, and they are hardcoded *inconsistently*:

- Every projection script targets the **source** season `TARGET_SEASON = "2024-25"`
  (`calculate_base_pr.py:25`, `calculate_player_pr.py:32`, `apply_progression.py:20`,
  `apply_playoff_experience_pr.py:21`, `calculate_final_simulation_pr.py:17`,
  `calculate_team_pr.py:26`).
- The **output** tables are named for the **target** season `..._25_26`
  (`projected_team_pr_25_26`, `team_playoff_pr_25_26`, `player_starting_teams_25_26`,
  `rookie_projected_pr_25_26`, `team_coaches_25_26`, `simulation_results_25_26`).

So the projection tables' `season` column stores the **stats source** season, not the
season being predicted. Confirmed in the DB: `player_projected_pr` and
`player_simulation_pr` both contain only `season='2024-25'`, while `team_coaches`
contains only `'2025-26'`. The pipeline "predicts 2025-26 from 2024-25 actuals + 2025-26
rookies", but that intent lives only in table names and constants, never in a parameter.

## 3. Layered data-flow graph

Nodes = tables. Edges = scripts (the script that performs the write). `UPDATE`-only
writers are marked `(upd)`. The **dead** `team_simulation_pr` branch and the fully
**orphaned** `team_coaches` table are drawn separately.

```mermaid
flowchart LR
    %% ---------- LAYER 0: RAW ----------
    subgraph L0["Layer 0 — Raw ingestion (multi-season 2020-21..2025-26)"]
        PSB[(player_stats_basic)]
        PSA[(player_stats_advanced)]
        PSBP[(player_stats_basic_playoffs)]
        PSAP[(player_stats_advanced_playoffs)]
        TS[(team_stats)]
        TSP[(team_stats_playoffs)]
        LS[(league_stats)]
        CD[(coach_data)]
        RD[(rookie_data)]
        RRD[(rookie_redshirt_data)]
        PST[(player_starting_teams_25_26)]
    end

    fpb[fetch_player_basic] --> PSB
    hpb[hydrate_player_basic upd] --> PSB
    fpa[fetch_player_advanced] --> PSA
    sp[sync_positions upd] --> PSA
    fpbp[fetch_playoff_basic] --> PSBP
    fpu[fetch_playoff_usg upd] --> PSBP
    fpap[fetch_playoff_advanced] --> PSAP
    fts[fetch_team_stats] --> TS
    hppr[hydrate_prev_playoff_result upd] --> TS
    ftp[fetch_team_playoffs] --> TSP
    bptp[backfill_prev_team_playoffs upd] --> TSP
    fls[fetch_league_stats] --> LS
    fcd[fetch_coach_data] --> CD
    frd[fetch_rookie_data] --> RD
    hrst[hydrate_rookie_signing_teams upd] --> RD
    frds[fetch_redshirt_data] --> RRD
    fpstt[fetch_player_starting_teams_25_26] --> PST

    %% ---------- LAYER 1: FEATURES ----------
    subgraph L1["Layer 1 — Features"]
        PP[(player_positions)]
        PSE[(player_special_effects)]
        PPC[(player_progression_curves)]
        RB[(rookie_baselines)]
        PM[(playstyle_multipliers)]
        TPD[(team_playstyle_data)]
        CSD[(coach_system_data)]
        TC26[(team_coaches_25_26)]
        RPP[(rookie_projected_pr_25_26)]
        PRC[(playoff_riser_choker)]
        PDP[(player_durability_profiles)]
    end

    PSB --> cpp[create_player_positions] --> PP
    PSB --> iype[init_yearly_player_effects] --> PSE
    PSB --> coe[calculate_offensive_effects upd] --> PSE
    PSA --> coe
    PSB --> cmvp[calculate_mvp_potential upd] --> PSE
    PSA --> cmvp
    ipc[init_progression_curves] --> PPC
    irb[init_rookie_baselines] --> RB
    cpm[create_playstyle_multipliers] --> PM
    PSB --> atp[assign_team_playstyles] --> TPD
    TS --> atp
    CD --> ics[init_coach_systems] --> CSD
    ucg[update_coach_grades upd] --> CSD
    CD --> ftc[fetch_team_coaches_25_26] --> TC26
    CSD --> ftc
    RD --> crpp[calculate_rookie_projected_pr_25_26] --> RPP
    RB --> crpp
    api[["NBA API"]] --> bcci[build_composite_clutch_index] --> PRC

    %% ---------- LAYER 2: PROJECTION ----------
    subgraph L2["Layer 2 — Projection"]
        PSPR[(player_simulation_pr)]
        PPPR[(player_projected_pr)]
        PEPR[(player_experience_pr)]
        FSP[(final_simulation_pr)]
        UPR[(ULTIMATE_PR)]
        UPPR[(ultimate_playoff_pr)]
        PT26[(projected_team_pr_25_26)]
        TP26[(team_playoff_pr_25_26)]
    end

    PSB --> cbpr[calculate_base_pr OR calculate_player_pr] --> PSPR
    PSA --> cbpr
    PSPR --> aprog[apply_progression] --> PPPR
    PPPR --> apep[apply_playoff_experience_pr] --> PEPR
    TSP --> apep
    PEPR --> cfsp[calculate_final_simulation_pr] --> FSP
    PSE --> cfsp
    FSP --> bupr[build_ultimate_pr] --> UPR
    RPP --> bupr
    PP --> uupp[update_ultimate_pr_positions upd] --> UPR
    UPR --> cupp[calculate_ultimate_playoff_pr] --> UPPR
    PEPR --> cupp
    PRC --> cupp
    UPPR --> aptb[apply_pedigree_trajectory_boost upd] --> UPR
    aptb --> UPPR
    UPPR --> bpdp[build_player_durability_profiles] --> PDP
    PSB --> bpdp
    PSBP --> bpdp

    PST --> bpt[build_projected_team_pr_25_26] --> PT26
    UPR --> bpt
    TC26 --> bpt
    TPD --> bpt
    PM --> bpt
    UPPR --> btp[build_team_playoff_pr_25_26] --> TP26
    PST --> btp
    CSD --> btp
    TC26 --> btp
    TPD --> btp

    %% ---------- LAYER 3: SIMULATION ----------
    subgraph L3["Layer 3 — Simulation"]
        SR[(simulation_results_25_26)]
    end

    UPR --> rmc[run_monte_carlo] --> SR
    UPPR --> rmc
    PDP --> rmc
    PST --> rmc
    PT26 --> rmc
    TP26 --> rmc
    PM --> rmc

    %% ---------- LAYER 4: PRESENTATION ----------
    subgraph L4["Layer 4 — Presentation"]
        APP{{app.py Streamlit}}
    end
    SR --> APP
    UPR --> APP
    PST --> APP
    PSB --> APP
    RD --> APP
    rmc -. imported as module .-> APP

    %% ---------- DEAD / ORPHANED ----------
    subgraph DEAD["Dead / orphaned (not on the path to simulation_results_25_26)"]
        TSPR[(team_simulation_pr)]
        TCOACH[(team_coaches — no reader, no writer)]
    end
    ctp[calculate_team_pr] --> TSPR
    ctpb[calculate_team_pr_base] --> TSPR
    cptp[calculate_projected_team_pr] --> TSPR
    acm[apply_coach_multipliers upd] --> TSPR
```

## 4. Narrative: how one number becomes a champion probability

1. **Ingest** (Layer 0). `fetch_*` pulls six seasons of box/advanced/playoff/team/league
   data; `hydrate_*`, `sync_*`, `backfill_*` patch columns in place via `UPDATE`
   (e.g. `hydrate_player_basic.py:318`, `backfill_prev_team_playoffs.py:92`).

2. **Featurize** (Layer 1). `create_player_positions` maps positions to G/F/C;
   `init_yearly_player_effects` seeds `player_special_effects`, then
   `calculate_offensive_effects` and `calculate_mvp_potential` flip effect flags via
   `UPDATE` (`calculate_offensive_effects.py:342`, `calculate_mvp_potential.py:361`);
   `assign_team_playstyles` labels each team; `build_composite_clutch_index` scrapes a
   3-year playoff window and writes the riser/choker multiplier
   (`build_composite_clutch_index.py:33,327`).

3. **Base rating** (Layer 2). `calculate_base_pr` / `calculate_player_pr` join basic +
   advanced for 2024-25 and write `player_simulation_pr.base_pr`
   (`calculate_base_pr.py:130-136`). `apply_progression` applies an age curve →
   `player_projected_pr` (`apply_progression.py:29-41,99-100`). `apply_playoff_experience_pr`
   applies a playoff-experience multiplier via `team_stats_playoffs.playoff_result` →
   `player_experience_pr` (`apply_playoff_experience_pr.py:24-32,83-91`).

4. **Effects stack** (Layer 2). `calculate_final_simulation_pr` adds special-effect
   bonuses (`calculate_final_simulation_pr.py:20-29`) → `final_simulation_pr`.
   `build_ultimate_pr` unions veterans + rookies into `ULTIMATE_PR`
   (`build_ultimate_pr.py:33-48`). `update_ultimate_pr_positions` fills positions from
   `player_positions` (`update_ultimate_pr_positions.py:277-286`);
   `apply_pedigree_trajectory_boost` bumps high-pedigree youth
   (`apply_pedigree_trajectory_boost.py:62,72`).

5. **Playoff rating** (Layer 2). `calculate_ultimate_playoff_pr` multiplies base PR by a
   youth multiplier and the clutch multiplier → `ultimate_playoff_pr`
   (`calculate_ultimate_playoff_pr.py:80-86`). `build_player_durability_profiles` reads
   `ultimate_playoff_pr` + basic/playoff games to produce injury durability
   (`build_player_durability_profiles.py:128-143`).

6. **Team ratings** (Layer 2). `build_projected_team_pr_25_26` drafts a 9-man rotation
   from `ULTIMATE_PR` and applies coach + playstyle multipliers
   (`build_projected_team_pr_25_26.py:87-121,206`) → `projected_team_pr_25_26`.
   `build_team_playoff_pr_25_26` drafts an 8-man rotation from `ultimate_playoff_pr` with
   a star boost + amplified coach + continuity multiplier
   (`build_team_playoff_pr_25_26.py:97-171,266`) → `team_playoff_pr_25_26`.

7. **Simulate** (Layer 3). `run_monte_carlo` reads `ULTIMATE_PR`, `ultimate_playoff_pr`,
   `player_durability_profiles`, `player_starting_teams_25_26`, `projected_team_pr_25_26`,
   `team_playoff_pr_25_26`, `playstyle_multipliers`
   (`run_monte_carlo.py:548-599`), runs a Bradley-Terry season + playoffs, and writes
   `simulation_results_25_26` (`run_monte_carlo.py:809-828`).

8. **Present** (Layer 4). `app.py` imports `run_monte_carlo` as a module
   (`app.py:37`) for the interactive "adjust variables" tab and reads
   `simulation_results_25_26` for the baseline view (`app.py:352`).

## 5. Cross-layer wrinkle worth flagging

`build_player_durability_profiles` sits in the feature layer conceptually but **depends on
`ultimate_playoff_pr`** (`build_player_durability_profiles.py:128`), which is a Layer-2
projection output. So durability cannot be built before playoff PR. The orchestrator
(see `SEASON_REFACTOR.md`) must sequence it *after* `calculate_ultimate_playoff_pr`, not
with the other Layer-1 features.

## 6. Where the two hardcoded simulators diverge

`run_monte_carlo.py` and `monte_carlo_season_25_26.py` are near-duplicates writing the
same `simulation_results_25_26`. `app.py:37` imports **`run_monte_carlo`**, making it the
canonical one. Difference in reads: `run_monte_carlo` pulls continuity from
`projected_team_pr_25_26` when the column exists and falls back to
`team_playoff_pr_25_26` (`run_monte_carlo.py:543,577-594`); `monte_carlo_season_25_26`
reads a fixed set (`monte_carlo_season_25_26.py:463-494`). Treat
`monte_carlo_season_25_26.py` as superseded (see `CLEANUP_PLAN.md`).
