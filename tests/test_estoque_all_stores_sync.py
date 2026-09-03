import asyncio
import json
import threading
import time

import pandas as pd
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.schemas.estoque import EstoqueSyncRequest
from backend.services import (
    bling_vendas,
    codex_actions,
    estoque_common,
    estoque_context,
    estoque_sync,
)


TENANT = "tenant-all-stores"
OTHER_TENANTS = ("tenant-a", "tenant-b")


def _store(store_id: str, name: str, token: str) -> dict:
    return {
        "store_id": store_id,
        "nome": name,
        "integracoes": {
            "bling": {
                "id": f"client-{store_id}",
                "secret": f"secret-{store_id}",
                "access_token": token,
                "refresh_token": f"refresh-{store_id}",
                "connected": True,
                "oauth_invalid": False,
                "status": "conectado",
                "_sync_version": "1",
            }
        },
    }


@pytest.fixture(autouse=True)
def _clear_sync_state():
    dictionaries = (
        estoque_sync.ESTOQUE_SYNC_ACTIVE,
        estoque_sync.ESTOQUE_SYNC_PROGRESS,
        estoque_sync.ESTOQUE_SYNC_LOGS,
        estoque_sync.ESTOQUE_SYNC_META,
        estoque_sync.ESTOQUE_SYNC_CANCEL_FLAGS,
    )
    for dictionary in dictionaries:
        for tenant in (TENANT, *OTHER_TENANTS):
            dictionary.pop(tenant, None)
    for tenant in (TENANT, *OTHER_TENANTS):
        estoque_common._ESTOQUE_SYNC_TERMINAL_JOBS.pop(tenant, None)
    yield
    for dictionary in dictionaries:
        for tenant in (TENANT, *OTHER_TENANTS):
            dictionary.pop(tenant, None)
    for tenant in (TENANT, *OTHER_TENANTS):
        estoque_common._ESTOQUE_SYNC_TERMINAL_JOBS.pop(tenant, None)


def _prepare_job(stores: list[dict], job_id: str = "job-all") -> list[dict]:
    snapshots = [estoque_sync._estoque_snapshot_loja(item) for item in stores]
    estoque_sync.ESTOQUE_SYNC_META[TENANT] = estoque_sync._estoque_criar_sync_meta(
        job_id,
        snapshots,
        todas_lojas=True,
    )
    estoque_sync.ESTOQUE_SYNC_LOGS[TENANT] = []
    return snapshots


def test_all_request_snapshots_exact_ids_and_rejects_invalid_ids_before_thread(monkeypatch):
    stores = [
        _store("store-b", "Loja Homonima", "token-b"),
        _store("store-a", "Loja Homonima", "token-a"),
    ]
    monkeypatch.setattr(estoque_sync, "carregar_lojas", lambda _tenant: stores)

    request = EstoqueSyncRequest(loja="__todas", todas_lojas=True)
    snapshots = estoque_sync._snapshot_lojas_estoque(TENANT)
    stores.append(_store("store-c", "Loja Nova", "token-c"))

    assert request.loja == "__todas"
    assert [item["store_id"] for item in snapshots] == ["store-b", "store-a"]
    assert [item["nome"] for item in snapshots] == ["Loja Homonima", "Loja Homonima"]

    thread_calls = []
    monkeypatch.setattr(
        estoque_sync.threading,
        "Thread",
        lambda **kwargs: thread_calls.append(kwargs),
    )
    invalid_configs = [
        [_store("", "Sem ID", "token")],
        [_store("dup", "A", "token-a"), _store("dup", "B", "token-b")],
    ]
    for config in invalid_configs:
        monkeypatch.setattr(estoque_sync, "carregar_lojas", lambda _tenant, c=config: c)
        with pytest.raises(HTTPException) as invalid:
            asyncio.run(estoque_sync.sincronizar_estoque(request, TENANT))
        assert invalid.value.status_code == 409

    assert thread_calls == []
    assert not estoque_sync.ESTOQUE_SYNC_ACTIVE.get(TENANT)


def test_same_tenant_returns_existing_job_without_loading_stores(monkeypatch):
    estoque_sync.ESTOQUE_SYNC_ACTIVE[TENANT] = True
    estoque_sync.ESTOQUE_SYNC_META[TENANT] = {
        "job_id": "job-existing",
        "scope": {"type": "all"},
    }
    monkeypatch.setattr(
        estoque_sync,
        "carregar_lojas",
        lambda _tenant: pytest.fail("active jobs must not reload stores"),
    )

    response = asyncio.run(
        estoque_sync.sincronizar_estoque(
            EstoqueSyncRequest(loja="__todas", todas_lojas=True), TENANT
        )
    )

    assert response == {
        "started": False,
        "already_running": True,
        "job_id": "job-existing",
        "message": "Atualização de estoque já em andamento.",
    }


