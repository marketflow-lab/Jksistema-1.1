'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const downloadDirectory = process.env.JK_ETIQUETAS_DOWNLOAD_DIR;
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const ids = ['ready', 'fallback', 'legacy', 'missing', 'conflict', 'error', 'empty', 'invalid', 'preflight-error'];
const lists = ids.map(id => ({
  id, nome_lista: id === 'missing' ? 'Lista <img src=x onerror="window.__etiquetasXss=1">' : 'Pedido sintético ' + id,
  loja: 'Loja sintética', store_id: 'synthetic-store', status: 'Analisando orçamento',
  supplier: 'Fornecedor sintético', fornecedor_id: 'supplier-a', nacionalizador: 'Empresa sintética',
  numero_invoice: id === 'legacy' ? '' : 'INV-' + id.toUpperCase(), order_date: '2026-10-05',
  packing_list_ready: true, total_itens: 1, total_quantidade: 2, total_m3: 0.03, total_usd: 20,
  itens: [{ SKU: 'SYN-001', Quantidade: 2, 'Valor unidade': 10, 'Valor total': 20 }],
}));
const pdf = (() => {
  let text = '%PDF-1.4\n';
  const offsets = [0];
  const objects = ['<< /Type /Catalog /Pages 2 0 R >>', '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << >> >>'];
  objects.forEach((body, index) => { offsets.push(Buffer.byteLength(text)); text += (index + 1) + ' 0 obj\n' + body + '\nendobj\n'; });
  const start = Buffer.byteLength(text);
  text += 'xref\n0 4\n0000000000 65535 f \n' + offsets.slice(1).map(offset => String(offset).padStart(10, '0') + ' 00000 n \n').join('');
  text += 'trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n' + start + '\n%%EOF\n';
  return Buffer.from(text);
})();
const state = { requests: [], downloads: [], updates: [], uploads: [], alerts: [], errors: [], routeErrors: [],
  downloadEvents: 0, chooserCount: 0, downloadGate: null, uploadGate: null, putGate: null, failPUT: false, failUpload: false };
