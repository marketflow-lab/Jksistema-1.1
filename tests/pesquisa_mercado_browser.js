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
    const fixtureNameSellers = clone(options.sellers || []);
    const requests = [];
    const windows = [];
    const jobs = new Map(saved.map(job => [job.id, clone(job)]));
    const avantChecks = [];
    const session = { partition: 'fixture-ml-session' };
    const pool = { active: true, workers: new Map([['w1', { id: 'existing-favoritos-window' }]]) };
    let serial = saved.length;
    let releaseRestore = null;
    let releaseTaxonomy = null;
    const taxonomyCalls = [];
    const taxonomyConstructors = [];
    function taxonomySnapshot() {
        const dump = options.taxonomyDump;
        const roots = dump ? Object.values(dump).filter(node => node.path_from_root.length === 1).map(node => node.id)
            : options.taxonomyRoots || [category];
        const children = dump ? Object.fromEntries(Object.values(dump).map(node => [node.id, node.children_categories.map(child => child.id)]))
            : options.taxonomyChildren || {};
        const nodes = new Map();
        if (dump) for (const node of Object.values(dump)) nodes.set(node.id, { key: node.id, nome: node.name,
            url: node.permalink || `https://lista.mercadolivre.com.br/_CatId_${node.id}` });
        else {
            for (const node of roots) nodes.set(node.key, clone(node));
            for (const list of Object.values(children)) for (const node of list) nodes.set(node.key, clone(node));
        }
        const rootIds = roots.map(node => typeof node === 'string' ? node : node.key);
        const childIds = Object.fromEntries(Object.entries(children).map(([key, list]) => [key,
            list.map(node => typeof node === 'string' ? node : node.key)]));
        const pending = rootIds.map(key => [key, 1]);
        let maxDepth = 0;
        for (let index = 0; index < pending.length; index += 1) {
            const [key, depth] = pending[index];
            maxDepth = Math.max(maxDepth, depth);
            pending.push(...(childIds[key] || []).map(child => [child, depth + 1]));
        }
        return { complete: true, updatedAt: '2026-10-06T10:07:07.000Z',
            summary: { total: nodes.size, rootCount: rootIds.length, subcategoryCount: nodes.size - rootIds.length, maxDepth },
            get: key => nodes.get(key) || null,
            children: key => (key == null ? rootIds : childIds[key] || []).map(child => nodes.get(child)),
            childCounts: keys => Object.fromEntries(keys.map(key => [key, (childIds[key] || []).length])) };
    }
    class FakeTaxonomyService {
        constructor(config) { taxonomyConstructors.push(clone(config)); }
        async load(input = {}) {
            taxonomyCalls.push(clone(input));
            events.push({ type: 'taxonomy-load', owner: requests.at(-1) && requests.at(-1).owner, forceRefresh: !!input.forceRefresh });
            if (options.blockTaxonomy) await new Promise(resolve => { releaseTaxonomy = resolve; });
            if (options.taxonomyFailure || input.forceRefresh && options.failRefresh) throw new Error('Fixture: árvore oficial temporariamente indisponível.');
            return taxonomySnapshot();
        }
    }
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
        const match = options.path.match(/^\/api\/pesquisa-mercado\/jobs\/([^/]+)(\/(?:checkpoint|vendedores|nomes))?$/);
        const job = match && jobs.get(decodeURIComponent(match[1]));
        if (!job || job.owner !== owner) return { status: 404, data: { detail: 'Pesquisa não encontrada.' } };
        if (options.method === 'GET' && match[2] === '/vendedores') return { status: 200, data: { items: clone(fixtureNameSellers) } };
        if (options.method === 'PUT' && match[2] === '/nomes') {
            const body = JSON.parse(payload);
            for (const update of body.updates) {
                const seller = fixtureNameSellers.find(item => item.seller_key === update.seller_key);
                if (seller) seller.nome = update.nome;
            }
            job.revision += 1;
            return { status: 200, data: { updated: body.updates.length,
                pending: fixtureNameSellers.filter(item => !runnerModule.cleanSellerName(item.nome, item.id)).length, job: clone(job) } };
        }
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
        app: { getPath: name => { assert.strictEqual(name, 'userData'); return path.join(root, 'fixture-public-cache-only'); } },
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
            if (filename.endsWith('pesquisa-mercado-avant-auth.js')) return {
                normalizeEmail: require('../electron_app/main/modules/pesquisa-mercado-avant-auth.js').normalizeEmail,
                AvantSession: class {
                    constructor(config) { this.config = config; }
                    async ensure(input) {
                        avantChecks.push({ email: this.config.getEmail(), context: this.config.getContext(), contents: this.config.getContents(), input });
                        return { authenticated: true, attempted: false };
                    }
                }
            };
            if (filename.endsWith('pesquisa-mercado-taxonomy.js')) return { TaxonomyService: FakeTaxonomyService };
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
    return { avantChecks, api: sandbox.testAPI, sandbox, trusted, command, events, requests, windows, jobs, pool, session, options, taxonomyCalls, taxonomyConstructors,
        releaseRestore: () => { if (releaseRestore) releaseRestore(); },
        releaseTaxonomy: () => { if (releaseTaxonomy) releaseTaxonomy(); }, taxonomyWaiting: () => !!releaseTaxonomy };
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
    const { progress, ...restoredCursor } = h.jobs.get('saved-job').checkpoint;
    assert.deepStrictEqual(restoredCursor, checkpoint, 'recuperação preserva o cursor');
    assert.strictEqual(progress.phase, 'paused', 'recuperação informa progresso pausado');
    assert.strictEqual(progress.detail, status.job.message, 'recuperação preserva a instrução para retomar');
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
    const hub = { key: 'MLB5672', nome: 'Acessórios', url: 'https://www.mercadolivre.com.br/c/acessorios-para-veiculos' };
    const child = { key: 'MLB191834', nome: 'Filtros', url: 'https://lista.mercadolivre.com.br/_CatId_MLB191834' };
    const h = harness([], { withoutChoices: true, taxonomyRoots: [hub], taxonomyChildren: { [hub.key]: [child] } });
    await assert.rejects(h.command('start'), /Escolha uma categoria/);
    assert.strictEqual(h.jobs.size, 0, 'não iniciar uma varredura ampla sem escolha confirmada');
    const roots = await h.command('categories');
    assert.deepStrictEqual(clone(roots), { items: [hub], parentCategory: null, complete: true,
        updatedAt: '2026-10-06T10:07:07.000Z', summary: { total: 2, rootCount: 1, subcategoryCount: 1, maxDepth: 2 }, childCounts: { [hub.key]: 1 } });
    assert.strictEqual(h.jobs.size, 0, 'abrir o seletor não cria execução');
    const children = await h.command('categories', 'one', null, { parentCategory: hub });
    assert.deepStrictEqual(clone(children.items), [child]);
    assert.strictEqual(h.windows.length, 0, 'raiz e filhos oficiais dispensam BrowserWindow');
    const leaf = await h.command('categories', 'one', null, { parentCategory: child });
    assert.deepStrictEqual(clone(leaf), { ...clone(roots), items: [], parentCategory: child, childCounts: {} }, 'folha confirmada preserva metadados da árvore completa');
    await h.command('categories', 'one', null, { parentCategory: child, forceRefresh: true });
    const refreshedAncestor = await h.command('categories', 'one', null, { parentCategory: hub });
    assert.deepStrictEqual(clone(refreshedAncestor.items), [child], 'refresh profundo preserva navegacao pelo breadcrumb conhecido');
    const beforeUnknown = h.taxonomyCalls.length;
    for (const invalid of [
        { ...child, key: 'MLB9999999' }, { ...child, nome: 'Nome alterado' },
        { ...child, url: 'https://evil.example/' }, { ...child, url: 'https://lista.mercadolivre.com.br/categoria-inventada' }
    ]) {
        await assert.rejects(h.command('categories', 'one', null, { parentCategory: invalid }), /Escolha uma categoria/);
        await assert.rejects(h.command('start', 'one', null, { category: invalid }), /Escolha uma categoria/);
    }
    assert.strictEqual(h.taxonomyCalls.length, beforeUnknown, 'seleção fabricada não chega ao cache público');
    const started = await h.command('start', 'one', null, { category: child });
    assert.deepStrictEqual(clone(started.job.scopeCategory), child);
    assert.deepStrictEqual(h.jobs.get(started.job.id).checkpoint.scope.category, child);
    await assert.rejects(h.command('categories'), /Pause ou encerre/);
    const beforeOtherOwner = h.taxonomyCalls.length;
    await assert.rejects(h.command('categories', 'two'), /n[aã]o encontrada/);
    assert.strictEqual(h.taxonomyCalls.length, beforeOtherOwner, 'proprietário inválido é rejeitado antes do cache');
    await h.command('pause', 'one', started.job.id);
    await h.api.coordinator().task;
    await assert.rejects(h.command('start', 'two', null, { category: child }), /Escolha uma categoria/, 'lista de uma sessão não autoriza outra');
    await h.api.quit();
}

