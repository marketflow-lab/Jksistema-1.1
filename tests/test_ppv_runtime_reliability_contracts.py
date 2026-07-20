from __future__ import annotations

import datetime
import hashlib
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from backend.schemas.perguntas_pos_venda import PerguntasAprovacaoRequest
from backend.services import codex_assistant_storage
from backend.services import perguntas_pos_venda_automacao as automation
from backend.services import perguntas_pos_venda_codex as orchestrator
from backend.services import perguntas_pos_venda_endpoints as endpoints
from backend.services import perguntas_pos_venda_store as store
from backend.services.whatsapp.approvals import question_workflow


def _runtime(tmp_path):
    return SimpleNamespace(PASTA_INFO=str(tmp_path))


def test_waiting_retry_is_active_and_never_reported_as_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    monkeypatch.setattr(orchestrator, "_RECOVERY_STARTED", True)
    scheduled: list[str] = []
    monkeypatch.setattr(
        orchestrator,
        "_schedule_retry_timer",
        lambda job: scheduled.append(str(job.get("job_id") or "")),
    )
    job = {
        "job_id": "job-waiting-retry",
        "profile": orchestrator.PROFILE,
        "client_id": "cliente",
        "task_type": orchestrator.TASK_TYPE_PUBLIC_QUESTION,
        "subject_key": "question:Q-WAIT",
        "event_subject_key": "Q-WAIT",
        "store": "JK Pecas",
        "status": "waiting_retry",
        "agent_state": "pesquisando",
        "current_step": "consultar",
        "deadline_at_epoch": time.time() + 120,
        "next_retry_at_epoch": time.time() + 30,
        "retry_count": 1,
        "retry_policy": "bounded",
        "created_at": "2026-07-20T14:00:00Z",
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "cliente", job
    )

    public = orchestrator.get_job("cliente", job["job_id"])
    resumed = orchestrator.resume_incomplete_job("cliente", job["job_id"])
    monkeypatch.setattr(orchestrator, "wait_job", lambda *_args, **_kwargs: public)
    endpoint_view = endpoints._customer_reply_wait_or_raise("cliente", public)

    assert "waiting_retry" in orchestrator.ACTIVE_STATUSES
    assert "waiting_retry" not in orchestrator.TERMINAL_STATUSES
    assert public["status"] == resumed["status"] == endpoint_view["status"] == "waiting_retry"
    assert public["queued"] is True
    assert public["success"] is False
    assert public["result"] == {}
    assert resumed["job_id"] == job["job_id"]
    assert scheduled == [job["job_id"]]


def test_approval_reconciles_crash_after_remote_send_idempotently(monkeypatch):
    response = "Resposta ja aceita pelo Mercado Livre."
    draft_hash = hashlib.sha256(response.encode("utf-8")).hexdigest()
    key = hashlib.sha256(
        f"cliente|JK Pecas|question-1|{draft_hash}".encode("utf-8")
    ).hexdigest()
    approvals = [{
        "id": "approval-reconcile",
        "status": "sending",
        "loja": "JK Pecas",
        "question_id": "question-1",
        "resposta_sugerida": response,
        "send_idempotency_key": key,
        "send_draft_hash": draft_hash,
    }]
    saved: list[list[dict]] = []
    sends: list[tuple] = []
    monkeypatch.setattr(endpoints, "dt", datetime, raising=False)
    monkeypatch.setattr(
        endpoints,
        "_perguntas_ia_aprovacoes_carregar",
        lambda _client: approvals,
        raising=False,
    )

    def save_approvals(_client, values):
        approvals[:] = values
        saved.append([dict(item) for item in values])

    monkeypatch.setattr(
        endpoints,
        "_perguntas_ia_aprovacoes_salvar",
        save_approvals,
        raising=False,
    )
    monkeypatch.setattr(endpoints, "_obter_cfg_ml", lambda *_args: {}, raising=False)
    monkeypatch.setattr(
        endpoints,
        "_perguntas_ia_limpar_resposta",
        lambda value: str(value).strip(),
        raising=False,
    )
    monkeypatch.setattr(
        endpoints,
        "_perguntas_ia_pergunta_respondida_ml",
        lambda *_args: (
            True,
            {
                "id": "question-1",
                "status": "ANSWERED",
                "answer": {"text": response},
            },
            {},
        ),
        raising=False,
    )
    monkeypatch.setattr(
        endpoints,
        "_perguntas_ia_enviar_resposta_ml",
        lambda *args: sends.append(args),
        raising=False,
    )
    request = PerguntasAprovacaoRequest(
        approval_id="approval-reconcile",
        resposta=response,
        idempotency_key=key,
    )

    first = endpoints.ml_perguntas_aprovacoes_aprovar(request, "cliente")
    replay = endpoints.ml_perguntas_aprovacoes_aprovar(request, "cliente")

    assert first["idempotent_replay"] is True
    assert first["approval"]["status"] == "sent_reconciled"
    assert first["approval"]["resolution_reason"] == "idempotent_reconciliation"
    assert replay["idempotent_replay"] is True
    assert replay["approval"]["status"] == "sent_reconciled"
    assert sends == []
    assert saved[-1][0]["send_idempotency_key"] == key


