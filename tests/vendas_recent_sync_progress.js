'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const runtime = fs.readFileSync(path.join(root, 'static/vendas/runtime.js'), 'utf8');
const period = fs.readFileSync(path.join(root, 'static/vendas/periodo-cache.js'), 'utf8');
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };

class Clock {
    constructor() { this.now = 0; this.sequence = 0; this.timers = new Map(); }
    set(fn, delay) { const id = ++this.sequence; this.timers.set(id, { at: this.now + delay, fn }); return id; }
    async tick(delay) {
        const end = this.now + delay;
        for (;;) {
            const next = [...this.timers].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
            if (!next) break;
            this.now = next[1].at; this.timers.delete(next[0]); next[1].fn(); await flush();
        }
        this.now = end; await flush();
    }
}

function harness() {
    const clock = new Clock(), calls = [], events = [], elements = new Map();
    const fixture = { tenant: 'tenant-a', start: '2026-10-01', end: '2026-10-26', payload: { active: false }, reads: 0 };
    const element = id => {
        if (!elements.has(id)) elements.set(id, { innerHTML: '', textContent: '', className: '', disabled: false, querySelectorAll: () => [] });
        return elements.get(id);
    };
    const context = vm.createContext({
        console, AbortController, DOMException, Date, fixture,
        setTimeout: (fn, delay) => clock.set(fn, delay), clearTimeout: id => clock.timers.delete(id),
        document: { visibilityState: 'visible', getElementById: element, addEventListener() {} },
        window: { addEventListener() {}, dispatchEvent: event => events.push(event) },
        CustomEvent: class { constructor(type, options = {}) { this.type = type; this.detail = options.detail; } },
        obterClientId: () => fixture.tenant, getDataIniISO: () => fixture.start, getDataFimISO: () => fixture.end,
        lojaSelecionada: '__todas', unidadeNegocioSelect: { value: '__todos' },
        syncController: null, syncEmAndamento: false, spinnerHtml: '', statusEl: element('status'),
        btnSync: element('btnSync'), btnCancel: element('btnCancel'),
        obterAuthHeaders: () => ({}), corrigirTextoVendas: value => String(value ?? ''),
        escaparHtmlVendas: value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;'),
        mesmaLoja: (left, right) => left === right, formatDate: value => value,
        fetchComTimeout: async () => { fixture.reads += 1; return { ok: true, json: async () => fixture.payload }; },
        carregarVendas: async () => { calls.push({ kind: 'cards', at: clock.now, start: fixture.start, end: fixture.end }); },
        carregarGrafico: async () => { calls.push({ kind: 'graph', at: clock.now, start: fixture.start, end: fixture.end }); },
    });
    vm.runInContext(runtime.slice(runtime.indexOf('function criarMonitorSyncVendas()'), runtime.indexOf('const vendasSyncMonitor =')), context);
    context.vendasSyncMonitor = context.criarMonitorSyncVendas();
    vm.runInContext(period.slice(period.indexOf('function setSyncButtons('), period.indexOf('// Funções utilitárias')), context);
    return { clock, calls, events, fixture, context, elements, observe: payload => vm.runInContext('atualizadorConfirmadoVendas', context).observe(payload) };
}

function payload(revision, status = 'running', days = ['2026-10-26']) {
    return {
        active: status === 'running', data_revision: revision,
        progress: { etapa: 'Dia', percentual: 100, concluido: true },
        automacao: {
            pendentes: status === 'complete' ? 0 : 1, dias_confirmados: days.length, dias_total: 26,
            lojas: [{ loja: 'Conta A', status, proximo_dia: '2026-10-25', dias_concluidos: days.length, dias_total: 26 }],
            jobs: [{ id: 'job-a', loja: 'Conta A', store_id: 'store-a', status, data_inicio: '2026-10-01', data_fim: '2026-10-26', dias_concluidos: days }],
        },
    };
}

