const electron = require('electron');
if (!electron || !electron.app) {
    const { spawn } = require('child_process');
    const env = { ...process.env };
    delete env.ELECTRON_RUN_AS_NODE;
    const electronPath = typeof electron === 'string' ? electron : process.execPath;
    spawn(electronPath, [__dirname], {
        cwd: __dirname,
        detached: true,
        env,
        stdio: 'ignore',
        windowsHide: false
    }).unref();
    process.exit(0);
}

const { app, BrowserWindow, BrowserView, ipcMain, session, net, shell, dialog } = electron;
const path = require('path');
const fs = require('fs');
const os = require('os');
const { spawn } = require('child_process');
const nodeNet = require('net');
const http = require('http');
const { pathToFileURL } = require('url');
let autoUpdater = null;
try {
    ({ autoUpdater } = require('electron-updater'));
} catch (err) {
    console.warn('[Atualizacao] electron-updater indisponivel:', err && err.message ? err.message : err);
}

// Evita crash silencioso de GPU em alguns ambientes Windows.
app.disableHardwareAcceleration();
app.commandLine.appendSwitch('disable-gpu');
app.commandLine.appendSwitch('disable-http-cache');
app.setName('JK Sistema Cliente');

const JK_LOCAL_BACKEND_PORT = 8001;
const JK_PROMO_WORKER_PORT = 8011;
const JK_DEFAULT_APP_URL = `http://127.0.0.1:${JK_LOCAL_BACKEND_PORT}/frontend_index.html`;
const JK_LOCAL_BACKEND_DIR_NAME = 'local_app';
const JK_PRIVATE_CREDENTIALS_FILE_NAME = 'credenciais-jk-private.jkcred';
let localBackendProcess = null;
let localBackendStartupPromise = null;

function resolveAppRootDir() {
    const candidates = [
        process.env.JK_APP_ROOT_DIR,
        app.isPackaged ? process.resourcesPath : null,
        fs.existsSync(path.join(__dirname, 'backend_api.py')) ? __dirname : null,
        path.resolve(__dirname, '..'),
        __dirname
    ].filter(Boolean);

    for (const candidate of candidates) {
        const root = path.resolve(candidate);
        if (
            fs.existsSync(path.join(root, 'electron_shell.html')) ||
            fs.existsSync(path.join(root, 'extensoes_chrome'))
        ) {
            return root;
        }
    }

    return path.resolve(candidates[0] || __dirname);
}

const JK_APP_ROOT_DIR = resolveAppRootDir();
const JK_DEFAULT_ELECTRON_USER_DATA_DIR = app.isPackaged
    ? app.getPath('userData')
    : path.join(JK_APP_ROOT_DIR, 'info', 'electron_user_data');
const JK_ELECTRON_USER_DATA_DIR = process.env.JK_ELECTRON_USER_DATA_DIR || JK_DEFAULT_ELECTRON_USER_DATA_DIR;
try {
    fs.mkdirSync(JK_ELECTRON_USER_DATA_DIR, { recursive: true });
    app.setPath('userData', JK_ELECTRON_USER_DATA_DIR);
} catch (err) {
    console.warn('[Sessao] Falha ao configurar pasta persistente do Electron:', err && err.message ? err.message : err);
}

let mlSession = null;
let mainWindow = null;
let internalBrowserWindow = null;
let embeddedMlBrowserView = null;
let embeddedMlBrowserOwner = null;
let chromeExtensionsLoadPromise = null;
let chromeExtensionSessionEventsRegistered = false;
let updateEventsRegistered = false;
let updateCheckInProgress = false;
let updateInstallInProgress = false;
let deferredDownloadedUpdateInfo = null;
let updateFeedConfigured = false;
let updateFeedSource = '';
const mlAutomationProtectionByWebContents = new Map();
const mlItemInfoCache = new Map();
const ML_ITEM_INFO_CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutos
const AUTO_UPDATE_CHECK_TIMEOUT_MS = 45000;
const JK_BROWSER_SESSION_PARTITION = process.env.JK_BROWSER_SESSION_PARTITION || 'persist:jk-sistema-browser';
const AVANTPRO_CHROME_EXTENSION_ID = 'jdefnfmbnchmnjkcknaadaddgjbgephh';

function logElectronLifecycle(...args) {
    const logDir = path.join(JK_ELECTRON_USER_DATA_DIR, 'logs');
    const message = `[Electron ${new Date().toISOString()}] ${args.map(value => {
        if (value instanceof Error) return value.stack || value.message;
        if (typeof value === 'string') return value;
        try { return JSON.stringify(value); } catch (_err) { return String(value); }
    }).join(' ')}\n`;
    try {
        fs.mkdirSync(logDir, { recursive: true });
        fs.appendFileSync(path.join(logDir, 'electron_runtime.log'), message, 'utf8');
    } catch (_err) {}
    try {
        if (process.stdout && !process.stdout.destroyed && process.stdout.writable) {
            process.stdout.write(message);
        }
    } catch (_err) {}
}

function isBrokenStdoutPipe(err) {
    return !!(err && (err.code === 'EPIPE' || /EPIPE|broken pipe/i.test(String(err.message || err))));
}

function ignoreBrokenPipe(stream) {
    try {
        if (!stream || typeof stream.on !== 'function') return;
        stream.on('error', (err) => {
            if (isBrokenStdoutPipe(err)) return;
            try {
                const logDir = path.join(getAppRootDir(), 'logs');
                fs.mkdirSync(logDir, { recursive: true });
                fs.appendFileSync(
                    path.join(logDir, 'electron_runtime.log'),
                    `[Electron ${new Date().toISOString()}] stream-error ${err && err.stack ? err.stack : String(err)}\n`,
                    'utf8'
                );
            } catch (_err) {}
        });
    } catch (_err) {}
}

ignoreBrokenPipe(process.stdout);
ignoreBrokenPipe(process.stderr);

const JK_ELECTRON_STARTUP_MARKER = path.join(JK_ELECTRON_USER_DATA_DIR, '.startup_incomplete');

function safeProfilePath(entryName) {
    const profileRoot = path.resolve(JK_ELECTRON_USER_DATA_DIR);
    const target = path.resolve(path.join(profileRoot, entryName));
    if (target !== profileRoot && target.startsWith(profileRoot + path.sep)) {
        return target;
    }
    return null;
}

function quarantineProfileEntry(entryName, recoveryDir) {
    const target = safeProfilePath(entryName);
    if (!target || !fs.existsSync(target)) return false;
    fs.mkdirSync(recoveryDir, { recursive: true });
    fs.renameSync(target, path.join(recoveryDir, entryName));
    return true;
}

function recoverProfileIfStartupCrashed() {
    if (!fs.existsSync(JK_ELECTRON_STARTUP_MARKER)) return;
    const stamp = new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14);
    const recoveryDir = path.join(JK_ELECTRON_USER_DATA_DIR, `electron_user_data_recovery_${stamp}`);
    const entries = [
        'Local Storage',
        'Session Storage',
        'Cache',
        'Code Cache',
        'GPUCache',
        'DawnCache',
        'blob_storage',
        'Shared Dictionary'
    ];
    const moved = [];
    for (const entry of entries) {
        try {
            if (quarantineProfileEntry(entry, recoveryDir)) moved.push(entry);
        } catch (err) {
            logElectronLifecycle('profile-recovery-failed', { entry, error: err && err.message ? err.message : String(err) });
        }
    }
    logElectronLifecycle('profile-recovery-after-startup-crash', { moved, recoveryDir });
}

function markStartupIncomplete() {
    try {
        fs.writeFileSync(JK_ELECTRON_STARTUP_MARKER, new Date().toISOString(), 'utf8');
    } catch (_err) {}
}

function clearStartupIncomplete() {
    try {
        if (fs.existsSync(JK_ELECTRON_STARTUP_MARKER)) {
            fs.unlinkSync(JK_ELECTRON_STARTUP_MARKER);
        }
    } catch (_err) {}
}

process.on('uncaughtException', (err) => {
    if (isBrokenStdoutPipe(err)) return;
    logElectronLifecycle('uncaughtException', err);
});
process.on('unhandledRejection', (err) => {
    if (isBrokenStdoutPipe(err)) return;
    logElectronLifecycle('unhandledRejection', err);
});

function _cacheNowMs() {
    return Date.now();
}

function _cacheGet(cacheMap, key) {
    const entry = cacheMap.get(key);
    if (!entry) return null;
    if (_cacheNowMs() - (entry.at || 0) > ML_ITEM_INFO_CACHE_TTL_MS) {
        cacheMap.delete(key);
        return null;
    }
    return entry.value;
}

function _cacheSet(cacheMap, key, value) {
    cacheMap.set(key, { at: _cacheNowMs(), value });
}

function normalizeUpdateInfo(info) {
    if (!info || typeof info !== 'object') {
        return null;
    }
    return {
        version: info.version || '',
        releaseName: info.releaseName || '',
        releaseDate: info.releaseDate || '',
        files: Array.isArray(info.files) ? info.files.length : 0
    };
}

function formatVersionLabel(value) {
    return String(value || '').trim() || 'desconhecida';
}

function withUpdateCheckTimeout(promise, timeoutMs, message) {
    let timer = null;
    const timeoutPromise = new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(message)), timeoutMs);
        if (timer && typeof timer.unref === 'function') timer.unref();
    });
    return Promise.race([promise, timeoutPromise]).finally(() => {
        if (timer) clearTimeout(timer);
    });
}

function getUpdateErrorMessage(err) {
    if (!err) return 'Erro desconhecido ao verificar atualizacao.';
    const raw = String(err.message || err);
    if (/app-update\.ya?ml|ENOENT/i.test(raw)) {
        return 'Canal de atualizacao nao encontrado no pacote instalado. Gere o instalador novamente ou confira o app-update.yml.';
    }
    if (/404|not found/i.test(raw)) {
        return 'Atualizacao nao encontrada no GitHub. Confira se a release e o latest.yml foram publicados.';
    }
    if (/401|403|unauthorized|forbidden/i.test(raw)) {
        return 'GitHub recusou a consulta da atualizacao. Confira o acesso ao repositorio ou token da release privada.';
    }
    if (/net::|ENOTFOUND|ECONN|ETIMEDOUT|network/i.test(raw)) {
        return 'Falha de rede ao consultar atualizacao. Confira a internet e tente novamente.';
    }
    return raw.slice(0, 500);
}

function getAppUpdateConfigPath() {
    const candidates = [
        app.isPackaged && process.resourcesPath ? path.join(process.resourcesPath, 'app-update.yml') : '',
        path.join(getAppRootDir(), 'app-update.yml')
    ].filter(Boolean);
    for (const candidate of candidates) {
        if (fs.existsSync(candidate)) return candidate;
    }
    return '';
}

function getBundledUpdateFeedConfig() {
    const fallback = {
        provider: 'github',
        owner: 'marketflow-lab',
        repo: 'Jksistema-1.1',
        releaseType: 'release'
    };
    const candidates = [
        path.join(__dirname, 'package.json'),
        path.join(getAppRootDir(), 'package.json')
    ];
    for (const candidate of candidates) {
        try {
            if (!candidate || !fs.existsSync(candidate)) continue;
            const pkg = JSON.parse(fs.readFileSync(candidate, 'utf8'));
            const publish = pkg && pkg.build && Array.isArray(pkg.build.publish) ? pkg.build.publish[0] : null;
            if (publish && publish.provider && publish.owner && publish.repo) {
                return {
                    provider: publish.provider,
                    owner: publish.owner,
                    repo: publish.repo,
                    releaseType: publish.releaseType || 'release'
                };
            }
        } catch (_err) {}
    }
    return fallback;
}

