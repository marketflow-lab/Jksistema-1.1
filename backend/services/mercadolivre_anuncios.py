"""Mercado Livre listing, visits and item detail services."""

from __future__ import annotations

import datetime as dt
import io
import json
import re
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterable, Iterator, Optional

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from backend.services.mercadolivre_cache import _ml_cache_get, _ml_cache_set
from backend.services.mercadolivre_context import _ctx


ML_EXPORT_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_ML_EXPORT_PAGE_LIMIT = 100
_ML_EXPORT_DETAIL_BATCH = 100
# XLSX aceita 1.048.576 linhas; a primeira fica reservada ao cabecalho.
_ML_EXPORT_MAX_ITEMS = 1_048_575
_ML_EXPORT_ACTIVE_LOCK = threading.Lock()
_ML_EXPORT_ACTIVE_KEYS: set[tuple[str, str]] = set()

_ML_EXPORT_ANUNCIOS_HEADERS = (
    "Loja",
    "ID do anuncio",
    "SKU",
    "SKUs das variacoes",
    "Titulo",
    "Familia",
    "Tipo do anuncio",
    "Condicao",
    "Preco atual",
    "Preco padrao",
    "Preco original",
    "Desconto (%)",
    "Custo do anuncio",
    "Tarifa fixa",
    "Tarifa de publicacao",
    "Taxa de venda (%)",
    "Custo de frete do vendedor",
    "Frete do comprador",
    "Frete base",
    "Frete de lista",
    "Frete gratis",
    "Tipo logistico",
    "Modo de envio",
    "Estoque disponivel",
    "Vendidas acumuladas",
    "Status",
    "Tem promocao",
    "ID da promocao",
    "Tipo da promocao",
    "Anuncio de catalogo",
    "ID do produto de catalogo",
    "ID do produto do vendedor",
    "Categoria",
    "Dominio",
    "Canais",
    "Data de criacao",
    "Ultima atualizacao",
    "Data de inicio",
    "Data de termino",
    "Link",
    "Miniatura",
)

_ML_EXPORT_VARIACOES_HEADERS = (
    "Loja",
    "ID do anuncio",
    "SKU pai",
    "ID da variacao",
    "Variacao",
    "SKU da variacao",
    "ID de inventario",
    "Preco",
    "Estoque disponivel",
    "Vendidas acumuladas",
    "ID da imagem",
)


def _ml_export_http_error(ctx, resp, fallback: str) -> HTTPException:
    try:
        detail = ctx.ml_parse_error_detail(resp, fallback)
    except Exception:
        detail = fallback
    return HTTPException(status_code=502, detail=detail or fallback)


def _ml_export_item_id(raw: Any) -> str:
    if isinstance(raw, dict):
        raw = raw.get("id")
    return str(raw or "").strip()


def _ml_export_total(raw: Any) -> Optional[int]:
    try:
        total = int(raw)
    except (TypeError, ValueError):
        return None
    return max(0, total)


