function collectFrameTree(frame, output = []) {
    if (!frame) return output;
    output.push(frame);
    const children = Array.isArray(frame.frames) ? frame.frames : [];
    for (const child of children) {
        collectFrameTree(child, output);
    }
    return output;
}

function isAvantProAuthFrame(frame) {
    const url = String(frame && frame.url || '').toLowerCase();
    return url.includes('auth.avantprocloud.com.br') || (url.includes('avantprocloud') && url.includes('auth'));
}

function isIgnorableNavigationAbort(errorCode, message = '') {
    return Number(errorCode) === -3 || /\bERR_ABORTED\b|\(-3\)|loading 'https?:\/\//i.test(String(message || ''));
}

function isMercadoLivreAuthenticationFlowUrl(targetUrl) {
    const raw = String(targetUrl || '').trim();
    if (!raw) return false;
    try {
        const url = new URL(/^https?:\/\//i.test(raw) ? raw : `https://${raw}`);
        const host = String(url.hostname || '').toLowerCase();
        const mercadoLivreHost = host === 'mercadolivre.com'
            || host.endsWith('.mercadolivre.com')
            || host === 'mercadolivre.com.br'
            || host.endsWith('.mercadolivre.com.br')
            || host === 'mercadolibre.com'
            || host.endsWith('.mercadolibre.com');
        if (!mercadoLivreHost) return false;
        const pathname = String(url.pathname || '/').toLowerCase().replace(/\/{2,}/g, '/');
        const specificAuthRoute = /^\/gz\/account-verification(?:\/|$)|^\/jms\/[^/]+\/lgz(?:\/|$)|^\/password\/validation(?:\/|$)|^\/totp(?:\/|$)|^\/login\/challenges?(?:\/|$)/.test(pathname);
        const genericAuthHost = host === 'mercadolivre.com'
            || host === 'mercadolivre.com.br'
            || host === 'mercadolibre.com'
            || /^(?:www|auth|accounts?|account)\./.test(host);
        const genericAuthRoute = /^\/login(?:\/|$)|^\/(?:captcha|recaptcha|security[-_/]?check|identity[-_/]?verification)(?:\/|$)/.test(pathname);
        let negativeTraffic = false;
        let explicitAuthParam = false;
        for (const [name, value] of url.searchParams.entries()) {
            const key = String(name || '').toLowerCase();
            const itemValue = String(value || '').toLowerCase();
            if (key === 'logintype' && itemValue === 'negative_traffic') negativeTraffic = true;
            if (['captcha', 'recaptcha', 'security_check', 'identity_verification'].includes(key) && itemValue) {
                explicitAuthParam = true;
            }
        }
        return specificAuthRoute
            || negativeTraffic
            || (genericAuthHost && (genericAuthRoute || explicitAuthParam));
    } catch (_err) {
        return false;
    }
}

function mercadoLivreUrlSeguraParaLog(targetUrl) {
    try {
        const url = new URL(String(targetUrl || ''));
        return `${url.origin}${url.pathname}`;
    } catch (_err) {
        return '';
    }
}

function mercadoLivreMensagemSeguraParaLog(value) {
    return String(value || '').replace(/https?:\/\/[^\s'"<>]+/gi, (match) => (
        mercadoLivreUrlSeguraParaLog(match) || '[url-removida]'
    ));
}

function shouldPreserveMercadoLivreAuthenticationNavigation(currentUrl, requestedUrl) {
    const observed = String(currentUrl || '').trim();
    const requested = String(requestedUrl || '').trim();
    if (!observed || observed === 'about:blank' || !requested) return false;
    if (normalizeComparableUrl(observed) === normalizeComparableUrl(requested)) return false;
    return isMercadoLivreAuthenticationFlowUrl(observed)
        || isMercadoLivreAuthenticationFlowUrl(requested);
}

function shouldRememberMercadoLivreStableUrl(targetUrl) {
    const url = String(targetUrl || '').trim();
    return /^https?:\/\//i.test(url) && !isMercadoLivreAuthenticationFlowUrl(url);
}

function buildMlProductUrlFromItemId(itemId) {
    const cleanId = String(itemId || '').trim().toUpperCase().replace('-', '');
    if (!/^MLB\d+$/.test(cleanId)) return '';
    return `https://produto.mercadolivre.com.br/${cleanId.replace('MLB', 'MLB-')}-_JM`;
}

function pickMlItemImage(item = {}) {
    const direct = String(item.secure_thumbnail || item.thumbnail || item.thumbnail_url || '').trim();
    if (direct) return direct;
    const pictures = Array.isArray(item.pictures) ? item.pictures : [];
    for (const picture of pictures) {
        if (!picture || typeof picture !== 'object') continue;
        const url = String(picture.secure_url || picture.url || picture.thumbnail || '').trim();
        if (url) return url;
    }
    return '';
}

let favoritosEmbeddedMlLastUrl = 'https://www.mercadolivre.com.br/';
let favoritosEmbeddedMlLastAvantRestore = { url: '', at: 0 };
let authenticationQuitPersistenceReady = false;
let authenticationQuitPersistencePromise = null;

function isEmbeddedMlBrowserWebContentsIpc(contents) {
    return !!(
        contents &&
        embeddedMlBrowserView &&
        embeddedMlBrowserView.webContents &&
        !embeddedMlBrowserView.webContents.isDestroyed() &&
        contents === embeddedMlBrowserView.webContents
    );
}

var contextVaultSecurityModule = require(path.join(
    __dirname,
    'electron_app',
    'main',
    'modules',
    'context-vault-security.js'
));

function normalizeContextVaultFilePath(value) {
    const normalized = path.normalize(path.resolve(String(value || '')));
    return process.platform === 'win32' ? normalized.toLowerCase() : normalized;
}

function isAllowedContextVaultFrameUrl(frameUrl) {
    const raw = String(frameUrl || '').trim();
    if (!raw) return false;
    try {
        const parsed = new URL(raw);
        if (parsed.protocol === 'http:') {
            const hostAllowed = parsed.hostname === '127.0.0.1' || parsed.hostname === 'localhost';
            const portAllowed = String(parsed.port || '') === String(JK_LOCAL_BACKEND_PORT);
            const pageAllowed = /\/(?:static\/)?configuracoes\.html$/i.test(parsed.pathname || '');
            return hostAllowed && portAllowed && pageAllowed;
        }
        if (parsed.protocol === 'file:') {
            const { fileURLToPath } = require('url');
            const requestedPath = normalizeContextVaultFilePath(fileURLToPath(parsed));
            const allowedPaths = [
                path.join(getAppRootDir(), 'configuracoes.html'),
                path.join(getAppRootDir(), 'static', 'configuracoes.html')
            ].map(normalizeContextVaultFilePath);
            return allowedPaths.includes(requestedPath);
        }
    } catch (_err) {}
    return false;
}

function contextVaultFrameBelongsToSender(senderFrame, sender) {
    if (!senderFrame || !sender) return false;
    try {
        if (typeof senderFrame.isDestroyed === 'function' && senderFrame.isDestroyed()) return false;
        const topFrame = senderFrame.top;
        const mainFrame = sender.mainFrame;
        if (!topFrame || !mainFrame) return false;
        if (topFrame === mainFrame) return true;
        return Number.isInteger(topFrame.processId)
            && Number.isInteger(topFrame.routingId)
            && topFrame.processId === mainFrame.processId
            && topFrame.routingId === mainFrame.routingId;
    } catch (_err) {
        return false;
    }
}

function contextVaultFrameUrlForLog(frameUrl) {
    try {
        const parsed = new URL(String(frameUrl || ''));
        return `${parsed.protocol}//${parsed.host}${parsed.pathname}`;
    } catch (_err) {
        return '';
    }
}

function assertTrustedContextVaultIpcSender(event) {
    const sender = event && event.sender;
    const senderFrame = event && event.senderFrame;
    let frameUrl = '';
    try { frameUrl = String(senderFrame && senderFrame.url || ''); } catch (_err) {}
    const mainWebContents = mainWindow
        && !mainWindow.isDestroyed()
        && mainWindow.webContents
        && !mainWindow.webContents.isDestroyed()
        ? mainWindow.webContents
        : null;
    const trusted = !!(
        sender
        && mainWebContents
        && sender === mainWebContents
        && contextVaultFrameBelongsToSender(senderFrame, sender)
        && isAllowedContextVaultFrameUrl(frameUrl)
    );
    if (trusted) return;
    logElectronLifecycle('context-vault-ipc-sender-blocked', {
        frameUrl: contextVaultFrameUrlForLog(frameUrl),
        mainWebContents: !!mainWebContents,
        senderMatchesMain: !!(sender && mainWebContents && sender === mainWebContents)
    });
    throw new Error('Origem IPC nao autorizada para abrir o Context Vault.');
}

function getLocalBackendJson(pathname, authToken = '', timeoutMs = 18000) {
    return new Promise((resolve) => {
        const token = String(authToken || '').trim();
        if (!token || token.length > 16384) {
            resolve({ success: false, status: 401, message: 'Sessao administrativa ausente ou invalida.' });
            return;
        }
        const headers = {
            'Accept': 'application/json',
            'Authorization': token.toLowerCase().startsWith('bearer ') ? token : `Bearer ${token}`
        };
        const req = http.request({
            hostname: '127.0.0.1',
            port: JK_LOCAL_BACKEND_PORT,
            path: pathname,
            method: 'GET',
            headers,
            timeout: timeoutMs
        }, (res) => {
            let text = '';
            res.setEncoding('utf8');
            res.on('data', (chunk) => {
                if (text.length < 1024 * 1024) text += chunk;
            });
            res.on('end', () => {
                let data = {};
                try { data = text ? JSON.parse(text) : {}; } catch (_err) {}
                const ok = res.statusCode >= 200 && res.statusCode < 300;
                resolve(ok
                    ? { success: true, status: res.statusCode, data }
                    : {
                        success: false,
                        status: res.statusCode,
                        message: data.detail || data.message || `Backend retornou HTTP ${res.statusCode}.`
                    });
            });
        });
        req.on('timeout', () => {
            req.destroy();
            resolve({ success: false, message: 'Tempo esgotado ao validar o Context Vault.' });
        });
        req.on('error', (err) => {
            resolve({ success: false, message: err && err.message ? err.message : String(err) });
        });
        req.end();
    });
}

async function openContextVaultForAdmin(event, authToken = '') {
    assertTrustedContextVaultIpcSender(event);
    const status = await getLocalBackendJson('/api/admin/context-hub/status', authToken);
    if (!status.success) {
        throw new Error(status.status === 403
            ? 'Apenas administradores full podem abrir o Context Vault.'
            : (status.message || 'Nao foi possivel validar a permissao administrativa.'));
    }
    const clientId = status.data && status.data.client_id;
    const result = await contextVaultSecurityModule.openAuthorizedContextVault({
        runtimeDir: getLocalBackendRuntimeDir(),
        clientId,
        shell
    });
    logElectronLifecycle('context-vault-opened', {
        clientId: String(clientId || ''),
        method: result.method
    });
    return result;
}

function assertTrustedFavoritosIpcSender(event, channel = 'favoritos') {
    const sender = event && event.sender;
    const trusted = !!(
        sender
        && mainWindow
        && !mainWindow.isDestroyed()
        && mainWindow.webContents
        && !mainWindow.webContents.isDestroyed()
        && sender === mainWindow.webContents
    );
    if (trusted) return;
    let senderUrl = '';
    try { senderUrl = sender && !sender.isDestroyed() ? sender.getURL() : ''; } catch (_err) {}
    logElectronLifecycle('favoritos-ipc-sender-blocked', { channel, senderUrl });
    throw new Error('Origem IPC nao autorizada para o Favoritos.');
}

async function salvarSessaoAvantProAntesDeOcultarNavegador(reason = 'embedded-browser-hide', details = {}) {
    if (typeof saveAvantProExtensionStorageSnapshot !== 'function') {
        return null;
    }
    const resultado = await saveAvantProExtensionStorageSnapshot(reason, {
        source: 'embedded-ml-browser',
        ...details
    }).catch((err) => ({
        success: false,
        reason: 'snapshot-error',
        error: err && err.message ? err.message : String(err)
    }));
    logElectronLifecycle('avantpro-storage-snapshot-before-hide-result', {
        reason,
        success: !!(resultado && resultado.success),
        skipped: !!(resultado && resultado.skipped),
        snapshotReason: resultado && resultado.reason,
        copied: resultado && resultado.copied,
        error: resultado && resultado.error
    });
    return resultado;
}

async function restaurarSessaoAvantProAntesDeAbrirNavegador(reason = 'embedded-browser-show', details = {}) {
    if (typeof ensureAvantProExtensionStorageFromSnapshot !== 'function') {
        return null;
    }
    const targetUrl = String(details && details.url || '').split('#')[0].trim().toLowerCase();
    if (reason === 'before-embedded-ml-browser-show' && targetUrl) {
        const now = Date.now();
        if (
            favoritosEmbeddedMlLastAvantRestore.url === targetUrl
            && now - favoritosEmbeddedMlLastAvantRestore.at < 90000
        ) {
            const resultadoIgnorado = {
                success: false,
                skipped: true,
                reason: 'recent-restore-same-url'
            };
            logElectronLifecycle('avantpro-storage-restore-before-show-result', {
                reason,
                success: false,
                skipped: true,
                restoreReason: resultadoIgnorado.reason,
                copied: false,
                error: ''
            });
            return resultadoIgnorado;
        }
        favoritosEmbeddedMlLastAvantRestore = { url: targetUrl, at: now };
    }
    const resultado = await ensureAvantProExtensionStorageFromSnapshot(reason, {
        source: 'embedded-ml-browser',
        ...details
    }, {
        reloadExtensions: true
    }).catch((err) => ({
        success: false,
        reason: 'restore-error',
        error: err && err.message ? err.message : String(err)
    }));
    logElectronLifecycle('avantpro-storage-restore-before-show-result', {
        reason,
        success: !!(resultado && resultado.success),
        skipped: !!(resultado && resultado.skipped),
        restoreReason: resultado && resultado.reason,
        copied: resultado && resultado.copied,
        error: resultado && resultado.error
    });
    return resultado;
}

function nativeWindowHandlePayload(win) {
    if (!win || win.isDestroyed()) {
        return { success: false, message: 'Janela principal indisponivel.' };
    }
    let hwnd = '';
    try {
        const buffer = win.getNativeWindowHandle();
        if (process.platform === 'win32') {
            hwnd = buffer.length >= 8
                ? buffer.readBigUInt64LE(0).toString()
                : String(buffer.readUInt32LE(0));
        } else {
            hwnd = buffer.toString('hex');
        }
    } catch (err) {
        return { success: false, message: err && err.message ? err.message : 'Nao foi possivel obter a janela.' };
    }
    return {
        success: true,
        platform: process.platform,
        hwnd,
        bounds: win.getBounds(),
        contentBounds: win.getContentBounds()
    };
}

function ensureWindowReadyForNativeDock(win) {
    if (!win || win.isDestroyed()) return null;
    if (win.isMinimized()) {
        try { win.restore(); } catch (_err) {}
    }
    try { win.show(); } catch (_err) {}
    try { win.focus(); } catch (_err) {}
    try {
        const bounds = win.getBounds();
        if (bounds && (Number(bounds.x) <= -20000 || Number(bounds.y) <= -20000)) {
            win.setBounds({
                x: 80,
                y: 80,
                width: Math.max(1024, Math.round(Number(bounds.width) || 1280)),
                height: Math.max(720, Math.round(Number(bounds.height) || 800))
            });
        }
    } catch (_err) {}
    return win;
}

function postLocalBackendJson(pathname, payload, authToken = '', timeoutMs = 18000) {
    return new Promise((resolve) => {
        const body = JSON.stringify(payload || {});
        const headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Content-Length': Buffer.byteLength(body)
        };
        const token = String(authToken || '').trim();
        if (token) headers.Authorization = token.toLowerCase().startsWith('bearer ') ? token : `Bearer ${token}`;
        const req = http.request({
            hostname: '127.0.0.1',
            port: JK_LOCAL_BACKEND_PORT,
            path: pathname,
            method: 'POST',
            headers,
            timeout: timeoutMs
        }, (res) => {
            let text = '';
            res.setEncoding('utf8');
            res.on('data', (chunk) => { text += chunk; });
            res.on('end', () => {
                let data = {};
                try { data = text ? JSON.parse(text) : {}; } catch (_err) { data = { raw: text }; }
                if (res.statusCode < 200 || res.statusCode >= 300) {
                    resolve({
                        success: false,
                        status: res.statusCode,
                        message: data.detail || data.message || `Backend retornou HTTP ${res.statusCode}.`,
                        ...data
                    });
                    return;
                }
                resolve(data && typeof data === 'object' ? data : { success: true, data });
            });
        });
        req.on('timeout', () => {
            req.destroy();
            resolve({ success: false, message: 'Tempo esgotado ao chamar o backend local do Acesso Remoto.' });
        });
        req.on('error', (err) => {
            resolve({ success: false, message: err && err.message ? err.message : String(err) });
        });
        req.write(body);
        req.end();
    });
}

