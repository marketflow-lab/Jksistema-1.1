'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const staticRoot = path.join(root, 'static');
const html = fs.readFileSync(path.join(staticRoot, 'perguntas_pos_venda.html'), 'utf8');
const lojas = [
  { store_id: 'store-alpha', nome: 'Loja Alpha', mercadolivre_conectado: true },
  { store_id: 'store-beta', nome: 'Loja Beta', mercadolivre_conectado: true },
];
const trainingByStore = {
  'store-alpha': {
    orientacoes_perguntas: 'Responder de forma cordial e objetiva.',
    contexto_loja: 'Postagem em até um dia útil.',
    compatibilidade_autopecas: 'Confirmar modelo, motor e ano.',
    proibicoes: 'Não inventar código OEM.',
    notas_sku: { '001': 'Aplicação confirmada somente no modelo Alpha.' },
    exemplos: { perguntas_anuncio: [] },
    profile_scope: 'store',
  },
  'store-beta': {
    orientacoes_perguntas: 'Usar a saudação da Loja Beta.',
    contexto_loja: '',
    compatibilidade_autopecas: '',
    proibicoes: '',
    notas_sku: {},
    exemplos: { perguntas_anuncio: [] },
    profile_scope: 'store',
  },
};
const productsByStore = {
  'store-alpha': [
    { sku: '001', nome: 'Produto Alpha 1', marca: 'Marca A' },
    { sku: '002', nome: 'Produto Alpha 2', marca: 'Marca A' },
    { sku: '003', nome: 'Produto Alpha 3', marca: 'Marca B' },
  ],
  'store-beta': [{ sku: '901', nome: 'Produto exclusivo Beta', marca: 'Marca B' }],
};

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
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.addInitScript(() => {
      localStorage.setItem('permissions', JSON.stringify({ perguntas_pos_venda: true }));
      localStorage.setItem('token', 'test-token');
    });
    await page.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      requests.push({ method: request.method(), pathname: url.pathname, search: url.search });

      if (url.pathname === '/perguntas_pos_venda.html') {
        await route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: html });
        return;
      }
      if (url.pathname === '/auth.js') {
        await route.fulfill({
          status: 200,
          contentType: 'application/javascript; charset=utf-8',
          body: 'window.verificarSessao = () => true; window.obterAuthHeaders = () => ({ Authorization: "Bearer test-token" });',
        });
        return;
      }
      if (url.pathname === '/ia-sidebar.js') {
        await route.fulfill({ status: 200, contentType: 'application/javascript; charset=utf-8', body: '' });
        return;
      }
      if (url.pathname.startsWith('/perguntas_pos_venda/')) {
        const relative = url.pathname.replace(/^\/+/, '');
        const target = path.resolve(staticRoot, relative);
        assert(target.startsWith(staticRoot + path.sep), 'asset deve permanecer dentro de static');
        await route.fulfill({ status: 200, contentType: contentType(target), body: fs.readFileSync(target) });
        return;
      }
      if (url.pathname === '/api/mercadolivre/perguntas/lojas') {
        await json(route, { lojas });
        return;
      }
      if (url.pathname === '/api/mercadolivre/ia-treinamento/skus') {
        const storeId = url.searchParams.get('store_id');
        await json(route, { success: true, store_id: storeId, produtos: productsByStore[storeId] || [] });
        return;
      }
      if (url.pathname === '/api/mercadolivre/ia-treinamento' && request.method() === 'GET') {
        const storeId = url.searchParams.get('store_id');
        await json(route, { success: true, store_id: storeId, ...(trainingByStore[storeId] || {}) });
        return;
      }
      if (url.pathname === '/api/mercadolivre/ia-treinamento' && request.method() === 'POST') {
        const payload = request.postDataJSON();
        assert.strictEqual(payload.store_id, 'store-alpha');
        assert.strictEqual(payload.sku, '002');
        assert.strictEqual(payload.notas_sku, 'Orientação nova e isolada do SKU 002.');
        await json(route, {
          success: true,
          storage: 'obsidian_context_hub_draft',
          requires_review: true,
          sku_note_id: 'draft-sku-002',
        });
        return;
      }
      if (url.pathname === '/api/mercadolivre/perguntas/automacao/status') {
        await json(route, { lojas: [], worker_iniciado: false, executando: false });
        return;
      }
      if (url.pathname.startsWith('/api/mercadolivre/perguntas')) {
        await json(route, { questions: [], total: 0, status_resumo: {} });
        return;
      }
      await json(route, { success: true, lojas: [], pendentes: [] });
    });

    await page.goto('http://jk.local/perguntas_pos_venda.html', { waitUntil: 'load' });
    await page.getByRole('tab', { name: 'Treinar IA' }).click();
    await page.locator('#ai-training-sku-count').filter({ hasText: '3 SKU(s)' }).waitFor();

    assert.strictEqual(await page.locator('#ai-training-scope').inputValue(), 'store-alpha');
    assert.strictEqual(await page.locator('#ai-training-scope option').count(), 3, 'somente escolha obrigatória e lojas exatas');
    assert.match(await page.locator('#ai-training-general-summary').innerText(), /Responder de forma cordial e objetiva/);
    assert.strictEqual(await page.locator('#ai-training-sku-list .training-sku-card').count(), 3, 'todos os SKUs da loja devem aparecer');

    await page.locator('[data-training-sku="002"]').click();
    await page.getByRole('dialog').waitFor({ state: 'visible' });
    assert.match(await page.getByRole('dialog').innerText(), /Produto Alpha 2/);
    assert.match(await page.getByRole('dialog').innerText(), /ainda não possui orientação específica/);

    await page.getByRole('button', { name: 'Adicionar orientação', exact: true }).last().click();
    await page.locator('#ai-training-notas-sku').fill('Orientação nova e isolada do SKU 002.');
    await page.getByRole('button', { name: 'Salvar orientação do SKU' }).click();
    await page.locator('#ai-training-status').filter({ hasText: 'salvo no Obsidian' }).waitFor();
    assert.match(await page.getByRole('dialog').innerText(), /Orientação nova e isolada do SKU 002/);
    if (process.env.JK_CAPTURE_TEST_SCREENSHOT === '1') {
      const outputDir = path.join(root, 'test-results', 'perguntas-training-store-sku');
      fs.mkdirSync(outputDir, { recursive: true });
      await page.screenshot({ path: path.join(outputDir, 'sku-guidance-popover.png'), fullPage: true });
    }

    await page.getByRole('button', { name: 'Fechar orientações do SKU' }).click();
    await page.locator('#ai-training-scope').selectOption('store-beta');
    await page.locator('#ai-training-sku-count').filter({ hasText: '1 SKU(s)' }).waitFor();
    assert.match(await page.locator('#ai-training-general-summary').innerText(), /saudação da Loja Beta/);
    assert.match(await page.locator('#ai-training-sku-list').innerText(), /Produto exclusivo Beta/);
    assert(!await page.locator('#ai-training-sku-list').innerText().then(text => text.includes('Produto Alpha')));

    const trainingGets = requests.filter(item => item.method === 'GET' && item.pathname === '/api/mercadolivre/ia-treinamento');
    assert(trainingGets.every(item => /store_id=store-(alpha|beta)/.test(item.search)), 'treinamento nunca pode consultar escopo global');
    assert.deepStrictEqual(pageErrors, [], `erros no navegador: ${pageErrors.join(' | ')}`);
    console.log('Treinar IA por loja no navegador: OK');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
