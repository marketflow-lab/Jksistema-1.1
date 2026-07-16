'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const read = relative => fs.readFileSync(path.join(root, relative), 'utf8');
const timerSource = read('static/favoritos/v2/ui/elapsed-timer.js');
const historySource = read('static/favoritos/tabelas-layout/05-resultados-historico.js');
const historyUiSource = read('static/favoritos/tabelas-layout/06-ranking-manual-historico-ui.js');
const executionSource = read('static/favoritos/tabelas-layout/07-execucao-render-layout.js');
const statusModalSource = read('static/favoritos/v2/ui/status-modal.js');
const backendHistorySource = read('backend/services/favoritos_storage.py');
const shellSource = read('electron_shell.html');
const syncSource = read('scripts/sync-favoritos-runtime.js');

function extractFunction(source, name, context = {}) {
    const marker = `function ${name}`;
    const markerAt = source.indexOf(marker);
    assert.ok(markerAt >= 0, `funcao ${name} ausente`);
    const start = source.slice(Math.max(0, markerAt - 6), markerAt) === 'async ' ? markerAt - 6 : markerAt;
    const paramsOpen = source.indexOf('(', markerAt);
    let paramsDepth = 0;
    let paramsClose = -1;
    for (let index = paramsOpen; index < source.length; index += 1) {
        const char = source[index];
        if (char === '(') paramsDepth += 1;
        if (char === ')' && --paramsDepth === 0) {
            paramsClose = index;
            break;
        }
    }
    const bodyOpen = source.indexOf('{', paramsClose);
    let depth = 0;
    let quote = '';
    let escaped = false;
    let end = -1;
    for (let index = bodyOpen; index < source.length; index += 1) {
        const char = source[index];
        if (quote) {
            if (escaped) escaped = false;
            else if (char === '\\') escaped = true;
            else if (char === quote) quote = '';
            continue;
        }
        if (char === '"' || char === "'" || char === '`') {
            quote = char;
            continue;
        }
        if (char === '{') depth += 1;
        if (char === '}' && --depth === 0) {
            end = index + 1;
            break;
        }
    }
    assert.ok(end > bodyOpen, `fim de ${name} ausente`);
    return vm.runInNewContext(`(${source.slice(start, end)})`, context);
}

async function testarCronometro() {
    let now = 1000;
    let nextIntervalId = 1;
    const intervals = new Map();
    const ticks = [];
    const context = {
        window: {},
        globalThis: {},
        Date,
        Math,
        Number,
        String,
        setInterval: (callback) => {
            const id = nextIntervalId++;
            intervals.set(id, callback);
            return id;
        },
        clearInterval: id => intervals.delete(id)
    };
    vm.runInNewContext(timerSource, context, { filename: 'elapsed-timer.js' });
    const factory = context.window.FavoritosElapsedTimer;
    assert.ok(factory && typeof factory.create === 'function');
    assert.strictEqual(factory.formatDuration(0), '00:00:00');
    assert.strictEqual(factory.formatDuration(3661000), '01:01:01');

    const timer = factory.create({
        now: () => now,
        setInterval: context.setInterval,
        clearInterval: context.clearInterval,
        onTick: value => ticks.push({ ...value })
    });
    timer.start();
    assert.strictEqual(timer.snapshot().formatted, '00:00:00');
    assert.strictEqual(intervals.size, 1, 'deve existir somente um ticker');

    now = 62000;
    [...intervals.values()][0]();
    assert.strictEqual(timer.snapshot().formatted, '00:01:01', 'Date.now deve absorver atraso/throttling');

    now = 92000;
    const finished = timer.finish({ status: 'done' });
    assert.strictEqual(finished.formatted, '00:01:31');
    assert.strictEqual(finished.active, false);
    assert.strictEqual(intervals.size, 0, 'fim deve eliminar o ticker');

    now = 120000;
    assert.strictEqual(timer.finish({ status: 'done' }).formatted, '00:01:31', 'fim repetido deve permanecer congelado');
    timer.start();
    assert.strictEqual(timer.snapshot().formatted, '00:00:00', 'nova execucao deve zerar o cronometro');
    assert.strictEqual(intervals.size, 1);
    now = 121500;
    assert.strictEqual(timer.finish({ status: 'canceled' }).formatted, '00:00:01');
    assert.strictEqual(intervals.size, 0, 'cancelamento deve eliminar o ticker');
    assert.ok(ticks.length >= 5);
}

