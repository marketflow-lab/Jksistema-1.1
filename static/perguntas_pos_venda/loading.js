/* In-memory question loading. No question content is persisted in browser storage. */
(function (root) {
    'use strict';
    const keyOf = q => `${q.store_id || q.loja || ''}::${q.id}`;
    function compareQuestions(a, b) {
        const date = (Date.parse(b.date_created) || 0) - (Date.parse(a.date_created) || 0);
        return date || String(a.store_id || '').localeCompare(String(b.store_id || ''))
            || String(a.id).localeCompare(String(b.id), undefined, { numeric: true });
    }
    async function pool(values, fn, size = 4) {
        let index = 0;
        await Promise.all(Array.from({ length: Math.min(size, values.length) }, async () => {
            while (index < values.length) await fn(values[index++]);
        }));
    }
    class QuestionPager {
        constructor(stores, request, status = '') {
            this.request = request;
            this.status = status;
            this.stores = stores.map(store => ({ store, chunks: new Map(), buffer: [], offset: 0, done: false, total: 0, error: null, summary: {} }));
            this.pages = [];
            this.seen = new Set();
            this.initialized = false;
            this.updated = 0;
        }
        async fetchStore(entry, force = false) {
            if (entry.done) return;
            const offset = entry.offset;
            try {
                const data = entry.chunks.get(offset) || await this.request(entry.store, offset, this.status, force);
                entry.chunks.set(offset, data);
                entry.buffer.push(...(data.questions || []).map(q => ({ ...q, loja: entry.store.nome, store_id: entry.store.store_id, site_id: entry.store.site_id })));
                entry.buffer.sort(compareQuestions);
                entry.total = Number(data.total || 0);
                entry.summary = data.status_resumo || {};
                entry.updated = Number(data.consultado_em || Date.now());
                entry.stale = Boolean(data.stale);
                entry.partial = Boolean(data.partial);
                entry.limited = Boolean(data.interrompido || data.limited);
                const next = data.next_offset;
                entry.done = next == null || Number(next) <= offset || !(data.questions || []).length;
                entry.offset = entry.done ? offset : Number(next);
                entry.error = null;
            } catch (error) {
                if (error.name === 'AbortError' || error.scope === 'session' || this.initialized) throw error;
                entry.error = error;
                entry.done = true;
            }
        }
        async initialize(progress, force = false) {
            if (this.initialized) return;
            let completed = 0;
            await pool(this.stores, async entry => {
                await this.fetchStore(entry, force);
                completed++;
                if (progress) progress(this.stores.flatMap(s => s.buffer).sort(compareQuestions).slice(0, 20), completed, this);
            });
            this.initialized = true;
            this.updated = Date.now();
        }
        async page(number, progress, force = false) {
            // Build against an isolated snapshot: cancellation must not consume cursors or rows.
            const sequence = this.sequence = (this.sequence || 0) + 1;
            const draft = Object.assign(Object.create(QuestionPager.prototype), this, {
                stores: this.stores.map(entry => ({ ...entry, chunks: new Map(entry.chunks), buffer: [...entry.buffer] })),
                pages: [...this.pages], seen: new Set(this.seen)
            });
            const result = await draft.buildPage(number, progress, force);
            if (sequence !== this.sequence) throw new DOMException('Página substituída', 'AbortError');
            for (const name of ['stores', 'pages', 'seen', 'initialized', 'updated']) this[name] = draft[name];
            return result;
        }
        async buildPage(number, progress, force = false) {
            await this.initialize(progress, force);
            while (this.pages.length < number) {
                const page = [];
                while (page.length < 20) {
                    await pool(this.stores.filter(s => !s.buffer.length && !s.done), s => this.fetchStore(s, force));
                    const candidates = this.stores.filter(s => s.buffer.length).sort((a, b) => compareQuestions(a.buffer[0], b.buffer[0]));
                    if (!candidates.length) break;
                    const question = candidates[0].buffer.shift();
                    if (!this.seen.has(keyOf(question))) {
                        this.seen.add(keyOf(question));
                        page.push(question);
                    }
                }
                this.pages.push(page);
                if (!page.length) break;
            }
            return this.pages[number - 1] || [];
        }
        get total() { return this.stores.reduce((sum, s) => sum + s.total, 0); }
        get hasNext() { return this.stores.some(s => s.buffer.length || !s.done); }
    }
    if (typeof module !== 'undefined' && module.exports) module.exports = { QuestionPager, compareQuestions, pool };
    if (!root || !root.document) return;

    const views = new Map(), itemCache = new Map(), detailCache = new Map(), drafts = new Map(), revisions = new Map();
    let generation = 0, controller = null, detailRun = null, session = '', storesFingerprint = '', currentView = '', activePager = null;
    const maxStale = 600000;
    function boundedSet(map, key, value) {
        map.delete(key); map.set(key, value);
        if (map.size > 500) map.delete(map.keys().next().value);
    }
    function clear(resetStores = true) {
        generation++; controller?.abort(); detailRun?.controller.abort(); detailRun = null;
        views.clear(); itemCache.clear(); detailCache.clear(); drafts.clear(); revisions.clear();
        currentView = ''; activePager = null;
        state.notificacoes = { carregando: false, atualizadoEm: 0, lojas: {}, totais: {}, erros: {} };
        if (typeof renderizarNotificacoes === 'function') renderizarNotificacoes();
        if (resetStores) {
            state.lojas = []; state.lojaSelecionada = ''; state.lojaSelecionadaStoreId = '';
            if (typeof lojasGrid !== 'undefined') lojasGrid.innerHTML = '';
            if (typeof posVendaLojasGrid !== 'undefined') posVendaLojasGrid.innerHTML = '';
        }
        state.perguntas = []; state.perguntaSelecionadaKey = ''; state.totalPerguntas = 0;
        if (typeof perguntasList !== 'undefined') perguntasList.innerHTML = '';
        if (typeof perguntasDetail !== 'undefined') perguntasDetail.innerHTML = '';
    }
    function checkSession() {
        const next = JSON.stringify(obterAuthHeaders());
        if (session && session !== next) {
            if (sessionIdentity(session) === sessionIdentity(next)) {
                saveDraft(); generation++; controller?.abort(); detailRun?.controller.abort(); detailRun = null;
                detailCache.clear(); itemCache.clear();
                state.perguntas.forEach(q => { q._detailReady = false; q._itemReady = false; });
                const renewedGeneration = generation;
                queueMicrotask(() => { if (renewedGeneration === generation) load(state.paginaPerguntas || 1, { background: true, forcar: true }); });
            } else clear();
        }
        session = next;
        const fingerprint = JSON.stringify(lojasMercadoLivreConectadas().map(store => [store.store_id, store.seller_id, store.site_id]));
        const previous = storesFingerprint;
        storesFingerprint = fingerprint;
        if (previous && previous !== fingerprint) {
            const current = new Map(JSON.parse(fingerprint).map(entry => [String(entry[0]), JSON.stringify(entry)]));
            JSON.parse(previous).forEach(entry => { if (current.get(String(entry[0])) !== JSON.stringify(entry)) revokeStore(String(entry[0])); });
        }
    }
    async function request(path, values, signal) {
        checkSession();
        const capturedSession = session;
        const response = await fetch(`/api/mercadolivre/perguntas/${path}?${new URLSearchParams(values)}`, { headers: obterAuthHeaders(), cache: 'no-store', signal });
        const data = await response.json().catch(() => ({}));
        if (capturedSession !== JSON.stringify(obterAuthHeaders())) { checkSession(); throw new DOMException('Sessão alterada', 'AbortError'); }
        if (signal?.aborted) throw new DOMException('Consulta substituída', 'AbortError');
        if (!response.ok) {
            const error = new Error(mensagemErroApi(data, 'Falha ao consultar perguntas.'));
            error.status = response.status;
            error.scope = response.headers.get('X-JK-Error-Scope') || (error.status === 401 ? 'session' : 'resource');
            error.code = response.headers.get('X-JK-Error-Code') || 'request_failed';
            const retry = response.headers.get('X-JK-Retryable');
            error.retryable = retry ? retry === 'true' : [408, 429, 500, 502, 503, 504].includes(error.status);
            error.retryAfter = retryDelay(response.headers.get('Retry-After'));
            handleDenial(error, values);
            throw error;
        }
        return data;
    }
    function handleDenial(error, values, question = null) {
        if ([401, 403].includes(error.status) && error.scope === 'session') {
            clear(); return true;
        }
        if ([401, 403].includes(error.status) && error.scope === 'store') {
            revokeStore(String(values.store_id || '')); return true;
        }
        if (question && (error.code === 'generation_context_unavailable' || [403, 404].includes(error.status))) {
            rejectContext(question, error.component || 'question', [403, 404].includes(error.status));
            return true;
        }
        return false;
    }
    function generationDenial(response, question) {
        return handleDenial({ status: response.status,
            scope: response.headers.get('X-JK-Error-Scope') || (response.status === 401 ? 'session' : 'resource'),
            code: response.headers.get('X-JK-Error-Code'), component: response.headers.get('X-JK-Error-Component')
        }, { store_id: question?.store_id }, question);
    }
    function revokeStore(id) {
        if (!id) return;
        revisions.set(id, (revisions.get(id) || 0) + 1);
        for (const [key, pager] of views) if (pager.stores.some(s => String(s.store.store_id) === id)) views.delete(key);
        for (const map of [itemCache, detailCache, drafts]) for (const key of map.keys()) if (key.startsWith(`${id}::`)) map.delete(key);
        state.perguntas = state.perguntas.filter(q => String(q.store_id) !== id);
        if (!state.perguntas.some(q => chavePerguntaAtendimento(q) === state.perguntaSelecionadaKey)) state.perguntaSelecionadaKey = '';
        renderizarPerguntas();
    }
    function retryDelay(value) {
        if (!value) return 0;
        const seconds = Number(value);
        return Number.isFinite(seconds) ? Math.max(0, seconds * 1000) : Math.max(0, Date.parse(value) - Date.now()) || 0;
    }
    function pause(ms, signal) {
        return new Promise((resolve, reject) => {
            const abort = () => { clearTimeout(timer); reject(new DOMException('Consulta substituída', 'AbortError')); };
            const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve(); }, ms);
            if (signal?.aborted) abort(); else signal?.addEventListener('abort', abort, { once: true });
        });
    }
    async function retryRequest(path, values, signal, inspect = () => null) {
        const deadline = Date.now() + 30000;
        const attemptController = new AbortController();
        const abort = () => attemptController.abort();
        signal?.addEventListener('abort', abort, { once: true });
        if (signal?.aborted) abort();
        let expired = false;
        const timer = setTimeout(() => { expired = true; abort(); }, 30000);
        let lastData, lastError;
        try {
            for (let attempt = 0; attempt < 3; attempt++) {
                try {
                    lastData = await request(path, { ...values, forcar: String(values.forcar === 'true' || attempt > 0) }, attemptController.signal);
                    lastError = inspect(lastData);
                    if (!lastError) return lastData;
                } catch (error) {
                    if (error.name === 'AbortError') throw error;
                    lastError = error;
                    if ([401, 403].includes(error.status)) lastData = undefined;
                    if (lastError.retryable == null) lastError.retryable = error instanceof TypeError;
                }
                if (!lastError.retryable || attempt === 2) break;
                const delay = Math.max(attempt === 0 ? 2000 : 4000, lastError.retryAfter || 0);
                if (Date.now() + delay >= deadline) break;
                await pause(delay, attemptController.signal);
            }
            if (lastData) return lastData;
            throw lastError;
        } catch (error) {
            if (expired && !signal?.aborted && error.name === 'AbortError') {
                if (lastData) return lastData;
                throw Object.assign(new Error('Prazo de consulta esgotado. Tente novamente.'), { code: 'timeout', retryable: true });
            }
            throw error;
        } finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); }
    }
    function storeFor(question) {
        return lojasMercadoLivreConectadas().find(s => question.store_id ? String(s.store_id) === String(question.store_id) : s.nome === lojaOrigemItem(question));
    }
    function saveDraft() {
        const snapshot = capturarInteracaoPerguntas();
        if (activePager) activePager.selectedKey = state.perguntaSelecionadaKey;
        if (snapshot?.perguntaSelecionadaKey) boundedSet(drafts, snapshot.perguntaSelecionadaKey, snapshot);
        return snapshot;
    }
    function renderSafe() {
        const snapshot = saveDraft();
        renderizarPerguntas();
        restaurarInteracaoPerguntas(snapshot?.perguntaSelecionadaKey === state.perguntaSelecionadaKey ? snapshot : drafts.get(state.perguntaSelecionadaKey));
    }
    function statusText(pager, completed = pager.stores.length) {
        const failed = pager.stores.filter(s => s.error).length;
        const stale = pager.stores.some(s => s.stale);
        const limited = pager.stores.some(s => s.limited);
        return [completed < pager.stores.length ? `Lista parcial: ${completed}/${pager.stores.length} lojas consultadas.` : '',
            failed ? `${failed} loja(s) indisponível(is); resultados parciais.` : '',
            stale ? 'Dados anteriores; atualizando em segundo plano.' : '', limited ? 'Limite de paginação do Mercado Livre atingido.' : ''].filter(Boolean).join(' ');
    }
    function display(pager, questions, page, completed, background, force = false) {
        saveDraft();
        if (background && state.perguntas.length) {
            const byKey = new Map(questions.map(q => [keyOf(q), q]));
            state.perguntas = state.perguntas.map(q => byKey.get(keyOf(q)) || q);
            if (questions.some(q => !state.perguntas.some(old => keyOf(old) === keyOf(q)))) pager.pending = true;
        } else state.perguntas = questions;
        state.paginaPerguntas = page;
        state.totalPerguntas = pager.initialized && !pager.hasNext
            ? Math.min(pager.total, pager.pages.flat().length) : pager.total;
        const summary = {};
        pager.stores.forEach(s => Object.entries(s.summary).forEach(([k, v]) => { summary[k] = (summary[k] || 0) + Number(v || 0); }));
        renderizarResumo({ modo_todas: todasAsLojasSelecionadas(), total: pager.total, retornadas: questions.length, status_resumo: summary,
            lojas_consultadas: completed, erros: pager.stores.filter(s => s.error), tempo_resposta_ml: pager.metrics });
        renderizarPerguntas();
        restaurarInteracaoPerguntas(drafts.get(state.perguntaSelecionadaKey));
        perguntasStatus.textContent = statusText(pager, completed) + (pager.pending ? ' Novas perguntas disponíveis. Clique em Atualizar.' : '');
        const timestamps = pager.stores.map(s => s.updated).filter(Boolean);
        if (timestamps.length) registrarUltimaAtualizacaoPerguntas(Math.min(...timestamps));
        enrichVisible(generation, force);
    }
    function invalidate(name, answered) {
        const stores = lojasMercadoLivreConectadas();
        const matches = stores.filter(s => answered?.store_id ? String(s.store_id) === String(answered.store_id) : String(s.store_id) === String(name) || s.nome === name);
        if (matches.length !== 1) return;
        const store = matches[0];
        if (!store) return;
        const id = String(store.store_id);
        revisions.set(id, (revisions.get(id) || 0) + 1);
        views.forEach(pager => { if (pager.stores.some(s => String(s.store.store_id) === id)) { pager.updated = 0; (pager.invalidStores ||= new Set()).add(id); } });
        for (const key of detailCache.keys()) if (key.startsWith(`${id}::`)) detailCache.delete(key);
        if (answered) {
            drafts.delete(chavePerguntaAtendimento(answered));
            views.forEach(pager => pager.pages.forEach(page => page.forEach(q => { if (keyOf(q) === keyOf(answered)) Object.assign(q, answered); })));
        }
    }
    async function load(page = 1, options = {}) {
        checkSession(); saveDraft();
        page = Math.max(1, Number(page) || 1);
        const stores = todasAsLojasSelecionadas() ? lojasMercadoLivreConectadas() : lojasMercadoLivreConectadas().filter(s => state.lojaSelecionadaStoreId ? String(s.store_id) === state.lojaSelecionadaStoreId : s.nome === state.lojaSelecionada);
        if (!stores.length) return false;
        const viewKey = JSON.stringify([stores.map(s => [s.store_id, s.seller_id, s.site_id]), statusFiltro.value]);
        const changed = currentView !== viewKey;
        currentView = viewKey;
        controller?.abort(); detailRun?.controller.abort(); controller = new AbortController();
        const signal = controller.signal, token = ++generation;
        let pager = views.get(viewKey);
        activePager = pager || null;
        const cached = pager && Date.now() - Number(pager.lastGood || 0) <= maxStale ? pager.pages[page - 1] : null;
        if (cached && changed && pager.selectedKey) state.perguntaSelecionadaKey = pager.selectedKey;
        if (cached) display(pager, cached, page, pager.stores.length, false);
        else if (changed || (pager?.lastGood && Date.now() - pager.lastGood > maxStale)) {
            state.perguntas = []; state.totalPerguntas = 0; state.perguntaSelecionadaKey = '';
            perguntasList.innerHTML = ''; perguntasDetail.innerHTML = ''; perguntasPagination.innerHTML = '';
            perguntasStatus.textContent = 'Carregando perguntas...';
        }
        const refresh = options.forcar || (pager?.pages[page - 1] && !cached) || (cached && (changed || Date.now() - pager.updated > 30000));
        if (!pager || refresh || !pager.initialized) {
            const previous = pager;
            pager = new QuestionPager(stores, async (store, offset, status, force) => {
                const revision = revisions.get(String(store.store_id)) || 0;
                let data;
                try { data = await retryRequest('lista', { store_id: store.store_id, loja: store.nome, status, offset, limit: 20, forcar: String(force) }, signal); }
                catch (error) {
                    const old = previous?.stores.find(entry => String(entry.store.store_id) === String(store.store_id))?.chunks.get(offset);
                    if (error.name === 'AbortError' || [401, 403].includes(error.status) || !old || Date.now() - Number(old.consultado_em || 0) > maxStale) throw error;
                    data = { ...old, stale: true, partial: true };
                }
                if (revision !== (revisions.get(String(store.store_id)) || 0)) throw new DOMException('Loja atualizada', 'AbortError');
                return data;
            }, statusFiltro.value);
            // A store notification invalidates only that store; other immutable chunks are reused.
            if (previous?.invalidStores?.size && !options.forcar) pager.stores.forEach(entry => {
                const old = previous.stores.find(s => String(s.store.store_id) === String(entry.store.store_id));
                if (old && !previous.invalidStores.has(String(entry.store.store_id))) entry.chunks = new Map(old.chunks);
            });
        } else {
            // Requests for additional offsets belong to the current navigation generation.
            pager.request = async (store, offset, status, force) => {
                const revision = revisions.get(String(store.store_id)) || 0;
                const data = await retryRequest('lista', { store_id: store.store_id, loja: store.nome, status, offset, limit: 20, forcar: String(force) }, signal);
                if (revision !== (revisions.get(String(store.store_id)) || 0)) throw new DOMException('Loja atualizada', 'AbortError');
                return data;
            };
        }
        activePager = pager;
        state.carregandoPerguntas = true;
        try {
            const hadVisible = state.perguntas.length > 0;
            const questions = await pager.page(page, (partial, completed, draftPager) => {
                if (token === generation && page === 1 && !cached && !hadVisible) display(draftPager, partial, page, completed, false);
            }, Boolean(refresh));
            if (token !== generation) return false;
            if (!pager.stores.some(s => !s.error) && cached) {
                perguntasStatus.textContent = 'Dados anteriores. As lojas estão indisponíveis; tente atualizar novamente.';
                pager.pages[page - 1] = cached;
                pager.lastGood = Math.min(...pager.stores.map(entry => entry.updated).filter(Boolean), Infinity);
                if (!Number.isFinite(pager.lastGood)) pager.lastGood = 0;
                pager.updated = 0;
                return false;
            }
            const consulted = pager.stores.map(entry => entry.updated).filter(Boolean);
            if (consulted.length) pager.lastGood = Math.min(...consulted);
            boundedSet(views, viewKey, pager);
            display(pager, questions, page, pager.stores.length, Boolean(cached && !options.forcar), Boolean(options.forcar));
            if (stores.length === 1) request('resumo', { store_id: stores[0].store_id, metricas: 'true' }, signal).then(data => {
                if (token !== generation) return;
                pager.metrics = data.tempo_resposta_ml;
                renderizarResumo({ total: pager.total, retornadas: state.perguntas.length, status_resumo: pager.stores[0].summary, tempo_resposta_ml: pager.metrics });
            }).catch(() => {});
            if (pager.stores.some(s => s.stale) && !refresh) queueMicrotask(() => { if (token === generation) load(page, { background: true, forcar: true }); });
            return true;
        } catch (error) {
            if (error.name !== 'AbortError' && token === generation) perguntasStatus.textContent = `Erro ao carregar perguntas: ${mensagemErro(error)}`;
            return false;
        } finally {
            if (token === generation) state.carregandoPerguntas = false;
        }
    }
    function itemFields(item, question) {
        const variation = (item.variations || []).find(v => String(v.id) === String(question.variation_id || question.item_variation_id));
        const attrs = variation?.attributes || item.attributes || [];
        const variationId = question.variation_id || question.item_variation_id;
        const sku = variationId
            ? (variation ? variation.seller_custom_field || attrs.find(a => a.id === 'SELLER_SKU')?.value_name || '' : question.item_sku || '')
            : item.item_sku || item.seller_custom_field || attrs.find(a => a.id === 'SELLER_SKU')?.value_name || '';
        return { item_title: item.title || item.item_title, item_thumbnail: item.secure_thumbnail || item.thumbnail || item.item_thumbnail,
            item_permalink: item.permalink || item.item_permalink, item_sku: sku, _itemReady: true };
    }
    function component(data, name) {
        return data?.components?.[name] || { state: data?.stale ? 'stale' : 'unavailable', retryable: false };
    }
    function stateError(components) {
        const pending = components.filter(value => value && value.state !== 'ready');
        if (!pending.length) return null;
        return { retryable: pending.some(value => value.retryable || value.state === 'stale'),
            retryAfter: Math.max(0, ...pending.map(value => retryDelay(value.retry_after))) };
    }
    function enrichVisible(token, force = false) {
        let cachedApplied = false;
        const selected = state.perguntas.find(q => chavePerguntaAtendimento(q) === state.perguntaSelecionadaKey);
        if (selected) detail(selected, force);
        const groups = new Map();
        state.perguntas.slice(0, 20).forEach(q => {
            if (force) itemCache.delete(`${q.store_id}::${q.item_id}`);
            const cached = itemCache.get(`${q.store_id}::${q.item_id}`);
            if (!force && cached && Date.now() - cached.at < 900000) {
                cachedApplied ||= !q._itemReady; Object.assign(q, itemFields(cached.item, q), { _itemState: 'ready' });
            } else if (!q._itemRun || q._itemRun.signal?.aborted || q._itemRun.token !== token) {
                const group = groups.get(q.store_id) || []; group.push(q); groups.set(q.store_id, group);
            }
        });
        if (cachedApplied) renderSafe();
        pool([...groups.values()], async questions => {
            const store = storeFor(questions[0]); if (!store) return;
            const run = { token, signal: controller?.signal, revision: revisions.get(String(store.store_id)) || 0 };
            questions.forEach(q => Object.assign(q, { _itemRun: run, _itemPending: true, _itemReady: false, _itemState: 'loading', _itemRetryable: true }));
            if (token === generation) renderSafe();
            try {
                const pendingIds = () => [...new Set(questions.filter(q => !q._itemReady && q._itemRetryable !== false).map(q => q.item_id))].join(',');
                const values = { store_id: store.store_id, forcar: String(force), get item_ids() { return pendingIds(); } };
                await retryRequest('itens', values, run.signal, data => {
                    if (token !== generation || run.revision !== (revisions.get(String(store.store_id)) || 0)) throw new DOMException('Loja atualizada', 'AbortError');
                    const items = new Map((Array.isArray(data.items) ? data.items : Object.values(data.items || {})).map(value => {
                        const item = value.body || value; return [String(item.id || item.item_id), item];
                    }));
                    const states = [];
                    questions.filter(q => !q._itemReady && q._itemRetryable !== false).forEach(q => {
                        const item = items.get(String(q.item_id));
                        const info = data.item_states?.[q.item_id] || { state: data.stale ? 'stale' : 'unavailable', retryable: false };
                        states.push(info);
                        if (q._itemRun !== run) return;
                        q._itemState = info.state; q._itemReady = info.state === 'ready' && Boolean(item) && !data.stale;
                        q._itemRetryable = Boolean(info.retryable || info.state === 'stale');
                        if (item) Object.assign(q, itemFields(item, q), { _itemReady: q._itemReady });
                        if (q._itemReady) boundedSet(itemCache, `${q.store_id}::${q.item_id}`, { item, at: Number(data.consultado_em || Date.now()) });
                        else if (info.state === 'blocked') {
                            itemCache.delete(`${q.store_id}::${q.item_id}`);
                            for (const name of ['item_title', 'item_thumbnail', 'item_permalink', 'item_sku']) q[name] = '';
                        }
                    });
                    renderSafe();
                    return stateError(states);
                });
            } catch (error) {
                if (error.name !== 'AbortError' && token === generation) {
                    questions.filter(q => q._itemRun === run && !q._itemReady).forEach(q => {
                        q._itemState = [401, 403].includes(error.status) ? 'blocked' : 'unavailable';
                        if (q._itemState === 'blocked') {
                            itemCache.delete(`${q.store_id}::${q.item_id}`);
                            for (const name of ['item_title', 'item_thumbnail', 'item_permalink', 'item_sku']) q[name] = '';
                        }
                    });
                    perguntasStatus.textContent = 'Lista disponível. Não foi possível completar alguns anúncios; tente novamente.';
                }
            } finally {
                questions.filter(q => q._itemRun === run).forEach(q => { q._itemPending = false; q._itemRun = null; });
                if (token === generation) renderSafe();
            }
        }).catch(() => {});
    }
    async function detail(question, force = false) {
        const store = storeFor(question); if (!store) return;
        const key = keyOf(question);
        if (force) detailCache.delete(key);
        const cache = detailCache.get(key);
        // Cancel the previous selection before consulting cache or pending flags (A -> B -> A).
        if (detailRun && (detailRun.key !== key || detailRun.token !== generation || force)) detailRun.controller.abort();
        if (detailRun?.key === key && !detailRun.controller.signal.aborted && detailRun.token === generation) return;
        if (!force && cache && Date.now() - cache.at < 60000) {
            const ready = question._detailReady;
            Object.assign(question, cache.data);
            if (!ready) {
                const cachedGeneration = generation;
                queueMicrotask(() => {
                    if (cachedGeneration === generation && chavePerguntaAtendimento(question) === state.perguntaSelecionadaKey) renderSafe();
                });
            }
            return;
        }
        if (!force && question._detailSettled === generation) return;
        const run = { key, controller: new AbortController(), token: generation, revision: revisions.get(String(store.store_id)) || 0 };
        detailRun = run;
        const token = generation;
        question._detailRun = run;
        question._generationError = '';
        Object.assign(question, { _detailPending: true, _detailReady: false, _historyState: 'loading', _questionState: 'loading' });
        // Rendering calls detalhe itself. Defer so the outer render can restore its draft first.
        queueMicrotask(() => { if (detailRun === run && token === generation) renderSafe(); });
        try {
            let received = false;
            const values = { store_id: store.store_id, question_id: question.id, forcar: String(force),
                get componentes() { return received ? question._questionState === 'ready' ? 'history' : 'question,history' : ''; } };
            await retryRequest('detalhe', values, run.controller.signal, data => {
                if (token !== generation || detailRun !== run || run.revision !== (revisions.get(String(store.store_id)) || 0)) throw new DOMException('Consulta substituída', 'AbortError');
                const fields = { ...(data.question || {}) };
                Object.keys(fields).filter(name => name.startsWith('item_') && !fields[name]).forEach(name => delete fields[name]);
                const main = component(data, 'question');
                const history = component(data, 'history');
                received = true;
                Object.assign(question, fields, {
                    _detailReady: main.state === 'ready' && history.state === 'ready' && !data.stale,
                    _questionState: main.state, _historyState: history.state,
                    _historyTruncated: Boolean(data.history_truncated || history.truncated || fields.buyer_question_history_truncated),
                    _detailPartial: Boolean(data.partial), loja: store.nome, store_id: store.store_id
                });
                if (history.state === 'blocked') {
                    question.buyer_question_chat = [];
                    question.buyer_question_history_count = 0;
                }
                if (question._detailReady) boundedSet(detailCache, key, { data: { ...fields, _detailReady: true, _questionState: 'ready', _historyState: 'ready', _historyTruncated: question._historyTruncated, _detailPartial: question._detailPartial }, at: Number(data.consultado_em || Date.now()) });
                else detailCache.delete(key);
                if (chavePerguntaAtendimento(question) === state.perguntaSelecionadaKey) renderSafe();
                return main.state === 'blocked' ? { retryable: false } : stateError([main, history]);
            });
        } catch (error) {
            if (error.name === 'AbortError') run.controller.abort();
            if (error.name !== 'AbortError' && token === generation && detailRun === run) {
                question._historyState = [401, 403].includes(error.status) ? 'blocked' : 'unavailable';
                question._questionState = question._historyState;
                if (question._historyState === 'blocked') {
                    for (const name of ['text', 'answer', 'from_id', 'buyer_id', 'buyer_name', 'buyer_nickname']) delete question[name];
                    question.buyer_question_chat = [];
                    question.buyer_question_history_count = 0;
                    detailCache.delete(key);
                }
                perguntasStatus.textContent = 'Histórico indisponível. Tente novamente para liberar a IA.';
            }
        } finally {
            if (!run.controller.signal.aborted && token === generation && question._detailRun === run) question._detailSettled = token;
            if (question._detailRun === run) { question._detailPending = false; question._detailRun = null; }
            if (detailRun === run) detailRun = null;
            if (token === generation && chavePerguntaAtendimento(question) === state.perguntaSelecionadaKey) renderSafe();
        }
    }
    function sessionIdentity(headers) {
        // Local cache partition only. Authorization always belongs to the server.
        try {
            const parsed = JSON.parse(headers);
            const name = Object.keys(parsed).find(key => key.toLowerCase() === 'authorization');
            const token = String(parsed[name] || '').replace(/^Bearer\s+/i, '');
            const claims = typeof _jwtPayloadLocal === 'function' ? _jwtPayloadLocal(token)
                : JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
            if (!claims?.sub || !claims?.client_id) return headers;
            const identity = Object.fromEntries(Object.keys(claims).sort().filter(key => !['exp', 'iat', 'nbf'].includes(key)).map(key => [key, claims[key]]));
            parsed[name] = identity;
            return JSON.stringify(parsed);
        } catch (_) { return headers; }
    }
    function retrySelected() {
        const question = state.perguntas.find(q => chavePerguntaAtendimento(q) === state.perguntaSelecionadaKey);
        if (question && !question._detailReady) detail(question, true);
        if (question && !question._itemReady) enrichVisible(generation);
    }
    function rejectContext(question, name, blocked = false) {
        if (!question) return;
        const status = blocked ? 'blocked' : 'unavailable';
        if (name === 'item' || name === 'all') {
            itemCache.delete(`${question.store_id}::${question.item_id}`);
            Object.assign(question, { _itemReady: false, _itemState: status });
            if (blocked) for (const field of ['item_title', 'item_thumbnail', 'item_permalink', 'item_sku']) question[field] = '';
        }
        if (name !== 'item') {
            detailCache.delete(keyOf(question));
            Object.assign(question, { _detailReady: false, _detailSettled: generation, _historyState: status });
            if (name === 'question') question._questionState = status;
            if (blocked) {
                question.buyer_question_chat = []; question.buyer_question_history_count = 0;
                if (name === 'question') for (const field of ['text', 'answer', 'from_id', 'buyer_id', 'buyer_name', 'buyer_nickname']) delete question[field];
            }
        }
        if (chavePerguntaAtendimento(question) === state.perguntaSelecionadaKey) renderSafe();
    }
    root.JKPerguntasLoading = { carregar: load, invalidar: invalidate, detalhe: detail, tentarNovamente: retrySelected, rejeitarContexto: rejectContext, tratarNegacaoGeracao: generationDenial, limpar: clear, salvarRascunho: saveDraft, restaurarRascunho: () => restaurarInteracaoPerguntas(drafts.get(state.perguntaSelecionadaKey)), request, verificarSessao: checkSession, QuestionPager };
    root.addEventListener('pagehide', clear);
    root.addEventListener('storage', checkSession);
    root.addEventListener('jk:logout', clear);
    root.addEventListener('focus', checkSession);
})(typeof window !== 'undefined' ? window : null);
