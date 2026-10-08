'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'estoque.html'), 'utf8');
const navigation = fs.readFileSync(path.join(root, 'static/auth/navigation.js'), 'utf8');
const origin = 'https://estoque.test';
const token = 'fixture-estoque-token';
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5RkAAAAASUVORK5CYII=', 'base64');
const shops = [{ store_id: 'store-a', nome: 'Loja A' }, { store_id: 'store-b', nome: 'Loja B' }];

function deferred() {
  let resolve;
  const promise = new Promise(r => { resolve = r; });
  return { promise, resolve };
}

function row(sku, store, foto = '', stock = 10) {
  return {
    sku,
    loja_sync: store === 'store-a' ? 'Loja A' : 'Loja B',
    last_update: new Date().toISOString(),
    nome_bling: `Produto ${sku}`,
    situacao_bling: 'A',
    ncm_bling: '85011010',
    saldo_loja: stock,
    saldo_full: 2,
    cest_bling: '0100100',
    monofasico_status: 'confirmado',
    monofasico_confianca: 'alta',
    monofasico_fundamento: 'fixture fiscal preservada',
    monofasico_fonte: 'fixture-fonte',
    monofasico_motivo: 'fixture-motivo',
    monofasico_verificado_em: '08/10/2026 10:30',
    store_id: store,
    __estoque_foto: foto,
  };
}

function fixtureRows() {
  return [
    row('001', 'store-a', '/api/cadastro/foto/fixture-client/lojas/store-a/foto-a.png', 12),
    row('002', 'store-a', '/api/cadastro/foto/fixture-client/lojas/store-a/404.png', 20),
    { ...row('003', 'store-a', '', 0), last_update: '08/10/2026 10:30' },
    row('004', 'store-a', 'https://fixture-images.test/public.png', 24),
    row('005', 'store-a', '/api/cadastro/foto/fixture-client/lojas/store-a/delayed.png', 25),
    ...Array.from({ length: 50 }, (_, i) => row(String(i + 10).padStart(3, '0'), 'store-a', '', i + 30)),
    row('001', 'store-b', '/api/cadastro/foto/fixture-client/lojas/store-b/foto-b.png', 100),
  ];
}

const history = {
  loja: 'Loja A',
  base_snapshot_at: '08/10/2026 10:30',
  series: {
    geral: [
      { label: 'Setembro 2026', saldo: 50, entradas: 12, saidas: 4 },
      { label: 'Outubro 2026', saldo: 58, entradas: 8, saidas: 0 },
    ],
    sku: [
      { label: 'Setembro 2026', saldo: null, entradas: null, saidas: null },
      { label: 'Outubro 2026', saldo: 12, entradas: 3, saidas: 0 },
    ],
  },
};

async function json(route, payload, status = 200) {
  await route.fulfill({ status, contentType: 'application/json; charset=utf-8', body: JSON.stringify(payload) });
}

async function screenshot(page, name) {
  if (!process.env.ESTOQUE_TEST_SCREENSHOT_DIR) return;
  const directory = path.resolve(process.env.ESTOQUE_TEST_SCREENSHOT_DIR);
  fs.mkdirSync(directory, { recursive: true });
  await page.screenshot({ path: path.join(directory, name + '.png'), fullPage: true });
}

async function noPageOverflow(page) {
  const widths = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }));
  assert(widths.document <= widths.viewport + 1, 'O documento excedeu a largura da viewport.');
  assert(widths.body <= widths.viewport + 1, 'O corpo da página excedeu a largura da viewport.');
}

