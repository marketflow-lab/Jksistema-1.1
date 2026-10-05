'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { EventEmitter } = require('events');
const runnerModule = require('../electron_app/main/modules/pesquisa-mercado-runner.js');
const root = path.resolve(__dirname, '..');
const clone = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));
const category = { key: 'MLB-FIXTURE', nome: 'Ferramentas', url: 'https://lista.mercadolivre.com.br/ferramentas' };
const source = fs.readFileSync(path.join(root, 'electron_app/main/modules/pesquisa-mercado-browser.js'), 'utf8');

function functionSource(relative, name) {
    const text = fs.readFileSync(path.join(root, relative), 'utf8');
    const start = text.indexOf(`function ${name}(`);
    assert.ok(start >= 0, `guard ${name} ausente`);
    return text.slice(start, text.indexOf('\n}', start) + 2);
}

function harness(saved = [], options = {}) {
    const events = [];
    const requests = [];
    const windows = [];
    const jobs = new Map(saved.map(job => [job.id, clone(job)]));
    const session = { partition: 'fixture-ml-session' };
    const pool = { active: true, workers: new Map([['w1', { id: 'existing-favoritos-window' }]]) };
    let serial = saved.length;
    let releaseRestore = null;
    class FakeWebContents extends EventEmitter {
        constructor() { super(); this.url = ''; this.stops = 0; this.scripts = []; }
        setUserAgent(value) { this.userAgent = value; }
        setWindowOpenHandler(handler) { this.windowOpenHandler = handler; }
        getURL() { return this.url; }
        isLoadingMainFrame() { return !!(options.loading && options.loading(this.url)); }
        isDestroyed() { return false; }
        stop() { this.stops += 1; events.push({ type: 'stop' }); }
        executeJavaScript(code, gesture) {
            this.scripts.push({ code, gesture });
            if (code === 'fixture:categories') {
                const fixture = options.categoryPages && options.categoryPages[this.url];
                return Promise.resolve(typeof fixture === 'function' ? fixture() : fixture || {
                    categories: this.url.endsWith('/categorias') ? [category] : [],
                    anuncios: this.url.endsWith('/categorias') ? [] : [{ id: 'MLB1000000000' }]
                }).then(page => ({ url: this.url, ...page }));
            }
            return Promise.resolve({ title: 'fixture result' });
        }
        send(channel, payload) { events.push({ type: 'send', channel, payload: clone(payload) }); }
    }
    class FakeBrowserWindow extends EventEmitter {
        constructor(options) {
            super(); this.options = options; this.webContents = new FakeWebContents();
            this.visible = !!options.show; this.destroyed = false; this.loads = [];
            windows.push(this); events.push({ type: 'create' });
        }
        setMenuBarVisibility(value) { this.menuVisible = value; }
        isDestroyed() { return this.destroyed; }
        hide() { this.visible = false; events.push({ type: 'hide' }); }
        show() { this.visible = true; events.push({ type: 'show' }); }
        focus() { this.focused = true; }
        destroy() { this.destroyed = true; events.push({ type: 'destroy' }); this.emit('closed'); }
        loadURL(url) {
            this.webContents.url = options.redirects && options.redirects[url] || url; this.loads.push(url); events.push({ type: 'load', url });
            return this.blockLoads ? new Promise(resolve => { this.releaseLoad = resolve; }) : Promise.resolve();
        }
    }
    class DormantRunner extends runnerModule.PesquisaMercadoRunner {
        async run() {
            try {
                await this.browser.prepare();
                if (!this.controller.signal.aborted) await new Promise(resolve => this.controller.signal.addEventListener('abort', resolve, { once: true }));
            } catch (_error) {
                this.job.status = this.stopStatus || 'failed';
                try { await this.save(); } catch (_saveError) {}
            } finally { await this.browser.finish(this.stopStatus || this.job.status); }
        }
    }
    function serve(options, payload) {
        const owner = options.headers.Authorization === 'Bearer fixture-owner-one' ? 'one'
            : options.headers.Authorization === 'Bearer fixture-owner-two' ? 'two' : null;
        requests.push({ method: options.method, path: options.path, owner, body: payload ? JSON.parse(payload) : null });
        if (!owner) return { status: 401, data: { detail: 'Sessão inválida.' } };
        if (options.path === '/api/pesquisa-mercado/jobs/latest') {
            return { status: 200, data: { job: clone([...jobs.values()].filter(job => job.owner === owner).at(-1) || null) } };
        }
        if (options.method === 'POST' && options.path === '/api/pesquisa-mercado/jobs') {
            const existing = [...jobs.values()].find(job => job.owner === owner && ['running', 'paused', 'waiting_login'].includes(job.status));
            if (existing) return { status: 200, data: { job: clone(existing) } };
            const job = { id: `fixture-job-${++serial}`, owner, status: 'running', revision: 1,
                counters: { categories: 0, pages: 0, sellers: 0, ads: 0, pending: 0 }, checkpoint: {} };
            const input = JSON.parse(payload || '{}');
            if (input.category) job.checkpoint = { scope: { category: input.category }, categories: [input.category], categoryIndex: 0, discoveryInitialized: true };
            jobs.set(job.id, job);
            return { status: 200, data: { job: clone(job) } };
        }
        const match = options.path.match(/^\/api\/pesquisa-mercado\/jobs\/([^/]+)(\/checkpoint)?$/);
        const job = match && jobs.get(decodeURIComponent(match[1]));
        if (!job || job.owner !== owner) return { status: 404, data: { detail: 'Pesquisa não encontrada.' } };
        if (options.method === 'PATCH' && match[2]) {
            Object.assign(job, clone(JSON.parse(payload)), { revision: job.revision + 1 });
            events.push({ type: 'persist', status: job.status, owner });
        }
        return { status: 200, data: { job: clone(job) } };
    }
    const http = {
        request(options, callback) {
            const request = new EventEmitter();
            request.end = payload => setImmediate(() => {
                const result = serve(options, payload);
                const response = new EventEmitter();
                response.statusCode = result.status;
                response.setEncoding = () => {};
                callback(response);
                response.emit('data', JSON.stringify(result.data));
                response.emit('end');
            });
            request.destroy = error => request.emit('error', error);
            return request;
        }
    };
    const mainWindow = { isDestroyed: () => false, webContents: new FakeWebContents() };
    const sandbox = {
        path, __dirname: root, http, Buffer, JK_LOCAL_BACKEND_PORT: 8001, BrowserWindow: FakeBrowserWindow,
        ML_BROWSER_USER_AGENT: 'fixture-user-agent', mainWindow,
        getMlSession: () => session,
        restaurarSessaoAvantProAntesDeAbrirNavegador: async () => {
            events.push({ type: 'restore' });
            if (options.blockRestore) await new Promise(resolve => { releaseRestore = resolve; });
        },
        ensureChromeExtensionsForMlSession: async () => { events.push({ type: 'extensions' }); },
        registerEmbeddedMlBrowserDownloadGuard: contents => { contents.downloadGuard = true; },
        normalizeTargetUrl: value => String(value),
        isMercadoLivreHost: host => /(^|\.)mercadolivre\.com\.br$/i.test(host),
        logElectronLifecycle: () => {},
        favoritosWorkersPoolState: pool, favoritosWorkerBrowsers: pool.workers,
        stopFavoritosWorkersPool: () => { throw new Error('A pesquisa não deve tocar no pool Favoritos.'); },
        getFavoritosWorkerBrowser: () => { throw new Error('A pesquisa não deve compartilhar janela Favoritos.'); },
        require: filename => {
            if (filename.endsWith('pesquisa-mercado-runner.js')) return { ...runnerModule, PesquisaMercadoRunner: options.realRunner ? runnerModule.PesquisaMercadoRunner : DormantRunner };
            if (filename.endsWith('page-scripts.js')) return { discoveryScript: () => 'fixture:categories' };
            if (filename.endsWith('shared-collector.js')) return {};
            throw new Error(`Dependência inesperada: ${path.basename(filename)}`);
        },
        URL, Promise, AbortController, setTimeout, clearTimeout
    };
    vm.createContext(sandbox);
    vm.runInContext(functionSource('electron_app/main/modules/ipc.js', 'assertTrustedFavoritosIpcSender'), sandbox);
    vm.runInContext(functionSource('electron_app/main/modules/favoritos-worker-browser.js', 'isAllowedFavoritosWorkerUrl'), sandbox);
    vm.runInContext(`${source}\nglobalThis.testAPI = {
        command: pesquisaMercadoCommand, browser: pesquisaMercadoBrowserAdapter,
        coordinator: getPesquisaMercadoCoordinator, quit: persistPesquisaMercadoBeforeQuit,
        peek: () => ({ token: pesquisaMercadoAuthToken, quitting: pesquisaMercadoQuitting, window: pesquisaMercadoWindow }),
        seedChoices: (token, items) => { pesquisaMercadoCategoriesToken = token; rememberPesquisaMercadoCategories(items); }
    };`, sandbox, { filename: 'pesquisa-mercado-browser.js' });
    const trusted = { sender: mainWindow.webContents };
    if (!options.withoutChoices) sandbox.testAPI.seedChoices('fixture-owner-one', [category]);
    sandbox.testAPI.coordinator().wait = async () => {};
    const command = (action, owner = 'one', jobId, extra = {}) => sandbox.testAPI.command(trusted, action, {
        authToken: owner === 'one' ? 'fixture-owner-one' : 'fixture-owner-two', ...(jobId ? { jobId } : {}),
        ...(action === 'start' ? { category } : {}), ...extra
    });
    return { api: sandbox.testAPI, sandbox, command, events, requests, windows, jobs, pool, session,
        releaseRestore: () => { if (releaseRestore) releaseRestore(); } };
}

