'use strict';

const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { environment, origin, gate } = require('./importacoes_custos_revisoes_browser');

async function esperar(page, condition) {
    const deadline = Date.now() + 10000;
    while (!condition()) {
        assert(Date.now() < deadline, 'Tempo limite para a condição da fila.');
        await page.waitForTimeout(20);
    }
}

async function ambiente(browser, concurrency = 2, options = {}) {
    const env = await environment(browser);
    const { context, lista, item, state } = env;
    lista.itens = Array.from({ length: 7 }, (_, index) => ({ ...item,
        SKU: index ? String(index).padStart(3, '0') : '47', analise_simulacao_importacao_sku: undefined }));
    const pending = new Map();
    const stats = { active: 0, peak: 0, started: [], completed: [], headers: [] };
    const json = (route, data, status = 200) => route.fulfill({
        status, contentType: 'application/json', body: JSON.stringify(data),
    }).catch(() => {});
    await context.route('**/configuracao-margens', route => json(route,
        options.deniedConfig ? { detail: 'Sem acesso' } : { concorrencia_lista: concurrency },
        options.deniedConfig ? 403 : options.legacyConfig ? 404 : 200));
    await context.route('**/skus/*/analise-concorrentes', async route => {
        const sku = decodeURIComponent(new URL(route.request().url()).pathname.split('/skus/')[1].split('/')[0]);
        const payload = route.request().postDataJSON();
        const hold = gate(), done = gate();
        pending.set(sku, { hold, done });
        stats.started.push(sku);
        stats.headers.push(route.request().headers()['x-jk-importacoes-origem']);
        stats.active++;
        stats.peak = Math.max(stats.peak, stats.active);
        const status = await hold.promise;
        stats.active--;
        if (status !== 200) {
            await json(route, { detail: 'Acesso indisponível.' }, status);
            done.resolve(); return;
        }
        const row = lista.itens.find(row => row.SKU === sku);
        const margin = { preco_venda: 85.89, margem_percentual: 30.97, custo_unitario: payload.custo_unitario,
            fonte_custo: payload.fonte_custo, financeiro_exato: true, item_id_loja: 'MLB9876543210',
            variation_id: null, store_id: 'store-A', seller_id: 'seller-A', site_id: 'MLB' };
        row.analise_simulacao_importacao_sku = { ...payload, store_id: 'store-A', fontes_versao: 3,
            atualizado_em: new Date().toISOString(), anuncios_versao: 2, anuncios_loja_store_id: 'store-A',
            anuncios_loja_seller_id: 'seller-A', anuncios_loja_site_id: 'MLB', anuncios_consultado_em_ms: Date.now(),
            anuncios_loja: [{ chave: 'concorrente_1', item_id: 'MLB9876543210', variation_id: null,
                preco: null, sku, seller_id: 'seller-A', site_id: 'MLB' }],
            margens: { concorrente_1: margin }, margens_anuncios_loja: {} };
        stats.completed.push(sku);
        await json(route, { analise_concorrentes: row.analise_simulacao_importacao_sku });
        done.resolve();
    });
    const page = await context.newPage();
    const started = n => esperar(page, () => stats.started.length >= n);
    const complete = async (sku, status = 200) => {
        const entry = pending.get(sku);
        assert(entry, 'O SKU precisa ter iniciado: ' + sku);
        entry.hold.resolve(status); await entry.done.promise;
    };
    const close = async () => { for (const entry of pending.values()) entry.hold.resolve(200); await context.close(); };
    return { ...env, page, stats, started, complete, close };
}

async function verificarParalelismo(browser, concurrency, legacyConfig = false) {
    const env = await ambiente(browser, concurrency, { legacyConfig });
    const { page, lista, stats, state, started, complete } = env;
    const limit = legacyConfig ? 2 : concurrency;
    try {
        await page.goto(origin + '/importacoes_lista.html?lista_id=A');
        await started(limit);
        assert.equal(stats.started.length, limit, 'A fila preenche apenas o limite negociado.');
        assert.equal(stats.peak, limit);
        const first = stats.started[0], fast = stats.started[1];
        await complete(fast);
        await started(limit + 1);
        await page.waitForFunction(sku => document.querySelector('tr[data-sku="' + sku + '"] [data-coluna="margens_concorrentes"]')
            ?.textContent.includes('30,97%'), fast);
        assert(!stats.completed.includes(first), 'Um SKU lento continua em andamento enquanto os outros avançam.');
        const finished = new Set([fast]);
        while (finished.size < lista.itens.length - 1) {
            await esperar(page, () => stats.started.some(sku => sku !== first && !finished.has(sku)));
            const next = stats.started.find(sku => sku !== first && !finished.has(sku));
            await complete(next); finished.add(next);
        }
        await complete(first);
        await page.waitForFunction(() => !filaMargensLista);
        assert.equal(stats.started.length, lista.itens.length);
        assert.equal(new Set(stats.started).size, lista.itens.length);
        assert.equal(stats.peak, limit);
        assert(stats.headers.every(value => value === 'lista'));
        for (const row of lista.itens) {
            const text = await page.locator('tr[data-sku="' + row.SKU + '"] [data-coluna="margens_concorrentes"]').innerText();
            assert.match(text, /30,97%/);
        }
        const before = stats.started.length;
        await page.reload();
        await page.waitForFunction(() => !filaMargensLista && document.querySelectorAll('.competitor-margin').length === 7);
        assert.equal(stats.started.length, before, 'Cálculos válidos são reutilizados após reabrir.');
        assert.deepEqual(state.errors, []);
    } finally { await env.close(); }
}

async function verificarAcesso(browser) {
    const env = await ambiente(browser);
    const { page, stats, started, complete } = env;
    try {
        await page.goto(origin + '/importacoes_lista.html?lista_id=A');
        await started(2);
        await complete(stats.started[0], 403);
        await page.waitForFunction(() => filaMargensLista?.interrompida === true);
        await complete(stats.started[1]);
        await page.waitForFunction(() => !filaMargensLista);
        assert.equal(stats.started.length, 2, 'Negação de acesso interrompe todos os trabalhadores.');
        assert.equal(await page.locator('.competitor-margin').count(), 0, 'Resposta atrasada não repõe margem após 403.');
        assert.equal(await page.evaluate(() => Object.keys(cacheMargensLista).length), 0);
    } finally { await env.close(); }
}

async function verificarNavegacao(browser) {
    const env = await ambiente(browser);
    const { page, stats, started, complete } = env;
    try {
        await page.goto(origin + '/importacoes_lista.html?lista_id=A');
        await started(2);
        await page.evaluate(() => { geracaoCarregamentoLista++; });
        for (const sku of stats.started) await complete(sku);
        await page.waitForFunction(() => !filaMargensLista);
        assert.equal(stats.started.length, 2);
        assert.equal(await page.locator('.competitor-margin').count(), 0, 'Respostas de outro carregamento são descartadas.');
    } finally { await env.close(); }
}

(async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        await verificarParalelismo(browser, 2);
        await verificarParalelismo(browser, 4);
        await verificarParalelismo(browser, 4, true);
        await verificarAcesso(browser);
        await verificarNavegacao(browser);
        const denied = await ambiente(browser, 4, { deniedConfig: true });
        try {
            await denied.page.goto(origin + '/importacoes_lista.html?lista_id=A');
            await denied.page.waitForFunction(() => document.getElementById('cacheListaStatus').textContent.includes('atualizados'));
            assert.equal(denied.stats.started.length, 0);
        } finally { await denied.close(); }
        console.log('Margens paralelas: limites2/4, progresso independente, cache, fallback, 403 e geração anterior: OK');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
