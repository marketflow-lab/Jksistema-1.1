'use strict';

// Real Vendas controls, read lifecycle and layout migration; only HTTP and Chart are synthetic.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(process.env.JK_VENDAS_TEST_SOURCE_ROOT || path.join(__dirname, '..'));
const read = relative => fs.readFileSync(path.join(root, relative), 'utf8');
const inline = source => '<script>' + source.replace(/<\/script/gi, '<\\/script') + '</script>';
const loaderSelector = '#graficoVendasLoading';
const bootstrap = `(() => {
    localStorage.setItem('user_data', JSON.stringify({client_id:'loader-fixture',username:'synthetic-user'}));
    localStorage.setItem('permissions', JSON.stringify({vendas:true}));
    localStorage.setItem('access_token', 'synthetic-token');
    window.obterAuthHeaders = () => ({Authorization:'Bearer synthetic-token'});
    window.verificarSessao = () => true;
    window.Chart = class {
        constructor(ctx, config) { this.ctx=ctx; this.canvas=ctx.canvas; this.config=config; this.data=config.data; this.options=config.options; }
        destroy(){} resize(){} update(){}
    };
})();`;
const assets = ['static/cache-persistente.js', 'static/vendas/cache-dados.js', 'static/vendas/runtime.js',
    'static/vendas/periodo-cache.js', 'static/vendas/sync.js', 'static/vendas/dados-render.js',
    'static/vendas/periodos-completos.js', 'static/vendas/grafico.js', 'static/vendas/visual-novo-bridge.js',
    'static/vendas/grafico-loading.js',
    'static/vendas/visual-novo.js'];
