New-Item -ItemType Directory -Force -Path archive | Out-Null
$archive = @(
  "cleanup_team_simulation_pr.py",
  "fix_advanced_positions.py",
  "fix_offensive_tracking.py",
  "fix_offensive_tracking_playoffs.py",
  "fix_playoff_rim_attempts.py",
  "fix_playoffs_adv_rim3_usg.py",
  "fix_shot_pct.py",
  "fix_shot_pct_playoffs.py",
  "heal_games_started.py",
  "migrate_player_stats_advanced.py",
  "migrate_playoffs_off_reb.py",
  "migrate_team_playoffs_v2.py",
  "patch_open_shots.py",
  "patch_rookie_nulls.py",
  "rename_effects_table.py",
  "rename_effects_table_final.py",
  "trim_rookie_data.py",
  "trim_season.py"
)
foreach ($f in $archive) { git mv $f archive/ }
git status
