"""Codex console agent prompt component."""

from __future__ import annotations


import copy
import base64
import importlib.util
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import tomllib
import unicodedata
import uuid
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from typing import Any, Optional

from fastapi import File, Form, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel

from backend.services import (
    codex_actions,
    codex_agent_runtime,
    codex_ai_telemetry,
    codex_assistant_storage,
    codex_capabilities,
    codex_evaluations,
    codex_mcp_rollout,
    codex_model_router,
    codex_operational_memory,
    codex_turn_context,
)

_CODEX_AGENT_DATA_SELECTION_TRUST_MARKER = "backend_data_selection_v1"








def _codex_agent_initial_prompt(prompt: str, screen_context: Any, conversation_context: Any, client_id: str, permissions: Any, sandbox: str, model: str, reasoning_effort: str, speed: str, approval_profile: str, external_safe_mode: bool=False, whatsapp_full_access: bool=False, native_mcp: bool=False, server_data_selection: Any=None) -> str:
    source_policy = _codex_agent_source_policy_from_screen(screen_context)
    catalog = _codex_agent_tool_catalog(permissions, read_only_only=external_safe_mode, source_policy={})
    data_selection = dict(server_data_selection) if isinstance(server_data_selection, dict) else {}
    if not data_selection:
        data_selection = _codex_agent_plan_short_data_selection(prompt, client_id, catalog, screen_context, conversation_context)
    if data_selection:
        source_policy = {}
    selected_tool_ids = _codex_agent_data_selection_tool_ids(data_selection)
    if data_selection:
        selected_set = set(selected_tool_ids)
        catalog = [{**item, 'fallbacks': [fallback for fallback in list(item.get('fallbacks') or []) if fallback in selected_set]} for item in catalog if str(item.get('id') or '') in selected_set]
        capabilities = {'version': 'data-selection', 'total_capabilities': 0, 'modules': [], 'capabilities': []}
    else:
        capabilities = _codex_agent_capability_catalog(client_id, permissions, read_only_only=external_safe_mode)
    screen_summary = _codex_agent_screen_summary(screen_context)
    if data_selection:
        screen_summary = {key: screen_summary.get(key) for key in ('title', 'pathname', 'modulo_atual') if screen_summary.get(key) not in (None, '', [], {})}
    tenant = str(client_id or '').strip()
    guidance_items = [] if data_selection else codex_agent_runtime.resolve_guidance(_codex_base_info_dir(), tenant, context=_codex_agent_guidance_context(prompt, screen_context)) if tenant else []
    guidance_text = codex_agent_runtime.guidance_prompt(guidance_items)
    conversation_context = conversation_context if isinstance(conversation_context, dict) else {}
    conversation_summary = str(conversation_context.get('summary') or '').strip()
    recent_messages = conversation_context.get('recent_messages') if isinstance(conversation_context.get('recent_messages'), list) else []
    report_mode = _codex_agent_is_report_request(prompt)
    max_cycles = _codex_int_env('JK_CODEX_AGENT_REPORT_MAX_CYCLES' if report_mode else 'JK_CODEX_AGENT_MAX_CYCLES', CODEX_AGENT_REPORT_MAX_CYCLES if report_mode else CODEX_AGENT_MAX_CYCLES, 1, 20)
    max_calls = _codex_int_env('JK_CODEX_AGENT_MAX_TOOL_CALLS_PER_CYCLE', CODEX_AGENT_MAX_TOOL_CALLS_PER_CYCLE, 1, 10)
    memory_parts: list[str] = []
    operational_memory: dict[str, Any] = {}
    if not data_selection and isinstance(permissions, dict) and (permissions.get('full') is True):
        try:
            operational_memory = codex_operational_memory.compact_context(client_id=tenant, message=str(prompt or ''), limit_chars=6000)
        except Exception:
            operational_memory = {}
    operational_summary = str((operational_memory or {}).get('summary') or '').strip()
    if operational_summary:
        memory_parts.append(f'Memoria operacional persistida do {BLACK_JHON_DISPLAY_NAME}:\n{operational_summary[:6000]}')
    if conversation_summary:
        memory_parts.append(f'Memoria compactada da conversa atual:\n{conversation_summary[:CODEX_CONVERSATION_SUMMARY_CHAR_LIMIT]}')
    if recent_messages:
        memory_parts.append(f'Ultimas mensagens relevantes da conversa atual:\n{_codex_agent_json(recent_messages, CODEX_CONVERSATION_RECENT_CHAR_LIMIT)}')
    memory_text = '\n\n'.join(memory_parts) if memory_parts else 'Sem historico persistido relevante para esta conversa.'
    approved_mobile_execution = bool(whatsapp_full_access and sandbox == 'full_access')
    mutable_rule = '- Esta tarefa mutavel ja foi confirmada fora do modelo por codigo unico no mesmo numero de WhatsApp. Pode executar o pedido com as ferramentas nativas do Codex, sem solicitar uma segunda confirmacao. O protocolo jk_tool_calls continua reservado a consultas e preparacao segura de dados.\n' if approved_mobile_execution else '- Acao mutavel nao pode ser executada por ferramenta: editar arquivo, comando, sincronizar, publicar, responder pergunta, alterar banco, Bling ou Mercado Livre exige aprovacao explicita.\n'
    mobile_report_rule = 'Estilo de conversa no WhatsApp:\n- Fale de forma natural, cordial e descontraida, como um colega prestativo.\n- Va direto ao ponto e varie a abertura; nao transforme toda resposta em comunicado formal.\n- Nao coloque titulo em respostas simples, nao repita o nome Black Jhon e nao assine ao final.\n- Use titulos e secoes somente quando ajudarem a organizar relatorios ou respostas realmente longas.\n- Nao use emojis nas respostas do WhatsApp.\n\nFormato de relatorio para WhatsApp:\n- Escreva para uma tela pequena, com frases curtas, espaco entre blocos e sem tabelas Markdown.\n- Use nesta ordem: Relatorio, Dados principais, Mais vendidos quando houver ranking, Analise e Fontes e cobertura.\n- Em Dados principais, mostre no maximo 8 indicadores objetivos. Em consultas comuns, use no maximo 5 itens; em relatorios completos, liste todos os SKUs e deixe o formatador dividir ate 8 por card.\n- Para cada item do ranking, informe SKU, nome completo do produto, quantidade e valor; nao misture dois produtos no mesmo paragrafo e nao corte texto com reticencias.\n- No relatorio do dia, use obrigatoriamente os pedidos da API do Mercado Livre e liste todos os SKUs vendidos, cada um com quantidade, valor unitario medio e total vendido.\n- Deixe as fontes por ultimo, em linguagem simples, incluindo periodo, conta ou loja, quantidade de registros e eventual lacuna.\n\n' if whatsapp_full_access else ''
    source_routing_rule = ''
    if source_policy:
        source_routing_rule = f'Politica obrigatoria de origem dos dados para esta pergunta do WhatsApp:\n- Execute primeiro todas as ferramentas de required_tools antes de qualquer fallback.\n- Estoque comum vem da API atual da Bling, sempre sem depositos Full.\n- Descricao de anuncios, pedidos e vendas vem primeiro da API do Mercado Livre.\n- Estoque Full vem exclusivamente da API de inventario fulfillment do Mercado Livre.\n- Nunca consulte nem use saldo Full da Bling ou saldo Full de cadastro/cache local.\n- Se a soma combinar loja e Full, use loja=Bling sem Full e Full=Mercado Livre; se faltar uma fonte, nao estime.\nPolitica calculada pelo servidor: {_codex_agent_json(source_policy, 4000)}\n\n'
    guidance_section = guidance_text + '\n\n' if guidance_text else ''
    data_selection_section = ''
    if data_selection:
        selection_status = str(data_selection.get('status') or '')
        selection_action = str(data_selection.get('action') or '')
        if selection_status == 'selection_unavailable' or selection_action == 'unavailable':
            selection_instruction = 'A selecao de dados esta indisponivel. Nao responda com numeros ou fatos operacionais e nao tente outras fontes; informe de forma curta que a consulta nao pode ser validada agora.'
        elif selection_action == 'clarify':
            selection_instruction = 'Nao solicite ferramentas. Peca somente os campos listados em missing_user_fields.'
        elif selection_action == 'mutation_candidate':
            selection_instruction = 'Nao solicite nem execute ferramentas. Para desenvolvimento, oriente o usuario a abrir o Codex Desktop em um Worktree; para operacoes comerciais, limite-se a explicar que a proposta tipada deve ser criada e aprovada no aplicativo.'
        else:
            selection_instruction = 'Use apenas as evidencias desse plano. Ao solicitar jk_tool_calls, copie exatamente o tool_id e o objeto arguments da chamada planejada, preserve a ordem e as dependencias e solicite cada chamada no maximo uma vez. Se o catalogo curto estiver vazio, nao solicite ferramenta comercial adicional.'
        data_selection_section = f'Selecao de dados ja validada pelo backend:\n{_codex_agent_json(data_selection, 12000)}\n{selection_instruction}\n\n'
    if native_mcp:
        tool_protocol = f'Protocolo de ferramenta:\nUse diretamente as ferramentas tipadas do servidor MCP jk_system. Nao escreva blocos jk_tool_calls quando o MCP estiver disponivel. Se o protocolo MCP falhar depois que o turno comecar, encerre com falha parcial; nunca troque para o parser legado no mesmo turno.\nUse no maximo {max_calls} ferramentas por etapa e no maximo {max_cycles} etapas. Depois de cada retorno, leia evidence.status. So complete ou confirmed_zero permitem conclusao geral; partial permite apenas fatos observados. Use evidence.next_sources antes de concluir quando houver cobertura faltante.\n\n'
    else:
        tool_protocol = f'Protocolo de ferramenta de compatibilidade:\nQuando precisar consultar dados, responda somente com um bloco JSON valido neste formato:\n<jk_tool_calls>\n[{{"tool_id":"sales_ranking","args":{{"message":"pedido original","data_inicio":"YYYY-MM-DD","data_fim":"YYYY-MM-DD","loja":"JK Pecas","limite":50}},"reason":"por que precisa"}}]\n</jk_tool_calls>\nUse no maximo {max_calls} ferramentas por ciclo e no maximo {max_cycles} ciclos. Depois que receber resultados suficientes, responda normalmente sem o bloco jk_tool_calls.\n\n'
    return f'Voce e o {BLACK_JHON_DISPLAY_NAME}, assistente interno unificado do JK Sistema. O Codex e sua IA principal de raciocinio e execucao.\nTrabalhe em modo agente: primeiro entenda a pergunta, depois solicite somente as ferramentas read-only necessarias. Nao invente dados e nao dependa da tela atual quando houver ferramenta de dados mais apropriada.\n\nRegras de seguranca:\n- Consultas internas e externas read-only podem ser solicitadas pelo protocolo abaixo.\n{mutable_rule}- Se os dados vierem vazios, peca fallbacks do catalogo antes de responder, respeitando os limites.\n- Depois de cada resultado, leia evidence.status. So complete ou confirmed_zero permitem conclusao geral; partial limita a resposta aos fatos observados. Use evidence.next_sources quando houver.\n- Para dados que nao estejam na tela, use fontes read-only: banco local, CSV, cache, logs de sync, Bling, Mercado Livre, perguntas, anuncios e fiscal.\n\nUse somente as ferramentas presentes no catalogo deste turno; ferramentas omitidas nao estao autorizadas para este usuario. Depois tente uma ferramenta especializada permitida. Se vier vazio, use apenas os fallbacks que tambem aparecem no catalogo. Nunca tente descobrir, enumerar ou consultar fontes, capacidades, acoes ou modulos ausentes do catalogo.\n\nRegra de comunicacao com o usuario:\n- Use os IDs de ferramentas apenas dentro do bloco jk_tool_calls.\n- Na resposta final, status e relatorios, nunca mostre nomes internos como local_database_query, source_discovery, stock_data, function, executor ou nomes de arquivos tecnicos.\n- Explique as fontes em linguagem simples: historico de vendas, historico de estoque, cadastro de produtos, status das integracoes, Bling ou Mercado Livre.\n\n{mobile_report_rule}{source_routing_rule}{guidance_section}{data_selection_section}{tool_protocol}No final de respostas com dados, inclua onde consultou em linguagem simples, periodo, loja/conta, quantidade de registros e avisos de dados incompletos.\n\nConfiguracao: modelo={model}, raciocinio={reasoning_effort}, velocidade={speed}, aprovacao={approval_profile}, sandbox={sandbox}.\n\nContexto de continuidade da conversa:\n{memory_text}\n\nResumo curto da tela atual:\n{_codex_agent_json(screen_summary, CODEX_AGENT_SCREEN_CONTEXT_LIMIT)}\n\nCatalogo compacto de ferramentas read-only disponiveis:\n{_codex_agent_json(catalog, CODEX_AGENT_CATALOG_LIMIT)}\n\nCatalogo compacto de capacidades do JK Sistema:\n{_codex_agent_json(capabilities, CODEX_AGENT_CATALOG_LIMIT)}\n\nPergunta do usuario:\n{prompt}'




