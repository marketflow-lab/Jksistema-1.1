(function vendasPareto80Module() {
    'use strict';

    const REPORT_URL = '/api/vendas/relatorios/pareto-80';
    const EXPORT_URL = '/api/vendas/relatorios/pareto-80/exportar';
    const REPORT_TIMEOUT_MS = 120000;
    let overlay = null;
    let lastFocusedElement = null;

    function formatCurrencyPareto(value) {
        return Number(value || 0).toLocaleString('pt-BR', {
            style: 'currency',
            currency: 'BRL'
        });
    }

    function formatPercentPareto(value) {
        return Number(value || 0).toLocaleString('pt-BR', {
            style: 'percent',
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        });
    }

    function escapeParetoHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function getSelectedStorePareto() {
        if (typeof lojaSelecionada !== 'undefined') return String(lojaSelecionada || '');
        return String(window.lojaSelecionada || '');
    }

    function getCurrentStartPareto() {
        try {
            return typeof getDataIniISO === 'function' ? String(getDataIniISO() || '') : '';
        } catch (_error) {
            return '';
        }
    }

    function getCurrentEndPareto() {
        try {
            return typeof getDataFimISO === 'function' ? String(getDataFimISO() || '') : '';
        } catch (_error) {
            return '';
        }
    }

    function buildParetoParams({ loja, periodo = '12m', dataInicio = '', dataFim = '' }) {
        const store = String(loja || '').trim();
        const mode = String(periodo || '12m').trim().toLowerCase();
        if (!store || store === '__todas') {
            throw new Error('Selecione uma conta específica antes de gerar o relatório.');
        }
        if (!['6m', '12m', 'personalizado'].includes(mode)) {
            throw new Error('Período inválido.');
        }
        const params = new URLSearchParams({ loja: store, periodo: mode });
        if (mode === 'personalizado') {
            if (!dataInicio || !dataFim) {
                throw new Error('Informe as datas inicial e final do período personalizado.');
            }
            if (String(dataInicio) > String(dataFim)) {
                throw new Error('A data inicial não pode ser posterior à data final.');
            }
            params.set('data_inicio', String(dataInicio));
            params.set('data_fim', String(dataFim));
        }
        return params;
    }

    function authHeadersPareto() {
        return typeof obterAuthHeaders === 'function' ? obterAuthHeaders() : {};
    }

    async function fetchPareto(url, options = {}) {
        if (typeof fetchComTimeout === 'function') {
            return fetchComTimeout(url, options, REPORT_TIMEOUT_MS);
        }
        return fetch(url, options);
    }

    async function responseErrorPareto(response, fallback) {
        try {
            const payload = await response.json();
            const detail = payload?.detail;
            if (typeof detail === 'string' && detail.trim()) return detail;
            if (detail) return JSON.stringify(detail);
        } catch (_error) {
            // A resposta pode ser binária ou vazia.
        }
        return `${fallback} (HTTP ${response.status || '?'})`;
    }

    function responseFilenamePareto(response, fallback) {
        const disposition = response?.headers?.get?.('Content-Disposition') || '';
        const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
        if (utf8Match) {
            try { return decodeURIComponent(utf8Match[1]); } catch (_error) { return utf8Match[1]; }
        }
        const simpleMatch = disposition.match(/filename="?([^";]+)"?/i);
        return simpleMatch ? simpleMatch[1] : fallback;
    }

    async function saveResponsePareto(response, fallbackName) {
        const blob = await response.blob();
        const objectUrl = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = objectUrl;
        link.download = responseFilenamePareto(response, fallbackName);
        link.style.display = 'none';
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    }

    async function downloadParetoFormats({
        formatos,
        params,
        fetcher = fetchPareto,
        saver = saveResponsePareto,
        headers = authHeadersPareto()
    }) {
        const results = [];
        for (const format of formatos) {
            const exportParams = new URLSearchParams(params.toString());
            exportParams.set('formato', format);
            try {
                const response = await fetcher(`${EXPORT_URL}?${exportParams.toString()}`, {
                    method: 'GET',
                    headers,
                    cache: 'no-store'
                });
                if (!response.ok) {
                    throw new Error(await responseErrorPareto(response, `Não foi possível gerar ${format.toUpperCase()}`));
                }
                await saver(response, `relatorio_pareto_80.${format}`);
                results.push({ formato: format, success: true });
            } catch (error) {
                results.push({
                    formato: format,
                    success: false,
                    error: error?.name === 'AbortError' ? 'Tempo de geração excedido.' : String(error?.message || error)
                });
            }
        }
        return results;
    }

    function createModalPareto() {
        if (overlay) return overlay;
        overlay = document.createElement('div');
        overlay.id = 'paretoReportOverlay';
        overlay.className = 'pareto-report-overlay';
        overlay.setAttribute('aria-hidden', 'true');
        overlay.innerHTML = `
            <section class="pareto-report-modal" role="dialog" aria-modal="true" aria-labelledby="paretoReportTitle">
                <header class="pareto-report-header">
                    <div>
                        <h2 id="paretoReportTitle">Relatório Pareto 80%</h2>
                        <p>Identifica o menor conjunto de SKUs que alcança 80% do faturamento.</p>
                    </div>
                    <button id="paretoReportClose" class="pareto-report-close" type="button" aria-label="Fechar">&times;</button>
                </header>
                <div class="pareto-report-body">
                    <div class="pareto-report-store">
                        <span>Conta selecionada</span>
                        <strong id="paretoReportStore">-</strong>
                    </div>

                    <div class="pareto-report-section">
                        <span class="pareto-report-section-title">Período do relatório</span>
                        <div class="pareto-period-options">
                            <label class="pareto-period-option">
                                <input type="radio" name="paretoPeriodo" value="6m">
                                <strong>6 meses</strong>
                            </label>
                            <label class="pareto-period-option">
                                <input type="radio" name="paretoPeriodo" value="12m" checked>
                                <strong>12 meses</strong>
                            </label>
                            <label class="pareto-period-option">
                                <input type="radio" name="paretoPeriodo" value="personalizado">
                                <strong>Personalizado</strong>
                            </label>
                        </div>
                        <div id="paretoCustomDates" class="pareto-custom-dates">
                            <label>Data inicial
                                <input id="paretoDataInicio" type="date">
                            </label>
                            <label>Data final
                                <input id="paretoDataFim" type="date">
                            </label>
                        </div>
                    </div>

                    <div class="pareto-report-section">
                        <span class="pareto-report-section-title">Arquivos para baixar</span>
                        <div class="pareto-format-options">
                            <label class="pareto-format-option">
                                <input type="checkbox" name="paretoFormato" value="xlsx" checked>
                                <strong>Planilha Excel (.xlsx)</strong>
                            </label>
                            <label class="pareto-format-option">
                                <input type="checkbox" name="paretoFormato" value="pdf" checked>
                                <strong>Relatório PDF (.pdf)</strong>
                            </label>
                        </div>
                    </div>

                    <p class="pareto-report-help">A análise usa toda a conta selecionada: Mercado Livre Full e demais canais. O filtro de unidade de negócio da tela não é aplicado.</p>
                    <div id="paretoReportPreview" class="pareto-report-preview" aria-live="polite"></div>
                    <div id="paretoReportMessage" class="pareto-report-message" aria-live="polite"></div>
                    <div class="pareto-report-actions">
                        <button id="paretoReportCancel" class="pareto-report-cancel" type="button">Fechar</button>
                        <button id="paretoReportGenerate" class="pareto-report-generate" type="button">Gerar XLSX e PDF</button>
                    </div>
                </div>
            </section>`;
        document.body.appendChild(overlay);

        overlay.querySelector('#paretoReportClose').addEventListener('click', closeModalPareto);
        overlay.querySelector('#paretoReportCancel').addEventListener('click', closeModalPareto);
        overlay.querySelector('#paretoReportGenerate').addEventListener('click', generateParetoReport);
        overlay.addEventListener('click', event => {
            if (event.target === overlay) closeModalPareto();
        });
        overlay.querySelectorAll('input[name="paretoPeriodo"]').forEach(input => {
            input.addEventListener('change', updateCustomDatesPareto);
        });
        overlay.querySelectorAll('input[name="paretoFormato"]').forEach(input => {
            input.addEventListener('change', updateGenerateLabelPareto);
        });
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape' && overlay?.classList.contains('ativo')) closeModalPareto();
        });
        return overlay;
    }

    function showTopStatusPareto(message, kind = 'error') {
        const element = typeof statusEl !== 'undefined' && statusEl
            ? statusEl
            : document.getElementById('status');
        if (!element) return;
        element.className = `status-bar ${kind}`;
        element.textContent = message;
    }

    function setMessagePareto(message, kind) {
        const element = overlay?.querySelector('#paretoReportMessage');
        if (!element) return;
        element.className = `pareto-report-message ativo ${kind || ''}`.trim();
        element.textContent = message;
    }

    function updateCustomDatesPareto() {
        const mode = overlay?.querySelector('input[name="paretoPeriodo"]:checked')?.value;
        overlay?.querySelector('#paretoCustomDates')?.classList.toggle('ativo', mode === 'personalizado');
    }

    function selectedFormatsPareto() {
        return Array.from(overlay?.querySelectorAll('input[name="paretoFormato"]:checked') || [])
            .map(input => input.value);
    }

    function updateGenerateLabelPareto() {
        const formats = selectedFormatsPareto();
        const button = overlay?.querySelector('#paretoReportGenerate');
        if (!button) return;
        button.textContent = formats.length
            ? `Gerar ${formats.map(item => item.toUpperCase()).join(' e ')}`
            : 'Selecione um formato';
    }

    function renderPreviewPareto(payload) {
        const preview = overlay?.querySelector('#paretoReportPreview');
        if (!preview) return;
        const summary = payload?.resumo || {};
        const period = payload?.periodo || {};
        const highlights = Array.isArray(payload?.analise_executiva?.destaques)
            ? payload.analise_executiva.destaques
            : [];
        const insights = highlights.length
            ? `<div class="pareto-preview-insights"><strong>Leitura executiva</strong><ul>${highlights.map(item => `<li>${escapeParetoHtml(item)}</li>`).join('')}</ul></div>`
            : '';
        preview.innerHTML = `
            <div class="pareto-preview-card"><span>Faturamento</span><strong>${escapeParetoHtml(formatCurrencyPareto(summary.faturamento_produtos))}</strong></div>
            <div class="pareto-preview-card"><span>SKUs no Pareto</span><strong>${escapeParetoHtml(summary.pareto_skus ?? 0)} de ${escapeParetoHtml(summary.total_skus ?? 0)}</strong></div>
            <div class="pareto-preview-card"><span>Participação</span><strong>${escapeParetoHtml(formatPercentPareto(summary.pareto_participacao))}</strong></div>
            <div class="pareto-preview-card"><span>SKU de corte</span><strong>${escapeParetoHtml(summary.sku_corte || '-')}</strong></div>
            <div class="pareto-preview-card"><span>Dados até</span><strong>${escapeParetoHtml(period.ultima_data_disponivel || 'Sem vendas')}</strong></div>
            <div class="pareto-preview-card"><span>Top 5</span><strong>${escapeParetoHtml(formatPercentPareto(summary.top_5_participacao))}</strong></div>
            <div class="pareto-preview-card"><span>Ajuste fiscal</span><strong>${escapeParetoHtml(formatCurrencyPareto(payload?.ajuste_fiscal?.valor))}</strong></div>
            <div class="pareto-preview-card"><span>Duplicidades removidas</span><strong>${escapeParetoHtml((payload?.auditoria?.duplicidades_entre_bases || 0) + (payload?.auditoria?.duplicidades_pedido_nota || 0))}</strong></div>
            ${insights}`;
        preview.classList.add('ativo');
    }

    function setBusyPareto(busy) {
        const generate = overlay?.querySelector('#paretoReportGenerate');
        const cancel = overlay?.querySelector('#paretoReportCancel');
        const close = overlay?.querySelector('#paretoReportClose');
        if (generate) generate.disabled = busy;
        if (cancel) cancel.disabled = busy;
        if (close) close.disabled = busy;
        overlay?.querySelectorAll('input').forEach(input => { input.disabled = busy; });
    }

    function openModalPareto() {
        const store = getSelectedStorePareto();
        if (!store || store === '__todas') {
            showTopStatusPareto('Selecione uma conta específica para gerar o relatório Pareto 80%.');
            return;
        }
        const modal = createModalPareto();
        lastFocusedElement = document.activeElement;
        modal.querySelector('#paretoReportStore').textContent = store;
        modal.querySelector('#paretoDataInicio').value = getCurrentStartPareto();
        modal.querySelector('#paretoDataFim').value = getCurrentEndPareto();
        modal.querySelector('#paretoReportPreview').classList.remove('ativo');
        modal.querySelector('#paretoReportPreview').innerHTML = '';
        modal.querySelector('#paretoReportMessage').className = 'pareto-report-message';
        modal.classList.add('ativo');
        modal.setAttribute('aria-hidden', 'false');
        document.body.style.overflow = 'hidden';
        updateCustomDatesPareto();
        updateGenerateLabelPareto();
        modal.querySelector('#paretoReportClose').focus();
    }

    function closeModalPareto() {
        if (!overlay || overlay.querySelector('#paretoReportGenerate')?.disabled) return;
        overlay.classList.remove('ativo');
        overlay.setAttribute('aria-hidden', 'true');
        document.body.style.overflow = '';
        lastFocusedElement?.focus?.();
    }

    async function generateParetoReport() {
        const formats = selectedFormatsPareto();
        if (!formats.length) {
            setMessagePareto('Selecione XLSX, PDF ou os dois formatos.', 'error');
            return;
        }
        const store = getSelectedStorePareto();
        const mode = overlay.querySelector('input[name="paretoPeriodo"]:checked')?.value || '12m';
        let params;
        try {
            params = buildParetoParams({
                loja: store,
                periodo: mode,
                dataInicio: overlay.querySelector('#paretoDataInicio').value,
                dataFim: overlay.querySelector('#paretoDataFim').value
            });
        } catch (error) {
            setMessagePareto(error.message, 'error');
            return;
        }

        setBusyPareto(true);
        setMessagePareto('Analisando vendas e preparando o relatório...', 'loading');
        try {
            const previewResponse = await fetchPareto(`${REPORT_URL}?${params.toString()}`, {
                method: 'GET',
                headers: authHeadersPareto(),
                cache: 'no-store'
            });
            if (!previewResponse.ok) {
                throw new Error(await responseErrorPareto(previewResponse, 'Não foi possível analisar as vendas'));
            }
            const payload = await previewResponse.json();
            renderPreviewPareto(payload);
            setMessagePareto('Análise concluída. Gerando os arquivos selecionados...', 'loading');

            const results = await downloadParetoFormats({ formatos: formats, params });
            const failures = results.filter(item => !item.success);
            if (!failures.length) {
                setMessagePareto(`Relatório concluído: ${formats.map(item => item.toUpperCase()).join(' e ')} baixado(s).`, 'success');
                showTopStatusPareto('Relatório Pareto 80% gerado com sucesso.', 'success');
            } else if (failures.length < results.length) {
                const downloaded = results.filter(item => item.success).map(item => item.formato.toUpperCase()).join(', ');
                setMessagePareto(`${downloaded} baixado(s). Falha em ${failures.map(item => item.formato.toUpperCase()).join(', ')}: ${failures.map(item => item.error).join('; ')}`, 'error');
            } else {
                setMessagePareto(`Não foi possível baixar os arquivos: ${failures.map(item => item.error).join('; ')}`, 'error');
            }
        } catch (error) {
            const message = error?.name === 'AbortError'
                ? 'A geração excedeu o tempo limite. Tente novamente.'
                : String(error?.message || error);
            setMessagePareto(message, 'error');
        } finally {
            setBusyPareto(false);
            updateGenerateLabelPareto();
        }
    }

    function initializePareto80() {
        const button = document.getElementById('btnPareto80');
        if (!button || button.dataset.paretoInitialized === 'true') return;
        button.dataset.paretoInitialized = 'true';
        button.addEventListener('click', openModalPareto);
    }

    window.__jkVendasPareto80 = {
        buildParetoParams,
        downloadParetoFormats,
        initializePareto80,
        openModalPareto
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializePareto80, { once: true });
    } else {
        initializePareto80();
    }
})();
