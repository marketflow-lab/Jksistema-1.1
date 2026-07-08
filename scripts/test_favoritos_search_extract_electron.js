const { app, BrowserWindow, session } = require('electron');
const fs = require('fs');
const os = require('os');
const path = require('path');

if (!app || !BrowserWindow || !session) {
    throw new Error('Este script precisa ser executado pelo electron.exe, nao pelo node/electron.cmd.');
}

const AVANTPRO_EXTENSION_ID = 'jdefnfmbnchmnjkcknaadaddgjbgephh';
const SEARCH_URL = process.env.FAVORITOS_SEARCH_TEST_URL
    || 'https://lista.mercadolivre.com.br/cebolao-sensor-de-temperatura-honda-cb500-2002';
const OUTPUT = process.env.FAVORITOS_SEARCH_TEST_OUTPUT
    || path.resolve(__dirname, '..', 'logs', 'favoritos_search_extract_electron.json');
const SCREENSHOT = process.env.FAVORITOS_SEARCH_TEST_SCREENSHOT
    || path.resolve(__dirname, '..', 'logs', 'favoritos_search_extract_electron.png');
const TRACE = process.env.FAVORITOS_SEARCH_TEST_TRACE
    || path.resolve(__dirname, '..', 'logs', 'favoritos_search_extract_electron.trace.log');

function trace(step, extra = {}) {
    try {
        fs.mkdirSync(path.dirname(TRACE), { recursive: true });
        fs.appendFileSync(TRACE, `${new Date().toISOString()} ${step} ${JSON.stringify(extra)}\n`, 'utf8');
    } catch (_err) {}
}

function wait(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
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
            const root = path.join(chromeUserData, profile.name, 'Extensions', AVANTPRO_EXTENSION_ID);
            if (!fs.existsSync(root)) continue;
            for (const versionEntry of fs.readdirSync(root, { withFileTypes: true })) {
                if (!versionEntry.isDirectory()) continue;
                const dir = path.join(root, versionEntry.name);
                if (fs.existsSync(path.join(dir, 'manifest.json'))) {
                    found.push({ dir, version: versionEntry.name.replace(/_\d+$/i, '') });
                }
            }
        }
        found.sort((left, right) => compareVersionStrings(right.version, left.version));
        dirs.push(...found.map(item => item.dir));
    }
    dirs.push(path.resolve(__dirname, '..', 'extensoes_chrome', 'avant_pro'));
    return Array.from(new Set(dirs)).filter(dir => fs.existsSync(path.join(dir, 'manifest.json')));
}

function sourceElectronProfileDir() {
    if (process.env.FAVORITOS_SEARCH_SOURCE_PROFILE) {
        return path.resolve(process.env.FAVORITOS_SEARCH_SOURCE_PROFILE);
    }
    if (process.env.APPDATA) {
        return path.join(process.env.APPDATA, 'JK Sistema Cliente', 'local_app', 'info', 'electron_user_data');
    }
    return path.resolve(__dirname, '..', 'info', 'electron_user_data');
}

function copyDirFiltered(source, target, stats) {
    const skipNames = new Set(['Cache', 'Code Cache', 'GPUCache', 'ShaderCache', 'DawnCache', 'GrShaderCache']);
    if (!fs.existsSync(source)) return;
    fs.mkdirSync(target, { recursive: true });
    for (const entry of fs.readdirSync(source, { withFileTypes: true })) {
        if (skipNames.has(entry.name)) continue;
        const from = path.join(source, entry.name);
        const to = path.join(target, entry.name);
        try {
            if (entry.isDirectory()) {
                copyDirFiltered(from, to, stats);
            } else if (entry.isFile()) {
                fs.mkdirSync(path.dirname(to), { recursive: true });
                fs.copyFileSync(from, to);
                stats.files += 1;
            }
        } catch (err) {
            stats.skipped.push({ path: from, error: err && err.message ? err.message : String(err) });
        }
    }
}

