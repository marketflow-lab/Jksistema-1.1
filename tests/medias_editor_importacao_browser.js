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
  const storeId = 'store-jk';
  const listas = [{
    id: 'lista-1',
    nome_lista: 'Pedido inicial',
    loja: 'JK Pecas',
    store_id: storeId,
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
  const skusConsultados = [];
  const inclusoesSku = [];
  const statusAplicados = [];
  let importacoes = 0;
  let produtosFallback = [];
  let respostaAdicionarSku = null;

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
      if (url.pathname.startsWith('/medias_compras/') || url.pathname === '/cache-persistente.js') {
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
        await json(route, [{ nome: 'JK Pecas', store_id: storeId }]);
        return;
      }
      if (url.pathname === '/api/cadastro/fornecedores') {
        await json(route, [{ id: 'fornecedor-teste', nome_empresa: 'Fornecedor teste' }]);
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
        const sku = url.searchParams.get('sku');
        skusConsultados.push(sku);
        produtosFallback = [];
        if (sku === 'FALLBACK-OK') produtosFallback = [{ sku }];
        if (sku === 'ZX-9') produtosFallback = [{ sku: 'ZX9' }];
        if (sku === 'QX9') produtosFallback = [{ sku: 'QX-9' }];
        if (sku === 'SEM-CADASTRO' || produtosFallback.length) {
          await json(route, { detail: produtosFallback.length ? 'Not Found' : 'SKU não encontrado no cadastro.' }, 404);
          return;
        }
        await json(route, {
          produto: {
            sku: sku === '1' ? '001' : (sku === '001-JAN' ? '1-1' : sku.toUpperCase()),
            ...(['1', '001-JAN'].includes(sku) ? { cubagem_sku: sku } : {}),
            titulo: 'Produto adicionado',
            OEM: 'OEM-2',
          },
        });
        return;
      }
      if (url.pathname === '/api/cadastro/produtos') {
        await json(route, produtosFallback);
        return;
      }
      if (url.pathname === '/api/medias-compras/listas-pedidos/importar-excel' && method === 'POST') {
        assert.match(request.postDataBuffer().toString('utf8'), /name="fornecedor_id"\r\n\r\nfornecedor-teste/);
        importacoes += 1;
        const criada = {
          id: 'lista-2',
          nome_lista: 'Pedido importado',
          loja: 'JK Pecas',
          store_id: storeId,
          status: 'Lista gerada',
          fornecedor_id: 'fornecedor-teste',
          supplier: 'Fornecedor teste',
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
        if (respostaAdicionarSku) {
          await json(route, { detail: respostaAdicionarSku.detail }, respostaAdicionarSku.status);
          return;
        }
        if (['SEM-CADASTRO', 'ZX-9', 'QX9'].includes(payload.sku)) {
          await json(route, { detail: 'SKU não encontrado no cadastro.' }, 404);
          return;
        }
        inclusoesSku.push(payload);
        const skuCanonico = payload.sku.toUpperCase();
        const item = {
          SKU: skuCanonico,
          'Título do produto em inglês': 'Produto adicionado',
          Quantidade: payload.quantidade,
          'Valor unidade': payload.valor_unitario,
          'Valor total': payload.quantidade * payload.valor_unitario,
        };
        const index = lista.itens.findIndex(item => item.SKU === skuCanonico);
        if (index >= 0) lista.itens[index] = item;
        else lista.itens.push(item);
        await json(route, { lista, sku: skuCanonico, acao: index >= 0 ? 'atualizado' : 'adicionado' });
        return;
      }
      if (url.pathname === '/api/medias-compras/listas-pedidos' && method === 'GET') {
        await json(route, {
          listas: listas.map(lista => ({
            id: lista.id,
            nome_lista: lista.nome_lista,
            loja: lista.loja,
            store_id: lista.store_id,
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
    await page.waitForFunction(() => {
      const select = document.getElementById('jkFornecedorListaSelect');
      return select && !select.disabled;
    });
    await page.locator('#jkFornecedorListaSelect').selectOption('fornecedor-teste');
    await page.locator('#jkFornecedorListaConfirmar').click();
    const fileChooser = await fileChooserPromise;
    await fileChooser.setFiles({
      name: 'pedido.xlsx',
      mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      buffer: Buffer.from('excel-local'),
    });
    await page.locator('#statusListaPedido', { hasText: 'Lista adicionada por Excel com sucesso.' }).waitFor();
    assert.strictEqual(importacoes, 1);
    assert.strictEqual(await page.evaluate(() => listaPedidoAtual.id), 'lista-2');

    const casosSku = [
      ['002', 2, 5],
      ['1', 2, 3],
      ['001-JAN', 2, 4],
      ['493-1', 30, 6],
      ['4931', 4, 3],
      ['FALLBACK-OK', 2, 4],
      ['ABC', 3, 2.5],
      ['A1B2', 4, 3],
      ['001-A', 1, 10],
      ['297-K', 2, 3.5],
      ['mIx9', 2, 7],
    ];
    async function adicionarSku(sku, qtd, valor) {
      await page.locator('#btnAdicionarSkuPedido').click();
      await page.waitForFunction(() => document.activeElement === document.getElementById('addSkuInput'));
      await page.locator('#addSkuInput').fill(sku);
      await page.locator('#addSkuQtdInput').fill(String(qtd));
      await page.locator('#addSkuValorInput').fill(String(valor));
      await page.locator('#btnConfirmarAdicionarSku').click();
    }
    for (const [sku, qtd, valor] of casosSku) {
      await adicionarSku(sku, qtd, valor);
      await page.locator('#modalAdicionarSkuPedido').waitFor({ state: 'hidden' });
      const skuCadastro = sku.toUpperCase();
      assert.deepStrictEqual(skusConsultados, [], 'The modern endpoint validates the raw SKU without a duplicate browser lookup');
      assert.deepStrictEqual(inclusoesSku.at(-1), { sku, quantidade: qtd, valor_unitario: valor });
      const item = listas.find(item => item.id === 'lista-2').itens.find(item => item.SKU === skuCadastro);
      assert.strictEqual(item['Valor total'], qtd * valor);
      const destacado = page.locator('#tblListaPedidoItens tbody tr').filter({
        has: page.locator(`input[data-kind="sku"][value="${skuCadastro}"]`),
      });
      assert.notStrictEqual(await destacado.evaluate(row => row.style.outline), '');
    }

    await adicionarSku('493-1', 4, 8);
    await page.locator('#modalAdicionarSkuPedido').waitFor({ state: 'hidden' });
    assert.strictEqual(listas.find(item => item.id === 'lista-2').itens.length, casosSku.length);
    assert.strictEqual(listas.find(item => item.id === 'lista-2').itens.find(item => item.SKU === '493-1').Quantidade, 4);

    await page.reload();
    await page.locator('#lojaBotoes button').filter({ hasText: 'JK Pecas' }).click();
    await page.locator('#btnAbaListasPedidos').click();
    await page.locator('.lista-item').filter({ hasText: 'Pedido importado' }).click();
    await page.locator('#editorListaPedido:not(.hidden)').waitFor();
    const skusReabertos = await page.locator('#tblListaPedidoItens input[data-kind="sku"]').evaluateAll(inputs => inputs.map(input => input.value));
    assert.deepStrictEqual(skusReabertos.sort(), casosSku.map(([sku]) => sku.toUpperCase()).sort());

    const consultasAntes = skusConsultados.length;
    const inclusoesAntes = inclusoesSku.length;
    for (const [sku, qtd, valor, mensagem] of [
      ['', 1, 5, 'Informe o SKU.'],
      ['ABC', 0, 5, 'Informe uma quantidade válida (maior que zero).'],
      ['ABC', 1, 0, 'Informe um valor unitário válido (maior que zero).'],
    ]) {
      await adicionarSku(sku, qtd, valor);
      assert.strictEqual(await page.locator('#modalAdicionarSkuStatus').textContent(), mensagem);
      await page.locator('#modalAdicionarSkuPedido [data-jk-action="fechar-adicionar-sku"]').click();
    }
    assert.strictEqual(skusConsultados.length, consultasAntes);
    assert.strictEqual(inclusoesSku.length, inclusoesAntes);

    for (const sku of ['SEM-CADASTRO', 'ZX-9', 'QX9']) {
      await adicionarSku(sku, 1, 5);
      await page.locator('#modalAdicionarSkuStatus', { hasText: 'SKU não encontrado no cadastro.' }).waitFor();
      assert.strictEqual(inclusoesSku.length, inclusoesAntes);
      assert.strictEqual(await page.locator('#btnConfirmarAdicionarSku').isEnabled(), true);
      await page.locator('#modalAvisoSkuCentral [data-jk-action="fechar-aviso"]').click();
      await page.locator('#modalAdicionarSkuPedido [data-jk-action="fechar-adicionar-sku"]').click();
    }

    const salvamentosAntes = salvamentos.length;
    for (const status of [400, 404, 409, 422, 500]) {
      respostaAdicionarSku = { status, detail: 'SKU recusado pelo servidor.' };
      await adicionarSku('RECUSADO-01', 1, 5);
      await page.locator('#modalAdicionarSkuStatus', { hasText: respostaAdicionarSku.detail }).waitFor();
      assert.strictEqual(salvamentos.length, salvamentosAntes);
      assert.strictEqual(listas.find(item => item.id === 'lista-2').itens.length, casosSku.length);
      await page.locator('#modalAvisoSkuCentral [data-jk-action="fechar-aviso"]').click();
      await page.locator('#modalAdicionarSkuPedido [data-jk-action="fechar-adicionar-sku"]').click();
    }

    respostaAdicionarSku = { status: 404, detail: 'Not Found' };
    await adicionarSku('ROTA-LEGADA', 2, 5);
    await page.locator('#modalAdicionarSkuPedido').waitFor({ state: 'hidden' });
    assert.strictEqual(salvamentos.length, salvamentosAntes + 1);
    assert.strictEqual(skusConsultados.at(-1), 'ROTA-LEGADA');
    assert.strictEqual(listas.find(item => item.id === 'lista-2').itens.find(item => item.SKU === 'ROTA-LEGADA').Quantidade, 2);
    // Only missing routes may use the old complete-catalog path; exact SKU identity still applies there.
    await adicionarSku('FALLBACK-OK', 3, 6);
    await page.locator('#modalAdicionarSkuPedido').waitFor({ state: 'hidden' });
    assert.strictEqual(salvamentos.length, salvamentosAntes + 2);
    assert.strictEqual(listas.find(item => item.id === 'lista-2').itens.find(item => item.SKU === 'FALLBACK-OK').Quantidade, 3);
    for (const sku of ['ZX-9', 'QX9']) {
      await adicionarSku(sku, 1, 5);
      await page.locator('#modalAdicionarSkuStatus', { hasText: 'SKU não encontrado no cadastro.' }).waitFor();
      assert.strictEqual(salvamentos.length, salvamentosAntes + 2, 'A different hyphenated SKU cannot authorize the legacy PUT');
      await page.locator('#modalAvisoSkuCentral [data-jk-action="fechar-aviso"]').click();
      await page.locator('#modalAdicionarSkuPedido [data-jk-action="fechar-adicionar-sku"]').click();
    }
    respostaAdicionarSku = null;
    assert.deepStrictEqual(errosPagina, []);

    console.log('medias editor and import browser: OK');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
