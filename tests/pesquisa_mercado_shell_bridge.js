'use strict';

const assert = require('assert');
const createBridge = require('../static/pesquisa-mercado/shell-bridge.js');

async function main() {
    const events = new Map();
    const messages = [];
    const commands = [];
    const categoryCalls = [];
    const browserCalls = [];
    const namesCalls = [];
    const category = { key: 'MLB-FERRAMENTAS', nome: 'Ferramentas', url: 'https://lista.mercadolivre.com.br/ferramentas' };
    let progress;
    let unsubscribed = false;
    const source = { postMessage: (value, origin) => messages.push({ value, origin }) };
    const otherModule = { postMessage: value => messages.push(value) };
    const tab = { url: 'http://127.0.0.1:8001/pesquisa_mercado.html' };
    const window = { addEventListener: (name, callback) => events.set(name, callback), removeEventListener: name => events.delete(name) };
    const dispose = createBridge(window, {
        appUrl: 'http://127.0.0.1:8001/dashboard.html',
        findTabBySource: target => target === source ? { tab } : target === otherModule ? { tab: { url: 'http://127.0.0.1:8001/favoritos.html' } } : null,
        getApi: () => ({
            refreshPesquisaMercadoNames: async payload => { namesCalls.push(payload); return { updated: 1, pending: 0 }; },
            startPesquisaMercadoJob: async payload => { commands.push(payload); return { job: { id: 'job-1' } }; },
            getPesquisaMercadoCategories: async payload => { categoryCalls.push(payload); return { items: [], parentCategory: payload.parentCategory || null }; },
            showPesquisaMercadoBrowser: async payload => { browserCalls.push(payload); return { success: true }; },
            onPesquisaMercadoProgress: callback => { progress = callback; return () => { unsubscribed = true; }; }
        })
    });
    const send = (action, payload = {}, origin = 'http://127.0.0.1:8001', target = source) => events.get('message')({
        origin, source: target, data: { channel: 'jk-pesquisa-mercado-command', requestId: 'pm-123-1', action, payload }
    });
    await send('start', { authToken: 'fixture-token' }, 'https://evil.example');
    await send('start', { authToken: 'fixture-token' }, 'http://127.0.0.1:8001', otherModule);
    await send('start', { authToken: 'fixture-token' }, 'http://127.0.0.1:8001', {});
    assert.strictEqual(commands.length, 0, 'origem externa, outros módulos e fontes desconhecidas não podem controlar pesquisa');
    await send('executeJavaScript', { authToken: 'fixture-token' });
    assert.strictEqual(commands.length, 0, 'somente comandos explícitos são permitidos');
    await send('start', { authToken: 'fixture-token' });
    assert.strictEqual(commands.length, 0, 'pesquisa exige categoria selecionada');
    await send('start', { authToken: 'fixture-token', category: { ...category, script: 'untrusted()' }, code: 'untrusted()', arbitrary: true });
    assert.strictEqual(commands.length, 0, 'payload com campos extras é rejeitado pelo schema fechado');
    await send('start', { authToken: 'fixture-token', category });
    assert.deepStrictEqual(commands, [{ authToken: 'fixture-token', category }], 'payload válido preserva categoria');
    assert.deepStrictEqual(messages.at(-1), { origin: 'http://127.0.0.1:8001', value: {
        channel: 'jk-pesquisa-mercado-result', requestId: 'pm-123-1', result: { job: { id: 'job-1' } }
    } });
    await send('start', { authToken: 'fixture-token', category: { ...category, url: 'https://evil.example/' } });
    assert.strictEqual(commands.length, 1, 'categoria de host externo é rejeitada');
    await send('categories', { authToken: 'fixture-token', arbitrary: true });
    await send('categories', { authToken: 'fixture-token', parentCategory: { ...category, code: 'untrusted()' } });
    assert.strictEqual(categoryCalls.length, 0, 'campos extras no comando ou categoria são rejeitados');
    await send('categories', { authToken: 'fixture-token' });
    await send('categories', { authToken: 'fixture-token', parentCategory: category });
    await send('categories', { authToken: 'fixture-token', parentCategory: category, forceRefresh: true });
    await send('categories', { authToken: 'fixture-token', forceRefresh: false });
    assert.deepStrictEqual(categoryCalls, [{ authToken: 'fixture-token' }, { authToken: 'fixture-token', parentCategory: category },
        { authToken: 'fixture-token', parentCategory: category, forceRefresh: true }, { authToken: 'fixture-token', forceRefresh: false }]);
    for (const forceRefresh of ['true', 1, null, [], {}]) await send('categories', { authToken: 'fixture-token', forceRefresh });
    await send('start', { authToken: 'fixture-token', category, forceRefresh: true });
    assert.strictEqual(commands.length, 1, 'refresh só é permitido em categories');
    assert.strictEqual(categoryCalls.length, 4, 'refresh exige booleano');
    await send('categories', { authToken: 'fixture-token', parentCategory: { ...category, key: '' } });
    assert.strictEqual(categoryCalls.length, 4, 'categoria incompleta não é encaminhada');
    await send('browser', { authToken: 'fixture-token', purpose: 'categories', jobId: 'old-job', code: 'untrusted()' });
    assert.strictEqual(browserCalls.length, 0, 'schema do navegador rejeita campos extras');
    await send('browser', { authToken: 'fixture-token', purpose: 'categories' });
    assert.deepStrictEqual(browserCalls, [{ authToken: 'fixture-token', purpose: 'categories' }], 'login de categorias pode abrir navegador antes de criar pesquisa');
    await send('browser', { authToken: 'fixture-token', purpose: 'arbitrary' });
    assert.strictEqual(browserCalls.length, 1, 'finalidade de navegador usa allowlist');
    await send('refresh-names', { authToken: 'fixture-token', jobId: 'saved-job' }, 'https://evil.example');
    await send('refresh-names', { authToken: 'fixture-token', jobId: 'saved-job' }, 'http://127.0.0.1:8001', otherModule);
    await send('refresh-names', { authToken: 'fixture-token', jobId: 'saved-job', code: 'untrusted()' });
    await send('refresh-names', { authToken: 'fixture-token' });
    assert.strictEqual(namesCalls.length, 0, 'recuperacao exige origem, modulo, job e schema fechado');
    await send('refresh-names', { authToken: 'fixture-token', jobId: 'saved-job' });
    assert.deepStrictEqual(namesCalls, [{ authToken: 'fixture-token', jobId: 'saved-job' }]);
    await send('browser', { authToken: 'fixture-token', purpose: 'names' });
    assert.strictEqual(browserCalls.length, 1, 'login de nomes exige pesquisa explicita');
    await send('browser', { authToken: 'fixture-token', purpose: 'names', jobId: 'saved-job' });
    assert.deepStrictEqual(browserCalls.at(-1), { authToken: 'fixture-token', purpose: 'names', jobId: 'saved-job' });
    await send('subscribe');
    progress({ job: { id: 'job-1', revision: 1 } });
    assert.strictEqual(messages.at(-1).value.channel, 'jk-pesquisa-mercado-progress');
    await send('unsubscribe');
    const previous = messages.length;
    progress({ job: { id: 'job-1', revision: 2 } });
    assert.strictEqual(messages.length, previous, 'sair do módulo só remove a assinatura de progresso');
    await send('subscribe');
    tab.url = 'http://127.0.0.1:8001/dashboard.html';
    const beforeRedirect = messages.length;
    progress({ job: { id: 'job-1' } });
    assert.strictEqual(messages.length, beforeRedirect, 'tab que mudou de módulo deixa de receber dados');
    dispose();
    assert.strictEqual(unsubscribed, true);
    assert.strictEqual(events.has('message'), false);
    console.log('Pesquisa de Mercado shell bridge: origem, tab, allowlist e eventos OK');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
