'use strict';

// Offline browser regression: production loading/rendering and real IndexedDB.
// The browser is closed and reopened with the same profile; no application runs.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const read = relative => fs.readFileSync(path.join(root, relative), 'utf8');
const inline = source => '<script>' + source.replace(/<\/script/gi, '<\\/script') + '</script>';
const profile = fs.mkdtempSync(path.join(process.env.JK_TEST_TMP || os.tmpdir(), 'jk-vendas-cache-profile-'));
const identity = { client_id: 'cache-test', username: 'usuario-a' };
const bootstrap = `(() => {
    if (!localStorage.getItem('user_data')) localStorage.setItem('user_data', JSON.stringify(${JSON.stringify(identity)}));
    if (!localStorage.getItem('permissions')) localStorage.setItem('permissions', JSON.stringify({vendas:true}));
    if (!localStorage.getItem('access_token')) localStorage.setItem('access_token', 'fixture-token');
    window.obterAuthHeaders = () => ({Authorization:'Bearer ' + localStorage.getItem('access_token')});
    window.verificarSessao = () => true;
    window.Chart = class { constructor(ctx, config) { this.canvas=ctx.canvas; this.config=config; this.data=config.data; this.options=config.options; } destroy(){} resize(){} update(){} };
})();`;
const assets = ['static/cache-persistente.js', 'static/vendas/cache-dados.js'];
const strip = html => html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '');
const mainHtml = strip(read('static/vendas.html')).replace('</body>', inline(bootstrap)
    + [...assets, 'static/vendas/runtime.js', 'static/vendas/periodo-cache.js', 'static/vendas/sync.js',
        'static/vendas/dados-render.js', 'static/vendas/periodos-completos.js', 'static/vendas/grafico.js',
        'static/vendas/visual-novo-bridge.js'].map(file => inline(read(file))).join('')
    + inline('function iaRestaurarOuCriarConversa(){return Promise.resolve();}')
    + inline(read('static/vendas/init.js')) + '</body>');
const skuHtml = read('static/vendas_sku.html').replace(/<script\b[^>]*\bsrc=[^>]*>[\s\S]*?<\/script>/gi, '')
    .replace(/<link\b[^>]*>/gi, '').replace('</head>', inline(bootstrap)
        + [...assets, 'static/vendas/periodos-completos.js'].map(file => inline(read(file))).join('') + '</head>');
const mainUrl = 'http://localhost/vendas.html?data_inicio=2026-10-01&data_fim=2026-10-20&loja=Loja+A&unidade_negocio=Canal+A';
const skuUrl = 'http://localhost/vendas_sku.html?sku=001&data_inicio=2026-10-01&data_fim=2026-10-20&loja=Loja+A&unidade_negocio=Canal+A';
const rows = [{ sku: '001', produto: 'Componente', quantidade: 7, itens: 7, pedidos: 2,
    itensDiretos: 2, itensComposicao: 5, itensComponentes: 7, valor: 70, loja_conta: 'Loja A' },
    { sku: '002', produto: 'Outro produto', quantidade: 1, itens: 1, pedidos: 1, valor: 15, loja_conta: 'Loja A' }];
const totals = { pedidos: 3, itens: 8, valor: 85, devolucoes: 0, itensDevolucao: 0,
    valorDevolucao: 0, itensLiquidos: 8, valorLiquido: 85 };
const graph = { labels: ['2026-10-10'], valores_vendas: [85], valores_devolucoes: [0],
    quantidades_vendas: [8], quantidades_devolucoes: [0] };
const sale = { data: '2026-10-10', numero: 'pedido-ficticio', numero_nf: 'nf-ficticia',
    comprador: 'Comprador de teste', sku: '001', quantidade: 7, valor: 70 };
let state;
const errors = [];