def _codex_agent_extract_tool_calls(text: str) -> tuple[list[dict[str, Any]], str]:
    content = str(text or "")
    blocks = re.findall(r"<jk_tool_calls>\s*(.*?)\s*</jk_tool_calls>", content, flags=re.I | re.S)
    if not blocks:
        blocks = re.findall(r"```(?:jk_tool_calls|json)\s*(\[[\s\S]*?\]|\{[\s\S]*?\})\s*```", content, flags=re.I)
    if not blocks:
        return [], ""
    raw = blocks[-1].strip()
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        return [], f"Bloco jk_tool_calls invalido: {exc}"
    if isinstance(parsed, dict):
        parsed = parsed.get("calls") or parsed.get("tool_calls") or [parsed]
    if not isinstance(parsed, list):
        return [], "Bloco jk_tool_calls precisa ser uma lista JSON."
    calls: list[dict[str, Any]] = []
    for item in parsed[:CODEX_AGENT_MAX_TOOL_CALLS_PER_CYCLE]:
        if not isinstance(item, dict):
            continue
        tool_id = str(item.get("tool_id") or item.get("id") or "").strip()
        args = item.get("args") if isinstance(item.get("args"), dict) else {}
        reason = str(item.get("reason") or item.get("motivo") or "").strip()
        if tool_id:
            calls.append({"tool_id": tool_id, "args": args, "reason": reason[:600]})
    return calls, ""


