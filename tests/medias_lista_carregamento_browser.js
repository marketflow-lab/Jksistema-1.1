'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const host = 'http://jk.test/medias_compras.html';
const html = fs.readFileSync(path.join(root, 'static/medias_compras.html'), 'utf8');
const stores = [{ nome: 'Loja A', store_id: 'store-a' }, { nome: 'Loja B', store_id: 'store-b' }];

function lista(id, store = stores[0], full = false) {
  return {
    id, nome_lista: 'Pedido ' + id.toUpperCase(), loja: store.nome, store_id: store.store_id,
    status: 'Analisando orçamento', supplier: 'Fornecedor sintético', fornecedor_id: 'supplier-test',
    created_at: '2026-10-13T10:00:00Z', updated_at: '2026-10-13T10:00:00Z',
    itens: [{ SKU: 'SKU-' + id.toUpperCase(), 'Título do produto em inglês': (full ? 'Complementado ' : 'Salvo ') + id,
      Quantidade: id === 'a' ? 0 : 2, 'Valor unidade': id === 'a' ? 0 : 10,
      'Valor total': id === 'a' ? 0 : 20, OEM: full ? 'OEM-' + id : '',
      'Estimed CBM': full ? 0.02 : '', 'M3 individual': full ? 0.01 : '' }],
  };
}

