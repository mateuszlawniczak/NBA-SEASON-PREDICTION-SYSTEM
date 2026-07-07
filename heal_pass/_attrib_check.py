import sqlite3
from config import DB_PATH

cur = sqlite3.connect(DB_PATH).cursor()
print("2024-25 Fox:")
for r in cur.execute(
    "SELECT player_name, team_abbr, gp FROM player_stats_basic "
    "WHERE season='2024-25' AND player_name LIKE '%Fox%'"
):
    print(" ", r)

checks = [
    ("2017-18", "DeAndre Jordan"),
    ("2017-18", "Lou Williams"),
    ("2018-19", "Nikola Vucevic"),
    ("2019-20", "Clint Capela"),
    ("2023-24", "Pascal Siakam"),
    ("2024-25", "De'Aaron Fox"),
]
print("\nAttribution spot checks:")
for s, n in checks:
    r = cur.execute(
        "SELECT team_abbr, gp, pts FROM player_stats_basic WHERE season=? AND player_name=?",
        (s, n),
    ).fetchone()
    print(f"  {s} {n}: {r}")
