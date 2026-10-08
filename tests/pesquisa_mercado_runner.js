'use strict';
const assert = require('assert');
const { PesquisaMercadoRunner, sellerFromPage, allowedUrl, normalizeCategory, sameCategoryPage, verifiedCategoryPage, currentPageRequiresLogin, validateCheckpointTaxonomy } = require('../electron_app/main/modules/pesquisa-mercado-runner');
const copy = value => JSON.parse(JSON.stringify(value));
const base = 'https://www.mercadolivre.com.br';

function harness(pages, collect) {
    let saved = { id: 'run-1', status: 'running', revision: 0, checkpoint: {}, counters: { categories: 0, pages: 0, sellers: 0, ads: 0, pending: 0 } };
    const vendors = new Map();
    const discovery = new Set();
    const visited = [];
    const events = [];
    let url = '';
    let stopped = 0;
    const api = async (method, path, body) => {
        if (method === 'GET' && path.includes('/descoberta?')) return { seen: discovery.has(new URL(path, base).searchParams.get('seed_key')) };
        if (method === 'GET' && path.includes('/vendedores?')) {
            const sellerKey = new URL(path, base).searchParams.get('seller_key');
            return { items: vendors.has(sellerKey) ? [{ seller_key: sellerKey }] : [] };
        }
        if (method === 'POST') {
            if (body.category) saved.checkpoint = { scope: { category: copy(body.category) }, categories: [copy(body.category)], categoryIndex: 0, discoveryInitialized: true };
            return { job: copy(saved) };
        }
        if (method === 'GET') return { job: copy(saved) };
        if (method === 'PATCH') {
            if (body.checkpoint.discoveredSeed) discovery.add(body.checkpoint.discoveredSeed.key);
            saved = { ...saved, ...copy(body), revision: saved.revision + 1 };
        }
        if (method === 'PUT') {
            vendors.set(body.seller.key, copy(body));
            saved.counters.sellers = vendors.size;
            saved.counters.ads = [...vendors.values()].reduce((n, value) => n + value.anuncios.length, 0);
            saved.revision += 1;
        }
        return { job: copy(saved) };
    };
    const runner = new PesquisaMercadoRunner({ api, wait: async () => {},
        browser: {
            async navigate(next) { visited.push(next); url = next; },
            async executeJavaScript(code) {
                if (code.startsWith('next:')) {
                    assert.strictEqual(code.slice(5), url, 'restore category before clicking its next control');
                    const target = pages[url] && pages[url].clickNext;
                    if (!target) return { clicked: false, hasNext: false, reason: 'last_page' };
                    visited.push(target); url = target;
                    return { clicked: true, hasNext: true };
                }
                const value = pages[url];
                if (typeof value === 'function') return value();
                if (!value) throw new Error('missing fixture');
                return copy({ url, ...value });
            }, getURL: () => url,
            stop: () => { stopped += 1; }, async finish() {}
        }, pageScripts: { discoveryScript: () => 'discovery', nextCategoryPageScript: url => `next:${url}` },
        collector: { collectFirstPage: collect || (async ({ seller }) => ({ anuncios: [{ id: 'MLB1000000001', rankable: true, seller_id: seller.id, vendas: 40, vendasFonte: 'avantpro', media_vendas_mensal: 10 }] })) },
        emit: event => events.push(copy(event))
    });
    return { runner, vendors, visited, events, saved: () => copy(saved), stopped: () => stopped,
        restoreCheckpoint(job) { saved = copy(job); discovery.clear(); } };
}

function seller(id) {
    return { sellerId: id, vendedor: `Seller ${id}`, perfilUrl: `${base}/perfil/${id}`, allAdsUrl: `${base}/anuncios/${id}` };
}

async function categoryAndSellerFlow() {
    const h = harness({
        [`${base}/`]: { categories: [{ key: 'parent', nome: 'Parent', url: `${base}/parent` }] },
        [`${base}/parent`]: { hasChildren: true, categories: [
            { key: 'a', nome: 'A', url: `${base}/a` }, { key: 'b', nome: 'B', url: `${base}/b` }
        ] },
        [`${base}/a`]: { anuncios: [{ id: 'seed-a', url: `${base}/product-a` }], nextUrl: `${base}/a2` },
        [`${base}/a2`]: { anuncios: [{ id: 'seed-a', url: `${base}/product-a` }, { id: 'seed-b', url: `${base}/product-b` }], nextUrl: `${base}/a2` },
        [`${base}/b`]: { anuncios: [{ id: 'seed-c', url: `${base}/product-c` }] },
        [`${base}/product-a`]: seller('111'), [`${base}/product-b`]: seller('111'),
        [`${base}/product-c`]: seller('222'),
        [`${base}/perfil/111`]: seller('111'), [`${base}/perfil/222`]: seller('222'),
        [`${base}/anuncios/111`]: { nextUrl: `${base}/anuncios/111/2` },
        [`${base}/anuncios/222`]: { nextUrl: `${base}/anuncios/222/2` }
    });
    const response = await h.runner.start();
    assert.strictEqual(response.job.status, 'running', 'start must respond before collection finishes');
    await h.runner.task;
    assert.strictEqual(h.vendors.size, 2, 'duplicate sellers must only be collected once');
    assert.strictEqual(h.visited.filter(url => url === `${base}/product-a`).length, 1, 'duplicate discovery advertisements skipped');
    assert.ok(!h.visited.some(url => /anuncios\/\d+\/2/.test(url)), 'must never visit second seller page');
    assert.strictEqual(h.saved().status, 'completed_partial', 'repeated category pagination must be reported');
    assert.strictEqual(h.saved().counters.ads, 2);
    assert.ok(h.events.some(event => event.job.counters.sellers === 1), 'publish intermediate supplier result');
    assert.ok(h.events.every(event => !('checkpoint' in event.job)), 'do not expose full persisted state in progress');
}

async function pauseAndResume() {
    let entered;
    const started = new Promise(resolve => { entered = resolve; });
    let collections = 0;
    const pages = {
        [`${base}/`]: { categories: [{ key: 'a', nome: 'A', url: `${base}/a` }] },
        [`${base}/a`]: { anuncios: [{ id: 'seed-a', url: `${base}/product-a` }] },
        [`${base}/product-a`]: seller('111'), [`${base}/perfil/111`]: seller('111'),
        [`${base}/anuncios/111`]: {}
    };
    const h = harness(pages, async ({ signal }) => {
        collections += 1;
        if (collections === 1) {
            entered();
            await new Promise((_resolve, reject) => signal.addEventListener('abort', () => {
                const err = new Error('aborted'); err.name = 'AbortError'; reject(err);
            }, { once: true }));
        }
        return { anuncios: [{ id: 'MLB1000000001', rankable: true }] };
    });
    await h.runner.start();
    await started;
    await h.runner.stop('paused');
    await h.runner.task;
    assert.strictEqual(h.saved().status, 'paused');
    assert.strictEqual(h.saved().checkpoint.page.index, 0, 'interrupted supplier must not advance cursor');
    assert.strictEqual(h.stopped(), 1);
    // Simulate the renderer disappearing; job state remains in the coordinator.
    await h.runner.resume('run-1');
    await h.runner.task;
    assert.strictEqual(h.saved().status, 'completed');
    assert.strictEqual(h.saved().checkpoint.progress.phase, 'completed');
    assert.strictEqual(h.saved().checkpoint.progress.detail, h.saved().message, 'legacy message remains compatible with progress detail');
    assert.strictEqual(h.vendors.size, 1);
    assert.strictEqual(collections, 2);
}

