'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const rowSelector = '#tblResultado tbody tr[data-jk-virtual-row]';
const sku = index => 'SKU-' + String(index).padStart(5, '0');
const skuLegado = 'sku-legado-02';

async function settle(page, frames = 6) {
  await page.evaluate(frames => new Promise(resolve => {
    const tick = () => --frames <= 0 ? resolve() : requestAnimationFrame(tick);
    requestAnimationFrame(tick);
  }), frames);
}

async function fixture(browser, count, { adiarFotos = false } = {}) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const errors = [], exports = [], savedHidden = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(adiarFotos => {
    localStorage.setItem('access_token', 'synthetic-token');
    localStorage.setItem('user_data', JSON.stringify({ client_id: 'tenant-virtual', username: 'synthetic-user' }));
    if (!adiarFotos) return;
    const nativo = fetch.bind(window);
    const chamadas = [], respostas = new Map(), invalidacoes = [];
    addEventListener('jk:cache-persistente-invalidado', event => invalidacoes.push(event.detail));
    window.fetch = (input, init = {}) => {
      if (new URL(input, location.href).pathname !== '/api/medias-compras/visao/fotos') return nativo(input, init);
      const chamada = {
        id: chamadas.length, metodo: init.method, body: JSON.parse(init.body),
        auth: new Headers(init.headers).get('authorization'), cancelada: init.signal.aborted,
        montados: [...document.querySelectorAll('#tblResultado [data-jk-virtual-row]')].map(row => row.dataset.virtualKey),
      };
      chamadas.push(chamada);
      init.signal.addEventListener('abort', () => { chamada.cancelada = true; });
      // O transporte de teste ainda pode entregar a resposta após o cancelamento.
      return new Promise(resolve => respostas.set(chamada.id, resolve));
    };
    window.__fotos = {
      chamadas, invalidacoes,
      pendentes: () => [...respostas.keys()],
      responder(id, body, status = 200) {
        respostas.get(id)({ ok: status === 200, status, json: async () => body });
        respostas.delete(id);
      },
    };
  }, adiarFotos);
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    const json = body => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname === '/medias_compras.html') {
      let html = fs.readFileSync(path.join(root, 'static/medias_compras.html'), 'utf8');
      if (!html.includes('/medias_compras/tabela-virtual.js')) html = html.replace(
        /(<script[^>]+src="\/medias_compras\/tabela\.js[^>]*><\/script>)/,
        '<script src="/medias_compras/tabela-virtual.js"></script>$1');
      return route.fulfill({ contentType: 'text/html', body: html });
    }
    if (url.pathname.startsWith('/medias_compras/')) return route.fulfill({
      contentType: url.pathname.endsWith('.css') ? 'text/css' : 'application/javascript',
      body: fs.readFileSync(path.join(root, 'static', url.pathname.slice(1))),
    });
    if (adiarFotos && url.pathname === '/cache-persistente.js') return route.fulfill({
      contentType: 'application/javascript', body: fs.readFileSync(path.join(root, 'static/cache-persistente.js')),
    });
    if (url.pathname === '/auth.js') return route.fulfill({ contentType: 'application/javascript',
      body: 'window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({Authorization:"Bearer synthetic-token","X-Client-ID":"tenant-virtual"});' +
        (adiarFotos ? 'window.JKAuthenticatedMedia={normalizarUrlFotoCadastro:foto=>foto,ehUrlProtegidaCadastro:foto=>foto.startsWith("/api/cadastro/")};' : '') });
    if (url.pathname === '/foto.gif') return route.fulfill({
      contentType: 'image/gif', body: Buffer.from('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7', 'base64'),
    });
    if (url.pathname === '/api/lojas') return json([{ nome: 'Loja de teste', store_id: 'store-virtual' }]);
    if (url.pathname.endsWith('/listas-pedidos')) return json({ listas: [] });
    if (url.pathname.endsWith('/preferencias-skus-ocultos')) {
      if (route.request().method() === 'PUT') savedHidden.push(route.request().postDataJSON());
      return json({ skus_ocultos: [] });
    }
    if (url.pathname.endsWith('/visao')) {
      const months = Number(url.searchParams.get('meses') || 12);
      const columns = Array.from({ length: months }, (_, i) => ({ key: `2026-${String(i + 1).padStart(2, '0')}` }));
      const storeId = url.searchParams.get('store_id') || '';
      const skusIniciais = adiarFotos ? [sku(1), skuLegado, 'X'.repeat(257)] : [];
      if (adiarFotos) assert.strictEqual(url.searchParams.get('fotos'), 'adiadas');
      return json({
        ...(adiarFotos ? { fotos_adiadas: true, _cache: {
          estado: 'ready', versao: storeId + ':' + months, politica_fotos: 'politica-1', completo: true,
        } } : {}),
        colunas_meses: columns, itens: Array.from({ length: count }, (_, i) => ({
        ...(adiarFotos ? { foto: '' } : {}),
        sku: skusIniciais[i] ?? sku(i + 1), titulo_anuncio: i % 7 === 0 ? 'Descricao longa com muitas palavras '.repeat(10) : 'Produto curto',
        aviso_sem_venda: i % 5 === 0 ? 'Sem venda no periodo' : '', vendas_mensais: {}, total_vendas_periodo: 12,
        media_mensal: 2, saldo_atual_estoque: 4, estoque_em_transito: 5,
        estoque_em_transito_listas: [{ lista_id: 'lista-teste', nome_lista: 'Lista de teste', quantidade: 5 }],
        posicao_estoque: 9, compra_sugerida: 3,
      })) });
    }
    if (url.pathname.endsWith('/gerar-lista-sugestao')) {
      exports.push(Object.fromEntries(url.searchParams));
      return json({ file_id: 'synthetic-export' });
    }
    if (url.pathname.includes('/download/')) return route.fulfill({ contentType: 'application/octet-stream', body: 'synthetic' });
    if (url.pathname.startsWith('/api/')) throw new Error('Unexpected API ' + url.pathname);
    return route.fulfill({ contentType: 'application/javascript', body: '' });
  });
  await page.goto('http://jk.test/medias_compras.html');
  await page.waitForFunction(count => document.querySelector('#tblResultado').dataset.totalRows === String(count)
    && document.querySelector('#status').textContent === '', count);
  if (!adiarFotos) {
    await page.locator('#lojaBotoes .loja-btn').last().click();
    await page.waitForFunction(() => document.querySelector('#status').textContent === '');
    await settle(page);
  }
  return { context, page, errors, exports, savedHidden };
}

