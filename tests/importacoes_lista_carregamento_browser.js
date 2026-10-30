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
    prefs: gate(), catalog: gate(), supplier: gate(), oldCost: gate(), detail: gate(), margins: gate(), marginPatch: gate(),
    calls: { prefs: 0, catalog: 0, globalCatalog: 0, cost: 0, detail: 0, saved: 0, polling: 0, margins: 0 },
    ready: false, transientRemaining: options.transient ? [503, 409] : [],
    updates: [], patches: [], errors: [] };
  if (options.needMargins) delete state.lists.A.itens[0].analise_concorrentes;
  if (options.queuedDetail) state.lists.A.itens[0].Quantidade = 4;
  page.on('pageerror', error => state.errors.push(error.message));
  if (options.fastTimeout) await page.addInitScript(() => {
    const timeout = window.setTimeout.bind(window);
    window.setTimeout = (callback, delay, ...args) => timeout(callback, [5000, 20000].includes(delay) ? 40 : delay, ...args);
  });
  if (options.scaledCatalogBudget) await page.addInitScript(() => {
    const timeout = window.setTimeout.bind(window);
    // Keep the observed 7.6-second catalog read between the old 5-second and
    // new 20-second deadlines while keeping the browser regression short.
    window.setTimeout = (callback, delay, ...args) => timeout(callback,
      delay === 5000 ? 50 : delay === 20000 ? 200 : delay, ...args);
  });
  if (options.fastPolling) await page.addInitScript(() => {
    const timeout = window.setTimeout.bind(window);
    window.setTimeout = (callback, delay, ...args) => timeout(callback,
      delay === 5000 ? 500 : [1000, 2000, 10000].includes(delay) ? 20 : delay, ...args);
  });
  if (options.cacheStub) await page.addInitScript(() => {
    const context = { tenant: 'synthetic', store: 'synthetic' };
    window.cacheWrites = [];
    window.cacheInvalidations = 0;
    window.JKCachePersistente = {
      contexto: () => context, vigente: () => true, dadosVigentes: () => true,
      ler: async () => null, confirmarMutacao: () => context,
      invalidar: async () => { window.cacheInvalidations++; },
      salvar: async (key, data) => { window.cacheWrites.push({ key, data }); },
    };
  });
  if (options.oldPolling403) await page.addInitScript(() => {
    const fetch = window.fetch.bind(window);
    // Simulate a transport that delivers a completed old request after abort.
    window.fetch = (url, options) => String(url).includes('/listas-pedidos/A?cache_mode=stale')
      ? new Promise(resolve => { window.releaseOldPolling = () => resolve(new Response(JSON.stringify({ detail: 'Acesso antigo negado.' }),
        { status: 403, headers: { 'Content-Type': 'application/json' } })); })
      : fetch(url, options);
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
      if (options.scaledCatalogBudget) await new Promise(resolve => setTimeout(resolve, 76));
      else await state.catalog.promise;
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
    if (pathname.endsWith('/concorrentes-links')) {
      ++state.calls.margins;
      if (options.holdMargins) await state.margins.promise;
      return json(route, { resultados: { '001': { concorrentes_valores: { concorrente_1: 10 } } } });
    }
    if (pathname.endsWith('/analise-concorrentes') && method === 'PATCH') {
      state.patches.push(route.request().postDataJSON());
      if (options.holdMarginPatch) await state.marginPatch.promise;
      return json(route, { analise_concorrentes: { margens: { concorrente_1: 25 } } });
    }
    const match = pathname.match(/^\/api\/medias-compras\/listas-pedidos\/([^/]+)$/);
    if (match) {
      if (method === 'GET') {
        if (url.searchParams.get('somente_salvos') === 'true') {
          ++state.calls.saved;
          if (options.denyDetail) return json(route, { detail: 'Acesso negado.' }, 403);
          return json(route, { lista: fixture(match[1], options.complete) });
        }
        ++state.calls.detail;
        if (options.denyDetail) return json(route, { detail: 'Acesso negado.' }, 403);
        if (url.searchParams.get('cache_mode') === 'stale') {
          ++state.calls.polling;
          if (state.transientRemaining.length) return json(route, {
            detail: { code: 'sources_changing', message: 'Fontes em atualização.' },
          }, state.transientRemaining.shift());
          if (options.queuedDetail && !state.ready) return json(route, {
            _cache: { estado: 'waiting', completo: false, atualizando: true, retry_after_ms: 1000 },
          }, 202);
        }
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

    const slowCatalog = await environment(browser, { scaledCatalogBudget: true });
    slowCatalog.state.prefs.resolve();
    await slowCatalog.page.goto('http://jk.test/importacoes_lista.html?lista_id=A');
    await slowCatalog.page.locator('#tbodyLista tr[data-sku="001"]').waitFor();
    await slowCatalog.page.waitForFunction(() => mapaM3IndividualCarregado && !mapaM3IndividualCarregando);
    assert.strictEqual(slowCatalog.state.calls.catalog, 1, 'Catálogo válido após o antigo limite não é cancelado nem repetido');
    assert.strictEqual(await slowCatalog.page.locator('tr[data-sku="002"] [data-col-key="m3"]').textContent(),
      await slowCatalog.page.evaluate(() => formatarM3(0.6)));
    assert.strictEqual(slowCatalog.state.calls.globalCatalog, 0);
    assert.deepStrictEqual(slowCatalog.state.errors, []);
    await slowCatalog.context.close();

    const queued = await environment(browser, { queuedDetail: true, transient: true, fastPolling: true, complete: true, cacheStub: true });
    queued.state.prefs.resolve();
    await queued.page.goto('http://jk.test/importacoes_lista.html?lista_id=A');
    await queued.page.locator('#tbodyLista tr[data-sku="001"]').waitFor();
    await waitUntil(() => queued.state.calls.polling >= 3);
    assert.strictEqual(await queued.page.locator('tr[data-sku="001"] [data-col-key="quantidade"]').textContent(), '2');
    assert.strictEqual(queued.state.calls.cost, 0, 'Resposta em processamento não inicia complementos nem análise');
    assert.strictEqual(queued.state.calls.margins, 0);
    assert.strictEqual(await queued.page.locator('#erro').textContent(), '');
    queued.state.ready = true;
    await queued.page.waitForFunction(() => ultimosItensCarregados[0]?.Quantidade === 4);
    await queued.page.waitForFunction(() => /^(Dados|Itens) atualizados/.test(document.getElementById('cacheListaStatus').textContent)
      && !/Atualizando/.test(document.getElementById('cacheListaStatus').textContent));
    assert.strictEqual(await queued.page.locator('tr[data-sku="001"] [data-col-key="quantidade"]').textContent(), '4');
    assert.strictEqual(await queued.page.evaluate(() => cacheWrites.some(entry => entry.data?._cache?.estado === 'waiting')), false,
      'Resposta 202 não substitui o snapshot válido');
    assert.deepStrictEqual(queued.state.errors, []);
    await queued.context.close();

    const denied = await environment(browser, { denyDetail: true, complete: true, cacheStub: true });
    await denied.page.goto('http://jk.test/importacoes_lista.html?lista_id=A');
    await denied.page.waitForFunction(() => window.cacheInvalidations > 0);
    assert.strictEqual(await denied.page.locator('#tbodyLista tr[data-sku]').count(), 0);
    assert.strictEqual(denied.state.calls.cost, 0);
    assert.strictEqual(denied.state.patches.length, 0);
    assert.deepStrictEqual(denied.state.errors, []);
    await denied.context.close();

    const oldDenied = await environment(browser, { oldPolling403: true, complete: true, cacheStub: true });
    oldDenied.state.prefs.resolve();
    await oldDenied.page.goto('http://jk.test/importacoes_lista.html?lista_id=A');
    await oldDenied.page.locator('#tbodyLista tr[data-sku="001"]').waitFor();
    await oldDenied.page.waitForFunction(() => typeof releaseOldPolling === 'function');
    await oldDenied.page.evaluate(() => { history.replaceState({}, '', '?lista_id=B'); return carregarDetalheLista(); });
    await oldDenied.page.waitForFunction(() => ultimaListaCarregada?.id === 'B');
    await oldDenied.page.evaluate(async () => { releaseOldPolling(); await new Promise(resolve => setTimeout(resolve, 0)); });
    assert.strictEqual(await oldDenied.page.locator('#titulo').textContent(), 'Importações - Lista B');
    assert.strictEqual(await oldDenied.page.locator('#tbodyLista tr[data-sku]').count(), 3);
    assert.strictEqual(await oldDenied.page.evaluate(() => cacheInvalidations), 0, '403 de geração anterior não invalida a lista atual');
    assert.deepStrictEqual(oldDenied.state.errors, []);
    await oldDenied.context.close();

    for (const dependency of ['holdMargins', 'holdMarginPatch']) {
      const pendingMargins = await environment(browser, { [dependency]: true, needMargins: true, complete: true, fastTimeout: true, cacheStub: true });
      pendingMargins.state.prefs.resolve();
      await pendingMargins.page.goto('http://jk.test/importacoes_lista.html?lista_id=A');
      await pendingMargins.page.locator('#tbodyLista tr[data-sku="001"]').waitFor();
      await waitUntil(() => dependency === 'holdMargins'
        ? pendingMargins.state.calls.margins > 0 : pendingMargins.state.patches.length > 0);
      await pendingMargins.page.waitForFunction(() => /^(Dados|Itens) atualizados/.test(document.getElementById('cacheListaStatus').textContent)
        && !/Atualizando/.test(document.getElementById('cacheListaStatus').textContent));
      await pendingMargins.page.waitForFunction(() => estadosAnaliseMargensAutomaticas.get('001')?.status === 'erro');
      assert.strictEqual(await pendingMargins.page.locator('#tbodyLista tr[data-sku]').count(), 3);
      assert.strictEqual(await pendingMargins.page.evaluate(() => ultimaListaCarregada.itens[0].analise_concorrentes), undefined,
        'Dependência de margens expirada não publica análise parcial');
      assert.strictEqual(pendingMargins.state.patches.length, dependency === 'holdMargins' ? 0 : 1);
      assert.deepStrictEqual(pendingMargins.state.errors, []);
      pendingMargins.state.margins.resolve();
      pendingMargins.state.marginPatch.resolve();
      await pendingMargins.context.close();
    }

    const stale = await environment(browser, { holdDetail: true });
    await stale.page.goto('http://jk.test/importacoes_lista.html?lista_id=A');
    await waitUntil(() => stale.state.calls.detail >= 1);
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
    assert.strictEqual(await timeout.page.locator('tr[data-sku="002"] [data-col-key="m3"]').textContent(), 'Cubagem pendente');
    assert.strictEqual(await timeout.page.locator('#btnRecarregarM3').isVisible(), true);
    timeout.state.catalog.resolve();
    await timeout.page.locator('#btnRecarregarM3').click();
    await timeout.page.waitForFunction(() => !mapaM3IndividualCarregando);
    assert.strictEqual(await timeout.page.locator('tr[data-sku="002"] [data-col-key="m3"]').textContent(), await timeout.page.evaluate(() => formatarM3(0.6)));
    assert.deepStrictEqual(timeout.state.errors, []);
    await timeout.context.close();
    console.log('Importação: render imediato, consulta pronta após 202/transitórios, catálogo lento, margens limitadas em segundo plano, M3 contextual, preferências tardias, edição/foco/arraste, fornecedor, custo fora de ordem, geração/auth e timeout/retry: OK');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
