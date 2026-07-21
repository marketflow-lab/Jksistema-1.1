from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from backend.services.context_hub_inventory import (
    ENTITY_KEYS,
    build_context_bundle_manifest,
    build_context_inventory,
    compare_context_inventory_openapi,
    render_context_entity_markdown,
    render_context_inventory_markdown,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _sku_payload(*, sku: str = "001", forbidden: bool = False) -> dict:
    payload = {
        "schema_version": 2,
        "sku": sku,
        "nome_produto": "Interruptor térmico da ventoinha Honda",
        "o_que_é": "Interruptor acionado pela temperatura do líquido.",
        "para_que_serve": {
            "status": "documentado",
            "itens": ["Aciona a ventoinha."],
            "observação": "",
        },
        "características_técnicas": {
            "status": "documentado",
            "itens": ["Rosca M16 x 1,5."],
            "observação": "",
        },
        "medidas_do_produto": {"status": "", "itens": [], "observação": ""},
        "modo_de_funcionamento": {
            "status": "documentado",
            "itens": ["Fecha o circuito na temperatura especificada."],
            "observação": "",
        },
        "instalação": {"status": "", "itens": [], "observação": ""},
        "oem": {"status": "documentado", "códigos": ["37760-MT2-003"], "observação": ""},
        "aplicação": {
            "tipo": "motocicleta",
            "veículos_compatíveis": {
                "status": "documentado",
                "itens": [{"marca": "Honda", "modelo": "CB 500", "anos": ["1997 a 2004"]}],
                "observação": "",
            },
            "equipamentos_ou_aplicações_compatíveis": {"status": "", "itens": [], "observação": ""},
        },
        "revisão": {
            "status": "revisado",
            "nome_em_português_do_brasil": True,
            "medidas_de_embalagem_removidas": True,
            "anos_exibidos_em_cada_modelo": True,
            "pendências": [],
        },
        "atualizado_em": "2026-07-17T00:00:00+00:00",
    }
    if forbidden:
        payload["access_token"] = "valor-que-nao-pode-ser-indexado"
    return payload


def _build_fixture(root: Path, *, forbidden_sku: bool = False) -> tuple[Path, Path]:
    base = root / "app"
    info = base / "info"
    _write(base / "package.json", '{"version":"9.9.9"}\n')
    _write(
        base / "backend" / "schemas" / "catalog.py",
        "from pydantic import BaseModel\n\n"
        "class ProductResponse(BaseModel):\n"
        "    sku: str\n"
        "    name: str\n",
    )
    _write(
        base / "backend" / "routers" / "catalog.py",
        "from fastapi import APIRouter\n"
        "from backend.schemas.catalog import ProductResponse\n"
        "class LegacyRouteSpec:\n"
        "    def __init__(self, method, path, endpoint_name): pass\n"
        "LEGACY_ROUTES = (LegacyRouteSpec('GET', '/legacy', 'legacy_lookup'),)\n"
        "router = APIRouter(prefix='/api/catalog')\n\n"
        "@router.get('/{sku}', response_model=ProductResponse)\n"
        "def get_product(sku: str):\n"
        "    return {}\n",
    )
    _write(
        base / "backend" / "services" / "catalog" / "worker.py",
        "def list_products(client_id: str):\n"
        "    \"\"\"Lista produtos sem efeitos colaterais.\"\"\"\n"
        "    sql = '''CREATE TABLE IF NOT EXISTS product_cache (sku TEXT, name TEXT)'''\n"
        "    return []\n\n"
        "def get_product(sku: str):\n"
        "    return {}\n\n"
        "def legacy_lookup():\n"
        "    return {}\n",
    )
    _write(base / "backend" / "lifecycle.py", "async def start_jobs():\n    return None\n")
    _write(
        base / "static" / "catalog.html",
        "<html><head><title>Catálogo</title></head><body>"
        "<script src='/static/catalog.js'></script></body></html>",
    )
    _write(
        base / "static" / "catalog.js",
        "fetch(`/api/catalog/${sku}`); fetch('/api/catalog/001');",
    )
    _write(base / "catalog.html", "<title>Espelho legado que deve ser ignorado</title>")
    _write(
        base / "tests" / "test_catalog.py",
        "def test_catalog_contract():\n    assert True\n",
    )
    _write(base / "docs" / "old-guide.md", "# Guia antigo\n\nNão é fonte canônica.\n")
    _write(base / "electron_app" / "main.js", "module.exports = {};\n")
    _write(base / "android_app" / "build.gradle", "plugins {}\n")

    sku_dir = info / "000002" / "SKU"
    _write(
        sku_dir / "001.json",
        json.dumps(_sku_payload(forbidden=forbidden_sku), ensure_ascii=False, indent=2),
    )
    _write(
        sku_dir / "_INDICE.json",
        json.dumps(
            {
                "schema_version": 2,
                "total_skus": 1,
                "arquivos_revisados": 1,
                "cobertura": {"total": 1, "com_pendências": 0},
                "skus_com_pendências": [],
                "skus_excluídos": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    _write(
        info / "codex_console" / "capabilities.json",
        json.dumps(
            {
                "client_id": "000002",
                "capabilities": [
                    {
                        "id": "get_catalog",
                        "module": "catalog",
                        "category": "consultar",
                        "title": "Consultar catálogo",
                        "route_path": "/api/catalog/{sku}",
                        "method": "GET",
                        "read_only": True,
                        "mutating": False,
                    }
                ],
            },
            ensure_ascii=False,
        ),
    )
    return base, info


def test_inventory_is_pure_complete_and_deterministic(tmp_path: Path) -> None:
    base, info = _build_fixture(tmp_path)

    first = build_context_inventory(str(base), str(info), "000002", "checkout")
    second = build_context_inventory(str(base), str(info), "000002", "checkout")

    assert first == second
    assert first["source_version"] == "9.9.9"
    assert first["stats"]["canonical_screens"] == 1
    assert first["stats"]["sku"]["valid"] == 1
    assert first["stats"]["sku"]["invalid"] == 0
    assert not [item for item in first["findings"] if item["severity"] == "blocker"]

    entities = {entity["id"]: entity for entity in first["entities"]}
    expected_ids = {
        "jk:api:GET:/api/catalog/{sku}",
        "jk:api:GET:/api/catalog/legacy",
        "jk:schema:catalog.ProductResponse",
        "jk:service:catalog.worker.list_products",
        "jk:service:lifecycle.start_jobs",
        "jk:screen:catalog",
        "jk:data:product_cache",
        "jk:test:tests/test_catalog.py#test_catalog_contract",
        "jk:integration:electron",
        "jk:integration:android",
        "jk:capability:get_catalog",
        "jk:sku:001",
        "jk:sku-map:catalog",
        "jk:domain:cadastro",
    }
    assert expected_ids.issubset(entities)
    for entity in first["entities"]:
        assert set(ENTITY_KEYS).issubset(entity)
        assert len(entity["source_hash"]) == 64
        assert not any(Path(ref).is_absolute() for ref in entity["source_refs"])
    assert entities["jk:document:docs-old-guide.md"]["truth_class"] == "legacy_unverified"
    assert entities["jk:document:docs-old-guide.md"]["metadata"]["content_ingested"] is False
    assert "Não é fonte" not in entities["jk:document:docs-old-guide.md"]["content"]

    calls = [entity for entity in first["entities"] if entity["kind"] == "frontend_api_call"]
    assert {item["metadata"]["classification"] for item in calls} == {"alias", "dynamic"}
    assert {
        row["target_id"] for row in entities["jk:api:GET:/api/catalog/{sku}"]["relationships"]
    } >= {"jk:service:catalog.worker.get_product"}
    assert {
        row["target_id"] for row in entities["jk:service:catalog.worker.list_products"]["relationships"]
    } >= {"jk:data:product_cache"}


def test_installed_inventory_uses_runtime_manifest_when_package_json_is_absent(tmp_path: Path) -> None:
    base, info = _build_fixture(tmp_path)
    (base / "package.json").unlink()
    _write(base / "runtime-manifest.json", '{"version":"9.9.10"}\n')

    inventory = build_context_inventory(str(base), str(info), "000002", "installed")

    assert inventory["source_version"] == "9.9.10"


def test_inventory_rejects_unsafe_source_versions(tmp_path: Path) -> None:
    base, info = _build_fixture(tmp_path)
    _write(base / "package.json", '{"version":"../../segredo"}\n')
    _write(base / "runtime-manifest.json", '{"version":"versao com espaco"}\n')

    inventory = build_context_inventory(str(base), str(info), "000002", "installed")

    assert inventory["source_version"] == "unknown"


def test_sku_unexpected_schema_fails_closed_without_leaking_value(tmp_path: Path) -> None:
    base, info = _build_fixture(tmp_path, forbidden_sku=True)

    inventory = build_context_inventory(str(base), str(info), "000002", "checkout")

    blockers = [item for item in inventory["findings"] if item["severity"] == "blocker"]
    assert blockers
    assert inventory["stats"]["sku"]["valid"] == 0
    assert not any(entity["id"] == "jk:sku:001" for entity in inventory["entities"])
    serialized = json.dumps(inventory, ensure_ascii=False)
    assert "valor-que-nao-pode-ser-indexado" not in serialized


def test_markdown_rendering_is_managed_and_aggregates_sku(tmp_path: Path) -> None:
    base, info = _build_fixture(tmp_path)
    inventory = build_context_inventory(str(base), str(info), "000002", "checkout")

    rendered = render_context_inventory_markdown(inventory, generated_at="2026-07-17T12:00:00Z")

    assert "70_Gerado/Produtos/Catalogo-SKU.md" in rendered
    assert not any(path.endswith("/001.md") for path in rendered)
    assert sum(1 for path in rendered if path.startswith("70_Gerado/Produtos/")) == 3
    catalog = rendered["70_Gerado/Produtos/Catalogo-SKU.md"]
    assert 'managed: true' in catalog
    assert 'status: "published"' in catalog
    assert 'ai_usage: "allowed"' in catalog
    assert 'required_permissions: ["admin_full"]' in catalog
    assert 'source_version: "9.9.9"' in catalog


def test_markdown_graph_uses_resolvable_obsidian_wikilinks(tmp_path: Path) -> None:
    base, info = _build_fixture(tmp_path)
    inventory = build_context_inventory(str(base), str(info), "000002", "checkout")

    rendered = render_context_inventory_markdown(inventory, generated_at="2026-07-17T12:00:00Z")
    targets = {
        target
        for content in rendered.values()
        for target in re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", content)
    }
    node_names = {path.removesuffix(".md") for path in rendered}
    adjacency = {node: set() for node in node_names}
    for path, content in rendered.items():
        source = path.removesuffix(".md")
        for target in re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", content):
            if target in node_names and target != source:
                adjacency[source].add(target)
                adjacency[target].add(source)

    assert rendered
    assert all("[[" in content for content in rendered.values())
    assert "70_Gerado/Mapas/Inventario-do-Programa" in targets
    assert {f"{target}.md" for target in targets}.issubset(rendered)
    assert all(neighbors for neighbors in adjacency.values())
    visited: set[str] = set()
    pending = [next(iter(node_names))]
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        pending.extend(adjacency[current] - visited)
    assert visited == node_names
    assert max(len(neighbors) for neighbors in adjacency.values()) <= 20


def test_markdown_graph_routes_leaf_notes_through_semantic_parents(tmp_path: Path) -> None:
    base, info = _build_fixture(tmp_path)
    inventory = build_context_inventory(str(base), str(info), "000002", "checkout")

    rendered = render_context_inventory_markdown(inventory, generated_at="2026-07-17T12:00:00Z")
    screen = rendered["70_Gerado/Mapas/Telas/catalog.md"]
    test_page = rendered["70_Gerado/Operacao/Testes/Parte-001.md"]
    api_index = rendered["70_Gerado/Contratos/APIs.md"]
    api_domain = rendered["70_Gerado/Contratos/APIs/cadastro.md"]
    inventory_index = rendered["70_Gerado/Mapas/Inventario-do-Programa.md"]
    areas_index = rendered["70_Gerado/Mapas/Areas-do-Programa.md"]

    assert "[[70_Gerado/Dominios/cadastro|Dominio cadastro]]" in screen
    assert "[[70_Gerado/Mapas/Inventario-do-Programa|Inventario do Programa]]" not in screen
    assert "[[70_Gerado/Contratos/APIs/cadastro|APIs do dominio cadastro]]" in screen
    assert "[[70_Gerado/Contratos/APIs|Mapa de APIs]]" not in screen
    assert "[[70_Gerado/Contratos/APIs/cadastro|APIs do dominio cadastro]]" in api_index
    assert "[[70_Gerado/Contratos/APIs|Mapa de APIs]]" in api_domain
    assert "[[70_Gerado/Mapas/Areas-do-Programa|Areas do Programa]]" in inventory_index
    assert "[[70_Gerado/Contratos/APIs|Mapa de APIs]]" not in inventory_index
    assert "[[70_Gerado/Contratos/APIs|Mapa de APIs]]" in areas_index
    assert "[[70_Gerado/Mapas/Areas-do-Programa|Areas do Programa]]" in api_index
    assert "[[70_Gerado/Operacao/Testes|Mapa de Testes]]" in test_page
    assert "[[70_Gerado/Mapas/Inventario-do-Programa|Inventario do Programa]]" not in test_page


def test_api_domain_maps_partition_every_api_exactly_once(tmp_path: Path) -> None:
    base, info = _build_fixture(tmp_path)
    inventory = build_context_inventory(str(base), str(info), "000002", "checkout")

    rendered = render_context_inventory_markdown(inventory, generated_at="2026-07-17T12:00:00Z")
    api_maps = {
        path: content
        for path, content in rendered.items()
        if path.startswith("70_Gerado/Contratos/APIs/")
    }
    expected_ids = {
        str(row["id"]) for row in inventory["entities"] if row.get("kind") == "api"
    }
    occurrences = {
        api_id: sum(content.count(f"- `{api_id}` - ") for content in api_maps.values())
        for api_id in expected_ids
    }

    assert api_maps
    assert all(re.search(r"^- `jk:api:", content, flags=re.MULTILINE) for content in api_maps.values())
    assert set(occurrences) == expected_ids
    assert all(count == 1 for count in occurrences.values())
    assert not any(
        "[[70_Gerado/Contratos/APIs|Mapa de APIs]]" in content
        for path, content in rendered.items()
        if path.startswith("70_Gerado/Dominios/") or path.startswith("70_Gerado/Mapas/Telas/")
    )


def test_markdown_graph_does_not_link_to_an_empty_group_map(tmp_path: Path) -> None:
    base, info = _build_fixture(tmp_path)
    inventory = build_context_inventory(str(base), str(info), "000002", "checkout")
    inventory["entities"] = [
        row
        for row in inventory["entities"]
        if row.get("kind") != "capability"
        and not str(row.get("id") or "").startswith("jk:sku-map:")
    ]

    rendered = render_context_inventory_markdown(inventory, generated_at="2026-07-17T12:00:00Z")
    index = rendered["70_Gerado/Mapas/Inventario-do-Programa.md"]
    areas = rendered["70_Gerado/Mapas/Areas-do-Programa.md"]

    assert "70_Gerado/Operacao/Capacidades-Codex.md" not in rendered
    assert "[[70_Gerado/Operacao/Capacidades-Codex|Capacidades Codex]]" not in index
    assert "[[70_Gerado/Operacao/Capacidades-Codex|Capacidades Codex]]" not in areas
    assert not any(path.startswith("70_Gerado/Produtos/") for path in rendered)
    assert "[[70_Gerado/Produtos/" not in index
    assert "[[70_Gerado/Produtos/" not in areas


def test_large_test_map_is_paginated_without_truncating_entities(tmp_path: Path) -> None:
    base, info = _build_fixture(tmp_path)
    inventory = build_context_inventory(str(base), str(info), "000002", "checkout")
    template = next(row for row in inventory["entities"] if row["kind"] == "test")
    expected_ids: list[str] = []
    for index in range(1_200):
        test_id = f"jk:test:tests/test_bulk_{index:04d}.py#test_contract_{index:04d}"
        expected_ids.append(test_id)
        inventory["entities"].append(
            {
                **template,
                "id": test_id,
                "title": f"Contrato extenso {index:04d} " + ("validacao " * 18),
                "source_refs": [f"tests/test_bulk_{index:04d}.py"],
                "source_hash": hashlib.sha256(test_id.encode("utf-8")).hexdigest(),
            }
        )

    rendered = render_context_inventory_markdown(inventory, generated_at="2026-07-17T12:00:00Z")
    test_pages = {
        path: content
        for path, content in rendered.items()
        if path.startswith("70_Gerado/Operacao/Testes/Parte-")
    }
    combined = "\n".join(test_pages.values())

    assert len(test_pages) > 1
    assert all("[[70_Gerado/Operacao/Testes|Mapa de Testes]]" in content for content in test_pages.values())
    assert all(combined.count(f"- `{test_id}` - ") == 1 for test_id in expected_ids)
    assert all(not content.rstrip().endswith("`") for content in test_pages.values())


def test_entity_renderer_rejects_incomplete_entity() -> None:
    with pytest.raises(ValueError, match="entidade incompleta"):
        render_context_entity_markdown({"id": "jk:invalid"})


def test_bundle_manifest_is_exact_and_deterministic(tmp_path: Path) -> None:
    knowledge = tmp_path / "docs" / "knowledge"
    _write(knowledge / "README.md", "# Contexto\n")
    _write(knowledge / "templates" / "note.md", "# Modelo\n")
    _write(knowledge / "context-bundle-manifest.json", "arquivo antigo ignorado")

    first = build_context_bundle_manifest(knowledge, "1.0.99")
    second = build_context_bundle_manifest(knowledge, "1.0.99")

    assert first == second
    assert set(first) == {"schema_version", "source_version", "files"}
    assert first["schema_version"] == 1
    assert first["source_version"] == "1.0.99"
    assert [item["path"] for item in first["files"]] == [
        "docs/knowledge/README.md",
        "docs/knowledge/templates/note.md",
    ]
    for item in first["files"]:
        local = tmp_path / item["path"]
        assert item["size"] == local.stat().st_size
        assert item["sha256"] == hashlib.sha256(local.read_bytes()).hexdigest()


def test_openapi_comparison_is_pure_and_reports_missing_route(tmp_path: Path) -> None:
    base, info = _build_fixture(tmp_path)
    inventory = build_context_inventory(str(base), str(info), "000002", "checkout")
    openapi = {
        "paths": {
            "/api/catalog/{sku}": {"get": {"operationId": "get_product"}},
            "/api/catalog/runtime-only": {"post": {"operationId": "runtime_only"}},
        }
    }

    comparison = compare_context_inventory_openapi(inventory, openapi)

    assert comparison["openapi_routes"] == 2
    assert comparison["matched_routes"] == 1
    assert comparison["complete"] is False
    assert comparison["missing_from_inventory"] == [
        {"method": "POST", "path": "/api/catalog/runtime-only"}
    ]


def test_openapi_comparison_normalizes_starlette_path_converters(tmp_path: Path) -> None:
    base, info = _build_fixture(tmp_path)
    _write(
        base / "backend" / "routers" / "files.py",
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/api/files/{filename:path}')\n"
        "def get_file(filename: str): return {}\n",
    )
    inventory = build_context_inventory(str(base), str(info), "000002", "checkout")
    openapi = {"paths": {"/api/files/{filename}": {"get": {}}}}

    comparison = compare_context_inventory_openapi(inventory, openapi)

    assert comparison["missing_from_inventory"] == []
    assert comparison["matched_routes"] == 1
    assert comparison["complete"] is True
