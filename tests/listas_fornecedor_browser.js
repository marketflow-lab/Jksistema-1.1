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
const state = {
  suppliers,
  supplierError: false,
  updateError: false,
  updateGate: null,
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
      return state.supplierError
        ? json(route, { detail: 'Cadastro indisponível' }, 500)
        : json(route, state.suppliers);
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
      if (method === 'PUT') {
        const payload = request.postDataJSON();
        state.updates.push(payload);
        assert.strictEqual(request.headers().authorization, 'Bearer test');
        if (state.updateGate) await state.updateGate;
        if (state.updateError) return json(route, { detail: 'Falha ao salvar fornecedor' }, 503);
        Object.assign(list, payload);
        if (payload.fornecedor_id) list.supplier = suppliers.find(item => item.id === payload.fornecedor_id).nome_empresa;
      }
      return json(route, { lista: list });
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

function inlineSelector(listId, container = '#listaWrap') {
  return container + ' select[data-fornecedor-lista-id="' + listId + '"]';
}

async function waitForInlineReady(page, selector, expectedValue) {
  await page.waitForFunction(({ selector, expectedValue }) => {
    const select = document.querySelector(selector);
    return select && !select.disabled && select.options.length > 1 &&
      (expectedValue === undefined || select.value === expectedValue);
  }, { selector, expectedValue });
  return page.locator(selector);
}

async function assertSupplierControlKeepsCardClosed(page, select) {
  const url = page.url();
  const updatesBefore = state.updates.length;
  await select.click();
  await select.press('Escape');
  for (const key of ['Enter', 'Space']) {
    await select.press(key);
    await select.press('Escape');
    assert.strictEqual(page.url(), url, 'O teclado no fornecedor não deve abrir o detalhe da lista');
  }
  const label = select.locator('..');
  const labelBox = await label.boundingBox();
  await label.click({ position: { x: 8, y: labelBox.height / 2 } });
  assert.strictEqual(page.url(), url, 'O clique no rótulo do fornecedor não deve abrir o detalhe da lista');
  assert.strictEqual(state.updates.length, updatesBefore, 'Abrir o seletor sem trocar a opção não deve salvar');
}

async function testInlineSuppliers(page, longSupplier) {
  const order = {
    id: 'inline-order', nome_lista: 'Pedido com fornecedor selecionável', loja: 'JK Pecas', store_id: 'store-jk',
    status: 'Analisando orçamento', fornecedor_id: 'supplier-a', supplier: 'Fornecedor A', itens: [],
  };
  state.lists.push(order);
  state.suppliers = [...suppliers, { id: 'supplier-long', nome_empresa: longSupplier }];
  Object.assign(state.lists.find(list => list.id === 'long-name'), { fornecedor_id: 'supplier-long', supplier: longSupplier });
  await page.evaluate(() => localStorage.setItem('jk_embarques_importacoes', JSON.stringify([{
    id: 'emb-inline', nome: 'Embarque de teste', lista_ids: ['inline-order', 'long-name'],
  }])));
  await page.goto('http://jk.test/importacoes.html');
  const mainSelector = inlineSelector(order.id);
  let main = await waitForInlineReady(page, mainSelector, 'supplier-a');
  await assertSupplierControlKeepsCardClosed(page, main);

  await main.focus();
  await main.selectOption('supplier-b');
  main = await waitForInlineReady(page, mainSelector, 'supplier-b');
  assert.strictEqual(await main.evaluate(select => document.activeElement === select), true,
    'Após salvar o fornecedor, o controle deve recuperar o foco para continuar usando o teclado');
  assert.deepStrictEqual(state.updates.at(-1), { fornecedor_id: 'supplier-b' });
  assert.strictEqual(order.supplier, 'Fornecedor B');
  assert.strictEqual(order.loja, 'JK Pecas', 'Trocar o fornecedor deve preservar a loja e o filtro');
  await page.locator('#lojaFiltroListas button').filter({ hasText: 'JK Pecas' }).click();
  main = await waitForInlineReady(page, mainSelector, 'supplier-b');

  state.updateError = true;
  await main.selectOption('supplier-a');
  await waitForInlineReady(page, mainSelector, 'supplier-b');
  await page.locator('#status', { hasText: 'Falha ao salvar fornecedor' }).waitFor();
  assert.strictEqual(order.fornecedor_id, 'supplier-b', 'Uma falha no PUT deve manter o fornecedor persistido');
  state.updateError = false;

  // O filtro recria o primeiro controle, e o embarque monta outro para a mesma lista.
  // Ambos precisam respeitar a requisição ainda pendente para impedir duas gravações.
  let releaseUpdate;
  state.updateGate = new Promise(resolve => { releaseUpdate = resolve; });
  const updatesBeforePending = state.updates.length;
  const pendingRequest = page.waitForRequest(request => request.method() === 'PUT' &&
    new URL(request.url()).pathname.endsWith('/listas-pedidos/' + order.id));
  await page.locator(mainSelector).selectOption('supplier-a');
  await pendingRequest;
  await page.waitForFunction(selector => document.querySelector(selector)?.disabled, mainSelector);
  await page.locator('#lojaFiltroListas button').filter({ hasText: 'Todas as lojas' }).click();
  await page.waitForFunction(selector => {
    const select = document.querySelector(selector);
    return select && select.disabled && Array.from(select.options).some(option => option.value === 'supplier-b');
  }, mainSelector);
  await page.locator('#embarquesWrap .embarque-item').filter({ hasText: 'Embarque de teste' }).click();
  const detailSelector = inlineSelector(order.id, '#embarqueDetalheListas');
  await page.waitForFunction(listId => {
    const selects = Array.from(document.querySelectorAll('select[data-fornecedor-lista-id="' + listId + '"]'));
    return selects.length === 2 && selects.every(select => select.disabled &&
      Array.from(select.options).some(option => option.value === 'supplier-b'));
  }, order.id);
  assert.strictEqual(state.updates.length, updatesBeforePending + 1, 'Recriar os controles deve manter apenas um PUT em andamento');
  state.updateGate = null;
  releaseUpdate();
  await waitForInlineReady(page, mainSelector, 'supplier-a');
  let detail = await waitForInlineReady(page, detailSelector, 'supplier-a');
  assert.strictEqual(order.supplier, 'Fornecedor A');
  await assertSupplierControlKeepsCardClosed(page, detail);

  await detail.selectOption('supplier-b');
  detail = await waitForInlineReady(page, detailSelector, 'supplier-b');
  assert.deepStrictEqual(state.updates.at(-1), { fornecedor_id: 'supplier-b' });
  await page.locator('#btnVoltarEmbarque').click();
  await waitForInlineReady(page, mainSelector, 'supplier-b');
  await page.locator('#lojaFiltroListas button').filter({ hasText: 'JK Pecas' }).click();
  await waitForInlineReady(page, mainSelector, 'supplier-b');
  await page.locator('#embarquesWrap .embarque-item').filter({ hasText: 'Embarque de teste' }).click();
  detail = await waitForInlineReady(page, detailSelector, 'supplier-b');
  state.updateError = true;
  await detail.selectOption('supplier-a');
  await waitForInlineReady(page, detailSelector, 'supplier-b');
  await page.locator('#status', { hasText: 'Falha ao salvar fornecedor' }).waitFor();
  assert.strictEqual(order.fornecedor_id, 'supplier-b');
  state.updateError = false;
  assert.strictEqual(await detail.getAttribute('aria-invalid'), 'true');
  await detail.selectOption('supplier-a');
  detail = await waitForInlineReady(page, detailSelector, 'supplier-a');
  assert.strictEqual(await detail.getAttribute('aria-invalid'), null, 'Uma nova gravação bem sucedida deve limpar o erro anterior');
  assert.strictEqual(await detail.getAttribute('title'), null);
  await detail.selectOption('supplier-b');
  await waitForInlineReady(page, detailSelector, 'supplier-b');
  if (previewDirectory) {
    await page.locator('.container').screenshot({ path: path.join(previewDirectory, 'embarque-fornecedor-selecionavel.png') });
  }
  await page.locator('#btnVoltarEmbarque').click();
  await page.reload();
  await waitForInlineReady(page, mainSelector, 'supplier-b');
  assert.strictEqual(order.supplier, 'Fornecedor B', 'O fornecedor salvo deve continuar selecionado após recarregar a página');

  const longCard = page.locator('.lista-item--compacta').filter({ hasText: 'JK55-Richard Aproved' });
  await waitForInlineReady(page, inlineSelector('long-name'), 'supplier-long');
  for (const width of [1033, 880, 390]) {
    await page.setViewportSize({ width, height: 900 });
    for (const listId of ['long-name', order.id]) {
      const layout = await page.locator(inlineSelector(listId)).evaluate(select => {
        const card = select.closest('.lista-item');
        const actions = card.querySelector('.lista-card-acoes');
        const selectRect = select.getBoundingClientRect();
        const actionsRect = actions.getBoundingClientRect();
        const overlap = selectRect.left < actionsRect.right && selectRect.right > actionsRect.left &&
          selectRect.top < actionsRect.bottom && selectRect.bottom > actionsRect.top;
        return { fits: card.scrollWidth <= card.clientWidth + 1, overlap, actionsVisible: actionsRect.width > 0,
          selectWidth: selectRect.width };
      });
      assert.strictEqual(layout.fits && !layout.overlap && layout.actionsVisible, true,
        'Nomes longos devem caber no cartão e manter as ações acessíveis em ' + width + ' px');
      assert(layout.selectWidth >= 72, 'O fornecedor deve continuar selecionável no cartão ' + listId +
        ' em ' + width + ' px: largura do select = ' + layout.selectWidth);
    }
    if (previewDirectory && width === 390) {
      await page.locator('.container').screenshot({ path: path.join(previewDirectory, 'importacoes-fornecedor-selecionavel-mobile.png') });
    }
  }
  await page.setViewportSize({ width: 880, height: 900 });
  assert.strictEqual(await longCard.locator('[data-fornecedor-pill]').getAttribute('title'), 'Fornecedor: ' + longSupplier);
  if (previewDirectory) {
    await page.locator('.container').screenshot({ path: path.join(previewDirectory, 'importacoes-fornecedor-selecionavel.png') });
    await page.locator(mainSelector).locator('xpath=ancestor::div[contains(@class,"lista-item")][1]').screenshot({
      path: path.join(previewDirectory, 'cartao-fornecedor-selecionavel.png'),
    });
  }

  const updatesBeforeUnavailable = state.updates.length;
  state.suppliers = [];
  await page.reload();
  await page.waitForFunction(selector => {
    const select = document.querySelector(selector);
    return select && select.disabled && Array.from(select.options).some(option => /Nenhum fornecedor cadastrado/.test(option.textContent));
  }, mainSelector);
  assert.strictEqual(order.fornecedor_id, 'supplier-b');
  state.suppliers = suppliers;
  state.supplierError = true;
  await page.reload();
  await page.waitForFunction(selector => {
    const select = document.querySelector(selector);
    return select && select.disabled && /Não foi possível carregar/.test(select.textContent);
  }, mainSelector);
  assert.strictEqual(state.updates.length, updatesBeforeUnavailable, 'Cadastro vazio ou indisponível não deve gravar dados');
  assert.strictEqual(order.fornecedor_id, 'supplier-b');
  state.supplierError = false;
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
    assert.strictEqual(await longCard.locator('.meta-fornecedor').getAttribute('title'), 'Fornecedor: ' + longSupplier);
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
    console.log('Listas com fornecedor: geração POST/GET, Excel, cadastro vazio/erro, listas antigas, troca inline nos cartões e embarques, restauração e bloqueio de PUT duplicado: OK');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
