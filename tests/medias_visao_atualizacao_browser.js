'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const host = 'http://jk.test/medias_compras.html';
const savedTime = new Date().toISOString();

function payload(title, version, { stale = false, error = false } = {}) {
  return { colunas_meses: [], itens: [{ sku: 'SKU-COMUM', titulo_anuncio: title, vendas_mensais: {},
    total_vendas_periodo: 12, media_mensal: 2, saldo_atual_estoque: 3, estoque_em_transito: 0,
    posicao_estoque: 3, compra_sugerida: 5, foto: 'http://jk.test/synthetic-photo.gif' }],
  _cache: { estado: 'ready', stale, atualizando: stale && !error, versao: version,
    gerado_em: savedTime, autoridade: 'authority-a', politica_fotos: 'photos-a', pendente_fontes: false, completo: true, ...(error ? { erro: 'Falha sintética' } : {}) } };
}

async function fixture(browser, name) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const errors = [], mutations = [], routeErrors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(initial => {
    localStorage.setItem('access_token', 'synthetic-view');
    localStorage.setItem('user_data', JSON.stringify({ client_id: 'tenant-view', username: 'synthetic-user' }));
    const nativeFetch = fetch.bind(window), nativeTimeout = setTimeout.bind(window), nativeClear = clearTimeout.bind(window);
    const timers = new Map(), queue = [], calls = [], pending = new Map();
    let clock = Date.now(), id = 0, fallback = { body: initial };
    Date.now = () => clock;
    const tick = () => new Promise(resolve => nativeTimeout(resolve, 0));
    window.setTimeout = (callback, delay, ...args) => {
      if (Number.isFinite(delay) && delay >= 300 && delay <= 30000) {
        const key = --id;
        timers.set(key, { delay, at: clock + delay, callback: () => callback(...args) });
        return key;
      }
      return nativeTimeout(callback, delay, ...args);
    };
    window.clearTimeout = key => key < 0 ? timers.delete(key) : nativeClear(key);
    window.fetch = async (input, init = {}) => {
      const url = new URL(input, location.href);
      if (url.pathname !== '/api/medias-compras/visao') return nativeFetch(input, init);
      const plan = queue.shift() || fallback;
      const call = { url: url.href, method: init.method || 'GET', aborted: !!init.signal?.aborted,
        headers: Object.fromEntries(new Headers(init.headers)), hasSignal: !!init.signal };
      calls.push(call);
      let reject;
      const index = calls.length - 1;
      init.signal?.addEventListener('abort', () => {
        call.aborted = true;
        if (!plan.ignoreAbort) { pending.delete(index); reject?.(new DOMException('Synthetic abort', 'AbortError')); }
      });
      return { ok: (plan.status || 200) < 400, status: plan.status || 200,
        json: () => plan.pending ? new Promise((resolve, failure) => {
          reject = failure; pending.set(index, () => { pending.delete(index); resolve(plan.body); });
        }) : Promise.resolve(plan.body) };
    };
    window.__view = { calls, plan: item => queue.push(item), fallback: item => { fallback = item; }, pending: () => [...pending.keys()],
      release: async index => { if (!pending.has(index)) throw new Error('No pending request ' + index); pending.get(index)(); await tick(); await tick(); }, timers: () => [...timers.values()].map(timer => timer.delay),
      run: async delay => {
        const selected = [...timers].filter(([, timer]) => timer.delay === delay);
        if (selected.length) clock = Math.max(clock, ...selected.map(([, timer]) => timer.at));
        for (const [key, timer] of selected) { timers.delete(key); timer.callback(); }
        await tick(); await tick();
        return selected.length;
      },
      advance: async ms => {
        const target = clock + ms;
        for (let step = 0; step < 1000; step++) {
          const next = [...timers].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
          if (!next) { clock = target; await tick(); await tick(); return; }
          timers.delete(next[0]); clock = Math.max(clock, next[1].at); next[1].callback();
          await tick(); await tick();
        }
        throw new Error('Timer loop did not settle');
      } };
  }, payload('Inicial', 'v1'));
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    const json = body => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    try {
      if (url.origin !== 'http://jk.test') return route.fulfill({ body: '' });
      if (url.pathname === '/synthetic-photo.gif') return route.fulfill({ contentType: 'image/gif', body: Buffer.from('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7', 'base64') });
      if (url.pathname === '/medias_compras.html') return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(path.join(root, 'static/medias_compras.html')) });
      if (url.pathname.startsWith('/medias_compras/')) return route.fulfill({
        contentType: url.pathname.endsWith('.css') ? 'text/css' : 'application/javascript',
        body: fs.readFileSync(path.join(root, 'static', url.pathname.slice(1))) });
      if (url.pathname === '/auth.js') return route.fulfill({ contentType: 'application/javascript', body:
        'window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({Authorization:"Bearer "+localStorage.getItem("access_token"),"X-Client-ID":JSON.parse(localStorage.getItem("user_data")).client_id});' });
      if (url.pathname.startsWith('/api/')) {
        if (request.method() !== 'GET') mutations.push({ method: request.method(), path: url.pathname });
        assert.strictEqual(request.method(), 'GET', 'Atualização e edição local não gravam automaticamente.');
        if (url.pathname === '/api/lojas') return json([{ nome: 'Loja A', store_id: 'store-a' }, { nome: 'Loja B', store_id: 'store-b' }]);
        if (url.pathname.endsWith('/preferencias-skus-ocultos')) return json({ skus_ocultos: [] });
        throw new Error('API inesperada: ' + url.pathname);
      }
      return route.fulfill({ contentType: 'application/javascript', body: '' });
    } catch (error) { routeErrors.push(error.message); return route.fulfill({ status: 500, body: '{}' }); }
  });
  await page.goto(host);
  await page.waitForFunction(() => itensVisaoAtual.length === 1 && document.querySelector('#status').textContent.startsWith('Dados atualizados em '));
  await page.locator('#lojaBotoes button').filter({ hasText: 'Loja A' }).click();
  await page.waitForFunction(() => document.querySelector('#status').textContent.startsWith('Dados atualizados em '));
  return { context, page, errors, mutations, routeErrors, name };
}

