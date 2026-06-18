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

const { app, BrowserWindow, BrowserView, desktopCapturer, ipcMain, session, net, shell, Notification } = electron;
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
if (process.platform === 'win32' && typeof app.setAppUserModelId === 'function') {
    app.setAppUserModelId('com.jksistema.cliente');
}

const JK_LOCAL_BACKEND_PORT = 8001;
const JK_PROMO_WORKER_PORT = 8011;
const JK_DEFAULT_APP_URL = `http://127.0.0.1:${JK_LOCAL_BACKEND_PORT}/frontend_index.html`;
const JK_LOCAL_BACKEND_DIR_NAME = 'local_app';
const JK_FIREBASE_PRESENCE_ENV_FILE_NAME = 'firebase-presence.env';
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
let avantProStorageRecoveryAttempted = false;
let avantProStorageRecoveryPromise = null;
let updateEventsRegistered = false;
let updateCheckInProgress = false;
let updateInstallInProgress = false;
let updateInstallRequested = false;
let downloadedUpdateInfo = null;
let deferredDownloadedUpdateInfo = null;
let updateFeedConfigured = false;
let updateFeedSource = '';
const mlAutomationProtectionByWebContents = new Map();
const mlItemInfoCache = new Map();
const configuredMeetingPermissionSessions = new WeakSet();
const ML_ITEM_INFO_CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutos
const AUTO_UPDATE_CHECK_TIMEOUT_MS = 45000;
const AUTO_UPDATE_START_DELAY_MS = 1500;
const JK_BROWSER_SESSION_PARTITION = process.env.JK_BROWSER_SESSION_PARTITION || 'persist:jk-sistema-browser';
const AVANTPRO_CHROME_EXTENSION_ID = 'jdefnfmbnchmnjkcknaadaddgjbgephh';
const AVANTPRO_EXTENSION_DEFAULT_ENABLED = !/^(0|false|nao|não|off)$/i.test(String(process.env.JK_ENABLE_AVANTPRO_EXTENSION || 'true'));

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

function formatPathTimestamp(date = new Date()) {
    return date.toISOString().replace(/[-:]/g, '').replace(/\..+$/, '').replace('T', '_');
}

function sanitizePathPart(value) {
    return String(value || 'reset')
        .replace(/[^a-z0-9_-]+/gi, '_')
        .replace(/^_+|_+$/g, '')
        .slice(0, 48) || 'reset';
}

function getMlSessionUserDataDir() {
    const partition = String(JK_BROWSER_SESSION_PARTITION || '');
    if (/^persist:/i.test(partition)) {
        return path.join(JK_ELECTRON_USER_DATA_DIR, 'Partitions', partition.replace(/^persist:/i, ''));
    }
    return JK_ELECTRON_USER_DATA_DIR;
}

function getAvantProExtensionStorageDir() {
    return path.join(
        getMlSessionUserDataDir(),
        'Local Extension Settings',
        AVANTPRO_CHROME_EXTENSION_ID
    );
}

function backupAndResetAvantProExtensionStorage(reason = 'reset') {
    const sourceDir = getAvantProExtensionStorageDir();
    if (!fs.existsSync(sourceDir)) {
        return { success: false, missing: true, sourceDir };
    }
    const backupRoot = path.join(JK_ELECTRON_USER_DATA_DIR, '_avantpro_storage_backups');
    fs.mkdirSync(backupRoot, { recursive: true });
    const baseName = `${AVANTPRO_CHROME_EXTENSION_ID}_${formatPathTimestamp()}_${sanitizePathPart(reason)}`;
    let backupDir = path.join(backupRoot, baseName);
    for (let index = 2; fs.existsSync(backupDir); index += 1) {
        backupDir = path.join(backupRoot, `${baseName}_${index}`);
    }
    try {
        fs.renameSync(sourceDir, backupDir);
        logElectronLifecycle('avantpro-storage-reset', { sourceDir, backupDir, reason });
        return { success: true, sourceDir, backupDir };
    } catch (err) {
        logElectronLifecycle('avantpro-storage-reset-failed', {
            sourceDir,
            backupDir,
            reason,
            error: err && err.message ? err.message : String(err)
        });
        return {
            success: false,
            sourceDir,
            backupDir,
            error: err && err.message ? err.message : String(err)
        };
    }
}

