"""Prepare closed sales months locally, independently of dashboard requests."""

from __future__ import annotations

from collections import defaultdict
import logging
import threading
import time

from .dependencies import get_tenant_path
from .legacy import _listar_bancos_vendas_tenant
from .monthly_coverage import meses_confirmados


_LOG = logging.getLogger("jk_sistema")
_LOCK = threading.Lock()
_thread = None
_next_check = 0.0


def preparar_cliente(client_id, stopped):
    from backend.services.medias_compras_fechamento import fechamentos_disponiveis, preparar_fechamentos
    from backend.services.store_listing_service import read_store_cards

    por_loja = defaultdict(dict)
    for (store_id, mes), versao in meses_confirmados(get_tenant_path(client_id), client_id).items():
        por_loja[store_id][mes] = versao
    if not por_loja:
        return
    for store in read_store_cards(client_id)["lojas"]:
        if stopped.is_set():
            return
        store_id, loja = store["store_id"], store["nome"]
        meses = por_loja.get(store_id)
        if not meses:
            continue
        try:
            fontes = _listar_bancos_vendas_tenant(client_id, loja)
            if not fontes:
                continue
            args = dict(client_id=client_id, loja=loja, store_id=store_id, db_paths=fontes)
            prontos = fechamentos_disponiveis(**args, meses=meses)
            pendentes = {mes: versao for mes, versao in meses.items() if mes not in prontos}
            if pendentes and not stopped.is_set():
                preparar_fechamentos(**args, meses=pendentes)
        except Exception:
            # One unavailable store must not delay closing months for the rest.
            _LOG.warning("Fechamento mensal de vendas pendente para uma loja.")


def _processar(tenants, stopped):
    global _next_check
    try:
        for client_id in tenants():
            if stopped.is_set():
                break
            try:
                preparar_cliente(client_id, stopped)
            except Exception:
                _LOG.warning("Fechamento mensal de vendas pendente para um cliente.")
    finally:
        with _LOCK:
            _next_check = time.monotonic() + 60


def dispatch(tenants, stopped):
    """Reconcile durable coverage on startup and after new daily collections."""
    global _thread
    with _LOCK:
        if stopped.is_set() or time.monotonic() < _next_check or (_thread and _thread.is_alive()):
            return
        _thread = threading.Thread(target=_processar, args=(tenants, stopped), name="vendas-meses", daemon=True)
        _thread.start()
