"""Calculation and dashboard view endpoints for Medias Compras."""

from __future__ import annotations

import os
import re
import sys
import unicodedata
import time
from datetime import datetime
from typing import Literal, NamedTuple

import pandas as pd
from backend.services.medias_compras_leitura import ler_csv, consulta_medias, fotos_da_consulta, registrar_tempo, leitura_atual, etapa
from backend.services.medias_compras_historico import ler_historico_vendas
from fastapi import Depends, HTTPException

from backend.schemas import MediasComprasRequest, MediasVisaoFotosRequest
from backend.services import medias_compras_common as medias_common
from backend.services.medias_compras_common import *
from backend.services.runtime_bridge import bind_runtime_globals
from medias_compras import calcular_medias_compras, calcular_reposicao_periodo


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


async def api_medias_compras_calcular(req: MediasComprasRequest):
    from backend.services.blocking_workers import run_heavy

    return await run_heavy(_api_medias_compras_calcular_sync, req)


def _api_medias_compras_calcular_sync(req: MediasComprasRequest):
    try:
        itens = [item.model_dump() for item in req.itens]
        return calcular_medias_compras(itens=itens, meses_cobertura=req.meses_cobertura)
    except Exception as e:
        logger.exception(f"Erro no mÃƒÂ³dulo MÃƒÂ©dias e compras: {e}")
        raise HTTPException(status_code=500, detail="Erro ao calcular mÃƒÂ©dias e compras")


async def api_medias_compras_visao(
    meses: int = 12,
    loja: str = "__todas",
    store_id: str = "",
    client_id: str = Depends(medias_common.get_tenant_id),
    cache_mode: Literal["stale"] | None = None,
    fotos: Literal["adiadas"] | None = None,
):
    from backend.services.medias_compras_cache import resultados_medias
    from backend.services.medias_compras_fontes import capturar_fontes, extras_runtime
    from backend.services.medias_compras_leitura import chave_arquivo
    from backend.services.medias_compras_visao_fontes import EtapasVisao

    if meses not in (3, 6, 12):
        raise HTTPException(400, "O periodo deve ser 3, 6 ou 12 meses")
    tenant = get_tenant_path(client_id)
    tenant_key = chave_arquivo(tenant)
    fotos_adiadas = fotos == "adiadas"
    modo_cache = ("fotos_adiadas",) if fotos_adiadas else ()

    def probe():
        if chave_arquivo(get_tenant_path(client_id)) != tenant_key:
            raise HTTPException(409, "A origem dos dados mudou durante a consulta.")
        scope = _resolver_escopo_loja_medias(client_id, loja, store_id)
        extras = extras_runtime(sys.modules[__name__])
        if fotos_adiadas:
            extras = [path for path in extras if os.path.basename(path) != "cadastro_fotos"]
        db_paths = tuple(_listar_bancos_vendas_tenant(client_id, scope["loja"]))
        extras.extend(db_paths)
        snapshot = {"escopo": scope, "db_paths": db_paths}
        source = capturar_fontes(
            tenant, extras=extras, escopo=(scope["store_id"], scope["loja"]),
            snapshot=snapshot, inventariar_fotos=not fotos_adiadas,
        )
        snapshot["etapas_visao"] = EtapasVisao(
            client_id, source, tenant,
            estoque_legado=globals().get("ARQUIVO_DB_PRODUTOS"),
            cadastro_legado=globals().get("ARQUIVO_DB_CADASTRO_PRODUTOS"),
        )
        return source

    def carregar(source):
        opcoes_fotos = {"_incluir_fotos": False} if fotos_adiadas else {}
        resultado = _api_medias_compras_visao_sync(
            meses=meses, loja=loja, store_id=store_id, client_id=client_id,
            _escopo_validado=source.snapshot["escopo"], _db_paths=source.snapshot["db_paths"],
            _fontes_snapshot=source.snapshot, **opcoes_fotos,
        )
        if fotos_adiadas:
            resultado["fotos_adiadas"] = True
        return resultado

    return await resultados_medias.consultar(
        client_id, lambda source: (tenant_key, "visao", meses, source.autoridade[1][0] or "__todas") + modo_cache,
        probe, carregar, stale=cache_mode == "stale", persistir_em=tenant,
    )


