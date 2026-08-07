from __future__ import annotations

import hashlib
import inspect
import json
import runpy
from pathlib import Path

from backend.services import context_hub_inventory as inventory


PUBLIC_EXPORTS = [
    "ENTITY_KEYS",
    "INVENTORY_SCHEMA_VERSION",
    "build_context_bundle_manifest",
    "build_context_inventory",
    "compare_context_inventory_openapi",
    "render_context_entity_markdown",
    "render_context_inventory_markdown",
]

PUBLIC_SIGNATURES = {
    "build_context_bundle_manifest": "(knowledge_dir: 'str | Path', source_version: 'str') -> 'dict[str, Any]'",
    "build_context_inventory": "(base_dir: 'str', info_root: 'str', client_id: 'str', surface: 'str') -> 'dict[str, Any]'",
    "compare_context_inventory_openapi": "(inventory: 'Mapping[str, Any]', openapi_schema: 'Mapping[str, Any]') -> 'dict[str, Any]'",
    "render_context_entity_markdown": "(entity: 'Mapping[str, Any]', *, source_version: 'str' = 'unknown', generated_at: 'str' = '1970-01-01T00:00:00Z') -> 'str'",
    "render_context_inventory_markdown": "(inventory: 'Mapping[str, Any]', *, generated_at: 'str' = '1970-01-01T00:00:00Z') -> 'dict[str, str]'",
}


def test_context_inventory_public_contract_snapshot() -> None:
    assert inventory.__all__ == PUBLIC_EXPORTS
    assert inventory.INVENTORY_SCHEMA_VERSION == 1
    assert inventory.ENTITY_KEYS == (
        "id",
        "kind",
        "domain",
        "title",
        "surface",
        "tenant_scope",
        "sensitivity",
        "truth_class",
        "source_refs",
        "source_hash",
        "relationships",
        "metadata",
        "content",
    )
    assert {
        name: str(inspect.signature(getattr(inventory, name)))
        for name in PUBLIC_SIGNATURES
    } == PUBLIC_SIGNATURES


def test_context_inventory_openapi_comparison_snapshot() -> None:
    result = inventory.compare_context_inventory_openapi(
        {
            "entities": [
                {
                    "kind": "api",
                    "metadata": {"method": "GET", "path": "/api/items/{item_id:path}"},
                }
            ]
        },
        {
            "paths": {
                "/api/items/{item_id}": {"get": {}},
                "/api/items": {"post": {}},
            }
        },
    )

    assert result == {
        "inventory_routes": 1,
        "openapi_routes": 2,
        "matched_routes": 1,
        "missing_from_inventory": [{"method": "POST", "path": "/api/items"}],
        "static_only": [],
        "complete": False,
    }


def test_context_entity_markdown_snapshot() -> None:
    rendered = inventory.render_context_entity_markdown(
        {
            "id": "jk:test:contract",
            "kind": "test",
            "domain": "ia",
            "title": "Contrato",
            "surface": "checkout",
            "tenant_scope": "system",
            "sensitivity": "internal",
            "truth_class": "generated",
            "source_refs": ["tests/contract.py"],
            "source_hash": "a" * 64,
            "relationships": [
                {"type": "covers", "target_id": "jk:api:get:/api/test"}
            ],
            "metadata": {"z": 2, "a": 1},
            "content": "Linha segura.",
        },
        source_version="1.2.3",
        generated_at="2026-08-05T12:00:00Z",
    )

    assert hashlib.sha256(rendered.encode("utf-8")).hexdigest() == (
        "6f0400de067aa7cfbfae4bb3f7fbfd5f0f48bda2c7d4718c837d442359326dcc"
    )


def test_context_bundle_manifest_snapshot(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "a.txt").write_bytes(b"A")
    (tmp_path / "nested" / "b.md").write_bytes(b"B")

    assert inventory.build_context_bundle_manifest(tmp_path, "1.2.3") == {
        "schema_version": 1,
        "source_version": "1.2.3",
        "files": [
            {
                "path": "docs/knowledge/a.txt",
                "sha256": hashlib.sha256(b"A").hexdigest(),
                "size": 1,
            },
            {
                "path": "docs/knowledge/nested/b.md",
                "sha256": hashlib.sha256(b"B").hexdigest(),
                "size": 1,
            },
        ],
    }


def test_full_inventory_and_markdown_fixture_hashes(tmp_path: Path) -> None:
    fixture_module = runpy.run_path(
        str(Path(__file__).with_name("test_context_hub_inventory.py"))
    )
    base, info = fixture_module["_build_fixture"](tmp_path)
    result = inventory.build_context_inventory(
        str(base), str(info), "000002", "checkout"
    )
    markdown = inventory.render_context_inventory_markdown(
        result,
        generated_at="2026-07-17T12:00:00Z",
    )

    canonical = lambda value: json.dumps(  # noqa: E731
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert len(result["entities"]) == 239
    assert len(markdown) == 233
    assert hashlib.sha256(canonical(result).encode("utf-8")).hexdigest() == (
        "26e7084ffe832faa67137c1a298a4fee85783ff7929b50dbfd0c7fb87eacb901"
    )
    assert hashlib.sha256(canonical(markdown).encode("utf-8")).hexdigest() == (
        "672caadf900c94572964e61a078ba72ba743f1d33065bbeebbf1103679156048"
    )
