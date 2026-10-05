'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const previewDirectory = process.env.JK_FORNECEDOR_PREVIEW_DIR;
const suppliers = [
  { id: 'supplier-a', nome_empresa: 'Fornecedor A' },
  { id: 'supplier-b', nome_empresa: 'Fornecedor B' },
];
const nationalizers = [
  { id: 'nationalizer-a', nome_empresa: 'Nacionalizador A' },
  { id: 'nationalizer-b', nome_empresa: 'Nacionalizador B' },
];
const state = {
  suppliers,
  nationalizers,
  supplierError: false,
  nationalizerError: false,
  supplierGate: null,
  nationalizerGate: null,
  supplierReads: 0,
  nationalizerReads: 0,
  updateError: false,
  updateGate: null,
  updateGates: {},
  updateSnapshots: false,
  fallback: false,
  creates: [],
  imports: [],
  updates: [],
  lists: [{
    id: 'legacy', nome_lista: 'Lista antiga', loja: 'JK Pecas', store_id: 'store-jk',
    status: 'Lista gerada', created_at: '2026-10-02T12:00:00Z',
    itens: [{ SKU: '001', Quantidade: 2, 'Valor unidade': 10, 'Valor total': 20 }],
  }],
};
const json = (route, body, status = 200) => route.fulfill({
  status, contentType: 'application/json', body: JSON.stringify(body),
});

function addList(payload) {
  const supplier = suppliers.find(item => item.id === payload.fornecedor_id);
  assert(supplier, 'Toda lista criada deve enviar um fornecedor cadastrado');
  const list = {
    id: 'created-' + state.lists.length, nome_lista: payload.nome_lista || 'Pedido importado',
    loja: 'JK Pecas', store_id: 'store-jk', status: 'Analisando orçamento',
    fornecedor_id: supplier.id, supplier: supplier.nome_empresa,
    created_at: '2026-10-02T13:00:00Z', itens: [], total_itens: 0,
  };
  state.lists.push(list);
  return list;
}

async function installRoutes(page) {
  await page.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const pathname = url.pathname;
    if (pathname.endsWith('.html')) {
      return route.fulfill({
        status: 200, contentType: 'text/html; charset=utf-8',
        body: fs.readFileSync(path.join(root, 'static', pathname.slice(1))),
      });
    }
    if (pathname.startsWith('/medias_compras/')) {
      return route.fulfill({
        status: 200,
        contentType: pathname.endsWith('.css') ? 'text/css' : 'application/javascript',
        body: fs.readFileSync(path.join(root, 'static', pathname.slice(1))),
      });
    }
    if (pathname === '/auth.js') {
      return route.fulfill({ status: 200, contentType: 'application/javascript',
        body: 'window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({Authorization:"Bearer test"});' });
    }
    if (pathname === '/api/cadastro/fornecedores') {
      assert.strictEqual(request.headers().authorization, 'Bearer test');
      state.supplierReads += 1;
      const snapshot = structuredClone(state.suppliers);
      if (state.supplierGate) await state.supplierGate;
      return state.supplierError
        ? json(route, { detail: 'Cadastro indisponível' }, 500)
        : json(route, snapshot);
    }
    if (pathname === '/api/cadastro/nacionalizadores') {
      assert.strictEqual(request.headers().authorization, 'Bearer test');
      state.nationalizerReads += 1;
      const snapshot = structuredClone(state.nationalizers);
      if (state.nationalizerGate) await state.nationalizerGate;
      return state.nationalizerError
        ? json(route, { detail: 'Cadastro de nacionalizadores indisponível' }, 500)
        : json(route, snapshot);
    }
    if (pathname === '/api/lojas') return json(route, [{ nome: 'JK Pecas', store_id: 'store-jk' }]);
    if (pathname === '/api/medias-compras/preferencias-skus-ocultos') return json(route, { skus_ocultos: [] });
    if (pathname === '/api/medias-compras/visao') {
      return json(route, { colunas_meses: [], itens: [{
        sku: '001', titulo_anuncio: 'Produto', vendas_mensais: {},
        media_mensal: 5, saldo_atual_estoque: 0, compra_sugerida: 10,
      }] });
    }
    if (pathname === '/api/medias-compras/gerar-lista-compra') {
      if (method === 'POST' && state.fallback) return json(route, {}, 405);
      const payload = method === 'POST' ? request.postDataJSON() : Object.fromEntries(url.searchParams);
      state.creates.push({ method, payload });
      const list = addList(payload);
      return json(route, { success: true, lista_id: list.id, total_itens: 1 });
    }
    if (pathname === '/api/medias-compras/listas-pedidos/importar-excel') {
      const multipart = request.postDataBuffer().toString('utf8');
      const match = multipart.match(/name="fornecedor_id"\r\n\r\n([^\r\n]+)/);
      assert(match, 'Excel deve enviar fornecedor_id no multipart');
      state.imports.push(match[1]);
      return json(route, { lista: addList({ fornecedor_id: match[1] }) });
    }
    if (pathname === '/api/medias-compras/listas-pedidos') {
      return json(route, { listas: state.lists.map(list => ({ ...list, total_itens: list.itens.length })) });
    }
    const match = pathname.match(/^\/api\/medias-compras\/listas-pedidos\/([^/]+)$/);
    if (match) {
      const list = state.lists.find(item => item.id === match[1]);
      let responseList = list;
      if (method === 'PUT') {
        const payload = request.postDataJSON();
        const original = structuredClone(list);
        state.updates.push(payload);
        assert.strictEqual(request.headers().authorization, 'Bearer test');
        const field = Object.hasOwn(payload, 'nacionalizador_id') ? 'nacionalizador_id' :
          Object.hasOwn(payload, 'fornecedor_id') ? 'fornecedor_id' : 'loja';
        const gate = state.updateGates[field] || state.updateGate;
        if (gate) await gate;
        if (state.updateError) return json(route, {
          detail: Object.hasOwn(payload, 'nacionalizador_id') ? 'Falha ao salvar nacionalizador' : 'Falha ao salvar fornecedor',
        }, 503);
        Object.assign(list, payload);
        if (payload.fornecedor_id) list.supplier = state.suppliers.find(item => item.id === payload.fornecedor_id).nome_empresa;
        if (Object.hasOwn(payload, 'nacionalizador_id')) {
          list.nacionalizador = state.nationalizers.find(item => item.id === payload.nacionalizador_id)?.nome_empresa || '';
        }
        if (state.updateSnapshots) {
          responseList = { ...original, ...payload };
          if (Object.hasOwn(payload, 'fornecedor_id')) responseList.supplier = list.supplier;
          if (Object.hasOwn(payload, 'nacionalizador_id')) responseList.nacionalizador = list.nacionalizador;
        }
      }
      return json(route, { lista: responseList });
    }
    if (pathname.startsWith('/api/')) return json(route, {});
    return route.fulfill({ status: 200, contentType: 'application/javascript', body: '' });
  });
}

