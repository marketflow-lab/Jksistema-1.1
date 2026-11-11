'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const assetContentType = filename => ({
  '.css': 'text/css', '.js': 'application/javascript', '.json': 'application/json',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp',
  '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.woff2': 'font/woff2',
})[path.extname(filename)] || 'application/octet-stream';
const html = fs.readFileSync(path.join(root, 'frontend_etiquetas.html'));
assert.deepStrictEqual(html, fs.readFileSync(path.join(root, 'static', 'frontend_etiquetas.html')),
  'As duas páginas devem oferecer o mesmo editor de impressão');
const png = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3ioAAAAASUVORK5CYII=';
const stores = ['Mercado Livre', 'Shopee', 'TikTok', 'Amazon'];
const defaults = JSON.parse(fs.readFileSync(path.join(root, 'static', 'etiquetas', 'layout-defaults.json'), 'utf8'));
const state = { configs: [], previews: [], processing: [], errors: [], alerts: [], defaultsRequests: 0,
  failPreview: false, apiStatus: null, delayedPreview: null, delayedConfig: null, configLayout: null, canSelectDanfe: null,
  downloads: [], delayedDownload: null, failDownload: false };
const modal = page => page.locator('#etiquetas-layout-dialog');
const gear = (page, store) => page.locator('[data-layout-store="' + store + '"]');
const item = (page, name) => modal(page).locator('[data-layout-item="' + name + '"]');
const field = (page, name) => modal(page).locator('[data-layout-field="' + name + '"]');
const storageKey = (tenant, user, store) => 'jk:etiquetas-layout:v1:' + JSON.stringify([tenant, user, store]);
const fixture = { name: 'Etiquetas de expedição 01.PDF', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\nSynthetic label\n%%EOF') };
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

function defaultLayout(store = 'Mercado Livre') {
  assert(defaults.stores[store], 'Defaults estáticos devem incluir todas as lojas');
  return clone(defaults.stores[store]);
}

function previewResponse(store, layout, uploaded = false) {
  const embeddedDanfe = uploaded && ['Shopee', 'TikTok'].includes(store);
  const separateDanfe = !embeddedDanfe || Boolean(layout.danfe_source);
  if (!separateDanfe) layout.items.danfe.visible = false;
  return { success: true, layout, current_layout: defaultLayout(store),
    mode: JSON.stringify(layout) === JSON.stringify(defaultLayout(store)) ? 'padrao_atual' : 'personalizado', sample: !uploaded,
    capabilities: { preview: true, processing: store !== 'Amazon', embedded_danfe: ['Shopee', 'TikTok'].includes(store) },
    components: { etiqueta: png, sku: png, danfe: separateDanfe ? png : null },
    availability: { etiqueta: true, sku: true, danfe: separateDanfe }, preview: png,
    source: embeddedDanfe || state.canSelectDanfe !== null ? png : null,
    ...(state.canSelectDanfe === null ? {} : { can_select_danfe: state.canSelectDanfe }),
    warnings: store === 'Amazon' ? ['Processamento Amazon ainda não está disponível.'] : [] };
}

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
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
      if (pathname.startsWith('/etiquetas/') || pathname.startsWith('/assets/')) {
        const asset = path.join(root, 'static', pathname.slice(1));
        assert(fs.existsSync(asset), 'Asset do editor deve existir: ' + pathname);
        if (pathname.endsWith('/layout-defaults.json')) state.defaultsRequests += 1;
        return route.fulfill({ contentType: assetContentType(pathname), body: fs.readFileSync(asset) });
      }
      if (pathname === '/api/etiquetas/layout/config') {
        assert.strictEqual(request.method(), 'GET', 'Configuração inicial deve usar GET');
        assert.strictEqual(request.headers().authorization, 'Bearer synthetic-token');
        const store = new URL(request.url()).searchParams.get('store');
        assert(stores.includes(store), 'GET config deve identificar a loja');
        const layout = state.configLayout ? clone(state.configLayout) : defaultLayout(store);
        state.configs.push({ store, layout: clone(layout) });
        if (state.delayedConfig) await state.delayedConfig;
        if (state.apiStatus) return json(route, { detail: 'Method Not Allowed' }, state.apiStatus);
        if (state.failPreview) return json(route, { detail: 'Falha sintética ao gerar prévia' }, 400);
        return json(route, previewResponse(store, layout));
      }
      if (pathname === '/api/etiquetas/layout/preview') {
        assert.strictEqual(request.headers().authorization, 'Bearer synthetic-token');
        const fields = multipart(request);
        assert(stores.includes(fields.store), 'Preview deve identificar a loja');
        const layout = fields.layout ? JSON.parse(fields.layout) : defaultLayout(fields.store);
        state.previews.push({ store: fields.store, layout: clone(layout), uploaded: Boolean(fields.file_etiquetas) });
        if (state.delayedPreview) await state.delayedPreview;
        if (state.apiStatus) return json(route, { detail: 'Method Not Allowed' }, state.apiStatus);
        if (state.failPreview) return json(route, { detail: 'Falha sintética ao gerar prévia' }, 400);
        return json(route, previewResponse(fields.store, layout, Boolean(fields.file_etiquetas)));
      }
      if (pathname === '/api/etiquetas/processar') {
        const fields = multipart(request);
        state.processing.push({ store: fields.store, layout: fields.layout ? JSON.parse(fields.layout) : null });
        return json(route, { success: true, qtd: 1, id_etiquetas: 'synthetic-result', id_lista: 'synthetic-list' });
      }
      if (pathname.startsWith('/api/etiquetas/download/')) {
        assert.strictEqual(request.method(), 'GET');
        assert.strictEqual(request.headers().authorization, 'Bearer synthetic-token');
        const fileId = pathname.split('/').at(-1);
        assert(['synthetic-result', 'synthetic-list'].includes(fileId));
        state.downloads.push(fileId);
        if (fileId === 'synthetic-result') {
          if (state.delayedDownload) await state.delayedDownload;
          if (state.failDownload) return json(route, { detail: 'Falha sintética no download' }, 500);
        }
        return route.fulfill({ contentType: 'application/pdf', body: fixture.buffer });
      }
      return route.fulfill({ contentType: pathname.endsWith('.css') ? 'text/css' : 'application/javascript', body: '' });
    } catch (error) {
      state.errors.push(error.message);
      return json(route, { detail: error.message }, 500);
    }
  });
}