async def api_medias_compras_visao_fotos(
    req: MediasVisaoFotosRequest, client_id: str = Depends(medias_common.get_tenant_id),
):
    from backend.services.blocking_workers import run_heavy

    return await run_heavy(_carregar_fotos_visao_sync, client_id, req)


@consulta_medias
def _carregar_fotos_visao_sync(client_id: str, req: MediasVisaoFotosRequest):
    from backend.services.medias_compras_cache import _compactar_fontes
    from backend.services.medias_compras_fontes import capturar_fontes, extras_runtime
    from backend.services.medias_compras_leitura import chave_arquivo

    tenant = get_tenant_path(client_id)
    tenant_key = chave_arquivo(tenant)
    skus = {sku for value in req.skus if (sku := _normalizar_sku_mes(value))}
    if not skus:
        raise HTTPException(422, "Informe pelo menos um SKU válido.")
    extras = extras_runtime(sys.modules[__name__])

    def probe():
        if chave_arquivo(get_tenant_path(client_id)) != tenant_key:
            raise HTTPException(409, "A origem dos dados mudou durante a consulta.")
        scope = _resolver_escopo_loja_medias(client_id, req.loja, req.store_id)
        source = capturar_fontes(
            tenant, detalhe=True, extras=extras, escopo=(scope["store_id"], scope["loja"]),
            inventariar_fotos=False,
        )
        if source.pendente:
            raise HTTPException(409, "Aguarde a atualização dos dados para consultar as fotos.")
        return scope, source

    scope, before = probe()
    cadastro = _carregar_contexto_cadastro_visao(client_id, scope, skus=skus)
    if not cadastro.completo:
        raise HTTPException(503, "Não foi possível consultar as fotos agora.")
    fotos = {
        sku: cadastro.produtos.get(sku, {}).get("foto") or _cadastro_resolver_foto_local(cadastro.fotos_locais, sku)
        for sku in skus
    }
    _, after = probe()
    if before.autoridade != after.autoridade:
        raise HTTPException(409, "A loja ou a política de fotos mudou durante a consulta.")
    return {"fotos": fotos, "politica_fotos": _compactar_fontes(after).politica_fotos[0]}