async function json(route, body, status = 200) {
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {});
}
async function start() {
    const context = await chromium.launchPersistentContext(profile, { headless: true, viewport: { width: 1250, height: 850 } });
    state = { mode: 'live', holdStores: false, held: [], calls: [] };
    await context.route('**/*', async route => {
        const request = route.request(), url = new URL(request.url());
        if (url.pathname === '/vendas.html') return route.fulfill({ contentType: 'text/html', body: mainHtml });
        if (url.pathname === '/vendas_sku.html') return route.fulfill({ contentType: 'text/html', body: skuHtml });
        if (!url.pathname.startsWith('/api/')) return route.abort();
        state.calls.push({ method: request.method(), path: url.pathname, search: url.search });
        assert.equal(request.method(), 'GET', 'Opening or restoring the page must not start synchronization');
        if ((state.mode === 'hold' && /^\/api\/(vendas|notas-entrada)/.test(url.pathname))
            || (state.holdStores && url.pathname === '/api/lojas')) { state.held.push(route); return; }
        if (state.mode === 'error') return json(route, {}, 503);
        if (url.pathname === '/api/lojas') return json(route, [{ store_id: 'store-a', nome: 'Loja A' }]);
        if (url.pathname === '/api/unidades-negocios') return json(route, { unidades: [{ nome: 'Canal A' }], mapeamento: {} });
        if (url.pathname === '/api/vendas/resumo') return json(route, { rows, totals, meta: {} });
        if (url.pathname === '/api/vendas/grafico') return state.mode === 'graph403' ? json(route, {}, 403) : json(route, graph);
        if (url.pathname.startsWith('/api/notas-entrada/sku/')) return json(route, []);
        if (url.pathname === '/api/vendas') return json(route, url.searchParams.has('incluir_ebazar')
            ? [sale, { ...sale, comprador: 'EBAZAR', numero: 'transferencia-ficticia' }] : [sale]);
        if (url.pathname === '/api/vendas/sync/progress') return json(route, { active: false, automacao: { jobs: [], lojas: [] } });
        if (url.pathname === '/api/vendas/limites') return json(route, { data_min: '2026-10-01', data_max: '2026-10-20' });
        return json(route, {});
    });
    context.on('page', page => page.on('pageerror', error => errors.push(String(error))));
    return context;
}
async function open(context, url) {
    const page = await context.newPage();
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    return page;
}
async function savedMain(page) {
    return page.evaluate(async () => JKCachePersistente.ler(chaveCacheTelaVendas(), { contexto: JKCachePersistente.contexto() }));
}
async function release(status, predicate = () => true) {
    const selected = state.held.filter(predicate);
    state.held = state.held.filter(route => !selected.includes(route));
    await Promise.all(selected.map(route => json(route, status === 200 ? [{ store_id: 'store-a', nome: 'Loja A' }] : {}, status)));
}

