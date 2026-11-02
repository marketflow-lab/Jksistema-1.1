'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'frontend_etiquetas.html'));
assert.deepStrictEqual(html, fs.readFileSync(path.join(root, 'static', 'frontend_etiquetas.html')),
  'As duas páginas devem oferecer o mesmo editor de impressão');
const png = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3ioAAAAASUVORK5CYII=';
const stores = ['Mercado Livre', 'Shopee', 'TikTok', 'Amazon'];
const state = { previews: [], processing: [], errors: [], alerts: [], failPreview: false, delayedPreview: null };
const modal = page => page.locator('#etiquetas-layout-dialog');
const gear = (page, store) => page.locator('[data-layout-store="' + store + '"]');
const item = (page, name) => modal(page).locator('[data-layout-item="' + name + '"]');
const field = (page, name) => modal(page).locator('[data-layout-field="' + name + '"]');
const storageKey = (tenant, user, store) => 'jk:etiquetas-layout:v1:' + JSON.stringify([tenant, user, store]);
const fixture = { name: 'synthetic-label.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\nSynthetic label\n%%EOF') };
const previewDirectory = process.env.JK_ETIQUETAS_PREVIEW_DIR;
const clone = value => JSON.parse(JSON.stringify(value));
const json = (route, value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
async function until(predicate) {
  const deadline = Date.now() + 12000;
  while (!predicate()) {
    assert(Date.now() < deadline, 'A operação sintética excedeu o tempo esperado');
    await new Promise(resolve => setTimeout(resolve, 20));
  }
}

function defaultLayout() {
  return { version: 1, page: { width: 100, height: 150 }, items: {
    etiqueta: { x: 0, y: 0, width: 88, height: 138, rotation: 0, visible: true },
    sku: { x: 2, y: 140, width: 96, height: 9, rotation: 0, visible: true },
    danfe: { x: 90, y: 2, width: 9, height: 135, rotation: 90, visible: true },
  } };
}

function multipart(request) {
  const result = {};
  const body = request.postData() || '';
  const boundary = (request.headers()['content-type'] || '').match(/boundary=(?:"([^"]+)"|([^;]+))/);
  assert(boundary, 'A requisição deve usar multipart/form-data');
  for (const part of body.split('--' + (boundary[1] || boundary[2]))) {
    const separator = part.indexOf('\r\n\r\n');
    if (separator < 0) continue;
    const match = part.slice(0, separator).match(/Content-Disposition: form-data; name="([^"]+)"/i);
    if (match) result[match[1]] = part.slice(separator + 4).replace(/\r\n$/, '');
  }
  return result;
}

async function routes(page) {
  await page.route('**/*', async route => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    try {
      if (pathname === '/frontend_etiquetas.html') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
      if (pathname.startsWith('/etiquetas/')) {
        const asset = path.join(root, 'static', pathname.slice(1));
        assert(fs.existsSync(asset), 'Asset do editor deve existir: ' + pathname);
        return route.fulfill({ contentType: pathname.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(asset) });
      }
      if (pathname === '/api/etiquetas/layout/preview') {
        assert.strictEqual(request.headers().authorization, 'Bearer synthetic-token');
        const fields = multipart(request);
        assert(stores.includes(fields.store), 'Preview deve identificar a loja');
        const layout = fields.layout ? JSON.parse(fields.layout) : defaultLayout();
        state.previews.push({ store: fields.store, layout: clone(layout), uploaded: Boolean(fields.file_etiquetas) });
        if (state.delayedPreview) await state.delayedPreview;
        if (state.failPreview) return json(route, { detail: 'Falha sintética ao gerar prévia' }, 400);
        const embeddedDanfe = Boolean(fields.file_etiquetas) && ['Shopee', 'TikTok'].includes(fields.store);
        const separateDanfe = !embeddedDanfe || Boolean(layout.danfe_source);
        if (!separateDanfe) layout.items.danfe.visible = false;
        return json(route, { success: true, layout, sample: !fields.file_etiquetas,
          components: { etiqueta: png, sku: png, danfe: separateDanfe ? png : null },
          availability: { etiqueta: true, sku: true, danfe: separateDanfe }, preview: png, source: embeddedDanfe ? png : null,
          warnings: fields.store === 'Amazon' ? ['Processamento Amazon ainda não está disponível.'] : [] });
      }
      if (pathname === '/api/etiquetas/processar') {
        const fields = multipart(request);
        state.processing.push({ store: fields.store, layout: fields.layout ? JSON.parse(fields.layout) : null });
        return json(route, { success: true, qtd: 1, id_etiquetas: 'synthetic-result', id_lista: null });
      }
      return route.fulfill({ contentType: pathname.endsWith('.css') ? 'text/css' : 'application/javascript', body: '' });
    } catch (error) {
      state.errors.push(error.message);
      return json(route, { detail: error.message }, 500);
    }
  });
}