async function openGeneration(page) {
  await page.locator('#btnAbaLista').click();
  await page.locator('[data-jk-action="abrir-lista-compra"]').click();
  await page.locator('#modalListaCompra.open').waitFor();
}

async function chooseSupplierForImport(page, trigger, id) {
  await trigger.click();
  const select = page.locator('#jkFornecedorListaSelect');
  await page.waitForFunction(() => {
    const select = document.getElementById('jkFornecedorListaSelect');
    return select && !select.disabled && select.options.length > 1;
  });
  assert.strictEqual(await page.locator('#jkFornecedorListaConfirmar').isDisabled(), true);
  await select.selectOption(id);
  const chooserPromise = page.waitForEvent('filechooser');
  const importPromise = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/medias-compras/listas-pedidos/importar-excel' && response.request().method() === 'POST');
  await page.locator('#jkFornecedorListaConfirmar').click();
  const chooser = await chooserPromise;
  await chooser.setFiles({ name: 'pedido.xlsx', mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buffer: Buffer.from('fixture') });
  await importPromise;
}

const inlineListNames = new Map([['long-name', 'JK55-Richard Aproved']]);

function inlinePill(page, listId, field = 'fornecedor', container = '#listaWrap') {
  return page.locator(container + ' .lista-item').filter({ hasText: inlineListNames.get(listId) })
    .locator('[data-' + field + '-pill]');
}

function inlineInput(page, listId, field = 'fornecedor', container = '#listaWrap') {
  return inlinePill(page, listId, field, container).locator('input.meta-loja-input[data-' + field + '-lista-id="' + listId + '"]');
}

async function waitForClosedPill(page, listId, field, text, container = '#listaWrap') {
  const pill = inlinePill(page, listId, field, container);
  await pill.waitFor({ state: 'attached' });
  await page.waitForFunction(({ listName, field, text, container }) => {
    const card = Array.from(document.querySelectorAll(container + ' .lista-item'))
      .find(item => item.textContent.includes(listName));
    const pill = card?.querySelector('[data-' + field + '-pill]');
    return pill && !pill.querySelector('input') && pill.textContent.includes(text) &&
      pill.getAttribute('aria-busy') !== 'true';
  }, { listName: inlineListNames.get(listId), field, text, container });
  return pill;
}

async function openInlineEditor(page, listId, field = 'fornecedor', container = '#listaWrap', waitCatalog = true) {
  await inlinePill(page, listId, field, container).dblclick();
  const input = inlineInput(page, listId, field, container);
  await input.waitFor({ state: 'visible' });
  assert.strictEqual(await input.isDisabled(), false, 'O editor deve aceitar digitação enquanto o cadastro carrega');
  assert(await input.getAttribute('list'), 'O editor deve usar uma lista de registros cadastrados');
  if (waitCatalog) {
    await page.waitForFunction(({ listId, field, container }) => {
      const input = document.querySelector(container + ' input[data-' + field + '-lista-id="' + listId + '"]');
      return input && input.list && input.list.options.length >= 2;
    }, { listId, field, container });
  }
  return input;
}

async function assertControlKeepsCardClosed(page, listId, field, container = '#listaWrap') {
  const pill = inlinePill(page, listId, field, container);
  const url = page.url();
  const updatesBefore = state.updates.length;
  await pill.click();
  assert.strictEqual(await pill.locator('input').count(), 0, 'Clique simples deve manter o pill fechado');
  for (const key of ['Enter', 'Space']) {
    await pill.press(key);
    assert.strictEqual(page.url(), url, 'O teclado no pill não deve abrir o detalhe da lista');
  }
  const input = await openInlineEditor(page, listId, field, container);
  await input.press('Space');
  await input.press('Escape');
  assert.strictEqual(page.url(), url, 'O teclado no editor não deve abrir o detalhe da lista');
  assert.strictEqual(await pill.locator('input').count(), 0, 'Escape deve fechar o editor');
  assert.strictEqual(state.updates.length, updatesBefore, 'Cancelar uma edição não deve salvar');
}

