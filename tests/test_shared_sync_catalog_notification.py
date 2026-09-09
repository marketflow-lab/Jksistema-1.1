import json
import threading
from pathlib import Path

import pytest

from backend.modules.context_hub import catalog_product_sync
from backend.services import shared_sync  # noqa: F401 - initialize shared runtime
from backend.services import cadastro_fotos, integracoes
from backend.services import shared_sync_apply_scope as apply
from backend.services import shared_sync_cadastro_transaction as transaction
from backend.services import shared_sync_merge_sqlite as merge
from backend.services.path_coordination import path_lock_for


@pytest.fixture
def catalog_import(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    stores = [{"store_id": "store-a", "nome": "Loja A", "integracoes": {}}]
    (tenant / "lojas_config.json").write_text(json.dumps(stores), encoding="utf-8")
    (tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).write_text(json.dumps({
        "schema": cadastro_fotos.CADASTRO_FOTOS_CONFIG_SCHEMA,
        "strict_store_scope": True, "shared_groups": [],
    }), encoding="utf-8")
    monkeypatch.setattr(apply, "get_tenant_path", lambda _: str(tenant), raising=False)
    monkeypatch.setattr(cadastro_fotos, "get_tenant_path", lambda _: str(tenant), raising=False)
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(tmp_path), raising=False)
    monkeypatch.setattr(cadastro_fotos, "_cadastro_carregar_lojas_foto", lambda _: stores)
    files = [
        ("cadastro_produtos_lojas.csv", (
            "store_id,sku,nome,row_version,updated_at_utc,deleted_at_utc\n"
            "store-a,001,Imported,1,2026-09-09T10:00:00Z,\n"
        ).encode("utf-8")),
        ("cadastro_produtos_meta.json", b'{"updated":true}'),
    ]
    monkeypatch.setattr(apply, "_shared_sync_read_validated_bundle", lambda *args: ({}, files))
    return tenant, files


@pytest.mark.parametrize("user_share", [False, True])
def test_catalog_notification_after_import_releases_transaction_and_locks(catalog_import, monkeypatch, user_share):
    tenant, _ = catalog_import
    notifications = []

    def notify(client_id, *, info_root):
        acquired = []

        def acquire_writer_locks():
            locks = [integracoes._LOJAS_CONFIG_LOCK,
                     path_lock_for(tenant / "cadastro_produtos_lojas.csv"),
                     path_lock_for(tenant / "cadastro_custos_lojas.csv")]
            for lock in locks:
                got = lock.acquire(timeout=1)
                acquired.append(got)
                if got:
                    lock.release()

        reader = threading.Thread(target=acquire_writer_locks)
        reader.start()
        reader.join(4)
        notifications.append((client_id, Path(info_root), transaction._active.get(), acquired,
                              (tenant / "cadastro_produtos_meta.json").read_bytes()))

    monkeypatch.setattr(catalog_product_sync, "notify_catalog_committed", notify)
    result = apply._shared_sync_aplicar_pacote(
        "000002", "cadastro", b"validated bundle", scope_config={"user_share": user_share})
    assert result["file_count"] == 2
    assert notifications == [("000002", tenant.parent, None, [True, True, True], b'{"updated":true}')]


@pytest.mark.parametrize("user_share", [False, True])
def test_catalog_notification_skips_import_that_rolls_back(catalog_import, monkeypatch, user_share):
    tenant, _ = catalog_import
    notifications = []
    monkeypatch.setattr(catalog_product_sync, "notify_catalog_committed",
                        lambda *args, **kwargs: notifications.append((args, kwargs)))
    module = merge if user_share else apply
    method = "_shared_sync_write_missing_file" if user_share else "_shared_sync_atomic_write"
    original = getattr(module, method)

    def fail_final_file(path, data):
        if Path(path).name == "cadastro_produtos_meta.json":
            assert (tenant / "cadastro_produtos_lojas.csv").exists()
            raise OSError("synthetic final import failure")
        return original(path, data)

    monkeypatch.setattr(module, method, fail_final_file)
    with pytest.raises(OSError, match="synthetic final import failure"):
        apply._shared_sync_aplicar_pacote(
            "000002", "cadastro", b"validated bundle", scope_config={"user_share": user_share})
    assert not (tenant / "cadastro_produtos_lojas.csv").exists()
    assert transaction._active.get() is None
    assert notifications == []


@pytest.mark.parametrize("file_count", [0, 1])
def test_catalog_notification_for_sales_requires_changed_files(catalog_import, monkeypatch, file_count):
    tenant, _ = catalog_import
    result = {"file_count": file_count, "files": ["produtos_compilado.csv"] if file_count else []}
    monkeypatch.setattr(apply, "_shared_sync_aplicar_pacote_conteudo", lambda *args: result)
    notifications = []
    monkeypatch.setattr(catalog_product_sync, "notify_catalog_committed",
                        lambda *args, **kwargs: notifications.append((args, kwargs)))
    assert apply._shared_sync_aplicar_pacote("000002", "vendas", b"validated bundle") == result
    assert notifications == ([(('000002',), {"info_root": str(tenant.parent)})] if file_count else [])