async function waitingLoginAndRetries() {
    let attempts = 0;
    const h = harness({
        [`${base}/`]: () => {
            attempts += 1;
            if (attempts < 3) throw new Error('transient');
            return { categories: [{ key: 'a', nome: 'A', url: `${base}/a` }] };
        },
        [`${base}/a`]: { authRequired: true }
    });
    await h.runner.start(); await h.runner.task;
    assert.strictEqual(attempts, 3, 'initial plus exactly two additional retries');
    assert.strictEqual(h.saved().status, 'waiting_login');
    assert.strictEqual(h.saved().checkpoint.progress.phase, 'waiting_login');
    assert.strictEqual(h.saved().checkpoint.progress.detail, h.saved().message, 'login reason remains available');
    assert.strictEqual(h.saved().checkpoint.categoryIndex, 0);
    const snapshot = await h.runner.status();
    assert.strictEqual(snapshot.job.status, 'waiting_login');
}

async function recoveredJob() {
    const h = harness({});
    const response = await h.runner.status();
    assert.strictEqual(response.job.status, 'paused', 'orphaned persisted running job requires explicit resume');
    assert.strictEqual(response.job.checkpoint.progress.phase, 'paused', 'recovery replaces stale activity with the paused state');
    assert.strictEqual(response.job.checkpoint.progress.detail, response.job.message);
    assert.strictEqual(h.runner.task, null);
    assert.strictEqual(allowedUrl('https://evil.example/'), false);
    assert.strictEqual(allowedUrl('http://www.mercadolivre.com.br/'), false);
    assert.strictEqual(allowedUrl('https://user:secret@www.mercadolivre.com.br/'), false);
    assert.strictEqual(allowedUrl('https://www.mercadolivre.com.br:8080/'), false);
    assert.deepStrictEqual(sellerFromPage({ sellerId: 123, perfilUrl: `${base}/perfil/foo`, vendedor: 'Foo' }).key, 'id:123');
}

async function landingAndJavaScriptPagination() {
    let slowReads = 0;
    const h = harness({
        [`${base}/`]: { categories: [{ key: 'leaf', nome: 'Leaf', url: `${base}/leaf` }] },
        [`${base}/leaf`]: { listingUrl: `${base}/leaf/_NoIndex_True` },
        [`${base}/leaf/_NoIndex_True`]: { anuncios: [{ id: 'seed-a', url: `${base}/product-a` }], hasNext: true, clickNext: `${base}/leaf/_Desde_49` },
        [`${base}/leaf/_Desde_49`]: () => {
            slowReads += 1;
            return slowReads < 3 ? { url: `${base}/leaf/_Desde_49`, anuncios: [] }
                : { url: `${base}/leaf/_Desde_49`, anuncios: [{ id: 'seed-b', url: `${base}/product-b` }] };
        },
        [`${base}/product-a`]: seller('111'), [`${base}/perfil/111`]: seller('111'),
        [`${base}/product-b`]: seller('111'), [`${base}/anuncios/111`]: { nextUrl: `${base}/anuncios/111/2` }
    });
    await h.runner.start(); await h.runner.task;
    assert.strictEqual(h.saved().status, 'completed');
    assert.strictEqual(h.saved().counters.pages, 2, 'landing is not counted as a listing page');
    assert.strictEqual(h.vendors.size, 1);
    assert.strictEqual(slowReads, 4, 'wait for a hydrated result list and confirm two settled readings');
    assert.ok(h.visited.filter(url => url === `${base}/leaf/_NoIndex_True`).length >= 2, 'restore first category page after visiting seller');
    assert.ok(!h.visited.some(url => /anuncios\/111\/2/.test(url)), 'category JavaScript pagination must not visit seller page two');
}

async function atomicPublicationRecovery() {
    let calls = 0;
    const h = harness({
        [`${base}/`]: { categories: [{ key: 'a', nome: 'A', url: `${base}/a` }] },
        [`${base}/a`]: { anuncios: [{ id: 'seed-a', url: `${base}/product-a` }] },
        [`${base}/product-a`]: seller('111'), [`${base}/perfil/111`]: seller('111'), [`${base}/anuncios/111`]: {}
    }, async () => { calls += 1; return { anuncios: [{ id: 'MLB1000000001' }] }; });
    await h.runner.start(); await h.runner.task;
    const published = h.saved();
    h.restoreCheckpoint({ ...published, status: 'paused', checkpoint: { ...published.checkpoint,
        categoryIndex: 0, seenSellerKeys: [], seenSeedIds: [], categorySeenPages: [`${base}/a`],
        page: { url: `${base}/a`, nextUrl: '', index: 0, anuncios: [{ id: 'seed-a', url: `${base}/product-a` }] }
    } });
    await h.runner.resume('run-1'); await h.runner.task;
    assert.strictEqual(calls, 1, 'SQL publication must prevent recollection after crash before checkpoint');
    assert.strictEqual(h.saved().status, 'completed');
    assert.strictEqual(h.vendors.size, 1);
}

async function pauseDuringPublication() {
    const h = harness({
        [`${base}/`]: { categories: [{ key: 'a', nome: 'A', url: `${base}/a` }] },
        [`${base}/a`]: { anuncios: [{ id: 'seed-a', url: `${base}/product-a` }] },
        [`${base}/product-a`]: seller('111'), [`${base}/perfil/111`]: seller('111'), [`${base}/anuncios/111`]: {}
    });
    let entered, release;
    const publishing = new Promise(resolve => { entered = resolve; });
    const delayedResponse = new Promise(resolve => { release = resolve; });
    const api = h.runner.api;
    h.runner.api = async (method, path, body) => {
        const result = await api(method, path, body);
        if (method === 'PUT') { entered(); await delayedResponse; }
        return result;
    };
    await h.runner.start();
    await publishing;
    await h.runner.stop('paused');
    const eventsBeforeRelease = h.events.length;
    release();
    await h.runner.task;
    assert.strictEqual(h.saved().status, 'paused', 'late publication cannot resume a stopped job');
    assert.ok(h.events.slice(eventsBeforeRelease).every(event => event.job.status === 'paused'), 'late responses keep the paused state in progress events');
    assert.ok(h.events.slice(eventsBeforeRelease).every(event => event.job.progress.phase === 'paused'), 'discovery cannot replace the paused activity');
    assert.strictEqual(h.runner.snapshot().job.message, 'Pesquisa pausada; progresso salvo.');
}

