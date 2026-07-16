'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const runtimeSource = fs.readFileSync(path.join(root, 'static', 'vendas', 'runtime.js'), 'utf8');
const periodoSource = fs.readFileSync(path.join(root, 'static', 'vendas', 'periodo-cache.js'), 'utf8');
const syncSource = fs.readFileSync(path.join(root, 'static', 'vendas', 'sync.js'), 'utf8');

assert.strictEqual((runtimeSource.match(/\/api\/vendas\/sync\/progress/g) || []).length, 1);
assert.strictEqual((periodoSource.match(/\/api\/vendas\/sync\/progress/g) || []).length, 0);
assert.strictEqual((syncSource.match(/\/api\/vendas\/sync\/progress/g) || []).length, 0);
assert.match(periodoSource, /}, 250\);/);
assert.match(syncSource, /waitForInactive\(\)/);

const documentListeners = new Map();
const windowListeners = new Map();
const pendingFetches = [];
let fetchCount = 0;

const documentMock = {
    visibilityState: 'visible',
    getElementById: () => null,
    querySelector: () => null,
    addEventListener(type, listener) { documentListeners.set(type, listener); }
};

const windowMock = {
    addEventListener(type, listener) {
        const list = windowListeners.get(type) || [];
        list.push(listener);
        windowListeners.set(type, list);
    },
    dispatchEvent(event) {
        for (const listener of windowListeners.get(event.type) || []) listener(event);
        return true;
    }
};

class CustomEventMock {
    constructor(type, options = {}) {
        this.type = type;
        this.detail = options.detail;
    }
}

const context = {
    window: windowMock,
    document: documentMock,
    CustomEvent: CustomEventMock,
    DOMException,
    AbortController,
    console,
    setTimeout,
    clearTimeout,
    localStorage: { getItem: () => null, setItem: () => {} },
    obterAuthHeaders: () => ({ Authorization: 'Bearer test' }),
    fetch: () => {
        fetchCount += 1;
        return new Promise(resolve => pendingFetches.push(resolve));
    }
};
vm.createContext(context);
vm.runInContext(runtimeSource, context, { filename: 'runtime.js' });

const monitor = windowMock.__jkVendasSyncMonitor;
assert.ok(monitor, 'monitor compartilhado deve ser publicado no window');

function resolveNext(payload) {
    const resolve = pendingFetches.shift();
    assert.ok(resolve, 'uma chamada fetch deve estar pendente');
    resolve({ ok: true, status: 200, json: async () => payload });
}

(async () => {
    let finishedEvents = 0;
    windowMock.addEventListener('jk:vendas-sync-finished', () => { finishedEvents += 1; });

    monitor.start();
    const first = monitor.refresh();
    const duplicate = monitor.refresh();
    assert.strictEqual(fetchCount, 1, 'chamadas simultaneas devem compartilhar uma unica requisicao');
    resolveNext({ active: true, progress: { etapa: 'Dia', percentual: 20 } });
    await Promise.all([first, duplicate]);

    const completion = monitor.refresh();
    assert.strictEqual(fetchCount, 2);
    resolveNext({ active: false, progress: { etapa: 'Finalizado', percentual: 100 } });
    await completion;
    assert.strictEqual(finishedEvents, 1, 'a transicao ativa -> inativa deve emitir um unico evento');

    const stillIdle = monitor.refresh();
    resolveNext({ active: false, progress: { etapa: 'Finalizado', percentual: 100 } });
    await stillIdle;
    assert.strictEqual(finishedEvents, 1, 'polls inativos repetidos nao podem duplicar a conclusao');

    documentMock.visibilityState = 'hidden';
    const beforeHidden = fetchCount;
    await monitor.refresh();
    assert.strictEqual(fetchCount, beforeHidden, 'pagina oculta nao deve consultar progresso');

    monitor.stop();
    console.log('vendas optimization regression: ok');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
