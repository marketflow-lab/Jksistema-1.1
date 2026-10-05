'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const previewDirectory = process.env.JK_INVOICE_PREVIEW_DIR;
const stores = [
  { nome: 'Loja Demonstração A', store_id: 'store-demo-a' },
  { nome: 'Loja Demonstração B', store_id: 'store-demo-b' },
];
const suppliers = [
  { id: 'supplier-a', nome_empresa: 'Fornecedor Demonstração A' },
  { id: 'supplier-b', nome_empresa: 'Fornecedor Demonstração B' },
];
const nationalizers = [
  { id: 'nationalizer-a', nome_empresa: 'Nacionalizador Demonstração A' },
  { id: 'nationalizer-b', nome_empresa: 'Nacionalizador Demonstração B' },
];
const htmlInvoice = '<img src=x onerror="window.__invoiceXss=1"><script>window.__invoiceXss=2</script>&"';
const longInvoice = 'INV-DEMONSTRACAO-' + '2026-IMPORTACAO-COMPONENTES-'.repeat(10);

function makeList(id, numeroInvoice) {
  const list = {
    id, nome_lista: 'Pedido demonstrativo ' + id,
    loja: stores[0].nome, store_id: stores[0].store_id,
    status: 'Analisando orçamento',
    fornecedor_id: suppliers[0].id, supplier: suppliers[0].nome_empresa,
    nacionalizador_id: nationalizers[0].id, nacionalizador: nationalizers[0].nome_empresa,
    itens: [{ SKU: 'DEMO-001', Quantidade: 2, 'Valor unidade': 10, 'Valor total': 20 }],
    total_itens: 1, total_quantidade: 2, total_m3: 0.4, total_usd: 20,
    created_at: '2026-10-05T12:00:00Z',
  };
  if (numeroInvoice !== undefined) list.numero_invoice = numeroInvoice;
  return list;
}

const state = {
  lists: [
    makeList('invoice-main', 'INV-BASE'),
    makeList('invoice-empty'),
    makeList('invoice-legacy', ''),
    { ...makeList('invoice-alias'), invoice: 'INV-ALIAS' },
    makeList('invoice-html', htmlInvoice),
    makeList('invoice-long', longInvoice),
  ],
  updates: [],
  responses: [],
  detailReads: [],
  commercialInvoices: [],
  plans: {},
  unexpected: [],
  routeErrors: [],
};
const json = (route, body, status = 200) => route.fulfill({
  status, contentType: 'application/json', body: JSON.stringify(body),
});

function deferred() {
  let release;
  const promise = new Promise(resolve => { release = resolve; });
  return { promise, release };
}

