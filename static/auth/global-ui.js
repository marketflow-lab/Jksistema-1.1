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

    const SYNC_MONITOR_INTERVAL_MS = 15000;

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
        const monitorCompartilhado = window.__jkVendasSyncMonitor;
        if (monitorCompartilhado && typeof monitorCompartilhado.subscribe === 'function') {
            monitorCompartilhado.subscribe(renderWidget);
            monitorCompartilhado.start();
            void monitorCompartilhado.refresh();
            return;
        }
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

        // A sidebar universal e a fonte unica do Black Jhon. Nao monte o widget legado por cima dela.
        if (document.getElementById('jk-ia-panel') || document.getElementById('jk-ia-fab') || document.getElementById('jk-ia-light-fab')) {
            return null;
        }

        if (!document.getElementById('jk-ia-panel') && !document.getElementById('jk-ia-fab')) {
            const jaExisteScript = Array.from(document.querySelectorAll('script[src]')).some((s) => {
                const src = String(s.getAttribute('src') || '');
                return src.includes('/ia-sidebar.js') || src.includes('/static/ia-sidebar.js');
            });
            if (!jaExisteScript) {
                const script = document.createElement('script');
                script.src = '/ia-sidebar.js?v=20260714-operational-agent-v1';
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
                #jk-global-ai-sidebar *,
                #jk-global-ai-sidebar *::before,
                #jk-global-ai-sidebar *::after {
                    box-sizing: border-box;
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
                    min-width: 0;
                    min-height: 220px;
                    display: flex;
                    flex-direction: column;
                    gap: 12px;
                    overflow-y: auto;
                    overflow-x: hidden;
                    padding: 4px 2px 12px;
                    margin-bottom: 10px;
                    scrollbar-width: none;
                }
                #jk-global-ai-sidebar .jk-ai-chat::-webkit-scrollbar {
                    display: none;
                }
                #jk-global-ai-sidebar .jk-ai-msg {
                    max-width: 86%;
                    min-width: 0;
                    overflow: hidden;
                    border-radius: 16px;
                    padding: 10px 12px;
                    border: 1px solid transparent;
                    font-size: 0.84rem;
                    line-height: 1.45;
                    white-space: pre-wrap;
                    overflow-wrap: anywhere;
                    word-break: break-word;
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
                    white-space: pre-wrap;
                    overflow-wrap: anywhere;
                    word-break: break-word;
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
                    min-width: 0;
                    border: 1px solid rgba(123, 207, 255, 0.22);
                    border-radius: 16px;
                    background: rgba(5, 13, 25, 0.88);
                    padding: 8px;
                }
                #jk-global-ai-sidebar .jk-ai-input {
                    flex: 1 1 auto;
                    min-width: 0;
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
                    overflow-wrap: anywhere;
                    word-break: break-word;
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
        sidebar.setAttribute('aria-label', 'Black Jhon');
        sidebar.innerHTML = `
            <div class="jk-ai-head">
                <h3>Black Jhon</h3>
                <button type="button" class="jk-ai-close" aria-label="Fechar Black Jhon">×</button>
            </div>
            <div class="jk-ai-status">Black Jhon conectado ao contexto da tela atual.</div>
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
        tab.textContent = 'BJ';
        tab.setAttribute('aria-label', 'Abrir Black Jhon');

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
    const ADMIN_MESSAGES_CACHE_MS = 2 * 60 * 1000;
    let buscando = false;
    let mensagemAtualId = '';
    let filaMensagensAdmin = [];
    let streamMensagensAdmin = null;
    let streamConectado = false;
    let fallbackTimer = null;
    let fallbackLiderTimer = null;
    let ultimasMensagensAdmin = [];
    let ultimasMensagensAdminAt = 0;
    const ADMIN_MESSAGES_CHANNEL = 'admin-user-messages';
    const adminMessagesLeader = window.jkTabCoordinator && typeof window.jkTabCoordinator.createLeader === 'function'
        ? window.jkTabCoordinator.createLeader(ADMIN_MESSAGES_CHANNEL, { ttlMs: 60000 })
        : null;

    function liderMensagensAdmin() {
        return !adminMessagesLeader || adminMessagesLeader.isLeader();
    }

    function publicarMensagensAdmin(type, payload) {
        try {
            window.jkTabCoordinator?.broadcast(ADMIN_MESSAGES_CHANNEL, { type, payload });
        } catch (_err) {}
    }

    function cacheMensagensAdminValido() {
        return Date.now() - ultimasMensagensAdminAt <= ADMIN_MESSAGES_CACHE_MS;
    }

    function salvarCacheMensagensAdmin(mensagens) {
        ultimasMensagensAdmin = Array.isArray(mensagens) ? mensagens.slice(0, 10) : [];
        ultimasMensagensAdminAt = Date.now();
    }

    function publicarCacheMensagensAdmin() {
        publicarMensagensAdmin('messages', ultimasMensagensAdmin);
    }

    function agendarChecagemLiderMensagens(delayMs = 30000) {
        if (fallbackLiderTimer) clearTimeout(fallbackLiderTimer);
        fallbackLiderTimer = setTimeout(() => {
            fallbackLiderTimer = null;
            iniciarMensagensAdmin();
        }, Math.max(5000, Number(delayMs) || 30000));
    }

    function fecharStreamMensagensAdmin() {
        try {
            if (streamMensagensAdmin) streamMensagensAdmin.close();
        } catch (_err) {}
        streamMensagensAdmin = null;
        streamConectado = false;
    }

    try {
        window.jkTabCoordinator?.subscribe(ADMIN_MESSAGES_CHANNEL, (evento) => {
            if (!evento || !evento.type) return;
            if (evento.type === 'message') {
                enfileirarMensagem(evento.payload);
            } else if (evento.type === 'messages' && Array.isArray(evento.payload)) {
                evento.payload.forEach(enfileirarMensagem);
            } else if (evento.type === 'request' && liderMensagensAdmin()) {
                if (cacheMensagensAdminValido()) {
                    publicarCacheMensagensAdmin();
                } else {
                    buscarMensagensAdmin().catch(() => {});
                }
            }
        });
    } catch (_err) {}

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
            salvarCacheMensagensAdmin(ultimasMensagensAdmin.filter(item => idMensagem(item) !== id));
            publicarCacheMensagensAdmin();
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
        if (!liderMensagensAdmin()) {
            publicarMensagensAdmin('request', {});
            agendarChecagemLiderMensagens();
            return;
        }
        buscando = true;
        try {
            const resp = await fetch('/api/user/messages', {
                headers: headersAuth(),
                cache: 'no-store'
            });
            const data = await resp.json().catch(() => ({}));
            if (resp.ok && data.success !== false && Array.isArray(data.messages)) {
                salvarCacheMensagensAdmin(data.messages);
                if (data.messages.length) {
                    data.messages.forEach(enfileirarMensagem);
                    publicarMensagensAdmin('messages', data.messages);
                }
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
        if (!liderMensagensAdmin()) {
            fecharStreamMensagensAdmin();
            publicarMensagensAdmin('request', {});
            agendarChecagemLiderMensagens();
            return;
        }
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
                    const msg = JSON.parse(event.data || '{}');
                    salvarCacheMensagensAdmin([msg].concat(
                        ultimasMensagensAdmin.filter(item => idMensagem(item) !== idMensagem(msg))
                    ));
                    enfileirarMensagem(msg);
                    publicarMensagensAdmin('message', msg);
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
        if (!liderMensagensAdmin()) {
            fecharStreamMensagensAdmin();
            publicarMensagensAdmin('request', {});
            agendarChecagemLiderMensagens();
            return;
        }
        buscarMensagensAdmin();
        iniciarStreamMensagensAdmin();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => setTimeout(iniciarMensagensAdmin, 3500), { once: true });
    } else {
        setTimeout(iniciarMensagensAdmin, 3500);
    }
    setInterval(() => {
        if (!liderMensagensAdmin() && (streamMensagensAdmin || fallbackTimer)) {
            fecharStreamMensagensAdmin();
            limparFallbackMensagens();
            agendarChecagemLiderMensagens();
        }
    }, 20000);
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) {
            iniciarMensagensAdmin();
        }
    });
    window.addEventListener('pagehide', () => {
        fecharStreamMensagensAdmin();
        try { adminMessagesLeader?.release(); } catch (_err) {}
    });
})();
