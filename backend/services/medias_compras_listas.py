"""Pedido-list endpoints and Excel imports for Medias Compras."""

from __future__ import annotations

import asyncio
import copy
import io
import json
import math
import os
import re
import unicodedata
import uuid
from datetime import datetime
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from backend.schemas import (
    ListaPedidoAddSkuRequest,
    ListaPedidoPreferenciasColunasRequest,
    ListaPedidoSkuAnaliseConcorrentesRequest,
    ListaPedidoSkuAprovacaoRequest,
    ListaPedidoStatusRequest,
    ListaPedidoUpdateRequest,
    MediasComprasSkusOcultosRequest,
)
from backend.services import medias_compras_common as medias_common
from backend.services.medias_compras_common import *
from backend.services.medias_compras_excel import *
from backend.services.medias_compras_fiscal import *
from backend.services.runtime_bridge import bind_runtime_globals


_RUNTIME_NAMES = (
    "logger",
    "get_tenant_path",
    "TEMP_FILES_STORAGE",
    "TEMP_FILES_META",
    "LISTA_PEDIDO_XLSX_CACHE",
    "LISTA_PEDIDO_XLSX_CACHE_MAX_ITENS",
)


def _sync_common_names() -> None:
    for name in medias_common.COMMON_EXPORTS:
        globals()[name] = getattr(medias_common, name)
    for name in _RUNTIME_NAMES:
        globals()[name] = getattr(medias_common, name)


def _configure_runtime_globals(runtime_module=None):
    runtime = medias_common.configure_medias_compras_common_runtime(runtime_module)
    bind_runtime_globals(globals(), runtime)
    _sync_common_names()
    return runtime


_CHAVES_CONCORRENTES = {f"concorrente_{numero}" for numero in range(1, 6)}


def _normalizar_item_id_mlb(valor: Any) -> str:
    texto = re.sub(r"[^A-Z0-9]", "", str(valor or "").strip().upper())
    return texto if re.fullmatch(r"MLB\d+", texto) else ""


def _numero_finito_margem(valor: Any) -> float | None:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if math.isfinite(numero) else None


def _loja_lista_definida(valor: Any) -> bool:
    texto = unicodedata.normalize("NFKD", str(valor or "").strip())
    texto = "".join(char for char in texto if not unicodedata.combining(char)).lower()
    return texto not in {"", "__todas", "nao definida", "todas", "todas as lojas"}


def _dependencias_margens_concorrentes() -> dict[str, Any]:
    from backend.services import mercadolivre_legacy_core, promocoes_core
    from backend.services.integracoes import carregar_lojas

    return {
        "carregar_lojas": carregar_lojas,
        "obter_cfg_ml": mercadolivre_legacy_core._obter_cfg_ml,
        "ml_api_request": mercadolivre_legacy_core._ml_api_request,
        "ml_contexto_frete_item": mercadolivre_legacy_core._ml_contexto_frete_item,
        "ml_obter_frete_detalhado": mercadolivre_legacy_core._ml_obter_frete_detalhado,
        "ml_obter_taxas_anuncio": mercadolivre_legacy_core._ml_obter_taxas_anuncio,
        "carregar_custos_impostos": promocoes_core._carregar_custos_impostos_cadastro_por_sku_loja,
        "resolver_custo": promocoes_core._resolver_custo_medio_por_skus,
        "resolver_imposto": promocoes_core._resolver_imposto_rate_por_sku,
        "calcular_margem": promocoes_core._calcular_margem_liquida_ml,
    }


def _margem_concorrente_indisponivel(
    preco_venda: float,
    item_id_loja: str,
    motivo: str,
    *,
    loja: str = "",
) -> dict[str, Any]:
    return {
        "preco_venda": round(float(preco_venda), 2),
        "item_id_loja": item_id_loja or None,
        "loja": loja or None,
        "margem_percentual": None,
        "financeiro_exato": False,
        "motivo_indisponivel": str(motivo or "Dados financeiros insuficientes."),
    }