async function callRustDeskBackendFromElectron(event, route, payload = {}, authToken = '') {
    const parent = ensureWindowReadyForNativeDock(BrowserWindow.fromWebContents(event.sender) || mainWindow);
    const handle = nativeWindowHandlePayload(parent);
    if (!handle.success || !handle.hwnd) {
        return {
            success: false,
            docked: false,
            external: false,
            message: handle.message || 'Janela principal indisponivel para acoplar o Acesso Remoto.'
        };
    }
    const incomingPayload = payload && typeof payload === 'object' ? payload : {};
    const requestPayload = {
        ...incomingPayload,
        dock: true,
        parent_hwnd: handle.hwnd,
        page_visible: true
    };
    if (!requestPayload.parent_mode && !requestPayload.parentMode && !requestPayload.parentTarget) {
        requestPayload.parent_mode = 'window';
    }
    const result = await postLocalBackendJson(route, requestPayload, authToken);
    return {
        ...(result && typeof result === 'object' ? result : {}),
        electron_parent_hwnd: handle.hwnd,
        electron_window_bounds: handle.bounds,
        electron_content_bounds: handle.contentBounds
    };
}

async function getEmbeddedMlBrowserForIpc(event = null, options = {}) {
    const recreate = options.recreate !== false;
    if (embeddedMlBrowserView && embeddedMlBrowserView.webContents && !embeddedMlBrowserView.webContents.isDestroyed()) {
        return embeddedMlBrowserView;
    }
    if (!recreate) {
        throw new Error('Navegador do Mercado Livre indisponivel.');
    }
    const parent = (event && event.sender ? BrowserWindow.fromWebContents(event.sender) : null) || mainWindow;
    if (!parent || parent.isDestroyed()) {
        throw new Error('Janela principal indisponivel para recriar navegador do Mercado Livre.');
    }
    const view = ensureEmbeddedMlBrowser(parent, { attach: true });
    view.setBounds(normalizarBoundsNavegadorMl({
        background: true,
        width: 1280,
        height: 900
    }));
    const targetUrl = normalizeTargetUrl(favoritosEmbeddedMlLastUrl || 'https://www.mercadolivre.com.br/');
    const currentUrl = view.webContents.getURL();
    if (targetUrl && normalizeComparableUrl(currentUrl) !== normalizeComparableUrl(targetUrl)) {
        const loadEventPromise = waitForWebContentsLoad(view.webContents, 22000, targetUrl).catch((err) => err);
        view.webContents.loadURL(targetUrl).catch((err) => {
            const warning = err && err.message ? err.message : String(err);
            if (!isIgnorableNavigationAbort(null, warning)) {
                logElectronLifecycle('embedded-ml-browser-recreate-load-warning', {
                    url: mercadoLivreUrlSeguraParaLog(targetUrl),
                    warning: mercadoLivreMensagemSeguraParaLog(warning)
                });
            }
        });
        const loadResult = await Promise.race([
            loadEventPromise,
            waitMs(18000).then(() => ({ timeout: true }))
        ]);
        if (loadResult instanceof Error && !isIgnorableNavigationAbort(null, loadResult.message || String(loadResult))) {
            logElectronLifecycle('embedded-ml-browser-recreate-load-error', {
                url: mercadoLivreUrlSeguraParaLog(targetUrl),
                warning: mercadoLivreMensagemSeguraParaLog(loadResult.message || String(loadResult))
            });
        } else if (loadResult && loadResult.timeout) {
            logElectronLifecycle('embedded-ml-browser-recreate-load-timeout', {
                url: mercadoLivreUrlSeguraParaLog(targetUrl)
            });
        }
    }
    return view;
}

