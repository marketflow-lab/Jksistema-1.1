'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const host = 'http://jk.test/medias_compras.html';

async function fixture(browser, options = {}) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  page.setDefaultTimeout(3000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(options => {
    const tenant = 'tenant-performance';
    localStorage.setItem('user_data', JSON.stringify({ client_id: tenant, username: 'synthetic-user' }));
    localStorage.setItem('access_token', 'synthetic-token');
    if (options.fail === 'prefs') localStorage.setItem('medias_compras_skus_ocultos_' + tenant, '["SKU-HIDDEN"]');
    const nativeFetch = window.fetch.bind(window);
    const calls = [], plans = new Map(), pending = new Map(), flashes = [];
    const item = sku => ({ sku, titulo_anuncio: sku, vendas_mensais: {}, total_vendas_periodo: 4,
      media_mensal: 1, saldo_atual_estoque: 5, estoque_em_transito: 0, posicao_estoque: 5, compra_sugerida: 0 });
    const summary = store => ({ id: store || 'all', nome_lista: 'Pedido ' + (store || 'all'), loja: store,
      store_id: store, itens: [], skus: [], total_itens: 0 });
    const plan = (key, value) => { const queue = plans.get(key) || []; queue.push(value); plans.set(key, queue); };
    if (options.holdInit) { plan('prefs', { phase: 'fetch' }); plan('stores', { phase: 'fetch' }); }
    if (options.fail) plan(options.fail, { status: 503 });
    new MutationObserver(() => {
      if (document.querySelector('#tblResultado tbody')?.textContent.includes('SKU-HIDDEN')) flashes.push(true);
    }).observe(document, { subtree: true, childList: true, characterData: true });
    window.fetch = async (input, init = {}) => {
      const url = new URL(String(input), location.href);
      if (!url.pathname.startsWith('/api/')) return nativeFetch(input, init);
      const headers = new Headers(init.headers);
      if (headers.get('authorization') !== 'Bearer synthetic-token' || headers.get('x-client-id') !== tenant) throw new Error('Missing synthetic tenant/auth');
      const method = init.method || 'GET';
      if (!['GET', 'PATCH', 'DELETE'].includes(method)) throw new Error('Unexpected mutation');
      const store = url.searchParams.get('store_id') || '';
      let key, body;
      if (method === 'PATCH' && url.pathname.endsWith('/status')) { key = 'status'; body = { lista: { ...summary('store-a'), status: 'Pedido Aprovado' } }; }
      else if (method === 'DELETE' && url.pathname.endsWith('/store-a')) { key = 'delete'; body = { success: true }; }
      else if (url.pathname === '/api/lojas') { key = 'stores'; body = [{ nome: 'Loja A', store_id: 'store-a' }, { nome: 'Loja B', store_id: 'store-b' }]; }
      else if (url.pathname.endsWith('/preferencias-skus-ocultos')) { key = 'prefs'; body = { skus_ocultos: ['SKU-HIDDEN'] }; }
      else if (url.pathname.endsWith('/visao')) { key = 'view:' + store + ':' + url.searchParams.get('meses'); body = { colunas_meses: [], itens: [item('SKU-VISIBLE'), item('SKU-HIDDEN')] }; }
      else if (url.pathname.endsWith('/listas-pedidos')) { key = 'summaries:' + store; body = { listas: [summary(store)] }; }
      else throw new Error('Unexpected API: ' + url.pathname);
      const selected = (plans.get(key) || []).shift() || {};
      const call = { index: calls.length, key, aborted: false, finished: false, hasSignal: !!init.signal };
      calls.push(call);
      const wait = () => new Promise((resolve, reject) => pending.set(call.index, { resolve, reject }));
      init.signal?.addEventListener('abort', () => {
        call.aborted = true;
        if (!selected.ignoreAbort && pending.has(call.index)) {
          pending.get(call.index).reject(new DOMException('Synthetic abort', 'AbortError'));
          pending.delete(call.index);
        }
      });
      if (selected.phase === 'fetch') await wait();
      return { ok: (selected.status || 200) < 400, status: selected.status || 200, headers: new Headers(),
        json: async () => {
          if (selected.phase === 'body') await wait();
          call.finished = true;
          return selected.body || body;
        } };
    };
    window.__performanceHarness = { calls, flashes, plan,
      release(index, error) {
        const current = pending.get(index);
        if (!current) throw new Error('No pending request ' + index);
        pending.delete(index);
        if (error) current.reject(new Error(error)); else current.resolve();
      }, pending: () => [...pending.keys()] };
  }, options);
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://jk.test') return route.fulfill({ status: 200, contentType: 'text/css', body: '' });
    if (url.pathname === '/medias_compras.html') return route.fulfill({ status: 200, contentType: 'text/html', body: fs.readFileSync(path.join(root, 'static/medias_compras.html'), 'utf8') });
    if (url.pathname.startsWith('/medias_compras/')) return route.fulfill({ status: 200, contentType: url.pathname.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(path.join(root, 'static', url.pathname.slice(1)), 'utf8') });
    if (url.pathname === '/auth.js') return route.fulfill({ status: 200, contentType: 'application/javascript', body: 'window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({Authorization:"Bearer synthetic-token","X-Client-ID":"tenant-performance"});' });
    return route.fulfill({ status: 200, contentType: 'application/javascript', body: '' });
  });
  await page.goto(host);
  if (!options.holdInit) await ready(page);
  return { page, context, errors };
}
async function ready(page) { await page.waitForFunction(() => document.querySelector('#status').textContent === '' && document.querySelector('#tblResultado tbody').textContent.includes('SKU-VISIBLE')); }
async function settle(page) { await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))); }
async function calls(page, prefix) { return page.evaluate(prefix => __performanceHarness.calls.filter(call => call.key.startsWith(prefix)), prefix); }
async function plan(page, key, value) { await page.evaluate(({ key, value }) => __performanceHarness.plan(key, value), { key, value }); }
async function release(page, index, error) { await page.evaluate(({ index, error }) => __performanceHarness.release(index, error), { index, error }); await settle(page); }
async function store(page, name) { await page.locator('#lojaBotoes button').filter({ hasText: 'Loja ' + name }).click(); }
async function lists(page) { await page.locator('#btnAbaListasPedidos').click(); await page.locator('.lista-item').waitFor(); }
async function assertClean(test) { assert.deepStrictEqual(test.errors, []); assert.strictEqual((await test.page.evaluate(() => __performanceHarness.flashes)).length, 0); }

