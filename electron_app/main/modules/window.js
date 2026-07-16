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
    const chromeUserData = typeof getChromeUserDataDir === 'function'
        ? getChromeUserDataDir()
        : (process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, 'Google', 'Chrome', 'User Data') : '');
    if (!chromeUserData || !fs.existsSync(chromeUserData)) return [];

    const candidates = [];
    for (const profile of fs.readdirSync(chromeUserData, { withFileTypes: true })) {
        if (!profile.isDirectory()) continue;
        if (profile.name !== 'Default' && !/^Profile\s+\d+$/i.test(profile.name)) continue;
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
        const preferredScore = configuredPreferredProfile
            ? (profile.name === configuredPreferredProfile || profileDisplayName === configuredPreferredProfile ? 200 : 0)
            : (/jk\s*pecas|jk\s*pe[cç]as/i.test(profileDisplayName) || /jk\s*pecas/.test(normalizedDisplay) ? 100 : 0);
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
                profileDisplayName,
                version: versionEntry.name.replace(/_\d+$/i, ''),
                mtimeMs: stat ? stat.mtimeMs : 0,
                storageMtimeMs: typeof getChromeExtensionStorageMtimeMs === 'function'
                    ? getChromeExtensionStorageMtimeMs(chromeUserData, profile.name, extensionId)
                    : 0,
                preferredScore
            });
        }
    }

    return candidates
        .sort((left, right) => {
            const scoreDiff = (right.preferredScore || 0) - (left.preferredScore || 0);
            if (scoreDiff) return scoreDiff;
            const storageDiff = (right.storageMtimeMs || 0) - (left.storageMtimeMs || 0);
            if (storageDiff) return storageDiff;
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
    const bundledRootKeys = new Set(bundledRoots.map(root => path.resolve(root).toLowerCase()));
    const avantProEnabled = isAvantProExtensionEnabled();
    if (!avantProEnabled) {
        return [];
    }
    const chromeInstalledExtensions = avantProEnabled ? findInstalledChromeExtensionVersions(AVANTPRO_CHROME_EXTENSION_ID) : [];
    const preferInstalled = !/^(0|false|nao|não|off|no)$/i.test(String(process.env.JK_PREFER_INSTALLED_CHROME_EXTENSIONS || '1'));
    const forceExplicitRoots = /^(1|true|sim|yes)$/i.test(String(process.env.JK_FORCE_CHROME_EXTENSIONS_DIR || ''));
    const allowBundledFallback = !chromeInstalledExtensions.length
        || /^(1|true|sim|yes)$/i.test(String(process.env.JK_ALLOW_BUNDLED_AVANTPRO_FALLBACK || ''));
    const fallbackRoots = allowBundledFallback ? bundledRoots : [];
    const explicitSearchRoots = explicitRoots.filter(root => {
        const key = path.resolve(root).toLowerCase();
        return forceExplicitRoots || allowBundledFallback || !bundledRootKeys.has(key);
    });
    const searchRoots = preferInstalled
        ? [
            ...(forceExplicitRoots ? explicitSearchRoots : []),
            ...chromeInstalledExtensions,
            ...(forceExplicitRoots ? [] : explicitSearchRoots),
            ...fallbackRoots
        ]
        : [
            ...explicitSearchRoots,
            ...fallbackRoots,
            ...chromeInstalledExtensions
        ];
    logElectronLifecycle('chrome-extension-search-roots', {
        preferInstalled,
        forceExplicitRoots,
        allowBundledFallback,
        installedCount: chromeInstalledExtensions.length,
        explicitRoots,
        searchRoots
    });

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

function isAvantProLoadedForMlSession(extensions = null) {
    const loaded = Array.isArray(extensions) ? extensions : getLoadedChromeExtensionsForMlSession();
    return loaded.some(ext => String(ext && ext.id || '').toLowerCase() === AVANTPRO_CHROME_EXTENSION_ID.toLowerCase());
}

function resetEmbeddedMlBrowserForExtensionReload(reason = 'extension-reloaded') {
    if (!embeddedMlBrowserView) return false;
    const previousOwner = embeddedMlBrowserOwner;
    const previousUrl = embeddedMlBrowserView.webContents && !embeddedMlBrowserView.webContents.isDestroyed()
        ? embeddedMlBrowserView.webContents.getURL()
        : '';
    if (previousOwner && !previousOwner.isDestroyed()) {
        try { previousOwner.removeBrowserView(embeddedMlBrowserView); } catch (_err) {}
    }
    try {
        if (embeddedMlBrowserView.webContents && !embeddedMlBrowserView.webContents.isDestroyed()) {
            embeddedMlBrowserView.webContents.destroy();
        }
    } catch (_err) {}
    embeddedMlBrowserView = null;
    embeddedMlBrowserOwner = null;
    logElectronLifecycle('embedded-ml-browser-reset-for-extension', {
        reason,
        previousUrl
    });
    return true;
}

async function runChromeExtensionsLoadForMlSession(reason = 'normal') {
    const hadEmbeddedBrowser = !!embeddedMlBrowserView;
    const hadAvantLoaded = isAvantProLoadedForMlSession();
    let snapshotPreload = null;
    if (!hadAvantLoaded && typeof ensureAvantProExtensionStorageFromSnapshot === 'function') {
        snapshotPreload = await ensureAvantProExtensionStorageFromSnapshot('before-extension-load', {
            reason
        }, {
            reloadExtensions: false
        }).catch((err) => {
            logElectronLifecycle('avantpro-storage-snapshot-preload-error', {
                reason,
                error: err && err.message ? err.message : String(err)
            });
            return null;
        });
        if (snapshotPreload && !snapshotPreload.skipped) {
            logElectronLifecycle('avantpro-storage-snapshot-preload-result', {
                reason,
                success: !!snapshotPreload.success,
                copied: snapshotPreload.copied || 0,
                snapshotDir: snapshotPreload.snapshotDir || '',
                targetDir: snapshotPreload.targetDir || '',
                restoreReason: snapshotPreload.reason || ''
            });
        }
    }
    const permitirPreloadStorageAvant = /^(1|true|sim|yes|on)$/i.test(String(process.env.JK_AVANTPRO_PRELOAD_STORAGE_IMPORT || ''));
    const snapshotAtualOuRestaurado = !!(snapshotPreload && (snapshotPreload.success || snapshotPreload.reason === 'target-current'));
    if (!hadAvantLoaded && !snapshotAtualOuRestaurado && permitirPreloadStorageAvant && typeof importAvantProExtensionStorageFromChrome === 'function') {
        const preloaded = await importAvantProExtensionStorageFromChrome('before-extension-load', {
            reason
        }, {
            reloadExtensions: false
        }).catch((err) => {
            logElectronLifecycle('avantpro-storage-preload-error', {
                reason,
                error: err && err.message ? err.message : String(err)
            });
            return null;
        });
        if (preloaded && !preloaded.skipped) {
            logElectronLifecycle('avantpro-storage-preload-result', {
                reason,
                success: !!preloaded.success,
                copied: preloaded.copied || 0,
                sourceProfile: preloaded.source && preloaded.source.profile,
                resetFailed: !!preloaded.resetFailed,
                missing: !!preloaded.missing,
                empty: !!preloaded.empty
            });
        }
    } else if (!hadAvantLoaded && !snapshotAtualOuRestaurado && !permitirPreloadStorageAvant) {
        logElectronLifecycle('avantpro-storage-preload-skipped', {
            reason,
            skipped: true,
            mode: 'disabled-by-default'
        });
    }
    const loaded = await loadChromeExtensionsForMlSession();
    const current = getLoadedChromeExtensionsForMlSession();
    const hasAvantLoaded = isAvantProLoadedForMlSession(current);
    if (hasAvantLoaded && typeof saveAvantProExtensionStorageSnapshot === 'function') {
        saveAvantProExtensionStorageSnapshot('after-extension-load-current-auth', {
            reason
        })
            .then((snapshot) => {
                if (snapshot && snapshot.success) {
                    logElectronLifecycle('avantpro-storage-snapshot-autosave-result', {
                        reason,
                        copied: snapshot.copied || 0,
                        snapshotDir: snapshot.snapshotDir || ''
                    });
                }
            })
            .catch((err) => {
                logElectronLifecycle('avantpro-storage-snapshot-autosave-error', {
                    reason,
                    error: err && err.message ? err.message : String(err)
                });
            });
    }
    if (!hadAvantLoaded && hasAvantLoaded && hadEmbeddedBrowser) {
        resetEmbeddedMlBrowserForExtensionReload(reason);
    }
    return current.length ? current : loaded;
}

async function tentarRestaurarSnapshotAvantProParaWebContents(webContents, stage, url, status, options = {}) {
    if (options.recuperarStorage === false || typeof restoreAvantProExtensionStorageSnapshot !== 'function') {
        return null;
    }
    const restored = await restoreAvantProExtensionStorageSnapshot(stage, {
        url,
        status
    }, {
        force: true,
        reloadExtensions: true
    }).catch((err) => {
        logElectronLifecycle('avantpro-storage-snapshot-restore-error', {
            stage,
            url,
            error: err && err.message ? err.message : String(err)
        });
        return null;
    });
    if (!(restored && restored.success)) return restored || null;
    await recarregarWebContentsParaAvantPro(webContents);
    const restoredStatus = await aguardarAvantProWebContents(webContents, {
        timeoutMs: options.timeoutAposSnapshotMs || options.timeoutAposReloadMs || 9000,
        pollMs: options.pollMs || 300
    });
    logElectronLifecycle('avantpro-after-storage-snapshot-restore', {
        stage,
        url: isWebContentsAlive(webContents) ? webContents.getURL() : url,
        restored: {
            copied: restored.copied,
            snapshotDir: restored.snapshotDir,
            targetDir: restored.targetDir
        },
        status: restoredStatus
    });
    return {
        ...restoredStatus,
        reloaded: true,
        storageSnapshotRestore: restored
    };
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

function ensureChromeExtensionsForMlSession(options = {}) {
    const forceRetryIfEmpty = !!options.forceRetryIfEmpty;
    const skipEmptyRetry = !!options.skipEmptyRetry;
    const currentExtensions = getLoadedChromeExtensionsForMlSession();
    if (isAvantProExtensionEnabled() && isAvantProLoadedForMlSession(currentExtensions)) {
        return Promise.resolve(currentExtensions);
    }
    if (!chromeExtensionsLoadPromise || forceRetryIfEmpty) {
        chromeExtensionsLoadPromise = runChromeExtensionsLoadForMlSession(forceRetryIfEmpty ? 'forced-retry' : 'initial').catch((err) => {
            console.error('[Extensoes] Erro ao carregar extensoes Chrome:', err && err.message ? err.message : err);
            return [];
        });
    }
    return chromeExtensionsLoadPromise.then((loaded) => {
        const afterLoadExtensions = getLoadedChromeExtensionsForMlSession();
        if (!isAvantProExtensionEnabled()) {
            return afterLoadExtensions.length ? afterLoadExtensions : loaded;
        }
        if (isAvantProLoadedForMlSession(afterLoadExtensions)) {
            return afterLoadExtensions;
        }
        if (!skipEmptyRetry) {
            logElectronLifecycle('chrome-extension-empty-cache-retry', {
                loadedCount: Array.isArray(loaded) ? loaded.length : 0,
                currentCount: afterLoadExtensions.length
            });
            chromeExtensionsLoadPromise = null;
            return ensureChromeExtensionsForMlSession({
                forceRetryIfEmpty: true,
                skipEmptyRetry: true
            });
        }
        return afterLoadExtensions.length ? afterLoadExtensions : loaded;
    });
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
        await ensureChromeExtensionsForMlSession({ forceRetryIfEmpty: true });
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

function isMercadoLivreExplicitUrl(targetUrl) {
    const value = String(targetUrl || '').trim();
    if (!value || /^about:blank$/i.test(value)) return false;
    try {
        const url = new URL(/^https?:\/\//i.test(value) ? value : `https://${value}`);
        return isMercadoLivreHost(url.hostname);
    } catch (_err) {
        return false;
    }
}

function isGoogleAccountAuthUrl(targetUrl) {
    try {
        const url = new URL(normalizeTargetUrl(targetUrl));
        const host = String(url.hostname || '').toLowerCase();
        return host === 'accounts.google.com' || host.endsWith('.accounts.google.com');
    } catch (_err) {
        return false;
    }
}

function isMlBrowserWebContents(contents) {
    return !!(
        contents &&
        embeddedMlBrowserView &&
        embeddedMlBrowserView.webContents &&
        !embeddedMlBrowserView.webContents.isDestroyed() &&
        contents === embeddedMlBrowserView.webContents
    );
}

function registerEmbeddedMlBrowserDownloadGuard(contents) {
    const ses = contents && contents.session;
    if (!ses || ses.__jkEmbeddedMlBrowserDownloadGuardRegistered) return;
    ses.__jkEmbeddedMlBrowserDownloadGuardRegistered = true;
    ses.on('will-download', (event, item, downloadWebContents) => {
        if (!isMlBrowserWebContents(downloadWebContents)) return;
        const url = item && typeof item.getURL === 'function' ? item.getURL() : '';
        const filename = item && typeof item.getFilename === 'function' ? item.getFilename() : '';
        event.preventDefault();
        try {
            if (item && typeof item.cancel === 'function') item.cancel();
        } catch (_err) {}
        logElectronLifecycle('embedded-ml-browser-download-blocked', {
            url,
            filename,
            pageUrl: downloadWebContents && !downloadWebContents.isDestroyed()
                ? downloadWebContents.getURL()
                : ''
        });
    });
}

function shouldUseMlSessionForPopup(contents, targetUrl) {
    const openerUrl = contents && typeof contents.getURL === 'function' ? String(contents.getURL() || '') : '';
    if (isMercadoLivreExplicitUrl(openerUrl)) return true;
    if (isMercadoLivreExplicitUrl(targetUrl)) return true;
    return isGoogleAccountAuthUrl(targetUrl) && (isMercadoLivreExplicitUrl(openerUrl) || isMlBrowserWebContents(contents));
}

function mlPopupParentFor(contents) {
    const opener = contents ? BrowserWindow.fromWebContents(contents) : null;
    if (opener && !opener.isDestroyed()) return opener;
    if (isMlBrowserWebContents(contents) && embeddedMlBrowserOwner && !embeddedMlBrowserOwner.isDestroyed()) {
        return embeddedMlBrowserOwner;
    }
    return mainWindow && !mainWindow.isDestroyed() ? mainWindow : null;
}

function mlPopupWindowOptions(contents = null) {
    const parent = mlPopupParentFor(contents);
    const options = {
        width: 520,
        height: 680,
        show: true,
        autoHideMenuBar: true,
        webPreferences: {
            contextIsolation: true,
            nodeIntegration: false,
            nativeWindowOpen: true,
            backgroundThrottling: false,
            session: getMlSession(),
            userAgent: ML_BROWSER_USER_AGENT
        }
    };
    if (parent) options.parent = parent;
    return options;
}

function configureMlPopupWindow(win, details = {}) {
    if (!win || win.isDestroyed()) return;
    try { win.setMenuBarVisibility(false); } catch (_err) {}
    try {
        if (win.webContents && !win.webContents.isDestroyed()) {
            win.webContents.__jkAllowMlAdNavigation = true;
            win.webContents.setUserAgent(ML_BROWSER_USER_AGENT);
            registerAvantProConsoleDiagnostics(win.webContents);
            win.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
                logElectronLifecycle('ml-login-popup-fail-load', { errorCode, errorDescription, validatedURL });
            });
        }
    } catch (err) {
        logElectronLifecycle('ml-login-popup-config-warning', {
            url: details && details.url,
            error: err && err.message ? err.message : String(err)
        });
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

function parseMlMoneyValue(value) {
    if (value && typeof value === 'object') {
        const nested = [value.amount, value.price, value.value, value.current_price];
        for (const candidate of nested) {
            const parsed = parseMlMoneyValue(candidate);
            if (parsed !== null) return parsed;
        }
        return null;
    }
    if (typeof value === 'number') {
        return Number.isFinite(value) && value > 0 ? value : null;
    }
    const raw = String(value === null || value === undefined ? '' : value).replace(/\s+/g, ' ').trim();
    if (!raw) return null;
    const aria = raw.match(/(\d[\d.]*)\s*reais?(?:\s*(?:e|,)?\s*(\d{1,2})\s*centavos?)?/i);
    if (aria && aria[1]) {
        const reais = Number(String(aria[1]).replace(/\./g, ''));
        const centavos = aria[2] ? Number(aria[2]) : 0;
        const total = reais + (Number.isFinite(centavos) ? centavos / 100 : 0);
        return Number.isFinite(total) && total > 0 ? total : null;
    }
    const match = raw.replace(/R\$\s*/gi, '').replace(/\s+/g, '').match(/\d[\d.,]*/);
    if (!match) return null;
    let normalized = match[0];
    if (normalized.includes('.') && normalized.includes(',')) {
        normalized = normalized.lastIndexOf('.') > normalized.lastIndexOf(',')
            ? normalized.replace(/,/g, '')
            : normalized.replace(/\./g, '').replace(/,/g, '.');
    } else if (normalized.includes(',')) {
        normalized = normalized.replace(/\./g, '').replace(/,/g, '.');
    } else if (/^\d{1,3}(?:\.\d{3})+$/.test(normalized)) {
        normalized = normalized.replace(/\./g, '');
    }
    const parsed = Number(normalized);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function normalizeMlBrowserPriceInfo(source = {}) {
    const info = source && typeof source === 'object' ? source : {};
    const salePrice = info.sale_price && typeof info.sale_price === 'object' ? info.sale_price : {};
    const firstPositive = (values) => {
        for (const value of values) {
            const parsed = parseMlMoneyValue(value);
            if (parsed !== null) return parsed;
        }
        return null;
    };
    const current = firstPositive([
        info.preco_promocional,
        info.promotional_price,
        salePrice.amount,
        info.preco,
        info.price,
        info.amount
    ]);
    const regular = firstPositive([
        info.preco_original,
        info.original_price,
        salePrice.regular_amount,
        info.standard_price,
        info.base_price
    ]);
    const promotional = current !== null && regular !== null && regular > current + 0.005;
    return {
        preco: current,
        price: current,
        preco_original: promotional ? regular : '',
        original_price: promotional ? regular : '',
        standard_price: regular || current || '',
        preco_promocional: promotional ? current : '',
        promotional_price: promotional ? current : '',
        discount_pct: promotional ? ((regular - current) / regular) * 100 : ''
    };
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
    const info = {
        data_criacao: '',
        vendedor: '',
        seller_id: null,
        id: cleanId,
        vendas: null,
        preco: null,
        price: null,
        preco_original: '',
        original_price: '',
        preco_promocional: '',
        promotional_price: '',
        discount_pct: ''
    };

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
        if (sameItem) {
            const prices = normalizeMlBrowserPriceInfo(cur);
            if (prices.preco !== null) Object.assign(info, prices);
        }
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

        if (sameItem && (info.data_criacao || info.vendedor || info.seller_id || info.vendas !== null || info.preco !== null)) {
            return info;
        }
    }

    return (info.data_criacao || info.vendedor || info.seller_id || info.vendas !== null || info.preco !== null) ? info : null;
}

function findMlItemInfoInText(text, itemId) {
    const normalized = normalizeMlText(text);
    try {
        const parsed = JSON.parse(normalized);
        const structuredInfo = findMlItemInfoInObject(parsed, itemId);
        if (structuredInfo) return structuredInfo;
    } catch (_err) {}
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

    return null;
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
                    var parseMoney = function (value) {
                        var raw = String(value === null || value === undefined ? '' : value).replace(/\\s+/g, ' ').trim();
                        if (!raw) return null;
                        var aria = raw.match(/(\\d[\\d.]*)\\s*reais?(?:\\s*(?:e|,)?\\s*(\\d{1,2})\\s*centavos?)?/i);
                        if (aria && aria[1]) {
                            var reais = Number(String(aria[1]).replace(/\\./g, ''));
                            var centavos = aria[2] ? Number(aria[2]) : 0;
                            var total = reais + (Number.isFinite(centavos) ? centavos / 100 : 0);
                            return Number.isFinite(total) && total > 0 ? total : null;
                        }
                        var match = raw.replace(/R\\$\\s*/gi, '').replace(/\\s+/g, '').match(/\\d[\\d.,]*/);
                        if (!match) return null;
                        var normalized = match[0];
                        if (normalized.indexOf('.') >= 0 && normalized.indexOf(',') >= 0) {
                            normalized = normalized.lastIndexOf('.') > normalized.lastIndexOf(',')
                                ? normalized.replace(/,/g, '')
                                : normalized.replace(/\\./g, '').replace(/,/g, '.');
                        } else if (normalized.indexOf(',') >= 0) {
                            normalized = normalized.replace(/\\./g, '').replace(/,/g, '.');
                        } else if (/^\\d{1,3}(?:\\.\\d{3})+$/.test(normalized)) {
                            normalized = normalized.replace(/\\./g, '');
                        }
                        var parsed = Number(normalized);
                        return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
                    };
                    var readMoney = function (selectors) {
                        for (var selectorIndex = 0; selectorIndex < selectors.length; selectorIndex += 1) {
                            var nodes = Array.prototype.slice.call(document.querySelectorAll(selectors[selectorIndex]));
                            for (var nodeIndex = 0; nodeIndex < nodes.length; nodeIndex += 1) {
                                var node = nodes[nodeIndex];
                                var value = parseMoney(
                                    (node.getAttribute && (
                                        node.getAttribute('aria-label')
                                        || node.getAttribute('content')
                                        || node.getAttribute('value')
                                    ))
                                    || node.textContent
                                    || ''
                                );
                                if (value !== null) return value;
                            }
                        }
                        return null;
                    };
                    var currentPrice = readMoney([
                        '.ui-pdp-price__second-line .andes-money-amount',
                        '[data-testid="price-part"] .andes-money-amount',
                        '[data-testid="price-part"]',
                        '.ui-pdp-price .andes-money-amount:not(.andes-money-amount--previous)',
                        'meta[itemprop="price"]',
                        'meta[property="product:price:amount"]'
                    ]);
                    var originalPrice = readMoney([
                        '.ui-pdp-price__original-value .andes-money-amount',
                        's.ui-pdp-price__original-value .andes-money-amount',
                        '.andes-money-amount--previous',
                        '.ui-pdp-price s .andes-money-amount',
                        's .andes-money-amount'
                    ]);
                    if (originalPrice !== null && (currentPrice === null || originalPrice <= currentPrice)) {
                        originalPrice = null;
                    }
                    return {
                        html: document.documentElement && document.documentElement.outerHTML ? document.documentElement.outerHTML : '',
                        title: document.title || '',
                        url: location.href,
                        preco: currentPrice,
                        price: currentPrice,
                        preco_original: originalPrice,
                        original_price: originalPrice,
                        preco_promocional: originalPrice !== null && currentPrice !== null ? currentPrice : '',
                        promotional_price: originalPrice !== null && currentPrice !== null ? currentPrice : '',
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
        const pagePrices = normalizeMlBrowserPriceInfo(pageInfo || {});
        if (pagePrices.preco !== null) {
            Object.assign(htmlInfo, pagePrices);
        }
        if (pageInfo && pageInfo.sellerText && !htmlInfo.vendedor) {
            htmlInfo.vendedor = normalizeMlText(pageInfo.sellerText).replace(/^(vendido por|loja oficial)\s+/i, '').trim();
        }
        if (htmlInfo.data_criacao || htmlInfo.vendedor || htmlInfo.seller_id || pagePrices.preco !== null) {
            htmlInfo.source = pagePrices.preco !== null ? 'browser_page' : (htmlInfo.source || 'page_html');
            found.push(htmlInfo);
        }

        const best = found.find(item => item && item.data_criacao)
            || found.find(item => item && item.vendedor)
            || found.find(item => item && item.vendas !== null && item.vendas !== undefined)
            || found.find(Boolean);
        const bestPrice = found.find(item => {
            const prices = normalizeMlBrowserPriceInfo(item || {});
            return prices.preco !== null && prices.preco_original;
        }) || found.find(item => normalizeMlBrowserPriceInfo(item || {}).preco !== null);
        const prices = normalizeMlBrowserPriceInfo(bestPrice || {});
        return {
            id: cleanId,
            url: pageInfo && pageInfo.url ? pageInfo.url : url,
            data_criacao: best && best.data_criacao ? best.data_criacao : '',
            vendedor: best && best.vendedor ? best.vendedor : '',
            seller_id: best && best.seller_id ? best.seller_id : null,
            vendas: best && best.vendas !== null && best.vendas !== undefined ? best.vendas : null,
            preco: prices.preco,
            price: prices.price,
            preco_original: prices.preco_original,
            original_price: prices.original_price,
            standard_price: prices.standard_price,
            preco_promocional: prices.preco_promocional,
            promotional_price: prices.promotional_price,
            discount_pct: prices.discount_pct,
            source: bestPrice && bestPrice.source ? bestPrice.source : (best && best.source ? best.source : ''),
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

        const onLoad = () => {
            if (expectedComparable) {
                const currentComparable = normalizeComparableUrl(webContents.getURL());
                if (currentComparable && currentComparable !== expectedComparable) {
                    logElectronLifecycle('webcontents-load-finish-ignored-other-url', {
                        expectedUrl,
                        currentUrl: webContents.getURL()
                    });
                    return;
                }
            }
            finish();
        };
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

function normalizeComparableUrl(value) {
    try {
        const url = new URL(String(value || ''));
        url.hash = '';
        if (isMercadoLivreHost(url.hostname)) {
            url.searchParams.delete('loader');
            url.searchParams.delete('noIndex');
            url.pathname = url.pathname.replace(/\/+$/, '').toLowerCase();
            url.hostname = url.hostname.toLowerCase();
        }
        return url.toString();
    } catch (_err) {
        return String(value || '')
            .replace(/#.*$/, '')
            .replace(/[?&]loader=true\b/i, '')
            .replace(/[?&]noIndex=true\b/i, '')
            .replace(/\/+$/, '')
            .toLowerCase();
    }
}

function isIgnorableNavigationAbort(errorCode, message = '') {
    return Number(errorCode) === -3 || /\bERR_ABORTED\b|\(-3\)|loading 'https?:\/\//i.test(String(message || ''));
}

function waitForWebContentsLoad(webContents, timeoutMs = 25000, expectedUrl = '') {
    return new Promise((resolve, reject) => {
        if (!isWebContentsAlive(webContents)) {
            reject(new Error('Navegador interno indisponivel.'));
            return;
        }

        let done = false;
        let timeout = null;
        const expectedComparable = normalizeComparableUrl(expectedUrl);
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
        const onFail = (_event, errorCode, errorDescription, validatedURL, isMainFrame) => {
            if (isMainFrame === false) return;
            const failedComparable = normalizeComparableUrl(validatedURL);
            if (isIgnorableNavigationAbort(errorCode, errorDescription)) {
                logElectronLifecycle('webcontents-load-aborted-ignored', {
                    expectedUrl,
                    validatedURL,
                    currentUrl: isWebContentsAlive(webContents) ? webContents.getURL() : '',
                    errorCode,
                    errorDescription
                });
                return;
            }
            if (expectedComparable && failedComparable && failedComparable !== expectedComparable) {
                logElectronLifecycle('webcontents-load-fail-ignored-other-url', {
                    expectedUrl,
                    validatedURL,
                    currentUrl: isWebContentsAlive(webContents) ? webContents.getURL() : '',
                    errorCode,
                    errorDescription
                });
                return;
            }
            finish(new Error(`Falha ao carregar pagina (${errorCode}): ${errorDescription}`));
        };

        timeout = setTimeout(() => {
            finish(new Error('Timeout ao carregar pagina no navegador interno.'));
        }, timeoutMs);

        webContents.on('did-finish-load', onLoad);
        webContents.on('did-fail-load', onFail);
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
            var normalizarBusca = function (value) {
                var text = normalizar(value);
                try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                return text.toLowerCase();
            };
            var contemAvant = function (value) {
                return /avant\\s*pro|avantpro|carregar\\s+dados\\s+avant|informacoes?\\s+avant|ferramentas|vincular\\s+conta|conectar\\s+conta|rotulos\\s+visuais|atualizar\\s+dados|extrair\\s+dados/i.test(normalizarBusca(value));
            };
            var ehControleDadosAvant = function (value) {
                return /carregar\\s+dados\\s+avant|informacoes?\\s+avant|informacoes?\\s+avantpro|rotulos\\s+visuais|atualizar\\s+dados\\s+avant|extrair\\s+dados\\s+avant/i.test(normalizarBusca(value));
            };
            var contemDadosAnuncioAvant = function (value) {
                return /vendas\\s+do\\s+(?:produto|anuncio|item)|faturamento\\s+do\\s+produto|nome\\s+do\\s+vendedor|anuncio\\s+(?:ganhador\\s+)?criado\\s+em|reputacao\\s+do\\s+vendedor/i.test(normalizarBusca(value));
            };
            var contarRotulosAvantNoTexto = function (value) {
                var busca = normalizarBusca(value);
                var padroes = [
                    /vendas\\s+do\\s+produto/,
                    /faturamento\\s+do\\s+produto/,
                    /nome\\s+do\\s+vendedor/,
                    /localizacao\\s+do\\s+vendedor/,
                    /\\bmarca\\b/,
                    /participacao\\b/,
                    /visitas\\s+do\\s+anuncio/,
                    /comissao\\b/,
                    /reputacao\\s+do\\s+vendedor/,
                    /anuncio\\s+(?:ganhador\\s+)?criado\\s+em/
                ];
                return padroes.reduce(function (total, regex) {
                    return total + (regex.test(busca) ? 1 : 0);
                }, 0);
            };
            var bodyBusca = normalizarBusca(document.body && document.body.innerText || '');
            var bodyHasAvantInfo = /informacoes?\\s+avant(?:\\s*pro|pro)?/.test(bodyBusca);
            var bodyDataLabels = contarRotulosAvantNoTexto(bodyBusca);
            var rows = document.querySelectorAll('.avantpro-product-info-row').length;
            var widgets = document.querySelectorAll('[class*="avantpro"], [id*="avantpro"], [data-testid*="avantpro"]').length;
            var cardCount = document.querySelectorAll('li.ui-search-layout__item, div.ui-search-result__wrapper, div.ui-search-result, div.poly-card, section.poly-card, [class*="poly-card"], [class*="ui-search-result"], [class*="ui-search-layout__item"]').length;
            var actionButtons = 0;
            var infoButtons = 0;
            var accountLinkButtons = 0;
            var toolsButtons = 0;
            Array.prototype.slice.call(document.querySelectorAll('button, a, [role="button"]')).forEach(function (node) {
                var text = [
                    node.innerText,
                    node.textContent,
                    node.getAttribute && node.getAttribute('aria-label'),
                    node.getAttribute && node.getAttribute('title')
                ].map(normalizar).join(' ');
                if (contemAvant(text)) actionButtons += 1;
                if (ehControleDadosAvant(text)) infoButtons += 1;
                var busca = normalizarBusca(text);
                if (/vincular\\s+conta|conectar\\s+conta|fazer\\s+login|entrar\\s+no\\s+avant/.test(busca)) accountLinkButtons += 1;
                if (/ferramentas|rotulos\\s+visuais|avant\\s*pro|avantpro/.test(busca)) toolsButtons += 1;
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
            var dataTextNodes = 0;
            Array.prototype.slice.call(document.querySelectorAll('[class*="avant"], [id*="avant"], .created-time-card, .avantpro-product-info-row, .avantpro-product-info-row *')).forEach(function (node) {
                var text = normalizar(node.innerText || node.textContent || '');
                if (text && contemDadosAnuncioAvant(text)) dataTextNodes += 1;
            });
            var extensionResources = 0;
            try {
                extensionResources = performance.getEntriesByType('resource').filter(function (entry) {
                    var name = String(entry && entry.name || '').toLowerCase();
                    return name.indexOf('chrome-extension://' + AVANT_ID) >= 0 || name.indexOf('avantpro') >= 0;
                }).length;
            } catch (_err) {}
            var ok = rows > 0 || infoButtons > 0 || dataTextNodes > 0 || (bodyHasAvantInfo && bodyDataLabels > 0) || bodyDataLabels >= 3;
            var extensionDetected = widgets > 0 || actionButtons > 0 || taggedNodes > 0 || extensionResources > 0 || bodyHasAvantInfo || bodyDataLabels > 0;
            var shellAvantOperavel = cardCount > 0 && extensionDetected && (widgets > 0 || actionButtons > 0 || toolsButtons > 0 || infoButtons > 0);
            var paginaLoginReal = /\\/gz\\/account-verification|\\/jms\\/mlb\\/lgz\\/login|auth\\.avantprocloud\\.com\\.br/i.test(String(location.href || ''))
                || /digite seu e-?mail|iniciar sessao|insira suas credenciais|seu e-?mail/.test(bodyBusca);
            var needsAccountLink = !ok && (paginaLoginReal || (accountLinkButtons > 0 && !shellAvantOperavel));
            return {
                ok: !!ok,
                rows: rows,
                widgets: widgets,
                cardCount: cardCount,
                actionButtons: actionButtons,
                infoButtons: infoButtons,
                accountLinkButtons: accountLinkButtons,
                toolsButtons: toolsButtons,
                needsAccountLink: !!needsAccountLink,
                shellOnly: !ok && extensionDetected,
                extensionDetected: !!extensionDetected,
                dataTextNodes: dataTextNodes,
                bodyHasAvantInfo: !!bodyHasAvantInfo,
                bodyDataLabels: bodyDataLabels,
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
    const expectedUrl = webContents.getURL();
    const loadPromise = waitForWebContentsLoad(webContents, 25000, expectedUrl).catch((err) => err);
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
    const podeRecuperarStorage = options.recuperarStorage !== false;
    const firstStatus = await aguardarAvantProWebContents(webContents, {
        timeoutMs: options.timeoutMs || 3500,
        pollMs: options.pollMs || 300
    });
    if (firstStatus && firstStatus.ok) return firstStatus;
    if (firstStatus && firstStatus.needsAccountLink) {
        logElectronLifecycle('avantpro-account-action-required', {
            url,
            status: firstStatus
        });
        const restoredStatus = await tentarRestaurarSnapshotAvantProParaWebContents(
            webContents,
            'avantpro-account-action-required',
            url,
            firstStatus,
            options
        );
        if (restoredStatus && restoredStatus.storageSnapshotRestore) {
            if (restoredStatus.ok || !restoredStatus.needsAccountLink) {
                return restoredStatus;
            }
        }
        if (podeRecuperarStorage && typeof importAvantProExtensionStorageFromChrome === 'function') {
            const imported = await importAvantProExtensionStorageFromChrome('avantpro-account-action-required', {
                url,
                status: firstStatus
            }).catch((err) => {
                logElectronLifecycle('avantpro-storage-import-error', {
                    url,
                    error: err && err.message ? err.message : String(err)
                });
                return null;
            });
            if (imported && imported.success) {
                await recarregarWebContentsParaAvantPro(webContents);
                const importedStatus = await aguardarAvantProWebContents(webContents, {
                    timeoutMs: options.timeoutAposReloadMs || 9000,
                    pollMs: options.pollMs || 300
                });
                logElectronLifecycle('avantpro-after-storage-import', {
                    url: isWebContentsAlive(webContents) ? webContents.getURL() : url,
                    imported: {
                        copied: imported.copied,
                        sourceProfile: imported.source && imported.source.profile
                    },
                    status: importedStatus
                });
                if (importedStatus && importedStatus.ok) {
                    return importedStatus;
                }
                if (importedStatus && !importedStatus.needsAccountLink) {
                    return importedStatus;
                }
            }
        }
        return {
            ...firstStatus,
            ok: false,
            accountActionRequired: true,
            message: 'Avant Pro carregou, mas esta pedindo login ou vinculacao da conta.'
        };
    }

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
    if (secondStatus && secondStatus.needsAccountLink && podeRecuperarStorage && typeof importAvantProExtensionStorageFromChrome === 'function') {
        const restoredStatus = await tentarRestaurarSnapshotAvantProParaWebContents(
            webContents,
            'avantpro-account-action-required-after-reload',
            url,
            secondStatus,
            options
        );
        if (restoredStatus && restoredStatus.storageSnapshotRestore) {
            if (restoredStatus.ok || !restoredStatus.needsAccountLink) {
                return restoredStatus;
            }
        }
        const imported = await importAvantProExtensionStorageFromChrome('avantpro-account-action-required-after-reload', {
            url,
            status: secondStatus
        }).catch((err) => {
            logElectronLifecycle('avantpro-storage-import-error', {
                url,
                error: err && err.message ? err.message : String(err)
            });
            return null;
        });
        if (imported && imported.success) {
            await recarregarWebContentsParaAvantPro(webContents);
            const importedStatus = await aguardarAvantProWebContents(webContents, {
                timeoutMs: options.timeoutAposReloadMs || 9000,
                pollMs: options.pollMs || 300
            });
            logElectronLifecycle('avantpro-after-storage-import-reload', {
                url: isWebContentsAlive(webContents) ? webContents.getURL() : url,
                imported: {
                    copied: imported.copied,
                    sourceProfile: imported.source && imported.source.profile
                },
                status: importedStatus
            });
            return { ...importedStatus, reloaded: true, storageImport: imported };
        }
    }
    const extensionDetected = !!(secondStatus && (
        secondStatus.extensionDetected
        || secondStatus.shellOnly
        || secondStatus.extensionResources > 0
        || secondStatus.widgets > 0
        || secondStatus.actionButtons > 0
        || secondStatus.taggedNodes > 0
    ));
    if (secondStatus && !secondStatus.ok && !extensionDetected && options.recuperarStorage !== false) {
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
        if (isIgnorableNavigationAbort(null, err && err.message ? err.message : String(err))) {
            logElectronLifecycle('local-backend-startup-screen-aborted-ignored', {
                url: shellUrl,
                warning: err && err.message ? err.message : String(err)
            });
            return;
        }
        console.error('Falha ao carregar shell de abas do JK Sistema:', err);
        win.loadURL(appUrl).catch((fallbackErr) => {
            if (isIgnorableNavigationAbort(null, fallbackErr && fallbackErr.message ? fallbackErr.message : String(fallbackErr))) {
                logElectronLifecycle('local-backend-startup-app-aborted-ignored', {
                    url: appUrl,
                    warning: fallbackErr && fallbackErr.message ? fallbackErr.message : String(fallbackErr)
                });
                return;
            }
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
            nativeWindowOpen: true,
            userAgent: ML_BROWSER_USER_AGENT,
            session: getMlSession()
        }
    });
    internalBrowserWindow.webContents.__jkAllowMlAdNavigation = true;
    internalBrowserWindow.webContents.setUserAgent(ML_BROWSER_USER_AGENT);
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
            nativeWindowOpen: true,
            userAgent: ML_BROWSER_USER_AGENT,
            session: getMlSession()
        }
    });
    detachedWindow.webContents.__jkAllowMlAdNavigation = true;
    detachedWindow.webContents.setUserAgent(ML_BROWSER_USER_AGENT);
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
    if (raw.background) {
        return {
            x: -20000,
            y: -20000,
            width: Math.max(1024, Math.round(Number(raw.width ?? 1280) || 1280)),
            height: Math.max(720, Math.round(Number(raw.height ?? 900) || 900))
        };
    }
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
                backgroundThrottling: false,
                nativeWindowOpen: true,
                userAgent: ML_BROWSER_USER_AGENT,
                session: getMlSession()
            }
        });
        embeddedMlBrowserView.webContents.__jkAllowMlAdNavigation = true;
        registerEmbeddedMlBrowserDownloadGuard(embeddedMlBrowserView.webContents);
        registerAvantProConsoleDiagnostics(embeddedMlBrowserView.webContents);
        embeddedMlBrowserView.webContents.setUserAgent(ML_BROWSER_USER_AGENT);
        embeddedMlBrowserView.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
            logElectronLifecycle('embedded-ml-browser-fail-load', { errorCode, errorDescription, validatedURL });
        });
    }
    embeddedMlBrowserView.webContents.__jkAllowMlAdNavigation = true;
    registerEmbeddedMlBrowserDownloadGuard(embeddedMlBrowserView.webContents);
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

function destroyEmbeddedMlBrowser(reason = 'hide') {
    const previousUrl = embeddedMlBrowserView && embeddedMlBrowserView.webContents && !embeddedMlBrowserView.webContents.isDestroyed()
        ? embeddedMlBrowserView.webContents.getURL()
        : '';
    if (embeddedMlBrowserView && embeddedMlBrowserOwner && !embeddedMlBrowserOwner.isDestroyed()) {
        try { embeddedMlBrowserOwner.removeBrowserView(embeddedMlBrowserView); } catch (_err) {}
    }
    try {
        if (embeddedMlBrowserView && embeddedMlBrowserView.webContents && !embeddedMlBrowserView.webContents.isDestroyed()) {
            try { embeddedMlBrowserView.webContents.stop(); } catch (_err) {}
            embeddedMlBrowserView.webContents.destroy();
        }
    } catch (_err) {}
    embeddedMlBrowserView = null;
    embeddedMlBrowserOwner = null;
    logElectronLifecycle('embedded-ml-browser-destroyed', { reason, previousUrl });
}

function hideEmbeddedMlBrowser(options = {}) {
    if (embeddedMlBrowserView && embeddedMlBrowserOwner && !embeddedMlBrowserOwner.isDestroyed()) {
        try { embeddedMlBrowserOwner.removeBrowserView(embeddedMlBrowserView); } catch (_err) {}
    }
    embeddedMlBrowserOwner = null;
    if (options && (options.destroy || options.unload)) {
        destroyEmbeddedMlBrowser(options.reason || 'hide');
    }
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
        request.setHeader('User-Agent', ML_BROWSER_USER_AGENT);
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
