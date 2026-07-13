'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(
    path.join(root, 'static', 'favoritos', 'tabelas-layout', '04-promocoes-busca-ranking.js'),
    'utf8'
);

function extractFunction(name, context = {}) {
    const marker = `function ${name}`;
    const start = source.indexOf(marker);
    assert.ok(start >= 0, `funcao ${name} ausente`);
    const functionStart = source.slice(Math.max(0, start - 6), start) === 'async ' ? start - 6 : start;
    const paramsOpen = source.indexOf('(', start);
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
    assert.ok(paramsClose > paramsOpen, `parametros de ${name} invalidos`);
    const open = source.indexOf('{', paramsClose);
    let depth = 0;
    let quote = '';
    let escaped = false;
    let end = -1;
    for (let index = open; index < source.length; index += 1) {
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
    assert.ok(end > open, `fim de ${name} ausente`);
    return vm.runInNewContext(`(${source.slice(functionStart, end)})`, context);
}

async function run() {
    const storageApi = {
        getAvantProStorageStatus: async () => ({
            success: true,
            currentUsable: false,
            snapshotUsable: true,
            manifest: { savedAt: '2026-07-10T21:34:25.512Z' }
        })
    };
    const apiContext = { window: { FavoritosV2: {}, electronAPI: storageApi } };
    const obterApi = extractFunction('obterElectronApiPersistenciaAvantProFavoritos', apiContext);
    apiContext.obterElectronApiPersistenciaAvantProFavoritos = obterApi;
    const obterStatus = extractFunction('obterStatusPersistenciaAvantProFavoritos', apiContext);
    const persisted = await obterStatus();
    assert.strictEqual(persisted.currentUsable, false);
    assert.strictEqual(persisted.snapshotUsable, true);
    assert.strictEqual(persisted.usable, true, 'snapshot valido deve liberar fallback persistido');

    const estadoContext = {
        mlWebviewEl: null,
        obterStatusPersistenciaAvantProFavoritos: async () => persisted
    };
    const obterEstado = extractFunction('obterEstadoAutenticacaoAvantProFavoritos', estadoContext);
    const estadoSemDom = await obterEstado();
    assert.strictEqual(estadoSemDom.indeterminado, true);
    assert.strictEqual(estadoSemDom.storageUsable, true, 'DOM ausente deve preservar sinal da sessao salva');

    async function validarComEstado(estado, snapshotResult = { success: true }) {
        const status = [];
        let abriuLogin = 0;
        let snapshots = 0;
        let continuou = 0;
        const context = {
            console,
            mostrarBalaoFavoritosStatus: mensagem => status.push(String(mensagem || '')),
            abrirBalaoResultadosMl: () => { abriuLogin += 1; },
            obterEstadoAutenticacaoAvantProFavoritos: async () => estado,
            registrarConfirmacaoUsuarioLoginAvantProFavoritos: async () => {
                snapshots += 1;
                return snapshotResult;
            }
        };
        const validar = extractFunction('validarLoginAvantProAntesDeContinuarFavoritos', context);
        const result = await validar(async () => {
            continuou += 1;
            return true;
        }, { disabled: true });
        return { result, status, abriuLogin, snapshots, continuou };
    }

    const fallbackPersistido = await validarComEstado({
        indeterminado: true,
        pendente: false,
        detectado: false,
        storageUsable: true
    });
    assert.strictEqual(fallbackPersistido.result, true);
    assert.strictEqual(fallbackPersistido.continuou, 1);
    assert.strictEqual(fallbackPersistido.snapshots, 0, 'reuso persistido nao deve sobrescrever snapshot sem confirmacao DOM');
    assert.ok(fallbackPersistido.status[0].includes('Validando login'));

    const loginPendente = await validarComEstado({
        indeterminado: false,
        pendente: true,
        detectado: true,
        storageUsable: true
    });
    assert.strictEqual(loginPendente.result, false, 'login explicitamente visivel deve continuar bloqueando');
    assert.strictEqual(loginPendente.abriuLogin, 1);
    assert.strictEqual(loginPendente.continuou, 0);

    const semQualquerSessao = await validarComEstado({
        indeterminado: true,
        pendente: false,
        detectado: false,
        storageUsable: false
    });
    assert.strictEqual(semQualquerSessao.result, false, 'sem DOM e sem storage nao pode iniciar coleta');

    const snapshotBestEffort = await validarComEstado({
        indeterminado: false,
        pendente: false,
        detectado: true,
        storageUsable: false
    }, { success: false, reason: 'busy' });
    assert.strictEqual(snapshotBestEffort.result, true, 'falha secundaria do snapshot nao deve bloquear login reconhecido');
    assert.strictEqual(snapshotBestEffort.snapshots, 1);
    assert.strictEqual(snapshotBestEffort.continuou, 1);

    const mensagensPrompt = [];
    const acoesPrompt = [];
    const promptContext = {
        mlFavoritosBalloonEl: {},
        mlFavoritosPerguntaResolver: null,
        mlFavoritosBalloonActionsEl: {
            _innerHTML: '',
            set innerHTML(value) {
                this._innerHTML = String(value || '');
                if (!value) acoesPrompt.length = 0;
            },
            get innerHTML() {
                return this._innerHTML;
            },
            appendChild(button) {
                acoesPrompt.push(button);
            }
        },
        document: {
            createElement: () => {
                const handlers = {};
                return {
                    type: '',
                    textContent: '',
                    disabled: false,
                    addEventListener: (type, handler) => { handlers[type] = handler; },
                    __click: () => handlers.click && handlers.click()
                };
            }
        },
        mostrarBalaoFavoritosStatus: mensagem => mensagensPrompt.push(String(mensagem || '')),
        esconderBalaoFavoritosStatus: () => {},
        limparBotaoContinuarLoginAvantProFavoritos: () => {},
        obterTermoInicialLoginAvantProFavoritos: () => '',
        validarLoginAvantProAntesDeContinuarFavoritos: async callback => {
            await callback();
            return true;
        }
    };
    const perguntarLogin = extractFunction('perguntarLoginAvantProAntesFavoritos', promptContext);
    const respostaPrompt = perguntarLogin({ selecionados: [], quantidade: 1 });
    assert.ok(
        mensagensPrompt.includes('Antes de fazer favoritos, o Avant Pro ja esta logado?'),
        'pergunta deve aparecer mesmo quando existe sessao persistida'
    );
    const botaoSim = acoesPrompt.find(button => button.textContent === 'Sim, continuar');
    assert.ok(botaoSim, 'pergunta deve oferecer confirmacao explicita');
    await botaoSim.__click();
    assert.strictEqual(await respostaPrompt, true);
    const respostaSegundoPrompt = perguntarLogin({ selecionados: [], quantidade: 1 });
    const segundoBotaoSim = acoesPrompt.find(button => button.textContent === 'Sim, continuar');
    assert.ok(segundoBotaoSim, 'nova execucao deve montar uma nova confirmacao');
    await segundoBotaoSim.__click();
    assert.strictEqual(await respostaSegundoPrompt, true);
    assert.strictEqual(
        mensagensPrompt.filter(mensagem => mensagem === 'Antes de fazer favoritos, o Avant Pro ja esta logado?').length,
        2,
        'cada nova execucao deve apresentar sua propria pergunta'
    );

    assert.doesNotMatch(
        source,
        /estadoPersistido\.storageUsable[\s\S]*Sessao salva do Avant Pro encontrada[\s\S]*return true;/,
        'sessao persistida nao deve pular a pergunta de confirmacao do usuario'
    );
    assert.match(
        source,
        /async function perguntarLoginAvantProAntesFavoritos[\s\S]*Antes de fazer favoritos, o Avant Pro ja esta logado\?[\s\S]*Nao, abrir login/,
        'cada nova execucao deve perguntar se o Avant Pro ja esta logado'
    );
}

run()
    .then(() => console.log('Favoritos Avant gate checks passed'))
    .catch(error => {
        console.error(error);
        process.exitCode = 1;
    });
