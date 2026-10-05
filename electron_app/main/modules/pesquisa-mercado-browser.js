const pesquisaMercadoRunnerModule = require(path.join(__dirname, 'electron_app', 'main', 'modules', 'pesquisa-mercado-runner.js'));
let pesquisaMercadoCoordinator = null;
let pesquisaMercadoWindow = null;
let pesquisaMercadoBrowserJobId = '';
let pesquisaMercadoAuthToken = '';
let pesquisaMercadoQuitting = false;
let pesquisaMercadoCommandQueue = Promise.resolve();
let pesquisaMercadoCategoriesToken = '';
let pesquisaMercadoCategoryChoices = new Map();
let pesquisaMercadoCategoryChildren = new Map();
let pesquisaMercadoCategoryLastUrl = '';

function rememberPesquisaMercadoCategories(items) {
    const result = [];
    for (const raw of items || []) {
        const category = pesquisaMercadoRunnerModule.normalizeCategory({ key: raw.key, nome: raw.nome, url: raw.url });
        if (!category || result.some(item => item.url === category.url)) continue;
        pesquisaMercadoCategoryChoices.set(category.url, category);
        result.push(category);
    }
    return result;
}

function knownPesquisaMercadoCategory(raw) {
    const category = pesquisaMercadoRunnerModule.normalizeCategory(raw);
    const known = category && pesquisaMercadoCategoryChoices.get(category.url);
    if (!known || known.key !== category.key || known.nome !== category.nome) throw new Error('Escolha uma categoria ou subcategoria na lista do Mercado Livre.');
    return { ...known };
}

async function loadPesquisaMercadoCategories(runner, parentCategory) {
    if (runner.task) throw new Error('Pause ou encerre a pesquisa antes de consultar outras categorias.');
    const parent = parentCategory == null ? null : knownPesquisaMercadoCategory(parentCategory);
    if (parent && pesquisaMercadoCategoryChildren.has(parent.url)) {
        return { items: pesquisaMercadoCategoryChildren.get(parent.url), parentCategory: parent };
    }
    const browser = runner.browser;
    await browser.prepare();
    const url = parent ? parent.url : 'https://www.mercadolivre.com.br/categorias';
    pesquisaMercadoCategoryLastUrl = url;
    let page;
    let lastError;
    let waitingLogin = false;
    try {
        for (let attempt = 0; attempt < 3; attempt += 1) {
            try {
                if (pesquisaMercadoQuitting) throw new Error('O aplicativo esta encerrando.');
                await browser.navigate(url);
                const destination = pesquisaMercadoRunnerModule.categoryPageWaiter(url);
                for (let poll = 0; poll < 34; poll += 1) {
                    await runner.wait(450);
                    if (pesquisaMercadoQuitting) throw new Error('O aplicativo esta encerrando.');
                    page = await browser.executeJavaScript(runner.scripts.discoveryScript());
                    if (pesquisaMercadoQuitting) throw new Error('O aplicativo esta encerrando.');
                    if (page.authRequired || page.avantAuthRequired) {
                        if (!pesquisaMercadoRunnerModule.currentPageRequiresLogin(page, browser)) {
                            destination.observe(page.url || browser.getURL(), false, true);
                            page = null;
                            continue;
                        }
                        waitingLogin = true;
                        return { items: [], parentCategory: parent, authRequired: true,
                            reason: 'Conclua o login no navegador e carregue as categorias novamente.' };
                    }
                    const effectiveUrl = page.url || browser.getURL();
                    const ready = (page.categories || []).length || (page.categoryTree || []).length || parent && ((page.anuncios || []).length || page.empty || page.listingUrl);
                    const loading = page.readyState === 'loading' || typeof browser.isLoading === 'function' && browser.isLoading();
                    if (destination.observe(effectiveUrl, ready, loading)) break;
                    page = null;
                }
                if (!page && destination.mismatch) throw new Error('O Mercado Livre abriu outra categoria. Recarregue a lista para escolher novamente.');
            } catch (error) {
                if (pesquisaMercadoQuitting) throw error;
                lastError = error;
                page = null;
            }
            if (page) break;
            if (attempt < 2) await runner.wait(600 * (attempt + 1));
        }
        if (!page) throw lastError || new Error('Nao foi possivel carregar as categorias do Mercado Livre. Tente novamente.');
        for (const group of page.categoryTree || []) {
            const roots = rememberPesquisaMercadoCategories([group]);
            if (roots.length) pesquisaMercadoCategoryChildren.set(roots[0].url, rememberPesquisaMercadoCategories(group.children));
        }
        const items = rememberPesquisaMercadoCategories(page.categories);
        return { items, parentCategory: parent };
    } finally {
        if (!waitingLogin) await browser.finish('paused');
    }
}

