'use strict';

// Synthetic localhost responses and a headless browser only; no running application is controlled.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const read = relative => fs.readFileSync(path.join(root, relative), 'utf8');
const inline = source => '<script>' + source.replace(/<\/script/gi, '<\\/script') + '</script>';
const identity = { client_id: 'saved-summary-test', username: 'usuario-a' };
const bootstrap = `(() => {
    if (!localStorage.getItem('user_data')) localStorage.setItem('user_data', JSON.stringify(${JSON.stringify(identity)}));
    if (!localStorage.getItem('permissions')) localStorage.setItem('permissions', JSON.stringify({vendas:true}));
    if (!localStorage.getItem('access_token')) localStorage.setItem('access_token', 'fixture-token');
    window.obterAuthHeaders = () => ({Authorization:'Bearer ' + localStorage.getItem('access_token')});
    window.verificarSessao = () => true;
    window.Chart = class { destroy(){} resize(){} update(){} };
    const originalTimeout = window.setTimeout.bind(window);
    window.setTimeout = (fn, delay, ...args) => originalTimeout(fn,
        delay >= 250 && delay <= 10000 && !/controller\\.abort|TimeoutError/.test(String(fn)) ? 12 : delay, ...args);
})();`;
const scripts = ['static/cache-persistente.js', 'static/vendas/cache-dados.js', 'static/vendas/runtime.js',
    'static/vendas/periodo-cache.js', 'static/vendas/sync.js', 'static/vendas/dados-render.js',
    'static/vendas/periodos-completos.js', 'static/vendas/grafico.js', 'static/vendas/visual-novo-bridge.js'];
const html = read('static/vendas.html').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '')
    .replace(/<link\b[^>]*>/gi, '').replace('</body>', inline(bootstrap)
        + scripts.map(file => inline(read(file))).join('') + '</body>');
const url = 'http://localhost/vendas.html';
const warnings = [{code:'legacy_catalog_unverified',store_id:'store-a',sku:''},
    {code:'missing_component',store_id:'store-a',sku:'001'}];
