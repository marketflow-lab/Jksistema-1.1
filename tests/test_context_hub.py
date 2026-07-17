from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.routers.context_hub import create_context_hub_router
from backend.services import context_hub, context_hub_endpoints


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _write_bundle(base: Path, *, version: str = "1.0.99", content: str = "# Operacao\n\nConhecimento tecnico seguro.\n") -> Path:
    document = base / "docs" / "knowledge" / "operacao.md"
    _write(document, content)
    data = document.read_bytes()
    manifest = {
        "files": [
            {
                "path": "docs/knowledge/operacao.md",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
        ],
        "schema_version": 1,
        "source_version": version,
    }
    _write(base / "context-bundle-manifest.json", json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    return document


def _entity(
    entity_id: str,
    *,
    title: str = "Modulo seguro",
    content: str = "Descricao operacional segura.",
    kind: str = "domain",
    domain: str = "sistema",
    source_ref: str = "backend/services/example.py",
) -> dict:
    return {
        "id": entity_id,
        "kind": kind,
        "domain": domain,
        "title": title,
        "surface": "development",
        "tenant_scope": "tenant",
        "sensitivity": "internal",
        "truth_class": "source",
        "source_refs": [source_ref],
        "source_hash": _sha(f"{entity_id}:{title}:{content}"),
        "relationships": [],
        "metadata": {},
        "content": content,
    }


class FakeInventoryAdapter:
    def __init__(self, entities: list[dict], *, version: str = "1.0.99", sku_map_only: bool = False):
        self.entities = entities
        self.version = version
        self.sku_map_only = sku_map_only

    def build_context_inventory(self, base_dir, info_root, client_id, surface):
        return {
            "schema_version": 1,
            "source_version": self.version,
            "surface": surface,
            "client_id": client_id,
            "entities": list(self.entities),
            "findings": [],
            "stats": {"entities": len(self.entities)},
        }

    def render_context_inventory_markdown(self, inventory):
        if self.sku_map_only:
            return {
                "70_Gerado/Produtos/Catalogo-SKU.md": (
                    "---\nid: jk:sku-map:catalog\ntype: map\n---\n\n"
                    "# Catalogo SKU\n\nMapa agregado do catalogo.\n"
                )
            }
        rendered = {}
        for entity in self.entities:
            if entity["kind"] == "sku":
                continue
            slug = hashlib.sha256(entity["id"].encode()).hexdigest()[:16]
            rendered[f"70_Gerado/Dominios/{slug}.md"] = (
                f"---\nid: {entity['id']}\ntype: {entity['kind']}\n---\n\n"
                f"# {entity['title']}\n\n{entity['content']}\n"
            )
        return rendered


@pytest.fixture
def hub_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    base = tmp_path / "app"
    info = tmp_path / "info"
    base.mkdir()
    info.mkdir()
    _write_bundle(base)
    adapter = FakeInventoryAdapter([_entity("jk:domain:test")])
    monkeypatch.setattr(context_hub, "_load_inventory_adapter", lambda: adapter)
    context_hub.configure_context_hub(base_dir=base, info_root=info, surface="development")
    yield base, info, adapter
    context_hub.stop_all_context_hub_watchers()


def test_bootstrap_creates_persistent_vault_without_workspace(hub_env) -> None:
    _base, info, _adapter = hub_env
    result = context_hub.bootstrap_context_hub("000002")

    vault = info / "000002" / "ContextVault"
    assert result["client_id"] == "000002"
    for relative in context_hub.VAULT_DIRECTORIES:
        assert (vault / relative).is_dir()
    assert json.loads((vault / ".obsidian" / "community-plugins.json").read_text(encoding="utf-8")) == []
    assert not (vault / ".obsidian" / "workspace.json").exists()
    assert (info / "000002" / "context_hub" / "context_hub.db").is_file()

    with pytest.raises(context_hub.ContextHubValidationError):
        context_hub.bootstrap_context_hub("../outro")


def test_bootstrap_preserves_existing_obsidian_configuration(hub_env) -> None:
    _base, info, _adapter = hub_env
    obsidian = info / "000002" / "ContextVault" / ".obsidian"
    _write(obsidian / "app.json", '{"userSetting":true}\n')
    _write(obsidian / "workspace.json", '{"layout":"user"}\n')

    context_hub.bootstrap_context_hub("000002")

    assert json.loads((obsidian / "app.json").read_text(encoding="utf-8")) == {"userSetting": True}
    assert (obsidian / "workspace.json").is_file()
    assert not (obsidian / "community-plugins.json").exists()


def test_external_symlink_inside_tenant_is_rejected(hub_env, tmp_path: Path) -> None:
    _base, info, _adapter = hub_env
    tenant = info / "000002"
    tenant.mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    try:
        (tenant / "ContextVault").symlink_to(external, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink indisponivel neste Windows: {error}")

    with pytest.raises(context_hub.ContextHubValidationError):
        context_hub.bootstrap_context_hub("000002")


def test_configured_info_root_cannot_be_a_junction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = tmp_path / "app"
    info = tmp_path / "linked-info"
    base.mkdir()
    info.mkdir()
    monkeypatch.setattr(
        context_hub,
        "_is_link_or_junction",
        lambda path: Path(path).absolute() == info.absolute(),
    )

    with pytest.raises(context_hub.ContextHubValidationError, match="raiz info"):
        context_hub.configure_context_hub(
            base_dir=base,
            info_root=info,
            surface="development",
        )


def test_dlp_blocks_categories_without_match_and_allows_hashes_and_oem() -> None:
    sha = "b3d19e4c864d4c0a1179e5a29af92f5bb76b7e130cb98f18df6a8c80f7607894"
    allowed = f"source_hash: {sha}\nOEM 12345678901\nSKU 11987654321"
    assert context_hub.scan_dlp(allowed) == []

    blocked = context_hub.scan_dlp(
        "Contato: pessoa@example.com\nTelefone: (31) 99999-1234\n"
        "Rua das Flores, 123 - CEP 12345-678\naccess_token: abcdefghijklmnop",
        source_ref="nota.md",
    )
    assert {row["category"] for row in blocked} == {"address", "credential", "email", "phone"}
    serialized = json.dumps(blocked)
    assert "pessoa@example.com" not in serialized
    assert "abcdefghijklmnop" not in serialized
    assert "99999" not in serialized
    assert "Flores" not in serialized


def test_file_lock_only_removes_its_own_token(hub_env) -> None:
    _base, info, _adapter = hub_env
    context_hub.bootstrap_context_hub("000002")
    paths = context_hub._tenant_paths("000002", info_root=info)

    with context_hub._exclusive_file_lock(paths):
        replacement = {"pid": os.getpid(), "token": "replacement", "created_at": "future"}
        paths.lock_path.write_text(json.dumps(replacement), encoding="utf-8")

    assert json.loads(paths.lock_path.read_text(encoding="utf-8"))["token"] == "replacement"
    paths.lock_path.unlink()


def test_rebuild_is_idempotent_publishes_and_searches_active_generation(hub_env) -> None:
    _base, info, _adapter = hub_env

    first = context_hub.rebuild_context("000002")
    second = context_hub.rebuild_context("000002")

    assert first["success"] is True
    assert first["status"] == "active"
    assert second["generation_id"] == first["generation_id"]
    assert second["idempotent"] is True
    assert second["source_hash"] == first["source_hash"]
    search = context_hub.search_context("000002", "Descricao operacional", limit=99)
    assert search["generation_id"] if "generation_id" in search else search["generation"]
    assert search["count"] >= 1
    assert len(search["results"]) <= 12
    hit = search["results"][0]
    assert hit["generation_id"] == first["generation_id"]
    assert hit["source_version"] == "1.0.99"
    assert len(hit["source_hash"]) == 64

    database = info / "000002" / "context_hub" / "context_hub.db"
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    connection.close()
    assert {
        "context_hub_generations",
        "context_hub_documents",
        "context_hub_chunks",
        "context_hub_active_generation",
        "context_hub_settings",
    } <= tables
    moved_database = database.with_suffix(".move-check")
    os.replace(database, moved_database)
    os.replace(moved_database, database)


def test_non_blocking_warnings_do_not_duplicate_an_unchanged_generation(
    hub_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _base, _info, adapter = hub_env
    original_build = adapter.build_context_inventory

    def build_with_warning(*args, **kwargs):
        inventory = original_build(*args, **kwargs)
        inventory["findings"] = [
            {
                "code": "frontend_dynamic_reference",
                "severity": "warning",
                "blocking": False,
            }
        ]
        return inventory

    monkeypatch.setattr(adapter, "build_context_inventory", build_with_warning)

    first = context_hub.rebuild_context("000002")
    second = context_hub.rebuild_context("000002")

    assert second["generation_id"] == first["generation_id"]
    assert second["source_hash"] == first["source_hash"]
    assert second["idempotent"] is True


def test_status_reports_document_diff_for_a_ready_generation(hub_env) -> None:
    _base, _info, adapter = hub_env
    active = context_hub.rebuild_context("000002")
    context_hub.update_settings("000002", auto_publish_enabled=False)
    adapter.entities = [
        _entity(
            "jk:domain:test",
            content="Descricao operacional alterada com seguranca.",
        ),
        _entity(
            "jk:domain:new",
            content="Novo dominio operacional seguro.",
        ),
    ]

    ready = context_hub.rebuild_context("000002")
    status = context_hub.get_status("000002")

    assert active["status"] == "active"
    assert ready["status"] == "ready"
    assert status["diff"]["has_changes"] is True
    assert status["diff"]["added"] == 1
    assert status["diff"]["updated"] == 1
    assert status["diff"]["removed"] == 0


def test_single_sku_change_reuses_unrelated_documents(hub_env) -> None:
    _base, _info, adapter = hub_env
    adapter.sku_map_only = True
    adapter.entities = [
        _entity("jk:sku:001", kind="sku", domain="cadastro", content="Peca Honda segura."),
        _entity("jk:sku:002", kind="sku", domain="cadastro", content="Peca Yamaha segura."),
    ]
    context_hub.rebuild_context("000002")
    context_hub.update_settings("000002", auto_publish_enabled=False)
    adapter.entities[0] = _entity(
        "jk:sku:001",
        kind="sku",
        domain="cadastro",
        content="Peca Honda segura com aplicacao atualizada.",
    )

    ready = context_hub.rebuild_context("000002")
    status = context_hub.get_status("000002")
    details = context_hub.get_generation("000002", ready["generation_id"])["generation"]

    assert status["diff"]["added"] == 0
    assert status["diff"]["removed"] == 0
    assert status["diff"]["updated"] == 1
    assert details["stats"]["reused_documents"] >= 2


def test_dlp_failure_never_persists_secret_and_preserves_active_generation(hub_env) -> None:
    _base, info, adapter = hub_env
    active = context_hub.rebuild_context("000002")
    managed_file = next((info / "000002" / "ContextVault" / "70_Gerado").rglob("*.md"))
    old_content = managed_file.read_text(encoding="utf-8")

    adapter.entities = [
        _entity(
            "jk:domain:test",
            title="Alteracao bloqueada",
            content="Contato privado: pessoa.sensivel@example.com",
        )
    ]
    failed = context_hub.rebuild_context("000002")

    assert failed["status"] == "failed"
    assert failed["success"] is False
    assert any(row["category"] == "email" for row in failed["findings"])
    assert managed_file.read_text(encoding="utf-8") == old_content
    assert context_hub.get_status("000002")["active_generation"]["generation_id"] == active["generation_id"]
    serialized = json.dumps(failed)
    assert "pessoa.sensivel" not in serialized
    internal = info / "000002" / "context_hub"
    for database_part in internal.glob("context_hub.db*"):
        assert b"pessoa.sensivel@example.com" not in database_part.read_bytes()


def test_manual_publish_rollback_cas_and_failed_swap_restore(hub_env, monkeypatch: pytest.MonkeyPatch) -> None:
    _base, info, adapter = hub_env
    context_hub.update_settings("000002", auto_publish_enabled=False)
    first = context_hub.rebuild_context("000002")
    context_hub.publish_generation("000002", first["generation_id"])
    generated = next((info / "000002" / "ContextVault" / "70_Gerado").rglob("*.md"))
    first_text = generated.read_text(encoding="utf-8")

    adapter.entities = [_entity("jk:domain:test", title="Versao dois", content="Conteudo versao dois.")]
    second = context_hub.rebuild_context("000002")
    context_hub.publish_generation("000002", second["generation_id"])
    assert "Conteudo versao dois" in next((info / "000002" / "ContextVault" / "70_Gerado").rglob("*.md")).read_text(encoding="utf-8")
    rolled = context_hub.rollback_generation("000002", first["generation_id"])
    assert rolled["rollback"] is True
    assert next((info / "000002" / "ContextVault" / "70_Gerado").rglob("*.md")).read_text(encoding="utf-8") == first_text

    adapter.entities = [_entity("jk:domain:test", title="Versao tres", content="Conteudo tres.")]
    third = context_hub.rebuild_context("000002")
    adapter.entities = [_entity("jk:domain:test", title="Versao quatro", content="Conteudo quatro.")]
    fourth = context_hub.rebuild_context("000002")
    context_hub.publish_generation("000002", third["generation_id"])
    with pytest.raises(context_hub.ContextHubConflictError):
        context_hub.publish_generation("000002", fourth["generation_id"])

    adapter.entities = [_entity("jk:domain:test", title="Versao cinco", content="Conteudo cinco.")]
    fifth = context_hub.rebuild_context("000002")
    before_failure = next((info / "000002" / "ContextVault" / "70_Gerado").rglob("*.md")).read_text(encoding="utf-8")
    real_replace = context_hub.os.replace

    def fail_new_directory(source, target):
        if Path(source).name == f".context_hub_publish_{fifth['generation_id']}" and Path(target).name == "70_Gerado":
            raise OSError("simulated swap failure")
        return real_replace(source, target)

    monkeypatch.setattr(context_hub.os, "replace", fail_new_directory)
    with pytest.raises(OSError, match="simulated"):
        context_hub.publish_generation("000002", fifth["generation_id"])
    after_failure = next((info / "000002" / "ContextVault" / "70_Gerado").rglob("*.md")).read_text(encoding="utf-8")
    assert after_failure == before_failure
    assert context_hub.get_status("000002")["active_generation"]["generation_id"] == third["generation_id"]


def test_curated_note_requires_explicit_publication(hub_env) -> None:
    _base, info, _adapter = hub_env
    vault = info / "000002" / "ContextVault"
    context_hub.bootstrap_context_hub("000002")
    _write(vault / "80_Curadoria" / "Notas" / "rascunho.md", "# Rascunho\n\nNAO_INDEXAR_123.\n")
    body = "# Regra publicada\n\nCURADORIA_INDEXADA_456.\n"
    metadata = {
        "id": "jk:curated:regra",
        "type": "rule",
        "managed": False,
        "status": "published",
        "ai_usage": "allowed",
        "tenant_scope": "tenant:000002",
        "sensitivity": "internal",
        "truth_class": "human_reviewed",
        "required_permissions": ["full"],
        "surface": "development",
        "source_version": "1.0.99",
        "source_refs": [],
        "source_hash": _sha(body),
        "generated_at": "2026-07-17T00:00:00+00:00",
    }
    published = f"---\n{yaml.safe_dump(metadata, sort_keys=False).strip()}\n---\n\n{body}"
    _write(vault / "80_Curadoria" / "Regras" / "publicada.md", published)

    context_hub.rebuild_context("000002")

    assert context_hub.search_context("000002", "CURADORIA_INDEXADA_456")["count"] == 1
    assert context_hub.search_context("000002", "NAO_INDEXAR_123")["count"] == 0


def test_450_skus_are_searchable_without_individual_vault_notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = tmp_path / "app"
    info = tmp_path / "info"
    base.mkdir()
    info.mkdir()
    _write_bundle(base)
    entities = [
        _entity(
            f"jk:sku:{number:03d}",
            title=f"Produto TESTSKU{number:05d}",
            content=f"Produto TESTSKU{number:05d}; OEM 12345678901; aplicacao segura.",
            kind="sku",
            domain="cadastro",
            source_ref=f"info/000002/SKU/{number:03d}.json",
        )
        for number in range(450)
    ]
    adapter = FakeInventoryAdapter(entities, sku_map_only=True)
    monkeypatch.setattr(context_hub, "_load_inventory_adapter", lambda: adapter)
    context_hub.configure_context_hub(base_dir=base, info_root=info, surface="development")

    result = context_hub.rebuild_context("000002")

    assert result["status"] == "active"
    notes = list((info / "000002" / "ContextVault" / "70_Gerado").rglob("*.md"))
    assert len(notes) == 1
    with sqlite3.connect(info / "000002" / "context_hub" / "context_hub.db") as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM context_hub_documents WHERE generation_id=? AND kind='sku'",
            (result["generation_id"],),
        ).fetchone()[0]
    connection.close()
    assert count == 450
    search = context_hub.search_context("000002", "TESTSKU00449")
    assert search["count"] == 1
    assert search["results"][0]["doc_id"] == "jk:sku:449"


def test_bundle_is_fail_closed_and_ignores_unlisted_overlay(hub_env) -> None:
    base, info, _adapter = hub_env
    active = context_hub.rebuild_context("000002")
    assert active["success"] is True, active
    listed = base / "docs" / "knowledge" / "operacao.md"
    original = listed.read_text(encoding="utf-8")
    listed.write_text(original.replace("seguro", "adulter"), encoding="utf-8")

    tampered = context_hub.rebuild_context("000002")
    assert tampered["status"] == "failed"
    assert any(row["code"] in {"context_bundle_hash_mismatch", "context_bundle_size_mismatch"} for row in tampered["findings"])
    assert context_hub.get_status("000002")["active_generation"]["generation_id"] == active["generation_id"]

    _write_bundle(base)
    _write(base / "docs" / "knowledge" / "overlay-obsoleto.md", "# Overlay\n\nNAO_IMPORTAR_OVERLAY_999.\n")
    rebuilt = context_hub.rebuild_context("000002", force=True)
    assert rebuilt["status"] == "active"
    assert context_hub.search_context("000002", "NAO_IMPORTAR_OVERLAY_999")["count"] == 0
    assert context_hub.search_context("000002", "Conhecimento tecnico seguro")["count"] >= 1

    manifest = json.loads((base / "context-bundle-manifest.json").read_text(encoding="utf-8"))
    manifest["source_version"] = "0.0.1"
    _write(base / "context-bundle-manifest.json", json.dumps(manifest, sort_keys=True))
    mismatch = context_hub.rebuild_context("000002")
    assert mismatch["status"] == "failed"
    assert any(row["code"] == "context_bundle_version_mismatch" for row in mismatch["findings"])


def test_watcher_fingerprint_uses_pruned_allowlist(tmp_path: Path) -> None:
    base = tmp_path / "app"
    info = tmp_path / "info"
    info.mkdir()
    _write(base / "backend" / "service.py", "VALUE = 1\n")
    _write(base / "electron_app" / "main" / "index.js", "const value = 1;\n")
    _write(base / "electron_app" / "dist-client-setup" / "noise.js", "dist 1\n")
    _write(base / "electron_app" / "node_modules" / "noise.js", "module 1\n")
    _write_bundle(base)
    context_hub.configure_context_hub(base_dir=base, info_root=info, surface="development")

    first = context_hub.scan_context_hub_changes("000002")
    _write(base / "electron_app" / "dist-client-setup" / "noise.js", "dist changed with new size\n")
    second = context_hub.scan_context_hub_changes("000002")
    _write(base / "backend" / "service.py", "VALUE = 123456\n")
    third = context_hub.scan_context_hub_changes("000002")

    assert first["initialized"] is False
    assert second["changed"] is False
    assert third["changed"] is True


def test_admin_api_is_full_only_and_never_accepts_client_id(hub_env, monkeypatch: pytest.MonkeyPatch) -> None:
    base, info, _adapter = hub_env
    app = FastAPI()
    app.include_router(create_context_hub_router(base_dir=base, info_root=info, surface="development"))
    client = TestClient(app)

    monkeypatch.setattr(
        context_hub_endpoints,
        "_require_full_admin",
        lambda *_args, **_kwargs: {"client_id": "000002", "username": "owner", "is_full": True},
    )
    status = client.get("/api/admin/context-hub/status")
    assert status.status_code == 200
    assert status.json()["client_id"] == "000002"
    assert str(info) not in status.text
    rejected = client.post(
        "/api/admin/context-hub/rebuild",
        json={"reason": "manual_admin", "client_id": "outro"},
    )
    assert rejected.status_code == 422
    assert not (info / "outro").exists()

    def deny(*_args, **_kwargs):
        raise HTTPException(status_code=403, detail="full only")

    monkeypatch.setattr(context_hub_endpoints, "_require_full_admin", deny)
    requests = [
        ("put", "/api/admin/context-hub/settings", {}),
        ("post", "/api/admin/context-hub/rebuild", {"reason": "manual_admin"}),
        ("post", "/api/admin/context-hub/generations/" + "a" * 32 + "/publish", None),
        ("post", "/api/admin/context-hub/generations/" + "a" * 32 + "/rollback", None),
        ("post", "/api/admin/context-hub/search", {"query": "teste", "limit": 12}),
    ]
    for method, path, payload in requests:
        response = getattr(client, method)(path, json=payload) if payload is not None else getattr(client, method)(path)
        assert response.status_code == 403
