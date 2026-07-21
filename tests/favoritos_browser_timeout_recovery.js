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

    let proxy = null;
    let modalOpen = false;
    const visibilityMessages = [];
    fakeTop.postMessage = message => visibilityMessages.push(message);
    const visibilityBridge = fakeWindow.FavoritosV2.browser.shellBridge.createBridge({
        getProxy: () => proxy,
        setProxy: value => { proxy = value; },
        getBounds: () => ({ left: 10, top: 20, width: 900, height: 600 }),
        getDefaultUrl: () => 'https://www.mercadolivre.com.br/',
        isBalloonOpen: () => modalOpen,
        isBackground: () => false,
        areUrlsEquivalent: (left, right) => left === right
    });
    proxy = visibilityBridge.criarProxy();
    proxy.src = 'https://lista.mercadolivre.com.br/modal-fechado';
    assert.strictEqual(
        visibilityMessages.some(message => message.channel === 'jk-ml-browser-show'),
        false,
        'atribuir URL com modal fechado nao pode exibir o navegador'
    );
    assert.strictEqual(
        visibilityMessages.some(message => message.channel === 'jk-ml-browser-hide'),
        true,
        'atribuir URL com modal fechado deve manter o navegador oculto'
    );

    modalOpen = true;
    proxy.src = 'https://lista.mercadolivre.com.br/modal-aberto';
    assert.strictEqual(
        visibilityMessages.filter(message => message.channel === 'jk-ml-browser-show').length,
        1,
        'modal aberto deve permitir exatamente uma exibicao'
    );
    modalOpen = false;
    const showsBeforeForce = visibilityMessages.filter(message => message.channel === 'jk-ml-browser-show').length;
    assert.strictEqual(visibilityBridge.forcarVisivel(), false, 'forcar visibilidade depois de fechar deve ser recusado');
    assert.strictEqual(
        visibilityMessages.filter(message => message.channel === 'jk-ml-browser-show').length,
        showsBeforeForce,
        'timer atrasado nao pode reabrir o navegador'
    );
    proxy.__visible = true;
    visibilityBridge.atualizarPosicao();
    assert.strictEqual(
        visibilityMessages.some(message => message.channel === 'jk-ml-browser-position'),
        false,
        'reposicionamento atrasado nao pode atuar depois que o modal fechou'
    );

    console.log('OK: timeout recupera estado e o navegador fechado nao reaparece por timer atrasado.');
}

main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