async function scenario(browser, options = {}) {
  const page = await browser.newPage({ viewport: options.viewport || { width: 1440, height: 1000 } });
  const state = { requests: [], errors: [], unexpected: [], routeErrors: [], apiBodies: [], rows: options.rows || fixtureRows() };
  const inventoryGate = deferred();
  const inventoryStarted = deferred();
  const photoGate = deferred();
  const photoStarted = deferred();
  page.setDefaultTimeout(8000);
  page.on('pageerror', error => state.errors.push(error.message));
  await page.addInitScript(() => {
    localStorage.setItem('user_data', JSON.stringify({ client_id: 'fixture-client' }));
    const revoke = URL.revokeObjectURL.bind(URL);
    window.fixtureRevokedObjectUrls = [];
    URL.revokeObjectURL = url => { window.fixtureRevokedObjectUrls.push(url); revoke(url); };
  });
  await page.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const entry = { url: url.href, method: request.method(), authorization: request.headers().authorization || '' };
    state.requests.push(entry);
    try {
      assert(!url.href.includes(token), 'A credencial apareceu na URL.');
      if (url.origin === origin && url.pathname.startsWith('/api/')) {
        assert.strictEqual(entry.authorization, `Bearer ${token}`, 'A API local deve receber Bearer.');
      }
      if (url.origin !== origin) assert.strictEqual(entry.authorization, '', 'Credencial enviada a outra origem.');
      if (url.pathname === '/estoque.html') return await route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
      if (url.pathname === '/auth.js') {
        const script = `window.obterToken=()=>${JSON.stringify(token)};window.tokenSessaoExpirado=()=>false;window.redirecionarSessaoExpirada=()=>{};window.obterAuthHeaders=extra=>Object.assign({Authorization:'Bearer ${token}'},extra||{});`;
        const loader = options.media === false ? '' : `document.write('<script src="/auth/navigation.js"><\\/script>');`;
        return await route.fulfill({ contentType: 'application/javascript; charset=utf-8', body: script + loader });
      }
      if (url.pathname === '/auth/navigation.js') return await route.fulfill({ contentType: 'application/javascript; charset=utf-8', body: navigation });
      if (['/ncm-sync.js', '/global-status.js', '/ia-sidebar.js'].includes(url.pathname)) return await route.fulfill({ contentType: 'application/javascript', body: '' });
      if (url.pathname === '/assets/estoque-stockroom.png' || url.hostname === 'fixture-images.test') return await route.fulfill({ contentType: 'image/png', body: png });
      if (url.pathname === '/api/configuracoes') return await json(route, { auto_sync_estoque_janela_minutos: 30 });
      if (url.pathname === '/api/lojas') return await json(route, options.shops || shops);
      if (url.pathname === '/api/estoque') {
        assert.strictEqual(url.searchParams.get('incluir_fotos'), 'true');
        inventoryStarted.resolve();
        if (options.delayInventory) await inventoryGate.promise;
        return await json(route, options.failInventory ? { detail: 'erro fixture' } : state.rows, options.failInventory ? 500 : 200);
      }
      if (url.pathname === '/api/estoque/sync/progress') return await json(route, { active: false });
      if (url.pathname === '/api/estoque/serie-retroativa') {
        assert.strictEqual(url.searchParams.get('store_id'), 'store-a');
        assert.strictEqual(url.searchParams.get('loja'), 'Loja A');
        return await json(route, history);
      }
      if (url.pathname === '/api/estoque/sync') {
        assert.strictEqual(request.method(), 'POST');
        state.apiBodies.push(request.postDataJSON());
        return await json(route, { started: false, message: 'Sincronização fixture sem efeito real.' });
      }
      if (url.pathname.startsWith('/api/cadastro/foto/')) {
        if (url.pathname.endsWith('/404.png')) return await json(route, { detail: 'Foto fixture ausente.' }, 404);
        if (options.delayPhoto && url.pathname.endsWith('/delayed.png')) {
          photoStarted.resolve();
          await photoGate.promise;
        }
        return await route.fulfill({ contentType: 'image/png', body: png }).catch(() => {});
      }
      if (url.pathname === '/favicon.ico') return await route.fulfill({ status: 204, body: '' });
      state.unexpected.push(url.pathname);
      await route.fulfill({ status: 404, body: 'Rota não prevista no teste isolado.' });
    } catch (error) {
      state.routeErrors.push(error.message);
      await route.fulfill({ status: 500, body: 'Falha na fixture.' }).catch(() => {});
    }
  });
  await page.goto(origin + '/estoque.html', { waitUntil: 'domcontentloaded' });
  return { page, state, inventoryGate, inventoryStarted, photoGate, photoStarted };
}

