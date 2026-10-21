'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const staticRoot = path.resolve(__dirname, '..', 'static');
// localhost provides the secure context required by the session identity hash.
const origin = 'http://localhost:43817';
const listUrl = `${origin}/cadastro.html?store_id=store-a`;
const cacheKey = 'jk.cadastro.lista.v1';
const stores = [
  { store_id: 'store-a', nome: 'Loja A' },
  { store_id: 'store-b', nome: 'Loja B' },
];
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j1ioAAAAASUVORK5CYII=', 'base64');

function json(route, payload, status = 200) {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) });
}

function contentType(filename) {
  if (filename.endsWith('.html')) return 'text/html; charset=utf-8';
  if (filename.endsWith('.css')) return 'text/css; charset=utf-8';
  if (filename.endsWith('.js')) return 'application/javascript; charset=utf-8';
  return 'application/octet-stream';
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const requests = [];
  const pageErrors = [];
  const unexpected = [];
  let authorizedStores = [...stores];
  const products = Array.from({ length: 125 }, (_, i) => ({
    sku: `SKU-${String(i + 1).padStart(3, '0')}`,
    sku_normalizado: `SKU-${String(i + 1).padStart(3, '0')}`,
    store_id: 'store-a', loja_sync: 'Loja A',
    nome: i < 120 ? `Grupo produto ${i + 1}` : `Outro produto ${i + 1}`,
    produto_bling: `Produto Bling ${i + 1}`, foto: '',
    categoria: 'Categoria de teste', ncm: '1234.56.78', row_version: 1,
  }));
  const collectionRequests = () => requests.filter(item => item.method === 'GET'
    && item.pathname === '/api/cadastro/lojas/store-a/produtos');
  const detailRequests = () => requests.filter(item => item.method === 'GET'
    && item.pathname.startsWith('/api/cadastro/lojas/store-a/produtos/'));
  const mutationRequests = method => requests.filter(item => item.method === method);

  try {
    const page = await browser.newPage({ viewport: { width: 1180, height: 720 } });
    page.setDefaultTimeout(10000);
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.addInitScript(() => {
      if (!localStorage.getItem('user_data')) {
        localStorage.setItem('user_data', JSON.stringify({ client_id: 'client-a', username: 'user-a' }));
      }
      if (!localStorage.getItem('access_token')) localStorage.setItem('access_token', 'session-a');
    });
    await page.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      const record = { method: request.method(), pathname: url.pathname, search: url.search };
      if (['PUT', 'POST'].includes(record.method)) record.body = request.postDataJSON();
      requests.push(record);

      if (['/cadastro.html', '/cadastro_editar_item.html', '/cadastro_incluir.html'].includes(url.pathname)
        || url.pathname.startsWith('/cadastro/')) {
        const filename = path.resolve(staticRoot, url.pathname.slice(1));
        assert(filename.startsWith(staticRoot + path.sep), 'asset deve permanecer em static');
        await route.fulfill({ status: 200, contentType: contentType(filename), body: fs.readFileSync(filename) });
        return;
      }
      if (url.pathname === '/auth.js') {
        await route.fulfill({ status: 200, contentType: 'application/javascript', body: `
          window.verificarSessao = () => true;
          window.obterAuthHeaders = extra => Object.assign({
            Authorization: 'Bearer ' + localStorage.getItem('access_token')
          }, extra || {});` });
        return;
      }
      if (url.pathname === '/ncm-sync.js') {
        await route.fulfill({ status: 200, contentType: 'application/javascript',
          body: 'window.NCM_SYNC={init(){},isRunning(){return false;},async iniciarSincronizacao(){}};' });
        return;
      }
      if (['/table_columns.js', '/ia-sidebar.js'].includes(url.pathname)) {
        await route.fulfill({ status: 200, contentType: 'application/javascript', body: '' });
        return;
      }
      if (url.pathname.startsWith('/img/') || url.pathname === '/favicon.ico') {
        await route.fulfill({ status: 200, contentType: 'image/png', body: png });
        return;
      }
      if (url.pathname === '/api/lojas') return json(route, authorizedStores);
      if (['/api/cadastro/fornecedores', '/api/cadastro/nacionalizadores'].includes(url.pathname)) return json(route, []);
      if (url.pathname === '/api/cadastro/lojas/store-a/importador-loja') return json(route, {});
      if (url.pathname === '/api/cadastro/lojas/store-a/colunas') {
        return json(route, { colunas: ['sku', 'nome', 'produto_bling', 'categoria', 'foto', 'ncm'] });
      }
      if (url.pathname.startsWith('/api/cadastro/foto/')) {
        await route.fulfill({ status: 200, contentType: 'image/png', body: png });
        return;
      }
      if (url.pathname === '/api/cadastro/lojas/store-a/produtos') {
        if (request.method() === 'GET') {
          assert.strictEqual(url.searchParams.get('view'), 'summary');
          return json(route, products);
        }
        if (request.method() === 'POST') {
          const produto = { ...record.body, store_id: 'store-a', loja_sync: 'Loja A',
            sku_normalizado: record.body.sku, row_version: 1 };
          products.push(produto);
          return json(route, { success: true, sku: produto.sku, produto });
        }
      }
      const detail = /^\/api\/cadastro\/lojas\/store-a\/produtos\/(.+)$/.exec(url.pathname);
      if (detail) {
        const sku = decodeURIComponent(detail[1]);
        const index = products.findIndex(item => item.sku === sku);
        assert(index >= 0, `SKU fixture desconhecido: ${sku}`);
        if (request.method() === 'GET') return json(route, { produto: products[index] });
        if (request.method() === 'PUT') {
          assert.strictEqual(Number(record.body.row_version), products[index].row_version,
            'edição deve usar a versão confirmada pelo GET pontual');
          const produto = { ...products[index], ...record.body,
            nome: `${String(record.body.nome).trim()} [persistido]`,
            row_version: products[index].row_version + 1 };
          if (record.body.__foto_data_url) produto.foto = `cadastro_fotos/lojas/store-a/${sku}.png`;
          delete produto.__foto_data_url;
          delete produto.__foto_filename;
          products[index] = produto;
          return json(route, { success: true, sku: produto.sku, produto });
        }
      }
      unexpected.push(`${request.method()} ${url.pathname}`);
      await json(route, { detail: 'fixture route missing' }, 404);
    });

    async function listReady(total = products.length) {
      await page.waitForURL(listUrl);
      await page.waitForFunction(expected => {
        const state = window.JKCadastro?.runtime?.state;
        return state && !state.carregandoProdutos && state.produtos.length === expected
          && state.produtosEscopoCarregado !== null;
      }, total);
    }

    async function openSku(sku) {
      const before = detailRequests().length;
      // Dispatch on the row so Playwright does not change the scroll being saved.
      await page.locator(`#tBody tr[data-sku="${sku}"]`).dispatchEvent('click');
      await page.waitForURL(`${origin}/cadastro_editar_item.html?store_id=store-a&sku=${sku}`);
      await page.waitForFunction(() => document.querySelector('#status').textContent.includes('SKU carregado:'));
      assert.strictEqual(detailRequests().length, before + 1, 'abrir SKU deve buscar somente o item atual');
    }

    async function firstVisibleSku() {
      return page.locator('#tBody tr[data-sku]').first().getAttribute('data-sku');
    }

    async function returnToList() {
      await page.locator('#voltarCadastroLink').click();
      await listReady();
    }

    async function expectReloadAfterEditorChange(change, message) {
      const before = collectionRequests().length;
      await openSku(await firstVisibleSku());
      await change();
      await returnToList();
      assert.strictEqual(collectionRequests().length, before + 1, message);
    }

    await page.goto(listUrl, { waitUntil: 'load' });
    await listReady();
    assert.strictEqual(collectionRequests().length, 1, 'primeira abertura deve carregar uma coleção');
    assert.strictEqual(await page.evaluate(() => typeof window.crypto.subtle.digest), 'function');
    await page.locator('#filtro').fill('Grupo');
    await page.locator('#paginacao button').filter({ hasText: /^2$/ }).click();
    await page.evaluate(() => {
      document.querySelector('.products-table').scrollLeft = 220;
      window.scrollTo(0, 360);
    });
    const visual = await page.evaluate(() => ({
      filtro: document.querySelector('#filtro').value,
      pagina: window.JKCadastro.runtime.state.paginaAtual,
      scrollY: window.scrollY,
      tabelaScrollLeft: document.querySelector('.products-table').scrollLeft,
    }));
    assert(visual.scrollY > 100 && visual.tabelaScrollLeft > 100, 'fixture deve exercitar rolagem real');

    await openSku('SKU-055');
    assert.strictEqual(collectionRequests().length, 1, 'entrar no SKU não deve buscar a coleção');
    await page.locator('[name="nome"]').fill('Grupo rascunho descartado');
    await returnToList();
    assert.strictEqual(collectionRequests().length, 1, 'voltar sem salvar deve reutilizar a lista');
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
    const restored = await page.evaluate(() => ({
      filtro: document.querySelector('#filtro').value,
      pagina: window.JKCadastro.runtime.state.paginaAtual,
      scrollY: window.scrollY,
      tabelaScrollLeft: document.querySelector('.products-table').scrollLeft,
    }));
    assert.strictEqual(restored.filtro, visual.filtro, 'retorno deve preservar filtro');
    assert.strictEqual(restored.pagina, visual.pagina, 'retorno deve preservar página');
    assert(Math.abs(restored.scrollY - visual.scrollY) <= 2, 'retorno deve preservar rolagem vertical');
    assert(Math.abs(restored.tabelaScrollLeft - visual.tabelaScrollLeft) <= 2, 'retorno deve preservar colunas visíveis');
    assert.doesNotMatch(await page.locator('#tBody').innerText(), /rascunho descartado/);

    await openSku('SKU-055');
    await page.locator('[name="nome"]').fill('Grupo alterado');
    await page.locator('#formEditarItem button[type="submit"]').click();
    await listReady();
    assert.strictEqual(collectionRequests().length, 1, 'salvar item deve aplicar o retorno do PUT sem buscar coleção');
    assert.strictEqual(mutationRequests('PUT').length, 1);
    assert.strictEqual(await page.evaluate(() => window.JKCadastro.runtime.state.produtos
      .find(item => item.sku === 'SKU-055').nome), 'Grupo alterado [persistido]',
    'lista deve usar o produto persistido retornado pelo servidor');
    assert.strictEqual(await page.locator('#filtro').inputValue(), 'Grupo');
    assert.strictEqual(await page.evaluate(() => window.JKCadastro.runtime.state.paginaAtual), 2);

    let before = collectionRequests().length;
    await Promise.all([
      page.waitForResponse(response => new URL(response.url()).pathname === '/api/cadastro/lojas/store-a/produtos'),
      page.locator('#btnAtualizar').click(),
    ]);
    await listReady();
    assert.strictEqual(collectionRequests().length, before + 1, 'Atualizar lista deve forçar rede');

    await expectReloadAfterEditorChange(async () => {
      await page.evaluate(key => {
        const envelope = JSON.parse(sessionStorage.getItem(key));
        envelope.atualizadoEm = Date.now() - 6 * 60 * 1000;
        sessionStorage.setItem(key, JSON.stringify(envelope));
      }, cacheKey);
    }, 'cache expirado deve voltar à rede');
    await expectReloadAfterEditorChange(async () => {
      await page.evaluate(key => sessionStorage.setItem(key, '{invalido'), cacheKey);
    }, 'cache inválido deve voltar à rede');
    await expectReloadAfterEditorChange(async () => {
      await page.evaluate(() => localStorage.setItem('access_token', 'session-b'));
    }, 'outra sessão não pode reutilizar snapshot anterior');
    await expectReloadAfterEditorChange(async () => {
      await page.evaluate(() => localStorage.setItem('user_data', JSON.stringify({ client_id: 'client-b', username: 'user-b' })));
    }, 'outro cliente não pode reutilizar snapshot anterior');
    await expectReloadAfterEditorChange(async () => {
      authorizedStores = [stores[0]];
    }, 'alteração das lojas autorizadas deve impedir reuso');

    before = collectionRequests().length;
    await openSku(await firstVisibleSku());
    await page.locator('#fotoFile').setInputFiles({ name: 'foto.png', mimeType: 'image/png', buffer: png });
    await page.locator('#btnEnviarFoto').click();
    await page.waitForFunction(() => document.querySelector('#status').textContent.includes('Imagem anexada'));
    await page.locator('#formEditarItem button[type="submit"]').click();
    await listReady();
    assert.strictEqual(collectionRequests().length, before + 1,
      'upload de foto deve invalidar lista por afetar grupo de lojas compartilhado');
    assert(mutationRequests('PUT').at(-1).body.__foto_data_url.startsWith('data:image/png;base64,'));

    before = collectionRequests().length;
    await page.locator('#btnIncluirCadastro').click();
    await page.waitForURL(`${origin}/cadastro_incluir.html?store_id=store-a`);
    await page.waitForFunction(() => document.querySelector('#status').textContent.includes('Preencha os dados'));
    await page.locator('[name="sku"]').fill('SKU-NOVO');
    await page.locator('[name="nome"]').fill('Grupo novo');
    await page.locator('#formIncluir button[type="submit"]').click();
    await page.waitForFunction(() => document.querySelector('#status').textContent.includes('SKU incluído com sucesso'));
    assert.strictEqual(mutationRequests('POST').length, 1);
    await returnToList();
    assert.strictEqual(collectionRequests().length, before + 1, 'inclusão deve invalidar lista anterior');
    assert(await page.evaluate(() => window.JKCadastro.runtime.state.produtos.some(item => item.sku === 'SKU-NOVO')));

    before = collectionRequests().length;
    products[0].nome = 'Grupo recebido por sincronização';
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('jk:machine-sync-updated', {
      detail: { received_scopes: ['cadastro'] },
    })));
    await page.waitForFunction(() => window.JKCadastro.runtime.state.produtos
      .some(item => item.nome === 'Grupo recebido por sincronização'));
    assert.strictEqual(collectionRequests().length, before + 1, 'recebimento por sync deve invalidar e buscar lista atual');
    await openSku(await firstVisibleSku());
    await returnToList();
    assert.strictEqual(collectionRequests().length, before + 1, 'snapshot posterior ao sync pode ser reutilizado');

    const cacheText = await page.evaluate(key => sessionStorage.getItem(key), cacheKey);
    assert(cacheText && !cacheText.includes('session-b'), 'snapshot não pode armazenar token de sessão');
    assert.deepStrictEqual(pageErrors, [], 'fluxos não podem gerar erros JavaScript');
    assert.deepStrictEqual(unexpected, [], 'todas as consultas devem usar as rotas esperadas');
    console.log('cadastro list reuse browser: OK (cancelar, salvar, visual, refresh, validade, identidade, foto, inclusão e sync)');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
