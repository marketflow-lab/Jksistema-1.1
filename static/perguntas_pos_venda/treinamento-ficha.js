// Cache de leitura privado da sessão. Nenhuma ficha ou edição vai para storage.
let treinamentoAuth = '', treinamentoEpoch = 0;
const treinamentoRequests = new Set();

function limparSessaoTreinamento() {
    treinamentoEpoch++;
    treinamentoRequests.forEach(request => request.controller.abort());
    treinamentoRequests.clear();
    treinamentoSync.stores.clear(); treinamentoSync.requestId++; treinamentoSync.skuRequestId++;
    treinamentoDetalhesRequestId++; treinamentoDetalhesController?.abort(); treinamentoCatalogoRequestId++;
    state.treinamentoCarregado = false; state.treinamentoSkuNotasAtual = ''; state.treinamentoFormularioIdentity = '';
    state.produtosTreinamento = []; state.produtosTreinamentoCarregados = false; state.produtosTreinamentoEscopo = null;
    state.treinamentoContexto = { notas_sku: {} };
    state.treinamentoDados = { perguntas_anuncio: { exemplos: [] }, pos_venda: { exemplos: [] } };
    [aiTrainingOrientacoes, aiTrainingContextoLoja, aiTrainingCompatibilidade, aiTrainingProibicoes, aiTrainingNotasSku, aiTrainingSku].forEach(input => { if (input) input.value = ''; });
    for (const id of ['ai-training-sku-details', 'ai-training-sku-guidance-view', 'ai-training-chat', 'ai-training-examples-list', 'ai-training-sku-list', 'ai-training-general-summary', 'ai-training-general-conflict', 'ai-training-sku-conflict']) {
        document.getElementById(id)?.replaceChildren();
    }
    for (const target of ['general', 'sku']) {
        const source = document.getElementById(`ai-training-${target}-source`);
        source?.classList.add('hidden');
        const body = source?.querySelector('pre'); if (body) body.textContent = '';
    }
    aiTrainingSkuPopover?.classList.add('hidden');
    aiTrainingSkuPopover?.setAttribute('aria-hidden', 'true');
}

function identidadeSessaoTreinamento(store = lojaEscopoTreinamento()) {
    const auth = JSON.stringify(obterAuthHeaders());
    if (treinamentoAuth && auth !== treinamentoAuth) limparSessaoTreinamento();
    treinamentoAuth = auth;
    const row = state.lojas.find(item => String(item.store_id) === String(store));
    return JSON.stringify([treinamentoEpoch, store, row?.seller_id || '', row?.site_id || '']);
}

function revisaoEditorTreinamento(session, key) {
    return key.startsWith('sku:') ? session.fichaRevisions?.[key.slice(4)] || session.snapshot?.editorial?.revision
        : session.snapshot?.editorial?.revision;
}

function revisaoFonteFichaTreinamento(details) {
    return String(details?.source_revision || details?.catalog?.source_revision || details?.catalog_document?.source_revision || '');
}

function invalidarConsultasFichaTreinamento() {
    treinamentoDetalhesRequestId++; treinamentoDetalhesController?.abort();
    treinamentoSync.requestId++;
    treinamentoRequests.forEach(request => { if (request.method === 'GET') request.controller.abort(); });
}