function configureAutoUpdaterFeed() {
    if (!autoUpdater) {
        return { success: false, reason: 'electron-updater nao esta disponivel neste pacote.' };
    }
    if (updateFeedConfigured) {
        return { success: true, source: updateFeedSource || 'cached' };
    }
    const configPath = getAppUpdateConfigPath();
    if (configPath) {
        updateFeedConfigured = true;
        updateFeedSource = 'app-update.yml';
        return { success: true, source: updateFeedSource, configPath };
    }
    const feed = getBundledUpdateFeedConfig();
    if (!feed || !feed.provider || !feed.owner || !feed.repo) {
        return {
            success: false,
            reason: 'Canal de atualizacao nao configurado no pacote.'
        };
    }
    try {
        autoUpdater.setFeedURL(feed);
        updateFeedConfigured = true;
        updateFeedSource = 'package-publish';
        logElectronLifecycle('auto-update-feed-configured', { source: updateFeedSource, feed });
        return { success: true, source: updateFeedSource, feed };
    } catch (err) {
        return {
            success: false,
            reason: getUpdateErrorMessage(err)
        };
    }
}

function getUpdateUnavailableReason() {
    if (!app.isPackaged) {
        return 'Atualizacao automatica funciona apenas no app instalado.';
    }
    if (!getAppUpdateConfigPath()) {
        const feed = getBundledUpdateFeedConfig();
        if (!feed || !feed.provider || !feed.owner || !feed.repo) {
            return 'Este instalador privado nao possui canal de atualizacao automatica. Use a versao privada mais recente gerada localmente.';
        }
    }
    return '';
}

function isMlAutomationProtected() {
    return mlAutomationProtectionByWebContents.size > 0;
}

function releaseMlAutomationProtectionForContents(contents) {
    if (!contents || !contents.id) return;
    if (mlAutomationProtectionByWebContents.delete(contents.id)) {
        logElectronLifecycle('ml-automation-protection-released', { webContentsId: contents.id });
        maybeInstallDeferredUpdate();
    }
}

function setMlAutomationProtection(contents, active, reason = '') {
    if (!contents || !contents.id) {
        return { success: false, active: isMlAutomationProtected(), count: mlAutomationProtectionByWebContents.size };
    }
    if (active) {
        const firstLock = !mlAutomationProtectionByWebContents.has(contents.id);
        mlAutomationProtectionByWebContents.set(contents.id, {
            reason: String(reason || 'favoritos'),
            startedAt: Date.now()
        });
        if (firstLock && typeof contents.once === 'function') {
            contents.once('destroyed', () => releaseMlAutomationProtectionForContents(contents));
        }
        logElectronLifecycle('ml-automation-protection-enabled', {
            webContentsId: contents.id,
            reason,
            count: mlAutomationProtectionByWebContents.size
        });
    } else {
        releaseMlAutomationProtectionForContents(contents);
    }
    return { success: true, active: isMlAutomationProtected(), count: mlAutomationProtectionByWebContents.size };
}

function maybeInstallDeferredUpdate() {
    if (!deferredDownloadedUpdateInfo || isMlAutomationProtected() || updateInstallInProgress || !autoUpdater) return;
    const info = deferredDownloadedUpdateInfo;
    deferredDownloadedUpdateInfo = null;
    setTimeout(() => {
        installDownloadedUpdateSafely(info).catch((err) => {
            logElectronLifecycle('auto-update-deferred-install-error', err);
        });
    }, 1200);
}

function sendUpdateStatus(status, payload = {}) {
    const message = { status, ...payload };
    logElectronLifecycle('auto-update-status', message);
    for (const win of BrowserWindow.getAllWindows()) {
        try {
            if (!win.isDestroyed()) {
                win.webContents.send('auto-update-status', message);
            }
        } catch (_err) {}
    }
}

function withTimeout(promise, timeoutMs, fallbackValue) {
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => resolve(fallbackValue), timeoutMs);
        promise
            .then((value) => {
                clearTimeout(timer);
                resolve(value);
            })
            .catch((err) => {
                clearTimeout(timer);
                reject(err);
            });
    });
}

async function prepareOpenWorkForUpdate(reason = 'auto-update') {
    sendUpdateStatus('saving-work', { reason });
    const windows = BrowserWindow.getAllWindows().filter((win) => win && !win.isDestroyed());
    const results = [];
    for (const win of windows) {
        try {
            const payload = JSON.stringify({ reason });
            const result = await withTimeout(
                win.webContents.executeJavaScript(`
                    (async () => {
                        if (typeof window.jkElectronPrepareForUpdate !== 'function') {
                            return { success: true, reason: 'no-renderer-handler' };
                        }
                        return await window.jkElectronPrepareForUpdate(${payload});
                    })()
                `, true),
                15000,
                { success: false, timedOut: true }
            );
            results.push(result);
        } catch (err) {
            results.push({ success: false, error: getUpdateErrorMessage(err) });
        }
    }
    await flushPersistentSessions();
    sendUpdateStatus('work-saved', { reason, results });
    return { success: true, results };
}

async function installDownloadedUpdateSafely(info = null) {
    if (!autoUpdater || updateInstallInProgress) return;
    if (isMlAutomationProtected()) {
        deferredDownloadedUpdateInfo = info || deferredDownloadedUpdateInfo;
        sendUpdateStatus('deferred', {
            reason: 'favoritos-em-execucao',
            updateInfo: normalizeUpdateInfo(info)
        });
        return;
    }
    updateInstallInProgress = true;
    try {
        await prepareOpenWorkForUpdate('update-install');
        sendUpdateStatus('installing', { updateInfo: normalizeUpdateInfo(info) });
        autoUpdater.quitAndInstall(true, true);
    } catch (err) {
        updateInstallInProgress = false;
        const message = getUpdateErrorMessage(err);
        sendUpdateStatus('error', { error: message });
    }
}

function registerAutoUpdateEvents() {
    if (!autoUpdater || updateEventsRegistered) return false;
    updateEventsRegistered = true;

    autoUpdater.autoDownload = true;
    autoUpdater.autoInstallOnAppQuit = true;
    autoUpdater.allowPrerelease = false;

    autoUpdater.on('checking-for-update', () => {
        sendUpdateStatus('checking');
    });
    autoUpdater.on('update-available', (info) => {
        sendUpdateStatus('available', { updateInfo: normalizeUpdateInfo(info) });
    });
    autoUpdater.on('update-not-available', (info) => {
        sendUpdateStatus('not-available', { updateInfo: normalizeUpdateInfo(info) });
    });
    autoUpdater.on('download-progress', (progress) => {
        sendUpdateStatus('downloading', {
            percent: Math.round(Number(progress && progress.percent || 0)),
            transferred: progress && progress.transferred || 0,
            total: progress && progress.total || 0
        });
    });
    autoUpdater.on('error', (err) => {
        sendUpdateStatus('error', { error: getUpdateErrorMessage(err) });
    });
    autoUpdater.on('update-downloaded', (info) => {
        sendUpdateStatus('downloaded', {
            updateInfo: normalizeUpdateInfo(info),
            autoInstall: true
        });
        installDownloadedUpdateSafely(info).catch((err) => {
            logElectronLifecycle('auto-update-auto-install-error', err);
        });
    });
    return true;
}

async function checkForUpdates(manual = false) {
    const unavailableReason = getUpdateUnavailableReason();
    if (unavailableReason) {
        const result = {
            success: false,
            skipped: true,
            reason: unavailableReason
        };
        sendUpdateStatus('skipped', { reason: result.reason });
        return result;
    }
    if (!autoUpdater) {
        const result = {
            success: false,
            skipped: true,
            reason: 'electron-updater nao esta disponivel neste pacote.'
        };
        sendUpdateStatus('skipped', { reason: result.reason });
        return result;
    }
    const feedStatus = configureAutoUpdaterFeed();
    if (!feedStatus.success) {
        const result = {
            success: false,
            skipped: true,
            reason: feedStatus.reason || 'Canal de atualizacao nao configurado.'
        };
        sendUpdateStatus('skipped', { reason: result.reason });
        return result;
    }
    if (updateCheckInProgress) {
        sendUpdateStatus('checking', { reason: 'Verificacao de atualizacao ja em andamento.' });
        return { success: true, checking: true };
    }

    registerAutoUpdateEvents();
    updateCheckInProgress = true;
    try {
        const result = await withUpdateCheckTimeout(
            autoUpdater.checkForUpdates(),
            AUTO_UPDATE_CHECK_TIMEOUT_MS,
            'Tempo esgotado ao consultar atualizacao. Confira a internet ou se a release foi publicada no GitHub.'
        );
        const updateInfo = normalizeUpdateInfo(result && result.updateInfo);
        const currentVersion = app.getVersion();
        const latestVersion = updateInfo && updateInfo.version ? updateInfo.version : currentVersion;
        const hasNewVersion = !!(updateInfo && updateInfo.version && updateInfo.version !== currentVersion);
        if (manual && hasNewVersion) {
            sendUpdateStatus('manual-update-available', {
                currentVersion,
                latestVersion,
                updateInfo
            });
        } else if (manual) {
            sendUpdateStatus('up-to-date', {
                currentVersion,
                latestVersion,
                updateInfo
            });
        }
        return {
            success: true,
            currentVersion,
            latestVersion,
            upToDate: !hasNewVersion,
            available: hasNewVersion,
            updateInfo
        };
    } catch (err) {
        const message = getUpdateErrorMessage(err);
        sendUpdateStatus('error', { error: message });
        return {
            success: false,
            error: message
        };
    } finally {
        updateCheckInProgress = false;
    }
}

function scheduleAutoUpdateCheck() {
    if (process.env.JK_DISABLE_AUTO_UPDATE === '1') {
        logElectronLifecycle('auto-update-disabled-by-env');
        return;
    }
    if (!app.isPackaged || !autoUpdater) {
        logElectronLifecycle('auto-update-skipped', {
            packaged: app.isPackaged,
            updaterAvailable: !!autoUpdater
        });
        return;
    }
    const unavailableReason = getUpdateUnavailableReason();
    if (unavailableReason) {
        logElectronLifecycle('auto-update-skipped', { reason: unavailableReason });
        return;
    }
    const feedStatus = configureAutoUpdaterFeed();
    if (!feedStatus.success) {
        logElectronLifecycle('auto-update-skipped', { reason: feedStatus.reason || 'Canal de atualizacao nao configurado.' });
        return;
    }
    registerAutoUpdateEvents();
    const timer = setTimeout(() => {
        checkForUpdates(false).catch((err) => {
            sendUpdateStatus('error', { error: getUpdateErrorMessage(err) });
        });
    }, 8000);
    if (typeof timer.unref === 'function') timer.unref();
}

function getMlSession() {
    if (!mlSession) {
        mlSession = session.fromPartition(JK_BROWSER_SESSION_PARTITION);
    }
    return mlSession;
}

function getBrowserSessionPartition() {
    return JK_BROWSER_SESSION_PARTITION;
}

