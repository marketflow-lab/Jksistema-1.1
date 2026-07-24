'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const automationSource = fs.readFileSync(
    path.join(root, 'static', 'perguntas_pos_venda', 'lojas-automacao.js'),
    'utf8'
);
const questionsSource = fs.readFileSync(
    path.join(root, 'static', 'perguntas_pos_venda', 'perguntas.js'),
    'utf8'
);
const helpersStart = questionsSource.indexOf('function formatarTempoDesdeAtualizacaoPerguntas');
const helpersEnd = questionsSource.indexOf('function renderizarResumo', helpersStart);
assert(helpersStart >= 0 && helpersEnd > helpersStart, 'helpers do indicador nao encontrados');
const interactionStart = questionsSource.indexOf('function capturarInteracaoPerguntas');
const interactionEnd = questionsSource.indexOf('function obterPerguntaPorId', interactionStart);
assert(interactionStart >= 0 && interactionEnd > interactionStart, 'helpers de preservacao nao encontrados');
const carregarPerguntasStart = questionsSource.indexOf('async function carregarPerguntas');
assert(carregarPerguntasStart >= 0, 'carregarPerguntas nao encontrado');

let agora = Date.parse('2026-07-18T15:00:00Z');
class FakeDate extends Date {
    static now() {
        return agora;
    }
}

let proximoTimer = 1;
const timeouts = new Map();
const intervals = new Map();
const timeoutsCancelados = new Set();
const intervalsCancelados = new Set();
const setTimeoutFake = (callback, delay) => {
    const id = proximoTimer++;
    timeouts.set(id, { callback, delay });
    return id;
};
const clearTimeoutFake = (id) => {
    timeoutsCancelados.add(id);
    timeouts.delete(id);
};
const setIntervalFake = (callback, delay) => {
    const id = proximoTimer++;
    intervals.set(id, { callback, delay });
    return id;
};
const clearIntervalFake = (id) => {
    intervalsCancelados.add(id);
    intervals.delete(id);
};
const executarTimeout = async (id) => {
    const timer = timeouts.get(id);
    assert(timer, `timeout ${id} nao encontrado`);
    timeouts.delete(id);
    await timer.callback();
    await Promise.resolve();
};

const indicador = { textContent: '', dateTime: '', title: '' };
const resumoTodas = { textContent: '', dataset: {} };
let recargas = 0;
let recargasContadores = 0;
let perguntasAtiva = true;
let ultimaRecargaOpcoes = null;
const chamadas = [];
let fetchImpl = async (url) => {
    chamadas.push(String(url));
    return {
        ok: true,
        json: async () => ({ success: true, novas_pendentes: [], pendentes: [], enviadas: [] })
    };
};

const state = {
    lojas: [
        { nome: 'Loja A', mercadolivre_conectado: true, config_perguntas: { responder_automaticamente: true, intervalo_minutos: 5 } },
        { nome: 'Loja B', mercadolivre_conectado: true, config_perguntas: { responder_automaticamente: true, intervalo_minutos: 5 } }
    ],
    lojaSelecionada: '__todas_contas__',
    carregandoPerguntas: false,
    carregandoPosVenda: false,
    paginaPerguntas: 1,
    ultimaAtualizacaoPerguntasEm: 0,
    ultimaAtualizacaoPerguntasTimer: null,
    ultimaChecagemAutomacaoPerguntasEm: 0,
    automacaoPerguntasTimers: [],
    automacaoPerguntasStartupTimers: [],
    automacaoPerguntasGeracao: 1,
    automacaoPerguntasCountdownTimer: null,
    automacaoPerguntasStatusTimer: null,
    automacaoPerguntasStatusCarregando: true,
    automacaoPerguntasStatusDisponivel: false,
    automacaoPerguntasStatusErro: '',
    automacaoPerguntasStatusLojas: {},
    automacaoPerguntasChangeTokensLojas: {},
    automacaoPerguntasQuestionIdsLojas: {},
    automacaoPerguntasRefreshLojas: new Set(),
    automacaoPerguntasWorkerIniciado: false,
    automacaoPerguntasBackendExecutando: false,
    automacaoPerguntasProximaChecagemBackendEm: 0,
    automacaoPerguntasRefreshTimer: null,
    automacaoPerguntasRefreshPendente: false,
    automacaoPerguntasNextChecks: {},
    automacaoPerguntasRodandoLojas: new Set(),
    aprovacoesNotificadas: new Set()
};

