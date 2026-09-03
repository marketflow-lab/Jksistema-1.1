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
  const pageErrors = [];
  let delayRcl = false;
  try {
    const page = await browser.newPage({ viewport: { width: 1173, height: 900 } });
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.addInitScript(() => {
      localStorage.setItem('user_data', JSON.stringify({ client_id: 'cliente-botoes' }));
    });
    await page.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      requests.push({ method: request.method(), pathname: url.pathname });

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
      const produtosMatch = url.pathname.match(/^\/api\/cadastro\/lojas\/([^/]+)\/produtos$/);
      if (produtosMatch) {
        const storeId = decodeURIComponent(produtosMatch[1]);
        if (delayRcl && storeId === 'store-rcl') await new Promise(resolve => setTimeout(resolve, 150));
        await json(route, [{ sku: `SKU-${storeId}`, nome: `Produto ${storeId}` }]);
        return;
      }
      await route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"not found"}' });
    });

    await page.goto('http://jk.local/cadastro.html', { waitUntil: 'load' });
    const botoes = page.locator('#cadastroLojaBotoes .loja-btn');
    await botoes.first().waitFor({ state: 'visible' });
    await page.waitForFunction(total => document.querySelector('#status').textContent.includes(`Produtos carregados: ${total}`), lojas.length);

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
      [{ method: 'GET', pathname: '/api/cadastro/lojas/store-rcl/produtos' }],
      'clicar RCL deve consultar somente o store_id exato',
    );

    const markerTodas = requests.length;
    await page.getByRole('button', { name: 'Todas as lojas', exact: true }).click();
    await page.waitForFunction(total => window.JKCadastro.runtime.state.storeIdSelecionado === ''
      && document.querySelector('#status').textContent.includes(`Produtos carregados: ${total}`), lojas.length);
    assert.strictEqual(
      requests.slice(markerTodas).filter(item => item.pathname.includes('/api/cadastro/lojas/')).length,
      lojas.length,
      'Todas as lojas deve consolidar todos os endpoints autorizados',
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
    await page.getByRole('button', { name: 'Carlos José', exact: true }).click();
    await page.waitForFunction(() => window.JKCadastro.runtime.state.storeIdSelecionado === 'store-carlos'
      && document.querySelector('#status').textContent.includes('Produtos carregados: 1'));
    await page.waitForTimeout(200);
    assert.strictEqual(await page.evaluate(() => window.JKCadastro.runtime.state.storeIdSelecionado), 'store-carlos', 'resposta atrasada não pode restaurar a loja anterior');
    assert.match(await page.locator('#tBody').innerText(), /Produto store-carlos/, 'resposta atrasada não pode sobrescrever a tabela atual');
    delayRcl = false;

    await page.goto('http://jk.local/cadastro.html?store_id=store-invalido', { waitUntil: 'load' });
    await page.getByRole('button', { name: 'RCL', exact: true }).waitFor({ state: 'visible' });
    assert.match(await page.locator('#status').innerText(), /Loja inválida ou indisponível/, 'URL inválida deve continuar falhando fechada');
    assert.strictEqual(await page.locator('#cadastroLojaBotoes .loja-btn').count(), lojas.length + 1, 'falha fechada não pode esconder as lojas válidas');
    await page.getByRole('button', { name: 'RCL', exact: true }).click();
    await page.waitForFunction(() => window.JKCadastro.runtime.state.storeIdSelecionado === 'store-rcl'
      && document.querySelector('#status').textContent.includes('Produtos carregados: 1'));
    assert.strictEqual(new URL(page.url()).searchParams.get('store_id'), 'store-rcl', 'seleção válida deve substituir a URL obsoleta');

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
