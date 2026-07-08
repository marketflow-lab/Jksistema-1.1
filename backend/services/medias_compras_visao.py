"""Calculation and dashboard view endpoints for Medias Compras."""

from __future__ import annotations

import os
import re
import sqlite3
import unicodedata
from datetime import datetime

import pandas as pd
from fastapi import Depends, HTTPException

from backend.schemas import MediasComprasRequest
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
    try:
        itens = [item.model_dump() for item in req.itens]
        return calcular_medias_compras(itens=itens, meses_cobertura=req.meses_cobertura)
    except Exception as e:
        logger.exception(f"Erro no mÃƒÂ³dulo MÃƒÂ©dias e compras: {e}")
        raise HTTPException(status_code=500, detail="Erro ao calcular mÃƒÂ©dias e compras")


async def api_medias_compras_visao(
    meses: int = 12,
    loja: str = "__todas",
    client_id: str = Depends(medias_common.get_tenant_id)
):
    try:
        if meses not in (3, 6, 12):
            raise HTTPException(status_code=400, detail="O perÃƒÂ­odo deve ser 3, 6 ou 12 meses")

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
        inicio_periodo = f"{meses_ref[0]}-01"
        fim_periodo = datetime.now().strftime("%Y-%m-%d")

        loja_sel = str(loja or "").strip() or "__todas"
        loja_sel_norm = loja_sel.lower()

        # Vendas por SKU e por mÃƒÂªs (somatÃƒÂ³rio de quantidade), consolidando todos os bancos do tenant.
        db_paths = _listar_bancos_vendas_tenant(client_id, loja_sel)
        vendas_por_sku = {}
        titulos_vendas_por_sku = {}
        ids_vendas_processados = set()

        for db_vendas in db_paths:
            if not (db_vendas and os.path.exists(db_vendas)):
                continue

            conn = sqlite3.connect(db_vendas)
            try:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("CREATE INDEX IF NOT EXISTS idx_vendas_sku_data ON vendas(sku, data)")

                query = (
                    """
                    SELECT
                        id_unico,
                        sku,
                        data AS data_venda,
                        COALESCE(produto, '') AS produto,
                        strftime('%Y-%m', date(data)) AS mes_ref,
                        COALESCE(quantidade, 0) AS qtd
                    FROM vendas
                    WHERE sku IS NOT NULL
                      AND trim(sku) != ''
                      AND date(data) BETWEEN ? AND ?
                    """
                )
                params = [inicio_periodo, fim_periodo]
                filtro_loja_sql, filtro_loja_params = _sql_filtro_loja_vendas(loja_sel)
                if filtro_loja_sql:
                    query += filtro_loja_sql
                    params.extend(filtro_loja_params)

                rows = cur.execute(query, params).fetchall()

                for r in rows:
                    sku_original = str(r["sku"] or "").strip()
                    if not sku_original:
                        continue

                    id_unico = str(r["id_unico"] or "").strip()
                    mes_ref = str(r["mes_ref"] or "").strip()
                    qtd = float(r["qtd"] or 0)
                    chave_unica = id_unico or f"{os.path.basename(db_vendas)}::{sku_original}::{mes_ref}::{qtd}"
                    if chave_unica in ids_vendas_processados:
                        continue
                    ids_vendas_processados.add(chave_unica)

                    sku_norm = _normalizar_sku_mes(sku_original)
                    titulo_venda = _titulo_venda_valido(r["produto"])
                    data_venda = str(r["data_venda"] or "").strip()
                    if titulo_venda:
                        atual = titulos_vendas_por_sku.get(sku_norm) or {}
                        if not atual or data_venda >= str(atual.get("data", "") or ""):
                            titulos_vendas_por_sku[sku_norm] = {
                                "titulo": titulo_venda,
                                "data": data_venda,
                            }
                    if sku_norm not in vendas_por_sku:
                        vendas_por_sku[sku_norm] = {
                            "sku": sku_norm,
                            "vendas_mensais": {m: 0.0 for m in meses_ref},
                        }
                    if mes_ref in vendas_por_sku[sku_norm]["vendas_mensais"]:
                        vendas_por_sku[sku_norm]["vendas_mensais"][mes_ref] += qtd

                query_titulos = (
                    """
                    SELECT
                        sku,
                        data AS data_venda,
                        COALESCE(produto, '') AS produto
                    FROM vendas
                    WHERE sku IS NOT NULL
                      AND trim(sku) != ''
                      AND trim(COALESCE(produto, '')) != ''
                    """
                )
                params_titulos = []
                filtro_titulos_sql, filtro_titulos_params = _sql_filtro_loja_vendas(loja_sel)
                if filtro_titulos_sql:
                    query_titulos += filtro_titulos_sql
                    params_titulos.extend(filtro_titulos_params)
                query_titulos += " ORDER BY date(data) DESC, data DESC"

                for r in cur.execute(query_titulos, params_titulos):
                    sku_titulo_raw = str(r["sku"] or "").strip()
                    if not sku_titulo_raw:
                        continue
                    sku_titulo_norm = _normalizar_sku_mes(sku_titulo_raw)
                    titulo_venda = _titulo_venda_valido(r["produto"])
                    data_venda = str(r["data_venda"] or "").strip()
                    if titulo_venda:
                        atual = titulos_vendas_por_sku.get(sku_titulo_norm) or {}
                        if not atual or data_venda >= str(atual.get("data", "") or ""):
                            titulos_vendas_por_sku[sku_titulo_norm] = {
                                "titulo": titulo_venda,
                                "data": data_venda,
                            }
            finally:
                conn.close()

        # Saldo de estoque por SKU considerando a loja selecionada.
        saldo_por_sku = {}
        arquivo_estoque = _migrar_arquivo_legado_para_tenant(client_id, "produtos_compilado.csv", ARQUIVO_DB_PRODUTOS)
        if arquivo_estoque and os.path.exists(arquivo_estoque):
            try:
                df_est = pd.read_csv(arquivo_estoque).fillna("")
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
                saldo_por_sku = saldo_por_sku or {}

        # Estoque jÃƒÂ¡ comprado e em trÃƒÂ¢nsito, vindo das listas com status aprovado.
        transito_por_sku = _mapa_estoque_em_transito_por_sku(client_id, loja_sel)
        ultima_venda_por_sku = _mapa_ultima_venda_por_sku(client_id, loja_sel)

        # Dados do cadastro por SKU (foto e tÃƒÂ­tulo do anuncio).
        cadastro_por_sku = {}
        cadastro_fallback_por_sku = {}
        mapa_fotos_cadastro = _cadastro_mapa_fotos_locais(client_id)
        arquivo_cadastro = _migrar_arquivo_legado_para_tenant(client_id, "cadastro_produtos.csv", ARQUIVO_DB_CADASTRO_PRODUTOS)
        caminhos_cadastro = []
        for caminho_cad in (
            arquivo_cadastro,
            os.path.join(PASTA_INFO, "default", "cadastro_produtos.csv"),
            ARQUIVO_DB_CADASTRO_PRODUTOS,
        ):
            if caminho_cad and os.path.exists(caminho_cad) and caminho_cad not in caminhos_cadastro:
                caminhos_cadastro.append(caminho_cad)

        for idx_cadastro, caminho_cadastro in enumerate(caminhos_cadastro):
            alvo_cadastro = cadastro_por_sku if idx_cadastro == 0 else cadastro_fallback_por_sku
            try:
                df_cad = pd.read_csv(caminho_cadastro, dtype=str).fillna("")
                if not df_cad.empty:
                    df_cad.columns = [str(c).strip().lower() for c in df_cad.columns]
                    mapa_colunas_cad = {_normalizar_chave_cadastro(c): c for c in df_cad.columns}
                    col_sku_cad = mapa_colunas_cad.get("sku")
                    if col_sku_cad:
                        for _, row in df_cad.iterrows():
                            sku_raw = str(row.get(col_sku_cad, "") or "").strip()
                            if not sku_raw:
                                continue
                            sku_norm = _normalizar_sku_mes(sku_raw)
                            if not sku_norm:
                                continue

                            foto = str(row.get("foto", "") or "").strip()
                            if not foto:
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

                            atual = alvo_cadastro.setdefault(sku_norm, {"foto": "", "titulo_anuncio": "", "titulo_cadastro": ""})
                            if not atual.get("foto") and foto:
                                atual["foto"] = foto
                            if not atual.get("titulo_anuncio") and titulo_anuncio:
                                atual["titulo_anuncio"] = titulo_anuncio
                            if not atual.get("titulo_cadastro") and titulo_cadastro:
                                atual["titulo_cadastro"] = titulo_cadastro
            except Exception:
                # Se cadastro estiver invalido, segue sem quebrar a tela.
                if idx_cadastro == 0:
                    cadastro_por_sku = cadastro_por_sku or {}
                else:
                    cadastro_fallback_por_sku = cadastro_fallback_por_sku or {}

        todos_skus = set(vendas_por_sku.keys()) | set(saldo_por_sku.keys()) | set(cadastro_por_sku.keys()) | set(transito_por_sku.keys()) | set(ultima_venda_por_sku.keys())
        itens = []

        for sku in sorted(todos_skus, key=_sku_sort_key):
            vendas_mensais = vendas_por_sku.get(sku, {"vendas_mensais": {m: 0.0 for m in meses_ref}})["vendas_mensais"]
            total_periodo = sum(float(vendas_mensais.get(m, 0) or 0) for m in meses_ref)
            saldo_atual = float(saldo_por_sku.get(sku, 0) or 0)
            estoque_em_transito = float(transito_por_sku.get(sku, 0) or 0)
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

            foto_cadastro = str(cadastro_por_sku.get(sku, {}).get("foto", "") or "").strip()
            if not foto_cadastro:
                foto_cadastro = _cadastro_resolver_foto_local(mapa_fotos_cadastro, sku)

            cad_principal = cadastro_por_sku.get(sku, {}) or {}
            cad_fallback = cadastro_fallback_por_sku.get(sku, {}) or {}
            titulo_venda = str((titulos_vendas_por_sku.get(sku, {}) or {}).get("titulo", "") or "").strip()
            titulo_anuncio = (
                str(cad_principal.get("titulo_anuncio", "") or "").strip()
                or str(cad_fallback.get("titulo_anuncio", "") or "").strip()
                or titulo_venda
                or str(cad_principal.get("titulo_cadastro", "") or "").strip()
                or str(cad_fallback.get("titulo_cadastro", "") or "").strip()
            )

            itens.append({
                "sku": sku,
                "foto": foto_cadastro,
                "titulo_anuncio": titulo_anuncio,
                "vendas_mensais": {m: round(float(vendas_mensais.get(m, 0) or 0), 2) for m in meses_ref},
                "total_vendas_periodo": round(total_periodo, 2),
                "saldo_atual_estoque": round(saldo_atual, 2),
                "estoque_em_transito": round(estoque_em_transito, 2),
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



def configure_medias_compras_visao_runtime(runtime_module=None):
    return _configure_runtime_globals(runtime_module)


configure_medias_compras_visao_runtime()

__all__ = [
    "configure_medias_compras_visao_runtime",
    "api_medias_compras_calcular",
    "api_medias_compras_visao",
]
