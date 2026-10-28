'use strict';

// Production event handlers, rendering and IndexedDB, with synthetic HTTP only.
// JK_VENDAS_TEST_SOURCE_ROOT allows the same regression to run against its baseline.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(process.env.JK_VENDAS_TEST_SOURCE_ROOT || path.join(__dirname, '..'));
const temporaryRoot = path.resolve(process.env.JK_TEST_TMP || os.tmpdir());
const profile = fs.mkdtempSync(path.join(temporaryRoot, 'jk-vendas-filter-cache-'));
const read = relative => fs.readFileSync(path.join(root, relative), 'utf8');
const inline = source => '<script>' + source.replace(/<\/script/gi, '<\\/script') + '</script>';
const bootstrap = `(() => {
    const NativeDate = Date;
    window.Date = class extends NativeDate {
        constructor(...args) { super(...(args.length ? args : ['2026-10-28T12:00:00Z'])); }
    };
    if (!localStorage.getItem('user_data')) localStorage.setItem('user_data', JSON.stringify({client_id:'filter-fixture',username:'user-a'}));
    if (!localStorage.getItem('permissions')) localStorage.setItem('permissions', JSON.stringify({vendas:true}));
    if (!localStorage.getItem('access_token')) localStorage.setItem('access_token', 'synthetic-token');
    window.obterAuthHeaders = () => ({Authorization:'Bearer ' + localStorage.getItem('access_token')});
    window.verificarSessao = () => true;
    window.Chart = class { constructor(ctx, config) { this.canvas=ctx.canvas; this.config=config; this.data=config.data; this.options=config.options; } destroy(){} resize(){} update(){} };
})();`;
const assets = ['static/cache-persistente.js', 'static/vendas/cache-dados.js', 'static/vendas/runtime.js',
    'static/vendas/periodo-cache.js', 'static/vendas/sync.js', 'static/vendas/dados-render.js',
    'static/vendas/periodos-completos.js', 'static/vendas/grafico.js', 'static/vendas/visual-novo-bridge.js'];
const html = read('static/vendas.html').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '')
    .replace(/<link\b[^>]*>/gi, '').replace('</body>', inline(bootstrap)
        + assets.map(file => inline(read(file))).join('')
        + inline('function iaRestaurarOuCriarConversa(){return Promise.resolve();}')
        + inline(read('static/vendas/init.js')) + '</body>');
