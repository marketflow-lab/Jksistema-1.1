/* Ponte de apresentação: mantém o motor, os filtros e as ações de Vendas como fonte única. */
(function instalarPonteVisualVendas() {
    'use strict';

    if (window.JKVendasLegacyBridge) return;

    const listeners = new Set();
    let publicationPending = false;
    let revision = 0;
    let capturedRows = [];
    let rowsContext = null;
    let rowsSearch = '';
    let capturedTotals = null;
    let capturedCards = [];
    let totalsContext = null;
    let capturedGraph = null;
    let capturedComparison = null;
    let graphContext = null;
    let skuOpenSequence = 0;

    function copy(value, seen = new WeakMap()) {
        if (value === null || typeof value !== 'object') return value;
        if (seen.has(value)) return seen.get(value);
        const output = Array.isArray(value) ? [] : {};
        seen.set(value, output);
        Object.keys(value).forEach(key => {
            if (key === '__proto__' || key === 'constructor' || key === 'prototype') return;
            if (typeof value[key] !== 'function') output[key] = copy(value[key], seen);
        });
        return output;
    }

    function currentClientId() {
        try {
            return String(typeof obterClientId === 'function' ? obterClientId() || '' : (typeof clientId !== 'undefined' ? clientId || '' : ''));
        } catch (_error) {
            return '';
        }
    }

    function legacyClientMatches() {
        return typeof clientId === 'undefined' || !clientId || String(clientId) === currentClientId();
    }

    function readFilters() {
        return {
            inicio: typeof getDataIniISO === 'function' ? getDataIniISO() : '',
            fim: typeof getDataFimISO === 'function' ? getDataFimISO() : '',
            loja: typeof lojaSelecionada !== 'undefined' ? lojaSelecionada || '__todas' : '__todas',
            unidade: typeof unidadeNegocioSelect !== 'undefined' ? unidadeNegocioSelect?.value || '__todos' : '__todos',
            search: typeof filtroTexto !== 'undefined' ? filtroTexto?.value || '' : ''
        };
    }

    function contextKey(filters = readFilters()) {
        return JSON.stringify([currentClientId(), filters.inicio, filters.fim, filters.loja, filters.unidade]);
    }

    function readChartOptions() {
        const periodo = typeof periodoGrafico !== 'undefined' ? periodoGrafico : 'manual';
        const tipo = typeof tipoGrafico !== 'undefined' ? tipoGrafico : 'linha';
        const metrica = typeof metricaGrafico !== 'undefined' ? metricaGrafico : 'valor';
        const intervalo = typeof intervaloGrafico !== 'undefined' ? intervaloGrafico : 'dia';
        return {
            periodo, tipo, metrica, intervalo,
            periodoGrafico: periodo, tipoGrafico: tipo, metricaGrafico: metrica, intervaloGrafico: intervalo,
            compararAnoPassado: typeof compararAnoPassado !== 'undefined' && Boolean(compararAnoPassado),
            mostrarEstoqueGeralGrafico: typeof mostrarEstoqueGeralGrafico !== 'undefined' && Boolean(mostrarEstoqueGeralGrafico),
            mostrarEstoqueSkuGrafico: typeof mostrarEstoqueSkuGrafico !== 'undefined' && Boolean(mostrarEstoqueSkuGrafico),
            skuAtualGrafico: typeof skuAtualGrafico !== 'undefined' ? skuAtualGrafico || null : null
        };
    }

    function chartContextKey() {
        const options = readChartOptions();
        return JSON.stringify([contextKey(), options.periodo, options.tipo, options.metrica, options.intervalo, options.compararAnoPassado,
            options.mostrarEstoqueGeralGrafico, options.mostrarEstoqueSkuGrafico, options.skuAtualGrafico]);
    }

    function number(value) {
        const numeric = Number(value || 0);
        return Number.isFinite(numeric) ? numeric : 0;
    }

    function normalizeRow(row) {
        return {
            ...copy(row || {}),
            sku: String(row?.sku || ''),
            produto: String(row?.produto || '-'),
            pedidos: number(row?.pedidos),
            itens: number(row?.itens ?? row?.quantidade),
            devolucoes: number(row?.devolucoes),
            itensDevolucao: number(row?.itensDevolucao),
            valorDevolucao: number(row?.valorDevolucao),
            valor: number(row?.valor),
            displayValue: typeof obterValorExibicaoSku === 'function' ? number(obterValorExibicaoSku(row)) : number(row?.valor)
        };
    }

    function readSummaryCards() {
        if (typeof resumo === 'undefined' || !resumo) return [];
        return Array.from(resumo.children || []).map(card => ({
            label: card.querySelector('h3')?.textContent || '',
            valueText: card.querySelector('.val')?.textContent || ''
        }));
    }

    function getSnapshot() {
        const filters = readFilters();
        const key = contextKey(filters);
        const clientMatches = legacyClientMatches();
        const rowsMatch = clientMatches && rowsContext === key && rowsSearch === filters.search;
        const totalsMatch = clientMatches && totalsContext === key;
        const graphMatch = clientMatches && graphContext === chartContextKey();
        const nativeStatus = typeof statusEl !== 'undefined' ? statusEl : null;
        const persistentStatus = document.getElementById('syncPersistenteStatus');
        return {
            revision,
            clientId: currentClientId(),
            contextKey: key,
            loading: !rowsMatch,
            graphLoading: !graphMatch,
            rows: rowsMatch ? copy(capturedRows) : [],
            totals: totalsMatch ? copy(capturedTotals) : null,
            summaryCards: totalsMatch ? copy(capturedCards) : [],
            filters,
            stores: clientMatches && typeof lojasDisponiveis !== 'undefined' && Array.isArray(lojasDisponiveis) ? copy(lojasDisponiveis) : [],
            units: clientMatches && typeof unidadeNegocioSelect !== 'undefined' ? Array.from(unidadeNegocioSelect?.options || []).map(option => ({
                value: String(option.value || ''), label: option.textContent || option.label || '', disabled: Boolean(option.disabled)
            })) : [],
            graph: { data: graphMatch ? copy(capturedGraph) : null, comparison: graphMatch ? copy(capturedComparison) : null },
            chartOptions: readChartOptions(),
            sync: clientMatches ? copy(window.__jkVendasSyncMonitor?.getLastPayload?.() || null) : null,
            status: {
                text: clientMatches ? nativeStatus?.textContent || '' : '',
                className: nativeStatus?.className || '',
                automationText: clientMatches ? persistentStatus?.textContent || '' : '',
                syncDisabled: typeof btnSync !== 'undefined' && Boolean(btnSync?.disabled),
                cancelDisabled: typeof btnCancel !== 'undefined' && Boolean(btnCancel?.disabled)
            }
        };
    }

    function publish() {
        publicationPending = false;
        revision += 1;
        listeners.forEach(listener => {
            try { listener(getSnapshot()); } catch (_error) { /* Uma interface não interrompe o motor nem outros consumidores. */ }
        });
        try { window.dispatchEvent(new CustomEvent('jk:vendas-view-state', { detail: getSnapshot() })); } catch (_error) { /* Preserva o fluxo legado. */ }
    }

    function schedulePublish() {
        if (publicationPending) return;
        publicationPending = true;
        if (typeof queueMicrotask === 'function') queueMicrotask(publish);
        else Promise.resolve().then(publish);
    }

    function subscribe(listener, options = {}) {
        if (typeof listener !== 'function') throw new TypeError('Informe uma função para acompanhar Vendas.');
        listeners.add(listener);
        if (options.immediate !== false) {
            try { listener(getSnapshot()); } catch (_error) { /* Mantém os demais consumidores ativos. */ }
        }
        return () => listeners.delete(listener);
    }

    function decorate(original, capture) {
        return function vendasRenderComNotificacao(...args) {
            const result = original.apply(this, args);
            try { capture?.(args); } catch (_error) { /* A apresentação não altera retorno ou exceções do motor. */ }
            schedulePublish();
            return result;
        };
    }

    function captureSummary() {
        capturedTotals = typeof dadosModoResumo !== 'undefined' && dadosModoResumo && typeof resumoTotaisVendas !== 'undefined'
            ? copy(resumoTotaisVendas) : null;
        capturedCards = readSummaryCards();
        totalsContext = contextKey();
    }

    if (typeof renderTabelaAgrupada === 'function') {
        renderTabelaAgrupada = decorate(renderTabelaAgrupada, args => {
            capturedRows = Array.isArray(args[0]) ? args[0].map(normalizeRow) : [];
            rowsContext = contextKey();
            rowsSearch = readFilters().search;
        });
    }
    if (typeof renderResumoTotaisVendas === 'function') {
        renderResumoTotaisVendas = decorate(renderResumoTotaisVendas, args => {
            capturedTotals = copy(args[0] || null);
            capturedCards = readSummaryCards();
            totalsContext = contextKey();
        });
    }
    if (typeof renderResumo === 'function') renderResumo = decorate(renderResumo, captureSummary);
    if (typeof renderizarGrafico === 'function') {
        renderizarGrafico = decorate(renderizarGrafico, args => {
            capturedGraph = copy(args[0] || null);
            capturedComparison = copy(args[1] || null);
            graphContext = chartContextKey();
        });
    }
    if (typeof renderBotoesLojas === 'function') renderBotoesLojas = decorate(renderBotoesLojas);
    if (typeof atualizarUnidadesNegocio === 'function') atualizarUnidadesNegocio = decorate(atualizarUnidadesNegocio);

    function notifyAction(action) {
        const result = action();
        schedulePublish();
        if (result && typeof result.then === 'function') result.then(schedulePublish, schedulePublish);
        return result;
    }

    function click(element) {
        if (!element || element.disabled) return false;
        return notifyAction(() => { element.click(); return true; });
    }

    function change(element, value, checkbox = false) {
        if (!element || element.disabled) return false;
        return notifyAction(() => {
            if (checkbox) element.checked = Boolean(value); else element.value = String(value ?? '');
            return element.dispatchEvent(new Event('change', { bubbles: true }));
        });
    }

    function graphButton(attribute, value, allowed) {
        if (!allowed.includes(value)) throw new RangeError('Opção de gráfico inválida.');
        const button = Array.from(document.querySelectorAll(`.grafico-btn[${attribute}]`)).find(item => item.getAttribute(attribute) === value);
        return click(button);
    }

    function setStore(name) {
        const value = String(name || '');
        const stores = typeof lojasDisponiveis !== 'undefined' ? lojasDisponiveis || [] : [];
        if (value !== '__todas' && !stores.some(store => String(store?.nome || '') === value)) throw new RangeError('Selecione uma loja disponível.');
        return notifyAction(() => { lojaSelecionada = value; return atualizarSelecao(); });
    }

    function setUnit(value) {
        const selected = String(value || '__todos');
        if (!Array.from(unidadeNegocioSelect?.options || []).some(option => option.value === selected)) throw new RangeError('Selecione uma loja virtual disponível.');
        return change(unidadeNegocioSelect, selected);
    }

    function validDate(value) {
        if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value))) return false;
        const date = new Date(`${value}T12:00:00Z`);
        return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
    }

    function setPeriod(start, end) {
        if (!validDate(start) || !validDate(end) || start > end) throw new RangeError('Informe um período válido.');
        return notifyAction(() => {
            sincronizarPeriodoTopo(start, end);
            return atualizarPeriodoComRecarregamento(true);
        });
    }

    function setMonth(value) {
        const month = String(value || '');
        if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(month)) throw new RangeError('Informe um mês válido.');
        if (typeof selecionarMes === 'function') return notifyAction(() => selecionarMes(month));
        return change(mesAno, month);
    }

    function setStockSku(enabled, sku) {
        return notifyAction(() => {
            if (sku !== undefined && typeof graficoEstoqueSkuInput !== 'undefined' && graficoEstoqueSkuInput) graficoEstoqueSkuInput.value = String(sku || '').trim();
            return change(typeof mostrarEstoqueSkuCheck !== 'undefined' ? mostrarEstoqueSkuCheck : document.getElementById('mostrarEstoqueSku'), enabled, true);
        });
    }

    function setSearch(value) {
        if (typeof filtroTexto === 'undefined' || !filtroTexto) return false;
        return notifyAction(() => {
            filtroTexto.value = String(value || '');
            return filtroTexto.dispatchEvent(new Event('input', { bubbles: true }));
        });
    }

    function openSku(sku) {
        const value = String(sku || '');
        const sequence = ++skuOpenSequence;
        const key = contextKey();
        if (!getSnapshot().rows.some(row => row.sku === value)) return false;
        const findRow = () => typeof tBody !== 'undefined' && tBody ? Array.from(tBody.querySelectorAll('tr[data-sku]')).find(row => row.getAttribute('data-sku') === value) : null;
        const row = findRow();
        if (row) return click(row);
        return new Promise(resolve => {
            let frames = 0;
            const attempt = () => {
                if (sequence !== skuOpenSequence || contextKey() !== key || frames++ >= 60) return resolve(false);
                const pendingRow = findRow();
                if (pendingRow) return resolve(click(pendingRow));
                requestAnimationFrame(attempt);
            };
            requestAnimationFrame(attempt);
        });
    }

    function getChartInstances() {
        return {
            sales: typeof chartInstance !== 'undefined' ? chartInstance : null,
            stock: typeof chartEstoqueInstance !== 'undefined' ? chartEstoqueInstance : null,
            stockSkus: typeof chartSkusEstoqueInstance !== 'undefined' ? chartSkusEstoqueInstance : null
        };
    }

    function resizeCharts() {
        Object.values(getChartInstances()).forEach(chart => {
            try { chart?.resize?.(); } catch (_error) { /* Um canvas oculto não impede a troca de versão. */ }
        });
    }

    const bridge = {
        getSnapshot, subscribe, setStore, setUnit, setPeriod, setMonth,
        setChartPeriod: value => graphButton('data-periodo', value, ['3m', '6m', '1a', '2a', 'max']),
        setChartType: value => graphButton('data-tipo', value, ['linha', 'barra']),
        setMetric: value => graphButton('data-metrica', value, ['valor', 'quantidade']),
        setInterval: value => graphButton('data-intervalo', value, ['dia', 'semana', 'mes']),
        setComparison: value => change(typeof compareAnoPassado !== 'undefined' ? compareAnoPassado : document.getElementById('compareAnoPassado'), value, true),
        setStockGeneral: value => change(typeof mostrarEstoqueGeralCheck !== 'undefined' ? mostrarEstoqueGeralCheck : document.getElementById('mostrarEstoqueGeral'), value, true),
        setStockSku, setSearch,
        sync: () => click(typeof btnSync !== 'undefined' ? btnSync : document.getElementById('btnSync')),
        cancel: () => click(typeof btnCancel !== 'undefined' ? btnCancel : document.getElementById('btnCancel')),
        openReport: name => {
            if (!['pareto', 'general'].includes(name)) throw new RangeError('Relatório inválido.');
            return click(document.getElementById(name === 'pareto' ? 'btnPareto80' : 'btnRelatorioGeralSkus'));
        },
        openSku, getChartInstances, resizeCharts
    };
    window.JKVendasLegacyBridge = Object.freeze(bridge);

    const observedNodes = ['status', 'syncPersistenteStatus', 'lojaBotoes', 'unidadeNegocioSelect', 'btnSync', 'btnCancel'];
    if (typeof MutationObserver === 'function') {
        const observer = new MutationObserver(schedulePublish);
        observedNodes.forEach(id => {
            const node = document.getElementById(id);
            if (node) observer.observe(node, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['class', 'disabled'] });
        });
    }
    ['dataIni', 'dataFim', 'mesAno', 'unidadeNegocioSelect', 'compareAnoPassado', 'mostrarEstoqueGeral', 'mostrarEstoqueSku', 'graficoEstoqueSkuInput'].forEach(id => document.getElementById(id)?.addEventListener('change', schedulePublish));
    document.getElementById('filtroTexto')?.addEventListener('input', schedulePublish);
    document.getElementById('lojaBotoes')?.addEventListener('click', schedulePublish);
    document.querySelectorAll('.grafico-btn').forEach(button => button.addEventListener('click', schedulePublish));
    window.addEventListener('jk:vendas-sync-state', schedulePublish);
    window.addEventListener('jk:vendas-sync-finished', schedulePublish);
    window.addEventListener('storage', event => { if (event.key === 'user_data' || event.key === 'access_token') schedulePublish(); });
}());
