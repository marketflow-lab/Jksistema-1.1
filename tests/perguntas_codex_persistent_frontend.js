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
assert.match(html, /perguntas\.js\?v=20260921-question-draft-recovery-v1/);
assert.match(html, /loading\.js\?v=20260921-question-draft-recovery-v1/);
assert.match(html, /lojas-snapshot\.js\?v=20260921-question-draft-recovery-v1/);
assert.doesNotMatch(source, /A pesquisa terminou sem rascunho/);
assert.match(source, /Rascunho gerado com as informacoes disponiveis/);
assert.match(source, /const respostaResultado = String\(result\.resposta \?\? data\?\.resposta \?\? ''\);/);
assert.match(source, /resposta_atual: String\(textarea\.value \|\| ''\)/);
assert.match(source, /function skuRealPergunta\(pergunta\)/);
assert.doesNotMatch(source, /String\(result\.resposta \|\| data\.resposta \|\| ''\)\.trim\(\)/);
assert.doesNotMatch(source, /resposta_atual: String\(textarea\.value \|\| ''\)\.trim\(\)/);
assert.match(source, /response\.status === 404 \|\| response\.status === 410/);
assert.match(source, /CODEX_DRAFT_AUTOSAVE_DEBOUNCE_MS = 500/);
for (const helper of [
    'recuperarRascunhoJobAtendimentoCodex',
    'agendarAutosaveRascunhoAtendimentoCodex',
    'flushAutosaveRascunhoAtendimentoCodex',
    'finalizarEstadoJobAtendimentoCodex',
    'marcarAtualizacaoProgramaticaRascunhoAtendimentoCodex'
]) assert.match(source, new RegExp(`function ${helper}\\b`), `contrato ausente: ${helper}`);

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
const loadingDrafts = new Map();
let tenantAtual = 'tenant-1';
const timerCallbacks = [];
let currentCard = makeCard('Loja A::Q1');
let fetchImpl = async () => ({ ok: true, json: async () => ({ status: 'cancelled' }) });
let rejectedContext = null;
const container = { querySelectorAll: () => currentCard ? [currentCard] : [] };
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
    AbortController: class AbortController {
        constructor() { this.signal = { onabort: null }; }
        abort() {
            const error = new Error('aborted');
            error.name = 'AbortError';
            if (typeof this.signal.onabort === 'function') this.signal.onabort(error);
        }
    },
    setTimeout: (callback) => { timerCallbacks.push(callback); return timerCallbacks.length; },
    clearTimeout: () => {},
    window: {
        JKPerguntasLoading: {
            rejeitarContexto: (_question, component, _blocked, reason) => { rejectedContext = { component, reason }; },
            obterRascunhoPorChave: questionKey => {
                const draft = loadingDrafts.get(String(questionKey || ''));
                return draft ? { ...draft } : null;
            },
            atualizarRascunhoPorChave: (questionKey, patch = {}) => {
                const key = String(questionKey || '');
                const draft = { ...(loadingDrafts.get(key) || {}), ...(patch || {}), perguntaSelecionadaKey: key };
                loadingDrafts.set(key, draft);
                return { ...draft };
            },
            removerRascunhoPorChave: questionKey => loadingDrafts.delete(String(questionKey || ''))
        }
    }
};
vm.createContext(context);
const stateStart = source.indexOf("const CODEX_JOB_STORAGE_PREFIX");
const stateEnd = source.indexOf("window.aguardarJobAtendimentoCodex", stateStart);
assert(stateStart >= 0 && stateEnd > stateStart, 'helpers persistentes do job nao encontrados');
vm.runInContext(source.slice(stateStart, stateEnd), context);

