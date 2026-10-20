"""Read-only sales projections reused across the three Medias periods.

The database inventory and store SQL come from the existing sales adapters.
Nothing here decides which database, store alias or SKU belongs to a tenant.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, RLock
import copy
import os
import sqlite3

from backend.services.medias_compras_fontes import assinatura_arquivo
from backend.services.medias_compras_leitura import chave_arquivo, registrar_contagem


class _MemoLimitado:
    """Bound retained memory and coalesce concurrent builds of the same key."""

    def __init__(self, max_bytes, max_entries):
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.bytes = 0
        self.entries = OrderedDict()
        self.lock = RLock()
        # Fixed stripes avoid retaining an unbounded collection of key locks.
        self.stripes = tuple(Lock() for _ in range(32))

    def obter(self, key, revision, construir, validar):
        with self.stripes[hash(key) % len(self.stripes)]:
            with self.lock:
                previous = self.entries.get(key)
                if previous is not None and previous[0] == revision:
                    self.entries.move_to_end(key)
                    registrar_contagem("historico_cache_hits")
                    return previous[1]
            value, weight = construir()
            if not validar():
                return value
            with self.lock:
                previous = self.entries.pop(key, None)
                if previous is not None:
                    self.bytes -= previous[2]
                if weight <= self.max_bytes and self.max_entries > 0:
                    self.entries[key] = (revision, value, weight)
                    self.bytes += weight
                while self.entries and (self.bytes > self.max_bytes or len(self.entries) > self.max_entries):
                    _, removed = self.entries.popitem(last=False)
                    self.bytes -= removed[2]
            return value


_PROJECOES = _MemoLimitado(96 * 1024 * 1024, 64)
_CONSOLIDADOS = _MemoLimitado(24 * 1024 * 1024, 24)


@dataclass(frozen=True)
class _Projecao:
    vendas: tuple
    titulos: dict
    ultimas: dict


def _revisao_banco(path):
    # WAL commits and rollback journal lifecycles participate in invalidation.
    return tuple(assinatura_arquivo(str(path) + suffix) for suffix in ("", "-wal", "-journal"))


def _meses_referencia(ultimo_mes, quantidade):
    ano, mes = map(int, ultimo_mes.split("-"))
    ordinal = ano * 12 + mes - 1
    return tuple(f"{(ordinal - offset) // 12:04d}-{(ordinal - offset) % 12 + 1:02d}"
                 for offset in range(quantidade - 1, -1, -1))


def _peso_textos(*values):
    # Conservative allowance for Python object headers and wide Unicode text.
    return sum(64 + len(str(value)) * 4 for value in values)


def _ordem_sqlite(value):
    if value is None:
        return (0, "")
    if isinstance(value, (int, float)):
        return (1, value)
    return (3 if isinstance(value, bytes) else 2, value)


def _ler_projecao(path, inicio, fim, filtro_sql, filtro_params, normalizar_sku, titulo_valido):
    recentes = []
    titulos = {}
    ultimas = {}
    ordens_titulos = {}
    skus_normalizados = {}
    titulos_validados = {}
    weight = 256
    query = """
        SELECT id_unico, sku, data, COALESCE(produto, ''),
               date(data), strftime('%Y-%m', date(data)), COALESCE(quantidade, 0)
        FROM vendas
        WHERE sku IS NOT NULL AND trim(sku) != ''
    """ + filtro_sql
    # Opening a GET must not create a missing database or an index.
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        registrar_contagem("historico_bancos_lidos")
        for identificador, sku, raw_date, produto, data_iso, mes, quantidade in conn.execute(query, filtro_params):
            registrar_contagem("historico_linhas_lidas")
            sku_original = str(sku or "").strip()
            if not sku_original:
                continue
            sku_norm = skus_normalizados.get(sku_original)
            if sku_norm is None:
                sku_norm = normalizar_sku(sku_original)
                if len(skus_normalizados) < 8192:
                    skus_normalizados[sku_original] = sku_norm
            data_texto = str(raw_date or "").strip()
            if produto in titulos_validados:
                titulo = titulos_validados[produto]
            else:
                titulo = titulo_valido(produto)
                if len(titulos_validados) < 8192:
                    titulos_validados[produto] = titulo
            if titulo:
                anterior = titulos.get(sku_norm)
                # The old title pass sorted date(data) DESC, data DESC, then
                # replaced ties using >= on the stripped date text. Retain
                # its last candidate even for whitespace/invalid date ties.
                ordem = (data_iso or "", _ordem_sqlite(raw_date))
                if (anterior is None or data_texto > anterior[1] or
                        (data_texto == anterior[1] and ordem <= ordens_titulos[sku_norm])):
                    titulos[sku_norm] = (titulo, data_texto)
                    ordens_titulos[sku_norm] = ordem
            if data_iso and (sku_norm not in ultimas or data_iso > ultimas[sku_norm]):
                ultimas[sku_norm] = data_iso
            if data_iso and inicio <= data_iso <= fim:
                # Keep original row order and raw SKU: both affect the legacy
                # id_unico/fallback deduplication and floating-point sums.
                identifier = str(identificador or "").strip()
                qty = float(quantidade or 0)
                recentes.append((identifier, sku_original, sku_norm, mes, qty))
                weight += 112 + _peso_textos(identifier, sku_original, sku_norm, mes)
    finally:
        conn.close()
    weight += sum(96 + _peso_textos(sku, *value) for sku, value in titulos.items())
    weight += sum(72 + _peso_textos(sku, date) for sku, date in ultimas.items())
    return _Projecao(tuple(recentes), titulos, ultimas), weight


def _consolidar(paths, projecoes, ultimo_mes):
    refs = {periodo: _meses_referencia(ultimo_mes, periodo) for periodo in (3, 6, 12)}
    conjuntos = {periodo: set(meses) for periodo, meses in refs.items()}
    vendas = {periodo: {} for periodo in refs}
    vistos = {periodo: set() for periodo in refs}
    titulos = {}
    ultimas = {}
    for path, projecao in zip(paths, projecoes):
        basename = os.path.basename(path)
        for identificador, sku_original, sku_norm, mes, qty in projecao.vendas:
            chave = identificador or f"{basename}::{sku_original}::{mes}::{qty}"
            for periodo, meses in refs.items():
                if mes not in conjuntos[periodo] or chave in vistos[periodo]:
                    continue
                vistos[periodo].add(chave)
                item = vendas[periodo].get(sku_norm)
                if item is None:
                    item = vendas[periodo][sku_norm] = {"sku": sku_norm, "vendas_mensais": dict.fromkeys(meses, 0.0)}
                item["vendas_mensais"][mes] += qty
        for sku, (titulo, data) in projecao.titulos.items():
            atual = titulos.get(sku)
            if atual is None or data >= atual["data"]:
                titulos[sku] = {"titulo": titulo, "data": data}
        for sku, data in projecao.ultimas.items():
            if sku not in ultimas or data > ultimas[sku]:
                ultimas[sku] = data
    weight = sum(400 + _peso_textos(sku) + len(item["vendas_mensais"]) * 112
                 for period in vendas.values() for sku, item in period.items())
    weight += sum(160 + _peso_textos(sku, *value.values()) for sku, value in titulos.items())
    weight += sum(72 + _peso_textos(sku, date) for sku, date in ultimas.items())
    return (vendas, titulos, ultimas), weight


def ler_historico_vendas(*, client_id, loja, store_id, db_paths, meses, fim_periodo,
                        filtro_sql, filtro_params, normalizar_sku, titulo_valido,
                        normalizacao_token=None):
    """Return independent result maps while keeping immutable projections private."""
    paths = tuple(str(path) for path in db_paths if path and os.path.exists(path))
    revisoes = tuple(_revisao_banco(path) for path in paths)
    refs = _meses_referencia(fim_periodo[:7], 12)
    inicio = refs[0] + "-01"
    params = tuple(filtro_params)
    scope = (str(client_id), str(store_id), str(loja), filtro_sql, params,
             normalizar_sku, normalizacao_token or titulo_valido)
    key = (scope, tuple(chave_arquivo(path) for path in paths), fim_periodo)

    def construir():
        projecoes = []
        for path, revision in zip(paths, revisoes):
            projection_key = (scope, chave_arquivo(path), fim_periodo)
            projecoes.append(_PROJECOES.obter(
                projection_key, revision,
                lambda path=path: _ler_projecao(path, inicio, fim_periodo, filtro_sql, params, normalizar_sku, titulo_valido),
                lambda path=path, revision=revision: _revisao_banco(path) == revision,
            ))
        return _consolidar(paths, projecoes, fim_periodo[:7])

    vendas, titulos, ultimas = _CONSOLIDADOS.obter(
        key, revisoes, construir,
        lambda: tuple(_revisao_banco(path) for path in paths) == revisoes,
    )
    return copy.deepcopy(vendas[meses]), copy.deepcopy(titulos), dict(ultimas)
