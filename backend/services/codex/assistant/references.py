"""Internal Codex Assistant component."""

from __future__ import annotations

import copy
import hashlib
import html
import io
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
import unicodedata
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.services import codex_assistant_storage, codex_turn_context
from backend.services.favoritos_margem import margem_calcular_anuncio, margem_formatar_moeda, margem_parse_float
from backend.services.sales_tools import api as sales_tools
from backend.services.whatsapp import intent as whatsapp_intent

from .normalization import _assistant_latest_ml_event_kind
from .runtime import _assistant_info_base, _assistant_periodo_padrao, _assistant_safe_id, _assistant_slug, _assistant_texto_norm
from .settings import CODEX_SALES_RETURNS_CONTEXT_CHAR_LIMIT
from .utils import _assistant_float, _assistant_money
def _assistant_resolve_period(client_id: str, message: str, screen_context: Any) -> tuple[str, str]:
    contexto = dict(screen_context) if isinstance(screen_context, dict) else {}
    try:
        data_inicio, data_fim = sales_tools.extract_sales_period(str(message or ""), contexto)
        if data_inicio and data_fim:
            return str(data_inicio), str(data_fim)
    except Exception:
        pass
    for start_key, end_key in (("data_inicio", "data_fim"), ("inicio", "fim"), ("start_date", "end_date")):
        data_inicio = str(contexto.get(start_key) or "").strip()
        data_fim = str(contexto.get(end_key) or "").strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}$", data_inicio) and re.match(r"^\d{4}-\d{2}-\d{2}$", data_fim):
            return data_inicio, data_fim
    text = _assistant_texto_norm(message)
    if (
        re.search(r"\b(relatorio|resumo)\b", text)
        and re.search(r"\b(do dia|diario|diaria|de hoje|hoje)\b", text)
    ):
        today = datetime.now(ZoneInfo("America/Sao_Paulo")).date().isoformat()
        return today, today
    match_days = re.search(r"\bultimos?\s+(\d{1,3})\s+dias?\b", text)
    if not match_days:
        match_days = re.search(r"\b(\d{1,3})\s+dias?\b", text)
    if match_days:
        return _assistant_periodo_padrao(max(1, min(int(match_days.group(1)), 365)))
    return _assistant_periodo_padrao(30)


def _assistant_previous_period(data_inicio: str, data_fim: str) -> tuple[str, str]:
    try:
        ini = date.fromisoformat(data_inicio)
        fim = date.fromisoformat(data_fim)
        days = max(1, (fim - ini).days + 1)
        prev_fim = ini - timedelta(days=1)
        prev_ini = prev_fim - timedelta(days=days - 1)
        return prev_ini.isoformat(), prev_fim.isoformat()
    except Exception:
        fim = date.today() - timedelta(days=30)
        ini = fim - timedelta(days=29)
        return ini.isoformat(), fim.isoformat()


def _assistant_resolve_loja(client_id: str, message: str, screen_context: Any) -> str:
    contexto = dict(screen_context) if isinstance(screen_context, dict) else {}
    try:
        loja = sales_tools.resolve_store(str(client_id or ""), str(message or ""), contexto)
        if loja:
            return str(loja)
    except Exception:
        pass
    for key in ("loja", "conta", "store", "store_name", "loja_atual", "contexto_loja"):
        loja = str(contexto.get(key) or "").strip()
        if loja and loja not in {"__todas", "Todas as lojas"}:
            return loja
    text = _assistant_texto_norm(message)
    try:
        from backend.services.ia_context import get_tenant_path

        tenant_path = get_tenant_path(client_id)
    except Exception:
        tenant_path = os.path.join(_assistant_info_base(), _assistant_safe_id(client_id))
    if os.path.exists(tenant_path):
        for name in os.listdir(tenant_path):
            if not (name.startswith("vendas_historico_") and name.endswith(".db")):
                continue
            slug = name[len("vendas_historico_"):-3]
            loja = slug.replace("_", " ").strip()
            loja_norm = _assistant_texto_norm(loja)
            if loja_norm and loja_norm in text:
                return loja
    return ""