async function consultarTreinamento(url, options = {}, timeoutMs = 8000) {
    const requestStore = lojaEscopoTreinamento();
    const identity = identidadeSessaoTreinamento(requestStore);
    const controller = new AbortController();
    const abort = () => controller.abort();
    options.signal?.addEventListener('abort', abort, { once: true });
    if (options.signal?.aborted) abort();
    const entry = { controller, method: options.method || 'GET' };
    treinamentoRequests.add(entry);
    const timer = setTimeout(abort, timeoutMs);
    try {
        const response = await fetch(url, { ...options, cache: 'no-store', signal: controller.signal,
            headers: { ...obterAuthHeaders(), ...(options.body ? { 'Content-Type': 'application/json' } : {}) } });
        const data = await response.json().catch(() => ({}));
        if (identity !== identidadeSessaoTreinamento(requestStore) || controller.signal.aborted) throw new DOMException('Consulta substituída', 'AbortError');
        if (response.status === 401 || response.status === 403) {
            limparSessaoTreinamento();
            aiTrainingStatus.textContent = 'Acesso indisponível. Verifique sua sessão.';
        }
        if (!response.ok && (!options.allowError || [401, 403].includes(response.status))) {
            const error = new Error(erroRespostaTreinamento(data, 'Não foi possível atualizar a leitura.'));
            error.status = response.status; error.code = data.detail?.code;
            const retryAfter = response.headers?.get('Retry-After');
            if (retryAfter) {
                const delay = /^\d+(?:\.\d+)?$/.test(retryAfter.trim())
                    ? Number(retryAfter) * 1000 : Date.parse(retryAfter) - Date.now();
                if (Number.isFinite(delay)) error.retryAfterMs = Math.max(1000, delay);
            }
            throw error;
        }
        return { response, data };
    } finally {
        clearTimeout(timer); options.signal?.removeEventListener('abort', abort); treinamentoRequests.delete(entry);
    }
}

function confirmarFichaSalvaTreinamento(session, data, sku) {
    if (!sku) return;
    session.fichaRevisions ??= {}; session.fichaRevisions[sku] = data.editorial?.revision;
    if (data.sku_details?.sku === sku) { session.skuDetails ??= {}; session.skuDetails[sku] = data.sku_details; }
    if (session.fichaCache) delete session.fichaCache[sku];
}

async function prepararEdicaoModeloSkuTreinamento(sku) {
    const session = sessaoTreinamento(), store = lojaEscopoTreinamento();
    if (!sku || !session) return;
    const { data } = await consultarTreinamento(`/api/mercadolivre/ia-treinamento/ficha?store_id=${encodeURIComponent(store)}&sku=${encodeURIComponent(sku)}`);
    if (session !== sessaoTreinamento() || session.saving) throw new DOMException('Consulta substituída', 'AbortError');
    receberFichaTreinamento(data, session, sku);
    if (['invalid', 'conflict', 'unavailable', 'not_indexed'].includes(data.sku_details.editorial_state?.status || data.sku_details.editorial_state)) {
        throw new Error('A orientação deste SKU está indisponível. Atualize a ficha antes de editar o modelo.');
    }
}

