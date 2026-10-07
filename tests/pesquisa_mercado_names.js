'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { PesquisaMercadoRunner, cleanSellerName, sellerFromPage, enrichSellerName, sameSeller } = require('../electron_app/main/modules/pesquisa-mercado-runner');
const copy = value => JSON.parse(JSON.stringify(value));
const base = 'https://lista.mercadolivre.com.br';
const savedSeller = (id, nome = id) => ({ key: `id:${id}`, seller_key: `id:${id}`, id, nome, perfilUrl: `${base}/_CustId_${id}` });
const page = (id, vendedor = '') => ({ sellerId: id, vendedor, perfilUrl: `${base}/_CustId_${id}`, readyState: 'complete' });
function harness(options = {}) {
    const sellers = copy(options.sellers || [savedSeller('123')]);
    const requests = [], visits = [], events = [], updates = [];
    let url = '', prepareCount = 0;
    const job = { id: 'old', status: options.status || 'completed', revision: 9,
        counters: { categories: 7, pages: 4, sellers: sellers.length, ads: 99, pending: 10 },
        checkpoint: { phase: 'completed', page: { url: `${base}/category`, index: 3 } } };
    const runner = new PesquisaMercadoRunner({ namesBudgetMs: options.budgetMs || 500,
        namesSellerTimeoutMs: options.sellerTimeoutMs || 60,
        emit: event => events.push(event), wait: async () => {},
        api: async (method, path, body) => {
            requests.push({ method, path, body: copy(body || {}) });
            if (options.denied) { const error = new Error('not owned'); error.status = 404; throw error; }
            if (method === 'GET' && path.endsWith('/old')) return { job: copy(job) };
            if (method === 'GET' && path.endsWith('/vendedores')) return { items: copy(sellers) };
            if (method === 'PUT' && path.endsWith('/nomes')) {
                assert.ok(body.updates.length <= 100);
                let changed = 0;
                for (const update of body.updates) {
                    assert.deepEqual(Object.keys(update).sort(), ['nome','seller_id','seller_key','seller_name_kind','seller_perfil_url','source'].sort());
                    const seller = sellers.find(item => item.seller_key === update.seller_key);
                    if (seller.nome !== update.nome) { Object.assign(seller, { nome: update.nome }); changed += 1; updates.push(copy(update)); }
                }
                if (changed) job.revision += 1;
                return { updated: changed, pending: sellers.filter(item => !cleanSellerName(item.nome, item.id)).length, job: copy(job) };
            }
            throw new Error(`Unexpected ${method} ${path}`);
        },
        browser: {
            prepare: async () => { prepareCount += 1; }, getURL: () => url, finish: async status => events.push({ finish: status }),
            stop: () => events.push({ stop: true }),
            navigate: async next => { url = next; visits.push(next); if (options.hangNavigation) await new Promise(() => {}); },
            executeJavaScript: async code => {
                const id = (url.match(/_CustId_(\d+)/) || [])[1];
                const result = options.pages && options.pages[id] || page(id, 'Nome confirmado');
                if (code === 'snapshot' && options.snapshots) return { url, ...copy(options.snapshots[id]) };
                if (code === 'click') return {};
                if (code === 'prepare') return {};
                return { url, ...copy(result) };
            }
        }, pageScripts: { discoveryScript: () => 'discovery', snapshotScript: () => 'snapshot', clickAvantScript: () => 'click', prepareScript: () => 'prepare' }, collector: {}
    });
    // Recovery of an old research must not replace a paused coordinator.
    runner.job = { id: 'other', status: 'paused', checkpoint: { phase: 'seller' } };
    runner.checkpoint = { phase: 'seller', page: { index: 42 } };
    return { runner, requests, visits, events, updates, job, sellers, prepared: () => prepareCount };
}
async function testBothPreloadEntrypoints() {
    for (const relative of ['preload.js', 'electron_app/preload.js']) {
        let exposed;
        const calls = [];
        vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', relative), 'utf8'), {
            require: name => {
                assert.equal(name, 'electron');
                return { contextBridge: { exposeInMainWorld(name, api) { assert.equal(name, 'electronAPI'); exposed = api; } },
                    ipcRenderer: { invoke: async (...args) => { calls.push(args); return { updated: 1, pending: 0 }; } } };
            }, window: { addEventListener() {} }, URL
        }, { filename: relative });
        const payload = { authToken: 'fixture-auth', jobId: 'fixture-job' };
        assert.equal(typeof exposed.refreshPesquisaMercadoNames, 'function', `${relative} must expose recovery`);
        assert.equal((await exposed.refreshPesquisaMercadoNames(payload)).updated, 1);
        assert.equal(calls[0][0], 'pesquisa-mercado:refresh-names');
        assert.strictEqual(calls[0][1], payload);
        await exposed.refreshPesquisaMercadoNames();
        assert.deepEqual(copy(calls[1][1]), {});
    }
}
async function main() {
    await testBothPreloadEntrypoints();
    for (const name of ['', '123', 'id:123', 'Ir para a loja oficial', 'Todos os produtos', 'Ver todos os anúncios', 'Vendedor não identificado', 'Nome pendente — vendedor 123', 'Nome pendente — vendedor sem ID', 'Vendedor desconhecido', 'Comprar', 'Saiba mais']) assert.equal(cleanSellerName(name, '123'), '');
    assert.equal(cleanSellerName('123456789', '123'), '', 'numeric names are pending as in backend sanitization');
    assert.equal(sellerFromPage({ ...page('123'), vendedor: 'Ir para a loja oficial' }).nome, '');
    assert.equal(sameSeller(savedSeller('123'), { id: '123', perfilUrl: `${base}/_CustId_999` }), false);
    assert.equal(sameSeller({ id: '123', perfilUrl: 'https://www.mercadolivre.com.br/perfil/AAA' }, { id: '123', perfilUrl: 'https://www.mercadolivre.com.br/perfil/BBB' }), false);
    const raw = { vendedor: 'Nome Avant', seller_id: '123', seller_confirmed: true, seller_name_source: 'avant' };
    assert.equal(enrichSellerName(savedSeller('123'), [raw]).nome, 'Nome Avant');
    assert.equal(enrichSellerName(savedSeller('123'), [{ ...raw, seller_id: '456' }]).nome, '');
    assert.equal(enrichSellerName(savedSeller('123'), [{ ...raw, avant_seller_id: '456' }]).nome, '');
    assert.equal(enrichSellerName(savedSeller('123'), [{ ...raw, seller_perfil_url: `${base}/_CustId_456` }]).nome, '');
    assert.equal(enrichSellerName(savedSeller('123'), [{ ...raw, seller_perfil_url: `${base}/_CustId_123?seller_id=456` }]).nome, '');
    assert.equal(enrichSellerName(savedSeller('123'), [{ ...raw, seller_confirmed: false }]).nome, '');
    assert.equal(enrichSellerName(savedSeller('123'), [raw, { ...raw, vendedor: 'Nome divergente' }]).nome, '');
    const official = { ...savedSeller('123', 'Marca Oficial'), seller_name_kind: 'store_brand' };
    assert.equal(enrichSellerName(official, [raw]).nome, 'Marca Oficial');
    assert.equal(enrichSellerName(official, [raw]).seller_name_kind, 'store_brand');

    const h = harness({ sellers: [{ ...savedSeller('123', 'Nome pendente — vendedor 123'), name_pending: true }, savedSeller('456', 'Nome existente'), savedSeller('789')],
        pages: { '123': page('123', 'Loja corrigida'), '789': page('999', 'Vendedor errado') } });
    const activeJob = copy(h.runner.job), checkpoint = copy(h.runner.checkpoint), oldCounters = copy(h.job.counters);
    const result = await h.runner.refreshNames('old');
    assert.equal(result.updated, 1); assert.equal(result.pending, 1);
    assert.deepEqual(h.visits, [`${base}/_CustId_123`, `${base}/_CustId_789`]);
    assert.deepEqual(h.runner.job, activeJob); assert.deepEqual(h.runner.checkpoint, checkpoint);
    assert.deepEqual(result.job.counters, oldCounters); assert.equal(result.job.status, 'completed');
    assert.deepEqual(result.job.checkpoint, h.job.checkpoint);
    assert.ok(!h.requests.some(request => request.method === 'PATCH' || request.path.includes('/vendedores/')));
    assert.deepEqual(h.events.filter(event => event.job), [], 'recovery never emits the other research progress');
    assert.equal(h.updates[0].source, 'perfil');

    const avant = harness({ pages: { '123': page('123') }, snapshots: { '123': { ...page('123'), anuncios: [raw] } } });
    assert.equal((await avant.runner.refreshNames('old')).updated, 1);
    assert.equal(avant.updates[0].source, 'avant');
    const stale = harness({ pages: { '123': { ...page('123', 'Nome de documento anterior'), url: `${base}/_CustId_456` } } });
    assert.equal((await stale.runner.refreshNames('old')).updated, 0, 'previous document cannot supply a name');
    const contradictory = harness({ pages: { '123': { ...page('123', 'Nome incorreto'), perfilUrl: `${base}/_CustId_456` } } });
    assert.equal((await contradictory.runner.refreshNames('old')).updated, 0);
    assert.deepEqual(contradictory.updates, []);
    const login = harness({ sellers: [savedSeller('123'), savedSeller('456')], pages: {
        '123': page('123', 'Confirmada antes do login'), '456': { ...page('456'), authRequired: true }
    } });
    const loginResult = await login.runner.refreshNames('old');
    assert.equal(loginResult.updated, 1); assert.equal(loginResult.pending, 1); assert.equal(loginResult.requiresLogin, true);
    assert.equal(login.job.status, 'completed'); assert.equal(login.events.at(-1).finish, 'waiting_login');

    const denied = harness({ denied: true });
    await assert.rejects(denied.runner.refreshNames('old'), /not owned/);
    assert.equal(denied.prepared(), 0); assert.deepEqual(denied.visits, []);
    for (const status of ['running', 'waiting_login']) {
        const blocked = harness({ status }); await assert.rejects(blocked.runner.refreshNames('old'), /Pause ou encerre/);
        assert.deepEqual(blocked.visits, []); assert.equal(blocked.prepared(), 0);
    }
    const active = harness(); active.runner.task = Promise.resolve();
    await assert.rejects(active.runner.refreshNames('old'), /Pause ou encerre/);
    assert.deepEqual(active.requests, []);
    const timed = harness({ hangNavigation: true, budgetMs: 20, sellerTimeoutMs: 50 });
    const timedResult = await timed.runner.refreshNames('old');
    assert.equal(timedResult.updated, 0); assert.equal(timedResult.pending, 1); assert.match(timedResult.message, /Tempo/);
    assert.equal(timed.runner.namesTask, null); assert.equal(timed.job.status, 'completed');
    const cancelled = harness({ hangNavigation: true });
    const cancelTask = cancelled.runner.refreshNames('old');
    await new Promise(resolve => setImmediate(resolve)); cancelled.runner.cancelNameRecovery();
    const cancelledResult = await cancelTask;
    assert.equal(cancelledResult.pending, 1); assert.equal(cancelled.job.status, 'completed');
    assert.equal(cancelled.runner.namesTask, null);

    const batch = harness({ sellers: Array.from({ length: 101 }, (_, index) => savedSeller(String(index + 1000))) });
    assert.equal((await batch.runner.refreshNames('old')).updated, 101);
    assert.deepEqual(batch.requests.filter(request => request.method === 'PUT').map(request => request.body.updates.length), [100, 1]);
    assert.equal((await batch.runner.refreshNames('old')).updated, 0, 'retry skips already confirmed names');
    console.log('Pesquisa de Mercado names: identity, official brand, exact ownership, partial login, time budget, cancellation, batches and research preservation OK');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