assert.strictEqual(context.classificarFalhaGeracaoAtendimento({ error_reason: 'codex_authentication_required' }).kind, 'auth');
assert.strictEqual(context.classificarFalhaGeracaoAtendimento({ error_reason: 'codex_runtime_disabled' }).kind, 'runtime');
assert.strictEqual(context.classificarFalhaGeracaoAtendimento({ error_reason: 'codex_dependency_missing' }).kind, 'runtime');
assert.strictEqual(context.classificarFalhaGeracaoAtendimento({ error_reason: 'codex_runtime_invalid' }).kind, 'runtime');
for (const reason of ['provider_timeout', 'provider_connection', 'provider_http_429', 'provider_http_5xx', 'provider_turn_failed', 'provider_empty_response']) {
    assert.strictEqual(context.classificarFalhaGeracaoAtendimento({ error_reason: reason }).kind, 'transient');
}
assert.strictEqual(context.classificarFalhaGeracaoAtendimento({ error_reason: 'codex_runtime_unknown' }).kind, 'generic');
assert.strictEqual(context.classificarFalhaGeracaoAtendimento({ error_reason: 'provider_unknown' }).kind, 'generic');
assert.strictEqual(context.classificarFalhaGeracaoAtendimento({
    error_code: 'generation_context_unavailable', error_reason: 'context_expired'
}).kind, 'generic', 'contexto sem componente explícito deve permanecer genérico');
assert.strictEqual(context.classificarFalhaGeracaoAtendimento({
    error_code: 'generation_context_unavailable', error_component: 'context', error_reason: 'context_expired'
}).kind, 'context');
assert.strictEqual(context.classificarFalhaGeracaoAtendimento({
    completion_reason: 'generation_context_unavailable', error_component: 'history'
}).kind, 'context');
assert.strictEqual(context.classificarFalhaGeracaoAtendimento({
    status_code: 403, error_component: 'context', error_reason: 'context_expired'
}).kind, 'generic', 'status HTTP e componente sem contrato não provam falha de contexto');
assert.strictEqual(context.normalizarFalhaGeracaoAtendimento({ retryable: 'false' }).retryable, false);
assert.strictEqual(context.normalizarFalhaGeracaoAtendimento({ retryable: 'true' }).retryable, true);
const preservedContextState = {
    _generationError: 'anterior', _generationComponent: 'context_hub', _generationReason: 'anterior',
    _contextState: 'stale', _contextHubState: 'partial'
};
context.limparFalhaGeracaoAtendimento(preservedContextState);
assert.strictEqual(preservedContextState._contextState, 'stale');
assert.strictEqual(preservedContextState._contextHubState, 'partial');

