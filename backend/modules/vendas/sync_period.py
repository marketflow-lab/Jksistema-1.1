"""Common imports and runtime glue for Vendas endpoint modules."""

from __future__ import annotations

import asyncio
import copy
import datetime as dt
import functools
import io
import json
import math
import os
import re
import sqlite3
import threading
import time
import traceback
import unicodedata
from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd
from backend.schemas import VendasSyncRequest

from .dependencies import logger
from .errors import VendasDomainError as HTTPException
from .legacy import (
    _bling_executar_com_refresh,
    _bling_listar_naturezas,
    _bling_listar_notas_entrada,
    _bling_listar_vendas,
    _bling_listar_vendas_fallback_nf_saida,
    _bling_marcar_oauth_invalido,
    _bling_obter_detalhes_nf,
    _bling_obter_numero_nf,
    _bling_renovar_token_loja,
    _carregar_mapeamento_lojas_virtuais_cliente,
    _carregar_mapeamento_unidades,
    _classificar_unidade_virtual_devolucao,
    _eh_devolucao_nota_entrada,
    _eh_devolucao_por_cfop_itens,
    _extrair_codigo_origem_nf,
    _get_notas_entrada_db,
    _get_vendas_db,
    _normalizar_nome_loja_virtual_candidato,
    _normalizar_texto,
    _normalizar_unidade_devolucao_entrada,
    _resolver_nome_loja_virtual,
    _tipo_devolucao_cfop_full_estoque,
    buscar_loja,
)
from .progress import (
    _criar_progresso,
    _set_progresso,
    _sync_context_key,
    _sync_log,
    _verificar_cancelamento,
)
from .state import SYNC_CANCEL_FLAGS, SYNC_LOGS


def _normalizar_numero_deduplicacao_venda(valor: Any) -> str:
    return re.sub(r"\s+", "", str(valor or "").strip())


def _normalizar_valor_deduplicacao_venda(valor: Any) -> str:
    try:
        return f"{float(valor):.8f}".rstrip("0").rstrip(".")
    except Exception:
        return str(valor or "").strip()


def _montar_chave_deduplicacao_venda(
    loja_conta: str,
    numero: str,
    sku: str,
    data: str,
    numero_nf: str = "",
    nota_fiscal_id: str = "",
    canal: str = "",
    comprador: str = "",
    quantidade: Any = 0,
    valor: Any = 0,
    id_pedido: str = "",
) -> tuple:
    return (
        _normalizar_numero_deduplicacao_venda(loja_conta).lower(),
        _normalizar_numero_deduplicacao_venda(id_pedido or numero_nf or numero).lower(),
        _normalizar_numero_deduplicacao_venda(sku).upper(),
        _normalizar_numero_deduplicacao_venda(data)[:10],
        _normalizar_numero_deduplicacao_venda(canal).lower(),
        _normalizar_numero_deduplicacao_venda(comprador).lower(),
        _normalizar_valor_deduplicacao_venda(quantidade),
        _normalizar_valor_deduplicacao_venda(valor),
        _normalizar_numero_deduplicacao_venda(nota_fiscal_id),
        _normalizar_numero_deduplicacao_venda(numero_nf),
    )


def _montar_id_unico_venda(
    loja_conta: str,
    id_pedido: str = "",
    numero_nf: str = "",
    numero: str = "",
    sku: str = "",
    quantidade: Any = 0,
    valor: Any = 0,
) -> str:
    referencia = str(id_pedido or numero_nf or numero or "sem_numero").strip()
    if not referencia:
        referencia = "sem_numero"
    referencia = _normalizar_numero_deduplicacao_venda(referencia)
    sku_normalizado = _normalizar_numero_deduplicacao_venda(sku).upper() or "sem_sku"
    quantidade_normalizada = _normalizar_valor_deduplicacao_venda(quantidade) or "0"
    valor_normalizado = _normalizar_valor_deduplicacao_venda(valor) or "0"
    loja = _normalizar_numero_deduplicacao_venda(loja_conta).replace("/", "_") or "sem_loja"
    return f"{loja}_{referencia}_{sku_normalizado}_{quantidade_normalizada}_{valor_normalizado}"


