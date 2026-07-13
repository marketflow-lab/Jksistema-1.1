let favoritosWorkerBrowserWindow = null;
let favoritosWorkerBrowserGeneration = 0;
const FAVORITOS_WORKER_MAX_SCRIPT_LENGTH = 1024 * 1024;
let favoritosWorkerBrowserState = {
    active: false,
    paused: false,
    cancelRequested: false,
    visible: false,
    status: 'idle',
    message: '',
    url: '',
    updatedAt: 0
};

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
    const data = favoritosWorkerBrowserStatus(payload);
    for (const win of BrowserWindow.getAllWindows()) {
        try {
            if (win && !win.isDestroyed() && win.webContents && !win.webContents.isDestroyed()) {
                win.webContents.send(channel, data);
            }
        } catch (_err) {}
    }
}

function favoritosWorkerBrowserStatus(extra = {}) {
    const windowAlive = !!(
        favoritosWorkerBrowserWindow &&
        !favoritosWorkerBrowserWindow.isDestroyed()
    );
    const visible = windowAlive && favoritosWorkerBrowserWindow.isVisible();
    let url = favoritosWorkerBrowserState.url || '';
    if (windowAlive) {
        try {
            url = favoritosWorkerBrowserWindow.webContents.getURL() || url;
        } catch (_err) {}
    }
    return {
        ...favoritosWorkerBrowserState,
        active: !!favoritosWorkerBrowserState.active,
        paused: !!favoritosWorkerBrowserState.paused,
        cancelRequested: !!favoritosWorkerBrowserState.cancelRequested,
        visible,
        hasWindow: windowAlive,
        url,
        ...extra
    };
}

function setFavoritosWorkerBrowserState(patch = {}, channel = 'favoritos-worker:progress') {
    favoritosWorkerBrowserState = {
        ...favoritosWorkerBrowserState,
        ...patch,
        updatedAt: Date.now()
    };
    emitFavoritosWorkerEvent(channel);
    return favoritosWorkerBrowserStatus();
}

function favoritosWorkerCanceledError() {
    const error = new Error('Navegador trabalhador do Favoritos cancelado.');
    error.name = 'AbortError';
    error.canceladoFavoritos = true;
    return error;
}

function assertFavoritosWorkerGeneration(generation, worker = null) {
    if (generation !== favoritosWorkerBrowserGeneration) throw favoritosWorkerCanceledError();
    if (worker && (worker.isDestroyed() || worker.__jkFavoritosWorkerGeneration !== generation)) {
        throw favoritosWorkerCanceledError();
    }
}

