'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const staticRoot = path.join(root, 'static');
const stores = [
  { store_id: 'store-a', nome: 'Loja repetida' },
  { store_id: 'store-b', nome: 'Loja repetida' },
];
const lookupPayload = {
  success: true,
  read_only: true,
  coverage_complete: true,
  store_id: 'store-a',
  sku: 'SKU-ML',
  campos: {
    foto: 'https://http2.mlstatic.com/photo.jpg',
    mlb_principal: 'MLB100',
    mlb_ids: 'MLB100|MLB200',
    qtd_anuncios_mlb: '2',
    titulo_ml: '<img src=x onerror="window.mlInjected=true">',
    titulos_anuncios_mlb: 'Titulo principal || Titulo secundario',
    categoria: 'Autopeças',
    categoria_id_mlb: 'MLB1234',
    marca: 'Marca API',
    modelo: 'Modelo API',
    gtins_mlb: '789|790',
    descricao: '<script>window.mlInjected=true</script>Descricao segura',
  },
  foto: {
    url: 'https://http2.mlstatic.com/photo.jpg',
    data_url: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB',
    filename: 'MLB100.png',
  },
  avisos: [],
};
let lookupResponse = lookupPayload;
let lookupGate = null;

function contentType(filePath) {
  if (filePath.endsWith('.css')) return 'text/css; charset=utf-8';
  if (filePath.endsWith('.js')) return 'application/javascript; charset=utf-8';
  if (filePath.endsWith('.html')) return 'text/html; charset=utf-8';
  return 'application/octet-stream';
}