def _carregar_contexto_anuncio_loja(
    client_id: str,
    loja_lista: str,
    item_id: str,
    deps: dict[str, Any],
    cache_contextos: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    if item_id in cache_contextos:
        contexto = cache_contextos[item_id]
        return (contexto, "") if contexto.get("ok") else (None, contexto.get("motivo") or "")

    candidatos: list[dict[str, Any]] = []
    if _loja_lista_definida(loja_lista):
        nomes_lojas = [str(loja_lista).strip()]
    else:
        try:
            nomes_lojas = [
                str(loja.get("nome") or "").strip()
                for loja in (deps["carregar_lojas"](client_id) or [])
                if isinstance(loja, dict)
                and str(loja.get("nome") or "").strip()
                and isinstance(loja.get("integracoes"), dict)
                and isinstance((loja.get("integracoes") or {}).get("mercadolivre"), dict)
                and str(((loja.get("integracoes") or {}).get("mercadolivre") or {}).get("access_token") or "").strip()
            ]
        except Exception:
            nomes_lojas = []

    for nome_loja in dict.fromkeys(nomes_lojas):
        try:
            cfg = dict(deps["obter_cfg_ml"](client_id, nome_loja) or {})
        except Exception:
            continue
        candidatos.append(
            {
                "nome": nome_loja,
                "cfg": cfg,
                "seller_id": str(cfg.get("user_id") or "").strip(),
            }
        )

    if not candidatos:
        motivo = (
            "A loja da lista não possui integração ativa com o Mercado Livre."
            if _loja_lista_definida(loja_lista)
            else "Defina a loja da lista para calcular tarifa, frete e imposto."
        )
        cache_contextos[item_id] = {"ok": False, "motivo": motivo}
        return None, motivo

    item = None
    candidato_consulta = None
    for candidato in candidatos:
        try:
            resposta, cfg_atualizada = deps["ml_api_request"](
                client_id,
                candidato["nome"],
                candidato["cfg"],
                "GET",
                f"https://api.mercadolibre.com/items/{item_id}",
                timeout=12,
            )
            candidato["cfg"] = dict(cfg_atualizada or candidato["cfg"])
            candidato["seller_id"] = str(candidato["cfg"].get("user_id") or candidato["seller_id"] or "").strip()
            if getattr(resposta, "status_code", None) == 200:
                payload = resposta.json() or {}
                if isinstance(payload, dict):
                    item = payload
                    candidato_consulta = candidato
                    break
        except Exception:
            continue

    if not isinstance(item, dict):
        motivo = "Não foi possível consultar o anúncio da loja no Mercado Livre."
        cache_contextos[item_id] = {"ok": False, "motivo": motivo}
        return None, motivo

    seller_id = str(item.get("seller_id") or ((item.get("seller") or {}).get("id") if isinstance(item.get("seller"), dict) else "") or "").strip()
    if _loja_lista_definida(loja_lista):
        escolhido = candidatos[0]
        if not seller_id or seller_id != escolhido["seller_id"]:
            motivo = "O anúncio informado não pertence à loja selecionada nesta lista."
            cache_contextos[item_id] = {"ok": False, "motivo": motivo}
            return None, motivo
    else:
        correspondentes = [candidato for candidato in candidatos if seller_id and candidato["seller_id"] == seller_id]
        if len(correspondentes) != 1:
            motivo = (
                "Há mais de uma loja compatível; defina a loja desta lista."
                if len(correspondentes) > 1
                else "Não foi possível identificar de forma segura a loja deste anúncio."
            )
            cache_contextos[item_id] = {"ok": False, "motivo": motivo}
            return None, motivo
        escolhido = correspondentes[0]
        if candidato_consulta is escolhido:
            escolhido["cfg"] = candidato_consulta["cfg"]

    contexto = {
        "ok": True,
        "loja": escolhido["nome"],
        "cfg": escolhido["cfg"],
        "item": item,
    }
    cache_contextos[item_id] = contexto
    return contexto, ""


def _calcular_margens_concorrentes_promocoes(
    client_id: str,
    loja_lista: str,
    sku: str,
    precos_concorrentes: dict[str, float],
    anuncios_loja: dict[str, str],
) -> dict[str, dict[str, Any]]:
    deps = _dependencias_margens_concorrentes()
    margens: dict[str, dict[str, Any]] = {}
    cache_contextos: dict[str, dict[str, Any]] = {}
    cache_cadastro: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}

    for chave in sorted(precos_concorrentes):
        preco_venda = float(precos_concorrentes[chave])
        item_id = _normalizar_item_id_mlb(anuncios_loja.get(chave))
        if not item_id:
            margens[chave] = _margem_concorrente_indisponivel(
                preco_venda,
                "",
                "Informe o anúncio da loja correspondente a este concorrente.",
            )
            continue

        contexto, motivo = _carregar_contexto_anuncio_loja(
            client_id,
            loja_lista,
            item_id,
            deps,
            cache_contextos,
        )
        if not contexto:
            margens[chave] = _margem_concorrente_indisponivel(preco_venda, item_id, motivo)
            continue

        loja = str(contexto.get("loja") or "").strip()
        item = dict(contexto.get("item") or {})
        cfg = dict(contexto.get("cfg") or {})
        if loja not in cache_cadastro:
            try:
                custos, impostos = deps["carregar_custos_impostos"](client_id, loja)
                cache_cadastro[loja] = (dict(custos or {}), dict(impostos or {}))
            except Exception:
                cache_cadastro[loja] = ({}, {})
        custos_cadastro, impostos_cadastro = cache_cadastro[loja]
        custo_unitario = deps["resolver_custo"](custos_cadastro, sku)
        custo_unitario = _numero_finito_margem(custo_unitario)
        if custo_unitario is None or custo_unitario < 0:
            margens[chave] = _margem_concorrente_indisponivel(
                preco_venda,
                item_id,
                "Custo do SKU não encontrado no cadastro desta loja.",
                loja=loja,
            )
            continue

        imposto_rate = deps["resolver_imposto"](impostos_cadastro, sku)
        imposto_rate = _numero_finito_margem(imposto_rate)
        if imposto_rate is None or imposto_rate < 0:
            margens[chave] = _margem_concorrente_indisponivel(
                preco_venda,
                item_id,
                "Imposto do SKU não encontrado para esta loja.",
                loja=loja,
            )
            continue

        item_preco = dict(item)
        item_preco["price"] = preco_venda
        try:
            tarifa_dados, cfg = deps["ml_obter_taxas_anuncio"](
                client_id,
                loja,
                cfg,
                item_preco,
            )
            tarifa_dados = dict(tarifa_dados or {})
        except Exception:
            tarifa_dados = {}
        tarifa_ml = _numero_finito_margem(tarifa_dados.get("ad_cost"))
        contexto_tarifa = _numero_finito_margem(tarifa_dados.get("ad_cost_price_context"))
        tarifa_exata = bool(tarifa_dados.get("ad_cost_exact_for_price"))
        tarifa_exata = tarifa_exata and contexto_tarifa is not None and abs(contexto_tarifa - preco_venda) <= 0.02
        if tarifa_ml is None or tarifa_ml < 0 or not tarifa_exata:
            margens[chave] = _margem_concorrente_indisponivel(
                preco_venda,
                item_id,
                "Tarifa exata do Mercado Livre não disponível para este preço.",
                loja=loja,
            )
            continue

        try:
            frete_dados, cfg = deps["ml_obter_frete_detalhado"](
                client_id,
                loja,
                cfg,
                item_id,
                item.get("shipping") or {},
                reconsultar_zero=True,
                contexto_frete=deps["ml_contexto_frete_item"](item, preco_venda),
            )
            frete_dados = dict(frete_dados or {})
        except Exception:
            frete_dados = {}
        contexto["cfg"] = cfg
        frete_ml = _numero_finito_margem(frete_dados.get("shipping_cost"))
        contexto_frete = _numero_finito_margem(frete_dados.get("shipping_price_context"))
        frete_exato = bool(frete_dados.get("shipping_exact_for_price"))
        frete_exato = frete_exato and contexto_frete is not None and abs(contexto_frete - preco_venda) <= 0.02
        if frete_ml is None or frete_ml < 0 or not frete_exato:
            margens[chave] = _margem_concorrente_indisponivel(
                preco_venda,
                item_id,
                "Frete exato do Mercado Livre não disponível para este preço.",
                loja=loja,
            )
            continue

        financeiro = deps["calcular_margem"](
            preco_venda,
            custo_unitario,
            imposto_rate,
            tarifa_ml,
            frete_ml,
        )
        if not financeiro:
            margens[chave] = _margem_concorrente_indisponivel(
                preco_venda,
                item_id,
                "Não foi possível calcular a margem financeira.",
                loja=loja,
            )
            continue

        margens[chave] = {
            "preco_venda": round(preco_venda, 2),
            "item_id_loja": item_id,
            "loja": loja,
            "custo_unitario": round(float(custo_unitario), 2),
            "imposto_percentual": round(float(imposto_rate) * 100.0, 4),
            "imposto_valor": round(float(financeiro["imposto"]), 2),
            "tarifa_ml": round(float(tarifa_ml), 2),
            "frete_ml": round(float(frete_ml), 2),
            "valor_liquido": round(float(financeiro["valor_liquido"]), 2),
            "margem_percentual": round(float(financeiro["margem_percentual"]), 2),
            "financeiro_exato": True,
            "tarifa_fonte": str(tarifa_dados.get("ad_cost_source") or "")[:120],
            "frete_fonte": str(
                frete_dados.get("shipping_cost_retry_source")
                or frete_dados.get("shipping_cost_source_path")
                or ""
            )[:120],
        }

    return margens