async function verifyRequests(state) {
  assert.deepStrictEqual(state.errors, [], 'Erro JavaScript no browser.');
  assert.deepStrictEqual(state.routeErrors, [], 'Violação do contrato das fixtures.');
  assert.deepStrictEqual(state.unexpected, [], 'Toda requisição precisa de mock explícito.');
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  let checks = 0;
  try {
    const { page, state } = await scenario(browser);
    await page.waitForFunction(() => document.querySelectorAll('#tBody tr').length === 50).catch(async error => {
      console.error(JSON.stringify({ errors: state.errors, routeErrors: state.routeErrors, unexpected: state.unexpected, requestCount: state.requests.length, status: await page.locator('#status').textContent(), rows: await page.locator('#tBody tr').count() }));
      throw error;
    });
    assert.strictEqual(await page.locator('#pageInfo').textContent(), 'Mostrando 1-50 de 56 SKUs (página 1/2)');
    assert.strictEqual(await page.locator('#tHead th').count(), Object.keys(fixtureRows()[0]).length);
    const headings = await page.locator('#tHead th').allTextContents();
    for (const name of ['Foto', 'SKU', 'Produto', 'Loja', 'Atualização', 'Saldo loja', 'Saldo Full', 'Situação Bling', 'NCM Bling', 'CEST Bling', 'MONOFASICO STATUS', 'MONOFASICO CONFIANCA', 'MONOFASICO FUNDAMENTO', 'MONOFASICO FONTE', 'MONOFASICO MOTIVO', 'MONOFASICO VERIFICADO EM', 'STORE ID']) assert(headings.includes(name), `Coluna ausente: ${name}`);
    assert(!headings.some(text => /__estoque_foto/i.test(text)));
    assert((await page.locator('#tBody tr').first().textContent()).includes('fixture fiscal preservada'));
    const isoCell = page.locator('#tBody tr').first().locator('.col-atualizacao');
    assert(/^\d{2}\/\d{2}\/\d{4}\n\d{2}:\d{2}$/.test(await isoCell.textContent()), 'A data ISO deve ser legível em pt-BR.');
    assert.strictEqual(await isoCell.getAttribute('title'), state.rows[0].last_update, 'O literal original da data deve continuar disponível.');
    await page.locator('#filtroTexto').fill(state.rows[0].last_update);
    assert(await page.locator('#tBody tr').count() > 0, 'A busca continua utilizando a data original.');
    checks += 6;

    await page.locator('#filtroTexto').fill('Produto 001');
    await page.waitForFunction(() => document.querySelectorAll('#tBody tr').length === 2);
    await page.waitForFunction(() => Array.from(document.querySelectorAll('#tBody img')).every(img => img.complete && img.naturalWidth > 0 && img.src.startsWith('blob:'))).catch(async error => {
      console.error(JSON.stringify(await page.evaluate(() => ({ media: typeof window.JKAuthenticatedMedia, images: Array.from(document.querySelectorAll('#tBody img')).map(img => ({ complete: img.complete, width: img.naturalWidth, protocol: img.src.split(':')[0], erro: img.classList.contains('jk-auth-image-error') })) }))));
      console.error(JSON.stringify({ errors: state.errors, routeErrors: state.routeErrors, unexpected: state.unexpected }));
      throw error;
    });
    const images = await page.locator('#tBody img').evaluateAll(nodes => nodes.map(img => ({ source: img.getAttribute('data-jk-auth-src'), src: img.getAttribute('src'), link: img.closest('a').getAttribute('href') })));
    assert.deepStrictEqual(images.map(img => img.source), ['/api/cadastro/foto/fixture-client/lojas/store-a/foto-a.png', '/api/cadastro/foto/fixture-client/lojas/store-b/foto-b.png']);
    assert.notStrictEqual(images[0].src, images[1].src);
    assert(images.every(img => img.src === img.link && img.src.startsWith('blob:')));
    checks += 3;

    await page.locator('#filtroTexto').fill('foto-a.png');
    assert.strictEqual(await page.locator('#tBody tr').count(), 0, 'A URL de mídia não pode virar termo de busca.');
    await page.locator('#filtroTexto').fill('002');
    await page.waitForFunction(() => document.querySelector('#tBody img')?.classList.contains('jk-auth-image-error'));
    assert.strictEqual(await page.locator('#tBody img').getAttribute('src'), null);
    assert((await page.locator('#tBody').textContent()).includes('Foto indisponível'));
    await page.locator('#filtroTexto').fill('003');
    assert.strictEqual(await page.locator('#tBody img').count(), 0);
    assert((await page.locator('#tBody').textContent()).includes('Sem foto'));
    assert.strictEqual(await page.locator('#tBody .col-atualizacao').textContent(), '08/10/2026 10:30');
    assert.strictEqual(await page.locator('#tBody .col-atualizacao').getAttribute('title'), '08/10/2026 10:30');
    checks += 2;
    checks += 3;

    await page.locator('#filtroTexto').fill('004');
    await page.waitForFunction(() => document.querySelector('#tBody img')?.complete && document.querySelector('#tBody img')?.naturalWidth > 0);
    assert.strictEqual(await page.locator('#tBody img').getAttribute('src'), 'https://fixture-images.test/public.png');
    assert(state.requests.some(item => item.url === 'https://fixture-images.test/public.png' && !item.authorization));
    await page.locator('#filtroTexto').fill('');
    await page.locator('#ordenarPor').selectOption('estoque_desc');
    assert.strictEqual(await page.locator('#tBody tr').first().locator('.col-sku').textContent(), '001');
    assert.strictEqual(await page.locator('#tBody tr').first().locator('.col-loja').textContent(), 'Loja B');
    await page.locator('[data-store-id="store-a"]').click();
    assert.strictEqual(await page.locator('#tBody tr').count(), 50);
    assert((await page.locator('#pageInfo').textContent()).includes('de 55 SKUs'));
    assert(!(await page.locator('#tBody').textContent()).includes('Loja B'));
    await page.locator('#btnPaginaProxima').click();
    assert.strictEqual(await page.locator('#tBody tr').count(), 5);
    assert(await page.locator('#btnPaginaProxima').isDisabled());
    await page.locator('#btnPaginaAnterior').click();
    assert.strictEqual(await page.locator('#tBody tr').count(), 50);
    await noPageOverflow(page);
    assert(await page.locator('#abaAtual .table-wrapper').evaluate(el => el.scrollWidth > el.clientWidth), 'A tabela ampla deve usar rolagem local.');
    await screenshot(page, 'estoque-desktop');
    checks += 7;

    await page.locator('#tabAtual').focus();
    await page.keyboard.press('ArrowRight');
    await page.waitForFunction(() => document.querySelectorAll('#historicoTBody tr').length === 2);
    assert.strictEqual(await page.locator('#tabHistorico').getAttribute('aria-selected'), 'true');
    assert.strictEqual(await page.locator('#tabHistorico').getAttribute('tabindex'), '0');
    assert.strictEqual(await page.locator('#tabAtual').getAttribute('tabindex'), '-1');
    await page.locator('#histIncluirSku').check();
    await page.locator('#histSkuInput').fill('001');
    await page.locator('#btnHistoricoCarregar').click();
    await page.waitForFunction(() => document.querySelectorAll('#historicoTHead th').length === 9);
    const firstHistory = await page.locator('#historicoTBody tr').first().locator('td').allTextContents();
    assert.deepStrictEqual(firstHistory, ['Setembro 2026', '50', '12', '4', '8', '—', '—', '—', '—']);
    assert((await page.locator('#historicoResumo').textContent()).includes('SKU 00112'));
    await noPageOverflow(page);
    await screenshot(page, 'estoque-historico');
    await page.locator('#tabHistorico').focus();
    await page.keyboard.press('Home');
    assert.strictEqual(await page.locator('#tabAtual').getAttribute('aria-selected'), 'true');
    await page.keyboard.press('End');
    assert.strictEqual(await page.locator('#tabHistorico').getAttribute('aria-selected'), 'true');
    await page.keyboard.press('ArrowRight');
    assert.strictEqual(await page.locator('#tabAtual').getAttribute('aria-selected'), 'true');
    checks += 6;

    await page.setViewportSize({ width: 390, height: 844 });
    await noPageOverflow(page);
    assert(await page.locator('#filtroTexto').isVisible());
    assert(await page.locator('#btnSync').isVisible());
    await screenshot(page, 'estoque-mobile');
    await page.locator('#tabHistorico').click();
    await noPageOverflow(page);
    checks += 2;
    await verifyRequests(state);
    await page.close();

    const race = await scenario(browser, { delayPhoto: true });
    await race.page.waitForFunction(() => document.querySelectorAll('#tBody tr').length === 50);
    await Promise.race([race.photoStarted.promise, new Promise((_, reject) => setTimeout(() => reject(new Error('A foto pendente não iniciou.')), 5000))]);
    await race.page.evaluate(() => { window.fixtureOldImage = document.querySelector('img[data-jk-auth-src$="/delayed.png"]'); });
    await race.page.locator('#btnPaginaProxima').click();
    const secondPageSkus = await race.page.locator('#tBody .col-sku').allTextContents();
    race.photoGate.resolve();
    await race.page.waitForTimeout(100);
    assert.deepStrictEqual(await race.page.locator('#tBody .col-sku').allTextContents(), secondPageSkus);
    assert.strictEqual(await race.page.evaluate(() => window.fixtureOldImage?.getAttribute('src')), null, 'Foto obsoleta hidratou uma linha removida.');
    assert(!await race.page.locator('img[data-jk-auth-src$="/delayed.png"]').count());
    assert(await race.page.evaluate(() => window.fixtureRevokedObjectUrls.length > 0), 'As fotos da página anterior devem ser liberadas.');
    await verifyRequests(race.state);
    await race.page.close();
    checks += 3;

    const missing = await scenario(browser, { media: false });
    await missing.page.waitForFunction(() => document.querySelectorAll('#tBody tr').length === 50);
    assert.strictEqual(await missing.page.evaluate(() => typeof window.JKAuthenticatedMedia), 'undefined');
    assert.strictEqual(await missing.page.locator('#tBody img[data-jk-auth-src*="foto-a.png"]').getAttribute('src'), null);
    assert(!(await missing.page.locator('#tBody .estoque-foto').first().getAttribute('href')));
    await missing.page.locator('#filtroTexto').fill('003');
    assert.strictEqual(await missing.page.locator('#tBody tr').count(), 1);
    await verifyRequests(missing.state);
    await missing.page.close();
    checks += 2;

    const loading = await scenario(browser, { delayInventory: true });
    await loading.inventoryStarted.promise;
    assert((await loading.page.locator('#status').textContent()).includes('Carregando estoque'));
    assert(await loading.page.locator('.inventory-loading').isVisible());
    await screenshot(loading.page, 'estoque-carregamento');
    loading.inventoryGate.resolve();
    await loading.page.waitForFunction(() => document.querySelectorAll('#tBody tr').length === 50);
    await verifyRequests(loading.state);
    await loading.page.close();
    checks += 1;

    const empty = await scenario(browser, { rows: [], shops: [] });
    await empty.page.waitForFunction(() => document.querySelector('#resumo .val')?.textContent === '0');
    assert(await empty.page.locator('.inventory-empty').isVisible());
    assert.strictEqual(await empty.page.locator('#tBody tr').count(), 0);
    await screenshot(empty.page, 'estoque-vazio');
    await verifyRequests(empty.state);
    await empty.page.close();
    checks += 1;

    const failed = await scenario(browser, { failInventory: true, shops: [] });
    await failed.page.waitForFunction(() => document.querySelector('#status').textContent.includes('Erro ao carregar estoque: HTTP 500'));
    assert(await failed.page.locator('#status').evaluate(el => el.classList.contains('error')));
    await screenshot(failed.page, 'estoque-erro');
    await verifyRequests(failed.state);
    await failed.page.close();
    checks += 1;

    console.log(`PASS estoque_design_browser: ${checks} verificações; todas as requisições interceptadas, sem API ou sessão real.`);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
