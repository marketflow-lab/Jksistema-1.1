from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path

import pytest

from backend.modules.context_hub import api as context_hub
from backend.modules.context_hub.store_sku_compiler import compile_store_sku_knowledge
from backend.modules.context_hub.store_sku_contracts import (
    APPLICABLE_GUIDANCE_MAX_CHARS,
    CANONICAL_DOCUMENT_MAX_CHARS,
    STORE_SKU_MIGRATION_SCHEMA,
)
from backend.modules.context_hub.store_sku_migration import migrate_store_sku_knowledge
from backend.modules.context_hub.materialization import capture_generation_materialization
from backend.modules.context_hub.paths import _tenant_paths
from backend.modules.context_hub.publication import _copy_publish_candidate
from backend.modules.context_hub.store_sku_repository import (
    create_store_guidance_draft,
    load_store_guidance,
    load_store_sku_knowledge,
    publish_approved_store_guidance,
    publish_store_sku_generation,
    rollback_store_sku_generation,
)


STORE_A = {
    "store_ref": "b1e5a6efb16c0db69bba1836",
    "store_name": "JK Pecas",
    "seller_id": "588182191",
    "site_id": "MLB",
}
STORE_B = {
    "store_ref": "666955b171470f4fb6f6a4a8",
    "store_name": "Carlos Jose",
    "seller_id": "1275608482",
    "site_id": "MLB",
}


@pytest.fixture()
def hub_root(tmp_path: Path):
    base = tmp_path / "app"
    info = tmp_path / "info"
    base.mkdir()
    info.mkdir()
    context_hub.stop_all_context_hub_watchers()
    context_hub.configure_context_hub(base_dir=base, info_root=info, surface="test")
    yield info
    context_hub.stop_all_context_hub_watchers()


def _canonical(sku: str, marker: str) -> dict:
    return {
        "schema_version": 2,
        "sku": sku,
        "nome_produto": f"Produto {marker}",
        "o_que_e": f"Contexto integral {marker}",
        "para_que_serve": {"status": "documentado", "itens": [f"Uso {marker}"]},
        "caracteristicas_tecnicas": {
            "status": "documentado",
            "itens": [f"Rosca {marker}", f"Conector {marker}"],
        },
        "medidas_do_produto": {"status": "documentado", "itens": [f"10 mm {marker}"]},
        "modo_de_funcionamento": {"status": "documentado", "itens": [f"Modo {marker}"]},
        "instalacao": {"status": "documentado", "itens": [f"Instalacao {marker}"]},
        "oem": {"status": "documentado", "codigos": [f"OEM-{marker}"]},
        "aplicacao": {
            "tipo": "geral",
            "veiculos_compativeis": {"status": "", "itens": []},
            "equipamentos_ou_aplicacoes_compativeis": {
                "status": "documentado",
                "itens": [f"Aplicacao {marker}"],
            },
        },
        "revisao": {"status": "revisado", "pendencias": []},
        "atualizado_em": "2026-09-07T00:00:00+00:00",
    }


def _publish(info: Path, store: dict, *, sku: str, item: str, marker: str, variation: str = ""):
    return publish_store_sku_generation(
        "tenant-a",
        store,
        canonical_documents={sku: _canonical(sku, marker)},
        store_guidance={"orientacoes_perguntas": f"Orientacao {marker}"},
        sku_guidance={sku: {"notas": f"Nota {marker}"}},
        bindings=[{"item_id": item, "variation_id": variation, "sku": sku}],
        info_root=info,
    )


def _identity(store: dict, *, sku: str, item: str, variation: str = "") -> dict:
    return {
        **store,
        "sku": sku,
        "item_id": item,
        "variation_id": variation,
    }


def test_exact_store_sku_load_is_integral_and_cross_store_closed(hub_root: Path):
    _publish(hub_root, STORE_A, sku="160-K", item="MLB100", marker="A")
    _publish(hub_root, STORE_B, sku="160-K", item="MLB200", marker="B")

    loaded_a = load_store_sku_knowledge(
        "tenant-a", _identity(STORE_A, sku="160-K", item="MLB100"), info_root=hub_root,
    )
    loaded_b = load_store_sku_knowledge(
        "tenant-a", _identity(STORE_B, sku="160-K", item="MLB200"), info_root=hub_root,
    )

    assert loaded_a["found"] is True
    assert loaded_a["canonical_document"] == _canonical("160-K", "A")
    assert loaded_a["guidance"]["general"] == {"orientacoes_perguntas": "Orientacao A"}
    assert loaded_a["guidance"]["sku"] == {"notas": "Nota A"}
    assert loaded_a["validity"] == {
        "status": "active_approved_generation",
        "identity_verified": True,
        "hashes_verified": True,
        "approval_state": "approved",
    }
    assert "Produto B" not in json.dumps(loaded_a, ensure_ascii=False)
    assert loaded_b["canonical_document"]["nome_produto"] == "Produto B"
    assert "relative_path" not in json.dumps(loaded_a)

    wrong_store = load_store_sku_knowledge(
        "tenant-a", _identity(STORE_B, sku="160-K", item="MLB100"), info_root=hub_root,
    )
    wrong_seller = load_store_sku_knowledge(
        "tenant-a",
        _identity({**STORE_A, "seller_id": "999"}, sku="160-K", item="MLB100"),
        info_root=hub_root,
    )
    assert wrong_store["found"] is False
    assert wrong_seller["found"] is False