async function testBrowserIsolationAndNavigation() {
    const h = harness();
    const adapter = h.api.browser();
    await adapter.prepare();
    const win = h.windows[0];
    assert.strictEqual(h.windows.length, 1);
    assert.strictEqual(win.options.show, false, 'a coleta nasce oculta');
    assert.strictEqual(win.options.webPreferences.backgroundThrottling, false, 'a coleta continua fora da aba ativa');
    assert.strictEqual(win.options.webPreferences.session, h.session, 'restaura a sessão ML/Avant compartilhada');
    assert.strictEqual(win.options.webPreferences.nodeIntegration, false);
    assert.strictEqual(win.options.webPreferences.contextIsolation, true);
    assert.strictEqual(win.menuVisible, false);
    assert.strictEqual(win.webContents.downloadGuard, true);
    assert.deepStrictEqual(clone(win.webContents.windowOpenHandler()), { action: 'deny' });
    assert.deepStrictEqual(h.events.slice(0, 3).map(event => event.type), ['restore', 'extensions', 'create']);
    assert.strictEqual(h.pool.active, true);
    assert.strictEqual(h.pool.workers.get('w1').id, 'existing-favoritos-window');
    await assert.rejects(adapter.navigate('https://evil.example/product'), /fora do Mercado Livre/);
    await assert.rejects(adapter.navigate('https://login.avantprocloud.com.br/'), /fora do Mercado Livre/);
    assert.strictEqual(win.loads.length, 0, 'URLs de partida só podem ser ML');
    await adapter.navigate('https://produto.mercadolivre.com.br/MLB-1234567890-item');
    for (const eventName of ['will-navigate', 'will-redirect']) {
        for (const [url, allowed] of [
            ['https://www.mercadolivre.com.br/login', true],
            ['https://login.avantprocloud.com.br/login', true],
            ['https://evil.example/', false],
            ['https://avantprocloud.com.br.evil.example/', false],
            ['http://www.mercadolivre.com.br/', false]
        ]) {
            let prevented = false;
            win.webContents.emit(eventName, { preventDefault: () => { prevented = true; } }, url);
            assert.strictEqual(prevented, !allowed, `${eventName} deve validar o destino`);
        }
    }
    const result = await adapter.executeJavaScript('document.title');
    assert.strictEqual(result.title, 'fixture result');
    win.webContents.url = 'https://evil.example/';
    await assert.rejects(adapter.executeJavaScript('document.title'), /nao permitida/);
    assert.strictEqual(win.webContents.scripts.length, 1);
    win.blockLoads = true;
    const controller = new AbortController();
    const loading = adapter.navigate('https://lista.mercadolivre.com.br/ferramentas', controller.signal);
    controller.abort();
    await assert.rejects(loading, error => error.name === 'AbortError');
    assert.strictEqual(win.webContents.stops, 1, 'abortar cancela carregamento da janela exclusiva');
    win.releaseLoad();
    adapter.stop();
    assert.strictEqual(win.webContents.stops, 2);
    win.show();
    await adapter.finish('waiting_login');
    assert.strictEqual(win.visible, true, 'pedido de login mantém a janela que o usuário abriu');
    await adapter.finish('paused');
    assert.strictEqual(win.visible, false);
    await h.api.quit();
}

