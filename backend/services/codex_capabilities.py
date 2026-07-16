"""Unified capability registry for Black Jhon."""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Optional

from backend.services.runtime_bridge import bind_runtime_globals


CAPABILITY_REGISTRY_VERSION = "20260624-capabilities-v2-readonly-sources"
CAPABILITY_CATEGORIES = {
    "consultar",
    "diagnosticar",
    "gerar_relatorio",
    "preparar_acao",
    "executar_acao_aprovada",
}

MODULE_LABELS = {
    "vendas": "Vendas e pedidos",
    "vendas_devolucoes": "Vendas e devolucoes",
    "estoque": "Estoque e ruptura",
    "cadastro": "Cadastro/produtos/custos/fotos",
    "produtos": "Cadastro/produtos/custos/fotos",
    "mercado_livre": "Mercado Livre/anuncios/perguntas",
    "marketplaces": "Mercado Livre/anuncios/perguntas",
    "bling": "Bling/produtos/estoque/fiscal/pedidos",
    "fiscal": "Impostos/fiscal",
    "impostos": "Impostos/fiscal",
    "margem": "Simulador/preco/margem",
    "full": "Full",
    "favoritos": "Favoritos ML",
    "etiquetas": "Etiquetas",
    "importacoes": "Importacoes",
    "integracoes": "Integracoes/sync",
    "sistema": "Usuarios/configuracoes",
    "relatorios": "Relatorios e diagnosticos",
}


def configure_codex_capabilities_runtime(runtime_module=None):
    return bind_runtime_globals(globals(), runtime_module)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _base_dir() -> str:
    base = str(globals().get("BASE_DIR") or os.getcwd()).strip()
    return os.path.abspath(base or os.getcwd())


def _info_root() -> Path:
    base_info = str(globals().get("PASTA_INFO") or os.path.join(_base_dir(), "info")).strip()
    if not os.path.isabs(base_info):
        base_info = os.path.join(_base_dir(), base_info)
    root = Path(base_info) / "codex_console"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _registry_path() -> Path:
    return _info_root() / "capabilities.json"


def _safe_id(value: Any, fallback: str = "capability") -> str:
    text = str(value or "").strip().lower()
    try:
        text = text.encode("latin1").decode("utf-8")
    except Exception:
        pass
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9_.-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._-")
    return (text or fallback)[:120]


def _norm(value: Any) -> str:
    text = str(value or "").lower()
    try:
        text = text.encode("latin1").decode("utf-8")
    except Exception:
        pass
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


def _schema_required(schema: Any) -> list[str]:
    if isinstance(schema, dict):
        required = schema.get("required")
        if isinstance(required, list):
            return [str(item or "") for item in required if str(item or "").strip()]
    return []


def _module_group(module: Any) -> str:
    module_id = str(module or "sistema").strip() or "sistema"
    return MODULE_LABELS.get(module_id, module_id.replace("_", " ").title())


def _capability(
    *,
    cid: str,
    module: str,
    category: str,
    title: str,
    description: str,
    intent_examples: Optional[list[str]] = None,
    input_schema: Optional[dict[str, Any]] = None,
    required_params: Optional[list[str]] = None,
    read_only: bool = True,
    mutating: bool = False,
    requires_approval: bool = False,
    executor_type: str = "data_tool",
    executor_id: str = "",
    route_path: str = "",
    method: str = "",
    sources: Optional[list[str]] = None,
    status_text: str = "",
    fallback_capabilities: Optional[list[str]] = None,
    output_summary: str = "",
) -> dict[str, Any]:
    category = category if category in CAPABILITY_CATEGORIES else "consultar"
    input_schema = input_schema or {}
    required = required_params if isinstance(required_params, list) else _schema_required(input_schema)
    return {
        "id": _safe_id(cid),
        "module": str(module or "sistema"),
        "module_group": _module_group(module),
        "category": category,
        "title": str(title or cid).strip(),
        "description": str(description or "").strip(),
        "intent_examples": [str(item or "").strip() for item in (intent_examples or []) if str(item or "").strip()],
        "input_schema": input_schema,
        "required_params": required,
        "read_only": bool(read_only),
        "mutating": bool(mutating),
        "requires_approval": bool(requires_approval),
        "executor_type": str(executor_type or ""),
        "executor_id": str(executor_id or ""),
        "route_path": str(route_path or ""),
        "method": str(method or ""),
        "sources": [str(item or "").strip() for item in (sources or []) if str(item or "").strip()],
        "status_text": str(status_text or "").strip(),
        "fallback_capabilities": [str(item or "").strip() for item in (fallback_capabilities or []) if str(item or "").strip()],
        "output_summary": str(output_summary or "").strip(),
    }