async function flushPersistentSessions() {
    const sessions = [session.defaultSession, getMlSession()];
    await Promise.all(sessions.map(async (ses) => {
        try {
            if (ses && typeof ses.flushStorageData === 'function') {
                await ses.flushStorageData();
            }
        } catch (err) {
            console.warn('[Sessao] Falha ao salvar dados persistentes:', err && err.message ? err.message : err);
        }
    }));
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

function getBundledLocalBackendDir() {
    const candidates = [
        process.env.JK_LOCAL_BACKEND_SOURCE_DIR,
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

function getLocalBackendFirebaseEnv(localAppDir) {
    for (const candidate of getFirebaseServiceAccountCandidates(localAppDir)) {
        if (!fs.existsSync(candidate)) continue;
        const account = readFirebaseServiceAccount(candidate);
        if (!account) continue;
        return {
            JK_ACCESS_BACKEND: 'firebase',
            FIREBASE_SERVICE_ACCOUNT_FILE: candidate,
            ...(account.project_id ? { FIREBASE_PROJECT_ID: String(account.project_id) } : {})
        };
    }
    return { JK_ACCESS_BACKEND: 'auto' };
}

function shouldSkipBackendCopyEntry(name, fullPath) {
    const lower = String(name || '').toLowerCase();
    if (
        lower === '.git' ||
        lower === '.venv' ||
        lower === 'node_modules' ||
        lower === 'electron_app' ||
        lower === 'info' ||
        lower === 'logs' ||
        lower === 'backups' ||
        lower === '__pycache__' ||
        lower.endsWith('.jkcred') ||
        lower.startsWith('.env')
    ) {
        return true;
    }
    try {
        return fs.statSync(fullPath).isDirectory() && lower.startsWith('dist');
    } catch (_err) {
        return false;
    }
}

function copyDirectoryRecursive(sourceDir, targetDir) {
    fs.mkdirSync(targetDir, { recursive: true });
    const entries = fs.readdirSync(sourceDir, { withFileTypes: true });
    for (const entry of entries) {
        const sourcePath = path.join(sourceDir, entry.name);
        const targetPath = path.join(targetDir, entry.name);
        if (shouldSkipBackendCopyEntry(entry.name, sourcePath)) {
            continue;
        }
        if (entry.isDirectory()) {
            copyDirectoryRecursive(sourcePath, targetPath);
            continue;
        }
        if (entry.isFile()) {
            fs.mkdirSync(path.dirname(targetPath), { recursive: true });
            fs.copyFileSync(sourcePath, targetPath);
        }
    }
}

function syncBundledLocalBackend() {
    const runtimeDir = getLocalBackendRuntimeDir();
    const bundledDir = getBundledLocalBackendDir();
    if (!bundledDir) {
        throw new Error('Backend local nao foi encontrado no pacote.');
    }
    if (path.resolve(runtimeDir) !== path.resolve(bundledDir)) {
        copyDirectoryRecursive(bundledDir, runtimeDir);
    }
    fs.mkdirSync(path.join(runtimeDir, 'info'), { recursive: true });
    fs.mkdirSync(path.join(runtimeDir, 'logs'), { recursive: true });
    return runtimeDir;
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

function localBackendHealthCompatible(health) {
    if (!health || health.ok !== true) return false;
    const backendVersion = String(health.appVersion || '').replace(/^v/i, '').trim();
    const desktopVersion = String(app.getVersion() || '').replace(/^v/i, '').trim();
    return !!backendVersion && backendVersion === desktopVersion;
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

function sanitizeMarkerVersion(value) {
    return String(value || 'dev').replace(/[^a-zA-Z0-9._-]+/g, '_');
}

function cmdValue(value) {
    return String(value || '').replace(/"/g, '');
}

function writeLocalBackendLauncher(localAppDir) {
    const infoDir = path.join(localAppDir, 'info');
    const firebaseEnv = getLocalBackendFirebaseEnv(localAppDir);
    const launcherPath = path.join(JK_ELECTRON_USER_DATA_DIR, 'start-local-backend.cmd');
    const logPath = path.join(localAppDir, 'logs', 'local_backend.log');
    const depsMarker = `.venv\\.jk_deps_${sanitizeMarkerVersion(app.getVersion())}.ok`;
    const localCallback = `http://127.0.0.1:${JK_LOCAL_BACKEND_PORT}/auth/callback`;
    const lines = [
        '@echo off',
        'setlocal',
        `cd /d "${cmdValue(localAppDir)}"`,
        'if not exist "logs" mkdir "logs"',
        'if not exist "info" mkdir "info"',
        `set "LOG_FILE=${cmdValue(logPath)}"`,
        `set "JK_INFO_DIR=${cmdValue(infoDir)}"`,
        `set "JK_REDIRECT_URI=${localCallback}"`,
        `set "JK_BLING_REDIRECT_URI=${localCallback}"`,
        `set "PROMO_WORKER_URL=http://127.0.0.1:${JK_PROMO_WORKER_PORT}"`,
        `set "JK_APP_VERSION=${cmdValue(app.getVersion())}"`,
        `set "JK_ACCESS_BACKEND=${cmdValue(firebaseEnv.JK_ACCESS_BACKEND || 'auto')}"`,
        ...(firebaseEnv.FIREBASE_SERVICE_ACCOUNT_FILE ? [
            `set "FIREBASE_SERVICE_ACCOUNT_FILE=${cmdValue(firebaseEnv.FIREBASE_SERVICE_ACCOUNT_FILE)}"`
        ] : []),
        ...(firebaseEnv.FIREBASE_PROJECT_ID ? [
            `set "FIREBASE_PROJECT_ID=${cmdValue(firebaseEnv.FIREBASE_PROJECT_ID)}"`
        ] : []),
        'set "PYTHONUNBUFFERED=1"',
        'set "PYTHONUTF8=1"',
        'echo.>> "%LOG_FILE%"',
        'echo ==== JK Sistema local backend %date% %time% ====>> "%LOG_FILE%"',
        'if not exist ".venv\\Scripts\\python.exe" (',
        '  where py >nul 2>nul',
        '  if not errorlevel 1 py -3 -m venv ".venv" >> "%LOG_FILE%" 2>&1',
        ')',
        'if not exist ".venv\\Scripts\\python.exe" (',
        '  where python >nul 2>nul',
        '  if not errorlevel 1 python -m venv ".venv" >> "%LOG_FILE%" 2>&1',
        ')',
        'if not exist ".venv\\Scripts\\python.exe" (',
        '  echo Python 3 nao encontrado. Instale Python 3 e abra o JK Sistema novamente.>> "%LOG_FILE%"',
        '  exit /b 1',
        ')',
        'set "PYTHON_EXE=.venv\\Scripts\\python.exe"',
        `if not exist "${depsMarker}" (`,
        '  "%PYTHON_EXE%" -m pip install --disable-pip-version-check --upgrade pip setuptools wheel >> "%LOG_FILE%" 2>&1',
        '  if errorlevel 1 exit /b %errorlevel%',
        '  "%PYTHON_EXE%" -m pip install --disable-pip-version-check -r requirements.txt >> "%LOG_FILE%" 2>&1',
        '  if errorlevel 1 exit /b %errorlevel%',
        '  "%PYTHON_EXE%" -m pip uninstall -y fitz >> "%LOG_FILE%" 2>&1',
        `  echo ok> "${depsMarker}"`,
        ')',
        `"%PYTHON_EXE%" -m uvicorn backend_api:app --host 127.0.0.1 --port ${JK_LOCAL_BACKEND_PORT} >> "%LOG_FILE%" 2>&1`
    ];
    fs.writeFileSync(launcherPath, `${lines.join('\r\n')}\r\n`, 'utf8');
    return launcherPath;
}

function ensureLocalBackendStarted() {
    if (localBackendStartupPromise) {
        return localBackendStartupPromise;
    }

    localBackendStartupPromise = (async () => {
        const localAppDir = syncBundledLocalBackend();
        const launcherPath = writeLocalBackendLauncher(localAppDir);
        const firebaseEnv = getLocalBackendFirebaseEnv(localAppDir);

        if (await isTcpPortOpen(JK_LOCAL_BACKEND_PORT)) {
            const health = await fetchLocalBackendJson('/health');
            if (localBackendHealthCompatible(health)) {
                logElectronLifecycle('local-backend-already-running', { port: JK_LOCAL_BACKEND_PORT, health });
                return { success: true, alreadyRunning: true, port: JK_LOCAL_BACKEND_PORT };
            }
            logElectronLifecycle('local-backend-stale-restart', {
                port: JK_LOCAL_BACKEND_PORT,
                currentVersion: app.getVersion(),
                health
            });
            await stopProcessListeningOnPort(JK_LOCAL_BACKEND_PORT);
            await waitForTcpPortClosed(JK_LOCAL_BACKEND_PORT);
        }

        logElectronLifecycle('local-backend-starting', { localAppDir, launcherPath });
        const child = spawn('cmd.exe', ['/d', '/c', launcherPath], {
            cwd: localAppDir,
            env: {
                ...process.env,
                ...firebaseEnv,
                JK_INFO_DIR: path.join(localAppDir, 'info'),
                JK_REDIRECT_URI: `http://127.0.0.1:${JK_LOCAL_BACKEND_PORT}/auth/callback`,
                JK_BLING_REDIRECT_URI: `http://127.0.0.1:${JK_LOCAL_BACKEND_PORT}/auth/callback`,
                PROMO_WORKER_URL: `http://127.0.0.1:${JK_PROMO_WORKER_PORT}`,
                JK_APP_VERSION: app.getVersion(),
                PYTHONUNBUFFERED: '1',
                PYTHONUTF8: '1'
            },
            stdio: 'ignore',
            windowsHide: true
        });
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
                reject(new Error(`Servidor local finalizou antes de iniciar. Codigo: ${code ?? ''} ${signal || ''}`.trim()));
            });
        });
        await Promise.race([
            waitForTcpPortOpen(JK_LOCAL_BACKEND_PORT, 180000),
            exitPromise
        ]);
        logElectronLifecycle('local-backend-ready', { port: JK_LOCAL_BACKEND_PORT });
        return { success: true, localAppDir, port: JK_LOCAL_BACKEND_PORT };
    })().catch((err) => {
        localBackendStartupPromise = null;
        throw err;
    });

    return localBackendStartupPromise;
}

function stopLocalBackend() {
    if (!localBackendProcess || !localBackendProcess.pid) {
        return;
    }
    try {
        spawn('taskkill.exe', ['/PID', String(localBackendProcess.pid), '/T', '/F'], {
            stdio: 'ignore',
            windowsHide: true
        }).unref();
    } catch (err) {
        logElectronLifecycle('local-backend-stop-error', err);
    }
    localBackendProcess = null;
}

function getCredentialsScriptPath() {
    const candidates = [
        path.join(getAppRootDir(), 'scripts', 'credenciais.js'),
        path.join(process.resourcesPath || '', 'scripts', 'credenciais.js'),
        path.join(path.resolve(__dirname, '..'), 'scripts', 'credenciais.js')
    ].filter(Boolean);
    for (const candidate of candidates) {
        if (fs.existsSync(candidate)) return candidate;
    }
    return '';
}

function getBundledPrivateCredentialsPaths() {
    const candidates = [
        process.env.JK_PRIVATE_CREDENTIALS_PACKAGE,
        path.join(getAppRootDir(), 'private', JK_PRIVATE_CREDENTIALS_FILE_NAME),
        path.join(getAppRootDir(), JK_PRIVATE_CREDENTIALS_FILE_NAME),
        path.join(process.resourcesPath || '', 'private', JK_PRIVATE_CREDENTIALS_FILE_NAME)
    ].filter(Boolean);
    const packagePaths = [];
    for (const candidate of candidates) {
        const resolved = path.resolve(candidate);
        if (fs.existsSync(resolved)) packagePaths.push(resolved);
    }
    const privateDirs = [
        path.join(getAppRootDir(), 'private'),
        path.join(process.resourcesPath || '', 'private')
    ].filter(Boolean);
    for (const privateDir of privateDirs) {
        try {
            const entries = fs.readdirSync(privateDir, { withFileTypes: true });
            for (const entry of entries) {
                if (entry.isFile() && entry.name.toLowerCase().endsWith('.jkcred')) {
                    packagePaths.push(path.join(privateDir, entry.name));
                }
            }
        } catch (_err) {}
    }
    return Array.from(new Set(packagePaths.map((item) => path.resolve(item)))).sort((a, b) => a.localeCompare(b));
}

function getPrivateCredentialsImportMarker() {
    return path.join(JK_ELECTRON_USER_DATA_DIR, '.private_credentials_imported');
}

function promptPrivateCredentialsPassword(parentWindow) {
    return new Promise((resolve) => {
        const channel = `private-credentials-password-${Date.now()}-${Math.random().toString(16).slice(2)}`;
        const modal = new BrowserWindow({
            width: 440,
            height: 260,
            title: 'Credenciais privadas',
            parent: parentWindow && !parentWindow.isDestroyed() ? parentWindow : undefined,
            modal: !!(parentWindow && !parentWindow.isDestroyed()),
            resizable: false,
            minimizable: false,
            maximizable: false,
            autoHideMenuBar: true,
            webPreferences: {
                nodeIntegration: true,
                contextIsolation: false
            }
        });
        let settled = false;
        const finish = (value) => {
            if (settled) return;
            settled = true;
            ipcMain.removeAllListeners(channel);
            try {
                if (!modal.isDestroyed()) modal.close();
            } catch (_err) {}
            resolve(value);
        };
        ipcMain.once(channel, (_event, payload) => {
            const action = payload && payload.action;
            if (action === 'ok') {
                finish(String(payload.password || ''));
            } else {
                finish(null);
            }
        });
        modal.on('closed', () => finish(null));
        const html = `<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>Credenciais privadas</title>
    <style>
        * { box-sizing: border-box; }
        body { margin: 0; min-height: 100vh; background: #07111f; color: #eef6ff; font-family: Inter, Segoe UI, Arial, sans-serif; display: grid; place-items: center; }
        main { width: 100%; padding: 24px; }
        h1 { margin: 0 0 8px; font-size: 20px; }
        p { margin: 0 0 18px; color: #b8c9dc; line-height: 1.4; }
        input { width: 100%; border: 1px solid rgba(130, 180, 230, 0.35); border-radius: 8px; padding: 12px; background: rgba(255,255,255,0.08); color: #fff; outline: none; font-size: 15px; }
        .actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
        button { border: 0; border-radius: 8px; padding: 10px 14px; color: #fff; background: #2387d8; font-weight: 700; cursor: pointer; }
        button.secondary { background: rgba(255,255,255,0.12); }
    </style>
</head>
<body>
    <main>
        <h1>Importar dados privados</h1>
        <p>Digite a senha do pacote criptografado para restaurar as credenciais e dados locais.</p>
        <input id="senha" type="password" autocomplete="current-password" autofocus>
        <div class="actions">
            <button class="secondary" id="cancelar" type="button">Depois</button>
            <button id="importar" type="button">Importar</button>
        </div>
    </main>
    <script>
        const { ipcRenderer } = require('electron');
        const channel = ${JSON.stringify(channel)};
        const senha = document.getElementById('senha');
        document.getElementById('cancelar').addEventListener('click', () => ipcRenderer.send(channel, { action: 'cancel' }));
        document.getElementById('importar').addEventListener('click', () => ipcRenderer.send(channel, { action: 'ok', password: senha.value || '' }));
        senha.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') ipcRenderer.send(channel, { action: 'ok', password: senha.value || '' });
            if (event.key === 'Escape') ipcRenderer.send(channel, { action: 'cancel' });
        });
    </script>
</body>
</html>`;
        modal.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`).catch(() => finish(null));
    });
}

function importCredentialPackage(importFile, password) {
    const scriptPath = getCredentialsScriptPath();
    if (!scriptPath) {
        throw new Error('scripts/credenciais.js nao foi encontrado no pacote.');
    }
    const localAppDir = syncBundledLocalBackend();
    const logPath = path.join(localAppDir, 'logs', 'private_credentials_import.log');
    fs.mkdirSync(path.dirname(logPath), { recursive: true });
    return new Promise((resolve, reject) => {
        const child = spawn(process.execPath, [
            scriptPath,
            'import',
            '--in',
            importFile,
            '--force',
            '--no-backup'
        ], {
            cwd: localAppDir,
            env: {
                ...process.env,
                ELECTRON_RUN_AS_NODE: '1',
                JK_CREDENTIALS_ROOT: localAppDir,
                JK_CREDENTIALS_PASSWORD: password
            },
            windowsHide: true
        });
        const logStream = fs.createWriteStream(logPath, { flags: 'a' });
        logStream.write(`\n==== Importacao privada ${new Date().toISOString()} ====\n`);
        child.stdout.on('data', (chunk) => logStream.write(chunk));
        child.stderr.on('data', (chunk) => logStream.write(chunk));
        child.once('error', (err) => {
            logStream.end();
            reject(err);
        });
        child.once('exit', (code, signal) => {
            logStream.end();
            if (code === 0) {
                resolve({ success: true });
            } else {
                reject(new Error(`Importacao falhou. Codigo: ${code ?? ''} ${signal || ''}`.trim()));
            }
        });
    });
}

function writeCredentialsImportRunner(importFile, options = {}) {
    const scriptPath = getCredentialsScriptPath();
    if (!scriptPath) {
        throw new Error('scripts/credenciais.js nao foi encontrado no pacote.');
    }
    const localAppDir = syncBundledLocalBackend();
    const runnerName = options.runnerName || 'importar-credenciais-local.cmd';
    const runnerPath = path.join(JK_ELECTRON_USER_DATA_DIR, runnerName);
    const markerPath = options.markerPath || '';
    const lines = [
        '@echo off',
        'setlocal',
        `cd /d "${cmdValue(localAppDir)}"`,
        'set "ELECTRON_RUN_AS_NODE=1"',
        `set "JK_CREDENTIALS_ROOT=${cmdValue(localAppDir)}"`,
        'echo Importando credenciais para o JK Sistema local.',
        'echo.',
        `"${cmdValue(process.execPath)}" "${cmdValue(scriptPath)}" import --in "${cmdValue(importFile)}" --force`,
        'set "IMPORT_EXIT=%ERRORLEVEL%"',
        'echo.',
        'if "%IMPORT_EXIT%"=="0" (',
        '  echo Importacao concluida.',
        ...(markerPath ? [`  echo ok> "${cmdValue(markerPath)}"`] : []),
        ') else (',
        '  echo Importacao falhou. Confira a senha e tente novamente.',
        ')',
        'echo.',
        'echo Feche esta janela para continuar.',
        'pause',
        'exit /b %IMPORT_EXIT%'
    ];
    fs.writeFileSync(runnerPath, `${lines.join('\r\n')}\r\n`, 'utf8');
    return { runnerPath, localAppDir };
}

function launchCredentialsImportRunner(runnerPath, localAppDir, options = {}) {
    if (process.platform !== 'win32') {
        return shell.openPath(runnerPath).then((result) => {
            if (result) throw new Error(result);
            return { code: 0 };
        });
    }

    const waitFlag = options.wait ? '/wait ' : '';
    const child = spawn('cmd.exe', ['/d', '/c', `start ${waitFlag}"" "${cmdValue(runnerPath)}"`], {
        cwd: localAppDir,
        detached: !options.wait,
        stdio: 'ignore',
        windowsHide: !!options.wait
    });

    if (!options.wait) {
        child.unref();
        return Promise.resolve({ code: 0 });
    }

    return new Promise((resolve, reject) => {
        child.once('error', reject);
        child.once('exit', (code, signal) => {
            resolve({ code, signal });
        });
    });
}

async function openLocalCredentialsImporter() {
    const scriptPath = getCredentialsScriptPath();
    if (!scriptPath) {
        throw new Error('scripts/credenciais.js nao foi encontrado no pacote.');
    }

    const selected = await dialog.showOpenDialog({
        title: 'Selecionar pacote de credenciais',
        properties: ['openFile'],
        filters: [
            { name: 'Credenciais JK', extensions: ['jkcred'] },
            { name: 'Todos os arquivos', extensions: ['*'] }
        ]
    });
    if (selected.canceled || !selected.filePaths || !selected.filePaths[0]) {
        return { success: false, canceled: true };
    }

    const localAppDir = syncBundledLocalBackend();
    const importFile = selected.filePaths[0];
    const runnerPath = path.join(JK_ELECTRON_USER_DATA_DIR, 'importar-credenciais-local.cmd');
    const lines = [
        '@echo off',
        'setlocal',
        `cd /d "${cmdValue(localAppDir)}"`,
        'set "ELECTRON_RUN_AS_NODE=1"',
        `set "JK_CREDENTIALS_ROOT=${cmdValue(localAppDir)}"`,
        'echo Importando credenciais para o JK Sistema local.',
        'echo.',
        `"${cmdValue(process.execPath)}" "${cmdValue(scriptPath)}" import --in "${cmdValue(importFile)}" --force`,
        'echo.',
        'echo Se a importacao terminou sem erro, feche esta janela e entre novamente no app.',
        'pause'
    ];
    fs.writeFileSync(runnerPath, `${lines.join('\r\n')}\r\n`, 'utf8');

    if (process.platform !== 'win32') {
        const result = await shell.openPath(runnerPath);
        if (result) throw new Error(result);
        return {
            success: true,
            path: runnerPath,
            message: 'Importador aberto. Informe a senha para restaurar as credenciais locais.'
        };
    }

    const child = spawn('cmd.exe', ['/d', '/c', `start "" "${cmdValue(runnerPath)}"`], {
        cwd: localAppDir,
        detached: true,
        stdio: 'ignore',
        windowsHide: false
    });
    child.unref();
    return {
        success: true,
        path: runnerPath,
        message: 'Importador aberto. Informe a senha para restaurar as credenciais locais.'
    };
}

async function openCredentialsImporter() {
    return await openLocalCredentialsImporter();
    const batPath = getImportCredentialsBatPath();
    if (!batPath) {
        throw new Error('ImportarCredenciais.bat não foi encontrado na pasta do sistema.');
    }
    if (process.platform === 'win32') {
        const child = spawn('cmd.exe', ['/d', '/c', 'start', '', batPath], {
            cwd: path.dirname(batPath),
            detached: true,
            stdio: 'ignore',
            windowsHide: false,
            env: {
                ...process.env,
                JK_NODE_BIN: process.execPath
            }
        });
        child.unref();
    } else {
        const result = await shell.openPath(batPath);
        if (result) {
            throw new Error(result);
        }
    }
    return {
        success: true,
        path: batPath,
        message: 'Importador aberto. Siga as instruções na janela para restaurar as credenciais.'
    };
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

function renderLocalBackendStartupScreen(win, options = {}) {
    if (!win || win.isDestroyed()) return;
    const error = options.error ? String(options.error) : '';
    const detail = error
        ? `Nao consegui iniciar o servidor local. Veja o log em ${path.join(getLocalBackendRuntimeDir(), 'logs', 'local_backend.log')}`
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
        p { margin: 0; color: #b8c9dc; line-height: 1.5; }
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
        logElectronLifecycle('local-backend-startup-screen-error', err);
    });
}

async function maybeImportBundledPrivateCredentials(win) {
    const packagePaths = getBundledPrivateCredentialsPaths();
    if (!packagePaths.length) {
        return { imported: false, reason: 'no-package' };
    }

    const markerPath = getPrivateCredentialsImportMarker();
    if (fs.existsSync(markerPath)) {
        return { imported: false, reason: 'already-imported' };
    }

    const response = await dialog.showMessageBox(win, {
        type: 'question',
        buttons: ['Importar agora', 'Depois'],
        defaultId: 0,
        cancelId: 1,
        title: 'Credenciais privadas encontradas',
        message: 'Este instalador privado inclui pacotes criptografados de credenciais e dados locais.',
        detail: 'Informe a senha na janela que abrir para restaurar os dados antes de entrar no sistema.'
    });
    if (response.response !== 0) {
        return { imported: false, reason: 'skipped' };
    }

    let password = await promptPrivateCredentialsPassword(win);
    if (!password) {
        return { imported: false, reason: 'password-skipped' };
    }

    while (true) {
        try {
            for (let index = 0; index < packagePaths.length; index += 1) {
                renderLocalBackendStartupScreen(win, {
                    detail: `Importando pacote privado ${index + 1} de ${packagePaths.length}. Aguarde, isso pode levar alguns minutos.`
                });
                await importCredentialPackage(packagePaths[index], password);
            }
            fs.writeFileSync(markerPath, new Date().toISOString(), 'utf8');
            break;
        } catch (err) {
            logElectronLifecycle('private-credentials-import-failed', err);
            const retry = await dialog.showMessageBox(win, {
                type: 'warning',
                buttons: ['Tentar novamente', 'Continuar sem importar'],
                defaultId: 0,
                cancelId: 1,
                title: 'Falha ao importar credenciais',
                message: 'Nao foi possivel importar o pacote privado.',
                detail: 'Confira a senha e tente novamente. O log fica na pasta local_app\\logs.'
            });
            if (retry.response !== 0) {
                return { imported: false, reason: 'failed' };
            }
            password = await promptPrivateCredentialsPassword(win);
            if (!password) {
                return { imported: false, reason: 'password-skipped' };
            }
        }
    }

    if (!fs.existsSync(markerPath)) {
        await dialog.showMessageBox(win, {
            type: 'warning',
            buttons: ['Continuar'],
            title: 'Credenciais nao importadas',
            message: 'A importacao privada nao foi concluida.',
            detail: 'O sistema vai abrir mesmo assim. Voce tambem pode importar depois pelo botao Importar credenciais no sidebar.'
        });
        return { imported: false, reason: 'not-finished' };
    }

    return { imported: true };
}

function loadConfiguredApp(win, clientConfig = null) {
    const config = clientConfig || loadClientConfig();
    logElectronLifecycle('client-config-loaded', { appUrl: config.appUrl, configPath: config.configPath });
    if (isLocalBackendAppUrl(config.appUrl)) {
        renderLocalBackendStartupScreen(win);
        maybeImportBundledPrivateCredentials(win)
            .then(() => ensureLocalBackendStarted())
            .then(() => {
                if (!win || win.isDestroyed()) return;
                loadElectronTabbedShell(win, appendNoCache(config.appUrl));
            })
            .catch((err) => {
                logElectronLifecycle('local-backend-start-failed', err);
                renderLocalBackendStartupScreen(win, { error: err && err.message ? err.message : String(err) });
            });
        return;
    }
    loadElectronTabbedShell(win, appendNoCache(config.appUrl));
}

function renderServerSetupScreen(win, clientConfig) {
    if (!win || win.isDestroyed()) return;
    const currentUrl = isPlaceholderAppUrl(clientConfig && clientConfig.appUrl) ? '' : String(clientConfig.appUrl || '');
    const configPath = String((clientConfig && clientConfig.configPath) || '');
    const html = `<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>Configurar JK Sistema Cliente</title>
    <style>
        * { box-sizing: border-box; }
        body {
            margin: 0;
            min-height: 100vh;
            display: grid;
            place-items: center;
            background: #eef3f9;
            color: #0f172a;
            font-family: "Segoe UI", Arial, sans-serif;
        }
        main {
            width: min(560px, calc(100vw - 32px));
            background: #ffffff;
            border: 1px solid #d5e2f2;
            border-radius: 10px;
            box-shadow: 0 18px 42px rgba(15, 23, 42, 0.14);
            padding: 26px;
        }
        h1 { margin: 0 0 8px; font-size: 23px; }
        p { margin: 0 0 18px; color: #475569; line-height: 1.45; }
        label { display: block; font-size: 13px; font-weight: 800; margin-bottom: 7px; }
        input {
            width: 100%;
            height: 42px;
            border: 1px solid #b8c7dc;
            border-radius: 8px;
            padding: 0 12px;
            font-size: 14px;
        }
        input:focus {
            outline: 2px solid rgba(37, 99, 235, 0.18);
            border-color: #2563eb;
        }
        .actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
        button {
            height: 38px;
            border: 0;
            border-radius: 8px;
            background: #2563eb;
            color: white;
            cursor: pointer;
            font-weight: 800;
            padding: 0 16px;
        }
        button:disabled { cursor: not-allowed; opacity: .55; }
        .muted { margin-top: 14px; color: #64748b; font-size: 12px; word-break: break-all; }
        .error { color: #b91c1c; font-size: 13px; min-height: 18px; margin-top: 10px; }
    </style>
</head>
<body>
    <main>
        <h1>Configurar servidor</h1>
        <p>Informe o endereco do servidor do JK Sistema. Essa configuracao sera salva neste computador.</p>
        <form id="form">
            <label for="serverUrl">Endereco do servidor</label>
            <input id="serverUrl" type="url" placeholder="https://seu-servidor.com/dashboard.html" value="${currentUrl.replace(/"/g, '&quot;')}" required>
            <div id="error" class="error"></div>
            <div class="actions">
                <button id="save" type="submit">Salvar e abrir</button>
            </div>
        </form>
        <div class="muted">Arquivo de configuracao: ${configPath.replace(/</g, '&lt;')}</div>
    </main>
    <script>
        const form = document.getElementById('form');
        const input = document.getElementById('serverUrl');
        const errorEl = document.getElementById('error');
        const saveBtn = document.getElementById('save');
        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            errorEl.textContent = '';
            saveBtn.disabled = true;
            saveBtn.textContent = 'Salvando...';
            try {
                const value = input.value.trim();
                await window.electronAPI.saveClientConfig(value);
                saveBtn.textContent = 'Abrindo...';
            } catch (err) {
                errorEl.textContent = err && err.message ? err.message : String(err);
                saveBtn.disabled = false;
                saveBtn.textContent = 'Salvar e abrir';
            }
        });
        setTimeout(() => input.focus(), 80);
    </script>
</body>
</html>`;
    win.loadURL(`data:text/html;charset=UTF-8,${encodeURIComponent(html)}`).catch((err) => {
        console.error('Falha ao carregar tela de configuracao:', err);
    });
}

function getChromeExtensionsRoots() {
    const roots = [
        process.env.JK_CHROME_EXTENSIONS_DIR,
        path.join(getAppRootDir(), 'extensoes_chrome'),
        path.join(__dirname, 'extensoes_chrome')
    ].filter(Boolean);
    return Array.from(new Set(roots.map(root => path.resolve(root))));
}

function compareVersionStrings(left, right) {
    const a = String(left || '').split('.').map(value => Number(value) || 0);
    const b = String(right || '').split('.').map(value => Number(value) || 0);
    const size = Math.max(a.length, b.length);
    for (let i = 0; i < size; i += 1) {
        const diff = (a[i] || 0) - (b[i] || 0);
        if (diff) return diff;
    }
    return 0;
}

function findInstalledChromeExtensionVersions(extensionId) {
    const chromeUserData = process.env.LOCALAPPDATA
        ? path.join(process.env.LOCALAPPDATA, 'Google', 'Chrome', 'User Data')
        : '';
    if (!chromeUserData || !fs.existsSync(chromeUserData)) return [];

    const candidates = [];
    for (const profile of fs.readdirSync(chromeUserData, { withFileTypes: true })) {
        if (!profile.isDirectory()) continue;
        if (profile.name !== 'Default' && !/^Profile\s+\d+$/i.test(profile.name)) continue;
        const extensionRoot = path.join(chromeUserData, profile.name, 'Extensions', extensionId);
        if (!fs.existsSync(extensionRoot)) continue;
        for (const versionEntry of fs.readdirSync(extensionRoot, { withFileTypes: true })) {
            if (!versionEntry.isDirectory()) continue;
            const dir = path.join(extensionRoot, versionEntry.name);
            const manifestPath = path.join(dir, 'manifest.json');
            if (!fs.existsSync(manifestPath)) continue;
            let stat = null;
            try { stat = fs.statSync(manifestPath); } catch (_err) {}
            candidates.push({
                dir,
                profile: profile.name,
                version: versionEntry.name.replace(/_\d+$/i, ''),
                mtimeMs: stat ? stat.mtimeMs : 0
            });
        }
    }

    return candidates
        .sort((left, right) => {
            const versionDiff = compareVersionStrings(right.version, left.version);
            if (versionDiff) return versionDiff;
            return (right.mtimeMs || 0) - (left.mtimeMs || 0);
        })
        .map(item => item.dir);
}

function getChromeExtensionManifestIdentity(extensionDir) {
    try {
        const manifest = JSON.parse(fs.readFileSync(path.join(extensionDir, 'manifest.json'), 'utf8'));
        return manifest.key || manifest.update_url && manifest.name || manifest.name || extensionDir;
    } catch (_err) {
        return extensionDir;
    }
}

function findUnpackedChromeExtensions() {
    const candidates = [];
    const explicitRoots = process.env.JK_CHROME_EXTENSIONS_DIR ? [path.resolve(process.env.JK_CHROME_EXTENSIONS_DIR)] : [];
    const chromeInstalledExtensions = findInstalledChromeExtensionVersions(AVANTPRO_CHROME_EXTENSION_ID);

    for (const root of [...explicitRoots, ...chromeInstalledExtensions, ...getChromeExtensionsRoots()]) {
        if (!fs.existsSync(root)) continue;

        const rootManifest = path.join(root, 'manifest.json');
        if (fs.existsSync(rootManifest)) {
            candidates.push(root);
            continue;
        }

        for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
            if (!entry.isDirectory()) continue;
            const dir = path.join(root, entry.name);
            if (fs.existsSync(path.join(dir, 'manifest.json'))) {
                candidates.push(dir);
            }
        }
    }

    const seen = new Set();
    const unique = [];
    for (const candidate of Array.from(new Set(candidates.map(item => path.resolve(item))))) {
        const identity = getChromeExtensionManifestIdentity(candidate);
        if (seen.has(identity)) continue;
        seen.add(identity);
        unique.push(candidate);
    }
    return unique;
}

async function loadChromeExtensionsForMlSession() {
    const ses = getMlSession();
    registerChromeExtensionSessionEvents(ses);
    const extensionDirs = findUnpackedChromeExtensions();

    if (!extensionDirs.length) {
        logElectronLifecycle('chrome-extensions-none-found');
        console.log('[Extensoes] Nenhuma extensao descompactada encontrada em extensoes_chrome.');
        return [];
    }

    const loaded = [];
    for (const extensionDir of extensionDirs) {
        try {
            const ext = await ses.loadExtension(extensionDir, { allowFileAccess: true });
            loaded.push(ext);
            logElectronLifecycle('chrome-extension-loaded-by-script', {
                id: ext && ext.id,
                name: ext && ext.name,
                path: extensionDir,
                url: ext && ext.url
            });
            console.log(`[Extensoes] Carregada: ${ext.name || ext.id} (${extensionDir})`);
        } catch (err) {
            logElectronLifecycle('chrome-extension-load-failed', {
                path: extensionDir,
                error: err && err.message ? err.message : String(err)
            });
            console.error(`[Extensoes] Falha ao carregar ${extensionDir}:`, err && err.message ? err.message : err);
        }
    }
    return loaded;
}

function registerChromeExtensionSessionEvents(ses) {
    if (!ses || chromeExtensionSessionEventsRegistered) return;
    chromeExtensionSessionEventsRegistered = true;
    ses.on('extension-loaded', (_event, ext) => {
        logElectronLifecycle('chrome-extension-loaded', {
            id: ext && ext.id,
            name: ext && ext.name,
            path: ext && ext.path,
            url: ext && ext.url
        });
    });
    ses.on('extension-ready', (_event, ext) => {
        logElectronLifecycle('chrome-extension-ready', {
            id: ext && ext.id,
            name: ext && ext.name,
            path: ext && ext.path,
            url: ext && ext.url
        });
    });
    ses.on('extension-unloaded', (_event, ext) => {
        logElectronLifecycle('chrome-extension-unloaded', {
            id: ext && ext.id,
            name: ext && ext.name,
            path: ext && ext.path,
            url: ext && ext.url
        });
    });
}

function ensureChromeExtensionsForMlSession() {
    if (!chromeExtensionsLoadPromise) {
        chromeExtensionsLoadPromise = loadChromeExtensionsForMlSession().catch((err) => {
            console.error('[Extensoes] Erro ao carregar extensoes Chrome:', err && err.message ? err.message : err);
            return [];
        });
    }
    return chromeExtensionsLoadPromise;
}

function getLoadedChromeExtensionsForMlSession() {
    const ses = getMlSession();
    if (!ses || typeof ses.getAllExtensions !== 'function') return [];
    return ses.getAllExtensions().map(ext => ({
        id: ext && ext.id,
        name: ext && ext.name,
        path: ext && ext.path,
        url: ext && ext.url
    }));
}

function getMachineInfo() {
    const hostname = os.hostname() || null;
    const interfaces = os.networkInterfaces();
    let selected = null;

    for (const name of Object.keys(interfaces)) {
        for (const iface of interfaces[name] || []) {
            if (!iface.internal && iface.mac && iface.mac !== '00:00:00:00:00:00') {
                selected = {
                    adapter: name,
                    mac: iface.mac.toUpperCase(),
                    address: iface.address || null
                };
                break;
            }
        }
        if (selected) break;
    }

    return {
        hostname,
        adapter: selected ? selected.adapter : null,
        mac: selected ? selected.mac : null,
        address: selected ? selected.address : null,
        machineId: [
            hostname ? `pc:${hostname}` : null,
            selected && selected.mac ? `mac:${selected.mac}` : null,
            selected && selected.adapter ? `rede:${selected.adapter}` : null
        ].filter(Boolean).join(' | ')
    };
}

function getMacAddress() {
    const info = getMachineInfo();
    return info && info.mac ? info.mac : null;
}

function normalizeTargetUrl(targetUrl) {
    const value = String(targetUrl || '').trim();
    if (!value) return 'https://www.mercadolivre.com.br/';
    if (/^https?:\/\//i.test(value)) return value;
    return `https://${value}`;
}

function isMercadoLivreHost(hostname) {
    const host = String(hostname || '').toLowerCase();
    return host === 'mercadolivre.com.br'
        || host.endsWith('.mercadolivre.com.br')
        || host === 'mercadolibre.com'
        || host.endsWith('.mercadolibre.com');
}

function isMercadoLivreAdUrl(targetUrl) {
    try {
        const url = new URL(normalizeTargetUrl(targetUrl));
        if (!isMercadoLivreHost(url.hostname)) return false;
        const pathAndQuery = `${url.pathname}${url.search}${url.hash}`;
        return /\/MLB-?\d{5,}/i.test(pathAndQuery)
            || /\/p\/MLB\d{5,}/i.test(pathAndQuery)
            || /(?:[?&#]|%26)(?:wid|item_id)=MLB-?\d{5,}/i.test(pathAndQuery)
            || /\/anuncios(?:\/|$)/i.test(url.pathname);
    } catch (_err) {
        return false;
    }
}

function isMercadoLivreLogoutUrl(targetUrl) {
    try {
        const url = new URL(normalizeTargetUrl(targetUrl));
        if (!isMercadoLivreHost(url.hostname)) return false;
        const text = `${url.pathname}${url.search}${url.hash}`.toLowerCase();
        return /logout|logou?t|sign[-_]?out|sair|cerrar[-_]?sesion|encerrar[-_]?sessao|end[-_]?session/.test(text);
    } catch (_err) {
        return false;
    }
}

function isAvantProAuthUrl(targetUrl) {
    try {
        const url = new URL(normalizeTargetUrl(targetUrl));
        const host = String(url.hostname || '').toLowerCase();
        const text = `${host}${url.pathname}${url.search}${url.hash}`.toLowerCase();
        return host.includes('avantpro')
            && (/auth|login|entrar|signin|oauth|callback|mercadolivre|mercadolibre/.test(text));
    } catch (_err) {
        return false;
    }
}

function isBlockedAutomationPopupUrl(targetUrl) {
    const raw = String(targetUrl || '').trim();
    if (!raw || /^about:blank$/i.test(raw) || /^data:/i.test(raw) || /^chrome-extension:/i.test(raw)) return false;
    try {
        const url = new URL(normalizeTargetUrl(raw));
        const host = String(url.hostname || '').toLowerCase();
        const text = `${host}${url.pathname}${url.search}${url.hash}`.toLowerCase();
        if (isMercadoLivreHost(host) || isAvantProAuthUrl(url.href)) return false;
        if (
            host === 'youtube.com' || host.endsWith('.youtube.com') ||
            host === 'youtu.be' ||
            host === 'whatsapp.com' || host.endsWith('.whatsapp.com') ||
            host === 'wa.me' ||
            host.includes('web.whatsapp') ||
            host.includes('hostinger')
        ) {
            return true;
        }
        return /suporte|support|ajuda|help|tutorial|introducao|introdução|curso|youtube|whatsapp|wa\.me/.test(text);
    } catch (_err) {
        return false;
    }
}

function chromeExecutableCandidates() {
    return [
        process.env.CHROME_PATH,
        process.env.GOOGLE_CHROME_SHIM,
        path.join(process.env.ProgramFiles || '', 'Google', 'Chrome', 'Application', 'chrome.exe'),
        path.join(process.env['ProgramFiles(x86)'] || '', 'Google', 'Chrome', 'Application', 'chrome.exe'),
        path.join(process.env.LOCALAPPDATA || '', 'Google', 'Chrome', 'Application', 'chrome.exe'),
        'chrome.exe',
        'chrome',
    ].filter(Boolean);
}

async function openUrlInGoogleChrome(targetUrl) {
    const url = normalizeTargetUrl(targetUrl);
    for (const candidate of chromeExecutableCandidates()) {
        try {
            if (path.isAbsolute(candidate) && candidate.toLowerCase().endsWith('.exe') && !fs.existsSync(candidate)) {
                continue;
            }
            const child = spawn(candidate, [url], {
                detached: true,
                stdio: 'ignore',
                windowsHide: false,
            });
            child.unref();
            return { success: true, url, browser: 'chrome', executable: candidate };
        } catch (_err) {}
    }
    await shell.openExternal(url);
    return { success: true, url, browser: 'default' };
}

function openMercadoLivreAdInChrome(targetUrl) {
    openUrlInGoogleChrome(targetUrl).catch((err) => {
        console.error('Falha ao abrir link de anuncio no Chrome:', err && err.message ? err.message : err);
    });
}

function normalizeMlText(value) {
    return String(value || '')
        .replace(/\\u002F/g, '/')
        .replace(/\\\//g, '/')
        .replace(/\\n/g, ' ')
        .replace(/\\t/g, ' ')
        .replace(/\\r/g, ' ')
        .replace(/&quot;/g, '"')
        .replace(/\\"/g, '"')
        .trim();
}

function parseMlQuantidade(value) {
    if (value === null || value === undefined) return null;
    const raw = String(value).trim();
    if (!raw) return null;
    const match = raw.match(/([0-9][0-9\.,]*)\s*(k|mil)?\b/i);
    if (!match) return null;
    let numeroTexto = String(match[1]).replace(/\s+/g, '');
    if (numeroTexto.includes('.') && numeroTexto.includes(',')) {
        numeroTexto = numeroTexto.lastIndexOf('.') > numeroTexto.lastIndexOf(',')
            ? numeroTexto.replace(/,/g, '')
            : numeroTexto.replace(/\./g, '').replace(',', '.');
    } else if (numeroTexto.includes(',')) {
        numeroTexto = /^\d{1,3}(?:,\d{3})+$/.test(numeroTexto)
            ? numeroTexto.replace(/,/g, '')
            : numeroTexto.replace(',', '.');
    } else if (numeroTexto.includes('.')) {
        numeroTexto = /^\d{1,3}(?:\.\d{3})+$/.test(numeroTexto)
            ? numeroTexto.replace(/\./g, '')
            : numeroTexto;
    }
    const numero = Number(numeroTexto);
    const sufixo = String(match[2] || '').toLowerCase();
    if (!Number.isFinite(numero)) return null;
    const total = (sufixo === 'k' || sufixo === 'mil') ? numero * 1000 : numero;
    return Number.isFinite(total) ? Math.round(total) : null;
}

function normalizarNomeVendedorMl(value) {
    return String(value || '')
        .replace(/^\s*(vendido\s+por|loja\s+oficial|oficial\s+loja)\s*/i, '')
        .replace(/&quot;|\\"/g, '"')
        .replace(/\s+/g, ' ')
        .trim();
}

function vendedorMlValido(valor) {
    const texto = normalizarNomeVendedorMl(valor);
    if (!texto) return false;
    if (texto.length < 2 || texto.length > 120) return false;
    if (!/[A-Za-z0-9]/.test(texto)) return false;
    if (/^\d+$/.test(texto)) return false;
    const textoBusca = normalizarNomeVendedorMl(texto)
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();
    if (!textoBusca || textoBusca.length < 2) return false;
    if (/(^|\b)(anuncio criado|an ncio criado|criado em|catalogo criado|cat logo criado|vendas produto|total vendas|quantidade vendas)(\b|$)/i.test(textoBusca)) return false;
    return !/^(vendido|vendedor|anuncio|anunci[oÃ´]o|produto|frete|envio|loja|oferta|ofertas|desconto|comprar|comprando|login|entrar|cadastro|email|senha|contato|perfil|busca|filtro|categoria|condi[cÃ§][aÃ£]o|aviso|informa[cÃ§][aÃ£]o|cria[cÃ§][aÃ£]o|valor|pre[cÃ§]o)$/i.test(textoBusca);
}

function escolherNomeVendedorMl(candidatos) {
    const itens = Array.isArray(candidatos) ? candidatos : [];
    const opcoes = [];

    for (let i = 0; i < itens.length; i += 1) {
        const item = itens[i];
        const valor = typeof item === 'string' ? item : item && item.valor;
        const prioridade = Number(item && item.prioridade) || 0;
        const nome = normalizarNomeVendedorMl(valor);
        if (!nome || !vendedorMlValido(nome)) continue;
        opcoes.push({
            nome,
            prioridade,
            score: nome.length + (/\s/.test(nome) ? 6 : 0),
            ordem: i
        });
    }

    if (!opcoes.length) return '';
    opcoes.sort((a, b) => b.prioridade - a.prioridade || b.score - a.score || a.ordem - b.ordem);
    return opcoes[0].nome;
}

function buildMlItemUrl(itemId, targetUrl) {
    const url = normalizeTargetUrl(targetUrl || '');
    if (targetUrl && /^https?:\/\//i.test(String(targetUrl))) return url;
    const cleanId = String(itemId || '').trim().toUpperCase().replace('-', '');
    if (/^MLB\d+$/.test(cleanId)) {
        return `https://produto.mercadolivre.com.br/${cleanId.replace('MLB', 'MLB-')}-_JM`;
    }
    return url;
}

function findMlItemInfoInObject(root, itemId) {
    const cleanId = String(itemId || '').trim().toUpperCase().replace('-', '');
    const dateKeys = new Set([
        'date_created',
        'dateCreated',
        'start_time',
        'startTime',
        'item_date_created',
        'creation_date',
        'creationDate',
        'listing_start_time',
        'start_date',
        'itemStartTime'
    ]);
    const sellerKeys = new Set([
        'seller_name',
        'sellerName',
        'nickname',
        'official_store_name',
        'officialStoreName'
    ]);
    const stack = [root];
    const seen = new Set();
    const info = { data_criacao: '', vendedor: '', seller_id: null, id: cleanId, vendas: null };

    while (stack.length) {
        const cur = stack.pop();
        if (!cur || typeof cur !== 'object') continue;
        if (seen.has(cur)) continue;
        seen.add(cur);
        if (seen.size > 8000) break;

        if (Array.isArray(cur)) {
            for (const item of cur) stack.push(item);
            continue;
        }

        const curId = normalizeMlText(cur.id || cur.item_id || cur.itemId || cur.itemID).toUpperCase().replace('-', '');
        const sameItem = cleanId && curId === cleanId;
        for (const [key, value] of Object.entries(cur)) {
            if (!info.data_criacao && dateKeys.has(key) && value) {
                info.data_criacao = normalizeMlText(value);
            }
            if (!info.data_criacao && key === 'date_created' && value && typeof value === 'object' && value.value) {
                info.data_criacao = normalizeMlText(value.value);
            }
            if (!info.vendedor && sellerKeys.has(key) && typeof value !== 'object' && value) {
                const vendedor = normalizarNomeVendedorMl(value);
                if (vendedorMlValido(vendedor)) {
                    info.vendedor = vendedor;
                }
            }
            if (!info.seller_id && (key === 'seller_id' || key === 'sellerId') && value) {
                info.seller_id = value;
            }
            if (info.vendas === null && (key === 'sold_quantity' || key === 'soldQuantity' || key === 'sold') && value !== null && value !== undefined) {
                const vendas = parseMlQuantidade(value);
                if (vendas !== null) info.vendas = vendas;
            }
            if (key === 'seller' && value && typeof value === 'object') {
                if (!info.seller_id && value.id) info.seller_id = value.id;
                if (!info.vendedor) {
                    const candidato = escolherNomeVendedorMl([
                        { valor: value.nickname, prioridade: 90 },
                        { valor: value.official_store_name, prioridade: 82 },
                        { valor: value.name, prioridade: 70 }
                    ]);
                    if (candidato) {
                        info.vendedor = candidato;
                    }
                }
                continue;
            }
            if (key === 'official_store' && value && typeof value === 'object') {
                if (!info.seller_id && value.seller_id) info.seller_id = value.seller_id;
                if (!info.vendedor) {
                    const candidato = escolherNomeVendedorMl([
                        { valor: value.nickname, prioridade: 92 },
                        { valor: value.name, prioridade: 82 },
                        { valor: value.official_store_name, prioridade: 78 }
                    ]);
                    if (candidato) {
                        info.vendedor = candidato;
                    }
                }
                if (!info.data_criacao && value.date_created) {
                    info.data_criacao = normalizeMlText(value.date_created);
                }
            }
            if (value && typeof value === 'object') stack.push(value);
        }

        if (sameItem && (info.data_criacao || info.vendedor || info.seller_id || info.vendas !== null)) {
            return info;
        }
    }

    return (info.data_criacao || info.vendedor || info.seller_id || info.vendas !== null) ? info : null;
}

function findMlItemInfoInText(text, itemId) {
    const normalized = normalizeMlText(text);
    const patterns = [
        /"date_created"\s*:\s*"([^"]+)"/i,
        /"dateCreated"\s*:\s*"([^"]+)"/i,
        /"start_time"\s*:\s*"([^"]+)"/i,
        /"startTime"\s*:\s*"([^"]+)"/i,
        /"creationDate"\s*:\s*"([^"]+)"/i,
        /"listing_start_time"\s*:\s*"([^"]+)"/i
    ];
    const sellerPatterns = [
        /"seller_name"\s*:\s*"([^"]+)"/i,
        /"sellerName"\s*:\s*"([^"]+)"/i,
        /"nickname"\s*:\s*"([^"]+)"/i,
        /"official_store_name"\s*:\s*"([^"]+)"/i,
        /"officialStoreName"\s*:\s*"([^"]+)"/i
    ];
    const info = { id: String(itemId || '').trim().toUpperCase(), data_criacao: '', vendedor: '', seller_id: null, vendas: null };
    for (const pattern of patterns) {
        const match = normalized.match(pattern);
        if (match && match[1]) {
            info.data_criacao = normalizeMlText(match[1]);
            break;
        }
    }
    for (const pattern of sellerPatterns) {
        const match = normalized.match(pattern);
        if (match && match[1]) {
            info.vendedor = normalizeMlText(match[1]);
            break;
        }
    }
    const sellerIdMatch = normalized.match(/"seller_id"\s*:\s*(\d+)/i) || normalized.match(/"sellerId"\s*:\s*(\d+)/i);
    if (sellerIdMatch && sellerIdMatch[1]) info.seller_id = sellerIdMatch[1];
    const salesMatch = normalized.match(/"sold_quantity"\s*:\s*([0-9,.]+\s*(?:mil|k)?)/i)
        || normalized.match(/"soldQuantity"\s*:\s*([0-9,.]+\s*(?:mil|k)?)/i)
        || normalized.match(/"sold"\s*:\s*([0-9,.]+\s*(?:mil|k)?)/i);
    if (salesMatch && salesMatch[1]) {
        const parsed = parseMlQuantidade(salesMatch[1]);
        if (parsed !== null) info.vendas = parsed;
    }
    if (info.data_criacao || info.vendedor || info.seller_id || info.vendas !== null) return info;

    try {
        const parsed = JSON.parse(normalized);
        return findMlItemInfoInObject(parsed, itemId);
    } catch (_err) {
        return null;
    }
}

