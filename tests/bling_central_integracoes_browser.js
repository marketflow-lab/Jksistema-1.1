'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
const html = fs.readFileSync('static/integracoes.html', 'utf8');

(async () => {
  const browser = await chromium.launch({headless: true,
    ...(process.env.PLAYWRIGHT_CHANNEL ? {channel: process.env.PLAYWRIGHT_CHANNEL} : {})});
  try {
    const context = await browser.newContext();
    await context.addInitScript(() => {
      localStorage.setItem('user_data', JSON.stringify({client_id: 'tenant-test',
        central: {enabled: true, sync_mode: 'provider', providers: ['bling']}}));
      window.alert = () => {};
      window.confirm = () => true;
      window.opened = [];
      window.electronAPI = {openExternalChrome: async url => window.opened.push(url)};
    });
    const localIntegrations = () => ({
      mercadolivre: {app_id: 'test-ml-app', client_secret: 'test-ml-secret',
        access_token: 'test-ml-access', refresh_token: 'test-ml-refresh', connected: true},
      mercadoturbo: {token: 'test-turbo-token', connected: true}
    });
    let stores = [
      {store_id: 'a'.repeat(24), nome: 'Bling centralizado', access: 'owner',
        integracoes: {...localIntegrations(), bling: {central: true, connected: true}}},
      {store_id: 'b'.repeat(24), nome: 'Loja ainda local',
        integracoes: {...localIntegrations(), bling: {id: 'test-app', secret: 'test-secret',
          access_token: 'test-access', refresh_token: 'test-refresh', connected: true}}},
      {store_id: 'c'.repeat(24), nome: 'Bling compartilhado', access: 'read',
        integracoes: {bling: {central: true, connected: true}}}
    ];
    const calls = [];
    let dropSelected = false;
    async function json(route, body, status = 200) {
      return route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    }
    await context.route('http://provider.test/**', async route => {
      const req = route.request();
      const url = new URL(req.url());
      if (url.pathname === '/integracoes.html') return route.fulfill({contentType: 'text/html', body: html});
      if (url.pathname === '/auth.js') return route.fulfill({contentType: 'application/javascript', body:
        "window.verificarSessao=()=>true;window.obterAuthHeaders=(h={})=>h;window.jkCentralManualMode=()=>false;window.jkCentralMigrationMode=()=>false;"});
      if (url.pathname === '/ia-sidebar.js') return route.fulfill({contentType: 'application/javascript', body: ''});
      if (url.pathname === '/admin_usuarios.html') return route.fulfill({contentType: 'text/html', body: '<p>Compartilhar</p>'});
      calls.push({path: url.pathname, method: req.method(), query: url.search,
        body: req.method() === 'POST' ? req.postDataJSON() : null});
      if (url.pathname === '/api/lojas') return json(route, stores);
      if (url.pathname.startsWith('/api/lojas/')) return json(route,
        stores.find(row => row.store_id === url.searchParams.get('store_id')));
      if (url.pathname === '/api/central/refresh') {
        if (dropSelected) stores = stores.filter(row => row.store_id !== 'a'.repeat(24));
        return json(route, {success: true});
      }
      if (url.pathname === '/api/integracoes/mercadolivre/start') return json(route, {url: 'https://auth.example.test/ml'});
      if (url.pathname.endsWith('/turbo')) {
        const store = stores.find(row => row.store_id === url.searchParams.get('store_id'));
        store.integracoes.mercadoturbo.token = req.postDataJSON().token;
        return json(route, {success: true});
      }
      throw new Error('Unexpected route ' + url.pathname);
    });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://provider.test/integracoes.html');
    assert.equal(await page.locator('#central-account-controls').isVisible(), true);
    assert.equal(await page.locator('#central-grant-form').isHidden(), true);
    assert.equal(await page.locator('[data-main-tab="compartilhar"]').isVisible(), true);
    assert.equal(await page.locator('#user-share-frame').count(), 1);
    assert.equal(await page.locator('#user-share-frame').getAttribute('src'), null);
    await page.locator('#store-list li[data-store-id="' + 'a'.repeat(24) + '"]').click();
    await page.waitForFunction(() => document.getElementById('store-title').textContent.includes('Bling centralizado'));
    assert.equal(await page.locator('#central-grant-form').isVisible(), true);
    assert.equal(await page.locator('#bling-central-migration').isHidden(), true);
    assert.equal(await page.locator('#ml-status').innerText(), 'Conectado');
    assert.equal(await page.locator('#turbo-status').innerText(), 'Conectado');
    assert.equal(await page.locator('#ml-client-secret').inputValue(), 'test-ml-secret');
    assert.equal(await page.locator('#turbo-token').inputValue(), 'test-turbo-token');
    assert.equal(calls.some(call => call.path.startsWith('/api/central/')), false, 'reading stores must not migrate or refresh');

    await page.locator('[data-tab="mercadolivre"]').click();
    await page.locator('#form-ml button[type="submit"]').click();
    await page.waitForFunction(() => window.opened.length === 1);
    const ml = calls.find(call => call.path === '/api/integracoes/mercadolivre/start');
    assert.equal(ml.body.store_id, 'a'.repeat(24));
    assert.equal(ml.body.client_secret, 'test-ml-secret');
    await page.locator('[data-tab="mercadoturbo"]').click();
    await page.locator('#turbo-token').fill('test-turbo-updated');
    await page.locator('#form-turbo button[type="submit"]').click();
    await page.waitForFunction(() => document.getElementById('turbo-token').value === 'test-turbo-updated');
    await page.locator('#central-refresh').click();
    await page.waitForFunction(() => /Lojas atualizadas/.test(document.getElementById('central-feedback').textContent));
    assert.equal(await page.locator('#ml-client-secret').inputValue(), 'test-ml-secret');
    assert.equal(await page.locator('#turbo-token').inputValue(), 'test-turbo-updated');

    // A missing Central session is distinct from a revoked Bling authorization.
    stores[0].integracoes.bling = {central: true, connected: false, status: 'central_indisponivel'};
    await page.locator('#central-refresh').click();
    await page.waitForFunction(() => document.getElementById('bling-status').textContent === 'Central indisponível');
    assert.equal(await page.locator('#bling-status').getAttribute('title'), 'Entre novamente no JK ou atualize as lojas para conferir o acesso à conexão.');
    stores[0].integracoes.bling = {central: true, connected: true};
    await page.locator('#central-refresh').click();
    await page.waitForFunction(() => document.getElementById('bling-status').textContent === 'Conectado');
    assert.equal(await page.locator('#bling-status').getAttribute('title'), '');

    await page.locator('#store-list li[data-store-id="' + 'b'.repeat(24) + '"]').click();
    await page.waitForFunction(() => document.getElementById('store-title').textContent.includes('Loja ainda local'));
    assert.equal(await page.locator('#central-grant-form').isHidden(), true, 'a local store has no central grant');
    await page.locator('[data-tab="bling"]').click();
    assert.equal(await page.locator('#bling-central-migration').isVisible(), true);
    await page.locator('#store-list li[data-store-id="' + 'c'.repeat(24) + '"]').click();
    await page.waitForFunction(() => document.getElementById('store-title').textContent.includes('Bling compartilhado'));
    assert.equal(await page.locator('#central-grant-form').isHidden(), true);
    assert.equal(await page.locator('#rename-store-btn').isHidden(), true);
    await page.locator('[data-main-tab="compartilhar"]').click();
    assert.match(await page.locator('#user-share-frame').getAttribute('src'), /admin_usuarios/);
    await page.locator('[data-main-tab="lojas"]').click();
    await page.locator('#store-list li[data-store-id="' + 'a'.repeat(24) + '"]').click();
    await page.waitForFunction(() => !document.getElementById('central-grant-form').hidden);
    dropSelected = true;
    await page.locator('#central-refresh').click();
    await page.waitForFunction(() => document.getElementById('integrations-panel').classList.contains('hidden'));
    assert.equal(await page.locator('#central-grant-form').isHidden(), true, 'refresh removing selected store clears grants');
    assert.deepEqual(errors, []);
    console.log('Bling provider integrations browser: OK');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