def _assistant_candidate_info_roots() -> list[str]:
    roots: list[str] = []

    def add(path: Any) -> None:
        text = str(path or "").strip()
        if not text:
            return
        text = os.path.abspath(os.path.expandvars(os.path.expanduser(text)))
        if text not in roots and os.path.isdir(text):
            roots.append(text)

    for env_name in ("JK_CODEX_INFO_FALLBACK_DIRS", "JK_INFO_DIR"):
        value = os.getenv(env_name) or ""
        parts = value.split(os.pathsep) if env_name.endswith("DIRS") else [value]
        for part in parts:
            add(part)

    add(_assistant_info_base())
    add(os.path.join(os.getcwd(), "info"))
    try:
        for parent in Path(__file__).resolve().parents:
            add(parent / "info")
    except Exception:
        pass

    appdata = os.getenv("APPDATA") or ""
    if appdata:
        add(os.path.join(appdata, "JK Sistema Cliente", "local_app", "info"))
        add(os.path.join(appdata, "JK Sistema Cliente", "info"))

    return roots


def _assistant_sales_db_candidates(client_id: str, loja: Optional[str]) -> list[tuple[str, str]]:
    client_safe = _assistant_safe_id(client_id)
    loja_slug = _assistant_slug(loja or "")
    loja_norm = _assistant_texto_norm(loja or "")
    roots = _assistant_candidate_info_roots()

    if loja_slug:
        exact_name = f"vendas_historico_{loja_slug}.db"
        for root in roots:
            path = os.path.join(root, client_safe, exact_name)
            if os.path.exists(path):
                return [(path, loja_slug.replace("_", " "))]

    for root in roots:
        tenant_path = os.path.join(root, client_safe)
        if not os.path.isdir(tenant_path):
            continue
        found: list[tuple[str, str]] = []
        for name in sorted(os.listdir(tenant_path)):
            if name == "vendas_historico.db":
                if not loja_slug:
                    found.append((os.path.join(tenant_path, name), "geral"))
                continue
            if not name.startswith("vendas_historico_") or not name.endswith(".db") or ".backup_" in name:
                continue
            slug = name[len("vendas_historico_"):-3]
            store_name = slug.replace("_", " ").strip() or "loja"
            slug_norm = _assistant_texto_norm(store_name)
            if loja_slug and loja_norm and loja_norm not in slug_norm and slug_norm not in loja_norm:
                continue
            found.append((os.path.join(tenant_path, name), store_name))
        if found:
            return found
    return []


def _assistant_sales_db_candidates_query(client_id: str, loja: Optional[str]) -> list[tuple[str, str]]:
    candidates = _assistant_sales_db_candidates(client_id, loja)
    if loja and str(loja).strip() and str(loja).strip() not in {"__todas", "Todas as lojas"}:
        return candidates
    store_candidates = [
        (path, store)
        for path, store in candidates
        if os.path.basename(path) != "vendas_historico.db"
    ]
    return store_candidates or candidates


def _assistant_store_label(store_name: str) -> str:
    raw = str(store_name or "").strip()
    norm = _assistant_texto_norm(raw)
    known = {
        "jk pecas": "JK Pecas",
        "uai mineirinho": "Uai Mineirinho",
        "carlos jose": "Carlos Jose",
        "deckas": "Deckas",
        "dona nina": "Dona Nina",
        "multimarcas": "Multimarcas",
        "imports": "Imports",
        "geral": "Geral",
    }
    if norm in known:
        return known[norm]
    return raw.replace("_", " ").strip().title() or "Loja"