async function testOwnershipAndQueue() {
    const h = harness();
    assert.throws(() => h.api.command({ sender: { isDestroyed: () => false, getURL: () => 'https://evil.example/' } }, 'start', {
        authToken: 'fixture-owner-one'
    }), /Origem IPC nao autorizada/);
    assert.strictEqual(h.requests.length, 0, 'sender não confiável é rejeitado antes do backend');
    const response = await h.command('start');
    await tick();
    const jobId = response.job.id;
    const runner = h.api.coordinator();
    const before = h.requests.length;
    await assert.rejects(h.command('pause', 'one', 'different-job'), /Outra pesquisa/);
    assert.strictEqual(h.requests.length, before, 'job solicitado não troca contexto da pesquisa ativa');
    const results = await Promise.allSettled([h.command('status', 'one'), h.command('cancel', 'two', jobId)]);
    assert.strictEqual(results[0].status, 'fulfilled');
    assert.strictEqual(results[1].status, 'rejected', 'token de outro usuário não pode cancelar a pesquisa ativa');
    assert.strictEqual(runner.job.id, jobId);
    assert.strictEqual(runner.job.status, 'running');
    assert.strictEqual(h.api.peek().token, 'fixture-owner-one', 'rejeição de ownership não substitui o token ativo');
    const paused = await h.command('pause', 'one', jobId);
    assert.strictEqual(paused.job.status, 'paused', 'fila continua após comando rejeitado');
    assert.strictEqual(h.jobs.get(jobId).status, 'paused');
    const resumed = await h.command('resume', 'one', jobId);
    assert.strictEqual(resumed.job.id, jobId);
    assert.strictEqual(resumed.job.status, 'running');
    await h.api.quit();
    const persistedAt = h.events.findLastIndex(event => event.type === 'persist' && event.status === 'paused');
    const destroyedAt = h.events.findIndex(event => event.type === 'destroy');
    assert.ok(persistedAt >= 0 && destroyedAt > persistedAt, 'encerramento salva pausa antes de destruir janela');
    assert.strictEqual(h.jobs.get(jobId).status, 'paused');
    assert.strictEqual(h.api.peek().token, '', 'encerramento remove token da memória');
    assert.strictEqual(h.api.peek().quitting, true);
    await assert.rejects(h.command('resume', 'one', jobId), /encerrando/);
}