async function extractMlInfoByBrowser(itemId, targetUrl) {
    const cleanId = String(itemId || '').trim().toUpperCase().replace('-', '');
    if (!/^MLB\d+$/.test(cleanId)) {
        throw new Error('ID de anuncio invalido para leitura no navegador.');
    }

    const url = buildMlItemUrl(cleanId, targetUrl);
    const win = new BrowserWindow({
        width: 1180,
        height: 760,
        show: false,
        webPreferences: {
            contextIsolation: true,
            nodeIntegration: false,
            session: getMlSession()
        }
    });
    win.webContents.__jkAllowMlAdNavigation = true;

    const found = [];
    const watchedRequests = new Map();
    let attached = false;

    try {
        try {
            win.webContents.debugger.attach('1.3');
            attached = true;
            await win.webContents.debugger.sendCommand('Network.enable');
            win.webContents.debugger.on('message', async (_event, method, params) => {
                try {
                    if (method === 'Network.responseReceived') {
                        const responseUrl = String(params && params.response && params.response.url || '');
                        const mime = String(params && params.response && params.response.mimeType || '');
                        const interesting =
                            responseUrl.includes(cleanId) ||
                            responseUrl.includes('/items') ||
                            responseUrl.includes('/p/api/') ||
                            responseUrl.includes('/api/') ||
                            mime.includes('json');
                        if (interesting && params.requestId) {
                            watchedRequests.set(params.requestId, responseUrl);
                        }
                    }
                    if (method === 'Network.loadingFinished' && watchedRequests.has(params.requestId)) {
                        const responseUrl = watchedRequests.get(params.requestId);
                        watchedRequests.delete(params.requestId);
                        try {
                            const body = await win.webContents.debugger.sendCommand('Network.getResponseBody', { requestId: params.requestId });
                            const text = body && body.body ? String(body.body) : '';
                            const info = findMlItemInfoInText(text, cleanId);
                            if (info) {
                                info.source = `network:${responseUrl}`;
                                found.push(info);
                            }
                        } catch (_bodyErr) {}
                    }
                } catch (_err) {}
            });
        } catch (debugErr) {
            console.warn('Falha ao anexar debugger ML:', debugErr.message || debugErr);
        }

        const loadPromise = waitForMainFrameLoad(win, 30000).catch(() => null);
        await win.loadURL(url);
        await loadPromise;
        await new Promise(resolve => setTimeout(resolve, 4500));

        const pageInfo = await win.webContents.executeJavaScript(`
            (function () {
                try {
                    return {
                        html: document.documentElement && document.documentElement.outerHTML ? document.documentElement.outerHTML : '',
                        title: document.title || '',
                        url: location.href,
                        sellerText: (function () {
                            var selectors = [
                                '.ui-pdp-seller__header__title',
                                '.ui-pdp-seller__link-trigger',
                                '.ui-pdp-seller__nickname',
                                '.ui-pdp-official-store-label',
                                '[data-testid="seller-info"]',
                                '[data-testid="official-store-info"]'
                            ];
                            for (var i = 0; i < selectors.length; i += 1) {
                                var node = document.querySelector(selectors[i]);
                                if (node && node.textContent) return node.textContent.trim();
                            }
                            return '';
                        })()
                    };
                } catch (err) {
                    return { html: '', title: '', url: location.href, sellerText: '', error: String(err && err.message || err) };
                }
            })();
        `, true);

        const htmlInfo = findMlItemInfoInText(pageInfo && pageInfo.html, cleanId) || {};
        if (pageInfo && pageInfo.sellerText && !htmlInfo.vendedor) {
            htmlInfo.vendedor = normalizeMlText(pageInfo.sellerText).replace(/^(vendido por|loja oficial)\s+/i, '').trim();
        }
        if (htmlInfo.data_criacao || htmlInfo.vendedor || htmlInfo.seller_id) {
            htmlInfo.source = htmlInfo.source || 'page_html';
            found.push(htmlInfo);
        }

        const best = found.find(item => item && item.data_criacao)
            || found.find(item => item && item.vendedor)
            || found.find(item => item && item.vendas !== null && item.vendas !== undefined)
            || found.find(Boolean);
        return {
            id: cleanId,
            url: pageInfo && pageInfo.url ? pageInfo.url : url,
            data_criacao: best && best.data_criacao ? best.data_criacao : '',
            vendedor: best && best.vendedor ? best.vendedor : '',
            seller_id: best && best.seller_id ? best.seller_id : null,
            vendas: best && best.vendas !== null && best.vendas !== undefined ? best.vendas : null,
            source: best && best.source ? best.source : '',
            attempts: found.length
        };
    } finally {
        if (attached && win && !win.isDestroyed()) {
            try {
                win.webContents.debugger.detach();
            } catch (_err) {}
        }
        if (win && !win.isDestroyed()) {
            win.close();
        }
    }
}

