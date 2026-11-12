'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const origin = 'http://jk.test';
const normalize = text => String(text).replace(/\s+/g, ' ').trim();
const previewDir = process.env.JK_CUSTO_PREVIEW_DIR;
if (previewDir) fs.mkdirSync(previewDir, { recursive: true });

function gate() {
    let resolve;
    return { promise: new Promise(done => { resolve = done; }), resolve: () => resolve() };
}

const products = [
    { sku: '001', cubagem_sku: '1', custo: '1.234,56' },
    { sku: '1', custo: '42.75' },
    { sku: 'ZERO', custo: 0 },
    { sku: 'VAZIO', custo: '' },
    { sku: 'INVALIDO', custo: 'indisponível' },
    { sku: 'NEGATIVO', custo: -3 },
];

function list(id = 'A') {
    return {
        id, nome_lista: 'Pedido ' + id, store_id: 'store-' + id, dolar_hoje: 5,
        itens: [...products, { sku: 'AUSENTE' }, { sku: 'OUTRA-LOJA' }].map(product => ({
            SKU: product.sku, Produto: 'Produto ' + product.sku, Quantidade: 2,
            'Valor unidade': 10, 'Valor total': 20, 'Valor Unitário R$': 999,
            'M3 individual': 0.1, 'Cubagem status': 'cadastro', NCM: '00000001',
            II: 10, IPI: 5, PIS: 2, COFINS: 3, 'ST Minas': 'Não', 'ST Paraíba': 'Não',
            analise_concorrentes: { custo_unitario: 888, margens: {} },
        })),
    };
}

async function environment(browser, options = {}) {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await context.newPage();
    page.setDefaultTimeout(7000);
    const state = { fiscal: gate(), costA: gate(), requests: [], errors: [], global: 0 };
    page.on('pageerror', error => state.errors.push(error.message));
    await context.addInitScript(() => {
        localStorage.setItem('importacoes_lista_ordem_colunas_v1', JSON.stringify(['sku', 'valor_unit_brl', 'acao']));
        localStorage.setItem('importacoes_sku_card_order_v1', JSON.stringify({ global: {
            produto_cards: ['kv_sku', 'kv_prop', 'kv_titulo_pt', 'kv_valor_unitario_produto', 'kv_custo_total_produto'],
        } }));
    });
    const json = (route, body, status = 200, headers = {}) => route.fulfill({
        status, contentType: 'application/json', body: JSON.stringify(body), headers,
    }).catch(() => {});
    await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        const pathname = url.pathname;
        if (pathname.endsWith('.html')) return route.fulfill({ status: 200, contentType: 'text/html',
            body: fs.readFileSync(path.join(root, 'static', pathname.slice(1))) });
        if (pathname === '/auth.js') return route.fulfill({ status: 200, contentType: 'application/javascript',
            body: 'window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({Authorization:"Bearer synthetic"});' });
        if (pathname.startsWith('/medias_compras/')) return route.fulfill({ status: 200, contentType: 'application/javascript',
            body: fs.readFileSync(path.join(root, 'static', pathname.slice(1))) });
        if (pathname === '/api/cadastro/produtos' || pathname === '/api/cadastro/produto') {
            state.global++;
            return json(route, { custo: 777 }, 500);
        }
        const storeProducts = pathname.match(/^\/api\/cadastro\/lojas\/(store-[AB])\/produtos(?:\/(.+))?$/);
        if (storeProducts) {
            const storeId = storeProducts[1];
            const sku = storeProducts[2] && decodeURIComponent(storeProducts[2]);
            const costs = url.searchParams.get('view') === 'costs';
            const revisionOnly = url.searchParams.get('somente_revisao') === 'true';
            state.requests.push({ storeId, sku, costs, revisionOnly });
            if (options.holdCostA && storeId === 'store-A') await state.costA.promise;
            if (options.failCost) return json(route, { detail: 'Cadastro indisponível.' }, 503);
            if (costs && revisionOnly) return json(route, { store_id: options.wrongStore ? 'store-X' : storeId,
                revisao: 'custos-' + storeId });
            const rows = products.map(product => ({ ...product, store_id: storeId, nome: 'Cadastro atual',
                'm3 individual': 0.1, ...(storeId === 'store-B' ? { custo: 25 } : {}) }));
            if (sku) {
                const product = rows.find(row => row.sku === sku) || { sku, store_id: storeId };
                if (options.wrongStore) product.store_id = 'store-X';
                if (options.cubagemAlias) { product.sku = '001'; product.cubagem_sku = sku; product.custo = 654; }
                return json(route, { produto: product });
            }
            return json(route, [
                { store_id: 'store-X', sku: '001', custo: 9000 },
                { store_id: 'store-X', sku: 'OUTRA-LOJA', custo: 9000 },
                ...rows.filter(row => !options.cubagemAlias || row.sku !== '1'),
            ], 200, costs ? { 'X-JK-Custos-Revisao': 'custos-' + storeId } : {});
        }
        const order = pathname.match(/^\/api\/medias-compras\/listas-pedidos\/([AB])$/);
        if (order) return json(route, { lista: list(order[1]) });
        if (pathname === '/api/impostos/produtos') {
            if (options.holdFiscal) await state.fiscal.promise;
            return json(route, { produtos: [] });
        }
        if (pathname.endsWith('/analise-concorrentes')) return json(route, { analise_concorrentes: { margens: {} } });
        if (pathname === '/api/medias-compras/concorrentes-links') return json(route, { concorrentes: {} });
        if (url.hostname === 'economia.awesomeapi.com.br') return json(route, { USDBRL: { bid: '5' } });
        if (/^\/api\/medias-compras\/listas-pedidos\/[^/]+\/skus\/[^/]+$/.test(pathname)) {
            return json(route, { detail: 'Not Found' }, 404);
        }
        if (pathname.startsWith('/api/')) return json(route, {});
        return route.fulfill({ status: 200, contentType: 'application/javascript', body: '' });
    });
    return { context, page, state };
}