async function progressWarningAndRestart() {
    const warning = 'Alguns anuncios ficaram sem dados suficientes do Avant.';
    const pages = {
        [`${base}/`]: { categories: [{ key: 'a', nome: 'A', url: `${base}/a` }] },
        [`${base}/a`]: { anuncios: [{ id: 'seed-a', url: `${base}/product-a` }, { id: 'seed-b', url: `${base}/product-b` }] },
        [`${base}/product-a`]: seller('111'), [`${base}/perfil/111`]: seller('111'), [`${base}/anuncios/111`]: {},
        [`${base}/product-b`]: seller('222'), [`${base}/perfil/222`]: seller('222'), [`${base}/anuncios/222`]: {}
    };
    let entered;
    const started = new Promise(resolve => { entered = resolve; });
    const h = harness(pages, async ({ seller, onProgress, signal }) => {
        onProgress({ total: 2, com_dados_avant: 1 });
        if (seller.id === '222') {
            entered();
            await new Promise((_resolve, reject) => signal.addEventListener('abort', () => {
                const error = new Error('aborted'); error.name = 'AbortError'; reject(error);
            }, { once: true }));
        }
        return { anuncios: [{ id: 'MLB1000000001', rankable: true }], incomplete: true };
    });
    await h.runner.start();
    await started;
    const partial = h.events.find(event => event.job.status === 'running' && event.job.progress.warning === warning);
    assert.ok(partial, 'incomplete Avant collection still reports a running job');
    assert.strictEqual(partial.job.progress.phase, 'collecting');
    assert.match(partial.job.progress.detail, /Seller 111: 2 anuncio\(s\).*1 com dados do Avant/);
    assert.strictEqual(partial.job.message, partial.job.progress.detail, 'warning does not replace the current activity');
    const nextSeller = h.events.find(event => event.job.progress.phase === 'seller' && /Seller 222/.test(event.job.progress.detail));
    assert.ok(nextSeller, 'the next seller replaces the previous activity');
    assert.strictEqual(nextSeller.job.progress.warning, warning, 'the warning remains secondary on subsequent stages');
    await h.runner.stop('paused');
    await h.runner.task;
    const paused = h.saved();
    assert.strictEqual(paused.checkpoint.progress.phase, 'paused');
    assert.strictEqual(paused.checkpoint.progress.warning, warning, 'pause persists the warning with the cursor');
    assert.strictEqual(paused.checkpoint.page.index, 1, 'pause retains the seller that was being collected');

    const collected = [];
    const restarted = harness(pages, async ({ seller, onProgress }) => {
        collected.push(seller.id);
        onProgress({ total: 1, com_dados_avant: 1 });
        return { anuncios: [{ id: 'MLB2000000001', rankable: true }] };
    });
    restarted.restoreCheckpoint({ ...paused, checkpoint: { ...paused.checkpoint,
        auth: { accessToken: 'fixture-secret' }, progress: { ...paused.checkpoint.progress, extra: { accessToken: 'fixture-secret' } }
    } });
    const resumed = await restarted.runner.resume('run-1');
    assert.strictEqual(resumed.job.status, 'running');
    assert.strictEqual(resumed.job.progress.phase, 'preparing');
    assert.match(resumed.job.progress.detail, /Retomando/);
    assert.strictEqual(resumed.job.progress.warning, warning, 'restart restores the saved warning');
    await restarted.runner.task;
    assert.deepStrictEqual(collected, ['222'], 'restart preserves the published seller and resumes only the interrupted one');
    assert.strictEqual(restarted.saved().status, 'completed_partial');
    assert.strictEqual(restarted.saved().checkpoint.progress.phase, 'completed_partial');
    assert.strictEqual(restarted.saved().message, 'Pesquisa concluida com pendencias; consulte as categorias incompletas.');
    assert.ok(restarted.events.every(event => !('checkpoint' in event.job)), 'progress never exposes the full checkpoint');
    assert.ok(restarted.events.every(event => !JSON.stringify(event).includes('fixture-secret')), 'extra persisted metadata is excluded from public progress');

    const legacy = harness({});
    legacy.restoreCheckpoint({ ...legacy.saved(), checkpoint: { incompleteCategories: [{ key: 'a', motivo: warning }] } });
    const recovered = await legacy.runner.status();
    assert.strictEqual(recovered.job.checkpoint.progress.warning, warning, 'older checkpoints retain their saved warning');
}

async function selectedCategoryScope() {
    const root = { key: 'hub', nome: 'Acessórios', url: `${base}/c/acessorios-para-veiculos` };
    const child = { key: 'child', nome: 'Peças', url: 'https://lista.mercadolivre.com.br/acessorios-veiculos/pecas' };
    const other = { key: 'other', nome: 'Informática', url: `${base}/c/informatica` };
    const h = harness({
        [`${base}/categorias`]: { categories: [root, other], categoryTree: [{ ...root, children: [child] }, { ...other, children: [] }] },
        [child.url]: { anuncios: [{ id: 'seed', url: `${base}/product` }] },
        [`${base}/product`]: seller('111'), [`${base}/perfil/111`]: seller('111'), [`${base}/anuncios/111`]: {}
    });
    const result = await h.runner.start(root);
    assert.deepStrictEqual(result.job.scopeCategory, root);
    await h.runner.task;
    assert.deepStrictEqual(h.saved().checkpoint.scope.category, root);
    assert.strictEqual(h.vendors.size, 1);
    assert.ok(!h.visited.includes(`${base}/`), 'escopo escolhido não dispara descoberta no site inteiro');
    assert.ok(!h.visited.includes(root.url), 'índice canônico substitui hub promocional');
    assert.ok(!h.visited.includes(other.url), 'categoria fora da seleção não é visitada');
    assert.deepStrictEqual(h.saved().checkpoint.categories.map(item => item.key), ['hub', 'child']);
    for (const url of ['https://www.mercadolivre.com.br/', 'https://lista.mercadolivre.com.br/_CustId_123',
        'https://produto.mercadolivre.com.br/MLB-123456-item', 'https://lista.mercadolivre.com.br/pecas?seller_id=1',
        'https://www.mercadolivre.com.br/loja/fixture', 'https://evil.example/']) {
        assert.strictEqual(normalizeCategory({ key: 'x', nome: 'x', url }), null);
    }
    const leafRun = harness({ [child.url]: { empty: true } });
    await leafRun.runner.start(child); await leafRun.runner.task;
    assert.deepStrictEqual(leafRun.visited, [child.url], 'uma subcategoria final inicia diretamente na seleção');
}

async function categoryRedirectScope() {
    const selected = { key: 'motor', nome: 'Motor', url: 'https://lista.mercadolivre.com.br/pecas/motor' };
    const sibling = { key: 'freios', nome: 'Freios', url: 'https://lista.mercadolivre.com.br/pecas/freios' };
    for (const destination of ['https://lista.mercadolivre.com.br/pecas', sibling.url]) {
        const h = harness({
            [selected.url]: { url: destination, hasChildren: true, categories: [sibling] },
            [sibling.url]: { empty: true }
        });
        await h.runner.start(selected); await h.runner.task;
        assert.strictEqual(h.saved().status, 'completed_partial', 'redirecionamento que amplia escopo vira pendencia');
        assert.deepStrictEqual(h.visited, [selected.url], 'nao enfileirar nem consultar categorias da pagina redirecionada');
        assert.deepStrictEqual(h.saved().checkpoint.categories, [selected]);
        assert.deepStrictEqual(h.saved().checkpoint.scope.category, selected);
    }
    const listing = selected.url + '/_NoIndex_True?original_category_landing=true';
    const next = selected.url + '/_Desde_49_NoIndex_True?tracking_id=fixture';
    const normal = harness({
        [selected.url]: { url: selected.url + '/?tracking_id=fixture', listingUrl: listing },
        [listing]: { empty: true, nextUrl: next },
        [next.replace('?tracking_id=fixture', '')]: { url: next, empty: true }
    });
    await normal.runner.start(selected); await normal.runner.task;
    assert.strictEqual(normal.saved().status, 'completed', 'vitrine, rastreamento, barra final e paginacao mantem a categoria');
    assert.strictEqual(normal.saved().counters.pages, 2);
    assert.strictEqual(sameCategoryPage('https://www.mercadolivre.com.br/categorias', 'https://www.mercadolivre.com.br/categorias/'), true);
    assert.strictEqual(sameCategoryPage(selected.url + '?category_id=MLB1', selected.url + '?category_id=MLB2'), false);
    assert.strictEqual(sameCategoryPage(selected.url, 'https://www.mercadolivre.com.br/c/motor'), false, 'alias de categoria sem prova nao amplia permissao');
    for (const jsPagination of [false, true]) {
        const h = harness({
            [selected.url]: { empty: true, ...(jsPagination ? { hasNext: true, clickNext: sibling.url } : { nextUrl: sibling.url }) },
            [sibling.url]: { empty: true }
        });
        await h.runner.start(selected); await h.runner.task;
        assert.strictEqual(h.saved().status, 'completed_partial', 'paginacao para outra categoria nao e consumida');
        assert.strictEqual(h.saved().counters.pages, 1);
        assert.deepStrictEqual(h.saved().checkpoint.categories, [selected]);
        if (!jsPagination) assert.deepStrictEqual(h.visited, [selected.url], 'href de paginacao fora da categoria e rejeitado antes da navegacao');
    }
}