async function json(route, payload, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) });
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const requests = [];
  const mutations = [];
  const pageErrors = [];
  try {
    const page = await browser.newPage();
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.addInitScript(() => {
      localStorage.setItem('user_data', JSON.stringify({ client_id: 'client-browser' }));
    });
    await page.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      requests.push({ method: request.method(), url: url.toString(), headers: request.headers() });

      if (url.pathname === '/auth.js') {
        await route.fulfill({
          status: 200,
          contentType: 'application/javascript; charset=utf-8',
          body: `window.verificarSessao=()=>true;
            window.obterAuthHeaders=extra=>Object.assign({Authorization:'Bearer browser-test'},extra||{});`,
        });
        return;
      }
      if (url.pathname === '/ia-sidebar.js') {
        await route.fulfill({ status: 200, contentType: 'application/javascript; charset=utf-8', body: '' });
        return;
      }
      if (url.pathname === '/cadastro_incluir.html' || url.pathname === '/cadastro_editar_item.html') {
        const target = path.join(staticRoot, url.pathname.slice(1));
        await route.fulfill({ status: 200, contentType: contentType(target), body: fs.readFileSync(target) });
        return;
      }
      if (url.pathname.startsWith('/cadastro/')) {
        const target = path.resolve(staticRoot, url.pathname.replace(/^\/+/, ''));
        assert(target.startsWith(staticRoot + path.sep));
        await route.fulfill({ status: 200, contentType: contentType(target), body: fs.readFileSync(target) });
        return;
      }
      if (url.pathname === '/api/lojas') {
        await json(route, stores);
        return;
      }
      if (url.pathname === '/api/cadastro/lojas/store-a/colunas') {
        await json(route, { colunas: ['sku', 'nome', 'categoria', 'marca', 'descricao', 'foto', 'mlb_ids', 'custos_frete_mlb'] });
        return;
      }
      if (url.pathname === '/api/cadastro/lojas/store-a/mercado-livre/produto') {
        assert.strictEqual(request.method(), 'GET');
        assert.strictEqual(url.searchParams.get('sku'), 'SKU-ML');
        assert.strictEqual(request.headers().authorization, 'Bearer browser-test');
        if (lookupGate) await lookupGate;
        await json(route, lookupResponse);
        return;
      }
      if (url.pathname === '/api/cadastro/lojas/store-a/produtos/SKU-ML' && request.method() === 'GET') {
        await json(route, {
          produto: {
            sku: 'SKU-ML',
            nome: 'Nome interno preservado',
            foto: 'foto-existente.png',
            mlb_principal: 'MLB200',
            mlb_ids: 'MLB200',
            custos_frete_mlb: '22,00',
            row_version: 7,
          },
        });
        return;
      }
      if (url.pathname === '/api/cadastro/lojas/store-a/produtos' && request.method() === 'POST') {
        mutations.push({ method: request.method(), body: request.postDataJSON() });
        await json(route, { success: true, sku: 'SKU-ML' });
        return;
      }
      if (url.pathname === '/api/cadastro/lojas/store-a/produtos/SKU-ML' && request.method() === 'PUT') {
        mutations.push({ method: request.method(), body: request.postDataJSON() });
        await json(route, { success: true, sku: 'SKU-ML', foto: 'cadastro_fotos/lojas/store-a/MLB100.png' });
        return;
      }
      if (url.pathname === '/cadastro.html' || url.pathname === '/cadastro_editar.html') {
        await route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>destino</title>' });
        return;
      }
      await route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"not found"}' });
    });

    await page.goto('http://jk.local/cadastro_incluir.html?store_id=store-a', { waitUntil: 'load' });
    await page.waitForFunction(() => document.querySelector('#status').textContent.includes('Preencha os dados'));
    await page.locator('[name="sku"]').fill('SKU-ML');
    const beforeIncludeLookup = mutations.length;
    await page.locator('#btnBuscarMercadoLivre').click();
    try {
      await page.waitForFunction(() => document.querySelector('#status').textContent.includes('2 anúncio(s) encontrado(s)'), null, { timeout: 8000 });
    } catch (error) {
      const status = await page.locator('#status').textContent();
      throw new Error(`${error.message}; status=${status}; pageErrors=${pageErrors.join('|')}`);
    }
    assert.strictEqual(mutations.length, beforeIncludeLookup, 'buscar no ML não pode cadastrar automaticamente');
    assert.strictEqual(await page.locator('[name="nome"]').inputValue(), '', 'título ML não pode sobrescrever nome interno');
    assert.strictEqual(await page.locator('[name="titulo_ml"]').inputValue(), lookupPayload.campos.titulo_ml);
    assert.strictEqual(await page.locator('[name="categoria_id_mlb"]').inputValue(), 'MLB1234');
    assert.strictEqual(await page.locator('[name="modelo"]').inputValue(), 'Modelo API');
    assert.deepStrictEqual(await page.locator('.mlb-id').evaluateAll(inputs => inputs.map(item => item.value)), ['MLB100', 'MLB200']);
    assert.strictEqual(await page.locator('#fotoPreview img').getAttribute('src'), lookupPayload.foto.data_url);
    assert.strictEqual(await page.evaluate(() => window.mlInjected), undefined);

    await page.locator('#formIncluir button[type="submit"]').click();
    await page.waitForFunction(() => document.querySelector('#status').textContent.includes('incluído com sucesso'));
    assert.strictEqual(mutations.length, 1);
    assert.strictEqual(mutations[0].method, 'POST');
    assert.strictEqual(mutations[0].body.titulo_ml, lookupPayload.campos.titulo_ml);
    assert.strictEqual(mutations[0].body.__foto_data_url, lookupPayload.foto.data_url);

    await page.goto('http://jk.local/cadastro_editar_item.html?store_id=store-a&sku=SKU-ML', { waitUntil: 'load' });
    await page.waitForFunction(() => document.querySelector('#status').textContent.includes('SKU carregado'));
    const beforeEditLookup = mutations.length;
    lookupResponse = {
      ...lookupPayload,
      coverage_complete: false,
      campos: { ...lookupPayload.campos, mlb_ids: 'MLB100', qtd_anuncios_mlb: '1' },
    };
    await page.locator('#btnBuscarMercadoLivre').click();
    await page.waitForFunction(() => document.querySelector('#status').textContent.includes('consulta do Mercado Livre ficou incompleta'));
    assert.deepStrictEqual(await page.locator('.mlb-id').evaluateAll(inputs => inputs.map(item => item.value)), ['MLB200']);
    assert.deepStrictEqual(await page.locator('.mlb-frete').evaluateAll(inputs => inputs.map(item => item.value)), ['22,00']);
    assert.strictEqual(await page.locator('[name="foto"]').inputValue(), 'foto-existente.png');

    lookupResponse = {
      ...lookupPayload,
      foto: {
        ...lookupPayload.foto,
        data_url: 'data:image/png;base64,x" onerror="window.mlPhotoInjected=true"',
      },
    };
    await page.locator('#btnBuscarMercadoLivre').click();
    await page.waitForFunction(() => document.querySelector('#status').textContent.includes('2 anúncio(s) encontrado(s)'));
    assert.strictEqual(await page.locator('[name="foto"]').inputValue(), 'foto-existente.png');
    assert.strictEqual(await page.evaluate(() => window.mlPhotoInjected), undefined);

    let releaseLookup;
    lookupResponse = lookupPayload;
    lookupGate = new Promise(resolve => { releaseLookup = resolve; });
    await page.locator('#btnBuscarMercadoLivre').click();
    await page.waitForFunction(() => document.querySelector('#btnBuscarMercadoLivre').disabled);
    await page.evaluate(() => {
      const select = document.querySelector('#cadastroLojaSelect');
      select.value = '';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    assert.strictEqual(await page.locator('#btnBuscarMercadoLivre').isEnabled(), true, 'seleção inválida durante consulta não pode travar formulário');
    releaseLookup();
    lookupGate = null;

    await page.locator('#btnBuscarMercadoLivre').click();
    await page.waitForFunction(() => document.querySelector('#status').textContent.includes('2 anúncio(s) encontrado(s)'));
    assert.strictEqual(mutations.length, beforeEditLookup, 'buscar no ML não pode salvar automaticamente');
    assert.strictEqual(await page.locator('[name="nome"]').inputValue(), 'Nome interno preservado');
    assert.deepStrictEqual(await page.locator('.mlb-id').evaluateAll(inputs => inputs.map(item => item.value)), ['MLB100', 'MLB200']);
    assert.deepStrictEqual(await page.locator('.mlb-frete').evaluateAll(inputs => inputs.map(item => item.value)), ['', '22,00']);
    const editLookup = requests.filter(item => new URL(item.url).pathname.endsWith('/mercado-livre/produto')).at(-1);
    assert.strictEqual(new URL(editLookup.url).searchParams.get('mlb_principal'), 'MLB200');

    await page.locator('#formEditarItem button[type="submit"]').click();
    await page.waitForFunction(() => document.title === 'destino');
    assert.strictEqual(mutations.length, 2);
    assert.strictEqual(mutations[1].method, 'PUT');
    assert.strictEqual(mutations[1].body.row_version, 7);
    assert.strictEqual(mutations[1].body.nome, 'Nome interno preservado');
    assert.strictEqual(mutations[1].body.__foto_data_url, lookupPayload.foto.data_url);
    assert.deepStrictEqual(pageErrors, []);
    console.log('cadastro mercado livre browser: OK');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
