'use strict';

const assert = require('assert');
const { chromium } = require('playwright');
const { fixture, openList, field, plan, calls, release, serverList, waitServer, clean } = require('./helpers/medias_autosalvamento_fixture');

const statuses = ['Lista gerada', 'Em Orçamento', 'Analisando orçamento', 'Pedido Aprovado', 'Pedido emitido', 'Em produção', 'Em trânsito', 'Em desembaraço', 'Recebido', 'Pedido cancelado'];
const card = (page, letter) => page.locator('.lista-item').filter({ hasText:'Pedido ' + letter });
const header = page => page.locator('#statusListaPedidoEdit');

async function editStatus(container) {
    const badge = container.locator('.status-badge');
    assert.strictEqual(await badge.getAttribute('role'), 'button');
    assert((await badge.getAttribute('title') || '').trim(), 'Editable status must explain the double-click action');
    await badge.dblclick();
    const select = container.locator('select.status-lista-select');
    await select.waitFor({ state:'visible' });
    return select;
}

async function waitBadge(container, text) {
    await container.locator('.status-badge').filter({ hasText:text }).waitFor();
    assert.strictEqual(await container.locator('select.status-lista-select').count(), 0, 'Confirmed status must return to its badge');
}

async function unselectedCardChangesWithoutOpeningList(browser) {
    const test = await fixture(browser); const { page } = test;
    try {
        const reads = (await calls(page, 'detail:list-b')).length;
        const select = await editStatus(card(page, 'B'));
        assert.strictEqual(await page.locator('#nomeListaPedidoEdit').inputValue(), 'Pedido A', 'Double-clicking a card status must not select that list');
        assert.strictEqual((await calls(page, 'detail:list-b')).length, reads, 'Status editing must not trigger card loading');
        await select.selectOption('Analisando orçamento');
        await waitServer(page, () => __autosaveFixture.lists['list-b'].status === 'Analisando orçamento');
        await waitBadge(card(page, 'B'), 'Analisando orçamento');
        const requests = await calls(page, 'status:list-b');
        assert.strictEqual(requests.length, 1, 'One choice must send one status PATCH');
        assert.strictEqual(requests[0].method, 'PATCH');
        assert.deepStrictEqual(requests[0].payload, { status:'Analisando orçamento' });
        assert.strictEqual(await page.locator('#nomeListaPedidoEdit').inputValue(), 'Pedido A');
        assert.strictEqual((await calls(page, 'detail:list-b')).length, reads);
        await waitBadge(header(page), 'Lista gerada');
        assert.strictEqual((await serverList(page, 'list-a')).status, 'Lista gerada');
    } finally { await clean(test); }
}

async function headerUsesCanonicalValuesAndPersists(browser) {
    const test = await fixture(browser); const { page } = test;
    try {
        await waitBadge(header(page), 'Lista gerada');
        const select = await editStatus(header(page));
        assert.deepStrictEqual(await select.locator('option').evaluateAll(options => options.map(option => option.value)), statuses, 'Choices must preserve existing API status values');
        assert.strictEqual(await select.locator('option[value="Lista gerada"]').textContent(), 'Sem orçamento');
        await select.selectOption('Em Orçamento');
        await waitServer(page, () => __autosaveFixture.lists['list-a'].status === 'Em Orçamento');
        await waitBadge(header(page), 'Em Orçamento');
        await waitBadge(card(page, 'A'), 'Em Orçamento');
        await openList(page, 'B');
        await openList(page, 'A');
        await waitBadge(header(page), 'Em Orçamento');
        const reset = await editStatus(header(page));
        await reset.selectOption('Lista gerada');
        await waitServer(page, () => __autosaveFixture.lists['list-a'].status === 'Lista gerada');
        await waitBadge(header(page), 'Lista gerada');
        assert.deepStrictEqual((await calls(page, 'status:list-a')).map(call => call.payload), [{ status:'Em Orçamento' }, { status:'Lista gerada' }]);
    } finally { await clean(test); }
}