async function testPublicTaxonomyAuthorizationBeforeCache() {
    const h = harness([], { withoutChoices: true, taxonomyDump: {
        MLB100: { id: 'MLB100', name: 'Categoria oficial', permalink: 'https://www.mercadolivre.com.br/c/ferramentas',
            path_from_root: [{ id: 'MLB100' }], children_categories: [{ id: 'MLB101' }] },
        MLB101: { id: 'MLB101', name: 'Subcategoria oficial', permalink: null,
            path_from_root: [{ id: 'MLB100' }, { id: 'MLB101' }], children_categories: [] }
    } });
    const roots = await h.command('categories');
    const again = await h.command('categories');
    assert.deepStrictEqual(clone(again), clone(roots), 'mesma árvore pública pode ser reutilizada');
    assert.deepStrictEqual(h.events.filter(event => event.type === 'taxonomy-load').map(event => event.owner), ['one', 'one']);
    assert.strictEqual(h.requests.filter(request => request.path === '/api/pesquisa-mercado/jobs/latest').length, 2, 'JK autoriza cada consulta antes de reutilizar cache');
    await assert.rejects(h.api.command(h.trusted, 'categories', { authToken: 'fixture-invalid-owner' }), /Sessão inválida/);
    assert.strictEqual(h.taxonomyCalls.length, 2, 'token inválido não lê nem aquece o cache');
    assert.strictEqual(h.api.peek().token, 'fixture-owner-one', 'falha de autorização preserva proprietário anterior');
    const child = await h.command('categories', 'one', null, { parentCategory: roots.items[0] });
    assert.strictEqual(child.items[0].key, 'MLB101');
    assert.strictEqual(child.items[0].url, 'https://lista.mercadolivre.com.br/_CatId_MLB101');
    assert.strictEqual(h.windows.length, 0, 'taxonomia pública não depende do login ML nem abre navegador');
    assert.strictEqual(h.events.filter(event => ['restore', 'extensions', 'create'].includes(event.type)).length, 0);
    assert.strictEqual(h.taxonomyConstructors.length, 1);
    assert.match(h.taxonomyConstructors[0].cachePath, /fixture-public-cache-only[\\/]public-mlb-taxonomy-v1\.json$/);
    await h.command('categories', 'two');
    await assert.rejects(h.command('start', 'two', null, { category: roots.items[0], authToken: 'fixture-owner-one' }), /Escolha uma categoria/, 'troca de token limpa escolhas, mesmo com cache público comum');
    assert.strictEqual(h.jobs.size, 0);
    await h.api.quit();
}

