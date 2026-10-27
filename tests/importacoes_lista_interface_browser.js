
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const host = 'http://jk.test/importacoes.html';
const previewDirectory = process.env.JK_LISTA_INTERFACE_PREVIEW_DIR;
const stores = [
  { nome: 'Loja Águas', store_id: 'store-a' },
  { nome: 'Loja Serra', store_id: 'store-b' },
];
const suppliers = [
  { id: 'supplier-a', nome_empresa: 'Fornecedor Ágata' },
  { id: 'supplier-b', nome_empresa: 'Fornecedor Beta' },
];
const nationalizers = [
  { id: 'nationalizer-a', nome_empresa: 'Despacho Íris' },
  { id: 'nationalizer-b', nome_empresa: 'Despacho Beta' },
];

function fixture() {
  const base = {
    status: 'Analisando orçamento', loja: stores[0].nome, store_id: stores[0].store_id,
    fornecedor_id: suppliers[0].id, supplier: suppliers[0].nome_empresa,
    nacionalizador_id: nationalizers[0].id, nacionalizador: nationalizers[0].nome_empresa,
    order_date: '2026-10-05', total_itens: 2, total_quantidade: 40, total_m3: 0.1, total_usd: 200,
    created_at: '2026-10-05T12:00:00Z', updated_at: '2026-10-09T12:00:00Z',
    itens: [{ SKU: 'SYN-001', Quantidade: 40, 'Valor unidade': 5, 'Valor total': 200 }],
  };
  return {
    lists: [
      { ...structuredClone(base), id: 'alpha', nome_lista: 'Pedido Águas', numero_invoice: 'INV-ALPHA' },
      { ...structuredClone(base), id: 'beta', nome_lista: 'Pedido Horizonte', numero_invoice: 'INV-BETA',
        supplier: suppliers[1].nome_empresa, fornecedor_id: suppliers[1].id,
        nacionalizador: nationalizers[1].nome_empresa, nacionalizador_id: nationalizers[1].id },
      { ...structuredClone(base), id: 'gamma', nome_lista: 'Pedido Serra', numero_invoice: 'INV-GAMMA',
        loja: stores[1].nome, store_id: stores[1].store_id },
      { ...structuredClone(base), id: 'excluded', nome_lista: 'Pedido já aprovado', numero_invoice: 'INV-EXCLUDED',
        status: 'Aprovado' },
    ],
    updates: [], deletions: [], detailReads: [], unexpected: [], routeErrors: [], putGates: {},
  };
}

const json = (route, data, status = 200) => route.fulfill({
  status, contentType: 'application/json', body: JSON.stringify(data),
});

