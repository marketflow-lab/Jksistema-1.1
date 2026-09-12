'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'static', 'integracoes.html'), 'utf8');

function json(route, payload, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(payload),
  });
}

(async () => {
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}),
  });
  try {
    const context = await browser.newContext();
    await context.addInitScript(() => {
      localStorage.setItem('user_data', JSON.stringify({ client_id: 'tenant-ui-test' }));
    });

    let stores = [
      { store_id: 'store-a', nome: 'Loja A', access: 'owner', integracoes: { bling: { connected: true, central: true } } },
      {
        store_id: 'store-b',
        nome: 'Loja B',
        access: 'write',
        integracoes: {
          bling: { connected: true, central: true },
          mercadolivre: { connected: true, central: true },
          mercadoturbo: { connected: true, token: 'turbo-local-preservado' },
        },
      },
    ];
    let delayedPatch = null;
    let failNextPatch = false;
    const patchCalls = [];

    await context.route('http://rename.test/**', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.pathname === '/auth.js') {
        return route.fulfill({
          contentType: 'application/javascript',
          body: `
            window.verificarSessao = () => true;
            window.obterAuthHeaders = (headers = {}) => headers;
            window.jkCentralManualMode = () => localStorage.getItem('rename-test-central') === '1';
          `,
        });
      }
      if (url.pathname === '/ia-sidebar.js') {
        return route.fulfill({ contentType: 'application/javascript', body: '' });
      }
      if (url.pathname === '/integracoes.html') {
        return route.fulfill({ contentType: 'text/html', body: html });
      }
      if (url.pathname === '/api/lojas' && request.method() === 'GET') {
        return json(route, stores);
      }
      if (url.pathname.startsWith('/api/lojas/') && request.method() === 'GET') {
        const store = stores.find(item => item.store_id === url.searchParams.get('store_id'));
        return store ? json(route, store) : json(route, { detail: 'Loja ausente.' }, 404);
      }
      if (url.pathname.startsWith('/api/lojas/') && request.method() === 'PATCH') {
        const storeId = url.searchParams.get('store_id');
        const payload = request.postDataJSON();
        patchCalls.push({ storeId, pathName: decodeURIComponent(url.pathname.split('/').pop()), payload });
        if (delayedPatch && delayedPatch.storeId === storeId) {
          await delayedPatch.promise;
          delayedPatch = null;
        }
        if (failNextPatch) {
          failNextPatch = false;
          return json(route, { detail: 'Nome recusado para o teste.' }, 409);
        }
        const index = stores.findIndex(item => item.store_id === storeId);
        const updated = { ...stores[index], nome: payload.nome };
        stores.splice(index, 1, updated);
        const centralIntegrations = updated.integracoes.mercadolivre
          ? { mercadolivre: updated.integracoes.mercadolivre }
          : (updated.integracoes.bling ? { bling: updated.integracoes.bling } : {});
        return json(route, {
          success: true,
          loja: {
            store_id: updated.store_id,
            nome: updated.nome,
            access: updated.access,
            integracoes: centralIntegrations,
          },
        });
      }
      throw new Error(`Rota inesperada no teste: ${request.method()} ${url.pathname}`);
    });

    const page = await context.newPage();
    await page.goto('http://rename.test/integracoes.html');
    await page.locator('#store-list li[data-store-id="store-a"]').click();
    await page.waitForFunction(() => document.getElementById('store-title').textContent.includes('Loja A'));

    assert.equal(await page.locator('#rename-store-btn').isVisible(), true, 'loja local deve mostrar o lapis');
    await page.locator('#rename-store-btn').click();
    assert.equal(await page.locator('#store-rename-save').isDisabled(), true, 'nome igual deve bloquear Salvar');
    await page.locator('#store-rename-input').fill('   ');
    assert.equal(await page.locator('#store-rename-save').isDisabled(), true, 'nome vazio apos trim deve bloquear Salvar');
    await page.locator('#store-rename-input').fill('Nome temporario');
    assert.equal(await page.locator('#store-rename-save').isEnabled(), true);
    await page.locator('#store-rename-cancel').click();
    assert.equal(await page.locator('#store-rename-editor').isHidden(), true, 'Cancelar deve fechar o editor');

    await page.locator('#rename-store-btn').click();
    await page.locator('#store-rename-input').press('Escape');
    assert.equal(await page.locator('#store-rename-editor').isHidden(), true, 'Escape deve fechar o editor');

    let releaseDelayedPatch;
    delayedPatch = {
      storeId: 'store-a',
      promise: new Promise(resolve => { releaseDelayedPatch = resolve; }),
    };
    await page.locator('#rename-store-btn').click();
    await page.locator('#store-rename-input').fill('Loja A Renomeada');
    await page.locator('#store-rename-input').press('Enter');
    await page.waitForFunction(() => document.getElementById('store-rename-editor').getAttribute('aria-busy') === 'true');
    assert.equal(await page.locator('#store-rename-save').innerText(), 'Salvando…');

    await page.locator('#store-list li[data-store-id="store-b"]').click();
    await page.waitForFunction(() => document.getElementById('store-title').textContent.includes('Loja B'));
    await page.locator('#rename-store-btn').click();
    assert.equal(await page.locator('#store-rename-save').innerText(), 'Salvar', 'novo editor deve restaurar o rotulo do botao');
    assert.equal(await page.locator('#store-rename-editor').getAttribute('aria-busy'), 'false', 'novo editor nao herda busy antigo');
    assert.equal(await page.locator('#turbo-status').innerText(), 'Conectado');
    assert.equal(await page.locator('#turbo-token').inputValue(), 'turbo-local-preservado');
    releaseDelayedPatch();
    await page.waitForFunction(() => Array.from(document.querySelectorAll('#store-list li span')).some(node => node.textContent === 'Loja A Renomeada'));
    assert.match(await page.locator('#store-title').innerText(), /Loja B/, 'resposta atrasada nao troca o titulo atual');
    assert.equal(await page.locator('#store-rename-input').inputValue(), 'Loja B', 'resposta atrasada nao troca o editor atual');

    failNextPatch = true;
    await page.locator('#store-rename-input').fill('Nome recusado');
    await page.locator('#store-rename-input').press('Enter');
    await page.waitForFunction(() => /Nome recusado/.test(document.getElementById('store-rename-feedback').textContent));
    assert.equal(await page.locator('#store-rename-input').inputValue(), 'Nome recusado', 'erro deve manter o valor tentado');
    assert.match(await page.locator('#store-title').innerText(), /Loja B/, 'erro deve preservar o nome anterior no titulo');

    await page.evaluate(() => {
      const originalSplice = Array.prototype.splice;
      window.__renameMergedStore = null;
      Array.prototype.splice = function (...args) {
        const replacement = args[2];
        if (replacement && replacement.store_id === 'store-b') {
          window.__renameMergedStore = structuredClone(replacement);
        }
        return originalSplice.apply(this, args);
      };
    });
    await page.locator('#store-rename-input').fill('  Loja B Nova  ');
    await page.locator('#store-rename-input').press('Enter');
    await page.waitForFunction(() => /sucesso/.test(document.getElementById('store-rename-feedback').textContent));
    assert.match(await page.locator('#store-title').innerText(), /Loja B Nova/);
    const mergedStore = await page.evaluate(() => window.__renameMergedStore);
    assert.deepEqual(
      mergedStore.integracoes.mercadoturbo,
      { connected: true, token: 'turbo-local-preservado' },
      'resposta parcial da Central deve preservar o provider Turbo local no estado da loja',
    );
    assert.equal(
      mergedStore.integracoes.bling,
      undefined,
      'provider central ausente da resposta autoritativa nao deve ser ressuscitado',
    );
    assert.equal(await page.locator('#turbo-status').innerText(), 'Conectado');
    assert.equal(await page.locator('#turbo-token').inputValue(), 'turbo-local-preservado');
    assert.equal(patchCalls.at(-1).storeId, 'store-b');
    assert.equal(patchCalls.at(-1).pathName, 'Loja B');
    assert.deepEqual(patchCalls.at(-1).payload, { nome: 'Loja B Nova' }, 'PATCH deve enviar o nome aparado');

    stores = [
      { store_id: 'read-store', nome: 'Somente leitura', access: 'read', integracoes: {} },
      { store_id: 'write-store', nome: 'Pode alterar', access: 'write', integracoes: {} },
      { store_id: 'owner-store', nome: 'Proprietaria', access: 'owner', integracoes: {} },
    ];
    await page.evaluate(() => localStorage.setItem('rename-test-central', '1'));
    await page.reload();
    await page.locator('#store-list li[data-store-id="read-store"]').click();
    await page.waitForFunction(() => document.getElementById('store-title').textContent.includes('Somente leitura'));
    assert.equal(await page.locator('#rename-store-btn').isHidden(), true, 'acesso read deve ocultar o lapis');
    await page.locator('#store-list li[data-store-id="write-store"]').click();
    await page.waitForFunction(() => document.getElementById('store-title').textContent.includes('Pode alterar'));
    assert.equal(await page.locator('#rename-store-btn').isVisible(), true, 'acesso write deve mostrar o lapis');
    await page.locator('#store-list li[data-store-id="owner-store"]').click();
    await page.waitForFunction(() => document.getElementById('store-title').textContent.includes('Proprietaria'));
    assert.equal(await page.locator('#rename-store-btn').isVisible(), true, 'acesso owner deve mostrar o lapis');

    console.log('integracoes store rename browser: OK');
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