async def _sincronizar_vendas_periodo_impl(req: VendasSyncRequest, client_id: str, reset_estado: bool = True):
    if reset_estado:
        SYNC_CANCEL_FLAGS.pop(_sync_context_key(client_id), None)
        # Limpar logs anteriores para não acumular entre runs
        SYNC_LOGS[_sync_context_key(client_id)] = []
        _set_progresso(client_id, _criar_progresso("Preparando", 0, 0, 0, "Iniciando sincronização."))
        _sync_log(client_id, "[SYNC] Iniciando sincronização")
    if not req.loja or req.loja == "__todas":
        raise HTTPException(status_code=400, detail="Selecione uma loja para sincronizar.")
    if not req.data_inicio or not req.data_fim:
        raise HTTPException(status_code=400, detail="Informe data inicial e final.")

    loja = buscar_loja(client_id, req.loja)
    if not loja:
        raise HTTPException(status_code=404, detail="Loja não encontrada para o cliente.")

    bling_cfg = loja.get("integracoes", {}).get("bling")
    if not bling_cfg:
        raise HTTPException(status_code=400, detail="Loja não possui integração Bling conectada.")

    access_token = bling_cfg.get("access_token")
    cid = bling_cfg.get("id")
    sec = bling_cfg.get("secret")
    refresh_tok = bling_cfg.get("refresh_token")

    if not (access_token and cid and sec):
        raise HTTPException(status_code=400, detail="Credenciais Bling incompletas para esta loja.")

    _verificar_cancelamento(client_id)
    _set_progresso(client_id, _criar_progresso("Bling", 0, 0, 5, "Conectando na Bling e listando vendas..."))
    _sync_log(client_id, "[SYNC] Etapa 1: Listagem de Vendas (iniciando)")
    unidades_cache = {}
    unidades_mapeamento = _carregar_mapeamento_unidades()
    mapa_lojas_cliente = _carregar_mapeamento_lojas_virtuais_cliente(client_id)
    registros, status_api, bling_cfg = _bling_executar_com_refresh(
        client_id,
        req.loja,
        bling_cfg,
        lambda token: _bling_listar_vendas(token, req.data_inicio, req.data_fim, req.loja, client_id, unidades_cache, unidades_mapeamento, mapa_lojas_cliente),
        on_refresh=lambda: _set_progresso(client_id, _criar_progresso("Bling", 0, 0, 8, "Renovando token da Bling...")),
    )
    access_token = bling_cfg.get("access_token")
    refresh_tok = bling_cfg.get("refresh_token")
    if status_api == 401:
        unidades_cache = {}
        unidades_mapeamento = _carregar_mapeamento_unidades()
        mapa_lojas_cliente = _carregar_mapeamento_lojas_virtuais_cliente(client_id)
        registros, status_api = _bling_listar_vendas(access_token, req.data_inicio, req.data_fim, req.loja, client_id, unidades_cache, unidades_mapeamento, mapa_lojas_cliente)

    if status_api == 401:
        raise HTTPException(status_code=401, detail="Token Bling expirado. Refaça a conexão em Integrações.")

    # Fallback para vendas faturadas por NF-e de saída (inclui cenários Full/Fulfillment).
    _sync_log(client_id, "[SYNC] Etapa 1.5: Verificando NF-e de saída para complementar vendas...")
    registros_nf_saida, status_nf_saida = _bling_listar_vendas_fallback_nf_saida(
        access_token,
        req.data_inicio,
        req.data_fim,
        req.loja,
        client_id,
        mapa_lojas_cliente,
    )
    if status_nf_saida == 401:
        registros_nf_saida, status_nf_saida, bling_cfg = _bling_executar_com_refresh(
            client_id,
            req.loja,
            bling_cfg,
            lambda token: _bling_listar_vendas_fallback_nf_saida(
                token,
                req.data_inicio,
                req.data_fim,
                req.loja,
                client_id,
                mapa_lojas_cliente,
            ),
            on_refresh=lambda: _set_progresso(client_id, _criar_progresso("Bling", 0, 0, 12, "Renovando token da Bling para NF-e de saída...")),
        )
        access_token = bling_cfg.get("access_token")
        refresh_tok = bling_cfg.get("refresh_token")

    if status_nf_saida == 401:
        raise HTTPException(status_code=401, detail="Token Bling expirado ao consultar NF-e de saída. Refaça a conexão em Integrações.")
    if status_nf_saida == 403:
        _sync_log(
            client_id,
            "[SYNC] Etapa 1.5: NF-e de saída bloqueada pela Bling (403/FORBIDDEN). "
            "Continuando a sincronização apenas com os pedidos."
        )
        status_nf_saida = 200
        registros_nf_saida = []

    registros = registros or []
    registros_nf_saida = registros_nf_saida or []
    if registros_nf_saida:
        chaves_existentes = {
            (
                str(r.get("nota_fiscal_id") or "").strip(),
                str(r.get("sku") or "").strip().upper(),
                str(r.get("data") or "")[:10],
            )
            for r in registros
        }
        adicionados = 0
        for r in registros_nf_saida:
            chave = (
                str(r.get("nota_fiscal_id") or "").strip(),
                str(r.get("sku") or "").strip().upper(),
                str(r.get("data") or "")[:10],
            )
            if chave in chaves_existentes:
                continue
            registros.append(r)
            chaves_existentes.add(chave)
            adicionados += 1
        _sync_log(client_id, f"[SYNC] Etapa 1.5: ✅ {adicionados} itens de venda adicionados via NF-e de saída")
    else:
        _sync_log(client_id, "[SYNC] Etapa 1.5: sem itens adicionais via NF-e de saída")

    registros = registros or []
    _sync_log(client_id, f"[SYNC] Etapa 1: Listagem de Vendas - {len(registros)} registros encontrados (Status: {status_api})")
    unidades_identificadas = sorted({
        str(r.get("unidade_negocio") or "").strip()
        for r in registros
        if str(r.get("unidade_negocio") or "").strip()
    })
    if not unidades_identificadas and unidades_cache:
        unidades_identificadas = sorted({str(v or "").strip() for v in unidades_cache.values() if str(v or "").strip()})
    _sync_log(client_id, f"[SYNC] Unidades de negócio identificadas: {len(unidades_identificadas)} - {unidades_identificadas}")
    _set_progresso(client_id, _criar_progresso("Vendas", 0, 0, 35, f"{len(registros)} vendas encontradas. Processando lotes..."))

    # Salva em DB segregado por loja
    db_path = _get_vendas_db(client_id, req.loja)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()

        # Não rebatiza unidades: manter nomes oficiais da Bling.

        # Se forçar re-sincronização, deletar dados antigos do período
        if req.forcar_resync:
            _sync_log(client_id, f"[SYNC] Etapa 2: Deletando dados antigos do período {req.data_inicio} a {req.data_fim}...")
            cur.execute(
                "DELETE FROM vendas WHERE loja_conta = ? AND date(data) BETWEEN ? AND ?",
                (req.loja, req.data_inicio, req.data_fim)
            )
            conn.commit()
            _sync_log(client_id, f"[SYNC] ✅ Dados antigos removidos")

        # IDs únicos já existentes para esta loja — usados para evitar re-processamento
        # desnecessário de registros idênticos. A inserção usa INSERT OR REPLACE, então
        # registros com id_unico novo são sempre adicionados mesmo que a data já exista.
        existentes_ids = set()
        chaves_existentes_vendas = set()
        for row in cur.execute(
            """
            SELECT loja_conta, numero, sku, data, numero_nf, nota_fiscal_id, canal, comprador, quantidade, valor, id_unico
            FROM vendas
            WHERE loja_conta = ?
            """,
            (req.loja,),
        ).fetchall():
            id_unico_existente = str(row[10] or "").strip()
            if id_unico_existente:
                existentes_ids.add(id_unico_existente)
            chaves_existentes_vendas.add(
                _montar_chave_deduplicacao_venda(
                    loja_conta=row[0] or "",
                    numero=row[1] or "",
                    sku=row[2] or "",
                    data=str(row[3] or "")[:10],
                    numero_nf=row[4] or "",
                    nota_fiscal_id=row[5] or "",
                    canal=row[6] or "",
                    comprador=row[7] or "",
                    quantidade=row[8] or 0,
                    valor=row[9] or 0,
                )
            )
        hoje_str = datetime.now().date().isoformat()
        _sync_log(client_id, f"[SYNC] Etapa 2: Processamento de Vendas - {len(existentes_ids)} registros já existentes no banco")
        _set_progresso(client_id, _criar_progresso("Vendas", 0, 0, 40, "Preparando registros de vendas..."))

        payload = []
        total_registros = len(registros)
        lote_size = 100
        total_lotes = (total_registros + lote_size - 1) // lote_size
        lote_atual = 0
        chaves_processadas = set()

        for idx, r in enumerate(registros):
            if idx % lote_size == 0:
                _verificar_cancelamento(client_id)
            lote_atual = (idx // lote_size) + 1
            if idx % lote_size == 0 and idx > 0:
                percentual = int((idx / total_registros) * 100)
                _sync_log(client_id, f"[SYNC] Processando lote {lote_atual}/{total_lotes} - {idx}/{total_registros} registros ({percentual}%)")
                pct_stage = 40 + int((idx / max(total_registros, 1)) * 20)  # faixa 40% -> 60%
                _set_progresso(client_id, _criar_progresso("Vendas", lote_atual, total_lotes, min(60, pct_stage), f"Processando lote {lote_atual}/{total_lotes} - {idx}/{total_registros} registros"))

            data_str = r.get("data") or ""
            data_base = data_str[:10]
            try:
                dt_obj = datetime.fromisoformat(data_base)
                mes_ano = dt_obj.strftime("%Y-%m")
            except Exception:
                mes_ano = ""
            numero = r.get("numero") or ""
            sku = r.get("sku") or ""
            id_pedido = str(r.get("id_pedido") or "").strip()
            numero_nf = str(r.get("numero_nf") or "").strip()
            nota_fiscal_id = str(r.get("nota_fiscal_id") or "").strip()
            id_unico = _montar_id_unico_venda(
                loja_conta=req.loja,
                id_pedido=id_pedido,
                numero_nf=numero_nf,
                numero=numero,
                sku=sku,
                quantidade=r.get("quantidade", 0),
                valor=r.get("valor", 0),
            )
            chave_deduplicacao = _montar_chave_deduplicacao_venda(
                loja_conta=req.loja,
                numero=numero,
                sku=sku,
                data=data_base,
                numero_nf=numero_nf,
                nota_fiscal_id=nota_fiscal_id,
                canal=r.get("canal", ""),
                comprador=r.get("comprador", ""),
                quantidade=r.get("quantidade", 0),
                valor=r.get("valor", 0),
                id_pedido=id_pedido,
            )
            # Pula apenas se o registro já foi processado para a loja/ordem/sku.
            if data_base != hoje_str and (
                id_unico in existentes_ids
                or chave_deduplicacao in chaves_existentes_vendas
                or chave_deduplicacao in chaves_processadas
            ):
                continue
            chaves_processadas.add(chave_deduplicacao)
            payload.append((
                id_unico,
                data_str,
                r.get("loja_conta"),
                r.get("canal"),
                numero,
                r.get("situacao"),
                r.get("devolucao", 0),
                sku,
                r.get("produto"),
                r.get("quantidade", 0),
                r.get("valor", 0),
                mes_ano,
                r.get("numero_nf"),
                r.get("comprador"),
                r.get("unidade_negocio", ""),
                r.get("nota_fiscal_id", ""),
                r.get("loja_id", ""),
                r.get("unidade_id", ""),
                r.get("intermediador_nome", ""),
                r.get("intermediador_cnpj", "")
            ))

        _sync_log(client_id, f"[SYNC] Etapa 2.5: {len(payload)} registros prontos para inserir no banco")
        _set_progresso(client_id, _criar_progresso("Vendas", total_lotes or 1, total_lotes or 1, 60, "Salvando vendas no banco..."))

        nf_enriquecidas = 0
        nf_pendentes_restantes = 0

        if payload:
            cur.executemany(
                """
                INSERT OR REPLACE INTO vendas
                (id_unico, data, loja_conta, canal, numero, situacao, devolucao, sku, produto, quantidade, valor, mes_ano, numero_nf, comprador, unidade_negocio, nota_fiscal_id, loja_id, unidade_id, intermediador_nome, intermediador_cnpj)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload
            )
            conn.commit()
            _sync_log(client_id, f"[SYNC] Etapa 2.5: ✅ {len(payload)} registros salvos com sucesso")
            _set_progresso(client_id, _criar_progresso("Vendas", total_lotes or 1, total_lotes or 1, 65, "Vendas salvas. Iniciando notas fiscais..."))

            # Etapa crítica: preencher número de NF diretamente durante a sincronização.
            cur.execute(
                """
                SELECT DISTINCT nota_fiscal_id
                FROM vendas
                WHERE loja_conta = ?
                  AND date(data) BETWEEN ? AND ?
                  AND COALESCE(TRIM(nota_fiscal_id), '') != ''
                  AND COALESCE(TRIM(numero_nf), '') = ''
                LIMIT 4000
                """,
                (req.loja, req.data_inicio, req.data_fim)
            )
            nf_ids_pendentes = [str(r[0]).strip() for r in cur.fetchall() if r and str(r[0]).strip()]

            if nf_ids_pendentes:
                _sync_log(client_id, f"[SYNC] Etapa 2.6: enriquecendo número de NF para {len(nf_ids_pendentes)} IDs pendentes...")
                cache_nf = {}
                updates_nf = []
                total_nf_ids = len(nf_ids_pendentes)
                status_nf_stats = {}

                for idx_nf, nf_id in enumerate(nf_ids_pendentes, start=1):
                    if idx_nf == 1 or idx_nf % 50 == 0 or idx_nf == total_nf_ids:
                        pct_local = int((idx_nf / max(total_nf_ids, 1)) * 100)
                        pct_global = 65 + int(pct_local * 0.10)  # 65% -> 75%
                        _set_progresso(
                            client_id,
                            _criar_progresso(
                                "Vendas",
                                idx_nf,
                                total_nf_ids,
                                min(75, pct_global),
                                f"Preenchendo NF: {idx_nf}/{total_nf_ids}"
                            )
                        )

                    if idx_nf % 25 == 0:
                        _verificar_cancelamento(client_id)

                    if nf_id in cache_nf:
                        numero_nf = cache_nf[nf_id]
                        status_nf = 200 if numero_nf else 0
                    else:
                        numero_nf, status_nf, bling_cfg = _bling_executar_com_refresh(
                            client_id,
                            req.loja,
                            bling_cfg,
                            lambda token: _bling_obter_numero_nf(token, nf_id),
                            on_refresh=lambda: _sync_log(client_id, "[SYNC] Etapa 2.6: Renovando token Bling para preencher NF..."),
                        )
                        access_token = bling_cfg.get("access_token")
                        refresh_tok = bling_cfg.get("refresh_token")

                        if status_nf != 200:
                            numero_nf = ""

                        cache_nf[nf_id] = numero_nf

                    if not numero_nf:
                        status_nf_stats[status_nf] = status_nf_stats.get(status_nf, 0) + 1

                    if numero_nf:
                        updates_nf.append((numero_nf, req.loja, req.data_inicio, req.data_fim, nf_id))

                if updates_nf:
                    cur.executemany(
                        """
                        UPDATE vendas
                        SET numero_nf = ?
                        WHERE loja_conta = ?
                          AND date(data) BETWEEN ? AND ?
                          AND nota_fiscal_id = ?
                          AND COALESCE(TRIM(numero_nf), '') = ''
                        """,
                        updates_nf
                    )
                    conn.commit()
                    nf_enriquecidas = cur.rowcount if cur.rowcount and cur.rowcount > 0 else len(updates_nf)

                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM vendas
                    WHERE loja_conta = ?
                      AND date(data) BETWEEN ? AND ?
                      AND COALESCE(TRIM(nota_fiscal_id), '') != ''
                      AND COALESCE(TRIM(numero_nf), '') = ''
                    """,
                    (req.loja, req.data_inicio, req.data_fim)
                )
                nf_pendentes_restantes = int(cur.fetchone()[0] or 0)
                _sync_log(client_id, f"[SYNC] Etapa 2.6: ✅ NF enriquecidas={nf_enriquecidas} | pendentes_restantes={nf_pendentes_restantes}")
                if status_nf_stats:
                    _sync_log(client_id, f"[SYNC] Etapa 2.6: detalhe falhas NF por status={status_nf_stats}")
                    if status_nf_stats.get(404):
                        _sync_log(client_id, "[SYNC] Etapa 2.6: IDs de nota fiscal retornaram 404 na Bling (histórico não disponível por ID).")
            else:
                _sync_log(client_id, "[SYNC] Etapa 2.6: sem NF pendente para enriquecer no período.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao salvar vendas: {str(e)}")
    finally:
        conn.close()

    # Sincroniza Notas Fiscais de Entrada para identificar devoluções
    _sync_log(client_id, "[SYNC] Etapa 3: Sincronização de Notas Fiscais de Entrada")
    _set_progresso(client_id, _criar_progresso("Notas", 0, 0, 76, "Buscando notas fiscais de entrada..."))
    _verificar_cancelamento(client_id)
    notas_total = 0
    natureza_map, status_nat, bling_cfg = _bling_executar_com_refresh(
        client_id,
        req.loja,
        bling_cfg,
        _bling_listar_naturezas,
        on_refresh=lambda: _sync_log(client_id, "[SYNC] Etapa 3.2: Renovando token Bling para naturezas..."),
    )
    access_token = bling_cfg.get("access_token")
    refresh_tok = bling_cfg.get("refresh_token")
    _sync_log(client_id, f"[SYNC] Etapa 3.1: Naturezas carregadas - Status: {status_nat}")
    _set_progresso(client_id, _criar_progresso("Notas", 0, 0, 78, "Naturezas carregadas. Listando notas..."))
    if status_nat == 401:
        raise HTTPException(status_code=401, detail="Token Bling expirado. Refaça a conexão em Integrações.")

    _verificar_cancelamento(client_id)
    notas, notas_itens, status_nf = _bling_listar_notas_entrada(access_token, req.data_inicio, req.data_fim, natureza_map or {}, client_id)
    if status_nf == 401:
        _sync_log(client_id, "[SYNC] Etapa 3.3: Renovando token Bling para notas de entrada...")
        bling_cfg = _bling_renovar_token_loja(client_id, req.loja, bling_cfg)
        access_token = bling_cfg.get("access_token")
        refresh_tok = bling_cfg.get("refresh_token")
        notas, notas_itens, status_nf = _bling_listar_notas_entrada(access_token, req.data_inicio, req.data_fim, natureza_map or {}, client_id)
    if status_nf == 401:
        _bling_marcar_oauth_invalido(client_id, req.loja, bling_cfg, "Token Bling expirado. Refaça a conexão em Integrações.")
    notas = notas or []
    notas_itens = notas_itens or []
    _sync_log(client_id, f"[SYNC] Etapa 3.3: Notas fiscais listadas - {len(notas)} notas, {len(notas_itens)} itens (Status: {status_nf})")
    _set_progresso(client_id, _criar_progresso("Notas", 0, 0, 82, f"{len(notas)} notas e {len(notas_itens)} itens encontrados."))

    if status_nf == 401:
        raise HTTPException(status_code=401, detail="Token Bling expirado. Refaça a conexão em Integrações.")

    # Busca detalhes para notas sem itens e notas suspeitas no payload inicial
    # para classificar devolução corretamente (ex.: veio como compra e sem finalidade).
    if notas:
        ids_notas_com_itens = set(str(i.get("id_nota") or "").strip() for i in notas_itens if i.get("id_nota") is not None)
        def _nota_suspeita_para_detalhe(nota: dict) -> bool:
            if not isinstance(nota, dict):
                return False
            if int(nota.get("devolucao") or 0) == 1:
                return False

            natureza_norm = _normalizar_texto(nota.get("natureza_operacao") or "")
            finalidade_norm = _normalizar_texto(nota.get("finalidade_operacao") or "")
            origem_norm = str(nota.get("origem_codigo") or "").strip()
            unidade_norm = str(nota.get("unidade_negocio") or "").strip()

            # Casos comuns de falso negativo na listagem resumida do Bling.
            if not finalidade_norm and ("COMPRA" in natureza_norm or "COMERCIALIZ" in natureza_norm):
                return True
            if not finalidade_norm and not origem_norm:
                return True
            if not unidade_norm:
                return True
            return False

        notas_para_detalhar = []
        for n in notas:
            nid = str(n.get("id") or "").strip()
            sem_itens = nid not in ids_notas_com_itens
            suspeita = _nota_suspeita_para_detalhe(n)
            if sem_itens or suspeita:
                notas_para_detalhar.append(n)

        _sync_log(client_id, f"[SYNC] Etapa 3.4: Buscando detalhes de {len(notas_para_detalhar)} notas (sem itens + suspeitas)...")

        for idx, nota in enumerate(notas_para_detalhar):
            percentual = int(((idx + 1) / len(notas_para_detalhar)) * 100) if notas_para_detalhar else 100
            nid = nota.get('id')
            numero = nota.get('numero')
            _sync_log(client_id, f"[SYNC] Processando nota {idx + 1}/{len(notas_para_detalhar)} (NF {numero}) - {percentual}%")
            pct_notas = 82 + int(((idx + 1) / max(len(notas_para_detalhar), 1)) * 8)  # faixa 82% -> 90%
            _set_progresso(client_id, _criar_progresso("Notas", idx + 1, len(notas_para_detalhar), min(90, pct_notas), f"Processando nota {idx + 1}/{len(notas_para_detalhar)} (NF {numero})"))

            # Buscar detalhes da NF (com itens)
            nf_detalhe, status_detalhe, bling_cfg = _bling_executar_com_refresh(
                client_id,
                req.loja,
                bling_cfg,
                lambda token: _bling_obter_detalhes_nf(token, str(nid)),
                on_refresh=lambda: _sync_log(client_id, "[SYNC] Etapa 3.4: Renovando token Bling para detalhe de NF..."),
            )
            access_token = bling_cfg.get("access_token")
            refresh_tok = bling_cfg.get("refresh_token")

            if status_detalhe == 401:
                raise HTTPException(status_code=401, detail="Token Bling expirado. Refaça a conexão em Integrações.")
            elif status_detalhe == 429:
                _sync_log(client_id, f"[SYNC] ⚠️  Rate limit atingido ao buscar NF {numero}. Parando busca detalhes.")
                break
            elif status_detalhe != 200 or not nf_detalhe:
                _sync_log(client_id, f"[SYNC] ⚠️  Erro {status_detalhe} ao buscar detalhes de {numero}")
                continue

            natureza_det = nota.get("natureza_operacao")
            natureza_obj = nf_detalhe.get("naturezaOperacao") or {}
            if isinstance(natureza_obj, dict):
                natureza_det = natureza_obj.get("descricao") or natureza_obj.get("nome") or natureza_det

            finalidade_det = nota.get("finalidade_operacao") or ""
            finalidade_obj = nf_detalhe.get("finalidade") or nf_detalhe.get("finalidadeOperacao") or {}
            if isinstance(finalidade_obj, dict):
                finalidade_det = (
                    finalidade_obj.get("descricao")
                    or finalidade_obj.get("nome")
                    or finalidade_obj.get("label")
                    or finalidade_obj.get("valor")
                    or finalidade_det
                )
            elif finalidade_obj:
                finalidade_det = str(finalidade_obj)

            unidade_det = str(nota.get("unidade_negocio") or "")
            loja_desc_det = ""
            loja_id_det = ""
            unidade_id_det = ""
            loja_det = nf_detalhe.get("loja") or {}
            if isinstance(loja_det, dict):
                loja_id_det = str(loja_det.get("id") or "").strip()
                unidade_obj = loja_det.get("unidadeNegocio") or {}
                if isinstance(unidade_obj, dict):
                    unidade_id_det = str(unidade_obj.get("id") or "").strip()
                    unidade_det = str(unidade_obj.get("nome") or unidade_obj.get("descricao") or unidade_det).strip()
                loja_desc_det = str(loja_det.get("descricao") or "").strip()
                if not unidade_det:
                    unidade_det = loja_desc_det
            intermediador_det = nf_detalhe.get("intermediador") or {}
            intermediador_nome_det = ""
            intermediador_cnpj_det = ""
            if isinstance(intermediador_det, dict):
                intermediador_nome_det = _normalizar_nome_loja_virtual_candidato(intermediador_det.get("nomeUsuario"))
                intermediador_cnpj_det = str(intermediador_det.get("cnpj") or "").strip()
            unidade_resolvida_det = _resolver_nome_loja_virtual(
                mapa_lojas_cliente,
                loja_id=loja_id_det,
                unidade_id=unidade_id_det,
                nome_oficial=unidade_det or loja_desc_det,
                intermediador_nome=intermediador_nome_det,
                intermediador_cnpj=intermediador_cnpj_det,
                canal=loja_desc_det,
            )

            origem_det = _extrair_codigo_origem_nf(nf_detalhe) or nota.get("origem_codigo")
            devolucao_det = _eh_devolucao_nota_entrada(natureza_det, unidade_det, loja_desc_det, str(finalidade_det or ""))
            unidade_virtual_det = _normalizar_unidade_devolucao_entrada(unidade_resolvida_det or unidade_det, natureza_det, loja_desc_det)

            # Atualiza metadados da nota principal antes de persistir.
            nota["natureza_operacao"] = natureza_det
            nota["finalidade_operacao"] = str(finalidade_det or "")
            nota["origem_codigo"] = origem_det
            nota["devolucao"] = devolucao_det
            nota["unidade_negocio"] = unidade_det
            nota["unidade_negocio_virtual"] = unidade_virtual_det

            # Extrair itens do detalhe
            nota_itens_detalhe = nf_detalhe.get("itens", [])
            if _eh_devolucao_por_cfop_itens(nota_itens_detalhe):
                devolucao_det = 1
            tipo_especial_det = _tipo_devolucao_cfop_full_estoque(natureza_det, str(finalidade_det or ""), nota_itens_detalhe)
            if tipo_especial_det:
                unidade_virtual_det = tipo_especial_det

            # Reaplica metadados após avaliar CFOP/tipo especial.
            nota["devolucao"] = devolucao_det
            nota["unidade_negocio_virtual"] = unidade_virtual_det
            for item in nota_itens_detalhe:
                # SKU vem diretamente no campo "codigo" do item
                sku = item.get("codigo") or item.get("sku") or item.get("id")
                descricao = item.get("descricao") or item.get("nome")

                quantidade = float(item.get("quantidade") or 0)
                valor_unitario = float(item.get("valor") or 0)

                notas_itens.append({
                    "id_nota": nid,
                    "numero_nota": numero,
                    "data_emissao": nota.get("data_emissao"),
                    "sku": sku,
                    "descricao": descricao,
                    "quantidade": quantidade,
                    "valor_unitario": valor_unitario,
                    "valor_total": quantidade * valor_unitario if quantidade and valor_unitario else 0,
                    "natureza_operacao": natureza_det,
                    "finalidade_operacao": str(finalidade_det or ""),
                    "devolucao": devolucao_det,
                    "fornecedor": nota.get("fornecedor"),
                    "origem_codigo": origem_det,
                    "unidade_negocio": unidade_det,
                    "unidade_negocio_virtual": unidade_virtual_det,
                })

            # Delay para evitar rate limit (a cada 5 requisições, espera 1s)
            if (idx + 1) % 5 == 0:
                _sync_log(client_id, f"[SYNC] Processadas {idx + 1}/{len(notas_para_detalhar)} notas. Aguardando...")
                time.sleep(1)

        _sync_log(client_id, f"[SYNC] Etapa 3.4: ✅ Busca de detalhes concluída. Total de itens encontrados: {len(notas_itens)}")

    if notas:
        db_path_nf = _get_notas_entrada_db(client_id, req.loja)
        conn_nf = sqlite3.connect(db_path_nf)
        try:
            cur_nf = conn_nf.cursor()

            # Mantém os dados segregados por loja/período: limpa somente o recorte
            # da loja sincronizada antes de regravar as notas e itens.
            cur_nf.execute(
                """
                DELETE FROM notas_entrada
                WHERE loja_conta = ?
                  AND date(data_emissao) BETWEEN ? AND ?
                """,
                (req.loja, req.data_inicio, req.data_fim),
            )
            cur_nf.execute(
                """
                DELETE FROM notas_entrada_itens
                WHERE loja_conta = ?
                  AND date(data_emissao) BETWEEN ? AND ?
                """,
                (req.loja, req.data_inicio, req.data_fim),
            )
            conn_nf.commit()

            # Datas já existentes no banco (para não reinserir, exceto hoje)
            existentes_notas = set(
                row[0] for row in cur_nf.execute(
                    "SELECT DISTINCT date(data_emissao) FROM notas_entrada WHERE id_bling IN (SELECT id_bling FROM notas_entrada)"
                ).fetchall() if row[0]
            )
            hoje_str = datetime.now().date().isoformat()
            _sync_log(client_id, f"[SYNC] Etapa 3.5: Salvando notas - {len(existentes_notas)} datas já no banco")
            _set_progresso(client_id, _criar_progresso("Notas", 0, 0, 92, "Salvando notas no banco..."))

            payload_nf = []
            for n in notas:
                data_str = n.get("data_emissao") or ""
                data_base = data_str[:10]
                unidade_virtual_sync = n.get("unidade_negocio_virtual")
                if int(n.get("devolucao") or 0) == 1:
                    unidade_virtual_sync = _classificar_unidade_virtual_devolucao(
                        n.get("unidade_negocio_virtual"),
                        n.get("natureza_operacao"),
                        req.loja,
                    )

                id_bling = n.get("id")
                id_unico = f"{req.loja}_{id_bling}" if id_bling is not None else f"{req.loja}_{n.get('numero')}_{data_base}"
                payload_nf.append((
                    id_unico,
                    id_bling,
                    n.get("numero"),
                    data_str,
                    n.get("natureza_operacao"),
                    n.get("finalidade_operacao"),
                    n.get("devolucao", 0),
                    n.get("valor", 0),
                    n.get("fornecedor"),
                    n.get("origem_codigo"),
                    req.loja,
                    n.get("unidade_negocio"),
                    unidade_virtual_sync
                ))

            if payload_nf:
                cur_nf.executemany(
                    """
                    INSERT OR REPLACE INTO notas_entrada
                    (id_unico, id_bling, numero, data_emissao, natureza_operacao, finalidade_operacao, devolucao, valor, fornecedor, origem_codigo, loja_conta, unidade_negocio, unidade_negocio_virtual)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    payload_nf
                )
                conn_nf.commit()
                notas_total = len(payload_nf)
                _sync_log(client_id, f"[SYNC] Etapa 3.5: ✅ {len(payload_nf)} notas salvadas")

            # Salva itens das notas (também respeita a regra de não reinserir datas antigas)
            if notas_itens:
                payload_itens = []
                lote_size = 100
                total_itens = len(notas_itens)
                total_lotes_itens = (total_itens + lote_size - 1) // lote_size
                _sync_log(client_id, f"[SYNC] Etapa 3.6: Salvando {total_itens} itens de notas ({total_lotes_itens} lotes)")

                for idx, item in enumerate(notas_itens):
                    lote_atual = (idx // lote_size) + 1
                    if idx % lote_size == 0 and idx > 0:
                        percentual = int((idx / total_itens) * 100)
                        _sync_log(client_id, f"[SYNC] Salvando itens: lote {lote_atual}/{total_lotes_itens} - {idx}/{total_itens} ({percentual}%)")
                        pct_itens = 92 + int((idx / max(total_itens, 1)) * 6)  # faixa 92% -> 98%
                        _set_progresso(client_id, _criar_progresso("Itens NF", lote_atual, total_lotes_itens, min(98, pct_itens), f"Salvando itens: lote {lote_atual}/{total_lotes_itens} - {idx}/{total_itens}"))

                    data_str = item.get("data_emissao") or ""
                    data_base = data_str[:10]
                    unidade_virtual_item = item.get("unidade_negocio_virtual")
                    if int(item.get("devolucao") or 0) == 1:
                        unidade_virtual_item = _classificar_unidade_virtual_devolucao(
                            item.get("unidade_negocio_virtual"),
                            item.get("natureza_operacao"),
                            req.loja,
                        )

                    id_unico = f"{req.loja}_{item.get('id_nota')}_{item.get('sku')}"
                    payload_itens.append((
                        id_unico,
                        item.get("id_nota"),
                        item.get("numero_nota"),
                        item.get("origem_codigo"),
                        data_str,
                        item.get("sku"),
                        item.get("descricao"),
                        item.get("quantidade"),
                        item.get("valor_unitario"),
                        item.get("valor_total"),
                        item.get("natureza_operacao"),
                        item.get("finalidade_operacao"),
                        item.get("devolucao", 0),
                        item.get("fornecedor"),
                        req.loja,
                        item.get("unidade_negocio"),
                        unidade_virtual_item
                    ))

                if payload_itens:
                    cur_nf.executemany(
                        """
                        INSERT OR REPLACE INTO notas_entrada_itens
                        (id_unico, id_nota, numero_nota, origem_codigo, data_emissao, sku, descricao, quantidade, valor_unitario, valor_total, natureza_operacao, finalidade_operacao, devolucao, fornecedor, loja_conta, unidade_negocio, unidade_negocio_virtual)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        payload_itens
                    )
                    conn_nf.commit()
                    _sync_log(client_id, f"[SYNC] Etapa 3.6: ✅ {len(payload_itens)} itens salvos")
        finally:
            conn_nf.close()

    _sync_log(client_id, f"[SYNC] ✅ FINALIZADO: {len(payload)} vendas + {notas_total} notas = {len(payload) + notas_total} registros sincronizados com sucesso")
    prog_final = _criar_progresso("Finalizado", 1, 1, 100, "Sincronização finalizada com sucesso.")
    prog_final["concluido"] = True
    prog_final["result"] = {
        "total": len(payload),
        "notas_entrada_total": notas_total,
        "nf_enriquecidas": nf_enriquecidas,
        "nf_pendentes_restantes": nf_pendentes_restantes,
    }
    _set_progresso(client_id, prog_final)

    return {
        "success": True,
        "total": len(payload),
        "notas_entrada_total": notas_total,
        "nf_enriquecidas": nf_enriquecidas,
        "nf_pendentes_restantes": nf_pendentes_restantes,
        "vendas_processadas": len(payload),
        "notas_processadas": notas_total
    }



__all__ = [
    "_sincronizar_vendas_periodo_impl",
]
