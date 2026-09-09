"""Read-only projections for store-scoped Cadastro products.

This module builds one request-local snapshot for all requested stores.  It
keeps the legacy compatibility rules from ``cadastro_lojas_produtos`` while
avoiding one complete CSV read and legacy re-index per store.
"""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Any, Iterable, Literal

from fastapi import Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from backend.services import cadastro_lojas_produtos as _store
from backend.services.path_coordination import path_locks_for


SUMMARY_FIELDS = frozenset(
    {
        "store_id",
        "loja_sync",
        "sku",
        "sku_normalizado",
        "scope_source",
        "row_version",
        "updated_at_utc",
        "foto",
        "produto_bling",
        "nome",
        "produto",
        "titulo_ml",
        "ncm",
        "ncm_validade",
        "monofasico",
        "monofasico_confianca",
        "cest",
        "categoria",
        "custo",
        "preco",
        "descricao",
        "imposto",
        "updated_at",
        "mlb_ids",
        "titulos_anuncios_mlb",
        "custos_frete_mlb",
    }
)


def projetar_produto_resumido(produto: dict[str, Any]) -> dict[str, Any]:
    """Return only fields consumed by the Cadastro list, cost and MLB panels."""

    return {
        chave: valor
        for chave, valor in produto.items()
        if chave in SUMMARY_FIELDS
    }


def _normalizar_lojas(lojas: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    normalizadas: list[dict[str, Any]] = []
    ids: set[str] = set()
    for item in lojas:
        if not isinstance(item, dict):
            continue
        store_id = str(item.get("store_id") or "").strip()
        if not store_id:
            continue
        if store_id in ids:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "store_config_ambiguous",
                    "message": "A identidade da loja esta duplicada na configuracao.",
                },
            )
        ids.add(store_id)
        normalizada = dict(item)
        normalizada["store_id"] = store_id
        normalizada["nome"] = str(item.get("nome") or "").strip()
        normalizadas.append(normalizada)
    return normalizadas


def _selecionar_lojas(
    lojas: list[dict[str, Any]],
    store_ids: Iterable[str] | None,
) -> list[dict[str, Any]]:
    if store_ids is None:
        return lojas
    solicitados = [str(valor or "").strip() for valor in store_ids]
    solicitados = [valor for valor in solicitados if valor]
    por_id = {loja["store_id"]: loja for loja in lojas}
    ausentes = [store_id for store_id in solicitados if store_id not in por_id]
    if ausentes:
        raise HTTPException(
            status_code=404,
            detail="Loja nao encontrada para este cliente.",
        )
    conjunto = set(solicitados)
    return [loja for loja in lojas if loja["store_id"] in conjunto]