function ensureFavoritosWorkerBrowser(parent = null) {
    if (
        favoritosWorkerBrowserWindow &&
        !favoritosWorkerBrowserWindow.isDestroyed()
    ) {
        return favoritosWorkerBrowserWindow;
    }

    if (!favoritosWorkerBrowserGeneration) favoritosWorkerBrowserGeneration = 1;
    const worker = new BrowserWindow({
        width: 1280,
        height: 900,
        show: false,
        title: 'Favoritos ML - Navegador Trabalhador',
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

    worker.__jkFavoritosWorkerGeneration = favoritosWorkerBrowserGeneration;
    favoritosWorkerBrowserWindow = worker;
    worker.setMenuBarVisibility(false);
    try {
        worker.setAlwaysOnTop(false);
    } catch (_err) {}
    try {
        worker.webContents.setUserAgent(ML_BROWSER_USER_AGENT);
    } catch (_err) {}
    try {
        registerAvantProConsoleDiagnostics(worker.webContents);
    } catch (_err) {}
    try {
        registerEmbeddedMlBrowserDownloadGuard(worker.webContents);
    } catch (_err) {}

    worker.on('closed', () => {
        const generation = worker.__jkFavoritosWorkerGeneration;
        if (favoritosWorkerBrowserWindow === worker) favoritosWorkerBrowserWindow = null;
        if (generation !== favoritosWorkerBrowserGeneration) return;
        const cancelado = !!favoritosWorkerBrowserState.cancelRequested
            || favoritosWorkerBrowserState.status === 'canceled';
        setFavoritosWorkerBrowserState({
            active: false,
            paused: false,
            visible: false,
            status: cancelado ? 'canceled' : 'closed',
            message: cancelado ? '' : 'Navegador trabalhador do Favoritos fechado.'
        }, 'favoritos-worker:done');
    });
    worker.on('show', () => {
        if (worker !== favoritosWorkerBrowserWindow || worker.__jkFavoritosWorkerGeneration !== favoritosWorkerBrowserGeneration) return;
        setFavoritosWorkerBrowserState({ visible: true });
    });
    worker.on('hide', () => {
        if (worker !== favoritosWorkerBrowserWindow || worker.__jkFavoritosWorkerGeneration !== favoritosWorkerBrowserGeneration) return;
        setFavoritosWorkerBrowserState({ visible: false });
    });
    worker.webContents.on('did-start-loading', () => {
        if (worker !== favoritosWorkerBrowserWindow || worker.__jkFavoritosWorkerGeneration !== favoritosWorkerBrowserGeneration) return;
        if (favoritosWorkerBrowserState.cancelRequested || favoritosWorkerBrowserState.status === 'canceled') return;
        setFavoritosWorkerBrowserState({
            status: 'loading',
            message: 'Carregando Mercado Livre no navegador trabalhador.',
            url: !worker.isDestroyed()
                ? worker.webContents.getURL()
                : favoritosWorkerBrowserState.url
        });
    });
    worker.webContents.on('did-finish-load', () => {
        if (worker !== favoritosWorkerBrowserWindow || worker.__jkFavoritosWorkerGeneration !== favoritosWorkerBrowserGeneration) return;
        if (favoritosWorkerBrowserState.cancelRequested || favoritosWorkerBrowserState.status === 'canceled') return;
        setFavoritosWorkerBrowserState({
            status: 'running',
            message: 'Navegador trabalhador pronto.',
            url: !worker.isDestroyed()
                ? worker.webContents.getURL()
                : favoritosWorkerBrowserState.url
        });
    });
    worker.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
        if (worker !== favoritosWorkerBrowserWindow || worker.__jkFavoritosWorkerGeneration !== favoritosWorkerBrowserGeneration) return;
        if (favoritosWorkerBrowserState.cancelRequested || favoritosWorkerBrowserState.status === 'canceled') return;
        if (isIgnorableNavigationAbort(errorCode, errorDescription)) return;
        setFavoritosWorkerBrowserState({
            status: 'error',
            message: errorDescription || 'Falha ao carregar no navegador trabalhador.',
            url: validatedURL || favoritosWorkerBrowserState.url
        }, 'favoritos-worker:error');
    });
    worker.webContents.on('will-navigate', (event, targetUrl) => {
        if (isAllowedFavoritosWorkerUrl(targetUrl)) return;
        event.preventDefault();
        logElectronLifecycle('favoritos-worker-browser-navigation-blocked', { url: targetUrl });
    });

    logElectronLifecycle('favoritos-worker-browser-created', {
        partition: getBrowserSessionPartition ? getBrowserSessionPartition() : '',
        userAgent: ML_BROWSER_USER_AGENT
    });
    return worker;
}

async function startFavoritosWorkerBrowser(targetUrl, parent = null, options = {}) {
    const generation = ++favoritosWorkerBrowserGeneration;
    const url = normalizeTargetUrl(targetUrl || 'https://www.mercadolivre.com.br/');
    assertAllowedFavoritosWorkerUrl(url);
    favoritosEmbeddedMlLastUrl = url;
    await restaurarSessaoAvantProAntesDeAbrirNavegador('before-favoritos-worker-browser-start', { url });
    assertFavoritosWorkerGeneration(generation);
    await ensureChromeExtensionsForMlSession();
    assertFavoritosWorkerGeneration(generation);
    const worker = ensureFavoritosWorkerBrowser(parent);
    worker.__jkFavoritosWorkerGeneration = generation;
    assertFavoritosWorkerGeneration(generation, worker);
    if (options.show !== false) {
        try {
            worker.setSkipTaskbar(false);
            if (!worker.isVisible()) {
                if (typeof worker.showInactive === 'function') worker.showInactive();
                else worker.show();
            }
        } catch (_err) {}
    }
    setFavoritosWorkerBrowserState({
        active: true,
        paused: false,
        cancelRequested: false,
        status: 'running',
        message: options.message || 'Favoritos rodando em segundo plano.',
        url
    });

    const currentUrl = worker.webContents.getURL();
    let loadWarning = null;
    if (normalizeComparableUrl(currentUrl) !== normalizeComparableUrl(url)) {
        const loadEventPromise = waitForWebContentsLoad(worker.webContents, 26000, url).catch((err) => err);
        worker.webContents.loadURL(url).catch((err) => {
            const warning = err && err.message ? err.message : String(err);
            if (isIgnorableNavigationAbort(null, warning)) {
                logElectronLifecycle('favoritos-worker-browser-load-aborted-ignored', { url, warning });
                return;
            }
            logElectronLifecycle('favoritos-worker-browser-load-start-warning', { url, warning });
        });
        const loadResult = await Promise.race([
            loadEventPromise,
            waitMs(22000).then(() => ({ timeout: true }))
        ]);
        assertFavoritosWorkerGeneration(generation, worker);
        if (loadResult instanceof Error) {
            loadWarning = loadResult.message || String(loadResult);
            if (isIgnorableNavigationAbort(null, loadWarning)) {
                logElectronLifecycle('favoritos-worker-browser-load-warning-ignored', { url, warning: loadWarning });
                loadWarning = null;
            } else {
                logElectronLifecycle('favoritos-worker-browser-load-warning', { url, warning: loadWarning });
            }
        } else if (loadResult && loadResult.timeout) {
            loadWarning = 'timeout';
            logElectronLifecycle('favoritos-worker-browser-load-timeout', { url });
        }
    }

    assertFavoritosWorkerGeneration(generation, worker);
    const loadedUrl = worker.webContents.getURL() || url;
    setFavoritosWorkerBrowserState({
        active: true,
        status: 'running',
        message: options.message || 'Favoritos rodando em segundo plano.',
        url: loadedUrl
    });
    return { success: true, worker: true, background: true, url: loadedUrl, loadWarning };
}