async function testOrphanAndShowCursor() {
    const checkpoint = { currentCategory: { nome: 'Ferramentas', url: 'https://lista.mercadolivre.com.br/ferramentas' },
        page: { url: 'https://lista.mercadolivre.com.br/ferramentas_Desde_49', index: 1, anuncios: [
            { url: 'https://produto.mercadolivre.com.br/MLB-1000000000-primeiro' },
            { url: 'https://produto.mercadolivre.com.br/MLB-2000000000-cursor' }
        ] } };
    const h = harness([{ id: 'saved-job', owner: 'one', status: 'running', revision: 3,
        counters: { pages: 6, ads: 30 }, checkpoint }]);
    const status = await h.command('status');
    assert.strictEqual(status.job.status, 'paused', 'uma execução órfã é persistida como pausada');
    assert.strictEqual(h.jobs.get('saved-job').counters.ads, 30);
    assert.deepStrictEqual(h.jobs.get('saved-job').checkpoint, checkpoint, 'recuperação preserva o cursor');
    assert.strictEqual(h.windows.length, 0, 'consultar estado não retoma nem cria navegador');
    const shown = await h.command('show-browser', 'one', 'saved-job');
    const win = h.windows[0];
    assert.strictEqual(shown.job.id, 'saved-job');
    assert.strictEqual(h.api.coordinator().task, null, 'mostrar navegador não inicia varredura');
    assert.strictEqual(win.loads[0], checkpoint.page.anuncios[1].url, 'navegador recupera anúncio no cursor salvo');
    assert.strictEqual(win.visible, true);
    assert.strictEqual(win.focused, true);
    const beforeClose = h.requests.length;
    let prevented = false;
    win.emit('close', { preventDefault: () => { prevented = true; } });
    assert.strictEqual(prevented, true);
    assert.strictEqual(win.destroyed, false);
    assert.strictEqual(win.visible, false, 'fechar janela auxiliar apenas a oculta');
    assert.strictEqual(h.requests.length, beforeClose, 'fechar navegador não cancela a pesquisa');
    await h.api.quit();
    assert.strictEqual(win.destroyed, true);
}