async function startDoesNotResumeAnotherScope() {
    const selected = { key: 'motor', nome: 'Motor', url: 'https://lista.mercadolivre.com.br/pecas/motor' };
    const other = { key: 'pecas', nome: 'Pecas', url: 'https://lista.mercadolivre.com.br/pecas' };
    const h = harness({ [selected.url]: { empty: true } });
    const api = h.runner.api;
    // The real backend's idempotent POST returns an already active/paused job.
    h.runner.api = async (method, path, body) => method === 'POST' ? { job: h.saved() } : api(method, path, body);
    for (const checkpoint of [{}, { scope: { category: other }, categories: [other], discoveryInitialized: true }]) {
        h.restoreCheckpoint({ ...h.saved(), status: 'paused', checkpoint });
        await assert.rejects(h.runner.start(selected), /outro escopo/);
        assert.strictEqual(h.runner.task, null, 'start nao retoma pesquisa legada ou de outro escopo');
        assert.strictEqual(h.saved().status, 'paused');
        assert.deepStrictEqual(h.visited, []);
    }
    const checkpoint = { scope: { category: selected }, categories: [selected], categoryIndex: 0, discoveryInitialized: true };
    for (const status of ['paused', 'waiting_login']) {
        h.restoreCheckpoint({ ...h.saved(), status, checkpoint });
        const result = await h.runner.start(selected);
        assert.strictEqual(result.job.status, status, 'mesmo escopo preserva pausa ate Retomar explicito');
        assert.strictEqual(h.runner.task, null);
        assert.deepStrictEqual(h.visited, []);
    }
    await h.runner.resume('run-1'); await h.runner.task;
    assert.strictEqual(h.saved().status, 'completed');
    assert.deepStrictEqual(h.visited, [selected.url], 'Retomar explicito continua o escopo salvo');
}

async function categoryNavigationSettles() {
    const selected = { key: 'cars', nome: 'Carros e Caminhonetes', url: 'https://lista.mercadolivre.com.br/veiculos/carros-caminhonetes' };
    const other = { key: 'old', nome: 'Anterior', url: 'https://lista.mercadolivre.com.br/anterior' };
    let reads = 0;
    const h = harness({ [selected.url]: () => {
        reads += 1;
        if (reads <= 2) return { url: `${base}/categorias`, readyState: reads === 1 ? 'loading' : 'complete', authRequired: true };
        if (reads === 3) return { url: `${base}/categorias`, readyState: 'complete', categories: [other], hasChildren: true };
        if (reads <= 5) return { url: selected.url + '/', readyState: 'loading', categories: [other], hasChildren: true };
        return { url: selected.url + '/', readyState: 'complete', empty: true };
    } });
    await h.runner.start(selected); await h.runner.task;
    assert.strictEqual(reads, 7);
    assert.strictEqual(h.saved().status, 'completed');
    assert.deepStrictEqual(h.visited, [selected.url], 'a loading category must not be navigated again');
    assert.deepStrictEqual(h.saved().checkpoint.categories, [selected], 'old and loading trees are never enqueued');
    assert.deepStrictEqual(h.saved().checkpoint.scope.category, selected);
    let hydrationReads = 0;
    const hydration = harness({ [selected.url]: () => {
        hydrationReads += 1;
        return { url: selected.url, readyState: 'complete', categoriesIndexUrl: `${base}/categorias`,
            ...(hydrationReads > 5 ? { empty: true } : {}) };
    } });
    await hydration.runner.start(selected); await hydration.runner.task;
    assert.strictEqual(hydrationReads, 7, 'a navigation-menu index link cannot declare a category fully loaded');
    assert.strictEqual(hydration.saved().status, 'completed');
    assert.strictEqual(hydration.saved().counters.pages, 1);
    for (const host of ['imoveis', 'servicos']) {
        const native = { key: host, nome: host, url: `https://${host}.mercadolivre.com.br/casas/` };
        assert.ok(normalizeCategory(native));
        assert.strictEqual(normalizeCategory({ ...native, url: `https://${host}.mercadolivre.com.br/` }), null);
        assert.strictEqual(normalizeCategory({ ...native, url: `https://${host}.mercadolivre.com.br/%2F%2F` }), null);
        assert.strictEqual(normalizeCategory({ ...native, url: native.url + '?seller_id=123' }), null);
    }
    const login = harness({ [selected.url]: { url: selected.url, avantAuthRequired: true } });
    await login.runner.start(selected); await login.runner.task;
    assert.strictEqual(login.saved().status, 'waiting_login', 'Avant login during category reading pauses collection');
    assert.deepStrictEqual(login.saved().checkpoint.scope.category, selected);
    const loginUrl = `${base}/login?from=category`;
    const redirected = harness({ [selected.url]: { url: loginUrl, readyState: 'complete', authRequired: true } });
    redirected.runner.browser.getURL = () => loginUrl;
    await redirected.runner.start(selected); await redirected.runner.task;
    assert.strictEqual(redirected.saved().status, 'waiting_login', 'an actual ML login redirect still pauses');
    const nextUrl = selected.url + '/_Desde_49_NoIndex_True';
    let nextReads = 0;
    const pagination = harness({
        [selected.url]: { empty: true, nextUrl },
        [nextUrl]: () => ++nextReads === 1
            ? { url: selected.url, readyState: 'complete', avantAuthRequired: true }
            : { url: nextUrl, readyState: 'complete', empty: true }
    });
    await pagination.runner.start(selected); await pagination.runner.task;
    assert.strictEqual(pagination.saved().status, 'completed', 'login from the previous pagination document is ignored');
    assert.strictEqual(nextReads, 3);
    assert.strictEqual(pagination.saved().counters.pages, 2);
    const authPage = { authRequired: true, readyState: 'complete' };
    assert.strictEqual(currentPageRequiresLogin({ ...authPage, url: selected.url + '?b=2&a=1' },
        { getURL: () => selected.url + '?tracking_id=fixture&polycard_client=fixture&a=1&b=2' }), true, 'parser tracking cleanup preserves document identity');
    assert.strictEqual(currentPageRequiresLogin({ ...authPage, url: `${base}/p/MLB123456?wid=MLB10000001` },
        { getURL: () => `${base}/p/MLB123456#wid=MLB10000001` }), true, 'parser wid normalization preserves product login');
    assert.strictEqual(currentPageRequiresLogin({ ...authPage, url: '' }, { getURL: () => selected.url }), false, 'old Avant document cannot pause current ML category');
    assert.strictEqual(currentPageRequiresLogin({ ...authPage, url: '' }, { getURL: () => 'https://login.avantprocloud.com.br/login' }), true, 'current Avant login remains supported');
    assert.strictEqual(currentPageRequiresLogin({ ...authPage, url: '' }, { getURL: () => 'https://avantprocloud.com.br.evil.example/login' }), false);
}

