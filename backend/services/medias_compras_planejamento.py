"""Read-only purchase inputs shared by the dashboard and order generation."""

from __future__ import annotations

import calendar
import math
from datetime import date, datetime, timedelta

from backend.services import medias_compras_common as common


_STATUS_CONFIRMADOS = {
    "Pedido Aprovado", "Pedido emitido", "Em produção", "Em trânsito", "Em desembaraço",
}


def _numero(valor):
    if isinstance(valor, bool):
        return 0.0
    if isinstance(valor, str):
        valor = valor.strip()
        if "," in valor:
            valor = valor.replace(".", "").replace(",", ".")
    try:
        numero = float(valor or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return max(numero, 0.0) if math.isfinite(numero) else 0.0


def _data(valor):
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = str(valor or "").strip()
    try:
        return date.fromisoformat(texto[:10])
    except ValueError:
        try:
            return datetime.strptime(texto[:10], "%d/%m/%Y").date()
        except ValueError:
            return None


def _somar_meses(valor, meses):
    ordinal = valor.year * 12 + valor.month - 1 + meses
    ano, mes = divmod(ordinal, 12)
    return date(ano, mes + 1, min(valor.day, calendar.monthrange(ano, mes + 1)[1]))


def _previsao_lista(lista, referencia):
    entrega = _data(lista.get("promised_delivery_date"))
    estimada = False
    if entrega is None:
        entrega = _data(lista.get("eta_date"))
        estimada = entrega is not None
    if entrega is None:
        pedido = _data(lista.get("order_date"))
        if pedido is not None:
            prazo = int(_numero(lista.get("lead_time_days")))
            try:
                entrega = pedido + timedelta(days=prazo) if prazo else _somar_meses(pedido, 6)
            except (OverflowError, ValueError):
                return "", True
            estimada = True
    # An overdue or missing date is not evidence that goods are available now.
    if entrega is None or entrega < referencia:
        return "", True
    return entrega.isoformat(), estimada


def entradas_confirmadas_por_sku(listas, *, loja="__todas", store_id="", data_referencia):
    """Use one tenant-owned list snapshot; quotations never count as receipts.

    Durable store identities take precedence over legacy display names. Missing
    dates remain explicit so the calculator cannot hide a stockout with them.
    """
    referencia = _data(data_referencia)
    if referencia is None:
        raise ValueError("Data de referência inválida.")
    loja_normalizada = str(loja or "__todas").strip().casefold()
    identidade = str(store_id or "").strip()
    mapa = {}
    for lista in listas:
        if not isinstance(lista, dict) or lista.get("unida_em_lista_id"):
            continue
        if common._normalizar_status_lista_pedido(lista.get("status")) not in _STATUS_CONFIRMADOS:
            continue
        if loja_normalizada != "__todas":
            identidade_lista = str(lista.get("store_id") or "").strip()
            if identidade and identidade_lista:
                if identidade_lista != identidade:
                    continue
            elif str(lista.get("loja") or "").strip().casefold() != loja_normalizada:
                continue
        data_prevista, data_estimada = _previsao_lista(lista, referencia)
        for item in lista.get("itens") or []:
            if not isinstance(item, dict):
                continue
            sku = common._normalizar_sku_mes(str(item.get("SKU") or "").strip())
            if not sku:
                continue
            recebida = next((item[chave] for chave in (
                "received_quantity", "Quantidade recebida", "Qtd recebida",
            ) if chave in item), 0)
            quantidade = max(0.0, _numero(item.get("Quantidade")) - _numero(recebida))
            if quantidade <= 0:
                continue
            mapa.setdefault(sku, []).append({
                "quantidade": round(quantidade, 6),
                "data_prevista": data_prevista,
                "data_estimada": data_estimada,
            })
    return mapa


def campos_planejamento(resultado):
    """Keep existing response fields intact and expose only additive forecasts."""
    nomes = (
        "planejamento_versao", "media_mensal_prevista", "tendencia_vendas",
        "variacao_vendas_percentual", "tendencia_motivo", "meses_historico",
        "data_chegada_prevista", "meses_estoque_apos_chegada",
        "reserva_antes_chegada_quantidade", "estoque_previsto_antes_chegada",
        "estoque_previsto_apos_chegada", "estoque_alvo_apos_chegada",
        "falta_antes_chegada_quantidade", "reserva_antes_chegada_atendida",
        "risco_ruptura_antes_chegada", "avisos_planejamento", "transito_confirmado",
        "planejamento_requer_revisao", "transito_sem_previsao_quantidade",
    )
    return {nome: resultado[nome] for nome in nomes if nome in resultado}


def filtrar_estoque_loja(quadro, loja, store_id=""):
    """Prefer canonical store rows; legacy names never override another ID."""
    if str(loja or "__todas").strip().casefold() == "__todas":
        return quadro
    colunas = {str(coluna).strip().casefold(): coluna for coluna in quadro.columns}
    coluna_loja = colunas.get("loja_sync")
    coluna_identidade = colunas.get("store_id")
    identidade = str(store_id or "").strip()
    if coluna_identidade is not None and identidade:
        identidades = quadro[coluna_identidade].astype(str).str.strip()
        exatas = quadro[identidades == identidade]
        if coluna_loja is None:
            return exatas.copy()
        legadas = quadro[(identidades == "") & (
            quadro[coluna_loja].astype(str).str.strip().str.casefold() == str(loja).strip().casefold()
        )]
        coluna_sku = colunas.get("sku")
        if coluna_sku is not None and not exatas.empty:
            canonicos = set(exatas[coluna_sku].astype(str).map(common._normalizar_sku_mes))
            legadas = legadas[~legadas[coluna_sku].astype(str).map(common._normalizar_sku_mes).isin(canonicos)]
        return quadro.loc[quadro.index.isin(exatas.index) | quadro.index.isin(legadas.index)].copy()
    if coluna_loja is not None:
        return quadro[
            quadro[coluna_loja].astype(str).str.strip().str.casefold() == str(loja).strip().casefold()
        ].copy()
    return quadro
