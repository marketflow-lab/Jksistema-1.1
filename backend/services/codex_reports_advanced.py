"""Decision-oriented reporting helpers for the Black Jhon assistant.

The module is intentionally independent from the assistant renderer.  It reads
tenant data in read-only mode, builds typed report datasets and persists only
explicit settings, manual financial adjustments and internal action queues.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import sqlite3
import statistics
from copy import deepcopy
from datetime import date, datetime, timedelta
from statistics import NormalDist
from typing import Any, Optional

from backend.services import codex_assistant_storage
from backend.services.favoritos_margem import margem_calcular_anuncio


REPORT_PROFILES = {"daily_exceptions", "weekly_sales_stock", "import_order", "custom"}
PIPELINE_STATUS_BUCKETS = {
    "pedido aprovado": "open",
    "pedido emitido": "open",
    "em producao": "production",
    "em transito": "transit",
    "em desembaraco": "customs",
    "recebido": "received",
}

DEFAULT_REPORT_SETTINGS: dict[str, Any] = {
    "global": {
        "margin_coverage_min": 0.95,
        "demand_history_days": 180,
        "weekly_min_samples": 8,
        "abc_a_limit": 0.80,
        "abc_b_limit": 0.95,
        "xyz_x_limit": 0.50,
        "xyz_y_limit": 1.00,
        "default_lead_time_days": 180,
        "review_cycle_days": 90,
        "safety_buffer_days": 30,
        "target_margin_pct": 20.0,
        "service_levels": {"A": 0.975, "B": 0.95, "C": 0.90},
        "z_service_reduction": 0.025,
        "minimum_service_level": 0.85,
        "scenario_exchange_pct": [5, 10],
        "scenario_freight_pct": [10, 20],
        "scenario_delay_days": [15, 30],
        "urgency_due_days": {"immediate": 1, "high": 3, "medium": 7, "low": 14},
        "owners": {
            "replenishment": {"username": "", "role": "Compras"},
            "price_review": {"username": "", "role": "Comercial"},
            "liquidation": {"username": "", "role": "Estoque"},
            "data_quality": {"username": "", "role": "Cadastro"},
        },
    },
    "stores": {},
    "suppliers": {},
    "skus": {},
}


def _deep_merge(base: Any, override: Any) -> Any:
    if not isinstance(base, dict) or not isinstance(override, dict):
        return deepcopy(override if override is not None else base)
    result = deepcopy(base)
    for key, value in override.items():
        result[key] = _deep_merge(result.get(key), value) if isinstance(value, dict) else deepcopy(value)
    return result


def normalize_report_settings(payload: Any) -> dict[str, Any]:
    data = _deep_merge(DEFAULT_REPORT_SETTINGS, payload if isinstance(payload, dict) else {})
    for group in ("stores", "suppliers", "skus"):
        if not isinstance(data.get(group), dict):
            data[group] = {}
    global_cfg = data.get("global") if isinstance(data.get("global"), dict) else {}
    global_cfg["margin_coverage_min"] = min(1.0, max(0.0, _float(global_cfg.get("margin_coverage_min"), 0.95)))
    global_cfg["demand_history_days"] = max(56, min(730, int(_float(global_cfg.get("demand_history_days"), 180))))
    global_cfg["weekly_min_samples"] = max(4, min(52, int(_float(global_cfg.get("weekly_min_samples"), 8))))
    data["global"] = global_cfg
    return data


def report_settings_get(info_base: str, client_id: str) -> dict[str, Any]:
    stored = codex_assistant_storage.codex_assistant_report_settings_get(info_base, client_id)
    return normalize_report_settings(stored)


def report_settings_save(info_base: str, client_id: str, payload: dict[str, Any], username: str) -> dict[str, Any]:
    normalized = normalize_report_settings(payload)
    return codex_assistant_storage.codex_assistant_report_settings_save(
        info_base,
        client_id,
        normalized,
        updated_by=username,
    )


def _text_key(value: Any) -> str:
    raw = fix_mojibake(str(value or "")).strip().lower()
    raw = re.sub(r"[^a-z0-9]+", " ", _strip_accents(raw))
    return re.sub(r"\s+", " ", raw).strip()


def _strip_accents(value: str) -> str:
    import unicodedata

    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _mojibake_score(value: str) -> int:
    return sum(value.count(marker) for marker in ("Ã", "Â", "â€", "ðŸ", "ï¿½", "�"))


def fix_mojibake(value: Any) -> str:
    text = str(value or "")
    if not text or _mojibake_score(text) == 0:
        return text
    for _ in range(3):
        current_score = _mojibake_score(text)
        candidates: list[str] = []
        for encoding in ("cp1252", "latin1"):
            try:
                candidates.append(text.encode(encoding).decode("utf-8"))
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
        improved = [candidate for candidate in candidates if _mojibake_score(candidate) < current_score]
        if not improved:
            break
        text = min(improved, key=_mojibake_score)
    return text


def normalize_text_tree(value: Any) -> Any:
    if isinstance(value, str):
        return fix_mojibake(value)
    if isinstance(value, list):
        return [normalize_text_tree(item) for item in value]
    if isinstance(value, dict):
        return {fix_mojibake(key): normalize_text_tree(item) for key, item in value.items()}
    return value


def _float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            number = float(value)
            return number if math.isfinite(number) else float(default)
        except Exception:
            return float(default)
    raw = str(value or "").strip().replace("\u00a0", " ")
    if not raw:
        return float(default)
    raw = re.sub(r"(?i)(r\$|us\$|usd|brl|\$|%)", "", raw)
    raw = re.sub(r"[^0-9,.\-]", "", raw)
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".") if raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        number = float(raw)
        return number if math.isfinite(number) else float(default)
    except Exception:
        return float(default)


def _optional_float(value: Any) -> Optional[float]:
    raw = str(value if value is not None else "").strip().lower()
    if not raw or raw in {"none", "null", "nan", "n/a", "indisponivel", "indisponível"}:
        return None
    number = _float(value, float("nan"))
    return number if math.isfinite(number) else None


def _tenant_path(info_base: str, client_id: str) -> str:
    return os.path.join(os.path.abspath(info_base), "".join(ch for ch in str(client_id) if ch.isalnum() or ch in "-_") or "default")


def _read_json(path: str, fallback: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception:
        return fallback


def _read_csv(path: str) -> list[dict[str, str]]:
    if not os.path.exists(path):
        return []
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            with open(path, "r", encoding=encoding, newline="") as fh:
                sample = fh.read(8192)
                fh.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
                except csv.Error:
                    dialect = csv.excel
                return [normalize_text_tree(dict(row)) for row in csv.DictReader(fh, dialect=dialect)]
        except UnicodeDecodeError:
            continue
        except Exception:
            return []
    return []


def _sales_db_paths(tenant: str) -> list[str]:
    if not os.path.isdir(tenant):
        return []
    paths: list[str] = []
    for name in sorted(os.listdir(tenant)):
        lower = name.lower()
        if lower == "vendas_historico.db" or (lower.startswith("vendas_historico_") and lower.endswith(".db") and ".backup_" not in lower):
            paths.append(os.path.join(tenant, name))
    return paths


def _sqlite_has_table(path: str, table: str) -> bool:
    if not path or not os.path.exists(path):
        return False
    try:
        conn = sqlite3.connect(path, timeout=2)
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (str(table or ""),),
        ).fetchone()
        conn.close()
        return bool(row)
    except Exception:
        return False


def _tenant_has_table(tenant: str, table: str) -> bool:
    for path in _sales_db_paths(tenant):
        if _sqlite_has_table(path, table):
            return True
    return False


def _fallback_store_from_db(path: str) -> str:
    name = os.path.basename(path)
    slug = re.sub(r"^vendas_historico_|\.db$", "", name, flags=re.I)
    if slug == name or not slug:
        return ""
    return fix_mojibake(slug.replace("_", " ").title())


def _load_sales_rows(tenant: str, start: date, end: date, store: str = "") -> tuple[list[dict[str, Any]], list[str]]:
    rows: dict[tuple[Any, ...], dict[str, Any]] = {}
    warnings: list[str] = []
    store_key = _text_key(store)
    for db_path in _sales_db_paths(tenant):
        try:
            conn = sqlite3.connect(db_path, timeout=5)
            conn.row_factory = sqlite3.Row
            tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "vendas" not in tables:
                conn.close()
                continue
            data = conn.execute(
                """
                SELECT id_unico, data, loja_conta, numero, sku, produto, quantidade, valor,
                       situacao, canal, comprador
                FROM vendas
                WHERE date(data) BETWEEN ? AND ?
                  AND COALESCE(devolucao, 0) = 0
                  AND LOWER(COALESCE(situacao,'')) NOT IN
                      ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchall()
            conn.close()
            fallback_store = _fallback_store_from_db(db_path)
            for raw in data:
                item = normalize_text_tree(dict(raw))
                item_store = str(item.get("loja_conta") or fallback_store or "Sem loja").strip()
                if store_key and _text_key(item_store) != store_key:
                    continue
                item["loja"] = item_store
                item["sku"] = str(item.get("sku") or "").strip().upper()
                key = ("id", str(item.get("id_unico"))) if str(item.get("id_unico") or "").strip() else (
                    "fallback",
                    str(item.get("data") or "")[:10],
                    _text_key(item_store),
                    str(item.get("numero") or ""),
                    item["sku"],
                    _float(item.get("quantidade")),
                    _float(item.get("valor")),
                )
                current = rows.get(key)
                if current is None or len(str(item.get("produto") or "")) > len(str(current.get("produto") or "")):
                    rows[key] = item
        except Exception as exc:
            warnings.append(f"Histórico de vendas indisponível em {os.path.basename(db_path)}: {str(exc)[:180]}")
    return list(rows.values()), warnings


