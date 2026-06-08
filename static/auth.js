// JK Sistema - Helpers de autenticação (JWT)
// Incluir este arquivo em todas as páginas via <script src="/auth.js"></script>

function obterToken() {
    return localStorage.getItem('access_token') || null;
}

function _jwtPayloadLocal(token) {
    try {
        const partes = String(token || '').split('.');
        if (partes.length < 2) return null;
        const base64 = partes[1].replace(/-/g, '+').replace(/_/g, '/');
        const json = decodeURIComponent(Array.from(atob(base64)).map(ch => {
            return '%' + ('00' + ch.charCodeAt(0).toString(16)).slice(-2);
        }).join(''));
        return JSON.parse(json);
    } catch (_e) {
        return null;
    }
}

function tokenSessaoExpirado() {
    const token = obterToken();
    if (!token) return false;
    const payload = _jwtPayloadLocal(token);
    const exp = Number(payload && payload.exp ? payload.exp : 0);
    return !!exp && Date.now() >= (exp * 1000);
}

function respostaIndicaSessaoExpirada(resp, payload) {
    if (!resp || resp.status !== 401) return false;
    const authHeader = String(resp.headers && resp.headers.get ? resp.headers.get('WWW-Authenticate') || '' : '');
    if (/bearer/i.test(authHeader)) return true;
    const detail = String(
        (payload && (payload.detail || payload.message || payload.error)) || ''
    ).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
    return (
        detail.includes('sessao expirada') ||
        detail.includes('token de autenticacao') ||
        detail.includes('faca o login novamente') ||
        detail.includes('login novamente')
    );
}

function redirecionarSessaoExpirada() {
    if (window.__jkSessionRedirecting) return;
    window.__jkSessionRedirecting = true;
    localStorage.removeItem('access_token');
    localStorage.removeItem('user_data');
    localStorage.removeItem('permissions');
    navegarComTransicao('/frontend_index.html');
}

(function initAuthFetchInterceptor() {
    if (window.__jkAuthFetchInterceptorInit || typeof window.fetch !== 'function') return;
    window.__jkAuthFetchInterceptorInit = true;
    const fetchOriginal = window.fetch.bind(window);
    window.fetch = async function jkAuthFetch(input, init) {
        const resp = await fetchOriginal(input, init);
        if (resp && resp.status === 401 && obterToken()) {
            let payload = null;
            try {
                payload = await resp.clone().json();
            } catch (_e) {}
            if (respostaIndicaSessaoExpirada(resp, payload)) {
                redirecionarSessaoExpirada();
            }
        }
        return resp;
    };
})();

const JK_TRANSITION_KEY = 'jk-page-transition';
const JK_TRANSITION_MS = 280;

function _isHtmlInterna(url) {
    try {
        const target = new URL(url, window.location.href);
        const sameOrigin = target.origin === window.location.origin;
        const isHtml = /\.html?$/i.test(target.pathname) || target.pathname === '/';
        return sameOrigin && isHtml;
    } catch (_e) {
        return false;
    }
}

function navegarComTransicao(url) {
    if (!_isHtmlInterna(url)) {
        window.location.href = url;
        return;
    }

    const target = new URL(url, window.location.href);
    if (document.body) {
        document.body.classList.add('jk-page-leaving');
    }
    sessionStorage.setItem(JK_TRANSITION_KEY, '1');
    setTimeout(() => {
        window.location.href = target.href;
    }, JK_TRANSITION_MS);
}

