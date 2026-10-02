"""Reproducible aggregate benchmark using generated sources and temporary tenants.

Run with Python from the worktree; no operational files or source bodies are logged.
"""

from __future__ import annotations

import argparse
import ast
import json
import runpy
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.modules.context_hub.bootstrap import bootstrap_context_hub
from backend.modules.context_hub.paths import _tenant_paths
from backend.modules.context_hub.runtime import configure_context_hub
from backend.modules.context_hub.settings import get_settings
from backend.modules.context_hub.watcher_settings import _read_watcher_settings
from backend.services.context_inventory.builder import build_context_inventory
from backend.services.context_inventory.static_cache import _inventory_cache_bypass


def _measure(call, cycles):
    start_cpu, start_wall = time.process_time(), time.perf_counter()
    for index in range(cycles):
        call(index)
    return {
        "cycles": cycles,
        "cpu_seconds": round(time.process_time() - start_cpu, 6),
        "wall_seconds": round(time.perf_counter() - start_wall, 6),
    }


def _inventory_benchmark(base, info, cycles):
    counters = {"ast": 0}
    original_parse = ast.parse
    capability_path = info / "codex_console/capabilities.json"
    capabilities = json.loads(capability_path.read_text(encoding="utf-8"))

    def counted(*args, **kwargs):
        counters["ast"] += 1
        return original_parse(*args, **kwargs)

    def cycle(index):
        title = "Synthetic capability " + str(index)
        capabilities["capabilities"][0]["title"] = title
        capability_path.write_text(json.dumps(capabilities), encoding="utf-8")
        result = build_context_inventory(str(base), str(info), "000002", "checkout")
        assert next(entity for entity in result["entities"] if entity["kind"] == "capability")["title"] == title

    with patch.object(ast, "parse", counted):
        with _inventory_cache_bypass(True):
            uncached = _measure(cycle, cycles)
        uncached["ast_parse_calls"] = counters["ast"]
        counters["ast"] = 0
        cached = _measure(cycle, cycles)
        cached["ast_parse_calls"] = counters["ast"]
    return {"uncached": uncached, "cached": cached, "tenant_inputs_refreshed": True}


def _settings_benchmark(info, cycles):
    paths = _tenant_paths("000002", info_root=info)
    counters = {"connections": 0, "schema_or_metadata_statements": 0}
    original_connect = sqlite3.connect

    def traced(statement):
        if statement.lstrip().upper().startswith(("CREATE ", "ALTER ", "INSERT ", "UPDATE ", "DELETE ")):
            counters["schema_or_metadata_statements"] += 1

    def counted(*args, **kwargs):
        counters["connections"] += 1
        connection = original_connect(*args, **kwargs)
        connection.set_trace_callback(traced)
        return connection

    with patch.object(sqlite3, "connect", counted):
        previous = _measure(lambda _i: get_settings("000002", info_root=info), cycles)
        previous.update(counters)
        counters.update(connections=0, schema_or_metadata_statements=0)
        current = _measure(lambda _i: _read_watcher_settings(paths, "test"), cycles)
        current.update(counters)
    return {"previous_poll": previous, "read_only_poll": current}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles", type=int, default=6)
    parser.add_argument("--files", type=int, default=120)
    parser.add_argument("--functions", type=int, default=50)
    args = parser.parse_args()
    temporary_root = ROOT / ".test-tmp-context-benchmark"
    temporary_root.mkdir(exist_ok=True)
    helper = runpy.run_path(str(ROOT / "tests/test_context_hub_inventory.py"))
    with tempfile.TemporaryDirectory(prefix="inventory-", dir=temporary_root) as directory:
        base, info = helper["_build_fixture"](Path(directory))
        source_root = base / "backend/services/synthetic"
        source_root.mkdir()
        source = "\n".join(f"def synthetic_{index}(value: int):\n    return value + {index}\n" for index in range(args.functions))
        for index in range(args.files):
            (source_root / f"module_{index:03d}.py").write_text(source, encoding="utf-8")
        configure_context_hub(base_dir=base, info_root=info, surface="test")
        bootstrap_context_hub("000002", base_dir=base, info_root=info)
        result = {
            "synthetic_files": args.files,
            "functions_per_file": args.functions,
            "inventory": _inventory_benchmark(base, info, args.cycles),
            "watcher_settings": _settings_benchmark(info, args.cycles),
        }
        print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
