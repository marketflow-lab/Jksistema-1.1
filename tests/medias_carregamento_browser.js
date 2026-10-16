'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const host = 'http://jk.test/medias_compras.html';
const html = fs.readFileSync(path.join(root, 'static/medias_compras.html'), 'utf8');
const knownCodes = ['stores_snapshot_initializing', 'stores_snapshot_unavailable', 'stores_snapshot_credentials_unavailable'];
const stores = [{ nome: 'Loja A', store_id: 'store-a' }, { nome: 'Loja B', store_id: 'store-b' }];

function unavailable(code, message = 'Configuração sintética em preparação.', retryAfter = '2', status = 503) {
  return { status, body: { detail: { code, message } }, headers: { 'Retry-After': retryAfter } };
}

function itemSku(tenant, request) {
  return tenant + '-' + (request.storeId || 'TODAS') + '-' + request.months;
}

function success(tenant, request) {
  return { body: { colunas_meses: [], itens: [{
    sku: itemSku(tenant, request), titulo_anuncio: 'Produto sintético isolado', vendas_mensais: {},
    total_vendas_periodo: 4, media_mensal: 1, saldo_atual_estoque: 5,
    estoque_em_transito: 0, posicao_estoque: 5, compra_sugerida: 0,
  }], _cache: { estado: 'ready', stale: false, atualizando: false, completo: true,
    versao: itemSku(tenant, request), gerado_em: new Date().toISOString(), autoridade: tenant + '-authority' } } };
}

async function openFixture(browser, { tenant = 'tenant-a', timers = 'fast', respond } = {}) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  const state = { tenant, calls: [], errors: [], routeErrors: [] };
  page.on('pageerror', error => state.errors.push(error.message));
  await page.addInitScript(({ tenant, timers }) => {
    localStorage.setItem('user_data', JSON.stringify({ client_id: tenant, username: 'synthetic-user' }));
    localStorage.setItem('access_token', 'synthetic-' + tenant);
    const original = window.setTimeout.bind(window), originalClear = window.clearTimeout.bind(window);
    const nativeNow = Date.now.bind(Date), nativeFetch = fetch.bind(window);
    const scheduled = new Map();
    let id = 0, clock = nativeNow(), inFlight = 0;
    if (timers !== 'real') Date.now = () => clock;
    const delays = [];
    const pending = () => inFlight ? [] : [...scheduled].filter(([, timer]) => timer.delay <= 5000);
    const release = key => {
      const timer = scheduled.get(key);
      if (!timer) return;
      scheduled.delete(key);
      clock = Math.max(clock, timer.at);
      delays.push(timer.delay);
      timer.callback();
    };
    window.__retryTimers = { delays, get pending() { return pending().map(([key]) => () => release(key)); },
      until: async predicate => {
        for (let turn = 0; turn < 1000; turn++) {
          if (predicate()) return;
          if (timers === 'fast') {
            const next = pending().sort((a, b) => a[1].at - b[1].at)[0];
            if (next) release(next[0]);
          }
          await new Promise(resolve => original(resolve, 5));
        }
        throw new Error('A fixture não atingiu o estado esperado dentro do prazo.');
      } };
    window.fetch = async (...args) => {
      inFlight++;
      let active = true;
      const done = () => { if (active) { active = false; inFlight--; } };
      try {
        const response = await nativeFetch(...args);
        const json = response.json.bind(response);
        response.json = async () => { try { return await json(); } finally { done(); } };
        return response;
      } catch (error) { done(); throw error; }
    };
    window.setTimeout = (callback, delay, ...args) => {
      if (Number(delay) >= 1000) {
        if (timers === 'real') return original(() => {
          if (delay <= 5000) delays.push(delay);
          callback(...args);
        }, delay);
        const key = --id;
        scheduled.set(key, { delay: Number(delay), at: clock + Number(delay), callback: () => callback(...args) });
        return key;
      }
      return original(callback, delay, ...args);
    };
    window.clearTimeout = key => key < 0 ? scheduled.delete(key) : originalClear(key);
  }, { tenant, timers });
  await page.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    try {
      if (url.origin !== 'http://jk.test') {
        return route.fulfill({ status: 200, contentType: 'text/css', body: '' });
      }
      if (url.pathname === '/medias_compras.html') {
        return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: html });
      }
      if (url.pathname.startsWith('/medias_compras/')) {
        return route.fulfill({ status: 200,
          contentType: url.pathname.endsWith('.css') ? 'text/css' : 'application/javascript',
          body: fs.readFileSync(path.join(root, 'static', url.pathname.slice(1))) });
      }
      if (url.pathname === '/auth.js') {
        return route.fulfill({ status: 200, contentType: 'application/javascript', body:
          'window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({' +
          'Authorization:"Bearer synthetic-' + tenant + '","X-Client-ID":"' + tenant + '"});' });
      }
      if (url.pathname.startsWith('/api/')) {
        assert.strictEqual(request.method(), 'GET', 'A recuperação não pode executar mutações.');
        assert.strictEqual(request.headers().authorization, 'Bearer synthetic-' + tenant);
        assert.strictEqual(request.headers()['x-client-id'], tenant);
        let response;
        if (url.pathname === '/api/lojas') response = { body: stores };
        else if (url.pathname === '/api/medias-compras/preferencias-skus-ocultos') {
          response = { body: { skus_ocultos: [] } };
        } else if (url.pathname === '/api/medias-compras/visao') {
          const call = { url: request.url(), months: Number(url.searchParams.get('meses')),
            storeId: url.searchParams.get('store_id') || '', storeName: url.searchParams.get('loja') || '', at: Date.now() };
          assert([3, 6, 12].includes(call.months));
          if (call.storeId) assert.strictEqual(stores.find(store => store.store_id === call.storeId)?.nome, call.storeName);
          else assert.strictEqual(call.storeName, '');
          assert.deepStrictEqual([...url.searchParams.keys()].sort(), call.storeId ? ['cache_mode', 'loja', 'meses', 'store_id'] : ['cache_mode', 'meses']);
          assert.strictEqual(url.searchParams.get('cache_mode'), 'stale');
          state.calls.push(call);
          response = respond ? await respond(call, state) : success(tenant, call);
        } else throw new Error('Rota de API inesperada na fixture: ' + url.pathname);
        return route.fulfill({ status: response.status || 200, contentType: 'application/json',
          headers: response.headers || {}, body: JSON.stringify(response.body) });
      }
      return route.fulfill({ status: 200, contentType: 'application/javascript', body: '' });
    } catch (error) {
      state.routeErrors.push(error.message);
      return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: error.message }) });
    }
  });
  await page.goto(host);
  return { page, context, state };
}