def _construir_indice_legado(
    lojas: list[dict[str, Any]],
    fontes: dict[str, list[dict[str, str]]],
    colunas_fontes: dict[str, list[str]],
) -> dict[str, Any]:
    ids_por_nome, nomes_ambiguos = _store._nomes_lojas_unicos(lojas)
    store_ids_atuais = {loja["store_id"] for loja in lojas}
    associacoes: set[tuple[str, str]] = set()
    nao_mapeados: list[dict[str, str]] = []
    vistos_nao_mapeados: set[tuple[str, str, str, str]] = set()
    resumo = {
        fonte: {
            "arquivo": _store._ARQUIVOS_LEGADOS[fonte],
            "linhas": len(linhas),
            "associacoes_comprovadas": 0,
            "nao_mapeadas": 0,
        }
        for fonte, linhas in fontes.items()
    }
    base_generica: dict[str, dict[str, str]] = {}
    base_por_loja: dict[str, dict[str, dict[str, str]]] = {}
    compilado_por_loja: dict[str, dict[str, dict[str, str]]] = {}
    custos_por_loja: dict[str, dict[str, dict[str, str]]] = {}
    prioridades_compilado: dict[tuple[str, str], int] = {}
    prioridades_custos: dict[tuple[str, str], int] = {}

    def registrar_nao_mapeado(
        chave: tuple[str, str, str, str],
        payload: dict[str, str],
    ) -> None:
        if chave in vistos_nao_mapeados:
            return
        vistos_nao_mapeados.add(chave)
        nao_mapeados.append(payload)
        resumo[payload["fonte"]]["nao_mapeadas"] += 1

    for fonte, linhas in fontes.items():
        for indice, linha in enumerate(linhas, start=2):
            sku = _store._normalizar_sku_chave(linha.get("sku") or "")
            labels = _store._labels_loja(linha)
            ids_linha: list[str] = []
            if not sku:
                registrar_nao_mapeado(
                    (fonte, str(indice), "", "sku_ausente"),
                    {
                        "fonte": fonte,
                        "linha": str(indice),
                        "sku": "",
                        "loja_sync": "|".join(labels),
                        "motivo": "sku_ausente",
                    },
                )
                continue

            store_id_fonte = str(linha.get("store_id") or "").strip()
            if store_id_fonte:
                if store_id_fonte not in store_ids_atuais:
                    motivo = "store_id_nao_corresponde_a_loja_atual"
                    registrar_nao_mapeado(
                        (fonte, sku, store_id_fonte, motivo),
                        {
                            "fonte": fonte,
                            "linha": str(indice),
                            "sku": sku,
                            "store_id": store_id_fonte,
                            "loja_sync": "|".join(labels),
                            "motivo": motivo,
                        },
                    )
                    continue
                ids_linha.append(store_id_fonte)
                associacoes.add((store_id_fonte, sku))
                resumo[fonte]["associacoes_comprovadas"] += 1

            if not labels and not ids_linha:
                registrar_nao_mapeado(
                    (fonte, sku, "", "sem_loja_comprovada"),
                    {
                        "fonte": fonte,
                        "linha": str(indice),
                        "sku": sku,
                        "loja_sync": "",
                        "motivo": "sem_loja_comprovada",
                    },
                )
                if fonte == "cadastro_base":
                    _store._merge_preenchidos(base_generica.setdefault(sku, {}), linha)
                continue

            if store_id_fonte:
                labels = []
            for label in labels:
                chave_label = _store._nome_loja_chave(label)
                if chave_label in nomes_ambiguos:
                    motivo = "nome_loja_atual_ambiguo"
                    store_id = ""
                else:
                    store_id = ids_por_nome.get(chave_label, "")
                    motivo = "label_nao_corresponde_a_loja_atual"
                if not store_id:
                    registrar_nao_mapeado(
                        (fonte, sku, label, motivo),
                        {
                            "fonte": fonte,
                            "linha": str(indice),
                            "sku": sku,
                            "loja_sync": label,
                            "motivo": motivo,
                        },
                    )
                    continue
                ids_linha.append(store_id)
                associacoes.add((store_id, sku))
                resumo[fonte]["associacoes_comprovadas"] += 1

            for store_id in set(ids_linha):
                if fonte == "cadastro_base":
                    destino = base_por_loja.setdefault(store_id, {}).setdefault(sku, {})
                    _store._merge_preenchidos(destino, linha)
                elif fonte == "produtos_compilado":
                    prioridade = 2 if store_id_fonte else 1
                    chave = (store_id, sku)
                    if prioridade >= prioridades_compilado.get(chave, 0):
                        prioridades_compilado[chave] = prioridade
                        compilado_por_loja.setdefault(store_id, {})[sku] = dict(linha)
                elif fonte == "custos_loja":
                    prioridade = 2 if store_id_fonte else 1
                    chave = (store_id, sku)
                    if prioridade >= prioridades_custos.get(chave, 0):
                        prioridades_custos[chave] = prioridade
                        custos_por_loja.setdefault(store_id, {})[sku] = dict(linha)

    return {
        "associacoes": associacoes,
        "base_generica": base_generica,
        "base_por_loja": base_por_loja,
        "compilado_por_loja": compilado_por_loja,
        "custos_por_loja": custos_por_loja,
        "nao_mapeados": nao_mapeados,
        "resumo_fontes": resumo,
        "colunas_fontes": colunas_fontes,
    }


