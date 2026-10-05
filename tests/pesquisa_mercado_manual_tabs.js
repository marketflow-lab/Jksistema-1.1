'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const core = require('../static/pesquisa-mercado/core.js');
const source = fs.readFileSync(path.join(__dirname, '../static/pesquisa-mercado/runtime.js'), 'utf8');

function harness(shell) {
    const listeners = new Map();
    const operations = [];
    const elements = new Map();
    const window = {
        PesquisaMercadoCore: core, innerWidth: 1440, innerHeight: 1000,
        location: {}, addEventListener(name, callback) { listeners.set(name, callback); }
    };
    function loaded(url) {
        queueMicrotask(() => listeners.get('message')({
            source: window.top, data: { channel: 'jk-ml-browser-event', event: 'did-finish-load', url }
        }));
    }
    window.top = shell ? { postMessage(data) {
        operations.push(data);
        if (data.channel === 'jk-ml-browser-show') loaded(data.payload.url);
    } } : window;
    window.electronAPI = {
        async showEmbeddedMlBrowser(url) { operations.push({ channel: 'jk-ml-browser-show', payload: { url } }); return { success: true, url }; },
        async hideEmbeddedMlBrowser() { operations.push({ channel: 'jk-ml-browser-hide' }); },
        async positionEmbeddedMlBrowser() {}, async executeEmbeddedMlBrowser() {}
    };
    const context = {
        window, console, setTimeout, clearTimeout, URL, Map, Set,
        alert() { throw new Error('Unexpected permission prompt'); },
        localStorage: { getItem(key) { return JSON.stringify(key === 'user_data' ? { id: 'fixture' } : { pesquisa_mercado: true }); } },
        document: { getElementById(id) {
            if (!elements.has(id)) elements.set(id, {
                value: '', style: {}, classList: { toggle() {} }, addEventListener() {},
                getBoundingClientRect() { return { left: 10, top: 200, right: 900, bottom: 700 }; }
            });
            return elements.get(id);
        } }
    };
    // Exercise the real runtime's private async operations without a production test API.
    vm.runInNewContext(source.replace(/\}\)\(\);\s*$/, 'window.testRuntime = { navegar, state, proxy: () => browserProxy }; })();'), context);
    return {
        ...window.testRuntime, operations,
        mode(mode) { listeners.get('jk-pesquisa-mercado-mode-change')({ detail: { mode } }); },
        shows() { return operations.filter(item => item.channel === 'jk-ml-browser-show'); }
    };
}

async function check(shell) {
    const h = harness(shell);
    const first = 'https://www.mercadolivre.com.br/perfil/VENDEDOR_TESTE';
    const second = 'https://lista.mercadolivre.com.br/_CustId_123';
    await h.navegar(first);
    h.state.ads = [{ id: 'MLB1000000001' }];
    h.state.running = true;
    h.mode('global');
    assert.strictEqual(h.proxy().visible, false);
    assert.strictEqual(h.operations.at(-1).channel, 'jk-ml-browser-hide');
    h.mode('global');
    let complete = false;
    const pending = h.navegar(second).then(() => { complete = true; });
    await new Promise(resolve => setTimeout(resolve, 180));
    assert.strictEqual(complete, false, 'manual collection waits while its tab is hidden');
    assert.strictEqual(h.shows().length, 1, 'pending manual navigation cannot overlay global research');
    assert.strictEqual(h.state.paused, false, 'tab selection does not change explicit pause');
    h.mode('manual');
    assert.strictEqual(h.shows().at(-1).payload.url, first, 'restore the previous manual browser');
    await pending;
    assert.strictEqual(h.proxy().visible, true);
    assert.strictEqual(h.proxy().currentUrl, second);
    assert.strictEqual(h.state.ads.length, 1, 'switching preserves manual results');

    h.state.paused = true;
    h.mode('global');
    h.mode('manual');
    const paused = h.navegar(first);
    const count = h.shows().length;
    await new Promise(resolve => setTimeout(resolve, 180));
    assert.strictEqual(h.shows().length, count, 'returning to manual respects its explicit pause');
    h.state.paused = false;
    await paused;
    h.mode('global');
    const cancelled = h.navegar(second);
    h.state.cancelled = true;
    await assert.rejects(cancelled, /cancelada/);
    assert.strictEqual(h.shows().at(-1).payload.url, first, 'cancel hidden navigation before loading it');
}

(async () => {
    await check(false);
    await check(true);
    console.log('Pesquisa de Mercado manual tabs: hidden browser, navigation gate, preserved state and independent pause/cancel OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
