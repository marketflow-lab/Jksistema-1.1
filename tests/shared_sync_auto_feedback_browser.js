'use strict';
const assert = require('assert');
const fs = require('fs');
const { chromium } = require('playwright');
const boot = fs.readFileSync('static/auth/shared-sync-boot.js', 'utf8');
const code = boot.slice(boot.indexOf('(function initMachineSharedSyncAuto()'));
const session = fs.readFileSync('static/auth/session.js', 'utf8');
const coordinator = session.slice(session.indexOf('(function initTabCoordinator()'));
const main = fs.readFileSync('static/cadastro/main/04-init.js', 'utf8');

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
  try {
    const context = await browser.newContext();
    const requests = [];
    await context.route('http://sync.test/**', async route => {
      if (route.request().url().includes('/api/')) requests.push(route.request().url());
      return route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><body><p id="products"></p><p id="costs"></p><img id="photo"></body></html>' });
    });
    async function openPage() {
      const page = await context.newPage();
      await page.goto('http://sync.test/cadastro.html');
      await page.evaluate(() => {
        localStorage.setItem('user_data', JSON.stringify({ client_id: 'tenant-a', username: 'operator-a', machine_id: 'synthetic' }));
        window.received = [];
        window.addEventListener('jk:machine-sync-updated', event => window.received.push(event.detail));
      });
      await page.addScriptTag({ content: coordinator });
      await page.addScriptTag({ content: code });
      return page;
    }
    const origin = await openPage();
    const destination = await openPage();
    for (const page of [origin, destination]) {
      assert.deepStrictEqual(await page.evaluate(() => window.jkMachineSyncNow()), { success: false, manual_only: true });
      await page.evaluate(() => {
        window.dispatchEvent(new Event('focus'));
        window.dispatchEvent(new Event('pageshow'));
        document.dispatchEvent(new Event('visibilitychange'));
      });
    }
    await destination.evaluate(() => {
      const noop = () => {};
      const elements = new Proxy({}, { get(target, key) {
        if (!target[key]) target[key] = document.createElement(key === 'fornecedorForm' ? 'form' : 'input');
        return target[key];
      } });
      window.loads = 0;
      window.failReload = false;
      window.JKCadastro = {
        components: new Set(['runtime', 'core', 'produtos', 'custos-mlb', 'actions', 'fornecedores', 'importacoes-catalogos']),
        runtime: { elements, state: {} },
        produtosTabela: { vincularNavegacaoLinhas: noop, renderTabela: noop },
        custosMlb: { renderCustos: noop, trocarAba: noop, renderDetalhesMlbPorSku: noop },
        core: { setStatus: text => { window.lastStatus = text; } },
        actions: {
          inicializarNcm: noop, inicializarLojas: async () => true,
          carregarProdutos: async () => {
            window.loads += 1;
            if (window.failReload) return false;
            document.querySelector('#products').textContent = '0007 Loja A';
            document.querySelector('#costs').textContent = '19.90';
            document.querySelector('#photo').alt = 'Foto 0007 Loja A';
            return true;
          },
          sincronizarNcmBackground: noop,
        },
        fornecedores: { init: noop }, importacoesCatalogos: { init: noop }
      };
    });
    await destination.addScriptTag({ content: main });
    await destination.waitForFunction(() => window.loads === 1);
    await destination.evaluate(() => { window.JKCadastro.runtime.state.importacaoCatalogoAtiva = true; });
    await origin.evaluate(() => window.jkNotificarImportacaoMaquinas({ received_scopes: ['cadastro'], results: [{ access_token: 'must-not-travel' }] }));
    await destination.waitForFunction(() => window.received.length === 1);
    assert.strictEqual(await destination.evaluate(() => window.loads), 1, 'active operation must defer screen reload');
    assert.strictEqual(await destination.evaluate(() => 'results' in window.received[0]), false, 'cross-tab message must contain only screen invalidation');
    await destination.evaluate(() => {
      window.JKCadastro.runtime.state.importacaoCatalogoAtiva = false;
      window.dispatchEvent(new Event('jk:cadastro-operation-finished'));
    });
    await destination.waitForFunction(() => window.loads === 2);
    assert.strictEqual(await destination.locator('#products').innerText(), '0007 Loja A');
    assert.strictEqual(await destination.locator('#costs').innerText(), '19.90');
    assert.strictEqual(await destination.locator('#photo').getAttribute('alt'), 'Foto 0007 Loja A');
    await destination.evaluate(() => {
      window.JKCadastro.runtime.elements.fornecedorForm.dispatchEvent(new Event('input'));
    });
    await origin.evaluate(() => window.jkNotificarImportacaoMaquinas({ received_scopes: ['cadastro'] }));
    await destination.waitForFunction(() => window.received.length === 2);
    await destination.evaluate(() => window.dispatchEvent(new Event('focus')));
    assert.strictEqual(await destination.evaluate(() => window.loads), 2, 'blur cannot discard an unfinished form');
    await destination.evaluate(() => window.JKCadastro.runtime.elements.fornecedorForm.reset());
    await destination.waitForFunction(() => window.loads === 3);
    await destination.evaluate(() => { window.failReload = true; });
    await origin.evaluate(() => window.jkNotificarImportacaoMaquinas({ received_scopes: ['cadastro'] }));
    await destination.waitForFunction(() => window.loads === 4);
    await destination.waitForFunction(() => /Dados recebidos/.test(window.lastStatus));
    await destination.evaluate(() => { window.failReload = false; window.dispatchEvent(new Event('focus')); });
    await destination.waitForFunction(() => window.loads === 5);
    await origin.evaluate(() => {
      const frame = document.createElement('iframe');
      frame.src = '/integracoes.html';
      document.body.appendChild(frame);
    });
    await origin.waitForFunction(() => document.querySelector('iframe')?.contentWindow?.location.pathname === '/integracoes.html' && document.querySelector('iframe').contentDocument?.head !== null);
    const embedded = origin.frames().find(frame => frame !== origin.mainFrame());
    await embedded.evaluate(() => {
      window.received = [];
      window.addEventListener('jk:machine-sync-updated', event => window.received.push(event.detail));
    });
    await embedded.addScriptTag({ content: coordinator });
    await embedded.addScriptTag({ content: code });
    assert.strictEqual(await embedded.evaluate(() => window.jkTabCoordinator.tabId), await origin.evaluate(() => window.jkTabCoordinator.tabId));
    await origin.evaluate(() => {
      const frame = document.createElement('iframe');
      frame.id = 'different-session';
      frame.src = '/different-session.html';
      document.body.appendChild(frame);
    });
    await origin.waitForFunction(() => document.getElementById('different-session')?.contentWindow?.location.pathname === '/different-session.html' && document.getElementById('different-session').contentDocument?.head !== null);
    const foreign = origin.frames().find(frame => frame.url().endsWith('/different-session.html'));
    await foreign.evaluate(() => {
      const storage = window.localStorage;
      Object.defineProperty(window, 'localStorage', { value: {
        getItem: key => key === 'user_data' ? JSON.stringify({ client_id: 'tenant-other', username: 'operator-a' }) : storage.getItem(key),
        setItem: (key, value) => storage.setItem(key, value), removeItem: key => storage.removeItem(key)
      } });
      window.received = [];
      window.addEventListener('jk:machine-sync-updated', event => window.received.push(event.detail));
    });
    await foreign.addScriptTag({ content: coordinator });
    await foreign.addScriptTag({ content: code });
    await origin.evaluate(() => window.jkNotificarImportacaoMaquinas({ received_scopes: ['cadastro'] }));
    await embedded.waitForFunction(() => window.received.length === 1);
    assert.strictEqual(await foreign.evaluate(() => window.received.length), 0, 'direct frame notification must also enforce sender session scope');
    const beforeForeign = await origin.evaluate(() => window.received.length);
    await foreign.evaluate(() => window.jkNotificarImportacaoMaquinas({ received_scopes: ['cadastro'] }));
    assert.strictEqual(await origin.evaluate(() => window.received.length), beforeForeign, 'foreign frame cannot invalidate another session');
    await destination.waitForFunction(() => window.loads === 6);
    await embedded.evaluate(() => window.jkNotificarImportacaoMaquinas({ received_scopes: ['cadastro'] }));
    await origin.waitForFunction(() => window.received.length === 5);
    await destination.waitForFunction(() => window.loads === 7);
    const before = await destination.evaluate(() => window.received.length);
    await origin.evaluate(() => {
      // Same backend origin switched to another authenticated client. The old
      // subscription must ignore events; no previously opened tab may refresh.
      localStorage.setItem('user_data', JSON.stringify({ client_id: 'tenant-b', username: 'operator-a' }));
      window.jkNotificarImportacaoMaquinas({ received_scopes: ['cadastro'] });
    });
    assert.strictEqual(await destination.evaluate(() => window.received.length), before);
    assert.deepStrictEqual(requests, [], 'no boot, focus, legacy alias, or notification may initiate transfer');
    console.log('shared sync manual notifications browser: OK');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