async function fillAvantProLoginInEmbeddedBrowser(email) {
    const view = await getEmbeddedMlBrowserForIpc(null);
    const cleanEmail = String(email || '').trim();
    if (!cleanEmail) {
        return { success: false, reason: 'email_indisponivel' };
    }
    const contents = view.webContents;
    const mainFrame = contents.mainFrame || null;
    const frames = collectFrameTree(mainFrame);
    const authFrames = frames.filter(isAvantProAuthFrame);
    const candidateFrames = [];
    const pushFrame = (frame) => {
        if (!frame || candidateFrames.includes(frame)) return;
        candidateFrames.push(frame);
    };
    authFrames.forEach(pushFrame);
    frames.forEach(pushFrame);
    const script = `
        (function () {
            var email = ${JSON.stringify(cleanEmail)};
            var normalizar = function (value) {
                var text = String(value || '').replace(/\\s+/g, ' ').trim();
                try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                return text.toLowerCase();
            };
            var isVisible = function (el) {
                if (!el || !el.getBoundingClientRect) return false;
                var rect = el.getBoundingClientRect();
                var style = window.getComputedStyle ? window.getComputedStyle(el) : null;
                return rect.width > 0 && rect.height > 0 && (!style || (style.display !== 'none' && style.visibility !== 'hidden'));
            };
            var queryAllDeep = function (selector, root) {
                var found = [];
                var seen = [];
                var walk = function (base) {
                    if (!base || seen.indexOf(base) >= 0) return;
                    seen.push(base);
                    try {
                        if (base.querySelectorAll) {
                            found = found.concat(Array.prototype.slice.call(base.querySelectorAll(selector)));
                        }
                    } catch (_err) {}
                    var nodes = [];
                    try {
                        nodes = base.querySelectorAll ? Array.prototype.slice.call(base.querySelectorAll('*')) : [];
                    } catch (_err2) {}
                    for (var i = 0; i < nodes.length; i += 1) {
                        if (nodes[i] && nodes[i].shadowRoot) walk(nodes[i].shadowRoot);
                    }
                };
                walk(root || document);
                return found.filter(function (node, index) { return found.indexOf(node) === index; });
            };
            var textoNode = function (node) {
                if (!node) return '';
                return [
                    node.innerText,
                    node.textContent,
                    node.value,
                    node.getAttribute && node.getAttribute('aria-label'),
                    node.getAttribute && node.getAttribute('title'),
                    node.getAttribute && node.getAttribute('placeholder'),
                    node.getAttribute && node.getAttribute('class'),
                    node.getAttribute && node.getAttribute('id')
                ].filter(Boolean).join(' ');
            };
            var contextoNode = function (node) {
                var root = null;
                try {
                    root = node && node.closest && node.closest('form, [role="dialog"], [aria-modal="true"], [class*="login"], [class*="auth"], [class*="avant"], [id*="avant"], [class*="modal"], [class*="popup"], [class*="drawer"]');
                } catch (_err) {}
                var host = null;
                try {
                    var nodeRoot = node && node.getRootNode ? node.getRootNode() : null;
                    host = nodeRoot && nodeRoot.host ? nodeRoot.host : null;
                } catch (_err2) {}
                root = root || node && node.parentElement || document.body;
                return normalizar([
                    textoNode(node),
                    root && textoNode(root),
                    root && (root.innerText || root.textContent),
                    host && textoNode(host),
                    host && (host.innerText || host.textContent),
                    document.title,
                    location.href
                ].filter(Boolean).join(' '));
            };
            var bodyBusca = normalizar(document.body && (document.body.innerText || document.body.textContent) || '');
            var inputs = queryAllDeep('input:not([type="hidden"])');
            var emailInput = inputs.find(function (input) {
                var attrs = normalizar([
                    input.type,
                    input.name,
                    input.id,
                    input.className,
                    input.placeholder,
                    input.getAttribute && input.getAttribute('aria-label'),
                    input.getAttribute && input.getAttribute('autocomplete')
                ].join(' '));
                var contexto = contextoNode(input);
                var pareceEmail = input.type === 'email' || /email|e-?mail|mail/.test(attrs + ' ' + contexto);
                var pareceAvant = /avant\\s*pro|avantpro|avantprocloud|iniciar\\s+sess[aã]o|insira\\s+suas\\s+credenciais|seu\\s+e-?mail|seu\\s+email|clique\\s+aqui|chame\\s+o\\s+suporte/.test(contexto + ' ' + bodyBusca);
                var pareceBuscaMl = /search|buscar|pesquisar|as_word|\\bq\\b/.test(attrs);
                return isVisible(input) && !input.disabled && !input.readOnly && pareceEmail && pareceAvant && !pareceBuscaMl;
            });
            if (!emailInput) {
                var acaoConta = queryAllDeep('button, a, input[type="button"], input[type="submit"], [role="button"], [tabindex]').find(function (button) {
                    if (!isVisible(button)) return false;
                    var label = normalizar(textoNode(button));
                    var contexto = contextoNode(button);
                    var alvo = label + ' ' + contexto;
                    return /vincular\s+(?:conta|agora)|conectar\s+conta|autorizar\s+(?:mercado\s+livre|conta)|permitir\s+acesso/.test(alvo)
                        && /avant\s*pro|avantpro|mercado\s+livre|conta|vincul/.test(alvo);
                });
                if (acaoConta) {
                    try {
                        var rectConta = acaoConta.getBoundingClientRect ? acaoConta.getBoundingClientRect() : null;
                        var optsConta = rectConta
                            ? { bubbles: true, cancelable: true, view: window, clientX: rectConta.left + rectConta.width / 2, clientY: rectConta.top + rectConta.height / 2 }
                            : { bubbles: true, cancelable: true, view: window };
                        acaoConta.dispatchEvent(new MouseEvent('mousedown', optsConta));
                        acaoConta.dispatchEvent(new MouseEvent('mouseup', optsConta));
                        acaoConta.dispatchEvent(new MouseEvent('click', optsConta));
                    } catch (_errConta) {}
                    try { acaoConta.click(); } catch (_errConta2) {}
                    return { success: false, reason: 'acao_conta_avant_clicada', clickedAccountLink: true, url: location.href };
                }
                return { success: false, reason: 'campo_email_avant_nao_encontrado', url: location.href };
            }
            var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
            if (setter && setter.set) setter.set.call(emailInput, email);
            else emailInput.value = email;
            emailInput.focus();
            emailInput.dataset.jkAvantEmailFilled = '1';
            emailInput.dispatchEvent(new Event('input', { bubbles: true }));
            emailInput.dispatchEvent(new Event('change', { bubbles: true }));
            var root = null;
            try {
                root = emailInput.closest('form, [role="dialog"], [aria-modal="true"], [class*="login"], [class*="auth"], [class*="avant"], [id*="avant"], [class*="modal"], [class*="popup"], [class*="drawer"]');
            } catch (_err) {}
            root = root || emailInput.form || document.body;
            var buttons = queryAllDeep('button, input[type="submit"], input[type="button"], [role="button"]', root);
            var submit = buttons.find(function (button) {
                if (!isVisible(button)) return false;
                var label = normalizar(textoNode(button));
                return /login|entrar|acessar|iniciar|continuar|enviar/.test(label);
            }) || (emailInput.form ? emailInput.form.querySelector('button, input[type="submit"]') : null);
            if (submit) {
                setTimeout(function () {
                    try {
                        var rect = submit.getBoundingClientRect ? submit.getBoundingClientRect() : null;
                        var opts = rect
                            ? { bubbles: true, cancelable: true, view: window, clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 }
                            : { bubbles: true, cancelable: true, view: window };
                        submit.dispatchEvent(new MouseEvent('mousedown', opts));
                        submit.dispatchEvent(new MouseEvent('mouseup', opts));
                        submit.dispatchEvent(new MouseEvent('click', opts));
                    } catch (_err) {}
                    try { submit.click(); } catch (_err2) {}
                    try { emailInput.form && emailInput.form.requestSubmit && emailInput.form.requestSubmit(); } catch (_err3) {}
                }, 180);
            }
            return {
                success: true,
                clicked: !!submit,
                emailValue: emailInput.value,
                url: location.href,
                title: document.title || ''
            };
        })();
    `;
    const attempts = [];
    for (const frame of candidateFrames) {
        try {
            const result = await frame.executeJavaScript(script, true);
            attempts.push({ url: frame.url, result });
            if (result && result.success) {
                logElectronLifecycle('avantpro-embedded-login-filled', {
                    frameUrl: frame.url,
                    clicked: !!result.clicked,
                    attemptCount: attempts.length
                });
                return {
                    ...result,
                    success: true,
                    frameUrl: frame.url,
                    attempts
                };
            }
        } catch (err) {
            attempts.push({
                url: frame.url,
                error: err && err.message ? err.message : String(err)
            });
        }
    }
    logElectronLifecycle('avantpro-embedded-login-not-filled', {
        reason: candidateFrames.length ? 'campo_email_avant_nao_encontrado' : 'iframe_auth_avant_nao_encontrado',
        frameCount: frames.length,
        authFrameCount: authFrames.length,
        attempts: attempts.map((attempt) => ({
            url: attempt.url,
            reason: attempt.result && attempt.result.reason,
            success: !!(attempt.result && attempt.result.success),
            error: attempt.error
        })).slice(0, 12)
    });
    return {
        success: false,
        reason: candidateFrames.length ? 'campo_email_avant_nao_encontrado' : 'iframe_auth_avant_nao_encontrado',
        frameUrls: frames.map(frame => String(frame && frame.url || '')).filter(Boolean).slice(0, 20),
        attempts
    };
}

