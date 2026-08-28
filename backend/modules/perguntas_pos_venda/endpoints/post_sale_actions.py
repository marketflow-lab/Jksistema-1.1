"""Post-sale response generation and sending."""

from __future__ import annotations


from fastapi import Depends, HTTPException
from fastapi.encoders import jsonable_encoder

from backend.modules.perguntas_pos_venda.endpoints.runtime import runtime_adapter
from backend.modules.perguntas_pos_venda.endpoints.security import get_tenant_id
from backend.schemas import PosVendaGerarRespostaRequest, PosVendaMensagemRequest
from backend.services import perguntas_pos_venda_codex
from backend.services.perguntas_pos_venda_state import ML_POS_VENDA_DEFAULT_MAX_CHARS
from backend.modules.perguntas_pos_venda.endpoints.jobs import (
    _customer_reply_wait_or_raise,
)

_ml_pos_venda_enviar_resposta_ml = runtime_adapter("_ml_pos_venda_enviar_resposta_ml")
_ml_pos_venda_executar_pipeline_ia = runtime_adapter("_ml_pos_venda_executar_pipeline_ia")
_ml_pos_venda_memoria_registrar_resposta_enviada = runtime_adapter("_ml_pos_venda_memoria_registrar_resposta_enviada")
_ml_pos_venda_montar_conversa_normalizada = runtime_adapter("_ml_pos_venda_montar_conversa_normalizada")
_ml_pos_venda_pipeline_resumo = runtime_adapter("_ml_pos_venda_pipeline_resumo")
_ml_pos_venda_preparar_conversa_ia = runtime_adapter("_ml_pos_venda_preparar_conversa_ia")
_obter_cfg_ml = runtime_adapter("_obter_cfg_ml")
_perguntas_ia_marcar_processada = runtime_adapter("_perguntas_ia_marcar_processada")
_perguntas_ia_memoria_registrar_resposta_aprovada = runtime_adapter("_perguntas_ia_memoria_registrar_resposta_aprovada")
_perguntas_ia_resolver_aprovacoes_pendentes = runtime_adapter("_perguntas_ia_resolver_aprovacoes_pendentes")
_perguntas_ia_state_carregar = runtime_adapter("_perguntas_ia_state_carregar")
_perguntas_ia_state_salvar = runtime_adapter("_perguntas_ia_state_salvar")
_pos_venda_ia_limpar_resposta = runtime_adapter("_pos_venda_ia_limpar_resposta")
logger = runtime_adapter("logger")


def ml_pos_venda_gerar_resposta_conversa(
    req: PosVendaGerarRespostaRequest,
    client_id: str = Depends(get_tenant_id),
):
    raise HTTPException(
        status_code=409,
        detail="Sugestoes de IA/Black Jhon para pos-venda estao desativadas. Digite e envie a resposta manualmente.",
    )

    nome_loja = str(req.loja or "").strip()
    pack = str(req.pack_id or "").strip()
    if not nome_loja:
        raise HTTPException(status_code=400, detail="Informe a loja.")
    if not pack:
        raise HTTPException(status_code=400, detail="Informe a conversa do pos venda.")

    if perguntas_pos_venda_codex.enabled():
        job = perguntas_pos_venda_codex.create_job(
            client_id=client_id,
            task_type="post_sale",
            store=nome_loja,
            subject_key=pack,
            request={
                "pack_id": pack,
                "order_id": str(req.order_id or "").strip(),
                "buyer_id": str(req.buyer_id or "").strip(),
                "max_chars": int(req.max_chars or ML_POS_VENDA_DEFAULT_MAX_CHARS),
                "resposta_atual": str(req.resposta_atual or "").strip(),
                "orientacao_usuario": str(req.orientacao_usuario or "").strip(),
            },
            channel="app",
        )
        if req.async_mode:
            return jsonable_encoder(job)
        job = _customer_reply_wait_or_raise(client_id, job)
        if str(job.get("status") or "") != "completed":
            return jsonable_encoder(job)
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        context = result.get("contexto") if isinstance(result.get("contexto"), dict) else {}
        return jsonable_encoder({
            **job,
            "success": True,
            "loja": nome_loja,
            "pack_id": pack,
            "resposta": result.get("resposta") or "",
            "model": result.get("model") or context.get("model") or "",
            "pode_enviar_automaticamente": False,
            "decisao": context.get("decisao") or {},
            "validacao": context.get("validacao") or {},
            "ia_pipeline": _ml_pos_venda_pipeline_resumo(context),
            "audit_id": "",
            "seller_max_message_length": int(req.max_chars or ML_POS_VENDA_DEFAULT_MAX_CHARS),
            "data_sufficient": bool(job.get("data_sufficient")),
            "warnings": job.get("warnings") or [],
        })

    cfg = _obter_cfg_ml(client_id, nome_loja)
    max_chars = int(req.max_chars or ML_POS_VENDA_DEFAULT_MAX_CHARS)
    conversa, cfg = _ml_pos_venda_montar_conversa_normalizada(
        client_id,
        nome_loja,
        cfg,
        pack,
        str(req.order_id or "").strip(),
    )
    if req.buyer_id and not conversa.get("buyer_id"):
        conversa["buyer_id"] = str(req.buyer_id or "").strip()
    if str(req.resposta_atual or "").strip():
        conversa["_resposta_atual"] = str(req.resposta_atual or "").strip()[:1200]
    if str(req.orientacao_usuario or "").strip():
        conversa["_orientacao_usuario"] = str(req.orientacao_usuario or "").strip()[:1200]
    conversa, cfg = _ml_pos_venda_preparar_conversa_ia(client_id, nome_loja, cfg, conversa)
    resultado_ia, cfg = _ml_pos_venda_executar_pipeline_ia(client_id, nome_loja, cfg, conversa, max_chars)
    return jsonable_encoder({
        "success": True,
        "loja": nome_loja,
        "pack_id": pack,
        "resposta": resultado_ia.get("resposta") or "",
        "model": resultado_ia.get("model") or "",
        "pode_enviar_automaticamente": bool(resultado_ia.get("pode_enviar_automaticamente")),
        "decisao": resultado_ia.get("decisao") or {},
        "validacao": resultado_ia.get("validacao") or {},
        "ia_pipeline": _ml_pos_venda_pipeline_resumo(resultado_ia.get("contexto_ia")),
        "audit_id": resultado_ia.get("audit_id") or "",
        "seller_max_message_length": conversa.get("seller_max_message_length") or ML_POS_VENDA_DEFAULT_MAX_CHARS,
    })


