"""Versioned prompt and context contracts for the Black Jhon WhatsApp lane.

This module has no runtime, authentication or permission responsibilities.  It
only owns deterministic schemas, prompt text metadata and bounded JSON context
serialization shared by the warm WhatsApp agents.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping, Sequence


PROMPT_CONTRACT_VERSION = "black-jhon-whatsapp-prompts.v2"
CONVERSATION_DECISION_V2 = "jk.whatsapp.conversation-decision.v2"
EVIDENCE_ENVELOPE_V2 = "jk.whatsapp.evidence-envelope.v2"
RETRIEVAL_RESULT_V2 = "jk.whatsapp.retrieval-result.v2"
PROMPT_CONTEXT_V2 = "jk.whatsapp.prompt-context.v2"
MAX_CONTEXT_CHARS = 24_000
MAX_EVIDENCE_CONTEXT_CHARS = 12_000


CONVERSATION_DEVELOPER_INSTRUCTIONS = (
    "Voce e o agente de decisao de conversa do Black Jhon no WhatsApp. "
    "Voce nao possui ferramentas e nao pode alegar que consultou fontes. Classifique semanticamente "
    "a mensagem, preserve o contexto da thread e devolva somente o objeto estruturado solicitado. "
    "Codex, Luna e Sol sao apenas identificadores de modelos, nunca nomes de papeis ou pessoas."
)

FUNCTION_MANAGER_DEVELOPER_INSTRUCTIONS = (
    "Voce e o agente planejador de funcoes do Black Jhon. Nao conversa com o usuario, nao possui "
    "credenciais e nao executa ferramentas. Produza somente um plano estruturado read-only usando o "
    "catalogo permitido fornecido pelo backend. Codex, Luna e Sol sao apenas identificadores de modelos."
)

WORKER_OUTPUT_INSTRUCTIONS = (
    "Voce e o agente de tarefa do Black Jhon. Pesquise profundamente usando apenas ferramentas read-only "
    "autorizadas. Nao converse com o usuario e nao produza texto para envio direto ao WhatsApp. Ao concluir, "
    "retorne somente um objeto JSON valido no schema EvidenceEnvelopeV2 com: schema_version, status "
    "(completed|partial|blocked|failed), records (registros field/value/store/period/source), facts (lista), "
    "sources (lista), gaps (lista), summary, confidence "
    "(high|medium|low|unknown), evidence_sufficient (booleano), coverage_complete (booleano), questions "
    "(lista) e data_requests (lista). Quando faltar dado interno do JK Sistema, descreva em data_requests o "
    "dado e os campos desejados; nao tente consultar diretamente ferramentas internas reservadas ao agente "
    "planejador. Nao envolva o JSON em Markdown. Use completed somente com evidencias suficientes. Para "
    "confirmar que nao existe registro, use completed somente se coverage_complete for true; timeout, busca "
    "vazia incompleta, HTTP 429/5xx e fonte indisponivel sao partial ou failed. Diferencie fatos confirmados, "
    "fontes e lacunas. Codex, Luna e Sol sao apenas identificadores de modelos.\n\n"
)

MANAGER_PROMPT_INSTRUCTIONS = (
    "Planeje a coleta interna obrigatoria antes do agente de tarefa. Escolha somente ferramentas do catalogo "
    "permitido. Use o texto original como autoridade para decidir o que foi pedido; o job_prompt pode detalhar, "
    "mas nao pode ampliar vendas, pedidos, devolucoes ou estoque sem pedido explicito do usuario. Para "
    "informacoes gerais de um SKU no Mercado Livre, prefira mercado_livre_listing, product_data e product_image; "
    "nunca use pedidos apenas porque o produto possui vendas. Marque required apenas nas fontes necessarias para "
    "responder. Use requires_sol somente como selecao do identificador de modelo Sol para analise, compatibilidade "
    "ou sintese complexa; use requires_web para fatos atuais ou externos. Nao execute funcoes. Em cada tool_call, "
    "arguments deve ser uma string contendo um objeto JSON (use '{}' quando nao houver argumentos). Considere os "
    "records estruturados e depois facts, sources e gaps do EvidenceEnvelopeV2; nunca trate uma lacuna como fato. Nao converse com "
    "o usuario e retorne apenas o JSON do schema. Codex, Luna e Sol sao apenas identificadores de modelos."
)

DECISION_PROMPT_INSTRUCTIONS = (
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
    "Use conversation_state como memoria estruturada atualmente confirmada. Em resolved_context devolva somente "
    "as referencias que voce decidiu aplicar ao pedido atual e liste cada uma em applied_fields. Para pergunta geral "
    "sem relacao comercial, applied_fields deve ficar vazio mesmo que a memoria contenha loja ou SKU. Preserve e "
    "aplique loja, SKU, MLB ou periodo quando o usuario disser 'mesma', 'esse' ou fizer uma continuacao; substitua "
    "pelo valor explicitamente novo; use clear_fields apenas quando o usuario abandonar uma referencia. Um SKU novo "
    "sem MLB novo nao herda o MLB anterior, e o inverso tambem vale. store_mode=all remove a loja unica. Nunca invente "
    "valores. Ao delegar, repita no job_prompt as referencias resolvidas necessarias para que o agente seguinte "
    "receba um pedido autossuficiente.\n"
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
    "consulta restante continua. No evento worker_result, escreva a resposta final natural com base exclusiva em "
    "facts e sources do EvidenceEnvelopeV2. Declare gaps e nunca finja confirmacao. O agente de tarefa nunca fala "
    "diretamente com o usuario.\n"
    "Em qualquer evento, reply_text deve ser uma mensagem pronta para WhatsApp, curta quando possivel, sem titulo "
    "em respostas simples, sem assinatura e sem qualquer emoji. A acao wait so pode ser usada em waiting_tick. Para "
    "delegate/queue, job_prompt deve conter o pedido completo e autossuficiente para o agente de tarefa. Retorne "
    "somente ConversationDecisionV2. Preencha schema_version, intent, response_mode, missing_fields, confidence, "
    "task, subtasks, action e resolved_context. Em task use title, prompt, requires_web e reasoning_effort; mantenha "
    "tambem os aliases V1 job_title, job_prompt, requires_web e needs_user_input com os mesmos valores. Codex, Luna "
    "e Sol sao apenas identificadores de modelos."
)


def _canonical_json(value: Any, *, sort_keys: bool = False) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=sort_keys, default=str)


_HASH_INPUT = {
    "version": PROMPT_CONTRACT_VERSION,
    "schemas": {
        "conversation_decision": CONVERSATION_DECISION_V2,
        "evidence_envelope": EVIDENCE_ENVELOPE_V2,
        "retrieval_result": RETRIEVAL_RESULT_V2,
        "prompt_context": PROMPT_CONTEXT_V2,
    },
    "prompts": {
        "conversation_developer": CONVERSATION_DEVELOPER_INSTRUCTIONS,
        "function_manager_developer": FUNCTION_MANAGER_DEVELOPER_INSTRUCTIONS,
        "worker_output": WORKER_OUTPUT_INSTRUCTIONS,
        "manager": MANAGER_PROMPT_INSTRUCTIONS,
        "decision": DECISION_PROMPT_INSTRUCTIONS,
    },
}
PROMPT_CONTRACT_HASH = hashlib.sha256(_canonical_json(_HASH_INPUT, sort_keys=True).encode("utf-8")).hexdigest()


def prompt_contract_diagnostics() -> dict[str, Any]:
    """Return public, non-sensitive identifiers for the active prompt contract."""

    return {
        "version": PROMPT_CONTRACT_VERSION,
        "hash": PROMPT_CONTRACT_HASH,
        "schemas": {
            "conversation_decision": CONVERSATION_DECISION_V2,
            "evidence_envelope": EVIDENCE_ENVELOPE_V2,
            "retrieval_result": RETRIEVAL_RESULT_V2,
            "prompt_context": PROMPT_CONTEXT_V2,
        },
        "max_context_chars": MAX_CONTEXT_CHARS,
    }


def prompt_contract_header(role: str) -> str:
    safe_role = str(role or "agent").strip().lower()[:80] or "agent"
    return (
        f"Prompt contract: {PROMPT_CONTRACT_VERSION}; role: {safe_role}; "
        f"hash: {PROMPT_CONTRACT_HASH}; context_schema: {PROMPT_CONTEXT_V2}.\n"
    )


def conversation_decision_v2_schema(base_schema: Mapping[str, Any]) -> dict[str, Any]:
    """Upgrade the existing decision schema without changing its V1 fields."""

    schema = copy.deepcopy(dict(base_schema))
    required = [str(item) for item in list(schema.get("required") or [])]
    v2_required = ["schema_version", "intent", "response_mode", "missing_fields", "confidence", "task"]
    required = [*v2_required, *(item for item in required if item not in v2_required)]
    properties = dict(schema.get("properties") or {})
    properties = {
        "schema_version": {"type": "string", "enum": [CONVERSATION_DECISION_V2]},
        "intent": {"type": "string", "maxLength": 120},
        "response_mode": {
            "type": "string",
            "enum": ["direct_reply", "task_delegation", "clarification", "status_update", "control", "silent_wait"],
        },
        "missing_fields": {"type": "array", "maxItems": 10, "items": {"type": "string", "maxLength": 200}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low", "unknown"]},
        "task": {
            "type": "object",
            "additionalProperties": False,
            "required": ["title", "prompt", "requires_web", "reasoning_effort"],
            "properties": {
                "title": {"type": "string", "maxLength": 180},
                "prompt": {"type": "string", "maxLength": 12000},
                "requires_web": {"type": "boolean"},
                "reasoning_effort": {"type": "string", "enum": ["low", "medium", "high", "xhigh"]},
            },
        },
        **properties,
    }
    schema["required"] = required
    schema["properties"] = properties
    return schema


_EVIDENCE_RECORD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["field", "value", "store", "period", "source"],
    "properties": {
        "field": {"type": "string", "maxLength": 200},
        "value": {
            "anyOf": [
                {"type": "string", "maxLength": 4000},
                {"type": "number"},
                {"type": "integer"},
                {"type": "boolean"},
                {"type": "null"},
            ]
        },
        "store": {"type": "string", "maxLength": 200},
        "period": {"type": "string", "maxLength": 160},
        "source": {"type": "string", "maxLength": 1000},
    },
}


EVIDENCE_ENVELOPE_V2_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "status",
        "records",
        "facts",
        "sources",
        "gaps",
        "summary",
        "confidence",
        "evidence_sufficient",
        "coverage_complete",
        "questions",
        "data_requests",
    ],
    "properties": {
        "schema_version": {"type": "string", "enum": [EVIDENCE_ENVELOPE_V2]},
        "source_schema_version": {"type": "string", "maxLength": 120},
        "status": {"type": "string", "enum": ["completed", "partial", "blocked", "failed"]},
        "records": {"type": "array", "maxItems": 40, "items": _EVIDENCE_RECORD_SCHEMA},
        "facts": {"type": "array", "maxItems": 30, "items": {"type": "string", "maxLength": 2000}},
        "verified_facts": {"type": "array", "maxItems": 30, "items": {"type": "string", "maxLength": 2000}},
        "sources": {"type": "array", "maxItems": 30, "items": {"type": "string", "maxLength": 1000}},
        "gaps": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 1000}},
        "missing": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 1000}},
        "summary": {"type": "string", "maxLength": 12000},
        "confidence": {"type": "string", "enum": ["high", "medium", "low", "unknown"]},
        "evidence_sufficient": {"type": "boolean"},
        "coverage_complete": {"type": "boolean"},
        "questions": {"type": "array", "maxItems": 10, "items": {"type": "string", "maxLength": 1000}},
        "data_requests": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["need", "fields", "reason"],
                "properties": {
                    "need": {"type": "string", "maxLength": 500},
                    "fields": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 200}},
                    "reason": {"type": "string", "maxLength": 1000},
                },
            },
        },
    },
}


RETRIEVAL_RESULT_V2_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "query", "records", "sources", "gaps", "coverage_complete", "count"],
    "properties": {
        "schema_version": {"type": "string", "enum": [RETRIEVAL_RESULT_V2]},
        "source_schema_version": {"type": "string", "maxLength": 120},
        "query": {"type": "string", "maxLength": 1000},
        "records": {"type": "array", "maxItems": 40, "items": _EVIDENCE_RECORD_SCHEMA},
        "sources": {"type": "array", "maxItems": 30, "items": {"type": "string", "maxLength": 1000}},
        "gaps": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 1000}},
        "coverage_complete": {"type": "boolean"},
        "count": {"type": "integer", "minimum": 0, "maximum": 40},
    },
}


def _clean_text(value: Any, limit: int) -> str:
    return str(value or "").replace("\x00", "").strip()[: max(0, int(limit))]


def _clean_list(value: Any, *, item_limit: int, max_items: int) -> list[str]:
    values = value if isinstance(value, (list, tuple)) else []
    result: list[str] = []
    seen: set[str] = set()
    for item in list(values)[:max_items]:
        text = _clean_text(item, item_limit)
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


def _clean_data_requests(value: Any) -> list[dict[str, Any]]:
    values = value if isinstance(value, (list, tuple)) else []
    return [
        {
            "need": _clean_text(item.get("need"), 500),
            "fields": _clean_list(item.get("fields"), item_limit=200, max_items=20),
            "reason": _clean_text(item.get("reason"), 1000),
        }
        for item in list(values)[:6]
        if isinstance(item, Mapping) and _clean_text(item.get("need"), 500)
    ]


def _clean_record_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (Mapping, list, tuple)):
        return _clean_text(_canonical_json(_json_safe(value)), 4000)
    return _clean_text(value, 4000)


def _clean_record(value: Any, *, fallback_field: str = "") -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    field = _clean_text(value.get("field") or value.get("name") or value.get("key") or fallback_field, 200)
    if not field:
        return None
    raw_value = value.get("value")
    if raw_value is None:
        for alias in ("fact", "snippet", "content", "summary", "result"):
            if value.get(alias) not in (None, "", [], {}):
                raw_value = value.get(alias)
                break
    return {
        "field": field,
        "value": _clean_record_value(raw_value),
        "store": _clean_text(value.get("store") or value.get("store_ref") or value.get("loja"), 200),
        "period": _clean_text(value.get("period") or value.get("periodo"), 160),
        "source": _clean_text(
            value.get("source") or value.get("reference") or value.get("url") or value.get("tool_id"),
            1000,
        ),
    }


def _clean_records(value: Any, *, fallback_field: str = "") -> list[dict[str, Any]]:
    values = value if isinstance(value, (list, tuple)) else []
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in list(values)[:40]:
        record = _clean_record(item, fallback_field=fallback_field)
        if not record:
            continue
        signature = _canonical_json(record, sort_keys=True)
        if signature in seen:
            continue
        seen.add(signature)
        records.append(record)
    return records


def normalize_evidence_envelope_v2(value: Any) -> dict[str, Any]:
    """Normalize legacy worker evidence and native V2 into one prioritized envelope.

    V1 aliases remain in the returned object because existing orchestration code
    still reads ``verified_facts`` and ``missing``.
    """

    source = dict(value) if isinstance(value, Mapping) else {}
    source_schema = _clean_text(source.get("schema_version"), 120) or "legacy-v1"
    raw_facts = source.get("facts") if isinstance(source.get("facts"), (list, tuple)) else source.get("verified_facts")
    raw_gaps = source.get("gaps") if isinstance(source.get("gaps"), (list, tuple)) else source.get("missing")
    facts = _clean_list(raw_facts, item_limit=2000, max_items=30)
    sources = _clean_list(source.get("sources"), item_limit=1000, max_items=30)
    gaps = _clean_list(raw_gaps, item_limit=1000, max_items=20)
    records = _clean_records(source.get("records"))
    if not records:
        records = [
            {
                "field": "fact",
                "value": fact,
                "store": "",
                "period": "",
                "source": (
                    sources[index]
                    if len(sources) == len(facts)
                    else sources[0] if len(sources) == 1 else ""
                ),
            }
            for index, fact in enumerate(facts[:40])
        ]
    if not facts:
        facts = [_clean_text(record.get("value"), 2000) for record in records if _clean_text(record.get("value"), 2000)][:30]
    for record in records:
        source_name = _clean_text(record.get("source"), 1000)
        if source_name and source_name not in sources and len(sources) < 30:
            sources.append(source_name)
    confidence = _clean_text(source.get("confidence"), 20).lower()
    if confidence not in {"high", "medium", "low", "unknown"}:
        confidence = "unknown"
    status = _clean_text(source.get("status"), 20).lower()
    if status not in {"completed", "partial", "blocked", "failed"}:
        status = "failed"
    envelope = {
        "schema_version": EVIDENCE_ENVELOPE_V2,
        "source_schema_version": source_schema,
        "records": records,
        "facts": facts,
        "sources": sources,
        "gaps": gaps,
        "status": status,
        "confidence": confidence,
        "evidence_sufficient": source.get("evidence_sufficient") is True,
        "coverage_complete": source.get("coverage_complete") is True,
        "summary": _clean_text(source.get("summary"), 12000),
        "questions": _clean_list(source.get("questions"), item_limit=1000, max_items=10),
        "data_requests": _clean_data_requests(source.get("data_requests")),
        # V1 aliases retained for all current downstream consumers.
        "verified_facts": facts,
        "missing": gaps,
    }
    return _bounded_evidence_envelope(envelope)


def normalize_retrieval_result_v2(value: Any) -> dict[str, Any]:
    """Normalize a retrieval payload into source-scoped structured records."""

    source = dict(value) if isinstance(value, Mapping) else {}
    raw_records = source.get("records")
    if not isinstance(raw_records, (list, tuple)):
        raw_records = source.get("rows") if isinstance(source.get("rows"), (list, tuple)) else source.get("results")
    records: list[dict[str, Any]] = []
    for item in list(raw_records or [])[:40]:
        if isinstance(item, Mapping):
            record = _clean_record(item, fallback_field="result")
        else:
            record = {
                "field": "result",
                "value": _clean_record_value(item),
                "store": "",
                "period": "",
                "source": "",
            }
        if record:
            records.append(record)
    sources = _clean_list(source.get("sources"), item_limit=1000, max_items=30)
    for record in records:
        source_name = _clean_text(record.get("source"), 1000)
        if source_name and source_name not in sources and len(sources) < 30:
            sources.append(source_name)
    gaps_value = source.get("gaps") if isinstance(source.get("gaps"), (list, tuple)) else source.get("missing")
    return {
        "schema_version": RETRIEVAL_RESULT_V2,
        "source_schema_version": _clean_text(source.get("schema_version"), 120) or "legacy-v1",
        "query": _clean_text(source.get("query"), 1000),
        "records": records,
        "sources": sources,
        "gaps": _clean_list(gaps_value, item_limit=1000, max_items=20),
        "coverage_complete": source.get("coverage_complete") is True,
        "count": len(records),
    }


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth >= 8:
        return _clean_text(value, 500)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value.replace("\x00", "")[:12000]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:100]:
            result[_clean_text(key, 120)] = _json_safe(item, depth=depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item, depth=depth + 1) for item in list(value)[:100]]
    return _clean_text(value, 2000)


_PROTECTED_CONTEXT_KEYS = frozenset({"records", "facts", "sources", "gaps", "verified_facts", "missing"})
_USER_CONTEXT_KEYS = frozenset({"user_message", "request_text", "job_prompt"})


def _reduction_candidates(value: Any, path: tuple[Any, ...] = ()) -> list[tuple[int, int, tuple[Any, ...], str]]:
    candidates: list[tuple[int, int, tuple[Any, ...], str]] = []
    protected = any(str(part) in _PROTECTED_CONTEXT_KEYS for part in path)
    user_content = any(str(part) in _USER_CONTEXT_KEYS for part in path)
    priority = 2 if protected else (1 if user_content else 0)
    if isinstance(value, str) and len(value) > 80:
        candidates.append((priority, -len(value), path, "string"))
    elif isinstance(value, list):
        if len(value) > 1:
            candidates.append((priority, -len(_canonical_json(value)), path, "list"))
        for index, item in enumerate(value):
            candidates.extend(_reduction_candidates(item, (*path, index)))
    elif isinstance(value, dict):
        for key, item in value.items():
            candidates.extend(_reduction_candidates(item, (*path, key)))
    return candidates


def _path_value(root: Any, path: tuple[Any, ...]) -> Any:
    current = root
    for part in path:
        current = current[part]
    return current


def _replace_path(root: Any, path: tuple[Any, ...], value: Any) -> None:
    if not path:
        raise ValueError("root_replacement_not_supported")
    parent = _path_value(root, path[:-1])
    parent[path[-1]] = value


def _shrink_once(value: dict[str, Any]) -> bool:
    candidates = sorted(_reduction_candidates(value), key=lambda item: (item[0], item[1], len(item[2])))
    if not candidates:
        return False
    _priority, _size, path, kind = candidates[0]
    current = _path_value(value, path)
    if kind == "list":
        keep = max(1, len(current) // 2)
        # Conversation history is most useful from the newest end. Evidence
        # lists preserve their first, highest-priority entries.
        replacement = current[-keep:] if "conversation_context" in path else current[:keep]
    else:
        # Keep making progress even after an earlier reduction added the
        # ellipsis. A minimum of 40 characters is enough for the final compact
        # form and avoids the 83 -> 83 loop.
        keep = max(40, min(len(current) - 4, len(current) // 2))
        replacement = current[:keep].rstrip() + "..."
    _replace_path(value, path, replacement)
    return True


def bounded_context_json(context: Mapping[str, Any], *, max_chars: int = MAX_CONTEXT_CHARS) -> str:
    """Serialize context as valid JSON within the hard character budget.

    Reduction happens on structured values before serialization.  The JSON
    string itself is never sliced, so consumers always receive a complete
    object. Evidence facts, sources and gaps are reduced only after optional
    context and user text.
    """

    safe_limit = max(1000, min(int(max_chars or MAX_CONTEXT_CHARS), MAX_CONTEXT_CHARS))
    source = dict(context) if isinstance(context, Mapping) else {}
    payload = _json_safe({"schema_version": PROMPT_CONTEXT_V2, **source})
    serialized = _canonical_json(payload)
    reductions = 0
    while len(serialized) > safe_limit and reductions < 1000 and _shrink_once(payload):
        reductions += 1
        serialized = _canonical_json(payload)
    if len(serialized) <= safe_limit:
        return serialized
    fallback = {
        "schema_version": PROMPT_CONTEXT_V2,
        "context_omitted": True,
        "reason": "context_budget_exceeded",
    }
    return _canonical_json(fallback)


def _bounded_evidence_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    candidate = copy.deepcopy(envelope)
    serialized = bounded_context_json(candidate, max_chars=MAX_EVIDENCE_CONTEXT_CHARS)
    decoded = json.loads(serialized)
    decoded.pop("schema_version", None)
    # bounded_context_json injects the prompt-context schema. Restore the
    # evidence schema after the structured reduction.
    result = {"schema_version": EVIDENCE_ENVELOPE_V2, **decoded}
    facts = list(result.get("facts") or result.get("verified_facts") or [])
    gaps = list(result.get("gaps") or result.get("missing") or [])
    result["facts"] = facts
    result["sources"] = list(result.get("sources") or [])
    result["gaps"] = gaps
    result["verified_facts"] = facts
    result["missing"] = gaps
    return result


__all__ = [
    "CONVERSATION_DECISION_V2",
    "CONVERSATION_DEVELOPER_INSTRUCTIONS",
    "DECISION_PROMPT_INSTRUCTIONS",
    "EVIDENCE_ENVELOPE_V2",
    "EVIDENCE_ENVELOPE_V2_SCHEMA",
    "FUNCTION_MANAGER_DEVELOPER_INSTRUCTIONS",
    "MANAGER_PROMPT_INSTRUCTIONS",
    "MAX_CONTEXT_CHARS",
    "PROMPT_CONTEXT_V2",
    "PROMPT_CONTRACT_HASH",
    "PROMPT_CONTRACT_VERSION",
    "RETRIEVAL_RESULT_V2",
    "RETRIEVAL_RESULT_V2_SCHEMA",
    "WORKER_OUTPUT_INSTRUCTIONS",
    "bounded_context_json",
    "conversation_decision_v2_schema",
    "normalize_evidence_envelope_v2",
    "normalize_retrieval_result_v2",
    "prompt_contract_diagnostics",
    "prompt_contract_header",
]