def _codex_agent_tool_status(tool_id: str) -> str:
    try:
        from backend.services.codex.assistant import catalog as assistant_catalog

        meta = assistant_catalog.tool_meta(tool_id)
        return str(meta.get("status") or meta.get("module") or tool_id).strip()
    except Exception:
        return tool_id





def _codex_agent_result_without_exact_transcripts(value: Any) -> Any:
    """Mantem fatos da venda no prompt, mas reserva falas ao formatador servidor."""
    if not isinstance(value, dict):
        return value
    safe = copy.deepcopy(value)
    if not (safe.get("exact_metadata") or {}).get("exact_lookup"):
        return safe
    for rows_key in ("top_rows", "all_rows"):
        rows = safe.get(rows_key)
        if not isinstance(rows, list):
            continue
        compact_rows = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            compact = copy.deepcopy(row)
            conversations = compact.pop("conversations", {})
            compact["conversation_counts"] = {
                "total_messages": int((conversations or {}).get("total_messages") or 0),
                "complete": bool((conversations or {}).get("complete")),
            }
            for claim in compact.get("claims") or []:
                if isinstance(claim, dict):
                    conversation = claim.pop("conversation", {})
                    claim["conversation_count"] = int((conversation or {}).get("total_messages") or 0)
            compact_rows.append(compact)
        safe[rows_key] = compact_rows
    return safe


