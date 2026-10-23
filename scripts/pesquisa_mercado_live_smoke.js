'use strict';

// Terminal-only, opt-in smoke against a dedicated development worker. Research
// requests are implemented below in memory; no JK backend endpoint is contacted.
const fs = require('node:fs/promises');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { PesquisaMercadoRunner, allowedUrl, normalizeCategory } = require('../electron_app/main/modules/pesquisa-mercado-runner');
const pageScripts = require('../static/pesquisa-mercado/page-scripts');
const collector = require('../static/pesquisa-mercado/shared-collector');
const { snapshotFromCache } = require('./audit_pesquisa_mercado_taxonomy_navigation');
const ROOT = '/api/pesquisa-mercado/jobs';
const clone = value => JSON.parse(JSON.stringify(value));
function failure(code, name = 'Error') { const error = new Error(code); error.code = code; error.name = name; return error; }
function safeCode(error) { return /^[a-z][a-z0-9_]{0,79}$/.test(error && error.code || '') ? error.code : 'smoke_failed'; }
function publicCategoryUrl(value) {
    try {
        const url = new URL(value);
        if (!allowedUrl(value) || /\/(?:perfil|loja|stores?|vendedor)(?:\/|$)|_CustId_|_SellerId_/i.test(url.pathname)) return '';
        url.search = ''; url.hash = '';
        return url.href;
    } catch (_error) { return ''; }
}