async function testQuitDuringPreparation() {
    const h = harness([], { blockRestore: true });
    const started = await h.command('start');
    const task = h.api.coordinator().task;
    assert.strictEqual(h.windows.length, 0, 'preparação ainda está aguardando restauração da sessão');
    const quitting = h.api.quit();
    await tick();
    await tick();
    h.releaseRestore();
    await quitting;
    await task;
    assert.strictEqual(h.jobs.get(started.job.id).status, 'paused');
    assert.strictEqual(h.api.peek().token, '');
    assert.ok(h.windows.every(window => window.destroyed), 'preparação tardia não pode deixar janela viva depois do encerramento');
}

async function testSwitchingPausedJobsShowsOwnedCursor() {
    const first = 'https://produto.mercadolivre.com.br/MLB-1000000000-primeiro';
    const second = 'https://produto.mercadolivre.com.br/MLB-2000000000-segundo';
    const h = harness([
        { id: 'paused-one', owner: 'one', status: 'paused', revision: 1, counters: {}, checkpoint: { page: { url: first, anuncios: [] } } },
        { id: 'paused-two', owner: 'two', status: 'paused', revision: 1, counters: {}, checkpoint: { page: { url: second, anuncios: [] } } },
        { id: 'paused-three', owner: 'two', status: 'paused', revision: 1, counters: {}, checkpoint: { page: { url: 'https://evil.example/untrusted-cursor', anuncios: [] } } }
    ]);
    await h.command('show-browser', 'one', 'paused-one');
    assert.strictEqual(h.windows[0].webContents.getURL(), first);
    await h.command('show-browser', 'two', 'paused-two');
    assert.strictEqual(h.windows.length, 1, 'uma execução pausada reutiliza a janela exclusiva');
    assert.strictEqual(h.api.coordinator().job.id, 'paused-two');
    assert.strictEqual(h.windows[0].webContents.getURL(), second, 'trocar pesquisa pausada precisa carregar o cursor do proprietário autorizado');
    await h.command('show-browser', 'two', 'paused-three');
    assert.strictEqual(h.windows[0].webContents.getURL(), 'https://www.mercadolivre.com.br/', 'cursor persistido externo usa a página inicial permitida');
    await h.api.quit();
}