def _codex_agent_results_prompt(cycle: int, results: list[dict[str, Any]]) -> str:
    prompt_results = [
        _codex_agent_result_without_exact_transcripts(item)
        for item in results
        if isinstance(item, dict)
    ]
    payload = {
        "cycle": cycle,
        "tool_results": prompt_results,
        "next_instruction": (
            "Analise estes resultados e leia evidence de cada ferramenta. So status complete ou confirmed_zero "
            "permite conclusao geral. Com partial, informe apenas fatos observados e nunca conclua total, zero, todas as lojas ou disponibilidade geral. "
            "Se houver evidence.next_sources, solicite novo bloco jk_tool_calls antes de concluir. "
            "Quando a evidencia for conclusiva, responda ao usuario em portugues, "
            "incluindo onde consultou em linguagem simples, periodo, loja/conta, quantidade de registros e avisos. "
            "Use source_label, sources_human, tool_label e os labels de evidence.next_sources para falar com o usuario; "
            "nao exponha tool_id, function, executor ou nomes internos de ferramenta."
        ),
    }
    return "Resultados compactos das ferramentas read-only:\n" + _codex_agent_json(payload, CODEX_AGENT_TOOL_RESULT_LIMIT)





def _codex_whatsapp_number(value: Any) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    if number.is_integer():
        return f"{int(number):,}".replace(",", ".")
    return f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _codex_whatsapp_money(value: Any) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    return "R$ " + f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _codex_whatsapp_report_date(value: Any) -> str:
    raw = str(value or "").strip()
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", raw)
    return f"{match.group(3)}/{match.group(2)}/{match.group(1)}" if match else raw


def _codex_whatsapp_inline(value: Any, fallback: str = "") -> str:
    """Normaliza apenas espacos; nunca corta nomes com reticencias."""
    text = " ".join(str(value or "").split()).strip()
    return text or fallback


def _codex_whatsapp_user_request_text(task: dict[str, Any]) -> str:
    """Extrai somente o pedido do usuario do envelope interno do WhatsApp."""
    prompt = str(task.get("prompt") or "").strip()
    marker = "Texto recebido:"
    if marker not in prompt:
        return prompt
    prompt = prompt.split(marker, 1)[1].strip()
    for suffix in (
        "\nO usuario pediu explicitamente uma foto",
        "\nAnexo local recebido pelo WhatsApp:",
        "\nTranscricao local do audio:",
    ):
        if suffix in prompt:
            prompt = prompt.split(suffix, 1)[0].strip()
    return prompt


def _codex_whatsapp_ml_report_requested(
    task: dict[str, Any],
    *,
    tool_id: str = "",
    args: Optional[dict[str, Any]] = None,
    results: Optional[list[dict[str, Any]]] = None,
) -> bool:
    """Reconhece relatorio ML mesmo quando a continuacao perdeu query_policy."""
    if str(task.get("origin") or "").strip().lower() != "whatsapp":
        return False
    query_policy = task.get("query_policy") if isinstance(task.get("query_policy"), dict) else {}
    source_policy = query_policy.get("source_policy") if isinstance(query_policy.get("source_policy"), dict) else {}
    args = dict(args or {}) if isinstance(args, dict) else {}
    results = list(results or []) if isinstance(results, list) else []
    result_matches = [
        item for item in results
        if isinstance(item, dict) and str(item.get("tool_id") or "") == "mercado_livre_orders"
    ]
    ml_orders_context = bool(
        str(tool_id or "") == "mercado_livre_orders"
        or "mercado_livre_orders" in (source_policy.get("required_tools") or [])
        or result_matches
    )
    if not ml_orders_context:
        return False
    if query_policy.get("report_mode") is True:
        return True
    if str(args.get("mode") or args.get("modo") or "").strip().lower() in {"report", "daily", "relatorio"}:
        return True
    for item in result_matches:
        result_args = item.get("args") if isinstance(item.get("args"), dict) else {}
        if str(result_args.get("mode") or result_args.get("modo") or "").strip().lower() in {"report", "daily", "relatorio"}:
            return True
        for summary_item in item.get("summary") if isinstance(item.get("summary"), list) else []:
            payload = summary_item.get("summary") if isinstance(summary_item, dict) and isinstance(summary_item.get("summary"), dict) else {}
            paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
            if paging.get("report_mode") is True:
                return True
    text = _codex_texto_sem_acentos(" ".join([
        _codex_whatsapp_user_request_text(task),
        str(query_policy.get("base_request") or ""),
        str(args.get("message") or args.get("mensagem") or ""),
    ]))
    if re.search(r"\b(relatorio|analise|resumo|balanco|fechamento|consolidado)\b", text):
        return True
    months = (
        r"janeiro|fevereiro|marco|abril|maio|junho|julho|agosto|"
        r"setembro|outubro|novembro|dezembro"
    )
    return bool(
        re.search(rf"\bvendas?\b[^.]*\b(hoje|ontem|dia|semana|mes|periodo|{months})\b", text)
        or re.search(rf"\b(hoje|ontem|dia|semana|mes|periodo|{months})\b[^.]*\bvendas?\b", text)
    )