async function fixture(browser, tenant, { twoItems = false, materializedLegacy = false, allowSave = false, longList = false } = {}) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  const state = { errors: [], routeErrors: [], calls: [], saves: [] };
  const saved = { a: lista('a'), b: lista('b'), c: lista('c', stores[1]) };
  const enriched = { a: lista('a', stores[0], true), b: lista('b', stores[0], true), c: lista('c', stores[1], true) };
  if (longList) {
    for (const data of [saved, enriched]) {
      data.a.itens = Array.from({ length: 200 }, (_, index) => ({ ...data.a.itens[0], SKU: 'LONG-' + index }));
      for (let index = 0; index < 40; index++) data['extra-' + index] = lista('extra-' + index);
    }
  }
  if (twoItems) {
    const extra = { SKU: 'SKU-B2', 'Título do produto em inglês': 'Segundo item salvo', Quantidade: 3,
      'Valor unidade': 5, 'Valor total': 15, 'Estimed CBM': '', 'M3 individual': '' };
    saved.b.itens.push(extra);
    enriched.b.itens.push({ ...extra, 'Título do produto em inglês': 'Segundo item complementado' });
  }
  if (materializedLegacy) {
    // A API materializa os aliases antigos antes de enviar a resposta rápida.
    saved.a.itens = [{ sku: 'LEGACY-001', quantidade: 13, valor_unitario: 1.3, total: 16.9,
      SKU: 'LEGACY-001', Quantidade: 13, 'Valor unidade': 1.3, 'Valor total': 16.9,
      'Título do produto em inglês': 'Salvo a', 'Estimed CBM': '', 'M3 individual': '' }];
  }
  page.on('pageerror', error => state.errors.push(error.message));
  await page.addInitScript(({ tenant, saved, enriched }) => {
    localStorage.setItem('user_data', JSON.stringify({ client_id: tenant, username: 'synthetic-user' }));
    localStorage.setItem('access_token', 'synthetic-' + tenant);
    const nativeTimeout = window.setTimeout.bind(window);
    const nativeClear = window.clearTimeout.bind(window);
    const nativeFetch = window.fetch.bind(window);
    let timerId = 0;
    const timers = new Map();
    const plans = new Map();
    const pending = new Map();
    const calls = [];
    const tick = () => new Promise(resolve => nativeTimeout(resolve, 0));
    window.setTimeout = (callback, delay, ...args) => {
      if (delay === 300 || delay === 5000) {
        const id = --timerId;
        timers.set(id, { delay, callback: () => callback(...args) });
        return id;
      }
      return nativeTimeout(callback, delay, ...args);
    };
    window.clearTimeout = id => {
      if (id < 0) timers.delete(id);
      else nativeClear(id);
    };
    window.fetch = async (input, init = {}) => {
      const url = new URL(String(input), location.href);
      const match = url.pathname.match(/^\/api\/medias-compras\/listas-pedidos\/([^/]+)$/);
      if (!match || (init.method && init.method !== 'GET')) return nativeFetch(input, init);
      const id = decodeURIComponent(match[1]);
      const fast = url.searchParams.get('somente_salvos') === 'true';
      const key = id + ':' + (fast ? 'saved' : 'full');
      const queue = plans.get(key) || [];
      const plan = queue.shift() || {};
      const index = calls.length;
      const call = { index, id, fast, url: url.href, headers: Object.fromEntries(new Headers(init.headers).entries()),
        method: init.method || 'GET', hasSignal: !!init.signal, aborted: !!init.signal?.aborted,
        phase: plan.phase || 'immediate', finished: false };
      calls.push(call);
      const body = plan.body || { lista: (fast ? saved : enriched)[id] };
      let rejectPending;
      const wait = () => new Promise((resolve, reject) => {
        rejectPending = reject;
        pending.set(index, { resolve: value => { pending.delete(index); resolve(value || body); },
          reject: message => { pending.delete(index); reject(new Error(message)); } });
      });
      init.signal?.addEventListener('abort', () => {
        call.aborted = true;
        if (!plan.ignoreAbort && rejectPending) {
          pending.delete(index);
          rejectPending(new DOMException('Synthetic request aborted', 'AbortError'));
        }
      });
      if (plan.error) { call.finished = true; throw new Error(plan.error); }
      if (plan.phase === 'fetch') await wait();
      return {
        ok: (plan.status || 200) < 400, status: plan.status || 200,
        json: async () => {
          const result = plan.phase === 'body' ? await wait() : body;
          call.finished = true;
          return result;
        },
      };
    };
    window.__listHarness = {
      calls, plan: (key, plan) => { const queue = plans.get(key) || []; queue.push(plan); plans.set(key, queue); },
      release: async (index, body) => { if (!pending.has(index)) throw new Error('No pending call ' + index);
        pending.get(index).resolve(body); await tick(); await tick(); },
      fail: async (index, message) => { if (!pending.has(index)) throw new Error('No pending call ' + index);
        pending.get(index).reject(message); await tick(); await tick(); },
      run: async delay => { const selected = [...timers].filter(([, timer]) => timer.delay === delay);
        for (const [id, timer] of selected) { timers.delete(id); timer.callback(); } await tick(); await tick(); return selected.length; },
      timers: () => [...timers.values()].map(timer => timer.delay), pending: () => [...pending.keys()],
      setSaved: (id, item) => { saved[id] = item; enriched[id] = item; },
    };
  }, { tenant, saved, enriched });
  await page.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    try {
      if (url.origin !== 'http://jk.test') return route.fulfill({ status: 200, contentType: 'text/css', body: '' });
      if (url.pathname === '/medias_compras.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: html });
      if (url.pathname.startsWith('/medias_compras/')) return route.fulfill({ status: 200,
        contentType: url.pathname.endsWith('.css') ? 'text/css' : 'application/javascript',
        body: fs.readFileSync(path.join(root, 'static', url.pathname.slice(1))) });
      if (url.pathname === '/auth.js') return route.fulfill({ status: 200, contentType: 'application/javascript', body:
        'window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({Authorization:"Bearer synthetic-' + tenant + '","X-Client-ID":"' + tenant + '"});' });
      if (url.pathname.startsWith('/api/')) {
        assert.strictEqual(request.headers().authorization, 'Bearer synthetic-' + tenant);
        assert.strictEqual(request.headers()['x-client-id'], tenant);
        const updateId = url.pathname.match(/^\/api\/medias-compras\/listas-pedidos\/([^/]+)$/);
        if (allowSave && request.method() === 'PUT' && updateId) {
          const id = decodeURIComponent(updateId[1]);
          const payload = request.postDataJSON();
          state.saves.push(payload);
          saved[id] = { ...saved[id], ...payload };
          await page.evaluate(({ id, item }) => __listHarness.setSaved(id, item), { id, item: saved[id] });
          return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ lista: saved[id] }) });
        }
        assert.strictEqual(request.method(), 'GET', 'Somente o caso explícito de salvamento pode executar um PUT sintético.');
        state.calls.push({ pathname: url.pathname, params: Object.fromEntries(url.searchParams) });
        let body;
        if (url.pathname === '/api/lojas') body = stores;
        else if (url.pathname === '/api/cadastro/fornecedores') body = [{ id: 'supplier-test', nome_empresa: 'Fornecedor sintético' }];
        else if (url.pathname === '/api/medias-compras/preferencias-skus-ocultos') body = { skus_ocultos: [] };
        else if (url.pathname === '/api/medias-compras/visao') body = { colunas_meses: [], itens: [] };
        else if (url.pathname === '/api/medias-compras/posicao-estoque') body = {
          sku: url.searchParams.get('sku'), store_id: url.searchParams.get('store_id'), loja: url.searchParams.get('loja'),
          saldo_atual_estoque: 0, estoque_em_transito: 0, estoque_em_transito_listas: [],
        };
        else if (url.pathname === '/api/medias-compras/listas-pedidos') {
          const store = stores.find(store => store.store_id === url.searchParams.get('store_id'));
          if (store) assert.strictEqual(url.searchParams.get('loja'), store.nome);
          body = { listas: Object.values(saved).filter(item => !store || item.store_id === store.store_id)
            .map(item => ({ ...item, total_itens: item.itens.length })) };
        } else throw new Error('API inesperada: ' + url.pathname);
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
      }
      return route.fulfill({ status: 200, contentType: 'application/javascript', body: '' });
    } catch (error) {
      state.routeErrors.push(error.message);
      return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: error.message }) });
    }
  });
  await page.goto(host);
  await page.locator('#lojaBotoes button').filter({ hasText: 'Loja A' }).click();
  await page.locator('#btnAbaListasPedidos').click();
  await page.locator('.lista-item').filter({ hasText: 'Pedido A' }).waitFor();
  return { page, context, state, tenant };
}