def test_variation_binding_requires_exact_variation(hub_root: Path):
    _publish(
        hub_root, STORE_A, sku="SKU-VAR", item="MLB300", marker="VAR", variation="98765",
    )
    missing = load_store_sku_knowledge(
        "tenant-a", _identity(STORE_A, sku="SKU-VAR", item="MLB300"), info_root=hub_root,
    )
    exact = load_store_sku_knowledge(
        "tenant-a",
        _identity(STORE_A, sku="SKU-VAR", item="MLB300", variation="98765"),
        info_root=hub_root,
    )
    assert missing["reason_code"] == "variation_identity_required"
    assert exact["found"] is True


def test_store_guidance_edit_does_not_propagate_to_another_store(hub_root: Path):
    _publish(hub_root, STORE_A, sku="160-K", item="MLB100", marker="A")
    _publish(hub_root, STORE_B, sku="160-K", item="MLB200", marker="B")
    draft = create_store_guidance_draft(
        "tenant-a",
        STORE_A,
        guidance={"orientacoes_perguntas": "Somente JK Pecas"},
        actor="usuario-interno-1",
        info_root=hub_root,
    )["note"]
    context_hub.validate_curated_note("tenant-a", draft["note_id"], actor="validator", info_root=hub_root)
    context_hub.review_curated_note("tenant-a", draft["note_id"], actor="reviewer", info_root=hub_root)
    context_hub.approve_curated_note("tenant-a", draft["note_id"], actor="approver", info_root=hub_root)
    with sqlite3.connect(hub_root / "tenant-a" / "context_hub" / "context_hub.db") as connection:
        actor_row = connection.execute(
            """
            SELECT reviewed_by, approved_by
            FROM context_hub_curated_approvals WHERE relative_path=?
            """,
            (draft["relative_path"],),
        ).fetchone()
    assert actor_row is not None
    assert actor_row[0].startswith("actor-") and actor_row[0] != "reviewer"
    assert actor_row[1].startswith("actor-") and actor_row[1] != "approver"
    publish_approved_store_guidance("tenant-a", STORE_A, info_root=hub_root)

    loaded_a = load_store_sku_knowledge(
        "tenant-a", _identity(STORE_A, sku="160-K", item="MLB100"), info_root=hub_root,
    )
    loaded_b = load_store_sku_knowledge(
        "tenant-a", _identity(STORE_B, sku="160-K", item="MLB200"), info_root=hub_root,
    )
    assert loaded_a["guidance"]["general"]["orientacoes_perguntas"] == "Somente JK Pecas"
    assert loaded_b["guidance"]["general"]["orientacoes_perguntas"] == "Orientacao B"


def test_store_sku_repository_rejects_tenant_override_and_unknown_sku_draft(hub_root: Path):
    _publish(hub_root, STORE_A, sku="160-K", item="MLB100", marker="A")

    mismatch = load_store_sku_knowledge(
        "tenant-a",
        {
            **_identity(STORE_A, sku="160-K", item="MLB100"),
            "tenant_scope": "tenant:tenant-b",
        },
        info_root=hub_root,
    )
    assert mismatch["found"] is False
    assert mismatch["reason_code"] == "tenant_scope_mismatch"
    with pytest.raises(context_hub.ContextHubValidationError, match="nao pertence"):
        create_store_guidance_draft(
            "tenant-a",
            STORE_A,
            sku="SKU-INEXISTENTE",
            guidance={"notas": "Nao deve ser materializada"},
            info_root=hub_root,
        )


