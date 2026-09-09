'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
const html = fs.readFileSync('static/integracoes.html', 'utf8');
const start = html.indexOf('            if (window.jkCentralMigrationMode?.()) {');
const script = html.slice(start, html.indexOf('            void loadStores().catch(() => {});', start));
const panel = html.match(/<section id="central-migration-controls"[\s\S]*?<\/section>/)[0];

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
  try {
    const context = await browser.newContext();
    const calls = [];
    const originalFingerprint = 'a'.repeat(64);
    let state = { status: 'not_started', operation_id: '', remote: null };
    let previewAvailable = false;
    let complete = false;
    let uploads = [];
    await context.route('http://central.test/**', async route => {
      const url = new URL(route.request().url());
      if (!url.pathname.startsWith('/api/')) return route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><body>' + panel + '</body></html>' });
      calls.push(url.pathname + url.search);
      if (url.pathname === '/api/central/migration/status') {
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify(state) });
      }
      if (url.pathname === '/api/central/migration/preview') {
        return route.fulfill({ status: previewAvailable ? 200 : 503, contentType: 'application/json', body: JSON.stringify(previewAvailable
          ? { stores_total: 1, connections_total: 2, preview_fingerprint: originalFingerprint, stores: [{ name: 'Synthetic Store' }] }
          : { detail: 'O serviço de migração da Central ainda não está disponível.' }) });
      }
      if (url.pathname === '/api/central/migration/execute') {
        const payload = route.request().postDataJSON();
        uploads.push(payload);
        state = { status: complete ? 'completed' : 'failed', operation_id: payload.operation_id,
          preview_fingerprint: payload.preview_fingerprint, backup_status: 'frozen', stores_total: 1, connections_total: 2,
          remote: { status: 'completed', success: true, operation_id: payload.operation_id }, needs_local_finalize: !complete };
        return route.fulfill({ status: complete ? 200 : 504, contentType: 'application/json', body: JSON.stringify(complete
          ? { success: true, status: 'completed', operation_id: payload.operation_id, logout_required: true }
          : { detail: 'A resposta da migração não pôde ser confirmada.' }) });
      }
      throw new Error('Unexpected route ' + url.pathname);
    });
    const page = await context.newPage();
    async function initialize() {
      await page.goto('http://central.test/integracoes.html');
      await page.evaluate(() => {
        window.jkCentralMigrationMode = () => true;
        window.obterAuthHeaders = () => ({});
        window.confirm = () => true;
        window.navegarComTransicao = () => {};
        localStorage.setItem('access_token', 'synthetic-session');
      });
      await page.addScriptTag({ content: script });
    }
    await initialize();
    await page.waitForFunction(() => /não está disponível/.test(document.getElementById('central-migration-preview').textContent));
    assert(await page.locator('#central-migration-run').isDisabled(), 'service failure must prevent execution');
    assert.strictEqual(uploads.length, 0);
    previewAvailable = true;
    await page.locator('#central-migration-status').click();
    await page.waitForFunction(() => !document.getElementById('central-migration-run').disabled);
    await page.locator('#central-migration-run').click();
    await page.waitForFunction(() => /não pôde ser confirmada/.test(document.getElementById('central-migration-feedback').textContent));
    assert.strictEqual(uploads.length, 1);
    const first = uploads[0];
    assert.strictEqual(first.preview_fingerprint, originalFingerprint);
    assert.match(first.operation_id, /^[a-f0-9]{32}$/);
    assert.strictEqual(await page.evaluate(() => localStorage.getItem('access_token')), 'synthetic-session');

    // A status outage blocks replay even though the prior POST may have succeeded.
    state.remote_error = { status_code: 503, code: 'central_unavailable', message: 'Central indisponível para conferir a operação.' };
    await page.locator('#central-migration-run').click();
    await page.waitForFunction(() => /Central indisponível/.test(document.getElementById('central-migration-feedback').textContent));
    assert.strictEqual(uploads.length, 1);
    delete state.remote_error;

    // Closing/reopening must recover the frozen identity, not request a new preview.
    const previousPreviews = calls.filter(url => url.endsWith('/preview')).length;
    await initialize();
    await page.waitForFunction(() => document.getElementById('central-migration-run').textContent === 'Finalizar migração nesta máquina');
    assert.strictEqual(calls.filter(url => url.endsWith('/preview')).length, previousPreviews);
    assert.match(await page.locator('#central-migration-feedback').innerText(), /Falta concluir/);
    assert.strictEqual(await page.evaluate(() => localStorage.getItem('access_token')), 'synthetic-session', 'remote completion cannot log out before local finalization');
    await page.locator('#central-migration-run').click();
    await page.waitForFunction(() => /não pôde ser confirmada/.test(document.getElementById('central-migration-feedback').textContent));
    assert.strictEqual(uploads.length, 2);
    assert.deepStrictEqual(uploads[1], first, 'retry must retain original operation and fingerprint');
    const secondExecuteIndex = calls.lastIndexOf('/api/central/migration/execute');
    assert.strictEqual(calls[secondExecuteIndex - 1], '/api/central/migration/status?operation_id=' + first.operation_id);

    state.remote = { status: 'processing', operation_id: first.operation_id };
    await page.locator('#central-migration-run').click();
    await page.waitForFunction(() => /ainda está em andamento/.test(document.getElementById('central-migration-feedback').textContent));
    assert.strictEqual(uploads.length, 2);
    state.remote = { status: 'completed', success: true, operation_id: first.operation_id };
    complete = true;
    await page.locator('#central-migration-run').click();
    await page.waitForFunction(() => /Migração concluída\./.test(document.getElementById('central-migration-feedback').textContent));
    assert.deepStrictEqual(uploads[2], first);
    assert.strictEqual(await page.evaluate(() => localStorage.getItem('access_token')), null);
    console.log('central migration recovery browser: OK');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