function receberFichaTreinamento(data, session, sku) {
    if (data.success !== true || data.store_id !== lojaEscopoTreinamento() || data.sku !== sku
        || data.sku_details?.sku !== sku || !data.editorial?.revision || !data.snapshot?.generation
        || !Object.hasOwn(data.notas_sku || {}, sku) || !Object.hasOwn(data.caracteristicas_sku || {}, sku)
        || !Array.isArray(data.exemplos?.perguntas_anuncio)) {
        throw new Error('A ficha recebida não corresponde à identidade selecionada.');
    }
    const store = state.lojas.find(item => String(item.store_id) === data.store_id);
    if ((data.seller_id !== undefined && String(data.seller_id) !== String(store?.seller_id || ''))
        || (data.site_id !== undefined && String(data.site_id) !== String(store?.site_id || ''))) {
        throw new Error('A identidade da conta mudou. Atualize as lojas.');
    }
    guardarEdicaoTreinamento();
    const key = `sku:${sku}`, draft = session.drafts[key];
    const base = session.snapshot || {};
    const editorialState = data.sku_details.editorial_state?.status || data.sku_details.editorial_state;
    const validEditorial = !['invalid', 'conflict', 'unavailable', 'not_indexed'].includes(editorialState);
    const partial = validEditorial ? { notas: data.notas_sku?.[sku] ?? '',
        exemplos: exemplosDoSkuTreinamento(data.exemplos?.perguntas_anuncio, sku),
        caracteristicas: data.caracteristicas_sku?.[sku] || {} } : {
        notas: base.notas_sku?.[sku] || '', exemplos: exemplosDoSkuTreinamento(base.exemplos?.perguntas_anuncio, sku),
        caracteristicas: base.caracteristicas_sku?.[sku] || {}
    };
    const oldDetails = session.skuDetails?.[sku];
    if (draft) {
        if (['invalid', 'conflict'].includes(editorialState)) draft.conflict = true;
        const sourceChanged = revisaoFonteFichaTreinamento(oldDetails) !== revisaoFonteFichaTreinamento(data.sku_details)
            || JSON.stringify(oldDetails?.characteristics?.map(field => [field.key, field.original_value])) !== JSON.stringify(data.sku_details.characteristics?.map(field => [field.key, field.original_value]));
        if (!iguaisTreinamento(draft.base, partial) || (sourceChanged && !iguaisTreinamento(draft.value.caracteristicas, draft.base.caracteristicas))) {
            draft.conflict = true; if (sourceChanged) draft.sourceConflict = true;
        }
    }
    // A revisão de um SKU não autoriza salvar um formulário geral possivelmente antigo.
    session.snapshot = { ...base,
        notas_sku: { ...base.notas_sku, [sku]: partial.notas },
        caracteristicas_sku: { ...base.caracteristicas_sku, [sku]: partial.caracteristicas },
        exemplos: { ...base.exemplos, perguntas_anuncio: [...(base.exemplos?.perguntas_anuncio || []).filter(item => String(item.sku || '') !== sku), ...partial.exemplos] },
        editorial: { ...base.editorial, skus: { ...base.editorial?.skus, [sku]: data.editorial.skus?.[sku] } }
    };
    session.fichaRevisions ??= {};
    if (validEditorial) session.fichaRevisions[sku] = data.editorial.revision;
    session.fichaCache ??= {}; session.fichaCache[sku] = { at: Date.now(), state: data.snapshot.state, generation: data.snapshot.generation, checkedAt: data.snapshot.checked_at };
    session.skuDetails ??= {}; session.skuDetails[sku] = data.sku_details;
    session.detailsError = '';
    session.detailsPending = ''; session.detailsRetryAt = 0;
    state.treinamentoContexto.notas_sku[sku] = draft?.value.notas ?? partial.notas;
    const examples = state.treinamentoDados.perguntas_anuncio?.exemplos || [];
    state.treinamentoDados.perguntas_anuncio = { ...state.treinamentoDados.perguntas_anuncio,
        exemplos: [...examples.filter(item => String(item.sku || '') !== sku), ...(draft?.value.exemplos ?? partial.exemplos)] };
    state.treinamentoFormularioIdentity = session.identity;
    const editing = !aiTrainingSkuEditor?.classList.contains('hidden') || document.getElementById('ai-training-sku-details')?.querySelector('textarea');
    if (!editing) { renderizarNotasSkuTreinamento(); exibirLeituraOrientacaoSku(); }
    else atualizarEstadoSincronizacaoTreinamento();
    renderizarConflitosTreinamento();
}

function aguardarFichaTreinamento(ms, signal) {
    return new Promise((resolve, reject) => {
        const abort = () => { clearTimeout(timer); signal.removeEventListener('abort', abort); reject(new DOMException('Consulta substituída', 'AbortError')); };
        const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, ms);
        signal.addEventListener('abort', abort, { once: true }); if (signal.aborted) abort();
    });
}

