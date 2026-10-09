"use strict";
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

class Element {
    constructor(value = '') { this.value = value; this.checked = false; this.style = {}; this.dataset = {}; this.listeners = {}; this.innerHTML = ''; }
    get selectedOptions() { return [{ value: this.value, dataset: { promoType: 'SELLER_CAMPAIGN' } }]; }
    addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
}
const windowFor = day => ({ start_date: '2026-10-' + day + 'T15:00:00+00:00', finish_date: '2026-10-' + day + 'T23:00:00+00:00' });
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
function fixture(enabled = true) {
    const ids = Object.fromEntries(['apiLojaSelect', 'apiPromoASelect', 'apiPromoBSelect', 'apiMargemMinima', 'apiMargemTolerancia',
        'apiAutoWorkEnabled', 'apiAutoApprovalRequired', 'apiAutoIntervalMinutes', 'apiAutoIntervalUnit', 'apiAutoStatus']
        .map(id => [id, new Element()]));
    ids.apiLojaSelect.value = 'store-a'; ids.apiPromoASelect.value = 'reference'; ids.apiMargemMinima.value = '15';
    ids.apiAutoWorkEnabled.checked = enabled; ids.apiAutoApprovalRequired.checked = true;
    ids.apiAutoIntervalMinutes.value = '60'; ids.apiAutoIntervalUnit.value = 'minutes';
    const storage = new Map([['user_data', JSON.stringify({ client_id: 'tenant-1', username: 'operator-synthetic' })], ['access_token', 'synthetic-token-1']]);
    const requests = []; let timerId = 0;
    const context = {
        console, URLSearchParams, FormData,
        document: { getElementById: id => ids[id] || null, createElement: () => new Element() },
        localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) },
        setTimeout: () => ++timerId, clearTimeout() {}, setInterval: () => ++timerId, clearInterval() {},
        fetch: (url, options = {}) => new Promise((resolve, reject) => requests.push({ url, options, resolve, reject,
            body: options.body ? JSON.parse(options.body) : null })),
    };
    vm.createContext(context);
    for (const file of ['runtime.js', 'api-mercadolivre.js', 'automacao-api.js']) {
        vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'frontend_promo', file), 'utf8'), context, { filename: file });
    }
    const windows = ['09', '10'].map(day => ({ id: 'campaign', name: 'Lightning', type: 'LIGHTNING', selection_id: 'window-' + day,
        lightning_window: windowFor(day), active_count: 1, eligible_count: 2 }));
    vm.runInContext('apiPromoBCampaigns = ' + JSON.stringify(windows) + '; apiAutoInicializada = true;', context);
    const selected = () => Array.from(vm.runInContext('apiPromoBSelectedIds', context));
    const select = idsToSelect => {
        vm.runInContext('apiPromoBSelectedIds = new Set(' + JSON.stringify(idsToSelect) + '); apiPromoBSelectionReady = true;', context);
        context.saveApiAutoPrefs();
    };
    const prefs = () => JSON.parse(storage.get(context.getApiAutoPrefsKey()) || '{}');
    const reply = (request, config = request.body, ok = true) => request.resolve({ ok, status: ok ? 200 : 503,
        json: async () => ok ? { config: { ...config, updated_at: 1 } } : { detail: 'synthetic first-save failure' } });
    const serverConfig = selection => ({ enabled, approval_required: true, loja: ids.apiLojaSelect.value, promocao_a_id: 'reference',
        promocao_a_type: 'SELLER_CAMPAIGN', interval_unit: 'minutes', interval_value: 60, interval_minutes: 60,
        promocoes_b_meta: windows.filter(item => selection.includes(item.selection_id)).map(item => ({ promo_b_id: item.id,
            promo_b_type: item.type, selection_id: item.selection_id, lightning_window: item.lightning_window })) });
    select(['window-09']);
    return { context, ids, storage, requests, selected, select, prefs, reply, serverConfig };
}

