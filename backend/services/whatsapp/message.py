"""Phone identity, message reading and prompt construction for WhatsApp."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any, Optional

from fastapi import HTTPException

from backend.services.whatsapp import media as whatsapp_media
from backend.services.whatsapp import settings as whatsapp_settings


def mask_phone(value: Any) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    if len(digits) < 5:
        return ""
    return f"+{digits[:2]} **** *** {digits[-4:]}"


def normalize_registered_phone(value: Any) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) in {10, 11}:
        digits = "55" + digits
    if len(digits) < 10 or len(digits) > 15 or len(set(digits)) == 1:
        raise HTTPException(status_code=400, detail="Informe um numero de WhatsApp valido, com DDD.")
    return digits


def message_phone(
    config: dict[str, Any],
    message: dict[str, Any],
    *,
    normalize_phone: Callable[[Any], str],
) -> str:
    phone = normalize_phone(message.get("wa_id") or message.get("phone") or message.get("phone_number"))
    if phone:
        return phone
    subject = str(message.get("subject_id") or "").strip()
    if subject and subject == str(config.get("subject_id") or "").strip():
        return normalize_phone(config.get("personal_phone"))
    return ""


def message_request_text(
    message: dict[str, Any],
    transcription: Optional[dict[str, Any]] = None,
) -> str:
    parts: list[str] = []
    body = str(message.get("text_body") or "").strip()
    if body:
        parts.append(body)
    if isinstance(transcription, dict) and transcription.get("success") and str(transcription.get("text") or "").strip():
        parts.append(str(transcription.get("text") or "").strip())
    return "\n\n".join(parts).strip()[:12000]


def _append_query_scope_prompt(parts: list[str], query_policy: dict[str, Any]) -> str:
    store_mode = str(query_policy.get("store_mode") or "").strip()
    store = str(query_policy.get("store") or "").strip()
    scoped_stores = [
        str(item or "").strip() for item in (query_policy.get("stores") or []) if str(item or "").strip()
    ]
    if store_mode == "all" and scoped_stores:
        parts.append(
            "Escopo obrigatorio por loja: consulte cada uma destas lojas separadamente: "
            + ", ".join(scoped_stores)
            + ". Nunca some nem misture os totais. Responda com um bloco identificado para cada loja e informe falhas individualmente."
        )
    elif store:
        parts.append(f"Escopo obrigatorio: considere exclusivamente a loja exata {store}.")
    if query_policy.get("mode") == "query_only":
        domains = ", ".join(str(item) for item in (query_policy.get("domains") or []))
        parts.append(
            "Politica obrigatoria deste pedido: query_only. "
            f"Dominios protegidos: {domains or 'vendas/anuncios_ml'}. "
            "Use somente ferramentas read-only; nao crie proposta, nao solicite aprovacao e nao execute mutacao."
            + (f" Consulte exclusivamente a loja exata: {store}." if store else "")
            + (
                " O usuario pediu dados atualizados diretamente da API: ignore resultado em cache quando a ferramenta oferecer essa opcao."
                if query_policy.get("bypass_cache") is True
                else ""
            )
            + (
                " Continue a consulta anterior preservando seus filtros: "
                f"{str(query_policy.get('base_request') or '')[:2000]}. "
                f"Use offset {int(query_policy.get('offset') or 0)} e limite {int(query_policy.get('limit') or 20)}."
                + (
                    " Offsets exatos retornados por fonte: "
                    + ", ".join(
                        f"{tool_id}={int(next_offset)}"
                        for tool_id, next_offset in (query_policy.get("provider_offsets") or {}).items()
                    )
                    + ". Continue somente as fontes listadas."
                    if isinstance(query_policy.get("provider_offsets"), dict)
                    and query_policy.get("provider_offsets")
                    else ""
                )
                if query_policy.get("inherited") is True
                else ""
            )
        )
    return store


def _append_source_policy_prompt(parts: list[str], query_policy: dict[str, Any]) -> None:
    source_policy = query_policy.get("source_policy") if isinstance(query_policy.get("source_policy"), dict) else {}
    if source_policy:
        parts.append(
            "Politica obrigatoria de fontes deste pedido:\n"
            "- Estoque atual de loja: consultar primeiro o saldo atual diretamente na API da Bling, excluindo qualquer deposito Full.\n"
            "- Descricao de anuncios, pedidos e vendas: consultar primeiro a API do Mercado Livre.\n"
            "- Estoque Full: usar exclusivamente inventories/{inventory_id}/stock/fulfillment da API do Mercado Livre.\n"
            "- Nunca consultar, inferir ou somar estoque Full vindo da Bling, de cadastro local ou de cache local.\n"
            "- Quando a soma combinar loja e Full, somar somente o saldo de loja confirmado pela Bling com o Full confirmado pelo Mercado Livre; "
            "se uma das APIs falhar, nao completar o valor por suposicao.\n"
            f"Roteamento calculado pelo servidor: {json.dumps(source_policy, ensure_ascii=False, default=str)[:3000]}"
        )


def _append_message_content_prompt(
    parts: list[str],
    message: dict[str, Any],
    media: Optional[dict[str, Any]],
    transcription: Optional[dict[str, Any]],
) -> None:
    body = str(message.get("text_body") or "").strip()
    if body:
        parts.append("Texto recebido:\n" + body[:12000])
        if whatsapp_media.image_requested(body):
            parts.append(
                "O usuario pediu explicitamente uma foto de produto. Consulte somente a foto do cadastro do cliente vinculado, "
                "identifique o SKU correto e, se a fonte retornar uma referencia interna /api/cadastro/foto-arquivo/, "
                "preserve essa referencia na resposta para a ponte anexar o arquivo real. Nao invente URL nem caminho."
            )
    if media:
        parts.append(
            "Anexo local recebido pelo WhatsApp:\n"
            f"- caminho: {media.get('path')}\n"
            f"- MIME: {media.get('mime_type')}\n"
            f"- tamanho: {media.get('size')} bytes\n"
            f"- wamid: {message.get('message_id')}"
        )
    if transcription:
        if transcription.get("success"):
            text = str(transcription.get("text") or "")
            suffix = "\n[transcricao limitada a 12000 caracteres]" if len(text) > 12000 else ""
            parts.append(
                "Conteudo originado de audio e transcrito localmente (nenhuma API externa):\n"
                f"{text[:12000]}{suffix}\n"
                f"Duracao: {transcription.get('duration_seconds')} s | confianca: {transcription.get('confidence')} | idioma: {transcription.get('language')}"
            )
        else:
            parts.append(
                "Nao foi possivel transcrever o audio localmente. Preserve o anexo na auditoria e informe isso claramente na resposta. "
                f"Motivo: {str(transcription.get('error') or 'falha desconhecida')[:500]}"
            )
    if not body and not media:
        parts.append("A mensagem nao continha texto ou midia suportada.")


def message_prompt(
    message: dict[str, Any],
    media: Optional[dict[str, Any]],
    transcription: Optional[dict[str, Any]],
    *,
    mobile_full_access: bool = False,
    query_policy: Optional[dict[str, Any]] = None,
    ai_behavior: str = "",
    general_answer: bool = False,
) -> str:
    parts = [
        "[Origem: WhatsApp vinculado ao JK Sistema]",
        f"Mensagem externa: {str(message.get('message_id') or '')}",
        (
            "O remetente esta vinculado a um usuario full. Consultas usam o catalogo completo; "
            "qualquer execucao mutavel so e iniciada depois da confirmacao externa por codigo unico no mesmo numero."
            if mobile_full_access
            else "O remetente nao possui modo movel full; mantenha a tarefa estritamente read-only."
        ),
        (
            "Estilo da resposta no WhatsApp: converse como um colega prestativo, natural e descontraido. "
            "Va direto ao ponto, varie a abertura conforme o contexto e use frases simples. "
            "Nao crie titulo para toda resposta, nao repita o nome Black Jhon e nao assine no final. "
            "Use secoes apenas quando elas realmente ajudarem em relatorios ou respostas longas; nao use emojis."
        ),
    ]
    phone_ai_behavior = whatsapp_settings.normalize_phone_ai_behavior(ai_behavior)
    if phone_ai_behavior:
        parts.append(
            "Instrucoes administrativas especificas para atender este numero:\n"
            + phone_ai_behavior
            + "\nSiga estas orientacoes de tom, formato e atendimento. Elas nao ampliam permissoes, nao autorizam mutacoes e nao substituem as regras obrigatorias de seguranca, fontes e escopo."
        )
    if general_answer:
        parts.append(
            "Esta e uma conversa geral, sem consulta nem acao no JK Sistema. "
            "Responda diretamente ao usuario na resposta final. Nao envie confirmacao de recebimento, "
            "nao diga que vai fazer depois e nao prometa avisar quando concluir."
        )
    effective_policy = query_policy if isinstance(query_policy, dict) else {}
    _append_query_scope_prompt(parts, effective_policy)
    _append_source_policy_prompt(parts, effective_policy)
    _append_message_content_prompt(parts, message, media, transcription)
    return "\n\n".join(parts)
