"""
check_pr.py
-----------
One-second, read-only snapshot of ULTIMATE_PR quality.

    py check_pr.py                 # every season in ULTIMATE_PR
    py check_pr.py --season 2025-26

Never writes the database. The connection is opened URI mode=ro; a probe
INSERT is attempted on every run so a writable connection cannot hide.
"""

from __future__ import annotations

import argparse
import math
import os
import sqlite3
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nba_data.db")

# Edit this list. Names are matched accent-insensitively against ULTIMATE_PR.
WATCHLIST = [
    "Stephen Curry",
    "Christian Braun",
    "Domantas Sabonis",
    "Victor Wembanyama",
    "Ausar Thompson",
    "Nikola Jokic",
    "Shai Gilgeous-Alexander",
    "Joel Embiid",
    "Tyrese Haliburton",
    "Jalen Williams",
]


def _fold_name(name: str) -> str:
    """Accent-insensitive, case-insensitive key. 'Nikola Jokic' == 'Nikola Jokić'."""
    decomposed = unicodedata.normalize("NFD", name)
    stripped = "".join(
        ch for ch in decomposed if unicodedata.category(ch) != "Mn"
    )
    return stripped.casefold()


def _pearson_r(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if den_x == 0.0 or den_y == 0.0:
        return None
    return num / (den_x * den_y)


def _fmt_pr(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}"


def _fmt_r(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def _open_readonly() -> sqlite3.Connection:
    uri = Path(DB_PATH).resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _probe_readonly(con: sqlite3.Connection) -> str:
    """Attempt a write. Success is a failure of the read-only guarantee."""
    try:
        con.execute("CREATE TABLE __readonly_probe (x INTEGER)")
    except sqlite3.Error as exc:
        return f"write refused: {exc}"
    raise RuntimeError(
        "database accepted a write; check_pr.py must open read-only."
    )


def _seasons(con: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in con.execute(
            "SELECT DISTINCT season FROM ULTIMATE_PR ORDER BY season"
        )
    ]


def _pr_rows(con: sqlite3.Connection, season: str) -> list[tuple[str, float | None]]:
    return [
        (name, pr)
        for name, pr in con.execute(
            "SELECT player_name, pr FROM ULTIMATE_PR WHERE season = ?",
            (season,),
        )
    ]


def _competition_rank(rows: list[tuple[str, float | None]]) -> dict[str, int]:
    """1-based competition rank (1, 2, 2, 4). Higher PR is better. NULL last."""
    ordered = sorted(
        rows,
        key=lambda item: (
            item[1] is None,
            -(item[1] if item[1] is not None else 0.0),
            item[0],
        ),
    )
    ranks: dict[str, int] = {}
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1][1] == ordered[i][1]:
            j += 1
        rank = i + 1
        for k in range(i, j + 1):
            ranks[ordered[k][0]] = rank
        i = j + 1
    return ranks


def print_resolution(con: sqlite3.Connection, seasons: list[str]) -> None:
    print("=== 1. RESOLUTION ===")
    print(
        f"{'Season':<10} {'Players':>8} {'Distinct':>9}  "
        f"{'Tied':<14} {'Largest tie'}"
    )
    for season in seasons:
        rows = _pr_rows(con, season)
        n = len(rows)
        prs = [pr for _, pr in rows if pr is not None]
        distinct = len(set(prs))
        counts = Counter(prs)
        tied_n = sum(c for c in counts.values() if c > 1)
        pct = (100.0 * tied_n / n) if n else 0.0
        if counts:
            max_n = max(counts.values())
            max_prs = sorted(
                (pr for pr, c in counts.items() if c == max_n), reverse=True
            )
            largest = f"{max_n} on " + ", ".join(_fmt_pr(p) for p in max_prs)
        else:
            largest = "—"
        print(
            f"{season:<10} {n:8d} {distinct:9d} "
            f"{pct:5.1f}% ({tied_n:3d})  {largest}"
        )
    print()


def print_ordering(con: sqlite3.Connection, seasons: list[str]) -> None:
    print("=== 2. ORDERING VS REALITY ===")
    print(
        f"{'Season':<10} {'n_mpg':>6} {'r(PR, mpg)':>12} "
        f"{'n_pts':>6} {'r(PR, pts/100)':>16}"
    )
    for season in seasons:
        pr_map = {
            name: pr
            for name, pr in con.execute(
                """
                SELECT player_name, pr FROM ULTIMATE_PR
                WHERE season = ? AND pr IS NOT NULL
                """,
                (season,),
            )
        }
        mpg_map = {
            name: mpg
            for name, mpg in con.execute(
                """
                SELECT player_name, mpg FROM player_stats_basic
                WHERE season = ? AND mpg IS NOT NULL
                """,
                (season,),
            )
        }
        pts_map = {
            name: pts
            for name, pts in con.execute(
                """
                SELECT player_name, pts_per100 FROM player_stats_advanced
                WHERE season = ? AND pts_per100 IS NOT NULL
                """,
                (season,),
            )
        }
        mpg_names = sorted(set(pr_map) & set(mpg_map))
        pts_names = sorted(set(pr_map) & set(pts_map))
        r_mpg = _pearson_r(
            [pr_map[n] for n in mpg_names], [mpg_map[n] for n in mpg_names]
        )
        r_pts = _pearson_r(
            [pr_map[n] for n in pts_names], [pts_map[n] for n in pts_names]
        )
        print(
            f"{season:<10} {len(mpg_names):6d} {_fmt_r(r_mpg):>12} "
            f"{len(pts_names):6d} {_fmt_r(r_pts):>16}"
        )
    print()


def print_watchlist(con: sqlite3.Connection, seasons: list[str]) -> None:
    print("=== 3. WATCHLIST ===")
    print(
        "Names matched accent-insensitively. "
        "Rank is competition rank (ties share the best place)."
    )
    for season in seasons:
        rows = _pr_rows(con, season)
        by_fold: dict[str, list[tuple[str, float | None]]] = defaultdict(list)
        for name, pr in rows:
            by_fold[_fold_name(name)].append((name, pr))
        counts = Counter(pr for _, pr in rows if pr is not None)
        ranks = _competition_rank(rows)

        print(f"\n{season}")
        print(f"  {'Watchlist':<28} {'DB name':<28} {'PR':>6} {'Rank':>6} {'Tied':>5}")
        found: list[tuple[str, str, float | None]] = []
        missing: list[str] = []
        for wanted in WATCHLIST:
            hits = by_fold.get(_fold_name(wanted), [])
            if not hits:
                missing.append(wanted)
                continue
            for db_name, pr in sorted(hits, key=lambda item: item[0]):
                found.append((wanted, db_name, pr))
                tied = counts[pr] if pr is not None else 0
                print(
                    f"  {wanted:<28} {db_name:<28} {_fmt_pr(pr):>6} "
                    f"{ranks[db_name]:6d} {tied:5d}"
                )

        groups: dict[float, list[str]] = defaultdict(list)
        for wanted, _db_name, pr in found:
            if pr is not None:
                groups[pr].append(wanted)
        for pr, names in sorted(groups.items(), key=lambda item: -item[0]):
            unique = list(dict.fromkeys(names))
            if len(unique) >= 2:
                print(
                    f"  FLAG tied on {_fmt_pr(pr)}: " + ", ".join(unique)
                )
        if missing:
            print("  missing: " + ", ".join(missing))
    print()


def print_top20(con: sqlite3.Connection, seasons: list[str]) -> None:
    print("=== 4. TOP 20 BY PR ===")
    for season in seasons:
        rows = _pr_rows(con, season)
        ordered = sorted(
            rows,
            key=lambda item: (
                item[1] is None,
                -(item[1] if item[1] is not None else 0.0),
                item[0],
            ),
        )
        print(f"\n{season}")
        print(f"  {'Rk':>3}  {'Player':<28} {'PR':>6}")
        for i, (name, pr) in enumerate(ordered[:20], start=1):
            print(f"  {i:3d}  {name:<28} {_fmt_pr(pr):>6}")
    print()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only ULTIMATE_PR quality check."
    )
    parser.add_argument(
        "--season",
        help="Restrict output to one season (e.g. 2025-26).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args(argv)
    if not os.path.exists(DB_PATH):
        print(f"[error] database not found: {DB_PATH}", file=sys.stderr)
        sys.exit(2)

    con = _open_readonly()
    try:
        available = _seasons(con)
        if args.season:
            if args.season not in available:
                print(
                    f"[error] season {args.season!r} not in ULTIMATE_PR "
                    f"({', '.join(available) or 'none'}).",
                    file=sys.stderr,
                )
                sys.exit(2)
            seasons = [args.season]
        else:
            seasons = available

        print_resolution(con, seasons)
        print_ordering(con, seasons)
        print_watchlist(con, seasons)
        print_top20(con, seasons)
        print("[read-only]", _probe_readonly(con))
    finally:
        con.close()


if __name__ == "__main__":
    main()
