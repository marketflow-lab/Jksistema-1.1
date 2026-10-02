'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');

function gate() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

function fixture(id = 'A', complete = false) {
  return {
    id, nome_lista: 'Lista ' + id, store_id: 'store-' + id, loja: 'Loja ' + id,
    fornecedor_id: 'supplier-a', supplier: 'Empresa A', numero_invoice: '',
    itens: [
      { SKU: '001', Quantidade: 2, 'M3 individual': 0.1, 'Valor unidade': 10, analise_concorrentes: { margens: {} } },
      { SKU: '002', Quantidade: 3, ...(complete ? { 'M3 individual': 0.2 } : { 'M3 individual': 0, M3: 0 }), 'Valor unidade': 5, analise_concorrentes: { margens: {} } },
      { SKU: '003', Quantidade: 0, 'M3 individual': 0.5, analise_concorrentes: { margens: {} } },
    ],
  };
}

async function environment(browser, options = {}) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const state = { lists: { A: fixture('A', options.complete), B: fixture('B', true) },
    prefs: gate(), catalog: gate(), supplier: gate(), oldCost: gate(), detail: gate(),
    calls: { prefs: 0, catalog: 0, globalCatalog: 0, cost: 0, detail: 0 }, updates: [], errors: [] };
  page.on('pageerror', error => state.errors.push(error.message));
  if (options.fastTimeout) await page.addInitScript(() => {
    const timeout = window.setTimeout.bind(window);
    window.setTimeout = (callback, delay, ...args) => timeout(callback, delay === 5000 ? 40 : delay, ...args);
  });
  const json = (route, body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {});
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    const pathname = url.pathname;
    if (pathname.endsWith('.html')) return route.fulfill({ status: 200, contentType: 'text/html',
      body: fs.readFileSync(path.join(root, 'static', pathname.slice(1))) });
    if (pathname === '/auth.js') return route.fulfill({ status: 200, contentType: 'application/javascript',
      body: 'window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({Authorization:"Bearer synthetic"});' });
    if (pathname.startsWith('/medias_compras/')) return route.fulfill({ status: 200, contentType: 'application/javascript',
      body: fs.readFileSync(path.join(root, 'static', pathname.slice(1))) });
    if (pathname === '/api/cadastro/fornecedores') return json(route, [
      { id: 'supplier-a', nome_empresa: 'Empresa A' }, { id: 'supplier-b', nome_empresa: 'Empresa B' },
    ]);
    if (pathname === '/api/cadastro/produtos') { ++state.calls.globalCatalog; return json(route, [{ sku: '002', m3: 999 }]); }
    if (pathname.startsWith('/api/cadastro/lojas/')) {
      ++state.calls.catalog;
      assert.strictEqual(pathname, '/api/cadastro/lojas/store-A/produtos');
      await state.catalog.promise;
      return json(route, [
        { store_id: 'store-X', sku: '002', 'M3 individual': 999 },
        { store_id: 'store-A', sku: '002', 'M3 individual': 0.2 },
        { store_id: 'store-A', sku: '001', 'M3 individual': 900 },
      ]);
    }
    if (pathname.endsWith('/preferencias-colunas')) {
      if (method === 'PUT') return json(route, {});
      ++state.calls.prefs;
      await state.prefs.promise;
      return json(route, { ordem_colunas: ['foto', 'sku', 'm3'], larguras_colunas: { sku: 321, m3: 211 } });
    }
    if (pathname.endsWith('/landed-cost')) {
      const requestOrder = ++state.calls.cost;
      if (requestOrder === 1 && options.holdCost) await state.oldCost.promise;
      return json(route, { analysis: { order_id: pathname.split('/').at(-2), tax_band_pct: requestOrder === 1 ? 55 : 23 } });
    }
    if (pathname === '/api/vendas/grafico') return json(route, { valores_vendas: [10], valores_devolucoes: [] });
    const match = pathname.match(/^\/api\/medias-compras\/listas-pedidos\/([^/]+)$/);
    if (match) {
      if (method === 'GET') {
        ++state.calls.detail;
        if (options.holdDetail && match[1] === 'A') {
          await state.detail.promise;
          return json(route, { detail: 'Erro antigo sintetico' }, 500);
        }
      } else if (method === 'PUT') {
        const payload = route.request().postDataJSON();
        state.updates.push(payload);
        if (payload.fornecedor_id && options.holdSupplier) await state.supplier.promise;
        Object.assign(state.lists[match[1]], payload);
        if (payload.fornecedor_id) state.lists[match[1]].supplier = payload.fornecedor_id === 'supplier-b' ? 'Empresa B' : 'Empresa A';
      }
      return json(route, { lista: state.lists[match[1]] });
    }
    if (pathname.startsWith('/api/')) return json(route, {});
    return route.fulfill({ status: 200, contentType: 'application/javascript', body: '' });
  });
  return { context, page, state };
}