async function testarFilaHistorico() {
    const chamadas = [];
    const liberacoes = [];
    const context = {
        Promise,
        String,
        Set,
        Map,
        Math,
        Number,
        mlHistoricoFavoritosPersistenciaFila: Promise.resolve({ success: true, historico: [] }),
        mlHistoricoFavoritosUltimaPersistenciaPromise: Promise.resolve({ success: true, historico: [] }),
        normalizarHistoricoFavoritosFrontend: lista => Array.isArray(lista) ? lista.map(item => ({ ...item })) : [],
        tratarErroSalvarHistoricoFavoritosServidor: () => {},
        lerHistoricoFavoritos: () => [],
        salvarHistoricoFavoritosServidor: lista => new Promise((resolve, reject) => {
            chamadas.push(lista.map(item => item.id));
            liberacoes.push({ resolve, reject, lista });
        })
    };
    context.normalizarDuracaoExecucaoFavoritosMs = extractFunction(
        historySource,
        'normalizarDuracaoExecucaoFavoritosMs',
        { Number, Math }
    );
    const enfileirar = extractFunction(historySource, 'enfileirarSalvamentoHistoricoFavoritosServidor', context);
    context.enfileirarSalvamentoHistoricoFavoritosServidor = enfileirar;
    const confirmar = extractFunction(historySource, 'confirmarSalvamentoHistoricoFavoritosServidor', context);

    const primeira = enfileirar([{ id: 'a' }]);
    const segunda = enfileirar([{ id: 'a' }, { id: 'b' }]);
    await new Promise(resolve => setImmediate(resolve));
    assert.deepStrictEqual(chamadas, [['a']], 'segundo PUT deve aguardar o primeiro');
    liberacoes[0].resolve({ historico: liberacoes[0].lista });
    await primeira;
    await new Promise(resolve => setImmediate(resolve));
    assert.deepStrictEqual(chamadas, [['a'], ['a', 'b']]);
    liberacoes[1].resolve({ historico: liberacoes[1].lista });
    await segunda;
    const confirmado = await confirmar(['a', 'b']);
    assert.strictEqual(confirmado.success, true);
    assert.strictEqual(JSON.stringify(confirmado.faltantes), '[]');

    const terceira = enfileirar([{ id: 'a' }, { id: 'b' }, { id: 'c' }]);
    await new Promise(resolve => setImmediate(resolve));
    liberacoes[2].reject(new Error('HTTP 500'));
    await terceira;
    const falhou = await confirmar(['c']);
    assert.strictEqual(falhou.success, false, 'PUT rejeitado nao pode ser anunciado como salvo');
    assert.match(falhou.erro, /HTTP 500/);

    const quarta = enfileirar([{ id: 'd' }]);
    await new Promise(resolve => setImmediate(resolve));
    liberacoes[3].resolve({ historico: liberacoes[3].lista });
    await quarta;
    const semDuracao = await confirmar(['d'], { duracao_execucao_ms: 90500 });
    assert.strictEqual(semDuracao.success, false, 'ID sem duracao nao pode confirmar o historico final');
    assert.deepStrictEqual(JSON.parse(JSON.stringify(semDuracao.duracao_divergente_ids)), ['d']);

    const quinta = enfileirar([{ id: 'd', duracao_execucao_ms: 90500 }]);
    await new Promise(resolve => setImmediate(resolve));
    liberacoes[4].resolve({ historico: liberacoes[4].lista });
    await quinta;
    const comDuracao = await confirmar(['d'], { duracao_execucao_ms: 90500 });
    assert.strictEqual(comDuracao.success, true, 'ID e duracao devolvidos devem confirmar o historico');
}

async function testarDuracaoNoHistorico() {
    const normalizar = extractFunction(historySource, 'normalizarDuracaoExecucaoFavoritosMs', { Number, Math });
    const formatar = extractFunction(historySource, 'formatarDuracaoExecucaoFavoritos', {
        normalizarDuracaoExecucaoFavoritosMs: normalizar,
        Math,
        String
    });
    assert.strictEqual(formatar(90500), '00:01:30');
    assert.strictEqual(formatar(3661000), '01:01:01');
    for (const invalido of [undefined, null, '', -1, 'invalido']) {
        assert.strictEqual(formatar(invalido), '', `valor invalido ${String(invalido)} deve ser omitido`);
    }

    const original = [
        { id: 'a', grupos: [{ sku: '001' }] },
        { id: 'b', grupos: [{ sku: '002' }] },
        { id: 'antigo', grupos: [{ sku: '003' }] }
    ];
    let salvo = null;
    const context = {
        Promise,
        Set,
        String,
        normalizarDuracaoExecucaoFavoritosMs: normalizar,
        lerHistoricoFavoritos: () => original.map(item => ({ ...item, grupos: item.grupos.map(grupo => ({ ...grupo })) })),
        salvarHistoricoFavoritos: historico => {
            salvo = historico;
            return Promise.resolve({ success: true, historico });
        }
    };
    const atualizar = extractFunction(historySource, 'atualizarDuracaoExecucaoHistoricosFavoritos', context);
    const resultado = await atualizar(['a', 'b'], 90500);
    assert.strictEqual(resultado.success, true);
    assert.strictEqual(salvo.find(item => item.id === 'a').duracao_execucao_ms, 90500);
    assert.strictEqual(salvo.find(item => item.id === 'b').grupos[0].duracao_execucao_ms, 90500);
    assert.strictEqual(salvo.find(item => item.id === 'antigo').duracao_execucao_ms, undefined);
}

