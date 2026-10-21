'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const source = name => fs.readFileSync(path.join(root, 'static/vendas', name + '.js'), 'utf8');
const runtime = source('runtime'), sync = source('sync'), period = source('periodo-cache');
const data = source('dados-render'), graph = source('grafico'), bridge = source('visual-novo-bridge');
const flush = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
const response = payload => ({ ok: true, status: 200, json: async () => payload });

class Clock {
    constructor() { this.now = 1000; this.next = 0; this.timers = new Map(); }
    set(fn, delay) { const id = ++this.next; this.timers.set(id, { fn, at: this.now + delay }); return id; }
    async tick(delay) {
        const until = this.now + delay;
        for (;;) {
            const next = [...this.timers].filter(([, t]) => t.at <= until).sort((a, b) => a[1].at - b[1].at)[0];
            if (!next) break;
            this.now = next[1].at; this.timers.delete(next[0]); next[1].fn(); await flush();
        }
        this.now = until; await flush();
    }
}

function harness() {
    const clock = new Clock(), events = new Map(), nodes = new Map();
    const fixture = { tenant: 'tenant-a', start: '2026-10-01', end: '2026-10-20', rows: [], graphs: [], calls: [], fromSku: false };
    const node = id => {
        if (!nodes.has(id)) nodes.set(id, {
            value: '', textContent: '', innerHTML: '', className: '', disabled: false, children: [], options: [], dataset: {}, listeners: {},
            addEventListener(type, listener) { this.listeners[type] = listener; },
            appendChild(child) { this.children.push(child); },
            querySelector() { return this.children.find(child => 'vendasReadRetry' in (child.dataset || {})) || null; }, querySelectorAll: () => [],
        });
        return nodes.get(id);
    };
    node('unidadeNegocioSelect').value = '__todos';
    node('unidadeNegocioSelect').options = [{ value: '__todos', textContent: 'Todas' }];
    const browserWindow = {
        addEventListener(type, listener) { if (!events.has(type)) events.set(type, []); events.get(type).push(listener); },
        dispatchEvent(event) { for (const listener of events.get(event.type) || []) listener(event); },
    };
    class FakeDate extends Date { static now() { return clock.now; } }
    const context = vm.createContext({
        fixture, window: browserWindow, document: { visibilityState: 'visible', getElementById: node, createElement: () => node('new-' + nodes.size), querySelector: () => null, querySelectorAll: () => [], addEventListener() {} },
        console: { log() {}, warn() {}, error() {} }, Date: FakeDate, AbortController, DOMException, URLSearchParams,
        setTimeout: (fn, delay) => clock.set(fn, delay), clearTimeout: id => clock.timers.delete(id),
        queueMicrotask, CustomEvent: class { constructor(type, options = {}) { this.type = type; this.detail = options.detail; } },
        localStorage: { getItem: key => key === 'user_data' ? JSON.stringify({ client_id: fixture.tenant }) : 'test-session' },
        sessionStorage: { getItem: () => null, removeItem() {}, setItem() {} },
        fetch: (url, options) => { fixture.calls.push({ url, options }); return fixture.fetch(url, options); },
        obterAuthHeaders: () => ({}),
    });
    vm.runInContext(runtime, context);
    vm.runInContext(sync.slice(0, sync.indexOf('function aguardarProximoFrame()')), context);
    vm.runInContext(graph.slice(0, graph.indexOf('function renderizarRankingModalVendas')), context);
    vm.runInContext(`
        function getDataIniISO(){return fixture.start;} function getDataFimISO(){return fixture.end;}
        function aguardarProximoFrame(){return Promise.resolve();}
        function agendarSegundoPlano(fn, delay){if(delay !== 900 && fn.toString().includes('carregarGrafico')) fn();}
        function atualizarUnidadesNegocio(){} function renderResumo(){} function renderResumoTotaisVendas(){}
        function renderBotoesLojas(){} function obterValorExibicaoSku(row){return Number(row.valor || 0);}
        function renderTabelaAgrupada(rows){fixture.rows.push(JSON.parse(JSON.stringify(rows)));}
        async function filtrar(){renderTabelaAgrupada(dados);}
        function renderizarGrafico(payload){fixture.graphs.push(payload);}
        function atualizarObservacoesGraficoVendas(){} function carregarOciososSidebarVendas(){}
        function periodoGraficoIncluiMesAtual(){return false;}
        function mesmaLoja(a,b){return String(a).trim().toLowerCase() === String(b).trim().toLowerCase();}
        function formatDate(value){return value;}
    `, context);
    vm.runInContext(data.slice(data.indexOf('function aplicarResumoVendas('), data.indexOf("filtroTexto.addEventListener('input'")), context);
    vm.runInContext(graph.slice(graph.indexOf('async function carregarGrafico()'), graph.indexOf('function compactarDadosGrafico(')), context);
    vm.runInContext(graph.slice(graph.indexOf('function deslocarAnoIso('), graph.indexOf('function alinharSerieComparativa(')), context);
    vm.runInContext(period.slice(period.indexOf('function setSyncButtons('), period.indexOf('// Funções utilitárias')), context);
    vm.runInContext(bridge, context);
    const evaluate = code => vm.runInContext(code, context);
    fixture.fetch = async url => response(url.includes('/resumo') ? { rows: [{ sku: '001', quantidade: 2, valor: 12.34 }], totals: { valor: 12.34 } } : url.includes('/grafico') ? { labels: ['2026-10'], valores_vendas: [12.34] } : { active: true, automacao: { jobs: [], lojas: [] } });
    return { clock, fixture, context, evaluate, nodes, node, window: browserWindow, snapshot: () => browserWindow.JKVendasLegacyBridge.getSnapshot() };
}