async function waitUntil(predicate) {
  for (let attempt = 0; attempt < 200; attempt++) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  throw new Error('Condição sintética não atingida.');
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const first = await environment(browser, { holdCost: true, holdSupplier: true });
    const { page, state } = first;
    await page.goto('http://jk.test/importacoes_lista.html?lista_id=A');
    await page.locator('#tbodyLista tr[data-sku="001"]').waitFor();
    assert.strictEqual(await page.locator('#tbodyLista tr[data-sku]').count(), 3);
    assert.strictEqual(state.calls.globalCatalog, 0);
    await waitUntil(() => state.calls.prefs === 1 && state.calls.catalog === 1);
    assert.match(await page.locator('tr[data-sku="002"] [data-col-key="m3"]').textContent(), /Carregando/);
    assert.strictEqual(await page.locator('tr[data-sku="003"] [data-col-key="m3"]').textContent(), await page.evaluate(() => formatarM3(0)));
    await page.locator('[data-edit-key="numero_invoice"]').dblclick();
    const invoice = page.locator('[data-edit-key="numero_invoice"] input');
    await invoice.fill('INV-SINTETICA');
    await page.evaluate(() => {
      window.invoiceNode = document.querySelector('[data-edit-key="numero_invoice"] input');
      window.rowNode = document.querySelector('#tbodyLista tr[data-sku="002"]');
      moverColuna('m3', 'sku');
      sincronizarColunasVisiveis();
      const indice = colunasTabela.findIndex(col => col.key === 'sku');
      aplicarLarguraColuna(indice, colunasTabela[indice], 177, true);
    });
    state.catalog.resolve();
    await page.waitForFunction(() => !mapaM3IndividualCarregando);
    assert.strictEqual(await page.evaluate(() => document.querySelector('tr[data-sku="002"]') === window.rowNode), true);
    assert.strictEqual(await page.evaluate(() => document.querySelector('[data-edit-key="numero_invoice"] input') === window.invoiceNode), true);
    assert.strictEqual(await invoice.inputValue(), 'INV-SINTETICA');
    assert.strictEqual(await page.evaluate(() => document.activeElement === window.invoiceNode), true);
    assert.strictEqual(await page.locator('tr[data-sku="002"] [data-col-key="m3"]').textContent(), await page.evaluate(() => formatarM3(0.6)));
    assert.strictEqual(await page.locator('tr[data-sku="001"] [data-col-key="m3"]').textContent(), await page.evaluate(() => formatarM3(0.2)));
    state.prefs.resolve();
    await page.waitForFunction(() => preferenciasColunasCarregadas);
    assert.strictEqual(await page.locator('#tHead th').first().getAttribute('data-col-key'), 'm3');
    assert.strictEqual(await page.evaluate(() => largurasColunas.sku), 177);
    assert.strictEqual(await page.evaluate(() => document.querySelector('tr[data-sku="002"]') === window.rowNode), true);
    await invoice.press('Enter');
    await waitUntil(() => state.updates.filter(item => item.numero_invoice).length === 1 && state.calls.cost >= 2);
    await page.waitForFunction(() => ultimaAnaliseCustoPosto?.tax_band_pct === 23);
    await page.waitForFunction(() => !document.getElementById('resumoFornecedorSelect').disabled);
    await page.locator('#resumoFornecedorSelect').selectOption('supplier-b');
    await waitUntil(() => state.updates.some(item => item.fornecedor_id));
    await page.evaluate(() => { window.supplierNode = document.getElementById('resumoFornecedorSelect'); });
    state.oldCost.resolve();
    await page.evaluate(() => carregarFaturamentoVendas12m());
    await page.waitForFunction(() => controladoresAuxiliares.size === 0);
    assert.strictEqual(await page.evaluate(() => document.getElementById('resumoFornecedorSelect') === window.supplierNode), true);
    assert.strictEqual(await page.locator('#resumoFornecedorSelect').inputValue(), 'supplier-b');
    assert.strictEqual(await page.locator('#resumoFornecedorSelect').isDisabled(), true);
    assert.strictEqual(await page.evaluate(() => ultimaAnaliseCustoPosto.tax_band_pct), 23);
    state.supplier.resolve();
    await page.waitForFunction(() => !document.getElementById('resumoFornecedorSelect').disabled);
    assert.deepStrictEqual(state.errors, []);
    await first.context.close();

    const columns = await environment(browser, { complete: true });
    await columns.page.goto('http://jk.test/importacoes_lista.html?lista_id=A');
    await columns.page.locator('#tbodyLista tr[data-sku="001"]').waitFor();
    assert.strictEqual(columns.state.calls.catalog, 0, 'Itens enriquecidos dispensam catálogo complementar');
    assert.strictEqual(await columns.page.evaluate(async () => {
      const original = renderResumoTopo;
      let chamadas = 0;
      renderResumoTopo = (...args) => { chamadas++; return original(...args); };
      try { await carregarMapaM3Individual(contextoCarregamentoAtual('A')); }
      finally { renderResumoTopo = original; }
      return chamadas;
    }), 0, 'Metragem já enriquecida dispensa a repetição do resumo');
    await columns.page.evaluate(() => { window.rowNode = document.querySelector('tr[data-sku="001"]'); skuArrastadoIndex = 0; });
    columns.state.prefs.resolve();
    await columns.page.waitForFunction(() => preferenciasColunasPendentes !== null);
    assert.strictEqual(await columns.page.locator('#tHead th').first().getAttribute('data-col-key'), 'sku');
    await columns.page.evaluate(() => { skuArrastadoIndex = -1; aplicarPreferenciasColunasPendentes(); });
    assert.strictEqual(await columns.page.locator('#tHead th').first().getAttribute('data-col-key'), 'foto');
    assert.strictEqual(await columns.page.evaluate(() => largurasColunas.sku), 321);
    assert.strictEqual(await columns.page.evaluate(() => document.querySelector('tr[data-sku="001"]') === window.rowNode), true);
    assert.deepStrictEqual(columns.state.errors, []);
    await columns.context.close();

    const stale = await environment(browser, { holdDetail: true });
    await stale.page.goto('http://jk.test/importacoes_lista.html?lista_id=A');
    await waitUntil(() => stale.state.calls.detail === 1);
    stale.state.prefs.resolve();
    await stale.page.evaluate(() => { history.replaceState({}, '', '?lista_id=B'); return carregarDetalheLista(); });
    await stale.page.locator('#tbodyLista tr[data-sku="001"]').waitFor();
    stale.state.detail.resolve();
    await stale.page.waitForLoadState('networkidle');
    assert.strictEqual(await stale.page.locator('#titulo').textContent(), 'Importações - Lista B');
    assert.strictEqual(await stale.page.locator('#erro').textContent(), '');
    assert.strictEqual(await stale.page.locator('#tbodyLista tr[data-sku]').count(), 3);
    assert.strictEqual(stale.state.calls.catalog, 0);
    assert.deepStrictEqual(stale.state.errors, []);
    await stale.context.close();

    const timeout = await environment(browser, { fastTimeout: true });
    timeout.state.prefs.resolve();
    await timeout.page.goto('http://jk.test/importacoes_lista.html?lista_id=A');
    await timeout.page.locator('#tbodyLista tr[data-sku="001"]').waitFor();
    await timeout.page.waitForFunction(() => !mapaM3IndividualCarregando);
    assert.strictEqual(timeout.state.calls.catalog, 2, 'Dependência expirada faz uma nova tentativa limitada');
    assert.strictEqual(await timeout.page.locator('tr[data-sku="002"] [data-col-key="m3"]').textContent(), 'Indisponível');
    assert.strictEqual(await timeout.page.locator('#btnRecarregarM3').isVisible(), true);
    timeout.state.catalog.resolve();
    await timeout.page.locator('#btnRecarregarM3').click();
    await timeout.page.waitForFunction(() => !mapaM3IndividualCarregando);
    assert.strictEqual(await timeout.page.locator('tr[data-sku="002"] [data-col-key="m3"]').textContent(), await timeout.page.evaluate(() => formatarM3(0.6)));
    assert.deepStrictEqual(timeout.state.errors, []);
    await timeout.context.close();
    console.log('Importação: render imediato, complementos paralelos, M3 contextual, preferências tardias, edição/foco/arraste, fornecedor, custo fora de ordem, geração obsoleta e timeout/retry: OK');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
