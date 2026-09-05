const privateCredentialBootstrapCore = require(path.join(
    __dirname,
    'electron_app',
    'main',
    'modules',
    'private-credential-bootstrap-core.js'
));

const JK_PRIVATE_CREDENTIAL_BUNDLE_FILE = 'credentials.bundle.json';
const JK_PRIVATE_CREDENTIAL_CACHE_FILE = 'credentials.safe';
const JK_PRIVATE_CREDENTIAL_MAX_BUNDLE_BYTES = 6 * 1024 * 1024;

function privateCredentialBundlePath() {
    const override = String(process.env.JK_PRIVATE_CREDENTIAL_BUNDLE_PATH || '').trim();
    if (override) return path.resolve(override);
    if (!app.isPackaged || !process.resourcesPath) return '';
    return path.join(process.resourcesPath, 'private_bootstrap', JK_PRIVATE_CREDENTIAL_BUNDLE_FILE);
}

function privateCredentialCachePath() {
    return path.join(JK_ELECTRON_USER_DATA_DIR, 'private_bootstrap', JK_PRIVATE_CREDENTIAL_CACHE_FILE);
}

function privateCredentialPreloadPath() {
    const candidates = [
        path.join(__dirname, 'private_bootstrap_preload.js'),
        process.resourcesPath ? path.join(process.resourcesPath, 'local_app', 'private_bootstrap_preload.js') : ''
    ].filter(Boolean);
    const selected = candidates.find(candidate => {
        try { return fs.statSync(candidate).isFile(); } catch (_err) { return false; }
    });
    if (!selected) {
        const error = new Error('Preload seguro do cofre privado nao encontrado.');
        error.code = 'PRIVATE_BUNDLE_PRELOAD_MISSING';
        throw error;
    }
    return selected;
}

function serializePrivateCredentialPayload(payload) {
    return {
        version: 1,
        environment: { ...payload.environment },
        files: payload.files.map(entry => ({
            target: entry.target,
            content_base64: entry.raw.toString('base64'),
            sha256: entry.sha256
        }))
    };
}

function readPrivateCredentialCache(bundleDigest) {
    const safeStorage = electron.safeStorage;
    if (!safeStorage || !safeStorage.isEncryptionAvailable()) return null;
    const cachePath = privateCredentialCachePath();
    try {
        const stat = fs.statSync(cachePath);
        if (!stat.isFile() || stat.size <= 0 || stat.size > JK_PRIVATE_CREDENTIAL_MAX_BUNDLE_BYTES) return null;
        const plaintext = safeStorage.decryptString(fs.readFileSync(cachePath));
        const cached = JSON.parse(plaintext);
        if (!cached || cached.format !== 'jk-private-cache-v1' || cached.bundle_digest !== bundleDigest) return null;
        return privateCredentialBootstrapCore.validatePrivateCredentialPayload(cached.payload);
    } catch (_err) {
        return null;
    }
}

function writePrivateCredentialCache(bundleDigest, payload) {
    const safeStorage = electron.safeStorage;
    if (!safeStorage || !safeStorage.isEncryptionAvailable()) {
        const error = new Error('A protecao de credenciais do Windows nao esta disponivel.');
        error.code = 'PRIVATE_BUNDLE_WINDOWS_PROTECTION_UNAVAILABLE';
        throw error;
    }
    const cachePath = privateCredentialCachePath();
    const cacheDir = path.dirname(cachePath);
    fs.mkdirSync(cacheDir, { recursive: true });
    const plaintext = JSON.stringify({
        format: 'jk-private-cache-v1',
        bundle_digest: bundleDigest,
        payload: serializePrivateCredentialPayload(payload)
    });
    const encrypted = safeStorage.encryptString(plaintext);
    const tempPath = path.join(cacheDir, `.${JK_PRIVATE_CREDENTIAL_CACHE_FILE}.${process.pid}.${Date.now()}.tmp`);
    fs.writeFileSync(tempPath, encrypted, { mode: 0o600, flag: 'wx' });
    fs.renameSync(tempPath, cachePath);
    try { fs.chmodSync(cachePath, 0o600); } catch (_err) {}
}

function privateCredentialUnlockHtml() {
    return `<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
  <title>Desbloquear instalador privado</title>
  <style>
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #0f172a; color: #e5eefb; font-family: "Segoe UI", sans-serif; }
    main { width: min(460px, calc(100vw - 34px)); padding: 28px; border: 1px solid #334155; border-radius: 14px; background: #111827; box-shadow: 0 24px 70px rgba(0,0,0,.42); }
    h1 { margin: 0 0 10px; font-size: 22px; }
    p { margin: 0 0 18px; color: #b9c6d8; line-height: 1.45; }
    label { display: block; margin-bottom: 7px; font-size: 13px; font-weight: 700; }
    input { width: 100%; height: 42px; padding: 0 12px; border: 1px solid #475569; border-radius: 8px; background: #0b1220; color: #fff; font-size: 15px; }
    input:focus { outline: 2px solid #38bdf8; outline-offset: 1px; }
    #error { min-height: 20px; margin: 10px 0 0; color: #fca5a5; font-size: 13px; }
    footer { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
    button { min-width: 112px; height: 38px; border: 0; border-radius: 8px; font-weight: 700; cursor: pointer; }
    #cancel { background: #334155; color: #e5eefb; }
    #unlock { background: #22c55e; color: #052e16; }
    button:disabled { opacity: .55; cursor: wait; }
  </style>
</head>
<body>
  <main>
    <h1>Instalador privado JK Sistema</h1>
    <p>Digite a senha entregue separadamente. As chaves serao protegidas pelo Windows nesta maquina e a senha nao sera armazenada.</p>
    <form id="unlock-form">
      <label for="password">Senha do cofre privado</label>
      <input id="password" type="password" minlength="16" maxlength="512" autocomplete="off" autofocus required>
      <div id="error" role="alert" aria-live="polite"></div>
      <footer>
        <button id="cancel" type="button">Cancelar</button>
        <button id="unlock" type="submit">Desbloquear</button>
      </footer>
    </form>
  </main>
</body>
</html>`;
}

