'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const preview = process.env.JK_PACKING_TOTAIS_PREVIEW_DIR;
const totals = (m3, caixas, peso, trays = 0) => ({
  total_m3: m3, total_caixas: caixas, total_trays: trays, peso_bruto_total_kg: peso,
});
const base = {
  status: 'Analisando orçamento', loja: 'Loja exemplo', store_id: 'store-a',
  fornecedor_id: 'supplier-a', supplier: 'Fornecedor exemplo', nacionalizador: 'Nacionalizador exemplo',
  numero_invoice: 'INV-EXEMPLO', order_date: '2026-10-23', total_itens: 30,
  total_quantidade: 3420, total_usd: 14180.5, m3_completo: true, m3_pendente_itens: 0,
  updated_at: '2026-10-23T12:36:00Z', m3_fonte: 'lista', packing_list_totais: null,
  itens: [{ SKU: 'EX-001', Quantidade: 3420, 'Valor unidade': 2, M3: 999, 'Cubagem status': 'historico' }],
};
const lists = [
  { ...structuredClone(base), id: 'ready', nome_lista: 'Pedido com Packing List', packing_list_ready: true,
    total_m3: 2.46, m3_fonte: 'packing_list', packing_list_totais: totals(2.46, 38, 486, 2) },
  { ...structuredClone(base), id: 'upload', nome_lista: 'Pedido sem Packing List', packing_list_ready: false,
    total_itens: 2, total_quantidade: 300, total_m3: 0.2083573334 },
  { ...structuredClone(base), id: 'unavailable', nome_lista: 'Documento indisponível', packing_list_ready: true,
    total_m3: 0.5, m3_completo: false, m3_pendente_itens: 1 },
];
const state = { uploads: 0, reads: 0, errors: [], unexpected: [], downloads: 0 };
const json = (route, data) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
const block = (page, id) => page.locator('[data-transporte-lista-id="' + id + '"]');
const packingButton = (page, id) => page.locator('[data-packing-lista-id="' + id + '"]');

