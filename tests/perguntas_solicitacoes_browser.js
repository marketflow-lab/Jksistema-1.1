'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const staticRoot = path.join(root, 'static');
const html = fs.readFileSync(path.join(staticRoot, 'perguntas_pos_venda.html'), 'utf8');
const stores = [
  { store_id: 'store-alpha', nome: 'Loja repetida', seller_id: 'seller-alpha', site_id: 'MLB', mercadolivre_conectado: true },
  { store_id: 'store-beta', nome: 'Loja repetida', seller_id: 'seller-beta', site_id: 'MLB', mercadolivre_conectado: true },
];

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

function contentType(filePath) {
  if (filePath.endsWith('.css')) return 'text/css; charset=utf-8';
  if (filePath.endsWith('.js')) return 'application/javascript; charset=utf-8';
  return 'application/octet-stream';
}

function solicitation(storeId, suffix = '') {
  const store = stores.find(item => item.store_id === storeId) || stores[0];
  const label = storeId === 'store-beta' ? 'BETA' : 'ALPHA';
  return {
    job_id: `job-${storeId}${suffix}`,
    store_id: store.store_id,
    loja: store.nome,
    seller_id: store.seller_id,
    site_id: store.site_id,
    task_type: 'public_question',
    subject_key: `Q-${label}`,
    status: 'completed',
    current_step: 'aprovar',
    status_message: `Processamento ${label} concluído`,
    attempt_count: 2,
    evidencias: [{ rotulo: 'Compatibilidade', status: 'confirmada' }],
    avisos: ['Revise antes de enviar.'],
    conclusao: {
      tipo: 'resposta_pronta',
      mensagem: `CONCLUSÃO EXCLUSIVA ${label}${suffix}`,
      resposta: `RESPOSTA EXCLUSIVA ${label}${suffix}`,
    },
    created_at: '2026-09-08T12:00:00Z',
    updated_at: '2026-09-08T12:01:00Z',
  };
}

(async () => {
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.JK_TEST_BROWSER_PATH ? { executablePath: process.env.JK_TEST_BROWSER_PATH } : {}),
  });
  const calls = [];
  const errors = [];
  let alphaDelay = null;
  let alphaStarted = null;
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    page.setDefaultTimeout(12000);
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      localStorage.setItem('permissions', JSON.stringify({ perguntas_pos_venda: true }));
      localStorage.setItem('token', 'tenant-test-token');
    });
    await page.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      const json = (payload, status = 200) => route.fulfill({
        status,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify(payload),
      });

      if (url.pathname === '/perguntas_pos_venda.html') {
        await route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: html });
        return;
      }
      if (url.pathname === '/auth.js') {
        await route.fulfill({
          status: 200,
          contentType: 'application/javascript; charset=utf-8',
          body: 'window.verificarSessao=()=>true; window.obterAuthHeaders=()=>({Authorization:"Bearer "+localStorage.getItem("token")});',
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
        assert(target.startsWith(staticRoot + path.sep));
        await route.fulfill({ status: 200, contentType: contentType(target), body: fs.readFileSync(target) });
        return;
      }
      if (url.pathname === '/api/mercadolivre/perguntas/lojas') {
        await json({ lojas: stores });
        return;
      }
      if (url.pathname === '/api/mercadolivre/assistant/solicitacoes') {
        const call = {
          storeId: url.searchParams.get('store_id') || '',
          status: url.searchParams.get('status') || '',
          limit: Number(url.searchParams.get('limit')),
          offset: Number(url.searchParams.get('offset')),
          authorization: request.headers().authorization || '',
        };
        calls.push(call);
        if (call.storeId === 'store-alpha' && alphaDelay) {
          if (alphaStarted) alphaStarted.resolve();
          await alphaDelay.promise;
        }
        const selected = call.storeId === 'store-beta' ? 'store-beta' : 'store-alpha';
        const suffix = call.offset ? '-PAGINA-2' : '';
        const items = call.storeId
          ? [solicitation(selected, suffix)]
          : [solicitation('store-alpha'), solicitation('store-beta')];
        const total = call.storeId ? (selected === 'store-beta' ? 21 : 1) : 2;
        await json({
          solicitacoes: items,
          total,
          limit: call.limit || 20,
          offset: call.offset || 0,
          status_resumo: { completed: total },
        });
        return;
      }
      if (url.pathname === '/api/mercadolivre/perguntas/automacao/status') {
        await json({ lojas: [], worker_iniciado: false, executando: false });
        return;
      }
      if (url.pathname.startsWith('/api/mercadolivre/perguntas')) {
        await json({ questions: [], lojas: [], total: 0, status_resumo: {} });
        return;
      }
      await json({ success: true, lojas: [], pendentes: [] });
    });

    await page.goto('http://jk.local/perguntas_pos_venda.html', { waitUntil: 'load' });
    await page.locator('#tab-solicitacao').click();
    await page.locator('.solicitacao-card').first().waitFor();
    assert.deepStrictEqual(
      await page.locator('.solicitacoes-store-group').evaluateAll(groups => groups.map(group => group.dataset.storeId)),
      ['store-alpha', 'store-beta'],
      'Todas as contas deve separar lojas homonimas pelo store_id',
    );

    alphaDelay = deferred();
    alphaStarted = deferred();
    await page.locator('#lojas-grid .store-card[data-store-id="store-alpha"]').click();
    await alphaStarted.promise;
    await page.locator('#lojas-grid .store-card[data-store-id="store-beta"]').click();
    await page.locator('#solicitacoes-list').filter({ hasText: 'BETA' }).waitFor();
    alphaDelay.resolve();
    await page.waitForTimeout(150);

    const renderedAfterLateAlpha = await page.locator('#solicitacoes-list').innerText();
    assert.match(renderedAfterLateAlpha, /BETA/);
    assert.doesNotMatch(renderedAfterLateAlpha, /ALPHA/, 'resposta atrasada da loja anterior nao pode substituir a loja atual');
    assert.match(await page.locator('#solicitacoes-loja-status').innerText(), /Loja repetida/);

    const filteredResponse = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === '/api/mercadolivre/assistant/solicitacoes'
        && url.searchParams.get('store_id') === 'store-beta'
        && url.searchParams.get('status') === 'completed';
    });
    await page.locator('#solicitacoes-status-filtro').selectOption('completed');
    await filteredResponse;
    await page.locator('.solicitacao-card').first().waitFor();
    const filtered = calls.at(-1);
    assert.strictEqual(filtered.storeId, 'store-beta');
    assert.strictEqual(filtered.status, 'completed');
    assert.strictEqual(filtered.limit, 20);
    assert.strictEqual(filtered.offset, 0);
    assert.strictEqual(filtered.authorization, 'Bearer tenant-test-token');

    await page.locator('#solicitacoes-pagination button').filter({ hasText: /Próxima/i }).click();
    await page.locator('#solicitacoes-list').filter({ hasText: 'PAGINA-2' }).waitFor();
    assert.strictEqual(calls.at(-1).storeId, 'store-beta');
    assert.strictEqual(calls.at(-1).offset, 20);
    assert.deepStrictEqual(errors, []);

    console.log('Perguntas solicitacoes browser: OK');
  } finally {
    if (alphaDelay) alphaDelay.resolve();
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
