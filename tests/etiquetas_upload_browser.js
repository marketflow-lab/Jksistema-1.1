'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'frontend_etiquetas.html'));
assert.deepStrictEqual(html, fs.readFileSync(path.join(root, 'static', 'frontend_etiquetas.html')),
  'As duas páginas devem oferecer o mesmo upload');
const assetTypes = {
  '.css': 'text/css', '.js': 'application/javascript', '.json': 'application/json',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp',
  '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.woff2': 'font/woff2',
};
const fixture = (name, mimeType = 'application/pdf', content = '%PDF-1.4\nSynthetic label\n%%EOF') =>
  ({ name, mimeType, buffer: Buffer.from(content) });
const state = { requests: [], errors: [], alerts: [] };
const previewDirectory = process.env.JK_ETIQUETAS_PREVIEW_DIR;
const group = (page, id) => page.locator('#' + id);
const names = (page, id) => page.locator('#' + id).evaluate(input => Array.from(input.files, file => file.name));

function multipart(request) {
  const boundary = (request.headers()['content-type'] || '').match(/boundary=(?:"([^"]+)"|([^;]+))/);
  assert(boundary, 'Processamento deve continuar usando multipart/form-data');
  const fields = {};
  for (const part of request.postDataBuffer().toString('utf8').split('--' + (boundary[1] || boundary[2]))) {
    const separator = part.indexOf('\r\n\r\n');
    if (separator < 0) continue;
    const header = part.slice(0, separator);
    const name = header.match(/Content-Disposition: form-data; name="([^"]+)"/i)?.[1];
    if (!name) continue;
    const filename = header.match(/filename="([^"]*)"/i)?.[1];
    const content = part.slice(separator + 4).replace(/\r\n$/, '');
    fields[name] = filename === undefined ? content : { filename, content };
  }
  return fields;
}

async function drop(page, groupId, files) {
  const transfer = await page.evaluateHandle(payload => {
    const data = new DataTransfer();
    for (const item of payload) data.items.add(new File([item.content], item.name, { type: item.type }));
    return data;
  }, files.map(file => ({ name: file.name, type: file.mimeType, content: file.buffer.toString('utf8') })));
  try {
    await group(page, groupId).dispatchEvent('dragenter', { dataTransfer: transfer });
    await group(page, groupId).dispatchEvent('dragover', { dataTransfer: transfer });
    await group(page, groupId).dispatchEvent('drop', { dataTransfer: transfer });
  } finally { await transfer.dispose(); }
}

async function chosen(page, groupId, inputId, filename) {
  assert.deepStrictEqual(await names(page, inputId), [filename]);
  assert.strictEqual(await group(page, groupId).locator('.upload-selection').isVisible(), true);
  assert.strictEqual(await group(page, groupId).locator('.upload-filename').textContent(), filename);
  assert.strictEqual(await group(page, groupId).locator('.upload-feedback').isVisible(), false);
}

async function remove(page, groupId, inputId) {
  await group(page, groupId).locator('.upload-remove').click();
  assert.deepStrictEqual(await names(page, inputId), []);
  assert.strictEqual(await group(page, groupId).locator('.upload-selection').isVisible(), false);
  assert.strictEqual(await group(page, groupId).locator('.upload-filename').textContent(), '');
  assert.strictEqual(await page.locator('#' + inputId).evaluate(input => document.activeElement === input), true,
    'Remover deve devolver o foco ao seletor de arquivos');
}