let passed = 0;
async function check(name, test) { await test(); passed++; console.log('OK ' + name); }

async function main() {
    for (const fromSku of [false, true]) {
        await check('entry does not await pending progress, fromSku=' + fromSku, async () => {
            const h = harness();
            const originalFetch = h.fixture.fetch;
            h.fixture.fetch = (url, options) => url.includes('/sync/progress') ? new Promise(() => {}) : originalFetch(url, options);
            h.context.fromSku = fromSku;
            h.evaluate(`
                function carregarPreferenciaGrafico(){} function aplicarPreferenciaGraficoUI(){}
                function iaRestaurarOuCriarConversa(){return Promise.resolve();}
                async function carregarLojas(){return true;} async function normalizarPeriodoGraficoRestaurado(){return true;}
                async function restaurarCacheTelaVendasSeValido(){return false;}
                var data_inicio_param='', data_fim_param='', retornoDoSku=fromSku;
            `);
            h.evaluate(source('init')); await flush();
            assert.equal(h.fixture.rows.length, 1); assert.equal(h.fixture.graphs.length, 1);
            assert.equal(h.snapshot().loading, false); assert.equal(h.snapshot().graphLoading, false);
            assert.equal(h.fixture.calls.filter(c => c.url.includes('/sync/progress')).length, 1);
            h.window.dispatchEvent({ type: 'pagehide' });
        });
    }
    await check('a bootstrap store rejection stops both spinners and exposes the error', async () => {
        const h = harness();
        h.evaluate(`
            function carregarPreferenciaGrafico(){} function aplicarPreferenciaGraficoUI(){}
            function iaRestaurarOuCriarConversa(){return Promise.resolve();}
            async function carregarLojas(){throw new Error('Lojas indisponíveis');}
            var data_inicio_param='', data_fim_param='', retornoDoSku=false;
        `);
        h.evaluate(source('init')); await flush();
        assert.equal(h.snapshot().loading, false); assert.equal(h.snapshot().graphLoading, false);
        assert.match(h.snapshot().loadError, /Lojas indisponíveis/); assert.match(h.snapshot().graphError, /Lojas indisponíveis/);
        assert.equal(h.fixture.calls.length, 0);
    });

    await check('deadline covers stalled headers and clears the request', async () => {
        const h = harness(); h.fixture.fetch = () => new Promise(() => {});
        const request = h.evaluate('fetchComTimeout("/headers", {}, 100)').then(() => null, e => e.name);
        await h.clock.tick(100); assert.equal(await request, 'TimeoutError'); assert.equal(h.clock.timers.size, 0);
    });
    await check('JSON body uses the original deadline after delayed headers', async () => {
        const h = harness(), headers = deferred(); h.fixture.fetch = () => headers.promise;
        const request = h.evaluate('fetchComTimeout("/body", {}, 100)');
        await h.clock.tick(60); headers.resolve({ ok: true, json: () => new Promise(() => {}) }); await flush();
        const body = (await request).json().then(() => null, e => e.name);
        await h.clock.tick(39); assert.equal(h.clock.timers.size, 1);
        await h.clock.tick(1); assert.equal(await body, 'TimeoutError'); assert.equal(h.clock.timers.size, 0);
    });
    await check('external cancellation between headers and body prevents reading it', async () => {
        const h = harness(); let reads = 0;
        h.fixture.fetch = async () => ({ json: () => { reads++; return Promise.resolve({}); } });
        const controller = new AbortController(); h.context.external = controller;
        const result = await h.evaluate('fetchComTimeout("/cancel", {signal:external.signal})'); controller.abort();
        await assert.rejects(result.json(), { name: 'AbortError' }); assert.equal(reads, 0);
    });
    await check('an expired deadline aborts the transport before starting a late body consumer', async () => {
        const h = harness(); let reads = 0;
        h.fixture.fetch = async () => ({ json: () => { reads++; return Promise.resolve({}); } });
        const result = await h.evaluate('fetchComTimeout("/late", {}, 100)'); await h.clock.tick(101);
        await assert.rejects(result.json(), { name: 'TimeoutError' });
        assert.equal(reads, 0); assert(h.fixture.calls[0].options.signal.aborted);
    });
    await check('native Response JSON, text and blob consumers retain their receiver', async () => {
        for (const method of ['json', 'text', 'blob', 'arrayBuffer']) {
            const h = harness(); h.fixture.fetch = async () => new Response('{"literal":"001"}', { status: 201 });
            const result = await h.evaluate('fetchComTimeout("/native")');
            assert.equal(result.status, 201); const body = await result[method]();
            if (method === 'json') assert.deepEqual(body, { literal: '001' });
            if (method === 'text') assert.equal(body, '{"literal":"001"}');
            assert.equal(h.clock.timers.size, 0);
        }
    });
    await check('progress times out, releases single-flight and retries without a loop', async () => {
        const h = harness(); h.fixture.fetch = () => new Promise(() => {});
        h.evaluate('vendasSyncMonitor.start()'); await flush();
        assert.equal(h.fixture.calls.length, 1);
        await h.clock.tick(15000); assert.equal(h.evaluate('vendasSyncMonitor.isRequestInFlight()'), false);
        await h.clock.tick(2999); assert.equal(h.fixture.calls.length, 1);
        await h.clock.tick(1); assert.equal(h.fixture.calls.length, 2);
        h.evaluate('vendasSyncMonitor.stop()'); await flush(); assert.equal(h.clock.timers.size, 0);
    });
    await check('summary body timeout ends loading and leaves explicit error during active sync', async () => {
        const h = harness(); h.fixture.fetch = async () => ({ ok: true, json: () => new Promise(() => {}) });
        const request = h.evaluate('carregarVendas()'); await flush(); await h.clock.tick(45000); await request;
        h.evaluate('aplicarEstadoMonitorSyncVendas({active:true,automacao:{jobs:[],lojas:[]}})');
        assert.equal(h.snapshot().loading, false); assert.match(h.snapshot().loadError, /tempo de resposta/);
        assert.match(h.node('status').className, /error/); assert.equal(h.fixture.rows.length, 0);
    });
    await check('graph body timeout is an error rather than permanent loading', async () => {
        const h = harness(); h.fixture.fetch = async () => ({ ok: true, json: () => new Promise(() => {}) });
        const request = h.evaluate('carregarGrafico()'); await flush(); await h.clock.tick(45000); await request;
        assert.equal(h.snapshot().graphLoading, false); assert.match(h.snapshot().graphError, /tempo de resposta/);
        assert.match(h.node('graficoObservacoesVendas').textContent, /tempo de resposta/);
    });
    await check('the graph accepts a valid 21 second response within its 45 second budget', async () => {
        const h = harness(), wait = deferred(); h.fixture.fetch = () => wait.promise;
        const request = h.evaluate('carregarGrafico()'); await h.clock.tick(21300);
        wait.resolve(response({ labels: ['2026-10'], valores_vendas: [123.45] })); await request;
        assert.equal(h.fixture.graphs.length, 1); assert.equal(h.snapshot().graphError, ''); assert.equal(h.snapshot().graphLoading, false);
    });
    await check('the year comparison shares the complete graph deadline', async () => {
        const h = harness(), first = deferred(); h.evaluate('compararAnoPassado = true');
        h.fixture.fetch = url => url.includes('data_inicio=2026') ? first.promise : Promise.resolve({ ok: true, json: () => new Promise(() => {}) });
        const request = h.evaluate('carregarGrafico()'); await h.clock.tick(30000);
        first.resolve(response({ labels: [], valores_vendas: [] })); await flush();
        await h.clock.tick(14999); assert.equal(h.snapshot().graphLoading, true);
        await h.clock.tick(1); await request; assert.equal(h.snapshot().graphLoading, false); assert(h.snapshot().graphError);
    });
    await check('retry actions in both layouts only read data and preserve the retry during active sync', async () => {
        for (const legacy of [false, true]) {
            const h = harness(); h.fixture.fetch = async () => ({ ok: false, status: 503 }); await h.evaluate('carregarVendas()');
            const button = h.node('status').querySelector('[data-vendas-read-retry]'); assert(button);
            h.evaluate('aplicarEstadoMonitorSyncVendas({active:true,automacao:{jobs:[],lojas:[]}})');
            assert.equal(h.node('status').querySelector('[data-vendas-read-retry]'), button);
            h.fixture.fetch = async url => response(url.includes('/resumo') ? { rows: [{ sku: '001', quantidade: 2, valor: 12.34 }], totals: { valor: 12.34 } } : { labels: [], valores_vendas: [] });
            const before = h.fixture.calls.length;
            if (legacy) { button.listeners.click(); await flush(); }
            else await h.window.JKVendasLegacyBridge.retryReads();
            assert.deepEqual(h.fixture.calls.slice(before).map(call => call.url.split('?')[0]).sort(), ['/api/vendas/grafico', '/api/vendas/resumo']);
            assert(h.fixture.calls.every(call => !call.options.method || call.options.method === 'GET'));
            assert.equal(h.fixture.rows[0][0].sku, '001'); assert.equal(h.fixture.rows[0][0].valor, 12.34);
            assert.equal(h.snapshot().loadError, ''); assert.equal(h.snapshot().loading, false);
        }
    });
    await check('HTTP errors and malformed JSON end loading for both reads', async () => {
        for (const malformed of [false, true]) {
            const h = harness();
            h.fixture.fetch = async () => malformed ? { ok: true, json: async () => { throw new SyntaxError('bad JSON'); } } : { ok: false, status: 503 };
            await Promise.all([h.evaluate('carregarVendas()'), h.evaluate('carregarGrafico()')]);
            assert.equal(h.snapshot().loading, false); assert.equal(h.snapshot().graphLoading, false);
            assert(h.snapshot().loadError); assert(h.snapshot().graphError);
        }
    });
    await check('same-context refresh keeps confirmed rows and chart visible while updating', async () => {
        const h = harness(); await Promise.all([h.evaluate('carregarVendas()'), h.evaluate('carregarGrafico()')]);
        const wait = deferred(); h.fixture.fetch = () => wait.promise;
        const requests = [h.evaluate('carregarVendas()'), h.evaluate('carregarGrafico()')]; await flush();
        const snapshot = h.snapshot();
        assert.equal(snapshot.loading, false); assert.equal(snapshot.graphLoading, false);
        assert.equal(snapshot.updating, true); assert.equal(snapshot.graphUpdating, true);
        assert.equal(snapshot.rows[0].sku, '001'); assert.equal(snapshot.graph.data.valores_vendas[0], 12.34);
        wait.resolve(response({ rows: [], labels: [] })); await Promise.all(requests);
    });
    await check('filter roundtrip rejects old summary and graph responses', async () => {
        const h = harness(), waiting = [];
        h.fixture.fetch = (url, options) => { const d = deferred(); waiting.push({ ...d, url, options }); return d.promise; };
        h.evaluate("lojaSelecionada='Loja A'"); const first = [h.evaluate('carregarVendas()'), h.evaluate('carregarGrafico()')]; await flush();
        h.evaluate("lojaSelecionada='Loja B'"); const second = [h.evaluate('carregarVendas()'), h.evaluate('carregarGrafico()')]; await flush();
        h.evaluate("lojaSelecionada='Loja A'"); const current = [h.evaluate('carregarVendas()'), h.evaluate('carregarGrafico()')]; await flush();
        for (let i = waiting.length - 1; i >= 0; i--) waiting[i].resolve(response(waiting[i].url.includes('/resumo') ? { rows: [{ sku: i >= 4 ? 'fresh' : 'stale' }] } : { label: i >= 4 ? 'fresh' : 'stale' }));
        await Promise.all([...first, ...second, ...current]);
        assert.equal(h.fixture.rows.length, 1); assert.equal(h.fixture.rows[0][0].sku, 'fresh');
        assert.equal(h.fixture.graphs.length, 1); assert.equal(h.fixture.graphs[0].label, 'fresh');
        assert(waiting.slice(0, 4).every(r => r.options.signal.aborted));
    });
    await check('same-tenant session change aborts old reads and permits a fresh request', async () => {
        const h = harness(), old = deferred(); h.fixture.fetch = () => old.promise;
        const request = h.evaluate('carregarVendas()'); await flush();
        h.window.dispatchEvent({ type: 'storage', key: 'access_token' });
        h.fixture.fetch = async () => response({ rows: [{ sku: 'new-session' }] }); await h.evaluate('carregarVendas()');
        old.resolve(response({ rows: [{ sku: 'old-session' }] })); await request;
        assert.equal(h.fixture.rows.length, 1); assert.equal(h.fixture.rows[0][0].sku, 'new-session');
    });
    for (const changed of ['unit', 'dates']) {
        await check('changing ' + changed + ' cancels a stalled old body and rejects stale values', async () => {
            const h = harness(), old = deferred();
            h.fixture.fetch = async () => ({ ok: true, json: () => old.promise });
            const requests = [h.evaluate('carregarVendas()'), h.evaluate('carregarGrafico()')]; await flush();
            if (changed === 'unit') h.node('unidadeNegocioSelect').value = 'Virtual 2'; else h.fixture.start = '2026-10-10';
            h.fixture.fetch = async url => response(url.includes('/resumo') ? { rows: [{ sku: 'fresh-filter' }] } : { marker: 'fresh-filter' });
            await Promise.all([h.evaluate('carregarVendas()'), h.evaluate('carregarGrafico()')]);
            old.resolve({ rows: [{ sku: 'stale-filter' }], marker: 'stale-filter' }); await Promise.all(requests);
            assert.equal(h.fixture.rows.length, 1); assert.equal(h.fixture.rows[0][0].sku, 'fresh-filter');
            assert.equal(h.fixture.graphs.length, 1); assert.equal(h.fixture.graphs[0].marker, 'fresh-filter');
        });
    }
    await check('tenant change discards old progress and business responses', async () => {
        const h = harness(), old = deferred(); h.fixture.fetch = () => old.promise;
        const read = h.evaluate('carregarVendas()'), progress = h.evaluate('vendasSyncMonitor.refresh()'); await flush();
        h.fixture.tenant = 'tenant-b';
        h.fixture.fetch = async url => response(url.includes('/sync/progress') ? { active: false, marker: 'tenant-b' } : { rows: [{ sku: 'tenant-b' }] });
        await h.evaluate('vendasSyncMonitor.refresh()'); await h.evaluate('carregarVendas()');
        old.resolve(response({ active: true, rows: [{ sku: 'tenant-a' }] })); await Promise.all([read, progress]);
        assert.equal(h.evaluate('vendasSyncMonitor.getLastPayload().marker'), 'tenant-b');
        assert.equal(h.fixture.rows.length, 1); assert.equal(h.fixture.rows[0][0].sku, 'tenant-b');
    });
    await check('pagehide cancels reads, progress and future refreshes', async () => {
        const h = harness(); h.fixture.fetch = () => new Promise(() => {});
        const reads = [h.evaluate('carregarVendas()'), h.evaluate('carregarGrafico()'), h.evaluate('vendasSyncMonitor.refresh()')];
        await flush(); h.window.dispatchEvent({ type: 'pagehide' }); await Promise.all(reads);
        await h.evaluate('carregarVendas()'); await h.evaluate('carregarGrafico()');
        assert.equal(h.fixture.calls.length, 3); assert.equal(h.clock.timers.size, 0);
        assert.equal(h.snapshot().loading, false); assert.equal(h.snapshot().graphLoading, false);
    });
    for (const interrupted of [false, true]) {
        await check('BFcache restores reads and monitor after pagehide, interrupted=' + interrupted, async () => {
            const h = harness(); await Promise.all([h.evaluate('carregarVendas()'), h.evaluate('carregarGrafico()')]);
            const beforeInitialShow = h.fixture.calls.length;
            h.window.dispatchEvent({ type: 'pageshow', persisted: false }); await flush();
            assert.equal(h.fixture.calls.length, beforeInitialShow, 'ordinary pageshow must not double the initial reads');
            let old = [];
            if (interrupted) {
                h.fixture.fetch = () => new Promise(() => {});
                old = [h.evaluate('carregarVendas()'), h.evaluate('carregarGrafico()')]; await flush();
            }
            h.window.dispatchEvent({ type: 'pagehide' }); await Promise.all(old);
            h.fixture.fetch = async url => response(url.includes('/sync/progress') ? { active: true, automacao: { jobs: [], lojas: [] } }
                : url.includes('/resumo') ? { rows: [{ sku: 'restored' }] } : { marker: 'restored' });
            const before = h.fixture.calls.length;
            h.window.dispatchEvent({ type: 'pageshow', persisted: true }); await flush();
            assert.equal(h.fixture.calls.length - before, 3);
            assert.equal(h.snapshot().loading, false); assert.equal(h.snapshot().graphLoading, false);
            assert.equal(h.snapshot().rows[0].sku, 'restored'); assert.equal(h.snapshot().graph.data.marker, 'restored');
            h.window.dispatchEvent({ type: 'pagehide' });
        });
    }

    const payload = (days, options = {}) => ({ active: true, automacao: { lojas: [], jobs: [{ id: 'job-a', loja: 'Loja A', store_id: 'a', data_inicio: '2026-10-01', data_fim: '2026-10-20', status: 'running', dias_concluidos: days, ...options }] } });
    await check('baseline and uncommitted checkpoints do not reload; confirmed days coalesce during continuous sync', async () => {
        const h = harness(); h.context.nextPayload = payload(['2026-10-01']); h.evaluate('atualizadorConfirmadoVendas.observe(nextPayload)');
        await h.clock.tick(5000); assert.equal(h.fixture.calls.length, 0);
        h.context.nextPayload = payload(['2026-10-01'], { checkpoint: { atualizado_em: 'later', documentos_salvos: 200 } }); h.evaluate('atualizadorConfirmadoVendas.observe(nextPayload)');
        await h.clock.tick(5000); assert.equal(h.fixture.calls.length, 0);
        for (const days of [['2026-10-01', '2026-10-02'], ['2026-10-01', '2026-10-02', '2026-10-03']]) {
            h.context.nextPayload = payload(days); h.evaluate('atualizadorConfirmadoVendas.observe(nextPayload)');
        }
        await h.clock.tick(4999); assert.equal(h.fixture.calls.length, 0); await h.clock.tick(1);
        assert.equal(h.fixture.rows.length, 1); assert.equal(h.fixture.graphs.length, 1); assert.equal(h.fixture.calls.length, 2);
        await h.clock.tick(30000); assert.equal(h.fixture.calls.length, 2);
    });
    await check('progress while refreshing produces one follow-up without concurrent or endless reloads', async () => {
        const h = harness(), pending = deferred(); h.context.nextPayload = payload([]); h.evaluate('atualizadorConfirmadoVendas.observe(nextPayload)');
        h.fixture.fetch = () => pending.promise;
        h.context.nextPayload = payload(['2026-10-01']); h.evaluate('atualizadorConfirmadoVendas.observe(nextPayload)'); await h.clock.tick(5000);
        assert.equal(h.fixture.calls.length, 2);
        h.context.nextPayload = payload(['2026-10-01', '2026-10-02']); h.evaluate('atualizadorConfirmadoVendas.observe(nextPayload)'); await h.clock.tick(5000);
        assert.equal(h.fixture.calls.length, 2);
        pending.resolve(response({ rows: [], labels: [] })); await flush();
        h.fixture.fetch = async () => response({ rows: [], labels: [] }); await h.clock.tick(5000);
        assert.equal(h.fixture.calls.length, 4); await h.clock.tick(30000); assert.equal(h.fixture.calls.length, 4);
    });
    await check('confirmed refresh respects selected store, dates, changed filters and tenant', async () => {
        const h = harness(); h.evaluate("lojaSelecionada='Loja B'"); h.context.nextPayload = payload([]); h.evaluate('atualizadorConfirmadoVendas.observe(nextPayload)');
        h.context.nextPayload = payload(['2026-10-01']); h.evaluate('atualizadorConfirmadoVendas.observe(nextPayload)'); await h.clock.tick(5000); assert.equal(h.fixture.calls.length, 0);
        h.evaluate("lojaSelecionada='Loja A'"); h.evaluate('atualizadorConfirmadoVendas.observe(nextPayload)');
        h.context.nextPayload = payload(['2026-10-01', '2026-10-02']); h.evaluate('atualizadorConfirmadoVendas.observe(nextPayload)');
        h.fixture.tenant = 'tenant-b'; await h.clock.tick(5000); assert.equal(h.fixture.calls.length, 0);
        h.evaluate('atualizadorConfirmadoVendas.observe(nextPayload)');
        h.context.nextPayload = payload(['2026-10-01', '2026-10-02', '2026-10-03']); h.evaluate('atualizadorConfirmadoVendas.observe(nextPayload)');
        h.fixture.start = '2026-10-10'; await h.clock.tick(5000); assert.equal(h.fixture.calls.length, 0);
    });
    await check('ending an old tenant refresh does not schedule an unsolicited reload in the new tenant', async () => {
        const h = harness(); h.context.nextPayload = payload([]); h.evaluate('atualizadorConfirmadoVendas.observe(nextPayload)');
        h.fixture.fetch = () => new Promise(() => {});
        h.context.nextPayload = payload(['2026-10-01']); h.evaluate('atualizadorConfirmadoVendas.observe(nextPayload)'); await h.clock.tick(5000);
        assert.equal(h.fixture.calls.length, 2);
        h.fixture.tenant = 'tenant-b'; h.window.dispatchEvent({ type: 'storage', key: 'user_data' });
        h.context.nextPayload = payload([]); h.evaluate('atualizadorConfirmadoVendas.observe(nextPayload)');
        await flush(); await h.clock.tick(5000); assert.equal(h.fixture.calls.length, 2);
        h.fixture.fetch = async () => response({ rows: [], labels: [] });
        h.context.nextPayload = payload(['2026-10-01']); h.evaluate('atualizadorConfirmadoVendas.observe(nextPayload)'); await h.clock.tick(5000);
        assert.equal(h.fixture.calls.length, 4);
    });
    await check('cache from another tenant is rejected before reading business rows', async () => {
        const h = harness();
        h.context.sessionStorage.getItem = () => JSON.stringify({ client_id: 'tenant-b', ts: h.clock.now, dados: [{ sku: 'secret' }] });
        h.evaluate(period.slice(period.indexOf('async function restaurarCacheTelaVendasSeValido()'), period.indexOf('function preencherMeses()')));
        assert.equal(await h.evaluate('restaurarCacheTelaVendasSeValido()'), false); assert.equal(h.fixture.rows.length, 0);
    });
    await check('a scheduled cache write cannot relabel old data after a filter or session change', async () => {
        for (const changed of ['tenant', 'dates', 'session']) {
            const h = harness(), background = [], cached = [];
            await h.evaluate('carregarVendas()');
            h.context.sessionStorage.setItem = (_key, value) => cached.push(JSON.parse(value));
            h.evaluate(period.slice(period.indexOf('function salvarCacheTelaVendas('), period.indexOf('async function restaurarCacheTelaVendasSeValido()')));
            h.evaluate('salvarCacheTelaVendas()'); assert.equal(cached.length, 1); assert.equal(cached[0].dados[0].sku, '001'); cached.length = 0;
            h.context.deferCache = fn => background.push(fn);
            h.evaluate('agendarSegundoPlano = fn => deferCache(fn); agendarSalvarCacheTelaVendas()');
            await h.clock.tick(180); assert.equal(background.length, 1);
            if (changed === 'tenant') { h.fixture.tenant = 'tenant-b'; h.evaluate('clientId = obterClientId()'); }
            if (changed === 'dates') h.fixture.start = '2026-10-10';
            if (changed === 'session') h.window.dispatchEvent({ type: 'storage', key: 'access_token' });
            background[0](); h.evaluate('salvarCacheTelaVendas()');
            assert.equal(cached.length, 0, changed + ' must not write old rows into the new scope');
        }
    });
    console.log(`vendas_loading_reliability: OK (${passed} behavioral scenarios)`);
}
main().catch(error => { console.error(error); process.exitCode = 1; });
