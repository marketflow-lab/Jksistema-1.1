from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from contextlib import nullcontext
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.modules.context_hub import api as context_hub
from backend.modules.context_hub import curation_records as hub_curation_records
from backend.modules.context_hub import generations as hub_generations
from backend.modules.context_hub.store_sku_contracts import STORE_SKU_PUBLIC_SURFACE
from backend.modules.context_hub.store_sku_repository import (
    load_store_sku_knowledge,
    publish_store_sku_generation,
)
from backend.services import shared_sync  # noqa: F401 - initialize shared runtime
from backend.services import shared_sync_ai_context as ai_context
from backend.services import shared_sync_apply_scope as apply_scope
from backend.services import shared_sync_bundle
from backend.services import shared_sync_cadastro_transaction as cadastro_transaction
from backend.services import shared_sync_machine


CLIENT_ID = "tenant-a"
USERNAME = "alice"
STORE = {
    "store_ref": "store-a",
    "store_name": "Loja Original",
    "seller_id": "588182191",
    "site_id": "MLB",
    "surface": STORE_SKU_PUBLIC_SURFACE,
}


def _canonical(sku: str) -> dict:
    return {
        "schema_version": 2,
        "sku": sku,
        "nome_produto": "Produto sincronizado",
        "o_que_e": "Contexto integral sincronizado entre maquinas.",
        "para_que_serve": {"status": "documentado", "itens": ["Uso documentado"]},
        "caracteristicas_tecnicas": {
            "status": "documentado",
            "itens": ["Caracteristica sincronizada"],
        },
        "medidas_do_produto": {"status": "documentado", "itens": ["10 mm"]},
        "modo_de_funcionamento": {"status": "documentado", "itens": ["Modo normal"]},
        "instalacao": {"status": "documentado", "itens": ["Instalacao direta"]},
        "oem": {"status": "documentado", "codigos": ["OEM-299"]},
        "aplicacao": {
            "tipo": "geral",
            "veiculos_compativeis": {"status": "", "itens": []},
            "equipamentos_ou_aplicacoes_compativeis": {
                "status": "documentado",
                "itens": ["Aplicacao documentada"],
            },
        },
        "revisao": {"status": "revisado", "pendencias": []},
        "atualizado_em": "2026-09-16T00:00:00+00:00",
    }