def _codex_whatsapp_prepare_agent_tool_call(
    task: dict[str, Any],
    tool_id: str,
    args: Optional[dict[str, Any]],
    previous_results: Optional[list[dict[str, Any]]] = None,
) -> tuple[dict[str, Any], bool]:
    prepared = dict(args or {}) if isinstance(args, dict) else {}
    if str(tool_id or "") in {"mercado_livre_orders", "mercado_livre_returns"}:
        prepared["message"] = str(
            prepared.get("message")
            or prepared.get("mensagem")
            or _codex_whatsapp_user_request_text(task)
            or ""
        ).strip()
    complete_report = _codex_whatsapp_ml_report_requested(
        task,
        tool_id=tool_id,
        args=prepared,
        results=previous_results,
    )
    if complete_report and str(tool_id or "") == "mercado_livre_orders":
        try:
            requested_limit = int(prepared.get("limite") or prepared.get("limit") or 0)
        except (TypeError, ValueError):
            requested_limit = 0
        try:
            requested_pages = int(prepared.get("max_paginas") or prepared.get("max_pages") or 0)
        except (TypeError, ValueError):
            requested_pages = 0
        prepared["mode"] = "report"
        prepared["limite"] = max(20_000, requested_limit)
        prepared["max_paginas"] = max(400, requested_pages)
        prepared["force_refresh"] = True
        prepared["status"] = "paid,partially_refunded"
        prepared["statuses"] = "paid,partially_refunded"
    return prepared, complete_report


def _codex_whatsapp_deposit_reason(value: Any) -> str:
    raw = _codex_whatsapp_inline(value)
    normalized = _codex_texto_sem_acentos(raw)
    if "full" in normalized or "fulfillment" in normalized:
        return "excluído por ser Full/Fulfillment"
    if "desconsiderarsaldo" in normalized or "desconsiderar saldo" in normalized:
        return "desconsiderado pela configuração da Bling"
    if "inativo" in normalized:
        return "depósito inativo na Bling"
    if "id nao encontrado" in normalized:
        return "não classificado; ID não encontrado no catálogo de depósitos"
    if "metadados insuficientes" in normalized:
        return "não classificado; metadados insuficientes para calcular o saldo confiável"
    return raw or "não classificado; não incluído no total confiável"


def _codex_whatsapp_bling_stock_response(task: dict[str, Any], results: list[dict[str, Any]]) -> str:
    """Formata o saldo Bling com cada deposito nomeado e sua classificacao."""
    if str(task.get("origin") or "").strip().lower() != "whatsapp":
        return ""
    result = next(
        (
            item for item in results
            if isinstance(item, dict)
            and item.get("success") is True
            and str(item.get("tool_id") or "") == "bling_stock_balances"
        ),
        None,
    )
    if not isinstance(result, dict):
        return ""
    rows = [row for row in (result.get("top_rows") or result.get("rows") or []) if isinstance(row, dict)]
    if not rows:
        return ""
    blocks: list[str] = []
    seen_blocks: set[str] = set()
    for row in rows:
        store = _codex_whatsapp_inline(row.get("loja") or row.get("store"), "loja selecionada")
        sku = _codex_whatsapp_inline(row.get("sku") or row.get("codigo"), "não informado")
        product = _codex_whatsapp_inline(row.get("produto") or row.get("nome"), "Produto sem nome")
        included = [item for item in (row.get("depositos") or []) if isinstance(item, dict)]
        excluded = [item for item in (row.get("depositos_excluidos") or []) if isinstance(item, dict)]
        gross = row.get("saldo_bruto_retornado")
        if gross is None:
            gross = sum(
                float(item.get("saldo_fisico", item.get("saldoFisico", item.get("saldo", 0))) or 0)
                for item in included + excluded
            )
        store_balance = row.get("saldo_loja_total", row.get("saldo_total"))
        if store_balance is None:
            store_balance_text = "indisponível — a classificação dos depósitos está incompleta"
        else:
            store_balance_text = f"{_codex_whatsapp_number(store_balance)} unidades"
        lines = [
            f"Na **{store}**, o SKU **{sku}** é **{product}**.",
            "",
            f"**Estoque disponível de loja na Bling:** {store_balance_text}",
            f"**Saldo bruto retornado pela Bling:** {_codex_whatsapp_number(gross)} unidades",
            "",
            "**Depósitos incluídos no saldo de loja**",
        ]
        if not included:
            lines.append("- Nenhum depósito pôde ser incluído com segurança.")
        for deposit in included:
            deposit_id = _codex_whatsapp_inline(deposit.get("id"))
            name = _codex_whatsapp_inline(deposit.get("nome") or deposit.get("descricao"))
            if not name or "nao classificado" in _codex_texto_sem_acentos(name):
                name = f"Depósito ID {deposit_id or 'desconhecido'} — não classificado"
            balance = deposit.get("saldo_fisico", deposit.get("saldoFisico", deposit.get("saldo", 0)))
            lines.append(f"- **{name}:** {_codex_whatsapp_number(balance)} unidades — incluído")
        lines.extend(["", "**Depósitos excluídos do saldo de loja**"])
        if not excluded:
            lines.append("- Nenhum depósito excluído.")
        for deposit in excluded:
            deposit_id = _codex_whatsapp_inline(deposit.get("id"))
            name = _codex_whatsapp_inline(deposit.get("nome") or deposit.get("descricao"))
            if not name or "nao classificado" in _codex_texto_sem_acentos(name):
                name = f"Depósito ID {deposit_id or 'desconhecido'} — não classificado"
            balance = deposit.get("saldo_fisico", deposit.get("saldoFisico", deposit.get("saldo", 0)))
            reason = _codex_whatsapp_deposit_reason(
                deposit.get("motivo") or deposit.get("reason") or deposit.get("motivo_exclusao")
            )
            lines.append(f"- **{name}:** {_codex_whatsapp_number(balance)} unidades — {reason}")
        if row.get("cobertura_depositos_completa") is False:
            lines.extend([
                "",
                "⚠️ A classificação dos depósitos está incompleta; depósitos não identificados não entraram no saldo disponível.",
            ])
        elif row.get("full_excluido") is True:
            lines.extend(["", "O estoque Full foi excluído desta consulta."])
        block = "\n".join(lines)
        if block not in seen_blocks:
            seen_blocks.add(block)
            blocks.append(block)
    return "\n\n".join(blocks).strip()

