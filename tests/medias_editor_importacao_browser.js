'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'medias_compras.html'), 'utf8');
const json = (route, body, status = 200) => route.fulfill({
  status,
  contentType: 'application/json',
  body: JSON.stringify(body),
});

(async () => {
  const browser = await chromium.launch({ headless: true });
  const listas = [{
    id: 'lista-1',
    nome_lista: 'Pedido inicial',
    loja: 'JK Pecas',
    status: 'Lista gerada',
    created_at: '2026-08-26T10:00:00Z',
    updated_at: '2026-08-26T10:00:00Z',
    itens: [{
      SKU: '001',
      'Título do produto em inglês': 'Produto inicial',
      Quantidade: 2,
      'Valor unidade': 10,
      'Valor total': 20,
    }],
  }];
  const salvamentos = [];
  const statusAplicados = [];
  let importacoes = 0;

  try {
    const page = await browser.newPage();
    const errosPagina = [];
    page.on('pageerror', error => errosPagina.push(error.message));
    await page.route('**/*', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const method = request.method();

      if (url.pathname === '/medias_compras.html') {
        await route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: html });
        return;
      }
      if (url.pathname.startsWith('/medias_compras/')) {
        const relativePath = url.pathname.replace(/^\//, '');
        const body = fs.readFileSync(path.join(root, 'static', relativePath));
        await route.fulfill({
          status: 200,
          contentType: relativePath.endsWith('.css') ? 'text/css' : 'application/javascript',
          body,
        });
        return;
      }
      if (url.pathname === '/auth.js') {
        await route.fulfill({
          status: 200,
          contentType: 'application/javascript',
          body: 'window.verificarSessao = () => true; window.obterAuthHeaders = () => ({});',
        });
        return;
      }
      if (url.pathname === '/api/lojas') {
        await json(route, [{ nome: 'JK Pecas' }]);
        return;
      }
      if (url.pathname === '/api/medias-compras/preferencias-skus-ocultos') {
        await json(route, { skus_ocultos: [] });
        return;
      }
      if (url.pathname === '/api/medias-compras/visao') {
        await json(route, { colunas_meses: [], itens: [] });
        return;
      }
      if (url.pathname === '/api/cadastro/produto') {
        await json(route, {
          produto: {
            sku: url.searchParams.get('sku'),
            titulo: 'Produto adicionado',
            OEM: 'OEM-2',
          },
        });
        return;
      }
      if (url.pathname === '/api/medias-compras/listas-pedidos/importar-excel' && method === 'POST') {
        importacoes += 1;
        const criada = {
          id: 'lista-2',
          nome_lista: 'Pedido importado',
          loja: 'JK Pecas',
          status: 'Lista gerada',
          created_at: '2026-08-26T11:00:00Z',
          updated_at: '2026-08-26T11:00:00Z',
          itens: [],
        };
        listas.push(criada);
        await json(route, { lista: criada });
        return;
      }
      if (url.pathname.endsWith('/gerar-download') && method === 'POST') {
        await json(route, { filename: 'pedido_teste.xlsx' });
        return;
      }
      if (url.pathname.endsWith('/download') && method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          body: Buffer.from('xlsx-local'),
        });
        return;
      }
      if (url.pathname.endsWith('/status') && method === 'PATCH') {
        const id = url.pathname.split('/').slice(-2, -1)[0];
        const payload = request.postDataJSON();
        const lista = listas.find(item => item.id === id);
        lista.status = payload.status;
        statusAplicados.push(payload.status);
        await json(route, { lista });
        return;
      }
      if (url.pathname.endsWith('/adicionar-sku') && method === 'POST') {
        const id = url.pathname.split('/').slice(-2, -1)[0];
        const payload = request.postDataJSON();
        const lista = listas.find(item => item.id === id);
        lista.itens.push({
          SKU: payload.sku,
          'Título do produto em inglês': 'Produto adicionado',
          Quantidade: payload.quantidade,
          'Valor unidade': payload.valor_unitario,
          'Valor total': payload.quantidade * payload.valor_unitario,
        });
        await json(route, { lista, acao: 'adicionado' });
        return;
      }
      if (url.pathname === '/api/medias-compras/listas-pedidos' && method === 'GET') {
        await json(route, {
          listas: listas.map(lista => ({
            id: lista.id,
            nome_lista: lista.nome_lista,
            loja: lista.loja,
            status: lista.status,
            created_at: lista.created_at,
            updated_at: lista.updated_at,
            total_itens: lista.itens.length,
          })),
        });
        return;
      }
      const matchLista = url.pathname.match(/^\/api\/medias-compras\/listas-pedidos\/([^/]+)$/);
      if (matchLista && method === 'GET') {
        await json(route, { lista: listas.find(item => item.id === matchLista[1]) });
        return;
      }
      if (matchLista && method === 'PUT') {
        const lista = listas.find(item => item.id === matchLista[1]);
        const payload = request.postDataJSON();
        Object.assign(lista, payload, { updated_at: '2026-08-26T12:00:00Z' });
        salvamentos.push(payload);
        await json(route, { lista });
        return;
      }
      await route.fulfill({ status: 200, contentType: 'application/javascript', body: '' });
    });

    await page.goto('http://jk.test/medias_compras.html');
    const botaoLoja = page.locator('#lojaBotoes button').filter({ hasText: 'JK Pecas' });
    await botaoLoja.waitFor();
    await botaoLoja.click();
    await page.locator('#btnAbaListasPedidos').click();
    await page.locator('.lista-item').first().click();
    await page.locator('#editorListaPedido:not(.hidden)').waitFor();

    const quantidade = page.locator('#tblListaPedidoItens input[data-kind="qtd"]').first();
    await quantidade.fill('3');
    await page.locator('#btnSalvarListaPedido').click();
    await page.locator('#statusListaPedido', { hasText: 'Lista salva com sucesso.' }).waitFor();
    assert.strictEqual(salvamentos.at(-1).itens[0].Quantidade, 3);

    const downloadPromise = page.waitForEvent('download');
    const baixarPromise = page.evaluate(() => baixarListaPedidoExcel('lista-1'));
    const download = await downloadPromise;
    await baixarPromise;
    assert.strictEqual(download.suggestedFilename(), 'pedido_teste.xlsx');
    assert.deepStrictEqual(statusAplicados, ['Em Orçamento']);

    const fileChooserPromise = page.waitForEvent('filechooser');
    await page.locator('#btnImportarListaPedidoExcel').click();
    const fileChooser = await fileChooserPromise;
    await fileChooser.setFiles({
      name: 'pedido.xlsx',
      mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      buffer: Buffer.from('excel-local'),
    });
    await page.locator('#statusListaPedido', { hasText: 'Lista adicionada por Excel com sucesso.' }).waitFor();
    assert.strictEqual(importacoes, 1);
    assert.strictEqual(await page.evaluate(() => listaPedidoAtual.id), 'lista-2');

    await page.locator('#btnAdicionarSkuPedido').click();
    await page.locator('#addSkuInput').fill('002');
    await page.locator('#addSkuQtdInput').fill('2');
    await page.locator('#addSkuValorInput').fill('5');
    await page.locator('#btnConfirmarAdicionarSku').click();
    await page.locator('#statusListaPedido', { hasText: 'Salvo com sucesso.' }).waitFor();
    await page.waitForFunction(() => (
      document.querySelector('#tblListaPedidoItens input[data-kind="sku"]')?.value === '002'
    ));
    assert.strictEqual(await page.locator('#tblListaPedidoItens input[data-kind="sku"]').inputValue(), '002');
    assert.deepStrictEqual(errosPagina, []);

    console.log('medias editor and import browser: OK');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