async function installRoutes(page, state) {
  await page.route('**/*', async route => {
    try {
      const request = route.request();
      const url = new URL(request.url());
      const pathname = url.pathname;
      const method = request.method();
      if (url.hostname === 'economia.awesomeapi.com.br') {
        return json(route, { USDBRL: { bid: '5.0', create_date: '2026-10-09 08:30:00' } });
      }
      if (url.hostname === 'fonts.googleapis.com' && method === 'GET') {
        return route.fulfill({ status: 200, contentType: 'text/css', body: '' });
      }
      if (url.origin !== 'http://jk.test') {
        state.unexpected.push(method + ' external request');
        return route.abort();
      }
      if (pathname === '/importacoes.html') {
        return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8',
          body: fs.readFileSync(path.join(root, 'static', 'importacoes.html')) });
      }
      if (pathname === '/importacoes_lista.html') {
        return route.fulfill({ status: 200, contentType: 'text/html',
          body: '<!doctype html><html><body>Destino sintético da lista.</body></html>' });
      }
      if (pathname === '/auth.js') {
        return route.fulfill({ status: 200, contentType: 'application/javascript',
          body: 'window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({Authorization:"Bearer synthetic-interface"});' });
      }
      if (pathname === '/cache-persistente.js') return route.fulfill({ status: 200,
        contentType: 'application/javascript', body: fs.readFileSync(path.join(root, 'static', 'cache-persistente.js')) });
      if (/^\/medias_compras\/[a-z0-9_-]+\.(?:js|css)$/i.test(pathname)) {
        return route.fulfill({ status: 200,
          contentType: pathname.endsWith('.css') ? 'text/css' : 'application/javascript',
          body: fs.readFileSync(path.join(root, 'static', pathname.slice(1))) });
      }
      if (pathname === '/ia-sidebar.js' || pathname === '/favicon.ico') {
        return route.fulfill({ status: 200, contentType: 'application/javascript', body: '' });
      }
      if (pathname.startsWith('/api/')) {
        assert.strictEqual(request.headers().authorization, 'Bearer synthetic-interface');
      }
      if (pathname === '/api/lojas' && method === 'GET') return json(route, stores);
      if (pathname === '/api/cadastro/fornecedores' && method === 'GET') return json(route, suppliers);
      if (pathname === '/api/cadastro/nacionalizadores' && method === 'GET') return json(route, nationalizers);
      if (pathname === '/api/medias-compras/listas-pedidos' && method === 'GET') {
        return json(route, { listas: structuredClone(state.lists) });
      }
      const match = pathname.match(/^\/api\/medias-compras\/listas-pedidos\/([^/]+)$/);
      if (match) {
        const list = state.lists.find(row => row.id === decodeURIComponent(match[1]));
        assert(list, 'Requisição deve usar uma lista sintética conhecida.');
        if (method === 'GET') {
          state.detailReads.push(list.id);
          return json(route, { lista: structuredClone(list) });
        }
        if (method === 'PUT') {
          const payload = request.postDataJSON();
          const keys = Object.keys(payload);
          assert.strictEqual(keys.length, 1, 'Edição deve enviar somente um campo.');
          const field = keys[0];
          assert(['nome_lista', 'numero_invoice', 'loja', 'fornecedor_id', 'nacionalizador_id', 'order_date'].includes(field));
          state.updates.push({ id: list.id, payload });
          const pendingGate = state.putGates[field];
          delete state.putGates[field];
          if (pendingGate) await pendingGate;
          Object.assign(list, payload);
          if (field === 'fornecedor_id') {
            const supplier = suppliers.find(row => row.id === payload[field]);
            assert(supplier, 'Fornecedor deve pertencer ao cadastro sintético.');
            list.supplier = supplier.nome_empresa;
          }
          if (field === 'nacionalizador_id') {
            const nationalizer = nationalizers.find(row => row.id === payload[field]);
            assert(nationalizer || !payload[field], 'Nacionalizador deve pertencer ao cadastro sintético.');
            list.nacionalizador = nationalizer?.nome_empresa || '';
          }
          if (field === 'loja') {
            const store = stores.find(row => row.nome === payload[field]);
            assert(store, 'Loja deve pertencer à fixture.');
            list.store_id = store.store_id;
          }
          return json(route, { success: true, lista: structuredClone(list) });
        }
        if (method === 'DELETE') {
          state.deletions.push(list.id);
          return json(route, { success: true });
        }
      }
      state.unexpected.push(method + ' ' + pathname);
      return json(route, { detail: 'Unexpected synthetic request' }, 404);
    } catch (error) {
      state.routeErrors.push(error.message);
      return route.abort();
    }
  });
}

function card(page, id) {
  return page.locator('#listaWrap div.lista-item').filter({
    has: page.locator('[data-invoice-lista-id="' + id + '"]'),
  });
}
async function waitCount(page, count) {
  await page.waitForFunction(expected => document.querySelectorAll('#listaWrap .lista-item').length === expected, count);
}
async function storeFilter(page, name) {
  await page.locator('#lojaFiltroListas button').filter({ hasText: name }).click();
}
async function closed(page, id, selector, text) {
  await page.waitForFunction(({ id, selector, text }) => {
    const row = [...document.querySelectorAll('#listaWrap .lista-item')]
      .find(item => item.querySelector('[data-invoice-lista-id="' + id + '"]'));
    const field = row?.querySelector(selector);
    return field && !field.querySelector('input') && field.textContent.includes(text) &&
      field.getAttribute('aria-busy') !== 'true';
  }, { id, selector, text });
}

async function testSearchAndDensity(page, state) {
  await waitCount(page, 3);
  const before = state.updates.length;
  const filters = await page.locator('#lojaFiltroListas').innerText();
  const boarding = await page.locator('#embarquesWrap').innerText();
  const search = page.locator('#buscarListasImportacoes');
  for (const [term, expected] of [['AGUAS', 2], ['inv-beta', 1], ['agata', 2], ['iris', 2], ['impossivel', 0]]) {
    await search.fill(term);
    await waitCount(page, expected);
    assert.strictEqual(await page.locator('#lojaFiltroListas').innerText(), filters, 'Busca não altera contagens das lojas.');
    assert.strictEqual(await page.locator('#embarquesWrap').innerText(), boarding, 'Busca não altera embarques.');
  }
  await search.fill('inv-gamma');
  await storeFilter(page, stores[0].nome);
  await waitCount(page, 0);
  await storeFilter(page, 'Todas as lojas');
  await waitCount(page, 1);
  await search.fill('');
  await waitCount(page, 3);
  const table = page.locator('#tabelaListasImportacoes');
  const compact = await table.evaluate(element => element.classList.contains('is-compacta'));
  const firstHeight = await card(page, 'alpha').evaluate(element => element.getBoundingClientRect().height);
  await page.locator('#btnDensidadeListas').click();
  const toggled = await table.evaluate(element => element.classList.contains('is-compacta'));
  assert.notStrictEqual(toggled, compact, 'Densidade deve alternar o modo da lista.');
  const secondHeight = await card(page, 'alpha').evaluate(element => element.getBoundingClientRect().height);
  assert.notStrictEqual(secondHeight, firstHeight, 'Alternar densidade deve mudar o espaço das linhas.');
  await page.locator('#btnDensidadeListas').click();
  assert.strictEqual(state.updates.length, before, 'Busca, filtros e densidade não salvam dados.');
  assert.deepStrictEqual(state.deletions, []);
}