def _codex_agent_authorize_planned_call(
    tool_id: str,
    arguments: dict[str, Any],
    planned_calls: list[dict[str, Any]],
    attempted_indexes: set[int],
    success_by_index: dict[int, bool],
) -> tuple[Optional[dict[str, Any]], str, str]:
    same_tool = [item for item in planned_calls if str(item.get("tool_id") or "") == str(tool_id or "")]
    if not same_tool:
        return None, "data_selection_tool_blocked", "Ferramenta fora do plano de dados validado para este turno."
    exact = [
        item
        for item in same_tool
        if isinstance(item.get("arguments"), dict) and item.get("arguments") == arguments
    ]
    if not exact:
        return None, "data_selection_arguments_mismatch", "Argumentos diferentes do plano server-side."
    pending = [item for item in exact if int(item.get("index") or 0) not in attempted_indexes]
    if not pending:
        return None, "data_selection_call_already_used", "A chamada planejada ja foi usada neste turno."
    planned = min(pending, key=lambda item: int(item.get("index") or 0))
    planned_index = int(planned.get("index") or 0)
    earlier_indexes = {
        int(item.get("index") or 0)
        for item in planned_calls
        if int(item.get("index") or 0) < planned_index
    }
    if not earlier_indexes.issubset(attempted_indexes):
        return None, "data_selection_call_out_of_order", "Chamada fora da ordem definida pelo plano server-side."
    dependencies = [int(item) for item in list(planned.get("depends_on") or []) if isinstance(item, int)]
    if any(dependency not in attempted_indexes for dependency in dependencies):
        return None, "data_selection_dependency_pending", "Dependencia planejada ainda nao foi executada."
    if any(success_by_index.get(dependency) is not True for dependency in dependencies):
        return None, "data_selection_dependency_failed", "Dependencia planejada falhou; chamada nao executada."
    return planned, "", ""



def _codex_agent_data_selection_from_task(task: Any) -> dict[str, Any]:
    """Return only a plan materialized and marked by the backend worker."""

    if not isinstance(task, dict):
        return {}
    if str(task.get("data_selection_trust_marker") or "") != _CODEX_AGENT_DATA_SELECTION_TRUST_MARKER:
        return {}
    candidate = task.get("data_selection")
    if not isinstance(candidate, dict) or not candidate:
        return {}
    try:
        from backend.services.codex_data_selection_agent import compact_evidence

        compact = compact_evidence(candidate, report=False)
        return compact if isinstance(compact, dict) else {}
    except Exception:
        return dict(candidate)



def _codex_agent_data_selection_tool_ids(selection: Any) -> list[str]:
    if not isinstance(selection, dict):
        return []
    ids: list[str] = []

    def add(value: Any) -> None:
        tool_id = str(value or "").strip()[:120]
        if tool_id and tool_id not in ids:
            ids.append(tool_id)

    for call in _codex_agent_planned_calls(selection):
        if isinstance(call.get("arguments"), dict):
            add(call.get("tool_id"))
    return ids[:10]