def _manual_capabilities() -> list[dict[str, Any]]:
    period_schema = {"data_inicio": "YYYY-MM-DD", "data_fim": "YYYY-MM-DD", "loja": "nome da loja/conta"}
    return [
        _capability(
            cid="consultar_vendas_periodo",
            module="vendas",
            category="consultar",
            title="Consultar vendas por periodo",
            description="Consulta pedidos, SKUs vendidos, quantidade, valor e loja em um periodo.",
            intent_examples=["vendas dos ultimos 30 dias", "ranking da JK Pecas", "listar pedidos por SKU"],
            input_schema={**period_schema, "sku": "SKU opcional", "limite": 50},
            read_only=True,
            executor_type="data_tool",
            executor_id="sales_ranking",
            sources=["vendas_historico", "sales_returns_query"],
            status_text="consultando vendas",
            fallback_capabilities=["sales_returns_query", "bling_sales_orders"],
            output_summary="Ranking e resumo de vendas por SKU.",
        ),
        _capability(
            cid="diagnosticar_ruptura_estoque",
            module="estoque",
            category="diagnosticar",
            title="Diagnosticar ruptura de estoque",
            description="Cruza vendas recentes com saldo de loja para apontar SKUs em risco de acabar.",
            intent_examples=["verificar ruptura", "estoque acabando", "quais SKUs preciso repor"],
            input_schema={"loja": "nome da loja/conta", "lookback_days": 30, "limite": 100},
            read_only=True,
            executor_type="data_tool",
            executor_id="stockout_forecast",
            sources=["estoque local", "vendas locais"],
            status_text="diagnosticando ruptura",
            fallback_capabilities=["stock_data", "sales_ranking"],
            output_summary="SKUs com risco, saldo considerado, media/dia e acao recomendada.",
        ),
        _capability(
            cid="gerar_relatorio_vendas_estoque",
            module="relatorios",
            category="gerar_relatorio",
            title="Gerar relatorio de vendas, estoque e margem",
            description="Monta relatorio gerencial com vendas, devolucoes, ruptura, estoque parado, custo e margem.",
            intent_examples=["gere relatorio dos ultimos 30 dias", "analise a JK Pecas", "relatorio de gestao"],
            input_schema={**period_schema, "formato": "chat|html|xlsx|pdf"},
            read_only=True,
            executor_type="report",
            executor_id="codex_assistant_report_create",
            sources=["CODEX_DATA_TOOLS", "cadastro", "vendas", "estoque"],
            status_text="gerando relatorio",
            fallback_capabilities=["sales_ranking", "stockout_forecast", "product_costs_and_margin"],
            output_summary="Relatorio exibido no chat e salvo em arquivo.",
        ),
        _capability(
            cid="preparar_resposta_pergunta_ml",
            module="mercado_livre",
            category="preparar_acao",
            title="Preparar resposta para pergunta do Mercado Livre",
            description="Le pergunta aberta, contexto da tela e dados do produto para sugerir uma resposta sem enviar.",
            intent_examples=["responda essa pergunta do Mercado Livre", "crie resposta para a pergunta aberta", "o que responder ao comprador"],
            input_schema={"pergunta_id": "ID opcional", "texto": "texto da pergunta opcional", "loja": "nome da loja"},
            read_only=True,
            executor_type="screen_helper",
            executor_id="program_action_match",
            sources=["tela atual", "perguntas pos-venda", "cadastro"],
            status_text="preparando resposta ML",
            fallback_capabilities=["operational_dispatcher", "product_data"],
            output_summary="Resposta sugerida e parametros para aprovacao.",
        ),
        _capability(
            cid="executar_resposta_pergunta_ml",
            module="mercado_livre",
            category="executar_acao_aprovada",
            title="Enviar resposta aprovada ao Mercado Livre",
            description="Envia uma resposta ao Mercado Livre somente apos confirmacao explicita do usuario.",
            intent_examples=["enviar resposta ao Mercado Livre", "publique essa resposta", "responder pergunta agora"],
            input_schema={"pergunta_id": "ID da pergunta", "resposta": "texto aprovado", "loja": "nome da loja"},
            read_only=False,
            mutating=True,
            requires_approval=True,
            executor_type="approved_action",
            executor_id="",
            sources=["Mercado Livre"],
            status_text="aguardando aprovacao para responder ML",
            fallback_capabilities=["preparar_resposta_pergunta_ml", "program_action_match"],
            output_summary="Acao externa mutavel. Se nao houver executor aprovado, deve retornar proposta incompleta.",
        ),
        _capability(
            cid="consultar_saldo_bling",
            module="bling",
            category="consultar",
            title="Consultar saldo na Bling",
            description="Consulta saldo em estoque na Bling por SKU, produto ou deposito, em modo read-only.",
            intent_examples=["saldo do SKU 124 na Bling", "estoque por deposito", "saldo direto na Bling"],
            input_schema={"mensagem": "SKU, codigo ou produto", "loja": "nome da loja", "limite": 50},
            read_only=True,
            executor_type="data_tool",
            executor_id="bling_stock_balances",
            sources=["Bling v3"],
            status_text="consultando saldo Bling",
            fallback_capabilities=["bling_products", "stock_data"],
            output_summary="Saldos retornados pela Bling e diagnostico quando vazio.",
        ),
        _capability(
            cid="diagnosticar_logs_sincronizacao",
            module="integracoes",
            category="diagnosticar",
            title="Diagnosticar logs e estado de sincronizacao",
            description="Consulta estados, logs e caches read-only para explicar vendas, estoque ou integracoes ausentes.",
            intent_examples=["por que nao aparecem vendas", "erro de sincronizacao", "ver logs de sync", "status do sync da JK Pecas"],
            input_schema={"mensagem": "erro, loja, periodo ou modulo", "limite": 50},
            read_only=True,
            executor_type="data_tool",
            executor_id="sync_logs_query",
            sources=["sync_logs_query", "integrations_status", "source_discovery"],
            status_text="consultando logs de sincronizacao",
            fallback_capabilities=["integrations_status", "source_discovery", "local_cache_query"],
            output_summary="Estados e logs encontrados, fontes tentadas e possiveis causas.",
        ),
        _capability(
            cid="preparar_sync_bling",
            module="integracoes",
            category="preparar_acao",
            title="Preparar sincronizacao via Bling",
            description="Identifica o tipo de sincronizacao solicitado, periodo, loja e impacto antes da aprovacao.",
            intent_examples=["sincronizar Bling", "baixar vendas", "atualizar estoque pela Bling"],
            input_schema={**period_schema, "tipo": "vendas|estoque|lancamentos"},
            read_only=True,
            executor_type="approved_action",
            executor_id="vendas.sync_periodo",
            sources=["codex_actions", "Bling"],
            status_text="preparando sincronizacao",
            fallback_capabilities=["vendas.sync_periodo", "estoque.sync_bling_to_jk"],
            output_summary="Proposta de sincronizacao com parametros e riscos.",
        ),
        _capability(
            cid="executar_sync_bling",
            module="integracoes",
            category="executar_acao_aprovada",
            title="Executar sincronizacao aprovada",
            description="Executa sincronizacao de vendas ou estoque depois da aprovacao do usuario.",
            intent_examples=["execute a sincronizacao", "confirmar sync", "sincronize vendas da JK Pecas"],
            input_schema={**period_schema, "tipo": "vendas|estoque|lancamentos"},
            read_only=False,
            mutating=True,
            requires_approval=True,
            executor_type="approved_action",
            executor_id="vendas.sync_periodo",
            sources=["codex_actions", "Bling"],
            status_text="aguardando aprovacao para sincronizar",
            fallback_capabilities=["preparar_sync_bling"],
            output_summary="Job de sincronizacao acompanhado no painel.",
        ),
        _capability(
            cid="gerar_etiqueta_produto",
            module="etiquetas",
            category="preparar_acao",
            title="Gerar etiqueta de produto",
            description="Prepara geracao de etiqueta usando SKU, marketplace ou dados de produto.",
            intent_examples=["gerar etiqueta", "etiqueta do SKU", "criar etiqueta Mercado Livre"],
            input_schema={"sku": "SKU ou lista de SKUs", "loja": "nome da loja", "modelo": "modelo opcional"},
            read_only=False,
            mutating=True,
            requires_approval=True,
            executor_type="approved_action",
            executor_id="",
            sources=["etiquetas", "cadastro", "marketplaces"],
            status_text="preparando etiqueta",
            fallback_capabilities=["product_data", "program_action_match"],
            output_summary="Etiqueta ou proposta de geracao quando houver executor.",
        ),
        _capability(
            cid="calcular_preco_margem",
            module="margem",
            category="diagnosticar",
            title="Calcular preco e margem",
            description="Usa custo, imposto, frete e tarifa para simular margem por SKU seguindo a logica do Favoritos.",
            intent_examples=["calcular margem", "simular preco", "lucro do SKU"],
            input_schema={"sku": "SKU", "preco": "preco de venda opcional", "loja": "nome da loja"},
            read_only=True,
            executor_type="data_tool",
            executor_id="product_costs_and_margin",
            sources=["cadastro_custos_lojas", "cadastro_produtos", "Favoritos ML"],
            status_text="calculando margem",
            fallback_capabilities=["product_margin", "mercado_livre_listing"],
            output_summary="Custo, imposto, tarifa, frete, lucro estimado e status da margem.",
        ),
    ]


