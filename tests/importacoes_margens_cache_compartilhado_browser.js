'use strict';

const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { environment, origin, gate, rev } = require('./importacoes_custos_revisoes_browser');

async function esperarMargem(page, valor) {
    await page.waitForFunction(valor => document.querySelector('tr[data-sku="47"] [data-coluna="margens_concorrentes"]')
        ?.textContent.includes(valor), valor);
}
async function esperarFila(page) {
    await page.waitForFunction(() => !filaMargensLista);
}

(async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const { context, state, item } = await environment(browser);
        await context.addInitScript(() => {
            if (location.origin !== 'http://127.0.0.1:8391') return;
            const agora = Date.now.bind(Date);
            Date.now = () => agora() + Number(localStorage.getItem('__relogio_margens') || 0);
        });
        const page = await context.newPage();
        await page.goto(origin + '/importacoes_lista.html?lista_id=A');
        await esperarMargem(page, '30,97%');
        await esperarFila(page);
        assert.equal(state.patches.length, 1);
        const primeira = state.patches[0];
        const fontesIniciais = state.sourceReads;
        const snapshotsAntes = state.requests.filter(url => url.includes('somente_salvos=true')).length;
        await page.evaluate(() => {
            for (let i = 0; i < 20; i++) window.dispatchEvent(new CustomEvent('jk:cache-persistente-invalidado', {
                detail: { tags: ['analises_importacao'], origem: 'storage', revogado: false },
            }));
        });
        await page.waitForTimeout(50);
        assert.equal(state.requests.filter(url => url.includes('somente_salvos=true')).length, snapshotsAntes,
            'Updates from other SKUs do not download the whole list again.');

        // Both screens use the same record. Opening the SKU does not repeat the financial PATCH.
        const sku = await context.newPage();
        await sku.goto(origin + '/importacoes_sku.html?lista_id=A&sku=47');
        await sku.waitForFunction(() => document.getElementById('vMargemConcorrente1')?.textContent.includes('30,97%'));
        await sku.waitForFunction(() => contextoDetalheSku?.consultaOfertasCompactas?.finalizada);
        assert.equal(state.patches.length, 1, 'Lista → SKU reuses the exact analysis.');
        await sku.close();
        await page.bringToFront();
        await page.reload();
        await esperarMargem(page, '30,97%');
        await esperarFila(page);
        assert.equal(state.patches.length, 1);
        assert.equal(state.sourceReads, fontesIniciais, 'Sources under60s are reused across screens.');

        // Refresh prices after61s while retaining the last compatible margin on the real page.
        state.clockOffset = 61000;
        await page.evaluate(offset => localStorage.setItem('__relogio_margens', String(offset)), state.clockOffset);
        state.holdFontes = gate();
        await page.reload();
        await esperarMargem(page, '30,97%');
        await page.waitForFunction(() => document.querySelector('[data-coluna="margens_concorrentes"]')?.textContent.includes('Atualização pendente'));
        assert.equal(state.patches.length, 1);
        const liberar = state.holdFontes; state.holdFontes = null; liberar.resolve();
        await esperarFila(page);
        assert.equal(state.patches.length, 1, 'Same inputs after61s do not recalculate the margin.');
        assert.equal(state.sourceReads, fontesIniciais + 1);

        // External fee/promotion validation runs in the background after15min; failures do not erase it.
        state.clockOffset = 901000;
        state.failFinance = true;
        const anterior = JSON.stringify(item.analise_simulacao_importacao_sku);
        await page.evaluate(offset => localStorage.setItem('__relogio_margens', String(offset)), state.clockOffset);
        const falhaAtualizacao = page.waitForResponse(response => response.url().endsWith('/analise-concorrentes')
            && response.request().method() === 'PATCH' && response.status() === 503);
        await page.reload();
        await esperarMargem(page, '30,97%');
        await falhaAtualizacao;
        await esperarFila(page);
        assert.equal(state.patches.length, 3, 'The transient error has two bounded attempts.');
        assert.equal(JSON.stringify(item.analise_simulacao_importacao_sku), anterior);
        await esperarMargem(page, '30,97%');
        assert.match(await page.locator('[data-coluna="margens_concorrentes"]').textContent(), /Atualização pendente/);

        // A confirmed price change produces a new analysis, while unchanged import cost stays cached.
        state.failFinance = false;
        state.marketPrice = 90;
        state.marketMargin = 42.5;
        const precoAtualizado = page.waitForResponse(response => response.url().endsWith('/analise-concorrentes')
            && response.request().method() === 'PATCH' && response.status() === 200);
        await page.reload();
        await precoAtualizado;
        await esperarMargem(page, '42,50%');
        await esperarFila(page);
        assert.equal(state.patches.length, 4);
        assert.equal(state.patches.at(-1).custo_unitario, primeira.custo_unitario);
        assert.equal(state.patches.at(-1).precos_concorrentes.concorrente_1, 90);

        // The source revision includes domestic tax: equal rounded unit cost cannot override this fence.
        state.baseVersion = rev('b');
        const revisaoAtualizada = page.waitForResponse(response => response.url().endsWith('/analise-concorrentes')
            && response.request().method() === 'PATCH'
            && response.request().postDataJSON().revisao_fontes_custo === rev('b'));
        await page.reload();
        await revisaoAtualizada;
        await esperarMargem(page, '42,50%');
        await esperarFila(page);
        assert.equal(state.patches.length, 5);
        assert.equal(state.patches.at(-1).assinatura_calculo, primeira.assinatura_calculo);
        assert.equal(state.patches.at(-1).revisao_fontes_custo, rev('b'));
        assert.deepEqual(state.errors, []);
        await context.close();
        console.log('Margens: cache compartilhado lista/SKU,61s sem PATCH, refresh15min, falha preservada, preço e revisão: OK');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
