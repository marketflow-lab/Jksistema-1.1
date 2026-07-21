"""Persistent orchestration and guidance rules for the Black Jhon agent."""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
import uuid
from typing import Any, Optional

from backend.services import codex_assistant_storage


AGENT_STATES = (
    "entendendo",
    "planejando",
    "consultando",
    "validando",
    "aguardando_dados",
    "aguardando_aprovacao",
    "executando",
    "verificando",
    "concluido",
    "parcial",
    "falhou",
    "cancelado",
)
TERMINAL_STATES = {"concluido", "parcial", "falhou", "cancelado"}
GUIDANCE_ORDER = {"global": 10, "module": 20, "store": 30, "supplier": 40, "sku": 50}
GUIDANCE_SCOPE_TYPES = frozenset(GUIDANCE_ORDER)

WHATSAPP_NON_DESTRUCTIVE_ACTIONS = {
    "vendas.sync_periodo",
    "vendas.cancel_sync",
    "estoque.sync_bling_to_jk",
    "estoque.lancamentos_sku",
    "estoque.lancamentos_periodo",
    "ml.pergunta_responder",
    "ml.aprovacao_aprovar",
    "reports.queue_replenishment",
    "reports.queue_price_review",
    "reports.queue_liquidation",
}

SENSITIVE_ACTION_RE = re.compile(
    r"(?:usuario|permiss|senha|credential|credencial|oauth|token|secret|segredo|"
    r"restaur|restore|backup|delete|delet|exclu|remov|disconnect|desconect|"
    r"publicar.*(?:app|sistema|versao)|atualiza.*(?:app|sistema|versao)|"
    r"preco|saldo|ajuste.*estoque|campanha.*massa|anuncio.*(?:public|paus|alter))",
    re.I,
)

FIXED_POLICY_IDS = (
    "security_permissions_v1",
    "source_routing_v1",
    "data_sufficiency_v1",
    "channel_isolation_v1",
)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _normal(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text)


def _json_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def redact_sensitive(value: Any) -> Any:
    """Remove secrets and direct personal identifiers from persisted audit payloads."""
    secret_keys = {
        "authorization",
        "access_token",
        "refresh_token",
        "token",
        "secret",
        "password",
        "senha",
        "cpf",
        "cnpj",
        "email",
        "phone",
        "phone_number",
        "wa_id",
        "address",
        "endereco",
    }
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = _normal(key).replace(" ", "_")
            if normalized in secret_keys or normalized.endswith("_token"):
                result[str(key)] = "[redigido]"
            else:
                result[str(key)] = redact_sensitive(item)
        return result
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, str):
        text = re.sub(r"(?i)bearer\s+[a-z0-9._~+\-/=]+", "Bearer [redigido]", value)
        text = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[email redigido]", text)
        return text[:12000]
    return value


