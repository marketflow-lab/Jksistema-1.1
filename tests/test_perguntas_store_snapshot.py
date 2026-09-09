from __future__ import annotations

import json
import threading
import time

from backend.services import central_accounts_client, integracoes
from backend.services.path_coordination import path_lock_for


def test_store_snapshot_does_not_wait_for_catalog_and_photo_transaction(tmp_path, monkeypatch):
    tenant = tmp_path / "tenant-a"
    tenant.mkdir()
    stores = [{
        "nome": "Loja A",
        "store_id": "store-a",
        "integracoes": {
            "mercadolivre": {
                "user_id": "seller-a",
                "site_id": "MLB",
                "access_token": "fixture",
                "refresh_token": "fixture",
                "app_id": "fixture",
                "client_secret": "fixture",
            }
        },
    }]
    (tenant / "lojas_config.json").write_text(
        json.dumps(stores, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(integracoes, "PASTA_INFO", str(tmp_path))
    monkeypatch.setattr(integracoes, "_get_tenant_path", lambda _client_id: str(tenant))
    monkeypatch.setattr(central_accounts_client, "current", lambda _client_id: None)

    acquired = threading.Event()
    release = threading.Event()

    def hold_catalog_lock():
        with path_lock_for(tenant / "cadastro_produtos_lojas.csv"):
            acquired.set()
            release.wait(3)

    holder = threading.Thread(target=hold_catalog_lock, daemon=True)
    holder.start()
    assert acquired.wait(1)
    started = time.perf_counter()
    try:
        loaded = integracoes.carregar_lojas_snapshot("tenant-a")
    finally:
        release.set()
        holder.join(timeout=1)

    assert time.perf_counter() - started < 0.5
    assert [(row["store_id"], row["integracoes"]["mercadolivre"]["user_id"]) for row in loaded] == [
        ("store-a", "seller-a")
    ]