(function initElectronTabNavigationBridge() {
    let dentroDaCascaDeAbas = false;
    try {
        dentroDaCascaDeAbas = !!window.top && window.top !== window;
    } catch (_err) {
        dentroDaCascaDeAbas = true;
    }
    if (!dentroDaCascaDeAbas || window.__jkElectronTabNavigationBridgeInit) return;
    window.__jkElectronTabNavigationBridgeInit = true;

    document.documentElement.classList.add('jk-electron-tab-shell');

    const style = document.createElement('style');
    style.id = 'jk-electron-tab-shell-nav-style';
    style.textContent = `
        html.jk-electron-tab-shell .top-actions,
        html.jk-electron-tab-shell .nav-top-actions,
        html.jk-electron-tab-shell .nav-actions {
            display: none !important;
        }
        html.jk-electron-tab-shell [data-jk-shell-nav-hidden="1"] {
            display: none !important;
        }
        html.jk-electron-tab-shell [data-jk-shell-nav-container-empty="1"] {
            display: none !important;
        }
    `;
    (document.head || document.documentElement).appendChild(style);

    function textoNormalizado(el) {
        return String(el?.textContent || '')
            .normalize('NFD')
            .replace(/[\u0300-\u036f]/g, '')
            .replace(/[^\p{L}\p{N}\s]/gu, ' ')
            .replace(/\s+/g, ' ')
            .trim()
            .toLowerCase();
    }

    function apontaParaDashboard(el) {
        const href = String(el?.getAttribute?.('href') || '');
        const onclick = String(el?.getAttribute?.('onclick') || '');
        return /dashboard\.html/i.test(href) || /dashboard\.html/i.test(onclick);
    }

    function ehBotaoNavegacaoPrincipal(el) {
        if (!el || !el.matches || !el.matches('a, button')) return false;
        const texto = textoNormalizado(el);
        if (texto === 'home') return true;
        if (texto === 'voltar' && apontaParaDashboard(el)) return true;
        if (texto === 'voltar ao dashboard') return true;
        if (texto === 'voltar dashboard') return true;
        return false;
    }

    function ocultarNavegacaoInterna() {
        const paisParaRevisar = new Set();
        document.querySelectorAll('a, button').forEach((el) => {
            if (ehBotaoNavegacaoPrincipal(el)) {
                el.setAttribute('data-jk-shell-nav-hidden', '1');
                if (el.parentElement) paisParaRevisar.add(el.parentElement);
            }
        });

        document.querySelectorAll('.header-actions, .top-actions, .nav-top-actions, .nav-actions').forEach((container) => {
            paisParaRevisar.add(container);
        });

        paisParaRevisar.forEach((container) => {
            if (!container || container === document.body || container === document.documentElement) return;
            const filhosVisiveis = Array.from(container.children || []).filter((child) => {
                return child.getAttribute('data-jk-shell-nav-hidden') !== '1';
            });
            if (!filhosVisiveis.length) {
                container.setAttribute('data-jk-shell-nav-container-empty', '1');
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', ocultarNavegacaoInterna, { once: true });
    } else {
        ocultarNavegacaoInterna();
    }
    window.addEventListener('load', ocultarNavegacaoInterna);

    window.addEventListener('message', (event) => {
        const data = event && event.data ? event.data : {};
        if (!data || typeof data !== 'object' || data.channel !== 'jk-shell-history-back') return;
        try {
            if (window.history.length > 1) {
                window.history.back();
                return;
            }
        } catch (_err) {}
        navegarComTransicao(data.fallbackUrl || '/dashboard.html');
    });
})();

(function initElectronTabTitleSync() {
    if (window.__jkElectronTabTitleSyncInit) return;
    window.__jkElectronTabTitleSyncInit = true;

    function limparTituloAba(valor) {
        return String(valor || '')
            .replace(/\s+/g, ' ')
            .replace(/^[^\p{L}\p{N}]+/u, '')
            .replace(/\s+-\s+JK Sistema.*$/i, '')
            .trim()
            .slice(0, 90);
    }

    function obterTituloAbaAtual() {
        const titulo = limparTituloAba(document.title || '');
        if (titulo && !/^JK Sistema/i.test(titulo)) return titulo;
        const h1 = limparTituloAba(document.querySelector('h1')?.textContent || '');
        if (h1 && !/^JK Sistema/i.test(h1)) return h1;
        return titulo || h1 || '';
    }

    function enviarTituloAba() {
        try {
            if (!window.top || window.top === window || typeof window.top.postMessage !== 'function') return;
            const titulo = obterTituloAbaAtual();
            if (!titulo) return;
            window.top.postMessage({
                channel: 'jk-tab-title',
                payload: {
                    title: titulo,
                    url: window.location.href
                }
            }, '*');
        } catch (_err) {}
    }

    function avisarPaginaPronta() {
        try {
            if (!window.top || window.top === window || typeof window.top.postMessage !== 'function') return;
            window.top.postMessage({
                channel: 'jk-page-ready',
                payload: {
                    title: obterTituloAbaAtual(),
                    url: window.location.href
                }
            }, '*');
        } catch (_err) {}
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            enviarTituloAba();
            avisarPaginaPronta();
        }, { once: true });
    } else {
        enviarTituloAba();
        avisarPaginaPronta();
    }
    window.addEventListener('load', () => {
        enviarTituloAba();
        avisarPaginaPronta();
    });
    window.addEventListener('pageshow', avisarPaginaPronta);
    setTimeout(() => {
        enviarTituloAba();
        avisarPaginaPronta();
    }, 250);
    setTimeout(() => {
        enviarTituloAba();
        avisarPaginaPronta();
    }, 1000);

    try {
        const titleEl = document.querySelector('title');
        if (titleEl && typeof MutationObserver !== 'undefined') {
            new MutationObserver(enviarTituloAba).observe(titleEl, { childList: true, characterData: true, subtree: true });
        }
    } catch (_err) {}
})();

(function initElectronUpdateSaveBridge() {
    if (window.__jkElectronUpdateSaveBridgeInit) return;
    window.__jkElectronUpdateSaveBridgeInit = true;

    const DRAFT_PREFIX = 'jk-update-draft:';
    window.__jkBeforeUpdateSaveHandlers = window.__jkBeforeUpdateSaveHandlers || [];

    window.jkRegisterBeforeUpdateSaveHandler = function jkRegisterBeforeUpdateSaveHandler(handler) {
        if (typeof handler !== 'function') return () => {};
        window.__jkBeforeUpdateSaveHandlers.push(handler);
        return () => {
            window.__jkBeforeUpdateSaveHandlers = window.__jkBeforeUpdateSaveHandlers.filter(item => item !== handler);
        };
    };

    function draftKey() {
        return `${DRAFT_PREFIX}${window.location.pathname}`;
    }

    function cssValue(value) {
        if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(value);
        return String(value || '').replace(/\\/g, '\\\\').replace(/"/g, '\\"');
    }

    function inputCanBeDrafted(el) {
        if (!el || !el.matches || !el.matches('input, textarea, select')) return false;
        if (el.disabled || el.readOnly) return false;
        const type = String(el.type || '').toLowerCase();
        return !['password', 'hidden', 'file', 'button', 'submit', 'reset'].includes(type);
    }

    function saveFormDraftsForUpdate() {
        const fields = [];
        document.querySelectorAll('input, textarea, select').forEach((el) => {
            if (!inputCanBeDrafted(el)) return;
            const key = el.id || el.name;
            if (!key) return;
            const type = String(el.type || '').toLowerCase();
            if (type === 'checkbox' || type === 'radio') return;
            fields.push({
                id: el.id || '',
                name: el.name || '',
                tag: String(el.tagName || '').toLowerCase(),
                type,
                value: el.value
            });
        });
        if (fields.length) {
            localStorage.setItem(draftKey(), JSON.stringify({
                savedAt: Date.now(),
                url: window.location.href,
                fields
            }));
        }
        return fields.length;
    }

    function findDraftTarget(field) {
        if (field.id) {
            const byId = document.getElementById(field.id);
            if (byId) return byId;
        }
        if (field.name) {
            return document.querySelector(`[name="${cssValue(field.name)}"]`);
        }
        return null;
    }

    function restoreFormDraftsAfterUpdate() {
        let draft = null;
        try {
            draft = JSON.parse(localStorage.getItem(draftKey()) || 'null');
        } catch (_err) {
            draft = null;
        }
        if (!draft || !Array.isArray(draft.fields)) return;
        if (Date.now() - Number(draft.savedAt || 0) > 2 * 60 * 60 * 1000) {
            localStorage.removeItem(draftKey());
            return;
        }
        draft.fields.forEach((field) => {
            const el = findDraftTarget(field);
            if (!inputCanBeDrafted(el)) return;
            if (String(el.value || '') !== '') return;
            el.value = field.value || '';
            try {
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            } catch (_err) {}
        });
    }

    function addKnownSaveFunction(name, promises) {
        const fn = window[name];
        if (typeof fn !== 'function') return;
        try {
            const result = fn();
            if (result && typeof result.then === 'function') promises.push(result);
        } catch (_err) {}
    }

    async function preparePageForUpdate(payload = {}) {
        const promises = [];
        const waitUntil = (promise) => {
            if (promise && typeof promise.then === 'function') promises.push(promise);
        };
        const drafts = saveFormDraftsForUpdate();
        try {
            window.dispatchEvent(new CustomEvent('jk-before-update-save', {
                detail: {
                    reason: payload.reason || 'update-install',
                    waitUntil
                }
            }));
        } catch (_err) {}
        for (const handler of window.__jkBeforeUpdateSaveHandlers.slice()) {
            try {
                const result = handler(payload || {});
                if (result && typeof result.then === 'function') promises.push(result);
            } catch (_err) {}
        }
        [
            'salvarSessaoNavegadorElectron',
            'salvarCacheTelaVendas',
            'salvarNotasSkuTreinamentoAtual',
            'salvarLarguras',
            '_salvarLarguras',
            'salvarLargurasColunas',
            'salvarLargurasColunasPedidos',
            'saveColumnPrefs',
            'savePagePrefs',
            'saveColumnWidthPrefsFromDom',
            'saveColumnWidthPrefsFromDomRobust'
        ].forEach((name) => addKnownSaveFunction(name, promises));
        if (promises.length) {
            await Promise.allSettled(promises.map((promise) => Promise.resolve(promise)));
        }
        try {
            localStorage.setItem('jk-last-update-save', JSON.stringify({
                savedAt: Date.now(),
                url: window.location.href,
                title: document.title || ''
            }));
        } catch (_err) {}
        return { success: true, drafts, asyncHandlers: promises.length, url: window.location.href };
    }

    window.jkPreparePageForUpdate = preparePageForUpdate;

    window.addEventListener('message', (event) => {
        const data = event && event.data ? event.data : {};
        if (!data || typeof data !== 'object' || data.channel !== 'jk-prepare-for-update') return;
        const requestId = data.requestId || '';
        preparePageForUpdate(data.payload || {})
            .then((result) => {
                if (event.source && typeof event.source.postMessage === 'function') {
                    event.source.postMessage({ channel: 'jk-prepare-for-update-result', requestId, result }, '*');
                }
            })
            .catch((err) => {
                if (event.source && typeof event.source.postMessage === 'function') {
                    event.source.postMessage({
                        channel: 'jk-prepare-for-update-result',
                        requestId,
                        result: { success: false, error: err && err.message ? err.message : String(err) }
                    }, '*');
                }
            });
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', restoreFormDraftsAfterUpdate, { once: true });
    } else {
        restoreFormDraftsAfterUpdate();
    }
    window.addEventListener('load', () => setTimeout(restoreFormDraftsAfterUpdate, 300));
})();

function obterClientId() {
    const u = JSON.parse(localStorage.getItem('user_data') || 'null');
    return u && u.client_id ? u.client_id : null;
}

/**
 * Retorna o objeto de headers HTTP com Authorization: Bearer <token>.
 * Se `extra` for fornecido (objeto), as propriedades são mescladas.
 * Se não houver token, redireciona para o login.
 */
function obterAuthHeaders(extra) {
    const token = obterToken();
    const clientId = obterClientId();
    if (tokenSessaoExpirado()) {
        redirecionarSessaoExpirada();
        return {};
    }
    if (!token && !clientId) {
        window.location.href = '/frontend_index.html';
        return {};
    }
    const headers = token
        ? { 'Authorization': 'Bearer ' + token }
        : { 'X-Client-ID': clientId }; // Compatibilidade com backend legado sem JWT
    if (extra && typeof extra === 'object') {
        Object.assign(headers, extra);
    }
    return headers;
}

(function initMachinePresenceHeartbeat() {
    if (window.__jkMachinePresenceInit) return;
    window.__jkMachinePresenceInit = true;

    const FIREBASE_SDK_VERSION = '10.12.5';
    const FALLBACK_HEARTBEAT_INTERVAL_MS = 45 * 1000;
    const RTDB_SESSION_ENDPOINT = '/api/firebase/realtime-presence/session';
    let ultimoHeartbeat = 0;
    let timer = null;
    let emExecucao = false;
    let appVersionCache = null;
    let appVersionPromise = null;
    const rtdbState = {
        disabled: false,
        session: null,
        sessionLoadedAt: 0,
        modulesPromise: null,
        startPromise: null,
        modules: null,
        app: null,
        auth: null,
        db: null,
        connectionRef: null,
        lastStateRef: null,
        unsubscribeConnected: null,
        started: false,
        lastUsersPayload: null
    };

    function obterUserDataPresenca() {
        try {
            return JSON.parse(localStorage.getItem('user_data') || 'null') || null;
        } catch (_e) {
            return null;
        }
    }

    function obterMachineIdPresenca() {
        const userData = obterUserDataPresenca();
        return String((userData && userData.machine_id) || '').trim();
    }

    function extrairAppVersionUserAgent() {
        const match = String(navigator.userAgent || '').match(/\bjk-sistema-desktop\/([0-9A-Za-z._+-]+)/i);
        return match ? String(match[1] || '').trim() : '';
    }

    async function obterAppVersionPresenca() {
        if (appVersionCache !== null) return appVersionCache;
        if (appVersionPromise) return appVersionPromise;
        appVersionPromise = (async () => {
            let versao = '';
            try {
                if (window.electronAPI && typeof window.electronAPI.getAppVersion === 'function') {
                    versao = String(await window.electronAPI.getAppVersion() || '').trim();
                }
            } catch (_err) {
                versao = '';
            }
            appVersionCache = versao || extrairAppVersionUserAgent();
            appVersionPromise = null;
            return appVersionCache;
        })();
        return appVersionPromise;
    }

    function normalizarTimestampSegundos(valor) {
        const numero = Number(valor || 0);
        if (!Number.isFinite(numero) || numero <= 0) return 0;
        return numero > 100000000000 ? Math.floor(numero / 1000) : Math.floor(numero);
    }

    function dataLocalDeTimestamp(segundos) {
        const ts = normalizarTimestampSegundos(segundos);
        if (!ts) return '';
        const data = new Date(ts * 1000);
        const pad = (v) => String(v).padStart(2, '0');
        return `${data.getFullYear()}-${pad(data.getMonth() + 1)}-${pad(data.getDate())} ${pad(data.getHours())}:${pad(data.getMinutes())}:${pad(data.getSeconds())}`;
    }

    function criarConnectionId() {
        const bytes = new Uint8Array(8);
        try {
            crypto.getRandomValues(bytes);
        } catch (_err) {
            for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
        }
        const aleatorio = Array.from(bytes).map(b => b.toString(16).padStart(2, '0')).join('');
        return `${Date.now().toString(36)}-${aleatorio}`;
    }

    function paginaAtualPresenca() {
        return `${window.location.pathname || ''}${window.location.search || ''}`.slice(0, 180);
    }

    async function carregarFirebaseModules() {
        if (!rtdbState.modulesPromise) {
            const base = `https://www.gstatic.com/firebasejs/${FIREBASE_SDK_VERSION}`;
            rtdbState.modulesPromise = Promise.all([
                import(`${base}/firebase-app.js`),
                import(`${base}/firebase-auth.js`),
                import(`${base}/firebase-database.js`)
            ]).then(([app, auth, database]) => ({ app, auth, database }));
        }
        rtdbState.modules = await rtdbState.modulesPromise;
        return rtdbState.modules;
    }

    async function obterSessaoRtdb(force = false) {
        if (rtdbState.disabled && !force) return null;
        if (!force && rtdbState.session && Date.now() - rtdbState.sessionLoadedAt < 45 * 60 * 1000) {
            return rtdbState.session;
        }
        const machineId = obterMachineIdPresenca();
        const url = `${RTDB_SESSION_ENDPOINT}?machine_id=${encodeURIComponent(machineId)}`;
        const resp = await fetch(url, {
            method: 'GET',
            headers: obterAuthHeaders(),
            cache: 'no-store'
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.success === false) {
            throw new Error(data.detail || data.message || 'Nao foi possivel preparar a presenca em tempo real.');
        }
        if (!data.enabled) {
            rtdbState.disabled = true;
            return null;
        }
        rtdbState.session = data;
        rtdbState.sessionLoadedAt = Date.now();
        return rtdbState.session;
    }

    async function prepararRtdb() {
        const session = await obterSessaoRtdb();
        if (!session) return null;
        const modules = await carregarFirebaseModules();
        const appName = `jk-presence-${String(session.clientKey || 'default').replace(/[^A-Za-z0-9_-]/g, '_').slice(0, 80)}`;
        let app = null;
        try {
            app = modules.app.getApp(appName);
        } catch (_err) {
            app = modules.app.initializeApp(session.firebaseConfig, appName);
        }
        const auth = modules.auth.getAuth(app);
        await modules.auth.signInWithCustomToken(auth, session.customToken);
        rtdbState.app = app;
        rtdbState.auth = auth;
        rtdbState.db = modules.database.getDatabase(app);
        return { session, modules, db: rtdbState.db };
    }

    async function montarPayloadPresencaRtdb(online) {
        const session = rtdbState.session || {};
        const machine = session.machine || {};
        const modules = rtdbState.modules || await carregarFirebaseModules();
        const appVersion = await obterAppVersionPresenca();
        const machineId = String(machine.machine_id || obterMachineIdPresenca() || '').trim();
        const machineKey = chaveFisicaMaquinaPresenca({
            machine_id: machineId,
            machine_key: session.machineKey,
            label: machine.label
        });
        return {
            username: String(session.username || '').trim().toLowerCase(),
            client_id: String(session.client_id || 'default').trim() || 'default',
            machine_id: machineId,
            machine_key: machineKey,
            label: String(machine.label || machineId || 'Maquina').slice(0, 120),
            page: paginaAtualPresenca(),
            app_version: String(appVersion || '').slice(0, 60),
            online: !!online,
            client_updated_at: Date.now(),
            last_seen_ts: modules.database.serverTimestamp()
        };
    }

    function normalizarChaveFisicaPresenca(valor) {
        const texto = String(valor || '').trim();
        if (!texto) return '';
        const macMatch = texto.match(/\bmac\s*:\s*([0-9a-f]{2}(?:[:-][0-9a-f]{2}){5}|[0-9a-f]{12})\b/i);
        if (macMatch) {
            return `mac:${macMatch[1].toLowerCase().replace(/[^0-9a-f]/g, '')}`;
        }
        const pcMatch = texto.match(/\bpc\s*:\s*([^|]+)/i);
        if (pcMatch) {
            const pc = pcMatch[1].trim().toLowerCase().replace(/[^a-z0-9]+/g, '');
            if (pc) return `pc:${pc}`;
        }
        const normalizado = texto.toLowerCase().replace(/[^a-z0-9]+/g, '');
        return normalizado ? `id:${normalizado}` : '';
    }

    function chaveFisicaMaquinaPresenca(machine) {
        if (!machine || typeof machine !== 'object') return '';
        const porMachineId = normalizarChaveFisicaPresenca(machine.machine_id);
        if (porMachineId) return porMachineId;
        const porLabel = normalizarChaveFisicaPresenca(machine.label);
        if (porLabel) return porLabel;
        const key = String(machine.machine_key || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '');
        return key ? `key:${key}` : '';
    }

    function deduplicarMaquinasPresenca(maquinas) {
        const mapa = new Map();
        (Array.isArray(maquinas) ? maquinas : []).forEach((machine) => {
            if (!machine || typeof machine !== 'object') return;
            const key = chaveFisicaMaquinaPresenca(machine);
            if (!key) return;
            const atual = mapa.get(key);
            const item = Object.assign({}, machine, {
                machine_key: key,
                online: machine.online !== false
            });
            if (!atual) {
                mapa.set(key, item);
                return;
            }
            const itemOnline = item.online !== false;
            const atualOnline = atual.online !== false;
            const itemTs = Number(item.last_seen_ts || 0);
            const atualTs = Number(atual.last_seen_ts || 0);
            if ((itemOnline && !atualOnline) || (itemOnline === atualOnline && itemTs >= atualTs)) {
                mapa.set(key, Object.assign({}, atual, item, { current: !!(atual.current || item.current) }));
            } else if (item.current) {
                atual.current = true;
            }
        });
        return Array.from(mapa.values())
            .sort((a, b) => Number(b.last_seen_ts || 0) - Number(a.last_seen_ts || 0));
    }

    function montarUsuarioRtdb(userNode, fallback, currentMachineId) {
        const now = Math.floor(Date.now() / 1000);
        const node = userNode && typeof userNode === 'object' ? userNode : {};
        const connections = node.connections && typeof node.connections === 'object' ? node.connections : {};
        const currentMachineKey = chaveFisicaMaquinaPresenca({ machine_id: currentMachineId });
        const maquinas = deduplicarMaquinasPresenca(Object.values(connections)
            .filter(item => item && typeof item === 'object' && item.online !== false)
            .map((item) => {
                const lastSeen = normalizarTimestampSegundos(item.last_seen_ts || item.client_updated_at);
                const machineId = String(item.machine_id || '').trim();
                const machineKey = chaveFisicaMaquinaPresenca({
                    machine_id: machineId,
                    machine_key: item.machine_key,
                    label: item.label
                });
                return {
                    username: String(item.username || '').trim().toLowerCase(),
                    client_id: String(item.client_id || '').trim(),
                    machine_id: machineId,
                    machine_key: machineKey,
                    label: String(item.label || machineId || 'Maquina'),
                    page: String(item.page || ''),
                    app_version: String(item.app_version || ''),
                    last_seen_at: dataLocalDeTimestamp(lastSeen),
                    last_seen_ts: lastSeen,
                    seconds_since_seen: lastSeen ? Math.max(0, now - lastSeen) : 0,
                    online: true,
                    current: !!(currentMachineKey && machineKey === currentMachineKey)
                };
            }));
        const lastState = node.lastState && typeof node.lastState === 'object' ? node.lastState : {};
        const lastSeen = normalizarTimestampSegundos(lastState.last_seen_ts || node.lastOnline || (maquinas[0] && maquinas[0].last_seen_ts));
        const username = String(
            fallback && fallback.username ||
            lastState.username ||
            (maquinas[0] && maquinas[0].username) ||
            node.username ||
            ''
        ).trim().toLowerCase();
        const clientId = String(
            fallback && fallback.client_id ||
            lastState.client_id ||
            (maquinas[0] && maquinas[0].client_id) ||
            node.client_id ||
            'default'
        ).trim() || 'default';
        return {
            username,
            name: username,
            client_id: clientId,
            online: maquinas.length > 0,
            online_count: maquinas.filter(machine => machine.online !== false).length,
            machines: maquinas.slice(0, 10),
            all_recent_machines: maquinas.slice(0, 20),
            last_seen_at: dataLocalDeTimestamp(lastSeen),
            seconds_since_seen: lastSeen ? Math.max(0, now - lastSeen) : null
        };
    }

    function montarPayloadUsuariosRtdb(rawUsers, session) {
        const usersRoot = rawUsers && typeof rawUsers === 'object' ? rawUsers : {};
        const users = Object.values(usersRoot)
            .map(node => montarUsuarioRtdb(node, null, ''))
            .filter(user => user.username);
        const maquinasUnicas = new Set();
        users.forEach(user => {
            (user.machines || []).forEach(machine => {
                const key = chaveFisicaMaquinaPresenca(machine);
                if (key) maquinasUnicas.add(key);
            });
        });
        users.sort((a, b) => (Number(b.online) - Number(a.online)) || String(a.username).localeCompare(String(b.username)));
        return {
            success: true,
            backend: 'firebase-rtdb',
            realtime: true,
            client_id: session && session.client_id,
            users,
            online_users: users.filter(user => user.online).length,
            online_machines: maquinasUnicas.size || users.reduce((total, user) => total + Number(user.online_count || 0), 0),
            online_timeout_seconds: 0
        };
    }

    async function atualizarConexaoRtdb() {
        if (!rtdbState.connectionRef || !rtdbState.lastStateRef || !rtdbState.modules) return null;
        const payload = await montarPayloadPresencaRtdb(true);
        await rtdbState.modules.database.set(rtdbState.connectionRef, payload);
        await rtdbState.modules.database.set(rtdbState.lastStateRef, payload);
        return { success: true, backend: 'firebase-rtdb' };
    }

    async function iniciarPresencaRtdb() {
        if (rtdbState.disabled) return null;
        if (rtdbState.startPromise) return rtdbState.startPromise;
        rtdbState.startPromise = (async () => {
            const ctx = await prepararRtdb();
            if (!ctx) return null;
            const { session, modules, db } = ctx;
            const userBasePath = `${session.rootPath}/users/${session.userKey}`;
            const connectedRef = modules.database.ref(db, '.info/connected');
            if (rtdbState.unsubscribeConnected) rtdbState.unsubscribeConnected();
            rtdbState.unsubscribeConnected = modules.database.onValue(connectedRef, async (snap) => {
                if (snap.val() !== true) return;
                try {
                    const connectionId = criarConnectionId();
                    const connectionRef = modules.database.ref(db, `${userBasePath}/connections/${connectionId}`);
                    const lastOnlineRef = modules.database.ref(db, `${userBasePath}/lastOnline`);
                    const lastStateRef = modules.database.ref(db, `${userBasePath}/lastState`);
                    rtdbState.connectionRef = connectionRef;
                    rtdbState.lastStateRef = lastStateRef;
                    const onlinePayload = await montarPayloadPresencaRtdb(true);
                    const offlinePayload = Object.assign({}, onlinePayload, {
                        online: false,
                        last_seen_ts: modules.database.serverTimestamp()
                    });
                    await modules.database.onDisconnect(connectionRef).remove();
                    await modules.database.onDisconnect(lastOnlineRef).set(modules.database.serverTimestamp());
                    await modules.database.onDisconnect(lastStateRef).set(offlinePayload);
                    await modules.database.set(lastStateRef, onlinePayload);
                    await modules.database.set(connectionRef, onlinePayload);
                    rtdbState.started = true;
                    if (timer) {
                        clearInterval(timer);
                        timer = null;
                    }
                } catch (_err) {
                    rtdbState.disabled = true;
                    iniciarFallbackHeartbeat();
                }
            }, () => {
                rtdbState.disabled = true;
                iniciarFallbackHeartbeat();
            });
            return { success: true, backend: 'firebase-rtdb' };
        })().catch((err) => {
            rtdbState.disabled = true;
            iniciarFallbackHeartbeat();
            throw err;
        }).finally(() => {
            rtdbState.startPromise = null;
        });
        return rtdbState.startPromise;
    }

    async function desconectarPresencaRtdb() {
        try {
            if (rtdbState.connectionRef && rtdbState.modules) {
                await rtdbState.modules.database.remove(rtdbState.connectionRef);
            }
        } catch (_err) {}
    }

    async function enviarHeartbeatBackend(motivo) {
        if (emExecucao || !obterToken() || tokenSessaoExpirado()) return null;
        if (/frontend_index\.html$/i.test(window.location.pathname || '')) return null;
        const agora = Date.now();
        if (motivo !== 'manual' && agora - ultimoHeartbeat < FALLBACK_HEARTBEAT_INTERVAL_MS) return null;
        ultimoHeartbeat = agora;
        emExecucao = true;
        try {
            const payload = {
                machine_id: obterMachineIdPresenca(),
                page: `${window.location.pathname || ''}${window.location.search || ''}`
            };
            const appVersion = await obterAppVersionPresenca();
            if (appVersion) payload.app_version = appVersion;
            const resp = await fetch('/api/user/machines/heartbeat', {
                method: 'POST',
                headers: obterAuthHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(payload),
                keepalive: motivo === 'hidden'
            });
            return await resp.json().catch(() => ({}));
        } catch (_err) {
            return null;
        } finally {
            emExecucao = false;
        }
    }

    async function enviarHeartbeat(motivo) {
        if (!obterToken() || tokenSessaoExpirado()) return null;
        if (/frontend_index\.html$/i.test(window.location.pathname || '')) return null;
        if (!rtdbState.disabled) {
            try {
                if (rtdbState.started) return await atualizarConexaoRtdb();
                const result = await iniciarPresencaRtdb();
                if (result) return result;
            } catch (_err) {}
        }
        return enviarHeartbeatBackend(motivo);
    }

    async function buscarMaquinasOnlineBackend() {
        if (!obterToken() || tokenSessaoExpirado()) return null;
        const machineId = obterMachineIdPresenca();
        const url = `/api/user/machines/online?machine_id=${encodeURIComponent(machineId)}`;
        const resp = await fetch(url, {
            method: 'GET',
            headers: obterAuthHeaders(),
            cache: 'no-store'
        });
        return await resp.json().catch(() => ({}));
    }

    async function buscarMaquinasOnlineRtdb() {
        const ctx = await prepararRtdb();
        if (!ctx) return null;
        const { session, modules, db } = ctx;
        const userRef = modules.database.ref(db, `${session.rootPath}/users/${session.userKey}`);
        const snap = await modules.database.get(userRef);
        const machineId = String((session.machine && session.machine.machine_id) || obterMachineIdPresenca() || '').trim();
        const user = montarUsuarioRtdb(snap.val(), session, machineId);
        return {
            success: true,
            backend: 'firebase-rtdb',
            realtime: true,
            username: session.username,
            client_id: session.client_id,
            online_count: Number(user.online_count || 0),
            machines: user.machines || [],
            all_recent_machines: user.all_recent_machines || [],
            online_timeout_seconds: 0
        };
    }

    async function buscarMaquinasOnline() {
        if (!obterToken() || tokenSessaoExpirado()) return null;
        if (!rtdbState.disabled) {
            try {
                const data = await buscarMaquinasOnlineRtdb();
                if (data) return data;
            } catch (_err) {}
        }
        return buscarMaquinasOnlineBackend();
    }

    function chaveUsuarioPresenca(user) {
        const username = String(user && user.username || '').trim().toLowerCase();
        const clientId = String(user && user.client_id || 'default').trim() || 'default';
        return username ? `${username}|${clientId}` : '';
    }

    function maquinasUnicasPresenca(users) {
        const maquinas = new Set();
        (Array.isArray(users) ? users : []).forEach((user) => {
            const lista = []
                .concat(Array.isArray(user && user.machines) ? user.machines : [])
                .concat(Array.isArray(user && user.all_recent_machines) ? user.all_recent_machines : []);
            lista.forEach((machine) => {
                if (!machine || machine.online === false) return;
                const key = chaveFisicaMaquinaPresenca(machine);
                if (key) maquinas.add(key);
            });
        });
        return maquinas;
    }

    function mesclarUsuariosOnline(backendPayload, realtimePayload) {
        const backend = backendPayload && typeof backendPayload === 'object' ? backendPayload : {};
        const realtime = realtimePayload && typeof realtimePayload === 'object' ? realtimePayload : {};
        const baseUsers = Array.isArray(backend.users) ? backend.users : [];
        const realtimeUsers = Array.isArray(realtime.users) ? realtime.users : [];
        if (!baseUsers.length && !realtimeUsers.length) return null;

        const realtimePorUsuario = new Map();
        realtimeUsers.forEach((user) => {
            const key = chaveUsuarioPresenca(user);
            if (key) realtimePorUsuario.set(key, user);
        });

        const vistos = new Set();
        const users = baseUsers.map((user) => {
            const key = chaveUsuarioPresenca(user);
            const presenca = realtimePorUsuario.get(key);
            if (key) vistos.add(key);
            if (!presenca) return user;
            const machines = Array.isArray(presenca.machines) && presenca.machines.length
                ? presenca.machines
                : (Array.isArray(user.machines) ? user.machines : []);
            const allRecent = Array.isArray(presenca.all_recent_machines) && presenca.all_recent_machines.length
                ? presenca.all_recent_machines
                : (Array.isArray(user.all_recent_machines) ? user.all_recent_machines : machines);
            const machinesUnicas = deduplicarMaquinasPresenca(machines);
            const recentesUnicas = deduplicarMaquinasPresenca(allRecent);
            return {
                ...user,
                online: !!(presenca.online || user.online),
                online_count: machinesUnicas.filter(machine => machine.online !== false).length || Number(presenca.online_count || user.online_count || 0),
                machines: machinesUnicas,
                all_recent_machines: recentesUnicas.length ? recentesUnicas : machinesUnicas,
                last_seen_at: presenca.last_seen_at || user.last_seen_at || '',
                seconds_since_seen: presenca.seconds_since_seen !== undefined && presenca.seconds_since_seen !== null
                    ? presenca.seconds_since_seen
                    : user.seconds_since_seen
            };
        });

        realtimeUsers.forEach((user) => {
            const key = chaveUsuarioPresenca(user);
            if (!key || vistos.has(key)) return;
            users.push(user);
            vistos.add(key);
        });

        users.sort((a, b) => (Number(b.online) - Number(a.online)) || String(a.name || a.username || '').localeCompare(String(b.name || b.username || '')));
        const onlineUsers = users.filter(user => user && user.online);
        const maquinasOnline = maquinasUnicasPresenca(onlineUsers);
        return {
            ...backend,
            success: true,
            backend: realtimeUsers.length ? `firebase-rtdb+${backend.backend || 'backend'}` : (backend.backend || 'backend'),
            realtime: !!(realtime.realtime || realtimeUsers.length),
            client_id: realtime.client_id || backend.client_id,
            users,
            online_users: onlineUsers.length,
            online_machines: maquinasOnline.size || onlineUsers.reduce((total, user) => total + Number(user.online_count || 0), 0),
            online_timeout_seconds: Number(realtime.online_timeout_seconds || backend.online_timeout_seconds || 0)
        };
    }

    async function buscarUsuariosOnlineBackend() {
        if (!obterToken() || tokenSessaoExpirado()) return null;
        const resp = await fetch('/api/admin/users/online', {
            method: 'GET',
            headers: obterAuthHeaders(),
            cache: 'no-store'
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.success === false) return null;
        return data;
    }

    async function buscarUsuariosOnlineRtdb() {
        const ctx = await prepararRtdb();
        if (!ctx || !(ctx.session && ctx.session.admin)) return null;
        if (rtdbState.lastUsersPayload && Array.isArray(rtdbState.lastUsersPayload.users)) {
            return rtdbState.lastUsersPayload;
        }
        const usersRef = ctx.modules.database.ref(ctx.db, `${ctx.session.rootPath}/users`);
        const snap = await ctx.modules.database.get(usersRef);
        const payload = montarPayloadUsuariosRtdb(snap.val(), ctx.session);
        rtdbState.lastUsersPayload = payload;
        return payload;
    }

    async function buscarUsuariosOnline() {
        if (!obterToken() || tokenSessaoExpirado()) return null;
        const realtimePromise = !rtdbState.disabled
            ? buscarUsuariosOnlineRtdb().catch(() => null)
            : Promise.resolve(null);
        const backendPromise = buscarUsuariosOnlineBackend().catch(() => null);
        const [realtimeData, backendData] = await Promise.all([realtimePromise, backendPromise]);
        return mesclarUsuariosOnline(backendData, realtimeData);
    }

    async function assinarPresencaUsuarios(onUpdate, onError) {
        const ctx = await prepararRtdb();
        if (!ctx || !(ctx.session && ctx.session.admin)) {
            throw new Error('Presenca em tempo real indisponivel para este usuario.');
        }
        const usersRef = ctx.modules.database.ref(ctx.db, `${ctx.session.rootPath}/users`);
        return ctx.modules.database.onValue(usersRef, (snap) => {
            const payload = montarPayloadUsuariosRtdb(snap.val(), ctx.session);
            rtdbState.lastUsersPayload = payload;
            if (typeof onUpdate === 'function') onUpdate(payload);
        }, (error) => {
            if (typeof onError === 'function') onError(error);
        });
    }

    function iniciarFallbackHeartbeat() {
        if (!obterToken() || tokenSessaoExpirado()) return;
        enviarHeartbeatBackend('inicio');
        if (!timer) {
            timer = setInterval(() => enviarHeartbeatBackend('intervalo'), FALLBACK_HEARTBEAT_INTERVAL_MS);
        }
    }

    window.jkEnviarHeartbeatMaquina = enviarHeartbeat;
    window.jkBuscarMaquinasOnline = buscarMaquinasOnline;
    window.jkBuscarUsuariosOnline = buscarUsuariosOnline;
    window.jkAssinarPresencaUsuarios = assinarPresencaUsuarios;
    window.jkPresencaRealtimeEstado = () => ({
        enabled: !rtdbState.disabled,
        connected: !!rtdbState.started,
        backend: rtdbState.started ? 'firebase-rtdb' : 'fallback'
    });

    function iniciar() {
        if (!obterToken() || tokenSessaoExpirado()) return;
        iniciarPresencaRtdb()
            .then((result) => { if (!result) iniciarFallbackHeartbeat(); })
            .catch(() => iniciarFallbackHeartbeat());
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', iniciar, { once: true });
    } else {
        setTimeout(iniciar, 500);
    }
    window.addEventListener('focus', () => iniciarPresencaRtdb().catch(() => {}));
    window.addEventListener('pagehide', () => { desconectarPresencaRtdb(); });
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') iniciarPresencaRtdb().catch(() => {});
        if (document.visibilityState === 'hidden' && rtdbState.disabled) enviarHeartbeatBackend('hidden');
    });
})();

(function initDriveBackupAutomatico() {
    if (window.__jkDriveBackupAutoInit) return;
    window.__jkDriveBackupAutoInit = true;

    let executando = false;
    let ultimaExecucao = 0;

    async function executarBackupSeMudou(motivo) {
        if (executando || !obterToken() || tokenSessaoExpirado()) return null;
        if (/frontend_index\.html$/i.test(window.location.pathname || '')) return null;
        if (motivo !== 'manual' && document.visibilityState === 'hidden') return null;
        const agora = Date.now();
        if (motivo !== 'manual' && agora - ultimaExecucao < 30000) return null;
        ultimaExecucao = agora;
        executando = true;
        try {
            const resp = await fetch('/api/drive-sync/backup-if-changed', {
                method: 'POST',
                headers: obterAuthHeaders()
            });
            const data = await resp.json().catch(() => ({}));
            if (resp.ok && data && data.action && !['synced', 'not_linked', 'empty'].includes(data.action)) {
                console.info('[Drive Sync]', data.message || data.action);
            }
            return data;
        } catch (err) {
            console.warn('[Drive Sync]', err);
            return null;
        } finally {
            executando = false;
        }
    }

    window.jkDriveBackupNow = () => executarBackupSeMudou('manual');
})();

(function initSharedSyncAutoPull() {
    if (window.__jkSharedSyncAutoPullInit) return;
    window.__jkSharedSyncAutoPullInit = true;

    let executandoPull = false;
    let executandoPush = false;
    let ultimaPull = 0;
    let ultimaPush = 0;

    function telaSeguraParaRestaurar() {
        const path = String(window.location.pathname || '').toLowerCase();
        return !path || path === '/' || /dashboard\.html$|configuracoes\.html$|admin_usuarios\.html$/.test(path);
    }

    function machineIdAtualSync() {
        try {
            const data = JSON.parse(localStorage.getItem('user_data') || '{}') || {};
            return String(data.machine_id || '').trim();
        } catch (_err) {
            return '';
        }
    }

    async function executarAutoPull(motivo) {
        if (executandoPull || !obterToken() || tokenSessaoExpirado()) return null;
        if (!telaSeguraParaRestaurar()) return null;
        if (motivo !== 'manual' && document.visibilityState === 'hidden') return null;
        const agora = Date.now();
        if (motivo !== 'manual' && agora - ultimaPull < 5 * 60 * 1000) return null;
        ultimaPull = agora;
        executandoPull = true;
        try {
            const resp = await fetch('/api/shared-sync/auto-pull', {
                method: 'POST',
                headers: obterAuthHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ machine_id: machineIdAtualSync() })
            });
            const data = await resp.json().catch(() => ({}));
            if (resp.ok && data && Array.isArray(data.results) && data.results.length) {
                console.info('[Shared Sync] Dados compartilhados restaurados:', data.results);
            }
            return data;
        } catch (err) {
            console.warn('[Shared Sync]', err);
            return null;
        } finally {
            executandoPull = false;
        }
    }

    async function executarAutoPush(motivo) {
        if (executandoPush || !obterToken() || tokenSessaoExpirado()) return null;
        if (!telaSeguraParaRestaurar()) return null;
        if (motivo !== 'manual' && document.visibilityState === 'hidden') return null;
        const agora = Date.now();
        if (motivo !== 'manual' && agora - ultimaPush < 5 * 60 * 1000) return null;
        ultimaPush = agora;
        executandoPush = true;
        try {
            const resp = await fetch('/api/shared-sync/user-shares/auto-push', {
                method: 'POST',
                headers: obterAuthHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ machine_id: machineIdAtualSync() })
            });
            const data = await resp.json().catch(() => ({}));
            if (resp.ok && data && Array.isArray(data.results) && data.results.length) {
                console.info('[Shared Sync] Dados compartilhados enviados:', data.results);
            }
            return data;
        } catch (err) {
            console.warn('[Shared Sync Push]', err);
            return null;
        } finally {
            executandoPush = false;
        }
    }

    window.jkSharedSyncAutoPullNow = () => executarAutoPull('manual');
    window.jkSharedSyncAutoPushNow = () => executarAutoPush('manual');
})();

