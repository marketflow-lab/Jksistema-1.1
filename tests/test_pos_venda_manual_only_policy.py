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
from backend.services import perguntas_pos_venda_endpoints as endpoints
from backend.services import perguntas_pos_venda_state as state
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
    monkeypatch.setattr(endpoints, "_perguntas_ia_state_carregar", _fail("state should not load"), raising=False)
    monkeypatch.setattr(endpoints, "_perguntas_ia_aprovacoes_carregar", _fail("approvals should not load"), raising=False)
    monkeypatch.setattr(endpoints, "carregar_lojas", _fail("stores should not load"), raising=False)
    monkeypatch.setattr(endpoints, "_obter_cfg_ml", _fail("Mercado Livre should not be called"), raising=False)
    monkeypatch.setattr(endpoints, "_ml_pos_venda_executar_pipeline_ia", _fail("AI should not be called"), raising=False)

    result = endpoints.ml_pos_venda_automacao_poll(client_id="cliente")

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
    monkeypatch.setattr(endpoints.perguntas_pos_venda_codex, "enabled", _fail("Codex should not be inspected"))
    monkeypatch.setattr(endpoints.perguntas_pos_venda_codex, "create_job", _fail("Codex job should not be created"))
    monkeypatch.setattr(endpoints, "_obter_cfg_ml", _fail("Mercado Livre should not be called"), raising=False)
    monkeypatch.setattr(endpoints, "_ml_pos_venda_executar_pipeline_ia", _fail("AI should not be called"), raising=False)

    with pytest.raises(HTTPException) as exc_info:
        endpoints.ml_pos_venda_gerar_resposta_conversa(
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


@pytest.mark.parametrize(
    "post_sale_marker",
    [
        {"tipo": "pos_venda"},
        {"approval_type": "pos_venda"},
        {"ia_origem": "mercado_livre_pos_venda"},
        {"origem": "geracao_pos_venda"},
        {"ia_finalidade": "pos_venda"},
    ],
)
def test_whatsapp_never_selects_post_sale_suggestion(post_sale_marker):
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
    }
    ppv_state = SimpleNamespace(
        _perguntas_loja_config_obter=lambda configs, loja: configs.get(loja),
        _perguntas_loja_config_normalizar=lambda config: dict(config or {}),
    )

    selected = question_workflow._pending_question_approval(
        ppv_state,
        {"JK Pecas": {"notificar_whatsapp_aprovacoes": True}},
        [post_sale, public_question],
    )

    assert selected["id"] == "approval-public-question"


def test_typed_manual_post_sale_response_still_sends(monkeypatch):
    calls: list[tuple] = []

    def send_manual(*args):
        calls.append(args)
        return {"id": "MSG-OUT"}, {"access_token": "fixture"}

    monkeypatch.setattr(endpoints, "_obter_cfg_ml", lambda *_args: {"access_token": "fixture"}, raising=False)
    monkeypatch.setattr(endpoints, "_pos_venda_ia_limpar_resposta", lambda value, _max: str(value).strip(), raising=False)
    monkeypatch.setattr(
        endpoints,
        "_ml_pos_venda_enviar_resposta_ml",
        send_manual,
        raising=False,
    )
    monkeypatch.setattr(endpoints, "_ml_pos_venda_preparar_conversa_ia", lambda _client, _loja, cfg, conversa: (conversa, cfg), raising=False)
    monkeypatch.setattr(endpoints, "_perguntas_ia_resolver_aprovacoes_pendentes", lambda *_args, **_kwargs: [], raising=False)
    monkeypatch.setattr(endpoints, "_ml_pos_venda_memoria_registrar_resposta_enviada", lambda *_args, **_kwargs: [], raising=False)

    result = endpoints.ml_pos_venda_responder_conversa(
        PosVendaMensagemRequest(
            loja="JK Pecas",
            pack_id="PACK-1",
            buyer_id="BUYER-1",
            texto="Resposta escrita pelo operador",
            conversa={"pack_id": "PACK-1", "buyer_id": "BUYER-1"},
        ),
        client_id="cliente",
    )

    assert result["success"] is True
    assert result["resposta"] == "Resposta escrita pelo operador"
    assert len(calls) == 1


def test_manual_post_sale_route_rejects_legacy_black_jhon_proposal_before_any_call(monkeypatch):
    monkeypatch.setattr(endpoints, "_obter_cfg_ml", _fail("Mercado Livre should not be called"), raising=False)
    monkeypatch.setattr(
        endpoints.perguntas_pos_venda_codex,
        "approve_or_refresh_proposal",
        _fail("Black Jhon proposal should not be inspected"),
    )

    with pytest.raises(HTTPException) as exc_info:
        endpoints.ml_pos_venda_responder_conversa(
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
