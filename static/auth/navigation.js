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

    function podeAvisarShellDaAba() {
        return !!(window.top && window.top !== window && window.parent === window.top);
    }

    function enviarTituloAba() {
        try {
            if (!podeAvisarShellDaAba() || typeof window.top.postMessage !== 'function') return;
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
            if (!podeAvisarShellDaAba() || typeof window.top.postMessage !== 'function') return;
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