(function initMachineSharedSyncAuto() {
    if (window.__jkMachineSharedSyncAutoInit) return;
    window.__jkMachineSharedSyncAutoInit = true;

    let executando = false;
    let ultimaExecucao = 0;

    function telaSeguraParaSincronizar() {
        const path = String(window.location.pathname || '').toLowerCase();
        return !path || path === '/' || /dashboard\.html$|configuracoes\.html$|admin_usuarios\.html$/.test(path);
    }

    function machineIdAtualSync() {
        try {
            const data = JSON.parse(localStorage.getItem('user_data') || '{}') || {};
            return String(data.machine_id || '').trim();
        } catch (_err) {
            return '';
        }
    }

    async function executarMachineSync(motivo) {
        if (executando || !obterToken() || tokenSessaoExpirado()) return null;
        if (/frontend_index\.html$/i.test(window.location.pathname || '')) return null;
        if (!telaSeguraParaSincronizar()) return null;
        if (motivo !== 'manual' && document.visibilityState === 'hidden') return null;
        const agora = Date.now();
        if (motivo !== 'manual' && agora - ultimaExecucao < 5 * 60 * 1000) return null;
        ultimaExecucao = agora;
        executando = true;
        try {
            const resp = await fetch('/api/shared-sync/machine-sync/auto', {
                method: 'POST',
                headers: obterAuthHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ machine_id: machineIdAtualSync() })
            });
            const data = await resp.json().catch(() => ({}));
            if (resp.ok && data && Array.isArray(data.results) && data.results.length) {
                console.info('[Machine Sync] Minhas maquinas sincronizadas:', data.results);
            }
            return data;
        } catch (err) {
            console.warn('[Machine Sync]', err);
            return null;
        } finally {
            executando = false;
        }
    }

    window.jkMachineSyncNow = () => executarMachineSync('manual');
})();