def test_approved_guidance_with_tampered_store_identity_cannot_be_published(hub_root: Path):
    _publish(hub_root, STORE_A, sku="160-K", item="MLB100", marker="A")
    draft = create_store_guidance_draft(
        "tenant-a",
        STORE_A,
        sku="160-K",
        guidance={"notas": "Somente esta loja"},
        actor="author-visible-name",
        info_root=hub_root,
    )["note"]
    context_hub.validate_curated_note(
        "tenant-a", draft["note_id"], actor="validator", info_root=hub_root,
    )
    context_hub.review_curated_note(
        "tenant-a", draft["note_id"], actor="reviewer", info_root=hub_root,
    )
    context_hub.approve_curated_note(
        "tenant-a", draft["note_id"], actor="approver", info_root=hub_root,
    )
    note_path = hub_root / "tenant-a" / "ContextVault" / "80_Curadoria" / draft["relative_path"]
    content = note_path.read_text(encoding="utf-8")
    assert "author-visible-name" not in content
    tampered = content.replace(STORE_A["seller_id"], STORE_B["seller_id"], 1)
    assert tampered != content
    note_path.write_text(tampered, encoding="utf-8")
    with pytest.raises(context_hub.ContextHubValidationError, match="diverge"):
        publish_approved_store_guidance("tenant-a", STORE_A, info_root=hub_root)


def test_guidance_editor_loads_all_sku_notes_from_only_the_selected_store(hub_root: Path):
    publish_store_sku_generation(
        "tenant-a",
        STORE_A,
        canonical_documents={
            "160-K": _canonical("160-K", "A"),
            "161-K": _canonical("161-K", "B"),
        },
        store_guidance={"orientacoes_perguntas": "Geral A"},
        sku_guidance={
            "160-K": {"notas": "Nota A"},
            "161-K": {"notas": "Nota B"},
        },
        bindings=[
            {"item_id": "MLB100", "sku": "160-K"},
            {"item_id": "MLB101", "sku": "161-K"},
        ],
        info_root=hub_root,
    )
    loaded = load_store_guidance("tenant-a", STORE_A, info_root=hub_root)
    assert loaded["guidance"]["general"] == {"orientacoes_perguntas": "Geral A"}
    assert loaded["sku_guidance"] == {
        "160-K": {"notas": "Nota A"},
        "161-K": {"notas": "Nota B"},
    }


@pytest.mark.parametrize("sku", ["", "160-K"])
def test_publish_markdown_from_moved_note_after_store_rename(hub_root: Path, sku: str):
    from backend.modules.context_hub.metadata import _dump_frontmatter, _parse_frontmatter
    from backend.modules.context_hub.curation_records import _curated_note_id

    _publish(hub_root, STORE_A, sku="160-K", item="MLB100", marker="A")
    paths = _tenant_paths("tenant-a", info_root=hub_root)
    note = create_store_guidance_draft(
        "tenant-a", STORE_A, sku=sku,
        guidance={"notas" if sku else "orientacoes_perguntas": "Inicial"}, info_root=hub_root,
    )["note"]
    original = paths.curated_dir / note["relative_path"]
    metadata, _ = _parse_frontmatter(original.read_text(encoding="utf-8"))
    body = "# Orientacoes da loja\n\n- Preservar a lista.\n- Confirmar a aplicacao."
    moved = paths.curated_dir / "Notas" / ("sku.md" if sku else "gerais.md")
    moved.parent.mkdir(exist_ok=True)
    moved.write_text(_dump_frontmatter(metadata, body), encoding="utf-8")
    original.unlink()
    note_id = _curated_note_id(moved.relative_to(paths.curated_dir).as_posix())
    context_hub.validate_curated_note("tenant-a", note_id, actor="validator", info_root=hub_root)
    context_hub.review_curated_note("tenant-a", note_id, actor="reviewer", info_root=hub_root)
    context_hub.approve_curated_note("tenant-a", note_id, actor="approver", info_root=hub_root)
    preserved = moved.read_bytes()
    renamed = {**STORE_A, "store_name": "Loja Renomeada"}
    publish_approved_store_guidance("tenant-a", renamed, info_root=hub_root)
    loaded = load_store_guidance("tenant-a", renamed, sku=sku, info_root=hub_root)
    actual = loaded["guidance"]["sku" if sku else "general"]
    assert actual["notas" if sku else "orientacoes_perguntas"] == body
    assert moved.read_bytes() == preserved
    repeated = publish_approved_store_guidance("tenant-a", renamed, info_root=hub_root)
    assert repeated["changed"] is False


