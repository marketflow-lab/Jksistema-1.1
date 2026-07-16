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

function loadElectronUpdaterModule() {
    const candidates = ['electron-updater'];
    if (process.resourcesPath) {
        candidates.push(
            path.join(process.resourcesPath, 'app.asar', 'node_modules', 'electron-updater'),
            path.join(process.resourcesPath, 'app.asar.unpacked', 'node_modules', 'electron-updater'),
            path.join(process.resourcesPath, 'node_modules', 'electron-updater')
        );
    }
    candidates.push(
        path.join(__dirname, 'electron_app', 'node_modules', 'electron-updater'),
        path.join(__dirname, '..', 'electron_app', 'node_modules', 'electron-updater')
    );

    let lastError = null;
    for (const candidate of candidates) {
        try {
            return require(candidate);
        } catch (err) {
            lastError = err;
        }
    }
    throw lastError || new Error('electron-updater nao encontrado.');
}

let autoUpdater = null;
try {
    ({ autoUpdater } = loadElectronUpdaterModule());
} catch (err) {
    console.warn('[Atualizacao] electron-updater indisponivel:', err && err.message ? err.message : err);
}

// Evita crash silencioso de GPU em alguns ambientes Windows.
app.disableHardwareAcceleration();
app.commandLine.appendSwitch('disable-gpu');
app.commandLine.appendSwitch('disable-http-cache');
const JK_REMOTE_DEBUGGING_PORT = String(process.env.JK_REMOTE_DEBUGGING_PORT || '').trim();
if (/^\d{2,5}$/.test(JK_REMOTE_DEBUGGING_PORT)) {
    app.commandLine.appendSwitch('remote-debugging-port', JK_REMOTE_DEBUGGING_PORT);
    app.commandLine.appendSwitch('remote-allow-origins', `http://127.0.0.1:${JK_REMOTE_DEBUGGING_PORT}`);
}
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
let localBackendStopPromise = null;