async function plan(page, key, value) { await page.evaluate(({ key, value }) => __listHarness.plan(key, value), { key, value }); }
async function run(page, delay) { return page.evaluate(delay => __listHarness.run(delay), delay); }
async function click(page, id) { await page.locator('.lista-item').filter({ hasText: 'Pedido ' + id.toUpperCase() }).click(); }
async function ready(page, id, title = 'Salvo ' + id) {
  await page.waitForFunction(({ id, title }) => listaPedidoAtual?.id === id &&
    document.querySelector('#tblListaPedidoItens tbody').textContent.includes(title) && !document.querySelector('#editorListaPedido').inert, { id, title });
}
async function calls(page) { return page.evaluate(() => __listHarness.calls); }
async function release(page, index, body) { await page.evaluate(({ index, body }) => __listHarness.release(index, body), { index, body }); }
async function fail(page, index, message) { await page.evaluate(({ index, message }) => __listHarness.fail(index, message), { index, message }); }
async function clean(test) {
  assert.strictEqual(test.page.url(), host);
  assert.deepStrictEqual(test.state.errors, []);
  assert.deepStrictEqual(test.state.routeErrors, []);
  for (const call of await calls(test.page)) {
    assert.strictEqual(call.method, 'GET');
    assert.strictEqual(call.hasSignal, true, 'Cada detalhe deve aceitar cancelamento.');
    assert.strictEqual(call.headers.authorization, 'Bearer synthetic-' + test.tenant);
    assert.strictEqual(call.headers['x-client-id'], test.tenant);
    const url = new URL(call.url);
    assert.deepStrictEqual([...url.searchParams.entries()], call.fast ? [['somente_salvos', 'true']] : []);
  }
}

async function testSavedFirstAndStore(browser) {
  const test = await fixture(browser, 'tenant-saved');
  const { page } = test;
  try {
    await plan(page, 'a:full', { phase: 'body', ignoreAbort: true });
    await click(page, 'a');
    await ready(page, 'a');
    assert.deepStrictEqual((await calls(page)).map(call => [call.id, call.fast]), [['a', true]], 'Itens salvos aparecem antes da consulta de complemento.');
    assert.deepStrictEqual(await page.evaluate(() => __listHarness.timers()), [300]);
    assert.strictEqual(await page.locator('input[data-kind="qtd"]').inputValue(), '0');
    assert.strictEqual(await page.evaluate(() => listaPedidoAtual.itens[0]['Valor unidade']), 0);
    assert.strictEqual(await page.locator('input[data-kind="vtot"]').inputValue(), '0');
    await run(page, 300);
    assert.strictEqual((await calls(page))[1].finished, false);
    await ready(page, 'a');
    assert.strictEqual(await page.locator('#btnSalvarListaPedido').isEnabled(), true);
    await release(page, 1);
    await ready(page, 'a', 'Complementado a');
    assert.strictEqual(await page.locator('input[data-kind="qtd"]').inputValue(), '0');
    assert.strictEqual(await page.evaluate(() => listaPedidoAtual.itens[0]['Valor unidade']), 0);
    await page.locator('#lojaBotoes button').filter({ hasText: 'Loja B' }).click();
    await page.locator('.lista-item').filter({ hasText: 'Pedido C' }).waitFor();
    await click(page, 'c');
    await ready(page, 'c');
    assert.strictEqual(await page.locator('#lojaListaPedidoEdit').inputValue(), 'store-b');
    assert(test.state.calls.some(call => call.pathname.endsWith('/listas-pedidos') && call.params.store_id === 'store-a' && call.params.loja === 'Loja A'));
    assert(test.state.calls.some(call => call.pathname.endsWith('/listas-pedidos') && call.params.store_id === 'store-b' && call.params.loja === 'Loja B'));
    await clean(test);
  } finally { await test.context.close(); }
  console.log('PASS snapshot: itens salvos aparecem antes do complemento; zero, autenticação, cliente e loja são preservados.');
}

