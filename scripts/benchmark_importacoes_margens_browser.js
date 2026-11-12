'use strict';

// Real Chromium + local FastAPI financial handlers. Providers and tenant data are synthetic.
// Usage: node scripts/benchmark_importacoes_margens_browser.js --iterations 3 --output <result.json>
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const path = require('node:path');
const { performance } = require('node:perf_hooks');
const { setTimeout: delay } = require('node:timers/promises');
const { parseArgs } = require('node:util');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const auth = { Authorization: 'Bearer synthetic' };
const scenarios = [
    { name: 'uniforme', provider_limit: 4 },
    { name: 'sku_lento', provider_limit: 4 },
    { name: 'provedor_limitado_2', provider_limit: 2 },
];

function summarize(values) {
    const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
    if (!sorted.length) return null;
    const middle = Math.floor(sorted.length / 2);
    return { p50: +(sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2).toFixed(2),
        p95: +sorted[Math.ceil(sorted.length * .95) - 1].toFixed(2), samples: sorted.length };
}

async function unusedPort() {
    const server = net.createServer();
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const port = server.address().port;
    await new Promise(resolve => server.close(resolve));
    return port;
}

async function api(origin, route, options = {}) {
    const response = await fetch(origin + route, { ...options,
        headers: { ...auth, ...(options.headers || {}) }, signal: AbortSignal.timeout(20000) });
    assert.equal(response.status, 200, route + ': ' + response.status + ' ' + await response.clone().text());
    return response.json();
}

async function startServer(concurrency) {
    const port = await unusedPort();
    const origin = 'http://127.0.0.1:' + port;
    const child = spawn(process.env.PYTHON || 'python', ['-u',
        path.join(root, 'tests/helpers/importacoes_margens_benchmark_server.py'), '--port', String(port)], {
        cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
        env: { ...process.env, JK_IMPORTACOES_MARGENS_CONCORRENCIA: String(concurrency) },
    });
    let output = '';
    child.stdout.on('data', value => { output = (output + value).slice(-12000); });
    child.stderr.on('data', value => { output = (output + value).slice(-12000); });
    child.on('error', error => { output += String(error); });
    const deadline = performance.now() + 120000;
    while (performance.now() < deadline) {
        if (child.exitCode !== null) throw new Error('Fixture server exited: ' + output);
        try {
            const response = await fetch(origin + '/__benchmark/health', { signal: AbortSignal.timeout(500) });
            if (response.ok) {
                const configuration = await api(origin, '/api/medias-compras/listas-pedidos/configuracao-margens');
                assert.equal(configuration.concorrencia_lista, concurrency);
                return { origin, child, concurrency, output: () => output };
            }
        } catch (error) {
            if (error.code === 'ERR_ASSERTION') { child.kill(); throw error; }
        }
        await delay(100);
    }
    child.kill();
    throw new Error('Fixture server did not start: ' + output);
}

async function installTiming(context, origin, count) {
    await context.addInitScript(({ origin, count }) => {
        if (location.origin !== origin) return;
        localStorage.setItem('user_data', JSON.stringify({ client_id: 'benchmark', username: 'synthetic' }));
        localStorage.setItem('access_token', 'synthetic');
        localStorage.setItem('permissions', JSON.stringify({ importacoes: true, medias_compras: true }));
        const timing = window.__margensBench = {};
        new MutationObserver(() => {
            if (location.pathname.endsWith('importacoes_sku.html')) {
                if (/%/.test(document.getElementById('vMargemMlb1')?.textContent || '')) timing.own_ms ??= performance.now();
                return;
            }
            const rows = [...document.querySelectorAll('#tbodyLista tr[data-sku]')];
            if (rows.some(row => row.querySelector('[data-coluna="margens_loja"] .own-margin'))) timing.first_own_ms ??= performance.now();
            if (rows.some(row => row.querySelector('[data-coluna="margens_concorrentes"] .competitor-margin'))) timing.first_competitor_ms ??= performance.now();
            if (rows.length === count && rows.every(row => row.querySelectorAll('[data-coluna="margens_loja"] .own-margin').length === 2
                && row.querySelectorAll('[data-coluna="margens_concorrentes"] .competitor-margin').length === 2)) timing.all_ms ??= performance.now();
        }).observe(document, { childList: true, subtree: true, characterData: true });
    }, { origin, count });
    // No remote fonts, images or marketplace pages are fetched during the fixture.
    await context.route('**/*', route => new URL(route.request().url()).origin === origin
        ? route.continue() : route.fulfill({ status: 200, body: '' }));
}

function normalizeSaved(saved) {
    return Object.fromEntries(Object.entries(saved).filter(([sku]) => sku !== '900').map(([sku, analysis]) => [sku, {
        custo_unitario: analysis.custo_unitario, fonte_custo: analysis.fonte_custo, store_id: analysis.store_id,
        margens: analysis.margens, margens_anuncios_loja: analysis.margens_anuncios_loja,
    }]));
}