(async () => {
    const rounds = harness(), monitor = rounds.context.vendasSyncMonitor;
    rounds.fixture.payload = payload(0); await monitor.refresh();
    rounds.fixture.payload = payload(1, 'queued'); await monitor.refresh();
    assert.equal(rounds.events.filter(event => event.type === 'jk:vendas-sync-finished').length, 0, 'devolver a conta à fila não conclui o período');
    rounds.context.aplicarEstadoMonitorSyncVendas(rounds.fixture.payload);
    assert.equal(rounds.context.syncEmAndamento, true);
    assert.equal(rounds.elements.get('btnSync').disabled, true, 'uma rodada na fila continua sendo a atualização solicitada');
    assert.match(rounds.elements.get('status').innerHTML, /Aguardando início · 3%/);
    assert.doesNotMatch(rounds.elements.get('status').innerHTML, /100%/);
    assert.match(rounds.elements.get('syncPersistenteStatus').innerHTML, /Próximo dia: 2026-10-25/);
    monitor.start(); await flush(); const reads = rounds.fixture.reads;
    await rounds.clock.tick(2999); assert.equal(rounds.fixture.reads, reads);
    await rounds.clock.tick(1); assert.equal(rounds.fixture.reads, reads + 1, 'a transição entre dias mantém o acompanhamento a cada três segundos');
    rounds.fixture.payload = payload(2, 'running'); await monitor.refresh();
    rounds.fixture.payload = payload(2, 'retry'); await monitor.refresh();
    assert.equal(rounds.events.filter(event => event.type === 'jk:vendas-sync-finished').length, 0, 'nova tentativa pendente não pode anunciar conclusão');
    rounds.fixture.payload = payload(2, 'running'); await monitor.refresh();
    rounds.fixture.payload = payload(3, 'complete'); await monitor.refresh();
    assert.equal(rounds.events.filter(event => event.type === 'jk:vendas-sync-finished').length, 1);
    await monitor.refresh(); assert.equal(rounds.events.filter(event => event.type === 'jk:vendas-sync-finished').length, 1);
    monitor.stop();

    const commits = harness();
    commits.observe(payload(10)); commits.observe(payload(11)); commits.observe(payload(12));
    await commits.clock.tick(4999); assert.equal(commits.calls.length, 0);
    await commits.clock.tick(1); assert.deepEqual(commits.calls.map(call => call.kind), ['cards', 'graph']);
    commits.observe(payload(12)); await commits.clock.tick(10000); assert.equal(commits.calls.length, 2, 'repetir o mesmo progresso não dispara outra leitura');
    commits.observe(payload(13)); await commits.clock.tick(4999); assert.equal(commits.calls.length, 2);
    await commits.clock.tick(1); assert.equal(commits.calls.length, 4, 'revisão do mesmo dia confirmado deve atualizar cartões e gráfico');
    assert(commits.calls[2].at - commits.calls[0].at >= 5000);

    const staging = harness();
    staging.observe(payload(undefined, 'running', []));
    const staged = payload(undefined, 'queued', []); staged.automacao.jobs[0].checkpoint = { documentos_salvos: 10 };
    staging.observe(staged); await staging.clock.tick(10000); assert.equal(staging.calls.length, 0, 'documentos preparados não comprovam gravação do dia');
    staging.observe(payload(undefined, 'queued', ['2026-10-26'])); await staging.clock.tick(5000); assert.equal(staging.calls.length, 2, 'servidores sem revisão mantêm a atualização por confirmação de dia');

    const hidden = harness();
    hidden.observe(payload(1)); hidden.observe(payload(2)); hidden.context.document.visibilityState = 'hidden';
    await hidden.clock.tick(5000); assert.equal(hidden.calls.length, 0);
    hidden.context.document.visibilityState = 'visible'; hidden.observe(payload(2));
    await hidden.clock.tick(5000); assert.equal(hidden.calls.length, 2, 'retornar à tela conserva a atualização confirmada durante a ocultação');

    const filters = harness(); filters.observe(payload(1)); filters.observe(payload(2));
    filters.fixture.start = '2026-10-20'; filters.observe(payload(2));
    await filters.clock.tick(5000); assert.equal(filters.calls.length, 0, 'trocar o período cancela a leitura agendada do filtro anterior');
    filters.observe(payload(3)); await filters.clock.tick(5000);
    assert(filters.calls.every(call => call.start === '2026-10-20'));

    const finishing = harness();
    finishing.context.renderProgresso({ percentual: 100, dias_confirmados: 26, dias_total: 26, concluido: false }, [], finishing.elements.get('status'));
    assert.match(finishing.elements.get('status').innerHTML, /99%/);
    assert.doesNotMatch(finishing.elements.get('status').innerHTML, /100%/, 'a manutenção final ainda pendente não pode mostrar o período concluído');
    console.log('vendas recent sync progress: ok');
})().catch(error => { console.error(error); process.exitCode = 1; });
