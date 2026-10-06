(function (root, factory) {
    'use strict';
    if (typeof module === 'object' && module.exports) module.exports = factory;
    else factory(root);
})(typeof window !== 'undefined' ? window : null, function createPesquisaMercadoUI(window) {
    'use strict';
    const document = window.document;
    const el = name => document.getElementById(`pm-global-${name}`);
    if (!el('start')) return null;

    const pageSize = 100;
    const categoryCommandTimeout = 3 * (35000 + 15000) + 30000;
    const emptyTaxonomy = () => ({ trail: [], parent: null, selected: null, items: [], loaded: false,
        loading: false, authRequired: false, error: false, message: 'Escolha uma categoria ou subcategoria para iniciar a pesquisa.' });
    const state = {
        job: null, scopeCategory: null, mode: 'manual', taxonomy: emptyTaxonomy(), tab: 'geral', query: '', category: '', seller: '', sellers: [],
        offsets: { geral: 0, vendedor: 0 }, rankings: { geral: null, vendedor: null },
        disposed: false, busy: false, generation: 0, commandSerial: 0
    };
    const pending = new Map();
    let unsubscribe = null;
    let polling = null;
    let debounce = null;
    let refreshTask = null;
    let refreshRequested = false;
    let synchronizeTask = null;
    let sessionToken = window.localStorage.getItem('access_token') || '';
    const elements = {};
    for (const name of ['start', 'pause', 'resume', 'cancel', 'browser', 'status', 'status-panel', 'status-detail', 'status-warning', 'category-current',
        'categories-count', 'pages-count', 'sellers-count', 'ads-count', 'pending-count', 'search',
        'category', 'tab-geral', 'tab-vendedor', 'sellers', 'seller-search', 'seller-list', 'seller-label',
        'body', 'empty', 'count', 'previous', 'next', 'page', 'scope', 'taxonomy-refresh', 'taxonomy-breadcrumb',
        'taxonomy-list', 'taxonomy-message', 'taxonomy-browser', 'taxonomy-selection']) elements[name] = el(name);

    const safeText = value => String(value == null ? '' : value);
    const escaped = value => safeText(value).replace(/[&<>"']/g, character => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[character]);
    const number = value => value !== null && value !== '' && Number.isFinite(Number(value))
        ? Number(value).toLocaleString('pt-BR', { maximumFractionDigits: 2 }) : '—';
    function safeUrl(value, image) {
        try {
            const url = new URL(safeText(value));
            if (url.protocol !== 'https:') return '';
            const allowed = image ? /(^|\.)(mlstatic\.com|mercadolivre\.com\.br)$/i : /(^|\.)mercadolivre\.com\.br$/i;
            return allowed.test(url.hostname) ? url.href : '';
        } catch (_error) { return ''; }
    }
    function formatDate(value) {
        const raw = safeText(value).trim();
        const iso = raw.match(/^(\d{4})-(\d{2})-(\d{2})/);
        return iso ? `${iso[3]}/${iso[2]}/${iso[1]}` : raw || '—';
    }
    function formatPrice(value) {
        return typeof value === 'number' && Number.isFinite(value)
            ? value.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' }) : safeText(value) || '—';
    }
    function userErrorMessage(error) {
        const message = typeof error === 'string' ? error : error && typeof error.message === 'string'
            ? error.message : 'Não foi possível concluir a operação.';
        return message.replace(/^Error invoking remote method ['"]pesquisa-mercado:(?:categories|start|status|resume|pause|cancel|show-browser)['"]:\s*(?:Error:\s*)?/, '');
    }
    function categoryChoice(value) {
        if (!value || typeof value !== 'object') return null;
        const key = safeText(value.key).trim();
        const nome = safeText(value.nome).trim();
        const url = safeUrl(value.url, false);
        return key && nome && url ? { key, nome, url } : null;
    }
    function electronApi() {
        try { if (window.electronAPI) return window.electronAPI; } catch (_error) {}
        try { if (window.top !== window && window.top.electronAPI) return window.top.electronAPI; } catch (_error) {}
        return null;
    }
    function currentSession() {
        const current = window.localStorage.getItem('access_token') || '';
        if (current !== sessionToken) {
            sessionToken = current;
            state.job = null;
            state.scopeCategory = null;
            state.taxonomy = emptyTaxonomy();
            state.seller = '';
            state.sellers = [];
            state.query = '';
            state.category = '';
            state.offsets = { geral: 0, vendedor: 0 };
            state.rankings = { geral: null, vendedor: null };
            state.generation += 1;
            elements.search.value = '';
            elements.category.innerHTML = '<option value="">Todas as categorias</option>';
            for (const name of ['categories', 'pages', 'sellers', 'ads', 'pending']) elements[`${name}-count`].textContent = '0';
            elements['category-current'].textContent = 'Aguardando categoria';
            renderScope();
            renderTaxonomy();
            renderSellers();
            renderRanking();
            renderControls();
            showMessage(current ? 'Recuperando a pesquisa desta sessão.' : 'Entre no JK Sistema para consultar a pesquisa.', !current);
        }
        return current;
    }
    function shellSend(action, payload) {
        if (!window.top || window.top === window) throw new Error('Abra a Pesquisa de Mercado no aplicativo JK Sistema.');
        const requestId = `pm-${Date.now()}-${++state.commandSerial}`;
        return new Promise((resolve, reject) => {
            const timer = window.setTimeout(() => {
                pending.delete(requestId);
                reject(new Error('O aplicativo não respondeu ao comando da pesquisa.'));
            }, action === 'categories' ? categoryCommandTimeout : 45000);
            pending.set(requestId, { resolve, reject, timer });
            window.top.postMessage({ channel: 'jk-pesquisa-mercado-command', requestId, action, payload }, '*');
        });
    }
    const methods = {
        start: 'startPesquisaMercadoJob', status: 'getPesquisaMercadoJobStatus', pause: 'pausePesquisaMercadoJob',
        resume: 'resumePesquisaMercadoJob', cancel: 'cancelPesquisaMercadoJob', browser: 'showPesquisaMercadoBrowser',
        categories: 'getPesquisaMercadoCategories'
    };
    async function invoke(action, extras = {}) {
        const authToken = currentSession();
        if (!authToken) throw new Error('Entre no JK Sistema para consultar a pesquisa.');
        const payload = { authToken };
        if (state.job && ['pause', 'resume', 'cancel', 'browser'].includes(action) && extras.purpose !== 'categories') payload.jobId = state.job.id;
        if (action === 'start') payload.category = categoryChoice(extras.category);
        if (action === 'categories' && extras.parentCategory) payload.parentCategory = categoryChoice(extras.parentCategory);
        if (action === 'browser' && extras.purpose === 'categories') payload.purpose = 'categories';
        const api = electronApi();
        const result = api && typeof api[methods[action]] === 'function'
            ? await api[methods[action]](payload) : await shellSend(action, payload);
        if (currentSession() !== authToken) throw new Error('A sessão mudou. Recuperando a pesquisa atual.');
        if (result && result.success === false) throw new Error(result.error || result.message || 'Não foi possível executar o comando.');
        return result;
    }
    async function request(path) {
        const authToken = currentSession();
        if (!authToken) throw new Error('Entre no JK Sistema para consultar a pesquisa.');
        const response = await window.fetch(`/api/pesquisa-mercado${path}`, {
            headers: { Authorization: `Bearer ${authToken}` }, cache: 'no-store'
        });
        if (!response.ok) {
            if (response.status === 401 || response.status === 403) throw new Error('A sessão do JK Sistema precisa ser renovada.');
            throw new Error('Não foi possível consultar os resultados da pesquisa.');
        }
        const data = await response.json();
        if (currentSession() !== authToken) throw new Error('A sessão mudou. Recuperando a pesquisa atual.');
        return data;
    }
    function showMessage(message, error) {
        if (state.job) {
            elements['status-warning'].textContent = message;
            elements['status-warning'].hidden = !message;
            elements['status-warning'].classList.toggle('error', !!error);
            return;
        }
        elements.status.classList.remove("success");
        elements.status.textContent = message;
        elements.status.classList.toggle('error', !!error);
        elements['status-panel'].dataset.state = 'idle';
        elements['status-detail'].textContent = '';
        elements['status-warning'].textContent = '';
        elements['status-warning'].hidden = true;
        elements['status-warning'].classList.remove('error');
    }
    function renderStatus(job) {
        const labels = { queued: 'Preparando a pesquisa', running: 'O sistema está pesquisando', pausing: 'Pausando a pesquisa',
            paused: 'Pesquisa pausada', waiting_login: 'Aguardando login', interrupted: 'Pesquisa interrompida',
            completed: 'Pesquisa concluída', completed_partial: 'Pesquisa concluída com pendências',
            cancelled: 'Pesquisa cancelada', failed: 'Pesquisa interrompida por uma falha' };
        const details = { queued: 'Preparando o navegador e a categoria selecionada.',
            running: 'A pesquisa continua em segundo plano. Os resultados serão atualizados durante a coleta.',
            pausing: 'Salvando o progresso para pausar a pesquisa.', paused: 'O progresso foi salvo. Clique em Retomar para continuar.',
            waiting_login: 'Conclua o login solicitado no navegador e clique em Retomar.', interrupted: 'O progresso foi preservado. Clique em Retomar para continuar.',
            completed: 'A pesquisa terminou. Os resultados estão disponíveis nos rankings abaixo.',
            completed_partial: 'A pesquisa terminou. Consulte as pendências e as categorias incompletas nos resultados.',
            cancelled: 'Os resultados já consultados permanecem disponíveis.', failed: 'O progresso foi preservado. Clique em Retomar para tentar continuar.' };
        const checkpoint = job.checkpoint || {};
        const progress = job.progress || checkpoint.progress || {};
        const active = ['running', 'queued', 'pausing'].includes(job.status);
        const message = safeText(job.message);
        const legacyWarning = active && /^Alguns an[uú]ncios ficaram sem dados suficientes do Avant/i.test(message);
        let warning = safeText(progress.warning || (legacyWarning ? message : ''));
        if (!warning && Number((job.counters || {}).pending) > 0) warning = 'Existem pendências de dados ou falhas de consulta.';
        if (warning && job.status === 'running') warning += ' A pesquisa continua.';
        const detail = active ? safeText(progress.detail || (legacyWarning ? '' : message))
            : progress.phase === job.status ? safeText(progress.detail) : '';
        elements['status-panel'].dataset.state = safeText(job.status || 'idle');
        elements.status.textContent = labels[job.status] || 'Acompanhando a pesquisa';
        elements.status.classList.toggle('error', job.status === 'failed');
        elements.status.classList.toggle('success', job.status === 'completed');
        elements['status-detail'].textContent = detail || details[job.status] || 'Acompanhando o progresso salvo.';
        elements['status-warning'].textContent = warning;
        elements['status-warning'].hidden = !warning;
        elements['status-warning'].classList.remove('error');
    }
    function isActive(job) {
        return !!job && ['queued', 'running', 'pausing', 'paused', 'waiting_login'].includes(job.status);
    }
    function renderControls() {
        const status = state.job ? state.job.status : '';
        const running = ['queued', 'running', 'pausing'].includes(status);
        const resumable = ['paused', 'waiting_login', 'interrupted', 'failed'].includes(status);
        elements.start.disabled = !sessionToken || state.busy || isActive(state.job) || state.taxonomy.loading || !state.taxonomy.selected || state.taxonomy.authRequired || state.taxonomy.error;
        elements.pause.disabled = state.busy || !running;
        elements.resume.disabled = state.busy || !resumable;
        elements.cancel.disabled = state.busy || !(isActive(state.job) || resumable);
        elements.browser.disabled = state.busy || !(isActive(state.job) || resumable);
        const taxonomyDisabled = !sessionToken || state.busy || state.taxonomy.loading || isActive(state.job);
        elements['taxonomy-refresh'].disabled = taxonomyDisabled;
        elements['taxonomy-browser'].disabled = !sessionToken || state.busy || isActive(state.job);
        for (const button of elements['taxonomy-list'].querySelectorAll('button')) button.disabled = taxonomyDisabled;
        for (const button of elements['taxonomy-breadcrumb'].querySelectorAll('button')) button.disabled = taxonomyDisabled;
    }
    function renderScope() {
        elements.scope.textContent = state.scopeCategory ? `Escopo da pesquisa: ${state.scopeCategory.nome}`
            : state.job ? 'Escopo da pesquisa: execução anterior.' : 'Nenhuma pesquisa iniciada.';
    }
    function renderTaxonomy() {
        const taxonomy = state.taxonomy;
        elements['taxonomy-list'].innerHTML = '';
        elements['taxonomy-breadcrumb'].innerHTML = '';
        const breadcrumb = (name, category, trail, current) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.textContent = name;
            if (current) button.setAttribute('aria-current', 'page');
            button.addEventListener('click', () => loadTaxonomy(category, trail));
            elements['taxonomy-breadcrumb'].appendChild(button);
        };
        breadcrumb('Todas as categorias', null, [], !taxonomy.parent);
        taxonomy.trail.forEach((category, index) => {
            const separator = document.createElement('span');
            separator.textContent = '›';
            separator.setAttribute('aria-hidden', 'true');
            elements['taxonomy-breadcrumb'].appendChild(separator);
            breadcrumb(category.nome, category, taxonomy.trail.slice(0, index + 1), index === taxonomy.trail.length - 1);
        });
        for (const category of taxonomy.items) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'pm-taxonomy-item';
            button.setAttribute('aria-label', `Selecionar ${category.nome} e ver subcategorias`);
            const name = document.createElement('span');
            name.textContent = category.nome;
            const arrow = document.createElement('span');
            arrow.textContent = '›';
            arrow.setAttribute('aria-hidden', 'true');
            button.appendChild(name);
            button.appendChild(arrow);
            button.addEventListener('click', () => loadTaxonomy(category, taxonomy.trail.concat(category)));
            elements['taxonomy-list'].appendChild(button);
        }
        const message = isActive(state.job) ? 'A seleção de uma nova categoria fica disponível ao concluir ou cancelar esta pesquisa.'
            : taxonomy.loading ? 'Carregando categorias do Mercado Livre…' : taxonomy.message;
        elements['taxonomy-message'].textContent = message;
        elements['taxonomy-message'].classList.toggle('error', taxonomy.error);
        elements['taxonomy-list'].setAttribute('aria-busy', taxonomy.loading ? 'true' : 'false');
        elements['taxonomy-browser'].hidden = !taxonomy.authRequired;
        elements['taxonomy-refresh'].textContent = taxonomy.loaded ? 'Atualizar lista' : 'Carregar categorias';
        elements['taxonomy-selection'].textContent = taxonomy.selected
            ? `Selecionada: ${taxonomy.trail.map(category => category.nome).join(' › ')}. A pesquisa inclui suas subcategorias.`
            : 'Nenhuma categoria selecionada.';
        renderControls();
    }
    async function loadTaxonomy(parentCategory = null, trail = []) {
        if (synchronizeTask) await synchronizeTask;
        currentSession();
        const taxonomy = state.taxonomy;
        if (state.disposed || state.mode !== 'global' || !sessionToken || isActive(state.job) || state.busy || taxonomy.loading) return;
        taxonomy.loading = true;
        taxonomy.error = false;
        taxonomy.authRequired = false;
        renderTaxonomy();
        try {
            const result = await invoke('categories', { parentCategory });
            if (state.disposed || state.taxonomy !== taxonomy) return;
            if (result && result.authRequired) {
                taxonomy.authRequired = true;
                taxonomy.error = true;
                taxonomy.message = result.reason || 'Conclua o login no navegador e depois recarregue as categorias.';
                return;
            }
            if (!result || !Array.isArray(result.items)) throw new Error('O aplicativo não retornou a lista de categorias.');
            const selected = categoryChoice(result.parentCategory);
            if (parentCategory && (!selected || selected.key !== parentCategory.key)) throw new Error('A categoria retornada não corresponde à seleção. Recarregue a lista.');
            taxonomy.parent = selected;
            taxonomy.selected = selected;
            taxonomy.trail = selected ? trail.slice(0, -1).concat(selected) : [];
            taxonomy.items = result.items.map(categoryChoice).filter(Boolean);
            taxonomy.loaded = true;
            taxonomy.message = selected ? taxonomy.items.length
                ? 'Pesquise na categoria selecionada ou escolha uma das subcategorias abaixo.'
                : 'Esta subcategoria não possui outras subcategorias. Clique em pesquisar para iniciar.'
                : taxonomy.items.length ? 'Escolha uma categoria ou subcategoria para iniciar a pesquisa.'
                    : 'Nenhuma categoria foi encontrada. Atualize a lista para tentar novamente.';
        } catch (error) {
            if (state.taxonomy === taxonomy && !state.disposed) {
                taxonomy.error = true;
                taxonomy.message = userErrorMessage(error);
            }
        } finally {
            if (state.taxonomy === taxonomy && !state.disposed) {
                taxonomy.loading = false;
                renderTaxonomy();
            }
        }
    }
    function changeMode(mode) {
        state.mode = mode;
        for (const value of ['manual', 'global']) {
            const active = value === mode;
            const button = document.getElementById(`pm-mode-${value}`);
            button.setAttribute('aria-selected', active ? 'true' : 'false');
            button.tabIndex = active ? 0 : -1;
            document.getElementById(`pm-panel-${value}`).hidden = !active;
        }
        window.dispatchEvent(new window.CustomEvent('jk-pesquisa-mercado-mode-change', { detail: { mode } }));
        if (mode === 'global' && !state.taxonomy.loaded && !state.taxonomy.loading) loadTaxonomy();
    }
    function updateJob(raw) {
        const job = raw && Object.prototype.hasOwnProperty.call(raw, 'job') ? raw.job : raw;
        if (!job || !job.id) return false;
        if (state.job && state.job.id === job.id && Number(job.revision) < Number(state.job.revision)) return false;
        const changed = !state.job || state.job.id !== job.id || state.job.revision !== job.revision;
        if (!state.job || state.job.id !== job.id) {
            state.offsets = { geral: 0, vendedor: 0 };
            state.rankings = { geral: null, vendedor: null };
            state.seller = '';
            state.generation += 1;
            state.scopeCategory = null;
        }
        state.job = job;
        const scopeCategory = categoryChoice(job.scopeCategory || job.checkpoint && job.checkpoint.scope && job.checkpoint.scope.category);
        if (scopeCategory) state.scopeCategory = scopeCategory;
        renderScope();
        renderTaxonomy();
        const counters = job.counters || {};
        for (const name of ['categories', 'pages', 'sellers', 'ads', 'pending']) elements[`${name}-count`].textContent = number(counters[name] || 0);
        const checkpoint = job.checkpoint || {};
        const currentCategory = job.currentCategory || checkpoint.currentCategory || checkpoint.category || checkpoint.category_name;
        const categoryName = typeof currentCategory === 'object' && currentCategory
            ? safeText(currentCategory.nome || currentCategory.name || currentCategory.categoria) : safeText(currentCategory);
        elements['category-current'].textContent = categoryName ? `Categoria atual: ${categoryName}` : 'Aguardando categoria';
        renderStatus(job);
        renderControls();
        return changed;
    }
    function renderSellers() {
        const query = elements['seller-search'].value.trim().toLocaleLowerCase('pt-BR');
        const sellers = state.sellers.filter(seller => safeText(seller.vendedor || seller.seller_key).toLocaleLowerCase('pt-BR').includes(query));
        elements['seller-list'].innerHTML = '';
        for (const seller of sellers) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = `pm-global-seller${seller.seller_key === state.seller ? ' selected' : ''}`;
            button.setAttribute('aria-pressed', seller.seller_key === state.seller ? 'true' : 'false');
            button.textContent = `${seller.vendedor || seller.seller_key}${seller.ads != null ? ` · ${number(seller.ads)} anúncios` : ''}`;
            button.addEventListener('click', () => {
                state.seller = seller.seller_key;
                state.offsets.vendedor = 0;
                state.rankings.vendedor = null;
                state.generation += 1;
                renderSellers();
                renderRanking();
                refresh();
            });
            elements['seller-list'].appendChild(button);
        }
        if (!sellers.length) {
            const note = document.createElement('p');
            note.className = 'pm-global-note';
            note.textContent = state.sellers.length ? 'Nenhum vendedor corresponde à busca.' : 'Os vendedores aparecerão após a primeira coleta.';
            elements['seller-list'].appendChild(note);
        }
        const selected = state.sellers.find(seller => seller.seller_key === state.seller);
        elements['seller-label'].textContent = selected ? selected.vendedor || selected.seller_key : 'Selecione um vendedor para ver seu ranking.';
    }
    function renderRanking() {
        const data = state.rankings[state.tab];
        const rows = data && Array.isArray(data.items) ? data.items : [];
        elements.body.innerHTML = rows.map(item => {
            const ranked = Number.isInteger(item.rank) && item.rank > 0;
            const image = safeUrl(item.imagem, true);
            const url = safeUrl(item.url, false);
            return `<tr><td class="rank-cell">${ranked ? item.rank : ''}</td>` +
                `<td>${image ? `<img class="pm-global-photo" src="${escaped(image)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : '—'}</td>` +
                `<td class="title-cell">${escaped(item.titulo || 'Anúncio sem título')}<small>${escaped(item.id)}</small></td>` +
                `<td>${escaped(item.vendedor || '—')}</td><td>${escaped(formatPrice(item.preco))}</td>` +
                `<td class="sales-cell">${number(item.vendas)}</td><td>${escaped(formatDate(item.data_criacao))}</td>` +
                `<td>${number(item.media_vendas_mensal)}</td><td>${url ? `<a class="link" href="${escaped(url)}" target="_blank" rel="noopener noreferrer">Abrir</a>` : '—'}</td>` +
                `<td><span class="status-chip${ranked ? '' : ' warning'}">${ranked ? 'Analisado' : escaped(item.erro || item.status || 'Aguardando dados do Avant')}</span></td></tr>`;
        }).join('');
        const total = data ? Number(data.total) || 0 : 0;
        const offset = state.offsets[state.tab];
        elements.empty.hidden = rows.length > 0;
        elements.empty.textContent = state.tab === 'vendedor' && !state.seller
            ? 'Selecione um vendedor para ver seu ranking.' : state.job
                ? 'Nenhum anúncio disponível para estes filtros.' : 'Inicie uma pesquisa para consultar os rankings.';
        elements.count.textContent = `${number(total)} anúncio(s)`;
        elements.page.textContent = `Página ${Math.floor(offset / pageSize) + 1} de ${Math.max(1, Math.ceil(total / pageSize))}`;
        elements.previous.disabled = offset === 0;
        elements.next.disabled = offset + pageSize >= total;
    }
    async function loadRankings() {
        if (!state.job || state.disposed) return;
        const jobId = state.job.id;
        const generation = state.generation;
        const base = `/jobs/${encodeURIComponent(jobId)}`;
        const [sellers, categories] = await Promise.all([request(`${base}/vendedores`), request(`${base}/categorias`)]);
        if (state.disposed || generation !== state.generation || jobId !== state.job.id) { refreshRequested = true; return; }
        state.sellers = Array.isArray(sellers.items) ? sellers.items : [];
        if (state.seller && !state.sellers.some(seller => seller.seller_key === state.seller)) state.seller = '';
        if (!state.seller && state.sellers.length) state.seller = state.sellers[0].seller_key;
        const selectedCategory = state.category;
        elements.category.innerHTML = '<option value="">Todas as categorias</option>';
        for (const category of Array.isArray(categories.items) ? categories.items : []) {
            const name = typeof category === 'string' ? category : category.categoria || category.name;
            if (!name) continue;
            const option = document.createElement('option');
            option.value = name;
            option.textContent = `${name}${category.status === 'incomplete' ? ' (incompleta)' : ''}`;
            elements.category.appendChild(option);
        }
        elements.category.value = selectedCategory;
        renderSellers();
        const tabs = ['geral', 'vendedor'];
        const results = await Promise.all(tabs.map(async tab => {
            if (tab === 'vendedor' && !state.seller) return { items: [], total: 0 };
            const params = new URLSearchParams({ offset: safeText(state.offsets[tab]), limit: safeText(pageSize), q: state.query, category: state.category });
            if (tab === 'vendedor') params.set('seller_key', state.seller);
            return request(`${base}/ranking?${params}`);
        }));
        if (state.disposed || generation !== state.generation || jobId !== state.job.id) { refreshRequested = true; return; }
        tabs.forEach((tab, index) => { state.rankings[tab] = results[index]; });
        renderRanking();
    }
    function refresh() {
        if (state.disposed) return Promise.resolve();
        refreshRequested = true;
        if (refreshTask) return refreshTask;
        refreshTask = (async () => {
            while (refreshRequested && !state.disposed) {
                refreshRequested = false;
                try { await loadRankings(); } catch (error) { if (!state.disposed) showMessage(userErrorMessage(error), true); }
            }
        })().finally(() => { refreshTask = null; });
        return refreshTask;
    }
    async function command(action) {
        if (state.busy || state.disposed) return;
        currentSession();
        if (action === 'start' && state.taxonomy.error) return;
        if (action === 'start' && (!state.taxonomy.selected || state.taxonomy.authRequired)) {
            state.taxonomy.message = 'Escolha uma categoria ou subcategoria para iniciar a pesquisa.';
            state.taxonomy.error = true;
            renderTaxonomy();
            return;
        }
        if (action === 'start' && (isActive(state.job) || state.taxonomy.loading)) return;
        state.busy = true;
        renderControls();
        try {
            const selected = state.taxonomy.selected;
            const result = await invoke(action, action === 'start' ? { category: selected } : {});
            if (state.disposed) return;
            if (action !== 'browser') {
                const changed = updateJob(result);
                if (changed) await refresh();
                else await synchronize();
            }
        } catch (error) { if (!state.disposed) showMessage(userErrorMessage(error), true); }
        finally { state.busy = false; if (!state.disposed) renderControls(); }
    }
    function synchronize() {
        if (state.disposed) return Promise.resolve();
        if (synchronizeTask) return synchronizeTask;
        synchronizeTask = (async () => {
            try {
                const api = electronApi();
                const latest = (api && typeof api.getPesquisaMercadoJobStatus === 'function') || window.top !== window
                    ? await invoke('status') : await request('/jobs/latest');
                if (state.disposed) return;
                if (updateJob(latest)) await refresh();
                else if (latest && latest.job == null && !state.job) renderControls();
            } catch (error) { if (!state.disposed) showMessage(userErrorMessage(error), true); }
        })().finally(() => { synchronizeTask = null; });
        return synchronizeTask;
    }
    function receiveProgress(payload) {
        if (state.disposed) return;
        currentSession();
        const job = payload && payload.job;
        if (!state.job) { if (sessionToken) synchronize(); return; }
        if (!job || job.id !== state.job.id) return;
        if (updateJob(payload)) refresh();
    }
    function onMessage(event) {
        if (event.source !== window.top) return;
        const data = event.data;
        if (!data || typeof data !== 'object') return;
        if (data.channel === 'jk-pesquisa-mercado-progress') receiveProgress(data.payload);
        if (data.channel === 'jk-pesquisa-mercado-result') {
            const item = pending.get(data.requestId);
            if (!item) return;
            pending.delete(data.requestId);
            window.clearTimeout(item.timer);
            if (data.error) item.reject(new Error(data.error)); else item.resolve(data.result);
        }
    }
    function changeTab(tab) {
        state.tab = tab;
        elements['tab-geral'].setAttribute('aria-selected', tab === 'geral' ? 'true' : 'false');
        elements['tab-vendedor'].setAttribute('aria-selected', tab === 'vendedor' ? 'true' : 'false');
        elements['tab-geral'].tabIndex = tab === 'geral' ? 0 : -1;
        elements['tab-vendedor'].tabIndex = tab === 'vendedor' ? 0 : -1;
        elements.sellers.hidden = tab !== 'vendedor';
        elements['seller-label'].hidden = tab !== 'vendedor';
        document.getElementById('pm-global-ranking-panel').setAttribute('aria-labelledby', `pm-global-tab-${tab}`);
        renderRanking();
    }
    function filtersChanged() {
        state.query = elements.search.value.trim();
        state.category = elements.category.value;
        state.offsets = { geral: 0, vendedor: 0 };
        state.generation += 1;
        window.clearTimeout(debounce);
        debounce = window.setTimeout(refresh, 250);
    }
    for (const action of ['start', 'pause', 'resume', 'cancel', 'browser']) elements[action].addEventListener('click', () => command(action));
    elements['taxonomy-refresh'].addEventListener('click', () => loadTaxonomy(state.taxonomy.parent, state.taxonomy.trail));
    elements['taxonomy-browser'].addEventListener('click', async () => {
        if (state.busy || isActive(state.job) || state.disposed) return;
        state.busy = true;
        renderControls();
        try {
            await invoke('browser', { purpose: 'categories' });
            if (!state.disposed) {
                state.taxonomy.message = 'Conclua o login no navegador e clique em Carregar categorias ou Atualizar lista.';
                renderTaxonomy();
            }
        } catch (error) {
            if (!state.disposed) { state.taxonomy.message = userErrorMessage(error); state.taxonomy.error = true; renderTaxonomy(); }
        } finally { state.busy = false; if (!state.disposed) renderControls(); }
    });
    for (const mode of ['manual', 'global']) {
        const button = document.getElementById(`pm-mode-${mode}`);
        button.addEventListener('click', () => changeMode(mode));
        button.addEventListener('keydown', event => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            const next = event.key === 'Home' ? 'manual' : event.key === 'End' ? 'global' : mode === 'manual' ? 'global' : 'manual';
            changeMode(next);
            document.getElementById(`pm-mode-${next}`).focus();
        });
    }
    for (const tab of ['geral', 'vendedor']) {
        elements[`tab-${tab}`].addEventListener('click', () => changeTab(tab));
        elements[`tab-${tab}`].addEventListener('keydown', event => {
            if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
                event.preventDefault();
                const next = event.key === 'Home' ? 'geral' : event.key === 'End' ? 'vendedor' : tab === 'geral' ? 'vendedor' : 'geral';
                changeTab(next);
                elements[`tab-${next}`].focus();
            }
        });
    }
    elements.search.addEventListener('input', filtersChanged);
    elements.category.addEventListener('change', filtersChanged);
    elements['seller-search'].addEventListener('input', renderSellers);
    for (const [name, direction] of [['previous', -1], ['next', 1]]) elements[name].addEventListener('click', () => {
        state.offsets[state.tab] = Math.max(0, state.offsets[state.tab] + direction * pageSize);
        state.generation += 1;
        refresh();
    });
    window.addEventListener('message', onMessage);
    const onStorage = event => {
        if (event.key !== 'access_token' && event.key !== null) return;
        currentSession();
        if (sessionToken) synchronize();
    };
    window.addEventListener('storage', onStorage);
    const api = electronApi();
    if (api && typeof api.onPesquisaMercadoProgress === 'function') unsubscribe = api.onPesquisaMercadoProgress(receiveProgress);
    else if (window.top !== window) {
        shellSend('subscribe', {}).catch(() => {});
        unsubscribe = () => window.top.postMessage({ channel: 'jk-pesquisa-mercado-command', requestId: `pm-${Date.now()}-${++state.commandSerial}`, action: 'unsubscribe', payload: {} }, '*');
    }
    function dispose() {
        state.disposed = true;
        if (typeof unsubscribe === 'function') unsubscribe();
        window.clearInterval(polling);
        window.clearTimeout(debounce);
        window.removeEventListener('message', onMessage);
        window.removeEventListener('storage', onStorage);
        for (const item of pending.values()) {
            window.clearTimeout(item.timer);
            item.reject(new Error('A tela foi fechada.'));
        }
        pending.clear();
    }
    window.addEventListener('beforeunload', dispose);
    renderControls();
    renderTaxonomy();
    renderScope();
    renderRanking();
    synchronize();
    polling = window.setInterval(synchronize, 15000);
    return { state, refresh, synchronize, receiveProgress, changeTab, changeMode, loadTaxonomy, dispose };
});
