"""Smoke-check the offline benchmark against the actual Git baseline."""
from unittest.mock import patch

import pytest

from scripts.benchmark_vendas_sync import benchmark


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
