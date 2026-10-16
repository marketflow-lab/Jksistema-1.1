"use strict";

const assert = require('assert');
const { chromium } = require('playwright');
const { fixture, openList, field, plan, calls, release, serverList, waitServer, clean } = require('./helpers/medias_autosalvamento_fixture');

async function allCalls(page) { return page.evaluate(() => __autosaveFixture.calls); }
function workCalls(items) { return items.filter(call => /^(sku:|products$|add:|save:|summaries$)/.test(call.key)); }
async function add(page, sku = '375-1') {
    await page.locator('#btnAdicionarSkuPedido').click();
    await page.locator('#addSkuInput').fill(sku);
    await page.locator('#addSkuQtdInput').fill('4');
    await page.locator('#addSkuValorInput').fill('3,50');
    await page.locator('#btnConfirmarAdicionarSku').click();
}
async function completed(page) {
    await page.waitForFunction(() => !document.querySelector('#modalAdicionarSkuPedido').classList.contains('open'));
}
async function caseWith(browser, execute) {
    const test = await fixture(browser);
    const before = (await allCalls(test.page)).length;
    try { await execute(test, before); }
    catch (error) {
        const state = await test.page.evaluate(() => ({
            modal: document.querySelector('#modalAdicionarSkuStatus')?.textContent,
            status: document.querySelector('#statusListaPedido')?.textContent,
            name: document.querySelector('#nomeListaPedidoEdit')?.value,
            values: ['addSkuInput', 'addSkuQtdInput', 'addSkuValorInput'].map(id => document.getElementById(id)?.value),
            calls: __autosaveFixture.calls.slice(-8).map(({ key, done, failed }) => ({ key, done, failed })),
        }));
        error.stack += '\nSynthetic fixture state: ' + JSON.stringify(state);
        throw error;
    } finally { await clean(test); }
}

async function modernUsesOnlyPost(browser) {
    await caseWith(browser, async ({ page }, before) => {
        await add(page);
        await completed(page);
        assert.deepStrictEqual(workCalls((await allCalls(page)).slice(before)).map(call => call.key), ['add:list-a'],
            'Modern addition must not pre-read the catalog or wait for all list summaries');
        const saved = await serverList(page), inserted = saved.itens.find(item => item.SKU === '375-1');
        assert.strictEqual(inserted.Quantidade, 4);
        assert.strictEqual(inserted['Valor unidade'], 3.5);
        assert.strictEqual(inserted['Valor total'], 14);
        assert.strictEqual(await page.evaluate(() => listasPedidosResumo.find(item => item.id === 'list-a').total_itens), saved.itens.length,
            'The existing card must reflect the POST response without another GET');
    });
}

async function modalCompletesBeforeSummaryResponse(browser) {
    await caseWith(browser, async ({ page }, before) => {
        await plan(page, 'summaries', { hold: true });
        await add(page);
        await waitServer(page, () => __autosaveFixture.lists['list-a'].itens.some(item => item.SKU === '375-1'));
        await completed(page);
        assert.strictEqual(workCalls((await allCalls(page)).slice(before)).filter(call => call.key === 'summaries').length, 0);
    });
}

async function canonicalSkuAndServerCubageRemainAuthoritative(browser) {
    await caseWith(browser, async ({ page }, before) => {
        await plan(page, 'add:list-a', { canonicalSku: 'CANON-375', item: { 'M3 individual': 0.125, 'Estimed CBM': '0.5', OEM: 'SERVER-OEM' } });
        await add(page, 'legacy-375');
        await completed(page);
        assert.strictEqual(await page.locator('[data-kind="sku"]').evaluateAll(nodes => nodes.filter(node => node.value === 'CANON-375').length), 1);
        const inserted = (await serverList(page)).itens.find(item => item.SKU === 'CANON-375');
        assert.strictEqual(inserted['Estimed CBM'], '0.5');
        await field(page, 'embalagem', 0).fill('Confirmed after addition');
        await field(page, 'embalagem', 0).press('Tab');
        await waitServer(page, () => __autosaveFixture.lists['list-a'].itens[0]['Individual packaging'] === 'Confirmed after addition');
        assert.deepStrictEqual((await serverList(page)).itens.find(item => item.SKU === 'CANON-375'), inserted,
            'A subsequent full autosave must retain the canonical server item and its volume');
        assert.strictEqual(workCalls((await allCalls(page)).slice(before)).filter(call => call.key.startsWith('sku:') || call.key === 'products').length, 0);
    });
}