async def api_medias_compras_listas_pedidos(
    loja: str = "__todas",
    store_id: str = "",
    client_id: str = Depends(medias_common.get_tenant_id)
):
    listas = _carregar_listas_pedidos(client_id)
    escopo = _resolver_escopo_loja_medias(client_id, loja, store_id)
    store_id_alvo = escopo["store_id"]
    if store_id_alvo:
        filtradas = []
        for lista in listas:
            lista_store_id = str((lista or {}).get("store_id") or "").strip()
            if lista_store_id:
                if lista_store_id == store_id_alvo:
                    filtradas.append(lista)
                continue
            try:
                legado = _resolver_escopo_loja_medias(
                    client_id,
                    str((lista or {}).get("loja") or ""),
                    "",
                    exigir_especifica=True,
                )
            except HTTPException:
                continue
            if legado["store_id"] == store_id_alvo:
                filtradas.append(lista)
        listas = filtradas
    m3_lookup = _construir_mapa_m3_sku(client_id)
    return {
        "success": True,
        "listas": [_resumo_lista_pedido(l, m3_lookup) for l in listas],
    }


async def api_medias_compras_skus_ocultos_get(
    request: Request,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    username = _extrair_username_do_request(request)
    prefs = _carregar_preferencias_skus_ocultos_medias(client_id, username)
    return {
        "success": True,
        "skus_ocultos": prefs.get("skus_ocultos") or [],
        "updated_at": prefs.get("updated_at"),
    }


async def api_medias_compras_skus_ocultos_put(
    req: MediasComprasSkusOcultosRequest,
    request: Request,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    username = _extrair_username_do_request(request)
    payload = _salvar_preferencias_skus_ocultos_medias(client_id, username, req.skus_ocultos or [])
    return {
        "success": True,
        "skus_ocultos": payload.get("skus_ocultos") or [],
        "updated_at": payload.get("updated_at"),
    }


async def api_medias_compras_preferencias_colunas_get(client_id: str = Depends(medias_common.get_tenant_id)):
    prefs = _carregar_preferencias_colunas_importacoes(client_id)
    return {
        "success": True,
        "ordem_colunas": prefs.get("ordem_colunas") or [],
        "larguras_colunas": prefs.get("larguras_colunas") or {},
    }


async def api_medias_compras_preferencias_colunas_put(
    req: ListaPedidoPreferenciasColunasRequest,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    payload = _salvar_preferencias_colunas_importacoes(client_id, {
        "ordem_colunas": req.ordem_colunas or [],
        "larguras_colunas": req.larguras_colunas or {},
    })
    return {
        "success": True,
        "ordem_colunas": payload.get("ordem_colunas") or [],
        "larguras_colunas": payload.get("larguras_colunas") or {},
        "updated_at": payload.get("updated_at"),
    }


async def api_medias_compras_concorrentes_links(
    sku: str,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    sku_in = str(sku or "").strip()
    if not sku_in:
        raise HTTPException(status_code=400, detail="SKU ÃƒÂ© obrigatÃƒÂ³rio")

    try:
        rows = await asyncio.to_thread(_carregar_linhas_planilha_concorrentes)
        indice = _indexar_linhas_planilha_concorrentes(rows)
        return _payload_concorrentes_planilha(sku_in, indice.get(_normalizar_sku_planilha_concorrentes(sku_in)))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Erro ao buscar links de concorrentes para SKU '{sku_in}': {e}")
        raise HTTPException(status_code=500, detail="Erro ao buscar links de concorrentes")


def _normalizar_sku_planilha_concorrentes(valor: Any) -> str:
    texto = str(valor or "").strip().upper().replace(" ", "")
    return re.sub(r"\.0+$", "", texto)


def _carregar_linhas_planilha_concorrentes() -> list[list[str]]:
    gc = autenticar_google_sheets()
    if not gc:
        raise HTTPException(status_code=500, detail="Não foi possível autenticar no Google Sheets")

    sh = gc.open_by_key(SPREADSHEET_ID_CONCORRENTES)
    ws = sh.get_worksheet(0)
    if ws is None:
        raise HTTPException(status_code=404, detail="Aba da planilha não encontrada")
    return list(ws.get_all_values() or [])


def _indexar_linhas_planilha_concorrentes(rows: list[list[str]]) -> dict[str, list[str]]:
    if not rows:
        return {}

    header = rows[0]
    sku_col_idx = 0
    for indice, cabecalho in enumerate(header):
        cabecalho_norm = str(cabecalho or "").strip().lower()
        if cabecalho_norm in {
            "sku",
            "código",
            "codigo",
            "cã³digo",
            "cãƒâ³digo",
            "codigo sku",
            "sku code",
        }:
            sku_col_idx = indice
            break

    resultado: dict[str, list[str]] = {}
    for row in rows[1:]:
        if sku_col_idx >= len(row):
            continue
        sku_norm = _normalizar_sku_planilha_concorrentes(row[sku_col_idx])
        if sku_norm and sku_norm not in resultado:
            resultado[sku_norm] = row
    return resultado


def _payload_concorrentes_planilha(sku: str, row: list[str] | None) -> dict[str, Any]:
    linha = row or []

    def _get_col(indice: int) -> str:
        if indice < 0 or indice >= len(linha):
            return ""
        return str(linha[indice] or "").strip()

    return {
        "success": True,
        "sku": str(sku or "").strip(),
        "concorrentes": {
            f"concorrente_{numero}": _get_col(indice)
            for numero, indice in enumerate((4, 8, 12, 16, 20), start=1)
        },
        "concorrentes_valores": {
            f"concorrente_{numero}": _get_col(indice)
            for numero, indice in enumerate((5, 9, 13, 17, 21), start=1)
        },
        "concorrentes_mlb": {
            f"concorrente_{numero}": _get_col(indice)
            for numero, indice in enumerate((2, 6, 10, 14, 18), start=1)
        },
        "concorrentes_preco": {
            f"concorrente_{numero}": _get_col(indice)
            for numero, indice in enumerate((3, 7, 11, 15, 19), start=1)
        },
    }


async def api_medias_compras_lista_pedido_concorrentes_links_lote(
    lista_id: str,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    listas = _carregar_listas_pedidos(client_id)
    lista = next((item for item in listas if str(item.get("id", "")) == str(lista_id)), None)
    if not lista:
        raise HTTPException(status_code=404, detail="Lista de pedidos nao encontrada")

    skus: list[str] = []
    for item in (lista.get("itens") or []):
        sku = _sku_item_lista_pedido(item)
        if sku and sku not in skus:
            skus.append(sku)

    try:
        rows = await asyncio.to_thread(_carregar_linhas_planilha_concorrentes)
        indice = _indexar_linhas_planilha_concorrentes(rows)
        resultados = {
            sku: _payload_concorrentes_planilha(
                sku,
                indice.get(_normalizar_sku_planilha_concorrentes(sku)),
            )
            for sku in skus
        }
        return {
            "success": True,
            "lista_id": str(lista_id),
            "total": len(resultados),
            "resultados": resultados,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Erro ao buscar links de concorrentes da lista '{lista_id}': {e}")
        raise HTTPException(status_code=500, detail="Erro ao buscar links de concorrentes da lista")


async def api_medias_compras_lista_pedido_detalhe(lista_id: str, client_id: str = Depends(medias_common.get_tenant_id)):
    listas = _carregar_listas_pedidos(client_id)
    idx = next((i for i, l in enumerate(listas) if str(l.get("id", "")) == str(lista_id)), -1)
    alvo = listas[idx] if idx >= 0 else None
    if not alvo:
        raise HTTPException(status_code=404, detail="Lista de pedidos nÃ£o encontrada")

    # Backfill: garante coluna persistida de frete internacional para listas antigas.
    # Listas novas guardam o store_id imutavel. As antigas ainda podem trazer o
    # nome atual ou um nome historico unico da loja.
    store_id_antes = str(alvo.get("store_id") or "").strip()
    referencia_loja_cadastro = str(
        store_id_antes or alvo.get("loja") or ""
    ).strip()
    escopo_cadastro_nao_resolvido = False
    if referencia_loja_cadastro and referencia_loja_cadastro != "__todas":
        try:
            from backend.services.cadastro_compatibilidade import (
                visao_produtos_cadastro_contexto_loja,
            )

            contexto_cadastro = visao_produtos_cadastro_contexto_loja(
                client_id,
                referencia_loja_cadastro,
            )
            escopo_cadastro_nao_resolvido = (
                contexto_cadastro.get("scope") == "unresolved"
            )
            store_id_resolvido = str(
                contexto_cadastro.get("store_id") or ""
            ).strip()
            if store_id_resolvido and not store_id_antes:
                alvo["store_id"] = store_id_resolvido
                referencia_loja_cadastro = store_id_resolvido
        except RuntimeError:
            escopo_cadastro_nao_resolvido = True

    itens_recalculados = _recalcular_frete_internacional_itens_lista(
        client_id,
        alvo.get("itens") or [],
        loja=referencia_loja_cadastro,
    )
    itens_persistidos = itens_recalculados
    if escopo_cadastro_nao_resolvido:
        # A resposta permanece fail-closed, mas um GET nunca apaga do JSON a
        # referencia anterior enquanto a identidade da loja estiver ambigua.
        itens_persistidos = [dict(item) for item in itens_recalculados]
        for indice, original in enumerate(alvo.get("itens") or []):
            if indice >= len(itens_persistidos) or not isinstance(original, dict):
                continue
            foto_original = str(original.get("Foto") or "").strip()
            if foto_original:
                itens_persistidos[indice]["Foto"] = foto_original
    mudou_itens = json.dumps(
        itens_persistidos,
        ensure_ascii=False,
        sort_keys=True,
    ) != json.dumps(alvo.get("itens") or [], ensure_ascii=False, sort_keys=True)
    mudou_store_id = str(alvo.get("store_id") or "").strip() != store_id_antes
    if mudou_itens or mudou_store_id:
        alvo["itens"] = itens_persistidos
        alvo["updated_at"] = datetime.now().isoformat(timespec="seconds")
        listas[idx] = alvo
        _salvar_listas_pedidos(client_id, listas)
        _limpar_cache_lista_pedido(client_id, str(alvo.get("id", "") or ""), manter_versao=alvo.get("updated_at"))

    return {
        "success": True,
        "lista": {
            **_resumo_lista_pedido(alvo),
            "itens": itens_recalculados,
        }
    }


async def api_medias_compras_lista_pedido_custo_posto(
    lista_id: str,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    """Return the backend-authoritative landed-cost calculation for one import list."""
    from backend.services import codex_reports_advanced

    context = codex_reports_advanced.build_profile_context(
        info_base=str(medias_common.PASTA_INFO or ""),
        client_id=str(client_id or "default"),
        profile="import_order",
        import_list_id=str(lista_id or ""),
        margin_rows=[],
    )
    analysis = context.get("import_analysis") if isinstance(context.get("import_analysis"), dict) else None
    if not analysis:
        raise HTTPException(status_code=404, detail="Lista de importacao nao encontrada")
    return {
        "success": True,
        "analysis": analysis,
        "supplier_performance": context.get("supplier_performance"),
        "scope": context.get("scope"),
        "data_quality": context.get("data_quality"),
    }


def _item_lista_pedido_compra_aprovada(item: dict[str, Any] | None) -> bool:
    item = item if isinstance(item, dict) else {}
    valor = item.get("compra_aprovada", item.get("Compra aprovada"))
    if valor is True or valor == 1:
        return True
    return str(valor or "").strip().lower() in {"1", "true", "sim", "aprovada", "aprovado"}


def _sku_item_lista_pedido(item: dict[str, Any] | None) -> str:
    item = item if isinstance(item, dict) else {}
    return _normalizar_sku_mes(str(item.get("SKU") or item.get("sku") or "").strip())


def _proteger_itens_aprovados_lista(
    itens_atuais: list[dict] | None,
    itens_novos: list[dict] | None,
) -> list[dict]:
    novos = [dict(item) for item in (itens_novos or []) if isinstance(item, dict)]
    novos_por_sku = {
        _sku_item_lista_pedido(item): item
        for item in novos
        if _sku_item_lista_pedido(item)
    }

    for item_atual in itens_atuais or []:
        sku = _sku_item_lista_pedido(item_atual)
        item_novo = novos_por_sku.get(sku)
        if item_novo and "analise_concorrentes" in item_atual:
            item_novo["analise_concorrentes"] = copy.deepcopy(item_atual["analise_concorrentes"])

        if not _item_lista_pedido_compra_aprovada(item_atual):
            continue
        if not item_novo:
            raise HTTPException(
                status_code=409,
                detail=f"O SKU {sku} esta aprovado e nao pode ser excluido. Desfaca a aprovacao dentro do SKU.",
            )
        if not _item_lista_pedido_compra_aprovada(item_novo):
            raise HTTPException(
                status_code=409,
                detail=f"A aprovacao do SKU {sku} so pode ser desfeita dentro do SKU.",
            )

        item_novo.pop("Compra aprovada", None)
        item_novo.pop("Compra aprovada em", None)
        item_novo["compra_aprovada"] = True
        aprovado_em = str(
            item_atual.get("compra_aprovada_em")
            or item_atual.get("Compra aprovada em")
            or ""
        ).strip()
        if aprovado_em:
            item_novo["compra_aprovada_em"] = aprovado_em

    return novos


async def api_medias_compras_lista_pedido_editar(
    lista_id: str,
    req: ListaPedidoUpdateRequest,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    listas = _carregar_listas_pedidos(client_id)
    idx = next((i for i, l in enumerate(listas) if str(l.get("id", "")) == str(lista_id)), -1)
    if idx < 0:
        raise HTTPException(status_code=404, detail="Lista de pedidos nÃ£o encontrada")
    lista = listas[idx]
    campos_informados = (
        getattr(req, "model_fields_set", None)
        or getattr(req, "__fields_set__", set())
        or set()
    )
    alterou = False
    alterou_loja = False
    if req.nome_lista is not None:
        nome_lista = str(req.nome_lista or "").strip()
        if nome_lista:
            lista["nome_lista"] = nome_lista
            alterou = True
    if req.status is not None:
        lista["status"] = _normalizar_status_lista_pedido(req.status)
        alterou = True
    if "loja" in campos_informados or "store_id" in campos_informados:
        escopo = _resolver_escopo_loja_medias(
            client_id,
            req.loja if "loja" in campos_informados else lista.get("loja"),
            req.store_id if "store_id" in campos_informados else "",
        )
        lista["loja"] = escopo["loja"]
        lista["store_id"] = escopo["store_id"]
        alterou = True
        alterou_loja = True

    campos_logisticos = (
        "numero_invoice",
        "supplier", "currency", "incoterm", "exchange_rate", "lead_time_days",
        "moq_default", "package_multiple_default", "order_date", "promised_ship_date",
        "actual_ship_date", "eta_date", "customs_clearance_date", "received_at",
        "promised_delivery_date",
    )
    for campo in campos_logisticos:
        if campo not in campos_informados:
            continue
        valor = getattr(req, campo, None)
        if campo in {"exchange_rate", "lead_time_days", "moq_default", "package_multiple_default"}:
            valor = max(0.0, float(valor or 0))
            if campo == "package_multiple_default" and valor <= 0:
                valor = 1.0
        elif valor is not None:
            valor = str(valor).strip()
        lista[campo] = valor
        alterou = True

    if "itens" in campos_informados:
        itens_protegidos = _proteger_itens_aprovados_lista(lista.get("itens") or [], req.itens or [])
        itens_norm = _recalcular_frete_internacional_itens_lista(
            client_id,
            itens_protegidos,
            loja=str(lista.get("store_id") or lista.get("loja") or ""),
        )
        lista["itens"] = itens_norm
        alterou = True
    elif alterou_loja:
        # A identidade da loja e parte da identidade da Foto. A troca de loja
        # precisa limpar/reidratar as referencias no mesmo commit, mesmo quando
        # o PATCH nao reenviar a colecao de itens.
        lista["itens"] = _recalcular_frete_internacional_itens_lista(
            client_id,
            lista.get("itens") or [],
            loja=str(lista.get("store_id") or lista.get("loja") or ""),
        )

    if alterou:
        lista["updated_at"] = datetime.now().isoformat(timespec="seconds")
    listas[idx] = lista
    _salvar_listas_pedidos(client_id, listas)
    if alterou:
        _limpar_cache_lista_pedido(client_id, str(lista.get("id", "") or ""), manter_versao=lista.get("updated_at"))

    return {
        "success": True,
        "lista": {
            **_resumo_lista_pedido(lista),
            "itens": lista.get("itens") or [],
        }
    }


async def api_medias_compras_lista_pedido_atualizar_aprovacao_sku(
    lista_id: str,
    sku: str,
    req: ListaPedidoSkuAprovacaoRequest,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    listas = _carregar_listas_pedidos(client_id)
    idx_lista = next((i for i, l in enumerate(listas) if str(l.get("id", "")) == str(lista_id)), -1)
    if idx_lista < 0:
        raise HTTPException(status_code=404, detail="Lista de pedidos nao encontrada")

    lista = listas[idx_lista]
    sku_ref = _normalizar_sku_mes(str(sku or "").strip())
    if not sku_ref:
        raise HTTPException(status_code=400, detail="SKU invalido para aprovacao")
    itens = [dict(item) for item in (lista.get("itens") or []) if isinstance(item, dict)]
    idx_item = next((i for i, item in enumerate(itens) if _sku_item_lista_pedido(item) == sku_ref), -1)
    if idx_item < 0:
        raise HTTPException(status_code=404, detail="SKU nao encontrado na lista de pedidos")

    item = itens[idx_item]
    ja_aprovada = _item_lista_pedido_compra_aprovada(item)
    item.pop("Compra aprovada", None)
    item.pop("Compra aprovada em", None)
    item["compra_aprovada"] = bool(req.aprovada)
    if req.aprovada:
        aprovado_em = str(item.get("compra_aprovada_em") or "").strip() if ja_aprovada else ""
        item["compra_aprovada_em"] = aprovado_em or datetime.now().isoformat(timespec="seconds")
    else:
        item.pop("compra_aprovada_em", None)

    itens[idx_item] = item
    lista["itens"] = itens
    lista["updated_at"] = datetime.now().isoformat(timespec="seconds")
    listas[idx_lista] = lista
    _salvar_listas_pedidos(client_id, listas)
    _limpar_cache_lista_pedido(client_id, str(lista.get("id", "") or ""), manter_versao=lista.get("updated_at"))

    return {
        "success": True,
        "sku": str(item.get("SKU") or sku),
        "compra_aprovada": bool(req.aprovada),
        "item": item,
        "lista": {
            **_resumo_lista_pedido(lista),
            "itens": itens,
        },
    }


async def api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku(
    lista_id: str,
    sku: str,
    req: ListaPedidoSkuAnaliseConcorrentesRequest,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    listas = _carregar_listas_pedidos(client_id)
    idx_lista = next((i for i, l in enumerate(listas) if str(l.get("id", "")) == str(lista_id)), -1)
    if idx_lista < 0:
        raise HTTPException(status_code=404, detail="Lista de pedidos nao encontrada")

    lista = listas[idx_lista]
    sku_ref = _normalizar_sku_mes(str(sku or "").strip())
    if not sku_ref:
        raise HTTPException(status_code=400, detail="SKU invalido para analise de concorrentes")

    itens = [dict(item) for item in (lista.get("itens") or []) if isinstance(item, dict)]
    idx_item = next((i for i, item in enumerate(itens) if _sku_item_lista_pedido(item) == sku_ref), -1)
    if idx_item < 0:
        raise HTTPException(status_code=404, detail="SKU nao encontrado na lista de pedidos")
    item = itens[idx_item]

    custo_unitario_informado = req.custo_unitario
    if custo_unitario_informado is not None:
        custo_unitario_informado = float(custo_unitario_informado)
        if not math.isfinite(custo_unitario_informado) or custo_unitario_informado < 0:
            raise HTTPException(status_code=400, detail="Custo unitario invalido para analise de concorrentes")

    precos_recebidos = dict(req.precos_concorrentes or {})
    anuncios_recebidos = dict(req.anuncios_loja or {})
    chaves_invalidas = sorted((set(precos_recebidos) | set(anuncios_recebidos)) - _CHAVES_CONCORRENTES)
    if chaves_invalidas:
        raise HTTPException(status_code=400, detail="Concorrente invalido para analise")

    precos_normalizados: dict[str, float] = {}
    for chave in sorted(precos_recebidos):
        preco_venda = float(precos_recebidos[chave])
        if not math.isfinite(preco_venda) or preco_venda <= 0:
            raise HTTPException(status_code=400, detail="Preco de concorrente invalido para analise")
        precos_normalizados[chave] = preco_venda

    anuncios_normalizados: dict[str, str] = {}
    for chave, item_id_raw in anuncios_recebidos.items():
        item_id_texto = str(item_id_raw or "").strip()
        if not item_id_texto:
            continue
        item_id = _normalizar_item_id_mlb(item_id_texto)
        if not item_id:
            raise HTTPException(status_code=400, detail="Anuncio da loja invalido para analise")
        anuncios_normalizados[chave] = item_id

    margens: dict[str, dict[str, Any]] = {}
    if precos_normalizados:
        try:
            margens = await asyncio.to_thread(
                _calcular_margens_concorrentes_promocoes,
                client_id,
                str(lista.get("loja") or ""),
                str(item.get("SKU") or sku),
                precos_normalizados,
                anuncios_normalizados,
            )
        except Exception:
            logger.warning("[IMPORTACOES] Calculo financeiro exato das margens concorrentes indisponivel")
            margens = {
                chave: _margem_concorrente_indisponivel(
                    preco_venda,
                    anuncios_normalizados.get(chave, ""),
                    "Cálculo financeiro temporariamente indisponível.",
                )
                for chave, preco_venda in precos_normalizados.items()
            }

    custos_cadastro = {
        float(dados["custo_unitario"])
        for dados in margens.values()
        if _numero_finito_margem(dados.get("custo_unitario")) is not None
    }
    custo_unitario_cadastro = next(iter(custos_cadastro)) if len(custos_cadastro) == 1 else None

    atualizado_em = datetime.now().isoformat(timespec="seconds")
    analise_concorrentes = {
        "custo_unitario": round(custo_unitario_cadastro, 2) if custo_unitario_cadastro is not None else None,
        "fonte_custo": "cadastro_sku_loja",
        "metodo": "analise_promocoes_ml_liquida",
        "margens": margens,
        "atualizado_em": atualizado_em,
    }

    # O cálculo consulta serviços externos e pode levar alguns segundos. Recarregue
    # a lista antes de persistir para não desfazer uma aprovação, exclusão ou edição
    # que tenha ocorrido enquanto a margem era calculada.
    listas_atuais = _carregar_listas_pedidos(client_id)
    idx_lista_atual = next(
        (i for i, atual in enumerate(listas_atuais) if str(atual.get("id", "")) == str(lista_id)),
        -1,
    )
    if idx_lista_atual < 0:
        raise HTTPException(status_code=404, detail="Lista de pedidos nao encontrada")

    lista_atual = listas_atuais[idx_lista_atual]
    if str(lista_atual.get("loja") or "").strip() != str(lista.get("loja") or "").strip():
        raise HTTPException(
            status_code=409,
            detail="A loja da lista mudou durante a análise. Recarregue para calcular novamente.",
        )

    itens_atuais = [dict(atual) for atual in (lista_atual.get("itens") or []) if isinstance(atual, dict)]
    idx_item_atual = next(
        (i for i, atual in enumerate(itens_atuais) if _sku_item_lista_pedido(atual) == sku_ref),
        -1,
    )
    if idx_item_atual < 0:
        raise HTTPException(status_code=404, detail="SKU nao encontrado na lista de pedidos")

    item_atual = itens_atuais[idx_item_atual]
    item_atual["analise_concorrentes"] = analise_concorrentes
    itens_atuais[idx_item_atual] = item_atual
    lista_atual["itens"] = itens_atuais
    lista_atual["updated_at"] = atualizado_em
    listas_atuais[idx_lista_atual] = lista_atual
    _salvar_listas_pedidos(client_id, listas_atuais)
    _limpar_cache_lista_pedido(
        client_id,
        str(lista_atual.get("id", "") or ""),
        manter_versao=lista_atual.get("updated_at"),
    )

    return {
        "success": True,
        "sku": str(item_atual.get("SKU") or sku),
        "analise_concorrentes": analise_concorrentes,
        "item": item_atual,
        "lista": {
            **_resumo_lista_pedido(lista_atual),
            "itens": itens_atuais,
        },
    }


async def api_medias_compras_lista_pedido_adicionar_sku(
    lista_id: str,
    req: ListaPedidoAddSkuRequest,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    listas = _carregar_listas_pedidos(client_id)
    idx = next((i for i, l in enumerate(listas) if str(l.get("id", "")) == str(lista_id)), -1)
    if idx < 0:
        raise HTTPException(status_code=404, detail="Lista de pedidos nÃ£o encontrada")
    lista = listas[idx]

    sku_input = str(req.sku or "").strip()
    sku_in = _normalizar_sku_mes(sku_input)
    if not sku_in:
        raise HTTPException(status_code=400, detail="SKU invalido")

    # Regra solicitada: aceitar apenas SKU numÃƒÂ©rico existente no cadastro.
    sku_input_compacto = re.sub(r"\s+", "", sku_input)
    if not re.fullmatch(r"\d+", sku_input_compacto or ""):
        raise HTTPException(status_code=400, detail="Somente SKU numÃƒÂ©rico ÃƒÂ© permitido nesta inclusÃƒÂ£o")

    qtd = max(0, int(round(float(req.quantidade or 0))))
    if qtd <= 0:
        raise HTTPException(status_code=400, detail="Quantidade deve ser maior que zero")

    vu = round(float(req.valor_unitario or 0), 2)
    if vu <= 0:
        raise HTTPException(status_code=400, detail="Valor unitÃƒÂ¡rio deve ser maior que zero")

    # Busca dados do cadastro no backend para garantir consistÃƒÂªncia da inclusÃƒÂ£o.
    try:
        prod_resp = await obter_produto_cadastro_query(sku=sku_in, client_id=client_id)
        produto = (prod_resp or {}).get("produto") or {}
    except HTTPException:
        # Fallback: busca por comparacao normalizada na listagem inteira do cadastro.
        produtos = await listar_produtos_cadastro(client_id=client_id)
        alvo_cmp = re.sub(r"[^A-Za-z0-9]", "", sku_in).upper()
        produto = None
        for p in (produtos or []):
            sku_p = _normalizar_sku_mes(str((p or {}).get("sku") or ""))
            sku_cmp = re.sub(r"[^A-Za-z0-9]", "", sku_p).upper()
            if sku_cmp and sku_cmp == alvo_cmp:
                produto = p
                break
        if not produto:
            raise HTTPException(status_code=404, detail="SKU nÃ£o encontrado no cadastro")

    from backend.services.cadastro_compatibilidade import (
        mesclar_produtos_legados_com_contexto_loja,
    )

    contexto_cadastro = mesclar_produtos_legados_com_contexto_loja(
        client_id,
        [produto] if isinstance(produto, dict) else [],
        str(lista.get("store_id") or lista.get("loja") or ""),
    )
    produto = next(
        (
            dict(item)
            for item in contexto_cadastro.get("produtos") or []
            if _normalizar_sku_mes(str(item.get("sku") or "")) == sku_in
        ),
        None,
    )
    if not produto:
        raise HTTPException(status_code=404, detail="SKU nÃ£o encontrado no cadastro desta loja")
    store_id_cadastro = str(contexto_cadastro.get("store_id") or "").strip()

    def _norm_prod_key(chave: str) -> str:
        txt = unicodedata.normalize("NFKD", str(chave or ""))
        txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
        txt = txt.lower().replace("_", " ").strip()
        txt = re.sub(r"\s+", " ", txt)
        txt = re.sub(r"[^a-z0-9 ]", "", txt)
        txt = txt.strip()
        if txt.startswith("cg "):
            txt = txt[3:].strip()
        return txt

    def _pick_prod(cands: list[str]) -> str:
        if not isinstance(produto, dict):
            return ""

        mapa = {}
        for k, v in produto.items():
            if v is None or not str(v).strip():
                continue
            nk = _norm_prod_key(str(k or ""))
            if nk and nk not in mapa:
                mapa[nk] = str(v).strip()

        for c in cands:
            key = _norm_prod_key(str(c or ""))
            if not key:
                continue
            val = mapa.get(key)
            if val:
                return val

            # Fallback por inclusÃƒÂ£o para colunas longas/variantes importadas de planilha.
            for mk, mv in mapa.items():
                if mk == key or key in mk or mk in key:
                    if mv:
                        return mv
        return ""

    sku_final = _normalizar_sku_mes(_pick_prod(["sku"]) or sku_in)
    if not re.fullmatch(r"\d+", str(sku_final or "")):
        raise HTTPException(status_code=400, detail="SKU do cadastro nÃ£o ÃƒÂ© numÃƒÂ©rico")
    titulo = _pick_prod([
        "produto bling", "produtos bling", "produto blig", "produtos blig",
        "titulo do produto em ingles", "titulo do produto em ingles",
        "titulo em ingles", "titulo", "product name", "nome", "produto",
        "traducao ptbr ou nome na bling"
    ])
    foto = _resolver_foto_cadastro_sku(
        client_id,
        sku_final,
        _pick_prod(["foto", "imagem", "url foto", "link foto"]),
        store_id_cadastro or None,
    )
    oem = _pick_prod(["oem", "codigo oem", "part number", "oem model", "oem model"])
    cor_lado = _pick_prod(["color side", "color/side", "cor lado", "cor/lado", "lado cor", "lado/cor", "lado", "cor", "color", "side"])
    link = _pick_prod(["link", "url", "link aliexpress", "url aliexpress", "mlb principal", "url ml"])

    itens = [_normalizar_item_lista_pedido(i) for i in (lista.get("itens") or [])]
    idx_existente = next((i for i, it in enumerate(itens) if _normalizar_sku_mes(it.get("SKU", "")) == sku_final), -1)

    item_novo = {
        "SKU": sku_final,
        "Foto": foto,
        TITULO_PRODUTO_INGLES_KEY: titulo,
        "OEM": oem,
        COR_LADO_LISTA_PEDIDO_KEY: cor_lado,
        "Link": link,
        "Quantidade": qtd,
        "Valor unidade": vu,
        "Valor total": round(qtd * vu, 2),
    }

    acao = "adicionado"
    if idx_existente >= 0:
        itens[idx_existente] = _normalizar_item_lista_pedido({**itens[idx_existente], **item_novo})
        acao = "atualizado"
    else:
        itens.append(_normalizar_item_lista_pedido(item_novo))

    lista["itens"] = _recalcular_frete_internacional_itens_lista(
        client_id,
        itens,
        loja=str(lista.get("store_id") or lista.get("loja") or ""),
    )
    lista["updated_at"] = datetime.now().isoformat(timespec="seconds")
    listas[idx] = lista
    _salvar_listas_pedidos(client_id, listas)
    _limpar_cache_lista_pedido(client_id, str(lista.get("id", "") or ""), manter_versao=lista.get("updated_at"))

    return {
        "success": True,
        "acao": acao,
        "sku": sku_final,
        "lista": {
            **_resumo_lista_pedido(lista),
            "itens": lista.get("itens") or [],
        },
    }


async def api_medias_compras_lista_pedido_atualizar_status(
    lista_id: str,
    req: ListaPedidoStatusRequest,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    listas = _carregar_listas_pedidos(client_id)
    idx = next((i for i, l in enumerate(listas) if str(l.get("id", "")) == str(lista_id)), -1)
    if idx < 0:
        raise HTTPException(status_code=404, detail="Lista de pedidos nÃ£o encontrada")

    lista = listas[idx]
    lista["status"] = _normalizar_status_lista_pedido(req.status)
    lista["updated_at"] = datetime.now().isoformat(timespec="seconds")
    listas[idx] = lista
    _salvar_listas_pedidos(client_id, listas)
    _limpar_cache_lista_pedido(client_id, str(lista.get("id", "") or ""), manter_versao=lista.get("updated_at"))

    return {
        "success": True,
        "lista": {
            **_resumo_lista_pedido(lista),
            "itens": lista.get("itens") or [],
        }
    }

def _lista_pedido_esta_em_importacoes(lista: dict[str, Any] | None) -> bool:
    status = unicodedata.normalize("NFD", str((lista or {}).get("status", "") or ""))
    status = "".join(ch for ch in status if unicodedata.category(ch) != "Mn")
    return status.strip().lower() == "analisando orcamento"


async def api_medias_compras_lista_pedido_excluir(
    lista_id: str,
    request: Request,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    listas = _carregar_listas_pedidos(client_id)
    idx = next((i for i, l in enumerate(listas) if str(l.get("id", "")) == str(lista_id)), -1)
    if idx < 0:
        raise HTTPException(status_code=404, detail="Lista de pedidos nÃ£o encontrada")

    lista = listas[idx]
    origem_delete = str(request.headers.get("x-jk-delete-origin", "") or "").strip().lower()
    if _lista_pedido_esta_em_importacoes(lista) and origem_delete != "importacoes":
        raise HTTPException(
            status_code=409,
            detail="Listas em Importacoes so podem ser excluidas pelo modulo Importacoes.",
        )

    removida = listas.pop(idx)
    _salvar_listas_pedidos(client_id, listas)
    _limpar_cache_lista_pedido(client_id, str(removida.get("id", "") or ""))
    return {
        "success": True,
        "lista_removida": {
            "id": str(removida.get("id", "") or ""),
            "nome_lista": str(removida.get("nome_lista", "") or ""),
        }
    }


async def api_medias_compras_lista_pedido_download(lista_id: str, client_id: str = Depends(medias_common.get_tenant_id)):
    listas = _carregar_listas_pedidos(client_id)
    lista = next((l for l in listas if str(l.get("id", "")) == str(lista_id)), None)
    if not lista:
        raise HTTPException(status_code=404, detail="Lista de pedidos nÃ£o encontrada")

    nome_lista = str(lista.get("nome_lista", "lista_pedido") or "lista_pedido").strip()
    nome_base = re.sub(r"[^A-Za-z0-9_-]+", "_", nome_lista).strip("_") or "lista_pedido"
    nome_arquivo = f"{nome_base}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    versao_lista = str(lista.get("updated_at") or lista.get("created_at") or "")
    cache_key = f"{client_id}:{lista_id}:{versao_lista}"
    caminho_cache = _arquivo_cache_lista_pedido(client_id, str(lista_id), versao_lista)

    # Cache persistente em disco (mais estÃƒÂ¡vel entre requisiÃƒÂ§ÃƒÂµes/processos).
    if os.path.exists(caminho_cache):
        headers = {
            "Content-Disposition": f"attachment; filename={nome_arquivo}"
        }
        return FileResponse(
            caminho_cache,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=nome_arquivo,
            headers=headers,
        )

    file_bytes = LISTA_PEDIDO_XLSX_CACHE.get(cache_key)
    if not file_bytes:
        file_bytes = _gerar_excel_lista_pedido_bytes(
            nome_lista,
            lista.get("itens") or [],
            client_id=client_id,
            loja=str(lista.get("store_id") or lista.get("loja") or ""),
        )
        LISTA_PEDIDO_XLSX_CACHE[cache_key] = file_bytes
        # MantÃ©m cache pequeno para evitar crescimento sem controle.
        while len(LISTA_PEDIDO_XLSX_CACHE) > LISTA_PEDIDO_XLSX_CACHE_MAX_ITENS:
            try:
                LISTA_PEDIDO_XLSX_CACHE.pop(next(iter(LISTA_PEDIDO_XLSX_CACHE)))
            except Exception:
                break

    try:
        with open(caminho_cache, "wb") as f:
            f.write(file_bytes)
        _limpar_cache_lista_pedido(client_id, str(lista_id), manter_versao=versao_lista)
    except Exception:
        pass

    headers = {
        "Content-Disposition": f"attachment; filename={nome_arquivo}"
    }
    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


def _data_aprovacao_commercial_invoice(lista: dict[str, Any]) -> str:
    itens = [item for item in (lista.get("itens") or []) if isinstance(item, dict)]
    datas = [
        str(item.get("compra_aprovada_em") or item.get("Compra aprovada em") or "").strip()
        for item in itens
    ]
    datas = [data for data in datas if data]
    return max(datas, default="")


async def api_medias_compras_lista_pedido_commercial_invoice(
    lista_id: str,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    listas = _carregar_listas_pedidos(client_id)
    lista = next((item for item in listas if str(item.get("id", "")) == str(lista_id)), None)
    if not lista:
        raise HTTPException(status_code=404, detail="Lista de pedidos nao encontrada")

    data_aprovacao = _data_aprovacao_commercial_invoice(lista)
    file_bytes = _gerar_commercial_invoice_bytes(
        lista,
        client_id=client_id,
        data_aprovacao=data_aprovacao,
    )
    nome_base = (
        str(lista.get("numero_invoice") or lista.get("invoice") or "").strip()
        or str(lista.get("nome_lista") or "").strip()
        or str(lista_id).strip()
    )
    nome_seguro = re.sub(r"[^A-Za-z0-9_-]+", "_", nome_base).strip("_")
    if not nome_seguro:
        nome_seguro = re.sub(r"[^A-Za-z0-9_-]+", "_", str(lista_id)).strip("_") or "lista"
    nome_arquivo = f"commercial_invoice_{nome_seguro}.xlsx"
    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nome_arquivo}"'},
    )


async def api_medias_compras_lista_pedido_gerar_download(lista_id: str, client_id: str = Depends(medias_common.get_tenant_id)):
    listas = _carregar_listas_pedidos(client_id)
    lista = next((l for l in listas if str(l.get("id", "")) == str(lista_id)), None)
    if not lista:
        raise HTTPException(status_code=404, detail="Lista de pedidos nÃ£o encontrada")

    nome_lista = str(lista.get("nome_lista", "lista_pedido") or "lista_pedido").strip()
    nome_base = re.sub(r"[^A-Za-z0-9_-]+", "_", nome_lista).strip("_") or "lista_pedido"
    nome_arquivo = f"{nome_base}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    versao_lista = str(lista.get("updated_at") or lista.get("created_at") or "")
    cache_key = f"{client_id}:{lista_id}:{versao_lista}"
    caminho_cache = _arquivo_cache_lista_pedido(client_id, str(lista_id), versao_lista)

    if os.path.exists(caminho_cache):
        return {
            "success": True,
            "download_url": f"/api/medias-compras/listas-pedidos/{lista_id}/download",
            "filename": nome_arquivo,
            "lista_id": str(lista.get("id", "") or ""),
        }

    file_bytes = None
    file_bytes = LISTA_PEDIDO_XLSX_CACHE.get(cache_key)

    if file_bytes is None:
        file_bytes = _gerar_excel_lista_pedido_bytes(
            nome_lista,
            lista.get("itens") or [],
            client_id=client_id,
            loja=str(lista.get("store_id") or lista.get("loja") or ""),
        )
        LISTA_PEDIDO_XLSX_CACHE[cache_key] = file_bytes
        while len(LISTA_PEDIDO_XLSX_CACHE) > LISTA_PEDIDO_XLSX_CACHE_MAX_ITENS:
            try:
                LISTA_PEDIDO_XLSX_CACHE.pop(next(iter(LISTA_PEDIDO_XLSX_CACHE)))
            except Exception:
                break
        _salvar_bytes_cache_lista_pedido(client_id, str(lista_id), versao_lista, file_bytes)

    file_id = str(uuid.uuid4())
    TEMP_FILES_STORAGE[file_id] = file_bytes
    TEMP_FILES_META[file_id] = {
        "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "filename": nome_arquivo,
    }
    return {
        "success": True,
        "file_id": file_id,
        "filename": nome_arquivo,
        "lista_id": str(lista.get("id", "") or ""),
    }




async def api_medias_compras_download(file_id: str, client_id: str = Depends(medias_common.get_tenant_id)):
    file_bytes = TEMP_FILES_STORAGE.get(file_id)
    if not file_bytes:
        raise HTTPException(status_code=404, detail="Arquivo nÃ£o encontrado ou expirado")

    meta = TEMP_FILES_META.get(file_id, {})
    media_type = meta.get("mime") or "application/octet-stream"
    filename = meta.get("filename") or f"arquivo_{file_id}.xlsx"

    headers = {
        "Content-Disposition": f"attachment; filename={filename}"
    }
    return StreamingResponse(io.BytesIO(file_bytes), media_type=media_type, headers=headers)



def configure_medias_compras_listas_runtime(runtime_module=None):
    return _configure_runtime_globals(runtime_module)


configure_medias_compras_listas_runtime()

__all__ = [
    "configure_medias_compras_listas_runtime",
    "api_medias_compras_listas_pedidos",
    "api_medias_compras_skus_ocultos_get",
    "api_medias_compras_skus_ocultos_put",
    "api_medias_compras_preferencias_colunas_get",
    "api_medias_compras_preferencias_colunas_put",
    "api_medias_compras_concorrentes_links",
    "api_medias_compras_lista_pedido_concorrentes_links_lote",
    "api_medias_compras_lista_pedido_detalhe",
    "api_medias_compras_lista_pedido_custo_posto",
    "api_medias_compras_lista_pedido_editar",
    "api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku",
    "api_medias_compras_lista_pedido_atualizar_aprovacao_sku",
    "api_medias_compras_lista_pedido_adicionar_sku",
    "api_medias_compras_lista_pedido_atualizar_status",
    "api_medias_compras_lista_pedido_excluir",
    "api_medias_compras_lista_pedido_download",
    "api_medias_compras_lista_pedido_commercial_invoice",
    "api_medias_compras_lista_pedido_gerar_download",
    "api_medias_compras_download",
]
