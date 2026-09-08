const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'static', 'perguntas_pos_venda', 'perguntas.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'static', 'perguntas_pos_venda.html'), 'utf8');

assert.match(source, /async function aguardarJobAtendimentoCodex[\s\S]*while \(true\)/);
assert.doesNotMatch(source, /185000|excedeu o limite de 180 segundos/);
assert.match(source, /Falha temporaria ao atualizar o andamento[\s\S]*A pesquisa continua no servidor/);
assert.match(source, /attempt_count[\s\S]*last_activity_at[\s\S]*status_message/);
assert.match(source, /next_retry_at_epoch|next_retry_in_seconds|Nova tentativa/);
assert.match(source, /\/api\/mercadolivre\/assistant\/jobs\/\$\{encodeURIComponent\(jobId\)\}\/cancel/);
assert.match(source, /question-ai-cancel-btn[\s\S]*Cancelar pesquisa/);
assert.match(source, /cancelarPesquisaAtendimentoCodex\(questionKey\)/);
assert.match(html, /perguntas\.js\?v=20260908-solicitacoes-v1/);
assert.doesNotMatch(source, /A pesquisa terminou sem rascunho/);
assert.match(source, /Rascunho gerado com as informacoes disponiveis/);
assert.match(source, /const resposta = String\(result\.resposta \?\? data\.resposta \?\? ''\);/);
assert.match(source, /resposta_atual: String\(textarea\.value \|\| ''\)/);
assert.match(source, /function skuRealPergunta\(pergunta\)/);
assert.doesNotMatch(source, /String\(result\.resposta \|\| data\.resposta \|\| ''\)\.trim\(\)/);
assert.doesNotMatch(source, /resposta_atual: String\(textarea\.value \|\| ''\)\.trim\(\)/);

class FakeClassList {
    constructor() { this.values = new Set(['hidden']); }
    add(value) { this.values.add(value); }
    remove(value) { this.values.delete(value); }
    toggle(value, force) { if (force) this.add(value); else this.remove(value); }
    contains(value) { return this.values.has(value); }
}

function makeCard(questionKey) {
    const elements = {
        button: { dataset: {}, disabled: false, classList: new FakeClassList() },
        generate: { disabled: false },
        status: { textContent: '', className: '' },
        textarea: { value: '', dataset: {}, dispatchEvent: () => true, focus: () => true }
    };
    return {
        dataset: { questionKey },
        elements,
        querySelector(selector) {
            if (selector === '.question-ai-cancel-btn') return elements.button;
            if (selector === '.question-ai-answer-btn') return elements.generate;
            if (selector === '.question-answer-composer .question-answer-status') return elements.status;
            if (selector === '.question-answer-text') return elements.textarea;
            return null;
        }
    };
}

const storage = new Map();
let tenantAtual = 'tenant-1';
const timerCallbacks = [];
let currentCard = makeCard('Loja A::Q1');
let fetchImpl = async () => ({ ok: true, json: async () => ({ status: 'cancelled' }) });
const container = { querySelectorAll: () => [currentCard] };
const context = {
    state: {},
    perguntasDetail: container,
    perguntasList: container,
    obterClientId: () => tenantAtual,
    obterAuthHeaders: () => ({}),
    sessionStorage: {
        getItem: (key) => storage.get(key) || null,
        setItem: (key, value) => storage.set(key, value),
        removeItem: (key) => storage.delete(key)
    },
    setStatusRespostaPergunta: (element, message, kind = '') => {
        if (element) {
            element.textContent = message;
            element.className = kind;
        }
    },
    mensagemErroApi: (_data, fallback) => fallback,
    mensagemErro: (error) => String(error && error.message || error || ''),
    fetch: (...args) => fetchImpl(...args),
    Event: class Event { constructor(type) { this.type = type; } },
    setTimeout: (callback) => { timerCallbacks.push(callback); return timerCallbacks.length; },
    clearTimeout: () => {},
    window: {}
};
vm.createContext(context);
const stateStart = source.indexOf("const CODEX_JOB_STORAGE_PREFIX");
const stateEnd = source.indexOf("window.aguardarJobAtendimentoCodex", stateStart);
assert(stateStart >= 0 && stateEnd > stateStart, 'helpers persistentes do job nao encontrados');
vm.runInContext(source.slice(stateStart, stateEnd), context);