async function testSelectionRaces(browser) {
  for (const late of ['body', 'error', 'fetch']) {
    const test = await fixture(browser, 'tenant-race-' + late);
    const { page } = test;
    try {
      await click(page, 'a');
      await ready(page, 'a');
      await plan(page, 'b:saved', { phase: late === 'fetch' ? 'fetch' : 'body', ignoreAbort: late !== 'fetch' });
      await click(page, 'b');
      await page.waitForFunction(() => __listHarness.calls.length === 2);
      assert.strictEqual(await page.locator('#nomeListaPedidoEdit').inputValue(), '');
      assert.strictEqual(await page.locator('input[data-kind="sku"]').count(), 0, 'A espera não pode exibir itens da lista anterior.');
      assert.strictEqual(await page.locator('#editorListaPedido').evaluate(el => el.inert), true);
      assert.strictEqual(await page.locator('#statusListaPedido').innerText(), 'Abrindo lista...');
      await click(page, 'a');
      await ready(page, 'a');
      assert.strictEqual((await calls(page))[1].aborted, true, 'A seleção antiga recebe abort.');
      if (late === 'error') await fail(page, 1, 'Erro antigo sintético');
      else if (late === 'body') await release(page, 1);
      assert.strictEqual(await page.evaluate(() => listaPedidoAtual.id), 'a');
      assert.strictEqual(await page.locator('#nomeListaPedidoEdit').inputValue(), 'Pedido A');
      assert.strictEqual(await page.locator('#statusListaPedido').innerText(), 'Lista carregada.');
      assert.strictEqual(await run(page, 300), 1, 'Somente a seleção atual agenda complemento.');
      await ready(page, 'a', 'Complementado a');
      assert.deepStrictEqual((await calls(page)).map(call => [call.id, call.fast]), [['a', true], ['b', true], ['a', true], ['a', false]]);
      await clean(test);
    } finally { await test.context.close(); }
  }
  console.log('PASS troca: limpa itens antigos, aborta consulta anterior e ignora corpo/erro atrasados.');
}

async function testInitialFailuresAndRetry(browser) {
  const cases = [
    { name: 'http', plan: { status: 500, body: { detail: 'Falha sintética inicial.' } }, message: 'Falha sintética inicial.' },
    { name: 'timeout', plan: { phase: 'body', ignoreAbort: true }, message: 'A lista demorou para responder. Clique nela para tentar novamente.' },
  ];
  for (const item of cases) {
    const test = await fixture(browser, 'tenant-failure-' + item.name);
    const { page } = test;
    try {
      await plan(page, 'a:saved', item.plan);
      await click(page, 'a');
      if (item.name === 'timeout') {
        await page.waitForFunction(() => __listHarness.pending().includes(0));
        assert.strictEqual(await run(page, 5000), 1, 'O limite inclui leitura do corpo.');
        assert.strictEqual((await calls(page))[0].aborted, true);
      }
      await page.waitForFunction(message => document.querySelector('#statusListaPedido').textContent === message, item.message);
      assert.strictEqual(await page.evaluate(() => listaPedidoAtual), null);
      assert.strictEqual(await page.locator('#editorListaPedido').evaluate(el => el.inert), false);
      assert.strictEqual(await page.locator('#btnSalvarListaPedido').isDisabled(), true);
      await click(page, 'a');
      await ready(page, 'a');
      if (item.name === 'timeout') await release(page, 0);
      assert.strictEqual(await page.locator('#statusListaPedido').innerText(), 'Lista carregada.');
      assert.deepStrictEqual((await calls(page)).map(call => [call.id, call.fast]), [['a', true], ['a', true]]);
      await clean(test);
    } finally { await test.context.close(); }
  }
  console.log('PASS abertura: erro e prazo vencido terminam a espera; novo clique recupera e ignora resposta antiga.');
}

async function testComplementFailureAndSelection(browser) {
  for (const mode of ['http', 'timeout', 'selection']) {
    const test = await fixture(browser, 'tenant-complement-' + mode);
    const { page } = test;
    try {
      await plan(page, 'b:full', mode === 'http' ? { status: 503, body: { detail: 'Complemento indisponível.' } } : { phase: 'body', ignoreAbort: true });
      await click(page, 'b');
      await ready(page, 'b');
      await run(page, 300);
      if (mode === 'selection') {
        await click(page, 'a');
        await ready(page, 'a');
        assert.strictEqual((await calls(page))[1].aborted, true);
        await release(page, 1);
        await ready(page, 'a');
        assert.strictEqual(await page.locator('#statusListaPedido').innerText(), 'Lista carregada.');
      } else {
        if (mode === 'timeout') await run(page, 5000);
        await page.waitForFunction(() => document.querySelector('#statusListaPedido').textContent.includes('A atualização dos dados não terminou'));
        assert.strictEqual(await page.locator('#nomeListaPedidoEdit').inputValue(), 'Pedido B');
        await ready(page, 'b');
        assert.strictEqual(await page.locator('input[data-kind="qtd"]').inputValue(), '2');
        assert.strictEqual(await page.locator('#btnSalvarListaPedido').isEnabled(), true);
        if (mode === 'timeout') {
          assert.strictEqual((await calls(page))[1].aborted, true);
          await release(page, 1);
          await ready(page, 'b');
        }
      }
      await clean(test);
    } finally { await test.context.close(); }
  }
  console.log('PASS complemento: falha/prazo preservam os itens salvos; trocar lista descarta complemento antigo.');
}

