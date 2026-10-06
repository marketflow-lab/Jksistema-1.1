'use strict';
const assert = require('assert');
const { PesquisaMercadoRunner, sellerFromPage, allowedUrl, normalizeCategory, sameCategoryPage, currentPageRequiresLogin } = require('../electron_app/main/modules/pesquisa-mercado-runner');
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

(async () => {
    await categoryAndSellerFlow(); await pauseAndResume(); await waitingLoginAndRetries(); await recoveredJob(); await landingAndJavaScriptPagination(); await atomicPublicationRecovery();
    await selectedCategoryScope(); await categoryRedirectScope(); await startDoesNotResumeAnotherScope();
    await categoryNavigationSettles(); await progressWarningAndRestart(); await pauseDuringPublication();
    console.log('Pesquisa de Mercado runner: categories, dedup, background, pause, recovery, progress warnings and retries OK');
})().catch(err => { console.error(err); process.exitCode = 1; });
