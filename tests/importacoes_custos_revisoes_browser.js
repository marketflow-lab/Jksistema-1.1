'use strict';

// Real pages + IndexedDB. Every HTTP response and identity is synthetic.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..', 'static');
const origin = 'http://127.0.0.1:8391';
const api = '/api/medias-compras/listas-pedidos/A';
const rev = letter => letter.repeat(64);
const norm = value => String(value).replace(/\s+/g, ' ').trim();
const gate = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

async function environment(browser, segundaLinha = false) {
    const context = await browser.newContext();
    const state = { errors: [], requests: [], costsVersion: 'c1', cost: 80, baseVersion: rev('a'),
        baseDownloads: 0, costDownloads: 0, patches: [], hold: null, clockOffset: 0 };
    const item = { SKU: '47', Quantidade: 150, 'Valor unidade': 3, 'Valor total': 450,
        M3: .0491693877, 'M3 individual': .0491693877 / 150, 'Cubagem status': 'historico',
        II: 18, IPI: 0, PIS: 3.12, COFINS: 14.37, 'ST Minas': 'Não', 'ST Paraíba': 'Não',
        analise_concorrentes: { custo_unitario: null, margens: {} } };
    const lista = { id: 'A', store_id: 'store-A', loja: 'Loja teste', nome_lista: 'C59 isolada',
        status: 'Recebido', exchange_rate: 5.4, 'Valor por M³': 300, total_usd: 450,
        itens: [item] };
    if (segundaLinha) lista.itens.push({ ...item, SKU: '385-4', Quantidade: 30, 'Valor unidade': 3.9,
        'Valor total': 117, M3: .01938, 'M3 individual': .01938 / 30, IPI: 9.75, PIS: 2.1, COFINS: 10.25 });
    const fiscal = { convenio_mg_st_minas: 'Não', convenio_mg_st_paraiba: 'Não', convenio_mg_mva: '' };
    const base = () => ({ success: true, tipo: 'importacoes_base_custos', versao: 1,
        lista: { id: 'A', store_id: 'store-A', loja: lista.loja, exchange_rate: lista.exchange_rate, 'Valor por M³': 300 },
        revisao: state.baseVersion, revisoes_sku: Object.fromEntries(lista.itens.map(row => [row.SKU, state.baseVersion])),
        itens: lista.itens.map(row => ({ ...row, fiscal: row.SKU === '47' ? fiscal : {
            convenio_mg_st_minas: 'Sim', convenio_mg_st_paraiba: 'Sim', convenio_mg_mva: '71,78%' }, fiscal_pronto: true })),
        rateio: { total_usd: 450, total_m3: item.M3, m3_completo: true, valor_por_m3: 300 } });
    const fontes = () => ({ success: true, fontes_versao: 3, consultado_em_ms: Date.now(),
        anuncios_loja_fonte: 'loja_lista_sku', anuncios_loja_store_id: 'store-A',
        anuncios_loja_seller_id: 'seller-A', anuncios_loja_site_id: 'MLB',
        concorrentes: { concorrente_1: 'https://produto.mercadolivre.com.br/MLB-1234567890' },
        concorrentes_valores: { concorrente_1: 85.89 },
        concorrentes_mlb: { concorrente_1: 'MLB9876543210' }, concorrentes_preco: {},
        anuncios_loja_status: { concorrente_1: 'paused' } });
    await context.addInitScript(() => {
        if (location.origin !== 'http://127.0.0.1:8391') return;
        localStorage.setItem('user_data', JSON.stringify({ client_id: 'synthetic', username: 'tester' }));
        localStorage.setItem('permissions', JSON.stringify({ importacoes: true, medias_compras: true }));
        localStorage.setItem('access_token', 'synthetic');
        localStorage.setItem('importacoes_sku_proporcao_v1', JSON.stringify({ A: { '47': '50', '385-4': '50' } }));
    });
    context.on('page', page => page.on('pageerror', error => state.errors.push(error.message)));
    const json = (route, value, headers = {}, status = 200) => route.fulfill({ status,
        contentType: 'application/json', headers, body: JSON.stringify(value) }).catch(() => {});
    await context.route('**/*', async route => {
        const req = route.request(), url = new URL(req.url()), p = url.pathname;
        if (url.origin !== origin) return route.fulfill({ status: 200, body: '' }).catch(() => {});
        if (p === '/auth.js') return route.fulfill({ contentType: 'application/javascript',
            body: 'window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({Authorization:"Bearer synthetic"});' });
        if (p.endsWith('.html') || p.endsWith('.js')) {
            const file = path.join(root, p.slice(1));
            if (fs.existsSync(file)) return route.fulfill({ contentType: p.endsWith('.html') ? 'text/html' : 'application/javascript', body: fs.readFileSync(file) });
        }
        if (!p.startsWith('/api/')) return route.fulfill({ status: 200, body: '' }).catch(() => {});
        state.requests.push(p + url.search);
        if (state.hold) await state.hold.promise;
        if (p === api + '/base-custos') {
            if (url.searchParams.has('somente_revisao')) {
                const { itens, rateio, ...metadata } = base(); return json(route, metadata);
            }
            state.baseDownloads++; return json(route, base());
        }
        if (p === '/api/cadastro/lojas/store-A/produtos' && url.searchParams.get('view') === 'costs') {
            if (url.searchParams.has('somente_revisao')) return json(route, { store_id: 'store-A', revisao: state.costsVersion });
            state.costDownloads++;
            return json(route, [{ store_id: 'store-A', sku: '47', custo: state.cost }], { 'X-JK-Custos-Revisao': state.costsVersion });
        }
        const detalhe = p.match(/\/skus\/([^/]+)$/);
        if (detalhe) {
            const row = base().itens.find(row => row.SKU === detalhe[1]);
            return json(route, { lista: base().lista, item: row,
                cadastro: { sku: row.SKU, store_id: 'store-A', custo: state.cost }, fiscal: row.fiscal, fiscal_pronto: true,
                rateio: base().rateio, revisao: state.baseVersion, revisao_fontes_custo: state.baseVersion,
                anuncios_identidade: { seller_id: 'seller-A', site_id: 'MLB', fontes_versao: 3 } });
        }
        if (p === '/api/medias-compras/concorrentes-links') return json(route, fontes());
        const analise = p.match(/\/skus\/([^/]+)\/analise-concorrentes$/);
        if (analise) {
            const payload = req.postDataJSON(); state.patches.push(payload);
            if (segundaLinha && state.patches.length === 1) {
                // The following SKU still runs after the former global 120-second deadline.
                await context.pages()[0].evaluate(() => { Date.now = ((now) => () => now() + 121000)(Date.now); });
            }
            assert.equal(payload.fonte_custo, 'simulacao_importacao_sku');
            assert.equal(payload.revisao_fontes_custo, state.baseVersion);
            assert.match(payload.assinatura_calculo, /^[a-f0-9]{64}$/);
            const row = lista.itens.find(row => row.SKU === analise[1]);
            const dados = { preco_venda: 85.89, custo_unitario: payload.custo_unitario, fonte_custo: payload.fonte_custo,
                margem_percentual: row.SKU === '47' ? 30.97 : 41.04,
                item_id_loja: 'MLB9876543210', store_id: 'store-A', seller_id: 'seller-A', site_id: 'MLB', financeiro_exato: true };
            row.analise_simulacao_importacao_sku = { ...payload, store_id: 'store-A',
                margens: { concorrente_1: dados }, margens_anuncios_loja: {} };
            return json(route, { analise_concorrentes: row.analise_simulacao_importacao_sku });
        }
        if (p === api) return json(route, { lista });
        if (p.endsWith('/landed-cost')) return json(route, { analysis: {} });
        if (p === '/api/vendas/grafico') return json(route, { valores_vendas: [], valores_devolucoes: [] });
        if (p.endsWith('/preferencias-colunas')) return json(route, {});
        if (p.startsWith('/api/cadastro/')) return json(route, []);
        return json(route, {});
    });
    return { context, state, lista, item };
}

