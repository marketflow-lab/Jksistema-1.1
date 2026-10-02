from __future__ import annotations

import json
import os
import runpy
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend.services.context_inventory import builder
from backend.services.context_inventory.cache_sources import _static_source_signature, _validated_source_root
from backend.services.context_inventory.static_cache import (
    _StaticInventoryCache, _inventory_cache_bypass, _object_size,
)


@pytest.fixture
def inventory_env(tmp_path, monkeypatch):
    helper = runpy.run_path(str(Path(__file__).with_name("test_context_hub_inventory.py")))
    base, info = helper["_build_fixture"](tmp_path)
    cache = _StaticInventoryCache()
    monkeypatch.setattr(builder, "STATIC_INVENTORY_CACHE", cache)
    original = builder._build_static_inventory
    builds = []

    def counted(*args):
        builds.append(1)
        return original(*args)

    monkeypatch.setattr(builder, "_build_static_inventory", counted)
    return base, info, cache, builds


def _build(base, info, tenant="000002"):
    return builder.build_context_inventory(str(base), str(info), tenant, "checkout")


def test_static_cache_reuses_derived_inventory_and_isolates_returned_objects(inventory_env):
    base, info, cache, builds = inventory_env
    expected = _build(base, info)
    changed = _build(base, info)
    changed["entities"][0]["metadata"]["injected"] = "modified"
    changed["findings"].append({"code": "caller_modified"})
    assert _build(base, info) == expected
    assert len(builds) == 1
    cached = next(iter(cache._entries.values()))[0]
    assert "capabilities" not in cached and "sku" not in cached and "entities" not in cached
    assert all(not isinstance(value, bytes) for value in cached.values())


@pytest.mark.parametrize("relative", [
    "backend/schemas/catalog.py", "backend/routers/catalog.py",
    "backend/services/catalog/worker.py", "backend/lifecycle.py",
    "static/catalog.html", "static/catalog.js", "tests/test_catalog.py",
    "docs/old-guide.md", "electron_app/main.js", "android_app/build.gradle",
])
def test_static_content_invalidation_ignores_unchanged_mtime_and_size(inventory_env, relative):
    base, info, _cache, builds = inventory_env
    _build(base, info)
    path = base / relative
    original = path.read_bytes()
    stat = path.stat()
    # A valid equal-length edit must invalidate even with exactly the old mtime.
    if path.suffix == ".py":
        updated = original.replace(b"return", b"yield ", 1) if b"return" in original else original.replace(b"str", b"int", 1)
    else:
        updated = original.replace(b"a", b"z", 1) if b"a" in original else original.replace(b"e", b"f", 1)
    if updated == original:
        byte = next(value for value in original if 65 <= value <= 90 or 97 <= value <= 122)
        updated = original.replace(bytes([byte]), bytes([byte + 1]), 1)
    assert len(updated) == len(original) and updated != original
    path.write_bytes(updated)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    _build(base, info)
    assert len(builds) == 2


def test_static_cache_tracks_additions_removals_surface_version_and_force(inventory_env):
    base, info, _cache, builds = inventory_env
    _build(base, info)
    added = base / "backend/services/addition.py"
    added.write_text("def addition():\n    return 1\n", encoding="utf-8")
    _build(base, info)
    assert len(builds) == 2
    added.unlink()
    _build(base, info)
    # Removal restores the original content signature; an existing valid entry is reusable.
    assert len(builds) == 2
    (base / "package.json").write_text('{"version":"9.9.8"}', encoding="utf-8")
    assert _build(base, info)["source_version"] == "9.9.8"
    builder.build_context_inventory(str(base), str(info), "000002", "installed")
    assert len(builds) == 4
    with _inventory_cache_bypass(True):
        _build(base, info)
    assert len(builds) == 5


def test_tenant_sku_and_capabilities_are_fresh_on_every_cached_build(inventory_env):
    base, info, _cache, builds = inventory_env
    first = _build(base, info)
    sku = info / "000002/SKU/001.json"
    payload = json.loads(sku.read_text(encoding="utf-8"))
    payload["nome_produto"] = "Produto atualizado"
    sku.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    capabilities = info / "codex_console/capabilities.json"
    cap = json.loads(capabilities.read_text(encoding="utf-8"))
    cap["capabilities"][0]["title"] = "Capacidade atualizada"
    capabilities.write_text(json.dumps(cap), encoding="utf-8")
    second = _build(base, info)
    assert first != second and len(builds) == 1
    assert next(row for row in second["entities"] if row["kind"] == "capability")["title"] == "Capacidade atualizada"
    other = _build(base, info, "000003")
    assert not [row for row in other["entities"] if row["kind"] in ("sku", "capability")]
    assert len(builds) == 1


