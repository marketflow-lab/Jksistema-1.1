(function () {
    'use strict';

    const core = window.PesquisaMercadoCore;
    if (!core) {
        console.error('PesquisaMercadoCore nao foi carregado.');
        return;
    }

    const userData = safeJson(localStorage.getItem('user_data'), null);
    const permissions = safeJson(localStorage.getItem('permissions'), {});
    if (!userData) {
        window.location.href = 'frontend_index.html';
        return;
    }
    if (!(permissions.full === true || permissions.favoritos === true || permissions.avant === true || permissions.pesquisa_mercado === true)) {
        alert('Acesso nao autorizado para Pesquisa de Mercado.');
        window.location.href = 'dashboard.html';
        return;
    }

    const MAX_PROFILE_PAGES = 1000;
    const NAVIGATION_TIMEOUT_MS = 35000;
    const EXECUTION_TIMEOUT_MS = 30000;
    const BETWEEN_ITEMS_DELAY_MS = 450;

    const els = {
        vendedor: document.getElementById('pm-vendedor'),
        top: document.getElementById('pm-top'),
        minimo: document.getElementById('pm-minimo'),
        busca: document.getElementById('pm-busca-ranking'),
        analisar: document.getElementById('pm-btn-analisar'),
        pausar: document.getElementById('pm-btn-pausar'),
        cancelar: document.getElementById('pm-btn-cancelar'),
        limpar: document.getElementById('pm-btn-limpar'),
        abrirPerfil: document.getElementById('pm-btn-abrir-perfil'),
        home: document.getElementById('pm-btn-home'),
        voltar: document.getElementById('pm-btn-voltar'),
        status: document.getElementById('pm-status'),
        progress: document.getElementById('pm-progress'),
        progressLabel: document.getElementById('pm-progress-label'),
        currentUrl: document.getElementById('pm-current-url'),
        frame: document.getElementById('pm-browser-frame'),
        host: document.getElementById('pm-browser-host'),
        rankingBody: document.getElementById('pm-ranking-body'),
        rankingCount: document.getElementById('pm-ranking-count'),
        empty: document.getElementById('pm-empty'),
        paginas: document.getElementById('pm-stat-paginas'),
        encontrados: document.getElementById('pm-stat-encontrados'),
        abertos: document.getElementById('pm-stat-abertos'),
        comVendas: document.getElementById('pm-stat-com-vendas'),
        maior: document.getElementById('pm-stat-maior')
    };

    const state = {
        running: false,
        paused: false,
        cancelled: false,
        phase: 'idle',
        pages: 0,
        opened: 0,
        ads: [],
        targetSeller: null,
        currentUrl: '',
        incomplete: false
    };

    let browserProxy = null;
    let positionTimer = null;
    let requestSerial = 0;
    const pendingShellRequests = new Map();

    function safeJson(raw, fallback) {
        try { return raw ? JSON.parse(raw) : fallback; } catch (_err) { return fallback; }
    }

    function escapeHtml(valor) {
        return String(valor == null ? '' : valor)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function formatarNumero(valor) {
        const numero = Number(valor);
        return Number.isFinite(numero) ? Math.round(numero).toLocaleString('pt-BR') : '0';
    }

    function delay(ms) {
        return new Promise(resolve => setTimeout(resolve, Math.max(0, Number(ms) || 0)));
    }

    class CancelledError extends Error {
        constructor() {
            super('Analise cancelada.');
            this.name = 'CancelledError';
        }
    }

    function setStatus(message, kind) {
        els.status.textContent = message || '';
        els.status.classList.toggle('error', kind === 'error');
        els.status.classList.toggle('success', kind === 'success');
    }

    function setProgress(current, total, label) {
        const safeTotal = Math.max(0, Number(total) || 0);
        const safeCurrent = Math.max(0, Number(current) || 0);
        const percent = safeTotal > 0 ? Math.min(100, Math.round((safeCurrent / safeTotal) * 100)) : 0;
        els.progress.style.width = `${percent}%`;
        els.progressLabel.textContent = label || `${percent}%`;
    }

    function setRunning(active) {
        state.running = !!active;
        els.analisar.disabled = state.running;
        els.abrirPerfil.disabled = state.running;
        els.vendedor.disabled = state.running;
        els.pausar.disabled = !state.running;
        els.cancelar.disabled = !state.running;
        if (!state.running) {
            state.paused = false;
            els.pausar.textContent = 'Pausar';
        }
    }

    function atualizarResumo() {
        const comVendas = state.ads.filter(item => item && item.vendas != null);
        const maior = comVendas.reduce((maximo, item) => Math.max(maximo, Number(item.vendas) || 0), 0);
        els.paginas.textContent = formatarNumero(state.pages);
        els.encontrados.textContent = formatarNumero(state.ads.length);
        els.abertos.textContent = formatarNumero(state.opened);
        els.comVendas.textContent = formatarNumero(comVendas.length);
        els.maior.textContent = formatarNumero(maior);
    }

    function statusAnuncio(item) {
        if (item.status === 'vendedor_divergente') return { label: 'Vendedor divergente', cls: 'warning' };
        if (item.status === 'erro') return { label: 'Falha na leitura', cls: 'warning' };
        if (item.vendas == null) return { label: 'Sem vendas legiveis', cls: 'warning' };
        return { label: 'Analisado', cls: '' };
    }

    function renderRanking() {
        const ranking = core.filtrarMaisVendidos(state.ads, {
            minimo: els.minimo.value,
            limite: els.top.value,
            busca: els.busca.value
        });
        els.rankingCount.textContent = `${ranking.length} resultado(s)`;
        els.empty.hidden = ranking.length > 0;
        els.empty.textContent = state.ads.length
            ? 'Nenhum anuncio atende aos filtros atuais ou apresentou vendas legiveis.'
            : 'Nenhuma analise executada ainda.';
        els.rankingBody.innerHTML = ranking.map((item, index) => {
            const status = statusAnuncio(item);
            const title = item.erro ? ` title="${escapeHtml(item.erro)}"` : '';
            return `
                <tr${title}>
                    <td class="rank-cell">${index + 1}</td>
                    <td class="sales-cell">${formatarNumero(item.vendas)}</td>
                    <td>${escapeHtml(item.id || '-')}</td>
                    <td class="title-cell">${escapeHtml(item.titulo || 'Anuncio sem titulo')}</td>
                    <td>${escapeHtml(item.preco || '-')}</td>
                    <td>${escapeHtml(item.vendedor || state.targetSeller?.vendedor || '-')}</td>
                    <td><span class="source-chip">${escapeHtml(item.vendasFonte || '-')}</span></td>
                    <td><span class="status-chip ${status.cls}">${escapeHtml(status.label)}</span></td>
                    <td><a class="link" href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">Abrir</a></td>
                </tr>
            `;
        }).join('');
    }

    function atualizarTela() {
        atualizarResumo();
        renderRanking();
    }

    function limparEstado() {
        state.cancelled = state.running;
        state.phase = 'idle';
        state.pages = 0;
        state.opened = 0;
        state.ads = [];
        state.targetSeller = null;
        state.incomplete = false;
        setProgress(0, 0, '0%');
        setStatus('Informe um vendedor para iniciar.');
        atualizarTela();
    }

    async function aguardarSePausado() {
        while (state.paused && !state.cancelled) await delay(150);
        if (state.cancelled) throw new CancelledError();
    }

    function usarShellElectron() {
        try {
            return !!(window.top && window.top !== window && typeof window.top.postMessage === 'function');
        } catch (_err) {
            return false;
        }
    }

    function hasEmbeddedApi() {
        const api = window.electronAPI || null;
        return !!(api
            && typeof api.showEmbeddedMlBrowser === 'function'
            && typeof api.positionEmbeddedMlBrowser === 'function'
            && typeof api.hideEmbeddedMlBrowser === 'function'
            && typeof api.executeEmbeddedMlBrowser === 'function');
    }

    function enviarParaShell(channel, payload) {
        if (!usarShellElectron()) return;
        try { window.top.postMessage({ channel, payload: payload || {} }, '*'); } catch (_err) {}
    }

    function obterBoundsNavegador() {
        const alvo = els.host || els.frame;
        if (!alvo || typeof alvo.getBoundingClientRect !== 'function') return null;
        const rect = alvo.getBoundingClientRect();
        const left = Math.max(0, rect.left);
        const top = Math.max(0, rect.top);
        const width = Math.max(0, Math.min(rect.right, window.innerWidth) - left);
        const height = Math.max(0, Math.min(rect.bottom, window.innerHeight) - top);
        if (width < 20 || height < 20) return null;
        return { left, top, width, height };
    }

    function atualizarPosicao() {
        if (!browserProxy || !browserProxy.visible) return;
        const bounds = obterBoundsNavegador();
        if (!bounds) return;
        if (usarShellElectron()) enviarParaShell('jk-ml-browser-position', { bounds });
        else if (hasEmbeddedApi()) window.electronAPI.positionEmbeddedMlBrowser(bounds).catch(() => {});
    }

    function agendarPosicao() {
        if (positionTimer) return;
        positionTimer = setTimeout(() => {
            positionTimer = null;
            atualizarPosicao();
        }, 80);
    }

    function esconderNavegador() {
        if (browserProxy) browserProxy.visible = false;
        if (usarShellElectron()) enviarParaShell('jk-ml-browser-hide', { reason: 'pesquisa-mercado-hide' });
        else if (hasEmbeddedApi()) window.electronAPI.hideEmbeddedMlBrowser({ reason: 'pesquisa-mercado-hide' }).catch(() => {});
    }

    function criarProxyNavegador() {
        if (browserProxy) return browserProxy;
        if (!usarShellElectron() && !hasEmbeddedApi()) {
            throw new Error('Abra o modulo pelo aplicativo JK Sistema para usar o navegador interno.');
        }

        const listeners = new Map();
        const proxy = {
            visible: false,
            currentUrl: '',
            addEventListener(name, handler) {
                if (!listeners.has(name)) listeners.set(name, new Set());
                listeners.get(name).add(handler);
            },
            removeEventListener(name, handler) {
                listeners.get(name)?.delete(handler);
            },
            dispatchEvent(name, payload) {
                listeners.get(name)?.forEach(handler => {
                    try { handler(payload || {}); } catch (err) { console.warn('Falha no navegador da Pesquisa de Mercado:', err); }
                });
            },
            getURL() { return proxy.currentUrl || ''; },
            executeJavaScript(code) {
                if (usarShellElectron()) {
                    const requestId = `pesquisa-mercado-${Date.now()}-${++requestSerial}`;
                    return new Promise((resolve, reject) => {
                        pendingShellRequests.set(requestId, { resolve, reject });
                        enviarParaShell('jk-ml-browser-execute', { requestId, code });
                        setTimeout(() => {
                            if (!pendingShellRequests.has(requestId)) return;
                            pendingShellRequests.delete(requestId);
                            reject(new Error('Tempo limite ao ler a pagina do Mercado Livre.'));
                        }, EXECUTION_TIMEOUT_MS);
                    });
                }
                return window.electronAPI.executeEmbeddedMlBrowser(code);
            }
        };

        Object.defineProperty(proxy, 'src', {
            get() { return proxy.currentUrl; },
            set(rawUrl) {
                proxy.currentUrl = String(rawUrl || '');
                proxy.visible = true;
                const bounds = obterBoundsNavegador();
                if (usarShellElectron()) {
                    enviarParaShell('jk-ml-browser-show', { url: proxy.currentUrl, bounds });
                    setTimeout(atualizarPosicao, 120);
                    return;
                }
                window.electronAPI.showEmbeddedMlBrowser(proxy.currentUrl, bounds)
                    .then(result => {
                        proxy.currentUrl = result?.url || proxy.currentUrl;
                        proxy.dispatchEvent(result?.success === false ? 'did-fail-load' : 'did-finish-load', {
                            url: proxy.currentUrl,
                            errorDescription: result?.reason || result?.loadWarning || ''
                        });
                    })
                    .catch(err => proxy.dispatchEvent('did-fail-load', {
                        url: proxy.currentUrl,
                        errorDescription: err?.message || String(err)
                    }));
                setTimeout(atualizarPosicao, 120);
            }
        });

        browserProxy = proxy;
        return proxy;
    }

    function navegar(url) {
        const destino = core.normalizarUrlMercadoLivre(url);
        if (!destino) return Promise.reject(new Error('URL do Mercado Livre invalida.'));
        const proxy = criarProxyNavegador();
        state.currentUrl = destino;
        els.currentUrl.textContent = destino;
        return new Promise((resolve, reject) => {
            let finished = false;
            const finish = (error, event) => {
                if (finished) return;
                finished = true;
                clearTimeout(timer);
                proxy.removeEventListener('did-finish-load', onLoad);
                proxy.removeEventListener('did-fail-load', onFail);
                if (error) reject(error);
                else {
                    const loadedUrl = core.normalizarUrlMercadoLivre(event?.url) || proxy.getURL() || destino;
                    proxy.currentUrl = loadedUrl;
                    state.currentUrl = loadedUrl;
                    els.currentUrl.textContent = loadedUrl;
                    resolve(loadedUrl);
                }
            };
            const onLoad = event => finish(null, event);
            const onFail = event => finish(new Error(event?.errorDescription || 'Falha ao carregar a pagina do Mercado Livre.'), event);
            const timer = setTimeout(() => finish(new Error('Tempo limite ao abrir a pagina do Mercado Livre.')), NAVIGATION_TIMEOUT_MS);
            proxy.addEventListener('did-finish-load', onLoad);
            proxy.addEventListener('did-fail-load', onFail);
            proxy.src = destino;
        });
    }

    function executar(code) {
        return criarProxyNavegador().executeJavaScript(code);
    }

    function scriptMapearPagina() {
        return `
            (async function () {
                function clean(value) { return String(value == null ? '' : value).replace(/\\s+/g, ' ').trim(); }
                function absolute(value) {
                    try {
                        var parsed = new URL(String(value || ''), location.href);
                        if (!/(^|\\.)mercadolivre\\.com\\.br$/i.test(parsed.hostname)) return '';
                        parsed.hash = '';
                        return parsed.href;
                    } catch (_err) { return ''; }
                }
                function itemId(value) {
                    var match = clean(value).match(/\\bMLB-?(\\d{7,})\\b/i);
                    return match ? 'MLB' + match[1] : '';
                }
                function firstText(root, selectors) {
                    for (var i = 0; i < selectors.length; i += 1) {
                        var el = root.querySelector(selectors[i]);
                        var value = clean(el && (el.innerText || el.textContent || el.getAttribute('aria-label')));
                        if (value) return value;
                    }
                    return '';
                }
                function sales(text) {
                    var raw = clean(text).toLowerCase();
                    var patterns = [
                        /(?:\\+\\s*)?(\\d[\\d.,]*)\\s*(mil|k|mi)?\\s*(?:vendidos?|vendas?)/i,
                        /(?:vendidos?|vendas?)\\s*(?:[:\\-=]|de)?\\s*(?:\\+\\s*)?(\\d[\\d.,]*)\\s*(mil|k|mi)?/i
                    ];
                    for (var i = 0; i < patterns.length; i += 1) {
                        var match = raw.match(patterns[i]);
                        if (match) return clean(match[1] + (match[2] ? ' ' + match[2] : ''));
                    }
                    return '';
                }

                var stable = 0;
                var lastHeight = 0;
                for (var scrollIndex = 0; scrollIndex < 24 && stable < 3; scrollIndex += 1) {
                    window.scrollTo(0, Math.max(document.body.scrollHeight, document.documentElement.scrollHeight));
                    await new Promise(function (resolve) { setTimeout(resolve, 350); });
                    var height = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
                    if (height === lastHeight) stable += 1;
                    else stable = 0;
                    lastHeight = height;
                }

                var resultRoot = document.querySelector(
                    'ol.ui-search-layout, .ui-search-results, .shops__search-results, [data-testid="search-results"], main .ui-search-layout'
                ) || document;
                var cardSelectors = [
                    'li.ui-search-layout__item',
                    '.ui-search-result__wrapper',
                    '.ui-search-result',
                    'div.poly-card',
                    'section.poly-card',
                    '[data-testid="product-card"]',
                    '.shops__layout-item'
                ];
                var cards = [];
                cardSelectors.forEach(function (selector) {
                    resultRoot.querySelectorAll(selector).forEach(function (card) {
                        if (cards.indexOf(card) < 0) cards.push(card);
                    });
                });

                var anuncios = [];
                var seen = {};
                function register(card, anchor) {
                    var url = absolute(anchor && anchor.href);
                    if (!url || !/mercadolivre\\.com\\.br/i.test(url)) return;
                    var id = itemId(url + ' ' + clean(card && card.getAttribute('data-id')) + ' ' + clean(anchor && anchor.getAttribute('data-id')));
                    var looksProduct = !!id || /\\/(?:p|up)\\/MLB/i.test(url) || /produto\\.mercadolivre/i.test(url);
                    if (!looksProduct) return;
                    var key = id || url.split('?')[0].toLowerCase();
                    if (seen[key]) return;
                    seen[key] = true;
                    var title = firstText(card || document, [
                        '.poly-component__title', '.ui-search-item__title', 'h2', 'h3', '[data-testid="product-title"]'
                    ]) || clean(anchor && (anchor.title || anchor.getAttribute('aria-label')));
                    var price = firstText(card || document, [
                        '.andes-money-amount__fraction', '.poly-price__current', '.price-tag-fraction', '[data-testid="price-part"]'
                    ]);
                    var imageEl = (card || document).querySelector('img');
                    anuncios.push({
                        id: id,
                        url: url,
                        titulo: title,
                        preco: price ? 'R$ ' + price : '',
                        imagem: absolute(imageEl && (imageEl.currentSrc || imageEl.src)),
                        vendas: sales(clean(card && (card.innerText || card.textContent))),
                        vendasFonte: sales(clean(card && (card.innerText || card.textContent))) ? 'card_perfil' : ''
                    });
                }

                cards.forEach(function (card) {
                    var links = Array.from(card.querySelectorAll('a[href]'));
                    var anchor = links.find(function (item) {
                        return itemId(item.href) || /\\/(?:p|up)\\/MLB/i.test(item.href || '') || /produto\\.mercadolivre/i.test(item.hostname || '');
                    });
                    if (anchor) register(card, anchor);
                });

                if (!anuncios.length) {
                    Array.from(resultRoot.querySelectorAll('a[href]')).forEach(function (anchor) { register(anchor.closest('li, article, section, div') || anchor, anchor); });
                }

                var profileSelectors = [
                    '.ui-pdp-seller__link-trigger[href]',
                    '.ui-pdp-seller__header__title[href]',
                    'a[href*="/perfil/"]',
                    'a[href*="_CustId_"]',
                    'a[href*="seller_id="]'
                ];
                var perfilUrl = '';
                for (var profileIndex = 0; profileIndex < profileSelectors.length && !perfilUrl; profileIndex += 1) {
                    var profileAnchor = document.querySelector(profileSelectors[profileIndex]);
                    perfilUrl = absolute(profileAnchor && profileAnchor.href);
                }
                var vendedor = firstText(document, [
                    '.ui-pdp-seller__header__title',
                    '.ui-pdp-seller__nickname',
                    '[data-testid="seller-info"]',
                    '.shops-header__title',
                    '.store-info__name',
                    'h1'
                ]);

                var nextSelectors = [
                    'a[rel="next"]',
                    'li.andes-pagination__button--next a',
                    'a.andes-pagination__link[title*="Seguinte"]',
                    'a.andes-pagination__link[aria-label*="Seguinte"]',
                    'a[aria-label*="Próxima"]',
                    'a[aria-label*="Proxima"]'
                ];
                var nextUrl = '';
                for (var nextIndex = 0; nextIndex < nextSelectors.length && !nextUrl; nextIndex += 1) {
                    var nextAnchor = document.querySelector(nextSelectors[nextIndex]);
                    if (nextAnchor && !nextAnchor.closest('[aria-disabled="true"], .andes-pagination__button--disabled')) nextUrl = absolute(nextAnchor.href);
                }

                var bodyText = clean(document.body && document.body.innerText);
                return {
                    url: absolute(location.href),
                    title: clean(document.title),
                    pageType: document.querySelector('.ui-pdp-container, [data-testid="vip-container"]') ? 'produto' : (anuncios.length ? 'perfil' : 'desconhecida'),
                    authRequired: /entrar|iniciar sessao|fazer login/i.test(bodyText.slice(0, 1200)) && anuncios.length === 0,
                    anuncios: anuncios,
                    nextUrl: nextUrl,
                    perfilUrl: perfilUrl,
                    vendedor: vendedor
                };
            })();
        `;
    }

    function scriptDetalharAnuncio() {
        return `
            (function () {
                function clean(value) { return String(value == null ? '' : value).replace(/\\s+/g, ' ').trim(); }
                function absolute(value) {
                    try {
                        var parsed = new URL(String(value || ''), location.href);
                        if (!/(^|\\.)mercadolivre\\.com\\.br$/i.test(parsed.hostname)) return '';
                        parsed.hash = '';
                        return parsed.href;
                    } catch (_err) { return ''; }
                }
                function itemId(value) {
                    var match = clean(value).match(/\\bMLB-?(\\d{7,})\\b/i);
                    return match ? 'MLB' + match[1] : '';
                }
                function firstText(selectors) {
                    for (var i = 0; i < selectors.length; i += 1) {
                        var el = document.querySelector(selectors[i]);
                        var value = clean(el && (el.innerText || el.textContent || el.getAttribute('aria-label')));
                        if (value) return value;
                    }
                    return '';
                }
                function parseNumber(rawValue, suffix) {
                    var raw = clean(rawValue).replace(/\\s/g, '').replace(/\\+/g, '');
                    if (!raw) return null;
                    if (raw.indexOf(',') >= 0 && raw.indexOf('.') >= 0) raw = raw.lastIndexOf(',') > raw.lastIndexOf('.') ? raw.replace(/\\./g, '').replace(',', '.') : raw.replace(/,/g, '');
                    else if (raw.indexOf(',') >= 0) raw = raw.replace(',', '.');
                    else if ((raw.match(/\\./g) || []).length > 1 || /\\.\\d{3}$/.test(raw)) raw = raw.replace(/\\./g, '');
                    var parsed = Number(raw.replace(/[^\\d.-]/g, ''));
                    if (!Number.isFinite(parsed)) return null;
                    var unit = clean(suffix).toLowerCase();
                    if (/^(mil|k)$/.test(unit)) parsed *= 1000;
                    else if (/^(mi|milh)/.test(unit)) parsed *= 1000000;
                    return Math.max(0, Math.round(parsed));
                }
                function findSales(text, restricted) {
                    var value = clean(text).toLowerCase();
                    var patterns = restricted ? [
                        /vendas?\\s+(?:do|deste)\\s+(?:anuncio|item|produto)(?:\\s+ganhador)?\\s*(?:[:\\-=]|de)?\\s*(?:\\+\\s*)?(\\d[\\d.,]*)\\s*(mil|k|mi|milh(?:ao|oes|ão|ões))?/i,
                        /vendas?\\s+do\\s+vendedor\\s+neste\\s+anuncio\\s*(?:[:\\-=]|de)?\\s*(?:\\+\\s*)?(\\d[\\d.,]*)\\s*(mil|k|mi)?/i
                    ] : [
                        /(?:\\+\\s*)?(\\d[\\d.,]*)\\s*(mil|k|mi|milh(?:ao|oes|ão|ões))?\\s*(?:vendidos?|vendas?)/i,
                        /(?:vendidos?|vendas?)\\s*(?:[:\\-=]|de)?\\s*(?:\\+\\s*)?(\\d[\\d.,]*)\\s*(mil|k|mi|milh(?:ao|oes|ão|ões))?/i
                    ];
                    for (var i = 0; i < patterns.length; i += 1) {
                        var match = value.match(patterns[i]);
                        if (!match) continue;
                        var parsed = parseNumber(match[1], match[2]);
                        if (Number.isFinite(parsed)) return parsed;
                    }
                    return null;
                }

                var bodyText = clean(document.body && document.body.innerText);
                var html = String(document.documentElement && document.documentElement.innerHTML || '');
                var avantNodes = Array.from(document.querySelectorAll('[class*="avantpro"], [id*="avantpro"], [data-jk-field="vendas"], [data-field="vendas"]'));
                var vendas = null;
                var fonte = '';
                for (var i = 0; i < avantNodes.length && vendas == null; i += 1) {
                    vendas = findSales(avantNodes[i].innerText || avantNodes[i].textContent, true);
                    if (vendas != null) fonte = 'avantpro_anuncio';
                }
                if (vendas == null) {
                    var visibleSelectors = ['.ui-pdp-header__subtitle', '.ui-pdp-sold-quantity', '[data-testid="sold-quantity"]', '.ui-pdp-header'];
                    for (var j = 0; j < visibleSelectors.length && vendas == null; j += 1) {
                        var visible = document.querySelector(visibleSelectors[j]);
                        vendas = findSales(visible && (visible.innerText || visible.textContent), false);
                        if (vendas != null) fonte = 'pagina_anuncio';
                    }
                }
                if (vendas == null) {
                    var jsonPatterns = [
                        /["']sold_quantity["']\\s*:\\s*(\\d+)/i,
                        /["']soldQuantity["']\\s*:\\s*(\\d+)/i,
                        /["']total_sold["']\\s*:\\s*(\\d+)/i
                    ];
                    for (var p = 0; p < jsonPatterns.length && vendas == null; p += 1) {
                        var jsonMatch = html.match(jsonPatterns[p]);
                        if (jsonMatch) {
                            vendas = parseNumber(jsonMatch[1], '');
                            if (vendas != null) fonte = 'json_pagina';
                        }
                    }
                }
                if (vendas == null) {
                    vendas = findSales(bodyText, true);
                    if (vendas != null) fonte = 'texto_pagina_rotulado';
                }
                if (vendas == null) {
                    vendas = findSales(bodyText.slice(0, 8000), false);
                    if (vendas != null) fonte = 'texto_cabecalho_pagina';
                }

                var profileSelectors = [
                    '.ui-pdp-seller__link-trigger[href]', '.ui-pdp-seller__header__title[href]',
                    'a[href*="/perfil/"]', 'a[href*="_CustId_"]', 'a[href*="seller_id="]'
                ];
                var perfilUrl = '';
                for (var s = 0; s < profileSelectors.length && !perfilUrl; s += 1) {
                    var sellerAnchor = document.querySelector(profileSelectors[s]);
                    perfilUrl = absolute(sellerAnchor && sellerAnchor.href);
                }

                var preco = firstText(['.ui-pdp-price__second-line .andes-money-amount__fraction', '.ui-pdp-price__main-container .andes-money-amount__fraction', '[data-testid="price-part"]']);
                return {
                    id: itemId(location.href + ' ' + html.slice(0, 150000)),
                    url: absolute(location.href),
                    titulo: firstText(['h1.ui-pdp-title', '[data-testid="product-title"]', 'h1']) || clean(document.title),
                    preco: preco ? 'R$ ' + preco : '',
                    imagem: absolute((document.querySelector('.ui-pdp-gallery__figure img, [data-testid="gallery-image"] img, img.ui-pdp-image') || {}).src),
                    vendas: vendas,
                    vendasFonte: fonte,
                    vendedor: firstText(['.ui-pdp-seller__header__title', '.ui-pdp-seller__nickname', '[data-testid="seller-info"]']),
                    perfilUrl: perfilUrl,
                    erro: vendas == null ? 'Quantidade de vendas nao encontrada na pagina.' : ''
                };
            })();
        `;
    }

    async function lerPaginaMapeamento() {
        const resultado = await executar(scriptMapearPagina());
        if (!resultado || typeof resultado !== 'object') throw new Error('A pagina nao retornou dados de pesquisa.');
        if (resultado.authRequired) throw new Error('O Mercado Livre solicitou login. Conclua o acesso no navegador e execute a analise novamente.');
        return resultado;
    }

    async function resolverPerfilInicial(entradaUrl) {
        setStatus('Abrindo o vendedor no Mercado Livre...');
        await navegar(entradaUrl);
        await delay(450);
        const primeira = await lerPaginaMapeamento();
        if (primeira.pageType === 'produto' && primeira.perfilUrl && primeira.perfilUrl !== primeira.url) {
            setStatus('Perfil do vendedor localizado. Abrindo todos os anuncios...');
            await navegar(primeira.perfilUrl);
            await delay(450);
            return await lerPaginaMapeamento();
        }
        return primeira;
    }

    async function mapearPerfil(entradaUrl) {
        state.phase = 'mapping';
        const primeira = await resolverPerfilInicial(entradaUrl);
        let pagina = primeira;
        let urlAtual = core.normalizarUrlMercadoLivre(primeira.url) || entradaUrl;
        const visitadas = new Set();
        let semNovos = 0;

        while (pagina && state.pages < MAX_PROFILE_PAGES) {
            await aguardarSePausado();
            const chavePagina = urlAtual.split('#')[0];
            if (visitadas.has(chavePagina)) break;
            visitadas.add(chavePagina);
            state.pages += 1;

            const antes = state.ads.length;
            state.ads = core.consolidarAnuncios(state.ads.concat(pagina.anuncios || []));
            semNovos = state.ads.length === antes ? semNovos + 1 : 0;
            state.targetSeller = {
                perfilUrl: core.normalizarUrlMercadoLivre(pagina.perfilUrl) || state.targetSeller?.perfilUrl || urlAtual,
                vendedor: core.texto(pagina.vendedor) || state.targetSeller?.vendedor || ''
            };
            atualizarTela();
            setProgress(state.pages, 0, `${state.pages} pagina(s) | ${state.ads.length} anuncio(s)`);
            setStatus(`Mapeando o perfil: ${state.pages} pagina(s), ${state.ads.length} anuncio(s) sem duplicidade...`);

            const nextUrl = core.normalizarUrlMercadoLivre(pagina.nextUrl, urlAtual);
            if (!nextUrl || visitadas.has(nextUrl.split('#')[0]) || semNovos >= 3) break;
            await aguardarSePausado();
            await navegar(nextUrl);
            await delay(350);
            urlAtual = nextUrl;
            pagina = await lerPaginaMapeamento();
        }

        if (state.pages >= MAX_PROFILE_PAGES && pagina?.nextUrl) {
            state.incomplete = true;
            throw new Error(`A coleta atingiu o limite de seguranca de ${MAX_PROFILE_PAGES} paginas e foi encerrada como incompleta.`);
        }
        if (!state.ads.length) throw new Error('Nenhum anuncio foi localizado no perfil informado. Verifique o vendedor ou conclua o login do Mercado Livre.');
    }

    async function detalharAnuncios() {
        state.phase = 'details';
        const total = state.ads.length;
        for (let index = 0; index < total; index += 1) {
            await aguardarSePausado();
            const anuncio = state.ads[index];
            setStatus(`Abrindo anuncio ${index + 1}/${total}: ${anuncio.id || anuncio.titulo || 'Mercado Livre'}...`);
            setProgress(index, total, `${index}/${total} anuncios abertos`);
            try {
                await navegar(anuncio.url);
                await delay(350);
                const detalhe = await executar(scriptDetalharAnuncio());
                state.ads[index] = core.aplicarDetalhe(anuncio, detalhe || {}, state.targetSeller || {});
            } catch (err) {
                state.ads[index] = {
                    ...anuncio,
                    status: 'erro',
                    erro: err?.message || String(err)
                };
            }
            state.opened = index + 1;
            atualizarTela();
            setProgress(state.opened, total, `${state.opened}/${total} anuncios abertos`);
            await aguardarSePausado();
            if (index + 1 < total) await delay(BETWEEN_ITEMS_DELAY_MS);
        }
    }

    async function analisar() {
        if (state.running) return;
        let entradaUrl;
        try {
            entradaUrl = core.normalizarEntradaVendedor(els.vendedor.value);
        } catch (err) {
            setStatus(err?.message || String(err), 'error');
            els.vendedor.focus();
            return;
        }

        state.cancelled = false;
        state.paused = false;
        state.pages = 0;
        state.opened = 0;
        state.ads = [];
        state.targetSeller = null;
        state.incomplete = false;
        setRunning(true);
        atualizarTela();
        setProgress(0, 0, 'Preparando');

        try {
            await mapearPerfil(entradaUrl);
            await detalharAnuncios();
            state.phase = 'done';
            setProgress(1, 1, '100%');
            const comVendas = state.ads.filter(item => item.vendas != null).length;
            setStatus(`Analise concluida: ${state.ads.length} anuncio(s) abertos e ${comVendas} com vendas legiveis.`, 'success');
        } catch (err) {
            if (err instanceof CancelledError || state.cancelled) {
                state.phase = 'cancelled';
                setStatus(`Analise cancelada apos ${state.opened} de ${state.ads.length} anuncio(s).`, 'error');
            } else {
                state.phase = 'error';
                setStatus(err?.message || 'Nao foi possivel concluir a Pesquisa de Mercado.', 'error');
            }
        } finally {
            setRunning(false);
            atualizarTela();
        }
    }

    async function abrirSomentePerfil() {
        if (state.running) return;
        let entradaUrl;
        try {
            entradaUrl = core.normalizarEntradaVendedor(els.vendedor.value);
        } catch (err) {
            setStatus(err?.message || String(err), 'error');
            return;
        }
        setStatus('Abrindo o perfil do vendedor...');
        try {
            await navegar(entradaUrl);
            await delay(350);
            const info = await lerPaginaMapeamento();
            if (info.pageType === 'produto' && info.perfilUrl && info.perfilUrl !== info.url) await navegar(info.perfilUrl);
            setStatus('Perfil aberto no navegador interno.', 'success');
        } catch (err) {
            setStatus(err?.message || 'Nao foi possivel abrir o perfil.', 'error');
        }
    }

    function alternarPausa() {
        if (!state.running) return;
        state.paused = !state.paused;
        els.pausar.textContent = state.paused ? 'Retomar' : 'Pausar';
        setStatus(state.paused ? 'Analise pausada. Clique em Retomar para continuar.' : 'Retomando a analise...', state.paused ? '' : 'success');
    }

    function cancelar() {
        if (!state.running) return;
        state.cancelled = true;
        state.paused = false;
        els.pausar.textContent = 'Pausar';
        setStatus('Cancelando apos finalizar a leitura atual...', 'error');
    }

    window.addEventListener('message', event => {
        if (usarShellElectron() && event.source !== window.top) return;
        const data = event?.data;
        if (!data || typeof data !== 'object') return;
        if (data.channel === 'jk-ml-browser-event' && browserProxy) {
            const url = core.normalizarUrlMercadoLivre(data.url);
            if (url) {
                browserProxy.currentUrl = url;
                state.currentUrl = url;
                els.currentUrl.textContent = url;
            }
            browserProxy.dispatchEvent(data.event, data);
            return;
        }
        if (data.channel === 'jk-ml-browser-execute-result') {
            const pending = pendingShellRequests.get(data.requestId);
            if (!pending) return;
            pendingShellRequests.delete(data.requestId);
            if (data.error) pending.reject(new Error(data.error));
            else pending.resolve(data.result);
        }
    });

    els.analisar.addEventListener('click', analisar);
    els.abrirPerfil.addEventListener('click', abrirSomentePerfil);
    els.pausar.addEventListener('click', alternarPausa);
    els.cancelar.addEventListener('click', cancelar);
    els.limpar.addEventListener('click', limparEstado);
    els.home.addEventListener('click', () => { window.location.href = 'dashboard.html'; });
    els.voltar.addEventListener('click', () => { window.location.href = 'dashboard.html'; });
    els.top.addEventListener('change', renderRanking);
    els.minimo.addEventListener('input', renderRanking);
    els.busca.addEventListener('input', renderRanking);
    els.vendedor.addEventListener('keydown', event => {
        if (event.key === 'Enter') analisar();
    });

    window.addEventListener('resize', agendarPosicao);
    window.addEventListener('scroll', agendarPosicao, true);
    window.addEventListener('beforeunload', () => {
        state.cancelled = true;
        esconderNavegador();
    });

    atualizarTela();
})();