def _codex_agent_delta_prompt(
    prompt: str,
    screen_context: Any,
    permissions: Any,
    *,
    read_only_only: bool,
    native_mcp: bool,
    server_data_selection: Any = None,
) -> str:
    """Per-turn delta for an already resumed technical Codex thread."""

    data_selection = (
        dict(server_data_selection)
        if isinstance(server_data_selection, dict)
        else {}
    )
    catalog = _codex_agent_tool_catalog(
        permissions,
        read_only_only=read_only_only,
        source_policy={},
    )
    selected = set(_codex_agent_data_selection_tool_ids(data_selection))
    if selected:
        catalog = [item for item in catalog if str(item.get("id") or "") in selected]
    payload = {
        "question": str(prompt or "").strip()[:12000],
        "screen": _codex_agent_screen_summary(screen_context),
        "data_selection": data_selection,
        "authorized_tools": catalog,
        "tool_protocol": "mcp_v2" if native_mcp else "typed_catalog_text_v1",
        "read_only": True,
    }
    return (
        "Turno incremental da thread tecnica ja inicializada. Preserve as instrucoes anteriores; "
        "considere somente o pedido, o escopo e as autorizacoes atuais abaixo. Ferramentas ausentes "
        "na lista atual nao estao autorizadas neste turno.\n\n"
        + _codex_agent_json(payload, CODEX_AGENT_CATALOG_LIMIT + 14000)
    )



def _codex_agent_materialize_task_data_selection(
    task: dict[str, Any],
    *,
    prompt: str,
    screen_context: Any,
    read_only_only: bool,
    conversation_context: Any = None,
) -> dict[str, Any]:
    """Resolve one server-owned selection plan and bind it to this task."""

    trusted = _codex_agent_data_selection_from_task(task)
    if trusted:
        selection = trusted
    else:
        catalog = _codex_agent_tool_catalog(
            task.get("permissions") if isinstance(task.get("permissions"), dict) else {},
            read_only_only=read_only_only,
            source_policy={},
        )
        selection = _codex_agent_plan_short_data_selection(
            prompt,
            str(task.get("client_id") or "").strip(),
            catalog,
            screen_context,
            conversation_context,
            str(task.get("trace_id") or task.get("task_id") or ""),
        )
    if not isinstance(selection, dict) or not selection:
        selection = {
            "schema_version": 1,
            "status": "selection_unavailable",
            "action": "unavailable",
            "selected_tools": [],
            "coverage_complete": False,
            "warnings": ["data_selection_invalid_plan"],
        }
    query_policy = dict(task.get("query_policy") or {}) if isinstance(task.get("query_policy"), dict) else {}
    query_policy["source_policy"] = {}
    task["query_policy"] = query_policy
    task["data_selection"] = selection
    task["data_selection_trust_marker"] = _CODEX_AGENT_DATA_SELECTION_TRUST_MARKER
    task["data_selection_tool_ids"] = _codex_agent_data_selection_tool_ids(selection)
    return selection



def _codex_agent_plan_short_data_selection(
    prompt: str,
    client_id: str,
    catalog: list[dict[str, Any]],
    screen_context: Any,
    conversation_context: Any = None,
    trace_id: str = "",
) -> dict[str, Any]:
    """Plan only the business data tools exposed to the responder."""

    tenant = str(client_id or "").strip()
    if not tenant:
        return {
            "schema_version": 1,
            "status": "selection_unavailable",
            "action": "unavailable",
            "selected_tools": [],
            "coverage_complete": False,
            "warnings": ["data_selection_tenant_required"],
        }

    try:
        from backend.services import codex_data_selection_agent, integracoes

        configured_stores = integracoes.carregar_lojas(tenant)
        authorized_stores = [
            str(item.get("nome") or item.get("name") or "").strip()[:180]
            for item in list(configured_stores or [])[:50]
            if isinstance(item, dict) and str(item.get("nome") or item.get("name") or "").strip()
        ]
        planner_catalog = []
        for raw_item in catalog:
            item = dict(raw_item)
            input_schema = dict(item.get("input_schema") or {}) if isinstance(item.get("input_schema"), dict) else {}
            if "properties" not in input_schema:
                input_schema["properties"] = {str(key): {} for key in list(input_schema)[:40]}
            item["input_schema"] = input_schema
            planner_catalog.append(item)
        screen_summary = _codex_agent_screen_summary(screen_context)
        conversation = conversation_context if isinstance(conversation_context, dict) else {}
        conversation_anchors: dict[str, Any] = {
            key: screen_summary.get(key)
            for key in ("title", "pathname", "modulo_atual", "periodo", "filtros")
            if screen_summary.get(key) not in (None, "", [], {})
        }
        summary = str(conversation.get("summary") or "").strip()
        if summary:
            conversation_anchors["conversation_summary"] = summary[:2000]
        recent_messages: list[dict[str, str]] = []
        for raw_message in list(conversation.get("recent_messages") or [])[-4:]:
            if not isinstance(raw_message, dict):
                continue
            text = str(raw_message.get("text") or raw_message.get("content") or "").strip()[:700]
            if not text:
                continue
            recent_messages.append(
                {
                    "role": "assistant" if str(raw_message.get("role") or "").lower() == "assistant" else "user",
                    "text": text,
                }
            )
        if recent_messages:
            conversation_anchors["recent_messages"] = recent_messages
        with codex_data_selection_agent.DATA_SELECTION_RUNTIME.telemetry_scope(
            client_id=tenant,
            trace_id=trace_id,
            manage_trace=not bool(trace_id),
        ):
            plan = codex_data_selection_agent.DATA_SELECTION_RUNTIME.plan(
                request_text=str(prompt or "")[:12000],
                job_prompt=str(prompt or "")[:12000],
                surface="app",
                allowed_tools=planner_catalog,
                authorized_stores=authorized_stores,
                conversation_anchors=conversation_anchors,
                previous_evidence=None,
                data_gap=None,
                model="gpt-5.6-luna",
                reasoning_effort="low",
                max_calls=6,
            )
        compact = codex_data_selection_agent.compact_evidence(plan, report=False)
        return compact if isinstance(compact, dict) else {}
    except Exception:
        return {
            "schema_version": 1,
            "status": "selection_unavailable",
            "action": "unavailable",
            "selected_tools": [],
            "coverage_complete": False,
        }



