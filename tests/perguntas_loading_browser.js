'use strict';

// Deterministic, synthetic integration fixture. No Mercado Livre traffic.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const http = require('http');
const { execFileSync } = require('child_process');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const baseline = process.argv.includes('--baseline');
const revision = '52c4b89';
const assets = new Map();
function readAsset(relative) {
  if (!assets.has(relative)) assets.set(relative, baseline
    ? execFileSync('git', ['show', `${revision}:static/${relative}`], { cwd: root, maxBuffer: 5 * 1024 * 1024 })
    : fs.readFileSync(path.join(root, 'static', relative)));
  return assets.get(relative);
}
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const stores = Array.from({ length: 11 }, (_, index) => ({
  nome: `Fixture ${String(index + 1).padStart(2, '0')}`,
  store_id: `fixture-${index + 1}`, seller_id: String(index + 101), site_id: 'MLB',
  mercadolivre_conectado: true, config_perguntas: { ativo: false, intervalo_minutos: 1440 },
}));
function questions(store, offset, limit) {
  const number = stores.indexOf(store) + 1;
  return Array.from({ length: Math.max(0, Math.min(limit, 60 - offset)) }, (_, i) => ({
    id: String(number * 1000 + offset + i), item_id: `MLB${number * 1000 + offset + i}`,
    text: 'Pergunta sintética de teste', status: 'UNANSWERED', from_id: number + 10000,
    date_created: new Date(Date.UTC(2026, 8, 8, 12) - ((offset + i) * 11 + number) * 60000).toISOString(),
    store_id: store.store_id, seller_id: store.seller_id, site_id: store.site_id, loja: store.nome,
    item_title: baseline ? `Anúncio fixture ${number}` : '', item_sku: baseline ? '001' : '',
  }));
}

async function fixture(browser) {
  const calls = [];
  const errors = [];
  const control = { slowMs: 900, fastMs: 35, deny: '', fail: '', listHold: null, counts: {} };
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    localStorage.setItem('token', 'synthetic-fixture-token');
    localStorage.setItem('permissions', JSON.stringify({ perguntas_pos_venda: true }));
    window.__loadingMetrics = { start: performance.now(), firstRows: null, firstStores: null };
    new MutationObserver(() => {
      const m = window.__loadingMetrics;
      if (!m.firstRows && document.querySelector('[data-question-select]')) m.firstRows = performance.now() - m.start;
      if (!m.firstStores && document.querySelector('#lojas-grid button')) m.firstStores = performance.now() - m.start;
    }).observe(document, { childList: true, subtree: true });
  });
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    const json = (payload, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) }).catch(() => {});
    if (url.pathname === '/auth.js') return route.fulfill({ contentType: 'application/javascript', body: 'window.verificarSessao=()=>true; window.obterAuthHeaders=()=>({Authorization:"Bearer "+localStorage.getItem("token")});' });
    if (url.pathname === '/ia-sidebar.js') return route.fulfill({ contentType: 'application/javascript', body: '' });
    if (url.pathname.startsWith('/api/')) {
      const call = { path: url.pathname, store: url.searchParams.get('store_id') || url.searchParams.get('loja'), offset: Number(url.searchParams.get('offset') || 0), limit: Number(url.searchParams.get('limit') || 20), start: Date.now() };
      calls.push(call);
      if (url.pathname.endsWith('/lojas')) return json({ lojas: stores });
      if (url.pathname === '/api/mercadolivre/perguntas' || url.pathname.endsWith('/perguntas/lista')) {
        const store = stores.find(s => s.store_id === call.store || s.nome === call.store);
        assert(store, 'consulta deve identificar uma loja da fixture');
        const payload = { questions: questions(store, call.offset, call.limit), total: 60, retornadas: Math.min(call.limit, 60 - call.offset), next_offset: call.offset + call.limit < 60 ? call.offset + call.limit : null, status_resumo: { UNANSWERED: 60 }, store_id: store.store_id, loja: store.nome, revision: 0, stale: false, partial: false, source: 'remote', consultado_em: Date.now(), fetched_at: new Date().toISOString() };
        if (control.listHold) await control.listHold;
        await delay(store === stores[9] ? control.slowMs : control.fastMs);
        call.end = Date.now();
        if (store.store_id === control.deny) return json({ detail: 'Acesso revogado na fixture' }, 403);
        if (store === stores[10] || store.store_id === control.fail) return json({ detail: 'Falha sintética' }, 503);
        return json(payload);
      }
      if (url.pathname.endsWith('/perguntas/itens')) {
        await delay(50);
        const ids = url.searchParams.getAll('item_ids').flatMap(value => value.split(','));
        const itens = ids.map(id => ({ id, item_id: id, item_title: `Anúncio ${id}`, item_thumbnail: '', item_permalink: '', item_sku: '001' }));
        return json({ itens, items: itens, store_id: call.store });
      }
      if (url.pathname.endsWith('/perguntas/detalhe')) {
        await delay(75);
        return json({ question: { buyer_name: 'Comprador fixture', buyer_question_chat: [], buyer_question_history_count: 1 }, store_id: call.store });
      }
      if (url.pathname.endsWith('/perguntas/resumo')) return json({ lojas: stores.map(s => ({ ...s, perguntas: s === stores[10] ? null : control.counts[s.store_id] ?? 60, total: s === stores[10] ? null : 60, status_resumo: { UNANSWERED: 60 }, erro: s === stores[10] ? 'Falha sintética' : null })), partial: true });
      if (url.pathname.endsWith('/perguntas/responder')) return json({ success: true, resposta: 'Resposta sintética confirmada' });
      if (url.pathname.endsWith('/ia-treinamento')) return json({ success: true, store_id: call.store, exemplos: {}, notas_sku: {}, orientacoes: '' });
      return json({ success: true, lojas: [], produtos: [], status: 'idle' });
    }
    if (url.origin !== fixture.origin) return route.abort();
    return route.continue();
  });
  return { context, page, calls, errors, control };
}

