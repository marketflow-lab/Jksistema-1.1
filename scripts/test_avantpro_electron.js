const { app, BrowserWindow, session } = require('electron');
const fs = require('fs');
const os = require('os');
const path = require('path');

const AVANTPRO_EXTENSION_ID = 'jdefnfmbnchmnjkcknaadaddgjbgephh';
const SEARCH_URL = process.env.AVANTPRO_TEST_URL
    || 'https://lista.mercadolivre.com.br/cebol%C3%A3o-sensor-de-temperatura-Honda-Cb500-2002';

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

function findAvantProExtensionDirs() {
    const dirs = [];
    if (process.env.JK_CHROME_EXTENSIONS_DIR) {
        dirs.push(path.resolve(process.env.JK_CHROME_EXTENSIONS_DIR));
    }

    const chromeUserData = process.env.LOCALAPPDATA
        ? path.join(process.env.LOCALAPPDATA, 'Google', 'Chrome', 'User Data')
        : '';
    if (chromeUserData && fs.existsSync(chromeUserData)) {
        const found = [];
        for (const profile of fs.readdirSync(chromeUserData, { withFileTypes: true })) {
            if (!profile.isDirectory()) continue;
            if (profile.name !== 'Default' && !/^Profile\s+\d+$/i.test(profile.name)) continue;
            const extensionRoot = path.join(chromeUserData, profile.name, 'Extensions', AVANTPRO_EXTENSION_ID);
            if (!fs.existsSync(extensionRoot)) continue;
            for (const versionEntry of fs.readdirSync(extensionRoot, { withFileTypes: true })) {
                if (!versionEntry.isDirectory()) continue;
                const dir = path.join(extensionRoot, versionEntry.name);
                const manifestPath = path.join(dir, 'manifest.json');
                if (!fs.existsSync(manifestPath)) continue;
                found.push({
                    dir,
                    profile: profile.name,
                    version: versionEntry.name.replace(/_\d+$/i, ''),
                });
            }
        }
        found.sort((left, right) => compareVersionStrings(right.version, left.version));
        dirs.push(...found.map(item => item.dir));
    }

    dirs.push(path.resolve(__dirname, '..', 'extensoes_chrome', 'avant_pro'));
    return Array.from(new Set(dirs)).filter(dir => fs.existsSync(path.join(dir, 'manifest.json')));
}

function wait(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function writeResult(result) {
    const outPath = process.env.AVANTPRO_TEST_OUTPUT
        || path.resolve(__dirname, '..', 'logs', 'avantpro_electron_test.json');
    fs.mkdirSync(path.dirname(outPath), { recursive: true });
    fs.writeFileSync(outPath, JSON.stringify(result, null, 2), 'utf8');
    console.log(JSON.stringify(result, null, 2));
}

async function waitForLoad(win, timeoutMs = 30000) {
    return new Promise((resolve, reject) => {
        let done = false;
        const finish = (err) => {
            if (done) return;
            done = true;
            clearTimeout(timer);
            win.webContents.removeListener('did-finish-load', onLoad);
            win.webContents.removeListener('did-fail-load', onFail);
            if (err) reject(err);
            else resolve();
        };
        const onLoad = () => finish();
        const onFail = (_event, code, description, url) => {
            finish(new Error(`did-fail-load ${code} ${description || ''} ${url || ''}`.trim()));
        };
        const timer = setTimeout(() => finish(new Error(`timeout after ${timeoutMs}ms`)), timeoutMs);
        win.webContents.once('did-finish-load', onLoad);
        win.webContents.once('did-fail-load', onFail);
    });
}

async function scanPage(win) {
    return await win.webContents.executeJavaScript(`
        (function () {
            var avantNodes = Array.prototype.slice.call(document.querySelectorAll('[class*="avant"], [id*="avant"], .created-time-card, .avantpro-product-info-row'));
            var scripts = performance.getEntriesByType('resource')
                .map(function (entry) { return entry.name || ''; })
                .filter(function (name) { return /chrome-extension|avantpro/i.test(name); });
            return {
                href: location.href,
                title: document.title,
                readyState: document.readyState,
                bodyTextSample: String(document.body && document.body.innerText || '').slice(0, 500),
                hasChromeRuntime: !!(window.chrome && chrome.runtime),
                avantNodeCount: avantNodes.length,
                avantNodeSamples: avantNodes.slice(0, 12).map(function (node) {
                    return {
                        tag: node.tagName,
                        id: node.id || '',
                        className: String(node.className || ''),
                        text: String(node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 160)
                    };
                }),
                extensionResources: scripts.slice(0, 30)
            };
        })();
    `, true);
}

async function main() {
    const useJkProfile = process.env.AVANTPRO_TEST_USE_JK_PROFILE === '1';
    const profileDir = useJkProfile
        ? path.resolve(__dirname, '..', 'info', 'electron_user_data')
        : path.join(os.tmpdir(), `jk-avantpro-electron-test-${Date.now()}`);
    const partition = useJkProfile ? 'persist:jk-sistema-browser' : 'persist:avantpro-test';
    app.setPath('userData', profileDir);
    app.commandLine.appendSwitch('disable-http-cache');

    await app.whenReady();
    const extensionDirs = findAvantProExtensionDirs();
    const ses = session.fromPartition(partition);

    const events = [];
    ses.on('extension-loaded', (_event, ext) => events.push({ event: 'extension-loaded', id: ext.id, name: ext.name, path: ext.path }));
    ses.on('extension-ready', (_event, ext) => events.push({ event: 'extension-ready', id: ext.id, name: ext.name, path: ext.path }));
    ses.webRequest.onBeforeRequest({ urls: ['chrome-extension://*/*'] }, (details, callback) => {
        events.push({ event: 'extension-request', url: details.url, resourceType: details.resourceType });
        callback({});
    });

    const loaded = [];
    for (const extensionDir of extensionDirs) {
        try {
            const ext = await ses.loadExtension(extensionDir, { allowFileAccess: true });
            loaded.push({ id: ext.id, name: ext.name, path: ext.path, url: ext.url, version: ext.manifest && ext.manifest.version });
            break;
        } catch (err) {
            events.push({ event: 'load-error', path: extensionDir, error: err && err.message ? err.message : String(err) });
        }
    }

    const win = new BrowserWindow({
        show: false,
        width: 1400,
        height: 900,
        webPreferences: {
            session: ses,
            nodeIntegration: false,
            contextIsolation: true
        }
    });

    const consoleMessages = [];
    win.webContents.on('console-message', (_event, level, message, line, sourceId) => {
        const text = String(message || '');
        if (/avant|chrome|extension|runtime|error|erro|failed|fail/i.test(text + ' ' + sourceId)) {
            consoleMessages.push({ level, message: text.slice(0, 500), line, sourceId });
        }
    });

    await win.loadURL(SEARCH_URL);
    await waitForLoad(win).catch(() => null);
    await wait(12000);
    const scan = await scanPage(win);

    writeResult({
        ok: true,
        searchUrl: SEARCH_URL,
        profileDir,
        partition,
        loaded,
        loadedFromSession: ses.getAllExtensions().map(ext => ({
            id: ext.id,
            name: ext.name,
            path: ext.path,
            url: ext.url,
            version: ext.manifest && ext.manifest.version
        })),
        events: events.slice(0, 80),
        consoleMessages: consoleMessages.slice(0, 80),
        scan
    });

    win.destroy();
    app.quit();
}

main().catch((err) => {
    writeResult({
        ok: false,
        error: err && err.stack ? err.stack : String(err)
    });
    app.quit();
    process.exit(1);
});