async function open(page, store) {
  const before = state.previews.length;
  await gear(page, store).click();
  await modal(page).waitFor({ state: 'visible' });
  await until(() => state.previews.length > before);
  await modal(page).locator('#layout-save').waitFor({ state: 'visible' });
  await page.waitForFunction(() => !document.getElementById('layout-save').disabled);
  assert((await modal(page).innerText()).includes(store), 'Editor deve identificar a loja aberta');
}

async function values(page) {
  return { x: Number(await field(page, 'x').inputValue()), y: Number(await field(page, 'y').inputValue()),
    width: Number(await field(page, 'width').inputValue()), height: Number(await field(page, 'height').inputValue()) };
}

async function fill(page, name, value) {
  await field(page, name).fill(String(value));
  await field(page, name).press('Tab');
}

function withinPage(value) {
  assert(value.x >= 0 && value.y >= 0 && value.width > 0 && value.height > 0, 'Item deve ter medidas positivas dentro da impressão');
  assert(value.x + value.width <= 100.05 && value.y + value.height <= 150.05,
    'Arraste e redimensionamento não podem deixar o item fora da impressão: ' + JSON.stringify(value));
}

function assertCrop(actual, expected) {
  assert(actual, 'Layout deve conter a região DANFE selecionada');
  for (const key of ['x', 'y', 'width', 'height']) {
    assert(Math.abs(actual[key] - expected[key]) < 0.000001,
      'Recorte em porcentagem deve virar frações normalizadas: ' + key + '=' + actual[key]);
  }
}

