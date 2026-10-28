'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'frontend_etiquetas.html'));
assert.deepStrictEqual(html, fs.readFileSync(path.join(root, 'static', 'frontend_etiquetas.html')),
  'As duas páginas de Etiquetas devem oferecer o mesmo fluxo');
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3ioAAAAASUVORK5CYII=', 'base64');
const requests = [];
const errors = [];
const alerts = [];
const dialog = page => page.getByRole('dialog');
const choice = (page, value) => dialog(page).locator('[data-pdf-choice="' + value + '"]');
const button = (page, qr) => page.locator(qr ? '#btn-qrcode' : '#btn-avulsa');

async function idle(page, qr) {
  await page.waitForFunction(id => !document.getElementById(id).disabled, qr ? 'btn-qrcode' : 'btn-avulsa');
  assert.strictEqual(await dialog(page).isVisible(), false);
  assert.strictEqual(await button(page, qr).evaluate(element => document.activeElement === element), true,
    'Ao terminar ou cancelar, o foco deve voltar para o botão de geração');
}

async function start(page, mode, size = 'grande') {
  const qr = mode === 'qrcode';
  await page.locator(qr ? '#tab-qrcode' : '#tab-avulsa').click();
  if (qr) {
    await page.locator('#qrcode-link').fill('https://example.test/produto/001');
    await page.locator('#qrcode-titulo').fill('Produto sintético');
  } else {
    await page.locator('input[name="formato-avulsa"][value="' + mode + '"]').check();
    if (mode === 'editor') await page.locator('.label-editor-card').first().fill('SKU: 000123 - Etiqueta sintética');
    else await page.locator('#avulsa-grade [data-col="SKU"]').first().fill('000123');
  }
  const count = requests.length;
  await button(page, qr).click();
  assert.strictEqual(await button(page, qr).isDisabled(), true, 'Geração deve ficar desabilitada durante a escolha');
  if (mode === 'editor') {
    await page.getByRole('dialog', { name: 'Escolha o formato do PDF' }).waitFor();
    assert((await choice(page, 'grande').textContent()).includes('15x8cm'));
    await choice(page, size).click();
  }
  await page.getByRole('dialog', { name: 'Escolha a orientação da página' }).waitFor();
  assert.strictEqual(requests.length, count, 'A escolha deve ocorrer antes de enviar o PDF à API');
  assert.strictEqual(await choice(page, 'retrato').evaluate(element => document.activeElement === element), true);
}

async function generate(page, mode, size, orientation, keyboard = false) {
  const count = requests.length;
  await start(page, mode, size);
  // Uma segunda chamada durante a escolha não pode criar outro modal ou cancelar a primeira.
  await page.evaluate(qr => { void (qr ? gerarQrCodeLink() : gerarImpressaoAvulsa()); }, mode === 'qrcode');
  assert.strictEqual(requests.length, count);
  if (keyboard) {
    if (orientation === 'paisagem') await page.keyboard.press('Tab');
    await page.keyboard.press('Enter');
  } else await choice(page, orientation).click();
  await page.waitForFunction(() => document.querySelector('#pdf-choice-backdrop').style.display === 'none');
  await idle(page, mode === 'qrcode');
  assert.strictEqual(requests.length, count + 1, 'Cada geração deve enviar um único POST');
  const request = requests.at(-1);
  assert.strictEqual(request.body.orientacao, orientation);
  const endpoint = mode === 'qrcode' ? 'qrcode-link' : mode === 'editor' ? 'impressao-editor' : 'impressao-avulsa';
  assert.strictEqual(request.pathname, '/api/etiquetas/' + endpoint);
  if (mode === 'qrcode') assert.strictEqual(request.body.link, 'https://example.test/produto/001');
  else if (mode === 'editor') {
    assert.strictEqual(request.body.tamanho, size);
    assert(request.body.etiquetas[0].includes('000123'));
  } else {
    assert.strictEqual(request.body.formato, mode);
    assert.strictEqual(request.body.itens[0].sku, '000123');
  }
  return count;
}