function isAllowedNavigationUrl(targetUrl) {
    const value = String(targetUrl || '').trim();
    if (!value) return true;
    return /^(https?:|about:blank|data:text\/html|chrome-extension:)/i.test(value);
}

function getNavigationEventUrl(urlOrDetails, maybeDetails) {
    if (typeof urlOrDetails === 'string') return urlOrDetails;
    if (urlOrDetails && typeof urlOrDetails.url === 'string') return urlOrDetails.url;
    if (typeof maybeDetails === 'string') return maybeDetails;
    if (maybeDetails && typeof maybeDetails.url === 'string') return maybeDetails.url;
    return '';
}

function waitForMainFrameLoad(win, timeoutMs = 25000) {
    return new Promise((resolve, reject) => {
        if (!win || win.isDestroyed()) {
            reject(new Error('Janela interna indisponivel.'));
            return;
        }

        let done = false;
        const finish = (err) => {
            if (done) return;
            done = true;
            clearTimeout(timeout);
            win.webContents.removeListener('did-finish-load', onLoad);
            win.webContents.removeListener('did-fail-load', onFail);
            if (err) reject(err);
            else resolve();
        };

        const onLoad = () => finish();
        const onFail = (_event, errorCode, errorDescription) => {
            finish(new Error(`Falha ao carregar pagina (${errorCode}): ${errorDescription}`));
        };

        const timeout = setTimeout(() => {
            finish(new Error('Timeout ao carregar pagina para extracao de links.'));
        }, timeoutMs);

        win.webContents.once('did-finish-load', onLoad);
        win.webContents.once('did-fail-load', onFail);
    });
}