function removeDirSafe(target, allowedRoot) {
    const resolvedTarget = path.resolve(target);
    const resolvedRoot = path.resolve(allowedRoot);
    if (!resolvedTarget || resolvedTarget === resolvedRoot || !resolvedTarget.startsWith(resolvedRoot + path.sep)) {
        throw new Error(`Recusa ao remover caminho fora do perfil temporario: ${resolvedTarget}`);
    }
    fs.rmSync(resolvedTarget, { recursive: true, force: true });
}

function restoreAvantSnapshotIntoTempProfile(sourceRoot, tempRoot, targetPartition, copyStats) {
    if (process.env.FAVORITOS_SEARCH_RESTORE_AVANT_SNAPSHOT === '0') return;
    const snapshotDir = path.join(
        sourceRoot,
        '_avantpro_storage_last_good',
        AVANTPRO_EXTENSION_ID
    );
    if (!fs.existsSync(snapshotDir)) {
        copyStats.snapshot = { restored: false, reason: 'snapshot-missing', snapshotDir };
        return;
    }
    const targetDir = path.join(
        targetPartition,
        'Local Extension Settings',
        AVANTPRO_EXTENSION_ID
    );
    const beforeFiles = copyStats.files;
    try {
        removeDirSafe(targetDir, tempRoot);
        copyDirFiltered(snapshotDir, targetDir, copyStats);
        copyStats.snapshot = {
            restored: copyStats.files > beforeFiles,
            copied: copyStats.files - beforeFiles,
            snapshotDir,
            targetDir
        };
    } catch (err) {
        copyStats.snapshot = {
            restored: false,
            reason: 'snapshot-copy-failed',
            snapshotDir,
            targetDir,
            error: err && err.message ? err.message : String(err)
        };
    }
}

function prepareTempProfile() {
    const sourceRoot = sourceElectronProfileDir();
    const sourcePartition = path.join(sourceRoot, 'Partitions', 'jk-sistema-browser');
    if (process.env.FAVORITOS_SEARCH_USE_LIVE_PROFILE === '1') {
        return {
            tempRoot: sourceRoot,
            usingLiveProfile: true,
            copyStats: {
                sourceRoot,
                sourcePartition,
                targetPartition: sourcePartition,
                files: 0,
                skipped: [],
                liveProfile: true
            }
        };
    }
    const tempRoot = path.join(os.tmpdir(), `jk-favoritos-search-electron-${Date.now()}`);
    const targetPartition = path.join(tempRoot, 'Partitions', 'jk-sistema-browser');
    const copyStats = { sourceRoot, sourcePartition, targetPartition, files: 0, skipped: [] };
    if (process.env.FAVORITOS_SEARCH_COPY_PROFILE !== '0' && fs.existsSync(sourcePartition)) {
        copyDirFiltered(sourcePartition, targetPartition, copyStats);
    }
    restoreAvantSnapshotIntoTempProfile(sourceRoot, tempRoot, targetPartition, copyStats);
    return { tempRoot, copyStats, usingLiveProfile: false };
}

function readScript(relativePath) {
    return fs.readFileSync(path.resolve(__dirname, '..', relativePath), 'utf8');
}

async function waitForLoad(win, timeoutMs = 45000) {
    return new Promise((resolve) => {
        let done = false;
        const finish = (result) => {
            if (done) return;
            done = true;
            clearTimeout(timer);
            win.webContents.removeListener('did-finish-load', onLoad);
            win.webContents.removeListener('did-fail-load', onFail);
            resolve(result);
        };
        const onLoad = () => finish({ loaded: true });
        const onFail = (_event, code, description, url) => finish({ loaded: false, code, description, url });
        const timer = setTimeout(() => finish({ loaded: false, timeout: true }), timeoutMs);
        win.webContents.once('did-finish-load', onLoad);
        win.webContents.once('did-fail-load', onFail);
    });
}

