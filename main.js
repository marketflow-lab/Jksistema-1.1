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

const { app, BrowserWindow, BrowserView, ipcMain, session, net, shell } = electron;
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { pathToFileURL } = require('url');

// Evita crash silencioso de GPU em alguns ambientes Windows.
app.disableHardwareAcceleration();
app.commandLine.appendSwitch('disable-gpu');
app.commandLine.appendSwitch('disable-http-cache');

const JK_APP_ROOT_DIR = __dirname;
const JK_ELECTRON_USER_DATA_DIR = process.env.JK_ELECTRON_USER_DATA_DIR || path.join(JK_APP_ROOT_DIR, 'info', 'electron_user_data');
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
const mlItemInfoCache = new Map();
const JK_BROWSER_SESSION_PARTITION = process.env.JK_BROWSER_SESSION_PARTITION || 'persist:jk-sistema-browser';
const AVANTPRO_CHROME_EXTENSION_ID = 'jdefnfmbnchmnjkcknaadaddgjbgephh';

function logElectronLifecycle(...args) {
    const message = `[Electron ${new Date().toISOString()}] ${args.map(value => {
        if (value instanceof Error) return value.stack || value.message;
        if (typeof value === 'string') return value;
        try { return JSON.stringify(value); } catch (_err) { return String(value); }
    }).join(' ')}\n`;
    try {
        fs.mkdirSync(path.join(__dirname, 'logs'), { recursive: true });
        fs.appendFileSync(path.join(__dirname, 'logs', 'electron_runtime.log'), message, 'utf8');
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
                fs.appendFileSync(
                    path.join(__dirname, 'logs', 'electron_runtime.log'),
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
    const recoveryDir = path.join(JK_APP_ROOT_DIR, 'info', `electron_user_data_recovery_${stamp}`);
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

function getImportCredentialsBatPath() {
    const candidates = [
        path.join(getAppRootDir(), 'ImportarCredenciais.bat'),
        path.join(__dirname, 'ImportarCredenciais.bat'),
        path.join(process.resourcesPath || '', 'ImportarCredenciais.bat')
    ].filter(Boolean);
    for (const candidate of candidates) {
        if (fs.existsSync(candidate)) return candidate;
    }
    return '';
}

async function openCredentialsImporter() {
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
    const bundledRoots = getChromeExtensionsRoots();
    const chromeInstalledExtensions = findInstalledChromeExtensionVersions(AVANTPRO_CHROME_EXTENSION_ID);
    const preferInstalled = /^(1|true|sim|yes)$/i.test(String(process.env.JK_PREFER_INSTALLED_CHROME_EXTENSIONS || ''));
    const searchRoots = preferInstalled
        ? [...explicitRoots, ...chromeInstalledExtensions, ...bundledRoots]
        : [...explicitRoots, ...bundledRoots, ...chromeInstalledExtensions];

    for (const root of searchRoots) {
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

function isLocalBackendUrl(targetUrl) {
        const value = String(targetUrl || '').trim().toLowerCase();
        return value.startsWith('http://127.0.0.1:8001/') || value.startsWith('http://localhost:8001/');
}

function renderBackendWaitingScreen(win, targetUrl, tentativa) {
        if (!win || win.isDestroyed()) return;
        const html = `<!DOCTYPE html>
        <html lang="pt-BR">
        <head>
            <meta charset="UTF-8" />
            <title>JK Sistema</title>
            <style>
                body { margin: 0; font-family: Segoe UI, Arial, sans-serif; background: linear-gradient(160deg, #061923, #0d3a4a); color: #e8fffb; display: flex; align-items: center; justify-content: center; min-height: 100vh; }
                .box { width: min(560px, 92vw); padding: 28px 30px; border-radius: 18px; background: rgba(4, 24, 34, 0.88); border: 1px solid rgba(120, 227, 212, 0.28); box-shadow: 0 18px 50px rgba(0,0,0,0.35); }
                h1 { margin: 0 0 10px; font-size: 24px; color: #8ee9de; }
                p { margin: 8px 0; line-height: 1.5; }
                .muted { color: #b7d7d2; font-size: 14px; }
                .pulse { width: 12px; height: 12px; border-radius: 999px; background: #53d4b7; display: inline-block; margin-right: 8px; box-shadow: 0 0 0 rgba(83,212,183,0.7); animation: pulse 1.5s infinite; }
                @keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(83,212,183,0.7); } 70% { box-shadow: 0 0 0 14px rgba(83,212,183,0); } 100% { box-shadow: 0 0 0 0 rgba(83,212,183,0); } }
            </style>
        </head>
        <body>
            <div class="box">
                <h1>JK Sistema</h1>
                <p><span class="pulse"></span>Preparando a tela desktop.</p>
                <p>O backend local ainda estÃ¡ iniciando. O aplicativo vai tentar se conectar novamente automaticamente.</p>
                <p class="muted">Tentativa: ${tentativa}</p>
                <p class="muted">URL local: ${targetUrl}</p>
            </div>
        </body>
        </html>`;
        win.loadURL(`data:text/html;charset=UTF-8,${encodeURIComponent(html)}`).catch(() => {});
}

function loadLocalAppWithRetry(win, targetUrl, tentativa = 1) {
        if (!win || win.isDestroyed()) return;

        const retry = () => {
                if (win.isDestroyed()) return;
                const nextAttempt = tentativa + 1;
                setTimeout(() => loadLocalAppWithRetry(win, targetUrl, nextAttempt), 2000);
        };

        win.loadURL(targetUrl).catch((err) => {
                console.error('Falha ao carregar URL local do JK Sistema:', err);
                if (!isLocalBackendUrl(targetUrl)) return;
                renderBackendWaitingScreen(win, targetUrl, tentativa);
                retry();
        });
}

function loadElectronTabbedShell(win, appUrl) {
    const shellPath = path.join(getAppRootDir(), 'electron_shell.html');
    const tabPreloadPath = path.join(getAppRootDir(), 'electron_tab_preload.js');

    if (!fs.existsSync(shellPath) || !fs.existsSync(tabPreloadPath)) {
        loadLocalAppWithRetry(win, appUrl);
        return;
    }

    const shellUrl = `${pathToFileURL(shellPath).toString()}?appUrl=${encodeURIComponent(appUrl)}&tabPreload=${encodeURIComponent(pathToFileURL(tabPreloadPath).toString())}&browserPartition=${encodeURIComponent(getBrowserSessionPartition())}`;
    win.loadURL(shellUrl).catch((err) => {
        console.error('Falha ao carregar shell de abas do JK Sistema:', err);
        loadLocalAppWithRetry(win, appUrl);
    });
}

function waitForMainFrameLoad(win, timeoutMs = 25000) {
    return new Promise((resolve, reject) => {
        if (!win || win.isDestroyed()) {
            reject(new Error('Janela interna indisponÃ­vel.'));
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
            finish(new Error(`Falha ao carregar pÃ¡gina (${errorCode}): ${errorDescription}`));
        };

        const timeout = setTimeout(() => {
            finish(new Error('Timeout ao carregar pÃ¡gina para extraÃ§Ã£o de links.'));
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

function ensureInternalBrowser(parent) {
    if (internalBrowserWindow && !internalBrowserWindow.isDestroyed()) {
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
    registerAvantProConsoleDiagnostics(internalBrowserWindow.webContents);
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
        registerAvantProConsoleDiagnostics(embeddedMlBrowserView.webContents);
        embeddedMlBrowserView.webContents.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36');
        embeddedMlBrowserView.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
            logElectronLifecycle('embedded-ml-browser-fail-load', { errorCode, errorDescription, validatedURL });
        });
    }
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

    const localUrlBase = process.env.JK_APP_URL || 'http://127.0.0.1:8001/dashboard.html';
    const localUrl = `${localUrlBase}${localUrlBase.includes('?') ? '&' : '?'}_jk_nocache=${Date.now()}`;
    loadElectronTabbedShell(win, localUrl);
    
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

function normalizarTextoMl(value) {
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
    const texto = String(value).trim();
    if (!texto) return null;
    const match = texto.match(/([0-9][0-9\.,]*)\s*(k|mil)?\b/i);
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
    if (!Number.isFinite(numero)) return null;
    const sufixo = String(match[2] || '').toLowerCase();
    const total = (sufixo === 'k' || sufixo === 'mil') ? (numero * 1000) : numero;
    return Number.isFinite(total) ? Math.round(total) : null;
}

function normalizarNomeVendedorDaResposta(item) {
    const seller = item && typeof item.seller === 'object' ? item.seller : {};
    const officialStore = item && typeof item.official_store === 'object' ? item.official_store : {};
    const candidatos = [
        seller.nickname,
        item && item.seller_name,
        item && item.official_store_name,
        officialStore.nickname,
        officialStore.name
    ];
    for (const candidato of candidatos) {
        const normalizado = normalizarTextoMl(candidato);
        if (normalizado) return normalizado;
    }
    return '';
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
        contents.on('will-navigate', (event, urlOrDetails) => {
            const url = getNavigationEventUrl(urlOrDetails);
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
            if (!isAllowedNavigationUrl(url)) {
                event.preventDefault();
            }
        });

        if (typeof contents.setWindowOpenHandler === 'function') {
            contents.setWindowOpenHandler(({ url }) => {
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
    ipcMain.handle('get-machine-info', () => {
        return getMachineInfo();
    });
    ipcMain.handle('import-credentials', async () => {
        return await openCredentialsImporter();
    });
    ipcMain.handle('ml-public-item-info', async (_event, itemId) => {
        const cleanId = String(itemId || '').trim().toUpperCase();
        if (!/^MLB\d+$/.test(cleanId)) {
            throw new Error('ID de anÃºncio invÃ¡lido.');
        }
        if (mlItemInfoCache.has(cleanId)) {
            return await mlItemInfoCache.get(cleanId);
        }

            const requestPromise = (async () => {
            const item = await netJsonGet(`https://api.mercadolibre.com/items/${encodeURIComponent(cleanId)}`);
            let vendedor = normalizarNomeVendedorDaResposta(item);
            const sellerId = item.seller_id || (item.seller && item.seller.id) || (item.official_store && item.official_store.seller_id);
            if (sellerId) {
                try {
                    const user = await netJsonGet(`https://api.mercadolibre.com/users/${encodeURIComponent(sellerId)}`);
                    const nomeUsuario = [
                        user.nickname,
                        user.official_store_name,
                        user.official_store && user.official_store.name
                    ].find(Boolean);
                    if (normalizarTextoMl(nomeUsuario)) {
                        vendedor = normalizarTextoMl(nomeUsuario);
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

        mlItemInfoCache.set(cleanId, requestPromise);
        try {
            return await requestPromise;
        } catch (err) {
            mlItemInfoCache.delete(cleanId);
            throw err;
        }
    });
    ipcMain.handle('ml-browser-item-info', async (_event, itemId) => {
        const cleanId = String(itemId || '').trim().toUpperCase().replace('-', '');
        if (!/^MLB\d+$/.test(cleanId)) {
            throw new Error('ID de anÃºncio invÃ¡lido.');
        }
        const cacheKey = `browser-public:${cleanId}`;
        if (mlItemInfoCache.has(cacheKey)) {
            return await mlItemInfoCache.get(cacheKey);
        }

        const requestPromise = (async () => {
            const item = await netJsonGet(`https://api.mercadolibre.com/items/${encodeURIComponent(cleanId)}`);
            let vendedor = normalizarNomeVendedorDaResposta(item);
            const sellerId = item.seller_id || (item.seller && item.seller.id) || (item.official_store && item.official_store.seller_id);
            if (sellerId) {
                try {
                    const user = await netJsonGet(`https://api.mercadolibre.com/users/${encodeURIComponent(sellerId)}`);
                    const nomeUsuario = [
                        user.nickname,
                        user.official_store_name,
                        user.official_store && user.official_store.name
                    ].find(Boolean);
                    if (normalizarTextoMl(nomeUsuario)) {
                        vendedor = normalizarTextoMl(nomeUsuario);
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

        mlItemInfoCache.set(cacheKey, requestPromise);
        try {
            return await requestPromise;
        } catch (err) {
            mlItemInfoCache.delete(cacheKey);
            throw err;
        }
    });
    ipcMain.handle('embedded-ml-browser-show', async (event, targetUrl, bounds) => {
        const url = normalizeTargetUrl(targetUrl);
        if (isMercadoLivreAdUrl(url)) {
            return await openUrlInGoogleChrome(url);
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
        if (isMercadoLivreAdUrl(url)) {
            return await openUrlInGoogleChrome(url);
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

            const extracted = await internalBrowser.webContents.executeJavaScript(`
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

            return extracted;
        } catch (err) {
            console.error('Erro ao extrair resultados do Mercado Livre:', err);
            throw err;
        }
    });

    createWindow();
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
    clearStartupIncomplete();
    flushPersistentSessions().catch(() => {});
});
