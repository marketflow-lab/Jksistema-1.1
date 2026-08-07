"""Perguntas and post-sale training endpoints."""

from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, HTTPException

from backend.modules.perguntas_pos_venda.endpoints.runtime import runtime_adapter
from backend.modules.perguntas_pos_venda.endpoints.security import get_tenant_id
from backend.schemas import IAChatRequest, IATreinamentoPerguntasPosVendaRequest, IATreinamentoPerguntasPosVendaSimularRequest
from backend.services.perguntas_pos_venda_state import ML_POS_VENDA_LIMITE_SEGURO, ML_RESPOSTA_PERGUNTA_MAX_CHARS

_chamar_codex_chat = runtime_adapter("_chamar_codex_chat")
_chamar_deepseek_chat = runtime_adapter("_chamar_deepseek_chat")
_chamar_gemini_chat = runtime_adapter("_chamar_gemini_chat")
_chamar_openai_responses = runtime_adapter("_chamar_openai_responses")
_chamar_vertex_ai_chat = runtime_adapter("_chamar_vertex_ai_chat")
_codex_modelo_nome_curto = runtime_adapter("_codex_modelo_nome_curto")
_gemini_nome_curto = runtime_adapter("_gemini_nome_curto")
_ia_modelo_perguntas_configurado = runtime_adapter("_ia_modelo_perguntas_configurado")
_ia_treinamento_ppv_listar_skus = runtime_adapter("_ia_treinamento_ppv_listar_skus")
_ia_treinamento_ppv_produto_por_sku = runtime_adapter("_ia_treinamento_ppv_produto_por_sku")
_ia_treinamento_ppv_produto_prompt = runtime_adapter("_ia_treinamento_ppv_produto_prompt")
_ia_treinamento_ppv_resolver = runtime_adapter("_ia_treinamento_ppv_resolver")
_ia_treinamento_ppv_salvar = runtime_adapter("_ia_treinamento_ppv_salvar")
_ia_treinamento_ppv_tipo_label = runtime_adapter("_ia_treinamento_ppv_tipo_label")
_ia_treinamento_ppv_tipo_normalizar = runtime_adapter("_ia_treinamento_ppv_tipo_normalizar")
_modelo_eh_codex = runtime_adapter("_modelo_eh_codex")
_modelo_eh_gemini_api = runtime_adapter("_modelo_eh_gemini_api")
_modelo_eh_vertex_ai = runtime_adapter("_modelo_eh_vertex_ai")
_normalizar_ia_modelo_padrao = runtime_adapter("_normalizar_ia_modelo_padrao")
_normalizar_sku_mes = runtime_adapter("_normalizar_sku_mes")
_perguntas_ia_assinatura_loja = runtime_adapter("_perguntas_ia_assinatura_loja")
_perguntas_ia_resposta_final_loja = runtime_adapter("_perguntas_ia_resposta_final_loja")
_vertex_ai_modelo_padrao = runtime_adapter("_vertex_ai_modelo_padrao")
_vertex_modelo_nome_curto = runtime_adapter("_vertex_modelo_nome_curto")


def ml_ia_treinamento_obter(loja: Optional[str] = None, client_id: str = Depends(get_tenant_id)):
    data = _ia_treinamento_ppv_resolver(client_id, loja)
    return {"success": True, **data}


def ml_ia_treinamento_salvar(req: IATreinamentoPerguntasPosVendaRequest, client_id: str = Depends(get_tenant_id)):
    data = _ia_treinamento_ppv_salvar(
        client_id,
        req.orientacoes,
        req.tipo,
        loja=req.loja,
        contexto_loja=req.contexto_loja,
        compatibilidade_autopecas=req.compatibilidade_autopecas,
        proibicoes=req.proibicoes,
        sku=req.sku,
        notas_sku=req.notas_sku,
        exemplos=req.exemplos,
    )
    return {"success": True, **data}


def ml_ia_treinamento_listar_skus(client_id: str = Depends(get_tenant_id)):
    return {"success": True, "produtos": _ia_treinamento_ppv_listar_skus(client_id)}