async function scrollTo(page, fraction) {
  await page.evaluate(fraction => {
    const wrap = document.getElementById('secaoTabelaPrincipal');
    wrap.scrollTop = (wrap.scrollHeight - wrap.clientHeight) * fraction;
  }, fraction);
  await settle(page);
}

async function visibleAnchor(page) {
  return page.evaluate(() => {
    const root = document.getElementById('secaoTabelaPrincipal');
    const top = root.getBoundingClientRect().top + root.clientTop + document.querySelector('#tblResultado thead').getBoundingClientRect().height;
    const rows = [...document.querySelectorAll('#tblResultado [data-jk-virtual-row]')];
    const row = rows.find(row => row.getBoundingClientRect().bottom > top + 0.5);
    return row && { key: row.dataset.virtualKey, offset: row.getBoundingClientRect().top - top };
  });
}

async function testLargeTable(browser, count) {
  const f = await fixture(browser, count);
  const { page } = f;
  try {
    assert.strictEqual(await page.evaluate(() => itensVisaoAtual.length), count, 'all records remain available to business operations');
    assert((await page.locator(rowSelector).count()) < Math.min(70, count), 'DOM must remain bounded even below the former 60-item threshold');
    const heights = await page.locator(rowSelector).evaluateAll(rows => rows.map(row => row.getBoundingClientRect().height));
    assert(Math.max(...heights) - Math.min(...heights) > 30, 'fixture exercises variable row heights');
    await scrollTo(page, 1);
    await page.locator(`${rowSelector}[data-virtual-key="${sku(count)}"]`).waitFor();
    assert((await page.locator(rowSelector).count()) < 70, 'last window stays bounded');
    assert(await page.evaluate(() => {
      const wrap = document.getElementById('secaoTabelaPrincipal');
      const last = document.querySelector('#tblResultado [data-jk-virtual-row]:last-child');
      return last && last.getBoundingClientRect().bottom <= wrap.getBoundingClientRect().bottom + 2;
    }), 'last product can be reached at the end of the continuous scroll');

    await page.evaluate(() => {
      const th = document.querySelector('#headRow th[data-col-key="titulo_anuncio"]');
      aplicarLarguraColunaPorIndice(Number(th.dataset.colIndex), 200);
    });
    await settle(page, 10);
    assert(await page.evaluate(() => {
      const wrap = document.getElementById('secaoTabelaPrincipal');
      const last = document.querySelector('#tblResultado [data-jk-virtual-row]:last-child');
      return last && wrap.scrollHeight - wrap.clientHeight - wrap.scrollTop < 2
        && last.getBoundingClientRect().bottom <= wrap.getBoundingClientRect().bottom + 2;
    }), 'resizing at the bottom keeps the last product visible instead of a spacer');

    await scrollTo(page, 0.5);
    const before = await visibleAnchor(page);
    await page.evaluate(() => {
      const th = document.querySelector('#headRow th[data-col-key="titulo_anuncio"]');
      aplicarLarguraColunaPorIndice(Number(th.dataset.colIndex), 500);
    });
    await settle(page, 10);
    const after = await visibleAnchor(page);
    assert.strictEqual(after.key, before.key, 'column resize preserves visible anchor SKU');
    assert(Math.abs(after.offset - before.offset) < 3, 'column resize preserves anchor offset');
    await page.evaluate(() => document.getElementById('secaoTabelaPrincipal').classList.add('hidden'));
    await settle(page, 3);
    await page.evaluate(() => document.getElementById('secaoTabelaPrincipal').classList.remove('hidden'));
    await settle(page);
    const afterHidden = await visibleAnchor(page);
    assert.strictEqual(afterHidden.key, after.key, 'hidden containers must not reset the last measured anchor');
    assert(Math.abs(afterHidden.offset - after.offset) < 3, 'showing the table restores the anchor offset');
    await page.setViewportSize({ width: 960, height: 650 });
    await settle(page);
    assert((await page.locator(rowSelector).count()) < 70);
    await scrollTo(page, 1);
    await page.locator(`${rowSelector}[data-virtual-key="${sku(count)}"]`).waitFor();

    await scrollTo(page, 0);
    const button = page.locator(`${rowSelector}[data-virtual-key="${sku(1)}"] .compra-sugerida-editavel`);
    await button.click();
    const input = page.locator('.compra-sugerida-input');
    await input.fill('73');
    await scrollTo(page, 1);
    assert.strictEqual(await input.inputValue(), '73', 'scroll must not destroy an active edit');
    assert(await input.evaluate(el => el === document.activeElement), 'active edit retains focus');
    await page.locator(`${rowSelector}[data-virtual-key="${sku(count)}"]`).waitFor();
    await input.evaluate(el => el.blur());
    await settle(page);
    assert.deepStrictEqual(await page.evaluate(() => obterQuantidadesSugeridasEditadas()), { [sku(1)]: 73 });
    assert.strictEqual(await page.locator('.compra-sugerida-input').count(), 0);
    assert((await page.locator(rowSelector).count()) < 70, 'unfocused row is released');

    await page.locator('#pesquisaSkuInput').fill(sku(count));
    await settle(page);
    assert.strictEqual(await page.locator(rowSelector).count(), 1, 'search reaches records absent from DOM');
    await page.locator(`${rowSelector} [data-jk-action="ocultar-sku"]`).click();
    await page.locator('#btnAbaOcultados').click();
    await settle(page);
    assert.strictEqual(await page.locator(rowSelector).count(), 1, 'hidden tab retains the matching SKU');
    await page.locator(`${rowSelector} [data-jk-action="reexibir-sku"]`).click();
    await page.locator('#btnAbaLista').click();
    await page.locator('#pesquisaSkuInput').fill('');
    await settle(page);
    assert.strictEqual(await page.evaluate(() => itensVisaoAtual.length), count);
    assert(f.savedHidden.some(item => item.skus_ocultos.includes(sku(count))));
    await page.evaluate(() => baixarListaSugestaoExcel());
    assert.strictEqual(f.exports.length, 1);
    assert.strictEqual(f.exports[0].store_id, 'store-virtual');
    assert.deepStrictEqual(JSON.parse(f.exports[0].quantidades_sugeridas), { [sku(1)]: 73 }, 'export includes edits outside rendered window');

    await page.evaluate(() => {
      skusOcultosSet = new Set(itensVisaoAtual.map(item => item.sku));
      selecionarAba('ocultados');
    });
    await settle(page);
    assert((await page.locator(rowSelector).count()) < 70, 'hidden tab is also virtualized');
    await scrollTo(page, 1);
    await page.locator(`${rowSelector}[data-virtual-key="${sku(count)}"]`).waitFor();
    const parity = await page.locator(rowSelector).evaluateAll(rows => rows.every(row =>
      row.classList.contains(Number(row.dataset.virtualIndex) % 2 === 0 ? 'linha-impar' : 'linha-par')));
    assert(parity, 'striping follows logical position, not spacer position');
    assert.deepStrictEqual(f.errors, []);
    console.log(`PASS virtualizacao ${count} produtos: heights, end, resize, edit, search, hidden, export`);
  } finally { await f.context.close(); }
}

