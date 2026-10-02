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
    assert.deepStrictEqual(errors, []);
    console.log('Listas com fornecedor: geração POST/GET, Excel dos dois módulos, cadastro vazio/erro, cancelamento, listas antigas e troca: OK');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