def _load_return_rows(tenant: str, start: date, end: date, store: str = "") -> tuple[list[dict[str, Any]], list[str]]:
    rows: dict[tuple[Any, ...], dict[str, Any]] = {}
    warnings: list[str] = []
    store_key = _text_key(store)
    for db_path in _sales_db_paths(tenant):
        try:
            conn = sqlite3.connect(db_path, timeout=5)
            conn.row_factory = sqlite3.Row
            tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "notas_entrada_itens" not in tables:
                conn.close()
                continue
            data = conn.execute(
                """
                SELECT id_unico, data_emissao AS data, loja_conta, numero_nota AS numero,
                       sku, descricao AS produto, quantidade, valor_total AS valor, fornecedor
                FROM notas_entrada_itens
                WHERE date(data_emissao) BETWEEN ? AND ? AND COALESCE(devolucao, 0) = 1
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchall()
            conn.close()
            fallback_store = _fallback_store_from_db(db_path)
            for raw in data:
                item = normalize_text_tree(dict(raw))
                item_store = str(item.get("loja_conta") or fallback_store or "Sem loja").strip()
                if store_key and _text_key(item_store) != store_key:
                    continue
                item["loja"] = item_store
                item["sku"] = str(item.get("sku") or "").strip().upper()
                key = ("id", str(item.get("id_unico"))) if str(item.get("id_unico") or "").strip() else (
                    "fallback",
                    str(item.get("data") or "")[:10],
                    _text_key(item_store),
                    str(item.get("numero") or ""),
                    item["sku"],
                    _float(item.get("quantidade")),
                    _float(item.get("valor")),
                )
                rows[key] = item
        except Exception as exc:
            warnings.append(f"Histórico de devoluções indisponível em {os.path.basename(db_path)}: {str(exc)[:180]}")
    return list(rows.values()), warnings


def _load_cost_maps(tenant: str) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, dict[str, Any]]]:
    by_store: dict[tuple[str, str], dict[str, Any]] = {}
    generic: dict[str, dict[str, Any]] = {}
    for row in _read_csv(os.path.join(tenant, "cadastro_custos_lojas.csv")):
        sku = str(row.get("sku") or row.get("SKU") or "").strip().upper()
        store = str(row.get("loja_sync") or row.get("loja") or "").strip()
        if sku:
            by_store[(_text_key(store), sku)] = {
                "cost": _optional_float(row.get("custo")),
                "price": _optional_float(row.get("preco")),
                "tax_pct": _optional_float(row.get("imposto")),
                "product": fix_mojibake(row.get("produto") or ""),
            }
    for row in _read_csv(os.path.join(tenant, "cadastro_produtos.csv")):
        sku = str(row.get("sku") or row.get("SKU") or "").strip().upper()
        if sku:
            generic[sku] = {
                "cost": _optional_float(row.get("custo")),
                "price": _optional_float(row.get("preco")),
                "tax_pct": _optional_float(row.get("imposto")),
                "product": fix_mojibake(row.get("nome") or row.get("produto") or ""),
            }
    return by_store, generic


def _cost_for(by_store: dict, generic: dict, store: str, sku: str) -> dict[str, Any]:
    local = by_store.get((_text_key(store), sku)) or {}
    base = generic.get(sku) or {}
    return {
        "cost": local.get("cost") if local.get("cost") is not None else base.get("cost"),
        "price": local.get("price") if local.get("price") is not None else base.get("price"),
        "tax_pct": local.get("tax_pct") if local.get("tax_pct") not in (None, "") else base.get("tax_pct"),
        "product": local.get("product") or base.get("product") or "",
    }


def _load_latest_stock(tenant: str, store: str = "") -> tuple[dict[tuple[str, str], dict[str, Any]], Optional[str], list[str]]:
    path = os.path.join(tenant, "estoque_historico.db")
    if not os.path.exists(path):
        return {}, None, ["Histórico de estoque não encontrado."]
    store_key = _text_key(store)
    result: dict[tuple[str, str], dict[str, Any]] = {}
    warnings: list[str] = []
    latest: Optional[str] = None
    try:
        conn = sqlite3.connect(path, timeout=5)
        conn.row_factory = sqlite3.Row
        data = conn.execute(
            """
            SELECT e.* FROM estoque_historico e
            INNER JOIN (
                SELECT loja_sync, MAX(data_ref) AS max_data
                FROM estoque_historico GROUP BY loja_sync
            ) latest ON latest.loja_sync = e.loja_sync AND latest.max_data = e.data_ref
            """
        ).fetchall()
        conn.close()
        for raw in data:
            item = normalize_text_tree(dict(raw))
            item_store = str(item.get("loja_sync") or "Sem loja").strip()
            if store_key and _text_key(item_store) != store_key:
                continue
            sku = str(item.get("sku") or "").strip().upper()
            if not sku:
                continue
            item["loja"] = item_store
            item["sku"] = sku
            result[(_text_key(item_store), sku)] = item
            stamp = str(item.get("recorded_at") or item.get("data_ref") or "")
            if stamp and (latest is None or stamp > latest):
                latest = stamp
    except Exception as exc:
        warnings.append(f"Histórico de estoque indisponível: {str(exc)[:180]}")
    return result, latest, warnings


def _load_opening_stock(tenant: str, start: date, store: str = "") -> tuple[dict[tuple[str, str], float], Optional[str]]:
    path = os.path.join(tenant, "estoque_historico.db")
    if not os.path.exists(path):
        return {}, None
    store_key = _text_key(store)
    result: dict[tuple[str, str], float] = {}
    snapshot: Optional[str] = None
    try:
        conn = sqlite3.connect(path, timeout=5)
        conn.row_factory = sqlite3.Row
        data = conn.execute(
            """
            SELECT e.* FROM estoque_historico e
            INNER JOIN (
                SELECT loja_sync, MAX(data_ref) AS max_data
                FROM estoque_historico
                WHERE date(data_ref) < ?
                GROUP BY loja_sync
            ) opening ON opening.loja_sync = e.loja_sync AND opening.max_data = e.data_ref
            """,
            (start.isoformat(),),
        ).fetchall()
        conn.close()
        for raw in data:
            item = normalize_text_tree(dict(raw))
            item_store = str(item.get("loja_sync") or "Sem loja").strip()
            if store_key and _text_key(item_store) != store_key:
                continue
            sku = str(item.get("sku") or "").strip().upper()
            if not sku:
                continue
            result[(_text_key(item_store), sku)] = max(0.0, _float(item.get("saldo_loja")))
            stamp = str(item.get("data_ref") or "")
            if stamp and (snapshot is None or stamp > snapshot):
                snapshot = stamp
    except Exception:
        return {}, None
    return result, snapshot


def _load_received_units(tenant: str, start: date, end: date, store: str = "") -> tuple[dict[tuple[str, str], float], bool]:
    store_key = _text_key(store)
    deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
    source_available = False
    for db_path in _sales_db_paths(tenant):
        try:
            conn = sqlite3.connect(db_path, timeout=5)
            conn.row_factory = sqlite3.Row
            tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "notas_entrada_itens" not in tables:
                conn.close()
                continue
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(notas_entrada_itens)")}
            required = {"data_emissao", "sku", "quantidade"}
            if not required.issubset(columns):
                conn.close()
                continue
            source_available = True
            id_expr = "id_unico" if "id_unico" in columns else "'' AS id_unico"
            store_expr = "loja_conta" if "loja_conta" in columns else "'' AS loja_conta"
            number_expr = "numero_nota" if "numero_nota" in columns else "'' AS numero_nota"
            return_filter = "AND COALESCE(devolucao, 0) = 0" if "devolucao" in columns else ""
            data = conn.execute(
                f"""
                SELECT {id_expr}, data_emissao, {store_expr}, {number_expr}, sku, quantidade
                FROM notas_entrada_itens
                WHERE date(data_emissao) BETWEEN ? AND ? {return_filter}
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchall()
            conn.close()
            fallback_store = _fallback_store_from_db(db_path)
            for raw in data:
                item = normalize_text_tree(dict(raw))
                item_store = str(item.get("loja_conta") or fallback_store or "Sem loja").strip()
                if store_key and _text_key(item_store) != store_key:
                    continue
                sku = str(item.get("sku") or "").strip().upper()
                if not sku:
                    continue
                item["store"] = item_store
                item["sku"] = sku
                row_key = ("id", str(item.get("id_unico"))) if str(item.get("id_unico") or "").strip() else (
                    "fallback", str(item.get("data_emissao") or "")[:10], _text_key(item_store),
                    str(item.get("numero_nota") or ""), sku, _float(item.get("quantidade")),
                )
                deduped[row_key] = item
        except Exception:
            continue
    result: dict[tuple[str, str], float] = {}
    for item in deduped.values():
        key = (_text_key(item.get("store")), str(item.get("sku") or ""))
        result[key] = result.get(key, 0.0) + max(0.0, _float(item.get("quantidade")))
    return result, source_available


def _pipeline_bucket(status: Any) -> str:
    return PIPELINE_STATUS_BUCKETS.get(_text_key(status), "")


def _load_purchase_pipeline(tenant: str, store: str = "") -> tuple[dict[tuple[str, str], dict[str, float]], list[dict[str, Any]], list[str]]:
    path = os.path.join(tenant, "listas_pedidos.json")
    lists = _read_json(path, [])
    if not isinstance(lists, list):
        return {}, [], ["Listas de pedidos inválidas."]
    store_key = _text_key(store)
    indexed: dict[tuple[str, str], dict[str, float]] = {}
    rows: list[dict[str, Any]] = []
    for order in lists:
        if not isinstance(order, dict):
            continue
        order = normalize_text_tree(order)
        order_store = str(order.get("loja") or "Sem loja").strip()
        if store_key and _text_key(order_store) not in {store_key, _text_key("__todas")}:
            continue
        bucket = _pipeline_bucket(order.get("status"))
        if not bucket:
            continue
        for item in order.get("itens") or []:
            if not isinstance(item, dict):
                continue
            sku = str(item.get("SKU") or item.get("sku") or "").strip().upper()
            if not sku:
                continue
            quantity = max(0.0, _float(item.get("Quantidade", item.get("quantity"))))
            key = (_text_key(order_store), sku)
            values = indexed.setdefault(key, {"open": 0.0, "production": 0.0, "transit": 0.0, "customs": 0.0, "received": 0.0})
            values[bucket] += quantity
            rows.append(
                {
                    "order_id": str(order.get("id") or ""),
                    "order_name": str(order.get("nome_lista") or ""),
                    "store": order_store,
                    "supplier": str(order.get("supplier") or order.get("fornecedor") or ""),
                    "status": fix_mojibake(order.get("status") or ""),
                    "bucket": bucket,
                    "sku": sku,
                    "quantity": quantity,
                    "eta": str(order.get("eta_date") or order.get("data_prevista_chegada") or ""),
                }
            )
    return indexed, rows, []


def _resolve_policy(settings: dict[str, Any], store: str, supplier: str, sku: str) -> dict[str, Any]:
    policy = dict(settings.get("global") or {})
    for group, key in (("stores", store), ("suppliers", supplier), ("skus", sku)):
        mapping = settings.get(group) if isinstance(settings.get(group), dict) else {}
        selected = mapping.get(key) or mapping.get(_text_key(key)) or {}
        if isinstance(selected, dict):
            policy = _deep_merge(policy, selected)
    return policy


def _period_for_profile(profile: str, tool_plan: Optional[dict[str, Any]] = None) -> dict[str, str]:
    today = date.today()
    end = today - timedelta(days=1)
    if profile == "weekly_sales_stock":
        days = 7
    elif profile == "daily_exceptions":
        days = 30
    else:
        raw_start = str((tool_plan or {}).get("data_inicio") or "")[:10]
        raw_end = str((tool_plan or {}).get("data_fim") or "")[:10]
        try:
            start_custom = date.fromisoformat(raw_start)
            end_custom = date.fromisoformat(raw_end)
            if start_custom <= end_custom:
                days = (end_custom - start_custom).days + 1
                start = start_custom
                end = end_custom
            else:
                raise ValueError
        except Exception:
            days = 30
            start = end - timedelta(days=days - 1)
        comparison_end = start - timedelta(days=1)
        comparison_start = comparison_end - timedelta(days=days - 1)
        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "comparison_start": comparison_start.isoformat(),
            "comparison_end": comparison_end.isoformat(),
        }
    start = end - timedelta(days=days - 1)
    comparison_end = start - timedelta(days=1)
    comparison_start = comparison_end - timedelta(days=days - 1)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "comparison_start": comparison_start.isoformat(),
        "comparison_end": comparison_end.isoformat(),
    }