/** Remove dados de sessão e redireciona para login. */
function encerrarSessao() {
    localStorage.removeItem('access_token');
    localStorage.removeItem('user_data');
    localStorage.removeItem('permissions');
    navegarComTransicao('/frontend_index.html');
}

/** Verifica se há token na sessão; redireciona se não houver. */
function verificarSessao() {
    if (tokenSessaoExpirado()) {
        redirecionarSessaoExpirada();
        return false;
    }
    if (!obterToken() && !obterClientId()) {
        window.location.href = '/frontend_index.html';
        return false;
    }
    return true;
}

(function initGlobalIaRagAutoIndex() {
    if (window.__jkIaRagAutoIndexInit) return;
    window.__jkIaRagAutoIndexInit = true;

    const STORAGE_KEY = 'jk-ia-rag-autoindex-v1';
    const SUCESSO_INTERVALO_MS = 6 * 60 * 60 * 1000; // 6h
    const TENTATIVA_MIN_INTERVALO_MS = 5 * 60 * 1000; // 5min
    const INDICATOR_REFRESH_MS = 60 * 1000;
    let emExecucao = false;
    let indicadorTimer = null;
    let modalDiagnostico = null;

    function usuarioAdmin() {
        try {
            const permissoes = JSON.parse(localStorage.getItem('permissions') || '{}');
            return permissoes && (permissoes.full === true || permissoes.admin_usuarios === true);
        } catch (_e) {
            return false;
        }
    }

    function formatarTempoDecorrido(timestamp) {
        const t = Number(timestamp || 0);
        if (!t) return '';
        const deltaMs = Math.max(0, Date.now() - t);
        const totalMin = Math.floor(deltaMs / 60000);
        if (totalMin <= 0) return 'agora';
        if (totalMin < 60) return `ha ${totalMin} min`;
        const horas = Math.floor(totalMin / 60);
        const minutos = totalMin % 60;
        if (horas < 24) {
            return minutos > 0 ? `ha ${horas}h ${minutos}min` : `ha ${horas}h`;
        }
        const dias = Math.floor(horas / 24);
        return `ha ${dias}d`;
    }

    function garantirIndicador() {
        if (!usuarioAdmin()) return null;

        if (!document.getElementById('jk-ia-rag-indicador-style')) {
            const style = document.createElement('style');
            style.id = 'jk-ia-rag-indicador-style';
            style.textContent = `
                #jk-ia-rag-indicador {
                    position: fixed;
                    left: 14px;
                    bottom: 14px;
                    z-index: 99996;
                    max-width: min(360px, calc(100vw - 28px));
                    padding: 8px 10px;
                    border-radius: 10px;
                    border: 1px solid rgba(123, 207, 255, 0.36);
                    background: linear-gradient(180deg, rgba(14, 29, 52, 0.96), rgba(10, 21, 38, 0.98));
                    color: #d9ecff;
                    font: 600 12px "Segoe UI", Tahoma, sans-serif;
                    line-height: 1.35;
                    box-shadow: 0 10px 20px rgba(0, 0, 0, 0.35);
                    letter-spacing: 0.1px;
                    user-select: none;
                    cursor: pointer;
                }
                #jk-ia-rag-indicador:hover {
                    filter: brightness(1.05);
                }
                #jk-ia-rag-indicador.running {
                    border-color: rgba(140, 215, 255, 0.6);
                }
                #jk-ia-rag-indicador.ok {
                    border-color: rgba(108, 224, 154, 0.55);
                }
                #jk-ia-rag-indicador.warn {
                    border-color: rgba(255, 169, 109, 0.62);
                }
                #jk-ia-rag-modal {
                    position: fixed;
                    inset: 0;
                    z-index: 99997;
                    display: none;
                    align-items: center;
                    justify-content: center;
                    background: rgba(2, 6, 12, 0.66);
                    backdrop-filter: blur(3px);
                }
                #jk-ia-rag-modal.open {
                    display: flex;
                }
                #jk-ia-rag-modal .jk-ia-rag-card {
                    width: min(560px, calc(100vw - 28px));
                    max-height: min(78vh, 760px);
                    overflow: auto;
                    border-radius: 12px;
                    border: 1px solid rgba(123, 207, 255, 0.38);
                    background: linear-gradient(180deg, rgba(12, 24, 44, 0.98), rgba(8, 17, 33, 0.98));
                    color: #d9ecff;
                    box-shadow: 0 22px 44px rgba(0, 0, 0, 0.52);
                    padding: 14px;
                    font: 600 12px "Segoe UI", Tahoma, sans-serif;
                }
                #jk-ia-rag-modal .jk-ia-rag-head {
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    gap: 8px;
                    margin-bottom: 12px;
                }
                #jk-ia-rag-modal .jk-ia-rag-title {
                    font-size: 14px;
                    font-weight: 800;
                    color: #cbe8ff;
                }
                #jk-ia-rag-modal .jk-ia-rag-close {
                    width: 28px;
                    height: 28px;
                    border: 1px solid rgba(123, 207, 255, 0.36);
                    border-radius: 8px;
                    background: rgba(14, 29, 52, 0.9);
                    color: #d9ecff;
                    cursor: pointer;
                    font-weight: 800;
                }
                #jk-ia-rag-modal .jk-ia-rag-grid {
                    display: grid;
                    grid-template-columns: 1fr 1fr;
                    gap: 8px;
                }
                #jk-ia-rag-modal .jk-ia-rag-item {
                    border: 1px solid rgba(123, 207, 255, 0.2);
                    border-radius: 8px;
                    padding: 8px;
                    background: rgba(255, 255, 255, 0.03);
                }
                #jk-ia-rag-modal .jk-ia-rag-item .k {
                    color: #9fd4ff;
                    font-weight: 700;
                    margin-bottom: 3px;
                }
                #jk-ia-rag-modal .jk-ia-rag-item .v {
                    color: #e4f2ff;
                    word-break: break-word;
                    white-space: pre-wrap;
                }
                #jk-ia-rag-modal .jk-ia-rag-foot {
                    margin-top: 10px;
                    color: #9ab8d8;
                    font-size: 11px;
                }
                @media (max-width: 760px) {
                    #jk-ia-rag-modal .jk-ia-rag-grid {
                        grid-template-columns: 1fr;
                    }
                }
            `;
            document.head.appendChild(style);
        }

        let el = document.getElementById('jk-ia-rag-indicador');
        if (!el) {
            el = document.createElement('div');
            el.id = 'jk-ia-rag-indicador';
            el.title = 'Clique para abrir diagnostico IA/RAG';
            el.addEventListener('click', () => {
                abrirDiagnosticoRag();
            });
            (document.body || document.documentElement).appendChild(el);
        }
        return el;
    }

    function boolTexto(v) {
        return v ? 'sim' : 'nao';
    }

    function textoCurto(v, limite = 280) {
        const s = String(v || '').trim();
        return s.length > limite ? `${s.slice(0, limite)}...` : s;
    }

    function garantirModalDiagnostico() {
        if (!usuarioAdmin()) return null;
        if (modalDiagnostico) return modalDiagnostico;

        const el = document.createElement('div');
        el.id = 'jk-ia-rag-modal';
        el.innerHTML = `
            <div class="jk-ia-rag-card" role="dialog" aria-modal="true" aria-label="Diagnostico IA RAG">
                <div class="jk-ia-rag-head">
                    <div class="jk-ia-rag-title">Diagnostico IA/RAG</div>
                    <button type="button" class="jk-ia-rag-close" aria-label="Fechar">x</button>
                </div>
                <div id="jk-ia-rag-modal-body">Carregando status...</div>
                <div class="jk-ia-rag-foot">Fonte: /api/ia/rag/status</div>
            </div>
        `;
        el.addEventListener('click', (event) => {
            if (event.target === el) {
                el.classList.remove('open');
            }
        });
        el.querySelector('.jk-ia-rag-close')?.addEventListener('click', () => {
            el.classList.remove('open');
        });
        window.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                el.classList.remove('open');
            }
        });
        (document.body || document.documentElement).appendChild(el);
        modalDiagnostico = el;
        return el;
    }

    async function abrirDiagnosticoRag() {
        const modal = garantirModalDiagnostico();
        if (!modal) return;

        modal.classList.add('open');
        const body = modal.querySelector('#jk-ia-rag-modal-body');
        if (body) body.textContent = 'Carregando status...';

        try {
            const resp = await fetch('/api/ia/rag/status', {
                headers: obterAuthHeaders(),
                cache: 'no-store'
            });
            let data = null;
            try {
                data = await resp.json();
            } catch (_e) {
                data = null;
            }

            if (!resp.ok) {
                const detalhe = textoCurto(data && data.detail ? data.detail : resp.statusText || 'falha ao consultar status');
                if (body) body.innerHTML = `<div class="jk-ia-rag-item"><div class="k">Erro</div><div class="v">${detalhe}</div></div>`;
                return;
            }

            if (!data || typeof data !== 'object') {
                if (body) body.innerHTML = `<div class="jk-ia-rag-item"><div class="k">Erro</div><div class="v">Resposta invalida do servidor.</div></div>`;
                return;
            }

            const itens = [
                ['Client ID', data.client_id || '-'],
                ['RAG habilitado', boolTexto(data.enabled)],
                ['Backend RAG', data.backend || '-'],
                ['Modo configurado', data.backend_configurado || '-'],
                ['Motor local', data.local_engine || '-'],
                ['Local ok', data.local_ok == null ? '-' : boolTexto(data.local_ok)],
                ['Docs locais', data.local_documentos != null ? String(data.local_documentos) : '-'],
                ['DB local MB', data.local_db_mb != null ? String(data.local_db_mb) : '-'],
                ['Postgres configurado', boolTexto(data.postgres_configurado)],
                ['Postgres ok', boolTexto(data.postgres_ok)],
                ['pgvector ok', boolTexto(data.pgvector_ok)],
                ['Ollama ok', boolTexto(data.ollama_ok)],
                ['Ollama base URL', data.ollama_base_url || '-'],
                ['Modelo embedding', data.ollama_embedding_model || '-'],
                ['Top K', data.top_k != null ? String(data.top_k) : '-'],
                ['psycopg instalado', boolTexto(data.psycopg_instalado)],
                ['Erro local', textoCurto(data.local_error || '-')],
                ['Erro Ollama', textoCurto(data.ollama_error || '-')],
                ['Erro Postgres', textoCurto(data.postgres_error || '-')]
            ];

            if (body) {
                body.innerHTML = `<div class="jk-ia-rag-grid">${itens.map(([k, v]) => `<div class="jk-ia-rag-item"><div class="k">${k}</div><div class="v">${String(v || '-')}</div></div>`).join('')}</div>`;
            }
        } catch (_e) {
            if (body) body.innerHTML = `<div class="jk-ia-rag-item"><div class="k">Erro</div><div class="v">Falha de conexao ao consultar /api/ia/rag/status.</div></div>`;
        }
    }

    function atualizarIndicador() {
        document.getElementById('jk-ia-rag-indicador')?.remove();
        return;

        const el = garantirIndicador();
        if (!el) return;

        const estado = lerEstado();
        const ultimoOk = Number(estado.last_ok_at || 0);
        const ultimaFalha = Number(estado.last_fail_at || 0);

        el.classList.remove('running', 'ok', 'warn');

        if (emExecucao) {
            el.classList.add('running');
            el.textContent = 'IA RAG: indexando em background...';
            return;
        }

        if (ultimoOk > 0) {
            el.classList.add('ok');
            el.textContent = `IA RAG: indexado ${formatarTempoDecorrido(ultimoOk)}.`;
            return;
        }

        if (ultimaFalha > 0) {
            el.classList.add('warn');
            el.textContent = `IA RAG: ultima tentativa falhou ${formatarTempoDecorrido(ultimaFalha)}.`;
            return;
        }

        el.classList.add('warn');
        el.textContent = 'IA RAG: aguardando primeira indexacao automatica.';
    }

    function lerEstado() {
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            return raw ? JSON.parse(raw) : {};
        } catch (_e) {
            return {};
        }
    }

    function salvarEstado(patch) {
        try {
            const atual = lerEstado();
            localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...atual, ...patch }));
        } catch (_e) {
            // silencioso
        }
    }

    function deveExecutar(clientId, agora) {
        const estado = lerEstado();
        if (!estado || estado.client_id !== clientId) return true;

        const ultimoSucesso = Number(estado.last_ok_at || 0);
        if (ultimoSucesso > 0 && (agora - ultimoSucesso) < SUCESSO_INTERVALO_MS) {
            return false;
        }

        const ultimaTentativa = Number(estado.last_try_at || 0);
        if (ultimaTentativa > 0 && (agora - ultimaTentativa) < TENTATIVA_MIN_INTERVALO_MS) {
            return false;
        }

        return true;
    }

    async function dispararAutoIndex() {
        if (!obterToken() && !obterClientId()) return;

        const clientId = obterClientId() || 'desconhecido';
        const agora = Date.now();
        if (!deveExecutar(clientId, agora)) return;

        emExecucao = true;
        salvarEstado({ client_id: clientId, last_try_at: agora });
        atualizarIndicador();

        try {
            const resp = await fetch('/api/ia/rag/reindexar', {
                method: 'POST',
                headers: {
                    ...obterAuthHeaders(),
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ force: false })
            });

            if (resp.ok) {
                salvarEstado({
                    client_id: clientId,
                    last_ok_at: Date.now(),
                    last_fail_at: 0,
                    last_fail_msg: ''
                });
            } else {
                let erroMsg = '';
                try {
                    const data = await resp.json();
                    erroMsg = String(data && data.detail ? data.detail : '').slice(0, 180);
                } catch (_e) {
                    erroMsg = resp.statusText || '';
                }
                salvarEstado({
                    client_id: clientId,
                    last_fail_at: Date.now(),
                    last_fail_msg: erroMsg
                });
            }
        } catch (_e) {
            salvarEstado({
                client_id: clientId,
                last_fail_at: Date.now(),
                last_fail_msg: 'falha de conexao'
            });
        } finally {
            emExecucao = false;
            atualizarIndicador();
        }
    }

    const iniciar = () => {
        // Aguarda a página estabilizar para não competir com chamadas críticas de carregamento.
        atualizarIndicador();
        if (!indicadorTimer) {
            indicadorTimer = setInterval(atualizarIndicador, INDICATOR_REFRESH_MS);
        }
        window.addEventListener('storage', (event) => {
            if (!event || event.key === STORAGE_KEY || event.key === 'permissions') {
                atualizarIndicador();
            }
        });
        setTimeout(dispararAutoIndex, 3500);
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', iniciar, { once: true });
    } else {
        iniciar();
    }
})();

