'use strict';
// Synthetic browser fixture: no application server, credentials or marketplace traffic.
const assert = require('assert');
const path = require('path');
const { chromium } = require('playwright');

(async () => {
    const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
        ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : { channel: 'chrome' }) });
    try {
        const page = await browser.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.clock.install({ time: new Date('2026-09-09T12:00:00Z') });
        await page.clock.pauseAt(new Date('2026-09-09T12:00:01Z'));
        await page.setContent('<div id="status"></div><div id="stores"></div><div id="post"></div>');
        await page.evaluate(() => {
            window.state = { lojas: [], lojaSelecionada: '', lojaSelecionadaStoreId: '' };
            window.lojasStatus = document.getElementById('status');
            window.lojasGrid = document.getElementById('stores');
            window.posVendaLojasGrid = document.getElementById('post');
            window.token = 'tenant-a-session-a';
            window.obterAuthHeaders = () => ({ Authorization: token });
            window.TODAS_LOJAS_VALUE = '*';
            window.todasAsLojasSelecionadas = () => state.lojaSelecionada === '*';
            window.lojasMercadoLivreConectadas = () => state.lojas.filter(s => s.mercadolivre_conectado);
            window.renderizarLojas = () => {
                lojasGrid.textContent = state.lojas.length ? state.lojas.map(s => s.store_id).join(',') : 'Nenhuma loja';
                posVendaLojasGrid.textContent = lojasGrid.textContent;
            };
            window.atualizarCabecalhoPosVenda = window.atualizarCabecalhoMediacao = window.iniciarAutomacaoPerguntas = () => {};
            window.questionLoads = 0;
            window.carregarPerguntas = async () => { questionLoads++; };
            window.carregarContadoresNotificacoes = () => {};
            window.JKPerguntasLoading = { limpar() {}, verificarSessao() {} };
            window.calls = [];
            window.queue = [];
            window.defaultResponse = { status: 503, body: { detail: { code: 'stores_snapshot_initializing', message: 'Preparando lojas' } } };
            window.fetch = async (_url, options) => {
                calls.push({ at: Date.now(), signal: options.signal });
                const result = queue.shift() || defaultResponse;
                if (result.hold) return new Promise(resolve => { window.release = () => resolve(makeResponse(result)); });
                if (result.hang) return new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('timeout', 'AbortError')), { once: true }));
                return makeResponse(result);
            };
            window.makeResponse = result => ({ ok: !result.status || result.status < 400, status: result.status || 200,
                headers: new Headers(result.headers || {}), json: async () => result.body });
            window.snapshot = (status = 'ready', lojas = [{ nome: 'Loja A', store_id: 'a', seller_id: '101', site_id: 'MLB', mercadolivre_conectado: true }], generation = '1') => ({
                body: { success: true, lojas, snapshot: { status, generation, published_at: '2026-09-09T12:00:00Z' } }
            });
        });
        await page.addScriptTag({ path: path.join(__dirname, '..', 'static/perguntas_pos_venda/lojas-snapshot.js') });
        const start = () => page.evaluate(() => { void JKPerguntasStores.carregar(); });
        const advance = ms => page.clock.runFor(ms);
        const value = expression => page.evaluate(expression);

        await start();
        assert.strictEqual(await page.locator('#stores').textContent(), '', 'initializing must not become an empty configuration');
        await advance(1999);
        assert.strictEqual(await value(() => calls.length), 1);
        await advance(1);
        assert.strictEqual(await value(() => calls.length), 2);
        await advance(4000);
        assert.strictEqual(await value(() => calls.length), 3);
        await advance(24000);
        assert.deepStrictEqual(await value(() => calls.map(c => c.at - calls[0].at)), [0, 2000, 6000, 14000, 22000]);
        assert.strictEqual(await page.locator('[data-retry-stores]').count(), 1, '30-second deadline leaves manual retry');
        await value(() => queue.push(snapshot()));
        await page.locator('[data-retry-stores]').click();
        assert.strictEqual(await page.locator('#stores').textContent(), 'a');
        assert.strictEqual(await value(() => questionLoads), 1);

        // Same-session temporary failures preserve cards and exact selected store.
        await value(() => { state.lojaSelecionada = 'Loja A'; state.lojaSelecionadaStoreId = 'a'; });
        await start();
        assert.strictEqual(await page.locator('#stores').textContent(), 'a');
        assert.strictEqual(await value(() => state.lojaSelecionadaStoreId), 'a');
        await value(() => { queue.push(snapshot('updating'), snapshot('updating'), snapshot()); });
        await advance(2000); await advance(4000); await advance(8000);
        assert.strictEqual(await value(() => questionLoads), 1, 'retrying the same snapshot does not reload questions');
        assert.strictEqual(await value(() => state.lojaSelecionadaStoreId), 'a');

        // A replaced request within the same session cannot overwrite a newer result.
        await value(() => queue.push({ ...snapshot('ready', [], 'old'), hold: true }));
        await start();
        await value(() => queue.push(snapshot('ready', undefined, 'new')));
        await start();
        await value(() => release());
        assert.strictEqual(await page.locator('#stores').textContent(), 'a');

        // A late response from a previous tenant is ignored even when transport ignores abort.
        await value(() => queue.push({ ...snapshot(), hold: true }));
        await start();
        await value(() => { token = 'tenant-b-session-b'; dispatchEvent(new Event('storage')); release(); });
        assert.strictEqual(await page.locator('#stores').textContent(), '');
        assert.strictEqual(await value(() => state.lojas.length), 0);
        assert.strictEqual(await value(() => calls.at(-1).signal.aborted), true);

        // Updating-empty never claims there are no stores; only a ready-empty snapshot can.
        await value(() => queue.push(snapshot('updating', [], '2'), snapshot('ready', [], '2')));
        await start();
        assert.strictEqual(await page.locator('#stores').textContent(), '');
        assert.match(await page.locator('#status').textContent(), /última configuração/);
        await advance(2000);
        assert.strictEqual(await page.locator('#stores').textContent(), 'Nenhuma loja');

        // Logout cancels pending retry and clears both module grids.
        await start();
        const beforeLogout = await value(() => calls.length);
        await value(() => dispatchEvent(new Event('jk:logout')));
        await advance(30000);
        assert.strictEqual(await value(() => calls.length), beforeLogout);
        assert.strictEqual(await page.locator('#stores').textContent(), '');
        assert.strictEqual(await page.locator('#post').textContent(), '');

        // Hung requests get aborted and cannot exceed the total deadline.
        await value(() => { defaultResponse = { hang: true }; calls.length = 0; });
        await start(); await advance(30000);
        assert.strictEqual(await page.locator('[data-retry-stores]').count(), 1);
        assert.strictEqual(await value(() => calls.every(c => c.signal.aborted)), true);

        // Invalid payloads do not replace an existing snapshot with an empty list.
        await value(() => { queue.push(snapshot()); defaultResponse = { status: 503, body: {} }; });
        await start();
        await value(() => queue.push({ body: { success: true, lojas: [] } }));
        await start();
        assert.strictEqual(await page.locator('#stores').textContent(), 'a');
        await value(() => queue.push({ status: 403, body: { detail: 'Acesso revogado' } }));
        await start();
        assert.strictEqual(await page.locator('#stores').textContent(), '');
        const forbiddenCalls = await value(() => calls.length);
        await advance(30000);
        assert.strictEqual(await value(() => calls.length), forbiddenCalls, '403 is terminal');
        assert.deepStrictEqual(errors, []);
        console.log('Perguntas stores snapshot browser: OK (retry, deadline, stale responses, session isolation, updating and ready-empty)');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