async function installRoutes(page) {
  await page.route('**/*', async route => {
    try {
      const request = route.request();
      const url = new URL(request.url());
      const pathname = url.pathname;
      const method = request.method();
      if (url.hostname === 'economia.awesomeapi.com.br') {
        return json(route, { USDBRL: { bid: '5.00', create_date: '2026-10-05 12:00:00' } });
      }
      if (url.hostname === 'fonts.googleapis.com' && url.pathname === '/css2' && method === 'GET') {
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
      if (pathname === '/auth.js') {
        return route.fulfill({ status: 200, contentType: 'application/javascript',
          body: 'window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({Authorization:"Bearer fixture"});' });
      }
      if (/^\/medias_compras\/[a-z0-9_-]+\.js$/i.test(pathname)) {
        return route.fulfill({ status: 200, contentType: 'application/javascript',
          body: fs.readFileSync(path.join(root, 'static', pathname.slice(1))) });
      }
      if (pathname === '/ia-sidebar.js' || pathname === '/favicon.ico') {
        return route.fulfill({ status: 200, contentType: 'application/javascript', body: '' });
      }
      if (pathname.startsWith('/api/')) {
        assert.strictEqual(request.headers().authorization, 'Bearer fixture',
          'Toda chamada da fixture deve usar a autenticação simulada');
      }
      if (pathname === '/api/lojas') return json(route, stores);
      if (pathname === '/api/cadastro/fornecedores') return json(route, suppliers);
      if (pathname === '/api/cadastro/nacionalizadores') return json(route, nationalizers);
      if (pathname === '/api/medias-compras/listas-pedidos' && method === 'GET') {
        return json(route, { listas: structuredClone(state.lists) });
      }
      const documentMatch = pathname.match(/^\/api\/medias-compras\/listas-pedidos\/([^/]+)\/commercial-invoice$/);
      if (documentMatch && method === 'GET') {
        const list = state.lists.find(item => item.id === decodeURIComponent(documentMatch[1]));
        assert(list, 'Documento deve consultar somente listas da fixture');
        state.commercialInvoices.push({ id: list.id, numero_invoice: list.numero_invoice });
        return route.fulfill({ status: 200,
          contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          headers: { 'Content-Disposition': 'attachment; filename="commercial_invoice_fixture.xlsx"' },
          body: 'Documento simulado: ' + String(list.numero_invoice || '') });
      }
      const match = pathname.match(/^\/api\/medias-compras\/listas-pedidos\/([^/]+)$/);
      if (match) {
        const list = state.lists.find(item => item.id === decodeURIComponent(match[1]));
        assert(list, 'Requisição deve consultar somente listas da fixture');
        if (method === 'GET') {
          state.detailReads.push({ id: list.id, numero_invoice: list.numero_invoice });
          return json(route, { lista: structuredClone(list) });
        }
        if (method === 'PUT') {
          const payload = request.postDataJSON();
          const keys = Object.keys(payload);
          assert.strictEqual(keys.length, 1, 'PUT deve conter somente o campo editado');
          const field = keys[0];
          assert(['numero_invoice', 'fornecedor_id', 'nacionalizador_id', 'loja'].includes(field),
            'A fixture aceita apenas os quatro controles testados');
          const original = structuredClone(list);
          const plan = state.plans[field] || {};
          delete state.plans[field];
          state.updates.push({ id: list.id, payload });
          if (plan.gate) await plan.gate;
          if (plan.fail) return json(route, { detail: 'Falha simulada ao salvar Invoice' }, 503);
          Object.assign(list, payload);
          if (field === 'numero_invoice') delete list.invoice;
          if (field === 'fornecedor_id') list.supplier = suppliers.find(item => item.id === payload[field]).nome_empresa;
          if (field === 'nacionalizador_id') list.nacionalizador = nationalizers.find(item => item.id === payload[field]).nome_empresa;
          if (field === 'loja') list.store_id = stores.find(item => item.nome === payload[field]).store_id;
          // A complete server response is intentionally a snapshot from when the PUT started.
          // Later responses must merge only their field, including when other controls saved meanwhile.
          const response = { ...original, ...payload };
          if (field === 'numero_invoice') delete response.invoice;
          if (field === 'fornecedor_id') response.supplier = list.supplier;
          if (field === 'nacionalizador_id') response.nacionalizador = list.nacionalizador;
          if (field === 'loja') response.store_id = list.store_id;
          state.responses.push({ field, lista: structuredClone(response) });
          return json(route, { success: true, lista: response });
        }
      }
      state.unexpected.push(method + ' ' + pathname);
      return json(route, { detail: 'Unexpected fixture request' }, 404);
    } catch (error) {
      state.routeErrors.push(error.message);
      await route.abort().catch(() => {});
    }
  });
}

function invoicePill(page, id, container = '#listaWrap') {
  return page.locator(container + ' [data-invoice-pill][data-invoice-lista-id="' + id + '"]');
}

function card(page, id, container = '#listaWrap') {
  const invoice = page.locator('[data-invoice-pill][data-invoice-lista-id="' + id + '"]');
  return page.locator(container + ' .lista-item').filter({ has: invoice });
}

async function closedInvoice(page, id, expected, container = '#listaWrap') {
  const selector = container + ' [data-invoice-pill][data-invoice-lista-id="' + id + '"]';
  await page.waitForFunction(({ selector, expected }) => {
    const pill = document.querySelector(selector);
    return pill && !pill.querySelector('input') && pill.querySelector('strong')?.textContent === (expected || '-') &&
      pill.getAttribute('aria-busy') !== 'true';
  }, { selector, expected });
  return invoicePill(page, id, container);
}

async function editInvoice(page, id, container = '#listaWrap') {
  await invoicePill(page, id, container).dblclick();
  const input = invoicePill(page, id, container).locator('input');
  await input.waitFor({ state: 'visible' });
  assert.strictEqual(await input.isDisabled(), false);
  assert.strictEqual(await input.evaluate(element => document.activeElement === element), true);
  assert((await input.getAttribute('aria-label')).includes('N° Invoice'));
  return input;
}

async function saveInvoice(page, id, value, container = '#listaWrap', trigger = 'Enter') {
  const input = await editInvoice(page, id, container);
  await input.fill(value);
  if (trigger === 'blur') await page.locator('#btnAtualizar').focus();
  else await input.press(trigger);
  return closedInvoice(page, id, value.trim(), container);
}

async function editOtherField(page, id, field, value) {
  const pill = card(page, id).locator('[data-' + field + '-pill]');
  await pill.dblclick();
  const input = pill.locator('input');
  await input.waitFor({ state: 'visible' });
  if (field !== 'loja') {
    await input.evaluate(async element => {
      while (!element.list || element.list.options.length < 2) {
        await new Promise(resolve => requestAnimationFrame(resolve));
      }
    });
  }
  await input.fill(value);
  await input.press('Enter');
  return pill;
}

async function closedOtherField(page, id, field, value) {
  await page.waitForFunction(({ id, field, value }) => {
    const invoice = document.querySelector('#listaWrap [data-invoice-lista-id="' + id + '"]');
    const pill = invoice?.closest('.lista-item')?.querySelector('[data-' + field + '-pill]');
    return pill && !pill.querySelector('input') && pill.textContent.includes(value);
  }, { id, field, value });
}

async function openBoarding(page) {
  await page.locator('#embarquesWrap .embarque-item').filter({ hasText: 'Embarque demonstrativo' }).click();
  await page.locator('#embarqueDetalheView').waitFor({ state: 'visible' });
}

function nextPut(page, field) {
  return page.waitForRequest(request => request.method() === 'PUT' &&
    new URL(request.url()).pathname.includes('/listas-pedidos/') &&
    Object.hasOwn(request.postDataJSON(), field));
}

async function assertSavedFields(page, invoice, expected) {
  await closedInvoice(page, 'invoice-main', invoice);
  for (const [field, value] of Object.entries(expected)) await closedOtherField(page, 'invoice-main', field, value);
  const cached = await page.evaluate(() => {
    const list = todasListasCache.find(item => item.id === 'invoice-main');
    return { numero_invoice: list.numero_invoice, fornecedor_id: list.fornecedor_id, supplier: list.supplier,
      nacionalizador_id: list.nacionalizador_id, nacionalizador: list.nacionalizador, loja: list.loja, store_id: list.store_id };
  });
  assert.strictEqual(cached.numero_invoice, invoice);
  if (expected.fornecedor) assert.strictEqual(cached.supplier, expected.fornecedor);
  if (expected.nacionalizador) assert.strictEqual(cached.nacionalizador, expected.nacionalizador);
  if (expected.loja) assert.strictEqual(cached.loja, expected.loja);
  return cached;
}

async function testEditorAndPersistence(page) {
  const initialURL = page.url();
  const before = state.updates.length;
  const pill = await closedInvoice(page, 'invoice-main', 'INV-BASE');
  await pill.click();
  assert.strictEqual(await pill.locator('input').count(), 0, 'Clique simples não deve editar nem abrir o detalhe');
  let input = await editInvoice(page, 'invoice-main');
  assert.strictEqual(await input.inputValue(), 'INV-BASE');
  await input.fill('INV-CANCELADA');
  await input.press('Escape');
  await closedInvoice(page, 'invoice-main', 'INV-BASE');
  assert.strictEqual(state.updates.length, before, 'Escape não pode gravar nem disparar blur com valor cancelado');
  assert.strictEqual(page.url(), initialURL, 'Duplo clique e teclado no Invoice devem manter a tela da lista');
  input = await editInvoice(page, 'invoice-main');
  await input.fill('   INV-ENTER   ');
  await input.press('Enter');
  const saved = await closedInvoice(page, 'invoice-main', 'INV-ENTER');
  assert.strictEqual(await saved.evaluate(element => document.activeElement === element), true, 'Enter deve devolver foco ao pill');
  assert.deepStrictEqual(state.updates.at(-1), { id: 'invoice-main', payload: { numero_invoice: 'INV-ENTER' } });
  const unchanged = state.updates.length;
  input = await editInvoice(page, 'invoice-main');
  await input.press('Enter');
  await closedInvoice(page, 'invoice-main', 'INV-ENTER');
  assert.strictEqual(state.updates.length, unchanged, 'Valor inalterado não precisa PUT');
  await saveInvoice(page, 'invoice-main', 'INV-BLUR', '#listaWrap', 'blur');
  assert.deepStrictEqual(state.updates.at(-1).payload, { numero_invoice: 'INV-BLUR' });
  await page.reload();
  await closedInvoice(page, 'invoice-main', 'INV-BLUR');
}

async function testEmptyLegacyAndHTML(page) {
  await closedInvoice(page, 'invoice-empty', '');
  await closedInvoice(page, 'invoice-legacy', 'INV-LOCAL');
  await closedInvoice(page, 'invoice-alias', 'INV-ALIAS');
  await saveInvoice(page, 'invoice-alias', '');
  await page.reload();
  await closedInvoice(page, 'invoice-alias', '');
  assert.strictEqual(Object.hasOwn(state.lists.find(item => item.id === 'invoice-alias'), 'invoice'), false,
    'Apagar Invoice legado deve persistir vazio sem ressurgir pelo alias');
  const input = await editInvoice(page, 'invoice-legacy');
  assert.strictEqual(await input.inputValue(), 'INV-LOCAL', 'Editor deve carregar Invoice histórico local');
  await input.fill('INV-LEGADO-SALVO');
  await input.press('Enter');
  await closedInvoice(page, 'invoice-legacy', 'INV-LEGADO-SALVO');
  const local = await page.evaluate(() => JSON.parse(localStorage.getItem('importacoes_lista_resumo_editavel')));
  assert.strictEqual(Object.hasOwn(local['invoice-legacy'], 'numero_invoice'), false,
    'Salvar deve remover somente a cópia local obsoleta do Invoice');
  assert.strictEqual(local['invoice-legacy'].observacao, 'Manter campo demonstrativo');
  assert.strictEqual(local['other-list'].numero_invoice, 'OUTRA-LISTA');
  await saveInvoice(page, 'invoice-legacy', '   ');
  await saveInvoice(page, 'invoice-empty', 'INV-ANTES-VAZIA');
  await page.reload();
  await closedInvoice(page, 'invoice-legacy', '');
  await closedInvoice(page, 'invoice-empty', 'INV-ANTES-VAZIA');
  const html = await closedInvoice(page, 'invoice-html', htmlInvoice);
  assert.strictEqual(await html.locator('img, script').count(), 0, 'Invoice retornado pelo backend deve ser texto escapado');
  assert((await html.getAttribute('title')).includes(htmlInvoice));
  await saveInvoice(page, 'invoice-empty', htmlInvoice);
  assert.strictEqual(await invoicePill(page, 'invoice-empty').locator('img, script').count(), 0,
    'Invoice editado deve continuar como texto, sem elementos HTML');
  await page.reload();
  await closedInvoice(page, 'invoice-empty', htmlInvoice);
  assert.strictEqual(await page.evaluate(() => window.__invoiceXss), undefined, 'HTML do Invoice não pode executar');
}

async function testErrorAndRetry(page) {
  const persisted = state.lists.find(item => item.id === 'invoice-main').numero_invoice;
  state.plans.numero_invoice = { fail: true };
  const input = await editInvoice(page, 'invoice-main');
  await input.fill('INV-FALHA');
  await input.press('Enter');
  await page.locator('#status').filter({ hasText: 'Falha simulada ao salvar Invoice' }).waitFor();
  assert.strictEqual(await input.inputValue(), 'INV-FALHA');
  assert.strictEqual(await input.isDisabled(), false, 'Falha deve permitir tentar novamente');
  assert.strictEqual(await input.getAttribute('aria-invalid'), 'true');
  assert.strictEqual(await input.evaluate(element => document.activeElement === element), true);
  assert.strictEqual(state.lists.find(item => item.id === 'invoice-main').numero_invoice, persisted);
  await input.fill('INV-RETRY');
  assert.strictEqual(await input.getAttribute('aria-invalid'), null, 'Digitar novamente deve retirar o erro anterior');
  await input.press('Enter');
  await closedInvoice(page, 'invoice-main', 'INV-RETRY');
  assert.deepStrictEqual(state.updates.at(-1).payload, { numero_invoice: 'INV-RETRY' });
}

async function testSlowPUTAndCopies(page) {
  const gate = deferred();
  state.plans.numero_invoice = { gate: gate.promise };
  const before = state.updates.length;
  const input = await editInvoice(page, 'invoice-main');
  await input.fill('INV-LENTA');
  const started = nextPut(page, 'numero_invoice');
  await input.press('Enter');
  await started;
  assert.strictEqual(await input.isDisabled(), true, 'Editor deve bloquear nova gravação durante PUT');
  await input.dispatchEvent('keydown', { key: 'Enter', bubbles: true });
  await input.dispatchEvent('blur');
  await page.locator('#lojaFiltroListas button').filter({ hasText: 'Todas as lojas' }).click();
  assert.strictEqual(await invoicePill(page, 'invoice-main').getAttribute('aria-busy'), 'true',
    'Cartão recriado deve refletir o PUT pendente');
  await invoicePill(page, 'invoice-main').dispatchEvent('dblclick', { bubbles: true });
  assert.strictEqual(await invoicePill(page, 'invoice-main').locator('input').count(), 0);
  await openBoarding(page);
  const other = invoicePill(page, 'invoice-main', '#embarqueDetalheListas');
  assert.strictEqual(await other.getAttribute('aria-busy'), 'true', 'Outro cartão da mesma lista deve respeitar gravação global');
  await other.dblclick();
  assert.strictEqual(await other.locator('input').count(), 0, 'Embarque não pode iniciar PUT concorrente da mesma lista');
  assert.strictEqual(state.updates.length, before + 1, 'Enter + blur + cópias recriadas devem gerar exatamente um PUT');
  const supplierPill = card(page, 'invoice-main', '#embarqueDetalheListas').locator('[data-fornecedor-pill]');
  await supplierPill.dblclick();
  const supplierInput = supplierPill.locator('input');
  await supplierInput.waitFor({ state: 'visible' });
  await page.waitForFunction(() => {
    const input = document.querySelector('#embarqueDetalheListas [data-fornecedor-pill] input');
    return input && input.list && input.list.options.length >= 2;
  });
  await supplierInput.fill(suppliers[1].nome_empresa);
  const otherEditor = await supplierInput.elementHandle();
  gate.release();
  await closedInvoice(page, 'invoice-main', 'INV-LENTA', '#embarqueDetalheListas');
  assert.strictEqual(await otherEditor.evaluate(element => element.isConnected), true,
    'Atualização de Invoice e resumos deve preservar outro editor ativo');
  assert.strictEqual(await supplierInput.inputValue(), suppliers[1].nome_empresa,
    'Resposta do Invoice não pode apagar o texto ainda não salvo em outro campo');
  assert.strictEqual(await supplierInput.evaluate(element => document.activeElement === element), true);
  assert.strictEqual(state.updates.length, before + 1,
    'Atualizar os resumos do embarque não deve causar blur e PUT de outro editor');
  await supplierInput.press('Escape');
  await closedInvoice(page, 'invoice-main', 'INV-LENTA');
  assert.strictEqual(await page.locator('[data-invoice-lista-id="invoice-main"] strong').count(), 2);
  assert.deepStrictEqual(await page.locator('[data-invoice-lista-id="invoice-main"] strong').allTextContents(),
    ['INV-LENTA', 'INV-LENTA'], 'Sucesso deve atualizar ambas as cópias da mesma lista');
  const currentURL = page.url();
  await saveInvoice(page, 'invoice-main', 'INV-EMBARQUE', '#embarqueDetalheListas');
  await closedInvoice(page, 'invoice-main', 'INV-EMBARQUE');
  await page.waitForFunction(() =>
    document.getElementById('embarqueInvoiceLine').textContent.includes('INV-EMBARQUE') &&
    document.getElementById('embarqueDetalheCards').textContent.includes('INV-EMBARQUE'));
  for (const selector of ['#embarqueInvoiceLine', '#embarqueDetalheCards']) {
    assert(!(await page.locator(selector).textContent()).includes('INV-LENTA'),
      'Cabeçalho e resumo do embarque devem substituir o número anterior após salvar pelo cartão detalhado');
  }
  assert.strictEqual(page.url(), currentURL, 'Edição no embarque não deve abrir o detalhe da lista');
  await page.locator('#btnVoltarEmbarque').click();
}

async function testOutOfOrderResponses(page) {
  const gate = deferred();
  state.plans.numero_invoice = { gate: gate.promise };
  let input = await editInvoice(page, 'invoice-main');
  await input.fill('INV-CONCORRENTE');
  const invoiceStarted = nextPut(page, 'numero_invoice');
  await input.press('Enter');
  await invoiceStarted;
  await editOtherField(page, 'invoice-main', 'fornecedor', suppliers[1].nome_empresa);
  await closedOtherField(page, 'invoice-main', 'fornecedor', suppliers[1].nome_empresa);
  await editOtherField(page, 'invoice-main', 'nacionalizador', nationalizers[1].nome_empresa);
  await closedOtherField(page, 'invoice-main', 'nacionalizador', nationalizers[1].nome_empresa);
  await editOtherField(page, 'invoice-main', 'loja', stores[1].nome);
  await closedOtherField(page, 'invoice-main', 'loja', stores[1].nome);
  gate.release();
  let cached = await assertSavedFields(page, 'INV-CONCORRENTE', {
    fornecedor: suppliers[1].nome_empresa, nacionalizador: nationalizers[1].nome_empresa, loja: stores[1].nome,
  });
  const oldInvoiceResponse = state.responses.filter(item => item.field === 'numero_invoice').at(-1).lista;
  assert.strictEqual(oldInvoiceResponse.supplier, suppliers[0].nome_empresa, 'Fixture deve comprovar resposta completa antiga');
  assert.strictEqual(oldInvoiceResponse.nacionalizador, nationalizers[0].nome_empresa);
  assert.strictEqual(oldInvoiceResponse.loja, stores[0].nome);
  assert.strictEqual(cached.fornecedor_id, suppliers[1].id);
  assert.strictEqual(cached.nacionalizador_id, nationalizers[1].id);
  assert.strictEqual(cached.store_id, stores[1].store_id);

  const storeGate = deferred();
  state.plans.loja = { gate: storeGate.promise };
  const storeStarted = nextPut(page, 'loja');
  await editOtherField(page, 'invoice-main', 'loja', stores[0].nome);
  await storeStarted;
  await saveInvoice(page, 'invoice-main', 'INV-APOS-LOJA');
  storeGate.release();
  cached = await assertSavedFields(page, 'INV-APOS-LOJA', {
    fornecedor: suppliers[1].nome_empresa, nacionalizador: nationalizers[1].nome_empresa, loja: stores[0].nome,
  });
  assert.strictEqual(state.responses.filter(item => item.field === 'loja').at(-1).lista.numero_invoice, 'INV-CONCORRENTE',
    'Resposta de Loja deve chegar com o Invoice anterior para exercitar a preservação');
  assert.strictEqual(cached.store_id, stores[0].store_id);
  await page.locator('#lojaFiltroListas button').filter({ hasText: 'Todas as lojas' }).click();
  await closedInvoice(page, 'invoice-main', 'INV-APOS-LOJA');
  await page.reload();
  await assertSavedFields(page, 'INV-APOS-LOJA', {
    fornecedor: suppliers[1].nome_empresa, nacionalizador: nationalizers[1].nome_empresa, loja: stores[0].nome,
  });
}

async function testCommercialInvoiceWaitsForSave(page) {
  const beforeReads = state.detailReads.length;
  const beforeDocuments = state.commercialInvoices.length;
  const beforeUpdates = state.updates.length;
  const gate = deferred();
  state.plans.numero_invoice = { gate: gate.promise };
  const input = await editInvoice(page, 'invoice-main');
  await input.fill('INV-DOCUMENTO-ATUAL');
  const button = card(page, 'invoice-main').locator('[data-commercial-invoice]');
  const started = nextPut(page, 'numero_invoice');
  // Click with the input still focused: blur must save before the document GETs.
  await button.click();
  await started;
  assert.strictEqual(await input.isDisabled(), true);
  assert.strictEqual(await button.isDisabled(), true);
  assert.strictEqual(await button.textContent(), 'Verificando...');
  // Keep the PUT gated while browser requests have time to reach the mocked server.
  await page.waitForTimeout(100);
  assert.strictEqual(state.detailReads.length, beforeReads,
    'Gerar documento deve aguardar o PUT antes de consultar a lista');
  assert.strictEqual(state.commercialInvoices.length, beforeDocuments,
    'Documento não pode ser gerado com o número anterior enquanto o PUT está pendente');
  assert.strictEqual(state.updates.length, beforeUpdates + 1, 'Clique deve disparar apenas o PUT do blur');
  const downloadReady = page.waitForEvent('download');
  gate.release();
  const download = await downloadReady;
  assert.strictEqual(await download.failure(), null, 'Documento simulado deve concluir o download');
  assert.strictEqual(download.suggestedFilename(), 'commercial_invoice_fixture.xlsx');
  await closedInvoice(page, 'invoice-main', 'INV-DOCUMENTO-ATUAL');
  assert.deepStrictEqual(state.detailReads.slice(beforeReads),
    [{ id: 'invoice-main', numero_invoice: 'INV-DOCUMENTO-ATUAL' }],
    'Consulta para geração deve enxergar somente o número já salvo');
  assert.deepStrictEqual(state.commercialInvoices.slice(beforeDocuments),
    [{ id: 'invoice-main', numero_invoice: 'INV-DOCUMENTO-ATUAL' }],
    'Documento deve ser gerado uma vez com a Invoice salva pelo blur');
  assert.strictEqual(await button.isDisabled(), false);
  assert.strictEqual(await button.textContent(), 'Commercial Invoice');
}

async function testCommercialInvoiceStopsAfterSaveFailure(page) {
  const persisted = state.lists.find(item => item.id === 'invoice-main').numero_invoice;
  const beforeReads = state.detailReads.length;
  const beforeDocuments = state.commercialInvoices.length;
  const beforeUpdates = state.updates.length;
  const gate = deferred();
  state.plans.numero_invoice = { gate: gate.promise, fail: true };
  const input = await editInvoice(page, 'invoice-main');
  await input.fill('INV-DOCUMENTO-FALHA');
  const button = card(page, 'invoice-main').locator('[data-commercial-invoice]');
  const started = nextPut(page, 'numero_invoice');
  await button.click();
  await started;
  assert.strictEqual(await button.isDisabled(), true);
  await page.waitForTimeout(100);
  assert.strictEqual(state.detailReads.length, beforeReads);
  assert.strictEqual(state.commercialInvoices.length, beforeDocuments);
  const alertReady = page.waitForEvent('dialog');
  gate.release();
  const alert = await alertReady;
  assert.strictEqual(alert.type(), 'alert');
  assert.strictEqual(alert.message(), 'Salve o número da Invoice antes de gerar o Commercial Invoice.');
  await alert.dismiss();
  await page.waitForFunction(() => {
    const pill = document.querySelector('#listaWrap [data-invoice-lista-id="invoice-main"]');
    return !pill?.closest('.lista-item')?.querySelector('[data-commercial-invoice]')?.disabled;
  });
  assert.strictEqual(state.detailReads.length, beforeReads,
    'Falha de gravação deve abortar antes de consultar dados para o documento');
  assert.strictEqual(state.commercialInvoices.length, beforeDocuments,
    'Falha de gravação não pode gerar documento com a Invoice anterior');
  assert.strictEqual(state.updates.length, beforeUpdates + 1, 'Falha não deve causar repetição automática do PUT');
  assert.strictEqual(state.lists.find(item => item.id === 'invoice-main').numero_invoice, persisted);
  assert.strictEqual(await input.inputValue(), 'INV-DOCUMENTO-FALHA');
  assert.strictEqual(await input.getAttribute('aria-invalid'), 'true');
  assert.strictEqual(await input.isDisabled(), false, 'Tentativa deve permanecer editável após a falha');
  assert.strictEqual(await button.textContent(), 'Commercial Invoice');
  await input.press('Escape');
  await closedInvoice(page, 'invoice-main', persisted);
}

async function assertLayout(pill, width, editing) {
  const layout = await pill.evaluate(element => {
    const card = element.closest('.lista-item');
    const rect = element.getBoundingClientRect();
    const cardRect = card.getBoundingClientRect();
    const actionRect = card.querySelector('.lista-card-acoes')?.getBoundingClientRect();
    const input = element.querySelector('input');
    const inputRect = input?.getBoundingClientRect();
    return {
      cardWidth: card.clientWidth,
      cardScrollWidth: card.scrollWidth,
      fits: card.scrollWidth <= card.clientWidth + 1,
      within: rect.left >= cardRect.left && rect.right <= cardRect.right + 1,
      overlaps: actionRect && rect.left < actionRect.right && rect.right > actionRect.left &&
        rect.top < actionRect.bottom && rect.bottom > actionRect.top,
      actionsVisible: !actionRect || actionRect.width > 0,
      inputFits: !inputRect || inputRect.right <= rect.right + 1,
      inputWidth: inputRect?.width,
    };
  });
  assert(layout.fits && layout.within && !layout.overlaps && layout.actionsVisible,
    'Invoice ' + (editing ? 'em edição' : 'com texto longo') + ' deve caber sem ocultar ações em ' + width + 'px: ' + JSON.stringify(layout));
  if (editing) assert(layout.inputFits && layout.inputWidth >= 55, 'Editor de Invoice deve permanecer utilizável em ' + width + 'px: ' + JSON.stringify(layout));
}

async function testResponsiveLayout(page) {
  if (previewDirectory) {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.evaluate(() => {
      const banner = document.createElement('p');
      banner.id = 'fixtureNotice';
      banner.textContent = 'DADOS SIMULADOS · Teste da edição de N° Invoice';
      banner.style.cssText = 'padding:12px;color:#ffd89a;border:1px solid #ffd89a;border-radius:8px';
      document.querySelector('.container').prepend(banner);
    });
    const previewInput = await editInvoice(page, 'invoice-main');
    await previewInput.fill('JK49-20261005');
    await page.locator('.container').screenshot({ path: path.join(previewDirectory, 'invoice-campo-em-edicao.png') });
    await card(page, 'invoice-main').screenshot({ path: path.join(previewDirectory, 'invoice-cartao-em-edicao.png') });
    await previewInput.press('Escape');
  }
  for (const width of [1440, 1033, 880, 390]) {
    await page.setViewportSize({ width, height: 900 });
    const long = await closedInvoice(page, 'invoice-long', longInvoice);
    assert((await long.getAttribute('title')).includes(longInvoice), 'Texto completo deve continuar disponível no título');
    await assertLayout(long, width, false);
    const input = await editInvoice(page, 'invoice-long');
    assert.strictEqual(await input.inputValue(), longInvoice, 'Editor não pode truncar Invoice longo');
    await assertLayout(invoicePill(page, 'invoice-long'), width, true);
    await input.press('Escape');
    await closedInvoice(page, 'invoice-long', longInvoice);
    if (previewDirectory && (width === 1440 || width === 390)) {
      await page.evaluate(() => {
        if (document.getElementById('fixtureNotice')) return;
        const banner = document.createElement('p');
        banner.id = 'fixtureNotice';
        banner.textContent = 'DADOS SIMULADOS · Teste da edição de N° Invoice';
        banner.style.cssText = 'padding:12px;color:#ffd89a;border:1px solid #ffd89a;border-radius:8px';
        document.querySelector('.container').prepend(banner);
      });
      const previewInput = await editInvoice(page, 'invoice-main');
      await page.locator('.container').screenshot({ path: path.join(previewDirectory,
        width === 390 ? 'invoice-edicao-mobile.png' : 'invoice-edicao-desktop.png') });
      if (width === 1440) await card(page, 'invoice-main').screenshot({
        path: path.join(previewDirectory, 'invoice-edicao-cartao-desktop.png') });
      await previewInput.press('Escape');
    }
  }
  await page.setViewportSize({ width: 390, height: 900 });
  await openBoarding(page);
  const boardingLong = await closedInvoice(page, 'invoice-long', longInvoice, '#embarqueDetalheListas');
  await assertLayout(boardingLong, 390, false);
  const input = await editInvoice(page, 'invoice-long', '#embarqueDetalheListas');
  await assertLayout(boardingLong, 390, true);
  await input.press('Escape');
  await page.locator('#btnVoltarEmbarque').click();
}

(async () => {
  if (previewDirectory) fs.mkdirSync(previewDirectory, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await context.addInitScript(() => {
      if (location.origin !== 'http://jk.test') return;
      if (!localStorage.getItem('importacoes_lista_resumo_editavel')) {
        localStorage.setItem('importacoes_lista_resumo_editavel', JSON.stringify({
          'invoice-legacy': { numero_invoice: 'INV-LOCAL', observacao: 'Manter campo demonstrativo' },
          'other-list': { numero_invoice: 'OUTRA-LISTA' },
        }));
      }
      if (!localStorage.getItem('jk_embarques_importacoes')) {
        localStorage.setItem('jk_embarques_importacoes', JSON.stringify([{
          id: 'emb-demo', nome: 'Embarque demonstrativo', lista_ids: ['invoice-main', 'invoice-long'],
        }]));
      }
    });
    const page = await context.newPage();
    page.setDefaultTimeout(8000);
    page.setDefaultNavigationTimeout(15000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await installRoutes(page);
    await page.goto('http://jk.test/importacoes.html');
    const scenarios = [
      ['duplo clique, Enter, blur, Escape e persistência', testEditorAndPersistence],
      ['vazio, legado local e HTML escapado', testEmptyLegacyAndHTML],
      ['erro mantém editor e retry', testErrorAndRetry],
      ['PUT lento, deduplicação e cópias no embarque', testSlowPUTAndCopies],
      ['respostas completas antigas e concorrência entre campos', testOutOfOrderResponses],
      ['Commercial Invoice aguarda gravação por blur', testCommercialInvoiceWaitsForSave],
      ['Commercial Invoice aborta após falha de gravação', testCommercialInvoiceStopsAfterSaveFailure],
      ['Invoice longo e layout desktop/móvel', testResponsiveLayout],
    ];
    for (const [name, test] of scenarios) {
      await test(page);
      console.log('N° Invoice: ' + name + ': OK');
    }
    assert.deepStrictEqual(errors, [], 'Página real não deve gerar erros JavaScript');
    assert.deepStrictEqual(state.routeErrors, [], 'Todos os contratos da fixture devem ser válidos');
    assert.deepStrictEqual(state.unexpected, [], 'Nenhuma chamada pode escapar do mock ou abrir detalhe involuntariamente');
    for (const update of state.updates.filter(item => Object.hasOwn(item.payload, 'numero_invoice'))) {
      assert.deepStrictEqual(Object.keys(update.payload), ['numero_invoice']);
    }
    console.log('Importações N° Invoice: ' + scenarios.length + ' cenários de navegador aprovados; todas as requisições simuladas.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
