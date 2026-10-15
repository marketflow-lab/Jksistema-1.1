'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const files = ['runtime.js', 'api-mercadolivre.js'].map(file => ({ file,
    source: fs.readFileSync(path.join(__dirname, '..', 'static', 'frontend_promo', file), 'utf8') }));
const baseCampaigns = [
    { id: 'SELLER', name: 'Promoção do usuário', type: 'SELLER_CAMPAIGN' },
    { id: 'BASE', name: 'Campanha existente', type: 'SMART', eligible_count: 8 },
];
const discovered = { id: 'NEW', name: 'Nome oficial recebido da API', type: 'PRICE_MATCHING', eligible_count: 3 };

function snapshot(overrides = {}) {
    return { success: true, job_id: 'job-test', client_id: 'client-test', loja: 'Loja Teste', seller_id: 'seller-test',
        site_id: 'MLB', status: 'running', complete: false, resumable: false, campaigns: [], revision: 1,
        progress: { processed: 1, total: 4, failed: 0, pages: 1 }, ignored_without_campaign_id: 0, ...overrides };
}

function harness(options = {}) {
    const ids = {};
    class Element {
        constructor() { this.children = []; this.style = {}; this.dataset = {}; this._value = ''; this._html = ''; }
        set id(value) { this._id = value; ids[value] = this; }
        get id() { return this._id; }
        set value(value) { this._value = value; }
        get value() { return this._value; }
        get options() { return this.children; }
        get selectedOptions() { return this.children.filter(child => child.value === this.value); }
        set innerHTML(value) {
            this._html = value; this.children = [];
            if (this.id === 'apiPromoASelect') this.value = '';
            this.button = value.includes('<button') ? {} : null;
        }
        get innerHTML() { return this._html; }
        appendChild(child) { child.parentElement = this; this.children.push(child); if (!this.value) this.value = child.value; }
        insertBefore(child) { child.parentElement = this; this.children.unshift(child); }
        setAttribute() {}
        querySelector(selector) { return selector === 'button' ? this.button : null; }
    }
    for (const id of ['apiLojaSelect', 'apiPromoASelect', 'apiPromoBSelect', 'apiErrorMsg']) new Element().id = id;
    ids.apiLojaSelect.value = 'Loja Teste';
    new Element().appendChild(ids.apiPromoBSelect);
    const storage = new Map([
        ['user_data', JSON.stringify({ client_id: 'client-test' })], ['access_token', 'token-test'],
    ]);
    const timers = new Map();
    const requests = [];
    let timerId = 0;
    const queue = [...(options.queue || [])];
    const context = {
        console, Set, Map, URLSearchParams, AbortController,
        document: { getElementById: id => ids[id] || null, createElement: () => new Element() },
        localStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value) },
        setTimeout: (callback, delay) => { const id = ++timerId; timers.set(id, { callback, delay }); return id; },
        clearTimeout: id => timers.delete(id),
        fetch: async (url, init = {}) => {
            requests.push({ url, init });
            if (!url.includes('/descoberta/')) {
                if (url.includes('/janelas?')) return { ok: true, json: async () => ({ promotion_id: 'LIGHT', promotion_type: 'LIGHTNING',
                    windows: [{ selection_id: 'LIGHT:dated', lightning_window: { start_date: '2026-10-14', finish_date: '2026-10-15' },
                        active_count: 1, eligible_count: 2 }] }) };
                return { ok: true, json: async () => ({ success: true, campaigns: options.campaigns || baseCampaigns,
                    discovery_available: options.available !== false, seller_id: 'seller-test', site_id: 'MLB' }) };
            }
            assert(queue.length, `Resposta simulada ausente: ${url}`);
            const next = queue.shift();
            if (next instanceof Error) throw next;
            if (typeof next === 'function') return next(url, init);
            return { ok: next.httpStatus ? false : true, status: next.httpStatus || 200,
                json: async () => next.httpStatus ? { detail: 'Serviço indisponível', success: false } : next };
        },
    };
    vm.createContext(context);
    for (const { source, file } of files) vm.runInContext(source, context, { filename: file });
    Object.assign(context, {
        escapeHtml: value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'),
        renderApiPromoBFiles() {}, carregarContagensCampanhasApi: async () => ({}),
        resolverAcompanhamentoAnaliseApi() {}, setApiCancelButtonVisible() {},
        getApiAutoPrefs: () => options.prefs || {}, saveApiAutoPrefs() {},
    });
    return {
        context, ids, requests, storage, timers,
        campaigns: () => Array.from(vm.runInContext('apiPromoBCampaigns', context)),
        selected: () => Array.from(vm.runInContext('apiPromoBSelectedIds', context)),
        status: () => ids.apiPromoDiscoveryStatus?.innerHTML || '',
        async tick() {
            const entry = [...timers.entries()].find(([, timer]) => timer.delay === 3000);
            assert(entry, 'A consulta deve possuir um próximo passo agendado.');
            timers.delete(entry[0]); entry[1].callback(); await settle();
        },
    };
}

