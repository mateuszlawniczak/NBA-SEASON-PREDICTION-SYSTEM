"""Guardrail 2: fingerprint of the six EXISTING seasons (2020-21 -> 2025-26).

For each affected table, restricted to existing seasons only, capture:
  * row count
  * per-column NULL counts
  * a content hash over all rows (ordered deterministically) so ANY value
    change - not just a null-count change - is detected.

Usage:
  py fingerprint.py --save     write baseline to fingerprint_baseline.json
  py fingerprint.py --check    compare current DB against the baseline; prints
                               a drift report and exits non-zero if anything moved.
"""
import hashlib
import json
import os
import sqlite3
import sys

from config import DB_PATH, AFFECTED_TABLES, EXISTING_SEASONS

BASELINE_PATH = os.path.join(os.path.dirname(__file__), "fingerprint_baseline.json")


def table_columns(cur, table):
    return [r[1] for r in cur.execute(f"PRAGMA table_info({table})")]


def fingerprint(con):
    cur = con.cursor()
    placeholders = ",".join("?" for _ in EXISTING_SEASONS)
    out = {}
    for table in AFFECTED_TABLES:
        cols = table_columns(cur, table)
        # Deterministic ordering. All affected tables share these key columns.
        order_cols = [c for c in ("season", "player_id", "team_id", "id") if c in cols]
        order_by = ", ".join(order_cols)
        col_list = ", ".join(cols)

        cur.execute(
            f"SELECT {col_list} FROM {table} WHERE season IN ({placeholders}) ORDER BY {order_by}",
            EXISTING_SEASONS,
        )
        rows = cur.fetchall()

        h = hashlib.md5()
        null_counts = {c: 0 for c in cols}
        for row in rows:
            for c, v in zip(cols, row):
                if v is None:
                    null_counts[c] += 1
            h.update(repr(row).encode("utf-8"))
            h.update(b"\x00")

        out[table] = {
            "row_count": len(rows),
            "content_hash": h.hexdigest(),
            "null_counts": null_counts,
        }
    return out


def print_summary(fp):
    for table, data in fp.items():
        print(f"\n  {table}: rows={data['row_count']}  hash={data['content_hash']}")


def do_save():
    con = sqlite3.connect(DB_PATH)
    fp = fingerprint(con)
    con.close()
    with open(BASELINE_PATH, "w", encoding="utf-8") as f:
        json.dump(fp, f, indent=2)
    print(f"[fingerprint] Baseline saved to {BASELINE_PATH}")
    print_summary(fp)


def do_check():
    if not os.path.exists(BASELINE_PATH):
        print("[fingerprint] No baseline found. Run --save first.")
        sys.exit(2)
    with open(BASELINE_PATH, encoding="utf-8") as f:
        base = json.load(f)
    con = sqlite3.connect(DB_PATH)
    cur = fingerprint(con)
    con.close()

    drift = []
    for table in base:
        b = base[table]
        c = cur.get(table, {})
        if b["row_count"] != c.get("row_count"):
            drift.append(f"{table}: row_count {b['row_count']} -> {c.get('row_count')}")
        if b["content_hash"] != c.get("content_hash"):
            drift.append(f"{table}: CONTENT HASH CHANGED {b['content_hash']} -> {c.get('content_hash')}")
            # Detail which columns' null counts moved, to aid diagnosis.
            for col, n in b["null_counts"].items():
                m = c.get("null_counts", {}).get(col)
                if n != m:
                    drift.append(f"    - {table}.{col} null_count {n} -> {m}")

    if drift:
        print("[fingerprint] !!! DRIFT DETECTED in existing six seasons !!!")
        for d in drift:
            print("   " + d)
        sys.exit(1)
    print("[fingerprint] OK - existing six seasons unchanged (row counts + content hashes match baseline).")
    print_summary(cur)


if __name__ == "__main__":
    if "--save" in sys.argv:
        do_save()
    elif "--check" in sys.argv:
        do_check()
    else:
        print(__doc__)
        sys.exit(2)