async function testTaxonomyRefreshFlagsAndFailure() {
    const h = harness([], { withoutChoices: true });
    await h.command('categories');
    const beforeInvalid = h.taxonomyCalls.length;
    for (const forceRefresh of [null, 'true', 1, [], {}]) {
        await assert.rejects(h.command('categories', 'one', null, { forceRefresh }), /Atualizacao de categorias invalida/);
    }
    assert.strictEqual(h.taxonomyCalls.length, beforeInvalid, 'refresh malformado não chega ao serviço');
    await h.command('categories', 'one', null, { forceRefresh: false });
    await h.command('categories', 'one', null, { forceRefresh: true });
    assert.deepStrictEqual(h.taxonomyCalls, [{ forceRefresh: false }, { forceRefresh: false }, { forceRefresh: true }]);
    h.options.failRefresh = true;
    await assert.rejects(h.command('categories', 'one', null, { forceRefresh: true }), /temporariamente indisponível/);
    assert.strictEqual(h.jobs.size, 0, 'falha no refresh não inicia execução');
    const cached = await h.command('categories', 'one', null, { parentCategory: category });
    assert.deepStrictEqual(clone(cached.parentCategory), category, 'falha de refresh preserva escolha anterior válida');
    h.options.failRefresh = false;
    const refreshed = await h.command('categories', 'one', null, { forceRefresh: true });
    assert.strictEqual(refreshed.complete, true, 'nova tentativa pode renovar árvore');
    assert.strictEqual(h.windows.length, 0, 'erro da API nunca abre fallback de scrape');
    await h.api.quit();
}