async function expectText(locator, expected) {
    await locator.waitFor();
    await locator.page().waitForFunction(({ selector, expected }) => {
        const element = document.querySelector(selector);
        return element && element.textContent.replace(/\s+/g, ' ').trim() === expected;
    }, { selector: await locator.evaluate(element => {
        element.dataset.costTestId = 'target';
        return '[data-cost-test-id="target"]';
    }), expected });
    assert.strictEqual(normalize(await locator.textContent()), expected);
    await locator.evaluate(element => { delete element.dataset.costTestId; });
}

(async () => {
    const browser = await chromium.launch({ headless: true,
        ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
            ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : { channel: 'chrome' }) });
    try {
        const table = await environment(browser);
        await table.page.goto(origin + '/importacoes_lista.html?lista_id=A');
        for (const [sku, expected] of [
            ['001', 'R$ 1.234,56'], ['1', 'R$ 42,75'], ['ZERO', 'R$ 0,00'],
            ['VAZIO', 'Não cadastrado'], ['INVALIDO', 'Não cadastrado'], ['NEGATIVO', 'Não cadastrado'],
            ['AUSENTE', 'Não cadastrado'], ['OUTRA-LOJA', 'Não cadastrado'],
        ]) await expectText(table.page.locator('tr[data-sku="' + sku + '"] [data-col-key="ultimo_custo_cadastrado"]'), expected);
        assert.strictEqual(await table.page.locator('thead [data-col-key="valor_unit_brl"]').evaluate(element =>
            element.nextElementSibling.dataset.colKey), 'ultimo_custo_cadastrado', 'Nova coluna migra junto ao valor unitário em ordens antigas.');
        assert.strictEqual(table.state.requests.filter(row => row.costs && !row.revisionOnly).length, 1,
            'Custos são consultados sem fotos mesmo quando todos os itens já têm cubagem.');
        assert.strictEqual(table.state.global, 0);
        assert.deepStrictEqual(table.state.errors, []);
        if (previewDir) await table.page.locator('.table-wrap').screenshot({ path: path.join(previewDir, 'ultimo-custo-lista.png') });
        await table.context.close();

        for (const [sku, expected] of [
            ['001', 'R$ 1.234,56'], ['1', 'R$ 42,75'], ['ZERO', 'R$ 0,00'],
            ['VAZIO', 'Não cadastrado'], ['INVALIDO', 'Não cadastrado'], ['NEGATIVO', 'Não cadastrado'],
        ]) {
            const skuPage = await environment(browser, { holdFiscal: true });
            await skuPage.page.goto(origin + '/importacoes_sku.html?lista_id=A&sku=' + sku);
            await expectText(skuPage.page.locator('#vUltimoCustoCadastrado'), expected);
            assert.strictEqual(await skuPage.page.locator('[data-card-id="kv_valor_unitario_produto"]').evaluate(element =>
                element.nextElementSibling.dataset.cardId), 'kv_ultimo_custo_cadastrado');
            assert.strictEqual(skuPage.state.global, 0);
            assert.deepStrictEqual(skuPage.state.errors, [], 'O custo aparece enquanto a consulta fiscal ainda aguarda.');
            if (previewDir && sku === '1') await skuPage.page.locator('.layout > .card').first()
                .screenshot({ path: path.join(previewDir, 'ultimo-custo-sku.png') });
            skuPage.state.fiscal.resolve();
            await skuPage.context.close();
        }

        for (const options of [{ failCost: true }, { wrongStore: true }, { cubagemAlias: true }]) {
            const skuPage = await environment(browser, options);
            await skuPage.page.goto(origin + '/importacoes_sku.html?lista_id=A&sku=1');
            await expectText(skuPage.page.locator('#vUltimoCustoCadastrado'), options.cubagemAlias ? 'Não cadastrado' : 'Indisponível');
            assert.deepStrictEqual(skuPage.state.errors, []);
            await skuPage.context.close();
        }

        const failure = await environment(browser, { failCost: true });
        await failure.page.goto(origin + '/importacoes_lista.html?lista_id=A');
        await expectText(failure.page.locator('tr[data-sku="001"] [data-col-key="ultimo_custo_cadastrado"]'), 'Indisponível');
        assert.deepStrictEqual(failure.state.errors, []);
        await failure.context.close();

        for (const isSku of [false, true]) {
            const switching = await environment(browser, { holdCostA: true });
            const screen = isSku ? 'importacoes_sku.html' : 'importacoes_lista.html';
            const suffix = isSku ? '&sku=001' : '';
            await switching.page.goto(origin + '/' + screen + '?lista_id=A' + suffix);
            await switching.page.waitForFunction(() => document.querySelector('#vSku')?.textContent === '001'
                || !!document.querySelector('tr[data-sku="001"]'));
            await switching.page.evaluate(async ({ screen, suffix, isSku }) => {
                history.replaceState({}, '', '/' + screen + '?lista_id=B' + suffix);
                if (isSku) void carregarDetalheSku();
                else void carregarDetalheLista();
            }, { screen, suffix, isSku });
            const cost = switching.page.locator(isSku ? '#vUltimoCustoCadastrado'
                : 'tr[data-sku="001"] [data-col-key="ultimo_custo_cadastrado"]');
            await expectText(cost, 'R$ 25,00');
            switching.state.costA.resolve();
            await switching.page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
            assert.strictEqual(normalize(await cost.textContent()), 'R$ 25,00', 'Resposta antiga da loja A não sobrescreve loja B.');
            assert.deepStrictEqual(switching.state.errors, []);
            await switching.context.close();
        }
        console.log('Último custo cadastrado: lista/SKU, fonte atual por loja e SKU, zero, ausência, falha, ordens salvas, fiscal pendente e troca de lista: OK');
    } finally { await browser.close(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
