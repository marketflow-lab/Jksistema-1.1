"""OAuth refresh must not restart a consistent Medias sales calculation."""

import copy
import json
import sqlite3
import time

import pytest

from backend.services import medias_compras_historico as historico
from backend.services.medias_compras_cache import ResultadoCache
from backend.services.medias_compras_fontes import capturar_fontes, fontes_consistentes
from backend.services.store_public_snapshot import build_snapshot, write_snapshot


def _persist(root, stores):
    for name in ("lojas_config.json", "lojas_config.json.bak"):
        pending = root / (name + ".tmp")
        pending.write_text(json.dumps(stores), encoding="utf-8")
        pending.replace(root / name)


@pytest.fixture
def source(tmp_path):
    stores = [{
        "nome": "Loja A", "store_id": "store-a", "nomes_anteriores": ["Loja Antiga"],
        "site_id": "MLB", "_sync_version": 1, "_sync_updated_at": "before",
        "integracoes": {
            "bling": {
                "id": "app-bling", "secret": "synthetic-secret",
                "oauth_connection_id": "authorization-a", "connected": True,
                "access_token": "synthetic-old-access", "refresh_token": "synthetic-old-refresh",
                "updated_at": "before", "_sync_version": 1, "_sync_updated_at": "before",
            },
            "mercadolivre": {
                "app_id": "app-ml", "client_secret": "synthetic-secret",
                "user_id": "seller-a", "site_id": "MLB", "connected": True,
                "access_token": "synthetic-old-access", "refresh_token": "synthetic-old-refresh",
                "updated_at": "before", "_sync_version": 1, "_sync_updated_at": "before",
            },
        },
    }]
    _persist(tmp_path, stores)
    write_snapshot(tmp_path, build_snapshot(stores))
    database = tmp_path / "vendas_historico.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE vendas (id_unico TEXT, sku TEXT, data TEXT, produto TEXT, quantidade REAL)")
        conn.execute("INSERT INTO vendas VALUES ('sale-a','SKU-A','2026-10-10','Produto A',2)")

    def capture():
        return capturar_fontes(tmp_path, inventariar_fotos=False,
            snapshot={"db_paths": (str(database),), "escopo": {"loja": "__todas", "store_id": ""}})

    before = capture()
    historico.ler_historico_vendas(
        client_id="tenant-rotation", loja="__todas", store_id="", db_paths=(str(database),),
        meses=6, fim_periodo="2026-11-11", filtro_sql="", filtro_params=(),
        normalizar_sku=str, titulo_valido=str, snapshot=before.snapshot,
    )
    return tmp_path, stores, database, before, capture


def _rotate(stores, provider):
    updated = copy.deepcopy(stores)
    updated[0].update(_sync_version=2, _sync_updated_at="after")
    updated[0]["integracoes"][provider].update(
        access_token="synthetic-new-access", refresh_token="synthetic-new-refresh",
        updated_at="after", _sync_version=2, _sync_updated_at="after",
    )
    return updated


@pytest.mark.parametrize("provider", ["bling", "mercadolivre"])
def test_credential_rotation_preserves_calculation_and_physical_revision(source, provider):
    root, stores, _, before, capture = source
    _persist(root, _rotate(stores, provider))
    after = capture()
    assert after.assinatura != before.assinatura
    assert after.autoridade == before.autoridade
    assert fontes_consistentes(before, after)
    # The private snapshot retains digests only, never canonical documents.
    revisions = after.snapshot["lojas_revisoes_logicas"]
    assert len(revisions) == 2 and all(isinstance(value, str) and len(value) == 64 for value in revisions.values())
    assert "synthetic" not in repr(revisions)


def test_result_owner_publishes_first_calculation_during_credential_rotation(source):
    root, stores, _, before, capture = source
    calls = []

    def load(_source):
        calls.append(True)
        _persist(root, _rotate(stores, "bling"))
        return {"itens": [{"sku": "SKU-A", "total_vendas_periodo": 2}]}

    value, final = ResultadoCache()._calculate(capture, load, time.perf_counter(), before)
    assert len(calls) == 1
    assert value["itens"][0]["total_vendas_periodo"] == 2
    assert final.assinatura == capture().assinatura


