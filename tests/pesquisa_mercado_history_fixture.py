"""Loopback fixture for real SQLite/router + browser integration; never backend_api."""
from pathlib import Path
import re
import sys
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from backend.routers.pesquisa_mercado import create_pesquisa_mercado_router
from backend.services import favoritos_endpoints, pesquisa_mercado
from backend.services.pesquisa_mercado import PesquisaMercadoStore

root = Path(__file__).resolve().parents[1]
fixture_root = Path(sys.argv[2]).resolve()
assert fixture_root.name.startswith('jk-pm-history-e2e-')
store = PesquisaMercadoStore(fixture_root / 'info')
pesquisa_mercado._STORE = store

def seller(number):
    return {'key': 'id:' + number, 'id': number, 'nome': 'Vendedor Exemplo ' + number,
            'perfilUrl': 'https://lista.mercadolivre.com.br/_CustId_' + number, 'categoria': 'Peças de Carros e Caminhonetes'}

def ad(number, title, monthly, sales, owner='123', pending=False):
    return {'id': 'MLB' + str(number), 'url': 'https://produto.mercadolivre.com.br/MLB-' + str(number) + '-produto-_JM',
            'titulo': title, 'vendedor': seller(owner)['nome'], 'seller_id': owner, 'preco': 99,
            'vendas': sales, 'vendasFonte': 'page_fallback' if pending else 'avantpro_anuncio',
            'media_vendas_mensal': monthly, 'data_criacao': '2026-01-01'}

category = {'key': 'MLB22693', 'nome': 'Peças de Carros e Caminhonetes', 'url': 'https://lista.mercadolivre.com.br/_CatId_MLB22693'}
old = store.start('fixture-client', 'alice', category=category)
store.publish(old['id'], 'id:123', 'fixture-client', 'alice', seller('123'), [
    ad(100000001, 'Bomba de combustível', 10, 100), ad(100000002, 'Sensor de temperatura', 40, 400)])
store.checkpoint(old['id'], 'fixture-client', 'alice', status='completed')
current = store.start('fixture-client', 'alice', category={**category, 'key': 'MLB100', 'nome': 'Sensores de temperatura', 'url': 'https://lista.mercadolivre.com.br/_CatId_MLB100'})
store.publish(current['id'], 'id:123', 'fixture-client', 'alice', seller('123'), [
    ad(100000001, 'Bomba de combustível', 20, 200), ad(100000002, 'Sensor de temperatura', None, None, pending=True)])
store.publish(current['id'], 'id:456', 'fixture-client', 'alice', seller('456'), [
    ad(100000003, 'Filtro de óleo', 90, 900, '456')])

async def auth(request, authorization=None):
    if authorization != 'Bearer fixture-alice':
        raise HTTPException(401, 'Fixture session required')
    request.state.username = 'alice'
    return 'fixture-client'

favoritos_endpoints._TENANT_DEPENDENCY = auth
app = FastAPI()
app.include_router(create_pesquisa_mercado_router())
app.mount('/pesquisa-mercado', StaticFiles(directory=root / 'static/pesquisa-mercado'), name='pm-assets')

@app.get('/pesquisa_mercado.html', response_class=HTMLResponse)
def page():
    html = (root / 'pesquisa_mercado.html').read_text(encoding='utf-8')
    html = re.sub(r'<script\b[^>]*>[\s\S]*?</script>', '', html, flags=re.I)
    return html.replace('</body>', '<script src="/pesquisa-mercado/global-ui.js"></script><script src="/pesquisa-mercado/history-ui.js"></script></body>')

@app.get('/fixture/meta')
def meta():
    return {'old': old['id'], 'current': current['id']}

@app.post('/fixture/progress')
def progress():
    return {'job': store.checkpoint(current['id'], 'fixture-client', 'alice', counters={'pages': 3}, message='Progresso da verificação integrada.')}

@app.post('/fixture/pause')
async def pause(request: Request):
    await auth(request, request.headers.get('Authorization'))
    payload = await request.json()
    if payload.get('jobId') != current['id']:
        raise HTTPException(422, 'Wrong active job')
    return {'job': store.checkpoint(current['id'], 'fixture-client', 'alice', status='paused')}

uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[1]), access_log=False, log_level='error')
