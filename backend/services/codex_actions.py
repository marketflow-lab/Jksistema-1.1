"""Controlled action registry for the internal Codex console."""

from __future__ import annotations

import asyncio
import ast
import hashlib
import json
import os
import re
import secrets
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException

from backend.services import codex_agent_runtime, codex_assistant_storage
from backend.services.runtime_bridge import bind_runtime_globals


MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
READ_ONLY_METHODS = {"GET"}
DESTRUCTIVE_WORDS = {
    "delete",
    "deletar",
    "excluir",
    "limpar",
    "reset",
    "resetar",
    "restore",
    "restaurar",
    "disconnect",
    "desconectar",
    "encerrar",
    "cancelar",
}
EXTERNAL_WORDS = {
    "bling",
    "mercadolivre",
    "mercado",
    "ml",
    "firebase",
    "drive",
    "shared-sync",
    "daily",
    "sala-reuniao",
    "promo",
    "promocao",
    "full",
}
EXCLUDED_PATH_PREFIXES = (
    "/auth/",
    "/webhooks/",
    "/api/login",
    "/api/auth/google",
    "/api/user/machines/heartbeat",
    "/api/user/chat/typing",
    "/api/admin/codex/tasks",
    "/api/admin/codex/assistant",
    "/api/admin/codex/actions",
)
COMMON_LOJA_ALIASES = {
    "jk pecas": "JK Peças",
    "uai mineirinho": "Uai Mineirinho",
    "carlos jose": "Carlos José",
    "deckas": "Deckas",
}

CODEX_ACTIONS: dict[str, dict[str, Any]] = {}
CODEX_ACTIONS_LOCK = threading.RLock()
CODEX_ACTION_PROPOSAL_CLIENTS: dict[str, str] = {}
CODEX_ACTION_RUN_CLIENTS: dict[str, str] = {}
SAFE_EXECUTORS = {
    "vendas_sync",
    "vendas_cancel",
    "estoque_sync",
    "estoque_lancamentos_sku",
    "estoque_lancamentos_lote",
    "ml_pergunta_responder",
    "ml_aprovacao_aprovar",
    "internal_report_queue",
}


@dataclass(frozen=True)
class CodexActionSpec:
    id: str
    module: str
    label: str
    aliases: tuple[str, ...] = ()
    params_schema: dict[str, Any] = field(default_factory=dict)
    risk_level: str = "local_write"
    side_effects: tuple[str, ...] = ()
    requires_confirmation: bool = True
    executor: str = "generic_route"
    route_path: str = ""
    method: str = ""
    status_kind: str = ""
    cancellable: bool = False


def configure_codex_actions_runtime(runtime_module=None):
    return bind_runtime_globals(globals(), runtime_module)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _future(hours: int = 24) -> str:
    try:
        hours_safe = max(1, int(hours))
    except Exception:
        hours_safe = 24
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + hours_safe * 3600))


def _safe_id(raw: Any, limit: int = 96) -> str:
    text = str(raw or "").strip().lower()
    text = re.sub(r"[^a-z0-9_.-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._-")
    return (text or uuid.uuid4().hex)[:limit]


def _base_dir() -> str:
    base = str(globals().get("BASE_DIR") or os.getcwd()).strip()
    return os.path.abspath(base or os.getcwd())


def _info_root() -> Path:
    base_info = str(globals().get("PASTA_INFO") or os.path.join(_base_dir(), "info")).strip()
    root = Path(base_info) / "codex_actions"
    (root / "proposals").mkdir(parents=True, exist_ok=True)
    (root / "runs").mkdir(parents=True, exist_ok=True)
    return root


def _assistant_info_base() -> str:
    return str(globals().get("PASTA_INFO") or os.path.join(_base_dir(), "info")).strip()


def _proposal_path(proposal_id: str) -> Path:
    return _info_root() / "proposals" / f"{_safe_id(proposal_id)}.json"


def _run_path(run_id: str) -> Path:
    return _info_root() / "runs" / f"{_safe_id(run_id)}.json"


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, default=str)
    tmp.replace(path)


def _proposal_load(proposal_id: str, client_id: str) -> Optional[dict[str, Any]]:
    proposal = codex_assistant_storage.codex_assistant_action_proposal_get(
        _assistant_info_base(),
        client_id,
        proposal_id,
    )
    if isinstance(proposal, dict):
        CODEX_ACTION_PROPOSAL_CLIENTS[str(proposal_id)] = str(client_id or "default")
        return proposal
    # Compatibilidade somente leitura com propostas criadas por versoes antigas.
    legacy = _read_json(_proposal_path(proposal_id), None)
    return legacy if isinstance(legacy, dict) else None


def _proposal_save(proposal: dict[str, Any]) -> dict[str, Any]:
    client_id = str(proposal.get("client_id") or "default")
    proposal_id = str(proposal.get("proposal_id") or "")
    saved = codex_assistant_storage.codex_assistant_action_proposal_save(
        _assistant_info_base(),
        client_id,
        proposal,
    )
    if proposal_id:
        CODEX_ACTION_PROPOSAL_CLIENTS[proposal_id] = client_id
    return saved


def _run_load(run_id: str, client_id: str = "") -> Optional[dict[str, Any]]:
    tenant = str(client_id or CODEX_ACTION_RUN_CLIENTS.get(str(run_id)) or "").strip()
    if tenant:
        run = codex_assistant_storage.codex_assistant_action_run_get(
            _assistant_info_base(),
            tenant,
            run_id,
        )
        if isinstance(run, dict):
            CODEX_ACTION_RUN_CLIENTS[str(run_id)] = tenant
            return run
    # Compatibilidade somente leitura com execucoes criadas por versoes antigas.
    legacy = _read_json(_run_path(run_id), None)
    return legacy if isinstance(legacy, dict) else None


def _run_save(run: dict[str, Any]) -> dict[str, Any]:
    client_id = str(run.get("client_id") or "default")
    run_id = str(run.get("run_id") or "")
    saved = codex_assistant_storage.codex_assistant_action_run_save(
        _assistant_info_base(),
        client_id,
        run,
    )
    if run_id:
        CODEX_ACTION_RUN_CLIENTS[run_id] = client_id
    return saved


def _update_run(run_id: str, **updates: Any) -> dict[str, Any]:
    with CODEX_ACTIONS_LOCK:
        run = _run_load(run_id, str(updates.pop("_client_id", "") or "")) or {}
        if not run:
            # Executores tambem sao exercitados isoladamente em validacoes e
            # simulacoes. Sem uma execucao persistida, devolva apenas o espelho
            # do progresso e nunca crie um registro operacional fantasma.
            return {"run_id": str(run_id or ""), **updates}
        run.update(updates)
        run["updated_at"] = _now()
        return _run_save(run)


def _normalizar(text: Any) -> str:
    value = str(text or "").lower()
    value = value.replace("ç", "c").replace("ã", "a").replace("á", "a").replace("à", "a")
    value = value.replace("é", "e").replace("ê", "e").replace("í", "i").replace("ó", "o")
    value = value.replace("ô", "o").replace("õ", "o").replace("ú", "u")
    try:
        value = value.encode("latin1").decode("utf-8")
    except Exception:
        pass
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value).strip()


def _route_id(route: Any, method: str) -> str:
    name = str(getattr(route, "name", "") or "").strip()
    if name:
        return _safe_id(name)
    path = str(getattr(route, "path", "") or "")
    return _safe_id(f"{method}_{path}")


def _infer_module(route: Any, path: str) -> str:
    tags = getattr(route, "tags", None)
    if tags:
        return str(tags[0] or "sistema").strip() or "sistema"
    bits = [part for part in path.strip("/").split("/") if part and not part.startswith("{")]
    if len(bits) >= 2 and bits[0] == "api":
        return bits[1]
    return bits[0] if bits else "sistema"


def _infer_risk(method: str, route_id: str, path: str) -> str:
    text = _normalizar(f"{method} {route_id} {path}")
    if method == "DELETE" or any(word in text for word in DESTRUCTIVE_WORDS):
        return "destructive"
    if any(word in text for word in EXTERNAL_WORDS):
        return "external_write"
    return "local_write"


def _side_effects_for(risk: str, method: str, path: str) -> tuple[str, ...]:
    if risk == "destructive":
        return ("Pode remover, cancelar, resetar, desconectar ou substituir dados.",)
    if risk == "external_write":
        return ("Pode chamar integracoes externas ou alterar dados sincronizados.",)
    return (f"Executa {method} {path} e pode alterar dados locais.",)


