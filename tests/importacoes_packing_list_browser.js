'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const previewDirectory = process.env.JK_PACKING_PREVIEW_DIR;
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const lists = ['empty', 'ready', 'fallback', 'legacy', 'error', 'upload-error', 'cancel'].map(id => ({
  id, nome_lista: id === 'empty' ? 'Lista <img src=x onerror="window.__packingXss=1">' : 'Pedido sintético ' + id,
  loja: 'Loja sintética', store_id: 'synthetic-store', status: 'Analisando orçamento',
  supplier: 'Fornecedor sintético', fornecedor_id: 'supplier-a', nacionalizador: 'Empresa sintética',
  numero_invoice: id === 'legacy' ? '' : 'INV-' + id.toUpperCase(), order_date: '2026-10-05',
  packing_list_ready: !['empty', 'upload-error', 'cancel'].includes(id),
  total_itens: 1, total_quantidade: 2, total_m3: 0.03, total_usd: 20,
  itens: [{ SKU: 'SYN-001', Quantidade: 2, 'Valor unidade': 10, 'Valor total': 20 }],
}));
const packingReady = Object.fromEntries(lists.map(list => [list.id, list.packing_list_ready && list.id !== 'fallback']));
const state = { downloads: [], uploads: [], updates: [], alerts: [], errors: [], routeErrors: [], chooserCount: 0,
  downloadGate: null, uploadGate: null, putGate: null, failUpload: false, failPUT: false };
