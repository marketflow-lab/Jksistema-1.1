import asyncio
import json
import sqlite3
import sys
import threading
import time
from types import ModuleType

import pytest

from backend.modules.context_hub import catalog_product_sync as sync


STORE = "b1e5a6efb16c0db69bba1836"
OTHER = "22bb88ce93b504a26dcef2b5"


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "info"
    tenant = root / "testclient"
    tenant.mkdir(parents=True)
    stores = [{"store_id": key, "nome": name,
               "integracoes": {"mercadolivre": {"user_id": seller, "site_id": "MLB"}}}
              for key, name, seller in [(STORE, "Loja A", "12345"), (OTHER, "Loja B", "98765")]]
    (tenant / "lojas_config.json").write_text(json.dumps(stores), encoding="utf8")
    monkeypatch.setattr(sync, "_ensure_worker", lambda root: None)
    calls = []
    module = ModuleType("backend.modules.context_hub.catalog_product_repository")

    def publish(client, scope, products, **kwargs):
        calls.append((client, scope, products, kwargs))
        return {"status": "completed", "total": len(products), "generation_id": "generation", "changed": len(products)}

    module.publish_catalog_snapshot = publish
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return root, tenant, calls, module


def test_store_source_excludes_global_and_preserves_zero_sku(env):
    root, tenant, _, _ = env
    (tenant / "cadastro_produtos_lojas.csv").write_text(
        "store_id;sku;descricao;deleted_at_utc\n"
        f"{STORE};001;Sensor da loja A;\n{OTHER};001;Sensor da loja B;\n"
        f"{STORE};002;Apagado;2026-09-09\n", encoding="utf8")
    (tenant / "cadastro_produtos.csv").write_text(
        "sku;descricao;loja_sync\n003;Legado identificado;Loja A\n004;Legado sem loja;\n",
        encoding="utf8")
    result = sync.load_catalog_source_snapshot("testclient", STORE, info_root=root)
    products = {item["sku"]: item for item in result["products"]}
    assert products["001"]["descricao"] == "Sensor da loja A"
    assert "003" in products and "004" not in products and "002" not in products
    assert result["deleted_skus"] == ["002"]
    assert result["ambiguous_count"] >= 1


def test_durable_coalescing_and_recovery(env):
    root, tenant, calls, _ = env
    first = sync.request_catalog_sync("testclient", STORE, sku="001", info_root=root)
    second = sync.request_catalog_sync("testclient", STORE, sku="002", info_root=root)
    assert first["requested_revision"] == 1 and second["requested_revision"] == 2
    paths = sync._tenant_paths("testclient", info_root=root)
    with sync._db(paths, readonly=True) as con:
        row = con.execute("SELECT * FROM catalog_outbox").fetchone()
        assert row["not_before"] >= time.time() + 4
    sync._drain(root, threading.Event())
    assert calls == []
    with sync._db(paths) as con:
        con.execute("UPDATE catalog_outbox SET not_before=0,status='running'")
        con.commit()
    # No thread-local state is necessary after an interrupted run.
    sync._drain(root, threading.Event())
    state = sync.get_catalog_sync_status("testclient", STORE, info_root=root)
    assert state["status"] == "completed" and state["completed_revision"] == 2
    assert len(calls) == 1


def test_partial_read_does_not_publish_or_acknowledge(env, monkeypatch):
    root, _, calls, _ = env
    sync.request_catalog_sync("testclient", STORE, info_root=root)

    def fail(*args, **kwargs):
        raise OSError("private source payload must not enter error status")

    monkeypatch.setattr(sync, "load_catalog_source_snapshot", fail)
    with pytest.raises(OSError):
        sync.run_catalog_sync_now("testclient", STORE, info_root=root)
    state = sync.get_catalog_sync_status("testclient", STORE, info_root=root)
    assert not calls and state["status"] == "error"
    assert state["completed_revision"] == 0 and state["pending"] == 1
    assert state["last_error_code"] == "snapshot_unavailable"


def test_new_revision_during_publication_remains_pending(env, monkeypatch):
    root, _, calls, module = env
    original = module.publish_catalog_snapshot

    def publish(*args, **kwargs):
        sync.request_catalog_sync("testclient", STORE, sku="002", info_root=root)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "publish_catalog_snapshot", publish)
    sync.request_catalog_sync("testclient", STORE, info_root=root)
    sync.run_catalog_sync_now("testclient", STORE, info_root=root)
    state = sync.get_catalog_sync_status("testclient", STORE, info_root=root)
    assert state["status"] == "pending"
    assert state["requested_revision"] == 2 and state["completed_revision"] == 1