def _sales_aggregates(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        store = str(row.get("loja") or "Sem loja")
        sku = str(row.get("sku") or "").strip().upper()
        if not sku:
            continue
        key = (_text_key(store), sku)
        current = result.setdefault(
            key,
            {"store": store, "sku": sku, "product": str(row.get("produto") or ""), "quantity": 0.0, "revenue": 0.0, "orders": set()},
        )
        current["quantity"] += _float(row.get("quantidade"))
        current["revenue"] += _float(row.get("valor"))
        if str(row.get("numero") or ""):
            current["orders"].add(str(row.get("numero")))
        if len(str(row.get("produto") or "")) > len(str(current.get("product") or "")):
            current["product"] = str(row.get("produto") or "")
    return result


def _weekly_demand(rows: list[dict[str, Any]], history_start: date, history_end: date) -> dict[tuple[str, str], list[float]]:
    weeks = max(1, math.ceil(((history_end - history_start).days + 1) / 7))
    result: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        try:
            row_date = date.fromisoformat(str(row.get("data") or "")[:10])
        except Exception:
            continue
        index = min(weeks - 1, max(0, (row_date - history_start).days // 7))
        key = (_text_key(row.get("loja")), str(row.get("sku") or "").strip().upper())
        result.setdefault(key, [0.0] * weeks)[index] += max(0.0, _float(row.get("quantidade")))
    return result


def _assign_abc(rows: list[dict[str, Any]], a_limit: float, b_limit: float) -> None:
    by_store: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_store.setdefault(_text_key(row.get("store")), []).append(row)
    for items in by_store.values():
        total = sum(max(0.0, _float(item.get("revenue"))) for item in items)
        running = 0.0
        for item in sorted(items, key=lambda entry: _float(entry.get("revenue")), reverse=True):
            running += max(0.0, _float(item.get("revenue")))
            cumulative = running / total if total > 0 else 0.0
            item["abc"] = "A" if cumulative <= a_limit or running == _float(item.get("revenue")) else ("B" if cumulative <= b_limit else "C")
            item["revenue_share_pct"] = (_float(item.get("revenue")) / total * 100.0) if total > 0 else 0.0


def _service_z(abc: str, xyz: str, policy: dict[str, Any]) -> tuple[float, float]:
    levels = policy.get("service_levels") if isinstance(policy.get("service_levels"), dict) else {}
    service = _float(levels.get(abc), {"A": 0.975, "B": 0.95, "C": 0.90}.get(abc, 0.90))
    if xyz == "Z":
        service -= _float(policy.get("z_service_reduction"), 0.025)
    service = min(0.999, max(_float(policy.get("minimum_service_level"), 0.85), service))
    return service, NormalDist().inv_cdf(service)


def _round_order_quantity(quantity: float, moq: float, multiple: float) -> int:
    if quantity <= 0:
        return 0
    minimum = max(0.0, moq)
    step = max(1.0, multiple)
    return int(math.ceil(max(quantity, minimum) / step) * step)


def _owner_for(settings: dict[str, Any], action_type: str) -> dict[str, str]:
    owners = (settings.get("global") or {}).get("owners") if isinstance(settings.get("global"), dict) else {}
    owner = owners.get(action_type) if isinstance(owners, dict) else {}
    return {
        "owner_username": str((owner or {}).get("username") or ""),
        "owner_role": str((owner or {}).get("role") or "Operação"),
    }


def _action_id(report_type: str, action_type: str, store: str, skus: list[str]) -> str:
    raw = "|".join([report_type, action_type, store, *sorted(skus)])
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:20]


def _build_action(
    settings: dict[str, Any],
    report_type: str,
    action_type: str,
    title: str,
    store: str,
    skus: list[str],
    impact_brl: Optional[float],
    urgency: str,
    confidence: str,
    evidence: str,
    recommendation: str,
) -> dict[str, Any]:
    due_policy = (settings.get("global") or {}).get("urgency_due_days") if isinstance(settings.get("global"), dict) else {}
    due_defaults = {"immediate": 1, "high": 3, "medium": 7, "low": 14}
    due_days = max(0, int(_float((due_policy or {}).get(urgency), due_defaults.get(urgency, 7))))
    return {
        "action_id": _action_id(report_type, action_type, store, skus),
        "action_type": action_type,
        "title": title,
        "store": store,
        "skus": skus[:100],
        "impact_brl": round(impact_brl, 2) if impact_brl is not None else None,
        "impact_label": f"R$ {impact_brl:,.2f}" if impact_brl is not None else "não estimável",
        "urgency": urgency,
        "confidence": confidence,
        "due_at": (date.today() + timedelta(days=due_days)).isoformat(),
        "evidence": evidence,
        "recommendation": recommendation,
        "queueable": confidence in {"alta", "média"},
        **_owner_for(settings, action_type),
    }


def _manual_advertising(
    info_base: str,
    client_id: str,
    start: str,
    end: str,
    store: str = "",
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    adjustments = codex_assistant_storage.codex_assistant_financial_adjustments_list(
        info_base,
        client_id,
        kind="advertising",
        store=store,
        period_start=start,
        period_end=end,
    )
    # Publicidade so pode ser descontada quando o ajuste representa exatamente
    # o mesmo intervalo do relatorio. Ajustes apenas sobrepostos continuam
    # visiveis na fonte, mas nao entram no resultado financeiro consolidado.
    adjustments = [
        item
        for item in adjustments
        if str(item.get("period_start") or "")[:10] == str(start or "")[:10]
        and str(item.get("period_end") or "")[:10] == str(end or "")[:10]
    ]
    by_store: dict[str, float] = {}
    for item in adjustments:
        key = _text_key(item.get("store") or "Sem loja")
        by_store[key] = by_store.get(key, 0.0) + max(0.0, _float(item.get("amount")))
    return by_store, adjustments


def _confidence(coverage: float, stock_stamp: Optional[str], sample: int, warning_count: int) -> tuple[int, str]:
    coverage_score = min(100.0, max(0.0, coverage * 100.0))
    freshness = 30.0
    try:
        stamp = datetime.fromisoformat(str(stock_stamp or "").replace("Z", "+00:00"))
        age = max(0, (datetime.now(stamp.tzinfo) - stamp).days) if stamp.tzinfo else max(0, (datetime.now() - stamp).days)
        freshness = 100.0 if age <= 1 else 70.0 if age <= 7 else 30.0
    except Exception:
        pass
    sample_score = min(100.0, sample / 30.0 * 100.0)
    consistency = max(0.0, 100.0 - warning_count * 15.0)
    score = int(round(coverage_score * 0.40 + freshness * 0.25 + sample_score * 0.20 + consistency * 0.15))
    return score, "alta" if score >= 85 else "média" if score >= 65 else "baixa"


def _tax_band(revenue_365: float) -> float:
    if revenue_365 <= 180000:
        return 4.0
    if revenue_365 <= 360000:
        return 7.3
    if revenue_365 <= 720000:
        return 9.5
    if revenue_365 <= 1800000:
        return 10.7
    if revenue_365 <= 3600000:
        return 14.3
    return 19.0


def _siscomex(ncm_count: int) -> float:
    count = max(0, int(ncm_count or 0))
    if count == 0:
        return 115.67
    if count <= 2:
        return 115.67 + count * 38.56
    if count <= 5:
        return 192.79 + (count - 2) * 30.85
    if count <= 10:
        return 285.34 + (count - 5) * 23.14
    if count <= 20:
        return 401.04 + (count - 10) * 15.42
    if count <= 50:
        return 555.24 + (count - 20) * 7.71
    return 786.54 + (count - 50) * 3.86


def _load_import_order(tenant: str, list_id: str) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]]]:
    lists = _read_json(os.path.join(tenant, "listas_pedidos.json"), [])
    if not isinstance(lists, list):
        return None, []
    normalized = [normalize_text_tree(item) for item in lists if isinstance(item, dict)]
    selected = next((item for item in normalized if str(item.get("id") or "") == str(list_id or "")), None)
    return selected, normalized