app.whenReady().then(async () => {
    if (!JK_PRIMARY_INSTANCE_LOCK_ACQUIRED) return;
    recoverProfileIfStartupCrashed();
    markStartupIncomplete();
    if (process.env.JK_CLEAR_ELECTRON_CACHE === '1') {
        await clearElectronCache();
    } else {
        logElectronLifecycle('cache-retained');
    }
    const flushTimer = setInterval(() => {
        flushPersistentSessions().catch(() => {});
    }, 30000);
    if (typeof flushTimer.unref === 'function') flushTimer.unref();

    ensureChromeExtensionsForMlSession();
    configureNotificationPermissions();
    registerPersistentSessionDurability();
    const authSnapshotTimer = setInterval(() => {
        persistAuthenticationState('periodic-auth-snapshot', { saveAvantPro: false }).catch(() => {});
    }, 5 * 60 * 1000);
    if (typeof authSnapshotTimer.unref === 'function') authSnapshotTimer.unref();

    app.on('web-contents-created', (_event, contents) => {
        configureNotificationPermissionsForSession(contents && contents.session);
        if (contents && typeof contents.once === 'function') {
            contents.once('destroyed', () => releaseMlAutomationProtectionForContents(contents));
        }
        contents.on('will-navigate', (event, urlOrDetails) => {
            const url = getNavigationEventUrl(urlOrDetails);
            if (isMlAutomationProtected() && isMercadoLivreLogoutUrl(url)) {
                event.preventDefault();
                logElectronLifecycle('blocked-ml-logout-navigation-during-favoritos', { url: mercadoLivreUrlSeguraParaLog(url) });
                return;
            }
            if (isBlockedAutomationPopupUrl(url)) {
                event.preventDefault();
                logElectronLifecycle('blocked-automation-navigation', { url: mercadoLivreUrlSeguraParaLog(url) });
                return;
            }
            if (isOAuthExternalAuthUrl(url)) {
                event.preventDefault();
                openOAuthExternalAuthInChrome(url);
                return;
            }
            if (isMercadoLivreAdUrl(url) && !contents.__jkAllowMlAdNavigation) {
                event.preventDefault();
                openMercadoLivreAdInChrome(url);
                return;
            }
            if (!isAllowedNavigationUrl(url)) {
                event.preventDefault();
            }
        });

        contents.on('will-frame-navigate', (event, urlOrDetails, maybeDetails) => {
            const url = getNavigationEventUrl(urlOrDetails, maybeDetails);
            if (isEmbeddedMlBrowserWebContentsIpc(contents) && /^(blob|data):/i.test(String(url || '').trim())) {
                event.preventDefault();
                logElectronLifecycle('embedded-ml-browser-blob-navigation-blocked', { url: mercadoLivreUrlSeguraParaLog(url) });
                return;
            }
            if (isMlAutomationProtected() && isMercadoLivreLogoutUrl(url)) {
                event.preventDefault();
                logElectronLifecycle('blocked-ml-logout-frame-navigation-during-favoritos', { url: mercadoLivreUrlSeguraParaLog(url) });
                return;
            }
            if (isBlockedAutomationPopupUrl(url)) {
                event.preventDefault();
                logElectronLifecycle('blocked-automation-frame-navigation', { url: mercadoLivreUrlSeguraParaLog(url) });
                return;
            }
            if (isOAuthExternalAuthUrl(url)) {
                event.preventDefault();
                openOAuthExternalAuthInChrome(url);
                return;
            }
            if (!isAllowedNavigationUrl(url)) {
                event.preventDefault();
            }
        });

        if (typeof contents.setWindowOpenHandler === 'function') {
            contents.setWindowOpenHandler(({ url }) => {
                if (isEmbeddedMlBrowserWebContentsIpc(contents) && /^(blob|data):/i.test(String(url || '').trim())) {
                    logElectronLifecycle('embedded-ml-browser-blob-popup-blocked', { url: mercadoLivreUrlSeguraParaLog(url) });
                    return { action: 'deny' };
                }
                if (isMlAutomationProtected() && isMercadoLivreLogoutUrl(url)) {
                    logElectronLifecycle('blocked-ml-logout-popup-during-favoritos', { url: mercadoLivreUrlSeguraParaLog(url) });
                    return { action: 'deny' };
                }
                if (isBlockedAutomationPopupUrl(url)) {
                    logElectronLifecycle('blocked-automation-popup', { url: mercadoLivreUrlSeguraParaLog(url) });
                    return { action: 'deny' };
                }
                if (isOAuthExternalAuthUrl(url)) {
                    openOAuthExternalAuthInChrome(url);
                    return { action: 'deny' };
                }
                if (isMercadoLivreAdUrl(url) && !contents.__jkAllowMlAdNavigation) {
                    openMercadoLivreAdInChrome(url);
                    return { action: 'deny' };
                }
                if (shouldUseMlSessionForPopup(contents, url)) {
                    logElectronLifecycle('ml-login-popup-allowed-with-session', { url: mercadoLivreUrlSeguraParaLog(url) });
                    return {
                        action: 'allow',
                        overrideBrowserWindowOptions: mlPopupWindowOptions(contents)
                    };
                }
                return isAllowedNavigationUrl(url) ? { action: 'allow' } : { action: 'deny' };
            });
        }
        contents.on('did-create-window', (win, details) => {
            const url = details && details.url ? details.url : '';
            if (shouldUseMlSessionForPopup(contents, url)) {
                configureMlPopupWindow(win, details || {});
            }
        });
        const observeMercadoLivreAuthenticationNavigation = (_navigationEvent, targetUrl) => {
            const url = String(targetUrl || '');
            if (!isMercadoLivreUrl(url)) return;
            const authenticationFlow = isMercadoLivreAuthenticationFlowUrl(url);
            const previousAuthenticationFlow = contents.__jkMercadoLivreAuthenticationFlow === true;
            contents.__jkMercadoLivreAuthenticationFlow = authenticationFlow;
            if (previousAuthenticationFlow && !authenticationFlow) {
                persistAuthenticationState('mercado-livre-auth-flow-completed', {
                    saveAvantPro: false
                }).catch((err) => {
                    logElectronLifecycle('mercado-livre-auth-persistence-failed', {
                        url: mercadoLivreUrlSeguraParaLog(url),
                        error: err && err.message ? err.message : String(err)
                    });
                });
            }
        };
        contents.on('did-navigate', observeMercadoLivreAuthenticationNavigation);
        contents.on('did-navigate-in-page', observeMercadoLivreAuthenticationNavigation);
    });

    ipcMain.handle('get-mac', () => {
        return getMacAddress();
    });
    ipcMain.handle('get-app-version', () => {
        return app.getVersion();
    });
    ipcMain.handle('get-native-window-handle', (event) => {
        return nativeWindowHandlePayload(BrowserWindow.fromWebContents(event.sender) || mainWindow);
    });
    ipcMain.handle('rustdesk-open-in-app', async (event, payload = {}, authToken = '') => {
        return await callRustDeskBackendFromElectron(event, '/api/sala-reuniao/rustdesk/abrir', payload, authToken);
    });
    ipcMain.handle('rustdesk-dock-in-app', async (event, payload = {}, authToken = '') => {
        return await callRustDeskBackendFromElectron(event, '/api/sala-reuniao/rustdesk/acoplar', payload, authToken);
    });
    ipcMain.handle('rustdesk-hide-in-app', async (event, payload = {}, authToken = '') => {
        return await callRustDeskBackendFromElectron(event, '/api/sala-reuniao/rustdesk/ocultar', payload, authToken);
    });
    ipcMain.handle('get-browser-session-partition', () => {
        return getBrowserSessionPartition();
    });
    ipcMain.handle('ensure-browser-extensions', async () => {
        await ensureChromeExtensionsForMlSession();
        return {
            success: true,
            extensions: getLoadedChromeExtensionsForMlSession(),
            settings: loadBrowserExtensionsConfig()
        };
    });
    ipcMain.handle('get-browser-extension-settings', async () => {
        return {
            success: true,
            ...getBrowserExtensionSettingsSnapshot()
        };
    });
    ipcMain.handle('set-avantpro-extension-enabled', async (_event, enabled) => {
        return {
            success: true,
            ...(await applyAvantProExtensionSetting(!!enabled))
        };
    });
    ipcMain.handle('avantpro-storage-status', async () => {
        if (typeof getAvantProStorageSnapshotStatus !== 'function') {
            return { success: false, message: 'Diagnostico de sessao Avant Pro indisponivel.' };
        }
        return getAvantProStorageSnapshotStatus();
    });
    ipcMain.handle('avantpro-storage-save-current', async (_event, reason = 'manual', details = {}) => {
        if (typeof saveAvantProExtensionStorageSnapshot !== 'function') {
            return { success: false, message: 'Salvamento de sessao Avant Pro indisponivel.' };
        }
        return await saveAvantProExtensionStorageSnapshot(reason || 'manual', details || {});
    });
    ipcMain.handle('avantpro-storage-restore-last-good', async (_event, reason = 'manual', details = {}, options = {}) => {
        if (typeof restoreAvantProExtensionStorageSnapshot !== 'function') {
            return { success: false, message: 'Restauracao de sessao Avant Pro indisponivel.' };
        }
        return await restoreAvantProExtensionStorageSnapshot(reason || 'manual', details || {}, options || {});
    });
    ipcMain.handle('flush-browser-session', async () => {
        return await persistAuthenticationState('renderer-flush-browser-session');
    });
    ipcMain.handle('choose-display-media-source', async (event) => {
        const sourceUrl = event && event.senderFrame && event.senderFrame.url
            ? event.senderFrame.url
            : (event && event.sender && typeof event.sender.getURL === 'function' ? event.sender.getURL() : '');
        const isLocalShell = !sourceUrl || /^file:/i.test(String(sourceUrl || ''));
        if (!isLocalShell && !isAllowedMediaPermissionUrl(sourceUrl)) {
            return { success: false, message: 'Origem sem permissao para compartilhar tela.' };
        }
        try {
            const sources = await desktopCapturer.getSources({
                types: ['screen', 'window'],
                thumbnailSize: { width: 0, height: 0 }
            });
            const source = await chooseDisplayMediaSource(sources);
            return source
                ? { success: true, source: publicDisplayMediaSource(source) }
                : { success: false, canceled: true };
        } catch (err) {
            return { success: false, message: err && err.message ? err.message : 'Nao foi possivel listar telas.' };
        }
    });
    ipcMain.handle('set-ml-automation-active', async (event, active, reason) => {
        if (active) {
            await flushPersistentSessions();
        }
        const result = setMlAutomationProtection(event.sender, !!active, reason);
        if (!active) {
            await persistAuthenticationState('mercado-livre-automation-finished');
            maybeInstallDeferredUpdate();
        }
        return result;
    });
    ipcMain.handle('show-windows-notification', (_event, payload) => {
        return showWindowsNotification(payload || {});
    });
    ipcMain.handle('get-machine-info', () => {
        return getMachineInfo();
    });
    ipcMain.handle('context-vault-open', async (event, authToken = '') => {
        return await openContextVaultForAdmin(event, authToken);
    });
    ipcMain.handle('check-for-updates', async () => {
        return await checkForUpdates(true);
    });
    ipcMain.handle('install-update-now', async () => {
        return await installUpdateNow();
    });
    ipcMain.handle('get-client-config', () => {
        const config = loadClientConfig();
        return {
            appUrl: config.appUrl,
            configPath: config.configPath,
            needsSetup: false
        };
    });
    ipcMain.handle('save-client-config', async (event, appUrl) => {
        const saved = saveClientAppUrl(appUrl);
        const win = BrowserWindow.fromWebContents(event.sender) || mainWindow;
        setTimeout(() => {
            if (win && !win.isDestroyed()) {
                loadConfiguredApp(win, saved);
            }
        }, 120);
        return { success: true, ...saved };
    });
    ipcMain.handle('ml-public-item-info', async (_event, itemId) => {
        const cleanId = String(itemId || '').trim().toUpperCase();
        if (!/^MLB\d+$/.test(cleanId)) {
            throw new Error('ID de anÃƒÂºncio invÃƒÂ¡lido.');
        }
        const cached = _cacheGet(mlItemInfoCache, cleanId);
        if (cached) {
            return await cached;
        }

    const requestPromise = (async () => {
            const item = await netJsonGet(`https://api.mercadolibre.com/items/${encodeURIComponent(cleanId)}`);
                const permalink = String(item.permalink || item.url || item.link || '').trim() || buildMlProductUrlFromItemId(item.id || cleanId);
                const imagem = pickMlItemImage(item);
                const seller = item && item.seller && typeof item.seller === 'object' ? item.seller : {};
                const officialStore = item && item.official_store && typeof item.official_store === 'object' ? item.official_store : {};
                let vendedor = normalizeMlText(
                    item.seller_name ||
                    item.official_store_name ||
                    seller.nickname ||
                    officialStore.name ||
                    officialStore.nickname ||
                    ''
                );
                const sellerId = item.seller_id || seller.id || officialStore.seller_id || null;
                if (sellerId) {
                    try {
                        const user = await netJsonGet(`https://api.mercadolibre.com/users/${encodeURIComponent(sellerId)}`);
                        const nomeUsuario = [
                            user.nickname,
                            user.official_store_name,
                            user.official_store && user.official_store.name
                        ].find(Boolean);
                        if (normalizeMlText(nomeUsuario)) {
                            vendedor = normalizeMlText(nomeUsuario);
                        }
                    } catch (err) {
                    console.warn('Falha ao consultar vendedor ML:', err.message || err);
                }
            }
            const salePrice = item && typeof item.sale_price === 'object' && item.sale_price ? item.sale_price : {};
            const saleAmount = salePrice.amount ?? salePrice.price ?? (item && typeof item.sale_price !== 'object' ? item.sale_price : '');
            const regularAmount = salePrice.regular_amount ?? item.original_price ?? '';
            return {
                id: item.id || cleanId,
                titulo: item.title || '',
                url: permalink,
                permalink,
                link: permalink,
                imagem,
                thumbnail: imagem,
                pictures: Array.isArray(item.pictures) ? item.pictures : [],
                preco: item.price ?? '',
                price: item.price ?? '',
                preco_original: item.original_price ?? regularAmount ?? '',
                original_price: item.original_price ?? regularAmount ?? '',
                standard_price: regularAmount || item.original_price || item.price || '',
                preco_promocional: saleAmount || (item.original_price && item.price && Number(item.original_price) > Number(item.price) ? item.price : ''),
                promotional_price: saleAmount || (item.original_price && item.price && Number(item.original_price) > Number(item.price) ? item.price : ''),
                moeda: item.currency_id || 'BRL',
                currency_id: item.currency_id || 'BRL',
                data_criacao: item.date_created || item.start_time || '',
                vendedor,
                seller_id: sellerId || null,
                listing_type_id: item.listing_type_id || '',
                listing_type_name: item.listing_type_id === 'gold_pro' ? 'Premium' : (item.listing_type_id ? 'Classico' : ''),
                tipo_anuncio: item.listing_type_id === 'gold_pro' ? 'Premium' : (item.listing_type_id ? 'Classico' : ''),
                shipping: item.shipping || null,
                logistic_type: item.shipping && item.shipping.logistic_type || '',
                shipping_mode: item.shipping && item.shipping.mode || '',
                is_full: String(item.shipping && item.shipping.logistic_type || '').toLowerCase() === 'fulfillment',
                vendas: parseMlQuantidade(item.sold_quantity ?? item.sold ?? item.soldQuantity),
                source: 'api'
            };
        })();

        _cacheSet(mlItemInfoCache, cleanId, requestPromise);
        try {
            return await requestPromise;
        } catch (err) {
            mlItemInfoCache.delete(cleanId);
            throw err;
        }
    });
    ipcMain.handle('ml-browser-item-info', async (_event, itemId, targetUrl) => {
        const cleanId = String(itemId || '').trim().toUpperCase().replace('-', '');
        if (!/^MLB\d+$/.test(cleanId)) {
            throw new Error('ID de anÃƒÂºncio invÃƒÂ¡lido.');
        }
        const cacheKey = `browser:${cleanId}:${String(targetUrl || '').trim()}`;
        const cached = _cacheGet(mlItemInfoCache, cacheKey);
        if (cached) {
            return await cached;
        }

        const requestPromise = extractMlInfoByBrowser(cleanId, targetUrl);
        _cacheSet(mlItemInfoCache, cacheKey, requestPromise);
        try {
            return await requestPromise;
        } catch (err) {
            mlItemInfoCache.delete(cacheKey);
            throw err;
        }
    });
    ipcMain.handle('embedded-ml-browser-show', async (event, targetUrl, bounds) => {
        assertTrustedFavoritosIpcSender(event, 'embedded-ml-browser-show');
        const url = normalizeTargetUrl(targetUrl);
        const safeUrl = mercadoLivreUrlSeguraParaLog(url);
        const requestedAuthFlow = isMercadoLivreAuthenticationFlowUrl(url);
        if (isMlAutomationProtected() && isMercadoLivreLogoutUrl(url)) {
            logElectronLifecycle('blocked-embedded-ml-logout-load-during-favoritos', { url: safeUrl });
            return {
                success: false,
                blocked: true,
                reason: 'favoritos-em-execucao',
                url: embeddedMlBrowserView && !embeddedMlBrowserView.webContents.isDestroyed()
                    ? embeddedMlBrowserView.webContents.getURL()
                    : ''
            };
        }
        await restaurarSessaoAvantProAntesDeAbrirNavegador('before-embedded-ml-browser-show', { url: safeUrl });
        await ensureChromeExtensionsForMlSession();
        const parent = BrowserWindow.fromWebContents(event.sender) || mainWindow;
        const background = !!(bounds && bounds.background);
        const view = ensureEmbeddedMlBrowser(parent, { attach: true });
        view.setBounds(normalizarBoundsNavegadorMl(background ? { ...bounds, background: true } : bounds));
        let currentUrl = view.webContents.getURL();
        if (shouldRememberMercadoLivreStableUrl(currentUrl)) favoritosEmbeddedMlLastUrl = currentUrl;
        const devePreservarNavegacaoAutenticacao = observedUrl => (
            shouldPreserveMercadoLivreAuthenticationNavigation(observedUrl, url)
        );
        const preservarNavegacaoAutenticacao = (observedUrl) => {
            const currentAuthFlow = isMercadoLivreAuthenticationFlowUrl(observedUrl);
            if (shouldRememberMercadoLivreStableUrl(observedUrl)) {
                favoritosEmbeddedMlLastUrl = observedUrl;
            }
            logElectronLifecycle('embedded-ml-browser-auth-flow-preserved', {
                currentUrl: mercadoLivreUrlSeguraParaLog(observedUrl),
                requestedUrl: mercadoLivreUrlSeguraParaLog(url),
                currentAuthFlow,
                requestedAuthFlow
            });
            return {
                success: true,
                url: observedUrl,
                authFlow: currentAuthFlow,
                preservedAuthFlow: true,
                loadWarning: null
            };
        };
        if (devePreservarNavegacaoAutenticacao(currentUrl)) {
            return preservarNavegacaoAutenticacao(currentUrl);
        }
        let loadWarning = null;
        if (normalizeComparableUrl(currentUrl) !== normalizeComparableUrl(url)) {
            const latestUrl = view.webContents.getURL() || currentUrl;
            if (devePreservarNavegacaoAutenticacao(latestUrl)) {
                return preservarNavegacaoAutenticacao(latestUrl);
            }
            currentUrl = latestUrl;
            if (normalizeComparableUrl(currentUrl) !== normalizeComparableUrl(url)) {
                const loadTimeoutMs = background ? 22000 : 14000;
                const loadEventPromise = waitForWebContentsLoad(view.webContents, loadTimeoutMs, url).catch((err) => err);
                view.webContents.loadURL(url).catch((err) => {
                    const warning = err && err.message ? err.message : String(err);
                    if (isIgnorableNavigationAbort(null, warning)) {
                        logElectronLifecycle('embedded-ml-browser-load-aborted-ignored', {
                            url: safeUrl,
                            warning: mercadoLivreMensagemSeguraParaLog(warning)
                        });
                        return;
                    }
                    logElectronLifecycle('embedded-ml-browser-load-start-warning', {
                        url: safeUrl,
                        warning: mercadoLivreMensagemSeguraParaLog(warning)
                    });
                });
                const loadResult = await Promise.race([
                    loadEventPromise,
                    waitMs(background ? 18000 : 9000).then(() => ({ timeout: true }))
                ]);
                if (loadResult instanceof Error) {
                    loadWarning = loadResult.message || String(loadResult);
                    if (isIgnorableNavigationAbort(null, loadWarning)) {
                        logElectronLifecycle('embedded-ml-browser-show-load-aborted-ignored', {
                            url: safeUrl,
                            warning: mercadoLivreMensagemSeguraParaLog(loadWarning)
                        });
                        loadWarning = null;
                    } else {
                        logElectronLifecycle('embedded-ml-browser-show-load-warning', {
                            url: safeUrl,
                            warning: mercadoLivreMensagemSeguraParaLog(loadWarning)
                        });
                    }
                } else if (loadResult && loadResult.timeout) {
                    loadWarning = 'timeout';
                    logElectronLifecycle('embedded-ml-browser-show-load-timeout', { url: safeUrl });
                }
            }
        }
        const loadedUrl = view.webContents.getURL() || url;
        if (shouldRememberMercadoLivreStableUrl(loadedUrl)) {
            favoritosEmbeddedMlLastUrl = loadedUrl;
        }
        if (
            loadWarning
            && normalizeComparableUrl(loadedUrl) === normalizeComparableUrl(url)
        ) {
            logElectronLifecycle('embedded-ml-browser-show-load-warning-cleared', {
                url: safeUrl,
                loadedUrl: mercadoLivreUrlSeguraParaLog(loadedUrl),
                warning: mercadoLivreMensagemSeguraParaLog(loadWarning)
            });
            loadWarning = null;
        }
        logElectronLifecycle('embedded-ml-browser-avant-monitoring-disabled', {
            url: mercadoLivreUrlSeguraParaLog(loadedUrl)
        });
        return {
            success: true,
            url: loadedUrl,
            authFlow: isMercadoLivreAuthenticationFlowUrl(loadedUrl),
            loadWarning
        };
    });
    ipcMain.handle('favoritos-job-browser-start', async (event, targetUrl) => {
        assertTrustedFavoritosIpcSender(event, 'favoritos-job-browser-start');
        const parent = BrowserWindow.fromWebContents(event.sender) || mainWindow;
        return startFavoritosWorkerBrowser(targetUrl || 'https://www.mercadolivre.com.br/', parent, {
            message: 'Favoritos rodando em segundo plano.'
        });
    });
    ipcMain.handle('favoritos-job-browser-stop', async (event) => {
        assertTrustedFavoritosIpcSender(event, 'favoritos-job-browser-stop');
        return stopFavoritosWorkerBrowser({
            destroy: true,
            reason: 'favoritos-job-browser-stop',
            message: 'Favoritos finalizado.'
        });
    });
    ipcMain.handle('favoritos-worker:start', async (event, targetUrl, workerId = FAVORITOS_WORKER_LEGACY_ID, options = {}) => {
        assertTrustedFavoritosIpcSender(event, 'favoritos-worker:start');
        const parent = BrowserWindow.fromWebContents(event.sender) || mainWindow;
        return startFavoritosWorkerBrowser(targetUrl || 'https://www.mercadolivre.com.br/', parent, {
            ...(options || {}),
            message: options && options.message || 'Favoritos rodando em segundo plano.'
        }, workerId);
    });
    ipcMain.handle('favoritos-worker:pause', async (event, workerId = FAVORITOS_WORKER_LEGACY_ID) => {
        assertTrustedFavoritosIpcSender(event, 'favoritos-worker:pause');
        return pauseFavoritosWorkerBrowser(workerId);
    });
    ipcMain.handle('favoritos-worker:resume', async (event, workerId = FAVORITOS_WORKER_LEGACY_ID) => {
        assertTrustedFavoritosIpcSender(event, 'favoritos-worker:resume');
        return resumeFavoritosWorkerBrowser(workerId);
    });
    ipcMain.handle('favoritos-worker:cancel', async (event, workerId = FAVORITOS_WORKER_LEGACY_ID) => {
        assertTrustedFavoritosIpcSender(event, 'favoritos-worker:cancel');
        return cancelFavoritosWorkerBrowser({}, workerId);
    });
    ipcMain.handle('favoritos-worker:status', async (event, workerId = FAVORITOS_WORKER_LEGACY_ID) => {
        assertTrustedFavoritosIpcSender(event, 'favoritos-worker:status');
        return favoritosWorkerBrowserStatus(workerId);
    });
    ipcMain.handle('favoritos-worker:show', async (event, workerId = FAVORITOS_WORKER_LEGACY_ID) => {
        assertTrustedFavoritosIpcSender(event, 'favoritos-worker:show');
        return showFavoritosWorkerBrowser(workerId);
    });
    ipcMain.handle('favoritos-worker:hide', async (event, workerId = FAVORITOS_WORKER_LEGACY_ID) => {
        assertTrustedFavoritosIpcSender(event, 'favoritos-worker:hide');
        return hideFavoritosWorkerBrowser(workerId);
    });
    ipcMain.handle('favoritos-worker:stop', async (event, options = {}, workerId = FAVORITOS_WORKER_LEGACY_ID) => {
        assertTrustedFavoritosIpcSender(event, 'favoritos-worker:stop');
        return stopFavoritosWorkerBrowser(options || {}, workerId);
    });
    ipcMain.handle('favoritos-worker:execute', async (event, code, workerId = FAVORITOS_WORKER_LEGACY_ID) => {
        assertTrustedFavoritosIpcSender(event, 'favoritos-worker:execute');
        return executeFavoritosWorkerBrowser(code, workerId);
    });
    ipcMain.handle('favoritos-worker:click', async (event, point, workerId = FAVORITOS_WORKER_LEGACY_ID) => {
        assertTrustedFavoritosIpcSender(event, 'favoritos-worker:click');
        return clickFavoritosWorkerBrowser(point || {}, workerId);
    });
    ipcMain.handle('favoritos-worker:type', async (event, payload, workerId = FAVORITOS_WORKER_LEGACY_ID) => {
        assertTrustedFavoritosIpcSender(event, 'favoritos-worker:type');
        return typeFavoritosWorkerBrowser(payload || {}, workerId);
    });
    ipcMain.handle('favoritos-workers:start-pool', async (event, payload = {}) => {
        assertTrustedFavoritosIpcSender(event, 'favoritos-workers:start-pool');
        const parent = BrowserWindow.fromWebContents(event.sender) || mainWindow;
        return startFavoritosWorkersPool(payload || {}, parent);
    });
    ipcMain.handle('favoritos-workers:status', async (event) => {
        assertTrustedFavoritosIpcSender(event, 'favoritos-workers:status');
        return favoritosWorkersPoolStatus();
    });
    ipcMain.handle('favoritos-workers:pause', async (event) => {
        assertTrustedFavoritosIpcSender(event, 'favoritos-workers:pause');
        return pauseFavoritosWorkersPool();
    });
    ipcMain.handle('favoritos-workers:resume', async (event) => {
        assertTrustedFavoritosIpcSender(event, 'favoritos-workers:resume');
        return resumeFavoritosWorkersPool();
    });
    ipcMain.handle('favoritos-workers:show', async (event) => {
        assertTrustedFavoritosIpcSender(event, 'favoritos-workers:show');
        return showFavoritosWorkersPool();
    });
    ipcMain.handle('favoritos-workers:hide', async (event) => {
        assertTrustedFavoritosIpcSender(event, 'favoritos-workers:hide');
        return hideFavoritosWorkersPool();
    });
    ipcMain.handle('favoritos-workers:stop-pool', async (event, options = {}) => {
        assertTrustedFavoritosIpcSender(event, 'favoritos-workers:stop-pool');
        return stopFavoritosWorkersPool(options || {});
    });
    ipcMain.handle('embedded-ml-browser-state', async (event) => {
        assertTrustedFavoritosIpcSender(event, 'embedded-ml-browser-state');
        const available = !!(
            embeddedMlBrowserView
            && embeddedMlBrowserView.webContents
            && !embeddedMlBrowserView.webContents.isDestroyed()
        );
        const currentUrl = available ? (embeddedMlBrowserView.webContents.getURL() || '') : '';
        return {
            success: available && /^https?:\/\//i.test(currentUrl),
            available,
            attached: !!(
                available
                && embeddedMlBrowserOwner
                && !embeddedMlBrowserOwner.isDestroyed()
            ),
            url: currentUrl,
            authFlow: isMercadoLivreAuthenticationFlowUrl(currentUrl)
        };
    });
    ipcMain.handle('embedded-ml-browser-position', async (event, bounds) => {
        assertTrustedFavoritosIpcSender(event, 'embedded-ml-browser-position');
        const parent = BrowserWindow.fromWebContents(event.sender) || mainWindow;
        const view = ensureEmbeddedMlBrowser(parent);
        view.setBounds(normalizarBoundsNavegadorMl(bounds));
        const currentUrl = view.webContents.getURL() || '';
        if (shouldRememberMercadoLivreStableUrl(currentUrl)) {
            favoritosEmbeddedMlLastUrl = currentUrl;
        }
        return {
            success: true,
            url: currentUrl,
            authFlow: isMercadoLivreAuthenticationFlowUrl(currentUrl)
        };
    });
    ipcMain.handle('embedded-ml-browser-hide', async (event, options = {}) => {
        assertTrustedFavoritosIpcSender(event, 'embedded-ml-browser-hide');
        const hideOptions = options && typeof options === 'object' ? options : {};
        if (hideOptions.preserveAvantProSession === true || hideOptions.preservarSessaoAvantPro === true) {
            await salvarSessaoAvantProAntesDeOcultarNavegador(hideOptions.reason || 'embedded-ml-browser-hide', {
                url: mercadoLivreUrlSeguraParaLog(favoritosEmbeddedMlLastUrl),
                destroy: !!(hideOptions.destroy || hideOptions.unload)
            });
        }
        hideEmbeddedMlBrowser(hideOptions);
        return { success: true };
    });
    ipcMain.handle('embedded-ml-browser-execute', async (event, code) => {
        assertTrustedFavoritosIpcSender(event, 'embedded-ml-browser-execute');
        const view = await getEmbeddedMlBrowserForIpc(event);
        assertAllowedFavoritosWorkerUrl(view.webContents.getURL());
        const script = String(code || '');
        if (script.length > FAVORITOS_WORKER_MAX_SCRIPT_LENGTH) {
            throw new Error('Script excede o limite permitido no navegador do Favoritos.');
        }
        return await view.webContents.executeJavaScript(script, true);
    });
    ipcMain.handle('embedded-ml-browser-login-avantpro', async (event, email) => {
        assertTrustedFavoritosIpcSender(event, 'embedded-ml-browser-login-avantpro');
        return await fillAvantProLoginInEmbeddedBrowser(email);
    });
    ipcMain.handle('embedded-ml-browser-type', async (event, payload) => {
        assertTrustedFavoritosIpcSender(event, 'embedded-ml-browser-type');
        const view = await getEmbeddedMlBrowserForIpc(event);
        const raw = payload && typeof payload === 'object' ? payload : { text: payload };
        const text = String(raw.text ?? raw.value ?? '');
        const clearFirst = raw.clearFirst !== false;
        const pressEnter = raw.pressEnter === true || raw.enter === true;
        const contents = view.webContents;
        logElectronLifecycle('embedded-ml-browser-native-type-start', {
            textLength: text.length,
            clearFirst,
            pressEnter,
            url: mercadoLivreUrlSeguraParaLog(contents.getURL())
        });
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
        logElectronLifecycle('embedded-ml-browser-native-type-done', {
            typed: text.length,
            enter: pressEnter,
            url: mercadoLivreUrlSeguraParaLog(contents.getURL())
        });
        return { success: true, typed: text.length, enter: pressEnter, url: contents.getURL() };
    });
    ipcMain.handle('embedded-ml-browser-click', async (event, point) => {
        assertTrustedFavoritosIpcSender(event, 'embedded-ml-browser-click');
        const view = await getEmbeddedMlBrowserForIpc(event);
        const raw = point || {};
        let x = Number(raw.x ?? raw.left);
        let y = Number(raw.y ?? raw.top);
        if (!Number.isFinite(x) || !Number.isFinite(y)) {
            throw new Error('Coordenada invalida para clicar no navegador do Mercado Livre.');
        }
        let bounds = null;
        try { bounds = view.getBounds ? view.getBounds() : null; } catch (_err) {}
        const maxX = bounds && Number(bounds.width) > 1 ? Number(bounds.width) - 1 : 9999;
        const maxY = bounds && Number(bounds.height) > 1 ? Number(bounds.height) - 1 : 9999;
        x = Math.max(1, Math.min(Math.round(x), maxX));
        y = Math.max(1, Math.min(Math.round(y), maxY));
        const clickCount = Math.max(1, Math.min(2, Math.round(Number(raw.clickCount) || 1)));
        const contents = view.webContents;
        try { contents.focus(); } catch (_err) {}
        logElectronLifecycle('embedded-ml-browser-native-click', {
            x,
            y,
            clickCount,
            source: raw.source || '',
            label: raw.label || '',
            url: mercadoLivreUrlSeguraParaLog(contents.getURL())
        });
        contents.sendInputEvent({ type: 'mouseMove', x, y, movementX: 0, movementY: 0 });
        contents.sendInputEvent({ type: 'mouseDown', x, y, button: 'left', clickCount });
        await waitMs(45);
        contents.sendInputEvent({ type: 'mouseUp', x, y, button: 'left', clickCount });
        return { success: true, x, y, url: contents.getURL() };
    });
    ipcMain.handle('open-internal-browser', async (event, targetUrl) => {
        const url = normalizeTargetUrl(targetUrl);
        if (isMlAutomationProtected() && isMercadoLivreLogoutUrl(url)) {
            logElectronLifecycle('blocked-internal-ml-logout-load-during-favoritos', { url: mercadoLivreUrlSeguraParaLog(url) });
            return { success: false, blocked: true, reason: 'favoritos-em-execucao', url: '' };
        }
        await ensureChromeExtensionsForMlSession();
        const parent = BrowserWindow.fromWebContents(event.sender) || null;
        const internalBrowser = ensureInternalBrowser(parent);

        try {
            const currentUrl = internalBrowser.webContents.getURL();
            if (normalizeComparableUrl(currentUrl) !== normalizeComparableUrl(url)) {
                const loadPromise = waitForMainFrameLoad(internalBrowser);
                await internalBrowser.loadURL(url);
                await loadPromise;
            }
            return { success: true, url: internalBrowser.webContents.getURL() || url };
        } catch (err) {
            console.error('Falha ao abrir navegador interno:', err);
            throw err;
        }
    });

    ipcMain.handle('open-detached-internal-browser', async (event, targetUrl, title = '') => {
        const url = normalizeTargetUrl(targetUrl);
        if (isMlAutomationProtected() && isMercadoLivreLogoutUrl(url)) {
            logElectronLifecycle('blocked-detached-ml-logout-load-during-favoritos', { url: mercadoLivreUrlSeguraParaLog(url) });
            return { success: false, blocked: true, reason: 'favoritos-em-execucao', url: '' };
        }
        await ensureChromeExtensionsForMlSession();
        const parent = BrowserWindow.fromWebContents(event.sender) || null;
        const detachedWindow = createDetachedInternalBrowser(parent, url, title);
        return { success: true, url, windowId: detachedWindow.id };
    });

    ipcMain.handle('open-external-chrome', async (_event, targetUrl) => {
        try {
            return await openUrlInGoogleChrome(targetUrl);
        } catch (err) {
            console.error('Falha ao abrir URL no Chrome:', err);
            throw err;
        }
    });

    ipcMain.handle('extract-ml-search-results', async (event, targetUrl) => {
        const parent = BrowserWindow.fromWebContents(event.sender) || null;
        const internalBrowser = ensureInternalBrowser(parent);
        const url = normalizeTargetUrl(targetUrl || internalBrowser.webContents.getURL());

        try {
            const currentUrl = internalBrowser.webContents.getURL();
            if (normalizeComparableUrl(currentUrl) !== normalizeComparableUrl(url)) {
                const loadPromise = waitForMainFrameLoad(internalBrowser);
                await internalBrowser.loadURL(url);
                await loadPromise;
            }

            return await internalBrowser.webContents.executeJavaScript(`
                (async function () {
                    try {
                        var sleep = function (ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); };
                        var currentUrl = String(window.location.href || '');
                        var bodyText = String(document.body && document.body.innerText ? document.body.innerText : '');
                        var lowerText = bodyText.toLowerCase();
                        var needsLogin =
                            currentUrl.indexOf('/gz/account-verification') >= 0 ||
                            currentUrl.indexOf('/jms/mlb/lgz/login') >= 0 ||
                            lowerText.indexOf('para continuar, acesse sua conta') >= 0;

                        var selectors = [
                            'li.ui-search-layout__item',
                            'div.ui-search-result__wrapper',
                            'div.ui-search-result',
                            'div.poly-card',
                            'section.poly-card'
                        ].join(',');

                        for (var round = 0; round < 18; round += 1) {
                            if (document.querySelectorAll(selectors).length > 0 || document.links.length > 20) break;
                            await sleep(250);
                        }

                        var isProductUrl = function (href) {
                            if (!href) return false;
                            href = String(href);
                            return href.indexOf('mercadolivre.com.br') >= 0 && (
                                href.indexOf('/MLB-') >= 0 ||
                                href.indexOf('/p/MLB') >= 0 ||
                                href.indexOf('/up/MLB') >= 0 ||
                                href.indexOf('pdp_filters=item_id%3AMLB') >= 0 ||
                                href.indexOf('pdp_filters=item_id:MLB') >= 0 ||
                                href.indexOf('wid=MLB') >= 0
                            );
                        };

                        var cleanUrl = function (href) {
                            if (!href) return '';
                            href = String(href).split('#')[0].trim();
                            return href;
                        };

                        var titleFrom = function (node) {
                            if (!node) return '';
                            var titleNode = node.querySelector && node.querySelector('a.poly-component__title, h2.poly-component__title-wrapper a, a.ui-search-link, h2.ui-search-item__title, h3, h2');
                            var txt = titleNode && titleNode.textContent ? titleNode.textContent.trim() : '';
                            if (!txt && node.textContent) txt = node.textContent.trim().split('\\n').map(function (s) { return s.trim(); }).filter(Boolean)[0] || '';
                            return txt.slice(0, 240);
                        };

                        var out = [];
                        var seen = {};
                        var cards = Array.prototype.slice.call(document.querySelectorAll(selectors));
                        for (var i = 0; i < cards.length; i += 1) {
                            var card = cards[i];
                            var cardLinks = Array.prototype.slice.call(card.querySelectorAll('a[href]'));
                            for (var j = 0; j < cardLinks.length; j += 1) {
                                var href = cleanUrl(cardLinks[j].href);
                                if (!isProductUrl(href) || seen[href]) continue;
                                seen[href] = true;
                                out.push({ posicao: out.length + 1, url: href, titulo: titleFrom(card) || (cardLinks[j].textContent || '').trim().slice(0, 240) });
                                break;
                            }
                        }

                        if (!out.length) {
                            var links = Array.prototype.slice.call(document.links || []);
                            for (var k = 0; k < links.length; k += 1) {
                                var linkHref = cleanUrl(links[k].href);
                                if (!isProductUrl(linkHref) || seen[linkHref]) continue;
                                seen[linkHref] = true;
                                out.push({ posicao: out.length + 1, url: linkHref, titulo: (links[k].textContent || links[k].getAttribute('title') || '').trim().slice(0, 240) });
                                if (out.length >= 100) break;
                            }
                        }

                        if (!out.length) {
                            var html = String(document.documentElement && document.documentElement.innerHTML ? document.documentElement.innerHTML : '');
                            var decodedHtml = html
                                .replace(/\\u002F/g, '/')
                                .replace(/\\\//g, '/')
                                .replace(/&amp;/g, '&')
                                .replace(/\\&/g, '&');
                            var productUrlRegex = new RegExp("https?:\\\\/\\\\/(?:www\\\\.)?mercadolivre\\\\.com\\\\.br\\\\/(?:[^\\\"'<>\\\\s]*?(?:MLB-?\\\\d{6,}|\\\\/p\\\\/MLB\\\\d+|wid=MLB\\\\d+|item_id%3AMLB\\\\d+|item_id:MLB\\\\d+)[^\\\"'<>\\\\s]*)", "gi");
                            var matches = decodedHtml.match(productUrlRegex) || [];
                            for (var m = 0; m < matches.length; m += 1) {
                                var matchHref = cleanUrl(matches[m]);
                                if (!isProductUrl(matchHref) || seen[matchHref]) continue;
                                seen[matchHref] = true;
                                out.push({ posicao: out.length + 1, url: matchHref, titulo: '' });
                                if (out.length >= 100) break;
                            }
                        }

                        return {
                            success: true,
                            currentUrl: currentUrl,
                            needsLogin: needsLogin,
                            total: out.length,
                            anuncios: out,
                            debug: {
                                linkCount: document.links ? document.links.length : 0,
                                cardCount: cards.length,
                                title: document.title || ''
                            }
                        };
                    } catch (err) {
                        return {
                            success: false,
                            currentUrl: String(window.location.href || ''),
                            needsLogin: false,
                            total: 0,
                            anuncios: [],
                            error: err && (err.stack || err.message) ? String(err.stack || err.message) : String(err)
                        };
                    }
                })();
            `, true);
        } catch (err) {
            console.error('Erro ao extrair resultados do Mercado Livre:', err);
            throw err;
        }
    });

    ipcMain.handle('fetch-page-links', async (event, targetUrl) => {
        const parent = BrowserWindow.fromWebContents(event.sender) || null;
        const internalBrowser = new BrowserWindow({
            width: 1280,
            height: 820,
            title: 'Navegador Interno - JK Sistema',
            parent,
            webPreferences: {
                contextIsolation: true,
                nodeIntegration: false,
                session: getMlSession()
            }
        });
        internalBrowser.setMenuBarVisibility(false);

        try {
            await internalBrowser.loadURL(normalizeTargetUrl(targetUrl));

            // Aguarda o carregamento completo da pÃƒÂ¡gina
            await internalBrowser.webContents.executeJavaScript(`
                new Promise((resolve) => {
                    const observer = new MutationObserver((mutations, observer) => {
                        if (document.readyState === 'complete') {
                            observer.disconnect();
                            resolve();
                        }
                    });
                    observer.observe(document, { childList: true, subtree: true });
                });
            `);

            // Extrai os links apÃƒÂ³s o carregamento completo
            const links = await internalBrowser.webContents.executeJavaScript(`
                Array.from(document.querySelectorAll('a')).map(a => a.href).filter(href => href)
            `);

            internalBrowser.close();
            return links;
        } catch (err) {
            console.error('Erro ao buscar links da pÃƒÂ¡gina:', err);
            internalBrowser.close();
            throw err;
        }
    });

    createWindow();
    scheduleAutoUpdateCheck();
    const stableTimer = setTimeout(() => {
        clearStartupIncomplete();
        logElectronLifecycle('startup-stable');
    }, 15000);
    if (typeof stableTimer.unref === 'function') stableTimer.unref();

    app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) {
            createWindow();
        }
    });
});