def _ml_export_listar_ids_offset(
    client_id: str,
    loja: str,
    cfg: dict,
    url: str,
) -> tuple[list[str], dict]:
    """Fallback paginado com prova de cobertura pelo paging.total."""
    ctx = _ctx()
    ids: list[str] = []
    vistos: set[str] = set()
    offset = 0
    total_esperado: Optional[int] = None

    while True:
        resp, cfg = ctx.ml_api_request_com_retry(
            client_id,
            loja,
            cfg,
            "GET",
            url,
            params={"offset": offset, "limit": _ML_EXPORT_PAGE_LIMIT, "status": "active"},
            timeout=35,
            max_attempts=3,
        )
        if resp.status_code != 200:
            raise _ml_export_http_error(ctx, resp, "Erro ao listar todos os anuncios ativos do Mercado Livre")

        data = resp.json() or {}
        batch = data.get("results") or []
        total_pagina = _ml_export_total((data.get("paging") or {}).get("total"))
        if total_pagina is None:
            raise HTTPException(status_code=502, detail="Mercado Livre nao informou o total de anuncios ativos.")
        total_esperado = max(total_esperado or 0, total_pagina)
        if total_esperado > _ML_EXPORT_MAX_ITEMS:
            raise HTTPException(
                status_code=413,
                detail=f"A loja possui mais de {_ML_EXPORT_MAX_ITEMS} anuncios ativos; a exportacao foi interrompida sem gerar arquivo parcial.",
            )

        for raw in batch:
            item_id = _ml_export_item_id(raw)
            if item_id and item_id not in vistos:
                vistos.add(item_id)
                ids.append(item_id)
                if len(ids) > _ML_EXPORT_MAX_ITEMS:
                    raise HTTPException(
                        status_code=413,
                        detail="A quantidade de anuncios ativos excede o limite de linhas de uma planilha Excel.",
                    )

        if len(ids) >= total_esperado:
            return ids, cfg
        if not batch:
            raise HTTPException(
                status_code=502,
                detail=f"Exportacao incompleta: o Mercado Livre informou {total_esperado} anuncios, mas somente {len(ids)} IDs unicos foram coletados.",
            )

        offset += len(batch)
        if offset > _ML_EXPORT_MAX_ITEMS:
            raise HTTPException(status_code=413, detail="Limite seguro da exportacao de anuncios excedido.")