def _landed_cost_analysis(
    tenant: str,
    order: dict[str, Any],
    settings: dict[str, Any],
    inventory_rows: list[dict[str, Any]],
    revenue_365: Optional[float],
) -> dict[str, Any]:
    items = [normalize_text_tree(item) for item in (order.get("itens") or []) if isinstance(item, dict)]
    exchange = _float(order.get("exchange_rate") or order.get("dolar_hoje") or order.get("cotacao_dolar") or order.get("dolar"))
    warnings: list[str] = []
    if exchange <= 0:
        warnings.append("Cotação da compra ausente; custo posto e cenários em reais ficaram indisponíveis.")
    tax_band_pct = _tax_band(revenue_365) if revenue_365 is not None else None
    if tax_band_pct is None:
        warnings.append("Faturamento liquido de 365 dias indisponivel; faixa tributaria e preco minimo ficaram sem valor.")
    prepared: list[dict[str, Any]] = []
    total_fob = 0.0
    total_m3 = 0.0
    total_weight = 0.0
    total_freight = 0.0
    ncm_values: set[str] = set()
    for item in items:
        quantity = max(0.0, _float(item.get("Quantidade") or item.get("quantity")))
        unit_usd = max(0.0, _float(item.get("Valor unidade") or item.get("Cost")))
        total_usd = max(0.0, _float(item.get("Valor total") or item.get("Sub-total(USD)"))) or quantity * unit_usd
        m3 = max(0.0, _float(item.get("M3") or item.get("m3") or item.get("Estimed CBM") or item.get("M3 individual")))
        if m3 and _float(item.get("M3 individual")) > 0 and m3 <= _float(item.get("M3 individual")) + 1e-9:
            m3 *= quantity
        weight = max(0.0, _float(item.get("Peso total") or item.get("weight_kg") or item.get("Peso")))
        if weight and _float(item.get("Peso unitario")) > 0 and weight <= _float(item.get("Peso unitario")) + 1e-9:
            weight *= quantity
        freight_usd = max(0.0, _float(item.get("Frete Internacional")))
        ncm = re.sub(r"\D", "", str(item.get("NCM") or ""))
        if ncm:
            ncm_values.add(ncm)
        prepared.append(
            {
                "sku": str(item.get("SKU") or "").strip().upper(),
                "product": str(item.get("Título do produto em inglês") or item.get("Description") or ""),
                "quantity": quantity,
                "unit_usd": unit_usd,
                "fob_usd": total_usd,
                "m3": m3,
                "weight_kg": weight,
                "freight_usd": freight_usd,
                "ncm": ncm,
                "ii_pct": _float(item.get("II")),
                "ipi_pct": _float(item.get("IPI")),
                "pis_pct": _float(item.get("PIS")),
                "cofins_pct": _float(item.get("COFINS")),
                "icms_pct": _float(item.get("ICMS")),
                "moq": _float(item.get("MOQ") or order.get("moq_default")),
                "package_multiple": _float(item.get("package_multiple") or item.get("Múltiplo embalagem") or order.get("package_multiple_default"), 1),
            }
        )
        total_fob += total_usd
        total_m3 += m3
        total_weight += weight
        total_freight += freight_usd
    if total_m3 <= 0 and prepared:
        warnings.append("Metragem ausente; o rateio usou peso e, quando indisponível, valor FOB.")
    order_freight = max(0.0, _float(order.get("international_freight_usd") or order.get("freight_usd") or order.get("frete_internacional_usd")))
    if total_freight <= 0 and order_freight > 0 and prepared:
        for item in prepared:
            if total_m3 > 0:
                share = item["m3"] / total_m3
            elif total_weight > 0:
                share = item["weight_kg"] / total_weight
            else:
                share = item["fob_usd"] / total_fob if total_fob > 0 else 1.0 / len(prepared)
            item["freight_usd"] = order_freight * share
        total_freight = order_freight
    insurance_usd = (total_fob + total_freight) * 0.0028
    cif_brl = (total_fob + total_freight + insurance_usd) * exchange if exchange > 0 else 0.0
    variable_volume_brl = total_m3 * ((25.47 + 12.74) * exchange + 509.42 + 30.0) if exchange > 0 else 0.0
    fixed_brl = ((80 + 100 + 100 + 351.04 + 350.52) * exchange + 900 + 300 + 1021 + _siscomex(len(ncm_values))) if exchange > 0 else 0.0
    trading_brl = cif_brl * 0.03
    afrmm_brl = total_freight * 0.08 * exchange if exchange > 0 else 0.0
    storage_brl = cif_brl * 0.035
    inventory_by_sku = {str(item.get("sku") or "").upper(): item for item in inventory_rows}
    total_taxes = 0.0
    total_landed = 0.0
    rows: list[dict[str, Any]] = []
    for item in prepared:
        fob_share = item["fob_usd"] / total_fob if total_fob > 0 else (1 / len(prepared) if prepared else 0)
        volume_share = item["m3"] / total_m3 if total_m3 > 0 else fob_share
        insurance_item_usd = insurance_usd * fob_share
        base_brl = (item["fob_usd"] + item["freight_usd"] + insurance_item_usd) * exchange if exchange > 0 else 0.0
        product_brl = item["fob_usd"] * exchange if exchange > 0 else 0.0
        ii = base_brl * item["ii_pct"] / 100.0
        ipi = (product_brl + ii) * item["ipi_pct"] / 100.0
        pis = base_brl * item["pis_pct"] / 100.0
        cofins = base_brl * item["cofins_pct"] / 100.0
        icms = base_brl * item["icms_pct"] / 100.0
        taxes = ii + ipi + pis + cofins + icms
        common = variable_volume_brl * volume_share + (fixed_brl + trading_brl + afrmm_brl + storage_brl) * fob_share
        landed = base_brl + taxes + common
        landed_unit = landed / item["quantity"] if item["quantity"] > 0 else None
        current_inventory = inventory_by_sku.get(item["sku"]) or {}
        policy = _resolve_policy(settings, str(order.get("loja") or ""), str(order.get("supplier") or order.get("fornecedor") or ""), item["sku"])
        target_margin = _float(policy.get("target_margin_pct"), 20.0) / 100.0
        sales_tax = tax_band_pct / 100.0 if tax_band_pct is not None else None
        denominator = 1.0 - sales_tax - target_margin if sales_tax is not None else None
        minimum_price = landed_unit / denominator if landed_unit is not None and denominator is not None and denominator > 0 else None
        sale_price = _float(current_inventory.get("sale_price"))
        margin_pct = ((sale_price - landed_unit) / sale_price * 100.0) if sale_price > 0 and landed_unit is not None else None
        coverage_days = current_inventory.get("coverage_days")
        lead_days = max(0, int(_float(policy.get("lead_time_days"), _float(policy.get("default_lead_time_days"), 180))))
        safety_days = max(0, int(_float(policy.get("safety_buffer_days"), 30)))
        next_order_deadline = None
        if coverage_days is not None:
            days_until_order = max(0, int(math.floor(_float(coverage_days) - lead_days - safety_days)))
            next_order_deadline = (date.today() + timedelta(days=days_until_order)).isoformat()
        total_taxes += taxes
        total_landed += landed
        rows.append(
            {
                **item,
                "insurance_usd": round(insurance_item_usd, 2),
                "customs_value_brl": round(base_brl, 2) if exchange > 0 else None,
                "taxes_brl": round(taxes, 2) if exchange > 0 else None,
                "common_costs_brl": round(common, 2) if exchange > 0 else None,
                "landed_total_brl": round(landed, 2) if exchange > 0 else None,
                "landed_unit_brl": round(landed_unit, 2) if landed_unit is not None else None,
                "minimum_sale_price_brl": round(minimum_price, 2) if minimum_price is not None else None,
                "sale_price_brl": round(sale_price, 2) if sale_price > 0 else None,
                "current_margin_pct": round(margin_pct, 2) if margin_pct is not None else None,
                "lead_time_days": lead_days,
                "safety_buffer_days": safety_days,
                "next_order_deadline": next_order_deadline,
            }
        )
    if rows and exchange > 0:
        expected_total = round(total_landed, 2)
        allocated_total = round(sum(_float(item.get("landed_total_brl")) for item in rows), 2)
        residual = round(expected_total - allocated_total, 2)
        if residual:
            last = rows[-1]
            last["common_costs_brl"] = round(_float(last.get("common_costs_brl")) + residual, 2)
            last["landed_total_brl"] = round(_float(last.get("landed_total_brl")) + residual, 2)
            if _float(last.get("quantity")) > 0:
                last["landed_unit_brl"] = round(_float(last.get("landed_total_brl")) / _float(last.get("quantity")), 2)
                policy = _resolve_policy(settings, str(order.get("loja") or ""), str(order.get("supplier") or order.get("fornecedor") or ""), str(last.get("sku") or ""))
                denominator = 1.0 - (tax_band_pct / 100.0) - (_float(policy.get("target_margin_pct"), 20.0) / 100.0) if tax_band_pct is not None else None
                last["minimum_sale_price_brl"] = round(_float(last.get("landed_unit_brl")) / denominator, 2) if denominator is not None and denominator > 0 else None
    global_cfg = settings.get("global") or {}
    scenarios: list[dict[str, Any]] = []
    if exchange > 0:
        base_cash = total_landed
        total_quantity = sum(_float(item.get("quantity")) for item in rows)
        current_sales_value = sum(_float(item.get("sale_price_brl")) * _float(item.get("quantity")) for item in rows)
        target_margin = _float(global_cfg.get("target_margin_pct"), 20.0) / 100.0
        sales_tax = tax_band_pct / 100.0 if tax_band_pct is not None else None
        denominator = 1.0 - sales_tax - target_margin if sales_tax is not None else None
        deadlines = [date.fromisoformat(str(item.get("next_order_deadline"))) for item in rows if item.get("next_order_deadline")]
        base_deadline = min(deadlines) if deadlines else None

        def scenario(kind: str, cash: float, *, change_pct: Optional[float] = None, delay_days: int = 0) -> dict[str, Any]:
            unit_landed = cash / total_quantity if total_quantity > 0 else None
            minimum_price = unit_landed / denominator if unit_landed is not None and denominator is not None and denominator > 0 else None
            margin = (current_sales_value - cash) / current_sales_value * 100.0 if current_sales_value > 0 else None
            deadline = base_deadline - timedelta(days=delay_days) if base_deadline else None
            payload = {
                "kind": kind,
                "cash_required_brl": round(cash, 2),
                "minimum_sale_price_brl": round(minimum_price, 2) if minimum_price is not None else None,
                "estimated_margin_pct": round(margin, 2) if margin is not None else None,
                "next_order_deadline": deadline.isoformat() if deadline else None,
            }
            if change_pct is not None:
                payload["change_pct"] = round(change_pct, 2)
            if delay_days:
                payload["delay_days"] = int(delay_days)
            return payload

        scenarios.append(scenario("baseline", base_cash))
        for pct in global_cfg.get("scenario_exchange_pct") or [5, 10]:
            scenarios.append(scenario("exchange", base_cash * (1 + _float(pct) / 100.0), change_pct=_float(pct)))
        freight_landed = total_freight * exchange
        for pct in global_cfg.get("scenario_freight_pct") or [10, 20]:
            scenarios.append(scenario("freight", base_cash + freight_landed * _float(pct) / 100.0, change_pct=_float(pct)))
        for delay in global_cfg.get("scenario_delay_days") or [15, 30]:
            scenarios.append(scenario("delay", base_cash, delay_days=int(_float(delay))))
    return {
        "order_id": str(order.get("id") or ""),
        "order_name": str(order.get("nome_lista") or ""),
        "store": str(order.get("loja") or ""),
        "supplier": str(order.get("supplier") or order.get("fornecedor") or ""),
        "currency": str(order.get("currency") or order.get("moeda") or "USD"),
        "incoterm": str(order.get("incoterm") or ""),
        "exchange_rate": exchange if exchange > 0 else None,
        "tax_band_pct": tax_band_pct,
        "rolling_365_revenue_brl": round(revenue_365, 2) if revenue_365 is not None else None,
        "fob_usd": round(total_fob, 2),
        "freight_usd": round(total_freight, 2),
        "insurance_usd": round(insurance_usd, 2),
        "taxes_brl": round(total_taxes, 2) if exchange > 0 else None,
        "common_costs_brl": round(variable_volume_brl + fixed_brl + trading_brl + afrmm_brl + storage_brl, 2) if exchange > 0 else None,
        "cash_required_brl": round(total_landed, 2) if exchange > 0 else None,
        "items": rows,
        "scenarios": scenarios,
        "warnings": warnings,
    }


def _supplier_performance(all_orders: list[dict[str, Any]], supplier: str) -> dict[str, Any]:
    supplier_key = _text_key(supplier)
    completed: list[dict[str, Any]] = []
    for order in all_orders:
        if supplier_key and _text_key(order.get("supplier") or order.get("fornecedor")) != supplier_key:
            continue
        if _pipeline_bucket(order.get("status")) != "received":
            continue
        try:
            ordered = date.fromisoformat(str(order.get("order_date") or order.get("created_at") or "")[:10])
            received = date.fromisoformat(str(order.get("received_at") or order.get("updated_at") or "")[:10])
        except Exception:
            continue
        promised_raw = str(order.get("promised_delivery_date") or order.get("eta_date") or "")[:10]
        try:
            promised = date.fromisoformat(promised_raw)
        except Exception:
            promised = None
        quantity = sum(_float(item.get("Quantidade")) for item in order.get("itens") or [] if isinstance(item, dict))
        received_qty = sum(_float(item.get("received_quantity", item.get("Quantidade"))) for item in order.get("itens") or [] if isinstance(item, dict))
        defective = sum(_float(item.get("defective_quantity")) for item in order.get("itens") or [] if isinstance(item, dict))
        order_cost = sum(
            _float(item.get("Valor total") or item.get("Sub-total(USD)"))
            or (_float(item.get("Quantidade")) * _float(item.get("Valor unidade") or item.get("Cost")))
            for item in order.get("itens") or []
            if isinstance(item, dict)
        )
        completed.append(
            {
                "lead_time_days": (received - ordered).days,
                "on_time": promised is not None and received <= promised,
                "in_full": quantity > 0 and received_qty >= quantity * 0.95,
                "quantity": quantity,
                "defective": defective,
                "unit_cost": order_cost / quantity if order_cost > 0 and quantity > 0 else None,
            }
        )
    if len(completed) < 3:
        return {"status": "insufficient", "orders": len(completed), "message": "Menos de três pedidos recebidos com datas válidas."}
    total_qty = sum(item["quantity"] for item in completed)
    costs = [item["unit_cost"] for item in completed if item.get("unit_cost") is not None]
    mean_cost = statistics.mean(costs) if costs else 0.0
    return {
        "status": "available",
        "orders": len(completed),
        "average_lead_time_days": round(statistics.mean(item["lead_time_days"] for item in completed), 1),
        "otif_pct": round(sum(1 for item in completed if item["on_time"] and item["in_full"]) / len(completed) * 100.0, 1),
        "defect_pct": round(sum(item["defective"] for item in completed) / total_qty * 100.0, 2) if total_qty > 0 else None,
        "cost_variation_pct": round(statistics.pstdev(costs) / mean_cost * 100.0, 2) if len(costs) > 1 and mean_cost > 0 else None,
    }