async function stored(page, tenant = 'tenant-a', user = 'user-a', store = 'Mercado Livre') {
  return page.evaluate(key => JSON.parse(localStorage.getItem(key) || 'null'), storageKey(tenant, user, store));
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.setDefaultTimeout(12000);
    page.on('pageerror', error => state.errors.push(error.message));
    page.on('dialog', async event => { state.alerts.push(event.message()); await event.dismiss(); });
    await page.addInitScript(() => {
      localStorage.setItem('access_token', 'synthetic-token');
      if (!localStorage.getItem('user_data')) localStorage.setItem('user_data', JSON.stringify({ client_id: 'tenant-a', username: 'user-a' }));
    });
    await routes(page);
    await page.goto('http://jk.test/frontend_etiquetas.html');
    assert.strictEqual(await page.locator('[data-layout-store]').count(), 4);
    for (const store of stores) {
      await open(page, store);
      assert.strictEqual(await page.locator('input[name="loja"]:checked').inputValue(), 'Mercado Livre',
        'A engrenagem deve abrir configurações sem mudar a loja selecionada');
      assert.strictEqual(await modal(page).locator('[data-layout-select]').count(), 3);
      await modal(page).locator('#layout-cancel').click();
      await modal(page).waitFor({ state: 'hidden' });
      assert.strictEqual(await gear(page, store).evaluate(element => document.activeElement === element), true,
        'O foco deve voltar à engrenagem que abriu o editor');
    }

    await open(page, 'Mercado Livre');
    assert((await modal(page).innerText()).toLowerCase().includes('amostra'), 'Prévia sem arquivo deve informar que é uma amostra');
    if (previewDirectory) await modal(page).screenshot({ path: path.join(previewDirectory, 'etiquetas-editor-desktop.png') });
    await modal(page).locator('[data-layout-select="sku"]').click();
    await fill(page, 'width', 60);
    await fill(page, 'height', 10);
    await fill(page, 'x', 8);
    await fill(page, 'y', 125);
    // A seleção muda os controles, mas preserva a ordem das camadas de impressão.
    await modal(page).locator('[data-layout-select="etiqueta"]').click();
    await fill(page, 'width', 60);
    await fill(page, 'height', 130);
    const coveredSku = await item(page, 'sku').boundingBox();
    const overlappingOriginal = await item(page, 'etiqueta').boundingBox();
    const overlapPoint = { x: coveredSku.x + coveredSku.width / 2, y: coveredSku.y + coveredSku.height / 4 };
    assert(overlapPoint.x < overlappingOriginal.x + overlappingOriginal.width && overlapPoint.y < overlappingOriginal.y + overlappingOriginal.height,
      'Cenário deve sobrepor SKU e etiqueta original');
    const layerAtSku = await page.evaluate(point => document.elementFromPoint(point.x, point.y)?.closest('[data-layout-item]')?.dataset.layoutItem, overlapPoint);
    assert.strictEqual(layerAtSku, 'sku', 'Selecionar a etiqueta original não pode cobrir o SKU que ficará por cima na impressão');
    const originalGrip = await item(page, 'etiqueta').locator('[data-layout-resize]').boundingBox();
    const gripLayer = await page.evaluate(point => {
      const target = document.elementFromPoint(point.x, point.y);
      return { item: target?.closest('[data-layout-item]')?.dataset.layoutItem, handle: target?.hasAttribute('data-layout-resize') };
    }, { x: originalGrip.x + originalGrip.width / 2, y: originalGrip.y + originalGrip.height / 2 });
    assert.deepStrictEqual(gripLayer, { item: 'etiqueta', handle: true },
      'A alça da etiqueta selecionada deve continuar acessível mesmo sobreposta ao SKU');
    await fill(page, 'width', 88);
    await fill(page, 'height', 138);
    await modal(page).locator('[data-layout-select="sku"]').click();
    const beforeDrag = await values(page);
    const box = await item(page, 'sku').boundingBox();
    assert(box && box.width > 0 && box.height > 0);
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 48, box.y + box.height / 2 + 20, { steps: 6 });
    await page.mouse.up();
    const dragged = await values(page);
    assert(dragged.x > beforeDrag.x && dragged.y > beforeDrag.y, 'Arrastar deve atualizar a posição em milímetros');
    withinPage(dragged);

    const handle = item(page, 'sku').locator('[data-layout-resize]').last();
    const grip = await handle.boundingBox();
    assert(grip, 'Item selecionado deve ter alça de redimensionamento');
    await page.mouse.move(grip.x + grip.width / 2, grip.y + grip.height / 2);
    await page.mouse.down();
    await page.mouse.move(grip.x + 1100, grip.y + 1100, { steps: 6 });
    await page.mouse.up();
    const resized = await values(page);
    assert(resized.width > dragged.width || resized.height > dragged.height, 'Alça deve alterar o tamanho do item');
    withinPage(resized);

    await fill(page, 'width', 40);
    await fill(page, 'height', 10);
    await fill(page, 'x', 10);
    await fill(page, 'y', 120);
    await item(page, 'sku').focus();
    await page.keyboard.press('ArrowRight');
    const keyboardMoved = await values(page);
    assert(keyboardMoved.x > 10, 'Seta do teclado deve mover o item selecionado');
    await field(page, 'rotation').selectOption('90');
    assert.strictEqual(await field(page, 'rotation').inputValue(), '90');
    await modal(page).locator('#layout-save').click();
    await modal(page).waitFor({ state: 'hidden' });
    const saved = await stored(page);
    assert(saved && saved.items.sku.rotation === 90);
    assert(Math.abs(saved.items.sku.x - keyboardMoved.x) < 0.1);
    assert.strictEqual(await stored(page, 'tenant-a', 'user-a', 'Shopee'), null, 'Salvar deve afetar somente a loja aberta');
    assert(!JSON.stringify(saved).includes('data:image') && !JSON.stringify(saved).includes('Synthetic label'),
      'Persistência deve guardar geometria, sem imagens ou conteúdo do arquivo');

    // Cancelar e Escape descartam somente o rascunho.
    await open(page, 'Mercado Livre');
    await modal(page).locator('[data-layout-select="sku"]').click();
    await fill(page, 'x', 30);
    await page.keyboard.press('Escape');
    await modal(page).waitFor({ state: 'hidden' });
    assert.deepStrictEqual(await stored(page), saved);
    await open(page, 'Mercado Livre');
    await modal(page).locator('[data-layout-select="sku"]').click();
    assert(Math.abs(Number(await field(page, 'x').inputValue()) - saved.items.sku.x) < 0.1);
    await fill(page, 'x', 25);
    await modal(page).locator('#layout-cancel').click();
    assert.deepStrictEqual(await stored(page), saved);

    // O arquivo real só pode ser associado à loja selecionada na tela.
    await page.locator('#file-etiquetas').setInputFiles(fixture);
    await open(page, 'Mercado Livre');
    const beforeReal = state.previews.length;
    await modal(page).locator('#layout-preview').click();
    await until(() => state.previews.length > beforeReal);
    await page.waitForFunction(() => !document.getElementById('layout-preview').disabled);
    assert.strictEqual(state.previews.at(-1).uploaded, true, 'Atualizar prévia deve enviar o arquivo da loja selecionada');
    await modal(page).locator('#layout-cancel').click();
    await open(page, 'Shopee');
    const beforeOtherStore = state.previews.length;
    await modal(page).locator('#layout-preview').click();
    await page.waitForTimeout(100);
    assert(!state.previews.slice(beforeOtherStore).some(request => request.uploaded),
      'Arquivo da loja selecionada não pode ser usado na prévia de outra loja');
    await modal(page).locator('#layout-cancel').click();

    await page.locator('#btn-processar').click();
    await page.waitForFunction(() => !document.getElementById('btn-processar').disabled);
    assert.deepStrictEqual(state.processing.at(-1), { store: 'Mercado Livre', layout: saved },
      'Processamento deve receber a mesma geometria salva no editor');

    // DANFE integrado só se torna uma camada separada depois da seleção explícita.
    await page.locator('input[name="loja"][value="Shopee"]').check();
    await page.locator('#file-etiquetas').setInputFiles(fixture);
    await open(page, 'Shopee');
    assert.strictEqual(await modal(page).locator('[data-layout-select="danfe"]').isDisabled(), true,
      'Código ainda integrado não pode parecer uma camada independente');
    assert.strictEqual(await modal(page).locator('#layout-crop-tools').isVisible(), true);
    await modal(page).locator('#layout-crop-open').click();
    await modal(page).locator('#layout-crop-panel').waitFor({ state: 'visible' });
    const cropField = name => modal(page).locator('[data-crop-field="' + name + '"]');
    await cropField('x').fill('25');
    await cropField('x').press('Tab');
    const beforeCancelCrop = state.previews.length;
    await modal(page).locator('#layout-crop-cancel').click();
    await modal(page).locator('#layout-crop-panel').waitFor({ state: 'hidden' });
    assert.strictEqual(state.previews.length, beforeCancelCrop, 'Cancelar a seleção não deve alterar o PDF ou o layout');
    assert.strictEqual(await stored(page, 'tenant-a', 'user-a', 'Shopee'), null);

    await modal(page).locator('#layout-crop-open').click();
    const canvas = modal(page).locator('#layout-crop-canvas');
    await canvas.scrollIntoViewIfNeeded();
    const cropCanvas = await canvas.boundingBox();
    assert(cropCanvas && cropCanvas.width > 0 && cropCanvas.height > 0);
    await page.mouse.move(cropCanvas.x + cropCanvas.width * 0.2, cropCanvas.y + cropCanvas.height * 0.7);
    await page.mouse.down();
    await page.mouse.move(cropCanvas.x + cropCanvas.width * 0.8, cropCanvas.y + cropCanvas.height * 0.9, { steps: 5 });
    await page.mouse.up();
    assert(Math.abs(Number(await cropField('x').inputValue()) - 20) < 1, 'Arraste deve selecionar a região horizontal do original');
    assert(Math.abs(Number(await cropField('height').inputValue()) - 20) < 1, 'Arraste deve selecionar a altura da região DANFE');
    for (const [name, value] of Object.entries({ x: 10, y: 70, width: 80, height: 20 })) {
      await cropField(name).fill(String(value));
      await cropField(name).press('Tab');
    }
    await modal(page).locator('#layout-crop-apply').click();
    await until(() => state.previews.at(-1).layout.danfe_source);
    await page.waitForFunction(() => !document.querySelector('[data-layout-select="danfe"]').disabled);
    assertCrop(state.previews.at(-1).layout.danfe_source, { x: 0.1, y: 0.7, width: 0.8, height: 0.2 });
    await modal(page).locator('[data-layout-select="danfe"]').click();
    await fill(page, 'width', 40);
    await fill(page, 'height', 15);
    await fill(page, 'x', 30);
    await fill(page, 'y', 110);
    const movedDanfe = await values(page);
    await modal(page).locator('[data-layout-select="sku"]').click();
    await fill(page, 'width', 50);
    await fill(page, 'x', 15);
    await modal(page).locator('[data-layout-select="danfe"]').click();
    assert.deepStrictEqual(await values(page), movedDanfe, 'Mover o SKU não pode alterar o DANFE separado');
    await modal(page).locator('#layout-save').click();
    await modal(page).waitFor({ state: 'hidden' });
    const croppedLayout = await stored(page, 'tenant-a', 'user-a', 'Shopee');
    assertCrop(croppedLayout.danfe_source, { x: 0.1, y: 0.7, width: 0.8, height: 0.2 });
    assert.strictEqual(croppedLayout.items.danfe.x, 30);
    assert(!JSON.stringify(croppedLayout).includes('data:image'), 'Só geometria e coordenadas do recorte podem ser persistidas');
    await page.locator('#btn-processar').click();
    await page.waitForFunction(() => !document.getElementById('btn-processar').disabled);
    assert.deepStrictEqual(state.processing.at(-1), { store: 'Shopee', layout: croppedLayout });

    await open(page, 'Shopee');
    await modal(page).locator('#layout-crop-clear').click();
    await until(() => state.previews.at(-1).store === 'Shopee' && !state.previews.at(-1).layout.danfe_source);
    await page.waitForFunction(() => document.querySelector('[data-layout-select="danfe"]').disabled);
    await modal(page).locator('#layout-cancel').click();
    assert.deepStrictEqual(await stored(page, 'tenant-a', 'user-a', 'Shopee'), croppedLayout,
      'Cancelar após limpar deve preservar a configuração salva');
    await open(page, 'Shopee');
    assert.strictEqual(await modal(page).locator('[data-layout-select="danfe"]').isDisabled(), false,
      'Reabrir deve restaurar o recorte salvo');
    await modal(page).locator('#layout-crop-clear').click();
    await until(() => !state.previews.at(-1).layout.danfe_source);
    await page.waitForFunction(() => !document.getElementById('layout-save').disabled);
    await modal(page).locator('#layout-save').click();
    await modal(page).waitFor({ state: 'hidden' });
    assert.strictEqual((await stored(page, 'tenant-a', 'user-a', 'Shopee')).danfe_source, undefined,
      'Salvar a limpeza deve devolver o código à etiqueta integrada');
    await page.locator('input[name="loja"][value="Mercado Livre"]').check();

    await page.evaluate(() => localStorage.setItem('user_data', JSON.stringify({ client_id: 'tenant-a', username: 'user-b' })));
    await open(page, 'Mercado Livre');
    assert.deepStrictEqual(state.previews.at(-1).layout, defaultLayout(), 'Outro usuário deve abrir o próprio layout padrão');
    await modal(page).locator('#layout-cancel').click();
    await page.evaluate(() => localStorage.setItem('user_data', JSON.stringify({ client_id: 'tenant-b', username: 'user-a' })));
    await open(page, 'Mercado Livre');
    assert.deepStrictEqual(state.previews.at(-1).layout, defaultLayout(), 'Outro cliente deve abrir o próprio layout padrão');
    await modal(page).locator('#layout-cancel').click();

    // Falhas de prévia devem permitir recuperação sem gravar layout incompleto.
    state.failPreview = true;
    await gear(page, 'Shopee').click();
    await modal(page).waitFor({ state: 'visible' });
    await page.waitForFunction(() => /Falha sintética/.test(document.getElementById('layout-status').textContent));
    assert.strictEqual(await stored(page, 'tenant-b', 'user-a', 'Shopee'), null);
    state.failPreview = false;
    await modal(page).locator('#layout-preview').click();
    await page.waitForFunction(() => !document.getElementById('layout-save').disabled);
    await modal(page).locator('#layout-cancel').click();

    await page.setViewportSize({ width: 390, height: 844 });
    await open(page, 'TikTok');
    const bounds = await modal(page).boundingBox();
    assert(bounds && bounds.x >= 0 && bounds.x + bounds.width <= 391, 'Editor deve caber na largura do celular');
    if (previewDirectory) await modal(page).screenshot({ path: path.join(previewDirectory, 'etiquetas-editor-mobile.png') });
    await modal(page).locator('[data-layout-select="danfe"]').click();
    await fill(page, 'x', 200);
    withinPage(await values(page));
    await modal(page).locator('#layout-cancel').click();
    assert.deepStrictEqual(state.errors, []);
    console.log('etiquetas_layout_browser: OK (4 lojas, ordem das camadas, arraste, limites, tamanho, teclado, recorte DANFE, cancelamento, isolamento, processamento e mobile)');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