async function cancel(page, mode, key) {
  const qr = mode === 'qrcode';
  const previewId = qr ? 'qrcode-preview-frame' : 'avulsa-preview-frame';
  const resultId = qr ? 'result-qrcode' : 'result-avulsa';
  const src = await page.locator('#' + previewId).getAttribute('src');
  assert(src, 'Uma prévia anterior deve existir para verificar o cancelamento');
  const count = requests.length;
  const calls = await page.evaluate(() => window.__pdfOpeningCalls);
  await start(page, mode);
  if (key) await page.keyboard.press('Escape');
  else await choice(page, '').click();
  await idle(page, qr);
  assert.strictEqual(requests.length, count, 'Cancelar não deve enviar POST');
  assert.strictEqual(await page.evaluate(() => window.__pdfOpeningCalls), calls, 'Cancelar não deve tentar abrir PDF');
  assert.strictEqual(await page.locator('#' + previewId).getAttribute('src'), src, 'Cancelar deve preservar a prévia anterior');
  assert.strictEqual(await page.locator('#' + resultId).isVisible(), true);
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
    page.on('pageerror', error => errors.push(error.message));
    page.on('dialog', async event => { alerts.push(event.message()); await event.dismiss(); });
    await page.addInitScript(() => {
      localStorage.setItem('access_token', 'synthetic-token');
      window.__pdfOpeningCalls = 0;
      window.electronAPI = { openEtiquetasPdfInChrome: async () => {
        window.__pdfOpeningCalls += 1;
        return { success: false, reason: 'synthetic-preview' };
      } };
    });
    await page.route('**/*', async route => {
      const request = route.request();
      const pathname = new URL(request.url()).pathname;
      if (pathname === '/frontend_etiquetas.html') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
      if (pathname.startsWith('/api/etiquetas/')) {
        assert.strictEqual(request.headers().authorization, 'Bearer synthetic-token');
        if (request.method() === 'POST') {
          requests.push({ pathname, body: request.postDataJSON() });
          return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ success: true, qtd: 1, id_etiquetas: 'pdf-' + requests.length }) });
        }
        if (pathname.startsWith('/api/etiquetas/preview/')) return route.fulfill({ contentType: 'image/png', body: png });
        if (pathname.startsWith('/api/etiquetas/download/')) return route.fulfill({ contentType: 'application/pdf', body: '%PDF-1.4\nsynthetic\n%%EOF' });
        throw new Error('Rota inesperada: ' + pathname);
      }
      return route.fulfill({ contentType: pathname.endsWith('.css') ? 'text/css' : 'application/javascript', body: '' });
    });
    await page.goto('http://jk.test/frontend_etiquetas.html');

    // Dados vazios são validados antes de solicitar orientação.
    await page.locator('#tab-qrcode').click();
    await button(page, true).click();
    assert.strictEqual(alerts.length, 1);
    assert(alerts.at(-1).includes('Informe o link'));
    assert.strictEqual(await dialog(page).isVisible(), false);
    await page.locator('#tab-avulsa').click();
    await button(page, false).click();
    assert.strictEqual(alerts.length, 2);
    assert(alerts.at(-1).includes('Informe pelo menos um item'));
    assert.strictEqual(requests.length, 0);

    for (const mode of ['grande', 'pequena', 'editor', 'qrcode']) {
      for (const orientation of ['retrato', 'paisagem']) {
        await generate(page, mode, 'grande', orientation, orientation === 'paisagem');
      }
      await cancel(page, mode, false);
      await cancel(page, mode, true);
    }
    // Editor pequeno mantém seu formato nos dois sentidos da página.
    await generate(page, 'editor', 'pequena', 'retrato');
    await generate(page, 'editor', 'pequena', 'paisagem');

    const preview = await page.locator('#avulsa-preview-frame').getAttribute('src');
    const beforeSizeCancel = requests.length;
    await button(page, false).click();
    await page.getByRole('dialog', { name: 'Escolha o formato do PDF' }).waitFor();
    await page.keyboard.press('Escape');
    await idle(page, false);
    assert.strictEqual(requests.length, beforeSizeCancel);
    assert.strictEqual(await page.locator('#avulsa-preview-frame').getAttribute('src'), preview);

    // O foco percorre somente os controles do modal nos dois sentidos.
    await start(page, 'qrcode');
    await page.keyboard.press('Shift+Tab');
    assert.strictEqual(await choice(page, '').evaluate(element => document.activeElement === element), true);
    await page.keyboard.press('Tab');
    assert.strictEqual(await choice(page, 'retrato').evaluate(element => document.activeElement === element), true);
    await page.keyboard.press('Tab');
    assert.strictEqual(await choice(page, 'paisagem').evaluate(element => document.activeElement === element), true);
    await page.keyboard.press('Tab');
    assert.strictEqual(await choice(page, '').evaluate(element => document.activeElement === element), true);
    await page.keyboard.press('Tab');
    assert.strictEqual(await choice(page, 'retrato').evaluate(element => document.activeElement === element), true);
    await page.keyboard.press('Escape');
    await idle(page, true);
    assert.deepStrictEqual(errors, []);
    assert.strictEqual(requests.length, 10);
    console.log('etiquetas_orientacao_browser: OK (10 gerações, 4 modos, cancelamento e teclado)');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
