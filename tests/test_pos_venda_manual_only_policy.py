from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.schemas.perguntas_pos_venda import (
    PerguntasAprovacaoRequest,
    PosVendaGerarRespostaRequest,
    PosVendaMensagemRequest,
)
from backend.modules.perguntas_pos_venda.endpoints import approvals as endpoints
from backend.modules.perguntas_pos_venda.endpoints import post_sale_actions
from backend.modules.perguntas_pos_venda.endpoints import post_sale_automation
from backend.services import perguntas_pos_venda_codex as codex_jobs
from backend.services import perguntas_pos_venda_state as state
from backend.services import codex_actions
from backend.services import codex_agent_runtime, perguntas_pos_venda_codex
from backend.services.whatsapp.approvals import question_workflow


def _fail(message: str):
    def raiser(*_args, **_kwargs):
        raise AssertionError(message)

    return raiser


def test_post_sale_automatic_flag_is_always_false_when_normalized_and_saved(tmp_path, monkeypatch):
    normalized = state._perguntas_loja_config_normalizar({
        "responder_automaticamente": True,
        "usar_pos_venda": True,
        "habilitar_pos_venda_automatico": True,
    })
    assert normalized["responder_automaticamente"] is True
    assert normalized["habilitar_pos_venda_automatico"] is False

    monkeypatch.setattr(state, "get_tenant_path", lambda _client: str(tmp_path), raising=False)
    monkeypatch.setattr(state, "dt", SimpleNamespace(datetime=datetime), raising=False)
    saved = state._perguntas_loja_config_salvar(
        "cliente",
        "JK Pecas",
        responder_automaticamente=True,
        solicitar_aprovacao=True,
        notificar_whatsapp_aprovacoes=True,
        habilitar_pos_venda_automatico=True,
        intervalo_minutos=5,
    )

    persisted = json.loads((tmp_path / "perguntas_pos_venda_lojas_config.json").read_text(encoding="utf-8"))
    assert saved["habilitar_pos_venda_automatico"] is False
    assert persisted["JK Pecas"]["habilitar_pos_venda_automatico"] is False


def test_post_sale_poll_is_noop_without_loading_ml_or_ai(monkeypatch):
    monkeypatch.setattr(post_sale_automation, "_perguntas_ia_state_carregar", _fail("state should not load"), raising=False)
    monkeypatch.setattr(post_sale_automation, "_perguntas_ia_aprovacoes_carregar", _fail("approvals should not load"), raising=False)
    monkeypatch.setattr(post_sale_automation, "carregar_lojas", _fail("stores should not load"), raising=False)
    monkeypatch.setattr(post_sale_automation, "_obter_cfg_ml", _fail("Mercado Livre should not be called"), raising=False)
    monkeypatch.setattr(post_sale_automation, "_ml_pos_venda_executar_pipeline_ia", _fail("AI should not be called"), raising=False)

    result = post_sale_automation.ml_pos_venda_automacao_poll(client_id="cliente")

    assert result == {
        "success": True,
        "disabled": True,
        "motivo": "pos_venda_somente_manual",
        "novas_pendentes": [],
        "pendentes": [],
        "enviadas": [],
        "erros": [],
    }


def test_post_sale_suggestion_is_rejected_before_codex_or_ai(monkeypatch):
    monkeypatch.setattr(post_sale_actions.perguntas_pos_venda_codex, "enabled", _fail("Codex should not be inspected"))
    monkeypatch.setattr(post_sale_actions.perguntas_pos_venda_codex, "create_job", _fail("Codex job should not be created"))
    monkeypatch.setattr(post_sale_actions, "_obter_cfg_ml", _fail("Mercado Livre should not be called"), raising=False)
    monkeypatch.setattr(post_sale_actions, "_ml_pos_venda_executar_pipeline_ia", _fail("AI should not be called"), raising=False)

    with pytest.raises(HTTPException) as exc_info:
        post_sale_actions.ml_pos_venda_gerar_resposta_conversa(
            PosVendaGerarRespostaRequest(loja="JK Pecas", pack_id="PACK-1"),
            client_id="cliente",
        )

    assert exc_info.value.status_code == 409
    assert "desativadas" in str(exc_info.value.detail)