async function settle() { for (let n = 0; n < 24; n++) await Promise.resolve(); }
function deferredResponse() {
    let resolve;
    const promise = new Promise(done => { resolve = payload => done({ ok: true, json: async () => payload }); });
    return { promise, resolve };
}

(async () => {
    const slow = deferredResponse();
    const fast = harness({ queue: [() => slow.promise] });
    await fast.context.carregarPromocoesApi();
    assert.deepStrictEqual(fast.campaigns().map(item => item.id), ['BASE'], 'a lista geral aparece enquanto a coleta complementar está pendente');
    assert.strictEqual(fast.ids.apiPromoASelect.value, 'SELLER');
    assert(fast.status().includes('Buscando outras promoções'));
    slow.resolve(snapshot()); await settle();
    assert.strictEqual(fast.requests[1].init.headers['X-Client-ID'], 'client-test');
    assert.strictEqual(fast.requests[1].init.headers.Authorization, 'Bearer token-test');

    const normal = harness({ queue: [snapshot(), snapshot({ revision: 2, status: 'completed', complete: true,
        campaigns: [discovered, discovered, { name: 'Sugestão individual sem campanha' },
            { id: 'COUPON', type: 'SELLER_COUPON_CAMPAIGN' }], ignored_without_campaign_id: 1 })] });
    await normal.context.carregarPromocoesApi(); await settle();
    normal.context.limparSelecaoApiPromoB();
    await normal.tick();
    assert.deepStrictEqual(normal.campaigns().map(item => item.id), ['BASE', 'NEW'], 'campanhas são unidas sem duplicar e sem transformar sugestões em campanhas');
    assert.deepStrictEqual(normal.selected(), [], 'novas campanhas não substituem a escolha explícita de limpar');
    assert(normal.ids.apiPromoBSelect.innerHTML.includes(discovered.name));
    assert(normal.status().includes('concluída') && normal.status().includes('consultadas no anúncio'));
    assert.strictEqual([...normal.timers.values()].filter(timer => timer.delay === 3000).length, 0);
    assert.strictEqual(normal.ids.apiPromoASelect.value, 'SELLER', 'a referência permanece selecionada');

    const saved = harness({ prefs: { promoBSelectionDefined: true, promoBSelectedIds: ['BASE', 'NEW'] },
        queue: [snapshot({ campaigns: [discovered] })] });
    await saved.context.carregarPromocoesApi(); await settle();
    assert.deepStrictEqual(saved.selected(), ['BASE', 'NEW'], 'preferências de uma campanha temporariamente ausente voltam a aparecer');
    saved.context.toggleApiPromoBSelection({ value: 'BASE', checked: false, closest: () => null });
    assert.deepStrictEqual(saved.selected(), ['NEW']);

    const partial = harness({ queue: [snapshot(), snapshot({ revision: 2, status: 'partial', resumable: true,
        campaigns: [discovered], progress: { processed: 2, total: 4, failed: 1, pages: 1 } }),
        snapshot({ revision: 3 }), snapshot({ revision: 4, status: 'completed', complete: true })] });
    await partial.context.carregarPromocoesApi(); await settle();
    await partial.tick();
    assert(partial.status().includes('parcial') && partial.status().includes('Retomando'));
    await partial.tick(); await partial.tick();
    assert(partial.status().includes('concluída'));
    assert.strictEqual(partial.requests.filter(request => request.init.method === 'POST').length, 2);
    assert(partial.requests.filter(request => request.init.method === 'POST').every(request => request.url.includes('force_refresh=false')));
    assert.deepStrictEqual(partial.selected(), ['BASE']);

    const stalled = harness({ queue: Array.from({ length: 4 }, (_, revision) => snapshot({ revision: revision + 1,
        status: 'partial', resumable: true, progress: { processed: 2, total: 4, failed: 2, pages: 1 } })) });
    await stalled.context.carregarPromocoesApi(); await settle();
    await stalled.tick(); await stalled.tick(); await stalled.tick();
    assert(stalled.status().includes('Retomar consulta'), 'a retomada sem progresso tem um limite e oferece ação manual');
    assert(!stalled.status().includes('Retomando...'));

    const restart = harness({ queue: [snapshot({ status: 'partial', resumable: false,
        campaigns: [{ id: 'EXPIRED', type: 'SMART', name: 'Campanha antiga' }],
        warnings: [{ code: 'INVENTORY_CHANGED', message: 'A lista de anúncios mudou durante a consulta.' }] }),
        snapshot({ job_id: 'new-job', revision: 1, status: 'completed', complete: true, campaigns: [discovered] })] });
    await restart.context.carregarPromocoesApi(); await settle();
    assert(restart.status().includes('A lista de anúncios mudou durante a consulta.'));
    assert(restart.status().includes('Atualizar consulta'), 'uma consulta parcial sem checkpoint exige uma nova geração');
    restart.context.toggleApiPromoBSelection({ value: 'EXPIRED', checked: true, closest: () => null });
    restart.ids.apiPromoDiscoveryStatus.button.onclick(); await settle();
    assert(restart.requests.at(-1).url.includes('force_refresh=true'));
    assert(restart.status().includes('concluída'));
    assert.deepStrictEqual(restart.campaigns().map(item => item.id), ['BASE', 'NEW'], 'uma geração nova retira campanhas antigas descobertas');
    assert.deepStrictEqual(Array.from(restart.context.getApiPromoBSelections(), item => item.value), ['BASE'], 'a campanha antiga não entra na análise nem na automação');
    for (const incorrect of [{ loja: 'Outra loja' }, { client_id: 'outro-cliente' }, { seller_id: 'outro-seller' }, { site_id: 'MLA' }]) {
        const isolated = harness({ queue: [snapshot({ campaigns: [discovered], ...incorrect })] });
        await isolated.context.carregarPromocoesApi(); await settle();
        assert.deepStrictEqual(isolated.campaigns().map(item => item.id), ['BASE'], `contexto diferente rejeitado: ${JSON.stringify(incorrect)}`);
    }
    const wrongJob = harness({ queue: [snapshot(), snapshot({ revision: 2, job_id: 'another-job', campaigns: [discovered] })] });
    await wrongJob.context.carregarPromocoesApi(); await settle(); await wrongJob.tick();
    assert.deepStrictEqual(wrongJob.campaigns().map(item => item.id), ['BASE']);

    const oldRevision = harness({ queue: [snapshot({ revision: 3 }), snapshot({ revision: 2, campaigns: [discovered] })] });
    await oldRevision.context.carregarPromocoesApi(); await settle(); await oldRevision.tick();
    assert.deepStrictEqual(oldRevision.campaigns().map(item => item.id), ['BASE'], 'uma revisão anterior não substitui o progresso atual');

    for (const change of ['store', 'client', 'generation', 'token']) {
        const pending = deferredResponse();
        const isolated = harness({ queue: [() => pending.promise, snapshot()] });
        await isolated.context.carregarPromocoesApi();
        const oldSignal = isolated.requests[1].init.signal;
        if (change === 'store') isolated.ids.apiLojaSelect.value = 'Outra loja';
        if (change === 'client') isolated.storage.set('user_data', JSON.stringify({ client_id: 'outro-cliente' }));
        if (change === 'token') isolated.storage.set('access_token', 'outro-token');
        if (change === 'generation') await isolated.context.carregarPromocoesApi();
        pending.resolve(snapshot({ campaigns: [discovered], status: 'completed', complete: true })); await settle();
        assert.deepStrictEqual(isolated.campaigns().map(item => item.id), ['BASE'], `resposta antiga ignorada após ${change}`);
        if (change === 'generation') assert(oldSignal.aborted, 'recarregar cancela a consulta pendente');
    }

    const lostJob = harness({ queue: [snapshot(), { httpStatus: 404 },
        snapshot({ job_id: 'replacement-job', revision: 1, status: 'completed', complete: true, campaigns: [discovered] })] });
    await lostJob.context.carregarPromocoesApi(); await settle(); await lostJob.tick();
    assert(lostJob.status().includes('Atualizar consulta'));
    lostJob.ids.apiPromoDiscoveryStatus.button.onclick(); await settle();
    assert(lostJob.requests.at(-1).url.includes('force_refresh=true'));
    assert(lostJob.status().includes('concluída'), 'um checkpoint inexistente é substituído por um novo job autorizado pelo clique');
    assert.deepStrictEqual(lostJob.campaigns().map(item => item.id), ['BASE', 'NEW']);
    const unavailable = harness({ available: false });
    await unavailable.context.carregarPromocoesApi();
    assert.strictEqual(unavailable.requests.length, 1, 'servidores antigos preservam apenas a consulta geral');
    const permanent = harness({ queue: [{ httpStatus: 403 }] });
    await permanent.context.carregarPromocoesApi(); await settle();
    assert(permanent.status().includes('incompleta') && permanent.status().includes('Retomar consulta'));
    assert.strictEqual([...permanent.timers.values()].filter(timer => timer.delay === 3000).length, 0, 'erros permanentes não são repetidos automaticamente');
    const network = harness({ queue: [new Error('Network error'), new Error('Network error'), snapshot({ status: 'completed', complete: true })] });
    await network.context.carregarPromocoesApi(); await settle(); await network.tick(); await network.tick();
    assert(network.status().includes('concluída'), 'falhas de rede transitórias retomam a consulta');

    const lightning = harness({ campaigns: [...baseCampaigns, { id: 'LIGHT', type: 'LIGHTNING', name: 'Ofertas relâmpago' }],
        queue: [snapshot({ campaigns: [{ id: 'LIGHT', type: 'LIGHTNING', eligible_count: 999 }, discovered] })] });
    await lightning.context.carregarPromocoesApi(); await settle();
    const window = lightning.campaigns().find(item => item.id === 'LIGHT');
    assert.strictEqual(window.selection_id, 'LIGHT:dated');
    assert.strictEqual(window.eligible_count, 2, 'contagens agregadas não sobrescrevem uma janela relâmpago');
    assert.deepStrictEqual(lightning.selected(), ['BASE', 'LIGHT:dated']);
    assert.strictEqual(lightning.requests.filter(request => request.url.includes('/janelas?')).length, 1);
    console.log('promocoes descoberta runtime checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