@consulta_medias
def _api_medias_compras_visao_sync(
    meses: int = 12,
    loja: str = "__todas",
    store_id: str = "",
    client_id: str = "default",
    *, _escopo_validado=None, _db_paths=None, _fontes_snapshot=None, _incluir_fotos: bool = True,
):
    try:
        if meses not in (3, 6, 12):
            raise HTTPException(status_code=400, detail="O perÃƒÂ­odo deve ser 3, 6 ou 12 meses")

        def _meses_referencia(qtd_meses: int) -> list[str]:
            agora = datetime.now()
            ano = agora.year
            mes = agora.month
            refs = []
            for i in range(qtd_meses - 1, -1, -1):
                m = mes - i
                a = ano
                while m <= 0:
                    m += 12
                    a -= 1
                refs.append(f"{a:04d}-{m:02d}")
            return refs

        def _sku_sort_key(sku_valor: str):
            sku_txt = str(sku_valor or "").strip().upper()
            primeiro_bloco = re.split(r"[.-]", sku_txt)[0] if sku_txt else ""
            somente_digitos = "".join(ch for ch in primeiro_bloco if ch.isdigit())
            if somente_digitos:
                return (0, int(somente_digitos), sku_txt)
            return (1, sku_txt)

        def _titulo_venda_valido(valor: str) -> str:
            titulo = re.sub(r"\s+", " ", str(valor or "").strip())
            if not titulo:
                return ""
            titulo_norm = normalizar_texto(titulo).strip().lower()
            invalidos = {"-", "produto s descricao", "produto s descricao", "produto sem descricao", "sem descricao"}
            if titulo_norm in invalidos:
                return ""
            return titulo

        meses_ref = _meses_referencia(meses)
        fim_periodo = datetime.now().strftime("%Y-%m-%d")

        escopo_loja = _escopo_validado if _escopo_validado is not None else _resolver_escopo_loja_medias(
            client_id,
            loja,
            store_id,
        )
        loja_sel = escopo_loja["loja"]
        store_id_escopo = escopo_loja["store_id"]

        # Vendas por SKU e por mÃƒÂªs (somatÃƒÂ³rio de quantidade), consolidando todos os bancos do tenant.
        inicio_historico = time.perf_counter()
        db_paths = _db_paths if _db_paths is not None else _listar_bancos_vendas_tenant(client_id, loja_sel)
        db_paths = tuple(path for path in db_paths if path and os.path.exists(path))
        vendas_por_sku, titulos_vendas_por_sku, ultima_venda_snapshot = {}, {}, {}
        if db_paths:
            filtro_loja_sql, filtro_loja_params = _sql_filtro_loja_vendas(loja_sel)
            vendas_por_sku, titulos_vendas_por_sku, ultima_venda_snapshot = ler_historico_vendas(
                client_id=client_id, loja=loja_sel, store_id=store_id_escopo,
                db_paths=db_paths, meses=meses, fim_periodo=fim_periodo,
                filtro_sql=filtro_loja_sql, filtro_params=filtro_loja_params,
                normalizar_sku=_normalizar_sku_mes, titulo_valido=_titulo_venda_valido,
                normalizacao_token=normalizar_texto, snapshot=_fontes_snapshot,
            )
        leitura = leitura_atual()
        if leitura is not None:
            leitura.values[("historico_ultima_venda", client_id, loja_sel, tuple(db_paths))] = ultima_venda_snapshot

        registrar_tempo("historico", inicio_historico)
        etapas = _fontes_snapshot.get("etapas_visao") if isinstance(_fontes_snapshot, dict) else None

        def carregar_estoque():
            return _carregar_estoque_visao(client_id, loja_sel)

        estoque = (
            etapas.obter("estoque", carregar_estoque, completo=lambda valor: valor.completo)
            if etapas is not None else carregar_estoque()
        )
        saldo_por_sku = estoque.saldo

        # Estoque presente no fluxo de Importacoes, com detalhamento por lista.
        @etapa("transito")
        def carregar_transito():
            return _mapa_estoque_em_transito_detalhado_por_sku(client_id, loja_sel)

        transito_detalhado_por_sku = (
            etapas.obter("transito", carregar_transito) if etapas is not None else carregar_transito()
        )
        transito_por_sku = {
            sku: float((detalhe or {}).get("total", 0) or 0)
            for sku, detalhe in transito_detalhado_por_sku.items()
        }
        ultima_venda_por_sku = (
            _mapa_ultima_venda_por_sku(client_id, loja_sel, _db_paths=db_paths)
            if _db_paths is not None else _mapa_ultima_venda_por_sku(client_id, loja_sel)
        )

        def carregar_catalogo():
            return _carregar_contexto_cadastro_visao(
                client_id, escopo_loja, incluir_fotos=_incluir_fotos, etapas=etapas,
            )

        cadastro = etapas.catalogo(_incluir_fotos, carregar_catalogo) if etapas is not None else carregar_catalogo()

        todos_skus = (
            set(vendas_por_sku)
            | set(saldo_por_sku)
            | cadastro.skus_visiveis
            | set(transito_por_sku)
            | set(ultima_venda_por_sku)
        )
        itens = []

        for sku in sorted(todos_skus, key=_sku_sort_key):
            vendas_mensais = vendas_por_sku.get(sku, {"vendas_mensais": {m: 0.0 for m in meses_ref}})["vendas_mensais"]
            total_periodo = sum(float(vendas_mensais.get(m, 0) or 0) for m in meses_ref)
            saldo_atual = float(saldo_por_sku.get(sku, 0) or 0)
            estoque_em_transito = float(transito_por_sku.get(sku, 0) or 0)
            listas_estoque_em_transito = [
                {
                    "lista_id": str((origem or {}).get("lista_id", "") or ""),
                    "nome_lista": str((origem or {}).get("nome_lista", "") or "").strip() or "Sem nome",
                    "quantidade": round(float((origem or {}).get("quantidade", 0) or 0), 2),
                }
                for origem in ((transito_detalhado_por_sku.get(sku, {}) or {}).get("listas") or [])
                if float((origem or {}).get("quantidade", 0) or 0) > 0
            ]
            ultima_venda = str(ultima_venda_por_sku.get(sku, "") or "")
            meses_sem_vender = _meses_sem_vender_desde(ultima_venda)
            aviso_sem_venda = _mensagem_sem_venda(meses_sem_vender)
            reposicao = calcular_reposicao_periodo(
                total_vendido_periodo=total_periodo,
                saldo_atual=saldo_atual,
                estoque_em_transito=estoque_em_transito,
                periodo_meses=meses,
                lead_time_meses=6,
                ciclo_compra_meses=3,
                margem_seguranca_meses=1,
                fator_crescimento=1.0,
            )

            cad_principal = cadastro.produtos.get(sku, {})
            foto_cadastro = str(cad_principal.get("foto") or "").strip()
            if _incluir_fotos and not foto_cadastro:
                foto_cadastro = _cadastro_resolver_foto_local(cadastro.fotos_locais, sku)

            titulo_venda = str((titulos_vendas_por_sku.get(sku, {}) or {}).get("titulo", "") or "").strip()
            titulo_anuncio = (
                str(cad_principal.get("titulo_anuncio", "") or "").strip()
                or titulo_venda
                or str(cad_principal.get("titulo_cadastro", "") or "").strip()
            )

            itens.append({
                "sku": sku,
                "foto": foto_cadastro,
                "titulo_anuncio": titulo_anuncio,
                "vendas_mensais": {m: round(float(vendas_mensais.get(m, 0) or 0), 2) for m in meses_ref},
                "total_vendas_periodo": round(total_periodo, 2),
                "saldo_atual_estoque": round(saldo_atual, 2),
                "estoque_em_transito": round(estoque_em_transito, 2),
                "estoque_em_transito_listas": listas_estoque_em_transito,
                "posicao_estoque": float(reposicao.get("posicao_estoque", 0) or 0),
                "media_mensal": float(reposicao.get("media_mensal", 0) or 0),
                "lead_time_meses": float(reposicao.get("lead_time_meses", 6) or 6),
                "meses_cobertura": float(reposicao.get("meses_cobertura", 0) or 0),
                "compra_sugerida": int(reposicao.get("compra_sugerida", 0) or 0),
                "ultima_venda": ultima_venda,
                "meses_sem_vender": int(meses_sem_vender),
                "aviso_sem_venda": aviso_sem_venda,
            })

        colunas_meses = [{"key": m, "label": f"{m[5:7]}/{m[0:4]}"} for m in meses_ref]

        return {
            "success": True,
            "periodo_meses": meses,
            "loja": loja_sel,
            "store_id": cadastro.store_id or store_id_escopo,
            "colunas_meses": colunas_meses,
            "itens": itens,
            "resumo": {
                "total_skus": len(itens),
                "total_vendido_periodo": round(sum(i["total_vendas_periodo"] for i in itens), 2),
                "saldo_total_estoque": round(sum(i["saldo_atual_estoque"] for i in itens), 2),
                "estoque_total_transito": round(sum(float(i.get("estoque_em_transito", 0) or 0) for i in itens), 2),
                "total_compra_sugerida": int(sum(int(i.get("compra_sugerida", 0) or 0) for i in itens)),
                "lead_time_meses": 6,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Erro ao montar visÃƒÂ£o de mÃƒÂ©dias e compras: {e}")
        raise HTTPException(status_code=500, detail="Erro ao consultar dados de vendas e estoque")



def _safe_float(valor, default=0.0):
    if valor is None:
        return default
    if isinstance(valor, (int, float)):
        return float(valor)
    txt = str(valor).strip().replace(".", "").replace(",", ".")
    if not txt:
        return default
    try:
        return float(txt)
    except Exception:
        return default


def _normalizar_chave_cadastro(valor: str) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or "").strip().lower())
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()