def test_progress_by_job_id_preserves_terminal_job_after_next_job_starts():
    snapshots = _prepare_job([_store("a", "A", "token-a")], job_id="job-a")
    meta_a = estoque_sync.ESTOQUE_SYNC_META[TENANT]
    meta_a["resultados"][0].update(status="success", total=0)
    estoque_sync.ESTOQUE_SYNC_LOGS[TENANT] = ["log-job-a"]
    estoque_sync._estoque_finalizar_job(
        TENANT,
        "job-a",
        "completed",
        etapa="Concluido",
        lote=1,
        total=1,
        mensagem="Job A concluido.",
    )

    estoque_sync._estoque_preparar_job(
        TENANT,
        "job-b",
        snapshots,
        todas_lojas=True,
        scope={"type": "all"},
        ativar=True,
    )
    progresso_b = estoque_sync._criar_progresso(
        "Bling", 1, 1, 25, "Job B em andamento."
    )
    estoque_sync.ESTOQUE_SYNC_PROGRESS[TENANT] = progresso_b
    estoque_sync.ESTOQUE_SYNC_LOGS[TENANT] = ["log-job-b"]

    terminal_a = asyncio.run(
        estoque_common.progresso_sincronizacao_estoque(TENANT, job_id="job-a")
    )
    corrente_b = asyncio.run(
        estoque_common.progresso_sincronizacao_estoque(TENANT, job_id="job-b")
    )
    legado_sem_job_id = asyncio.run(
        estoque_common.progresso_sincronizacao_estoque(TENANT)
    )

    assert terminal_a["job_id"] == "job-a"
    assert terminal_a["active"] is False
    assert terminal_a["progress"]["percentual"] == 100
    assert terminal_a["logs"] == ["log-job-a"]
    assert terminal_a["sync_meta"]["outcome"] == "completed"
    assert corrente_b["job_id"] == "job-b"
    assert corrente_b["active"] is True
    assert corrente_b["progress"] == progresso_b
    assert legado_sem_job_id == corrente_b

    with pytest.raises(HTTPException) as outro_tenant:
        asyncio.run(
            estoque_common.progresso_sincronizacao_estoque(
                OTHER_TENANTS[0], job_id="job-a"
            )
        )
    assert outro_tenant.value.status_code == 404

    estoque_common._ESTOQUE_SYNC_TERMINAL_JOBS[TENANT]["job-a"][
        "expires_at"
    ] = time.monotonic() - 1
    with pytest.raises(HTTPException) as expirado:
        asyncio.run(
            estoque_common.progresso_sincronizacao_estoque(TENANT, job_id="job-a")
        )
    assert expirado.value.status_code == 404
    assert expirado.value.detail["code"] == "estoque_sync_job_not_found"


def test_all_job_continues_safe_failure_and_reports_partial(monkeypatch):
    stores = [_store("a", "A", "token-a"), _store("b", "B", "token-b"), _store("c", "C", "token-c")]
    snapshots = _prepare_job(stores)
    calls = []

    async def sync_one(req, _tenant, **_kwargs):
        calls.append(req.store_id)
        if req.store_id == "b":
            raise HTTPException(status_code=401, detail="auth failed")
        return {
            "success": True,
            "total": 1,
            "event_id": f"event-{req.store_id}",
            "historico_registrado": True,
        }

    monkeypatch.setattr(estoque_sync, "_sincronizar_estoque_loja_impl", sync_one)
    result = asyncio.run(
        estoque_sync._sincronizar_estoque_job(
            EstoqueSyncRequest(loja="__todas", todas_lojas=True),
            TENANT,
            snapshots,
            "job-all",
        )
    )

    assert calls == ["a", "b", "c"]
    assert result["outcome"] == "partial"
    assert [item["status"] for item in result["resultados"]] == ["success", "failed", "success"]
    assert result["resultados"][1]["erro"]["code"] == "bling_auth_error"
    assert estoque_sync.ESTOQUE_SYNC_META[TENANT]["contagens"] == {
        "success": 2,
        "failed": 1,
        "uncertain": 0,
        "cancelled": 0,
        "skipped": 0,
    }
    assert estoque_sync.ESTOQUE_SYNC_PROGRESS[TENANT]["percentual"] == 100
    public_state = json.dumps(
        {
            "meta": estoque_sync.ESTOQUE_SYNC_META[TENANT],
            "logs": estoque_sync.ESTOQUE_SYNC_LOGS[TENANT],
        }
    )
    assert "token-" not in public_state
    assert "secret-" not in public_state


def test_uncertain_publication_stops_remaining_stores(monkeypatch):
    stores = [_store("a", "A", "a"), _store("b", "B", "b"), _store("c", "C", "c")]
    snapshots = _prepare_job(stores)
    calls = []

    async def sync_one(req, _tenant, **_kwargs):
        calls.append(req.store_id)
        if req.store_id == "b":
            raise estoque_sync._EstoquePublicacaoInconclusiva("after replace")
        return {"total": 1, "event_id": "event-a", "historico_registrado": True}

    monkeypatch.setattr(estoque_sync, "_sincronizar_estoque_loja_impl", sync_one)
    result = asyncio.run(
        estoque_sync._sincronizar_estoque_job(
            EstoqueSyncRequest(loja="__todas", todas_lojas=True),
            TENANT,
            snapshots,
            "job-all",
        )
    )

    assert calls == ["a", "b"]
    assert result["outcome"] == "partial"
    assert [item["status"] for item in result["resultados"]] == ["success", "uncertain", "skipped"]
    assert result["resultados"][1]["erro"]["code"] == "estoque_publication_uncertain"
    assert result["resultados"][1]["requires_reconciliation"] is True
    assert result["resultados"][2]["erro"]["code"] == "estoque_publication_blocked"
    assert result["requires_reconciliation"] is True
    assert estoque_sync.ESTOQUE_SYNC_META[TENANT]["requires_reconciliation"] is True
    assert estoque_sync.ESTOQUE_SYNC_META[TENANT]["contagens"]["uncertain"] == 1