@pytest.mark.parametrize("provider,field,value", [
    ("bling", "oauth_connection_id", "other-authorization"),
    ("bling", "id", "other-app"),
    ("bling", "secret", "other-secret"),
    ("bling", "connected", False),
    ("bling", "access_token", ""),
    ("mercadolivre", "user_id", "other-seller"),
    ("mercadolivre", "site_id", "MLA"),
    ("mercadolivre", "app_id", "other-app"),
    ("mercadolivre", "refresh_token", ""),
])
def test_connection_and_access_changes_still_reject_calculation(source, provider, field, value):
    root, stores, _, before, capture = source
    updated = _rotate(stores, provider)
    updated[0]["integracoes"][provider][field] = value
    _persist(root, updated)
    assert not fontes_consistentes(before, capture())


@pytest.mark.parametrize("field,value", [
    ("store_id", "store-b"), ("nome", "Outro nome"),
    ("nomes_anteriores", ["Outro alias"]), ("policy", {"shared": True}),
])
def test_store_aliases_identity_and_unknown_policy_fields_still_reject(source, field, value):
    root, stores, _, before, capture = source
    updated = _rotate(stores, "bling")
    updated[0][field] = value
    _persist(root, updated)
    assert not fontes_consistentes(before, capture())


@pytest.mark.parametrize("provider,identity", [("bling", "oauth_connection_id"), ("mercadolivre", "user_id")])
def test_legacy_connection_without_stable_identity_keeps_physical_fence(source, provider, identity):
    root, stores, _, _, capture = source
    unbound = copy.deepcopy(stores)
    unbound[0]["integracoes"][provider].pop(identity)
    _persist(root, unbound)
    before = capture()
    _persist(root, _rotate(unbound, provider))
    assert not fontes_consistentes(before, capture())


@pytest.mark.parametrize("provider", ["bling", "mercadolivre"])
@pytest.mark.parametrize("invalid_token", [True, None, ["not-a-token"]])
def test_invalid_token_types_cannot_collide_with_private_redaction(source, provider, invalid_token):
    root, stores, _, before, capture = source
    invalid = copy.deepcopy(stores)
    cfg = invalid[0]["integracoes"][provider]
    cfg.update(access_token=invalid_token, refresh_token=invalid_token)
    cfg.pop("updated_at")
    _persist(root, invalid)
    after = capture()
    assert not fontes_consistentes(before, after)
    assert all(value is None for value in after.snapshot["lojas_revisoes_logicas"].values())


def test_rotation_does_not_hide_new_sales_or_publication_journal(source):
    root, stores, database, before, capture = source
    _persist(root, _rotate(stores, "bling"))
    with sqlite3.connect(database) as conn:
        conn.execute("INSERT INTO vendas VALUES ('sale-b','SKU-A','2026-10-11','Produto A',3)")
    assert not fontes_consistentes(before, capture())
    journal = root / "_stores_publication" / "transaction.json"
    journal.parent.mkdir()
    journal.write_text("{}", encoding="utf-8")
    assert not fontes_consistentes(before, capture())


@pytest.mark.parametrize("content", ['{broken', '{"duplicate":1,"duplicate":2}', '[1]'])
def test_invalid_store_file_preserves_strict_physical_fence(source, content):
    root, _, _, before, capture = source
    (root / "lojas_config.json").write_text(content, encoding="utf-8")
    after = capture()
    assert not fontes_consistentes(before, after)
    assert any(value is None for value in after.snapshot["lojas_revisoes_logicas"].values())


def test_change_during_store_capture_cannot_authorize_equivalence(source, monkeypatch):
    root, stores, _, before, capture = source
    _persist(root, _rotate(stores, "bling"))
    from backend.services import medias_compras_fontes as fontes
    original = fontes.assinatura_arquivo
    calls = {}

    def changing(path, entry=None):
        result = original(path, entry)
        if path.name == "lojas_config.json":
            calls[path] = calls.get(path, 0) + 1
            if calls[path] == 2:
                return (*result[:-1], "changed-during-capture")
        return result

    monkeypatch.setattr(fontes, "assinatura_arquivo", changing)
    assert not fontes_consistentes(before, capture())