(function ensureGlobalTableColumns() {
    if (window.__jkTableColumnsLoaderInit) return;
    window.__jkTableColumnsLoaderInit = true;

    function carregarScript() {
        if (window.JKTableColumns || document.querySelector('script[data-jk-table-columns="1"]')) return;
        const script = document.createElement('script');
        script.src = '/table_columns.js';
        script.async = false;
        script.dataset.jkTableColumns = '1';
        document.head.appendChild(script);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', carregarScript, { once: true });
    } else {
        carregarScript();
    }
})();

(function initGlobalPageTransitions() {
    if (window.__jkPageTransitionsInit) return;
    window.__jkPageTransitionsInit = true;

    const style = document.createElement('style');
    style.id = 'jk-page-transitions-style';
    style.textContent = `
        .jk-page-transition-overlay {
            position: fixed;
            inset: 0;
            background: #000;
            opacity: 0;
            pointer-events: none;
            transition: opacity ${JK_TRANSITION_MS}ms ease;
            z-index: 2147483647;
        }
        body.jk-page-preload .jk-page-transition-overlay {
            opacity: 1;
        }
        body.jk-page-leaving .jk-page-transition-overlay {
            opacity: 1;
        }
        body.jk-page-preload {
            overflow: hidden;
        }
        body.jk-page-preload > *:not(.jk-page-transition-overlay):not(.dashboard-handoff) {
            opacity: 0;
            transform: translateY(10px);
            filter: blur(6px);
            transition: opacity ${JK_TRANSITION_MS}ms ease, transform ${JK_TRANSITION_MS}ms ease, filter ${JK_TRANSITION_MS}ms ease;
        }
        body.jk-page-ready > *:not(.jk-page-transition-overlay):not(.dashboard-handoff) {
            opacity: 1;
            transform: none;
            filter: none;
            transition: opacity ${JK_TRANSITION_MS}ms ease, transform ${JK_TRANSITION_MS}ms ease, filter ${JK_TRANSITION_MS}ms ease;
        }
    `;
    document.head.appendChild(style);

    const garantirPaginaVisivel = () => {
        if (!document.body) return;
        document.body.classList.remove('jk-page-preload', 'jk-page-leaving');
        document.body.classList.add('jk-page-ready');
    };

    const prepararEntrada = () => {
        if (!document.body) return;
        if (document.body.classList.contains('dashboard-preload')) {
            sessionStorage.removeItem(JK_TRANSITION_KEY);
            return;
        }

        if (!document.querySelector('.jk-page-transition-overlay')) {
            const overlay = document.createElement('div');
            overlay.className = 'jk-page-transition-overlay';
            document.body.appendChild(overlay);
        }

        const veioTransicao = sessionStorage.getItem(JK_TRANSITION_KEY) === '1';
        if (!veioTransicao) {
            garantirPaginaVisivel();
            return;
        }

        sessionStorage.removeItem(JK_TRANSITION_KEY);
        document.body.classList.add('jk-page-preload');
        requestAnimationFrame(() => {
            requestAnimationFrame(garantirPaginaVisivel);
        });
    };

    const interceptarLinks = () => {
        document.addEventListener('click', (event) => {
            const link = event.target && event.target.closest ? event.target.closest('a[href]') : null;
            if (!link) return;

            const href = link.getAttribute('href');
            if (!href || href.startsWith('#')) return;
            if (link.target && link.target !== '_self') return;
            if (link.hasAttribute('download')) return;
            if (link.dataset && link.dataset.jkNoTransition === '1') return;
            if (event.defaultPrevented) return;
            if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
            if (!_isHtmlInterna(href)) return;

            event.preventDefault();
            navegarComTransicao(link.href);
        }, true);
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            prepararEntrada();
            interceptarLinks();
        }, { once: true });
    } else {
        prepararEntrada();
        interceptarLinks();
    }

    window.addEventListener('pageshow', () => {
        setTimeout(garantirPaginaVisivel, 0);
    });
    window.addEventListener('load', () => {
        setTimeout(garantirPaginaVisivel, 120);
    }, { once: true });
    setTimeout(garantirPaginaVisivel, Math.max(900, JK_TRANSITION_MS * 4));

    window.navegarComTransicao = navegarComTransicao;
})();