async function run(page, delay) { return page.evaluate(delay => __view.run(delay), delay); }
async function plan(page, value) { await page.evaluate(value => __view.plan(value), value); }
async function clean(test) {
  assert.deepStrictEqual(test.errors, []);
  assert.deepStrictEqual(test.routeErrors, []);
  assert.deepStrictEqual(test.mutations, []);
  assert.strictEqual(test.page.url(), host);
  for (const call of await test.page.evaluate(() => __view.calls)) {
    assert.strictEqual(call.method, 'GET');
    assert.strictEqual(call.hasSignal, true);
    assert.strictEqual(call.headers.authorization, 'Bearer synthetic-view');
    assert.strictEqual(call.headers['x-client-id'], 'tenant-view');
    assert.strictEqual(new URL(call.url).searchParams.get('cache_mode'), 'stale');
  }
}

async function testDeferredDuringEdit(browser, outcome) {
  const test = await fixture(browser, outcome), { page } = test;
  try {
    await page.locator('.compra-sugerida-editavel').click();
    await page.locator('.compra-sugerida-input').fill('17');
    await page.evaluate(() => { window.__input = document.querySelector('.compra-sugerida-input'); });
    await plan(page, { body: payload('Anterior novo', 'v2', { stale: true }) });
    if (outcome === 'pending') {
      await page.evaluate(() => __view.fallback({ status: 202, body: { dados: null, _cache: { estado: 'waiting', atualizando: true, completo: false } } }));
      await plan(page, { pending: true });
    }
    else await plan(page, { body: payload(outcome === 'fresh' ? 'Atual novo' : 'Anterior novo', outcome === 'fresh' ? 'v3' : 'v2',
      outcome === 'error' ? { stale: true, error: true } : {}) });
    await page.evaluate(() => { window.__refresh = carregarVisao(periodoAtual); });
    await page.waitForFunction(() => document.querySelector('#status').textContent.includes('concluir a edição'));
    assert.strictEqual(await page.evaluate(() => itensVisaoAtual[0].titulo_anuncio), 'Inicial');
    await run(page, 1000);
    if (outcome === 'pending') await page.evaluate(() => __view.advance(30000));
    else await page.evaluate(() => window.__refresh);
    const expected = outcome === 'pending' ? /Continuamos acompanhando/ : outcome === 'error' ? /A atualização não terminou/ : /Dados atualizados em/;
    assert.match(await page.locator('#status').innerText(), expected);
    assert.strictEqual(await page.evaluate(() => document.activeElement === window.__input), true, 'Resposta não rouba o foco da edição.');
    assert.strictEqual(await page.evaluate(() => document.querySelector('.compra-sugerida-input') === window.__input), true);
    assert.strictEqual(await page.locator('.compra-sugerida-input').inputValue(), '17');
    assert.strictEqual(await page.evaluate(() => itensVisaoAtual[0].titulo_anuncio), 'Inicial');
    await page.locator('.compra-sugerida-input').evaluate(input => input.blur());
    assert.strictEqual(await page.locator('.compra-sugerida-input').count(), 0);
    assert.strictEqual(await page.evaluate(() => itensVisaoAtual[0].titulo_anuncio), outcome === 'fresh' ? 'Atual novo' : 'Anterior novo');
    assert.strictEqual(await page.evaluate(() => itensVisaoAtual[0].compra_sugerida), 17, 'Resultado novo preserva a quantidade confirmada.');
    assert.strictEqual(await page.evaluate(() => obterQuantidadesSugeridasEditadas()['SKU-COMUM']), 17);
    assert.match(await page.locator('#status').innerText(), expected, 'Blur preserva pendência/erro em vez de declarar sucesso.');
    assert.doesNotMatch(await page.locator('#status').innerText(), /concluir a edição/);
    if (outcome === 'pending') await page.evaluate(async () => { cancelarCarregamentoVisao(); await window.__refresh; });
    await clean(test);
  } finally { await test.context.close(); }
  console.log('PASS visão ' + outcome + ': resultado adiado mantém foco e valor; blur aplica dados preservando edição e status.');
}