const json = (route, body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
const button = (page, id) => page.locator('[data-packing-list][data-packing-lista-id="' + id + '"]');
const modal = page => page.locator('dialog.packing-list-dialog');
const input = page => modal(page).locator('input[type=file]');
const file = { name: 'synthetic-packing.xlsx', mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buffer: Buffer.from('PK synthetic packing') };
async function until(predicate) {
  const deadline = Date.now() + 10000;
  while (!predicate()) { assert(Date.now() < deadline, 'A operação sintética excedeu o tempo esperado'); await new Promise(resolve => setTimeout(resolve, 20)); }
}

async function routes(page) {
  await page.route('**/*', async route => {
    const request = route.request();
    try {
      const url = new URL(request.url());
      const pathname = url.pathname;
      if (url.origin !== 'http://jk.test') {
        if (url.hostname === 'economia.awesomeapi.com.br') return json(route, { USDBRL: { bid: '5.00' } });
        return route.fulfill({ status: 200, contentType: 'text/css', body: '' });
      }
      if (pathname === '/importacoes.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: fs.readFileSync(path.join(root, 'static', 'importacoes.html')) });
      if (pathname === '/auth.js') return route.fulfill({ status: 200, contentType: 'application/javascript', body: 'window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({Authorization:"Bearer synthetic"});' });
      if (pathname.startsWith('/medias_compras/')) return route.fulfill({ status: 200, contentType: pathname.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(path.join(root, 'static', pathname.slice(1))) });
      if (pathname.startsWith('/api/')) assert.strictEqual(request.headers().authorization, 'Bearer synthetic');
      if (pathname === '/api/lojas') return json(route, [{ nome: 'Loja sintética', store_id: 'synthetic-store' }]);
      if (pathname === '/api/cadastro/fornecedores') return json(route, [{ id: 'supplier-a', nome_empresa: 'Fornecedor sintético' }]);
      if (pathname === '/api/cadastro/nacionalizadores') return json(route, []);
      if (pathname === '/api/medias-compras/listas-pedidos') return json(route, { listas: structuredClone(lists) });
      const packing = pathname.match(/^\/api\/medias-compras\/listas-pedidos\/([^/]+)\/packing-list(\/importar-excel)?$/);
      if (packing) {
        const id = decodeURIComponent(packing[1]);
        const list = lists.find(item => item.id === id);
        assert(list, 'Packing deve pertencer a uma lista sintética');
        if (packing[2]) {
          assert.strictEqual(request.method(), 'POST');
          assert(request.headers()['content-type'].startsWith('multipart/form-data; boundary='));
          assert(request.postDataBuffer().includes(Buffer.from('name="file"')));
          assert(request.postDataBuffer().includes(Buffer.from('filename="synthetic-packing.xlsx"')));
          state.uploads.push(id);
          const gate = state.uploadGate;
          const fail = state.failUpload;
          state.failUpload = false;
          if (gate) await gate;
          if (fail) return json(route, { detail: 'Planilha sintética inválida' }, 400);
          packingReady[id] = true;
          list.packing_list_ready = true;
          return json(route, { success: true, packing_list_ready: true });
        }
        assert.strictEqual(request.method(), 'GET');
        state.downloads.push({ id, invoice: list.numero_invoice, date: list.order_date });
        if (state.downloadGate) await state.downloadGate;
        if (!packingReady[id]) return json(route, { detail: 'Packing List não incluído' }, 404);
        if (id === 'error') return json(route, { detail: 'Erro sintético real no documento' }, 503);
        return route.fulfill({ status: 200, contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          headers: { 'Content-Disposition': 'attachment; filename="packing_list_synthetic.xlsx"' }, body: Buffer.from('PK synthetic document') });
      }
      const detail = pathname.match(/^\/api\/medias-compras\/listas-pedidos\/([^/]+)$/);
      if (detail) {
        const list = lists.find(item => item.id === decodeURIComponent(detail[1]));
        assert(list);
        if (request.method() === 'GET') return json(route, { lista: { ...structuredClone(list), packing_list_ready: packingReady[list.id] } });
        assert.strictEqual(request.method(), 'PUT');
        const payload = request.postDataJSON();
        assert.strictEqual(Object.keys(payload).length, 1, 'Salvamento deve enviar apenas o campo editado');
        state.updates.push({ id: list.id, payload });
        const gate = state.putGate;
        const fail = state.failPUT;
        state.failPUT = false;
        if (gate) await gate;
        if (fail) return json(route, { detail: 'Falha sintética no salvamento' }, 503);
        Object.assign(list, payload);
        return json(route, { lista: structuredClone(list) });
      }
      if (pathname.startsWith('/api/')) return json(route, {});
      if (pathname.startsWith('/importacoes_lista.html')) throw new Error('Packing List abriu o detalhe indevidamente');
      return route.fulfill({ status: 200, contentType: 'application/javascript', body: '' });
    } catch (error) {
      if (request.failure()) return;
      state.routeErrors.push(error.message);
      return json(route, { detail: error.message }, 500);
    }
  });
}

async function download(page, id) {
  const event = page.waitForEvent('download');
  await button(page, id).click();
  const result = await event;
  assert.strictEqual(result.suggestedFilename(), 'packing_list_synthetic.xlsx');
  await page.waitForFunction(id => !document.querySelector('[data-packing-lista-id="' + id + '"]').disabled, id);
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 960 }, acceptDownloads: true });
    page.on('pageerror', error => state.errors.push(error.message));
    page.on('dialog', async dialog => { state.alerts.push(dialog.message()); await dialog.dismiss(); });
    page.on('filechooser', () => { state.chooserCount++; });
    let downloadEvents = 0;
    page.on('download', () => { downloadEvents++; });
    await page.addInitScript(() => localStorage.setItem('importacoes_lista_resumo_editavel', JSON.stringify({ legacy: { numero_invoice: 'INV-LEGACY-LOCAL' } })));
    await routes(page);
    await page.goto('http://jk.test/importacoes.html');
    await button(page, 'empty').waitFor();
    const originalURL = page.url();

    // Empty state, accessible modal, explicit chooser, cancellation and keyboard activation.
    await button(page, 'empty').click();
    await modal(page).waitFor({ state: 'visible' });
    assert.strictEqual(await modal(page).locator('h2').textContent(), 'Incluir Packing List');
    assert.strictEqual(await input(page).getAttribute('accept'), '.xlsx');
    assert.strictEqual(state.chooserCount, 0, 'O modal não pode disparar filepicker automaticamente');
    assert.strictEqual(await page.evaluate(() => window.__packingXss), undefined);
    assert((await modal(page).textContent()).includes('<img src=x'));
    if (previewDirectory) { fs.mkdirSync(previewDirectory, { recursive: true }); await page.screenshot({ path: path.join(previewDirectory, 'packing-incluir-desktop.png') }); }
    await modal(page).locator('[data-packing-list-cancelar]').click();
    assert.strictEqual(state.uploads.length, 0);
    await button(page, 'empty').press('Enter');
    await modal(page).waitFor({ state: 'visible' });
    await page.keyboard.press('Escape');
    await modal(page).waitFor({ state: 'detached' });
    console.log('Packing List: modal acessível, cancelamento e nenhuma navegação/filepicker automático: OK');

    // Invalid file, single upload, no automatic download, following click downloads.
    await button(page, 'empty').click();
    await input(page).setInputFiles({ ...file, name: 'invalid.txt' });
    assert.strictEqual(await modal(page).locator('[data-packing-list-enviar]').isDisabled(), true);
    await input(page).setInputFiles(file);
    const uploadGate = deferred(); state.uploadGate = uploadGate.promise;
    await modal(page).locator('form').evaluate(form => { form.requestSubmit(); form.requestSubmit(); });
    await until(() => state.uploads.length === 1);
    assert.strictEqual(await modal(page).locator('[data-packing-list-enviar]').isDisabled(), true);
    uploadGate.resolve(); state.uploadGate = null;
    await modal(page).waitFor({ state: 'detached' });
    assert.strictEqual(downloadEvents, 0);
    await download(page, 'empty');
    assert.strictEqual(state.downloads.at(-1).id, 'empty');
    assert.strictEqual(state.uploads.length, 1);
    console.log('Packing List: upload multipart único e download somente no próximo clique: OK');

    // Stale ready flag/404 enters the same modal; real errors preserve download button.
    await button(page, 'fallback').click();
    await modal(page).waitFor({ state: 'visible' });
    await modal(page).locator('[data-packing-list-cancelar]').click();
    await button(page, 'error').click();
    await until(() => state.alerts.includes('Erro sintético real no documento'));
    assert.strictEqual(await modal(page).count(), 0);
    assert.strictEqual(await button(page, 'error').isDisabled(), false);
    console.log('Packing List: 404 abre inclusão; erro real permanece erro: OK');

    // Upload error remains reviewable and permits retry.
    await button(page, 'upload-error').click(); await input(page).setInputFiles(file);
    state.failUpload = true;
    await modal(page).locator('[data-packing-list-enviar]').click();
    await page.waitForFunction(() => document.querySelector('[data-packing-list-status]')?.textContent.includes('Planilha sintética inválida'));
    assert.strictEqual(await modal(page).locator('[data-packing-list-enviar]').isDisabled(), false);
    const eventsBeforeRetry = downloadEvents;
    await modal(page).locator('[data-packing-list-enviar]').click();
    await modal(page).waitFor({ state: 'detached' });
    assert.strictEqual(downloadEvents, eventsBeforeRetry);
    console.log('Packing List: falha de upload e retry sem download automático: OK');

    // A cancelled request may finish server-side; the next click checks its current state.
    await button(page, 'cancel').click(); await input(page).setInputFiles(file);
    const cancelGate = deferred(); state.uploadGate = cancelGate.promise;
    const uploadsBeforeCancel = state.uploads.length;
    await modal(page).locator('[data-packing-list-enviar]').click();
    await until(() => state.uploads.length === uploadsBeforeCancel + 1);
    await modal(page).locator('[data-packing-list-cancelar]').click();
    await modal(page).waitFor({ state: 'detached' });
    cancelGate.resolve(); state.uploadGate = null;
    await until(() => packingReady.cancel);
    await download(page, 'cancel');
    console.log('Packing List: cancelamento ativo e consulta posterior ao servidor: OK');

    // Pending invoice/date saves finish before the document request.
    for (const [field, selector, value] of [
      ['numero_invoice', '[data-invoice-pill][data-invoice-lista-id="ready"]', 'INV-CURRENT'],
      ['order_date', '[data-order-date-pill][data-lista-order-date-id="ready"]', '2026-10-17'],
    ]) {
      const gate = deferred(); state.putGate = gate.promise;
      await page.locator('#listaWrap ' + selector).dblclick();
      const editor = page.locator('#listaWrap ' + selector + ' input');
      await editor.fill(value); await editor.press('Enter');
      await until(() => state.updates.some(update => update.id === 'ready' && update.payload[field] === value));
      const before = state.downloads.length;
      const result = page.waitForEvent('download');
      await button(page, 'ready').click();
      assert.strictEqual(state.downloads.length, before);
      gate.resolve(); state.putGate = null; await result;
      assert.strictEqual(state.downloads.at(-1)[field === 'order_date' ? 'date' : 'invoice'], value);
      await page.waitForFunction(() => !document.querySelector('[data-packing-lista-id="ready"]').disabled);
    }
    await download(page, 'legacy');
    assert.strictEqual(state.downloads.at(-1).invoice, 'INV-LEGACY-LOCAL');
    assert(state.updates.some(update => update.id === 'legacy' && update.payload.numero_invoice === 'INV-LEGACY-LOCAL'));
    console.log('Packing List: aguarda invoice/data e persiste invoice legado antes de gerar: OK');

    // Duplicate clicks and failed saves never generate duplicate/stale documents.
    const gate = deferred(); state.downloadGate = gate.promise;
    const before = state.downloads.length;
    const result = page.waitForEvent('download');
    await button(page, 'ready').evaluate(element => { element.click(); element.click(); });
    await until(() => state.downloads.length === before + 1);
    gate.resolve(); state.downloadGate = null; await result;
    await page.waitForFunction(() => !document.querySelector('[data-packing-lista-id="ready"]').disabled);
    assert.strictEqual(state.downloads.length, before + 1);
    const failGate = deferred(); state.putGate = failGate.promise; state.failPUT = true;
    await page.locator('#listaWrap [data-invoice-lista-id="ready"]').dblclick();
    const editor = page.locator('#listaWrap [data-invoice-lista-id="ready"] input');
    await editor.fill('INV-FAILED'); await editor.press('Enter');
    await until(() => state.updates.some(update => update.payload.numero_invoice === 'INV-FAILED'));
    const countBeforeFailure = state.downloads.length;
    await button(page, 'ready').click();
    failGate.resolve(); state.putGate = null;
    await until(() => state.alerts.some(message => message.includes('Salve o número da Invoice antes de gerar o Packing List')));
    assert.strictEqual(state.downloads.length, countBeforeFailure);
    await editor.press('Escape');
    console.log('Packing List: duplo clique e falha de salvamento protegidos: OK');

    await page.setViewportSize({ width: 390, height: 844 });
    await button(page, 'fallback').click(); await modal(page).waitFor({ state: 'visible' });
    const bounds = await modal(page).boundingBox();
    assert(bounds.x >= 0 && bounds.x + bounds.width <= 390, 'Modal deve caber na tela móvel');
    if (previewDirectory) await page.screenshot({ path: path.join(previewDirectory, 'packing-incluir-mobile.png') });
    await modal(page).locator('[data-packing-list-cancelar]').click();
    assert.strictEqual(page.url(), originalURL);
    assert.strictEqual(state.chooserCount, 0);
    assert.deepStrictEqual(state.errors, []);
    assert.deepStrictEqual(state.routeErrors, []);
    console.log('Packing List: 8 cenários de navegador aprovados; todas as requisições simuladas.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
