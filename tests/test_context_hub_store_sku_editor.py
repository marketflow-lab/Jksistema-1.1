from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.modules.context_hub import api as hub
from backend.modules.context_hub.contracts import ContextHubValidationError
from backend.modules.context_hub.metadata import _dump_frontmatter, _parse_frontmatter
from backend.modules.context_hub.paths import _tenant_paths
from backend.modules.context_hub.store_sku_editor import (
    StoreGuidanceEditorConflict, load_store_guidance_editor, save_store_guidance_editor,
)
from backend.modules.context_hub.store_sku_repository import load_store_guidance, publish_store_sku_generation

STORE = {"store_ref": "b1e5a6efb16c0db69bba1836", "store_name": "Loja A", "seller_id": "123", "site_id": "MLB"}
OTHER = {"store_ref": "666955b171470f4fb6f6a4a8", "store_name": "Loja B", "seller_id": "456", "site_id": "MLB"}


@pytest.fixture()
def root(tmp_path):
    base, info = tmp_path / "app", tmp_path / "info"
    base.mkdir()
    info.mkdir()
    hub.configure_context_hub(base_dir=base, info_root=info, surface="test")
    return info


def load(root, store=STORE, tenant="editor-tenant"):
    return load_store_guidance_editor(tenant, store, info_root=root)


def save(root, text, *, sku="", revision=None, store=STORE, **fields):
    revision = revision if revision is not None else load(root, store)["editorial"]["revision"]
    return save_store_guidance_editor(
        "editor-tenant", store, guidance={"notas" if sku else "orientacoes_perguntas": text, **fields},
        sku=sku, expected_revision=revision, info_root=root,
    )


def note(root, snapshot, sku=""):
    entry = snapshot["editorial"]["skus"][sku] if sku else snapshot["editorial"]["general"]
    return _tenant_paths("editor-tenant", info_root=root).curated_dir / entry["relative_path"]


def publish(root):
    return publish_store_sku_generation(
        "editor-tenant", STORE, canonical_documents={"0007": {"sku": "0007", "nome": "Produto A"}},
        store_guidance={"orientacoes_perguntas": "Versao publicada"},
        sku_guidance={"0007": {"notas": "Nota publicada"}},
        bindings=[{"item_id": "MLB100", "sku": "0007"}], info_root=root,
    )


def test_editor_loads_external_change_and_preserves_published_version(root):
    publish(root)
    first = load(root)
    assert first["editorial"]["general"]["status"] == "published"
    target = note(root, first)
    target.write_text(target.read_text(encoding="utf-8").replace("Versao publicada", "Versao do Obsidian"), encoding="utf-8")
    changed = load(root)
    assert changed["guidance"]["general"]["orientacoes_perguntas"] == "Versao do Obsidian"
    assert changed["editorial"]["general"]["status"] == "draft"
    assert changed["editorial"]["revision"] != first["editorial"]["revision"]
    assert changed["published_guidance"]["guidance"]["general"]["orientacoes_perguntas"] == "Versao publicada"
    assert load_store_guidance("editor-tenant", STORE, info_root=root)["guidance"]["general"]["orientacoes_perguntas"] == "Versao publicada"


def test_stale_revision_cannot_overwrite_external_edit(root):
    first = save(root, "Primeira")
    target = note(root, first)
    target.write_text(target.read_text(encoding="utf-8").replace("Primeira", "Externa"), encoding="utf-8")
    external = target.read_bytes()
    with pytest.raises(StoreGuidanceEditorConflict):
        save(root, "Desatualizada", revision=first["editorial"]["revision"])
    assert target.read_bytes() == external


def test_changes_during_preparation_are_caught(root, monkeypatch):
    import backend.modules.context_hub.store_sku_editor as editor
    first = save(root, "Primeira")
    target = note(root, first)
    original = editor._updated_content

    def concurrent(raw, value, sku):
        target.write_text(raw.replace("Primeira", "Externa"), encoding="utf-8")
        return original(raw, value, sku)

    monkeypatch.setattr(editor, "_updated_content", concurrent)
    with pytest.raises(StoreGuidanceEditorConflict):
        save(root, "Desatualizada", revision=first["editorial"]["revision"])
    assert "Externa" in target.read_text(encoding="utf-8")


