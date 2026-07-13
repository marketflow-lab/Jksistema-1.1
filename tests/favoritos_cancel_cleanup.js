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
assert.match(executionSource, /if \(mlFavoritosEmExecucao\) return;[\s\S]{0,120}mlFavoritosCancelado = false;[\s\S]{0,120}mlFavoritosPausado = false;/, 'nova tentativa deve limpar cancelamento antigo antes do primeiro status');
assert.match(executionSource, /function pararNavegadorFavoritosBackground\(opcoes = \{\}\)[\s\S]*status[\s\S]*message[\s\S]*reason/, 'encerramento do worker deve preservar estado terminal solicitado');
assert.match(mlBrowserSource, /const signal = opcoes\.signal[\s\S]*err\.name = 'AbortError'[\s\S]*Promise\.race[\s\S]*verificarCancelamento\(\)/, 'coleta longa deve ser abortavel entre esperas e scripts');
assert.match(workerSource, /favoritosWorkerBrowserGeneration[\s\S]*assertFavoritosWorkerGeneration[\s\S]*async function startFavoritosWorkerBrowser[\s\S]*assertFavoritosWorkerGeneration\(generation, worker\)/, 'worker deve invalidar inicializacoes antigas por geracao');
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
            this.pendingReject = null;
        }
        isDestroyed() { return this.owner.destroyed; }
        getURL() { return this.url; }
        setUserAgent() {}
        stop() {}
        send() {}
        loadURL(url) { this.url = url; return Promise.resolve(); }
        executeJavaScript() {
            return new Promise((_resolve, reject) => { this.pendingReject = reject; });
        }
        rejectPending() {
            if (this.pendingReject) this.pendingReject(new Error('webContents destroyed'));
            this.pendingReject = null;
        }
    }
    class FakeBrowserWindow extends EventEmitter {
        constructor() {
            super();
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
        ensureChromeExtensionsForMlSession: () => Promise.resolve([]),
        restaurarSessaoAvantProAntesDeAbrirNavegador: (...args) => restoreSession(...args),
        salvarSessaoAvantProAntesDeOcultarNavegador: () => {
            snapshots += 1;
            return Promise.resolve({ success: true });
        }
    });
    vm.runInContext(workerSource, workerContext, { filename: 'favoritos-worker-browser.js' });
    const start = vm.runInContext('startFavoritosWorkerBrowser', workerContext);
    const execute = vm.runInContext('executeFavoritosWorkerBrowser', workerContext);
    const cancel = vm.runInContext('cancelFavoritosWorkerBrowser', workerContext);
    const status = vm.runInContext('favoritosWorkerBrowserStatus', workerContext);

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
}

validarLifecycleWorkerElectron()
    .then(() => console.log('Favoritos cancellation cleanup checks passed'))
    .catch(error => {
        console.error(error);
        process.exitCode = 1;
    });