async function testPendingUpdate(page, order, field, oldName, newName, newId) {
  let releaseUpdate;
  state.updateGate = new Promise(resolve => { releaseUpdate = resolve; });
  const updatesBefore = state.updates.length;
  const input = await openInlineEditor(page, order.id, field);
  await input.fill(newName);
  const pendingRequest = page.waitForRequest(request => request.method() === 'PUT' &&
    new URL(request.url()).pathname.endsWith('/listas-pedidos/' + order.id));
  await input.press('Enter');
  await pendingRequest;
  await input.dispatchEvent('keydown', { key: 'Enter', bubbles: true });
  await input.dispatchEvent('blur');
  await page.locator('#lojaFiltroListas button').filter({ hasText: 'Todas as lojas' }).click();
  const main = inlinePill(page, order.id, field);
  await main.waitFor();
  assert((await main.innerText()).includes(oldName), 'Pill recriado deve manter o nome anterior durante o PUT');
  assert.strictEqual(await main.getAttribute('aria-disabled'), 'true', 'O filtro deve respeitar a gravação pendente');
  await main.dispatchEvent('dblclick', { bubbles: true });
  assert.strictEqual(await main.locator('input').count(), 0, 'Pill recriado não pode iniciar outra gravação');
  await page.locator('#embarquesWrap .embarque-item').filter({ hasText: 'Embarque de teste' }).click();
  const detail = inlinePill(page, order.id, field, '#embarqueDetalheListas');
  await detail.waitFor();
  assert((await detail.innerText()).includes(oldName), 'O embarque deve conservar o valor anterior durante o PUT');
  assert.strictEqual(await detail.getAttribute('aria-disabled'), 'true', 'O embarque deve respeitar a gravação pendente');
  await detail.dispatchEvent('dblclick', { bubbles: true });
  assert.strictEqual(await detail.locator('input').count(), 0);
  assert.strictEqual(state.updates.length, updatesBefore + 1, 'Enter, blur e controles recriados devem produzir só um PUT');
  state.updateGate = null;
  releaseUpdate();
  await waitForClosedPill(page, order.id, field, newName, '#embarqueDetalheListas');
  await waitForClosedPill(page, order.id, field, newName);
  assert.deepStrictEqual(state.updates.at(-1), { [field + '_id']: newId });
  await assertControlKeepsCardClosed(page, order.id, field, '#embarqueDetalheListas');
  const detailEditor = await openInlineEditor(page, order.id, field, '#embarqueDetalheListas');
  await detailEditor.fill(oldName);
  await detailEditor.press('Enter');
  await waitForClosedPill(page, order.id, field, oldName, '#embarqueDetalheListas');
  await page.locator('#btnVoltarEmbarque').click();
  await waitForClosedPill(page, order.id, field, oldName);
}

async function testDelayedCatalog(page, order, field, expectedName) {
  const gateKey = field === 'fornecedor' ? 'supplierGate' : 'nationalizerGate';
  const readsKey = field === 'fornecedor' ? 'supplierReads' : 'nationalizerReads';
  let releaseRead;
  state[gateKey] = new Promise(resolve => { releaseRead = resolve; });
  const readsBefore = state[readsKey];
  const firstRequest = page.waitForRequest(request => new URL(request.url()).pathname ===
    '/api/cadastro/' + (field === 'fornecedor' ? 'fornecedores' : 'nacionalizadores'));
  const input = await openInlineEditor(page, order.id, field, '#listaWrap', false);
  await firstRequest;
  await input.fill('Valor digitado durante o carregamento');
  await inlinePill(page, 'long-name', field).dispatchEvent('dblclick', { bubbles: true });
  const secondInput = inlineInput(page, 'long-name', field);
  await secondInput.waitFor();
  assert.strictEqual(state[readsKey], readsBefore + 1, 'Editores simultâneos devem compartilhar o GET em andamento');
  await secondInput.press('Escape');
  await input.focus();
  state[gateKey] = null;
  releaseRead();
  await page.waitForFunction(({ id, field }) => {
    const input = document.querySelector('#listaWrap input[data-' + field + '-lista-id="' + id + '"]');
    return input?.list?.options.length >= 2;
  }, { id: order.id, field });
  assert.strictEqual(await input.inputValue(), 'Valor digitado durante o carregamento', 'Resposta tardia deve preservar o texto');
  assert.strictEqual(await input.evaluate(element => document.activeElement === element), true, 'Resposta tardia deve preservar o foco');
  await input.press('Escape');
  await waitForClosedPill(page, order.id, field, expectedName);
}

async function testConcurrentFields(page, order) {
  let releaseSupplier;
  let releaseNationalizer;
  state.updateGates.fornecedor_id = new Promise(resolve => { releaseSupplier = resolve; });
  state.updateGates.nacionalizador_id = new Promise(resolve => { releaseNationalizer = resolve; });
  state.updateSnapshots = true;
  const supplierRequest = page.waitForRequest(request => request.method() === 'PUT' &&
    request.postDataJSON()?.fornecedor_id === 'supplier-b');
  const supplierInput = await openInlineEditor(page, order.id);
  await supplierInput.fill('Fornecedor B');
  await supplierInput.press('Enter');
  await supplierRequest;
  const nationalizerRequest = page.waitForRequest(request => request.method() === 'PUT' &&
    request.postDataJSON()?.nacionalizador_id === 'nationalizer-a');
  const nationalizerInput = await openInlineEditor(page, order.id, 'nacionalizador');
  await nationalizerInput.fill('Nacionalizador A');
  await nationalizerInput.press('Enter');
  await nationalizerRequest;
  delete state.updateGates.nacionalizador_id;
  releaseNationalizer();
  await waitForClosedPill(page, order.id, 'nacionalizador', 'Nacionalizador A');
  delete state.updateGates.fornecedor_id;
  releaseSupplier();
  await waitForClosedPill(page, order.id, 'fornecedor', 'Fornecedor B');
  state.updateSnapshots = false;
  await page.locator('#lojaFiltroListas button').filter({ hasText: 'JK Pecas' }).click();
  await waitForClosedPill(page, order.id, 'nacionalizador', 'Nacionalizador A');
  await waitForClosedPill(page, order.id, 'fornecedor', 'Fornecedor B');
  assert.strictEqual(order.fornecedor_id, 'supplier-b');
  assert.strictEqual(order.nacionalizador_id, 'nationalizer-a');
  await page.reload();
  await waitForClosedPill(page, order.id, 'fornecedor', 'Fornecedor B');
  await waitForClosedPill(page, order.id, 'nacionalizador', 'Nacionalizador A');
  const supplierReset = await openInlineEditor(page, order.id);
  await supplierReset.fill('Fornecedor A');
  await supplierReset.press('Enter');
  await waitForClosedPill(page, order.id, 'fornecedor', 'Fornecedor A');
}

