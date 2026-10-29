'use strict';

// Real page and durable storage; only synthetic HTTP responses and identities.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(process.env.JK_IMPORTACOES_TEST_SOURCE_ROOT || path.join(__dirname, '..'));
const temporaryRoot = path.resolve(process.env.JK_TEST_TMP || os.tmpdir());
const profile = fs.mkdtempSync(path.join(temporaryRoot, 'jk-importacoes-imediato-'));
const origin = 'http://127.0.0.1:8132';
const key = 'importacoes:GET:/api/medias-compras/listas-pedidos';
const errors = [];
const gates = [];
let context;
function gate() {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    const result = { promise, resolve };
    gates.push(result);
    return result;
}
function lista(revision) {
    return { id: 'synthetic-a', nome_lista: 'Pedido ' + revision, numero_invoice: 'INV-A',
        status: 'Analisando orçamento', store_id: 'store-a', loja: 'Loja A',
        total_itens: 1, total_quantidade: revision, total_usd: 10, total_m3: 0.1,
        itens: [{ SKU: 'SYN-001', Quantidade: revision, 'Valor unidade': 10 }] };
}
async function start({ hold = false, holdFont = false, revision = 1, fail = false } = {}) {
    context = await chromium.launchPersistentContext(profile, { headless: true });
    const state = { hold, holdFont, revision, fail, denied: false, requests: 0, fontRequests: 0,
        pending: gate(), font: gate(), writes: [], unexpected: [] };
    await context.addInitScript(() => {
        if (location.origin !== 'http://127.0.0.1:8132') return;
        if (!localStorage.getItem('user_data')) {
            localStorage.setItem('user_data', JSON.stringify({ client_id: 'tenant-a', username: 'reader' }));
            localStorage.setItem('permissions', JSON.stringify({ medias_compras: true }));
            localStorage.setItem('access_token', 'synthetic-token');
        }
        // Accelerate only the production request deadline, not rendering or storage.
        const timeout = window.setTimeout.bind(window);
        window.setTimeout = (callback, delay, ...args) => timeout(callback,
            window.__testTimeout && delay === 15000 ? 100 : delay, ...args);
        const transaction = IDBDatabase.prototype.transaction;
        IDBDatabase.prototype.transaction = function (names, mode, ...args) {
            if (window.__holdWrites && mode === 'readwrite') {
                window.__writeAttempted = true;
                const store = { put() {}, delete() {}, getAll: () => ({}) };
                return { abort() {}, objectStore: () => store };
            }
            return transaction.call(this, names, mode, ...args);
        };
    });
    context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
    const json = (route, data, status = 200) => route.fulfill({ status,
        contentType: 'application/json', body: JSON.stringify(data) }).catch(() => {});
    await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.hostname === 'fonts.googleapis.com') {
            state.fontRequests++;
            if (state.holdFont) await state.font.promise;
            return route.fulfill({ contentType: 'text/css', body: '' }).catch(() => {});
        }
        if (url.hostname === 'economia.awesomeapi.com.br') return json(route, { USDBRL: { bid: '5' } });
        assert.equal(url.origin, origin, 'Nenhuma API real pode ser acessada.');
        const p = url.pathname;
        if (p === '/auth.js') return route.fulfill({ contentType: 'application/javascript',
            body: 'window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({Authorization:"Bearer synthetic"});' });
        if (p === '/importacoes.html' || p === '/cache-persistente.js' || p.startsWith('/medias_compras/')) {
            return route.fulfill({ contentType: p.endsWith('.html') ? 'text/html' : 'application/javascript',
                body: fs.readFileSync(path.join(root, 'static', p.slice(1))) });
        }
        if (p === '/api/medias-compras/listas-pedidos') {
            state.requests++;
            const body = { listas: [lista(state.revision)] };
            const status = state.denied ? 403 : state.fail ? 503 : 200;
            if (state.hold) await state.pending.promise;
            return json(route, status === 200 ? body : { detail: 'Resposta sintética' }, status);
        }
        if (p === '/api/vendas/sync/start' && route.request().method() === 'POST') {
            state.writes.push(p);
            return json(route, { success: true });
        }
        if (p === '/api/lojas') return json(route, [{ store_id: 'store-a', nome: 'Loja A' }]);
        if (p === '/api/cadastro/fornecedores' || p === '/api/cadastro/nacionalizadores') return json(route, []);
        if (p === '/ia-sidebar.js' || p === '/favicon.ico') return route.fulfill({ body: '' });
        state.unexpected.push(p);
        return route.abort();
    });
    return state;
}
async function open() {
    const page = await context.newPage();
    page.setDefaultTimeout(8000);
    await page.goto(origin + '/importacoes.html', { waitUntil: 'commit' });
    return page;
}
const rows = page => page.locator('#listaWrap .lista-item');
const status = page => page.locator('#cacheImportacoesStatus');
async function visible(page, revision) {
    await page.waitForFunction(value => document.querySelector('#listaWrap .nome-lista')?.textContent === 'Pedido ' + value, revision);
}
async function saved(page) {
    await page.waitForFunction(async key => !!await JKCachePersistente.ler(key), key);
}