async function main() {
  if (!baseline) await verifyPager();
  // Exclude git extraction/disk asset reads from the measured navigation in both modes.
  readAsset('perguntas_pos_venda.html');
  for (const file of ['runtime.js', 'lojas-automacao.js', 'perguntas.js', 'pos-venda.js', 'mediacao.js', 'treinamento-ia.js', 'init.js', 'styles.css']) readAsset(`perguntas_pos_venda/${file}`);
  if (!baseline) readAsset('perguntas_pos_venda/loading.js');
  const server = http.createServer((req, res) => {
    try {
      const relative = decodeURIComponent(new URL(req.url, 'http://fixture').pathname).replace(/^\/+/, '') || 'perguntas_pos_venda.html';
      assert(!relative.includes('..'));
      const body = readAsset(relative);
      res.writeHead(200, { 'content-type': relative.endsWith('.js') ? 'application/javascript' : relative.endsWith('.css') ? 'text/css' : 'text/html' });
      res.end(body);
    } catch (_) { res.writeHead(404); res.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  fixture.origin = `http://127.0.0.1:${server.address().port}`;
  const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE;
  const browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : { channel: 'chrome' }) });
  try {
    const f = await fixture(browser);
    const { page, calls, errors } = f;
    await page.goto(`${fixture.origin}/perguntas_pos_venda.html`, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.querySelectorAll('[data-question-select]').length === 20);
    const first = await page.evaluate(() => window.__loadingMetrics);
    const slow = calls.find(call => call.store === 'Fixture 10' || call.store === 'fixture-10');
    if (baseline) assert(slow.end, 'baseline espera pela loja lenta');
    else assert(!slow?.end, 'primeira lista deve aparecer antes da loja lenta');
    await page.waitForFunction(() => !state.carregandoPerguntas);
    await delay(1100);
    const initialCalls = calls.filter(c => c.path === '/api/mercadolivre/perguntas' || c.path.endsWith('/perguntas/lista') || c.path.endsWith('/perguntas/resumo')).length;
    const page1 = await page.evaluate(() => state.perguntas.map(p => p.id));
    await page.evaluate(() => carregarPerguntas(2));
    const page2 = await page.evaluate(() => state.perguntas.map(p => p.id));
    assert(!page1.some(id => page2.includes(id)), 'páginas não devem repetir perguntas');
    const listReads = () => calls.filter(c => c.path === '/api/mercadolivre/perguntas' || c.path.endsWith('/perguntas/lista')).length;
    const callsBeforeBack = listReads();
    const backStart = Date.now();
    await page.evaluate(() => { void carregarPerguntas(1); });
    await page.waitForFunction(ids => JSON.stringify(state.perguntas.map(p => p.id)) === JSON.stringify(ids), page1);
    const backMs = Date.now() - backStart;
    if (!baseline) assert(backMs <= 200, `retorno do cache excedeu 200 ms: ${backMs}`);
    await page.waitForFunction(() => !state.carregandoPerguntas);
    const callsOnBack = listReads() - callsBeforeBack;
    if (!baseline) assert.strictEqual(callsOnBack, 0, 'voltar página não repete consultas de blocos');
    if (!baseline) await verifyUpdated(f);
    assert.deepStrictEqual(errors, [], 'erros de JavaScript no navegador');
    console.log(JSON.stringify({ mode: baseline ? `baseline ${revision}` : 'updated', firstRowsMs: Math.round(first.firstRows), initialReadCalls: initialCalls, backMs, callsOnBack, passed: true }));
    await f.context.close();
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}

async function verifyPager() {
  const { QuestionPager, compareQuestions } = require('../static/perguntas_pos_venda/loading.js');
  const blocks = [];
  let active = 0, maxActive = 0;
  const pager = new QuestionPager(stores, async (store, offset) => {
    active++; maxActive = Math.max(maxActive, active);
    try {
      await delay(2);
      blocks.push(`${store.store_id}:${offset}`);
      const all = questions(store, 0, 60).map((question, index) => ({
        ...question,
        // Store 1 dominates multiple pages, forcing refills while other stores have buffers.
        date_created: new Date(Date.UTC(2026, 8, 8) - (stores.indexOf(store) * 100 + index) * 60000).toISOString(),
      }));
      return { questions: all.slice(offset, offset + 20), total: all.length, next_offset: offset + 20 < all.length ? offset + 20 : null };
    } finally { active--; }
  });
  const pages = [];
  for (let number = 1; number <= 4; number++) pages.push(...await pager.page(number));
  assert.strictEqual(new Set(pages.map(q => `${q.store_id}:${q.id}`)).size, 80, 'buffers não podem duplicar perguntas');
  assert.deepStrictEqual([...pages].sort(compareQuestions), pages, 'refill deve manter ordenação global');
  assert.deepStrictEqual(blocks.filter(key => key.startsWith('fixture-1:')), ['fixture-1:0', 'fixture-1:20', 'fixture-1:40']);
  assert.strictEqual(new Set(blocks).size, blocks.length, 'cada bloco é consultado uma única vez');
  assert(maxActive <= 4, 'consultas de lojas devem respeitar limite quatro');
  const before = blocks.length;
  await pager.page(1);
  assert.strictEqual(blocks.length, before, 'página anterior deve reaproveitar blocos');
}

async function verifyUpdated(f) {
  const { page, calls, control } = f;
  const listCalls = calls.filter(c => c.path.endsWith('/perguntas/lista'));
  assert(listCalls.every(c => /^fixture-/.test(c.store)), 'lista deve enviar store_id canônico');
  assert(!calls.some(c => c.path === '/api/mercadolivre/perguntas'), 'interface não deve usar consulta pesada');
  await page.evaluate(() => { void selecionarLoja('Fixture 10'); void selecionarLoja('Fixture 01'); });
  await page.waitForFunction(() => state.lojaSelecionada === 'Fixture 01' && state.perguntas.length > 0 && !state.carregandoPerguntas);
  await delay(1000);
  assert(await page.evaluate(() => state.perguntas.every(p => p.store_id === 'fixture-1')), 'resposta antiga contaminou a loja atual');
  const input = page.locator('.question-answer-text');
  await input.fill('Rascunho sintético preservado');
  await input.focus();
  await page.evaluate(() => carregarPerguntas(1, { background: true, preservarInteracao: true, forcar: true }));
  assert.strictEqual(await input.inputValue(), 'Rascunho sintético preservado');
  assert(await input.evaluate(el => el === document.activeElement), 'atualização deve preservar foco');
  control.fail = 'fixture-1';
  await page.evaluate(() => carregarPerguntas(1, { forcar: true, background: true }));
  assert.strictEqual(await page.locator('[data-question-select]').count(), 20, '503 preserva dados autorizados em cache');
  assert.strictEqual(await input.inputValue(), 'Rascunho sintético preservado');
  control.fail = '';
  // Capture a pre-send list response, confirm a manual send, then release the old response.
  let releaseList;
  control.listHold = new Promise(resolve => { releaseList = resolve; });
  const previousCalls = calls.filter(c => c.path.endsWith('/perguntas/lista')).length;
  await page.evaluate(() => { void carregarPerguntas(1, { forcar: true, background: true }); });
  for (let attempt = 0; attempt < 100 && calls.filter(c => c.path.endsWith('/perguntas/lista')).length === previousCalls; attempt++) await delay(10);
  assert(calls.filter(c => c.path.endsWith('/perguntas/lista')).length > previousCalls, 'requisição antiga deve estar em andamento');
  const answeredId = await page.locator('[data-question-card]').getAttribute('data-question-id');
  try {
    await page.locator('.question-send-answer-btn').click();
    await page.waitForFunction(id => state.perguntas.find(q => q.id === id)?.status === 'ANSWERED', answeredId);
  } finally { control.listHold = null; releaseList(); }
  await page.waitForFunction(() => !state.carregandoPerguntas);
  assert.strictEqual(await page.evaluate(id => state.perguntas.find(q => q.id === id)?.status, answeredId), 'ANSWERED', 'consulta anterior não pode restaurar pendência após resposta');
  control.deny = 'fixture-1';
  await page.evaluate(() => carregarPerguntas(1, { forcar: true, background: true }));
  assert.strictEqual(await page.locator('[data-question-select]').count(), 0, '403 deve remover os dados em cache');
  control.deny = '';
  await page.evaluate(() => carregarLojas());
  await page.evaluate(() => selecionarLoja('Fixture 02'));
  await page.waitForFunction(() => state.perguntas.length === 20);
  await verifyExpiry(f);
  await verifyDuplicateNames(f);
  await verifyTokenChange(f);
  await page.evaluate(() => carregarLojas());
  await page.waitForFunction(() => state.perguntas.length === 20);
  await page.evaluate(() => window.dispatchEvent(new Event('jk:logout')));
  assert.strictEqual(await page.locator('[data-question-select]').count(), 0, 'logout deve limpar lista');
  assert.strictEqual(await page.locator('[data-question-card]').count(), 0, 'logout deve limpar detalhe');
}

async function verifyExpiry({ page, control }) {
  await page.evaluate(() => carregarPerguntas(1, { forcar: true }));
  const consulted = await page.evaluate(() => state.ultimaAtualizacaoPerguntasEm);
  control.fail = 'fixture-2';
  await page.evaluate(() => { window.__fixtureDateNow = Date.now; Date.now = () => window.__fixtureDateNow() + 590000; });
  try {
    await page.evaluate(() => carregarPerguntas(1, { forcar: true, background: true }));
    assert.strictEqual(await page.locator('[data-question-select]').count(), 20, 'dados de menos de dez minutos permitem fallback');
    assert.strictEqual(await page.evaluate(() => state.ultimaAtualizacaoPerguntasEm), consulted, 'fallback não pode renovar horário da consulta');
    await page.evaluate(() => { Date.now = () => window.__fixtureDateNow() + 610000; });
    await page.evaluate(() => carregarPerguntas(1, { background: true }));
    assert.strictEqual(await page.locator('[data-question-select]').count(), 0, 'cache com mais de dez minutos não pode reaparecer');
  } finally {
    control.fail = '';
    await page.evaluate(() => { Date.now = window.__fixtureDateNow; delete window.__fixtureDateNow; });
  }
}

async function verifyDuplicateNames({ page, calls, control }) {
  control.counts['fixture-1'] = 7;
  control.counts['fixture-2'] = 13;
  await page.evaluate(async () => {
    state.lojas[0].nome = 'Nome repetido'; state.lojas[1].nome = 'Nome repetido';
    renderizarLojas();
    await selecionarLoja('Nome repetido', 'fixture-2');
    await carregarContadoresNotificacoes(true);
  });
  assert.deepStrictEqual(await page.evaluate(() => [state.notificacoes.lojas['fixture-1'].perguntas, state.notificacoes.lojas['fixture-2'].perguntas]), [7, 13]);
  assert.match(await page.locator('#lojas-grid .store-card[data-store-id="fixture-1"] .store-notifications').innerText(), /^7 perguntas$/);
  assert.match(await page.locator('#lojas-grid .store-card[data-store-id="fixture-2"] .store-notifications').innerText(), /^13 perguntas$/);
  const before = calls.filter(call => call.path.endsWith('/perguntas/lista')).length;
  let release;
  control.listHold = new Promise(resolve => { release = resolve; });
  try {
    await page.locator('.question-answer-text').fill('Resposta da segunda loja homônima');
    await page.evaluate(() => { void carregarPerguntas(1, { forcar: true, background: true }); });
    for (let attempt = 0; attempt < 100 && calls.filter(call => call.path.endsWith('/perguntas/lista')).length === before; attempt++) await delay(10);
    assert(calls.filter(call => call.path.endsWith('/perguntas/lista')).length > before);
    await page.locator('.question-send-answer-btn').click();
    await page.waitForFunction(() => state.perguntas[0]?.status === 'ANSWERED');
  } finally { control.listHold = null; release(); }
  await page.waitForFunction(() => !state.carregandoPerguntas);
  assert(await page.evaluate(() => state.perguntas[0]?.status === 'ANSWERED' && state.perguntas[0]?.store_id === 'fixture-2'), 'loja homônima deve invalidar sua própria consulta anterior');
}

async function verifyTokenChange({ page, calls, control }) {
  await delay(250);
  let release;
  control.listHold = new Promise(resolve => { release = resolve; });
  const before = calls.length;
  try {
    await page.evaluate(() => {
      window.__tokenReadDone = false;
      void JKPerguntasLoading.request('lista', { store_id: 'fixture-2', limit: 20 })
        .catch(() => {}).finally(() => { window.__tokenReadDone = true; });
    });
    for (let attempt = 0; attempt < 100 && calls.length === before; attempt++) await delay(10);
    assert(calls.length > before);
    assert(await page.locator('[data-question-select]').count() > 0);
    await page.evaluate(() => localStorage.setItem('token', 'synthetic-replacement-session'));
  } finally { control.listHold = null; release(); }
  await page.waitForFunction(() => window.__tokenReadDone);
  assert.strictEqual(await page.locator('[data-question-select]').count(), 0, 'resposta detectando token alterado deve limpar lista imediatamente');
  assert.strictEqual(await page.locator('[data-question-card]').count(), 0);
  assert.deepStrictEqual(await page.evaluate(() => state.notificacoes.lojas), {}, 'troca de token deve limpar contadores');
  assert.strictEqual(await page.locator('#lojas-grid .notification-pill').count(), 0);
}

main().catch(error => { console.error(error); process.exitCode = 1; });