(function initGlobalNavigationCards() {
    if (window.__jkNavigationCardsInit) return;
    window.__jkNavigationCardsInit = true;

    const style = document.createElement('style');
    style.id = 'jk-global-navigation-cards-style';
    style.textContent = `
        .jk-topbar-actions-enhanced {
            display: flex !important;
            align-items: center;
            justify-content: flex-end;
            gap: 10px;
            flex-wrap: wrap;
        }
        .jk-nav-card-group {
            position: fixed !important;
            top: 16px;
            right: 18px;
            z-index: 1600;
            display: inline-flex !important;
            align-items: center;
            justify-content: flex-end;
            gap: 10px;
            flex-wrap: wrap;
            max-width: calc(100vw - 24px);
        }
        .jk-nav-card,
        .jk-nav-card-group .back-btn,
        .jk-nav-card-group .back-main-btn,
        .jk-nav-card-group .btn-back,
        .jk-nav-card-group .btn-voltar,
        .jk-nav-card-group #btnVoltar,
        .jk-nav-card-group #btnVoltarVendas {
            position: static !important;
            top: auto !important;
            right: auto !important;
            left: auto !important;
            margin: 0 !important;
            display: inline-flex !important;
            align-items: center;
            justify-content: center;
            gap: 8px;
            min-height: 46px;
            padding: 10px 16px !important;
            border-radius: 14px !important;
            border: 1px solid rgba(110, 189, 255, 0.34) !important;
            background: linear-gradient(180deg, rgba(25, 48, 83, 0.98), rgba(17, 34, 61, 1)) !important;
            color: #eef6ff !important;
            text-decoration: none !important;
            font-weight: 800 !important;
            line-height: 1.1;
            box-shadow: 0 12px 24px rgba(8, 17, 34, 0.24);
            cursor: pointer;
            transition: transform 0.18s ease, filter 0.18s ease, box-shadow 0.18s ease;
        }
        .jk-nav-card:hover,
        .jk-nav-card-group .back-btn:hover,
        .jk-nav-card-group .back-main-btn:hover,
        .jk-nav-card-group .btn-back:hover,
        .jk-nav-card-group .btn-voltar:hover,
        .jk-nav-card-group #btnVoltar:hover,
        .jk-nav-card-group #btnVoltarVendas:hover {
            transform: translateY(-1px);
            filter: brightness(1.04);
            box-shadow: 0 14px 28px rgba(8, 17, 34, 0.3);
        }
        .jk-home-card {
            background: linear-gradient(180deg, rgba(22, 110, 114, 0.98), rgba(15, 85, 94, 1)) !important;
            border-color: rgba(101, 232, 229, 0.38) !important;
        }
        .jk-import-card {
            background: linear-gradient(180deg, rgba(85, 61, 153, 0.98), rgba(58, 38, 115, 1)) !important;
            border-color: rgba(190, 163, 255, 0.4) !important;
        }
        @media (max-width: 760px) {
            .jk-topbar-actions-enhanced,
            .jk-nav-card-group {
                width: auto;
                max-width: calc(100vw - 20px);
                right: 10px;
                top: 10px;
                justify-content: flex-end;
            }
            .jk-nav-card,
            .jk-nav-card-group .back-btn,
            .jk-nav-card-group .back-main-btn,
            .jk-nav-card-group .btn-back,
            .jk-nav-card-group .btn-voltar,
            .jk-nav-card-group #btnVoltar,
            .jk-nav-card-group #btnVoltarVendas {
                min-height: 42px;
                padding: 8px 12px !important;
                font-size: 0.95rem !important;
            }
        }
    `;
    document.head.appendChild(style);

    function normalizarTexto(value) {
        return String(value || '')
            .normalize('NFD')
            .replace(/[\u0300-\u036f]/g, '')
            .trim()
            .toLowerCase();
    }

    function adicionarCardHome() {
        const candidatos = Array.from(document.querySelectorAll('a.back-btn, a.back-main-btn, button.back-btn, button.back-main-btn, button.btn-back, a.btn-voltar, button.btn-voltar, #btnVoltar, #btnVoltarVendas'));

        const obterGrupo = () => {
            let group = document.querySelector('.jk-nav-card-group');
            if (!group) {
                group = document.createElement('div');
                group.className = 'jk-nav-card-group';
                (document.body || document.documentElement).appendChild(group);
            }
            return group;
        };

        candidatos.forEach((backButton) => {
            if (!/voltar/.test(normalizarTexto(backButton.textContent))) return;

            const parent = backButton.parentElement || document.body;
            const group = obterGrupo();

            if (parent.classList.contains('topbar-actions') || parent.classList.contains('header-actions') || parent.classList.contains('header')) {
                parent.classList.add('jk-topbar-actions-enhanced');
            }

            backButton.classList.add('jk-nav-card', 'jk-back-card');

            let homeLink = Array.from(parent.querySelectorAll('a, button')).find((el) => {
                if (!el || el === backButton) return false;
                return /home/.test(normalizarTexto(el.textContent));
            }) || group.querySelector('.jk-home-card');

            if (!homeLink) {
                homeLink = document.createElement('a');
                homeLink.href = '/dashboard.html';
                homeLink.textContent = '🏠 Home';
            }

            homeLink.classList.add('jk-nav-card', 'jk-home-card');

            if (homeLink.parentElement !== group) group.appendChild(homeLink);
            if (backButton.parentElement !== group) group.appendChild(backButton);

            const duplicadosHome = Array.from(group.querySelectorAll('a, button')).filter((el) => el !== homeLink && /home/.test(normalizarTexto(el.textContent)));
            duplicadosHome.forEach((dup) => dup.remove());
        });
    }

    function transformarLinksDeImportacao() {
        const links = Array.from(document.querySelectorAll('a[href]'));
        links.forEach((link) => {
            const texto = normalizarTexto(link.textContent);
            if (!texto.startsWith('ir para importa')) return;
            link.classList.add('jk-nav-card', 'jk-import-card');
            if (!String(link.textContent || '').includes('📥')) {
                link.textContent = `📥 ${String(link.textContent || '').trim()}`;
            }
            const parent = link.parentElement;
            if (parent && (parent.classList.contains('topbar-actions') || parent.classList.contains('header-actions') || parent.classList.contains('header'))) {
                parent.classList.add('jk-topbar-actions-enhanced');
            }
        });
    }

    function init() {
        adicionarCardHome();
        transformarLinksDeImportacao();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
})();

(function initGlobalVendasSyncMonitor() {
    if (window.__jkSyncMonitorInit) return;
    window.__jkSyncMonitorInit = true;

    const JK_SYNC_WIDGET_MIN_KEY = 'jk-sync-widget-minimized';

    function obterSyncWidgetMinimizado() {
        try {
            return localStorage.getItem(JK_SYNC_WIDGET_MIN_KEY) === '1';
        } catch (_e) {
            return false;
        }
    }

    function salvarSyncWidgetMinimizado(minimizado) {
        try {
            localStorage.setItem(JK_SYNC_WIDGET_MIN_KEY, minimizado ? '1' : '0');
        } catch (_e) {
            // silencioso
        }
    }

    function formatarDataBr(isoDate) {
        if (!isoDate || typeof isoDate !== 'string') return '-';
        const base = isoDate.slice(0, 10);
        if (!/^\d{4}-\d{2}-\d{2}$/.test(base)) return isoDate;
        const [ano, mes, dia] = base.split('-');
        return `${dia}/${mes}/${ano}`;
    }

    function garantirWidget() {
        let el = document.getElementById('jk-global-sync-widget');
        if (el) return el;

        const style = document.createElement('style');
        style.id = 'jk-global-sync-style';
        style.textContent = `
            #jk-global-sync-widget {
                position: fixed;
                right: 16px;
                bottom: 16px;
                width: min(420px, calc(100vw - 32px));
                z-index: 99999;
                background: #0a1428;
                border: 1px solid #2f7ed3;
                border-radius: 10px;
                box-shadow: 0 10px 28px rgba(0,0,0,0.45);
                color: #d7ebff;
                font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
                padding: 10px 12px;
                display: none;
                overflow: hidden;
            }
            #jk-global-sync-widget .jk-head {
                position: relative;
                display: flex;
                align-items: center;
                gap: 8px;
                margin-bottom: 6px;
                padding-right: 34px;
            }
            #jk-global-sync-widget .jk-title {
                flex: 1 1 auto;
                min-width: 0;
                font-size: 13px;
                font-weight: 700;
                color: #9bd1ff;
                margin-bottom: 0;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }
            #jk-global-sync-widget .jk-toggle {
                position: absolute;
                top: 0;
                right: 0;
                width: 26px;
                height: 26px;
                border-radius: 8px;
                border: 1px solid #3b5f8e;
                background: #10213d;
                color: #d7ebff;
                font-size: 16px;
                font-weight: 800;
                line-height: 1;
                cursor: pointer;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                padding: 0;
                z-index: 2;
            }
            #jk-global-sync-widget .jk-toggle:hover {
                filter: brightness(1.08);
            }
            #jk-global-sync-widget .jk-widget-body {
                display: block;
            }
            #jk-global-sync-widget.is-collapsed {
                width: 46px;
                height: 46px;
                min-width: 46px;
                padding: 0;
                border-radius: 999px;
                display: flex !important;
                align-items: center;
                justify-content: center;
                background: linear-gradient(180deg, #0f1e39, #091427);
                cursor: pointer;
            }
            #jk-global-sync-widget.is-collapsed .jk-widget-body {
                display: none;
            }
            #jk-global-sync-widget.is-collapsed .jk-head {
                margin: 0;
                padding-right: 0;
                justify-content: center;
            }
            #jk-global-sync-widget.is-collapsed .jk-title {
                display: none;
            }
            #jk-global-sync-widget.is-collapsed .jk-toggle {
                position: static;
                width: 34px;
                height: 34px;
                min-width: 34px;
                border-radius: 999px;
                font-size: 0;
                border-color: rgba(91, 208, 255, 0.65);
                background: radial-gradient(circle at 30% 30%, #6ecbff, #2f7ed3 58%, #17345e 100%);
                box-shadow: 0 0 0 2px rgba(18, 39, 71, 0.45);
                animation: jk-sync-spin 1.1s linear infinite;
            }
            #jk-global-sync-widget.is-collapsed .jk-toggle::before {
                content: '';
                width: 10px;
                height: 10px;
                border-radius: 999px;
                background: rgba(255,255,255,0.92);
                display: block;
            }
            @keyframes jk-sync-spin {
                from { transform: rotate(0deg); }
                to { transform: rotate(360deg); }
            }
            #jk-global-sync-widget .jk-meta {
                font-size: 12px;
                color: #c8e2ff;
                margin-bottom: 8px;
            }
            #jk-global-sync-widget .jk-progress-wrap {
                height: 8px;
                background: #2a3140;
                border-radius: 999px;
                overflow: hidden;
                margin-bottom: 6px;
            }
            #jk-global-sync-widget .jk-progress-bar {
                height: 8px;
                width: 0%;
                background: linear-gradient(90deg, #4facfe, #2b86d9);
                transition: width .4s ease;
            }
            #jk-global-sync-widget .jk-row {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 8px;
                font-size: 12px;
                color: #a9d5ff;
            }
            #jk-global-sync-widget .jk-link {
                color: #9bd1ff;
                text-decoration: underline;
                cursor: pointer;
                white-space: nowrap;
            }
        `;
        document.head.appendChild(style);

        el = document.createElement('div');
        el.id = 'jk-global-sync-widget';
        el.innerHTML = `
            <div class="jk-head">
                <div class="jk-title">Sincronização de vendas em andamento</div>
                <button type="button" class="jk-toggle" aria-label="Minimizar status" title="Minimizar status">−</button>
            </div>
            <div class="jk-widget-body">
                <div class="jk-meta" id="jk-sync-meta">Período: -</div>
                <div class="jk-progress-wrap"><div class="jk-progress-bar" id="jk-sync-bar"></div></div>
                <div class="jk-row">
                    <div id="jk-sync-status">Preparando...</div>
                    <a class="jk-link" href="/vendas.html">Abrir vendas</a>
                </div>
            </div>
        `;

        function aplicarEstadoMinimizado(minimizado) {
            el.classList.toggle('is-collapsed', !!minimizado);
            const btn = el.querySelector('.jk-toggle');
            const titleEl = el.querySelector('.jk-title');
            if (btn) {
                btn.textContent = minimizado ? '+' : '−';
                btn.title = minimizado ? 'Expandir status' : 'Minimizar status';
                btn.setAttribute('aria-label', btn.title);
            }
            if (titleEl) {
                const fullTitle = titleEl.dataset.fullTitle || titleEl.textContent || 'Sincronização de vendas';
                titleEl.dataset.fullTitle = fullTitle;
                titleEl.textContent = minimizado ? 'Status de vendas' : fullTitle;
            }
        }

        const btnToggle = el.querySelector('.jk-toggle');
        if (btnToggle) {
            btnToggle.addEventListener('click', (event) => {
                event.stopPropagation();
                const minimizado = !el.classList.contains('is-collapsed');
                aplicarEstadoMinimizado(minimizado);
                salvarSyncWidgetMinimizado(minimizado);
            });
        }

        el.addEventListener('click', () => {
            if (!el.classList.contains('is-collapsed')) return;
            aplicarEstadoMinimizado(false);
            salvarSyncWidgetMinimizado(false);
        });

        const sincronizarEstadoGlobal = () => {
            aplicarEstadoMinimizado(obterSyncWidgetMinimizado());
        };

        window.addEventListener('pageshow', sincronizarEstadoGlobal);
        window.addEventListener('storage', (event) => {
            if (!event || event.key === JK_SYNC_WIDGET_MIN_KEY) {
                sincronizarEstadoGlobal();
            }
        });
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) sincronizarEstadoGlobal();
        });

        aplicarEstadoMinimizado(obterSyncWidgetMinimizado());
        document.body.appendChild(el);
        return el;
    }

    function corrigirTextoMojibakeGlobal(valor) {
        let texto = valor === null || valor === undefined ? '' : String(valor);
        const pareceMojibake = (txt) => /Ã[\u0080-\u00bf]|Ãƒ|Ã‚|Â[\u0080-\u00bf]|â[€œš–—™“”€¢]|ï¿½|�/.test(txt);
        const pares = [
            ['Ã¢Å“â€¦', '✅'], ['âœ…', '✅'], ['Ã¢ÂÅ’', '❌'], ['âŒ', '❌'],
            ['Ã¢Å¡Â Ã¯Â¸Â', '⚠️'], ['âš ï¸', '⚠️'], ['â€”', '-'], ['â€“', '-'],
            ['â€œ', '"'], ['â€', '"'], ['â€˜', "'"], ['â€™', "'"]
        ];
        const substituir = (txt) => pares.reduce((acc, [errado, correto]) => acc.split(errado).join(correto), txt);
        texto = substituir(texto);
        if (!pareceMojibake(texto) || typeof TextDecoder !== 'function') return texto;
        const mapaCp1252 = new Map([
            ['€', 0x80], ['‚', 0x82], ['ƒ', 0x83], ['„', 0x84], ['…', 0x85], ['†', 0x86], ['‡', 0x87],
            ['ˆ', 0x88], ['‰', 0x89], ['Š', 0x8a], ['‹', 0x8b], ['Œ', 0x8c], ['Ž', 0x8e],
            ['‘', 0x91], ['’', 0x92], ['“', 0x93], ['”', 0x94], ['•', 0x95], ['–', 0x96], ['—', 0x97],
            ['˜', 0x98], ['™', 0x99], ['š', 0x9a], ['›', 0x9b], ['œ', 0x9c], ['ž', 0x9e], ['Ÿ', 0x9f]
        ]);
        const score = (txt) => (txt.match(/Ã|Â|â|ï¿½|�/g) || []).length;
        const replacements = (txt) => (txt.match(/�/g) || []).length;
        const decoder = new TextDecoder('utf-8');
        for (let i = 0; i < 3 && pareceMojibake(texto); i += 1) {
            const bytes = Uint8Array.from(Array.from(texto, ch => mapaCp1252.get(ch) ?? (ch.charCodeAt(0) & 0xff)));
            const corrigido = substituir(decoder.decode(bytes));
            if (!corrigido || corrigido === texto) break;
            if (score(corrigido) > score(texto) || replacements(corrigido) > replacements(texto)) break;
            texto = corrigido;
        }
        return substituir(texto);
    }

    function renderWidget(payload) {
        const widget = garantirWidget();
        const progress = payload && payload.progress ? payload.progress : null;
        const active = !!(payload && payload.active);
        const meta = payload && payload.sync_meta ? payload.sync_meta : null;

        if (!active) {
            widget.style.display = 'none';
            return;
        }

        const percent = Math.max(0, Math.min(100, Number(progress && progress.percentual ? progress.percentual : 0)));
        const loja = meta && meta.loja ? meta.loja : '-';
        const inicio = formatarDataBr(meta && meta.data_inicio ? meta.data_inicio : '');
        const fim = formatarDataBr(meta && meta.data_fim ? meta.data_fim : '');
        const etapa = corrigirTextoMojibakeGlobal(progress && progress.etapa ? progress.etapa : 'Preparando');
        const mensagem = corrigirTextoMojibakeGlobal(progress && progress.mensagem ? progress.mensagem : 'Sincronizando...');

        const titleEl = widget.querySelector('.jk-title');
        const fullTitle = `Sincronização de vendas (${loja})`;
        titleEl.dataset.fullTitle = fullTitle;
        titleEl.textContent = widget.classList.contains('is-collapsed') ? 'Status de vendas' : fullTitle;
        widget.querySelector('#jk-sync-meta').textContent = `Período: ${inicio} até ${fim}`;
        widget.querySelector('#jk-sync-bar').style.width = `${percent}%`;
        widget.querySelector('#jk-sync-status').textContent = `${percent}% • ${etapa} • ${mensagem}`;
        widget.style.display = 'block';
    }

    const SYNC_MONITOR_INTERVAL_MS = 10000;

    async function atualizarSyncGlobal() {
        if (document.visibilityState === 'hidden') return;
        if (!obterToken() && !obterClientId()) return;
        try {
            const permissoes = JSON.parse(localStorage.getItem('permissions') || '{}');
            if (!(permissoes.full === true || permissoes.vendas === true)) {
                if (window.__jkSyncMonitorTimer) {
                    clearInterval(window.__jkSyncMonitorTimer);
                    window.__jkSyncMonitorTimer = null;
                }
                return;
            }
        } catch (_e) {
            // Se permissões locais estiverem inválidas, deixa o backend decidir.
        }
        try {
            const resp = await fetch('/api/vendas/sync/progress', {
                headers: obterAuthHeaders(),
                cache: 'no-store'
            });
            if (resp.status === 401 || resp.status === 403) {
                if (window.__jkSyncMonitorTimer) {
                    clearInterval(window.__jkSyncMonitorTimer);
                    window.__jkSyncMonitorTimer = null;
                }
                return;
            }
            if (!resp.ok) return;
            const payload = await resp.json();
            renderWidget(payload);
        } catch (_e) {
            // Silencioso: monitor global não deve quebrar páginas.
        }
    }

    const iniciar = () => {
        atualizarSyncGlobal();
        window.__jkSyncMonitorTimer = setInterval(atualizarSyncGlobal, SYNC_MONITOR_INTERVAL_MS);
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') atualizarSyncGlobal();
        });
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', iniciar, { once: true });
    } else {
        iniciar();
    }
})();