async function testEditingPreservesDom(browser) {
  for (const when of ['before-start', 'pending-body', 'change']) {
    const test = await fixture(browser, 'tenant-edits-' + when, { allowSave: true });
    const { page } = test;
    try {
      await plan(page, 'b:full', { phase: 'body', ignoreAbort: true });
      await click(page, 'b');
      await ready(page, 'b');
      if (when !== 'before-start') await run(page, 300);
      await page.evaluate(() => { window.__editedRow = document.querySelector('#tblListaPedidoItens tbody tr'); });
      if (when === 'change') {
        await page.locator('#lojaListaPedidoEdit').selectOption('store-b');
      } else {
        await page.locator('#nomeListaPedidoEdit').fill('Nome editado pelo usuário');
        await page.locator('input[data-kind="qtd"]').fill('7');
      }
      if (when === 'before-start') {
        assert.strictEqual(await run(page, 300), 0, 'Editar antes do agendamento cancela o complemento.');
        assert.strictEqual((await calls(page)).length, 1);
      } else {
        assert.strictEqual((await calls(page))[1].aborted, true, 'Editar durante complemento cancela a consulta.');
        await release(page, 1);
      }
      assert.strictEqual(await page.evaluate(() => window.__editedRow === document.querySelector('#tblListaPedidoItens tbody tr')), true, 'A resposta não pode reconstruir o DOM editado.');
      if (when === 'change') assert.strictEqual(await page.locator('#lojaListaPedidoEdit').inputValue(), 'store-b');
      else {
        assert.strictEqual(await page.locator('#nomeListaPedidoEdit').inputValue(), 'Nome editado pelo usuário');
        assert.strictEqual(await page.locator('input[data-kind="qtd"]').inputValue(), '7');
        assert.strictEqual(await page.locator('input[data-kind="vtot"]').inputValue(), '70');
      }
      if (when === 'change') await page.waitForFunction(() => document.querySelector('#statusListaPedido').textContent === 'Lista salva com sucesso.');
      else assert.strictEqual(await page.locator('#statusListaPedido').innerText(), 'Alterações pendentes de confirmação.');
      await clean(test);
    } finally { await test.context.close(); }
  }
  console.log('PASS edição: nome/quantidade e seleção de loja permanecem; consulta tardia preserva o mesmo DOM.');
}

async function testStructuralEdits(browser) {
  for (const action of ['delete', 'reorder']) {
    for (const when of ['before-start', 'pending-body', 'pending-failure']) {
      const test = await fixture(browser, 'tenant-structural-' + action + '-' + when, { twoItems: true, allowSave: true });
      const { page } = test;
      try {
        await plan(page, 'b:full', { phase: 'body', ignoreAbort: true });
        await click(page, 'b');
        await ready(page, 'b');
        assert.strictEqual(await page.locator('tr[data-lista-pedido-row="1"]').count(), 2);
        if (when !== 'before-start') await run(page, 300);
        if (action === 'delete') {
          await page.evaluate(() => { window.confirm = () => true; });
          await page.locator('button[data-kind="excluir-item"]').first().click();
        } else {
          await page.evaluate(() => {
            const tbody = document.querySelector('#tblListaPedidoItens tbody');
            const rows = [...tbody.querySelectorAll('tr[data-lista-pedido-row="1"]')];
            tbody.dataset.dragStartOrder = rows.map(row => row.dataset.idx).join('|');
            tbody.insertBefore(rows[1], rows[0]);
            finalizarArrasteLinhasListaPedido(tbody);
          });
        }
        await page.waitForFunction(() => document.querySelector('#statusListaPedido').textContent === 'Lista salva com sucesso.');
        const expected = action === 'delete' ? ['SKU-B2'] : ['SKU-B2', 'SKU-B'];
        assert.deepStrictEqual(await page.evaluate(() => listaPedidoAtual.itens.map(item => item.SKU)), expected);
        const statusEdited = await page.locator('#statusListaPedido').innerText();
        await page.evaluate(() => { window.__structuralRow = document.querySelector('#tblListaPedidoItens tbody tr'); });
        if (when === 'before-start') {
          await run(page, 300);
          assert.strictEqual((await calls(page)).length, 1, 'Excluir/reordenar antes do complemento deve impedir a consulta pesada.');
        } else {
          if (when === 'pending-failure') {
            if (action === 'delete') await fail(page, 1, 'Erro de complemento após edição estrutural.');
            else await run(page, 5000);
          } else await release(page, 1);
          assert.strictEqual((await calls(page)).length, 2);
        }
        assert.deepStrictEqual(await page.evaluate(() => listaPedidoAtual.itens.map(item => item.SKU)), expected, 'Complemento não pode restaurar SKU excluído nem desfazer a ordem.');
        assert.deepStrictEqual(await page.locator('input[data-kind="sku"]').evaluateAll(inputs => inputs.map(input => input.value)), expected);
        assert.strictEqual(await page.evaluate(() => window.__structuralRow === document.querySelector('#tblListaPedidoItens tbody tr')), true);
        assert.strictEqual(await page.locator('#statusListaPedido').innerText(), statusEdited);
        assert.strictEqual(await page.locator('input[data-kind="qtd"]').first().inputValue(), '3');
        assert.strictEqual(await page.evaluate(() => listaPedidoAtual.itens[0]['Valor unidade']), 5);
        await clean(test);
      } finally { await test.context.close(); }
    }
  }
  console.log('PASS exclusão/ordem: ações antes do timer impedem complemento; resposta, erro e prazo vencido preservam itens, ordem, DOM e aviso.');
}