function loadElectronTabbedShell(win, appUrl) {
    const shellPath = path.join(getAppRootDir(), 'electron_shell.html');
    const tabPreloadPath = path.join(getAppRootDir(), 'electron_tab_preload.js');

    if (!fs.existsSync(shellPath) || !fs.existsSync(tabPreloadPath)) {
        win.loadURL(appUrl).catch((err) => {
            console.error('Falha ao carregar URL local do JK Sistema:', err);
        });
        return;
    }

    const shellUrl = `${pathToFileURL(shellPath).toString()}?appUrl=${encodeURIComponent(appUrl)}&tabPreload=${encodeURIComponent(pathToFileURL(tabPreloadPath).toString())}&browserPartition=${encodeURIComponent(getBrowserSessionPartition())}`;
    win.loadURL(shellUrl).catch((err) => {
        console.error('Falha ao carregar shell de abas do JK Sistema:', err);
        win.loadURL(appUrl).catch((fallbackErr) => {
            console.error('Falha ao carregar URL local do JK Sistema:', fallbackErr);
        });
    });
}

function ensureInternalBrowser(parent) {
    if (internalBrowserWindow && !internalBrowserWindow.isDestroyed()) {
        internalBrowserWindow.webContents.__jkAllowMlAdNavigation = true;
        if (internalBrowserWindow.isMinimized()) {
            internalBrowserWindow.restore();
        }
        internalBrowserWindow.show();
        internalBrowserWindow.focus();
        return internalBrowserWindow;
    }

    internalBrowserWindow = new BrowserWindow({
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
    internalBrowserWindow.webContents.__jkAllowMlAdNavigation = true;
    internalBrowserWindow.setMenuBarVisibility(false);
    internalBrowserWindow.on('closed', () => {
        internalBrowserWindow = null;
    });
    return internalBrowserWindow;
}

function normalizarBoundsNavegadorMl(bounds) {
    const raw = bounds || {};
    const x = Math.max(0, Math.round(Number(raw.x ?? raw.left ?? 0)));
    const y = Math.max(0, Math.round(Number(raw.y ?? raw.top ?? 0)));
    const width = Math.max(80, Math.round(Number(raw.width ?? 0)));
    const height = Math.max(80, Math.round(Number(raw.height ?? 0)));
    return { x, y, width, height };
}

function ensureEmbeddedMlBrowser(parent, options = {}) {
    const shouldAttach = options.attach !== false;
    const owner = parent && !parent.isDestroyed() ? parent : mainWindow;
    if (!owner || owner.isDestroyed()) {
        throw new Error('Janela principal indisponivel para abrir o navegador do Mercado Livre.');
    }
    if (!BrowserView) {
        throw new Error('BrowserView indisponivel nesta versao do Electron.');
    }
    if (!embeddedMlBrowserView) {
        embeddedMlBrowserView = new BrowserView({
            webPreferences: {
                contextIsolation: true,
                nodeIntegration: false,
                session: getMlSession()
            }
        });
        embeddedMlBrowserView.webContents.__jkAllowMlAdNavigation = true;
        embeddedMlBrowserView.webContents.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36');
        embeddedMlBrowserView.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
            logElectronLifecycle('embedded-ml-browser-fail-load', { errorCode, errorDescription, validatedURL });
        });
    }
    embeddedMlBrowserView.webContents.__jkAllowMlAdNavigation = true;
    if (!shouldAttach) {
        if (embeddedMlBrowserOwner && !embeddedMlBrowserOwner.isDestroyed()) {
            try { embeddedMlBrowserOwner.removeBrowserView(embeddedMlBrowserView); } catch (_err) {}
        }
        embeddedMlBrowserOwner = null;
        return embeddedMlBrowserView;
    }
    if (embeddedMlBrowserOwner && embeddedMlBrowserOwner !== owner && !embeddedMlBrowserOwner.isDestroyed()) {
        try { embeddedMlBrowserOwner.removeBrowserView(embeddedMlBrowserView); } catch (_err) {}
    }
    if (embeddedMlBrowserOwner !== owner) {
        owner.addBrowserView(embeddedMlBrowserView);
        embeddedMlBrowserOwner = owner;
    }
    if (typeof owner.setTopBrowserView === 'function') {
        try { owner.setTopBrowserView(embeddedMlBrowserView); } catch (_err) {}
    }
    return embeddedMlBrowserView;
}