const packingReady = Object.fromEntries(ids.map(id => [id, id !== 'missing']));
const json = (route, body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
const button = (page, id) => page.locator('[data-etiquetas][data-etiquetas-lista-id="' + id + '"]');
const modal = page => page.locator('dialog.packing-list-dialog');
const fileInput = page => modal(page).locator('input[type=file]');
const xlsx = { name: 'synthetic-packing.xlsx', mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buffer: Buffer.from('PK synthetic packing') };
async function until(predicate) {
  const deadline = Date.now() + 10000;
  while (!predicate()) { assert(Date.now() < deadline, 'A operação sintética excedeu o tempo esperado'); await new Promise(resolve => setTimeout(resolve, 20)); }
}
async function idle(page, id) {
  await page.waitForFunction(id => !document.querySelector('[data-etiquetas-lista-id="' + id + '"]').disabled, id);
  assert.strictEqual(await button(page, id).getAttribute('aria-busy'), null);
  assert.strictEqual((await button(page, id).textContent()).trim(), 'Etiquetas');
}
async function download(page, id, filename, key = null) {
  const before = state.downloadEvents;
  const result = page.waitForEvent('download');
  if (key) await button(page, id).press(key); else await button(page, id).click();
  const document = await result;
  assert.strictEqual(document.suggestedFilename(), filename);
  const payload = await page.evaluate(async () => await window.__etiquetasDownloadPayloads.at(-1));
  assert.strictEqual(payload.type, 'application/pdf');
  assert.deepStrictEqual(Buffer.from(payload.bytes), pdf, 'Blob do download deve preservar o PDF recebido');
  assert.deepStrictEqual(fs.readFileSync(await document.path()), pdf, 'Arquivo baixado deve preservar o PDF recebido');
  assert.strictEqual(await document.failure(), null);
  await idle(page, id);
  assert.strictEqual(state.downloadEvents, before + 1);
}
async function alertWithoutDownload(page, id, expected) {
  const before = state.downloadEvents;
  const alertCount = state.alerts.length;
  await button(page, id).click();
  await until(() => state.alerts.length > alertCount);
  assert(state.alerts.at(-1).includes(expected), 'Mensagem deve explicar a falha: ' + state.alerts.at(-1));
  await idle(page, id);
  assert.strictEqual(state.downloadEvents, before, 'Falha não pode baixar documento');
  assert.strictEqual(await modal(page).count(), 0, 'Somente ausência de packing deve abrir inclusão');
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
      if (pathname.startsWith('/api/')) {
        assert.strictEqual(request.headers().authorization, 'Bearer synthetic', 'Toda requisição deve usar autenticação');
        state.requests.push({ pathname, method: request.method() });
      }
      if (pathname === '/api/lojas') return json(route, [{ nome: 'Loja sintética', store_id: 'synthetic-store' }]);
      if (pathname === '/api/cadastro/fornecedores') return json(route, [{ id: 'supplier-a', nome_empresa: 'Fornecedor sintético' }]);
      if (pathname === '/api/cadastro/nacionalizadores') return json(route, []);
      if (pathname === '/api/medias-compras/listas-pedidos') return json(route, { listas: structuredClone(lists) });
      const labels = pathname.match(/^\/api\/medias-compras\/listas-pedidos\/([^/]+)\/etiquetas$/);
      if (labels) {
        const id = decodeURIComponent(labels[1]);
        const list = lists.find(item => item.id === id);
        assert(list, 'Etiquetas devem pertencer à lista sintética');
        assert.strictEqual(request.method(), 'GET');
        state.downloads.push({ id, invoice: list.numero_invoice, date: list.order_date });
        if (state.downloadGate) await state.downloadGate;
        if (!packingReady[id]) return json(route, { detail: 'Packing List não incluído' }, 404);
        if (id === 'conflict') return json(route, { detail: 'Packing List sintético contém caixas inconsistentes' }, 409);
        if (id === 'error') return json(route, { detail: 'Erro sintético ao gerar etiquetas' }, 500);
        const headers = id === 'fallback' ? {} : { 'Content-Disposition': "attachment; filename*=UTF-8''Etiquetas%20sint%C3%A9ticas.pdf" };
        return route.fulfill({ status: 200, contentType: id === 'invalid' ? 'text/html' : 'application/pdf', headers,
          body: id === 'empty' ? Buffer.alloc(0) : pdf });
      }
      const upload = pathname.match(/^\/api\/medias-compras\/listas-pedidos\/([^/]+)\/packing-list\/importar-excel$/);
      if (upload) {
        const id = decodeURIComponent(upload[1]);
        assert.strictEqual(request.method(), 'POST');
        assert(request.headers()['content-type'].startsWith('multipart/form-data; boundary='));
        assert(request.postDataBuffer().includes(Buffer.from('name="file"')));
        assert(request.postDataBuffer().includes(Buffer.from('filename="synthetic-packing.xlsx"')));
        state.uploads.push(id);
        const fail = state.failUpload; state.failUpload = false;
        if (state.uploadGate) await state.uploadGate;
        if (fail) return json(route, { detail: 'Planilha sintética inválida' }, 400);
        packingReady[id] = true;
        lists.find(item => item.id === id).packing_list_ready = true;
        return json(route, { success: true, packing_list_ready: true });
      }
      const detail = pathname.match(/^\/api\/medias-compras\/listas-pedidos\/([^/]+)$/);
      if (detail) {
        const list = lists.find(item => item.id === decodeURIComponent(detail[1]));
        assert(list);
        if (request.method() === 'GET') {
          assert.strictEqual(url.searchParams.get('somente_salvos'), 'true', 'Consulta prévia deve usar dados salvos');
          if (list.id === 'preflight-error') return json(route, { detail: 'Falha sintética na consulta da lista' }, 503);
          return json(route, { lista: { ...structuredClone(list), packing_list_ready: packingReady[list.id] } });
        }
        assert.strictEqual(request.method(), 'PUT');
        const payload = request.postDataJSON();
        assert.strictEqual(Object.keys(payload).length, 1, 'Salvar somente o campo editado');
        state.updates.push({ id: list.id, payload });
        const fail = state.failPUT; state.failPUT = false;
        if (state.putGate) await state.putGate;
        if (fail) return json(route, { detail: 'Falha sintética no salvamento' }, 503);
        Object.assign(list, payload);
        return json(route, { lista: structuredClone(list) });
      }
      if (pathname.endsWith('/packing-list')) throw new Error('Etiquetas não devem baixar o packing intermediário');
      if (pathname.startsWith('/importacoes_lista.html')) throw new Error('Etiquetas abriu o detalhe da lista indevidamente');
      if (pathname.startsWith('/api/')) return json(route, {});
      return route.fulfill({ status: 200, contentType: 'application/javascript', body: '' });
    } catch (error) {
      if (request.failure()) return;
      state.routeErrors.push(error.message);
      return json(route, { detail: error.message }, 500);
    }
  });
}