def test_publication_exclusive_across_threads(env, monkeypatch):
    root, _, calls, module = env
    active = 0
    max_active = 0
    lock = threading.Lock()
    original = module.publish_catalog_snapshot

    def publish(*args, **kwargs):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(active, max_active)
        time.sleep(0.08)
        result = original(*args, **kwargs)
        with lock:
            active -= 1
        return result

    monkeypatch.setattr(module, "publish_catalog_snapshot", publish)
    errors = []

    def run():
        try:
            sync.run_catalog_sync_now("testclient", STORE, info_root=root)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert not errors and max_active == 1 and len(calls) == 2


def test_seller_replacement_cannot_drain_old_scope(env):
    root, tenant, calls, _ = env
    sync.request_catalog_sync("testclient", STORE, info_root=root)
    paths = sync._tenant_paths("testclient", info_root=root)
    with sync._db(paths) as con:
        con.execute("UPDATE catalog_outbox SET not_before=0")
        con.commit()
    config = json.loads((tenant / "lojas_config.json").read_text())
    config[0]["integracoes"]["mercadolivre"]["user_id"] = "45678"
    (tenant / "lojas_config.json").write_text(json.dumps(config))
    sync._drain(root, threading.Event())
    assert calls == []
    assert sync.get_catalog_sync_status("testclient", STORE, info_root=root)["status"] == "not_synced"


def test_missing_notification_is_reconciled(env):
    root, _, _, _ = env
    sync._reconcile(root)
    assert sync.get_catalog_sync_status("testclient", STORE, info_root=root)["pending"] == 1
    assert sync.get_catalog_sync_status("testclient", OTHER, info_root=root)["pending"] == 1


def test_notification_failure_does_not_fail_committed_save(env, monkeypatch):
    root, _, _, _ = env

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("disk unavailable")

    monkeypatch.setattr(sync, "request_catalog_sync", fail)
    sync.notify_catalog_committed("testclient", STORE, info_root=root)


def test_images_are_resolved_only_from_exact_store(env):
    from backend.services import cadastro_fotos as photos
    root, tenant, _, _ = env
    own = photos._cadastro_store_id_foto_segmento(STORE)
    other = photos._cadastro_store_id_foto_segmento(OTHER)
    folder = tenant / "cadastro_fotos" / "lojas" / own
    folder.mkdir(parents=True)
    (folder / "001.png").write_bytes(b"test image")
    (tenant / "cadastro_produtos_lojas.csv").write_text(
        "store_id;sku;foto\n"
        f"{STORE};001;\n{STORE};002;cadastro_fotos/lojas/{other}/002.png\n"
        f"{STORE};003;cadastro_fotos/003.png\n", encoding="utf8")
    result = sync.load_catalog_source_snapshot("testclient", STORE, info_root=root)
    products = {item["sku"]: item for item in result["products"]}
    assert products["001"]["foto"] == f"cadastro_fotos/lojas/{own}/001.png"
    assert not products["002"]["foto"] and not products["003"]["foto"]


def test_crud_hooks_follow_commit_and_locks_and_skip_rollback(env, monkeypatch):
    from backend.services import cadastro_lojas_produtos as cadastro, cadastro_custos, cadastro_fotos, integracoes
    from backend.services.path_coordination import path_lock_for
    root, tenant, _, _ = env
    for module in (cadastro, cadastro_custos, cadastro_fotos):
        monkeypatch.setattr(module, "get_tenant_path", lambda client_id: str(root / client_id))
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(root), raising=False)
    stores = json.loads((tenant / "lojas_config.json").read_text())
    monkeypatch.setattr(integracoes, "carregar_lojas", lambda client_id: stores)
    notifications = []

    def notified(client, store):
        acquired = []

        def read_from_other_thread():
            lock = path_lock_for(tenant / cadastro.CADASTRO_PRODUTOS_LOJAS_ARQUIVO)
            got = lock.acquire(timeout=1)
            acquired.append(got)
            if got:
                lock.release()

        thread = threading.Thread(target=read_from_other_thread)
        thread.start()
        thread.join(2)
        assert acquired == [True], "notification must run after commit locks are released"
        assert (tenant / cadastro.CADASTRO_PRODUTOS_LOJAS_ARQUIVO).exists()
        notifications.append((client, store))

    monkeypatch.setattr(cadastro, "_notificar_ficha_catalogo", notified)
    cadastro.salvar_produto_loja("testclient", STORE, {"sku": "001", "nome": "A"})
    cadastro.salvar_produtos_loja_em_lote("testclient", STORE, [{"sku": "002", "nome": "B"}, {"sku": "003", "nome": "C"}])
    asyncio.run(cadastro.excluir_produto_loja(STORE, "001", 1, "testclient"))
    assert notifications == [("testclient", STORE)] * 3

    def fail(*args, **kwargs):
        raise OSError("rollback")

    monkeypatch.setattr(cadastro, "_salvar_registros_atomico", fail)
    with pytest.raises(OSError):
        cadastro.salvar_produto_loja("testclient", STORE, {"sku": "004", "nome": "D"})
    assert len(notifications) == 3
