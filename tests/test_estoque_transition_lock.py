import asyncio
import json
import subprocess
import sys
import threading
from pathlib import Path

import pandas as pd
import pytest
from fastapi import HTTPException

from backend.schemas.estoque import EstoqueSyncRequest
from backend.services import estoque_sync, integracoes


_DELETE_STORE_SCRIPT = r"""
import json
import logging
import os
import sys

from fastapi import HTTPException
from backend.services import integracoes

info_root, client_id, store_name, store_id = sys.argv[1:5]

def tenant_path(current_client_id):
    return os.path.join(info_root, str(current_client_id))

integracoes.configure_integracoes_context(
    logger_ref=logging.getLogger("estoque-delete-test"),
    pasta_info=info_root,
    get_tenant_path=tenant_path,
)
print("START", flush=True)
try:
    integracoes.excluir_loja(
        client_id,
        store_name,
        store_id=store_id,
    )
except HTTPException as exc:
    print(
        json.dumps({"status": exc.status_code, "detail": exc.detail}),
        flush=True,
    )
else:
    print(json.dumps({"status": 200, "detail": {}}), flush=True)
"""


def _store_config() -> list[dict]:
    return [
        {
            "store_id": "store-a",
            "nome": "Loja store-a",
            "integracoes": {
                "bling": {
                    "access_token": "token-a",
                    "id": "id-a",
                    "secret": "secret-a",
                }
            },
        }
    ]


def _configure_tenant(tmp_path: Path, monkeypatch) -> Path:
    tenant = tmp_path / "000002"
    tenant.mkdir()
    (tenant / "lojas_config.json").write_text(
        json.dumps(_store_config()),
        encoding="utf-8",
    )

    resolver = lambda client_id: str(tmp_path / str(client_id))
    monkeypatch.setattr(integracoes, "PASTA_INFO", str(tmp_path))
    monkeypatch.setattr(
        integracoes,
        "ARQUIVO_LOJAS",
        str(tmp_path / "lojas_config.json"),
    )
    monkeypatch.setattr(integracoes, "_get_tenant_path", resolver)
    monkeypatch.setattr(estoque_sync, "get_tenant_path", resolver, raising=False)
    monkeypatch.setattr(estoque_sync, "carregar_lojas", integracoes.carregar_lojas)
    estoque_sync.ESTOQUE_SYNC_CANCEL_FLAGS.pop("000002", None)
    return tenant