async function testFotosAdiadas(browser) {
  const total = 430;
  const f = await fixture(browser, total, { adiarFotos: true });
  const { page } = f;
  const primeiraLinha = page.locator(rowSelector + '[data-virtual-key="' + sku(1) + '"]');
  const ultimaLinha = page.locator(rowSelector + '[data-virtual-key="' + sku(total) + '"]');
  const esperarLote = async (codigo, storeId, desde = 0) => {
    for (;;) {
      const handle = await page.waitForFunction(([storeId, desde]) =>
        __fotos.chamadas.find(item => item.id >= desde && item.body.store_id === storeId
          && !item.cancelada && __fotos.pendentes().includes(item.id)), [storeId, desde]);
      const lote = await handle.jsonValue();
      if (lote.body.skus.includes(codigo)) return lote;
      await responder(lote, {});
      desde = lote.id + 1;
    }
  };
  const responder = (lote, fotos, { politica = 'politica-1', status = 200 } = {}) => page.evaluate(
    ([id, fotos, politica_fotos, status]) => __fotos.responder(id, { fotos, politica_fotos }, status),
    [lote.id, fotos, politica, status]);
  const cancelada = lote => page.evaluate(id => __fotos.chamadas[id].cancelada, lote.id);

  try {
    const primeira = await esperarLote(sku(1), '');
    assert.strictEqual(primeira.metodo, 'POST');
    assert.strictEqual(primeira.auth, 'Bearer synthetic-token');
    assert.strictEqual(primeira.body.loja, '__todas');
    assert(primeira.body.skus.length <= 64 && primeira.body.skus.length < total);
    assert(primeira.body.skus.every(codigo => primeira.montados.includes(codigo) && codigo.length <= 256));
    assert.strictEqual(new Set(primeira.body.skus).size, primeira.body.skus.length);
    assert(primeira.body.skus.includes(skuLegado), 'a referência usa a mesma grafia do SKU legado');
    assert.strictEqual(await primeiraLinha.locator('img').count(), 0);
    await primeiraLinha.locator('.compra-sugerida-editavel').click();
    const input = primeiraLinha.locator('.compra-sugerida-input');
    await input.fill('17');
    await responder(primeira, { [sku(1)]: 'http://jk.test/foto.gif' });
    await page.waitForFunction(() => document.querySelector('#tblResultado .foto-sku')?.naturalWidth > 0);
    assert.strictEqual(await input.inputValue(), '17');
    assert(await input.evaluate(el => el === document.activeElement));
    await input.press('Enter');
    await settle(page);
    assert.strictEqual(await page.evaluate(codigo => __fotos.chamadas.filter(item => item.body.skus.includes(codigo)).length, sku(1)), 1);

    assert.deepStrictEqual(await page.evaluate(() => __fotos.invalidacoes), [], 'o lote POST é somente leitura');
    await page.evaluate(() => window.dispatchEvent(new Event('jk:cadastro-operation-finished')));
    const anterior = await esperarLote(sku(1), '', primeira.id + 1);
    await page.locator('#lojaBotoes .loja-btn').last().click();
    const loja = await esperarLote(sku(1), 'store-virtual', anterior.id + 1);
    assert.strictEqual(loja.body.loja, 'Loja de teste');
    assert(await cancelada(anterior), 'trocar a loja cancela o lote da visão anterior');
    await responder(anterior, { [sku(1)]: 'http://jk.test/foto.gif?origem=atrasada' });
    await settle(page);
    assert.strictEqual(await primeiraLinha.locator('img').count(), 0);
    await responder(loja, { [sku(1)]: '/api/cadastro/foto-arquivo/foto-loja.png' });
    const fotoLoja = primeiraLinha.locator('img[data-jk-auth-src="/api/cadastro/foto-arquivo/foto-loja.png"]');
    await fotoLoja.waitFor();
    assert(await fotoLoja.getAttribute('data-jk-photo-revision'));

    await scrollTo(page, 1);
    const falha = await esperarLote(sku(total), 'store-virtual', loja.id + 1);
    assert(falha.body.skus.every(codigo => falha.montados.includes(codigo)));
    assert(!falha.body.skus.includes(sku(1)));
    await responder(falha, {}, { status: 503 });
    await settle(page);
    assert.strictEqual(await ultimaLinha.locator('.foto-empty').textContent(), '—');
    assert.strictEqual(await page.evaluate(codigo => __fotos.chamadas.filter(item => item.body.skus.includes(codigo)).length, sku(total)), 1);

    await scrollTo(page, 0);
    await scrollTo(page, 1);
    const novaMontagem = await esperarLote(sku(total), 'store-virtual', falha.id + 1);
    await page.locator('#btnAbaListasPedidos').click();
    assert(await cancelada(novaMontagem), 'abrir lista cancela as fotos da visão');
    await responder(novaMontagem, { [sku(total)]: 'http://jk.test/foto.gif?origem=aba-antiga' });
    await settle(page);
    assert.strictEqual(await page.locator('#tblResultado img').count(), 0);
    await page.locator('#btnAbaLista').click();
    await scrollTo(page, 0);
    const politica = await esperarLote(sku(1), 'store-virtual', novaMontagem.id + 1);
    await responder(politica, { [sku(1)]: 'http://jk.test/foto.gif?origem=politica-antiga' }, { politica: 'politica-2' });
    await settle(page);
    assert.strictEqual(await primeiraLinha.locator('img').count(), 0);
    assert.strictEqual(await primeiraLinha.locator('.compra-sugerida-editavel').textContent(), '3');

    await page.locator('[data-jk-action="periodo"][data-periodo="3"]').click();
    const sessao = await esperarLote(sku(1), 'store-virtual', politica.id + 1);
    await page.evaluate(() => {
      localStorage.setItem('access_token', 'other-synthetic-session');
      window.dispatchEvent(new Event('jk-auth-changed'));
    });
    assert(await cancelada(sessao), 'a mudança de sessão cancela as fotos antigas');
    await responder(sessao, { [sku(1)]: 'http://jk.test/foto.gif?origem=sessao-antiga' });
    await settle(page);
    assert.strictEqual(await page.locator('#tblResultado img').count(), 0);
    assert.deepStrictEqual(f.errors, []);
    console.log('PASS fotos de Médias após a tabela, com lote, edição, virtualização, loja, aba, política e sessão');
  } finally { await f.context.close(); }
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    await testFotosAdiadas(browser);
    await testLargeTable(browser, 50);
    await testLargeTable(browser, 432);
    await testLargeTable(browser, 2000);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
