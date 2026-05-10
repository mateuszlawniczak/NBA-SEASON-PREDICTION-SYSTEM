import sqlite3

conn = sqlite3.connect("nba_data.db")
cur = conn.cursor()

# Safe SQLite correlated-subquery UPDATE
# Matches strictly on both player_id AND season to respect career position changes
cur.execute("""
    UPDATE player_stats_advanced
    SET position = (
        SELECT psb.position
        FROM player_stats_basic psb
        WHERE psb.player_id = player_stats_advanced.player_id
          AND psb.season    = player_stats_advanced.season
        LIMIT 1
    )
    WHERE EXISTS (
        SELECT 1
        FROM player_stats_basic psb
        WHERE psb.player_id = player_stats_advanced.player_id
          AND psb.season    = player_stats_advanced.season
    )
""")

rows_updated = cur.rowcount
conn.commit()

# Sanity check: any rows still missing a position?
cur.execute("SELECT COUNT(*) FROM player_stats_advanced WHERE position IS NULL OR position = ''")
nulls_remaining = cur.fetchone()[0]

# Sample of synced positions
cur.execute("""
    SELECT psa.player_name, psa.season, psa.position
    FROM player_stats_advanced psa
    ORDER BY psa.player_name, psa.season
    LIMIT 8
""")
sample = cur.fetchall()

conn.close()

print(f"Position sync complete — {rows_updated} row(s) successfully updated in player_stats_advanced.")
print(f"Rows still missing position: {nulls_remaining}")
print("\nSample (player_name, season, position):")
for row in sample:
    print(f"  {row[0]:<30} {row[1]}  {row[2]}")