async function testSessionDuringPendingView(browser, kind, event, betweenPolls = false) {
  const test = await fixture(browser, 'session-' + kind + '-' + event), { page } = test;
  try {
    await page.locator('.compra-sugerida-editavel').click();
    await page.locator('.compra-sugerida-input').fill('17');
    await page.locator('.compra-sugerida-input').press('Enter');
    await page.locator('.compra-sugerida-editavel').click();
    await page.locator('.compra-sugerida-input').fill('29');
    await plan(page, betweenPolls ? { body: payload('Resposta da sessão antiga', 'v2', { stale: true }) }
      : { body: payload('Resposta da sessão antiga', 'v2'), pending: true, ignoreAbort: true });
    await page.evaluate(() => { window.__refresh = carregarVisao(periodoAtual); });
    if (betweenPolls) await page.waitForFunction(() => document.querySelector('#status').textContent.includes('concluir a edição'));
    else await page.waitForFunction(() => __view.pending().length === 1);
    const index = betweenPolls ? null : await page.evaluate(() => __view.pending()[0]);
    await page.evaluate(({ kind, event }) => {
      const key = kind === 'tenant' ? 'user_data' : kind === 'permissions' ? 'permissions' : 'access_token';
      window.__oldAuth = { key, value: localStorage.getItem(key) };
      const value = kind === 'tenant' ? JSON.stringify({ client_id: 'tenant-other', username: 'other-user' })
        : kind === 'permissions' ? JSON.stringify({ medias_compras: false }) : 'synthetic-other';
      localStorage.setItem(key, value);
      if (event === 'storage') dispatchEvent(new StorageEvent('storage', { key, newValue: value, storageArea: localStorage }));
      if (event === 'auth') dispatchEvent(new Event('jk-auth-changed'));
    }, { kind, event });
    if (event !== 'none') {
      assert.strictEqual(await page.evaluate(() => itensVisaoAtual.length), 0, 'Mudança notificada limpa a visão imediatamente.');
      assert.strictEqual(await page.locator('.compra-sugerida-input').count(), 0);
    }
    if (betweenPolls) await run(page, 1000);
    else await page.evaluate(index => __view.release(index), index);
    await page.evaluate(() => window.__refresh);
    assert.strictEqual(await page.evaluate(() => itensVisaoAtual.length), 0, 'Resposta da sessão antiga não pode repovoar o modelo.');
    assert.strictEqual(await page.locator('#tblResultado [data-jk-virtual-row]').count(), 0);
    assert.strictEqual(await page.locator('.compra-sugerida-input').count(), 0);
    assert.deepStrictEqual(await page.evaluate(() => obterQuantidadesSugeridasEditadas()), {}, 'Edições confirmadas também são descartadas ao trocar sessão.');
    assert.match(await page.locator('#status').innerText(), /sessão mudou/i);
    await page.evaluate(() => {
      if (__oldAuth.value === null) localStorage.removeItem(__oldAuth.key);
      else localStorage.setItem(__oldAuth.key, __oldAuth.value);
      dispatchEvent(new Event('jk-auth-changed'));
    });
    await plan(page, { body: payload('Nova leitura após sessão', 'v3') });
    await page.evaluate(() => carregarVisao(periodoAtual));
    assert.strictEqual(await page.evaluate(() => itensVisaoAtual[0].titulo_anuncio), 'Nova leitura após sessão');
    assert.strictEqual(await page.evaluate(() => itensVisaoAtual[0].compra_sugerida), 5, 'Nenhuma edição anterior volta quando a tela carrega novamente.');
    assert.deepStrictEqual(await page.evaluate(() => obterQuantidadesSugeridasEditadas()), {});
    await clean(test);
  } finally { await test.context.close(); }
  console.log('PASS visão sessão ' + kind + '/' + event + (betweenPolls ? '/entre-polls' : '') + ': corpo antigo, edição aberta e override anterior são invalidados.');
}