async function ready(page, sku) {
  await page.evaluate(expected => __retryTimers.until(() => document.querySelector('#status')?.textContent.startsWith('Dados atualizados em ') &&
    document.querySelector('#tblResultado tbody')?.textContent.includes(expected)), sku);
}

async function failed(page, message) {
  await page.evaluate(expected => __retryTimers.until(() => document.querySelector('#status')?.textContent ===
    'Falha ao carregar dados: ' + expected), message);
  assert(!((await page.locator('#status').innerText()).includes('[object Object]')));
}

async function assertClean({ page, state }) {
  assert.strictEqual(page.url(), host);
  assert.deepStrictEqual(state.errors, []);
  assert.deepStrictEqual(state.routeErrors, []);
}

async function releaseWaitingRetries(page) {
  await page.evaluate(async () => {
    for (const release of window.__retryTimers.pending) release();
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  });
}

async function testRecoveryAndIsolation(browser) {
  const recoverable = knownCodes.map(code => ({ code, retryAfter: '2' })).concat([
    { code: knownCodes[0], retryAfter: '11' },
    { code: knownCodes[0], retryAfter: new Date(Date.now() + 60000).toUTCString() },
  ]);
  for (const [index, { code, retryAfter }] of recoverable.entries()) {
    const tenant = 'tenant-recovery-' + index;
    const fixture = await openFixture(browser, { tenant, timers: index === 0 ? 'real' : 'fast',
      respond: (call, state) => state.calls.length === 1 ? unavailable(code, undefined, retryAfter) : success(tenant, call) });
    try {
      await ready(fixture.page, tenant + '-TODAS-12');
      assert.strictEqual(fixture.state.calls.length, 2);
      assert.strictEqual(fixture.state.calls[0].url, fixture.state.calls[1].url, 'Retry preserva período e loja solicitados.');
      assert.deepStrictEqual(await fixture.page.evaluate(() => window.__retryTimers.delays), [1000]);
      if (index === 0) assert(fixture.state.calls[1].at - fixture.state.calls[0].at >= 900,
        'O caso real precisa aguardar o primeiro intervalo de um segundo.');
      assert(!((await fixture.page.locator('#tblResultado tbody').innerText()).includes('tenant-recovery-' + (index + 1))));
      await assertClean(fixture);
    } finally { await fixture.context.close(); }
  }
  console.log('PASS 503: códigos temporários e Retry-After antigos seguem polling de 1 s; recuperação, escopo, URL e espera real preservados.');
}

