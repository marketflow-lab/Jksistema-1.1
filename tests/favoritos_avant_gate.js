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

    let snapshotPayload = null;
    const memoriaLocal = [];
    const registrarContext = {
        obterElectronApiPersistenciaAvantProFavoritos: () => ({
            saveAvantProStorageSnapshot: async (_reason, payload) => {
                snapshotPayload = payload;
                return { success: false, reason: 'busy' };
            }
        }),
        localStorage: {
            setItem: (key, value) => memoriaLocal.push([key, value])
        }
    };
    const registrarConfirmacao = extractFunction('registrarConfirmacaoUsuarioLoginAvantProFavoritos', registrarContext);
    const registroParcial = await registrarConfirmacao('prompt_usuario_confirmou', {
        liveAuthConfirmed: false
    });
    assert.strictEqual(registroParcial.success, false, 'falha do snapshot deve ser reportada ao chamador best-effort');
    assert.strictEqual(registroParcial.memoriaLocalSalva, true, 'memoria local deve ser tentada mesmo quando o snapshot falha');
    assert.strictEqual(memoriaLocal.length, 1, 'confirmacao do usuario deve ficar registrada localmente');
    assert.strictEqual(snapshotPayload.userConfirmed, true, 'snapshot deve distinguir confirmacao humana');
    assert.strictEqual(snapshotPayload.liveAuthConfirmed, false, 'confirmacao humana nao deve fingir validacao DOM');

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
    assert.strictEqual(loginPendente.result, false, 'login explicitamente visivel deve continuar bloqueando no fluxo manual');
    assert.strictEqual(loginPendente.abriuLogin, 1);
    assert.strictEqual(loginPendente.continuou, 0);

    const semQualquerSessao = await validarComEstado({
        indeterminado: true,
        pendente: false,
        detectado: false,
        storageUsable: false
    });
    assert.strictEqual(semQualquerSessao.result, false, 'fluxo manual sem DOM e sem storage nao pode iniciar coleta');

    const snapshotBestEffort = await validarComEstado({
        indeterminado: false,
        pendente: false,
        detectado: true,
        storageUsable: false
    }, { success: false, reason: 'busy' });
    assert.strictEqual(snapshotBestEffort.result, true, 'falha secundaria do snapshot nao deve bloquear login reconhecido');
    assert.strictEqual(snapshotBestEffort.snapshots, 1);
    assert.strictEqual(snapshotBestEffort.continuou, 1);

    let resolverSnapshotPendente = null;
    let snapshotPendenteResolvido = false;
    const memoriaLocalPendente = [];
    const snapshotPendente = new Promise(resolve => {
        resolverSnapshotPendente = resultado => {
            snapshotPendenteResolvido = true;
            resolve(resultado);
        };
    });
    const registrarPendente = extractFunction('registrarConfirmacaoUsuarioLoginAvantProFavoritos', {
        obterElectronApiPersistenciaAvantProFavoritos: () => ({
            saveAvantProStorageSnapshot: async () => snapshotPendente
        }),
        localStorage: {
            setItem: (key, value) => memoriaLocalPendente.push([key, value])
        }
    });
    const mensagensPrompt = [];
    const acoesPrompt = [];
    let confirmacoesBestEffort = 0;
    let validacoesMercadoLivre = 0;
    let validacoesAvantManual = 0;
    let leiturasEstadoAvant = 0;
    const registrarBestEffort = extractFunction('registrarConfirmacaoUsuarioLoginAvantProBestEffortFavoritos', {
        console,
        registrarConfirmacaoUsuarioLoginAvantProFavoritos: (...args) => {
            confirmacoesBestEffort += 1;
            return registrarPendente(...args);
        }
    });
    const promptContext = {
        window: {},
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
        registrarConfirmacaoUsuarioLoginAvantProBestEffortFavoritos: registrarBestEffort,
        obterEstadoAutenticacaoAvantProFavoritos: async () => {
            leiturasEstadoAvant += 1;
            throw new Error('revalidacao Avant nao deveria ocorrer');
        },
        abrirBalaoResultadosMl: () => {},
        fecharBalaoResultadosMl: () => {},
        balaoResultadosMlAberto: () => true,
        obterEstadoAutenticacaoMercadoLivreFavoritos: async () => ({ pendente: false, indeterminado: false }),
        validarLoginMercadoLivreAntesDeContinuarFavoritos: async callback => {
            validacoesMercadoLivre += 1;
            return callback();
        },
        validarLoginAvantProAntesDeContinuarFavoritos: async callback => {
            validacoesAvantManual += 1;
            await callback();
            return true;
        },
        mostrarBotaoContinuarLoginAvantProFavoritos: () => ({})
    };
    const perguntarLogin = extractFunction('perguntarLoginAvantProAntesFavoritos', promptContext);
    const respostaPrompt = perguntarLogin({ selecionados: [], quantidade: 1 });
    assert.ok(
        mensagensPrompt.includes('Antes de fazer favoritos, o Avant Pro ja esta logado?'),
        'pergunta deve aparecer mesmo quando existe sessao persistida'
    );
    const botaoSim = acoesPrompt.find(button => button.textContent === 'Sim, continuar');
    assert.ok(botaoSim, 'pergunta deve oferecer confirmacao explicita');
    assert.strictEqual(validacoesAvantManual, 0, 'antes do clique Sim nao deve haver validacao Avant Pro');
    const cliqueSim = botaoSim.__click();
    const respostaAntesDoSnapshot = await Promise.race([
        respostaPrompt.then(valor => ({ resolvida: true, valor })),
        new Promise(resolve => setImmediate(() => resolve({ resolvida: false })))
    ]);
    assert.deepStrictEqual(
        respostaAntesDoSnapshot,
        { resolvida: true, valor: true },
        'Promise da pergunta deve resolver antes do snapshot IPC pendente'
    );
    assert.strictEqual(snapshotPendenteResolvido, false, 'snapshot deve continuar pendente quando o Sim finaliza');
    assert.strictEqual(memoriaLocalPendente.length, 1, 'localStorage deve ser gravado antes do await IPC');
    resolverSnapshotPendente({ success: true });
    await cliqueSim;
    await new Promise(resolve => setImmediate(resolve));
    assert.strictEqual(confirmacoesBestEffort, 1, 'Sim deve registrar a confirmacao sem revalidar o Avant Pro');
    assert.strictEqual(validacoesAvantManual, 0, 'Sim nao deve invocar a validacao programatica do Avant Pro');
    assert.ok(
        mensagensPrompt.every(mensagem => !mensagem.includes('Validando Avant Pro') && !mensagem.includes('Validando login e sessao salva')),
        'caminho Sim nao deve exibir a etapa redundante de validacao Avant Pro'
    );
    const respostaSegundoPrompt = perguntarLogin({ selecionados: [], quantidade: 1 });
    const segundoBotaoSim = acoesPrompt.find(button => button.textContent === 'Sim, continuar');
    assert.ok(segundoBotaoSim, 'nova execucao deve montar uma nova confirmacao');
    await segundoBotaoSim.__click();
    assert.strictEqual(await respostaSegundoPrompt, true);
    assert.strictEqual(confirmacoesBestEffort, 2, 'cada confirmacao deve registrar memoria best-effort');
    assert.strictEqual(validacoesAvantManual, 0, 'nova confirmacao Sim tambem deve confiar no usuario');
    assert.strictEqual(
        mensagensPrompt.filter(mensagem => mensagem === 'Antes de fazer favoritos, o Avant Pro ja esta logado?').length,
        2,
        'cada nova execucao deve apresentar sua propria pergunta'
    );

    const respostaLoginManual = perguntarLogin({ selecionados: [], quantidade: 1 });
    const botaoNao = acoesPrompt.find(button => button.textContent === 'Nao, abrir login');
    assert.ok(botaoNao, 'pergunta deve permitir abrir o login manual');
    botaoNao.__click();
    await new Promise(resolve => setImmediate(resolve));
    const botaoContinuar = acoesPrompt.find(button => button.textContent === 'Continuar favoritos');
    assert.ok(botaoContinuar, 'login manual deve oferecer Continuar favoritos');
    await botaoContinuar.__click();
    assert.strictEqual(await respostaLoginManual, true);
    assert.strictEqual(validacoesMercadoLivre, 1, 'fluxo manual deve manter a validacao do Mercado Livre');
    assert.strictEqual(validacoesAvantManual, 1, 'fluxo Nao deve preservar a validacao programatica do Avant Pro');
    assert.strictEqual(confirmacoesBestEffort, 2, 'helper de confirmacao humana direta deve ficar restrito ao Sim');
    assert.strictEqual(leiturasEstadoAvant, 0, 'o prompt nao deve ler o DOM diretamente fora do validador manual');

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
    assert.match(
        source,
        /sim\.addEventListener\('click', async[\s\S]*registrarConfirmacaoUsuarioLoginAvantProBestEffortFavoritos\('prompt_usuario_confirmou'\)[\s\S]*finalizar\(true\)/,
        'Sim deve registrar a confirmacao humana e finalizar sem validar o DOM'
    );
    assert.doesNotMatch(
        source,
        /sim\.addEventListener\('click', async[^}]*validarLoginAvantProAntesDeContinuarFavoritos/,
        'handler Sim nao deve chamar a validacao programatica do Avant Pro'
    );
    assert.doesNotMatch(
        source,
        /sim\.addEventListener\('click', async[^}]*await registrarConfirmacaoUsuarioLoginAvantProBestEffortFavoritos/,
        'handler Sim nao deve aguardar o snapshot best-effort'
    );
    assert.match(
        source,
        /function registrarConfirmacaoUsuarioLoginAvantProBestEffortFavoritos[\s\S]*registrarConfirmacaoUsuarioLoginAvantProFavoritos[\s\S]*\.then\([\s\S]*\.catch\([\s\S]*return true/,
        'helper best-effort deve tratar a Promise sem aguardar o IPC'
    );
    assert.match(
        source,
        /function registrarConfirmacaoUsuarioLoginAvantProFavoritos[\s\S]*localStorage\.setItem[\s\S]*saveAvantProStorageSnapshot/,
        'memoria local deve ser gravada antes do await IPC'
    );
    assert.match(
        source,
        /const validarEFinalizar = botao => validarLoginMercadoLivreAntesDeContinuarFavoritos\([\s\S]*validarLoginAvantProAntesDeContinuarFavoritos\(\(\) => finalizar\(true\), botao\)/,
        'fluxo Nao deve manter as validacoes Mercado Livre e Avant Pro'
    );
    assert.match(
        source,
        /Avant Pro nao retornou dados coletaveis[\s\S]*erroLoginAvantProFavoritos/,
        'ausencia real de dados Avant durante a coleta deve continuar detectada'
    );
}

run()
    .then(() => console.log('Favoritos Avant gate checks passed'))
    .catch(error => {
        console.error(error);
        process.exitCode = 1;
    });