async function officialTaxonomyScope() {
    const category = id => ({key: id, nome: id, url: `https://lista.mercadolivre.com.br/pecas/${id.toLowerCase()}/`});
    const root=category('MLB100'), branch=category('MLB101'), leaf=category('MLB102'), outside=category('MLB200');
    const tree={MLB100:[branch], MLB101:[leaf], MLB102:[], MLB200:[]};
    const native='https://lista.mercadolivre.com.br/pecas/filtros/';
    const attach = h => { let loads=0; h.runner.taxonomy={load:async()=>{loads+=1;return {complete:true,updatedAt:'2026-10-06T10:00:00Z',get:key=>[root,branch,leaf,outside].find(c=>c.key===key),children:key=>copy(tree[key])};}}; return ()=>loads; };
    const h=harness({[leaf.url]:{url:native,effectiveCategoryId:leaf.key,empty:true,categories:[outside],hasChildren:true}});
    const loads=attach(h);
    await h.runner.start(root); await h.runner.task;
    assert.equal(loads(),1);
    assert.equal(h.saved().status,'completed');
    assert.equal(h.saved().checkpoint.progress.phase,'completed','official taxonomy preserves the final progress state');
    assert.deepEqual(h.visited,[leaf.url],'intermediate official nodes need no partial DOM discovery');
    assert.deepEqual(h.saved().checkpoint.categories.map(c=>c.key),[root.key,branch.key,leaf.key]);
    assert.deepEqual(Object.keys(h.saved().checkpoint.officialTaxonomy.children),[root.key,branch.key,leaf.key]);
    assert.equal(h.saved().counters.pages,1);
    for(const alter of [
        c=>{c.officialTaxonomy.children.MLB102=[root];},
        c=>{c.officialTaxonomy.children.MLB200=[];},
        c=>{c.categories.push(outside);},
        c=>{c.currentCategory=outside;},
        c=>{c.officialTaxonomy.children.MLB101=[outside];}
    ]) {const corrupt=copy(h.saved().checkpoint);alter(corrupt);assert.throws(()=>validateCheckpointTaxonomy(corrupt),/arvore preservada/);}

    for(const evidence of [{},{effectiveCategoryId:outside.key},{effectiveCategoryId:leaf.key,categoryIdentityConflict:true}]) {
        const blocked=harness({[leaf.url]:{url:native,empty:true,categories:[outside],...evidence}}); attach(blocked);
        await blocked.runner.start(root); await blocked.runner.task;
        assert.equal(blocked.saved().status,'completed_partial','redirect requires nonconflicting exact category evidence');
        assert.equal(blocked.saved().checkpoint.progress.phase,'completed_partial');
        assert.match(blocked.saved().checkpoint.progress.warning,/Categoria indisponivel/,'scope failure remains a secondary warning');
        assert.equal(blocked.saved().counters.pages,0);
        assert.deepEqual(blocked.saved().checkpoint.categories.map(c=>c.key),[root.key,branch.key,leaf.key]);
    }
    assert.equal(verifiedCategoryPage(leaf.url,native,leaf.key,leaf.key),true);
    assert.equal(verifiedCategoryPage(leaf.url,native,leaf.key,outside.key),false);
    assert.equal(verifiedCategoryPage(leaf.url,'https://lista.mercadolivre.com.br/_CustId_123',leaf.key,leaf.key),false);
    assert.equal(verifiedCategoryPage(leaf.url,'https://example.com/cat',leaf.key,leaf.key),false);
    // Resume uses the snapshot saved for this owner; a later refresh cannot
    // add an unrelated public node to the in-flight selection.
    const resumed=harness({[leaf.url]:{url:native,effectiveCategoryId:leaf.key,empty:true}});
    resumed.restoreCheckpoint({...h.saved(),status:'paused',checkpoint:{...h.saved().checkpoint,categoryIndex:2,page:null}});
    resumed.runner.taxonomy={load:async()=>{throw new Error('changed taxonomy must not be loaded on resume');}};
    await resumed.runner.resume('run-1'); await resumed.runner.task;
    assert.equal(resumed.saved().status,'completed');
    assert.deepEqual(resumed.visited,[leaf.url]);
}