async function testSixEditors(page, state) {
  const url = page.url();
  const fields = [
    { selector: '.nome-lista', field: 'nome_lista', value: 'Pedido Renomeado', display: 'Pedido Renomeado' },
    { selector: '[data-invoice-pill]', field: 'numero_invoice', value: 'INV-EDITADA', display: 'INV-EDITADA' },
    { selector: '[data-loja-pill]', field: 'loja', value: stores[1].nome, display: stores[1].nome },
    { selector: '[data-fornecedor-pill]', field: 'fornecedor_id', value: suppliers[1].nome_empresa,
      payload: suppliers[1].id, display: suppliers[1].nome_empresa },
    { selector: '[data-nacionalizador-pill]', field: 'nacionalizador_id', value: nationalizers[1].nome_empresa,
      payload: nationalizers[1].id, display: nationalizers[1].nome_empresa },
    { selector: '[data-order-date-pill]', field: 'order_date', value: '2026-10-12', display: '12/10/2026' },
  ];
  for (const field of fields) {
    const before = state.updates.length;
    let control = card(page, 'alpha').locator(field.selector);
    await control.click();
    assert.strictEqual(await card(page, 'alpha').locator('input').count(), 0, 'Clique simples não edita ' + field.field);
    assert.strictEqual(page.url(), url, 'Clique no campo não abre a lista.');
    await control.dblclick();
    let input = card(page, 'alpha').locator('input');
    await input.waitFor({ state: 'visible' });
    await input.fill(field.value);
    await input.press('Escape');
    await page.waitForFunction(() => !document.querySelector('#listaWrap .lista-item input'));
    assert.strictEqual(state.updates.length, before, 'Escape não salva ' + field.field);
    control = card(page, 'alpha').locator(field.selector);
    await control.dblclick();
    input = card(page, 'alpha').locator('input');
    await input.waitFor({ state: 'visible' });
    if (field.field.endsWith('_id')) {
      await input.waitFor({ state: 'visible' });
      await page.waitForFunction(() => document.querySelector('#listaWrap .lista-item input')?.dataset.cadastroCarregado === '1');
    }
    await input.fill(field.value);
    await input.press('Enter');
    await closed(page, 'alpha', field.selector, field.display);
    assert.strictEqual(state.updates.length, before + 1, 'Enter e blur produzem um PUT para ' + field.field);
    assert.deepStrictEqual(state.updates.at(-1), { id: 'alpha', payload: { [field.field]: field.payload || field.value } });
    assert.strictEqual(page.url(), url, 'Salvar o campo não pode abrir o detalhe.');
  }
  const search = page.locator('#buscarListasImportacoes');
  await search.fill('renomeado');
  await waitCount(page, 1);
  assert.strictEqual(await card(page, 'alpha').locator('.nome-lista').innerText(), 'Pedido Renomeado');
  await search.fill('pedido aguas');
  await waitCount(page, 0);
  await search.fill('');
  await waitCount(page, 3);
  assert.strictEqual(await card(page, 'alpha').locator('.nome-lista').innerText(), 'Pedido Renomeado',
    'Filtrar não pode recuperar o nome antigo do cache.');
  await page.reload();
  await waitCount(page, 3);
  assert.strictEqual(await card(page, 'alpha').locator('.nome-lista').innerText(), 'Pedido Renomeado');
}

async function testSearchDuringSave(page, state) {
  for (const edit of [
    { field: 'nome_lista', selector: '.nome-lista', value: 'Pedido Renomeado Pendente', term: 'pendente' },
    { field: 'numero_invoice', selector: '[data-invoice-pill]', value: 'INV-ASSINCRONA', term: 'assincrona' },
  ]) {
    let release;
    state.putGates[edit.field] = new Promise(resolve => { release = resolve; });
    const before = state.updates.length;
    await card(page, 'alpha').locator(edit.selector).dblclick();
    const input = card(page, 'alpha').locator('input');
    await input.fill(edit.value);
    await input.press('Enter');
    await page.waitForFunction(() => document.querySelector('#listaWrap .lista-item input')?.disabled);
    const currentEditor = await input.elementHandle();
    await page.locator('#buscarListasImportacoes').fill(edit.term);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert(await currentEditor.evaluate(element => element.isConnected),
      'Buscar durante PUT não pode desmontar o editor de ' + edit.field);
    release();
    await waitCount(page, 1);
    await closed(page, 'alpha', edit.selector, edit.value);
    assert.strictEqual(state.updates.length, before + 1, 'Busca durante PUT deve compartilhar a edição pendente.');
    assert.deepStrictEqual(state.updates.at(-1), { id: 'alpha', payload: { [edit.field]: edit.value } });
    await page.locator('#buscarListasImportacoes').fill('');
    await waitCount(page, 3);
  }
}