async function stopFavoritosWorkerBrowser(options = {}) {
    const reason = options.reason || 'favoritos-worker-stop';
    const status = String(options.status || 'stopped').toLowerCase();
    if (status === 'canceled' || status === 'cancelled') {
        return cancelFavoritosWorkerBrowser({ ...options, reason, status: 'canceled' });
    }
    const generation = favoritosWorkerBrowserGeneration;
    const worker = favoritosWorkerBrowserWindow;
    await salvarSessaoAvantProAntesDeOcultarNavegador(reason, {
        url: favoritosEmbeddedMlLastUrl,
        destroy: options.destroy !== false
    });
    if (generation !== favoritosWorkerBrowserGeneration) {
        return { success: true, worker: true, superseded: true };
    }
    const shouldDestroy = options.destroy !== false;
    if (worker && !worker.isDestroyed() && worker.__jkFavoritosWorkerGeneration === generation) {
        if (shouldDestroy) {
            favoritosWorkerBrowserGeneration += 1;
            if (favoritosWorkerBrowserWindow === worker) favoritosWorkerBrowserWindow = null;
            worker.destroy();
        } else {
            worker.hide();
        }
    }
    if (shouldDestroy && favoritosWorkerBrowserWindow === worker) favoritosWorkerBrowserWindow = null;
    const possuiMensagem = Object.prototype.hasOwnProperty.call(options, 'message');
    setFavoritosWorkerBrowserState({
        active: false,
        paused: false,
        cancelRequested: false,
        visible: false,
        status,
        message: possuiMensagem ? String(options.message || '') : 'Favoritos finalizado.',
        url: favoritosEmbeddedMlLastUrl
    }, options.error ? 'favoritos-worker:error' : 'favoritos-worker:done');
    return { success: true, worker: true };
}

async function showFavoritosWorkerBrowser() {
    const worker = ensureFavoritosWorkerBrowser(mainWindow);
    worker.setSkipTaskbar(false);
    worker.show();
    worker.focus();
    return setFavoritosWorkerBrowserState({
        visible: true,
        message: 'Navegador trabalhador visivel.'
    });
}

async function hideFavoritosWorkerBrowser() {
    if (
        favoritosWorkerBrowserWindow &&
        !favoritosWorkerBrowserWindow.isDestroyed()
    ) {
        favoritosWorkerBrowserWindow.hide();
        favoritosWorkerBrowserWindow.setSkipTaskbar(false);
    }
    return setFavoritosWorkerBrowserState({
        visible: false,
        message: 'Navegador trabalhador oculto; coleta continua.'
    });
}

