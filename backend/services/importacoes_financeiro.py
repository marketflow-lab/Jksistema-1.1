"""Short-lived authenticated source reuse for revisioned import simulations.

Only provider documents are cached: never credentials or a calculated margin.
The item document contains every variation and the promotion document every
offer, so cost/price/variation selection remains local to each calculation.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
import json
import re

import requests
from fastapi import HTTPException

from backend.services.importacoes_consultas_cache import CacheConsultas
from backend.services.path_coordination import canonical_path_key


_CONTEXTO = ContextVar("importacoes_financeiro_contexto", default=None)
_ITENS_VERIFICADOS = ContextVar("importacoes_itens_financeiros", default=None)
_DOCUMENTOS = CacheConsultas(1024)


class ConsultaFinanceiraTemporaria(RuntimeError):
    """A provider failure must not replace a compatible saved analysis."""


@contextmanager
def contexto_financeiro(client_id, raiz):
    token = _CONTEXTO.set((str(client_id), canonical_path_key(raiz)))
    itens = _ITENS_VERIFICADOS.set({})
    try:
        yield
    finally:
        _ITENS_VERIFICADOS.reset(itens)
        _CONTEXTO.reset(token)


def escopo_financeiro():
    return _CONTEXTO.get()


def usar_itens_verificados(client, scope, itens, *, cache_scope=""):
    """Reuse the authenticated source snapshot without extending its 60s TTL."""
    current = _ITENS_VERIFICADOS.get()
    if current is None or not escopo_financeiro() or escopo_financeiro()[0] != str(client):
        return
    if cache_scope and canonical_path_key(cache_scope) != escopo_financeiro()[1]:
        raise HTTPException(409, "A origem dos anúncios mudou durante a análise.")
    identity = tuple(str(scope.get(key) or "") for key in ("store_id", "seller_id", "site_id"))
    for item_id, item in itens.items():
        current[(*identity, item_id)] = deepcopy(item)


def verificar_resposta_financeira(resposta):
    if escopo_financeiro():
        status = getattr(resposta, "status_code", 0)
        if status == 429 or 500 <= status <= 599:
            raise ConsultaFinanceiraTemporaria("O provedor financeiro está temporariamente indisponível.")


def propagar_falha_financeira(erro):
    if isinstance(erro, ConsultaFinanceiraTemporaria):
        raise erro
    if escopo_financeiro() and isinstance(erro, HTTPException) and erro.status_code in (401, 403, 409):
        raise erro
    if escopo_financeiro() and (
        isinstance(erro, (requests.RequestException, TimeoutError))
        or isinstance(erro, HTTPException) and (erro.status_code == 429 or erro.status_code >= 500)
    ):
        raise ConsultaFinanceiraTemporaria("A consulta financeira não foi concluída.") from None


def _identidade(cfg):
    return tuple(str(cfg.get(key) or "").strip() for key in ("_store_id_context", "user_id", "site_id"))


def _autorizar(deps, client, loja, esperado):
    store, seller, site = _identidade(esperado)
    rows = [row for row in deps["carregar_lojas"](client) or []
            if isinstance(row, dict) and str(row.get("store_id") or "") == store]
    if len(rows) != 1:
        raise HTTPException(409, "A autorização da loja mudou durante a análise.")
    row = rows[0]
    provider = (row.get("integracoes") or {}).get("mercadolivre") or {}
    if (row.get("nome") != loja or provider.get("connected") is False or provider.get("oauth_invalid") is True
            or "mercadolivre" in (row.get("_unavailable_providers") or [])
            or str(provider.get("user_id") or "") != seller
            or str(provider.get("site_id") or row.get("site_id") or "") != site):
        raise HTTPException(409, "A autorização da conta mudou durante a análise.")
    atual = deps["obter_cfg_ml"](client, loja, store_id=store)
    if not isinstance(atual, dict) or not atual.get("access_token") or _identidade(atual) != (store, seller, site):
        raise HTTPException(409, "A conexão da conta mudou durante a análise.")
    return dict(atual)


@dataclass
class _RespostaDocumento:
    documento: object
    status_code: int = 200

    def json(self):
        return deepcopy(self.documento)


class _RespostaSemCache(Exception):
    def __init__(self, resposta):
        self.resposta = resposta


def dependencias_com_cache(deps):
    """Wrap only item/promotion reads; fee/shipping retain their existing caches."""
    scope = escopo_financeiro()
    if not scope:
        return deps
    request = deps["ml_api_request"]

    def consultar(client, loja, cfg, method, url, **kwargs):
        matched = re.fullmatch(r"https://api\.mercadolibre\.com/(items|seller-promotions/items)/(MLB\d+)", url)
        if str(client) != scope[0]:
            raise HTTPException(409, "O cliente da consulta financeira mudou.")
        if method != "GET" or not matched:
            return request(client, loja, cfg, method, url, **kwargs)
        atual = _autorizar(deps, client, loja, cfg)
        identidade = _identidade(atual)
        capturado = (_ITENS_VERIFICADOS.get() or {}).get((*identidade, matched[2])) if matched[1] == "items" else None
        if isinstance(capturado, dict):
            return _RespostaDocumento(capturado), _autorizar(deps, client, loja, cfg)
        chave = (*scope, loja, *identidade, url,
                 json.dumps(kwargs.get("params") or {}, sort_keys=True, separators=(",", ":")))

        def carregar():
            try:
                resposta, retornada = request(client, loja, atual, method, url, **kwargs)
                verificar_resposta_financeira(resposta)
                if _identidade(retornada) != identidade or not retornada.get("access_token"):
                    raise HTTPException(409, "A identidade da conexão mudou durante a consulta financeira.")
                if resposta.status_code != 200:
                    raise _RespostaSemCache(resposta)
                dados = resposta.json()
                if matched[1] == "items" and (not isinstance(dados, dict) or dados.get("id") != matched[2]
                        or str(dados.get("seller_id") or "") != identidade[1]
                        or dados.get("site_id") != identidade[2]):
                    raise _RespostaSemCache(resposta)
                if matched[1] != "items":
                    ofertas = dados.get("results") if isinstance(dados, dict) else dados
                    if not isinstance(ofertas, list) or any(not isinstance(oferta, dict) for oferta in ofertas):
                        raise _RespostaSemCache(resposta)
                _autorizar(deps, client, loja, retornada)
                return deepcopy(dados)
            except Exception as erro:
                propagar_falha_financeira(erro)
                raise

        try:
            consulta = _DOCUMENTOS.consultar(chave, carregar)
            resposta = _RespostaDocumento(consulta.valor)
        except _RespostaSemCache as erro:
            resposta = erro.resposta
        atual = _autorizar(deps, client, loja, cfg)
        return resposta, atual

    def financeira(funcao):
        def executar(client, loja, cfg, *args, **kwargs):
            if str(client) != scope[0]:
                raise HTTPException(409, "O cliente da consulta financeira mudou.")
            atual = _autorizar(deps, client, loja, cfg)
            try:
                valor, retornada = funcao(client, loja, atual, *args, **kwargs)
            except Exception as erro:
                propagar_falha_financeira(erro)
                raise
            if _identidade(retornada) != _identidade(atual):
                raise HTTPException(409, "A identidade da conexão mudou durante a consulta financeira.")
            return valor, _autorizar(deps, client, loja, retornada)
        return executar

    return {**deps, "ml_api_request": consultar,
            "ml_obter_taxas_anuncio": financeira(deps["ml_obter_taxas_anuncio"]),
            "ml_obter_frete_detalhado": financeira(deps["ml_obter_frete_detalhado"])}