def test_cancel_during_store_marks_remaining_as_skipped(monkeypatch):
    stores = [_store("a", "A", "a"), _store("b", "B", "b"), _store("c", "C", "c")]
    snapshots = _prepare_job(stores)
    calls = []

    async def sync_one(req, _tenant, **_kwargs):
        calls.append(req.store_id)
        if req.store_id == "b":
            estoque_sync.ESTOQUE_SYNC_CANCEL_FLAGS[TENANT] = True
            estoque_sync._estoque_verificar_cancelamento(TENANT)
        return {"total": 1, "event_id": "event-a", "historico_registrado": True}

    monkeypatch.setattr(estoque_sync, "_sincronizar_estoque_loja_impl", sync_one)
    result = asyncio.run(
        estoque_sync._sincronizar_estoque_job(
            EstoqueSyncRequest(loja="__todas", todas_lojas=True),
            TENANT,
            snapshots,
            "job-all",
        )
    )

    assert calls == ["a", "b"]
    assert result["outcome"] == "cancelled"
    assert [item["status"] for item in result["resultados"]] == ["success", "cancelled", "skipped"]


def _patch_bling_success(monkeypatch, tmp_path, stores, execute):
    monkeypatch.setattr(estoque_sync, "carregar_lojas", lambda _tenant: stores)
    monkeypatch.setattr(estoque_sync, "get_tenant_path", lambda _tenant: str(tmp_path), raising=False)
    monkeypatch.setattr(estoque_sync, "_novo_event_id_estoque", lambda: "event-a")
    monkeypatch.setattr(estoque_sync, "_bling_executar_com_refresh", execute, raising=False)
    monkeypatch.setattr(estoque_sync, "_bling_listar_produtos", lambda _token: None, raising=False)
    monkeypatch.setattr(estoque_sync, "_bling_map_depositos", lambda _token: None, raising=False)
    monkeypatch.setattr(estoque_sync, "_bling_saldos", lambda *_args: None, raising=False)


def test_all_job_keeps_csv_readable_when_first_store_is_empty(monkeypatch, tmp_path):
    stores = [_store("empty", "Loja Vazia", "token-empty"), _store("item", "Loja Item", "token-item")]
    target = tmp_path / "produtos_compilado.csv"
    responses = {
        "empty": [[], {}, {}],
        "item": [
            [
                {
                    "sku": "SKU-ITEM",
                    "id_bling": "product-item",
                    "nome_bling": "Produto da segunda loja",
                    "situacao_bling": "A",
                    "ncm_bling": "12345678",
                }
            ],
            {},
            {"product-item": {"loja": 4, "full": 1}},
        ],
    }
    calls = {"empty": 0, "item": 0}
    header_after_empty = []

    def execute(
        _tenant,
        _name,
        cfg,
        _call,
        on_refresh=None,
        *,
        store_id=None,
        require_owned_refresh=False,
    ):
        assert require_owned_refresh is True
        index = calls[store_id]
        if store_id == "item" and index == 0:
            empty_csv = pd.read_csv(target, dtype=str, keep_default_na=False)
            assert empty_csv.empty
            header_after_empty.append(list(empty_csv.columns))
        calls[store_id] += 1
        return responses[store_id][index], 200, cfg

    event_ids = iter(("event-empty", "event-item"))
    monkeypatch.setattr(estoque_sync, "carregar_lojas", lambda _tenant: stores)
    monkeypatch.setattr(estoque_sync, "get_tenant_path", lambda _tenant: str(tmp_path), raising=False)
    monkeypatch.setattr(estoque_sync, "_novo_event_id_estoque", lambda: next(event_ids))
    monkeypatch.setattr(estoque_sync, "_bling_executar_com_refresh", execute, raising=False)
    monkeypatch.setattr(estoque_sync, "_bling_listar_produtos", lambda _token: None, raising=False)
    monkeypatch.setattr(estoque_sync, "_bling_map_depositos", lambda _token: None, raising=False)
    monkeypatch.setattr(estoque_sync, "_bling_saldos", lambda *_args: None, raising=False)
    monkeypatch.setattr(
        estoque_sync,
        "_registrar_snapshot_historico_estoque",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        estoque_sync,
        "_confirmar_evento_historico_estoque",
        lambda _tenant, event_id, **_kwargs: 0 if event_id == "event-empty" else 1,
    )

    result = asyncio.run(
        estoque_sync._sincronizar_estoque_impl(
            EstoqueSyncRequest(loja="__todas", todas_lojas=True), TENANT
        )
    )

    expected_columns = list(estoque_sync._ESTOQUE_CSV_COLUNAS_CANONICAS)
    assert header_after_empty == [expected_columns]
    assert result["outcome"] == "completed"
    assert [item["total"] for item in result["resultados"]] == [0, 1]
    compiled = pd.read_csv(target, dtype=str, keep_default_na=False)
    assert list(compiled.columns) == expected_columns
    assert compiled.to_dict("records") == [
        {
            "sku": "SKU-ITEM",
            "store_id": "item",
            "loja_sync": "Loja Item",
            "last_update": compiled.iloc[0]["last_update"],
            "id_bling": "product-item",
            "nome_bling": "Produto da segunda loja",
            "situacao_bling": "A",
            "ncm_bling": "12345678",
            "saldo_loja": "4",
            "saldo_full": "1",
        }
    ]


