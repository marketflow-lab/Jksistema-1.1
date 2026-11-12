'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
const html = fs.readFileSync('static/integracoes.html', 'utf8');
const start = html.indexOf('            function initializeBlingCentralMigration() {');
const end = html.indexOf('            initializeBlingCentralMigration();', start)
  + '            initializeBlingCentralMigration();'.length;
const script = html.slice(start, end);
const panel = html.match(/<section id="bling-central-migration"[\s\S]*?<\/section>/)[0];

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
  try {
    const context = await browser.newContext();
    const storeId = 'a'.repeat(24);
    const fingerprint = 'b'.repeat(64);
    const uploads = [];
    const calls = [];
    let previewStoreIds = [storeId];
    let state = {status: 'not_started', backup_status: 'none', providers: ['mercadolivre', 'bling']};
    let complete = false;
    await context.route('http://bling.test/**', async route => {
      const url = new URL(route.request().url());
      if (!url.pathname.startsWith('/api/')) return route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><body>' + panel + '</body></html>' });
      calls.push(url.pathname + url.search);
      if (url.pathname === '/api/central/migration/status') {
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify(state) });
      }
      if (url.pathname === '/api/central/migration/preview') {
        assert.equal(url.searchParams.get('provider'), 'bling');
        assert.equal(url.searchParams.get('store_id'), storeId);
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify({
          preview_fingerprint: fingerprint, stores_total: 1, connections_total: 1,
          providers: ['bling'], store_ids: previewStoreIds
        }) });
      }
      if (url.pathname === '/api/central/migration/execute') {
        const payload = route.request().postDataJSON();
        uploads.push(payload);
        state = {status: complete ? 'completed' : 'failed', backup_status: 'frozen',
          providers: payload.providers, store_ids: payload.store_ids,
          operation_id: payload.operation_id, preview_fingerprint: fingerprint,
          remote: {status: 'completed', success: true, operation_id: payload.operation_id}};
        return route.fulfill({status: complete ? 200 : 504, contentType: 'application/json', body: JSON.stringify(complete
          ? {success: true, status: 'completed', logout_required: true}
          : {detail: 'Resposta não confirmada; consulte novamente antes de retomar.'})});
      }
      throw new Error('Unexpected route ' + url.pathname);
    });
    const page = await context.newPage();
    async function initialize(accepted) {
      await page.goto('http://bling.test/integracoes.html');
      await page.evaluate(({storeId, accepted}) => {
        window.selectedStore = {store_id: storeId, nome: 'Loja de teste'};
        window.obterAuthHeaders = () => ({});
        window.confirm = message => {window.confirmation = message; return accepted;};
        window.navegarComTransicao = () => {};
        document.getElementById('bling-central-migration').hidden = false;
        localStorage.setItem('access_token', 'test-session');
      }, {storeId, accepted});
      await page.addScriptTag({content: script});
    }
    await initialize(false);
    await page.locator('#bling-central-migrate').click();
    await page.waitForFunction(() => /não iniciada/.test(document.getElementById('bling-central-feedback').textContent));
    assert.equal(uploads.length, 0, 'cancel must not submit or rotate credentials');
    assert.match(await page.evaluate(() => window.confirmation), /atualize o JK em todos os computadores/);
    assert.match(await page.evaluate(() => window.confirmation), /feche as sessões antigas/);

    await initialize(true);
    await page.locator('#bling-central-migrate').click();
    await page.waitForFunction(() => /não confirmada/.test(document.getElementById('bling-central-feedback').textContent));
    const first = uploads[0];
    assert.deepEqual(first.providers, ['bling']);
    assert.deepEqual(first.store_ids, [storeId]);
    assert.equal(first.legacy_copies_stopped, true);
    assert.equal(await page.evaluate(() => localStorage.getItem('access_token')), 'test-session');

    // An uncertain submission resumes the original scope without another preview.
    const previewCount = calls.filter(call => call.includes('/preview')).length;
    state.remote_error = {message: 'Central indisponível para conferir.'};
    await page.locator('#bling-central-migrate').click();
    await page.waitForFunction(() => /Central indisponível/.test(document.getElementById('bling-central-feedback').textContent));
    assert.equal(uploads.length, 1, 'status outage must prevent replay');
    delete state.remote_error;
    await initialize(true);
    complete = true;
    await page.locator('#bling-central-migrate').click();
    await page.waitForFunction(() => /Bling centralizado/.test(document.getElementById('bling-central-feedback').textContent));
    assert.deepEqual(uploads[1], first);
    assert.equal(calls.filter(call => call.includes('/preview')).length, previewCount);
    assert.equal(await page.evaluate(() => localStorage.getItem('access_token')), null);

    // All aliases displayed in the preview must join the same operation.
    state = {status: 'not_started', backup_status: 'none'};
    previewStoreIds = [storeId, 'c'.repeat(24)];
    await initialize(true);
    await page.locator('#bling-central-migrate').click();
    await page.waitForFunction(() => /Bling centralizado/.test(document.getElementById('bling-central-feedback').textContent));
    assert.deepEqual(uploads[2].store_ids, previewStoreIds);
    assert.match(await page.evaluate(() => window.confirmation), /outras 1 loja/);
    console.log('Bling scoped migration browser: OK');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