async function nauticalOfficialNavigation() {
    const index = `${base}/categorias`;
    const prefix = 'https://lista.mercadolivre.com.br/acessorios-veiculos/acessorios-nauticos/';
    const node = (key, nome, url = `https://lista.mercadolivre.com.br/_CatId_${key}`) => ({key, nome, url});
    const vehicles = node('MLB5672','Acessórios para Veículos');
    const scope = node('MLB6005','Acessórios Náuticos');
    const branch = node('MLB271613','Sistemas de Navegação');
    const leaf = node('MLB455956','Anemômetros');
    const rootLink = node('https://www.mercadolivre.com.br/c/acessorios-para-veiculos',vehicles.nome,'https://www.mercadolivre.com.br/c/acessorios-para-veiculos');
    const scopeLink = node(prefix,scope.nome,prefix);
    const branchUrl=prefix+'sistemas-navegacao/', leafUrl=branchUrl+'anemometros/';
    const branchLink=node(branchUrl,branch.nome,branchUrl), leafLink=node(leafUrl,leaf.nome,leafUrl);
    const tree={ [scope.key]:[branch], [branch.key]:[leaf], [leaf.key]:[] };
    const make = (leafPage={empty:true}, parentPage={categories:[leafLink]}) => harness({
        [index]:{categoryTree:[{...rootLink,children:[scopeLink]}],categories:[rootLink]},
        [prefix]:{categories:[branchLink]}, [branchUrl]:parentPage,
        [leafUrl]:leafPage,
        [leaf.url]:{empty:true,categoryNotFound:true}
    });
    const taxonomy = () => ({load:async()=>({complete:true,updatedAt:'2026-10-08T12:00:00Z',
        get:key=>[vehicles,scope,branch,leaf].find(c=>c.key===key),
        children:key=>copy(tree[key]||[]),pathFromRoot:key=>key===scope.key?copy([vehicles,scope]):[]})});
    const h=make(); h.runner.taxonomy=taxonomy();
    await h.runner.start(scope); await h.runner.task;
    assert.equal(h.saved().status,'completed');
    assert.deepEqual(h.visited,[index,prefix,branchUrl,leafUrl]);
    assert(!h.visited.some(url=>/_CatId_/.test(url)), 'unresolved IDs must never become browser navigation');
    assert.deepEqual(h.saved().checkpoint.officialTaxonomy.ancestry,[vehicles,scope]);
    assert.equal(h.saved().counters.pages,1,'ancestry navigation is not counted as seller discovery');
    assert.equal(h.saved().checkpoint.scope.category.url,scope.url,'selected scope remains immutable');

    const seedUrl = `${base}/p/MLB1000000100`;
    const full = harness({
        [index]: { categoryTree: [{ ...rootLink, children: [scopeLink] }], categories: [rootLink] },
        [prefix]: { categories: [branchLink] }, [branchUrl]: { categories: [leafLink] },
        [leafUrl]: { anuncios: [{ id: 'MLB1000000100', url: seedUrl }] },
        [seedUrl]: seller('111'), [`${base}/perfil/111`]: seller('111'),
        [`${base}/anuncios/111`]: {},
    });
    full.runner.taxonomy = taxonomy();
    await full.runner.start(scope); await full.runner.task;
    assert.equal(full.saved().status, 'completed', 'official category navigation reaches the normal seller publication flow');
    assert.equal(full.vendors.size, 1);
    assert.equal(full.saved().counters.sellers, 1);
    assert.equal(full.saved().counters.ads, 1);
    assert.equal(full.saved().counters.pages, 1);
    const publication = full.vendors.get('id:111');
    assert(publication, 'the seed seller must be published');
    assert.equal(publication.seller.id, '111');
    assert.equal(publication.seller.categoria, leaf.nome);
    assert.equal(publication.anuncios[0].categoria_key, leaf.key);
    assert.equal(publication.anuncios[0].categoria, leaf.nome);
    assert.equal(publication.anuncios.length, 1);
    assert.equal(publication.anuncios[0].id, 'MLB1000000001');
    assert.equal(publication.anuncios[0].rankable, true, 'the collected advertisement remains eligible for ranking');
    assert.equal(publication.anuncios[0].seller_id, '111');
    assert.deepEqual(full.visited, [index, prefix, branchUrl, leafUrl, seedUrl,
        `${base}/perfil/111`, `${base}/anuncios/111`]);
    assert(!full.visited.some(url => /_CatId_/.test(url)), 'full seller collection never visits an unresolved category alias');

    const resumed=make(); resumed.runner.taxonomy={load:async()=>{throw new Error('frozen ancestry must not be refreshed');}};
    const checkpoint={...h.saved().checkpoint,categoryIndex:2,currentCategory:leaf,
        page:{categoryKey:leaf.key,url:leaf.url,index:0,anuncios:[]},categorySeenPages:[leaf.url],seenSellerKeys:['saved-seller']};
    resumed.restoreCheckpoint({...h.saved(),status:'paused',checkpoint});
    await resumed.runner.resume('run-1'); await resumed.runner.task;
    assert.equal(resumed.saved().status,'completed');
    assert(resumed.saved().checkpoint.seenSellerKeys.includes('saved-seller'),'resume preserves completed sellers');
    assert(resumed.visited.includes(leafUrl));
    assert(!resumed.visited.includes(leaf.url));

    assert.equal(resumed.saved().counters.pages,1,'an obsolete empty alias is not counted twice');
    const queued=make(); queued.runner.taxonomy={load:async()=>{throw new Error('frozen ancestry must not be refreshed');}};
    const seedJob=copy(checkpoint);
    seedJob.page.anuncios=[{id:'saved-seed',url:base+'/saved-product'}];
    let consumed=0;
    queued.runner.processSeed=async seed=>{assert.equal(seed.id,'saved-seed');consumed+=1;};
    queued.restoreCheckpoint({...h.saved(),status:'paused',checkpoint:seedJob});
    await queued.runner.resume('run-1'); await queued.runner.task;
    assert.equal(consumed,1,'saved discovery seeds are consumed even if the current category no longer lists them');
    assert.equal(queued.saved().status,'completed');
    assert(queued.visited.includes(leafUrl));
    assert(!queued.visited.includes(leaf.url));

    const legacy=make(); legacy.runner.taxonomy=taxonomy();
    const old=copy(checkpoint); delete old.officialTaxonomy.ancestry;
    legacy.restoreCheckpoint({...h.saved(),status:'paused',checkpoint:old});
    await legacy.runner.resume('run-1'); await legacy.runner.task;
    assert.equal(legacy.saved().status,'completed');
    assert.deepEqual(legacy.saved().checkpoint.officialTaxonomy.children,tree,'ancestry migration preserves frozen descendants');

    for (const page of [
        {categories:[{...leafLink,url:'https://lista.mercadolivre.com.br/agro/anemometros/',key:'MLB457024'}]},
        {categories:[leafLink,{...leafLink,url:branchUrl+'anemometros-alias/',key:branchUrl+'anemometros-alias/'}]},
        {categories:[]},
    ]) {
        const blocked=make({empty:true},page); blocked.runner.taxonomy=taxonomy();
        await blocked.runner.start(scope); await blocked.runner.task;
        assert.equal(blocked.saved().status,'completed_partial','unconfirmed category cannot be reported as an empty completed leaf');
        assert.equal(blocked.saved().counters.pages,0);
        assert(!blocked.visited.includes(leafUrl));
    }
    const notFound=make({empty:true,categoryNotFound:true}); notFound.runner.taxonomy=taxonomy();
    await notFound.runner.start(scope); await notFound.runner.task;
    assert.equal(notFound.saved().status,'completed_partial');
    assert.equal(notFound.saved().counters.pages,0);
    assert.equal(notFound.visited.filter(url=>url===leafUrl).length,1,'not-found is not retried as a loading page');

    for (const mutate of [
        c=>{c.officialTaxonomy.ancestry=[vehicles,leaf,scope];},
        c=>{c.officialTaxonomy.ancestry=[scope,scope];},
        c=>{c.officialTaxonomy.ancestry[0].url='https://evil.example/';},
        c=>{c.officialTaxonomy.ancestry.at(-1).nome='Outra categoria';},
    ]) { const invalid=copy(checkpoint);mutate(invalid);assert.throws(()=>validateCheckpointTaxonomy(invalid),/arvore preservada/); }
}

async function officialDirectoryAndRescueSettles() {
    const index = `${base}/categorias`;
    const native = 'https://lista.mercadolivre.com.br/acessorios-veiculos/acessorios-nauticos/';
    const vehicles = { key: 'MLB5672', nome: 'Acessorios para Veiculos', url: 'https://lista.mercadolivre.com.br/_CatId_MLB5672' };
    const scope = { key: 'MLB6005', nome: 'Acessorios Nauticos', url: 'https://lista.mercadolivre.com.br/_CatId_6005' };
    const rootLink = { key: 'root-link', nome: vehicles.nome, url: `${base}/c/acessorios-para-veiculos` };
    const scopeLink = { key: 'scope-link', nome: scope.nome, url: native };
    const directory = { url: index, readyState: 'complete', categories: [rootLink],
        categoryTree: [{ ...rootLink, children: [scopeLink] }] };
    const taxonomy = () => ({ load: async () => ({ complete: true, updatedAt: '2026-10-08T12:00:00Z',
        get: key => [vehicles, scope].find(node => node.key === key), children: () => [],
        pathFromRoot: () => copy([vehicles, scope]) }) });

    for (const [description, initial] of [
        ['previous product document', { url: `${base}/p/MLB123456789`, readyState: 'complete',
            anuncios: [{ id: 'old-product' }] }],
        ['loading directory document', { ...directory, readyState: 'loading' }],
    ]) {
        let indexReads = 0;
        const h = harness({ [index]: () => ++indexReads === 1 ? copy(initial) : copy(directory),
            [native]: { empty: true, readyState: 'complete' } });
        h.runner.taxonomy = taxonomy();
        await h.runner.start(scope); await h.runner.task;
        assert.equal(h.saved().status, 'completed', description + ' must settle to the official directory');
        assert.equal(indexReads, 3, description + ' must precede two stable ready reads');
        assert.deepEqual(h.visited, [index, native]);
        assert.equal(h.saved().counters.pages, 1, 'directory settling is not a discovery page');
        assert.deepEqual(h.saved().checkpoint.scope.category, scope);
    }

    const wrongDestination = harness({ [index]: { ...directory, url: `${base}/c/agro` },
        [native]: { empty: true } });
    wrongDestination.runner.taxonomy = taxonomy();
    await wrongDestination.runner.start(scope); await wrongDestination.runner.task;
    assert.equal(wrongDestination.saved().status, 'completed_partial', 'a directory tree from another destination is never consumed');
    assert.deepEqual(wrongDestination.visited, [index], 'wrong document links must never become category navigation');
    assert.equal(wrongDestination.saved().counters.pages, 0);
    assert.deepEqual(wrongDestination.saved().checkpoint.scope.category, scope);

    for (const [description, initial] of [
        ['previous rescue document', { url: scope.url, categoryNotFound: true, empty: true, readyState: 'complete' }],
        ['loading rescue document', { url: native, categoryNotFound: true, empty: true, readyState: 'loading' }],
    ]) {
        let categoryReads = 0;
        const h = harness({ [index]: directory,
            [native]: () => ++categoryReads === 1 ? copy(initial)
                : { url: native, empty: true, readyState: 'complete' } });
        h.runner.taxonomy = taxonomy();
        await h.runner.start(scope); await h.runner.task;
        assert.equal(h.saved().status, 'completed', description + ' is not a settled unavailable category');
        assert.equal(categoryReads, 3, description + ' must precede two stable ready reads');
        assert.equal(h.saved().counters.pages, 1);
        assert.equal(h.saved().checkpoint.incompleteCategories.length, 0);
    }
}