async function esperarStatusDownload(page, tipo, texto) {
  const id = 'status-download-' + tipo;
  await page.waitForFunction(({ id, texto }) => document.getElementById(id)?.textContent.trim() === texto, { id, texto });
  assert.strictEqual(await page.locator('#' + id).isVisible(), true);
}

async function verificarDownloadDoResultado(page) {
  const etiquetas = page.locator('#btn-download-etiquetas');
  const lista = page.locator('#btn-download-lista');
  const pending = deferred();
  state.delayedDownload = pending.promise;
  const nomeEtiqueta = 'E Etiquetas de expedição 01.PDF';
  const nomeLista = 'L Etiquetas de expedição 01.PDF';

  await etiquetas.click();
  await until(() => state.downloads.length > 0);
  await esperarStatusDownload(page, 'etiquetas', 'Baixando…');
  assert.strictEqual(await etiquetas.isDisabled(), true);
  await etiquetas.evaluate(button => { button.click(); button.click(); });

  assert.strictEqual(await lista.isEnabled(), true);
  const [downloadLista] = await Promise.all([
    page.waitForEvent('download'),
    lista.click(),
  ]);
  assert.strictEqual(downloadLista.suggestedFilename(), nomeLista);
  assert.strictEqual(await downloadLista.failure(), null);
  await esperarStatusDownload(page, 'lista', 'Download iniciado.');
  await esperarStatusDownload(page, 'etiquetas', 'Baixando…');
  assert.deepStrictEqual(state.downloads, ['synthetic-result', 'synthetic-list']);

  const primeiraEtiqueta = page.waitForEvent('download');
  state.delayedDownload = null;
  pending.resolve();
  const downloadEtiqueta = await primeiraEtiqueta;
  assert.strictEqual(downloadEtiqueta.suggestedFilename(), nomeEtiqueta);
  assert.strictEqual(await downloadEtiqueta.failure(), null);
  await esperarStatusDownload(page, 'etiquetas', 'Download iniciado.');
  assert.strictEqual(await etiquetas.isEnabled(), true);

  state.failDownload = true;
  await etiquetas.click();
  await esperarStatusDownload(page, 'etiquetas', 'Não foi possível baixar o arquivo. Tente novamente.');
  assert.strictEqual(await etiquetas.isEnabled(), true);

  state.failDownload = false;
  const [novaEtiqueta] = await Promise.all([page.waitForEvent('download'), etiquetas.click()]);
  assert.strictEqual(novaEtiqueta.suggestedFilename(), nomeEtiqueta);
  assert.strictEqual(await novaEtiqueta.failure(), null);
  await esperarStatusDownload(page, 'etiquetas', 'Download iniciado.');
  assert.deepStrictEqual(state.downloads, ['synthetic-result', 'synthetic-list', 'synthetic-result', 'synthetic-result']);
}