async function carregarDetalhesSkuTreinamento(force = false, refresh = false) {
    const store = lojaEscopoTreinamento(), sku = String(aiTrainingSku.value || ''), session = sessaoTreinamento();
    if (!store || !sku || session?.saving || (session.detailsLoading === sku && !force)) return;
    if (!refresh && session.detailsPending === sku && session.detailsRetryAt > Date.now()) return;
    const cached = session.fichaCache?.[sku];
    if (!force && cached && Date.now() - cached.at < 30000 && ['ready', 'fresh'].includes(cached.state)) {
        renderizarDetalhesSkuTreinamento(); return;
    }
    const identity = identidadeSessaoTreinamento();
    const requestId = ++treinamentoDetalhesRequestId;
    treinamentoDetalhesController?.abort();
    const controller = treinamentoDetalhesController = new AbortController();
    treinamentoSync.requestId++;
    const current = () => identity === identidadeSessaoTreinamento() && requestId === treinamentoDetalhesRequestId
        && store === lojaEscopoTreinamento() && sku === aiTrainingSku.value && !session.saving;
    session.detailsLoading = sku; session.detailsRequestId = requestId; session.detailsError = ''; session.detailsRetryStopped = false;
    session.detailsPending = ''; session.detailsRetryAt = 0;
    renderizarDetalhesSkuTreinamento();
    const deadline = Date.now() + 30000;
    let attempt = 0, requested = false;
    async function schedule() {
        if (requested) return;
        await consultarTreinamento('/api/mercadolivre/ia-treinamento/ficha/atualizar', {
            method: 'POST', body: JSON.stringify({ store_id: store, sku }), signal: controller.signal
        }, Math.max(1, Math.min(8000, deadline - Date.now())));
        requested = true;
    }
    try {
        while (current() && Date.now() < deadline) {
            try {
                if (refresh) await schedule();
                const { data } = await consultarTreinamento(`/api/mercadolivre/ia-treinamento/ficha?${new URLSearchParams({ store_id: store, sku })}`,
                    { signal: controller.signal }, Math.max(1, Math.min(8000, deadline - Date.now())));
                if (!current()) return;
                receberFichaTreinamento(data, session, sku);
                const refreshed = !refresh || !cached || data.snapshot.generation !== cached.generation || data.snapshot.checked_at !== cached.checkedAt;
                if (['ready', 'fresh'].includes(data.snapshot.state) && refreshed) return;
                await schedule();
            } catch (error) {
                if (!current() || controller.signal.aborted || [401, 403].includes(error.status)) return;
                if (error.status === 503 && error.code === 'training_index_initializing') {
                    // O GET já agenda a leitura. A primeira geração pode levar mais de um ciclo.
                    session.detailsPending = sku; session.detailsError = '';
                    session.detailsRetryAt = Date.now() + (error.retryAfterMs ?? 2000);
                    renderizarDetalhesSkuTreinamento();
                } else {
                    session.detailsPending = ''; session.detailsRetryAt = 0;
                    session.detailsError = error.name === 'AbortError' ? 'A atualização demorou mais que o esperado.' : mensagemErro(error);
                    renderizarDetalhesSkuTreinamento();
                    if (error.status && error.status < 500 && error.status !== 429) return;
                    try { await schedule(); } catch (scheduleError) { if (!current() || [401, 403].includes(scheduleError.status)) return; }
                }
            }
            const remaining = deadline - Date.now();
            if (remaining <= 0) break;
            const delay = session.detailsPending === sku ? Math.max(0, session.detailsRetryAt - Date.now())
                : 2000 * 2 ** Math.min(attempt++, 2);
            await aguardarFichaTreinamento(Math.min(delay, remaining), controller.signal);
        }
        if (current() && !session.detailsError && !session.detailsPending) session.detailsError = 'A atualização continua indisponível. Tente novamente.';
    } catch (error) {
        if (current() && !controller.signal.aborted && ![401, 403].includes(error.status)) session.detailsError = mensagemErro(error);
    } finally {
        if (session.detailsRequestId === requestId) session.detailsLoading = '';
        if (current()) {
            session.detailsRetryStopped = Boolean(session.detailsError);
            renderizarDetalhesSkuTreinamento();
        }
    }
}

window.addEventListener('storage', () => identidadeSessaoTreinamento());
window.addEventListener('focus', () => identidadeSessaoTreinamento());
window.addEventListener('jk:logout', limparSessaoTreinamento);
window.addEventListener('pagehide', limparSessaoTreinamento);