async function escapeBlurAndUnchangedChoiceCancel(browser) {
    const test = await fixture(browser); const { page } = test;
    try {
        let select = await editStatus(header(page));
        await select.press('Escape');
        await waitBadge(header(page), 'Lista gerada');
        select = await editStatus(card(page, 'B'));
        await page.locator('#nomeListaPedidoEdit').click();
        await waitBadge(card(page, 'B'), 'Lista gerada');
        select = await editStatus(header(page));
        await select.selectOption('Lista gerada');
        await page.locator('#nomeListaPedidoEdit').click();
        await waitBadge(header(page), 'Lista gerada');
        assert.strictEqual((await calls(page, 'status:list-a')).length, 0, 'Canceling and choosing the current value must not write');
        assert.strictEqual((await calls(page, 'status:list-b')).length, 0);
        assert.strictEqual((await calls(page, 'save:list-a')).length, 0, 'Status editor cancellation must not trigger an autosave');
    } finally { await clean(test); }
}

async function failurePreservesStatusAndAllowsRetry(browser) {
    const test = await fixture(browser); const { page } = test;
    try {
        await plan(page, 'status:list-a', { status:503, detail:'Falha sintética ao atualizar status' });
        let select = await editStatus(header(page));
        await select.selectOption('Pedido emitido');
        await page.waitForFunction(() => document.querySelector('#statusListaPedido').textContent.includes('Falha sintética ao atualizar status'));
        assert.strictEqual((await serverList(page)).status, 'Lista gerada');
        await waitBadge(header(page), 'Lista gerada');
        await waitBadge(card(page, 'A'), 'Lista gerada');
        select = await editStatus(header(page));
        await select.selectOption('Pedido emitido');
        await waitServer(page, () => __autosaveFixture.lists['list-a'].status === 'Pedido emitido');
        await waitBadge(header(page), 'Pedido emitido');
        assert.strictEqual((await calls(page, 'status:list-a')).length, 2);
        assert(!(await page.locator('#statusListaPedido').textContent()).includes('Falha sintética'), 'A successful retry must replace the old failure message');
    } finally { await clean(test); }
}

async function pendingPatchPreservesFocusedDraftAndDom(browser) {
    const test = await fixture(browser); const { page } = test;
    try {
        await plan(page, 'status:list-a', { hold:true });
        const select = await editStatus(header(page));
        await select.selectOption('Em produção');
        await page.waitForFunction(() => __autosaveFixture.calls.some(call => call.key === 'status:list-a' && !call.done));
        const pending = (await calls(page, 'status:list-a'))[0];
        await field(page, 'embalagem', 2).fill('Rascunho durante mudança de status');
        await field(page, 'embalagem', 2).evaluate(node => { window.__statusDraftNode = node; });
        await release(page, pending.index);
        await waitServer(page, () => __autosaveFixture.lists['list-a'].status === 'Em produção');
        await waitBadge(header(page), 'Em produção');
        assert.strictEqual(await page.evaluate(() => window.__statusDraftNode.isConnected && document.activeElement === window.__statusDraftNode), true, 'PATCH response must preserve the focused input node');
        assert.strictEqual(await field(page, 'embalagem', 2).inputValue(), 'Rascunho durante mudança de status');
        assert.strictEqual((await serverList(page)).itens[2]['Individual packaging'], 'Box', 'Status change must not save unconfirmed input');
        assert.strictEqual((await calls(page, 'save:list-a')).length, 0);
        await field(page, 'embalagem', 2).press('Tab');
        await waitServer(page, () => __autosaveFixture.lists['list-a'].itens[2]['Individual packaging'] === 'Rascunho durante mudança de status');
        assert.strictEqual((await serverList(page)).status, 'Em produção', 'A later autosave must preserve the selected status');
        await waitBadge(header(page), 'Em produção');
    } finally { await clean(test); }
}