function resolveAppRootDir() {
    const packagedRoot = app.isPackaged && process.resourcesPath
        ? path.join(process.resourcesPath, JK_LOCAL_BACKEND_DIR_NAME)
        : null;
    const envRoot = process.env.JK_APP_ROOT_DIR;
    const candidates = app.isPackaged
        ? [
            packagedRoot,
            app.isPackaged ? process.resourcesPath : null,
            fs.existsSync(path.join(__dirname, 'backend_api.py')) ? __dirname : null,
            path.resolve(__dirname, '..'),
            envRoot,
            __dirname
        ].filter(Boolean)
        : [
            envRoot,
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

function resolveElectronUserDataDir(options = {}) {
    const configuredDir = String(options.configuredDir || '').trim();
    if (configuredDir) return path.resolve(configuredDir);
    const platform = String(options.platform || process.platform);
    const appDataDir = String(options.appDataDir || '').trim();
    if (platform === 'win32' && appDataDir) {
        return path.resolve(appDataDir, 'JK Sistema Cliente');
    }
    const defaultUserDataDir = String(options.defaultUserDataDir || '').trim();
    return path.resolve(defaultUserDataDir || path.join(JK_APP_ROOT_DIR, 'info', 'electron_user_data'));
}

const JK_DEFAULT_ELECTRON_USER_DATA_DIR = resolveElectronUserDataDir({
    appDataDir: app.getPath('appData'),
    defaultUserDataDir: app.getPath('userData'),
    platform: process.platform
});
const JK_ELECTRON_USER_DATA_DIR = resolveElectronUserDataDir({
    configuredDir: process.env.JK_ELECTRON_USER_DATA_DIR,
    appDataDir: app.getPath('appData'),
    defaultUserDataDir: JK_DEFAULT_ELECTRON_USER_DATA_DIR,
    platform: process.platform
});
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
let avantProStorageImportAttempted = false;
let avantProStorageImportPromise = null;
let avantProStorageSnapshotPromise = null;
let avantProSnapshotArtifactsRecovered = false;
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
const ML_BROWSER_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';
const AVANTPRO_CHROME_EXTENSION_ID = 'jdefnfmbnchmnjkcknaadaddgjbgephh';
const AVANTPRO_EXTENSION_DEFAULT_ENABLED = !/^(0|false|nao|não|off)$/i.test(String(process.env.JK_ENABLE_AVANTPRO_EXTENSION || 'true'));
const AVANTPRO_AUTO_STORAGE_RECOVERY = /^(1|true|sim|yes|on)$/i.test(String(process.env.JK_AVANTPRO_AUTO_STORAGE_RECOVERY || ''));

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

let JK_PRIMARY_INSTANCE_LOCK_ACQUIRED = true;
const JK_ALLOW_MULTIPLE_INSTANCES_FOR_TESTS = /^(1|true|yes)$/i.test(
    String(process.env.JK_ALLOW_MULTIPLE_INSTANCES_FOR_TESTS || '')
);
try {
    if (!JK_ALLOW_MULTIPLE_INSTANCES_FOR_TESTS && typeof app.requestSingleInstanceLock === 'function') {
        JK_PRIMARY_INSTANCE_LOCK_ACQUIRED = app.requestSingleInstanceLock();
    }
} catch (err) {
    JK_PRIMARY_INSTANCE_LOCK_ACQUIRED = true;
    logElectronLifecycle('single-instance-lock-warning', {
        error: err && err.message ? err.message : String(err)
    });
}

function focusPrimaryJkWindow() {
    const target = mainWindow && !mainWindow.isDestroyed()
        ? mainWindow
        : BrowserWindow.getAllWindows().find(win => win && !win.isDestroyed());
    if (!target) return false;
    if (target.isMinimized()) target.restore();
    target.show();
    target.focus();
    return true;
}

if (JK_PRIMARY_INSTANCE_LOCK_ACQUIRED) {
    logElectronLifecycle('authentication-profile-selected', {
        userDataDir: JK_ELECTRON_USER_DATA_DIR,
        appRootDir: JK_APP_ROOT_DIR,
        packaged: !!app.isPackaged
    });
    app.on('second-instance', () => {
        logElectronLifecycle('second-instance-focused-primary', {
            focused: focusPrimaryJkWindow()
        });
    });
} else {
    logElectronLifecycle('second-instance-rejected', {
        userDataDir: JK_ELECTRON_USER_DATA_DIR
    });
    setImmediate(() => app.quit());
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

function getAvantProLastGoodStorageRootDir() {
    return path.join(JK_ELECTRON_USER_DATA_DIR, '_avantpro_storage_last_good');
}

function getAvantProLastGoodStorageDir() {
    return path.join(getAvantProLastGoodStorageRootDir(), AVANTPRO_CHROME_EXTENSION_ID);
}

function getAvantProLastGoodManifestPath() {
    return path.join(getAvantProLastGoodStorageRootDir(), 'manifest.json');
}

function recoverAvantProSnapshotTransactionArtifacts() {
    if (avantProSnapshotArtifactsRecovered) return;
    avantProSnapshotArtifactsRecovered = true;
    const snapshotRoot = getAvantProLastGoodStorageRootDir();
    if (!fs.existsSync(snapshotRoot)) return;
    const snapshotDir = getAvantProLastGoodStorageDir();
    const manifestPath = getAvantProLastGoodManifestPath();
    let entries = [];
    try { entries = fs.readdirSync(snapshotRoot, { withFileTypes: true }); } catch (_err) { return; }
    const candidates = entries
        .filter(entry => entry && entry.isDirectory() && (
            entry.name.startsWith(`${AVANTPRO_CHROME_EXTENSION_ID}.previous_`)
            || entry.name.startsWith(`${AVANTPRO_CHROME_EXTENSION_ID}.tmp_`)
        ))
        .map(entry => path.join(snapshotRoot, entry.name))
        .sort((left, right) => getDirectoryLatestFileMtimeMs(right) - getDirectoryLatestFileMtimeMs(left));
    let currentAuth = inspectAvantProExtensionStorage(snapshotDir);
    if (!avantProStorageAuthLooksUsable(currentAuth)) {
        const recoveryDir = candidates.find(candidate => (
            avantProStorageAuthLooksUsable(inspectAvantProExtensionStorage(candidate))
        ));
        if (recoveryDir) {
            try {
                removeManagedDirectory(snapshotDir, snapshotRoot);
                fs.renameSync(recoveryDir, snapshotDir);
                currentAuth = inspectAvantProExtensionStorage(snapshotDir);
                logElectronLifecycle('avantpro-storage-snapshot-transaction-recovered', {
                    recoveryDir,
                    snapshotDir,
                    auth: publicAvantProAuthInfo(currentAuth)
                });
            } catch (err) {
                logElectronLifecycle('avantpro-storage-snapshot-transaction-recovery-failed', {
                    recoveryDir,
                    snapshotDir,
                    error: err && err.message ? err.message : String(err)
                });
            }
        }
    }
    let manifestValid = false;
    try {
        manifestValid = !!JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
    } catch (_err) {}
    if (avantProStorageAuthLooksUsable(currentAuth) && !manifestValid) {
        try {
            const recoveredFiles = countDirectoryFilesWithoutLocks(snapshotDir);
            const manifest = {
                savedAt: new Date().toISOString(),
                reason: 'transaction-artifact-recovery',
                sourceDir: snapshotDir,
                snapshotDir,
                copied: recoveredFiles,
                sourceFiles: recoveredFiles,
                auth: publicAvantProAuthInfo(currentAuth)
            };
            const tempManifest = `${manifestPath}.recovery_${process.pid}`;
            fs.writeFileSync(tempManifest, JSON.stringify(manifest, null, 2), 'utf8');
            if (fs.existsSync(manifestPath)) fs.rmSync(manifestPath, { force: true });
            fs.renameSync(tempManifest, manifestPath);
        } catch (_err) {}
    }
    if (!avantProStorageAuthLooksUsable(currentAuth)) return;
    for (const candidate of candidates) {
        if (path.resolve(candidate) === path.resolve(snapshotDir)) continue;
        try { removeManagedDirectory(candidate, snapshotRoot); } catch (_err) {}
    }
    for (const entry of entries) {
        if (!entry || entry.isDirectory()) continue;
        if (!/^manifest\.json\.(?:tmp|previous)_/i.test(entry.name)) continue;
        try { fs.rmSync(path.join(snapshotRoot, entry.name), { force: true }); } catch (_err) {}
    }
}

function getChromeUserDataDir() {
    return process.env.LOCALAPPDATA
        ? path.join(process.env.LOCALAPPDATA, 'Google', 'Chrome', 'User Data')
        : '';
}

function getDirectoryLatestFileMtimeMs(dir) {
    if (!dir || !fs.existsSync(dir)) return 0;
    let latest = 0;
    const stack = [dir];
    while (stack.length) {
        const current = stack.pop();
        let entries = [];
        try {
            entries = fs.readdirSync(current, { withFileTypes: true });
        } catch (_err) {
            continue;
        }
        for (const entry of entries) {
            if (!entry || entry.name === 'LOCK') continue;
            const fullPath = path.join(current, entry.name);
            try {
                const stat = fs.statSync(fullPath);
                if (stat && stat.mtimeMs > latest) latest = stat.mtimeMs;
                if (entry.isDirectory()) stack.push(fullPath);
            } catch (_err) {}
        }
    }
    return latest;
}

function getChromeExtensionStorageDir(chromeUserData, profileName, extensionId) {
    if (!chromeUserData || !profileName || !extensionId) return '';
    return path.join(
        chromeUserData,
        profileName,
        'Local Extension Settings',
        extensionId
    );
}

function getChromeExtensionStorageMtimeMs(chromeUserData, profileName, extensionId) {
    return getDirectoryLatestFileMtimeMs(getChromeExtensionStorageDir(chromeUserData, profileName, extensionId));
}

function inspectAvantProExtensionStorage(storageDir) {
    const info = {
        hasUser: false,
        hasAccounts: false,
        hasAccessToken: false,
        hasLoginAt: false,
        maxExpiresInMs: 0,
        latestMtimeMs: 0
    };
    if (!storageDir || !fs.existsSync(storageDir)) return info;
    const stack = [storageDir];
    let bytesRead = 0;
    const maxBytes = 12 * 1024 * 1024;
    while (stack.length && bytesRead < maxBytes) {
        const current = stack.pop();
        let entries = [];
        try {
            entries = fs.readdirSync(current, { withFileTypes: true });
        } catch (_err) {
            continue;
        }
        for (const entry of entries) {
            if (!entry || entry.name === 'LOCK') continue;
            const fullPath = path.join(current, entry.name);
            let stat = null;
            try {
                stat = fs.statSync(fullPath);
                if (stat && stat.mtimeMs > info.latestMtimeMs) info.latestMtimeMs = stat.mtimeMs;
            } catch (_err) {
                continue;
            }
            if (entry.isDirectory()) {
                stack.push(fullPath);
                continue;
            }
            if (!stat || !stat.size || stat.size > maxBytes) continue;
            try {
                const buffer = fs.readFileSync(fullPath);
                bytesRead += buffer.length;
                const text = buffer.toString('latin1');
                if (/avantproUser/i.test(text)) info.hasUser = true;
                if (/avantproAccounts/i.test(text)) info.hasAccounts = true;
                if (/accessToken/i.test(text)) info.hasAccessToken = true;
                if (/loginAt/i.test(text)) info.hasLoginAt = true;
                const regex = /expiresIn[^0-9]{0,40}([0-9]{10,})/gi;
                let match = null;
                while ((match = regex.exec(text))) {
                    const value = Number(match[1]);
                    if (Number.isFinite(value) && value > info.maxExpiresInMs) {
                        info.maxExpiresInMs = value;
                    }
                }
            } catch (_err) {}
        }
    }
    // Na extensao atual, accessToken + loginAt formam o registro de login.
    // avantproAccounts.expiresIn e apenas a validade do cache de contas e
    // nao pode fazer o aplicativo descartar uma sessao que ainda pode renovar.
    info.hasAuthRecord = !!(info.hasAccessToken && info.hasLoginAt);
    info.hasLikelyAuth = !!info.hasAuthRecord;
    info.cacheFresh = info.maxExpiresInMs > Date.now();
    info.authValid = info.hasLikelyAuth;
    return info;
}

function findInstalledChromeExtensionStorageCandidates(extensionId) {
    const chromeUserData = getChromeUserDataDir();
    if (!chromeUserData || !fs.existsSync(chromeUserData)) return [];
    const candidates = [];
    for (const profile of fs.readdirSync(chromeUserData, { withFileTypes: true })) {
        if (!profile.isDirectory()) continue;
        if (profile.name !== 'Default' && !/^Profile\s+\d+$/i.test(profile.name)) continue;
        const storageDir = getChromeExtensionStorageDir(chromeUserData, profile.name, extensionId);
        if (!fs.existsSync(storageDir)) continue;
        const latestMtimeMs = getDirectoryLatestFileMtimeMs(storageDir);
        if (!latestMtimeMs) continue;
        let profileDisplayName = '';
        try {
            const preferencesPath = path.join(chromeUserData, profile.name, 'Preferences');
            if (fs.existsSync(preferencesPath)) {
                const preferences = JSON.parse(fs.readFileSync(preferencesPath, 'utf8'));
                profileDisplayName = String(preferences && preferences.profile && preferences.profile.name || '').trim();
            }
        } catch (_err) {}
        const configuredPreferredProfile = String(process.env.JK_AVANTPRO_CHROME_PROFILE || '').trim();
        const normalizedDisplay = profileDisplayName
            .normalize('NFD')
            .replace(/[\u0300-\u036f]/g, '')
            .toLowerCase();
        const normalizedProfileName = String(profile.name || '').toLowerCase();
        const preferredScore = configuredPreferredProfile
            ? (profile.name === configuredPreferredProfile || profileDisplayName === configuredPreferredProfile ? 200 : 0)
            : (/jk\s*pecas|jk\s*pe[cç]as/i.test(profileDisplayName) || /jk\s*pecas/.test(normalizedDisplay) ? 100 : 0);
        candidates.push({
            profile: profile.name,
            profileDisplayName,
            profileDir: path.join(chromeUserData, profile.name),
            storageDir,
            latestMtimeMs,
            authInfo: inspectAvantProExtensionStorage(storageDir),
            preferredScore,
            normalizedProfileName
        });
    }
    return candidates.sort((left, right) => {
        const rightAuth = right.authInfo || {};
        const leftAuth = left.authInfo || {};
        const validDiff = (rightAuth.authValid ? 1 : 0) - (leftAuth.authValid ? 1 : 0);
        if (validDiff) return validDiff;
        const expiresDiff = Number(rightAuth.maxExpiresInMs || 0) - Number(leftAuth.maxExpiresInMs || 0);
        if (expiresDiff) return expiresDiff;
        const authDiff = (rightAuth.hasLikelyAuth ? 1 : 0) - (leftAuth.hasLikelyAuth ? 1 : 0);
        if (authDiff) return authDiff;
        const scoreDiff = (right.preferredScore || 0) - (left.preferredScore || 0);
        if (scoreDiff) return scoreDiff;
        return (right.latestMtimeMs || 0) - (left.latestMtimeMs || 0);
    });
}

function copyDirectoryWithoutLocks(sourceDir, targetDir) {
    if (!sourceDir || !targetDir || !fs.existsSync(sourceDir)) return 0;
    fs.mkdirSync(targetDir, { recursive: true });
    let copied = 0;
    for (const entry of fs.readdirSync(sourceDir, { withFileTypes: true })) {
        if (!entry || entry.name === 'LOCK') continue;
        const sourcePath = path.join(sourceDir, entry.name);
        const targetPath = path.join(targetDir, entry.name);
        if (entry.isDirectory()) {
            copied += copyDirectoryWithoutLocks(sourcePath, targetPath);
            continue;
        }
        try {
            fs.copyFileSync(sourcePath, targetPath);
            copied += 1;
        } catch (err) {
            logElectronLifecycle('avantpro-storage-import-copy-file-failed', {
                sourcePath,
                targetPath,
                error: err && err.message ? err.message : String(err)
            });
        }
    }
    return copied;
}

function countDirectoryFilesWithoutLocks(sourceDir) {
    if (!sourceDir || !fs.existsSync(sourceDir)) return 0;
    let total = 0;
    for (const entry of fs.readdirSync(sourceDir, { withFileTypes: true })) {
        if (!entry || entry.name === 'LOCK') continue;
        const sourcePath = path.join(sourceDir, entry.name);
        total += entry.isDirectory() ? countDirectoryFilesWithoutLocks(sourcePath) : 1;
    }
    return total;
}

function pathIsInside(parentDir, candidatePath) {
    const parent = path.resolve(parentDir);
    const candidate = path.resolve(candidatePath);
    return candidate === parent || candidate.startsWith(parent + path.sep);
}

function removeManagedDirectory(targetDir, allowedRootDir) {
    if (!targetDir || !allowedRootDir || !fs.existsSync(targetDir)) return;
    if (!pathIsInside(allowedRootDir, targetDir)) {
        throw new Error(`Recusado remover pasta fora da area gerenciada: ${targetDir}`);
    }
    fs.rmSync(targetDir, { recursive: true, force: true });
}

function avantProStorageAuthLooksUsable(authInfo) {
    return !!(authInfo && authInfo.hasAuthRecord);
}

function publicAvantProAuthInfo(authInfo) {
    return {
        hasUser: !!(authInfo && authInfo.hasUser),
        hasAccounts: !!(authInfo && authInfo.hasAccounts),
        hasAccessToken: !!(authInfo && authInfo.hasAccessToken),
        hasLoginAt: !!(authInfo && authInfo.hasLoginAt),
        hasAuthRecord: !!(authInfo && authInfo.hasAuthRecord),
        hasLikelyAuth: !!(authInfo && authInfo.hasLikelyAuth),
        authValid: !!(authInfo && authInfo.authValid),
        cacheFresh: !!(authInfo && authInfo.cacheFresh),
        maxExpiresInMs: Number(authInfo && authInfo.maxExpiresInMs || 0),
        latestMtimeMs: Number(authInfo && authInfo.latestMtimeMs || 0)
    };
}

function getAvantProStorageSnapshotStatus() {
    recoverAvantProSnapshotTransactionArtifacts();
    const currentDir = getAvantProExtensionStorageDir();
    const snapshotDir = getAvantProLastGoodStorageDir();
    const currentAuth = inspectAvantProExtensionStorage(currentDir);
    const snapshotAuth = inspectAvantProExtensionStorage(snapshotDir);
    let manifest = null;
    try {
        const manifestPath = getAvantProLastGoodManifestPath();
        if (fs.existsSync(manifestPath)) {
            manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
        }
    } catch (_err) {}
    return {
        success: true,
        currentDir,
        snapshotDir,
        manifestPath: getAvantProLastGoodManifestPath(),
        hasCurrentStorage: fs.existsSync(currentDir),
        hasSnapshotStorage: fs.existsSync(snapshotDir),
        currentAuth: publicAvantProAuthInfo(currentAuth),
        snapshotAuth: publicAvantProAuthInfo(snapshotAuth),
        currentUsable: avantProStorageAuthLooksUsable(currentAuth),
        snapshotUsable: avantProStorageAuthLooksUsable(snapshotAuth),
        manifest
    };
}

async function saveAvantProExtensionStorageSnapshot(reason = 'manual', details = {}) {
    const previousSnapshot = avantProStorageSnapshotPromise || Promise.resolve(null);
    let queuedSnapshot = null;
    queuedSnapshot = previousSnapshot
        .catch(() => null)
        .then(() => saveAvantProExtensionStorageSnapshotUnlocked(reason, details))
        .finally(() => {
            if (avantProStorageSnapshotPromise === queuedSnapshot) {
                avantProStorageSnapshotPromise = null;
            }
        });
    avantProStorageSnapshotPromise = queuedSnapshot;
    return queuedSnapshot;
}

async function saveAvantProExtensionStorageSnapshotUnlocked(reason = 'manual', details = {}) {
    recoverAvantProSnapshotTransactionArtifacts();
    if (!isAvantProExtensionEnabled()) {
        return { success: false, skipped: true, reason: 'avantpro-disabled' };
    }
    if (typeof flushPersistentSessions === 'function') {
        await flushPersistentSessions().catch((err) => {
            logElectronLifecycle('avantpro-storage-snapshot-flush-warning', {
                reason,
                error: err && err.message ? err.message : String(err)
            });
        });
    }
    const sourceDir = getAvantProExtensionStorageDir();
    const sourceAuth = inspectAvantProExtensionStorage(sourceDir);
    const sourceFiles = countDirectoryFilesWithoutLocks(sourceDir);
    if (!avantProStorageAuthLooksUsable(sourceAuth)) {
        logElectronLifecycle('avantpro-storage-snapshot-skipped-no-auth', {
            reason,
            sourceDir,
            sourceAuth: publicAvantProAuthInfo(sourceAuth)
        });
        return {
            success: false,
            skipped: true,
            reason: 'source-without-usable-auth',
            sourceDir,
            sourceAuth: publicAvantProAuthInfo(sourceAuth)
        };
    }
    const snapshotRoot = getAvantProLastGoodStorageRootDir();
    const snapshotDir = getAvantProLastGoodStorageDir();
    const liveAuthConfirmed = !!(details && details.liveAuthConfirmed === true);
    const existingSnapshotAuth = inspectAvantProExtensionStorage(snapshotDir);
    if (!liveAuthConfirmed && avantProStorageAuthLooksUsable(existingSnapshotAuth)) {
        return {
            success: false,
            skipped: true,
            reason: 'existing-good-kept-without-live-auth-confirmation',
            sourceDir,
            snapshotDir,
            sourceAuth: publicAvantProAuthInfo(sourceAuth),
            snapshotAuth: publicAvantProAuthInfo(existingSnapshotAuth)
        };
    }
    const tempDir = path.join(snapshotRoot, `${AVANTPRO_CHROME_EXTENSION_ID}.tmp_${process.pid}_${Date.now()}`);
    const previousDir = path.join(snapshotRoot, `${AVANTPRO_CHROME_EXTENSION_ID}.previous_${process.pid}_${Date.now()}`);
    const manifestPath = getAvantProLastGoodManifestPath();
    const tempManifestPath = `${manifestPath}.tmp_${process.pid}_${Date.now()}`;
    const previousManifestPath = `${manifestPath}.previous_${process.pid}_${Date.now()}`;
    fs.mkdirSync(snapshotRoot, { recursive: true });
    removeManagedDirectory(tempDir, snapshotRoot);
    removeManagedDirectory(previousDir, snapshotRoot);
    let copied = 0;
    let previousMoved = false;
    let previousManifestMoved = false;
    let snapshotPromoted = false;
    try {
        copied = copyDirectoryWithoutLocks(sourceDir, tempDir);
        const tempAuth = inspectAvantProExtensionStorage(tempDir);
        if (!copied || copied !== sourceFiles || !avantProStorageAuthLooksUsable(tempAuth)) {
            removeManagedDirectory(tempDir, snapshotRoot);
            logElectronLifecycle('avantpro-storage-snapshot-skipped-empty', {
                reason,
                sourceDir,
                copied,
                sourceFiles,
                tempAuth: publicAvantProAuthInfo(tempAuth)
            });
            return {
                success: false,
                skipped: true,
                reason: 'snapshot-copy-without-usable-auth',
                copied,
                sourceFiles,
                sourceDir,
                auth: publicAvantProAuthInfo(tempAuth)
            };
        }
        if (fs.existsSync(snapshotDir)) {
            fs.renameSync(snapshotDir, previousDir);
            previousMoved = true;
        }
        fs.renameSync(tempDir, snapshotDir);
        snapshotPromoted = true;
        const manifest = {
            savedAt: new Date().toISOString(),
            reason: String(reason || 'manual'),
            details: details && typeof details === 'object' ? details : {},
            sourceDir,
            snapshotDir,
            copied,
            sourceFiles,
            auth: publicAvantProAuthInfo(tempAuth)
        };
        fs.writeFileSync(tempManifestPath, JSON.stringify(manifest, null, 2), 'utf8');
        if (fs.existsSync(manifestPath)) {
            fs.renameSync(manifestPath, previousManifestPath);
            previousManifestMoved = true;
        }
        fs.renameSync(tempManifestPath, manifestPath);
        try { if (previousMoved) removeManagedDirectory(previousDir, snapshotRoot); } catch (_cleanupErr) {}
        try {
            if (previousManifestMoved && fs.existsSync(previousManifestPath)) {
                fs.rmSync(previousManifestPath, { force: true });
            }
        } catch (_cleanupErr) {}
        logElectronLifecycle('avantpro-storage-snapshot-saved', manifest);
        return { success: true, copied, sourceDir, snapshotDir, auth: manifest.auth, manifest };
    } catch (err) {
        try { removeManagedDirectory(tempDir, snapshotRoot); } catch (_err) {}
        try { if (fs.existsSync(tempManifestPath)) fs.rmSync(tempManifestPath, { force: true }); } catch (_err) {}
        try {
            if (previousManifestMoved && fs.existsSync(previousManifestPath)) {
                if (fs.existsSync(manifestPath)) fs.rmSync(manifestPath, { force: true });
                fs.renameSync(previousManifestPath, manifestPath);
            }
        } catch (manifestRollbackErr) {
            logElectronLifecycle('avantpro-storage-snapshot-manifest-rollback-failed', {
                manifestPath,
                previousManifestPath,
                error: manifestRollbackErr && manifestRollbackErr.message ? manifestRollbackErr.message : String(manifestRollbackErr)
            });
        }
        try {
            if (previousMoved && fs.existsSync(previousDir)) {
                removeManagedDirectory(snapshotDir, snapshotRoot);
                fs.renameSync(previousDir, snapshotDir);
            } else if (snapshotPromoted) {
                removeManagedDirectory(snapshotDir, snapshotRoot);
            }
        } catch (rollbackErr) {
            logElectronLifecycle('avantpro-storage-snapshot-rollback-failed', {
                snapshotDir,
                previousDir,
                error: rollbackErr && rollbackErr.message ? rollbackErr.message : String(rollbackErr)
            });
        }
        logElectronLifecycle('avantpro-storage-snapshot-failed', {
            reason,
            sourceDir,
            snapshotDir,
            copied,
            error: err && err.message ? err.message : String(err)
        });
        return {
            success: false,
            reason: 'snapshot-failed',
            sourceDir,
            snapshotDir,
            copied,
            error: err && err.message ? err.message : String(err)
        };
    }
}

function rollbackAvantProStorageRestore(backup, targetDir) {
    const backupDir = backup && backup.success ? String(backup.backupDir || '') : '';
    if (!backupDir || !fs.existsSync(backupDir)) {
        if (!(backup && backup.missing)) return false;
        try {
            if (fs.existsSync(targetDir)) removeManagedDirectory(targetDir, JK_ELECTRON_USER_DATA_DIR);
            return true;
        } catch (_err) {
            return false;
        }
    }
    if (!pathIsInside(JK_ELECTRON_USER_DATA_DIR, backupDir) || !pathIsInside(JK_ELECTRON_USER_DATA_DIR, targetDir)) {
        return false;
    }
    try {
        if (fs.existsSync(targetDir)) {
            removeManagedDirectory(targetDir, JK_ELECTRON_USER_DATA_DIR);
        }
        fs.mkdirSync(path.dirname(targetDir), { recursive: true });
        fs.renameSync(backupDir, targetDir);
        return true;
    } catch (err) {
        logElectronLifecycle('avantpro-storage-snapshot-restore-rollback-failed', {
            backupDir,
            targetDir,
            error: err && err.message ? err.message : String(err)
        });
        return false;
    }
}

async function restoreAvantProExtensionStorageSnapshot(reason = 'manual', details = {}, options = {}) {
    recoverAvantProSnapshotTransactionArtifacts();
    if (!isAvantProExtensionEnabled()) {
        return { success: false, skipped: true, reason: 'avantpro-disabled' };
    }
    const snapshotDir = getAvantProLastGoodStorageDir();
    const snapshotAuth = inspectAvantProExtensionStorage(snapshotDir);
    const snapshotFiles = countDirectoryFilesWithoutLocks(snapshotDir);
    if (!avantProStorageAuthLooksUsable(snapshotAuth)) {
        return {
            success: false,
            skipped: true,
            reason: 'snapshot-without-usable-auth',
            snapshotDir,
            snapshotAuth: publicAvantProAuthInfo(snapshotAuth)
        };
    }
    const targetDir = getAvantProExtensionStorageDir();
    const targetAuth = inspectAvantProExtensionStorage(targetDir);
    const targetUsable = avantProStorageAuthLooksUsable(targetAuth);
    const targetAtLeastAsRecent = Number(targetAuth.latestMtimeMs || 0) >= Number(snapshotAuth.latestMtimeMs || 0);
    if (targetUsable && (!options.force || targetAtLeastAsRecent)) {
        return {
            success: false,
            skipped: true,
            reason: 'target-current',
            targetDir,
            targetAuth: publicAvantProAuthInfo(targetAuth),
            snapshotAuth: publicAvantProAuthInfo(snapshotAuth)
        };
    }
    logElectronLifecycle('avantpro-storage-snapshot-restore-started', {
        reason,
        details,
        snapshotDir,
        targetDir,
        snapshotAuth: publicAvantProAuthInfo(snapshotAuth),
        targetAuth: publicAvantProAuthInfo(targetAuth),
        force: !!options.force
    });
    await unloadAvantProExtensionForRecovery();
    const backup = backupAndResetAvantProExtensionStorage(`restore_snapshot_${reason}`);
    if (!backup.success && !backup.missing) {
        return { success: false, reason: 'target-backup-failed', backup, snapshotDir, targetDir };
    }
    let copied = 0;
    let restoredAuth = null;
    try {
        fs.mkdirSync(path.dirname(targetDir), { recursive: true });
        copied = copyDirectoryWithoutLocks(snapshotDir, targetDir);
        restoredAuth = inspectAvantProExtensionStorage(targetDir);
    } catch (err) {
        const rolledBack = rollbackAvantProStorageRestore(backup, targetDir);
        return {
            success: false,
            reason: 'snapshot-restore-copy-failed',
            copied,
            backup,
            rolledBack,
            snapshotDir,
            targetDir,
            error: err && err.message ? err.message : String(err)
        };
    }
    if (!copied || copied !== snapshotFiles || !avantProStorageAuthLooksUsable(restoredAuth)) {
        const rolledBack = rollbackAvantProStorageRestore(backup, targetDir);
        logElectronLifecycle('avantpro-storage-snapshot-restore-no-auth', {
            reason,
            snapshotDir,
            targetDir,
            copied,
            snapshotFiles,
            restoredAuth: publicAvantProAuthInfo(restoredAuth),
            backup,
            rolledBack
        });
        return {
            success: false,
            reason: 'restored-without-usable-auth',
            copied,
            snapshotFiles,
            backup,
            rolledBack,
            snapshotDir,
            targetDir,
            restoredAuth: publicAvantProAuthInfo(restoredAuth)
        };
    }
    if (options.reloadExtensions !== false) {
        chromeExtensionsLoadPromise = null;
        await ensureChromeExtensionsForMlSession({ forceRetryIfEmpty: true }).catch((err) => {
            logElectronLifecycle('avantpro-storage-snapshot-restore-reload-warning', {
                reason,
                error: err && err.message ? err.message : String(err)
            });
            return [];
        });
    }
    logElectronLifecycle('avantpro-storage-snapshot-restored', {
        reason,
        snapshotDir,
        targetDir,
        copied,
        backup,
        restoredAuth: publicAvantProAuthInfo(restoredAuth)
    });
    return {
        success: true,
        copied,
        backup,
        snapshotDir,
        targetDir,
        restoredAuth: publicAvantProAuthInfo(restoredAuth)
    };
}

async function ensureAvantProExtensionStorageFromSnapshot(reason = 'ensure', details = {}, options = {}) {
    const targetDir = getAvantProExtensionStorageDir();
    const targetAuth = inspectAvantProExtensionStorage(targetDir);
    if (avantProStorageAuthLooksUsable(targetAuth)) {
        return {
            success: false,
            skipped: true,
            reason: 'target-current',
            targetDir,
            targetAuth: publicAvantProAuthInfo(targetAuth)
        };
    }
    return await restoreAvantProExtensionStorageSnapshot(reason, details, {
        ...options,
        force: true
    });
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
        const snapshotRestore = await restoreAvantProExtensionStorageSnapshot(reason, details, {
            force: true,
            reloadExtensions: true
        }).catch((err) => ({
            success: false,
            error: err && err.message ? err.message : String(err)
        }));
        if (snapshotRestore && (snapshotRestore.success || snapshotRestore.reason === 'target-current')) {
            return {
                ...snapshotRestore,
                success: true,
                recoveredFromSnapshot: !!snapshotRestore.success,
                preservedCurrentStorage: snapshotRestore.reason === 'target-current'
            };
        }
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

async function importAvantProExtensionStorageFromChrome(reason = 'avantpro-account-action-required', details = {}, options = {}) {
    if (!isAvantProExtensionEnabled()) {
        return { success: false, skipped: true, reason: 'avantpro-disabled' };
    }
    if (avantProStorageImportPromise) return avantProStorageImportPromise;
    const allowRepeat = !!(options && options.allowRepeat);
    const reloadExtensions = !(options && options.reloadExtensions === false);
    if (avantProStorageImportAttempted && !allowRepeat) {
        return { success: false, skipped: true, reason: 'already-attempted' };
    }
    avantProStorageImportPromise = (async () => {
        const source = findInstalledChromeExtensionStorageCandidates(AVANTPRO_CHROME_EXTENSION_ID)[0] || null;
        if (!source) {
            logElectronLifecycle('avantpro-storage-import-no-source', { reason, details });
            return { success: false, missing: true };
        }
        const sourceAuth = source.authInfo || inspectAvantProExtensionStorage(source.storageDir);
        if (!sourceAuth.hasLikelyAuth) {
            logElectronLifecycle('avantpro-storage-import-no-auth-source', {
                reason,
                details,
                sourceProfile: source.profile,
                sourceDir: source.storageDir,
                sourceAuth
            });
            return { success: false, noAuthSource: true, source };
        }
        avantProStorageImportAttempted = true;
        const targetDir = getAvantProExtensionStorageDir();
        const targetAuth = inspectAvantProExtensionStorage(targetDir);
        if (
            avantProStorageAuthLooksUsable(targetAuth)
            && Number(targetAuth.latestMtimeMs || 0) >= Number(source.latestMtimeMs || 0) - 1000
        ) {
            logElectronLifecycle('avantpro-storage-import-skipped-target-current', {
                reason,
                details,
                sourceProfile: source.profile,
                sourceAuth,
                targetDir,
                targetAuth
            });
            return { success: false, skipped: true, reason: 'target-current', source, targetAuth };
        }
        logElectronLifecycle('avantpro-storage-import-started', {
            reason,
            details,
            sourceProfile: source.profile,
            sourceDir: source.storageDir,
            sourceAuth,
            targetDir,
            targetAuth
        });
        let sourceFiles = 0;
        try {
            sourceFiles = countDirectoryFilesWithoutLocks(source.storageDir);
        } catch (err) {
            logElectronLifecycle('avantpro-storage-import-source-enumeration-failed', {
                reason,
                sourceProfile: source.profile,
                sourceDir: source.storageDir,
                error: err && err.message ? err.message : String(err)
            });
            return {
                success: false,
                reason: 'source-enumeration-failed',
                source,
                error: err && err.message ? err.message : String(err)
            };
        }
        if (!sourceFiles) {
            return { success: false, reason: 'source-empty', source, sourceFiles };
        }
        await unloadAvantProExtensionForRecovery();
        const backup = backupAndResetAvantProExtensionStorage(`import_from_${source.profile}_${reason}`);
        if (!backup.success && !backup.missing) {
            logElectronLifecycle('avantpro-storage-import-aborted-reset-failed', {
                reason,
                sourceProfile: source.profile,
                sourceDir: source.storageDir,
                targetDir,
                backup
            });
            return { success: false, resetFailed: true, backup, source };
        }
        let copied = 0;
        let importedAuth = null;
        try {
            fs.mkdirSync(path.dirname(targetDir), { recursive: true });
            copied = copyDirectoryWithoutLocks(source.storageDir, targetDir);
            importedAuth = inspectAvantProExtensionStorage(targetDir);
        } catch (err) {
            const rolledBack = rollbackAvantProStorageRestore(backup, targetDir);
            return {
                success: false,
                reason: 'import-copy-failed',
                copied,
                sourceFiles,
                rolledBack,
                backup,
                source,
                error: err && err.message ? err.message : String(err)
            };
        }
        if (!copied || copied !== sourceFiles || !avantProStorageAuthLooksUsable(importedAuth)) {
            const rolledBack = rollbackAvantProStorageRestore(backup, targetDir);
            logElectronLifecycle('avantpro-storage-import-empty', {
                reason,
                sourceProfile: source.profile,
                sourceDir: source.storageDir,
                targetDir,
                backup,
                copied,
                sourceFiles,
                rolledBack,
                importedAuth: publicAvantProAuthInfo(importedAuth)
            });
            return { success: false, empty: true, copied, sourceFiles, rolledBack, backup, source };
        }
        chromeExtensionsLoadPromise = reloadExtensions ? null : chromeExtensionsLoadPromise;
        const loaded = reloadExtensions
            ? await ensureChromeExtensionsForMlSession({ forceRetryIfEmpty: true })
            : [];
        logElectronLifecycle('avantpro-storage-import-finished', {
            reason,
            sourceProfile: source.profile,
            copied,
            backup,
            reloadExtensions,
            loaded: loaded.map(ext => ({
                id: ext && ext.id,
                name: ext && ext.name,
                path: ext && ext.path
            }))
        });
        const snapshot = await saveAvantProExtensionStorageSnapshot('import_from_chrome_success', {
            reason,
            sourceProfile: source.profile,
            sourceDir: source.storageDir
        }).catch((err) => ({
            success: false,
            error: err && err.message ? err.message : String(err)
        }));
        return { success: true, copied, backup, source, snapshot };
    })().finally(() => {
        avantProStorageImportPromise = null;
    });
    return avantProStorageImportPromise;
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
            if (!AVANTPRO_AUTO_STORAGE_RECOVERY) {
                logElectronLifecycle('avantpro-storage-recovery-skipped', {
                    reason: 'auto-recovery-disabled',
                    payload
                });
                return;
            }
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
        'Cache',
        'Code Cache',
        'GPUCache',
        'DawnCache',
        'blob_storage',
        'Shared Dictionary'
    ];
    const resetAuthStorage = /^(1|true|sim|yes|on)$/i.test(String(process.env.JK_RESET_AUTH_STORAGE_ON_STARTUP_CRASH || ''));
    if (resetAuthStorage) {
        entries.unshift('Local Storage', 'Session Storage', 'WebStorage');
    }
    const moved = [];
    for (const entry of entries) {
        try {
            if (quarantineProfileEntry(entry, recoveryDir)) moved.push(entry);
        } catch (err) {
            logElectronLifecycle('profile-recovery-failed', { entry, error: err && err.message ? err.message : String(err) });
        }
    }
    logElectronLifecycle('profile-recovery-after-startup-crash', {
        moved,
        recoveryDir,
        authenticationStoragePreserved: !resetAuthStorage
    });
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