async function injectFavoritosScripts(win) {
    await win.webContents.executeJavaScript(`
        window.electronAPI = window.electronAPI || {};
        try { localStorage.setItem('user_data', JSON.stringify({ username: 'codex', nome: 'Codex Teste' })); } catch (_err) {}
        try { localStorage.setItem('permissions', JSON.stringify({ full: true, favoritos: true })); } catch (_err) {}
        try { localStorage.setItem('access_token', 'codex-test-token'); } catch (_err) {}
        window.AVANT_PRO_LOGIN_EMAIL = '';
        window.headersJsonAutenticado = function () { return {}; };
        window.sinalFavoritosAtual = function () { return undefined; };
        window.mostrarBalaoFavoritosStatus = function () {};
        window.FavoritosV2 = window.FavoritosV2 || { ui: { statusModal: {} } };
        window.mlWebviewEl = {
            executeJavaScript: async function (script) {
                return (0, eval)(String(script || ''));
            }
        };
        true;
    `, true);
    for (const script of [
        'static/favoritos/v2/browser/url-utils.js',
        'static/favoritos/v2/browser/shell-bridge.js',
        'static/favoritos/v2/browser/avant-cache.js',
        'static/favoritos/ml-browser.js',
        'static/favoritos/ranking.js',
        'static/favoritos/promocoes-efetivacao.js',
        'static/favoritos/tabelas-layout/01-ml-base-busca.js',
        'static/favoritos/tabelas-layout/04-promocoes-busca-ranking.js'
    ]) {
        await win.webContents.executeJavaScript(`${readScript(script)}\n; true;\n//# sourceURL=${script}`, true);
    }
    await win.webContents.executeJavaScript('try { mlWebviewEl = window.mlWebviewEl; } catch (_err) {} true;', true);
}