async function missingEndpointUsesNarrowLegacyFallback(browser) {
    for (const status of [404, 405]) await caseWith(browser, async ({ page }, before) => {
        await plan(page, 'add:list-a', { status, detail: status === 404 ? 'Not Found' : 'Method Not Allowed' });
        await add(page);
        await completed(page);
        assert.deepStrictEqual(workCalls((await allCalls(page)).slice(before)).map(call => call.key), ['add:list-a', 'sku:375-1', 'save:list-a'],
            'Only an unavailable endpoint may trigger the legacy lookup and full PUT');
        assert.strictEqual((await serverList(page)).itens.filter(item => item.SKU === '375-1').length, 1);
    });
}

async function businessAndAccessErrorsNeverFallBack(browser) {
    for (const status of [401, 403, 404, 500]) await caseWith(browser, async ({ page }, before) => {
        await plan(page, 'add:list-a', { status, detail: 'Synthetic SKU/access failure ' + status });
        await add(page);
        await page.waitForFunction(status => document.querySelector('#modalAdicionarSkuStatus').textContent.includes('failure ' + status), status);
        assert.deepStrictEqual(workCalls((await allCalls(page)).slice(before)).map(call => call.key), ['add:list-a']);
        assert(!(await serverList(page)).itens.some(item => item.SKU === '375-1'));
        assert.strictEqual(await page.locator('#btnConfirmarAdicionarSku').isDisabled(), false);
    });
}

async function legacyLookupErrorsNeverDownloadWholeCatalog(browser) {
    for (const status of [401, 403, 404]) await caseWith(browser, async ({ page }, before) => {
        await plan(page, 'add:list-a', { status: 405 });
        await plan(page, 'sku:375-1', { status, detail: 'Synthetic lookup failure ' + status });
        await add(page);
        await page.waitForFunction(() => __autosaveFixture.calls.some(call => call.key === 'sku:375-1' && call.done));
        await page.waitForFunction(() => !document.querySelector('#btnConfirmarAdicionarSku').disabled);
        assert.deepStrictEqual(workCalls((await allCalls(page)).slice(before)).map(call => call.key), ['add:list-a', 'sku:375-1']);
        assert(!(await serverList(page)).itens.some(item => item.SKU === '375-1'));
    });
}

async function previousPutKeepsAdditionSerialized(browser) {
    await caseWith(browser, async ({ page }, before) => {
        await plan(page, 'save:list-a', { hold: true });
        await field(page, 'qtd').fill('75'); await field(page, 'qtd').press('Enter');
        await page.waitForFunction(() => __autosaveFixture.calls.some(call => call.key === 'save:list-a' && !call.done));
        const previous = (await calls(page, 'save:list-a'))[0];
        await add(page);
        await page.waitForTimeout(80);
        assert.strictEqual((await calls(page, 'add:list-a')).length, 0);
        assert.match(await page.locator('#modalAdicionarSkuStatus').innerText(), /aguardando.*salvamento/i,
            'Waiting in the write queue must have its own honest progress message');
        await release(page, previous.index);
        await completed(page);
        assert.deepStrictEqual(workCalls((await allCalls(page)).slice(before)).map(call => call.key), ['save:list-a', 'add:list-a']);
        assert.strictEqual((await serverList(page)).itens[0].Quantidade, 75);
    });
}