def test_publish_never_silently_ignores_approved_new_sku(hub_root: Path):
    from backend.modules.context_hub.store_sku_editor import load_store_guidance_editor, save_store_guidance_editor

    first = _publish(hub_root, STORE_A, sku="160-K", item="MLB100", marker="A")
    before = load_store_guidance_editor("tenant-a", STORE_A, info_root=hub_root)
    edited = save_store_guidance_editor(
        "tenant-a", STORE_A, sku="NEW", guidance={"notas": "Orientacao do novo produto"},
        expected_revision=before["editorial"]["revision"], info_root=hub_root,
    )
    note_id = edited["editorial"]["skus"]["NEW"]["note_id"]
    context_hub.validate_curated_note("tenant-a", note_id, actor="validator", info_root=hub_root)
    context_hub.review_curated_note("tenant-a", note_id, actor="reviewer", info_root=hub_root)
    context_hub.approve_curated_note("tenant-a", note_id, actor="approver", info_root=hub_root)
    with pytest.raises(context_hub.ContextHubValidationError, match="Sincronize o cadastro"):
        publish_approved_store_guidance("tenant-a", STORE_A, info_root=hub_root)
    after = load_store_guidance_editor("tenant-a", STORE_A, info_root=hub_root)
    assert after["generation_id"] == first["generation_id"]
    assert after["sku_guidance"]["NEW"]["notas"] == "Orientacao do novo produto"


def test_store_rollback_switches_database_and_materialized_vault_only_for_that_store(hub_root: Path):
    first = _publish(hub_root, STORE_A, sku="160-K", item="MLB100", marker="A1")
    _publish(hub_root, STORE_B, sku="160-K", item="MLB200", marker="B")
    second = _publish(hub_root, STORE_A, sku="160-K", item="MLB100", marker="A2")
    assert second["previous_generation_id"] == first["generation_id"]
    draft = create_store_guidance_draft(
        "tenant-a", STORE_A, guidance={"orientacoes_perguntas": "Edicao ainda em revisao"},
        info_root=hub_root,
    )
    draft_path = hub_root / "tenant-a" / "ContextVault" / "80_Curadoria" / draft["note"]["relative_path"]
    draft_bytes = draft_path.read_bytes()

    rolled_back = rollback_store_sku_generation(
        "tenant-a", STORE_A, first["generation_id"], info_root=hub_root,
    )
    assert rolled_back["changed"] is True
    assert draft_path.read_bytes() == draft_bytes, "Rollback deve preservar a curadoria atual"
    loaded_a = load_store_sku_knowledge(
        "tenant-a", _identity(STORE_A, sku="160-K", item="MLB100"), info_root=hub_root,
    )
    loaded_b = load_store_sku_knowledge(
        "tenant-a", _identity(STORE_B, sku="160-K", item="MLB200"), info_root=hub_root,
    )
    assert loaded_a["canonical_document"]["nome_produto"] == "Produto A1"
    assert loaded_b["canonical_document"]["nome_produto"] == "Produto B"
    context_path = (
        hub_root / "tenant-a" / "ContextVault" / "70_Gerado" / "Lojas"
        / "b1e5a6efb16c0db69bba1836--jk-pecas" / "SKUs" / "160-K" / "Contexto.md"
    )
    materialized = context_path.read_text(encoding="utf-8")
    assert "Produto A1" in materialized
    assert "Produto A2" not in materialized


def test_new_store_generation_prunes_stale_generated_sku_and_rollback_restores_it(
    hub_root: Path,
):
    first = publish_store_sku_generation(
        "tenant-a",
        STORE_A,
        canonical_documents={
            "160-K": _canonical("160-K", "A"),
            "161-K": _canonical("161-K", "B"),
        },
        store_guidance={},
        sku_guidance={},
        bindings=[
            {"item_id": "MLB100", "sku": "160-K"},
            {"item_id": "MLB101", "sku": "161-K"},
        ],
        info_root=hub_root,
    )
    assert first["stats"]["store_guidance_count"] == 0
    stale_path = (
        hub_root / "tenant-a" / "ContextVault" / "70_Gerado" / "Lojas"
        / "b1e5a6efb16c0db69bba1836--jk-pecas" / "SKUs" / "161-K" / "Contexto.md"
    )
    assert stale_path.is_file()
    publish_store_sku_generation(
        "tenant-a",
        STORE_A,
        canonical_documents={"160-K": _canonical("160-K", "A2")},
        store_guidance={},
        sku_guidance={},
        bindings=[{"item_id": "MLB100", "sku": "160-K"}],
        info_root=hub_root,
    )
    assert not stale_path.exists()
    rollback_store_sku_generation(
        "tenant-a", STORE_A, first["generation_id"], info_root=hub_root,
    )
    assert stale_path.is_file()
    assert "Produto B" in stale_path.read_text(encoding="utf-8")