async function createCdpAdapter({ targetWs, marker, commandTimeoutMs = 35000 }) {
    let endpoint;
    try { endpoint = new URL(targetWs); } catch (_error) { throw failure('invalid_target_ws'); }
    if (endpoint.protocol !== 'ws:' || !['127.0.0.1', 'localhost', '[::1]'].includes(endpoint.hostname)
        || endpoint.username || endpoint.password || endpoint.search || endpoint.hash
        || !/^\/devtools\/page\/[a-zA-Z0-9-]+$/.test(endpoint.pathname)
        || typeof marker !== 'string' || !/^[a-zA-Z0-9_-]{12,128}$/.test(marker)) throw failure('invalid_target_ws');
    const socket = new WebSocket(endpoint.href);
    let nextId = 0, currentUrl = '', mainFrame = '', mainLoader = '', loadedLoader = '', stoppedLoader = '', loading = false, navigationStatus = null;
    const pending = new Map();
    const rejectPending = () => {
        for (const item of pending.values()) { clearTimeout(item.timer); item.reject(failure('cdp_closed')); }
        pending.clear();
    };
    socket.addEventListener('message', event => {
        let message;
        try { message = JSON.parse(String(event.data)); } catch (_error) { return; }
        if (message.id) {
            const item = pending.get(message.id);
            if (!item) return;
            pending.delete(message.id); clearTimeout(item.timer);
            if (message.error) item.reject(failure('cdp_command_failed'));
            else item.resolve(message.result || {});
            return;
        }
        const params = message.params || {};
        if (message.method === 'Page.frameNavigated' && params.frame && !params.frame.parentId) {
            mainFrame = params.frame.id; mainLoader = params.frame.loaderId || ''; currentUrl = params.frame.url;
        } else if (message.method === 'Page.navigatedWithinDocument' && params.frameId === mainFrame) currentUrl = params.url;
        else if (message.method === 'Page.frameStartedLoading' && params.frameId === mainFrame) loading = true;
        else if (message.method === 'Page.frameStoppedLoading' && params.frameId === mainFrame) { loading = false; stoppedLoader = mainLoader; }
        else if (message.method === 'Page.loadEventFired') { loading = false; loadedLoader = mainLoader; }
        else if (message.method === 'Network.responseReceived' && params.type === 'Document' && params.response
            && (!mainFrame || params.frameId === mainFrame)) {
            navigationStatus = { url: params.response.url, statusCode: params.response.status };
        }
    });
    socket.addEventListener('close', rejectPending);
    socket.addEventListener('error', rejectPending);
    try {
        await new Promise((resolve, reject) => {
            const timer = setTimeout(() => reject(failure('cdp_connect_timeout')), commandTimeoutMs);
            socket.addEventListener('open', () => { clearTimeout(timer); resolve(); }, { once: true });
            socket.addEventListener('error', () => { clearTimeout(timer); reject(failure('cdp_connect_failed')); }, { once: true });
        });
        function send(method, params = {}) {
            if (socket.readyState !== WebSocket.OPEN) return Promise.reject(failure('cdp_closed'));
            return new Promise((resolve, reject) => {
                const id = ++nextId;
                const timer = setTimeout(() => { pending.delete(id); reject(failure('cdp_command_timeout')); }, commandTimeoutMs);
                pending.set(id, { resolve, reject, timer });
                socket.send(JSON.stringify({ id, method, params }));
            });
        }
        const evaluate = async expression => {
            const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true, userGesture: true });
            if (result.exceptionDetails) throw failure('cdp_evaluation_failed');
            return result.result && result.result.value;
        };
        // A page websocket is not sufficient authorization: the development
        // launcher must mark the new disposable worker before handing it off.
        const target = await send('Target.getTargetInfo');
        if (!target.targetInfo || target.targetInfo.type !== 'page'
            || await evaluate('globalThis.__jkPesquisaMercadoLiveSmokeMarker') !== marker) throw failure('target_marker_mismatch');
        currentUrl = await evaluate('location.href');
        if (currentUrl !== 'about:blank' && !allowedUrl(currentUrl)) throw failure('target_origin_rejected');
        await send('Page.enable'); await send('Network.enable');
        const tree = await send('Page.getFrameTree');
        mainFrame = tree.frameTree && tree.frameTree.frame && tree.frameTree.frame.id || '';
        mainLoader = tree.frameTree && tree.frameTree.frame && tree.frameTree.frame.loaderId || '';
        const adapter = {
            async prepare() {},
            getURL: () => currentUrl,
            isLoading: () => loading,
            getNavigationStatus: () => navigationStatus && navigationStatus.url === currentUrl ? { ...navigationStatus } : null,
            async navigate(url, signal) {
                if (!allowedUrl(url)) throw failure('navigation_origin_rejected');
                if (signal && signal.aborted) throw failure('smoke_aborted', 'AbortError');
                let rejectAbort;
                const aborted = new Promise((_resolve, reject) => { rejectAbort = reject; });
                const abort = () => { send('Page.stopLoading').catch(() => {}); rejectAbort(failure('smoke_aborted', 'AbortError')); };
                if (signal) signal.addEventListener('abort', abort, { once: true });
                const deadline = Date.now() + commandTimeoutMs;
                loading = true; navigationStatus = null; loadedLoader = ''; stoppedLoader = '';
                try {
                    const result = await Promise.race([send('Page.navigate', { url }), aborted]);
                    if (signal && signal.aborted) throw failure('smoke_aborted', 'AbortError');
                    if (result.errorText) throw failure('smoke_navigation_failed');
                    // Page.navigate acknowledges the request before the HTML stream
                    // finishes. Match the new main loader before inspecting its
                    // document, just as Electron's loadURL does in the app.
                    while (Date.now() < deadline) {
                        const committed = result.loaderId ? mainLoader === result.loaderId : currentUrl === url;
                        const loaded = !result.loaderId || loadedLoader === result.loaderId || stoppedLoader === result.loaderId;
                        if (committed && loaded) {
                            try {
                                const documentState = await Promise.race([evaluate('({url:location.href,readyState:document.readyState})'), aborted]);
                                if (documentState && documentState.readyState === 'complete' && allowedUrl(documentState.url)) {
                                    currentUrl = documentState.url; loading = false; return;
                                }
                            } catch (error) { if (error.name === 'AbortError') throw error; }
                        }
                        await Promise.race([new Promise(resolve => setTimeout(resolve, 50)), aborted]);
                    }
                    send('Page.stopLoading').catch(() => {});
                    throw failure('category_load_timeout', 'TimeoutError');
                } finally { if (signal) signal.removeEventListener('abort', abort); }
            },
            async executeJavaScript(code) {
                if (!allowedUrl(currentUrl)) throw failure('evaluation_origin_rejected');
                return evaluate(code);
            },
            stop() { send('Page.stopLoading').catch(() => {}); },
            async finish() {},
            detach() { rejectPending(); socket.close(); }
        };
        return adapter;
    } catch (error) { socket.close(); throw error; }
}

