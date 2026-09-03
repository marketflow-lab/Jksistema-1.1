(function (global) {
    'use strict';
    const cadastro = global.JKCadastro;
    if (!cadastro || !cadastro.components.has('actions')) throw new Error('Ações do Cadastro não inicializadas.');
    if (cadastro.components.has('importacoes-catalogos')) return;
    const { elements, state } = cadastro.runtime;
    const actions = cadastro.actions;
    const storeTools = global.JKCadastroStore;
    const POLL_INTERVAL_MS = 750, MAX_PREVIEW_ROWS = 500, MAX_CONFLICTS = 50;
    const SOURCES = Object.freeze({ bling: 'Bling', mercadolivre: 'Mercado Livre' });
    const POLLABLE = new Set(['queued', 'pending', 'running', 'collecting', 'applying']);
    const CANCELLABLE = new Set(['queued', 'pending', 'running', 'collecting', 'ready']);
    const TERMINAL_ERRORS = new Set(['error', 'failed', 'stale']);
    const flow = { applying: false, cancelling: false, jobId: '', lastFocus: null, lastPayload: null, opened: false, requestSeq: 0, source: '', storeId: '' };
    function authHeaders(extra) { if (typeof global.obterAuthHeaders !== 'function') throw new Error('Autenticação indisponível.'); return global.obterAuthHeaders(extra); }
    function apiImportacao(storeId, suffix) { return `${storeTools.apiLoja(storeId, 'importacoes')}/${suffix}`; }
    function texto(value, limit = 500) {
        if (value === null || value === undefined) return ''; let result;
        if (typeof value === 'object') { try { result = JSON.stringify(value); } catch (_error) { result = ''; } }
        else result = String(value);
        return result.length > limit ? `${result.slice(0, limit)}…` : result;
    }
    function statusPayload(payload) { return String(payload && (payload.status || payload.estado) || '').trim().toLowerCase(); }
    function erroPayload(payload, fallback) {
        const detail = payload && (payload.detail || payload.error); const message = payload && (payload.message || payload.mensagem);
        if (typeof detail === 'string' && detail.trim()) return detail.trim();
        if (detail && typeof detail === 'object') { const message = detail.message || detail.mensagem || detail.detail;
            if (message) return texto(message, 300); }
        return message ? texto(message, 300) : fallback;
    }
    async function requestJson(url, options) {
        const response = await global.fetch(url, options);
        let payload = {}; try { payload = await response.json(); } catch (_error) { payload = {}; }
        if (!response.ok) throw new Error(erroPayload(payload, `HTTP ${response.status}`));
        return payload && typeof payload === 'object' ? payload : {};
    }
    function setStatus(message, type) { elements.importacaoCatalogoStatus.textContent = String(message || ''); elements.importacaoCatalogoStatus.className = `status-bar ${type || ''}`.trim(); }
    function dialogImportacao() { return elements.modalImportacaoCatalogo.querySelector('.catalog-import-dialog'); }
    function sincronizarEstadoFluxo(status) {
        const applying = Boolean(flow.applying || status === 'applying'); const cancelling = Boolean(flow.cancelling);
        const terminal = status === 'applied' || status === 'cancelled' || TERMINAL_ERRORS.has(status); let changed = false;
        if (state.importacaoCatalogoAplicando !== applying) { state.importacaoCatalogoAplicando = applying; changed = true; }
        if (state.importacaoCatalogoCancelando !== cancelling) { state.importacaoCatalogoCancelando = cancelling; changed = true; }
        if (terminal && state.importacaoCatalogoAtiva) { state.importacaoCatalogoAtiva = false; changed = true; }
        if (changed) actions.atualizarEstadoMutacoes();
        if ((applying || cancelling) && !elements.modalImportacaoCatalogo.hidden) dialogImportacao().focus();
        return applying || cancelling;
    }
    function numeroResumo(value) { const parsed = Number(value); return Number.isFinite(parsed) && parsed > 0 ? Math.trunc(parsed) : 0; }
    function resumoPayload(payload) {
        const summary = payload && (payload.summary || payload.resumo) || {};
        const conflicts = Array.isArray(payload && payload.conflicts) ? payload.conflicts.length : 0;
        const ignored = Array.isArray(payload && (payload.ignored || payload.ignorados)) ? (payload.ignored || payload.ignorados).length : 0;
        return {
            novos: numeroResumo(summary.novos ?? summary.new), preencher: numeroResumo(summary.preencher ?? summary.campos_preencher),
            inalterados: numeroResumo(summary.inalterados ?? summary.unchanged), conflitos: numeroResumo(summary.conflitos ?? summary.conflicts ?? conflicts),
            ignorados: Math.max(numeroResumo(payload && payload.ignored_total), numeroResumo(summary.ignorados ?? summary.ignored), ignored),
        };
    }
    function progressoPayload(payload, status) {
        const progress = payload && (payload.progress || payload.progresso) || {}; const raw = progress.percent ?? progress.percentual ?? payload.progress_percent;
        if (raw === null || raw === undefined || raw === '') return status === 'ready' ? 100 : null;
        let value = Number(raw); if (!Number.isFinite(value)) return status === 'ready' ? 100 : null; if (value > 0 && value <= 1) value *= 100;
        return Math.max(0, Math.min(100, value));
    }
    function mensagemProgresso(payload) { const progress = payload && (payload.progress || payload.progresso) || {}; return String(progress.message || progress.mensagem || '').trim().slice(0, 300); }
    function warningsUnicos(value) {
        const values = Array.isArray(value) ? value : value === null || value === undefined ? [] : [value];
        return [...new Set(values.map(item => texto(item, 300).trim()).filter(Boolean))];
    }
    function totalWarnings(payload) {
        const items = payload && Array.isArray(payload.items) ? payload.items : [];
        return warningsUnicos(payload && payload.warnings).length + items.reduce((total, item) => total + warningsUnicos(item && item.warnings).length, 0);
    }
    function linhasPayload(payload) {
        const raw = payload && (payload.items || payload.preview || payload.diferencas || payload.produtos);
        const rows = [];
        const ignored = payload && (payload.ignored || payload.ignorados);
        (Array.isArray(ignored) ? ignored : []).forEach(item => {
            if (!item || typeof item !== 'object') return;
            const external = item.external_ids && typeof item.external_ids === 'object' ? item.external_ids : {};
            const identifier = item.sku || item.mlb || external.id_bling || external.mlb;
            const details = [item.reason || item.motivo];
            if (Array.isArray(item.missing_fields)) details.push(`campos: ${item.missing_fields.join(', ')}`);
            rows.push({ sku: identifier, action: 'ignorado', field: item.variation_id || item.entity,
                current: item.mlb && item.mlb !== identifier ? item.mlb : '', incoming: Array.isArray(item.candidates) ? item.candidates.join(', ') : '', note: details.filter(Boolean).join(' | ') });
        });
        if (payload && payload.ignored_truncated) rows.push({ action: 'ignorados limitados',
            note: `Exibindo ${Array.isArray(ignored) ? ignored.length : 0} de ${numeroResumo(payload.ignored_total)} itens ignorados.` });
        (Array.isArray(raw) ? raw : []).forEach(item => {
            if (!item || typeof item !== 'object') return;
            const changes = Array.isArray(item.changes) ? item.changes : [];
            const itemWarnings = warningsUnicos(item.warnings).join(', ');
            if (!changes.length) {
                rows.push({
                    sku: item.sku, action: item.action || item.acao || item.status, field: item.field || item.campo,
                    current: item.current ?? item.valor_atual, incoming: item.incoming ?? item.valor_novo,
                    note: [item.note || item.observacao || item.message, itemWarnings].filter(Boolean).join(' | '),
                });
                return;
            }
            changes.forEach(change => rows.push({
                sku: item.sku, action: change.action || change.acao || item.status, field: change.field || change.campo,
                current: change.current ?? change.valor_atual, incoming: change.incoming ?? change.valor_novo,
                note: [change.note || change.observacao, itemWarnings].filter(Boolean).join(' | '),
            }));
        });
        return rows;
    }
    function appendCell(row, value) { const cell = global.document.createElement('td'); cell.textContent = texto(value); cell.title = texto(value, 1000); row.appendChild(cell); }
    function renderTabela(payload) {
        const rows = linhasPayload(payload);
        const fragment = global.document.createDocumentFragment();
        rows.slice(0, MAX_PREVIEW_ROWS).forEach(item => {
            const row = global.document.createElement('tr');
            [item.sku, item.action, item.field, item.current, item.incoming, item.note]
                .forEach(value => appendCell(row, value));
            fragment.appendChild(row);
        });
        if (rows.length > MAX_PREVIEW_ROWS || payload.items_truncated) {
            const row = global.document.createElement('tr');
            ['', 'Prévia limitada', '', '', '', `Exibindo até ${MAX_PREVIEW_ROWS} diferenças. O resumo considera o catálogo completo.`]
                .forEach(value => appendCell(row, value));
            fragment.appendChild(row);
        }
        elements.tBodyImportacaoCatalogo.replaceChildren(fragment);
    }
    function conflitosPayload(payload) {
        const explicit = payload && (payload.conflicts || payload.conflitos); if (Array.isArray(explicit)) return explicit;
        const items = payload && payload.items; return Array.isArray(items)
            ? items.filter(item => item && (item.status === 'conflito' || numeroResumo(item.conflicts) > 0)) : [];
    }
    function rotuloConflito(item) {
        if (typeof item === 'string') return item;
        if (!item || typeof item !== 'object') return 'Conflito sem detalhes.';
        const sku = texto(item.sku, 100);
        const detail = Array.isArray(item.conflicts)
            ? item.conflicts.map(value => texto(value, 120)).filter(Boolean).join(', ')
            : texto(item.message || item.mensagem || item.reason || item.campo || item.status, 240);
        return `${sku ? `SKU ${sku}: ` : ''}${detail || 'valores divergentes preservados.'}`;
    }
    function renderConflitos(payload) {
        const conflicts = conflitosPayload(payload);
        const fragment = global.document.createDocumentFragment();
        conflicts.slice(0, MAX_CONFLICTS).forEach(item => {
            const entry = global.document.createElement('li');
            entry.textContent = rotuloConflito(item);
            fragment.appendChild(entry);
        });
        if (conflicts.length > MAX_CONFLICTS) {
            const entry = global.document.createElement('li');
            entry.textContent = `Outros ${conflicts.length - MAX_CONFLICTS} conflito(s) constam no resumo.`;
            fragment.appendChild(entry);
        }
        elements.importacaoCatalogoConflitosLista.replaceChildren(fragment);
        elements.importacaoCatalogoConflitos.hidden = conflicts.length === 0;
    }
    function coberturaGeralPayload(payload) { return payload && (payload.coverage_complete ?? payload.cobertura_completa); }
    function coberturaSkuPayload(payload) {
        if (!payload || typeof payload !== 'object') return undefined;
        if (Object.prototype.hasOwnProperty.call(payload, 'sku_coverage_complete')) return payload.sku_coverage_complete;
        if (Object.prototype.hasOwnProperty.call(payload, 'cobertura_sku_completa')) return payload.cobertura_sku_completa;
        return coberturaGeralPayload(payload);
    }
    function detalhesRevisao(payload, summary) {
        const warnings = totalWarnings(payload);
        return `Avisos: ${warnings}. Ignorados: ${summary.ignorados}. Itens sem SKU são ignorados e não serão cadastrados.`;
    }
    function podeAplicar(payload, status) { return status === 'ready' && payload && payload.can_apply === true && coberturaSkuPayload(payload) === true; }
    function renderPayload(payload) {
        const status = statusPayload(payload);
        const summary = resumoPayload(payload);
        const progress = progressoPayload(payload, status);
        const coverage = coberturaGeralPayload(payload);
        const skuCoverage = coberturaSkuPayload(payload);
        const reviewDetails = detalhesRevisao(payload, summary);
        const busy = sincronizarEstadoFluxo(status);
        elements.importacaoCatalogoNovos.textContent = String(summary.novos); elements.importacaoCatalogoPreencher.textContent = String(summary.preencher); elements.importacaoCatalogoInalterados.textContent = String(summary.inalterados);
        elements.importacaoCatalogoConflitosTotal.textContent = String(summary.conflitos); elements.importacaoCatalogoIgnorados.textContent = String(summary.ignorados);
        if (progress === null) {
            const terminalLabel = status === 'cancelled' ? 'Cancelada' : TERMINAL_ERRORS.has(status) ? 'Interrompida' : '';
            if (terminalLabel) { elements.importacaoCatalogoProgress.value = 0; elements.importacaoCatalogoProgressText.textContent = terminalLabel; } else { elements.importacaoCatalogoProgress.removeAttribute('value'); elements.importacaoCatalogoProgressText.textContent = 'Em andamento'; }
        } else { elements.importacaoCatalogoProgress.value = progress; elements.importacaoCatalogoProgressText.textContent = `${Math.round(progress)}%`; }
        renderTabela(payload); renderConflitos(payload);
        elements.btnAplicarImportacaoCatalogo.disabled = !podeAplicar(payload, status) || flow.applying || flow.cancelling;
        elements.btnFecharImportacaoCatalogo.disabled = busy;
        elements.btnCancelarImportacaoCatalogo.disabled = busy || !flow.jobId || !CANCELLABLE.has(status);
        const applyResult = payload && payload.apply_result && typeof payload.apply_result === 'object' ? payload.apply_result : {}; const fotosSalvas = numeroResumo(applyResult.fotos_salvas); const fotosIgnoradas = numeroResumo(applyResult.fotos_ignoradas);
        if (status === 'applied' && payload.refresh_failed) setStatus('Importação aplicada com sucesso, mas a lista não pôde ser atualizada. Use Atualizar.', 'warning'); else if (status === 'applied' && fotosIgnoradas > 0) setStatus(`Importação aplicada. ${fotosSalvas} capa(s) comprimidas foram salvas e ${fotosIgnoradas} não puderam ser baixadas; os demais dados foram mantidos.`, 'warning'); else if (status === 'applied' && fotosSalvas > 0) setStatus(`Importação aplicada com sucesso. ${fotosSalvas} capa(s) comprimidas do Mercado Livre foram salvas no cadastro.`, 'success'); else if (status === 'applied') setStatus('Importação aplicada com sucesso.', 'success');
        else if (status === 'cancelled') setStatus('Consulta cancelada. Nenhuma nova alteração será aplicada por este trabalho.', 'success');
        else if (TERMINAL_ERRORS.has(status)) setStatus(erroPayload(payload, 'A consulta não pôde ser concluída.'), 'error');
        else if (status === 'ready' && skuCoverage !== true) setStatus(`Prévia concluída sem cobertura completa de SKU. Nenhuma alteração pode ser aplicada. ${reviewDetails}`, 'error');
        else if (status === 'ready' && coverage !== true) {
            const message = podeAplicar(payload, status) ? 'Prévia pronta para aplicar, mas há dados opcionais incompletos. Revise as diferenças.'
                : 'Prévia concluída com dados opcionais incompletos, sem alterações automáticas aplicáveis.';
            setStatus(`${message} ${reviewDetails}`, 'warning');
        }
        else if (status === 'ready') setStatus(`Prévia pronta. Revise as diferenças antes de aplicar. ${reviewDetails}`, 'success');
        else if (status === 'applying') setStatus('A aplicação desta importação está em andamento...', 'loading');
        else setStatus(mensagemProgresso(payload) || erroPayload(payload, 'Consultando o catálogo externo...'), 'loading');
        if (status === 'ready' && skuCoverage === true && coverage === true && !podeAplicar(payload, status)) {
            const warnings = totalWarnings(payload);
            const needsReview = summary.conflitos > 0 || summary.ignorados > 0 || warnings > 0;
            setStatus(needsReview
                ? `Prévia concluída para revisão, sem alterações automáticas aplicáveis. ${reviewDetails}`
                : `Prévia concluída. O cadastro já está atualizado com os dados disponíveis desta fonte. ${reviewDetails}`, needsReview ? 'warning' : 'success');
        }
    }
    function isCurrent(seq, storeId) { return flow.opened && seq === flow.requestSeq && storeId === flow.storeId && storeId === state.storeIdSelecionado; }
    function validarEscopo(payload, storeId, source) { if (payload.store_id && String(payload.store_id) !== storeId) throw new Error('A resposta pertence a outra loja.'); if (payload.source && String(payload.source).toLowerCase() !== source) throw new Error('A resposta pertence a outra fonte.'); }
    function esperar(ms) { return new Promise(resolve => global.setTimeout(resolve, ms)); }
    async function pollJob(seq, storeId, source) {
        while (isCurrent(seq, storeId)) {
            await esperar(POLL_INTERVAL_MS);
            if (!isCurrent(seq, storeId)) return;
            const payload = await requestJson(apiImportacao(storeId, encodeURIComponent(flow.jobId)), { headers: authHeaders() });
            if (!isCurrent(seq, storeId)) return;
            validarEscopo(payload, storeId, source);
            flow.lastPayload = payload;
            renderPayload(payload);
            const status = statusPayload(payload);
            if (status === 'ready' || status === 'applied' || status === 'cancelled' || TERMINAL_ERRORS.has(status)) return;
            if (!POLLABLE.has(status)) throw new Error('A importação retornou um estado desconhecido.');
        }
    }
    function resetPreview(source, storeId) {
        const sourceLabel = SOURCES[source];
        const store = state.lojas.find(item => item.store_id === storeId);
        elements.importacaoCatalogoTitulo.textContent = source === 'mercadolivre' ? 'Trazer catálogo do Mercado Livre' : `Trazer catálogo da ${sourceLabel}`;
        elements.importacaoCatalogoLoja.textContent = String(store && store.nome || storeId);
        elements.importacaoCatalogoProgress.value = 0; elements.importacaoCatalogoProgressText.textContent = '0%';
        elements.importacaoCatalogoConflitos.hidden = true;
        elements.importacaoCatalogoConflitosLista.replaceChildren();
        elements.tBodyImportacaoCatalogo.replaceChildren();
        [elements.importacaoCatalogoNovos, elements.importacaoCatalogoPreencher,
            elements.importacaoCatalogoInalterados, elements.importacaoCatalogoConflitosTotal,
            elements.importacaoCatalogoIgnorados].forEach(item => { item.textContent = '0'; });
        elements.btnAplicarImportacaoCatalogo.disabled = true; elements.btnFecharImportacaoCatalogo.disabled = false;
        elements.btnCancelarImportacaoCatalogo.disabled = true; state.importacaoCatalogoAplicando = false; state.importacaoCatalogoCancelando = false;
        setStatus(source === 'mercadolivre' ? 'Iniciando consulta somente leitura no Mercado Livre...' : `Iniciando consulta somente leitura na ${sourceLabel}...`, 'loading');
    }
    function encerrarFluxo(fecharModal) {
        if (flow.applying || flow.cancelling || state.importacaoCatalogoAplicando || state.importacaoCatalogoCancelando) return false;
        flow.requestSeq += 1; flow.opened = false; flow.jobId = ''; flow.lastPayload = null;
        state.importacaoCatalogoAtiva = false; state.importacaoCatalogoAplicando = false; state.importacaoCatalogoCancelando = false;
        if (fecharModal) elements.modalImportacaoCatalogo.hidden = true;
        actions.atualizarEstadoMutacoes();
        if (fecharModal && flow.lastFocus && typeof flow.lastFocus.focus === 'function') flow.lastFocus.focus();
        flow.lastFocus = null;
        return true;
    }
    async function abrir(source) {
        const sourceKey = String(source || '').toLowerCase();
        if (!SOURCES[sourceKey] || state.importacaoCatalogoAtiva || state.carregandoProdutos) return false;
        const storeId = String(state.storeIdSelecionado || '').trim();
        if (!storeId || !storeTools.lojaExiste(state.lojas, storeId)) return false;
        const seq = ++flow.requestSeq;
        Object.assign(flow, { applying: false, cancelling: false, jobId: '', lastFocus: global.document.activeElement, lastPayload: null, opened: true, source: sourceKey, storeId });
        state.importacaoCatalogoAtiva = true;
        resetPreview(sourceKey, storeId);
        elements.modalImportacaoCatalogo.hidden = false;
        actions.atualizarEstadoMutacoes();
        elements.btnFecharImportacaoCatalogo.focus();
        try {
            const payload = await requestJson(apiImportacao(storeId, `${sourceKey}/preview`), {
                method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }), body: '{}',
            });
            if (!isCurrent(seq, storeId)) return false;
            validarEscopo(payload, storeId, sourceKey);
            flow.jobId = String(payload.job_id || '').trim();
            if (!flow.jobId) throw new Error('A consulta não retornou uma identificação de trabalho.');
            flow.lastPayload = payload;
            renderPayload(payload);
            const status = statusPayload(payload);
            if (POLLABLE.has(status)) await pollJob(seq, storeId, sourceKey);
            else if (status !== 'ready' && status !== 'applied' && status !== 'cancelled'
                && !TERMINAL_ERRORS.has(status)) throw new Error('A importação retornou um estado desconhecido.');
            return isCurrent(seq, storeId);
        } catch (error) {
            if (!isCurrent(seq, storeId)) return false;
            state.importacaoCatalogoAtiva = false; state.importacaoCatalogoAplicando = false; state.importacaoCatalogoCancelando = false;
            elements.btnAplicarImportacaoCatalogo.disabled = true;
            setStatus(`Erro ao consultar catálogo: ${error.message}`, 'error');
            actions.atualizarEstadoMutacoes();
            return false;
        }
    }
    async function finalizarAplicado(result, seq, storeId) {
        if (!isCurrent(seq, storeId)) return false;
        validarEscopo(result, storeId, flow.source);
        if (statusPayload(result) !== 'applied') throw new Error('A aplicação retornou um estado inesperado.');
        flow.lastPayload = result; let refreshed = false;
        try { refreshed = (await actions.carregarProdutos()) !== false; } catch (_reloadError) { refreshed = false; }
        if (!isCurrent(seq, storeId)) return false;
        flow.lastPayload = refreshed ? result : { ...result, refresh_failed: true };
        flow.applying = false; state.importacaoCatalogoAtiva = false; renderPayload(flow.lastPayload);
        return true;
    }
    async function aplicar() {
        const payload = flow.lastPayload;
        const status = statusPayload(payload);
        if (flow.applying || flow.cancelling || !flow.opened || !flow.jobId || !podeAplicar(payload, status)) return false;
        const seq = flow.requestSeq;
        const storeId = flow.storeId;
        flow.applying = true;
        sincronizarEstadoFluxo(status);
        renderPayload(payload);
        dialogImportacao().focus();
        setStatus('Aplicando alterações revisadas ao cadastro...', 'loading');
        try {
            const result = await requestJson(apiImportacao(storeId, `${encodeURIComponent(flow.jobId)}/aplicar`), {
                method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }), body: '{}',
            });
            return await finalizarAplicado(result, seq, storeId);
        } catch (error) {
            try {
                let reconciled = await requestJson(apiImportacao(storeId, encodeURIComponent(flow.jobId)), { headers: authHeaders() });
                if (!isCurrent(seq, storeId)) return false;
                validarEscopo(reconciled, storeId, flow.source); flow.lastPayload = reconciled;
                if (POLLABLE.has(statusPayload(reconciled))) { await pollJob(seq, storeId, flow.source); reconciled = flow.lastPayload; }
                const reconciledStatus = statusPayload(reconciled);
                if (reconciledStatus === 'applied') return await finalizarAplicado(reconciled, seq, storeId);
                if (reconciledStatus === 'cancelled' || TERMINAL_ERRORS.has(reconciledStatus)) return false;
            } catch (_reconcileError) { /* estado remoto indisponível; invalida a prévia local abaixo */ }
            if (isCurrent(seq, storeId)) {
                flow.lastPayload = { ...(flow.lastPayload || {}), status: 'stale', can_apply: false,
                    error: { message: `Erro ao aplicar importação: ${error.message}` } };
                setStatus(flow.lastPayload.error.message, 'error');
            }
            return false;
        } finally {
            flow.applying = false;
            if (isCurrent(seq, storeId)) renderPayload(flow.lastPayload || {});
            else {
                state.importacaoCatalogoAplicando = false;
                actions.atualizarEstadoMutacoes();
            }
            actions.atualizarEstadoMutacoes();
        }
    }
    async function cancelar() {
        const payload = flow.lastPayload;
        const status = statusPayload(payload);
        if (flow.applying || flow.cancelling || !flow.opened || !flow.jobId || !CANCELLABLE.has(status)) return false;
        const seq = flow.requestSeq;
        const storeId = flow.storeId;
        let cancelError = '';
        flow.cancelling = true;
        renderPayload(payload || {});
        dialogImportacao().focus();
        setStatus('Cancelando a consulta do catálogo...', 'loading');
        try {
            const result = await requestJson(apiImportacao(storeId, `${encodeURIComponent(flow.jobId)}/cancelar`), {
                method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }), body: '{}',
            });
            if (!isCurrent(seq, storeId)) return false;
            validarEscopo(result, storeId, flow.source);
            const resultStatus = statusPayload(result);
            if (resultStatus !== 'cancelled' && resultStatus !== 'applied') throw new Error('O cancelamento retornou um estado inesperado.');
            if (resultStatus === 'applied') return await finalizarAplicado(result, seq, storeId);
            flow.lastPayload = result; renderPayload(result);
            return true;
        } catch (error) {
            if (isCurrent(seq, storeId)) cancelError = `Erro ao cancelar consulta: ${error.message}`;
            return false;
        } finally {
            flow.cancelling = false;
            if (isCurrent(seq, storeId)) {
                renderPayload(flow.lastPayload || {});
                if (cancelError) setStatus(cancelError, 'error');
            }
            actions.atualizarEstadoMutacoes();
        }
    }
    function onStoreChanged(storeId) { if (flow.opened && String(storeId || '') !== flow.storeId) encerrarFluxo(true); }
    function onKeydown(event) {
        if (!flow.opened) return;
        const busy = flow.applying || flow.cancelling || state.importacaoCatalogoAplicando;
        if (event.key === 'Escape') {
            if (busy) { event.preventDefault(); dialogImportacao().focus(); return; }
            encerrarFluxo(true);
            return;
        }
        if (event.key !== 'Tab') return;
        if (busy) { event.preventDefault(); dialogImportacao().focus(); return; }
        const focusable = [elements.btnFecharImportacaoCatalogo, elements.btnCancelarImportacaoCatalogo,
            elements.btnAplicarImportacaoCatalogo].filter(item => !item.disabled);
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && global.document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && global.document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
    function init() {
        elements.btnFecharImportacaoCatalogo.addEventListener('click', () => encerrarFluxo(true)); elements.btnCancelarImportacaoCatalogo.addEventListener('click', cancelar);
        elements.btnAplicarImportacaoCatalogo.addEventListener('click', aplicar); global.document.addEventListener('keydown', onKeydown);
    }
    cadastro.importacoesCatalogos = Object.freeze({ aplicar, abrir, cancelar, init, onStoreChanged });
    cadastro.components.add('importacoes-catalogos');
})(window);