def _category_for_tool(tool: dict[str, Any]) -> str:
    tid = _norm(tool.get("id"))
    text = _norm(" ".join(str(tool.get(key) or "") for key in ("id", "module", "description", "status")))
    if "report" in tid or "relatorio" in text:
        return "gerar_relatorio"
    if any(word in text for word in ("diagnost", "ruptura", "anomalia", "comparacao", "margem", "lucro", "taxa")):
        return "diagnosticar"
    return "consultar"


def _capabilities_from_tools() -> list[dict[str, Any]]:
    try:
        from backend.services import codex_assistant

        # O catalogo interno precisa conhecer todas as capacidades. A filtragem
        # por usuario ocorre antes de expor/enviar o catalogo ao agente.
        tools = codex_assistant._assistant_tools_public({"full": True})
    except Exception:
        tools = []
    caps: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tid = str(tool.get("id") or "").strip()
        if not tid or tid in {"capability_resolve"}:
            continue
        caps.append(
            _capability(
                cid=tid,
                module=str(tool.get("module") or "sistema"),
                category=_category_for_tool(tool),
                title=str(tool.get("description") or tid).strip()[:120],
                description=str(tool.get("description") or ""),
                intent_examples=list(tool.get("intent_examples") or []),
                input_schema=tool.get("input_schema") if isinstance(tool.get("input_schema"), dict) else {},
                read_only=True,
                mutating=False,
                requires_approval=False,
                executor_type="data_tool",
                executor_id=tid,
                sources=[str(tool.get("executor") or tid)],
                status_text=str(tool.get("status") or tool.get("module") or tid),
                fallback_capabilities=list(tool.get("fallbacks") or []),
                output_summary=", ".join(str(item) for item in (tool.get("output_fields") or [])[:8]),
            )
        )
    return caps


