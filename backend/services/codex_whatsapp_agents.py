"""Warm Codex conversation lane for the Black Jhon WhatsApp bridge.

Only Codex produces semantic decisions and user-facing free text here.  The
bridge remains responsible for authentication, persistence, queueing and hard
security rules.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from typing import Any, Optional


CONVERSATION_ACTIONS = (
    "reply",
    "delegate",
    "steer",
    "queue",
    "cancel_job",
    "request_information",
    "wait",
)

DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "action",
        "reply_text",
        "job_title",
        "job_prompt",
        "related_job_id",
        "needs_user_input",
        "requires_web",
        "subtasks",
    ],
    "properties": {
        "action": {"type": "string", "enum": list(CONVERSATION_ACTIONS)},
        "reply_text": {"type": "string", "maxLength": 3500},
        "job_title": {"type": "string", "maxLength": 180},
        "job_prompt": {"type": "string", "maxLength": 12000},
        "related_job_id": {"type": "string", "maxLength": 100},
        "needs_user_input": {"type": "boolean"},
        "requires_web": {"type": "boolean"},
        "subtasks": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "prompt", "requires_web", "reasoning_effort"],
                "properties": {
                    "title": {"type": "string", "maxLength": 180},
                    "prompt": {"type": "string", "maxLength": 12000},
                    "requires_web": {"type": "boolean"},
                    "reasoning_effort": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "xhigh"],
                    },
                },
            },
        },
    },
}

WORKER_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "summary", "verified_facts", "sources", "confidence", "missing", "questions"],
    "properties": {
        "status": {"type": "string", "enum": ["completed", "partial", "blocked", "failed"]},
        "summary": {"type": "string"},
        "verified_facts": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low", "unknown"]},
        "evidence_sufficient": {"type": "boolean"},
        "coverage_complete": {"type": "boolean"},
        "missing": {"type": "array", "items": {"type": "string"}},
        "questions": {"type": "array", "items": {"type": "string"}},
        "data_requests": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["need", "fields", "reason"],
                "properties": {
                    "need": {"type": "string", "maxLength": 500},
                    "fields": {"type": "array", "maxItems": 20, "items": {"type": "string"}},
                    "reason": {"type": "string", "maxLength": 1000},
                },
            },
        },
    },
}


FUNCTION_MANAGER_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "intent",
        "store",
        "store_mode",
        "sku",
        "item_id",
        "requested_fields",
        "tool_calls",
        "requires_sol",
        "requires_web",
        "missing_user_fields",
        "reason",
    ],
    "properties": {
        "intent": {"type": "string", "maxLength": 120},
        "store": {"type": "string", "maxLength": 180},
        "store_mode": {"type": "string", "enum": ["single", "all", "none"]},
        "sku": {"type": "string", "maxLength": 100},
        "item_id": {"type": "string", "maxLength": 60},
        "requested_fields": {"type": "array", "maxItems": 30, "items": {"type": "string"}},
        "tool_calls": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["tool_id", "arguments", "required", "reason"],
                "properties": {
                    "tool_id": {"type": "string", "maxLength": 100},
                    "arguments": {"type": "string", "maxLength": 4000},
                    "required": {"type": "boolean"},
                    "reason": {"type": "string", "maxLength": 500},
                },
            },
        },
        "requires_sol": {"type": "boolean"},
        "requires_web": {"type": "boolean"},
        "missing_user_fields": {"type": "array", "maxItems": 10, "items": {"type": "string"}},
        "reason": {"type": "string", "maxLength": 1200},
    },
}


def _clean_text(value: Any, limit: int) -> str:
    return str(value or "").replace("\x00", "").strip()[:limit]


_RESPONSE_EMOJI_RE = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U00002600-\U000026FF"
    "\U0000FE0F"
    "\U0000200D"
    "]+"
)


def _strip_response_emojis(value: Any, limit: int = 3500) -> str:
    text = _RESPONSE_EMOJI_RE.sub("", str(value or "").replace("\x00", ""))
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+([,.;:!?])", r"\1", text)
    return text.strip()[:limit]


def _compact_whatsapp_reply(value: Any, limit: int) -> str:
    """Limit a reply at a natural boundary instead of leaking a long dump."""

    text = _strip_response_emojis(value, 3500)
    if len(text) <= limit:
        return text
    candidate = text[: max(1, limit)].rstrip()
    boundaries = [candidate.rfind(marker) for marker in ("\n\n", "\n", ". ", "? ", "! ", "; ")]
    boundary = max(boundaries)
    if boundary >= max(80, int(limit * 0.55)):
        candidate = candidate[: boundary + (1 if candidate[boundary:boundary + 1] in ".?!" else 0)]
    else:
        word_boundary = candidate.rfind(" ")
        if word_boundary >= max(40, int(limit * 0.7)):
            candidate = candidate[:word_boundary]
    return candidate.rstrip(" ,;:-") + "..."


def parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    text = _clean_text(value, 30000)
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def normalize_decision(value: Any, *, event_type: str) -> dict[str, Any]:
    parsed = parse_json_object(value)
    action = _clean_text(parsed.get("action"), 40).lower()
    if action not in CONVERSATION_ACTIONS:
        raise RuntimeError("conversation_agent_invalid_action")
    action_limits = {
        "waiting_tick": 320,
        "worker_partial": 900,
        "worker_result": 3500,
    }
    reply_limit = action_limits.get(event_type, 900)
    if action == "request_information":
        reply_limit = min(reply_limit, 420)
    elif action in {"delegate", "queue", "steer", "cancel_job"}:
        reply_limit = min(reply_limit, 600)
    reply = _compact_whatsapp_reply(parsed.get("reply_text"), reply_limit)
    if not reply and not (event_type == "waiting_tick" and action == "wait"):
        raise RuntimeError("conversation_agent_empty_reply")
    if event_type == "waiting_tick" and action not in {"reply", "request_information", "wait"}:
        raise RuntimeError("conversation_agent_invalid_event_action")
    if event_type in {"worker_result", "worker_partial"} and action not in {"reply", "request_information"}:
        raise RuntimeError("conversation_agent_invalid_event_action")
    if event_type == "user_message" and action == "wait":
        raise RuntimeError("conversation_agent_invalid_event_action")
    if action in {"delegate", "queue"} and not _clean_text(parsed.get("job_prompt"), 12000):
        raise RuntimeError("conversation_agent_empty_job_prompt")
    subtasks: list[dict[str, Any]] = []
    for item in list(parsed.get("subtasks") or [])[:6]:
        if not isinstance(item, dict):
            continue
        prompt = _clean_text(item.get("prompt"), 12000)
        if not prompt:
            continue
        subtasks.append(
            {
                "title": _clean_text(item.get("title"), 180) or _clean_text(parsed.get("job_title"), 180),
                "prompt": prompt,
                "requires_web": bool(item.get("requires_web")),
                "reasoning_effort": "low",
            }
        )
    return {
        "action": action,
        "reply_text": reply,
        "job_title": _clean_text(parsed.get("job_title"), 180),
        "job_prompt": _clean_text(parsed.get("job_prompt"), 12000),
        "related_job_id": _clean_text(parsed.get("related_job_id"), 100),
        "needs_user_input": bool(parsed.get("needs_user_input")),
        "requires_web": bool(parsed.get("requires_web")),
        "subtasks": subtasks,
    }


def normalize_worker_result(task: dict[str, Any]) -> dict[str, Any]:
    raw_response = _clean_text(task.get("final_response"), 20000)
    parsed = parse_json_object(raw_response)
    allowed_statuses = {"completed", "partial", "blocked", "failed"}
    if parsed and str(parsed.get("status") or "") in allowed_statuses:
        facts = [_clean_text(item, 2000) for item in list(parsed.get("verified_facts") or [])[:30] if _clean_text(item, 2000)]
        sources = [_clean_text(item, 1000) for item in list(parsed.get("sources") or [])[:30] if _clean_text(item, 1000)]
        confidence = str(parsed.get("confidence") or "unknown") if str(parsed.get("confidence") or "") in {"high", "medium", "low", "unknown"} else "unknown"
        missing = [_clean_text(item, 1000) for item in list(parsed.get("missing") or [])[:20] if _clean_text(item, 1000)]
        inferred_sufficient = bool(
            str(parsed.get("status")) == "completed"
            and facts
            and sources
            and confidence in {"high", "medium"}
            and not missing
        )
        return {
            "status": str(parsed.get("status")),
            "summary": _clean_text(parsed.get("summary"), 12000),
            "verified_facts": facts,
            "sources": sources,
            "confidence": confidence,
            "evidence_sufficient": bool(parsed.get("evidence_sufficient")) if "evidence_sufficient" in parsed else inferred_sufficient,
            "coverage_complete": bool(parsed.get("coverage_complete")) if "coverage_complete" in parsed else False,
            "missing": missing,
            "questions": [_clean_text(item, 1000) for item in list(parsed.get("questions") or [])[:10] if _clean_text(item, 1000)],
            "data_requests": [
                {
                    "need": _clean_text(item.get("need"), 500),
                    "fields": [_clean_text(value, 200) for value in list(item.get("fields") or [])[:20] if _clean_text(value, 200)],
                    "reason": _clean_text(item.get("reason"), 1000),
                }
                for item in list(parsed.get("data_requests") or [])[:6]
                if isinstance(item, dict) and _clean_text(item.get("need"), 500)
            ],
        }
    status = str(task.get("status") or "failed")
    mapped_status = status if status in allowed_statuses else ("partial" if status == "canceled" else "failed")
    verification = task.get("verification") if isinstance(task.get("verification"), dict) else {}
    summaries = [
        item
        for item in list(task.get("tool_results_summary") or [])
        if isinstance(item, dict)
    ]
    validations = [
        item.get("tool_validation")
        for item in summaries
        if isinstance(item.get("tool_validation"), dict)
    ]
    confirmed_by_tools = bool(
        verification.get("confirmed") is True
        or (
            mapped_status == "completed"
            and validations
            and any(item.get("dados_suficientes") is True for item in validations)
        )
    )
    sources = [_clean_text(item, 1000) for item in list(task.get("sources") or [])[:30] if _clean_text(item, 1000)]
    for item in summaries:
        validation = item.get("tool_validation") if isinstance(item.get("tool_validation"), dict) else {}
        if validation.get("dados_suficientes") is not True:
            continue
        source = _clean_text(
            item.get("source_label") or item.get("source") or item.get("tool_label") or item.get("tool_id"),
            1000,
        )
        if source and source not in sources:
            sources.append(source)
    if confirmed_by_tools and not sources:
        sources.append("validacao consolidada das ferramentas autorizadas")
    missing = [] if confirmed_by_tools else ([_clean_text(task.get("error"), 1000)] if task.get("error") else [])
    fallback_facts = [raw_response] if raw_response else []
    if confirmed_by_tools and not fallback_facts:
        for item in summaries:
            validation = item.get("tool_validation") if isinstance(item.get("tool_validation"), dict) else {}
            if validation.get("dados_suficientes") is not True:
                continue
            payload = {
                key: item.get(key)
                for key in ("tool_id", "records", "summary", "data", "result")
                if item.get(key) not in (None, "", [], {})
            }
            if payload:
                fallback_facts.append(_clean_text(json.dumps(payload, ensure_ascii=False, default=str), 2000))
    return {
        "status": mapped_status,
        "summary": raw_response or _clean_text(task.get("error"), 4000),
        "verified_facts": fallback_facts[:30],
        "sources": sources,
        "confidence": "high" if confirmed_by_tools else ("low" if mapped_status != "completed" else "medium"),
        "evidence_sufficient": confirmed_by_tools,
        "coverage_complete": bool(verification.get("coverage_complete") is True or confirmed_by_tools),
        "missing": missing,
        "questions": [],
        "data_requests": [],
    }


def normalize_manager_plan(value: Any, *, max_calls: int = 6) -> dict[str, Any]:
    parsed = parse_json_object(value)
    if not parsed:
        raise RuntimeError("function_manager_invalid_json")
    store_mode = _clean_text(parsed.get("store_mode"), 20).lower()
    if store_mode not in {"single", "all", "none"}:
        store_mode = "single" if _clean_text(parsed.get("store"), 180) else "none"
    calls: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in list(parsed.get("tool_calls") or [])[: max(1, min(6, int(max_calls or 6)))]:
        if not isinstance(item, dict):
            continue
        tool_id = _clean_text(item.get("tool_id"), 100)
        raw_arguments = item.get("arguments")
        if isinstance(raw_arguments, dict):
            arguments = dict(raw_arguments)
        else:
            arguments = parse_json_object(raw_arguments)
        signature = json.dumps({"tool_id": tool_id, "arguments": arguments}, ensure_ascii=False, sort_keys=True, default=str)
        if not tool_id or signature in seen:
            continue
        seen.add(signature)
        calls.append(
            {
                "tool_id": tool_id,
                "arguments": arguments,
                "required": item.get("required") is not False,
                "reason": _clean_text(item.get("reason"), 500),
            }
        )
    return {
        "intent": _clean_text(parsed.get("intent"), 120) or "generic_data_query",
        "store": _clean_text(parsed.get("store"), 180),
        "store_mode": store_mode,
        "sku": _clean_text(parsed.get("sku"), 100),
        "item_id": _clean_text(parsed.get("item_id"), 60),
        "requested_fields": [_clean_text(item, 200) for item in list(parsed.get("requested_fields") or [])[:30] if _clean_text(item, 200)],
        "tool_calls": calls,
        "requires_sol": parsed.get("requires_sol") is True,
        "requires_web": parsed.get("requires_web") is True,
        "missing_user_fields": [_clean_text(item, 300) for item in list(parsed.get("missing_user_fields") or [])[:10] if _clean_text(item, 300)],
        "reason": _clean_text(parsed.get("reason"), 1200),
    }


def worker_output_instruction() -> str:
    return (
        "Voce e o agente Codex de tarefa do Black Jhon. Pesquise profundamente usando apenas ferramentas read-only "
        "autorizadas. Nao converse com o usuario e nao produza texto para envio direto ao WhatsApp. Ao concluir, "
        "retorne somente um objeto JSON valido com: status (completed|partial|blocked|failed), summary, "
        "verified_facts (lista), sources (lista), confidence (high|medium|low|unknown), evidence_sufficient "
        "(booleano), coverage_complete (booleano), missing (lista), questions (lista) e data_requests (lista). "
        "Quando faltar dado interno do JK Sistema, descreva em data_requests o dado e os campos desejados; nao "
        "tente consultar diretamente ferramentas internas que foram reservadas ao Luna Gerenciador. Nao envolva o JSON em "
        "Markdown. Use completed somente com evidencias suficientes. Para confirmar que nao existe registro, use "
        "completed somente se coverage_complete for true; timeout, busca vazia incompleta, HTTP 429/5xx e fonte "
        "indisponivel sao partial ou failed. Diferencie fatos confirmados, lacunas e falhas de fonte.\n\n"
    )


def _manager_prompt(
    *,
    request_text: str,
    job_prompt: str,
    query_policy: Optional[dict[str, Any]],
    tool_catalog: list[dict[str, Any]],
    previous_evidence: Optional[dict[str, Any]],
    data_requests: Optional[list[dict[str, Any]]],
) -> str:
    context = {
        "request_text": _clean_text(request_text, 12000),
        "job_prompt": _clean_text(job_prompt, 12000),
        "query_policy": query_policy if isinstance(query_policy, dict) else {},
        "allowed_tools": list(tool_catalog or [])[:80],
        "previous_evidence": previous_evidence if isinstance(previous_evidence, dict) else {},
        "sol_data_requests": list(data_requests or [])[:6],
    }
    return (
        "Planeje a coleta interna obrigatoria antes do agente Sol. Escolha somente ferramentas do catalogo permitido. "
        "Use o texto original como autoridade para decidir o que foi pedido; o job_prompt pode detalhar, mas nao pode "
        "ampliar vendas, pedidos, devolucoes ou estoque sem pedido explicito do usuario. Para informacoes gerais de um "
        "SKU no Mercado Livre, prefira mercado_livre_listing, product_data e product_image; nunca use pedidos apenas "
        "porque o produto possui vendas. Marque required apenas nas fontes necessarias para responder. Use requires_sol "
        "para analise, compatibilidade ou sintese complexa e requires_web para fatos atuais/externos. Nao execute funcoes, "
        "Em cada tool_call, arguments deve ser uma string contendo um objeto JSON (use '{}' quando nao houver argumentos). "
        "Nao converse com o usuario e retorne apenas o JSON do schema.\n\n"
        + json.dumps(context, ensure_ascii=False, separators=(",", ":"), default=str)
    )


def _decision_prompt(
    *,
    event_type: str,
    user_message: str,
    active_job: Optional[dict[str, Any]],
    worker_result: Optional[dict[str, Any]],
    conversation_context: Optional[list[dict[str, Any]]],
    ai_behavior: str,
    tick_index: int,
) -> str:
    active = active_job if isinstance(active_job, dict) else {}
    result = worker_result if isinstance(worker_result, dict) else {}
    context = {
        "event_type": event_type,
        "user_message": _clean_text(user_message, 12000),
        "active_job": {
            "job_id": _clean_text(active.get("job_id") or active.get("task_id"), 100),
            "title": _clean_text(active.get("job_title") or active.get("request_text"), 500),
            "status": _clean_text(active.get("status"), 60),
            "verified_partial": [_clean_text(item, 1200) for item in list(active.get("verified_partial") or [])[:10]],
            "recent_conversation_messages": [
                _clean_text(item, 500) for item in list(active.get("recent_conversation_messages") or [])[-5:]
            ],
        } if active else {},
        "worker_result": result,
        "conversation_context": [
            {
                "role": _clean_text(item.get("role"), 20),
                "text": _clean_text(item.get("text"), 900),
            }
            for item in list(conversation_context or [])[-10:]
            if isinstance(item, dict) and _clean_text(item.get("text"), 900)
        ],
        "waiting_turn_index": max(0, int(tick_index or 0)),
        "phone_behavior": _clean_text(ai_behavior, 2000),
    }
    return (
        "Decida a proxima acao da conversa usando o contexto JSON abaixo. Toda mensagem livre passa por voce.\n"
        "Atenda tambem perguntas gerais do usuario, mesmo quando nao tiverem relacao com lojas ou com o JK Sistema. "
        "Nunca solicite loja para clima, noticias, conhecimento geral, escrita, calculos ou outros assuntos pessoais. "
        "Quando a resposta depender de informacao atual ou de fonte externa, use delegate e escreva no job_prompt "
        "o pedido completo, incluindo os dados fornecidos pelo usuario nas mensagens anteriores. "
        "Use reply para saudacoes, conversa casual e respostas que nao exigem novas fontes. Use delegate quando for "
        "necessario pesquisar dados, APIs, documentos ou executar analise longa. Use steer quando a mensagem altera "
        "ou complementa a tarefa ativa. Use queue para uma nova tarefa complexa independente enquanto outra estiver "
        "ativa. Use cancel_job quando o usuario pedir para parar a tarefa. Use request_information somente quando um "
        "dado do usuario for indispensavel antes da pesquisa.\n"
        "Use conversation_context como memoria explicita e duravel da conversa, inclusive se a thread do provedor "
        "tiver sido recriada. Resolva pronomes, expressoes como 'esse SKU', 'nessa loja' e continuacoes com base nele; "
        "nao peca novamente um dado que ja esteja ali. Se ainda houver duas interpretacoes materialmente diferentes, "
        "use request_information e faca exatamente uma pergunta curta e objetiva. Nao liste capacidades, fontes, "
        "varias hipoteses ou informacoes laterais. Responda somente ao que foi pedido e nao amplie o escopo.\n"
        "Ao delegar, preencha requires_web e, somente quando o trabalho tiver partes realmente independentes, divida-o "
        "em ate seis subtasks autossuficientes. Nao crie varios agentes para uma contagem simples que uma unica "
        "ferramenta consegue consultar em paralelo. Todos os agentes de tarefa executam em low; mantenha "
        "reasoning_effort como low em cada subtask. Se nao houver divisao util, deixe subtasks vazio.\n"
        "Quando houver tarefa ativa e o usuario enviar apenas ?, e ai, terminou ou uma pergunta de estado equivalente, "
        "responda sobre a mesma tarefa usando reply; nunca use delegate, queue ou steer para uma consulta de estado.\n"
        "No evento waiting_tick, mantenha a conversa naturalmente em ate 320 caracteres. No primeiro aviso, se a tarefa "
        "ainda estiver executando e nao houver resultado, diga uma unica vez que algumas fontes ainda estao sendo "
        "consultadas e peca para aguardar mais um pouco. Nos avisos seguintes, compartilhe apenas fatos parciais novos, "
        "faca uma pergunta util ou use wait com reply_text vazio. Nao repita o pedido nem confirme o escopo novamente. "
        "Nao use titulo, assinatura, nomes internos, contagem de segundos, a palavra Andamento nem invente progresso.\n"
        "No evento worker_partial, apresente somente os novos fatos confirmados e deixe claro, de forma natural, que a "
        "consulta restante continua. No evento worker_result, escreva a resposta final natural com base exclusiva no "
        "resultado fornecido. Declare "
        "lacunas e nunca finja confirmacao. O agente de tarefa nunca fala diretamente com o usuario.\n"
        "Em qualquer evento, reply_text deve ser uma mensagem pronta para WhatsApp, curta quando possivel, sem titulo "
        "em respostas simples, sem assinatura e sem qualquer emoji. A acao wait so pode ser usada em waiting_tick. Para delegate/queue, "
        "job_prompt deve conter o pedido completo e "
        "autossuficiente para o agente de tarefa. Retorne somente o JSON solicitado pelo schema.\n\n"
        + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    )


class WarmConversationRuntime:
    """Owns one warm Codex app-server used only by the Luna conversation lane."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._codex: Any = None
        self._available_models: list[str] = []
        self._last_error = ""
        self._started_at = ""
        self._last_used_at = ""
        self._effective_model = ""

    def _start_locked(self) -> Any:
        if self._codex is not None:
            return self._codex
        from backend.services import codex_console
        from openai_codex import Codex, CodexConfig

        codex_console._codex_apply_sdk_protocol_compat()
        runtime_bin = codex_console._codex_runtime_require_ready()
        client = Codex(
            CodexConfig(
                codex_bin=runtime_bin,
                env=codex_console._codex_sdk_env(),
                cwd=str(codex_console._codex_base_dir()),
                config_overrides=codex_console._codex_nonfull_config_overrides(fast_mode=True),
            )
        )
        client.__enter__()
        self._codex = client
        self._started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._last_error = ""
        try:
            response = client.models(include_hidden=False)
            data = getattr(response, "data", None) or getattr(response, "models", None) or []
            models: list[str] = []
            for item in data:
                value = getattr(item, "id", None) or getattr(item, "model", None) or (item.get("id") if isinstance(item, dict) else "")
                if value:
                    models.append(str(value))
            self._available_models = list(dict.fromkeys(models))
        except Exception:
            self._available_models = []
        return client

    def _close_locked(self) -> None:
        client, self._codex = self._codex, None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def resolve_model(self, requested: str) -> str:
        value = _clean_text(requested, 100).removeprefix("codex:")
        with self._lock:
            self._start_locked()
            if not self._available_models or value in self._available_models:
                return value
            for fallback in ("gpt-5.6-terra", "gpt-5.5", "gpt-5.4"):
                if fallback in self._available_models:
                    return fallback
        raise RuntimeError(f"codex_model_unavailable:{value}")

    def run(
        self,
        *,
        thread_id: str,
        model: str,
        reasoning_effort: str,
        event_type: str,
        user_message: str,
        active_job: Optional[dict[str, Any]] = None,
        worker_result: Optional[dict[str, Any]] = None,
        conversation_context: Optional[list[dict[str, Any]]] = None,
        ai_behavior: str = "",
        tick_index: int = 0,
        speed: str = "fast",
        service_tier: str = "priority",
    ) -> dict[str, Any]:
        from backend.services import codex_console
        from openai_codex.generated.v2_all import ReasoningSummary

        prompt = _decision_prompt(
            event_type=event_type,
            user_message=user_message,
            active_job=active_job,
            worker_result=worker_result,
            conversation_context=conversation_context,
            ai_behavior=ai_behavior,
            tick_index=tick_index,
        )
        effective_speed = codex_console._codex_normalizar_speed(speed)
        effective_service_tier = codex_console._codex_normalizar_service_tier(
            service_tier,
            effective_speed,
        )
        last_error: Optional[Exception] = None
        for attempt in range(2):
            with self._lock:
                try:
                    client = self._start_locked()
                    effective_model = self.resolve_model(model)
                    developer_instructions = (
                        "Voce e o unico agente autorizado a conversar com o usuario do Black Jhon no WhatsApp. "
                        "Voce nao possui ferramentas e nao pode alegar que consultou fontes. Classifique semanticamente "
                        "a mensagem, preserve o contexto da thread e devolva somente o objeto estruturado solicitado."
                    )
                    kwargs = {
                        "cwd": str(codex_console._codex_base_dir()),
                        "model": effective_model,
                        "approval_mode": codex_console._codex_approval_mode_enum("read_only", "read_only"),
                        "developer_instructions": developer_instructions,
                        "service_tier": effective_service_tier,
                    }
                    if thread_id:
                        try:
                            thread = client.thread_resume(thread_id, **kwargs)
                        except Exception:
                            thread = client.thread_start(**kwargs)
                    else:
                        thread = client.thread_start(**kwargs)
                    result = thread.run(
                        prompt,
                        model=effective_model,
                        effort=codex_console._codex_reasoning_effort_enum(reasoning_effort),
                        approval_mode=codex_console._codex_approval_mode_enum("read_only", "read_only"),
                        output_schema=DECISION_SCHEMA,
                        summary=ReasoningSummary.model_validate("none"),
                        service_tier=effective_service_tier,
                    )
                    decision = normalize_decision(getattr(result, "final_response", ""), event_type=event_type)
                    decision.update(
                        {
                            "thread_id": _clean_text(getattr(thread, "id", ""), 200),
                            "requested_model": _clean_text(model, 100).removeprefix("codex:"),
                            "effective_model": effective_model,
                            "reasoning_effort": reasoning_effort,
                            "speed": effective_speed,
                            "service_tier": effective_service_tier or "",
                        }
                    )
                    self._effective_model = effective_model
                    self._last_used_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    self._last_error = ""
                    return decision
                except Exception as exc:
                    last_error = exc
                    self._last_error = str(exc)[:1000]
                    self._close_locked()
            if attempt == 0:
                time.sleep(0.2)
        raise RuntimeError(f"conversation_agent_failed:{last_error}")

    def run_manager(
        self,
        *,
        thread_id: str,
        model: str,
        reasoning_effort: str,
        request_text: str,
        job_prompt: str,
        query_policy: Optional[dict[str, Any]],
        tool_catalog: list[dict[str, Any]],
        previous_evidence: Optional[dict[str, Any]] = None,
        data_requests: Optional[list[dict[str, Any]]] = None,
        max_calls: int = 6,
        speed: str = "fast",
        service_tier: str = "priority",
    ) -> dict[str, Any]:
        from backend.services import codex_console
        from openai_codex.generated.v2_all import ReasoningSummary

        prompt = _manager_prompt(
            request_text=request_text,
            job_prompt=job_prompt,
            query_policy=query_policy,
            tool_catalog=tool_catalog,
            previous_evidence=previous_evidence,
            data_requests=data_requests,
        )
        effective_speed = codex_console._codex_normalizar_speed(speed)
        effective_service_tier = codex_console._codex_normalizar_service_tier(service_tier, effective_speed)
        last_error: Optional[Exception] = None
        for attempt in range(2):
            with self._lock:
                try:
                    client = self._start_locked()
                    effective_model = self.resolve_model(model)
                    kwargs = {
                        "cwd": str(codex_console._codex_base_dir()),
                        "model": effective_model,
                        "approval_mode": codex_console._codex_approval_mode_enum("read_only", "read_only"),
                        "developer_instructions": (
                            "Voce e o Luna Gerenciador de funcoes do Black Jhon. Nao conversa com o usuario, nao "
                            "possui credenciais e nao executa ferramentas. Produza somente um plano estruturado "
                            "read-only usando o catalogo permitido fornecido pelo backend."
                        ),
                        "service_tier": effective_service_tier,
                    }
                    if thread_id:
                        try:
                            thread = client.thread_resume(thread_id, **kwargs)
                        except Exception:
                            thread = client.thread_start(**kwargs)
                    else:
                        thread = client.thread_start(**kwargs)
                    result = thread.run(
                        prompt,
                        model=effective_model,
                        effort=codex_console._codex_reasoning_effort_enum(reasoning_effort),
                        approval_mode=codex_console._codex_approval_mode_enum("read_only", "read_only"),
                        output_schema=FUNCTION_MANAGER_PLAN_SCHEMA,
                        summary=ReasoningSummary.model_validate("none"),
                        service_tier=effective_service_tier,
                    )
                    plan = normalize_manager_plan(getattr(result, "final_response", ""), max_calls=max_calls)
                    plan.update(
                        {
                            "thread_id": _clean_text(getattr(thread, "id", ""), 200),
                            "requested_model": _clean_text(model, 100).removeprefix("codex:"),
                            "effective_model": effective_model,
                            "reasoning_effort": reasoning_effort,
                            "speed": effective_speed,
                            "service_tier": effective_service_tier or "",
                        }
                    )
                    self._effective_model = effective_model
                    self._last_used_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    self._last_error = ""
                    return plan
                except Exception as exc:
                    last_error = exc
                    self._last_error = str(exc)[:1000]
                    self._close_locked()
            if attempt == 0:
                time.sleep(0.2)
        raise RuntimeError(f"function_manager_failed:{last_error}")

    def warm(self, conversation_model: str, task_model: str) -> dict[str, Any]:
        with self._lock:
            conversation_effective = self.resolve_model(conversation_model)
            task_effective = self.resolve_model(task_model)
            self._effective_model = conversation_effective
            return {
                "ready": True,
                "conversation_effective_model": conversation_effective,
                "task_effective_model": task_effective,
                "available_models": list(self._available_models),
            }

    def diagnostics(self) -> dict[str, Any]:
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            return {
                "ready": self._codex is not None and not self._last_error,
                "started_at": self._started_at,
                "last_used_at": self._last_used_at,
                "last_error": self._last_error,
                "effective_model": self._effective_model,
                "available_models": list(self._available_models),
                "busy": True,
            }
        try:
            return {
                "ready": self._codex is not None and not self._last_error,
                "started_at": self._started_at,
                "last_used_at": self._last_used_at,
                "last_error": self._last_error,
                "effective_model": self._effective_model,
                "available_models": list(self._available_models),
                "busy": False,
            }
        finally:
            self._lock.release()

    def close(self) -> None:
        with self._lock:
            self._close_locked()