async function open(page, store) {
  const before = state.previews.length + state.configs.length;
  await gear(page, store).click();
  await modal(page).waitFor({ state: 'visible' });
  await until(() => state.previews.length + state.configs.length > before);
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

function withinPage(value, page = defaultLayout().page) {
  assert(value.x >= 0 && value.y >= 0 && value.width > 0 && value.height > 0, 'Item deve ter medidas positivas dentro da impressão');
  assert(value.x + value.width <= page.width + 0.05 && value.y + value.height <= page.height + 0.05,
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
      assert(Math.abs(defaultLayout(store).page.width - 283 * 25.4 / 72) < 0.001,
        'Largura padrão deve preservar os 283 pontos usados pela impressão atual');
      assert(Math.abs(defaultLayout(store).page.height - 425 * 25.4 / 72) < 0.001,
        'Altura padrão deve preservar os 425 pontos usados pela impressão atual');
    }

    // Os campos e as caixas aparecem antes da resposta lenta da API.
    const slowConfig = deferred();
    state.delayedConfig = slowConfig.promise;
    const beforeSlowConfig = state.configs.length;
    const postsBeforeConfig = state.previews.length;
    await gear(page, 'Mercado Livre').click();
    await modal(page).waitFor({ state: 'visible' });
    await until(() => state.configs.length > beforeSlowConfig);
    assert(state.defaultsRequests > 0, 'A configuração inicial deve carregar o asset de defaults');
    const pendingFields = await values(page);
    for (const key of ['x', 'y', 'width', 'height']) {
      assert(Math.abs(pendingFields[key] - defaultLayout().items.etiqueta[key]) <= 0.0051,
        'Defaults devem preencher as coordenadas antes do GET, com a precisão exibida: ' + key);
    }
    assert.strictEqual(await item(page, 'etiqueta').isVisible(), true);
    assert.strictEqual(await modal(page).locator('#layout-save').isDisabled(), true,
      'Salvar deve aguardar a prévia validada pelo servidor');
    assert.strictEqual(state.previews.length, postsBeforeConfig, 'Abrir configuração sem arquivo ou salvo deve usar somente GET');
    slowConfig.resolve();
    state.delayedConfig = null;
    await page.waitForFunction(() => !document.getElementById('layout-save').disabled);
    await modal(page).locator('#layout-cancel').click();

    // Headers podem chegar antes da troca de sessão e o corpo JSON só depois dela.
    state.configLayout = defaultLayout();
    Object.assign(state.configLayout.items.etiqueta, { x: 11, y: 17, width: 55, height: 90 });
    await page.evaluate(() => {
      const originalFetch = window.fetch;
      let release;
      const bodyGate = new Promise(done => { release = done; });
      window.__releaseLayoutJson = release;
      window.__layoutJsonWaiting = false;
      window.__layoutJsonSettled = false;
      window.__restoreLayoutFetch = () => { window.fetch = originalFetch; };
      window.fetch = async (...args) => {
        const response = await originalFetch(...args);
        if (String(args[0]).includes('/api/etiquetas/layout/config')) {
          const originalJson = response.json.bind(response);
          response.json = async () => {
            const body = await originalJson();
            window.__layoutJsonWaiting = true;
            await bodyGate;
            window.__layoutJsonSettled = true;
            return body;
          };
        }
        return response;
      };
    });
    await gear(page, 'Mercado Livre').click();
    await modal(page).waitFor({ state: 'visible' });
    await page.waitForFunction(() => window.__layoutJsonWaiting);
    const fieldsBeforeSessionChange = await values(page);
    assert.strictEqual(await item(page, 'etiqueta').locator('img').getAttribute('src'), null);
    await page.evaluate(() => {
      localStorage.setItem('user_data', JSON.stringify({ client_id: 'tenant-body-new', username: 'user-body-new' }));
      window.__releaseLayoutJson();
    });
    await page.waitForFunction(() => window.__layoutJsonSettled);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
    assert.deepStrictEqual(await values(page), fieldsBeforeSessionChange,
      'Resposta antiga não pode repintar as coordenadas depois de uma troca de sessão durante JSON');
    assert.strictEqual(await item(page, 'etiqueta').locator('img').getAttribute('src'), null,
      'Imagem da sessão antiga não pode aparecer depois da troca de usuário');
    assert.strictEqual(await modal(page).locator('#layout-save').isDisabled(), true,
      'Uma resposta invalidada pela troca de sessão não pode habilitar salvar');
    assert.strictEqual(await stored(page), null);
    assert.strictEqual(await stored(page, 'tenant-body-new', 'user-body-new'), null,
      'Resposta antiga não pode persistir dados na sessão nova');
    await modal(page).locator('#layout-cancel').click();
    await page.evaluate(() => {
      window.__restoreLayoutFetch();
      localStorage.setItem('user_data', JSON.stringify({ client_id: 'tenant-a', username: 'user-a' }));
    });
    state.configLayout = null;

    // Um servidor antigo não deve deixar o editor vazio nem provocar POST alternativo.
    state.apiStatus = 405;
    const postsBefore405 = state.previews.length;
    await gear(page, 'Shopee').click();
    await modal(page).waitFor({ state: 'visible' });
    await page.waitForFunction(() => /reinici/i.test(document.getElementById('layout-status').textContent));
    assert(Number(await field(page, 'width').inputValue()) > 0 && Number(await field(page, 'height').inputValue()) > 0,
      '405 deve preservar as medidas iniciais da impressão');
    assert.strictEqual(await item(page, 'etiqueta').isVisible(), true);
    assert(!/gerando/i.test(await modal(page).locator('#layout-sample').textContent()),
      'Falha deve encerrar o indicador de geração da prévia');
    assert.strictEqual(await modal(page).locator('#layout-save').isDisabled(), true);
    assert.strictEqual(state.previews.length, postsBefore405, 'GET 405 não pode disparar POST de fallback');
    assert.strictEqual(await stored(page, 'tenant-a', 'user-a', 'Shopee'), null);
    await modal(page).locator('#layout-cancel').click();
    state.apiStatus = null;

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

    // O placeholder DANFE pode existir e ainda permitir selecionar um código escaneado.
    state.canSelectDanfe = true;
    await open(page, 'Mercado Livre');
    assert.strictEqual(await modal(page).locator('[data-layout-select="danfe"]').isDisabled(), false,
      'Cenário deve manter o componente DANFE disponível enquanto a seleção manual é permitida');
    assert.strictEqual(await item(page, 'danfe').locator('img').getAttribute('src'), png);
    assert.strictEqual(await modal(page).locator('#layout-crop-open').isVisible(), true,
      'can_select_danfe deve permitir recorte mesmo com availability.danfe verdadeira');
    await modal(page).locator('#layout-crop-open').click();
    await modal(page).locator('#layout-crop-panel').waitFor({ state: 'visible' });
    await modal(page).locator('#layout-crop-cancel').click();
    await modal(page).locator('#layout-cancel').click();
    state.canSelectDanfe = false;
    await open(page, 'Mercado Livre');
    assert.strictEqual(await modal(page).locator('#layout-crop-open').isVisible(), false,
      'Flag explícita falsa deve ocultar seleção manual quando não existe recorte salvo');
    await modal(page).locator('#layout-cancel').click();
    state.canSelectDanfe = null;

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

    const slowSaved = deferred();
    state.delayedPreview = slowSaved.promise;
    const beforeSavedPreview = state.previews.length;
    const configsBeforeSaved = state.configs.length;
    await gear(page, 'Mercado Livre').click();
    await modal(page).waitFor({ state: 'visible' });
    await until(() => state.previews.length > beforeSavedPreview);
    await modal(page).locator('[data-layout-select="sku"]').click();
    assert(Math.abs(Number(await field(page, 'x').inputValue()) - saved.items.sku.x) < 0.001,
      'Layout salvo deve aparecer antes da resposta do POST lento');
    assert.strictEqual(await item(page, 'sku').isVisible(), true);
    assert.strictEqual(state.configs.length, configsBeforeSaved, 'Layout salvo deve ir diretamente ao POST de prévia');
    slowSaved.resolve();
    state.delayedPreview = null;
    await page.waitForFunction(() => !document.getElementById('layout-save').disabled);
    await modal(page).locator('#layout-cancel').click();

    state.apiStatus = 405;
    await gear(page, 'Mercado Livre').click();
    await modal(page).waitFor({ state: 'visible' });
    await page.waitForFunction(() => /reinici/i.test(document.getElementById('layout-status').textContent));
    await modal(page).locator('[data-layout-select="sku"]').click();
    assert(Math.abs(Number(await field(page, 'x').inputValue()) - saved.items.sku.x) < 0.001,
      'POST 405 deve preservar as coordenadas já salvas na tela');
    assert.strictEqual(await modal(page).locator('#layout-save').isDisabled(), true);
    assert.deepStrictEqual(await stored(page), saved, 'Falha do servidor não pode sobrescrever o layout salvo');
    await modal(page).locator('#layout-cancel').click();
    state.apiStatus = null;

    await open(page, 'Mercado Livre');
    await modal(page).locator('#layout-reset').click();
    await page.waitForFunction(() => !document.getElementById('layout-save').disabled);
    await modal(page).locator('[data-layout-select="etiqueta"]').click();
    const resetOriginal = await values(page);
    for (const key of ['x', 'y', 'width', 'height']) {
      assert(Math.abs(resetOriginal[key] - defaultLayout().items.etiqueta[key]) <= 0.0051,
        'Restaurar deve mostrar a configuração atual da impressão: ' + key);
    }
    assert.deepStrictEqual(await stored(page), saved, 'Restaurar sem salvar deve manter o layout anterior persistido');
    await modal(page).locator('#layout-cancel').click();

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
    await verificarDownloadDoResultado(page);

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
    withinPage(await values(page), defaultLayout('TikTok').page);
    await modal(page).locator('#layout-cancel').click();
    assert.deepStrictEqual(state.errors, []);
    console.log('etiquetas_layout_browser: OK (config GET, defaults imediatos, API lenta/405, sessão durante JSON, 4 lojas, camadas, arraste, flag e recorte DANFE, cancelamento, isolamento, processamento, downloads e mobile)');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