async function unloadAvantProExtensionForRecovery() {
    const ses = getMlSession();
    if (!ses || typeof ses.removeExtension !== 'function') {
        return { success: false, unsupported: true };
    }
    try {
        await Promise.resolve(ses.removeExtension(AVANTPRO_CHROME_EXTENSION_ID));
        logElectronLifecycle('avantpro-extension-unloaded-for-recovery', { id: AVANTPRO_CHROME_EXTENSION_ID });
        return { success: true };
    } catch (err) {
        logElectronLifecycle('avantpro-extension-unload-failed-for-recovery', {
            id: AVANTPRO_CHROME_EXTENSION_ID,
            error: err && err.message ? err.message : String(err)
        });
        return { success: false, error: err && err.message ? err.message : String(err) };
    }
}

async function recoverAvantProExtensionStorage(reason = 'avantpro-not-detected', details = {}) {
    if (!isAvantProExtensionEnabled()) {
        return { success: false, skipped: true, reason: 'avantpro-disabled' };
    }
    if (avantProStorageRecoveryPromise) return avantProStorageRecoveryPromise;
    if (avantProStorageRecoveryAttempted) {
        return { success: false, skipped: true, reason: 'already-attempted' };
    }
    avantProStorageRecoveryAttempted = true;
    avantProStorageRecoveryPromise = (async () => {
        logElectronLifecycle('avantpro-storage-recovery-started', { reason, details });
        await unloadAvantProExtensionForRecovery();
        const reset = backupAndResetAvantProExtensionStorage(reason);
        if (!reset.success && !reset.missing) {
            return { success: false, reset };
        }
        chromeExtensionsLoadPromise = null;
        const loaded = await ensureChromeExtensionsForMlSession();
        return {
            success: true,
            reset,
            loaded: loaded.map(ext => ({
                id: ext && ext.id,
                name: ext && ext.name,
                path: ext && ext.path
            }))
        };
    })().finally(() => {
        avantProStorageRecoveryPromise = null;
    });
    return avantProStorageRecoveryPromise;
}

function isAvantProStorageConsoleMessage(message, sourceId) {
    const text = `${message || ''} ${sourceId || ''}`;
    return text.includes(AVANTPRO_CHROME_EXTENSION_ID)
        && /IO error|LevelDB|MANIFEST-\d+|Unable to create sequential file|Invalid argument|corrupt/i.test(text);
}

