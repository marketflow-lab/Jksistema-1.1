'use strict';
const assert = require('node:assert/strict');
const { createCdpAdapter, createMemoryApi, publicCategoryUrl } = require('../scripts/pesquisa_mercado_live_smoke');
const nonce = 'fixture_nonce_123456';
const targetWs = 'ws://127.0.0.1:9425/devtools/page/fixture';
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

class FixtureSocket extends EventTarget {
    static OPEN = 1;
    static marker = nonce;
    static latest;
    constructor() {
        super(); this.readyState = 0; this.url = 'about:blank'; this.complete = true; this.timers = []; this.commands = [];
        FixtureSocket.latest = this;
        queueMicrotask(() => { this.readyState = 1; this.dispatchEvent(new Event('open')); });
    }
    emit(data) { const event = new Event('message'); event.data = JSON.stringify(data); this.dispatchEvent(event); }
    send(text) {
        const { id, method, params } = JSON.parse(text);
        this.commands.push(method);
        let result = {};
        if (method === 'Target.getTargetInfo') result = { targetInfo: { type: 'page' } };
        if (method === 'Page.getFrameTree') result = { frameTree: { frame: { id: 'main', loaderId: 'initial' } } };
        if (method === 'Runtime.evaluate') {
            const value = params.expression === 'globalThis.__jkPesquisaMercadoLiveSmokeMarker' ? FixtureSocket.marker
                : params.expression === 'location.href' ? this.url : { url: this.url, readyState: this.complete ? 'complete' : 'loading' };
            result = { result: { value } };
        }
        if (method === 'Page.navigate') {
            this.complete = false;
            result = { frameId: 'main', loaderId: 'new-document' };
            if (!params.url.endsWith('/abort/')) {
                this.timers.push(setTimeout(() => {
                    this.url = params.url;
                    this.emit({ method: 'Page.frameNavigated', params: { frame: { id: 'main', loaderId: 'new-document', url: this.url } } });
                }, 20));
                this.timers.push(setTimeout(() => {
                    this.complete = true;
                    this.emit({ method: 'Network.responseReceived', params: { type: 'Document', frameId: 'main', response: { url: this.url, status: 200 } } });
                    this.emit({ method: 'Page.loadEventFired', params: {} });
                }, 80));
            }
        }
        queueMicrotask(() => this.emit({ id, result }));
    }
    close() { this.readyState = 3; this.timers.forEach(clearTimeout); this.dispatchEvent(new Event('close')); }
}

async function main() {
    const original = globalThis.WebSocket;
    globalThis.WebSocket = FixtureSocket;
    try {
        const browser = await createCdpAdapter({ targetWs, marker: nonce, commandTimeoutMs: 1000 });
        const started = Date.now();
        let resolved = false;
        const navigation = browser.navigate('https://lista.mercadolivre.com.br/coroas/').then(() => { resolved = true; });
        await wait(35);
        assert.equal(resolved, false, 'navigation ACK and new context do not prove a fully loaded document');
        await navigation;
        assert.ok(Date.now() - started >= 75, 'the adapter waits for the streamed document to complete');
        assert.equal(browser.getURL(), 'https://lista.mercadolivre.com.br/coroas/');
        assert.equal(browser.isLoading(), false);
        assert.deepEqual(browser.getNavigationStatus(), { url: browser.getURL(), statusCode: 200 });
        const controller = new AbortController();
        const aborting = browser.navigate('https://lista.mercadolivre.com.br/abort/', controller.signal);
        controller.abort();
        await assert.rejects(aborting, error => error.name === 'AbortError');
        browser.detach();
        FixtureSocket.marker = 'different_marker';
        await assert.rejects(createCdpAdapter({ targetWs, marker: nonce, commandTimeoutMs: 1000 }), error => error.code === 'target_marker_mismatch');
        assert.equal(FixtureSocket.latest.commands.includes('Page.navigate'), false, 'an unmarked worker is never navigated');
        const memory = createMemoryApi();
        const category = { key: 'MLB1', nome: 'Fixture', url: 'https://lista.mercadolivre.com.br/fixture/' };
        const response = await memory.api('POST', '/api/pesquisa-mercado/jobs', { category });
        await assert.rejects(memory.api('GET', '/api/pesquisa-mercado/jobs/production-id'), error => error.code === 'smoke_api_route_rejected');
        await assert.rejects(memory.api('PUT', '/api/pesquisa-mercado/jobs/' + response.job.id + '/vendedores/a', { generation: 2 }), error => error.code === 'smoke_generation_mismatch');
        assert.equal(publicCategoryUrl(category.url + '?tracking_id=fixture'), category.url);
        assert.equal(publicCategoryUrl('https://www.mercadolivre.com.br/perfil/fixture'), '');
        console.log('Live smoke harness: document load, abort, nonce, memory isolation, generation and public URL sanitization OK');
    } finally { globalThis.WebSocket = original; }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