def _assistant_calendar_date(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _assistant_bool_arg(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return bool(default)
    normalized = _assistant_texto_norm(str(value))
    if normalized in {"1", "true", "sim", "yes", "on", "com", "incluir"}:
        return True
    if normalized in {"0", "false", "nao", "no", "off", "sem", "excluir"}:
        return False
    return bool(default)


def _assistant_force_refresh_requested(args: Any, message: str) -> bool:
    args = args if isinstance(args, dict) else {}
    if _assistant_bool_arg(args.get("force_refresh", args.get("atualizar_cache")), False):
        return True
    text = _assistant_texto_norm(message)
    return bool(re.search(r"\b(atualize agora|atualizar agora|consulte agora|sem cache|ignorar cache|force refresh)\b", text))


def _assistant_normalize_api_status(value: Any, default: str = "") -> str:
    raw_values = value if isinstance(value, (list, tuple, set)) else re.split(r"[,;|\s]+", str(value or ""))
    statuses: list[str] = []
    for item in raw_values:
        status = re.sub(r"[^a-z0-9_-]+", "", _assistant_texto_norm(str(item or "")).replace(" ", "_"))
        if status and status not in statuses:
            statuses.append(status[:40])
    return ",".join(statuses[:12]) or str(default or "")


def _assistant_normalize_identifier(value: Any, limit: int = 60) -> str:
    raw = str(value or "").strip()
    match = re.search(r"[A-Za-z0-9][A-Za-z0-9._-]*", raw)
    return str(match.group(0) if match else "")[:limit]


def _assistant_normalize_ml_item_id(value: Any) -> str:
    raw = str(value or "").strip().upper()
    match = re.search(r"\bMLB[\s_-]?(\d{6,})\b", raw)
    if match:
        return "MLB" + match.group(1)
    digits = re.sub(r"\D+", "", raw)
    return ("MLB" + digits) if len(digits) >= 6 and re.fullmatch(r"[\d\s_-]+", raw or "") else ""


def _assistant_normalize_sku(value: Any) -> str:
    raw = str(value or "").strip()
    if _assistant_calendar_date(raw):
        return ""
    sku = raw.upper()
    sku = re.sub(r"\s+", "", sku)
    return sku[:80]


def _assistant_extract_sku_filter(message: str, screen_context: Any = None) -> str:
    if isinstance(screen_context, dict):
        for key in ("sku", "selected_sku", "sku_atual", "filtro_sku", "produto_sku"):
            sku_ctx = _assistant_normalize_sku(screen_context.get(key))
            if sku_ctx:
                return sku_ctx
    text = str(message or "")
    patterns = (
        r"\bsku\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9._/-]{1,79})",
        r"\bSKU\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9._/-]{1,79})",
    )
    blocked = {
        "PERIODO", "LOJA", "LOJAS", "CONTA", "CONTAS", "TODAS", "VENDA", "VENDAS",
        "DEVOLUCAO", "DEVOLUCOES", "ULTIMA", "ULTIMO", "MAIS", "RECENTE",
        "NA", "NO", "NAS", "NOS", "DA", "DO", "DAS", "DOS", "DE", "EM", "PARA", "E", "OU",
    }
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            sku = _assistant_normalize_sku(str(match.group(1) or "").rstrip(".,;:!?"))
            if sku and sku not in blocked:
                return sku
    if _assistant_latest_ml_event_kind(message):
        normalized = _assistant_texto_norm(message)
        inferred = re.findall(
            r"\b(?:venda|pedido|devolucao|reembolso|estorno)\b[^.?!]{0,80}?\b(?:do|da|de)\s+(?:sku\s*)?([a-z0-9][a-z0-9._/-]{1,79})",
            normalized,
        )
        for candidate in reversed(inferred):
            sku = _assistant_normalize_sku(candidate)
            if sku and sku not in blocked:
                return sku
    return ""


def _assistant_product_refs_from_context(*values: Any) -> dict[str, list[str]]:
    refs: dict[str, list[str]] = {"skus": [], "bling_ids": []}
    seen_nodes = 0

    def add(kind: str, value: Any) -> None:
        raw_values = value if isinstance(value, (list, tuple, set)) else [value]
        for raw in raw_values:
            text = str(raw or "").strip().strip(".,;:)")
            if not text:
                continue
            if kind == "skus":
                text = _assistant_normalize_sku(text)
                if not text or text in {
                    "SKU", "PRODUTO", "SALDO", "ESTOQUE", "BLING",
                    "NA", "NO", "NAS", "NOS", "DA", "DO", "DAS", "DOS", "DE", "EM", "PARA", "E", "OU",
                }:
                    continue
            else:
                if not re.fullmatch(r"\d{3,}", text):
                    continue
            if text not in refs[kind]:
                refs[kind].append(text)
            if len(refs[kind]) >= 20:
                return

    def scan_text(text: str) -> None:
        raw = str(text or "")
        for match in re.finditer(r"\bsku\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9._/-]{1,79})", raw, flags=re.I):
            add("skus", match.group(1))
        for match in re.finditer(
            r"\b(?:id_bling|id\s*bling|bling\s*id|id_produto_bling|id\s*produto\s*bling|idsProdutos\[\]|ids_produtos)\b[^0-9]{0,30}((?:\d{3,}[\s,;/|]*){1,12})",
            raw,
            flags=re.I,
        ):
            add("bling_ids", re.findall(r"\d{3,}", match.group(1) or ""))

    def visit(obj: Any, depth: int = 0) -> None:
        nonlocal seen_nodes
        if seen_nodes >= 600 or depth > 6:
            return
        seen_nodes += 1
        if isinstance(obj, dict):
            for key, value in obj.items():
                key_norm = _assistant_texto_norm(str(key or "")).replace(" ", "_").replace("-", "_")
                if key_norm in {"sku", "selected_sku", "sku_atual", "filtro_sku", "produto_sku", "seller_sku", "canonical_sku", "codigo"}:
                    add("skus", value)
                if "bling" in key_norm and "id" in key_norm:
                    if isinstance(value, str):
                        add("bling_ids", re.findall(r"\d{3,}", value))
                    else:
                        add("bling_ids", value)
                if key_norm in {"idsprodutos[]", "ids_produtos", "id_produto_bling", "id_bling"}:
                    if isinstance(value, str):
                        add("bling_ids", re.findall(r"\d{3,}", value))
                    else:
                        add("bling_ids", value)
                if isinstance(value, str):
                    scan_text(value)
                if isinstance(value, (dict, list, tuple)):
                    visit(value, depth + 1)
        elif isinstance(obj, (list, tuple)):
            for item in obj[:80]:
                visit(item, depth + 1)
        elif isinstance(obj, str):
            scan_text(obj)

    for value in values:
        visit(value)
    return refs


def _assistant_bling_message_with_refs(
    message: str,
    plan: dict[str, Any],
    screen_context: Any,
    existing_registry_results: Optional[list[dict[str, Any]]] = None,
) -> str:
    refs = _assistant_product_refs_from_context(screen_context, existing_registry_results or [])
    plan_sku = _assistant_normalize_sku(plan.get("sku"))
    if plan_sku:
        refs["skus"] = [plan_sku, *[item for item in refs["skus"] if item != plan_sku]]
    if not refs["skus"] and not refs["bling_ids"]:
        return message
    lines = ["Contexto materializado para consulta Bling read-only:"]
    if refs["skus"]:
        lines.append("SKU " + ", ".join(refs["skus"][:5]))
    if refs["bling_ids"]:
        lines.append("id_bling " + ", ".join(refs["bling_ids"][:12]))
    loja = str(plan.get("loja") or "").strip()
    if loja:
        lines.append("loja " + loja)
    original_message = str(message or "").strip()
    if original_message:
        lines.extend(["", "Pedido original:", original_message])
    return "\n".join(line for line in lines if line is not None).strip()


def _assistant_wants_store_breakdown(message: str, loja: Optional[str]) -> bool:
    text = _assistant_texto_norm(message)
    if re.search(r"\b(todas as lojas|todas as contas|por loja|por conta|cada loja|cada conta|loja a loja|conta a conta|separado por loja|separada por loja|separando por loja|individualmente)\b", text):
        return True
    if not loja and re.search(r"\b(lojas|contas)\b", text) and re.search(r"\b(vendas?|devolucoes?|devolvidos?)\b", text):
        return True
    return False


def _assistant_sr_empty_store(store: str, source_path: str) -> dict[str, Any]:
    return {
        "loja": store,
        "source_path": source_path,
        "vendas_quantidade": 0.0,
        "vendas_valor": 0.0,
        "vendas_pedidos": 0,
        "vendas_registros": 0,
        "devolucoes_quantidade": 0.0,
        "devolucoes_valor": 0.0,
        "devolucoes_registros": 0,
        "devolucoes_fonte": "",
    }


def _assistant_sr_empty_sku(sku: str) -> dict[str, Any]:
    return {
        "sku": sku or "SEM SKU",
        "produto": "",
        "quantidade_vendida": 0.0,
        "valor_vendido": 0.0,
        "pedidos": 0,
        "quantidade_devolvida": 0.0,
        "valor_devolvido": 0.0,
        "lojas": set(),
        "_pedidos": set(),
    }


def _assistant_sr_add_sku(aggregates: dict[str, dict[str, Any]], sku_raw: Any, produto: Any, loja: str, kind: str, quantidade: Any, valor: Any, pedido_key: str = "") -> None:
    sku = _assistant_normalize_sku(sku_raw) or "SEM SKU"
    item = aggregates.setdefault(sku, _assistant_sr_empty_sku(sku))
    produto_txt = str(produto or "").strip()
    if produto_txt and not item.get("produto"):
        item["produto"] = produto_txt[:220]
    if loja:
        item["lojas"].add(loja)
    qtd = _assistant_float(quantidade)
    val = _assistant_float(valor)
    if kind == "venda":
        item["quantidade_vendida"] += qtd
        item["valor_vendido"] += val
        if pedido_key:
            item["_pedidos"].add(pedido_key)
    else:
        item["quantidade_devolvida"] += qtd
        item["valor_devolvido"] += val


def _assistant_sr_finalize_sku_rows(aggregates: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in aggregates.values():
        vendas_qtd = float(item.get("quantidade_vendida") or 0)
        vendas_valor = float(item.get("valor_vendido") or 0)
        devol_qtd = float(item.get("quantidade_devolvida") or 0)
        devol_valor = float(item.get("valor_devolvido") or 0)
        rows.append(
            {
                "sku": item.get("sku") or "SEM SKU",
                "produto": item.get("produto") or "",
                "quantidade_vendida": vendas_qtd,
                "valor_vendido": vendas_valor,
                "pedidos": len(item.get("_pedidos") or []),
                "quantidade_devolvida": devol_qtd,
                "valor_devolvido": devol_valor,
                "taxa_devolucao_quantidade_percentual": (devol_qtd / vendas_qtd * 100.0) if vendas_qtd > 0 else 0.0,
                "taxa_devolucao_valor_percentual": (devol_valor / vendas_valor * 100.0) if vendas_valor > 0 else 0.0,
                "lojas": sorted(item.get("lojas") or []),
            }
        )
    return sorted(rows, key=lambda row: (float(row.get("valor_devolvido") or 0), float(row.get("quantidade_devolvida") or 0), float(row.get("valor_vendido") or 0)), reverse=True)


def _assistant_sr_compact_record(record: dict[str, Any], kind: str) -> str:
    if kind == "venda":
        return (
            f"{record.get('data') or '-'} | {record.get('loja') or '-'} | SKU {record.get('sku') or '-'} | "
            f"qtd {record.get('quantidade') or 0} | valor {_assistant_money(record.get('valor') or 0)} | "
            f"pedido {record.get('numero') or record.get('id_unico') or '-'} | {record.get('produto') or ''}"
        )
    return (
        f"{record.get('data') or '-'} | {record.get('loja') or '-'} | SKU {record.get('sku') or '-'} | "
        f"qtd {record.get('quantidade') or 0} | valor {_assistant_money(record.get('valor_total') or 0)} | "
        f"nota {record.get('numero_nota') or record.get('numero') or '-'} | {record.get('produto') or ''}"
    )


def _assistant_sales_returns_context_text(result: dict[str, Any]) -> str:
    totals = result.get("totais") if isinstance(result.get("totais"), dict) else {}
    lines = [
        "Consulta unificada de vendas e devolucoes:",
        f"Periodo: {result.get('data_inicio') or '-'} a {result.get('data_fim') or '-'}",
        f"Loja/conta: {result.get('loja') or 'todas'} | SKU: {result.get('sku') or 'todos'}",
        (
            "Totais: "
            f"vendas qtd {totals.get('quantidade_vendida_total') or 0}, "
            f"vendas valor {_assistant_money(totals.get('valor_vendido_total') or 0)}, "
            f"pedidos {totals.get('pedidos_total') or 0}, "
            f"devolucoes qtd {totals.get('quantidade_devolvida_total') or 0}, "
            f"devolucoes valor {_assistant_money(totals.get('valor_devolvido_total') or 0)}, "
            f"taxa qtd {totals.get('taxa_devolucao_quantidade_percentual') or 0:.2f}%, "
            f"taxa valor {totals.get('taxa_devolucao_valor_percentual') or 0:.2f}%."
        ),
        "",
        "Por loja:",
    ]
    for row in result.get("por_loja") or []:
        lines.append(
            f"- {row.get('loja') or '-'}: vendas qtd {row.get('vendas_quantidade') or 0}, "
            f"valor {_assistant_money(row.get('vendas_valor') or 0)}, pedidos {row.get('vendas_pedidos') or 0}; "
            f"devolucoes qtd {row.get('devolucoes_quantidade') or 0}, valor {_assistant_money(row.get('devolucoes_valor') or 0)} "
            f"({row.get('devolucoes_fonte') or 'sem fonte'})."
        )
    lines.append("")
    lines.append("Por SKU:")
    for row in result.get("por_sku") or []:
        lines.append(
            f"- SKU {row.get('sku') or '-'} | {row.get('produto') or ''} | "
            f"vendido qtd {row.get('quantidade_vendida') or 0}, valor {_assistant_money(row.get('valor_vendido') or 0)}, pedidos {row.get('pedidos') or 0}; "
            f"devolvido qtd {row.get('quantidade_devolvida') or 0}, valor {_assistant_money(row.get('valor_devolvido') or 0)}; "
            f"lojas {', '.join(row.get('lojas') or []) or '-'}."
        )
    lines.append("")
    lines.append("Vendas:")
    for record in result.get("vendas") or []:
        lines.append("- " + _assistant_sr_compact_record(record, "venda"))
    lines.append("")
    lines.append("Devolucoes:")
    for record in result.get("devolucoes") or []:
        lines.append("- " + _assistant_sr_compact_record(record, "devolucao"))
    warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
    if warnings:
        lines.append("")
        lines.append("Avisos:")
        for warning in warnings:
            lines.append("- " + str(warning))
    return "\n".join(lines)[:CODEX_SALES_RETURNS_CONTEXT_CHAR_LIMIT]