app.on('window-all-closed', () => {
    logElectronLifecycle('window-all-closed');
    if (process.platform !== 'darwin') {
        app.quit();
    }
});

app.on('before-quit', (event) => {
    if (!JK_PRIMARY_INSTANCE_LOCK_ACQUIRED) {
        logElectronLifecycle('before-quit-secondary-instance');
        return;
    }
    if (authenticationQuitPersistenceReady) {
        logElectronLifecycle('before-quit');
        clearStartupIncomplete();
        return;
    }

    event.preventDefault();
    if (authenticationQuitPersistencePromise) return;
    logElectronLifecycle('before-quit-authentication-persistence-started');
    const timeoutPromise = new Promise((resolve) => {
        setTimeout(() => resolve({ success: false, timeout: true }), 8000);
    });
    authenticationQuitPersistencePromise = Promise.race([
        persistAuthenticationState('before-quit-authentication'),
        timeoutPromise
    ])
        .then((result) => {
            logElectronLifecycle('before-quit-authentication-persistence-finished', result || {});
        })
        .catch((err) => {
            logElectronLifecycle('before-quit-authentication-persistence-failed', {
                error: err && err.message ? err.message : String(err)
            });
        })
        .finally(async () => {
            const backendStopResult = await stopLocalBackend();
            logElectronLifecycle('before-quit-local-backend-finished', backendStopResult || {});
            clearStartupIncomplete();
            authenticationQuitPersistenceReady = true;
            authenticationQuitPersistencePromise = null;
            app.quit();
        });
});