async function testStoreOutOfOrder(page, order) {
  for (const field of ['fornecedor', 'nacionalizador']) {
    await page.locator('#lojaFiltroListas button').filter({ hasText: 'Todas as lojas' }).click();
    let releaseStore;
    state.updateGates.loja = new Promise(resolve => { releaseStore = resolve; });
    state.updateSnapshots = true;
    const originalStore = order.loja;
    const newStore = 'Loja temporária ' + field;
    const storeRequest = page.waitForRequest(request => request.method() === 'PUT' && request.postDataJSON()?.loja === newStore);
    const storePill = inlinePill(page, order.id).locator('xpath=ancestor::div[contains(@class,"lista-item")][1]').locator('[data-loja-pill]');
    await storePill.dblclick();
    const storeInput = storePill.locator('input.meta-loja-input');
    await storeInput.fill(newStore);
    await storeInput.press('Enter');
    await storeRequest;
    const newName = field === 'fornecedor' ? 'Fornecedor B' : 'Nacionalizador B';
    const newId = field === 'fornecedor' ? 'supplier-b' : 'nationalizer-b';
    const input = await openInlineEditor(page, order.id, field);
    await input.fill(newName);
    await input.press('Enter');
    await waitForClosedPill(page, order.id, field, newName);
    delete state.updateGates.loja;
    releaseStore();
    await page.waitForFunction(({ listName, newStore }) => {
      const card = Array.from(document.querySelectorAll('#listaWrap .lista-item')).find(item => item.textContent.includes(listName));
      const pill = card?.querySelector('[data-loja-pill]');
      return pill && !pill.querySelector('input') && pill.textContent.includes(newStore);
    }, { listName: inlineListNames.get(order.id), newStore });
    state.updateSnapshots = false;
    await waitForClosedPill(page, order.id, field, newName);
    assert.strictEqual(order[field + '_id'], newId, 'Resposta antiga de Loja deve preservar o cadastro salvo depois');
    await page.locator('#lojaFiltroListas button').filter({ hasText: 'Todas as lojas' }).click();
    await waitForClosedPill(page, order.id, field, newName);
    await storePill.dblclick();
    await storePill.locator('input.meta-loja-input').fill(originalStore);
    await storePill.locator('input.meta-loja-input').press('Enter');
    await page.waitForFunction(({ listName, originalStore }) => {
      const card = Array.from(document.querySelectorAll('#listaWrap .lista-item')).find(item => item.textContent.includes(listName));
      const pill = card?.querySelector('[data-loja-pill]');
      return pill && !pill.querySelector('input') && pill.textContent.includes(originalStore);
    }, { listName: inlineListNames.get(order.id), originalStore });
  }
  await page.reload();
  await waitForClosedPill(page, order.id, 'fornecedor', 'Fornecedor B');
  await waitForClosedPill(page, order.id, 'nacionalizador', 'Nacionalizador B');
  const supplierReset = await openInlineEditor(page, order.id);
  await supplierReset.fill('Fornecedor A');
  await supplierReset.press('Enter');
  await waitForClosedPill(page, order.id, 'fornecedor', 'Fornecedor A');
}

