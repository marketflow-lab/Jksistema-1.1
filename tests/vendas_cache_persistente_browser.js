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
const temporaryRoot = path.resolve(process.env.JK_TEST_TMP || os.tmpdir());
const profile = fs.mkdtempSync(path.join(temporaryRoot, 'jk-vendas-cache-profile-'));
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
const maxUrl = 'http://localhost/vendas.html?loja=Loja+A&unidade_negocio=Canal+A';
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
        if (url.pathname === '/api/vendas/grafico') {
            if (state.mode === 'graph403' || state.mode === 'graph401') return json(route, {}, state.mode === 'graph401' ? 401 : 403);
            return json(route, { ...graph, labels: [url.searchParams.get('data_inicio') || graph.labels[0]] });
        }
        if (url.pathname.startsWith('/api/notas-entrada/sku/')) return json(route, []);
        if (url.pathname === '/api/vendas') return json(route, url.searchParams.has('incluir_ebazar')
            ? [sale, { ...sale, comprador: 'EBAZAR', numero: 'transferencia-ficticia' }] : [sale]);
        if (url.pathname === '/api/vendas/sync/progress') return json(route, { active: false, automacao: { jobs: [], lojas: [] } });
        if (url.pathname === '/api/vendas/limites') return json(route, { inicio: '2026-01-10', fim: '2026-10-20' });
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
async function settledMain(page) {
    // Filtering also schedules a save after 180 ms + an idle callback (up to 900 ms) + 60 ms.
    // Capture the baseline after those live writes, before closing the browser or restoring cache.
    await page.evaluate(() => Promise.allSettled([carregarVendasPromise, carregarGraficoPromise]));
    let previous, stableSince = performance.now();
    const deadline = performance.now() + 5000;
    while (performance.now() < deadline) {
        const snapshot = await savedMain(page);
        if (snapshot?.salvoEm !== previous) {
            previous = snapshot?.salvoEm;
            stableSince = performance.now();
        }
        if (snapshot && performance.now() - stableSince >= 1300) return snapshot;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw new Error('The live list cache did not finish its scheduled saves before restart');
}
async function release(status, predicate = () => true) {
    const selected = state.held.filter(predicate);
    state.held = state.held.filter(route => !selected.includes(route));
    await Promise.all(selected.map(route => json(route, status === 200 ? [{ store_id: 'store-a', nome: 'Loja A' }] : {}, status)));
}

async function savedGraph(page) {
    return page.evaluate(() => JKVendasCache.ler(prepararConsultaGraficoVendas().chaveCache, JKVendasCache.contexto()));
}
async function waitHeld(predicate) {
    for (let attempt = 0; attempt < 200; attempt++) {
        const route = state.held.find(predicate);
        if (route) return route;
        await new Promise(resolve => setTimeout(resolve, 10));
    }
    throw new Error('A requisição sintética esperada não iniciou.');
}
function takeHeld(route) { state.held = state.held.filter(candidate => candidate !== route); return route; }

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
        const maxPage = await open(context, maxUrl);
        await maxPage.waitForFunction(() => lojasDisponiveis.length > 0);
        await maxPage.evaluate(async () => {
            periodoGrafico = 'max'; compararAnoPassado = true; aplicarPreferenciaGraficoUI();
            await aplicarPeriodoGraficoMesesCompletos('max', { recarregar: false });
            await Promise.all([carregarVendas(), carregarGrafico()]);
        });
        await maxPage.waitForFunction(async () => !!await JKVendasCache.ler(prepararConsultaGraficoVendas().chaveCache, JKVendasCache.contexto()));
        const maxDates = await maxPage.evaluate(() => [getDataIniISO(), getDataFimISO()]);
        assert.ok(await savedMain(maxPage), 'The max-period list must finish saving before the browser restarts');
        assert.deepEqual(await maxPage.evaluate(() => {
            const pref = carregarPreferenciaPeriodoData(); return [pref?.inicio, pref?.fim];
        }), maxDates, 'The saved max range must match the current filters');
        const maxGraphAt = (await savedGraph(maxPage)).salvoEm;
        const limitsAt = await maxPage.evaluate(async () => (await JKVendasCache.ler(prepararConsultaLimitesVendas().chave, JKVendasCache.contexto())).salvoEm);
        await page.evaluate(async () => { compararAnoPassado = true; aplicarPreferenciaGraficoUI(); await carregarGrafico(); });
        await page.waitForFunction(async () => !!await JKVendasCache.ler(prepararConsultaGraficoVendas().chaveCache, JKVendasCache.contexto()));
        const graphAt = (await savedGraph(page)).salvoEm;
        assert.deepEqual(await maxPage.evaluate(() => {
            const pref = carregarPreferenciaPeriodoData(); return [pref?.inicio, pref?.fim];
        }), maxDates, 'Preparing a second cached graph must retain the saved max range');
        const savedAt = (await settledMain(page)).salvoEm;
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
        await page.waitForFunction(() => JKVendasLegacyBridge.getSnapshot().graph.data?.labels?.length === 1);
        assert.equal(state.calls.filter(call => call.path === '/api/vendas/grafico').length, 0,
            'The graph must restore while stores and all business APIs remain pending');
        assert.equal((await savedGraph(page)).salvoEm, graphAt);
        const maxRestarted = await open(context, maxUrl);
        await maxRestarted.evaluate(() => restauracaoInicialVendas);
        assert.deepEqual(await maxRestarted.evaluate(() => [getDataIniISO(), getDataFimISO()]), maxDates,
            'Opening without URL dates must restore the saved max range before the network');
        assert.ok(await savedMain(maxRestarted), 'The same max-period list cache key must be available after restart');
        await maxRestarted.locator('#tBody tr[data-sku="001"]').waitFor();
        await maxRestarted.waitForFunction(() => JKVendasLegacyBridge.getSnapshot().graph.comparison?.labels?.length === 1);
        assert.deepEqual(await maxRestarted.evaluate(() => [getDataIniISO(), getDataFimISO()]), maxDates);
        assert.equal(await maxRestarted.evaluate(() => periodoGrafico), 'max');
        assert.equal(state.calls.filter(call => call.path === '/api/vendas/grafico').length, 0);
        state.holdStores = false;
        await release(200, route => new URL(route.request().url()).pathname === '/api/lojas');
        await waitHeld(route => new URL(route.request().url()).pathname === '/api/vendas/limites');
        await release(503, route => new URL(route.request().url()).pathname === '/api/vendas/limites');
        await maxRestarted.waitForFunction(() => carregarGraficoPromise !== null);
        assert.equal(await maxRestarted.evaluate(() => periodoGrafico), 'max');
        assert.deepEqual(await maxRestarted.evaluate(() => [getDataIniISO(), getDataFimISO()]), maxDates);
        assert.equal(await maxRestarted.evaluate(async () => (await JKVendasCache.ler(prepararConsultaLimitesVendas().chave, JKVendasCache.contexto())).salvoEm), limitsAt);
        state.mode = 'error';
        await release(503);
        await page.waitForFunction(() => document.getElementById('cacheVendasStatus').textContent.includes('Atualização indisponível'));
        assert.equal(await page.locator('#tBody tr[data-sku="001"]').count(), 1);
        snapshot = await savedMain(page);
        assert.equal(snapshot.salvoEm, savedAt, 'A cached render or failed refresh must not renew the saved timestamp');
        assert.equal(snapshot.dados.dados[0].itensComposicao, 5);
        assert.equal((await savedGraph(page)).salvoEm, graphAt, 'Restoring or failing a chart refresh does not renew its timestamp');
        assert.equal((await savedGraph(maxRestarted)).salvoEm, maxGraphAt);
        await maxRestarted.close();

        // An older graph cannot repaint a period selected after its request started.
        state.mode = 'hold';
        const oldGraph = page.evaluate(() => { compararAnoPassado = false; setDataRangeISO('2026-09-01', '2026-09-20'); return carregarGrafico(); });
        const oldRoute = await waitHeld(route => new URL(route.request().url()).pathname === '/api/vendas/grafico'
            && new URL(route.request().url()).searchParams.get('data_inicio') === '2026-09-01');
        const currentGraph = page.evaluate(() => { setDataRangeISO('2026-10-01', '2026-10-20'); return carregarGrafico(); });
        const currentRoute = await waitHeld(route => new URL(route.request().url()).pathname === '/api/vendas/grafico'
            && new URL(route.request().url()).searchParams.get('data_inicio') === '2026-10-01');
        await json(takeHeld(currentRoute), { ...graph, labels: ['2026-10-18'], valores_vendas: [185] });
        await currentGraph;
        await json(takeHeld(oldRoute), { ...graph, labels: ['2026-09-18'], valores_vendas: [999] });
        await oldGraph;
        assert.deepEqual(await page.evaluate(() => JKVendasLegacyBridge.getSnapshot().graph.data.labels), ['2026-10-18']);

        // A delayed 401 from the previous user cannot clear the current session.
        const oldLimits = page.evaluate(() => obterLimitesComVendasServidor());
        const oldLimitsRoute = await waitHeld(route => new URL(route.request().url()).pathname === '/api/vendas/limites');
        await page.evaluate(() => {
            localStorage.setItem('user_data', JSON.stringify({client_id:'cache-test',username:'temporary-user'}));
            window.dispatchEvent(new Event('jk-auth-changed'));
        });
        state.mode = 'live';
        await page.evaluate(async () => { await carregarLojas(); await Promise.all([carregarVendas(), carregarGrafico()]); });
        // Table chunks render on animation frames after carregarVendas resolves.
        await page.locator('#tBody tr[data-sku="001"]').waitFor();
        await json(takeHeld(oldLimitsRoute), { detail: 'Token de autenticação inválido.' }, 401);
        await oldLimits;
        assert.equal(await page.locator('#tBody tr[data-sku="001"]').count(), 1);
        assert.equal(await page.evaluate(() => JKVendasLegacyBridge.getSnapshot().graph.data.labels.length), 1);
        await page.evaluate(() => {
            localStorage.setItem('user_data', JSON.stringify({client_id:'cache-test',username:'usuario-a'}));
            window.dispatchEvent(new Event('jk-auth-changed'));
        });
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
        await page.evaluate(() => carregarGrafico());
        await page.waitForFunction(async () => !!await JKVendasCache.ler(prepararConsultaGraficoVendas().chaveCache, JKVendasCache.contexto()));
        await page.evaluate(() => JKVendasCache.salvar(prepararConsultaGraficoVendas().chaveCache,
            { data: { labels: null }, dataComparativo: null }, JKVendasCache.contexto()));
        const graphReadsBefore = state.calls.filter(call => call.path === '/api/vendas/grafico').length;
        await page.reload({waitUntil:'domcontentloaded'});
        await page.waitForFunction(() => JKVendasLegacyBridge.getSnapshot().graph.data?.labels?.length === 1);
        assert.ok(state.calls.filter(call => call.path === '/api/vendas/grafico').length > graphReadsBefore,
            'A malformed saved graph still reads and renders the canonical response');
        const corruptSku = await open(context, skuUrl);
        await corruptSku.waitForFunction(() => document.querySelector('#tBody')?.textContent.includes('pedido-ficticio'));
        await corruptSku.evaluate(() => JKCachePersistente.salvar(prepararConsultaVendasSku().chave,
            {schema:1,dados:[null],devolucoes:[],transferencias:[]}, {contexto:JKCachePersistente.contexto(),tags:['vendas']}));
        const skuReadsBefore = state.calls.filter(call => call.path === '/api/vendas').length;
        await corruptSku.reload({waitUntil:'domcontentloaded'});
        await corruptSku.waitForFunction(() => document.querySelector('#tBody')?.textContent.includes('pedido-ficticio'));
        assert.ok(state.calls.filter(call => call.path === '/api/vendas').length > skuReadsBefore,
            'A malformed cached SKU must still fetch and render live data');
        state.mode = 'graph401';
        await page.evaluate(() => carregarGrafico());
        assert.equal(await page.locator('#tBody tr').count(), 0, 'A current graph 401 clears the visible saved session');
        assert.equal(await page.evaluate(() => JKVendasLegacyBridge.getSnapshot().graph.data), null);
        assert.ok(state.calls.every(call => call.method === 'GET'));
        assert.deepEqual(errors, []);
        console.log('PASS Vendas: browser restart restores list and graph before stores; max/comparison and saved bounds survive API failure; timestamps, stale graph/401, user isolation and malformed cache pass.');
    } finally {
        await context.close();
        const resolved = path.resolve(profile);
        assert.equal(path.dirname(resolved), temporaryRoot);
        assert.ok(path.basename(resolved).startsWith('jk-vendas-cache-profile-'));
        fs.rmSync(resolved, {recursive:true,force:true});
    }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