async function testParallelInit(browser) {
  const test = await fixture(browser, { holdInit: true });
  try {
    await test.page.waitForFunction(() => __performanceHarness.calls.some(call => call.key === 'stores'), null, { timeout: 700 });
    assert.strictEqual((await calls(test.page, 'view:')).length, 0);
    await release(test.page, 0);
    assert.strictEqual((await calls(test.page, 'view:')).length, 0, 'Preferences alone must not start view.');
    await release(test.page, 1);
    await ready(test.page);
    assert.strictEqual((await calls(test.page, 'view:')).length, 1);
    await assertClean(test);
  } finally { await test.context.close(); }
  for (const fail of ['prefs', 'stores']) {
    const failed = await fixture(browser, { fail });
    try { assert.strictEqual((await calls(failed.page, '')).length, 3); await assertClean(failed); }
    finally { await failed.context.close(); }
  }
}
async function testInitInteractionGate(browser) {
  const test = await fixture(browser, { holdInit: true });
  try {
    await test.page.waitForFunction(() => __performanceHarness.calls.length === 2);
    await release(test.page, 1);
    await store(test.page, 'A'); await test.page.locator('#btnPeriodo12').click(); await settle(test.page);
    assert.strictEqual((await calls(test.page, 'view:')).length, 0, 'Store/period clicks must await preferences.');
    await release(test.page, 0); await ready(test.page);
    assert.strictEqual((await calls(test.page, 'view:store-a:12')).length, 1);
    await assertClean(test);
  } finally { await test.context.close(); }
}
async function testLateMutationRefreshesCurrentStore(browser) {
  for (const action of ['status', 'delete']) {
    const test = await fixture(browser);
    try {
      await lists(test.page); await store(test.page, 'A');
      await test.page.locator('.lista-item').filter({ hasText: 'Pedido store-a' }).waitFor();
      await plan(test.page, action, { phase: 'fetch' });
      await test.page.evaluate(action => {
        window.confirm = () => true;
        if (action === 'status') void atualizarStatusListaPedido('store-a', 'Pedido Aprovado');
        else void excluirListaPedido('store-a', 'Pedido A', 'Lista gerada');
      }, action); await settle(test.page);
      const mutation = (await calls(test.page, action)).at(-1);
      await plan(test.page, 'summaries:store-b', { phase: 'body', ignoreAbort: true });
      await store(test.page, 'B'); await settle(test.page);
      const previous = (await calls(test.page, 'summaries:store-b')).at(-1);
      assert.strictEqual(await test.page.locator('.lista-item').count(), 0, 'Previous store cards must not remain clickable while loading another store.');
      assert.strictEqual(await test.page.evaluate(() => listasPedidosResumo.length), 0);
      await release(test.page, mutation.index);
      await test.page.locator('.lista-item').filter({ hasText: 'Pedido store-b' }).waitFor();
      await release(test.page, previous.index);
      assert.deepStrictEqual(await test.page.evaluate(() => listasPedidosResumo.map(item => item.store_id)), ['store-b']);
      assert(!(await test.page.locator('#statusListaPedido').innerText()).startsWith('Carregando'));
      await assertClean(test);
    } finally { await test.context.close(); }
  }
}
async function testActiveTab(browser) {
  const test = await fixture(browser);
  try {
    await lists(test.page);
    const before = (await calls(test.page, 'view:')).length;
    await store(test.page, 'A');
    await test.page.locator('.lista-item').filter({ hasText: 'Pedido store-a' }).waitFor();
    await test.page.locator('#btnPeriodo12').click(); await settle(test.page);
    assert.strictEqual((await calls(test.page, 'view:')).length, before, 'Lists must not load hidden view.');
    await test.page.locator('#btnAbaLista').click(); await ready(test.page);
    assert.strictEqual((await calls(test.page, 'view:store-a:12')).length, 1);
    await test.page.locator('#btnAbaOcultados').click(); await settle(test.page);
    assert.strictEqual((await calls(test.page, 'view:')).length, before + 1);
    assert.deepStrictEqual(test.errors, []);
  } finally { await test.context.close(); }
}
async function testNoOldScopeFlash(browser) {
  const test = await fixture(browser);
  try {
    await lists(test.page); await store(test.page, 'A');
    await test.page.locator('.lista-item').filter({ hasText: 'Pedido store-a' }).waitFor();
    await plan(test.page, 'view:store-a:6', { phase: 'body', ignoreAbort: true });
    await test.page.locator('#btnAbaLista').click(); await settle(test.page);
    assert(!(await test.page.locator('#tblResultado tbody').textContent()).includes('SKU-VISIBLE'), 'Previous store must not appear under the new filter.');
    assert.strictEqual(await test.page.evaluate(() => itensVisaoAtual.length), 0, 'Purchase actions must not use rows from the previous store.');
    const pending = (await calls(test.page, 'view:store-a:6')).at(-1);
    await release(test.page, pending.index); await ready(test.page);
    await plan(test.page, 'view:store-a:6', { phase: 'body', ignoreAbort: true });
    await store(test.page, 'A'); await settle(test.page);
    const sameScope = (await calls(test.page, 'view:store-a:6')).at(-1);
    assert(sameScope.index > pending.index, 'Clicking current store after completion must still allow a fresh read.');
    assert((await test.page.locator('#tblResultado tbody').textContent()).includes('SKU-VISIBLE'), 'Same-scope refresh should preserve visible rows.');
    await release(test.page, sameScope.index); await ready(test.page);
    await assertClean(test);
  } finally { await test.context.close(); }
}
async function testDedupe(browser) {
  const test = await fixture(browser);
  try {
    const before = (await calls(test.page, 'view:')).length;
    await plan(test.page, 'view::6', { phase: 'body', ignoreAbort: true });
    await test.page.evaluate(() => { void carregarVisao(6); void carregarVisao(6); void carregarVisao(6); });
    await settle(test.page);
    const pending = await calls(test.page, 'view:');
    assert.strictEqual(pending.length, before + 1, 'Same view must share pending work.');
    await release(test.page, pending.at(-1).index); await ready(test.page);
    await lists(test.page);
    const count = (await calls(test.page, 'summaries:')).length;
    await plan(test.page, 'summaries:', { phase: 'body', ignoreAbort: true });
    await test.page.evaluate(() => { void carregarListasPedidos(); void carregarListasPedidos(); void carregarListasPedidos(); });
    await settle(test.page);
    const summaries = await calls(test.page, 'summaries:');
    assert.strictEqual(summaries.length, count + 1, 'Same summary must share pending work.');
    assert.strictEqual(await test.page.locator('.lista-item').count(), 1, 'Same-scope refresh should preserve cards.');
    await release(test.page, summaries.at(-1).index);
    await assertClean(test);
  } finally { await test.context.close(); }
}
async function testSummaryRace(browser) {
  for (const phase of ['fetch', 'body', 'error']) {
    const test = await fixture(browser);
    try {
      await lists(test.page);
      await plan(test.page, 'summaries:store-a', { phase: phase === 'fetch' ? 'fetch' : 'body', ignoreAbort: true });
      await store(test.page, 'A'); await settle(test.page);
      const old = (await calls(test.page, 'summaries:store-a')).at(-1);
      await store(test.page, 'B');
      await test.page.locator('.lista-item').filter({ hasText: 'Pedido store-b' }).waitFor();
      const status = await test.page.locator('#statusListaPedido').innerText();
      await release(test.page, old.index, phase === 'error' ? 'Old failure' : undefined);
      assert.strictEqual(await test.page.evaluate(() => storeIdSelecionado), 'store-b');
      assert.deepStrictEqual(await test.page.evaluate(() => listasPedidosResumo.map(item => item.store_id)), ['store-b']);
      assert.strictEqual(await test.page.locator('#statusListaPedido').innerText(), status);
      assert.strictEqual((await calls(test.page, 'summaries:store-a')).at(-1).aborted, true);
      await assertClean(test);
    } finally { await test.context.close(); }
  }
}
async function testForceRefresh(browser) {
  const test = await fixture(browser);
  try {
    await lists(test.page);
    await plan(test.page, 'summaries:', { phase: 'body', ignoreAbort: true, body: { listas: [{ id: 'old', nome_lista: 'Old summary', itens: [] }] } });
    await test.page.evaluate(() => { void carregarListasPedidos(); }); await settle(test.page);
    const old = (await calls(test.page, 'summaries:')).at(-1);
    await test.page.evaluate(() => carregarListasPedidos(true));
    await release(test.page, old.index);
    assert.deepStrictEqual(await test.page.evaluate(() => listasPedidosResumo.map(item => item.id)), ['all']);
    assert.strictEqual((await calls(test.page, 'summaries:')).at(-2).aborted, true);
    await assertClean(test);
  } finally { await test.context.close(); }
}
async function testCancelViewAndInitNavigation(browser) {
  const test = await fixture(browser);
  try {
    await plan(test.page, 'view::6', { phase: 'body', ignoreAbort: true });
    await test.page.evaluate(() => { void carregarVisao(6); }); await settle(test.page);
    const old = (await calls(test.page, 'view:')).at(-1);
    await lists(test.page);
    assert.strictEqual((await calls(test.page, 'view:')).at(-1).aborted, true);
    await release(test.page, old.index);
    assert.strictEqual(await test.page.evaluate(() => abaAtual), 'listas_pedidos');
    await assertClean(test);
  } finally { await test.context.close(); }
  const initial = await fixture(browser, { holdInit: true });
  try {
    await initial.page.waitForFunction(() => __performanceHarness.calls.length === 2);
    await lists(initial.page); await initial.page.locator('#btnPeriodo12').click();
    await release(initial.page, 0); await release(initial.page, 1);
    assert.strictEqual((await calls(initial.page, 'view:')).length, 0);
    await initial.page.locator('#btnAbaLista').click(); await ready(initial.page);
    assert.strictEqual((await calls(initial.page, 'view::12')).length, 1);
    await assertClean(initial);
  } finally { await initial.context.close(); }
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  let failures = 0;
  try {
    for (const test of [testParallelInit, testInitInteractionGate, testActiveTab, testNoOldScopeFlash, testDedupe, testSummaryRace, testForceRefresh, testLateMutationRefreshesCurrentStore, testCancelViewAndInitNavigation]) {
      try { await test(browser); console.log('PASS ' + test.name); }
      catch (error) { failures++; console.error('FAIL ' + test.name + ': ' + error.message); }
    }
  } finally { await browser.close(); }
  if (failures) process.exitCode = 1;
})().catch(error => { console.error(error); process.exitCode = 1; });