async function testMenuAndNavigation(page, state) {
  const row = card(page, 'alpha');
  const currentName = state.lists.find(list => list.id === 'alpha').nome_lista;
  const trigger = row.locator('[data-menu-lista]');
  const menu = page.locator('#menuListaImportacoes');
  const before = state.updates.length;
  await trigger.click();
  await menu.waitFor({ state: 'visible' });
  assert.strictEqual(await trigger.getAttribute('aria-expanded'), 'true');
  await page.keyboard.press('Escape');
  await menu.waitFor({ state: 'hidden' });
  assert.strictEqual(await trigger.getAttribute('aria-expanded'), 'false');
  await trigger.click();
  await page.locator('#buscarListasImportacoes').click();
  await menu.waitFor({ state: 'hidden' });
  await trigger.click();
  await menu.locator('[data-menu-editar]').click();
  const input = card(page, 'alpha').locator('.nome-lista-input');
  await input.waitFor({ state: 'visible' });
  await input.fill('Nome cancelado no menu');
  await input.press('Escape');
  assert.strictEqual(state.updates.length, before, 'Editar pelo menu e cancelar não pode salvar.');
  assert.strictEqual(await card(page, 'alpha').locator('.nome-lista').innerText(), currentName);
  await trigger.click();
  const confirmation = page.waitForEvent('dialog');
  const deleteClick = menu.locator('[data-menu-excluir]').click();
  const dialog = await confirmation;
  assert.strictEqual(dialog.type(), 'confirm');
  assert(dialog.message().includes(currentName), 'Excluir deve mostrar o nome atual.');
  await dialog.dismiss();
  await deleteClick;
  assert.deepStrictEqual(state.deletions, [], 'Cancelar confirmação não emite DELETE.');
  await trigger.click();
  await menu.locator('[data-menu-abrir]').click();
  await page.waitForURL('**/importacoes_lista.html?lista_id=alpha');
  await page.goto(host);
  await waitCount(page, 3);
  await card(page, 'beta').locator('[data-abrir-lista]').click();
  await page.waitForURL('**/importacoes_lista.html?lista_id=beta');
  assert.strictEqual(state.updates.length, before, 'Menu e navegação não salvam dados.');
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await context.addInitScript(() => {
      if (location.origin !== 'http://jk.test') return;
      localStorage.setItem('jk_embarques_importacoes', JSON.stringify([
        { id: 'synthetic-boarding', nome: 'Embarque Sintético', lista_ids: ['alpha', 'gamma'] },
      ]));
    });
    const state = fixture();
    const page = await context.newPage();
    page.setDefaultTimeout(8000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await installRoutes(page, state);
    await page.goto(host);
    if (previewDirectory) {
      fs.mkdirSync(previewDirectory, { recursive: true });
      await waitCount(page, 3);
      await page.screenshot({ path: path.join(previewDirectory, 'importacoes-real-desktop.png'), fullPage: true });
      await page.setViewportSize({ width: 390, height: 900 });
      await page.screenshot({ path: path.join(previewDirectory, 'importacoes-real-mobile.png'), fullPage: true });
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
        'Interface deve caber no viewport móvel.');
      await page.setViewportSize({ width: 375, height: 900 });
      await page.screenshot({ path: path.join(previewDirectory, 'importacoes-real-mobile-375.png'), fullPage: true });
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'Interface deve caber em 375 px.');
      await page.setViewportSize({ width: 1440, height: 900 });
    }
    await testSearchAndDensity(page, state);
    console.log('PASS busca: acentos/campos, filtro loja, contagens/embarques, densidade e zero mutações.');
    await testSixEditors(page, state);
    console.log('PASS edições: seis duplos cliques, Escape/Enter, payloads isolados, nome no cache e recarga.');
    await testSearchDuringSave(page, state);
    console.log('PASS busca durante PUT: nome e Invoice preservam editor, cache atualizado e um envio.');
    await testMenuAndNavigation(page, state);
    console.log('PASS menu: abrir/fechar, Escape, clique externo, editar/cancelar, confirmação e navegação.');
    assert.deepStrictEqual(errors, []);
    assert.deepStrictEqual(state.routeErrors, []);
    assert.deepStrictEqual(state.unexpected, [], 'Todos os requests devem ficar na fixture sintética.');
    console.log('Importações interface da lista: OK; nenhuma API real ou aplicação instalada acessada.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
