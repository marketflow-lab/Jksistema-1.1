"""Isolated HTTP fixture for restarting the Vendas read server, never the application."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
fixture = Path(sys.argv[1]).resolve()
os.environ['JK_INFO_DIR'] = str(fixture / 'info')
os.environ['JK_APP_ROOT_DIR'] = str(ROOT)

from fastapi import FastAPI, Header, HTTPException
import uvicorn

from backend.core import AppPaths
from backend.modules.vendas import analytics, dependencies, performance, repository, sync_automation
from backend.modules.vendas.compositions import CompositionCatalog
from backend.modules.vendas.dependencies import LegacyBlingVendasAdapter, VendasModuleDependencies
from backend.modules.vendas.module import create_vendas_module


port = int(sys.argv[2])
mode = sys.argv[3]
logger = logging.getLogger('vendas-restart-fixture')
logger.addHandler(logging.NullHandler())
logger.propagate = False
paths = AppPaths.create(base_dir=ROOT, info_dir=fixture / 'info', logger=logger)


def tenant(x_cache_tenant: str = Header(default='')):
    if x_cache_tenant not in {'tenant-a', 'tenant-b'}:
        raise HTTPException(status_code=403, detail='Synthetic tenant denied')
    return x_cache_tenant


# Test startup must never launch synchronization or any real worker.
sync_automation.start = lambda: None
sync_automation.stop = lambda: None
module = create_vendas_module(VendasModuleDependencies(
    get_tenant_id=tenant, paths=paths, logger=logger, legacy=LegacyBlingVendasAdapter(),
))


def database(client_id, _store=None):
    return str(Path(paths.tenant_path(client_id)) / 'vendas_historico_fixture.db')


def catalog(_client):
    return CompositionCatalog.from_products(
        [{'store_id': 'store-a', 'sku': 'SKU-001', 'produto_bling': 'Synthetic product'}],
        stores=[{'store_id': 'store-a', 'nome': 'Loja A'}],
    )


reads = {'resumo': 0, 'grafico': 0}


def instrument(kind, original):
    def reader(*args, **kwargs):
        reads[kind] += 1
        if mode == 'cached-only':
            raise RuntimeError('Synthetic SQL read forbidden after restart')
        return original(*args, **kwargs)
    return reader


for target in (repository, analytics):
    target._listar_bancos_vendas_tenant = lambda client_id, store=None: [database(client_id, store)]
    target._get_vendas_db_path = database
    target.load_composition_catalog = catalog
    target._carregar_mapeamento_lojas_virtuais_cliente = lambda _client: {}
    target._resolver_nome_loja_virtual = lambda *_args, **_kwargs: 'Canal A'
repository.open_vendas_readonly = instrument('resumo', repository.open_vendas_readonly)
analytics.open_vendas_readonly = instrument('grafico', analytics.open_vendas_readonly)
analytics._vendas_series_estoque_historico = lambda **_kwargs: {
    'estoque_geral': [], 'estoque_sku': [], 'estoque_skus_com_saldo': [],
    'estoque_skus_pareto_com_saldo': [], 'estoque_meta': {'success': True, 'requested': False},
}

app = FastAPI()
app.include_router(module.router)


@app.get('/__test_metrics')
def metrics():
    return {'reads': reads}


if mode == 'invalidate':
    performance.invalidate_vendas_cache()
    sys.exit(0)

uvicorn.run(app, host='127.0.0.1', port=port, log_level='critical', access_log=False)
