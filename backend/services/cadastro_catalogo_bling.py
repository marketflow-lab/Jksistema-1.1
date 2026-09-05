"""Read-only, store-scoped Bling catalog collector for Cadastro previews.

This module deliberately does not write Cadastro data.  It turns a complete
Bling product scan into a provider-prefixed review payload that a later,
explicit persistence boundary may apply.
"""

from __future__ import annotations

import json
import logging
import math
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import HTTPException

from backend.services import integracoes
from backend.services.bling import _BlingAdaptiveLimiter, _bling_get_with_adaptive_limit
from backend.services.bling_vendas import (
    _bling_executar_com_refresh,
    _bling_renovar_token_loja,
)
from backend.services.cadastro_catalogo_common import (
    configuracao_catalogo_aplicacao_fingerprint,
    configuracao_catalogo_fingerprint,
)
from backend.services.cadastro_lojas_produtos import _normalizar_sku_chave


logger = logging.getLogger("jk_sistema")

BLING_API_BASE = "https://api.bling.com.br/Api/v3"
BLING_PAGE_SIZE = 100
BLING_STOCK_BATCH_SIZE = 50
BLING_CATEGORY_PAGE_SIZE = 100
BLING_MIN_REQUEST_INTERVAL_SECONDS = 0.35
BLING_MAX_PAGES_PER_TYPE = 1000
BLING_MAX_CATALOG_RECORDS = 25_000
# A conta Bling aceita poucas requisicoes por segundo e o detalhamento e feito
# por produto. O prazo precisa comportar o limite seguro inteiro, inclusive
# saldos e categorias, sem transformar catalogos grandes em parciais.
BLING_CATALOG_TIMEOUT_SECONDS = 5 * 60 * 60


class _CatalogoBlingCancelado(Exception):
    pass


class _CatalogoBlingPrazoExcedido(Exception):
    pass


def _detail(code: str, message: str, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"code": code, "message": message}
    result.update(extra)
    return result


def _cancelamento_solicitado(cancel_event: Any) -> bool:
    if cancel_event is None:
        return False
    is_set = getattr(cancel_event, "is_set", None)
    if callable(is_set):
        return bool(is_set())
    if callable(cancel_event):
        return bool(cancel_event())
    return False


def _verificar_limites(cancel_event: Any, deadline: float) -> None:
    if _cancelamento_solicitado(cancel_event):
        raise _CatalogoBlingCancelado()
    if time.monotonic() >= deadline:
        raise _CatalogoBlingPrazoExcedido()


def _emitir_progresso(
    progress_callback: Callable[..., Any] | None,
    etapa: str,
    atual: int,
    total: int,
    percentual: int,
    mensagem: str,
) -> None:
    if not callable(progress_callback):
        return
    try:
        progress_callback(
            str(etapa),
            int(atual or 0),
            int(total or 0),
            min(100, max(0, int(percentual or 0))),
            str(mensagem or ""),
        )
    except Exception:
        # Progress is advisory and must never turn a complete provider read
        # into a failed catalog collection.
        logger.debug("[Cadastro Bling] Falha ao emitir progresso.", exc_info=True)


def _adicionar_aviso(destino: list[str], aviso: str) -> None:
    texto = str(aviso or "").strip()
    if texto and texto not in destino:
        destino.append(texto)


def _marcar_incompleto(state: dict[str, Any], aviso: str) -> None:
    state["coverage_complete"] = False
    _adicionar_aviso(state["warnings"], aviso)


def _marcar_cobertura_sku_incompleta(state: dict[str, Any], aviso: str) -> None:
    """Marca falhas que impedem provar que todos os SKUs foram enumerados."""
    state["sku_coverage_complete"] = False
    _marcar_incompleto(state, aviso)


def _valor_preenchido(valor: Any) -> bool:
    if valor is None:
        return False
    if isinstance(valor, str):
        return bool(valor.strip())
    if isinstance(valor, (list, tuple, dict, set)):
        return bool(valor)
    if isinstance(valor, float):
        return math.isfinite(valor)
    return True


def _valor_scalar(valor: Any) -> Any:
    if isinstance(valor, str):
        return valor.strip()
    return valor


