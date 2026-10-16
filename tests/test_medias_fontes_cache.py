import json
import os
from types import SimpleNamespace

import pytest

from backend.services.medias_compras_cache import Fontes
from backend.services.medias_compras_fontes import assinatura_arquivo, capturar_fontes, categorias_alteradas, digest_lista


def test_revision_diagnostics_use_fixed_categories_without_file_identity():
    private = "C:/private-tenant/private-user/"
    before = Fontes(("day-a", (
        (private + "lojas_config.json", 1), (private + "lojas_config.json.bak", 1),
        (private + "vendas_historico_private-store.db", 1),
        (private + "cadastro_fotos/private-product.png", 1),
        (private + "not-a-public-category.secret", 1),
    )), ("authority",))
    after = Fontes(("day-b", (
        (private + "lojas_config.json", 2), (private + "lojas_config.json.bak", 2),
        (private + "vendas_historico_private-store.db", 2),
        (private + "vendas_historico_private-store.db-wal", 2),
        (private + "not-a-public-category.secret", 2),
    )), ("authority",))
    assert categorias_alteradas(before, after) == {
        "calendario": 1, "fotos": 1, "lojas_config": 2, "outros": 1,
        "vendas_db": 1, "vendas_wal": 1,
    }
    assert categorias_alteradas(before, before) == {}


def test_directory_enumeration_metadata_does_not_create_a_false_revision(tmp_path):
    directory = tmp_path / "cadastro_fotos" / "lojas"
    directory.mkdir(parents=True)
    actual = directory.lstat()
    stale_enumeration = SimpleNamespace(
        st_mode=actual.st_mode, st_ctime_ns=actual.st_ctime_ns,
        st_mtime_ns=actual.st_mtime_ns - 1_000_000, st_size=actual.st_size,
        st_ino=0, st_file_attributes=getattr(actual, "st_file_attributes", 0),
    )
    assert assinatura_arquivo(directory, stale_enumeration) == assinatura_arquivo(directory)
    before = capturar_fontes(tmp_path)
    photo = directory / "001.png"
    photo.write_bytes(b"new")
    added = capturar_fontes(tmp_path)
    assert added.assinatura != before.assinatura
    photo.unlink()
    assert capturar_fontes(tmp_path).assinatura != added.assinatura


def test_inventory_wal_and_same_size_replacement_change_revision(tmp_path):
    path = tmp_path / "vendas_historico_a.db"
    path.write_bytes(b"version-a")
    first = capturar_fontes(tmp_path)
    (tmp_path / "vendas_historico_a.db-wal").write_bytes(b"wal")
    second = capturar_fontes(tmp_path)
    assert first.assinatura != second.assinatura
    original_time = path.stat().st_mtime_ns
    replacement = tmp_path / "replacement.tmp"
    replacement.write_bytes(b"version-b")
    os.replace(replacement, path)
    os.utime(path, ns=(original_time, original_time))
    third = capturar_fontes(tmp_path)
    assert second.assinatura != third.assinatura
    (tmp_path / "cadastro_produtos_backup_new.csv").write_text("sku\n002\n")
    assert third.assinatura != capturar_fontes(tmp_path).assinatura


def test_policy_store_and_target_are_hard_revisions_other_lists_are_not(tmp_path):
    first = capturar_fontes(tmp_path, detalhe=True, alvo=digest_lista({"id": "a", "itens": []}))
    (tmp_path / "listas_pedidos.json").write_text(json.dumps([{"id": "other"}]))
    second = capturar_fontes(tmp_path, detalhe=True, alvo=first.alvo)
    assert first == second
    (tmp_path / "cadastro_fotos_config.json").write_text('{"strict_store_scope":true}')
    assert second.autoridade != capturar_fontes(tmp_path, detalhe=True, alvo=first.alvo).autoridade
    assert digest_lista({"id": "a", "itens": []}) != digest_lista({"id": "a", "itens": [{"SKU": "001"}]})


def test_photo_policy_digest_excludes_result_scope_and_list_target(tmp_path):
    first = capturar_fontes(tmp_path, detalhe=True, escopo=("list-a",), alvo="target-a")
    second = capturar_fontes(tmp_path, detalhe=True, escopo=("list-b",), alvo="target-b")
    assert first.autoridade != second.autoridade
    assert first.politica_fotos == second.politica_fotos


def test_publication_and_sqlite_transactions_mark_unstable(tmp_path):
    publication = tmp_path / "_stores_publication" / "transaction.json"
    publication.parent.mkdir()
    publication.write_text("{}")
    assert capturar_fontes(tmp_path).pendente
    publication.unlink()
    assert not capturar_fontes(tmp_path).pendente
    (tmp_path / "vendas_historico_a.db-journal").write_bytes(b"writing")
    (tmp_path / "vendas_historico_a.db").write_bytes(b"database")
    assert capturar_fontes(tmp_path).pendente


def test_sync_progress_and_token_refresh_are_not_hard_invalidations(tmp_path):
    (tmp_path / "lojas_public_snapshot.json").write_text('{"generation":"same"}')
    (tmp_path / "lojas_config.json").write_text('{"token":"old"}')
    before = capturar_fontes(tmp_path)
    (tmp_path / "sync_progress.json").write_text('{"progress":1}')
    (tmp_path / "settings.json").write_text('{}')
    (tmp_path / "unrelated.db").write_bytes(b"noise")
    assert capturar_fontes(tmp_path) == before
    (tmp_path / "lojas_config.json").write_text('{"token":"new"}')
    after = capturar_fontes(tmp_path)
    assert after.assinatura != before.assinatura
    assert after.autoridade == before.autoridade


def test_photo_replacement_and_symlink_identity_are_fenced(tmp_path):
    photo = tmp_path / "cadastro_fotos" / "lojas" / "a" / "001.png"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"photo")
    first = capturar_fontes(tmp_path)
    photo.write_bytes(b"new-photo")
    assert capturar_fontes(tmp_path).assinatura != first.assinatura
    target = photo.with_name("002.png")
    target.write_bytes(b"second")
    link = photo.with_name("link.png")
    try:
        link.symlink_to(photo)
    except OSError:
        pytest.skip("Windows symlink permission unavailable")
    before = capturar_fontes(tmp_path)
    link.unlink()
    link.symlink_to(target)
    assert capturar_fontes(tmp_path).assinatura != before.assinatura