def test_post_sale_approval_is_hidden_without_remote_validation(monkeypatch):
    historical = {
        "id": "approval-post-sale",
        "tipo": "pos_venda",
        "status": "pending",
        "loja": "JK Pecas",
        "pack_id": "PACK-1",
        "resposta_sugerida": "Sugestao historica",
    }
    monkeypatch.setattr(endpoints, "_perguntas_ia_aprovacoes_carregar", lambda _client: [historical], raising=False)
    monkeypatch.setattr(endpoints, "_obter_cfg_ml", _fail("Mercado Livre should not be called"), raising=False)
    monkeypatch.setattr(endpoints, "_perguntas_ia_aprovacoes_salvar", _fail("history should not be rewritten"), raising=False)

    result = endpoints.ml_perguntas_aprovacoes_listar(client_id="cliente")

    assert result == {"success": True, "pendentes": []}


def test_public_approval_from_old_ai_contract_is_hidden(monkeypatch):
    historical = {
        "id": "approval-public-old",
        "status": "pending",
        "loja": "JK Pecas",
        "question_id": "Q-OLD",
        "resposta_sugerida": "Resposta generica antiga.",
        "codex_job_id": "job-old",
        "ia_origem": "mercado_livre_perguntas",
    }
    saved: list[list[dict]] = []
    monkeypatch.setattr(endpoints, "_perguntas_ia_aprovacoes_carregar", lambda _client: [historical], raising=False)
    monkeypatch.setattr(endpoints, "_perguntas_ia_aprovacoes_salvar", lambda _client, data: saved.append(data), raising=False)
    monkeypatch.setattr(endpoints.perguntas_pos_venda_codex, "get_job", lambda _client, _job: None)
    monkeypatch.setattr(endpoints, "_obter_cfg_ml", lambda *_args: {}, raising=False)
    monkeypatch.setattr(endpoints, "_perguntas_ia_limpar_resposta", lambda value: str(value).strip(), raising=False)
    monkeypatch.setattr(
        endpoints,
        "_perguntas_ia_pergunta_respondida_ml",
        lambda *_args: (False, {"id": "Q-1", "status": "UNANSWERED"}, {}),
        raising=False,
    )

    result = endpoints.ml_perguntas_aprovacoes_listar(client_id="cliente")

    assert result == {"success": True, "pendentes": []}
    assert historical["status"] == "stale_contract"
    assert saved and saved[-1][0]["status"] == "stale_contract"


def test_existing_post_sale_approval_cannot_send(monkeypatch):
    historical = {
        "id": "approval-post-sale",
        "approval_type": "pos_venda",
        "status": "pending",
        "loja": "JK Pecas",
        "pack_id": "PACK-1",
        "question_id": "pos_venda:PACK-1:MSG-1",
        "resposta_sugerida": "Sugestao historica",
    }
    monkeypatch.setattr(endpoints, "_perguntas_ia_aprovacoes_carregar", lambda _client: [historical], raising=False)
    monkeypatch.setattr(endpoints, "_obter_cfg_ml", _fail("Mercado Livre should not be called"), raising=False)
    monkeypatch.setattr(endpoints, "_ml_pos_venda_enviar_resposta_ml", _fail("post-sale suggestion must not send"), raising=False)

    with pytest.raises(HTTPException) as exc_info:
        endpoints.ml_perguntas_aprovacoes_aprovar(
            PerguntasAprovacaoRequest(approval_id="approval-post-sale"),
            client_id="cliente",
        )

    assert exc_info.value.status_code == 409
    assert historical["status"] == "pending"


def test_public_approval_without_current_ai_job_is_quarantined(monkeypatch):
    historical = {
        "id": "approval-public-legacy",
        "status": "pending",
        "loja": "JK Pecas",
        "question_id": "Q-1",
        "resposta_sugerida": "Resposta antiga.",
        "ia_origem": "mercado_livre_perguntas",
    }
    saved: list[list[dict]] = []
    monkeypatch.setattr(endpoints, "_perguntas_ia_aprovacoes_carregar", lambda _client: [historical], raising=False)
    monkeypatch.setattr(endpoints, "_perguntas_ia_aprovacoes_salvar", lambda _client, data: saved.append(data), raising=False)
    monkeypatch.setattr(endpoints, "_obter_cfg_ml", lambda *_args: {}, raising=False)
    monkeypatch.setattr(endpoints, "_perguntas_ia_limpar_resposta", lambda value: str(value).strip(), raising=False)
    monkeypatch.setattr(
        endpoints,
        "_perguntas_ia_pergunta_respondida_ml",
        lambda *_args: (False, {"id": "Q-1", "status": "UNANSWERED"}, {}),
        raising=False,
    )
    monkeypatch.setattr(endpoints, "_perguntas_ia_enviar_resposta_ml", _fail("legacy draft must not send"), raising=False)

    with pytest.raises(HTTPException) as exc_info:
        endpoints.ml_perguntas_aprovacoes_aprovar(
            PerguntasAprovacaoRequest(approval_id="approval-public-legacy"),
            client_id="cliente",
        )

    assert exc_info.value.status_code == 409
    assert historical["status"] == "stale_contract"
    assert saved and saved[-1][0]["status"] == "stale_contract"