const unit = page => page.locator('tr[data-sku="47"] [data-col-key="valor_unit_brl"]');
const cost = page => page.locator('tr[data-sku="47"] [data-col-key="ultimo_custo_cadastrado"]');
async function waitText(locator, expected) {
    await locator.page().waitForFunction(({ selector, expected }) =>
        document.querySelector(selector)?.textContent.replace(/\s+/g, ' ').trim() === expected,
    { selector: await locator.evaluate(el => { el.dataset.check = 'target'; return '[data-check="target"]'; }), expected });
    await locator.evaluate(el => delete el.dataset.check);
}

(async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const env = await environment(browser), { context, state } = env;
        const page = await context.newPage();
        page.setDefaultTimeout(10000);
        await page.goto(origin + '/importacoes_lista.html?lista_id=A');
        await unit(page).waitFor();
        await waitText(unit(page), 'R$ 23,75');
        await waitText(cost(page), 'R$ 80,00');
        await page.waitForFunction(() => document.querySelector('[data-coluna="margens_concorrentes"]')?.textContent.includes('30,97%'));
        assert.equal(state.patches.length, 1);
        assert.equal(state.baseDownloads, 1); assert.equal(state.costDownloads, 1);
        const downloaded = [state.baseDownloads, state.costDownloads];
        const firstSignature = state.patches[0].assinatura_calculo;
        // More than 120 s and sales invalidation cannot erase either independent cost cache.
        await page.evaluate(async () => {
            Date.now = ((now) => () => now() + 240000)(Date.now);
            window.dispatchEvent(new CustomEvent('jk:vendas-sync-finished'));
        });
        await page.waitForTimeout(100);
        state.hold = gate();
        await page.reload();
        await unit(page).waitFor();
        await waitText(unit(page), 'R$ 23,75');
        await waitText(cost(page), 'R$ 80,00');
        const held = state.hold; state.hold = null; held.resolve();
        await page.waitForFunction(() => document.getElementById('cacheListaStatus').textContent.includes('atualizados'));
        await page.waitForTimeout(200);
        assert.deepEqual([state.baseDownloads, state.costDownloads], downloaded, 'Unchanged revisions reuse saved payloads.');
        assert.equal(state.patches.length, 1, 'Same cost and sources reuse saved margins.');
        // A change from another machine is detected without an application event.
        state.cost = 91; state.costsVersion = 'c2';
        await page.evaluate(() => window.dispatchEvent(new Event('focus')));
        await waitText(cost(page), 'R$ 91,00');
        assert.equal(state.costDownloads, 2); assert.equal(state.baseDownloads, 1);
        // Local proportion affects import cost and its margins, not Cadastro.
        await page.locator('tr[data-sku="47"] input.proporcao-sku-input').fill('100');
        await page.locator('tr[data-sku="47"] input.proporcao-sku-input').press('Tab');
        await page.waitForFunction(() => document.querySelector('tr[data-sku="47"] [data-col-key="valor_unit_brl"]').textContent !== 'R$ 23,75');
        await page.waitForFunction(() => window.__nothing === undefined && document.querySelector('[data-coluna="margens_concorrentes"]')?.textContent.includes('30,97%'));
        await page.waitForTimeout(300);
        assert.ok(state.patches.length >= 2);
        assert.notEqual(state.patches.at(-1).assinatura_calculo, firstSignature);
        assert.equal(state.costDownloads, 2);
        assert.ok(!state.requests.includes('/api/impostos/produtos'), 'No global fiscal catalog request.');
        assert.deepEqual(state.errors, []);
        await context.close();
        // Numerical inputs copied from C59 into an isolated HTTP fixture; no live API or tenant data is mutated.
        const c59 = await environment(browser, true);
        const listaPage = await c59.context.newPage();
        await listaPage.goto(origin + '/importacoes_lista.html?lista_id=A');
        for (const [sku, expected] of [['47', 'R$ 23,75'], ['385-4', 'R$ 36,15']]) {
            const row = listaPage.locator('tr[data-sku="' + sku + '"]');
            await row.waitFor();
            await waitText(row.locator('[data-col-key="valor_unit_brl"]'), expected);
            await listaPage.waitForFunction(sku => document.querySelector('tr[data-sku="' + sku + '"] [data-coluna="margens_concorrentes"]')?.textContent.includes('%'), sku);
            const margemLista = (await row.locator('.competitor-margin').first().innerText()).replace(/\s+/g, '');
            const skuPage = await c59.context.newPage();
            await skuPage.goto(origin + '/importacoes_sku.html?lista_id=A&sku=' + sku);
            await skuPage.locator('#vValorUnitarioProduto').waitFor();
            await waitText(skuPage.locator('#vValorUnitarioProduto'), expected);
            await skuPage.waitForFunction(sku => document.getElementById('vMargemConcorrente1')?.textContent.includes(sku === '47' ? '30,97%' : '41,04%'), sku, { timeout: 10000 });
            assert.ok(margemLista.includes(sku === '47' ? '30,97%' : '41,04%'));
            await skuPage.close();
        }
        assert.deepEqual(c59.state.errors, []);
        await c59.context.close();
        console.log('Importações: cache por revisão, C59 isolada SKUs47/385-4 com custo e margem iguais na lista/detalhe: OK');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
