function getMlSession() {
    if (!mlSession) {
        mlSession = session.fromPartition(JK_BROWSER_SESSION_PARTITION);
    }
    return mlSession;
}

let persistentSessionsFlushPromise = null;
let persistentSessionsFlushTimer = null;
let persistentSessionDurabilityRegistered = false;
let persistentAuthCookieChangeCount = 0;

function getBrowserSessionPartition() {
    return JK_BROWSER_SESSION_PARTITION;
}

function isLocalBackendUrlForNotification(rawUrl) {
    try {
        const parsed = new URL(String(rawUrl || ''));
        return (
            ['127.0.0.1', 'localhost'].includes(parsed.hostname)
            && String(parsed.port || '80') === String(JK_LOCAL_BACKEND_PORT)
        );
    } catch (_err) {
        return false;
    }
}

function isDailyMeetingUrl(rawUrl) {
    try {
        const parsed = new URL(String(rawUrl || ''));
        const host = parsed.hostname.toLowerCase();
        return parsed.protocol === 'https:' && (host === 'daily.co' || host.endsWith('.daily.co'));
    } catch (_err) {
        return false;
    }
}

function isAllowedMediaPermissionUrl(rawUrl) {
    return isDailyMeetingUrl(rawUrl) || isLocalBackendUrlForNotification(rawUrl);
}

function labelDisplayMediaSource(source) {
    const name = String(source && source.name || '').trim() || 'Fonte sem nome';
    const type = String(source && source.id || '').startsWith('screen:') ? 'Tela' : 'Janela';
    return `${type}: ${name}`.slice(0, 90);
}

function publicDisplayMediaSource(source) {
    if (!source || !source.id) return null;
    return {
        id: String(source.id || ''),
        name: String(source.name || '').trim() || 'Fonte sem nome',
        type: String(source.id || '').startsWith('screen:') ? 'screen' : 'window',
        label: labelDisplayMediaSource(source)
    };
}