async function latePostCannotReplaceNewSelection(browser) {
    await caseWith(browser, async ({ page }) => {
        await plan(page, 'add:list-a', { hold: true });
        await add(page);
        await page.waitForFunction(() => __autosaveFixture.calls.some(call => call.key === 'add:list-a' && !call.done));
        const previous = (await calls(page, 'add:list-a'))[0];
        await page.locator('[data-jk-action="fechar-adicionar-sku"]').click();
        await openList(page, 'B');
        const currentNode = await page.locator('[data-kind="qtd"]').first().evaluate(node => { window.__currentAdditionNode = node; return node.value; });
        await release(page, previous.index);
        await page.waitForFunction(() => !document.querySelector('#btnConfirmarAdicionarSku').disabled);
        assert.strictEqual(await page.locator('#nomeListaPedidoEdit').inputValue(), 'Pedido B');
        assert.strictEqual(await page.locator('[data-kind="qtd"]').first().evaluate(node => node === window.__currentAdditionNode), true);
        assert.strictEqual(await page.locator('[data-kind="qtd"]').first().inputValue(), currentNode);
        assert(!(await serverList(page, 'list-b')).itens.some(item => item.SKU === '375-1'));
    });
}


async function pendingOldSummaryCannotReplaceSavedCard(browser) {
    await caseWith(browser, async ({ page }, before) => {
        const old = await serverList(page);
        await plan(page, 'summaries', { hold: true, body: { listas: [{ ...old, itens: [], total_itens: old.itens.length }] } });
        await page.evaluate(() => { void carregarListasPedidos(true); });
        await page.waitForFunction(() => __autosaveFixture.calls.some(call => call.key === 'summaries' && !call.done));
        const previous = (await calls(page, 'summaries')).find(call => !call.done);
        await add(page); await completed(page);
        assert.strictEqual(await page.evaluate(() => listasPedidosResumo.find(item => item.id === 'list-a').total_itens), old.itens.length + 1);
        await release(page, previous.index);
        assert.strictEqual(await page.evaluate(() => listasPedidosResumo.find(item => item.id === 'list-a').total_itens), old.itens.length + 1,
            'A pre-mutation GET cannot overwrite the summary derived from the completed POST');
        assert.strictEqual(workCalls((await allCalls(page)).slice(before)).filter(call => call.key === 'summaries').length, 1,
            'Only the deliberately pending old summary GET may exist');
    });
}

async function savedCardUpdatesWithoutConfirmingDraft(browser) {
    await caseWith(browser, async ({ page }, before) => {
        const old = await serverList(page);
        await plan(page, 'add:list-a', { hold: true });
        await add(page);
        await page.waitForFunction(() => __autosaveFixture.calls.some(call => call.key === 'add:list-a' && !call.done));
        const previous = (await calls(page, 'add:list-a'))[0];
        await page.locator('[data-jk-action="fechar-adicionar-sku"]').click();
        await page.locator('#nomeListaPedidoEdit').fill('Unconfirmed name');
        await page.locator('#nomeListaPedidoEdit').evaluate(node => { window.__additionDraftNode = node; });
        await release(page, previous.index);
        await page.waitForFunction(() => !document.querySelector('#btnConfirmarAdicionarSku').disabled);
        assert.strictEqual(await page.locator('#nomeListaPedidoEdit').inputValue(), 'Unconfirmed name');
        assert.strictEqual(await page.evaluate(() => document.activeElement === window.__additionDraftNode && window.__additionDraftNode.isConnected), true);
        const card = await page.evaluate(() => listasPedidosResumo.find(item => item.id === 'list-a'));
        assert.strictEqual(card.nome_lista, 'Pedido A', 'Summary uses persisted values, never an unconfirmed draft');
        assert.strictEqual(card.total_itens, old.itens.length + 1, 'The card must update even when the editor keeps a focused draft');
        assert.deepStrictEqual(workCalls((await allCalls(page)).slice(before)).map(call => call.key), ['add:list-a']);
        assert.strictEqual((await serverList(page)).nome_lista, 'Pedido A');
    });
}