def _pick_cadastro_valor(row, mapa_colunas: dict, aliases: list[str]) -> str:
    for alias in aliases:
        col = mapa_colunas.get(_normalizar_chave_cadastro(alias))
        if col is None:
            continue
        valor = str(row.get(col, "") or "").strip()
        if valor:
            return valor
    return ""

def _primeiro_titulo_lista(valor: str) -> str:
    texto = str(valor or "").strip()
    if not texto:
        return ""
    for parte in re.split(r"\|\||\n|;", texto):
        titulo = str(parte or "").strip()
        if titulo:
            return titulo
    return texto



class _EstoqueVisao(NamedTuple):
    saldo: dict
    completo: bool


@etapa("estoque")
def _carregar_estoque_visao(client_id, loja_sel):
    loja_sel_norm = loja_sel.lower()
    # Saldo de estoque por SKU considerando a loja selecionada.
    saldo_por_sku = {}
    arquivo_estoque = _migrar_arquivo_legado_para_tenant(client_id, "produtos_compilado.csv", ARQUIVO_DB_PRODUTOS)
    if arquivo_estoque and os.path.exists(arquivo_estoque):
        try:
            df_est = ler_csv(arquivo_estoque).fillna("")
            if not df_est.empty:
                colunas_lower = {str(c).strip().lower(): c for c in df_est.columns}
                col_sku = colunas_lower.get("sku") or next((c for c in df_est.columns if str(c).strip().lower() == "sku"), None)
                col_loja_sync = colunas_lower.get("loja_sync")

                if col_loja_sync is not None and loja_sel_norm != "__todas":
                    df_est = df_est[
                        df_est[col_loja_sync].astype(str).str.strip().str.lower() == loja_sel_norm
                    ].copy()

                if col_sku is not None:
                    for _, row in df_est.iterrows():
                        sku_raw = str(row.get(col_sku, "") or "").strip()
                        if not sku_raw:
                            continue
                        sku_norm = _normalizar_sku_mes(sku_raw)

                        saldo_loja = _safe_float(row.get("saldo_loja", 0), 0.0)
                        # Compatibilidade com bases antigas sem coluna saldo_loja.
                        saldo_total = saldo_loja if saldo_loja != 0 else _safe_float(row.get("saldo", row.get("saldo_total", 0)), 0.0)

                        saldo_por_sku[sku_norm] = saldo_por_sku.get(sku_norm, 0.0) + saldo_total
        except Exception:
            # Se o estoque estiver invalido, nÃ£o quebra a tela; retorna com saldo zero.
            return _EstoqueVisao(saldo_por_sku, False)
    return _EstoqueVisao(saldo_por_sku, True)