async function statusSharesAutosaveQueue(browser) {
    const test = await fixture(browser); const { page } = test;
    try {
        await plan(page, 'save:list-a', { hold:true });
        await field(page, 'qtd').fill('27');
        await field(page, 'qtd').press('Enter');
        await page.waitForFunction(() => __autosaveFixture.calls.some(call => call.key === 'save:list-a' && !call.done));
        const first = (await calls(page, 'save:list-a'))[0];
        await plan(page, 'status:list-a', { hold:true });
        const select = await editStatus(header(page));
        await select.selectOption('Analisando orçamento');
        assert.strictEqual((await calls(page, 'status:list-a')).length, 0, 'Status PATCH must wait for an earlier PUT of the same list');
        await release(page, first.index);
        await page.waitForFunction(() => __autosaveFixture.calls.some(call => call.key === 'status:list-a' && !call.done));
        const status = (await calls(page, 'status:list-a'))[0];
        await field(page, 'qtd', 1).fill('38');
        await field(page, 'qtd', 1).press('Enter');
        assert.strictEqual((await calls(page, 'save:list-a')).length, 1, 'A later PUT must wait for the pending status PATCH');
        await release(page, status.index);
        await waitServer(page, () => __autosaveFixture.lists['list-a'].itens[1].Quantidade === 38);
        const saved = await serverList(page);
        assert.strictEqual(saved.itens[0].Quantidade, 27);
        assert.strictEqual(saved.status, 'Analisando orçamento');
        await waitBadge(header(page), 'Analisando orçamento');
        assert.deepStrictEqual(await page.evaluate(() => __autosaveFixture.calls.filter(call => ['save:list-a', 'status:list-a'].includes(call.key)).map(call => call.key)), ['save:list-a', 'status:list-a', 'save:list-a']);
    } finally { await clean(test); }
}

async function latePatchCannotReplaceAnotherList(browser) {
    const test = await fixture(browser); const { page } = test;
    try {
        await plan(page, 'status:list-a', { hold:true });
        const select = await editStatus(header(page));
        await select.selectOption('Em trânsito');
        await page.waitForFunction(() => __autosaveFixture.calls.some(call => call.key === 'status:list-a' && !call.done));
        const pending = (await calls(page, 'status:list-a'))[0];
        await openList(page, 'B');
        await field(page, 'embalagem').fill('Rascunho da lista B');
        await field(page, 'embalagem').evaluate(node => { window.__otherStatusDraft = node; });
        await release(page, pending.index);
        await waitServer(page, () => __autosaveFixture.lists['list-a'].status === 'Em trânsito');
        assert.strictEqual(await page.locator('#nomeListaPedidoEdit').inputValue(), 'Pedido B');
        await waitBadge(header(page), 'Lista gerada');
        assert.strictEqual(await page.evaluate(() => window.__otherStatusDraft.isConnected && document.activeElement === window.__otherStatusDraft), true, 'A late status response must preserve the new list editor');
        assert.strictEqual(await field(page, 'embalagem').inputValue(), 'Rascunho da lista B');
        assert.strictEqual((await serverList(page, 'list-b')).status, 'Lista gerada');
        assert.strictEqual((await calls(page, 'status:list-b')).length, 0);
    } finally { await clean(test); }
}

async function unselectedStatusAndReopeningUseSameQueue(browser) {
    const test = await fixture(browser); const { page } = test;
    try {
        await plan(page, 'status:list-b', { hold:true });
        let select = await editStatus(card(page, 'B'));
        await select.selectOption('Em Orçamento');
        await page.waitForFunction(() => __autosaveFixture.calls.some(call => call.key === 'status:list-b' && !call.done));
        const first = (await calls(page, 'status:list-b'))[0];
        const reads = (await calls(page, 'detail:list-b')).length;
        const opening = openList(page, 'B');
        await page.waitForFunction(() => document.querySelector('#editorListaPedido').inert);
        await page.waitForTimeout(80);
        assert.strictEqual((await calls(page, 'detail:list-b')).length, reads, 'Opening the card must wait for its pending status PATCH before reading the list');
        assert.strictEqual((await calls(page, 'status:list-b')).length, 1, 'A pending nonselected status mutation must use the list queue');
        await release(page, first.index);
        await opening;
        await waitBadge(header(page), 'Em Orçamento');
        select = await editStatus(header(page));
        await select.selectOption('Em trânsito');
        await waitServer(page, () => __autosaveFixture.lists['list-b'].status === 'Em trânsito');
        await waitBadge(header(page), 'Em trânsito');
        assert.deepStrictEqual((await calls(page, 'status:list-b')).map(call => call.payload.status), ['Em Orçamento', 'Em trânsito']);
        assert.strictEqual((await serverList(page, 'list-a')).status, 'Lista gerada');
    } finally { await clean(test); }
}