async function testViewAccessDenied(browser, status) {
  const test = await fixture(browser, 'denied-' + status), { page } = test;
  try {
    await page.locator('.compra-sugerida-editavel').click();
    await page.locator('.compra-sugerida-input').fill('17');
    await page.locator('.compra-sugerida-input').press('Enter');
    await page.locator('.compra-sugerida-editavel').click();
    await page.locator('.compra-sugerida-input').fill('29');
    await plan(page, { status, body: { detail: 'Acesso removido.' } });
    await page.evaluate(() => carregarVisao(periodoAtual));
    assert.strictEqual(await page.evaluate(() => itensVisaoAtual.length), 0);
    assert.strictEqual(await page.locator('.compra-sugerida-input').count(), 0);
    assert.deepStrictEqual(await page.evaluate(() => obterQuantidadesSugeridasEditadas()), {});
    assert.match(await page.locator('#status').innerText(), /Acesso removido/);
    await clean(test);
  } finally { await test.context.close(); }
  console.log('PASS visão HTTP ' + status + ': conteúdo e edições locais são removidos sem gravação.');
}


async function testUnavailableAuthority(browser, invalidation, editing) {
  const test = await fixture(browser, 'authority-' + invalidation + '-' + editing), { page } = test;
  try {
    if (editing) {
      await page.locator('.compra-sugerida-editavel').click();
      await page.locator('.compra-sugerida-input').fill('73');
    }
    const previous = payload('Atualização anterior válida', 'v2', { stale: true });
    const waiting = { dados: null, _cache: { ...previous._cache, estado: 'waiting', completo: false,
      autoridade: invalidation === 'authority' ? 'authority-b' : 'authority-a',
      politica_fotos: invalidation === 'authority' ? 'photos-b' : 'photos-a',
      pendente_fontes: invalidation === 'sources' } };
    const fresh = payload(invalidation === 'same' ? 'Atualização anterior válida' : 'Novo resultado permitido', 'v2');
    fresh._cache.autoridade = waiting._cache.autoridade;
    fresh._cache.politica_fotos = waiting._cache.politica_fotos;
    await plan(page, { body: previous });
    await plan(page, { status: 202, body: waiting });
    await plan(page, { body: fresh });
    await page.evaluate(() => { window.__refresh = carregarVisao(periodoAtual); });
    await page.waitForFunction(editing => editing
      ? document.querySelector('#status').textContent.includes('concluir a edição')
      : itensVisaoAtual[0]?.titulo_anuncio === 'Atualização anterior válida', editing);
    await page.evaluate(() => {
      window.__authorityRow = document.querySelector('#tblResultado tbody [data-jk-virtual-row]');
      window.__authorityPhoto = document.querySelector('#tblResultado img.foto-sku');
      window.__authorityInput = document.querySelector('.compra-sugerida-input');
    });
    assert.strictEqual(await page.locator('#tblResultado img.foto-sku').count(), 1);
    await run(page, 1000);
    if (invalidation === 'same') {
      assert.strictEqual(await page.evaluate(() => __authorityRow === document.querySelector('#tblResultado tbody [data-jk-virtual-row]')), true, '202 da mesma autoridade mantém linhas montadas.');
      assert.strictEqual(await page.evaluate(() => __authorityPhoto.isConnected), true);
      if (editing) {
        assert.strictEqual(await page.evaluate(() => document.activeElement === __authorityInput), true);
        assert.strictEqual(await page.locator('.compra-sugerida-input').inputValue(), '73');
      }
    } else {
      assert.strictEqual(await page.evaluate(() => itensVisaoAtual.length), 0);
      assert.strictEqual(await page.locator('#tblResultado [data-jk-virtual-row]').count(), 0);
      assert.strictEqual(await page.locator('#tblResultado img.foto-sku').count(), 0);
      assert.strictEqual(await page.locator('.compra-sugerida-input').count(), 0);
      assert.strictEqual(await page.evaluate(() => __authorityRow.isConnected || __authorityPhoto.isConnected), false);
      assert.deepStrictEqual(await page.evaluate(() => obterQuantidadesSugeridasEditadas()), {}, 'Desmontar edição invalidada não confirma quantidade antiga.');
      if (editing) await page.evaluate(() => __authorityInput.dispatchEvent(new Event('blur')));
      assert.strictEqual(await page.evaluate(() => itensVisaoAtual.length), 0, 'Blur antigo não aplica resultado adiado invalidado.');
    }
    assert.match(await page.locator('#status').innerText(), /Atualizando dados/);
    await run(page, 1000);
    await page.evaluate(() => window.__refresh);
    if (editing && invalidation === 'same') await page.locator('.compra-sugerida-input').evaluate(input => input.blur());
    assert.strictEqual(await page.evaluate(() => itensVisaoAtual[0].titulo_anuncio), fresh.itens[0].titulo_anuncio, '200 após invalidação repinta mesmo quando reutiliza o identificador de versão anterior.');
    assert.strictEqual(await page.evaluate(() => itensVisaoAtual[0].compra_sugerida), editing && invalidation === 'same' ? 73 : 5);
    assert.match(await page.locator('#status').innerText(), /^Dados atualizados em /);
    await clean(test);
  } finally { await test.context.close(); }
  console.log('PASS visão 202 ' + invalidation + (editing ? '/edição-adiada' : '') + ': snapshot/fotos/edição respeitam autoridade e próxima 200 pode reutilizar versão.');
}

