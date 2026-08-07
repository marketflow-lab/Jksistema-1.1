(function vendasGeneralSkuReportModule() {
    'use strict';

    const REPORT_URL = '/api/vendas/relatorios/vendas-estoque-devolucoes';
    const EXPORT_URL = '/api/vendas/relatorios/vendas-estoque-devolucoes/exportar';
    const REPORT_TIMEOUT_MS = 120000;
    let overlay = null;
    let lastFocusedElement = null;

    function formatCurrency(value) {
        return Number(value || 0).toLocaleString('pt-BR', {
            style: 'currency',
            currency: 'BRL'
        });
    }

    function formatNumber(value) {
        return Number(value || 0).toLocaleString('pt-BR', {
            minimumFractionDigits: 0,
            maximumFractionDigits: 2
        });
    }

    function formatPercent(value) {
        if (value === null || value === undefined) return '-';
        return Number(value).toLocaleString('pt-BR', {
            style: 'percent',
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        });
    }

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function getSelectedStore() {
        if (typeof lojaSelecionada !== 'undefined') return String(lojaSelecionada || '');
        return String(window.lojaSelecionada || '');
    }

    function getCurrentStart() {
        try {
            return typeof getDataIniISO === 'function' ? String(getDataIniISO() || '') : '';
        } catch (_error) {
            return '';
        }
    }

    function getCurrentEnd() {
        try {
            return typeof getDataFimISO === 'function' ? String(getDataFimISO() || '') : '';
        } catch (_error) {
            return '';
        }
    }

    function buildParams({ loja, periodo = '12m', dataInicio = '', dataFim = '' }) {
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

    function authHeaders() {
        return typeof obterAuthHeaders === 'function' ? obterAuthHeaders() : {};
    }

    async function fetchReport(url, options = {}) {
        if (typeof fetchComTimeout === 'function') {
            return fetchComTimeout(url, options, REPORT_TIMEOUT_MS);
        }
        return fetch(url, options);
    }

    async function responseError(response, fallback) {
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

    function responseFilename(response, fallback) {
        const disposition = response?.headers?.get?.('Content-Disposition') || '';
        const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
        if (utf8Match) {
            try { return decodeURIComponent(utf8Match[1]); } catch (_error) { return utf8Match[1]; }
        }
        const simpleMatch = disposition.match(/filename="?([^";]+)"?/i);
        return simpleMatch ? simpleMatch[1] : fallback;
    }

    async function saveResponse(response, fallbackName) {
        const blob = await response.blob();
        const objectUrl = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = objectUrl;
        link.download = responseFilename(response, fallbackName);
        link.style.display = 'none';
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    }

    async function downloadFormats({
        formatos,
        params,
        fetcher = fetchReport,
        saver = saveResponse,
        headers = authHeaders()
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
                    throw new Error(await responseError(response, `Não foi possível gerar ${format.toUpperCase()}`));
                }
                await saver(response, `relatorio_geral_skus.${format}`);
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

    function createModal() {
        if (overlay) return overlay;
        overlay = document.createElement('div');
        overlay.id = 'generalSkuReportOverlay';
        overlay.className = 'pareto-report-overlay';
        overlay.setAttribute('aria-hidden', 'true');
        overlay.innerHTML = `
            <section class="pareto-report-modal" role="dialog" aria-modal="true" aria-labelledby="generalSkuReportTitle">
                <header class="pareto-report-header">
                    <div>
                        <h2 id="generalSkuReportTitle">Relatório Geral de SKUs</h2>
                        <p>Vendas mensais, estoque da Loja, duração estimada e devoluções de todos os SKUs.</p>
                    </div>
                    <button id="generalSkuReportClose" class="pareto-report-close" type="button" aria-label="Fechar">&times;</button>
                </header>
                <div class="pareto-report-body">
                    <div class="pareto-report-store">
                        <span>Conta selecionada</span>
                        <strong id="generalSkuReportStore">-</strong>
                    </div>

                    <div class="pareto-report-section">
                        <span class="pareto-report-section-title">Período do relatório</span>
                        <div class="pareto-period-options">
                            <label class="pareto-period-option">
                                <input type="radio" name="generalSkuPeriodo" value="6m">
                                <strong>6 meses</strong>
                            </label>
                            <label class="pareto-period-option">
                                <input type="radio" name="generalSkuPeriodo" value="12m" checked>
                                <strong>12 meses</strong>
                            </label>
                            <label class="pareto-period-option">
                                <input type="radio" name="generalSkuPeriodo" value="personalizado">
                                <strong>Personalizado</strong>
                            </label>
                        </div>
                        <div id="generalSkuCustomDates" class="pareto-custom-dates">
                            <label>Data inicial
                                <input id="generalSkuDataInicio" type="date">
                            </label>
                            <label>Data final
                                <input id="generalSkuDataFim" type="date">
                            </label>
                        </div>
                    </div>

                    <div class="pareto-report-section">
                        <span class="pareto-report-section-title">Arquivos para baixar</span>
                        <div class="pareto-format-options">
                            <label class="pareto-format-option">
                                <input type="checkbox" name="generalSkuFormato" value="xlsx" checked>
                                <strong>Planilha Excel (.xlsx)</strong>
                            </label>
                            <label class="pareto-format-option">
                                <input type="checkbox" name="generalSkuFormato" value="pdf" checked>
                                <strong>Relatório PDF (.pdf)</strong>
                            </label>
                        </div>
                    </div>

                    <p class="pareto-report-help">Vendas e devoluções incluem Loja + Mercado Livre Full da conta. O estoque usa somente o saldo da Loja e nunca soma o Full. Nenhuma sincronização será executada.</p>
                    <div id="generalSkuReportPreview" class="pareto-report-preview" aria-live="polite"></div>
                    <div id="generalSkuReportMessage" class="pareto-report-message" aria-live="polite"></div>
                    <div class="pareto-report-actions">
                        <button id="generalSkuReportCancel" class="pareto-report-cancel" type="button">Fechar</button>
                        <button id="generalSkuReportGenerate" class="pareto-report-generate" type="button">Gerar XLSX e PDF</button>
                    </div>
                </div>
            </section>`;
        document.body.appendChild(overlay);

        overlay.querySelector('#generalSkuReportClose').addEventListener('click', closeModal);
        overlay.querySelector('#generalSkuReportCancel').addEventListener('click', closeModal);
        overlay.querySelector('#generalSkuReportGenerate').addEventListener('click', generateReport);
        overlay.addEventListener('click', event => {
            if (event.target === overlay) closeModal();
        });
        overlay.querySelectorAll('input[name="generalSkuPeriodo"]').forEach(input => {
            input.addEventListener('change', updateCustomDates);
        });
        overlay.querySelectorAll('input[name="generalSkuFormato"]').forEach(input => {
            input.addEventListener('change', updateGenerateLabel);
        });
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape' && overlay?.classList.contains('ativo')) closeModal();
        });
        return overlay;
    }

    function showTopStatus(message, kind = 'error') {
        const element = typeof statusEl !== 'undefined' && statusEl
            ? statusEl
            : document.getElementById('status');
        if (!element) return;
        element.className = `status-bar ${kind}`;
        element.textContent = message;
    }

    function setMessage(message, kind) {
        const element = overlay?.querySelector('#generalSkuReportMessage');
        if (!element) return;
        element.className = `pareto-report-message ativo ${kind || ''}`.trim();
        element.textContent = message;
    }

    function updateCustomDates() {
        const mode = overlay?.querySelector('input[name="generalSkuPeriodo"]:checked')?.value;
        overlay?.querySelector('#generalSkuCustomDates')?.classList.toggle('ativo', mode === 'personalizado');
    }

    function selectedFormats() {
        return Array.from(overlay?.querySelectorAll('input[name="generalSkuFormato"]:checked') || [])
            .map(input => input.value);
    }

    function updateGenerateLabel() {
        const formats = selectedFormats();
        const button = overlay?.querySelector('#generalSkuReportGenerate');
        if (!button) return;
        button.textContent = formats.length
            ? `Gerar ${formats.map(item => item.toUpperCase()).join(' e ')}`
            : 'Selecione um formato';
    }

    function renderPreview(payload) {
        const preview = overlay?.querySelector('#generalSkuReportPreview');
        if (!preview) return;
        const summary = payload?.resumo || {};
        const period = payload?.periodo || {};
        const highlights = Array.isArray(payload?.analise_executiva?.destaques)
            ? payload.analise_executiva.destaques
            : [];
        const insights = highlights.length
            ? `<div class="pareto-preview-insights"><strong>Leitura executiva</strong><ul>${highlights.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul></div>`
            : '';
        preview.innerHTML = `
            <div class="pareto-preview-card"><span>SKUs analisados</span><strong>${escapeHtml(summary.total_skus ?? 0)}</strong></div>
            <div class="pareto-preview-card"><span>Unidades vendidas</span><strong>${escapeHtml(formatNumber(summary.unidades_vendidas))}</strong></div>
            <div class="pareto-preview-card"><span>Faturamento</span><strong>${escapeHtml(formatCurrency(summary.faturamento))}</strong></div>
            <div class="pareto-preview-card"><span>Estoque Loja conhecido</span><strong>${escapeHtml(formatNumber(summary.estoque_loja_total_conhecido))}</strong></div>
            <div class="pareto-preview-card"><span>Sem registro de estoque</span><strong>${escapeHtml(summary.skus_sem_registro_estoque ?? 0)}</strong></div>
            <div class="pareto-preview-card"><span>Quantidade devolvida</span><strong>${escapeHtml(formatNumber(summary.quantidade_devolvida))}</strong></div>
            <div class="pareto-preview-card"><span>Taxa de devolução</span><strong>${escapeHtml(formatPercent(summary.taxa_devolucao))}</strong></div>
            <div class="pareto-preview-card"><span>Estoque atualizado</span><strong>${escapeHtml(period.estoque_atualizado_em || 'Indisponível')}</strong></div>
            ${insights}`;
        preview.classList.add('ativo');
    }

    function setBusy(busy) {
        const generate = overlay?.querySelector('#generalSkuReportGenerate');
        const cancel = overlay?.querySelector('#generalSkuReportCancel');
        const close = overlay?.querySelector('#generalSkuReportClose');
        if (generate) generate.disabled = busy;
        if (cancel) cancel.disabled = busy;
        if (close) close.disabled = busy;
        overlay?.querySelectorAll('input').forEach(input => { input.disabled = busy; });
    }

    function openModal() {
        const store = getSelectedStore();
        if (!store || store === '__todas') {
            showTopStatus('Selecione uma conta específica para gerar o Relatório Geral de SKUs.');
            return;
        }
        const modal = createModal();
        lastFocusedElement = document.activeElement;
        modal.querySelector('#generalSkuReportStore').textContent = store;
        modal.querySelector('#generalSkuDataInicio').value = getCurrentStart();
        modal.querySelector('#generalSkuDataFim').value = getCurrentEnd();
        modal.querySelector('#generalSkuReportPreview').classList.remove('ativo');
        modal.querySelector('#generalSkuReportPreview').innerHTML = '';
        modal.querySelector('#generalSkuReportMessage').className = 'pareto-report-message';
        modal.classList.add('ativo');
        modal.setAttribute('aria-hidden', 'false');
        document.body.style.overflow = 'hidden';
        updateCustomDates();
        updateGenerateLabel();
        modal.querySelector('#generalSkuReportClose').focus();
    }

    function closeModal() {
        if (!overlay || overlay.querySelector('#generalSkuReportGenerate')?.disabled) return;
        overlay.classList.remove('ativo');
        overlay.setAttribute('aria-hidden', 'true');
        document.body.style.overflow = '';
        lastFocusedElement?.focus?.();
    }

    async function generateReport() {
        const formats = selectedFormats();
        if (!formats.length) {
            setMessage('Selecione XLSX, PDF ou os dois formatos.', 'error');
            return;
        }
        const store = getSelectedStore();
        const mode = overlay.querySelector('input[name="generalSkuPeriodo"]:checked')?.value || '12m';
        let params;
        try {
            params = buildParams({
                loja: store,
                periodo: mode,
                dataInicio: overlay.querySelector('#generalSkuDataInicio').value,
                dataFim: overlay.querySelector('#generalSkuDataFim').value
            });
        } catch (error) {
            setMessage(error.message, 'error');
            return;
        }

        setBusy(true);
        setMessage('Analisando todos os SKUs, estoque da Loja e devoluções...', 'loading');
        try {
            const previewResponse = await fetchReport(`${REPORT_URL}?${params.toString()}`, {
                method: 'GET',
                headers: authHeaders(),
                cache: 'no-store'
            });
            if (!previewResponse.ok) {
                throw new Error(await responseError(previewResponse, 'Não foi possível analisar os SKUs'));
            }
            const payload = await previewResponse.json();
            renderPreview(payload);
            setMessage('Análise concluída. Gerando os arquivos selecionados...', 'loading');
            const results = await downloadFormats({ formatos: formats, params });
            const failures = results.filter(item => !item.success);
            if (!failures.length) {
                setMessage(`Relatório concluído: ${formats.map(item => item.toUpperCase()).join(' e ')} baixado(s).`, 'success');
                showTopStatus('Relatório Geral de SKUs gerado com sucesso.', 'success');
            } else if (failures.length < results.length) {
                const downloaded = results.filter(item => item.success).map(item => item.formato.toUpperCase()).join(', ');
                setMessage(`${downloaded} baixado(s). Falha em ${failures.map(item => item.formato.toUpperCase()).join(', ')}: ${failures.map(item => item.error).join('; ')}`, 'error');
            } else {
                setMessage(`Não foi possível baixar os arquivos: ${failures.map(item => item.error).join('; ')}`, 'error');
            }
        } catch (error) {
            const message = error?.name === 'AbortError'
                ? 'A geração excedeu o tempo limite. Tente novamente.'
                : String(error?.message || error);
            setMessage(message, 'error');
        } finally {
            setBusy(false);
            updateGenerateLabel();
        }
    }

    function initialize() {
        const button = document.getElementById('btnRelatorioGeralSkus');
        if (!button || button.dataset.generalSkuInitialized === 'true') return;
        button.dataset.generalSkuInitialized = 'true';
        button.addEventListener('click', openModal);
    }

    window.__jkVendasGeneralSkuReport = {
        buildParams,
        downloadFormats,
        initialize,
        openModal
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initialize, { once: true });
    } else {
        initialize();
    }
})();
