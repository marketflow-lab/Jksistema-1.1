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
  const control = { slowMs: 900, fastMs: 35, deny: '', fail: '', listHold: null, counts: {}, detailMs: 75, history: 'ready', detailDeny: false, itemDeny: false, omitComponents: false, itemMissingOnce: '', detailErrors: [], retryAfter: '0', generationReject: '', generationReason: '', generationFailure: null };
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
      const call = { path: url.pathname, store: url.searchParams.get('store_id') || url.searchParams.get('loja'), question: url.searchParams.get('question_id'), itemIds: url.searchParams.get('item_ids'), force: url.searchParams.get('forcar'), offset: Number(url.searchParams.get('offset') || 0), limit: Number(url.searchParams.get('limit') || 20), start: Date.now() };
      calls.push(call);
      if (url.pathname.endsWith('/lojas')) return json({ success: true, lojas: stores, snapshot: { generation: 'fixture-1', published_at: '2026-09-09T12:00:00Z', status: 'ready' } });
      if (url.pathname === '/api/mercadolivre/perguntas' || url.pathname.endsWith('/perguntas/lista')) {
        const store = stores.find(s => s.store_id === call.store || s.nome === call.store);
        assert(store, 'consulta deve identificar uma loja da fixture');
        const payload = { questions: questions(store, call.offset, call.limit), total: 60, retornadas: Math.min(call.limit, 60 - call.offset), next_offset: call.offset + call.limit < 60 ? call.offset + call.limit : null, status_resumo: { UNANSWERED: 60 }, store_id: store.store_id, loja: store.nome, revision: 0, stale: false, partial: false, source: 'remote', consultado_em: Date.now(), fetched_at: new Date().toISOString() };
        if (control.listHold) await control.listHold;
        await delay(store === stores[9] ? control.slowMs : control.fastMs);
        call.end = Date.now();
        if (store.store_id === control.deny) return route.fulfill({ status: 401, contentType: 'application/json', headers: {'X-JK-Error-Scope': 'store', 'X-JK-Retryable': 'false'}, body: JSON.stringify({ detail: 'Acesso revogado na fixture' }) }).catch(() => {});
        if (store === stores[10] || store.store_id === control.fail) return json({ detail: 'Falha sintética' }, 503);
        return json(payload);
      }
      if (url.pathname.endsWith('/perguntas/itens')) {
        await delay(50);
        if (control.itemDeny) return route.fulfill({status: 403, contentType: 'application/json', headers: {'X-JK-Error-Scope': 'resource', 'X-JK-Retryable': 'false'}, body: JSON.stringify({detail: 'Anúncio com acesso negado'})}).catch(() => {});
        const ids = url.searchParams.getAll('item_ids').flatMap(value => value.split(','));
        const missing = ids.includes(control.itemMissingOnce) ? control.itemMissingOnce : '';
        if (missing) control.itemMissingOnce = '';
        const itens = ids.filter(id => id !== missing).map(id => ({
          id, item_id: id, item_title: `Anúncio ${id}`, item_thumbnail: '', item_permalink: '', item_sku: '',
          variations: [
            { id: 'gray', attribute_combinations: [{ id: 'COLOR', name: 'Cor', value_name: 'Cinza' }], attributes: [{ id: 'SELLER_SKU', value_name: 'T74-11' }], available_quantity: 7 },
            { id: 'beige', attribute_combinations: [{ id: 'COLOR', name: 'Cor', value_name: 'Bege' }], seller_custom_field: 'T74-5', available_quantity: 3 },
            { id: 'black', attribute_combinations: [{ id: 'COLOR', name: 'Cor', value_name: 'Preto' }], seller_custom_field: 'T74-1', available_quantity: 0 },
          ],
        }));
        return json({ itens, items: itens, store_id: call.store, partial: Boolean(missing), item_states: Object.fromEntries(ids.map(id => [id, {state: id === missing ? 'unavailable' : 'ready', retryable: id === missing}])) });
      }
      if (url.pathname.endsWith('/perguntas/detalhe')) {
        await delay(control.detailMs);
        const transient = control.detailErrors.shift();
        if (transient) return route.fulfill({status: transient, contentType: 'application/json', headers: {'X-JK-Error-Scope': 'resource', 'X-JK-Retryable': 'true', 'Retry-After': control.retryAfter}, body: JSON.stringify({detail: 'Falha transitória sintética'})}).catch(() => {});
        if (control.detailDeny) return route.fulfill({ status: 403, contentType: 'application/json', headers: {'X-JK-Error-Scope': 'resource', 'X-JK-Retryable': 'false'}, body: JSON.stringify({ detail: 'Recurso indisponível' }) }).catch(() => {});
        const buyerQuestionChat = Array.from({ length: 12 }, (_, index) => ({
          role: index % 2 === 0 ? 'buyer' : 'seller',
          text: `Mensagem sintética ${index + 1} do histórico deste anúncio`,
          date: new Date(Date.UTC(2026, 8, 8, 11, index)).toISOString(),
        }));
        return json({ question: { text: 'Pergunta sintética de teste', buyer_name: 'Comprador fixture', buyer_question_chat: buyerQuestionChat, buyer_question_history_count: 6 }, store_id: call.store, partial: !control.omitComponents, stale: false,
          history_truncated: control.history === 'ready', components: control.omitComponents ? undefined : { question: {state: 'ready'}, history: {state: control.history, retryable: false}, buyer: {state: 'unavailable', retryable: false} } });
      }
      if (url.pathname.endsWith('/perguntas/resumo')) return json({ lojas: stores.map(s => ({ ...s, perguntas: s === stores[10] ? null : control.counts[s.store_id] ?? 60, total: s === stores[10] ? null : 60, status_resumo: { UNANSWERED: 60 }, erro: s === stores[10] ? 'Falha sintética' : null })), partial: true });
      if (url.pathname.endsWith('/perguntas/resposta/gerar') && control.generationScope) return route.fulfill({status: control.generationScope === 'session' ? 401 : control.generationScope === 'resource' ? 404 : 403, contentType: 'application/json', headers: {'X-JK-Error-Code': `${control.generationScope}_disconnected`, 'X-JK-Error-Scope': control.generationScope}, body: JSON.stringify({detail: 'Consulta sem acesso na fixture'})}).catch(() => {});
      if (url.pathname.endsWith('/perguntas/resposta/gerar') && control.generationReject) return route.fulfill({status: 409, contentType: 'application/json', headers: {'X-JK-Error-Code': 'generation_context_unavailable', 'X-JK-Error-Component': control.generationReject, 'X-JK-Error-Reason': control.generationReason}, body: JSON.stringify({detail: control.generationReject === 'history' ? 'Histórico não confirmado. Tente novamente.' : control.generationReject === 'context_hub' ? 'Índice temporariamente indisponível.' : 'Contexto não confirmado. Tente novamente.'})}).catch(() => {});
      if (url.pathname.endsWith('/perguntas/resposta/gerar') && control.generationFailure) {
        const failure = control.generationFailure;
        const headers = {
          'X-JK-Error-Code': failure.code || 'generation_failed',
          'X-JK-Error-Reason': failure.reason || '',
          'X-JK-Error-Scope': failure.scope || 'resource'
        };
        if (failure.component) headers['X-JK-Error-Component'] = failure.component;
        return route.fulfill({status: failure.status || 409, contentType: 'application/json', headers, body: JSON.stringify({
          detail: failure.detail || 'Falha sintética ao gerar.',
          error_code: failure.code || 'generation_failed',
          error_reason: failure.reason || '',
          error_component: failure.component || ''
        })}).catch(() => {});
      }
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
    if (!baseline) {
      await verifyUnspecifiedVariations(f);
      await verifyResponsiveLayout(f);
    }
    if (!baseline) {
      const summaryText = await page.locator('#perguntas-summary').innerText();
      assert.match(summaryText, /10\s*Contas/, 'contas deve somar somente consultas bem-sucedidas');
      assert.match(summaryText, /1\s*Erros/, 'falha deve permanecer visível separadamente');
    }
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