async function testInlineSuppliers(page, longSupplier) {
  const longNationalizer = 'Nacionalizador Internacional de Comércio e Importação ' + 'Exemplo '.repeat(8).trim();
  const order = {
    id: 'inline-order', nome_lista: 'Pedido com fornecedor selecionável', loja: 'JK Pecas', store_id: 'store-jk',
    status: 'Analisando orçamento', fornecedor_id: 'supplier-a', supplier: 'Fornecedor A',
    nacionalizador_id: 'nationalizer-a', nacionalizador: 'Nacionalizador A', itens: [],
  };
  const historic = {
    id: 'historic-order', nome_lista: 'Pedido com vínculos históricos', loja: 'JK Pecas', store_id: 'store-jk',
    status: 'Analisando orçamento', fornecedor_id: 'removed-supplier', supplier: 'Fornecedor histórico',
    nacionalizador_id: 'removed-nationalizer', nacionalizador: 'Nacionalizador histórico', itens: [],
  };
  const legacy = {
    id: 'text-order', nome_lista: 'Pedido com nomes sem vínculo', loja: 'JK Pecas', store_id: 'store-jk',
    status: 'Analisando orçamento', supplier: 'Fornecedor sem vínculo', nacionalizador: 'Nacionalizador sem vínculo', itens: [],
  };
  const sameName = {
    id: 'same-name-order', nome_lista: 'Pedido histórico com nome cadastrado', loja: 'JK Pecas', store_id: 'store-jk',
    status: 'Analisando orçamento', supplier: 'Fornecedor A', nacionalizador: 'Nacionalizador A', itens: [],
  };
  for (const list of [order, historic, legacy, sameName]) inlineListNames.set(list.id, list.nome_lista);
  state.lists.push(order, historic, legacy, sameName);
  state.suppliers = [...suppliers, { id: 'supplier-long', nome_empresa: longSupplier }];
  state.nationalizers = [...nationalizers, { id: 'nationalizer-long', nome_empresa: longNationalizer }];
  Object.assign(state.lists.find(list => list.id === 'long-name'), {
    fornecedor_id: 'supplier-long', supplier: longSupplier,
    nacionalizador_id: 'nationalizer-long', nacionalizador: longNationalizer,
  });
  await page.evaluate(() => localStorage.setItem('jk_embarques_importacoes', JSON.stringify([{
    id: 'emb-inline', nome: 'Embarque de teste', lista_ids: ['inline-order', 'long-name'],
  }])));
  const supplierReadsBefore = state.supplierReads;
  const nationalizerReadsBefore = state.nationalizerReads;
  await page.goto('http://jk.test/importacoes.html');
  await waitForClosedPill(page, order.id, 'fornecedor', 'Fornecedor A');
  await waitForClosedPill(page, order.id, 'nacionalizador', 'Nacionalizador A');
  assert.strictEqual(state.supplierReads, supplierReadsBefore, 'Pills fechados não devem carregar fornecedores');
  assert.strictEqual(state.nationalizerReads, nationalizerReadsBefore, 'Pills fechados não devem carregar nacionalizadores');
  await assertControlKeepsCardClosed(page, order.id, 'fornecedor');
  await assertControlKeepsCardClosed(page, order.id, 'nacionalizador');

  let input = await openInlineEditor(page, order.id);
  await input.fill('Fornecedor B');
  await input.press('Enter');
  let pill = await waitForClosedPill(page, order.id, 'fornecedor', 'Fornecedor B');
  assert.strictEqual(await pill.evaluate(element => document.activeElement === element), true, 'Salvar com Enter deve devolver o foco ao pill');
  assert.deepStrictEqual(state.updates.at(-1), { fornecedor_id: 'supplier-b' });
  assert.strictEqual(order.supplier, 'Fornecedor B');
  assert.strictEqual(order.loja, 'JK Pecas', 'Trocar fornecedor deve preservar loja e store_id');
  assert.strictEqual(order.store_id, 'store-jk');
  await page.locator('#lojaFiltroListas button').filter({ hasText: 'JK Pecas' }).click();
  await waitForClosedPill(page, order.id, 'fornecedor', 'Fornecedor B');

  state.updateError = true;
  input = await openInlineEditor(page, order.id);
  await input.fill('Fornecedor A');
  await input.press('Enter');
  await page.locator('#status', { hasText: 'Falha ao salvar fornecedor' }).waitFor();
  assert.strictEqual(await input.inputValue(), 'Fornecedor A', 'Falha deve preservar o valor escolhido no editor');
  assert.strictEqual(await input.getAttribute('aria-invalid'), 'true');
  assert.strictEqual(await input.isDisabled(), false, 'Falha deve permitir tentar novamente');
  assert.strictEqual(order.fornecedor_id, 'supplier-b', 'Falha no PUT deve preservar o vínculo persistido');
  state.updateError = false;
  await input.press('Enter');
  pill = await waitForClosedPill(page, order.id, 'fornecedor', 'Fornecedor A');
  assert.strictEqual(await pill.getAttribute('aria-invalid'), null);
  await testPendingUpdate(page, order, 'fornecedor', 'Fornecedor A', 'Fornecedor B', 'supplier-b');

  input = await openInlineEditor(page, order.id, 'nacionalizador');
  await input.fill('Nacionalizador B');
  await page.locator('#btnImportarPedidoMenu').focus();
  await waitForClosedPill(page, order.id, 'nacionalizador', 'Nacionalizador B');
  assert.deepStrictEqual(state.updates.at(-1), { nacionalizador_id: 'nationalizer-b' });
  state.updateError = true;
  input = await openInlineEditor(page, order.id, 'nacionalizador');
  await input.fill('Nacionalizador A');
  await input.press('Enter');
  await page.locator('#status', { hasText: 'Falha ao salvar nacionalizador' }).waitFor();
  assert.strictEqual(await input.inputValue(), 'Nacionalizador A');
  assert.strictEqual(await input.getAttribute('aria-invalid'), 'true');
  assert.strictEqual(order.nacionalizador_id, 'nationalizer-b');
  state.updateError = false;
  await input.press('Enter');
  await waitForClosedPill(page, order.id, 'nacionalizador', 'Nacionalizador A');
  await testPendingUpdate(page, order, 'nacionalizador', 'Nacionalizador A', 'Nacionalizador B', 'nationalizer-b');
  input = await openInlineEditor(page, order.id, 'nacionalizador');
  await input.fill('');
  await input.press('Enter');
  await waitForClosedPill(page, order.id, 'nacionalizador', 'Nacionalizador');
  assert.deepStrictEqual(state.updates.at(-1), { nacionalizador_id: '' }, 'Nacionalizador vazio deve limpar o vínculo opcional');
  assert.strictEqual(order.nacionalizador, '');
  assert.strictEqual(order.fornecedor_id, 'supplier-a', 'Limpar nacionalizador deve preservar fornecedor');

  const updatesBeforeInvalid = state.updates.length;
  for (const value of ['', 'Fornecedor inexistente']) {
    input = await openInlineEditor(page, order.id);
    await input.fill(value);
    await input.press('Enter');
    await page.waitForFunction(() => document.querySelector('#listaWrap input[data-fornecedor-lista-id="inline-order"]')?.getAttribute('aria-invalid') === 'true');
    assert.strictEqual(state.updates.length, updatesBeforeInvalid, 'Fornecedor vazio ou fora do cadastro não pode gerar PUT');
    await input.press('Escape');
  }
  input = await openInlineEditor(page, order.id, 'nacionalizador');
  await input.fill('Nacionalizador inexistente');
  await input.press('Enter');
  assert.strictEqual(await input.getAttribute('aria-invalid'), 'true');
  assert.strictEqual(state.updates.length, updatesBeforeInvalid, 'Nacionalizador desconhecido não pode gerar PUT');
  await input.press('Escape');

  const updatesBeforeHistorical = state.updates.length;
  for (const list of [historic, legacy, sameName]) {
    for (const field of ['fornecedor', 'nacionalizador']) {
      const previousText = field === 'fornecedor' ? list.supplier : list.nacionalizador;
      input = await openInlineEditor(page, list.id, field);
      await input.press('Enter');
      await waitForClosedPill(page, list.id, field, previousText);
      input = await openInlineEditor(page, list.id, field);
      await page.locator('#btnImportarPedidoMenu').focus();
      await waitForClosedPill(page, list.id, field, previousText);
    }
  }
  assert.strictEqual(state.updates.length, updatesBeforeHistorical, 'Nomes históricos sem alteração devem manter os vínculos sem PUT');
  for (const field of ['fornecedor', 'nacionalizador']) {
    input = await openInlineEditor(page, sameName.id, field);
    await input.fill(field === 'fornecedor' ? 'Fornecedor A' : 'Nacionalizador A');
    await input.press('Enter');
    await waitForClosedPill(page, sameName.id, field, field === 'fornecedor' ? 'Fornecedor A' : 'Nacionalizador A');
    const expectedId = field === 'fornecedor' ? 'supplier-a' : 'nationalizer-a';
    assert.deepStrictEqual(state.updates.at(-1), { [field + '_id']: expectedId }, 'Selecionar explicitamente o mesmo nome histórico deve resolver o ID');
    assert.strictEqual(sameName[field + '_id'], expectedId);
  }

  for (const field of ['fornecedor', 'nacionalizador']) {
    const collection = field === 'fornecedor' ? state.suppliers : state.nationalizers;
    const duplicateName = field === 'fornecedor' ? 'Fornecedor duplicado' : 'Nacionalizador duplicado';
    const firstId = field + '-duplicate-a';
    const secondId = field + '-duplicate-b';
    collection.push({ id: firstId, nome_empresa: duplicateName }, { id: secondId, nome_empresa: duplicateName });
    input = await openInlineEditor(page, order.id, field);
    const duplicateOptions = await input.evaluate((element, duplicateName) =>
      Array.from(element.list.options).map(option => option.value).filter(value => value.startsWith(duplicateName)), duplicateName);
    assert.deepStrictEqual(duplicateOptions.sort(), [duplicateName + ' (' + firstId + ')', duplicateName + ' (' + secondId + ')'].sort(),
      'Empresas homônimas devem permitir escolher o ID exato');
    await input.fill(duplicateName + ' (' + secondId + ')');
    await input.press('Enter');
    await waitForClosedPill(page, order.id, field, duplicateName);
    assert.deepStrictEqual(state.updates.at(-1), { [field + '_id']: secondId });
    input = await openInlineEditor(page, order.id, field);
    assert.strictEqual(await input.inputValue(), duplicateName, 'Pill reaberto deve mostrar o nome salvo');
    const updatesBeforeUnchangedDuplicate = state.updates.length;
    await input.press('Enter');
    await waitForClosedPill(page, order.id, field, duplicateName);
    assert.strictEqual(state.updates.length, updatesBeforeUnchangedDuplicate, 'Nome duplicado inalterado deve preservar o ID salvo');
    input = await openInlineEditor(page, order.id, field);
    await input.fill(field === 'fornecedor' ? 'Fornecedor A' : '');
    await input.press('Enter');
    await waitForClosedPill(page, order.id, field, field === 'fornecedor' ? 'Fornecedor A' : 'Nacionalizador');
  }

  await testDelayedCatalog(page, order, 'fornecedor', 'Fornecedor A');
  await testDelayedCatalog(page, order, 'nacionalizador', 'Nacionalizador');
  await testConcurrentFields(page, order);
  await testStoreOutOfOrder(page, order);
  input = await openInlineEditor(page, order.id, 'nacionalizador');
  await input.fill('Nacionalizador B');
  await input.press('Enter');
  await waitForClosedPill(page, order.id, 'nacionalizador', 'Nacionalizador B');
  await page.reload();
  await waitForClosedPill(page, order.id, 'fornecedor', 'Fornecedor A');
  await waitForClosedPill(page, order.id, 'nacionalizador', 'Nacionalizador B');

  for (const width of [1033, 880, 390]) {
    await page.setViewportSize({ width, height: 900 });
    for (const listId of ['long-name', order.id]) {
      for (const field of ['fornecedor', 'nacionalizador']) {
        const layout = await inlinePill(page, listId, field).evaluate(pill => {
          const card = pill.closest('.lista-item');
          const actionsRect = card.querySelector('.lista-card-acoes').getBoundingClientRect();
          const rect = pill.getBoundingClientRect();
          const overlap = rect.left < actionsRect.right && rect.right > actionsRect.left &&
            rect.top < actionsRect.bottom && rect.bottom > actionsRect.top;
          return { fits: card.scrollWidth <= card.clientWidth + 1, overlap, actionsVisible: actionsRect.width > 0, width: rect.width };
        });
        assert(layout.fits && !layout.overlap && layout.actionsVisible, 'Pill de ' + field + ' deve caber sem ocultar ações em ' + width + ' px');
        assert(layout.width >= 72, 'Pill de ' + field + ' deve continuar acessível em ' + width + ' px');
        input = await openInlineEditor(page, listId, field);
        assert(await input.evaluate(element => element.closest('.lista-item').scrollWidth <= element.closest('.lista-item').clientWidth + 1),
          'Editor de ' + field + ' deve caber em ' + width + ' px');
        await input.press('Escape');
      }
    }
    if (previewDirectory && width === 390) await page.locator('.container').screenshot({ path: path.join(previewDirectory, 'importacoes-fornecedor-selecionavel-mobile.png') });
  }
  await page.setViewportSize({ width: 880, height: 900 });
  assert((await inlinePill(page, 'long-name').getAttribute('title')).includes(longSupplier));
  assert((await inlinePill(page, 'long-name', 'nacionalizador').getAttribute('title')).includes(longNationalizer));
  if (previewDirectory) {
    await page.locator('.container').screenshot({ path: path.join(previewDirectory, 'importacoes-fornecedor-selecionavel.png') });
    await inlinePill(page, order.id).locator('xpath=ancestor::div[contains(@class,"lista-item")][1]').screenshot({ path: path.join(previewDirectory, 'cartao-fornecedor-selecionavel.png') });
  }

  const updatesBeforeUnavailable = state.updates.length;
  state.suppliers = [];
  input = await openInlineEditor(page, order.id, 'fornecedor', '#listaWrap', false);
  await page.locator('#status').filter({ hasText: /Cadastre|Nenhum fornecedor/ }).waitFor();
  await input.fill('Fornecedor B');
  await input.press('Enter');
  assert.strictEqual(state.updates.length, updatesBeforeUnavailable);
  await input.press('Escape');
  state.suppliers = suppliers;
  for (const field of ['fornecedor', 'nacionalizador']) {
    const errorKey = field === 'fornecedor' ? 'supplierError' : 'nationalizerError';
    const readKey = field === 'fornecedor' ? 'supplierReads' : 'nationalizerReads';
    state[errorKey] = true;
    input = await openInlineEditor(page, order.id, field, '#listaWrap', false);
    await page.waitForFunction(({ id, field }) => document.querySelector('#listaWrap input[data-' + field + '-lista-id="' + id + '"]')?.getAttribute('aria-invalid') === 'true', { id: order.id, field });
    await page.locator('#status').filter({ hasText: /indisponível/ }).waitFor();
    assert.strictEqual(await input.isDisabled(), false, 'Erro do cadastro deve preservar editor editável');
    await input.press('Escape');
    state[errorKey] = false;
    const readsBeforeRetry = state[readKey];
    input = await openInlineEditor(page, order.id, field);
    assert.strictEqual(state[readKey], readsBeforeRetry + 1, 'Reabrir após falha deve tentar consultar o cadastro novamente');
    await input.press('Escape');
  }
  assert.strictEqual(state.updates.length, updatesBeforeUnavailable, 'Cadastro vazio ou indisponível não deve gravar dados');
  assert.strictEqual(order.fornecedor_id, 'supplier-a');
  assert.strictEqual(order.nacionalizador_id, 'nationalizer-b');

  state.nationalizerError = true;
  input = await openInlineEditor(page, order.id, 'nacionalizador', '#listaWrap', false);
  await page.waitForFunction(() => document.querySelector('#listaWrap input[data-nacionalizador-lista-id="inline-order"]')?.getAttribute('aria-invalid') === 'true');
  await input.fill('');
  await input.press('Enter');
  await waitForClosedPill(page, order.id, 'nacionalizador', 'Não informado');
  assert.deepStrictEqual(state.updates.at(-1), { nacionalizador_id: '' }, 'Limpar Nacionalizador deve funcionar mesmo com erro no catálogo');
  assert.strictEqual(order.fornecedor_id, 'supplier-a');
  state.nationalizerError = false;
  input = await openInlineEditor(page, order.id, 'nacionalizador');
  await input.fill('Nacionalizador B');
  await input.press('Enter');
  await waitForClosedPill(page, order.id, 'nacionalizador', 'Nacionalizador B');

  let releaseNationalizerRead;
  state.nationalizerGate = new Promise(resolve => { releaseNationalizerRead = resolve; });
  const delayedRead = page.waitForResponse(response => new URL(response.url()).pathname === '/api/cadastro/nacionalizadores');
  const startedRead = page.waitForRequest(request => new URL(request.url()).pathname === '/api/cadastro/nacionalizadores');
  input = await openInlineEditor(page, order.id, 'nacionalizador', '#listaWrap', false);
  await startedRead;
  await input.fill('');
  await input.press('Enter');
  await waitForClosedPill(page, order.id, 'nacionalizador', 'Não informado');
  assert.deepStrictEqual(state.updates.at(-1), { nacionalizador_id: '' }, 'Limpar Nacionalizador não deve aguardar catálogo pendente');
  state.nationalizerGate = null;
  releaseNationalizerRead();
  await delayedRead;
  await waitForClosedPill(page, order.id, 'nacionalizador', 'Não informado');
  input = await openInlineEditor(page, order.id, 'nacionalizador');
  await input.fill('Nacionalizador B');
  await input.press('Enter');
  await waitForClosedPill(page, order.id, 'nacionalizador', 'Nacionalizador B');
  assert.strictEqual(order.fornecedor_id, 'supplier-a');
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await installRoutes(page);
    await page.goto('http://jk.test/medias_compras.html');
    await page.locator('#lojaBotoes button').filter({ hasText: 'JK Pecas' }).click();

    await openGeneration(page);
    await page.waitForFunction(() => !document.getElementById('fornecedorListaPedido').disabled);
    assert.strictEqual(await page.locator('#btnConfirmarListaCompra').isDisabled(), true);
    assert.strictEqual(state.creates.length, 0);
    await page.locator('#nomeListaPedido').fill('Lista fornecedor A');
    if (previewDirectory) {
      fs.mkdirSync(previewDirectory, { recursive: true });
      await page.locator('#modalListaCompra').screenshot({ path: path.join(previewDirectory, 'criar-lista-fornecedor.png') });
    }
    await page.locator('#fornecedorListaPedido').selectOption('supplier-a');
    await page.locator('#btnConfirmarListaCompra').click();
    await page.waitForFunction(() => !document.getElementById('modalListaCompra').classList.contains('open'));
    assert.strictEqual(state.creates.at(-1).payload.fornecedor_id, 'supplier-a');
    await page.locator('.lista-item').filter({ hasText: 'Lista fornecedor A' }).filter({ hasText: 'Fornecedor A' }).waitFor();

    state.fallback = true;
    await openGeneration(page);
    await page.waitForFunction(() => !document.getElementById('fornecedorListaPedido').disabled);
    await page.locator('#nomeListaPedido').fill('Lista fornecedor B');
    await page.locator('#fornecedorListaPedido').selectOption('supplier-b');
    await page.locator('#btnConfirmarListaCompra').click();
    await page.waitForFunction(() => !document.getElementById('modalListaCompra').classList.contains('open'));
    assert.strictEqual(state.creates.at(-1).method, 'GET');
    assert.strictEqual(state.creates.at(-1).payload.fornecedor_id, 'supplier-b');

    state.suppliers = [];
    await openGeneration(page);
    await page.locator('#modalListaCompraStatus', { hasText: 'Cadastre um fornecedor' }).waitFor();
    assert.strictEqual(await page.locator('#btnConfirmarListaCompra').isDisabled(), true);
    await page.locator('[data-jk-action="fechar-lista-compra"]').first().click();
    state.suppliers = suppliers;
    state.supplierError = true;
    await openGeneration(page);
    await page.locator('#modalListaCompraStatus', { hasText: 'Cadastro indisponível' }).waitFor();
    assert.strictEqual(await page.locator('#btnConfirmarListaCompra').isDisabled(), true);
    await page.locator('[data-jk-action="fechar-lista-compra"]').first().click();
    state.supplierError = false;

    await page.locator('#btnAbaListasPedidos').click();
    await page.locator('.lista-item').filter({ hasText: 'Lista antiga' }).filter({ hasText: 'Fornecedor não informado' }).click();
    await page.waitForFunction(() => !document.getElementById('fornecedorListaPedidoEdit').disabled);
    await page.locator('#nomeListaPedidoEdit').fill('Lista antiga editada');
    await page.locator('#btnSalvarListaPedido').click();
    await page.locator('#statusListaPedido', { hasText: 'Lista salva com sucesso.' }).waitFor();
    assert.strictEqual(Object.hasOwn(state.updates.at(-1), 'fornecedor_id'), false);
    await page.locator('#fornecedorListaPedidoEdit').selectOption('supplier-a');
    await page.locator('#btnSalvarListaPedido').click();
    await page.waitForFunction(() => listaPedidoAtual.fornecedor_id === 'supplier-a');
    assert.strictEqual(state.updates.at(-1).fornecedor_id, 'supplier-a');

    await page.locator('#btnImportarListaPedidoExcel').click();
    await page.locator('#jkFornecedorListaCancelar').click();
    assert.strictEqual(state.imports.length, 0);
    await chooseSupplierForImport(page, page.locator('#btnImportarListaPedidoExcel'), 'supplier-b');
    await page.locator('#statusListaPedido', { hasText: 'Lista adicionada por Excel com sucesso.' }).waitFor();
    assert.deepStrictEqual(state.imports, ['supplier-b']);

    const longSupplier = ('Fornecedor Internacional de Componentes Automotivos e Equipamentos para Importação ' + 'Exemplo '.repeat(5)).trim();
    state.lists.push({ id: 'long-name', nome_lista: 'JK55-Richard Aproved', loja: 'JK Pecas', store_id: 'store-jk',
      status: 'Analisando orçamento', fornecedor_id: 'supplier-a', supplier: longSupplier, itens: [] });
    await page.setViewportSize({ width: 1033, height: 900 });
    await page.goto('http://jk.test/importacoes.html');
    await page.locator('.meta-fornecedor').filter({ hasText: 'Fornecedor B' }).first().waitFor();
    const longCard = page.locator('.lista-item--compacta').filter({ hasText: 'JK55-Richard Aproved' });
    assert.strictEqual(await longCard.evaluate(element => element.scrollWidth <= element.clientWidth + 1), true,
      'Fornecedor com nome longo não deve estourar o cartão nem esconder ações');
    assert((await longCard.locator('.meta-fornecedor').getAttribute('title')).includes('Fornecedor: ' + longSupplier));
    if (previewDirectory) {
      await page.locator('.container').screenshot({ path: path.join(previewDirectory, 'listas-com-fornecedor.png') });
    }
    state.suppliers = [];
    await page.locator('#btnImportarPedidoMenu').click();
    await page.locator('#jkFornecedorListaModal', { hasText: 'Cadastre um fornecedor' }).waitFor();
    assert.strictEqual(await page.locator('#jkFornecedorListaConfirmar').isDisabled(), true);
    await page.locator('#jkFornecedorListaCancelar').click();
    assert.strictEqual(state.imports.length, 1);
    state.suppliers = suppliers;
    await chooseSupplierForImport(page, page.locator('#btnImportarPedidoMenu'), 'supplier-a');
    await page.locator('.meta-fornecedor').filter({ hasText: 'Fornecedor A' }).first().waitFor();
    assert.deepStrictEqual(state.imports, ['supplier-b', 'supplier-a']);

    const imported = state.lists.at(-1);
    await page.goto('http://jk.test/importacoes_lista.html?lista_id=' + imported.id);
    await page.waitForFunction(() => {
      const select = document.getElementById('resumoFornecedorSelect');
      return select && !select.disabled;
    });
    assert.strictEqual(await page.locator('#resumoFornecedorSelect').inputValue(), 'supplier-a');
    await page.locator('#resumoFornecedorSelect').selectOption('supplier-b');
    await page.waitForFunction(() => document.getElementById('resumoFornecedorSelect')?.value === 'supplier-b' && !document.getElementById('resumoFornecedorSelect').disabled);
    assert.deepStrictEqual(state.updates.at(-1), { fornecedor_id: 'supplier-b' });
    assert.strictEqual(imported.supplier, 'Fornecedor B');
    await testInlineSuppliers(page, longSupplier);
    assert.deepStrictEqual(errors, []);
    console.log('Listas com fornecedor e nacionalizador: geração POST/GET, Excel, detalhe, pills, duplo clique, Enter/blur/Esc, históricos, catálogo atrasado/erro, retry, concorrência e layout: OK');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