def _ml_export_listar_ids_ativos(client_id: str, loja: str, cfg: dict, user_id: str) -> tuple[list[str], dict]:
    """Lista todos os IDs ativos por scan e nunca aceita truncamento silencioso."""
    ctx = _ctx()
    url = f"https://api.mercadolibre.com/users/{user_id}/items/search"
    ids: list[str] = []
    vistos: set[str] = set()
    scroll_vistos: set[str] = set()
    scroll_id = ""
    total_esperado: Optional[int] = None

    for pagina in range((_ML_EXPORT_MAX_ITEMS // _ML_EXPORT_PAGE_LIMIT) + 2):
        params = {"search_type": "scan", "limit": _ML_EXPORT_PAGE_LIMIT}
        if pagina == 0:
            params["status"] = "active"
        else:
            params["scroll_id"] = scroll_id

        resp, cfg = ctx.ml_api_request_com_retry(
            client_id,
            loja,
            cfg,
            "GET",
            url,
            params=params,
            timeout=35,
            max_attempts=3,
        )
        if resp.status_code != 200:
            if pagina == 0:
                return _ml_export_listar_ids_offset(client_id, loja, cfg, url)
            raise _ml_export_http_error(ctx, resp, "Erro durante a coleta completa dos anuncios ativos")

        data = resp.json() or {}
        batch = data.get("results") or []
        total_pagina = _ml_export_total((data.get("paging") or {}).get("total"))
        if total_pagina is not None:
            total_esperado = max(total_esperado or 0, total_pagina)
            if total_esperado > _ML_EXPORT_MAX_ITEMS:
                raise HTTPException(
                    status_code=413,
                    detail=f"A loja possui mais de {_ML_EXPORT_MAX_ITEMS} anuncios ativos; a exportacao foi interrompida sem gerar arquivo parcial.",
                )

        adicionados = 0
        for raw in batch:
            item_id = _ml_export_item_id(raw)
            if item_id and item_id not in vistos:
                vistos.add(item_id)
                ids.append(item_id)
                adicionados += 1
                if len(ids) > _ML_EXPORT_MAX_ITEMS:
                    raise HTTPException(
                        status_code=413,
                        detail="A quantidade de anuncios ativos excede o limite de linhas de uma planilha Excel.",
                    )

        if total_esperado is not None and len(ids) >= total_esperado:
            return ids, cfg
        if not batch:
            if total_esperado in (None, len(ids)):
                return ids, cfg
            raise HTTPException(
                status_code=502,
                detail=f"Exportacao incompleta: o Mercado Livre informou {total_esperado} anuncios, mas somente {len(ids)} IDs unicos foram coletados.",
            )

        proximo_scroll = str(data.get("scroll_id") or "").strip()
        if not proximo_scroll:
            return _ml_export_listar_ids_offset(client_id, loja, cfg, url)
        if proximo_scroll in scroll_vistos or (pagina > 0 and adicionados == 0):
            raise HTTPException(status_code=502, detail="Exportacao interrompida porque a paginacao do Mercado Livre nao avancou.")
        scroll_vistos.add(proximo_scroll)
        scroll_id = proximo_scroll

    raise HTTPException(status_code=413, detail="Limite seguro da exportacao de anuncios excedido.")


def _ml_export_iterar_detalhes(
    client_id: str,
    loja: str,
    cfg: dict,
    item_ids: list[str],
) -> Iterator[tuple[dict, dict]]:
    """Enriquece em lotes limitados para nao acumular itens e futures na memoria."""
    ctx = _ctx()
    cfg_local = cfg
    for inicio in range(0, len(item_ids), _ML_EXPORT_DETAIL_BATCH):
        ids_lote = item_ids[inicio: inicio + _ML_EXPORT_DETAIL_BATCH]
        itens, cfg_local = ctx.ml_buscar_itens_batch(client_id, loja, cfg_local, ids_lote)
        itens_por_id = {
            str(item.get("id") or "").strip(): item
            for item in itens or []
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
        faltantes = [item_id for item_id in ids_lote if item_id not in itens_por_id]
        if faltantes:
            raise HTTPException(
                status_code=502,
                detail=f"Exportacao incompleta: faltaram detalhes de {len(faltantes)} anuncio(s). Nenhum arquivo parcial foi gerado.",
            )

        ativos = [
            itens_por_id[item_id]
            for item_id in ids_lote
            if str(itens_por_id[item_id].get("status") or "").lower() == "active"
        ]
        detalhes_lote: list[Optional[tuple[dict, dict]]] = [None] * len(ativos)
        erros: list[str] = []
        if ativos:
            max_workers = min(6, max(2, len(ativos)))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futuros = {
                    executor.submit(
                        ctx.ml_montar_detalhe_anuncio_listagem,
                        client_id,
                        loja,
                        dict(cfg_local),
                        item,
                        "",
                    ): (idx, item)
                    for idx, item in enumerate(ativos)
                }
                for futuro in as_completed(futuros):
                    idx, item = futuros[futuro]
                    try:
                        detalhe = futuro.result()
                        if not isinstance(detalhe, dict):
                            raise ValueError("detalhe vazio")
                        detalhes_lote[idx] = (detalhe, item)
                    except Exception as exc:
                        erros.append(f"{item.get('id')}: {exc}")

        ausentes = sum(detalhe is None for detalhe in detalhes_lote)
        if erros or ausentes:
            raise HTTPException(
                status_code=502,
                detail=f"Exportacao incompleta: nao foi possivel preparar {max(len(erros), ausentes)} anuncio(s). Nenhum arquivo parcial foi gerado.",
            )
        for detalhe in detalhes_lote:
            if detalhe is not None:
                yield detalhe


def _ml_excel_valor_seguro(valor: Any) -> Any:
    if valor is None or isinstance(valor, (bool, int, float, dt.date, dt.datetime)):
        return valor
    if isinstance(valor, (list, tuple, set)):
        valor = ", ".join(str(item) for item in valor if item not in (None, ""))
    elif isinstance(valor, dict):
        valor = json.dumps(valor, ensure_ascii=False, separators=(",", ":"))
    texto = str(valor)
    if texto and texto[0] in "=+-@\t\r\n":
        return "'" + texto
    return texto


def _ml_excel_data(valor: Any) -> Any:
    if valor in (None, ""):
        return None
    if isinstance(valor, dt.datetime):
        return valor.replace(tzinfo=None) if valor.tzinfo else valor
    if isinstance(valor, dt.date):
        return valor
    try:
        parsed = dt.datetime.fromisoformat(str(valor).strip().replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except (TypeError, ValueError):
        return _ml_excel_valor_seguro(valor)


def _ml_export_nome_arquivo(loja: str) -> str:
    loja_ascii = unicodedata.normalize("NFKD", str(loja or "")).encode("ascii", "ignore").decode("ascii")
    loja_segura = re.sub(r"[^A-Za-z0-9_-]+", "-", loja_ascii).strip("-_")[:60] or "loja"
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"anuncios-ativos-{loja_segura}-{timestamp}.xlsx"


def _ml_export_preparar_planilha(ws, headers: tuple[str, ...]) -> None:
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    header_alignment = Alignment(horizontal="center", vertical="center")
    header_cells = []
    for index, value in enumerate(headers, start=1):
        cell = WriteOnlyCell(ws, value=value)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = header_alignment
        header_cells.append(cell)
        ws.column_dimensions[get_column_letter(index)].width = min(max(len(value) + 2, 12), 48)
    ws.freeze_panes = "A2"
    ws.append(header_cells)


def _ml_export_append_linha(
    ws,
    valores: list[Any],
    *,
    currency_columns: set[int],
    percent_columns: set[int],
    date_columns: set[int],
) -> None:
    cells = []
    for index, valor in enumerate(valores, start=1):
        cell = WriteOnlyCell(ws, value=_ml_excel_valor_seguro(valor))
        if index in currency_columns:
            cell.number_format = 'R$ #,##0.00'
        elif index in percent_columns:
            cell.number_format = '0.00'
        elif index in date_columns:
            cell.number_format = 'yyyy-mm-dd hh:mm:ss'
        cells.append(cell)
    ws.append(cells)


def _ml_export_escrever_workbook(
    workbook: Workbook,
    loja: str,
    detalhes: Iterable[tuple[dict, dict]],
) -> io.BytesIO:
    anuncios_ws = workbook.create_sheet("Anuncios")
    variacoes_ws = workbook.create_sheet("Variacoes")
    _ml_export_preparar_planilha(anuncios_ws, _ML_EXPORT_ANUNCIOS_HEADERS)
    _ml_export_preparar_planilha(variacoes_ws, _ML_EXPORT_VARIACOES_HEADERS)
    total_anuncios = 0
    total_variacoes = 0

    for detalhe, raw in detalhes:
        total_anuncios += 1
        variacoes = detalhe.get("variations") if isinstance(detalhe.get("variations"), list) else []
        skus_variacoes = [str(var.get("sku") or "").strip() for var in variacoes if str(var.get("sku") or "").strip()]
        _ml_export_append_linha(anuncios_ws, [
            _ml_excel_valor_seguro(loja),
            _ml_excel_valor_seguro(detalhe.get("id")),
            _ml_excel_valor_seguro(detalhe.get("sku")),
            _ml_excel_valor_seguro(skus_variacoes),
            _ml_excel_valor_seguro(detalhe.get("title")),
            _ml_excel_valor_seguro(detalhe.get("family_name")),
            _ml_excel_valor_seguro(detalhe.get("listing_type_name") or detalhe.get("listing_type_id")),
            _ml_excel_valor_seguro(detalhe.get("item_condition") or raw.get("condition")),
            _ml_excel_valor_seguro(detalhe.get("price")),
            _ml_excel_valor_seguro(detalhe.get("standard_price")),
            _ml_excel_valor_seguro(detalhe.get("original_price")),
            _ml_excel_valor_seguro(detalhe.get("discount_pct")),
            _ml_excel_valor_seguro(detalhe.get("ad_cost")),
            _ml_excel_valor_seguro(detalhe.get("fixed_fee_amount")),
            _ml_excel_valor_seguro(detalhe.get("listing_fee_amount")),
            _ml_excel_valor_seguro(detalhe.get("sale_fee_pct")),
            _ml_excel_valor_seguro(detalhe.get("shipping_cost")),
            _ml_excel_valor_seguro(detalhe.get("shipping_buyer_cost")),
            _ml_excel_valor_seguro(detalhe.get("shipping_base_cost")),
            _ml_excel_valor_seguro(detalhe.get("shipping_list_cost")),
            bool(detalhe.get("free_shipping")),
            _ml_excel_valor_seguro(detalhe.get("logistic_type")),
            _ml_excel_valor_seguro(detalhe.get("shipping_mode")),
            _ml_excel_valor_seguro(detalhe.get("available_quantity")),
            _ml_excel_valor_seguro(detalhe.get("sold_quantity")),
            _ml_excel_valor_seguro(detalhe.get("status")),
            bool(detalhe.get("has_promotion")),
            _ml_excel_valor_seguro(detalhe.get("promotion_id")),
            _ml_excel_valor_seguro(detalhe.get("promotion_type")),
            bool(detalhe.get("catalog_listing")),
            _ml_excel_valor_seguro(detalhe.get("catalog_product_id")),
            _ml_excel_valor_seguro(detalhe.get("user_product_id")),
            _ml_excel_valor_seguro(raw.get("category_id")),
            _ml_excel_valor_seguro(raw.get("domain_id")),
            _ml_excel_valor_seguro(detalhe.get("channels")),
            _ml_excel_data(raw.get("date_created")),
            _ml_excel_data(raw.get("last_updated")),
            _ml_excel_data(raw.get("start_time")),
            _ml_excel_data(raw.get("stop_time") or raw.get("expiration_time")),
            _ml_excel_valor_seguro(detalhe.get("permalink")),
            _ml_excel_valor_seguro(detalhe.get("thumbnail")),
        ], currency_columns={9, 10, 11, 13, 14, 15, 17, 18, 19, 20}, percent_columns={12, 16}, date_columns={36, 37, 38, 39})

        for variacao in variacoes:
            total_variacoes += 1
            if total_variacoes > _ML_EXPORT_MAX_ITEMS:
                raise HTTPException(
                    status_code=413,
                    detail="A quantidade de variacoes excede o limite de linhas de uma planilha Excel.",
                )
            _ml_export_append_linha(variacoes_ws, [
                _ml_excel_valor_seguro(loja),
                _ml_excel_valor_seguro(detalhe.get("id")),
                _ml_excel_valor_seguro(variacao.get("parent_sku") or detalhe.get("sku")),
                _ml_excel_valor_seguro(variacao.get("id")),
                _ml_excel_valor_seguro(variacao.get("title")),
                _ml_excel_valor_seguro(variacao.get("sku")),
                _ml_excel_valor_seguro(variacao.get("inventory_id")),
                _ml_excel_valor_seguro(variacao.get("price")),
                _ml_excel_valor_seguro(variacao.get("available_quantity")),
                _ml_excel_valor_seguro(variacao.get("sold_quantity")),
                _ml_excel_valor_seguro(variacao.get("picture_id")),
            ], currency_columns={8}, percent_columns=set(), date_columns=set())

    anuncios_ws.auto_filter.ref = f"A1:{get_column_letter(len(_ML_EXPORT_ANUNCIOS_HEADERS))}{total_anuncios + 1}"
    variacoes_ws.auto_filter.ref = f"A1:{get_column_letter(len(_ML_EXPORT_VARIACOES_HEADERS))}{total_variacoes + 1}"
    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    return output


def _ml_export_criar_workbook(loja: str, detalhes: Iterable[tuple[dict, dict]]) -> io.BytesIO:
    workbook = Workbook(write_only=True)
    try:
        return _ml_export_escrever_workbook(workbook, loja, detalhes)
    except Exception:
        # Write-only usa arquivos temporarios; feche e remova-os se a coleta falhar
        # antes do save para nao deixar residuos nem geradores XML pendentes.
        for worksheet in workbook.worksheets:
            try:
                worksheet.close()
            except Exception:
                pass
            writer = getattr(worksheet, "_writer", None)
            if writer is not None:
                try:
                    writer.cleanup()
                except Exception:
                    pass
        raise


def exportar_anuncios_ativos_mercado_livre(client_id: str, loja: str) -> tuple[io.BytesIO, str]:
    """Gera um XLSX completo da loja selecionada sem persistir dados no disco."""
    ctx = _ctx()
    chave_ativa = (str(client_id or "").strip(), str(loja or "").strip().casefold())
    with _ML_EXPORT_ACTIVE_LOCK:
        if chave_ativa in _ML_EXPORT_ACTIVE_KEYS:
            raise HTTPException(status_code=409, detail="Ja existe uma exportacao em andamento para esta loja.")
        _ML_EXPORT_ACTIVE_KEYS.add(chave_ativa)
    try:
        try:
            cfg = ctx.obter_cfg_ml(client_id, loja)
            user_id = str(cfg.get("user_id") or "").strip()
            if not user_id:
                raise HTTPException(status_code=400, detail="ID do usuario Mercado Livre nao encontrado")
            item_ids, cfg = _ml_export_listar_ids_ativos(client_id, loja, cfg, user_id)
            detalhes = _ml_export_iterar_detalhes(client_id, loja, cfg, item_ids)
            return _ml_export_criar_workbook(loja, detalhes), _ml_export_nome_arquivo(loja)
        except HTTPException:
            raise
        except Exception as exc:
            ctx.logger.exception("[ML EXPORT] Falha ao exportar anuncios ativos da loja %s: %s", loja, exc)
            raise HTTPException(status_code=500, detail="Erro ao exportar anuncios ativos do Mercado Livre.")
    finally:
        with _ML_EXPORT_ACTIVE_LOCK:
            _ML_EXPORT_ACTIVE_KEYS.discard(chave_ativa)


def listar_anuncios_mercado_livre(
    client_id: str,
    loja: str,
    offset: int = 0,
    limit: int = 50,
    sku: Optional[str] = None,
) -> dict:
    ctx = _ctx()
    logger = ctx.logger
    try:
        sku_norm = str(sku or "").strip()
        chave_cache = f"anuncios:{client_id}:{loja}:{offset}:{limit}:{sku_norm}"
        cached = _ml_cache_get(chave_cache)
        if cached is not None:
            logger.info(f"[ML CACHE] Retornando anuncios do cache para loja={loja} offset={offset} sku={sku_norm}")
            return cached

        cfg = ctx.obter_cfg_ml(client_id, loja)
        user_id = cfg.get("user_id")
        if not user_id:
            raise HTTPException(status_code=400, detail="ID do usuario Mercado Livre nao encontrado")

        limit = max(1, min(int(limit or 20), 20))
        offset = max(0, int(offset or 0))
        sku_filtro = str(sku or "").strip()
        sku_filtro_lower = sku_filtro.lower()
        url = f"https://api.mercadolibre.com/users/{user_id}/items/search"
        params = {"offset": offset, "limit": limit, "status": "active"}
        if sku_filtro:
            params = {"offset": 0, "limit": 100, "status": "active", "seller_sku": sku_filtro}

        logger.info(f"[ML API] Buscando anuncios para user_id={user_id}, loja={loja}, offset={offset}, limit={limit}, sku={sku_filtro}")

        resp, cfg = ctx.ml_api_request(client_id, loja, cfg, "GET", url, params=params, timeout=15)
        logger.info(f"[ML API] Status da busca de anuncios: {resp.status_code}")
        if resp.status_code != 200:
            logger.error(f"[ML API] Erro ao buscar anuncios: {resp.status_code} - {resp.text}")
            raise HTTPException(status_code=resp.status_code, detail=ctx.ml_parse_error_detail(resp, "Erro ao buscar anuncios"))

        data = resp.json() or {}
        results = data.get("results", []) or []
        total = data.get("paging", {}).get("total", 0)
        logger.info(f"[ML API] Total de anuncios: {total}, IDs retornados: {len(results)}")

        if sku_filtro and not results:
            logger.info(f"[ML API] Nenhum resultado exato para sku={sku_filtro}. Fazendo varredura paginada.")
            results = []
            scan_offset = 0
            scan_limit = 100
            while True:
                scan_params = {"offset": scan_offset, "limit": scan_limit, "status": "active"}
                scan_resp, cfg = ctx.ml_api_request(client_id, loja, cfg, "GET", url, params=scan_params, timeout=15)
                if scan_resp.status_code != 200:
                    logger.warning(f"[ML API] Falha na varredura por SKU: {scan_resp.status_code}")
                    break
                scan_data = scan_resp.json() or {}
                batch = scan_data.get("results", []) or []
                if not batch:
                    break
                results.extend(batch)
                total_scan = scan_data.get("paging", {}).get("total", len(results))
                scan_offset += scan_limit
                if scan_offset >= total_scan:
                    break
            total = len(results)

        item_ids = results if sku_filtro else results
        itens_detalhados, cfg = ctx.ml_buscar_itens_batch(client_id, loja, cfg, item_ids)

        detalhes_ordenados = [None] * len(itens_detalhados)
        if itens_detalhados:
            max_workers = min(6, max(2, len(itens_detalhados)))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {
                    executor.submit(ctx.ml_montar_detalhe_anuncio_listagem, client_id, loja, dict(cfg), item, sku_filtro_lower): idx
                    for idx, item in enumerate(itens_detalhados)
                }
                for future in as_completed(future_map):
                    idx = future_map[future]
                    try:
                        detalhes_ordenados[idx] = future.result()
                    except Exception as e:
                        logger.error(f"[ML API] Excecao ao montar item da listagem: {e}")

        detalhes = [det for det in detalhes_ordenados if det]

        if sku_filtro:
            total = len(detalhes)
            detalhes = detalhes[offset: offset + limit]

        logger.info(f"[ML API] Detalhes carregados: {len(detalhes)} anuncios")
        resultado = jsonable_encoder({"total": total, "results": detalhes, "offset": offset, "limit": limit})
        _ml_cache_set(chave_cache, resultado)
        return resultado
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[ML API] Falha inesperada ao listar anuncios da loja {loja}: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao listar anuncios do Mercado Livre: {str(e)}")


def _ml_parse_data_visita(valor: Any) -> Optional[dt.date]:
    texto = str(valor or "").strip()
    if not texto:
        return None
    try:
        return dt.datetime.fromisoformat(texto.replace("Z", "+00:00")).date()
    except Exception:
        pass
    try:
        return dt.datetime.strptime(texto[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def historico_visitas_anuncio_mercado_livre(
    client_id: str,
    item_id: str,
    loja: str,
    dias: int = 150,
) -> dict:
    ctx = _ctx()
    logger = ctx.logger
    try:
        item_id = str(item_id or "").strip().upper()
        if not re.match(r"^[A-Z]{3}\d+$", item_id):
            raise HTTPException(status_code=400, detail="ID do anuncio invalido.")

        dias = max(1, min(150, int(dias or 150)))
        ending = dt.datetime.now().strftime("%Y-%m-%d")
        chave_cache = f"visitas:{client_id}:{loja}:{item_id}:{dias}:{ending}"
        cached = _ml_cache_get(chave_cache, 900)
        if cached is not None:
            return cached

        cfg = ctx.obter_cfg_ml(client_id, loja)
        resp, cfg = ctx.ml_api_request(
            client_id,
            loja,
            cfg,
            "GET",
            f"https://api.mercadolibre.com/items/{item_id}/visits/time_window",
            params={"last": dias, "unit": "day", "ending": ending},
            timeout=15,
        )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=resp.status_code,
                detail=ctx.ml_parse_error_detail(resp, "Erro ao consultar historico de visitas"),
            )

        data = resp.json() or {}
        pontos_map: dict[str, int] = {}
        for entry in data.get("results") or []:
            if not isinstance(entry, dict):
                continue
            dia = _ml_parse_data_visita(entry.get("date"))
            if not dia:
                continue
            try:
                total_dia = int(float(entry.get("total") or 0))
            except Exception:
                total_dia = 0
            pontos_map[dia.isoformat()] = max(0, total_dia)

        data_inicio = _ml_parse_data_visita(data.get("date_from"))
        data_fim = _ml_parse_data_visita(data.get("date_to"))
        if not data_fim:
            data_fim = dt.datetime.strptime(ending, "%Y-%m-%d").date()
        if not data_inicio:
            data_inicio = data_fim - dt.timedelta(days=dias)

        pontos = []
        dia_atual = data_inicio
        limite_dias = 0
        while dia_atual < data_fim and limite_dias < 160:
            data_iso = dia_atual.isoformat()
            pontos.append({"date": data_iso, "total": int(pontos_map.get(data_iso, 0))})
            dia_atual += dt.timedelta(days=1)
            limite_dias += 1
        if not pontos and pontos_map:
            pontos = [{"date": data_iso, "total": total} for data_iso, total in sorted(pontos_map.items())]

        total_visits = data.get("total_visits")
        try:
            total_visits = int(float(total_visits))
        except Exception:
            total_visits = sum(p["total"] for p in pontos)

        media = round((total_visits / len(pontos)), 2) if pontos else 0
        recorde = None
        if pontos:
            recorde = max(pontos, key=lambda p: p["total"])

        payload = jsonable_encoder({
            "success": True,
            "item_id": data.get("item_id") or item_id,
            "date_from": data.get("date_from"),
            "date_to": data.get("date_to"),
            "total_visits": total_visits,
            "average": media,
            "record": recorde,
            "last": dias,
            "unit": data.get("unit") or "day",
            "results": pontos,
            "raw_count": len(data.get("results") or []),
        })
        _ml_cache_set(chave_cache, payload)
        return payload
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ML API] Falha inesperada ao consultar visitas do anuncio %s: %s", item_id, e)
        raise HTTPException(status_code=500, detail=f"Erro ao consultar historico de visitas: {str(e)}")


def buscar_anuncio_mercado_livre(client_id: str, item_id: str, loja: str) -> dict:
    ctx = _ctx()
    cfg = ctx.obter_cfg_ml(client_id, loja)
    url = f"https://api.mercadolibre.com/items/{item_id}"
    resp, cfg = ctx.ml_api_request(client_id, loja, cfg, "GET", url, timeout=15)
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Anuncio nao encontrado")
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=ctx.ml_parse_error_detail(resp, "Erro ao buscar anuncio"))

    item = resp.json() or {}
    price_info, cfg = ctx.ml_obter_preco_detalhado(client_id, loja, cfg, item_id, fallback_price=item.get("price"))
    shipping_data, cfg = ctx.ml_obter_frete_detalhado(client_id, loja, cfg, item_id, item.get("shipping") or {})
    item_fee = dict(item)
    if price_info.get("price") is not None:
        item_fee["price"] = price_info.get("price")
    fee_data, cfg = ctx.ml_obter_taxas_anuncio(client_id, loja, cfg, item_fee)

    preco_ref = ctx.parse_float_flex(price_info.get("price"))
    if preco_ref is None:
        preco_ref = ctx.parse_float_flex(item.get("price"))
    taxa_fixa_formula = ctx.ml_estimar_taxa_fixa_por_preco(
        preco_ref,
        domain_id=item.get("domain_id") or "",
        category_id=item.get("category_id") or "",
        listing_type_id=item.get("listing_type_id") or "",
    )
    if taxa_fixa_formula is not None:
        fee_data["fixed_fee_amount"] = float(taxa_fixa_formula)
        fee_data["fixed_fee_text"] = "-" if float(taxa_fixa_formula) == 0 else ctx.formatar_moeda_br(taxa_fixa_formula)
        fee_data["fixed_fee_source"] = "formula_padrao"

    item["price_details"] = price_info
    item["shipping_details"] = shipping_data
    item["fee_details"] = fee_data
    desc_url = f"https://api.mercadolibre.com/items/{item_id}/description"
    desc_resp, cfg = ctx.ml_api_request(client_id, loja, cfg, "GET", desc_url, timeout=12)
    if desc_resp.status_code == 200:
        item["description_info"] = desc_resp.json()

    return item
