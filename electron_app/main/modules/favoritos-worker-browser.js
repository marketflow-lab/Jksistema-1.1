const FAVORITOS_WORKER_POOL_LIMIT = 4;
const FAVORITOS_WORKER_LEGACY_ID = 'w0';
const FAVORITOS_WORKER_MAX_SCRIPT_LENGTH = 1024 * 1024;
const FAVORITOS_WORKER_EXECUTE_TIMEOUT_MS = 12000;
const favoritosWorkerBrowsers = new Map();
let favoritosWorkersPoolFocusCursor = 0;
let favoritosWorkerLegacyHandoffToPool = false;
let favoritosWorkersPoolState = {
    active: false,
    poolId: '',
    status: 'idle',
    visible: true,
    paused: false,
    cancelRequested: false,
    startedAt: 0,
    finishedAt: 0,
    message: '',
    preparation: null
};

function normalizarFavoritosWorkerId(value = FAVORITOS_WORKER_LEGACY_ID) {
    const workerId = String(value || FAVORITOS_WORKER_LEGACY_ID).trim().toLowerCase();
    if (workerId === FAVORITOS_WORKER_LEGACY_ID || /^w[1-4]$/.test(workerId)) return workerId;
    throw new Error('Identificador invalido para o navegador trabalhador do Favoritos.');
}

