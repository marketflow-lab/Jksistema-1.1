'use strict';

const assert = require('assert');
const path = require('path');
const { chromium } = require('playwright');
const { fixture, clean } = require('./helpers/medias_autosalvamento_fixture');

async function setup(browser) {
    const test = await fixture(browser, { rows: 1 });
    await test.page.evaluate(async () => {
        const base = window.__autosaveFixture;
        const clone = value => JSON.parse(JSON.stringify(value));
        base.lists['list-c'] = clone(base.lists['list-a']);
        for (const [index, id] of ['list-a', 'list-b', 'list-c'].entries()) {
            const lista = base.lists[id];
            Object.assign(lista, { id, nome_lista: 'Pedido ' + id.slice(-1).toUpperCase(), store_id: 'store-a', loja: 'Loja A', currency: 'USD', pode_unir: true });
            Object.assign(lista.itens[0], { SKU: 'SKU-IGUAL', Quantidade: [10, 15, 5][index], 'Valor unidade': index === 1 ? 4 : 2.5 });
        }
        const previousFetch = window.fetch;
        const control = window.__unionFixture = { calls: [], operations: {}, created: 0, failure: '', previews: 0 };
        window.fetch = async (input, options = {}) => {
            const url = new URL(String(input), location.href);
            if (!url.pathname.startsWith('/api/medias-compras/listas-pedidos/uniao')) return previousFetch(input, options);
            const headers = new Headers(options.headers);
            if (headers.get('authorization') !== 'Bearer synthetic-token' || headers.get('x-client-id') !== 'tenant-autosave') throw new Error('Union session scope missing');
            if (options.method !== 'POST') throw new Error('Union must POST');
            const payload = JSON.parse(options.body);
            control.calls.push({ url: url.pathname, payload: clone(payload) });
            const response = (body, status = 200) => ({ ok: status < 400, status, json: async () => clone(body) });
            if (url.pathname.endsWith('/previa')) {
                control.previews++;
                const sources = payload.lista_ids.map(id => base.lists[id]);
                return response({ success: true, previa: { token: 'snapshot-' + control.previews,
                    listas: sources.map(row => ({ id: row.id, nome_lista: row.nome_lista })), loja: 'Loja A', store_id: 'store-a', supplier: 'Supplier A', currency: 'USD',
                    total_listas: sources.length, total_skus: 1, skus_repetidos: 1, total_quantidade: sources.reduce((sum, row) => sum + row.itens[0].Quantidade, 0),
                    itens: [{ sku: 'SKU-IGUAL', descricao: 'Produto sintético', quantidade: sources.reduce((sum, row) => sum + row.itens[0].Quantidade, 0), valor_unitario: 2.5, campos: {} }],
                    conflitos: [{ id: 'price', sku: 'SKU-IGUAL', campo: 'Valor unidade', rotulo: 'Valor unitário', opcoes: [
                        { id: 'price-a', valor: 2.5, listas: [{ id: 'list-a', nome_lista: 'Pedido A' }] },
                        { id: 'price-b', valor: 4, listas: [{ id: 'list-b', nome_lista: 'Pedido B' }] },
                    ] }],
                } });
            }
            if (control.failure === 'stale') { control.failure = ''; return response({ detail: 'Uma lista mudou desde a prévia.' }, 409); }
            if (!control.operations[payload.operacao_id]) {
                if (payload.escolhas.price !== 'price-b') throw new Error('Explicit conflict choice missing');
                const lista = { ...clone(base.lists['list-a']), id: 'merged-' + (++control.created), nome_lista: payload.nome_lista,
                    status: 'Lista gerada', uniao_origens: payload.lista_ids.map(id => ({ id, nome_lista: base.lists[id].nome_lista })) };
                lista.itens[0].Quantidade = payload.lista_ids.reduce((sum, id) => sum + base.lists[id].itens[0].Quantidade, 0);
                lista.itens[0]['Valor unidade'] = 4;
                lista.itens[0]['Valor total'] = lista.itens[0].Quantidade * 4;
                base.lists[lista.id] = lista;
                for (const id of payload.lista_ids) Object.assign(base.lists[id], { unida_em_lista_id: lista.id, unida_em_lista_nome: lista.nome_lista, pode_unir: false });
                control.operations[payload.operacao_id] = { success: true, lista, origens: payload.lista_ids };
            }
            if (control.failure === 'network-after-commit') { control.failure = ''; throw new TypeError('Conexão interrompida'); }
            if (control.failure === 'timeout-after-commit') { control.failure = ''; await new Promise(() => {}); }
            return response(control.operations[payload.operacao_id]);
        };
        await carregarListasPedidos(true);
    });
    return test;
}

const selector = (page, id) => page.locator('.uniao-lista-checkbox[data-lista-id="' + id + '"]');
const modal = page => page.locator('#modalUniaoListas');
const confirm = page => modal(page).locator('[data-uniao="confirmar"]');

