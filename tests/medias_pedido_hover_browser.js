'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'medias_compras.html'), 'utf8');
const json = (route, body, status = 200) => route.fulfill({
  status,
  contentType: 'application/json',
  body: JSON.stringify(body),
});
const photoA = 'http://photos.test/produto-a.svg';
const photoB = 'http://photos.test/produto-b.svg';
const item = (sku, foto = photoA) => ({
  SKU: sku,
  Foto: foto,
  'Título do produto em inglês': 'Produto ' + sku,
  Quantidade: 2,
  'Valor unidade': 10,
  'Valor total': 20,
});
const listas = [{
  id: 'lista-uai',
  nome_lista: 'Pedido Uai',
  loja: 'Uai Mineirinho',
  store_id: 'store-uai',
  status: 'Lista gerada',
  created_at: '2026-10-13T10:00:00Z',
  updated_at: '2026-10-13T10:00:00Z',
  itens: [item('001'), item('002', photoB), item('003'), item('004'), item('005'), item('006'), item('007', '')],
}, {
  id: 'lista-jk',
  nome_lista: 'Pedido JK',
  loja: 'JK Pecas',
  store_id: 'store-jk',
  status: 'Lista gerada',
  created_at: '2026-10-13T10:00:00Z',
  updated_at: '2026-10-13T10:00:00Z',
  itens: [item('008', photoB)],
}];