def _schema(required: list[str], properties: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return {
        "type": "object",
        "required": required,
        "properties": properties or {},
    }


def _action_can_execute(spec: CodexActionSpec) -> bool:
    return str(spec.executor or "") in SAFE_EXECUTORS


def _manual_specs() -> dict[str, CodexActionSpec]:
    return {
        "vendas.sync_periodo": CodexActionSpec(
            id="vendas.sync_periodo",
            module="vendas",
            label="Sincronizar vendas e devolucoes por periodo",
            aliases=("sincronizar vendas", "sicronizar vendas", "baixar vendas", "atualizar vendas"),
            params_schema=_schema(
                ["loja", "data_inicio", "data_fim"],
                {
                    "loja": {"type": "string"},
                    "data_inicio": {"type": "string", "format": "date"},
                    "data_fim": {"type": "string", "format": "date"},
                    "forcar_resync": {"type": "boolean"},
                },
            ),
            risk_level="external_write",
            side_effects=("Consulta Bling e grava vendas/notas/devolucoes no banco local.",),
            executor="vendas_sync",
            status_kind="vendas_sync",
            cancellable=True,
        ),
        "vendas.cancel_sync": CodexActionSpec(
            id="vendas.cancel_sync",
            module="vendas",
            label="Cancelar sincronizacao de vendas",
            aliases=("cancelar sincronizacao", "cancelar vendas", "parar sincronizacao"),
            params_schema=_schema([]),
            risk_level="local_write",
            side_effects=("Sinaliza cancelamento dos jobs de vendas em andamento.",),
            executor="vendas_cancel",
            status_kind="vendas_sync",
        ),
        "estoque.sync_bling_to_jk": CodexActionSpec(
            id="estoque.sync_bling_to_jk",
            module="estoque",
            label="Atualizar estoque local pela Bling",
            aliases=("atualizar estoque", "sincronizar estoque", "estoque pela bling", "baixar estoque"),
            params_schema=_schema(["loja"], {"loja": {"type": "string"}}),
            risk_level="external_write",
            side_effects=("Consulta produtos/saldos na Bling e atualiza CSV/historico local do JK Sistema.",),
            executor="estoque_sync",
            status_kind="estoque_sync",
            cancellable=True,
        ),
        "estoque.lancamentos_sku": CodexActionSpec(
            id="estoque.lancamentos_sku",
            module="estoque",
            label="Sincronizar lancamentos de estoque por SKU",
            aliases=("lancamentos de estoque por sku", "movimentos do sku", "sincronizar lote do sku"),
            params_schema=_schema(
                ["loja", "sku"],
                {
                    "loja": {"type": "string"},
                    "sku": {"type": "string"},
                    "data_inicio": {"type": "string", "format": "date"},
                    "data_fim": {"type": "string", "format": "date"},
                },
            ),
            risk_level="external_write",
            side_effects=("Consulta Bling e grava lancamentos de estoque do SKU.",),
            executor="estoque_lancamentos_sku",
            status_kind="estoque_lancamentos",
        ),
        "estoque.lancamentos_periodo": CodexActionSpec(
            id="estoque.lancamentos_periodo",
            module="estoque",
            label="Sincronizar lancamentos de estoque por periodo",
            aliases=("lancamentos de estoque", "movimentos de estoque", "sincronizar lancamentos"),
            params_schema=_schema(
                ["loja"],
                {
                    "loja": {"type": "string"},
                    "data_inicio": {"type": "string", "format": "date"},
                    "data_fim": {"type": "string", "format": "date"},
                },
            ),
            risk_level="external_write",
            side_effects=("Consulta notas na Bling e grava movimentos de estoque por periodo.",),
            executor="estoque_lancamentos_lote",
            status_kind="estoque_lancamentos",
            cancellable=True,
        ),
        "ml.pergunta_responder": CodexActionSpec(
            id="ml.pergunta_responder",
            module="perguntas_pos_venda",
            label="Responder pergunta do Mercado Livre",
            aliases=("responder pergunta mercado livre", "responder pergunta ml", "enviar resposta pergunta", "pergunta aberta"),
            params_schema=_schema(
                ["loja", "question_id", "resposta"],
                {
                    "loja": {"type": "string"},
                    "question_id": {"type": "string"},
                    "resposta": {"type": "string"},
                    "pergunta": {"type": "object"},
                    "sku": {"type": "string"},
                    "item_id": {"type": "string"},
                },
            ),
            risk_level="external_write",
            side_effects=("Envia uma resposta publica ao comprador no Mercado Livre.",),
            executor="ml_pergunta_responder",
            status_kind="mercado_livre",
        ),
        "ml.aprovacao_aprovar": CodexActionSpec(
            id="ml.aprovacao_aprovar",
            module="perguntas_pos_venda",
            label="Aprovar resposta pendente do Mercado Livre",
            aliases=("aprovar resposta mercado livre", "aprovar pergunta ml", "aprovar pos venda", "enviar aprovacao"),
            params_schema=_schema(
                ["approval_id"],
                {
                    "approval_id": {"type": "string"},
                    "resposta": {"type": "string"},
                    "texto": {"type": "string"},
                },
            ),
            risk_level="external_write",
            side_effects=("Aprova uma resposta pendente e envia ao Mercado Livre.",),
            executor="ml_aprovacao_aprovar",
            status_kind="mercado_livre",
        ),
        "ml.anuncio_atualizar": CodexActionSpec(
            id="ml.anuncio_atualizar",
            module="anuncios_ml",
            label="Preparar alteracao de anuncio Mercado Livre",
            aliases=("atualizar anuncio", "alterar anuncio", "mudar preco anuncio", "pausar anuncio", "publicar anuncio"),
            params_schema=_schema(
                ["loja", "item_id", "alteracoes"],
                {
                    "loja": {"type": "string"},
                    "item_id": {"type": "string"},
                    "alteracoes": {"type": "object"},
                },
            ),
            risk_level="external_write",
            side_effects=("Pode alterar preco, estoque, titulo, status ou dados do anuncio no Mercado Livre.",),
            executor="proposal_only",
            status_kind="mercado_livre",
        ),
        "estoque.ajuste_manual": CodexActionSpec(
            id="estoque.ajuste_manual",
            module="estoque",
            label="Preparar ajuste manual de estoque",
            aliases=("alterar estoque", "ajustar estoque", "mudar saldo", "corrigir saldo"),
            params_schema=_schema(
                ["loja", "sku", "saldo", "motivo"],
                {
                    "loja": {"type": "string"},
                    "sku": {"type": "string"},
                    "saldo": {"type": "number"},
                    "motivo": {"type": "string"},
                },
            ),
            risk_level="local_write",
            side_effects=("Pode alterar saldo local ou preparar lancamento de estoque.",),
            executor="proposal_only",
            status_kind="estoque",
        ),
        "cadastro.produto_salvar": CodexActionSpec(
            id="cadastro.produto_salvar",
            module="cadastro",
            label="Preparar salvamento de produto no cadastro",
            aliases=("salvar cadastro", "salvar produto", "atualizar cadastro", "alterar cadastro produto"),
            params_schema=_schema(
                ["sku", "dados"],
                {
                    "sku": {"type": "string"},
                    "dados": {"type": "object"},
                    "loja": {"type": "string"},
                },
            ),
            risk_level="local_write",
            side_effects=("Pode criar ou alterar dados do produto, custo, preco, imposto, descricao ou fotos.",),
            executor="proposal_only",
            status_kind="cadastro",
        ),
        "impostos.aplicar_custo_imposto": CodexActionSpec(
            id="impostos.aplicar_custo_imposto",
            module="impostos",
            label="Preparar aplicacao de custo ou imposto no cadastro",
            aliases=("aplicar imposto", "alterar imposto", "salvar custo", "atualizar custo", "aplicar custo"),
            params_schema=_schema(
                ["sku", "dados"],
                {
                    "sku": {"type": "string"},
                    "dados": {"type": "object"},
                    "loja": {"type": "string"},
                },
            ),
            risk_level="local_write",
            side_effects=("Pode alterar custo, preco ou imposto usado nos calculos de margem.",),
            executor="proposal_only",
            status_kind="impostos",
        ),
        "reports.queue_replenishment": CodexActionSpec(
            id="reports.queue_replenishment",
            module="medias_compras",
            label="Criar lista interna de reposicao",
            aliases=("enviar reposicao para fila", "criar lista de reposicao", "aprovar reposicao black jhon"),
            params_schema=_schema(
                ["report_id", "report_action"],
                {"report_id": {"type": "string"}, "report_action": {"type": "object"}},
            ),
            risk_level="local_write",
            side_effects=("Cria uma lista local em Medias e Compras com status Lista gerada; nao altera estoque externo.",),
            executor="internal_report_queue",
            status_kind="black_jhon_report",
        ),
        "reports.queue_price_review": CodexActionSpec(
            id="reports.queue_price_review",
            module="vendas",
            label="Criar fila interna de revisao de preco",
            aliases=("enviar preco para revisao", "criar fila de preco", "aprovar revisao de preco"),
            params_schema=_schema(
                ["report_id", "report_action"],
                {"report_id": {"type": "string"}, "report_action": {"type": "object"}},
            ),
            risk_level="local_write",
            side_effects=("Registra uma revisao interna; nao altera preco ou anuncio externo.",),
            executor="internal_report_queue",
            status_kind="black_jhon_report",
        ),
        "reports.queue_liquidation": CodexActionSpec(
            id="reports.queue_liquidation",
            module="estoque",
            label="Criar fila interna de liquidacao de excesso",
            aliases=("enviar excesso para liquidacao", "criar fila de liquidacao", "aprovar liquidacao"),
            params_schema=_schema(
                ["report_id", "report_action"],
                {"report_id": {"type": "string"}, "report_action": {"type": "object"}},
            ),
            risk_level="local_write",
            side_effects=("Registra uma fila interna de liquidacao; nao altera anuncio, preco ou estoque externo.",),
            executor="internal_report_queue",
            status_kind="black_jhon_report",
        ),
    }


def _discover_route_specs() -> dict[str, CodexActionSpec]:
    app = globals().get("app") or getattr(globals().get("_runtime"), "app", None)
    specs: dict[str, CodexActionSpec] = {}
    for route in list(getattr(app, "routes", []) or []):
        path = str(getattr(route, "path", "") or "")
        if not path or any(path.startswith(prefix) for prefix in EXCLUDED_PATH_PREFIXES):
            continue
        methods = sorted(set(getattr(route, "methods", set()) or set()) & MUTATING_METHODS)
        for method in methods:
            rid = _route_id(route, method)
            action_id = f"{_infer_module(route, path)}.{rid}"
            risk = _infer_risk(method, rid, path)
            aliases = (rid.replace("_", " "), path.replace("/", " "), path)
            specs[action_id] = CodexActionSpec(
                id=action_id,
                module=_infer_module(route, path),
                label=str(getattr(route, "name", "") or f"{method} {path}"),
                aliases=aliases,
                params_schema=_schema(
                    [],
                    {
                        "path_params": {"type": "object"},
                        "query": {"type": "object"},
                        "body": {"type": "object"},
                    },
                ),
                risk_level=risk,
                side_effects=_side_effects_for(risk, method, path),
                executor="generic_route",
                route_path=path,
                method=method,
                cancellable=False,
            )
    try:
        source_routes = _routes_from_router_sources()
    except Exception:
        source_routes = []
    for item in source_routes:
        method = str(item.get("method") or "").upper()
        path = str(item.get("path") or "")
        if method not in MUTATING_METHODS or not path or any(path.startswith(prefix) for prefix in EXCLUDED_PATH_PREFIXES):
            continue
        rid = _safe_id(str(item.get("name") or item.get("id") or f"{method}_{path}"))
        action_id = f"{item.get('module') or _infer_module(None, path)}.{rid}"
        if action_id in specs:
            continue
        risk = _infer_risk(method, rid, path)
        specs[action_id] = CodexActionSpec(
            id=action_id,
            module=str(item.get("module") or _infer_module(None, path) or "sistema"),
            label=str(item.get("name") or f"{method} {path}"),
            aliases=(rid.replace("_", " "), path.replace("/", " "), path),
            params_schema=_schema(
                [],
                {
                    "path_params": {"type": "object"},
                    "query": {"type": "object"},
                    "body": {"type": "object"},
                },
            ),
            risk_level=risk,
            side_effects=_side_effects_for(risk, method, path),
            executor="generic_route",
            route_path=path,
            method=method,
            cancellable=False,
        )
    specs.update(_manual_specs())
    return dict(sorted(specs.items(), key=lambda item: item[0]))


def _public_spec(spec: CodexActionSpec) -> dict[str, Any]:
    return codex_agent_runtime.enrich_action_contract({
        "id": spec.id,
        "module": spec.module,
        "label": spec.label,
        "aliases": list(spec.aliases),
        "params_schema": spec.params_schema,
        "risk_level": spec.risk_level,
        "side_effects": list(spec.side_effects),
        "requires_confirmation": spec.requires_confirmation,
        "executor": spec.executor,
        "route_path": spec.route_path,
        "method": spec.method,
        "status_kind": spec.status_kind,
        "cancellable": spec.cancellable,
        "can_execute": _action_can_execute(spec),
        "proposal_only": not _action_can_execute(spec),
    })


def list_actions() -> dict[str, Any]:
    specs = _discover_route_specs()
    return {
        "success": True,
        "actions": [_public_spec(spec) for spec in specs.values()],
        "total": len(specs),
        "generated_at": _now(),
        "requires_confirmation_default": True,
    }


def _inventory_path() -> Path:
    path = _info_root() / "program_functions_inventory.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _safe_rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path(_base_dir()).resolve())).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


def _literal_str(node: Any) -> str:
    try:
        value = ast.literal_eval(node)
        return str(value or "")
    except Exception:
        return ""


def _literal_methods(node: Any) -> list[str]:
    try:
        value = ast.literal_eval(node)
    except Exception:
        value = None
    if isinstance(value, str):
        return [value.upper()]
    if isinstance(value, (list, tuple, set)):
        return [str(item or "").upper() for item in value if str(item or "").strip()]
    return []


def _route_public_item(
    *,
    method: str,
    path: str,
    name: str = "",
    source: str = "",
    endpoint: str = "",
    tags: Optional[list[str]] = None,
) -> dict[str, Any]:
    method = str(method or "GET").upper()
    path = str(path or "").strip()
    module = str(tags[0] if tags else _infer_module(None, path) or "sistema")
    route_name = str(name or endpoint or f"{method} {path}").strip()
    mutating = method in MUTATING_METHODS
    return {
        "id": _safe_id(f"{method}:{path}:{route_name}", 140),
        "kind": "route",
        "module": module,
        "name": route_name,
        "method": method,
        "path": path,
        "read_only": method in READ_ONLY_METHODS,
        "mutating": mutating,
        "requires_approval": mutating,
        "source": source,
        "endpoint": endpoint,
        "summary": f"{method} {path}",
    }


def _routes_from_app() -> list[dict[str, Any]]:
    app = globals().get("app") or getattr(globals().get("_runtime"), "app", None)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for route in list(getattr(app, "routes", []) or []):
        path = str(getattr(route, "path", "") or "")
        if not path or any(path.startswith(prefix) for prefix in EXCLUDED_PATH_PREFIXES):
            continue
        tags = [str(item or "") for item in (getattr(route, "tags", None) or []) if str(item or "").strip()]
        endpoint = getattr(route, "endpoint", None)
        endpoint_name = str(getattr(endpoint, "__name__", "") or "")
        route_name = str(getattr(route, "name", "") or endpoint_name)
        for method in sorted(set(getattr(route, "methods", set()) or set())):
            method = str(method or "").upper()
            if method in {"HEAD", "OPTIONS"}:
                continue
            key = f"{method} {path}"
            if key in seen:
                continue
            seen.add(key)
            items.append(_route_public_item(method=method, path=path, name=route_name, endpoint=endpoint_name, tags=tags))
    return sorted(items, key=lambda item: (item.get("module") or "", item.get("path") or "", item.get("method") or ""))


def _routes_from_router_sources() -> list[dict[str, Any]]:
    router_dir = Path(_base_dir()) / "backend" / "routers"
    if not router_dir.exists():
        return []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(router_dir.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="strict"))
        except Exception:
            continue
        source = _safe_rel(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            attr = str(getattr(func, "attr", "") or "")
            route_path = ""
            methods: list[str] = []
            route_name = ""
            endpoint = ""
            if attr == "add_api_route":
                if node.args:
                    route_path = _literal_str(node.args[0])
                if len(node.args) >= 2:
                    endpoint = getattr(node.args[1], "attr", "") or getattr(node.args[1], "id", "") or ""
                for kw in node.keywords or []:
                    if kw.arg == "methods":
                        methods = _literal_methods(kw.value)
                    elif kw.arg == "name":
                        route_name = _literal_str(kw.value)
                methods = methods or ["GET"]
            elif attr in {"get", "post", "put", "patch", "delete"}:
                if node.args:
                    route_path = _literal_str(node.args[0])
                methods = [attr.upper()]
                for kw in node.keywords or []:
                    if kw.arg == "name":
                        route_name = _literal_str(kw.value)
            if not route_path:
                continue
            for method in methods:
                method = str(method or "GET").upper()
                if method in {"HEAD", "OPTIONS"}:
                    continue
                key = f"{method} {route_path}"
                if key in seen:
                    continue
                seen.add(key)
                items.append(_route_public_item(method=method, path=route_path, name=route_name, endpoint=endpoint, source=source))
    return sorted(items, key=lambda item: (item.get("module") or "", item.get("path") or "", item.get("method") or ""))


def _routes_inventory() -> list[dict[str, Any]]:
    routes = _routes_from_app()
    return routes or _routes_from_router_sources()


def _services_inventory() -> list[dict[str, Any]]:
    services_dir = Path(_base_dir()) / "backend" / "services"
    if not services_dir.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(services_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="strict"))
        except Exception:
            continue
        module = path.stem
        source = _safe_rel(path)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("_") and not node.name.startswith("_ia_tool_"):
                    continue
                doc = ast.get_docstring(node) or ""
                items.append(
                    {
                        "id": _safe_id(f"service:{module}.{node.name}", 140),
                        "kind": "service_function",
                        "module": module,
                        "name": node.name,
                        "async": isinstance(node, ast.AsyncFunctionDef),
                        "read_only": node.name.startswith(("listar", "buscar", "obter", "get_", "consultar", "progresso", "_ia_tool_get", "_ia_tool_detect")),
                        "mutating": bool(re.search(r"\b(salvar|atualizar|sincronizar|criar|remover|deletar|cancelar|aprovar|enviar|executar|limpar)\b", node.name)),
                        "requires_approval": bool(re.search(r"\b(salvar|atualizar|sincronizar|criar|remover|deletar|cancelar|aprovar|enviar|executar|limpar)\b", node.name)),
                        "source": source,
                        "line": int(getattr(node, "lineno", 0) or 0),
                        "summary": doc.strip().splitlines()[0][:260] if doc else node.name.replace("_", " "),
                    }
                )
            elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                doc = ast.get_docstring(node) or ""
                items.append(
                    {
                        "id": _safe_id(f"class:{module}.{node.name}", 140),
                        "kind": "service_class",
                        "module": module,
                        "name": node.name,
                        "read_only": False,
                        "mutating": False,
                        "requires_approval": False,
                        "source": source,
                        "line": int(getattr(node, "lineno", 0) or 0),
                        "summary": doc.strip().splitlines()[0][:260] if doc else node.name,
                    }
                )
    return items


