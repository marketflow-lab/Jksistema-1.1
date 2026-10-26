"""Smoke-check the offline benchmark against the actual Git baseline."""
import gzip
import hashlib
import json
import subprocess
from unittest.mock import patch

import pytest

from scripts.benchmark_vendas_sync import benchmark
from scripts import benchmark_vendas_sync as sync_benchmark


def test_benchmark_preserves_data_uses_real_capacity_and_meets_targets():
    with patch("requests.sessions.Session.request", side_effect=AssertionError("The benchmark must stay offline")):
        result = benchmark(days=2, latency_ms=100)
    assert result["kind"] == "offline_virtual_io_simulation"
    assert result["all_targets_passed"]
    single, parallel = result["scenarios"]["1"], result["scenarios"]["4"]
    assert single["total_change_percent"] <= 5
    assert parallel["latest_day_reduction_percent"] >= 40
    assert parallel["baseline"]["max_active_accounts"] == 2
    assert parallel["candidate"]["max_active_accounts"] == 4
    for count, scenario in result["scenarios"].items():
        old, new = scenario["baseline"], scenario["candidate"]
        assert old["requests"] == new["requests"] == int(count) * 2 * 6
        assert old["totals"] == new["totals"]
        assert old["commercial_digest"] == new["commercial_digest"]
        assert old["compositions_digest"] == new["compositions_digest"]
        assert old["turns"][0]["day"] == "2026-10-25"
        for account in range(int(count)):
            assert [turn["day"] for turn in new["turns"] if turn["account"] == account] == ["2026-10-26", "2026-10-25"]
    assert all(turn["day"] == "2026-10-26" for turn in parallel["candidate"]["turns"][:4])


@pytest.mark.parametrize("arguments", [{"days": 0}, {"days": 32}, {"latency_ms": 99}])
def test_benchmark_rejects_unrepresentative_fixture_settings(arguments):
    with pytest.raises(ValueError):
        benchmark(**arguments)


def test_shallow_checkout_uses_exact_offline_baseline_without_fetching():
    sources = {path: sync_benchmark._git_source(sync_benchmark.BASELINE_COMMIT, path)
               for path in sync_benchmark.BASELINE_SHA256}
    missing_history = subprocess.CalledProcessError(128, ["git", "show"])
    with patch.object(sync_benchmark.subprocess, "run", side_effect=missing_history), \
            patch("requests.sessions.Session.request", side_effect=AssertionError("The baseline must stay offline")):
        fallback = {path: sync_benchmark._git_source(sync_benchmark.BASELINE_COMMIT, path)
                    for path in sync_benchmark.BASELINE_SHA256}
        result = benchmark(days=2, latency_ms=100)
    assert fallback == sources
    assert {path: hashlib.sha256(source.encode()).hexdigest() for path, source in fallback.items()} == sync_benchmark.BASELINE_SHA256
    assert result["all_targets_passed"]
    assert all(scenario["same_commercial_data"] for scenario in result["scenarios"].values())


def test_missing_custom_baseline_does_not_silently_use_default_snapshot():
    with patch.object(sync_benchmark.subprocess, "run", side_effect=subprocess.CalledProcessError(128, ["git", "show"])):
        with pytest.raises(subprocess.CalledProcessError):
            sync_benchmark._git_source("another-commit", "backend/services/bling.py")


def test_offline_baseline_rejects_changed_source(tmp_path, monkeypatch):
    with gzip.open(sync_benchmark.BASELINE_FIXTURE, "rt", encoding="utf-8") as stream:
        fixture = json.load(stream)
    fixture["sources"]["backend/services/bling.py"] += "\n# changed baseline\n"
    corrupted = tmp_path / "baseline.json.gz"
    corrupted.write_bytes(gzip.compress(json.dumps(fixture).encode(), mtime=0))
    monkeypatch.setattr(sync_benchmark, "BASELINE_FIXTURE", corrupted)
    with patch.object(sync_benchmark.subprocess, "run", side_effect=subprocess.CalledProcessError(128, ["git", "show"])):
        with pytest.raises(ValueError, match="checksum"):
            sync_benchmark._git_source(sync_benchmark.BASELINE_COMMIT, "backend/services/bling.py")
