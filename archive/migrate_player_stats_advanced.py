import sqlite3

DB_PATH = "nba_data.db"


def populate_off_reb() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Verify current state before update
    cur.execute("SELECT COUNT(*) FROM player_stats_advanced WHERE off_reb IS NOT NULL")
    before = cur.fetchone()[0]

    cur.execute(
        """
        UPDATE player_stats_advanced
        SET off_reb = ROUND(
            (
                SELECT psb.reb
                FROM player_stats_basic psb
                WHERE psb.player_id = player_stats_advanced.player_id
                  AND psb.season    = player_stats_advanced.season
                  AND psb.reb IS NOT NULL
            ) - player_stats_advanced.def_reb,
            1
        )
        WHERE player_stats_advanced.def_reb IS NOT NULL
          AND EXISTS (
              SELECT 1
              FROM player_stats_basic psb
              WHERE psb.player_id = player_stats_advanced.player_id
                AND psb.season    = player_stats_advanced.season
                AND psb.reb IS NOT NULL
          )
        """
    )

    rows_updated = cur.rowcount
    conn.commit()

    # Verify after update
    cur.execute("SELECT COUNT(*) FROM player_stats_advanced WHERE off_reb IS NOT NULL")
    after = cur.fetchone()[0]

    # Spot-check a few rows
    cur.execute(
        """
        SELECT
            psa.player_name,
            psa.season,
            psb.reb   AS total_reb,
            psa.def_reb,
            psa.off_reb
        FROM player_stats_advanced psa
        JOIN player_stats_basic    psb
          ON psb.player_id = psa.player_id
         AND psb.season    = psa.season
        WHERE psa.off_reb IS NOT NULL
        ORDER BY psa.off_reb DESC
        LIMIT 5
        """
    )
    samples = cur.fetchall()

    conn.close()

    print(f"[migrate_player_stats_advanced] off_reb populated successfully.")
    print(f"  Rows updated   : {rows_updated}")
    print(f"  Non-NULL before: {before}")
    print(f"  Non-NULL after : {after}")
    print()
    print("  Top-5 off_reb sample (player | season | total_reb | def_reb | off_reb):")
    for row in samples:
        print(f"    {row[0]:<28} {row[1]}  total={row[2]}  def={row[3]}  off={row[4]}")


if __name__ == "__main__":
    populate_off_reb()
