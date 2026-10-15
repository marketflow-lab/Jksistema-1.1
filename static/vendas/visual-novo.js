/* Optional sales presentation. Financial data and actions remain owned by the legacy module. */
(() => {
    'use strict';
    if (window.JKVendasNova) return;

    const state = {
        active: false, tab: 'overview', query: '', returnsOnly: false,
        sort: 'value-desc', view: 'table', page: 1, pageSize: 20,
        snapshot: null, context: null, originalSearch: '', legacyHidden: false,
        legacyScroll: 0, originalBodyClass: false, selectedSku: null,
        graphSignature: null, graphError: false,
    };
    let root = null;
    let legacy = null;
    let unsubscribe = null;
    let productRenderKey = '';
    let highlightsRenderKey = '';
    let detailRenderKey = '';
    let renderFrame = null;
    let chartFrame = null;
    const moves = [];
    const themedCharts = new Map();
    const money = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });
    const integer = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 0 });
    const iconPaths = {
        calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18"/>',
        store: '<path d="M3 10 5 3h14l2 7M4 10v11h16V10M9 21v-7h6v7M3 10c0 3 4 3 4 0 0 3 5 3 5 0 0 3 5 3 5 0 0 3 4 3 4 0"/>',
        channel: '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4"/>',
        refresh: '<path d="M20 7v5h-5M4 17v-5h5M5.5 8a7 7 0 0 1 11.5-3L20 8M4 16l3 3a7 7 0 0 0 11.5-3"/>',
        pause: '<path d="M8 5v14M16 5v14"/>',
        money: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="12" cy="12" r="3"/><path d="M6 12h.01M18 12h.01"/>',
        cart: '<path d="M2 3h3l3 13h11l2-9H6"/><circle cx="9" cy="21" r="1"/><circle cx="18" cy="21" r="1"/>',
        box: '<path d="m3 7 9-4 9 4v10l-9 4-9-4V7Zm0 0 9 4 9-4m-9 4v10M7.5 5l9 4"/>',
        returns: '<path d="m9 3-6 6 6 6M3 9h12a6 6 0 0 1 0 12h-4"/>',
        search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5"/>',
        arrow: '<path d="M4 12h15m-5-5 5 5-5 5"/>',
        down: '<path d="m6 9 6 6 6-6"/>',
        close: '<path d="m6 6 12 12M6 18 18 6"/>',
        grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
        list: '<path d="M7 5h14M7 12h14M7 19h14M3 5h.1M3 12h.1M3 19h.1"/>',
        file: '<path d="M6 3h8l4 4v14H6V3Zm8 0v5h4M9 12h6m-6 4h6"/>',
    };
    const icon = name => `<svg class="vn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${iconPaths[name] || iconPaths.box}</svg>`;
    const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
    const normalize = value => String(value ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
    const numeric = value => Number.isFinite(Number(value)) ? Number(value) : 0;
    const formatMoney = value => value == null || !Number.isFinite(Number(value)) ? '—' : money.format(Number(value));
    const formatInteger = value => value == null || !Number.isFinite(Number(value)) ? '—' : integer.format(Number(value));
    const bridge = () => window.JKVendasLegacyBridge;
    const find = id => root?.querySelector(`#${id}`);
    const rows = () => Array.isArray(state.snapshot?.rows) ? state.snapshot.rows : [];
    const rowValue = row => numeric(row.displayValue ?? (numeric(row.valor) > 0 ? row.valor : row.valorDevolucao));

    function text(id, value) {
        const el = find(id);
        if (el && el.textContent !== String(value ?? '')) el.textContent = String(value ?? '');
    }

    function setValue(id, value) {
        const el = find(id);
        if (el && document.activeElement !== el && el.value !== String(value ?? '')) el.value = String(value ?? '');
    }

    function photo(row, className = '') {
        return `<span class="vn-photo ${className}"><span class="vn-photo-fallback">${icon('box')}<span>Sem imagem</span></span><img data-vendas-foto="${escape(row.sku)}" alt="${escape(row.produto || `Produto ${row.sku}`)}" loading="lazy" decoding="async"></span>`;
    }

    function call(method, ...args) {
        try {
            if (typeof bridge()?.[method] !== 'function') return;
            Promise.resolve(bridge()[method](...args)).catch(showError);
        } catch (error) {
            showError(error);
        }
    }

    function showError(error) {
        text('vnActionStatus', error?.message || 'Não foi possível concluir a ação. Tente novamente.');
    }

    function segmented(group, values) {
        return `<div class="vn-segmented" role="group" aria-label="${escape(group)}">${values.map(([value, label, attr]) => `<button type="button" ${attr}="${value}" aria-pressed="false">${label}</button>`).join('')}</div>`;
    }

    function build() {
        if (root) return;
        legacy = document.querySelector('body > .container') || document.querySelector('.container');
        root = document.createElement('main');
        root.id = 'vendasNova';
        root.className = 'vn-root';
        root.hidden = true;
        root.innerHTML = `
            <header class="vn-topbar">
                <div class="vn-identity"><span class="vn-brand" aria-label="JK Sistema">J<span>K</span></span><span class="vn-divider"></span><h1>Vendas</h1></div>
                <nav class="vn-tabs" role="tablist" aria-label="Seções de Vendas"><button type="button" id="vnTabOverview" role="tab" data-vn-tab="overview" aria-controls="vnOverview" aria-selected="true">Visão geral</button><button type="button" id="vnTabProducts" role="tab" data-vn-tab="products" aria-controls="vnProducts" aria-selected="false" tabindex="-1">Produtos</button></nav>
                <div class="vn-header-actions"><a class="vn-shortcut" href="/medias_compras.html">Médias e pedidos</a><a class="vn-shortcut" href="/importacoes.html">Importação</a><details class="vn-dropdown"><summary class="vn-btn">${icon('file')}Relatórios${icon('down')}</summary><div class="vn-dropdown-menu"><button type="button" data-vn-report="pareto">Pareto 80%</button><button type="button" data-vn-report="general">Relatório geral de SKUs</button></div></details><button type="button" id="vnBack" class="vn-btn">Versão antiga</button></div>
            </header>
            <section class="vn-filterbar" aria-label="Filtros das vendas">
                <div class="vn-filter-field"><span>Período</span><button type="button" class="vn-filter-button" id="vnPeriod" aria-haspopup="dialog">${icon('calendar')}<span id="vnPeriodLabel">Selecionar período</span>${icon('down')}</button></div>
                <label class="vn-filter-field"><span>Loja</span><span class="vn-select-wrap">${icon('store')}<select id="vnStore" aria-label="Loja"></select></span></label>
                <label class="vn-filter-field"><span>Loja virtual</span><span class="vn-select-wrap">${icon('channel')}<select id="vnUnit" aria-label="Loja virtual"></select></span></label>
                <div class="vn-sync-actions"><button type="button" id="vnSync" class="vn-btn vn-btn-primary">${icon('refresh')}Sincronizar</button><button type="button" id="vnPause" class="vn-btn vn-btn-pause" title="Pausar sincronização e atualização automática de todas as lojas">${icon('pause')}Pausar</button></div>
            </section>
            <section class="vn-rangebar" aria-label="Atalhos e opções do período"><div class="vn-control-group"><span class="vn-control-label">Período rápido</span>${segmented('Período rápido', [['3m', '3 meses', 'data-vn-period'], ['6m', '6 meses', 'data-vn-period'], ['1a', '1 ano', 'data-vn-period'], ['2a', '2 anos', 'data-vn-period'], ['max', 'Tudo', 'data-vn-period']])}</div><div class="vn-sync-options"><label><input type="checkbox" id="vnForce">Re-sincronizar</label><label><input type="checkbox" id="vnAuto">Atualizar automaticamente</label></div></section>
            <div class="vn-status" role="status" aria-live="polite"><span id="vnLoading" class="vn-loading" hidden></span><span id="vnStatus"></span><span id="vnPersistentStatus"></span><span id="vnActionStatus"></span></div>
            <section class="vn-kpis" aria-label="Indicadores do período">
                <article class="vn-kpi vn-kpi-primary"><h2>${icon('money')}Valor líquido</h2><div class="vn-kpi-value" id="vnNet">—</div><p id="vnGross">Bruto —</p></article>
                <article class="vn-kpi"><h2>${icon('cart')}Pedidos</h2><div class="vn-kpi-value" id="vnOrders">—</div><p>No período selecionado</p></article>
                <article class="vn-kpi"><h2>${icon('box')}Itens líquidos</h2><div class="vn-kpi-value" id="vnItems">—</div><p id="vnSoldReturned">Vendidos — · devolvidos —</p></article>
                <article class="vn-kpi vn-kpi-returns"><h2>${icon('returns')}Devoluções</h2><button type="button" id="vnReturns" class="vn-kpi-link" title="Ver produtos com devoluções"><span class="vn-kpi-value" id="vnReturnsValue">—</span>${icon('arrow')}</button><div class="vn-kpi-return-note"><p id="vnReturnedMoney">Devolvidos —</p><button type="button" id="vnOpenReturns" class="vn-text-btn">Abrir devoluções</button></div></article>
            </section>
            <section id="vnOverview" role="tabpanel" aria-labelledby="vnTabOverview">
                <div class="vn-analysis-grid"><article class="vn-panel vn-chart-panel"><div class="vn-panel-head"><div><h2>Evolução de vendas</h2><p id="vnChartSubtitle">Vendas e devoluções do período</p></div><span class="vn-live-label">Dados do sistema</span></div>
                    <div class="vn-chart-controls"><div class="vn-control-group"><span class="vn-control-label">Tipo de gráfico</span>${segmented('Tipo de gráfico', [['linha', 'Linha', 'data-vn-chart'], ['barra', 'Barras', 'data-vn-chart']])}</div><div class="vn-control-group"><span class="vn-control-label">Métrica</span>${segmented('Métrica', [['valor', 'Valor', 'data-vn-metric'], ['quantidade', 'Quantidade', 'data-vn-metric']])}</div><div class="vn-control-group"><span class="vn-control-label">Intervalo</span>${segmented('Intervalo', [['dia', 'Dia', 'data-vn-interval'], ['semana', 'Semana', 'data-vn-interval'], ['mes', 'Mês', 'data-vn-interval']])}</div></div>
                    <div class="vn-chart-secondary"><label><input type="checkbox" id="vnCompare">Comparar ano passado</label><label><input type="checkbox" id="vnStockGeneral">Estoque geral</label><label><input type="checkbox" id="vnStockSku">Estoque por SKU</label><label class="vn-stock-input"><span>SKU</span><input id="vnStockSkuInput" type="text" placeholder="Digite o SKU" aria-label="SKU para o gráfico de estoque"><button type="button" class="vn-text-btn" id="vnApplyStock">Aplicar</button></label></div>
                    <div id="vnChartState" class="vn-chart-state" role="status" aria-live="polite" hidden><span id="vnChartStateSpinner" class="vn-loading" aria-hidden="true"></span><p id="vnChartStateText">Carregando gráfico para estes filtros…</p><button type="button" id="vnChartRetry" class="vn-btn">Tentar novamente</button></div><div id="vnChartSlot" class="vn-chart-slot"></div><p class="vn-chart-note">Clique em um ponto ou barra para selecionar o período. A previsão, quando disponível, usa o cálculo do sistema.</p>
                </article><article class="vn-panel vn-featured-panel"><div class="vn-panel-head"><h2>Produto em destaque</h2><span class="vn-live-label">Maior valor</span></div><div id="vnFeatured" class="vn-featured-body"></div><p id="vnPhotoHint" class="vn-photo-hint" hidden>Selecione uma loja para visualizar as fotos.</p></article></div>
                <section class="vn-top-section" aria-labelledby="vnTopTitle"><div class="vn-section-head"><div><h2 id="vnTopTitle">Produtos em destaque</h2><p>Os produtos com maior valor no período</p></div><button type="button" id="vnViewAll" class="vn-text-btn">Ver todos${icon('arrow')}</button></div><div id="vnHighlights" class="vn-highlights"></div></section>
                <div id="vnStockSlot" class="vn-stock-slot"></div>
            </section>
            <section id="vnProducts" role="tabpanel" aria-labelledby="vnTabProducts" hidden>
                <div class="vn-products-toolbar"><div class="vn-products-heading"><h2>Produtos</h2><span class="vn-count" id="vnProductsCount"></span></div><div class="vn-products-tools"><label class="vn-search-wrap">${icon('search')}<input type="search" id="vnProductSearch" placeholder="Buscar SKU ou produto" aria-label="Buscar SKU ou produto"></label><label class="vn-sort-wrap"><span class="vn-sr-only">Ordenar produtos</span><select id="vnProductSort"><option value="value-desc">Maior valor</option><option value="orders-desc">Mais pedidos</option><option value="items-desc">Mais itens vendidos</option><option value="returns-desc">Mais devoluções</option><option value="name-asc">Nome do produto</option><option value="sku-asc">SKU</option></select></label><div class="vn-segmented vn-view-switch" role="group" aria-label="Visualização dos produtos"><button type="button" data-vn-view="cards" aria-pressed="false" aria-label="Visualizar em cards" title="Visualizar em cards">${icon('grid')}<span>Cards</span></button><button type="button" data-vn-view="table" aria-pressed="true" aria-label="Visualizar em tabela" title="Visualizar em tabela">${icon('list')}<span>Tabela</span></button></div></div></div>
                <div class="vn-products-filterline"><span id="vnProductsContext">Todos os produtos carregados do período</span><div><label><input type="checkbox" id="vnReturnsOnly">Com devoluções</label><button type="button" id="vnClearProducts" class="vn-text-btn">Limpar filtros</button></div></div>
                <div id="vnProductGrid" class="vn-product-grid" hidden></div>
                <div id="vnProductTableWrap" class="vn-table-wrap"><table class="vn-table"><thead><tr><th scope="col">Produto</th><th scope="col">SKU</th><th scope="col" class="vn-num">Pedidos</th><th scope="col" class="vn-num">Itens</th><th scope="col" class="vn-num">Devoluções</th><th scope="col" class="vn-num">Valor</th></tr></thead><tbody id="vnProductTableBody"></tbody></table></div>
                <div id="vnProductEmpty" class="vn-empty" hidden>${icon('search')}<h3>Nenhum produto encontrado</h3><p>Altere a busca ou os filtros para ver os produtos do período.</p><button type="button" class="vn-btn" data-vn-clear>Limpar busca e filtros</button></div>
                <div class="vn-pagination"><span id="vnPageLabel"></span><div><label>Por página<select id="vnPageSize" aria-label="Produtos por página"><option value="20">20</option><option value="50">50</option><option value="100">100</option><option value="all">Todos</option></select></label><button type="button" id="vnPagePrevious" class="vn-btn">Anterior</button><span id="vnPageNumber"></span><button type="button" id="vnPageNext" class="vn-btn">Próxima</button></div></div>
            </section>
            <footer class="vn-footer"><span id="vnFooterContext"></span><a href="/dashboard.html">Voltar ao início</a></footer>
            <dialog id="vnPeriodDialog" class="vn-dialog vn-period-dialog" aria-labelledby="vnPeriodTitle"><form id="vnPeriodForm"><div class="vn-dialog-head"><h2 id="vnPeriodTitle">Selecionar período</h2><button type="button" class="vn-close" data-vn-close="vnPeriodDialog" aria-label="Fechar seleção de período">${icon('close')}</button></div><div class="vn-dialog-body"><div class="vn-date-grid"><label>Data inicial<input id="vnDateStart" type="date" required></label><label>Data final<input id="vnDateEnd" type="date" required></label></div><label class="vn-month-label">Selecionar um mês<select id="vnMonth"><option value="">Selecione um mês</option></select></label><p id="vnDateError" class="vn-field-error" role="alert"></p></div><div class="vn-dialog-actions"><button type="button" class="vn-btn" data-vn-close="vnPeriodDialog">Cancelar</button><button type="submit" class="vn-btn vn-btn-primary">Aplicar período${icon('arrow')}</button></div></form></dialog>
            <dialog id="vnProductDialog" class="vn-dialog vn-detail-dialog" aria-labelledby="vnDetailTitle"><div class="vn-dialog-head"><h2 id="vnDetailTitle">Detalhes do produto</h2><button type="button" class="vn-close" data-vn-close="vnProductDialog" aria-label="Fechar detalhes do produto">${icon('close')}</button></div><div id="vnProductDetail"></div></dialog>`;
        if (legacy?.nextSibling) legacy.parentNode.insertBefore(root, legacy.nextSibling);
        else document.body.appendChild(root);
        bind();
        installChartTheme();
    }

    function bind() {
        root.addEventListener('click', event => {
            const button = event.target.closest('button');
            if (!button || !root.contains(button)) return;
            if (button.dataset.vnTab) return switchTab(button.dataset.vnTab, true);
            if (button.dataset.vnPeriod) {
                // The legacy engine owns inclusive month boundaries and refreshes the shared dates.
                return call('setChartPeriod', button.dataset.vnPeriod);
            }
            if (button.dataset.vnChart) return call('setChartType', button.dataset.vnChart);
            if (button.dataset.vnMetric) return call('setMetric', button.dataset.vnMetric);
            if (button.dataset.vnInterval) return call('setInterval', button.dataset.vnInterval);
            if (button.dataset.vnReport) {
                button.closest('details')?.removeAttribute('open');
                return call('openReport', button.dataset.vnReport);
            }
            if (button.dataset.vnClose) return find(button.dataset.vnClose)?.close();
            if (button.hasAttribute('data-vn-clear')) return clearProducts();
            if (button.dataset.vnSku != null) return openProduct(button.dataset.vnSku);
            switch (button.id) {
                case 'vnBack': return hide();
                case 'vnSync': return call('sync');
                case 'vnPause': return call('cancel');
                case 'vnPeriod': return openPeriod();
                case 'vnViewAll': return switchTab('products', true);
                case 'vnClearProducts': return clearProducts();
                case 'vnReturns':
                    switchTab('products', true);
                    state.returnsOnly = true;
                    state.sort = 'returns-desc';
                    return renderProducts(true);
                case 'vnOpenReturns': return document.querySelector('#resumo .card-devolucoes button')?.click();
                case 'vnApplyStock': return applyStock();
                case 'vnChartRetry': {
                    state.graphError = false;
                    renderGraphState(state.snapshot);
                    const options = state.snapshot?.chartOptions || {};
                    return call('setInterval', options.intervalo ?? options.intervaloGrafico ?? 'mes');
                }
                case 'vnPagePrevious': state.page = Math.max(1, state.page - 1); renderProducts(); scrollProducts(); return;
                case 'vnPageNext': state.page += 1; renderProducts(); scrollProducts(); return;
                case 'vnOpenSku': {
                    const sku = state.selectedSku;
                    find('vnProductDialog').close();
                    return call('openSku', sku);
                }
                default:
                    if (button.dataset.vnView) { state.view = button.dataset.vnView; renderProducts(true); }
            }
        });
        root.addEventListener('change', event => {
            const el = event.target;
            switch (el.id) {
                case 'vnStore': return call('setStore', el.value);
                case 'vnUnit': return call('setUnit', el.value);
                case 'vnForce': return copyCheckbox('forcarSync', el.checked);
                case 'vnAuto': return copyCheckbox('syncAutomatico', el.checked);
                case 'vnCompare': return call('setComparison', el.checked);
                case 'vnStockGeneral': return call('setStockGeneral', el.checked);
                case 'vnStockSku': return applyStock();
                case 'vnProductSort': state.sort = el.value; state.page = 1; return renderProducts();
                case 'vnPageSize': state.pageSize = el.value === 'all' ? 'all' : Number(el.value); state.page = 1; return renderProducts();
                case 'vnReturnsOnly': state.returnsOnly = el.checked; state.page = 1; return renderProducts();
                case 'vnMonth': {
                    if (!el.value) return;
                    const original = document.getElementById('mesAno');
                    const selected = original?.querySelector(`option[value="${CSS.escape(el.value)}"]`);
                    if (!selected) return;
                    find('vnPeriodDialog').close();
                    return call('setMonth', el.value);
                }
            }
        });
        find('vnProductSearch').addEventListener('input', event => {
            state.query = event.target.value;
            state.page = 1;
            renderProducts();
        });
        find('vnStockSkuInput').addEventListener('keydown', event => {
            if (event.key === 'Enter') { event.preventDefault(); applyStock(); }
        });
        root.querySelector('.vn-tabs').addEventListener('keydown', event => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            const next = event.key === 'Home' ? 'overview' : event.key === 'End' ? 'products' : state.tab === 'overview' ? 'products' : 'overview';
            switchTab(next, true);
            find(next === 'products' ? 'vnTabProducts' : 'vnTabOverview').focus();
        });
        find('vnPeriodForm').addEventListener('submit', event => {
            event.preventDefault();
            const start = find('vnDateStart').value;
            const end = find('vnDateEnd').value;
            if (!start || !end || start > end) {
                text('vnDateError', 'Escolha uma data final igual ou posterior à data inicial.');
                return;
            }
            find('vnPeriodDialog').close();
            call('setPeriod', start, end);
        });
        root.querySelectorAll('dialog').forEach(dialog => {
            dialog.addEventListener('click', event => { if (event.target === dialog) { const rect = dialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close(); } });
        });
        find('vnProductDialog').addEventListener('close', () => { state.selectedSku = null; detailRenderKey = ''; });
        root.addEventListener('load', event => {
            if (event.target.matches?.('img[data-vendas-foto]')) {
                event.target.closest('.vn-photo')?.classList.add('vn-photo-ready');
                photoHint();
            }
        }, true);
        root.addEventListener('error', event => {
            if (event.target.matches?.('img[data-vendas-foto]')) event.target.closest('.vn-photo')?.classList.remove('vn-photo-ready');
        }, true);
        document.addEventListener('keydown', event => {
            if (state.active && event.key === 'Escape') root.querySelectorAll('details[open]').forEach(details => details.removeAttribute('open'));
        });
        const nativeGraphStatus = document.getElementById('graficoObservacoesVendas');
        if (nativeGraphStatus && typeof MutationObserver === 'function') {
            const graphStatusObserver = new MutationObserver(() => {
                if (!state.active || !state.snapshot?.graphLoading) return;
                if (/nao foi possivel carregar observacoes do grafico/.test(normalize(nativeGraphStatus.textContent))) {
                    state.graphError = true;
                    renderGraphState(state.snapshot);
                }
            });
            graphStatusObserver.observe(nativeGraphStatus, { childList: true, subtree: true, characterData: true });
        }
    }

    function copyCheckbox(id, checked) {
        const source = document.getElementById(id);
        if (!source || source.checked === checked) return;
        source.checked = checked;
        source.dispatchEvent(new Event('change', { bubbles: true }));
        refresh();
    }

    function applyStock() {
        call('setStockSku', find('vnStockSku').checked, find('vnStockSkuInput').value.trim());
    }

    function openPeriod() {
        const filters = state.snapshot?.filters || {};
        find('vnDateStart').value = filters.inicio || '';
        find('vnDateEnd').value = filters.fim || '';
        text('vnDateError', '');
        const source = document.getElementById('mesAno');
        const items = Array.from(source?.options || []).map(option => [option.value, option.textContent]);
        setOptions(find('vnMonth'), items.length ? items : [['', 'Selecione um mês']], source?.value || '');
        find('vnPeriodDialog').showModal();
    }

    function setOptions(select, entries, value) {
        if (!select) return;
        if (value && !entries.some(([entryValue]) => String(entryValue) === String(value))) entries.push([value, value]);
        const key = JSON.stringify(entries);
        if (select.dataset.vnOptions !== key) {
            select.replaceChildren(...entries.map(([optionValue, label]) => {
                const option = document.createElement('option');
                option.value = optionValue;
                option.textContent = label;
                return option;
            }));
            select.dataset.vnOptions = key;
        }
        if (select.value !== String(value ?? '')) select.value = String(value ?? '');
    }

    function formatDate(iso) {
        const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
        return match ? `${match[3]}/${match[2]}/${match[1]}` : String(iso || '');
    }

    function controls(snapshot) {
        const filters = snapshot.filters || {};
        const options = snapshot.chartOptions || {};
        const periodLabel = filters.inicio && filters.fim ? `${formatDate(filters.inicio)} — ${formatDate(filters.fim)}` : 'Selecionar período';
        text('vnPeriodLabel', periodLabel);
        text('vnChartSubtitle', periodLabel === 'Selecionar período' ? 'Vendas e devoluções do período' : periodLabel);
        const stores = [['__todas', 'Todas as lojas'], ...(snapshot.stores || []).map(store => typeof store === 'string' ? [store, store] : [store.nome ?? store.name ?? store.value ?? '', store.nome ?? store.name ?? store.label ?? '']).filter(([value]) => value && value !== '__todas')];
        const units = (snapshot.units || []).map(unit => typeof unit === 'string' ? [unit, unit] : [unit.value ?? unit.id ?? '', unit.label ?? unit.text ?? unit.nome ?? unit.value ?? '']);
        setOptions(find('vnStore'), stores, filters.loja || '__todas');
        setOptions(find('vnUnit'), units.length ? units : [['__todos', 'Todas as lojas virtuais']], filters.unidade || '__todos');
        const mapped = {
            'data-vn-period': options.periodo ?? options.periodoGrafico,
            'data-vn-chart': options.tipo ?? options.tipoGrafico,
            'data-vn-metric': options.metrica ?? options.metricaGrafico,
            'data-vn-interval': options.intervalo ?? options.intervaloGrafico,
        };
        Object.entries(mapped).forEach(([attribute, value]) => root.querySelectorAll(`[${attribute}]`).forEach(button => {
            button.setAttribute('aria-pressed', String(button.getAttribute(attribute) === value));
            if (attribute === 'data-vn-period') {
                // Weekly views in the existing engine use the selected date range.
                button.disabled = (options.intervalo ?? options.intervaloGrafico) === 'semana';
                button.title = button.disabled ? 'Selecione Dia ou Mês para usar este atalho' : 'Meses completos e mês atual, conforme o cálculo do sistema';
            }
        }));
        find('vnCompare').checked = Boolean(options.compararAnoPassado);
        find('vnStockGeneral').checked = Boolean(options.mostrarEstoqueGeralGrafico);
        find('vnStockSku').checked = Boolean(options.mostrarEstoqueSkuGrafico);
        setValue('vnStockSkuInput', options.skuAtualGrafico || '');
        const syncButton = document.getElementById('btnSync');
        const pauseButton = document.getElementById('btnCancel');
        find('vnSync').disabled = Boolean(syncButton?.disabled);
        find('vnPause').disabled = Boolean(pauseButton?.disabled);
        find('vnSync').classList.toggle('vn-syncing', Boolean(snapshot.sync?.running || snapshot.sync?.emAndamento || snapshot.syncEmAndamento));
        find('vnForce').checked = Boolean(document.getElementById('forcarSync')?.checked);
        find('vnAuto').checked = Boolean(document.getElementById('syncAutomatico')?.checked);
        find('vnLoading').hidden = !snapshot.loading;
        const status = snapshot.status;
        text('vnStatus', typeof status === 'string' ? status : status?.text ?? status?.message ?? '');
        text('vnPersistentStatus', status?.automationText ?? status?.persistent ?? status?.persistentText ?? '');
        text('vnFooterContext', `${filters.loja && filters.loja !== '__todas' ? filters.loja : 'Todas as lojas'}${filters.inicio && filters.fim ? ` · ${periodLabel}` : ''}`);
        find('vnOpenReturns').disabled = !document.querySelector('#resumo .card-devolucoes button');
    }

    function renderTotals(snapshot) {
        const totals = snapshot.totals;
        const cards = Array.isArray(snapshot.summaryCards) ? snapshot.summaryCards : [];
        const cardValue = label => cards.find(card => normalize(card.label) === normalize(label))?.valueText || '—';
        const hasTotals = totals && typeof totals === 'object' && Object.keys(totals).length > 0;
        const value = (key, label, isMoney) => hasTotals && totals[key] != null ? (isMoney ? formatMoney(totals[key]) : formatInteger(totals[key])) : cardValue(label);
        text('vnNet', value('valorLiquido', 'Valor Líquido', true));
        text('vnGross', `Bruto ${value('valor', 'Valor Bruto', true)}`);
        text('vnOrders', value('pedidos', 'Pedidos'));
        text('vnItems', value('itensLiquidos', 'Itens Líquidos'));
        text('vnSoldReturned', `${value('itens', 'Itens Vendidos')} vendidos · ${value('itensDevolucao', 'Itens Devolvidos')} devolvidos`);
        text('vnReturnsValue', value('devolucoes', 'Devoluções'));
        text('vnReturnedMoney', `${value('valorDevolucao', 'Valor Devolvido', true)} devolvidos`);
    }

    function productCard(row) {
        return `<button type="button" class="vn-product-card" data-vn-sku="${escape(row.sku)}">${photo(row, 'vn-card-photo')}<span class="vn-product-copy"><span class="vn-product-sku">SKU ${escape(row.sku)}</span><span class="vn-product-name">${escape(row.produto || 'Produto sem descrição')}</span><span class="vn-product-price">${formatMoney(rowValue(row))}</span><span class="vn-product-meta">${formatInteger(row.pedidos)} pedidos · ${formatInteger(row.itens)} itens</span></span>${icon('arrow')}</button>`;
    }

    function renderHighlights() {
        const ordered = [...rows()].sort((a, b) => rowValue(b) - rowValue(a));
        const relevant = ordered.slice(0, 4);
        const key = JSON.stringify([state.context, Boolean(state.snapshot?.loading), relevant]);
        if (highlightsRenderKey === key) return;
        highlightsRenderKey = key;
        const featured = relevant[0];
        find('vnFeatured').innerHTML = featured ? `<button type="button" class="vn-featured-product" data-vn-sku="${escape(featured.sku)}">${photo(featured, 'vn-featured-photo')}<span class="vn-product-sku">SKU ${escape(featured.sku)}</span><span class="vn-featured-name">${escape(featured.produto || 'Produto sem descrição')}</span><span class="vn-featured-value">${formatMoney(rowValue(featured))}</span><span class="vn-featured-meta">${formatInteger(featured.pedidos)} pedidos<span>Ver detalhes ${icon('arrow')}</span></span></button>` : `<div class="vn-empty vn-empty-compact">${icon('box')}<p>${state.snapshot?.loading ? 'Carregando produtos…' : 'Sem produtos para o período selecionado.'}</p></div>`;
        find('vnHighlights').innerHTML = relevant.slice(1, 4).map(productCard).join('');
        text('vnViewAll', `Ver todos os ${formatInteger(rows().length)} produtos →`);
        photoHint();
        hydratePhotos();
    }

    function filteredRows() {
        const query = normalize(state.query.trim());
        let list = rows().filter(row => (!state.returnsOnly || numeric(row.devolucoes) > 0) && (!query || normalize(`${row.sku} ${row.produto}`).includes(query)));
        const collator = new Intl.Collator('pt-BR', { numeric: true, sensitivity: 'base' });
        const sorts = {
            'value-desc': (a, b) => rowValue(b) - rowValue(a),
            'orders-desc': (a, b) => numeric(b.pedidos) - numeric(a.pedidos),
            'items-desc': (a, b) => numeric(b.itens) - numeric(a.itens),
            'returns-desc': (a, b) => numeric(b.devolucoes) - numeric(a.devolucoes),
            'name-asc': (a, b) => collator.compare(a.produto, b.produto),
            'sku-asc': (a, b) => collator.compare(a.sku, b.sku),
        };
        list.sort(sorts[state.sort] || sorts['value-desc']);
        return list;
    }

    function renderProducts(force = false) {
        if (!root) return;
        const list = filteredRows();
        const size = state.pageSize === 'all' ? Math.max(1, list.length) : state.pageSize;
        const pages = Math.max(1, Math.ceil(list.length / size));
        state.page = Math.max(1, Math.min(state.page, pages));
        const start = (state.page - 1) * size;
        const shown = list.slice(start, start + size);
        const key = JSON.stringify([state.context, state.view, state.page, state.pageSize, shown]);
        if (force || productRenderKey !== key) {
            productRenderKey = key;
            if (state.view === 'cards') {
                find('vnProductGrid').innerHTML = shown.map(productCard).join('');
                find('vnProductTableBody').replaceChildren();
            } else {
                find('vnProductGrid').replaceChildren();
                find('vnProductTableBody').innerHTML = shown.map(row => `<tr><td><button type="button" class="vn-row-product" data-vn-sku="${escape(row.sku)}">${photo(row, 'vn-row-photo')}<span class="vn-row-product-name">${escape(row.produto || 'Produto sem descrição')}</span></button></td><td class="vn-sku">${escape(row.sku)}</td><td class="vn-num">${formatInteger(row.pedidos)}</td><td class="vn-num">${formatInteger(row.itens)}</td><td class="vn-num ${numeric(row.devolucoes) > 0 ? 'vn-returns-num' : ''}">${formatInteger(row.devolucoes)}</td><td class="vn-num vn-money" title="${numeric(row.valor) > 0 ? 'Valor vendido' : numeric(row.valorDevolucao) > 0 ? 'Valor devolvido' : 'Valor'}">${formatMoney(rowValue(row))}</td></tr>`).join('');
            }
            hydratePhotos();
        }
        find('vnProductGrid').hidden = state.view !== 'cards' || list.length === 0;
        find('vnProductTableWrap').hidden = state.view !== 'table' || list.length === 0;
        find('vnProductEmpty').hidden = list.length > 0;
        text('vnProductsCount', `${formatInteger(rows().length)} produtos`);
        text('vnProductsContext', state.query || state.returnsOnly ? `${formatInteger(list.length)} de ${formatInteger(rows().length)} produtos correspondem aos filtros da lista` : 'Todos os produtos carregados do período');
        text('vnPageLabel', list.length ? `${formatInteger(start + 1)}–${formatInteger(start + shown.length)} de ${formatInteger(list.length)} produtos` : '0 produtos');
        text('vnPageNumber', `${state.page} de ${pages}`);
        setValue('vnProductSearch', state.query);
        setValue('vnProductSort', state.sort);
        setValue('vnPageSize', state.pageSize);
        find('vnReturnsOnly').checked = state.returnsOnly;
        find('vnPagePrevious').disabled = state.page <= 1;
        find('vnPageNext').disabled = state.page >= pages;
        root.querySelectorAll('[data-vn-view]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.vnView === state.view)));
    }

    function clearProducts() {
        state.query = '';
        state.returnsOnly = false;
        state.page = 1;
        find('vnProductSearch').value = '';
        renderProducts(true);
    }

    function switchTab(tab, reset) {
        state.tab = tab === 'products' ? 'products' : 'overview';
        if (state.tab === 'products' && reset) clearProducts();
        find('vnOverview').hidden = state.tab !== 'overview';
        find('vnProducts').hidden = state.tab !== 'products';
        root.querySelectorAll('[data-vn-tab]').forEach(button => {
            const selected = button.dataset.vnTab === state.tab;
            button.setAttribute('aria-selected', String(selected));
            button.tabIndex = selected ? 0 : -1;
        });
        if (state.tab === 'products') { renderProducts(); scrollProducts(); }
        else resizeCharts();
        hydratePhotos();
    }

    function scrollProducts() {
        requestAnimationFrame(() => {
            find('vnProductTableWrap').scrollTop = 0;
            find('vnProducts')?.scrollIntoView({ block: 'start', behavior: 'auto' });
        });
    }

    function openProduct(sku) {
        if (!rows().some(row => String(row.sku) === String(sku))) return;
        state.selectedSku = String(sku);
        renderDetail();
        find('vnProductDialog').showModal();
        hydratePhotos();
    }

    function renderDetail() {
        if (state.selectedSku == null) return;
        const row = rows().find(item => String(item.sku) === state.selectedSku);
        if (!row) { find('vnProductDialog').close(); return; }
        const key = JSON.stringify([state.context, row]);
        if (detailRenderKey === key) return;
        detailRenderKey = key;
        find('vnProductDetail').innerHTML = `<div class="vn-detail-body">${photo(row, 'vn-detail-photo')}<div class="vn-detail-copy"><span class="vn-product-sku">SKU ${escape(row.sku)}</span><h3>${escape(row.produto || 'Produto sem descrição')}</h3><div class="vn-detail-value">${formatMoney(rowValue(row))}</div><dl class="vn-detail-metrics"><div><dt>Pedidos</dt><dd>${formatInteger(row.pedidos)}</dd></div><div><dt>Itens vendidos</dt><dd>${formatInteger(row.itens)}</dd></div><div><dt>Devoluções</dt><dd>${formatInteger(row.devolucoes)}</dd></div><div><dt>Itens devolvidos</dt><dd>${formatInteger(row.itensDevolucao)}</dd></div><div><dt>Valor devolvido</dt><dd>${formatMoney(row.valorDevolucao)}</dd></div></dl></div></div><div class="vn-dialog-actions"><button type="button" class="vn-btn vn-btn-primary" id="vnOpenSku">Abrir análise SKU${icon('arrow')}</button></div>`;
        hydratePhotos();
    }

    function hydratePhotos() {
        if (state.active && root) window.JKVendasNovaFotos?.hydrate(root, state.snapshot);
    }

    function photoHint() {
        const selectedStore = state.snapshot?.filters?.loja;
        const showHint = selectedStore === '__todas' && !root.querySelector('.vn-featured-photo.vn-photo-ready');
        if (find('vnPhotoHint')) find('vnPhotoHint').hidden = !showHint;
    }

    function renderGraphState(snapshot) {
        if (!snapshot || !root) return;
        const options = snapshot.chartOptions || {};
        const signature = JSON.stringify([snapshot.contextKey, options.periodo ?? options.periodoGrafico,
            options.intervalo ?? options.intervaloGrafico, options.tipo ?? options.tipoGrafico,
            options.metrica ?? options.metricaGrafico, options.compararAnoPassado,
            options.mostrarEstoqueGeralGrafico, options.mostrarEstoqueSkuGrafico, options.skuAtualGrafico]);
        if (state.graphSignature !== signature) {
            state.graphSignature = signature;
            state.graphError = false;
        }
        const ready = !snapshot.graphLoading && snapshot.graph?.data != null;
        const wasHidden = find('vnChartSlot').hidden;
        // Only the presentation slots are hidden. Native canvas state and wrapper visibility remain owned by the engine.
        find('vnChartSlot').hidden = !ready;
        find('vnStockSlot').hidden = !ready;
        find('vnChartState').hidden = ready;
        if (ready) {
            state.graphError = false;
            if (wasHidden) resizeCharts();
            return;
        }
        find('vnChartStateSpinner').hidden = state.graphError || !snapshot.graphLoading;
        text('vnChartStateText', state.graphError ? 'Não foi possível carregar o gráfico para estes filtros.'
            : snapshot.graphLoading ? 'Carregando gráfico para estes filtros…' : 'Gráfico indisponível para estes filtros.');
    }

    function acceptSnapshot(snapshot) {
        if (!state.active || !snapshot) return;
        const nextContext = String(snapshot.contextKey ?? `${snapshot.clientId || ''}|${snapshot.filters?.loja || ''}|${snapshot.filters?.unidade || ''}|${snapshot.filters?.inicio || ''}|${snapshot.filters?.fim || ''}`);
        if (state.context !== null && state.context !== nextContext) {
            window.JKVendasNovaFotos?.release(root);
            state.query = '';
            state.returnsOnly = false;
            state.page = 1;
            find('vnProductSearch').value = '';
            root.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
            productRenderKey = '';
            highlightsRenderKey = '';
            detailRenderKey = '';
        }
        state.context = nextContext;
        state.snapshot = snapshot;
        // Invalidate previous visuals before the next paint, including when the replacement request fails.
        renderGraphState(snapshot);
        if (renderFrame) cancelAnimationFrame(renderFrame);
        renderFrame = requestAnimationFrame(() => {
            renderFrame = null;
            if (!state.active) return;
            controls(state.snapshot);
            renderTotals(state.snapshot);
            renderHighlights();
            renderProducts();
            renderDetail();
        });
    }

    function moveCharts() {
        const mainCanvas = document.getElementById('graficoVendas');
        const wrappers = [[mainCanvas?.closest('.grafico-wrapper'), find('vnChartSlot')], [document.getElementById('graficoEstoqueWrapper'), find('vnStockSlot')], [document.getElementById('graficoSkusEstoqueWrapper'), find('vnStockSlot')]];
        wrappers.forEach(([node, destination]) => {
            if (!node || !destination || moves.some(move => move.node === node)) return;
            const anchor = document.createComment('vendas-nova-chart-origin');
            node.parentNode.insertBefore(anchor, node);
            moves.push({ node, anchor });
            destination.appendChild(node);
        });
    }

    function restoreCharts() {
        moves.splice(0).forEach(({ node, anchor }) => {
            if (anchor.parentNode) anchor.parentNode.replaceChild(node, anchor);
        });
    }

    function chartList() {
        const charts = bridge()?.getChartInstances?.();
        const values = Array.isArray(charts) ? charts : charts && typeof charts === 'object' ? Object.values(charts) : [];
        return values.filter(chart => chart?.canvas && typeof chart.update === 'function');
    }

    function applyChartTheme(chart) {
        if (!state.active || !['graficoVendas', 'graficoEstoque', 'graficoSkusEstoque'].includes(chart.canvas?.id)) return;
        themedCharts.forEach((_saved, previousChart) => {
            if (previousChart !== chart && (!previousChart.ctx || !previousChart.canvas?.isConnected)) themedCharts.delete(previousChart);
        });
        let saved = themedCharts.get(chart);
        if (!saved) { saved = []; themedCharts.set(chart, saved); }
        const change = (object, key, value) => {
            if (!object || object[key] === value) return;
            if (!saved.some(item => item.object === object && item.key === key)) saved.push({ object, key, own: Object.prototype.hasOwnProperty.call(object, key), value: object[key] });
            object[key] = value;
        };
        const options = chart.config.options;
        change(options, 'maintainAspectRatio', false);
        change(options?.plugins?.legend?.labels, 'color', '#727b96');
        const tooltip = options?.plugins?.tooltip;
        change(tooltip, 'backgroundColor', '#ffffff');
        change(tooltip, 'titleColor', '#211c33');
        change(tooltip, 'bodyColor', '#57516b');
        change(tooltip, 'borderColor', '#e5ddf2');
        change(tooltip, 'borderWidth', 1);
        Object.values(options?.scales || {}).forEach(scale => {
            change(scale.ticks, 'color', '#8790a8');
            change(scale.title, 'color', '#786588');
            change(scale.grid, 'color', '#eceef6');
            change(scale.border, 'color', '#e2e6ef');
        });
        (chart.config.data.datasets || []).forEach((dataset, index) => {
            const label = normalize(dataset.label);
            const color = dataset.isForecast || label.includes('previs') ? '#c59a3e' : label.includes('devolu') ? '#e681aa' : label.includes('passado') || label.includes('anterior') ? '#b5a2da' : label.includes('estoque') || label.includes('saldo') ? '#60ae9e' : index === 0 ? '#7945d6' : '#aa8bdf';
            change(dataset, 'borderColor', color);
            change(dataset, 'backgroundColor', chart.config.type === 'bar' || dataset.type === 'bar' ? `${color}b8` : `${color}22`);
            change(dataset, 'pointBackgroundColor', color);
            change(dataset, 'topLabelColor', dataset.isForecast || label.includes('previs') ? '#936d26' : label.includes('devolu') ? '#ad527a' : '#7953ab');
            if (chart.config.type === 'bar' || dataset.type === 'bar') change(dataset, 'borderRadius', 4);
        });
    }

    function installChartTheme() {
        if (!window.Chart?.register || window.Chart.registry?.plugins?.get?.('jkVendasNovaTheme')) return;
        window.Chart.register({ id: 'jkVendasNovaTheme', beforeUpdate: applyChartTheme });
    }

    function restoreChartTheme() {
        themedCharts.forEach((saved, chart) => {
            saved.forEach(({ object, key, own, value }) => {
                if (own) object[key] = value;
                else delete object[key];
            });
            if (chart.ctx && chart.canvas?.isConnected) chart.update('none');
        });
        themedCharts.clear();
    }

    function resizeCharts() {
        if (chartFrame) cancelAnimationFrame(chartFrame);
        chartFrame = requestAnimationFrame(() => {
            chartFrame = null;
            chartList().forEach(chart => { if (state.active) applyChartTheme(chart); chart.resize(); chart.update('none'); });
            bridge()?.resizeCharts?.();
        });
    }

    function show() {
        if (state.active) return;
        if (!bridge()) return;
        build();
        state.originalSearch = bridge().getSnapshot()?.filters?.search ?? document.getElementById('filtroTexto')?.value ?? '';
        state.legacyScroll = window.scrollY;
        state.legacyHidden = Boolean(legacy?.hidden);
        state.originalBodyClass = document.body.classList.contains('vendas-layout-novo');
        state.active = true;
        document.getElementById('btnVersaoNovaVendas')?.setAttribute('aria-expanded', 'true');
        state.context = null;
        state.graphSignature = null;
        state.graphError = false;
        state.query = '';
        state.returnsOnly = false;
        state.page = 1;
        find('vnProductSearch').value = '';
        root.hidden = false;
        document.body.classList.add('vendas-layout-novo');
        moveCharts();
        if (legacy) legacy.hidden = true;
        unsubscribe = bridge().subscribe(acceptSnapshot, { immediate: true });
        call('setSearch', '');
        switchTab('overview', false);
        acceptSnapshot(bridge().getSnapshot());
        resizeCharts();
        window.scrollTo({ top: 0, behavior: 'auto' });
        find('vnBack').focus({ preventScroll: true });
        window.dispatchEvent(new CustomEvent('jk:vendas-new-layout', { detail: { active: true } }));
    }

    function hide() {
        if (!state.active) return;
        state.active = false;
        document.getElementById('btnVersaoNovaVendas')?.setAttribute('aria-expanded', 'false');
        if (typeof unsubscribe === 'function') unsubscribe();
        unsubscribe = null;
        if (renderFrame) cancelAnimationFrame(renderFrame);
        renderFrame = null;
        root.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
        root.querySelectorAll('details[open]').forEach(details => details.removeAttribute('open'));
        window.JKVendasNovaFotos?.release(root);
        restoreCharts();
        restoreChartTheme();
        if (legacy) legacy.hidden = state.legacyHidden;
        root.hidden = true;
        if (!state.originalBodyClass) document.body.classList.remove('vendas-layout-novo');
        call('setSearch', state.originalSearch);
        resizeCharts();
        window.scrollTo({ top: state.legacyScroll, behavior: 'auto' });
        document.getElementById('btnVersaoNovaVendas')?.focus({ preventScroll: true });
        window.dispatchEvent(new CustomEvent('jk:vendas-new-layout', { detail: { active: false } }));
    }

    function refresh() {
        if (state.active) acceptSnapshot(bridge()?.getSnapshot());
    }

    function init() {
        document.getElementById('btnVersaoNovaVendas')?.addEventListener('click', show);
        window.addEventListener('jk:vendas-view-state', event => {
            // subscribe is the primary delivery path; avoid copying the complete list twice.
            if (state.active && typeof unsubscribe !== 'function') acceptSnapshot(event.detail || bridge()?.getSnapshot());
        });
        window.addEventListener('resize', () => { if (state.active) resizeCharts(); });
    }

    window.JKVendasNova = Object.freeze({ show, hide, isActive: () => state.active, refresh });
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
    else init();
})();
