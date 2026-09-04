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

(function instalarMidiaAutenticadaJK(global) {
    'use strict';

    if (global.JKAuthenticatedMedia) return;
    const SOURCE_ATTR = 'data-jk-auth-src';
    const LINK_ATTR = 'data-jk-auth-link';
    const registros = new WeakMap();
    const ativos = new Set();

    function segmentoFotoSeguro(valor) {
        const bruto = String(valor || '');
        if (!bruto || bruto === '.' || bruto === '..' || /[\\\u0000-\u001f\u007f]/.test(bruto)) return '';
        let decodificado = bruto;
        try {
            decodificado = decodeURIComponent(bruto);
        } catch (_error) {
            // '%' e valido em nomes legados; a codificacao canonica abaixo o preserva como %25.
        }
        if (
            !decodificado
            || decodificado === '.'
            || decodificado === '..'
            || /[\/:\\\u0000-\u001f\u007f]/.test(decodificado)
        ) return '';
        return encodeURIComponent(decodificado);
    }

    function normalizarPartesFoto(caminho, comTenant) {
        const bruto = String(caminho || '');
        if (!bruto || bruto.startsWith('/') || bruto.endsWith('/')) return '';
        const partes = bruto.split('/');
        const formatoRaiz = comTenant ? partes.length === 2 : partes.length === 1;
        const formatoLoja = comTenant
            ? partes.length === 4 && partes[1].toLowerCase() === 'lojas'
            : partes.length === 3 && partes[0].toLowerCase() === 'lojas';
        if (!formatoRaiz && !formatoLoja) return '';
        const codificadas = partes.map(segmentoFotoSeguro);
        if (codificadas.some(parte => !parte)) return '';
        if (!/\.(?:png|jpe?g|gif|webp|bmp)$/i.test(decodeURIComponent(codificadas[codificadas.length - 1]))) return '';
        if (formatoLoja) codificadas[comTenant ? 1 : 0] = 'lojas';
        return codificadas.join('/');
    }

    function dividirCaminhoESufixo(valor) {
        const bruto = String(valor || '');
        const indice = bruto.search(/[?#]/);
        return indice < 0
            ? { caminho: bruto, sufixo: '' }
            : { caminho: bruto.slice(0, indice), sufixo: bruto.slice(indice) };
    }

    function caminhoUrlExternaSeguro(valor) {
        const caminhoExterno = String(valor || '')
            .replace(/^(?:https?:)?\/\/[^/?#]*/i, '')
            .split(/[?#]/, 1)[0];
        return !caminhoExterno.split('/').some((parte) => {
            if (!parte) return false;
            let decodificada = parte;
            try { decodificada = decodeURIComponent(parte); } catch (_error) {}
            return decodificada === '.'
                || decodificada === '..'
                || /[\\/\u0000-\u001f\u007f]/.test(decodificada);
        });
    }

    function rotaFotoCadastroLocal(valor) {
        const partes = [];
        String(valor || '')
            .split(/[?#]/, 1)[0]
            .replace(/\\/g, '/')
            .replace(/^\/+/, '')
            .split('/')
            .forEach((parte) => {
                if (!parte || parte === '.') return;
                if (parte === '..') partes.pop();
                else partes.push(parte);
            });
        const normalizada = partes.join('/').toLowerCase();
        return normalizada.startsWith('api/cadastro/foto/')
            || normalizada.startsWith('api/cadastro/foto-arquivo/');
    }

    function normalizarCaminhoArquivoLocal(valor, endpointArquivo) {
        let caminho = String(valor || '').replace(/\\/g, '/');
        if (/^file:/i.test(caminho)) caminho = caminho.slice(caminho.indexOf(':') + 1);
        caminho = caminho.split(/[?#]/, 1)[0];
        const partes = caminho.split('/').filter(Boolean);
        if (partes.length && /^[a-z]:$/i.test(partes[0])) partes.shift();
        if (!partes.length) return '';
        const codificadas = partes.map(segmentoFotoSeguro);
        if (codificadas.some(parte => !parte)) return '';
        const nome = codificadas[codificadas.length - 1];
        if (!/\.(?:png|jpe?g|gif|webp|bmp)$/i.test(decodeURIComponent(nome))) return '';
        return `${endpointArquivo}${nome}`;
    }

    function normalizarUrlFotoCadastro(valor) {
        let bruto = String(valor || '').trim();
        if (!bruto) return '';
        bruto = bruto.replace(/^["'`‘’“”]+|["'`‘’“”]+$/g, '').trim();
        if (!bruto) return '';

        if (/^(?:https?:)?\/\//i.test(bruto)) {
            return caminhoUrlExternaSeguro(bruto) ? bruto : '';
        }

        const endpointArquivo = '/api/cadastro/foto-arquivo/';
        const endpointTenant = '/api/cadastro/foto/';
        const caminhoWindows = /^[a-z]:[\\/]/i.test(bruto);
        const esquema = /^([a-z][a-z0-9+.-]*):/i.exec(bruto);
        if (esquema && !caminhoWindows && !/^file$/i.test(esquema[1])) {
            if (!/^https?$/i.test(esquema[1]) || !rotaFotoCadastroLocal(bruto.slice(esquema[0].length))) {
                return /[\u0000-\u001f\u007f]/.test(bruto) ? '' : bruto;
            }
            bruto = bruto.slice(esquema[0].length);
        }
        if (/^file:/i.test(bruto) || caminhoWindows) {
            return normalizarCaminhoArquivoLocal(bruto, endpointArquivo);
        }
        if (/[\\\u0000-\u001f\u007f]/.test(bruto)) return '';

        if (bruto.toLowerCase().startsWith(endpointArquivo)) {
            const { caminho, sufixo } = dividirCaminhoESufixo(bruto.slice(endpointArquivo.length));
            const seguro = normalizarPartesFoto(caminho, false);
            return seguro ? `${endpointArquivo}${seguro}${sufixo}` : '';
        }
        if (bruto.toLowerCase().startsWith(endpointTenant)) {
            const { caminho, sufixo } = dividirCaminhoESufixo(bruto.slice(endpointTenant.length));
            const seguro = normalizarPartesFoto(caminho, true);
            return seguro ? `${endpointTenant}${seguro}${sufixo}` : '';
        }

        if (/[?#]/.test(bruto)) return '';
        if (/^cadastro_fotos\//i.test(bruto)) bruto = bruto.slice(bruto.indexOf('/') + 1);
        const seguro = normalizarPartesFoto(bruto, false);
        return seguro ? `${endpointArquivo}${seguro}` : normalizarCaminhoArquivoLocal(bruto, endpointArquivo);
    }

    function ehUrlProtegidaCadastro(valor) {
        const bruto = String(valor || '').trim();
        if (!bruto) return false;
        try {
            const url = new global.URL(bruto, global.location.href);
            return url.origin === global.location.origin
                && /^\/api\/cadastro\/(?:foto-arquivo\/|foto\/)/i.test(url.pathname);
        } catch (_error) {
            return false;
        }
    }

    function revogarRegistro(registro) {
        if (!registro) return;
        if (registro.controller) registro.controller.abort();
        if (registro.objectUrl && global.URL && typeof global.URL.revokeObjectURL === 'function') {
            global.URL.revokeObjectURL(registro.objectUrl);
        }
        ativos.delete(registro);
    }

    function revogarImagem(img) {
        const registro = registros.get(img);
        if (!registro) return;
        revogarRegistro(registro);
        registros.delete(img);
        if (registro.objectUrl && img.getAttribute('src') === registro.objectUrl) img.removeAttribute('src');
        const link = img.closest && img.closest(`a[${LINK_ATTR}]`);
        if (link && registro.objectUrl && link.getAttribute('href') === registro.objectUrl) link.removeAttribute('href');
    }

    async function hidratarImagem(img, fonteExplicita) {
        if (!img || typeof img.getAttribute !== 'function') return '';
        const fonte = String(fonteExplicita || img.getAttribute(SOURCE_ATTR) || '').trim();
        if (!fonte) {
            revogarImagem(img);
            return '';
        }
        if (!ehUrlProtegidaCadastro(fonte)) {
            revogarImagem(img);
            img.setAttribute('src', fonte);
            return fonte;
        }

        const anterior = registros.get(img);
        if (anterior && anterior.source === fonte) return anterior.promise;
        revogarImagem(img);
        img.setAttribute(SOURCE_ATTR, fonte);
        img.removeAttribute('src');

        const controller = typeof global.AbortController === 'function' ? new global.AbortController() : null;
        const registro = { source: fonte, controller, objectUrl: '', promise: null };
        registros.set(img, registro);
        ativos.add(registro);
        registro.promise = (async () => {
            if (typeof global.obterAuthHeaders !== 'function') throw new Error('Autenticacao indisponivel.');
            const headers = global.obterAuthHeaders();
            if (!headers || !Object.keys(headers).length) throw new Error('Autenticacao indisponivel.');
            const response = await global.fetch(fonte, {
                headers,
                ...(controller ? { signal: controller.signal } : {}),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const blob = await response.blob();
            if (!blob || (blob.type && !String(blob.type).toLowerCase().startsWith('image/'))) {
                throw new Error('Resposta de foto invalida.');
            }
            if (registros.get(img) !== registro) return '';
            const objectUrl = global.URL.createObjectURL(blob);
            if (registros.get(img) !== registro) {
                global.URL.revokeObjectURL(objectUrl);
                return '';
            }
            registro.objectUrl = objectUrl;
            registro.controller = null;
            img.setAttribute('src', objectUrl);
            img.classList && img.classList.remove('jk-auth-image-error');
            const link = img.closest && img.closest(`a[${LINK_ATTR}]`);
            if (link) link.setAttribute('href', objectUrl);
            return objectUrl;
        })().catch((error) => {
            if (registros.get(img) === registro) {
                registros.delete(img);
                ativos.delete(registro);
                img.removeAttribute('src');
                if (!error || error.name !== 'AbortError') {
                    img.classList && img.classList.add('jk-auth-image-error');
                    img.title = 'Nao foi possivel carregar esta imagem.';
                }
            }
            return '';
        });
        return registro.promise;
    }

    let intersectionObserver = null;
    if (typeof global.IntersectionObserver === 'function') {
        intersectionObserver = new global.IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                if (!entry.isIntersecting) return;
                intersectionObserver.unobserve(entry.target);
                hidratarImagem(entry.target);
            });
        }, { rootMargin: '160px' });
    }

    function agendarImagem(img) {
        const fonte = String(img && img.getAttribute && img.getAttribute(SOURCE_ATTR) || '').trim();
        if (!fonte) {
            revogarImagem(img);
            return;
        }
        const atual = registros.get(img);
        if (atual && atual.source === fonte) {
            if (atual.objectUrl && img.getAttribute('src') !== atual.objectUrl) {
                img.setAttribute('src', atual.objectUrl);
            }
            return;
        }
        if (atual) revogarImagem(img);
        img.removeAttribute('src');
        if (intersectionObserver) intersectionObserver.observe(img);
        else hidratarImagem(img);
    }

    function imagensDaRaiz(raiz) {
        const imagens = [];
        if (raiz && raiz.matches && raiz.matches(`img[${SOURCE_ATTR}]`)) imagens.push(raiz);
        if (raiz && raiz.querySelectorAll) imagens.push(...raiz.querySelectorAll(`img[${SOURCE_ATTR}]`));
        return imagens;
    }

    function hidratar(raiz) {
        imagensDaRaiz(raiz || global.document).forEach(agendarImagem);
    }

    function liberar(raiz) {
        imagensDaRaiz(raiz).forEach((img) => {
            if (intersectionObserver) intersectionObserver.unobserve(img);
            revogarImagem(img);
        });
    }

    let mutationObserver = null;
    function iniciarObservacao() {
        hidratar(global.document);
        if (typeof global.MutationObserver !== 'function' || !global.document.documentElement) return;
        mutationObserver = new global.MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                mutation.removedNodes && mutation.removedNodes.forEach(liberar);
                mutation.addedNodes && mutation.addedNodes.forEach(hidratar);
                if (mutation.type === 'attributes') agendarImagem(mutation.target);
            });
        });
        mutationObserver.observe(global.document.documentElement, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: [SOURCE_ATTR],
        });
    }

    global.JKAuthenticatedMedia = Object.freeze({
        ehUrlProtegidaCadastro,
        hidratar,
        hidratarImagem,
        liberar,
        normalizarUrlFotoCadastro,
        revogarImagem,
    });
    if (global.document.readyState === 'loading') {
        global.document.addEventListener('DOMContentLoaded', iniciarObservacao, { once: true });
    } else iniciarObservacao();
    global.addEventListener('beforeunload', () => {
        if (mutationObserver) mutationObserver.disconnect();
        if (intersectionObserver) intersectionObserver.disconnect();
        Array.from(ativos).forEach(revogarRegistro);
        ativos.clear();
    });
})(window);