def test_store_name_change_never_leaves_an_old_generated_context_branch(hub_root: Path):
    first = _publish(hub_root, STORE_A, sku="160-K", item="MLB100", marker="A1")
    generated = hub_root / "tenant-a" / "ContextVault" / "70_Gerado" / "Lojas"
    old_context = generated / "b1e5a6efb16c0db69bba1836--jk-pecas" / "SKUs" / "160-K" / "Contexto.md"
    renamed_store = {**STORE_A, "store_name": "JK Pecas Renomeada"}
    _publish(hub_root, renamed_store, sku="160-K", item="MLB100", marker="A2")
    new_context = generated / "b1e5a6efb16c0db69bba1836--jk-pecas-renomeada" / "SKUs" / "160-K" / "Contexto.md"

    assert not old_context.exists()
    assert new_context.is_file()
    rollback_store_sku_generation(
        "tenant-a", renamed_store, first["generation_id"], info_root=hub_root,
    )
    assert old_context.is_file()
    assert not new_context.exists()


def test_global_publish_candidate_rehydrates_isolated_store_notes_without_hash_collision(
    hub_root: Path,
):
    _publish(hub_root, STORE_A, sku="160-K", item="MLB100", marker="A")
    paths = _tenant_paths("tenant-a", info_root=hub_root)
    generation_id = "a" * 32
    snapshot = paths.generations_dir / generation_id / "70_Gerado"
    global_note = snapshot / "Mapas" / "Global.md"
    global_note.parent.mkdir(parents=True)
    global_note.write_text("# Geracao global\n", encoding="utf-8")
    expected_global_attestation = capture_generation_materialization(snapshot)

    temporary, _backup = _copy_publish_candidate(paths, generation_id)
    store_note = (
        temporary / "Lojas" / "b1e5a6efb16c0db69bba1836--jk-pecas"
        / "SKUs" / "160-K" / "Contexto.md"
    )
    assert store_note.is_file()
    assert "Produto A" in store_note.read_text(encoding="utf-8")
    assert capture_generation_materialization(temporary) == expected_global_attestation


def test_publication_is_idempotent_and_rejects_legacy_1599_or_oversize(hub_root: Path):
    first = _publish(hub_root, STORE_A, sku="160-K", item="MLB100", marker="A")
    second = _publish(hub_root, STORE_A, sku="160-K", item="MLB100", marker="A")
    assert first["changed"] is True
    assert second["changed"] is False
    assert second["generation_id"] == first["generation_id"]

    with pytest.raises(context_hub.ContextHubValidationError, match="1599"):
        _publish(hub_root, STORE_B, sku="1599", item="MLB200", marker="LEGACY")
    too_large = _canonical("BIG", "X")
    too_large["caracteristicas_tecnicas"]["itens"] = ["x" * (CANONICAL_DOCUMENT_MAX_CHARS + 1)]
    with pytest.raises(context_hub.ContextHubValidationError, match="canonical_document_too_large"):
        publish_store_sku_generation(
            "tenant-a",
            STORE_B,
            canonical_documents={"BIG": too_large},
            store_guidance={},
            sku_guidance={},
            bindings=[{"item_id": "MLB201", "sku": "BIG"}],
            info_root=hub_root,
        )


def test_publication_preserves_approved_guidance_between_four_and_eight_thousand_chars(
    hub_root: Path,
):
    guidance = {"orientacoes_perguntas": "g" * 6_200}
    published = publish_store_sku_generation(
        "tenant-a",
        STORE_A,
        canonical_documents={"160-K": _canonical("160-K", "A")},
        store_guidance=guidance,
        sku_guidance={},
        bindings=[{"item_id": "MLB100", "sku": "160-K"}],
        info_root=hub_root,
    )
    assert published["changed"] is True
    loaded = load_store_sku_knowledge(
        "tenant-a", _identity(STORE_A, sku="160-K", item="MLB100"),
        info_root=hub_root,
    )
    assert loaded["guidance"]["general"] == guidance

    with pytest.raises(context_hub.ContextHubValidationError, match="applicable_guidance_too_large"):
        publish_store_sku_generation(
            "tenant-a",
            STORE_B,
            canonical_documents={"BIG": _canonical("BIG", "B")},
            store_guidance={"orientacoes_perguntas": "g" * APPLICABLE_GUIDANCE_MAX_CHARS},
            sku_guidance={},
            bindings=[{"item_id": "MLB200", "sku": "BIG"}],
            info_root=hub_root,
        )


