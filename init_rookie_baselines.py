"""
init_rookie_baselines.py
-------------------------
Creates isolated lookup table rookie_baselines (Year 1 baseline PR by draft
and age tier). Does not alter any other tables. Safe re-run: DROP + CREATE.
"""

import os
import sqlite3
import sys

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DROP_SQL = "DROP TABLE IF EXISTS rookie_baselines;"

CREATE_SQL = """
CREATE TABLE rookie_baselines (
    draft_tier      TEXT NOT NULL,
    age_tier        TEXT NOT NULL,
    year_1_base_pr  REAL NOT NULL,
    archetype_tag   TEXT NOT NULL,
    PRIMARY KEY (draft_tier, age_tier)
);
"""

INSERT_SQL = """
INSERT INTO rookie_baselines (
    draft_tier, age_tier, year_1_base_pr, archetype_tag
) VALUES (?, ?, ?, ?);
"""

ROWS = [
    ("Top 3", "Under 21", 25.0, "Generational / Elite Project"),
    ("Top 3", "21+", 27.0, "Pro-Ready Franchise Player"),
    ("Lottery 4-14", "Under 20", 14.0, "High-Ceiling Project"),
    ("Lottery 4-14", "20-21", 17.0, "Upside Rotation Player"),
    ("Lottery 4-14", "22+", 20.0, "Instant Impact Veteran Rookie"),
    ("Late First 15-30", "Any Age", 8.0, "Bench Project / Role Player"),
    ("Second Round", "Any Age", 8.0, "Stash / Fringe Roster"),
]


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(DROP_SQL + CREATE_SQL)
        conn.executemany(INSERT_SQL, ROWS)
        conn.commit()
    finally:
        conn.close()

    print("rookie_baselines table created and populated (7 rows).")
    print()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            """
            SELECT draft_tier, age_tier, year_1_base_pr, archetype_tag
            FROM rookie_baselines
            ORDER BY
                CASE draft_tier
                    WHEN 'Top 3' THEN 1
                    WHEN 'Lottery 4-14' THEN 2
                    WHEN 'Late First 15-30' THEN 3
                    WHEN 'Second Round' THEN 4
                END,
                CASE age_tier
                    WHEN 'Under 20' THEN 1
                    WHEN 'Under 21' THEN 2
                    WHEN '20-21' THEN 3
                    WHEN '21+' THEN 4
                    WHEN '22+' THEN 5
                    WHEN 'Any Age' THEN 6
                END;
            """
        )
        rows = cur.fetchall()
        headers = ("draft_tier", "age_tier", "year_1_base_pr", "archetype_tag")
        col_widths = [max(len(h), *(len(str(r[i])) for r in rows)) for i, h in enumerate(headers)]

        def fmt_row(cells: tuple, *, is_header: bool = False) -> str:
            parts = []
            for i, c in enumerate(cells):
                if i == 2 and not is_header:
                    s = f"{float(c):.1f}"
                else:
                    s = str(c)
                parts.append(s.ljust(col_widths[i]))
            return "  ".join(parts)

        print(fmt_row(tuple(headers), is_header=True))
        print("-" * (sum(col_widths) + 2 * (len(headers) - 1)))
        for row in rows:
            print(fmt_row(row))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