function avantFlowPages() {
    return {
        [`${base}/`]: { categories: [{ key: 'a', nome: 'A', url: `${base}/a` }] },
        [`${base}/a`]: { anuncios: [{ id: 'seed-a', url: `${base}/product-a` }] },
        [`${base}/product-a`]: seller('111'), [`${base}/perfil/111`]: seller('111'),
        [`${base}/anuncios/111`]: {}
    };
}

async function automaticAvantLoginAndFreshDocument() {
    for (const modalVisible of [false, true]) {
        let authenticated = false, loginAttempts = 0;
        const calls = [], pages = avantFlowPages();
        const firstPage = `${base}/anuncios/111`;
        pages[firstPage] = () => authenticated ? { url: firstPage, ...seller('111') }
            : modalVisible ? { url: firstPage, avantAuthRequired: true, readyState: 'complete' }
            : { url: firstPage, ...seller('999') };
        const h = harness(pages, async () => {
            assert(authenticated, 'seller collection requires a confirmed Avant session');
            return { anuncios: [{ id: 'MLB1000000001', rankable: true }] };
        });
        h.runner.browser.ensureAvantSession = async ({ signal, force }) => {
            assert(signal instanceof AbortSignal);
            calls.push({ force, url: h.runner.browser.getURL() });
            const attempted = !authenticated;
            if (attempted) loginAttempts += 1;
            authenticated = true;
            return { authenticated, attempted };
        };
        await h.runner.start(); await h.runner.task;
        assert.equal(h.saved().status, 'completed');
        assert.equal(loginAttempts, 1, 'one actual authentication, with or without a visible modal');
        assert.equal(calls.filter(call => call.force).length, modalVisible ? 1 : 0);
        assert(calls.every(call => call.url === firstPage), 'normal authentication belongs to the first seller page and collector');
        assert(!h.vendors.has('id:999'), 'login reload must discard the seller document read before authentication');
        assert.equal(h.saved().counters.pages, 1, 'authentication reload cannot duplicate the category checkpoint');
        assert.equal(h.vendors.size, 1);
        assert.equal(h.saved().counters.ads, 1);
    }
}

async function discoveryDoesNotRequireAvantControls() {
    const pages = {
        [`${base}/`]: { categories: [{ key: 'a', nome: 'A', url: `${base}/a` }] },
        [`${base}/a`]: { empty: true, hasNext: true, clickNext: `${base}/a2` },
        [`${base}/a2`]: { empty: true }
    };
    const empty = harness(pages);
    let discoveryChecks = 0;
    empty.runner.browser.ensureAvantSession = async () => {
        discoveryChecks += 1;
        return { authenticated: false, attempted: false, reason: 'unknown' };
    };
    await empty.runner.start(); await empty.runner.task;
    assert.equal(empty.saved().status, 'completed', 'index/category discovery and JavaScript pagination do not require Avant controls');
    assert.equal(discoveryChecks, 0);
    assert.equal(empty.saved().counters.pages, 2);
    assert.equal(empty.vendors.size, 0);

    let collectionCalls = 0, firstPageChecks = 0;
    const blocked = harness(avantFlowPages(), async () => {
        collectionCalls += 1;
        return { anuncios: [] };
    });
    blocked.runner.browser.ensureAvantSession = async () => {
        firstPageChecks += 1;
        assert.equal(blocked.runner.browser.getURL(), `${base}/anuncios/111`, 'unknown is checked only when the first seller page needs Avant');
        return { authenticated: false, attempted: false, reason: 'unknown' };
    };
    await blocked.runner.start(); await blocked.runner.task;
    assert.equal(blocked.saved().status, 'waiting_login', 'unknown cannot authorize collection without an Avant session');
    assert.deepEqual(blocked.visited, [`${base}/`, `${base}/a`, `${base}/product-a`, `${base}/perfil/111`, `${base}/anuncios/111`]);
    assert.equal(firstPageChecks, 1);
    assert.equal(collectionCalls, 0);
    assert.equal(blocked.saved().checkpoint.page.index, 0);
    assert.equal(blocked.vendors.size, 0);
}

async function automaticAvantRecoveryDuringCollection() {
    let collections = 0, forceCalls = 0, publications = 0;
    const h = harness(avantFlowPages(), async () => {
        collections += 1;
        return collections === 1
            ? { authRequired: true, reason: 'avant_login_required', anuncios: [{ id: 'discarded-partial' }] }
            : { anuncios: [{ id: 'MLB1000000001', rankable: true }] };
    });
    const api = h.runner.api;
    h.runner.api = async (...args) => {
        if (args[0] === 'PUT') publications += 1;
        return api(...args);
    };
    h.runner.browser.ensureAvantSession = async ({ force }) => {
        if (force) forceCalls += 1;
        return { authenticated: true, attempted: force };
    };
    await h.runner.start(); await h.runner.task;
    assert.equal(h.saved().status, 'completed');
    assert.equal(forceCalls, 1, 'an expired session gets exactly one forced recovery for this collector operation');
    assert.equal(collections, 2);
    assert.equal(publications, 1, 'only the collection after authenticated reload is published');
    assert.equal(h.vendors.get('id:111').anuncios[0].id, 'MLB1000000001');
    assert.equal(h.saved().counters.ads, 1);

    let repeatedCollections = 0, repeatedForces = 0;
    const blocked = harness(avantFlowPages(), async () => {
        repeatedCollections += 1;
        return { authRequired: true, reason: 'avant_login_required', anuncios: [] };
    });
    blocked.runner.browser.ensureAvantSession = async ({ force }) => {
        if (force) repeatedForces += 1;
        return { authenticated: true, attempted: force };
    };
    await blocked.runner.start(); await blocked.runner.task;
    assert.equal(blocked.saved().status, 'waiting_login');
    assert.equal(repeatedForces, 1, 'persistent login cannot generate another authentication submission');
    assert.equal(repeatedCollections, 2);
    assert.equal(blocked.saved().checkpoint.page.index, 0);
    assert.equal(blocked.vendors.size, 0);

    let initialCollectionLogin = 0, extraForces = 0, collectedAfterLogin = 0;
    const alreadyAttempted = harness(avantFlowPages(), async () => {
        collectedAfterLogin += 1;
        return { authRequired: true, reason: 'avant_login_required', anuncios: [] };
    });
    alreadyAttempted.runner.browser.ensureAvantSession = async ({ force }) => {
        if (force) extraForces += 1;
        const attempted = alreadyAttempted.runner.checkpoint.progress.phase === 'collecting';
        if (attempted) initialCollectionLogin += 1;
        return { authenticated: true, attempted };
    };
    await alreadyAttempted.runner.start(); await alreadyAttempted.runner.task;
    assert.equal(alreadyAttempted.saved().status, 'waiting_login');
    assert.equal(initialCollectionLogin, 1);
    assert.equal(extraForces, 0, 'a preflight that already submitted login cannot submit it a second time');
    assert.equal(collectedAfterLogin, 1);
}

