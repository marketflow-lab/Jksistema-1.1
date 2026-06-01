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

const JK_DEFAULT_APP_URL = 'https://jk-sistema-api-1077918177671.southamerica-east1.run.app/dashboard.html';

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
const mlItemInfoCache = new Map();
const ML_ITEM_INFO_CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutos
const JK_BROWSER_SESSION_PARTITION = process.env.JK_BROWSER_SESSION_PARTITION || 'persist:jk-sistema-browser';
const AVANTPRO_CHROME_EXTENSION_ID = 'jdefnfmbnchmnjkcknaadaddgjbgephh';

function logElectronLifecycle(...args) {
    const logDir = path.join(getAppRootDir(), 'logs');
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

function getUpdateErrorMessage(err) {
    if (!err) return 'Erro desconhecido ao verificar atualizacao.';
    return String(err.message || err).slice(0, 500);
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
        const version = info && info.version ? ` ${info.version}` : '';
        sendUpdateStatus('downloaded', { updateInfo: normalizeUpdateInfo(info) });
        dialog.showMessageBox({
            type: 'info',
            buttons: ['Reiniciar agora', 'Depois'],
            defaultId: 0,
            cancelId: 1,
            title: 'Atualizacao pronta',
            message: `Uma nova versao${version} foi baixada.`,
            detail: 'Reinicie o aplicativo para instalar a atualizacao.'
        }).then((result) => {
            if (result.response === 0) {
                autoUpdater.quitAndInstall(false, true);
            }
        }).catch((err) => {
            logElectronLifecycle('auto-update-dialog-error', err);
        });
    });
    return true;
}

async function checkForUpdates(manual = false) {
    if (!app.isPackaged) {
        const result = {
            success: false,
            skipped: true,
            reason: 'Atualizacao automatica funciona apenas no app instalado.'
        };
        if (manual) {
            await dialog.showMessageBox({
                type: 'info',
                title: 'Atualizacao',
                message: result.reason
            });
        }
        return result;
    }
    if (!autoUpdater) {
        const result = {
            success: false,
            skipped: true,
            reason: 'electron-updater nao esta disponivel neste pacote.'
        };
        if (manual) {
            await dialog.showMessageBox({
                type: 'warning',
                title: 'Atualizacao indisponivel',
                message: result.reason
            });
        }
        return result;
    }
    if (updateCheckInProgress) {
        return { success: true, checking: true };
    }

    registerAutoUpdateEvents();
    updateCheckInProgress = true;
    try {
        const result = await autoUpdater.checkForUpdates();
        if (manual && !(result && result.updateInfo && result.updateInfo.version && result.updateInfo.version !== app.getVersion())) {
            await dialog.showMessageBox({
                type: 'info',
                title: 'Atualizacao',
                message: 'Voce ja esta usando a versao mais recente.'
            });
        }
        return {
            success: true,
            updateInfo: normalizeUpdateInfo(result && result.updateInfo)
        };
    } catch (err) {
        const message = getUpdateErrorMessage(err);
        sendUpdateStatus('error', { error: message });
        if (manual) {
            await dialog.showMessageBox({
                type: 'warning',
                title: 'Erro ao verificar atualizacao',
                message
            });
        }
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
        notes: 'Altere appUrl para o endereco do seu servidor, por exemplo: https://seu-servidor.com/dashboard.html'
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
    if (!envAppUrl && (isPlaceholderAppUrl(appUrl) || isLegacyLocalAppUrl(appUrl) || isLegacyTunnelAppUrl(appUrl))) {
        appUrl = JK_DEFAULT_APP_URL;
        writeJsonFile(paths.userConfig, {
            appUrl,
            notes: 'Endereco do servidor Cloud Run do JK Sistema.'
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
        notes: 'Endereco do servidor do JK Sistema Cliente.'
    };
    if (!writeJsonFile(paths.userConfig, payload)) {
        throw new Error('Nao foi possivel salvar a configuracao do servidor.');
    }
    return { ...payload, configPath: paths.userConfig };
}

function loadConfiguredApp(win, clientConfig = null) {
    const config = clientConfig || loadClientConfig();
    logElectronLifecycle('client-config-loaded', { appUrl: config.appUrl, configPath: config.configPath });
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
    clearStartupIncomplete();
    flushPersistentSessions().catch(() => {});
});
