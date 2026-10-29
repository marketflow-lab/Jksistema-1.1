'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');

function gate() {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    return { promise, resolve };
}

function fixture(id = 'A', complete = false) {
    return {
        id, nome_lista: 'Lista ' + id, store_id: 'store-' + id, dolar_hoje: 5,
        itens: [
            { SKU: '001', Quantidade: 2, 'Valor unidade': 10, 'Valor total': 20,
                'M3 individual': 0.1, Produto: 'Produto da lista ' + id,
                NCM: '00000001', Link: 'https://produto.test/' + id,
                II: 10, IPI: 5, PIS: 2, COFINS: 3, 'ST Minas': 'Não', 'ST Paraíba': 'Não',
                analise_concorrentes: { margens: {} } },
            { SKU: '002', Quantidade: 3, 'Valor unidade': 5, 'Valor total': 15,
                ...(complete ? { 'M3 individual': 0.2 } : {}) },
        ],
    };
}

async function waitUntil(predicate) {
    for (let attempt = 0; attempt < 200; attempt++) {
        if (predicate()) return;
        await new Promise(resolve => setTimeout(resolve, 10));
    }
    throw new Error('Condição sintética não atingida.');
}

async function environment(browser, options = {}) {
    const context = await browser.newContext();
    const page = await context.newPage();
    const state = {
        cadastro: gate(), mapa: gate(), impostos: gate(), concorrentes: gate(), detail: gate(), preco: gate(), fx: gate(),
        calls: { cadastro: 0, mapa: 0, impostos: 0, concorrentes: 0, global: 0 }, patches: [], errors: [],
        savedReads: 0, queuedReads: 0, blockingReads: 0, ready: false, savedPending: false,
    };
    page.on('pageerror', error => state.errors.push(error.message));
    if (options.fastTimeout) await page.addInitScript(() => {
        const timeout = window.setTimeout.bind(window);
        window.setTimeout = (callback, delay, ...args) => timeout(callback, delay === 5000 ? 50 : delay, ...args);
    });
    if (options.fastPolling) await page.addInitScript(() => {
        const timeout = window.setTimeout.bind(window);
        window.setTimeout = (callback, delay, ...args) => timeout(callback, [1000, 2000, 10000].includes(delay) ? 50 : delay, ...args);
    });
    if (options.cacheStub) await page.addInitScript(() => {
        const context = { tenant: 'synthetic', store: 'synthetic' };
        window.cacheWrites = [];
        window.cacheInvalidations = 0;
        window.JKCachePersistente = {
            contexto: () => context, vigente: () => true, dadosVigentes: () => true,
            ler: async () => null, invalidar: async () => { window.cacheInvalidations++; }, confirmarMutacao: () => context,
            salvar: async (key, data) => { window.cacheWrites.push({ key, data }); },
        };
    });
    if (options.oldPolling403) await page.addInitScript(() => {
        const fetch = window.fetch.bind(window);
        window.fetch = (url, options) => String(url).includes('/listas-pedidos/A?cache_mode=stale')
            ? new Promise(resolve => { window.releaseOldPolling = () => resolve(new Response(JSON.stringify({ detail: 'Acesso antigo negado.' }),
                { status: 403, headers: { 'Content-Type': 'application/json' } })); })
            : fetch(url, options);
    });
    if (options.holdPrice) await page.addInitScript(() => {
        let resolve;
        const promise = new Promise(done => { resolve = done; });
        window.releasePrice = () => resolve({ price: 999, original_price: 1000 });
        window.electronAPI = { getMlBrowserItemInfo: () => promise };
    });
    const json = (route, body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {});
    await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        const pathname = url.pathname;
        if (pathname === '/importacoes_sku.html') return route.fulfill({ status: 200, contentType: 'text/html',
            body: fs.readFileSync(path.join(root, 'static', 'importacoes_sku.html')) });
        if (pathname === '/auth.js') return route.fulfill({ status: 200, contentType: 'application/javascript',
            body: 'window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({Authorization:"Bearer synthetic"});' });
        if (pathname === '/api/cadastro/produtos' || pathname === '/api/cadastro/produto') {
            ++state.calls.global;
            return json(route, {}, 500);
        }
        const cadastro = pathname.match(/^\/api\/cadastro\/lojas\/(store-[AB])\/produtos\/(.+)$/);
        if (cadastro) {
            ++state.calls.cadastro;
            const store = cadastro[1];
            if (options.hold && store === 'store-A') await state.cadastro.promise;
            return json(route, { produto: { store_id: options.wrongStore ? 'store-X' : store,
                sku: cadastro[2], nome: 'Cadastro ' + store, ncm: '00000002', 'M3 individual': 0.1 } });
        }
        const mapa = pathname.match(/^\/api\/cadastro\/lojas\/(store-[AB])\/produtos$/);
        if (mapa) {
            ++state.calls.mapa;
            if (options.hold && mapa[1] === 'store-A') await state.mapa.promise;
            return json(route, [
                { store_id: 'store-X', sku: '002', 'M3 individual': 999 },
                { store_id: mapa[1], sku: '002', 'M3 individual': 0.2 },
            ]);
        }
        if (pathname === '/api/impostos/produtos') {
            ++state.calls.impostos;
            if (options.hold && state.calls.impostos === 1) await state.impostos.promise;
            return json(route, { produtos: [] }, options.failFiscal ? 500 : 200);
        }
        if (pathname === '/api/medias-compras/concorrentes-links') {
            ++state.calls.concorrentes;
            if ((options.hold || options.holdConcorrentes) && state.calls.concorrentes === 1) await state.concorrentes.promise;
            return json(route, { concorrentes: { concorrente_1: options.holdPrice && state.calls.concorrentes === 1 ? 'https://produto.mercadolivre.com.br/MLB-1234567890' : '' },
                concorrentes_valores: { concorrente_1: options.holdPrice && state.calls.concorrentes === 1 ? '' : 'R$ 200,00' } });
        }
        const lista = pathname.match(/^\/api\/medias-compras\/listas-pedidos\/([AB])$/);
        if (lista) {
            const saved = url.searchParams.get('somente_salvos') === 'true';
            const polling = url.searchParams.get('cache_mode') === 'stale';
            if (saved) ++state.savedReads;
            else if (polling) ++state.queuedReads;
            else ++state.blockingReads;
            if (options.denySaved && saved) return json(route, { detail: 'Acesso negado.' }, 403);
            if (options.denyPolling && polling) return json(route, { detail: 'Acesso negado.' }, 403);
            if (saved && state.savedPending) return json(route, { success: true, dados: null,
                _cache: { estado: 'waiting', atualizando: true, retry_after_ms: 1000, completo: false } }, 202);
            if (options.queuedDetail && lista[1] === 'A' && !saved && !state.ready) {
                if (!polling) { await state.detail.promise; return json(route, { detail: 'Consulta bloqueante expirou.' }, 504); }
                const metadata = { estado: 'waiting', atualizando: true, stale: true, retry_after_ms: 1000, completo: false };
                if (options.staleResult) return json(route, { lista: { ...fixture('A', true), nome_lista: 'Resultado anterior' },
                    _cache: { ...metadata, estado: 'refreshing', completo: true } });
                return json(route, { success: true, dados: null, _cache: metadata }, 202);
            }
            if (options.holdDetail && lista[1] === 'A') {
                await state.detail.promise;
                return json(route, { detail: 'Falha antiga' }, 500);
            }
            const data = fixture(lista[1], options.complete || lista[1] === 'B');
            if (options.queuedDetail && !saved && state.ready) data.itens[0].Quantidade = 4;
            if (options.zeroQuantity) data.itens.push({ SKU: 'ZERO', Quantidade: 0 });
            if (options.noDollar) delete data.dolar_hoje;
            if (options.missingValues) { delete data.itens[0]['Valor unidade']; delete data.itens[0]['Valor total']; }
            return json(route, { lista: data });
        }
        if (pathname.endsWith('/analise-concorrentes')) {
            assert.strictEqual(route.request().method(), 'PATCH');
            state.patches.push({ pathname, payload: route.request().postDataJSON() });
            return json(route, { analise_concorrentes: { margens: {} } });
        }
        if (url.hostname === 'economia.awesomeapi.com.br') {
            if (options.holdFx) await state.fx.promise;
            return json(route, { USDBRL: { bid: '5', timestamp: '1' } });
        }
        if (pathname.startsWith('/api/')) return json(route, {});
        return route.fulfill({ status: 200, contentType: 'application/javascript', body: '' });
    });
    return { context, page, state };
}

