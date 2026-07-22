'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

function carregarBridge() {
    const root = path.resolve(__dirname, '..');
    const workerControllerSource = fs.readFileSync(
        path.join(root, 'static', 'favoritos', 'v2', 'browser', 'worker-controller.js'),
        'utf8'
    );
    const shellBridgeSource = fs.readFileSync(
        path.join(root, 'static', 'favoritos', 'v2', 'browser', 'shell-bridge.js'),
        'utf8'
    );
    const calls = [];
    const messages = [];
    const fakeTop = {
        postMessage(message) {
            messages.push(message);
        }
    };
    const electronAPI = {
        startFavoritosWorkerBrowser(...args) {
            calls.push({ method: 'start', args });
            return Promise.resolve({ success: true, url: args[0] || '' });
        },
        executeFavoritosWorkerBrowser(...args) {
            calls.push({ method: 'execute', args });
            return Promise.resolve(true);
        }
    };
    const fakeWindow = {
        FavoritosV2: {},
        electronAPI,
        top: fakeTop,
        parent: fakeTop,
        __JK_FAVORITOS_WORKER_BROWSER_ACTIVE: true,
        __JK_FAVORITOS_WORKERS_POOL_ACTIVE: false
    };
    const sandbox = {
        window: fakeWindow,
        console,
        Date,
        Map,
        Promise,
        URL,
        setTimeout,
        clearTimeout
    };
    vm.runInNewContext(workerControllerSource, sandbox, { filename: 'worker-controller.js' });
    vm.runInNewContext(shellBridgeSource, sandbox, { filename: 'shell-bridge.js' });
    return { fakeWindow, calls, messages };
}

function criarBridge(fakeWindow) {
    let proxy = null;
    const bridge = fakeWindow.FavoritosV2.browser.shellBridge.createBridge({
        getProxy: () => proxy,
        setProxy: value => { proxy = value; },
        getBounds: () => ({ left: 10, top: 20, width: 900, height: 600 }),
        getDefaultUrl: () => 'https://www.mercadolivre.com.br/',
        isBalloonOpen: () => true,
        isBackground: () => true,
        areUrlsEquivalent: (left, right) => left === right
    });
    return { bridge, getProxy: () => proxy };
}

async function main() {
    const { fakeWindow, calls, messages } = carregarBridge();
    const { bridge } = criarBridge(fakeWindow);

    const proxyLegado = bridge.criarProxy();
    assert.ok(proxyLegado, 'fora do pool, o proxy legado w0 deve continuar disponivel');

    fakeWindow.__JK_FAVORITOS_WORKERS_POOL_ACTIVE = true;
    assert.strictEqual(
        bridge.criarProxy(),
        null,
        'pool ativo nao pode criar um novo proxy legado que cairia no w0'
    );

    proxyLegado.src = 'https://lista.mercadolivre.com.br/furadeira';
    await new Promise(resolve => setTimeout(resolve, 0));
    await assert.rejects(
        () => proxyLegado.executeJavaScript('document.title'),
        error => error && error.code === 'FAVORITOS_WORKERS_POOL_ACTIVE',
        'pool ativo deve rejeitar a execucao no proxy legado antes de atingir o w0'
    );
    assert.deepStrictEqual(
        calls,
        [],
        'pool ativo nao pode iniciar nem executar JavaScript no worker legado w0'
    );
    assert.strictEqual(
        messages.some(message => /^jk-ml-browser-(?:show|execute)$/.test(message.channel)),
        false,
        'bloqueio do w0 nao pode desviar a operacao para o BrowserView legado'
    );

    fakeWindow.__JK_FAVORITOS_WORKERS_POOL_ACTIVE = false;
    proxyLegado.src = 'https://lista.mercadolivre.com.br/parafusadeira';
    await new Promise(resolve => setTimeout(resolve, 0));
    const legacyResult = await proxyLegado.executeJavaScript('document.title');
    assert.strictEqual(legacyResult, true);
    assert.deepStrictEqual(
        calls.map(call => ({ method: call.method, args: Array.from(call.args) })),
        [
            { method: 'start', args: ['https://lista.mercadolivre.com.br/parafusadeira'] },
            { method: 'execute', args: ['document.title'] }
        ],
        'fora do pool, o caminho legado deve continuar usando o worker padrao w0'
    );

    console.log('OK: pool bloqueia o w0 e o caminho legado continua valido fora do pool.');
}

main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