def _codex_agent_planned_arguments(value: Any) -> Optional[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return None
    if not isinstance(value, dict):
        return None
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))



def _codex_agent_planned_calls(selection: Any) -> list[dict[str, Any]]:
    """Materialize the exact, ordered commercial calls authorized for one turn."""

    if not isinstance(selection, dict):
        return []
    plan = selection.get("plan") if isinstance(selection.get("plan"), dict) else selection
    if not isinstance(plan, dict):
        return []
    calls: list[dict[str, Any]] = []
    for raw_index, raw_call in enumerate(list(plan.get("tool_calls") or [])[:6]):
        if not isinstance(raw_call, dict):
            continue
        tool_id = str(raw_call.get("tool_id") or raw_call.get("id") or "").strip()[:120]
        if not tool_id:
            continue
        dependencies = [
            item
            for item in list(raw_call.get("depends_on") or [])[:6]
            if isinstance(item, int) and 0 <= item < raw_index
        ]
        calls.append(
            {
                "index": raw_index,
                "tool_id": tool_id,
                "arguments": _codex_agent_planned_arguments(raw_call.get("arguments", {})),
                "required": raw_call.get("required") is True,
                "depends_on": dependencies,
            }
        )
    hub = plan.get("context_hub") if isinstance(plan.get("context_hub"), dict) else {}
    hub_mode = str(hub.get("mode") or "not_applicable").strip().lower()
    if (
        hub_mode in {"optional", "required"}
        and not any(item.get("tool_id") == "context_hub_search" for item in calls)
        and len(calls) < 6
    ):
        hub_filters = hub.get("filters") if isinstance(hub.get("filters"), dict) else {}
        try:
            hub_limit = max(1, min(6, int(hub.get("top_k") or 6)))
        except Exception:
            hub_limit = 6
        hub_arguments: dict[str, Any] = {
            "query": str(hub.get("query") or "")[:1000],
            "limit": hub_limit,
        }
        for filter_key, argument_key in (
            ("sku", "sku"),
            ("mlb", "mlb"),
            ("module", "module"),
            ("source_type", "source_type"),
            ("surface", "surface"),
            ("ids", "ids"),
            ("document_types", "document_types"),
            ("store_ref", "store_ref"),
            ("tags", "tags"),
            ("valid_at", "valid_at"),
            ("truth_class", "truth_class"),
            ("authority", "authority"),
            ("sensitivity", "sensitivity"),
        ):
            filter_value = hub_filters.get(filter_key)
            if filter_value not in (None, "", [], {}):
                hub_arguments[argument_key] = filter_value
        calls.append(
            {
                "index": len(calls),
                "tool_id": "context_hub_search",
                "arguments": hub_arguments,
                "required": hub_mode == "required",
                "depends_on": [],
            }
        )
    return calls
__codex_dependencies__ = ['BLACK_JHON_DISPLAY_NAME', 'CODEX_AGENT_CATALOG_LIMIT', 'CODEX_AGENT_MAX_CYCLES', 'CODEX_AGENT_MAX_TOOL_CALLS_PER_CYCLE', 'CODEX_AGENT_REPORT_MAX_CYCLES', 'CODEX_AGENT_SCREEN_CONTEXT_LIMIT', 'CODEX_AGENT_TOOL_RESULT_LIMIT', 'CODEX_CONVERSATION_RECENT_CHAR_LIMIT', 'CODEX_CONVERSATION_SUMMARY_CHAR_LIMIT', '_codex_agent_capability_catalog', '_codex_agent_guidance_context', '_codex_agent_is_report_request', '_codex_agent_json', '_codex_agent_screen_summary', '_codex_agent_source_policy_from_screen', '_codex_agent_tool_catalog', '_codex_base_info_dir', '_codex_int_env', '_codex_texto_sem_acentos']

__codex_exports__ = ['_codex_agent_initial_prompt', '_codex_agent_extract_tool_calls', '_codex_agent_tool_status', '_codex_agent_result_without_exact_transcripts', '_codex_agent_results_prompt', '_codex_whatsapp_number', '_codex_whatsapp_money', '_codex_whatsapp_report_date', '_codex_whatsapp_inline', '_codex_whatsapp_user_request_text', '_codex_whatsapp_ml_report_requested', '_codex_whatsapp_prepare_agent_tool_call', '_codex_whatsapp_deposit_reason', '_codex_whatsapp_bling_stock_response', '_codex_agent_authorize_planned_call', '_codex_agent_data_selection_from_task', '_codex_agent_data_selection_tool_ids', '_codex_agent_delta_prompt', '_codex_agent_materialize_task_data_selection', '_codex_agent_plan_short_data_selection', '_codex_agent_planned_arguments', '_codex_agent_planned_calls']