function fixtureHtml() {
    return read('static/vendas.html').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '')
        .replace('</body>', inline(bootstrap) + assets.map(file => inline(read(file))).join('')
            + inline('function iaRestaurarOuCriarConversa(){return Promise.resolve();}')
            + inline(read('static/vendas/init.js')) + '</body>');
}
const mainUrl = 'http://localhost/vendas.html?data_inicio=2026-10-01&data_fim=2026-10-20&loja=Loja+A';
const graphFor = url => ({
    labels:[url.searchParams.get('data_inicio') || '2026-10-01'],
    valores_vendas:[url.searchParams.get('loja') === 'Loja B' ? 45 : 80], valores_devolucoes:[0],
    quantidades_vendas:[7], quantidades_devolucoes:[0], fixture_scope:url.search,
});
async function json(route, body, status=200) {
    await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)}).catch(() => {});
}
function answer(url) {
    if(url.pathname === '/api/lojas') return [{store_id:'store-a',nome:'Loja A'},{store_id:'store-b',nome:'Loja B'}];
    if(url.pathname === '/api/unidades-negocios') return {unidades:[{nome:'Canal A'}],mapeamento:{}};
    if(url.pathname === '/api/vendas/limites') return {inicio:'2024-04-10',fim:'2026-10-20'};
    if(url.pathname === '/api/vendas/grafico') return graphFor(url);
    if(url.pathname === '/api/vendas/resumo') return {
        rows:[{sku:'001',produto:'Produto sintético',quantidade:7,itens:7,pedidos:1,valor:80,
            loja_conta:url.searchParams.get('loja') || '__todas',unidade_negocio:'Canal A'}],
        totals:{pedidos:1,itens:7,valor:80,devolucoes:0,itensDevolucao:0,valorDevolucao:0,itensLiquidos:7,valorLiquido:80},
        meta:{unidades:['Canal A']},
    };
    if(url.pathname === '/api/vendas/sync/progress') return {active:false,automacao:{jobs:[],lojas:[]}};
    return {};
}
async function createCase(browser, layout) {
    const context = await browser.newContext({viewport:{width:1280,height:900}});
    const state = {hold:false,pending:[],errors:[]};
    const html = fixtureHtml();
    await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        if(url.pathname === '/vendas.html') return route.fulfill({contentType:'text/html',body:html});
        if(url.pathname.startsWith('/api/')) {
            assert.equal(route.request().method(), 'GET', 'Changing filters cannot synchronize sales');
            if(state.hold && url.pathname === '/api/vendas/grafico') {
                state.pending.push({route,url}); return;
            }
            return json(route,answer(url));
        }
        if(url.origin === 'http://localhost' && url.pathname.startsWith('/vendas/') && url.pathname.endsWith('.css')) {
            const file = path.join(root,'static',decodeURIComponent(url.pathname));
            if(fs.existsSync(file)) return route.fulfill({contentType:'text/css',body:fs.readFileSync(file)});
        }
        if(url.origin === 'http://localhost' && decodeURIComponent(url.pathname) === '/img/Logo Trend2.png') {
            return route.fulfill({contentType:'image/png',body:fs.readFileSync(path.join(root,'img/Logo Trend2.png'))});
        }
        return route.abort();
    });
    const page = await context.newPage();
    await page.clock.install({time:new Date('2026-10-28T12:00:00Z')});
    page.on('pageerror', error => state.errors.push(error.message));
    await page.goto(mainUrl,{waitUntil:'domcontentloaded'});
    await page.waitForFunction(() => lojasDisponiveis.length === 2 && !carregarGraficoPromise
        && JKVendasLegacyBridge.getSnapshot().graph.data != null);
    await page.waitForFunction(async () => Boolean(await JKVendasCache.ler(prepararConsultaGraficoVendas().chaveCache, JKVendasCache.contexto())));
    if(layout === 'novo') {
        await page.locator('#btnVersaoNovaVendas').click();
        await page.waitForFunction(() => JKVendasNova.isActive());
    }
    return {context,page,state,layout};
}
async function held(test,predicate) {
    const deadline = performance.now()+3000;
    while(performance.now()<deadline) {
        const item = [...test.state.pending].reverse().find(candidate => predicate(candidate.url));
        if(item) return item;
        await new Promise(resolve => setTimeout(resolve,10));
    }
    const state = await test.page.evaluate(() => ({filters:JKVendasLegacyBridge.getSnapshot().filters,
        chartOptions:JKVendasLegacyBridge.getSnapshot().chartOptions,pending:Boolean(carregarGraficoPromise)}));
    throw new Error('Synthetic chart request was not started: ' + JSON.stringify({state,errors:test.state.errors}));
}
async function release(test,item,status=200) {
    test.state.pending = test.state.pending.filter(candidate => candidate !== item);
    await json(item.route,status === 200 ? graphFor(item.url) : {},status);
}
async function chooseStore(test,name) {
    if(test.layout === 'novo') await test.page.selectOption('#vnStore',name);
    else await test.page.locator('#lojaBotoes button').filter({hasText:name}).click();
}
async function choosePeriod(test,period) {
    await test.page.locator(test.layout === 'novo' ? `[data-vn-period="${period}"]` : `.grafico-btn[data-periodo="${period}"]`).click();
}
async function loading(test) {
    const loader = test.page.locator(loaderSelector);
    await loader.waitFor({state:'visible'});
    const state = await loader.evaluate(element => {
        const wrapper = document.getElementById('graficoVendas').closest('.grafico-wrapper');
        const image = element.querySelector('img');
        return {sameWrapper:wrapper.contains(element),busy:wrapper.getAttribute('aria-busy'),
            imageLoaded:Boolean(image?.complete && image.naturalWidth),text:element.textContent,
            inNewSlot:Boolean(element.closest('#vnChartSlot'))};
    });
    assert.equal(state.sameWrapper,true,'Both layouts must keep the logo with the shared graph');
    assert.equal(state.busy,'true','The graph exposes its loading state to assistive technology');
    assert.equal(state.imageLoaded,true,'The official local logo must be available');
    assert.match(state.text,/Carregando vendas/);
    assert.equal(state.inNewSlot,test.layout === 'novo');
}
async function finished(test) {
    await test.page.locator(loaderSelector).waitFor({state:'hidden'});
    assert.notEqual(await test.page.locator('#graficoVendas').evaluate(canvas => canvas.closest('.grafico-wrapper').getAttribute('aria-busy')),'true');
}
async function runCase(browser,layout) {
    const test = await createCase(browser,layout);
    try {
        test.state.hold=true;
        await chooseStore(test,'Loja B');
        const store = await held(test,url=>url.searchParams.get('loja') === 'Loja B');
        await loading(test);
        await test.page.setViewportSize({width:390,height:900});
        const bounds = await test.page.locator(loaderSelector).evaluate(element => {
            const card=element.querySelector('.vendas-grafico-loading-card').getBoundingClientRect();
            const wrapper=element.parentElement.getBoundingClientRect();
            return {fits:card.left >= wrapper.left && card.right <= wrapper.right
                && card.top >= wrapper.top && card.bottom <= wrapper.bottom};
        });
        assert.equal(bounds.fits,true,'The translucent panel must fit its graph on a narrow screen');
        await test.page.setViewportSize({width:1280,height:900});
        await release(test,store);
        await finished(test);

        // Revisited filters restore their graph while the translucent update indicator remains truthful.
        await chooseStore(test,'Loja A');
        const cached = await held(test,url=>url.searchParams.get('loja') === 'Loja A');
        await loading(test);
        await test.page.waitForFunction(() => JKVendasLegacyBridge.getSnapshot().graphUpdating === true);
        await release(test,cached);
        await finished(test);

        await choosePeriod(test,'6m');
        const previous = await held(test,url=>url.searchParams.get('periodo') === '6m');
        await loading(test);
        await choosePeriod(test,'1a');
        const current = await held(test,url=>url.searchParams.get('periodo') === '1a');
        await loading(test);
        await release(test,previous);
        await test.page.evaluate(() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
        await loading(test);
        await release(test,current);
        await finished(test);
        assert.equal(await test.page.evaluate(() => JKVendasLegacyBridge.getSnapshot().graph.data.fixture_scope),current.url.search,
            'A late previous period must not replace the current graph');

        await test.page.evaluate(() => {
            localStorage.setItem('access_token','replacement-token');
            window.dispatchEvent(new StorageEvent('storage',{key:'access_token',oldValue:'synthetic-token',newValue:'replacement-token'}));
        });
        await finished(test);
        await choosePeriod(test,'2a');
        const resumed = await held(test,url=>url.searchParams.get('periodo') === '2a');
        await loading(test);
        await release(test,resumed);
        await finished(test);

        await choosePeriod(test,'6m');
        const failed = await held(test,url=>url.searchParams.get('periodo') === '6m');
        await loading(test);
        await release(test,failed,503);
        await finished(test);
        await test.page.waitForFunction(() => Boolean(JKVendasLegacyBridge.getSnapshot().graphError));

        await choosePeriod(test,'3m');
        await held(test,url=>url.searchParams.get('periodo') === '3m');
        await loading(test);
        await test.page.clock.fastForward(120001);
        await finished(test);
        await test.page.waitForFunction(() => /tempo de resposta/i.test(JKVendasLegacyBridge.getSnapshot().graphError));

        await choosePeriod(test,'max');
        const denied = await held(test,url=>url.searchParams.get('periodo') === 'max');
        await loading(test);
        await release(test,denied,401);
        await finished(test);
        await test.page.waitForFunction(() => /Acesso negado|sessão expirada/i.test(document.getElementById('status').textContent));
        assert.deepEqual(test.state.errors,[]);
        console.log('PASS Vendas logo loading: ' + layout + ' (store, periods, cache, stale response, success, token change, HTTP error, timeout, denied access)');
    } finally {
        await test.context.close();
    }
}
(async () => {
    const browser = await chromium.launch({headless:true});
    try { for(const layout of ['antigo','novo']) await runCase(browser,layout); }
    finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