async function processUpload(page, store, expectedFiles) {
  const count = state.requests.length;
  await Promise.all([
    page.waitForResponse(response => new URL(response.url()).pathname === '/api/etiquetas/processar'),
    page.locator('#btn-processar').click(),
  ]);
  await page.waitForFunction(() => !document.getElementById('btn-processar').disabled);
  assert.strictEqual(state.requests.length, count + 1, 'Cada clique deve enviar um único processamento');
  const request = state.requests.at(-1);
  assert.strictEqual(request.store, store);
  assert.deepStrictEqual(Object.keys(request).sort(), ['store', ...Object.keys(expectedFiles)].sort());
  for (const [field, file] of Object.entries(expectedFiles)) {
    assert.deepStrictEqual(request[field], { filename: file.name, content: file.buffer.toString('utf8') },
      'O arquivo enviado deve ser exatamente o selecionado em ' + field);
  }
  assert.strictEqual(await page.locator('#qtd-etiquetas').textContent(), '3');
  assert.strictEqual(await page.locator('#result-section').isVisible(), true);
  assert.strictEqual(await page.locator('#btn-download-etiquetas').isVisible(), true);
  assert.strictEqual(await page.locator('#btn-download-lista').isVisible(), true);
  assert.strictEqual(await page.locator('#error-msg').isVisible(), false);
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
    page.on('pageerror', error => state.errors.push(error.message));
    page.on('dialog', async dialog => { state.alerts.push(dialog.message()); await dialog.dismiss(); });
    await page.addInitScript(() => {
      localStorage.setItem('access_token', 'synthetic-token');
      localStorage.setItem('user_data', JSON.stringify({ client_id: 'synthetic-client', username: 'synthetic-user' }));
      window.__uploadNameExecuted = false;
    });
    await page.route('**/*', async route => {
      const request = route.request();
      const pathname = new URL(request.url()).pathname;
      try {
        if (pathname === '/frontend_etiquetas.html') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
        if (pathname.startsWith('/etiquetas/') || pathname.startsWith('/assets/')) {
          const asset = path.join(root, 'static', pathname.slice(1));
          assert(fs.existsSync(asset), 'Asset da página deve existir: ' + pathname);
          return route.fulfill({ contentType: assetTypes[path.extname(pathname)] || 'application/octet-stream', body: fs.readFileSync(asset) });
        }
        if (pathname === '/api/etiquetas/processar') {
          assert.strictEqual(request.method(), 'POST');
          assert.strictEqual(request.headers().authorization, 'Bearer synthetic-token');
          state.requests.push(multipart(request));
          return route.fulfill({ contentType: 'application/json', body: JSON.stringify({
            success: true, qtd: 3, id_etiquetas: 'synthetic-labels', id_lista: 'synthetic-picking',
          }) });
        }
        assert(!pathname.startsWith('/api/'), 'Rota de API inesperada: ' + pathname);
        return route.fulfill({ contentType: 'application/javascript', body: '' });
      } catch (error) {
        state.errors.push(error.message);
        return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: error.message }) });
      }
    });
    await page.goto('http://jk.test/frontend_etiquetas.html');
    await page.waitForFunction(() => Array.from(document.querySelectorAll('#panel-processador img')).every(image => image.complete));
    const images = await page.locator('img[src^="/etiquetas/images/"], img[src="/assets/mercado-livre-symbol.svg"]').evaluateAll(
      elements => elements.map(image => ({ src: image.getAttribute('src'), loaded: image.complete && image.naturalWidth > 0 })));
    assert(images.length >= 7, 'A página deve carregar os logos e as ilustrações locais');
    assert(images.every(image => image.loaded), 'As imagens reais devem carregar com o MIME correto: ' + JSON.stringify(images));

    // O botão nativo continua abrindo o seletor, inclusive para nomes longos e não confiáveis.
    const hostileName = 'Etiquetas de expedição '.repeat(12) + '<img src=x onerror=window.__uploadNameExecuted=true>.PDF';
    const [chooser] = await Promise.all([
      page.waitForEvent('filechooser'),
      page.locator('#file-etiquetas').click(),
    ]);
    await chooser.setFiles(fixture(hostileName));
    await chosen(page, 'group-file-1', 'file-etiquetas', hostileName);
    assert.strictEqual(await group(page, 'group-file-1').locator('.upload-filename img').count(), 0,
      'Nome do arquivo deve ser exibido como texto, sem criar elementos HTML');
    assert.strictEqual(await page.evaluate(() => window.__uploadNameExecuted), false);
    await remove(page, 'group-file-1', 'file-etiquetas');

    await page.locator('#file-etiquetas').setInputFiles(fixture('arquivo-inválido.txt', 'text/plain', 'invalid'));
    assert.deepStrictEqual(await names(page, 'file-etiquetas'), [], 'Seleção nativa inválida deve ser recusada');
    assert((await group(page, 'group-file-1').locator('.upload-feedback').textContent()).includes('PDF'));
    assert.strictEqual(await group(page, 'group-file-1').locator('.upload-feedback').isVisible(), true);

    const mercadoLivre = fixture('Mercado Livre original.PDF');
    await drop(page, 'group-file-1', [mercadoLivre]);
    await chosen(page, 'group-file-1', 'file-etiquetas', mercadoLivre.name);
    await drop(page, 'group-file-1', [fixture('não-é-pdf.xml', 'application/xml', '<synthetic/>')]);
    assert.deepStrictEqual(await names(page, 'file-etiquetas'), [mercadoLivre.name], 'Drop inválido deve preservar o PDF válido');
    assert.strictEqual(await group(page, 'group-file-1').locator('.upload-filename').textContent(), mercadoLivre.name);
    assert.strictEqual(await group(page, 'group-file-1').locator('.upload-feedback').isVisible(), true);
    await drop(page, 'group-file-1', [fixture('primeiro.pdf'), fixture('segundo.pdf')]);
    assert.deepStrictEqual(await names(page, 'file-etiquetas'), [mercadoLivre.name], 'Drop múltiplo deve preservar o PDF válido');
    assert((await group(page, 'group-file-1').locator('.upload-feedback').textContent()).includes('um arquivo por vez'));
    await drop(page, 'group-file-1', [mercadoLivre]);
    await chosen(page, 'group-file-1', 'file-etiquetas', mercadoLivre.name);
    await processUpload(page, 'Mercado Livre', { file_etiquetas: mercadoLivre });

    await page.locator('input[name="loja"][value="Shopee"]').check();
    const shopee = fixture('Shopee etiquetas.pdf', 'application/pdf', '%PDF-1.4\nShopee synthetic\n%%EOF');
    await page.locator('#file-etiquetas').setInputFiles(shopee);
    await chosen(page, 'group-file-1', 'file-etiquetas', shopee.name);
    await processUpload(page, 'Shopee', { file_etiquetas: shopee });

    // Amazon mantém seu campo de nota fiscal e limpa o arquivo principal do fluxo existente.
    await page.locator('input[name="loja"][value="Amazon"]').check();
    assert.deepStrictEqual(await names(page, 'file-etiquetas'), []);
    assert.strictEqual(await group(page, 'group-file-1').locator('.upload-filename').textContent(), '');
    assert.strictEqual(await group(page, 'group-file-1').isVisible(), false);
    assert.strictEqual(await group(page, 'group-file-nf').isVisible(), true);
    const notaFiscal = fixture('Nota fiscal sintética.XML', 'application/xml', '<?xml version="1.0"?><syntheticNfe/>');
    await drop(page, 'group-file-nf', [notaFiscal]);
    await chosen(page, 'group-file-nf', 'file-nf', notaFiscal.name);
    await processUpload(page, 'Amazon', { file_nota_fiscal: notaFiscal });
    await remove(page, 'group-file-nf', 'file-nf');

    await page.locator('input[name="loja"][value="TikTok"]').check();
    const etiquetasTikTok = fixture('TikTok etiquetas.pdf', 'application/pdf', '%PDF-1.4\nTikTok labels\n%%EOF');
    const picking = fixture('TikTok picking.pdf', 'application/pdf', '%PDF-1.4\nTikTok picking\n%%EOF');
    await page.locator('#file-etiquetas').setInputFiles(etiquetasTikTok);
    await page.locator('#file-lista').setInputFiles(picking);
    await chosen(page, 'group-file-2', 'file-lista', picking.name);
    await page.locator('input[name="loja"][value="Amazon"]').check();
    assert.deepStrictEqual(await names(page, 'file-etiquetas'), [], 'Trocar para Amazon deve limpar o principal');
    assert.deepStrictEqual(await names(page, 'file-lista'), [picking.name], 'Trocar a loja deve preservar o picking já selecionado');
    await page.locator('input[name="loja"][value="TikTok"]').check();
    await chosen(page, 'group-file-2', 'file-lista', picking.name);
    await page.locator('#file-etiquetas').setInputFiles(etiquetasTikTok);
    await processUpload(page, 'TikTok', { file_etiquetas: etiquetasTikTok, file_lista: picking });
    if (previewDirectory) {
      fs.mkdirSync(previewDirectory, { recursive: true });
      await page.screenshot({ path: path.join(previewDirectory, 'etiquetas-upload-funcional-tiktok.png'), fullPage: true });
    }
    assert.deepStrictEqual(state.errors, []);
    assert.deepStrictEqual(state.alerts, []);
    assert.strictEqual(state.requests.length, 4);
    console.log('etiquetas_upload_browser: OK (seletor nativo, nome seguro, remoção/foco, drops, rejeições, XML, troca de loja, picking e multipart de 4 lojas)');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