async function verifyUnspecifiedVariations({ page }) {
  await page.waitForFunction(() => document.querySelectorAll('.question-variation-item').length === 3);
  const rows = await page.locator('.question-variation-item').allInnerTexts();
  assert.deepStrictEqual(rows, [
    'Cinza\nSKU T74-11\nEstoque atual: 7',
    'Bege\nSKU T74-5\nEstoque atual: 3',
    'Preto\nSKU T74-1\nEstoque atual: 0',
  ]);
  assert.match(await page.locator('.question-detail-meta').innerText(), /3 variaç(?:ão|ões)/);
  assert.match(await page.locator('.question-context-card').first().innerText(), /Variação não especificada na pergunta/);
}

async function verifyResponsiveLayout({ page }) {
  await page.waitForFunction(() => document.querySelectorAll('.question-chat-bubble').length === 12);
  await page.setViewportSize({ width: 1033, height: 657 });
  await delay(50);
  const desktop = await page.evaluate(() => {
    const container = document.querySelector('.container');
    const panel = document.querySelector('.panel');
    const layout = document.querySelector('.questions-inbox-layout');
    const left = document.querySelector('.questions-list-pane');
    const right = document.querySelector('.question-detail-pane');
    const rect = element => element.getBoundingClientRect();
    left.scrollTop = 0;
    right.scrollTop = 0;
    left.scrollTop = left.scrollHeight;
    const leftAfter = left.scrollTop;
    const rightBefore = right.scrollTop;
    right.scrollTop = right.scrollHeight;
    return {
      viewport: { width: innerWidth, height: innerHeight },
      container: rect(container),
      panel: rect(panel),
      layout: rect(layout),
      left: { rect: rect(left), clientHeight: left.clientHeight, scrollHeight: left.scrollHeight, scrollTop: leftAfter, afterRightScroll: left.scrollTop },
      right: { rect: rect(right), clientHeight: right.clientHeight, scrollHeight: right.scrollHeight, scrollTop: right.scrollTop, before: rightBefore },
      leftOverflow: getComputedStyle(left).overflowY,
      rightOverflow: getComputedStyle(right).overflowY,
      leftScrollbarWidth: getComputedStyle(left).scrollbarWidth,
      bodyScrollWidth: document.documentElement.scrollWidth,
      bodyScrollHeight: document.documentElement.scrollHeight,
    };
  });
  assert(Math.abs(desktop.container.width - desktop.viewport.width) <= 1, 'container deve acompanhar toda a largura da viewport');
  assert(Math.abs(desktop.container.height - desktop.viewport.height) <= 1, 'container deve acompanhar toda a altura da viewport');
  assert(desktop.panel.bottom <= desktop.viewport.height + 1, 'painel deve terminar dentro da viewport');
  assert(desktop.bodyScrollWidth <= desktop.viewport.width, 'layout desktop não pode criar rolagem horizontal no documento');
  assert(desktop.bodyScrollHeight <= desktop.viewport.height, 'layout desktop não pode depender da rolagem do documento');
  assert(Math.abs(desktop.left.rect.y - desktop.right.rect.y) <= 1 && desktop.right.rect.x > desktop.left.rect.x, 'fila e detalhe devem permanecer lado a lado');
  assert.strictEqual(desktop.leftOverflow, 'auto');
  assert.strictEqual(desktop.rightOverflow, 'auto');
  assert.strictEqual(desktop.leftScrollbarWidth, 'thin', 'scrollbar da fila deve usar largura discreta');
  assert(desktop.left.scrollHeight > desktop.left.clientHeight && desktop.left.scrollTop > 0, 'fila deve rolar dentro da própria coluna');
  assert(desktop.right.scrollHeight > desktop.right.clientHeight && desktop.right.scrollTop > 0, 'mensagens devem rolar dentro da própria coluna');
  assert.strictEqual(desktop.right.before, 0, 'rolagem da fila não pode mover as mensagens');
  assert.strictEqual(desktop.left.afterRightScroll, desktop.left.scrollTop, 'rolagem das mensagens não pode mover a fila');

  await page.setViewportSize({ width: 800, height: 360 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await delay(50);
  const lowViewport = await page.evaluate(() => {
    const tab = document.querySelector('#aba-perguntas').getBoundingClientRect();
    const left = document.querySelector('.questions-list-pane').getBoundingClientRect();
    const right = document.querySelector('.question-detail-pane').getBoundingClientRect();
    return {
      bodyOverflow: getComputedStyle(document.body).overflowY,
      leftOverflow: getComputedStyle(document.querySelector('.questions-list-pane')).overflowY,
      rightOverflow: getComputedStyle(document.querySelector('.question-detail-pane')).overflowY,
      tabHeight: tab.height,
      sideBySide: Math.abs(left.y - right.y) <= 1 && right.x > left.x,
      bodyScrollHeight: document.documentElement.scrollHeight,
      bodyScrollWidth: document.documentElement.scrollWidth,
      viewportHeight: innerHeight,
      viewportWidth: innerWidth,
    };
  });
  assert.strictEqual(lowViewport.bodyOverflow, 'auto', 'janela baixa deve usar a rolagem da página');
  assert.strictEqual(lowViewport.leftOverflow, 'visible');
  assert.strictEqual(lowViewport.rightOverflow, 'visible');
  assert(lowViewport.tabHeight > 0 && lowViewport.sideBySide, 'janela baixa deve manter perguntas acessíveis em duas colunas');
  assert(lowViewport.bodyScrollHeight > lowViewport.viewportHeight, 'janela baixa deve permitir alcançar todo o conteúdo');
  assert(lowViewport.bodyScrollWidth <= lowViewport.viewportWidth, 'janela baixa não pode criar rolagem horizontal');

  await page.setViewportSize({ width: 360, height: 480 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await delay(50);
  const mobile = await page.evaluate(() => {
    const tab = document.querySelector('#aba-perguntas');
    const left = document.querySelector('.questions-list-pane');
    const right = document.querySelector('.question-detail-pane');
    const leftRect = left.getBoundingClientRect();
    const rightRect = right.getBoundingClientRect();
    return {
      tabOverflow: getComputedStyle(tab).overflowY,
      bodyOverflow: getComputedStyle(document.body).overflowY,
      leftOverflow: getComputedStyle(left).overflowY,
      rightOverflow: getComputedStyle(right).overflowY,
      stacked: rightRect.top >= leftRect.bottom - 1,
      tabHeight: tab.getBoundingClientRect().height,
      bodyScrollWidth: document.documentElement.scrollWidth,
      bodyScrollHeight: document.documentElement.scrollHeight,
      viewportHeight: innerHeight,
      viewportWidth: innerWidth,
    };
  });
  assert.strictEqual(mobile.tabOverflow, 'visible', 'em tela estreita a aba deve acompanhar a rolagem da página');
  assert.strictEqual(mobile.bodyOverflow, 'auto', 'em tela estreita a página deve permanecer rolável');
  assert.strictEqual(mobile.leftOverflow, 'visible');
  assert.strictEqual(mobile.rightOverflow, 'visible');
  assert(mobile.stacked, 'fila e detalhe devem ser empilhados em tela estreita');
  assert(mobile.tabHeight > 0, 'aba de perguntas não pode ser comprimida pelo cabeçalho');
  assert(mobile.bodyScrollHeight > mobile.viewportHeight, 'conteúdo empilhado deve continuar acessível pela rolagem da página');
  assert(mobile.bodyScrollWidth <= mobile.viewportWidth, 'layout móvel não pode criar rolagem horizontal');
  const mobileReach = await page.evaluate(() => {
    window.scrollTo(0, document.documentElement.scrollHeight);
    const detail = document.querySelector('.question-detail-pane').getBoundingClientRect();
    return { scrollY, detailTop: detail.top, detailBottom: detail.bottom, viewportHeight: innerHeight };
  });
  assert(mobileReach.scrollY > 0, 'página estreita deve aceitar rolagem vertical');
  assert(mobileReach.detailTop < mobileReach.viewportHeight && mobileReach.detailBottom > 0, 'detalhe deve ser alcançável no fim da página');
  await page.setViewportSize({ width: 1280, height: 900 });
  await delay(50);
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
  await page.waitForFunction(() => document.querySelector('.question-ai-answer-btn')?.disabled === false);
  await verifyRecovery(f);
  assert.strictEqual(
    await page.evaluate(() => state.perguntas.find(
      question => chavePerguntaAtendimento(question) === state.perguntaSelecionadaKey
    )?._detailPartial),
    true,
    'comprador opcional indisponível não bloqueia a geração quando o histórico foi consultado'
  );
  await input.fill('Rascunho sintético preservado');
  await input.focus();
  await page.evaluate(() => carregarPerguntas(1, { background: true, preservarInteracao: true, forcar: true }));
  assert.strictEqual(await input.inputValue(), 'Rascunho sintético preservado');
  assert(await page.evaluate(() => {
    const composer = document.querySelector('.question-answer-text');
    return composer !== null && composer === document.activeElement;
  }), 'atualização deve preservar foco');
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
  assert.strictEqual(await page.locator('[data-question-select]').count(), 0, 'revogação da loja deve remover seus dados em cache');
  assert.strictEqual(await page.evaluate(() => state.lojas.length), 11, 'revogação da loja não apaga as outras lojas');
  control.deny = '';
  await page.evaluate(() => carregarLojas());
  await page.evaluate(() => selecionarLoja('Fixture 02'));
  await page.waitForFunction(() => state.perguntas.length === 20);
  await verifyExpiry(f);
  await verifyDuplicateNames(f);
  await verifySessionRenewal(f);
  await verifyTokenChange(f);
  await page.evaluate(() => carregarLojas());
  await page.waitForFunction(() => state.perguntas.length === 20);
  await verifyGenerationDenial(f);
  await page.evaluate(() => carregarLojas());
  await page.waitForFunction(() => state.perguntas.length === 20);
  await page.evaluate(() => window.dispatchEvent(new Event('jk:logout')));
  assert.strictEqual(await page.locator('[data-question-select]').count(), 0, 'logout deve limpar lista');
  assert.strictEqual(await page.locator('[data-question-card]').count(), 0, 'logout deve limpar detalhe');
}

async function verifyGenerationDenial({ page, control }) {
  await page.evaluate(() => selecionarLoja(TODAS_LOJAS_VALUE));
  await page.waitForFunction(() => document.querySelector('.question-ai-answer-btn')?.disabled === false);
  await page.locator('.question-answer-text').fill('Rascunho enquanto o servidor pesquisa');
  await page.evaluate(() => aplicarResultadoJobAtendimentoCodex(state.perguntaSelecionadaKey, {blocked_without_draft: true, error_code: 'generation_context_unavailable', result: {resposta: '', blocked_without_draft: true, error_code: 'generation_context_unavailable', error_component: 'history', warnings: ['Histórico indisponível durante a pesquisa.']}}));
  assert.strictEqual(await page.locator('.question-answer-text').inputValue(), 'Rascunho enquanto o servidor pesquisa', 'job bloqueado sem resposta não apaga rascunho local');
  assert.strictEqual(await page.locator('.question-ai-answer-btn').isEnabled(), false);
  assert.match(
    await page.locator('.question-answer-composer .question-answer-status').innerText(),
    /histórico (?:indisponível|não pôde ser confirmado)/i,
  );
  await page.locator('.question-loading-retry').click();
  await page.waitForFunction(() => document.querySelector('.question-ai-answer-btn')?.disabled === false);
  const before = await page.evaluate(() => {
    const selected = state.perguntas.find(q => chavePerguntaAtendimento(q) === state.perguntaSelecionadaKey);
    return {store: selected.store_id, others: state.perguntas.filter(q => q.store_id !== selected.store_id).map(q => q.id)};
  });
  assert(before.others.length > 0);
  control.generationScope = 'store';
  await page.locator('.question-ai-answer-btn').click();
  await page.waitForFunction(id => state.perguntas.every(q => q.store_id !== id), before.store);
  assert.deepStrictEqual(await page.evaluate(() => state.perguntas.map(q => q.id)), before.others, 'revogação durante geração preserva perguntas de outras lojas');
  assert.strictEqual(await page.evaluate(() => state.lojas.length), 11);
  await page.waitForFunction(() => document.querySelector('.question-ai-answer-btn')?.disabled === false);
  control.generationScope = 'resource';
  await page.locator('.question-ai-answer-btn').click();
  await page.waitForFunction(() => /não foi possível gerar a sugestão/i.test(document.querySelector('.question-answer-composer .question-answer-status')?.innerText || ''));
  assert(await page.evaluate(() => state.perguntas.length > 0 && state.lojas.length === 11), '404 de recurso durante geração não esvazia loja ou sessão');
  assert.strictEqual(await page.evaluate(() => Boolean(state.perguntas.find(q => chavePerguntaAtendimento(q) === state.perguntaSelecionadaKey)?.text)), true, '404 sem componente explícito preserva a pergunta');
  assert.strictEqual(await page.locator('.question-ai-answer-btn').isEnabled(), true, 'erro genérico deve permitir nova tentativa');
  await page.locator('[data-question-select]').nth(1).click();
  await page.waitForFunction(() => document.querySelector('.question-ai-answer-btn')?.disabled === false);
  control.generationScope = 'session';
  await page.locator('.question-ai-answer-btn').click();
  await page.waitForFunction(() => state.perguntas.length === 0 && state.lojas.length === 0);
  control.generationScope = '';
  // A revoked session requires a new login before the following logout regression.
  await page.evaluate(() => localStorage.setItem('token', 'synthetic-session-after-relogin'));
}

async function verifyRecovery({ page, calls, control }) {
  const select = index => page.locator('[data-question-select]').nth(index).click();
  const questionA = await page.evaluate(() => String(state.perguntas[18].id));
  control.detailMs = 600;
  const start = calls.length;
  await select(18);
  await page.waitForFunction(() => state.perguntas[18]._detailPending);
  await select(19);
  await select(18);
  await page.waitForFunction(() => state.perguntas[18]._detailReady && !state.perguntas[18]._detailPending);
  assert(calls.slice(start).filter(c => c.question === questionA).length >= 2, 'A → B → A deve retomar a consulta abortada');
  assert.strictEqual(await page.locator('.question-ai-answer-btn').isEnabled(), true);
  control.generationReject = 'history';
  await page.locator('.question-ai-answer-btn').click();
  await page.waitForFunction(() => document.querySelector('.question-loading-retry') && document.querySelector('.question-ai-answer-btn')?.disabled);
  assert.match(await page.locator('.question-answer-composer .question-answer-status').innerText(), /histórico não pôde ser confirmado/i);
  await page.evaluate(() => renderizarPerguntas());
  assert.match(await page.locator('.question-answer-composer .question-answer-status').innerText(), /histórico não pôde ser confirmado/i, 'nova renderização deve preservar explicação da rejeição');
  control.generationReject = '';
  await page.locator('.question-loading-retry').click();
  await page.waitForFunction(() => document.querySelector('.question-ai-answer-btn')?.disabled === false);
  await page.locator('.question-answer-text').fill('Rascunho preservado na divergência');
  control.generationReject = 'identity';
  control.generationReason = 'store_changed';
  await page.locator('.question-ai-answer-btn').click();
  await page.waitForFunction(() => document.querySelector('[data-component="identity"]')?.dataset.state === 'blocked');
  assert.match(await page.locator('.question-loading-status').innerText(), /Os dados da loja, pergunta ou anúncio mudaram\. Recarregue o contexto\./);
  assert.doesNotMatch(await page.locator('[data-component="history"]').innerText(), /Acesso bloqueado/, 'divergência de identidade não pode se apresentar como bloqueio do histórico');
  assert.strictEqual(await page.locator('.question-answer-text').inputValue(), 'Rascunho preservado na divergência');
  assert.strictEqual(await page.locator('.question-loading-retry').innerText(), 'Recarregar contexto');
  control.generationReject = '';
  control.generationReason = '';
  await page.locator('.question-loading-retry').click();
  await page.waitForFunction(() => document.querySelector('.question-ai-answer-btn')?.disabled === false);
  assert.strictEqual(await page.locator('.question-answer-text').inputValue(), 'Rascunho preservado na divergência');
  control.generationReject = 'context_hub';
  control.generationReason = 'training_index_initializing';
  await page.locator('.question-ai-answer-btn').click();
  await page.waitForFunction(() => document.querySelector('[data-component="context_hub"]')?.dataset.state === 'unavailable');
  assert.match(await page.locator('.question-loading-status').innerText(), /informações do Obsidian ainda estão sendo preparadas/i);
  assert.strictEqual(await page.locator('.question-answer-text').inputValue(), 'Rascunho preservado na divergência');
  assert.strictEqual(await page.locator('.question-ai-answer-btn').isEnabled(), true, 'Context Hub indisponível deve permitir nova tentativa sem apagar o contexto do Mercado Livre');
  control.generationReject = '';
  control.generationReason = '';
  for (const failure of [
    { reason: 'codex_authentication_required', expected: /autenticação local da IA expirou/i },
    { reason: 'codex_runtime_invalid', expected: /IA local está indisponível neste computador/i },
    { reason: 'provider_connection', expected: /IA está temporariamente indisponível/i },
    { code: 'generation_context_unavailable', reason: 'context_expired', expected: /não foi possível gerar a sugestão/i }
  ]) {
    control.generationFailure = failure;
    await page.locator('.question-ai-answer-btn').click();
    await page.waitForFunction(source => new RegExp(source, 'i').test(document.querySelector('.question-answer-composer .question-answer-status')?.innerText || ''), failure.expected.source);
    assert.strictEqual(await page.locator('.question-answer-text').inputValue(), 'Rascunho preservado na divergência');
    assert.strictEqual(await page.locator('.question-ai-answer-btn').isEnabled(), true, 'falha terminal deve permitir Gerar IA novamente');
    assert.strictEqual(await page.locator('[data-component="context"]').count(), 0, 'falha sem componente explícito não pode virar contexto expirado');
  }
  control.generationFailure = null;
  control.detailMs = 75;
  control.history = 'unavailable';
  await page.evaluate(() => JKPerguntasLoading.detalhe(state.perguntas[18], true));
  assert.strictEqual(await page.locator('.question-ai-answer-btn').isEnabled(), false, 'histórico não consultado bloqueia IA');
  assert.strictEqual(await page.locator('[data-question-select]').count(), 20, 'pergunta válida permanece visível');
  assert.match(await page.locator('[data-component="history"]').innerText(), /Indisponível/);
  assert.strictEqual(await page.locator('.question-loading-retry').count(), 1);
  control.history = 'ready';
  await page.locator('.question-loading-retry').click();
  await page.waitForFunction(() => document.querySelector('.question-ai-answer-btn')?.disabled === false);
  assert.match(await page.locator('.question-loading-status').innerText(), /50 perguntas/);
  control.omitComponents = true;
  await page.evaluate(() => JKPerguntasLoading.detalhe(state.perguntas[18], true));
  assert.strictEqual(await page.locator('.question-ai-answer-btn').isEnabled(), false, 'HTTP200 e array de histórico sem confirmação de consulta não liberam IA');
  control.omitComponents = false;
  await page.evaluate(() => JKPerguntasLoading.detalhe(state.perguntas[18], true));
  await page.locator('.question-answer-text').fill('Rascunho antes de falha pontual');
  control.detailDeny = true;
  await page.evaluate(() => JKPerguntasLoading.detalhe(state.perguntas[18], true));
  assert.strictEqual(await page.locator('[data-question-select]').count(), 20, '403 de recurso não esvazia perguntas');
  assert.strictEqual(await page.evaluate(() => state.lojas.length), 11, '403 de recurso não esvazia lojas');
  assert.strictEqual(await page.locator('.question-answer-text').inputValue(), 'Rascunho antes de falha pontual');
  assert.strictEqual(await page.evaluate(() => Boolean(state.perguntas[18].text)), false, '403 da pergunta principal remove conteúdo protegido desse recurso');
  assert.strictEqual(await page.locator('.question-send-answer-btn').isEnabled(), false, 'pergunta negada também bloqueia envio manual');
  assert.strictEqual(await page.locator('.question-ai-answer-btn').isEnabled(), false);
  control.detailDeny = false;
  const missing = await page.evaluate(() => String(state.perguntas[18].item_id));
  control.itemMissingOnce = missing;
  const refreshStart = calls.length;
  await page.evaluate(() => carregarPerguntas(1, { forcar: true }));
  await page.waitForFunction(() => state.perguntas.every(q => q._itemReady && !q._itemPending));
  await page.waitForFunction(() => document.querySelector('.question-ai-answer-btn')?.disabled === false);
  const refresh = calls.slice(refreshStart);
  for (const path of ['lista', 'itens', 'detalhe']) assert(refresh.some(c => c.path.endsWith(`/perguntas/${path}`) && c.force === 'true'), `Atualizar deve forçar ${path}`);
  assert(refresh.some(c => c.path.endsWith('/perguntas/itens') && c.itemIds === missing), 'tentativa deve consultar apenas anúncio pendente');
  control.detailErrors = [503, 429];
  control.retryAfter = '1';
  const retryStart = calls.length;
  await page.evaluate(() => JKPerguntasLoading.detalhe(state.perguntas[18], true));
  const attempts = calls.slice(retryStart).filter(c => c.path.endsWith('/perguntas/detalhe'));
  assert.strictEqual(attempts.length, 3, '503 e 429 devem repetir até três tentativas');
  assert(attempts[1].start - attempts[0].start >= 2000 && attempts[2].start - attempts[1].start >= 4000, 'repetições respeitam intervalos de 2 e 4 segundos');
  assert.strictEqual(await page.locator('.question-ai-answer-btn').isEnabled(), true);
  control.detailErrors = [429];
  control.retryAfter = '60';
  const rateLimitStart = calls.length;
  await page.evaluate(() => JKPerguntasLoading.detalhe(state.perguntas[18], true));
  assert.strictEqual(calls.slice(rateLimitStart).filter(c => c.path.endsWith('/perguntas/detalhe')).length, 1, 'Retry-After além da janela de 30s deve oferecer tentativa manual');
  assert.strictEqual(await page.locator('.question-ai-answer-btn').isEnabled(), false);
  assert.strictEqual(await page.locator('.question-loading-retry').count(), 1);
  control.retryAfter = '0';
  control.itemDeny = true;
  await page.evaluate(() => carregarPerguntas(1, {forcar: true}));
  await page.waitForFunction(() => state.perguntas.every(q => !q._itemPending));
  assert(await page.evaluate(() => state.perguntas.every(q => !q.item_title && q._itemState === 'blocked')), '403 de anúncio remove campos antigos e bloqueia prontidão');
  assert.strictEqual(await page.evaluate(() => state.lojas.length), 11);
  control.itemDeny = false;
  await page.evaluate(() => carregarPerguntas(1, {forcar: true}));
  await select(0);
  await page.waitForFunction(() => document.querySelector('.question-ai-answer-btn')?.disabled === false);
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
  const firstCounter = page.locator('#lojas-grid .store-card[data-store-id="fixture-1"] .store-notifications');
  const secondCounter = page.locator('#lojas-grid .store-card[data-store-id="fixture-2"] .store-notifications');
  assert.strictEqual(await firstCounter.innerText(), '7');
  assert.strictEqual(await secondCounter.innerText(), '13');
  assert.strictEqual(await firstCounter.locator('xpath=..').getAttribute('class'), 'store-card-status');
  assert.strictEqual(await firstCounter.locator('.notification-pill').getAttribute('aria-label'), '7 perguntas não respondidas');
  const [cardBox, counterBox] = await Promise.all([
    page.locator('#lojas-grid .store-card[data-store-id="fixture-1"]').boundingBox(),
    firstCounter.boundingBox(),
  ]);
  assert(cardBox && counterBox, 'card e contador devem estar visíveis');
  assert(counterBox.y < cardBox.y + cardBox.height / 2, 'contador deve ficar na faixa superior do card');
  assert(cardBox.x + cardBox.width - (counterBox.x + counterBox.width) <= 24, 'contador deve ficar no canto superior direito');
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

async function verifySessionRenewal({ page }) {
  const jwt = claims => `synthetic.${Buffer.from(JSON.stringify(claims)).toString('base64url')}.signature`;
  const identity = {sub: 'fixture-user', client_id: 'fixture-tenant', jti: 'fixture-session'};
  await page.evaluate(token => { localStorage.setItem('token', token); JKPerguntasLoading.verificarSessao(); }, jwt({...identity, exp: 100}));
  await page.evaluate(() => carregarLojas());
  await page.waitForFunction(() => state.perguntas.length === 20 && !state.carregandoPerguntas);
  await page.locator('[data-question-select]').nth(3).click();
  await page.locator('.question-answer-text').fill('Rascunho conservado na renovação');
  const key = await page.evaluate(() => state.perguntaSelecionadaKey);
  await page.evaluate(token => { localStorage.setItem('token', token); JKPerguntasLoading.verificarSessao(); }, jwt({...identity, exp: 200}));
  await page.waitForFunction(() => !state.carregandoPerguntas);
  assert.strictEqual(await page.evaluate(() => state.perguntaSelecionadaKey), key, 'renovação mantém seleção da mesma sessão');
  assert.strictEqual(await page.locator('.question-answer-text').inputValue(), 'Rascunho conservado na renovação');
  await page.evaluate(token => { localStorage.setItem('token', token); JKPerguntasLoading.verificarSessao(); }, jwt({...identity, client_id: 'other-tenant', exp: 300}));
  assert.strictEqual(await page.locator('[data-question-select]').count(), 0, 'troca de empresa limpa dados e rascunhos');
  await page.evaluate(() => carregarLojas());
  await page.waitForFunction(() => state.perguntas.length === 20 && !state.carregandoPerguntas);
}

main().catch(error => { console.error(error); process.exitCode = 1; });
