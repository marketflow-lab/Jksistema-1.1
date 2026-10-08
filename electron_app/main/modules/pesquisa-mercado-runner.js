'use strict';

// A page only observes this coordinator. Its lifetime belongs to Electron main.
const ACTIVE = new Set(['running', 'paused', 'waiting_login', 'failed']);
const ROOT = '/api/pesquisa-mercado/jobs';
const clone = value => JSON.parse(JSON.stringify(value));
const { OfficialCategoryNavigation, unsupportedCategoryAlias } = require('./pesquisa-mercado-category-navigation');

function allowedUrl(value) {
    try {
        const url = new URL(String(value || ''));
        return url.protocol === 'https:' && !url.username && !url.password && (!url.port || url.port === '443')
            && /(^|\.)mercadolivre\.com\.br$/i.test(url.hostname);
    } catch (_err) { return false; }
}

function canonicalUrl(value) {
    if (!allowedUrl(value)) return '';
    const url = new URL(value);
    url.hash = '';
    for (const key of [...url.searchParams.keys()]) {
        if (/^(?:tracking|utm_|matt_|position|type|search_layout)/i.test(key)) url.searchParams.delete(key);
    }
    return url.href;
}

function normalizeCategory(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    if (Object.keys(value).some(key => !['key', 'nome', 'url'].includes(key))) return null;
    const url = canonicalUrl(value.url);
    if (!url) return null;
    const parsed = new URL(url);
    const listingHost = ['lista.mercadolivre.com.br', 'imoveis.mercadolivre.com.br', 'servicos.mercadolivre.com.br'].includes(parsed.hostname);
    if (!listingHost && !['www.mercadolivre.com.br', 'mercadolivre.com.br'].includes(parsed.hostname)) return null;
    let path;
    try { path = decodeURIComponent(parsed.pathname); } catch (_error) { return null; }
    if (/\/(?:p|up|perfil|loja|stores?|vendedor)(?:\/|$)|\/MLB-\d+|_CustId_|_SellerId_/i.test(path)) return null;
    if ([...parsed.searchParams.keys()].some(key => /^(?:seller|sellerid|custid|customerid|user|userid|wid|itemid|productid|catalogproductid)$/i.test(key.replace(/[_-]/g, '')) || /token|password|senha|secret|authorization|api[_-]?key|cookie/i.test(key))) return null;
    if ([...parsed.searchParams.values()].some(value => /_CustId_|_SellerId_|(?:^|[|,;])(?:item_id|catalog_product_id|seller[_-]?id|cust[_-]?id):/i.test(value))) return null;
    if (listingHost ? !path.replace(/\//g, '') : !/^\/(?:c|categorias)\/[^/]+/i.test(parsed.pathname)) return null;
    const key = String(value.key || '').trim();
    const nome = String(value.nome || '').replace(/\s+/g, ' ').trim();
    // Keep the pathname discovered in navigation: ML can interpret a listing
    // URL without its trailing slash as a generic search outside that category.
    return key && key.length <= 500 && nome && nome.length <= 400 && url.length <= 4000 ? { key, nome, url: parsed.href } : null;
}

function sameCategoryPage(expected, actual) {
    if (!allowedUrl(expected) || !allowedUrl(actual)) return false;
    const identity = value => {
        const parsed = new URL(value);
        const path = decodeURIComponent(parsed.pathname)
            .replace(/(?:\/)?_(?:Desde_\d+|NoIndex_(?:True|False)|OrderId_[A-Za-z0-9]+|DisplayType_[A-Za-z0-9]+)/gi, '')
            .replace(/\/+$/, '').toLowerCase();
        const categories = [...parsed.searchParams.entries()].filter(([key]) => /^(?:category|category_id|categoryid|categories)$/i.test(key))
            .map(([key, value]) => [key.toLowerCase(), value]).sort();
        return JSON.stringify([parsed.hostname.replace(/^www\./, ''), path, categories]);
    };
    try { return identity(expected) === identity(actual); } catch (_error) { return false; }
}

function categoryIdFromUrl(value) {
    if (!allowedUrl(value)) return '';
    try {
        const parsed = new URL(value);
        const ids = new Set();
        const pattern = /_(?:CatId|CATEGORY)_(MLB\d+)(?=_|\/|$)/gi;
        for (const match of decodeURIComponent(parsed.pathname).matchAll(pattern)) ids.add(match[1].toUpperCase());
        for (const [key, id] of parsed.searchParams) {
            if (/^(?:category|category_id|categoryid)$/i.test(key) && /^MLB\d+$/i.test(id)) ids.add(id.toUpperCase());
        }
        return ids.size === 1 ? [...ids][0] : '';
    } catch (_error) { return ''; }
}

function verifiedCategoryPage(expected, actual, categoryId = '', effectiveCategoryId = '') {
    if (!allowedUrl(expected) || !allowedUrl(actual)) return false;
    if (!categoryId) return sameCategoryPage(expected, actual);
    if (!/^MLB\d+$/.test(categoryId) || !normalizeCategory({ key: categoryId, nome: 'Categoria', url: actual })) return false;
    const urlId = categoryIdFromUrl(actual);
    const pageId = /^MLB\d+$/.test(effectiveCategoryId) ? effectiveCategoryId : '';
    if (urlId && urlId !== categoryId || pageId && pageId !== categoryId) return false;
    // A canonical permalink keeps its exact scope. An alias redirect needs
    // the selected ID in the effective document, never just a similar name.
    return sameCategoryPage(expected, actual) || (urlId || pageId) === categoryId;
}

// Navigation may still expose the previous document or a loading shell. Never
// consume that tree, and give the requested document time to replace it.
function categoryPageWaiter(expected, categoryId = '') {
    let lastUrl = '', stableReads = 0;
    return {
        actualUrl: '', mismatch: false,
        observe(actualUrl, ready, loading, effectiveCategoryId = '', identityConflict = false) {
            this.actualUrl = actualUrl;
            this.mismatch = !!expected && (identityConflict || !verifiedCategoryPage(expected, actualUrl, categoryId, effectiveCategoryId));
            if (this.mismatch || !ready || loading) {
                lastUrl = ''; stableReads = 0;
                return false;
            }
            const current = canonicalUrl(actualUrl);
            if (!current) return false;
            stableReads = lastUrl === current ? stableReads + 1 : 1;
            lastUrl = current;
            return stableReads >= 2;
        }
    };
}

function currentPageRequiresLogin(page, browser) {
    if (!page.authRequired && !page.avantAuthRequired) return false;
    if (page.readyState === 'loading' || typeof browser.isLoading === 'function' && browser.isLoading()) return false;
    // A login redirect is allowed, but its document must be the one currently
    // displayed. Avant pages have no ML URL in the parser and use getURL().
    if (!page.url) {
        try {
            const current = new URL(browser.getURL());
            return current.protocol === 'https:' && !current.username && !current.password && (!current.port || current.port === '443')
                && (current.hostname === 'avantprocloud.com.br' || current.hostname.endsWith('.avantprocloud.com.br'));
        } catch (_error) { return false; }
    }
    const documentUrl = value => {
        if (!allowedUrl(value)) return '';
        const parsed = new URL(value);
        // Match the parser's URL cleanup without removing pagination. The
        // previous page belongs to the same category but is another document.
        const fragment = parsed.hash.replace(/^#/, '');
        let wid = new URLSearchParams(fragment).get('wid');
        if (!wid) {
            try { wid = new URLSearchParams(decodeURIComponent(fragment)).get('wid'); } catch (_error) {}
        }
        if (!parsed.searchParams.has('wid') && /^MLB\d{6,}$/i.test(wid || '')) parsed.searchParams.set('wid', wid.toUpperCase());
        for (const key of ['polycard_client', 'sid', 'reco_id', 'searchVariation']) parsed.searchParams.delete(key);
        const normalized = new URL(canonicalUrl(parsed.href));
        normalized.searchParams.sort();
        return normalized.href;
    };
    const observed = documentUrl(page.url);
    return !!observed && observed === documentUrl(browser.getURL());
}

function requiresAvantLogin(result) {
    return !!result && (result.reason === 'avant_login_required'
        || result.avantAuthRequired === true && result.authRequired !== true);
}

function cleanSellerName(value, id = '') {
    if (typeof value !== 'string' || /[\u0000-\u001f\u007f]/.test(value)) return '';
    const name = value.replace(/\s+/g, ' ').trim().slice(0, 200);
    const normalized = name.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
    if (!name || /^(?:id:)?[\d\s.,_-]+$/.test(normalized)
        || /^(?:nome (?:pendente|nao identificado)|vendedor (?:nao identificado|desconhecido))\b/.test(normalized)
        || /^(?:(?:ir para|visitar|acessar|ver|conhecer) (?:a |o )?)?(?:loja(?: oficial)?|perfil(?: do vendedor)?|vendedor|todos os (?:produtos|anuncios))$/.test(normalized)
        || /^(?:ver|veja) (?:todos os |mais )?(?:produtos|anuncios)$/.test(normalized)
        || /^(?:seller|comprar|saiba mais)$/.test(normalized)) return '';
    return name;
}

function sellerIdentity(value) {
    const ids = new Set(), profileIds = new Set();
    const declared = String(value.id || value.sellerId || value.seller_id || '').trim();
    if (/^\d+$/.test(declared)) ids.add(declared);
    const url = canonicalUrl(value.perfilUrl || value.seller_perfil_url || value.sellerUrl);
    let profile = '';
    if (url) {
        const parsed = new URL(url);
        for (const match of parsed.pathname.matchAll(/_(?:CustId|SellerId)_(\d+)/gi)) profileIds.add(match[1]);
        for (const [key, id] of parsed.searchParams) if (/^(?:seller_id|cust_?id)$/i.test(key) && /^\d+$/.test(id)) profileIds.add(id);
        for (const id of profileIds) ids.add(id);
        try {
            if (profileIds.size === 1) profile = `id:${[...profileIds][0]}`;
            else if (/^\/loja\//i.test(parsed.pathname)) profile = `store:${decodeURIComponent(parsed.pathname).replace(/\/+$/, '').toLowerCase()}`;
            else if (/^\/perfil\//i.test(parsed.pathname)) profile = `profile:${decodeURIComponent(parsed.pathname).replace(/\/+$/, '').toLowerCase()}`;
        } catch (_error) { return { id: '', profile: '', conflict: true }; }
    }
    return { id: ids.size === 1 ? [...ids][0] : '', profile, conflict: ids.size > 1 };
}

function sameSeller(expected, observed) {
    if (expected.seller_identity_conflict || observed.seller_identity_conflict) return false;
    const left = sellerIdentity(expected), right = sellerIdentity(observed);
    if (left.conflict || right.conflict) return false;
    if (left.profile && right.profile && !left.profile.startsWith('id:') && !right.profile.startsWith('id:') && left.profile !== right.profile) return false;
    if (left.id && right.id) return left.id === right.id;
    return !!(left.profile && right.profile && left.profile === right.profile);
}

function enrichSellerName(seller, anuncios) {
    const current = cleanSellerName(seller.nome, seller.id);
    if (current) return { ...seller, nome: current };
    const names = new Map();
    for (const item of anuncios || []) {
        const name = cleanSellerName(item.vendedor, seller.id);
        if (!name || item.seller_confirmed !== true || !sameSeller(seller, item)) continue;
        if ((item.avant_seller_id || item.avant_seller_perfil_url) && !sameSeller(seller, {
            id: item.avant_seller_id, perfilUrl: item.avant_seller_perfil_url
        })) continue;
        const kind = item.seller_name_kind === 'store_brand' ? 'store_brand' : 'seller';
        names.set(name.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase(), {
            nome: name, seller_name_kind: kind, seller_name_source: item.seller_name_source === 'perfil' ? 'perfil' : 'avant',
            seller_name_id: item.avant_seller_id || item.seller_id || '',
            seller_name_profile_url: item.avant_seller_perfil_url || item.seller_perfil_url || ''
        });
    }
    return names.size === 1 ? { ...seller, ...names.values().next().value } : { ...seller, nome: '' };
}

function sellerFromPage(page) {
    if (page.seller_identity_conflict || sellerIdentity(page).conflict) return null;
    const profile = canonicalUrl(page.perfilUrl || page.sellerUrl);
    const match = profile.match(/(?:_CustId_|[?&]seller_id=)(\d+)/i);
    const id = String(page.sellerId || page.seller_id || match && match[1] || '').trim();
    const key = id ? `id:${id}` : profile ? `perfil:${profile}` : '';
    return key ? { key, id, nome: cleanSellerName(page.vendedor || page.sellerName, id), perfilUrl: profile,
        seller_name_kind: page.seller_name_kind === 'store_brand' || /\/loja\//.test(profile) ? 'store_brand' : 'seller',
        seller_name_source: 'perfil' } : null;
}

const PROGRESS_PHASES = new Set(['preparing', 'categories', 'discovery', 'seller', 'collecting',
    'pagination', 'finalizing', 'completed', 'completed_partial', 'paused', 'cancelled', 'waiting_login', 'failed']);

function progressState(value) {
    const source = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    const text = value => typeof value === 'string' ? value.replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 600) : '';
    return { phase: PROGRESS_PHASES.has(source.phase) ? source.phase : '', detail: text(source.detail), warning: text(source.warning) };
}

function initialCheckpoint(saved) {
    const checkpoint = {
        version: 1, phase: 'categories', categories: [], categoryIndex: 0,
        currentCategory: null, page: null, categorySeenPages: [], seenSeedIds: [],
        seenSellerKeys: [], incompleteCategories: [], discoveryInitialized: false,
        ...clone(saved || {})
    };
    checkpoint.progress = progressState(checkpoint.progress);
    if (!checkpoint.progress.warning) {
        const previous = Array.isArray(checkpoint.incompleteCategories) ? checkpoint.incompleteCategories.at(-1) : null;
        checkpoint.progress.warning = progressState({ warning: previous && previous.motivo }).warning;
    }
    return checkpoint;
}

function validateCheckpointTaxonomy(checkpoint) {
    const tree = checkpoint.officialTaxonomy;
    if (!tree) return;
    const fail = () => { throw new Error('A arvore preservada da pesquisa esta incompleta. Inicie uma nova pesquisa.'); };
    const scope = normalizeCategory(checkpoint.scope && checkpoint.scope.category);
    if (!scope || !/^MLB\d+$/.test(scope.key) || !tree || typeof tree !== 'object' || Array.isArray(tree)
        || Object.keys(tree).some(key => !['updatedAt','children','ancestry'].includes(key))
        || !Number.isFinite(Date.parse(tree.updatedAt)) || !tree.children || typeof tree.children !== 'object' || Array.isArray(tree.children)) fail();
    if (tree.ancestry !== undefined) {
        if (!Array.isArray(tree.ancestry) || !tree.ancestry.length || tree.ancestry.length > 32) fail();
        const seen = new Set();
        for (const raw of tree.ancestry) {
            const node = normalizeCategory(raw);
            if (!node || !/^MLB\d+$/.test(node.key) || node.url !== raw.url || seen.has(node.key)
                || node.key !== scope.key && Object.prototype.hasOwnProperty.call(tree.children, node.key)) fail();
            seen.add(node.key);
        }
        const last = tree.ancestry.at(-1);
        if (last.key !== scope.key || last.nome !== scope.nome || last.url !== scope.url) fail();
    }
    const keys = Object.keys(tree.children);
    if (!keys.length || keys.length > 100000 || !Object.prototype.hasOwnProperty.call(tree.children,scope.key)) fail();
    const records = new Map([[scope.key,scope]]), parents = new Map();
    for (const key of keys) {
        if (!/^MLB\d+$/.test(key) || !Array.isArray(tree.children[key])) fail();
        for (const raw of tree.children[key]) {
            const category = normalizeCategory(raw);
            if (!category || !/^MLB\d+$/.test(category.key) || category.url !== raw.url
                || !Object.prototype.hasOwnProperty.call(tree.children,category.key) || parents.has(category.key) || category.key === scope.key) fail();
            parents.set(category.key,key); records.set(category.key,category);
        }
    }
    const reachable = new Set(), pending = [scope.key];
    for(let index=0;index<pending.length;index+=1) {
        const key=pending[index]; if(reachable.has(key)) fail(); reachable.add(key);
        pending.push(...tree.children[key].map(child=>child.key));
    }
    if(reachable.size!==keys.length) fail();
    const inScope = raw => {
        const category=normalizeCategory(raw), known=category && records.get(category.key);
        return known && category.nome===known.nome && category.url===known.url;
    };
    if(!Array.isArray(checkpoint.categories) || checkpoint.categories.some(raw=>!inScope(raw))
        || checkpoint.currentCategory && !inScope(checkpoint.currentCategory)) fail();
    if(checkpoint.page && (!checkpoint.currentCategory || checkpoint.page.categoryKey!==checkpoint.currentCategory.key
        || !normalizeCategory({key:checkpoint.currentCategory.key,nome:checkpoint.currentCategory.nome,url:checkpoint.page.url}))) fail();
}

class PesquisaMercadoRunner {
    constructor(options) {
        this.api = options.api;
        this.browser = options.browser;
        this.scripts = options.pageScripts;
        this.collector = options.collector;
        this.taxonomy = options.taxonomy || null;
        this.emit = options.emit || (() => {});
        this.wait = options.wait || (ms => new Promise(resolve => setTimeout(resolve, ms)));
        this.job = null;
        this.checkpoint = null;
        this.task = null;
        this.controller = null;
        this.stopStatus = '';
        this.saveQueue = Promise.resolve();
        this.namesTask = null;
        this.namesController = null;
        this.namesJobId = '';
        this.officialNavigation = null;
        this.namesBudgetMs = Math.max(1, options.namesBudgetMs || 120000);
        this.namesSellerTimeoutMs = Math.max(1, options.namesSellerTimeoutMs || 18000);
    }

    snapshot() {
        if (!this.job) return { job: null };
        // No authentication context or raw checkpoint is sent as a progress event.
        const { id, status, revision, counters, message, created_at, updated_at } = this.job;
        return { job: { id, status, revision, counters, message, created_at, updated_at,
            scopeCategory: this.checkpoint && this.checkpoint.scope && this.checkpoint.scope.category || null,
            currentCategory: this.checkpoint && this.checkpoint.currentCategory || null,
            progress: progressState(this.checkpoint && this.checkpoint.progress) } };
    }

    setProgress(phase, detail) {
        if (this.stopStatus && phase !== this.stopStatus) return;
        this.checkpoint.progress = progressState({ ...this.checkpoint.progress, phase, detail });
        this.job.message = this.checkpoint.progress.detail;
        this.emit(this.snapshot());
    }

    async start(category) {
        if (this.namesTask) throw new Error('Aguarde a recuperacao dos nomes antes de iniciar uma pesquisa.');
        const selected = category === undefined ? null : normalizeCategory(category);
        if (category !== undefined && !selected) throw new Error('Escolha uma categoria ou subcategoria valida.');
        const response = await this.api('POST', ROOT, selected ? { category: selected } : {});
        if (selected) {
            const existing = normalizeCategory(response.job && response.job.checkpoint && response.job.checkpoint.scope && response.job.checkpoint.scope.category);
            if (!existing || existing.key !== selected.key || existing.url !== selected.url) {
                throw new Error('Existe uma pesquisa ativa com outro escopo. Retome ou cancele essa pesquisa antes de iniciar a categoria escolhida.');
            }
            if (['paused', 'waiting_login'].includes(response.job.status)) return response;
        }
        return this.attach(response.job);
    }

    attach(job) {
        if (this.namesTask) throw new Error('Aguarde a recuperacao dos nomes antes de retomar uma pesquisa.');
        if (!job || !job.id) throw new Error('A pesquisa nao retornou uma execucao valida.');
        if (this.task) {
            if (this.job.id !== job.id) throw new Error('Outra pesquisa ja esta em execucao neste aplicativo.');
            return this.snapshot();
        }
        this.job = clone(job);
        this.job.counters = { categories: 0, pages: 0, sellers: 0, ads: 0, pending: 0, ...(job.counters || {}) };
        this.checkpoint = initialCheckpoint(job.checkpoint);
        this.officialNavigation = null;
        validateCheckpointTaxonomy(this.checkpoint);
        this.seenSeeds = new Set(this.checkpoint.seenSeedIds);
        this.seenSellers = new Set(this.checkpoint.seenSellerKeys);
        this.stopStatus = '';
        this.controller = new AbortController();
        this.job.status = 'running';
        this.setProgress('preparing', ['paused', 'waiting_login', 'failed'].includes(job.status)
            ? 'Retomando a pesquisa a partir do progresso salvo.' : this.job.message || 'Preparando a pesquisa no Mercado Livre.');
        this.task = this.run().finally(() => { this.task = null; });
        // Caller receives an immediate response; failures are represented in job status.
        return this.snapshot();
    }

    async status(jobId = '') {
        const response = await this.api('GET', jobId ? `${ROOT}/${encodeURIComponent(jobId)}` : `${ROOT}/latest`);
        if (this.task && this.job && response.job && this.job.id === response.job.id) return this.snapshot();
        if (response.job && response.job.status === 'running' && !this.task) {
            const message = 'Pesquisa recuperada. Clique em Retomar para continuar.';
            const checkpoint = clone(response.job.checkpoint || {});
            checkpoint.progress = progressState({ ...initialCheckpoint(checkpoint).progress, phase: 'paused', detail: message });
            return this.api('PATCH', `${ROOT}/${encodeURIComponent(response.job.id)}/checkpoint`, {
                checkpoint, counters: response.job.counters, status: 'paused', message
            });
        }
        return response;
    }

    refreshNames(jobId) {
        if (this.task || this.namesTask) return Promise.reject(new Error('Pause ou encerre a pesquisa antes de recuperar os nomes.'));
        if (!jobId) return Promise.reject(new Error('Escolha uma pesquisa para recuperar os nomes.'));
        this.namesJobId = String(jobId);
        this.namesController = new AbortController();
        this.namesTask = this.recoverNames(this.namesJobId, this.namesController.signal).finally(() => {
            this.namesTask = null; this.namesController = null; this.namesJobId = '';
        });
        return this.namesTask;
    }

    cancelNameRecovery() {
        if (this.namesController) this.namesController.abort();
        if (this.namesTask && typeof this.browser.stop === 'function') this.browser.stop();
    }

    async nameStep(operation, signal) {
        if (signal.aborted) { const error = new Error('Recuperacao interrompida.'); error.name = 'AbortError'; throw error; }
        let abort;
        const interrupted = new Promise((_resolve, reject) => {
            abort = () => { const error = new Error('Recuperacao interrompida.'); error.name = 'AbortError'; reject(error); };
            signal.addEventListener('abort', abort, { once: true });
        });
        try { return await Promise.race([Promise.resolve().then(operation), interrupted]); }
        finally { signal.removeEventListener('abort', abort); }
    }

    async ensureAvantSession(signal, state, force = false) {
        if (typeof this.browser.ensureAvantSession !== 'function') return true;
        if (!force && state.checked) return true;
        state.checked = true;
        let result;
        try {
            result = await this.nameStep(() => this.browser.ensureAvantSession({ signal, force }), signal);
        } catch (error) {
            if (error && error.status === 404) error.avantSessionRejected = true;
            if (error && (error.name === 'AbortError' || error.status === 401 || error.status === 403 || error.status === 404)) throw error;
        }
        if (signal.aborted) { const error = new Error('Pesquisa interrompida.'); error.name = 'AbortError'; throw error; }
        state.attempted = state.attempted || !!(result && result.attempted);
        if (result && result.authenticated === true) return true;
        const error = new Error(result && result.reason === 'ml_login_required'
            ? 'Conclua o login do Mercado Livre no navegador e tente novamente.'
            : 'Nao foi possivel autenticar no Avant Pro. Conclua o login no navegador e tente novamente.');
        error.waitingLogin = true;
        throw error;
    }

    async recoverAvantSession(result, signal, state) {
        if (!requiresAvantLogin(result) || state.forced || state.attempted
            || typeof this.browser.ensureAvantSession !== 'function') return false;
        state.forced = true;
        await this.ensureAvantSession(signal, state, true);
        return true;
    }

    async readSellerName(seller, signal) {
        if (!sameSeller(seller, seller)) return null;
        const profile = canonicalUrl(seller.perfilUrl) || (/^\d+$/.test(String(seller.id || ''))
            ? `https://lista.mercadolivre.com.br/_CustId_${seller.id}` : '');
        if (!profile) return null;
        const avantState = { checked: false, attempted: false, forced: false };
        await this.nameStep(() => this.browser.navigate(profile, signal), signal);
        const candidate = page => {
            const confirmed = sellerFromPage(page);
            if (!confirmed || !sameSeller(seller, confirmed)) return null;
            if (confirmed.nome) return { seller_key: seller.seller_key || seller.key, nome: confirmed.nome,
                seller_id: confirmed.id || seller.id || '', seller_perfil_url: confirmed.perfilUrl,
                seller_name_kind: confirmed.seller_name_kind, source: 'perfil' };
            return null;
        };
        let page, target = profile;
        for (let poll = 0; poll < 8; poll += 1) {
            await this.nameStep(() => this.wait(450), signal);
            page = await this.nameStep(() => this.browser.executeJavaScript(this.scripts.discoveryScript()), signal);
            if (!page) continue;
            if (currentPageRequiresLogin(page, this.browser)) {
                if (await this.recoverAvantSession(page, signal, avantState)) { poll -= 1; continue; }
                const error = new Error('Conclua o login do Mercado Livre ou Avant e recupere os nomes novamente.'); error.waitingLogin = true; throw error;
            }
            if (page.readyState === 'loading' || typeof this.browser.isLoading === 'function' && this.browser.isLoading()) continue;
            // Discard a previous document still visible during navigation.
            if (page.url && canonicalUrl(page.url) !== canonicalUrl(this.browser.getURL())) continue;
            const found = candidate(page);
            if (found) return found;
            if (sameSeller(seller, page) && allowedUrl(page.allAdsUrl) && canonicalUrl(page.allAdsUrl) !== target) {
                target = canonicalUrl(page.allAdsUrl);
                await this.nameStep(() => this.browser.navigate(target, signal), signal);
                avantState.checked = false;
                continue;
            }
            if (sameSeller(seller, page)) break;
        }
        if (!page || !sameSeller(seller, page) || typeof this.scripts.snapshotScript !== 'function') return null;
        await this.ensureAvantSession(signal, avantState);
        if (typeof this.scripts.prepareScript === 'function') await this.nameStep(() => this.browser.executeJavaScript(this.scripts.prepareScript()), signal);
        for (let poll = 0; poll < 3; poll += 1) {
            const snapshot = await this.nameStep(() => this.browser.executeJavaScript(this.scripts.snapshotScript({ seller: {
                ...seller, nome: '', vendedor: '', sellerId: seller.id
            } })), signal);
            if (!snapshot) continue;
            if (currentPageRequiresLogin(snapshot, this.browser)) {
                if (await this.recoverAvantSession(snapshot, signal, avantState)) {
                    if (typeof this.scripts.prepareScript === 'function') await this.nameStep(() => this.browser.executeJavaScript(this.scripts.prepareScript()), signal);
                    poll -= 1; continue;
                }
                const error = new Error('Conclua o login do Mercado Livre ou Avant e recupere os nomes novamente.'); error.waitingLogin = true; throw error;
            }
            if (!sameSeller(seller, snapshot) || snapshot.url && canonicalUrl(snapshot.url) !== canonicalUrl(this.browser.getURL())) return null;
            const enriched = enrichSellerName({ ...seller, nome: '' }, snapshot.anuncios);
            if (enriched.nome) return { seller_key: seller.seller_key || seller.key, nome: enriched.nome,
                seller_id: enriched.seller_name_id || snapshot.sellerId || '',
                seller_perfil_url: enriched.seller_name_profile_url || snapshot.perfilUrl || '',
                seller_name_kind: enriched.seller_name_kind, source: enriched.seller_name_source };
            if (typeof this.scripts.clickAvantScript !== 'function') break;
            const click = await this.nameStep(() => this.browser.executeJavaScript(this.scripts.clickAvantScript()), signal);
            if (click && click.loginBlocked) {
                if (await this.recoverAvantSession({ reason: 'avant_login_required' }, signal, avantState)) {
                    if (typeof this.scripts.prepareScript === 'function') await this.nameStep(() => this.browser.executeJavaScript(this.scripts.prepareScript()), signal);
                    poll -= 1; continue;
                }
                const error = new Error('Conclua o login do Avant e recupere os nomes novamente.'); error.waitingLogin = true; throw error;
            }
            await this.nameStep(() => this.wait(450), signal);
        }
        return null;
    }

    async recoverNames(jobId, signal) {
        const base = `${ROOT}/${encodeURIComponent(jobId)}`;
        let timer, updated = 0, pending = 0, requiresLogin = false, timedOut = false, job, prepared = false;
        const updates = [];
        const flush = async () => {
            if (!updates.length) return;
            const result = await this.api('PUT', `${base}/nomes`, { updates: updates.splice(0, 100) });
            updated += Number(result.updated || 0); pending = Number(result.pending || 0); job = result.job || job;
        };
        timer = setTimeout(() => { timedOut = true; this.cancelNameRecovery(); }, this.namesBudgetMs);
        try {
            // Resolve this exact job's ownership before creating or navigating a browser.
            const authorized = await this.nameStep(() => this.api('GET', base), signal);
            job = authorized.job;
            if (!job || ['running', 'waiting_login'].includes(job.status)) throw new Error('Pause ou encerre a pesquisa antes de recuperar os nomes.');
            const response = await this.nameStep(() => this.api('GET', `${base}/vendedores`), signal);
            const affected = (response.items || []).filter(seller => seller.name_pending === true || !cleanSellerName(seller.nome || seller.vendedor, seller.id));
            pending = affected.length;
            if (!pending) return { updated, pending, job, requiresLogin: false, message: 'Os nomes desta pesquisa ja estao identificados.' };
            if (typeof this.browser.prepare === 'function') await this.nameStep(() => this.browser.prepare(), signal);
            prepared = true;
            for (const seller of affected) {
                if (signal.aborted) break;
                const controller = new AbortController();
                const abort = () => controller.abort();
                signal.addEventListener('abort', abort, { once: true });
                const sellerTimer = setTimeout(() => { controller.abort(); if (typeof this.browser.stop === 'function') this.browser.stop(); }, this.namesSellerTimeoutMs);
                try {
                    const update = await this.readSellerName(seller, controller.signal);
                    if (update && !signal.aborted) updates.push(update);
                    if (updates.length === 100) await flush();
                } catch (error) {
                    if (error.status === 401 || error.status === 403 || error.status === 404) throw error;
                    if (error.waitingLogin) { requiresLogin = true; break; }
                    // A single timeout or unavailable profile remains pending.
                } finally { clearTimeout(sellerTimer); signal.removeEventListener('abort', abort); }
            }
            await flush();
            return { updated, pending, job, requiresLogin,
                message: requiresLogin ? 'Conclua o login no navegador e recupere os nomes novamente.'
                    : timedOut ? 'Tempo de consulta encerrado; nomes confirmados foram salvos. Repita para consultar os pendentes.'
                    : signal.aborted ? 'Recuperacao interrompida; nomes confirmados foram salvos.'
                    : pending ? 'Nomes confirmados foram salvos. Alguns vendedores continuam pendentes de consulta.' : 'Nomes das lojas atualizados.' };
        } catch (error) {
            if (error.name !== 'AbortError') throw error;
            await flush();
            return { updated, pending, job, requiresLogin, message: 'Consulta interrompida; repita para recuperar os nomes pendentes.' };
        } finally {
            clearTimeout(timer);
            if (prepared && typeof this.browser.finish === 'function') await this.browser.finish(requiresLogin ? 'waiting_login' : 'completed');
        }
    }

    async resume(jobId) {
        if (this.task) {
            if (this.job.id !== jobId) throw new Error('Outra pesquisa ja esta em execucao.');
            if (!this.stopStatus) return this.snapshot();
            await this.task;
        }
        const response = await this.api('GET', `${ROOT}/${encodeURIComponent(jobId)}`);
        if (!response.job || !ACTIVE.has(response.job.status)) throw new Error('Esta pesquisa ja foi encerrada.');
        return this.attach(response.job);
    }

    async stop(status = 'paused') {
        if (!this.job) return this.snapshot();
        if (!ACTIVE.has(this.job.status) && !this.task) return this.snapshot();
        this.stopStatus = status;
        this.job.status = status;
        if (this.controller) this.controller.abort();
        if (typeof this.browser.stop === 'function') this.browser.stop();
        this.setProgress(status, status === 'cancelled' ? 'Pesquisa cancelada; resultados preservados.' : 'Pesquisa pausada; progresso salvo.');
        await this.save();
        return this.snapshot();
    }

    async save() {
        const checkpoint = clone(this.checkpoint);
        const status = this.stopStatus || this.job.status;
        const counters = clone(this.job.counters || {});
        const message = this.job.message || '';
        this.saveQueue = this.saveQueue.catch(() => {}).then(async () => {
            const response = await this.api('PATCH', `${ROOT}/${encodeURIComponent(this.job.id)}/checkpoint`, {
                checkpoint, status: this.stopStatus || status, counters, message
            });
            this.job = { ...this.job, ...response.job, status: this.stopStatus || response.job.status, message: this.checkpoint.progress.detail };
            if (checkpoint.discoveredSeed && this.checkpoint.discoveredSeed && checkpoint.discoveredSeed.key === this.checkpoint.discoveredSeed.key) {
                delete this.checkpoint.discoveredSeed;
            }
            this.emit(this.snapshot());
        });
        return this.saveQueue;
    }

    check() {
        if (this.controller.signal.aborted) {
            const err = new Error('Pesquisa interrompida.');
            err.name = 'AbortError';
            throw err;
        }
    }

    async attempt(operation) {
        let last;
        const avantState = { checked: false, attempted: false, forced: false };
        for (let index = 0; index < 3; index += 1) {
            this.check();
            try {
                const result = await operation(avantState);
                this.check();
                if (result && (result.authRequired || result.avantAuthRequired || result.reason === 'avant_login_required')) {
                    if (await this.recoverAvantSession(result, this.controller.signal, avantState)) { index -= 1; continue; }
                    const err = new Error(result.reason || 'Conclua o login do Mercado Livre ou Avant e clique em Retomar.');
                    err.waitingLogin = true;
                    throw err;
                }
                return result;
            } catch (err) {
                if (err.name === 'AbortError' || err.waitingLogin || err.categoryScopeMismatch || err.categoryUnavailable || err.status === 401 || err.status === 403 || err.status === 404) throw err;
                last = err;
                if (index < 2) await this.wait(600 * (index + 1));
            }
        }
        throw last;
    }

    async read(url, purpose = 'category', navigationCategoryId = '') {
        if (!allowedUrl(url)) throw new Error('Link de pesquisa fora do Mercado Livre.');
        return this.attempt(async avantState => {
            this.check();
            await this.browser.navigate(url, this.controller.signal);
            let page;
            const isCategory = purpose === 'category' || purpose === 'navigation';
            const isOfficialIndex = purpose === 'index' && sameCategoryPage(url, 'https://www.mercadolivre.com.br/categorias');
            const verifyDestination = isCategory || isOfficialIndex;
            const categoryId = isOfficialIndex ? '' : purpose === 'navigation' ? navigationCategoryId : this.officialCategoryId();
            const destination = categoryPageWaiter(isOfficialIndex || isCategory && this.checkpoint && this.checkpoint.scope && this.checkpoint.scope.category ? url : null,
                verifyDestination ? categoryId : '');
            for (let poll = 0; poll < 34; poll += 1) {
                await this.wait(450);
                this.check();
                page = await this.browser.executeJavaScript(this.scripts.discoveryScript());
                if (page.authRequired || page.avantAuthRequired) {
                    if (currentPageRequiresLogin(page, this.browser)) return { ...page, authRequired: true,
                        reason: page.authRequired ? 'ml_login_required' : 'avant_login_required' };
                    if (verifyDestination) destination.observe(page.url || this.browser.getURL(), false, true);
                    continue;
                }
                const listings = (page.anuncios || []).length > 0;
                if (purpose === 'first-page' && page.readyState !== 'loading' && !(typeof this.browser.isLoading === 'function' && this.browser.isLoading())
                    && (!page.url || canonicalUrl(page.url) === canonicalUrl(this.browser.getURL()))) {
                    const attemptedBefore = avantState.attempted;
                    await this.ensureAvantSession(this.controller.signal, avantState);
                    if (!attemptedBefore && avantState.attempted) { poll -= 1; continue; }
                }
                const ready = purpose === 'seller' ? !!page.allAdsUrl
                    : purpose === 'item' ? !!sellerFromPage(page)
                    : purpose === 'first-page' ? true // The collector owns settling and scrolling this page.
                    : isOfficialIndex ? (page.categoryTree || []).length > 0 || (page.categories || []).length > 0
                    : listings || (page.categories || []).length > 0 || page.listingUrl || (purpose === 'index' && page.categoriesIndexUrl) || page.empty;
                if (verifyDestination) {
                    const loading = page.readyState === 'loading' || typeof this.browser.isLoading === 'function' && this.browser.isLoading();
                    if (destination.observe(page.url || this.browser.getURL(), ready, loading, page.effectiveCategoryId, page.categoryIdentityConflict)) {
                        if (page.categoryNotFound && !listings) {
                            const error = new Error('O Mercado Livre nao reconheceu o endereco da categoria.');
                            error.categoryUnavailable = true;
                            throw error;
                        }
                        return page;
                    }
                } else if (ready) return page;
            }
            if (verifyDestination && destination.mismatch) {
                const error = new Error('O Mercado Livre abriu outra categoria. O escopo selecionado foi preservado.');
                error.categoryScopeMismatch = true;
                throw error;
            }
            if (purpose === 'index' && !isOfficialIndex && !page.authRequired && !page.avantAuthRequired) return page; // The homepage can link to the full category index instead.
            throw new Error('A pagina ainda nao apresentou os dados para continuar a pesquisa.');
        });
    }

    enqueueCategories(categories) {
        const known = new Set(this.checkpoint.categories.map(item => item.key));
        for (const raw of categories || []) {
            const url = canonicalUrl(raw.url);
            const key = String(raw.key || url);
            if (!url || !key || known.has(key)) continue;
            known.add(key);
            this.checkpoint.categories.push({ key, nome: String(raw.nome || raw.name || key), url });
        }
    }

    assertCategoryDestination(expected, actual) {
        if (this.checkpoint && this.checkpoint.scope && this.checkpoint.scope.category && !verifiedCategoryPage(expected, actual, this.officialCategoryId())) {
            const error = new Error('O Mercado Livre abriu outra categoria. O escopo selecionado foi preservado.');
            error.categoryScopeMismatch = true;
            throw error;
        }
    }

    officialCategoryId() {
        const category = this.checkpoint && this.checkpoint.currentCategory;
        const tree = this.checkpoint && this.checkpoint.officialTaxonomy;
        return tree && category && Object.prototype.hasOwnProperty.call(tree.children, category.key) ? category.key : '';
    }

    async prepareOfficialTaxonomy() {
        const scope = this.checkpoint.scope && this.checkpoint.scope.category;
        if (!this.taxonomy || !scope || !/^MLB\d+$/.test(scope.key)) return;
        const previous = this.checkpoint.officialTaxonomy;
        const needsNavigation = [scope, ...Object.values(previous && previous.children || {}).flat()]
            .some(node => unsupportedCategoryAlias(node.url));
        if (previous && (previous.ancestry || !needsNavigation)) return;
        const snapshot = await this.taxonomy.load();
        this.check();
        if (!snapshot.complete || !snapshot.get(scope.key)) throw new Error('A arvore oficial da categoria nao esta disponivel.');
        const children = previous ? previous.children : Object.create(null), pending = previous ? [] : [scope.key];
        for (let index = 0; index < pending.length; index += 1) {
            const key = pending[index];
            if (Object.prototype.hasOwnProperty.call(children, key)) continue;
            children[key] = snapshot.children(key).map(raw => {
                const category = normalizeCategory(raw);
                if (!category || !/^MLB\d+$/.test(category.key)) throw new Error('A arvore oficial retornou uma categoria invalida.');
                return category;
            });
            pending.push(...children[key].map(item => item.key));
        }
        // Preserve descendants on resume; an older checkpoint needs only the
        // missing ancestry, never a replacement tree from a refreshed cache.
        const tree = previous || { updatedAt: snapshot.updatedAt, children };
        if (typeof snapshot.pathFromRoot === 'function') {
            const ancestry = snapshot.pathFromRoot(scope.key).map(normalizeCategory);
            if (!ancestry.length || ancestry.some(node => !node) || ancestry.at(-1).key !== scope.key
                || ancestry.at(-1).nome !== scope.nome) throw new Error('O caminho oficial da categoria nao esta disponivel.');
            ancestry[ancestry.length - 1] = clone(scope);
            tree.ancestry = ancestry;
        } else if (needsNavigation || Object.values(children).flat().some(node => unsupportedCategoryAlias(node.url))) {
            throw new Error('O caminho oficial da categoria nao esta disponivel.');
        }
        this.checkpoint.officialTaxonomy = tree;
        validateCheckpointTaxonomy(this.checkpoint);
        await this.save();
    }

    async officialCategoryUrl(category) {
        if (!unsupportedCategoryAlias(category.url)) return category.url;
        const tree = this.checkpoint.officialTaxonomy;
        if (!tree || !tree.ancestry) throw new Error('O caminho oficial da categoria nao esta disponivel.');
        if (!this.officialNavigation) {
            this.officialNavigation = new OfficialCategoryNavigation({
                ancestry: tree.ancestry, children: tree.children,
                normalize: normalizeCategory, read: (url, purpose, key) => this.read(url, purpose, key)
            });
        }
        return this.officialNavigation.resolve(category.key);
    }

    async readCategory(category) {
        const tree = this.checkpoint.officialTaxonomy;
        if (tree) {
            if (!Object.prototype.hasOwnProperty.call(tree.children, category.key)) throw new Error('Categoria fora da arvore selecionada.');
            const children = tree.children[category.key];
            if (!Array.isArray(children) || children.some(child => !normalizeCategory(child) || !Object.prototype.hasOwnProperty.call(tree.children, child.key))) {
                throw new Error('A arvore preservada da pesquisa esta incompleta.');
            }
            if (children.length) return { url: category.url, categories: children, hasChildren: true };
            const page = await this.read(await this.officialCategoryUrl(category));
            return { ...page, categories: [], hasChildren: false };
        }

        // Category hubs contain promotional carousels. The navigation index is
        // the canonical relationship between a hub and its actual categories.
        if (/^\/c\//.test(new URL(category.url).pathname)) {
            if (!Array.isArray(this.checkpoint.categoryTree)) {
                const index = await this.read('https://www.mercadolivre.com.br/categorias', 'index');
                this.checkpoint.categoryTree = Array.isArray(index.categoryTree) ? index.categoryTree : [];
                await this.save();
            }
            const group = this.checkpoint.categoryTree.find(item => canonicalUrl(item.url) === canonicalUrl(category.url));
            if (group && Array.isArray(group.children) && group.children.length) {
                return { url: category.url, categories: group.children, hasChildren: true };
            }
        }
        return this.read(category.url);
    }

    async nextCategoryPage(current) {
        const next = canonicalUrl(current.nextUrl);
        if (next) {
            this.assertCategoryDestination(current.url, next);
            return this.read(next);
        }
        if (!current.hasNext) return null;
        if (typeof this.scripts.nextCategoryPageScript !== 'function') throw new Error('Controle de paginacao indisponivel.');
        // Visiting a seller moves the browser away from discovery. Restore the
        // saved category page before using its own JavaScript pagination control.
        return this.attempt(async avantState => {
            await this.read(current.url);
            const click = await this.browser.executeJavaScript(this.scripts.nextCategoryPageScript(current.url));
            if (!click || !click.clicked) {
                if (click && click.hasNext === false && ['last_page', 'no_next_page'].includes(click.reason)) return null;
                throw new Error('O controle da proxima pagina da categoria nao respondeu.');
            }
            let page;
            const destination = categoryPageWaiter(this.checkpoint && this.checkpoint.scope && this.checkpoint.scope.category ? current.url : null, this.officialCategoryId());
            for (let poll = 0; poll < 34; poll += 1) {
                this.check();
                await this.wait(450);
                page = await this.browser.executeJavaScript(this.scripts.discoveryScript());
                this.check();
                if (page.authRequired || page.avantAuthRequired) {
                    if (currentPageRequiresLogin(page, this.browser)) return { ...page, authRequired: true,
                        reason: page.authRequired ? 'ml_login_required' : 'avant_login_required' };
                    destination.observe(page.url || this.browser.getURL(), false, true);
                    continue;
                }
                const ready = canonicalUrl(page.url) !== canonicalUrl(current.url) && ((page.anuncios || []).length || page.empty);
                const loading = page.readyState === 'loading' || typeof this.browser.isLoading === 'function' && this.browser.isLoading();
                if (destination.observe(page.url || this.browser.getURL(), ready, loading, page.effectiveCategoryId, page.categoryIdentityConflict)) {
                    if (page.categoryNotFound && !(page.anuncios || []).length) {
                        const error = new Error('O Mercado Livre nao reconheceu a pagina da categoria.');
                        error.categoryUnavailable = true;
                        throw error;
                    }
                    return page;
                }
            }
            if (destination.mismatch) this.assertCategoryDestination(current.url, destination.actualUrl);
            throw new Error('A proxima pagina da categoria nao terminou de carregar.');
        });
    }

    incomplete(message) {
        const category = this.checkpoint.currentCategory;
        if (category && !this.checkpoint.incompleteCategories.some(item => item.key === category.key)) {
            this.checkpoint.incompleteCategories.push({ ...category, motivo: String(message || 'Coleta incompleta.') });
        }
        this.checkpoint.progress = progressState({ ...this.checkpoint.progress, warning: message });
    }

    async publishSeller(seller, result) {
        seller = enrichSellerName(seller, result.anuncios);
        const category = this.checkpoint.currentCategory;
        const anuncios = (result.anuncios || []).map((item, index) => ({
            ...item, seller_key: seller.key, vendedor: item.vendedor || seller.nome,
            categoria: category && category.nome || '',
            categoria_key: category && category.key || '',
            ordem_coleta: Number(this.job.counters && this.job.counters.ads || 0) + index
        }));
        const response = await this.api('PUT', `${ROOT}/${encodeURIComponent(this.job.id)}/vendedores/${encodeURIComponent(seller.key)}`, {
            seller: { ...seller, categoria: category && category.nome || '' }, anuncios
        });
        this.job = { ...this.job, ...response.job, status: this.stopStatus || response.job.status, message: this.checkpoint.progress.detail };
        this.rememberSeller(seller.key);
        if (result.incomplete) this.incomplete('Alguns anuncios ficaram sem dados suficientes do Avant.');
        this.emit(this.snapshot());
    }

    rememberSeller(key) {
        if (!this.seenSellers.has(key)) {
            this.seenSellers.add(key);
            this.checkpoint.seenSellerKeys.push(key);
        }
    }

    async sellerPublished(key) {
        if (this.seenSellers.has(key)) return true;
        const result = await this.api('GET', `${ROOT}/${encodeURIComponent(this.job.id)}/vendedores?seller_key=${encodeURIComponent(key)}`);
        const found = (result.items || []).some(item => item.seller_key === key);
        if (found) this.rememberSeller(key);
        return found;
    }

    async processSeed(seed) {
        const key = String(seed.id || canonicalUrl(seed.url));
        if (!key || this.seenSeeds.has(key)) return;
        const history = await this.api('GET', `${ROOT}/${encodeURIComponent(this.job.id)}/descoberta?seed_key=${encodeURIComponent(key)}`);
        if (history.seen) { this.seenSeeds.add(key); return; }
        this.setProgress('discovery', `Consultando anuncio${seed.id ? ' ' + seed.id : ''} para identificar o vendedor...`);
        const page = await this.read(seed.url, 'item');
        let seller = sellerFromPage(page);
        if (!seller) throw new Error('Nao foi possivel confirmar o perfil do vendedor deste anuncio.');
        const discoveredKey = seller.key;
        if (!(await this.sellerPublished(seller.key))) {
            this.setProgress('seller', `Consultando a primeira pagina de ${seller.nome || seller.id || 'vendedor'}...`);
            this.checkpoint.phase = 'seller';
            await this.save();
            const profile = await this.read(seller.perfilUrl, 'seller');
            const confirmed = sellerFromPage(profile);
            if (seller.id && confirmed && confirmed.id && seller.id !== confirmed.id) throw new Error('O perfil retornou um vendedor diferente do anuncio consultado.');
            if (confirmed && sameSeller(seller, confirmed)) seller = { ...seller, ...confirmed, key: seller.key, id: confirmed.id || seller.id, nome: confirmed.nome || seller.nome };
            const allAdsUrl = canonicalUrl(profile.allAdsUrl || page.allAdsUrl);
            if (!allAdsUrl) throw new Error('A opcao ver todos os anuncios deste vendedor nao foi encontrada.');
            const allAdsPage = await this.read(allAdsUrl, 'first-page');
            const listingSeller = sellerFromPage(allAdsPage);
            if (seller.id && listingSeller && listingSeller.id && seller.id !== listingSeller.id) throw new Error('A listagem retornou anuncios de outro vendedor.');
            if (listingSeller && sameSeller(seller, listingSeller)) seller = { ...seller, ...listingSeller, key: listingSeller.id ? listingSeller.key : seller.key, id: listingSeller.id || seller.id,
                nome: seller.seller_name_kind === 'store_brand' && seller.nome ? seller.nome : listingSeller.nome || seller.nome,
                seller_name_kind: seller.seller_name_kind === 'store_brand' && seller.nome ? 'store_brand' : listingSeller.seller_name_kind };
            if (!(await this.sellerPublished(seller.key))) {
                this.setProgress('collecting', `Coletando anuncios de ${seller.nome || seller.id || 'vendedor'} e consultando os dados do Avant...`);
                const result = await this.attempt(async avantState => {
                    await this.ensureAvantSession(this.controller.signal, avantState);
                    return this.collector.collectFirstPage({
                        browser: this.browser, signal: this.controller.signal, seller: {
                            ...seller, sellerId: seller.id, vendedor: seller.nome
                        }, wait: this.wait,
                        onProgress: progress => {
                            if (this.stopStatus) return;
                            const total = Number(progress.coletados || progress.total || 0);
                            const avant = Number(progress.com_dados_avant);
                            const detail = `Consultando ${seller.nome || seller.id || 'vendedor'}: ${total} anuncio(s) encontrado(s)`
                                + (Number.isFinite(avant) ? `; ${avant} com dados do Avant.` : '.');
                            this.setProgress('collecting', detail);
                        }
                    });
                });
                this.check();
                await this.publishSeller(seller, result);
            }
            this.rememberSeller(discoveredKey);
        }
        this.checkpoint.seenSeedIds.push(key);
        this.seenSeeds.add(key);
        this.checkpoint.discoveredSeed = { key, id: seed.id || '', url: seed.url,
            category_key: this.checkpoint.currentCategory.key, seller_key: seller.key, status: 'consulted' };
        this.checkpoint.phase = 'discovery';
        this.setProgress('discovery', `Descobrindo outros vendedores em ${this.checkpoint.currentCategory.nome}...`);
    }

    async run() {
        try {
            if (typeof this.browser.prepare === 'function') await this.browser.prepare();
            this.check();
            await this.save();
            await this.prepareOfficialTaxonomy();
            if (!this.checkpoint.discoveryInitialized) {
                this.setProgress('categories', 'Carregando as categorias do Mercado Livre...');
                const root = await this.read('https://www.mercadolivre.com.br/', 'index');
                this.enqueueCategories(root.categories);
                if (!this.checkpoint.categories.length) {
                    const index = await this.read(canonicalUrl(root.categoriesIndexUrl) || 'https://www.mercadolivre.com.br/categorias', 'index');
                    this.enqueueCategories(index.categories);
                }
                if (!this.checkpoint.categories.length) throw new Error('Nenhuma categoria foi localizada na navegacao do Mercado Livre.');
                this.checkpoint.discoveryInitialized = true;
                await this.save();
            }
            while (this.checkpoint.categoryIndex < this.checkpoint.categories.length) {
                this.check();
                const category = this.checkpoint.categories[this.checkpoint.categoryIndex];
                this.checkpoint.currentCategory = category;
                // Old aliases can contain a saved empty page. Reopen this same
                // category without discarding completed sellers or seed IDs.
                if (this.checkpoint.officialTaxonomy && this.checkpoint.page && unsupportedCategoryAlias(this.checkpoint.page.url)
                    && !(this.checkpoint.page.anuncios || []).length) {
                    this.checkpoint.page = null;
                    this.checkpoint.categorySeenPages = [];
                    this.job.counters.pages = Math.max(0, Number(this.job.counters.pages || 0) - 1);
                }
                if (!this.checkpoint.page) {
                    this.setProgress('categories', `Descobrindo vendedores em ${category.nome}...`);
                    try {
                        let page = await this.readCategory(category);
                        this.enqueueCategories(page.categories);
                        if (page.hasChildren && page.categories && page.categories.length) {
                            this.checkpoint.categoryIndex += 1;
                            this.job.counters.categories = Number(this.job.counters.categories || 0) + 1;
                            await this.save();
                            continue;
                        }
                        if (page.listingUrl && canonicalUrl(page.listingUrl) !== canonicalUrl(page.url || category.url)) {
                            this.assertCategoryDestination(page.url || category.url, page.listingUrl);
                            page = await this.read(canonicalUrl(page.listingUrl));
                        }
                        this.checkpoint.page = {
                            categoryKey: this.officialCategoryId(),
                            url: canonicalUrl(page.url || category.url), nextUrl: canonicalUrl(page.nextUrl),
                            hasNext: !!page.hasNext, anuncios: page.anuncios || [], index: 0
                        };
                        this.checkpoint.categorySeenPages = [this.checkpoint.page.url];
                        this.job.counters.pages = Number(this.job.counters.pages || 0) + 1;
                        await this.save();
                    } catch (err) {
                        if (err.avantSessionRejected || err.waitingLogin || err.name === 'AbortError' || err.status === 401 || err.status === 403) throw err;
                        this.incomplete(`Categoria indisponivel: ${category.nome}.`);
                        await this.finishCategory();
                        continue;
                    }
                }
                while (this.checkpoint.page) {
                    const current = this.checkpoint.page;
                    while (current.index < current.anuncios.length) {
                        this.check();
                        const seed = current.anuncios[current.index];
                        try { await this.processSeed(seed); }
                        catch (err) {
                            if (err.avantSessionRejected || err.waitingLogin || err.name === 'AbortError' || err.status === 401 || err.status === 403) throw err;
                            this.incomplete('Um anuncio ou vendedor nao pode ser consultado; a pesquisa continuara.');
                            this.checkpoint.discoveredSeed = { key: String(seed.id || canonicalUrl(seed.url)),
                                id: seed.id || '', url: seed.url, category_key: category.key, status: 'failed' };
                        }
                        current.index += 1;
                        this.checkpoint.phase = 'discovery';
                        await this.save();
                    }
                    // Consume a restored discovery queue before replacing its
                    // obsolete route. Current links may no longer include those seeds.
                    if (this.checkpoint.officialTaxonomy && unsupportedCategoryAlias(current.url)) {
                        try {
                            const page = await this.readCategory(category);
                            const loaded = canonicalUrl(page.url || category.url);
                            this.checkpoint.categorySeenPages = [loaded];
                            this.checkpoint.page = { categoryKey: this.officialCategoryId(), url: loaded,
                                nextUrl: canonicalUrl(page.nextUrl), hasNext: !!page.hasNext,
                                anuncios: page.anuncios || [], index: 0 };
                            this.job.counters.pages = Number(this.job.counters.pages || 0) + 1;
                            await this.save();
                            continue;
                        } catch (err) {
                            if (err.avantSessionRejected || err.waitingLogin || err.name === 'AbortError' || err.status === 401 || err.status === 403) throw err;
                            this.incomplete('Nao foi possivel reabrir a categoria; os anuncios ja consultados foram preservados.');
                            break;
                        }
                    }
                    const next = canonicalUrl(current.nextUrl);
                    if (!next && !current.hasNext) break;
                    if (next && this.checkpoint.categorySeenPages.includes(next)) {
                        this.incomplete('A categoria retornou uma pagina repetida.');
                        break;
                    }
                    try {
                        this.setProgress('pagination', `Abrindo a proxima pagina de ${category.nome} para descobrir outros vendedores...`);
                        const page = await this.nextCategoryPage(current);
                        if (!page) break;
                        const loaded = canonicalUrl(page.url || next);
                        if (this.checkpoint.categorySeenPages.includes(loaded)) {
                            this.incomplete('O Mercado Livre repetiu a pagina da categoria.');
                            break;
                        }
                        this.checkpoint.categorySeenPages.push(loaded);
                        this.checkpoint.page = { categoryKey: this.officialCategoryId(), url: loaded, nextUrl: canonicalUrl(page.nextUrl), hasNext: !!page.hasNext, anuncios: page.anuncios || [], index: 0 };
                        this.job.counters.pages = Number(this.job.counters.pages || 0) + 1;
                        await this.save();
                    } catch (err) {
                        if (err.avantSessionRejected || err.waitingLogin || err.name === 'AbortError' || err.status === 401 || err.status === 403) throw err;
                        this.incomplete('Nao foi possivel abrir a proxima pagina da categoria.');
                        break;
                    }
                }
                await this.finishCategory();
            }
            this.setProgress('finalizing', 'Finalizando a pesquisa e salvando os resultados...');
            this.job.status = this.checkpoint.incompleteCategories.length ? 'completed_partial' : 'completed';
            this.setProgress(this.job.status, this.job.status === 'completed' ? 'Pesquisa concluida.' : 'Pesquisa concluida com pendencias; consulte as categorias incompletas.');
            await this.save();
        } catch (err) {
            this.job.status = this.stopStatus || (err.waitingLogin || err.status === 401 || err.status === 403 ? 'waiting_login' : 'failed');
            if (!this.stopStatus) this.setProgress(this.job.status, err.waitingLogin ? err.message :
                this.job.status === 'waiting_login' ? 'Atualize o login e clique em Retomar.' : 'Pesquisa interrompida; o progresso foi preservado.');
            try { await this.save(); } catch (_saveError) { this.emit(this.snapshot()); }
        } finally {
            if (typeof this.browser.finish === 'function') await this.browser.finish(this.job.status).catch(() => {});
        }
    }

    async finishCategory() {
        this.setProgress('finalizing', `Finalizando a categoria ${this.checkpoint.currentCategory.nome} e salvando os resultados...`);
        this.checkpoint.categoryIndex += 1;
        this.checkpoint.page = null;
        this.checkpoint.categorySeenPages = [];
        this.checkpoint.seenSeedIds = [];
        this.seenSeeds.clear();
        this.job.counters.categories = Number(this.job.counters.categories || 0) + 1;
        await this.save();
    }
}

module.exports = { PesquisaMercadoRunner, allowedUrl, canonicalUrl, sellerFromPage, cleanSellerName, sameSeller, enrichSellerName, normalizeCategory, sameCategoryPage, verifiedCategoryPage, categoryIdFromUrl, categoryPageWaiter, currentPageRequiresLogin, validateCheckpointTaxonomy };