async function chooseDisplayMediaSource(sources) {
    const validSources = (Array.isArray(sources) ? sources : [])
        .filter(source => source && source.id)
        .sort((a, b) => {
            const aScreen = String(a.id || '').startsWith('screen:') ? 0 : 1;
            const bScreen = String(b.id || '').startsWith('screen:') ? 0 : 1;
            return aScreen - bScreen || String(a.name || '').localeCompare(String(b.name || ''), 'pt-BR');
        })
        .slice(0, 18);
    if (!validSources.length) return null;
    const parent = BrowserWindow.getFocusedWindow() || mainWindow || BrowserWindow.getAllWindows().find(win => win && !win.isDestroyed()) || null;
    const channel = `jk-display-source-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const escapeHtml = (value) => String(value || '').replace(/[&<>"']/g, (char) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    }[char]));
    const cards = validSources.map((source, index) => {
        const isScreen = String(source.id || '').startsWith('screen:');
        const kind = isScreen ? 'Tela' : 'Janela';
        const title = String(source.name || '').trim() || 'Fonte sem nome';
        const icon = isScreen
            ? '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="12" rx="2"></rect><path d="M8 20h8"></path><path d="M12 16v4"></path></svg>'
            : '<svg viewBox="0 0 24 24"><rect x="4" y="5" width="16" height="14" rx="2"></rect><path d="M4 9h16"></path><path d="M8 7h.01"></path><path d="M11 7h.01"></path></svg>';
        return `
            <button class="source-card" type="button" data-index="${index}" title="${escapeHtml(labelDisplayMediaSource(source))}">
                <span class="source-icon" aria-hidden="true">${icon}</span>
                <span class="source-text">
                    <span class="source-kind">${kind}</span>
                    <strong>${escapeHtml(title)}</strong>
                    <small>${isScreen ? 'Compartilhar este monitor' : 'Compartilhar esta janela'}</small>
                </span>
            </button>
        `;
    }).join('');
    const html = `<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'self' 'unsafe-inline' data:;">
<title>Compartilhar tela</title>
<style>
* { box-sizing: border-box; }
html, body { margin: 0; width: 100%; min-height: 100%; background: #0e1117; color: #edf6ff; font-family: Inter, "Segoe UI", Arial, sans-serif; }
body { overflow: hidden; }
.share-picker { min-height: 100vh; display: grid; grid-template-rows: auto minmax(0, 1fr) auto; border: 1px solid #2f4562; border-radius: 16px; background: #111827; box-shadow: 0 24px 80px rgba(0, 0, 0, 0.42); overflow: hidden; }
.share-head { -webkit-app-region: drag; display: flex; align-items: center; justify-content: space-between; gap: 18px; padding: 18px 20px 14px; border-bottom: 1px solid #22354e; background: #141d2c; }
.share-title { display: grid; gap: 4px; min-width: 0; }
.share-title span { color: #77b8ff; font-size: 0.72rem; font-weight: 900; letter-spacing: 0.08em; text-transform: uppercase; }
.share-title h1 { margin: 0; font-size: 1.18rem; line-height: 1.2; }
.share-title p { margin: 0; color: #9fb2c8; font-size: 0.86rem; line-height: 1.35; }
.close-btn { -webkit-app-region: no-drag; width: 36px; height: 36px; border: 1px solid #324964; border-radius: 10px; background: #101827; color: #dbeafe; font-size: 1.18rem; cursor: pointer; }
.close-btn:hover { background: #1d2a3d; border-color: #4facfe; }
.source-grid { min-height: 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; padding: 16px 18px; overflow: auto; }
.source-card { min-width: 0; min-height: 92px; display: grid; grid-template-columns: 46px minmax(0, 1fr); align-items: center; gap: 12px; border: 1px solid #28405c; border-radius: 14px; background: #151f31; color: #edf6ff; padding: 14px; text-align: left; cursor: pointer; }
.source-card:hover, .source-card:focus { outline: none; border-color: #4facfe; background: #19273c; box-shadow: 0 14px 32px rgba(79, 172, 254, 0.14); }
.source-icon { width: 46px; height: 46px; display: grid; place-items: center; border-radius: 14px; background: #0b1424; color: #4facfe; border: 1px solid #28405c; }
.source-icon svg { width: 25px; height: 25px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.source-text { min-width: 0; display: grid; gap: 3px; }
.source-kind { color: #83c4ff; font-size: 0.72rem; font-weight: 900; letter-spacing: 0.06em; text-transform: uppercase; }
.source-text strong { min-width: 0; overflow: hidden; color: #fff; font-size: 0.92rem; line-height: 1.25; text-overflow: ellipsis; white-space: nowrap; }
.source-text small { color: #9fb2c8; font-size: 0.78rem; }
.share-foot { display: flex; justify-content: space-between; align-items: center; gap: 14px; padding: 14px 18px 16px; border-top: 1px solid #22354e; background: #101827; }
.hint { color: #9fb2c8; font-size: 0.82rem; }
.cancel-btn { min-height: 38px; border: 1px solid #3a516d; border-radius: 10px; background: #182235; color: #edf6ff; padding: 0 16px; font-weight: 800; cursor: pointer; }
.cancel-btn:hover { border-color: #ff6b6b; color: #ffe6e6; background: #2a1d27; }
@media (max-width: 720px) { .source-grid { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<main class="share-picker">
    <header class="share-head">
        <div class="share-title">
            <span>Compartilhar tela</span>
            <h1>Escolha o que deseja mostrar</h1>
            <p>Selecione um monitor ou uma janela. Para trocar depois, pare o compartilhamento e escolha novamente.</p>
        </div>
        <button id="closeBtn" class="close-btn" type="button" aria-label="Fechar">x</button>
    </header>
    <section class="source-grid" aria-label="Fontes disponiveis">${cards}</section>
    <footer class="share-foot">
        <span class="hint">Dica: escolha uma janela especifica para evitar compartilhar as duas telas.</span>
        <button id="cancelBtn" class="cancel-btn" type="button">Cancelar</button>
    </footer>
</main>
<script>
const { ipcRenderer } = require('electron');
const channel = ${JSON.stringify(channel)};
function send(payload) { ipcRenderer.send(channel, payload); }
document.querySelectorAll('[data-index]').forEach((button) => {
    button.addEventListener('click', () => send({ index: Number(button.dataset.index) }));
});
document.getElementById('closeBtn').addEventListener('click', () => send({ canceled: true }));
document.getElementById('cancelBtn').addEventListener('click', () => send({ canceled: true }));
window.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') send({ canceled: true });
});
</script>
</body>
</html>`;
    return await new Promise((resolve) => {
        let settled = false;
        const height = Math.min(640, Math.max(430, 238 + Math.ceil(validSources.length / 2) * 116));
        const chooser = new BrowserWindow({
            parent: parent || undefined,
            modal: !!parent,
            width: 880,
            height,
            minWidth: 680,
            minHeight: 420,
            resizable: true,
            minimizable: false,
            maximizable: false,
            frame: false,
            title: 'Compartilhar tela',
            backgroundColor: '#0e1117',
            autoHideMenuBar: true,
            webPreferences: {
                nodeIntegration: true,
                contextIsolation: false,
                sandbox: false
            }
        });
        const finish = (source) => {
            if (settled) return;
            settled = true;
            ipcMain.removeListener(channel, onChoice);
            if (chooser && !chooser.isDestroyed()) chooser.close();
            resolve(source || null);
        };
        const onChoice = (_event, payload) => {
            if (payload && payload.canceled) {
                finish(null);
                return;
            }
            const index = Number(payload && payload.index);
            finish(Number.isInteger(index) && index >= 0 && index < validSources.length ? validSources[index] : null);
        };
        ipcMain.on(channel, onChoice);
        chooser.on('closed', () => finish(null));
        chooser.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(html)).catch(() => finish(null));
    });
}

function configureNotificationPermissionsForSession(ses) {
    if (!ses || configuredMeetingPermissionSessions.has(ses)) return;
    configuredMeetingPermissionSessions.add(ses);
    if (typeof ses.setPermissionRequestHandler === 'function') {
        ses.setPermissionRequestHandler((webContents, permission, callback, details) => {
            const requestingUrl = details && (details.requestingUrl || details.embeddingOrigin) || (webContents && webContents.getURL && webContents.getURL()) || '';
            if (permission === 'media') {
                callback(isAllowedMediaPermissionUrl(requestingUrl));
                return;
            }
            if (permission === 'notifications') {
                callback(isLocalBackendUrlForNotification(requestingUrl));
                return;
            }
            callback(false);
        });
    }
    if (typeof ses.setPermissionCheckHandler === 'function') {
        ses.setPermissionCheckHandler((webContents, permission, requestingOrigin) => {
            const currentUrl = requestingOrigin || (webContents && webContents.getURL && webContents.getURL()) || '';
            if (permission === 'media') return isAllowedMediaPermissionUrl(currentUrl);
            if (permission === 'notifications') return isLocalBackendUrlForNotification(currentUrl);
            return false;
        });
    }
    if (typeof ses.setDisplayMediaRequestHandler === 'function') {
        ses.setDisplayMediaRequestHandler((request, callback) => {
            const requestingUrl = request && (request.securityOrigin || request.requestingUrl || request.frameOrigin) || '';
            if (!isAllowedMediaPermissionUrl(requestingUrl)) {
                logElectronLifecycle('display-media-denied', { requestingUrl });
                callback({});
                return;
            }
            logElectronLifecycle('display-media-request', { requestingUrl });
            desktopCapturer.getSources({ types: ['screen', 'window'], thumbnailSize: { width: 0, height: 0 } })
                .then((sources) => chooseDisplayMediaSource(sources))
                .then((source) => {
                    logElectronLifecycle('display-media-selected', source ? publicDisplayMediaSource(source) : { canceled: true });
                    callback(source ? { video: source } : {});
                })
                .catch((err) => {
                    logElectronLifecycle('display-media-error', err);
                    callback({});
                });
        }, { useSystemPicker: false });
    }
}

function configureNotificationPermissions() {
    [session.defaultSession, getMlSession()].forEach(configureNotificationPermissionsForSession);
}

function truncateNotificationText(value, maxLength = 240) {
    const text = String(value || '').replace(/\s+/g, ' ').trim();
    if (text.length <= maxLength) return text;
    return `${text.slice(0, Math.max(0, maxLength - 1)).trim()}...`;
}

function focusMainWindowForNotification() {
    const win = mainWindow && !mainWindow.isDestroyed()
        ? mainWindow
        : BrowserWindow.getAllWindows().find(item => item && !item.isDestroyed());
    if (!win) return;
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
}

function showWindowsNotification(payload = {}) {
    if (!Notification || typeof Notification.isSupported !== 'function' || !Notification.isSupported()) {
        return { success: false, reason: 'unsupported' };
    }
    const title = truncateNotificationText(payload.title || 'JK Sistema', 90);
    const body = truncateNotificationText(payload.body || payload.message || '', 320);
    const notification = new Notification({
        title,
        body,
        silent: payload.silent === true,
    });
    notification.on('click', focusMainWindowForNotification);
    notification.show();
    return { success: true };
}

async function flushPersistentSessions() {
    if (persistentSessionsFlushPromise) return persistentSessionsFlushPromise;
    persistentSessionsFlushPromise = (async () => {
        const sessions = Array.from(new Set([session.defaultSession, getMlSession()].filter(Boolean)));
        const results = await Promise.all(sessions.map(async (ses, index) => {
            let lastError = null;
            for (let attempt = 1; attempt <= 2; attempt += 1) {
                try {
                    if (ses.cookies && typeof ses.cookies.flushStore === 'function') {
                        await ses.cookies.flushStore();
                    }
                    if (typeof ses.flushStorageData === 'function') {
                        await ses.flushStorageData();
                    }
                    return { index, success: true, attempts: attempt };
                } catch (err) {
                    lastError = err;
                    if (attempt < 2) await new Promise(resolve => setTimeout(resolve, 120));
                }
            }
            const error = lastError && lastError.message ? lastError.message : String(lastError || 'erro desconhecido');
            console.warn('[Sessao] Falha ao salvar dados persistentes:', error);
            return { index, success: false, attempts: 2, error };
        }));
        const failures = results.filter(item => !item.success);
        return { success: failures.length === 0, sessions: sessions.length, results, failures };
    })().finally(() => {
        persistentSessionsFlushPromise = null;
    });
    return persistentSessionsFlushPromise;
}

function cookiePertenceAFluxoDeAutenticacaoPersistente(cookie) {
    const domain = String(cookie && cookie.domain || '').replace(/^\./, '').toLowerCase();
    return domain === 'localhost'
        || domain === '127.0.0.1'
        || domain.endsWith('.mercadolivre.com.br')
        || domain === 'mercadolivre.com.br'
        || domain.endsWith('.mercadolivre.com')
        || domain === 'mercadolivre.com'
        || domain.endsWith('.mercadolibre.com')
        || domain === 'mercadolibre.com'
        || domain.endsWith('.mercadopago.com.br')
        || domain === 'mercadopago.com.br'
        || domain.endsWith('.mercadopago.com')
        || domain === 'mercadopago.com';
}

function schedulePersistentSessionsFlush(reason = 'cookie-changed', delayMs = 900) {
    if (persistentSessionsFlushTimer) clearTimeout(persistentSessionsFlushTimer);
    persistentSessionsFlushTimer = setTimeout(() => {
        persistentSessionsFlushTimer = null;
        flushPersistentSessions()
            .then((result) => {
                logElectronLifecycle(result && result.success ? 'authentication-session-flushed' : 'authentication-session-flush-failed', {
                    reason,
                    sessions: result && result.sessions || 0,
                    failures: result && result.failures || [],
                    authCookieChanges: persistentAuthCookieChangeCount
                });
                persistentAuthCookieChangeCount = 0;
            })
            .catch((err) => {
                logElectronLifecycle('authentication-session-flush-failed', {
                    reason,
                    error: err && err.message ? err.message : String(err)
                });
            });
    }, Math.max(100, Number(delayMs) || 900));
    if (typeof persistentSessionsFlushTimer.unref === 'function') persistentSessionsFlushTimer.unref();
}

function registerPersistentSessionDurability() {
    if (persistentSessionDurabilityRegistered) return;
    persistentSessionDurabilityRegistered = true;
    const sessions = Array.from(new Set([session.defaultSession, getMlSession()].filter(Boolean)));
    sessions.forEach((ses) => {
        if (!ses.cookies || typeof ses.cookies.on !== 'function') return;
        ses.cookies.on('changed', (_event, cookie) => {
            if (!cookiePertenceAFluxoDeAutenticacaoPersistente(cookie)) return;
            persistentAuthCookieChangeCount += 1;
            schedulePersistentSessionsFlush('authentication-cookie-changed');
        });
    });
    logElectronLifecycle('authentication-session-durability-ready', {
        partition: getBrowserSessionPartition(),
        sessions: sessions.length,
        userDataDir: JK_ELECTRON_USER_DATA_DIR
    });
}

async function persistAuthenticationState(reason = 'manual', options = {}) {
    const flush = await flushPersistentSessions();
    let avant = null;
    if (options.saveAvantPro !== false && typeof saveAvantProExtensionStorageSnapshot === 'function') {
        avant = await saveAvantProExtensionStorageSnapshot(reason, {
            source: 'electron-auth-persistence',
            savedAt: Date.now()
        }).catch((err) => ({
            success: false,
            error: err && err.message ? err.message : String(err)
        }));
    }
    const result = {
        success: !!(flush && flush.success) && (
            options.saveAvantPro === false
            || !avant
            || !!avant.success
            || (avant.skipped && avant.reason === 'avantpro-disabled')
        ),
        sessions: flush && flush.sessions || 0,
        sessionFailures: flush && flush.failures || [],
        avantPro: avant ? {
            success: !!avant.success,
            skipped: !!avant.skipped,
            reason: avant.reason || '',
            copied: Number(avant.copied || 0)
        } : null
    };
    logElectronLifecycle('authentication-state-persisted', { reason, ...result });
    return result;
}

async function clearElectronCache() {
    const sessions = [session.defaultSession, getMlSession()];
    await Promise.all(sessions.map(async (ses) => {
        try {
            await ses.clearCache();
        } catch (err) {
            console.warn('[Cache] Falha ao limpar cache:', err && err.message ? err.message : err);
        }
    }));
}

function getAppRootDir() {
    return JK_APP_ROOT_DIR;
}

function sameResolvedPath(left, right) {
    if (!left || !right) return false;
    try {
        return path.resolve(left).toLowerCase() === path.resolve(right).toLowerCase();
    } catch (_err) {
        return false;
    }
}

function getPackagedLocalBackendSourceDir() {
    if (!app.isPackaged || !process.resourcesPath) return '';
    const candidate = path.join(process.resourcesPath, JK_LOCAL_BACKEND_DIR_NAME);
    return fs.existsSync(path.join(candidate, 'backend_api.py')) ? candidate : '';
}

function getEnvLocalBackendSourceDir() {
    const rawSourceDir = process.env.JK_LOCAL_BACKEND_SOURCE_DIR;
    if (!rawSourceDir) return '';
    const resolved = path.resolve(rawSourceDir);
    if (app.isPackaged && sameResolvedPath(resolved, getLocalBackendRuntimeDir())) {
        return '';
    }
    return resolved;
}

function getBundledLocalBackendDir() {
    const candidates = app.isPackaged
        ? [
            getPackagedLocalBackendSourceDir(),
            getEnvLocalBackendSourceDir(),
            path.join(getAppRootDir(), JK_LOCAL_BACKEND_DIR_NAME),
            fs.existsSync(path.join(getAppRootDir(), 'backend_api.py')) ? getAppRootDir() : '',
            path.resolve(__dirname, '..')
        ].filter(Boolean)
        : [
            getEnvLocalBackendSourceDir(),
            path.join(getAppRootDir(), JK_LOCAL_BACKEND_DIR_NAME),
            fs.existsSync(path.join(getAppRootDir(), 'backend_api.py')) ? getAppRootDir() : '',
            path.resolve(__dirname, '..')
        ].filter(Boolean);
    for (const candidate of candidates) {
        const resolved = path.resolve(candidate);
        if (fs.existsSync(path.join(resolved, 'backend_api.py'))) return resolved;
    }
    return '';
}

function getLocalBackendRuntimeDir() {
    if (process.env.JK_LOCAL_BACKEND_DIR) {
        return path.resolve(process.env.JK_LOCAL_BACKEND_DIR);
    }
    if (app.isPackaged) {
        return path.join(JK_ELECTRON_USER_DATA_DIR, JK_LOCAL_BACKEND_DIR_NAME);
    }
    return getBundledLocalBackendDir() || path.resolve(__dirname, '..');
}

function getLocalBackendInfoDir() {
    return path.join(getLocalBackendRuntimeDir(), 'info');
}

function getFirebaseServiceAccountCandidates(localAppDir) {
    const infoDir = path.join(localAppDir, 'info');
    const candidates = [
        path.join(infoDir, 'firebase-service-account.json'),
        path.join(infoDir, 'firebase_service_account.json'),
        path.join(localAppDir, 'firebase-service-account.json'),
        path.join(localAppDir, 'firebase_service_account.json')
    ];
    try {
        const entries = fs.readdirSync(localAppDir, { withFileTypes: true });
        for (const entry of entries) {
            if (!entry.isFile()) continue;
            const lower = entry.name.toLowerCase();
            if (
                /^jkjkjk-.*\.json$/i.test(lower) ||
                /service[-_ ]?account.*\.json$/i.test(lower) ||
                /^firebase[-_].*\.json$/i.test(lower)
            ) {
                candidates.push(path.join(localAppDir, entry.name));
            }
        }
    } catch (_err) {}
    return [...new Set(candidates)];
}

function readFirebaseServiceAccount(filePath) {
    const data = readJsonFile(filePath);
    if (
        data &&
        data.type === 'service_account' &&
        data.client_email &&
        data.private_key
    ) {
        return data;
    }
    return null;
}

function getFirebasePresenceEnvCandidates(localAppDir) {
    return [
        path.join(localAppDir, JK_FIREBASE_PRESENCE_ENV_FILE_NAME),
        path.join(localAppDir, 'info', JK_FIREBASE_PRESENCE_ENV_FILE_NAME),
        path.join(localAppDir, '.env')
    ].filter(Boolean);
}

function readFirebaseRuntimeEnvValues(localAppDir) {
    const values = {};
    for (const candidate of getFirebasePresenceEnvCandidates(localAppDir)) {
        Object.assign(values, readLocalDotEnvValues(candidate));
    }
    return values;
}

function pickFirebaseRuntimeEnv(values) {
    const allowedKeys = [
        'FIREBASE_DATABASE_URL',
        'FIREBASE_REALTIME_DATABASE_URL',
        'JK_FIREBASE_DATABASE_URL',
        'JK_FIREBASE_REALTIME_DATABASE_URL',
        'FIREBASE_WEB_API_KEY',
        'FIREBASE_API_KEY',
        'JK_FIREBASE_WEB_API_KEY',
        'JK_FIREBASE_API_KEY',
        'FIREBASE_AUTH_DOMAIN',
        'JK_FIREBASE_AUTH_DOMAIN',
        'FIREBASE_PROJECT_ID',
        'JK_FIREBASE_PROJECT_ID',
        'FIREBASE_WEB_APP_ID',
        'JK_FIREBASE_WEB_APP_ID'
    ];
    const selected = {};
    for (const key of allowedKeys) {
        const value = String(values[key] || '').trim();
        if (value) selected[key] = value;
    }
    return selected;
}

function getLocalBackendFirebaseEnv(localAppDir) {
    const runtimeEnv = pickFirebaseRuntimeEnv(readFirebaseRuntimeEnvValues(localAppDir));
    for (const candidate of getFirebaseServiceAccountCandidates(localAppDir)) {
        if (!fs.existsSync(candidate)) continue;
        const account = readFirebaseServiceAccount(candidate);
        if (!account) continue;
        return {
            JK_ACCESS_BACKEND: 'firebase',
            FIREBASE_SERVICE_ACCOUNT_FILE: candidate,
            JK_FIREBASE_LIVE_FEATURES: 'true',
            FIREBASE_LIVE_FEATURES: 'true',
            JK_FIREBASE_CHAT_PRESENCE_ENABLED: 'true',
            ...runtimeEnv,
            ...(account.project_id && !runtimeEnv.FIREBASE_PROJECT_ID ? { FIREBASE_PROJECT_ID: String(account.project_id) } : {})
        };
    }
    return { JK_ACCESS_BACKEND: 'auto' };
}

let backendRuntimeMaterializerModule = null;

function getBackendRuntimeMaterializer() {
    if (backendRuntimeMaterializerModule) return backendRuntimeMaterializerModule;
    const candidates = [
        path.join(getAppRootDir(), 'electron_app', 'main', 'modules', 'backend-runtime-materializer.js'),
        path.join(__dirname, 'electron_app', 'main', 'modules', 'backend-runtime-materializer.js')
    ];
    let lastError = null;
    for (const candidate of candidates) {
        try {
            backendRuntimeMaterializerModule = require(candidate);
            return backendRuntimeMaterializerModule;
        } catch (err) {
            lastError = err;
        }
    }
    throw lastError || new Error('Materializador seguro do backend local nao foi encontrado.');
}

function inspectBundledLocalBackendMaterialization() {
    const runtimeDir = getLocalBackendRuntimeDir();
    const bundledDir = getBundledLocalBackendDir();
    if (!bundledDir) {
        throw new Error('Backend local nao foi encontrado no pacote.');
    }
    if (sameResolvedPath(runtimeDir, bundledDir)) {
        return {
            required: false,
            sameDirectory: true,
            runtimeDir,
            bundledDir
        };
    }
    const application = getBackendRuntimeMaterializer().inspectMaterialization({
        sourceDir: bundledDir,
        targetDir: runtimeDir,
        expectedVersion: app.getVersion()
    });
    const payload = getBackendRuntimeMaterializer().inspectImmutableRuntimePayloads({
        sourceDir: bundledDir,
        targetDir: runtimeDir
    });
    return {
        ...application,
        required: !!(application.required || payload.required),
        recoveryRequired: !!(application.recoveryRequired || payload.recoveryRequired),
        application,
        payload,
        runtimeDir,
        bundledDir
    };
}

function syncBundledLocalBackend(inspection = null) {
    const plan = inspection || inspectBundledLocalBackendMaterialization();
    const runtimeDir = plan.runtimeDir || getLocalBackendRuntimeDir();
    const bundledDir = plan.bundledDir || getBundledLocalBackendDir();
    if (!bundledDir) {
        throw new Error('Backend local nao foi encontrado no pacote.');
    }
    const payloadMaterialization = getBackendRuntimeMaterializer().ensureImmutableRuntimePayloads({
        sourceDir: bundledDir,
        targetDir: runtimeDir
    });
    const materialization = getBackendRuntimeMaterializer().materializeLocalApp({
        sourceDir: bundledDir,
        targetDir: runtimeDir,
        expectedVersion: app.getVersion()
    });
    fs.mkdirSync(path.join(runtimeDir, 'info'), { recursive: true });
    fs.mkdirSync(path.join(runtimeDir, 'logs'), { recursive: true });
    logElectronLifecycle('local-backend-materialization', {
        changed: !!materialization.changed,
        adopted: !!materialization.adopted,
        version: materialization.version || app.getVersion(),
        changedEntries: Array.isArray(materialization.changedEntries)
            ? materialization.changedEntries
            : [],
        payloadChanged: !!payloadMaterialization.changed,
        payloadAdopted: !!payloadMaterialization.adopted
    });
    return { runtimeDir, bundledDir, materialization, payloadMaterialization };
}

function isTcpPortOpen(port, host = '127.0.0.1', timeoutMs = 700) {
    return new Promise((resolve) => {
        const socket = new nodeNet.Socket();
        let done = false;
        const finish = (result) => {
            if (done) return;
            done = true;
            try { socket.destroy(); } catch (_err) {}
            resolve(result);
        };
        socket.setTimeout(timeoutMs);
        socket.once('connect', () => finish(true));
        socket.once('timeout', () => finish(false));
        socket.once('error', () => finish(false));
        socket.connect(port, host);
    });
}

function waitForTcpPortOpen(port, timeoutMs = 120000, intervalMs = 650) {
    const startedAt = Date.now();
    return new Promise((resolve, reject) => {
        const check = async () => {
            if (await isTcpPortOpen(port)) {
                resolve(true);
                return;
            }
            if (Date.now() - startedAt >= timeoutMs) {
                reject(new Error(`Servidor local nao respondeu na porta ${port}.`));
                return;
            }
            setTimeout(check, intervalMs);
        };
        check();
    });
}

function waitForTcpPortClosed(port, timeoutMs = 15000, intervalMs = 400) {
    const startedAt = Date.now();
    return new Promise((resolve) => {
        const check = async () => {
            if (!(await isTcpPortOpen(port, '127.0.0.1', 350))) {
                resolve(true);
                return;
            }
            if (Date.now() - startedAt >= timeoutMs) {
                resolve(false);
                return;
            }
            setTimeout(check, intervalMs);
        };
        check();
    });
}

function fetchLocalBackendJson(pathname, timeoutMs = 2500) {
    return new Promise((resolve) => {
        const req = http.get({
            host: '127.0.0.1',
            port: JK_LOCAL_BACKEND_PORT,
            path: pathname,
            timeout: timeoutMs,
            headers: {
                'Cache-Control': 'no-cache',
                'User-Agent': `JK-Sistema-Desktop/${app.getVersion()}`
            }
        }, (res) => {
            let body = '';
            res.setEncoding('utf8');
            res.on('data', chunk => { body += chunk; });
            res.on('end', () => {
                try {
                    resolve(JSON.parse(body || '{}'));
                } catch (_err) {
                    resolve(null);
                }
            });
        });
        req.on('timeout', () => {
            try { req.destroy(); } catch (_err) {}
            resolve(null);
        });
        req.on('error', () => resolve(null));
    });
}

function localBackendHealthCompatible(health, firebaseEnv = null) {
    if (!health || health.ok !== true) return false;
    const backendVersion = String(health.appVersion || '').replace(/^v/i, '').trim();
    const desktopVersion = String(app.getVersion() || '').replace(/^v/i, '').trim();
    if (!backendVersion || backendVersion !== desktopVersion) return false;
    const expectsFirebase = firebaseEnv && String(firebaseEnv.JK_ACCESS_BACKEND || '').toLowerCase() === 'firebase';
    if (expectsFirebase) {
        if (health.firebaseActive !== true) return false;
        if (health.firebaseLiveFeatures !== true) return false;
    }
    return true;
}

function localBackendHealthUsable(health) {
    return Boolean(health && health.ok === true);
}

function waitForLocalBackendCompatibleHealth(firebaseEnv, timeoutMs = 45000, intervalMs = 650) {
    const startedAt = Date.now();
    return new Promise((resolve, reject) => {
        const check = async () => {
            const health = await fetchLocalBackendJson('/health', 3500);
            if (localBackendHealthCompatible(health, firebaseEnv)) {
                resolve(health);
                return;
            }
            if (localBackendHealthUsable(health)) {
                reject(new Error(
                    `Servidor local iniciou com versao incompativel: ${health.appVersion || 'desconhecida'}.`
                ));
                return;
            }
            if (Date.now() - startedAt >= timeoutMs) {
                reject(new Error('Servidor local abriu a porta, mas nao confirmou um health compativel.'));
                return;
            }
            setTimeout(check, intervalMs);
        };
        check();
    });
}

function stopProcessListeningOnPort(port) {
    return new Promise((resolve) => {
        const script = [
            `$ErrorActionPreference = 'SilentlyContinue'`,
            `$pids = Get-NetTCPConnection -LocalPort ${Number(port)} -State Listen | Select-Object -ExpandProperty OwningProcess -Unique`,
            `foreach ($pidValue in $pids) { if ($pidValue) { Stop-Process -Id $pidValue -Force } }`
        ].join('; ');
        const child = spawn('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', script], {
            stdio: 'ignore',
            windowsHide: true
        });
        child.on('error', () => resolve(false));
        child.on('exit', (code) => resolve(code === 0));
    });
}

function consumeCanonicalLauncherPreparedServers() {
    const prepared = /^(1|true|yes)$/i.test(
        String(process.env.JK_LOCAL_SERVERS_PREPARED_BY_LAUNCHER || '')
    );
    delete process.env.JK_LOCAL_SERVERS_PREPARED_BY_LAUNCHER;
    return prepared;
}

function managedLocalServerPorts() {
    return Array.from(new Set([
        JK_LOCAL_BACKEND_PORT,
        JK_PROMO_WORKER_PORT,
        JK_LEGACY_WHATSAPP_VOICE_PORT
    ].map(Number).filter(port => Number.isInteger(port) && port > 0 && port <= 65535)));
}

async function stopManagedLocalServers(reason = 'unspecified') {
    const ports = managedLocalServerPorts();
    const listeningBefore = await Promise.all(ports.map(port => isTcpPortOpen(port)));
    const killRequested = await Promise.all(ports.map(port => stopProcessListeningOnPort(port)));
    const closed = await Promise.all(ports.map(port => waitForTcpPortClosed(port)));
    const servers = ports.map((port, index) => ({
        port,
        listeningBefore: listeningBefore[index],
        killRequested: killRequested[index],
        closed: closed[index]
    }));
    const result = {
        success: servers.every(server => server.closed),
        reason: String(reason || 'unspecified'),
        servers
    };
    logElectronLifecycle(
        result.success ? 'local-managed-servers-stopped' : 'local-managed-servers-stop-incomplete',
        result
    );
    return result;
}

function cmdValue(value) {
    return String(value || '').replace(/"/g, '');
}

function isolatedPythonChildEnv(overrides = {}) {
    const env = { ...process.env, ...overrides };
    for (const key of Object.keys(env)) {
        const normalized = String(key).toUpperCase();
        if (
            normalized === 'PYTHONHOME'
            || normalized === 'PYTHONPATH'
            || normalized === 'PYTHONUSERBASE'
            || normalized === 'VIRTUAL_ENV'
            || normalized.startsWith('PIP_')
        ) {
            delete env[key];
        }
    }
    return {
        ...env,
        PYTHONNOUSERSITE: '1',
        PYTHONDONTWRITEBYTECODE: '1',
        PYTHONUTF8: '1',
        PYTHONUNBUFFERED: '1',
        PIP_CONFIG_FILE: 'NUL',
        PIP_DISABLE_PIP_VERSION_CHECK: '1',
        PIP_NO_INDEX: '1'
    };
}

function firebaseRuntimeCmdLines(firebaseEnv) {
    const keys = [
        'FIREBASE_DATABASE_URL',
        'FIREBASE_REALTIME_DATABASE_URL',
        'JK_FIREBASE_DATABASE_URL',
        'JK_FIREBASE_REALTIME_DATABASE_URL',
        'FIREBASE_WEB_API_KEY',
        'FIREBASE_API_KEY',
        'JK_FIREBASE_WEB_API_KEY',
        'JK_FIREBASE_API_KEY',
        'FIREBASE_AUTH_DOMAIN',
        'JK_FIREBASE_AUTH_DOMAIN',
        'FIREBASE_WEB_APP_ID',
        'JK_FIREBASE_WEB_APP_ID',
        'FIREBASE_LIVE_FEATURES',
        'JK_FIREBASE_LIVE_FEATURES',
        'JK_FIREBASE_CHAT_PRESENCE_ENABLED'
    ];
    return keys
        .filter((key) => String(firebaseEnv[key] || '').trim())
        .map((key) => `set "${key}=${cmdValue(firebaseEnv[key])}"`);
}

function readPythonRuntimeProvisionStatus(localAppDir) {
    const statusPath = path.join(localAppDir, 'info', 'python-runtime-status.json');
    try {
        const status = JSON.parse(fs.readFileSync(statusPath, 'utf8'));
        return { statusPath, status: status && typeof status === 'object' ? status : null };
    } catch (_err) {
        return { statusPath, status: null };
    }
}

function pythonRuntimeStageFromStatus(status) {
    const explicit = String(status && (status.stage || status.step) || '').trim();
    if (explicit) return explicit;
    const code = String(status && (status.error_code || status.errorCode || status.code) || '').toLowerCase();
    if (/lock/.test(code)) return 'provision_lock';
    if (/portable|runtime|python_(?:missing|probe|version|abi|arch)/.test(code)) return 'portable_runtime';
    if (/wheel|requirements/.test(code)) return 'wheelhouse_validation';
    if (/venv/.test(code)) return 'venv_creation';
    if (/pip|ensurepip|offline_install/.test(code)) return 'dependency_install';
    if (/critical_import|health/.test(code)) return 'health_check';
    return 'provision_exit';
}

function pythonRuntimeDiagnosticError(message, details = {}) {
    const statusInfo = details.statusInfo || readPythonRuntimeProvisionStatus(details.targetRoot || getLocalBackendRuntimeDir());
    const status = statusInfo.status || {};
    const stage = String(details.stage || status.stage || status.step || 'python_runtime').trim();
    const rawCode = details.code ?? status.code ?? status.error_code ?? status.errorCode;
    const code = rawCode === undefined || rawCode === null || rawCode === '' ? 'indisponivel' : String(rawCode);
    const logPath = String(details.logPath || status.log_file || status.logPath || '').trim();
    const statusMessage = String(status.state || '').toLowerCase() === 'failed'
        ? String(status.message || status.error || status.detail || '').trim()
        : '';
    const lines = [
        String(message || 'Falha ao preparar o ambiente Python local.'),
        `Etapa: ${stage}`,
        `Codigo: ${code}`,
        ...(statusMessage && !String(message || '').includes(statusMessage) ? [`Detalhe: ${statusMessage}`] : []),
        ...(logPath ? [`Log: ${logPath}`] : []),
        `Status: ${statusInfo.statusPath}`
    ];
    const error = new Error(lines.join('\n'));
    error.jkLocalBackendDiagnostic = true;
    error.jkStage = stage;
    error.jkCode = code;
    error.jkLogPath = logPath;
    return error;
}

function appendProvisionerOutput(logPath, label, output) {
    const text = String(output || '').trim();
    if (!text) return;
    try {
        fs.appendFileSync(logPath, `\n[${label}]\n${text}\n`, 'utf8');
    } catch (_err) {}
}

function ensurePythonRuntimeProvisioned(sourceRoot, targetRoot) {
    const portablePython = path.join(sourceRoot, 'python_runtime', 'portable', 'python.exe');
    const provisioner = path.join(sourceRoot, 'scripts', 'provision_python_runtime.py');
    const logPath = path.join(targetRoot, 'logs', 'python-runtime-provision.log');
    const statusPath = path.join(targetRoot, 'info', 'python-runtime-status.json');
    const venvPython = path.join(targetRoot, '.venv', 'Scripts', 'python.exe');
    const readyMarker = path.join(targetRoot, '.venv', '.jk-venv-ready.json');
    const incompleteInstallerMarker = path.join(targetRoot, 'info', 'installer-python-runtime.incomplete');

    fs.mkdirSync(path.dirname(logPath), { recursive: true });
    fs.mkdirSync(path.dirname(statusPath), { recursive: true });
    try {
        fs.appendFileSync(
            logPath,
            `\n==== JK Sistema Python self-heal ${new Date().toISOString()} ====\nSource: ${sourceRoot}\nTarget: ${targetRoot}\n`,
            'utf8'
        );
    } catch (_err) {}

    if (!fs.existsSync(portablePython)) {
        return Promise.reject(pythonRuntimeDiagnosticError(
            'Python portatil nao foi encontrado no pacote. Reinstale o JK Sistema usando o instalador completo.',
            { stage: 'portable_python', code: 'PORTABLE_PYTHON_MISSING', logPath, targetRoot }
        ));
    }
    if (!fs.existsSync(provisioner)) {
        return Promise.reject(pythonRuntimeDiagnosticError(
            'Provisionador do ambiente Python nao foi encontrado no pacote.',
            { stage: 'provisioner', code: 'PROVISIONER_MISSING', logPath, targetRoot }
        ));
    }

    logElectronLifecycle('python-runtime-provision-starting', {
        sourceRoot,
        targetRoot,
        portablePython,
        provisioner,
        logPath,
        statusPath
    });

    return new Promise((resolve, reject) => {
        let settled = false;
        let stdout = '';
        let stderr = '';
        const outputLimit = 512 * 1024;
        const collect = (current, chunk) => `${current}${String(chunk || '')}`.slice(-outputLimit);
        const child = spawn(portablePython, [
            '-B',
            '-I',
            provisioner,
            '--source-root', sourceRoot,
            '--target-root', targetRoot,
            '--log-file', logPath,
            '--quick-reuse',
            '--command-timeout', '1500'
        ], {
            cwd: sourceRoot,
            env: isolatedPythonChildEnv(),
            stdio: ['ignore', 'pipe', 'pipe'],
            windowsHide: true
        });
        const timeout = setTimeout(async () => {
            if (settled) return;
            settled = true;
            const treeStopped = await stopTrackedProcessTree(child.pid);
            if (!treeStopped) {
                try { child.kill(); } catch (_err) {}
            }
            appendProvisionerOutput(logPath, 'stdout', stdout);
            appendProvisionerOutput(logPath, 'stderr', stderr);
            reject(pythonRuntimeDiagnosticError(
                'O preparo do ambiente Python excedeu o tempo limite de 40 minutos.',
                { stage: 'provision_timeout', code: 'TIMEOUT', logPath, targetRoot }
            ));
        }, 40 * 60 * 1000);

        if (child.stdout) child.stdout.on('data', chunk => { stdout = collect(stdout, chunk); });
        if (child.stderr) child.stderr.on('data', chunk => { stderr = collect(stderr, chunk); });
        child.once('error', (err) => {
            if (settled) return;
            settled = true;
            clearTimeout(timeout);
            appendProvisionerOutput(logPath, 'stdout', stdout);
            appendProvisionerOutput(logPath, 'stderr', `${stderr}\n${err && err.stack ? err.stack : err}`);
            reject(pythonRuntimeDiagnosticError(
                'Nao foi possivel executar o provisionador do ambiente Python.',
                { stage: 'provision_spawn', code: err && err.code ? err.code : 'SPAWN_FAILED', logPath, targetRoot }
            ));
        });
        child.once('close', (code, signal) => {
            if (settled) return;
            settled = true;
            clearTimeout(timeout);
            appendProvisionerOutput(logPath, 'stdout', stdout);
            appendProvisionerOutput(logPath, 'stderr', stderr);
            const statusInfo = readPythonRuntimeProvisionStatus(targetRoot);
            const ready = Number(code) === 0
                && statusInfo.status
                && String(statusInfo.status.state || '').toLowerCase() === 'ready'
                && fs.existsSync(venvPython)
                && fs.existsSync(readyMarker);
            if (!ready) {
                const statusCode = statusInfo.status && (
                    statusInfo.status.error_code
                    || statusInfo.status.errorCode
                    || statusInfo.status.code
                );
                reject(pythonRuntimeDiagnosticError(
                    'O ambiente Python local nao foi preparado ou validado.',
                    {
                        stage: pythonRuntimeStageFromStatus(statusInfo.status),
                        code: statusCode || (code === null ? (signal || 'NO_EXIT_CODE') : code),
                        logPath,
                        targetRoot,
                        statusInfo
                    }
                ));
                return;
            }
            logElectronLifecycle('python-runtime-provision-ready', {
                code,
                sourceRoot,
                targetRoot,
                statusPath,
                logPath
            });
            try { fs.rmSync(incompleteInstallerMarker, { force: true }); } catch (_err) {}
            resolve({ portablePython, venvPython, readyMarker, statusPath, logPath, status: statusInfo.status });
        });
    });
}

function writeLocalBackendLauncher(localAppDir) {
    const infoDir = path.join(localAppDir, 'info');
    const firebaseEnv = getLocalBackendFirebaseEnv(localAppDir);
    const launcherPath = path.join(JK_ELECTRON_USER_DATA_DIR, 'start-local-backend.cmd');
    const logPath = path.join(localAppDir, 'logs', `local_backend_start_${Date.now()}_${process.pid}.log`);
    const localCallback = process.env.JK_LOCAL_OAUTH_CALLBACK_URL || 'https://jkjkjk-485920.web.app/auth/callback';
    const localGoogleCallback = process.env.JK_LOCAL_GOOGLE_CALLBACK_URL || 'https://jkjkjk-485920.web.app/auth/google/callback';
    const lines = [
        '@echo off',
        'setlocal EnableExtensions EnableDelayedExpansion',
        `cd /d "${cmdValue(localAppDir)}"`,
        'if not exist "logs" mkdir "logs"',
        'if not exist "info" mkdir "info"',
        `set "LOG_FILE=${cmdValue(logPath)}"`,
        `set "JK_INFO_DIR=${cmdValue(infoDir)}"`,
        `set "JK_REDIRECT_URI=${localCallback}"`,
        `set "JK_BLING_REDIRECT_URI=${localCallback}"`,
        `set "GOOGLE_LOGIN_REDIRECT_URI_LOCAL=${localGoogleCallback}"`,
        `set "PROMO_WORKER_URL=http://127.0.0.1:${JK_PROMO_WORKER_PORT}"`,
        `set "JK_APP_VERSION=${cmdValue(app.getVersion())}"`,
        'set "JK_CONTEXT_HUB_SURFACE=installed"',
        `set "JK_CODEX_CONSOLE_ENABLED=${cmdValue(process.env.JK_CODEX_CONSOLE_ENABLED || 'true')}"`,
        'set "IA_RAG_ENABLED=true"',
        'set "IA_RAG_BACKEND=local"',
        'set "IA_RAG_TOP_K=5"',
        'set "IA_RAG_SEARCH_TIMEOUT_S=4"',
        `set "JK_ACCESS_BACKEND=${cmdValue(firebaseEnv.JK_ACCESS_BACKEND || 'auto')}"`,
        ...(firebaseEnv.FIREBASE_SERVICE_ACCOUNT_FILE ? [
            `set "FIREBASE_SERVICE_ACCOUNT_FILE=${cmdValue(firebaseEnv.FIREBASE_SERVICE_ACCOUNT_FILE)}"`
        ] : []),
        ...(firebaseEnv.FIREBASE_PROJECT_ID ? [
            `set "FIREBASE_PROJECT_ID=${cmdValue(firebaseEnv.FIREBASE_PROJECT_ID)}"`
        ] : []),
        ...firebaseRuntimeCmdLines(firebaseEnv),
        'set "PYTHONHOME="',
        'set "PYTHONPATH="',
        'set "PYTHONUSERBASE="',
        'set "VIRTUAL_ENV="',
        'set "PYTHONNOUSERSITE=1"',
        'set "PYTHONDONTWRITEBYTECODE=1"',
        'set "PYTHONUNBUFFERED=1"',
        'set "PYTHONUTF8=1"',
        'set "PIP_CONFIG_FILE=NUL"',
        'set "PIP_NO_INDEX=1"',
        'echo.>> "%LOG_FILE%"',
        'echo ==== JK Sistema local backend %date% %time% ====>> "%LOG_FILE%"',
        'set "PYTHON_EXE=.venv\\Scripts\\python.exe"',
        'if not exist ".venv\\Scripts\\python.exe" (',
        '  echo Ambiente Python provisionado nao encontrado. Execute novamente o instalador completo.>> "%LOG_FILE%"',
        '  exit /b 21',
        ')',
        '"%PYTHON_EXE%" -B -I -X utf8 -c "import sys, uvicorn; print(sys.version)" >> "%LOG_FILE%" 2>&1',
        'if errorlevel 1 (',
        '  echo Ambiente Python provisionado ficou indisponivel antes de iniciar o backend.>> "%LOG_FILE%"',
        '  exit /b 22',
        ')',
        'set "JK_CA_BUNDLE=%CD%\\.venv\\Lib\\site-packages\\certifi\\cacert.pem"',
        'if exist "%JK_CA_BUNDLE%" (',
        '  set "SSL_CERT_FILE=%JK_CA_BUNDLE%"',
        '  set "REQUESTS_CA_BUNDLE=%JK_CA_BUNDLE%"',
        '  set "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=%JK_CA_BUNDLE%"',
        '  echo Usando certificados Python: %JK_CA_BUNDLE%>> "%LOG_FILE%"',
        ')',
        `"%PYTHON_EXE%" -X utf8 -B -I -m uvicorn --app-dir "%CD%" backend_api:app --host 127.0.0.1 --port ${JK_LOCAL_BACKEND_PORT} >> "%LOG_FILE%" 2>&1`
    ];
    fs.writeFileSync(launcherPath, `${lines.join('\r\n')}\r\n`, 'utf8');
    return { launcherPath, logPath };
}

function ensureLocalBackendStarted() {
    if (localBackendStartupPromise) {
        return localBackendStartupPromise;
    }

    let startupLogPath = '';
    let pendingMaterialization = null;
    localBackendStartupPromise = (async () => {
        const inspection = inspectBundledLocalBackendMaterialization();
        const localAppDir = inspection.runtimeDir;
        const bundledSourceDir = inspection.bundledDir;
        const firebaseEnv = getLocalBackendFirebaseEnv(localAppDir);

        const launcherPrepared = consumeCanonicalLauncherPreparedServers();
        if (launcherPrepared && await isTcpPortOpen(JK_LOCAL_BACKEND_PORT)) {
            const health = await fetchLocalBackendJson('/health');
            if (localBackendHealthCompatible(health, firebaseEnv) && !inspection.required) {
                logElectronLifecycle('local-backend-prestarted-by-canonical-launcher', {
                    port: JK_LOCAL_BACKEND_PORT,
                    health
                });
                return {
                    success: true,
                    alreadyRunning: true,
                    preparedByCanonicalLauncher: true,
                    port: JK_LOCAL_BACKEND_PORT
                };
            }
        }

        const cleanup = await stopManagedLocalServers('before-start');
        if (!cleanup.success) {
            const blockedPorts = cleanup.servers
                .filter(server => !server.closed)
                .map(server => server.port)
                .join(', ');
            throw new Error(`Nao foi possivel encerrar os servidores antigos nas portas ${blockedPorts}.`);
        }

        const synchronized = syncBundledLocalBackend(inspection);
        pendingMaterialization = synchronized.materialization;
        await ensurePythonRuntimeProvisioned(bundledSourceDir, localAppDir);
        const launcher = writeLocalBackendLauncher(localAppDir);
        const launcherPath = launcher.launcherPath;
        startupLogPath = launcher.logPath;
        logElectronLifecycle('local-backend-starting', { localAppDir, launcherPath, logPath: startupLogPath });
        const child = spawn('cmd.exe', ['/d', '/c', launcherPath], {
            cwd: localAppDir,
            env: isolatedPythonChildEnv({
                ...firebaseEnv,
                JK_INFO_DIR: path.join(localAppDir, 'info'),
                JK_REDIRECT_URI: process.env.JK_LOCAL_OAUTH_CALLBACK_URL || 'https://jkjkjk-485920.web.app/auth/callback',
                JK_BLING_REDIRECT_URI: process.env.JK_LOCAL_OAUTH_CALLBACK_URL || 'https://jkjkjk-485920.web.app/auth/callback',
                GOOGLE_LOGIN_REDIRECT_URI_LOCAL: process.env.JK_LOCAL_GOOGLE_CALLBACK_URL || 'https://jkjkjk-485920.web.app/auth/google/callback',
                PROMO_WORKER_URL: `http://127.0.0.1:${JK_PROMO_WORKER_PORT}`,
                JK_APP_VERSION: app.getVersion(),
                JK_CONTEXT_HUB_SURFACE: 'installed',
                JK_CODEX_CONSOLE_ENABLED: process.env.JK_CODEX_CONSOLE_ENABLED || 'true',
                IA_RAG_ENABLED: 'true',
                IA_RAG_BACKEND: 'local',
                IA_RAG_TOP_K: '5',
                IA_RAG_SEARCH_TIMEOUT_S: '4'
            }),
            stdio: 'ignore',
            windowsHide: true
        });
        let backendBecameReady = false;
        let runtimeMarkerInvalidated = false;
        const invalidateRuntimeMarker = (reason, details = {}) => {
            if (runtimeMarkerInvalidated || backendBecameReady) return;
            runtimeMarkerInvalidated = true;
            const readyMarker = path.join(localAppDir, '.venv', '.jk-venv-ready.json');
            try {
                fs.rmSync(readyMarker, { force: true });
                logElectronLifecycle('python-runtime-marker-invalidated', {
                    reason,
                    readyMarker,
                    ...details
                });
            } catch (markerError) {
                logElectronLifecycle('python-runtime-marker-invalidation-failed', markerError);
            }
        };
        localBackendProcess = child;
        child.on('error', (err) => {
            logElectronLifecycle('local-backend-process-error', err);
        });
        child.on('exit', (code, signal) => {
            logElectronLifecycle('local-backend-exit', { code, signal });
            if (localBackendProcess === child) {
                localBackendProcess = null;
                localBackendStartupPromise = null;
            }
        });

        const exitPromise = new Promise((_resolve, reject) => {
            child.once('exit', (code, signal) => {
                invalidateRuntimeMarker('backend_exited_before_ready', { code, signal });
                reject(pythonRuntimeDiagnosticError(
                    'Servidor local finalizou antes de responder.',
                    {
                        stage: 'backend_start',
                        code: code === null ? (signal || 'NO_EXIT_CODE') : code,
                        logPath: startupLogPath,
                        targetRoot: localAppDir
                    }
                ));
            });
        });
        try {
            await Promise.race([
                waitForTcpPortOpen(JK_LOCAL_BACKEND_PORT, 180000),
                exitPromise
            ]);
            const health = await Promise.race([
                waitForLocalBackendCompatibleHealth(firebaseEnv),
                exitPromise
            ]);
            logElectronLifecycle('local-backend-health-compatible', {
                port: JK_LOCAL_BACKEND_PORT,
                appVersion: health && health.appVersion
            });
        } catch (startupError) {
            invalidateRuntimeMarker('backend_not_ready', {
                error: startupError && startupError.message ? startupError.message : String(startupError)
            });
            await stopTrackedProcessTree(child.pid);
            throw startupError;
        }
        backendBecameReady = true;
        if (pendingMaterialization && pendingMaterialization.changed) {
            const finalization = getBackendRuntimeMaterializer().finalizeMaterialization(pendingMaterialization);
            logElectronLifecycle('local-backend-materialization-finalized', finalization);
        }
        pendingMaterialization = null;
        logElectronLifecycle('local-backend-ready', { port: JK_LOCAL_BACKEND_PORT });
        return { success: true, localAppDir, port: JK_LOCAL_BACKEND_PORT };
    })().catch((err) => {
        if (pendingMaterialization && pendingMaterialization.changed) {
            try {
                const rollback = getBackendRuntimeMaterializer().rollbackMaterialization(pendingMaterialization);
                logElectronLifecycle('local-backend-materialization-rolled-back', rollback);
            } catch (rollbackError) {
                logElectronLifecycle('local-backend-materialization-rollback-failed', rollbackError);
                err.materializationRollbackError = rollbackError;
            } finally {
                pendingMaterialization = null;
            }
        }
        localBackendStartupPromise = null;
        if (err && err.jkLocalBackendDiagnostic) throw err;
        throw pythonRuntimeDiagnosticError(
            err && err.message ? err.message : String(err),
            {
                stage: startupLogPath ? 'backend_health' : 'backend_prepare',
                code: err && err.code ? err.code : 'STARTUP_FAILED',
                logPath: startupLogPath,
                targetRoot: getLocalBackendRuntimeDir()
            }
        );
    });

    return localBackendStartupPromise;
}

function stopTrackedProcessTree(pid) {
    return new Promise((resolve) => {
        if (!Number.isInteger(Number(pid)) || Number(pid) <= 0) {
            resolve(false);
            return;
        }
        let settled = false;
        const finish = (success) => {
            if (settled) return;
            settled = true;
            resolve(Boolean(success));
        };
        try {
            const child = spawn('taskkill.exe', ['/PID', String(pid), '/T', '/F'], {
                stdio: 'ignore',
                windowsHide: true
            });
            child.once('error', () => finish(false));
            child.once('exit', (code) => finish(code === 0));
        } catch (_err) {
            finish(false);
        }
    });
}

async function stopLocalBackend() {
    if (localBackendStopPromise) return localBackendStopPromise;

    localBackendStopPromise = (async () => {
        const trackedProcess = localBackendProcess;
        const trackedPid = trackedProcess && trackedProcess.pid ? Number(trackedProcess.pid) : null;
        localBackendProcess = null;

        const trackedTreeStopped = trackedPid
            ? await stopTrackedProcessTree(trackedPid)
            : false;

        // O .cmd de inicializacao pode terminar antes do Uvicorn. Por isso
        // todas as portas gerenciadas sao encerradas mesmo sem PID rastreado.
        const managedStop = await stopManagedLocalServers('quit');

        localBackendStartupPromise = null;
        const result = {
            success: managedStop.success,
            trackedPid,
            trackedTreeStopped,
            managedServers: managedStop.servers
        };
        logElectronLifecycle(
            result.success ? 'local-backend-stopped' : 'local-backend-stop-incomplete',
            result
        );
        return result;
    })()
        .catch((err) => {
            const result = {
                success: false,
                error: err && err.message ? err.message : String(err)
            };
            logElectronLifecycle('local-backend-stop-error', result);
            return result;
        })
        .finally(() => {
            localBackendStopPromise = null;
        });

    return localBackendStopPromise;
}

function readLocalDotEnvValues(envPath) {
    const values = {};
    try {
        if (!fs.existsSync(envPath)) return values;
        const content = fs.readFileSync(envPath, 'utf8');
        for (const rawLine of content.split(/\r?\n/)) {
            const line = String(rawLine || '').trim();
            if (!line || line.startsWith('#') || !line.includes('=')) continue;
            const index = line.indexOf('=');
            const key = line.slice(0, index).trim();
            let value = line.slice(index + 1).trim();
            if (!key) continue;
            if (
                (value.startsWith('"') && value.endsWith('"')) ||
                (value.startsWith("'") && value.endsWith("'"))
            ) {
                value = value.slice(1, -1);
            }
            values[key] = value;
        }
    } catch (_err) {}
    return values;
}

function readJsonFile(filePath) {
    try {
        if (!filePath || !fs.existsSync(filePath)) return null;
        return JSON.parse(fs.readFileSync(filePath, 'utf8'));
    } catch (err) {
        console.warn('[Config] Falha ao ler configuracao:', filePath, err && err.message ? err.message : err);
        return null;
    }
}

function writeJsonFile(filePath, data) {
    try {
        fs.mkdirSync(path.dirname(filePath), { recursive: true });
        fs.writeFileSync(filePath, `${JSON.stringify(data, null, 2)}\n`, 'utf8');
        return true;
    } catch (err) {
        console.warn('[Config] Falha ao salvar configuracao:', filePath, err && err.message ? err.message : err);
        return false;
    }
}

function getBrowserExtensionsConfigPath() {
    return path.join(JK_ELECTRON_USER_DATA_DIR, 'browser-extensions-config.json');
}

function normalizeBrowserExtensionsConfig(raw) {
    const data = raw && typeof raw === 'object' ? raw : {};
    return {
        avantProEnabled: data.avantProEnabled === undefined
            ? AVANTPRO_EXTENSION_DEFAULT_ENABLED
            : data.avantProEnabled === true
    };
}

function loadBrowserExtensionsConfig() {
    return normalizeBrowserExtensionsConfig(readJsonFile(getBrowserExtensionsConfigPath()));
}

function saveBrowserExtensionsConfig(nextConfig) {
    const current = loadBrowserExtensionsConfig();
    const payload = {
        ...current,
        ...(nextConfig && typeof nextConfig === 'object' ? nextConfig : {}),
        updatedAt: new Date().toISOString()
    };
    payload.avantProEnabled = payload.avantProEnabled === true;
    if (!writeJsonFile(getBrowserExtensionsConfigPath(), payload)) {
        throw new Error('Nao foi possivel salvar a configuracao das extensoes.');
    }
    return normalizeBrowserExtensionsConfig(payload);
}

function isAvantProExtensionEnabled() {
    return loadBrowserExtensionsConfig().avantProEnabled === true;
}

function normalizeAppUrl(value) {
    const raw = String(value || '').trim();
    if (!raw) return '';
    if (!/^https?:\/\//i.test(raw)) return '';
    return raw;
}

function getConfigPaths() {
    return {
        envConfig: process.env.JK_APP_CONFIG ? path.resolve(process.env.JK_APP_CONFIG) : '',
        userConfig: path.join(JK_ELECTRON_USER_DATA_DIR, 'client-config.json'),
        bundledConfig: path.join(getAppRootDir(), 'client-config.json'),
        devConfig: path.join(__dirname, 'client-config.json')
    };
}

function ensureUserConfigFile(paths) {
    if (fs.existsSync(paths.userConfig)) return;
    const bundled = readJsonFile(paths.bundledConfig) || readJsonFile(paths.devConfig);
    writeJsonFile(paths.userConfig, bundled || {
        appUrl: JK_DEFAULT_APP_URL,
        notes: 'Endereco local do JK Sistema. O app inicia o backend local automaticamente.'
    });
}

function loadClientConfig() {
    const paths = getConfigPaths();
    ensureUserConfigFile(paths);

    const sources = [
        readJsonFile(paths.devConfig),
        readJsonFile(paths.bundledConfig),
        readJsonFile(paths.userConfig),
        readJsonFile(paths.envConfig)
    ].filter(Boolean);

    const merged = Object.assign({}, ...sources);
    const envAppUrl = normalizeAppUrl(process.env.JK_APP_URL);
    let appUrl = envAppUrl || normalizeAppUrl(merged.appUrl) || JK_DEFAULT_APP_URL;
    if (!envAppUrl && (isPlaceholderAppUrl(appUrl) || isLegacyCloudRunAppUrl(appUrl) || isLegacyTunnelAppUrl(appUrl))) {
        appUrl = JK_DEFAULT_APP_URL;
        writeJsonFile(paths.userConfig, {
            appUrl,
            notes: 'Endereco local do JK Sistema. O app inicia o backend local automaticamente.'
        });
    }
    return { ...merged, appUrl, configPath: paths.userConfig };
}

function isPlaceholderAppUrl(appUrl) {
    const value = String(appUrl || '').trim().toLowerCase();
    return !value || value.includes('seu-servidor.com');
}

function isLegacyLocalAppUrl(appUrl) {
    const value = String(appUrl || '').trim().toLowerCase();
    return value.startsWith('http://127.0.0.1:') || value.startsWith('http://localhost:');
}

function isLegacyCloudRunAppUrl(appUrl) {
    const value = String(appUrl || '').trim().toLowerCase();
    return (
        value.includes('jk-sistema-api-1077918177671.southamerica-east1.run.app') ||
        value.includes('.a.run.app') ||
        value.includes('.run.app/')
    );
}

function isLegacyTunnelAppUrl(appUrl) {
    const value = String(appUrl || '').trim().toLowerCase();
    return (
        value.includes('.ngrok-free.app') ||
        value.includes('.ngrok-free.dev') ||
        value.includes('.ngrok.app') ||
        value.includes('.ngrok.io')
    );
}

function appendNoCache(appUrl) {
    const value = normalizeAppUrl(appUrl) || JK_DEFAULT_APP_URL;
    return `${value}${value.includes('?') ? '&' : '?'}_jk_nocache=${Date.now()}`;
}

function saveClientAppUrl(appUrl) {
    const value = normalizeAppUrl(appUrl);
    if (!value) {
        throw new Error('Informe um endereco valido com http:// ou https://.');
    }
    const paths = getConfigPaths();
    const payload = {
        appUrl: value,
        notes: 'Endereco local ou personalizado do JK Sistema Cliente.'
    };
    if (!writeJsonFile(paths.userConfig, payload)) {
        throw new Error('Nao foi possivel salvar a configuracao do servidor.');
    }
    return { ...payload, configPath: paths.userConfig };
}

function isLocalBackendAppUrl(appUrl) {
    try {
        const parsed = new URL(normalizeAppUrl(appUrl) || JK_DEFAULT_APP_URL);
        const host = parsed.hostname.toLowerCase();
        return (host === '127.0.0.1' || host === 'localhost') && Number(parsed.port || 80) === JK_LOCAL_BACKEND_PORT;
    } catch (_err) {
        return false;
    }
}

function escapeHtml(value) {
    return String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function isIgnorableStartupNavigationAbort(errorCode, message = '') {
    return Number(errorCode) === -3 || /\bERR_ABORTED\b|\(-3\)|loading 'https?:\/\//i.test(String(message || ''));
}

function renderLocalBackendStartupScreen(win, options = {}) {
    if (!win || win.isDestroyed()) return;
    const error = options.error ? String(options.error) : '';
    const logsDir = path.join(getLocalBackendRuntimeDir(), 'logs');
    const detail = error
        ? `${error}\n\nPasta de logs: ${logsDir}`
        : String(options.detail || 'Preparando o servidor local. Na primeira abertura isso pode levar alguns minutos enquanto as dependencias sao instaladas.');
    const html = `<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>JK Sistema</title>
    <style>
        :root { color-scheme: dark; font-family: Inter, Segoe UI, Arial, sans-serif; }
        body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #07111f; color: #eef6ff; }
        main { width: min(560px, calc(100vw - 48px)); }
        h1 { margin: 0 0 10px; font-size: 28px; font-weight: 800; }
        p { margin: 0; color: #b8c9dc; line-height: 1.5; white-space: pre-line; overflow-wrap: anywhere; }
        .bar { height: 5px; overflow: hidden; border-radius: 99px; background: rgba(255,255,255,0.12); margin-top: 24px; }
        .bar::before { content: ""; display: block; width: 42%; height: 100%; background: #5db7ff; border-radius: inherit; animation: load 1.2s ease-in-out infinite; }
        .error { color: #ffb4b4; }
        @keyframes load { 0% { transform: translateX(-110%); } 100% { transform: translateX(260%); } }
    </style>
</head>
<body>
    <main>
        <h1>${error ? 'Servidor local indisponivel' : 'Iniciando JK Sistema'}</h1>
        <p class="${error ? 'error' : ''}">${escapeHtml(detail)}</p>
        ${error ? '' : '<div class="bar" aria-hidden="true"></div>'}
    </main>
</body>
</html>`;
    win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`).catch((err) => {
        if (isIgnorableStartupNavigationAbort(null, err && err.message ? err.message : String(err))) {
            logElectronLifecycle('local-backend-startup-screen-aborted-ignored', {
                warning: err && err.message ? err.message : String(err)
            });
            return;
        }
        logElectronLifecycle('local-backend-startup-screen-error', err);
    });
}