class WarmConversationRuntimePool:
    """Pool of isolated warm Codex app-servers for the Luna lane.

    A slot is checked out for one model turn and returned afterwards.  The
    caller is responsible for serializing turns that belong to the same phone;
    different phones can therefore use different app-server processes at the
    same time.
    """

    def __init__(self, default_size: int = 4) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._configure_lock = threading.Lock()
        self._slots: list[WarmConversationRuntime] = []
        self._available: deque[WarmConversationRuntime] = deque()
        self._busy: set[int] = set()
        self._target_size = max(1, min(8, int(default_size or 4)))
        self._closing = False
        self._last_error = ""

    def _checkout(self, timeout: float = 60.0) -> WarmConversationRuntime:
        deadline = time.monotonic() + max(0.1, float(timeout or 0.1))
        with self._condition:
            while not self._available and not self._closing:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("conversation_runtime_pool_unavailable")
                self._condition.wait(min(remaining, 1.0))
            if self._closing:
                raise RuntimeError("conversation_runtime_pool_closed")
            slot = self._available.popleft()
            self._busy.add(id(slot))
            return slot

    def _release(self, slot: WarmConversationRuntime) -> None:
        close_slot = False
        with self._condition:
            self._busy.discard(id(slot))
            if self._closing or len(self._slots) > self._target_size:
                try:
                    self._slots.remove(slot)
                except ValueError:
                    pass
                close_slot = True
            elif slot in self._slots:
                self._available.append(slot)
            self._condition.notify_all()
        if close_slot:
            slot.close()

    def configure(self, size: int, conversation_model: str, task_model: str) -> dict[str, Any]:
        with self._configure_lock:
            return self._configure_locked(size, conversation_model, task_model)

    def _configure_locked(self, size: int, conversation_model: str, task_model: str) -> dict[str, Any]:
        target = max(1, min(8, int(size or 1)))
        with self._condition:
            if self._closing and not self._slots:
                self._closing = False
            if self._closing:
                raise RuntimeError("conversation_runtime_pool_closed")
            current = len(self._slots)

        # New slots are fully warmed before they become visible to workers.
        additions: list[WarmConversationRuntime] = []
        try:
            for _ in range(max(0, target - current)):
                slot = WarmConversationRuntime()
                slot.warm(conversation_model, task_model)
                additions.append(slot)
        except Exception as exc:
            for slot in additions:
                slot.close()
            self._last_error = str(exc)[:1000]
            raise

        removable: list[WarmConversationRuntime] = []
        with self._condition:
            self._target_size = target
            for slot in additions:
                self._slots.append(slot)
                self._available.append(slot)
            while len(self._slots) > target and self._available:
                slot = self._available.pop()
                try:
                    self._slots.remove(slot)
                except ValueError:
                    continue
                removable.append(slot)
            self._last_error = ""
            self._condition.notify_all()
        for slot in removable:
            slot.close()
        return self.diagnostics()

    def run(self, **kwargs: Any) -> dict[str, Any]:
        slot = self._checkout()
        try:
            return slot.run(**kwargs)
        finally:
            self._release(slot)

    def run_manager(self, **kwargs: Any) -> dict[str, Any]:
        slot = self._checkout()
        try:
            return slot.run_manager(**kwargs)
        finally:
            self._release(slot)

    def resolve_model(self, requested: str) -> str:
        slot = self._checkout()
        try:
            return slot.resolve_model(requested)
        finally:
            self._release(slot)

    def warm(
        self,
        conversation_model: str,
        task_model: str,
        pool_size: Optional[int] = None,
    ) -> dict[str, Any]:
        with self._condition:
            size = int(pool_size or self._target_size or 4)
        return self.configure(size, conversation_model, task_model)

    def diagnostics(self) -> dict[str, Any]:
        with self._condition:
            slots = list(self._slots)
            busy_ids = set(self._busy)
            target = self._target_size
            last_error = self._last_error
        details: list[dict[str, Any]] = []
        healthy = 0
        for index, slot in enumerate(slots):
            diagnostic = slot.diagnostics()
            diagnostic = {
                **diagnostic,
                "slot": index + 1,
                "busy": id(slot) in busy_ids,
            }
            if diagnostic.get("ready"):
                healthy += 1
            details.append(diagnostic)
        return {
            "ready": bool(slots) and healthy == len(slots) and not last_error,
            "pool_size": len(slots),
            "target_pool_size": target,
            "busy": len(busy_ids),
            "available": max(0, len(slots) - len(busy_ids)),
            "healthy": healthy,
            "last_error": last_error,
            "slots": details,
        }

    def close(self) -> None:
        with self._condition:
            self._closing = True
            slots = list(self._available)
            for slot in slots:
                try:
                    self._slots.remove(slot)
                except ValueError:
                    pass
            self._available.clear()
            self._condition.notify_all()
        for slot in slots:
            slot.close()


CONVERSATION_RUNTIME = WarmConversationRuntimePool(default_size=4)
FUNCTION_MANAGER_RUNTIME = WarmConversationRuntimePool(default_size=4)


__all__ = [
    "CONVERSATION_ACTIONS",
    "DECISION_SCHEMA",
    "WORKER_RESULT_SCHEMA",
    "FUNCTION_MANAGER_PLAN_SCHEMA",
    "CONVERSATION_RUNTIME",
    "FUNCTION_MANAGER_RUNTIME",
    "WarmConversationRuntime",
    "WarmConversationRuntimePool",
    "normalize_decision",
    "normalize_worker_result",
    "normalize_manager_plan",
    "parse_json_object",
    "worker_output_instruction",
]