def _capabilities_from_actions() -> list[dict[str, Any]]:
    try:
        from backend.services import codex_actions

        payload = codex_actions.list_actions()
        actions = payload.get("actions") if isinstance(payload, dict) else []
    except Exception:
        actions = []
    caps: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        action_id = str(action.get("id") or "").strip()
        if not action_id:
            continue
        label = str(action.get("label") or action_id).strip()
        caps.append(
            _capability(
                cid=action_id,
                module=str(action.get("module") or "sistema"),
                category="executar_acao_aprovada",
                title=label,
                description=label,
                intent_examples=list(action.get("aliases") or []),
                input_schema=action.get("params_schema") if isinstance(action.get("params_schema"), dict) else {},
                read_only=False,
                mutating=True,
                requires_approval=True,
                executor_type="approved_action",
                executor_id=action_id,
                route_path=str(action.get("route_path") or ""),
                method=str(action.get("method") or ""),
                sources=["codex_actions"],
                status_text="aguardando aprovacao",
                fallback_capabilities=["program_action_match"],
                output_summary="Acao aprovada: " + "; ".join(str(item) for item in (action.get("side_effects") or [])[:3]),
            )
        )
    return caps


def _capabilities_from_program_inventory(client_id: str = "", limit: int = 2000) -> list[dict[str, Any]]:
    try:
        from backend.services import codex_actions

        payload = codex_actions.list_program_functions(client_id=client_id, limit=limit)
    except Exception:
        payload = {}
    caps: list[dict[str, Any]] = []
    rows = []
    if isinstance(payload, dict):
        for key in ("routes", "pages"):
            value = payload.get(key)
            if isinstance(value, list):
                rows.extend(value)
    for item in rows[:limit]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "")
        item_id = str(item.get("id") or "").strip()
        if not item_id:
            continue
        read_only = bool(item.get("read_only", True))
        mutating = bool(item.get("mutating"))
        if kind == "route":
            executor_type = "route"
            category = "consultar" if read_only else "executar_acao_aprovada"
        elif kind == "page":
            executor_type = "screen_helper"
            category = "consultar"
        else:
            continue
        caps.append(
            _capability(
                cid=item_id,
                module=str(item.get("module") or "sistema"),
                category=category,
                title=str(item.get("name") or item.get("summary") or item_id),
                description=str(item.get("summary") or ""),
                intent_examples=[str(item.get("path") or ""), str(item.get("name") or "")],
                input_schema={},
                read_only=read_only,
                mutating=mutating,
                requires_approval=bool(item.get("requires_approval")),
                executor_type=executor_type,
                executor_id=item_id,
                route_path=str(item.get("path") or ""),
                method=str(item.get("method") or ""),
                sources=[str(item.get("source") or kind)],
                status_text="consultando tela/rota" if read_only else "aguardando aprovacao",
                fallback_capabilities=[],
                output_summary=str(item.get("summary") or ""),
            )
        )
    return caps