def test_bling_fingerprint_change_blocks_publish(monkeypatch, tmp_path):
    stores = [_store("a", "A", "token-old")]
    calls = []

    def execute(
        _tenant,
        _name,
        cfg,
        _call,
        on_refresh=None,
        *,
        store_id=None,
        require_owned_refresh=False,
    ):
        assert require_owned_refresh is True
        calls.append(store_id)
        payloads = [
            [{"sku": "001", "id_bling": "p1"}],
            {"deposit": "loja"},
            {"p1": {"loja": 2, "full": 1}},
        ]
        if len(calls) == 3:
            stores[0]["integracoes"]["bling"] = {
                **stores[0]["integracoes"]["bling"],
                "id": "other-account",
                "secret": "other-secret",
                "access_token": "other-token",
            }
        return payloads[len(calls) - 1], 200, cfg

    publications = []
    _patch_bling_success(monkeypatch, tmp_path, stores, execute)
    monkeypatch.setattr(
        estoque_sync,
        "_atualizar_produtos_compilados_loja",
        lambda *_args, **_kwargs: publications.append(True),
    )

    with pytest.raises(HTTPException) as changed:
        asyncio.run(
            estoque_sync._sincronizar_estoque_impl(
                EstoqueSyncRequest(loja="A", store_id="a"), TENANT
            )
        )

    assert changed.value.status_code == 409
    assert changed.value.detail["code"] == "estoque_bling_config_changed"
    assert publications == []


def test_persisted_refresh_fingerprint_allows_publish(monkeypatch, tmp_path):
    stores = [_store("a", "A", "token-old")]
    calls = []

    def execute(
        _tenant,
        _name,
        cfg,
        _call,
        on_refresh=None,
        *,
        store_id=None,
        require_owned_refresh=False,
    ):
        assert require_owned_refresh is True
        calls.append((store_id, cfg["access_token"]))
        if len(calls) == 1:
            refreshed = {
                **cfg,
                "access_token": "token-new",
                "refresh_token": "refresh-new",
                "_sync_version": "2",
            }
            stores[0]["integracoes"]["bling"] = dict(refreshed)
            cfg = refreshed
        payloads = [
            [{"sku": "001", "id_bling": "p1"}],
            {"deposit": "loja"},
            {"p1": {"loja": 2, "full": 1}},
        ]
        return payloads[len(calls) - 1], 200, cfg

    publications = []
    _patch_bling_success(monkeypatch, tmp_path, stores, execute)
    monkeypatch.setattr(
        estoque_sync,
        "_atualizar_produtos_compilados_loja",
        lambda *_args, **_kwargs: publications.append(True) or 1,
    )

    result = asyncio.run(
        estoque_sync._sincronizar_estoque_impl(
            EstoqueSyncRequest(loja="A", store_id="a"), TENANT
        )
    )

    assert result["success"] is True
    assert calls == [("a", "token-old"), ("a", "token-new"), ("a", "token-new")]
    assert publications == [True]


def test_estoque_sync_request_keeps_loja_required():
    with pytest.raises(ValidationError):
        EstoqueSyncRequest(todas_lojas=True)


@pytest.mark.parametrize(
    ("disposition", "should_retry"),
    [
        ("committed_by_caller", True),
        ("reused_concurrent", False),
        ("cas_lost", False),
    ],
)
def test_owned_refresh_retries_only_caller_commit(monkeypatch, disposition, should_retry):
    calls = []

    def api_call(token):
        calls.append(token)
        return ({"ok": True}, 200) if token == "new-token" else ({}, 401)

    def refresh(_tenant, _name, _cfg, *, store_id, return_disposition=False):
        assert store_id == "store-a"
        assert return_disposition is True
        return {"access_token": "new-token"}, disposition

    monkeypatch.setattr(bling_vendas, "_bling_renovar_token_loja", refresh)
    if should_retry:
        payload, status, cfg = bling_vendas._bling_executar_com_refresh(
            TENANT,
            "Loja A",
            {"access_token": "old-token"},
            api_call,
            store_id="store-a",
            require_owned_refresh=True,
        )
        assert (payload, status, cfg["access_token"]) == ({"ok": True}, 200, "new-token")
        assert calls == ["old-token", "new-token"]
    else:
        with pytest.raises(HTTPException) as conflict:
            bling_vendas._bling_executar_com_refresh(
                TENANT,
                "Loja A",
                {"access_token": "old-token"},
                api_call,
                store_id="store-a",
                require_owned_refresh=True,
            )
        assert conflict.value.status_code == 409
        assert conflict.value.detail["code"] == "bling_refresh_not_owned"
        assert conflict.value.detail["disposition"] == disposition
        assert calls == ["old-token"]


@pytest.mark.parametrize(
    ("active_scope", "sync_request", "conflict"),
    [
        ({"type": "all"}, EstoqueSyncRequest(loja="__todas", todas_lojas=True), False),
        ({"type": "store", "store_id": "a"}, EstoqueSyncRequest(loja="A", store_id="a"), False),
        ({"type": "store", "store_id": "a"}, EstoqueSyncRequest(loja="B", store_id="b"), True),
        ({"type": "all"}, EstoqueSyncRequest(loja="A", store_id="a"), True),
        ({"type": "store", "store_id": "a"}, EstoqueSyncRequest(loja="__todas", todas_lojas=True), True),
        ({"type": "legacy_name", "loja": "A"}, EstoqueSyncRequest(loja="A"), False),
        ({"type": "legacy_name", "loja": "A"}, EstoqueSyncRequest(loja="B"), True),
    ],
)
def test_active_job_requires_identical_scope_without_store_io(
    monkeypatch, active_scope, sync_request, conflict
):
    estoque_sync.ESTOQUE_SYNC_ACTIVE[TENANT] = True
    estoque_sync.ESTOQUE_SYNC_META[TENANT] = {
        "job_id": "job-active",
        "scope": active_scope,
    }
    monkeypatch.setattr(
        estoque_sync,
        "carregar_lojas",
        lambda _tenant: pytest.fail("first active check must not load stores"),
    )
    if conflict:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(estoque_sync.sincronizar_estoque(sync_request, TENANT))
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "estoque_sync_scope_conflict"
    else:
        response = asyncio.run(estoque_sync.sincronizar_estoque(sync_request, TENANT))
        assert response["already_running"] is True
        assert response["job_id"] == "job-active"


