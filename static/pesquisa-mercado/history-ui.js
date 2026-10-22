(function (root, factory) {
    'use strict';
    if (typeof module === 'object' && module.exports) module.exports = factory;
    else root.__jkPesquisaMercadoHistory = factory(root);
})(typeof window !== 'undefined' ? window : null, function createPesquisaMercadoHistory(window) {
    'use strict';
    const document = window.document;
    const historyRoot = document.getElementById('pm-history-root');
    const consolidatedRoot = document.getElementById('pm-consolidated-root');
    if (!historyRoot || !consolidatedRoot) return null;
    const statuses = { running: 'Em andamento', paused: 'Pausada', waiting_login: 'Aguardando login',
        cancelled: 'Cancelada', completed: 'Concluída', completed_partial: 'Concluída parcialmente', failed: 'Falha',
        queued: 'Preparando', pausing: 'Pausando', interrupted: 'Interrompida' };
    const text = value => String(value == null ? '' : value);
    const sellerLabel = item => window.PesquisaMercadoCore ? window.PesquisaMercadoCore.nomeVendedorParaExibicao(item) : text(item.vendedor || item.nome || item.seller_key || 'Nome pendente');
    const escaped = value => text(value).replace(/[&<>"']/g, value => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[value]);
    const number = value => value !== null && value !== '' && Number.isFinite(Number(value))
        ? Number(value).toLocaleString('pt-BR', { maximumFractionDigits: 2 }) : '—';
    const date = value => {
        const parsed = new Date(value);
        return value && Number.isFinite(parsed.getTime()) ? parsed.toLocaleString('pt-BR', { timeZone: 'America/Sao_Paulo', dateStyle: 'short', timeStyle: 'short' }) : '—';
    };
    const price = value => typeof value === 'number' && Number.isFinite(value)
        ? value.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' }) : text(value) || '—';
    function safeUrl(value) {
        try {
            const url = new URL(text(value));
            return url.protocol === 'https:' && !url.username && !url.password && /(^|\.)mercadolivre\.com\.br$/i.test(url.hostname) ? url.href : '';
        } catch (_error) { return ''; }
    }
    const scope = job => job && (job.scope_category || job.scopeCategory || job.checkpoint && job.checkpoint.scope && job.checkpoint.scope.category);
    const scopeName = job => {
        const checkpoint = job && job.checkpoint || {};
        const path = job && job.scopePath || checkpoint.officialTaxonomy && checkpoint.officialTaxonomy.ancestry || checkpoint.scope && checkpoint.scope.path;
        const names = Array.isArray(path) ? path.map(item => text(typeof item === 'string' ? item : item && (item.nome || item.name)).trim()).filter(Boolean) : [];
        return names.length ? names.join(' → ') : text(scope(job) && scope(job).nome) || 'Todo o Mercado Livre';
    };
    const incompleteCategories = job => Array.isArray(job && job.incompleteCategories) ? job.incompleteCategories
        : Array.isArray(job && job.checkpoint && job.checkpoint.incompleteCategories) ? job.checkpoint.incompleteCategories : [];
    const salesRecoveryPending = job => !!job && (job.salesRecoveryPending === true || !!job.checkpoint && job.checkpoint.salesRecoveryPending === true);
    const canRecover = job => !!job && ['completed_partial', 'failed'].includes(job.status) && (Number(job.incompleteCount) > 0 || incompleteCategories(job).length > 0 || salesRecoveryPending(job));
    function incompleteReason(item) {
        const reasons = { category_link_missing: 'Link da categoria não localizado.', category_link_ambiguous: 'Mais de um link corresponde à categoria.',
            category_filters_unavailable: 'Os filtros de categorias não ficaram disponíveis.', category_scope_mismatch: 'A página aberta não corresponde ao escopo escolhido.',
            category_not_found: 'Categoria não encontrada.', category_load_timeout: 'O carregamento da categoria excedeu o tempo limite.',
            category_read_failed: 'Não foi possível ler a categoria.', category_pagination_failed: 'Não foi possível consultar a próxima página da categoria.',
            category_access_denied: 'O Mercado Livre bloqueou o acesso à categoria.',
            category_mismatch: 'A página aberta não corresponde à categoria.', category_unavailable: 'Categoria indisponível.',
            navigation_timeout: 'O carregamento da categoria excedeu o tempo limite.', timeout: 'O carregamento da categoria excedeu o tempo limite.',
            login_required: 'Login necessário no Mercado Livre.', waiting_login: 'Login necessário no Mercado Livre.',
            blocked: 'O Mercado Livre bloqueou o acesso à página.', ancestor_unavailable: 'Uma categoria anterior do caminho ficou indisponível.',
            listing_unrecognized: 'A listagem não pôde ser confirmada.', legacy_unknown: 'Motivo anterior desconhecido.' };
        const code = item && typeof item === 'object' && (item.code || item.reasonCode || item.motivo);
        return reasons[code] || 'Motivo anterior desconhecido.';
    }
    const freshHistory = () => ({ items: [], total: 0, offset: 0, revision: null, query: '', status: '', scope: '', scopes: [],
        scopesOffset: 0, scopesTotal: 0, busy: false, scopesBusy: false, loaded: false, generation: 0,
        deleteId: '', deleteBusy: '', detail: null, detailId: '', detailTab: 'geral', detailQuery: '', detailCategory: '', detailSeller: '',
        detailSellers: [], detailCategories: [], detailItems: [], detailTotal: 0, detailOffset: 0, detailBusy: false, namesBusy: false, recoverBusy: false, namesLogin: false, detailNeedsRefresh: false, detailGeneration: 0 });
    const freshConsolidated = () => ({ items: [], total: 0, offset: 0, revision: null, query: '', evidence: '', scope: '', scopes: [],
        scopesOffset: 0, scopesTotal: 0, busy: false, scopesBusy: false, loaded: false, generation: 0, summary: null,
        origin: null, originItems: [], originTotal: 0, originOffset: 0, originBusy: false, originGeneration: 0 });
    const state = { history: freshHistory(), consolidated: freshConsolidated(), mode: 'manual', disposed: false };
    let sessionToken = window.localStorage.getItem('access_token') || '';
    let openingDetail = false;
    const controllers = new Map();
    const removers = [];
    const timers = new Map();
    const el = id => document.getElementById(`pm-${id}`);
    function listen(target, type, callback) { target.addEventListener(type, callback); removers.push(() => target.removeEventListener(type, callback)); }
    function cancelReads(prefix, includeMutations = false) {
        for (const [controller, group] of controllers) if (group.startsWith(prefix) && (includeMutations || !group.startsWith('mutation-'))) { controller.abort(); controllers.delete(controller); }
    }
    function message(panel, value, error) { el(`${panel}-message`).textContent = value; el(`${panel}-message`).classList.toggle('error', !!error); }
    function currentSession() {
        const current = window.localStorage.getItem('access_token') || '';
        if (current !== sessionToken) {
            sessionToken = current;
            cancelReads('', true);
            for (const timer of timers.values()) window.clearTimeout(timer);
            timers.clear();
            state.history = freshHistory(); state.consolidated = freshConsolidated();
            resetInputs(); renderHistory(); renderDetail(); renderConsolidated(); renderOrigins();
            for (const panel of ['history', 'consolidated']) message(panel, current ? 'Atualize para consultar as pesquisas desta sessão.' : 'Entre no JK Sistema para consultar as pesquisas.', !current);
        }
        return current;
    }
    async function request(path, params, group, method = 'GET') {
        const token = currentSession();
        if (!token) throw new Error('Entre no JK Sistema para consultar as pesquisas.');
        const query = new URLSearchParams();
        for (const [key, value] of Object.entries(params || {})) if (value != null && value !== '') query.set(key, text(value));
        const controller = new window.AbortController();
        controllers.set(controller, group);
        try {
            const response = await window.fetch(`/api/pesquisa-mercado${path}${query.size ? `?${query}` : ''}`, {
                method, headers: { Authorization: `Bearer ${token}` }, cache: 'no-store', signal: controller.signal
            });
            if (currentSession() !== token || state.disposed) throw new DOMException('Consulta encerrada.', 'AbortError');
            if (!response.ok) {
                const error = new Error(response.status === 401 || response.status === 403
                    ? 'A sessão do JK Sistema precisa ser renovada.' : method === 'DELETE'
                        ? response.status === 409 ? 'Cancele a pesquisa antes de apagar seu histórico.' : 'Não foi possível apagar a pesquisa. Tente novamente.'
                        : response.status === 409 ? 'As pesquisas foram atualizadas. A consulta foi reiniciada na primeira página.' : 'Não foi possível consultar as pesquisas salvas. Tente atualizar.');
                error.status = response.status;
                throw error;
            }
            const data = await response.json();
            if (currentSession() !== token || state.disposed) throw new DOMException('Consulta encerrada.', 'AbortError');
            return data;
        } finally { controllers.delete(controller); }
    }
    async function requestDiagnostic(base) {
        try { return await request(`${base}/diagnostico`, {}, 'history-detail'); }
        catch (error) {
            if (error.name === 'AbortError' || error.status === 401 || error.status === 403) throw error;
            return { scopePath: [], incompleteCategories: [], incompleteCategoriesTotal: 0, salesRecoveryPending: false, recovery: null, unavailable: true };
        }
    }
    const statusChip = status => `<span class="status-chip${['cancelled', 'failed', 'completed_partial', 'interrupted'].includes(status) ? ' warning' : ''}">${escaped(statuses[status] || status || '—')}</span>`;
    const scopeField = (panel, label) => `<div class="pm-saved-field"><label for="pm-${panel}-scope">${label}</label><select id="pm-${panel}-scope"><option value="">Todos os escopos</option></select><button type="button" class="pm-saved-more" id="pm-${panel}-scope-more" hidden>Carregar mais escopos</button></div>`;
    const pager = (prefix, label) => `<div class="pm-saved-pagination" aria-label="${label}"><button type="button" class="btn" id="pm-${prefix}-previous" disabled>Anterior</button><span id="pm-${prefix}-page">Página 1 de 1</span><button type="button" class="btn" id="pm-${prefix}-next" disabled>Próxima</button></div>`;
    historyRoot.className = consolidatedRoot.className = 'pm-saved';
    historyRoot.innerHTML = `<div class="pm-saved-heading"><div><h2>Histórico de pesquisas</h2><p class="pm-saved-note">Escolha uma pesquisa geral salva para consultar seus resultados.</p></div><button type="button" class="btn" id="pm-history-refresh">Atualizar</button></div>
        <div class="pm-saved-filters"><div class="pm-saved-field"><label for="pm-history-search">Buscar pesquisa</label><input type="search" id="pm-history-search" maxlength="200" placeholder="Categoria ou identificação da pesquisa"></div>${scopeField('history', 'Escopo da pesquisa')}
        <div class="pm-saved-field"><label for="pm-history-status">Situação da pesquisa</label><select id="pm-history-status"><option value="">Todas as situações</option>${Object.entries(statuses).filter(([key]) => !['queued', 'pausing', 'interrupted'].includes(key)).map(([key, value]) => `<option value="${key}">${value}</option>`).join('')}</select></div></div>
        <p class="pm-saved-message" id="pm-history-message" role="status"></p><div class="pm-saved-grid"><div><p class="pm-saved-note" id="pm-history-count">0 pesquisas</p><div class="pm-saved-list" id="pm-history-list"></div>${pager('history', 'Páginas do histórico')}</div>
        <section class="pm-saved-detail" aria-label="Resultado da pesquisa escolhida"><div class="pm-saved-detail-heading" id="pm-history-detail-heading"><span class="pm-saved-eyebrow">Consulta de pesquisa salva</span><h3>Selecione uma pesquisa</h3></div>
        <div class="pm-saved-detail-body" id="pm-history-detail-body" hidden><div class="pm-global-actions"><button type="button" class="btn" id="pm-history-refresh-names">Atualizar nomes das lojas</button><button type="button" class="btn" id="pm-history-names-browser" hidden>Mostrar navegador</button><button type="button" class="btn" id="pm-history-recover-incomplete" hidden>Tentar categorias incompletas novamente</button><button type="button" class="btn" id="pm-history-diagnostic-browser" hidden>Mostrar navegador para diagnóstico</button></div><p id="pm-history-diagnostic-message" class="pm-saved-note" hidden>Os resultados estão disponíveis. Atualize para consultar as pendências desta pesquisa.</p><p id="pm-history-recovery-progress" class="pm-saved-note" hidden></p><details id="pm-history-incomplete" class="pm-category-incomplete" hidden><summary id="pm-history-incomplete-summary">Categorias incompletas e motivos</summary><ul id="pm-history-incomplete-list"></ul></details><div class="pm-global-tabs" role="tablist" aria-label="Rankings da pesquisa salva"><button type="button" class="pm-global-tab" role="tab" aria-selected="true" aria-controls="pm-history-ranking-panel" id="pm-history-tab-geral">Ranking geral</button><button type="button" class="pm-global-tab" role="tab" aria-selected="false" aria-controls="pm-history-ranking-panel" tabindex="-1" id="pm-history-tab-vendedor">Ranking por vendedor</button></div>
        <div class="pm-saved-detail-filters"><input type="search" id="pm-history-detail-search" maxlength="200" aria-label="Buscar anúncios da pesquisa salva" placeholder="Título, MLB ou vendedor"><select id="pm-history-detail-category" aria-label="Escopo dos resultados"><option value="">Todos os escopos dos resultados</option></select><select id="pm-history-detail-seller" aria-label="Vendedor da pesquisa salva" hidden></select></div>
        <div id="pm-history-ranking-panel" role="tabpanel" aria-labelledby="pm-history-tab-geral"><p class="pm-saved-message" id="pm-history-detail-message" role="status"></p><div class="pm-saved-table"><table><thead><tr><th>Pos.</th><th>Anúncio</th><th>Vendedor</th><th>Preço</th><th>Vendas</th><th>Média mensal</th><th>Situação</th><th>Link</th></tr></thead><tbody id="pm-history-detail-rows"></tbody></table></div>${pager('history-detail', 'Páginas da pesquisa escolhida')}</div></div></section></div>
        <p class="pm-saved-note" style="margin-top:16px">Consultas manuais por vendedor feitas anteriormente não foram gravadas e não aparecem neste histórico.</p>`;
    consolidatedRoot.innerHTML = `<div class="pm-saved-heading"><div><h2>Consolidado de todas as pesquisas</h2><p class="pm-saved-note">Pesquisas gerais salvas para seu usuário e cliente. Mercado Livre Brasil.</p></div><button type="button" class="btn" id="pm-consolidated-refresh">Atualizar</button></div>
        <div class="pm-saved-stats" id="pm-consolidated-stats"></div><div class="pm-saved-rule">Cada anúncio aparece uma vez por vendedor, com sua observação mais recente. As vendas repetidas não são somadas.<br><span class="pm-saved-note">O filtro de escopo inclui anúncios encontrados nesse escopo e mostra sua observação mais recente entre todas as pesquisas. Ranking pela média mensal do Avant. Sem dados suficientes, o anúncio fica sem posição numérica.</span></div>
        <div class="pm-saved-filters"><div class="pm-saved-field"><label for="pm-consolidated-search">Buscar anúncio</label><input type="search" id="pm-consolidated-search" maxlength="200" placeholder="Título, MLB ou vendedor"></div>${scopeField('consolidated', 'Escopo da pesquisa de origem')}<div class="pm-saved-field"><label for="pm-consolidated-evidence">Dados do anúncio</label><select id="pm-consolidated-evidence"><option value="">Todos os anúncios</option><option value="rankable">Com dados para ranking</option><option value="pending">Sem dados suficientes</option></select></div></div>
        <p class="pm-saved-message" id="pm-consolidated-message" role="status"></p><section class="pm-saved-origin" id="pm-consolidated-origins" aria-label="Pesquisas de origem do anúncio" hidden><div class="pm-saved-heading"><h3 id="pm-consolidated-origin-title"></h3><button type="button" class="btn" id="pm-consolidated-origin-close">Fechar origens</button></div><p class="pm-saved-message" id="pm-consolidated-origin-message" role="status"></p><div class="pm-saved-list" id="pm-consolidated-origin-list"></div>${pager('consolidated-origin', 'Páginas das observações')}</section>
        <div class="pm-saved-table"><table><thead><tr><th>Pos.</th><th>Anúncio</th><th>Vendedor</th><th>Preço</th><th>Vendas</th><th>Média mensal</th><th>Última consulta</th><th>Pesquisas de origem</th><th>Situação</th><th>Link</th></tr></thead><tbody id="pm-consolidated-rows"></tbody></table></div>${pager('consolidated', 'Páginas do consolidado')}<p class="pm-saved-note" id="pm-consolidated-range" style="margin-top:12px"></p>`;
    function resetInputs() {
        for (const id of ['history-search', 'history-scope', 'history-status', 'history-detail-search', 'history-detail-category', 'consolidated-search', 'consolidated-scope', 'consolidated-evidence']) el(id).value = '';
    }
    function renderPager(prefix, offset, total, limit, busy) {
        el(`${prefix}-page`).textContent = `Página ${Math.floor(offset / limit) + 1} de ${Math.max(1, Math.ceil(total / limit))}`;
        el(`${prefix}-previous`).disabled = busy || offset === 0;
        el(`${prefix}-next`).disabled = busy || offset + limit >= total;
    }
    function renderScopes(panel) {
        const data = state[panel];
        const select = el(`${panel}-scope`);
        select.innerHTML = '<option value="">Todos os escopos</option>' + data.scopes.map(item => `<option value="${escaped(item.key)}">${escaped(item.nome)}</option>`).join('');
        select.value = data.scope;
        el(`${panel}-scope-more`).hidden = data.scopesOffset >= data.scopesTotal;
        el(`${panel}-scope-more`).disabled = data.busy || data.scopesBusy;
    }
    function replaceScopes(data, categories) {
        const selected = data.scopes.find(item => item.key === data.scope);
        data.scopes = categories.items || [];
        data.scopesOffset = data.scopes.length;
        data.scopesTotal = Number(categories.total) || 0;
        // Keep a selected scope from a later metadata page visible after refreshing page one.
        if (selected && !data.scopes.some(item => item.key === selected.key)) data.scopes.push(selected);
    }
    function renderHistory() {
        const focused = document.activeElement;
        const focusAttribute = focused && el('history-list').contains(focused) && ['data-consult-job', 'data-delete-job', 'data-confirm-delete-job', 'data-cancel-delete-job'].find(attribute => focused.hasAttribute(attribute));
        const focusJob = focusAttribute && focused.getAttribute(focusAttribute);
        const h = state.history;
        el('history-count').textContent = `${number(h.total)} pesquisas`;
        el('history-refresh').disabled = h.busy;
        renderScopes('history');
        el('history-list').innerHTML = h.items.map(job => {
            const terminal = ['completed', 'completed_partial', 'cancelled', 'failed'].includes(job.status);
            const available = canDelete(job);
            const deleting = h.deleteBusy === job.id;
            return `<article class="pm-saved-card${job.id === h.detailId ? ' selected' : ''}"><div class="pm-saved-card-top"><span>${escaped(date(job.created_at))}</span>${statusChip(job.status)}</div><h3>${escaped(scopeName(job))}</h3><div class="pm-saved-card-foot"><span>${number(job.counters && job.counters.sellers || 0)} vendedores · ${number(job.counters && job.counters.ads || 0)} anúncios</span><div class="pm-saved-card-actions"><button type="button" class="btn${job.id === h.detailId ? ' btn-primary' : ''}" data-consult-job="${escaped(job.id)}">${job.id === h.detailId ? 'Em consulta' : 'Consultar'}</button><button type="button" class="btn pm-saved-delete" data-delete-job="${escaped(job.id)}" aria-label="Apagar pesquisa ${escaped(scopeName(job))} de ${escaped(date(job.created_at))}"${available ? '' : ' disabled'}>${deleting ? 'Apagando…' : 'Apagar'}</button></div></div>${!terminal ? '<p class="pm-saved-note pm-saved-delete-note">Cancele a pesquisa antes de apagar.</p>' : ''}${h.deleteId === job.id ? `<div class="pm-saved-delete-confirm" role="group" aria-label="Confirmar exclusão da pesquisa"><p>Apagar esta pesquisa e seus resultados? Ela também será removida do consolidado. Esta ação não pode ser desfeita.</p><div class="pm-saved-card-actions"><button type="button" class="btn" data-cancel-delete-job="${escaped(job.id)}"${h.deleteBusy ? ' disabled' : ''}>Cancelar</button><button type="button" class="btn pm-saved-delete" data-confirm-delete-job="${escaped(job.id)}"${available ? '' : ' disabled'}>${deleting ? 'Apagando…' : 'Confirmar exclusão'}</button></div></div>` : ''}</article>`;
        }).join('');
        renderPager('history', h.offset, h.total, 25, h.busy);
        if (focusAttribute) {
            const next = Array.from(el('history-list').querySelectorAll(`[${focusAttribute}]`)).find(button => button.getAttribute(focusAttribute) === focusJob && !button.disabled)
                || el('history-list').querySelector('[data-consult-job]') || el('history-count');
            if (next === el('history-count')) next.tabIndex = -1;
            next.focus();
        }
    }
    function canDelete(job) {
        const h = state.history;
        return !!job && !h.deleteBusy && !h.busy && !h.namesBusy && !h.recoverBusy
            && ['completed', 'completed_partial', 'cancelled', 'failed'].includes(job.status)
            && (typeof window.__jkPesquisaMercadoCanDeleteJob !== 'function' || window.__jkPesquisaMercadoCanDeleteJob(job.id));
    }
    function focusDelete(jobId, attribute) {
        const button = Array.from(historyRoot.querySelectorAll(`[${attribute}]`)).find(button => button.getAttribute(attribute) === jobId);
        if (button) button.focus();
    }
    async function deleteJob(jobId) {
        currentSession();
        const h = state.history, job = h.items.find(item => item.id === jobId);
        if (state.disposed || h.deleteId !== jobId || !canDelete(job)) return;
        h.deleteBusy = jobId; window.dispatchEvent(new window.CustomEvent('jk-pesquisa-mercado-history-delete-change')); renderHistory(); renderDetail(); message('history', 'Apagando pesquisa…');
        try {
            let missing = false;
            try { await request(`/jobs/${encodeURIComponent(jobId)}`, {}, 'mutation-delete', 'DELETE'); }
            catch (error) { if (error.status !== 404) throw error; missing = true; }
            if (state.disposed || state.history !== h) return;
            cancelReads('history-list'); cancelReads('history-scope'); cancelReads('consolidated');
            for (const key of ['history', 'consolidated']) { window.clearTimeout(timers.get(key)); timers.delete(key); }
            h.generation += 1; h.busy = h.scopesBusy = false;
            h.items = h.items.filter(item => item.id !== jobId); h.total = Math.max(0, h.total - 1);
            h.offset = Math.min(h.offset, Math.max(0, Math.ceil(h.total / 25) - 1) * 25);
            h.deleteId = ''; h.revision = null; h.loaded = false;
            if (h.detailId === jobId) {
                cancelReads('history-detail'); window.clearTimeout(timers.get('detail')); timers.delete('detail'); h.detailGeneration += 1;
                h.detailId = ''; h.detail = null; h.detailSellers = []; h.detailCategories = []; h.detailItems = [];
                h.detailTotal = h.detailOffset = 0; h.detailBusy = h.detailNeedsRefresh = h.namesLogin = false;
                el('history-detail-search').value = ''; message('history-detail', '');
            }
            const c = state.consolidated;
            c.generation += 1; c.originGeneration += 1; c.revision = null; c.loaded = false;
            c.items = []; c.total = c.offset = 0; c.summary = c.origin = null; c.originItems = [];
            c.busy = c.originBusy = c.scopesBusy = false;
            renderHistory(); renderDetail(); renderConsolidated(); renderOrigins();
            window.dispatchEvent(new window.CustomEvent('jk-pesquisa-mercado-job-deleted', { detail: { jobId } }));
            if (state.mode === 'history') await loadHistory(false);
            else if (state.mode === 'consolidated') await loadConsolidated(true);
            if (!state.disposed && state.history === h) message('history', missing ? 'A pesquisa já não está disponível.' : 'Pesquisa apagada. Seus resultados foram removidos do consolidado.');
        } catch (error) {
            if (!state.disposed && state.history === h && error.name !== 'AbortError') message('history', error.message, true);
        } finally {
            h.deleteBusy = '';
            window.dispatchEvent(new window.CustomEvent('jk-pesquisa-mercado-history-delete-change'));
            if (!state.disposed && state.history === h) { renderHistory(); renderDetail(); }
        }
    }
    function rankCells(item) {
        const ranked = Number.isInteger(item.rank) && item.rank > 0;
        return `<td class="rank-cell">${ranked ? item.rank : ''}</td><td class="title-cell">${escaped(item.titulo || 'Anúncio sem título')}<small>${escaped(item.id || item.ad_key)}</small></td><td>${escaped(sellerLabel(item))}</td><td>${escaped(price(item.preco))}</td><td>${number(item.vendas)}</td><td>${number(item.media_vendas_mensal)}</td>`;
    }
    function evidenceCell(item) {
        const ranked = Number.isInteger(item.rank) && item.rank > 0;
        return `<td><span class="status-chip${ranked ? '' : ' warning'}">${ranked ? 'Dados Avant' : escaped(item.erro || 'Sem dados suficientes do Avant')}</span></td>`;
    }
    function linkCell(item) {
        const url = safeUrl(item.url);
        return `<td>${url ? `<a class="link" href="${escaped(url)}" target="_blank" rel="noopener noreferrer">Abrir</a>` : '—'}</td>`;
    }
    function renderDetail() {
        const h = state.history;
        el('history-detail-body').hidden = !h.detail;
        el('history-diagnostic-message').hidden = !h.detail || !h.detail.diagnosticUnavailable;
        const operationBusy = !!h.deleteBusy || h.namesBusy || h.recoverBusy || h.detailBusy || (typeof window.__jkPesquisaMercadoCanDeleteJob === 'function' && !window.__jkPesquisaMercadoCanDeleteJob());
        el('history-refresh-names').disabled = operationBusy || !h.detail || ['running', 'waiting_login', 'queued', 'pausing'].includes(h.detail.status);
        el('history-names-browser').hidden = !h.namesLogin;
        el('history-names-browser').disabled = h.namesBusy;
        el('history-recover-incomplete').hidden = !canRecover(h.detail);
        el('history-recover-incomplete').disabled = operationBusy || !canRecover(h.detail);
        el('history-recover-incomplete').textContent = incompleteCategories(h.detail).length || Number(h.detail && h.detail.incompleteCount) > 0
            ? 'Tentar categorias incompletas novamente' : 'Tentar dados pendentes novamente';
        el('history-diagnostic-browser').hidden = !h.detail || !['completed_partial', 'failed'].includes(h.detail.status);
        el('history-diagnostic-browser').disabled = operationBusy;
        const incomplete = incompleteCategories(h.detail), recovery = h.detail && h.detail.checkpoint && h.detail.checkpoint.recovery;
        el('history-incomplete').hidden = !incomplete.length;
        const incompleteTotal = Number.isSafeInteger(h.detail && h.detail.incompleteCount) ? h.detail.incompleteCount : incomplete.length;
        el('history-incomplete-summary').textContent = `${number(incompleteTotal)} categorias incompletas e motivos${incompleteTotal > incomplete.length ? ` (exibindo ${number(incomplete.length)})` : ''}`;
        el('history-incomplete-list').innerHTML = incomplete.map(item => `<li><strong>${escaped(typeof item === 'string' ? item : item && (item.nome || item.key) || 'Categoria')}</strong>: ${escaped(incompleteReason(item))}</li>`).join('');
        const recoveryTotal = recovery && (Array.isArray(recovery.categoryKeys) ? recovery.categoryKeys.length : recovery.total);
        el('history-recovery-progress').hidden = !Number.isSafeInteger(recoveryTotal);
        el('history-recovery-progress').textContent = Number.isSafeInteger(recoveryTotal) ? `Recuperação das categorias: ${number(Math.min(recoveryTotal, Math.max(0, Number(recovery.index) || 0)))} de ${number(recoveryTotal)} verificadas nesta tentativa.` : '';
        const heading = el('history-detail-heading');
        heading.innerHTML = `<span class="pm-saved-eyebrow">Consulta de pesquisa salva</span><h3>${h.detail ? escaped(scopeName(h.detail)) : h.detailBusy ? 'Carregando pesquisa…' : 'Selecione uma pesquisa'}</h3>${h.detail ? `<p class="pm-saved-note">${escaped(date(h.detail.created_at))} · ${escaped(statuses[h.detail.status] || h.detail.status)} · ${number(h.detail.counters && h.detail.counters.ads || 0)} anúncios</p>` : ''}`;
        for (const tab of ['geral', 'vendedor']) { el(`history-tab-${tab}`).setAttribute('aria-selected', text(tab === h.detailTab)); el(`history-tab-${tab}`).tabIndex = tab === h.detailTab ? 0 : -1; }
        el('history-ranking-panel').setAttribute('aria-labelledby', `pm-history-tab-${h.detailTab}`);
        el('history-detail-seller').hidden = h.detailTab !== 'vendedor';
        el('history-detail-seller').innerHTML = h.detailSellers.map(item => `<option value="${escaped(item.seller_key)}">${escaped(sellerLabel(item))}</option>`).join('');
        el('history-detail-seller').value = h.detailSeller;
        el('history-detail-category').innerHTML = '<option value="">Todos os escopos dos resultados</option>' + h.detailCategories.map(item => `<option value="${escaped(item.categoria)}">${escaped(item.categoria)}${item.status === 'incomplete' ? ' (incompleta)' : ''}</option>`).join('');
        el('history-detail-category').value = h.detailCategory;
        el('history-detail-rows').innerHTML = h.detailItems.map(item => `<tr>${rankCells(item)}${evidenceCell(item)}${linkCell(item)}</tr>`).join('');
        renderPager('history-detail', h.detailOffset, h.detailTotal, 100, h.detailBusy);
    }
    function observed(item) { return `${escaped(date(item.observed_at))}${item.date_approximate ? '<small>Data aproximada do registro legado</small>' : ''}`; }
    function renderConsolidated() {
        const c = state.consolidated, summary = c.summary || {};
        el('consolidated-stats').innerHTML = [['researches', 'Pesquisas', 'gerais salvas'], ['sellers', 'Vendedores', 'únicos'], ['unique_ads', 'Anúncios', 'sem duplicidade'], ['rankable', 'Com dados Avant', 'aptos ao ranking'], ['pending', 'Pendências', 'sem dados suficientes']].map(([key, label, note]) => `<article class="stat-card"><div class="stat-label">${label}</div><div class="stat-value">${number(summary[key] || 0)}</div><div class="stat-detail">${note}</div></article>`).join('');
        el('consolidated-refresh').disabled = c.busy;
        renderScopes('consolidated');
        el('consolidated-rows').innerHTML = c.items.map((item, index) => `<tr>${rankCells(item)}<td class="pm-saved-date">${observed(item)}</td><td><button type="button" class="pm-saved-source" data-origin-index="${index}">Ver ${number(item.research_count || item.observations || 1)} pesquisa(s)</button></td>${evidenceCell(item)}${linkCell(item)}</tr>`).join('');
        renderPager('consolidated', c.offset, c.total, 100, c.busy);
        el('consolidated-range').textContent = summary.first_research_at ? `Pesquisas salvas de ${date(summary.first_research_at)} a ${date(summary.last_research_at)}. ${number(c.total)} anúncios nos filtros atuais.` : 'Nenhuma pesquisa geral salva nesta sessão.';
    }
    function renderOrigins() {
        const c = state.consolidated;
        el('consolidated-origins').hidden = !c.origin;
        el('consolidated-origin-title').textContent = c.origin ? `Pesquisas de origem: ${c.origin.titulo || c.origin.id || c.origin.ad_key}` : '';
        el('consolidated-origin-list').innerHTML = c.originItems.map(item => `<article class="pm-saved-card"><div class="pm-saved-card-top"><span>${observed(item)}</span>${statusChip(item.status)}</div><h3>${escaped(scopeName(item))}</h3><div class="pm-saved-card-foot"><span>Vendas: ${number(item.vendas)} · Média mensal: ${number(item.media_vendas_mensal)}</span><button type="button" class="btn" data-consult-job="${escaped(item.source_job_id)}">Consultar pesquisa</button></div></article>`).join('');
        renderPager('consolidated-origin', c.originOffset, c.originTotal, 25, c.originBusy);
    }
    async function loadHistory(fresh, retried) {
        currentSession();
        const h = state.history;
        if (state.disposed || state.mode !== 'history') return;
        if (fresh) { h.offset = 0; h.revision = null; }
        const generation = ++h.generation;
        cancelReads('history-list'); cancelReads('history-scope');
        h.busy = true; h.scopesBusy = false; renderHistory(); message('history', 'Carregando pesquisas salvas…');
        try {
            const data = await request('/jobs', { q: h.query, status: h.status, scope_category: h.scope, offset: h.offset, limit: 25, revision: h.revision }, 'history-list');
            if (state.history !== h || h.generation !== generation || state.disposed) return;
            const categories = h.revision === data.owner_revision && h.loaded ? null : await request('/consolidado/categorias', { offset: 0, limit: 100, revision: data.owner_revision }, 'history-list');
            if (state.history !== h || h.generation !== generation || state.disposed) return;
            h.items = Array.isArray(data.items) ? data.items : [];
            if (!h.items.some(item => item.id === h.deleteId)) h.deleteId = '';
            h.total = Number(data.total) || 0; h.revision = data.owner_revision; h.loaded = true;
            if (categories) replaceScopes(h, categories);
            message('history', `${retried ? 'As pesquisas foram atualizadas; consulta reiniciada na primeira página. ' : ''}${h.items.length ? 'Resultados salvos. Consultar não inicia outra pesquisa.' : 'Nenhuma pesquisa corresponde aos filtros.'}`);
        } catch (error) {
            if (state.history !== h || h.generation !== generation || state.disposed || error.name === 'AbortError') return;
            if (error.status === 409 && !retried) return loadHistory(true, true);
            h.items = []; message('history', error.message, true);
        } finally { if (state.history === h && h.generation === generation && !state.disposed) { h.busy = false; renderHistory(); } }
    }
    async function loadScopes(panel) {
        const data = state[panel], generation = data.generation;
        if (data.busy || data.scopesBusy || data.revision == null || data.scopesOffset >= data.scopesTotal) return;
        data.scopesBusy = true; renderScopes(panel);
        try {
            const result = await request('/consolidado/categorias', { offset: data.scopesOffset, limit: 100, revision: data.revision }, `${panel}-scope`);
            if (state[panel] !== data || data.generation !== generation || state.disposed) return;
            const items = result.items || [], known = new Set(data.scopes.map(item => item.key));
            data.scopes.push(...items.filter(item => !known.has(item.key))); data.scopesOffset += items.length; data.scopesTotal = Number(result.total) || 0;
        } catch (error) {
            if (state[panel] !== data || data.generation !== generation || state.disposed || error.name === 'AbortError') return;
            if (error.status === 409) { if (panel === 'history') loadHistory(true); else loadConsolidated(true); }
            else message(panel, error.message, true);
        } finally { if (state[panel] === data && data.generation === generation && !state.disposed) { data.scopesBusy = false; renderScopes(panel); } }
    }
    async function openDetail(jobId) {
        if (!jobId || state.disposed) return;
        openingDetail = true;
        try {
            if (typeof window.__jkPesquisaMercadoChangeMode === 'function') window.__jkPesquisaMercadoChangeMode('history');
            else changeMode('history');
        } finally { openingDetail = false; }
        const h = state.history;
        h.detailId = jobId; h.namesLogin = false; h.detail = null; h.detailItems = []; h.detailTab = 'geral'; h.detailQuery = ''; h.detailCategory = ''; h.detailSeller = ''; h.detailOffset = 0;
        el('history-detail-search').value = '';
        const generation = ++h.detailGeneration;
        cancelReads('history-detail'); h.detailBusy = true; renderHistory(); renderDetail();
        try {
            const base = `/jobs/${encodeURIComponent(jobId)}`;
            const [result, diagnostic, sellers, categories] = await Promise.all([
                request(`${base}/resumo`, {}, 'history-detail'), requestDiagnostic(base),
                request(`${base}/vendedores`, {}, 'history-detail'), request(`${base}/categorias`, {}, 'history-detail')]);
            if (state.history !== h || h.detailGeneration !== generation || state.disposed) return;
            h.detail = { ...result.job, diagnosticUnavailable: diagnostic.unavailable === true, scopePath: diagnostic.scopePath, incompleteCount: diagnostic.incompleteCategoriesTotal, salesRecoveryPending: diagnostic.salesRecoveryPending === true, checkpoint: { incompleteCategories: diagnostic.incompleteCategories, recovery: diagnostic.recovery } };
            h.detailSellers = sellers.items || []; h.detailCategories = categories.items || []; h.detailSeller = h.detailSellers.length ? h.detailSellers[0].seller_key : '';
            await loadDetailRanking();
        } catch (error) {
            if (state.history === h && h.detailGeneration === generation && !state.disposed && error.name !== 'AbortError') { message('history', error.message, true); h.detailBusy = false; renderDetail(); }
        }
    }
    async function refreshDetail() {
        const h = state.history;
        if (!h.detailId || state.disposed || state.mode !== 'history') return;
        const generation = ++h.detailGeneration;
        cancelReads('history-detail'); h.detailBusy = true; renderDetail(); message('history-detail', 'Atualizando os resultados salvos…');
        try {
            const base = `/jobs/${encodeURIComponent(h.detailId)}`;
            const [result, diagnostic, sellers, categories] = await Promise.all([
                request(`${base}/resumo`, {}, 'history-detail'), requestDiagnostic(base),
                request(`${base}/vendedores`, {}, 'history-detail'), request(`${base}/categorias`, {}, 'history-detail')
            ]);
            if (state.history !== h || h.detailGeneration !== generation || state.disposed) return;
            h.detail = { ...result.job, diagnosticUnavailable: diagnostic.unavailable === true, scopePath: diagnostic.scopePath, incompleteCount: diagnostic.incompleteCategoriesTotal, salesRecoveryPending: diagnostic.salesRecoveryPending === true, checkpoint: { incompleteCategories: diagnostic.incompleteCategories, recovery: diagnostic.recovery } };
            const selectedSeller = h.detailSellers.find(item => item.seller_key === h.detailSeller);
            h.detailSellers = sellers.items || [];
            if (h.detailSeller && !h.detailSellers.some(item => item.seller_key === h.detailSeller)) h.detailSellers.push(selectedSeller || { seller_key: h.detailSeller, vendedor: h.detailSeller });
            if (!h.detailSeller && h.detailSellers.length) h.detailSeller = h.detailSellers[0].seller_key;
            h.detailCategories = categories.items || [];
            if (h.detailCategory && !h.detailCategories.some(item => item.categoria === h.detailCategory)) h.detailCategories.push({ categoria: h.detailCategory });
            await loadDetailRanking();
        } catch (error) {
            if (state.history === h && h.detailGeneration === generation && !state.disposed && error.name !== 'AbortError') {
                h.detailBusy = false; renderDetail(); message('history-detail', error.message, true);
            }
        }
    }
    async function loadDetailRanking() {
        const h = state.history;
        if (!h.detail || state.disposed) return;
        const generation = ++h.detailGeneration;
        cancelReads('history-detail'); h.detailBusy = true; h.detailItems = []; renderDetail(); message('history-detail', 'Carregando resultados salvos…');
        try {
            const result = h.detailTab === 'vendedor' && !h.detailSeller ? { items: [], total: 0 } : await request(`/jobs/${encodeURIComponent(h.detailId)}/ranking`, {
                offset: h.detailOffset, limit: 100, q: h.detailQuery, category: h.detailCategory,
                seller_key: h.detailTab === 'vendedor' ? h.detailSeller : ''
            }, 'history-detail');
            if (state.history !== h || h.detailGeneration !== generation || state.disposed) return;
            h.detailItems = result.items || []; h.detailTotal = Number(result.total) || 0;
            message('history-detail', h.detailItems.length ? `${number(h.detailTotal)} anúncio(s). Ranking pela média mensal do Avant.` : 'Nenhum anúncio disponível para estes filtros.');
        } catch (error) {
            if (state.history === h && h.detailGeneration === generation && !state.disposed && error.name !== 'AbortError') message('history-detail', error.message, true);
        } finally { if (state.history === h && h.detailGeneration === generation && !state.disposed) { h.detailBusy = false; renderDetail(); } }
    }
    async function loadConsolidated(fresh, retried) {
        currentSession();
        const c = state.consolidated;
        if (state.disposed || state.mode !== 'consolidated') return;
        if (fresh) { c.offset = 0; c.revision = null; }
        const generation = ++c.generation;
        cancelReads('consolidated'); c.origin = null; c.originItems = []; c.originGeneration += 1; renderOrigins();
        c.busy = true; c.scopesBusy = false; renderConsolidated(); message('consolidated', 'Carregando consolidado…');
        const filters = { q: c.query, scope_category: c.scope, evidence: c.evidence };
        try {
            const summary = await request('/consolidado/resumo', { ...filters, revision: c.revision }, 'consolidated');
            if (state.consolidated !== c || c.generation !== generation || state.disposed) return;
            const [ranking, categories] = await Promise.all([request('/consolidado/ranking', { ...filters, offset: c.offset, limit: 100, revision: summary.owner_revision }, 'consolidated'),
                c.revision === summary.owner_revision && c.loaded ? Promise.resolve(null) : request('/consolidado/categorias', { offset: 0, limit: 100, revision: summary.owner_revision }, 'consolidated')]);
            if (state.consolidated !== c || c.generation !== generation || state.disposed) return;
            c.summary = summary; c.items = ranking.items || []; c.total = Number(ranking.total) || 0; c.revision = summary.owner_revision; c.loaded = true;
            if (categories) replaceScopes(c, categories);
            message('consolidated', `${retried ? 'As pesquisas foram atualizadas; consulta reiniciada na primeira página. ' : ''}${c.items.length ? `${number(c.total)} anúncios. Consulte as origens para ver cada observação salva.` : 'Nenhum anúncio corresponde aos filtros.'}`);
        } catch (error) {
            if (state.consolidated !== c || c.generation !== generation || state.disposed || error.name === 'AbortError') return;
            if (error.status === 409 && !retried) return loadConsolidated(true, true);
            c.items = []; message('consolidated', error.message, true);
        } finally { if (state.consolidated === c && c.generation === generation && !state.disposed) { c.busy = false; renderConsolidated(); } }
    }
    async function loadOrigins(item, offset) {
        const c = state.consolidated;
        if (!item || state.disposed) return;
        const generation = ++c.originGeneration;
        cancelReads('consolidated-origin'); c.origin = item; c.originOffset = offset || 0; c.originItems = []; c.originBusy = true; renderOrigins(); message('consolidated-origin', 'Carregando observações…');
        try {
            const result = await request('/consolidado/origens', { ad_key: item.ad_key, seller_key: item.seller_key, offset: c.originOffset, limit: 25, revision: c.revision }, 'consolidated-origin');
            if (state.consolidated !== c || c.originGeneration !== generation || state.disposed) return;
            c.originItems = result.items || []; c.originTotal = Number(result.total) || 0;
            message('consolidated-origin', c.originItems.length ? 'Cada linha é uma observação salva. As quantidades não são somadas.' : 'Nenhuma observação disponível.');
        } catch (error) {
            if (state.consolidated !== c || c.originGeneration !== generation || state.disposed || error.name === 'AbortError') return;
            if (error.status === 409) return loadConsolidated(true);
            message('consolidated-origin', error.message, true);
        } finally { if (state.consolidated === c && c.originGeneration === generation && !state.disposed) { c.originBusy = false; renderOrigins(); } }
    }
    function debounce(key, callback) {
        window.clearTimeout(timers.get(key));
        timers.set(key, window.setTimeout(() => { timers.delete(key); if (!state.disposed) callback(); }, 250));
    }
    function filtersChanged(panel) {
        const data = state[panel];
        data.query = el(`${panel}-search`).value.trim(); data.scope = el(`${panel}-scope`).value;
        if (panel === 'history') data.status = el('history-status').value; else data.evidence = el('consolidated-evidence').value;
        data.offset = 0; data.generation += 1; cancelReads(panel === 'history' ? 'history-list' : 'consolidated');
        debounce(panel, () => panel === 'history' ? loadHistory(false) : loadConsolidated(false));
    }
    function detailChanged() {
        const h = state.history;
        h.detailQuery = el('history-detail-search').value.trim(); h.detailCategory = el('history-detail-category').value; h.detailSeller = el('history-detail-seller').value;
        h.detailOffset = 0; h.detailGeneration += 1; cancelReads('history-detail'); debounce('detail', loadDetailRanking);
    }
    function changeMode(mode) {
        currentSession();
        const entering = state.mode !== mode;
        if (entering) {
            if (state.history.busy || timers.has('history')) state.history.loaded = false;
            if (state.consolidated.busy || timers.has('consolidated')) state.consolidated.loaded = false;
            if (state.history.detailBusy || timers.has('detail')) state.history.detailNeedsRefresh = true;
            if (state.consolidated.originBusy) state.consolidated.origin = null;
            for (const timer of timers.values()) window.clearTimeout(timer);
            timers.clear();
            cancelReads(''); state.history.generation += 1; state.history.detailGeneration += 1;
            state.consolidated.generation += 1; state.consolidated.originGeneration += 1;
            state.history.busy = state.history.detailBusy = state.history.scopesBusy = false;
            state.consolidated.busy = state.consolidated.originBusy = state.consolidated.scopesBusy = false;
        }
        state.mode = mode;
        renderHistory(); renderDetail(); renderConsolidated(); renderOrigins();
        if (mode === 'history' && entering) loadHistory(false);
        if (mode === 'consolidated' && entering) loadConsolidated(false);
        if (mode === 'history' && entering && state.history.detailId && !openingDetail) {
            state.history.detailNeedsRefresh = false;
            refreshDetail();
        } else if (mode === 'history' && state.history.detailNeedsRefresh && !openingDetail) {
            state.history.detailNeedsRefresh = false;
            if (state.history.detail) loadDetailRanking();
            else if (state.history.detailId) openDetail(state.history.detailId);
        }
    }
    for (const panel of ['history', 'consolidated']) {
        listen(el(`${panel}-refresh`), 'click', () => {
            if (panel !== 'history') { loadConsolidated(true); return; }
            loadHistory(true);
            if (state.history.detailId && !state.history.namesBusy && !state.history.recoverBusy && !state.history.deleteBusy) refreshDetail();
        });
        listen(el(`${panel}-search`), 'input', () => filtersChanged(panel));
        listen(el(`${panel}-scope`), 'change', () => filtersChanged(panel));
        listen(el(`${panel}-scope-more`), 'click', () => loadScopes(panel));
        for (const [direction, name] of [[-1, 'previous'], [1, 'next']]) listen(el(`${panel}-${name}`), 'click', () => {
            const data = state[panel]; if (data.busy) return;
            data.offset = Math.max(0, data.offset + direction * (panel === 'history' ? 25 : 100));
            if (panel === 'history') loadHistory(false); else loadConsolidated(false);
        });
    }
    listen(el('history-refresh-names'), 'click', async () => {
        const h = state.history, jobId = h.detailId;
        if (!jobId || h.deleteBusy || h.namesBusy || !h.detail || ['running', 'waiting_login'].includes(h.detail.status)) return;
        h.namesBusy = true; renderHistory(); renderDetail(); message('history-detail', 'Consultando os nomes das lojas…');
        try {
            if (typeof window.__jkPesquisaMercadoRefreshNames !== 'function') throw new Error('Abra a pesquisa no aplicativo JK Sistema.');
            const result = await window.__jkPesquisaMercadoRefreshNames(jobId);
            if (state.disposed || state.history !== h || h.detailId !== jobId) return;
            h.namesLogin = !!result.requiresLogin;
            await refreshDetail();
            if (state.history === h && h.detailId === jobId && !state.disposed) message('history-detail', result.message || `${number(result.updated || 0)} nomes atualizados. ${number(result.pending || 0)} nomes pendentes.`, !!result.requiresLogin);
        } catch (error) { if (!state.disposed && state.history === h && h.detailId === jobId) message('history-detail', error.message, true); }
        finally { h.namesBusy = false; if (!state.disposed) { renderHistory(); renderDetail(); } }
    });
    listen(el('history-names-browser'), 'click', async () => {
        try { await window.__jkPesquisaMercadoNamesBrowser(state.history.detailId); }
        catch (error) { message('history-detail', error.message, true); }
    });
    listen(el('history-recover-incomplete'), 'click', async () => {
        currentSession();
        const h = state.history, jobId = h.detailId, revision = h.detail && h.detail.revision;
        if (!jobId || h.deleteBusy || h.namesBusy || h.recoverBusy || h.detailBusy || !canRecover(h.detail)) return;
        h.recoverBusy = true; renderHistory(); renderDetail(); message('history-detail', 'Preparando uma nova tentativa para as categorias incompletas…');
        try {
            if (typeof window.__jkPesquisaMercadoRecoverIncomplete !== 'function') throw new Error('Abra a pesquisa no aplicativo JK Sistema.');
            const result = await window.__jkPesquisaMercadoRecoverIncomplete(jobId, revision);
            if (state.disposed || state.history !== h || h.detailId !== jobId) return;
            if (result && result.job && result.job.id === jobId) h.detail = result.job;
            h.loaded = state.consolidated.loaded = false; h.revision = state.consolidated.revision = null;
            await refreshDetail();
            if (state.history === h && h.detailId === jobId && !state.disposed) message('history-detail', 'A recuperação foi iniciada. Acompanhe o progresso na pesquisa geral.');
        } catch (error) { if (!state.disposed && state.history === h && h.detailId === jobId) message('history-detail', error.message, true); }
        finally { h.recoverBusy = false; if (!state.disposed) { renderHistory(); renderDetail(); } }
    });
    listen(el('history-diagnostic-browser'), 'click', async () => {
        const jobId = state.history.detailId;
        if (!jobId || state.history.deleteBusy || state.history.recoverBusy) return;
        try {
            if (typeof window.__jkPesquisaMercadoDiagnosticBrowser !== 'function') throw new Error('Abra a pesquisa no aplicativo JK Sistema.');
            await window.__jkPesquisaMercadoDiagnosticBrowser(jobId);
        } catch (error) { if (state.history.detailId === jobId) message('history-detail', error.message, true); }
    });
    listen(window, 'jk-pesquisa-mercado-names-updated', event => {
        state.history.loaded = state.consolidated.loaded = false;
        state.history.revision = state.consolidated.revision = null;
        if (state.mode === 'history' && !state.history.namesBusy && state.history.detailId === event.detail.jobId) refreshDetail();
        if (state.mode === 'consolidated') loadConsolidated(true);
    });
    listen(el('history-status'), 'change', () => filtersChanged('history'));
    listen(el('consolidated-evidence'), 'change', () => filtersChanged('consolidated'));
    listen(window, 'jk-pesquisa-mercado-operation-change', () => { renderHistory(); renderDetail(); });
    listen(historyRoot, 'click', event => {
        const action = event.target.closest('[data-delete-job], [data-confirm-delete-job], [data-cancel-delete-job]');
        if (!action || !historyRoot.contains(action) || action.disabled) return;
        currentSession();
        const h = state.history;
        if (action.hasAttribute('data-confirm-delete-job')) { deleteJob(action.dataset.confirmDeleteJob); return; }
        if (action.hasAttribute('data-cancel-delete-job')) {
            if (h.deleteBusy) return;
            h.deleteId = ''; renderHistory(); focusDelete(action.dataset.cancelDeleteJob, 'data-delete-job'); return;
        }
        const jobId = action.dataset.deleteJob;
        if (!canDelete(h.items.find(item => item.id === jobId))) return;
        h.deleteId = jobId; message('history', 'Confirme abaixo para apagar a pesquisa.'); renderHistory(); focusDelete(jobId, 'data-cancel-delete-job');
    });
    for (const root of [historyRoot, consolidatedRoot]) listen(root, 'click', event => {
        const consult = event.target.closest('[data-consult-job]');
        if (consult && root.contains(consult)) openDetail(consult.dataset.consultJob);
        const origin = event.target.closest('[data-origin-index]');
        if (origin && root.contains(origin)) loadOrigins(state.consolidated.items[Number(origin.dataset.originIndex)], 0);
    });
    for (const tab of ['geral', 'vendedor']) {
        const change = value => { state.history.detailTab = value; state.history.detailOffset = 0; loadDetailRanking(); };
        listen(el(`history-tab-${tab}`), 'click', () => change(tab));
        listen(el(`history-tab-${tab}`), 'keydown', event => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault(); const next = event.key === 'Home' ? 'geral' : event.key === 'End' ? 'vendedor' : tab === 'geral' ? 'vendedor' : 'geral';
            change(next); el(`history-tab-${next}`).focus();
        });
    }
    listen(el('history-detail-search'), 'input', detailChanged);
    for (const id of ['history-detail-category', 'history-detail-seller']) listen(el(id), 'change', detailChanged);
    for (const [direction, name] of [[-1, 'previous'], [1, 'next']]) {
        listen(el(`history-detail-${name}`), 'click', () => { const h = state.history; if (h.detailBusy) return; h.detailOffset = Math.max(0, h.detailOffset + direction * 100); loadDetailRanking(); });
        listen(el(`consolidated-origin-${name}`), 'click', () => { const c = state.consolidated; if (!c.originBusy) loadOrigins(c.origin, Math.max(0, c.originOffset + direction * 25)); });
    }
    listen(el('consolidated-origin-close'), 'click', () => { cancelReads('consolidated-origin'); state.consolidated.originGeneration += 1; state.consolidated.origin = null; renderOrigins(); });
    listen(window, 'jk-pesquisa-mercado-mode-change', event => changeMode(event.detail && event.detail.mode));
    listen(window, 'storage', event => { if (event.key === 'access_token' || event.key === null) { currentSession(); if (sessionToken) { if (state.mode === 'history') loadHistory(true); else if (state.mode === 'consolidated') loadConsolidated(true); } } });
    function dispose() {
        state.disposed = true; cancelReads('', true); for (const timer of timers.values()) window.clearTimeout(timer); timers.clear();
        for (const remove of removers) remove();
    }
    listen(window, 'beforeunload', dispose);
    renderHistory(); renderDetail(); renderConsolidated(); renderOrigins();
    return { state, changeMode, openDetail, dispose, refresh: () => state.mode === 'history' ? loadHistory(true) : loadConsolidated(true) };
});
