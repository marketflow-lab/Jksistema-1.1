(function (root) {
    'use strict';
    const DEADLINE_MS = 30000;
    const REQUEST_MS = 8000;
    let active = null, generation = 0, session = JSON.stringify(obterAuthHeaders());
    let accepted = false, storesSignature = '', connectedSignature = '', snapshotStatus = '';
    const abortError = () => new DOMException('Consulta substituída', 'AbortError');

    function cancel(clear = false) {
        generation += 1;
        active?.abort();
        active = null;
        if (!clear) return;
        accepted = false; storesSignature = ''; connectedSignature = ''; snapshotStatus = '';
        root.JKPerguntasLoading?.limpar();
        state.lojas = []; state.lojaSelecionada = ''; state.lojaSelecionadaStoreId = '';
        state.lojaConfiguracaoPerguntas = '';
        lojasGrid.innerHTML = ''; posVendaLojasGrid.innerHTML = '';
        lojasStatus.textContent = '';
    }
    function checkSession() {
        const next = JSON.stringify(obterAuthHeaders());
        if (session !== next) {
            cancel(true); session = next;
            root.JKPerguntasLoading?.verificarSessao();
        }
        return next;
    }
    function assertCurrent(token, capturedSession) {
        if (checkSession() !== capturedSession || generation !== token) throw abortError();
    }
    function wait(ms, signal) {
        return new Promise((resolve, reject) => {
            const finish = () => { signal.removeEventListener('abort', abort); resolve(); };
            const timer = setTimeout(finish, ms);
            const abort = () => { clearTimeout(timer); signal.removeEventListener('abort', abort); reject(abortError()); };
            signal.addEventListener('abort', abort, { once: true });
            if (signal.aborted) abort();
        });
    }
    async function request(signal, remaining, token, capturedSession) {
        const controller = new AbortController();
        const abort = () => controller.abort();
        signal.addEventListener('abort', abort, { once: true });
        const timer = setTimeout(abort, Math.min(REQUEST_MS, remaining));
        try {
            const response = await fetch('/api/mercadolivre/perguntas/lojas', {
                headers: obterAuthHeaders(), cache: 'no-store', signal: controller.signal
            });
            const data = await response.json().catch(() => ({}));
            assertCurrent(token, capturedSession);
            if (controller.signal.aborted) throw abortError();
            if (!response.ok) {
                const detail = data.detail;
                const error = new Error(typeof detail === 'string' ? detail : detail?.message || 'Não foi possível consultar as lojas.');
                error.status = response.status;
                error.code = detail?.code;
                const retry = Number(response.headers.get('Retry-After'));
                error.retryAfter = Number.isFinite(retry) ? Math.max(0, retry * 1000) : 0;
                throw error;
            }
            if (data.success !== true || !Array.isArray(data.lojas) || !['ready', 'updating'].includes(data.snapshot?.status)
                || !data.snapshot.generation || !data.snapshot.published_at) {
                throw new Error('A configuração das lojas ainda não está disponível.');
            }
            return data;
        } catch (error) {
            assertCurrent(token, capturedSession);
            if (signal.aborted) throw abortError();
            if (error.name === 'AbortError') throw new Error('A lista de lojas demorou mais que o esperado.');
            throw error;
        } finally {
            clearTimeout(timer); signal.removeEventListener('abort', abort);
        }
    }
    function showStatus(message, retry = false) {
        lojasStatus.textContent = message;
        if (retry) {
            const button = document.createElement('button');
            button.type = 'button'; button.className = 'action-btn secondary';
            button.dataset.retryStores = ''; button.textContent = 'Tentar novamente';
            button.addEventListener('click', () => carregar());
            lojasStatus.appendChild(button);
        }
    }
    function applySnapshot(data) {
        const signature = JSON.stringify(data.lojas);
        const changed = !accepted || signature !== storesSignature || snapshotStatus !== data.snapshot.status;
        state.lojas = data.lojas;
        const connected = lojasMercadoLivreConectadas();
        const selection = state.lojaSelecionadaStoreId
            ? state.lojas.find(loja => String(loja.store_id) === state.lojaSelecionadaStoreId)
            : state.lojas.find(loja => String(loja.nome) === state.lojaSelecionada);
        if (!connected.length) {
            state.lojaSelecionada = ''; state.lojaSelecionadaStoreId = '';
            state.lojaConfiguracaoPerguntas = '';
        } else if (!selection?.mercadolivre_conectado || todasAsLojasSelecionadas()) {
            state.lojaSelecionada = TODAS_LOJAS_VALUE; state.lojaSelecionadaStoreId = '';
            state.lojaConfiguracaoPerguntas = TODAS_LOJAS_VALUE;
        } else {
            state.lojaSelecionada = selection.nome;
            state.lojaSelecionadaStoreId = String(selection.store_id || '');
            state.lojaConfiguracaoPerguntas = selection.nome;
        }
        const fingerprint = JSON.stringify(connected.map(loja => [loja.store_id, loja.seller_id, loja.site_id]));
        const reload = !accepted || fingerprint !== connectedSignature;
        accepted = true; storesSignature = signature; connectedSignature = fingerprint; snapshotStatus = data.snapshot.status;
        if (changed && (data.lojas.length || data.snapshot.status === 'ready')) {
            renderizarLojas(); atualizarCabecalhoPosVenda(); atualizarCabecalhoMediacao();
            root.JKSolicitacoes?.atualizarCabecalho();
            iniciarAutomacaoPerguntas();
        }
        if (reload) {
            root.JKPerguntasLoading?.verificarSessao();
            if (state.lojaSelecionada) void carregarPerguntas().catch(() => {});
            void Promise.resolve(carregarContadoresNotificacoes(true)).catch(() => {});
        }
        showStatus(data.snapshot.status === 'updating' ? 'Exibindo última configuração disponível'
            : data.lojas.length ? `${data.lojas.length} loja(s) cadastrada(s)` : 'Nenhuma loja cadastrada');
    }
    async function carregar() {
        const capturedSession = checkSession();
        root.JKPerguntasLoading?.verificarSessao();
        cancel();
        const token = generation;
        const controller = active = new AbortController();
        const deadline = Date.now() + DEADLINE_MS;
        let retryIndex = 0, lastError = null, updating = false;
        showStatus(accepted ? 'Atualizando configuração das lojas...' : 'Carregando lojas...');
        try {
            while (Date.now() < deadline) {
                try {
                    const data = await request(controller.signal, deadline - Date.now(), token, capturedSession);
                    assertCurrent(token, capturedSession);
                    applySnapshot(data);
                    updating = data.snapshot.status === 'updating';
                    if (!updating) return;
                    lastError = null;
                } catch (error) {
                    assertCurrent(token, capturedSession);
                    if (controller.signal.aborted) return;
                    lastError = error;
                    if (error.status === 401 || error.status === 403) {
                        cancel(true); showStatus('Acesso às lojas indisponível. Verifique sua sessão.'); return;
                    }
                    showStatus(updating || accepted ? 'Exibindo última configuração disponível'
                        : error.code === 'stores_snapshot_initializing' ? 'Preparando configuração das lojas...'
                            : 'Configuração das lojas temporariamente indisponível.');
                    if (error.status && error.status < 500 && error.status !== 429) break;
                }
                const delay = Math.min(8000, Math.max(2000 * 2 ** Math.min(retryIndex++, 2), lastError?.retryAfter || 0));
                const remaining = deadline - Date.now();
                if (remaining <= 0) break;
                await wait(Math.min(delay, remaining), controller.signal);
            }
            assertCurrent(token, capturedSession);
            showStatus(accepted ? 'Exibindo última configuração disponível. Atualização indisponível.'
                : lastError?.message || 'A configuração das lojas ainda não está disponível.', true);
        } catch (error) {
            if (error.name !== 'AbortError') throw error;
        } finally { if (generation === token) active = null; }
    }
    root.JKPerguntasStores = { carregar, limpar: () => cancel(true), verificarSessao: checkSession };
    root.addEventListener('storage', checkSession);
    root.addEventListener('focus', checkSession);
    root.addEventListener('jk:logout', () => cancel(true));
    root.addEventListener('pagehide', () => cancel(true));
})(window);