def ml_pos_venda_responder_conversa(req: PosVendaMensagemRequest, client_id: str = Depends(get_tenant_id)):
    nome_loja = str(req.loja or "").strip()
    pack = str(req.pack_id or "").strip()
    buyer = str(req.buyer_id or "").strip()
    if not nome_loja:
        raise HTTPException(status_code=400, detail="Informe a loja.")
    if not pack:
        raise HTTPException(status_code=400, detail="Informe a conversa do pos venda.")
    if str(req.proposal_id or "").strip():
        raise HTTPException(
            status_code=409,
            detail="Sugestoes do Black Jhon estao desativadas no pos-venda. Digite e envie a resposta manualmente.",
        )
    cfg = _obter_cfg_ml(client_id, nome_loja)
    max_chars = int(req.max_chars or ML_POS_VENDA_DEFAULT_MAX_CHARS)
    resposta = req.texto if isinstance(req.texto, str) else str(req.texto or "")
    if not resposta.strip():
        raise HTTPException(status_code=400, detail="Resposta vazia.")
    conversa_memoria = req.conversa if isinstance(req.conversa, dict) else {}
    if conversa_memoria:
        conversa_memoria.setdefault("pack_id", pack)
        conversa_memoria.setdefault("order_id", str(req.order_id or "").strip())
        if buyer:
            conversa_memoria.setdefault("buyer_id", buyer)
    if not buyer:
        try:
            conversa_memoria, cfg = _ml_pos_venda_montar_conversa_normalizada(
                client_id,
                nome_loja,
                cfg,
                pack,
                str(req.order_id or "").strip(),
            )
            buyer = str(conversa_memoria.get("buyer_id") or "").strip()
        except Exception as exc:
            logger.warning("[ML POS VENDA] Nao foi possivel resolver buyer_id antes do envio pack=%s loja=%s: %s", pack, nome_loja, exc)
    resposta_ml, cfg = _ml_pos_venda_enviar_resposta_ml(client_id, nome_loja, cfg, pack, buyer, resposta, max_chars)
    if not conversa_memoria:
        try:
            conversa_memoria, cfg = _ml_pos_venda_montar_conversa_normalizada(
                client_id,
                nome_loja,
                cfg,
                pack,
                str(req.order_id or "").strip(),
            )
            if buyer and isinstance(conversa_memoria, dict):
                conversa_memoria.setdefault("buyer_id", buyer)
        except Exception as exc:
            logger.warning("[ML POS VENDA] Nao foi possivel carregar conversa para memoria pack=%s loja=%s: %s", pack, nome_loja, exc)
    if conversa_memoria:
        try:
            conversa_memoria, cfg = _ml_pos_venda_preparar_conversa_ia(client_id, nome_loja, cfg, conversa_memoria)
        except Exception as exc:
            logger.warning("[ML POS VENDA IA] Falha ao carregar perguntas do anuncio para memoria pack=%s loja=%s: %s", pack, nome_loja, exc)
    resolvidas = _perguntas_ia_resolver_aprovacoes_pendentes(
        client_id,
        nome_loja,
        pack_id=pack,
        status="sent_manual_pos_venda",
        motivo="pos_venda_respondido_manualmente",
        resposta=resposta,
    )
    try:
        if resolvidas:
            for approval in resolvidas:
                registrados = _ml_pos_venda_memoria_registrar_resposta_enviada(
                    client_id,
                    nome_loja,
                    conversa_memoria,
                    resposta,
                    origem="manual_pos_venda_com_aprovacao",
                    approval=approval,
                )
                if not registrados:
                    _perguntas_ia_memoria_registrar_resposta_aprovada(
                        client_id,
                        nome_loja,
                        resposta,
                        approval=approval,
                        origem="manual_pos_venda_com_aprovacao",
                    )
        else:
            _ml_pos_venda_memoria_registrar_resposta_enviada(
                client_id,
                nome_loja,
                conversa_memoria,
                resposta,
                origem="manual_pos_venda",
            )
    except Exception as exc:
        logger.warning("[ML POS VENDA IA] Falha ao registrar resposta enviada na memoria do SKU: %s", exc)
    if resolvidas:
        state = _perguntas_ia_state_carregar(client_id)
        for approval in resolvidas:
            _perguntas_ia_marcar_processada(state, nome_loja, str(approval.get("question_id") or ""), "sent_manual_pos_venda")
        _perguntas_ia_state_salvar(client_id, state)
    return jsonable_encoder({
        "success": True,
        "loja": nome_loja,
        "pack_id": pack,
        "resposta": resposta,
        "mercadolivre": resposta_ml,
        "proposal_version": None,
        "proposal_hash": "",
    })


__all__ = [
    "ml_pos_venda_gerar_resposta_conversa",
    "ml_pos_venda_responder_conversa",
]