(async () => {
  if (downloadDirectory) fs.mkdirSync(downloadDirectory, { recursive: true });
  const browser = await chromium.launch({ headless: true, ...(downloadDirectory ? { downloadsPath: downloadDirectory } : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 960 }, acceptDownloads: true });
    page.on('pageerror', error => state.errors.push(error.message));
    page.on('dialog', async dialog => { state.alerts.push(dialog.message()); await dialog.dismiss(); });
    page.on('filechooser', () => { state.chooserCount++; });
    page.on('download', () => { state.downloadEvents++; });
    await page.addInitScript(() => {
      localStorage.setItem('importacoes_lista_resumo_editavel', JSON.stringify({ legacy: { numero_invoice: 'INV-LEGACY-LOCAL' } }));
      window.__etiquetasDownloadPayloads = [];
      const original = URL.createObjectURL;
      URL.createObjectURL = function(blob) {
        window.__etiquetasDownloadPayloads.push(blob.arrayBuffer().then(buffer => ({ type: blob.type, bytes: Array.from(new Uint8Array(buffer)) })));
        return original.call(this, blob);
      };
    });
    await routes(page);
    await page.goto('http://jk.test/importacoes.html');
    await button(page, 'ready').waitFor();
    const originalURL = page.url();
    assert.strictEqual(await page.locator('[data-etiquetas]').count(), lists.length);

    await download(page, 'ready', 'Etiquetas sintéticas.pdf');
    const documentRequests = state.requests.filter(request => request.pathname.includes('/listas-pedidos/ready'));
    assert.deepStrictEqual(documentRequests.map(request => request.pathname.split('/').at(-1)), ['ready', 'etiquetas']);
    await download(page, 'fallback', 'Etiquetas_Externas_INV-FALLBACK.pdf');
    await download(page, 'legacy', 'Etiquetas sintéticas.pdf');
    assert.strictEqual(state.downloads.at(-1).invoice, 'INV-LEGACY-LOCAL');
    assert(state.updates.some(update => update.id === 'legacy' && update.payload.numero_invoice === 'INV-LEGACY-LOCAL'));
    console.log('Etiquetas: PDF autenticado, arquivo íntegro, nome UTF-8/fallback e invoice legado: OK');

    const gate = deferred(); state.downloadGate = gate.promise;
    const beforeDuplicate = state.downloads.length;
    const result = page.waitForEvent('download');
    await button(page, 'ready').evaluate(element => { element.click(); element.click(); });
    await until(() => state.downloads.length === beforeDuplicate + 1);
    assert.strictEqual(await button(page, 'ready').isDisabled(), true);
    assert.strictEqual(await button(page, 'ready').getAttribute('aria-busy'), 'true');
    gate.resolve(); state.downloadGate = null; await result; await idle(page, 'ready');
    assert.strictEqual(state.downloads.length, beforeDuplicate + 1);
    console.log('Etiquetas: duplo clique protegido e botão restaurado após a resposta: OK');

    for (const [id, message] of [
      ['conflict', 'caixas inconsistentes'], ['error', 'Erro sintético ao gerar etiquetas'],
      ['empty', 'PDF de etiquetas foi gerado vazio'], ['invalid', 'não retornou um PDF de etiquetas válido'],
      ['preflight-error', 'Falha sintética na consulta da lista'],
    ]) await alertWithoutDownload(page, id, message);
    assert(!state.downloads.some(item => item.id === 'preflight-error'), 'Falha da consulta deve impedir geração');
    console.log('Etiquetas: 409, 500, resposta vazia, MIME inválido e falha da consulta: OK');

    const beforeMissing = state.downloadEvents;
    await button(page, 'missing').click();
    await modal(page).waitFor({ state: 'visible' });
    assert((await modal(page).locator('[data-packing-list-contexto]').textContent()).includes('para gerar as etiquetas'));
    assert.strictEqual(await fileInput(page).getAttribute('accept'), '.xlsx');
    assert.strictEqual(await page.evaluate(() => window.__etiquetasXss), undefined);
    assert((await modal(page).textContent()).includes('<img src=x'));
    assert.strictEqual(state.chooserCount, 0, 'Inclusão exige seleção explícita do usuário');
    await page.keyboard.press('Escape'); await modal(page).waitFor({ state: 'detached' });
    await button(page, 'missing').press('Enter'); await modal(page).waitFor({ state: 'visible' });
    await fileInput(page).setInputFiles({ ...xlsx, name: 'invalid.txt' });
    assert.strictEqual(await modal(page).locator('[data-packing-list-enviar]').isDisabled(), true);
    await fileInput(page).setInputFiles(xlsx);
    state.failUpload = true;
    await modal(page).locator('[data-packing-list-enviar]').click();
    await page.waitForFunction(() => document.querySelector('[data-packing-list-status]')?.textContent.includes('Planilha sintética inválida'));
    assert.strictEqual(await modal(page).locator('[data-packing-list-enviar]').isDisabled(), false);
    const uploadGate = deferred(); state.uploadGate = uploadGate.promise;
    const uploadsBeforeRetry = state.uploads.length;
    await modal(page).locator('form').evaluate(form => { form.requestSubmit(); form.requestSubmit(); });
    await until(() => state.uploads.length === uploadsBeforeRetry + 1);
    assert.strictEqual(await modal(page).locator('[data-packing-list-enviar]').isDisabled(), true);
    uploadGate.resolve(); state.uploadGate = null;
    await modal(page).waitFor({ state: 'detached' });
    assert.strictEqual(state.downloadEvents, beforeMissing, 'Upload deve aguardar novo clique para baixar etiquetas');
    await download(page, 'missing', 'Etiquetas sintéticas.pdf');
    assert.strictEqual(state.uploads.length, uploadsBeforeRetry + 1, 'Envio duplicado deve ser bloqueado');
    console.log('Etiquetas: 404, modal seguro, teclado, upload multipart, falha/retry e novo clique: OK');

    for (const [field, selector, value] of [
      ['numero_invoice', '[data-invoice-pill][data-invoice-lista-id="ready"]', 'INV-CURRENT'],
      ['order_date', '[data-order-date-pill][data-lista-order-date-id="ready"]', '2026-10-17'],
    ]) {
      const saveGate = deferred(); state.putGate = saveGate.promise;
      await page.locator('#listaWrap ' + selector).dblclick();
      const editor = page.locator('#listaWrap ' + selector + ' input');
      await editor.fill(value); await editor.press('Enter');
      await until(() => state.updates.some(update => update.id === 'ready' && update.payload[field] === value));
      const before = state.downloads.length;
      const event = page.waitForEvent('download');
      await button(page, 'ready').click();
      assert.strictEqual(state.downloads.length, before, 'Documento deve aguardar salvamento pendente');
      saveGate.resolve(); state.putGate = null; await event; await idle(page, 'ready');
      assert.strictEqual(state.downloads.at(-1)[field === 'order_date' ? 'date' : 'invoice'], value);
    }
    for (const [field, selector, value, expected] of [
      ['numero_invoice', '[data-invoice-lista-id="ready"]', 'INV-FAILED', 'Salve o número da Invoice antes de gerar as etiquetas'],
      ['order_date', '[data-lista-order-date-id="ready"]', '2026-10-19', 'Salve a data do pedido antes de gerar as etiquetas'],
    ]) {
      const failGate = deferred(); state.putGate = failGate.promise; state.failPUT = true;
      await page.locator('#listaWrap ' + selector).dblclick();
      const editor = page.locator('#listaWrap ' + selector + ' input');
      await editor.fill(value); await editor.press('Enter');
      await until(() => state.updates.some(update => update.payload[field] === value));
      const before = state.downloads.length;
      const alerts = state.alerts.length;
      await button(page, 'ready').click();
      failGate.resolve(); state.putGate = null;
      await until(() => state.alerts.length > alerts && state.alerts.at(-1).includes(expected));
      await idle(page, 'ready');
      assert.strictEqual(state.downloads.length, before, 'Falha de salvamento deve impedir PDF desatualizado');
      await editor.press('Escape');
    }
    console.log('Etiquetas: aguarda invoice/data e impede PDF após falhas de salvamento: OK');

    await page.setViewportSize({ width: 390, height: 844 });
    for (const element of await page.locator('[data-etiquetas]').all()) {
      assert(await element.isVisible());
      const bounds = await element.evaluate(button => {
        const bounds = button.getBoundingClientRect();
        return { x: bounds.x, right: bounds.right, height: bounds.height, fits: button.scrollWidth <= button.clientWidth + 1 };
      });
      assert(bounds.x >= 0 && bounds.right <= 391 && bounds.height >= 32 && bounds.fits, 'Botão deve caber na tela estreita');
    }
    await download(page, 'ready', 'Etiquetas sintéticas.pdf', 'Space');
    packingReady.missing = false;
    await button(page, 'missing').click(); await modal(page).waitFor({ state: 'visible' });
    const bounds = await modal(page).boundingBox();
    assert(bounds.x >= 0 && bounds.x + bounds.width <= 390, 'Modal deve caber na tela estreita');
    await modal(page).locator('[data-packing-list-cancelar]').click();
    assert.strictEqual(page.url(), originalURL, 'Etiquetas deve preservar a página da lista');
    assert.strictEqual(state.chooserCount, 0);
    assert.deepStrictEqual(state.errors, []);
    assert.deepStrictEqual(state.routeErrors, []);
    console.log('Etiquetas: 6 grupos de cenários aprovados; todas as requisições simuladas, sem navegar ou acessar o app instalado.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
