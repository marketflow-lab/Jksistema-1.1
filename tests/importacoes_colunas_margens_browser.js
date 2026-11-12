'use strict';

const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { environment, origin, gate, waitText, unit } = require('./importacoes_custos_revisoes_browser');

async function executar() {
    const browser = await chromium.launch({ headless: true });
    const env = await environment(browser);
    const { context, state, item } = env;
    const prefsGate = gate();
    let primeiraLeitura = true;
    let prefs = { ordem_colunas: [], larguras_colunas: {}, colunas_ocultas: [] };
    let saves = 0;
    const ownAd = { chave: 'concorrente_1', item_id: 'MLB9876543210', variation_id: '123',
        url: 'https://produto.mercadolivre.com.br/MLB-9876543210', preco: 90,
        status: 'active', sku: '47', seller_id: 'seller-A', site_id: 'MLB' };
    const json = (route, data) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
    await context.route('**/preferencias-colunas', async route => {
        if (route.request().method() === 'PUT') { prefs = route.request().postDataJSON(); saves++; return json(route, prefs); }
        const resposta = structuredClone(prefs);
        if (primeiraLeitura) { primeiraLeitura = false; await prefsGate.promise; }
        return json(route, resposta);
    });
    await context.route('**/concorrentes-links?*', route => json(route, {
        success: true, fontes_versao: 3, consultado_em_ms: Date.now(), anuncios_versao: 2,
        anuncios_loja_fonte: 'loja_lista_sku', anuncios_loja_store_id: 'store-A',
        anuncios_loja_seller_id: 'seller-A', anuncios_loja_site_id: 'MLB', anuncios_loja: [ownAd],
        concorrentes: { concorrente_1: 'https://produto.mercadolivre.com.br/MLB-1234567890' },
        concorrentes_valores: { concorrente_1: 85.89 }, concorrentes_mlb: { concorrente_1: ownAd.item_id },
        concorrentes_preco: { concorrente_1: 90 }, anuncios_loja_variacoes: { concorrente_1: '123' },
    }));
    await context.route('**/skus/47/analise-concorrentes', route => {
        const payload = route.request().postDataJSON();
        state.patches.push(payload);
        const margin = (preco, margem) => ({ fonte_custo: payload.fonte_custo, custo_unitario: payload.custo_unitario,
            preco_venda: preco, margem_percentual: margem, financeiro_exato: true,
            item_id_loja: ownAd.item_id, variation_id: '123', store_id: 'store-A', seller_id: 'seller-A', site_id: 'MLB',
            imposto_valor: 5, tarifa_ml: 11, frete_ml: 8, valor_liquido: 66 });
        item.analise_simulacao_importacao_sku = { ...payload, store_id: 'store-A', fontes_versao: 3,
            anuncios_versao: 2, anuncios_loja_store_id: 'store-A', anuncios_loja_seller_id: 'seller-A',
            anuncios_loja_site_id: 'MLB', anuncios_loja: [ownAd], anuncios_consultado_em_ms: Date.now(),
            atualizado_em: new Date().toISOString(), margens: { concorrente_1: margin(85.89, 30.97) },
            margens_anuncios_loja: { concorrente_1: margin(90, 35.25) } };
        return json(route, { analise_concorrentes: item.analise_simulacao_importacao_sku });
    });
    const cell = (page, key) => page.locator('tr[data-sku="47"] [data-col-key="' + key + '"]');
    const checkbox = (page, label) => page.getByRole('checkbox', { name: label, exact: true });
    const flush = page => page.evaluate(() => gravacaoPreferenciasColunas);
    try {
        const page = await context.newPage();
        await page.setViewportSize({ width: 1440, height: 1000 });
        await page.goto(origin + '/importacoes_lista.html?lista_id=A');
        await waitText(unit(page), 'R$ 23,75');
        await page.waitForFunction(() => document.querySelector('[data-coluna="margens_loja"]')?.textContent.includes('35,25%'));
        assert.match(await cell(page, 'margens_concorrentes').innerText(), /30,97%/);
        const own = cell(page, 'margens_loja').locator('.own-margin');
        assert.match(await own.innerText(), /A1\s*35,25%/);
        assert.match(await own.getAttribute('class'), /positive/);
        assert.match(await own.getAttribute('title'), /MLB9876543210.*Variação 123/);
        assert.equal(await cell(page, 'm3').innerText(), '0,0492');
        assert.equal(await page.locator('.totais-row [data-col-key="m3"]').innerText(), '0,0492');
        assert.equal(await page.evaluate(() => baseCustosLista.itens[0].M3), .0491693877, 'Exibição não arredonda os insumos.');
        const initialPatches = state.patches.length;
        await page.locator('#seletorColunas summary').click();
        await checkbox(page, 'Foto').uncheck();
        await checkbox(page, 'Anúncios da loja').uncheck();
        await flush(page);
        prefsGate.resolve();
        await page.waitForFunction(() => preferenciasColunasCarregadas);
        assert.equal(await cell(page, 'foto').isVisible(), false, 'Resposta antiga não desfaz a escolha local.');
        assert.equal(await cell(page, 'anuncios_loja').isVisible(), false);
        assert.equal(await own.isVisible(), true);
        assert.deepEqual(prefs.colunas_ocultas.sort(), ['anuncios_loja', 'foto']);
        assert.equal(state.patches.length, initialPatches, 'Escolher colunas não recalcula margens.');

        // Cabeçalhos, dados e totais seguem a mesma ordem e largura ao ocultar colunas.
        const positions = await page.evaluate(() => {
            const own = selector => [...document.querySelectorAll(selector)].filter(el => !el.hidden)
                .map(el => ({ key: el.dataset.colKey, x: el.getBoundingClientRect().x, width: el.getBoundingClientRect().width }));
            return [own('#tHead th'), own('tr[data-sku="47"] td'), own('.totais-row td')];
        });
        assert.deepEqual(positions[0], positions[1]);
        assert.deepEqual(positions[0], positions[2]);
        await page.reload();
        await waitText(unit(page), 'R$ 23,75');
        await page.waitForFunction(() => preferenciasColunasCarregadas);
        assert.equal(await cell(page, 'foto').isVisible(), false);
        assert.equal(await cell(page, 'anuncios_loja').isVisible(), false);
        assert.match(await cell(page, 'margens_loja').innerText(), /35,25%/);
        await page.locator('#seletorColunas summary').click();
        await checkbox(page, 'Nossa margem').uncheck();
        await flush(page);
        assert.equal(await cell(page, 'margens_loja').isVisible(), false);
        await checkbox(page, 'Nossa margem').check();
        await flush(page);
        assert.equal(await cell(page, 'margens_loja').isVisible(), true);
        assert.match(await cell(page, 'margens_loja').innerText(), /35,25%/);
        await page.getByRole('button', { name: 'Mostrar todas', exact: true }).click();
        await flush(page);
        assert.deepEqual(prefs.colunas_ocultas, []);
        assert.equal(await page.locator('#tHead th[hidden]').count(), 0);

        // Limite de uma coluna visível e recuperação sem perder ordem ou larguras.
        const labels = await page.locator('#opcoesColunas label').allTextContents();
        for (const label of labels.filter(label => label !== 'SKU')) await checkbox(page, label).uncheck();
        assert.equal(await checkbox(page, 'SKU').isDisabled(), true);
        await flush(page);
        await page.getByRole('button', { name: 'Mostrar todas', exact: true }).click();
        await flush(page);
        assert.equal(await checkbox(page, 'SKU').isDisabled(), false);
        assert.ok(saves > 0);
        if (process.env.JK_IMPORTACOES_COLUNAS_PREVIEW) {
            const show = ['SKU', 'Quantidade', 'M³', 'Valor Unitário R$', 'Nossa margem', 'Margem concorrentes'];
            for (const label of labels.filter(label => !show.includes(label))) await checkbox(page, label).uncheck();
            await flush(page);
            await page.locator('#seletorColunas summary').click();
            await page.locator('.table-wrap').screenshot({ path: process.env.JK_IMPORTACOES_COLUNAS_PREVIEW });
        }
        assert.deepEqual(state.errors, []);
        console.log('Importações: nossa margem, precisão M³, seleção persistente de colunas, resposta tardia, alinhamento e restauração: OK');
    } finally {
        prefsGate.resolve();
        await context.close();
        await browser.close();
    }
}

executar().catch(error => { console.error(error); process.exitCode = 1; });