async function permissionLossDuringLegacyLookupPreventsPut(browser) {
    await caseWith(browser, async ({ page }, before) => {
        await plan(page, 'add:list-a', { status: 405 });
        await plan(page, 'sku:375-1', { hold: true });
        await add(page);
        await page.waitForFunction(() => __autosaveFixture.calls.some(call => call.key === 'sku:375-1' && !call.done));
        const lookup = (await calls(page, 'sku:375-1'))[0];
        await page.evaluate(() => { localStorage.setItem('permissions', JSON.stringify({ medias_compras: false })); dispatchEvent(new StorageEvent('storage', { key: 'permissions' })); });
        await release(page, lookup.index);
        await page.waitForFunction(() => !document.querySelector('#btnConfirmarAdicionarSku').disabled);
        assert.strictEqual(workCalls((await allCalls(page)).slice(before)).filter(call => call.method === 'PUT').length, 0,
            'A legacy lookup completed after permission loss cannot authorize a new write');
        assert.strictEqual(await page.evaluate(() => listaPedidoAtual), null);
        assert(!(await serverList(page)).itens.some(item => item.SKU === '375-1'));
    });
}


async function oldPostCannotRestoreAnotherSession(browser) {
    for (const kind of ['token', 'tenant', 'permissions']) for (const status of [200, 405]) {
        await caseWith(browser, async ({ page }) => {
            await plan(page, 'add:list-a', { hold: true, status });
            await add(page);
            await page.waitForFunction(() => __autosaveFixture.calls.some(call => call.key === 'add:list-a' && !call.done));
            const post = (await calls(page, 'add:list-a'))[0];
            await page.evaluate(kind => {
                const key = kind === 'token' ? 'access_token' : kind === 'tenant' ? 'user_data' : 'permissions';
                const value = kind === 'token' ? 'another-token' : kind === 'tenant'
                    ? JSON.stringify({ client_id: 'another-tenant', username: 'another-user' }) : JSON.stringify({ medias_compras: false });
                localStorage.setItem(key, value);
                dispatchEvent(new StorageEvent('storage', { key }));
            }, kind);
            const requestsAfterInvalidation = (await allCalls(page)).length;
            assert.strictEqual(await page.evaluate(() => listaPedidoAtual), null);
            assert.strictEqual(await page.locator('.lista-item').count(), 0);
            await release(page, post.index);
            await page.waitForFunction(() => !document.querySelector('#btnConfirmarAdicionarSku').disabled);
            assert.strictEqual(await page.evaluate(() => listaPedidoAtual), null, kind + ': old POST cannot restore the editor');
            assert.strictEqual(await page.locator('.lista-item').count(), 0, kind + ': old POST cannot restore the cards');
            assert.strictEqual((await allCalls(page)).length, requestsAfterInvalidation, kind + ': old POST cannot trigger fallback or follow-up reads');
        });
    }
}