function favoritosWorkerPoolIdNovo() {
    return `favoritos-pool-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function favoritosWorkerEstadoInicial(workerId) {
    return {
        workerId,
        poolId: workerId === FAVORITOS_WORKER_LEGACY_ID ? '' : favoritosWorkersPoolState.poolId,
        active: false,
        paused: false,
        cancelRequested: false,
        visible: false,
        status: 'idle',
        message: '',
        url: '',
        sku: '',
        attempt: 0,
        updatedAt: 0
    };
}

function obterRegistroFavoritosWorker(workerId = FAVORITOS_WORKER_LEGACY_ID, criar = true) {
    const id = normalizarFavoritosWorkerId(workerId);
    let record = favoritosWorkerBrowsers.get(id) || null;
    if (!record && criar) {
        record = {
            workerId: id,
            window: null,
            generation: 0,
            lastUrl: '',
            state: favoritosWorkerEstadoInicial(id)
        };
        favoritosWorkerBrowsers.set(id, record);
    }
    return record;
}

function isAllowedFavoritosWorkerUrl(targetUrl) {
    const value = String(targetUrl || '').trim();
    if (!value || value === 'about:blank') return true;
    try {
        const url = new URL(normalizeTargetUrl(value));
        const host = String(url.hostname || '').toLowerCase();
        return url.protocol === 'https:' && (
            isMercadoLivreHost(host)
            || host === 'avantprocloud.com.br'
            || host.endsWith('.avantprocloud.com.br')
        );
    } catch (_err) {
        return false;
    }
}

function assertAllowedFavoritosWorkerUrl(targetUrl) {
    if (!isAllowedFavoritosWorkerUrl(targetUrl)) {
        throw new Error('URL nao permitida no navegador trabalhador do Favoritos.');
    }
}

function emitFavoritosWorkerEvent(channel, payload = {}) {
    for (const win of BrowserWindow.getAllWindows()) {
        try {
            if (win && !win.isDestroyed() && win.webContents && !win.webContents.isDestroyed()) {
                win.webContents.send(channel, payload);
            }
        } catch (_err) {}
    }
}

function favoritosWorkerBrowserStatus(workerId = FAVORITOS_WORKER_LEGACY_ID, extra = {}) {
    const record = obterRegistroFavoritosWorker(workerId, true);
    const worker = record.window;
    const windowAlive = !!(worker && !worker.isDestroyed());
    const visible = windowAlive && worker.isVisible();
    let url = record.state.url || record.lastUrl || '';
    if (windowAlive) {
        try { url = worker.webContents.getURL() || url; } catch (_err) {}
    }
    return {
        ...record.state,
        workerId: record.workerId,
        active: !!record.state.active,
        paused: !!record.state.paused,
        cancelRequested: !!record.state.cancelRequested,
        visible,
        hasWindow: windowAlive,
        url,
        ...extra
    };
}

function favoritosWorkersPoolStatus(extra = {}) {
    const workers = [...favoritosWorkerBrowsers.values()]
        .filter(record => (
            record.workerId !== FAVORITOS_WORKER_LEGACY_ID
            && (!favoritosWorkersPoolState.poolId || record.state.poolId === favoritosWorkersPoolState.poolId)
        ))
        .sort((a, b) => a.workerId.localeCompare(b.workerId))
        .map(record => favoritosWorkerBrowserStatus(record.workerId));
    const counts = {
        total: workers.length,
        active: workers.filter(item => item.active).length,
        paused: workers.filter(item => item.paused).length,
        visible: workers.filter(item => item.visible).length,
        error: workers.filter(item => item.status === 'error').length,
        done: workers.filter(item => ['done', 'stopped', 'closed'].includes(item.status)).length,
        canceled: workers.filter(item => ['canceled', 'cancelled'].includes(item.status)).length
    };
    return {
        success: true,
        poolId: favoritosWorkersPoolState.poolId,
        active: !!favoritosWorkersPoolState.active,
        paused: !!favoritosWorkersPoolState.paused,
        cancelRequested: !!favoritosWorkersPoolState.cancelRequested,
        visible: !!favoritosWorkersPoolState.visible,
        status: favoritosWorkersPoolState.status,
        message: favoritosWorkersPoolState.message,
        startedAt: favoritosWorkersPoolState.startedAt,
        finishedAt: favoritosWorkersPoolState.finishedAt,
        limit: FAVORITOS_WORKER_POOL_LIMIT,
        counts,
        workers,
        ...extra
    };
}

function emitirEstadoFavoritosWorker(record, channel = 'favoritos-worker:progress', extra = {}) {
    const workerStatus = favoritosWorkerBrowserStatus(record.workerId, extra);
    const pool = record.workerId === FAVORITOS_WORKER_LEGACY_ID
        ? null
        : favoritosWorkersPoolStatus();
    emitFavoritosWorkerEvent(channel, {
        ...workerStatus,
        pool: pool ? {
            poolId: pool.poolId,
            active: pool.active,
            paused: pool.paused,
            status: pool.status,
            counts: pool.counts,
            startedAt: pool.startedAt,
            finishedAt: pool.finishedAt
        } : null
    });
    return workerStatus;
}

function setFavoritosWorkerBrowserState(workerId, patch = {}, channel = 'favoritos-worker:progress') {
    const record = obterRegistroFavoritosWorker(workerId, true);
    record.state = {
        ...record.state,
        ...patch,
        workerId: record.workerId,
        updatedAt: Date.now()
    };
    return emitirEstadoFavoritosWorker(record, channel);
}

function favoritosWorkerCanceledError(workerId = FAVORITOS_WORKER_LEGACY_ID) {
    const error = new Error(`Navegador trabalhador ${normalizarFavoritosWorkerId(workerId)} do Favoritos cancelado.`);
    error.name = 'AbortError';
    error.canceladoFavoritos = true;
    error.workerId = normalizarFavoritosWorkerId(workerId);
    return error;
}

function assertFavoritosWorkerLegadoDisponivel(workerId = FAVORITOS_WORKER_LEGACY_ID) {
    const id = normalizarFavoritosWorkerId(workerId);
    if (
        id !== FAVORITOS_WORKER_LEGACY_ID
        || (!favoritosWorkerLegacyHandoffToPool && !favoritosWorkersPoolState.active)
    ) return id;
    const error = new Error('O navegador legado w0 do Favoritos nao pode ser criado enquanto o pool de trabalhadores esta ativo.');
    error.name = 'ConflictError';
    error.code = 'FAVORITOS_WORKERS_POOL_ACTIVE';
    error.workerId = id;
    error.poolId = favoritosWorkersPoolState.poolId;
    throw error;
}

function assertFavoritosWorkerGeneration(record, generation, worker = null) {
    if (!record || generation !== record.generation) throw favoritosWorkerCanceledError(record && record.workerId);
    if (worker && (worker.isDestroyed() || worker.__jkFavoritosWorkerGeneration !== generation)) {
        throw favoritosWorkerCanceledError(record.workerId);
    }
}

function favoritosWorkerWindowTitle(record) {
    const numero = record.workerId === FAVORITOS_WORKER_LEGACY_ID
        ? ''
        : record.workerId.replace(/^w/, '');
    const sku = String(record.state.sku || '').trim();
    const attempt = Math.max(0, Number(record.state.attempt) || 0);
    const prefix = numero ? `Favoritos ML - Trabalhador ${numero}` : 'Favoritos ML - Navegador Trabalhador';
    return `${prefix}${sku ? ` - SKU ${sku}` : ''}${attempt > 1 ? ` - Tentativa ${attempt}` : ''}`;
}

function atualizarTituloFavoritosWorker(record) {
    const worker = record && record.window;
    if (!worker || worker.isDestroyed() || typeof worker.setTitle !== 'function') return;
    try { worker.setTitle(favoritosWorkerWindowTitle(record)); } catch (_err) {}
}

function ensureFavoritosWorkerBrowser(parent = null, workerId = FAVORITOS_WORKER_LEGACY_ID) {
    const id = assertFavoritosWorkerLegadoDisponivel(workerId);
    const record = obterRegistroFavoritosWorker(id, true);
    if (record.window && !record.window.isDestroyed()) return record.window;

    if (!record.generation) record.generation = 1;
    const index = record.workerId === FAVORITOS_WORKER_LEGACY_ID
        ? 0
        : Math.max(0, Number(record.workerId.slice(1)) - 1);
    const worker = new BrowserWindow({
        x: 28 + (index * 32),
        y: 42 + (index * 28),
        width: 1280,
        height: 900,
        show: false,
        title: favoritosWorkerWindowTitle(record),
        backgroundColor: '#ffffff',
        skipTaskbar: false,
        webPreferences: {
            contextIsolation: true,
            nodeIntegration: false,
            backgroundThrottling: false,
            nativeWindowOpen: true,
            userAgent: ML_BROWSER_USER_AGENT,
            session: getMlSession()
        }
    });

    worker.__jkFavoritosWorkerId = record.workerId;
    worker.__jkFavoritosWorkerGeneration = record.generation;
    record.window = worker;
    worker.setMenuBarVisibility(false);
    try { worker.setAlwaysOnTop(false); } catch (_err) {}
    try { worker.webContents.setUserAgent(ML_BROWSER_USER_AGENT); } catch (_err) {}
    try { registerAvantProConsoleDiagnostics(worker.webContents); } catch (_err) {}
    try { registerEmbeddedMlBrowserDownloadGuard(worker.webContents); } catch (_err) {}

    worker.on('closed', () => {
        const generation = worker.__jkFavoritosWorkerGeneration;
        if (record.window === worker) record.window = null;
        if (generation !== record.generation) return;
        const cancelado = !!record.state.cancelRequested || record.state.status === 'canceled';
        setFavoritosWorkerBrowserState(record.workerId, {
            active: false,
            paused: false,
            visible: false,
            status: cancelado ? 'canceled' : 'closed',
            message: cancelado ? '' : `Trabalhador ${record.workerId} fechado.`
        }, 'favoritos-worker:done');
    });
    worker.on('show', () => {
        if (record.window !== worker || worker.__jkFavoritosWorkerGeneration !== record.generation) return;
        setFavoritosWorkerBrowserState(record.workerId, { visible: true });
    });
    worker.on('hide', () => {
        if (record.window !== worker || worker.__jkFavoritosWorkerGeneration !== record.generation) return;
        setFavoritosWorkerBrowserState(record.workerId, { visible: false });
    });
    worker.on('page-title-updated', (event) => {
        try { if (event && typeof event.preventDefault === 'function') event.preventDefault(); } catch (_err) {}
        atualizarTituloFavoritosWorker(record);
    });
    worker.webContents.on('did-start-loading', () => {
        if (record.window !== worker || worker.__jkFavoritosWorkerGeneration !== record.generation) return;
        if (record.state.cancelRequested || record.state.status === 'canceled') return;
        setFavoritosWorkerBrowserState(record.workerId, {
            status: 'loading',
            message: record.state.sku
                ? `Trabalhador ${record.workerId}: carregando SKU ${record.state.sku}.`
                : `Trabalhador ${record.workerId}: carregando Mercado Livre.`,
            url: !worker.isDestroyed() ? worker.webContents.getURL() : record.state.url
        });
    });
    worker.webContents.on('did-finish-load', () => {
        if (record.window !== worker || worker.__jkFavoritosWorkerGeneration !== record.generation) return;
        if (record.state.cancelRequested || record.state.status === 'canceled') return;
        setFavoritosWorkerBrowserState(record.workerId, {
            status: 'running',
            message: `Trabalhador ${record.workerId} pronto.`,
            url: !worker.isDestroyed() ? worker.webContents.getURL() : record.state.url
        });
    });
    worker.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
        if (record.window !== worker || worker.__jkFavoritosWorkerGeneration !== record.generation) return;
        if (record.state.cancelRequested || record.state.status === 'canceled') return;
        if (isIgnorableNavigationAbort(errorCode, errorDescription)) return;
        setFavoritosWorkerBrowserState(record.workerId, {
            status: 'error',
            message: errorDescription || `Falha no trabalhador ${record.workerId}.`,
            url: validatedURL || record.state.url
        }, 'favoritos-worker:error');
    });
    worker.webContents.on('will-navigate', (event, targetUrl) => {
        if (isAllowedFavoritosWorkerUrl(targetUrl)) return;
        event.preventDefault();
        logElectronLifecycle('favoritos-worker-browser-navigation-blocked', {
            workerId: record.workerId,
            url: targetUrl
        });
    });

    logElectronLifecycle('favoritos-worker-browser-created', {
        workerId: record.workerId,
        poolId: record.state.poolId || '',
        partition: getBrowserSessionPartition ? getBrowserSessionPartition() : '',
        userAgent: ML_BROWSER_USER_AGENT
    });
    return worker;
}

async function prepararFavoritosWorkersPool(poolId, url = '') {
    if (
        favoritosWorkersPoolState.poolId === poolId
        && favoritosWorkersPoolState.preparation
    ) {
        return favoritosWorkersPoolState.preparation;
    }
    const preparation = Promise.resolve()
        .then(() => restaurarSessaoAvantProAntesDeAbrirNavegador('before-favoritos-workers-pool-start', {
            poolId,
            url
        }))
        .then(() => ensureChromeExtensionsForMlSession());
    favoritosWorkersPoolState.preparation = preparation;
    return preparation;
}

async function startFavoritosWorkerBrowser(targetUrl, parent = null, options = {}, workerId = FAVORITOS_WORKER_LEGACY_ID) {
    const id = assertFavoritosWorkerLegadoDisponivel(workerId);
    const record = obterRegistroFavoritosWorker(id, true);
    const generation = ++record.generation;
    const url = normalizeTargetUrl(targetUrl || 'https://www.mercadolivre.com.br/');
    const deferInitialNavigation = options.deferInitialNavigation === true;
    assertAllowedFavoritosWorkerUrl(url);
    record.lastUrl = url;
    favoritosEmbeddedMlLastUrl = url;

    const poolId = String(options.poolId || record.state.poolId || '').trim();
    if (record.workerId !== FAVORITOS_WORKER_LEGACY_ID && poolId) {
        await prepararFavoritosWorkersPool(poolId, url);
    } else {
        await restaurarSessaoAvantProAntesDeAbrirNavegador('before-favoritos-worker-browser-start', {
            url,
            workerId: record.workerId
        });
        assertFavoritosWorkerGeneration(record, generation);
        await ensureChromeExtensionsForMlSession();
    }
    assertFavoritosWorkerGeneration(record, generation);

    const worker = ensureFavoritosWorkerBrowser(parent, record.workerId);
    worker.__jkFavoritosWorkerGeneration = generation;
    assertFavoritosWorkerGeneration(record, generation, worker);
    record.state = {
        ...record.state,
        poolId,
        sku: String(options.sku || record.state.sku || '').trim(),
        attempt: Math.max(0, Number(options.attempt) || 0)
    };
    atualizarTituloFavoritosWorker(record);
    if (options.show !== false) {
        try {
            worker.setSkipTaskbar(false);
            if (!worker.isVisible()) {
                if (typeof worker.showInactive === 'function') worker.showInactive();
                else worker.show();
            }
        } catch (_err) {}
    }
    setFavoritosWorkerBrowserState(record.workerId, {
        active: true,
        paused: false,
        cancelRequested: false,
        visible: worker.isVisible(),
        status: deferInitialNavigation ? 'idle' : 'running',
        message: options.message || (deferInitialNavigation
            ? `Trabalhador ${record.workerId} aguardando SKU.`
            : `Trabalhador ${record.workerId} processando${record.state.sku ? ` SKU ${record.state.sku}` : ''}.`),
        url: deferInitialNavigation ? '' : url,
        poolId,
        sku: record.state.sku,
        attempt: record.state.attempt
    });

    const currentUrl = worker.webContents.getURL();
    let loadWarning = null;
    if (!deferInitialNavigation && normalizeComparableUrl(currentUrl) !== normalizeComparableUrl(url)) {
        const loadEventPromise = waitForWebContentsLoad(worker.webContents, 26000, url).catch(err => err);
        worker.webContents.loadURL(url).catch(err => {
            const warning = err && err.message ? err.message : String(err);
            if (isIgnorableNavigationAbort(null, warning)) {
                logElectronLifecycle('favoritos-worker-browser-load-aborted-ignored', { workerId: record.workerId, url, warning });
                return;
            }
            logElectronLifecycle('favoritos-worker-browser-load-start-warning', { workerId: record.workerId, url, warning });
        });
        const loadResult = await Promise.race([
            loadEventPromise,
            waitMs(22000).then(() => ({ timeout: true }))
        ]);
        assertFavoritosWorkerGeneration(record, generation, worker);
        if (loadResult instanceof Error) {
            loadWarning = loadResult.message || String(loadResult);
            if (isIgnorableNavigationAbort(null, loadWarning)) loadWarning = null;
        } else if (loadResult && loadResult.timeout) {
            loadWarning = 'timeout';
        }
    }

    assertFavoritosWorkerGeneration(record, generation, worker);
    const loadedUrl = deferInitialNavigation ? '' : (worker.webContents.getURL() || url);
    if (loadedUrl) record.lastUrl = loadedUrl;
    setFavoritosWorkerBrowserState(record.workerId, {
        active: true,
        status: deferInitialNavigation ? 'idle' : 'running',
        message: options.message || (deferInitialNavigation
            ? `Trabalhador ${record.workerId} aguardando SKU.`
            : `Trabalhador ${record.workerId} em execucao.`),
        url: loadedUrl
    });
    return {
        success: true,
        worker: true,
        workerId: record.workerId,
        poolId,
        background: true,
        url: loadedUrl,
        loadWarning
    };
}

async function stopFavoritosWorkerBrowser(options = {}, workerId = FAVORITOS_WORKER_LEGACY_ID) {
    const record = obterRegistroFavoritosWorker(workerId, true);
    const reason = options.reason || 'favoritos-worker-stop';
    const status = String(options.status || 'stopped').toLowerCase();
    if (status === 'canceled' || status === 'cancelled') {
        return cancelFavoritosWorkerBrowser({ ...options, reason, status: 'canceled' }, record.workerId);
    }
    const generation = record.generation;
    const worker = record.window;
    if (options.skipSessionSave !== true && record.workerId === FAVORITOS_WORKER_LEGACY_ID) {
        await salvarSessaoAvantProAntesDeOcultarNavegador(reason, {
            url: record.lastUrl,
            destroy: options.destroy !== false,
            workerId: record.workerId
        });
    }
    if (generation !== record.generation) return { success: true, worker: true, workerId: record.workerId, superseded: true };

    const shouldDestroy = options.destroy !== false;
    if (worker && !worker.isDestroyed() && worker.__jkFavoritosWorkerGeneration === generation) {
        if (shouldDestroy) {
            record.generation += 1;
            if (record.window === worker) record.window = null;
            worker.destroy();
        } else {
            worker.hide();
        }
    }
    const possuiMensagem = Object.prototype.hasOwnProperty.call(options, 'message');
    return setFavoritosWorkerBrowserState(record.workerId, {
        active: false,
        paused: false,
        cancelRequested: false,
        visible: false,
        status,
        message: possuiMensagem ? String(options.message || '') : `Trabalhador ${record.workerId} finalizado.`,
        url: record.lastUrl,
        sku: options.keepAssignment ? record.state.sku : '',
        attempt: options.keepAssignment ? record.state.attempt : 0
    }, options.error ? 'favoritos-worker:error' : 'favoritos-worker:done');
}

async function showFavoritosWorkerBrowser(workerId = FAVORITOS_WORKER_LEGACY_ID) {
    const record = obterRegistroFavoritosWorker(workerId, true);
    const worker = ensureFavoritosWorkerBrowser(mainWindow, record.workerId);
    worker.setSkipTaskbar(false);
    worker.show();
    worker.focus();
    return setFavoritosWorkerBrowserState(record.workerId, { visible: true, message: `Trabalhador ${record.workerId} visivel.` });
}

async function hideFavoritosWorkerBrowser(workerId = FAVORITOS_WORKER_LEGACY_ID) {
    const record = obterRegistroFavoritosWorker(workerId, true);
    if (record.window && !record.window.isDestroyed()) {
        record.window.hide();
        record.window.setSkipTaskbar(false);
    }
    return setFavoritosWorkerBrowserState(record.workerId, { visible: false, message: `Trabalhador ${record.workerId} oculto; coleta continua.` });
}

async function executeFavoritosWorkerBrowser(code, workerId = FAVORITOS_WORKER_LEGACY_ID) {
    const record = obterRegistroFavoritosWorker(workerId, true);
    if (record.state.cancelRequested || record.state.status === 'canceled') throw favoritosWorkerCanceledError(record.workerId);
    const worker = ensureFavoritosWorkerBrowser(mainWindow, record.workerId);
    const generation = record.generation;
    worker.__jkFavoritosWorkerGeneration = generation;
    assertFavoritosWorkerGeneration(record, generation, worker);
    const currentUrl = worker.webContents.getURL();
    assertAllowedFavoritosWorkerUrl(currentUrl);
    const script = String(code || '');
    if (script.length > FAVORITOS_WORKER_MAX_SCRIPT_LENGTH) {
        throw new Error('Script excede o limite permitido no navegador trabalhador do Favoritos.');
    }
    let timeoutId = null;
    try {
        const execution = Promise.resolve(worker.webContents.executeJavaScript(script, true));
        const timeout = new Promise((_resolve, reject) => {
            timeoutId = setTimeout(() => {
                const error = new Error(`O navegador trabalhador ${record.workerId} excedeu ${FAVORITOS_WORKER_EXECUTE_TIMEOUT_MS / 1000}s sem responder.`);
                error.name = 'TimeoutError';
                error.favoritosWorkerTimeout = true;
                reject(error);
            }, FAVORITOS_WORKER_EXECUTE_TIMEOUT_MS);
        });
        const result = await Promise.race([execution, timeout]);
        assertFavoritosWorkerGeneration(record, generation, worker);
        if (result && typeof result === 'object' && (
            Object.prototype.hasOwnProperty.call(result, 'total')
            || Object.prototype.hasOwnProperty.call(result, 'totalVisiveis')
            || Object.prototype.hasOwnProperty.call(result, 'debug')
            || Array.isArray(result.anuncios)
        )) {
            logElectronLifecycle('favoritos-worker-browser-execute-result', {
                workerId: record.workerId,
                sku: record.state.sku,
                url: worker.webContents.getURL() || currentUrl || '',
                total: Number(result.total ?? result.totalVisiveis ?? 0) || 0,
                anuncios: Array.isArray(result.anuncios) ? result.anuncios.length : 0,
                debug: result.debug || null
            });
        }
        return result;
    } catch (err) {
        let errorUrl = currentUrl || '';
        try {
            if (!worker.isDestroyed() && !worker.webContents.isDestroyed()) errorUrl = worker.webContents.getURL() || errorUrl;
        } catch (_urlErr) {}
        logElectronLifecycle('favoritos-worker-browser-execute-error', {
            workerId: record.workerId,
            sku: record.state.sku,
            url: errorUrl,
            error: err && err.message ? err.message : String(err)
        });
        if (generation !== record.generation || worker.isDestroyed() || worker.__jkFavoritosWorkerGeneration !== generation) {
            throw favoritosWorkerCanceledError(record.workerId);
        }
        throw err;
    } finally {
        if (timeoutId) clearTimeout(timeoutId);
    }
}

async function typeFavoritosWorkerBrowser(payload, workerId = FAVORITOS_WORKER_LEGACY_ID) {
    const record = obterRegistroFavoritosWorker(workerId, true);
    const worker = ensureFavoritosWorkerBrowser(mainWindow, record.workerId);
    const raw = payload && typeof payload === 'object' ? payload : { text: payload };
    const text = String(raw.text ?? raw.value ?? '');
    const clearFirst = raw.clearFirst !== false;
    const pressEnter = raw.pressEnter === true || raw.enter === true;
    const contents = worker.webContents;
    const tapKey = async (keyCode, modifiers = []) => {
        contents.sendInputEvent({ type: 'keyDown', keyCode, modifiers });
        await waitMs(25);
        contents.sendInputEvent({ type: 'keyUp', keyCode, modifiers });
    };
    try { contents.focus(); } catch (_err) {}
    if (clearFirst) {
        await tapKey('A', ['control']);
        await waitMs(20);
        await tapKey('Backspace');
    }
    if (text) {
        if (typeof contents.insertText === 'function') await Promise.resolve(contents.insertText(text));
        else {
            for (const ch of text) {
                contents.sendInputEvent({ type: 'char', keyCode: ch });
                await waitMs(2);
            }
        }
    }
    if (pressEnter) {
        await waitMs(80);
        await tapKey('Enter');
    }
    return { success: true, worker: true, workerId: record.workerId, typed: text.length, enter: pressEnter, url: contents.getURL() };
}

async function clickFavoritosWorkerBrowser(point, workerId = FAVORITOS_WORKER_LEGACY_ID) {
    const record = obterRegistroFavoritosWorker(workerId, true);
    const worker = ensureFavoritosWorkerBrowser(mainWindow, record.workerId);
    const raw = point || {};
    let x = Number(raw.x ?? raw.left);
    let y = Number(raw.y ?? raw.top);
    if (!Number.isFinite(x) || !Number.isFinite(y)) throw new Error('Coordenada invalida para clicar no navegador trabalhador do Favoritos.');
    const bounds = worker.getBounds ? worker.getBounds() : { width: 1280, height: 900 };
    x = Math.max(1, Math.min(Math.round(x), Number(bounds.width) > 1 ? Number(bounds.width) - 1 : 1279));
    y = Math.max(1, Math.min(Math.round(y), Number(bounds.height) > 1 ? Number(bounds.height) - 1 : 899));
    const clickCount = Math.max(1, Math.min(2, Math.round(Number(raw.clickCount) || 1)));
    const contents = worker.webContents;
    try { contents.focus(); } catch (_err) {}
    contents.sendInputEvent({ type: 'mouseMove', x, y, movementX: 0, movementY: 0 });
    contents.sendInputEvent({ type: 'mouseDown', x, y, button: 'left', clickCount });
    await waitMs(45);
    contents.sendInputEvent({ type: 'mouseUp', x, y, button: 'left', clickCount });
    return { success: true, worker: true, workerId: record.workerId, x, y, url: contents.getURL() };
}

function pauseFavoritosWorkerBrowser(workerId = FAVORITOS_WORKER_LEGACY_ID) {
    const record = obterRegistroFavoritosWorker(workerId, true);
    return setFavoritosWorkerBrowserState(record.workerId, { paused: true, status: 'paused', message: `Trabalhador ${record.workerId} pausado.` });
}

function resumeFavoritosWorkerBrowser(workerId = FAVORITOS_WORKER_LEGACY_ID) {
    const record = obterRegistroFavoritosWorker(workerId, true);
    return setFavoritosWorkerBrowserState(record.workerId, { paused: false, cancelRequested: false, status: 'running', message: `Trabalhador ${record.workerId} retomado.` });
}

async function cancelFavoritosWorkerBrowser(options = {}, workerId = FAVORITOS_WORKER_LEGACY_ID) {
    const record = obterRegistroFavoritosWorker(workerId, true);
    record.generation += 1;
    const worker = record.window;
    record.window = null;
    setFavoritosWorkerBrowserState(record.workerId, {
        active: false,
        cancelRequested: true,
        paused: false,
        visible: false,
        status: 'canceled',
        message: ''
    }, 'favoritos-worker:done');
    if (worker && !worker.isDestroyed() && worker.webContents && !worker.webContents.isDestroyed()) {
        try { worker.webContents.stop(); } catch (_err) {}
        try { worker.destroy(); } catch (_err) {}
    }
    if (options.skipSessionSave !== true && record.workerId === FAVORITOS_WORKER_LEGACY_ID) {
        await salvarSessaoAvantProAntesDeOcultarNavegador(
            options.reason || 'favoritos-cancelado-pelo-usuario',
            { url: record.lastUrl, destroy: true, canceled: true, workerId: record.workerId }
        ).catch(() => null);
    }
    return favoritosWorkerBrowserStatus(record.workerId);
}

async function startFavoritosWorkersPool(payload = {}, parent = null) {
    const size = Math.max(1, Math.min(FAVORITOS_WORKER_POOL_LIMIT, Math.floor(Number(payload.size) || 1)));
    if (favoritosWorkersPoolState.active) {
        await stopFavoritosWorkersPool({ destroy: true, reason: 'favoritos-pool-restart', message: '' });
    }
    let poolId = '';
    let initialUrl = '';
    let deferInitialNavigation = false;
    favoritosWorkerLegacyHandoffToPool = true;
    try {
        const legacyRecord = obterRegistroFavoritosWorker(FAVORITOS_WORKER_LEGACY_ID, false);
        if (legacyRecord && legacyRecord.window && !legacyRecord.window.isDestroyed()) {
            await stopFavoritosWorkerBrowser({
                destroy: true,
                reason: 'favoritos-worker-legacy-handoff-to-pool',
                message: ''
            }, FAVORITOS_WORKER_LEGACY_ID);
        }
        poolId = String(payload.poolId || favoritosWorkerPoolIdNovo()).trim();
        favoritosWorkersPoolFocusCursor = 0;
        initialUrl = normalizeTargetUrl(payload.initialUrl || 'https://www.mercadolivre.com.br/');
        deferInitialNavigation = payload.deferInitialNavigation === true;
        assertAllowedFavoritosWorkerUrl(initialUrl);
        favoritosWorkersPoolState = {
            active: true,
            poolId,
            status: 'running',
            visible: payload.visible !== false,
            paused: false,
            cancelRequested: false,
            startedAt: Number(payload.startedAt) || Date.now(),
            finishedAt: 0,
            message: String(payload.message || 'Pool do Favoritos iniciado.'),
            preparation: null
        };
    } finally {
        favoritosWorkerLegacyHandoffToPool = false;
    }
    await prepararFavoritosWorkersPool(poolId, initialUrl);
    const workers = await Promise.all(Array.from({ length: size }, (_item, offset) => {
        const index = offset + 1;
        return startFavoritosWorkerBrowser(initialUrl, parent, {
            poolId,
            show: payload.visible !== false,
            deferInitialNavigation,
            message: `Trabalhador ${index} aguardando SKU.`
        }, `w${index}`);
    }));
    for (let index = size + 1; index <= FAVORITOS_WORKER_POOL_LIMIT; index += 1) {
        const record = obterRegistroFavoritosWorker(`w${index}`, false);
        if (record && record.window && !record.window.isDestroyed()) {
            await stopFavoritosWorkerBrowser({ destroy: true, skipSessionSave: true, message: '' }, record.workerId);
        }
    }
    const status = favoritosWorkersPoolStatus({ workersStarted: workers.length });
    emitFavoritosWorkerEvent('favoritos-workers:progress', status);
    return status;
}

async function pauseFavoritosWorkersPool() {
    favoritosWorkersPoolState.paused = true;
    favoritosWorkersPoolState.status = 'paused';
    favoritosWorkersPoolState.message = 'Pool do Favoritos pausado.';
    for (const record of favoritosWorkerBrowsers.values()) {
        if (record.workerId !== FAVORITOS_WORKER_LEGACY_ID && record.state.active) pauseFavoritosWorkerBrowser(record.workerId);
    }
    const status = favoritosWorkersPoolStatus();
    emitFavoritosWorkerEvent('favoritos-workers:progress', status);
    return status;
}

async function resumeFavoritosWorkersPool() {
    favoritosWorkersPoolState.paused = false;
    favoritosWorkersPoolState.status = 'running';
    favoritosWorkersPoolState.message = 'Pool do Favoritos retomado.';
    for (const record of favoritosWorkerBrowsers.values()) {
        if (record.workerId !== FAVORITOS_WORKER_LEGACY_ID && record.state.active) resumeFavoritosWorkerBrowser(record.workerId);
    }
    const status = favoritosWorkersPoolStatus();
    emitFavoritosWorkerEvent('favoritos-workers:progress', status);
    return status;
}

async function showFavoritosWorkersPool() {
    favoritosWorkersPoolState.visible = true;
    const ativos = [...favoritosWorkerBrowsers.values()]
        .filter(record => record.workerId !== FAVORITOS_WORKER_LEGACY_ID && record.window && !record.window.isDestroyed());
    for (const record of ativos) {
        try {
            record.window.setSkipTaskbar(false);
            if (typeof record.window.showInactive === 'function') record.window.showInactive();
            else record.window.show();
        } catch (_err) {}
    }
    if (ativos.length) {
        const alvo = ativos[favoritosWorkersPoolFocusCursor % ativos.length];
        favoritosWorkersPoolFocusCursor = (favoritosWorkersPoolFocusCursor + 1) % ativos.length;
        try {
            if (alvo.window && !alvo.window.isDestroyed()) {
                alvo.window.show();
                alvo.window.focus();
            }
        } catch (_err) {}
    }
    return favoritosWorkersPoolStatus();
}

async function hideFavoritosWorkersPool() {
    favoritosWorkersPoolState.visible = false;
    for (const record of favoritosWorkerBrowsers.values()) {
        if (record.workerId === FAVORITOS_WORKER_LEGACY_ID || !record.window || record.window.isDestroyed()) continue;
        try { record.window.hide(); } catch (_err) {}
    }
    return favoritosWorkersPoolStatus();
}

async function stopFavoritosWorkersPool(options = {}) {
    const status = String(options.status || 'stopped').toLowerCase();
    const canceled = status === 'canceled' || status === 'cancelled';
    const keepWorkerId = options.keepWorkerId ? normalizarFavoritosWorkerId(options.keepWorkerId) : '';
    favoritosWorkersPoolState.active = false;
    favoritosWorkersPoolState.paused = false;
    favoritosWorkersPoolState.cancelRequested = canceled;
    favoritosWorkersPoolState.status = canceled ? 'canceled' : status;
    favoritosWorkersPoolState.finishedAt = Number(options.finishedAt) || Date.now();
    favoritosWorkersPoolState.message = Object.prototype.hasOwnProperty.call(options, 'message')
        ? String(options.message || '')
        : (canceled ? '' : 'Pool do Favoritos finalizado.');

    for (const record of favoritosWorkerBrowsers.values()) {
        if (record.workerId === FAVORITOS_WORKER_LEGACY_ID) continue;
        if (keepWorkerId && record.workerId === keepWorkerId) {
            try {
                if (record.window && !record.window.isDestroyed()) {
                    record.window.show();
                    record.window.focus();
                }
            } catch (_err) {}
            setFavoritosWorkerBrowserState(record.workerId, {
                active: false,
                paused: false,
                status: 'error',
                message: options.message || 'Corrija o acesso nesta janela e execute novamente.'
            }, 'favoritos-worker:error');
            continue;
        }
        if (canceled) {
            await cancelFavoritosWorkerBrowser({ ...options, skipSessionSave: true }, record.workerId);
        } else {
            await stopFavoritosWorkerBrowser({
                ...options,
                status,
                destroy: options.destroy !== false,
                skipSessionSave: true
            }, record.workerId);
        }
    }
    if (!keepWorkerId) {
        await salvarSessaoAvantProAntesDeOcultarNavegador(
            options.reason || 'favoritos-workers-pool-stop',
            {
                poolId: favoritosWorkersPoolState.poolId,
                destroy: options.destroy !== false,
                canceled
            }
        ).catch(() => null);
    }
    const result = favoritosWorkersPoolStatus();
    emitFavoritosWorkerEvent(canceled ? 'favoritos-workers:done' : 'favoritos-workers:done', result);
    favoritosWorkersPoolState.preparation = null;
    return result;
}