async function testStoreSwitchCancelsSnapshot(browser) {
  for (const outcome of ['body', 'error']) {
    const test = await fixture(browser, 'tenant-store-switch-' + outcome);
    const { page } = test;
    try {
      await plan(page, 'a:saved', { phase: 'body', ignoreAbort: true });
      await click(page, 'a');
      await page.waitForFunction(() => __listHarness.pending().includes(0));
      await page.locator('#lojaBotoes button').filter({ hasText: 'Loja B' }).click();
      await page.locator('.lista-item').filter({ hasText: 'Pedido C' }).waitFor();
      assert.strictEqual((await calls(page))[0].aborted, true, 'Trocar filtro de loja cancela o snapshot anterior.');
      assert.strictEqual(await page.evaluate(() => listaPedidoAtual), null);
      assert.strictEqual(await page.locator('#editorListaPedido').evaluate(el => el.classList.contains('hidden')), true);
      assert.strictEqual(await page.locator('input[data-kind="sku"]').count(), 0);
      const statusAfterSwitch = await page.locator('#statusListaPedido').innerText();
      if (outcome === 'body') await release(page, 0);
      else await fail(page, 0, 'Falha tardia da loja anterior.');
      assert.strictEqual(await page.evaluate(() => listaPedidoAtual), null, 'A lista da loja anterior não pode reaparecer.');
      assert.strictEqual(await page.locator('#editorListaPedido').evaluate(el => el.classList.contains('hidden')), true);
      assert.strictEqual(await page.locator('#statusListaPedido').innerText(), statusAfterSwitch);
      assert.strictEqual(await run(page, 300), 0, 'A loja anterior não pode agendar complemento.');
      await click(page, 'c');
      await ready(page, 'c');
      assert.strictEqual(await page.locator('#lojaListaPedidoEdit').inputValue(), 'store-b');
      await clean(test);
    } finally { await test.context.close(); }
  }
  console.log('PASS filtro de loja: troca cancela snapshot pendente; resposta/erro antigos não reabrem editor nem alteram status.');
}

async function testDragInProgressPreservesDom(browser) {
  const test = await fixture(browser, 'tenant-drag-in-progress', { twoItems: true, allowSave: true });
  const { page } = test;
  try {
    await plan(page, 'b:full', { phase: 'body', ignoreAbort: true });
    await click(page, 'b');
    await ready(page, 'b');
    await run(page, 300);
    await page.evaluate(() => {
      const tbody = document.querySelector('#tblListaPedidoItens tbody');
      const first = tbody.querySelector('tr[data-lista-pedido-row="1"]');
      window.__draggingRow = first;
      first.dispatchEvent(new DragEvent('dragstart', { bubbles: true, dataTransfer: new DataTransfer() }));
      tbody.appendChild(first);
    });
    assert.strictEqual((await calls(page))[1].aborted, true, 'Começar arraste cancela complemento antes de finalizar a ordem.');
    assert.deepStrictEqual(await page.locator('input[data-kind="sku"]').evaluateAll(inputs => inputs.map(input => input.value)), ['SKU-B2', 'SKU-B']);
    assert.deepStrictEqual(await page.evaluate(() => listaPedidoAtual.itens.map(item => item.SKU)), ['SKU-B', 'SKU-B2'], 'Durante arraste o objeto ainda mantém a ordem anterior.');
    await release(page, 1);
    assert.strictEqual(await page.evaluate(() => window.__draggingRow === document.querySelector('tr.dragging')), true, 'Resposta não pode reconstruir a linha enquanto o usuário arrasta.');
    assert.deepStrictEqual(await page.locator('input[data-kind="sku"]').evaluateAll(inputs => inputs.map(input => input.value)), ['SKU-B2', 'SKU-B']);
    assert.strictEqual(await page.locator('#tblListaPedidoItens tbody').evaluate(tbody => tbody.dataset.dragStartOrder), '0|1');
    await page.evaluate(() => finalizarArrasteLinhasListaPedido(document.querySelector('#tblListaPedidoItens tbody')));
    assert.deepStrictEqual(await page.evaluate(() => listaPedidoAtual.itens.map(item => item.SKU)), ['SKU-B2', 'SKU-B']);
    assert.strictEqual(await page.locator('tr.dragging').count(), 0);
    await page.waitForFunction(() => document.querySelector('#statusListaPedido').textContent === 'Lista salva com sucesso.');
    await clean(test);
  } finally { await test.context.close(); }
  console.log('PASS arraste aberto: dragstart cancela complemento e mantém a mesma linha/DOM até confirmar a nova ordem.');
}