(async () => {
    currentCard = makeCard('Loja A::Q-BLOCKED');
    currentCard.elements.textarea.value = 'Rascunho do operador preservado';
    context.aplicarResultadoJobAtendimentoCodex('Loja A::Q-BLOCKED', {
        blocked_without_draft: true,
        result: { resposta: '', blocked_without_draft: true, warnings: ['Histórico indisponível. Tente novamente.'] }
    });
    assert.strictEqual(currentCard.elements.textarea.value, 'Rascunho do operador preservado');
    assert.strictEqual(currentCard.elements.status.textContent, 'Não foi possível gerar a sugestão. Tente novamente.');
    assert.strictEqual(rejectedContext, null, 'falha sem componente não pode virar erro de contexto');

    currentCard = makeCard('Loja A::Q-AUTH');
    currentCard.elements.textarea.value = 'Rascunho preservado sem autenticação do Codex';
    context.state.perguntas = [{ question_key: 'Loja A::Q-AUTH' }];
    context.chavePerguntaAtendimento = (question) => question.question_key;
    context.aplicarResultadoJobAtendimentoCodex('Loja A::Q-AUTH', {
        blocked_without_draft: true,
        error_reason: 'codex_authentication_required',
        result: { resposta: '', blocked_without_draft: true }
    });
    assert.strictEqual(currentCard.elements.textarea.value, 'Rascunho preservado sem autenticação do Codex');
    assert.strictEqual(currentCard.elements.status.textContent, 'A autenticação local da IA expirou. Faça login no Codex e gere a sugestão novamente.');
    assert.strictEqual(rejectedContext, null);

    currentCard = makeCard('Loja A::Q-RUNTIME');
    currentCard.elements.textarea.value = 'Rascunho preservado sem runtime';
    context.state.perguntas = [{ question_key: 'Loja A::Q-RUNTIME' }];
    context.aplicarResultadoJobAtendimentoCodex('Loja A::Q-RUNTIME', {
        blocked_without_draft: true,
        error_reason: 'codex_dependency_missing',
        result: { resposta: '', blocked_without_draft: true }
    });
    assert.strictEqual(currentCard.elements.textarea.value, 'Rascunho preservado sem runtime');
    assert.strictEqual(currentCard.elements.status.textContent, 'A IA local está indisponível neste computador. Verifique a configuração e tente novamente.');
    assert.strictEqual(rejectedContext, null);

    currentCard = makeCard('Loja A::Q-TRANSIENT');
    currentCard.elements.textarea.value = 'Rascunho preservado na falha transitória';
    context.state.perguntas = [{ question_key: 'Loja A::Q-TRANSIENT' }];
    context.aplicarResultadoJobAtendimentoCodex('Loja A::Q-TRANSIENT', {
        blocked_without_draft: true,
        error_reason: 'provider_timeout',
        result: { resposta: '', blocked_without_draft: true }
    });
    assert.strictEqual(currentCard.elements.textarea.value, 'Rascunho preservado na falha transitória');
    assert.strictEqual(currentCard.elements.status.textContent, 'A IA está temporariamente indisponível. Tente novamente.');
    assert.strictEqual(rejectedContext, null);

    currentCard = makeCard('Loja A::Q-CONTEXT-EXPIRED');
    context.state.perguntas = [{ question_key: 'Loja A::Q-CONTEXT-EXPIRED' }];
    context.aplicarResultadoJobAtendimentoCodex('Loja A::Q-CONTEXT-EXPIRED', {
        blocked_without_draft: true,
        error_code: 'generation_context_unavailable',
        error_component: 'context',
        error_reason: 'context_expired',
        result: { resposta: '', blocked_without_draft: true }
    });
    assert.deepStrictEqual(rejectedContext, { component: 'context', reason: 'context_expired' });

    rejectedContext = null;
    currentCard = makeCard('Loja A::Q-CONTEXT-HUB');
    currentCard.elements.textarea.value = 'Rascunho preservado com Obsidian indisponível';
    context.state.perguntas = [{ key: 'fixture' }];
    context.chavePerguntaAtendimento = () => 'Loja A::Q-CONTEXT-HUB';
    context.aplicarResultadoJobAtendimentoCodex('Loja A::Q-CONTEXT-HUB', {
        blocked_without_draft: true,
        error_code: 'generation_context_unavailable',
        error_component: 'context_hub',
        error_reason: 'training_index_initializing',
        result: { resposta: '', blocked_without_draft: true }
    });
    assert.strictEqual(currentCard.elements.textarea.value, 'Rascunho preservado com Obsidian indisponível');
    assert.deepStrictEqual(rejectedContext, { component: 'context_hub', reason: 'training_index_initializing' });

    context.chavePerguntaAtendimento = (question) => question.question_key;
    currentCard = makeCard('Loja A::Q-HUB-UNAVAILABLE');
    const unavailableQuestion = { question_key: 'Loja A::Q-HUB-UNAVAILABLE', _contextHubState: '' };
    context.state.perguntas = [unavailableQuestion];
    context.aplicarResultadoJobAtendimentoCodex('Loja A::Q-HUB-UNAVAILABLE', {
        result: {
            resposta: 'Rascunho sem a ficha do Obsidian.',
            context_hub_status: { state: 'unavailable', reason_code: 'context_hub_unavailable' }
        }
    });
    assert.strictEqual(currentCard.elements.textarea.value, 'Rascunho sem a ficha do Obsidian.');
    assert.strictEqual(unavailableQuestion._contextHubState, 'unavailable');
    assert.strictEqual(unavailableQuestion._generationComponent, 'context_hub');
    assert.match(currentCard.elements.status.textContent, /informações do Obsidian estavam indisponíveis/i);
    assert.doesNotMatch(currentCard.elements.status.textContent, /Histórico/);

    currentCard = makeCard('Loja A::Q-HUB-PARTIAL');
    const partialHubQuestion = { question_key: 'Loja A::Q-HUB-PARTIAL', _contextHubState: '' };
    context.state.perguntas = [partialHubQuestion];
    context.aplicarResultadoJobAtendimentoCodex('Loja A::Q-HUB-PARTIAL', {
        result: {
            resposta: 'Rascunho com ficha parcial.',
            contexto: { context_hub_status: { partial_unavailable: true } }
        }
    });
    assert.strictEqual(currentCard.elements.textarea.value, 'Rascunho com ficha parcial.');
    assert.strictEqual(partialHubQuestion._contextHubState, 'partial');
    assert.match(currentCard.elements.status.textContent, /Obsidian foram carregadas parcialmente/i);

    currentCard = makeCard('Loja A::Q-HUB-READY');
    const readyHubQuestion = { question_key: 'Loja A::Q-HUB-READY', _contextHubState: 'unavailable', _generationComponent: 'context_hub' };
    context.state.perguntas = [readyHubQuestion];
    context.aplicarResultadoJobAtendimentoCodex('Loja A::Q-HUB-READY', {
        result: { resposta: 'Rascunho com ficha pronta.', context_hub_status: { state: 'ready' } }
    });
    assert.strictEqual(readyHubQuestion._contextHubState, '');
    assert.strictEqual(readyHubQuestion._generationComponent, '');

    currentCard = makeCard('Loja A::Q-HUB-ABSENT');
    const absentHubQuestion = { question_key: 'Loja A::Q-HUB-ABSENT', _contextHubState: 'partial', _generationComponent: 'context_hub' };
    context.state.perguntas = [absentHubQuestion];
    context.aplicarResultadoJobAtendimentoCodex('Loja A::Q-HUB-ABSENT', {
        result: { resposta: 'Rascunho sem metadado novo.' }
    });
    assert.strictEqual(absentHubQuestion._contextHubState, '');
    assert.strictEqual(absentHubQuestion._generationComponent, '');

    currentCard = makeCard('Loja A::Q-HUB-EMPTY');
    const emptyHubQuestion = { question_key: 'Loja A::Q-HUB-EMPTY', _contextHubState: 'partial', _generationComponent: 'context_hub' };
    context.state.perguntas = [emptyHubQuestion];
    context.aplicarResultadoJobAtendimentoCodex('Loja A::Q-HUB-EMPTY', {
        result: { resposta: 'Rascunho sem consulta aplicável.', context_hub_status: {} }
    });
    assert.strictEqual(emptyHubQuestion._contextHubState, '');
    assert.strictEqual(emptyHubQuestion._generationComponent, '');

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

    currentCard = makeCard('Loja A::Q-STALE');
    context.aplicarResultadoJobAtendimentoCodex('Loja A::Q-STALE', {
        result: {
            resposta: 'Resposta com contexto recente.',
            context_status: { source: 'stale_fallback', age_seconds: 240 }
        }
    });
    assert.match(currentCard.elements.status.textContent, /contexto validado há até 5 minutos/i);
    assert.match(currentCard.elements.status.textContent, /Revise antes de enviar/);

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
    assert.deepStrictEqual(
        [
            context.obterEstadoJobAtendimentoCodex('Loja A::Q2')?.terminal,
            context.obterEstadoJobAtendimentoCodex('Loja A::Q2')?.polling_active,
            context.obterEstadoJobAtendimentoCodex('Loja A::Q2')?.cancelled
        ],
        [true, false, false],
        'completed deve manter referência terminal recuperável até envio ou descarte'
    );

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

    const offscreenKey = 'Loja A::Q-OFFSCREEN';
    context.state.perguntas = [{ question_key: offscreenKey, store_id: 'store-a', id: 'Q-OFFSCREEN' }];
    context.salvarEstadoJobAtendimentoCodex(offscreenKey, {
        job_id: 'job-completed-offscreen', status_message: 'Consultando', can_cancel: true, polling_active: true
    });
    currentCard = null;
    fetchImpl = async () => ({
        ok: true,
        json: async () => ({
            status: 'completed',
            job_id: 'job-completed-offscreen',
            result: {
                resposta: 'Resposta concluida fora do DOM.',
                proposal_id: 'proposal-offscreen',
                proposal_version: 3,
                proposal_hash: 'hash-offscreen'
            }
        })
    });
    const offscreenCompleted = await context.garantirPollingJobAtendimentoCodex(offscreenKey);
    assert.strictEqual(offscreenCompleted.status, 'completed');
    assert.strictEqual(
        context.window.JKPerguntasLoading.obterRascunhoPorChave(offscreenKey)?.resposta,
        'Resposta concluida fora do DOM.',
        'conclusao sem card deve materializar um rascunho recuperavel'
    );

    assert.deepStrictEqual(
        [
            context.obterEstadoJobAtendimentoCodex(offscreenKey)?.terminal,
            context.obterEstadoJobAtendimentoCodex(offscreenKey)?.polling_active
        ],
        [true, false],
        'conclusao sem card deve manter referência terminal para A-B-A e reload'
    );
    context.window.JKPerguntasLoading.atualizarRascunhoPorChave(offscreenKey, {
        resposta: 'Edição local ainda não salva.'
    });
    context.aplicarResultadoJobAtendimentoCodex(offscreenKey, offscreenCompleted);
    assert.strictEqual(
        context.window.JKPerguntasLoading.obterRascunhoPorChave(offscreenKey)?.resposta,
        'Edição local ainda não salva.',
        'resultado repetido da mesma versão não pode substituir edição local'
    );
    context.aplicarResultadoJobAtendimentoCodex(offscreenKey, {
        ...offscreenCompleted,
        result: { ...offscreenCompleted.result, resposta: 'Versão antiga.', proposal_version: 2, proposal_hash: 'hash-antigo' }
    });
    assert.strictEqual(
        context.window.JKPerguntasLoading.obterRascunhoPorChave(offscreenKey)?.resposta,
        'Edição local ainda não salva.',
        'versão antiga nunca pode substituir o rascunho atual'
    );
    fetchImpl = async () => ({
        ok: false,
        status: 409,
        json: async () => ({ detail: { code: 'proposal_conflict' } })
    });
    const timersAntesConflito = timerCallbacks.length;
    context.agendarAutosaveRascunhoAtendimentoCodex(offscreenKey);
    assert.strictEqual(timerCallbacks.length, timersAntesConflito + 1);
    timerCallbacks.pop()();
    await new Promise(resolve => setImmediate(resolve));
    context.window.JKPerguntasLoading.atualizarRascunhoPorChave(offscreenKey, {
        resposta: 'Edição local mantida após conflito.'
    });
    context.agendarAutosaveRascunhoAtendimentoCodex(offscreenKey);
    assert.strictEqual(
        timerCallbacks.length,
        timersAntesConflito,
        'conflito 409 deve bloquear novos autosaves silenciosos até revisão'
    );
    tenantAtual = 'tenant-2';
    assert.strictEqual(context.obterEstadoJobAtendimentoCodex(offscreenKey), null, 'outro tenant nao pode recuperar a referência terminal');
    tenantAtual = 'tenant-1';
    assert.strictEqual(context.window.JKPerguntasLoading.obterRascunhoPorChave('Loja B::Q-OFFSCREEN'), null, 'outra loja/pergunta nao pode recuperar o rascunho');

    context.limparEstadoJobAtendimentoCodex(offscreenKey);
    assert.strictEqual(context.obterEstadoJobAtendimentoCodex(offscreenKey), null);
    assert.strictEqual(
        context.window.JKPerguntasLoading.obterRascunhoPorChave(offscreenKey)?.resposta,
        'Edição local mantida após conflito.',
        'limpar somente o job deve preservar o texto editável em falha ou cancelamento'
    );
    context.window.JKPerguntasLoading.removerRascunhoPorChave(offscreenKey);
    assert.strictEqual(context.window.JKPerguntasLoading.obterRascunhoPorChave(offscreenKey), null, 'limpeza explícita de envio deve remover o rascunho recuperável');

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

    tenantAtual = 'tenant-1';
    currentCard = makeCard('Loja A::Q-FAILED');
    context.salvarEstadoJobAtendimentoCodex('Loja A::Q-FAILED', {
        job_id: 'job-failed-terminal', can_cancel: true, polling_active: true
    });
    let failedGets = 0;
    fetchImpl = async () => {
        failedGets += 1;
        return { ok: true, json: async () => ({ status: 'failed', error: 'Falha definitiva da fixture.' }) };
    };
    await assert.rejects(
        context.garantirPollingJobAtendimentoCodex('Loja A::Q-FAILED'),
        /Falha definitiva da fixture/
    );
    assert.strictEqual(failedGets, 1, 'status failed deve encerrar o polling na primeira leitura');
    assert.strictEqual(context.obterEstadoJobAtendimentoCodex('Loja A::Q-FAILED'), null);
    assert.strictEqual(currentCard.elements.status.textContent, 'Não foi possível gerar a sugestão. Tente novamente.');
    assert.strictEqual(currentCard.elements.status.className, 'error');

    currentCard = makeCard('Loja A::Q-GONE');
    context.salvarEstadoJobAtendimentoCodex('Loja A::Q-GONE', {
        job_id: 'job-gone-terminal', can_cancel: true, polling_active: true
    });
    let goneGets = 0;
    fetchImpl = async () => {
        goneGets += 1;
        return { ok: false, status: 410, json: async () => ({ detail: 'Job expirado.' }) };
    };
    await assert.rejects(context.garantirPollingJobAtendimentoCodex('Loja A::Q-GONE'));
    assert.strictEqual(goneGets, 1, 'HTTP 410 deve encerrar o polling na primeira leitura');
    assert.strictEqual(context.obterEstadoJobAtendimentoCodex('Loja A::Q-GONE'), null);
    assert.strictEqual(currentCard.elements.status.className, 'error');
    assert.strictEqual(currentCard.elements.status.textContent, 'Não foi possível gerar a sugestão. Tente novamente.');

    currentCard.elements.textarea.value = 'Rascunho manual preservado.';
    context.aplicarResultadoJobAtendimentoCodex('Loja A::Q-GONE', {
        status: 'completed', completion_reason: 'stores_busy_retry_exhausted',
        blocked_without_draft: true, result: { resposta: '' }
    });
    assert.strictEqual(currentCard.elements.textarea.value, 'Rascunho manual preservado.');
    assert.match(currentCard.elements.status.textContent, /configuração da loja continua ocupada/);

    console.log('Perguntas Codex persistent frontend contract: OK');
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