async function testCategorySelectionAndIsolation() {
    const hub = { key: 'hub', nome: 'Acessórios', url: 'https://www.mercadolivre.com.br/c/acessorios-para-veiculos' };
    const child = { key: 'child', nome: 'Peças', url: 'https://lista.mercadolivre.com.br/acessorios-veiculos/pecas' };
    const h = harness([], { withoutChoices: true, categoryPages: {
        'https://www.mercadolivre.com.br/categorias': { categories: [hub], categoryTree: [{ ...hub, children: [child] }] },
        [child.url]: { categories: [], anuncios: [{ id: 'MLB1000000001' }] }
    } });
    await assert.rejects(h.command('start'), /Escolha uma categoria/);
    assert.strictEqual(h.jobs.size, 0, 'não iniciar uma varredura ampla sem escolha confirmada');
    const roots = await h.command('categories');
    assert.deepStrictEqual(clone(roots.items), [hub]);
    assert.strictEqual(h.jobs.size, 0, 'abrir o seletor não cria execução');
    const children = await h.command('categories', 'one', null, { parentCategory: hub });
    assert.deepStrictEqual(clone(children.items), [child]);
    assert.strictEqual(h.windows[0].loads.length, 1, 'filhos canônicos do índice dispensam o hub promocional');
    const leaf = await h.command('categories', 'one', null, { parentCategory: child });
    assert.strictEqual(leaf.items.length, 0, 'categoria final é pesquisável mesmo sem filhos');
    await assert.rejects(h.command('start', 'one', null, { category: { ...child, nome: 'Nome alterado' } }), /Escolha uma categoria/);
    const started = await h.command('start', 'one', null, { category: child });
    assert.deepStrictEqual(clone(started.job.scopeCategory), child);
    assert.deepStrictEqual(h.jobs.get(started.job.id).checkpoint.scope.category, child);
    await assert.rejects(h.command('categories'), /Pause ou encerre/);
    await assert.rejects(h.command('categories', 'two'), /n[aã]o encontrada/);
    await h.command('pause', 'one', started.job.id);
    await h.api.coordinator().task;
    await assert.rejects(h.command('categories', 'one', null, { parentCategory: { ...child, url: 'https://evil.example/' } }), /Escolha uma categoria/);
    await assert.rejects(h.command('start', 'two', null, { category: child }), /Escolha uma categoria/, 'lista de uma sessão não autoriza outra');
    await h.api.quit();
}

async function testCategoryLoginBeforeAnyJob() {
    const h = harness([], { withoutChoices: true, categoryPages: {
        'https://www.mercadolivre.com.br/categorias': { authRequired: true }
    } });
    const result = await h.command('categories');
    assert.strictEqual(result.authRequired, true);
    assert.strictEqual(h.jobs.size, 0);
    await h.command('show-browser', 'one', null, { purpose: 'categories' });
    assert.strictEqual(h.windows[0].visible, true, 'login de categorias permite mostrar navegador antes de iniciar pesquisa');
    await h.api.quit();
}

async function testCategoryTransientRetries() {
    let attempts = 0;
    const h = harness([], { withoutChoices: true, categoryPages: {
        'https://www.mercadolivre.com.br/categorias': () => {
            if (++attempts < 3) throw new Error('Temporary page failure');
            return { categories: [category] };
        }
    } });
    const result = await h.command('categories');
    assert.deepStrictEqual(clone(result.items), [category]);
    assert.strictEqual(h.windows[0].loads.length, 3, 'two additional retries after transient failures');
    assert.strictEqual(h.jobs.size, 0);
    await h.api.quit();
}

async function testCategoryRedirectAndQuit() {
    const parent = { key: 'motor', nome: 'Motor', url: 'https://lista.mercadolivre.com.br/pecas/motor' };
    const sibling = { key: 'freios', nome: 'Freios', url: 'https://lista.mercadolivre.com.br/pecas/freios' };
    const h = harness([], { categoryPages: {
        'https://www.mercadolivre.com.br/categorias': { categories: [parent] },
        [parent.url]: { url: 'https://lista.mercadolivre.com.br/pecas', categories: [sibling] }
    } });
    await h.command('categories');
    await assert.rejects(h.command('categories', 'one', null, { parentCategory: parent }), /outra categoria/);
    await assert.rejects(h.command('start', 'one', null, { category: sibling }), /Escolha uma categoria/);
    await h.api.quit();

    let release;
    const closing = harness([], { withoutChoices: true, categoryPages: {
        'https://www.mercadolivre.com.br/categorias': () => new Promise(resolve => { release = resolve; })
    } });
    const load = closing.command('categories');
    while (!release) await new Promise(resolve => setImmediate(resolve));
    const quit = closing.api.quit();
    release({ categories: [] });
    await assert.rejects(load, /encerrando/);
    await quit;
    assert.strictEqual(closing.windows[0].destroyed, true, 'category query cannot hold the browser after quit');
}