function pesquisaMercadoBackendRequest(method, pathname, body, token = pesquisaMercadoAuthToken) {
    return new Promise((resolve, reject) => {
        const value = String(token || '').trim();
        if (!value || value.length > 16384) {
            const err = new Error('Faca login no JK Sistema para pesquisar.');
            err.status = 401;
            reject(err);
            return;
        }
        if (!/^\/api\/pesquisa-mercado\//.test(pathname)) return reject(new Error('Rota de pesquisa invalida.'));
        const payload = body === undefined ? '' : JSON.stringify(body);
        const headers = { Accept: 'application/json', Authorization: /^bearer /i.test(value) ? value : `Bearer ${value}` };
        if (payload) {
            headers['Content-Type'] = 'application/json';
            headers['Content-Length'] = Buffer.byteLength(payload);
        }
        const request = http.request({ hostname: '127.0.0.1', port: JK_LOCAL_BACKEND_PORT,
            path: pathname, method, headers, timeout: 30000 }, response => {
            let data = '';
            response.setEncoding('utf8');
            response.on('data', chunk => {
                data += chunk;
                if (data.length > 64 * 1024 * 1024) request.destroy(new Error('Resposta de pesquisa excede o tamanho permitido.'));
            });
            response.on('error', reject);
            response.on('end', () => {
                let parsed;
                try { parsed = data ? JSON.parse(data) : {}; }
                catch (_err) { return reject(new Error('O backend retornou uma resposta de pesquisa invalida.')); }
                if (response.statusCode >= 200 && response.statusCode < 300) return resolve(parsed);
                const err = new Error(typeof parsed.detail === 'string' ? parsed.detail : 'Nao foi possivel atualizar a pesquisa.');
                err.status = response.statusCode;
                reject(err);
            });
        });
        request.on('error', reject);
        request.on('timeout', () => request.destroy(new Error('Tempo esgotado ao gravar a pesquisa.')));
        request.end(payload);
    });
}

function ensurePesquisaMercadoBrowser() {
    if (pesquisaMercadoQuitting) throw new Error('O aplicativo esta encerrando.');
    if (pesquisaMercadoWindow && !pesquisaMercadoWindow.isDestroyed()) return pesquisaMercadoWindow;
    const worker = new BrowserWindow({ width: 1280, height: 900, show: false,
        title: 'Pesquisa de Mercado - coleta', backgroundColor: '#ffffff',
        webPreferences: { contextIsolation: true, nodeIntegration: false, backgroundThrottling: false,
            userAgent: ML_BROWSER_USER_AGENT, session: getMlSession() }
    });
    pesquisaMercadoWindow = worker;
    worker.setMenuBarVisibility(false);
    worker.webContents.setUserAgent(ML_BROWSER_USER_AGENT);
    worker.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    // Closing the auxiliary window only hides it, preserving the active job.
    worker.on('close', event => { if (!pesquisaMercadoQuitting) { event.preventDefault(); worker.hide(); } });
    worker.on('closed', () => { if (pesquisaMercadoWindow === worker) { pesquisaMercadoWindow = null; pesquisaMercadoBrowserJobId = ''; } });
    worker.webContents.on('will-navigate', (event, url) => {
        if (!isAllowedFavoritosWorkerUrl(url)) event.preventDefault();
    });
    worker.webContents.on('will-redirect', (event, url) => {
        if (!isAllowedFavoritosWorkerUrl(url)) event.preventDefault();
    });
    try { registerEmbeddedMlBrowserDownloadGuard(worker.webContents); } catch (_err) {}
    return worker;
}

function pesquisaMercadoBrowserAdapter() {
    return {
        async prepare() {
            if (pesquisaMercadoQuitting) throw new Error('O aplicativo esta encerrando.');
            await restaurarSessaoAvantProAntesDeAbrirNavegador('before-pesquisa-mercado-start', {});
            if (pesquisaMercadoQuitting) throw new Error('O aplicativo esta encerrando.');
            await ensureChromeExtensionsForMlSession();
            ensurePesquisaMercadoBrowser();
        },
        getURL() {
            return pesquisaMercadoWindow && !pesquisaMercadoWindow.isDestroyed() ? pesquisaMercadoWindow.webContents.getURL() : '';
        },
        isLoading() {
            return !!(pesquisaMercadoWindow && !pesquisaMercadoWindow.isDestroyed() && pesquisaMercadoWindow.webContents.isLoadingMainFrame());
        },
        async navigate(url, signal) {
            if (!pesquisaMercadoRunnerModule.allowedUrl(url)) throw new Error('Link fora do Mercado Livre.');
            const worker = ensurePesquisaMercadoBrowser();
            if (signal && signal.aborted) { const err = new Error('Pesquisa interrompida.'); err.name = 'AbortError'; throw err; }
            let timer;
            let abort;
            const interrupted = new Promise((_resolve, reject) => {
                const fail = message => { worker.webContents.stop(); const err = new Error(message); err.name = signal && signal.aborted ? 'AbortError' : 'TimeoutError'; reject(err); };
                timer = setTimeout(() => fail('Tempo esgotado ao abrir a pagina.'), 35000);
                abort = () => fail('Pesquisa interrompida.');
                if (signal) signal.addEventListener('abort', abort, { once: true });
            });
            try {
                await Promise.race([worker.loadURL(url), interrupted]);
                pesquisaMercadoBrowserJobId = String(pesquisaMercadoCoordinator && pesquisaMercadoCoordinator.job && pesquisaMercadoCoordinator.job.id || '');
            }
            finally { clearTimeout(timer); if (signal) signal.removeEventListener('abort', abort); }
        },
        async executeJavaScript(code) {
            const worker = ensurePesquisaMercadoBrowser();
            if (!isAllowedFavoritosWorkerUrl(worker.webContents.getURL())) throw new Error('Pagina de coleta nao permitida.');
            let timer;
            try {
                return await Promise.race([
                    worker.webContents.executeJavaScript(String(code), true),
                    new Promise((_resolve, reject) => { timer = setTimeout(() => reject(new Error('Tempo esgotado ao ler os dados.')), 12000); })
                ]);
            } finally { clearTimeout(timer); }
        },
        stop() { if (pesquisaMercadoWindow && !pesquisaMercadoWindow.isDestroyed()) pesquisaMercadoWindow.webContents.stop(); },
        async finish(status) {
            if (pesquisaMercadoWindow && !pesquisaMercadoWindow.isDestroyed() && status !== 'waiting_login') pesquisaMercadoWindow.hide();
        }
    };
}

function getPesquisaMercadoCoordinator() {
    if (pesquisaMercadoCoordinator) return pesquisaMercadoCoordinator;
    const sharedDir = path.join(__dirname, 'static', 'pesquisa-mercado');
    pesquisaMercadoCoordinator = new pesquisaMercadoRunnerModule.PesquisaMercadoRunner({
        api: pesquisaMercadoBackendRequest,
        browser: pesquisaMercadoBrowserAdapter(),
        pageScripts: require(path.join(sharedDir, 'page-scripts.js')),
        collector: require(path.join(sharedDir, 'shared-collector.js')),
        emit: payload => {
            if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('pesquisa-mercado:progress', payload);
        }
    });
    return pesquisaMercadoCoordinator;
}

function pesquisaMercadoCommand(event, action, payload = {}) {
    assertTrustedFavoritosIpcSender(event, `pesquisa-mercado:${action}`);
    const command = pesquisaMercadoCommandQueue.then(() => executePesquisaMercadoCommand(action, payload));
    pesquisaMercadoCommandQueue = command.catch(() => {});
    return command;
}

async function executePesquisaMercadoCommand(action, payload) {
    if (pesquisaMercadoQuitting) throw new Error('O aplicativo esta encerrando; a pesquisa sera preservada.');
    if (!['start', 'status', 'resume', 'pause', 'cancel', 'show-browser', 'categories'].includes(action)) throw new Error('Comando de pesquisa desconhecido.');
    const token = String(payload.authToken || '').trim();
    const runner = getPesquisaMercadoCoordinator();
    const jobId = String(payload.jobId || runner.job && runner.job.id || '');
    if (runner.task && payload.jobId && runner.job.id !== jobId) throw new Error('Outra pesquisa ja esta em execucao neste aplicativo.');
    let authorizedLatest;
    // Validate ownership using the new token before replacing the active request context.
    if (runner.task && runner.job) {
        await pesquisaMercadoBackendRequest('GET', `/api/pesquisa-mercado/jobs/${encodeURIComponent(runner.job.id)}`, undefined, token);
    } else {
        authorizedLatest = await pesquisaMercadoBackendRequest('GET', '/api/pesquisa-mercado/jobs/latest', undefined, token);
    }
    pesquisaMercadoAuthToken = token;
    if (pesquisaMercadoCategoriesToken !== token) {
        pesquisaMercadoCategoriesToken = token;
        pesquisaMercadoCategoryChoices.clear();
        pesquisaMercadoCategoryChildren.clear();
        pesquisaMercadoCategoryLastUrl = '';
    }
    if (action === 'categories') return loadPesquisaMercadoCategories(runner, payload.parentCategory);
    if (action === 'start') return runner.start(knownPesquisaMercadoCategory(payload.category));
    if (action === 'status') return runner.status(payload.jobId || '');
    if (action === 'resume') return runner.resume(jobId);
    if (action === 'pause' || action === 'cancel') {
        if (!jobId) return { job: null };
        if (!runner.task || !runner.job || runner.job.id !== jobId) {
            const response = await pesquisaMercadoBackendRequest('GET', `/api/pesquisa-mercado/jobs/${encodeURIComponent(jobId)}`);
            runner.job = response.job;
            runner.checkpoint = response.job.checkpoint;
        }
        return runner.stop(action === 'cancel' ? 'cancelled' : 'paused');
    }
    if (action === 'show-browser') {
        if (payload.purpose === 'categories') {
            if (runner.task) throw new Error('A pesquisa esta usando este navegador.');
            if (!pesquisaMercadoCategoryLastUrl) throw new Error('Carregue as categorias antes de abrir o navegador.');
            await runner.browser.prepare();
            const worker = ensurePesquisaMercadoBrowser();
            if (!worker.webContents.getURL()) await runner.browser.navigate(pesquisaMercadoCategoryLastUrl);
            worker.show(); worker.focus();
            return { success: true };
        }
        const response = jobId ? await pesquisaMercadoBackendRequest('GET', `/api/pesquisa-mercado/jobs/${encodeURIComponent(jobId)}`) : authorizedLatest;
        if (!response || !response.job || !['running', 'paused', 'waiting_login', 'failed'].includes(response.job.status)) throw new Error('Inicie ou retome uma pesquisa para abrir seu navegador.');
        if (!runner.task) {
            runner.job = response.job;
            runner.checkpoint = response.job.checkpoint || {};
        }
        const hadBrowser = pesquisaMercadoWindow && !pesquisaMercadoWindow.isDestroyed();
        const changedJob = pesquisaMercadoBrowserJobId !== response.job.id;
        if (!hadBrowser) await runner.browser.prepare();
        const worker = ensurePesquisaMercadoBrowser();
        if (!hadBrowser || changedJob || !worker.webContents.getURL()) {
            const checkpoint = runner.checkpoint || {};
            const page = checkpoint.page || {};
            const seed = (page.anuncios || [])[Number(page.index || 0)];
            const target = seed && seed.url || page.url || checkpoint.currentCategory && checkpoint.currentCategory.url || 'https://www.mercadolivre.com.br/';
            await runner.browser.navigate(pesquisaMercadoRunnerModule.allowedUrl(target) ? target : 'https://www.mercadolivre.com.br/');
        }
        worker.show(); worker.focus();
        return runner.snapshot();
    }
    throw new Error('Comando de pesquisa desconhecido.');
}

async function persistPesquisaMercadoBeforeQuit() {
    pesquisaMercadoQuitting = true;
    if (pesquisaMercadoCoordinator) pesquisaMercadoCoordinator.browser.stop();
    await pesquisaMercadoCommandQueue;
    const runner = pesquisaMercadoCoordinator;
    try {
        if (runner && runner.task) await runner.stop('paused');
    } finally {
        pesquisaMercadoAuthToken = '';
        pesquisaMercadoCategoriesToken = '';
        pesquisaMercadoCategoryChoices.clear();
        pesquisaMercadoCategoryChildren.clear();
        if (pesquisaMercadoWindow && !pesquisaMercadoWindow.isDestroyed()) pesquisaMercadoWindow.destroy();
    }
}