def test_second_locked_check_rejects_scope_started_during_snapshot(monkeypatch):
    def load_stores(_tenant):
        estoque_sync.ESTOQUE_SYNC_ACTIVE[TENANT] = True
        estoque_sync.ESTOQUE_SYNC_META[TENANT] = {
            "job_id": "racing-job",
            "scope": {"type": "store", "store_id": "b"},
        }
        return [_store("a", "A", "token-a")]

    monkeypatch.setattr(estoque_sync, "carregar_lojas", load_stores)
    with pytest.raises(HTTPException) as conflict:
        asyncio.run(
            estoque_sync.sincronizar_estoque(
                EstoqueSyncRequest(loja="A", store_id="a"), TENANT
            )
        )
    assert conflict.value.detail["code"] == "estoque_sync_scope_conflict"


def test_pre_replace_failure_is_safe(monkeypatch, tmp_path):
    target = tmp_path / "produtos_compilado.csv"
    discarded = []
    monkeypatch.setattr(estoque_sync, "_registrar_snapshot_historico_estoque", lambda *_a, **_k: 1)
    monkeypatch.setattr(
        estoque_sync,
        "_descartar_evento_pendente_estoque",
        lambda *_a, **_k: discarded.append(True),
    )
    monkeypatch.setattr(estoque_sync.os, "replace", lambda *_a: (_ for _ in ()).throw(OSError("pre")))

    with pytest.raises(OSError):
        estoque_sync._publicar_csv_estoque_apos_historico(
            TENANT,
            "A",
            [{"sku": "1"}],
            pd.DataFrame([{"sku": "1"}]),
            str(target),
            "event-pre",
        )
    assert discarded == [True]


def test_post_replace_confirmation_failure_is_uncertain(monkeypatch, tmp_path):
    target = tmp_path / "produtos_compilado.csv"
    monkeypatch.setattr(estoque_sync, "_registrar_snapshot_historico_estoque", lambda *_a, **_k: 1)
    monkeypatch.setattr(
        estoque_sync,
        "_confirmar_evento_historico_estoque",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("confirm")),
    )

    with pytest.raises(estoque_sync._EstoquePublicacaoInconclusiva):
        estoque_sync._publicar_csv_estoque_apos_historico(
            TENANT,
            "A",
            [{"sku": "1"}],
            pd.DataFrame([{"sku": "1"}]),
            str(target),
            "event-post",
        )
    assert target.exists()


def test_cancel_flag_is_bound_to_job_and_legacy_bool_still_works(monkeypatch):
    estoque_sync.ESTOQUE_SYNC_CANCEL_FLAGS[TENANT] = {"job_id": "old-job"}
    estoque_sync._estoque_verificar_cancelamento(TENANT, "new-job")
    assert TENANT not in estoque_sync.ESTOQUE_SYNC_CANCEL_FLAGS

    estoque_sync.ESTOQUE_SYNC_CANCEL_FLAGS[TENANT] = {"job_id": "new-job"}
    with pytest.raises(HTTPException):
        estoque_sync._estoque_verificar_cancelamento(TENANT, "new-job")

    estoque_sync.ESTOQUE_SYNC_CANCEL_FLAGS[TENANT] = True
    with pytest.raises(HTTPException):
        estoque_sync._estoque_verificar_cancelamento(TENANT, "another-job")

    run = {
        "status": "running",
        "client_id": TENANT,
        "action": {"status_kind": "estoque_sync"},
        "estoque_sync_owned_job_id": "run-job",
    }
    estoque_sync.ESTOQUE_SYNC_ACTIVE[TENANT] = True
    estoque_sync.ESTOQUE_SYNC_META[TENANT] = {"job_id": "run-job", "outcome": None}
    monkeypatch.setattr(codex_actions, "_run_load", lambda *_a, **_k: run)
    monkeypatch.setattr(codex_actions, "_update_run", lambda *_a, **_k: run)
    codex_actions.cancel_run("run-id", client_id=TENANT)
    assert estoque_context.ESTOQUE_SYNC_CANCEL_FLAGS[TENANT] == {"job_id": "run-job"}


def test_codex_cancel_without_owned_job_does_not_touch_unrelated_ui_job(monkeypatch):
    estoque_sync.ESTOQUE_SYNC_META[TENANT] = {"job_id": "ui-job"}
    run = {
        "status": "running",
        "client_id": TENANT,
        "action": {"status_kind": "estoque_sync"},
        "sync_meta": {"job_id": "ui-job"},
    }
    monkeypatch.setattr(codex_actions, "_run_load", lambda *_a, **_k: run)
    monkeypatch.setattr(codex_actions, "_update_run", lambda *_a, **_k: run)

    codex_actions.cancel_run("run-sem-vinculo", client_id=TENANT)

    assert TENANT not in estoque_context.ESTOQUE_SYNC_CANCEL_FLAGS