function requestPrivateCredentialUnlock(parent, bundle) {
    return new Promise((resolve, reject) => {
        let settled = false;
        let attemptInProgress = false;
        const promptWindow = new BrowserWindow({
            width: 520,
            height: 390,
            title: 'Desbloquear instalador privado',
            parent: parent && !parent.isDestroyed() ? parent : undefined,
            modal: !!(parent && !parent.isDestroyed()),
            resizable: false,
            minimizable: false,
            maximizable: false,
            autoHideMenuBar: true,
            show: false,
            webPreferences: {
                preload: privateCredentialPreloadPath(),
                nodeIntegration: false,
                contextIsolation: true,
                sandbox: true
            }
        });

        const cleanup = () => {
            ipcMain.removeListener('jk-private-bootstrap-submit', onSubmit);
            ipcMain.removeListener('jk-private-bootstrap-cancel', onCancel);
        };
        const fail = (error) => {
            if (settled) return;
            settled = true;
            cleanup();
            if (!promptWindow.isDestroyed()) promptWindow.destroy();
            reject(error);
        };
        const onCancel = (event) => {
            if (event.sender !== promptWindow.webContents) return;
            const error = new Error('O desbloqueio do instalador privado foi cancelado.');
            error.code = 'PRIVATE_BUNDLE_UNLOCK_CANCELLED';
            fail(error);
        };
        const onSubmit = (event, password) => {
            if (event.sender !== promptWindow.webContents || settled || attemptInProgress) return;
            attemptInProgress = true;
            try {
                const payload = privateCredentialBootstrapCore.decryptPrivateCredentialBundle(bundle, password);
                settled = true;
                cleanup();
                if (!promptWindow.isDestroyed()) promptWindow.destroy();
                resolve(payload);
            } catch (err) {
                attemptInProgress = false;
                if (!promptWindow.isDestroyed()) {
                    promptWindow.webContents.send(
                        'jk-private-bootstrap-error',
                        err && err.code === 'PRIVATE_BUNDLE_PASSWORD_INVALID'
                            ? 'A senha precisa ter pelo menos 16 caracteres.'
                            : 'Senha incorreta ou cofre privado corrompido.'
                    );
                }
            }
        };

        ipcMain.on('jk-private-bootstrap-submit', onSubmit);
        ipcMain.on('jk-private-bootstrap-cancel', onCancel);
        promptWindow.once('closed', () => {
            if (settled) return;
            const error = new Error('O desbloqueio do instalador privado foi cancelado.');
            error.code = 'PRIVATE_BUNDLE_UNLOCK_CANCELLED';
            fail(error);
        });
        promptWindow.once('ready-to-show', () => promptWindow.show());
        promptWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(privateCredentialUnlockHtml())}`)
            .catch(fail);
    });
}

async function ensurePrivateCredentialBootstrap(runtimeDir, parent = null) {
    const bundlePath = privateCredentialBundlePath();
    if (!bundlePath || !fs.existsSync(bundlePath)) {
        return { present: false, unlocked: false };
    }
    const raw = fs.readFileSync(bundlePath);
    if (!raw.length || raw.length > JK_PRIVATE_CREDENTIAL_MAX_BUNDLE_BYTES) {
        const error = new Error('O cofre privado esta vazio ou excede o limite permitido.');
        error.code = 'PRIVATE_BUNDLE_INVALID';
        throw error;
    }
    let bundle;
    try {
        bundle = JSON.parse(raw.toString('utf8'));
    } catch (_err) {
        const error = new Error('O cofre privado nao e um JSON valido.');
        error.code = 'PRIVATE_BUNDLE_INVALID';
        throw error;
    }
    const bundleDigest = privateCredentialBootstrapCore.privateCredentialBundleDigest(raw);
    let payload = readPrivateCredentialCache(bundleDigest);
    let firstUnlock = false;
    if (!payload) {
        payload = await requestPrivateCredentialUnlock(parent || mainWindow, bundle);
        writePrivateCredentialCache(bundleDigest, payload);
        firstUnlock = true;
    }
    const applied = privateCredentialBootstrapCore.applyPrivateCredentialPayload(payload, runtimeDir);
    logElectronLifecycle('private-credential-bootstrap-ready', {
        firstUnlock,
        environmentKeys: applied.environmentKeys,
        credentialFiles: applied.credentialFiles,
        writtenFiles: applied.writtenFiles,
        preservedFiles: applied.preservedFiles
    });
    return { present: true, unlocked: true, firstUnlock, ...applied };
}