function createMemoryApi() {
    let job = null;
    const sellers = new Map(), discovery = new Map();
    const state = () => job && clone(job);
    const pendingCount = () => [...sellers.values()].reduce((sum, item) => sum + item.anuncios.filter(ad => !collector.isComplete(ad)).length, 0)
        + [...discovery.values()].filter(item => item.status === 'failed').length;
    const api = async (method, pathname, body) => {
        const url = new URL(pathname, 'http://isolated.invalid');
        if (method === 'POST' && url.pathname === ROOT && !job) {
            if (!body || !body.category) throw failure('smoke_category_required');
            job = { id: 'isolated-live-smoke', revision: 1, generation: 0, status: 'running',
                counters: { categories: 0, pages: 0, sellers: 0, ads: 0, pending: 0 },
                checkpoint: { scope: { category: clone(body.category) }, categories: [clone(body.category)], categoryIndex: 0, discoveryInitialized: true } };
            return { job: state() };
        }
        if (!job || !url.pathname.startsWith(ROOT + '/' + job.id)) throw failure('smoke_api_route_rejected');
        const suffix = url.pathname.slice((ROOT + '/' + job.id).length);
        if (method === 'GET' && !suffix) return { job: state() };
        if (method === 'GET' && suffix === '/descoberta') {
            const found = discovery.get(url.searchParams.get('seed_key'));
            return { seen: !!found, ...(url.searchParams.has('include_status') ? { status: found && found.status || null } : {}) };
        }
        if (method === 'GET' && suffix === '/failed-seeds') return { generation: job.generation, items: [], total: 0 };
        if (method === 'GET' && suffix === '/vendedores') return { items: [...sellers.entries()]
            .filter(([key]) => !url.searchParams.get('seller_key') || key === url.searchParams.get('seller_key'))
            .map(([key, value]) => ({ ...clone(value.seller), key, seller_key: key })) };
        if (method === 'GET' && suffix === '/ranking') {
            const items = [...sellers.entries()].filter(([key]) => !url.searchParams.get('seller_key') || key === url.searchParams.get('seller_key'))
                .flatMap(([_key, value]) => clone(value.anuncios));
            const offset = Number(url.searchParams.get('offset') || 0), limit = Number(url.searchParams.get('limit') || 100);
            return { items: items.slice(offset, offset + limit), total: items.length, revision: job.revision };
        }
        if ((method === 'PATCH' || method === 'PUT') && Number(body && body.generation) !== job.generation) throw failure('smoke_generation_mismatch');
        if (method === 'PATCH' && suffix === '/checkpoint') {
            const checkpoint = clone(body.checkpoint || job.checkpoint);
            if (JSON.stringify(checkpoint.scope) !== JSON.stringify(job.checkpoint.scope)) throw failure('smoke_scope_mutation');
            if (checkpoint.discoveredSeed) { discovery.set(checkpoint.discoveredSeed.key, clone(checkpoint.discoveredSeed)); delete checkpoint.discoveredSeed; }
            job = { ...job, checkpoint, status: body.status || job.status, message: body.message || '', revision: job.revision + 1,
                counters: { ...job.counters, categories: body.counters.categories, pages: body.counters.pages, pending: pendingCount() } };
            return { job: state() };
        }
        if (method === 'PUT' && suffix.startsWith('/vendedores/')) {
            const key = decodeURIComponent(suffix.slice('/vendedores/'.length));
            if (!body.seller || key !== body.seller.key || !Array.isArray(body.anuncios)) throw failure('smoke_publication_rejected');
            sellers.set(key, clone(body));
            job.revision += 1; job.counters.sellers = sellers.size;
            job.counters.ads = [...sellers.values()].reduce((sum, value) => sum + value.anuncios.length, 0);
            job.counters.pending = pendingCount();
            return { job: state() };
        }
        throw failure('smoke_api_route_rejected');
    };
    return { api, state, sellers, discovery };
}

