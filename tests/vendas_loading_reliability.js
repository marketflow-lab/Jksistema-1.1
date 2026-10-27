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
            addEventListener(type, listener) {
                const previous = this.listeners[type];
                this.listeners[type] = previous ? function(event) { previous.call(this, event); listener.call(this, event); } : listener;
            },
            appendChild(child) { this.children.push(child); },
            querySelector() { return this.children.find(child => 'vendasReadRetry' in (child.dataset || {})) || null; }, querySelectorAll: () => [],
            getContext() { return { canvas: this }; },
            classList: {
                toggle(name, enabled) {
                    const classes = new Set(node(id).className.split(/\s+/).filter(Boolean));
                    const active = enabled ?? !classes.has(name);
                    if (active) classes.add(name); else classes.delete(name);
                    node(id).className = [...classes].join(' ');
                    return active;
                },
                add(...names) { names.forEach(name => this.toggle(name, true)); },
                remove(...names) { names.forEach(name => this.toggle(name, false)); },
            },
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
        Chart: class {
            constructor(ctx, config) { this.canvas = ctx.canvas; this.config = config; this.destroyed = false; }
            destroy() { this.destroyed = true; }
        },
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
        function renderizarGrafico(payload){
            fixture.graphs.push(payload);
            renderizarGraficoEstoque(payload.labels, payload.estoque_geral, payload.estoque_sku,
                payload.quantidades_vendas, false, payload.estoque_meta);
            renderizarGraficoSkusComEstoque(payload.labels, payload.estoque_skus_com_saldo,
                payload.estoque_skus_pareto_com_saldo, false, payload.estoque_meta);
        }
        function formatarLabelDataGrafico(label){return label;}
        function atualizarObservacoesGraficoVendas(){} function carregarOciososSidebarVendas(){}
        function salvarPreferenciaGrafico(){}
        function periodoGraficoIncluiMesAtual(){return false;}
        function mesmaLoja(a,b){return String(a).trim().toLowerCase() === String(b).trim().toLowerCase();}
        function formatDate(value){return value;}
    `, context);
    vm.runInContext(data.slice(data.indexOf('function aplicarResumoVendas('), data.indexOf("filtroTexto.addEventListener('input'")), context);
    vm.runInContext(graph.slice(graph.indexOf('function prepararConsultaGraficoVendas()'), graph.indexOf('function compactarDadosGrafico(')), context);
    vm.runInContext(graph.slice(graph.indexOf('function renderizarGraficoEstoque('), graph.indexOf('async function obterLimitesComVendasServidor(')), context);
    vm.runInContext(graph.slice(graph.indexOf('function atualizarSkuGraficoPeloInput()')), context);
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
    const stockPayload = value => ({
        labels: ['2026-10-01'], valores_vendas: [12.34], quantidades_vendas: [2],
        estoque_geral: [value], estoque_skus_com_saldo: [3],
        estoque_skus_pareto_com_saldo: [2], estoque_meta: { lojas: 2, lojas_com_historico: 1 },
    });
    for (const failure of ['timeout', 'http', 'json']) {
        await check('stock and coverage end ' + failure + ' and recover on a successful retry', async () => {
            const h = harness();
            for (const id of ['graficoEstoqueResumo', 'graficoSkusEstoqueResumo']) h.node(id).textContent = 'Carregando...';
            h.fixture.fetch = async () => failure === 'http' ? { ok: false, status: 503 }
                : { ok: true, json: failure === 'timeout' ? () => new Promise(() => {}) : async () => { throw new SyntaxError('bad JSON'); } };
            const request = h.evaluate('carregarGrafico()'); await flush();
            if (failure === 'timeout') await h.clock.tick(45000);
            await request;
            assert.equal(h.snapshot().graphLoading, false); assert(h.snapshot().graphError);
            for (const [summary, state, canvas] of [
                ['graficoEstoqueResumo', 'graficoEstoqueEstado', 'graficoEstoque'],
                ['graficoSkusEstoqueResumo', 'graficoSkusEstoqueEstado', 'graficoSkusEstoque'],
            ]) {
                assert.doesNotMatch(h.node(summary).textContent, /Carregando/);
                assert.equal(h.node(state).hidden, false); assert.match(h.node(state).textContent, /gráfico/);
                assert.equal(h.node(canvas).hidden, true);
            }
            h.fixture.fetch = async () => response(stockPayload(12));
            await h.evaluate('carregarGrafico()');
            assert.equal(h.snapshot().graphError, ''); assert.equal(h.snapshot().graphLoading, false);
            assert.match(h.node('graficoEstoqueResumo').textContent, /Atual 12 un\./);
            assert.match(h.node('graficoSkusEstoqueResumo').textContent, /Atual 3 SKU/);
            for (const id of ['graficoEstoqueEstado', 'graficoSkusEstoqueEstado']) assert.equal(h.node(id).hidden, true);
            for (const id of ['graficoEstoque', 'graficoSkusEstoque']) assert.equal(h.node(id).hidden, false);
            assert.equal(h.clock.timers.size, 0);
        });
    }
    await check('failed refresh preserves confirmed stock only in the same filter context', async () => {
        const h = harness(); h.fixture.fetch = async () => response(stockPayload(17));
        await h.evaluate('carregarGrafico()');
        const previous = h.evaluate('[chartEstoqueInstance, chartSkusEstoqueInstance]');
        const summary = h.node('graficoEstoqueResumo').textContent;
        h.fixture.fetch = async () => ({ ok: false, status: 503 });
        for (let attempt = 0; attempt < 2; attempt++) {
            await h.evaluate('carregarGrafico()');
            assert.equal(h.node('graficoEstoqueResumo').textContent, summary);
            assert(previous.every(chart => !chart.destroyed));
            assert.match(h.node('graficoEstoqueEstado').textContent, /confirmados.*mantidos/);
            assert.equal(h.node('graficoEstoque').hidden, false);
        }
        h.evaluate("lojaSelecionada='Loja B'");
        await h.evaluate('carregarGrafico()');
        assert(previous.every(chart => chart.destroyed));
        assert.doesNotMatch(h.node('graficoEstoqueResumo').textContent, /Atual 17/);
        assert.equal(h.node('graficoEstoque').hidden, true);
        assert.equal(h.snapshot().graph.data, null);
        h.fixture.fetch = async () => response(stockPayload(25));
        await h.evaluate('carregarGrafico()');
        assert.match(h.node('graficoEstoqueResumo').textContent, /Atual 25 un\./);
        assert.equal(h.node('graficoEstoque').hidden, false);
        assert.equal(h.node('graficoEstoqueEstado').hidden, true);
    });
    for (const change of ['metricaGrafico = "quantidade"', 'tipoGrafico = "barra"']) {
        await check('changing ' + change + ' replaces the pending read and reaches a terminal state', async () => {
            const h = harness(), old = deferred(), current = deferred();
            h.fixture.fetch = () => h.fixture.calls.length === 1 ? old.promise : current.promise;
            const first = h.evaluate('carregarGrafico()'); await flush();
            h.evaluate(change); const second = h.evaluate('carregarGrafico()'); await flush();
            assert.equal(h.fixture.calls.length, 2); assert(h.fixture.calls[0].options.signal.aborted);
            old.resolve(response(stockPayload(999))); current.resolve(response(stockPayload(23)));
            await Promise.all([first, second]); await h.clock.tick(50000);
            assert.equal(h.fixture.graphs.length, 1);
            assert.match(h.node('graficoEstoqueResumo').textContent, /Atual 23 un\./);
            assert.equal(h.snapshot().graphLoading, false); assert.equal(h.snapshot().graphError, '');
            assert.equal(h.evaluate('carregarGraficoPromise'), null); assert.equal(h.clock.timers.size, 0);
        });
    }
    await check('editing an inactive stock SKU keeps pending and confirmed graph data without another request', async () => {
        const h = harness(), pending = deferred(); h.fixture.fetch = () => pending.promise;
        const request = h.evaluate('carregarGrafico()'); await flush();
        const input = h.node('graficoEstoqueSkuInput'); input.value = '0001'; input.listeners.change.call(input);
        assert.equal(h.fixture.calls.length, 1); assert.equal(h.evaluate('mostrarEstoqueSkuGrafico'), false);
        pending.resolve(response(stockPayload(19))); await request; await h.clock.tick(50000);
        assert.equal(h.fixture.graphs.length, 1); assert.equal(h.snapshot().graphLoading, false);
        assert.equal(h.snapshot().graph.data.estoque_geral[0], 19); assert.equal(h.snapshot().graphError, '');
        assert.equal(h.snapshot().chartOptions.skuAtualGrafico, '0001'); assert.equal(input.value, '0001');
        input.value = '0002'; input.listeners.change.call(input); await flush();
        assert.equal(h.snapshot().graph.data.estoque_geral[0], 19); assert.equal(h.snapshot().graphLoading, false);
        assert.equal(h.snapshot().chartOptions.skuAtualGrafico, '0002');
        assert.equal(h.fixture.calls.length, 1); assert.equal(h.clock.timers.size, 0);
    });
    await check('editing an enabled stock SKU replaces the pending request and confirms the new SKU', async () => {
        const h = harness(), old = deferred(), current = deferred();
        h.evaluate('mostrarEstoqueSkuGrafico = true'); h.node('graficoEstoqueSkuInput').value = '0001';
        h.fixture.fetch = () => h.fixture.calls.length === 1 ? old.promise : current.promise;
        const first = h.evaluate('carregarGrafico()'); await flush();
        const input = h.node('graficoEstoqueSkuInput'); input.value = '0002'; input.listeners.change.call(input); await flush();
        assert.equal(h.fixture.calls.length, 2); assert(h.fixture.calls[0].options.signal.aborted);
        assert.equal(new URLSearchParams(h.fixture.calls[1].url.split('?')[1]).get('sku'), '0002');
        old.resolve(response(stockPayload(999))); current.resolve(response(stockPayload(29)));
        await first; await flush(); await h.clock.tick(50000);
        assert.equal(h.fixture.graphs.length, 1); assert.equal(h.snapshot().graphLoading, false);
        assert.equal(h.snapshot().graph.data.estoque_geral[0], 29); assert.equal(h.snapshot().graphError, '');
        assert.equal(h.snapshot().chartOptions.skuAtualGrafico, '0002'); assert.equal(h.clock.timers.size, 0);
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
    for (const method of ['carregarVendas', 'carregarGrafico']) {
        await check('a 205 day all-store ' + method + ' read accepts valid headers after 65 seconds', async () => {
            const h = harness(), pending = deferred();
            h.fixture.start = '2026-04-01'; h.fixture.end = '2026-10-22';
            h.fixture.fetch = () => pending.promise;
            const request = h.evaluate(method + '()'); await h.clock.tick(65000);
            pending.resolve(response(method === 'carregarVendas'
                ? { rows: [{ sku: 'confirmed', quantidade: 2, valor: 12.34 }], totals: { valor: 12.34 } } : stockPayload(31)));
            await request;
            const snapshot = h.snapshot();
            if (method === 'carregarVendas') {
                assert.equal(h.fixture.rows.length, 1); assert.equal(snapshot.loading, false); assert.equal(snapshot.loadError, '');
            } else {
                assert.equal(h.fixture.graphs.length, 1); assert.equal(snapshot.graphLoading, false); assert.equal(snapshot.graphError, '');
            }
            assert.equal(h.fixture.calls.length, 1); assert.equal(h.clock.timers.size, 0);
        });
    }
    await check('205 day all-store bodies stop at 120 seconds and manual retry recovers without automatic loops', async () => {
        const h = harness(); h.fixture.start = '2026-04-01'; h.fixture.end = '2026-10-22';
        h.fixture.fetch = async () => ({ ok: true, json: () => new Promise(() => {}) });
        const requests = [h.evaluate('carregarVendas()'), h.evaluate('carregarGrafico()')]; await flush();
        await h.clock.tick(119999); assert.equal(h.snapshot().loading, true); assert.equal(h.snapshot().graphLoading, true);
        await h.clock.tick(1); await Promise.all(requests);
        assert.equal(h.snapshot().loading, false); assert.match(h.snapshot().loadError, /tempo de resposta/);
        assert.equal(h.snapshot().graphLoading, false); assert.match(h.snapshot().graphError, /tempo de resposta/);
        assert.equal(h.clock.timers.size, 0); await h.clock.tick(600000); assert.equal(h.fixture.calls.length, 2);
        h.fixture.fetch = async url => response(url.includes('/resumo')
            ? { rows: [{ sku: 'retry-confirmed', quantidade: 1, valor: 5 }], totals: { valor: 5 } } : stockPayload(37));
        await h.window.JKVendasLegacyBridge.retryReads();
        assert.equal(h.snapshot().loading, false); assert.equal(h.snapshot().graphLoading, false);
        assert.equal(h.snapshot().loadError, ''); assert.equal(h.snapshot().graphError, '');
        assert.equal(h.snapshot().rows[0].sku, 'retry-confirmed');
        assert.equal(h.snapshot().graph.data.estoque_geral[0], 37);
        assert.equal(h.fixture.calls.length, 4); await h.clock.tick(120000); assert.equal(h.fixture.calls.length, 4);
        assert(h.fixture.calls.every(call => !call.options.method || call.options.method === 'GET'));
        assert.equal(h.clock.timers.size, 0);
    });
    await check('a long all-store graph shares its 120 second deadline with the year comparison body', async () => {
        const h = harness(), first = deferred(); h.fixture.start = '2026-04-01'; h.fixture.end = '2026-10-22';
        h.evaluate('compararAnoPassado = true');
        h.fixture.fetch = url => url.includes('data_inicio=2026') ? first.promise
            : Promise.resolve({ ok: true, json: () => new Promise(() => {}) });
        const request = h.evaluate('carregarGrafico()'); await h.clock.tick(65000);
        first.resolve(response(stockPayload(41))); await flush(); assert.equal(h.fixture.calls.length, 2);
        await h.clock.tick(54999); assert.equal(h.snapshot().graphLoading, true);
        await h.clock.tick(1); await request;
        assert.equal(h.snapshot().graphLoading, false); assert.match(h.snapshot().graphError, /tempo de resposta/);
        assert.equal(h.fixture.graphs.length, 0); assert.equal(h.clock.timers.size, 0);
    });
    for (const scope of ['one-store-long', 'all-stores-short']) {
        await check(scope + ' summary and graph reads keep their 45 second deadline', async () => {
            const h = harness();
            if (scope === 'one-store-long') {
                h.fixture.start = '2026-04-01'; h.fixture.end = '2026-10-22'; h.evaluate("lojaSelecionada = 'Loja A'");
            }
            h.fixture.fetch = () => new Promise(() => {});
            const requests = [h.evaluate('carregarVendas()'), h.evaluate('carregarGrafico()')];
            await h.clock.tick(44999); assert.equal(h.snapshot().loading, true); assert.equal(h.snapshot().graphLoading, true);
            await h.clock.tick(1); await Promise.all(requests);
            assert.equal(h.snapshot().loading, false); assert.equal(h.snapshot().graphLoading, false);
            assert.match(h.snapshot().loadError, /tempo de resposta/); assert.match(h.snapshot().graphError, /tempo de resposta/);
            assert.equal(h.clock.timers.size, 0);
        });
    }
    await check('read budgets use explicit valid ISO dates and the inclusive 60 day boundary', async () => {
        const h = harness();
        for (const [start, end, store, expected] of [
            ['2026-04-01', '2026-05-30', '__todas', 45000],
            ['2026-04-01', '2026-05-31', '__todas', 120000],
            ['2026-04-01', '2026-10-22', 'Loja A', 45000],
            ['2026-04-31', '2026-10-22', '__todas', 45000],
            ['2026-10-22', '2026-04-01', '__todas', 45000],
            ['', '2026-10-22', '__todas', 45000],
        ]) assert.equal(h.evaluate(`obterPrazoLeituraVendasMs(${JSON.stringify(start)},${JSON.stringify(end)},${JSON.stringify(store)})`), expected);
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
        h.window.JKVendasCache = { contexto: () => ({}), dadosVigentes: () => true,
            ler: async () => ({ dados: { client_id: 'tenant-b', ts: h.clock.now, dados: [{ sku: 'secret' }] } }) };
        h.evaluate(period.slice(period.indexOf('function chaveCacheTelaVendas('), period.indexOf('function preencherMeses()')));
        assert.equal(await h.evaluate('restaurarCacheTelaVendasSeValido()'), false); assert.equal(h.fixture.rows.length, 0);
    });
    await check('a scheduled cache write cannot relabel old data after a filter or session change', async () => {
        for (const changed of ['tenant', 'dates', 'session']) {
            const h = harness(), background = [], cached = [];
            h.window.JKVendasCache = { contexto: () => ({}), vigente: () => true, dadosVigentes: () => true,
                ler: async () => null, estado() {}, salvar: async (_key, value) => { cached.push(JSON.parse(JSON.stringify(value))); return true; } };
            await h.evaluate('carregarVendas()');
            h.evaluate(period.slice(period.indexOf('function chaveCacheTelaVendas('), period.indexOf('async function restaurarCacheTelaVendasSeValido(')));
            await h.evaluate('salvarCacheTelaVendas()'); assert.equal(cached.length, 1); assert.equal(cached[0].dados[0].sku, '001'); cached.length = 0;
            h.context.deferCache = fn => background.push(fn);
            h.evaluate('agendarSegundoPlano = fn => deferCache(fn); agendarSalvarCacheTelaVendas()');
            await h.clock.tick(180); assert.equal(background.length, 1);
            if (changed === 'tenant') { h.fixture.tenant = 'tenant-b'; h.evaluate('clientId = obterClientId()'); }
            if (changed === 'dates') h.fixture.start = '2026-10-10';
            if (changed === 'session') h.window.dispatchEvent({ type: 'storage', key: 'access_token' });
            background[0](); await h.evaluate('salvarCacheTelaVendas()');
            assert.equal(cached.length, 0, changed + ' must not write old rows into the new scope');
        }
    });
    for (const failure of [false, true]) {
        await check('persistent summary appears before refresh and preserves composition, failed=' + failure, async () => {
            const h = harness(), pending = deferred(), saved = [], states = [];
            const cached = { client_id: 'tenant-a', data_inicio: h.fixture.start, data_fim: h.fixture.end,
                unidadeSelecionada: '__todos', dadosModoResumo: true, resumoTotaisVendas: { itens: 7 },
                dados: [{ sku: 'cached', quantidade: 7, itens: 7, itensDiretos: 2, itensComposicao: 5 }], devolucaoItens: [] };
            h.window.JKVendasCache = { contexto: () => ({}), vigente: () => true, dadosVigentes: () => true,
                ler: async () => ({ dados: cached, salvoEm: 100 }), estado: (...args) => states.push(args),
                salvar: async (key, value) => { saved.push({ key, value: JSON.parse(JSON.stringify(value)) }); return true; } };
            h.evaluate(period.slice(period.indexOf('function chaveCacheTelaVendas('), period.indexOf('function preencherMeses()')));
            h.fixture.fetch = () => pending.promise;
            const request = h.evaluate('carregarVendas()'); await flush();
            assert.equal(h.fixture.rows.at(-1)[0].sku, 'cached');
            assert.equal(h.fixture.rows.at(-1)[0].itensComposicao, 5);
            assert.equal(saved.length, 0, 'Reading a saved snapshot must not renew its timestamp');
            pending.resolve(failure ? { ok: false, status: 503 } : response({ rows: [{ sku: 'fresh', quantidade: 9, itensComposicao: 6 }], totals: { itens: 9 } }));
            await request;
            assert.equal(h.fixture.rows.at(-1)[0].sku, failure ? 'cached' : 'fresh');
            assert.equal(saved.length, failure ? 0 : 1);
            if (!failure) assert.equal(saved[0].value.dados[0].itensComposicao, 6);
            else assert.ok(states.some(([kind, state]) => kind === 'Lista' && state === 'erro'));
        });
    }
    await check('unavailable persistent identity still permits the normal network read', async () => {
        const h = harness();
        h.window.JKCachePersistente = { contexto: () => ({}), vigente: () => false,
            dadosVigentes: () => false, ler: () => { throw new Error('Unavailable cache must not be read'); } };
        h.evaluate(source('cache-dados'));
        h.evaluate(period.slice(period.indexOf('function chaveCacheTelaVendas('), period.indexOf('function preencherMeses()')));
        await h.evaluate('carregarVendas()');
        assert.equal(h.fixture.rows.at(-1)[0].sku, '001');
        assert.equal(h.fixture.calls.length, 1);
    });
    await check('a forbidden primary summary clears the shown snapshot and invalidates Vendas', async () => {
        const h = harness(), invalidated = [];
        h.window.JKCachePersistente = { invalidar: tags => invalidated.push(tags) };
        await h.evaluate('carregarVendas()');
        h.fixture.fetch = async () => ({ ok: false, status: 403 });
        await h.evaluate('carregarVendas()');
        assert.equal(h.evaluate('dados.length'), 0);
        assert.equal(h.snapshot().rows.length, 0);
        assert.equal(JSON.stringify(invalidated), JSON.stringify([['vendas']]));
        assert.match(h.node('status').textContent, /Acesso negado/);
    });
    await check('a failed yearly comparison preserves the complete saved chart without replacing its cache', async () => {
        const h = harness(), saved = [], states = [];
        h.evaluate('compararAnoPassado = true');
        h.window.JKVendasCache = { contexto: () => ({}), dadosVigentes: () => true,
            ler: async () => ({dados:{data:{labels:['saved'],valores_vendas:[20]},dataComparativo:{labels:['previous'],valores_vendas:[10]}},salvoEm:100}),
            estado: (...args) => states.push(args), salvar: async (...args) => saved.push(args) };
        h.fixture.fetch = async url => url.includes('2025-') ? {ok:false,status:503} : response({labels:['fresh'],valores_vendas:[30]});
        await h.evaluate('carregarGrafico()');
        assert.equal(h.fixture.graphs.length, 1);
        assert.equal(h.fixture.graphs[0].labels[0], 'saved');
        assert.equal(saved.length, 0);
        assert.ok(states.some(([kind,state]) => kind === 'Gráfico' && state === 'erro'));
    });
    await check('a malformed saved graph cannot prevent a normal network graph', async () => {
        const h = harness();
        h.window.JKVendasCache = { contexto: () => ({}), dadosVigentes: () => true,
            ler: async () => ({dados:{data:{labels:'invalid'}},salvoEm:100}), estado(){}, salvar:async()=>true };
        await h.evaluate('carregarGrafico()');
        assert.equal(h.fixture.calls.filter(call => call.url.includes('/grafico')).length, 1);
        assert.equal(h.fixture.graphs.length, 1);
        assert.equal(h.fixture.graphs[0].labels[0], '2026-10');
    });
    console.log(`vendas_loading_reliability: OK (${passed} behavioral scenarios)`);
}
main().catch(error => { console.error(error); process.exitCode = 1; });
