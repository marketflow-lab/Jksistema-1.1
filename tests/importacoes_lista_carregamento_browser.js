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

function prepararDadosCusto(lista, cotacao = 5) {
  Object.assign(lista, { exchange_rate: cotacao, 'Valor por M³': 50 });
  Object.assign(lista.itens[0], { II: '10%', IPI: '5%', PIS: '2%', COFINS: '8%' });
  return lista;
}

async function environment(browser, options = {}) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const state = { lists: { A: fixture('A', options.complete), B: fixture('B', true) }, savedLists: {},
    prefs: gate(), catalog: gate(), supplier: gate(), oldCost: gate(), detail: gate(), margins: gate(), marginPatch: gate(),
    calls: { prefs: 0, catalog: 0, globalCatalog: 0, cost: 0, fiscal: 0, detail: 0, saved: 0, polling: 0, margins: 0 },
    ready: false, deniedStatus: options.denyDetail ? 403 : null,
    transientRemaining: options.transient ? [503, 409] : [],
    updates: [], patches: [], errors: [] };
  if (options.needMargins) {
    prepararDadosCusto(state.lists.A);
    delete state.lists.A.itens[0].analise_concorrentes;
  }
  if (options.queuedDetail) state.lists.A.itens[0].Quantidade = 4;
  page.on('pageerror', error => state.errors.push(error.message));
  if (options.fastTimeout) await page.addInitScript(() => {
    const timeout = window.setTimeout.bind(window);
    window.setTimeout = (callback, delay, ...args) => timeout(callback, [5000, 20000, 120000].includes(delay) ? 150 : delay, ...args);
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
  if (options.realCache) await page.addInitScript(() => {
    localStorage.setItem('user_data', JSON.stringify({ client_id: 'synthetic', username: 'tester', store_id: 'store-A' }));
  });
  if (options.oldResponses) await page.addInitScript(() => {
    const fetch = window.fetch.bind(window);
    // Simula respostas que chegam depois do cancelamento da lista anterior.
    window.fetch = (url, options) => {
      if (String(url).includes('/listas-pedidos/A?cache_mode=stale')) {
        return new Promise(resolve => { window.releaseOldPolling = () => resolve(new Response(JSON.stringify({ detail: 'Acesso antigo negado.' }),
          { status: 403, headers: { 'Content-Type': 'application/json' } })); });
      }
      if (String(url).includes('/listas-pedidos/A/base-custos') && !window.releaseOldFiscal) {
        return new Promise(resolve => { window.releaseOldFiscal = () => resolve(new Response(JSON.stringify({ produtos: [] }),
          { headers: { 'Content-Type': 'application/json' } })); });
      }
      return fetch(url, options);
    };
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
    if ((options.realCache && pathname === '/cache-persistente.js') || pathname.startsWith('/medias_compras/')) {
      return route.fulfill({ status: 200, contentType: 'application/javascript',
        body: fs.readFileSync(path.join(root, 'static', pathname.slice(1))) });
    }
    const fiscalSku = { sku: '001', convenio_mg_st_paraiba: 'Sim', convenio_mg_mva: '40%' };
    const baseMatch = pathname.match(/^\/api\/medias-compras\/listas-pedidos\/([^/]+)\/base-custos$/);
    if (baseMatch) {
      const id = baseMatch[1];
      const lista = options.queuedDetail && !state.ready && state.savedLists[id] ? state.savedLists[id] : state.lists[id];
      const revision = require('crypto').createHash('sha256').update(JSON.stringify(lista)).digest('hex');
      const { itens: _itens, ...cabecalho } = lista;
      const meta = { success: true, versao: 1, lista: { ...cabecalho, exchange_rate: lista.exchange_rate || lista.dolar_hoje },
        revisao: revision, revisoes_sku: Object.fromEntries(lista.itens.map(item => [item.SKU, revision])) };
      if (url.searchParams.get('somente_revisao') === 'true') return json(route, meta);
      ++state.calls.fiscal;
      return json(route, { ...meta, itens: lista.itens.map(item => ({ ...item, fiscal_pronto: true, fiscal: item.SKU === '001' ? fiscalSku : {} })),
        rateio: { total_usd: 35, total_m3: 0.8, m3_completo: true, valor_por_m3: 50 } });
    }
    if (pathname === '/api/impostos/produtos') {
      ++state.calls.fiscal;
      return json(route, { produtos: [fiscalSku] });
    }
    if (pathname === '/api/medias-compras/listas-pedidos/A/skus/001') {
      const { itens, ...lista } = state.lists.A;
      const somenteSalvos = url.searchParams.get('somente_salvos') === 'true';
      return json(route, {
        lista, item: itens[0], revisao: 'lista-A',
        rateio: { total_usd: 35, total_m3: 0.8, m3_completo: true, valor_por_m3: 50 },
        fiscal: somenteSalvos ? null : fiscalSku, fiscal_pronto: !somenteSalvos,
      });
    }
    if (pathname === '/api/cadastro/fornecedores') return json(route, [
      { id: 'supplier-a', nome_empresa: 'Empresa A' }, { id: 'supplier-b', nome_empresa: 'Empresa B' },
    ]);
    if (pathname === '/api/cadastro/produtos') { ++state.calls.globalCatalog; return json(route, [{ sku: '002', m3: 999 }]); }
    // O custo consulta a projeção resumida; o contador abaixo mede somente a cubagem.
    if (pathname.startsWith('/api/cadastro/lojas/') && url.searchParams.get('view') === 'costs') return route.fulfill({
      status: 200, headers: { 'X-Cadastro-Custos-Revision': 'cadastro-synthetic' }, contentType: 'application/json', body: '[]' });
    if (pathname === '/api/cadastro/lojas/store-A/produtos/001') return json(route, {});
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
      return json(route, { ordem_colunas: ['foto', 'sku', 'm3', 'valor_unit_brl'], larguras_colunas: { sku: 321, m3: 211 } });
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
      return json(route, { fontes_versao: 3, anuncios_loja_fonte: 'loja_lista_sku', anuncios_loja_store_id: 'store-A',
        concorrentes_valores: { concorrente_1: 10 }, concorrentes_mlb: { concorrente_1: 'MLB1111111111' } });
    }
    if (pathname.endsWith('/analise-concorrentes') && method === 'PATCH') {
      state.patches.push(route.request().postDataJSON());
      if (options.holdMarginPatch) await state.marginPatch.promise;
      const payload = route.request().postDataJSON();
      return json(route, { analise_concorrentes: { ...payload, store_id: 'store-A', margens: { concorrente_1: { margem_percentual: 25 } } } });
    }
    const match = pathname.match(/^\/api\/medias-compras\/listas-pedidos\/([^/]+)$/);
    if (match) {
      if (method === 'GET') {
        if (url.searchParams.get('somente_salvos') === 'true') {
          ++state.calls.saved;
          if (state.deniedStatus) return json(route, { detail: 'Acesso negado.' }, state.deniedStatus);
          return json(route, { lista: state.savedLists[match[1]] || fixture(match[1], options.complete) });
        }
        ++state.calls.detail;
        if (state.deniedStatus) return json(route, { detail: 'Acesso negado.' }, state.deniedStatus);
        if (url.searchParams.get('cache_mode') === 'stale') {
          ++state.calls.polling;
          if (options.holdFirstReady && state.calls.polling === 1) {
            const lista = structuredClone(state.lists[match[1]]);
            await state.detail.promise;
            return json(route, { lista });
          }
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
    Object.assign(state.lists.A, { dolar_hoje: 5, 'Valor por M³': 50 });
    Object.assign(state.lists.A.itens[0], { II: '10%', IPI: '5%', PIS: '2%', COFINS: '8%', 'Valor Unitário R$': 999, 'Frete Internacional': 10 });
    await page.addInitScript(() => {
      const chave = 'importacoes_sku_proporcao_v1';
      if (!localStorage.getItem(chave)) localStorage.setItem(chave, JSON.stringify({ A: { '001': '80' }, B: { '001': '60' } }));
    });
    await page.goto('http://jk.test/importacoes_lista.html?lista_id=A');
    await page.locator('#tbodyLista tr[data-sku="001"]').waitFor();
    assert.strictEqual(await page.locator('#tbodyLista tr[data-sku]').count(), 3);
    assert.strictEqual(state.calls.globalCatalog, 0);
    await waitUntil(() => state.calls.prefs === 1 && state.calls.catalog === 1);
    assert.match(await page.locator('tr[data-sku="002"] [data-col-key="m3"]').textContent(), /Carregando/);
    assert.strictEqual(await page.locator('tr[data-sku="003"] [data-col-key="m3"]').textContent(), await page.evaluate(() => formatarM3(0)));
    for (const key of ['ii', 'ipi', 'pis', 'cofins', 'icms']) {
      assert.strictEqual(await page.locator(`#tHead [data-col-key="${key}"], #tbodyLista [data-col-key="${key}"]`).count(), 0);
    }
    const custo001 = page.locator('tr[data-sku="001"] [data-col-key="valor_unit_brl"]');
    const custo002 = page.locator('tr[data-sku="002"] [data-col-key="valor_unit_brl"]');
    await page.waitForFunction(() => /R\$/.test(document.querySelector('tr[data-sku="001"] [data-col-key="valor_unit_brl"]')?.textContent));
    assert.match(await custo001.textContent(), /123,29/);
    assert.strictEqual(await custo002.textContent(), 'Dados insuficientes');
    const frete001 = page.locator('tr[data-sku="001"] [data-col-key="frete_internacional"]');
    assert.match(await frete001.textContent(), /50,00/);
    await waitUntil(() => state.calls.cost === 1);
    await page.evaluate(() => atualizarCampoLogisticoLista('A', 'exchange_rate', '6'));
    assert.match(await custo001.textContent(), /147,95/, 'A cotação editada prevalece sobre o dólar legado no custo unitário.');
    assert.match(await frete001.textContent(), /60,00/, 'O frete em reais acompanha a cotação editada sem recarregar a página.');
    await page.evaluate(() => atualizarCampoLogisticoLista('A', 'exchange_rate', '5'));
    assert.match(await custo001.textContent(), /123,29/);
    assert.match(await frete001.textContent(), /50,00/);
    const proporcao = page.locator('#resumoProporcaoForm');
    const percentual = proporcao.locator('input');
    const aplicar = proporcao.getByRole('button', { name: 'Aplicar a todos' });
    assert.strictEqual(await proporcao.locator('xpath=preceding-sibling::*[1]/*[@class="label"]').textContent(), 'Total NCM');
    assert.strictEqual(await percentual.inputValue(), '', 'Proporções diferentes não são exibidas como um valor único');
    const lerProporcoes = () => page.evaluate(() => JSON.parse(localStorage.getItem('importacoes_sku_proporcao_v1')));
    await percentual.fill('101');
    await aplicar.click();
    assert.deepStrictEqual(await lerProporcoes(), { A: { '001': '80' }, B: { '001': '60' } });
    const proporcao001 = page.getByRole('spinbutton', { name: 'Proporção (%) do SKU 001' });
    const proporcao002 = page.getByRole('spinbutton', { name: 'Proporção (%) do SKU 002' });
    assert.strictEqual(await proporcao001.inputValue(), '80');
    assert.strictEqual(await proporcao002.inputValue(), '100');
    await proporcao001.fill('62.5');
    await proporcao001.press('Enter');
    assert.match(page.url(), /importacoes_lista\.html\?lista_id=A$/);
    assert.deepStrictEqual(await lerProporcoes(), { A: { '001': '62.5' }, B: { '001': '60' } });
    assert.notStrictEqual(await custo001.textContent(), await page.evaluate(() => formatarBRL(123.29)));
    assert.strictEqual(await proporcao002.inputValue(), '100');
    assert.strictEqual(await percentual.getAttribute('placeholder'), 'Variada');
    await proporcao001.fill('0');
    await proporcao001.press('Enter');
    assert.strictEqual(await proporcao001.evaluate(input => input === document.activeElement), true);
    await proporcao001.press('Escape');
    assert.strictEqual(await proporcao001.inputValue(), '62.5');
    assert.deepStrictEqual(await lerProporcoes(), { A: { '001': '62.5' }, B: { '001': '60' } });

    await percentual.fill('45.5');
    await page.evaluate(() => renderResumoTopo(ultimaListaCarregada, ultimosItensCarregados));
    assert.strictEqual(await percentual.inputValue(), '45.5', 'Atualização do painel preserva o valor em edição');
    await aplicar.click();
    assert.match(await proporcao.getByRole('status').textContent(), /3 SKUs/);
    assert.match(await custo001.textContent(), /109,42/);
    assert.strictEqual(await proporcao001.inputValue(), '45.5');
    assert.strictEqual(await proporcao002.inputValue(), '45.5');
    assert.deepStrictEqual(await lerProporcoes(), {
      A: { '001': '45.5', '002': '45.5', '003': '45.5' },
      B: { '001': '60' },
    });
    const outraAba = await first.context.newPage();
    await outraAba.route('http://jk.test/proporcoes', route => route.fulfill({ contentType: 'text/html', body: '<html></html>' }));
    await outraAba.goto('http://jk.test/proporcoes');
    await percentual.focus();
    await outraAba.evaluate(() => {
      const chave = 'importacoes_sku_proporcao_v1';
      const mapa = JSON.parse(localStorage.getItem(chave));
      mapa.A['002'] = '70';
      localStorage.setItem(chave, JSON.stringify(mapa));
    });
    await page.waitForFunction(() => document.getElementById('resumoProporcaoPercentual').placeholder === 'Variada');
    assert.strictEqual(await percentual.inputValue(), '');
    assert.strictEqual(await proporcao002.inputValue(), '70');
    await outraAba.close();
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
    await proporcao001.fill('50');
    await proporcao001.press('Tab');
    assert.strictEqual(await percentual.getAttribute('placeholder'), 'Variada');
    const custoNaLista = await custo001.textContent();
    assert.match(custoNaLista, /111,23/);
    await page.goto('http://jk.test/importacoes_sku.html?lista_id=A&sku=001');
    await page.waitForFunction(() => /R\$/.test(document.getElementById('vValorUnitarioProduto').textContent));
    assert.strictEqual(await page.locator('#vProp').textContent(), '50%');
    assert.strictEqual(await page.locator('#vValorUnitarioProduto').textContent(), custoNaLista);
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
    assert.strictEqual(await columns.page.locator('#tHead th[data-col-key="proporcao"] + th').getAttribute('data-col-key'), 'valor_unit_brl');
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
    queued.state.savedLists.A = prepararDadosCusto(fixture('A', true));
    prepararDadosCusto(queued.state.lists.A, 6);
    queued.state.prefs.resolve();
    await queued.page.goto('http://jk.test/importacoes_lista.html?lista_id=A');
    await queued.page.locator('#tbodyLista tr[data-sku="001"]').waitFor();
    await waitUntil(() => queued.state.calls.polling >= 3);
    assert.strictEqual(await queued.page.locator('tr[data-sku="001"] [data-col-key="quantidade"]').textContent(), '2');
    await queued.page.waitForFunction(() => /131,33/.test(document.querySelector('tr[data-sku="001"] [data-col-key="valor_unit_brl"]')?.textContent),
      undefined, { timeout: 5000 });
    assert.match(await queued.page.locator('#cacheListaStatus').textContent(), /Atualizando cálculos/);
    assert.strictEqual(queued.state.calls.fiscal, 1);
    assert.strictEqual(queued.state.calls.cost, 0, 'A análise de custo posto aguarda a lista pronta');
    assert.strictEqual(queued.state.calls.margins, 0);
    assert.strictEqual(await queued.page.locator('#erro').textContent(), '');
    queued.state.ready = true;
    await queued.page.waitForFunction(() => ultimosItensCarregados[0]?.Quantidade === 4);
    await queued.page.waitForFunction(() => /^(Dados|Itens) atualizados/.test(document.getElementById('cacheListaStatus').textContent)
      && !/Atualizando/.test(document.getElementById('cacheListaStatus').textContent));
    assert.strictEqual(await queued.page.locator('tr[data-sku="001"] [data-col-key="quantidade"]').textContent(), '4');
    await queued.page.waitForFunction(() => /157,60/.test(document.querySelector('tr[data-sku="001"] [data-col-key="valor_unit_brl"]')?.textContent));
    assert.match(await queued.page.locator('tr[data-sku="001"] [data-col-key="valor_unit_brl"]').textContent(), /157,60/);
    assert.strictEqual(queued.state.calls.fiscal, 2, 'A versão pronta obtém a nova base após mudar quantidade e cotação');
    assert.strictEqual(await queued.page.evaluate(() => cacheWrites.some(entry => entry.data?._cache?.estado === 'waiting')), false,
      'Resposta 202 não substitui o snapshot válido');
    assert.deepStrictEqual(queued.state.errors, []);
    await queued.context.close();

    for (const listaEditada of ['B', 'A']) {
      const revisao = await environment(browser, { realCache: true, holdFirstReady: true, queuedDetail: true, complete: true });
      revisao.state.ready = true;
      revisao.state.prefs.resolve();
      await revisao.page.goto('http://jk.test/importacoes_lista.html?lista_id=A');
      const quantidade = revisao.page.locator('tr[data-sku="001"] [data-col-key="quantidade"]');
      await quantidade.waitFor();
      assert.strictEqual(await quantidade.textContent(), '2');
      await waitUntil(() => revisao.state.calls.polling === 1);
      const editor = revisao.page.locator('tr[data-sku="001"] .proporcao-sku-input');
      if (listaEditada === 'B') await editor.focus();
      const itens = structuredClone(revisao.state.lists[listaEditada].itens);
      itens[0].Quantidade = 9;
      const status = await revisao.page.evaluate(async ({ id, itens }) => {
        const resposta = await fetch('/api/medias-compras/listas-pedidos/' + id, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ itens }),
        });
        return resposta.status;
      }, { id: listaEditada, itens });
      assert.strictEqual(status, 200);
      revisao.state.detail.resolve();
      if (listaEditada === 'B') {
        await waitUntil(() => revisao.state.calls.polling === 2);
        assert.strictEqual(await quantidade.textContent(), '2', 'A reconsulta aguarda a edição ativa');
        assert.strictEqual(await editor.evaluate(campo => campo === document.activeElement), true);
        await editor.blur();
      }
      const quantidadeAtual = listaEditada === 'A' ? 9 : 4;
      await revisao.page.waitForFunction(esperada => ultimosItensCarregados[0]?.Quantidade === esperada, quantidadeAtual);
      assert.strictEqual(await quantidade.textContent(), String(quantidadeAtual));
      assert.strictEqual(revisao.state.calls.polling, 2);
      assert.strictEqual(revisao.state.calls.saved, 1, 'A reconsulta preserva a cópia já visível');
      if (listaEditada === 'A') {
        const podeManter = await revisao.page.evaluate(() => {
          const contexto = JKCachePersistente.contexto();
          const usuario = JSON.parse(localStorage.getItem('user_data'));
          localStorage.setItem('user_data', JSON.stringify({ ...usuario, store_id: 'store-B' }));
          const mesmaAutorizacao = JKCachePersistente.podeManterDados(contexto);
          window.dispatchEvent(new StorageEvent('storage', { key: 'user_data' }));
          return mesmaAutorizacao;
        });
        assert.strictEqual(podeManter, false);
        assert.strictEqual(await revisao.page.locator('tr[data-sku]').count(), 0);
      }
      assert.deepStrictEqual(revisao.state.errors, []);
      await revisao.context.close();
    }

    for (const status of [401, 403]) {
      const revogacao = await environment(browser, { realCache: true, holdFirstReady: true, complete: true });
      await revogacao.page.goto('http://jk.test/importacoes_lista.html?lista_id=A');
      await revogacao.page.locator('tr[data-sku="001"]').waitFor();
      await waitUntil(() => revogacao.state.calls.polling === 1);
      revogacao.state.deniedStatus = status;
      const podeManter = await revogacao.page.evaluate(async () => {
        const contexto = JKCachePersistente.contexto();
        await JKCachePersistente.invalidar(['importacoes']);
        return JKCachePersistente.podeManterDados(contexto);
      });
      assert.strictEqual(podeManter, false);
      revogacao.state.detail.resolve();
      await revogacao.page.waitForFunction(() => document.getElementById('cacheListaStatus').textContent
        .startsWith('Acesso expirado ou negado.'));
      assert.strictEqual(await revogacao.page.locator('tr[data-sku]').count(), 0);
      assert.strictEqual(revogacao.state.calls.cost, 0);
      assert.deepStrictEqual(revogacao.state.errors, []);
      await revogacao.context.close();
    }

    const denied = await environment(browser, { denyDetail: true, complete: true, cacheStub: true });
    await denied.page.goto('http://jk.test/importacoes_lista.html?lista_id=A');
    await denied.page.waitForFunction(() => window.cacheInvalidations > 0);
    assert.strictEqual(await denied.page.locator('#tbodyLista tr[data-sku]').count(), 0);
    assert.strictEqual(denied.state.calls.cost, 0);
    assert.strictEqual(denied.state.patches.length, 0);
    assert.deepStrictEqual(denied.state.errors, []);
    await denied.context.close();

    const oldDenied = await environment(browser, { oldResponses: true, complete: true, cacheStub: true });
    oldDenied.state.savedLists.A = prepararDadosCusto(fixture('A', true));
    prepararDadosCusto(oldDenied.state.lists.B);
    oldDenied.state.prefs.resolve();
    await oldDenied.page.goto('http://jk.test/importacoes_lista.html?lista_id=A');
    await oldDenied.page.locator('#tbodyLista tr[data-sku="001"]').waitFor();
    await oldDenied.page.waitForFunction(() => typeof releaseOldPolling === 'function' && typeof releaseOldFiscal === 'function');
    await oldDenied.page.evaluate(() => { history.replaceState({}, '', '?lista_id=B'); return carregarDetalheLista(); });
    const custoListaNova = 'tr[data-sku="001"] [data-col-key="valor_unit_brl"]';
    await oldDenied.page.waitForFunction(seletor => /131,33/.test(document.querySelector(seletor)?.textContent), custoListaNova);
    await oldDenied.page.evaluate(async () => { releaseOldPolling(); releaseOldFiscal(); await new Promise(resolve => setTimeout(resolve, 0)); });
    assert.strictEqual(await oldDenied.page.locator('#titulo').textContent(), 'Importações - Lista B');
    assert.strictEqual(await oldDenied.page.locator('#tbodyLista tr[data-sku]').count(), 3);
    assert.match(await oldDenied.page.locator(custoListaNova).textContent(), /131,33/, 'Resposta fiscal antiga não altera o custo da lista nova');
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
      assert.strictEqual(await pendingMargins.page.evaluate(() => ultimaListaCarregada.itens[0].analise_simulacao_importacao_sku), undefined,
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
