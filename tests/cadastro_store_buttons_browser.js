'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const staticRoot = path.join(root, 'static');
const html = fs.readFileSync(path.join(staticRoot, 'cadastro.html'), 'utf8');
const lojas = [
  { store_id: 'store-jk', nome: 'JK Peças' },
  { store_id: 'store-uai', nome: 'Uai Mineirinho' },
  { store_id: 'store-carlos', nome: 'Carlos José' },
  { store_id: 'store-deckas', nome: 'Deckas' },
  { store_id: 'store-leri', nome: 'Leri' },
  { store_id: 'store-rcl', nome: 'RCL' },
  { store_id: 'store-long', nome: 'Loja com nome extremamente longo para validar a quebra responsiva' },
];

function json(route, payload, status = 200) {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) });
}

function contentType(filePath) {
  if (filePath.endsWith('.css')) return 'text/css; charset=utf-8';
  if (filePath.endsWith('.js')) return 'application/javascript; charset=utf-8';
  return 'application/octet-stream';
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const requests = [];
  const failedRequests = [];
  const pageErrors = [];
  let delayRcl = false;
  let failNextRcl = false;
  let failNextBulk = false;
  let invalidNextBulk = false;
  let allErrorNextBulk = false;
  let partialNextBulk = false;
  let nextRclResponse = '';
  try {
    const page = await browser.newPage({ viewport: { width: 1173, height: 900 } });
    page.on('pageerror', error => pageErrors.push(error.message));
    page.on('requestfailed', request => failedRequests.push({
      pathname: new URL(request.url()).pathname,
      errorText: request.failure() && request.failure().errorText,
    }));
    await page.addInitScript(() => {
      localStorage.setItem('user_data', JSON.stringify({ client_id: 'cliente-botoes' }));
      const nativeSetTimeout = window.setTimeout.bind(window);
      window.setTimeout = (callback, delay, ...args) => nativeSetTimeout(
        callback,
        Number(delay) === 30000 ? 25 : delay,
        ...args,
      );
    });
    await page.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      requests.push({ method: request.method(), pathname: url.pathname, search: url.search });

      if (url.pathname === '/cadastro.html') {
        await route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: html });
        return;
      }
      if (url.pathname.startsWith('/cadastro/')) {
        const relative = url.pathname.replace(/^\/+/, '');
        const target = path.resolve(staticRoot, relative);
        assert(target.startsWith(staticRoot + path.sep), 'asset deve permanecer dentro de static');
        await route.fulfill({ status: 200, contentType: contentType(target), body: fs.readFileSync(target) });
        return;
      }
      if (url.pathname === '/auth.js') {
        await route.fulfill({
          status: 200,
          contentType: 'application/javascript; charset=utf-8',
          body: 'window.obterAuthHeaders = extra => Object.assign({}, extra || {});',
        });
        return;
      }
      if (url.pathname === '/ncm-sync.js') {
        await route.fulfill({
          status: 200,
          contentType: 'application/javascript; charset=utf-8',
          body: 'window.NCM_SYNC = { init() {}, isRunning() { return false; }, async iniciarSincronizacao() {} };',
        });
        return;
      }
      if (url.pathname === '/table_columns.js' || url.pathname === '/ia-sidebar.js') {
        await route.fulfill({ status: 200, contentType: 'application/javascript; charset=utf-8', body: '' });
        return;
      }
      if (url.pathname === '/api/lojas') {
        await json(route, lojas);
        return;
      }
      if (url.pathname === '/api/cadastro/fornecedores') {
        await json(route, []);
        return;
      }
      if (url.pathname === '/api/cadastro/lojas/produtos') {
        assert.strictEqual(url.searchParams.get('view'), 'summary', 'visão consolidada deve pedir somente o resumo');
        if (allErrorNextBulk) {
          allErrorNextBulk = false;
          await json(route, {
            produtos: [],
            lojas: lojas.map(loja => ({ store_id: loja.store_id, loja_sync: loja.nome, status: 'error', total: 0 })),
            total: 0,
            partial: true,
          });
          return;
        }
        if (invalidNextBulk) {
          invalidNextBulk = false;
          await json(route, { produtos: [], lojas: [], total: 1, partial: 'não' });
          return;
        }
        if (failNextBulk) {
          failNextBulk = false;
          await json(route, { detail: { code: 'cadastro_stores_unavailable' } }, 503);
          return;
        }
        const partial = partialNextBulk;
        partialNextBulk = false;
        const available = partial ? lojas.filter(loja => loja.store_id !== 'store-deckas') : lojas;
        await json(route, {
          produtos: available.map(loja => ({
            sku: `SKU-${loja.store_id}`,
            nome: `Produto ${loja.store_id}`,
            store_id: loja.store_id,
            loja_sync: loja.nome,
          })),
          lojas: lojas.map(loja => ({
            store_id: loja.store_id,
            loja_sync: loja.nome,
            status: partial && loja.store_id === 'store-deckas' ? 'error' : 'ok',
            total: partial && loja.store_id === 'store-deckas' ? 0 : 1,
            ...(partial && loja.store_id === 'store-deckas' ? { erro_codigo: 'store_projection_failed' } : {}),
          })),
          total: available.length,
          partial,
        });
        return;
      }
      const produtosMatch = url.pathname.match(/^\/api\/cadastro\/lojas\/([^/]+)\/produtos$/);
      if (produtosMatch) {
        const storeId = decodeURIComponent(produtosMatch[1]);
        assert.strictEqual(url.searchParams.get('view'), 'summary', 'visão de loja deve pedir somente o resumo');
        const responseMode = nextRclResponse;
        nextRclResponse = '';
        if (storeId === 'store-rcl' && responseMode === 'empty') {
          await route.fulfill({ status: 200, contentType: 'application/json', body: '' });
          return;
        }
        if (storeId === 'store-rcl' && responseMode === 'html') {
          await route.fulfill({ status: 500, contentType: 'text/html', body: '<html>erro interno</html>' });
          return;
        }
        if (storeId === 'store-rcl' && responseMode === 'truncated') {
          await route.fulfill({ status: 200, contentType: 'application/json', body: '{"produtos":[' });
          return;
        }
        if (storeId === 'store-rcl' && ['401', '403'].includes(responseMode)) {
          await json(route, { detail: { code: 'auth_denied' } }, Number(responseMode));
          return;
        }
        if (storeId === 'store-rcl' && responseMode === 'timeout') {
          await new Promise(resolve => setTimeout(resolve, 150));
          try { await json(route, [{ sku: 'LATE', nome: 'Resposta fora do prazo' }]); } catch (_aborted) {}
          return;
        }
        if (failNextRcl && storeId === 'store-rcl') {
          failNextRcl = false;
          await json(route, { detail: 'falha controlada' }, 500);
          return;
        }
        if (delayRcl && storeId === 'store-rcl') await new Promise(resolve => setTimeout(resolve, 150));
        try {
          await json(route, [{ sku: `SKU-${storeId}`, nome: `Produto ${storeId}` }]);
        } catch (error) {
          if (!(delayRcl && storeId === 'store-rcl')) throw error;
        }
        return;
      }
      await route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"not found"}' });
    });

    await page.goto('http://jk.local/cadastro.html', { waitUntil: 'load' });
    const botoes = page.locator('#cadastroLojaBotoes .loja-btn');
    await botoes.first().waitFor({ state: 'visible' });
    await page.waitForFunction(total => document.querySelector('#status').textContent.includes(`Produtos carregados: ${total}`), lojas.length);
    assert.deepStrictEqual(
      requests.filter(item => item.pathname.includes('/api/cadastro/lojas/')),
      [{ method: 'GET', pathname: '/api/cadastro/lojas/produtos', search: '?view=summary' }],
      'a abertura consolidada deve fazer uma única requisição resumida',
    );

    assert.strictEqual(await botoes.count(), lojas.length + 1, 'todas as lojas devem ter botão visível');
    const rotulos = await botoes.allTextContents();
    assert.deepStrictEqual(new Set(rotulos), new Set(['Todas as lojas', ...lojas.map(loja => loja.nome)]));
    assert(await page.getByRole('button', { name: 'RCL', exact: true }).isVisible(), 'RCL deve aparecer no Cadastro');
    assert.strictEqual(await page.getByRole('button', { name: 'Todas as lojas', exact: true }).getAttribute('aria-pressed'), 'true');

    const desktopLayout = await page.locator('#cadastroLojaBotoes').evaluate(group => ({
      clientWidth: group.clientWidth,
      scrollWidth: group.scrollWidth,
      children: Array.from(group.children, element => {
        const rect = element.getBoundingClientRect();
        return { left: rect.left, right: rect.right, width: rect.width };
      }),
    }));
    assert(desktopLayout.children.every(item => item.width > 0), 'nenhum botão pode ser ocultado');
    assert(desktopLayout.scrollWidth <= desktopLayout.clientWidth + 1, 'grupo não deve recortar lojas no desktop');

    const markerRcl = requests.length;
    await page.getByRole('button', { name: 'RCL', exact: true }).click();
    await page.waitForFunction(() => window.JKCadastro.runtime.state.storeIdSelecionado === 'store-rcl'
      && document.querySelector('#status').textContent.includes('Produtos carregados: 1'));
    assert.strictEqual(await page.locator('#cadastroLojaSelect').inputValue(), 'store-rcl', 'select oculto deve acompanhar o botão');
    assert.strictEqual(await page.getByRole('button', { name: 'RCL', exact: true }).getAttribute('aria-pressed'), 'true');
    assert.strictEqual(await page.locator('#cadastroLojaBotoes .loja-btn.active').count(), 1, 'somente uma loja deve ficar ativa');
    assert.match(await page.locator('#tBody').innerText(), /Produto store-rcl/);
    assert.deepStrictEqual(
      requests.slice(markerRcl).filter(item => item.pathname.includes('/api/cadastro/lojas/')),
      [{ method: 'GET', pathname: '/api/cadastro/lojas/store-rcl/produtos', search: '?view=summary' }],
      'clicar RCL deve consultar somente o store_id exato',
    );

    failNextRcl = true;
    await page.locator('#btnAtualizar').click();
    await page.waitForFunction(() => document.querySelector('#status').textContent.includes('Erro ao carregar produtos'));
    assert.match(await page.locator('#tBody').innerText(), /Produto store-rcl/, 'falha ao atualizar o mesmo escopo deve preservar a tabela útil');
    assert.strictEqual(await page.evaluate(() => window.JKCadastro.runtime.state.produtos.length), 1);
    await page.locator('#btnAtualizar').click();
    await page.waitForFunction(() => document.querySelector('#status').textContent.includes('Produtos carregados: 1'));

    for (const scenario of [
      ['empty', 'resposta vazia'],
      ['html', 'resposta inválida'],
      ['truncated', 'resposta inválida'],
      ['401', 'Falha HTTP 401'],
      ['403', 'Falha HTTP 403'],
      ['timeout', 'limite de 30 segundos'],
    ]) {
      const [mode, expectedMessage] = scenario;
      nextRclResponse = mode;
      await page.locator('#btnAtualizar').click();
      await page.waitForFunction(
        expected => document.querySelector('#status').textContent.toLowerCase().includes(expected),
        expectedMessage.toLowerCase(),
      );
      assert.match(
        await page.locator('#tBody').innerText(),
        /Produto store-rcl/,
        `${mode}: refresh inválido do mesmo escopo deve preservar a tabela`,
      );
      assert.strictEqual(await page.evaluate(() => window.JKCadastro.runtime.state.produtos.length), 1);
      await page.locator('#btnAtualizar').click();
      await page.waitForFunction(() => document.querySelector('#status').textContent.includes('Produtos carregados: 1'));
    }

    const markerTodas = requests.length;
    await page.getByRole('button', { name: 'Todas as lojas', exact: true }).click();
    await page.waitForFunction(total => window.JKCadastro.runtime.state.storeIdSelecionado === ''
      && document.querySelector('#status').textContent.includes(`Produtos carregados: ${total}`), lojas.length);
    assert.deepStrictEqual(
      requests.slice(markerTodas).filter(item => item.pathname.includes('/api/cadastro/lojas/')),
      [{ method: 'GET', pathname: '/api/cadastro/lojas/produtos', search: '?view=summary' }],
      'Todas as lojas deve usar uma única leitura consolidada',
    );

    partialNextBulk = true;
    await page.locator('#btnAtualizar').click();
    await page.waitForFunction(total => window.JKCadastro.runtime.state.produtos.length === total - 1, lojas.length);
    assert.match(await page.locator('#status').innerText(), /parcial/i, 'resposta parcial deve ficar explícita');
    assert.doesNotMatch(await page.locator('#tBody').innerText(), /Produto store-deckas/);
    failNextBulk = true;
    await page.locator('#btnAtualizar').click();
    await page.waitForFunction(() => document.querySelector('#status').textContent.includes('Erro ao carregar produtos'));
    assert.strictEqual(
      await page.evaluate(() => window.JKCadastro.runtime.state.produtos.length),
      lojas.length - 1,
      'falha ao atualizar Todas deve preservar o último resultado útil',
    );
    await page.locator('#btnAtualizar').click();
    await page.waitForFunction(total => window.JKCadastro.runtime.state.produtos.length === total, lojas.length);
    invalidNextBulk = true;
    await page.locator('#btnAtualizar').click();
    await page.waitForFunction(() => document.querySelector('#status').textContent.includes('formato inválido'));
    assert.strictEqual(
      await page.evaluate(() => window.JKCadastro.runtime.state.produtos.length),
      lojas.length,
      'envelope consolidado inválido deve preservar o último resultado útil',
    );
    allErrorNextBulk = true;
    await page.locator('#btnAtualizar').click();
    await page.waitForFunction(() => document.querySelector('#status').textContent.includes('Nenhuma loja pôde ser carregada'));
    assert.strictEqual(
      await page.evaluate(() => window.JKCadastro.runtime.state.produtos.length),
      lojas.length,
      'resposta 200 com falha em todas as lojas deve ser tratada como erro total',
    );

    await page.setViewportSize({ width: 390, height: 900 });
    const mobileLayout = await page.locator('#cadastroLojaBotoes').evaluate(group => {
      const groupRect = group.getBoundingClientRect();
      const children = Array.from(group.children, element => {
        const rect = element.getBoundingClientRect();
        return { top: Math.round(rect.top), left: rect.left, right: rect.right, width: rect.width };
      });
      return { groupRight: groupRect.right, clientWidth: group.clientWidth, scrollWidth: group.scrollWidth, children };
    });
    assert.strictEqual(new Set(mobileLayout.children.map(item => item.top)).size > 1, true, 'botões devem quebrar linha no celular');
    assert(mobileLayout.children.every(item => item.width > 0 && item.right <= mobileLayout.groupRight + 1), 'todas as lojas devem permanecer dentro do grupo no celular');
    assert(mobileLayout.scrollWidth <= mobileLayout.clientWidth + 1, 'grupo não deve exigir rolagem horizontal no celular');
    assert.strictEqual(await botoes.count(), lojas.length + 1, 'nenhuma loja pode sumir após o reflow');

    delayRcl = true;
    await page.getByRole('button', { name: 'RCL', exact: true }).click();
    await page.waitForFunction(() => window.JKCadastro.runtime.state.storeIdSelecionado === 'store-rcl'
      && window.JKCadastro.runtime.state.produtos.length === 0);
    assert.strictEqual(await page.locator('#painelProdutos').getAttribute('aria-busy'), 'true');
    assert.deepStrictEqual(
      await page.evaluate(() => window.JKCadastro.runtime.state.carregamentoProdutosProgresso),
      { concluidas: 0, total: 1 },
      'carregamento deve expor progresso do escopo atual',
    );
    await page.getByRole('button', { name: 'Carlos José', exact: true }).click();
    await page.waitForFunction(() => window.JKCadastro.runtime.state.storeIdSelecionado === 'store-carlos'
      && document.querySelector('#status').textContent.includes('Produtos carregados: 1'));
    await page.waitForTimeout(200);
    assert.strictEqual(await page.evaluate(() => window.JKCadastro.runtime.state.storeIdSelecionado), 'store-carlos', 'resposta atrasada não pode restaurar a loja anterior');
    assert.strictEqual(await page.locator('#painelProdutos').getAttribute('aria-busy'), 'false');
    assert.match(await page.locator('#tBody').innerText(), /Produto store-carlos/, 'resposta atrasada não pode sobrescrever a tabela atual');
    assert(
      failedRequests.every(item => !item.errorText || /abort|cancel/i.test(item.errorText)),
      'cancelamentos de troca rápida não podem virar falhas de rede inesperadas',
    );
    delayRcl = false;

    await page.goto('http://jk.local/cadastro.html?store_id=store-invalido', { waitUntil: 'load' });
    await page.getByRole('button', { name: 'RCL', exact: true }).waitFor({ state: 'visible' });
    assert.match(await page.locator('#status').innerText(), /Loja inválida ou indisponível/, 'URL inválida deve continuar falhando fechada');
    assert.strictEqual(await page.locator('#cadastroLojaBotoes .loja-btn').count(), lojas.length + 1, 'falha fechada não pode esconder as lojas válidas');
    await page.getByRole('button', { name: 'RCL', exact: true }).click();
    await page.waitForFunction(() => window.JKCadastro.runtime.state.storeIdSelecionado === 'store-rcl'
      && document.querySelector('#status').textContent.includes('Produtos carregados: 1'));
    assert.strictEqual(new URL(page.url()).searchParams.get('store_id'), 'store-rcl', 'seleção válida deve substituir a URL obsoleta');

    await page.evaluate(() => {
      const state = window.JKCadastro.runtime.state;
      state.produtos = Array.from({ length: 120 }, (_, i) => ({ sku: String(i), store_id: state.storeIdSelecionado, nome: 'Synthetic' }));
      window.JKCadastro.produtosTabela.renderTabela();
    });
    assert((await page.locator('#paginacao button').count()) > 0);
    await page.evaluate(() => {
      window.JKCadastro.runtime.state.produtos = [];
      window.JKCadastro.produtosTabela.renderTabela();
    });
    assert.strictEqual(await page.locator('#paginacao button').count(), 0, 'lista vazia deve limpar paginação');
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('jk:machine-sync-updated', { detail: { received_scopes: ['cadastro'] } })));
    await page.waitForFunction(() => window.JKCadastro.runtime.state.produtos.length === 1);
    assert.match(await page.locator('#tBody').innerText(), /Produto store-rcl/);

    assert.deepStrictEqual(pageErrors, [], 'a tela não deve emitir erros JavaScript');
    assert.strictEqual(requests.some(item => ['POST', 'PUT', 'PATCH', 'DELETE'].includes(item.method)), false, 'trocar loja deve ser somente leitura');
    console.log('cadastro store buttons browser: OK');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