function releaseComplements(state) {
    state.cadastro.resolve(); state.mapa.resolve(); state.impostos.resolve(); state.concorrentes.resolve();
}

(async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const queued = await environment(browser, { queuedDetail: true, fastPolling: true, complete: true, cacheStub: true });
        await queued.page.goto('http://jk.test/importacoes_sku.html?lista_id=A&sku=001');
        await queued.page.waitForFunction(() => document.getElementById('vSku').textContent === '001', null, { timeout: 1500 });
        await waitUntil(() => queued.state.queuedReads > 0);
        assert.strictEqual(await queued.page.locator('#vQtd').textContent(), '2');
        assert.match(await queued.page.locator('#cacheSkuStatus').textContent(), /salvos|Atualizando/);
        assert.strictEqual(queued.state.savedReads, 1);
        assert.strictEqual(queued.state.blockingReads, 0, 'Abrir SKU não espera o recálculo em um GET bloqueante.');
        assert.strictEqual(queued.state.calls.cadastro, 0, 'Os complementos aguardam um resultado atual.');
        assert.strictEqual(queued.state.patches.length, 0);
        queued.state.savedPending = true;
        await queued.page.evaluate(() => mostrarDadosSalvosSku(contextoDetalheSku));
        assert.strictEqual(await queued.page.locator('#vQtd').textContent(), '2', 'Snapshot 202 não apaga dados já visíveis.');
        queued.state.savedPending = false;
        assert.strictEqual(await queued.page.evaluate(() => cacheWrites.some(entry => entry.data?.dados === null)), false,
            'Resposta 202 não substitui os dados salvos.');
        queued.state.ready = true;
        await waitUntil(() => queued.state.patches.length === 1);
        assert.strictEqual(await queued.page.locator('#vQtd').textContent(), '4');
        assert.strictEqual(queued.state.calls.cadastro, 1);
        assert.deepStrictEqual(queued.state.errors, []);
        await queued.context.close();

        const staleResult = await environment(browser, { queuedDetail: true, staleResult: true, fastPolling: true, complete: true, cacheStub: true });
        await staleResult.page.goto('http://jk.test/importacoes_sku.html?lista_id=A&sku=001');
        await waitUntil(() => staleResult.state.queuedReads > 0);
        assert.strictEqual(await staleResult.page.locator('#vQtd').textContent(), '2');
        assert.strictEqual(await staleResult.page.evaluate(() => cacheWrites.some(entry => entry.data?._cache?.stale)), false);
        staleResult.state.ready = true;
        await waitUntil(() => staleResult.state.patches.length === 1);
        assert.deepStrictEqual(staleResult.state.errors, []);
        await staleResult.context.close();

        const deniedSnapshot = await environment(browser, { denySaved: true, cacheStub: true });
        await deniedSnapshot.page.goto('http://jk.test/importacoes_sku.html?lista_id=A&sku=001');
        await deniedSnapshot.page.waitForFunction(() => document.querySelector('.layout').children.length === 0);
        assert.strictEqual(deniedSnapshot.state.queuedReads, 0);
        assert.strictEqual(deniedSnapshot.state.patches.length, 0);
        assert.deepStrictEqual(deniedSnapshot.state.errors, []);
        await deniedSnapshot.context.close();

        const deniedPolling = await environment(browser, { denyPolling: true, cacheStub: true });
        await deniedPolling.page.goto('http://jk.test/importacoes_sku.html?lista_id=A&sku=001');
        await deniedPolling.page.waitForFunction(() => document.querySelector('.layout').children.length === 0);
        assert.strictEqual(deniedPolling.state.savedReads, 1);
        assert.strictEqual(deniedPolling.state.queuedReads, 1);
        assert.strictEqual(deniedPolling.state.patches.length, 0);
        assert.deepStrictEqual(deniedPolling.state.errors, []);
        await deniedPolling.context.close();

        const oldDeniedPolling = await environment(browser, { oldPolling403: true, complete: true, cacheStub: true });
        await oldDeniedPolling.page.goto('http://jk.test/importacoes_sku.html?lista_id=A&sku=001');
        await oldDeniedPolling.page.waitForFunction(() => typeof window.releaseOldPolling === 'function');
        await oldDeniedPolling.page.evaluate(() => { history.replaceState({}, '', '?lista_id=B&sku=001'); return carregarDetalheSku(); });
        await oldDeniedPolling.page.evaluate(() => window.releaseOldPolling());
        await oldDeniedPolling.page.waitForFunction(() => controladoresDetalheSku.size === 0);
        assert.strictEqual(await oldDeniedPolling.page.locator('#vTituloPt').textContent(), 'Cadastro store-B');
        assert.strictEqual(await oldDeniedPolling.page.locator('.layout .card').count(), 3);
        assert.strictEqual(await oldDeniedPolling.page.evaluate(() => cacheInvalidations), 0, '403 antigo não invalida o contexto atual.');
        assert.deepStrictEqual(oldDeniedPolling.state.errors, []);
        await oldDeniedPolling.context.close();

        const pending = await environment(browser, { queuedDetail: true, fastPolling: true, complete: true });
        await pending.page.goto('http://jk.test/importacoes_sku.html?lista_id=A&sku=001');
        await waitUntil(() => pending.state.queuedReads > 0);
        await pending.page.evaluate(() => { const end = Date.now() + 120001; Date.now = () => end; });
        await pending.page.waitForFunction(() => document.getElementById('cacheSkuStatus').textContent.includes('Não foi possível atualizar'));
        assert.strictEqual(await pending.page.locator('#vSku').textContent(), '001');
        assert.strictEqual(await pending.page.locator('#vQtd').textContent(), '2');
        assert.strictEqual(pending.state.patches.length, 0);
        const pendingReads = pending.state.queuedReads;
        await pending.page.waitForTimeout(150);
        assert.strictEqual(pending.state.queuedReads, pendingReads, 'Acompanhamento encerra no prazo, preservando os dados salvos.');
        assert.deepStrictEqual(pending.state.errors, []);
        await pending.context.close();

        const queuedStale = await environment(browser, { queuedDetail: true, fastPolling: true, complete: true });
        await queuedStale.page.goto('http://jk.test/importacoes_sku.html?lista_id=A&sku=001');
        await waitUntil(() => queuedStale.state.queuedReads > 0);
        await queuedStale.page.evaluate(() => { history.replaceState({}, '', '?lista_id=B&sku=001'); return carregarDetalheSku(); });
        const readsAfterNavigation = queuedStale.state.queuedReads;
        queuedStale.state.ready = true;
        await queuedStale.page.waitForTimeout(150);
        assert.strictEqual(await queuedStale.page.locator('#vTituloPt').textContent(), 'Cadastro store-B');
        assert.strictEqual(queuedStale.state.queuedReads, readsAfterNavigation, 'A geração anterior não continua consultando.');
        assert.strictEqual(await queuedStale.page.evaluate(() => controladoresDetalheSku.size), 0, 'Timer anterior é cancelado ao navegar.');
        assert(queuedStale.state.patches.every(patch => patch.pathname.includes('/B/')));
        assert.deepStrictEqual(queuedStale.state.errors, []);
        await queuedStale.context.close();

        const first = await environment(browser, { hold: true });
        await first.page.goto('http://jk.test/importacoes_sku.html?lista_id=A&sku=001');
        await first.page.waitForFunction(() => document.getElementById('vSku').textContent === '001');
        await waitUntil(() => Object.entries(first.state.calls).filter(([key]) => key !== 'global').every(([,value]) => value === 1));
        assert.strictEqual(await first.page.locator('#vQtd').textContent(), '2');
        assert.strictEqual(await first.page.locator('#vTituloPt').textContent(), 'Produto da lista A');
        assert.strictEqual(await first.page.locator('#vDolar').textContent(), await first.page.evaluate(() => fmtUsd(10)));
        assert.strictEqual(await first.page.locator('#vCustoTotalProduto').textContent(), '-');
        assert.strictEqual(first.state.patches.length, 0);
        await first.page.locator('.kv.prop').dblclick();
        await first.page.locator('.prop-input').fill('77');
        await first.page.evaluate(() => { window.editorNode = document.querySelector('.prop-input'); });
        releaseComplements(first.state);
        await waitUntil(() => first.state.patches.length === 1);
        assert.strictEqual(await first.page.locator('#vTituloPt').textContent(), 'Cadastro store-A');
        assert.strictEqual(await first.page.evaluate(() => ultimoValorUnitarioProduto), 108.57);
        assert.strictEqual(await first.page.evaluate(() => document.querySelector('.prop-input') === window.editorNode && document.activeElement === window.editorNode), true);
        assert.strictEqual(await first.page.locator('.prop-input').inputValue(), '77');
        assert.strictEqual(first.state.calls.global, 0);
        assert.strictEqual(first.state.patches[0].payload.precos_concorrentes.concorrente_1, 200);
        assert.deepStrictEqual(first.state.errors, []);
        await first.page.locator('.prop-input').press('Escape');
        await first.context.close();

        const complete = await environment(browser, { complete: true });
        await complete.page.goto('http://jk.test/importacoes_sku.html?lista_id=A&sku=001');
        await waitUntil(() => complete.state.patches.length === 1);
        assert.strictEqual(complete.state.calls.mapa, 0, 'Lista enriquecida dispensa o catálogo M3');
        assert.strictEqual(await complete.page.evaluate(() => ultimoValorUnitarioProduto), 108.57);
        assert.deepStrictEqual(complete.state.errors, []);
        await complete.context.close();

        const zeroQuantity = await environment(browser, { complete: true, zeroQuantity: true });
        await zeroQuantity.page.goto('http://jk.test/importacoes_sku.html?lista_id=A&sku=001');
        await waitUntil(() => zeroQuantity.state.patches.length === 1);
        assert.strictEqual(zeroQuantity.state.calls.mapa, 0, 'Quantidade zero dispensa metragem e preço do item');
        assert.strictEqual(await zeroQuantity.page.evaluate(() => ultimoValorUnitarioProduto), 108.57, 'Item com quantidade zero não invalida o rateio dos demais');
        const rateioComZero = await zeroQuantity.page.evaluate(() => calcularValorPorM3PeloCabecalho({}, [
            { Quantidade: 2, 'Valor total': 0, 'Valor unidade': 99, 'M3 individual': 0.1 },
            { Quantidade: 1, 'Valor total': 10, 'Valor unidade': 10, 'M3 individual': 0.1 },
        ], new Map()));
        assert(Math.abs(rateioComZero - 10 / 0.3) < 1e-10, 'Total zero informado não deve ser substituído pelo preço unitário');
        assert.deepStrictEqual(zeroQuantity.state.errors, []);
        await zeroQuantity.context.close();

        const fallbackFx = await environment(browser, { complete: true, noDollar: true });
        await fallbackFx.page.goto('http://jk.test/importacoes_sku.html?lista_id=A&sku=001');
        await waitUntil(() => fallbackFx.state.patches.length === 1);
        assert.strictEqual(await fallbackFx.page.evaluate(() => ultimoValorUnitarioProduto), 108.57, 'Cotação do topo preserva fallback sem cotação da compra');
        assert.deepStrictEqual(fallbackFx.state.errors, []);
        await fallbackFx.context.close();

        const delayedFx = await environment(browser, { complete: true, noDollar: true, holdFx: true });
        await delayedFx.page.goto('http://jk.test/importacoes_sku.html?lista_id=A&sku=001');
        await delayedFx.page.waitForFunction(() => document.getElementById('vSku').textContent === '001');
        assert.strictEqual(await delayedFx.page.locator('#vCustoTotalProduto').textContent(), '-');
        assert.strictEqual(delayedFx.state.patches.length, 0);
        delayedFx.state.fx.resolve();
        await waitUntil(() => delayedFx.state.patches.length === 1);
        assert.strictEqual(await delayedFx.page.evaluate(() => ultimoValorUnitarioProduto), 108.57);
        assert.deepStrictEqual(delayedFx.state.errors, []);
        await delayedFx.context.close();

        const independentCosts = await environment(browser, { complete: true, holdConcorrentes: true });
        await independentCosts.page.goto('http://jk.test/importacoes_sku.html?lista_id=A&sku=001');
        await independentCosts.page.waitForFunction(() => ultimoValorUnitarioProduto === 108.57);
        assert.strictEqual(independentCosts.state.patches.length, 0, 'Concorrentes pendentes não bloqueiam o custo nem publicam análise parcial');
        independentCosts.state.concorrentes.resolve();
        await waitUntil(() => independentCosts.state.patches.length === 1);
        assert.deepStrictEqual(independentCosts.state.errors, []);
        await independentCosts.context.close();

        const timeout = await environment(browser, { hold: true, fastTimeout: true });
        await timeout.page.goto('http://jk.test/importacoes_sku.html?lista_id=A&sku=001');
        await timeout.page.waitForFunction(() => document.getElementById('erro').textContent.includes('complementares'));
        assert.strictEqual(await timeout.page.locator('#vSku').textContent(), '001');
        assert.strictEqual(await timeout.page.locator('#vTituloPt').textContent(), 'Produto da lista A');
        assert.strictEqual(await timeout.page.locator('#vValorPorM3').textContent(), '-');
        assert.strictEqual(await timeout.page.locator('#vCustoTotalProduto').textContent(), '-');
        assert.strictEqual(timeout.state.patches.length, 0);
        await timeout.page.locator('.kv.prop').dblclick();
        await timeout.page.locator('.prop-input').fill('80');
        await timeout.page.locator('.prop-input').press('Enter');
        assert.strictEqual(timeout.state.patches.length, 0, 'Edição não persiste uma análise incompleta');
        releaseComplements(timeout.state);
        await timeout.page.evaluate(() => carregarDetalheSku());
        await waitUntil(() => timeout.state.patches.length === 1);
        assert.strictEqual(await timeout.page.locator('#erro').textContent(), '');
        assert.deepStrictEqual(timeout.state.errors, []);
        await timeout.context.close();

        const wrongStore = await environment(browser, { complete: true, wrongStore: true });
        await wrongStore.page.goto('http://jk.test/importacoes_sku.html?lista_id=A&sku=001');
        await wrongStore.page.waitForFunction(() => document.getElementById('erro').textContent.includes('complementares'));
        assert.strictEqual(await wrongStore.page.locator('#vTituloPt').textContent(), 'Produto da lista A');
        assert.strictEqual(wrongStore.state.calls.global, 0);
        assert.deepStrictEqual(wrongStore.state.errors, []);
        await wrongStore.context.close();

        const missingValues = await environment(browser, { complete: true, missingValues: true });
        await missingValues.page.goto('http://jk.test/importacoes_sku.html?lista_id=A&sku=001');
        await missingValues.page.waitForFunction(() => controladoresDetalheSku.size === 0);
        assert.strictEqual(await missingValues.page.locator('#vDolar').textContent(), '-');
        assert.strictEqual(await missingValues.page.locator('#vValorTotalDolar').textContent(), '-', 'Preço ausente não vira total zero');
        assert.strictEqual(await missingValues.page.locator('#vValorPorM3').textContent(), '-', 'Lista com preço ausente não calcula rateio parcial');
        assert.strictEqual(await missingValues.page.locator('#vCustoTotalProduto').textContent(), '-');
        assert.strictEqual(missingValues.state.patches.length, 0);
        assert.deepStrictEqual(missingValues.state.errors, []);
        await missingValues.context.close();

        const fiscal = await environment(browser, { complete: true, failFiscal: true });
        await fiscal.page.goto('http://jk.test/importacoes_sku.html?lista_id=A&sku=001');
        await fiscal.page.waitForFunction(() => document.getElementById('erro').textContent.includes('complementares'));
        assert.strictEqual(await fiscal.page.locator('#vCustoTotalProduto').textContent(), '-');
        assert.strictEqual(fiscal.state.calls.impostos, 1);
        assert.strictEqual(fiscal.state.patches.length, 0);
        assert.deepStrictEqual(fiscal.state.errors, []);
        await fiscal.context.close();

        const stale = await environment(browser, { hold: true });
        await stale.page.goto('http://jk.test/importacoes_sku.html?lista_id=A&sku=001');
        await waitUntil(() => stale.state.calls.cadastro === 1 && stale.state.calls.mapa === 1);
        await stale.page.evaluate(() => { history.replaceState({}, '', '?lista_id=B&sku=001'); return carregarDetalheSku(); });
        await waitUntil(() => stale.state.patches.length === 1);
        releaseComplements(stale.state);
        assert.strictEqual(await stale.page.locator('#vTituloPt').textContent(), 'Cadastro store-B');
        assert.strictEqual(await stale.page.locator('#erro').textContent(), '');
        assert(stale.state.patches.every(patch => patch.pathname.includes('/B/')));
        assert.deepStrictEqual(stale.state.errors, []);
        await stale.context.close();

        const staleDetail = await environment(browser, { holdDetail: true });
        await staleDetail.page.goto('http://jk.test/importacoes_sku.html?lista_id=A&sku=001');
        await staleDetail.page.evaluate(() => { history.replaceState({}, '', '?lista_id=B&sku=001'); return carregarDetalheSku(); });
        staleDetail.state.detail.resolve();
        assert.strictEqual(await staleDetail.page.locator('#vTituloPt').textContent(), 'Cadastro store-B');
        assert.strictEqual(await staleDetail.page.locator('#erro').textContent(), '');
        assert.deepStrictEqual(staleDetail.state.errors, []);
        await staleDetail.context.close();

        const stalePrice = await environment(browser, { complete: true, holdPrice: true });
        await stalePrice.page.goto('http://jk.test/importacoes_sku.html?lista_id=A&sku=001');
        await stalePrice.page.waitForFunction(() => document.getElementById('lblConcorrente1').textContent === 'MLB1234567890');
        await stalePrice.page.evaluate(() => { history.replaceState({}, '', '?lista_id=B&sku=001'); return carregarDetalheSku(); });
        await stalePrice.page.evaluate(() => window.releasePrice());
        await stalePrice.page.waitForTimeout(40);
        assert.strictEqual(await stalePrice.page.locator('#vConcorrente1').textContent(), await stalePrice.page.evaluate(() => fmtBRL(200)));
        assert(stalePrice.state.patches.every(patch => patch.pathname.includes('/B/')));
        assert.deepStrictEqual(stalePrice.state.errors, []);
        await stalePrice.context.close();

        console.log('SKU: snapshot imediato, fila 202/stale, prazo, 403, complementos, loja exata, cálculo, edição/foco, timeout/retry e respostas antigas: OK');
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