def test_codex_cancel_cannot_overwrite_terminal_state_after_signal(monkeypatch):
    state = {
        "run_id": "run-race",
        "status": "running",
        "client_id": TENANT,
        "action": {"status_kind": "estoque_sync"},
        "estoque_sync_owned_job_id": "owned-race",
    }

    def load(*_args, **_kwargs):
        return dict(state)

    def update(_run_id, **kwargs):
        kwargs.pop("_client_id", None)
        state.update(kwargs)
        return dict(state)

    signals = []

    def signal(tenant, job_id):
        signals.append((tenant, job_id))
        state.update(status="canceled", live_status="Acao cancelada.")
        return True

    monkeypatch.setattr(codex_actions, "_run_load", load)
    monkeypatch.setattr(codex_actions, "_update_run", update)
    monkeypatch.setattr(estoque_sync, "_estoque_solicitar_cancelamento_job", signal)

    response = codex_actions.cancel_run("run-race", client_id=TENANT)

    assert signals == [(TENANT, "owned-race")]
    assert state["status"] == "canceled"
    assert response["run"]["status"] == "canceled"


def test_codex_all_stores_uses_one_owned_backend_job(monkeypatch):
    from backend.services import estoque

    requests = []
    updates = []
    polls = []

    async def start(req, tenant):
        requests.append((req, tenant))
        return {"started": True, "job_id": "job-codex-all", "total_lojas": 2}

    def poll(run_id, kind, tenant, **kwargs):
        polls.append((run_id, kind, tenant, kwargs))
        return {
            "active": False,
            "sync_meta": {"job_id": "job-codex-all", "outcome": "completed"},
        }

    monkeypatch.setattr(estoque, "sincronizar_estoque", start)
    monkeypatch.setattr(codex_actions, "_poll_progress_until_idle", poll)
    monkeypatch.setattr(
        codex_actions,
        "_update_run",
        lambda run_id, **kwargs: updates.append((run_id, kwargs)) or kwargs,
    )

    result = codex_actions._execute_estoque_sync(
        "run-all",
        {"client_id": TENANT, "params": {"loja": "__todas"}},
    )

    assert len(requests) == 1
    req, tenant = requests[0]
    assert tenant == TENANT
    assert req.todas_lojas is True
    assert req.store_id == "__todas"
    assert updates[0][1]["estoque_sync_job_id"] == "job-codex-all"
    assert updates[0][1]["estoque_sync_owned_job_id"] == "job-codex-all"
    assert polls == [
        (
            "run-all",
            "estoque_sync",
            TENANT,
            {"job_id": "job-codex-all", "owned_job": True},
        )
    ]
    assert len(result["lojas"]) == 1


def test_codex_stock_response_without_job_id_fails_closed_before_poll(monkeypatch):
    from backend.services import estoque

    async def start_without_job(_req, _tenant):
        return {"started": True}

    monkeypatch.setattr(estoque, "sincronizar_estoque", start_without_job)
    monkeypatch.setattr(
        codex_actions,
        "_poll_progress_until_idle",
        lambda *_a, **_k: pytest.fail("missing job_id must never poll tenant-global state"),
    )

    with pytest.raises(RuntimeError, match="nao informou job_id"):
        codex_actions._execute_estoque_sync(
            "run-sem-job",
            {"client_id": TENANT, "params": {"loja": "__todas"}},
        )


def test_codex_get_run_reads_only_its_tracked_stock_job(monkeypatch):
    tracked_run = {
        "run_id": "run-tracked",
        "status": "running",
        "client_id": TENANT,
        "action": {"status_kind": "estoque_sync"},
        "estoque_sync_job_id": "tracked-job",
    }
    progress_calls = []
    monkeypatch.setattr(codex_actions, "_run_load", lambda *_a, **_k: dict(tracked_run))
    monkeypatch.setattr(
        codex_actions,
        "_progress_payload",
        lambda kind, tenant, **kwargs: progress_calls.append((kind, tenant, kwargs))
        or {
            "progress": {"percentual": 25},
            "logs": [],
            "sync_meta": {"job_id": "tracked-job", "outcome": None},
        },
    )
    monkeypatch.setattr(
        codex_actions,
        "_update_run",
        lambda _run_id, **kwargs: {**tracked_run, **kwargs},
    )

    response = codex_actions.get_run("run-tracked", client_id=TENANT)

    assert progress_calls == [
        ("estoque_sync", TENANT, {"job_id": "tracked-job"})
    ]
    assert response["run"]["sync_meta"]["job_id"] == "tracked-job"


def test_codex_adopted_job_cancel_stops_polling_without_canceling_backend(monkeypatch):
    monkeypatch.setattr(
        codex_actions,
        "_run_load",
        lambda *_a, **_k: {"status": "cancel_requested"},
    )
    monkeypatch.setattr(
        codex_actions,
        "_progress_payload",
        lambda *_a, **_k: pytest.fail("adopted job must not be polled after run cancellation"),
    )

    result = codex_actions._poll_progress_until_idle(
        "run-adotado",
        "estoque_sync",
        TENANT,
        job_id="ui-job",
        owned_job=False,
    )

    assert result["cancel_requested"] is True
    assert result["sync_meta"] == {
        "job_id": "ui-job",
        "outcome": "cancelled",
        "adopted_job": True,
    }
    assert TENANT not in estoque_context.ESTOQUE_SYNC_CANCEL_FLAGS