async function missingCardIsInsertedOnlyInsideActiveStore(browser) {
    for (const store of ['all', 'a', 'b']) await caseWith(browser, async ({ page }) => {
        if (store !== 'all') {
            await page.locator('#lojaBotoes button').filter({ hasText: 'Loja ' + store.toUpperCase() }).click();
            await page.waitForFunction(store => storeIdSelecionado === 'store-' + store, store);
            await page.locator('.lista-item').filter({ hasText: 'Pedido ' + store.toUpperCase() }).waitFor();
        }
        await page.evaluate(async () => {
            listasPedidosResumo = listasPedidosResumo.filter(item => item.id !== 'list-a');
            renderListaPedidosResumo();
            await abrirListaPedidoPorId('list-a');
        });
        await page.waitForFunction(() => listaPedidoAtual?.id === 'list-a' && !document.querySelector('#editorListaPedido').inert);
        await page.waitForTimeout(350);
        const oldCards = await page.evaluate(() => listasPedidosResumo.map(item => item.id));
        const oldPayload = await page.evaluate(() => ({ listas: JSON.parse(JSON.stringify(listasPedidosResumo)) }));
        await plan(page, 'summaries', { hold: true, body: oldPayload });
        await page.evaluate(() => { void carregarListasPedidos(true); });
        await page.waitForFunction(() => __autosaveFixture.calls.some(call => call.key === 'summaries' && !call.done));
        const pending = (await calls(page, 'summaries')).find(call => !call.done);
        await add(page); await completed(page);
        const expected = store === 'b' ? oldCards : [...oldCards, 'list-a'];
        assert.deepStrictEqual(await page.evaluate(() => listasPedidosResumo.map(item => item.id)), expected,
            'Upsert must append the missing persisted card only when its store matches, preserving every other position');
        await release(page, pending.index);
        assert.deepStrictEqual(await page.evaluate(() => listasPedidosResumo.map(item => item.id)), expected);
    });
}

async function legacyStoreCardKeepsItsServerResolvedScope(browser) {
    for (const existing of [true, false]) await caseWith(browser, async ({ page }) => {
        const legacy = await page.evaluate(() => {
            __autosaveFixture.lists['list-a'].store_id = '';
            return JSON.parse(JSON.stringify(__autosaveFixture.lists['list-a']));
        });
        // The backend resolves the legacy name to this store; its serialized list still has no ID.
        await plan(page, 'summaries', { body: { listas: [{ ...legacy, itens: [], total_itens: legacy.itens.length }] } });
        await page.locator('#lojaBotoes button').filter({ hasText: 'Loja A' }).click();
        await page.locator('.lista-item').filter({ hasText: 'Pedido A' }).waitFor();
        await openList(page, 'A');
        if (!existing) await page.evaluate(() => { listasPedidosResumo = []; renderListaPedidosResumo(); });
        await add(page); await completed(page);
        assert.strictEqual((await serverList(page)).store_id, '', 'The fixture must retain the actual legacy response shape');
        assert.deepStrictEqual(await page.evaluate(() => listasPedidosResumo.map(item => item.id)), existing ? ['list-a'] : [],
            'Keep an existing server-scoped legacy card, but never infer a missing card scope from a potentially ambiguous name');
    });
}

(async () => {
    const browser = await chromium.launch({ headless: true });
    const failures = [];
    const tests = [modernUsesOnlyPost, modalCompletesBeforeSummaryResponse, canonicalSkuAndServerCubageRemainAuthoritative,
        missingEndpointUsesNarrowLegacyFallback, businessAndAccessErrorsNeverFallBack, legacyLookupErrorsNeverDownloadWholeCatalog,
        previousPutKeepsAdditionSerialized, latePostCannotReplaceNewSelection, pendingOldSummaryCannotReplaceSavedCard, savedCardUpdatesWithoutConfirmingDraft, permissionLossDuringLegacyLookupPreventsPut, oldPostCannotRestoreAnotherSession, missingCardIsInsertedOnlyInsideActiveStore, legacyStoreCardKeepsItsServerResolvedScope];
    try {
        for (const test of tests.filter(test => !process.env.JK_ADD_SKU_TEST || test.name === process.env.JK_ADD_SKU_TEST)) {
            try { await test(browser); console.log('PASS ' + test.name); }
            catch (error) { failures.push(test.name); console.error('FAIL ' + test.name + ': ' + error.stack); }
        }
    } finally { await browser.close(); }
    if (failures.length) { console.error(JSON.stringify({ failed: failures })); process.exitCode = 1; }
})().catch(error => { console.error(error); process.exitCode = 1; });
