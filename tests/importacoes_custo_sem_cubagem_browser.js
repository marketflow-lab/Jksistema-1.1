'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..', 'static');
const origin = 'http://127.0.0.1:8392';
const api = '/api/medias-compras/listas-pedidos/A';
const norm = value => String(value).replace(/\s+/g, ' ').trim();

async function executar() {
    const browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    const fiscal = { convenio_mg_st_minas: 'Não', convenio_mg_st_paraiba: 'Não', convenio_mg_mva: '' };
    const item = { Quantidade: 2, 'Valor unidade': 10, 'Valor total': 20,
        M3: '', 'M3 individual': '', 'Cubagem status': 'pendente',
        II: 10, IPI: 5, PIS: 2, COFINS: 8, 'ST Minas': 'Não', 'ST Paraíba': 'Não' };
    const lista = { id: 'A', nome_lista: 'Teste de custos sem cubagem', store_id: 'store-A',
        loja: 'Loja de teste', status: 'Analisando orçamento', exchange_rate: 5,
        itens: [
            { ...item, SKU: 'COM-FRETE', 'Frete Internacional': 3 },
            { ...item, SKU: 'SEM-FRETE' },
            { ...item, SKU: 'COM-CUBAGEM', M3: .4, 'M3 individual': .2,
                'Cubagem status': 'cadastro', 'Frete Internacional': 3 },
        ] };
    let revision = 'a';
    const base = () => {
        const completo = lista.itens.every(row => row['Cubagem status'] !== 'pendente');
        const totalM3 = lista.itens.reduce((sum, row) => sum + (Number(row.M3) || 0), 0);
        return { success: true, tipo: 'importacoes_base_custos', versao: 1,
            lista: { id: 'A', loja: lista.loja, store_id: 'store-A', exchange_rate: 5 },
            revisao: revision.repeat(64),
            revisoes_sku: Object.fromEntries(lista.itens.map(row => [row.SKU, revision.repeat(64)])),
            itens: lista.itens.map(row => ({ ...row, fiscal, fiscal_pronto: true })),
            rateio: { total_usd: 60, total_m3: totalM3, m3_completo: completo,
                valor_por_m3: completo ? 60 / totalM3 : null } };
    };
    context.setDefaultTimeout(10000);
    context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
    await context.addInitScript(() => {
        if (location.origin !== 'http://127.0.0.1:8392') return;
        localStorage.setItem('user_data', JSON.stringify({ client_id: 'synthetic', username: 'tester' }));
        localStorage.setItem('access_token', 'synthetic');
        localStorage.setItem('permissions', JSON.stringify({ importacoes: true, medias_compras: true }));
    });
    const json = (route, data, headers = {}) => route.fulfill({
        contentType: 'application/json', body: JSON.stringify(data), headers,
    });
    await context.route('**/*', async route => {
        const url = new URL(route.request().url()), p = url.pathname;
        if (url.origin !== origin) return route.fulfill({ status: 200, body: '' });
        if (p === '/auth.js') return route.fulfill({ contentType: 'application/javascript',
            body: 'window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({Authorization:"Bearer synthetic"});' });
        if (p.endsWith('.html') || p.endsWith('.js')) {
            const file = path.join(root, p.slice(1));
            if (fs.existsSync(file)) return route.fulfill({
                contentType: p.endsWith('.html') ? 'text/html' : 'application/javascript', body: fs.readFileSync(file),
            });
        }
        if (p === api + '/base-custos') {
            const data = base();
            if (url.searchParams.has('somente_revisao')) { delete data.itens; delete data.rateio; }
            return json(route, data);
        }
        const sku = p.match(/\/skus\/([^/]+)$/);
        if (sku) {
            const data = base(), row = data.itens.find(row => row.SKU === sku[1]);
            return json(route, { lista: data.lista, item: row, fiscal, fiscal_pronto: true,
                cadastro: { sku: row.SKU, store_id: 'store-A' }, rateio: data.rateio,
                revisao: data.revisao, revisao_fontes_custo: data.revisao });
        }
        if (p === api) return json(route, { lista });
        if (p === '/api/cadastro/lojas/store-A/produtos' && url.searchParams.get('view') === 'costs') {
            return url.searchParams.has('somente_revisao')
                ? json(route, { store_id: 'store-A', revisao: 'c1' })
                : json(route, [], { 'X-JK-Custos-Revisao': 'c1' });
        }
        if (p === '/api/vendas/grafico') return json(route, { valores_vendas: [], valores_devolucoes: [] });
        if (p.startsWith('/api/cadastro/')) return json(route, []);
        return json(route, {});
    });
    const unit = (page, sku) => page.locator('tr[data-sku="' + sku + '"] [data-col-key="valor_unit_brl"]');
    const waitCost = async (page, sku, value) => page.waitForFunction(({ sku, value }) =>
        document.querySelector('tr[data-sku="' + sku + '"] [data-col-key="valor_unit_brl"]')
            ?.textContent.replace(/\s+/g, ' ').trim() === value, { sku, value });
    try {
        const page = await context.newPage();
        await page.goto(origin + '/importacoes_lista.html?lista_id=A');
        for (const [sku, expected, freight] of [
            ['COM-FRETE', 'R$ 91,13', 'R$ 15,00'], ['SEM-FRETE', 'R$ 79,66', 'R$ 0,00'],
            ['COM-CUBAGEM', 'R$ 91,13', 'R$ 15,00'],
        ]) {
            await waitCost(page, sku, expected);
            const row = page.locator('tr[data-sku="' + sku + '"]');
            assert.equal(norm(await row.locator('[data-col-key="frete_internacional"]').innerText()), freight);
            assert.equal(norm(await unit(page, sku).innerText()), expected, 'Custo sem aviso adicional');
            if (sku !== 'COM-CUBAGEM') assert.equal(await row.locator('[data-col-key="m3"]').innerText(), 'Cubagem pendente');
            const detail = await context.newPage();
            await detail.goto(origin + '/importacoes_sku.html?lista_id=A&sku=' + sku);
            await detail.waitForFunction(expected => document.getElementById('vValorUnitarioProduto')
                ?.textContent.replace(/\s+/g, ' ').trim() === expected, expected);
            assert.equal(norm(await detail.locator('#vValorUnitarioProduto').innerText()), expected);
            assert.ok(norm(await detail.locator('#vFreteInternacional').innerText()).startsWith(freight));
            if (sku !== 'COM-CUBAGEM') assert.equal(await detail.locator('#vM3').innerText(), 'Cubagem pendente');
            await detail.close();
        }
        if (process.env.JK_IMPORTACOES_SCREENSHOT) await page.locator('#tbodyLista').screenshot({ path: process.env.JK_IMPORTACOES_SCREENSHOT });
        // A lista reaberta conserva o custo; cadastrar o frete muda a revisão e o resultado.
        await page.reload();
        await waitCost(page, 'SEM-FRETE', 'R$ 79,66');
        lista.itens[1]['Frete Internacional'] = 3;
        revision = 'b';
        await page.reload();
        await waitCost(page, 'SEM-FRETE', 'R$ 91,13');
        // Sem nenhum m³, todos continuam calculando pelo frete salvo.
        Object.assign(lista.itens[2], { M3: '', 'M3 individual': '', 'Cubagem status': 'pendente' });
        revision = 'c';
        await page.reload();
        for (const row of lista.itens) await waitCost(page, row.SKU, 'R$ 91,13');
        // Quando a cubagem fica completa, volta o rateio original para lista e detalhe.
        for (const row of lista.itens) Object.assign(row, { M3: .4, 'M3 individual': .2, 'Cubagem status': 'cadastro' });
        revision = 'd';
        await page.reload();
        await page.waitForFunction(() => document.querySelector('tr[data-sku="SEM-FRETE"] [data-col-key="frete_internacional"]')
            ?.textContent.replace(/\s+/g, ' ').trim() === 'R$ 100,00');
        const completeCost = norm(await unit(page, 'SEM-FRETE').innerText());
        assert.notEqual(completeCost, 'R$ 91,13');
        const detail = await context.newPage();
        await detail.goto(origin + '/importacoes_sku.html?lista_id=A&sku=SEM-FRETE');
        await detail.waitForFunction(expected => document.getElementById('vValorUnitarioProduto')
            ?.textContent.replace(/\s+/g, ' ').trim() === expected, completeCost);
        // A ausência de cubagem não dispensa preço, quantidade, câmbio ou alíquotas.
        assert.deepEqual(await page.evaluate(() => {
            const motor = JKImportacoesCustoSimulado;
            const entrada = { totalUsd: 20, freteUsd: 0, cotacao: 5, quantidade: 2,
                iiPct: 10, ipiPct: 5, pisPct: 2, cofinsPct: 8, proporcaoPct: 100 };
            return ['totalUsd', 'cotacao', 'quantidade', 'iiPct'].map(key => motor.calcular({ ...entrada, [key]: null }).custoUnitario);
        }), [null, null, null, null]);
        assert.deepEqual(errors, []);
        console.log('Custos sem cubagem: frete salvo/ausente, lista parcial/sem volumes, paridade, cache e recálculo após cadastro: OK');
    } finally {
        await context.close();
        await browser.close();
    }
}

executar().catch(error => { console.error(error); process.exitCode = 1; });