async function testTerminalErrors(browser) {
  const cases = [
    unavailable('store_scope_unresolved', 'Selecione uma loja sintética.', '2', 409),
    unavailable(knownCodes[0], 'Conflito sintético não repetível.', '2', 409),
    unavailable('unknown_code', 'Indisponibilidade não permitida.'),
    { status: 503, body: { detail: 'Mensagem sintética simples.' } },
  ];
  for (const response of cases) {
    const fixture = await openFixture(browser, { respond: () => response });
    try {
      await failed(fixture.page, typeof response.body.detail === 'string' ? response.body.detail : response.body.detail.message);
      assert.strictEqual(fixture.state.calls.length, 1);
      assert.deepStrictEqual(await fixture.page.evaluate(() => window.__retryTimers.delays), []);
      await assertClean(fixture);
    } finally { await fixture.context.close(); }
  }
  console.log('PASS erros terminais: 409, código desconhecido e detalhe string não repetem nem exibem objeto bruto.');
}

async function testBoundedRetries(browser) {
  const fixture = await openFixture(browser, { respond: () => unavailable(knownCodes[0], 'Snapshot sintético continua indisponível.') });
  try {
    await fixture.page.evaluate(() => __retryTimers.until(() => document.querySelector('#status').textContent.includes('Atualização pendente')));
    assert(fixture.state.calls.length >= 12 && fixture.state.calls.length <= 13, 'Consultas ficam limitadas ao orçamento de 30 s, incluindo a fronteira final.');
    const callsAtDeadline = fixture.state.calls.length;
    assert.strictEqual(new Set(fixture.state.calls.map(call => call.url)).size, 1);
    assert.deepStrictEqual(await fixture.page.evaluate(() => window.__retryTimers.delays), [1000, 1000, 1000, 1000, 1000, 2000, 2000, 2000, 5000, 5000, 5000, 4000]);
    await releaseWaitingRetries(fixture.page);
    assert.strictEqual(fixture.state.calls.length, callsAtDeadline, 'A espera encerrada não pode reiniciar polling.');
    await assertClean(fixture);
  } finally { await fixture.context.close(); }
  console.log('PASS limite: 503 persistente usa polling 1/2/5 s até 30 s e termina com pendência honesta, sem novas consultas.');
}

async function testPeriodAndStoreChanges(browser) {
  const tenant = 'tenant-navigation';
  const fixture = await openFixture(browser, { tenant, timers: 'hold', respond: (call, state) =>
    (state.calls.length === 1 || call.storeId === 'store-a') ? unavailable(knownCodes[0]) : success(tenant, call) });
  try {
    await fixture.page.waitForFunction(() => window.__retryTimers?.pending.length === 1);
    await fixture.page.locator('#btnPeriodo6').click();
    await ready(fixture.page, tenant + '-TODAS-6');
    await releaseWaitingRetries(fixture.page);
    assert.deepStrictEqual(fixture.state.calls.map(call => call.months), [12, 6], 'O período anterior não pode repetir.');
    await fixture.page.locator('#lojaBotoes button').filter({ hasText: 'Loja A' }).click();
    await fixture.page.waitForFunction(() => window.__retryTimers?.pending.length === 1);
    await fixture.page.locator('#lojaBotoes button').filter({ hasText: 'Loja B' }).click();
    await ready(fixture.page, tenant + '-store-b-6');
    await releaseWaitingRetries(fixture.page);
    assert.deepStrictEqual(fixture.state.calls.map(call => [call.months, call.storeId]), [[12, ''], [6, ''], [6, 'store-a'], [6, 'store-b']]);
    assert.match(await fixture.page.locator('#status').innerText(), /^Dados atualizados em /);
    assert(!(await fixture.page.locator('#tblResultado tbody').innerText()).includes('store-a'));
    assert.strictEqual(await fixture.page.locator('#btnPeriodo6').evaluate(button => button.classList.contains('active')), true);
    await assertClean(fixture);
  } finally { await fixture.context.close(); }
  console.log('PASS geração: mudar período ou loja descarta repetições antigas sem sobrescrever resultado/status novos.');
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    await testRecoveryAndIsolation(browser);
    await testTerminalErrors(browser);
    await testBoundedRetries(browser);
    await testPeriodAndStoreChanges(browser);
  } finally { await browser.close(); }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