async function waitMargins(page) {
    await page.waitForFunction(() => Number.isFinite(window.__margensBench?.all_ms), null, { timeout: 40000 });
    await page.waitForFunction(() => typeof filaMargensLista !== 'undefined' && !filaMargensLista, null, { timeout: 40000 });
    return page.evaluate(() => window.__margensBench);
}

async function runSample(browser, server, scenario, iteration, count, interact = false) {
    await api(server.origin, '/__benchmark/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scenario: scenario.name, provider_limit: scenario.provider_limit, sku_count: count }) });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
    await installTiming(context, server.origin, count);
    const page = await context.newPage();
    try {
        await page.goto(server.origin + '/importacoes_lista.html?lista_id=A');
        let interactive = null;
        if (interact) {
            // Measure interaction separately: focusing this tab can pause the list's dispatch.
            const deadline = performance.now() + 20000;
            let busy;
            do {
                busy = await api(server.origin, '/__benchmark/metrics');
                if ((busy.active.list || 0) >= server.concurrency) break;
                await delay(15);
            } while (performance.now() < deadline);
            assert.ok((busy.active.list || 0) >= server.concurrency, 'The list did not reach its configured concurrency');
            const detail = await context.newPage();
            await detail.goto(server.origin + '/importacoes_sku.html?lista_id=A&sku=900');
            await detail.waitForFunction(() => Number.isFinite(window.__margensBench?.own_ms), null, { timeout: 20000 });
            interactive = await detail.evaluate(() => window.__margensBench.own_ms);
            await detail.waitForFunction(() => contextoDetalheSku?.consultaOfertasCompactas?.finalizada, null, { timeout: 20000 });
            await detail.close();
            await page.bringToFront();
        }
        const cold = await waitMargins(page);
        const beforeReload = await api(server.origin, '/__benchmark/metrics');
        const saved = normalizeSaved(beforeReload.saved);
        assert.equal(Object.keys(saved).length, count);
        assert.equal(beforeReload.completed.list, count);
        assert.equal(beforeReload.maximum.list, server.concurrency);
        if (interact) assert.ok(beforeReload.maximum.interactive >= 1);
        assert.ok(beforeReload.maximum.provider <= scenario.provider_limit);
        for (const [sku, analysis] of Object.entries(saved)) {
            assert.equal(analysis.store_id, 'store-benchmark');
            assert.equal(Object.keys(analysis.margens).length, 2);
            assert.equal(Object.keys(analysis.margens_anuncios_loja).length, 2);
            for (const record of [...Object.values(analysis.margens), ...Object.values(analysis.margens_anuncios_loja)]) {
                assert.equal(record.financeiro_exato, true);
                assert.ok(Number.isFinite(record.margem_percentual));
                assert.equal(record.custo_unitario, analysis.custo_unitario);
                assert.equal(record.seller_id, '111');
            }
            const displayed = await page.locator('tr[data-sku="' + sku + '"] [data-col-key="valor_unit_brl"]').innerText();
            assert.equal(Number(displayed.replace(/[^\d,.-]/g, '').replace(/\./g, '').replace(',', '.')), analysis.custo_unitario);
        }
        let warm = null;
        if (!interact) {
            await page.reload();
            warm = await waitMargins(page);
            const afterReload = await api(server.origin, '/__benchmark/metrics');
            assert.equal(afterReload.completed.list, beforeReload.completed.list, 'Reopening must reuse valid financial calculations');
            assert.deepEqual(afterReload.calls, beforeReload.calls, 'Reopening must not repeat provider calls');
            assert.deepEqual(normalizeSaved(afterReload.saved), saved);
            assert.deepEqual(afterReload.http_errors, []);
        }
        assert.deepEqual(beforeReload.http_errors, []);
        assert.deepEqual(errors, []);
        return { scenario: scenario.name, iteration, concurrency: server.concurrency,
            cold: { first_own_ms: cold.first_own_ms, first_competitor_ms: cold.first_competitor_ms,
                all_ms: cold.all_ms, interactive_sku_first_own_ms: interactive },
            warm: warm ? { first_own_ms: warm.first_own_ms, all_ms: warm.all_ms } : null,
            maximum: beforeReload.maximum, provider_calls: beforeReload.calls, errors: [], saved };
    } catch (error) {
        const metrics = await api(server.origin, '/__benchmark/metrics').catch(() => null);
        const pages = await Promise.all(context.pages().map(async tab => ({
            page: new URL(tab.url()).pathname,
            detail: await tab.evaluate(() => {
                if (typeof contextoDetalheSku === 'undefined') return null;
                const c = contextoDetalheSku;
                const fonte = value => value && { storeId: value.storeId, sellerId: value.sellerId,
                    siteId: value.siteId, fontes_versao: value.fontes_versao, preco: value.preco,
                    variacoes: value.variacoes, statusAnuncios: value.statusAnuncios };
                return { storeId: c?.storeId, confirmado: c?.compactoConfirmado, podeSalvar: c?.podeSalvar,
                    custoPendente: custoComplementosPendente, custo: ultimoValorUnitarioProduto,
                    temRevisao: Boolean(c?.revisaoFontesCusto), temCalculo: Boolean(c?.calculoCompartilhado),
                    temAnalise: Boolean(ultimaAnaliseConcorrentes), ultimaFonte: fonte(c?.fontesUltimaAnalise),
                    atualizando: Boolean(c?.atualizacaoMargensCompacta),
                    vigente: dadosSkuVigentes(c), complementos: analiseComplementosPronta,
                    ofertas: fonte(c?.ofertasCompactas), proprias: fonte(c?.ofertasProprias),
                };
            }),
        })));
        console.error(JSON.stringify({ errors, pages, active: metrics?.active, completed: metrics?.completed,
            http_errors: metrics?.http_errors, saved_count: Object.keys(metrics?.saved || {}).length,
            server_errors: server.output() }, null, 2));
        throw error;
    } finally { await context.close(); }
}

async function main() {
    const { values } = parseArgs({ options: { iterations: { type: 'string', default: '3' },
        skus: { type: 'string', default: '12' }, output: { type: 'string' } } });
    const iterations = Number(values.iterations), count = Number(values.skus);
    assert.ok(Number.isInteger(iterations) && iterations >= 1 && iterations <= 20);
    assert.ok(Number.isInteger(count) && count >= 4 && count <= 100);
    const browser = await chromium.launch({ headless: true });
    const servers = [];
    const samples = [];
    try {
        // Separate processes keep worker limits and provider caches independent.
        for (const concurrency of [2, 4]) servers.push(await startServer(concurrency));
        for (const scenario of scenarios) {
            for (let iteration = 0; iteration < iterations; iteration++) {
                const order = iteration % 2 ? [...servers].reverse() : servers;
                const pair = [];
                for (const server of order) {
                    const sample = await runSample(browser, server, scenario, iteration + 1, count);
                    const interaction = await runSample(browser, server, scenario, iteration + 1, count, true);
                    assert.deepEqual(sample.saved, interaction.saved, 'Opening an interactive SKU cannot change the list margins');
                    sample.interaction = { list_all_ms: interaction.cold.all_ms,
                        interactive_sku_first_own_ms: interaction.cold.interactive_sku_first_own_ms,
                        maximum: interaction.maximum, provider_calls: interaction.provider_calls };
                    pair.push(sample); samples.push(sample);
                    console.log(scenario.name + ' · ' + server.concurrency + ' SKUs · rodada ' + (iteration + 1)
                        + ': lista fria ' + sample.cold.all_ms.toFixed(0) + ' ms; SKU interativo ' + sample.interaction.interactive_sku_first_own_ms.toFixed(0) + ' ms');
                }
                assert.deepEqual(pair[0].saved, pair[1].saved, '2 and 4 workers must save identical costs and margins');
            }
        }
        const summary = scenarios.map(scenario => ({ scenario: scenario.name, provider_limit: scenario.provider_limit,
            configurations: servers.map(server => {
                const rows = samples.filter(sample => sample.scenario === scenario.name && sample.concurrency === server.concurrency);
                return { concurrency: server.concurrency,
                    cold_first_own_ms: summarize(rows.map(row => row.cold.first_own_ms)),
                    cold_all_ms: summarize(rows.map(row => row.cold.all_ms)),
                    interactive_sku_first_own_ms: summarize(rows.map(row => row.interaction.interactive_sku_first_own_ms)),
                    cold_with_interaction_all_ms: summarize(rows.map(row => row.interaction.list_all_ms)),
                    warm_all_ms: summarize(rows.map(row => row.warm.all_ms)),
                    errors: rows.flatMap(row => row.errors).length };
            }) }));
        const output = { type: 'controlled_synthetic_benchmark', iterations, skus: count,
            description: 'Chromium real, HTTP FastAPI real, fórmula financeira e persistência por SKU reais; dados e provedores sintéticos.',
            scope: 'Tempos não representam medição dos provedores ou dados de produção. A carga fria principal mantém a lista visível; a abertura simultânea do SKU extra é uma execução independente.',
            source_pool_per_kind: 2, source_delay_ms: { anuncios: 40, planilha: 25 },
            provider_delay_ms: { item: 45, fee: 55, shipping: 55, promotion: 35, slow_first_item_extra: 1000 },
            financial_interactive_lane_limit: 2, interactive_extra_sku: '900', summary,
            consistency: 'Cost and own/competitor margins match exactly between 2 and 4; reopening did not call financial providers.',
            samples: samples.map(({ saved, ...sample }) => sample) };
        if (values.output) fs.writeFileSync(path.resolve(values.output), JSON.stringify(output, null, 2) + '\n');
        console.log(JSON.stringify(summary, null, 2));
    } finally {
        await browser.close();
        for (const server of servers) {
            await api(server.origin, '/__benchmark/shutdown', { method: 'POST' }).catch(() => {});
            await Promise.race([new Promise(resolve => server.child.once('exit', resolve)), delay(3000)]);
            if (server.child.exitCode === null) server.child.kill();
        }
    }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
