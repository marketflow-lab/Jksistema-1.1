from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers.context_hub import create_context_hub_router
from backend.modules.context_hub import bundles as hub_bundles
from backend.modules.context_hub import curation_records as hub_curation_records
from backend.modules.context_hub import generations as hub_generations
from backend.services import context_hub
from backend.services import context_hub_endpoints
from backend.modules.context_hub import api as context_hub_api


@pytest.fixture()
def governed_hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    base = tmp_path / "app"
    info = tmp_path / "info"
    base.mkdir()
    info.mkdir()
    context_hub.stop_all_context_hub_watchers()
    context_hub.configure_context_hub(base_dir=base, info_root=info, surface="installed")
    inventory = {
        "schema_version": 1,
        "source_version": "1.0.104",
        "entities": [
            {
                "id": "jk:domain:base",
                "kind": "domain",
                "title": "Base segura",
                "domain": "core",
                "content": "Conhecimento tecnico base.",
                "source_refs": ["backend/base.py"],
            }
        ],
        "findings": [],
        "stats": {"entities": 1},
    }
    monkeypatch.setattr(hub_generations, "_build_inventory", lambda config, client_id: (inventory, []))
    monkeypatch.setattr(hub_generations, "_load_context_bundle", lambda *args, **kwargs: ([], [], []))
    monkeypatch.setattr(
        hub_curation_records,
        "_render_inventory",
        lambda current: {"Dominios/base.md": "# Base segura\n\nConhecimento tecnico base.\n"},
    )
    yield base, info
    context_hub.stop_all_context_hub_watchers()


def _approve(client_id: str, note_id: str) -> None:
    context_hub.validate_curated_note(client_id, note_id, actor="admin")
    context_hub.review_curated_note(client_id, note_id, actor="reviewer")
    context_hub.approve_curated_note(client_id, note_id, actor="approver")


def test_approval_hash_edit_invalidation_and_manual_publication(governed_hub) -> None:
    _base, info = governed_hub
    created = context_hub.create_curated_note(
        "tenant-a",
        title="Regra de estoque",
        body="# Regra de estoque\n\nREGRA_ESTOQUE_104 deve ser aplicada.",
        category="Regras",
    )
    note = created["note"]
    assert note["state"] == "draft"
    _approve("tenant-a", note["note_id"])

    ready = context_hub.rebuild_context("tenant-a", reason="manual_admin")
    assert ready["status"] == "ready"
    assert context_hub.get_status("tenant-a")["active_generation"] is None

    path = info / "tenant-a" / "ContextVault" / "80_Curadoria" / note["relative_path"]
    path.write_text(path.read_text(encoding="utf-8") + "\nEdicao no Obsidian.\n", encoding="utf-8")
    listed = context_hub.list_curated_notes("tenant-a")["notes"][0]
    assert listed["state"] == "draft"
    assert listed["approved_at"] is None
    with pytest.raises(context_hub.ContextHubConflictError, match="reconstrua"):
        context_hub.publish_generation("tenant-a", ready["generation_id"])

    _approve("tenant-a", note["note_id"])
    with pytest.raises(context_hub.ContextHubConflictError, match="reconstrua"):
        context_hub.publish_generation("tenant-a", ready["generation_id"])
    published = context_hub.publish_curated_context("tenant-a", force=True)
    assert published["status"] == "active"
    found = context_hub.search_context("tenant-a", "REGRA_ESTOQUE_104")
    assert found["count"] >= 1
    assert found["search_engine"] == "fts5_bm25"
    assert found["embeddings_enabled"] is False

    database = info / "tenant-a" / "context_hub" / "context_hub.db"
    with sqlite3.connect(database) as connection:
        truth_class, content = connection.execute(
            """
            SELECT truth_class, content FROM context_hub_documents
            WHERE generation_id=? AND relative_path LIKE '80_Curadoria/%'
            """,
            (published["generation_id"],),
        ).fetchone()
    assert truth_class == "human_curated"
    assert "authority: advisory" in content
    assert "tenant_scope: tenant:tenant-a" in content


def test_declared_authority_is_ignored_and_cross_tenant_isolated(governed_hub) -> None:
    _base, info = governed_hub
    created = context_hub.create_curated_note(
        "tenant-a",
        title="Autoridade declarada",
        body="# Autoridade\n\nCONTEUDO_TENANT_A_104.",
    )["note"]
    path = info / "tenant-a" / "ContextVault" / "80_Curadoria" / created["relative_path"]
    content = path.read_text(encoding="utf-8")
    content = content.replace("authority: advisory", "authority: binding")
    content = content.replace("truth_class: human_curated", "truth_class: system_authoritative")
    content = content.replace("tenant_scope: tenant:tenant-a", "tenant_scope: tenant:tenant-b")
    path.write_text(content, encoding="utf-8")
    _approve("tenant-a", created["note_id"])
    context_hub.publish_curated_context("tenant-a", force=True)

    assert context_hub.search_context("tenant-a", "CONTEUDO_TENANT_A_104")["count"] >= 1
    assert context_hub.search_context("tenant-b", "CONTEUDO_TENANT_A_104")["count"] == 0