async function testCategoryNavigationSettles() {
    const parent = { key: 'cars', nome: 'Carros e Caminhonetes', url: 'https://lista.mercadolivre.com.br/veiculos/carros-caminhonetes' };
    const child = { key: 'native', nome: 'Casas', url: 'https://imoveis.mercadolivre.com.br/casas' };
    const old = { key: 'old', nome: 'Categoria anterior', url: 'https://lista.mercadolivre.com.br/anterior' };
    let reads = 0;
    const h = harness([], { withoutChoices: true, categoryPages: {
        'https://www.mercadolivre.com.br/categorias': { categories: [parent, child] },
        [parent.url]: () => {
            reads += 1;
            if (reads <= 2) return { url: reads === 1 ? '' : 'https://www.mercadolivre.com.br/categorias', readyState: 'complete', avantAuthRequired: true };
            if (reads === 3) return { url: 'https://www.mercadolivre.com.br/categorias', readyState: 'complete', categories: [old] };
            if (reads <= 5) return { url: parent.url + '/', readyState: 'loading', anuncios: [{ id: 'temporary' }] };
            return { url: parent.url + '/', readyState: 'complete', empty: true };
        },
        [child.url]: { url: child.url + '/', readyState: 'complete', empty: true }
    } });
    await h.command('categories');
    const result = await h.command('categories', 'one', null, { parentCategory: parent });
    assert.deepStrictEqual(clone(result), { items: [], parentCategory: parent });
    assert.strictEqual(reads, 7, 'previous document and loading shell wait for two settled readings');
    assert.strictEqual(h.windows[0].loads.filter(url => url === parent.url).length, 1, 'transient old URL cannot restart navigation');
    await assert.rejects(h.command('start', 'one', null, { category: old }), /Escolha uma categoria/, 'old tree never enters choices');
    const native = await h.command('categories', 'one', null, { parentCategory: child });
    assert.deepStrictEqual(clone(native.parentCategory), child);
    assert.deepStrictEqual(clone(native.items), []);
    await h.api.quit();

    let loadingReads = 0;
    const loading = harness([], { withoutChoices: true, loading: url => url === parent.url && ++loadingReads <= 2,
        categoryPages: {
            'https://www.mercadolivre.com.br/categorias': { categories: [parent] },
            [parent.url]: { url: parent.url, readyState: 'complete', empty: true }
        } });
    await loading.command('categories');
    await loading.command('categories', 'one', null, { parentCategory: parent });
    assert.strictEqual(loadingReads, 4, 'Electron loading state must also settle');
    await loading.api.quit();
}