async function collect(win) {
    const raw = await win.webContents.executeJavaScript(`
        (async function () {
            var timeoutValue = function (ms, value) {
                return new Promise(function (resolve) { setTimeout(function () { resolve(value); }, ms); });
            };
            var slim = function (item) {
                if (!item || typeof item !== 'object') return item;
                return {
                    id: item.id || item.mlb || '',
                    mlb: item.mlb || item.id || '',
                    posicao: item.posicao || '',
                    titulo: item.titulo || item.title || '',
                    url: item.url || item.permalink || item.link || '',
                    imagem: item.imagem || item.thumbnail || item.foto || '',
                    preco: item.preco,
                    price: item.price,
                    preco_original: item.preco_original,
                    preco_promocional: item.preco_promocional,
                    moeda: item.moeda || item.currency_id || '',
                    vendedor: item.vendedor || '',
                    vendas: item.vendas,
                    media_mensal: item.media_mensal || item.ritmo_atual || '',
                    data_criacao: item.data_criacao || '',
                    tituloFonte: item.tituloFonte || item.titulo_fonte || '',
                    fotoFonte: item.fotoFonte || item.foto_fonte || '',
                    precoFonte: item.precoFonte || item.preco_fonte || item.fonte_preco || '',
                    vendasFonte: item.vendasFonte || item.vendas_fonte || '',
                    vendedorFonte: item.vendedorFonte || item.vendedor_fonte || '',
                    dataCriacaoFonte: item.dataCriacaoFonte || item.data_criacao_fonte || '',
                    estado_qualidade: item.estado_qualidade || '',
                    origem_dados: item.origem_dados || item.source || ''
                };
            };
            var pageInfo = {
                href: location.href,
                title: document.title,
                readyState: document.readyState,
                bodyTextSample: String(document.body && document.body.innerText || '').slice(0, 900)
            };
            var direto = {
                productLinks: Array.prototype.slice.call(document.querySelectorAll('a[href]'))
                    .map(function (a) { return a.href || a.getAttribute('href') || ''; })
                    .filter(function (href) { return /MLB|produto\\.mercadolivre/.test(href); })
                    .slice(0, 30),
                selectorCounts: {}
            };
            [
                'li.ui-search-layout__item',
                'div.ui-search-result__wrapper',
                'div.poly-card',
                'section.poly-card',
                'article.poly-card',
                '[class*="poly-card"]',
                '[class*="ui-search-result"]',
                'main ol > li',
                'main ul > li',
                '.avantpro-product-info-row',
                '[class*="avantpro"]'
            ].forEach(function (selector) {
                direto.selectorCounts[selector] = document.querySelectorAll(selector).length;
            });
            direto.debugCards = Array.prototype.slice.call(document.querySelectorAll('li.ui-search-layout__item, div.poly-card, [class*="ui-search-result"]'))
                .slice(0, 8)
                .map(function (card, index) {
                    var cls = function (node) { return String(node && node.className || '').replace(/\\s+/g, ' ').slice(0, 180); };
                    var productHref = Array.prototype.slice.call(card.querySelectorAll('a[href]'))
                        .map(function (a) { return a.href || a.getAttribute('href') || ''; })
                        .filter(function (href) { return /MLB|\\/up\\/|produto\\.mercadolivre/.test(href); })[0] || '';
                    var titleNode = card.querySelector('h2, h3, .poly-component__title, .ui-search-item__title, [class*="title"]');
                    var priceNodes = Array.prototype.slice.call(card.querySelectorAll('.poly-price__current .andes-money-amount, .poly-component__price .andes-money-amount, .ui-search-price__second-line .andes-money-amount, [class*="price"] .andes-money-amount, .andes-money-amount, .price-tag'))
                        .slice(0, 20)
                        .map(function (node) {
                            var parents = [];
                            var atual = node;
                            for (var i = 0; atual && i < 5; i += 1) {
                                parents.push({
                                    tag: atual.tagName || '',
                                    cls: cls(atual),
                                    text: String(atual.innerText || atual.textContent || '').replace(/\\s+/g, ' ').slice(0, 160)
                                });
                                atual = atual.parentElement;
                            }
                            return {
                                text: String(node.innerText || node.textContent || '').replace(/\\s+/g, ' ').slice(0, 160),
                                cls: cls(node),
                                parents: parents
                            };
                        });
                    return {
                        index: index + 1,
                        cardClass: cls(card),
                        href: productHref,
                        title: String(titleNode && (titleNode.innerText || titleNode.textContent) || '').replace(/\\s+/g, ' ').slice(0, 220),
                        textSample: String(card.innerText || card.textContent || '').replace(/\\s+/g, ' ').slice(0, 700),
                        priceNodes: priceNodes
                    };
            });
            var base = await extrairCardsMercadoLivreBasicoWebview({ limite: 80 });
            var avant = await Promise.race([
                extrairAnunciosAvantProDomWebview({ limite: 80 }),
                timeoutValue(3000, { success: false, total: 0, anuncios: [], error: 'timeout_avant_global_teste' })
            ]);
            var merged = mesclarAnunciosAvant(base.anuncios || [], avant.anuncios || []);
            var resumo = resumoPrimeiraPaginaFavoritos(base.total || merged.length, merged);
            var progressEvents = [];
            var tempoControlada = Number(${JSON.stringify(process.env.FAVORITOS_SEARCH_TEMPO_LIMITE_MS || '45000')}) || 45000;
            var controlada = await Promise.race([
                coletarPrimeiraPaginaFavoritosControlada({
                    maxAnuncios: 80,
                    tempoLimiteMs: tempoControlada,
                    maxPassadas: Number(${JSON.stringify(process.env.FAVORITOS_SEARCH_MAX_PASSADAS || '1')}) || 1,
                    loteCliques: Number(${JSON.stringify(process.env.FAVORITOS_SEARCH_LOTE_CLIQUES || '6')}) || 0,
                    onProgress: function (payload) {
                        progressEvents.push(Object.assign({}, payload || {}));
                    }
                }),
                timeoutValue(tempoControlada + 10000, {
                    success: false,
                    totalVisiveis: base.total || 0,
                    anuncios: base.anuncios || [],
                    elapsedMs: tempoControlada + 10000,
                    tempoEsgotado: true,
                    resumo: resumoPrimeiraPaginaFavoritos(base.total || 0, base.anuncios || [], { etapa: 'timeout_teste' }),
                    error: 'timeout_controlada_teste'
                })
            ]);
            var avantQueue = window.__JK_AVANT_CARD_QUEUE_CLICKED_KEYS || {};
            var avantCache = window.__JK_AVANT_CARD_DATA_CACHE || {};
            var payload = {
                pageInfo: pageInfo,
                direto: direto,
                base: {
                    success: base.success,
                    total: base.total,
                    error: base.error || '',
                    debug: base.debug || null,
                    sample: (base.anuncios || []).slice(0, 8).map(slim),
                    all: (base.anuncios || []).slice(0, 100).map(slim)
                },
                avant: {
                    success: avant.success,
                    total: avant.total,
                    error: avant.error || '',
                    debug: avant.debug || null,
                    sample: (avant.anuncios || []).slice(0, 8).map(slim),
                    all: (avant.anuncios || []).slice(0, 100).map(slim)
                },
                merged: {
                    total: merged.length,
                    resumo: resumo,
                    sample: merged.slice(0, 8).map(slim),
                    all: merged.slice(0, 100).map(slim)
                },
                controlada: {
                    success: controlada.success,
                    totalVisiveis: controlada.totalVisiveis,
                    elapsedMs: controlada.elapsedMs,
                    tempoEsgotado: controlada.tempoEsgotado,
                    resumo: controlada.resumo,
                    progress: progressEvents.slice(-40),
                    queueKeys: Object.keys(avantQueue).slice(0, 20),
                    queueTotal: Object.keys(avantQueue).length,
                    queueDebug: window.__JK_AVANT_CARD_QUEUE_DEBUG || null,
                    queueLastResult: window.__JK_AVANT_LAST_QUEUE_RESULT || null,
                    cacheKeys: Object.keys(avantCache).slice(0, 20),
                    cacheTotal: Object.keys(avantCache).length,
                    lastClicked: window.__JK_AVANT_LAST_CARD_CLICKED || null,
                    sample: (controlada.anuncios || []).slice(0, 8).map(slim),
                    all: (controlada.anuncios || []).slice(0, 100).map(slim)
                }
            };
            return JSON.stringify(payload);
        })();
    `, true).catch(err => ({
        error: err && err.stack ? err.stack : String(err && err.message || err)
    }));
    if (typeof raw === 'string') {
        try {
            return JSON.parse(raw);
        } catch (err) {
            return { error: `Falha ao parsear JSON da coleta: ${err && err.message ? err.message : err}`, raw: raw.slice(0, 1000) };
        }
    }
    return raw;
}