async function testarFinalizacaoAtomicaFrontend() {
    const inicio = 1_720_000_000_000;
    let chamadaPersistencia = null;
    const normalizar = extractFunction(historySource, 'normalizarDuracaoExecucaoFavoritosMs', { Number, Math });
    const historicoServidor = [{
        id: 'hist-final',
        duracao_execucao_ms: 5500,
        grupos: [{ sku: '001', duracao_execucao_ms: 5500 }]
    }];
    const context = {
        Date: { now: () => inicio + 5000 },
        Number,
        Math,
        String,
        Set,
        normalizarDuracaoExecucaoFavoritosMs: normalizar,
        lerHistoricoFavoritos: () => [{ id: 'hist-final', grupos: [{ sku: '001' }] }],
        salvarHistoricoFavoritos: (historico, opcoes) => {
            chamadaPersistencia = { historico, opcoes };
            return Promise.resolve({
                success: true,
                historico: historicoServidor,
                duracao_execucao_ms: 5500,
                finalizado_em_ms: inicio + 5500
            });
        },
        confirmarSalvamentoHistoricoFavoritosServidor: async (ids, opcoes) => {
            assert.deepStrictEqual(JSON.parse(JSON.stringify(ids)), ['hist-final']);
            assert.strictEqual(opcoes.duracao_execucao_ms, 5500);
            return { success: true, historico: historicoServidor };
        }
    };
    const finalizar = extractFunction(historySource, 'finalizarDuracaoExecucaoHistoricosFavoritos', context);
    const resultado = await finalizar(['hist-final'], inicio);
    assert.strictEqual(resultado.success, true);
    assert.strictEqual(resultado.duracao_execucao_ms, 5500, 'duracao do commit deve vencer a provisoria do renderer');
    assert.strictEqual(chamadaPersistencia.historico[0].duracao_execucao_ms, 5000, 'fallback local deve guardar duracao provisoria');
    assert.deepStrictEqual(
        JSON.parse(JSON.stringify(chamadaPersistencia.opcoes.finalizarDuracao)),
        { ids: ['hist-final'], inicio_execucao_ms: inicio }
    );
}

async function testarMergeHistoricoPreservaDuracao() {
    const normalizarDuracao = extractFunction(historySource, 'normalizarDuracaoExecucaoFavoritosMs', { Number, Math });
    const context = {
        Math,
        ML_FAVORITOS_HISTORICO_MAX: 80,
        normalizarDuracaoExecucaoFavoritosMs: normalizarDuracao,
        normalizarHistoricoFavoritosFrontend: lista => JSON.parse(JSON.stringify(Array.isArray(lista) ? lista : [])),
        idEntradaHistoricoFavoritos: entrada => String(entrada && entrada.id || '')
    };
    const obterDuracao = extractFunction(historySource, 'duracaoExecucaoMescladaHistoricoFavoritos', context);
    context.duracaoExecucaoMescladaHistoricoFavoritos = obterDuracao;
    const preservar = extractFunction(historySource, 'preservarDuracaoExecucaoMergeHistoricoFavoritos', context);
    context.preservarDuracaoExecucaoMergeHistoricoFavoritos = preservar;
    const mesclar = extractFunction(historySource, 'mesclarHistoricosFavoritos', context);

    const rica = [{
        id: 'hist-1',
        data_iso: '2026-07-12T10:00:00Z',
        duracao_execucao_ms: 90500,
        grupos: [{ sku: '001', duracao_execucao_ms: 90500 }]
    }];
    const antiga = [{
        id: 'hist-1',
        data_iso: '2026-07-12T10:00:00Z',
        grupos: [{ sku: '001' }]
    }];

    for (const resultado of [mesclar(rica, antiga), mesclar(antiga, rica)]) {
        assert.strictEqual(resultado.length, 1);
        assert.strictEqual(resultado[0].duracao_execucao_ms, 90500, 'copia antiga nao pode apagar a duracao');
        assert.strictEqual(resultado[0].grupos[0].duracao_execucao_ms, 90500, 'grupo deve preservar a duracao total');
    }

    const maisLonga = [{ ...rica[0], duracao_execucao_ms: 120000, grupos: [{ sku: '001', duracao_execucao_ms: 120000 }] }];
    assert.strictEqual(mesclar(rica, maisLonga)[0].duracao_execucao_ms, 120000, 'maior duracao valida deve vencer');
}