def _spawn_store_delete(tenant: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            _DELETE_STORE_SCRIPT,
            str(tenant.parent),
            tenant.name,
            "Loja store-a",
            "store-a",
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _finish_store_delete(process: subprocess.Popen, timeout: float = 15.0) -> dict:
    stdout, stderr = process.communicate(timeout=timeout)
    assert process.returncode == 0, stderr
    lines = [line for line in stdout.splitlines() if line.strip()]
    assert lines, stderr
    return json.loads(lines[-1])


def _patch_bling_success(monkeypatch, *, before_balances=None) -> None:
    calls = []

    def execute(
        _client_id,
        _store_name,
        config,
        _call,
        on_refresh=None,
        *,
        store_id=None,
        require_owned_refresh=False,
    ):
        assert store_id == "store-a"
        assert require_owned_refresh is True
        calls.append(1)
        if len(calls) == 1:
            payload = [
                {
                    "sku": "001",
                    "id_bling": "bling-1",
                    "nome_bling": "Produto 1",
                    "situacao_bling": "A",
                    "ncm_bling": "1234",
                }
            ]
        elif len(calls) == 2:
            payload = {"deposito-a": "loja"}
        else:
            if before_balances is not None:
                before_balances()
            payload = {"bling-1": {"loja": 7, "full": 2}}
        return payload, 200, dict(config)

    monkeypatch.setattr(
        estoque_sync,
        "_bling_executar_com_refresh",
        execute,
        raising=False,
    )
    monkeypatch.setattr(
        estoque_sync,
        "_bling_listar_produtos",
        lambda _token: None,
        raising=False,
    )
    monkeypatch.setattr(
        estoque_sync,
        "_bling_map_depositos",
        lambda _token: None,
        raising=False,
    )
    monkeypatch.setattr(
        estoque_sync,
        "_bling_saldos",
        lambda *_args: None,
        raising=False,
    )
    monkeypatch.setattr(estoque_sync, "_novo_event_id_estoque", lambda: "event-a")
    monkeypatch.setattr(
        estoque_sync,
        "_registrar_snapshot_historico_estoque",
        lambda *_args, **_kwargs: 1,
    )
    monkeypatch.setattr(
        estoque_sync,
        "_confirmar_evento_historico_estoque",
        lambda *_args, **_kwargs: 1,
    )
    monkeypatch.setattr(
        estoque_sync,
        "_descartar_evento_pendente_estoque",
        lambda *_args, **_kwargs: None,
    )


def _run_stock_sync(result: list, errors: list) -> None:
    try:
        result.append(
            asyncio.run(
                estoque_sync._sincronizar_estoque_impl(
                    EstoqueSyncRequest(
                        loja="Loja store-a",
                        store_id="store-a",
                    ),
                    "000002",
                )
            )
        )
    except BaseException as exc:  # pragma: no cover - surfaced by assertions
        errors.append(exc)


def test_estoque_writer_serializa_exclusao_cross_process(tmp_path, monkeypatch):
    tenant = _configure_tenant(tmp_path, monkeypatch)
    _patch_bling_success(monkeypatch)
    ready = threading.Event()
    release = threading.Event()
    real_publish = estoque_sync._atualizar_produtos_compilados_loja

    def publish_gated(*args, **kwargs):
        ready.set()
        if not release.wait(15):
            raise AssertionError("timeout aguardando liberar publicacao do estoque")
        return real_publish(*args, **kwargs)

    monkeypatch.setattr(
        estoque_sync,
        "_atualizar_produtos_compilados_loja",
        publish_gated,
    )
    result = []
    errors = []
    writer = threading.Thread(
        target=_run_stock_sync,
        args=(result, errors),
        daemon=True,
    )
    delete_process = None
    writer.start()
    try:
        assert ready.wait(10), "estoque nao chegou ao commit protegido"
        delete_process = _spawn_store_delete(tenant)
        assert delete_process.stdout.readline().strip() == "START"
        with pytest.raises(subprocess.TimeoutExpired):
            delete_process.wait(timeout=0.5)
    finally:
        release.set()

    writer.join(15)
    assert not writer.is_alive()
    if errors:
        raise errors[0]
    assert result[0]["success"] is True
    assert delete_process is not None
    delete_result = _finish_store_delete(delete_process)
    assert delete_result["status"] == 409
    assert delete_result["detail"]["code"] == "store_has_legacy_catalog_records"
    compiled = pd.read_csv(
        tenant / "produtos_compilado.csv",
        dtype=str,
        keep_default_na=False,
    )
    assert compiled[["store_id", "sku"]].to_dict("records") == [
        {"store_id": "store-a", "sku": "001"}
    ]
    stores = json.loads(
        (tenant / "lojas_config.json").read_text(encoding="utf-8-sig")
    )
    assert [store["store_id"] for store in stores] == ["store-a"]


def test_estoque_revalida_exclusao_cross_process_antes_do_commit(
    tmp_path,
    monkeypatch,
):
    tenant = _configure_tenant(tmp_path, monkeypatch)
    balances_ready = threading.Event()
    release_balances = threading.Event()

    def before_balances():
        balances_ready.set()
        if not release_balances.wait(15):
            raise AssertionError("timeout aguardando exclusao da loja")

    _patch_bling_success(monkeypatch, before_balances=before_balances)
    result = []
    errors = []
    writer = threading.Thread(
        target=_run_stock_sync,
        args=(result, errors),
        daemon=True,
    )
    writer.start()
    try:
        assert balances_ready.wait(10), "estoque nao chegou antes do commit"
        delete_process = _spawn_store_delete(tenant)
        assert delete_process.stdout.readline().strip() == "START"
        delete_result = _finish_store_delete(delete_process)
        assert delete_result["status"] == 200
    finally:
        release_balances.set()

    writer.join(15)
    assert not writer.is_alive()
    assert result == []
    assert len(errors) == 1
    assert isinstance(errors[0], HTTPException)
    assert errors[0].status_code == 404
    assert not (tenant / "produtos_compilado.csv").exists()
    assert json.loads(
        (tenant / "lojas_config.json").read_text(encoding="utf-8-sig")
    ) == []