def test_codex_owned_job_cancel_signals_only_its_exact_job(monkeypatch):
    estoque_sync.ESTOQUE_SYNC_ACTIVE[TENANT] = True
    estoque_sync.ESTOQUE_SYNC_META[TENANT] = {"job_id": "owned-job", "outcome": None}
    monkeypatch.setattr(
        codex_actions,
        "_run_load",
        lambda *_a, **_k: {"status": "cancel_requested"},
    )
    monkeypatch.setattr(
        codex_actions,
        "_progress_payload",
        lambda *_a, **_k: {
            "active": False,
            "sync_meta": {"job_id": "owned-job", "outcome": "cancelled"},
        },
    )
    monkeypatch.setattr(codex_actions, "_update_run", lambda *_a, **_k: {})

    result = codex_actions._poll_progress_until_idle(
        "run-owned",
        "estoque_sync",
        TENANT,
        job_id="owned-job",
        owned_job=True,
    )

    assert result["sync_meta"]["outcome"] == "cancelled"
    assert estoque_context.ESTOQUE_SYNC_CANCEL_FLAGS[TENANT] == {
        "job_id": "owned-job"
    }


def test_stale_cancel_request_cannot_overwrite_current_job_flag():
    estoque_sync.ESTOQUE_SYNC_ACTIVE[TENANT] = True
    estoque_sync.ESTOQUE_SYNC_META[TENANT] = {"job_id": "job-b", "outcome": None}
    estoque_sync.ESTOQUE_SYNC_CANCEL_FLAGS[TENANT] = {"job_id": "job-b"}

    accepted = estoque_sync._estoque_solicitar_cancelamento_job(TENANT, "job-a")

    assert accepted is False
    assert estoque_sync.ESTOQUE_SYNC_CANCEL_FLAGS[TENANT] == {"job_id": "job-b"}


def test_codex_worker_preserves_cancelled_as_terminal_canceled(monkeypatch):
    state = {"status": "running"}
    updates = []

    def update(_run_id, **kwargs):
        state.update(kwargs)
        updates.append(dict(kwargs))
        return dict(state)

    monkeypatch.setattr(codex_actions, "_update_run", update)
    monkeypatch.setattr(codex_actions, "_run_load", lambda *_a, **_k: dict(state))
    monkeypatch.setattr(
        codex_actions,
        "_execute_estoque_sync",
        lambda *_a, **_k: {
            "final_progress": {
                "active": False,
                "sync_meta": {"job_id": "job-cancelado", "outcome": "cancelled"},
            }
        },
    )
    monkeypatch.setattr(codex_actions, "_sync_task_from_action", lambda *_a, **_k: None)
    monkeypatch.setattr(
        codex_actions.codex_agent_runtime,
        "verification_from_result",
        lambda *_a, **_k: pytest.fail("cancelled run must not be reclassified"),
    )

    codex_actions._execute_run_worker(
        "run-cancelado",
        {
            "client_id": TENANT,
            "action": {"id": "estoque_sync", "executor": "estoque_sync"},
        },
        None,
    )

    assert state["status"] == "canceled"
    assert state["live_status"] == "Acao cancelada."
    assert state["verification"]["status"] == "canceled"
    assert not any(update.get("status") in {"completed", "partial"} for update in updates)


@pytest.mark.parametrize(
    ("outcome", "progress_success", "expected_status", "expected_verification"),
    [
        ("completed", True, "completed", "confirmed"),
        ("partial", True, "partial", "partial"),
        ("failed", True, "failed", "failed"),
        ("", False, "failed", "failed"),
    ],
)
def test_codex_worker_maps_stock_terminal_outcome_explicitly(
    monkeypatch,
    outcome,
    progress_success,
    expected_status,
    expected_verification,
):
    state = {"status": "running"}

    def update(_run_id, **kwargs):
        state.update(kwargs)
        return dict(state)

    monkeypatch.setattr(codex_actions, "_update_run", update)
    monkeypatch.setattr(codex_actions, "_run_load", lambda *_a, **_k: dict(state))
    monkeypatch.setattr(
        codex_actions,
        "_execute_estoque_sync",
        lambda *_a, **_k: {
            "final_progress": {
                "success": progress_success,
                "active": False,
                "sync_meta": {"job_id": "job-outcome", "outcome": outcome},
            }
        },
    )
    monkeypatch.setattr(codex_actions, "_sync_task_from_action", lambda *_a, **_k: None)
    monkeypatch.setattr(
        codex_actions.codex_agent_runtime,
        "verification_from_result",
        lambda *_a, **_k: pytest.fail("stock terminal outcome must be mapped explicitly"),
    )

    codex_actions._execute_run_worker(
        "run-outcome",
        {
            "client_id": TENANT,
            "action": {"id": "estoque_sync", "executor": "estoque_sync"},
        },
        None,
    )

    assert state["status"] == expected_status
    assert state["verification"]["status"] == expected_verification