@pytest.mark.parametrize(
    "post_sale_marker",
    [
        {"tipo": "pos_venda"},
        {"approval_type": "pos_venda"},
        {"ia_origem": "mercado_livre_pos_venda"},
        {"origem": "geracao_pos_venda"},
        {"ia_finalidade": "pos_venda"},
        {"tipo": "pos-venda"},
        {"approval_type": "pos venda"},
        {"ia_origem": "mercado-livre-pos-venda"},
    ],
)
def test_whatsapp_never_selects_post_sale_suggestion(post_sale_marker, monkeypatch):
    monkeypatch.setattr(codex_jobs, "approval_job_current", lambda _client, job_id: job_id == "job-current")
    post_sale = {
        "id": "approval-post-sale",
        "status": "pending",
        "loja": "JK Pecas",
        "resposta_sugerida": "Sugestao historica",
        **post_sale_marker,
    }
    public_question = {
        "id": "approval-public-question",
        "status": "pending",
        "loja": "JK Pecas",
        "resposta_sugerida": "Resposta para pergunta publica",
        "ia_origem": "mercado_livre_perguntas",
        "codex_job_id": "job-current",
    }
    ppv_state = SimpleNamespace(
        _perguntas_loja_config_obter=lambda configs, loja: configs.get(loja),
        _perguntas_loja_config_normalizar=lambda config: dict(config or {}),
    )

    selected = question_workflow._pending_question_approval(
        ppv_state,
        {"JK Pecas": {"notificar_whatsapp_aprovacoes": True}},
        [post_sale, public_question],
        client_id="cliente",
    )

    assert selected["id"] == "approval-public-question"


def test_whatsapp_ignores_public_draft_without_current_ai_job(monkeypatch):
    monkeypatch.setattr(codex_jobs, "get_job", lambda _client, _job: None)
    ppv_state = SimpleNamespace(
        _perguntas_loja_config_obter=lambda configs, loja: configs.get(loja),
        _perguntas_loja_config_normalizar=lambda config: dict(config or {}),
    )
    approval = {
        "id": "legacy-public-question",
        "status": "pending",
        "loja": "JK Pecas",
        "resposta_sugerida": "Resposta antiga.",
        "codex_job_id": "job-legado",
        "ia_origem": "mercado_livre_perguntas",
    }

    selected = question_workflow._pending_question_approval(
        ppv_state,
        {"JK Pecas": {"notificar_whatsapp_aprovacoes": True}},
        [approval],
        client_id="cliente",
    )

    assert selected is None


def test_typed_manual_post_sale_response_still_sends(monkeypatch):
    calls: list[tuple] = []
    literal = "  Resposta escrita pelo operador.  \n\nAssinatura literal.  "

    def send_manual(*args):
        calls.append(args)
        return {"id": "MSG-OUT"}, {"access_token": "fixture"}

    monkeypatch.setattr(post_sale_actions, "_obter_cfg_ml", lambda *_args: {"access_token": "fixture"}, raising=False)
    monkeypatch.setattr(
        post_sale_actions,
        "_ml_pos_venda_enviar_resposta_ml",
        send_manual,
        raising=False,
    )
    monkeypatch.setattr(post_sale_actions, "_ml_pos_venda_preparar_conversa_ia", lambda _client, _loja, cfg, conversa: (conversa, cfg), raising=False)
    monkeypatch.setattr(post_sale_actions, "_perguntas_ia_resolver_aprovacoes_pendentes", lambda *_args, **_kwargs: [], raising=False)
    monkeypatch.setattr(post_sale_actions, "_ml_pos_venda_memoria_registrar_resposta_enviada", lambda *_args, **_kwargs: [], raising=False)

    result = post_sale_actions.ml_pos_venda_responder_conversa(
        PosVendaMensagemRequest(
            loja="JK Pecas",
            pack_id="PACK-1",
            buyer_id="BUYER-1",
            texto=literal,
            conversa={"pack_id": "PACK-1", "buyer_id": "BUYER-1"},
        ),
        client_id="cliente",
    )

    assert result["success"] is True
    assert result["resposta"] == literal
    assert len(calls) == 1
    assert calls[0][5] == literal