def make_idempotency_key(
    *,
    client_id: str,
    username: str,
    channel: str,
    conversation_id: str,
    conversation_generation: int,
    request_id: str = "",
    message: str = "",
) -> str:
    stable_request = str(request_id or "").strip()
    if not stable_request:
        stable_request = _json_hash({"message": str(message or "").strip(), "bucket": int(time.time() // 5)})
    material = "|".join(
        (
            str(client_id or "default").strip().lower(),
            str(username or "user").strip().lower(),
            str(channel or "app").strip().lower(),
            str(conversation_id or "").strip(),
            str(max(1, int(conversation_generation or 1))),
            stable_request,
        )
    )
    return hashlib.sha256(material.encode("utf-8", "replace")).hexdigest()


def initial_steps(*, mutable: bool = False) -> list[dict[str, Any]]:
    labels = [
        ("entender", "Entender o pedido e preservar o contexto"),
        ("consultar", "Consultar as fontes autorizadas"),
        ("validar", "Validar suficiência, escopo e consistência"),
    ]
    if mutable:
        labels.extend(
            [
                ("preparar", "Preparar uma ação determinística"),
                ("aprovar", "Aguardar confirmação explícita"),
                ("executar", "Executar pelo adaptador autorizado"),
                ("verificar", "Verificar o resultado na fonte"),
            ]
        )
    labels.append(("responder", "Responder com resultado e evidências"))
    return [
        {
            "step_id": step_id,
            "title": title,
            "status": "in_progress" if index == 0 else "pending",
            "started_at": _now() if index == 0 else "",
            "completed_at": "",
        }
        for index, (step_id, title) in enumerate(labels)
    ]


def create_plan(
    info_base: str,
    client_id: str,
    *,
    task_id: str,
    conversation_id: str,
    conversation_generation: int,
    username: str,
    channel: str,
    message: str,
    mutable: bool,
    idempotency_key: str,
    guidance_applied: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    existing = codex_assistant_storage.codex_assistant_agent_plan_get(
        info_base,
        client_id,
        idempotency_key=idempotency_key,
    )
    if isinstance(existing, dict):
        return existing
    plan = {
        "plan_id": uuid.uuid4().hex,
        "task_id": str(task_id or ""),
        "conversation_id": str(conversation_id or ""),
        "conversation_generation": max(1, int(conversation_generation or 1)),
        "created_by": str(username or ""),
        "channel": str(channel or "app"),
        "agent_state": "entendendo",
        "current_step": "entender",
        "steps": initial_steps(mutable=mutable),
        "required_input": [],
        "proposal": {},
        "verification": {},
        "guidance_applied": list(guidance_applied or []),
        "idempotency_key": str(idempotency_key or ""),
        "request_preview": str(message or "").strip()[:600],
        "state_history": [{"state": "entendendo", "at": _now()}],
        "created_at": _now(),
    }
    saved = codex_assistant_storage.codex_assistant_agent_plan_save(info_base, client_id, plan)
    audit(
        info_base,
        client_id,
        event_type="plan_created",
        entity_type="plan",
        entity_id=saved["plan_id"],
        actor=username,
        channel=channel,
        payload={"task_id": task_id, "mutable": bool(mutable), "idempotency_key": idempotency_key},
    )
    return saved


def transition_plan(
    info_base: str,
    client_id: str,
    plan_id: str,
    state: str,
    *,
    current_step: str = "",
    step_status: str = "",
    required_input: Optional[list[Any]] = None,
    proposal: Optional[dict[str, Any]] = None,
    verification: Optional[dict[str, Any]] = None,
    details: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if state not in AGENT_STATES:
        raise ValueError(f"Estado do agente invalido: {state}")
    plan = codex_assistant_storage.codex_assistant_agent_plan_get(info_base, client_id, plan_id)
    if not isinstance(plan, dict):
        raise KeyError(plan_id)
    plan["agent_state"] = state
    if current_step:
        plan["current_step"] = current_step
    if required_input is not None:
        plan["required_input"] = list(required_input)
    if proposal is not None:
        plan["proposal"] = dict(proposal)
    if verification is not None:
        plan["verification"] = dict(verification)
    history = list(plan.get("state_history") or [])
    history.append({"state": state, "step": current_step or plan.get("current_step") or "", "at": _now()})
    plan["state_history"] = history[-100:]
    steps = [dict(item) for item in (plan.get("steps") or []) if isinstance(item, dict)]
    target_step = current_step or str(plan.get("current_step") or "")
    for item in steps:
        if str(item.get("step_id") or "") != target_step:
            continue
        status = step_status or ("completed" if state in TERMINAL_STATES else "in_progress")
        item["status"] = status
        if status == "in_progress" and not item.get("started_at"):
            item["started_at"] = _now()
        if status in {"completed", "failed", "canceled"}:
            item["completed_at"] = _now()
        if details:
            item["details"] = redact_sensitive(details)
    plan["steps"] = steps
    saved = codex_assistant_storage.codex_assistant_agent_plan_save(info_base, client_id, plan)
    audit(
        info_base,
        client_id,
        event_type="plan_transition",
        entity_type="plan",
        entity_id=plan_id,
        actor=str(plan.get("created_by") or ""),
        channel=str(plan.get("channel") or ""),
        payload={"state": state, "current_step": target_step, "details": details or {}},
    )
    return saved


def _scope_matches(item: dict[str, Any], context: dict[str, Any]) -> bool:
    scope_type = str(item.get("scope_type") or "global").strip().lower()
    if scope_type == "global":
        return True
    expected = _normal(item.get("scope_key"))
    actual_key = {
        "module": "module",
        "store": "store",
        "supplier": "supplier",
        "sku": "sku",
    }.get(scope_type, "")
    return bool(actual_key and expected and expected == _normal(context.get(actual_key)))


def resolve_guidance(
    info_base: str,
    client_id: str,
    *,
    context: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    context = dict(context or {})
    resolved: list[dict[str, Any]] = [
        {
            "guidance_id": item,
            "version": 1,
            "scope_type": "fixed_policy",
            "scope_key": "",
            "source": "system",
        }
        for item in FIXED_POLICY_IDS
    ]
    configured = codex_assistant_storage.codex_assistant_agent_guidance_list(
        info_base,
        client_id,
        active_only=True,
        latest_only=True,
    )
    configured = [item for item in configured if _scope_matches(item, context)]
    configured.sort(key=lambda item: GUIDANCE_ORDER.get(str(item.get("scope_type") or "global"), 999))
    for item in configured:
        resolved.append(
            {
                "guidance_id": str(item.get("guidance_id") or ""),
                "version": max(1, int(item.get("version") or 1)),
                "scope_type": str(item.get("scope_type") or "global"),
                "scope_key": str(item.get("scope_key") or ""),
                "text": str(item.get("text") or item.get("guidance") or "")[:12000],
                "source": "agent_settings",
            }
        )

    module = _normal(context.get("module"))
    if module in {"perguntas_pos_venda", "perguntas", "pos_venda", "mercado_livre"}:
        try:
            from backend.services import ia_treinamento_ppv

            legacy = ia_treinamento_ppv._ia_treinamento_ppv_resolver(client_id, str(context.get("store") or ""))
        except Exception:
            legacy = {}
        if isinstance(legacy, dict):
            kind = "pos_venda" if "pos" in module else "perguntas_anuncio"
            text = str(
                legacy.get("orientacoes_pos_venda")
                if kind == "pos_venda"
                else legacy.get("orientacoes_perguntas") or legacy.get("orientacoes")
                or ""
            ).strip()
            sku = str(context.get("sku") or "").strip()
            notes = legacy.get("notas_sku") if isinstance(legacy.get("notas_sku"), dict) else {}
            note = str(notes.get(sku) or "").strip() if sku else ""
            if text or note:
                updated = str(legacy.get("updated_at") or "legacy")
                resolved.append(
                    {
                        "guidance_id": "legacy_perguntas_pos_venda",
                        "version": int(_json_hash({"updated_at": updated, "text": text, "note": note})[:8], 16),
                        "scope_type": "sku" if note else "store" if context.get("store") else "global",
                        "scope_key": sku or str(context.get("store") or ""),
                        "text": "\n".join(item for item in (text, note) if item)[:12000],
                        "source": "ia_treinamento_perguntas_pos_venda",
                    }
                )
    return resolved


def guidance_prompt(items: list[dict[str, Any]], limit: int = 18000) -> str:
    blocks: list[str] = []
    for item in items:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        blocks.append(
            f"[{item.get('scope_type') or 'global'}:{item.get('scope_key') or 'padrao'} "
            f"v{item.get('version') or 1}]\n{text}"
        )
    if not blocks:
        return ""
    joined = "\n\n".join(blocks)[: max(1000, int(limit or 18000))]
    return (
        "Orientacoes administrativas aplicaveis a este pedido, da mais geral para a mais especifica. "
        "Elas orientam tom e operacao, mas nunca ampliam permissoes nem substituem seguranca, fontes ou escopo:\n"
        + joined
    )


def save_guidance(
    info_base: str,
    client_id: str,
    payload: dict[str, Any],
    *,
    updated_by: str,
) -> dict[str, Any]:
    scope_type = str((payload or {}).get("scope_type") or "global").strip().lower()
    if scope_type not in GUIDANCE_SCOPE_TYPES:
        raise ValueError("Escopo de orientacao invalido.")
    data = dict(payload or {})
    data["scope_type"] = scope_type
    data["text"] = str(data.get("text") or data.get("guidance") or "").strip()[:12000]
    if not data["text"] and data.get("active") is not False:
        raise ValueError("Informe o texto da orientacao.")
    saved = codex_assistant_storage.codex_assistant_agent_guidance_save(
        info_base,
        client_id,
        data,
        updated_by=updated_by,
    )
    audit(
        info_base,
        client_id,
        event_type="guidance_saved",
        entity_type="guidance",
        entity_id=str(saved.get("guidance_id") or ""),
        actor=updated_by,
        channel="app",
        payload={
            "version": saved.get("version"),
            "scope_type": saved.get("scope_type"),
            "scope_key": saved.get("scope_key"),
            "active": saved.get("active"),
        },
    )
    return saved


def proposal_material(proposal: dict[str, Any]) -> dict[str, Any]:
    return {
        "proposal_id": str(proposal.get("proposal_id") or ""),
        "version": max(1, int(proposal.get("version") or 1)),
        "action_id": str(proposal.get("action_id") or ""),
        "params": proposal.get("params") if isinstance(proposal.get("params"), dict) else {},
        "accounts": list(proposal.get("accounts") or []),
        "entities": list(proposal.get("entities") or []),
        "before_snapshot": proposal.get("before_snapshot") if isinstance(proposal.get("before_snapshot"), dict) else {},
        "preconditions": list(proposal.get("preconditions") or []),
        "postconditions": list(proposal.get("postconditions") or []),
        "risk": str(proposal.get("risk") or ""),
        "channels_allowed": list(proposal.get("channels_allowed") or []),
    }


def proposal_hash(proposal: dict[str, Any]) -> str:
    return _json_hash(proposal_material(proposal))


def enrich_action_contract(action: dict[str, Any]) -> dict[str, Any]:
    data = dict(action or {})
    action_id = str(data.get("id") or data.get("action_id") or "")
    module = str(data.get("module") or "sistema")
    risk = str(data.get("risk_level") or data.get("risk") or "local_write")
    executor = str(data.get("executor") or "proposal_only")
    sensitive = bool(SENSITIVE_ACTION_RE.search(" ".join((action_id, module, str(data.get("label") or ""), risk))))
    can_execute = bool(data.get("can_execute")) if "can_execute" in data else executor not in {"", "proposal_only", "generic_route"}
    whatsapp_allowed = action_id in WHATSAPP_NON_DESTRUCTIVE_ACTIONS and not sensitive and risk != "destructive"
    if can_execute:
        operational_class = "execution_with_confirmation"
    elif sensitive or risk == "destructive":
        operational_class = "app_only"
    else:
        operational_class = "preparation_only"
    data.update(
        {
            "operational_class": operational_class,
            "channels_allowed": ["app"] + (["whatsapp"] if whatsapp_allowed else []),
            "requires_confirmation": True,
            "idempotency_required": True,
            "preconditions": ["usuario_e_escopo_autorizados", "parametros_revalidados_antes_da_execucao"],
            "postconditions": ["resultado_confirmado_pelo_executor", "auditoria_persistida"],
            "whatsapp_allowed": whatsapp_allowed,
            "sensitive": sensitive,
        }
    )
    return data


def verification_from_result(result: Any, *, executor: str = "") -> dict[str, Any]:
    if isinstance(result, dict) and result.get("success") is False:
        return {
            "status": "failed",
            "confirmed": False,
            "method": "executor_result",
            "executor": executor,
            "warning": str(result.get("error") or result.get("message") or "A fonte informou falha.")[:1000],
            "verified_at": _now(),
        }
    evidence = isinstance(result, dict) and bool(result)
    if evidence:
        return {
            "status": "confirmed",
            "confirmed": True,
            "method": "executor_result",
            "executor": executor,
            "evidence_keys": [str(key) for key in list(result.keys())[:20]],
            "verified_at": _now(),
        }
    return {
        "status": "partial",
        "confirmed": False,
        "method": "executor_result",
        "executor": executor,
        "warning": "A execucao terminou sem evidencia suficiente para confirmar o efeito.",
        "verified_at": _now(),
    }


def audit(
    info_base: str,
    client_id: str,
    *,
    event_type: str,
    entity_type: str,
    entity_id: str,
    actor: str,
    channel: str,
    payload: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return codex_assistant_storage.codex_assistant_agent_audit_add(
        info_base,
        client_id,
        {
            "event_type": str(event_type or "event"),
            "entity_type": str(entity_type or ""),
            "entity_id": str(entity_id or ""),
            "actor": str(actor or ""),
            "channel": str(channel or ""),
            "payload": redact_sensitive(payload or {}),
            "created_at": _now(),
        },
    )


def capability_coverage(
    *,
    actions: list[dict[str, Any]],
    data_tools: list[dict[str, Any]],
) -> dict[str, Any]:
    contracts: list[dict[str, Any]] = []
    for item in data_tools:
        if not isinstance(item, dict):
            continue
        contracts.append(
            {
                **dict(item),
                "operational_class": "automatic_query",
                "channels_allowed": ["app", "whatsapp"],
                "requires_confirmation": False,
                "idempotency_required": False,
            }
        )
    contracts.extend(enrich_action_contract(item) for item in actions if isinstance(item, dict))
    by_class: dict[str, int] = {}
    modules: dict[str, int] = {}
    for item in contracts:
        category = str(item.get("operational_class") or "unavailable")
        module = str(item.get("module") or "sistema")
        by_class[category] = by_class.get(category, 0) + 1
        modules[module] = modules.get(module, 0) + 1
    return {
        "success": True,
        "total": len(contracts),
        "classified": len(contracts),
        "coverage_percent": 100.0 if contracts else 0.0,
        "by_class": by_class,
        "modules": modules,
        "contracts": contracts,
    }


__all__ = [
    "AGENT_STATES",
    "TERMINAL_STATES",
    "GUIDANCE_SCOPE_TYPES",
    "WHATSAPP_NON_DESTRUCTIVE_ACTIONS",
    "make_idempotency_key",
    "create_plan",
    "transition_plan",
    "resolve_guidance",
    "guidance_prompt",
    "save_guidance",
    "proposal_hash",
    "proposal_material",
    "enrich_action_contract",
    "verification_from_result",
    "capability_coverage",
    "redact_sensitive",
    "audit",
]