async function run() {
    await testarCronometro();
    await testarFilaHistorico();
    await testarDuracaoNoHistorico();
    await testarFinalizacaoAtomicaFrontend();
    await testarMergeHistoricoPreservaDuracao();
    const shellInlineScripts = [...shellSource.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)];
    assert.ok(shellInlineScripts.length, 'shell deve conter script inline');
    shellInlineScripts.forEach((match, index) => {
        assert.doesNotThrow(
            () => new vm.Script(match[1], { filename: `electron_shell.inline-${index + 1}.js` }),
            `script inline ${index + 1} do shell deve compilar`
        );
    });
    assert.match(shellSource, /id="favoritos-worker-elapsed"[\s\S]*FavoritosElapsedTimer\?\.create[\s\S]*favoritosWorkerElapsedTimer\.finish/, 'shell deve exibir, iniciar e congelar o cronometro');
    assert.match(shellSource, /favoritosWorkerElapsedTimer\.start\(/, 'shell deve iniciar o cronometro');
    assert.match(shellSource, /favoritosWorkerTerminalLocked[\s\S]*!isFinal && !startSignal/, 'progresso tardio nao deve reabrir cronometro terminal');
    assert.match(historySource, /assinaturaHistoricoFavoritos\(historicoMesclado\)[\s\S]*await enfileirarSalvamentoHistoricoFavoritosServidor\(historicoMesclado\)/, 'toda mesclagem deve usar a mesma fila de persistencia');
    assert.match(historySource, /function atualizarDuracaoExecucaoHistoricosFavoritos[\s\S]*duracao_execucao_ms:[\s\S]*salvarHistoricoFavoritos\(historico, \{ imediato: true \}\)/, 'duracao deve ser aplicada e enfileirada nos IDs da execucao');
    assert.match(historySource, /function preservarDuracaoExecucaoMergeHistoricoFavoritos[\s\S]*Math\.max/, 'merge deve preservar a maior duracao valida');
    assert.match(historySource, /Tempo total: \$\{duracaoExecucao\}/, 'lista recente deve exibir o tempo total');
    assert.match(historyUiSource, /formatarDuracaoExecucaoFavoritos\(entrada\.duracao_execucao_ms\)[\s\S]*Tempo total: \$\{duracaoExecucao\}/, 'detalhe do historico deve exibir o tempo total');
    assert.match(statusModalSource, /startedAt:\s*inicioExecucao/, 'shell deve receber o mesmo inicio usado no historico');
    assert.match(backendHistorySource, /duracao_execucao_ms[\s\S]*_favoritos_duracao_execucao_ms/, 'backend deve preservar a duracao no payload SQLite');
    assert.match(executionSource, /resumoHistorico\.salvos\.length !== resumoHistorico\.total[\s\S]*reason: 'historico_parcial'/, 'historico parcial nao pode encerrar com sucesso');
    assert.match(executionSource, /typeof confirmarSalvamentoHistoricoFavoritosServidor !== 'function'[\s\S]*reason: 'confirmacao_historico_indisponivel'/, 'ausencia do confirmador deve falhar de forma fechada');
    assert.match(executionSource, /Falha na nova coleta de favoritos:[\s\S]*fecharNavegadorFavoritosAposColeta\('favoritos-coleta-erro',[\s\S]*status: 'error'/, 'erro inesperado deve encerrar worker e cronometro');
    assert.match(executionSource, /Salvando o tempo total de[\s\S]*await finalizarDuracaoExecucaoHistoricosFavoritos[\s\S]*duracaoExecucaoPersistidaMs[\s\S]*fecharNavegadorFavoritosAposColeta\('favoritos-coleta-concluida',[\s\S]*finishedAt:/, 'worker so deve finalizar depois do commit atomico da duracao no historico');
    assert.match(historySource, /body\.finalizar_ids[\s\S]*body\.inicio_execucao_ms[\s\S]*duracao_grupos_divergente_ids/, 'PUT final deve pedir commit atomico e confirmar entrada e grupos');
    assert.match(backendHistorySource, /_favoritos_finalizar_duracao_historico[\s\S]*conn\.commit\(\)[\s\S]*finalizado_em_ms/, 'backend deve calcular a duracao dentro da gravacao SQLite');
    assert.match(syncSource, /runtimeCodeFiles\s*=\s*\[[\s\S]*'electron_shell\.html'/, 'sincronizador deve entregar o shell com cronometro');
}

run()
    .then(() => console.log('Favoritos elapsed timer and history confirmation checks passed'))
    .catch(error => {
        console.error(error);
        process.exitCode = 1;
    });
