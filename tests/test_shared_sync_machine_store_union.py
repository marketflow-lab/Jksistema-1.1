import json
import sys
import threading
import types
from contextlib import contextmanager

import backend.services as services_package

from backend.services import shared_sync
from backend.services import shared_sync_apply_scope
from backend.services import shared_sync_merge_sqlite
from backend.services import shared_sync_merge_integracoes


def _store(store_id: str, name: str) -> dict:
    return {
        "store_id": store_id,
        "nome": name,
        "integracoes": {},
    }


def test_machine_pull_uses_additive_store_merge(tmp_path, monkeypatch):
    captured = {}

    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tmp_path),
        raising=False,
    )
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "_shared_sync_read_validated_bundle",
        lambda _bundle, _scope: ({}, [("lojas_config.json", b"[]")]),
    )

    def apply_stores(
        _client_id,
        _sources,
        _tenant,
        _backup,
        *,
        add_only,
        **_kwargs,
    ):
        captured["add_only"] = add_only
        return {"files": [], "stores_count": 0}

    monkeypatch.setattr(
        shared_sync_apply_scope,
        "_shared_sync_aplicar_lojas_integracoes",
        apply_stores,
    )

    result = shared_sync_apply_scope._shared_sync_aplicar_pacote(
        "cliente-a",
        "lojas_integracoes",
        b"bundle",
    )

    assert captured["add_only"] is True
    assert result["stores_count"] == 0


def test_additive_store_merge_keeps_local_and_remote_only_stores(tmp_path):
    target = tmp_path / "lojas_config.json"
    target.write_text(
        json.dumps(
            [
                _store("store-shared", "Loja compartilhada"),
                _store("store-local", "Loja criada neste PC"),
            ]
        ),
        encoding="utf-8",
    )
    remote = json.dumps(
        [
            _store("store-shared", "Loja compartilhada"),
            _store("store-remote", "Loja criada no outro PC"),
        ]
    ).encode("utf-8")

    merged = json.loads(
        shared_sync_merge_integracoes._shared_sync_merge_lojas_integracoes_bytes(
            str(target),
            remote,
            add_only=True,
        )
    )

    assert {store["store_id"] for store in merged} == {
        "store-shared",
        "store-local",
        "store-remote",
    }


def test_additive_store_merge_removes_only_with_explicit_tombstone(
    tmp_path,
    monkeypatch,
):
    tenant = tmp_path / "info" / "cliente-a"
    tenant.mkdir(parents=True, exist_ok=True)

    fake_integrations = types.ModuleType("backend.services.integracoes")
    fake_integrations._LOJAS_CONFIG_LOCK = threading.RLock()

    @contextmanager
    def lock_catalog(_client_id, _tenant_abs):
        yield

    def capture(path):
        target = type(tenant)(path)
        return {
            "exists": target.exists(),
            "data": target.read_bytes() if target.exists() else b"",
        }

    def write_json(path, payload):
        target = type(tenant)(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload), encoding="utf-8")

    def save_stores(_client_id, stores, **_kwargs):
        write_json(tenant / "lojas_config.json", stores)

    def rollback(states):
        for path, state in states.items():
            target = type(tenant)(path)
            if state["exists"]:
                target.write_bytes(state["data"])
            elif target.exists():
                target.unlink()

    fake_integrations._integracoes_bloquear_catalogo_e_transicao_fotos = lock_catalog
    fake_integrations._integracoes_capturar_estado_arquivo = capture
    fake_integrations._integracoes_escrever_lojas_config_atomico = write_json
    fake_integrations._integracoes_rollback_estados_arquivo = rollback
    fake_integrations.salvar_lojas = save_stores
    monkeypatch.setitem(
        sys.modules,
        "backend.services.integracoes",
        fake_integrations,
    )
    monkeypatch.setattr(
        services_package,
        "integracoes",
        fake_integrations,
        raising=False,
    )

    (tenant / "lojas_config.json").write_text(
        json.dumps(
            [
                _store("store-delete", "Loja excluida manualmente"),
                _store("store-keep", "Loja preservada"),
            ]
        ),
        encoding="utf-8",
    )
    remote_stores = json.dumps(
        [_store("store-keep", "Loja preservada")]
    ).encode("utf-8")
    remote_tombstones = json.dumps(
        [
            {
                "key": "store:store-delete:",
                "type": "store",
                "store_id": "store-delete",
                "service": "",
                "version": 2,
                "deleted_at": "2026-09-02T17:30:00Z",
            }
        ]
    ).encode("utf-8")

    shared_sync_merge_sqlite._shared_sync_aplicar_lojas_integracoes_atomico(
        "cliente-a",
        [
            ("lojas_config.json", remote_stores),
            ("lojas_sync_tombstones.json", remote_tombstones),
        ],
        str(tenant),
        str(tenant / "backup"),
        add_only=True,
    )

    merged = json.loads(
        (tenant / "lojas_config.json").read_text(encoding="utf-8")
    )
    assert {store["store_id"] for store in merged} == {"store-keep"}