async function preview(page, ids = ['list-a', 'list-b']) {
    for (const id of ids) await selector(page, id).check();
    await page.locator('#btnUnirListasPedido').click();
    await modal(page).locator('select[data-conflito-id="price"]').waitFor();
}

async function threeListsAndLostResponse(browser) {
    const test = await setup(browser); const { page } = test;
    try {
        assert(await page.locator('#btnUnirListasPedido').isDisabled());
        await selector(page, 'list-a').check();
        assert(await page.locator('#btnUnirListasPedido').isDisabled());
        await selector(page, 'list-b').check();
        assert(await page.locator('#btnUnirListasPedido').isEnabled());
        assert.strictEqual(await page.locator('#nomeListaPedidoEdit').inputValue(), 'Pedido A', 'Selection must not open another list.');
        await selector(page, 'list-c').check();
        await page.locator('#btnUnirListasPedido').click();
        await modal(page).locator('select[data-conflito-id="price"]').waitFor();
        assert.match(await modal(page).locator('.uniao-resumo').textContent(), /3 listas.*1 SKU.*30 unidades/);
        assert(await confirm(page).isDisabled(), 'Conflicts require an explicit choice.');
        await modal(page).locator('select').selectOption('price-b');
        await page.locator('#nomeUniaoListas').fill('Pedido consolidado');
        assert.match(await modal(page).locator('.uniao-total').textContent(), /120/);
        if (process.argv.includes('--preview')) await page.screenshot({ path: path.resolve(__dirname, '..', '..', 'previa-unir-listas.png') });
        await page.evaluate(() => { __unionFixture.failure = 'network-after-commit'; });
        await confirm(page).click();
        await modal(page).getByRole('status').filter({ hasText: 'Conexão interrompida' }).waitFor();
        assert(await page.locator('#nomeUniaoListas').isDisabled(), 'Ambiguous confirmation freezes the name for retry.');
        assert(await modal(page).locator('select').isDisabled());
        assert(await modal(page).locator('[data-uniao="atualizar"]').isDisabled());
        await confirm(page).click();
        await modal(page).waitFor({ state: 'detached' });
        await page.waitForFunction(() => listaPedidoAtual?.nome_lista === 'Pedido consolidado');
        const result = await page.evaluate(() => ({
            created: __unionFixture.created,
            requests: __unionFixture.calls.filter(call => !call.url.endsWith('/previa')).map(call => call.payload),
            newList: __autosaveFixture.lists['merged-1'],
            originals: ['list-a', 'list-b', 'list-c'].map(id => __autosaveFixture.lists[id].itens[0].Quantidade),
        }));
        assert.strictEqual(result.created, 1, 'Retry after an ambiguous response must keep the operation ID.');
        assert.deepStrictEqual(result.requests[0], result.requests[1]);
        assert.strictEqual(result.newList.itens[0].Quantidade, 30);
        assert.strictEqual(result.newList.status, 'Lista gerada');
        assert.deepStrictEqual(result.originals, [10, 15, 5], 'Original rows stay unchanged.');
        const destination = page.locator('.lista-item').filter({ has: page.locator('.lista-item-titulo', { hasText: 'Pedido consolidado' }) });
        assert(await destination.locator('[data-action="excluir"]').isDisabled());
        const source = page.locator('.lista-item').filter({ has: page.locator('.lista-item-titulo', { hasText: 'Pedido A' }) });
        assert.strictEqual(await source.locator('.status-badge').getAttribute('role'), null);
        assert(await source.locator('[data-action="anexar"]').isDisabled());
        assert(await source.locator('[data-action="excluir"]').isDisabled());
        await source.locator('.lista-item-titulo').click();
        await page.waitForFunction(() => listaPedidoAtual?.id === 'list-a' && !document.querySelector('#editorListaPedido').inert);
        assert.strictEqual(await page.locator('#editorListaPedido input:not(:disabled), #editorListaPedido select:not(:disabled)').count(), 0);
        assert(await page.locator('#btnSalvarListaPedido').isDisabled());
        assert(await page.locator('#btnAdicionarSkuPedido').isDisabled());
        const writes = await page.evaluate(() => __autosaveFixture.calls.filter(call => call.method !== 'GET').length);
        await page.evaluate(() => salvarListaPedidoAtual());
        assert.strictEqual(await page.evaluate(() => __autosaveFixture.calls.filter(call => call.method !== 'GET').length), writes);
        await page.locator('#editorListaPedido .uniao-link').click();
        await page.waitForFunction(() => listaPedidoAtual?.id === 'merged-1' && !document.querySelector('#editorListaPedido').inert);
        assert(await page.locator('#nomeListaPedidoEdit').isEnabled(), 'Opening the destination must restore editing.');
    } finally { await clean(test); }
}