def test_save_one_sku_preserves_general_other_sku_and_custom_markdown(root):
    general = save(root, "Geral", contexto_loja="Contexto")
    general_bytes = note(root, general).read_bytes()
    one = save(root, "Nota um", sku="0007")
    path_one = note(root, one, "0007")
    original = path_one.read_text(encoding="utf-8")
    original = original.replace("managed: false", "managed: false\n# Comentario particular\ncustom_label: manter")
    original += "\n## Referencias\nConservar este paragrafo.\n"
    path_one.write_text(original, encoding="utf-8")
    two = save(root, "Nota dois", sku="0008")
    two_bytes = note(root, two, "0008").read_bytes()
    result = save(root, "  Nota um alterada\ncom segunda linha  ", sku="0007")
    assert note(root, general).read_bytes() == general_bytes
    assert note(root, two, "0008").read_bytes() == two_bytes
    current = path_one.read_text(encoding="utf-8")
    assert "# Comentario particular\ncustom_label: manter" in current
    assert "## Referencias\nConservar este paragrafo." in current
    assert result["sku_guidance"]["0007"]["notas"] == "  Nota um alterada\ncom segunda linha  "
    assert result["generation_id"] == ""  # A draft need not wait for a catalog generation.


def test_rename_and_move_find_identity_without_creating_another_note(root):
    first = save(root, "Original")
    source = note(root, first)
    renamed_store = {**STORE, "store_name": "Nome novo"}
    assert load(root, renamed_store)["guidance"]["general"]["orientacoes_perguntas"] == "Original"
    moved = source.parents[2] / "Editorial" / "Nota movida.md"
    moved.parent.mkdir()
    source.rename(moved)
    result = save(root, "Nova", store=renamed_store)
    assert note(root, result) == moved
    assert "Nova" in moved.read_text(encoding="utf-8")


def test_duplicate_identity_is_visible_and_blocks_save(root):
    first = save(root, "Original")
    original = note(root, first)
    duplicate = original.with_name("Copia.md")
    duplicate.write_bytes(original.read_bytes())
    snapshot = load(root)
    assert snapshot["editorial"]["general"]["status"] == "conflict"
    assert snapshot["guidance"]["general"] == {}
    with pytest.raises(ContextHubValidationError):
        save(root, "Nao escolher vencedor")


def test_invalid_json_and_deleted_note_do_not_hide_as_empty_store(root):
    publish(root)
    first = load(root)
    target = note(root, first)
    target.write_text(target.read_text(encoding="utf-8").replace('"Versao publicada"', 'INVALID'), encoding="utf-8")
    invalid = load(root)
    assert invalid["editorial"]["general"]["status"] == "invalid"
    with pytest.raises(ContextHubValidationError):
        save(root, "Nao sobrescrever invalido")
    target.unlink()
    deleted = load(root)
    assert deleted["editorial"]["general"]["status"] == "deleted"
    assert deleted["guidance"]["general"] == {}
    assert deleted["published_guidance"]["guidance"]["general"]


def test_tenant_store_and_seller_isolation(root):
    save(root, "Somente A", sku="0007")
    save(root, "Somente B", sku="0007", store=OTHER)
    assert load(root)["sku_guidance"]["0007"]["notas"] == "Somente A"
    assert load(root, OTHER)["sku_guidance"]["0007"]["notas"] == "Somente B"
    assert load(root, tenant="other-tenant")["sku_guidance"] == {}
    wrong_seller = load(root, {**STORE, "seller_id": "999"})
    assert wrong_seller["sku_guidance"] == {}
    assert "Somente A" not in json.dumps(wrong_seller)


def test_plain_markdown_is_editorial_and_can_be_edited(root):
    first = save(root, "Original")
    target = note(root, first)
    metadata, _ = _parse_frontmatter(target.read_text(encoding="utf-8"))
    target.write_text(_dump_frontmatter(metadata, "# Orientacao\n\nTexto escrito no Obsidian."), encoding="utf-8")
    current = load(root)
    assert "Texto escrito no Obsidian." in current["guidance"]["general"]["orientacoes_perguntas"]
    assert save(root, "Novo texto")["guidance"]["general"]["orientacoes_perguntas"].strip() == "Novo texto"


def test_missing_revision_and_generation_change_conflict(root):
    first = load(root)
    with pytest.raises(StoreGuidanceEditorConflict):
        save(root, "Sem revisao", revision="")
    publish(root)
    with pytest.raises(StoreGuidanceEditorConflict):
        save(root, "Revisao antiga", revision=first["editorial"]["revision"])


