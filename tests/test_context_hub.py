from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.routers.context_hub import create_context_hub_router
from backend.services import context_hub, context_hub_endpoints


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _publish_ready(result: dict) -> dict:
    assert result["success"] is True, result
    assert result["status"] == "ready", result
    return context_hub.publish_generation("000002", result["generation_id"])


def _rebuild_and_publish(*, force: bool = False) -> dict:
    return _publish_ready(context_hub.rebuild_context("000002", force=force))


def _write_bundle(
    base: Path,
    *,
    version: str = "1.0.99",
    content: str = "# Operacao\n\nConhecimento tecnico seguro.\n",
    include_ml_guide: bool = False,
) -> Path:
    relative_documents = {
        "docs/knowledge/README.md": "# Context Hub\n\nConhecimento versionado seguro.\n",
        "docs/knowledge/security-policy.md": "# Seguranca\n\nPolitica tecnica segura.\n",
        "docs/knowledge/vault-structure.md": "# Estrutura\n\nEstrutura persistente segura.\n",
        "docs/knowledge/templates/curated-note.md": "# Nota curada\n\nModelo editorial seguro.\n",
        "docs/knowledge/templates/generated-note.md": "# Nota gerada\n\nModelo gerenciado seguro.\n",
        "docs/knowledge/operacao.md": content,
    }
    if include_ml_guide:
        relative_documents["docs/knowledge/mercado-livre-api-consultas.md"] = (
            "# API Mercado Livre\n\nGUIA_ML_OBSIDIAN_2026 seguro.\n"
        )
    files = []
    for relative, document_content in sorted(relative_documents.items()):
        document = base / relative
        _write(document, document_content)
        data = document.read_bytes()
        files.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
        )
    manifest = {
        "files": files,
        "schema_version": 1,
        "source_version": version,
    }
    _write(base / "context-bundle-manifest.json", json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    return base / "docs" / "knowledge" / "operacao.md"


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
    dashboard = vault / context_hub.CURATION_DASHBOARD_RELATIVE_PATH
    assert dashboard.is_file()
    assert "ai_usage: denied" in dashboard.read_text(encoding="utf-8")
    assert "- Total: 0" in dashboard.read_text(encoding="utf-8")

    with pytest.raises(context_hub.ContextHubValidationError):
        context_hub.bootstrap_context_hub("../outro")


def test_repeated_settings_poll_does_not_rescan_curation_dashboard(
    hub_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _base, _info, _adapter = hub_env
    context_hub.bootstrap_context_hub("000002")
    calls = 0
    original = context_hub._refresh_curation_dashboard_best_effort

    def counted(paths):
        nonlocal calls
        calls += 1
        return original(paths)

    monkeypatch.setattr(context_hub, "_refresh_curation_dashboard_best_effort", counted)
    context_hub.get_settings("000002")
    context_hub.get_settings("000002")

    assert calls == 0


@pytest.mark.parametrize(
    "client_id",
    [
        "Cliente",
        "cliente.",
        "cliente ",
        "000002 ",
        "con",
        "con.txt",
        "aux.json",
        "com1",
        "lpt9.log",
    ],
)
def test_client_id_rejects_windows_collisions(client_id: str) -> None:
    with pytest.raises(context_hub.ContextHubValidationError):
        context_hub._normalize_client_id(client_id)

    assert context_hub._normalize_client_id("000002") == "000002"


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


def test_configured_info_root_is_revalidated_for_every_tenant_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = tmp_path / "app"
    info = tmp_path / "info"
    base.mkdir()
    info.mkdir()
    context_hub.configure_context_hub(base_dir=base, info_root=info, surface="development")
    real_is_link = context_hub._is_link_or_junction

    monkeypatch.setattr(
        context_hub,
        "_is_link_or_junction",
        lambda path: Path(path).absolute() == info.absolute() or real_is_link(path),
    )

    with pytest.raises(context_hub.ContextHubValidationError, match="raiz info"):
        context_hub._tenant_paths("000002")


def test_dlp_blocks_categories_without_match_and_allows_hashes_and_oem() -> None:
    sha = "b3d19e4c864d4c0a1179e5a29af92f5bb76b7e130cb98f18df6a8c80f7607894"
    allowed = (
        f"source_hash: {sha}\n"
        "OEM 52998224725\nSKU 11222333000181\n"
        "Codigo compacto 31999991234\n"
        "token: redacted\nAuthorization: Bearer <redacted>"
    )
    assert context_hub.scan_dlp(allowed) == []

    blocked = context_hub.scan_dlp(
        "Contato: pessoa@example.com\nTelefone: 31999991234\n"
        "Rua das Flores, 123 - CEP 12345-678\naccess_token: abcdefghijklmnop\n"
        "Authorization: Bearer bearer-value-1234\nOAuth: oauth-value-1234\n"
        "token: generic-value-1234\nCPF: 52998224725\nCNPJ: 11222333000181",
        source_ref="nota.md",
    )
    assert {row["category"] for row in blocked} == {
        "address",
        "cnpj",
        "cpf",
        "credential",
        "email",
        "phone",
    }
    serialized = json.dumps(blocked)
    assert "pessoa@example.com" not in serialized
    assert "abcdefghijklmnop" not in serialized
    assert "99999" not in serialized
    assert "Flores" not in serialized


def test_atomic_replace_retries_transient_permission_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.txt"
    target = tmp_path / "target.txt"
    source.write_text("novo", encoding="utf-8")
    target.write_text("antigo", encoding="utf-8")
    real_replace = context_hub.os.replace
    calls = 0

    def transient_replace(source_path, target_path):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise PermissionError(5, "bloqueio transitorio")
        return real_replace(source_path, target_path)

    monkeypatch.setattr(context_hub.os, "replace", transient_replace)

    context_hub._replace_with_retry(source, target, attempts=3)

    assert calls == 3
    assert target.read_text(encoding="utf-8") == "novo"


def test_dlp_scans_all_nested_metadata_and_omits_sensitive_source_ref() -> None:
    metadata = {
        "id": "jk:note:pessoa@example.com",
        "source_refs": ["80_Curadoria/contato-pessoa@example.com.md"],
        "nested": {
            "credentials": [{"token": "nested-token-value"}],
            "contact": {"phone": "31999991234"},
        },
    }

    blocked = context_hub.scan_dlp(
        context_hub._dlp_document_text(metadata, "Corpo editorial seguro."),
        source_ref="80_Curadoria/pessoa@example.com.md",
    )

    assert {row["category"] for row in blocked} == {"credential", "email", "phone"}
    assert all("source_ref" not in row for row in blocked)
    serialized = json.dumps(blocked)
    assert "pessoa@example.com" not in serialized
    assert "nested-token-value" not in serialized
    assert "31999991234" not in serialized


def test_dlp_sensitive_metadata_never_reaches_generation_database(hub_env) -> None:
    _base, info, adapter = hub_env
    active = _rebuild_and_publish()
    adapter.entities = [
        _entity(
            "jk:domain:seguro",
            source_ref="backend/pessoa@example.com.py",
        )
    ]
    adapter.entities[0]["metadata"] = {
        "auth": {"token": "metadata-secret-value"},
        "contact": {"phone": "31999991234"},
    }

    failed = context_hub.rebuild_context("000002", force=True)

    assert active["status"] == "active"
    assert failed["status"] == "failed"
    assert context_hub.get_status("000002")["active_generation"]["generation_id"] == active["generation_id"]
    serialized = json.dumps(failed)
    assert "pessoa@example.com" not in serialized
    assert "metadata-secret-value" not in serialized
    assert "31999991234" not in serialized
    internal = info / "000002" / "context_hub"
    for database_part in internal.glob("context_hub.db*"):
        contents = database_part.read_bytes()
        assert b"pessoa@example.com" not in contents
        assert b"metadata-secret-value" not in contents
        assert b"31999991234" not in contents


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

    first = _rebuild_and_publish()
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
    active = _rebuild_and_publish()
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


def test_status_reads_database_from_one_snapshot(hub_env, monkeypatch: pytest.MonkeyPatch) -> None:
    _base, info, _adapter = hub_env
    active = _rebuild_and_publish()
    database = info / "000002" / "context_hub" / "context_hub.db"
    real_active_generation_id = context_hub._active_generation_id
    mutation_done = False

    def mutate_after_pointer_read(connection: sqlite3.Connection):
        nonlocal mutation_done
        generation_id = real_active_generation_id(connection)
        if generation_id and not mutation_done:
            mutation_done = True
            with sqlite3.connect(database, timeout=5, isolation_level=None) as writer:
                writer.execute(
                    "UPDATE context_hub_generations SET source_version=? WHERE generation_id=?",
                    ("changed-after-snapshot", generation_id),
                )
        return generation_id

    monkeypatch.setattr(context_hub, "_active_generation_id", mutate_after_pointer_read)

    status = context_hub.get_status("000002")

    assert mutation_done is True
    assert status["active_generation"]["generation_id"] == active["generation_id"]
    assert status["source_version"] == "1.0.99"
    with sqlite3.connect(database) as verifier:
        stored_version = verifier.execute(
            "SELECT source_version FROM context_hub_generations WHERE generation_id=?",
            (active["generation_id"],),
        ).fetchone()[0]
    assert stored_version == "changed-after-snapshot"


def test_single_sku_change_reuses_unrelated_documents(hub_env) -> None:
    _base, _info, adapter = hub_env
    adapter.sku_map_only = True
    adapter.entities = [
        _entity("jk:sku:001", kind="sku", domain="cadastro", content="Peca Honda segura."),
        _entity("jk:sku:002", kind="sku", domain="cadastro", content="Peca Yamaha segura."),
    ]
    _rebuild_and_publish()
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
    active = _rebuild_and_publish()
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


def test_recovery_restores_backup_when_crash_happens_before_old_moved_journal(hub_env) -> None:
    _base, info, adapter = hub_env
    active = _rebuild_and_publish()
    paths = context_hub._tenant_paths("000002", info_root=info)
    before = {
        path.relative_to(paths.generated_dir).as_posix(): path.read_bytes()
        for path in paths.generated_dir.rglob("*")
        if path.is_file()
    }

    adapter.entities = [_entity("jk:domain:test", title="Candidata", content="Nova candidata")]
    context_hub.update_settings("000002", auto_publish_enabled=False)
    candidate = context_hub.rebuild_context("000002")
    temporary, backup = context_hub._copy_publish_candidate(paths, candidate["generation_id"])
    context_hub._write_json_atomic(
        paths.journal_path,
        {
            "generation_id": candidate["generation_id"],
            "previous_generation_id": active["generation_id"],
            "had_previous": True,
            "state": "prepared",
            "created_at": "2026-07-17T00:00:00+00:00",
        },
    )
    os.replace(paths.generated_dir, backup)

    context_hub._recover_publish_journal(paths)

    after = {
        path.relative_to(paths.generated_dir).as_posix(): path.read_bytes()
        for path in paths.generated_dir.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert not temporary.exists()
    assert not backup.exists()
    assert not paths.journal_path.exists()
    assert context_hub.get_status("000002")["active_generation"]["generation_id"] == active["generation_id"]


def test_recovery_fails_closed_when_previous_backup_is_missing(hub_env) -> None:
    _base, info, adapter = hub_env
    active = _rebuild_and_publish()
    paths = context_hub._tenant_paths("000002", info_root=info)
    adapter.entities = [_entity("jk:domain:test", title="Candidata", content="Nova candidata")]
    context_hub.update_settings("000002", auto_publish_enabled=False)
    candidate = context_hub.rebuild_context("000002")
    temporary, _backup = context_hub._copy_publish_candidate(paths, candidate["generation_id"])
    context_hub._write_json_atomic(
        paths.journal_path,
        {
            "generation_id": candidate["generation_id"],
            "previous_generation_id": active["generation_id"],
            "had_previous": True,
            "state": "old_moved",
            "created_at": "2026-07-17T00:00:00+00:00",
        },
    )

    with pytest.raises(context_hub.ContextHubValidationError, match="Backup"):
        context_hub._recover_publish_journal(paths)

    assert temporary.exists()
    assert paths.journal_path.exists()


def test_curated_note_requires_explicit_publication(hub_env) -> None:
    _base, _info, _adapter = hub_env
    draft = context_hub.create_curated_note(
        "000002", title="Rascunho", body="NAO_INDEXAR_123.", category="Notas", actor="owner",
    )["note"]
    approved = context_hub.create_curated_note(
        "000002", title="Regra publicada", body="CURADORIA_INDEXADA_456.", category="Regras", actor="owner",
    )["note"]
    context_hub.validate_curated_note("000002", approved["note_id"], actor="owner")
    context_hub.review_curated_note("000002", approved["note_id"], actor="owner")
    context_hub.approve_curated_note("000002", approved["note_id"], actor="owner")

    ready = context_hub.rebuild_context("000002")

    assert ready["status"] == "ready"
    assert context_hub.search_context("000002", "CURADORIA_INDEXADA_456")["count"] == 0
    context_hub.publish_generation("000002", ready["generation_id"])
    assert context_hub.search_context("000002", "CURADORIA_INDEXADA_456")["count"] == 1
    assert context_hub.search_context("000002", "NAO_INDEXAR_123")["count"] == 0
    assert draft["state"] == "draft"


def test_curation_dashboard_tracks_workflow_without_indexing_note_body(hub_env) -> None:
    _base, info, _adapter = hub_env
    created = context_hub.create_curated_note(
        "000002",
        title="Regra segura",
        body="SEGREDO_DO_CORPO_9988.",
        category="Regras",
        actor="owner",
    )["note"]
    dashboard = (
        info
        / "000002"
        / "ContextVault"
        / context_hub.CURATION_DASHBOARD_RELATIVE_PATH
    )

    draft_text = dashboard.read_text(encoding="utf-8")
    assert "[[80_Curadoria/Regras/regra-segura\\|Regra segura]]" in draft_text
    assert "[[80_Curadoria/Regras/regra-segura|Regra segura]]" not in draft_text
    assert "| Rascunho | Valida | Sem prazo | Consultiva |" in draft_text
    assert "SEGREDO_DO_CORPO_9988" not in draft_text
    assert "owner" not in draft_text
    assert created["content_sha256"] not in draft_text
    assert "source_hash" not in draft_text

    context_hub.validate_curated_note("000002", created["note_id"], actor="owner")
    context_hub.review_curated_note("000002", created["note_id"], actor="owner")
    context_hub.approve_curated_note("000002", created["note_id"], actor="owner")
    assert "| Aprovada | Valida | Sem prazo | Consultiva |" in dashboard.read_text(encoding="utf-8")

    note_path = (
        info / "000002" / "ContextVault" / "80_Curadoria" / created["relative_path"]
    )
    current = note_path.read_text(encoding="utf-8")
    note_path.write_text(current.replace("SEGREDO_DO_CORPO_9988", "SEGREDO_DO_CORPO_8899"), encoding="utf-8")
    context_hub.list_curated_notes("000002")
    refreshed = dashboard.read_text(encoding="utf-8")
    assert "| Rascunho | Valida | Sem prazo | Consultiva |" in refreshed
    assert "SEGREDO_DO_CORPO_8899" not in refreshed
    with sqlite3.connect(info / "000002" / "context_hub" / "context_hub.db") as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM context_hub_documents WHERE relative_path=?",
            (context_hub.CURATION_DASHBOARD_RELATIVE_PATH,),
        ).fetchone()[0]
    assert count == 0


def test_curation_dashboard_table_wikilinks_create_recognizable_backlinks(hub_env) -> None:
    _base, info, _adapter = hub_env
    created = [
        context_hub.create_curated_note(
            "000002",
            title=f"Nota de regressao {index}",
            body=f"Conteudo seguro {index}.",
            category="Regras" if index % 2 else "Notas",
        )["note"]
        for index in range(1, 10)
    ]
    dashboard = (
        info
        / "000002"
        / "ContextVault"
        / context_hub.CURATION_DASHBOARD_RELATIVE_PATH
    ).read_text(encoding="utf-8")

    targets: list[str] = []
    for line in dashboard.splitlines():
        if not line.startswith("| [["):
            continue
        # Obsidian/Markdown divide table cells only at unescaped pipes. A raw
        # wikilink alias separator would therefore produce six cells here and
        # the note would not receive the intended graph backlink.
        cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        assert len(cells) == 5
        match = re.fullmatch(r"\[\[([^\[\]|]+)\\\|([^\[\]]+)\]\]", cells[0])
        assert match is not None
        targets.append(match.group(1))

    assert set(targets) == {
        f"80_Curadoria/{note['relative_path'][:-3]}" for note in created
    }
    assert len(targets) == len(created) == 9


def test_moving_or_deleting_curated_note_tombstones_old_state(hub_env) -> None:
    _base, info, _adapter = hub_env
    created = context_hub.create_curated_note(
        "000002", title="Regra movel", body="Conteudo movel seguro.", category="Regras"
    )["note"]
    context_hub.validate_curated_note("000002", created["note_id"])
    context_hub.review_curated_note("000002", created["note_id"])
    context_hub.approve_curated_note("000002", created["note_id"])
    curated_root = info / "000002" / "ContextVault" / "80_Curadoria"
    source = curated_root / created["relative_path"]
    target = curated_root / "Notas" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    source.replace(target)

    notes = context_hub.list_curated_notes("000002")["notes"]
    assert len(notes) == 1
    assert notes[0]["relative_path"].startswith("Notas/")
    assert notes[0]["state"] == "draft"
    assert context_hub.get_status("000002")["curation"]["states"] == {"draft": 1}
    with sqlite3.connect(info / "000002" / "context_hub" / "context_hub.db") as connection:
        rows = connection.execute(
            "SELECT relative_path, state, present FROM context_hub_curated_approvals ORDER BY relative_path"
        ).fetchall()
    assert rows == [
        (f"Notas/{source.name}", "draft", 1),
        (created["relative_path"], "approved", 0),
    ]

    target.unlink()
    assert context_hub.list_curated_notes("000002")["count"] == 0
    assert context_hub.get_status("000002")["curation"]["states"] == {}
    dashboard = (
        info / "000002" / "ContextVault" / context_hub.CURATION_DASHBOARD_RELATIVE_PATH
    ).read_text(encoding="utf-8")
    assert "- Total: 0" in dashboard


def test_curation_dashboard_withholds_identifier_for_unreadable_note(hub_env) -> None:
    _base, info, _adapter = hub_env
    context_hub.bootstrap_context_hub("000002")
    invalid = (
        info
        / "000002"
        / "ContextVault"
        / "80_Curadoria"
        / "Notas"
        / "nota-invalida.md"
    )
    invalid.write_bytes(b"\xff\xfe\x00")

    context_hub.list_curated_notes("000002")
    dashboard = (
        info / "000002" / "ContextVault" / context_hub.CURATION_DASHBOARD_RELATIVE_PATH
    ).read_text(encoding="utf-8")

    assert "| Nota retida | Indisponivel | Bloqueada | Indisponivel | Consultiva |" in dashboard
    assert context_hub._curated_note_id("Notas/nota-invalida.md")[:8] not in dashboard


def test_superseded_curated_note_cannot_be_approved(hub_env) -> None:
    _base, info, _adapter = hub_env
    created = context_hub.create_curated_note(
        "000002", title="Regra historica", body="Conteudo historico seguro.", category="Regras"
    )["note"]
    note_path = info / "000002" / "ContextVault" / "80_Curadoria" / created["relative_path"]
    content = note_path.read_text(encoding="utf-8")
    note_path.write_text(
        content.replace(
            "module: curadoria\n",
            "lifecycle: superseded\n"
            "superseded_by: jk:curated:replacement\n"
            "valid_to: '2026-07-20'\n"
            "module: curadoria\n",
        ),
        encoding="utf-8",
    )

    context_hub.validate_curated_note("000002", created["note_id"])
    context_hub.review_curated_note("000002", created["note_id"])
    with pytest.raises(context_hub.ContextHubValidationError, match="substituida"):
        context_hub.approve_curated_note("000002", created["note_id"])
    dashboard = (
        info / "000002" / "ContextVault" / context_hub.CURATION_DASHBOARD_RELATIVE_PATH
    ).read_text(encoding="utf-8")
    assert "| Revisada | Somente historico | Substituida | Consultiva |" in dashboard
    with sqlite3.connect(info / "000002" / "context_hub" / "context_hub.db") as connection:
        connection.execute(
            "UPDATE context_hub_curated_approvals SET state='approved' WHERE relative_path=?",
            (created["relative_path"],),
        )
        connection.commit()
    active = _publish_ready(context_hub.rebuild_context("000002"))
    assert active["status"] == "active"
    search = context_hub.search_context("000002", "Conteudo historico seguro")
    assert all(result["doc_id"] != created["document_id"] for result in search["results"])


def test_unknown_curated_lifecycle_fails_closed(hub_env) -> None:
    _base, info, _adapter = hub_env
    created = context_hub.create_curated_note(
        "000002", title="Regra invalida", body="Conteudo seguro.", category="Regras"
    )["note"]
    note_path = info / "000002" / "ContextVault" / "80_Curadoria" / created["relative_path"]
    content = note_path.read_text(encoding="utf-8")
    note_path.write_text(
        content.replace("module: curadoria\n", "lifecycle: superseeded\nmodule: curadoria\n"),
        encoding="utf-8",
    )

    with pytest.raises(context_hub.ContextHubValidationError, match="validacao"):
        context_hub.validate_curated_note("000002", created["note_id"])


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

    result = _publish_ready(context_hub.rebuild_context("000002"))

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
    assert search["count"] >= 1
    assert any(item["doc_id"] == "jk:sku:449" for item in search["results"])


def test_bundle_is_fail_closed_and_ignores_unlisted_overlay(hub_env) -> None:
    base, info, _adapter = hub_env
    active = _rebuild_and_publish()
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
    rebuilt = _publish_ready(context_hub.rebuild_context("000002", force=True))
    assert rebuilt["status"] == "active"
    assert context_hub.search_context("000002", "NAO_IMPORTAR_OVERLAY_999")["count"] == 0
    assert context_hub.search_context("000002", "Conhecimento tecnico seguro")["count"] >= 1

    manifest = json.loads((base / "context-bundle-manifest.json").read_text(encoding="utf-8"))
    manifest["source_version"] = "0.0.1"
    _write(base / "context-bundle-manifest.json", json.dumps(manifest, sort_keys=True))
    mismatch = context_hub.rebuild_context("000002")
    assert mismatch["status"] == "failed"
    assert any(row["code"] == "context_bundle_version_mismatch" for row in mismatch["findings"])


def test_reviewed_bundle_guide_is_visible_in_obsidian_without_duplicate_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = tmp_path / "app"
    info = tmp_path / "info"
    base.mkdir()
    info.mkdir()
    _write_bundle(base, include_ml_guide=True)
    adapter = FakeInventoryAdapter([_entity("jk:domain:test")])
    monkeypatch.setattr(
        adapter,
        "render_context_inventory_markdown",
        lambda _inventory: {
            "70_Gerado/Contratos/APIs/mercado-livre.md": (
                "---\nid: jk:map:apis:mercado-livre\ntype: map\n---\n\n"
                "# APIs do dominio mercado-livre\n"
            )
        },
    )
    monkeypatch.setattr(context_hub, "_load_inventory_adapter", lambda: adapter)
    context_hub.configure_context_hub(base_dir=base, info_root=info, surface="development")

    active = _publish_ready(context_hub.rebuild_context("000002"))

    visible = (
        info
        / "000002"
        / "ContextVault"
        / "70_Gerado"
        / "Contratos"
        / "Mercado-Livre-API-Consultas.md"
    )
    assert active["status"] == "active"
    assert visible.is_file()
    assert "GUIA_ML_OBSIDIAN_2026" in visible.read_text(encoding="utf-8")
    search = context_hub.search_context("000002", "GUIA_ML_OBSIDIAN_2026")
    assert search["count"] == 1
    assert search["results"][0]["doc_id"] == "jk:bundle:mercado-livre-api-consultas-md"
    assert not (info / "000002" / "ContextVault" / "80_Curadoria" / "Mercado-Livre-API-Consultas.md").exists()
    anchor = (
        info
        / "000002"
        / "ContextVault"
        / "70_Gerado"
        / "Contratos"
        / "APIs"
        / "mercado-livre.md"
    )
    assert anchor.is_file()
    anchor_text = anchor.read_text(encoding="utf-8")
    assert "[[70_Gerado/Contratos/Mercado-Livre-API-Consultas|" in anchor_text
    with sqlite3.connect(info / "000002" / "context_hub" / "context_hub.db") as connection:
        anchor_documents = connection.execute(
            "SELECT COUNT(*) FROM context_hub_documents WHERE generation_id=? AND relative_path=?",
            (active["generation_id"], "70_Gerado/Contratos/APIs/mercado-livre.md"),
        ).fetchone()[0]
        indexed_bundle_content = connection.execute(
            "SELECT content FROM context_hub_documents WHERE generation_id=? AND relative_path=?",
            (active["generation_id"], "@bundle/mercado-livre-api-consultas.md"),
        ).fetchone()[0]
    assert anchor_documents == 1
    assert visible.read_text(encoding="utf-8") == indexed_bundle_content
    assert not (
        info
        / "000002"
        / "ContextVault"
        / "70_Gerado"
        / "Mapas"
        / "Documentacao-Revisada.md"
    ).exists()


def test_bundle_rejects_empty_manifest_and_missing_required_entries(hub_env) -> None:
    base, _info, _adapter = hub_env
    active = _rebuild_and_publish()
    manifest_path = base / "context-bundle-manifest.json"

    _write(
        manifest_path,
        json.dumps({"files": [], "schema_version": 1, "source_version": "1.0.99"}),
    )
    empty = context_hub.rebuild_context("000002", force=True)

    assert empty["status"] == "failed"
    assert any(row["code"] == "context_bundle_manifest_empty" for row in empty["findings"])
    assert context_hub.get_status("000002")["active_generation"]["generation_id"] == active["generation_id"]

    document = base / "docs" / "knowledge" / "operacao.md"
    data = document.read_bytes()
    _write(
        manifest_path,
        json.dumps(
            {
                "files": [
                    {
                        "path": "docs/knowledge/operacao.md",
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "size": len(data),
                    }
                ],
                "schema_version": 1,
                "source_version": "1.0.99",
            }
        ),
    )
    missing = context_hub.rebuild_context("000002", force=True)

    assert missing["status"] == "failed"
    assert any(
        row["code"] == "context_bundle_required_entries_missing"
        for row in missing["findings"]
    )
    assert context_hub.get_status("000002")["active_generation"]["generation_id"] == active["generation_id"]


def test_watcher_fingerprint_observes_only_curated_notes(tmp_path: Path) -> None:
    base = tmp_path / "app"
    info = tmp_path / "info"
    info.mkdir()
    _write(base / "backend" / "service.py", "VALUE = 1\n")
    _write(base / "electron_app" / "main" / "index.js", "const value = 1;\n")
    _write(base / "electron_app" / "dist-client-setup" / "noise.js", "dist 1\n")
    _write(base / "electron_app" / "node_modules" / "noise.js", "module 1\n")
    _write_bundle(base)
    context_hub.configure_context_hub(base_dir=base, info_root=info, surface="development")
    context_hub.bootstrap_context_hub("000002")

    first = context_hub.scan_context_hub_changes("000002")
    _write(base / "electron_app" / "dist-client-setup" / "noise.js", "dist changed with new size\n")
    second = context_hub.scan_context_hub_changes("000002")
    _write(base / "backend" / "service.py", "VALUE = 123456\n")
    third = context_hub.scan_context_hub_changes("000002")
    curated = info / "000002" / "ContextVault" / "80_Curadoria" / "Notas" / "rascunho.md"
    _write(curated, "# Rascunho\n\nMudanca humana.\n")
    fourth = context_hub.scan_context_hub_changes("000002")
    dashboard = info / "000002" / "ContextVault" / context_hub.CURATION_DASHBOARD_RELATIVE_PATH
    dashboard.write_text(dashboard.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    fifth = context_hub.scan_context_hub_changes("000002")
    original_stat = curated.stat()
    _write(curated, "# Rascunho\n\nMudanca segura.\n")
    os.utime(curated, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    sixth = context_hub.scan_context_hub_changes("000002")

    assert first["initialized"] is False
    assert second["changed"] is False
    assert third["changed"] is False
    assert fourth["changed"] is True
    assert fifth["changed"] is False
    assert sixth["changed"] is True


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