async function automaticAvantFailuresPreserveProgress() {
    for (const reason of ['timeout', 'email_missing']) {
        const h = harness(avantFlowPages());
        const previous = h.saved();
        const category = { key: 'a', nome: 'A', url: `${base}/a` };
        previous.status = 'paused';
        previous.counters = { ...previous.counters, ads: 48, sellers: 1, pages: 1 };
        previous.checkpoint = { categories: [category], categoryIndex: 0, discoveryInitialized: true,
            currentCategory: category, seenSellerKeys: ['saved-seller'], seenSeedIds: ['saved-seed'],
            page: { url: `${base}/a`, index: 0, anuncios: [{ id: 'seed-a', url: `${base}/product-a` }] },
            progress: { phase: 'paused', warning: 'Existing collection warning', detail: 'Paused' } };
        h.restoreCheckpoint(previous);
        let calls = 0;
        h.runner.browser.ensureAvantSession = async () => {
            calls += 1;
            return { authenticated: false, attempted: reason === 'timeout', reason };
        };
        await h.runner.resume('run-1'); await h.runner.task;
        const result = h.saved();
        assert.equal(result.status, 'waiting_login');
        assert.equal(calls, 1, 'failed login or absent email is never retried as a transient page error');
        assert.equal(result.counters.ads, 48);
        assert.equal(result.counters.sellers, 1);
        assert.equal(result.counters.pages, 1);
        assert.equal(result.checkpoint.page.index, 0);
        assert.deepEqual(result.checkpoint.seenSellerKeys, ['saved-seller']);
        assert.deepEqual(result.checkpoint.seenSeedIds, ['saved-seed']);
        assert.equal(result.checkpoint.progress.warning, 'Existing collection warning');
        assert.match(result.message, /Avant Pro/);
        assert(!JSON.stringify(h.events).includes(reason), 'technical failure detail is not exposed as an authentication credential or raw error');
    }
}

async function mlChallengesNeverTriggerAvantLogin() {
    for (const page of [
        { authRequired: true, readyState: 'complete' },
        { authRequired: true, avantAuthRequired: true, readyState: 'complete' }
    ]) {
        const h = harness({ [`${base}/`]: page });
        let calls = 0;
        h.runner.browser.ensureAvantSession = async () => {
            calls += 1;
            throw new Error('Avant authentication must not handle an ML challenge');
        };
        await h.runner.start(); await h.runner.task;
        assert.equal(h.saved().status, 'waiting_login');
        assert.equal(calls, 0);
        assert.equal(h.saved().counters.ads, 0);
    }
}

async function automaticAvantAbortAndOwnership() {
    let entered;
    const started = new Promise(resolve => { entered = resolve; });
    const h = harness(avantFlowPages());
    let calls = 0;
    h.runner.browser.ensureAvantSession = async () => {
        calls += 1; entered();
        return new Promise(() => {});
    };
    await h.runner.start(); await started;
    await h.runner.stop('paused'); await h.runner.task;
    assert.equal(h.saved().status, 'paused');
    assert.equal(calls, 1);
    assert.equal(h.saved().counters.ads, 0);
    assert.equal(h.stopped(), 1, 'cancellation stops the login browser through the coordinator');

    for (const status of [403, 404]) {
        const denied = harness(avantFlowPages());
        let deniedCalls = 0;
        denied.runner.browser.ensureAvantSession = async () => {
            if (denied.runner.browser.getURL() !== `${base}/anuncios/111`) return { authenticated: true, attempted: false };
            deniedCalls += 1;
            const error = new Error('Job unavailable'); error.status = status; throw error;
        };
        await denied.runner.start(); await denied.runner.task;
        assert.equal(denied.saved().status, status === 403 ? 'waiting_login' : 'failed');
        assert.equal(deniedCalls, 1, 'ownership rejection is never retried as an Avant login');
        assert.equal(denied.vendors.size, 0);
        assert.equal(denied.saved().checkpoint.page.index, 0, 'an unavailable owned job cannot advance discovery as a failed advertisement');
    }
}

async function automaticAvantLoginForSellerNames() {
    for (const modalVisible of [false, true]) {
        let authenticated = false, calls = 0;
        const profile = `${base}/perfil/111`;
        const h = harness({ [profile]: () => authenticated
            ? { url: profile, ...seller('111'), vendedor: 'Confirmed Store' }
            : modalVisible ? { url: profile, avantAuthRequired: true, readyState: 'complete' }
            : { url: profile, ...seller('111'), vendedor: '', allAdsUrl: profile } });
        const execute = h.runner.browser.executeJavaScript;
        h.runner.scripts.snapshotScript = () => 'snapshot';
        h.runner.browser.executeJavaScript = async code => code === 'snapshot'
            ? { url: profile, ...seller('111'), anuncios: [{ seller_id: '111', seller_confirmed: true, vendedor: 'Confirmed Store',
                seller_name_kind: 'store_brand', seller_name_source: 'avant' }] }
            : execute(code);
        h.runner.browser.ensureAvantSession = async ({ force }) => {
            calls += 1;
            assert.equal(force, modalVisible);
            authenticated = true;
            return { authenticated: true, attempted: true };
        };
        const signal = new AbortController().signal;
        const update = await h.runner.readSellerName({ id: '111', perfilUrl: profile, seller_key: 'id:111' }, signal);
        assert.equal(update.nome, 'Confirmed Store');
        assert.equal(update.seller_key, 'id:111');
        assert.equal(calls, 1);
    }

    const profile = `${base}/perfil/111`;
    const fromML = harness({ [profile]: { ...seller('111'), vendedor: 'ML Store' } });
    let checks = 0;
    fromML.runner.browser.ensureAvantSession = async () => {
        checks += 1;
        return { authenticated: false, attempted: false, reason: 'unknown' };
    };
    const update = await fromML.runner.readSellerName({ id: '111', perfilUrl: profile, seller_key: 'id:111' }, new AbortController().signal);
    assert.equal(update.nome, 'ML Store', 'a seller name confirmed directly by ML is usable without Avant');
    assert.equal(checks, 0);

    const fallback = harness({ [profile]: { ...seller('111'), vendedor: '', allAdsUrl: profile } });
    fallback.runner.scripts.snapshotScript = () => 'snapshot';
    let fallbackChecks = 0;
    fallback.runner.browser.ensureAvantSession = async () => {
        fallbackChecks += 1;
        return { authenticated: false, attempted: false, reason: 'unknown' };
    };
    await assert.rejects(fallback.runner.readSellerName({ id: '111', perfilUrl: profile, seller_key: 'id:111' }, new AbortController().signal),
        error => error.waitingLogin === true);
    assert.equal(fallbackChecks, 1, 'Avant fallback cannot consume data with unknown authentication');
}

(async () => {
    await categoryAndSellerFlow(); await pauseAndResume(); await waitingLoginAndRetries(); await recoveredJob(); await landingAndJavaScriptPagination(); await atomicPublicationRecovery();
    await selectedCategoryScope(); await categoryRedirectScope(); await startDoesNotResumeAnotherScope();
    await categoryNavigationSettles();
    await officialTaxonomyScope();
    await nauticalOfficialNavigation();
    await officialDirectoryAndRescueSettles();
    await progressWarningAndRestart();
    await pauseDuringPublication();
    await automaticAvantLoginAndFreshDocument();
    await discoveryDoesNotRequireAvantControls();
    await automaticAvantRecoveryDuringCollection();
    await automaticAvantFailuresPreserveProgress();
    await mlChallengesNeverTriggerAvantLogin();
    await automaticAvantAbortAndOwnership();
    await automaticAvantLoginForSellerNames();
    console.log('Pesquisa de Mercado runner: official taxonomy, categories, dedup, background, pause, recovery, progress warnings and retries OK');
})().catch(err => { console.error(err); process.exitCode = 1; });