(async () => {
    try {
        let state = await start();
        let page = await open();
        await visible(page, 1);
        await saved(page);
        await context.close();

        state = await start({ hold: true, holdFont: true, revision: 2 });
        page = await open();
        await visible(page, 1);
        assert.match(await status(page).textContent(), /Cópia salva.*segundo plano/);
        assert(state.fontRequests > 0, 'A fonte está pendente enquanto as listas já aparecem.');
        assert(state.requests > 0, 'A atualização já foi iniciada em segundo plano.');
        await page.locator('#listaWrap .nome-lista').dblclick();
        const input = page.locator('#listaWrap input').first();
        await input.fill('Edição ainda não salva');
        const refreshed = page.waitForResponse(response => response.url() === origin + '/api/medias-compras/listas-pedidos');
        state.pending.resolve();
        await refreshed;
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
        assert.equal(await input.inputValue(), 'Edição ainda não salva');
        await input.press('Escape');
        await visible(page, 2);
        await saved(page);
        state.font.resolve();
        console.log('PASS reinício: cópia aparece com fonte/API pendentes; atualização preserva edição.');

        state.hold = true;
        state.pending = gate();
        await page.evaluate(() => window.dispatchEvent(new CustomEvent('jk:vendas-sync-finished')));
        const stale = await page.evaluate(async key => ({ strict: await JKCachePersistente.ler(key),
            stale: (await JKCachePersistente.ler(key, { permitirDesatualizado: true }))?.desatualizado }), key);
        assert.equal(stale.strict, null);
        assert.equal(stale.stale, true);
        await context.close();
        state.pending.resolve();

        state = await start({ hold: true, revision: 3 });
        page = await open();
        await visible(page, 2);
        assert.match(await status(page).textContent(), /Cópia salva/);
        state.pending.resolve();
        await visible(page, 3);
        await saved(page);
        console.log('PASS sincronização: cópia desatualizada sobrevive ao reinício e é revalidada.');

        state.pending = gate();
        state.revision = 4;
        const before = state.requests;
        await page.evaluate(() => { void carregarListasImportacoes(); });
        await page.waitForFunction(() => !!controladorListasImportacoes);
        while (state.requests === before) await new Promise(resolve => setTimeout(resolve, 5));
        state.hold = false;
        state.revision = 5;
        await page.evaluate(() => fetch('/api/vendas/sync/start', { method: 'POST' }));
        await visible(page, 5);
        state.pending.resolve();
        await saved(page);
        assert.equal(await page.locator('#listaWrap .nome-lista').textContent(), 'Pedido 5');
        console.log('PASS invalidação durante GET: nova consulta vence resposta antiga.');

        state.hold = true;
        state.pending = gate();
        await page.evaluate(() => { window.__testTimeout = true; void carregarListasImportacoes(); });
        await page.waitForFunction(() => document.getElementById('cacheImportacoesStatus').textContent.includes('Não foi possível atualizar'));
        await visible(page, 5);
        state.pending.resolve();
        state.hold = false;
        state.revision = 6;
        await page.evaluate(() => { window.__testTimeout = false; window.__holdWrites = true; void carregarListasImportacoes(); });
        await visible(page, 6);
        await page.waitForFunction(() => window.__writeAttempted === true);
        assert.match(await status(page).textContent(), /Dados atualizados/);
        await page.evaluate(() => { window.__holdWrites = false; });
        console.log('PASS timeout preserva cópia; IndexedDB lento não bloqueia resposta nova.');

        state.revision = 7;
        await page.evaluate(async () => {
            const original = Storage.prototype.setItem;
            Storage.prototype.setItem = function (key, value) {
                if (key.startsWith('jk.ui.cache.')) throw new DOMException('Quota sintética', 'QuotaExceededError');
                return original.call(this, key, value);
            };
            try { await fetch('/api/vendas/sync/start', { method: 'POST' }); }
            finally { Storage.prototype.setItem = original; }
            await carregarListasImportacoes();
        });
        await visible(page, 7);
        await page.reload();
        await saved(page);
        console.log('PASS falha no armazenamento permite carregar a resposta autorizada da API.');

        const second = await context.newPage();
        await second.goto(origin + '/importacoes.html');
        await visible(second, 7);
        state.revision = 8;
        await second.evaluate(() => JKCachePersistente.invalidar(['importacoes']));
        await visible(page, 8);
        await visible(second, 8);
        console.log('PASS invalidação manual entre abas consulta dados novos sem mensagem de sessão expirada.');

        state.denied = true;
        await page.evaluate(() => carregarListasImportacoes());
        assert.equal(await rows(page).count(), 0);
        assert.equal(await page.evaluate(async key => await JKCachePersistente.ler(key, { permitirDesatualizado: true }), key), null);
        await second.waitForFunction(() => acessoNegadoListasImportacoes === true);
        const deniedRequests = state.requests;
        await page.evaluate(() => window.dispatchEvent(new CustomEvent('jk:vendas-sync-finished')));
        await second.evaluate(() => window.dispatchEvent(new CustomEvent('jk:vendas-sync-finished')));
        await page.waitForTimeout(300);
        assert.equal(state.requests, deniedRequests, 'Abas com acesso negado não entram em ciclo de reconsulta.');
        await context.close();
        state = await start({ hold: true });
        page = await open();
        await page.waitForFunction(() => typeof carregarListasImportacoes === 'function' && !!controladorListasImportacoes);
        assert.equal(await rows(page).count(), 0, 'Reabrir não revive cópia após 403.');
        await page.evaluate(() => { window.__testTimeout = true; void carregarListasImportacoes(); });
        await page.waitForFunction(() => document.getElementById('status').textContent.includes('demorou mais que o esperado'));
        assert.equal(await rows(page).count(), 0);
        state.hold = false;
        state.pending.resolve();
        await page.locator('#btnAtualizar').click();
        await visible(page, 1);
        await saved(page);
        const other = await context.newPage();
        await other.goto(origin + '/importacoes.html');
        await other.evaluate(() => localStorage.setItem('permissions', JSON.stringify({ medias_compras: false })));
        await page.waitForFunction(() => document.querySelectorAll('#listaWrap .lista-item').length === 0);
        assert.equal(await page.evaluate(async key => await JKCachePersistente.ler(key, { permitirDesatualizado: true }), key), null);
        assert.deepEqual(state.unexpected, []);
        assert.deepEqual(errors, []);
        console.log('PASS 403/reinício e mudança de permissões não reutilizam cópia revogada.');
    } finally {
        gates.forEach(item => item.resolve());
        await context?.close();
        assert.equal(path.dirname(path.resolve(profile)), temporaryRoot);
        assert(path.basename(profile).startsWith('jk-importacoes-imediato-'));
        fs.rmSync(profile, { recursive: true, force: true });
    }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