def test_publication_rejects_a_stale_expected_active_generation(hub_root: Path):
    first = _publish(hub_root, STORE_A, sku="160-K", item="MLB100", marker="A1")
    _publish(hub_root, STORE_A, sku="160-K", item="MLB100", marker="A2")

    with pytest.raises(context_hub.ContextHubConflictError, match="geracao ativa"):
        publish_store_sku_generation(
            "tenant-a",
            STORE_A,
            canonical_documents={"160-K": _canonical("160-K", "A3")},
            store_guidance={},
            sku_guidance={},
            bindings=[{"item_id": "MLB100", "sku": "160-K"}],
            expected_active_generation_id=first["generation_id"],
            info_root=hub_root,
        )


def test_compiler_requires_complete_catalog_and_copies_notes_only_to_confirmed_sku():
    catalog = {
        "store_id": STORE_A["store_ref"],
        "store_name": STORE_A["store_name"],
        "seller_id": STORE_A["seller_id"],
        "coverage_complete": True,
        "cancelled": False,
        "items": [{
            "sku": "160-K",
            "fields": {"site_id_ml": "MLB"},
            "listings": [{"mlb": "MLB100"}],
            "conflicts": {"title": ["Titulo A", "Titulo B"]},
        }],
    }
    plan = compile_store_sku_knowledge(
        "tenant-a",
        STORE_A,
        catalog,
        canonical_documents={
            "160-K": _canonical("160-K", "A"),
            "OUTRO": _canonical("OUTRO", "OUTRO"),
        },
        store_guidance={"orientacoes_perguntas": "Geral"},
        legacy_sku_guidance={
            "160-K": {"notas": "Valida"},
            "OUTRO": {"notas": "Nao copiar"},
        },
        quarantined_legacy={"1599": {"notas": "Sem identidade"}},
    )
    assert set(plan.canonical_documents) == {"160-K"}
    assert set(plan.sku_guidance) == {"160-K"}
    assert plan.quarantined_legacy == {"1599": {"notas": "Sem identidade"}}
    assert plan.report["quarantined_legacy_count"] == 1
    assert plan.report["skipped_by_code"]["non_binding_conflict_reported"] == 1

    with pytest.raises(context_hub.ContextHubValidationError, match="incompleto"):
        compile_store_sku_knowledge(
            "tenant-a",
            STORE_A,
            {**catalog, "coverage_complete": False},
            canonical_documents={"160-K": _canonical("160-K", "A")},
            store_guidance={},
            legacy_sku_guidance={},
        )

    partial = compile_store_sku_knowledge(
        "tenant-a",
        STORE_A,
        {
            **catalog,
            "coverage_complete": False,
            "warnings": ["provider gap"],
            "skipped": [{"reason": "listing_details_missing"}],
        },
        canonical_documents={"160-K": _canonical("160-K", "A")},
        store_guidance={},
        legacy_sku_guidance={},
        allow_partial_catalog=True,
    )
    assert partial.report["coverage_complete"] is False
    assert partial.report["partial_catalog"] is True
    assert partial.report["catalog_warning_count"] == 1
    assert partial.report["catalog_skipped_count"] == 1
    assert partial.bindings == [{
        "schema": "jk_context_store_sku_binding_v1",
        "item_id": "MLB100",
        "variation_id": "",
        "sku": "160-K",
    }]

    with pytest.raises(context_hub.ContextHubValidationError, match="incompleto"):
        compile_store_sku_knowledge(
            "tenant-a",
            STORE_A,
            {**catalog, "coverage_complete": False, "cancelled": True},
            canonical_documents={"160-K": _canonical("160-K", "A")},
            store_guidance={},
            legacy_sku_guidance={},
            allow_partial_catalog=True,
        )

    with pytest.raises(context_hub.ContextHubValidationError, match="seller_id"):
        compile_store_sku_knowledge(
            "tenant-a",
            STORE_A,
            {**catalog, "seller_id": STORE_B["seller_id"]},
            canonical_documents={"160-K": _canonical("160-K", "A")},
            store_guidance={},
            legacy_sku_guidance={},
        )

    with pytest.raises(context_hub.ContextHubValidationError, match="site_id"):
        compile_store_sku_knowledge(
            "tenant-a",
            STORE_A,
            {**catalog, "site_id": "MLA"},
            canonical_documents={"160-K": _canonical("160-K", "A")},
            store_guidance={},
            legacy_sku_guidance={},
        )

    without_item_site = {
        **catalog,
        "items": [{**catalog["items"][0], "fields": {}}],
    }
    with pytest.raises(context_hub.ContextHubValidationError, match="identidade completa"):
        compile_store_sku_knowledge(
            "tenant-a",
            STORE_A,
            without_item_site,
            canonical_documents={"160-K": _canonical("160-K", "A")},
            store_guidance={},
            legacy_sku_guidance={},
        )