function writeResult(payload) {
    trace('write-result', { ok: !!(payload && payload.ok), output: OUTPUT });
    fs.mkdirSync(path.dirname(OUTPUT), { recursive: true });
    fs.writeFileSync(OUTPUT, JSON.stringify(payload, null, 2), 'utf8');
    console.log(JSON.stringify(payload, null, 2));
}

async function main() {
    trace('main-start', {
        electron: process.versions.electron || '',
        node: process.versions.node || '',
        output: OUTPUT,
        screenshot: SCREENSHOT
    });
    const { tempRoot, copyStats, usingLiveProfile } = prepareTempProfile();
    trace('profile-ready', { tempRoot, usingLiveProfile, copyStats: { ...copyStats, skipped: (copyStats.skipped || []).length } });
    app.setPath('userData', tempRoot);
    app.commandLine.appendSwitch('disable-http-cache');
    app.commandLine.appendSwitch('disable-gpu');
    await app.whenReady();
    trace('app-ready');

    const partition = 'persist:jk-sistema-browser';
    const ses = session.fromPartition(partition);
    const extensionDirs = findAvantProExtensionDirs();
    const loaded = [];
    for (const extensionDir of extensionDirs) {
        try {
            const ext = await ses.loadExtension(extensionDir, { allowFileAccess: true });
            loaded.push({ id: ext.id, name: ext.name, path: ext.path, version: ext.manifest && ext.manifest.version });
            break;
        } catch (_err) {}
    }
    trace('extensions-loaded', { count: loaded.length, loaded });
    ses.on('will-download', (event, item) => {
        event.preventDefault();
        try { item.cancel(); } catch (_err) {}
    });

    const win = new BrowserWindow({
        show: process.env.FAVORITOS_SEARCH_SHOW === '1',
        width: 1400,
        height: 920,
        webPreferences: {
            session: ses,
            nodeIntegration: false,
            contextIsolation: true,
            backgroundThrottling: false
        }
    });
    const consoleMessages = [];
    win.webContents.on('console-message', (_event, level, message, line, sourceId) => {
        const text = String(message || '');
        if (/error|erro|avant|extension|failed|fail|mercado|favoritos/i.test(text + ' ' + sourceId)) {
            consoleMessages.push({ level, message: text.slice(0, 700), line, sourceId });
        }
    });

    const loadEventPromise = waitForLoad(win, Number(process.env.FAVORITOS_SEARCH_LOAD_TIMEOUT_MS) || 45000);
    trace('load-start', { url: SEARCH_URL });
    const loadUrlResult = await Promise.race([
        win.loadURL(SEARCH_URL).then(() => ({ loaded: true, source: 'loadURL' })).catch(err => ({
            loaded: false,
            source: 'loadURL',
            error: err && err.message ? err.message : String(err)
        })),
        wait(Number(process.env.FAVORITOS_SEARCH_LOAD_TIMEOUT_MS) || 45000).then(() => ({
            loaded: false,
            source: 'loadURL',
            timeout: true
        }))
    ]);
    const loadEvent = await Promise.race([
        loadEventPromise,
        wait(1000).then(() => null)
    ]);
    const load = loadEvent || loadUrlResult;
    trace('load-finished', { load });
    await wait(Number(process.env.FAVORITOS_SEARCH_WAIT_MS) || 15000);
    trace('post-load-wait-done');
    await win.webContents.executeJavaScript(`
        (async function () {
            var doc = document.scrollingElement || document.documentElement || document.body;
            for (var y = 0; y < Math.min((doc && doc.scrollHeight) || 0, 7000); y += 900) {
                window.scrollTo(0, y);
                if (doc) doc.scrollTop = y;
                await new Promise(function (resolve) { setTimeout(resolve, 250); });
            }
            window.scrollTo(0, 0);
            if (doc) doc.scrollTop = 0;
            return true;
        })();
    `, true).catch(() => null);
    trace('initial-scroll-done');
    await injectFavoritosScripts(win);
    trace('scripts-injected');
    const collectTimeoutMs = Math.max(
        45000,
        (Number(process.env.FAVORITOS_SEARCH_TEMPO_LIMITE_MS || '45000') || 45000) + 90000
    );
    const result = await Promise.race([
        collect(win),
        wait(collectTimeoutMs).then(() => ({
            error: `timeout_node_collect_${collectTimeoutMs}ms`
        }))
    ]);
    trace('collect-done', {
        error: result && result.error || '',
        baseTotal: result && result.base && result.base.total,
        controladaTotal: result && result.controlada && result.controlada.totalVisiveis,
        controladaResumo: result && result.controlada && result.controlada.resumo
    });
    const image = await win.webContents.capturePage().catch(() => null);
    trace('capture-done', { hasImage: !!(image && !image.isEmpty()) });
    if (image && !image.isEmpty()) {
        fs.mkdirSync(path.dirname(SCREENSHOT), { recursive: true });
        fs.writeFileSync(SCREENSHOT, image.toPNG());
    }
    writeResult({
        ok: !result.error,
        searchUrl: SEARCH_URL,
        usingLiveProfile: !!usingLiveProfile,
        tempRoot,
        partition,
        copyStats: {
            ...copyStats,
            skipped: copyStats.skipped.slice(0, 20)
        },
        loadedExtensions: loaded,
        load,
        consoleMessages: consoleMessages.slice(0, 120),
        screenshot: fs.existsSync(SCREENSHOT) ? SCREENSHOT : '',
        result
    });
    win.destroy();
    app.quit();
}

main().catch(err => {
    trace('main-error', { error: err && err.stack ? err.stack : String(err) });
    writeResult({
        ok: false,
        error: err && err.stack ? err.stack : String(err)
    });
    if (app && typeof app.quit === 'function') app.quit();
    process.exit(1);
});