function parseOptions(args) {
    const options = { categoryKey: 'MLB122156', budgetMs: 180000, maxSellers: 1, stopAfterMetric: false,
        cache: path.join(process.env.APPDATA || '', 'JK Sistema Cliente', 'public-mlb-taxonomy-v1.json') };
    const names = { '--target-ws': 'targetWs', '--target-marker': 'marker', '--category-key': 'categoryKey', '--budget-ms': 'budgetMs',
        '--max-sellers': 'maxSellers', '--cache': 'cache', '--evidence-output': 'evidenceOutput' };
    for (let index = 0; index < args.length; index += 1) {
        if (args[index] === '--stop-after-metric') { options.stopAfterMetric = true; continue; }
        const name = names[args[index]];
        if (!name || !args[index + 1]) throw failure('invalid_smoke_options');
        options[name] = args[++index];
    }
    options.budgetMs = Number(options.budgetMs); options.maxSellers = Number(options.maxSellers);
    if (!options.targetWs || !options.marker || !/^MLB\d+$/.test(options.categoryKey)
        || !Number.isSafeInteger(options.budgetMs) || options.budgetMs < 1000 || options.budgetMs > 900000
        || !Number.isSafeInteger(options.maxSellers) || options.maxSellers < 1 || options.maxSellers > 3) throw failure('invalid_smoke_options');
    if (options.evidenceOutput) {
        const relative = path.relative(process.cwd(), path.resolve(options.evidenceOutput));
        if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) throw failure('invalid_evidence_destination');
    }
    return options;
}