function registerAvantProConsoleDiagnostics(webContents) {
    if (!isAvantProExtensionEnabled()) return;
    if (!webContents || webContents.__jkAvantProConsoleDiagnosticsRegistered) return;
    webContents.__jkAvantProConsoleDiagnosticsRegistered = true;
    webContents.on('console-message', (_event, level, message, line, sourceId) => {
        const text = `${message || ''} ${sourceId || ''}`;
        if (!/avant|chrome-extension|jdefnfmbnchmnjkcknaadaddgjbgephh/i.test(text)) return;
        const payload = {
            level,
            message: String(message || '').slice(0, 500),
            line,
            sourceId: String(sourceId || '').slice(0, 500)
        };
        logElectronLifecycle('avantpro-console-message', payload);
        if (isAvantProStorageConsoleMessage(message, sourceId)) {
            recoverAvantProExtensionStorage('avantpro-console-storage-error', payload).catch((err) => {
                logElectronLifecycle('avantpro-storage-recovery-error', err);
            });
        }
    });
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
        return 'GitHub recusou a consulta da atualizacao. Confira o acesso ao repositorio ou ao canal de releases.';
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
            return 'Este instalador nao possui canal de atualizacao automatica configurado. Use a versao mais recente publicada.';
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

async function installUpdateNow() {
    if (!autoUpdater) {
        const message = 'electron-updater nao esta disponivel neste pacote.';
        sendUpdateStatus('error', { error: message });
        return { success: false, error: message };
    }
    const feedStatus = configureAutoUpdaterFeed();
    if (!feedStatus.success) {
        const message = feedStatus.reason || 'Canal de atualizacao nao configurado.';
        sendUpdateStatus('error', { error: message });
        return { success: false, error: message };
    }
    registerAutoUpdateEvents();
    updateInstallRequested = true;
    if (downloadedUpdateInfo) {
        await installDownloadedUpdateSafely(downloadedUpdateInfo);
        return { success: true, installing: true };
    }
    sendUpdateStatus('download-requested');
    try {
        await autoUpdater.downloadUpdate();
        return { success: true, downloading: true };
    } catch (err) {
        updateInstallRequested = false;
        const message = getUpdateErrorMessage(err);
        sendUpdateStatus('error', { error: message });
        return { success: false, error: message };
    }
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
        downloadedUpdateInfo = null;
        updateInstallRequested = true;
        sendUpdateStatus('available', {
            updateInfo: normalizeUpdateInfo(info),
            autoDownload: true,
            autoInstall: true
        });
    });
    autoUpdater.on('update-not-available', (info) => {
        downloadedUpdateInfo = null;
        updateInstallRequested = false;
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
        updateInstallRequested = false;
        sendUpdateStatus('error', { error: getUpdateErrorMessage(err) });
    });
    autoUpdater.on('update-downloaded', (info) => {
        downloadedUpdateInfo = info || {};
        sendUpdateStatus('downloaded', {
            updateInfo: normalizeUpdateInfo(info),
            autoInstall: true,
            installRequested: true
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
    }, AUTO_UPDATE_START_DELAY_MS);
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

function writeLocalBackendLauncher(localAppDir) {
    const infoDir = path.join(localAppDir, 'info');
    const firebaseEnv = getLocalBackendFirebaseEnv(localAppDir);
    const launcherPath = path.join(JK_ELECTRON_USER_DATA_DIR, 'start-local-backend.cmd');
    const logPath = path.join(localAppDir, 'logs', 'local_backend.log');
    const depsMarker = `.venv\\.jk_deps_${sanitizeMarkerVersion(app.getVersion())}.ok`;
    const localCallback = process.env.JK_LOCAL_OAUTH_CALLBACK_URL || 'https://jkjkjk-485920.web.app/auth/callback';
    const localGoogleCallback = process.env.JK_LOCAL_GOOGLE_CALLBACK_URL || 'https://jkjkjk-485920.web.app/auth/google/callback';
    const pythonRuntimeDir = '.python-runtime';
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
        `set "GOOGLE_LOGIN_REDIRECT_URI_LOCAL=${localGoogleCallback}"`,
        `set "PROMO_WORKER_URL=http://127.0.0.1:${JK_PROMO_WORKER_PORT}"`,
        `set "JK_APP_VERSION=${cmdValue(app.getVersion())}"`,
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
        'set "PYTHONUNBUFFERED=1"',
        'set "PYTHONUTF8=1"',
        'echo.>> "%LOG_FILE%"',
        'echo ==== JK Sistema local backend %date% %time% ====>> "%LOG_FILE%"',
        `set "PYTHON_RUNTIME_DIR=${pythonRuntimeDir}"`,
        'set "BUNDLED_PYTHON_INSTALLER="',
        'for %%I in (python_runtime\\python-*.exe) do if exist "%%~fI" if not defined BUNDLED_PYTHON_INSTALLER set "BUNDLED_PYTHON_INSTALLER=%%~fI"',
        'if exist "%PYTHON_RUNTIME_DIR%\\python.exe" (',
        '  "%PYTHON_RUNTIME_DIR%\\python.exe" -c "import sys; print(sys.executable)" >> "%LOG_FILE%" 2>&1',
        '  if errorlevel 1 (',
        '    echo Python runtime local invalido. Recriando runtime empacotado...>> "%LOG_FILE%"',
        '    rmdir /s /q "%PYTHON_RUNTIME_DIR%" >> "%LOG_FILE%" 2>&1',
        '  )',
        ')',
        'if not exist "%PYTHON_RUNTIME_DIR%\\python.exe" (',
        '  if defined BUNDLED_PYTHON_INSTALLER (',
        '    echo Instalando Python empacotado: %BUNDLED_PYTHON_INSTALLER%>> "%LOG_FILE%"',
        '    "%BUNDLED_PYTHON_INSTALLER%" /quiet InstallAllUsers=0 TargetDir="%CD%\\%PYTHON_RUNTIME_DIR%" Include_pip=1 Include_launcher=0 AssociateFiles=0 Shortcuts=0 Include_test=0 PrependPath=0 >> "%LOG_FILE%" 2>&1',
        '  ) else (',
        '    echo Instalador Python empacotado nao encontrado em python_runtime.>> "%LOG_FILE%"',
        '  )',
        ')',
        'if exist ".venv\\Scripts\\python.exe" (',
        '  ".venv\\Scripts\\python.exe" -c "import sys; print(sys.executable)" >> "%LOG_FILE%" 2>&1',
        '  if errorlevel 1 (',
        '    echo Ambiente Python virtual invalido. Recriando .venv...>> "%LOG_FILE%"',
        '    rmdir /s /q ".venv" >> "%LOG_FILE%" 2>&1',
        '  )',
        ')',
        'if not exist ".venv\\Scripts\\python.exe" (',
        '  if exist "%PYTHON_RUNTIME_DIR%\\python.exe" "%PYTHON_RUNTIME_DIR%\\python.exe" -m venv ".venv" >> "%LOG_FILE%" 2>&1',
        ')',
        'if not exist ".venv\\Scripts\\python.exe" (',
        '  where py >nul 2>nul',
        '  if not errorlevel 1 py -3.11 -m venv ".venv" >> "%LOG_FILE%" 2>&1',
        ')',
        'if not exist ".venv\\Scripts\\python.exe" (',
        '  where py >nul 2>nul',
        '  if not errorlevel 1 py -3.12 -m venv ".venv" >> "%LOG_FILE%" 2>&1',
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
        '"%PYTHON_EXE%" -c "import sys; print(sys.executable)" >> "%LOG_FILE%" 2>&1',
        'if errorlevel 1 (',
        '  echo Ambiente Python virtual continuou invalido apos recriacao.>> "%LOG_FILE%"',
        '  rmdir /s /q ".venv" >> "%LOG_FILE%" 2>&1',
        '  exit /b 1',
        ')',
        `if not exist "${depsMarker}" (`,
        '  "%PYTHON_EXE%" -m ensurepip --upgrade >> "%LOG_FILE%" 2>&1',
        '  if exist "python_wheels\\*.whl" (',
        '    echo Instalando dependencias offline em python_wheels...>> "%LOG_FILE%"',
        '    "%PYTHON_EXE%" -m pip install --disable-pip-version-check --no-index --find-links "python_wheels" -r requirements.txt >> "%LOG_FILE%" 2>&1',
        '    if errorlevel 1 (',
        '      echo Instalacao offline falhou. Tentando instalar pela internet...>> "%LOG_FILE%"',
        '      "%PYTHON_EXE%" -m pip install --disable-pip-version-check --upgrade pip setuptools wheel >> "%LOG_FILE%" 2>&1',
        '      if not errorlevel 1 "%PYTHON_EXE%" -m pip install --disable-pip-version-check -r requirements.txt >> "%LOG_FILE%" 2>&1',
        '    )',
        '  ) else (',
        '    echo Wheelhouse offline ausente. Instalando dependencias pela internet...>> "%LOG_FILE%"',
        '    "%PYTHON_EXE%" -m pip install --disable-pip-version-check --upgrade pip setuptools wheel >> "%LOG_FILE%" 2>&1',
        '    if not errorlevel 1 "%PYTHON_EXE%" -m pip install --disable-pip-version-check -r requirements.txt >> "%LOG_FILE%" 2>&1',
        '  )',
        '  if errorlevel 1 (',
        '    echo Falha ao instalar dependencias. Verifique este log.>> "%LOG_FILE%"',
        '    exit /b %errorlevel%',
        '  )',
        '  "%PYTHON_EXE%" -m pip check >> "%LOG_FILE%" 2>&1',
        '  "%PYTHON_EXE%" -m pip uninstall -y fitz >> "%LOG_FILE%" 2>&1',
        `  echo ok> "${depsMarker}"`,
        ')',
        'set "JK_CA_BUNDLE=%CD%\\.venv\\Lib\\site-packages\\certifi\\cacert.pem"',
        'if exist "%JK_CA_BUNDLE%" (',
        '  set "SSL_CERT_FILE=%JK_CA_BUNDLE%"',
        '  set "REQUESTS_CA_BUNDLE=%JK_CA_BUNDLE%"',
        '  set "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=%JK_CA_BUNDLE%"',
        '  echo Usando certificados Python: %JK_CA_BUNDLE%>> "%LOG_FILE%"',
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
            if (localBackendHealthCompatible(health, firebaseEnv)) {
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
                JK_REDIRECT_URI: process.env.JK_LOCAL_OAUTH_CALLBACK_URL || 'https://jkjkjk-485920.web.app/auth/callback',
                JK_BLING_REDIRECT_URI: process.env.JK_LOCAL_OAUTH_CALLBACK_URL || 'https://jkjkjk-485920.web.app/auth/callback',
                GOOGLE_LOGIN_REDIRECT_URI_LOCAL: process.env.JK_LOCAL_GOOGLE_CALLBACK_URL || 'https://jkjkjk-485920.web.app/auth/google/callback',
                PROMO_WORKER_URL: `http://127.0.0.1:${JK_PROMO_WORKER_PORT}`,
                JK_APP_VERSION: app.getVersion(),
                IA_RAG_ENABLED: 'true',
                IA_RAG_BACKEND: 'local',
                IA_RAG_TOP_K: '5',
                IA_RAG_SEARCH_TIMEOUT_S: '4',
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

function loadConfiguredApp(win, clientConfig = null) {
    const config = clientConfig || loadClientConfig();
    logElectronLifecycle('client-config-loaded', { appUrl: config.appUrl, configPath: config.configPath });
    if (isLocalBackendAppUrl(config.appUrl)) {
        renderLocalBackendStartupScreen(win);
        ensureLocalBackendStarted()
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
        path.join(getLocalBackendRuntimeDir(), 'extensoes_chrome'),
        path.join(app.getPath('userData'), JK_LOCAL_BACKEND_DIR_NAME, 'extensoes_chrome'),
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

function readChromeExtensionManifest(extensionDir) {
    try {
        return JSON.parse(fs.readFileSync(path.join(extensionDir, 'manifest.json'), 'utf8'));
    } catch (_err) {
        return null;
    }
}

function isAvantProExtensionCandidate(extensionDir) {
    const normalizedPath = String(extensionDir || '').replace(/\\/g, '/').toLowerCase();
    if (normalizedPath.includes(AVANTPRO_CHROME_EXTENSION_ID.toLowerCase())) return true;
    if (/(^|\/)avant[_-]?pro(\/|$)/i.test(normalizedPath)) return true;
    const manifest = readChromeExtensionManifest(extensionDir);
    if (!manifest) return false;
    const manifestText = [
        manifest.name,
        manifest.short_name,
        manifest.description,
        manifest.homepage_url
    ].filter(Boolean).join(' ');
    return /avant\s*pro|avantpro/i.test(manifestText);
}

function findUnpackedChromeExtensions() {
    const candidates = [];
    const explicitRoots = process.env.JK_CHROME_EXTENSIONS_DIR ? [path.resolve(process.env.JK_CHROME_EXTENSIONS_DIR)] : [];
    const bundledRoots = getChromeExtensionsRoots();
    const avantProEnabled = isAvantProExtensionEnabled();
    if (!avantProEnabled) {
        return [];
    }
    const chromeInstalledExtensions = avantProEnabled ? findInstalledChromeExtensionVersions(AVANTPRO_CHROME_EXTENSION_ID) : [];
    const preferInstalled = /^(1|true|sim|yes)$/i.test(String(process.env.JK_PREFER_INSTALLED_CHROME_EXTENSIONS || ''));
    const searchRoots = preferInstalled
        ? [...explicitRoots, ...chromeInstalledExtensions, ...bundledRoots]
        : [...explicitRoots, ...bundledRoots, ...chromeInstalledExtensions];

    for (const root of searchRoots) {
        if (!fs.existsSync(root)) continue;

        const rootManifest = path.join(root, 'manifest.json');
        if (fs.existsSync(rootManifest)) {
            if (!isAvantProExtensionCandidate(root)) continue;
            candidates.push(root);
            continue;
        }

        for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
            if (!entry.isDirectory()) continue;
            const dir = path.join(root, entry.name);
            if (fs.existsSync(path.join(dir, 'manifest.json'))) {
                if (!isAvantProExtensionCandidate(dir)) continue;
                candidates.push(dir);
            }
        }
    }

    const seen = new Set();
    const unique = [];
    for (const candidate of Array.from(new Set(candidates.map(item => path.resolve(item))))) {
        if (!isAvantProExtensionCandidate(candidate)) continue;
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

function getBrowserExtensionSettingsSnapshot() {
    return {
        ...loadBrowserExtensionsConfig(),
        configPath: getBrowserExtensionsConfigPath(),
        loadedExtensions: getLoadedChromeExtensionsForMlSession()
    };
}

async function applyAvantProExtensionSetting(enabled) {
    const config = saveBrowserExtensionsConfig({ avantProEnabled: !!enabled });
    const ses = getMlSession();
    chromeExtensionsLoadPromise = null;

    if (!config.avantProEnabled && ses && typeof ses.removeExtension === 'function') {
        try {
            await Promise.resolve(ses.removeExtension(AVANTPRO_CHROME_EXTENSION_ID));
            logElectronLifecycle('avantpro-extension-disabled-by-user', { id: AVANTPRO_CHROME_EXTENSION_ID });
        } catch (err) {
            logElectronLifecycle('avantpro-extension-disable-remove-failed', {
                id: AVANTPRO_CHROME_EXTENSION_ID,
                error: err && err.message ? err.message : String(err)
            });
        }
    }

    if (config.avantProEnabled) {
        await ensureChromeExtensionsForMlSession();
        logElectronLifecycle('avantpro-extension-enabled-by-user', { id: AVANTPRO_CHROME_EXTENSION_ID });
    }

    return getBrowserExtensionSettingsSnapshot();
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

function isMercadoLivreUrl(targetUrl) {
    try {
        const url = new URL(normalizeTargetUrl(targetUrl));
        return isMercadoLivreHost(url.hostname);
    } catch (_err) {
        return false;
    }
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
        return /suporte|support|ajuda|help|tutorial|introducao|introduÃ§Ã£o|curso|youtube|whatsapp|wa\.me/.test(text);
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

function isOAuthExternalAuthUrl(targetUrl) {
    try {
        const url = new URL(normalizeTargetUrl(targetUrl));
        const host = url.hostname.toLowerCase();
        const pathAndQuery = `${url.pathname}${url.search}`.toLowerCase();
        if ((host === 'www.bling.com.br' || host === 'bling.com.br') && pathAndQuery.includes('/api/v3/oauth/authorize')) {
            return true;
        }
        return (host === 'auth.mercadolivre.com.br' || host === 'auth.mercadolibre.com' || host.endsWith('.mercadolibre.com'))
            && pathAndQuery.includes('/authorization');
    } catch (_err) {
        return false;
    }
}

function openOAuthExternalAuthInChrome(targetUrl) {
    openUrlInGoogleChrome(targetUrl).catch((err) => {
        console.error('Falha ao abrir autenticacao OAuth no Chrome:', err && err.message ? err.message : err);
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
    return !/^(vendido|vendedor|anuncio|anunci[oÃƒÂ´]o|produto|frete|envio|loja|oferta|ofertas|desconto|comprar|comprando|login|entrar|cadastro|email|senha|contato|perfil|busca|filtro|categoria|condi[cÃƒÂ§][aÃƒÂ£]o|aviso|informa[cÃƒÂ§][aÃƒÂ£]o|cria[cÃƒÂ§][aÃƒÂ£]o|valor|pre[cÃƒÂ§]o)$/i.test(textoBusca);
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

function waitMs(ms) {
    return new Promise(resolve => setTimeout(resolve, Math.max(0, Number(ms) || 0)));
}

function isWebContentsAlive(webContents) {
    return !!(webContents && typeof webContents.isDestroyed === 'function' && !webContents.isDestroyed());
}

function waitForWebContentsLoad(webContents, timeoutMs = 25000) {
    return new Promise((resolve, reject) => {
        if (!isWebContentsAlive(webContents)) {
            reject(new Error('Navegador interno indisponivel.'));
            return;
        }

        let done = false;
        let timeout = null;
        const finish = (err) => {
            if (done) return;
            done = true;
            clearTimeout(timeout);
            if (isWebContentsAlive(webContents)) {
                webContents.removeListener('did-finish-load', onLoad);
                webContents.removeListener('did-fail-load', onFail);
            }
            if (err) reject(err);
            else resolve();
        };

        const onLoad = () => finish();
        const onFail = (_event, errorCode, errorDescription, _validatedURL, isMainFrame) => {
            if (isMainFrame === false) return;
            finish(new Error(`Falha ao carregar pagina (${errorCode}): ${errorDescription}`));
        };

        timeout = setTimeout(() => {
            finish(new Error('Timeout ao carregar pagina no navegador interno.'));
        }, timeoutMs);

        webContents.once('did-finish-load', onLoad);
        webContents.once('did-fail-load', onFail);
    });
}

async function diagnosticarAvantProWebContents(webContents) {
    if (!isAvantProExtensionEnabled()) return { ok: false, skipped: true, reason: 'avantpro-disabled' };
    if (!isWebContentsAlive(webContents)) return null;
    return await webContents.executeJavaScript(`
        (function () {
            var AVANT_ID = ${JSON.stringify(AVANTPRO_CHROME_EXTENSION_ID)};
            var normalizar = function (value) {
                return String(value || '').replace(/\\s+/g, ' ').trim();
            };
            var contemAvant = function (value) {
                return /avant\\s*pro|avantpro|carregar\\s+dados\\s+avant|informacoes?\\s+avant|informa[c\\u00e7][o\\u00f5]es\\s+avant/i.test(normalizar(value));
            };
            var rows = document.querySelectorAll('.avantpro-product-info-row').length;
            var widgets = document.querySelectorAll('[class*="avantpro"], [id*="avantpro"], [data-testid*="avantpro"]').length;
            var actionButtons = 0;
            Array.prototype.slice.call(document.querySelectorAll('button, a, [role="button"]')).forEach(function (node) {
                var text = [
                    node.innerText,
                    node.textContent,
                    node.getAttribute && node.getAttribute('aria-label'),
                    node.getAttribute && node.getAttribute('title')
                ].map(normalizar).join(' ');
                if (contemAvant(text)) actionButtons += 1;
            });
            var taggedNodes = 0;
            Array.prototype.slice.call(document.querySelectorAll('[class], [id], script')).forEach(function (node) {
                var text = [
                    node.id,
                    node.className,
                    node.getAttribute && node.getAttribute('src')
                ].map(normalizar).join(' ');
                if (contemAvant(text)) taggedNodes += 1;
            });
            var extensionResources = 0;
            try {
                extensionResources = performance.getEntriesByType('resource').filter(function (entry) {
                    var name = String(entry && entry.name || '').toLowerCase();
                    return name.indexOf('chrome-extension://' + AVANT_ID) >= 0 || name.indexOf('avantpro') >= 0;
                }).length;
            } catch (_err) {}
            var ok = rows > 0 || widgets > 0 || actionButtons > 0 || taggedNodes > 0;
            return {
                ok: !!ok,
                rows: rows,
                widgets: widgets,
                actionButtons: actionButtons,
                taggedNodes: taggedNodes,
                extensionResources: extensionResources,
                url: location.href,
                title: document.title || ''
            };
        })();
    `, true).catch((err) => ({
        ok: false,
        error: err && err.message ? err.message : String(err)
    }));
}

async function aguardarAvantProWebContents(webContents, options = {}) {
    const timeoutMs = Math.max(500, Number(options.timeoutMs) || 3500);
    const pollMs = Math.max(150, Number(options.pollMs) || 300);
    const startedAt = Date.now();
    let lastStatus = null;
    while (Date.now() - startedAt < timeoutMs) {
        lastStatus = await diagnosticarAvantProWebContents(webContents);
        if (lastStatus && lastStatus.ok) {
            return { ...lastStatus, elapsedMs: Date.now() - startedAt };
        }
        await waitMs(pollMs);
    }
    return lastStatus ? { ...lastStatus, ok: false } : { ok: false, unavailable: true };
}

async function recarregarWebContentsParaAvantPro(webContents) {
    if (!isWebContentsAlive(webContents)) return false;
    const loadPromise = waitForWebContentsLoad(webContents, 25000).catch((err) => err);
    try {
        webContents.reloadIgnoringCache();
    } catch (_err) {
        return false;
    }
    await loadPromise;
    await waitMs(900);
    return isWebContentsAlive(webContents);
}

async function garantirAvantProWebContents(webContents, targetUrl, options = {}) {
    if (!isAvantProExtensionEnabled()) {
        return { ok: true, skipped: true, reason: 'avantpro-disabled' };
    }
    const url = targetUrl || (isWebContentsAlive(webContents) ? webContents.getURL() : '');
    if (!isMercadoLivreUrl(url)) return { ok: true, skipped: true };
    const firstStatus = await aguardarAvantProWebContents(webContents, {
        timeoutMs: options.timeoutMs || 3500,
        pollMs: options.pollMs || 300
    });
    if (firstStatus && firstStatus.ok) return firstStatus;

    if (options.recarregarSeAusente === false) return firstStatus;
    logElectronLifecycle('avantpro-not-detected-reloading-embedded-browser', {
        url,
        status: firstStatus
    });
    const reloaded = await recarregarWebContentsParaAvantPro(webContents);
    if (!reloaded) return firstStatus;
    const secondStatus = await aguardarAvantProWebContents(webContents, {
        timeoutMs: options.timeoutAposReloadMs || 5500,
        pollMs: options.pollMs || 300
    });
    logElectronLifecycle('avantpro-after-embedded-browser-reload', {
        url: isWebContentsAlive(webContents) ? webContents.getURL() : url,
        status: secondStatus
    });
    if (secondStatus && !secondStatus.ok && options.recuperarStorage !== false) {
        const recovery = await recoverAvantProExtensionStorage('avantpro-not-detected-after-reload', {
            url,
            status: secondStatus
        });
        logElectronLifecycle('avantpro-storage-recovery-result', recovery);
        if (recovery && recovery.success && isWebContentsAlive(webContents)) {
            await recarregarWebContentsParaAvantPro(webContents);
            const recoveredStatus = await aguardarAvantProWebContents(webContents, {
                timeoutMs: options.timeoutAposRecoveryMs || 7000,
                pollMs: options.pollMs || 300
            });
            logElectronLifecycle('avantpro-after-storage-recovery', {
                url: isWebContentsAlive(webContents) ? webContents.getURL() : url,
                status: recoveredStatus
            });
            return { ...recoveredStatus, reloaded: true, storageRecovery: recovery };
        }
    }
    return { ...secondStatus, reloaded: true };
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
    registerAvantProConsoleDiagnostics(internalBrowserWindow.webContents);
    internalBrowserWindow.setMenuBarVisibility(false);
    internalBrowserWindow.on('closed', () => {
        internalBrowserWindow = null;
    });
    return internalBrowserWindow;
}

function createDetachedInternalBrowser(parent, targetUrl, title = '') {
    const safeTitle = String(title || '').replace(/\s+/g, ' ').trim().slice(0, 90);
    const detachedWindow = new BrowserWindow({
        width: 1280,
        height: 820,
        title: safeTitle ? `${safeTitle} - JK Sistema` : 'Navegador Interno - JK Sistema',
        parent,
        webPreferences: {
            contextIsolation: true,
            nodeIntegration: false,
            session: getMlSession()
        }
    });
    detachedWindow.webContents.__jkAllowMlAdNavigation = true;
    registerAvantProConsoleDiagnostics(detachedWindow.webContents);
    detachedWindow.setMenuBarVisibility(false);
    detachedWindow.webContents.on('page-title-updated', (_event, pageTitle) => {
        const clean = String(pageTitle || safeTitle || 'Navegador Interno')
            .replace(/\s+/g, ' ')
            .trim()
            .slice(0, 90);
        if (clean) detachedWindow.setTitle(`${clean} - JK Sistema`);
    });
    detachedWindow.loadURL(targetUrl).catch((err) => {
        logElectronLifecycle('detached-internal-browser-load-failed', {
            url: targetUrl,
            error: err && err.message ? err.message : String(err)
        });
    });
    return detachedWindow;
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
        registerAvantProConsoleDiagnostics(embeddedMlBrowserView.webContents);
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
            backgroundThrottling: false,
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
    configureNotificationPermissions();

    app.on('web-contents-created', (_event, contents) => {
        configureNotificationPermissionsForSession(contents && contents.session);
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
                if (isMlAutomationProtected() && isMercadoLivreLogoutUrl(url)) {
                    logElectronLifecycle('blocked-ml-logout-popup-during-favoritos', { url });
                    return { action: 'deny' };
                }
                if (isBlockedAutomationPopupUrl(url)) {
                    logElectronLifecycle('blocked-automation-popup', { url });
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
    ipcMain.handle('flush-browser-session', async () => {
        await flushPersistentSessions();
        return { success: true };
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
            await flushPersistentSessions();
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
        await garantirAvantProWebContents(view.webContents, view.webContents.getURL() || url).catch((err) => {
            logElectronLifecycle('avantpro-embedded-browser-check-failed', {
                url,
                error: err && err.message ? err.message : String(err)
            });
        });
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

    ipcMain.handle('open-detached-internal-browser', async (event, targetUrl, title = '') => {
        const url = normalizeTargetUrl(targetUrl);
        if (isMlAutomationProtected() && isMercadoLivreLogoutUrl(url)) {
            logElectronLifecycle('blocked-detached-ml-logout-load-during-favoritos', { url });
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

app.on('before-quit', () => {
    logElectronLifecycle('before-quit');
    stopLocalBackend();
    clearStartupIncomplete();
    flushPersistentSessions().catch(() => {});
});