async function sessionInvalidationCancelsQueuedStatus(browser) {
    const test = await fixture(browser); const { page } = test;
    try {
        await plan(page, 'save:list-a', { hold:true });
        await field(page, 'qtd').fill('49');
        await field(page, 'qtd').press('Enter');
        await page.waitForFunction(() => __autosaveFixture.calls.some(call => call.key === 'save:list-a' && !call.done));
        const pending = (await calls(page, 'save:list-a'))[0];
        const select = await editStatus(header(page));
        await select.selectOption('Recebido');
        assert.strictEqual((await calls(page, 'status:list-a')).length, 0);
        await page.evaluate(() => { localStorage.setItem('permissions', JSON.stringify({ medias_compras:false })); dispatchEvent(new StorageEvent('storage', { key:'permissions' })); });
        await page.waitForFunction(() => document.querySelectorAll('#tblListaPedidoItens [data-kind="qtd"]').length === 0);
        await release(page, pending.index);
        await page.waitForFunction(() => __autosaveFixture.calls.some(call => call.key === 'save:list-a' && call.done));
        await page.waitForTimeout(80);
        assert.strictEqual((await calls(page, 'status:list-a')).length, 0, 'Losing permission must prevent a queued status mutation from starting');
        assert.strictEqual(await page.evaluate(() => listaPedidoAtual), null, 'An old response must not restore a session-invalidated editor');
        assert.strictEqual((await serverList(page)).status, 'Lista gerada');
    } finally { await clean(test); }
}

async function latePatchCannotRestoreInvalidatedSession(browser) {
    const test = await fixture(browser); const { page } = test;
    try {
        await plan(page, 'status:list-a', { hold:true });
        const select = await editStatus(header(page));
        await select.selectOption('Pedido cancelado');
        await page.waitForFunction(() => __autosaveFixture.calls.some(call => call.key === 'status:list-a' && !call.done));
        const pending = (await calls(page, 'status:list-a'))[0];
        await page.evaluate(() => { localStorage.setItem('permissions', JSON.stringify({ medias_compras:false })); dispatchEvent(new StorageEvent('storage', { key:'permissions' })); });
        await page.waitForFunction(() => document.querySelectorAll('#tblListaPedidoItens [data-kind="qtd"]').length === 0);
        await release(page, pending.index);
        await waitServer(page, () => __autosaveFixture.lists['list-a'].status === 'Pedido cancelado');
        await page.waitForTimeout(80);
        assert.strictEqual(await page.evaluate(() => listaPedidoAtual), null, 'An already-started PATCH must not restore the invalidated editor on completion');
        assert.strictEqual(await page.locator('#tblListaPedidoItens [data-kind="qtd"]').count(), 0);
        assert.strictEqual(await page.locator('select.status-lista-select').count(), 0);
    } finally { await clean(test); }
}

(async () => {
    const browser = await chromium.launch({ headless:true });
    const tests = [unselectedCardChangesWithoutOpeningList, headerUsesCanonicalValuesAndPersists, escapeBlurAndUnchangedChoiceCancel,
        failurePreservesStatusAndAllowsRetry, pendingPatchPreservesFocusedDraftAndDom, statusSharesAutosaveQueue,
        latePatchCannotReplaceAnotherList, unselectedStatusAndReopeningUseSameQueue, sessionInvalidationCancelsQueuedStatus, latePatchCannotRestoreInvalidatedSession];
    const selection = process.env.JK_STATUS_TEST;
    try {
        for (const test of tests.filter(test => !selection || test.name === selection)) {
            await test(browser);
            console.log('PASS ' + test.name);
        }
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