def ml_ia_treinamento_simular(req: IATreinamentoPerguntasPosVendaSimularRequest, client_id: str = Depends(get_tenant_id)):
    pergunta = str(req.pergunta or "").strip()
    if not pergunta:
        raise HTTPException(status_code=400, detail="Informe uma pergunta para simular.")

    tipo_treinamento = _ia_treinamento_ppv_tipo_normalizar(req.tipo)
    contexto_tipo = "pos-venda" if tipo_treinamento == "pos_venda" else "pergunta de anuncio"
    limite_resposta = (
        ML_POS_VENDA_LIMITE_SEGURO
        if tipo_treinamento == "pos_venda"
        else ML_RESPOSTA_PERGUNTA_MAX_CHARS
    )
    contexto_extra = str(req.contexto or "").strip()
    loja = str(req.loja or "").strip()
    mensagem = (
        f"Simule um rascunho via IA de {contexto_tipo} para enviar a um comprador do Mercado Livre. "
        f"Use as orientacoes salvas no treinamento de {_ia_treinamento_ppv_tipo_label(tipo_treinamento)}. "
        "A resposta deve ser cordial, objetiva e comercial, sem inventar dados tecnicos, prazo, estoque, garantia ou compatibilidade. "
        "Nunca se apresente como IA, assistente, Gemini, Vertex ou JK Sistema. "
        "Responda como a equipe da loja, sem mencionar sistema interno, app, prompt, JSON, modelo ou treinamento. "
        f"Finalize exatamente com: {_perguntas_ia_assinatura_loja(loja)} "
        f"Se faltar informacao essencial, peÃ§a a informacao de forma educada. "
        f"Mantenha a resposta com no maximo {limite_resposta} caracteres para evitar falha no Mercado Livre.\n\n"
        f"Pergunta do comprador:\n{pergunta}"
    )
    sku_selecionado = _normalizar_sku_mes(str(req.sku or "").strip())
    produto_sku = _ia_treinamento_ppv_produto_por_sku(client_id, sku_selecionado) if sku_selecionado else {}
    produto_prompt = _ia_treinamento_ppv_produto_prompt(produto_sku)
    if produto_prompt:
        mensagem += produto_prompt
    if contexto_extra:
        mensagem += f"\n\nContexto adicional informado pelo usuario:\n{contexto_extra}"

    payload = IAChatRequest(
        message=mensagem,
        page="Perguntas e pÃ³s venda",
        context={
            "modulo": "perguntas_pos_venda",
            "tipo": "treinamento_ia",
            "tipo_treinamento": tipo_treinamento,
            "loja": loja,
            "sku": sku_selecionado,
            "produto": produto_sku,
        },
        model=req.model,
    )
    model_req = _normalizar_ia_modelo_padrao(str(req.model or "").strip() or _ia_modelo_perguntas_configurado())
    payload.model = model_req

    if _modelo_eh_codex(model_req):
        resposta = _chamar_codex_chat(payload, client_id)
        model_usado = f"codex:{_codex_modelo_nome_curto(model_req)}"
    elif _modelo_eh_vertex_ai(model_req):
        resposta = _chamar_vertex_ai_chat(payload, client_id)
        model_usado = f"vertex:{_vertex_modelo_nome_curto(model_req) or _vertex_ai_modelo_padrao()}"
    elif _modelo_eh_gemini_api(model_req):
        resposta = _chamar_gemini_chat(payload, client_id)
        model_usado = f"gemini:{_gemini_nome_curto(model_req) or 'gemini-2.5-flash'}"
    elif model_req.startswith("deepseek-"):
        resposta = _chamar_deepseek_chat(payload, client_id)
        model_usado = model_req
    else:
        resposta = _chamar_openai_responses(payload, client_id)
        model_usado = model_req or (os.getenv("OPENAI_MODEL") or "gpt-5.4-nano").strip()

    resposta_final = _perguntas_ia_resposta_final_loja(resposta, loja)
    if not resposta_final:
        raise HTTPException(status_code=502, detail="IA nao gerou resposta para a simulacao.")
    return {"success": True, "model": model_usado, "resposta": resposta_final}


__all__ = [
    "ml_ia_treinamento_obter",
    "ml_ia_treinamento_salvar",
    "ml_ia_treinamento_listar_skus",
    "ml_ia_treinamento_simular",
]