def _pages_inventory() -> list[dict[str, Any]]:
    base = Path(_base_dir())
    candidates = list(base.glob("*.html"))
    static_dir = base / "static"
    if static_dir.exists():
        candidates.extend(static_dir.glob("*.html"))
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(candidates):
        rel = _safe_rel(path)
        if rel in seen:
            continue
        seen.add(rel)
        module = path.stem.replace("_", "-")
        title = path.stem.replace("_", " ").replace("-", " ").title()
        try:
            text = path.read_text(encoding="utf-8", errors="strict")[:6000]
            match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.I | re.S)
            if match:
                title = re.sub(r"\s+", " ", match.group(1)).strip()[:160] or title
        except Exception:
            pass
        items.append(
            {
                "id": _safe_id(f"page:{rel}", 140),
                "kind": "page",
                "module": module,
                "name": title,
                "path": rel,
                "read_only": True,
                "mutating": False,
                "requires_approval": False,
                "summary": f"Tela HTML {title}",
            }
        )
    return items


def _actions_inventory() -> list[dict[str, Any]]:
    return [
        {
            **_public_spec(spec),
            "kind": "approved_action",
            "read_only": False,
            "mutating": True,
            "requires_approval": True,
            "summary": spec.label,
        }
        for spec in _discover_route_specs().values()
    ]


def _inventory_match(item: dict[str, Any], query: str, module: str) -> bool:
    if module:
        module_norm = _normalizar(module)
        item_module = _normalizar(item.get("module") or "")
        if module_norm and module_norm not in item_module and item_module not in module_norm:
            return False
    if not query:
        return True
    query_norm = _normalizar(query)
    haystack = _normalizar(
        " ".join(
            str(item.get(key) or "")
            for key in ("id", "kind", "module", "name", "summary", "path", "method", "source", "label", "route_path")
        )
    )
    aliases = item.get("aliases")
    if isinstance(aliases, list):
        haystack += " " + _normalizar(" ".join(str(value or "") for value in aliases))
    return all(part in haystack for part in query_norm.split()[:8] if len(part) >= 2)


def _inventory_filter_query(query: str) -> str:
    text = _normalizar(query)
    if not text:
        return ""
    generic = {
        "liste",
        "listar",
        "lista",
        "todas",
        "todos",
        "tudo",
        "funcoes",
        "funcionalidades",
        "programa",
        "sistema",
        "a",
        "as",
        "o",
        "os",
        "de",
        "do",
        "da",
        "dos",
        "das",
        "e",
        "para",
        "que",
        "joao",
        "pretinho",
        "voce",
        "consegue",
        "fazer",
        "saber",
        "catalogo",
        "inventario",
        "modulos",
        "rotas",
        "telas",
        "servicos",
        "disponiveis",
    }
    tokens = [token for token in re.split(r"[^a-z0-9]+", text) if token and token not in generic]
    return " ".join(tokens[:8])


def _limit_items(items: list[dict[str, Any]], query: str, module: str, limit: int) -> list[dict[str, Any]]:
    filtered = [item for item in items if _inventory_match(item, query, module)]
    return filtered[: max(1, min(int(limit or 300), 2000))]


def list_program_functions(
    *,
    client_id: str = "",
    query: str = "",
    module: str = "",
    limit: int = 300,
    include_routes: bool = True,
    include_services: bool = True,
    include_actions: bool = True,
    include_pages: bool = True,
) -> dict[str, Any]:
    query = str(query or "").strip()
    module = str(module or "").strip()
    filter_query = _inventory_filter_query(query)
    limit = max(1, min(int(limit or 300), 2000))
    routes = _limit_items(_routes_inventory(), filter_query, module, limit) if include_routes else []
    services = _limit_items(_services_inventory(), filter_query, module, limit) if include_services else []
    actions = _limit_items(_actions_inventory(), filter_query, module, limit) if include_actions else []
    pages = _limit_items(_pages_inventory(), filter_query, module, limit) if include_pages else []
    modules: dict[str, dict[str, Any]] = {}
    for group_name, group_items in (("routes", routes), ("services", services), ("actions", actions), ("pages", pages)):
        for item in group_items:
            module_name = str(item.get("module") or "sistema")
            bucket = modules.setdefault(module_name, {"module": module_name, "routes": 0, "services": 0, "actions": 0, "pages": 0})
            bucket[group_name] += 1
    rows = (actions + routes + pages + services)[: min(limit, 200)]
    payload = {
        "success": True,
        "client_id": str(client_id or ""),
        "query": query,
        "filter_query": filter_query,
        "module_filter": module,
        "routes": routes,
        "services": services,
        "actions": actions,
        "pages": pages,
        "modules": sorted(modules.values(), key=lambda item: item["module"]),
        "rows": rows,
        "totals": {
            "routes": len(routes),
            "services": len(services),
            "actions": len(actions),
            "pages": len(pages),
            "modules": len(modules),
            "rows": len(rows),
        },
        "generated_at": _now(),
        "safety_model": {
            "read_only_runs_automatically": True,
            "mutating_requires_approval": True,
            "external_or_destructive_requires_approval": True,
        },
    }
    try:
        _write_json(_inventory_path(), payload)
    except Exception:
        pass
    return payload


