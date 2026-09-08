'use strict';
const assert = require('assert');
const fs = require('fs');
const { chromium } = require('playwright');
const source = fs.readFileSync('static/auth/shared-sync-boot.js', 'utf8');
const start = source.indexOf('(function initMachineSharedSyncAuto()');
const code = source.slice(start, source.indexOf('\n})();', start) + '\n})();'.length);

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const requests = [];
    let response = { success: false, partial: true,
      results: [{ scope: 'lojas_integracoes', success: true, direction: 'pull' }],
      skipped: [{ scope: 'cadastro', reason: 'pull_failed', status_code: 409, message: 'Conflito de cadastro' }] };
    await page.route('http://sync.test/**', async route => {
      if (route.request().url().endsWith('/api/shared-sync/machine-sync/auto')) {
        requests.push(route.request().method());
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify(response) });
      }
      return route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><body></body></html>' });
    });
    await page.goto('http://sync.test/cadastro.html');
    await page.evaluate(() => {
      window.obterToken = () => 'synthetic-session';
      window.tokenSessaoExpirado = () => false;
      window.obterAuthHeaders = () => ({});
      localStorage.setItem('user_data', JSON.stringify({ machine_id: 'synthetic-device' }));
      window.received = [];
      window.addEventListener('jk:machine-sync-updated', event => window.received.push(event.detail.received_scopes));
    });
    await page.addScriptTag({ content: code });
    await page.evaluate(() => window.jkMachineSyncNow());
    assert.deepStrictEqual(await page.evaluate(() => window.received), [['lojas_integracoes']]);
    assert.match(await page.locator('#jkMachineSyncNotice').innerText(), /Conflito de cadastro/);
    assert.strictEqual(requests.length, 1, 'Cadastro must be able to receive');
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    assert.strictEqual(requests.length, 1, 'conflict must stop automatic retries');

    await page.evaluate(() => { window.jkCadastroPodeReceberSync = () => false; });
    await page.evaluate(() => window.jkMachineSyncNow());
    assert.strictEqual(requests.length, 1, 'must defer while editing/importing');
    await page.evaluate(() => { window.jkCadastroPodeReceberSync = () => true; });
    response = { success: true, results: [{ scope: 'cadastro', direction: 'pull', success: true }], skipped: [] };
    await page.getByRole('button', { name: 'Tentar receber novamente' }).click();
    await page.waitForFunction(() => window.received.length === 2);
    assert.strictEqual(await page.locator('#jkMachineSyncNotice').isVisible(), false);
    assert.strictEqual(requests.length, 2);
    assert(requests.every(method => method === 'POST'));
    console.log('shared sync auto feedback browser: OK');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
