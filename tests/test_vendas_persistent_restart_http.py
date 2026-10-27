"""Real process restarts exercise disk cache through the unchanged Vendas routes."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / 'tests' / 'helpers' / 'vendas_cache_server.py'
SUMMARY = '/api/vendas/resumo?data_inicio=2026-10-01&data_fim=2026-10-31'
GRAPH = '/api/vendas/grafico?periodo=3m&intervalo=dia&data_inicio=2026-10-01&data_fim=2026-10-31'


def _fixtures(root):
    for client, amount in [('tenant-a', 50), ('tenant-b', 900)]:
        tenant = root / 'info' / client
        tenant.mkdir(parents=True)
        (tenant / 'lojas_config.json').write_text(json.dumps([{'store_id': 'store-a', 'nome': 'Loja A'}]), encoding='utf-8')
        with sqlite3.connect(tenant / 'vendas_historico_fixture.db') as db:
            db.executescript('''
                CREATE TABLE vendas (
                    id_unico TEXT PRIMARY KEY, data TEXT, loja_conta TEXT, store_id TEXT,
                    sku TEXT, produto TEXT, quantidade REAL, valor REAL, devolucao INTEGER,
                    canal TEXT, numero TEXT, situacao TEXT, numero_nf TEXT, nota_fiscal_id TEXT,
                    comprador TEXT, unidade_negocio TEXT, loja_id TEXT, unidade_id TEXT,
                    intermediador_nome TEXT, intermediador_cnpj TEXT
                );
                CREATE TABLE notas_entrada_itens (
                    id_unico TEXT PRIMARY KEY, data_emissao TEXT, loja_conta TEXT, store_id TEXT,
                    sku TEXT, descricao TEXT, quantidade REAL, valor_total REAL, devolucao INTEGER,
                    numero_nota TEXT, origem_codigo TEXT, valor_unitario REAL, fornecedor TEXT,
                    unidade_negocio TEXT, unidade_negocio_virtual TEXT, natureza_operacao TEXT,
                    finalidade_operacao TEXT
                );
            ''')
            db.execute('INSERT INTO vendas VALUES (' + ','.join('?' * 20) + ')', (
                'sale-1', '2026-10-01', 'Loja A', 'store-a', 'SKU-001', 'Synthetic product',
                2, amount, 0, 'Loja virtual', 'order-1', 'Faturado', '', '', '', 'Canal A', '', '', '', '',
            ))
            db.execute('INSERT INTO notas_entrada_itens VALUES (' + ','.join('?' * 17) + ')', (
                'return-1', '2026-10-02', 'Loja A', 'store-a', 'SKU-001', 'Synthetic return', 1,
                10, 1, 'return-1', 'return-1', 10, '', 'Canal A', '', 'Devolucao', '',
            ))


def _get(base, route, client='tenant-a'):
    request = Request(base + route, headers={'X-Cache-Tenant': client})
    try:
        with urlopen(request, timeout=4) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


def _environment():
    env = dict(os.environ)
    for name in ('JK_APP_ROOT_DIR', 'JK_LOCAL_BACKEND_DIR', 'JK_LOCAL_BACKEND_SOURCE_DIR', 'JK_INFO_DIR'):
        env.pop(name, None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return env


@contextmanager
def _server(root, mode='live'):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, '-B', str(SERVER), str(root), str(port), mode], cwd=root,
        env=_environment(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    base = f'http://127.0.0.1:{port}'
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError('Isolated server failed: ' + process.stderr.read().decode(errors='replace')[-1200:])
            try:
                if _get(base, '/__test_metrics')[0] == 200:
                    break
            except (URLError, TimeoutError, ConnectionError):
                time.sleep(0.05)
        else:
            raise AssertionError('Isolated server did not start')
        yield base
    finally:
        process.terminate()
        process.wait(timeout=8)
        process.stderr.close()


def test_summary_and_chart_survive_a_real_http_server_process_restart(tmp_path):
    _fixtures(tmp_path)
    with _server(tmp_path) as base:
        summary_status, expected_summary = _get(base, SUMMARY)
        chart_status, expected_chart = _get(base, GRAPH)
        assert (summary_status, chart_status) == (200, 200)
        assert expected_summary['totals']['valor'] == 50
        assert expected_summary['totals']['valorDevolucao'] == 10
        assert sum(expected_chart['valores_vendas']) == 50
        assert _get(base, '/__test_metrics')[1]['reads']['resumo'] > 0
        assert _get(base, '/__test_metrics')[1]['reads']['grafico'] > 0

    # A brand-new interpreter serves the same payload even when SQL would fail.
    with _server(tmp_path, 'cached-only') as base:
        assert _get(base, SUMMARY) == (200, expected_summary)
        assert _get(base, GRAPH) == (200, expected_chart)
        assert _get(base, '/__test_metrics')[1]['reads'] == {'resumo': 0, 'grafico': 0}
        assert _get(base, SUMMARY, client='')[0] == 403
        status, other_client = _get(base, SUMMARY, client='tenant-b')
        assert status == 200 and other_client['totals']['valor'] == 0
        assert _get(base, '/__test_metrics')[1]['reads']['resumo'] > 0


def test_changed_database_and_global_invalidation_cannot_resurrect_an_old_response(tmp_path):
    _fixtures(tmp_path)
    with _server(tmp_path) as base:
        assert _get(base, SUMMARY)[1]['totals']['valor'] == 50
    db_path = tmp_path / 'info' / 'tenant-a' / 'vendas_historico_fixture.db'
    with sqlite3.connect(db_path) as db:
        db.execute('UPDATE vendas SET valor=75')
    with _server(tmp_path) as base:
        assert _get(base, SUMMARY)[1]['totals']['valor'] == 75
        assert _get(base, '/__test_metrics')[1]['reads']['resumo'] > 0

    completed = subprocess.run(
        [sys.executable, '-B', str(SERVER), str(tmp_path), '0', 'invalidate'],
        cwd=tmp_path, env=_environment(), capture_output=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors='replace')[-1200:]
    with _server(tmp_path, 'cached-only') as base:
        _get(base, SUMMARY)
        assert _get(base, '/__test_metrics')[1]['reads']['resumo'] > 0