def _contexto_loja(
    client_id: str,
    loja: dict[str, Any],
    indice: dict[str, Any],
) -> dict[str, Any]:
    store_id = loja["store_id"]
    sombras: dict[str, dict[str, str]] = {}
    campos_derivados: dict[str, set[str]] = {}
    for assoc_store_id, sku in sorted(indice["associacoes"]):
        if assoc_store_id != store_id:
            continue
        produto: dict[str, str] = {}
        _store._merge_preenchidos(produto, indice["base_generica"].get(sku, {}))
        explicitos = _store._merge_sobrescrever_preenchidos(
            produto,
            indice["base_por_loja"].get(store_id, {}).get(sku, {}),
        )
        campos_derivados[sku] = _store._aplicar_compilado(
            produto,
            indice["compilado_por_loja"].get(store_id, {}).get(sku, {}),
            sobrescrever_aliases_legadas=True,
            campos_explicitos_loja=explicitos,
        )
        produto.update(
            {
                "store_id": store_id,
                "sku": _store._normalizar_sku_mes(produto.get("sku") or sku),
                "sku_normalizado": sku,
                "loja_sync": loja["nome"],
                "row_version": "0",
                "updated_at_utc": "",
                "deleted_at_utc": "",
                "scope_source": "legacy_shadow",
            }
        )
        sombras[sku] = produto
    return {
        "client_id": client_id,
        "sombras": sombras,
        "compilado": indice["compilado_por_loja"].get(store_id, {}),
        "custos": indice["custos_por_loja"].get(store_id, {}),
        "fotos": _store._cadastro_mapa_fotos_locais(client_id, store_id),
        "campos_derivados_sombras": campos_derivados,
        "nao_mapeados": indice["nao_mapeados"],
        "resumo_fontes": indice["resumo_fontes"],
        "colunas_fontes": indice["colunas_fontes"],
    }


def _capturar_snapshot(
    client_id: str,
    store_ids: Iterable[str] | None,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, str]]], dict[str, Any]]:
    tenant = os.fspath(_store.get_tenant_path(client_id))
    caminho_canonico = _store._cadastro_produtos_lojas_path(client_id)
    caminhos_legados = [
        os.path.join(tenant, arquivo)
        for arquivo in _store._ARQUIVOS_LEGADOS.values()
    ]
    with _store.integracoes._LOJAS_CONFIG_LOCK:
        lojas = _normalizar_lojas(_store._lojas_atuais(client_id))
        selecionadas = _selecionar_lojas(lojas, store_ids)
        with path_locks_for([caminho_canonico, *caminhos_legados]):
            registros, _ = _store._ler_registros_persistidos(client_id)
            fontes: dict[str, list[dict[str, str]]] = {}
            colunas_fontes: dict[str, list[str]] = {}
            for fonte, arquivo in _store._ARQUIVOS_LEGADOS.items():
                linhas, colunas = _store._ler_csv_generico(os.path.join(tenant, arquivo))
                fontes[fonte] = linhas
                colunas_fontes[fonte] = colunas
    por_loja: dict[str, list[dict[str, str]]] = defaultdict(list)
    for registro in registros:
        por_loja[str(registro.get("store_id") or "")].append(registro)
    indice = _construir_indice_legado(lojas, fontes, colunas_fontes)
    return selecionadas, por_loja, indice