async function testMaterializedLegacyRename(browser) {
  const test = await fixture(browser, 'tenant-legacy-save', { materializedLegacy: true, allowSave: true });
  const { page } = test;
  try {
    await click(page, 'a');
    await ready(page, 'a');
    assert.strictEqual(await page.locator('input[data-kind="sku"]').inputValue(), 'LEGACY-001');
    assert.strictEqual(await page.locator('input[data-kind="qtd"]').inputValue(), '13');
    assert.strictEqual(await page.evaluate(() => toNumeroDecimal(document.querySelector('input[data-kind="vun"]').value)), 1.3);
    assert.strictEqual(await page.locator('input[data-kind="vtot"]').inputValue(), '16.9');
    await page.locator('#nomeListaPedidoEdit').fill('Pedido legado renomeado');
    assert.strictEqual(await run(page, 300), 0);
    await page.locator('#btnSalvarListaPedido').click();
    await page.waitForFunction(() => document.querySelector('#statusListaPedido').textContent === 'Lista salva com sucesso.');
    assert(test.state.saves.length >= 1 && test.state.saves.length <= 2, 'Confirmação automática e botão manual podem compartilhar a gravação pendente ou confirmar novamente.');
    const payload = test.state.saves.at(-1);
    assert.strictEqual(payload.nome_lista, 'Pedido legado renomeado');
    assert.strictEqual(payload.store_id, 'store-a');
    assert.strictEqual(payload.loja, 'Loja A');
    assert.strictEqual(payload.itens[0].SKU, 'LEGACY-001');
    assert.strictEqual(payload.itens[0].Quantidade, 13);
    assert.strictEqual(payload.itens[0]['Valor unidade'], 1.3);
    assert.strictEqual(payload.itens[0]['Valor total'], 16.9);
    assert.strictEqual(await page.locator('input[data-kind="qtd"]').inputValue(), '13');
    assert.strictEqual(await page.evaluate(() => listaPedidoAtual.itens[0]['Valor unidade']), 1.3);
    await clean(test);
  } finally { await test.context.close(); }
  console.log('PASS legado materializado: renomear e salvar preservam SKU, quantidade, preço e total do snapshot rápido.');
}

async function testSavePreservesViewport(browser) {
  const test = await fixture(browser, 'tenant-save-position', { allowSave: true, longList: true });
  const { page } = test;
  try {
    await click(page, 'a');
    await ready(page, 'a');
    await page.addStyleTag({ content: '.editor-lista { min-width: 0; } .mini-table-wrap { max-width: 600px; }' });
    await page.evaluate(() => {
      const name = document.getElementById('nomeListaPedidoEdit');
      name.value = 'Pedido A editado';
      name.dispatchEvent(new Event('input', { bubbles: true }));
      document.getElementById('listasPedidosLista').scrollTop = 700;
      document.querySelector('.mini-table-wrap').scrollLeft = 180;
      window.scrollTo(0, 1400);
      window.__savedFirstRow = document.querySelector('#tblListaPedidoItens tbody').firstElementChild;
    });
    const viewport = () => page.evaluate(() => ({ y: window.scrollY,
      list: document.getElementById('listasPedidosLista').scrollTop,
      x: document.querySelector('.mini-table-wrap').scrollLeft,
      rows: document.querySelectorAll('#tblListaPedidoItens tbody tr[data-lista-pedido-row]').length }));
    const before = await viewport();
    assert.deepStrictEqual(before, { y: 1400, list: 700, x: 180, rows: 200 }, 'A fixture precisa de rolagem real vertical, horizontal e no painel lateral.');
    await plan(page, 'a:saved', { phase: 'body' });
    await page.evaluate(() => { window.__savePending = salvarListaPedidoAtual(); });
    await page.waitForFunction(() => __listHarness.calls.length === 2 && __listHarness.pending().length === 1);
    assert.deepStrictEqual(await viewport(), before, 'Atualizar a mesma lista após salvar não pode encolher a tabela nem perder a posição.');
    assert.strictEqual(await page.evaluate(() => window.__savedFirstRow === document.querySelector('#tblListaPedidoItens tbody').firstElementChild), true, 'Os itens salvos continuam visíveis durante a atualização.');
    assert.strictEqual(await page.locator('#editorListaPedido').evaluate(el => el.inert), true, 'A atualização pendente deve impedir novas edições sobre o snapshot anterior.');
    await release(page, 1);
    await page.evaluate(() => window.__savePending);
    assert.deepStrictEqual(await viewport(), before, 'A posição deve permanecer depois da resposta da mesma lista.');
    assert.strictEqual(await page.locator('#nomeListaPedidoEdit').inputValue(), 'Pedido A editado');
    assert.strictEqual(await page.locator('#statusListaPedido').innerText(), 'Lista salva com sucesso.');
    assert.strictEqual(test.state.saves[0].itens.length, 200);
    await clean(test);
  } finally { await test.context.close(); }
  console.log('PASS posição ao salvar: 200 itens e rolagens vertical, horizontal e lateral preservados antes, durante e após a atualização.');
}

