'use strict';

// Runs the two frontend versions with identical data, request latencies and browser.
// Usage: node scripts/benchmark_importacoes_sku_browser.js --before <tree> --after <tree> [--iterations 20]
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { parseArgs } = require('node:util');
const { setTimeout: delay } = require('node:timers/promises');
const { chromium } = require('playwright');

const origin = 'http://127.0.0.1:8137';
const listPath = '/api/medias-compras/listas-pedidos/A';
const ownId = 'MLB9876543210';
const storeId = 'store-A';
const iterationsDefault = 20;
const scenarios = ['primeira_abertura', 'copia_parcial', 'copia_parcial_anuncios_lentos', 'reabertura', 'outro_sku'];
const latencies = { saved: 35, detail: 120, catalog: 70, volumes: 95, tax: 90, offers: 400,
    ownOffers: 100, margin: 95, validation: 25, fx: 35 };
const offerIdentity = { seller_id: 'seller-A', site_id: 'MLB', fontes_versao: 'fontes-fixture' };
const slowOffersMs = 900;
const itemFields = {
    II: 10, IPI: 5, PIS: 2, COFINS: 3, 'ST Minas': 'Não', 'ST Paraíba': 'Não',
    NCM: '00000001', CEST: '00.000.01', 'Descricao do NCM': 'Descrição sintética',
};
const items = Array.from({ length: 60 }, (_, index) => {
    const sku = String(index + 1).padStart(3, '0');
    return { SKU: sku, Quantidade: 2, 'Valor unidade': 10, 'Valor total': 20, Produto: 'SKU sintético ' + sku,
        Link: 'https://produto.test/' + sku, ...itemFields, ...(index < 2 ? { 'M3 individual': 0.1 } : {}) };
});
const list = { id: 'A', nome_lista: 'Lista sintética', loja: 'Loja A', store_id: storeId,
    exchange_rate: 5, status: 'Analisando orçamento', itens: items };
const catalog = Array.from({ length: 1500 }, (_, index) => {
    const sku = String(index + 1).padStart(3, '0');
    return { store_id: storeId, sku, nome: 'Cadastro ' + sku, ncm: '00000001', 'M3 individual': 0.1,
        custo: '13.00', imposto: '10', descricao: 'Descrição sintética do cadastro para o SKU ' + sku };
});
const tax = catalog.map(({ sku }) => ({ sku, ...itemFields }));
const rateio = { total_usd: 1200, total_m3: 12, m3_completo: true, valor_por_m3: 100 };
const offers = {
    anuncios_loja_store_id: storeId, anuncios_loja_nome: 'Loja A', anuncios_loja_fonte: 'loja_lista_sku',
    anuncios_loja_seller_id: 'seller-A', anuncios_loja_site_id: 'MLB',
    fontes_versao: 'fontes-fixture', anuncios_loja_status: { concorrente_1: 'active' },
    concorrentes: { concorrente_1: 'https://produto.mercadolivre.com.br/MLB-1234567890' },
    concorrentes_valores: { concorrente_1: 'R$ 200,00' },
    concorrentes_mlb: { concorrente_1: 'https://produto.mercadolivre.com.br/MLB-9876543210' },
    concorrentes_preco: { concorrente_1: 'R$ 300,00' },
};

function summarize(samples) {
    return Object.fromEntries(Object.keys(samples[0]).map(key => {
        const values = samples.map(sample => sample[key]).sort((a, b) => a - b);
        const middle = Math.floor(values.length / 2);
        const median = values.length % 2 ? values[middle] : (values[middle - 1] + values[middle]) / 2;
        return [key, { p50: Number(median.toFixed(2)), p95: Number(values[Math.ceil(values.length * .95) - 1].toFixed(2)) }];
    }));
}

function finance(payload) {
    const margin = price => ({
        preco_venda: price, item_id_loja: ownId, financeiro_exato: true,
        loja: 'Loja A', store_id: storeId, seller_id: 'seller-A', site_id: 'MLB',
        fonte_custo: payload.fonte_custo, custo_unitario: payload.custo_unitario,
        imposto_valor: price * .1, tarifa_ml: price * .15, frete_ml: 8,
        valor_liquido: price - payload.custo_unitario - price * .25 - 8,
        margem_percentual: (price - payload.custo_unitario - price * .25 - 8) * 100 / price,
    });
    return { fonte_custo: payload.fonte_custo, custo_unitario: payload.custo_unitario,
        store_id: storeId, atualizado_em: new Date().toISOString(),
        margens: Object.fromEntries(Object.entries(payload.precos_concorrentes || {}).map(([key, price]) => [key, margin(price)])),
        margens_anuncios_loja: Object.fromEntries(Object.entries(payload.precos_anuncios_loja || {}).map(([key, price]) => [key, margin(price)])) };
}