async function runSmoke(options) {
    const bytes = await fs.readFile(options.cache);
    const snapshotSha256 = createHash('sha256').update(bytes).digest('hex');
    const snapshot = snapshotFromCache(JSON.parse(bytes.toString('utf8')));
    const category = snapshot.get(options.categoryKey);
    if (!category || snapshot.children(options.categoryKey).length) throw failure('smoke_leaf_category_required');
    const browser = await createCdpAdapter({ targetWs: options.targetWs, marker: options.marker });
    const memory = createMemoryApi();
    const observedAds = new Set(), completeAvantAds = new Set(), confirmedSellers = new Set(), discoveredSeeds = new Set();
    const observations = new Map(), failureCodes = new Set(), phases = new Set();
    let runner, stopReason = '', stopTask = null, timer;
    const stop = reason => {
        if (stopReason) return;
        stopReason = reason;
        stopTask = Promise.resolve().then(() => runner.stop('paused')).catch(() => {});
    };
    const observeCollection = async (result, opts) => {
        if (opts.seller && (opts.seller.id || opts.seller.sellerId)) confirmedSellers.add(String(opts.seller.id || opts.seller.sellerId));
        for (const ad of result && result.anuncios || []) {
            const key = ad.id || ad.url;
            if (!key) continue;
            observedAds.add(key);
            if (ad.seller_confirmed === true && /^avantpro(?:_|$)/.test(String(ad.vendasFonte || '')) && collector.isComplete(ad)) completeAvantAds.add(key);
            else completeAvantAds.delete(key);
        }
        if (options.stopAfterMetric && completeAvantAds.size && confirmedSellers.size && discoveredSeeds.size) setTimeout(() => stop('metric_sample_reached'), 0);
    };
    const trackedCollector = { ...collector };
    for (const method of ['collectFirstPage', 'collectDetail']) trackedCollector[method] = async opts => {
        const result = await collector[method]({ ...opts, timeoutMs: Math.min(60000, options.budgetMs),
            onPartial: async partial => { await observeCollection(partial, opts); if (opts.onPartial) await opts.onPartial(partial); } });
        await observeCollection(result, opts); return result;
    };
    const execute = browser.executeJavaScript;
    browser.executeJavaScript = async code => {
        const result = await execute(code);
        if (result && Array.isArray(result.anuncios)) {
            const key = result.effectiveCategoryId;
            const url = publicCategoryUrl(result.url);
            const known = /^MLB\d+$/.test(key || '') && snapshot.get(key);
            if (known && url && normalizeCategory({ ...known, url })) {
                const status = browser.getNavigationStatus();
                observations.set(key, { categoryKey: key, source: 'live_document', observedAt: new Date().toISOString(), url,
                    documentCategoryId: result.categoryDocumentIdentity === true ? key : '', statusCode: status && status.statusCode || 0, listingCount: result.anuncios.length,
                    empty: !!result.empty, categoryNotFound: !!result.categoryNotFound, identityConflict: !!result.categoryIdentityConflict });
            }
        }
        return result;
    };
    try {
        runner = new PesquisaMercadoRunner({ api: memory.api, browser, pageScripts, collector: trackedCollector,
            taxonomy: { load: async () => snapshot }, detailTimeoutMs: 12000,
            emit: event => {
                const job = event && event.job;
                if (!job) return;
                if (job.progress && job.progress.phase) phases.add(job.progress.phase);
                for (const item of job.incompleteCategories || []) if (/^[a-z][a-z0-9_]+$/.test(item.code || '')) failureCodes.add(item.code);
                if (job.counters && job.counters.sellers >= options.maxSellers) setTimeout(() => stop('seller_sample_reached'), 0);
            } });
        const processSeed = runner.processSeed.bind(runner);
        runner.processSeed = async (seed, ...args) => {
            if (seed && allowedUrl(seed.url)) discoveredSeeds.add(seed.id || seed.url);
            return processSeed(seed, ...args);
        };
        timer = setTimeout(() => stop('budget_reached'), options.budgetMs);
        await runner.start(category);
        await runner.task;
        if (stopTask) await stopTask;
        const job = memory.state();
        const sampleConfirmed = discoveredSeeds.size > 0 && confirmedSellers.size > 0 && observedAds.size > 0 && completeAvantAds.size > 0;
        const report = { version: 1, categoryKey: category.key, snapshotSha256, researchPersistence: 'memory_only',
            status: job.status, stopReason: stopReason || 'runner_finished', sampleConfirmed,
            fullResearchComplete: job.status === 'completed', categoryPages: job.counters.pages,
            discoveredSeeds: discoveredSeeds.size, confirmedSellers: confirmedSellers.size, observedAds: observedAds.size,
            completeAvantAds: completeAvantAds.size, publishedSellers: job.counters.sellers, publishedAds: job.counters.ads,
            incompleteCategories: runner.snapshot().job.incompleteCount, phases: [...phases], failureCodes: [...failureCodes] };
        if (options.evidenceOutput) await fs.writeFile(options.evidenceOutput, JSON.stringify({ version: 1, snapshotSha256, observations: [...observations.values()] }, null, 2) + '\n', { flag: 'wx' });
        return report;
    } finally {
        clearTimeout(timer);
        if (runner && runner.task) { await runner.stop('paused').catch(() => {}); await runner.task.catch(() => {}); }
        browser.detach();
    }
}

module.exports = { createCdpAdapter, createMemoryApi, parseOptions, publicCategoryUrl, runSmoke };
if (require.main === module) Promise.resolve().then(() => runSmoke(parseOptions(process.argv.slice(2)))).then(report => {
    process.stdout.write(JSON.stringify(report) + '\n');
    if (!report.sampleConfirmed) process.exitCode = 2;
}).catch(error => {
    process.stderr.write(JSON.stringify({ version: 1, sampleConfirmed: false, code: safeCode(error) }) + '\n');
    process.exitCode = 1;
});