function hideEmbeddedMlBrowser() {
    if (embeddedMlBrowserView && embeddedMlBrowserOwner && !embeddedMlBrowserOwner.isDestroyed()) {
        try { embeddedMlBrowserOwner.removeBrowserView(embeddedMlBrowserView); } catch (_err) {}
    }
    embeddedMlBrowserOwner = null;
}

function createWindow() {
    if (mainWindow && !mainWindow.isDestroyed()) {
        if (mainWindow.isMinimized()) {
            mainWindow.restore();
        }
        mainWindow.show();
        mainWindow.focus();
        return mainWindow;
    }

    mainWindow = new BrowserWindow({
        width: 1280,
        height: 800,
        title: "JK Sistema de Gestao",
        autoHideMenuBar: true,
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            nodeIntegration: false,
            nodeIntegrationInSubFrames: true,
            contextIsolation: true,
            webviewTag: true,
            defaultEncoding: 'UTF-8'
        }
    });
    const win = mainWindow;
    logElectronLifecycle('main-window-created');
    win.webContents.on('render-process-gone', (_event, details) => {
        logElectronLifecycle('render-process-gone', details || {});
    });
    win.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
        logElectronLifecycle('did-fail-load', { errorCode, errorDescription, validatedURL });
    });
    win.on('unresponsive', () => logElectronLifecycle('main-window-unresponsive'));
    win.on('closed', () => {
        logElectronLifecycle('main-window-closed');
        if (mainWindow === win) {
            mainWindow = null;
        }
    });

    const clientConfig = loadClientConfig();
    loadConfiguredApp(win, clientConfig);
    
    // win.webContents.openDevTools(); // Descomente para debug
}

