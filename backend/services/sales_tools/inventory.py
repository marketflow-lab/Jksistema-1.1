from __future__ import annotations

import datetime as dt
import logging
import math
import os
import sqlite3
from typing import Optional

from .charts import _ia_stale_stock_chart_data, _ia_stockout_chart_data
from .parsing import _ia_tool_float, _ia_tool_resolver_sku
from .repository import _ia_obter_data_referencia_vendas
from .runtime import (
    _cadastro_ler_custos_lojas,
    _ia_carregar_produtos_tool_df,
    _ia_tool_get_product_data,
    _listar_bancos_vendas_tenant,
    _normalizar_texto,
    _sku_lookup_variantes,
    _sql_filtro_loja_vendas,
)

logger = logging.getLogger(__name__)


def _ia_tool_get_days_without_sale(client_id: str, mensagem: str, produto_tool: Optional[dict] = None, loja: Optional[str] = None) -> Optional[dict]:
    try:
        sku = _ia_tool_resolver_sku(client_id, mensagem, produto_tool)
        if not sku:
            return None

        loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else None
        dbs = _listar_bancos_vendas_tenant(client_id, loja_filtro)
        if not dbs:
            return None

        sql_loja, params_loja = _sql_filtro_loja_vendas(loja_filtro)
        ultima_venda = ""
        primeira_venda = ""
        produto_nome = ""

        for db_path in dbs:
            if not os.path.exists(db_path):
                continue
            try:
                conn = sqlite3.connect(db_path, timeout=5)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                row = cur.execute(
                    f"""
                    SELECT
                        MIN(CASE WHEN COALESCE(devolucao, 0) = 0
                                  AND LOWER(COALESCE(situacao,'')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                                 THEN data END) AS primeira_venda,
                        MAX(CASE WHEN COALESCE(devolucao, 0) = 0
                                  AND LOWER(COALESCE(situacao,'')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                                 THEN data END) AS ultima_venda,
                        MAX(produto) AS produto
                    FROM vendas
                    WHERE UPPER(TRIM(COALESCE(sku, ''))) = ?
                      {sql_loja}
                    """,
                    [sku, *params_loja],
                ).fetchone()
                conn.close()
                if row:
                    prod = str(row["produto"] or "").strip()
                    if prod and not produto_nome:
                        produto_nome = prod
                    p = str(row["primeira_venda"] or "").strip()
                    u = str(row["ultima_venda"] or "").strip()
                    if p and (not primeira_venda or p < primeira_venda):
                        primeira_venda = p
                    if u and (not ultima_venda or u > ultima_venda):
                        ultima_venda = u
            except Exception as exc:
                logger.warning(f"[IA TOOLS] Erro ao consultar dias sem venda para SKU {sku} em {db_path}: {exc}")
                continue

        data_ref = _ia_obter_data_referencia_vendas(client_id, loja_filtro) or dt.date.today()
        status = "nunca_vendeu"
        dias_sem_vender = None
        if ultima_venda:
            try:
                ultima_venda_data = str(ultima_venda).strip().replace("T", " ").split(" ", 1)[0]
                dias_sem_vender = max(0, (data_ref - dt.date.fromisoformat(ultima_venda_data)).days)
                ultima_venda = ultima_venda_data
                status = "sem_venda_recente" if dias_sem_vender > 0 else "vendeu_no_dia"
            except Exception:
                dias_sem_vender = None
                status = "sem_dado"

        return {
            "function": "get_days_without_sale",
            "arguments": {"sku": sku, "loja": loja_filtro or ""},
            "result": {
                "sku": sku,
                "produto": produto_nome,
                "loja": loja_filtro or "",
                "data_referencia": data_ref.isoformat(),
                "primeira_venda": primeira_venda,
                "ultima_venda": ultima_venda,
                "dias_sem_vender": dias_sem_vender,
                "status": status,
            },
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao calcular dias sem venda: {exc}")
        return None



def _inventory_maps(dataframe) -> dict:
    maps = {
        "products": {}, "total_stock": {}, "store_stock": {}, "full_stock": {},
        "costs": {}, "prices": {}, "cost_sources": {}, "price_sources": {},
        "known_costs": set(), "known_prices": set(),
    }
    for _, row in dataframe.iterrows():
        sku = str(row.get("sku_norm") or "").strip().upper()
        if not sku or not any(character.isalnum() for character in sku):
            continue
        maps["products"].setdefault(
            sku,
            str(row.get("nome_tool") or row.get("nome") or row.get("nome_bling") or "").strip(),
        )
        store_stock = _ia_tool_float(row.get("saldo_loja"))
        full_stock = _ia_tool_float(row.get("saldo_full"))
        total_stock = _ia_tool_float(row.get("saldo_total")) or store_stock + full_stock
        maps["total_stock"][sku] = max(maps["total_stock"].get(sku, 0.0), float(total_stock))
        maps["store_stock"][sku] = max(maps["store_stock"].get(sku, 0.0), float(store_stock))
        maps["full_stock"][sku] = max(maps["full_stock"].get(sku, 0.0), float(full_stock))
        for column in ("custo", "custo_y", "custo_x"):
            raw_cost = str(row.get(column) or "").strip()
            if raw_cost:
                maps["costs"][sku] = _ia_tool_float(raw_cost)
                maps["known_costs"].add(sku)
                maps["cost_sources"][sku] = "cadastro geral"
                break
        for column in ("preco", "preco_y", "preco_x"):
            raw_price = str(row.get(column) or "").strip()
            if raw_price:
                maps["prices"][sku] = _ia_tool_float(raw_price)
                maps["known_prices"].add(sku)
                maps["price_sources"][sku] = "cadastro geral"
                break
    return maps


def _merge_store_costs(client_id: str, store_filter: Optional[str], maps: dict) -> None:
    try:
        store_costs = _cadastro_ler_custos_lojas(client_id)
        costs_by_sku: dict[str, list[float]] = {}
        prices_by_sku: dict[str, list[float]] = {}
        target_store = _normalizar_texto(store_filter or "")
        rows = store_costs.iterrows() if store_costs is not None and not store_costs.empty else []
        for _, row in rows:
            row_sku = str(row.get("sku") or "").strip().upper()
            if not row_sku:
                continue
            if target_store and _normalizar_texto(row.get("loja_sync") or "") != target_store:
                continue
            try:
                variants = list(dict.fromkeys([row_sku, *_sku_lookup_variantes(row_sku)]))
            except Exception:
                variants = [row_sku]
            sku = next((value for value in variants if value in maps["products"]), row_sku)
            raw_cost, raw_price = str(row.get("custo") or "").strip(), str(row.get("preco") or "").strip()
            if raw_cost:
                costs_by_sku.setdefault(sku, []).append(_ia_tool_float(raw_cost))
            if raw_price:
                prices_by_sku.setdefault(sku, []).append(_ia_tool_float(raw_price))
        source = "cadastro da loja" if store_filter else "media do cadastro por loja"
        for sku, values in costs_by_sku.items():
            if values:
                maps["costs"][sku] = sum(values) / len(values)
                maps["known_costs"].add(sku)
                maps["cost_sources"][sku] = source
        for sku, values in prices_by_sku.items():
            if values:
                maps["prices"][sku] = sum(values) / len(values)
                maps["known_prices"].add(sku)
                maps["price_sources"][sku] = source
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao complementar custo por loja no estoque parado: {exc}")


def _latest_sales_by_sku(databases: list[str], store_filter: Optional[str]) -> dict[str, str]:
    store_sql, store_params = _sql_filtro_loja_vendas(store_filter)
    latest: dict[str, str] = {}
    for db_path in databases:
        if not os.path.exists(db_path):
            continue
        try:
            connection = sqlite3.connect(db_path, timeout=5)
            connection.row_factory = sqlite3.Row
            try:
                rows = connection.execute(
                    f"""
                    SELECT UPPER(TRIM(COALESCE(sku, ''))) AS sku, MAX(data) AS ultima_venda
                    FROM vendas
                    WHERE COALESCE(devolucao, 0) = 0
                      AND LOWER(COALESCE(situacao,'')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                      {store_sql}
                    GROUP BY UPPER(TRIM(COALESCE(sku, '')))
                    """,
                    [*store_params],
                ).fetchall()
            finally:
                connection.close()
            for row in rows or []:
                sku = str(row["sku"] or "").strip().upper()
                sale_date = str(row["ultima_venda"] or "").strip().replace("T", " ").split(" ", 1)[0]
                if sku and sale_date and sale_date > latest.get(sku, ""):
                    latest[sku] = sale_date
        except Exception as exc:
            logger.warning(f"[IA TOOLS] Erro ao buscar ultimas vendas em {db_path}: {exc}")
    return latest


def _stale_inventory_items(
    maps: dict,
    latest_sales: dict[str, str],
    reference_date: dt.date,
    only_with_stock: bool,
    only_previously_sold: bool,
    only_store_stock: bool,
) -> list[dict]:
    items = []
    for sku, product_name in maps["products"].items():
        total_stock = float(maps["total_stock"].get(sku) or 0.0)
        store_stock = float(maps["store_stock"].get(sku) or 0.0)
        full_stock = float(maps["full_stock"].get(sku) or 0.0)
        if (only_with_stock and total_stock <= 0) or (only_store_stock and store_stock <= 0):
            continue
        known_cost, known_price = sku in maps["known_costs"], sku in maps["known_prices"]
        unit_cost, unit_price = float(maps["costs"].get(sku) or 0.0), float(maps["prices"].get(sku) or 0.0)
        registered_values = {
            "custo_cadastrado": known_cost,
            "custo_unitario": unit_cost if known_cost else None,
            "custo_origem": maps["cost_sources"].get(sku, "") if known_cost else "",
            "valor_custo_estoque_loja": round(store_stock * unit_cost, 2) if known_cost else None,
            "preco_cadastrado": known_price,
            "preco_unitario": unit_price if known_price else None,
            "preco_origem": maps["price_sources"].get(sku, "") if known_price else "",
            "valor_venda_estoque_loja": round(store_stock * unit_price, 2) if known_price else None,
        }
        latest = str(latest_sales.get(sku) or "").strip()
        base = {"sku": sku, "produto": product_name, "saldo_loja": store_stock,
                "saldo_full": full_stock, "saldo_total": total_stock, **registered_values}
        if not latest:
            if not only_previously_sold:
                items.append({**base, "ultima_venda": "", "dias_sem_vender": None, "status": "nunca_vendeu"})
            continue
        try:
            days = max(0, (reference_date - dt.date.fromisoformat(latest)).days)
            status = "sem_venda_recente" if days > 0 else "vendeu_no_dia"
            items.append({**base, "ultima_venda": latest, "dias_sem_vender": days, "status": status})
        except Exception:
            items.append({**base, "ultima_venda": latest, "dias_sem_vender": None, "status": "sem_dado"})
    return items


def _stale_stock_summary(items: list[dict]) -> dict:
    stale = [
        item for item in items
        if float(item.get("saldo_loja") or 0) > 0
        and (item.get("dias_sem_vender") is None or int(item.get("dias_sem_vender") or 0) >= 30)
    ]
    covered = [item for item in stale if item.get("custo_cadastrado") is True]
    return {
        "total_skus": len(stale),
        "total_unidades_loja": round(sum(float(item.get("saldo_loja") or 0) for item in stale), 3),
        "nunca_venderam": sum(1 for item in stale if item.get("status") == "nunca_vendeu"),
        "dias_180_mais": sum(1 for item in stale if item.get("dias_sem_vender") is not None and int(item.get("dias_sem_vender") or 0) >= 180),
        "dias_90_179": sum(1 for item in stale if item.get("dias_sem_vender") is not None and 90 <= int(item.get("dias_sem_vender") or 0) < 180),
        "dias_30_89": sum(1 for item in stale if item.get("dias_sem_vender") is not None and 30 <= int(item.get("dias_sem_vender") or 0) < 90),
        "custos_cobertos": len(covered),
        "capital_custo_conhecido": round(sum(float(item.get("valor_custo_estoque_loja") or 0) for item in covered), 2),
    }


def _ia_tool_get_days_without_sale_top(
    client_id: str,
    loja: Optional[str] = None,
    limite: int = 20,
    apenas_com_estoque: bool = False,
    apenas_ja_vendidos: bool = False,
    apenas_saldo_loja: bool = False,
) -> Optional[dict]:
    try:
        limit = max(1, min(int(limite or 20), 500))
        store_filter = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else None
        dataframe = _ia_carregar_produtos_tool_df(client_id)
        if dataframe is None or dataframe.empty:
            return None
        maps = _inventory_maps(dataframe)
        _merge_store_costs(client_id, store_filter, maps)
        if not maps["products"]:
            return None
        databases = _listar_bancos_vendas_tenant(client_id, store_filter)
        if not databases:
            return None
        latest_sales = _latest_sales_by_sku(databases, store_filter)
        reference_date = _ia_obter_data_referencia_vendas(client_id, store_filter) or dt.date.today()
        items = _stale_inventory_items(
            maps, latest_sales, reference_date,
            apenas_com_estoque, apenas_ja_vendidos, apenas_saldo_loja,
        )
        if not items:
            return None
        ordered = sorted(items, key=lambda item: (
            0 if item.get("dias_sem_vender") is None else 1,
            -int(item.get("dias_sem_vender") or 0),
            str(item.get("sku") or ""),
        ))
        result = {
            "loja": store_filter or "",
            "data_referencia": reference_date.isoformat(),
            "total_skus_avaliados": len(ordered),
            "total_skus_retornados": min(len(ordered), limit),
            "resultado_truncado": len(ordered) > limit,
            "resumo_estoque_parado": _stale_stock_summary(ordered),
            "itens": ordered[:limit],
        }
        result["chart_data"] = _ia_stale_stock_chart_data(result)
        return {
            "function": "get_days_without_sale_top",
            "arguments": {"loja": store_filter or "", "limite": limit,
                          "apenas_com_estoque": bool(apenas_com_estoque),
                          "apenas_ja_vendidos": bool(apenas_ja_vendidos),
                          "apenas_saldo_loja": bool(apenas_saldo_loja)},
            "result": result,
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao listar SKUs sem venda: {exc}")
        return None


def _ia_tool_get_stock_data(client_id: str, mensagem: str, produto_tool: Optional[dict] = None) -> Optional[dict]:
    base_produto = produto_tool or _ia_tool_get_product_data(client_id, mensagem)
    if not base_produto:
        return None
    resultado = base_produto.get("result") or {}
    matches = resultado.get("matches") or []
    if not matches:
        return {
            "function": "get_stock_data",
            "arguments": {"sku_ou_termo": (base_produto.get("arguments") or {}).get("sku_ou_termo") or ""},
            "result": {"found": False, "items": []},
        }

    sku = str(resultado.get("canonical_sku") or matches[0].get("sku") or "").strip().upper()
    saldo_loja_total = sum(_ia_tool_float(item.get("saldo_loja")) for item in matches)
    saldo_full_total = sum(_ia_tool_float(item.get("saldo_full")) for item in matches)
    return {
        "function": "get_stock_data",
        "arguments": {"sku_ou_termo": (base_produto.get("arguments") or {}).get("sku_ou_termo") or sku},
        "result": {
            "found": True,
            "sku": sku,
            "nome": str(matches[0].get("nome") or "").strip(),
            "saldo_loja_total": saldo_loja_total,
            "saldo_full_total": saldo_full_total,
            "saldo_total": saldo_loja_total + saldo_full_total,
            "linhas_origem": len(matches),
        },
    }



def _stock_by_sku(dataframe) -> dict[str, dict]:
    valid = dataframe.copy()
    valid["sku_norm"] = valid["sku_norm"].astype(str).str.strip().str.upper()
    valid = valid[valid["sku_norm"] != ""]
    stock: dict[str, dict] = {}
    for _, row in valid.iterrows():
        sku = str(row.get("sku_norm") or "").strip().upper()
        if not sku:
            continue
        current_item = stock.setdefault(sku, {
            "sku": sku,
            "nome": str(row.get("nome_tool") or row.get("nome") or row.get("nome_bling") or "").strip(),
            "saldo_loja": 0.0,
            "saldo_full": 0.0,
        })
        current_item["saldo_loja"] += _ia_tool_float(row.get("saldo_loja"))
        current_item["saldo_full"] += _ia_tool_float(row.get("saldo_full"))
    return stock


def _sales_quantity_by_sku(
    databases: list[str],
    start_date: str,
    end_date: str,
    store_filter: Optional[str],
) -> dict[str, float]:
    store_sql, store_params = _sql_filtro_loja_vendas(store_filter)
    sales: dict[str, float] = {}
    for db_path in databases:
        if not os.path.exists(db_path):
            continue
        try:
            connection = sqlite3.connect(db_path, timeout=5)
            connection.row_factory = sqlite3.Row
            try:
                rows = connection.execute(
                    f"""
                    SELECT UPPER(TRIM(COALESCE(sku, ''))) AS sku,
                           SUM(COALESCE(quantidade, 0)) AS qtd
                    FROM vendas
                    WHERE data BETWEEN ? AND ? AND COALESCE(devolucao, 0) = 0
                      AND LOWER(COALESCE(situacao,'')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                      {store_sql}
                    GROUP BY UPPER(TRIM(COALESCE(sku, '')))
                    """,
                    [start_date, end_date, *store_params],
                ).fetchall()
            finally:
                connection.close()
            for row in rows or []:
                sku = str(row["sku"] or "").strip().upper()
                if sku:
                    sales[sku] = float(sales.get(sku) or 0) + float(row["qtd"] or 0)
        except Exception as exc:
            logger.warning(f"[IA TOOLS] Erro ao calcular consumo por SKU em {db_path}: {exc}")
    return sales


def _stockout_rows(
    stock: dict[str, dict],
    sales: dict[str, float],
    target_skus: list[str],
    reference_date: dt.date,
    lookback_days: int,
) -> list[dict]:
    forecasts = []
    for sku in target_skus:
        item = stock.get(sku)
        if not item:
            continue
        total_stock = float(item.get("saldo_loja") or 0) + float(item.get("saldo_full") or 0)
        sold_quantity = float(sales.get(sku) or 0)
        daily_average = sold_quantity / float(lookback_days)
        days_to_stockout, stockout_date, risk = None, "", "sem_consumo"
        if daily_average > 0:
            days_to_stockout = total_stock / daily_average if total_stock > 0 else 0.0
            stockout_date = (reference_date + dt.timedelta(days=max(0, int(math.ceil(days_to_stockout))))).isoformat()
            if days_to_stockout <= 7:
                risk = "critico"
            elif days_to_stockout <= 15:
                risk = "alto"
            elif days_to_stockout <= 30:
                risk = "medio"
            else:
                risk = "baixo"
        forecasts.append({
            "sku": sku, "nome": item.get("nome") or "",
            "saldo_loja": float(item.get("saldo_loja") or 0),
            "saldo_full": float(item.get("saldo_full") or 0),
            "saldo_total": total_stock, "quantidade_vendida_janela": sold_quantity,
            "media_venda_dia": daily_average, "dias_ate_ruptura": days_to_stockout,
            "data_prevista_ruptura": stockout_date, "risco_ruptura": risk,
        })
    return forecasts


def _ia_tool_get_stockout_forecast(
    client_id: str,
    mensagem: str,
    produto_tool: Optional[dict] = None,
    loja: Optional[str] = None,
    lookback_days: int = 30,
    limite: int = 100,
) -> Optional[dict]:
    try:
        lookback_days = max(7, min(int(lookback_days or 30), 180))
        store_filter = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else None
        dataframe = _ia_carregar_produtos_tool_df(client_id)
        if dataframe is None or dataframe.empty:
            return None
        stock = _stock_by_sku(dataframe)
        if not stock:
            return None
        reference_date = _ia_obter_data_referencia_vendas(client_id, store_filter) or dt.date.today()
        start_date = (reference_date - dt.timedelta(days=lookback_days - 1)).isoformat()
        end_date = reference_date.isoformat()
        databases = _listar_bancos_vendas_tenant(client_id, store_filter)
        if not databases:
            return None
        sales = _sales_quantity_by_sku(databases, start_date, end_date, store_filter)
        requested_sku = _ia_tool_resolver_sku(client_id, mensagem, produto_tool)
        if requested_sku and requested_sku not in stock:
            requested_sku = ""
        target_skus = [requested_sku] if requested_sku else list(stock)
        forecasts = _stockout_rows(stock, sales, target_skus, reference_date, lookback_days)
        if not forecasts:
            return None
        ordered = sorted(forecasts, key=lambda item: (
            float(item.get("dias_ate_ruptura") if item.get("dias_ate_ruptura") is not None else 10**9),
            -float(item.get("media_venda_dia") or 0),
        ))
        result = ordered[0] if requested_sku else {
            "data_referencia": reference_date.isoformat(), "janela_dias": lookback_days,
            "loja": store_filter or "", "itens": ordered[:max(1, limite)],
            "total_skus_analisados": len(ordered),
        }
        result["chart_data"] = _ia_stockout_chart_data(result)
        return {
            "function": "get_stockout_forecast",
            "arguments": {"sku": requested_sku or "", "data_inicio": start_date,
                          "data_fim": end_date, "janela_dias": lookback_days,
                          "loja": store_filter or ""},
            "result": result,
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao prever ruptura de estoque: {exc}")
        return None


__all__ = [
    "_ia_tool_get_days_without_sale",
    "_ia_tool_get_days_without_sale_top",
    "_ia_tool_get_stock_data",
    "_ia_tool_get_stockout_forecast",
]
