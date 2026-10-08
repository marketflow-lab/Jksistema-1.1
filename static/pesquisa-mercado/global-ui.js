(function (root, factory) {
    'use strict';
    if (typeof module === 'object' && module.exports) module.exports = factory;
    else factory(root);
})(typeof window !== 'undefined' ? window : null, function createPesquisaMercadoUI(window) {
    'use strict';
    const document = window.document;
    const el = name => document.getElementById(`pm-global-${name}`);
    if (!el('start')) return null;

    const modes = ['manual', 'global', 'history', 'consolidated'];
    const pageSize = 100;
    const categoryCommandTimeout = 3 * (35000 + 15000) + 30000;
    const emptyTaxonomy = () => ({ trail: [], parent: null, selected: null, items: [], loaded: false,
        loading: false, authRequired: false, error: false, summary: null, updatedAt: '', childCounts: {}, requestId: 0, message: 'Escolha uma categoria ou subcategoria para iniciar a pesquisa.' });
    const state = {
        activeJob: null, scopeCategory: null, mode: 'manual', taxonomy: emptyTaxonomy(), tab: 'geral', query: '', category: '', seller: '', sellers: [],
        offsets: { geral: 0, vendedor: 0 }, rankings: { geral: null, vendedor: null },
        disposed: false, busy: false, generation: 0, commandSerial: 0
    };
    const pending = new Map();
    const deletedJobs = new Set();
    let unsubscribe = null;
    let polling = null;
    let debounce = null;
    let refreshTask = null;
    let refreshRequested = false;
    let synchronizeTask = null;
    let sessionToken = window.localStorage.getItem('access_token') || '';
    const avantEmailStoragePrefix = 'jkPesquisaMercadoAvantEmail:v1:';
    let avantIdentity = avantConfigurationIdentity(sessionToken);
    const elements = {};
    for (const name of ['start', 'pause', 'resume', 'cancel', 'browser', 'refresh-names', 'status', 'status-panel', 'status-detail', 'status-warning', 'category-current',
        'categories-count', 'pages-count', 'sellers-count', 'ads-count', 'pending-count', 'search',
        'category', 'tab-geral', 'tab-vendedor', 'sellers', 'seller-search', 'seller-list', 'seller-label',
        'body', 'empty', 'count', 'previous', 'next', 'page', 'scope', 'taxonomy-refresh', 'taxonomy-breadcrumb',
        'taxonomy-list', 'taxonomy-message', 'taxonomy-browser', 'taxonomy-selection', 'taxonomy-summary', 'taxonomy-updated', 'avant-email', 'avant-email-note']) elements[name] = el(name);

    function avantConfigurationIdentity(token) {
        if (!token) return '';
        try {
            const user = JSON.parse(window.localStorage.getItem('user_data') || 'null');
            const parts = token.split('.');
            if (!user || parts.length !== 3) return '';
            const claims = JSON.parse(decodeURIComponent(Array.from(window.atob(parts[1].replace(/-/g, '+').replace(/_/g, '/')))
                .map(character => `%${character.charCodeAt(0).toString(16).padStart(2, '0')}`).join('')));
            if (typeof user.client_id !== 'string' || typeof user.username !== 'string'
                || typeof claims.client_id !== 'string' || typeof claims.sub !== 'string') return '';
            const client = user.client_id.trim();
            const username = user.username.trim();
            if (!client || !username || client.length > 200 || username.length > 200
                || /[\u0000-\u001f\u007f]/.test(client + username)
                || client !== claims.client_id.trim() || username !== claims.sub.trim()) return '';
            // Claims bind the local preference to its owner; backend verification still authorizes every command.
            return avantEmailStoragePrefix + encodeURIComponent(JSON.stringify([client, username]));
        } catch (_error) { return ''; }
    }
    function validAvantEmail(value) {
        return typeof value === 'string' && value.length <= 254 && value === value.trim()
            && /^(?=.{1,64}@)[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$/.test(value)
            && !value.startsWith('.') && !value.includes('..') && !value.includes('.@');
    }
    function restoreAvantConfiguration() {
        if (!elements['avant-email']) return;
        let saved = '';
        if (sessionToken && avantIdentity) {
            try { saved = window.localStorage.getItem(avantIdentity) || ''; } catch (_error) {}
        }
        elements['avant-email'].value = validAvantEmail(saved) ? saved : '';
        elements['avant-email'].setCustomValidity('');
    }
    function saveAvantConfiguration() {
        if (!elements['avant-email']) return '';
        const value = elements['avant-email'].value.trim();
        if (value && !validAvantEmail(value)) {
            elements['avant-email'].setCustomValidity('Informe um e-mail válido do Avant Pro.');
            throw new Error('Informe um e-mail válido do Avant Pro para o login automático.');
        }
        elements['avant-email'].setCustomValidity('');
        elements['avant-email'].value = value;
        if (sessionToken && avantIdentity) {
            try {
                if (value) window.localStorage.setItem(avantIdentity, value);
                else window.localStorage.removeItem(avantIdentity);
            } catch (_error) { /* A sessão atual pode usar o valor mesmo sem armazenamento local. */ }
        }
        return value;
    }

    const photoPreview = createPhotoPreview();
    const safeText = value => String(value == null ? '' : value);
    const sellerLabel = item => window.PesquisaMercadoCore ? window.PesquisaMercadoCore.nomeVendedorParaExibicao(item) : safeText(item.vendedor || item.nome || item.seller_key || 'Nome pendente');
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
    function createPhotoPreview() {
        let preview = null;
        let activeButton = null;
        const listeners = [];
        function listen(target, type, callback, options) {
            target.addEventListener(type, callback, options);
            listeners.push(() => target.removeEventListener(type, callback, options));
        }
        function hide() {
            if (activeButton) activeButton.removeAttribute('aria-describedby');
            activeButton = null;
            if (preview) { preview.hidden = true; preview.replaceChildren(); }
        }
        function photoButton(target) {
            const button = target && target.closest && target.closest('.pm-global-photo-trigger');
            return button && elements.body.contains(button) ? button : null;
        }
        function show(button) {
            const thumbnail = button && button.querySelector('.pm-global-photo');
            const source = thumbnail && safeUrl(thumbnail.currentSrc || thumbnail.src, true);
            if (!source || !button.isConnected || (thumbnail.complete && !thumbnail.naturalWidth)) return;
            if (activeButton === button) return;
            hide();
            const margin = 12;
            const width = document.documentElement.clientWidth;
            const height = document.documentElement.clientHeight;
            const size = Math.min(320, width - margin * 2, height - margin * 2);
            if (size < 64) return;
            if (!preview) {
                preview = document.createElement('div');
                preview.id = 'pm-global-photo-preview';
                preview.className = 'pm-global-photo-preview';
                preview.setAttribute('role', 'tooltip');
                preview.hidden = true;
                document.body.appendChild(preview);
            }
            activeButton = button;
            const image = document.createElement('img');
            image.alt = button.getAttribute('aria-label') || 'Foto ampliada do anúncio';
            image.referrerPolicy = 'no-referrer';
            image.onload = () => {
                if (activeButton !== button || preview.firstElementChild !== image) return;
                if (!button.isConnected || !button.getClientRects().length) { hide(); return; }
                const rect = button.getBoundingClientRect();
                let left = rect.right + margin;
                if (left + size > width - margin) left = rect.left - size - margin;
                left = Math.max(margin, Math.min(left, width - size - margin));
                const top = Math.max(margin, Math.min(rect.top + (rect.height - size) / 2, height - size - margin));
                Object.assign(preview.style, { width: `${size}px`, height: `${size}px`, left: `${left}px`, top: `${top}px` });
                button.setAttribute('aria-describedby', preview.id);
                preview.hidden = false;
            };
            image.onerror = () => { if (activeButton === button && preview.firstElementChild === image) hide(); };
            preview.replaceChildren(image);
            image.src = source;
        }
        const enter = event => {
            if (event.pointerType === 'touch') return;
            const button = photoButton(event.target);
            if (button && !button.contains(event.relatedTarget)) show(button);
        };
        const leave = event => {
            const button = photoButton(event.target);
            if (button && button === activeButton && !button.contains(event.relatedTarget)) hide();
        };
        listen(elements.body, 'pointerover', enter);
        listen(elements.body, 'pointerout', leave);
        listen(elements.body, 'focusin', event => show(photoButton(event.target)));
        listen(elements.body, 'focusout', leave);
        listen(elements.body, 'click', event => show(photoButton(event.target)));
        listen(document, 'pointerdown', event => { if (activeButton && !activeButton.contains(event.target)) hide(); });
        listen(document, 'keydown', event => { if (event.key === 'Escape') hide(); });
        listen(document, 'visibilitychange', () => { if (document.visibilityState !== 'visible') hide(); });
        listen(window, 'scroll', hide, true);
        listen(window, 'resize', hide);
        listen(window, 'blur', hide);
        return { hide, dispose() { hide(); listeners.forEach(remove => remove()); if (preview) preview.remove(); } };
    }
    function userErrorMessage(error) {
        const message = typeof error === 'string' ? error : error && typeof error.message === 'string'
            ? error.message : 'Não foi possível concluir a operação.';
        return message.replace(/^Error invoking remote method ['"]pesquisa-mercado:(?:categories|start|status|resume|pause|cancel|show-browser|refresh-names)['"]:\s*(?:Error:\s*)?/, '');
    }
    function categoryChoice(value) {
        if (!value || typeof value !== 'object' || Array.isArray(value)
            || Object.keys(value).some(key => !['key', 'nome', 'url'].includes(key))
            || typeof value.key !== 'string' || typeof value.nome !== 'string' || typeof value.url !== 'string') return null;
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
        const identity = avantConfigurationIdentity(current);
        if (current !== sessionToken || identity !== avantIdentity) {
            sessionToken = current;
            avantIdentity = identity;
            restoreAvantConfiguration();
            deletedJobs.clear();
            state.activeJob = null;
            state.namesLogin = false;
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
            renderActiveResearch();
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
            }, action === 'refresh-names' ? 180000 : action === 'categories' ? categoryCommandTimeout : 45000);
            pending.set(requestId, { resolve, reject, timer });
            window.top.postMessage({ channel: 'jk-pesquisa-mercado-command', requestId, action, payload }, '*');
        });
    }
    const methods = {
        start: 'startPesquisaMercadoJob', status: 'getPesquisaMercadoJobStatus', pause: 'pausePesquisaMercadoJob',
        resume: 'resumePesquisaMercadoJob', cancel: 'cancelPesquisaMercadoJob', browser: 'showPesquisaMercadoBrowser',
        categories: 'getPesquisaMercadoCategories', 'refresh-names': 'refreshPesquisaMercadoNames'
    };
    async function invoke(action, extras = {}) {
        const authToken = currentSession();
        if (!authToken) throw new Error('Entre no JK Sistema para consultar a pesquisa.');
        const payload = { authToken };
        if (state.activeJob && ['pause', 'resume', 'cancel', 'browser'].includes(action) && extras.purpose !== 'categories') payload.jobId = state.activeJob.id;
        if (extras.jobId) payload.jobId = extras.jobId;
        if (action === 'refresh-names' && !payload.jobId && state.activeJob) payload.jobId = state.activeJob.id;
        if (action === 'start') payload.category = categoryChoice(extras.category);
        if (action === 'categories' && extras.parentCategory) payload.parentCategory = categoryChoice(extras.parentCategory);
        if (action === 'categories' && extras.forceRefresh === true) payload.forceRefresh = true;
        if (action === 'browser' && ['categories', 'names'].includes(extras.purpose)) payload.purpose = extras.purpose;
        if (['start', 'resume', 'refresh-names'].includes(action)) {
            const email = saveAvantConfiguration();
            if (email) payload.avantLoginEmail = email;
        }
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
        if (state.activeJob) {
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
        const deleting = window.__jkPesquisaMercadoHistory && window.__jkPesquisaMercadoHistory.state.history.deleteBusy;
        const status = state.activeJob ? state.activeJob.status : '';
        const running = ['queued', 'running', 'pausing'].includes(status);
        const resumable = ['paused', 'waiting_login', 'interrupted', 'failed'].includes(status);
        if (elements['avant-email']) elements['avant-email'].disabled = !sessionToken || state.busy || running;
        elements.start.disabled = !sessionToken || state.busy || isActive(state.activeJob) || state.taxonomy.loading || !state.taxonomy.selected || state.taxonomy.authRequired || state.taxonomy.error;
        elements.pause.disabled = state.busy || !running;
        elements.resume.disabled = state.busy || !!deleting || !resumable;
        elements.cancel.disabled = state.busy || !!deleting || !(isActive(state.activeJob) || resumable);
        elements.browser.disabled = state.busy || !(isActive(state.activeJob) || resumable || state.namesLogin);
        if (elements['refresh-names']) elements['refresh-names'].disabled = !sessionToken || state.busy || !!deleting || !state.activeJob || ['queued', 'running', 'pausing', 'waiting_login'].includes(status) || state.taxonomy.loading;
        const taxonomyDisabled = !sessionToken || state.busy || state.taxonomy.loading || isActive(state.activeJob);
        elements['taxonomy-refresh'].disabled = taxonomyDisabled;
        elements['taxonomy-browser'].disabled = !sessionToken || state.busy || isActive(state.activeJob);
        for (const button of elements['taxonomy-list'].querySelectorAll('button')) button.disabled = taxonomyDisabled;
        for (const button of elements['taxonomy-breadcrumb'].querySelectorAll('button')) button.disabled = taxonomyDisabled;
        window.dispatchEvent(new window.CustomEvent('jk-pesquisa-mercado-operation-change'));
    }
    function renderScope() {
        elements.scope.textContent = state.scopeCategory ? `Escopo da pesquisa: ${state.scopeCategory.nome}`
            : state.activeJob ? 'Escopo da pesquisa: execução anterior.' : 'Nenhuma pesquisa iniciada.';
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
            const count = document.createElement('span');
            count.className = 'pm-taxonomy-count';
            const children = taxonomy.childCounts[category.key];
            count.textContent = children === 0 ? 'Último nível' : `${number(children)} subcategorias`;
            button.appendChild(name);
            button.appendChild(count);
            button.addEventListener('click', () => loadTaxonomy(category, taxonomy.trail.concat(category)));
            elements['taxonomy-list'].appendChild(button);
        }
        const message = isActive(state.activeJob) ? 'A seleção de uma nova categoria fica disponível ao concluir ou cancelar esta pesquisa.'
            : taxonomy.loading ? 'Carregando categorias do Mercado Livre…' : taxonomy.message;
        elements['taxonomy-summary'].textContent = taxonomy.summary
            ? `Árvore oficial: ${number(taxonomy.summary.rootCount)} categorias principais · ${number(taxonomy.summary.subcategoryCount)} subcategorias · até ${number(taxonomy.summary.maxDepth)} níveis.` : '';
        elements['taxonomy-updated'].textContent = taxonomy.updatedAt
            ? `Atualizada em ${new Date(taxonomy.updatedAt).toLocaleString('pt-BR')}` : '';
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
    async function loadTaxonomy(parentCategory = null, trail = [], forceRefresh = false) {
        if (synchronizeTask) await synchronizeTask;
        currentSession();
        const taxonomy = state.taxonomy;
        if (state.disposed || state.mode !== 'global' || !sessionToken || isActive(state.activeJob) || state.busy || taxonomy.loading) return;
        const requestId = ++taxonomy.requestId;
        taxonomy.loading = true;
        taxonomy.error = false;
        taxonomy.authRequired = false;
        renderTaxonomy();
        try {
            const result = await invoke('categories', { parentCategory, forceRefresh });
            if (state.disposed || state.taxonomy !== taxonomy || taxonomy.requestId !== requestId) return;
            if (result && result.authRequired) {
                taxonomy.authRequired = true;
                taxonomy.error = true;
                taxonomy.message = result.reason || 'Conclua o login no navegador e depois recarregue as categorias.';
                return;
            }
            const incomplete = 'Não foi possível carregar a árvore completa de categorias. Clique em Atualizar lista para tentar novamente.';
            const summary = result && result.summary;
            const counts = result && result.childCounts;
            if (!result || result.complete !== true || !Array.isArray(result.items)
                || !summary || !['total', 'rootCount', 'subcategoryCount', 'maxDepth'].every(key => Number.isSafeInteger(summary[key]) && summary[key] >= 0)
                || summary.rootCount < 1 || summary.maxDepth < 1 || summary.total !== summary.rootCount + summary.subcategoryCount
                || typeof result.updatedAt !== 'string' || !Number.isFinite(Date.parse(result.updatedAt))
                || !counts || typeof counts !== 'object' || Array.isArray(counts)) throw new Error(incomplete);
            const selected = categoryChoice(result.parentCategory);
            if (parentCategory ? !selected || selected.key !== parentCategory.key : result.parentCategory !== null) {
                throw new Error('A categoria retornada não corresponde à seleção. Recarregue a lista.');
            }
            const categories = result.items.map(categoryChoice);
            if (categories.some(category => !category) || new Set(categories.map(category => category.key)).size !== categories.length
                || categories.some(category => !Object.prototype.hasOwnProperty.call(counts, category.key)
                    || !Number.isSafeInteger(counts[category.key]) || counts[category.key] < 0 || counts[category.key] > summary.total)
                || !selected && categories.length !== summary.rootCount) throw new Error(incomplete);
            taxonomy.parent = selected;
            taxonomy.selected = selected;
            taxonomy.trail = selected ? trail.slice(0, -1).concat(selected) : [];
            taxonomy.items = categories;
            taxonomy.summary = summary;
            taxonomy.updatedAt = result.updatedAt;
            taxonomy.childCounts = counts;
            taxonomy.loaded = true;
            taxonomy.message = selected ? taxonomy.items.length
                ? 'Pesquise na categoria selecionada ou escolha uma das subcategorias abaixo.'
                : 'Esta categoria é o último nível da árvore oficial. Clique em pesquisar para iniciar.'
                : taxonomy.items.length ? 'Escolha uma categoria ou subcategoria para iniciar a pesquisa.'
                    : 'Nenhuma categoria foi encontrada. Atualize a lista para tentar novamente.';
        } catch (error) {
            if (state.taxonomy === taxonomy && taxonomy.requestId === requestId && !state.disposed) {
                taxonomy.error = true;
                taxonomy.message = userErrorMessage(error);
            }
        } finally {
            if (state.taxonomy === taxonomy && taxonomy.requestId === requestId && !state.disposed) {
                taxonomy.loading = false;
                renderTaxonomy();
            }
        }
    }
    function changeMode(mode) {
        photoPreview.hide();
        if (!modes.includes(mode) || state.disposed) return;
        state.mode = mode;
        for (const value of modes) {
            const active = value === mode;
            const button = document.getElementById(`pm-mode-${value}`);
            button.setAttribute('aria-selected', active ? 'true' : 'false');
            button.tabIndex = active ? 0 : -1;
            document.getElementById(`pm-panel-${value}`).hidden = !active;
        }
        window.dispatchEvent(new window.CustomEvent('jk-pesquisa-mercado-mode-change', { detail: { mode } }));
        if (mode === 'global' && !state.taxonomy.loaded && !state.taxonomy.loading) loadTaxonomy();
    }
    function renderActiveResearch() {
        const bar = document.getElementById('pm-active-research');
        if (!bar) return;
        const job = state.activeJob;
        bar.hidden = !isActive(job);
        if (!isActive(job)) return;
        const labels = { paused: 'Pesquisa pausada', waiting_login: 'Pesquisa aguardando login' };
        document.getElementById('pm-active-research-label').textContent = labels[job.status] || 'Pesquisa em andamento';
        document.getElementById('pm-active-research-detail').textContent =
            `${state.scopeCategory ? state.scopeCategory.nome : 'Pesquisa geral no ML'} · ${number(job.counters && job.counters.ads || 0)} anúncios salvos`;
    }
    function clearJob() {
        state.activeJob = null; state.namesLogin = false; state.scopeCategory = null;
        state.seller = ''; state.sellers = []; state.offsets = { geral: 0, vendedor: 0 };
        state.rankings = { geral: null, vendedor: null }; state.generation += 1;
        elements.category.innerHTML = '<option value="">Todas as categorias</option>'; state.category = '';
        for (const name of ['categories', 'pages', 'sellers', 'ads', 'pending']) elements[`${name}-count`].textContent = '0';
        elements['category-current'].textContent = 'Aguardando categoria';
        renderScope(); renderActiveResearch(); renderSellers(); renderRanking(); renderControls();
        showMessage('Escolha uma categoria para iniciar uma pesquisa.');
    }
    const onJobDeleted = event => {
        const jobId = event.detail && event.detail.jobId;
        if (state.disposed || !jobId) return;
        currentSession(); deletedJobs.add(jobId);
        if (state.activeJob && state.activeJob.id === jobId) clearJob();
    };
    window.addEventListener('jk-pesquisa-mercado-job-deleted', onJobDeleted);
    const onDeleteChange = () => { if (!state.disposed) renderControls(); };
    window.addEventListener('jk-pesquisa-mercado-history-delete-change', onDeleteChange);
    const canDeleteJob = () => !state.busy && !state.namesBusy && !state.disposed;
    window.__jkPesquisaMercadoCanDeleteJob = canDeleteJob;
    function updateJob(raw) {
        const job = raw && Object.prototype.hasOwnProperty.call(raw, 'job') ? raw.job : raw;
        if (!job || !job.id) {
            if (raw && Object.prototype.hasOwnProperty.call(raw, 'job') && raw.job === null && state.activeJob) { clearJob(); return true; }
            return false;
        }
        if (deletedJobs.has(job.id)) return false;
        if (state.activeJob && state.activeJob.id === job.id && Number(job.revision) < Number(state.activeJob.revision)) return false;
        const changed = !state.activeJob || state.activeJob.id !== job.id || state.activeJob.revision !== job.revision;
        if (!state.activeJob || state.activeJob.id !== job.id) {
            state.namesLogin = false;
            state.offsets = { geral: 0, vendedor: 0 };
            state.rankings = { geral: null, vendedor: null };
            state.seller = '';
            state.generation += 1;
            state.scopeCategory = null;
        }
        state.activeJob = job;
        const scopeCategory = categoryChoice(job.scopeCategory || job.checkpoint && job.checkpoint.scope && job.checkpoint.scope.category);
        if (scopeCategory) state.scopeCategory = scopeCategory;
        renderScope();
        renderActiveResearch();
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
        const sellers = state.sellers.filter(seller => `${sellerLabel(seller)} ${seller.seller_key}`.toLocaleLowerCase('pt-BR').includes(query));
        elements['seller-list'].innerHTML = '';
        for (const seller of sellers) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = `pm-global-seller${seller.seller_key === state.seller ? ' selected' : ''}`;
            button.setAttribute('aria-pressed', seller.seller_key === state.seller ? 'true' : 'false');
            button.textContent = `${sellerLabel(seller)}${seller.ads != null ? ` · ${number(seller.ads)} anúncios` : ''}`;
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
        elements['seller-label'].textContent = selected ? sellerLabel(selected) : 'Selecione um vendedor para ver seu ranking.';
    }
    function renderRanking() {
        photoPreview.hide();
        const data = state.rankings[state.tab];
        const rows = data && Array.isArray(data.items) ? data.items : [];
        elements.body.innerHTML = rows.map(item => {
            const ranked = Number.isInteger(item.rank) && item.rank > 0;
            const image = safeUrl(item.imagem, true);
            const url = safeUrl(item.url, false);
            return `<tr><td class="rank-cell">${ranked ? item.rank : ''}</td>` +
                `<td>${image ? `<button class="pm-global-photo-trigger" type="button" aria-label="Ampliar foto de ${escaped(item.titulo || 'anúncio sem título')}"><img class="pm-global-photo" src="${escaped(image)}" alt="" loading="lazy" referrerpolicy="no-referrer"></button>` : '—'}</td>` +
                `<td class="title-cell">${escaped(item.titulo || 'Anúncio sem título')}<small>${escaped(item.id)}</small></td>` +
                `<td>${escaped(sellerLabel(item))}</td><td>${escaped(formatPrice(item.preco))}</td>` +
                `<td class="sales-cell">${number(item.vendas)}</td><td>${escaped(formatDate(item.data_criacao))}</td>` +
                `<td>${number(item.media_vendas_mensal)}</td><td>${url ? `<a class="link" href="${escaped(url)}" target="_blank" rel="noopener noreferrer">Abrir</a>` : '—'}</td>` +
                `<td><span class="status-chip${ranked ? '' : ' warning'}">${ranked ? 'Analisado' : escaped(item.erro || item.status || 'Aguardando dados do Avant')}</span></td></tr>`;
        }).join('');
        const total = data ? Number(data.total) || 0 : 0;
        const offset = state.offsets[state.tab];
        elements.empty.hidden = rows.length > 0;
        elements.empty.textContent = state.tab === 'vendedor' && !state.seller
            ? 'Selecione um vendedor para ver seu ranking.' : state.activeJob
                ? 'Nenhum anúncio disponível para estes filtros.' : 'Inicie uma pesquisa para consultar os rankings.';
        elements.count.textContent = `${number(total)} anúncio(s)`;
        elements.page.textContent = `Página ${Math.floor(offset / pageSize) + 1} de ${Math.max(1, Math.ceil(total / pageSize))}`;
        elements.previous.disabled = offset === 0;
        elements.next.disabled = offset + pageSize >= total;
    }
    async function loadRankings() {
        if (!state.activeJob || state.disposed) return;
        const jobId = state.activeJob.id;
        const generation = state.generation;
        const base = `/jobs/${encodeURIComponent(jobId)}`;
        const [sellers, categories] = await Promise.all([request(`${base}/vendedores`), request(`${base}/categorias`)]);
        if (state.disposed || generation !== state.generation || !state.activeJob || jobId !== state.activeJob.id) { refreshRequested = true; return; }
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
        if (state.disposed || generation !== state.generation || !state.activeJob || jobId !== state.activeJob.id) { refreshRequested = true; return; }
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
                const generation = state.generation;
                try { await loadRankings(); } catch (error) { if (!state.disposed && generation === state.generation) showMessage(userErrorMessage(error), true); }
            }
        })().finally(() => { refreshTask = null; });
        return refreshTask;
    }
    async function refreshNames(jobId) {
        if (state.busy || state.disposed || window.__jkPesquisaMercadoHistory && window.__jkPesquisaMercadoHistory.state.history.deleteBusy) throw new Error('Aguarde a operação atual.');
        if (!jobId) throw new Error('Escolha uma pesquisa salva.');
        state.busy = state.namesBusy = true; renderControls();
        try {
            const result = await invoke('refresh-names', { jobId });
            if (state.disposed) return result;
            if (state.activeJob && state.activeJob.id === jobId) {
                state.namesLogin = !!result.requiresLogin;
                updateJob(result); await refresh();
            }
            window.dispatchEvent(new window.CustomEvent('jk-pesquisa-mercado-names-updated', { detail: { jobId } }));
            return result;
        } finally { state.busy = state.namesBusy = false; if (!state.disposed) renderControls(); }
    }
    window.__jkPesquisaMercadoRefreshNames = refreshNames;
    window.__jkPesquisaMercadoNamesBrowser = jobId => invoke('browser', { jobId, purpose: 'names' });
    async function command(action) {
        if (state.busy || state.disposed || window.__jkPesquisaMercadoHistory && window.__jkPesquisaMercadoHistory.state.history.deleteBusy) return;
        currentSession();
        if (action === 'refresh-names') {
            showMessage('Consultando os nomes das lojas…');
            try {
                const result = await refreshNames(state.activeJob && state.activeJob.id);
                showMessage(result.message || `${number(result.updated || 0)} nomes atualizados. ${number(result.pending || 0)} nomes pendentes.`, !!result.requiresLogin);
            } catch (error) { if (!state.disposed) showMessage(userErrorMessage(error), true); }
            return;
        }
        if (action === 'browser' && state.namesLogin) {
            try { await invoke('browser', { purpose: 'names' }); }
            catch (error) { showMessage(userErrorMessage(error), true); }
            return;
        }
        if (action === 'start'  && state.taxonomy.error) return;
        if (action === 'start' && (!state.taxonomy.selected || state.taxonomy.authRequired)) {
            state.taxonomy.message = 'Escolha uma categoria ou subcategoria para iniciar a pesquisa.';
            state.taxonomy.error = true;
            renderTaxonomy();
            return;
        }
        if (action === 'start' && (isActive(state.activeJob) || state.taxonomy.loading)) return;
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
        if (state.disposed || state.namesBusy) return Promise.resolve();
        if (synchronizeTask) return synchronizeTask;
        synchronizeTask = (async () => {
            try {
                currentSession();
                const generation = state.generation, token = sessionToken;
                const api = electronApi();
                const latest = (api && typeof api.getPesquisaMercadoJobStatus === 'function') || window.top !== window
                    ? await invoke('status') : await request('/jobs/latest');
                if (state.disposed || generation !== state.generation || currentSession() !== token) return;
                if (updateJob(latest)) await refresh();
                else if (latest && latest.job == null && !state.activeJob) renderControls();
            } catch (error) { if (!state.disposed) showMessage(userErrorMessage(error), true); }
        })().finally(() => { synchronizeTask = null; });
        return synchronizeTask;
    }
    function receiveProgress(payload) {
        if (state.disposed) return;
        currentSession();
        const job = payload && payload.job;
        if (!state.activeJob) { if (sessionToken) synchronize(); return; }
        if (!job || job.id !== state.activeJob.id) return;
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
        photoPreview.hide();
        state.query = elements.search.value.trim();
        state.category = elements.category.value;
        state.offsets = { geral: 0, vendedor: 0 };
        state.generation += 1;
        window.clearTimeout(debounce);
        debounce = window.setTimeout(refresh, 250);
    }
    for (const action of ['start', 'pause', 'resume', 'cancel', 'browser', 'refresh-names']) elements[action].addEventListener('click', () => command(action));
    elements['taxonomy-refresh'].addEventListener('click', () => loadTaxonomy(state.taxonomy.parent, state.taxonomy.trail, true));
    elements['taxonomy-browser'].addEventListener('click', async () => {
        if (state.busy || isActive(state.activeJob) || state.disposed) return;
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
        } finally { state.busy = state.namesBusy = false; if (!state.disposed) renderControls(); }
    });
    const followActive = document.getElementById('pm-active-research-follow');
    if (followActive) followActive.addEventListener('click', () => changeMode('global'));
    window.__jkPesquisaMercadoChangeMode = changeMode;
    for (const mode of modes) {
        const button = document.getElementById(`pm-mode-${mode}`);
        button.addEventListener('click', () => changeMode(mode));
        button.addEventListener('keydown', event => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            const index = modes.indexOf(mode);
            const next = event.key === 'Home' ? modes[0] : event.key === 'End' ? modes[modes.length - 1]
                : modes[(index + (event.key === 'ArrowRight' ? 1 : -1) + modes.length) % modes.length];
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
    if (elements['avant-email']) {
        elements['avant-email'].addEventListener('input', () => elements['avant-email'].setCustomValidity(''));
        elements['avant-email'].addEventListener('change', () => {
            currentSession();
            try { saveAvantConfiguration(); }
            catch (error) { showMessage(userErrorMessage(error), true); elements['avant-email'].reportValidity(); }
        });
    }
    for (const [name, direction] of [['previous', -1], ['next', 1]]) elements[name].addEventListener('click', () => {
        state.offsets[state.tab] = Math.max(0, state.offsets[state.tab] + direction * pageSize);
        state.generation += 1;
        refresh();
    });
    window.addEventListener('message', onMessage);
    const onStorage = event => {
        if (event.key === avantIdentity) { restoreAvantConfiguration(); return; }
        if (event.key !== 'access_token' && event.key !== 'user_data' && event.key !== null) return;
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
        photoPreview.dispose();
        state.disposed = true;
        if (window.__jkPesquisaMercadoChangeMode === changeMode) delete window.__jkPesquisaMercadoChangeMode;
        if (window.__jkPesquisaMercadoRefreshNames === refreshNames) { delete window.__jkPesquisaMercadoRefreshNames; delete window.__jkPesquisaMercadoNamesBrowser; }
        if (typeof unsubscribe === 'function') unsubscribe();
        window.clearInterval(polling);
        window.clearTimeout(debounce);
        window.removeEventListener('message', onMessage);
        window.removeEventListener('storage', onStorage);
        window.removeEventListener('jk-pesquisa-mercado-job-deleted', onJobDeleted);
        window.removeEventListener('jk-pesquisa-mercado-history-delete-change', onDeleteChange);
        if (window.__jkPesquisaMercadoCanDeleteJob === canDeleteJob) delete window.__jkPesquisaMercadoCanDeleteJob;
        for (const item of pending.values()) {
            window.clearTimeout(item.timer);
            item.reject(new Error('A tela foi fechada.'));
        }
        pending.clear();
    }
    window.addEventListener('beforeunload', dispose);
    restoreAvantConfiguration();
    renderControls();
    renderTaxonomy();
    renderScope();
    renderActiveResearch();
    renderRanking();
    synchronize();
    polling = window.setInterval(synchronize, 15000);
    return { state, refresh, synchronize, receiveProgress, changeTab, changeMode, loadTaxonomy, dispose };
});