def test_compiler_blocks_same_item_variation_bound_to_distinct_skus():
    catalog = {
        "store_id": STORE_A["store_ref"],
        "store_name": STORE_A["store_name"],
        "seller_id": STORE_A["seller_id"],
        "coverage_complete": True,
        "cancelled": False,
        "items": [
            {
                "sku": "SKU-A",
                "fields": {"site_id_ml": "MLB"},
                "listings": [{"mlb": "MLB100", "variation_id": "123"}],
            },
            {
                "sku": "SKU-B",
                "fields": {"site_id_ml": "MLB"},
                "listings": [{"mlb": "MLB100", "variation_id": "123"}],
            },
        ],
    }
    with pytest.raises(context_hub.ContextHubValidationError, match="SKUs distintos"):
        compile_store_sku_knowledge(
            "tenant-a",
            STORE_A,
            catalog,
            canonical_documents={
                "SKU-A": _canonical("SKU-A", "A"),
                "SKU-B": _canonical("SKU-B", "B"),
            },
            store_guidance={},
            legacy_sku_guidance={},
        )


def test_migration_preview_is_read_only_and_apply_is_restartable(hub_root: Path):
    tenant = hub_root / "tenant-a"
    sku_dir = tenant / "SKU"
    sku_dir.mkdir(parents=True)
    (sku_dir / "160-K.json").write_text(
        json.dumps(_canonical("160-K", "A"), ensure_ascii=False), encoding="utf-8",
    )
    (tenant / "ia_treinamento_perguntas_pos_venda.json").write_text(
        json.dumps({
            "por_loja": {f"store_id:{STORE_A['store_ref']}": {
                "orientacoes_perguntas": "Orientacao aprovada",
                "notas_sku": {
                    "160-K": {"notas": "Nota aprovada"},
                    "1599": {"notas": "Registro orfao"},
                },
                "exemplos": {"perguntas_anuncio": [{
                    "sku": "160-K", "pergunta": "Aplicacao?", "resposta": "Aplicacao A",
                }]},
            }},
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    def collector(_client: str, _store: str):
        return {
            "store_id": STORE_A["store_ref"],
            "store_name": STORE_A["store_name"],
            "seller_id": STORE_A["seller_id"],
            "coverage_complete": True,
            "cancelled": False,
            "items": [{
                "sku": "160-K",
                "fields": {"site_id_ml": "MLB"},
                "listings": [{"mlb": "MLB100"}],
                "conflicts": {},
            }],
        }

    preview = migrate_store_sku_knowledge(
        "tenant-a", targets=[STORE_A], catalog_collector=collector,
        apply=False, info_root=hub_root,
    )
    assert preview["contract"] == STORE_SKU_MIGRATION_SCHEMA
    assert preview["ready_count"] == 1
    assert not (tenant / "context_hub" / "context_hub.db").exists()

    first = migrate_store_sku_knowledge(
        "tenant-a", targets=[STORE_A], catalog_collector=collector,
        apply=True, info_root=hub_root,
    )
    second = migrate_store_sku_knowledge(
        "tenant-a", targets=[STORE_A], catalog_collector=collector,
        apply=True, info_root=hub_root,
    )
    assert first["applied_count"] == 1
    assert first["results"][0]["changed"] is True
    assert second["results"][0]["idempotent"] is True
    loaded = load_store_sku_knowledge(
        "tenant-a", _identity(STORE_A, sku="160-K", item="MLB100"), info_root=hub_root,
    )
    assert loaded["guidance"]["general"] == {"orientacoes_perguntas": "Orientacao aprovada"}
    assert loaded["guidance"]["sku"] == {
        "notas": "Nota aprovada",
        "exemplos_perguntas": [{
            "sku": "160-K", "pergunta": "Aplicacao?", "resposta": "Aplicacao A",
        }],
    }
    assert (
        tenant / "ContextVault" / "90_Arquivo" / "Quarentena"
        / "SKUs-sem-identidade" / "1599.md"
    ).is_file()


def test_migration_partial_catalog_requires_explicit_opt_in(hub_root: Path):
    tenant = hub_root / "tenant-a"
    sku_dir = tenant / "SKU"
    sku_dir.mkdir(parents=True)
    (sku_dir / "160-K.json").write_text(
        json.dumps(_canonical("160-K", "A"), ensure_ascii=False), encoding="utf-8",
    )

    def collector(_client: str, _store: str):
        return {
            "store_id": STORE_A["store_ref"],
            "seller_id": STORE_A["seller_id"],
            "coverage_complete": False,
            "cancelled": False,
            "warnings": ["one missing page"],
            "skipped": [{"reason": "listing_details_missing"}],
            "items": [{
                "sku": "160-K",
                "fields": {"site_id_ml": "MLB"},
                "listings": [{"mlb": "MLB100"}],
            }],
        }

    blocked = migrate_store_sku_knowledge(
        "tenant-a", targets=[STORE_A], catalog_collector=collector,
        apply=False, info_root=hub_root,
    )
    assert blocked["ready_count"] == 0
    assert blocked["errors"][0]["reason_code"] == "catalog_incomplete"

    preview = migrate_store_sku_knowledge(
        "tenant-a", targets=[STORE_A], catalog_collector=collector,
        allow_partial_catalog=True, apply=False, info_root=hub_root,
    )
    assert preview["ready_count"] == 1
    assert preview["partial_catalog_allowed"] is True
    assert preview["stores"][0]["partial_catalog"] is True
    assert not (tenant / "context_hub" / "context_hub.db").exists()

    applied = migrate_store_sku_knowledge(
        "tenant-a", targets=[STORE_A], catalog_collector=collector,
        allow_partial_catalog=True, apply=True, info_root=hub_root,
    )
    assert applied["applied_count"] == 1
    assert applied["stores"][0]["coverage_complete"] is False


def test_migration_preview_reports_safe_site_divergence_code(hub_root: Path):
    sku_dir = hub_root / "tenant-a" / "SKU"
    sku_dir.mkdir(parents=True)
    (sku_dir / "160-K.json").write_text(
        json.dumps(_canonical("160-K", "A"), ensure_ascii=False),
        encoding="utf-8",
    )

    def collector(_client: str, _store: str):
        return {
            "store_id": STORE_A["store_ref"],
            "seller_id": STORE_A["seller_id"],
            "site_id": "MLA",
            "coverage_complete": True,
            "cancelled": False,
            "items": [],
        }

    preview = migrate_store_sku_knowledge(
        "tenant-a",
        targets=[STORE_A],
        catalog_collector=collector,
        apply=False,
        info_root=hub_root,
    )
    assert preview["ready_count"] == 0
    assert preview["blocked_count"] == 1
    assert preview["errors"] == [{
        "store_ref_hash": preview["errors"][0]["store_ref_hash"],
        "error_type": "ContextHubValidationError",
        "reason_code": "catalog_site_mismatch",
    }]


def test_migration_validates_quarantine_dlp_before_apply(hub_root: Path):
    tenant = hub_root / "tenant-a"
    sku_dir = tenant / "SKU"
    sku_dir.mkdir(parents=True)
    (sku_dir / "160-K.json").write_text(
        json.dumps(_canonical("160-K", "A"), ensure_ascii=False),
        encoding="utf-8",
    )
    (tenant / "ia_treinamento_perguntas_pos_venda.json").write_text(
        json.dumps({"por_loja": {f"store_id:{STORE_A['store_ref']}": {
            "notas_sku": {"1599": {"notas": "api_key:abcdef123456"}},
        }}}),
        encoding="utf-8",
    )

    def collector(_client: str, _store: str):
        return {
            "store_id": STORE_A["store_ref"],
            "seller_id": STORE_A["seller_id"],
            "coverage_complete": True,
            "cancelled": False,
            "items": [{
                "sku": "160-K",
                "fields": {"site_id_ml": "MLB"},
                "listings": [{"mlb": "MLB100"}],
            }],
        }

    preview = migrate_store_sku_knowledge(
        "tenant-a",
        targets=[STORE_A],
        catalog_collector=collector,
        apply=False,
        info_root=hub_root,
    )
    assert preview["ready_count"] == 0
    assert preview["errors"][0]["reason_code"] == "validation_failed"
    assert not (tenant / "context_hub" / "context_hub.db").exists()


def test_store_sku_components_have_explicit_size_and_function_budgets():
    project = Path(__file__).resolve().parents[1]
    budgets = {
        "store_sku_contracts.py": 220,
        "store_sku_compiler.py": 420,
        "store_sku_migration.py": 380,
        "obsidian_store_sku_index.py": 140,
        "store_sku_repository.py": 620,
        "store_sku_repository_db.py": 460,
        "store_sku_repository_support.py": 640,
    }
    module_root = project / "backend" / "modules" / "context_hub"
    for name, line_budget in budgets.items():
        source = (module_root / name).read_text(encoding="utf-8")
        assert len(source.splitlines()) <= line_budget, name
        tree = ast.parse(source)
        function_sizes = [
            node.end_lineno - node.lineno + 1
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.end_lineno is not None
        ]
        assert max(function_sizes, default=0) <= 120, name

    repository_source = (module_root / "store_sku_repository.py").read_text(encoding="utf-8")
    assert "store_sku_migration" not in repository_source
    assert "store_sku_compiler" not in repository_source