def test_invalid_metadata_cannot_be_overwritten(root):
    first = save(root, "Original")
    target = note(root, first)
    target.write_text("---\nmalformed: [\n---\nConteudo preservar", encoding="utf-8")
    before = target.read_bytes()
    assert load(root)["editorial"]["general"]["status"] == "invalid"
    with pytest.raises(ContextHubValidationError):
        save(root, "Nao apagar")
    assert target.read_bytes() == before


@pytest.mark.parametrize("sku", ["", "0007"])
def test_legacy_json_alias_is_normalized_without_losing_body(root, sku):
    first = save(root, "Original", sku=sku)
    target = note(root, first, sku)
    field = "notas" if sku else "orientacoes_perguntas"
    target.write_text(target.read_text(encoding="utf-8").replace('"' + field + '"', '"orientacoes"') + "\n## Complemento\nTexto externo.\n", encoding="utf-8")
    snapshot = load(root)
    value = snapshot["sku_guidance"][sku] if sku else snapshot["guidance"]["general"]
    entry = snapshot["editorial"]["skus"][sku] if sku else snapshot["editorial"]["general"]
    assert value[field] == "Original"
    assert "orientacoes" not in value
    assert "Texto externo." in entry["source_body"]
    assert snapshot["editorial"]["revision"] == load(root)["editorial"]["revision"]


def test_invalid_scope_and_dlp_do_not_expose_note_body(root):
    first = save(root, "Original")
    target = note(root, first)
    original = target.read_text(encoding="utf-8")
    target.write_text(original.replace("scope_kind: store", "scope_kind: store_sku"), encoding="utf-8")
    entry = load(root)["editorial"]["general"]
    assert entry["status"] == "invalid"
    assert "source_body" not in entry
    target.write_text(original + "\nAuthorization: Bearer secret_token_1234567890\n", encoding="utf-8")
    entry = load(root)["editorial"]["general"]
    assert entry["status"] == "invalid"
    assert "source_body" not in entry


def test_plain_body_whitespace_and_explicit_clear_are_preserved(root):
    first = save(root, "Original")
    target = note(root, first)
    metadata, _ = _parse_frontmatter(target.read_text(encoding="utf-8"))
    content = _dump_frontmatter(metadata, "temporary")
    target.write_text(content.replace("temporary", "  Texto com espacos  \n\nFinal  "), encoding="utf-8")
    value = load(root)["guidance"]["general"]["orientacoes_perguntas"]
    assert value == "  Texto com espacos  \n\nFinal  "
    changed = save(root, "  Novo texto  \n\nFinal novo  ")
    assert changed["guidance"]["general"]["orientacoes_perguntas"] == "  Novo texto  \n\nFinal novo  "
    assert save(root, "")["guidance"]["general"]["orientacoes_perguntas"] == ""


def test_legacy_text_alias_does_not_resurrect_cleared_sku_guidance(root):
    first = save(root, "Antiga", sku="0007")
    target = note(root, first, "0007")
    target.write_text(target.read_text(encoding="utf-8").replace('"notas"', '"texto"'), encoding="utf-8")
    assert load(root)["sku_guidance"]["0007"] == {"notas": "Antiga"}
    result = save(root, "", sku="0007")
    assert result["sku_guidance"]["0007"] == {"notas": ""}
    assert '"texto"' not in target.read_text(encoding="utf-8")


def test_catalog_sync_flag_is_independent_of_editorial_review_status(root):
    draft = save(root, "Geral antes do catalogo")
    assert draft["editorial"]["general"]["requires_catalog_sync"] is True
    assert draft["editorial"]["general"]["status"] == "draft"
    new_sku = save(root, "SKU novo antes da geracao", sku="0008")
    assert new_sku["editorial"]["skus"]["0008"]["requires_catalog_sync"] is True
    publish(root)
    snapshot = load(root)
    assert snapshot["published_guidance"]["canonical_skus"] == ["0007"]
    assert snapshot["editorial"]["general"]["requires_catalog_sync"] is False
    assert snapshot["editorial"]["skus"]["0007"]["requires_catalog_sync"] is False
    assert snapshot["editorial"]["skus"]["0007"]["status"] == "published"
    assert snapshot["editorial"]["skus"]["0008"]["requires_catalog_sync"] is True
    assert snapshot["editorial"]["skus"]["0008"]["status"] == "draft"