def test_store_notification_gate_blocks_whatsapp_when_disabled(monkeypatch):
    approval = {
        "id": "approval-gated",
        "status": "pending",
        "loja": "JK Pecas",
        "pergunta": "Tem garantia?",
        "resposta_sugerida": "Sim, possui garantia.",
    }
    ppv_state = SimpleNamespace(
        _perguntas_loja_configs_carregar=lambda _client: {
            "JK Pecas": {"notificar_whatsapp_aprovacoes": False}
        },
        _perguntas_ia_aprovacoes_carregar=lambda _client: [approval],
        _perguntas_loja_config_obter=lambda configs, loja: configs.get(loja),
        _perguntas_loja_config_normalizar=lambda config: dict(config or {}),
    )
    sent: list[dict] = []
    monkeypatch.setattr(
        question_workflow,
        "_post_interactive_approval",
        lambda _config, **payload: sent.append(payload) or {"status": "sent"},
        raising=False,
    )
    monkeypatch.setattr(
        question_workflow,
        "_question_active_approval",
        lambda *_args, **_kwargs: (None, "", None),
        raising=False,
    )

    question_workflow._forward_question_approval_for_binding(
        {},
        {},
        ppv_state,
        {},
        "cliente",
        "operador",
        "subject-1",
    )

    assert sent == []


@pytest.mark.parametrize("specific_status", ["template_not_approved", "waiting_free_window"])
def test_blocked_specific_question_template_selects_approved_generic_utility(
    monkeypatch,
    specific_status,
):
    proactive_calls: list[dict] = []
    recorded: list[dict] = []

    def post_proactive(_config, payload):
        proactive_calls.append(dict(payload))
        if payload.get("template_name") == "jk_joao_aprovacao_pendente":
            return {"success": True, "status": "sent"}
        raise AssertionError(f"template inesperado: {payload.get('template_name')}")

    class BridgeStore:
        def record_notification(self, fingerprint, **details):
            recorded.append({"fingerprint": fingerprint, **details})

    monkeypatch.setattr(question_workflow, "_post_proactive", post_proactive, raising=False)
    monkeypatch.setattr(
        question_workflow,
        "_worker_health",
        lambda _config: {
            "templates": [
                {
                    "name": "jk_black_jhon_nova_pergunta",
                    "category": "UTILITY",
                    "status": "PENDING",
                },
                {
                    "name": "jk_joao_aprovacao_pendente",
                    "category": "UTILITY",
                    "status": "APPROVED",
                },
            ]
        },
        raising=False,
    )
    monkeypatch.setattr(question_workflow, "_bridge_store", lambda: BridgeStore(), raising=False)
    monkeypatch.setattr(question_workflow, "_now", lambda: "2026-07-20T14:00:00Z", raising=False)
    notifications: dict[str, dict] = {}

    question_workflow._record_blocked_question_notification(
        {"machine_id": "machine-1"},
        notifications,
        "notification-key",
        "approval-1",
        "subject-1",
        "JK Pecas",
        specific_status,
    )

    assert [item["template_name"] for item in proactive_calls] == [
        "jk_joao_aprovacao_pendente",
    ]
    assert proactive_calls[0]["subject_id"] == "subject-1"
    assert notifications["notification-key"]["status"] == "sent"
    assert notifications["notification-key"]["blocked"] is False
    assert recorded[-1]["status"] == "sent"