def build_profile_context(
    *,
    info_base: str,
    client_id: str,
    profile: str,
    store: str = "",
    import_list_id: str = "",
    context: Optional[dict[str, Any]] = None,
    margin_rows: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    profile = profile if profile in REPORT_PROFILES else "custom"
    context = context if isinstance(context, dict) else {}
    settings = report_settings_get(info_base, client_id)
    tool_plan = context.get("tool_plan") if isinstance(context.get("tool_plan"), dict) else {}
    period = _period_for_profile(profile, tool_plan)
    start = date.fromisoformat(period["start"])
    end = date.fromisoformat(period["end"])
    comparison_start = date.fromisoformat(period["comparison_start"])
    comparison_end = date.fromisoformat(period["comparison_end"])
    tenant = _tenant_path(info_base, client_id)
    sales_source_available = _tenant_has_table(tenant, "vendas")
    returns_source_available = _tenant_has_table(tenant, "notas_entrada_itens")
    stock_source_available = _sqlite_has_table(os.path.join(tenant, "estoque_historico.db"), "estoque_historico")
    pipeline_source_available = isinstance(_read_json(os.path.join(tenant, "listas_pedidos.json"), None), list)
    selected_store = str(store or tool_plan.get("loja") or "").strip()
    if _text_key(selected_store) in {_text_key("todas"), _text_key("todas as lojas"), _text_key("__todas")}:
        selected_store = ""
    sales, sales_warnings = _load_sales_rows(tenant, start, end, selected_store)
    previous_sales, previous_warnings = _load_sales_rows(tenant, comparison_start, comparison_end, selected_store)
    returns, return_warnings = _load_return_rows(tenant, start, end, selected_store)
    stock, stock_stamp, stock_warnings = _load_latest_stock(tenant, selected_store)
    opening_stock, opening_stock_stamp = _load_opening_stock(tenant, start, selected_store)
    received_units, received_units_available = _load_received_units(tenant, start, end, selected_store)
    pipeline, pipeline_rows, pipeline_warnings = _load_purchase_pipeline(tenant, selected_store)
    global_cfg = settings.get("global") or {}
    history_end = end
    history_start = history_end - timedelta(days=int(global_cfg.get("demand_history_days") or 180) - 1)
    history_sales, history_warnings = _load_sales_rows(tenant, history_start, history_end, selected_store)
    if not sales_source_available:
        sales_warnings.append("Historico de vendas indisponivel; faturamento e quantidade nao foram interpretados como zero.")
    if not returns_source_available:
        return_warnings.append("Historico de devolucoes indisponivel; devolucoes e receita liquida ficaram sem valor.")
    current = _sales_aggregates(sales)
    previous = _sales_aggregates(previous_sales)
    weekly = _weekly_demand(history_sales, history_start, history_end)
    by_store_cost, generic_cost = _load_cost_maps(tenant)
    sales_rows: list[dict[str, Any]] = []
    for key, aggregate in current.items():
        prior = previous.get(key) or {}
        cost_data = _cost_for(by_store_cost, generic_cost, aggregate["store"], aggregate["sku"])
        current_revenue = _float(aggregate.get("revenue"))
        prior_revenue = _float(prior.get("revenue"))
        trend = ((current_revenue - prior_revenue) / prior_revenue * 100.0) if prior_revenue > 0 else None
        sales_rows.append(
            {
                **aggregate,
                "orders": len(aggregate.get("orders") or []),
                "revenue": round(current_revenue, 2),
                "quantity": round(_float(aggregate.get("quantity")), 2),
                "previous_revenue": round(prior_revenue, 2),
                "trend_pct": round(trend, 2) if trend is not None else None,
                "unit_cost": _float(cost_data.get("cost")) if cost_data.get("cost") is not None else None,
                "tax_pct": cost_data.get("tax_pct") if cost_data.get("tax_pct") not in (None, "") else None,
                "sale_price": _float(cost_data.get("price")) or (current_revenue / _float(aggregate.get("quantity")) if _float(aggregate.get("quantity")) > 0 else None),
            }
        )
    _assign_abc(sales_rows, _float(global_cfg.get("abc_a_limit"), 0.80), _float(global_cfg.get("abc_b_limit"), 0.95))
    return_by_store: dict[str, float] = {}
    for item in returns:
        key = _text_key(item.get("loja"))
        return_by_store[key] = return_by_store.get(key, 0.0) + max(0.0, _float(item.get("valor")))
    ads_by_store, ad_adjustments = _manual_advertising(info_base, client_id, period["start"], period["end"], selected_store)
    inventory_rows: list[dict[str, Any]] = []
    all_inventory_keys = set(current) | set(stock)
    for key in all_inventory_keys:
        sale = current.get(key) or {}
        stock_item = stock.get(key) or {}
        store_name = str(sale.get("store") or stock_item.get("loja") or "Sem loja")
        sku = str(sale.get("sku") or stock_item.get("sku") or "").upper()
        cost_data = _cost_for(by_store_cost, generic_cost, store_name, sku)
        demand = weekly.get(key) or []
        min_samples = int(global_cfg.get("weekly_min_samples") or 8)
        mean_weekly = statistics.mean(demand) if demand else 0.0
        std_weekly = statistics.pstdev(demand) if len(demand) > 1 else 0.0
        cv = std_weekly / mean_weekly if mean_weekly > 0 else None
        if len(demand) < min_samples:
            xyz = "insuficiente"
        elif cv is None:
            xyz = "Z"
        elif cv <= _float(global_cfg.get("xyz_x_limit"), 0.50):
            xyz = "X"
        elif cv <= _float(global_cfg.get("xyz_y_limit"), 1.00):
            xyz = "Y"
        else:
            xyz = "Z"
        abc = str(sale.get("abc") or "C")
        supplier = str((settings.get("skus") or {}).get(sku, {}).get("supplier") or "") if isinstance(settings.get("skus"), dict) else ""
        policy = _resolve_policy(settings, store_name, supplier, sku)
        service, z_value = _service_z(abc, xyz, policy)
        lead_days = max(0.0, _float(policy.get("lead_time_days"), _float(policy.get("default_lead_time_days"), 180)))
        review_days = max(0.0, _float(policy.get("review_cycle_days"), 90))
        average_daily = mean_weekly / 7.0
        std_daily = std_weekly / math.sqrt(7.0) if std_weekly > 0 else 0.0
        safety_stock = z_value * std_daily * math.sqrt(lead_days) if lead_days > 0 else 0.0
        reorder_point = average_daily * lead_days + safety_stock
        target_stock = average_daily * (lead_days + review_days) + safety_stock
        stock_known = key in stock
        local_stock = max(0.0, _float(stock_item.get("saldo_loja"))) if stock_known else None
        full_stock = max(0.0, _float(stock_item.get("saldo_full"))) if stock_known else None
        pipeline_values = pipeline.get(key) or pipeline.get((_text_key("__todas"), sku)) or {"open": 0.0, "production": 0.0, "transit": 0.0, "customs": 0.0, "received": 0.0}
        confirmed_pipeline = sum(_float(pipeline_values.get(bucket)) for bucket in ("open", "production", "transit", "customs")) if pipeline_source_available else None
        position = local_stock + confirmed_pipeline if local_stock is not None and confirmed_pipeline is not None else None
        planning_known = sales_source_available and position is not None
        raw_purchase = max(0.0, target_stock - position) if planning_known else None
        moq = max(0.0, _float(policy.get("moq")))
        multiple = max(1.0, _float(policy.get("package_multiple"), 1))
        suggested_purchase = _round_order_quantity(raw_purchase, moq, multiple) if raw_purchase is not None else None
        coverage_days = local_stock / average_daily if local_stock is not None and sales_source_available and average_daily > 0 else None
        stockout_date = (date.today() + timedelta(days=max(0, math.ceil(coverage_days)))).isoformat() if coverage_days is not None else None
        excess_qty = max(0.0, position - target_stock) if planning_known else None
        unit_cost = _float(cost_data.get("cost")) if cost_data.get("cost") is not None else None
        capital_tied = excess_qty * unit_cost if excess_qty is not None and unit_cost is not None else None
        opening_available = key in opening_stock
        opening_local_stock = opening_stock.get(key)
        received_in_period = received_units.get(key) if received_units_available else None
        flow_complete = opening_available and received_units_available
        units_sold = max(0.0, _float(sale.get("quantity")))
        units_available = (_float(opening_local_stock) + _float(received_in_period)) if flow_complete else 0.0
        sell_through = units_sold / units_available * 100.0 if flow_complete and units_available > 0 else None
        average_inventory_units = (_float(opening_local_stock) + local_stock) / 2.0 if opening_available and local_stock is not None else None
        turnover = units_sold / average_inventory_units if average_inventory_units and average_inventory_units > 0 else None
        gross_profit = (_float(sale.get("revenue")) - units_sold * unit_cost) if unit_cost is not None else None
        average_inventory_value = average_inventory_units * unit_cost if average_inventory_units is not None and unit_cost is not None else None
        gmroi = gross_profit / average_inventory_value if gross_profit is not None and average_inventory_value and average_inventory_value > 0 else None
        inventory_rows.append(
            {
                "store": store_name,
                "sku": sku,
                "product": str(sale.get("product") or stock_item.get("nome_bling") or cost_data.get("product") or ""),
                "abc": abc,
                "xyz": xyz,
                "coefficient_variation": round(cv, 4) if cv is not None else None,
                "service_level_pct": round(service * 100.0, 2),
                "average_daily": round(average_daily, 4) if sales_source_available else None,
                "local_stock": round(local_stock, 2) if local_stock is not None else None,
                "full_stock": round(full_stock, 2) if full_stock is not None else None,
                "open_purchase": round(_float(pipeline_values.get("open")), 2) if pipeline_source_available else None,
                "production": round(_float(pipeline_values.get("production")), 2) if pipeline_source_available else None,
                "transit": round(_float(pipeline_values.get("transit")), 2) if pipeline_source_available else None,
                "customs": round(_float(pipeline_values.get("customs")), 2) if pipeline_source_available else None,
                "received": round(_float(pipeline_values.get("received")), 2) if pipeline_source_available else None,
                "coverage_days": round(coverage_days, 1) if coverage_days is not None else None,
                "safety_stock": round(safety_stock, 2) if sales_source_available else None,
                "reorder_point": round(reorder_point, 2) if sales_source_available else None,
                "target_stock": round(target_stock, 2) if sales_source_available else None,
                "stockout_date": stockout_date,
                "suggested_purchase": suggested_purchase,
                "moq": moq,
                "package_multiple": multiple,
                "excess_quantity": round(excess_qty, 2) if excess_qty is not None else None,
                "capital_tied_brl": round(capital_tied, 2) if capital_tied is not None else None,
                "unit_cost": unit_cost,
                "tax_pct": cost_data.get("tax_pct") if cost_data.get("tax_pct") not in (None, "") else None,
                "sale_price": sale.get("sale_price"),
                "opening_stock_date": opening_stock_stamp,
                "opening_local_stock": round(opening_local_stock, 2) if opening_local_stock is not None else None,
                "received_in_period": round(received_in_period, 2) if received_in_period is not None else None,
                "sell_through_pct": round(sell_through, 2) if sell_through is not None else None,
                "inventory_turnover": round(turnover, 4) if turnover is not None else None,
                "gmroi": round(gmroi, 4) if gmroi is not None else None,
            }
        )
    gross_revenue = sum(_float(item.get("revenue")) for item in sales_rows)
    returned_value = sum(_float(item.get("valor")) for item in returns)
    net_revenue = gross_revenue - returned_value if sales_source_available and returns_source_available else None
    cost_covered_revenue = sum(_float(item.get("revenue")) for item in sales_rows if item.get("unit_cost") is not None)
    margin_rows = margin_rows if isinstance(margin_rows, list) else []
    raw_complete_value = sum(_float(item.get("_valor_num")) for item in margin_rows if item.get("_margem_completa"))
    raw_complete_profit = sum(_float(item.get("_lucro_num")) for item in margin_rows if item.get("_margem_completa"))
    complete_scale = min(1.0, gross_revenue / raw_complete_value) if gross_revenue > 0 and raw_complete_value > 0 else 1.0
    complete_value = raw_complete_value * complete_scale
    complete_profit = raw_complete_profit * complete_scale
    complete_ratio = complete_value / gross_revenue if sales_source_available and gross_revenue > 0 else None
    cost_ratio = cost_covered_revenue / gross_revenue if sales_source_available and gross_revenue > 0 else None
    threshold = _float(global_cfg.get("margin_coverage_min"), 0.95)
    before_ads_valid = bool(sales_source_available and gross_revenue > 0 and complete_ratio is not None and complete_ratio >= threshold)
    advertising_total = sum(ads_by_store.values())
    stores_with_revenue = {_text_key(item.get("store")) for item in sales_rows if _float(item.get("revenue")) > 0}
    ads_coverage = (1.0 if stores_with_revenue.issubset(set(ads_by_store)) else 0.0) if stores_with_revenue else None
    after_ads_valid = bool(before_ads_valid and ads_coverage is not None and ads_coverage >= threshold)
    warnings = list(dict.fromkeys(sales_warnings + previous_warnings + return_warnings + stock_warnings + pipeline_warnings + history_warnings + list(context.get("warnings") or [])))
    coverage_score = ((_float(cost_ratio) if cost_ratio is not None else 0.0) + (1.0 if stock_source_available else 0.0) + (1.0 if sales_source_available else 0.0)) / 3.0
    score, confidence_label = _confidence(min(1.0, coverage_score), stock_stamp, len(sales), len(warnings))
    financial_coverage = {
        "minimum_required_pct": threshold * 100.0,
        "cost_by_revenue_pct": round(cost_ratio * 100.0, 2) if cost_ratio is not None else None,
        "complete_margin_by_revenue_pct": round(complete_ratio * 100.0, 2) if complete_ratio is not None else None,
        "advertising_pct": round(ads_coverage * 100.0, 2) if ads_coverage is not None else None,
        "contribution_margin_valid": before_ads_valid,
        "net_margin_after_ads_valid": after_ads_valid,
        "covered_revenue_brl": round(complete_value, 2) if sales_source_available else None,
        "covered_profit_brl": round(complete_profit, 2) if complete_value > 0 else None,
        "consolidated_profit_brl": round(complete_profit, 2) if before_ads_valid else None,
        "consolidated_margin_pct": round(complete_profit / complete_value * 100.0, 2) if before_ads_valid and complete_value > 0 else None,
        "net_profit_after_ads_brl": round(complete_profit - advertising_total, 2) if after_ads_valid else None,
        "status": "reliable" if after_ads_valid else "partial" if sales_source_available and complete_value > 0 else "insufficient",
    }
    stores = sorted({str(item.get("store") or "Sem loja") for item in sales_rows} | {str(item.get("loja") or "Sem loja") for item in stock.values()})
    store_summaries: list[dict[str, Any]] = []
    for store_name in stores:
        key = _text_key(store_name)
        store_sales = [item for item in sales_rows if _text_key(item.get("store")) == key]
        store_revenue = sum(_float(item.get("revenue")) for item in store_sales)
        store_previous = sum(_float(item.get("previous_revenue")) for item in store_sales)
        target_monthly = _float(((settings.get("stores") or {}).get(store_name) or (settings.get("stores") or {}).get(key) or {}).get("monthly_sales_target_brl"))
        period_days = (end - start).days + 1
        target_period = target_monthly * period_days / 30.0 if target_monthly > 0 else None
        store_summaries.append(
            {
                "store": store_name,
                "gross_revenue_brl": round(store_revenue, 2) if sales_source_available else None,
                "returns_brl": round(return_by_store.get(key, 0.0), 2) if returns_source_available else None,
                "net_revenue_brl": round(store_revenue - return_by_store.get(key, 0.0), 2) if sales_source_available and returns_source_available else None,
                "orders": sum(int(item.get("orders") or 0) for item in store_sales) if sales_source_available else None,
                "units": round(sum(_float(item.get("quantity")) for item in store_sales), 2) if sales_source_available else None,
                "previous_revenue_brl": round(store_previous, 2) if sales_source_available else None,
                "trend_pct": round((store_revenue - store_previous) / store_previous * 100.0, 2) if sales_source_available and store_previous > 0 else None,
                "target_brl": round(target_period, 2) if target_period is not None else None,
                "target_attainment_pct": round(store_revenue / target_period * 100.0, 2) if sales_source_available and target_period else None,
                "advertising_brl": round(ads_by_store.get(key, 0.0), 2) if key in ads_by_store else None,
            }
        )
    top_actions: list[dict[str, Any]] = []
    risky = sorted(
        [item for item in inventory_rows if item.get("coverage_days") is not None and _float(item.get("coverage_days")) <= 15 and _float(item.get("average_daily")) > 0],
        key=lambda item: _float(item.get("coverage_days")),
    )
    for item in risky[:3]:
        revenue_at_risk = _float(item.get("average_daily")) * 7.0 * _float(item.get("sale_price"))
        action = _build_action(
                settings,
                profile,
                "replenishment",
                f"Repor SKU {item.get('sku')} antes da ruptura",
                str(item.get("store") or ""),
                [str(item.get("sku") or "")],
                revenue_at_risk if revenue_at_risk > 0 else None,
                "immediate" if _float(item.get("coverage_days")) <= 7 else "high",
                confidence_label,
                f"Cobertura local de {item.get('coverage_days')} dia(s); Full separado: {item.get('full_stock')} un.",
                f"Criar lista interna com {item.get('suggested_purchase')} unidade(s), respeitando MOQ e embalagem.",
            )
        action["items"] = [{"sku": str(item.get("sku") or ""), "quantity": int(item.get("suggested_purchase") or 0)}]
        top_actions.append(action)
    excess = sorted([item for item in inventory_rows if _float(item.get("excess_quantity")) > 0], key=lambda item: _float(item.get("capital_tied_brl")), reverse=True)
    if excess and len(top_actions) < 5:
        item = excess[0]
        top_actions.append(
            _build_action(
                settings,
                profile,
                "liquidation",
                f"Revisar excesso do SKU {item.get('sku')}",
                str(item.get("store") or ""),
                [str(item.get("sku") or "")],
                _float(item.get("capital_tied_brl")) if item.get("capital_tied_brl") is not None else None,
                "medium",
                confidence_label,
                f"Excesso estimado de {item.get('excess_quantity')} unidade(s).",
                "Criar fila interna para liquidar, montar kit, revisar preço ou pausar nova compra.",
            )
        )
    declining = sorted(
        [item for item in sales_rows if item.get("trend_pct") is not None and _float(item.get("trend_pct")) <= -20.0],
        key=lambda item: _float(item.get("previous_revenue")) - _float(item.get("revenue")),
        reverse=True,
    )
    if declining and len(top_actions) < 5:
        item = declining[0]
        lost_revenue = max(0.0, _float(item.get("previous_revenue")) - _float(item.get("revenue")))
        top_actions.append(
            _build_action(
                settings,
                profile,
                "price_review",
                f"Revisar preco e competitividade do SKU {item.get('sku')}",
                str(item.get("store") or ""),
                [str(item.get("sku") or "")],
                lost_revenue if lost_revenue > 0 else None,
                "high" if _float(item.get("trend_pct")) <= -40 else "medium",
                confidence_label,
                f"Faturamento caiu {abs(_float(item.get('trend_pct'))):.1f}% contra o periodo anterior.",
                "Criar fila interna para revisar preco, tarifa, frete, anuncio e posicionamento sem alterar o marketplace.",
            )
        )
    if not sales_source_available and len(top_actions) < 5:
        top_actions.append(
            _build_action(
                settings,
                profile,
                "data_quality",
                "Restabelecer o historico de vendas",
                selected_store or "Todas as lojas",
                [],
                None,
                "immediate",
                "alta",
                "A fonte de vendas nao estava disponivel; ausencia de registros nao foi interpretada como faturamento zero.",
                "Verificar sincronizacao, arquivo SQLite e escopo da loja antes de tomar decisoes financeiras.",
            )
        )
    elif cost_ratio is not None and cost_ratio < threshold and len(top_actions) < 5:
        missing = [item.get("sku") for item in sales_rows if item.get("unit_cost") is None][:100]
        top_actions.append(
            _build_action(
                settings,
                profile,
                "data_quality",
                "Completar custos antes de decidir margem",
                selected_store or "Todas as lojas",
                [str(item) for item in missing if item],
                None,
                "high",
                "alta",
                f"Cobertura de custo ponderada pelo faturamento: {cost_ratio * 100.0:.1f}%.",
                "Cadastrar custo, imposto, frete e tarifa dos SKUs de maior faturamento.",
            )
        )
    import_analysis = None
    supplier_performance = None
    if profile == "import_order":
        order, all_orders = _load_import_order(tenant, import_list_id)
        if order is None:
            warnings.append("Pedido de importação não encontrado.")
        else:
            rolling_start = end - timedelta(days=364)
            rolling_store = str(order.get("loja") or selected_store or "")
            rolling_sales, rolling_warnings = _load_sales_rows(tenant, rolling_start, end, rolling_store)
            rolling_returns, rolling_return_warnings = _load_return_rows(tenant, rolling_start, end, rolling_store)
            warnings.extend(rolling_warnings + rolling_return_warnings)
            rolling_revenue = (
                max(
                    0.0,
                    sum(_float(item.get("valor")) for item in rolling_sales)
                    - sum(_float(item.get("valor")) for item in rolling_returns),
                )
                if sales_source_available and returns_source_available
                else None
            )
            import_analysis = _landed_cost_analysis(tenant, order, settings, inventory_rows, rolling_revenue)
            warnings.extend(import_analysis.get("warnings") or [])
            supplier_performance = _supplier_performance(all_orders, str(order.get("supplier") or order.get("fornecedor") or ""))
    source_health = [
        {"source": "Movimentacao de estoque", "status": "ok" if opening_stock and received_units_available else "partial" if stock_source_available or received_units_available else "unavailable", "records": len(received_units), "last_sync_at": opening_stock_stamp},
        {"source": "Histórico de vendas", "status": "ok" if sales_source_available else "unavailable", "records": len(sales), "last_sync_at": period["end"] if sales_source_available else None},
        {"source": "Histórico de devoluções", "status": "ok" if returns_source_available else "unavailable", "records": len(returns), "last_sync_at": period["end"] if returns_source_available else None},
        {"source": "Histórico de estoque", "status": "ok" if stock_source_available else "unavailable", "records": len(stock), "last_sync_at": stock_stamp},
        {"source": "Cadastro de custos", "status": "ok" if by_store_cost or generic_cost else "unavailable", "records": len(by_store_cost) + len(generic_cost), "coverage_pct": round(cost_ratio * 100.0, 2) if cost_ratio is not None else None},
        {"source": "Publicidade", "status": "manual" if ad_adjustments else "unavailable", "records": len(ad_adjustments), "coverage_pct": round(ads_coverage * 100.0, 2) if ads_coverage is not None else None},
        {"source": "Pedidos e importações", "status": "ok" if pipeline_source_available else "unavailable", "records": len(pipeline_rows)},
    ]
    return normalize_text_tree(
        {
            "report_type": profile,
            "scope": {
                "period_start": period["start"],
                "period_end": period["end"],
                "comparison_start": period["comparison_start"],
                "comparison_end": period["comparison_end"],
                "stores": stores,
                "selected_store": selected_store or None,
                "stock_as_of": stock_stamp,
                "generated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "data_quality": {
                "status": "reliable" if confidence_label == "alta" else "partial" if confidence_label == "média" else "insufficient",
                "score": score,
                "confidence": confidence_label,
                "source_health": source_health,
                "warnings": warnings[:50],
            },
            "financial_coverage": financial_coverage,
            "financial_summary": {
                "gross_revenue_brl": round(gross_revenue, 2) if sales_source_available else None,
                "returns_brl": round(returned_value, 2) if returns_source_available else None,
                "net_revenue_brl": round(net_revenue, 2) if net_revenue is not None else None,
                "advertising_brl": round(advertising_total, 2) if ad_adjustments else None,
                "contribution_profit_brl": financial_coverage.get("consolidated_profit_brl"),
                "net_profit_after_ads_brl": financial_coverage.get("net_profit_after_ads_brl"),
            },
            "store_summaries": store_summaries,
            "sales_rows": sorted(sales_rows, key=lambda item: _float(item.get("revenue")), reverse=True),
            "inventory_rows": sorted(inventory_rows, key=lambda item: (_float(item.get("coverage_days"), 999999), -_float(item.get("revenue")))),
            "purchase_pipeline_rows": pipeline_rows,
            "import_analysis": import_analysis,
            "supplier_performance": supplier_performance,
            "top_actions": top_actions[:5],
        }
    )


def apply_marketplace_commercial(
    context: dict[str, Any],
    marketplace_commercial: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Apply a read-only ML snapshot to an already-built report context.

    This function is deliberately pure: it performs no network or filesystem
    access. Current listing contribution and reconciled historical result stay
    separate, and missing components never become zero.
    """

    output = deepcopy(context if isinstance(context, dict) else {})
    snapshot = deepcopy(marketplace_commercial if isinstance(marketplace_commercial, dict) else {})
    listing_values = [row for row in snapshot.get("listing_rows") or [] if isinstance(row, dict)]
    ledger_values = [row for row in snapshot.get("ledger_rows") or [] if isinstance(row, dict)]

    cost_index: dict[tuple[str, str], dict[str, Any]] = {}
    global_cost: dict[str, dict[str, Any]] = {}
    for source_name in ("sales_rows", "inventory_rows"):
        for row in output.get(source_name) or []:
            if not isinstance(row, dict):
                continue
            sku = str(row.get("sku") or "").strip().upper()
            if not sku:
                continue
            values = {
                "cost": row.get("unit_cost"),
                "tax_pct": row.get("tax_pct"),
                "product": row.get("product"),
            }
            store_key = _text_key(row.get("store"))
            current = cost_index.setdefault((store_key, sku), {})
            for key, value in values.items():
                if current.get(key) in (None, "") and value not in (None, ""):
                    current[key] = value
            if not store_key or _text_key(row.get("cost_scope")) == "global":
                generic = global_cost.setdefault(sku, {})
                for key, value in values.items():
                    if generic.get(key) in (None, "") and value not in (None, ""):
                        generic[key] = value

    listing_margin_rows: list[dict[str, Any]] = []
    component_complete = {"price": 0, "cost": 0, "tax": 0, "fee": 0, "shipping": 0}
    for raw in listing_values:
        store = str(raw.get("store") or raw.get("loja") or "").strip()
        sku = str(raw.get("sku") or raw.get("seller_sku") or "").strip().upper()
        exact_cost = cost_index.get((_text_key(store), sku)) or {}
        fallback_cost = global_cost.get(sku) or {}
        cost_data = dict(fallback_cost)
        cost_data.update({key: value for key, value in exact_cost.items() if value not in (None, "")})
        cost_scope = "store" if exact_cost.get("cost") not in (None, "") else "global" if fallback_cost.get("cost") not in (None, "") else "unavailable"
        tax_scope = "store" if exact_cost.get("tax_pct") not in (None, "") else "global" if fallback_cost.get("tax_pct") not in (None, "") else "unavailable"
        margin_input = {
            **raw,
            "price": raw.get("price") if raw.get("price") is not None else raw.get("current_price"),
            "sale_fee_amount": raw.get("sale_fee_amount") if raw.get("sale_fee_amount") is not None else raw.get("fee_amount"),
            "shipping_seller_cost": raw.get("shipping_seller_cost") if raw.get("shipping_seller_cost") is not None else raw.get("seller_shipping_cost"),
        }
        margin = margem_calcular_anuncio(
            margin_input,
            sku_hint=sku,
            custo=cost_data.get("cost"),
            imposto_rate=cost_data.get("tax_pct"),
            require_shipping=True,
        )
        missing = [str(item) for item in margin.get("faltando_margem") or []]
        if "preco" not in missing:
            component_complete["price"] += 1
        if "custo" not in missing:
            component_complete["cost"] += 1
        if "imposto" not in missing:
            component_complete["tax"] += 1
        if "tarifa" not in missing:
            component_complete["fee"] += 1
        if "frete" not in missing:
            component_complete["shipping"] += 1
        sources = raw.get("sources") if isinstance(raw.get("sources"), list) else []
        listing_margin_rows.append(
            {
                "store": store,
                "mlb": str(raw.get("mlb") or raw.get("item_id") or raw.get("id") or "").strip().upper(),
                "variation_id": str(raw.get("variation_id") or "").strip(),
                "sku": sku,
                "product": str(raw.get("product") or cost_data.get("product") or "").strip(),
                "status": str(raw.get("status") or "").strip(),
                "available_quantity": raw.get("available_quantity"),
                "current_price_brl": margin.get("preco_final_margem"),
                "regular_price_brl": raw.get("regular_price") if raw.get("regular_price") is not None else raw.get("original_price"),
                "unit_cost_brl": margin.get("custo"),
                "cost_scope": cost_scope,
                "tax_pct": margin.get("imposto_percentual"),
                "tax_scope": tax_scope,
                "ml_fee_brl": margin.get("tarifa_ml"),
                "seller_shipping_brl": margin.get("frete_ml"),
                "unit_contribution_brl": margin.get("valor_liquido") if margin.get("margem_completa") else None,
                "contribution_margin_pct": margin.get("margem_percentual") if margin.get("margem_completa") else None,
                "margin_status": "available" if margin.get("margem_completa") else "unavailable",
                "margin_status_label": "Disponível" if margin.get("margem_completa") else "Indisponível",
                "missing_components": missing,
                "missing_components_text": ", ".join(missing) if missing else "-",
                "collected_at": raw.get("collected_at") or snapshot.get("collected_at"),
                "sources": sources,
                "source_labels": ", ".join(
                    str(item.get("resource") or item.get("source") or item)
                    for item in sources[:8]
                    if item not in (None, "")
                ),
            }
        )

    historical_rows: list[dict[str, Any]] = []
    seen_ledger: set[tuple[str, ...]] = set()
    covered_revenue = 0.0
    covered_profit = 0.0
    reconciled_return_gross = 0.0
    reconciled_return_impact = 0.0
    for raw in ledger_values:
        key = (
            str(raw.get("store") or raw.get("loja") or ""),
            str(raw.get("order_id") or ""),
            str(raw.get("line_number") if raw.get("line_number") is not None else ""),
            str(raw.get("item_id") or raw.get("mlb") or ""),
            str(raw.get("variation_id") or ""),
            str(raw.get("sku") or "").upper(),
        )
        if key in seen_ledger:
            continue
        seen_ledger.add(key)
        quantity = max(0.0, _float(raw.get("quantity")))
        sold_price = _optional_float(raw.get("sold_unit_price") if raw.get("sold_unit_price") is not None else raw.get("unit_price"))
        gross_amount = _optional_float(raw.get("gross_amount"))
        if gross_amount is None and sold_price is not None:
            gross_amount = round(sold_price * quantity, 2)
        unit_cost = _optional_float(raw.get("unit_cost"))
        tax_pct = _optional_float(raw.get("tax_pct"))
        fee_total = _optional_float(raw.get("sale_fee_total"))
        fee_unit = _optional_float(raw.get("sale_fee_amount") if raw.get("sale_fee_amount") is not None else raw.get("fee_unit"))
        if fee_unit is None and fee_total is not None and quantity > 0:
            fee_unit = round(fee_total / quantity, 2)
        pack_item_count = int(_float(raw.get("pack_item_count"), 1))
        shipping_total = _optional_float(raw.get("seller_shipping_cost") if raw.get("seller_shipping_cost") is not None else raw.get("shipping_seller_cost"))
        shipping_unit = _optional_float(raw.get("shipping_unit"))
        if shipping_unit is None and shipping_total is not None and pack_item_count <= 1 and quantity > 0:
            shipping_unit = round(shipping_total / quantity, 2)
        historical_input = {
            "price": sold_price,
            "sale_fee_amount": fee_unit,
            "shipping_seller_cost": shipping_unit,
        }
        historical_margin = margem_calcular_anuncio(
            historical_input,
            sku_hint=str(raw.get("sku") or "").upper(),
            custo=unit_cost,
            imposto_rate=tax_pct,
            require_shipping=True,
        )
        missing = [str(item) for item in historical_margin.get("faltando_margem") or []]
        if raw.get("historical_cost_confirmed") is False:
            missing.append("custo_historico_nao_confirmado")
        if raw.get("historical_tax_confirmed") is False:
            missing.append("imposto_historico_nao_confirmado")
        if pack_item_count > 1 and "frete" not in missing:
            missing.append("frete_pack_sem_rateio")
        historical_basis_confirmed = (
            raw.get("historical_cost_confirmed") is not False
            and raw.get("historical_tax_confirmed") is not False
        )
        complete = bool(
            historical_margin.get("margem_completa")
            and historical_basis_confirmed
            and pack_item_count <= 1
            and gross_amount is not None
        )
        contribution_total = round(_float(historical_margin.get("valor_liquido")) * quantity, 2) if complete else None
        if complete:
            covered_revenue += max(0.0, _float(gross_amount))
            covered_profit += _float(contribution_total)
        return_state = str(raw.get("return_reconciliation_state") or "").lower()
        return_gross = _optional_float(raw.get("return_gross_amount"))
        return_impact = _optional_float(raw.get("return_impact_brl"))
        if return_state == "reconciled" and return_gross is not None and return_impact is not None:
            reconciled_return_gross += max(0.0, return_gross)
            reconciled_return_impact += return_impact
        historical_rows.append(
            {
                "store": key[0],
                "order_id": key[1],
                "line_number": key[2],
                "pack_id": str(raw.get("pack_id") or ""),
                "mlb": key[3].upper(),
                "variation_id": key[4],
                "sku": key[5],
                "quantity": quantity,
                "sold_unit_price_brl": sold_price,
                "gross_amount_brl": gross_amount,
                "contribution_total_brl": contribution_total,
                "reconciliation_state": str(raw.get("reconciliation_state") or ("reconciled" if complete else "partial")),
                "margin_status": "available" if complete else "unavailable",
                "margin_status_label": "Disponível" if complete else "Indisponível",
                "missing_components": missing,
                "missing_components_text": ", ".join(missing) if missing else "-",
                "pack_shipping_brl": shipping_total,
                "shipping_scope": "pack_unallocated" if pack_item_count > 1 else "single_item_pack",
                "shipping_scope_label": "Pack multi-item sem rateio" if pack_item_count > 1 else "Pack de item único",
                "sources": raw.get("sources") if isinstance(raw.get("sources"), list) else [],
            }
        )

    financial = output.get("financial_summary") if isinstance(output.get("financial_summary"), dict) else {}
    coverage = output.get("financial_coverage") if isinstance(output.get("financial_coverage"), dict) else {}
    gross_revenue = _optional_float(financial.get("gross_revenue_brl"))
    threshold_pct = _float(coverage.get("minimum_required_pct"), 95.0)
    threshold = threshold_pct / 100.0
    covered_revenue = min(covered_revenue, gross_revenue) if gross_revenue is not None else covered_revenue
    margin_ratio = covered_revenue / gross_revenue if gross_revenue is not None and gross_revenue > 0 else None
    contribution_valid = bool(margin_ratio is not None and margin_ratio >= threshold)
    stores_with_revenue = {
        _text_key(item.get("store"))
        for item in output.get("store_summaries") or []
        if isinstance(item, dict) and _float(item.get("gross_revenue_brl")) > 0
    }
    stores_with_ads = {
        _text_key(item.get("store"))
        for item in output.get("store_summaries") or []
        if isinstance(item, dict) and item.get("advertising_brl") is not None
    }
    ads_ratio = 1.0 if stores_with_revenue and stores_with_revenue.issubset(stores_with_ads) else 0.0 if stores_with_revenue else None
    after_ads_valid = bool(contribution_valid and ads_ratio is not None and ads_ratio >= threshold)
    returns_total = _optional_float(financial.get("returns_brl"))
    if returns_total == 0:
        returns_ratio = 1.0
    elif returns_total is not None and returns_total > 0:
        returns_ratio = min(1.0, reconciled_return_gross / returns_total)
    else:
        returns_ratio = None
    after_returns_valid = bool(after_ads_valid and returns_ratio is not None and returns_ratio >= threshold)
    advertising_total = _optional_float(financial.get("advertising_brl"))
    consolidated_profit = round(covered_profit, 2) if contribution_valid else None
    after_ads = round(covered_profit - advertising_total, 2) if after_ads_valid and advertising_total is not None else None
    after_returns = round(after_ads - reconciled_return_impact, 2) if after_returns_valid and after_ads is not None else None

    listing_count = len(listing_margin_rows)
    coverage.update(
        {
            "minimum_required_pct": threshold_pct,
            "complete_margin_by_revenue_pct": round(margin_ratio * 100.0, 2) if margin_ratio is not None else None,
            "covered_revenue_brl": round(covered_revenue, 2) if gross_revenue is not None else None,
            "uncovered_revenue_brl": round(max(0.0, gross_revenue - covered_revenue), 2) if gross_revenue is not None else None,
            "advertising_pct": round(ads_ratio * 100.0, 2) if ads_ratio is not None else None,
            "returns_reconciled_pct": round(returns_ratio * 100.0, 2) if returns_ratio is not None else None,
            "contribution_margin_valid": contribution_valid,
            "net_margin_after_ads_valid": after_ads_valid,
            "net_margin_after_returns_valid": after_returns_valid,
            "consolidated_profit_brl": consolidated_profit,
            "consolidated_margin_pct": round(covered_profit / covered_revenue * 100.0, 2) if contribution_valid and covered_revenue > 0 else None,
            "net_profit_after_ads_brl": after_ads,
            "net_profit_after_ads_and_returns_brl": after_returns,
            "listing_component_coverage_pct": {
                key: round(value / listing_count * 100.0, 2) if listing_count else None
                for key, value in component_complete.items()
            },
            "status": "reliable" if after_returns_valid else "partial" if listing_margin_rows or covered_revenue > 0 else "insufficient",
        }
    )
    financial.update(
        {
            "contribution_profit_brl": consolidated_profit,
            "net_profit_after_ads_brl": after_ads,
            "reconciled_returns_impact_brl": round(reconciled_return_impact, 2) if returns_ratio is not None else None,
            "net_profit_after_ads_and_returns_brl": after_returns,
        }
    )

    quality = output.get("data_quality") if isinstance(output.get("data_quality"), dict) else {}
    source_health = quality.get("source_health") if isinstance(quality.get("source_health"), list) else []
    source_health = [item for item in source_health if not (isinstance(item, dict) and item.get("source") == "Mercado Livre comercial")]
    source_health.append(
        {
            "source": "Mercado Livre comercial",
            "status": str(snapshot.get("status") or ("unavailable" if not snapshot else "partial")),
            "records": listing_count,
            "last_sync_at": snapshot.get("collected_at"),
            "coverage_pct": round(margin_ratio * 100.0, 2) if margin_ratio is not None else None,
            "stale": bool(snapshot.get("stale")),
            "read_only": True,
        }
    )
    warnings = list(quality.get("warnings") or []) + [str(item) for item in snapshot.get("warnings") or []]
    incomplete_count = sum(1 for item in listing_margin_rows if item.get("margin_status") != "available")
    if incomplete_count:
        warnings.append(f"{incomplete_count} anuncio(s)/variacao(oes) ficaram sem margem por componentes comerciais ausentes.")
    if snapshot.get("stale"):
        warnings.append("A contingencia comercial do Mercado Livre esta vencida e foi exibida com a data da coleta.")
    quality["source_health"] = source_health
    quality["warnings"] = list(dict.fromkeys(warnings))[:50]

    output.update(
        {
            "marketplace_commercial": snapshot,
            "listing_margin_rows": listing_margin_rows,
            "historical_margin_ledger": historical_rows,
            "financial_coverage": coverage,
            "financial_summary": financial,
            "data_quality": quality,
        }
    )
    return normalize_text_tree(output)


def create_queue_action(
    *,
    info_base: str,
    client_id: str,
    username: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    action_type = str(payload.get("action_type") or "").strip()
    if action_type not in {"replenishment", "price_review", "liquidation", "data_quality"}:
        raise ValueError("Tipo de ação interna inválido.")
    data = normalize_text_tree(dict(payload or {}))
    data["status"] = str(data.get("status") or "queued")
    data["approved_by"] = str(username or "")
    data["created_by"] = str(data.get("created_by") or username or "")
    data["audit"] = [
        {
            "at": datetime.now().isoformat(timespec="seconds"),
            "actor": str(username or ""),
            "event": "created",
            "status": data["status"],
        }
    ]
    return codex_assistant_storage.codex_assistant_action_queue_save(info_base, client_id, data, actor=username)


def update_queue_action(
    *,
    info_base: str,
    client_id: str,
    action_id: str,
    username: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    current = codex_assistant_storage.codex_assistant_action_queue_get(info_base, client_id, action_id)
    if not isinstance(current, dict):
        raise KeyError(action_id)
    allowed = {"status", "owner_username", "owner_role", "due_at", "note", "result", "linked_list_id"}
    for key, value in (updates or {}).items():
        if key in allowed:
            current[key] = value
    current["updated_by"] = username
    audit = current.get("audit") if isinstance(current.get("audit"), list) else []
    audit.append(
        {
            "at": datetime.now().isoformat(timespec="seconds"),
            "actor": str(username or ""),
            "event": "updated",
            "changes": {key: value for key, value in (updates or {}).items() if key in allowed},
        }
    )
    current["audit"] = audit[-200:]
    return codex_assistant_storage.codex_assistant_action_queue_save(info_base, client_id, current, actor=username)


def queue_actions_list(info_base: str, client_id: str, **filters: Any) -> list[dict[str, Any]]:
    return codex_assistant_storage.codex_assistant_action_queue_list(info_base, client_id, **filters)


def create_replenishment_list(
    *,
    info_base: str,
    client_id: str,
    action: dict[str, Any],
    username: str,
) -> dict[str, Any]:
    import uuid

    tenant = _tenant_path(info_base, client_id)
    os.makedirs(tenant, exist_ok=True)
    path = os.path.join(tenant, "listas_pedidos.json")
    existing = _read_json(path, [])
    lists = existing if isinstance(existing, list) else []
    items_raw = action.get("items") if isinstance(action.get("items"), list) else []
    if not items_raw:
        items_raw = [{"sku": sku, "quantity": 0} for sku in (action.get("skus") or [])]
    items = []
    for item in items_raw:
        if not isinstance(item, dict):
            continue
        sku = str(item.get("sku") or item.get("SKU") or "").strip().upper()
        quantity = max(0, int(math.ceil(_float(item.get("quantity") or item.get("Quantidade")))))
        if sku and quantity > 0:
            items.append({"SKU": sku, "Quantidade": quantity, "Valor unidade": 0.0, "Valor total": 0.0})
    if not items:
        raise ValueError("A recomendacao nao possui quantidades validas para criar a lista de reposicao.")
    now = datetime.now().isoformat(timespec="seconds")
    list_id = str(uuid.uuid4())
    payload = {
        "id": list_id,
        "nome_lista": f"Reposicao Black Jhon {date.today().isoformat()}",
        "loja": str(action.get("store") or "__todas"),
        "status": "Lista gerada",
        "created_at": now,
        "updated_at": now,
        "origem": "black_jhon_report",
        "report_id": str(action.get("report_id") or ""),
        "report_action_id": str(action.get("action_id") or ""),
        "created_by": str(username or ""),
        "itens": items,
    }
    lists.insert(0, payload)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as fh:
        json.dump(lists, fh, ensure_ascii=False, indent=2, default=str)
    os.replace(temp, path)
    return {"list_id": list_id, "status": "Lista gerada", "items": len(items), "store": payload["loja"]}