function netJsonGet(url) {
    return new Promise((resolve, reject) => {
        const request = net.request({
            method: 'GET',
            url,
            useSessionCookies: true
        });
        request.setHeader('Accept', 'application/json, text/plain, */*');
        request.setHeader('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36');
        request.setHeader('Referer', 'https://www.mercadolivre.com.br/');

        let raw = '';
        request.on('response', (response) => {
            response.on('data', (chunk) => {
                raw += chunk.toString();
            });
            response.on('end', () => {
                if (response.statusCode < 200 || response.statusCode >= 300) {
                    reject(new Error(`HTTP ${response.statusCode}: ${raw.slice(0, 200)}`));
                    return;
                }
                try {
                    resolve(JSON.parse(raw));
                } catch (err) {
                    reject(err);
                }
            });
        });
        request.on('error', reject);
        request.end();
    });
}

app.whenReady().then(async () => {
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

    app.on('web-contents-created', (_event, contents) => {
        if (contents && typeof contents.once === 'function') {
            contents.once('destroyed', () => releaseMlAutomationProtectionForContents(contents));
        }
        contents.on('will-navigate', (event, urlOrDetails) => {
            const url = getNavigationEventUrl(urlOrDetails);
            if (isMlAutomationProtected() && isMercadoLivreLogoutUrl(url)) {
                event.preventDefault();
                logElectronLifecycle('blocked-ml-logout-navigation-during-favoritos', { url });
                return;
            }
            if (isBlockedAutomationPopupUrl(url)) {
                event.preventDefault();
                logElectronLifecycle('blocked-automation-navigation', { url });
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
            if (isMlAutomationProtected() && isMercadoLivreLogoutUrl(url)) {
                event.preventDefault();
                logElectronLifecycle('blocked-ml-logout-frame-navigation-during-favoritos', { url });
                return;
            }
            if (isBlockedAutomationPopupUrl(url)) {
                event.preventDefault();
                logElectronLifecycle('blocked-automation-frame-navigation', { url });
                return;
            }
            if (!isAllowedNavigationUrl(url)) {
                event.preventDefault();
            }
        });

        if (typeof contents.setWindowOpenHandler === 'function') {
            contents.setWindowOpenHandler(({ url }) => {
                if (isMlAutomationProtected() && isMercadoLivreLogoutUrl(url)) {
                    logElectronLifecycle('blocked-ml-logout-popup-during-favoritos', { url });
                    return { action: 'deny' };
                }
                if (isBlockedAutomationPopupUrl(url)) {
                    logElectronLifecycle('blocked-automation-popup', { url });
                    return { action: 'deny' };
                }
                if (isMercadoLivreAdUrl(url) && !contents.__jkAllowMlAdNavigation) {
                    openMercadoLivreAdInChrome(url);
                    return { action: 'deny' };
                }
                return isAllowedNavigationUrl(url) ? { action: 'allow' } : { action: 'deny' };
            });
        }
    });

    ipcMain.handle('get-mac', () => {
        return getMacAddress();
    });
    ipcMain.handle('get-app-version', () => {
        return app.getVersion();
    });
    ipcMain.handle('get-browser-session-partition', () => {
        return getBrowserSessionPartition();
    });
    ipcMain.handle('ensure-browser-extensions', async () => {
        await ensureChromeExtensionsForMlSession();
        return {
            success: true,
            extensions: getLoadedChromeExtensionsForMlSession()
        };
    });
    ipcMain.handle('flush-browser-session', async () => {
        await flushPersistentSessions();
        return { success: true };
    });
    ipcMain.handle('set-ml-automation-active', async (event, active, reason) => {
        if (active) {
            await flushPersistentSessions();
        }
        const result = setMlAutomationProtection(event.sender, !!active, reason);
        if (!active) {
            await flushPersistentSessions();
            maybeInstallDeferredUpdate();
        }
        return result;
    });
    ipcMain.handle('get-machine-info', () => {
        return getMachineInfo();
    });
    ipcMain.handle('import-credentials', async () => {
        return await openCredentialsImporter();
    });
    ipcMain.handle('check-for-updates', async () => {
        return await checkForUpdates(true);
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
            throw new Error('ID de anÃºncio invÃ¡lido.');
        }
        const cached = _cacheGet(mlItemInfoCache, cleanId);
        if (cached) {
            return await cached;
        }

    const requestPromise = (async () => {
            const item = await netJsonGet(`https://api.mercadolibre.com/items/${encodeURIComponent(cleanId)}`);
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
            return {
                id: item.id || cleanId,
                titulo: item.title || '',
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
            throw new Error('ID de anÃºncio invÃ¡lido.');
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
        const url = normalizeTargetUrl(targetUrl);
        if (isMlAutomationProtected() && isMercadoLivreLogoutUrl(url)) {
            logElectronLifecycle('blocked-embedded-ml-logout-load-during-favoritos', { url });
            return {
                success: false,
                blocked: true,
                reason: 'favoritos-em-execucao',
                url: embeddedMlBrowserView && !embeddedMlBrowserView.webContents.isDestroyed()
                    ? embeddedMlBrowserView.webContents.getURL()
                    : ''
            };
        }
        await ensureChromeExtensionsForMlSession();
        const parent = BrowserWindow.fromWebContents(event.sender) || mainWindow;
        const background = !!(bounds && bounds.background);
        const view = ensureEmbeddedMlBrowser(parent, { attach: !background });
        if (!background) {
            view.setBounds(normalizarBoundsNavegadorMl(bounds));
        }
        const currentUrl = view.webContents.getURL();
        if (currentUrl !== url) {
            await view.webContents.loadURL(url);
        }
        return { success: true, url: view.webContents.getURL() || url };
    });
    ipcMain.handle('embedded-ml-browser-position', async (event, bounds) => {
        if (bounds && bounds.background) {
            hideEmbeddedMlBrowser();
            return { success: true };
        }
        const parent = BrowserWindow.fromWebContents(event.sender) || mainWindow;
        const view = ensureEmbeddedMlBrowser(parent);
        view.setBounds(normalizarBoundsNavegadorMl(bounds));
        return { success: true };
    });
    ipcMain.handle('embedded-ml-browser-hide', async () => {
        hideEmbeddedMlBrowser();
        return { success: true };
    });
    ipcMain.handle('embedded-ml-browser-execute', async (_event, code) => {
        if (!embeddedMlBrowserView) {
            throw new Error('Navegador do Mercado Livre indisponivel.');
        }
        return await embeddedMlBrowserView.webContents.executeJavaScript(String(code || ''), true);
    });
    ipcMain.handle('open-internal-browser', async (event, targetUrl) => {
        const url = normalizeTargetUrl(targetUrl);
        if (isMlAutomationProtected() && isMercadoLivreLogoutUrl(url)) {
            logElectronLifecycle('blocked-internal-ml-logout-load-during-favoritos', { url });
            return { success: false, blocked: true, reason: 'favoritos-em-execucao', url: '' };
        }
        await ensureChromeExtensionsForMlSession();
        const parent = BrowserWindow.fromWebContents(event.sender) || null;
        const internalBrowser = ensureInternalBrowser(parent);

        try {
            const currentUrl = internalBrowser.webContents.getURL();
            if (currentUrl !== url) {
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
            if (currentUrl !== url) {
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

            // Aguarda o carregamento completo da pÃ¡gina
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

            // Extrai os links apÃ³s o carregamento completo
            const links = await internalBrowser.webContents.executeJavaScript(`
                Array.from(document.querySelectorAll('a')).map(a => a.href).filter(href => href)
            `);

            internalBrowser.close();
            return links;
        } catch (err) {
            console.error('Erro ao buscar links da pÃ¡gina:', err);
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

app.on('before-quit', () => {
    logElectronLifecycle('before-quit');
    stopLocalBackend();
    clearStartupIncomplete();
    flushPersistentSessions().catch(() => {});
});