def test_encrypted_backup_wrong_key_and_restore_to_draft(governed_hub) -> None:
    _base, info = governed_hub
    note = context_hub.create_curated_note(
        "tenant-a",
        title="Backup seguro",
        body="# Backup\n\nCONTEUDO_BACKUP_104.",
    )["note"]
    _approve("tenant-a", note["note_id"])
    active = context_hub.publish_curated_context("tenant-a", force=True)
    backup = context_hub.create_curated_backup(
        "tenant-a",
        passphrase="senha-muito-forte-104",
    )["backup"]
    path = info / "tenant-a" / "ContextVault" / "80_Curadoria" / note["relative_path"]
    original = path.read_text(encoding="utf-8")
    path.write_text(original + "\nALTERADO_DEPOIS_BACKUP.\n", encoding="utf-8")

    with pytest.raises(context_hub.ContextHubValidationError, match="Senha incorreta"):
        context_hub.restore_curated_backup(
            "tenant-a",
            backup["backup_id"],
            passphrase="senha-totalmente-errada",
        )
    assert "ALTERADO_DEPOIS_BACKUP" in path.read_text(encoding="utf-8")

    restored = context_hub.restore_curated_backup(
        "tenant-a",
        backup["backup_id"],
        passphrase="senha-muito-forte-104",
    )
    assert restored["restore"]["active_generation_changed"] is False
    assert path.read_text(encoding="utf-8") == original
    assert context_hub.list_curated_notes("tenant-a")["notes"][0]["state"] == "draft"
    assert context_hub.get_status("tenant-a")["active_generation"]["generation_id"] == active["generation_id"]


def test_semantic_chunking_keeps_markdown_units() -> None:
    chunks = hub_bundles._chunks_for_document(
        "jk:test",
        """# Produto

Campo: valor
Outro: valor 2

| SKU | Estoque |
| --- | --- |
| ABC | 10 |

## Aplicacao

Honda Civic 2020.
""",
        maximum=500,
    )
    types = [chunk["semantic_type"] for chunk in chunks]
    assert "fields" in types
    assert "table" in types
    table = next(chunk["content"] for chunk in chunks if chunk["semantic_type"] == "table")
    assert "| SKU | Estoque |" in table
    assert "| ABC | 10 |" in table


def test_admin_endpoint_rejects_tenant_injection(governed_hub, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}
    monkeypatch.setattr(
        context_hub_endpoints,
        "_require_full_admin",
        lambda request, authorization: {"client_id": "tenant-a", "username": "admin"},
    )

    def fake_create(client_id: str, **kwargs):
        captured["client_id"] = client_id
        return {"success": True}

    monkeypatch.setattr(context_hub_api, "create_curated_note", fake_create)
    app = FastAPI()
    app.include_router(create_context_hub_router())
    client = TestClient(app)

    rejected = client.post(
        "/api/admin/context-hub/curation/notes",
        json={"title": "Nota", "body": "# Nota", "client_id": "tenant-b"},
    )
    assert rejected.status_code == 422
    accepted = client.post(
        "/api/admin/context-hub/curation/notes",
        json={"title": "Nota", "body": "# Nota"},
    )
    assert accepted.status_code == 200
    assert captured["client_id"] == "tenant-a"


def test_watcher_is_off_by_default_and_scoped_to_curated(governed_hub) -> None:
    base, info = governed_hub
    context_hub.bootstrap_context_hub("tenant-a")
    settings = context_hub.get_settings("tenant-a")
    assert settings["watch_enabled"] is False
    assert settings["auto_publish_enabled"] is False
    assert settings["watcher_supported"] is True

    first = context_hub.scan_context_hub_changes("tenant-a")
    (base / "backend").mkdir()
    (base / "backend" / "outside.py").write_text("VALUE = 1\n", encoding="utf-8")
    outside = context_hub.scan_context_hub_changes("tenant-a")
    curated = info / "tenant-a" / "ContextVault" / "80_Curadoria" / "Notas" / "obsidian.md"
    curated.write_text("# Alteracao no Obsidian\n", encoding="utf-8")
    inside = context_hub.scan_context_hub_changes("tenant-a")

    assert first["initialized"] is False
    assert outside["changed"] is False
    assert inside["changed"] is True
    with pytest.raises(context_hub.ContextHubValidationError, match="automatica"):
        context_hub.update_settings("tenant-a", auto_publish_enabled=True)