def test_disabled_post_sale_automation_never_schedules_post_sale(monkeypatch):
    calls: list[tuple[str, str, str, int]] = []
    monkeypatch.setattr(automation, "_perguntas_automacao_bg_tenants", lambda: ["cliente"])
    monkeypatch.setattr(
        automation,
        "_perguntas_loja_configs_carregar",
        lambda _client: {
            "JK Pecas": {
                "responder_automaticamente": True,
                "habilitar_pos_venda_automatico": False,
                "intervalo_minutos": 5,
            }
        },
        raising=False,
    )
    monkeypatch.setattr(
        automation,
        "_perguntas_loja_config_normalizar",
        lambda config: dict(config or {}),
        raising=False,
    )
    monkeypatch.setattr(
        automation,
        "carregar_lojas",
        lambda _client: [{
            "nome": "JK Pecas",
            "integracoes": {"mercadolivre": {"access_token": "fixture"}},
        }],
        raising=False,
    )
    monkeypatch.setattr(
        automation,
        "_ml_oauth_status",
        lambda _config: {"conectado": True},
        raising=False,
    )
    monkeypatch.setattr(
        automation,
        "_perguntas_automacao_bg_executar",
        lambda client, loja, kind, interval: calls.append((client, loja, kind, interval)),
    )

    automation._perguntas_automacao_bg_tick()

    assert calls == [("cliente", "JK Pecas", "perguntas", 300)]


@pytest.mark.parametrize(
    ("mode", "initial_cursor", "detail", "expected_calls", "bootstrap_complete"),
    [
        ("bootstrap", 10000, "Cobertura parcial", [], True),
        ("incremental", 0, "local_rate_limited", [0], False),
    ],
)
def test_post_sale_cursor_and_rate_limit_failures_leave_terminal_failed_state(
    tmp_path,
    monkeypatch,
    mode,
    initial_cursor,
    detail,
    expected_calls,
    bootstrap_complete,
):
    class Logger:
        def warning(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(endpoints, "logger", Logger(), raising=False)
    monkeypatch.setattr(
        endpoints,
        "_integracoes_nome_normalizado",
        lambda value: str(value or "").strip().casefold(),
        raising=False,
    )
    monkeypatch.setattr(
        endpoints,
        "_perguntas_ia_diagnostico_texto",
        lambda value, _limit=500: str(value or "").strip(),
        raising=False,
    )
    calls: list[int] = []

    def remote_failure(**kwargs):
        calls.append(int(kwargs["offset"]))
        raise HTTPException(status_code=429, detail=detail)

    monkeypatch.setattr(endpoints, "_ml_pos_venda_listar_conversas_remoto", remote_failure)
    if initial_cursor:
        store.begin_sync(tmp_path, "JK Pecas", "SELLER-1", mode, 365, cursor=0)
        store.update_sync_progress(
            tmp_path,
            "JK Pecas",
            "SELLER-1",
            cursor=initial_cursor,
            conversations_saved=312,
            orders_scanned=initial_cursor,
        )
        store.fail_sync(
            tmp_path,
            "JK Pecas",
            "SELLER-1",
            "falha anterior",
            cursor=initial_cursor,
        )

    endpoints._ml_pos_venda_sync_worker(
        client_id="cliente",
        tenant_path=str(tmp_path),
        loja="JK Pecas",
        seller_id="SELLER-1",
        mode=mode,
        coverage_days=365 if mode == "bootstrap" else 30,
        max_orders=10000,
    )

    state = store.get_state(tmp_path, "JK Pecas", "SELLER-1")
    assert calls == expected_calls
    assert state["status"] == "failed"
    assert state["status"] != "running"
    assert state["cursor"] == initial_cursor
    assert state["bootstrap_complete"] is bootstrap_complete
    assert detail in state["error"]
