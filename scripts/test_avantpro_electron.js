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
            var norm = function (value) {
                var text = String(value || '').replace(/\\s+/g, ' ').trim();
                try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                return text.toLowerCase();
            };
            var visible = function (node) {
                if (!node || !node.getBoundingClientRect) return false;
                var rect = node.getBoundingClientRect();
                var style = window.getComputedStyle ? window.getComputedStyle(node) : null;
                return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0
                    && (!style || (style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) !== 0));
            };
            var textOf = function (node) {
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
            var emailInputs = Array.prototype.slice.call(document.querySelectorAll('input:not([type="hidden"]), textarea, [role="textbox"], [contenteditable="true"]'))
                .filter(function (node) {
                    return visible(node) && /email|e-?mail|mail|credenciais|avant/.test(norm(textOf(node)));
                });
            var confirmarButtons = Array.prototype.slice.call(document.querySelectorAll('button, input[type="button"], input[type="submit"], [role="button"], a'))
                .filter(function (node) {
                    return visible(node) && /confirmar|entrar|acessar|login|iniciar|continuar|enviar|comecar|começar/.test(norm(textOf(node)));
                });
            var scripts = performance.getEntriesByType('resource')
                .map(function (entry) { return entry.name || ''; })
                .filter(function (name) { return /chrome-extension|avantpro/i.test(name); });
            return {
                href: location.href,
                title: document.title,
                readyState: document.readyState,
                bodyTextSample: String(document.body && document.body.innerText || '').slice(0, 500),
                hasChromeRuntime: !!(window.chrome && chrome.runtime),
                hasThanksMessage: /obrigado\\s+por\\s+usar\\s+nossa\\s+extensao|obrigado\\s+por\\s+usar\\s+nossa\\s+extens[aã]o/.test(norm(document.body && document.body.innerText || '')),
                emailInputCount: emailInputs.length,
                confirmarButtonCount: confirmarButtons.length,
                emailInputSamples: emailInputs.slice(0, 5).map(function (node) {
                    var rect = node.getBoundingClientRect();
                    return {
                        tag: node.tagName,
                        type: node.type || '',
                        id: node.id || '',
                        className: String(node.className || ''),
                        placeholder: node.getAttribute && node.getAttribute('placeholder') || '',
                        x: Math.round(rect.left + rect.width / 2),
                        y: Math.round(rect.top + rect.height / 2)
                    };
                }),
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

async function localizarFerramentasAvantPro(win) {
    return await win.webContents.executeJavaScript(`
        (function () {
            var normalizar = function (value) {
                var text = String(value || '').replace(/\\s+/g, ' ').trim();
                try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                return text.toLowerCase();
            };
            var visivel = function (node) {
                if (!node || !node.getBoundingClientRect) return false;
                var rect = node.getBoundingClientRect();
                var style = window.getComputedStyle ? window.getComputedStyle(node) : null;
                return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0
                    && rect.left < (window.innerWidth || document.documentElement.clientWidth || 0)
                    && rect.top < (window.innerHeight || document.documentElement.clientHeight || 0)
                    && (!style || (style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) !== 0));
            };
            var textoNode = function (node) {
                if (!node) return '';
                return [
                    node.innerText,
                    node.textContent,
                    node.value,
                    node.getAttribute && node.getAttribute('aria-label'),
                    node.getAttribute && node.getAttribute('title'),
                    node.getAttribute && node.getAttribute('class'),
                    node.getAttribute && node.getAttribute('id')
                ].filter(Boolean).join(' ');
            };
            var contextoNode = function (node) {
                var parts = [textoNode(node)];
                var current = node && node.parentElement;
                for (var level = 0; current && level < 5; level += 1) {
                    parts.push(textoNode(current));
                    parts.push(current.innerText || current.textContent || '');
                    current = current.parentElement;
                }
                return normalizar(parts.filter(Boolean).join(' '));
            };
            var escolherAlvoClique = function (node) {
                var candidatos = [];
                var incluir = function (item, bonus) {
                    if (!item || candidatos.indexOf(item) >= 0 || !visivel(item)) return;
                    var rect = item.getBoundingClientRect();
                    if (rect.width < 24 || rect.height < 12) return;
                    var texto = normalizar(textoNode(item));
                    var alvo = texto + ' ' + contextoNode(item);
                    if (!/\\bferramentas\\b|\\btools\\b/.test(alvo)) return;
                    if (/assine\\s+ja|assinar|suporte/.test(texto) && !/^ferramentas$|^tools$/.test(texto)) return;
                    var area = rect.width * rect.height;
                    var score = Number(bonus) || 0;
                    if (/button|a/i.test(item.tagName || '')) score += 80;
                    if (item.getAttribute && item.getAttribute('role') === 'button') score += 70;
                    if (rect.width >= 70 && rect.height >= 28 && rect.width <= 280 && rect.height <= 120) score += 90;
                    if (/avant|speed|dial|menu|tool|ferramentas/.test(String(item.className || '') + ' ' + String(item.id || ''))) score += 50;
                    if (area > 1200 && area < 32000) score += 40;
                    candidatos.push({ node: item, score: score, area: area });
                };
                incluir(node, 0);
                try {
                    incluir(node.closest && node.closest('button, a, [role="button"], [class*="speed-dial-action"], [class*="speed-dial-item"], [class*="floating-button"], [class*="avantpro"]'), 50);
                } catch (_err) {}
                var atual = node && node.parentElement;
                for (var nivel = 0; atual && nivel < 5; nivel += 1) {
                    incluir(atual, 40 - nivel * 5);
                    atual = atual.parentElement;
                }
                candidatos.sort(function (a, b) { return b.score - a.score || b.area - a.area; });
                return candidatos.length ? candidatos[0].node : node;
            };
            var todosCandidatos = Array.prototype.slice.call(document.querySelectorAll('button, a, input[type="button"], input[type="submit"], [role="button"], [tabindex], [aria-label], [title], [class*="andes-button"], [class*="avant"], [id*="avant"], [class*="speed"], [class*="menu"], div, span'))
                .map(function (node, index) {
                    if (!visivel(node)) return null;
                    var texto = normalizar(textoNode(node));
                    if (!/\\bferramentas\\b|\\btools\\b/.test(texto)) return null;
                    if (/assine\\s+ja|assinar|suporte/.test(texto)) return null;
                    var clickNode = escolherAlvoClique(node);
                    var rect = clickNode.getBoundingClientRect();
                    return {
                        node: clickNode,
                        index: index,
                        score: (/button|a/i.test(clickNode.tagName) ? 80 : 0) + (/avant|speed|menu|tool|ferramentas/.test(String(clickNode.className || '') + ' ' + texto) ? 80 : 0) + (rect.width >= 70 && rect.height >= 28 ? 90 : 0),
                        x: rect.left + rect.width / 2,
                        y: rect.top + rect.height / 2,
                        width: rect.width,
                        height: rect.height,
                        label: textoNode(node).replace(/\\s+/g, ' ').trim().slice(0, 120)
                    };
                })
                .filter(Boolean);
            var candidatos = todosCandidatos
                .filter(function (item) { return item.width >= 40 && item.height >= 20; })
                .sort(function (a, b) { return b.score - a.score || a.index - b.index; });
            if (candidatos.length) {
                var item = candidatos[0];
                return {
                    success: true,
                    source: 'ferramentas_dom_real',
                    x: Math.round(item.x),
                    y: Math.round(item.y),
                    width: item.width,
                    height: item.height,
                    label: item.label
                };
            }
            var rotulosPequenos = todosCandidatos
                .filter(function (item) { return item.score > 0 && (item.width < 40 || item.height < 20); })
                .sort(function (a, b) { return b.score - a.score || a.index - b.index; });
            if (rotulosPequenos.length) {
                var r = rotulosPequenos[0];
                return {
                    success: true,
                    source: 'ferramentas_rotulo_estimado',
                    x: Math.max(40, Math.min(Math.round(r.x - 30), (window.innerWidth || document.documentElement.clientWidth || 1280) - 1)),
                    y: Math.max(40, Math.min(Math.round(r.y), (window.innerHeight || document.documentElement.clientHeight || 900) - 1)),
                    width: 120,
                    height: 46,
                    label: r.label || 'Ferramentas'
                };
            }
            var bodyBusca = normalizar(document.body && (document.body.innerText || document.body.textContent) || '');
            if (/\\bferramentas\\b/.test(bodyBusca) && (/\\bsuporte\\b|assine\\s+ja|avant\\s*pro|avantpro/.test(bodyBusca))) {
                return {
                    success: true,
                    source: 'ferramentas_menu_lateral_estimado',
                    x: Math.max(40, (window.innerWidth || document.documentElement.clientWidth || 1280) - 110),
                    y: Math.max(40, (window.innerHeight || document.documentElement.clientHeight || 900) - 180),
                    width: 120,
                    height: 46,
                    label: 'Ferramentas'
                };
            }
            return { success: false, reason: 'ferramentas_nao_localizado', bodyText: String(document.body && document.body.innerText || '').slice(0, 500) };
        })();
    `, true);
}

async function clicarFerramentasAvantPro(win) {
    const target = await localizarFerramentasAvantPro(win);
    if (!target || !target.success) return { target, clicked: false };
    win.webContents.focus();
    win.webContents.sendInputEvent({ type: 'mouseMove', x: target.x, y: target.y, movementX: 0, movementY: 0 });
    win.webContents.sendInputEvent({ type: 'mouseDown', x: target.x, y: target.y, button: 'left', clickCount: 1 });
    await wait(60);
    win.webContents.sendInputEvent({ type: 'mouseUp', x: target.x, y: target.y, button: 'left', clickCount: 1 });
    await wait(Number(process.env.AVANTPRO_TEST_CLICK_WAIT_MS) || 5000);
    return { target, clicked: true };
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
    let ferramentasClick = null;
    let scanAfterTools = null;
    if (process.env.AVANTPRO_TEST_CLICK_TOOLS === '1') {
        ferramentasClick = await clicarFerramentasAvantPro(win);
        scanAfterTools = await scanPage(win);
    }

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
        scan,
        ferramentasClick,
        scanAfterTools
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