def _action_id_from_capability(client_id: str, capability_id: str) -> str:
    capability_id = str(capability_id or "").strip()
    if not capability_id:
        return ""
    try:
        from backend.services import codex_capabilities

        cap = codex_capabilities.get_capability(client_id=str(client_id or "default"), capability_id=capability_id)
    except Exception:
        cap = {}
    if not isinstance(cap, dict):
        return ""
    executor_type = str(cap.get("executor_type") or "").strip()
    if executor_type == "approved_action":
        return str(cap.get("executor_id") or "").strip()
    if executor_type == "screen_helper" and str(cap.get("executor_id") or "") == "program_action_match":
        return str(cap.get("action_id") or "").strip()
    return ""


def match_action_dry_run(
    *,
    client_id: str,
    username: str = "codex",
    message: str,
    action_id: str = "",
    capability_id: str = "",
    params: Optional[dict[str, Any]] = None,
    screen_context: Optional[dict[str, Any]] = None,
    history: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    specs = _discover_route_specs()
    cap_action_id = _action_id_from_capability(client_id, capability_id)
    requested_action_id = str(action_id or cap_action_id or "").strip()
    spec = specs.get(requested_action_id) if requested_action_id else None
    spec = spec or _select_action(message, specs)
    reference_message = ""
    if not spec:
        reference_message = _last_action_message_from_history(message, history, specs)
        if reference_message:
            spec = _select_action(reference_message, specs)
    if not spec:
        return {
            "success": True,
            "matched": False,
            "message": "Nenhuma acao operacional aprovada foi reconhecida para este pedido.",
            "generated_at": _now(),
        }
    merged = (
        _merge_followup_params(client_id, spec, message, reference_message, params)
        if reference_message
        else _merge_params(client_id, spec, message, screen_context, params)
    )
    missing = _missing_params(spec, merged)
    public_action = _public_spec(spec)
    proposal_preview = {
        "title": _proposal_title(spec),
        "summary": _proposal_summary(spec, merged),
        "accounts": _proposal_accounts(merged),
        "entities": _proposal_entities(merged),
        "params": merged,
        "risk": spec.risk_level,
        "side_effects": list(spec.side_effects),
        "missing_params": missing,
        "can_execute": _action_can_execute(spec),
        "requires_approval": True,
        "after_intent": _proposal_after_intent(spec, merged),
    }
    return {
        "success": True,
        "matched": True,
        "dry_run": True,
        "needs_input": bool(missing),
        "action": public_action,
        "params": merged,
        "missing_params": missing,
        "proposal_preview": proposal_preview,
        "resolved_from_history": bool(reference_message),
        "reference_message": reference_message[:500],
        "requires_confirmation": True,
        "created_by": username,
        "client_id": client_id,
        "capability_id": str(capability_id or ""),
        "message": (
            "Esta acao pode ser proposta para aprovacao."
            if not missing
            else "Faltam parametros antes de criar a proposta: " + ", ".join(missing)
        ),
        "generated_at": _now(),
    }


def _screen_text(screen_context: Any) -> str:
    if not isinstance(screen_context, dict):
        return ""
    parts = [
        screen_context.get("title"),
        screen_context.get("url"),
        screen_context.get("modulo_atual"),
        screen_context.get("visible_text"),
    ]
    controls = screen_context.get("controls")
    if isinstance(controls, list):
        for item in controls[:80]:
            if isinstance(item, dict):
                parts.append(" ".join(str(item.get(k) or "") for k in ("label", "name", "id", "value", "text")))
            else:
                parts.append(str(item))
    return "\n".join(str(part or "") for part in parts)


def _parse_date(value: str) -> str:
    value = str(value or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return value
    match = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", value)
    if match:
        d, m, y = match.groups()
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    return ""


def _extract_dates(text: str) -> tuple[str, str]:
    matches = re.findall(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{4}\b", text)
    dates = [_parse_date(item) for item in matches]
    dates = [item for item in dates if item]
    if len(dates) >= 2:
        return dates[0], dates[1]
    if len(dates) == 1:
        return dates[0], dates[0]
    return "", ""


def _load_lojas(client_id: str) -> list[dict[str, Any]]:
    try:
        fn = globals().get("carregar_lojas")
        if callable(fn):
            lojas = fn(client_id)
            return list(lojas or []) if isinstance(lojas, list) else []
    except Exception:
        pass
    try:
        from backend.services import integracoes

        return list(integracoes.carregar_lojas(client_id) or [])
    except Exception:
        return []


def _loja_names(client_id: str) -> list[str]:
    names = []
    for loja in _load_lojas(client_id):
        name = str((loja or {}).get("nome") or "").strip()
        if name:
            names.append(name)
    return names


def _extract_loja(client_id: str, text: str) -> str:
    norm = _normalizar(text)
    if re.search(r"\b(todas as lojas|todas lojas|todos os lojas|todas as contas|todas contas|__todas)\b", norm):
        return "__todas"
    names = list(dict.fromkeys([*_loja_names(client_id), *COMMON_LOJA_ALIASES.values()]))
    for name in names:
        normalized_name = _normalizar(name)
        if normalized_name and normalized_name in norm:
            return name
    for alias, name in COMMON_LOJA_ALIASES.items():
        if alias in norm:
            return name
    return ""


def _extract_sku(text: str) -> str:
    match = re.search(r"\bsku[:\s#-]*([a-zA-Z0-9._/-]{2,})\b", text, flags=re.I)
    if match:
        return match.group(1).strip()
    return ""


def _extract_named_id(text: str, *names: str) -> str:
    for name in names:
        pattern = rf"\b{re.escape(name)}\s*[:#=\-]\s*([a-zA-Z0-9._/-]{{2,}})\b"
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(1).strip()
    return ""


def _extract_mlb(text: str) -> str:
    match = re.search(r"\b(MLB\d{6,})\b", text, flags=re.I)
    return match.group(1).upper() if match else ""


def _extract_response_text(text: str) -> str:
    patterns = (
        r"\b(?:responda|responder|envie|enviar)\s+(?:com|o texto|a resposta)\s*[:\-]?\s*[\"'“”]?(.+)$",
        r"\b(?:resposta|texto)\s*[:\-]\s*[\"'“”]?(.+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I | re.S)
        if match:
            value = match.group(1).strip().strip("\"'“”")
            return value[:2000]
    return ""


def _extract_number_value(text: str, *labels: str) -> str:
    for label in labels:
        match = re.search(rf"\b{re.escape(label)}\s*[:=\-]?\s*(-?\d+(?:[,.]\d+)?)\b", text, flags=re.I)
        if match:
            return match.group(1).replace(",", ".")
    match = re.search(r"\bpara\s*(-?\d+(?:[,.]\d+)?)\b", text, flags=re.I)
    return match.group(1).replace(",", ".") if match else ""


def _is_followup_command(message: str) -> bool:
    text = _normalizar(message)
    return bool(re.search(
        r"\b(faca o mesmo|faz o mesmo|repita|repetir|tambem|mesma coisa|o mesmo|nessa loja|nesta loja|para essa loja|para esta loja)\b",
        text,
    ))


def _is_capability_or_readonly_request(message: str) -> bool:
    text = _normalizar(message)
    if not text:
        return False
    if re.search(r"^(voce\s+)?(consegue|pode|sabe|tem como|e possivel|da para)\b", text):
        return True
    if re.search(
        r"\b(verifique|verificar|olhe|analise|analisar|me diga|explique|qual|quais|como|quanto|quando|liste|listar|mostre|mostrar|consulta|consultar|busca|buscar|relatorio|ranking|saldo)\b",
        text,
    ):
        return True
    return "?" in str(message or "")


def _is_explicit_mutating_request(message: str) -> bool:
    text = _normalizar(message)
    if not text:
        return False
    if _is_capability_or_readonly_request(message):
        return False
    if re.search(r"\bresponda\b", text) and re.search(r"\b(com|texto|mensagem|resposta pronta|essa resposta|esta resposta|pergunta|pos venda|pos-venda|mercado livre|mercadolivre)\b", text):
        return True
    if re.search(r"\b(envie|enviar|aprovar|aprove|publique|publicar)\b", text) and re.search(
        r"\b(pergunta|resposta|mercado livre|mercadolivre|anuncio|bling)\b",
        text,
    ):
        return True
    return bool(re.search(
        r"\b(sincronize|sincronizar|sincroniza|sicronize|sicronizar|baixar|baixe|atualize|atualizar|altere|alterar|ajuste|ajustar|corrija|corrigir|mude|mudar|forcar|force|re-sincronizar|reprocessar|salve|salvar|remova|remover|exclua|excluir|limpe|limpar|cancele|cancelar|execute|executar|rode|rodar)\b",
        text,
    ))


def _select_action(message: str, specs: dict[str, CodexActionSpec]) -> Optional[CodexActionSpec]:
    text = _normalizar(message)
    if not _is_explicit_mutating_request(message):
        return None
    # Pos-venda e exclusivamente manual: o Black Jhon nao pode nem propor uma
    # acao de resposta/aprovacao para esse fluxo.
    if re.search(r"\b(pos venda|pos-venda|mensagem privada)\b", text):
        return None
    action_words = r"(sincronizar|sincronize|sincroniza|sicronizar|sicronize|baixar|baixe|atualizar|atualize|forcar|force|re-sincronizar)"
    if re.search(r"\b(aprovar|aprove|enviar aprovacao|confirmar aprovacao)\b", text) and re.search(r"\b(pergunta|resposta|pos venda|pos-venda|mercado livre|mercadolivre|aprovacao)\b", text):
        return specs.get("ml.aprovacao_aprovar")
    if re.search(r"\b(responder|responda|enviar|envie|mandar|mande)\b", text) and re.search(r"\b(pergunta|mercado livre|mercadolivre|ml)\b", text):
        return specs.get("ml.pergunta_responder")
    if re.search(r"\b(cancelar|cancele|parar|pare)\b", text) and re.search(r"\b(sincronizacao|sincronizar|sincronize|sicronizar|sicronize|vendas)\b", text):
        return specs.get("vendas.cancel_sync")
    if re.search(rf"\b{action_words}\b", text) and re.search(r"\b(venda|vendas|devolucao|devolucoes|pedido|pedidos)\b", text):
        return specs.get("vendas.sync_periodo")
    if re.search(r"\b(alterar|altere|atualizar|atualize|mudar|mude|pausar|pause|publicar|publique|salvar|salve)\b", text) and re.search(r"\b(anuncio|anuncios|mercado livre|mercadolivre|mlb\d+)\b", text):
        return specs.get("ml.anuncio_atualizar")
    if re.search(r"\b(alterar|altere|ajustar|ajuste|corrigir|corrija|mudar|mude)\b", text) and re.search(r"\b(estoque|saldo)\b", text):
        return specs.get("estoque.ajuste_manual")
    if re.search(r"\b(aplicar|salvar|atualizar|alterar|corrigir)\b", text) and re.search(r"\b(custo|imposto|tributacao|margem)\b", text):
        return specs.get("impostos.aplicar_custo_imposto")
    if re.search(r"\b(salvar|salve|atualizar|atualize|alterar|altere|incluir|inclua)\b", text) and re.search(r"\b(cadastro|produto|sku)\b", text):
        return specs.get("cadastro.produto_salvar")
    if re.search(rf"\b{action_words}\b", text) and "estoque" in text and "lancamento" not in text:
        return specs.get("estoque.sync_bling_to_jk")
    if "estoque" in text and "lancamento" in text and "sku" in text:
        return specs.get("estoque.lancamentos_sku")
    if "estoque" in text and "lancamento" in text:
        return specs.get("estoque.lancamentos_periodo")

    best: tuple[int, Optional[CodexActionSpec]] = (0, None)
    for spec in specs.values():
        candidates = [spec.id, spec.label, spec.route_path, *spec.aliases]
        score = 0
        for candidate in candidates:
            cand = _normalizar(candidate)
            if cand and cand in text:
                score = max(score, min(80, 20 + len(cand) // 2))
            else:
                words = [w for w in re.split(r"[^a-z0-9]+", cand) if len(w) >= 4]
                hits = sum(1 for word in words[:8] if word in text)
                if hits >= 2:
                    score = max(score, hits * 8)
        if score > best[0]:
            best = (score, spec)
    return best[1] if best[0] >= 16 else None


def _history_messages(history: Any) -> list[dict[str, Any]]:
    if not isinstance(history, list):
        return []
    items: list[dict[str, Any]] = []
    for item in history[-18:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        text = str(item.get("text") or item.get("content") or "").strip()
        if text:
            items.append({"role": role, "text": text})
    return items


def _last_action_message_from_history(message: str, history: Any, specs: dict[str, CodexActionSpec]) -> str:
    if not _is_followup_command(message):
        return ""
    current = _normalizar(message)
    for item in reversed(_history_messages(history)):
        role = str(item.get("role") or "")
        text = str(item.get("text") or "").strip()
        if role != "user" or not text:
            continue
        if _normalizar(text) == current:
            continue
        if _select_action(text, specs):
            return text
    return ""


def _merge_followup_params(
    client_id: str,
    spec: CodexActionSpec,
    message: str,
    reference_message: str,
    raw_params: Any,
) -> dict[str, Any]:
    params = _merge_params(client_id, spec, reference_message, {}, raw_params)
    current = _merge_params(client_id, spec, message, {}, {})
    for key in ("loja", "data_inicio", "data_fim", "sku", "question_id", "approval_id", "pack_id", "order_id", "buyer_id", "item_id", "resposta", "texto"):
        value = current.get(key)
        if value not in (None, ""):
            params[key] = value
    if current.get("forcar_resync"):
        params["forcar_resync"] = True
    return params


def _merge_params(client_id: str, spec: CodexActionSpec, message: str, screen_context: Any, raw_params: Any) -> dict[str, Any]:
    params = dict(raw_params or {}) if isinstance(raw_params, dict) else {}
    corpus = "\n".join([message, _screen_text(screen_context)])
    data_inicio, data_fim = _extract_dates(corpus)
    loja = _extract_loja(client_id, corpus)
    sku = _extract_sku(corpus)
    question_id = _extract_named_id(corpus, "question_id", "pergunta_id", "id_pergunta")
    approval_id = _extract_named_id(corpus, "approval_id", "aprovacao", "aprovação", "aprovacao_id")
    pack_id = _extract_named_id(corpus, "pack_id", "pack", "pacote")
    order_id = _extract_named_id(corpus, "order_id", "pedido", "pedido_id")
    buyer_id = _extract_named_id(corpus, "buyer_id", "comprador", "buyer")
    item_id = _extract_named_id(corpus, "item_id", "anuncio", "anúncio", "mlb") or _extract_mlb(corpus)
    resposta = _extract_response_text(message)
    saldo = _extract_number_value(corpus, "saldo", "estoque", "quantidade")

    if data_inicio and "data_inicio" not in params:
        params["data_inicio"] = data_inicio
    if data_fim and "data_fim" not in params:
        params["data_fim"] = data_fim
    if loja and "loja" not in params:
        params["loja"] = loja
    if sku and "sku" not in params:
        params["sku"] = sku
    for key, value in (
        ("question_id", question_id),
        ("approval_id", approval_id),
        ("pack_id", pack_id),
        ("order_id", order_id),
        ("buyer_id", buyer_id),
        ("item_id", item_id),
        ("resposta", resposta),
        ("texto", resposta),
        ("saldo", saldo if spec.id == "estoque.ajuste_manual" else ""),
    ):
        if value and key not in params:
            params[key] = value
    if "forcar_resync" not in params:
        params["forcar_resync"] = bool(re.search(r"\b(forcar|forçar|resync|re-sincronizar|reprocessar)\b", _normalizar(message)))

    if spec.id in {"ml.anuncio_atualizar", "cadastro.produto_salvar", "impostos.aplicar_custo_imposto"}:
        params.setdefault("dados", {})
        params.setdefault("alteracoes", params.get("dados") or {})
    if spec.id == "estoque.ajuste_manual" and "motivo" not in params:
        params["motivo"] = "Solicitado pelo Black Jhon; revisar antes de executar."
    if spec.executor == "generic_route":
        params.setdefault("path_params", {})
        params.setdefault("query", {})
        params.setdefault("body", {})
    return params


def _missing_params(spec: CodexActionSpec, params: dict[str, Any]) -> list[str]:
    required = list((spec.params_schema or {}).get("required") or [])
    missing = [key for key in required if params.get(key) in (None, "")]
    if spec.executor == "generic_route":
        path_names = re.findall(r"{([^}:]+)", spec.route_path or "")
        path_params = params.get("path_params") if isinstance(params.get("path_params"), dict) else {}
        missing.extend([f"path_params.{name}" for name in path_names if not path_params.get(name)])
        if spec.method in {"POST", "PUT", "PATCH"} and not params.get("body"):
            missing.append("body")
    return list(dict.fromkeys(missing))


def _proposal_accounts(params: dict[str, Any]) -> list[str]:
    loja = str(params.get("loja") or params.get("conta") or "").strip()
    if loja == "__todas":
        return ["Todas as lojas configuradas"]
    return [loja] if loja else []


def _proposal_entities(params: dict[str, Any]) -> list[dict[str, str]]:
    entities: list[dict[str, str]] = []
    for key, label in (
        ("sku", "SKU"),
        ("question_id", "Pergunta ML"),
        ("approval_id", "Aprovacao"),
        ("pack_id", "Pack"),
        ("order_id", "Pedido"),
        ("buyer_id", "Comprador"),
        ("item_id", "Anuncio"),
    ):
        value = str(params.get(key) or "").strip()
        if value:
            entities.append({"type": label, "id": value})
    return entities


def _proposal_title(spec: CodexActionSpec) -> str:
    return f"Vou executar: {spec.label}" if _action_can_execute(spec) else f"Vou preparar: {spec.label}"


def _proposal_summary(spec: CodexActionSpec, params: dict[str, Any]) -> str:
    accounts = ", ".join(_proposal_accounts(params)) or "conta/loja informada nos parametros"
    entities = ", ".join(f"{item['type']} {item['id']}" for item in _proposal_entities(params)) or "entidades informadas nos parametros"
    return f"Vou fazer {spec.label} em {accounts}, usando {entities}."


def _proposal_after_intent(spec: CodexActionSpec, params: dict[str, Any]) -> str:
    if _action_can_execute(spec):
        return "Ao aprovar, a acao sera enfileirada e executada pelo backend com os parametros exibidos."
    return "Esta proposta ainda nao possui executor seguro; serve para revisao dos dados antes de criar um executor especifico."


def _proposal_before_snapshot(screen_context: Any, params: dict[str, Any]) -> dict[str, Any]:
    context = screen_context if isinstance(screen_context, dict) else {}
    return {
        "title": context.get("title") or "",
        "url": context.get("url") or "",
        "modulo_atual": context.get("modulo_atual") or "",
        "visible_text_preview": str(context.get("visible_text") or "")[:1200],
        "params_preview": {key: value for key, value in list((params or {}).items())[:20]},
    }


def create_proposal(
    *,
    client_id: str,
    username: str,
    message: str,
    action_id: str = "",
    capability_id: str = "",
    params: Optional[dict[str, Any]] = None,
    screen_context: Optional[dict[str, Any]] = None,
    history: Optional[list[dict[str, Any]]] = None,
    conversation_id: str = "",
    conversation_generation: int = 1,
    plan_id: str = "",
    task_id: str = "",
    channel: str = "app",
    wa_id_hash: str = "",
    idempotency_key: str = "",
) -> dict[str, Any]:
    specs = _discover_route_specs()
    cap_action_id = _action_id_from_capability(client_id, capability_id)
    requested_action_id = str(action_id or cap_action_id or "").strip()
    spec = specs.get(requested_action_id) if requested_action_id else None
    spec = spec or _select_action(message, specs)
    reference_message = ""
    if not spec:
        reference_message = _last_action_message_from_history(message, history, specs)
        if reference_message:
            spec = _select_action(reference_message, specs)
    if not spec:
        return {
            "success": True,
            "matched": False,
            "message": "Nenhuma acao operacional segura foi reconhecida para este pedido.",
        }

    merged = (
        _merge_followup_params(client_id, spec, message, reference_message, params)
        if reference_message
        else _merge_params(client_id, spec, message, screen_context, params)
    )
    missing = _missing_params(spec, merged)
    if missing:
        preview_action = _public_spec(spec)
        return {
            "success": True,
            "matched": True,
            "needs_input": True,
            "action": preview_action,
            "capability_id": str(capability_id or ""),
            "missing_params": missing,
            "proposal_preview": {
                "title": _proposal_title(spec),
                "summary": _proposal_summary(spec, merged),
                "accounts": _proposal_accounts(merged),
                "entities": _proposal_entities(merged),
                "params": merged,
                "risk": spec.risk_level,
                "side_effects": list(spec.side_effects),
                "missing_params": missing,
                "can_execute": _action_can_execute(spec),
                "requires_approval": True,
                "preconditions": list(preview_action.get("preconditions") or []),
                "postconditions": list(preview_action.get("postconditions") or []),
                "channels_allowed": list(preview_action.get("channels_allowed") or ["app"]),
                "after_intent": _proposal_after_intent(spec, merged),
            },
            "message": "Preciso destes parametros antes de criar a proposta: " + ", ".join(missing),
        }

    proposal_id = uuid.uuid4().hex
    site_context = {
        "title": (screen_context or {}).get("title") if isinstance(screen_context, dict) else "",
        "url": (screen_context or {}).get("url") if isinstance(screen_context, dict) else "",
        "modulo_atual": (screen_context or {}).get("modulo_atual") if isinstance(screen_context, dict) else "",
        "visible_text_preview": str((screen_context or {}).get("visible_text") or "")[:1200] if isinstance(screen_context, dict) else "",
    }
    can_execute = _action_can_execute(spec)
    public_action = _public_spec(spec)
    before_snapshot = _proposal_before_snapshot(screen_context, merged)
    proposal = {
        "proposal_id": proposal_id,
        "version": 1,
        "status": "awaiting_approval",
        "action": public_action,
        "capability_id": str(capability_id or ""),
        "action_id": spec.id,
        "title": _proposal_title(spec),
        "summary": _proposal_summary(spec, merged),
        "accounts": _proposal_accounts(merged),
        "entities": _proposal_entities(merged),
        "params": merged,
        "before_snapshot": before_snapshot,
        "after_intent": _proposal_after_intent(spec, merged),
        "risk": spec.risk_level,
        "side_effects": list(spec.side_effects),
        "missing_params": [],
        "can_execute": can_execute,
        "requires_approval": True,
        "preconditions": list(public_action.get("preconditions") or []),
        "postconditions": list(public_action.get("postconditions") or []),
        "channels_allowed": list(public_action.get("channels_allowed") or ["app"]),
        "requires_app_confirmation": str(channel or "app") == "whatsapp" and not bool(public_action.get("whatsapp_allowed")),
        "expires_at": _future(24),
        "message": str(message or ""),
        "site_context": site_context,
        "resolved_from_history": bool(reference_message),
        "reference_message": reference_message[:500],
        "created_at": _now(),
        "created_by": username,
        "client_id": client_id,
        "conversation_id": str(conversation_id or ""),
        "conversation_generation": max(1, int(conversation_generation or 1)),
        "plan_id": str(plan_id or ""),
        "task_id": str(task_id or ""),
        "channel": "whatsapp" if str(channel or "").lower() == "whatsapp" else "app",
        "wa_id_hash": str(wa_id_hash or ""),
        "idempotency_key": str(idempotency_key or ""),
        "requires_confirmation": True,
    }
    proposal["precondition_hash"] = hashlib.sha256(
        json.dumps(
            {"action_id": spec.id, "params": merged, "before_snapshot": before_snapshot},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8", "replace")
    ).hexdigest()
    proposal["proposal_hash"] = codex_agent_runtime.proposal_hash(proposal)
    proposal = _proposal_save(proposal)
    if plan_id:
        try:
            codex_agent_runtime.transition_plan(
                _assistant_info_base(),
                client_id,
                plan_id,
                "aguardando_aprovacao",
                current_step="aprovar",
                proposal=proposal,
                details={"proposal_id": proposal_id, "action_id": spec.id},
            )
        except Exception:
            pass
    codex_agent_runtime.audit(
        _assistant_info_base(),
        client_id,
        event_type="proposal_created",
        entity_type="proposal",
        entity_id=proposal_id,
        actor=username,
        channel=str(channel or "app"),
        payload={
            "action_id": spec.id,
            "version": 1,
            "proposal_hash": proposal.get("proposal_hash"),
            "can_execute": can_execute,
            "requires_app_confirmation": proposal.get("requires_app_confirmation"),
        },
    )
    try:
        from backend.services import codex_operational_memory

        codex_operational_memory.add_memory(
            client_id,
            category="propostas",
            content=f"Proposta criada: {_proposal_summary(spec, merged)}",
            source="codex_action_proposal",
            metadata={
                "proposal_id": proposal_id,
                "action_id": spec.id,
                "can_execute": can_execute,
                "risk": spec.risk_level,
            },
            importance=3,
            entry_id=f"proposal_{proposal_id}",
        )
    except Exception:
        pass
    return {"success": True, "matched": True, "proposal": proposal}


def _run_async(coro):
    return asyncio.run(coro)


def _expand_lojas(client_id: str, loja: str) -> list[str]:
    loja = str(loja or "").strip()
    if loja == "__todas":
        return _loja_names(client_id)
    return [loja] if loja else []


def _progress_payload(kind: str, client_id: str) -> dict[str, Any]:
    try:
        if kind == "vendas_sync":
            from backend.services import vendas

            return _run_async(vendas.progresso_sincronizacao_vendas(client_id))
        if kind == "estoque_sync":
            from backend.services import estoque

            return _run_async(estoque.progresso_sincronizacao_estoque(client_id))
        if kind == "estoque_lancamentos":
            from backend.services import estoque

            return _run_async(estoque.progresso_sincronizacao_lancamentos_estoque(client_id))
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    return {}


def _progress_is_active(payload: dict[str, Any]) -> bool:
    try:
        if bool(payload.get("active")):
            return True
        return int(payload.get("active_count") or 0) > 0
    except Exception:
        return False


def _poll_progress_until_idle(run_id: str, kind: str, client_id: str, max_seconds: int = 12 * 60 * 60) -> dict[str, Any]:
    deadline = time.time() + max_seconds
    last: dict[str, Any] = {}
    checks = 0
    seen_active = False
    while time.time() < deadline:
        last = _progress_payload(kind, client_id)
        active = _progress_is_active(last)
        seen_active = seen_active or active
        _update_run(
            run_id,
            live_status=str(((last.get("progress") or {}) if isinstance(last.get("progress"), dict) else {}).get("mensagem") or ""),
            progress=last.get("progress"),
            logs=list(last.get("logs") or [])[-120:],
            sync_meta=last.get("sync_meta"),
            result_preview=last,
        )
        if not active and (seen_active or checks >= 3):
            return last
        checks += 1
        time.sleep(2)
    raise TimeoutError("Tempo maximo de acompanhamento excedido.")


def _execute_vendas_sync(run_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
    from backend.schemas import VendasSyncRequest
    from backend.services import vendas

    client_id = str(proposal.get("client_id") or "default")
    params = proposal.get("params") or {}
    lojas = _expand_lojas(client_id, str(params.get("loja") or ""))
    if not lojas:
        raise RuntimeError("Nenhuma loja encontrada para sincronizar vendas.")

    pending = list(lojas)
    started: list[dict[str, Any]] = []
    while pending:
        progress = _progress_payload("vendas_sync", client_id)
        active_count = int(progress.get("active_count") or 0)
        if active_count >= 2:
            _update_run(run_id, live_status="Aguardando vaga para sincronizar mais lojas.", progress=progress.get("progress"), logs=list(progress.get("logs") or [])[-120:])
            time.sleep(2)
            continue
        loja = pending.pop(0)
        req = VendasSyncRequest(
            loja=loja,
            data_inicio=str(params.get("data_inicio") or ""),
            data_fim=str(params.get("data_fim") or ""),
            forcar_resync=bool(params.get("forcar_resync")),
        )
        result = _run_async(vendas.sincronizar_vendas(req, client_id))
        started.append({"loja": loja, "result": result})
        _update_run(run_id, live_status=f"Sincronizacao de vendas iniciada para {loja}.", result={"started": started})
        if result and result.get("limit_reached"):
            pending.insert(0, loja)
            time.sleep(2)

    final_progress = _poll_progress_until_idle(run_id, "vendas_sync", client_id)
    return {"started": started, "final_progress": final_progress}


def _execute_vendas_cancel(run_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
    from backend.services import vendas

    client_id = str(proposal.get("client_id") or "default")
    result = _run_async(vendas.cancelar_sincronizacao_vendas(client_id))
    _update_run(run_id, live_status="Cancelamento de vendas solicitado.", result=result)
    return result


def _execute_estoque_sync(run_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
    from backend.schemas import EstoqueSyncRequest
    from backend.services import estoque

    client_id = str(proposal.get("client_id") or "default")
    params = proposal.get("params") or {}
    lojas = _expand_lojas(client_id, str(params.get("loja") or ""))
    if not lojas:
        raise RuntimeError("Nenhuma loja encontrada para atualizar estoque.")

    results: list[dict[str, Any]] = []
    for loja in lojas:
        while _progress_is_active(_progress_payload("estoque_sync", client_id)):
            _update_run(run_id, live_status="Aguardando estoque atual terminar antes da proxima loja.")
            time.sleep(2)
        req = EstoqueSyncRequest(loja=loja)
        result = _run_async(estoque.sincronizar_estoque(req, client_id))
        results.append({"loja": loja, "result": result})
        _update_run(run_id, live_status=f"Atualizacao de estoque iniciada para {loja}.", result={"lojas": results})
        _poll_progress_until_idle(run_id, "estoque_sync", client_id)
    return {"lojas": results, "final_progress": _progress_payload("estoque_sync", client_id)}


def _execute_estoque_lancamentos_sku(run_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
    from backend.schemas import EstoqueLancamentosSyncRequest
    from backend.services import estoque

    client_id = str(proposal.get("client_id") or "default")
    params = proposal.get("params") or {}
    req = EstoqueLancamentosSyncRequest(
        loja=str(params.get("loja") or ""),
        sku=str(params.get("sku") or ""),
        data_inicio=params.get("data_inicio"),
        data_fim=params.get("data_fim"),
    )
    result = _run_async(estoque.sincronizar_lancamentos_estoque_api(req, client_id))
    _update_run(run_id, result=result, live_status="Lancamentos de estoque por SKU sincronizados.")
    return result


def _execute_estoque_lancamentos_lote(run_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
    from backend.schemas import EstoqueLancamentosSyncLoteRequest
    from backend.services import estoque

    client_id = str(proposal.get("client_id") or "default")
    params = proposal.get("params") or {}
    req = EstoqueLancamentosSyncLoteRequest(
        loja=str(params.get("loja") or ""),
        data_inicio=params.get("data_inicio"),
        data_fim=params.get("data_fim"),
    )
    result = _run_async(estoque.sincronizar_lancamentos_estoque_lote_api(req, client_id))
    _update_run(run_id, result=result, live_status="Sincronizacao de lancamentos iniciada.")
    _poll_progress_until_idle(run_id, "estoque_lancamentos", client_id)
    return result


def _execute_ml_pergunta_responder(run_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
    from backend.schemas.perguntas_pos_venda import PerguntasEnviarRespostaRequest
    from backend.modules.perguntas_pos_venda.endpoints import api as perguntas_pos_venda_endpoints

    client_id = str(proposal.get("client_id") or "default")
    params = proposal.get("params") or {}
    req = PerguntasEnviarRespostaRequest(
        loja=str(params.get("loja") or ""),
        question_id=str(params.get("question_id") or ""),
        resposta=str(params.get("resposta") or params.get("texto") or ""),
        pergunta=params.get("pergunta") if isinstance(params.get("pergunta"), dict) else None,
        sku=str(params.get("sku") or ""),
        item_id=str(params.get("item_id") or ""),
    )
    result = perguntas_pos_venda_endpoints.ml_perguntas_responder_manual(req, client_id)
    _update_run(run_id, live_status="Resposta de pergunta Mercado Livre enviada.", result=result)
    return result


def _execute_ml_aprovacao_aprovar(run_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
    from backend.schemas.perguntas_pos_venda import PerguntasAprovacaoRequest
    from backend.modules.perguntas_pos_venda.endpoints import api as perguntas_pos_venda_endpoints

    client_id = str(proposal.get("client_id") or "default")
    params = proposal.get("params") or {}
    req = PerguntasAprovacaoRequest(
        approval_id=str(params.get("approval_id") or ""),
        resposta=params.get("resposta"),
        texto=params.get("texto"),
    )
    result = perguntas_pos_venda_endpoints.ml_perguntas_aprovacoes_aprovar(req, client_id)
    _update_run(run_id, live_status="Aprovacao Mercado Livre enviada.", result=result)
    return result


def _execute_internal_report_queue(run_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
    from backend.services import codex_assistant_storage, codex_reports_advanced
    from backend.services.codex.assistant import runtime as assistant_runtime

    params = proposal.get("params") if isinstance(proposal.get("params"), dict) else {}
    report_action = dict(params.get("report_action") or {}) if isinstance(params.get("report_action"), dict) else {}
    report_id = str(params.get("report_id") or report_action.get("report_id") or "").strip()
    spec_id = str((proposal.get("action") or {}).get("id") or "")
    expected_type = {
        "reports.queue_replenishment": "replenishment",
        "reports.queue_price_review": "price_review",
        "reports.queue_liquidation": "liquidation",
    }.get(spec_id)
    if not report_id or not report_action or not expected_type:
        raise RuntimeError("Relatorio ou acao interna ausente na proposta aprovada.")
    if str(report_action.get("action_type") or "") != expected_type:
        raise RuntimeError("O tipo da recomendacao nao corresponde a acao aprovada.")
    client_id = str(proposal.get("client_id") or "default")
    username = str(proposal.get("created_by") or proposal.get("username") or "")
    report = codex_assistant_storage.codex_assistant_report_get(assistant_runtime.info_base(), client_id, report_id)
    if not isinstance(report, dict):
        raise RuntimeError("Relatorio de origem nao encontrado.")
    valid_action = next(
        (
            item for item in (report.get("top_actions") or [])
            if isinstance(item, dict) and str(item.get("action_id") or "") == str(report_action.get("action_id") or "")
        ),
        None,
    )
    if not isinstance(valid_action, dict):
        raise RuntimeError("A recomendacao nao pertence ao relatorio informado.")
    if valid_action.get("queueable") is False:
        raise RuntimeError("A recomendacao nao possui confianca suficiente para entrar na fila.")
    queue_payload = {**valid_action, "report_id": report_id, "status": "queued", "approved_by": username}
    queue_item = codex_reports_advanced.create_queue_action(
        info_base=assistant_runtime.info_base(),
        client_id=client_id,
        username=username,
        payload=queue_payload,
    )
    result: dict[str, Any] = {"queue_action": queue_item, "external_mutation": False}
    if expected_type == "replenishment":
        linked = codex_reports_advanced.create_replenishment_list(
            info_base=assistant_runtime.info_base(),
            client_id=client_id,
            action=queue_payload,
            username=username,
        )
        queue_item = codex_reports_advanced.update_queue_action(
            info_base=assistant_runtime.info_base(),
            client_id=client_id,
            action_id=str(queue_item.get("action_id") or ""),
            username=username,
            updates={"linked_list_id": linked.get("list_id"), "result": linked},
        )
        result.update({"queue_action": queue_item, "replenishment_list": linked})
    _update_run(run_id, live_status="Fila interna criada sem alteracoes externas.", result=result)
    return result


def _render_route_path(path: str, path_params: dict[str, Any]) -> str:
    out = str(path or "")
    for name in re.findall(r"{([^}:]+)", out):
        if name not in path_params:
            raise RuntimeError(f"Parametro de rota ausente: {name}")
        out = re.sub(r"{%s(:[^}]+)?}" % re.escape(name), urllib.parse.quote(str(path_params[name])), out)
    return out


def _execute_generic_route(run_id: str, proposal: dict[str, Any], authorization: Optional[str]) -> dict[str, Any]:
    action = proposal.get("action") or {}
    params = proposal.get("params") or {}
    method = str(action.get("method") or "POST").upper()
    route_path = _render_route_path(str(action.get("route_path") or ""), params.get("path_params") or {})
    query = params.get("query") if isinstance(params.get("query"), dict) else {}
    if query:
        route_path += ("&" if "?" in route_path else "?") + urllib.parse.urlencode(query, doseq=True)
    body = params.get("body") if isinstance(params.get("body"), dict) else None
    bases = [
        os.getenv("JK_CODEX_ACTION_BASE_URL") or "",
        "http://127.0.0.1:8001",
        "http://localhost:8001",
        "http://127.0.0.1:8012",
        "http://localhost:8012",
    ]
    last_error = ""
    for base in [b.rstrip("/") for b in bases if str(b or "").strip()]:
        url = base + route_path
        data = None
        headers = {"Content-Type": "application/json"}
        if authorization:
            headers["Authorization"] = authorization
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read().decode("utf-8", errors="strict")
                parsed = json.loads(raw) if raw else {}
                _update_run(run_id, live_status=f"Rota executada: {method} {route_path}", result=parsed)
                return {"url": url, "status": resp.status, "response": parsed}
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="strict")
            last_error = f"HTTP {exc.code}: {body_text[:600]}"
            if exc.code not in {404, 405}:
                break
        except Exception as exc:
            last_error = str(exc)
    raise RuntimeError(last_error or "Falha ao executar rota generica.")


def _sync_task_from_action(proposal: dict[str, Any], run: dict[str, Any]) -> None:
    task_id = str(proposal.get("task_id") or "").strip()
    if not task_id:
        return
    try:
        from backend.services.codex.console import tasks as console_tasks

        status = str(run.get("status") or "")
        verification = run.get("verification") if isinstance(run.get("verification"), dict) else {}
        action = proposal.get("action") if isinstance(proposal.get("action"), dict) else {}
        label = str(action.get("label") or proposal.get("action_id") or "acao")
        if status == "completed":
            response = f"{label}: execucao concluida e verificada."
        elif status == "partial":
            response = f"{label}: execucao concluida, mas a verificacao retornou evidencias parciais."
        else:
            response = f"{label}: a execucao falhou. {str(run.get('error') or '').strip()}".strip()
        console_tasks.update(
            task_id,
            status=status if status in {"completed", "partial", "failed", "canceled"} else "running",
            completed_at=str(run.get("completed_at") or ""),
            final_response=response,
            live_status=str(run.get("live_status") or ""),
            error=str(run.get("error") or ""),
            verification=verification,
            action_run=run,
            agent_state="concluido" if status == "completed" else status if status in {"partial", "failed", "canceled"} else "executando",
            current_step="responder" if status in {"completed", "partial", "failed", "canceled"} else "executar",
        )
    except Exception:
        pass


def _execute_run_worker(run_id: str, proposal: dict[str, Any], authorization: Optional[str]) -> None:
    spec_id = str((proposal.get("action") or {}).get("id") or "")
    executor = str((proposal.get("action") or {}).get("executor") or "generic_route")
    try:
        _update_run(run_id, status="running", started_at=_now(), live_status="Executando acao.")
        if executor == "vendas_sync":
            result = _execute_vendas_sync(run_id, proposal)
        elif executor == "vendas_cancel":
            result = _execute_vendas_cancel(run_id, proposal)
        elif executor == "estoque_sync":
            result = _execute_estoque_sync(run_id, proposal)
        elif executor == "estoque_lancamentos_sku":
            result = _execute_estoque_lancamentos_sku(run_id, proposal)
        elif executor == "estoque_lancamentos_lote":
            result = _execute_estoque_lancamentos_lote(run_id, proposal)
        elif executor == "ml_pergunta_responder":
            result = _execute_ml_pergunta_responder(run_id, proposal)
        elif executor == "ml_aprovacao_aprovar":
            result = _execute_ml_aprovacao_aprovar(run_id, proposal)
        elif executor == "internal_report_queue":
            result = _execute_internal_report_queue(run_id, proposal)
        elif executor == "proposal_only":
            raise RuntimeError("Esta proposta ainda nao possui executor seguro. Revise os dados e crie um executor especifico antes de aprovar.")
        else:
            raise RuntimeError("Executor generico bloqueado. Esta funcao precisa de um adaptador seguro e testado.")
        _update_run(run_id, live_status="Verificando o resultado da acao.")
        plan_id = str(proposal.get("plan_id") or "")
        client_id = str(proposal.get("client_id") or "default")
        if plan_id:
            try:
                codex_agent_runtime.transition_plan(
                    _assistant_info_base(),
                    client_id,
                    plan_id,
                    "verificando",
                    current_step="verificar",
                    details={"run_id": run_id, "executor": executor},
                )
            except Exception:
                pass
        verification = codex_agent_runtime.verification_from_result(result, executor=executor)
        final_status = "completed" if verification.get("confirmed") else "partial"
        final_run = _update_run(
            run_id,
            status=final_status,
            completed_at=_now(),
            live_status="Acao concluida e verificada." if verification.get("confirmed") else "Acao concluida com verificacao parcial.",
            result=result,
            verification=verification,
            error="",
        )
        _sync_task_from_action(proposal, final_run)
        if plan_id:
            try:
                codex_agent_runtime.transition_plan(
                    _assistant_info_base(),
                    client_id,
                    plan_id,
                    "concluido" if verification.get("confirmed") else "parcial",
                    current_step="responder",
                    step_status="completed",
                    verification=verification,
                    details={"run_id": run_id},
                )
            except Exception:
                pass
    except Exception as exc:
        failed_run = _update_run(
            run_id,
            status="failed",
            completed_at=_now(),
            live_status="Acao falhou.",
            error=f"{spec_id}: {str(exc)}",
        )
        _sync_task_from_action(proposal, failed_run)
        plan_id = str(proposal.get("plan_id") or "")
        if plan_id:
            try:
                codex_agent_runtime.transition_plan(
                    _assistant_info_base(),
                    str(proposal.get("client_id") or "default"),
                    plan_id,
                    "falhou",
                    current_step="executar",
                    step_status="failed",
                    verification={"status": "failed", "confirmed": False, "error": str(exc)[:1000]},
                )
            except Exception:
                pass


def _proposal_expired(proposal: dict[str, Any]) -> bool:
    expires_at = str(proposal.get("expires_at") or "").strip()
    if not expires_at:
        return False
    try:
        return time.mktime(time.strptime(expires_at, "%Y-%m-%dT%H:%M:%SZ")) <= time.time()
    except Exception:
        return False


def approve_proposal(
    proposal_id: str,
    *,
    username: str,
    client_id: str,
    authorization: Optional[str],
    source: str = "app",
    wa_id: str = "",
    proposal_version: Optional[int] = None,
    proposal_hash: str = "",
) -> dict[str, Any]:
    proposal = _proposal_load(proposal_id, client_id)
    if not isinstance(proposal, dict):
        raise HTTPException(status_code=404, detail="Proposta Codex Action nao encontrada.")
    if (
        str(proposal.get("client_id") or "").strip() != str(client_id or "").strip()
        or str(proposal.get("created_by") or "").strip().lower() != str(username or "").strip().lower()
    ):
        raise HTTPException(status_code=404, detail="Proposta Codex Action nao encontrada.")
    if _proposal_expired(proposal):
        proposal["status"] = "expired"
        _proposal_save(proposal)
        raise HTTPException(status_code=409, detail="A proposta expirou. Gere uma nova confirmacao.")
    current_version = max(1, int(proposal.get("version") or 1))
    current_hash = codex_agent_runtime.proposal_hash(proposal)
    stored_hash = str(proposal.get("proposal_hash") or "")
    if stored_hash and not secrets.compare_digest(stored_hash, current_hash):
        raise HTTPException(status_code=409, detail="A proposta mudou desde a revisao. Gere uma nova confirmacao.")
    if proposal_version is not None and int(proposal_version) != current_version:
        raise HTTPException(status_code=409, detail="A versao confirmada nao e mais a versao atual da proposta.")
    if proposal_hash and not secrets.compare_digest(str(proposal_hash), current_hash):
        raise HTTPException(status_code=409, detail="O conteudo confirmado nao corresponde mais a proposta atual.")
    source = "whatsapp" if str(source or "").strip().lower() == "whatsapp" else "app"
    if source not in list(proposal.get("channels_allowed") or ["app"]):
        raise HTTPException(status_code=409, detail="Esta acao precisa ser confirmada no aplicativo.")
    if str(proposal.get("status") or "") not in {"awaiting_approval", "approved"}:
        raise HTTPException(status_code=400, detail="Proposta nao esta aguardando aprovacao.")
    action = proposal.get("action") if isinstance(proposal.get("action"), dict) else {}
    executor = str(action.get("executor") or "")
    if proposal.get("can_execute") is False or executor not in SAFE_EXECUTORS:
        raise HTTPException(
            status_code=400,
            detail="Esta proposta ainda nao possui executor seguro aprovado para execucao.",
        )

    idempotency_key = hashlib.sha256(
        f"{client_id}|{proposal_id}|{current_version}|{current_hash}".encode("utf-8", "replace")
    ).hexdigest()
    existing_run = codex_assistant_storage.codex_assistant_action_run_get(
        _assistant_info_base(),
        client_id,
        idempotency_key=idempotency_key,
    )
    if isinstance(existing_run, dict):
        return {"success": True, "proposal": proposal, "run": existing_run, "idempotent_replay": True}

    run_id = uuid.uuid4().hex
    proposal["status"] = "approved"
    proposal["approved_at"] = _now()
    proposal["approved_by"] = username
    proposal["run_id"] = run_id
    proposal["approved_source"] = source
    _proposal_save(proposal)

    wa_id_hash = hashlib.sha256(re.sub(r"\D", "", str(wa_id or "")).encode("utf-8")).hexdigest() if wa_id else ""
    codex_assistant_storage.codex_assistant_action_approval_save(
        _assistant_info_base(),
        client_id,
        {
            "approval_id": uuid.uuid4().hex,
            "proposal_id": proposal_id,
            "proposal_version": current_version,
            "proposal_hash": current_hash,
            "status": "approved",
            "source": source,
            "actor": username,
            "wa_id_hash": wa_id_hash,
        },
    )

    run = {
        "run_id": run_id,
        "proposal_id": proposal_id,
        "status": "queued",
        "action": proposal.get("action"),
        "params": proposal.get("params"),
        "site_context": proposal.get("site_context") or {},
        "created_at": _now(),
        "updated_at": _now(),
        "created_by": username,
        "client_id": client_id,
        "live_status": "Acao aprovada e enfileirada.",
        "progress": None,
        "logs": [],
        "result": None,
        "error": "",
        "idempotency_key": idempotency_key,
        "proposal_version": current_version,
        "proposal_hash": current_hash,
        "verification": {},
    }
    _run_save(run)
    plan_id = str(proposal.get("plan_id") or "")
    if plan_id:
        try:
            codex_agent_runtime.transition_plan(
                _assistant_info_base(),
                client_id,
                plan_id,
                "executando",
                current_step="executar",
                details={"proposal_id": proposal_id, "run_id": run_id},
            )
        except Exception:
            pass
    codex_agent_runtime.audit(
        _assistant_info_base(),
        client_id,
        event_type="proposal_approved",
        entity_type="proposal",
        entity_id=proposal_id,
        actor=username,
        channel=source,
        payload={"version": current_version, "proposal_hash": current_hash, "run_id": run_id},
    )
    try:
        from backend.services import codex_operational_memory

        codex_operational_memory.add_memory(
            client_id,
            category="decisoes",
            content=f"Acao aprovada: {proposal.get('summary') or (action.get('label') or action.get('id') or 'acao Codex')}",
            source="codex_action_approval",
            metadata={
                "proposal_id": proposal_id,
                "run_id": run_id,
                "action_id": action.get("id"),
                "approved_by": username,
                "risk": proposal.get("risk") or action.get("risk_level"),
            },
            importance=4,
        )
    except Exception:
        pass
    thread = threading.Thread(target=_execute_run_worker, args=(run_id, proposal, authorization), daemon=True)
    thread.start()
    return {"success": True, "proposal": proposal, "run": get_run(run_id, client_id=client_id).get("run")}


def reject_proposal(proposal_id: str, *, username: str, client_id: str, source: str = "app") -> dict[str, Any]:
    proposal = _proposal_load(proposal_id, client_id)
    if not isinstance(proposal, dict):
        raise HTTPException(status_code=404, detail="Proposta Codex Action nao encontrada.")
    if (
        str(proposal.get("client_id") or "").strip() != str(client_id or "").strip()
        or str(proposal.get("created_by") or "").strip().lower() != str(username or "").strip().lower()
    ):
        raise HTTPException(status_code=404, detail="Proposta Codex Action nao encontrada.")
    status = str(proposal.get("status") or "")
    if status == "rejected":
        return {"success": True, "proposal": proposal}
    if status != "awaiting_approval":
        raise HTTPException(status_code=400, detail="Proposta nao esta aguardando aprovacao.")
    proposal.update(
        {
            "status": "rejected",
            "rejected_at": _now(),
            "rejected_by": str(username or ""),
            "rejection_source": str(source or "app")[:40],
        }
    )
    _proposal_save(proposal)
    codex_assistant_storage.codex_assistant_action_approval_save(
        _assistant_info_base(),
        client_id,
        {
            "approval_id": uuid.uuid4().hex,
            "proposal_id": proposal_id,
            "proposal_version": max(1, int(proposal.get("version") or 1)),
            "proposal_hash": str(proposal.get("proposal_hash") or ""),
            "status": "rejected",
            "source": str(source or "app"),
            "actor": username,
        },
    )
    if proposal.get("plan_id"):
        try:
            codex_agent_runtime.transition_plan(
                _assistant_info_base(),
                client_id,
                str(proposal.get("plan_id")),
                "cancelado",
                current_step="aprovar",
                step_status="canceled",
            )
        except Exception:
            pass
    return {"success": True, "proposal": proposal}


def revise_proposal(
    proposal_id: str,
    *,
    username: str,
    client_id: str,
    params: Optional[dict[str, Any]] = None,
    message: str = "",
    source: str = "app",
) -> dict[str, Any]:
    proposal = _proposal_load(proposal_id, client_id)
    if not isinstance(proposal, dict):
        raise HTTPException(status_code=404, detail="Proposta Codex Action nao encontrada.")
    if (
        str(proposal.get("client_id") or "") != str(client_id or "")
        or str(proposal.get("created_by") or "").strip().lower() != str(username or "").strip().lower()
    ):
        raise HTTPException(status_code=404, detail="Proposta Codex Action nao encontrada.")
    if str(proposal.get("status") or "") not in {"awaiting_approval", "expired", "rejected"}:
        raise HTTPException(status_code=409, detail="Uma proposta em execucao ou concluida nao pode ser revisada.")
    if isinstance(params, dict):
        proposal["params"] = {**dict(proposal.get("params") or {}), **params}
    if message:
        proposal["message"] = str(message)[:4000]
    proposal["version"] = max(1, int(proposal.get("version") or 1)) + 1
    proposal["status"] = "awaiting_approval"
    proposal["expires_at"] = _future(24)
    proposal.pop("approved_at", None)
    proposal.pop("approved_by", None)
    proposal.pop("run_id", None)
    proposal["proposal_hash"] = codex_agent_runtime.proposal_hash(proposal)
    saved = _proposal_save(proposal)
    codex_agent_runtime.audit(
        _assistant_info_base(),
        client_id,
        event_type="proposal_revised",
        entity_type="proposal",
        entity_id=proposal_id,
        actor=username,
        channel=str(source or "app"),
        payload={"version": saved.get("version"), "proposal_hash": saved.get("proposal_hash")},
    )
    return {"success": True, "proposal": saved}


def list_proposals(
    *,
    client_id: str,
    username: str,
    status: str = "awaiting_approval",
    limit: int = 100,
) -> dict[str, Any]:
    proposals = codex_assistant_storage.codex_assistant_action_proposal_list(
        _assistant_info_base(),
        client_id,
        status=str(status or ""),
        created_by=str(username or ""),
        limit=limit,
    )
    return {"success": True, "proposals": proposals, "total": len(proposals)}


def get_run(run_id: str, *, client_id: str = "") -> dict[str, Any]:
    run = _run_load(run_id, client_id)
    if not isinstance(run, dict):
        raise HTTPException(status_code=404, detail="Execucao Codex Action nao encontrada.")
    action = run.get("action") if isinstance(run.get("action"), dict) else {}
    kind = str(action.get("status_kind") or "")
    client_id = str(run.get("client_id") or "default")
    if run.get("status") == "running" and kind:
        progress = _progress_payload(kind, client_id)
        run = _update_run(
            run_id,
            _client_id=client_id,
            progress=progress.get("progress"),
            logs=list(progress.get("logs") or [])[-120:],
            sync_meta=progress.get("sync_meta"),
            result_preview=progress,
        )
    return {"success": True, "run": run}


def cancel_run(run_id: str, *, client_id: str = "") -> dict[str, Any]:
    run = get_run(run_id, client_id=client_id).get("run") or {}
    if str(run.get("status") or "") in {"completed", "partial", "failed", "canceled"}:
        return {"success": True, "run": run}
    action = run.get("action") if isinstance(run.get("action"), dict) else {}
    kind = str(action.get("status_kind") or "")
    client_id = str(run.get("client_id") or "default")
    try:
        if kind == "vendas_sync":
            from backend.services import vendas

            _run_async(vendas.cancelar_sincronizacao_vendas(client_id))
        elif kind == "estoque_sync":
            from backend.services import estoque_context

            estoque_context.ESTOQUE_SYNC_CANCEL_FLAGS[client_id] = True
        elif kind == "estoque_lancamentos":
            from backend.services import estoque_context

            estoque_context.ESTOQUE_LANC_SYNC_ACTIVE.pop(client_id, None)
    except Exception:
        pass
    run = _update_run(run_id, _client_id=client_id, status="cancel_requested", live_status="Cancelamento solicitado.")
    return {"success": True, "run": run}


configure_codex_actions_runtime()


__all__ = [
    "CodexActionSpec",
    "configure_codex_actions_runtime",
    "list_actions",
    "list_program_functions",
    "match_action_dry_run",
    "create_proposal",
    "approve_proposal",
    "reject_proposal",
    "revise_proposal",
    "list_proposals",
    "get_run",
    "cancel_run",
]