async function testLateCompletionAndEmpty(browser) {
  const test = await fixture(browser, 'late-and-empty'), { page } = test;
  try {
    await page.evaluate(() => __view.fallback({ status: 202, body: { dados: null,
      _cache: { estado: 'waiting', atualizando: true, completo: false } } }));
    await page.locator('#lojaBotoes button').filter({ hasText: 'Loja B' }).click();
    await page.waitForFunction(() => itensVisaoAtual.length === 0 && document.querySelector('#status').textContent.includes('Atualizando dados'));
    assert.match(await page.locator('#tblResultado tbody').innerText(), /Carregando médias/);
    assert.doesNotMatch(await page.locator('#tblResultado tbody').innerText(), /Nenhum SKU/);
    await page.evaluate(() => { window.__late = carregarVisao(periodoAtual); });
    await page.evaluate(() => __view.advance(35000));
    assert.match(await page.locator('#status').innerText(), /Continuamos acompanhando/);
    assert.match(await page.locator('#tblResultado tbody').innerText(), /Continuamos acompanhando/);
    const late = payload('Concluído depois de 30 segundos', 'late');
    await plan(page, { body: late });
    await page.evaluate(() => __view.advance(10000));
    await page.evaluate(() => window.__late);
    assert.strictEqual(await page.evaluate(() => itensVisaoAtual[0].titulo_anuncio), late.itens[0].titulo_anuncio);
    assert.strictEqual(await page.evaluate(() => itensVisaoAtual[0].media_mensal), late.itens[0].media_mensal);
    assert.match(await page.locator('#status').innerText(), /^Dados atualizados em /);
    const empty = { ...payload('', 'empty'), itens: [] };
    await plan(page, { body: empty });
    await page.evaluate(() => carregarVisao(3));
    assert.match(await page.locator('#tblResultado tbody').innerText(), /^Nenhum SKU encontrado para o período selecionado\.$/);
    assert.match(await page.locator('#status').innerText(), /^Dados atualizados em /);
    await clean(test);
  } finally { await test.context.close(); }
  console.log('PASS visão tardia: após 30 s continua acompanhando, recebe os valores e só declara zero SKUs quando o resultado completo está pronto.');
}