@pytest.fixture()
def context_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    base = tmp_path / "app"
    source_info = tmp_path / "source-info"
    destination_info = tmp_path / "destination-info"
    base.mkdir()
    source_info.mkdir()
    destination_info.mkdir()
    context_hub.stop_all_context_hub_watchers()
    context_hub.configure_context_hub(
        base_dir=base,
        info_root=source_info,
        surface="test",
    )
    inventory = {
        "schema_version": 1,
        "source_version": "test",
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
    monkeypatch.setattr(
        hub_generations,
        "_build_inventory",
        lambda config, client_id: (inventory, []),
    )
    monkeypatch.setattr(
        hub_generations,
        "_load_context_bundle",
        lambda *args, **kwargs: ([], [], []),
    )
    monkeypatch.setattr(
        hub_curation_records,
        "_render_inventory",
        lambda current: {"Dominios/base.md": "# Base segura\n\nConhecimento tecnico base.\n"},
    )
    yield base, source_info, destination_info
    context_hub.stop_all_context_hub_watchers()


def _authorized_store(*, name: str = "Loja Renomeada", seller_id: str | None = None) -> dict:
    return {
        **STORE,
        "store_name": name,
        "seller_id": seller_id or STORE["seller_id"],
    }


def _publish_source_context(source_info: Path) -> None:
    publish_store_sku_generation(
        CLIENT_ID,
        STORE,
        canonical_documents={"00299-1": _canonical("00299-1")},
        store_guidance={"orientacoes_perguntas": "Atenda como vendedor da loja."},
        sku_guidance={"00299-1": {"notas": "Orientacao exata do SKU."}},
        bindings=[
            {"item_id": "MLB123456789", "variation_id": "007", "sku": "00299-1"},
        ],
        preserve_curated_files=True,
        info_root=source_info,
    )
    created = context_hub.create_curated_note(
        CLIENT_ID,
        title="Regra sincronizada",
        body="# Regra sincronizada\n\nMARCADOR_CONTEXTO_SINCRONIZADO deve ser aplicado.",
        category="Regras",
        actor="admin",
        info_root=source_info,
    )
    note_id = created["note"]["note_id"]
    context_hub.validate_curated_note(CLIENT_ID, note_id, actor="admin", info_root=source_info)
    context_hub.review_curated_note(CLIENT_ID, note_id, actor="reviewer", info_root=source_info)
    context_hub.approve_curated_note(CLIENT_ID, note_id, actor="approver", info_root=source_info)
    published = context_hub.publish_curated_context(
        CLIENT_ID,
        force=True,
        info_root=source_info,
    )
    assert published["status"] == "active"


def _snapshot(
    source_info: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[bytes, dict]:
    monkeypatch.setattr(
        ai_context,
        "_resolve_store_scope",
        lambda client_id, store_id: _authorized_store(),
    )
    return ai_context.build_snapshot_bytes(
        CLIENT_ID,
        USERNAME,
        info_root=source_info,
    )


def test_context_round_trip_rebuilds_global_and_exact_store_sku_projection(
    context_roots,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _base, source_info, destination_info = context_roots
    _publish_source_context(source_info)
    data, payload = _snapshot(source_info, monkeypatch)

    assert payload["global_publication"]["active"] is True
    assert payload["store_generations"][0]["scope"]["store_name"] == "Loja Original"
    assert payload["store_generations"][0]["bindings"][0]["sku"] == "00299-1"
    assert b"SQLite format 3" not in data
    assert b"context_hub.db" not in data

    plan = ai_context.validate_snapshot_bytes(
        data,
        expected_client_id=CLIENT_ID,
        expected_username=USERNAME,
    )
    destination_tenant = destination_info / CLIENT_ID
    destination_tenant.mkdir()
    with cadastro_transaction.transaction(str(destination_tenant), []):
        staged = ai_context.stage_snapshot(plan, str(destination_tenant))
    result = ai_context.finalize_snapshot(staged, str(destination_tenant))

    assert result["status"] == "applied"
    assert result["note_count"] == 1
    assert result["store_generation_count"] == 1
    found = context_hub.search_context(
        CLIENT_ID,
        "MARCADOR_CONTEXTO_SINCRONIZADO",
        info_root=destination_info,
    )
    assert found["count"] >= 1
    loaded = load_store_sku_knowledge(
        CLIENT_ID,
        {
            **_authorized_store(),
            "item_id": "MLB123456789",
            "variation_id": "007",
            "sku": "00299-1",
        },
        info_root=destination_info,
    )
    assert loaded["found"] is True
    assert loaded["canonical_document"]["nome_produto"] == "Produto sincronizado"
    assert loaded["guidance"]["general"]["orientacoes_perguntas"] == "Atenda como vendedor da loja."
    assert loaded["guidance"]["sku"]["notas"] == "Orientacao exata do SKU."

    # Removing an active source projection must retire the previously imported
    # destination projection without copying or deleting a SQLite file.
    with sqlite3.connect(source_info / CLIENT_ID / "context_hub" / "context_hub.db") as connection:
        connection.execute("DELETE FROM context_hub_store_sku_active_generations")
        connection.commit()
    second_data, _second_payload = _snapshot(source_info, monkeypatch)
    second_plan = ai_context.validate_snapshot_bytes(
        second_data,
        expected_client_id=CLIENT_ID,
        expected_username=USERNAME,
    )
    with cadastro_transaction.transaction(str(destination_tenant), []):
        second_staged = ai_context.stage_snapshot(second_plan, str(destination_tenant))
    second_result = ai_context.finalize_snapshot(second_staged, str(destination_tenant))
    assert second_result["removed_store_generation_count"] == 1
    removed = load_store_sku_knowledge(
        CLIENT_ID,
        {
            **_authorized_store(),
            "item_id": "MLB123456789",
            "variation_id": "007",
            "sku": "00299-1",
        },
        info_root=destination_info,
    )
    assert removed["found"] is False
    assert context_hub.search_context(
        CLIENT_ID,
        "MARCADOR_CONTEXTO_SINCRONIZADO",
        info_root=destination_info,
    )["count"] >= 1

    with sqlite3.connect(source_info / CLIENT_ID / "context_hub" / "context_hub.db") as connection:
        connection.execute("DELETE FROM context_hub_active_generation")
        connection.commit()
    empty_data, _empty_payload = _snapshot(source_info, monkeypatch)
    empty_plan = ai_context.validate_snapshot_bytes(
        empty_data,
        expected_client_id=CLIENT_ID,
        expected_username=USERNAME,
    )
    with cadastro_transaction.transaction(str(destination_tenant), []):
        empty_staged = ai_context.stage_snapshot(empty_plan, str(destination_tenant))
    empty_result = ai_context.finalize_snapshot(empty_staged, str(destination_tenant))
    assert empty_result["note_count"] == 0
    assert context_hub.search_context(
        CLIENT_ID,
        "MARCADOR_CONTEXTO_SINCRONIZADO",
        info_root=destination_info,
    )["count"] == 0


def test_snapshot_is_bound_to_tenant_user_and_immutable_store_identity(
    context_roots,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _base, source_info, _destination_info = context_roots
    publish_store_sku_generation(
        CLIENT_ID,
        STORE,
        canonical_documents={"00299-1": _canonical("00299-1")},
        store_guidance={},
        sku_guidance={},
        bindings=[{"item_id": "MLB123456789", "variation_id": "", "sku": "00299-1"}],
        preserve_curated_files=True,
        info_root=source_info,
    )
    data, _payload = _snapshot(source_info, monkeypatch)

    # A store rename is allowed because store_id/seller/site/surface remain exact.
    plan = ai_context.validate_snapshot_bytes(
        data,
        expected_client_id=CLIENT_ID,
        expected_username=USERNAME,
    )
    assert plan["store_generations"][0]["scope"]["store_name"] == "Loja Original"
    with pytest.raises(HTTPException) as wrong_user:
        ai_context.validate_snapshot_bytes(
            data,
            expected_client_id=CLIENT_ID,
            expected_username="bob",
        )
    assert wrong_user.value.status_code == 403
    with pytest.raises(HTTPException) as wrong_tenant:
        ai_context.validate_snapshot_bytes(
            data,
            expected_client_id="tenant-b",
            expected_username=USERNAME,
        )
    assert wrong_tenant.value.status_code == 403

    monkeypatch.setattr(
        ai_context,
        "_resolve_store_scope",
        lambda client_id, store_id: _authorized_store(seller_id="999"),
    )
    with pytest.raises(HTTPException) as wrong_store:
        ai_context.validate_snapshot_bytes(
            data,
            expected_client_id=CLIENT_ID,
            expected_username=USERNAME,
        )
    assert wrong_store.value.status_code == 409


def test_cadastro_machine_bundle_has_typed_sidecar_and_legacy_bundle_does_not(
    context_roots,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _base, source_info, _destination_info = context_roots
    tenant = source_info / CLIENT_ID
    tenant.mkdir()
    monkeypatch.setattr(
        shared_sync_bundle,
        "get_tenant_path",
        lambda _client: str(tenant),
        raising=False,
    )
    monkeypatch.setattr(
        shared_sync_bundle,
        "_shared_sync_coletar_arquivos",
        lambda *args, **kwargs: ([], []),
    )
    monkeypatch.setattr(
        shared_sync_bundle,
        "_shared_sync_canonicalize_cadastro_photo_config",
        lambda _client, entries: entries,
    )
    monkeypatch.setattr(
        shared_sync_bundle,
        "_shared_sync_sanitize_strict_legacy_photo_entries",
        lambda _client, entries: entries,
    )
    monkeypatch.setattr(
        shared_sync_bundle,
        "_shared_sync_filter_store_photos_by_canonical_rows",
        lambda _client, entries: entries,
    )

    bundle, manifest, _warnings = shared_sync_bundle._shared_sync_montar_pacote_locked(
        CLIENT_ID,
        "cadastro",
        USERNAME,
        user_only=True,
        include_ai_context=True,
    )
    with zipfile.ZipFile(io.BytesIO(bundle), "r") as archive:
        assert ai_context.AI_CONTEXT_EXTENSION_PATH in archive.namelist()
        assert not any("ContextVault" in name or name.endswith((".db", ".db-wal", ".db-shm")) for name in archive.namelist())
    assert manifest["extensions"]["ai_context"]["schema"] == ai_context.AI_CONTEXT_SCHEMA
    validated, files = apply_scope._shared_sync_read_validated_bundle(bundle, "cadastro")
    assert validated["_ai_context_payload"]
    assert files == []

    legacy, legacy_manifest, _warnings = shared_sync_bundle._shared_sync_montar_pacote_locked(
        CLIENT_ID,
        "cadastro",
        USERNAME,
        user_only=True,
    )
    with zipfile.ZipFile(io.BytesIO(legacy), "r") as archive:
        assert ai_context.AI_CONTEXT_EXTENSION_PATH not in archive.namelist()
    assert "extensions" not in legacy_manifest


def test_only_cadastro_machine_push_enables_ai_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def push_scope(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return {"success": True}

    monkeypatch.setattr(shared_sync_machine, "_shared_sync_push_scope", push_scope)
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_machine_doc_id",
        lambda client_id, username, scope: f"{client_id}:{username}:{scope}",
    )
    session = {"client_id": CLIENT_ID, "username": USERNAME}
    shared_sync_machine._shared_sync_machine_push_scope(session, "cadastro")
    shared_sync_machine._shared_sync_machine_push_scope(session, "vendas")

    assert calls[0]["kwargs"]["include_ai_context"] is True
    assert calls[1]["kwargs"]["include_ai_context"] is False


def test_tampered_sidecar_is_rejected_before_payload_application(
    context_roots,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _base, source_info, _destination_info = context_roots
    tenant = source_info / CLIENT_ID
    tenant.mkdir()
    monkeypatch.setattr(
        shared_sync_bundle,
        "get_tenant_path",
        lambda _client: str(tenant),
        raising=False,
    )
    monkeypatch.setattr(shared_sync_bundle, "_shared_sync_coletar_arquivos", lambda *a, **k: ([], []))
    monkeypatch.setattr(shared_sync_bundle, "_shared_sync_canonicalize_cadastro_photo_config", lambda _c, e: e)
    monkeypatch.setattr(shared_sync_bundle, "_shared_sync_sanitize_strict_legacy_photo_entries", lambda _c, e: e)
    monkeypatch.setattr(shared_sync_bundle, "_shared_sync_filter_store_photos_by_canonical_rows", lambda _c, e: e)
    bundle, _manifest, _warnings = shared_sync_bundle._shared_sync_montar_pacote_locked(
        CLIENT_ID, "cadastro", USERNAME, include_ai_context=True,
    )
    tampered = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(bundle), "r") as source, zipfile.ZipFile(tampered, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            if name == ai_context.AI_CONTEXT_EXTENSION_PATH:
                data += b" "
            target.writestr(name, data)
    with pytest.raises(HTTPException) as error:
        apply_scope._shared_sync_read_validated_bundle(tampered.getvalue(), "cadastro")
    assert error.value.status_code == 502


def test_user_share_cannot_import_ai_context_sidecar(
    context_roots,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _base, source_info, destination_info = context_roots
    data, payload = ai_context.build_snapshot_bytes(
        CLIENT_ID,
        USERNAME,
        info_root=source_info,
    )
    tenant = destination_info / CLIENT_ID
    tenant.mkdir()
    manifest = {
        "client_id": CLIENT_ID,
        "_ai_context_payload": data,
        "extensions": {"ai_context": ai_context.extension_descriptor(data, payload)},
    }
    monkeypatch.setattr(
        apply_scope,
        "get_tenant_path",
        lambda _client: str(tenant),
        raising=False,
    )
    monkeypatch.setattr(
        apply_scope,
        "_shared_sync_read_validated_bundle",
        lambda *args: (dict(manifest), []),
    )
    with pytest.raises(HTTPException) as error:
        apply_scope._shared_sync_aplicar_pacote(
            CLIENT_ID,
            "cadastro",
            b"bundle",
            USERNAME,
            {"user_share": True},
        )
    assert error.value.status_code == 403


def test_context_conflict_rolls_back_cadastro_file_in_same_transaction(
    context_roots,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _base, source_info, destination_info = context_roots
    created = context_hub.create_curated_note(
        CLIENT_ID,
        title="Nota para conflito",
        body="# Nota para conflito\n\nCONTEUDO_ORIGINAL_SINCRONIZADO.",
        category="Notas",
        info_root=source_info,
    )
    note_id = created["note"]["note_id"]
    context_hub.validate_curated_note(CLIENT_ID, note_id, info_root=source_info)
    context_hub.review_curated_note(CLIENT_ID, note_id, info_root=source_info)
    context_hub.approve_curated_note(CLIENT_ID, note_id, info_root=source_info)
    context_hub.publish_curated_context(CLIENT_ID, force=True, info_root=source_info)
    data, payload = ai_context.build_snapshot_bytes(CLIENT_ID, USERNAME, info_root=source_info)
    plan = ai_context.validate_snapshot_bytes(
        data,
        expected_client_id=CLIENT_ID,
        expected_username=USERNAME,
    )
    tenant = destination_info / CLIENT_ID
    tenant.mkdir()
    with cadastro_transaction.transaction(str(tenant), []):
        staged = ai_context.stage_snapshot(plan, str(tenant))
    ai_context.finalize_snapshot(staged, str(tenant))

    note_path = tenant / "ContextVault" / "80_Curadoria" / payload["notes"][0]["path"]
    note_path.write_text(note_path.read_text(encoding="utf-8") + "\nEDICAO_LOCAL.\n", encoding="utf-8")
    cadastro_path = tenant / "cadastro_produtos_meta.json"
    cadastro_path.write_bytes(b'{"state":"before"}')
    manifest = {
        "client_id": CLIENT_ID,
        "_ai_context_payload": data,
        "extensions": {"ai_context": ai_context.extension_descriptor(data, payload)},
    }
    monkeypatch.setattr(
        apply_scope,
        "get_tenant_path",
        lambda _client: str(tenant),
        raising=False,
    )
    monkeypatch.setattr(
        apply_scope,
        "_shared_sync_read_validated_bundle",
        lambda *args: (dict(manifest), [("cadastro_produtos_meta.json", b'{"state":"after"}')]),
    )
    monkeypatch.setattr(apply_scope, "_shared_sync_prevalidar_cadastro", lambda *args: None)
    monkeypatch.setattr(
        apply_scope,
        "_shared_sync_bloquear_writer_store_id",
        lambda *args: nullcontext(),
    )

    def apply_cadastro(*_args):
        apply_scope._shared_sync_atomic_write(
            str(cadastro_path),
            b'{"state":"after"}',
        )
        return {"file_count": 1, "files": ["cadastro_produtos_meta.json"]}

    monkeypatch.setattr(apply_scope, "_shared_sync_aplicar_pacote_conteudo", apply_cadastro)
    with pytest.raises(HTTPException) as error:
        apply_scope._shared_sync_aplicar_pacote(
            CLIENT_ID,
            "cadastro",
            b"bundle",
            USERNAME,
            {"ai_context_machine_sync": True},
        )
    assert error.value.status_code == 409
    assert cadastro_path.read_bytes() == b'{"state":"before"}'
    assert "EDICAO_LOCAL" in note_path.read_text(encoding="utf-8")