def test_edit_during_construction_retries_and_never_caches_a_mixed_snapshot(inventory_env, monkeypatch):
    base, info, cache, builds = inventory_env
    original = builder._build_static_inventory
    worker = base / "backend/services/catalog/worker.py"

    def edited(*args):
        result = original(*args)
        if len(builds) == 1:
            worker.write_text(worker.read_text(encoding="utf-8") + "\ndef newly_added():\n    return 3\n", encoding="utf-8")
        return result

    monkeypatch.setattr(builder, "_build_static_inventory", edited)
    result = _build(base, info)
    assert any(row["title"] == "newly_added" for row in result["entities"])
    assert len(builds) == 2 and len(cache._entries) == 1
    assert _build(base, info) == result


def test_cache_singleflight_does_not_hold_global_lock_during_io(tmp_path):
    base = tmp_path / "app"
    base.mkdir()
    cache = _StaticInventoryCache()
    entered, release = threading.Event(), threading.Event()
    builds = []

    def blocked():
        builds.append(1)
        entered.set()
        assert release.wait(5)
        return {"value": ["ready"]}

    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(cache.get_or_build, base, "one", blocked)
        assert entered.wait(5)
        second = pool.submit(cache.get_or_build, base, "one", blocked)
        unrelated = pool.submit(cache.get_or_build, base, "two", lambda: {"value": ["independent"]})
        assert unrelated.result(timeout=2)["value"] == ["independent"]
        release.set()
        assert first.result(timeout=5) == second.result(timeout=5)
        assert first.result() is not second.result()
    assert len(builds) == 1


def test_cache_enforces_lru_entries_and_actual_object_memory_budget(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    cache = _StaticInventoryCache(max_entries=4)
    for index in range(5):
        cache.get_or_build(tmp_path, str(index), lambda: {"entity": {"many": [str(n) for n in range(80)]}})
    assert len(cache._entries) == 4
    assert all(key[1] != "0" for key in cache._entries)
    oversized = {"entity": ["value" + str(n) for n in range(500)]}
    assert _object_size(oversized) > len(json.dumps(oversized))
    small = _StaticInventoryCache(max_bytes=1024)
    small.get_or_build(tmp_path, "oversized", lambda: oversized)
    assert not small._entries and small._bytes == 0
    budgeted = _StaticInventoryCache(max_bytes=4096)
    for index in range(4):
        budgeted.get_or_build(tmp_path, str(index), lambda: {"values": [f"row-{n}-" + "x" * 20 for n in range(20)]})
    assert 0 < len(budgeted._entries) < 4
    assert budgeted._bytes + budgeted._base_bytes <= budgeted.max_bytes


def test_inventory_source_root_is_validated_before_resolving_a_junction(tmp_path, monkeypatch):
    from backend.services.context_inventory import cache_sources
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr(cache_sources, "_is_redirect", lambda path: path == source)
    with pytest.raises(ValueError, match="redirected"):
        _validated_source_root(source)
    _static_source_signature(tmp_path)


def test_rebuild_force_recomputes_static_inventory_without_changing_public_signature(inventory_env):
    from backend.modules.context_hub import generations
    from backend.modules.context_hub.state import CONTEXT_HUB_STATE
    base, info, _cache, builds = inventory_env
    previous = CONTEXT_HUB_STATE.runtime_config
    helper = runpy.run_path(str(Path(__file__).with_name("test_context_hub.py")))
    helper["_write_bundle"](base, version="9.9.9")
    try:
        first = generations.rebuild_context("000002", base_dir=base, info_root=info, surface="checkout")
        repeated = generations.rebuild_context("000002", base_dir=base, info_root=info, surface="checkout")
        forced = generations.rebuild_context("000002", base_dir=base, info_root=info, surface="checkout", force=True)
        assert first["success"] and repeated["idempotent"] and forced["success"]
        assert len(builds) == 2
        assert forced["generation_id"] != first["generation_id"]
    finally:
        with CONTEXT_HUB_STATE.config_guard:
            CONTEXT_HUB_STATE.runtime_config = previous