async function testLateNavigationCancellation(browser, destination) {
  const test = await fixture(browser, 'late-cancel-' + destination), { page } = test;
  try {
    await page.evaluate(() => __view.fallback({ status: 202, body: { dados: null,
      _cache: { estado: 'waiting', atualizando: true, completo: false } } }));
    await page.locator('#lojaBotoes button').filter({ hasText: 'Loja B' }).click();
    await page.waitForFunction(() => itensVisaoAtual.length === 0 && document.querySelector('#status').textContent.includes('Atualizando dados'));
    await page.evaluate(() => __view.advance(35000));
    await plan(page, { body: payload('Resposta antiga proibida', 'old-late'), pending: true, ignoreAbort: true });
    await page.evaluate(() => __view.advance(10000));
    const index = await page.evaluate(() => __view.pending()[0]);
    assert(Number.isInteger(index), 'Consulta antiga está aguardando transporte depois do aviso de 30 s.');
    if (destination === 'pagehide') await page.evaluate(() => dispatchEvent(new Event('pagehide')));
    else {
      await plan(page, { body: payload('Resultado do novo período', 'new-scope') });
      await page.locator('#btnPeriodo12').click();
      await page.waitForFunction(() => itensVisaoAtual[0]?.titulo_anuncio === 'Resultado do novo período');
    }
    assert.strictEqual(await page.evaluate(index => __view.calls[index].aborted, index), true);
    await page.evaluate(index => __view.release(index), index);
    const callsAfterCancel = await page.evaluate(() => __view.calls.length);
    await page.evaluate(() => __view.advance(90000));
    assert.strictEqual(await page.evaluate(() => __view.calls.length), callsAfterCancel, 'Troca de escopo/saída remove timer e não cria consultas adicionais.');
    assert.strictEqual(await page.evaluate(() => itensVisaoAtual[0]?.titulo_anuncio || ''), destination === 'pagehide' ? '' : 'Resultado do novo período');
    assert.doesNotMatch(await page.locator('#tblResultado tbody').innerText(), /Resposta antiga proibida/);
    await clean(test);
  } finally { await test.context.close(); }
  console.log('PASS visão cancelada depois de 30 s (' + destination + '): transporte interrompido, resposta antiga descartada e nenhum polling adicional.');
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    await testLateCompletionAndEmpty(browser);
    for (const destination of ['period', 'pagehide']) await testLateNavigationCancellation(browser, destination);
    for (const invalidation of ['same', 'authority', 'sources']) for (const editing of [false, true]) await testUnavailableAuthority(browser, invalidation, editing);
    for (const [kind, event] of [['tenant', 'none'], ['permissions', 'none'], ['token', 'none'], ['tenant', 'storage'], ['permissions', 'storage'], ['token', 'auth']]) await testSessionDuringPendingView(browser, kind, event);
    for (const kind of ['tenant', 'permissions', 'token']) await testSessionDuringPendingView(browser, kind, 'none', true);
    for (const status of [401, 403, 404]) await testViewAccessDenied(browser, status);
    for (const outcome of ['fresh', 'pending', 'error']) await testDeferredDuringEdit(browser, outcome);
  }
  finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