async function main() {
    let context = await start();
    try {
        let page = await open(context, mainUrl);
        await page.locator('#tBody tr[data-sku="001"]').waitFor();
        await page.waitForFunction(() => JKVendasLegacyBridge.getSnapshot().graph.data?.labels?.length === 1);
        await page.evaluate(async () => { filtroTexto.value = '001'; await filtrar(); await salvarCacheTelaVendas(); });
        let snapshot = await savedMain(page);
        assert.ok(snapshot, 'The confirmed list must be saved');
        assert.equal(snapshot.dados.dados[0].itensComposicao, 5);
        assert.equal(snapshot.dados.filtroTexto, '001');
        assert.equal(snapshot.dados.resumoTotaisVendas.valor, 85);

        const sku = await open(context, skuUrl);
        await sku.waitForFunction(() => document.querySelector('#tBody')?.textContent.includes('pedido-ficticio'));
        await sku.waitForFunction(async () => !!await JKCachePersistente.ler(prepararConsultaVendasSku().chave,
            { contexto: JKCachePersistente.contexto() }));
        assert.match(await sku.locator('#btnTransferenciaFull').textContent(), /1/);
        const savedAt = (await savedMain(page)).salvoEm;
        await context.close();

        // A new browser process has no previous JavaScript state or sessionStorage.
        context = await start();
        state.mode = 'hold'; state.holdStores = true;
        page = await open(context, mainUrl);
        await page.locator('#tBody tr[data-sku="001"]').waitFor();
        assert.equal(await page.locator('#filtroTexto').inputValue(), '001');
        assert.match(await page.locator('#cacheVendasStatus').textContent(), /dados salvos/);
        assert.equal(state.calls.filter(call => call.path === '/api/vendas/resumo').length, 0,
            'The saved list must appear even while the store request is still pending');
        state.holdStores = false;
        await release(200, route => new URL(route.request().url()).pathname === '/api/lojas');
        await page.waitForFunction(() => JKVendasLegacyBridge.getSnapshot().graph.data?.labels?.length === 1);
        state.mode = 'error';
        await release(503);
        await page.waitForFunction(() => document.getElementById('cacheVendasStatus').textContent.includes('Atualização indisponível'));
        assert.equal(await page.locator('#tBody tr[data-sku="001"]').count(), 1);
        snapshot = await savedMain(page);
        assert.equal(snapshot.salvoEm, savedAt, 'A cached render or failed refresh must not renew the saved timestamp');
        assert.equal(snapshot.dados.dados[0].itensComposicao, 5);

        state.mode = 'hold';
        const reopenedSku = await open(context, skuUrl);
        await reopenedSku.waitForFunction(() => document.querySelector('#tBody')?.textContent.includes('pedido-ficticio'));
        assert.match(await reopenedSku.locator('#cacheVendasStatus').textContent(), /dados salvos/);
        state.mode = 'error'; await release(503);
        await reopenedSku.waitForFunction(() => document.getElementById('cacheVendasStatus').textContent.includes('Atualização indisponível'));
        assert.match(await reopenedSku.locator('#tBody').textContent(), /pedido-ficticio/);
        await reopenedSku.close();

        state.mode = 'hold';
        await page.evaluate(() => { setDataRangeISO('2026-09-01', '2026-09-20'); void carregarVendas(); });
        await page.waitForFunction(() => document.getElementById('tBody').textContent === '');
        state.mode = 'error'; await release(503);
        assert.equal(await page.locator('#tBody tr').count(), 0, 'A different period cannot reuse the previous period rows');

        await page.evaluate(() => {
            localStorage.setItem('user_data', JSON.stringify({client_id:'cache-test',username:'usuario-b'}));
            window.dispatchEvent(new Event('jk-auth-changed'));
        });
        await page.goto(mainUrl, { waitUntil: 'domcontentloaded' });
        await page.waitForFunction(() => document.getElementById('status').textContent.includes('Erro'));
        assert.equal(await page.locator('#tBody tr').count(), 0, 'Another user cannot recover the first user snapshot');

        await page.evaluate(() => {
            localStorage.setItem('user_data', JSON.stringify({client_id:'cache-test',username:'usuario-a'}));
            localStorage.setItem('access_token', 'renewed-fixture-token');
        });
        state.mode = 'hold';
        await page.goto(mainUrl, { waitUntil: 'domcontentloaded' });
        await page.locator('#tBody tr[data-sku="001"]').waitFor();
        state.mode = 'error'; await release(401);
        await page.waitForFunction(() => document.getElementById('status').textContent.includes('Acesso negado'));
        assert.equal(await page.locator('#tBody tr').count(), 0, '401 must clear an already rendered snapshot');

        await page.evaluate(() => localStorage.setItem('access_token', 'another-renewed-fixture-token'));
        state.mode = 'graph403';
        await page.goto(mainUrl, { waitUntil: 'domcontentloaded' });
        await page.locator('#tBody tr[data-sku="001"]').waitFor();
        await page.waitForFunction(async () => !!await JKCachePersistente.ler(chaveCacheTelaVendas(), {contexto:JKCachePersistente.contexto()}));
        assert.ok(await savedMain(page), '403 from an optional graph cannot invalidate the authorized list');
        state.mode = 'live';
        const corruptSku = await open(context, skuUrl);
        await corruptSku.waitForFunction(() => document.querySelector('#tBody')?.textContent.includes('pedido-ficticio'));
        await corruptSku.evaluate(() => JKCachePersistente.salvar(prepararConsultaVendasSku().chave,
            {schema:1,dados:[null],devolucoes:[],transferencias:[]}, {contexto:JKCachePersistente.contexto(),tags:['vendas']}));
        const skuReadsBefore = state.calls.filter(call => call.path === '/api/vendas').length;
        await corruptSku.reload({waitUntil:'domcontentloaded'});
        await corruptSku.waitForFunction(() => document.querySelector('#tBody')?.textContent.includes('pedido-ficticio'));
        assert.ok(state.calls.filter(call => call.path === '/api/vendas').length > skuReadsBefore,
            'A malformed cached SKU must still fetch and render live data');
        assert.ok(state.calls.every(call => call.method === 'GET'));
        assert.deepEqual(errors, []);
        console.log('PASS Vendas: real browser restart restores list/filter/composition and SKU; offline failure preserves data; period/user isolation, 401 cleanup and optional 403 pass.');
    } finally { await context.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