(async () => {
    {
        const e = fixture();
        const a = e.context.salvarApiAutoPrefsServidor();
        e.select(['window-10']);
        const b = e.context.salvarApiAutoPrefsServidor();
        await flush();
        assert.strictEqual(e.requests.length, 1, 'B must wait until A completes');
        assert.strictEqual(e.requests[0].body.promocoes_b_meta[0].selection_id, 'window-09');
        e.reply(e.requests[0]); await a; await flush();
        assert.deepStrictEqual(e.selected(), ['window-10'], 'A response must not restore A while B is queued');
        assert.deepStrictEqual(e.prefs().promoBSelectedIds, ['window-10']);
        assert.strictEqual(e.requests.length, 2);
        assert.strictEqual(e.requests[1].body.promocoes_b_meta[0].selection_id, 'window-10');
        assert.deepStrictEqual(e.requests[1].body.promocoes_b_meta[0].lightning_window, windowFor('10'));
        e.reply(e.requests[1]); await b;
        assert.deepStrictEqual(e.selected(), ['window-10']);
        assert.deepStrictEqual(e.prefs().promoBSelectedIdsByStore['store-a'], ['window-10']);
    }
    {
        const e = fixture();
        const a = e.context.salvarApiAutoPrefsServidor();
        const rejection = assert.rejects(a, /synthetic first-save failure/);
        e.select(['window-10']); const b = e.context.salvarApiAutoPrefsServidor();
        await flush(); e.reply(e.requests[0], null, false); await rejection; await flush();
        assert.strictEqual(e.requests.length, 2, 'failure in A must not poison the queue for B');
        assert.deepStrictEqual(e.selected(), ['window-10']);
        e.reply(e.requests[1]); await b;
        assert.deepStrictEqual(e.selected(), ['window-10']);
    }
    {
        const e = fixture();
        const a = e.context.salvarApiAutoPrefsServidor();
        e.select(['window-10']); const b = e.context.salvarApiAutoPrefsServidor();
        await flush();
        e.storage.set('user_data', JSON.stringify({ client_id: 'tenant-2', username: 'operator-synthetic' }));
        e.storage.set('access_token', 'synthetic-token-2'); e.ids.apiLojaSelect.value = 'store-b'; e.select([]);
        const newPrefs = e.storage.get(e.context.getApiAutoPrefsKey());
        e.reply(e.requests[0]); await a; await flush();
        assert.strictEqual(e.requests[1].body.loja, 'store-a', 'queued config is captured before store changes');
        assert.strictEqual(e.requests[1].options.headers['X-Client-ID'], 'tenant-1', 'queued request retains the original tenant');
        assert.strictEqual(e.requests[1].options.headers.Authorization, 'Bearer synthetic-token-1', 'queued request retains the original auth');
        e.reply(e.requests[1]); await b;
        assert.deepStrictEqual(e.selected(), [], 'foreign store/tenant response must not change the current selection');
        assert.strictEqual(e.storage.get(e.context.getApiAutoPrefsKey()), newPrefs, 'foreign response must not write current-user preferences');
    }
    for (const scope of ['store', 'client']) {
        const e = fixture(); const save = e.context.salvarApiAutoPrefsServidor(); await flush();
        if (scope === 'store') e.ids.apiLojaSelect.value = 'store-b';
        else e.storage.set('user_data', JSON.stringify({ client_id: 'tenant-2', username: 'operator-synthetic' }));
        e.select(['window-10']); e.reply(e.requests[0]); await save;
        assert.deepStrictEqual(e.selected(), ['window-10'], scope + ' change must discard stale PUT UI');
    }
    {
        const e = fixture(false); const sync = e.context.sincronizarAutomacaoPromoServidor();
        e.context.toggleApiPromoBSelection({ value: 'window-09', checked: false, closest: () => null });
        e.context.toggleApiPromoBSelection({ value: 'window-10', checked: true, closest: () => null });
        assert.strictEqual(e.requests.length, 1, 'disabled automation changes preferences without a PUT');
        e.reply(e.requests[0], e.serverConfig(['window-09']));
        assert.strictEqual(await sync, null, 'GET started before local user input is superseded');
        assert.deepStrictEqual(e.selected(), ['window-10']);
        assert.deepStrictEqual(e.prefs().promoBSelectedIds, ['window-10']);
    }
    {
        const e = fixture(); const a = e.context.salvarApiAutoPrefsServidor(); await flush();
        e.select(['window-10']); const b = e.context.salvarApiAutoPrefsServidor();
        const sync = e.context.sincronizarAutomacaoPromoServidor();
        const pendingGet = e.requests.find(request => !request.body);
        e.reply(e.requests[0]); await a; await flush();
        e.reply(e.requests.find(request => request.body?.promocoes_b_meta[0]?.selection_id === 'window-10')); await b;
        e.reply(pendingGet, e.serverConfig(['window-09']));
        assert.strictEqual(await sync, null, 'GET captured during pending writes remains obsolete after writes settle');
        assert.deepStrictEqual(e.selected(), ['window-10']);
    }
    {
        const e = fixture(); const first = e.context.sincronizarAutomacaoPromoServidor(); const second = e.context.sincronizarAutomacaoPromoServidor();
        e.reply(e.requests[1], e.serverConfig(['window-10'])); await second;
        e.reply(e.requests[0], e.serverConfig(['window-09']));
        assert.strictEqual(await first, null, 'earlier GET cannot replace a newer server snapshot');
        assert.deepStrictEqual(e.selected(), ['window-10']);
    }
    for (const scope of ['store', 'client']) {
        const e = fixture(); const sync = e.context.sincronizarAutomacaoPromoServidor(); const oldConfig = e.serverConfig(['window-09']);
        if (scope === 'store') e.ids.apiLojaSelect.value = 'store-b';
        else e.storage.set('user_data', JSON.stringify({ client_id: 'tenant-2', username: 'operator-synthetic' }));
        e.select(['window-10']); e.reply(e.requests[0], oldConfig);
        assert.strictEqual(await sync, null);
        assert.deepStrictEqual(e.selected(), ['window-10'], scope + ' change must discard stale GET UI');
    }
    {
        const e = fixture();
        e.ids.apiLojaSelect.value = 'store-b'; e.select(['window-10']);
        e.ids.apiLojaSelect.value = 'store-a'; e.select([]);
        const save = e.context.salvarApiAutoPrefsServidor(); await flush(); e.reply(e.requests[0]); await save;
        assert.deepStrictEqual(e.requests[0].body.promocoes_b_meta, []);
        assert.deepStrictEqual(e.selected(), [], 'empty selection must stay empty after current PUT');
        assert.deepStrictEqual(e.prefs().promoBSelectedIdsByStore['store-a'], []);
        assert.deepStrictEqual(e.prefs().promoBSelectedIdsByStore['store-b'], ['window-10']);
        const sync = e.context.sincronizarAutomacaoPromoServidor();
        e.select(['window-10', 'window-disappeared']);
        e.reply(e.requests[1], e.serverConfig([])); await sync;
        assert.deepStrictEqual(e.selected(), ['window-10', 'window-disappeared'], 'obsolete GET must preserve disappeared persisted windows');
    }
    {
        const e = fixture(false); e.context.inicializarAutomacaoPromoApi();
        const initialGet = e.requests[0];
        e.select(['window-10']); e.reply(initialGet, e.serverConfig(['window-09'])); await flush();
        assert.deepStrictEqual(e.selected(), ['window-10'], 'late initialization GET must not restore the old date');
        assert.strictEqual(e.requests.length, 2, 'latest local choice is saved after initialization was superseded');
        assert.strictEqual(e.requests[1].body.promocoes_b_meta[0].selection_id, 'window-10');
        e.reply(e.requests[1]); await flush();
        assert.deepStrictEqual(e.selected(), ['window-10']);
    }
    console.log('promocoes automation preferences async runtime checks passed (12 cases)');
})().catch(error => { console.error(error); process.exitCode = 1; });