async function createContext(browser, source, offersDelay) {
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const page = await context.newPage();
    const calls = [];
    const faults = [];
    const analyses = new Map();
    page.on('pageerror', error => faults.push(error.message));
    await context.addInitScript(() => {
        if (!location.pathname.endsWith('.html')) return;
        localStorage.setItem('user_data', JSON.stringify({ client_id: 'benchmark', username: 'synthetic' }));
        localStorage.setItem('permissions', JSON.stringify({ medias_compras: true, vendas: true }));
        localStorage.setItem('access_token', 'synthetic');
        if (!location.pathname.endsWith('importacoes_sku.html')) return;
        window.__skuBench = {};
        const sku = new URLSearchParams(location.search).get('sku');
        const read = id => document.getElementById(id)?.textContent.trim();
        new MutationObserver(records => {
            if (read('vSku') !== sku) return;
            const now = performance.now();
            window.__skuBench.produto_ms ??= now;
            if (!/R\$\s*\d/.test(read('vValorUnitarioProduto') || '')) return;
            window.__skuBench.custo_ms ??= now;
            const cost = document.getElementById('vValorUnitarioProduto');
            if (typeof contextoDetalheSku !== 'undefined' && contextoDetalheSku?.listaAtualizada
                && records.some(record => cost.contains(record.target))) {
                window.__skuBench.custo_atualizado_ms ??= now;
            }
            if (/%/.test(read('vMargemMlb1') || '')) {
                window.__skuBench.primeira_margem_propria_ms ??= now;
                if (/%/.test(read('vMargemConcorrente1') || '')) window.__skuBench.margens_ms ??= now;
            }
        }).observe(document, { childList: true, subtree: true, characterData: true });
    });
    await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        const pathname = url.pathname;
        const method = route.request().method();
        const json = async (group, response, ms, status = 200) => {
            const body = JSON.stringify(response);
            const call = { group, bytes: Buffer.byteLength(body), done: false };
            calls.push(call);
            try {
                await delay(ms);
                await route.fulfill({ status, contentType: 'application/json', body });
            } catch (error) {
                if (!/closed|cancelled|aborted|interception/i.test(error.message)) throw error;
            } finally {
                call.done = true;
            }
        };
        try {
            if (url.hostname === 'economia.awesomeapi.com.br') {
                return await json('fx', { USDBRL: { bid: '5', timestamp: '1' } }, latencies.fx);
            }
            if (url.origin !== origin) return await route.fulfill({ contentType: 'text/css', body: '' });
            if (pathname === '/seed.html') {
                return await route.fulfill({ contentType: 'text/html',
                    body: '<!doctype html><script src="/cache-persistente.js"></script>' });
            }
            if (pathname === '/auth.js') return await route.fulfill({ contentType: 'application/javascript',
                body: 'window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({Authorization:"Bearer synthetic"});' });
            if (pathname === listPath) {
                const saved = url.searchParams.get('somente_salvos') === 'true';
                const all = items.map(item => analyses.has(item.SKU)
                    ? { ...item, analise_simulacao_importacao_sku: analyses.get(item.SKU) } : item);
                return await json(saved ? 'saved' : 'detail', { lista: { ...list, itens: all } },
                    latencies[saved ? 'saved' : 'detail']);
            }
            const skuPath = pathname.match(/\/listas-pedidos\/A\/skus\/(\d{3})(\/analise-concorrentes)?(\/previa)?$/);
            if (skuPath) {
                const sku = skuPath[1];
                if (skuPath[3] && method === 'POST') {
                    const payload = route.request().postDataJSON();
                    assert(payload.custo_unitario > 0);
                    assert.equal(Object.keys(payload.precos_concorrentes || {}).length, 0);
                    return await json('margin-preview', { analise_concorrentes: finance(payload) }, latencies.margin);
                }
                if (skuPath[2] && method === 'PATCH') {
                    const payload = route.request().postDataJSON();
                    assert(payload.custo_unitario > 0);
                    const result = finance(payload);
                    analyses.set(sku, result);
                    return await json('margin', { analise_concorrentes: result }, latencies.margin);
                }
                assert.equal(method, 'GET');
                if (!source.compact) return await json('missing', { detail: 'Not Found' }, 0, 404);
                if (url.searchParams.get('validar_margem') === 'true') {
                    return await json('validation', { revisao_fontes_margem: 'fixture-sources-' + sku,
                        anuncios_identidade: offerIdentity }, latencies.validation);
                }
                const saved = url.searchParams.get('somente_salvos') === 'true';
                const index = Number(sku) - 1;
                const { itens: _, ...metadata } = list;
                const item = { ...items[index], analise_simulacao_importacao_sku: analyses.get(sku) };
                return await json(saved ? 'saved' : 'detail', { success: true, lista: metadata, item,
                    cadastro: saved ? null : catalog[index], fiscal: saved ? null : tax[index], fiscal_pronto: !saved,
                    rateio: saved ? { ...rateio, total_m3: null, m3_completo: false, valor_por_m3: null } : rateio,
                    anuncios_identidade: offerIdentity, revisao_fontes_margem: 'fixture-sources-' + sku,
                    revisao: 'fixture-r1' }, latencies[saved ? 'saved' : 'detail']);
            }
            if (pathname === '/api/medias-compras/concorrentes-links') {
                if (url.searchParams.get('somente_proprios') === 'true') {
                    return await json('own-offers', { ...offers, concorrentes: {}, concorrentes_valores: {},
                        consultado_em_ms: Date.now() }, latencies.ownOffers);
                }
                return await json('offers', { ...offers, consultado_em_ms: Date.now() }, offersDelay);
            }
            if (pathname === '/api/impostos/produtos') return await json('tax', { produtos: tax }, latencies.tax);
            const product = pathname.match(/\/api\/cadastro\/lojas\/store-A\/produtos(?:\/(\d{3}))?$/);
            if (product) return product[1]
                ? await json('catalog', { produto: catalog[Number(product[1]) - 1] }, latencies.catalog)
                : await json('volumes', catalog, latencies.volumes);
            if (pathname.startsWith('/api/')) throw new Error('Endpoint fora da fixture: ' + method + ' ' + pathname);
            if (source.assets.has(pathname)) {
                return await route.fulfill({ status: 200, body: source.assets.get(pathname),
                    contentType: pathname.endsWith('.html') ? 'text/html' : 'application/javascript' });
            }
            return await route.fulfill({ contentType: 'application/javascript', body: '' });
        } catch (error) {
            faults.push(error.message);
            await route.abort().catch(() => {});
        }
    });
    return { context, page, calls, faults };
}