def test_manual_post_sale_route_rejects_legacy_black_jhon_proposal_before_any_call(monkeypatch):
    monkeypatch.setattr(post_sale_actions, "_obter_cfg_ml", _fail("Mercado Livre should not be called"), raising=False)
    monkeypatch.setattr(
        post_sale_actions.perguntas_pos_venda_codex,
        "approve_or_refresh_proposal",
        _fail("Black Jhon proposal should not be inspected"),
    )

    with pytest.raises(HTTPException) as exc_info:
        post_sale_actions.ml_pos_venda_responder_conversa(
            PosVendaMensagemRequest(
                loja="JK Pecas",
                pack_id="PACK-1",
                buyer_id="BUYER-1",
                texto="Sugestao antiga",
                proposal_id="proposal-post-sale-legacy",
            ),
            client_id="cliente",
        )

    assert exc_info.value.status_code == 409
    assert "Black Jhon" in str(exc_info.value.detail)


def test_black_jhon_has_no_post_sale_action_or_safe_executor():
    specs = codex_actions._manual_specs()

    assert "ml.pos_venda_responder" not in specs
    assert "ml_pos_venda_responder" not in codex_actions.SAFE_EXECUTORS
    assert "ml.pos_venda_responder" not in codex_agent_runtime.WHATSAPP_NON_DESTRUCTIVE_ACTIONS
    assert not hasattr(codex_actions, "_execute_ml_pos_venda_responder")
    assert codex_actions._select_action("Responda o pos-venda no Mercado Livre", specs) is None
    assert codex_actions._select_action("Aprove a resposta do pos-venda", specs) is None

    public_question = codex_actions._select_action("Responda a pergunta do Mercado Livre", specs)
    assert public_question is not None
    assert public_question.id == "ml.pergunta_responder"


def test_post_sale_codex_job_creation_is_rejected_before_storage(monkeypatch):
    monkeypatch.setattr(
        perguntas_pos_venda_codex,
        "_runtime_info_base",
        _fail("storage should not be initialized"),
    )

    with pytest.raises(PermissionError, match="Black Jhon"):
        perguntas_pos_venda_codex.create_job(
            client_id="cliente",
            task_type="post_sale",
            store="JK Pecas",
            subject_key="PACK-1",
            request={"pack_id": "PACK-1"},
        )


def test_legacy_post_sale_codex_worker_cancels_without_loading_ai(monkeypatch):
    saved: dict = {}
    job = {
        "job_id": "legacy-post-sale-job",
        "client_id": "cliente",
        "task_type": "post_sale",
        "status": "queued",
        "result": {"resposta": "Sugestao antiga"},
        "last_partial_result": {"resposta": "Outra sugestao antiga"},
    }

    monkeypatch.setattr(perguntas_pos_venda_codex, "_runtime_info_base", lambda: "info")
    monkeypatch.setattr(
        perguntas_pos_venda_codex.codex_assistant_storage,
        "codex_assistant_customer_reply_job_claim",
        lambda *_args, **_kwargs: dict(job),
    )
    monkeypatch.setattr(
        perguntas_pos_venda_codex.codex_assistant_storage,
        "codex_assistant_customer_reply_job_save",
        lambda _info, _client, data, **_kwargs: saved.update(data) or dict(data),
    )
    monkeypatch.setattr(
        perguntas_pos_venda_codex,
        "_load_post_sale_context",
        _fail("AI/context must not run"),
    )

    perguntas_pos_venda_codex._run_job("cliente", "legacy-post-sale-job")

    assert saved["status"] == "cancelled"
    assert saved["error"] == "pos_venda_somente_manual"
    assert "result" not in saved
    assert "last_partial_result" not in saved


@pytest.mark.parametrize(
    "marker",
    [
        {"tipo": "pos-venda"},
        {"approval_type": "pos venda"},
        {"origem": "geracao-pos-venda"},
        {"ia_origem": "mercado livre pos venda"},
    ],
)
def test_backend_recognizes_legacy_post_sale_markers(marker):
    assert endpoints._aprovacao_eh_pos_venda(marker) is True