const mainUrl = 'http://localhost/vendas.html?data_inicio=2026-10-01&data_fim=2026-10-20&loja=Loja+A';
const base = { store: 'Loja A', unit: '__todos', start: '2026-10-01', end: '2026-10-20' };
const complete3m = { ...base, start: '2026-07-01', end: '2026-10-28' };
const complete1a = { ...base, start: '2025-10-01', end: '2026-10-28' };
const maxA = { ...base, start: '2024-04-01', end: '2026-09-30' };
const maxB = { ...base, store: 'Loja B', start: '2025-02-01', end: '2026-09-30' };
let state;
const errors = [], checks = [];
const scope = filter => [filter.store, filter.unit, filter.start, filter.end].join('|');
function filterFrom(url) {
    return { store: url.searchParams.get('loja') || '__todas', unit: url.searchParams.get('unidade_negocio') || '__todos',
        start: url.searchParams.get('data_inicio'), end: url.searchParams.get('data_fim') };
}
function graphFor(filter) {
    const value = [...scope(filter)].reduce((n, c) => (n * 31 + c.charCodeAt(0)) % 1000000, 7);
    return { labels: [filter.start], valores_vendas: [value], valores_devolucoes: [0],
        quantidades_vendas: [7], quantidades_devolucoes: [0], fixture_scope: scope(filter) };
}
async function json(route, body, status = 200) {
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {});
}
function answer(url) {
    if (url.pathname === '/api/lojas') return [{store_id:'store-a',nome:'Loja A'}, {store_id:'store-b',nome:'Loja B'}];
    if (url.pathname === '/api/unidades-negocios') return {unidades:[{nome:'Canal A'}],mapeamento:{}};
    if (url.pathname === '/api/vendas/limites') return {inicio: url.searchParams.get('loja') === 'Loja B' ? '2025-02-10' : '2024-04-10',fim:'2026-10-20'};
    if (url.pathname === '/api/vendas/grafico') return graphFor(filterFrom(url));
    if (url.pathname === '/api/vendas/resumo') {
        const filter = filterFrom(url), value = graphFor(filter).valores_vendas[0];
        return {rows:[{sku:'001',produto:'Produto sintético',quantidade:7,itens:7,pedidos:1,valor:value,
            loja_conta:filter.store,unidade_negocio:'Canal A'}],
            totals:{pedidos:1,itens:7,valor:value,devolucoes:0,itensDevolucao:0,valorDevolucao:0,itensLiquidos:7,valorLiquido:value},
            meta:{unidades:['Canal A']}};
    }
    if (url.pathname === '/api/vendas/sync/progress') return {active:false,automacao:{jobs:[],lojas:[]}};
    return {};
}
async function start(hold = false) {
    const context = await chromium.launchPersistentContext(profile, {headless:true,viewport:{width:1250,height:850}});
    state = {hold, held:[], calls:[]};
    await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.pathname === '/vendas.html') return route.fulfill({contentType:'text/html',body:html});
        if (!url.pathname.startsWith('/api/')) return route.abort();
        assert.equal(route.request().method(), 'GET', 'Filter selection cannot trigger synchronization');
        state.calls.push(url);
        if (state.hold && ['/api/vendas/resumo','/api/vendas/grafico','/api/vendas/limites'].includes(url.pathname)) {
            state.held.push({route,url}); return;
        }
        await json(route, answer(url));
    });
    context.on('page', page => page.on('pageerror', error => errors.push(String(error))));
    return context;
}
async function open(context) {
    const page = await context.newPage();
    await page.goto(mainUrl, {waitUntil:'domcontentloaded'});
    await page.waitForFunction(() => lojasDisponiveis.length === 2);
    return page;
}
async function expectGraph(page, filter, name, cached = false, verifyUpdating = true) {
    const began = performance.now();
    await page.waitForFunction(expected => JKVendasLegacyBridge.getSnapshot().graph.data?.fixture_scope === expected,
        scope(filter), {timeout:2500});
    const actual = await page.evaluate(() => JKVendasLegacyBridge.getSnapshot().graph.data);
    assert.deepEqual(actual.valores_vendas, graphFor(filter).valores_vendas, name);
    if (cached) {
        assert.match(await page.locator('#cacheVendasStatus').textContent(), /Gráfico: dados salvos/, name);
        if (verifyUpdating) {
            await page.waitForFunction(() => JKVendasLegacyBridge.getSnapshot().graphUpdating === true, null, {timeout:2500});
            const snapshot = await page.evaluate(() => JKVendasLegacyBridge.getSnapshot());
            assert.equal(snapshot.graphLoading, false, 'A restored graph cannot keep the blocking overlay: ' + name);
        }
    }
    checks.push({name, milliseconds: Math.round(performance.now() - began)});
}
async function confirmed(page, filter) {
    await expectGraph(page, filter, 'prime ' + scope(filter));
    await page.waitForFunction(async () => !carregarVendasPromise && !carregarGraficoPromise
        && Boolean(await JKVendasCache.ler(prepararConsultaGraficoVendas().chaveCache, JKVendasCache.contexto()))
        && Boolean(await JKVendasCache.ler(chaveCacheTelaVendas(), JKVendasCache.contexto())));
}
async function store(page, name) {
    // Do not await the action promise: the list is intentionally held during the regression.
    await page.locator('#lojaBotoes button').filter({hasText:name}).click();
}
async function shortcut(page, name) { await page.locator(`.grafico-btn[data-periodo="${name}"]`).click(); }
async function period(page, start, end) {
    await page.evaluate(({start,end}) => { JKVendasLegacyBridge.setPeriod(start,end); }, {start,end});
}
async function waitHeld(predicate) {
    for (let attempt = 0; attempt < 250; attempt++) {
        const selected = [...state.held].reverse().find(item => predicate(item.url));
        if (selected) return selected;
        await new Promise(resolve => setTimeout(resolve, 10));
    }
    throw new Error('Expected synthetic request did not start independently');
}
async function release(item, body, status = 200) {
    state.held = state.held.filter(candidate => candidate !== item);
    await json(item.route, body, status);
}
async function failHeld() {
    const held = state.held; state.held = [];
    await Promise.all(held.map(item => json(item.route, {}, 503)));
}
async function main() {
    let context = await start();
    try {
        let page = await open(context);
        await confirmed(page, base);
        await store(page, 'Loja B'); await confirmed(page, {...base,store:'Loja B'});
        await store(page, 'Loja A'); await confirmed(page, base);
        await page.selectOption('#unidadeNegocioSelect', 'Canal A'); await confirmed(page, {...base,unit:'Canal A'});
        await page.selectOption('#unidadeNegocioSelect', '__todos'); await confirmed(page, base);
        await shortcut(page, '3m'); await confirmed(page, complete3m);
        await shortcut(page, '1a'); await confirmed(page, complete1a);
        await shortcut(page, 'max'); await confirmed(page, maxA);
        await store(page, 'Loja B'); await confirmed(page, maxB);
        await store(page, 'Loja A'); await confirmed(page, maxA);
        await period(page, base.start, base.end); await confirmed(page, base);
        await page.check('#compareAnoPassado'); await confirmed(page, base);
        await page.waitForFunction(() => JKVendasLegacyBridge.getSnapshot().graph.comparison?.labels?.length === 1);
        await page.uncheck('#compareAnoPassado'); await confirmed(page, base);
        await context.close();

        context = await start(true); page = await open(context);
        await expectGraph(page, base, 'restart restores initial graph', true, false);
        await waitHeld(url => url.pathname === '/api/vendas/resumo');
        await waitHeld(url => url.pathname === '/api/vendas/grafico');
        await store(page, 'Loja B');
        await expectGraph(page, {...base,store:'Loja B'}, 'store B while summary and chart stay pending', true);
        await store(page, 'Loja A'); await expectGraph(page, base, 'return to store A', true);
        await page.selectOption('#unidadeNegocioSelect', 'Canal A');
        await expectGraph(page, {...base,unit:'Canal A'}, 'channel while summary stays pending', true);
        await page.selectOption('#unidadeNegocioSelect', '__todos'); await expectGraph(page, base, 'all channels', true);
        await shortcut(page, '3m'); await expectGraph(page, complete3m, 'short period while summary stays pending', true);
        await shortcut(page, '1a'); await expectGraph(page, complete1a, 'long period while summary stays pending', true);
        await shortcut(page, 'max'); await expectGraph(page, maxA, 'maximum period while bounds stay pending', true);
        await waitHeld(url => url.pathname === '/api/vendas/limites' && url.searchParams.get('loja') === 'Loja A');
        await store(page, 'Loja B'); await expectGraph(page, maxB, 'maximum in store B while bounds stay pending', true);
        await store(page, 'Loja A'); await expectGraph(page, maxA, 'maximum returns to store A', true);

        // Revalidation can discover older sales; only the current maximum selection may change.
        const maxRefresh = await waitHeld(url => url.pathname === '/api/vendas/limites' && url.searchParams.get('loja') === 'Loja A');
        await release(maxRefresh, {inicio:'2023-03-10',fim:'2026-10-20'});
        await page.waitForFunction(() => getDataIniISO() === '2023-03-01' && getDataFimISO() === '2026-09-30', null, {timeout:2500});
        await waitHeld(url => url.pathname === '/api/vendas/grafico' && url.searchParams.get('data_inicio') === '2023-03-01');
        checks.push({name:'changed maximum bounds trigger current graph without waiting for summary'});

        await period(page, base.start, base.end); await expectGraph(page, base, 'manual period restores', true);
        const remainingBounds = state.held.filter(item => item.url.pathname === '/api/vendas/limites');
        await Promise.all(remainingBounds.map(item => release(item, {inicio:'2020-01-01',fim:'2026-10-20'})));
        await page.waitForTimeout(150);
        assert.deepEqual(await page.evaluate(() => [getDataIniISO(),getDataFimISO()]), [base.start,base.end],
            'Late maximum responses cannot replace a manual period');
        checks.push({name:'late maximum bounds do not replace manual period'});
        await page.check('#compareAnoPassado'); await expectGraph(page, base, 'comparison restored', true);
        assert.equal(await page.evaluate(() => JKVendasLegacyBridge.getSnapshot().graph.comparison?.fixture_scope),
            scope({...base,start:'2025-10-01',end:'2025-10-20'}));
        await page.uncheck('#compareAnoPassado');

        // A combination never visited has no snapshot, but its chart request must still start now.
        await period(page, '2022-01-01', '2022-02-28');
        const cold = await waitHeld(url => url.pathname === '/api/vendas/grafico' && url.searchParams.get('data_inicio') === '2022-01-01');
        assert.ok(state.held.some(item => item.url.pathname === '/api/vendas/resumo' && item.url.searchParams.get('data_inicio') === '2022-01-01'));
        await release(cold, graphFor({...base,start:'2022-01-01',end:'2022-02-28'}));
        await expectGraph(page, {...base,start:'2022-01-01',end:'2022-02-28'}, 'cold chart completes before summary');

        await failHeld();
        await page.evaluate(() => {
            localStorage.setItem('user_data', JSON.stringify({client_id:'filter-fixture',username:'user-b'}));
            window.dispatchEvent(new Event('jk-auth-changed'));
        });
        await page.goto(mainUrl, {waitUntil:'domcontentloaded'});
        await page.waitForFunction(() => lojasDisponiveis.length === 2);
        await waitHeld(url => url.pathname === '/api/vendas/grafico');
        assert.equal(await page.evaluate(() => JKVendasLegacyBridge.getSnapshot().graph.data), null,
            'Another user cannot reuse the persisted graph');
        assert.deepEqual(errors, []);
        console.log('PASS Vendas filter cache: ' + JSON.stringify(checks));
    } finally {
        await context.close();
        const resolved = path.resolve(profile);
        assert.equal(path.dirname(resolved), temporaryRoot);
        assert.ok(path.basename(resolved).startsWith('jk-vendas-filter-cache-'));
        fs.rmSync(resolved, {recursive:true,force:true});
    }
}
main().catch(error => {console.error(error);process.exitCode=1;});