async function seedPartial(page) {
    await page.goto(origin + '/seed.html');
    await page.evaluate(async data => {
        const options = { tags: ['importacoes', 'vendas'] };
        const snapshot = { versao: 3, anunciosDaLista: true, lista: data, cadastro: null,
            mapaM3: [], impostos: [], cotacao: 5, custoPendente: true,
            concorrentes: { storeId: data.store_id, fonte: 'loja_lista_sku', links: {}, valores: {}, mlb: {}, preco: {} },
            precosOfertas: {} };
        if (!await JKCachePersistente.salvar('importacoes:SKU:A:001', snapshot, options)
            || !await JKCachePersistente.salvar('importacoes:GET:/api/medias-compras/listas-pedidos/A', { lista: data }, options)) {
            throw new Error('Cópia salva sintética indisponível.');
        }
    }, list);
}

async function open(page, sku) {
    await page.goto(origin + '/importacoes_sku.html?lista_id=A&sku=' + sku, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => Number.isFinite(window.__skuBench?.margens_ms)
        && Number.isFinite(window.__skuBench?.custo_atualizado_ms), null, { timeout: 6000 });
    return page.evaluate(() => ({ ...window.__skuBench, valores: [
        'vValorUnitarioProduto', 'vCustoTotalProduto', 'vMargemMlb1', 'vMargemConcorrente1',
    ].map(id => document.getElementById(id).textContent.replace(/^Última margem: /, 'Margem: ')) }));
}