async function installRoutes(page) {
  await page.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const p = url.pathname;
    if (url.origin !== 'http://jk.test') {
      if (url.hostname === 'economia.awesomeapi.com.br') return json(route, { USDBRL: { bid: '5' } });
      return route.fulfill({ status: 200, contentType: 'text/css', body: '' });
    }
    if (p === '/importacoes.html') return route.fulfill({ status: 200, contentType: 'text/html',
      body: fs.readFileSync(path.join(root, 'static/importacoes.html')) });
    if (p === '/auth.js') return route.fulfill({ status: 200, contentType: 'application/javascript',
      body: 'window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({Authorization:"Bearer synthetic"});' });
    if (p.startsWith('/medias_compras/')) return route.fulfill({ status: 200,
      contentType: p.endsWith('.css') ? 'text/css' : 'application/javascript',
      body: fs.readFileSync(path.join(root, 'static', p.slice(1))) });
    if (p === '/ia-sidebar.js' || p === '/favicon.ico') return route.fulfill({ status: 200, body: '' });
    assert.strictEqual(request.headers().authorization, 'Bearer synthetic');
    if (p === '/api/lojas') return json(route, [{ nome: base.loja, store_id: base.store_id }]);
    if (p === '/api/cadastro/fornecedores') return json(route, [{ id: 'supplier-a', nome_empresa: base.supplier }]);
    if (p === '/api/cadastro/nacionalizadores') return json(route, []);
    if (p === '/api/medias-compras/listas-pedidos') { state.reads++; return json(route, { listas: lists }); }
    if (p === '/api/medias-compras/listas-pedidos/upload/packing-list/importar-excel') {
      assert.strictEqual(request.method(), 'POST');
      state.uploads++;
      Object.assign(lists[1], { packing_list_ready: true, m3_fonte: 'packing_list', total_m3: 1.25,
        packing_list_totais: totals(1.25, 12, 64), m3_completo: true, m3_pendente_itens: 0 });
      return json(route, { success: true, packing_list_ready: true, lista: lists[1] });
    }
    const detail = p.match(/^\/api\/medias-compras\/listas-pedidos\/([^/]+)$/);
    if (detail && request.method() === 'GET') {
      const list = lists.find(row => row.id === detail[1]);
      assert(list);
      return json(route, { lista: list });
    }
    state.unexpected.push(request.method() + ' ' + p);
    return route.abort();
  });
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1500, height: 1050 } });
    page.setDefaultTimeout(10000);
    page.on('pageerror', error => state.errors.push(error.message));
    page.on('download', () => { state.downloads++; });
    await page.addInitScript(() => localStorage.setItem('jk_embarques_importacoes', JSON.stringify([
      { id: 'example', nome: 'Embarque exemplo', lista_ids: ['ready', 'upload', 'removed'] },
    ])));
    await installRoutes(page);
    await page.goto('http://jk.test/importacoes.html');
    await block(page, 'ready').waitFor();
    assert((await block(page, 'ready').innerText()).includes('2,46 m³'));
    assert.strictEqual(await block(page, 'ready').locator('[data-transporte-caixas]').innerText(), '38');
    assert.strictEqual(await block(page, 'ready').locator('[data-transporte-peso]').innerText(), '486,00 kg');
    assert((await block(page, 'ready').innerText()).includes('+ 2 Trays'), 'Trays não devem ser somados às caixas');
    assert((await block(page, 'upload').innerText()).includes('0,2083573334 m³'));
    assert.strictEqual(await block(page, 'upload').locator('[data-transporte-caixas]').innerText(), '—');
    assert.strictEqual(await block(page, 'upload').locator('[data-transporte-peso]').innerText(), '—');
    assert((await block(page, 'unavailable').innerText()).includes('Dados do Packing List indisponíveis'));
    assert((await block(page, 'unavailable').innerText()).includes('(parcial)'));
    assert((await page.locator('#embarquesWrap').innerText()).includes('2,6683573334 m³'));
    if (preview) {
      fs.mkdirSync(preview, { recursive: true });
      await page.locator('#tabelaListasImportacoes').screenshot({ path: path.join(preview, 'packing-totais-implementado-desktop.png') });
    }
    await page.locator('#embarquesWrap .embarque-item').click();
    const volumeCard = page.locator('.emb-resumo-card').filter({ has: page.locator('.emb-label', { hasText: /^M³ Total$/ }) });
    await volumeCard.locator('.emb-value').waitFor();
    assert.strictEqual(await volumeCard.locator('.emb-value').innerText(), '2,6683573334 m³',
      'Detalhe e cabeçalho devem usar o Packing mesmo com referência antiga a lista removida');
    await page.locator('#btnVoltarEmbarque').click();
    const reads = state.reads;
    await packingButton(page, 'upload').click();
    const modal = page.locator('.packing-list-dialog');
    await modal.locator('input[type=file]').setInputFiles({
      name: 'packing-exemplo.xlsx', mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      buffer: Buffer.from('PK synthetic'),
    });
    await modal.locator('[data-packing-list-enviar]').click();
    await modal.waitFor({ state: 'detached' });
    assert.strictEqual(await block(page, 'upload').locator('[data-transporte-caixas]').innerText(), '12');
    assert.strictEqual(await block(page, 'upload').locator('[data-transporte-peso]').innerText(), '64,00 kg');
    assert((await block(page, 'upload').innerText()).includes('1,25 m³'));
    assert((await page.locator('#embarquesWrap').innerText()).includes('3,71 m³'));
    assert.strictEqual(state.uploads, 1);
    assert.strictEqual(state.reads, reads, 'Resposta do upload deve atualizar os totais sem recarregar a lista');
    assert.strictEqual(state.downloads, 0, 'Upload não deve baixar o documento automaticamente');
    await page.locator('#buscarListasImportacoes').fill('sem Packing');
    await page.locator('#buscarListasImportacoes').fill('');
    assert.strictEqual(await block(page, 'upload').locator('[data-transporte-peso]').innerText(), '64,00 kg');
    for (const width of [1669, 1500, 1440, 1280, 1161, 1100, 980, 800, 781, 780, 768, 700, 390, 375, 320]) {
      await page.setViewportSize({ width, height: 1050 });
      const layout = await page.evaluate(() => ({
        overflow: document.documentElement.scrollWidth > innerWidth,
        clipped: [...document.querySelectorAll('#listaWrap .lista-transporte-valor')].some(el =>
          parseFloat(getComputedStyle(el).fontSize) < 12 ||
          el.scrollWidth > el.clientWidth + 1 || el.getBoundingClientRect().right > innerWidth + 1),
      }));
      assert.deepStrictEqual(layout, { overflow: false, clipped: false }, 'Layout de transporte em ' + width + ' px');
    }
    if (preview) await page.locator('#listaWrap .lista-item').first().screenshot({
      path: path.join(preview, 'packing-totais-implementado-mobile.png'),
    });
    Object.assign(lists[2], { m3_fonte: 'packing_list', packing_list_totais: totals(0, 0, 0),
      total_m3: 0, m3_completo: true, m3_pendente_itens: 0 });
    await page.reload();
    await block(page, 'unavailable').waitFor();
    assert((await block(page, 'unavailable').innerText()).includes('0,00 m³'));
    assert.strictEqual(await block(page, 'unavailable').locator('[data-transporte-caixas]').innerText(), '0');
    assert.strictEqual(await block(page, 'unavailable').locator('[data-transporte-peso]').innerText(), '0,00 kg');
    assert(!(await block(page, 'unavailable').innerText()).includes('(parcial)'));
    assert.strictEqual(await block(page, 'upload').locator('[data-transporte-peso]').innerText(), '64,00 kg');
    assert.deepStrictEqual(state.errors, []);
    assert.deepStrictEqual(state.unexpected, []);
    console.log('PASS totais Packing: prioridade, caixas/Trays, peso bruto, fallback, zero, upload imediato, recarga, embarque e 15 viewports.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