async function testPublicVehicleUrlsPreserveTrailingSlash() {
    const hub = { key: 'vehicles', nome: 'Carros, Motos e Outros', url: 'https://www.mercadolivre.com.br/c/carros-motos-e-outros' };
    const slugs = ['caminhoes', 'carros-antigos', 'carros-caminhonetes', 'consorcios', 'motorhomes',
        'motos', 'nautica', 'outros', 'veiculos-pesados', 'onibus'];
    // Public links from the category index. The slash is part of the requested
    // resource: the observed slashless Caminhoes request became a generic search.
    const children = slugs.map(slug => ({ key: 'https://lista.mercadolivre.com.br/veiculos/' + slug + '/',
        nome: slug, url: 'https://lista.mercadolivre.com.br/veiculos/' + slug + '/' }));
    const categoryPages = {
        'https://www.mercadolivre.com.br/categorias': { categories: [hub], categoryTree: [{ ...hub, children }] }
    };
    const redirects = {};
    for (const child of children) {
        const slashless = child.url.replace(/\/$/, '');
        const genericSearch = slashless.replace('/veiculos/', '/');
        redirects[slashless] = genericSearch;
        categoryPages[child.url] = { readyState: 'complete', empty: true };
        categoryPages[genericSearch] = { readyState: 'complete', empty: true };
    }
    const h = harness([], { withoutChoices: true, realRunner: true, categoryPages, redirects });
    try {
        await h.command('categories');
        const directory = await h.command('categories', 'one', null, { parentCategory: hub });
        for (const child of children) {
            const leaf = await h.command('categories', 'one', null, { parentCategory: child });
            assert.deepStrictEqual(clone(leaf), { items: [], parentCategory: child },
                'seletor mantém a URL pública com slash para ' + child.nome);
            assert.strictEqual(h.windows[0].loads.at(-1), child.url,
                'navegação não pode transformar categoria de veículos em busca genérica');
            assert.strictEqual(h.windows[0].webContents.getURL(), child.url);
        }
        assert.deepStrictEqual(clone(directory.items), children, 'os dez links públicos permanecem íntegros no cache do seletor');
        for (const child of children) {
            const before = h.windows[0].loads.length;
            const started = await h.command('start', 'one', null, { category: child });
            await h.api.coordinator().task;
            const post = h.requests.filter(request => request.method === 'POST').at(-1);
            assert.deepStrictEqual(post.body, { category: child }, 'POST deve preservar escopo e URL escolhidos');
            const saved = h.jobs.get(started.job.id);
            assert.strictEqual(saved.status, 'completed', 'categoria vazia continua válida e concluível');
            assert.deepStrictEqual(saved.checkpoint.scope.category, child, 'checkpoint mantém a URL depois do POST e da gravação incremental');
            assert.strictEqual(saved.checkpoint.categories[0].url, child.url);
            assert.deepStrictEqual(h.windows[0].loads.slice(before), [child.url],
                'executor real abre a mesma URL do seletor depois de iniciar');
            assert.strictEqual(saved.counters.pages, 1);
            assert.strictEqual(saved.counters.sellers, 0);
        }
        assert(!h.windows[0].loads.some(url => Object.hasOwn(redirects, url)),
            'nenhuma das dez categorias usa o endereço sem slash que redireciona para busca');
    } finally { await h.api.quit(); }

    const observed = children[0];
    const legacy = { ...observed, url: observed.url.replace(/\/$/, '') };
    const redirected = harness([], { withoutChoices: true, categoryPages: {
        'https://www.mercadolivre.com.br/categorias': { categories: [legacy] },
        'https://lista.mercadolivre.com.br/caminhoes': { readyState: 'complete', empty: true }
    }, redirects: { [legacy.url]: 'https://lista.mercadolivre.com.br/caminhoes' } });
    try {
        await redirected.command('categories');
        await assert.rejects(redirected.command('categories', 'one', null, { parentCategory: legacy }), /outra categoria/,
            'perda real de /veiculos continua sendo rejeitada; não aceitar alias para busca genérica');
        assert.strictEqual(redirected.windows[0].webContents.getURL(), 'https://lista.mercadolivre.com.br/caminhoes');
        assert.strictEqual(redirected.windows[0].loads.filter(url => url === legacy.url).length, 3);
        assert.strictEqual(redirected.jobs.size, 0, 'falha do seletor não pode iniciar uma execução');
    } finally { await redirected.api.quit(); }
}
async function main() {
    await testPublicVehicleUrlsPreserveTrailingSlash();
    await testBrowserIsolationAndNavigation();
    await testOwnershipAndQueue();
    await testOrphanAndShowCursor();
    await testQuitDuringPreparation();
    await testSwitchingPausedJobsShowsOwnedCursor();
    await testCategorySelectionAndIsolation();
    await testCategoryLoginBeforeAnyJob();
    await testCategoryTransientRetries();
    await testCategoryRedirectAndQuit();
    await testCategoryNavigationSettles();
    console.log('Pesquisa de Mercado main browser: janela exclusiva, navegação, ownership, recuperação e encerramento OK');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