(async () => {
  const browser = await chromium.launch({ headless: true });
  const consultas = [];
  const pendentes = new Map();
  const routeErrors = [];
  const respostas = new Set();
  let cenarios = 0;
  const padrao = url => ({
    sku: url.searchParams.get('sku'),
    loja: url.searchParams.get('loja'),
    store_id: url.searchParams.get('store_id'),
    saldo_atual_estoque: 12,
    estoque_em_transito: 9,
    estoque_em_transito_listas: [{ nome_lista: 'Pedido A', quantidade: 5 }, { nome_lista: 'Pedido B', quantidade: 4 }],
  });

  try {
    const page = await browser.newPage({ viewport: { width: 1536, height: 920 } });
    const errosPagina = [];
    page.on('pageerror', error => errosPagina.push(error.message));
    await page.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.pathname === '/medias_compras.html') {
        await route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: html });
        return;
      }
      if (url.pathname.startsWith('/medias_compras/')) {
        const relativePath = url.pathname.replace(/^\//, '');
        await route.fulfill({
          status: 200,
          contentType: relativePath.endsWith('.css') ? 'text/css' : 'application/javascript',
          body: fs.readFileSync(path.join(root, 'static', relativePath)),
        });
        return;
      }
      if (url.pathname === '/auth.js') {
        await route.fulfill({ status: 200, contentType: 'application/javascript', body:
          'window.verificarSessao = () => true; window.obterAuthHeaders = () => ({});' });
        return;
      }
      if (url.hostname === 'photos.test') {
        const color = url.pathname.includes('produto-a') ? '#46aee8' : '#d8a23d';
        await route.fulfill({ status: 200, contentType: 'image/svg+xml', body:
          '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="300" viewBox="0 0 600 300">' +
          '<rect width="600" height="300" fill="' + color + '"/><circle cx="300" cy="150" r="95" fill="#111827"/></svg>' });
        return;
      }
      if (url.pathname === '/api/lojas') {
        await json(route, [{ nome: 'JK Pecas', store_id: 'store-jk' }, { nome: 'Uai Mineirinho', store_id: 'store-uai' }]);
        return;
      }
      if (url.pathname === '/api/cadastro/fornecedores') {
        await json(route, []);
        return;
      }
      if (url.pathname === '/api/medias-compras/preferencias-skus-ocultos') {
        await json(route, { skus_ocultos: [] });
        return;
      }
      if (url.pathname === '/api/medias-compras/visao') {
        await json(route, { colunas_meses: [], itens: [] });
        return;
      }
      if (url.pathname === '/api/medias-compras/listas-pedidos') {
        await json(route, { listas: listas.map(lista => ({ ...lista, itens: undefined, total_itens: lista.itens.length })) });
        return;
      }
      const listaMatch = url.pathname.match(/^\/api\/medias-compras\/listas-pedidos\/([^/]+)$/);
      if (listaMatch) {
        await json(route, { lista: listas.find(lista => lista.id === listaMatch[1]) });
        return;
      }
      if (url.pathname === '/api/medias-compras/posicao-estoque') {
        assert.strictEqual(request.method(), 'GET');
        const sku = url.searchParams.get('sku');
        consultas.push({ sku, loja: url.searchParams.get('loja'), store_id: url.searchParams.get('store_id') });
        let body = padrao(url);
        let status = 200;
        if (sku === '002') body = { ...body, saldo_atual_estoque: 0, estoque_em_transito: 0, estoque_em_transito_listas: [] };
        if (sku === '007') body = { ...body, estoque_em_transito: null, estoque_em_transito_listas: [] };
        if (sku === '003') { body = { detail: 'Fonte de estoque indisponível' }; status = 503; }
        if (['004', '005', '006'].includes(sku)) {
          await new Promise(resolve => pendentes.set(sku, resolve));
          body = { ...body, saldo_atual_estoque: 987, estoque_em_transito: 654 };
        }
        try {
          await json(route, body, status);
        } catch (error) {
          // A request cancelled on mouse leave, list change or timeout may no longer accept a response.
          if (!/closed|handled|cancel|abort/i.test(String(error.message))) routeErrors.push(error.message);
        } finally {
          respostas.add(sku);
        }
        return;
      }
      await route.fulfill({ status: 200, contentType: 'application/javascript', body: '' });
    });

    const qtd = sku => page.locator('#tblListaPedidoItens tbody tr').filter({
      has: page.locator('input[data-kind="sku"][value="' + sku + '"]'),
    }).locator('input[data-kind="qtd"]');
    const foto = sku => page.locator('#tblListaPedidoItens tbody tr').filter({
      has: page.locator('input[data-kind="sku"][value="' + sku + '"]'),
    }).locator('.foto-sku');
    const popupFoto = page.locator('#balaoFotoListaPedido');
    const popupQtd = page.locator('#balaoQuantidadeListaPedido');
    const visivel = async id => {
      await page.locator('#' + id).waitFor({ state: 'visible' });
      await page.waitForFunction(id => {
        const el = document.getElementById(id);
        return el && el.getAttribute('aria-hidden') !== 'true' && getComputedStyle(el).opacity === '1';
      }, id);
    };
    const oculto = id => page.waitForFunction(id => {
      const el = document.getElementById(id);
      return !el || el.getAttribute('aria-hidden') === 'true' ||
        getComputedStyle(el).visibility === 'hidden' || getComputedStyle(el).display === 'none';
    }, id);
    const aguardarTexto = texto => page.waitForFunction(texto => {
      const el = document.getElementById('balaoQuantidadeListaPedido');
      return el && el.getAttribute('aria-hidden') !== 'true' && el.textContent.includes(texto);
    }, texto);
    const afastar = async () => {
      await page.mouse.move(12, 12);
      await page.locator('body').click({ position: { x: 10, y: 10 } });
    };
    const abrirLista = async nome => {
      await page.locator('.lista-item').filter({ hasText: nome }).click();
      await page.locator('#nomeListaPedidoEdit').evaluate((el, nome) => {
        if (el.value !== nome) throw new Error('Lista ainda não carregada: ' + el.value);
      }, nome).catch(async () => page.waitForFunction(nome => document.getElementById('nomeListaPedidoEdit').value === nome, nome));
      await page.locator('#editorListaPedido:not(.hidden)').waitFor();
    };
    const viewportContem = async locator => {
      const bounds = await locator.boundingBox();
      assert(bounds, 'Balão deve ocupar espaço visível');
      const viewport = page.viewportSize();
      assert(bounds.x >= 0 && bounds.y >= 0, 'Balão não deve ultrapassar o início da janela');
      assert(bounds.x + bounds.width <= viewport.width + 1, 'Balão não deve ultrapassar a largura da janela');
      assert(bounds.y + bounds.height <= viewport.height + 1, 'Balão não deve ultrapassar a altura da janela');
    };
    const liberar = async sku => {
      assert(pendentes.has(sku), 'Consulta pendente esperada para SKU ' + sku);
      pendentes.get(sku)();
      pendentes.delete(sku);
      const deadline = Date.now() + 3000;
      while (!respostas.has(sku) && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 20));
      assert(respostas.has(sku), 'Resposta atrasada deve concluir no mock');
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    };
    const aguardarConsulta = sku => page.waitForRequest(request => {
      const url = new URL(request.url());
      return url.pathname === '/api/medias-compras/posicao-estoque' && url.searchParams.get('sku') === sku;
    });

    await page.goto('http://jk.test/medias_compras.html');
    await page.locator('#lojaBotoes button').filter({ hasText: 'JK Pecas' }).click();
    await page.locator('#btnAbaListasPedidos').click();
    await abrirLista('Pedido Uai');
    assert.strictEqual(await page.evaluate(() => storeIdSelecionado), 'store-jk');

    // A real photo is enlarged proportionally and the next row uses its own photo.
    await foto('001').hover();
    await visivel('balaoFotoListaPedido');
    const imagemMaior = popupFoto.locator('img');
    await imagemMaior.evaluate(img => new Promise((resolve, reject) => {
      if (img.complete && img.naturalWidth) resolve();
      else { img.onload = resolve; img.onerror = () => reject(new Error('Foto ampliada não carregou')); }
    }));
    assert.strictEqual(await imagemMaior.evaluate(img => img.currentSrc || img.src), photoA);
    assert.strictEqual(await imagemMaior.evaluate(img => getComputedStyle(img).objectFit), 'contain');
    const thumbBounds = await foto('001').boundingBox();
    const enlargedBounds = await imagemMaior.boundingBox();
    assert(enlargedBounds.width > thumbBounds.width * 2 && enlargedBounds.height > thumbBounds.height * 2);
    await viewportContem(popupFoto);
    if (process.env.JK_HOVER_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.JK_HOVER_SCREENSHOT_DIR, 'foto-ampliada.png'), fullPage: false, animations: 'disabled' });
    await foto('002').hover();
    await visivel('balaoFotoListaPedido');
    assert.strictEqual(await imagemMaior.evaluate(img => img.currentSrc || img.src), photoB);
    await afastar();
    await oculto('balaoFotoListaPedido');
    cenarios += 1;

    // A previously authenticated thumbnail reuses its hydrated blob instead of exposing a protected URL.
    const blobFoto = await foto('001').evaluate(img => new Promise(resolve => {
      const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="300"><rect width="600" height="300" fill="#73c9a5"/></svg>';
      const source = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }));
      img.setAttribute('data-jk-auth-src', '/api/cadastro/foto-arquivo/produto-001');
      img.onload = () => resolve(source);
      img.src = source;
    }));
    await foto('001').hover();
    await visivel('balaoFotoListaPedido');
    assert.strictEqual(await imagemMaior.evaluate(img => img.currentSrc || img.src), blobFoto);
    await imagemMaior.evaluate(img => new Promise((resolve, reject) => {
      if (img.complete && img.naturalWidth) resolve();
      else { img.onload = resolve; img.onerror = () => reject(new Error('Foto autenticada não carregou')); }
    }));
    assert.strictEqual(await imagemMaior.evaluate(img => getComputedStyle(img).objectFit), 'contain');
    await afastar();
    await oculto('balaoFotoListaPedido');
    cenarios += 1;

    // The order's store determines the result, independently of the global filter.
    await qtd('001').hover();
    await visivel('balaoQuantidadeListaPedido');
    await aguardarTexto('Pedido A');
    let texto = await popupQtd.innerText();
    assert.match(texto, /Estoque atual\s*12\b/);
    assert.match(texto, /Pedidos a caminho\s*9\b/);
    assert.match(texto, /Pedido A\s*5\b/);
    assert.match(texto, /Pedido B\s*4\b/);
    assert.deepStrictEqual(consultas.find(consulta => consulta.sku === '001'), {
      sku: '001', loja: 'Uai Mineirinho', store_id: 'store-uai',
    });
    await viewportContem(popupQtd);
    if (process.env.JK_HOVER_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.JK_HOVER_SCREENSHOT_DIR, 'quantidade-estoque.png'), fullPage: false, animations: 'disabled' });
    await afastar();
    await oculto('balaoQuantidadeListaPedido');
    cenarios += 1;

    // Keyboard focus opens details while the quantity remains editable.
    await qtd('001').focus();
    await visivel('balaoQuantidadeListaPedido');
    await aguardarTexto('Pedido A');
    await qtd('001').fill('17');
    assert.strictEqual(await qtd('001').inputValue(), '17');
    assert.strictEqual(await qtd('001').isEditable(), true);
    await page.keyboard.press('Escape');
    await oculto('balaoQuantidadeListaPedido');
    await afastar();
    cenarios += 1;

    // Selecting another store in the order clears the old result and queries the new scope.
    await qtd('001').hover();
    await visivel('balaoQuantidadeListaPedido');
    await aguardarTexto('Pedido A');
    await page.locator('#lojaListaPedidoEdit').selectOption('store-jk');
    await oculto('balaoQuantidadeListaPedido');
    await afastar();
    await qtd('001').hover();
    await visivel('balaoQuantidadeListaPedido');
    await aguardarTexto('Pedido A');
    assert.deepStrictEqual(consultas.at(-1), { sku: '001', loja: 'JK Pecas', store_id: 'store-jk' });
    await page.locator('#lojaListaPedidoEdit').selectOption('store-uai');
    await oculto('balaoQuantidadeListaPedido');
    await afastar();
    cenarios += 1;

    // A confirmed zero must not be rendered as missing data; failures must not fabricate zero.
    await qtd('002').hover();
    await visivel('balaoQuantidadeListaPedido');
    await aguardarTexto('Estoque atual');
    await page.waitForFunction(() => /Estoque atual\s*0\b/.test(document.getElementById('balaoQuantidadeListaPedido').innerText));
    texto = await popupQtd.innerText();
    assert.match(texto, /Pedidos a caminho\s*0\b/);
    assert.doesNotMatch(texto, /indispon[ií]vel|n[aã]o foi poss[ií]vel/i);
    await afastar();
    await qtd('003').hover();
    await visivel('balaoQuantidadeListaPedido');
    await page.waitForFunction(() => /indispon[ií]vel|n[aã]o foi poss[ií]vel/i.test(document.getElementById('balaoQuantidadeListaPedido').innerText));
    texto = await popupQtd.innerText();
    assert.doesNotMatch(texto, /Estoque atual\s*0\b|Pedidos a caminho\s*0\b/);
    await afastar();
    cenarios += 1;

    // A late result for a previous row cannot replace the currently hovered row.
    let consultaPendente = aguardarConsulta('004');
    await qtd('004').hover();
    await consultaPendente;
    await qtd('002').hover();
    await visivel('balaoQuantidadeListaPedido');
    await page.waitForFunction(() => /Estoque atual\s*0\b/.test(document.getElementById('balaoQuantidadeListaPedido').innerText));
    await liberar('004');
    texto = await popupQtd.innerText();
    assert.match(texto, /Estoque atual\s*0\b/);
    assert.doesNotMatch(texto, /987|654/);
    await afastar();
    cenarios += 1;

    // Changing lists cancels visible details and keeps delayed results hidden.
    consultaPendente = aguardarConsulta('005');
    await qtd('005').hover();
    await consultaPendente;
    await abrirLista('Pedido JK');
    await oculto('balaoQuantidadeListaPedido');
    await liberar('005');
    await oculto('balaoQuantidadeListaPedido');
    await foto('008').hover();
    await visivel('balaoFotoListaPedido');
    assert.strictEqual(await imagemMaior.evaluate(img => img.currentSrc || img.src), photoB);
    await abrirLista('Pedido Uai');
    await oculto('balaoFotoListaPedido');
    cenarios += 1;

    // Scroll and Escape dismiss a popup; narrow windows keep the enlargement inside the viewport.
    await qtd('001').hover();
    await visivel('balaoQuantidadeListaPedido');
    await page.locator('#tblListaPedidoItens').evaluate(table => table.parentElement.dispatchEvent(new Event('scroll')));
    await oculto('balaoQuantidadeListaPedido');
    await afastar();
    await page.setViewportSize({ width: 720, height: 620 });
    await afastar();
    await foto('001').scrollIntoViewIfNeeded();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await foto('001').hover();
    await visivel('balaoFotoListaPedido');
    await viewportContem(popupFoto);
    await page.keyboard.press('Escape');
    await oculto('balaoFotoListaPedido');
    await afastar();
    await page.setViewportSize({ width: 1536, height: 920 });
    cenarios += 1;

    // A blocked source becomes unavailable after five seconds and cannot revive on late completion.
    consultaPendente = aguardarConsulta('006');
    await qtd('006').hover();
    await consultaPendente;
    await visivel('balaoQuantidadeListaPedido');
    await page.waitForFunction(() => /indispon[ií]vel|n[aã]o foi poss[ií]vel/i.test(document.getElementById('balaoQuantidadeListaPedido').innerText), null, { timeout: 8000 });
    texto = await popupQtd.innerText();
    assert.doesNotMatch(texto, /Estoque atual\s*0\b|Pedidos a caminho\s*0\b/);
    await liberar('006');
    assert.doesNotMatch(await popupQtd.innerText(), /987|654/);
    await afastar();
    await oculto('balaoQuantidadeListaPedido');
    cenarios += 1;

    // A missing photo remains an explicit empty state, without reusing the previous product.
    const semFoto = page.locator('#tblListaPedidoItens tbody tr').filter({
      has: page.locator('input[data-kind="sku"][value="007"]'),
    }).locator('.foto-empty');
    assert.match(await semFoto.innerText(), /Sem foto/i);
    await semFoto.hover();
    await oculto('balaoFotoListaPedido');
    await qtd('007').hover();
    await visivel('balaoQuantidadeListaPedido');
    await page.waitForFunction(() => /Pedidos a caminho\s*Indisponível/i.test(document.getElementById('balaoQuantidadeListaPedido').innerText));
    texto = await popupQtd.innerText();
    assert.match(texto, /Estoque atual\s*12\b/);
    assert.match(texto, /Pedidos a caminho\s*Indisponível/i);
    await afastar();
    await oculto('balaoQuantidadeListaPedido');
    assert.deepStrictEqual(errosPagina, []);
    assert.deepStrictEqual(routeErrors, []);
    cenarios += 1;

    console.log('medias pedido hover browser: OK (' + cenarios + ' cenários)');
  } finally {
    for (const resolve of pendentes.values()) resolve();
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