class _CadastroVisao(NamedTuple):
    produtos: dict
    skus_visiveis: set
    store_id: str
    fotos_locais: dict
    completo: bool


@etapa("catalogo")
def _carregar_contexto_cadastro_visao(client_id, escopo_loja, *, incluir_fotos=True, skus=None, etapas=None):
    loja_sel = escopo_loja["loja"]
    store_id_escopo = escopo_loja["store_id"]
    selecionados = None if skus is None else {_normalizar_sku_mes(sku) for sku in skus}
    # Dados do cadastro por SKU (foto e tÃƒÂ­tulo do anuncio).
    cadastro_por_sku = {}
    skus_cadastro_visiveis = set()
    completo = True
    arquivo_cadastro = _migrar_arquivo_legado_para_tenant(client_id, "cadastro_produtos.csv", ARQUIVO_DB_CADASTRO_PRODUTOS)
    caminhos_cadastro = []
    for caminho_cad in (
        arquivo_cadastro,
        os.path.join(PASTA_INFO, "default", "cadastro_produtos.csv"),
        ARQUIVO_DB_CADASTRO_PRODUTOS,
    ):
        if caminho_cad and os.path.exists(caminho_cad) and caminho_cad not in caminhos_cadastro:
            caminhos_cadastro.append(caminho_cad)

    linhas_cadastro_legado = []
    for caminho_cadastro in caminhos_cadastro:
        try:
            df_cad = ler_csv(caminho_cadastro, dtype=str).fillna("")
            if not df_cad.empty:
                df_cad.columns = [str(c).strip().lower() for c in df_cad.columns]
                linhas_cadastro_legado.extend(df_cad.to_dict(orient="records"))
        except Exception:
            completo = False
            continue

    from backend.services.cadastro_compatibilidade import (
        mesclar_produtos_legados_com_contexto_loja,
    )

    opcoes = {}
    if selecionados is not None:
        from backend.services.cadastro_lojas_produtos import _normalizar_sku_chave

        opcoes.update(skus=selecionados, _normalizar_sku_consulta=_normalizar_sku_chave)
    if not incluir_fotos:
        opcoes["_incluir_fotos"] = False
    try:
        contexto_cadastro = mesclar_produtos_legados_com_contexto_loja(
            client_id,
            linhas_cadastro_legado,
            store_id_escopo or loja_sel,
            **opcoes,
        )
    except RuntimeError:
        contexto_cadastro = {"produtos": [], "store_id": "", "scope": "unavailable"}
    completo = completo and contexto_cadastro.get("scope") not in {"unavailable", "unresolved"}
    store_id_cadastro = str(contexto_cadastro.get("store_id") or "").strip()
    mapa_fotos_cadastro = {}
    if incluir_fotos:
        def carregar_fotos_locais():
            opcoes_fotos = {} if selecionados is None else {"skus": selecionados}
            return fotos_da_consulta(_cadastro_mapa_fotos_locais)(
                client_id, store_id_cadastro or None, **opcoes_fotos,
            )

        mapa_fotos_cadastro = (
            etapas.obter("fotos", carregar_fotos_locais, variacao=(store_id_cadastro,))
            if etapas is not None and selecionados is None and completo else carregar_fotos_locais()
        )
    for row in contexto_cadastro.get("produtos") or []:
        if not isinstance(row, dict):
            continue
        mapa_colunas_cad = {
            _normalizar_chave_cadastro(c): c
            for c in row
        }
        col_sku_cad = mapa_colunas_cad.get("sku")
        sku_raw = str(row.get(col_sku_cad, "") or "").strip() if col_sku_cad else ""
        sku_norm = _normalizar_sku_mes(sku_raw)
        if not sku_norm or (selecionados is not None and sku_norm not in selecionados):
            continue

        # Um cadastro excluido no Bling nao cria uma linha vazia. Os dados
        # continuam disponiveis para SKUs com vendas, estoque ou importacao.
        situacao_bling = str(row.get("situacao_bling") or "").strip().upper()
        tem_anuncio_ml = (
            bool(str(row.get("mlb_ids") or "").strip())
            or bool(str(row.get("mlb_principal") or "").strip())
            or _safe_float(row.get("qtd_anuncios_mlb"), 0.0) > 0
        )
        if situacao_bling != "E" or tem_anuncio_ml:
            skus_cadastro_visiveis.add(sku_norm)

        foto = str(row.get("foto", "") or "").strip() if incluir_fotos else ""
        if incluir_fotos and not foto:
            foto = _cadastro_resolver_foto_local(mapa_fotos_cadastro, sku_norm)
        titulos_mlb = _pick_cadastro_valor(row, mapa_colunas_cad, [
            "titulos_anuncios_mlb", "titulos anuncios mlb", "titulo anuncio", "titulo do anuncio",
            "titulo do anúncio", "titulo mlb", "title", "titulo",
        ])
        titulo_anuncio = _primeiro_titulo_lista(titulos_mlb)
        titulo_cadastro = _pick_cadastro_valor(row, mapa_colunas_cad, [
            "produto bling", "produtos bling", "produto_bling", "nome_bling",
            "titulo do produto em ingles", "titulo em ingles", "titulo ingles",
            "cg product name", "product name", "nome", "produto",
            "cg tradução ptbr ou nome na bling", "cg denominacao do produto",
            "cg denominação do produto",
        ])
        atual = cadastro_por_sku.setdefault(
            sku_norm,
            {"foto": "", "titulo_anuncio": "", "titulo_cadastro": ""},
        )
        if not atual.get("foto") and foto:
            atual["foto"] = foto
        if not atual.get("titulo_anuncio") and titulo_anuncio:
            atual["titulo_anuncio"] = titulo_anuncio
        if not atual.get("titulo_cadastro") and titulo_cadastro:
            atual["titulo_cadastro"] = titulo_cadastro
    return _CadastroVisao(cadastro_por_sku, skus_cadastro_visiveis, store_id_cadastro, mapa_fotos_cadastro, completo)


def configure_medias_compras_visao_runtime(runtime_module=None):
    return _configure_runtime_globals(runtime_module)


configure_medias_compras_visao_runtime()

__all__ = [
    "configure_medias_compras_visao_runtime",
    "api_medias_compras_calcular",
    "api_medias_compras_visao",
    "api_medias_compras_visao_fotos",
]