const savedAt = Date.UTC(2026, 9, 29, 10) / 1000;
function payload(value = 70, estado = 'salvo', extra = {}) {
    return { success: true, rows: [{sku:'001',produto:'Produto salvo',loja_conta:'Loja A',pedidos:2,
        itens:7,quantidade:7,valor:value,itensDiretos:2,itensComposicao:5,itensComponentes:7}],
        totals: {pedidos:2,itens:7,valor:value,devolucoes:0,itensDevolucao:0,valorDevolucao:0,itensLiquidos:7,valorLiquido:value},
        meta: {composicao:{incomplete:true,read_complete:true,warnings},
            leitura:{estado,possui_salvo:true,atualizando:estado === 'salvo' || estado === 'processando',
                revisao:estado === 'atualizado' ? 'revision-new' : 'revision-saved',salvo_em:savedAt,...extra}} };
}
function processing() {
    return {success:true,rows:[],totals:{},meta:{leitura:{estado:'processando',possui_salvo:false,
        atualizando:true,revisao:'',salvo_em:null}}};
}
async function respond(route, body, status = 200) {
    await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)}).catch(() => {});
}
async function prepare(page, {inicio='2026-10-01',fim='2026-10-20',loja='Loja A'} = {}) {
    await page.evaluate(options => {
        clientId = obterClientId(); lojaSelecionada = options.loja;
        lojasDisponiveis = [{store_id:'store-a',nome:'Loja A'},{store_id:'store-b',nome:'Loja B'}];
        unidadeNegocioSelect.innerHTML = '<option value="__todos">Todas</option>';
        setDataRangeISO(options.inicio,options.fim);
        window.__summaryRequest = carregarVendas();
    }, {inicio,fim,loja});
}
async function settled(page) {
    await page.evaluate(async () => { await window.__summaryRequest; });
}
async function visible(page, value = 70) {
    await page.locator('#tBody tr[data-sku="001"]').waitFor();
    await page.waitForFunction(expected => resumoTotaisVendas?.valor === expected, value);
}
async function assertSavedVisible(page, value = 70) {
    await visible(page,value);
    assert.equal(await page.locator('#composicaoAviso').isHidden(), false, 'Saved composition warnings remain visible');
    assert.deepEqual(await page.evaluate(() => vendasResumoMeta.composicao.warnings), warnings);
    assert.equal(await page.evaluate(() => JKVendasLegacyBridge.getSnapshot().totals?.valor), value);
}
async function waitHeld(state, predicate = () => true) {
    const deadline = performance.now() + 5000;
    while (performance.now() < deadline) {
        const found = state.held.find(item => predicate(item));
        if (found) return found;
        await new Promise(resolve => setTimeout(resolve,10));
    }
    throw new Error('The expected background read did not start');
}
async function run(browser, label, scenario) {
    const context = await browser.newContext({viewport:{width:1250,height:850}});
    const errors = [];
    const state = {calls:[],held:[],saved:(_route,_query) => payload(),stale:(_route,_query) => payload(100,'atualizado')};
    context.on('page', page => page.on('pageerror', error => errors.push(String(error))));
    await context.route('**/*', async route => {
        const request = route.request(), parsed = new URL(request.url());
        if (parsed.pathname === '/vendas.html') return route.fulfill({contentType:'text/html',body:html});
        if (!parsed.pathname.startsWith('/api/')) return route.abort();
        assert.equal(request.method(),'GET','Loading saved summaries must not start a sales synchronization');
        const call = {path:parsed.pathname,query:parsed.searchParams,route}; state.calls.push(call);
        if (parsed.pathname === '/api/vendas/resumo') {
            const handler = parsed.searchParams.get('somente_salvos') === 'true' ? state.saved : state.stale;
            assert.ok(parsed.searchParams.get('somente_salvos') === 'true' || parsed.searchParams.get('cache_mode') === 'stale',
                'The screen must use saved-only followed by background reads, never the blocking summary');
            const result = await handler(route,parsed.searchParams);
            if (result === 'hold') { state.held.push(call); return; }
            return respond(route,result?.body || result,result?.status || 200);
        }
        if (parsed.pathname === '/api/lojas') return respond(route,[{store_id:'store-a',nome:'Loja A'}]);
        if (parsed.pathname === '/api/unidades-negocios') return respond(route,{unidades:[],mapeamento:{}});
        if (parsed.pathname === '/api/vendas/sync/progress') return respond(route,{active:false,automacao:{jobs:[],lojas:[]}});
        return respond(route,{});
    });
    try {
        const page = await context.newPage();
        await page.goto(url,{waitUntil:'domcontentloaded'});
        await scenario(page,state);
        assert.deepEqual(errors,[],label + ': production scripts must not throw');
        console.log('PASS ' + label);
    } finally { await context.close(); }
}
async function main() {
    const browser = await chromium.launch({headless:true});
    try {
        await run(browser,'saved rows, totals and warnings render before the background response',async(page,state) => {
            state.stale = () => 'hold';
            await prepare(page); await assertSavedVisible(page);
            const background = await waitHeld(state);
            assert.equal(state.calls[0].query.get('somente_salvos'),'true');
            assert.match(await page.locator('#cacheVendasStatus').textContent(),/dados salvos/i);
            assert.match(await page.locator('#cacheVendasStatus').textContent(),/2026/, 'Unix-second timestamps must display the saved year');
            await respond(background.route,payload(100,'atualizado')); await settled(page);
            await assertSavedVisible(page,100);
        });
        await run(browser,'processing responses do not erase a saved summary',async(page,state) => {
            let polls=0; state.stale=() => ++polls < 3 ? {body:processing(),status:202} : 'hold';
            await prepare(page); await waitHeld(state); await assertSavedVisible(page);
            await respond(state.held[0].route,payload(100,'atualizado')); await settled(page); await visible(page,100);
        });
        await run(browser,'HTTP failure retains the saved table, totals and warnings',async(page,state) => {
            state.stale=() => ({body:{},status:503});
            await prepare(page); await settled(page); await assertSavedVisible(page);
            assert.match(await page.locator('#cacheVendasStatus').textContent(),/indisponível|mantid/i);
        });
        await run(browser,'a network timeout retains the saved summary',async(page) => {
            await page.evaluate(() => {
                const original=fetchComTimeout;
                fetchComTimeout=(target,...args) => String(target).includes('cache_mode=stale')
                    ? Promise.reject(new DOMException('Fixture timeout','TimeoutError')) : original(target,...args);
            });
            await prepare(page); await settled(page); await assertSavedVisible(page);
        });
        await run(browser,'an explicit background error cannot replace the saved summary',async(page,state) => {
            state.stale=() => payload(70,'erro',{atualizando:false});
            await prepare(page); await settled(page); await assertSavedVisible(page);
            assert.match(await page.locator('#cacheVendasStatus').textContent(),/indisponível|mantid/i);
        });
        await run(browser,'a first visit without a snapshot polls until the summary is ready',async(page,state) => {
            state.saved=() => ({body:processing(),status:202});
            let polls=0; state.stale=() => ++polls < 3 ? {body:processing(),status:202} : payload(100,'atualizado');
            await prepare(page); await settled(page); await assertSavedVisible(page,100);
            assert.equal(polls,3);
        });
        await run(browser,'changing filters discards a late response for the previous period',async(page,state) => {
            state.stale=(_route,query) => query.get('data_inicio') === '2026-10-01' ? 'hold' : payload(222,'atualizado');
            state.saved=(_route,query) => payload(query.get('data_inicio') === '2026-10-01' ? 70 : 200);
            await prepare(page); const old=await waitHeld(state); await visible(page);
            await prepare(page,{inicio:'2026-09-01',fim:'2026-09-20'}); await settled(page); await visible(page,222);
            await respond(old.route,payload(999,'atualizado'));
            await page.waitForTimeout(100); await assertSavedVisible(page,222);
            assert.equal(await page.evaluate(() => getDataIniISO()),'2026-09-01');
        });
        for (const code of [401,403]) {
            await run(browser,`a current ${code} clears already displayed saved data`,async(page,state) => {
                state.stale=() => 'hold'; await prepare(page); await assertSavedVisible(page);
                const held=await waitHeld(state); await respond(held.route,{detail:'Fixture denied'},code); await settled(page);
                assert.equal(await page.locator('#tBody tr[data-sku]').count(),0);
                assert.equal(await page.evaluate(() => resumoTotaisVendas),null);
            });
        }
        await run(browser,'a delayed denial for an old session cannot clear new session data',async(page,state) => {
            state.stale=() => 'hold'; await prepare(page); const old=await waitHeld(state); await visible(page);
            await page.evaluate(() => {
                localStorage.setItem('user_data',JSON.stringify({client_id:'saved-summary-test',username:'usuario-b'}));
                localStorage.setItem('access_token','second-fixture-token');
                window.dispatchEvent(new Event('jk-auth-changed'));
            });
            state.saved=() => payload(200); state.stale=() => payload(222,'atualizado');
            await prepare(page); await settled(page); await visible(page,222);
            await respond(old.route,{detail:'Old fixture denied'},401);
            await page.waitForTimeout(100); await assertSavedVisible(page,222);
        });
        await run(browser,'pagehide cancels polling and ignores a late update',async(page,state) => {
            state.stale=() => 'hold'; await prepare(page); const held=await waitHeld(state); await visible(page);
            await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
            const before=state.calls.length;
            await respond(held.route,payload(999,'atualizado')); await page.waitForTimeout(150);
            assert.equal(await page.evaluate(() => resumoTotaisVendas?.valor),70);
            assert.equal(state.calls.length,before);
        });
        await run(browser,'background polling is bounded and leaves saved data visible',async(page,state) => {
            let polls=0; state.stale=() => { polls++; return {body:processing(),status:202}; };
            await prepare(page); await settled(page); await assertSavedVisible(page);
            assert.ok(polls >= 2 && polls <= 32,`Expected bounded polling, received ${polls}`);
            const before=polls; await page.waitForTimeout(150); assert.equal(polls,before);
        });
        await run(browser,'IndexedDB restores rows and warnings before the saved-only server read finishes',async(page,state) => {
            await prepare(page); await settled(page); await visible(page,100);
            await page.evaluate(() => salvarCacheTelaVendas());
            const snapshot=await page.evaluate(() => JKCachePersistente.ler(chaveCacheTelaVendas(),{contexto:JKCachePersistente.contexto()}));
            assert.ok(snapshot,'The ready summary must have a durable browser snapshot');
            assert.deepEqual(snapshot.dados.vendasResumoMeta.composicao.warnings,warnings);
            state.saved=() => 'hold'; state.stale=() => 'hold';
            await page.reload({waitUntil:'domcontentloaded'}); await prepare(page); await assertSavedVisible(page,100);
            const savedRead=await waitHeld(state,item=>item.query.get('somente_salvos') === 'true');
            assert.match(await page.locator('#cacheVendasStatus').textContent(),/dados salvos/i);
            await respond(savedRead.route,payload(100));
            const background=await waitHeld(state,item=>item.query.get('cache_mode') === 'stale');
            await respond(background.route,payload(222,'atualizado')); await settled(page); await assertSavedVisible(page,222);
        });
        console.log('PASS Vendas resumo salvo: 13 protocol, preservation, isolation and cancellation scenarios.');
    } finally { await browser.close(); }
}
main().catch(error => {console.error(error);process.exitCode=1;});