def _json_canonico(valor: Any) -> str:
    if not _valor_preenchido(valor):
        return ""
    try:
        return json.dumps(
            valor,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return ""


def _definir_campo(
    fields: dict[str, Any],
    nome: str,
    valor: Any,
    *,
    serializar: bool = False,
) -> None:
    if serializar:
        valor = _json_canonico(valor)
    else:
        valor = _valor_scalar(valor)
    if _valor_preenchido(valor):
        fields[nome] = valor


def _primeiro_preenchido(*valores: Any) -> Any:
    for valor in valores:
        if _valor_preenchido(valor):
            return valor
    return None


def _codigo_aninhado(valor: Any) -> Any:
    if not isinstance(valor, dict):
        return valor
    return _primeiro_preenchido(
        valor.get("codigo"),
        valor.get("id"),
        valor.get("valor"),
        valor.get("descricao"),
        valor.get("nome"),
    )


def _id_texto(valor: Any) -> str:
    if not _valor_preenchido(valor):
        return ""
    return str(valor).strip()


def _saldo_total_valido(valor: Any) -> bool:
    if isinstance(valor, bool) or not _valor_preenchido(valor):
        return False
    try:
        return math.isfinite(float(valor))
    except (TypeError, ValueError):
        return False


def _mapear_campos_produto(
    produto: dict[str, Any],
    *,
    consultado_em_utc: str = "",
) -> dict[str, Any]:
    """Map known Bling fields without populating generic Cadastro columns."""

    fields: dict[str, Any] = {}
    produto_id = _id_texto(produto.get("id"))
    nome = _primeiro_preenchido(produto.get("nome"), produto.get("produto"))
    estoque = produto.get("estoque") if isinstance(produto.get("estoque"), dict) else {}
    tributacao = (
        produto.get("tributacao")
        if isinstance(produto.get("tributacao"), dict)
        else {}
    )
    categoria = (
        produto.get("categoria")
        if isinstance(produto.get("categoria"), dict)
        else produto.get("categoria")
    )
    dimensoes = (
        produto.get("dimensoes")
        if isinstance(produto.get("dimensoes"), dict)
        else {}
    )
    fornecedor = (
        produto.get("fornecedor")
        if isinstance(produto.get("fornecedor"), dict)
        else {}
    )
    estrutura = produto.get("estrutura")
    componentes = produto.get("componentes")
    if not _valor_preenchido(componentes) and isinstance(estrutura, dict):
        componentes = estrutura.get("componentes")
    midia = produto.get("midia") if isinstance(produto.get("midia"), dict) else {}

    _definir_campo(fields, "id_bling", produto_id)
    _definir_campo(fields, "id_produto_pai_bling", produto.get("idProdutoPai"))
    _definir_campo(fields, "produto_bling", nome)
    _definir_campo(fields, "nome_bling", nome)
    _definir_campo(fields, "situacao_bling", produto.get("situacao"))
    _definir_campo(fields, "tipo_bling", produto.get("tipo"))
    _definir_campo(fields, "formato_bling", produto.get("formato"))
    _definir_campo(fields, "data_validade_bling", produto.get("dataValidade"))
    _definir_campo(fields, "tipo_producao_bling", produto.get("tipoProducao"))
    _definir_campo(fields, "condicao_bling", produto.get("condicao"))
    _definir_campo(fields, "frete_gratis_bling", produto.get("freteGratis"))
    _definir_campo(fields, "action_estoque_bling", produto.get("actionEstoque"))
    _definir_campo(fields, "linha_produto_bling", produto.get("linhaProduto"))
    _definir_campo(fields, "artigo_perigoso_bling", produto.get("artigoPerigoso"))
    _definir_campo(fields, "duns_bling", produto.get("duns"))
    _definir_campo(fields, "marca_bling", _codigo_aninhado(produto.get("marca")))
    _definir_campo(fields, "gtin_bling", produto.get("gtin"))
    _definir_campo(fields, "gtin_embalagem_bling", produto.get("gtinEmbalagem"))
    _definir_campo(fields, "preco_bling", produto.get("preco"))
    _definir_campo(
        fields,
        "custo_bling",
        _primeiro_preenchido(
            produto.get("precoCusto"),
            fornecedor.get("precoCusto"),
            fornecedor.get("precoCompra"),
        ),
    )
    _definir_campo(fields, "unidade_bling", produto.get("unidade"))
    _definir_campo(fields, "peso_liquido_bling", produto.get("pesoLiquido"))
    _definir_campo(fields, "peso_bruto_bling", produto.get("pesoBruto"))
    _definir_campo(fields, "volumes_bling", produto.get("volumes"))
    _definir_campo(fields, "itens_por_caixa_bling", produto.get("itensPorCaixa"))
    if fornecedor:
        _definir_campo(
            fields,
            "fornecedor_bling_json",
            fornecedor,
            serializar=True,
        )

    _definir_campo(
        fields,
        "estoque_virtual_bling",
        _primeiro_preenchido(
            estoque.get("saldoVirtualTotal"),
            produto.get("saldoVirtualTotal"),
        ),
    )
    _definir_campo(fields, "estoque_minimo_bling", estoque.get("minimo"))
    _definir_campo(fields, "estoque_maximo_bling", estoque.get("maximo"))
    _definir_campo(fields, "crossdocking_bling", estoque.get("crossdocking"))
    _definir_campo(fields, "localizacao_bling", estoque.get("localizacao"))

    ncm = _primeiro_preenchido(tributacao.get("ncm"), produto.get("ncm"))
    cest = _primeiro_preenchido(tributacao.get("cest"), produto.get("cest"))
    _definir_campo(fields, "ncm_bling", _codigo_aninhado(ncm))
    _definir_campo(fields, "cest_bling", _codigo_aninhado(cest))
    if tributacao:
        _definir_campo(fields, "tributacao_bling", tributacao, serializar=True)

    if isinstance(categoria, dict):
        _definir_campo(fields, "categoria_id_bling", categoria.get("id"))
        _definir_campo(
            fields,
            "categoria_bling",
            _primeiro_preenchido(categoria.get("descricao"), categoria.get("nome")),
        )
    else:
        _definir_campo(fields, "categoria_id_bling", categoria)

    _definir_campo(fields, "largura_bling", dimensoes.get("largura"))
    _definir_campo(fields, "altura_bling", dimensoes.get("altura"))
    _definir_campo(fields, "profundidade_bling", dimensoes.get("profundidade"))
    _definir_campo(
        fields,
        "unidade_medida_dimensoes_bling",
        dimensoes.get("unidadeMedida"),
    )
    if dimensoes:
        _definir_campo(fields, "dimensoes_bling", dimensoes, serializar=True)

    _definir_campo(fields, "descricao_bling", produto.get("descricao"))
    _definir_campo(fields, "descricao_curta_bling", produto.get("descricaoCurta"))
    _definir_campo(
        fields,
        "descricao_complementar_bling",
        produto.get("descricaoComplementar"),
    )
    _definir_campo(
        fields,
        "descricao_embalagem_discreta_bling",
        produto.get("descricaoEmbalagemDiscreta"),
    )
    _definir_campo(fields, "observacoes_bling", produto.get("observacoes"))
    _definir_campo(fields, "link_externo_bling", produto.get("linkExterno"))
    _definir_campo(fields, "imagem_url_bling", produto.get("imagemURL"))

    imagens = midia.get("imagens")
    if _valor_preenchido(imagens):
        _definir_campo(fields, "imagens_bling", imagens, serializar=True)
    if _valor_preenchido(midia.get("video")):
        valor_video = midia.get("video")
        _definir_campo(
            fields,
            "video_bling",
            valor_video,
            serializar=isinstance(valor_video, (dict, list, tuple)),
        )

    for origem, destino in (
        ("variacoes", "variacoes_bling"),
        ("estrutura", "estrutura_bling"),
        ("camposCustomizados", "campos_customizados_bling"),
    ):
        if _valor_preenchido(produto.get(origem)):
            _definir_campo(fields, destino, produto.get(origem), serializar=True)
    if _valor_preenchido(componentes):
        _definir_campo(
            fields,
            "componentes_bling_json",
            componentes,
            serializar=True,
        )
    _definir_campo(fields, "consultado_em_utc_bling", consultado_em_utc)

    return fields


def _estado_inicial(store_id: str, store_name: str) -> dict[str, Any]:
    return {
        "store_id": store_id,
        "store_name": store_name,
        "coverage_complete": True,
        "sku_coverage_complete": True,
        "items": [],
        "skipped": [],
        "warnings": [],
        "stats": {
            "pages_by_type": {"T": 0},
            "listed_by_type": {"T": 0},
            "records_scanned": 0,
            "invalid_records": 0,
            "duplicate_product_ids": 0,
            "details_requested": 0,
            "details_complete": 0,
            "details_failed": 0,
            "balances_requested": 0,
            "balances_returned": 0,
            "balance_batches": 0,
            "balances_failed": 0,
            "balance_invalid_rows": 0,
            "categories_requested": 0,
            "categories_complete": 0,
            "categories_failed": 0,
            "category_pages": 0,
            "category_invalid_rows": 0,
            "cancelled": False,
            "deadline_exceeded": False,
        },
    }


def _resultado_publico(state: dict[str, Any]) -> dict[str, Any]:
    items = state["items"]
    grupos: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        sku_normalizado = str(item.get("sku_normalizado") or "").strip()
        if sku_normalizado:
            grupos.setdefault(sku_normalizado, []).append(item)

    duplicate_skus = 0
    duplicate_items = 0
    for sku_normalizado, grupo in grupos.items():
        if len(grupo) < 2:
            continue
        duplicate_skus += 1
        duplicate_items += len(grupo)
        for item in grupo:
            _adicionar_aviso(
                item["warnings"],
                f"sku_duplicado_bling:{sku_normalizado}",
            )

    stats = state["stats"]
    stats["items"] = len(items)
    stats["skipped"] = len(state["skipped"])
    stats["missing_sku"] = sum(
        1 for item in state["skipped"] if item.get("reason") == "missing_sku"
    )
    stats["duplicate_skus"] = duplicate_skus
    stats["duplicate_items"] = duplicate_items

    return {
        "source": "bling",
        "store_id": state["store_id"],
        "store_name": state["store_name"],
        "coverage_complete": bool(state["coverage_complete"]),
        "sku_coverage_complete": bool(state["sku_coverage_complete"]),
        "items": items,
        "skipped": state["skipped"],
        "stats": stats,
        "warnings": state["warnings"],
    }


def _get_json(
    access_token: str,
    path: str,
    *,
    params: Any,
    limiter: _BlingAdaptiveLimiter,
    cancel_callback: Callable[[], object],
    deadline: float,
) -> tuple[Any, int]:
    _verificar_limites(cancel_callback, deadline)
    remaining = deadline - time.monotonic()
    response = _bling_get_with_adaptive_limit(
        f"{BLING_API_BASE}{path}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "enable-jwt": "1",
        },
        params=params,
        timeout=(5, 20),
        limiter=limiter,
        max_attempts=3,
        cancel_callback=cancel_callback,
        total_timeout=min(60.0, max(0.1, remaining)),
    )
    if response is None:
        return None, 503
    status = int(getattr(response, "status_code", 503) or 503)
    if status != 200:
        return None, status
    try:
        payload = response.json()
    except Exception:
        return None, 502
    if not isinstance(payload, dict) or "data" not in payload:
        return None, 502
    return payload.get("data"), 200