const context = {
    assert,
    state,
    Date: FakeDate,
    URLSearchParams,
    Promise,
    Set,
    Math,
    Number,
    Object,
    Array,
    String,
    console,
    CustomEvent: class FakeCustomEvent {
        constructor(type, options = {}) {
            this.type = type;
            this.detail = options.detail;
        }
    },
    dispatchEvent: () => true,
    setTimeout: setTimeoutFake,
    clearTimeout: clearTimeoutFake,
    setInterval: setIntervalFake,
    clearInterval: clearIntervalFake,
    perguntasUltimaAtualizacao: indicador,
    TODAS_LOJAS_VALUE: '__todas_contas__',
    perguntasAutomationControls: null,
    obterAuthHeaders: () => ({ Authorization: 'Bearer teste' }),
    mensagemErro: (error) => String(error && error.message ? error.message : error || ''),
    chaveLojaCronometro: (nome) => String(nome || '').trim(),
    lojasMercadoLivreConectadas: () => state.lojas.filter((loja) => loja.mercadolivre_conectado === true),
    formatarCronometroPerguntas: (ms) => `${Math.max(0, Math.ceil(Number(ms || 0) / 1000))}s`,
    carregarPerguntas: async (...args) => { recargas += 1; ultimaRecargaOpcoes = args[1] || null; return true; },
    carregarContadoresNotificacoes: async () => { recargasContadores += 1; },
    carregarAprovacoesPendentes: async () => {},
    carregarPosVenda: async () => {},
    fetch: (...args) => fetchImpl(...args),
    document: {
        querySelectorAll(selector) {
            return selector === '[data-automation-summary]' ? [resumoTodas] : [];
        },
        getElementById(id) {
            if (id === 'aba-perguntas') return { classList: { contains: () => perguntasAtiva } };
            if (id === 'aba-pos-venda') return { classList: { contains: () => false } };
            return null;
        }
    }
};
context.window = context;
vm.createContext(context);
vm.runInContext(questionsSource.slice(helpersStart, helpersEnd), context);
vm.runInContext(automationSource, context);
context.carregarPerguntas = async (...args) => { recargas += 1; ultimaRecargaOpcoes = args[1] || null; return true; };
context.carregarContadoresNotificacoes = async () => { recargasContadores += 1; };