(async () => {
    currentCard = makeCard('Loja A::Q-CONTEXTUAL');
    context.aplicarResultadoJobAtendimentoCodex('Loja A::Q-CONTEXTUAL', {
        result: {
            resposta: 'Resposta contextual.',
            draft_source: 'contextual_fallback',
            data_sufficient: false,
            completed_with_partial: true
        }
    });
    assert.strictEqual(currentCard.elements.textarea.value, 'Resposta contextual.');
    assert.strictEqual(
        currentCard.elements.status.textContent,
        'Rascunho de contingencia baseado no contexto. Revise antes de enviar.'
    );
    assert.strictEqual(currentCard.elements.status.className, '');
    assert.doesNotMatch(currentCard.elements.status.textContent, /Black Jhon/);

    currentCard = makeCard('Loja A::Q-NEUTRAL');
    context.aplicarResultadoJobAtendimentoCodex('Loja A::Q-NEUTRAL', {
        result: { resposta: 'Resposta neutra.', draft_source: 'neutral_fallback' }
    });
    assert.strictEqual(
        currentCard.elements.status.textContent,
        'Rascunho neutro de contingencia. Revise antes de enviar.'
    );
    assert.strictEqual(currentCard.elements.status.className, '');
    assert.doesNotMatch(currentCard.elements.status.textContent, /Black Jhon/);

    currentCard = makeCard('Loja A::Q-AI');
    context.aplicarResultadoJobAtendimentoCodex('Loja A::Q-AI', {
        result: { resposta: 'Resposta da IA.' }
    });
    assert.strictEqual(currentCard.elements.status.textContent, 'Sugestao gerada pelo Black Jhon.');
    assert.strictEqual(currentCard.elements.status.className, 'ok');

    currentCard = makeCard('Loja A::Q-PARTIAL');
    context.aplicarResultadoJobAtendimentoCodex('Loja A::Q-PARTIAL', {
        result: {
            resposta: 'Resposta parcial.',
            data_sufficient: false,
            completed_with_partial: true
        }
    });
    assert.strictEqual(
        currentCard.elements.status.textContent,
        'Rascunho gerado com as informacoes disponiveis.'
    );

    currentCard = makeCard('Loja A::Q1');
    context.salvarEstadoJobAtendimentoCodex('Loja A::Q1', {
        job_id: 'job-123', status_message: 'Tentativa 2. Nova tentativa em 15s.', can_cancel: true, polling_active: true
    });
    context.aplicarEstadoJobAtendimentoCodex('Loja A::Q1');
    assert.strictEqual(currentCard.elements.button.dataset.jobId, 'job-123');

    currentCard = makeCard('Loja A::Q1');
    context.atualizarEstadoJobAtendimentoCodex('Loja A::Q1', { status_message: 'waiting_retry visivel' });
    assert.strictEqual(currentCard.elements.status.textContent, 'waiting_retry visivel');
    assert.strictEqual(currentCard.elements.button.dataset.jobId, 'job-123');

    context.state.codexJobsAtendimento = {};
    currentCard = makeCard('Loja A::Q1');
    context.aplicarEstadoJobAtendimentoCodex('Loja A::Q1');
    assert.strictEqual(currentCard.elements.button.dataset.jobId, 'job-123', 'reload deve restaurar o mesmo job');
    assert.strictEqual(context.obterEstadoJobAtendimentoCodex('Loja B::Q1'), null, 'mesmo id em outra loja deve ficar isolado');
    tenantAtual = 'tenant-2';
    assert.strictEqual(context.obterEstadoJobAtendimentoCodex('Loja A::Q1'), null, 'mesma loja/pergunta em outro tenant deve ficar isolada');
    tenantAtual = 'tenant-1';

    let cancelledUrl = '';
    fetchImpl = async (url) => {
        cancelledUrl = String(url);
        return { ok: true, json: async () => ({ status: 'cancelled', status_message: 'Cancelada.' }) };
    };
    await context.cancelarPesquisaAtendimentoCodex('Loja A::Q1');
    assert.match(cancelledUrl, /job-123\/cancel$/);
    assert.strictEqual(context.obterEstadoJobAtendimentoCodex('Loja A::Q1').cancelled, true);

    currentCard = makeCard('Loja A::Q2');
    context.salvarEstadoJobAtendimentoCodex('Loja A::Q2', {
        job_id: 'job-completed', status_message: 'Concluindo', can_cancel: true, polling_active: true
    });
    fetchImpl = async () => ({
        ok: true,
        json: async () => ({ status: 'completed', job_id: 'job-completed', result: { resposta: 'Resposta vencedora.' } })
    });
    const completed = await context.cancelarPesquisaAtendimentoCodex('Loja A::Q2');
    assert.strictEqual(completed.status, 'completed');
    assert.strictEqual(currentCard.elements.textarea.value, 'Resposta vencedora.');
    assert.strictEqual(context.obterEstadoJobAtendimentoCodex('Loja A::Q2'), null, 'completed deve limpar storage sem marcar cancelado');

    currentCard = makeCard('Loja A::Q3');
    context.salvarEstadoJobAtendimentoCodex('Loja A::Q3', {
        job_id: 'job-poll-rerender', status_message: 'Iniciando', can_cancel: true, polling_active: true
    });
    let pollGets = 0;
    fetchImpl = async (url) => {
        assert.match(String(url), /job-poll-rerender$/);
        pollGets += 1;
        if (pollGets === 1) {
            return { ok: true, json: async () => ({
                status: 'waiting_retry', status_message: 'Nova tentativa em 15s.', can_cancel: true,
                attempt_count: 2, current_step: 'consultar', next_retry_in_seconds: 15
            }) };
        }
        return { ok: true, json: async () => ({
            status: 'completed', job_id: 'job-poll-rerender', result: { resposta: 'Resposta apos rerender.' }
        }) };
    };
    const pollOne = context.garantirPollingJobAtendimentoCodex('Loja A::Q3');
    const pollSame = context.garantirPollingJobAtendimentoCodex('Loja A::Q3');
    for (let index = 0; index < 12 && timerCallbacks.length === 0; index += 1) await Promise.resolve();
    currentCard = makeCard('Loja A::Q3');
    context.aplicarEstadoJobAtendimentoCodex('Loja A::Q3');
    assert.match(currentCard.elements.status.textContent, /Nova tentativa em 15s/);
    assert.strictEqual(currentCard.elements.button.dataset.jobId, 'job-poll-rerender');
    assert.strictEqual(Object.keys(context.state.codexPollsAtendimento).length, 1, 'um unico poll por runtime key');
    assert(timerCallbacks.length > 0, 'poll deve aguardar antes da segunda consulta');
    timerCallbacks.shift()();
    await Promise.all([pollOne, pollSame]);
    assert.strictEqual(pollGets, 2, 're-render nao deve duplicar polling');
    assert.strictEqual(currentCard.elements.textarea.value, 'Resposta apos rerender.');

    const countsByJob = new Map();
    fetchImpl = async (url) => {
        const jobId = String(url).split('/').pop();
        const count = (countsByJob.get(jobId) || 0) + 1;
        countsByJob.set(jobId, count);
        if (count === 1) return { ok: true, json: async () => ({ status: 'waiting_retry', can_cancel: true }) };
        return { ok: true, json: async () => ({ status: 'completed', job_id: jobId, result: { resposta: jobId } }) };
    };
    tenantAtual = 'tenant-1';
    context.salvarEstadoJobAtendimentoCodex('Loja A::Q4', { job_id: 'job-tenant-1', can_cancel: true, polling_active: true });
    const tenantOnePoll = context.garantirPollingJobAtendimentoCodex('Loja A::Q4');
    tenantAtual = 'tenant-2';
    context.salvarEstadoJobAtendimentoCodex('Loja A::Q4', { job_id: 'job-tenant-2', can_cancel: true, polling_active: true });
    const tenantTwoPoll = context.garantirPollingJobAtendimentoCodex('Loja A::Q4');
    for (let index = 0; index < 12 && timerCallbacks.length < 2; index += 1) await Promise.resolve();
    assert.strictEqual(Object.keys(context.state.codexPollsAtendimento).length, 2, 'tenants distintos precisam de promises distintas');
    while (timerCallbacks.length) timerCallbacks.shift()();
    await Promise.all([tenantOnePoll, tenantTwoPoll]);
    assert.strictEqual(countsByJob.get('job-tenant-1'), 2);
    assert.strictEqual(countsByJob.get('job-tenant-2'), 2);

    console.log('Perguntas Codex persistent frontend contract: OK');
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