def _assinatura_pagina(rows: list[dict[str, Any]]) -> str:
    return _json_canonico(
        [
            [_id_texto(row.get("id")), str(row.get("codigo") or "").strip()]
            for row in rows
        ]
    )


def _coletar_com_token(
    access_token: str,
    *,
    store_id: str,
    store_name: str,
    progress_callback: Callable[..., Any] | None,
    cancel_event: Any,
    deadline: float,
    holder: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    state = _estado_inicial(store_id, store_name)
    holder["state"] = state
    limiter = _BlingAdaptiveLimiter(
        min_interval=BLING_MIN_REQUEST_INTERVAL_SECONDS,
        start_interval=BLING_MIN_REQUEST_INTERVAL_SECONDS,
    )
    consultado_em_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    seen_product_ids: dict[str, str] = {}

    def check_cancel() -> None:
        _verificar_limites(cancel_event, deadline)

    try:
        catalog_limit_reached = False
        # The Bling list endpoint defaults to recently included products.  The
        # explicit all/all pair is required to prove full SKU coverage across
        # products, services, structures/compositions and variations.
        for type_index, tipo in enumerate(("T",)):
            previous_signature = ""
            type_complete = False
            for pagina in range(1, BLING_MAX_PAGES_PER_TYPE + 1):
                check_cancel()
                _emitir_progresso(
                    progress_callback,
                    "listing",
                    pagina,
                    0,
                    2 + type_index * 8,
                    f"Lendo produtos Bling do tipo {tipo}, pagina {pagina}.",
                )
                data, status = _get_json(
                    access_token,
                    "/produtos",
                    params={
                        "pagina": pagina,
                        "limite": BLING_PAGE_SIZE,
                        "tipo": tipo,
                        "criterio": 5,
                    },
                    limiter=limiter,
                    cancel_callback=check_cancel,
                    deadline=deadline,
                )
                state["stats"]["pages_by_type"][tipo] = pagina
                if status == 401:
                    return _resultado_publico(state), 401
                if status != 200:
                    _marcar_cobertura_sku_incompleta(
                        state,
                        f"catalog_page_http_{status}:type={tipo}:page={pagina}",
                    )
                    break
                if not isinstance(data, list):
                    _marcar_cobertura_sku_incompleta(
                        state,
                        f"catalog_page_invalid:type={tipo}:page={pagina}",
                    )
                    break

                valid_rows = [row for row in data if isinstance(row, dict)]
                invalid_count = len(data) - len(valid_rows)
                if invalid_count:
                    state["stats"]["invalid_records"] += invalid_count
                    _marcar_cobertura_sku_incompleta(
                        state,
                        f"catalog_invalid_records:type={tipo}:page={pagina}:count={invalid_count}",
                    )

                signature = _assinatura_pagina(valid_rows)
                if data and previous_signature and signature == previous_signature:
                    _marcar_cobertura_sku_incompleta(
                        state,
                        f"catalog_repeated_page:type={tipo}:page={pagina}",
                    )
                    break

                for row in valid_rows:
                    if state["stats"]["records_scanned"] >= BLING_MAX_CATALOG_RECORDS:
                        catalog_limit_reached = True
                        _marcar_cobertura_sku_incompleta(
                            state,
                            f"catalog_item_limit:max={BLING_MAX_CATALOG_RECORDS}",
                        )
                        break
                    state["stats"]["records_scanned"] += 1
                    state["stats"]["listed_by_type"][tipo] += 1
                    sku = str(row.get("codigo") or "").strip()
                    external_id = _id_texto(row.get("id"))
                    sku_normalizado = _normalizar_sku_chave(sku) if sku else ""
                    if external_id:
                        if external_id in seen_product_ids:
                            first_sku_normalizado = seen_product_ids[external_id]
                            duplicate_warnings = ["id_produto_bling_repetido"]
                            mismatch = first_sku_normalizado != sku_normalizado
                            if mismatch:
                                duplicate_warnings.append(
                                    "sku_bling_divergente_para_id_repetido"
                                )
                            state["stats"]["duplicate_product_ids"] += 1
                            state["skipped"].append(
                                {
                                    "reason": "duplicate_product_id",
                                    "sku": sku,
                                    "sku_normalizado": sku_normalizado,
                                    "external_ids": {"id_bling": external_id},
                                    "fields": _mapear_campos_produto(
                                        row,
                                        consultado_em_utc=consultado_em_utc,
                                    ),
                                    "warnings": duplicate_warnings,
                                }
                            )
                            warning = f"catalog_duplicate_product_id:id={external_id}"
                            if mismatch:
                                warning += ":sku_mismatch"
                            _marcar_cobertura_sku_incompleta(state, warning)
                            continue
                        seen_product_ids[external_id] = sku_normalizado
                    if not sku:
                        state["skipped"].append(
                            {
                                "reason": "missing_sku",
                                "external_ids": (
                                    {"id_bling": external_id} if external_id else {}
                                ),
                                "fields": _mapear_campos_produto(
                                    row,
                                    consultado_em_utc=consultado_em_utc,
                                ),
                                "warnings": ["sku_bling_ausente"],
                            }
                        )
                        continue
                    if not sku_normalizado:
                        state["skipped"].append(
                            {
                                "reason": "missing_sku",
                                "external_ids": (
                                    {"id_bling": external_id} if external_id else {}
                                ),
                                "fields": _mapear_campos_produto(
                                    row,
                                    consultado_em_utc=consultado_em_utc,
                                ),
                                "warnings": ["sku_bling_invalido"],
                            }
                        )
                        continue
                    item = {
                        "sku": sku,
                        "sku_normalizado": sku_normalizado,
                        "external_ids": (
                            {"id_bling": external_id} if external_id else {}
                        ),
                        "fields": _mapear_campos_produto(
                            row,
                            consultado_em_utc=consultado_em_utc,
                        ),
                        "warnings": [],
                    }
                    if not external_id:
                        _adicionar_aviso(item["warnings"], "id_produto_bling_ausente")
                        _marcar_incompleto(state, "catalog_product_id_missing")
                    state["items"].append(item)

                if catalog_limit_reached:
                    break

                if len(data) < BLING_PAGE_SIZE:
                    type_complete = True
                    break
                previous_signature = signature

            if not type_complete and state["stats"]["pages_by_type"][tipo] >= BLING_MAX_PAGES_PER_TYPE:
                _marcar_cobertura_sku_incompleta(
                    state,
                    f"catalog_page_limit:type={tipo}:max={BLING_MAX_PAGES_PER_TYPE}",
                )
            if catalog_limit_reached:
                break

        if catalog_limit_reached:
            return _resultado_publico(state), 200

        total_items = len(state["items"])
        for index, item in enumerate(state["items"], start=1):
            check_cancel()
            product_id = str(item["external_ids"].get("id_bling") or "").strip()
            if not product_id:
                continue
            state["stats"]["details_requested"] += 1
            _emitir_progresso(
                progress_callback,
                "details",
                index,
                total_items,
                20 + int((index / max(1, total_items)) * 65),
                f"Lendo detalhes Bling {index} de {total_items}.",
            )
            detail, status = _get_json(
                access_token,
                f"/produtos/{product_id}",
                params=None,
                limiter=limiter,
                cancel_callback=check_cancel,
                deadline=deadline,
            )
            if status == 401:
                return _resultado_publico(state), 401
            if status != 200 or not isinstance(detail, dict):
                state["stats"]["details_failed"] += 1
                _adicionar_aviso(item["warnings"], f"detalhe_bling_http_{status}")
                _marcar_incompleto(
                    state,
                    f"product_detail_incomplete:id={product_id}:status={status}",
                )
                continue
            if not detail:
                state["stats"]["details_failed"] += 1
                _adicionar_aviso(item["warnings"], "detalhe_bling_vazio")
                _marcar_incompleto(
                    state,
                    f"product_detail_empty:id={product_id}",
                )
                continue
            detail_id = _id_texto(detail.get("id"))
            if detail_id != product_id:
                state["stats"]["details_failed"] += 1
                _adicionar_aviso(item["warnings"], "detalhe_bling_id_divergente")
                _marcar_incompleto(
                    state,
                    f"product_detail_id_mismatch:requested={product_id}:received={detail_id or 'missing'}",
                )
                continue
            detail_sku_raw = detail.get("codigo")
            detail_sku = (
                str(detail_sku_raw).strip()
                if _valor_preenchido(detail_sku_raw)
                else ""
            )
            if detail_sku and _normalizar_sku_chave(detail_sku) != item["sku_normalizado"]:
                state["stats"]["details_failed"] += 1
                _adicionar_aviso(item["warnings"], "detalhe_bling_sku_divergente")
                _marcar_incompleto(
                    state,
                    f"product_detail_sku_mismatch:id={product_id}",
                )
                continue
            item["fields"].update(
                _mapear_campos_produto(
                    detail,
                    consultado_em_utc=consultado_em_utc,
                )
            )
            state["stats"]["details_complete"] += 1

        product_items_by_id: dict[str, list[dict[str, Any]]] = {}
        for item in state["items"]:
            product_id = _id_texto(item["external_ids"].get("id_bling"))
            if product_id:
                product_items_by_id.setdefault(product_id, []).append(item)

        product_ids = list(product_items_by_id)
        state["stats"]["balances_requested"] = len(product_ids)
        returned_balance_ids: set[str] = set()
        failed_balance_ids: set[str] = set()
        for start in range(0, len(product_ids), BLING_STOCK_BATCH_SIZE):
            check_cancel()
            batch = product_ids[start : start + BLING_STOCK_BATCH_SIZE]
            requested_ids = set(batch)
            batch_number = (start // BLING_STOCK_BATCH_SIZE) + 1
            total_batches = math.ceil(len(product_ids) / BLING_STOCK_BATCH_SIZE)
            state["stats"]["balance_batches"] = batch_number
            _emitir_progresso(
                progress_callback,
                "balances",
                batch_number,
                total_batches,
                86 + int((batch_number / max(1, total_batches)) * 6),
                f"Lendo saldos Bling, lote {batch_number} de {total_batches}.",
            )
            balances, status = _get_json(
                access_token,
                "/estoques/saldos",
                params=[("idsProdutos[]", product_id) for product_id in batch],
                limiter=limiter,
                cancel_callback=check_cancel,
                deadline=deadline,
            )
            if status == 401:
                return _resultado_publico(state), 401
            if status != 200 or not isinstance(balances, list):
                failed_balance_ids.update(requested_ids)
                for product_id in requested_ids:
                    for item in product_items_by_id[product_id]:
                        _adicionar_aviso(
                            item["warnings"],
                            f"saldo_bling_incompleto:status={status}",
                        )
                _marcar_incompleto(
                    state,
                    f"stock_balance_incomplete:batch={batch_number}:status={status}",
                )
                continue

            batch_returned: set[str] = set()
            for balance in balances:
                if not isinstance(balance, dict):
                    state["stats"]["balance_invalid_rows"] += 1
                    _marcar_incompleto(
                        state,
                        f"stock_balance_invalid_row:batch={batch_number}",
                    )
                    continue
                product = (
                    balance.get("produto")
                    if isinstance(balance.get("produto"), dict)
                    else {}
                )
                product_id = _id_texto(
                    _primeiro_preenchido(product.get("id"), balance.get("idProduto"))
                )
                if (
                    not product_id
                    or product_id not in requested_ids
                    or product_id in batch_returned
                    or product_id in returned_balance_ids
                ):
                    state["stats"]["balance_invalid_rows"] += 1
                    _marcar_incompleto(
                        state,
                        f"stock_balance_unexpected_product:batch={batch_number}",
                    )
                    continue
                if not all(
                    _saldo_total_valido(balance.get(field))
                    for field in ("saldoFisicoTotal", "saldoVirtualTotal")
                ):
                    state["stats"]["balance_invalid_rows"] += 1
                    for item in product_items_by_id[product_id]:
                        _adicionar_aviso(item["warnings"], "saldo_bling_totais_invalidos")
                    _marcar_incompleto(
                        state,
                        f"stock_balance_invalid_totals:id={product_id}",
                    )
                    continue
                batch_returned.add(product_id)
                returned_balance_ids.add(product_id)
                for item in product_items_by_id[product_id]:
                    fields = item["fields"]
                    _definir_campo(
                        fields,
                        "estoque_fisico_bling",
                        balance.get("saldoFisicoTotal"),
                    )
                    _definir_campo(
                        fields,
                        "estoque_virtual_bling",
                        balance.get("saldoVirtualTotal"),
                    )
                    _definir_campo(
                        fields,
                        "estoques_bling_json",
                        balance,
                        serializar=True,
                    )

            missing_ids = requested_ids - batch_returned
            if missing_ids:
                failed_balance_ids.update(missing_ids)
                for product_id in missing_ids:
                    for item in product_items_by_id[product_id]:
                        _adicionar_aviso(item["warnings"], "saldo_bling_ausente")
                _marcar_incompleto(
                    state,
                    f"stock_balance_missing:batch={batch_number}:count={len(missing_ids)}",
                )

        state["stats"]["balances_returned"] = len(returned_balance_ids)
        state["stats"]["balances_failed"] = len(failed_balance_ids)

        categorias: dict[str, list[dict[str, Any]]] = {}
        for item in state["items"]:
            fields = item["fields"]
            category_id = _id_texto(fields.get("categoria_id_bling"))
            if category_id and not _valor_preenchido(fields.get("categoria_bling")):
                categorias.setdefault(category_id, []).append(item)

        state["stats"]["categories_requested"] = len(categorias)
        category_names: dict[str, str] = {}
        previous_category_signature = ""
        for page in range(1, BLING_MAX_PAGES_PER_TYPE + 1):
            if not categorias or len(category_names) == len(categorias):
                break
            check_cancel()
            state["stats"]["category_pages"] = page
            _emitir_progresso(
                progress_callback,
                "categories",
                len(category_names),
                len(categorias),
                93 + int((len(category_names) / max(1, len(categorias))) * 6),
                f"Lendo mapa de categorias Bling, pagina {page}.",
            )
            category_rows, status = _get_json(
                access_token,
                "/categorias/produtos",
                params={"pagina": page, "limite": BLING_CATEGORY_PAGE_SIZE},
                limiter=limiter,
                cancel_callback=check_cancel,
                deadline=deadline,
            )
            if status == 401:
                return _resultado_publico(state), 401
            if status != 200 or not isinstance(category_rows, list):
                _marcar_incompleto(
                    state,
                    f"category_catalog_incomplete:page={page}:status={status}",
                )
                break
            valid_rows = [row for row in category_rows if isinstance(row, dict)]
            invalid_count = len(category_rows) - len(valid_rows)
            if invalid_count:
                state["stats"]["category_invalid_rows"] += invalid_count
                _marcar_incompleto(
                    state,
                    f"category_catalog_invalid_rows:page={page}:count={invalid_count}",
                )
            signature = _json_canonico(valid_rows)
            if category_rows and previous_category_signature == signature:
                _marcar_incompleto(state, f"category_catalog_repeated_page:page={page}")
                break
            for category in valid_rows:
                category_id = _id_texto(category.get("id"))
                description = _primeiro_preenchido(
                    category.get("descricao"), category.get("nome")
                )
                if category_id in categorias and _valor_preenchido(description):
                    category_names[category_id] = str(description).strip()
            if len(category_rows) < BLING_CATEGORY_PAGE_SIZE:
                break
            previous_category_signature = signature
        else:
            _marcar_incompleto(
                state,
                f"category_catalog_page_limit:max={BLING_MAX_PAGES_PER_TYPE}",
            )

        for category_id, category_items in categorias.items():
            description = category_names.get(category_id)
            if description:
                for item in category_items:
                    _definir_campo(item["fields"], "categoria_bling", description)
                state["stats"]["categories_complete"] += 1
                continue
            state["stats"]["categories_failed"] += 1
            for item in category_items:
                _adicionar_aviso(item["warnings"], f"categoria_bling_ausente:{category_id}")
            _marcar_incompleto(state, f"category_detail_missing:id={category_id}")

        _emitir_progresso(
            progress_callback,
            "done",
            len(state["items"]),
            len(state["items"]),
            100,
            "Catalogo Bling coletado para revisao.",
        )
        return _resultado_publico(state), 200
    except _CatalogoBlingCancelado:
        state["stats"]["cancelled"] = True
        _marcar_cobertura_sku_incompleta(state, "catalog_collection_cancelled")
        return _resultado_publico(state), 499
    except _CatalogoBlingPrazoExcedido:
        state["stats"]["deadline_exceeded"] = True
        _marcar_cobertura_sku_incompleta(
            state,
            "catalog_collection_deadline_exceeded",
        )
        return _resultado_publico(state), 504


def _resolver_loja_bling(client_id: str, store_id: str) -> tuple[str, dict[str, Any]]:
    tenant = str(client_id or "").strip()
    store_id_exato = str(store_id or "").strip()
    if not tenant:
        raise HTTPException(
            status_code=400,
            detail=_detail("client_id_required", "client_id e obrigatorio."),
        )
    if not store_id_exato:
        raise HTTPException(
            status_code=400,
            detail=_detail("store_id_required", "store_id e obrigatorio."),
        )

    matches = [
        loja
        for loja in (integracoes.carregar_lojas(tenant) or [])
        if isinstance(loja, dict)
        and str(loja.get("store_id") or "").strip() == store_id_exato
    ]
    if not matches:
        raise HTTPException(
            status_code=404,
            detail=_detail(
                "store_not_found",
                "Loja nao encontrada para este cliente.",
                store_id=store_id_exato,
            ),
        )
    if len(matches) != 1:
        raise HTTPException(
            status_code=409,
            detail=_detail(
                "store_config_ambiguous",
                "A identidade da loja esta duplicada na configuracao.",
                store_id=store_id_exato,
            ),
        )

    loja = matches[0]
    nome = str(loja.get("nome") or "").strip()
    cfg = dict(((loja.get("integracoes") or {}).get("bling") or {}))
    if not cfg or not (
        str(cfg.get("access_token") or "").strip()
        or str(cfg.get("refresh_token") or "").strip()
    ):
        raise HTTPException(
            status_code=409,
            detail=_detail(
                "bling_not_connected",
                "Conecte a Bling nesta loja antes de coletar o catalogo.",
                store_id=store_id_exato,
            ),
        )
    return nome, cfg


def _mesma_identidade_estavel_bling(
    store_id: str,
    store_name: str,
    anterior: dict[str, Any],
    atual: dict[str, Any],
) -> bool:
    """Accept token rotation only inside one explicit Bling connection epoch."""

    connection_anterior = str(anterior.get("oauth_connection_id") or "").strip()
    connection_atual = str(atual.get("oauth_connection_id") or "").strip()
    if not connection_anterior or not connection_atual or not secrets.compare_digest(
        connection_anterior,
        connection_atual,
    ):
        return False
    return secrets.compare_digest(
        configuracao_catalogo_aplicacao_fingerprint(
            "bling",
            store_id,
            store_name,
            anterior,
        ),
        configuracao_catalogo_aplicacao_fingerprint(
            "bling",
            store_id,
            store_name,
            atual,
        ),
    )


def _preparar_credencial_coleta(
    client_id: str,
    store_id: str,
    store_name: str,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """Start a potentially long scan with a fresh, CAS-owned OAuth token."""
    if cfg.get("central"):
        # The server renews its own token before each requested provider call.
        return dict(cfg)

    if not str(cfg.get("refresh_token") or "").strip():
        raise HTTPException(
            status_code=409,
            detail=_detail(
                "bling_refresh_required",
                "Reconecte a Bling nesta loja antes de coletar o catalogo completo.",
                store_id=store_id,
            ),
        )
    renewed, disposition = _bling_renovar_token_loja(
        client_id,
        store_name,
        cfg,
        store_id=store_id,
        return_disposition=True,
    )
    if disposition == "reused_concurrent" and _mesma_identidade_estavel_bling(
        store_id,
        store_name,
        cfg,
        dict(renewed or {}),
    ):
        disposition = "reused_concurrent_same_connection"
    if disposition not in {
        "committed_by_caller",
        "reused_concurrent_same_connection",
    }:
        raise HTTPException(
            status_code=409,
            detail=_detail(
                "bling_refresh_not_owned",
                "A credencial Bling mudou durante o preparo; gere uma nova previa.",
                store_id=store_id,
                disposition=disposition,
            ),
        )
    renewed = dict(renewed or {})
    if not str(renewed.get("access_token") or "").strip():
        raise HTTPException(
            status_code=502,
            detail=_detail(
                "bling_refresh_invalid",
                "A Bling nao retornou uma credencial valida para a coleta.",
                store_id=store_id,
            ),
        )
    return renewed


def coletar_catalogo_bling(
    client_id: str,
    store_id: str,
    *,
    progress_callback: Callable[..., Any] | None = None,
    cancel_event: Any = None,
    expected_config_fingerprint: str = "",
    expected_apply_config_fingerprint: str = "",
) -> dict[str, Any]:
    """Collect a complete Bling catalog for one exact JK store.

    The function performs no Cadastro writes and never returns OAuth secrets.
    ``progress_callback`` follows the existing Bling five-argument convention:
    ``(stage, current, total, percent, message)``.
    """

    tenant = str(client_id or "").strip()
    store_id_exato = str(store_id or "").strip()
    store_name, cfg = _resolver_loja_bling(tenant, store_id_exato)
    started_config_fingerprint = configuracao_catalogo_fingerprint(
        "bling",
        store_id_exato,
        store_name,
        cfg,
    )
    started_apply_config_fingerprint = configuracao_catalogo_aplicacao_fingerprint(
        "bling",
        store_id_exato,
        store_name,
        cfg,
    )
    expected = str(expected_config_fingerprint or "").strip().lower()
    expected_apply = str(expected_apply_config_fingerprint or "").strip().lower()
    exact_match = not expected or secrets.compare_digest(
        started_config_fingerprint,
        expected,
    )
    stable_match = bool(
        expected_apply
        and str(cfg.get("oauth_connection_id") or "").strip()
        and secrets.compare_digest(
            started_apply_config_fingerprint,
            expected_apply,
        )
    )
    if not exact_match and not stable_match:
        raise HTTPException(
            status_code=409,
            detail=_detail(
                "store_config_changed",
                "A configuracao Bling mudou antes do inicio da coleta.",
                store_id=store_id_exato,
            ),
        )
    cfg = _preparar_credencial_coleta(
        tenant,
        store_id_exato,
        store_name,
        cfg,
    )
    deadline = time.monotonic() + BLING_CATALOG_TIMEOUT_SECONDS
    holder: dict[str, Any] = {}

    def operation(access_token: str) -> tuple[dict[str, Any], int]:
        return _coletar_com_token(
            access_token,
            store_id=store_id_exato,
            store_name=store_name,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
            deadline=deadline,
            holder=holder,
        )

    result, status, cfg_final = _bling_executar_com_refresh(
        tenant,
        store_name,
        cfg,
        operation,
        store_id=store_id_exato,
        require_owned_refresh=True,
        reused_concurrent_validator=lambda anterior, atual: (
            _mesma_identidade_estavel_bling(
                store_id_exato,
                store_name,
                anterior,
                atual,
            )
        ),
    )

    # Even a read-only result must not cross an account reconnect or a store
    # identity change that happened while the provider requests were in flight.
    current_name, current_cfg = _resolver_loja_bling(tenant, store_id_exato)
    if current_name != store_name or not _mesma_identidade_estavel_bling(
        store_id_exato,
        store_name,
        dict(cfg_final or {}),
        current_cfg,
    ):
        raise HTTPException(
            status_code=409,
            detail=_detail(
                "store_config_changed",
                "A configuracao Bling da loja mudou durante a coleta.",
                store_id=store_id_exato,
            ),
        )

    if not isinstance(result, dict):
        state = holder.get("state") or _estado_inicial(store_id_exato, store_name)
        _marcar_cobertura_sku_incompleta(
            state,
            f"catalog_collection_http_{status}",
        )
        result = _resultado_publico(state)
    elif status != 200:
        result["coverage_complete"] = False
        result["sku_coverage_complete"] = False
        warnings = result.setdefault("warnings", [])
        if status == 401:
            _adicionar_aviso(warnings, "bling_authentication_failed")
        elif status not in {499, 504}:
            _adicionar_aviso(warnings, f"catalog_collection_http_{status}")

    # Rebuild only the documented envelope, preventing accidental propagation
    # of credentials or internal collection state.
    return {
        "source": "bling",
        "store_id": store_id_exato,
        "store_name": store_name,
        "started_config_fingerprint": started_config_fingerprint,
        "started_apply_config_fingerprint": started_apply_config_fingerprint,
        "config_fingerprint": configuracao_catalogo_fingerprint(
            "bling",
            store_id_exato,
            current_name,
            current_cfg,
        ),
        "apply_config_fingerprint": configuracao_catalogo_aplicacao_fingerprint(
            "bling",
            store_id_exato,
            current_name,
            current_cfg,
        ),
        "coverage_complete": bool(result.get("coverage_complete")),
        "sku_coverage_complete": bool(result.get("sku_coverage_complete")),
        "items": list(result.get("items") or []),
        "skipped": list(result.get("skipped") or []),
        "stats": dict(result.get("stats") or {}),
        "warnings": list(result.get("warnings") or []),
    }


__all__ = ["coletar_catalogo_bling"]