(async () => {
    const [resultadoA, resultadoB] = await Promise.all([
        context.executarAutomacaoPerguntas('Loja A', state.automacaoPerguntasGeracao),
        context.executarAutomacaoPerguntas('Loja B', state.automacaoPerguntasGeracao)
    ]);
    assert(resultadoA && resultadoB, 'as duas lojas devem concluir o poll');
    const polls = chamadas.filter((url) => url.includes('/automacao/poll?'));
    assert.strictEqual(polls.length, 2, 'o lock por loja nao pode descartar a segunda conta');
    assert(polls.some((url) => url.includes('loja=Loja+A')), 'poll da Loja A ausente');
    assert(polls.some((url) => url.includes('loja=Loja+B')), 'poll da Loja B ausente');
    assert.strictEqual(state.automacaoPerguntasRodandoLojas.size, 0, 'locks devem ser liberados');
    assert.strictEqual(state.automacaoPerguntasRefreshTimer, null, 'checagem sem novidade nao deve atualizar a lista');
    assert.strictEqual(timeouts.size, 0, 'checagem sem novidade deve permanecer silenciosa');
    assert.strictEqual(recargas, 0, 'lista principal deve permanecer intacta sem pergunta nova');
    assert.strictEqual(recargasContadores, 0, 'contadores nao precisam ser recarregados sem novidade');
    assert.strictEqual(indicador.textContent, 'Checado agora', 'poll real deve atualizar o indicador');

    fetchImpl = async (url) => {
        chamadas.push(String(url));
        return {
            ok: true,
            json: async () => ({ success: true, novas_pendentes: [{ id: 'nova-1' }], pendentes: [], enviadas: [] })
        };
    };
    await context.executarAutomacaoPerguntas('Loja A', state.automacaoPerguntasGeracao);
    assert(state.automacaoPerguntasRefreshTimer, 'pergunta nova deve agendar uma atualizacao da lista');
    await executarTimeout(state.automacaoPerguntasRefreshTimer);
    assert.strictEqual(recargas, 1, 'pergunta nova deve atualizar a lista uma vez');
    assert.strictEqual(recargasContadores, 1, 'pergunta nova deve atualizar os contadores uma vez');

    agora = Date.parse('2026-07-18T15:00:03Z');
    state.automacaoPerguntasStatusCarregando = false;
    fetchImpl = async (url) => {
        chamadas.push(String(url));
        assert(String(url).endsWith('/api/mercadolivre/perguntas/automacao/status'));
        return {
            ok: true,
            json: async () => ({
                success: true,
                worker_iniciado: true,
                lojas: [
                    {
                        loja: 'Loja A',
                        automacao_ativa: true,
                        intervalo_minutos: 5,
                        ultima_checagem: '2026-07-18T15:00:02Z',
                        proxima_checagem: '2026-07-18T15:05:00Z',
                        executando: false,
                        sucesso: true,
                        erro: '',
                        change_token: 'token-a-1',
                        question_ids: ['historica-1'],
                        new_question_ids: ['historica-1'],
                        new_questions_count: 1,
                        question_snapshot_complete: true,
                        contagens: { enviadas: 0, novas_pendentes: 1, erros: 0 }
                    },
                    {
                        loja: 'Loja B',
                        automacao_ativa: true,
                        intervalo_minutos: 5,
                        ultima_checagem: '2026-07-18T15:00:01Z',
                        proxima_checagem: '2026-07-18T15:05:00Z',
                        executando: false,
                        sucesso: true,
                        erro: '',
                        contagens: { enviadas: 0, novas_pendentes: 0, erros: 0 }
                    }
                ]
            })
        };
    };
    const status = await context.consultarStatusAutomacaoPerguntas();
    assert(status && status.success, 'status do backend deve ser aceito');
    assert.strictEqual(state.automacaoPerguntasStatusDisponivel, true);
    assert.strictEqual(state.automacaoPerguntasWorkerIniciado, true);
    assert.strictEqual(context.deveExecutarPollFrontendPerguntas(), false, 'worker ativo deve evitar poll duplicado no renderer');
    assert.strictEqual(
        state.ultimaChecagemAutomacaoPerguntasEm,
        Date.parse('2026-07-18T15:00:02Z'),
        'indicador deve usar a ultima checagem real do backend'
    );
    assert.strictEqual(
        state.automacaoPerguntasNextChecks['Loja A'],
        Date.parse('2026-07-18T15:05:00Z'),
        'proxima checagem por loja deve vir do backend'
    );
    assert(resumoTodas.textContent.includes('2 conta(s) ativa(s)'), 'Todas deve mostrar quantidade de contas ativas');
    assert(resumoTodas.textContent.includes('proxima em'), 'Todas deve mostrar a proxima checagem');
    assert.strictEqual(state.automacaoPerguntasRefreshTimer, null, 'primeiro status deve apenas criar a linha de base');
    assert.strictEqual(recargas, 1, 'resultado historico nao deve atualizar a lista ao abrir a tela');
    assert.strictEqual(recargasContadores, 1, 'resultado historico nao deve atualizar os contadores ao abrir a tela');

    state.automacaoPerguntasStatusCarregando = false;
    fetchImpl = async () => ({
        ok: true,
        json: async () => ({
            success: true,
            worker_iniciado: true,
            lojas: [{
                loja: 'Loja A',
                automacao_ativa: true,
                intervalo_minutos: 5,
                ultima_checagem: '2026-07-18T15:00:03Z',
                proxima_checagem: '2026-07-18T15:05:03Z',
                executando: false,
                sucesso: true,
                erro: '',
                change_token: 'token-a-2',
                question_ids: ['historica-1', 'nova-token-1'],
                new_question_ids: ['nova-token-1'],
                new_questions_count: 1,
                question_snapshot_complete: true,
                contagens: { enviadas: 0, novas_pendentes: 1, erros: 0 }
            }]
        })
    });
    const statusComNovidade = await context.consultarStatusAutomacaoPerguntas();
    assert(statusComNovidade && statusComNovidade.success, 'novo ciclo do worker deve ser aceito');
    assert(state.automacaoPerguntasRefreshTimer, 'novo ciclo com novidade deve agendar a lista');
    await executarTimeout(state.automacaoPerguntasRefreshTimer);
    assert.strictEqual(recargas, 2, 'novidade posterior deve atualizar a lista uma vez');
    assert.strictEqual(recargasContadores, 2, 'novidade posterior deve atualizar os contadores uma vez');
    assert.strictEqual(ultimaRecargaOpcoes && ultimaRecargaOpcoes.background, true);
    assert.strictEqual(ultimaRecargaOpcoes && ultimaRecargaOpcoes.preservarInteracao, true);

    state.automacaoPerguntasStatusCarregando = false;
    fetchImpl = async () => ({
        ok: true,
        json: async () => ({
            success: true,
            worker_iniciado: true,
            lojas: [{
                loja: 'Loja A',
                automacao_ativa: true,
                intervalo_minutos: 5,
                ultima_checagem: '2026-07-18T15:00:04Z',
                proxima_checagem: '2026-07-18T15:05:04Z',
                executando: false,
                sucesso: true,
                erro: '',
                change_token: 'token-a-2',
                question_ids: ['historica-1', 'nova-token-1'],
                new_question_ids: ['nova-token-1'],
                new_questions_count: 1,
                question_snapshot_complete: true,
                contagens: { enviadas: 0, novas_pendentes: 1, erros: 0 }
            }]
        })
    });
    const statusSemNovidade = await context.consultarStatusAutomacaoPerguntas();
    assert(statusSemNovidade && statusSemNovidade.success, 'status vazio deve continuar valido');
    assert.strictEqual(state.automacaoPerguntasRefreshTimer, null, 'token igual nao deve atualizar a lista');
    assert.strictEqual(recargas, 2, 'token igual deve preservar a lista atual');
    assert.strictEqual(recargasContadores, 2, 'token igual deve preservar os contadores');

    context.aplicarStatusBackendAutomacaoPerguntas({
        success: true,
        worker_iniciado: true,
        lojas: [{
            loja: 'Loja A',
            ultima_checagem: '2026-07-18T15:00:05Z',
            change_token: 'token-a-3',
            question_ids: ['historica-1'],
            new_question_ids: [],
            new_questions_count: 0,
            question_snapshot_complete: true,
            contagens: { enviadas: 0, novas_pendentes: 1, erros: 0 }
        }]
    });
    assert.strictEqual(state.automacaoPerguntasRefreshTimer, null, 'token novo sem IDs novos nao deve renderizar');

    state.automacaoPerguntasChangeTokensLojas['Loja A'] = 'token-gap-1';
    state.automacaoPerguntasQuestionIdsLojas['Loja A'] = ['historica-1'];
    const idsRecuperadosAposCicloPerdido = context.registrarTokenStatusPerguntas('Loja A', {
        change_token: 'token-gap-3',
        question_ids: ['historica-1', 'nova-nao-consumida'],
        new_question_ids: [],
        question_snapshot_complete: true
    });
    assert.deepStrictEqual(
        Array.from(idsRecuperadosAposCicloPerdido),
        ['nova-nao-consumida'],
        'snapshot atual deve recuperar novidade mesmo se o delta do ciclo original nao foi consumido'
    );
    delete state.automacaoPerguntasChangeTokensLojas['Loja A'];
    state.automacaoPerguntasQuestionIdsLojas['Loja A'] = ['fallback-q1'];
    const idsNaVoltaDoWorker = context.registrarTokenStatusPerguntas('Loja A', {
        change_token: 'token-worker-retomado',
        question_ids: ['fallback-q1', 'worker-q2'],
        new_question_ids: [],
        question_snapshot_complete: true
    });
    assert.deepStrictEqual(
        Array.from(idsNaVoltaDoWorker),
        ['worker-q2'],
        'retorno do worker deve comparar com a baseline criada pelo fallback local'
    );

    state.lojaSelecionada = 'Loja A';
    context.aplicarStatusBackendAutomacaoPerguntas({
        lojas: [{ loja: 'Loja B', change_token: 'token-b-1', question_ids: [], new_question_ids: [], question_snapshot_complete: true }]
    });
    context.aplicarStatusBackendAutomacaoPerguntas({
        lojas: [{ loja: 'Loja B', change_token: 'token-b-2', question_ids: ['nova-b-1'], new_question_ids: ['nova-b-1'], question_snapshot_complete: true }]
    });
    assert(state.automacaoPerguntasRefreshTimer, 'novidade de outra loja deve agendar apenas contadores');
    await executarTimeout(state.automacaoPerguntasRefreshTimer);
    assert.strictEqual(recargas, 2, 'novidade de outra loja nao pode renderizar a loja selecionada');
    assert.strictEqual(recargasContadores, 3, 'novidade de outra loja deve atualizar badge/contador');

    state.lojaSelecionada = 'Loja B';
    perguntasAtiva = false;
    context.agendarRecarregamentoPerguntasAposPoll('', 0);
    await executarTimeout(state.automacaoPerguntasRefreshTimer);
    assert.strictEqual(recargas, 2, 'aba inativa nao deve renderizar a lista');
    perguntasAtiva = true;
    context.agendarRecarregamentoPerguntasAposPoll('', 0);
    await executarTimeout(state.automacaoPerguntasRefreshTimer);
    assert.strictEqual(recargas, 3, 'ao voltar para a aba, a loja pendente deve atualizar uma vez');

    state.automacaoPerguntasStatusCarregando = false;
    fetchImpl = async () => ({
        ok: false,
        json: async () => ({ detail: 'Rota ainda indisponivel' })
    });
    const indisponivel = await context.consultarStatusAutomacaoPerguntas();
    assert.strictEqual(indisponivel, null, 'status indisponivel deve usar fallback sem propagar erro');
    assert.strictEqual(state.automacaoPerguntasStatusDisponivel, false);
    assert.strictEqual(context.deveExecutarPollFrontendPerguntas(), true, 'status indisponivel deve liberar fallback frontend');
    assert(resumoTodas.textContent.includes('acompanhamento local ativo'), 'fallback local deve ficar visivel');

    state.lojaSelecionada = 'Loja A';
    delete state.automacaoPerguntasQuestionIdsLojas['Loja A'];
    fetchImpl = async () => ({
        ok: true,
        json: async () => ({
            success: true,
            question_snapshots: [{ loja: 'Loja A', question_ids: ['q-1'], question_snapshot_complete: true }]
        })
    });
    await context.executarAutomacaoPerguntas('Loja A', state.automacaoPerguntasGeracao);
    assert.strictEqual(state.automacaoPerguntasRefreshTimer, null, 'primeiro snapshot do fallback deve ser baseline');
    fetchImpl = async () => ({
        ok: true,
        json: async () => ({
            success: true,
            question_snapshots: [{ loja: 'Loja A', question_ids: ['q-1', 'q-2'], question_snapshot_complete: true }]
        })
    });
    const recargasAntesSnapshot = recargas;
    await context.executarAutomacaoPerguntas('Loja A', state.automacaoPerguntasGeracao);
    assert(state.automacaoPerguntasRefreshTimer, 'ID novo no snapshot do poll deve agendar refresh');
    await executarTimeout(state.automacaoPerguntasRefreshTimer);
    assert.strictEqual(recargas, recargasAntesSnapshot + 1, 'snapshot do poll deve renderizar uma vez');

    const intervaloLoja = setIntervalFake(() => {}, 300000);
    const intervaloContador = setIntervalFake(() => {}, 1000);
    const intervaloStatus = setIntervalFake(() => {}, 10000);
    const startup = setTimeoutFake(() => {}, 250);
    const refresh = setTimeoutFake(() => {}, 250);
    state.automacaoPerguntasTimers = [intervaloLoja];
    state.automacaoPerguntasCountdownTimer = intervaloContador;
    state.automacaoPerguntasStatusTimer = intervaloStatus;
    state.automacaoPerguntasStartupTimers = [startup];
    state.automacaoPerguntasRefreshTimer = refresh;
    state.automacaoPerguntasRefreshPendente = true;
    state.automacaoPerguntasRodandoLojas.add('Loja A');
    state.automacaoPerguntasNextChecks = { 'Loja A': agora + 1000 };
    const geracaoAnterior = state.automacaoPerguntasGeracao;
    context.pararAutomacaoPerguntas();
    assert(intervalsCancelados.has(intervaloLoja));
    assert(intervalsCancelados.has(intervaloContador));
    assert(intervalsCancelados.has(intervaloStatus));
    assert(timeoutsCancelados.has(startup));
    assert(timeoutsCancelados.has(refresh));
    assert.strictEqual(state.automacaoPerguntasTimers.length, 0);
    assert.strictEqual(state.automacaoPerguntasStartupTimers.length, 0);
    assert.strictEqual(state.automacaoPerguntasRefreshTimer, null);
    assert.strictEqual(state.automacaoPerguntasRodandoLojas.size, 0);
    assert.strictEqual(state.automacaoPerguntasGeracao, geracaoAnterior + 1);

    let resolverStatusInicial;
    fetchImpl = async (url) => {
        chamadas.push(String(url));
        if (!String(url).endsWith('/api/mercadolivre/perguntas/automacao/status')) {
            return {
                ok: true,
                json: async () => ({ success: true, novas_pendentes: [], pendentes: [], enviadas: [] })
            };
        }
        return new Promise((resolve) => { resolverStatusInicial = resolve; });
    };
    state.automacaoPerguntasStatusDisponivel = false;
    state.automacaoPerguntasWorkerIniciado = false;
    const pollsAntesStartup = chamadas.filter((url) => url.includes('/automacao/poll?')).length;
    context.iniciarAutomacaoPerguntas();
    const startupPerguntasPendente = state.automacaoPerguntasStartupTimers[0];
    const callbackStartup = timeouts.get(startupPerguntasPendente).callback();
    await Promise.resolve();
    assert.strictEqual(
        chamadas.filter((url) => url.includes('/automacao/poll?')).length,
        pollsAntesStartup,
        'startup deve aguardar o primeiro status antes de liberar o fallback'
    );
    resolverStatusInicial({
        ok: true,
        json: async () => ({
            success: true,
            worker_iniciado: true,
            lojas: [{
                loja: 'Loja A',
                automacao_ativa: true,
                intervalo_minutos: 5,
                ultima_checagem: '2026-07-18T15:00:04Z',
                proxima_checagem: '2026-07-18T15:05:04Z',
                executando: false,
                sucesso: true,
                erro: '',
                contagens: { enviadas: 0, novas_pendentes: 0, erros: 0 }
            }]
        })
    });
    await callbackStartup;
    assert.strictEqual(
        chamadas.filter((url) => url.includes('/automacao/poll?')).length,
        pollsAntesStartup,
        'worker confirmado deve impedir poll local duplicado no startup'
    );
    context.pararAutomacaoPerguntas();

    let textareaAtual = {
        value: 'rascunho preservado',
        dataset: { codexProposalId: 'p-1', codexProposalVersion: '2', codexProposalHash: 'hash-1' },
        selectionStart: 3,
        selectionEnd: 9,
        selectionDirection: 'forward',
        scrollTop: 17
    };
    let checkboxAtual = { checked: false };
    const detalhe = {
        scrollTop: 71,
        querySelector: (selector) => selector === '.question-answer-text' ? textareaAtual : checkboxAtual
    };
    const lista = { scrollTop: 43 };
    context.perguntasDetail = detalhe;
    context.perguntasList = lista;
    context.document.activeElement = textareaAtual;
    context.window.scrollX = 11;
    context.window.scrollY = 29;
    context.Event = class FakeEvent { constructor(type) { this.type = type; } };
    context.ajustarAlturaTextareaAtendimento = () => {};
    context.obterEstadoJobAtendimentoCodex = () => null;
    context.salvarEstadoJobAtendimentoCodex = () => null;
    context.aplicarEstadoJobAtendimentoCodex = () => null;
    let scrollRestaurado = null;
    context.window.scrollTo = (x, y) => { scrollRestaurado = [x, y]; };
    vm.runInContext(questionsSource.slice(interactionStart, interactionEnd), context);
    state.perguntaSelecionadaKey = 'Loja A::q-1';
    const snapshotInteracao = context.capturarInteracaoPerguntas();
    let focoRestaurado = false;
    let selecaoRestaurada = null;
    textareaAtual = {
        value: '', dataset: {}, scrollTop: 0,
        dispatchEvent: () => true,
        focus: () => { focoRestaurado = true; },
        setSelectionRange: (...args) => { selecaoRestaurada = args; }
    };
    checkboxAtual = { checked: true, dispatchEvent: () => true, focus: () => {} };
    lista.scrollTop = 0;
    detalhe.scrollTop = 0;
    context.restaurarInteracaoPerguntas(snapshotInteracao);
    assert.strictEqual(textareaAtual.value, 'rascunho preservado');
    assert.strictEqual(checkboxAtual.checked, false);
    assert.strictEqual(textareaAtual.dataset.codexProposalId, 'p-1');
    assert.strictEqual(focoRestaurado, true);
    assert.deepStrictEqual(selecaoRestaurada, [3, 9, 'forward']);
    assert.strictEqual(lista.scrollTop, 43);
    assert.strictEqual(detalhe.scrollTop, 71);
    assert.deepStrictEqual(scrollRestaurado, [11, 29]);

    let resolverFetchPerguntas;
    let rascunhoDuranteFetch = 'rascunho inicial';
    let snapshotRestauradoAposFetch = null;
    let capturasDuranteFetch = 0;
    const refreshState = {
        lojaSelecionada: 'Loja A',
        carregandoPerguntas: false,
        paginaPerguntas: 1,
        tamanhoPaginaPerguntas: 25,
        perguntas: [],
        totalPerguntas: 0,
        perguntaSelecionadaKey: 'Loja A::q-1'
    };
    const refreshContext = {
        state: refreshState,
        URLSearchParams,
        Number,
        Array,
        String,
        Error,
        btnRecarregar: { disabled: false },
        perguntasSummary: { classList: { add: () => {} } },
        perguntasList: { innerHTML: '' },
        perguntasPagination: { innerHTML: '', classList: { add: () => {} } },
        perguntasStatus: { textContent: '' },
        statusFiltro: { value: '' },
        document: {
            getElementById: () => ({ classList: { contains: () => true } })
        },
        todasAsLojasSelecionadas: () => false,
        obterAuthHeaders: () => ({}),
        ordenarPerguntasRecentes: (perguntas) => perguntas,
        renderizarResumo: () => {},
        renderizarPerguntas: () => {},
        registrarUltimaAtualizacaoPerguntas: () => {},
        mensagemErro: (error) => String(error && error.message ? error.message : error || ''),
        capturarInteracaoPerguntas: () => {
            capturasDuranteFetch += 1;
            return {
                perguntaSelecionadaKey: refreshState.perguntaSelecionadaKey,
                resposta: rascunhoDuranteFetch,
                checkboxMarcado: false
            };
        },
        restaurarInteracaoPerguntas: (snapshot) => { snapshotRestauradoAposFetch = snapshot; },
        fetch: () => new Promise((resolve) => { resolverFetchPerguntas = resolve; })
    };
    vm.createContext(refreshContext);
    vm.runInContext(questionsSource.slice(carregarPerguntasStart), refreshContext);
    const carregamentoBackground = refreshContext.carregarPerguntas(1, {
        background: true,
        preservarInteracao: true
    });
    await Promise.resolve();
    assert.strictEqual(typeof resolverFetchPerguntas, 'function', 'fetch em segundo plano deve estar pendente');
    rascunhoDuranteFetch = 'texto digitado enquanto buscava';
    resolverFetchPerguntas({
        ok: true,
        json: async () => ({ questions: [{ id: 'q-1' }], total: 1 })
    });
    assert.strictEqual(await carregamentoBackground, true);
    assert(capturasDuranteFetch >= 2, 'interacao deve ser recapturada imediatamente antes do render');
    assert.strictEqual(
        snapshotRestauradoAposFetch.resposta,
        'texto digitado enquanto buscava',
        'digitacao feita durante a rede nao pode ser substituida pelo snapshot inicial'
    );

    console.log('OK: token por loja, refresh seletivo, estado da tela e cleanup validados.');
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