(function initGlobalAiSidebar() {
    if (window.__jkGlobalAiSidebarInit) return;
    window.__jkGlobalAiSidebarInit = true;

    const STORAGE_KEY = 'jk-global-ai-sidebar-open';

    function obterAberto() {
        try {
            return localStorage.getItem(STORAGE_KEY) === '1';
        } catch (_e) {
            return false;
        }
    }

    function salvarAberto(aberto) {
        try {
            localStorage.setItem(STORAGE_KEY, aberto ? '1' : '0');
        } catch (_e) {
            // silencioso
        }
    }

    function obterTituloPagina() {
        const h1 = document.querySelector('h1');
        const titulo = (h1 && h1.textContent ? h1.textContent : document.title || 'Sistema').trim();
        return titulo.replace(/\s+/g, ' ');
    }

    function obterResumoPagina() {
        const cards = Array.from(document.querySelectorAll('.card, .summary .card, [class*="card"]'))
            .slice(0, 6)
            .map((card) => card.textContent.replace(/\s+/g, ' ').trim())
            .filter(Boolean);
        const filtros = Array.from(document.querySelectorAll('select, input[type="text"], input[type="date"], input[type="search"]'))
            .slice(0, 5)
            .map((el) => {
                const label = el.getAttribute('aria-label') || el.id || el.name || 'campo';
                const value = el.value || el.getAttribute('placeholder') || '';
                return value ? `${label}: ${value}` : '';
            })
            .filter(Boolean);
        const table = Array.from(document.querySelectorAll('tbody tr'))
            .slice(0, 8)
            .map((row) => row.textContent.replace(/\s+/g, ' ').trim())
            .filter(Boolean);
        return {
            title: obterTituloPagina(),
            url: location.pathname,
            cards,
            filtros,
            table
        };
    }

    function obterHistoricoSidebar(sidebar) {
        return Array.from(sidebar.querySelectorAll('.jk-ai-msg')).slice(-8).map((msg) => ({
            role: msg.classList.contains('user') ? 'user' : 'assistant',
            content: msg.textContent || ''
        })).filter((msg) => msg.content.trim());
    }

    async function chamarAssistenteBackend(sidebar, pergunta) {
        const payload = JSON.stringify({
            message: pergunta,
            page: obterTituloPagina(),
            context: obterResumoPagina(),
            history: obterHistoricoSidebar(sidebar)
        });
        const urls = ['/api/ia/chat'];
        if (location.hostname === '127.0.0.1' || location.hostname === 'localhost') {
            urls.push('http://127.0.0.1:8012/api/ia/chat');
        }

        let ultimoErro = null;
        for (const url of urls) {
            try {
                const resp = await fetch(url, {
                    method: 'POST',
                    headers: {
                        ...obterAuthHeaders(),
                        'Content-Type': 'application/json'
                    },
                    body: payload
                });
                let data = null;
                try {
                    data = await resp.json();
                } catch (_e) {
                    data = null;
                }
                if (resp.ok) {
                    return extrairTextoAssistente(data) || 'A IA não retornou resposta.';
                }
                ultimoErro = new Error(resp.status === 405
                    ? 'O servidor local ainda está com uma versão antiga. Reinicie o programa para ativar a IA.'
                    : (data?.detail || resp.statusText || 'Falha ao consultar o assistente IA.'));
                if (resp.status !== 405) break;
            } catch (error) {
                if (!ultimoErro) ultimoErro = error;
            }
        }
        throw ultimoErro || new Error('Falha ao consultar o assistente IA.');
    }

    function garantirSidebar() {
        if (document.getElementById('jk-global-ai-sidebar')) return document.getElementById('jk-global-ai-sidebar');
        if (document.getElementById('sidebarPainelAssistenteVendas')) return null;

        if (!document.getElementById('jk-ia-panel') && !document.getElementById('jk-ia-fab')) {
            const jaExisteScript = Array.from(document.querySelectorAll('script[src]')).some((s) => {
                const src = String(s.getAttribute('src') || '');
                return src.includes('/ia-sidebar.js') || src.includes('/static/ia-sidebar.js');
            });
            if (!jaExisteScript) {
                const script = document.createElement('script');
                script.src = '/ia-sidebar.js?v=20260608-video-call-open-fix';
                script.async = true;
                script.setAttribute('data-jk-ia-loader', '1');
                document.body.appendChild(script);
            }
            return null;
        }

        if (!document.getElementById('jk-global-ai-sidebar-style')) {
            const style = document.createElement('style');
            style.id = 'jk-global-ai-sidebar-style';
            style.textContent = `
                #jk-global-ai-tab {
                    position: fixed;
                    right: 0;
                    top: 50vh;
                    transform: translateY(-50%);
                    width: 44px;
                    height: 84px;
                    z-index: 99998;
                    border-radius: 12px 0 0 12px;
                    border: 1px solid rgba(123, 207, 255, 0.85);
                    border-right: 0;
                    background: linear-gradient(165deg, #4facfe, #2e8be6);
                    color: #061523;
                    font: 900 13px "Segoe UI", Tahoma, sans-serif;
                    cursor: pointer;
                    box-shadow: 0 14px 30px rgba(0, 0, 0, 0.38);
                }
                #jk-global-ai-sidebar {
                    position: fixed;
                    top: 0;
                    right: 0;
                    width: 320px;
                    height: 100vh;
                    z-index: 99997;
                    transform: translateX(100%);
                    transition: transform 0.2s ease;
                    background: linear-gradient(165deg, #0d1e37, #071321);
                    border-left: 1px solid rgba(123, 207, 255, 0.35);
                    box-shadow: -18px 0 34px rgba(0, 0, 0, 0.38);
                    color: #eaf3ff;
                    font-family: "Segoe UI", Tahoma, sans-serif;
                    padding: 14px;
                    overflow: hidden;
                    display: flex;
                    flex-direction: column;
                }
                #jk-global-ai-sidebar.open { transform: translateX(0); }
                #jk-global-ai-sidebar.open + #jk-global-ai-tab { right: 320px; }
                #jk-global-ai-sidebar .jk-ai-head {
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    gap: 10px;
                    margin-bottom: 12px;
                }
                #jk-global-ai-sidebar h3 {
                    margin: 0;
                    color: #9bd1ff;
                    font-size: 1rem;
                }
                #jk-global-ai-sidebar .jk-ai-close {
                    width: 30px;
                    height: 30px;
                    border-radius: 8px;
                    border: 1px solid rgba(123, 207, 255, 0.36);
                    background: rgba(15, 32, 57, 0.95);
                    color: #d7efff;
                    cursor: pointer;
                    font-weight: 900;
                }
                #jk-global-ai-sidebar .jk-ai-status {
                    color: #b8c7d9;
                    font-size: 0.76rem;
                    line-height: 1.35;
                    margin-bottom: 12px;
                }
                #jk-global-ai-sidebar .jk-ai-chat {
                    flex: 1 1 auto;
                    min-height: 220px;
                    display: flex;
                    flex-direction: column;
                    gap: 12px;
                    overflow-y: auto;
                    padding: 4px 2px 12px;
                    margin-bottom: 10px;
                    scrollbar-width: none;
                }
                #jk-global-ai-sidebar .jk-ai-chat::-webkit-scrollbar {
                    display: none;
                }
                #jk-global-ai-sidebar .jk-ai-msg {
                    max-width: 86%;
                    border-radius: 16px;
                    padding: 10px 12px;
                    border: 1px solid transparent;
                    font-size: 0.84rem;
                    line-height: 1.45;
                    white-space: pre-wrap;
                }
                #jk-global-ai-sidebar .jk-ai-msg.user {
                    align-self: flex-end;
                    background: linear-gradient(165deg, #1888ff, #0f65d8);
                    color: #ffffff;
                    border-bottom-right-radius: 5px;
                }
                #jk-global-ai-sidebar .jk-ai-msg.assistant {
                    align-self: flex-start;
                    background: rgba(255, 255, 255, 0.08);
                    color: #eef5ff;
                    border-color: rgba(255, 255, 255, 0.1);
                    border-bottom-left-radius: 5px;
                }
                #jk-global-ai-sidebar .jk-ai-msg.assistant code {
                    background: rgba(255, 255, 255, 0.14);
                    border: 1px solid rgba(255, 255, 255, 0.18);
                    border-radius: 6px;
                    padding: 1px 5px;
                    font-size: 0.78rem;
                }
                #jk-global-ai-sidebar .jk-ai-msg.assistant strong {
                    color: #ffffff;
                }
                #jk-global-ai-sidebar .jk-ai-msg.loading {
                    color: #b8c7d9;
                    font-style: italic;
                }
                #jk-global-ai-sidebar .jk-ai-suggestions {
                    display: grid;
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                    gap: 8px;
                    margin-bottom: 10px;
                    padding-bottom: 2px;
                }
                #jk-global-ai-sidebar .jk-ai-chip {
                    min-height: 42px;
                    border: 1px solid rgba(123, 207, 255, 0.22);
                    border-radius: 12px;
                    background: rgba(255, 255, 255, 0.06);
                    color: #d7eaff;
                    font-size: 0.75rem;
                    font-weight: 700;
                    padding: 8px 10px;
                    cursor: pointer;
                    text-align: left;
                    white-space: normal;
                }
                #jk-global-ai-sidebar .jk-ai-composer {
                    display: flex;
                    align-items: flex-end;
                    gap: 8px;
                    border: 1px solid rgba(123, 207, 255, 0.22);
                    border-radius: 16px;
                    background: rgba(5, 13, 25, 0.88);
                    padding: 8px;
                }
                #jk-global-ai-sidebar .jk-ai-input {
                    flex: 1 1 auto;
                    min-height: 42px;
                    max-height: 140px;
                    resize: none;
                    border-radius: 12px;
                    border: 0;
                    background: transparent;
                    color: #eaf3ff;
                    font: inherit;
                    font-size: 0.82rem;
                    padding: 9px 8px;
                    outline: none;
                }
                #jk-global-ai-sidebar .jk-ai-send {
                    flex: 0 0 42px;
                    width: 42px;
                    height: 42px;
                    border: 0;
                    border-radius: 999px;
                    background: linear-gradient(165deg, #4facfe, #2e8be6);
                    color: #061523;
                    font-size: 1rem;
                    font-weight: 900;
                    padding: 0;
                    cursor: pointer;
                }
                @media (max-width: 760px) {
                    #jk-global-ai-sidebar { width: min(320px, calc(100vw - 46px)); }
                    #jk-global-ai-sidebar.open + #jk-global-ai-tab { right: min(320px, calc(100vw - 46px)); }
                }
            `;
            document.head.appendChild(style);
        }

        const sidebar = document.createElement('aside');
        sidebar.id = 'jk-global-ai-sidebar';
        sidebar.setAttribute('aria-label', 'Assistente IA');
        sidebar.innerHTML = `
            <div class="jk-ai-head">
                <h3>Assistente IA</h3>
                <button type="button" class="jk-ai-close" aria-label="Fechar assistente">×</button>
            </div>
            <div class="jk-ai-status">Assistente conectado ao contexto da tela atual.</div>
            <div class="jk-ai-chat" aria-live="polite">
                <div class="jk-ai-msg assistant">Olá. Posso resumir esta tela, apontar dados importantes ou sugerir a próxima análise.</div>
            </div>
            <div class="jk-ai-suggestions">
                <button class="jk-ai-chip" type="button" data-prompt="Resuma a tela atual">Resumir tela atual</button>
                <button class="jk-ai-chip" type="button" data-prompt="O que devo investigar agora?">Proxima analise</button>
                <button class="jk-ai-chip" type="button" data-prompt="Quais dados visiveis sao importantes?">Dados importantes</button>
            </div>
            <div class="jk-ai-composer">
                <textarea class="jk-ai-input" placeholder="Pergunte sobre esta pagina..."></textarea>
                <button class="jk-ai-send" type="button" aria-label="Enviar pergunta">↑</button>
            </div>
        `;

        const tab = document.createElement('button');
        tab.id = 'jk-global-ai-tab';
        tab.type = 'button';
        tab.textContent = 'IA';
        tab.setAttribute('aria-label', 'Abrir assistente IA');

        document.body.appendChild(sidebar);
        document.body.appendChild(tab);
        return sidebar;
    }

    function escaparHtmlAssistente(texto) {
        return String(texto || '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#39;');
    }

    function formatarMarkdownBasicoAssistente(texto) {
        let html = escaparHtmlAssistente(texto || '');
        html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        html = html.replace(/(^|[^*])\*(?!\s)([^*]+?)\*(?!\*)/g, '$1<em>$2</em>');
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
        html = html.replace(/\r\n?|\n/g, '<br>');
        return html;
    }

    function pareceDadoInternoAssistente(texto) {
        const bruto = String(texto || '').trim();
        if (!bruto) return false;
        const marcadores = [
            '"pack_id"', '"order_id"', '"buyer"', '"messages"', '"seller_max_message_length"',
            '"from_role"', '"created_at"', '"resolved_at"', '"status_message"', '"items"', '"pergunta"'
        ];
        const qtdMarcadores = marcadores.filter((item) => bruto.includes(item)).length;
        const qtdAspasJson = (bruto.match(/"[a-zA-Z0-9_]+":/g) || []).length;
        return qtdMarcadores >= 3 || qtdAspasJson >= 8 || (/^\s*[\[{]/.test(bruto) && bruto.length > 500);
    }

    function extrairTextoAssistente(valor) {
        if (valor == null) return '';
        if (typeof valor === 'object') {
            const candidatos = [
                valor.resposta, valor.response, valor.answer, valor.text, valor.message,
                valor.content, valor.output, valor.result, valor.status_message
            ];
            for (const candidato of candidatos) {
                const texto = extrairTextoAssistente(candidato);
                if (texto) return texto;
            }
            return pareceDadoInternoAssistente(JSON.stringify(valor))
                ? 'Recebi dados internos do módulo em vez de uma resposta pronta. Tente perguntar novamente com uma pergunta mais específica.'
                : JSON.stringify(valor, null, 2);
        }
        let texto = String(valor || '').trim();
        if (!texto) return '';

        if (/^\s*[\[{]/.test(texto)) {
            try {
                const parsed = JSON.parse(texto);
                const extraido = extrairTextoAssistente(parsed);
                if (extraido) return extraido;
            } catch (_e) {
                // segue com a protecao contra dado interno abaixo
            }
        }

        if (pareceDadoInternoAssistente(texto)) {
            return 'Recebi dados internos do módulo em vez de uma resposta pronta. Tente perguntar novamente com uma pergunta mais específica.';
        }
        return texto;
    }

    function definirTextoMensagemAssistente(el, texto, tipo) {
        if (!el) return;
        if (tipo === 'assistant') {
            el.innerHTML = formatarMarkdownBasicoAssistente(extrairTextoAssistente(texto) || '');
            return;
        }
        el.textContent = texto || '';
    }

    function adicionarMensagem(sidebar, tipo, texto) {
        const chat = sidebar.querySelector('.jk-ai-chat');
        if (!chat) return;
        const msg = document.createElement('div');
        msg.className = `jk-ai-msg ${tipo}`;
        definirTextoMensagemAssistente(msg, texto, tipo);
        if (tipo === 'assistant' && /consultando/i.test(texto)) {
            msg.classList.add('loading');
        }
        chat.appendChild(msg);
        chat.scrollTop = chat.scrollHeight;
        return msg;
    }

    function setAberto(sidebar, aberto) {
        const tab = document.getElementById('jk-global-ai-tab');
        sidebar.classList.toggle('open', !!aberto);
        if (tab) tab.setAttribute('aria-label', aberto ? 'Fechar assistente IA' : 'Abrir assistente IA');
        salvarAberto(aberto);
    }

    function init() {
        const sidebar = garantirSidebar();
        if (!sidebar) return;
        const tab = document.getElementById('jk-global-ai-tab');
        const input = sidebar.querySelector('.jk-ai-input');

        const enviar = async (textoManual) => {
            const texto = String(textoManual || input.value || '').trim();
            if (!texto) return;
            adicionarMensagem(sidebar, 'user', texto);
            input.value = '';
            setAberto(sidebar, true);
            const aguardando = adicionarMensagem(sidebar, 'assistant', 'Pensando...');
            try {
                const resposta = await chamarAssistenteBackend(sidebar, texto);
                definirTextoMensagemAssistente(aguardando, resposta, 'assistant');
                aguardando.classList.remove('loading');
            } catch (error) {
                definirTextoMensagemAssistente(aguardando, error?.message === 'Method Not Allowed'
                    ? 'O servidor local ainda está com uma versão antiga. Reinicie o programa para ativar a IA.'
                    : (error?.message || 'Não foi possível consultar a IA.'), 'assistant');
                aguardando.classList.remove('loading');
            }
        };

        tab.addEventListener('click', () => {
            const jaAberto = sidebar.classList.contains('open');
            setAberto(sidebar, !jaAberto);
        });
        sidebar.querySelector('.jk-ai-close')?.addEventListener('click', () => setAberto(sidebar, false));
        sidebar.querySelector('.jk-ai-send')?.addEventListener('click', () => enviar());
        input?.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                enviar();
            }
        });
        sidebar.querySelectorAll('.jk-ai-chip').forEach((btn) => {
            btn.addEventListener('click', () => enviar(btn.dataset.prompt || btn.textContent));
        });

        setAberto(sidebar, obterAberto());
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
})();

(function initAdminUserMessages() {
    if (window.__jkAdminUserMessagesInit) return;
    window.__jkAdminUserMessagesInit = true;

    const FALLBACK_POLL_MS = 60 * 60 * 1000;
    let buscando = false;
    let mensagemAtualId = '';
    let filaMensagensAdmin = [];
    let streamMensagensAdmin = null;
    let streamConectado = false;
    let fallbackTimer = null;

    function tokenAtual() {
        return localStorage.getItem('access_token') || '';
    }

    function headersAuth(extra) {
        const headers = Object.assign({}, extra || {});
        const token = tokenAtual();
        if (token) headers.Authorization = `Bearer ${token}`;
        return headers;
    }

    function escapar(texto) {
        return String(texto || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function garantirEstilo() {
        if (document.getElementById('jk-admin-message-style')) return;
        const style = document.createElement('style');
        style.id = 'jk-admin-message-style';
        style.textContent = `
            #jk-admin-message-toast {
                position: fixed;
                right: 22px;
                bottom: 92px;
                z-index: 999999;
                width: min(360px, calc(100vw - 32px));
                border: 1px solid rgba(143, 211, 255, 0.35);
                border-radius: 16px;
                background: rgba(7, 17, 31, 0.96);
                color: #edf6ff;
                box-shadow: 0 20px 44px rgba(0, 0, 0, 0.35);
                padding: 14px;
                font-family: Segoe UI, Arial, sans-serif;
                transform: translateY(14px);
                opacity: 0;
                pointer-events: none;
                transition: opacity .18s ease, transform .18s ease;
            }
            #jk-admin-message-toast.open {
                opacity: 1;
                transform: translateY(0);
                pointer-events: auto;
            }
            #jk-admin-message-toast strong {
                display: block;
                color: #9bd1ff;
                font-size: .94rem;
                margin-bottom: 6px;
            }
            #jk-admin-message-toast p {
                margin: 0 0 12px;
                color: #d8eaff;
                font-size: .86rem;
                line-height: 1.42;
                white-space: pre-wrap;
            }
            #jk-admin-message-toast .jk-admin-message-meta {
                color: #9fb6cf;
                font-size: .72rem;
                margin-bottom: 10px;
            }
            #jk-admin-message-toast button {
                border: 1px solid rgba(143, 211, 255, 0.42);
                border-radius: 10px;
                background: rgba(79, 172, 254, 0.18);
                color: #edf6ff;
                padding: 8px 12px;
                font-weight: 800;
                cursor: pointer;
            }
        `;
        document.head.appendChild(style);
    }

    function garantirToast() {
        garantirEstilo();
        let toast = document.getElementById('jk-admin-message-toast');
        if (toast) return toast;
        toast = document.createElement('div');
        toast.id = 'jk-admin-message-toast';
        toast.setAttribute('role', 'status');
        toast.setAttribute('aria-live', 'polite');
        document.body.appendChild(toast);
        return toast;
    }

    async function marcarLida(id) {
        if (!id || !tokenAtual()) return;
        try {
            await fetch('/api/user/messages/' + encodeURIComponent(id) + '/read', {
                method: 'POST',
                headers: headersAuth(),
                cache: 'no-store'
            });
        } catch (_err) {}
    }

    function esconderToast() {
        const toast = document.getElementById('jk-admin-message-toast');
        if (toast) toast.classList.remove('open');
        mensagemAtualId = '';
    }

    function idMensagem(msg) {
        return String(msg && msg.id || '').trim();
    }

    function mensagemJaPendente(id) {
        return filaMensagensAdmin.some(item => idMensagem(item) === id);
    }

    function enfileirarMensagem(msg) {
        const id = idMensagem(msg);
        if (!id || id === mensagemAtualId || mensagemJaPendente(id)) return;
        if (mensagemAtualId) {
            filaMensagensAdmin.push(msg);
            return;
        }
        mostrarMensagem(msg);
    }

    function mostrarMensagem(msg) {
        if (!msg || !msg.id || mensagemAtualId === msg.id) return;
        mensagemAtualId = msg.id;
        const toast = garantirToast();
        toast.innerHTML = `
            <strong>${escapar(msg.title || 'Mensagem do administrador')}</strong>
            <div class="jk-admin-message-meta">${escapar(msg.sender || 'admin')} | ${escapar(msg.created_at || '')}</div>
            <p>${escapar(msg.message || '')}</p>
            <button type="button">Entendi</button>
        `;
        toast.querySelector('button')?.addEventListener('click', async () => {
            await marcarLida(msg.id);
            esconderToast();
            const proxima = filaMensagensAdmin.shift();
            if (proxima) {
                mostrarMensagem(proxima);
            } else if (!streamConectado) {
                setTimeout(buscarMensagensAdmin, 500);
            }
        });
        requestAnimationFrame(() => toast.classList.add('open'));
    }

    async function buscarMensagensAdmin() {
        if (buscando || !tokenAtual()) return;
        const path = String(window.location.pathname || '').toLowerCase();
        if (path.includes('frontend_index') || path.includes('login')) return;
        buscando = true;
        try {
            const resp = await fetch('/api/user/messages', {
                headers: headersAuth(),
                cache: 'no-store'
            });
            const data = await resp.json().catch(() => ({}));
            if (resp.ok && data.success !== false && Array.isArray(data.messages) && data.messages.length) {
                data.messages.forEach(enfileirarMensagem);
            }
        } catch (_err) {
        } finally {
            buscando = false;
        }
    }

    function agendarFallbackMensagens(delayMs = FALLBACK_POLL_MS) {
        if (fallbackTimer) clearTimeout(fallbackTimer);
        fallbackTimer = setTimeout(async () => {
            fallbackTimer = null;
            if (!streamConectado) {
                await buscarMensagensAdmin();
                agendarFallbackMensagens(FALLBACK_POLL_MS);
            }
        }, Math.max(30000, Number(delayMs) || FALLBACK_POLL_MS));
    }

    function limparFallbackMensagens() {
        if (fallbackTimer) clearTimeout(fallbackTimer);
        fallbackTimer = null;
    }

    function iniciarStreamMensagensAdmin() {
        const token = tokenAtual();
        if (!token || /frontend_index|login/i.test(String(window.location.pathname || ''))) return;
        if (!window.EventSource) {
            agendarFallbackMensagens(60000);
            return;
        }
        if (streamMensagensAdmin && streamMensagensAdmin.readyState !== EventSource.CLOSED) return;
        try {
            streamMensagensAdmin = new EventSource('/api/user/messages/stream?token=' + encodeURIComponent(token));
            streamMensagensAdmin.onopen = () => {
                streamConectado = true;
                limparFallbackMensagens();
            };
            streamMensagensAdmin.addEventListener('ready', () => {
                streamConectado = true;
                limparFallbackMensagens();
            });
            streamMensagensAdmin.addEventListener('admin-message', (event) => {
                streamConectado = true;
                limparFallbackMensagens();
                try {
                    enfileirarMensagem(JSON.parse(event.data || '{}'));
                } catch (_err) {}
            });
            streamMensagensAdmin.addEventListener('fallback', () => {
                streamConectado = false;
                try { streamMensagensAdmin.close(); } catch (_err) {}
                agendarFallbackMensagens(60000);
            });
            streamMensagensAdmin.onerror = () => {
                streamConectado = false;
                agendarFallbackMensagens(2 * 60 * 1000);
            };
        } catch (_err) {
            streamConectado = false;
            agendarFallbackMensagens(60000);
        }
    }

    function iniciarMensagensAdmin() {
        buscarMensagensAdmin();
        iniciarStreamMensagensAdmin();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => setTimeout(iniciarMensagensAdmin, 3500), { once: true });
    } else {
        setTimeout(iniciarMensagensAdmin, 3500);
    }
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) {
            iniciarStreamMensagensAdmin();
            if (!streamConectado) buscarMensagensAdmin();
        }
    });
})();
