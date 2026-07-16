'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

async function main() {
    const root = path.resolve(__dirname, '..');
    const source = fs.readFileSync(
        path.join(root, 'static', 'favoritos', 'v2', 'browser', 'shell-bridge.js'),
        'utf8'
    );
    const sent = [];
    const fakeTop = {
        postMessage(message) {
            sent.push(message);
        }
    };
    const fakeWindow = {
        FavoritosV2: {},
        top: fakeTop,
        parent: fakeTop
    };
    const sandbox = {
        window: fakeWindow,
        console,
        Date,
        Map,
        Promise,
        setTimeout,
        clearTimeout
    };
    vm.runInNewContext(source, sandbox, { filename: 'shell-bridge.js' });

    const bridge = fakeWindow.FavoritosV2.browser.shellBridge.createBridge({
        getProxy: () => null,
        setProxy: () => null,
        getBounds: () => ({ left: 10, top: 20, width: 900, height: 600 }),
        getDefaultUrl: () => 'https://www.mercadolivre.com.br/'
    });
    const expectedUrl = 'https://www.mercadolivre.com.br/';
    const pending = bridge.verificarEstado(expectedUrl, 1000);
    assert.strictEqual(sent.length, 1, 'consulta deve enviar exatamente uma mensagem ao shell');
    assert.strictEqual(sent[0].channel, 'jk-ml-browser-status');
    assert.strictEqual(sent[0].payload.expectedUrl, expectedUrl);
    assert.ok(sent[0].payload.requestId, 'consulta deve ser correlacionada por requestId');

    const handled = bridge.handleMessage({
        channel: 'jk-ml-browser-status-result',
        requestId: sent[0].payload.requestId,
        result: {
            success: true,
            available: true,
            attached: true,
            url: expectedUrl,
            authFlow: false
        }
    });
    assert.strictEqual(handled, true, 'resposta de estado deve ser reconhecida pelo bridge');
    const state = await pending;
    assert.deepStrictEqual(JSON.parse(JSON.stringify(state)), {
        success: true,
        available: true,
        attached: true,
        url: expectedUrl,
        authFlow: false
    });

    console.log('OK: timeout do navegador pode recuperar a URL real antes de exibir falso erro.');
}

main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