def _dedupe_capabilities(caps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for cap in caps:
        if not isinstance(cap, dict):
            continue
        cid = str(cap.get("id") or "").strip()
        if not cid:
            continue
        existing = by_id.get(cid)
        if not existing:
            by_id[cid] = cap
            continue
        # Manual/data-tool entries usually have better wording than raw route entries.
        if existing.get("executor_type") == "route" and cap.get("executor_type") != "route":
            by_id[cid] = cap
    return sorted(by_id.values(), key=lambda item: (str(item.get("module") or ""), str(item.get("category") or ""), str(item.get("title") or "")))


def _filter_capabilities(caps: list[dict[str, Any]], query: str = "", module: str = "", category: str = "", limit: int = 300) -> list[dict[str, Any]]:
    query_norm = _norm(query)
    module_norm = _norm(module)
    category = str(category or "").strip()

    def match(cap: dict[str, Any]) -> bool:
        if category and str(cap.get("category") or "") != category:
            return False
        if module_norm:
            cap_module = _norm(cap.get("module") or "")
            cap_group = _norm(cap.get("module_group") or "")
            if module_norm not in cap_module and module_norm not in cap_group and cap_module not in module_norm:
                return False
        if not query_norm:
            return True
        haystack = _norm(
            " ".join(
                [
                    str(cap.get("id") or ""),
                    str(cap.get("module") or ""),
                    str(cap.get("module_group") or ""),
                    str(cap.get("category") or ""),
                    str(cap.get("title") or ""),
                    str(cap.get("description") or ""),
                    str(cap.get("executor_id") or ""),
                    " ".join(str(item or "") for item in cap.get("intent_examples") or []),
                ]
            )
        )
        tokens = [token for token in query_norm.split() if len(token) >= 2]
        return all(token in haystack for token in tokens[:8])

    return [cap for cap in caps if match(cap)][: max(1, min(int(limit or 300), 2000))]


def build_capabilities(client_id: str = "", limit_inventory: int = 2000) -> list[dict[str, Any]]:
    caps = []
    caps.extend(_manual_capabilities())
    caps.extend(_capabilities_from_tools())
    caps.extend(_capabilities_from_actions())
    caps.extend(_capabilities_from_program_inventory(client_id, limit_inventory))
    return _dedupe_capabilities(caps)


def _modules_payload(caps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    modules: dict[str, dict[str, Any]] = {}
    for cap in caps:
        module = str(cap.get("module") or "sistema")
        bucket = modules.setdefault(
            module,
            {
                "module": module,
                "label": _module_group(module),
                "total": 0,
                "consultar": 0,
                "diagnosticar": 0,
                "gerar_relatorio": 0,
                "preparar_acao": 0,
                "executar_acao_aprovada": 0,
            },
        )
        bucket["total"] += 1
        category = str(cap.get("category") or "consultar")
        if category in CAPABILITY_CATEGORIES:
            bucket[category] += 1
    return sorted(modules.values(), key=lambda item: (str(item.get("label") or ""), str(item.get("module") or "")))


def _payload(client_id: str, caps: list[dict[str, Any]], query: str = "", module: str = "", category: str = "", limit: int = 300) -> dict[str, Any]:
    filtered = _filter_capabilities(caps, query, module, category, limit)
    modules = _modules_payload(caps)
    totals = {category_id: 0 for category_id in sorted(CAPABILITY_CATEGORIES)}
    for cap in caps:
        category_id = str(cap.get("category") or "consultar")
        if category_id in totals:
            totals[category_id] += 1
    totals["all"] = len(caps)
    return {
        "success": True,
        "version": CAPABILITY_REGISTRY_VERSION,
        "client_id": str(client_id or ""),
        "query": str(query or ""),
        "module_filter": str(module or ""),
        "category_filter": str(category or ""),
        "capabilities": filtered,
        "modules": modules,
        "categories": sorted(CAPABILITY_CATEGORIES),
        "totals": totals,
        "generated_at": _now(),
        "safety_model": {
            "read_only_runs_automatically": True,
            "mutating_requires_approval": True,
            "external_or_destructive_requires_approval": True,
        },
    }


def list_capabilities(
    *,
    client_id: str = "",
    query: str = "",
    module: str = "",
    category: str = "",
    limit: int = 300,
) -> dict[str, Any]:
    caps = build_capabilities(client_id)
    payload = _payload(client_id, caps, query, module, category, limit)
    try:
        with _registry_path().open("w", encoding="utf-8") as fh:
            json.dump(_payload(client_id, caps, "", "", "", 2000), fh, ensure_ascii=False, indent=2, default=str)
    except Exception:
        pass
    return payload


def list_modules(*, client_id: str = "") -> dict[str, Any]:
    caps = build_capabilities(client_id)
    modules = _modules_payload(caps)
    return {
        "success": True,
        "version": CAPABILITY_REGISTRY_VERSION,
        "client_id": str(client_id or ""),
        "modules": modules,
        "total_modules": len(modules),
        "total_capabilities": len(caps),
        "generated_at": _now(),
    }


def get_capability(*, client_id: str = "", capability_id: str = "") -> Optional[dict[str, Any]]:
    wanted = _safe_id(capability_id)
    for cap in build_capabilities(client_id):
        if str(cap.get("id") or "") == wanted:
            return cap
    return None


def _score_capability(cap: dict[str, Any], query: str) -> int:
    query_norm = _norm(query)
    if not query_norm:
        return 0
    haystack = _norm(
        " ".join(
            [
                str(cap.get("id") or ""),
                str(cap.get("title") or ""),
                str(cap.get("description") or ""),
                str(cap.get("module") or ""),
                str(cap.get("module_group") or ""),
                str(cap.get("category") or ""),
                str(cap.get("executor_id") or ""),
                " ".join(str(item or "") for item in cap.get("intent_examples") or []),
            ]
        )
    )
    score = 0
    for token in [item for item in query_norm.split() if len(item) >= 2][:12]:
        if token in haystack:
            score += 3 if token in _norm(cap.get("title") or "") else 1
    if query_norm in haystack:
        score += 8
    for example in cap.get("intent_examples") or []:
        ex_norm = _norm(example)
        if ex_norm and (ex_norm in query_norm or query_norm in ex_norm):
            score += 18
    return score


def resolve_capability(
    *,
    client_id: str = "",
    message: str = "",
    capability_id: str = "",
    module: str = "",
    category: str = "",
    params: Optional[dict[str, Any]] = None,
    limit: int = 8,
) -> dict[str, Any]:
    caps = build_capabilities(client_id)
    selected: Optional[dict[str, Any]] = None
    if capability_id:
        selected = next((cap for cap in caps if str(cap.get("id") or "") == _safe_id(capability_id)), None)
    candidates = _filter_capabilities(caps, message, module, category, 80) if message or module or category else caps
    if message:
        scored_all = [
            (cap, _score_capability(cap, message))
            for cap in caps
            if (not category or str(cap.get("category") or "") == category)
            and (not module or _norm(module) in _norm(cap.get("module") or "") or _norm(module) in _norm(cap.get("module_group") or ""))
        ]
        ranked = [cap for cap, score in sorted(scored_all, key=lambda item: item[1], reverse=True) if score > 0][:80]
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for cap in [*candidates, *ranked]:
            cid = str(cap.get("id") or "")
            if cid and cid not in seen:
                seen.add(cid)
                merged.append(cap)
        candidates = merged
    if selected and selected not in candidates:
        candidates.insert(0, selected)
    if not selected and candidates:
        scored = [(cap, _score_capability(cap, message)) for cap in candidates]
        selected = sorted(scored, key=lambda item: item[1], reverse=True)[0][0] if message else candidates[0]
    params = params if isinstance(params, dict) else {}
    missing = []
    if selected:
        for key in selected.get("required_params") or []:
            if params.get(key) in (None, ""):
                missing.append(key)
    return {
        "success": True,
        "matched": bool(selected),
        "capability": selected or {},
        "candidates": candidates[: max(1, min(int(limit or 8), 20))],
        "missing_params": missing,
        "needs_input": bool(missing),
        "message": (
            "Capacidade encontrada."
            if selected and not missing
            else "Capacidade encontrada, mas faltam parametros: " + ", ".join(missing)
            if selected
            else "Nenhuma capacidade compativel foi encontrada."
        ),
        "generated_at": _now(),
    }


def compact_capability_catalog(*, client_id: str = "", limit: int = 120, read_only_only: bool = False) -> dict[str, Any]:
    caps = build_capabilities(client_id)
    if read_only_only:
        caps = [
            cap
            for cap in caps
            if cap.get("read_only") is True
            and not bool(cap.get("mutating"))
            and not bool(cap.get("requires_approval"))
        ]
    grouped: dict[str, dict[str, Any]] = {}
    for cap in caps:
        module = str(cap.get("module") or "sistema")
        group = grouped.setdefault(
            module,
            {"module": module, "label": _module_group(module), "categories": {}, "capabilities": []},
        )
        category = str(cap.get("category") or "consultar")
        group["categories"][category] = int(group["categories"].get(category) or 0) + 1
        if len(group["capabilities"]) < 12:
            group["capabilities"].append(
                {
                    "id": cap.get("id"),
                    "category": cap.get("category"),
                    "title": cap.get("title"),
                    "executor_type": cap.get("executor_type"),
                    "executor_id": cap.get("executor_id"),
                    "read_only": cap.get("read_only"),
                    "requires_approval": cap.get("requires_approval"),
                    "status_text": cap.get("status_text"),
                }
            )
    modules = sorted(grouped.values(), key=lambda item: str(item.get("label") or ""))
    return {
        "version": CAPABILITY_REGISTRY_VERSION,
        "total_capabilities": len(caps),
        "modules": modules[: max(1, min(int(limit or 120), 120))],
        "generated_at": _now(),
    }


configure_codex_capabilities_runtime()


__all__ = [
    "CAPABILITY_REGISTRY_VERSION",
    "configure_codex_capabilities_runtime",
    "list_capabilities",
    "list_modules",
    "resolve_capability",
    "get_capability",
    "compact_capability_catalog",
    "build_capabilities",
]