async function testSaveRefreshFailures(browser) {
  for (const mode of ['error', 'timeout']) {
    const test = await fixture(browser, 'tenant-save-failure-' + mode, { allowSave: true, longList: true });
    const { page } = test;
    try {
      await click(page, 'a');
      await ready(page, 'a');
      await page.evaluate(() => {
        const name = document.getElementById('nomeListaPedidoEdit');
        name.value = 'Pedido A salvo';
        name.dispatchEvent(new Event('input', { bubbles: true }));
        window.scrollTo(0, 1400);
        window.__rowBeforeFailedRefresh = document.querySelector('#tblListaPedidoItens tbody').firstElementChild;
      });
      await plan(page, 'a:saved', { phase: 'body', ignoreAbort: true });
      await page.evaluate(() => { window.__savePending = salvarListaPedidoAtual(); });
      await page.waitForFunction(() => __listHarness.pending().includes(1));
      if (mode === 'error') await fail(page, 1, 'Falha sintética ao atualizar a lista salva.');
      else assert.strictEqual(await run(page, 5000), 1);
      await page.evaluate(() => window.__savePending);
      assert.match(await page.locator('#statusListaPedido').innerText(), mode === 'error' ? /Falha sintética/ : /demorou para responder/);
      assert.strictEqual(await page.evaluate(() => window.scrollY), 1400, 'A falha da atualização mantém a posição na lista salva.');
      assert.strictEqual(await page.locator('input[data-kind="sku"]').count(), 200);
      assert.strictEqual(await page.evaluate(() => window.__rowBeforeFailedRefresh === document.querySelector('#tblListaPedidoItens tbody').firstElementChild), true);
      assert.strictEqual(await page.locator('#editorListaPedido').evaluate(el => el.inert), false, 'A falha libera edição e nova tentativa.');
      await page.evaluate(async () => {
        const name = document.getElementById('nomeListaPedidoEdit');
        name.value = 'Pedido A após nova tentativa';
        name.dispatchEvent(new Event('input', { bubbles: true }));
        await salvarListaPedidoAtual();
      });
      if (mode === 'timeout') await release(page, 1);
      assert.strictEqual(await page.locator('#statusListaPedido').innerText(), 'Lista salva com sucesso.');
      assert.strictEqual(await page.locator('#nomeListaPedidoEdit').inputValue(), 'Pedido A após nova tentativa');
      assert.strictEqual(await page.evaluate(() => window.scrollY), 1400);
      assert.strictEqual(test.state.saves.length, 2);
      await clean(test);
    } finally { await test.context.close(); }
  }
  console.log('PASS falha ao atualizar após salvar: conteúdo/posição preservados, sem sucesso indevido, nova tentativa e resposta tardia protegidas.');
}

async function testSaveRefreshSelectionRaces(browser) {
  for (const change of ['list', 'store']) {
    const test = await fixture(browser, 'tenant-save-selection-' + change, { allowSave: true });
    const { page } = test;
    try {
      await click(page, 'a');
      await ready(page, 'a');
      await page.locator('#nomeListaPedidoEdit').fill('Pedido A salvo');
      await plan(page, 'a:saved', { phase: 'body', ignoreAbort: true });
      await page.evaluate(() => { window.__savePending = salvarListaPedidoAtual(); });
      await page.waitForFunction(() => __listHarness.calls.length === 2 && __listHarness.pending().length === 1);
      if (change === 'list') { await click(page, 'b'); await ready(page, 'b'); }
      else {
        await page.locator('#lojaBotoes button').filter({ hasText: 'Loja B' }).click();
        await page.locator('.lista-item').filter({ hasText: 'Pedido C' }).waitFor();
      }
      const status = await page.locator('#statusListaPedido').innerText();
      assert.strictEqual((await calls(page))[1].aborted, true);
      await release(page, 1);
      await page.evaluate(() => window.__savePending);
      assert.strictEqual(await page.evaluate(() => listaPedidoAtual?.id || null), change === 'list' ? 'b' : null);
      assert.strictEqual(await page.locator('#statusListaPedido').innerText(), status, 'A resposta do salvamento anterior não pode alterar o status da nova seleção.');
      assert.strictEqual(await page.locator('#editorListaPedido').evaluate(el => el.inert), false);
      if (change === 'store') assert.strictEqual(await page.locator('input[data-kind="sku"]').count(), 0);
      await clean(test);
    } finally { await test.context.close(); }
  }
  console.log('PASS concorrência ao salvar: outra lista ou loja cancela a atualização anterior sem reabrir conteúdo nem substituir o status atual.');
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    await testSavedFirstAndStore(browser);
    await testSelectionRaces(browser);
    await testInitialFailuresAndRetry(browser);
    await testComplementFailureAndSelection(browser);
    await testEditingPreservesDom(browser);
    await testStructuralEdits(browser);
    await testStoreSwitchCancelsSnapshot(browser);
    await testDragInProgressPreservesDom(browser);
    await testMaterializedLegacyRename(browser);
    await testSavePreservesViewport(browser);
    await testSaveRefreshSelectionRaces(browser);
    await testSaveRefreshFailures(browser);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