async function settle(calls) {
    // Snapshot writes and optional rendering happen after the first visible margins.
    await delay(90);
    const deadline = performance.now() + 2500;
    while (calls.some(call => !call.done) && performance.now() < deadline) await delay(20);
    assert(calls.every(call => call.done), 'A fixture deixou uma consulta pendente.');
    await delay(35);
}

async function sample(browser, source, scenario) {
    const offersDelay = scenario === 'copia_parcial_anuncios_lentos' ? slowOffersMs : latencies.offers;
    const { context, page, calls, faults } = await createContext(browser, source, offersDelay);
    try {
        if (scenario.startsWith('copia_parcial')) await seedPartial(page);
        if (scenario === 'reabertura' || scenario === 'outro_sku') {
            await open(page, '001');
            await settle(calls);
            calls.length = 0;
        }
        const result = await open(page, scenario === 'outro_sku' ? '002' : '001');
        await settle(calls);
        assert.deepEqual(faults, []);
        return { ...result, consultas: calls.length, bytes_json: calls.reduce((sum, call) => sum + call.bytes, 0),
            gravacoes_analise: calls.filter(call => call.group === 'margin').length,
            chamadas_anuncios: calls.filter(call => ['offers', 'own-offers'].includes(call.group)).length };
    } catch (error) {
        const diagnostic = await page.evaluate(() => ({
            timings: window.__skuBench,
            status: document.getElementById('cacheSkuStatus')?.textContent,
        })).catch(() => null);
        throw new Error(source.name + '/' + scenario + ': ' + error.message
            + ' ' + JSON.stringify({ diagnostic, faults, calls }), { cause: error });
    } finally {
        await context.close();
    }
}

async function main() {
    const { values } = parseArgs({ options: {
        before: { type: 'string' }, after: { type: 'string' }, iterations: { type: 'string' },
        warmup: { type: 'string', default: '3' }, 'compact-before': { type: 'boolean' },
        scenario: { type: 'string', multiple: true },
    } });
    const iterations = Number(values.iterations || iterationsDefault);
    const warmup = Number(values.warmup);
    assert(Number.isInteger(iterations) && iterations > 0 && Number.isInteger(warmup) && warmup >= 0);
    const selected = values.scenario || scenarios;
    assert(selected.every(scenario => scenarios.includes(scenario)), 'Cenário inválido.');
    const sources = [
        values.before && { name: 'antes', root: path.resolve(values.before), compact: !!values['compact-before'] },
        values.after && { name: 'depois', root: path.resolve(values.after), compact: true },
    ].filter(Boolean);
    assert(sources.length, 'Informe --before e/ou --after.');
    const assets = ['/importacoes_sku.html', '/cache-persistente.js', '/medias_compras/importacoes-custo-simulado.js'];
    for (const source of sources) {
        source.assets = new Map(assets.map(asset => [asset, fs.readFileSync(path.join(source.root, 'static', asset.slice(1)))]));
    }
    const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE;
    const browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : { channel: 'chrome' }) });
    const htmlSha256 = Object.fromEntries(sources.map(source => [
        source.name, createHash('sha256').update(source.assets.get('/importacoes_sku.html')).digest('hex'),
    ]));
    const results = { navegador: browser.version(), iterations, warmup, itens: items.length,
        cadastro: catalog.length, html_sha256: htmlSha256,
        latencias_mock_ms: latencies, anuncios_lentos_ms: slowOffersMs, cenarios: {} };
    try {
        for (const scenario of selected) {
            const samples = Object.fromEntries(sources.map(source => [source.name, []]));
            for (let i = -warmup; i < iterations; i++) {
                const order = i % 2 ? [...sources].reverse() : sources;
                for (const source of order) {
                    const result = await sample(browser, source, scenario);
                    if (i >= 0) samples[source.name].push(result);
                }
            }
            const { valores } = samples[sources[0].name][0];
            for (const observations of Object.values(samples)) {
                for (const observed of observations) assert.deepEqual(observed.valores, valores, 'A comparação exige custos e margens equivalentes.');
            }
            results.cenarios[scenario] = Object.fromEntries(Object.entries(samples).map(([source, observations]) => [
                source, { valores, metricas: summarize(observations.map(({ valores: _, ...metrics }) => metrics)),
                    amostras: observations.map(({ valores: _, ...metrics }) => metrics) },
            ]));
            console.error(scenario + ': ' + iterations + ' amostras por versão.');
        }
    } finally {
        await browser.close();
    }
    console.log(JSON.stringify(results, null, 2));
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