def test_codex_cancel_during_verification_wins_atomic_final_transition(monkeypatch):
    state = {
        "run_id": "run-verification-race",
        "status": "queued",
        "client_id": TENANT,
        "action": {"status_kind": "estoque_sync"},
        "estoque_sync_job_id": "tracked-race",
        "estoque_sync_owned_job_id": "",
    }
    verification_started = threading.Event()
    release_verification = threading.Event()

    def load(*_args, **_kwargs):
        return dict(state)

    def update(_run_id, **kwargs):
        kwargs.pop("_client_id", None)
        state.update(kwargs)
        if kwargs.get("live_status") == "Verificando o resultado da acao.":
            verification_started.set()
            assert release_verification.wait(3)
        return dict(state)

    monkeypatch.setattr(codex_actions, "_run_load", load)
    monkeypatch.setattr(codex_actions, "_update_run", update)
    monkeypatch.setattr(codex_actions, "_sync_task_from_action", lambda *_a, **_k: None)
    monkeypatch.setattr(
        codex_actions,
        "_execute_estoque_sync",
        lambda *_a, **_k: {
            "final_progress": {
                "active": False,
                "sync_meta": {"job_id": "tracked-race", "outcome": "completed"},
            }
        },
    )
    monkeypatch.setattr(
        codex_actions.codex_agent_runtime,
        "verification_from_result",
        lambda *_a, **_k: pytest.fail("stock outcome is mapped without generic verification"),
    )

    worker = threading.Thread(
        target=codex_actions._execute_run_worker,
        args=(
            "run-verification-race",
            {
                "client_id": TENANT,
                "action": {"id": "estoque_sync", "executor": "estoque_sync"},
            },
            None,
        ),
    )
    worker.start()
    assert verification_started.wait(3)

    codex_actions.cancel_run("run-verification-race", client_id=TENANT)
    release_verification.set()
    worker.join(timeout=3)

    assert not worker.is_alive()
    assert state["status"] == "canceled"
    assert state["verification"]["status"] == "canceled"


def test_codex_worker_canceled_before_start_never_invokes_stock_backend(monkeypatch):
    state = {"status": "cancel_requested"}

    def update(_run_id, **kwargs):
        state.update(kwargs)
        return dict(state)

    monkeypatch.setattr(codex_actions, "_run_load", lambda *_a, **_k: dict(state))
    monkeypatch.setattr(codex_actions, "_update_run", update)
    monkeypatch.setattr(codex_actions, "_sync_task_from_action", lambda *_a, **_k: None)
    monkeypatch.setattr(
        codex_actions,
        "_execute_estoque_sync",
        lambda *_a, **_k: pytest.fail("canceled run must not start a stock job"),
    )

    codex_actions._execute_run_worker(
        "run-cancelado-antes",
        {
            "client_id": TENANT,
            "action": {"id": "estoque_sync", "executor": "estoque_sync"},
        },
        None,
    )

    assert state["status"] == "canceled"
    assert state["live_status"] == "Acao cancelada antes de iniciar."
    assert state["verification"]["reason"] == "cancel_requested_before_start"


def test_codex_worker_exception_cannot_overwrite_requested_cancellation(monkeypatch):
    state = {"status": "queued"}

    def update(_run_id, **kwargs):
        state.update(kwargs)
        return dict(state)

    def fail_after_cancel(*_args, **_kwargs):
        state["status"] = "cancel_requested"
        raise RuntimeError("late failure")

    monkeypatch.setattr(codex_actions, "_run_load", lambda *_a, **_k: dict(state))
    monkeypatch.setattr(codex_actions, "_update_run", update)
    monkeypatch.setattr(codex_actions, "_sync_task_from_action", lambda *_a, **_k: None)
    monkeypatch.setattr(codex_actions, "_execute_estoque_sync", fail_after_cancel)

    codex_actions._execute_run_worker(
        "run-cancel-exception",
        {
            "client_id": TENANT,
            "action": {"id": "estoque_sync", "executor": "estoque_sync"},
        },
        None,
    )

    assert state["status"] == "canceled"
    assert state["verification"]["status"] == "canceled"
    assert state["error"] == ""


def test_public_single_store_jobs_are_isolated_by_tenant_and_release_active(monkeypatch):
    stores = {
        "tenant-a": [_store("a", "A", "token-a")],
        "tenant-b": [_store("b", "B", "token-b")],
    }
    release = threading.Event()
    entered = []
    entered_lock = threading.Lock()

    monkeypatch.setattr(estoque_sync, "carregar_lojas", lambda tenant: stores[tenant])

    async def sync_one(req, tenant, **_kwargs):
        with entered_lock:
            entered.append((tenant, req.store_id))
        await asyncio.get_running_loop().run_in_executor(None, release.wait)
        return {"total": 1, "event_id": f"event-{tenant}", "historico_registrado": True}

    monkeypatch.setattr(estoque_sync, "_sincronizar_estoque_loja_impl", sync_one)
    response_a = asyncio.run(
        estoque_sync.sincronizar_estoque(
            EstoqueSyncRequest(loja="A", store_id="a"), "tenant-a"
        )
    )
    response_b = asyncio.run(
        estoque_sync.sincronizar_estoque(
            EstoqueSyncRequest(loja="B", store_id="b"), "tenant-b"
        )
    )

    deadline = time.time() + 3
    while len(entered) < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert set(entered) == {("tenant-a", "a"), ("tenant-b", "b")}
    assert estoque_sync.ESTOQUE_SYNC_ACTIVE.get("tenant-a") is True
    assert estoque_sync.ESTOQUE_SYNC_ACTIVE.get("tenant-b") is True
    assert response_a["job_id"] != response_b["job_id"]

    release.set()
    deadline = time.time() + 3
    while any(estoque_sync.ESTOQUE_SYNC_ACTIVE.get(t) for t in OTHER_TENANTS) and time.time() < deadline:
        time.sleep(0.01)
    assert not any(estoque_sync.ESTOQUE_SYNC_ACTIVE.get(t) for t in OTHER_TENANTS)
    assert {estoque_sync.ESTOQUE_SYNC_META[t]["outcome"] for t in OTHER_TENANTS} == {"completed"}
