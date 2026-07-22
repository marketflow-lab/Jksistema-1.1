'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { EventEmitter } = require('events');

const root = path.resolve(__dirname, '..');
const read = relative => fs.readFileSync(path.join(root, relative), 'utf8');

const statusModalSource = read('static/favoritos/v2/ui/status-modal.js');
const cancelSource = read('static/favoritos/tabelas-layout/04-promocoes-busca-ranking.js');
const executionSource = read('static/favoritos/tabelas-layout/07-execucao-render-layout.js');
const mlBrowserSource = read('static/favoritos/ml-browser.js');
const workerSource = read('electron_app/main/modules/favoritos-worker-browser.js');
const shellSource = read('electron_shell.html');
const jobsSource = read('backend/services/favoritos_jobs.py');

class FakeClassList {
    constructor(initial = []) { this.values = new Set(initial); }
    add(...names) { names.forEach(name => this.values.add(name)); }
    remove(...names) { names.forEach(name => this.values.delete(name)); }
    contains(name) { return this.values.has(name); }
    toggle(name, force) {
        const enabled = force === undefined ? !this.values.has(name) : !!force;
        if (enabled) this.values.add(name); else this.values.delete(name);
        return enabled;
    }
}

class FakeElement {
    constructor(id = '', classes = []) {
        this.id = id;
        this.classList = new FakeClassList(classes);
        this.styleValues = new Map();
        this.style = { setProperty: (name, value) => this.styleValues.set(name, value) };
        this.textContent = '';
        this.parentElement = null;
        this.children = [];
        this.attributes = {};
        this._innerHTML = '';
    }
    appendChild(child) {
        child.parentElement = this;
        this.children.push(child);
        return child;
    }
    querySelector() { return null; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    set innerHTML(value) { this._innerHTML = String(value); this.children = []; }
    get innerHTML() { return this._innerHTML; }
}

const body = new FakeElement('body');
const status = new FakeElement('ml-favoritos-status');
const live = new FakeElement('ml-work-modal-live-status');
const balloon = new FakeElement('ml-favoritos-status-balloon');
const balloonText = new FakeElement('ml-favoritos-status-balloon-text');
const balloonActions = new FakeElement('ml-favoritos-status-balloon-actions');
const frameWrap = new FakeElement('ml-browser-frame-wrap', ['has-status-overlay']);
const layer = new FakeElement('ml-favoritos-balloon-layer');
layer.appendChild(balloon);
body.appendChild(layer);
balloonText.textContent = 'progresso antigo';
status.textContent = 'progresso antigo';
live.textContent = 'progresso antigo';

const byId = new Map([
    [status.id, status],
    [live.id, live],
    [balloon.id, balloon],
    [balloonText.id, balloonText],
    [balloonActions.id, balloonActions],
    [layer.id, layer]
]);
const executedScripts = [];
const shellMessages = [];
const webview = {
    executeJavaScript(script) {
        executedScripts.push(String(script));
        return Promise.resolve(true);
    }
};
const topWindow = { postMessage: message => shellMessages.push(message) };
const windowObject = { FavoritosV2: {}, top: topWindow };

const context = {
    window: windowObject,
    document: {
        body,
        documentElement: body,
        getElementById: id => byId.get(id) || null,
        createElement: () => new FakeElement(),
        contains: element => [body, layer, balloon].includes(element)
    },
    console,
    setTimeout,
    clearTimeout,
    mlFavoritosStatusEl: status,
    mlWorkModalLiveStatusEl: live,
    mlFavoritosBalloonEl: balloon,
    mlFavoritosBalloonTextEl: balloonText,
    mlFavoritosBalloonActionsEl: balloonActions,
    mlFavoritosBalloonOriginalParentEl: body,
    mlFavoritosBalloonLayerEl: layer,
    mlBrowserFrameWrapEl: frameWrap,
    mlWebviewEl: webview,
    mlFavoritosEmExecucao: true,
    mlFavoritosCancelado: true,
    mlFavoritosPausado: false,
    mlFavoritosExecucaoEmSegundoPlano: true,
    balaoResultadosMlAberto: () => true,
    usarNavegadorMlNoShellElectron: () => true,
    agendarAtualizacaoPosicaoNavegadorMlShell: () => {}
};
context.globalThis = context;
vm.runInNewContext(statusModalSource, context, { filename: 'status-modal.js' });
assert.strictEqual(vm.runInNewContext('typeof mlWebviewEl', context), 'object');
assert.strictEqual(vm.runInNewContext('typeof mlWebviewEl.executeJavaScript', context), 'function');
assert.strictEqual(vm.runInNewContext('usarNavegadorMlNoShellElectron()', context), true);

context.window.mostrarBalaoFavoritosStatus('progresso atrasado');
context.window.atualizarStatusFavoritosNoNavegadorMl('', false, { imediato: true, forcar: true });
assert.ok(balloon.classList.contains('hidden'), 'progresso tardio nao pode reabrir balao');
assert.ok(live.classList.contains('hidden'), 'status fixo do modal deve fechar');
assert.strictEqual(status.textContent, '', 'status principal deve ser limpo');
assert.strictEqual(live.textContent, '', 'status ao vivo deve ser limpo');
assert.strictEqual(balloonText.textContent, '', 'texto antigo do balao deve ser limpo');
assert.ok(!frameWrap.classList.contains('has-status-overlay'), 'faixa reservada do overlay deve ser removida');
assert.strictEqual(frameWrap.styleValues.get('--ml-favoritos-status-overlay-height'), '0px');
assert.ok(
    executedScripts.some(script => script.includes('jk-favoritos-status-overlay') && script.includes('remove()')),
    `DOM remoto deve receber remocao forcada; scripts=${executedScripts.length}`
);
assert.ok(shellMessages.some(message => (
    message.channel === 'jk-favoritos-worker-done'
    && message.payload.active === false
    && message.payload.status === 'canceled'
    && message.payload.message === ''
)), 'shell deve receber terminal canceled sem mensagem residual');

assert.match(cancelSource, /AbortController[\s\S]*\.abort\(\)[\s\S]*pararPollingFavoritosJob[\s\S]*limparStatusTerminalFavoritos[\s\S]*pararNavegadorFavoritosBackground/, 'cancelamento deve abortar, parar polling e fechar worker/status');
assert.doesNotMatch(cancelSource, /Cancelando favoritos\.\.\./, 'cancelamento nao deve manter aviso intermediario aberto');
assert.match(executionSource, /signal:\s*sinalFavoritosAtual\(\)[\s\S]*if \(mlFavoritosCancelado \|\| !mlFavoritosEmExecucao\) return;/, 'coletor deve receber signal e bloquear progresso atrasado');
assert.match(executionSource, /mlFavoritosPoolStatusTimer[\s\S]*setTimeout\([\s\S]*250\)/, 'progresso agregado do pool deve ser limitado a uma atualizacao a cada 250 ms');
assert.match(executionSource, /if \(mlFavoritosEmExecucao\) return;[\s\S]{0,120}mlFavoritosCancelado = false;[\s\S]{0,120}mlFavoritosPausado = false;/, 'nova tentativa deve limpar cancelamento antigo antes do primeiro status');
assert.match(executionSource, /function pararNavegadorFavoritosBackground\(opcoes = \{\}\)[\s\S]*status[\s\S]*message[\s\S]*reason/, 'encerramento do worker deve preservar estado terminal solicitado');
assert.match(mlBrowserSource, /const signal = opcoes\.signal[\s\S]*err\.name = 'AbortError'[\s\S]*Promise\.race[\s\S]*verificarCancelamento\(\)/, 'coleta longa deve ser abortavel entre esperas e scripts');
assert.match(workerSource, /favoritosWorkerBrowsers\s*=\s*new Map\(\)[\s\S]*generation:\s*0[\s\S]*assertFavoritosWorkerGeneration[\s\S]*async function startFavoritosWorkerBrowser[\s\S]*assertFavoritosWorkerGeneration\(record, generation, worker\)/, 'cada worker deve invalidar inicializacoes antigas por geracao independente');
assert.match(workerSource, /async function cancelFavoritosWorkerBrowser[\s\S]*status:\s*'canceled'[\s\S]*worker\.destroy\(\)[\s\S]*salvarSessaoAvantProAntesDeOcultarNavegador/, 'Electron deve destruir o worker antes de salvar o snapshot de cancelamento');
assert.match(shellSource, /favoritosWorkerCancellationLocked[\s\S]*canceled \|\| \(!favoritosWorkerBrowserActive && !message\)[\s\S]*if \(canceled\) return;/, 'shell deve ocultar canceled imediatamente e bloquear progresso tardio');
assert.match(jobsSource, /FAVORITOS_JOB_MODE_AVANTPRO_BROWSER[\s\S]*job\["status"\] = "canceled"/, 'job visual deve ficar terminal ao cancelar');
assert.match(jobsSource, /status_code=409, detail="Job de favoritos ja foi cancelado/, 'resultado tardio nao pode ressuscitar job cancelado');

async function validarLifecycleWorkerElectron() {
    const windows = [];
    class FakeWebContents extends EventEmitter {
        constructor(owner) {
            super();
            this.owner = owner;
            this.url = 'https://www.mercadolivre.com.br/';
            this.loadCalls = [];
            this.pendingReject = null;
            this.immediateExecutionResult = undefined;
        }
        isDestroyed() { return this.owner.destroyed; }
        getURL() { return this.url; }
        setUserAgent() {}
        stop() {}
        send() {}
        loadURL(url) { this.loadCalls.push(url); this.url = url; return Promise.resolve(); }
        executeJavaScript() {
            if (this.immediateExecutionResult !== undefined) {
                return Promise.resolve(this.immediateExecutionResult);
            }
            return new Promise((_resolve, reject) => { this.pendingReject = reject; });
        }
        rejectPending() {
            if (this.pendingReject) this.pendingReject(new Error('webContents destroyed'));
            this.pendingReject = null;
        }
    }
    class FakeBrowserWindow extends EventEmitter {
        constructor(options = {}) {
            super();
            this.options = options;
            this.destroyed = false;
            this.visible = false;
            this.webContents = new FakeWebContents(this);
            windows.push(this);
        }
        static getAllWindows() { return windows.filter(win => !win.destroyed); }
        isDestroyed() { return this.destroyed; }
        isVisible() { return this.visible; }
        setMenuBarVisibility() {}
        setAlwaysOnTop() {}
        setSkipTaskbar() {}
        setTitle(value) { this.title = value; }
        showInactive() { this.visible = true; this.emit('show'); }
        show() { this.visible = true; this.emit('show'); }
        hide() { this.visible = false; this.emit('hide'); }
        focus() {}
        getBounds() { return { width: 1280, height: 900 }; }
        destroy() {
            if (this.destroyed) return;
            this.destroyed = true;
            this.webContents.rejectPending();
            this.emit('closed');
        }
    }

    let restoreSession = () => Promise.resolve({ success: true });
    let snapshots = 0;
    let ensureExtensionsCalls = 0;
    const workerContext = vm.createContext({
        BrowserWindow: FakeBrowserWindow,
        URL,
        console,
        Date,
        Promise,
        setTimeout,
        clearTimeout,
        mainWindow: null,
        favoritosEmbeddedMlLastUrl: 'https://www.mercadolivre.com.br/',
        ML_BROWSER_USER_AGENT: 'test-agent',
        normalizeTargetUrl: value => String(value || ''),
        normalizeComparableUrl: value => String(value || '').replace(/\/$/, ''),
        isMercadoLivreHost: host => /mercadolivre\.com\.br$/.test(host),
        getMlSession: () => ({}),
        getBrowserSessionPartition: () => 'persist:jk-sistema-browser',
        registerAvantProConsoleDiagnostics: () => {},
        registerEmbeddedMlBrowserDownloadGuard: () => {},
        logElectronLifecycle: () => {},
        isIgnorableNavigationAbort: () => false,
        waitForWebContentsLoad: () => Promise.resolve({ loaded: true }),
        waitMs: ms => new Promise(resolve => setTimeout(resolve, Math.min(Number(ms) || 0, 1))),
        ensureChromeExtensionsForMlSession: () => {
            ensureExtensionsCalls += 1;
            return Promise.resolve([]);
        },
        restaurarSessaoAvantProAntesDeAbrirNavegador: (...args) => restoreSession(...args),
        salvarSessaoAvantProAntesDeOcultarNavegador: () => {
            snapshots += 1;
            return Promise.resolve({ success: true });
        }
    });
    vm.runInContext(workerSource, workerContext, { filename: 'favoritos-worker-browser.js' });
    const start = vm.runInContext('startFavoritosWorkerBrowser', workerContext);
    const stop = vm.runInContext('stopFavoritosWorkerBrowser', workerContext);
    const execute = vm.runInContext('executeFavoritosWorkerBrowser', workerContext);
    const cancel = vm.runInContext('cancelFavoritosWorkerBrowser', workerContext);
    const status = vm.runInContext('favoritosWorkerBrowserStatus', workerContext);
    const startPool = vm.runInContext('startFavoritosWorkersPool', workerContext);
    const stopPool = vm.runInContext('stopFavoritosWorkersPool', workerContext);
    const poolStatus = vm.runInContext('favoritosWorkersPoolStatus', workerContext);

    await start('https://www.mercadolivre.com.br/', null, { show: false });
    assert.strictEqual(status().active, true, 'worker deve iniciar ativo');
    const firstWindow = windows.at(-1);
    const execution = execute('1 + 1');
    await new Promise(resolve => setTimeout(resolve, 0));
    const canceled = await cancel();
    await assert.rejects(execution, error => error && error.name === 'AbortError');
    assert.strictEqual(firstWindow.destroyed, true, 'cancelamento deve destruir a janela no mesmo ciclo');
    assert.strictEqual(canceled.status, 'canceled');
    assert.strictEqual(canceled.active, false);
    assert.strictEqual(canceled.hasWindow, false);
    assert.strictEqual(snapshots, 1, 'snapshot deve ocorrer depois do destroy sem duplicacao');

    let releaseRestore = null;
    restoreSession = () => new Promise(resolve => { releaseRestore = resolve; });
    const staleStart = start('https://www.mercadolivre.com.br/', null, { show: false });
    await new Promise(resolve => setTimeout(resolve, 0));
    await cancel();
    releaseRestore({ success: true });
    await assert.rejects(staleStart, error => error && error.name === 'AbortError');
    assert.strictEqual(status().status, 'canceled', 'start atrasado nao pode ressuscitar estado running');

    restoreSession = () => Promise.resolve({ success: true });
    await start('https://www.mercadolivre.com.br/', null, { show: false });
    assert.strictEqual(status().active, true, 'nova geracao deve iniciar normalmente apos cancelamento');
    assert.strictEqual(status().status, 'running');

    let restorePoolCalls = 0;
    restoreSession = () => {
        restorePoolCalls += 1;
        return Promise.resolve({ success: true });
    };
    const snapshotsAntesPool = snapshots;
    const workerLegadoAntesPool = windows.at(-1);
    const windowsAntesPool = windows.length;
    const poolIniciado = await startPool({
        size: 4,
        visible: true,
        initialUrl: 'https://lista.mercadolivre.com.br/sku-inicial',
        deferInitialNavigation: true
    });
    assert.strictEqual(poolIniciado.counts.total, 4, 'processo principal deve criar quatro registros no pool');
    assert.strictEqual(poolIniciado.counts.active, 4, 'os quatro trabalhadores devem iniciar ativos');
    assert.strictEqual(restorePoolCalls, 1, 'sessao e extensao devem ser preparadas uma unica vez por pool');
    assert.strictEqual(workerLegadoAntesPool.destroyed, true, 'inicio do pool deve destruir o navegador legado w0 que ja estava vivo');
    assert.strictEqual(status().hasWindow, false, 'inicio do pool nao pode manter BrowserWindow do w0');
    assert.strictEqual(snapshots, snapshotsAntesPool + 1, 'handoff do w0 para o pool deve salvar a sessao exatamente uma vez');
    assert.strictEqual(
        windows.slice(windowsAntesPool).reduce((total, win) => total + win.webContents.loadCalls.length, 0),
        0,
        'pool adiado deve criar janelas sem carregar a mesma pesquisa em todos os trabalhadores'
    );
    assert.strictEqual(
        windows.filter(win => !win.destroyed && /^Favoritos ML - Trabalhador [1-4]/.test(win.title || win.options.title || '')).length,
        4,
        'pool deve manter quatro janelas visiveis e identificadas'
    );
    assert.strictEqual(
        windows.filter(win => !win.destroyed && (win.title || win.options.title || '') === 'Favoritos ML - Navegador Trabalhador').length,
        0,
        'pool ativo deve manter zero BrowserWindow legado w0'
    );
    const restoresAntesDoBloqueioW0 = restorePoolCalls;
    const extensionsAntesDoBloqueioW0 = ensureExtensionsCalls;
    await assert.rejects(
        () => start('https://www.mercadolivre.com.br/', null, { show: false }),
        error => error && error.code === 'FAVORITOS_WORKERS_POOL_ACTIVE',
        'start padrao do w0 deve ser rejeitado enquanto o pool estiver ativo'
    );
    await assert.rejects(
        () => execute('true'),
        error => error && error.code === 'FAVORITOS_WORKERS_POOL_ACTIVE',
        'execute padrao do w0 deve ser rejeitado enquanto o pool estiver ativo'
    );
    assert.strictEqual(restorePoolCalls, restoresAntesDoBloqueioW0, 'w0 bloqueado deve falhar antes de restaurar a sessao');
    assert.strictEqual(ensureExtensionsCalls, extensionsAntesDoBloqueioW0, 'w0 bloqueado deve falhar antes de preparar extensoes');
    assert.strictEqual(
        windows.filter(win => !win.destroyed && (win.title || win.options.title || '') === 'Favoritos ML - Navegador Trabalhador').length,
        0,
        'start/execute bloqueados nao podem criar BrowserWindow w0'
    );
    const statusLegadoDurantePool = status();
    assert.strictEqual(statusLegadoDurantePool.workerId, 'w0', 'status do w0 deve continuar consultavel durante o pool');
    assert.strictEqual(statusLegadoDurantePool.hasWindow, false);
    const stopLegadoDurantePool = await stop({ destroy: true, skipSessionSave: true, reason: 'teste-w0-pool-ativo' });
    assert.strictEqual(stopLegadoDurantePool.workerId, 'w0', 'stop do w0 deve continuar chamavel durante o pool');
    assert.strictEqual(stopLegadoDurantePool.hasWindow, false);
    assert.throws(() => status('w5'), /Identificador invalido/, 'processo principal deve rejeitar worker fora de w1-w4');
    await stopPool({ status: 'done', destroy: true, reason: 'teste-pool' });
    assert.strictEqual(poolStatus().active, false, 'stop do pool deve finalizar o estado agregado');
    assert.strictEqual(snapshots, snapshotsAntesPool + 2, 'snapshot final do pool deve ser separado do snapshot unico de handoff');
    const legadoRetomado = await start('https://www.mercadolivre.com.br/', null, {
        show: false,
        deferInitialNavigation: true
    });
    assert.strictEqual(legadoRetomado.workerId, 'w0', 'apos parar o pool, o worker legado deve voltar a iniciar');
    windows.at(-1).webContents.immediateExecutionResult = 42;
    assert.strictEqual(await execute('40 + 2'), 42, 'apos parar o pool, o worker legado deve voltar a executar JavaScript');
    await cancel({ skipSessionSave: true });
    const poolUm = await startPool({
        size: 1,
        visible: false,
        initialUrl: 'https://www.mercadolivre.com.br/'
    });
    assert.strictEqual(poolUm.counts.total, 1, 'um novo pool menor nao pode contar registros de trabalhadores antigos');
    assert.strictEqual(poolUm.workers.length, 1, '1 SKU deve expor somente o trabalhador necessario');
    await stopPool({ status: 'done', destroy: true, reason: 'teste-pool-menor' });
}

validarLifecycleWorkerElectron()
    .then(() => console.log('Favoritos cancellation cleanup checks passed'))
    .catch(error => {
        console.error(error);
        process.exitCode = 1;
    });
