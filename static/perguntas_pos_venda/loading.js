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
                if (error.name === 'AbortError' || error.status === 401 || error.status === 403) throw error;
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
                if (progress) progress(this.stores.flatMap(s => s.buffer).sort(compareQuestions).slice(0, 20), completed);
            });
            this.initialized = true;
            this.updated = Date.now();
        }
        async page(number, progress, force = false) {
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
    let generation = 0, controller = null, detailController = null, detailSequence = 0, session = '', storesFingerprint = '', currentView = '', activePager = null;
    const maxStale = 600000;
    function boundedSet(map, key, value) {
        map.delete(key); map.set(key, value);
        if (map.size > 500) map.delete(map.keys().next().value);
    }
    function clear(resetStores = true) {
        generation++; controller?.abort(); detailController?.abort();
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
        if (session && session !== next) clear();
        session = next;
        const fingerprint = JSON.stringify(lojasMercadoLivreConectadas().map(store => [store.store_id, store.seller_id, store.site_id]));
        if (storesFingerprint && storesFingerprint !== fingerprint) clear(false);
        storesFingerprint = fingerprint;
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
            if (error.status === 401 || error.status === 403) clear();
            throw error;
        }
        return data;
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
    function display(pager, questions, page, completed, background) {
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
        enrichVisible(generation);
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
        controller?.abort(); detailController?.abort(); controller = new AbortController();
        const signal = controller.signal, token = ++generation;
        let pager = views.get(viewKey);
        activePager = pager || null;
        const cached = pager && Date.now() - Number(pager.lastGood || 0) <= maxStale ? pager.pages[page - 1] : null;
        if (cached && changed && pager.selectedKey) state.perguntaSelecionadaKey = pager.selectedKey;
        if (cached) display(pager, cached, page, pager.stores.length, false);
        else if (changed || !options.background) {
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
                try { data = await request('lista', { store_id: store.store_id, loja: store.nome, status, offset, limit: 20, forcar: String(force) }, signal); }
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
            boundedSet(views, viewKey, pager);
        } else {
            // Requests for additional offsets belong to the current navigation generation.
            pager.request = async (store, offset, status, force) => {
                const revision = revisions.get(String(store.store_id)) || 0;
                const data = await request('lista', { store_id: store.store_id, loja: store.nome, status, offset, limit: 20, forcar: String(force) }, signal);
                if (revision !== (revisions.get(String(store.store_id)) || 0)) throw new DOMException('Loja atualizada', 'AbortError');
                return data;
            };
        }
        activePager = pager;
        state.carregandoPerguntas = true;
        try {
            const questions = await pager.page(page, (partial, completed) => {
                if (token === generation && page === 1 && !cached) display(pager, partial, page, completed, false);
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
            display(pager, questions, page, pager.stores.length, Boolean(cached && !options.forcar));
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
    function enrichVisible(token) {
        let cachedApplied = false;
        const selected = state.perguntas.find(q => chavePerguntaAtendimento(q) === state.perguntaSelecionadaKey);
        if (selected) detail(selected);
        const groups = new Map();
        state.perguntas.slice(0, 20).forEach(q => {
            const cached = itemCache.get(`${q.store_id}::${q.item_id}`);
            if (cached && Date.now() - cached.at < 900000) { cachedApplied ||= !q._itemReady; Object.assign(q, itemFields(cached.item, q)); }
            else if (!q._itemPending && !q._itemReady) {
                const group = groups.get(q.store_id) || []; group.push(q); groups.set(q.store_id, group); q._itemPending = true;
            }
        });
        if (cachedApplied) renderSafe();
        pool([...groups.values()], async questions => {
            const store = storeFor(questions[0]); if (!store) return;
            try {
                const data = await request('itens', { store_id: store.store_id, item_ids: [...new Set(questions.map(q => q.item_id))].join(',') }, controller?.signal);
                if (token !== generation) return;
                const items = Array.isArray(data.items) ? data.items : Object.values(data.items || {});
                items.forEach(value => { const item = value.body || value; boundedSet(itemCache, `${store.store_id}::${item.id}`, { item, at: Number(data.consultado_em || Date.now()) }); });
                questions.forEach(q => { const hit = itemCache.get(`${q.store_id}::${q.item_id}`); if (hit) Object.assign(q, itemFields(hit.item, q)); });
                renderSafe();
            } catch (error) {
                if (error.name !== 'AbortError' && token === generation) perguntasStatus.textContent = 'Lista disponível. Não foi possível completar alguns anúncios; use Atualizar para tentar novamente.';
            } finally { questions.forEach(q => { q._itemPending = false; }); }
        }).catch(() => {});
    }
    async function detail(question, force = false) {
        const store = storeFor(question); if (!store) return;
        const key = keyOf(question), cache = detailCache.get(key);
        if (!force && cache && Date.now() - cache.at < 60000) {
            const ready = question._detailReady;
            Object.assign(question, cache.data, { _detailReady: true });
            if (!ready) {
                const cachedGeneration = generation;
                queueMicrotask(() => {
                    if (cachedGeneration === generation && chavePerguntaAtendimento(question) === state.perguntaSelecionadaKey) renderSafe();
                });
            }
            return;
        }
        if (question._detailPending) return;
        detailController?.abort(); detailController = new AbortController();
        const token = generation, sequence = ++detailSequence, revision = revisions.get(String(store.store_id)) || 0;
        question._detailPending = true;
        try {
            const data = await request('detalhe', { store_id: store.store_id, question_id: question.id, forcar: String(force) }, detailController.signal);
            if (token !== generation || sequence !== detailSequence || revision !== (revisions.get(String(store.store_id)) || 0)) return;
            const fields = { ...(data.question || {}) };
            Object.keys(fields).filter(name => name.startsWith('item_') && !fields[name]).forEach(name => delete fields[name]);
            Object.assign(question, fields, { _detailReady: !data.partial && !data.stale, loja: store.nome, store_id: store.store_id });
            if (!data.partial && !data.stale) boundedSet(detailCache, key, { data: fields, at: Number(data.consultado_em || Date.now()) });
            if (data.stale && !force) {
                question._detailPending = false;
                return detail(question, true);
            }
            if (chavePerguntaAtendimento(question) === state.perguntaSelecionadaKey) renderSafe();
        } catch (error) {
            if (error.name !== 'AbortError' && token === generation) perguntasStatus.textContent = 'Histórico indisponível. Atualize para tentar novamente.';
        } finally { question._detailPending = false; }
    }
    root.JKPerguntasLoading = { carregar: load, invalidar: invalidate, detalhe: detail, limpar: clear, salvarRascunho: saveDraft, restaurarRascunho: () => restaurarInteracaoPerguntas(drafts.get(state.perguntaSelecionadaKey)), request, verificarSessao: checkSession, QuestionPager };
    root.addEventListener('pagehide', clear);
    root.addEventListener('storage', checkSession);
    root.addEventListener('jk:logout', clear);
    root.addEventListener('focus', checkSession);
})(typeof window !== 'undefined' ? window : null);
