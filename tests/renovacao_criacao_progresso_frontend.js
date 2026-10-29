'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'renovacao.html'), 'utf8');
assert.strictEqual(html, fs.readFileSync(path.join(root, 'static', 'renovacao.html'), 'utf8'));
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(match => match[1]).join('\n');
assert.doesNotThrow(() => new Function(script));
assert(html.includes('role="progressbar"'));
assert(html.includes('<summary>Ver detalhes</summary>'));

const startUrl = '/api/renovacao/criar-proximo-mes/iniciar';
const pollUrl = '/api/renovacao/criar-proximo-mes/progresso/job-criacao';
const controls = ['renovacao-loja', 'renovacao-campanha', 'renovacao-nome',
    'btn-atualizar-campanhas-renovacao', 'btn-abrir-criacao-manual-renovacao', 'btn-criar-renovacao',
    'renovacao-sync-origem', 'renovacao-campanha-destino', 'btn-sincronizar-renovacao',
    'btn-conferir-renovacao', 'btn-corrigir-renovacao', 'btn-salvar-periodo-renovacao'];
const response = (body, ok = true) => ({ ok, async json() { return body; } });
const flush = () => new Promise(setImmediate);
function deferred() {
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
}
function element(id) {
    const attributes = {};
    return { id, value: '', textContent: '', className: '', innerHTML: '', disabled: false,
        hidden: false, open: false, style: {}, dataset: {}, attributes,
        setAttribute(name, value) { attributes[name] = value; },
        removeAttribute(name) { delete attributes[name]; },
        addEventListener() {}, querySelectorAll() { return []; },
        classList: { toggle() {} }, focus() {}, reset() {} };
}
function setup(queue = []) {
    const elements = new Map([...html.matchAll(/id="([^"]+)"/g)].map(match => [match[1], element(match[1])]));
    elements.get('renovacao-loja').value = 'Deckas';
    elements.get('renovacao-campanha').value = 'C-ORIGEM';
    elements.get('renovacao-sync-origem').value = 'C-ORIGEM';
    elements.get('renovacao-campanha-destino').value = 'C-DESTINO';
    elements.get('renovacao-nome').value = 'Promo Novembro';
    const rows = [element('periodo-linha'), element('deletar-linha'), element('agendar-linha')];
    const timers = [], intervals = new Map(), requests = [], confirmations = [], reloads = [], progressStyles = [];
    let intervalId = 0, inFlight = 0, maxInFlight = 0;
    Object.defineProperty(elements.get('renovacao-sync-progress'), 'className', {
        get() { return progressStyles.at(-1) || ''; }, set(value) { progressStyles.push(value); },
    });
    const sandbox = {
        console, Date,
        setInterval(callback) { const id = ++intervalId; intervals.set(id, callback); return id; },
        clearInterval(id) { intervals.delete(id); },
        document: {
            addEventListener() {}, getElementById(id) { return elements.get(id) || null; },
            querySelectorAll(selector) { return selector.includes('button,') ? rows : []; },
        },
        window: { setTimeout(callback, delay) { timers.push({ callback, delay }); return timers.length; } },
        obterAuthHeaders() { return { 'X-Test-Session': 'authenticated' }; },
        confirm(message) { confirmations.push(message); return true; },
        async fetch(url, options = {}) {
            requests.push({ url, options });
            inFlight += 1;
            maxInFlight = Math.max(maxInFlight, inFlight);
            try {
                assert(queue.length, `requisição inesperada: ${url}`);
                const next = queue.shift();
                if (next instanceof Error) throw next;
                return await next;
            } finally { inFlight -= 1; }
        },
    };
    vm.createContext(sandbox);
    vm.runInContext(script + `
        this.testState = {
            campaigns(value) { campanhasRenovacao = value; },
            get creation() { return renovacaoCriacaoState; },
            set creation(value) { renovacaoCriacaoState = value; },
            get report() { return ultimaConferenciaRenovacao; },
        };`, sandbox);
    sandbox.testState.campaigns([{ id: 'C-ORIGEM', name: 'Promo Outubro', active_count: 233,
        programmed_count: 0, promotion_type: 'SELLER_CAMPAIGN' }]);
    const loadCampaigns = sandbox.carregarCampanhasRenovacao;
    sandbox.carregarCampanhasRenovacao = async silent => { reloads.push(silent); };
    return { sandbox, elements, rows, timers, intervals, requests, confirmations, reloads, progressStyles,
        loadCampaigns, queue, get maxInFlight() { return maxInFlight; },
        async tick() { assert(timers.length, 'esperava próxima consulta agendada'); const timer = timers.shift();
            assert.strictEqual(timer.delay, 1000); timer.callback(); await flush(); } };
}
const fullResult = { success: true, nova_campanha: { id: 'C-NOVA', nome: 'Promo Novembro' },
    incluidos: 233, atualizados: 0, mlbs_ja_presentes: 0, falhas_total: 0,
    conferencia: { completa: true, confirmados: 233, total_origem: 233, pendentes: 0 } };

(async () => {
    // Preparação sem percentual inventado, progresso real e uma consulta por vez.
    const start = deferred(), slowPoll = deferred();
    const main = setup([start.promise,
        response({ status: 'running', etapa: 'Copia', mensagem: 'Copiando anúncios', percentual: 68, processados: 120, total: 233 }),
        slowPoll.promise, response({ status: 'done', result: fullResult })]);
    const originallyDisabled = main.elements.get('btn-salvar-periodo-renovacao');
    originallyDisabled.disabled = true;
    const run = main.sandbox.criarCampanhaMesSeguinte();
    const progress = main.elements.get('renovacao-sync-progress');
    const label = main.elements.get('renovacao-sync-progress-text');
    const percent = main.elements.get('renovacao-sync-progress-percent');
    const fill = main.elements.get('renovacao-sync-progress-fill');
    const track = main.elements.get('renovacao-sync-progress-track');
    assert.match(progress.className, /indeterminate/);
    assert.strictEqual(percent.textContent, '');
    assert.strictEqual(fill.style.width, '35%', 'preparação deve mostrar atividade sem afirmar uma porcentagem');
    assert.strictEqual(track.attributes['aria-valuenow'], undefined);
    for (const id of controls) assert(main.elements.get(id).disabled, `${id} deve ficar bloqueado`);
    assert(main.rows.every(row => row.disabled));
    assert.strictEqual(main.requests[0].url, startUrl);
    assert.strictEqual(main.requests[0].options.method, 'POST');
    assert.strictEqual(main.requests[0].options.headers['X-Test-Session'], 'authenticated');
    assert.deepStrictEqual(JSON.parse(main.requests[0].options.body), {
        loja: 'Deckas', campanha_id: 'C-ORIGEM', nome: 'Promo Novembro',
        promotion_type: 'SELLER_CAMPAIGN', quantidade_origem_esperada: 233,
    });
    await main.sandbox.criarCampanhaMesSeguinte();
    await main.sandbox.sincronizarPromocaoRenovacao();
    await main.sandbox.criarPromocaoManualRenovacao({ preventDefault() {} });
    await main.sandbox.deletarCampanhaRenovacao();
    await main.sandbox.alterarPeriodoCampanhaRenovacao();
    await main.sandbox.salvarAgendamentoLinhaRenovacao({ currentTarget: main.rows[2] });
    await main.loadCampaigns();
    assert.strictEqual(main.requests.length, 1, 'ações conflitantes não podem iniciar outra operação');
    start.resolve(response({ job_id: 'job-criacao' }));
    await flush();
    assert.strictEqual(main.requests[1].url, pollUrl);
    assert.strictEqual(main.requests[1].options.headers['X-Test-Session'], 'authenticated');
    assert.match(label.textContent, /120 de 233 anúncios/);
    assert.strictEqual(percent.textContent, '68%');
    assert.strictEqual(fill.style.width, '68%');
    assert.strictEqual(track.attributes['aria-valuenow'], '68');
    assert(!progress.className.includes('indeterminate'));
    await main.tick();
    assert.strictEqual(main.requests.length, 3);
    assert.strictEqual(main.timers.length, 0, 'consulta lenta não deve agendar outra consulta em paralelo');
    await flush();
    assert.strictEqual(main.requests.length, 3);
    slowPoll.resolve(response({ status: 'running', mensagem: 'Conferindo descontos 233/233', progress: 95,
        processados: 233, total: 233 }));
    await flush();
    assert.strictEqual(label.textContent, 'Conferindo descontos 233/233', 'contagem já presente não deve ser duplicada');
    assert.strictEqual(percent.textContent, '95%');
    await main.tick();
    await run;
    assert.strictEqual(main.maxInFlight, 1);
    assert.strictEqual(main.requests.filter(item => item.options.method === 'POST').length, 1);
    assert.match(progress.className, /ok.*finished/);
    assert.match(label.textContent, /Promo Novembro.*Concluído/);
    assert.match(main.elements.get('renovacao-sync-details-text').textContent, /233 de 233/);
    assert.strictEqual(main.elements.get('renovacao-sync-details').hidden, false);
    assert.strictEqual(main.elements.get('renovacao-sync-details').open, false);
    assert.strictEqual(main.elements.get('btn-baixar-conferencia-renovacao').style.display, '');
    assert.strictEqual(main.sandbox.testState.report, fullResult);
    assert.deepStrictEqual(main.reloads, [true], 'recarga silenciosa deve preservar o resultado');
    for (const id of controls.filter(id => id !== 'btn-salvar-periodo-renovacao')) assert.strictEqual(main.elements.get(id).disabled, false);
    assert.strictEqual(originallyDisabled.disabled, true, 'estado anterior do controle deve ser restaurado');
    assert(main.rows.every(row => !row.disabled));
    assert.strictEqual(main.sandbox.testState.creation, null);

    // Finalização com pendências mantém resumo/relatório, sem sucesso verde.
    const partial = setup([response({ job_id: 'job-criacao' }), response({ status: 'done', result: {
        ...fullResult, success: false, incluidos: 231, falhas_total: 2,
        conferencia: { completa: false, confirmados: 231, total_origem: 233, pendentes: 2 },
    } })]);
    await partial.sandbox.criarCampanhaMesSeguinte();
    assert(partial.progressStyles.every(style => !/\bok\b/.test(style)));
    assert.match(partial.elements.get('renovacao-sync-progress').className, /warning.*finished/);
    assert.match(partial.elements.get('renovacao-sync-progress-text').textContent, /pendências/);
    assert.match(partial.elements.get('renovacao-sync-result-stats').innerHTML, />2<.*anúncios pendentes/);
    assert.strictEqual(partial.elements.get('renovacao-sync-details').hidden, false);

    // Erro já na primeira consulta conserva o percentual informado pelo servidor.
    const failed = setup([response({ job_id: 'job-criacao' }),
        response({ status: 'error', error: 'Falha na cópia', percentual: 68 })]);
    await failed.sandbox.criarCampanhaMesSeguinte();
    assert.match(failed.elements.get('renovacao-sync-progress').className, /error/);
    assert.strictEqual(failed.elements.get('renovacao-sync-progress-percent').textContent, '68%');
    assert.strictEqual(failed.elements.get('renovacao-sync-progress-fill').style.width, '68%');
    assert.strictEqual(failed.reloads.length, 0);
    assert.strictEqual(failed.sandbox.testState.report, null);
    assert.strictEqual(failed.elements.get('btn-criar-renovacao').disabled, false);

    // Queda na consulta não repete o POST nem perde o último avanço real.
    const disconnected = setup([response({ job_id: 'job-criacao' }),
        response({ status: 'running', mensagem: 'Copiando anúncios', percentual: 40 }), new Error('Rede indisponível')]);
    const disconnectedRun = disconnected.sandbox.criarCampanhaMesSeguinte();
    await flush();
    await disconnected.tick();
    await disconnectedRun;
    assert.strictEqual(disconnected.elements.get('renovacao-sync-progress-percent').textContent, '40%');
    assert.match(disconnected.elements.get('renovacao-sync-progress-text').textContent, /pode continuar.*Atualize as campanhas/);
    assert.strictEqual(disconnected.requests.filter(item => item.options.method === 'POST').length, 1);
    assert.strictEqual(disconnected.timers.length, 0);
    const unknownStart = setup([new Error('Resposta perdida')]);
    await unknownStart.sandbox.criarCampanhaMesSeguinte();
    assert.match(unknownStart.elements.get('renovacao-sync-progress-text').textContent, /pode continuar/);
    assert.strictEqual(unknownStart.requests.length, 1);
    assert.strictEqual(unknownStart.elements.get('renovacao-sync-progress-percent').textContent, '');
    const rejected = setup([response({ detail: 'Origem inválida' }, false)]);
    await rejected.sandbox.criarCampanhaMesSeguinte();
    assert.strictEqual(rejected.elements.get('renovacao-sync-progress-text').textContent, 'Origem inválida');
    assert.strictEqual(rejected.requests.length, 1);

    // Respostas de outra loja ou de outro job não atualizam resultado/controles.
    for (const change of ['loja', 'job', 'contexto']) {
        const pending = deferred();
        const stale = setup([response({ job_id: 'job-criacao' }), pending.promise]);
        const staleRun = stale.sandbox.criarCampanhaMesSeguinte();
        await flush();
        if (change === 'loja') stale.elements.get('renovacao-loja').value = 'Outra Loja';
        else if (change === 'job') stale.sandbox.testState.creation.jobId = 'outro-job';
        else stale.sandbox.testState.creation = { loja: 'Deckas', jobId: 'outro-job' };
        stale.elements.get('renovacao-sync-progress-text').textContent = 'Novo contexto';
        pending.resolve(response({ status: 'done', result: fullResult }));
        await staleRun;
        assert.strictEqual(stale.elements.get('renovacao-sync-progress-text').textContent, 'Novo contexto');
        assert.strictEqual(stale.sandbox.testState.report, null);
        assert.strictEqual(stale.reloads.length, 0);
        if (change === 'contexto') assert.strictEqual(stale.elements.get('btn-criar-renovacao').disabled, true);
    }

    // Uma consulta antiga do sync pode terminar depois de a criação nova começar.
    for (const failLate of [false, true]) {
        const first = deferred(), late = deferred(), pendingCreation = deferred();
        const previous = setup([response({ status: 'running', percentual: 30 }), first.promise, late.promise, pendingCreation.promise]);
        await previous.sandbox.acompanharSincronizacaoRenovacao('sync-antigo', previous.elements.get('btn-sincronizar-renovacao'), 'Campanha antiga');
        const callback = [...previous.intervals.values()][0];
        const firstRun = callback(), lateRun = callback();
        first.resolve(response({ status: 'done', result: fullResult }));
        await firstRun;
        const creationRun = previous.sandbox.criarCampanhaMesSeguinte();
        const before = previous.elements.get('renovacao-sync-progress-text').textContent;
        if (failLate) late.reject(new Error('Erro antigo'));
        else late.resolve(response({ status: 'done', result: fullResult }));
        await lateRun;
        assert.strictEqual(previous.elements.get('renovacao-sync-progress-text').textContent, before);
        assert.strictEqual(previous.elements.get('btn-sincronizar-renovacao').disabled, true);
        assert.strictEqual(previous.sandbox.testState.report, null);
        pendingCreation.resolve(response({ detail: 'Fim do teste' }, false));
        await creationRun;
    }

    const canceled = setup();
    canceled.sandbox.confirm = () => false;
    await canceled.sandbox.criarCampanhaMesSeguinte();
    assert.strictEqual(canceled.requests.length, 0);
    const invalid = setup();
    invalid.elements.get('renovacao-nome').value = '';
    await invalid.sandbox.criarCampanhaMesSeguinte();
    assert.strictEqual(invalid.requests.length, 0);
    const busySync = setup();
    busySync.elements.get('btn-sincronizar-renovacao').disabled = true;
    await busySync.sandbox.criarCampanhaMesSeguinte();
    assert.strictEqual(busySync.requests.length, 0);
    console.log('Renovacao criação: progresso real, consultas sequenciais, pendências, erros, isolamento e bloqueios aprovados.');
})().catch(error => { console.error(error); process.exitCode = 1; });