async function stalePreviewRequiresReview(browser) {
    const test = await setup(browser); const { page } = test;
    try {
        await preview(page);
        await modal(page).locator('select').selectOption('price-b');
        await page.evaluate(() => { __unionFixture.failure = 'stale'; });
        await confirm(page).click();
        await modal(page).getByRole('status').filter({ hasText: 'Atualize a prévia' }).waitFor();
        assert(await confirm(page).isDisabled());
        await modal(page).locator('[data-uniao="atualizar"]').click();
        await modal(page).locator('select').waitFor();
        assert.strictEqual(await modal(page).locator('select').inputValue(), '', 'New preview requires fresh conflict review.');
        assert(await confirm(page).isDisabled());
        await modal(page).locator('select').selectOption('price-b');
        await confirm(page).click();
        await modal(page).waitFor({ state: 'detached' });
        const requests = await page.evaluate(() => __unionFixture.calls.filter(call => !call.url.endsWith('/previa')).map(call => call.payload));
        assert.notStrictEqual(requests[0].previa_token, requests[1].previa_token);
        assert.notStrictEqual(requests[0].operacao_id, requests[1].operacao_id);
    } finally { await clean(test); }
}

async function shippingEligibilityAndCancel(browser) {
    const test = await setup(browser); const { page } = test;
    try {
        await page.evaluate(() => {
            const row = listasPedidosResumo.find(lista => lista.id === 'list-c');
            row.pode_unir = false; row.motivo_bloqueio_uniao = 'Lista já possui itens aprovados.';
            renderListaPedidosResumo();
        });
        assert(await selector(page, 'list-c').isDisabled());
        assert.match(await selector(page, 'list-c').locator('..').getAttribute('title'), /itens aprovados/);
        await preview(page);
        await modal(page).locator('select').selectOption('price-b');
        await page.evaluate(() => localStorage.setItem('jk_embarques_importacoes', JSON.stringify([{ lista_ids: ['list-a'] }])));
        await confirm(page).click();
        await modal(page).getByRole('status').filter({ hasText: 'vinculada a um embarque' }).waitFor();
        assert.strictEqual(await page.evaluate(() => __unionFixture.calls.filter(call => !call.url.endsWith('/previa')).length), 0);
        await modal(page).locator('[data-uniao="cancelar"]').click();
        await modal(page).waitFor({ state: 'detached' });
        await page.evaluate(() => renderListaPedidosResumo());
        assert(await selector(page, 'list-a').isDisabled());
    } finally { await clean(test); }
}

async function timeoutRetainsOperation(browser) {
    const test = await setup(browser); const { page } = test;
    try {
        await preview(page);
        await modal(page).locator('select').selectOption('price-b');
        await page.evaluate(() => {
            __unionFixture.failure = 'timeout-after-commit';
            const original = window.setTimeout.bind(window);
            window.setTimeout = (callback, delay, ...args) => original(callback, delay === 30000 ? 50 : delay, ...args);
        });
        await confirm(page).click();
        await modal(page).getByRole('status').filter({ hasText: '30 segundos' }).waitFor();
        assert(await confirm(page).isEnabled());
        assert(await page.locator('#nomeUniaoListas').isDisabled());
        await confirm(page).click();
        await modal(page).waitFor({ state: 'detached' });
        const result = await page.evaluate(() => ({ created: __unionFixture.created,
            attempts: __unionFixture.calls.filter(call => !call.url.endsWith('/previa')).map(call => call.payload) }));
        assert.strictEqual(result.created, 1);
        assert.deepStrictEqual(result.attempts[0], result.attempts[1], 'Timeout retry must preserve the full request.');
    } finally { await clean(test); }
}

async function changedSessionCancelsSelection(browser) {
    const test = await setup(browser); const { page } = test;
    try {
        await preview(page);
        await modal(page).locator('select').selectOption('price-b');
        await page.evaluate(() => localStorage.setItem('access_token', 'another-session'));
        await confirm(page).click();
        await modal(page).waitFor({ state: 'detached' });
        assert.strictEqual(await page.evaluate(() => __unionFixture.calls.filter(call => !call.url.endsWith('/previa')).length), 0);
        await page.evaluate(() => renderListaPedidosResumo());
        assert.strictEqual(await page.locator('.uniao-lista-checkbox:checked').count(), 0);
        assert(await page.locator('#btnUnirListasPedido').isDisabled());
    } finally { await clean(test); }
}

(async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        await threeListsAndLostResponse(browser);
        await stalePreviewRequiresReview(browser);
        await shippingEligibilityAndCancel(browser);
        await timeoutRetainsOperation(browser);
        await changedSessionCancelsSelection(browser);
        console.log('PASS união: seleção de 2/3 listas, conflitos, timeout/retry idempotente, prévia obsoleta, origens preservadas/readonly e bloqueio de embarque.');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
