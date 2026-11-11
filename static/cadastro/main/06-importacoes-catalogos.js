(function (global) {
    'use strict';
    const cadastro = global.JKCadastro;
    if (!cadastro || !cadastro.components.has('actions')) throw new Error('Ações do Cadastro não inicializadas.');
    if (cadastro.components.has('importacoes-catalogos')) return;
    const { elements, state } = cadastro.runtime, actions = cadastro.actions, storeTools = global.JKCadastroStore;
    const POLL_INTERVAL_MS = 750, MAX_PREVIEW_ROWS = 500, MAX_CONFLICTS = 50, SOURCES = Object.freeze({ bling: 'Bling', mercadolivre: 'Mercado Livre' });
    const POLLABLE = new Set(['queued', 'pending', 'running', 'collecting', 'applying']), CANCELLABLE = new Set(['queued', 'pending', 'running', 'collecting', 'ready']), TERMINAL_ERRORS = new Set(['error', 'failed', 'stale']);
    const flow = { applying: false, cancelling: false, retrying: false, retryPhotosPreview: false, jobId: '', lastFocus: null, lastPayload: null, opened: false, requestSeq: 0, source: '', storeId: '' };
    const {
        texto, statusPayload, erroPayload, numeroResumo, resumoPayload, progressoPayload, mensagemProgresso,
        totalWarnings, resumoMotivosIgnorados, mudancaFotoAplicavel, linhasPayload, conflitosPayload,
        detalhesTecnicosConflito, coberturaGeralPayload, plural, progressoDetalhadoAplicacao,
        resultadoFotosAplicadas, resultadoCamposAplicados, detalhesFotosPendentes,
    } = cadastro.importacoesCatalogosPayload;
    function authHeaders(extra) { if (typeof global.obterAuthHeaders !== 'function') throw new Error('Autenticação indisponível.'); return global.obterAuthHeaders(extra); }
    function apiImportacao(storeId, suffix) { return `${storeTools.apiLoja(storeId, 'importacoes')}/${suffix}`; }
    async function requestJson(url, options) {
        const response = await global.fetch(url, options);
        let payload = {}; try { payload = await response.json(); } catch (_error) { payload = {}; }
        if (!response.ok) throw new Error(erroPayload(payload, `HTTP ${response.status}`));
        return payload && typeof payload === 'object' ? payload : {};
    }
    function setStatus(message, type) { elements.importacaoCatalogoStatus.textContent = String(message || ''); elements.importacaoCatalogoStatus.className = `status-bar ${type || ''}`.trim(); elements.importacaoCatalogoStatus.title = ''; }
    let retryPhotosButton = null;
    function dialogImportacao() { return elements.modalImportacaoCatalogo.querySelector('.catalog-import-dialog'); }
    function sincronizarEstadoFluxo(status) {
        const applying = Boolean(flow.applying || flow.retrying || status === 'applying'); const cancelling = Boolean(flow.cancelling);
        const terminal = status === 'applied' || status === 'cancelled' || TERMINAL_ERRORS.has(status); let changed = false;
        if (state.importacaoCatalogoAplicando !== applying) { state.importacaoCatalogoAplicando = applying; changed = true; }
        if (state.importacaoCatalogoCancelando !== cancelling) { state.importacaoCatalogoCancelando = cancelling; changed = true; }
        if (terminal && !flow.retrying && state.importacaoCatalogoAtiva) { state.importacaoCatalogoAtiva = false; changed = true; }
        if (changed) actions.atualizarEstadoMutacoes();
        if ((applying || cancelling) && !elements.modalImportacaoCatalogo.hidden) dialogImportacao().focus();
        return applying || cancelling;
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
    function rotuloConflito(item, payload) {
        if (!item || typeof item !== 'object') return 'Há informações diferentes que precisam de revisão.';
        const sku = texto(item.sku, 100); const technical = detalhesTecnicosConflito(item).toLowerCase();
        const plannedItem = (Array.isArray(payload && payload.items) ? payload.items : []).find(row => row && row.sku === item.sku) || item;
        const hasPhoto = String(payload && payload.source || flow.source) === 'mercadolivre' && mudancaFotoAplicavel(plannedItem);
        let detail = 'há informações diferentes entre os anúncios ou o cadastro; somente as alterações previstas por SKU serão aplicadas.';
        if (/sku_duplicado|duplicate_listing|mais de um anuncio|mais de um anúncio/.test(technical)) {
            detail = 'há mais de um anúncio para este SKU. Os dados diferentes serão preservados.';
            if (hasPhoto) detail += ' A foto prevista para este SKU será tentada ao aplicar.';
        }
        else if (/valor_existente_preservado|preserv/.test(technical)) detail = 'alguns dados do cadastro são diferentes dos anúncios e serão preservados.';
        else if (/sku_excluido|excluíd|excluid/.test(technical)) detail = 'este SKU está excluído no cadastro e precisa de revisão.';
        else if (/identidade_ambigua|ambígu|ambigu/.test(technical)) detail = 'não foi possível confirmar com segurança qual cadastro corresponde ao anúncio.';
        return `${sku ? `SKU ${sku}: ` : ''}${detail}`;
    }
    function renderConflitos(payload) {
        const conflicts = conflitosPayload(payload);
        const fragment = global.document.createDocumentFragment();
        conflicts.slice(0, MAX_CONFLICTS).forEach(item => {
            const entry = global.document.createElement('li');
            entry.textContent = rotuloConflito(item, payload);
            const technical = detalhesTecnicosConflito(item); if (technical) entry.title = `Detalhes técnicos: ${technical}`;
            fragment.appendChild(entry);
        });
        if (conflicts.length > MAX_CONFLICTS) {
            const entry = global.document.createElement('li');
            entry.textContent = `Outros ${conflicts.length - MAX_CONFLICTS} conflito(s) constam no resumo.`;
            fragment.appendChild(entry);
        }
        elements.importacaoCatalogoConflitosLista.replaceChildren(fragment); elements.importacaoCatalogoConflitosTitulo.textContent = `${conflicts.length} ${conflicts.length === 1 ? 'produto tem' : 'produtos têm'} diferenças — ver quais`;
        elements.importacaoCatalogoConflitos.hidden = conflicts.length === 0;
    }
    function detalhesRevisao(payload, summary) {
        const source = String(payload && payload.source || flow.source || '').toLowerCase();
        const parts = [`${plural(summary.aplicaveis, 'cadastro será atualizado', 'cadastros serão atualizados')}`,
            `${plural(summary.conflitos, 'produto tem diferenças que serão mantidas', 'produtos têm diferenças que serão mantidas')}`,
            `${plural(summary.ignorados, 'anúncio ou variação foi ignorado', 'anúncios ou variações foram ignorados')}`];
        if (source === 'mercadolivre' && flow.retryPhotosPreview) {
            return `${summary.fotosPlanejadas > 0 ? `${plural(summary.fotosPlanejadas, 'SKU tem foto pendente prevista', 'SKUs têm fotos pendentes previstas')}; somente essas fotos serão tentadas ao aplicar.` : 'Nenhuma foto está prevista para esta aplicação.'}${resumoMotivosIgnorados(payload)}`;
        }
        if (source === 'mercadolivre') {
            parts.push('somente as alterações previstas para cada SKU confirmado serão aplicadas, inclusive aos cadastros legados identificados');
            parts.push(summary.fotosPlanejadas > 0
                ? `as fotos ainda não foram baixadas; ao aplicar, serão tentadas ${plural(summary.fotosPlanejadas, 'foto prevista', 'fotos previstas')}`
                : 'nenhuma foto está prevista para esta aplicação');
        }
        return `${parts.join('; ')}.${resumoMotivosIgnorados(payload)}`;
    }
    function podeRetentarFotos(payload, status) {
        return status === 'applied' && flow.source === 'mercadolivre' && payload && payload.apply_result
            && payload.apply_result.can_retry_photos === true;
    }
    function podeAplicar(payload, status) { return status === 'ready' && payload && payload.can_apply === true; }
    function renderPayload(payload) {
        const status = statusPayload(payload);
        const summary = resumoPayload(payload);
        const progress = progressoPayload(payload, status);
        const coverage = coberturaGeralPayload(payload);
        const reviewDetails = detalhesRevisao(payload, summary);
        const busy = sincronizarEstadoFluxo(status);
        elements.importacaoCatalogoNovos.textContent = String(summary.encontrados || summary.novos + summary.preencher + summary.inalterados); elements.importacaoCatalogoPreencher.textContent = String(summary.aplicaveis); elements.importacaoCatalogoInalterados.textContent = String(summary.inalterados);
        elements.importacaoCatalogoConflitosTotal.textContent = String(summary.conflitos); elements.importacaoCatalogoIgnorados.textContent = String(summary.ignorados);
        if (progress === null) {
            const terminalLabel = status === 'cancelled' ? 'Cancelada' : TERMINAL_ERRORS.has(status) ? 'Interrompida' : '';
            if (terminalLabel) { elements.importacaoCatalogoProgress.value = 0; elements.importacaoCatalogoProgressText.textContent = terminalLabel; } else { elements.importacaoCatalogoProgress.removeAttribute('value'); elements.importacaoCatalogoProgressText.textContent = 'Em andamento'; }
        } else { elements.importacaoCatalogoProgress.value = progress; elements.importacaoCatalogoProgressText.textContent = `${Math.round(progress)}%`; }
        renderTabela(payload); renderConflitos(payload);
        elements.btnAplicarImportacaoCatalogo.disabled = !podeAplicar(payload, status) || busy;
        elements.btnFecharImportacaoCatalogo.disabled = busy;
        elements.btnCancelarImportacaoCatalogo.disabled = busy || !flow.jobId || !CANCELLABLE.has(status);
        const applyResult = payload && payload.apply_result && typeof payload.apply_result === 'object' ? payload.apply_result : {};
        const fotosIgnoradas = numeroResumo(applyResult.fotos_ignoradas);
        const resultadoFotos = resultadoFotosAplicadas(applyResult);
        if (retryPhotosButton) { retryPhotosButton.hidden = !podeRetentarFotos(payload, status); retryPhotosButton.disabled = busy; }
        if (status === 'applied' && resultadoFotos) setStatus(`Aplicação concluída. ${resultadoCamposAplicados(applyResult)}${resultadoFotos}${detalhesFotosPendentes(applyResult)}${payload.refresh_failed ? ' A lista não pôde ser atualizada; use Atualizar.' : ''}`, fotosIgnoradas > 0 || payload.refresh_failed ? 'warning' : 'success');
        else if (status === 'applied' && payload.refresh_failed) setStatus('Importação aplicada com sucesso, mas a lista não pôde ser atualizada. Use Atualizar.', 'warning');
        else if (status === 'applied') setStatus('Importação aplicada com sucesso.', 'success');
        else if (status === 'cancelled') setStatus('Consulta cancelada. Nenhuma nova alteração será aplicada por este trabalho.', 'success');
        else if (TERMINAL_ERRORS.has(status)) setStatus(erroPayload(payload, 'A consulta não pôde ser concluída.'), 'error');
        else if (status === 'ready' && podeAplicar(payload, status)) { const partial = payload.partial_application === true || coverage !== true; setStatus(`${partial ? 'Prévia parcial pronta para aplicar.' : 'Prévia pronta para aplicar.'} ${reviewDetails}`, partial || summary.conflitos > 0 || summary.ignorados > 0 ? 'warning' : 'success'); }
        else if (status === 'applying') setStatus(progressoDetalhadoAplicacao(payload), 'loading');
        else setStatus(mensagemProgresso(payload) || erroPayload(payload, 'Consultando o catálogo externo...'), 'loading');
        if (status === 'ready' && !podeAplicar(payload, status)) {
            const warnings = totalWarnings(payload);
            const needsReview = summary.conflitos > 0 || summary.ignorados > 0 || warnings > 0;
            setStatus(needsReview
                ? `${flow.retryPhotosPreview ? 'Prévia de fotos concluída para revisão. Nenhuma foto pendente pôde ser confirmada para aplicação.' : 'Prévia concluída para revisão. Nenhum produto pode ser preenchido automaticamente.'} ${reviewDetails}`
                : flow.retryPhotosPreview ? 'Prévia de fotos concluída. Nenhuma foto pendente precisa ser salva.' : 'Prévia concluída. O cadastro já está atualizado com os dados disponíveis desta fonte.', needsReview ? 'warning' : 'success');
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
    async function acompanharAplicacao(seq, storeId, source, ativo) {
        while (ativo() && isCurrent(seq, storeId)) {
            await esperar(POLL_INTERVAL_MS);
            if (!ativo() || !isCurrent(seq, storeId)) return;
            try {
                const payload = await requestJson(apiImportacao(storeId, encodeURIComponent(flow.jobId)), { headers: authHeaders() });
                if (!ativo() || !isCurrent(seq, storeId)) return;
                validarEscopo(payload, storeId, source);
                const status = statusPayload(payload);
                if (status === 'applying') { flow.lastPayload = payload; renderPayload(payload); }
                else if (status === 'applied') { flow.lastPayload = payload; return; }
                else if (status === 'cancelled' || TERMINAL_ERRORS.has(status)) { flow.lastPayload = payload; renderPayload(payload); return; }
            } catch (_pollError) { /* o resultado definitivo continua sendo aguardado no POST */ }
        }
    }
    function resetPreview(source, storeId) {
        if (retryPhotosButton) retryPhotosButton.hidden = true;
        const sourceLabel = SOURCES[source];
        const store = state.lojas.find(item => item.store_id === storeId);
        elements.importacaoCatalogoTitulo.textContent = source === 'mercadolivre' ? 'Trazer catálogo do Mercado Livre' : `Trazer catálogo da ${sourceLabel}`;
        elements.importacaoCatalogoLoja.textContent = String(store && store.nome || storeId);
        elements.importacaoCatalogoProgress.value = 0; elements.importacaoCatalogoProgressText.textContent = '0%';
        elements.importacaoCatalogoConflitos.hidden = true;
        elements.importacaoCatalogoConflitosLista.replaceChildren();
        elements.tBodyImportacaoCatalogo.replaceChildren();
        elements.modalImportacaoCatalogo.querySelectorAll('details').forEach(details => { details.open = false; });
        [elements.importacaoCatalogoNovos, elements.importacaoCatalogoPreencher,
            elements.importacaoCatalogoInalterados, elements.importacaoCatalogoConflitosTotal,
            elements.importacaoCatalogoIgnorados].forEach(item => { item.textContent = '0'; });
        elements.btnAplicarImportacaoCatalogo.disabled = true; elements.btnFecharImportacaoCatalogo.disabled = false;
        elements.btnCancelarImportacaoCatalogo.disabled = true; state.importacaoCatalogoAplicando = false; state.importacaoCatalogoCancelando = false;
        setStatus(source === 'mercadolivre' ? 'Iniciando consulta somente leitura no Mercado Livre...' : `Iniciando consulta somente leitura na ${sourceLabel}...`, 'loading');
    }
    function encerrarFluxo(fecharModal) {
        if (flow.applying || flow.cancelling || flow.retrying || state.importacaoCatalogoAplicando || state.importacaoCatalogoCancelando) return false;
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
        if (!SOURCES[sourceKey] || state.importacaoCatalogoAtiva || state.importacaoCatalogoAplicando || state.importacaoCatalogoCancelando || state.carregandoProdutos) return false;
        const storeId = String(state.storeIdSelecionado || '').trim();
        if (!storeId || !storeTools.lojaExiste(state.lojas, storeId)) return false;
        const seq = ++flow.requestSeq;
        Object.assign(flow, { applying: false, cancelling: false, retrying: false, retryPhotosPreview: false, jobId: '', lastFocus: global.document.activeElement, lastPayload: null, opened: true, source: sourceKey, storeId });
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
        if (flow.applying || flow.cancelling || flow.retrying || !flow.opened || !flow.jobId || !podeAplicar(payload, status)) return false;
        const seq = flow.requestSeq;
        const storeId = flow.storeId;
        flow.applying = true;
        const summary = resumoPayload(payload);
        flow.lastPayload = { ...payload, status: 'applying', can_apply: false,
            progress: { stage: 'applying', current: 0, total: summary.aplicaveis, percent: 0,
                message: 'Preparando informações e fotos para salvar no Cadastro...' } };
        renderPayload(flow.lastPayload);
        dialogImportacao().focus();
        let acompanhar = true;
        const acompanhamento = acompanharAplicacao(seq, storeId, flow.source, () => acompanhar);
        try {
            const result = await requestJson(apiImportacao(storeId, `${encodeURIComponent(flow.jobId)}/aplicar`), {
                method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }), body: '{}',
            });
            acompanhar = false;
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
            acompanhar = false;
            await acompanhamento;
            if (seq === flow.requestSeq) {
                flow.applying = false;
                if (isCurrent(seq, storeId)) renderPayload(flow.lastPayload || {});
                else state.importacaoCatalogoAplicando = false;
            }
            actions.atualizarEstadoMutacoes();
        }
    }
    async function retentarFotos() {
        const previous = flow.lastPayload;
        if (flow.applying || flow.cancelling || flow.retrying || !flow.opened || !flow.jobId
            || !podeRetentarFotos(previous, statusPayload(previous))) return false;
        const seq = ++flow.requestSeq, storeId = flow.storeId, source = flow.source, previousJobId = flow.jobId;
        flow.retrying = true; state.importacaoCatalogoAtiva = true;
        renderPayload(previous);
        setStatus('Preparando uma nova prévia das fotos que não puderam ser salvas...', 'loading');
        let retryError = '';
        try {
            const result = await requestJson(apiImportacao(storeId, `${encodeURIComponent(previousJobId)}/retentar-fotos`), {
                method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }), body: '{}',
            });
            if (!isCurrent(seq, storeId)) return false;
            validarEscopo(result, storeId, source);
            const nextJobId = String(result.job_id || '').trim();
            if (!nextJobId || nextJobId === previousJobId || statusPayload(result) !== 'ready') throw new Error('Não foi possível preparar a nova prévia das fotos.');
            flow.jobId = nextJobId; flow.lastPayload = result; flow.retryPhotosPreview = true;
            elements.importacaoCatalogoTitulo.textContent = 'Revisar fotos pendentes do Mercado Livre';
            return true;
        } catch (error) {
            if (isCurrent(seq, storeId)) retryError = `Não foi possível consultar as fotos pendentes: ${error.message}. As alterações já aplicadas ao cadastro foram mantidas.`;
            return false;
        } finally {
            flow.retrying = false;
            if (isCurrent(seq, storeId)) { renderPayload(flow.lastPayload || previous); if (retryError) setStatus(retryError, 'error'); }
            else { state.importacaoCatalogoAplicando = false; state.importacaoCatalogoAtiva = false; }
            actions.atualizarEstadoMutacoes();
        }
    }
    async function cancelar() {
        const payload = flow.lastPayload;
        const status = statusPayload(payload);
        if (flow.applying || flow.cancelling || flow.retrying || !flow.opened || !flow.jobId || !CANCELLABLE.has(status)) return false;
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
        const busy = flow.applying || flow.cancelling || flow.retrying || state.importacaoCatalogoAplicando;
        if (event.key === 'Escape') {
            if (busy) { event.preventDefault(); dialogImportacao().focus(); return; }
            encerrarFluxo(true);
            return;
        }
        if (event.key !== 'Tab') return;
        if (busy) { event.preventDefault(); dialogImportacao().focus(); return; }
        const focusable = [elements.btnFecharImportacaoCatalogo, elements.btnCancelarImportacaoCatalogo,
            retryPhotosButton, elements.btnAplicarImportacaoCatalogo].filter(item => item && !item.disabled && !item.hidden);
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && global.document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && global.document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
    function init() {
        retryPhotosButton = global.document.createElement('button');
        retryPhotosButton.id = 'btnRetentarFotosImportacaoCatalogo'; retryPhotosButton.type = 'button';
        retryPhotosButton.className = 'btn btn-secondary'; retryPhotosButton.hidden = true;
        retryPhotosButton.textContent = 'Tentar fotos pendentes novamente';
        elements.btnAplicarImportacaoCatalogo.before(retryPhotosButton);
        retryPhotosButton.addEventListener('click', retentarFotos);
        elements.btnFecharImportacaoCatalogo.addEventListener('click', () => encerrarFluxo(true)); elements.btnCancelarImportacaoCatalogo.addEventListener('click', cancelar);
        elements.btnAplicarImportacaoCatalogo.addEventListener('click', aplicar); global.document.addEventListener('keydown', onKeydown);
    }
    cadastro.importacoesCatalogos = Object.freeze({ aplicar, abrir, cancelar, retentarFotos, init, onStoreChanged });
    cadastro.components.add('importacoes-catalogos');
})(window);