async function executeFavoritosWorkerBrowser(code) {
    if (favoritosWorkerBrowserState.cancelRequested || favoritosWorkerBrowserState.status === 'canceled') {
        throw favoritosWorkerCanceledError();
    }
    const worker = ensureFavoritosWorkerBrowser(mainWindow);
    const generation = favoritosWorkerBrowserGeneration;
    worker.__jkFavoritosWorkerGeneration = generation;
    assertFavoritosWorkerGeneration(generation, worker);
    const currentUrl = worker.webContents.getURL();
    assertAllowedFavoritosWorkerUrl(currentUrl);
    const script = String(code || '');
    if (script.length > FAVORITOS_WORKER_MAX_SCRIPT_LENGTH) {
        throw new Error('Script excede o limite permitido no navegador trabalhador do Favoritos.');
    }
    try {
        const result = await worker.webContents.executeJavaScript(script, true);
        assertFavoritosWorkerGeneration(generation, worker);
        if (
            result &&
            typeof result === 'object' &&
            (
                Object.prototype.hasOwnProperty.call(result, 'total') ||
                Object.prototype.hasOwnProperty.call(result, 'totalVisiveis') ||
                Object.prototype.hasOwnProperty.call(result, 'debug') ||
                Array.isArray(result.anuncios)
            )
        ) {
            logElectronLifecycle('favoritos-worker-browser-execute-result', {
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
            if (worker && !worker.isDestroyed() && worker.webContents && !worker.webContents.isDestroyed()) {
                errorUrl = worker.webContents.getURL() || errorUrl;
            }
        } catch (_urlErr) {}
        logElectronLifecycle('favoritos-worker-browser-execute-error', {
            url: errorUrl,
            error: err && err.message ? err.message : String(err)
        });
        if (
            generation !== favoritosWorkerBrowserGeneration
            || worker.isDestroyed()
            || worker.__jkFavoritosWorkerGeneration !== generation
        ) {
            throw favoritosWorkerCanceledError();
        }
        throw err;
    }
}

async function typeFavoritosWorkerBrowser(payload) {
    const worker = ensureFavoritosWorkerBrowser(mainWindow);
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
        if (typeof contents.insertText === 'function') {
            await Promise.resolve(contents.insertText(text));
        } else {
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
    return { success: true, worker: true, typed: text.length, enter: pressEnter, url: contents.getURL() };
}

async function clickFavoritosWorkerBrowser(point) {
    const worker = ensureFavoritosWorkerBrowser(mainWindow);
    const raw = point || {};
    let x = Number(raw.x ?? raw.left);
    let y = Number(raw.y ?? raw.top);
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
        throw new Error('Coordenada invalida para clicar no navegador trabalhador do Favoritos.');
    }
    const bounds = worker.getBounds ? worker.getBounds() : { width: 1280, height: 900 };
    const maxX = bounds && Number(bounds.width) > 1 ? Number(bounds.width) - 1 : 1279;
    const maxY = bounds && Number(bounds.height) > 1 ? Number(bounds.height) - 1 : 899;
    x = Math.max(1, Math.min(Math.round(x), maxX));
    y = Math.max(1, Math.min(Math.round(y), maxY));
    const clickCount = Math.max(1, Math.min(2, Math.round(Number(raw.clickCount) || 1)));
    const contents = worker.webContents;
    try { contents.focus(); } catch (_err) {}
    logElectronLifecycle('favoritos-worker-browser-native-click', {
        x,
        y,
        clickCount,
        source: raw.source || '',
        label: raw.label || '',
        url: contents.getURL()
    });
    contents.sendInputEvent({ type: 'mouseMove', x, y, movementX: 0, movementY: 0 });
    contents.sendInputEvent({ type: 'mouseDown', x, y, button: 'left', clickCount });
    await waitMs(45);
    contents.sendInputEvent({ type: 'mouseUp', x, y, button: 'left', clickCount });
    return { success: true, worker: true, x, y, url: contents.getURL() };
}

function pauseFavoritosWorkerBrowser() {
    return setFavoritosWorkerBrowserState({
        paused: true,
        status: 'paused',
        message: 'Favoritos pausado.'
    });
}

function resumeFavoritosWorkerBrowser() {
    return setFavoritosWorkerBrowserState({
        paused: false,
        cancelRequested: false,
        status: 'running',
        message: 'Favoritos retomado.'
    });
}

async function cancelFavoritosWorkerBrowser(options = {}) {
    if (
        favoritosWorkerBrowserState.status === 'canceled'
        && !favoritosWorkerBrowserState.active
        && (!favoritosWorkerBrowserWindow || favoritosWorkerBrowserWindow.isDestroyed())
    ) {
        favoritosWorkerBrowserGeneration += 1;
        return favoritosWorkerBrowserStatus();
    }
    favoritosWorkerBrowserGeneration += 1;
    const worker = favoritosWorkerBrowserWindow;
    if (favoritosWorkerBrowserWindow === worker) favoritosWorkerBrowserWindow = null;
    setFavoritosWorkerBrowserState({
        active: false,
        cancelRequested: true,
        paused: false,
        visible: false,
        status: 'canceled',
        message: ''
    }, 'favoritos-worker:done');
    if (worker && !worker.isDestroyed() && worker.webContents && !worker.webContents.isDestroyed()) {
        try {
            worker.webContents.stop();
        } catch (_err) {}
        try { worker.destroy(); } catch (_err) {}
    }
    await salvarSessaoAvantProAntesDeOcultarNavegador(
        options.reason || 'favoritos-cancelado-pelo-usuario',
        { url: favoritosEmbeddedMlLastUrl, destroy: true, canceled: true }
    ).catch(() => null);
    return favoritosWorkerBrowserStatus();
}