def _produtos_loja_do_snapshot(
    client_id: str,
    loja: dict[str, Any],
    registros: list[dict[str, str]],
    indice: dict[str, Any],
    *,
    include_deleted: bool,
    include_legacy_snapshot_hash: bool = False,
) -> list[dict[str, Any]]:
    contexto = _contexto_loja(client_id, loja, indice)
    saida: list[dict[str, Any]] = []
    chaves_explicitadas: set[str] = set()
    for item in registros:
        sku = item.get("sku_normalizado", "")
        chaves_explicitadas.add(sku)
        apagado = bool(str(item.get("deleted_at_utc") or "").strip())
        if apagado and not include_deleted:
            continue
        saida.append(_store._enriquecer_produto(item, loja, contexto))
    for sku, sombra in contexto["sombras"].items():
        if sku in chaves_explicitadas:
            continue
        enriquecido = _store._enriquecer_produto(
            sombra,
            loja,
            contexto,
            scope_source="legacy_shadow",
        )
        if include_legacy_snapshot_hash:
            enriquecido["__legacy_snapshot_hash"] = _store._fingerprint_sombra_legada(
                sombra
            )
        saida.append(enriquecido)
    return sorted(saida, key=lambda item: str(item.get("sku_normalizado") or ""))


def listar_produtos_loja_snapshot_sync(
    client_id: str,
    store_id: str,
    *,
    include_deleted: bool = False,
    include_legacy_snapshot_hash: bool = False,
    view: Literal["full", "summary"] = "full",
) -> list[dict[str, Any]]:
    produtos = _store._listar_produtos_loja_sync(
        client_id,
        store_id,
        include_deleted=include_deleted,
        include_legacy_snapshot_hash=include_legacy_snapshot_hash,
    )
    if view == "summary":
        return [projetar_produto_resumido(produto) for produto in produtos]
    return produtos


def listar_produtos_lojas_snapshot_sync(
    client_id: str,
    *,
    view: Literal["full", "summary"] = "summary",
) -> dict[str, Any]:
    lojas, por_loja, indice = _capturar_snapshot(client_id, None)
    produtos: list[dict[str, Any]] = []
    estados: list[dict[str, Any]] = []
    falhas = 0
    for loja in lojas:
        try:
            produtos_loja = _produtos_loja_do_snapshot(
                client_id,
                loja,
                por_loja.get(loja["store_id"], []),
                indice,
                include_deleted=False,
            )
            if view == "summary":
                produtos_loja = [
                    projetar_produto_resumido(produto) for produto in produtos_loja
                ]
            produtos.extend(produtos_loja)
            estados.append(
                {
                    "store_id": loja["store_id"],
                    "loja_sync": loja["nome"],
                    "status": "ok",
                    "total": len(produtos_loja),
                }
            )
        except HTTPException as exc:
            falhas += 1
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            estados.append(
                {
                    "store_id": loja["store_id"],
                    "loja_sync": loja["nome"],
                    "status": "error",
                    "total": 0,
                    "erro_codigo": str(detail.get("code") or "store_projection_failed"),
                }
            )
        except Exception as exc:  # pragma: no cover - guarded by integration tests
            falhas += 1
            _store.logger.error(
                "[CADASTRO LOJAS] Falha na projecao da loja: %s",
                type(exc).__name__,
            )
            estados.append(
                {
                    "store_id": loja["store_id"],
                    "loja_sync": loja["nome"],
                    "status": "error",
                    "total": 0,
                    "erro_codigo": "store_projection_failed",
                }
            )
    if lojas and falhas == len(lojas):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "cadastro_stores_unavailable",
                "message": "Nao foi possivel carregar o cadastro das lojas.",
                "lojas": estados,
            },
        )
    return {
        "produtos": produtos,
        "lojas": estados,
        "total": len(produtos),
        "partial": bool(falhas),
    }


async def listar_produtos_lojas(
    view: Literal["summary"] = "summary",
    client_id: str = Depends(_store.get_tenant_id),
):
    return await run_in_threadpool(
        listar_produtos_lojas_snapshot_sync,
        client_id,
        view=view,
    )


__all__ = [
    "SUMMARY_FIELDS",
    "listar_produtos_loja_snapshot_sync",
    "listar_produtos_lojas",
    "listar_produtos_lojas_snapshot_sync",
    "projetar_produto_resumido",
]