async function testTaxonomyRemovedCategoryRefresh() {
    const removed = { key: 'MLB700', nome: 'Categoria removida', url: 'https://lista.mercadolivre.com.br/_CatId_MLB700' };
    const replacement = { key: 'MLB701', nome: 'Categoria atual', url: 'https://lista.mercadolivre.com.br/_CatId_MLB701' };
    const h = harness([], { withoutChoices: true, taxonomyRoots: [removed] });
    await h.command('categories');
    h.options.taxonomyRoots = [replacement];
    await assert.rejects(h.command('categories', 'one', null, { parentCategory: removed, forceRefresh: true }), /deixou de estar disponivel/);
    const roots = await h.command('categories', 'one', null, { forceRefresh: true });
    assert.deepStrictEqual(clone(roots.items), [replacement]);
    await assert.rejects(h.command('start', 'one', null, { category: removed }), /Escolha uma categoria/, 'refresh da raiz invalida seleção removida');
    assert.strictEqual(h.jobs.size, 0);
    assert.strictEqual(h.windows.length, 0);
    await h.api.quit();
}

async function testTaxonomyQuitInflight() {
    const h = harness([], { withoutChoices: true, blockTaxonomy: true });
    const loading = h.command('categories');
    while (!h.taxonomyWaiting()) await tick();
    const quitting = h.api.quit();
    h.releaseTaxonomy();
    await assert.rejects(loading, /encerrando/);
    await quitting;
    assert.strictEqual(h.windows.length, 0, 'consulta atrasada não deixa navegador após encerramento');
    assert.strictEqual(h.jobs.size, 0);
    assert.strictEqual(h.api.peek().token, '');
    const loads = h.taxonomyCalls.length;
    await assert.rejects(h.command('categories'), /encerrando/);
    assert.strictEqual(h.taxonomyCalls.length, loads, 'encerramento bloqueia novas consultas');
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
    const h = harness([], { withoutChoices: true, realRunner: true, categoryPages, redirects,
        taxonomyRoots: [hub], taxonomyChildren: { [hub.key]: children } });
    try {
        await h.command('categories');
        const directory = await h.command('categories', 'one', null, { parentCategory: hub });
        for (const child of children) {
            const leaf = await h.command('categories', 'one', null, { parentCategory: child });
            assert.deepStrictEqual(clone(leaf.items), [],
                'seletor mantém a URL pública com slash para ' + child.nome);
            assert.deepStrictEqual(clone(leaf.parentCategory), child);
            assert.strictEqual(leaf.complete, true);
            assert.strictEqual(h.windows.length, 0, 'seleção pública histórica mantém URL sem navegar navegador');
        }
        assert.deepStrictEqual(clone(directory.items), children, 'os dez links públicos permanecem íntegros no cache do seletor');
        for (const child of children) {
            const before = h.windows.length ? h.windows[0].loads.length : 0;
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
    const redirected = harness([], { withoutChoices: true, realRunner: true, taxonomyRoots: [legacy], categoryPages: {
        'https://lista.mercadolivre.com.br/caminhoes': { readyState: 'complete', empty: true }
    }, redirects: { [legacy.url]: 'https://lista.mercadolivre.com.br/caminhoes' } });
    try {
        await redirected.command('categories');
        const started = await redirected.command('start', 'one', null, { category: legacy });
        await redirected.api.coordinator().task;
        const saved = redirected.jobs.get(started.job.id);
        assert.strictEqual(saved.status, 'completed_partial', 'executor rejeita perda real de /veiculos');
        assert.strictEqual(saved.counters.pages, 0, 'busca genérica não pode ser contada como categoria');
        assert.strictEqual(redirected.windows[0].webContents.getURL(), 'https://lista.mercadolivre.com.br/caminhoes');
        assert.strictEqual(redirected.windows[0].loads.filter(url => url === legacy.url).length, 1, 'divergência confirmada de escopo interrompe sem repetir navegador');
        assert.deepStrictEqual(saved.checkpoint.scope.category, legacy, 'erro de redirecionamento preserva escopo histórico');
    } finally { await redirected.api.quit(); }

    const resumed = harness([{ id: 'legacy-slash-job', owner: 'one', status: 'paused', revision: 3, counters: {}, checkpoint: {
        scope: { category: observed }, categories: [observed], categoryIndex: 0, discoveryInitialized: true,
        currentCategory: observed, incompleteCategories: []
    } }], { realRunner: true, withoutChoices: true, categoryPages: { [observed.url]: { readyState: 'complete', empty: true } } });
    try {
        await resumed.command('resume', 'one', 'legacy-slash-job');
        await resumed.api.coordinator().task;
        const saved = resumed.jobs.get('legacy-slash-job');
        assert.strictEqual(saved.status, 'completed');
        assert.deepStrictEqual(saved.checkpoint.scope.category, observed, 'retomada preserva URL com slash do checkpoint histórico');
        assert.deepStrictEqual(resumed.windows[0].loads, [observed.url]);
        assert.strictEqual(resumed.taxonomyCalls.length, 0, 'job histórico não precisa trocar escopo pelo cache atual');
    } finally { await resumed.api.quit(); }

}
async function testNamesCommandOwnershipAndRecovery() {
    const profile = 'https://lista.mercadolivre.com.br/_CustId_123';
    const h = harness([
        { id: 'old-names', owner: 'one', status: 'completed', revision: 4, counters: { ads: 48 }, checkpoint: { phase: 'completed' } },
        { id: 'another-paused', owner: 'one', status: 'paused', revision: 7, counters: { ads: 15 }, checkpoint: { phase: 'seller' } }
    ], { sellers: [{ seller_key: 'id:123', key: 'id:123', id: '123', nome: 'Nome pendente — vendedor 123', perfilUrl: profile, name_pending: true }],
        categoryPages: { [profile]: { sellerId: '123', perfilUrl: profile, vendedor: 'Loja confirmada', readyState: 'complete' } } });
    const runner = h.api.coordinator();
    runner.job = clone(h.jobs.get('another-paused')); runner.checkpoint = clone(runner.job.checkpoint);
    const previous = clone(runner.job), checkpoint = clone(runner.checkpoint);
    try {
        await assert.rejects(h.command('refresh-names', 'two', 'old-names'), /Pesquisa não encontrada/);
        assert.strictEqual(h.windows.length, 0, 'exact ownership is required before profile navigation');
        const result = await h.command('refresh-names', 'one', 'old-names');
        assert.strictEqual(result.updated, 1); assert.strictEqual(result.pending, 0);
        assert.deepStrictEqual(clone(runner.job), previous);
        assert.deepStrictEqual(clone(runner.checkpoint), checkpoint);
        assert.strictEqual(h.jobs.get('old-names').status, 'completed');
        assert.deepStrictEqual(h.windows[0].loads, [profile]);
        await h.command('show-browser', 'one', 'old-names', { purpose: 'names' });
        assert.strictEqual(h.windows[0].visible, true);
        assert.deepStrictEqual(clone(runner.job), previous);
        assert.ok(!h.requests.some(request => request.method === 'PATCH'), 'old recovery never pauses or resumes the other job');
        const ipc = fs.readFileSync(path.join(root, 'electron_app/main/modules/ipc.js'), 'utf8');
        const preload = fs.readFileSync(path.join(root, 'electron_app/preload.js'), 'utf8');
        assert.match(ipc, /'categories', 'refresh-names'/);
        assert.match(preload, /refreshPesquisaMercadoNames: payload => ipcRenderer\.invoke\('pesquisa-mercado:refresh-names'/);
    } finally { await h.api.quit(); }
}
async function testAvantConfigurationOwnership() {
    const h = harness();
    const email = 'researcher@account.example';
    const secondEmail = 'other@account.example';
    try {
        await assert.rejects(h.command('status', 'one', null, { avantLoginEmail: email }), /E-mail/);
        await assert.rejects(h.command('start', 'one', null, { avantLoginEmail: 'invalid\naccount' }), /E-mail/);
        assert.strictEqual(h.requests.length, 0, 'reject invalid configuration before backend/browser');
        const created = await h.command('start', 'one', null, { avantLoginEmail: email });
        await h.api.browser().prepare();
        await h.api.browser().ensureAvantSession({});
        assert.strictEqual(h.avantChecks.at(-1).email, email);
        assert.strictEqual(h.avantChecks.at(-1).contents, h.windows[0].webContents, 'login must target the research window');
        await assert.rejects(h.command('start', 'two', null, { avantLoginEmail: secondEmail }), /Pesquisa n[aã]o encontrada/);
        await h.api.browser().ensureAvantSession({});
        assert.strictEqual(h.avantChecks.at(-1).email, email, 'failed ownership cannot change login account');
        await h.command('pause', 'one', created.job.id);
        await h.command('status', 'two');
        await h.api.browser().ensureAvantSession({});
        assert.strictEqual(h.avantChecks.at(-1).email, '', 'new idle owner cannot inherit email');
        assert.ok(!JSON.stringify(h.requests).includes(email), 'configuration is never persisted into backend jobs');
        assert.ok(!JSON.stringify(h.events).includes(email), 'progress never exposes the account');
    } finally { await h.api.quit(); }
}

async function testNamesLoginContextOwnership() {
    const h = harness([{ id: 'private-names', owner: 'one', status: 'completed', revision: 1, counters: { ads: 48 }, checkpoint: {} }]);
    const runner = h.api.coordinator();
    await h.command('status', 'one');
    runner.namesTask = Promise.resolve(); runner.namesJobId = 'private-names';
    try {
        await assert.rejects(h.command('status', 'two'), /Pesquisa n[aã]o encontrada/);
        assert.strictEqual(h.api.peek().token, 'fixture-owner-one', 'pending names keep their authorized backend context');
        assert.strictEqual(h.windows.length, 0);
    } finally { runner.namesTask = null; await h.api.quit(); }
}

async function main() {
    await testNamesLoginContextOwnership();
    await testAvantConfigurationOwnership();
    await testNamesCommandOwnershipAndRecovery();
    await testPublicVehicleUrlsPreserveTrailingSlash();
    await testBrowserIsolationAndNavigation();
    await testOwnershipAndQueue();
    await testOrphanAndShowCursor();
    await testQuitDuringPreparation();
    await testSwitchingPausedJobsShowsOwnedCursor();
    await testCategorySelectionAndIsolation();
    await testPublicTaxonomyAuthorizationBeforeCache();
    await testTaxonomyRefreshFlagsAndFailure();
    await testTaxonomyRemovedCategoryRefresh();
    await testTaxonomyQuitInflight();
    console.log('Pesquisa de Mercado main browser: janela exclusiva, navegação, ownership, recuperação e encerramento OK');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
