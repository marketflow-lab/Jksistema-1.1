"""Repeatable SQLite benchmark for the Vendas date-filter optimization."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.vendas_performance import prepare_vendas_database


def percentile(values: list[float], percentile_value: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile_value)))
    return ordered[index]


def measure(conn: sqlite3.Connection, query: str, params: tuple, iterations: int) -> dict:
    durations = []
    row_count = 0
    for _ in range(iterations):
        started = time.perf_counter()
        rows = conn.execute(query, params).fetchall()
        durations.append((time.perf_counter() - started) * 1000)
        row_count = len(rows)
    return {
        "rows": row_count,
        "p50_ms": round(statistics.median(durations), 3),
        "p95_ms": round(percentile(durations, 0.95), 3),
        "samples_ms": [round(value, 3) for value in durations],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--start", default="2026-06-01")
    parser.add_argument("--end", default="2026-07-01", help="Exclusive optimized upper bound")
    parser.add_argument("--iterations", type=int, default=20)
    args = parser.parse_args()

    if not args.db.is_file():
        raise SystemExit(f"Database not found: {args.db}")

    with tempfile.TemporaryDirectory(prefix="jk-vendas-benchmark-") as temp_dir:
        copy_path = Path(temp_dir) / args.db.name
        shutil.copy2(args.db, copy_path)
        prepare_vendas_database(str(copy_path))
        conn = sqlite3.connect(copy_path)
        try:
            legacy = measure(
                conn,
                "SELECT id_unico, data, sku, quantidade, valor FROM vendas WHERE date(data) >= ? AND date(data) < ?",
                (args.start, args.end),
                args.iterations,
            )
            optimized = measure(
                conn,
                "SELECT id_unico, data, sku, quantidade, valor FROM vendas WHERE data >= ? AND data < ?",
                (args.start, args.end),
                args.iterations,
            )
            plan = conn.execute(
                "EXPLAIN QUERY PLAN SELECT id_unico FROM vendas WHERE data >= ? AND data < ?",
                (args.start, args.end),
            ).fetchall()
        finally:
            conn.close()

    improvement = 0.0
    if legacy["p95_ms"]:
        improvement = (legacy["p95_ms"] - optimized["p95_ms"]) / legacy["p95_ms"] * 100
    print(json.dumps({
        "database": str(args.db),
        "iterations": args.iterations,
        "legacy": legacy,
        "optimized": optimized,
        "p95_improvement_percent": round(improvement, 2),
        "optimized_query_plan": plan,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
