'use strict';

// A page only observes this coordinator. Its lifetime belongs to Electron main.
const ACTIVE = new Set(['running', 'paused', 'waiting_login', 'failed']);
const ROOT = '/api/pesquisa-mercado/jobs';
const SALES_PENDING_WARNINGS = new Set(['Alguns anuncios ficaram sem dados suficientes do Avant.',
    'Alguns vendedores salvos continuam com vendas ou medias pendentes de consulta.']);
const clone = value => JSON.parse(JSON.stringify(value));
const { OfficialCategoryNavigation, unsupportedCategoryAlias, CategoryNavigationError, categoryLabel } = require('./pesquisa-mercado-category-navigation');
const CATEGORY_FAILURES = Object.freeze({
    category_link_missing: 'O link oficial da categoria nao foi localizado.',
    category_link_ambiguous: 'O Mercado Livre apresentou mais de um link para a categoria.',
    category_filters_unavailable: 'O Mercado Livre nao apresentou os links das subcategorias nesta pagina.',
    category_scope_mismatch: 'O Mercado Livre abriu outra categoria; o escopo foi preservado.',
    category_not_found: 'O Mercado Livre nao reconheceu o endereco da categoria.',
    category_load_timeout: 'A pagina da categoria nao terminou de carregar.',
    category_access_denied: 'O Mercado Livre bloqueou temporariamente o acesso a pagina. Abra o navegador e tente novamente.',
    category_read_failed: 'Nao foi possivel consultar a categoria.',
    category_pagination_failed: 'Nao foi possivel abrir a proxima pagina da categoria.'
});

function categoryFailure(error, stage = 'category') {
    const code = Object.prototype.hasOwnProperty.call(CATEGORY_FAILURES, error && error.code) ? error.code
        : error && error.categoryScopeMismatch ? 'category_scope_mismatch' : 'category_read_failed';
    return { code, stage: ['category', 'navigation', 'pagination'].includes(stage) ? stage : 'category',
        dependency_key: /^MLB\d+$/.test(error && error.navigationDependencyKey || '') ? error.navigationDependencyKey : '',
        motivo: CATEGORY_FAILURES[code] };
}

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
    for (const field of ['id', 'sellerId', 'seller_id']) {
        const declared = String(value[field] || '').trim();
        if (/^\d+$/.test(declared)) ids.add(declared);
    }
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

function normalizedIncompleteCategories(checkpoint) {
    if (!Array.isArray(checkpoint.incompleteCategories || [])) throw new Error('As pendencias preservadas desta pesquisa estao indisponiveis.');
    const records = new Map(), ambiguous = new Set(), names = new Map();
    const clean = value => typeof value === 'string' ? value.replace(/\s+/g, ' ').trim() : '';
    for (const raw of checkpoint.categories || []) {
        const category = normalizeCategory(raw);
        if (!category) continue;
        const previous = records.get(category.key);
        if (previous && (previous.nome !== category.nome || previous.url !== category.url)) ambiguous.add(category.key);
        records.set(category.key, category);
    }
    for (const category of records.values()) {
        const name = category.nome.toLowerCase();
        if (!names.has(name)) names.set(name, []);
        names.get(name).push(category);
    }
    return (checkpoint.incompleteCategories || []).map(raw => {
        const item = raw && typeof raw === 'object' && !Array.isArray(raw) ? { ...raw } : { nome: clean(raw) };
        const name = clean(item.nome || item.categoria);
        const matches = item.key ? [records.get(item.key)].filter(Boolean) : names.get(name.toLowerCase()) || [];
        const category = matches.length === 1 && !ambiguous.has(matches[0].key) ? matches[0] : null;
        // A unique record in the frozen queue supplies identity. Homonyms and
        // unknown legacy warnings remain unassigned until evidence exists.
        return { ...item, ...(category || { nome: name }),
            motivo: typeof item.motivo === 'string' ? item.motivo : 'Motivo anterior desconhecido.' };
    });
}

function initialCheckpoint(saved) {
    const checkpoint = {
        version: 1, phase: 'categories', categories: [], categoryIndex: 0,
        currentCategory: null, page: null, categorySeenPages: [], seenSeedIds: [],
        seenSellerKeys: [], incompleteCategories: [], discoveryInitialized: false,
        ...clone(saved || {})
    };
    checkpoint.progress = progressState(checkpoint.progress);
    checkpoint.incompleteCategories = normalizedIncompleteCategories(checkpoint);
    if (!Array.isArray(checkpoint.countedCategoryKeys)) checkpoint.countedCategoryKeys = checkpoint.categories.slice(0, checkpoint.categoryIndex).map(item => item.key);
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
        this.salesRecoveryFinished = false;
        this.salesRecoveryDeferred = false;
        this.namesBudgetMs = Math.max(1, options.namesBudgetMs || 120000);
        this.namesSellerTimeoutMs = Math.max(1, options.namesSellerTimeoutMs || 18000);
        this.detailTimeoutMs = Math.max(1, options.detailTimeoutMs || 30000);
        this.detailNavigationTimeoutMs = Math.max(1, options.detailNavigationTimeoutMs || 35000);
    }

    snapshot() {
        if (!this.job) return { job: null };
        // No authentication context or raw checkpoint is sent as a progress event.
        const { id, status, revision, counters, message, created_at, updated_at } = this.job;
        const incompleteCategories = (this.checkpoint && this.checkpoint.incompleteCategories || []).map(raw => {
            const item = raw && typeof raw === 'object' ? raw : { nome: typeof raw === 'string' ? raw : '' };
            const known = Object.prototype.hasOwnProperty.call(CATEGORY_FAILURES, item.code);
            return { key: String(item.key || '').slice(0, 500), nome: String(item.nome || '').slice(0, 400),
                code: known ? item.code : 'legacy_unknown', motivo: known ? CATEGORY_FAILURES[item.code] : 'Motivo anterior desconhecido.',
                stage: ['category', 'navigation', 'pagination'].includes(item.stage) ? item.stage : 'category',
                dependency_key: /^MLB\d+$/.test(item.dependency_key || '') ? item.dependency_key : '' };
        });
        const recovery = this.checkpoint && this.checkpoint.recovery;
        return { job: { id, status, revision, counters, message, created_at, updated_at, generation: this.job.generation || 0,
            scopeCategory: this.checkpoint && this.checkpoint.scope && this.checkpoint.scope.category || null,
            scopePath: this.checkpoint && this.checkpoint.officialTaxonomy && this.checkpoint.officialTaxonomy.ancestry || [],
            currentCategory: this.checkpoint && this.checkpoint.currentCategory || null,
            incompleteCategories, incompleteCount: incompleteCategories.length,
            salesRecoveryPending: !!(this.checkpoint && this.checkpoint.salesRecoveryPending),
            recovery: recovery ? { generation: recovery.generation, index: recovery.index, total: recovery.categoryKeys.length } : null,
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
        this.salesRecoveryFinished = false;
        this.salesRecoveryDeferred = false;
        this.controller = new AbortController();
        this.job.status = 'running';
        this.setProgress('preparing', ['paused', 'waiting_login', 'failed'].includes(job.status)
            ? 'Retomando a pesquisa a partir do progresso salvo.' : this.job.message || 'Preparando a pesquisa no Mercado Livre.');
        this.task = this.run().finally(() => { this.task = null; });
        // Caller receives an immediate response; failures are represented in job status.
        return this.snapshot();
    }

    async status(jobId = '') {
        if (!jobId && this.task && this.job) return this.snapshot();
        const response = await this.api('GET', jobId ? `${ROOT}/${encodeURIComponent(jobId)}` : `${ROOT}/latest`);
        if (this.task && this.job && response.job && this.job.id === response.job.id) return this.snapshot();
        if (response.job && response.job.checkpoint && Object.prototype.hasOwnProperty.call(response.job.checkpoint, 'incompleteCategories')) {
            response.job.checkpoint.incompleteCategories = normalizedIncompleteCategories(response.job.checkpoint);
        }
        if (response.job && response.job.status === 'running' && !this.task) {
            const message = 'Pesquisa recuperada. Clique em Retomar para continuar.';
            const checkpoint = clone(response.job.checkpoint || {});
            checkpoint.progress = progressState({ ...initialCheckpoint(checkpoint).progress, phase: 'paused', detail: message });
            return this.api('PATCH', `${ROOT}/${encodeURIComponent(response.job.id)}/checkpoint`, {
                checkpoint, counters: response.job.counters, status: 'paused', message, generation: response.job.generation || 0
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

    async recoverIncomplete(jobId, expectedRevision) {
        if (this.task || this.namesTask) throw new Error('Pause a pesquisa e aguarde a operacao atual antes de recuperar as pendencias.');
        if (!jobId || !Number.isSafeInteger(expectedRevision) || expectedRevision < 0) throw new Error('Atualize a pesquisa antes de recuperar as pendencias.');
        await this.saveQueue.catch(() => {});
        const current = await this.api('GET', `${ROOT}/${encodeURIComponent(jobId)}`);
        if (!current.job || current.job.id !== jobId) throw new Error('Atualize a pesquisa antes de recuperar as pendencias.');
        // Validate the frozen selection before the backend atomically starts a
        // new generation. The revision check below still rejects concurrent edits.
        validateCheckpointTaxonomy(initialCheckpoint(current.job.checkpoint));
        const response = await this.api('POST', `${ROOT}/${encodeURIComponent(jobId)}/recover-incomplete`, { expected_revision: expectedRevision });
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
        this.checkMutationGeneration();
        const checkpoint = clone(this.checkpoint);
        const jobId = this.job.id, generation = Number(this.job.generation || 0);
        const status = this.stopStatus || this.job.status;
        const counters = clone(this.job.counters || {});
        const message = this.job.message || '';
        this.saveQueue = this.saveQueue.catch(() => {}).then(async () => {
            const response = await this.api('PATCH', `${ROOT}/${encodeURIComponent(jobId)}/checkpoint`, {
                checkpoint, status: this.stopStatus || status, counters, message, generation
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

    checkMutationGeneration() {
        const recovery = this.checkpoint && this.checkpoint.recovery;
        if (recovery && recovery.generation !== Number(this.job.generation || 0)) throw new Error('A pesquisa foi atualizada. Reabra o progresso salvo.');
    }

    async attempt(operation, signal = this.controller.signal) {
        let last;
        const avantState = { checked: false, attempted: false, forced: false };
        for (let index = 0; index < 3; index += 1) {
            this.check();
            try {
                const result = await this.nameStep(() => operation(avantState), signal);
                this.check();
                if (result && (result.authRequired || result.avantAuthRequired || result.reason === 'avant_login_required')) {
                    if (await this.nameStep(() => this.recoverAvantSession(result, signal, avantState), signal)) { index -= 1; continue; }
                    const err = new Error(result.reason || 'Conclua o login do Mercado Livre ou Avant e clique em Retomar.');
                    err.waitingLogin = true;
                    throw err;
                }
                return result;
            } catch (err) {
                if (err.name === 'AbortError' || err.waitingLogin || err.categoryScopeMismatch || err.categoryUnavailable || err.status === 401 || err.status === 403 || err.status === 404) throw err;
                last = err;
                if (index < 2) await this.nameStep(() => this.wait(600 * (index + 1)), signal);
            }
        }
        throw last;
    }

    async read(url, purpose = 'category', navigationCategoryId = '', expectedChild = null, browseTransition = false) {
        if (!allowedUrl(url)) throw new Error('Link de pesquisa fora do Mercado Livre.');
        let pendingBrowseUrl = '';
        const result = await this.attempt(async avantState => {
            this.check();
            await this.browser.navigate(url, this.controller.signal);
            let page;
            const isCategory = purpose === 'category' || purpose === 'navigation';
            const isCategoryOptions = purpose === 'category-options';
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
                const navigationStatus = typeof this.browser.getNavigationStatus === 'function' ? this.browser.getNavigationStatus() : null;
                if (navigationStatus && [403, 429].includes(navigationStatus.statusCode)
                    && canonicalUrl(navigationStatus.url) === canonicalUrl(page.url || this.browser.getURL())) {
                    const error = new CategoryNavigationError('category_access_denied', navigationCategoryId);
                    error.sharedCategoryFailure = true;
                    throw error;
                }
                const listings = (page.anuncios || []).length > 0;
                const loading = page.readyState === 'loading' || typeof this.browser.isLoading === 'function' && this.browser.isLoading();
                if (isCategory && page.categoryBrowse && !browseTransition && !loading
                    && canonicalUrl(page.url) === canonicalUrl(this.browser.getURL())) {
                    const browse = page.categoryBrowse;
                    const target = allowedUrl(browse.url) && new URL(browse.url);
                    if (browse.key === categoryId && target && !target.search
                        && target.hostname === 'lista.mercadolivre.com.br' && /\/\*_NoIndex_True$/.test(target.pathname)
                        && sameCategoryPage(url, page.url) && !page.categoryIdentityConflict) {
                        // The template is emitted by this category's Trends
                        // document. Its destination must then prove the same ID.
                        pendingBrowseUrl = browse.url;
                        return null;
                    }
                }
                if (isCategory && (page.categoryBrowse || page.categoryTrends)) continue;
                if (isCategoryOptions) {
                    const hasExpected = !expectedChild || (page.categories || []).some(child => child.key === expectedChild.key
                        || categoryLabel(child.nome) === categoryLabel(expectedChild.nome));
                    const conflictingParent = page.categoryDocumentIdentity && page.effectiveCategoryId
                        && navigationCategoryId && page.effectiveCategoryId !== navigationCategoryId;
                    if (!loading && !page.categoryIdentityConflict && !conflictingParent && canonicalUrl(page.url) === canonicalUrl(url)
                        && (page.categories || []).length && hasExpected) return page;
                    continue;
                }
                const requiresDocumentIdentity = isCategory && (browseTransition || /\/\*_NoIndex_True(?:_|$)/.test(new URL(url).pathname));
                if (requiresDocumentIdentity && (!page.categoryDocumentIdentity || page.effectiveCategoryId !== categoryId)) {
                    destination.observe(page.url || this.browser.getURL(), false, loading, page.effectiveCategoryId, page.categoryIdentityConflict);
                    continue;
                }
                if (purpose === 'first-page' && page.readyState !== 'loading' && !(typeof this.browser.isLoading === 'function' && this.browser.isLoading())
                    && (!page.url || canonicalUrl(page.url) === canonicalUrl(this.browser.getURL()))) {
                    const attemptedBefore = avantState.attempted;
                    await this.ensureAvantSession(this.controller.signal, avantState);
                    if (!attemptedBefore && avantState.attempted) { poll -= 1; continue; }
                }
                const ready = purpose === 'navigation' && expectedChild ? page.categoryNotFound || !!page.categoryFilterMoreUrl || (page.categories || []).some(child =>
                    child.key === expectedChild.key || categoryLabel(child.nome) === categoryLabel(expectedChild.nome))
                    : purpose === 'seller' ? !!page.allAdsUrl
                    : purpose === 'item' ? !!sellerFromPage(page)
                    : purpose === 'first-page' ? true // The collector owns settling and scrolling this page.
                    : isOfficialIndex ? (page.categoryTree || []).length > 0 || (page.categories || []).length > 0
                    : listings || (page.categories || []).length > 0 || page.listingUrl || (purpose === 'index' && page.categoriesIndexUrl) || page.empty;
                if (verifyDestination) {
                    if (destination.observe(page.url || this.browser.getURL(), ready, loading, page.effectiveCategoryId, page.categoryIdentityConflict)) {
                        if (page.categoryNotFound && !listings) {
                            const error = new Error('O Mercado Livre nao reconheceu o endereco da categoria.');
                            error.code = 'category_not_found';
                            error.categoryUnavailable = true;
                            throw error;
                        }
                        return page;
                    }
                } else if (ready) return page;
            }
            if (verifyDestination && destination.mismatch) {
                const error = new Error('O Mercado Livre abriu outra categoria. O escopo selecionado foi preservado.');
                error.code = 'category_scope_mismatch';
                error.categoryScopeMismatch = true;
                throw error;
            }
            if (purpose === 'index' && !isOfficialIndex && !page.authRequired && !page.avantAuthRequired) return page; // The homepage can link to the full category index instead.
            if (isCategoryOptions) throw new CategoryNavigationError(page && (page.categoryIdentityConflict
                || page.categoryDocumentIdentity && page.effectiveCategoryId && navigationCategoryId && page.effectiveCategoryId !== navigationCategoryId)
                ? 'category_scope_mismatch' : 'category_link_missing', expectedChild && expectedChild.key || navigationCategoryId);
            if (purpose === 'navigation' && expectedChild) {
                throw new CategoryNavigationError((page && page.categories || []).length ? 'category_link_missing' : 'category_filters_unavailable',
                    (page && page.categories || []).length ? expectedChild.key : navigationCategoryId);
            }
            const error = new Error('A pagina ainda nao apresentou os dados para continuar a pesquisa.');
            error.code = 'category_load_timeout';
            throw error;
        });
        // Keep the transition outside attempt(): a retry of the destination
        // must not reopen the Trends hub and repeat the transition.
        return pendingBrowseUrl ? this.read(pendingBrowseUrl, purpose, navigationCategoryId, expectedChild, true) : result;
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
                normalize: normalizeCategory, read: (url, purpose, key, expected) => this.read(url, purpose, key, expected)
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

    incomplete(message, failure = null) {
        this.checkpoint.categoryAttemptFailed = true;
        const category = this.checkpoint.currentCategory;
        if (category) {
            const previous = this.checkpoint.incompleteCategories.find(item => item.key === category.key);
            if (!previous) this.checkpoint.incompleteCategories.push({ ...category, motivo: String(message || 'Coleta incompleta.'), ...(failure || {}) });
            else if (failure) Object.assign(previous, failure);
            else if (SALES_PENDING_WARNINGS.has(previous.motivo) && !SALES_PENDING_WARNINGS.has(message)) {
                previous.motivo = String(message || 'Coleta incompleta.');
            }
        }
        this.checkpoint.progress = progressState({ ...this.checkpoint.progress, warning: message });
    }

    async complementSellerListings(seller, result, allAdsUrl, discoverySeed = null) {
        if (typeof this.collector.collectDetail !== 'function' || typeof this.collector.isComplete !== 'function') return result;
        const saved = this.checkpoint.sellerCollection;
        const collection = saved && saved.sellerKey === seller.key && saved.url === allAdsUrl
            ? saved : { sellerKey: seller.key, url: allAdsUrl, result: clone(result), index: 0,
                ...(discoverySeed ? { categoryKey: this.checkpoint.currentCategory.key,
                    seedKey: String(discoverySeed.id || canonicalUrl(discoverySeed.url)), seed: clone(discoverySeed) } : {}) };
        if (!Array.isArray(collection.result.anuncios)) return result;
        this.checkpoint.sellerCollection = collection;
        await this.save();
        const items = collection.result.anuncios;
        let navigated = false;
        for (let index = collection.index; index < items.length; index += 1) {
            this.check();
            const item = items[index];
            if (this.collector.isComplete(item) || !allowedUrl(item.url)) { collection.index = index + 1; continue; }
            this.setProgress('collecting', `Consultando vendas e media do anuncio ${index + 1} de ${items.length} de ${seller.nome || seller.id || 'vendedor'}...`);
            const controller = new AbortController();
            const abort = () => controller.abort();
            this.controller.signal.addEventListener('abort', abort, { once: true });
            let timer, latestPartial = null, collectionTimedOut = false, active = true;
            const armTimeout = (milliseconds, phase) => {
                if (!active || controller.signal.aborted) return;
                clearTimeout(timer);
                timer = setTimeout(() => {
                    collectionTimedOut = phase !== 'navigation';
                    controller.abort();
                    if (typeof this.browser.stop === 'function') this.browser.stop();
                }, milliseconds);
            };
            // Legacy collectors retain a bounded call. Current collectors give
            // navigation and the loaded Avant panel separate time budgets.
            armTimeout(this.detailTimeoutMs, 'collection');
            try {
                navigated = true;
                let detail;
                try {
                    detail = await this.attempt(async () => {
                        let attemptActive = true;
                        latestPartial = null;
                        const alive = () => attemptActive && active && !controller.signal.aborted;
                        const capture = partial => {
                            if (!alive()) return false;
                            const invalid = !partial || partial.authRequired || partial.avantAuthRequired
                                || ['avant_login_required', 'ml_login_required', 'page_changed', 'invalid_page_result'].includes(partial.reason);
                            latestPartial = invalid ? null : clone(partial);
                            return !invalid;
                        };
                        try {
                            const observed = await this.collector.collectDetail({
                                browser: this.browser, signal: controller.signal, url: canonicalUrl(item.url),
                                expectedItem: { id: item.id || '', url: item.url },
                                seller: { ...seller, sellerId: seller.id, vendedor: seller.nome },
                                timeoutMs: this.detailTimeoutMs, missingWaitMs: Math.min(15000, this.detailTimeoutMs), wait: this.wait,
                                onNavigationStart: () => {
                                    if (!alive()) return;
                                    latestPartial = null;
                                    armTimeout(this.detailNavigationTimeoutMs, 'navigation');
                                },
                                onNavigationComplete: () => { if (alive()) armTimeout(this.detailTimeoutMs, 'collection'); },
                                onPartial: capture
                            });
                            // Authentication or a changed document also vetoes
                            // metrics observed before the terminal result.
                            const valid = capture(observed);
                            return valid ? observed : { ...observed, anuncios: [] };
                        } finally { attemptActive = false; }
                    }, controller.signal);
                } catch (error) {
                    this.check();
                    if (!collectionTimedOut || error.name !== 'AbortError' || !latestPartial) throw error;
                    detail = latestPartial;
                }
                this.check();
                const found = (detail.anuncios || []).find(candidate => item.id
                    ? String(candidate.id || '').replace(/-/g, '').toUpperCase() === String(item.id).replace(/-/g, '').toUpperCase()
                    : canonicalUrl(candidate.url) === canonicalUrl(item.url));
                if (found && found.seller_confirmed === true && sameSeller(seller, found)
                    && (!(found.avant_seller_id || found.avant_seller_perfil_url) || sameSeller(seller, {
                        id: found.avant_seller_id, perfilUrl: found.avant_seller_perfil_url
                    }))) {
                    const avantSource = value => /^(?:avant|avant_pro|avant-pro)$/.test(String(value || '').toLowerCase())
                        || String(value || '').toLowerCase().startsWith('avantpro');
                    const previousError = String(item.erro || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
                    const previousProof = item.seller_confirmed !== false
                        && !['vendedor_nao_confirmado', 'catalogo_sem_evidencia_vendedor'].includes(item.status)
                        && !/identidade|vendedor|escopo|catalogo/.test(previousError) && sameSeller(seller, item)
                        && (!(item.avant_seller_id || item.avant_seller_perfil_url) || sameSeller(seller, {
                            id: item.avant_seller_id, perfilUrl: item.avant_seller_perfil_url
                        })) && (!item.catalogo || item.seller_confirmed === true && /anuncio|item/i.test(String(item.vendas_rotulo || '')) && sameSeller(seller, {
                            id: item.avant_seller_id, perfilUrl: item.avant_seller_perfil_url
                        }));
                    const merged = { ...item, ...found, id: item.id || found.id, url: item.url, ordem_coleta: item.ordem_coleta };
                    const freshComplete = this.collector.isComplete(found);
                    let reusedUnconfirmed = false;
                    // A detail panel may expose only the metric missing from the card.
                    // Keep existing values visible, but only reuse proven provenance
                    // to construct a complete rank from separate observations.
                    for (const [value, sources] of [
                        ['vendas', ['vendasFonte', 'vendas_fonte', 'vendas_rotulo']],
                        ['media_mensal', ['media_mensal_fonte']],
                        ['media_vendas_mensal', ['media_vendas_mensal_fonte']],
                        ['data_criacao', ['dataCriacaoFonte']]
                    ]) {
                        if (freshComplete) {
                            merged[value] = found[value] ?? (value === 'data_criacao' ? '' : null);
                            for (const source of sources) merged[source] = found[source] || '';
                            continue;
                        }
                        if (value === 'media_vendas_mensal' && (found[value] === null || found[value] === undefined)
                            && Number.isFinite(found.media_mensal)) {
                            merged[value] = found.media_mensal;
                            merged.media_vendas_mensal_fonte = found.media_mensal_fonte;
                            continue;
                        }
                        if (found[value] === null || found[value] === undefined || found[value] === '') {
                            if (item[value] !== undefined) {
                                merged[value] = item[value];
                                for (const source of sources) if (item[source] !== undefined) merged[source] = item[source];
                                const monthlySource = item.media_mensal_fonte || item.media_vendas_mensal_fonte;
                                const provenance = value === 'vendas' ? avantSource(item.vendasFonte || item.vendas_fonte)
                                    : value === 'data_criacao' ? avantSource(item.dataCriacaoFonte)
                                        || monthlySource === 'calculado_vendas_dias_avantpro' && avantSource(item.vendasFonte)
                                    : avantSource(monthlySource) || monthlySource === 'calculado_vendas_dias_avantpro' && avantSource(item.vendasFonte);
                                if (item[value] !== null && item[value] !== '' && (!previousProof || !provenance)) reusedUnconfirmed = true;
                            }
                        }
                    }
                    const catalogueProof = !merged.catalogo || /anuncio|item/i.test(String(merged.vendas_rotulo || '')) && sameSeller(seller, {
                        id: merged.avant_seller_id, perfilUrl: merged.avant_seller_perfil_url
                    });
                    merged.rankable = (found.rankable === true || ['sem_dados_avant', 'sem_quantidade_avant'].includes(found.status)) && catalogueProof;
                    merged.rankable = !reusedUnconfirmed && this.collector.isComplete(merged);
                    if (merged.rankable) { merged.status = 'analisado'; merged.erro = ''; }
                    else if (reusedUnconfirmed) {
                        merged.status = 'vendedor_nao_confirmado';
                        merged.erro = 'A origem dos dados anteriores deste anuncio precisa ser confirmada em uma nova coleta completa.';
                    }
                    items[index] = merged;
                }
            } catch (error) {
                this.check();
                if (error.waitingLogin || error.avantSessionRejected || error.status === 401 || error.status === 403 || error.status === 404) throw error;
                // A bounded unavailable detail keeps the original card's evidence.
            } finally {
                active = false;
                clearTimeout(timer);
                this.controller.signal.removeEventListener('abort', abort);
            }
            collection.index = index + 1;
            await this.save();
        }
        this.check();
        if (navigated) await this.read(allAdsUrl, 'first-page');
        const missing = items.filter(item => !this.collector.isComplete(item)).length;
        const previousReason = collection.result.reason || 'complete';
        const reason = !missing && ['complete', 'avant_data_incomplete'].includes(previousReason)
            ? 'complete' : missing && previousReason === 'complete' ? 'avant_data_incomplete' : previousReason;
        const anuncios = typeof this.collector.finalizeListings === 'function' ? this.collector.finalizeListings(items) : items;
        return { ...collection.result, anuncios, reason, incomplete: reason !== 'complete' || missing > 0,
            total: items.length, com_dados_avant: items.length - missing, incompletos: missing };
    }

    async recoverPublishedSales() {
        if (this.salesRecoveryFinished || typeof this.collector.collectDetail !== 'function'
            || typeof this.collector.isComplete !== 'function') return;
        const root = `${ROOT}/${encodeURIComponent(this.job.id)}`;
        const response = await this.nameStep(() => this.api('GET', `${root}/vendedores`), this.controller.signal);
        const saved = this.checkpoint.sellerCollection;
        const published = new Set((response.items || []).map(seller => seller.seller_key || seller.key));
        if (saved && !published.has(saved.sellerKey)) {
            // Finish an interrupted, unpublished seller before using its single
            // detail checkpoint for a round of already published pending cards.
            this.salesRecoveryDeferred = true;
            return;
        }
        this.salesRecoveryDeferred = false;
        if (!Array.isArray(this.checkpoint.salesRecoverySellerKeys)) {
            this.checkpoint.salesRecoverySellerKeys = [];
            this.checkpoint.salesRecoveryPending = false;
            if (saved && Array.isArray(saved.result && saved.result.anuncios)) {
                const pendingIndex = saved.result.anuncios.findIndex(item => !this.collector.isComplete(item));
                saved.index = pendingIndex < 0 ? saved.result.anuncios.length : pendingIndex;
            }
            await this.save();
        }
        // These keys mean visited in this interrupted round, never resolved.
        const done = new Set(this.checkpoint.salesRecoverySellerKeys);
        for (const previous of response.items || []) {
            this.check();
            const key = previous.seller_key || previous.key;
            if (!key || done.has(key)) continue;
            const seller = { ...previous, key, nome: cleanSellerName(previous.nome || previous.vendedor, previous.id) };
            this.setProgress('collecting', `Recuperando vendas e medias pendentes de ${seller.nome || seller.id || 'vendedor'}...`);
            try {
                // Read the complete saved seller list; a refreshed first page can
                // differ from the old collection and must not delete its cards.
                const anuncios = [];
                let total = Infinity, revision;
                for (let offset = 0; offset < total; offset += 100) {
                    const page = await this.nameStep(() => this.api('GET', `${root}/ranking?seller_key=${encodeURIComponent(key)}&offset=${offset}&limit=100`), this.controller.signal);
                    this.check();
                    if (revision !== undefined && page.revision !== revision) throw new Error('A lista salva mudou durante a leitura.');
                    revision = page.revision;
                    total = Number(page.total);
                    if (!Number.isSafeInteger(total) || total < 0 || !Array.isArray(page.items) || !page.items.length && offset < total) {
                        throw new Error('A lista salva deste vendedor esta indisponivel.');
                    }
                    anuncios.push(...page.items);
                }
                if (anuncios.every(item => this.collector.isComplete(item))) {
                    done.add(key);
                    this.checkpoint.salesRecoverySellerKeys = [...done];
                    await this.save();
                    continue;
                }
                const perfilUrl = canonicalUrl(seller.perfilUrl) || (/^\d+$/.test(String(seller.id || ''))
                    ? `https://lista.mercadolivre.com.br/_CustId_${seller.id}` : '');
                if (!perfilUrl || !sameSeller(seller, seller)) throw new Error('Perfil do vendedor salvo indisponivel.');
                const profile = await this.read(perfilUrl, 'seller');
                const confirmed = sellerFromPage(profile);
                if (!confirmed || !sameSeller(seller, confirmed)) throw new Error('O perfil salvo retornou outro vendedor.');
                const allAdsUrl = canonicalUrl(profile.allAdsUrl);
                if (!allAdsUrl) throw new Error('Primeira pagina do vendedor salvo indisponivel.');
                await this.read(allAdsUrl, 'first-page');
                const result = await this.complementSellerListings(seller, {
                    anuncios, reason: 'avant_data_incomplete', incomplete: true, primeira_pagina: true
                }, allAdsUrl);
                this.check();
                await this.publishSeller(seller, result, true);
                delete this.checkpoint.sellerCollection;
                if (result.incomplete) this.checkpoint.salesRecoveryPending = true;
            } catch (error) {
                this.check();
                if (error.waitingLogin || error.avantSessionRejected || error.status === 401 || error.status === 403) throw error;
                this.checkpoint.salesRecoveryPending = true;
                this.incomplete('Alguns vendedores salvos continuam com vendas ou medias pendentes de consulta.');
            }
            done.add(key);
            this.checkpoint.salesRecoverySellerKeys = [...done];
            await this.save();
        }
        if (!this.checkpoint.salesRecoveryPending) {
            this.checkpoint.incompleteCategories = this.checkpoint.incompleteCategories.filter(item => !SALES_PENDING_WARNINGS.has(item.motivo));
            if (SALES_PENDING_WARNINGS.has(this.checkpoint.progress.warning)) {
                this.checkpoint.progress = progressState({ ...this.checkpoint.progress, warning: '' });
            }
        }
        this.checkpoint.salesCollectionVersion = 2;
        delete this.checkpoint.salesRecoverySellerKeys;
        await this.save();
        this.salesRecoveryFinished = true;
    }

    async publishSeller(seller, result, preserveCategories = false) {
        this.checkMutationGeneration();
        seller = enrichSellerName(seller, result.anuncios);
        const category = this.checkpoint.currentCategory;
        const anuncios = (result.anuncios || []).map((item, index) => ({
            ...item, seller_key: seller.key, vendedor: item.vendedor || seller.nome,
            categoria: preserveCategories ? item.categoria || seller.categoria || '' : category && category.nome || '',
            categoria_key: preserveCategories ? item.categoria_key || '' : category && category.key || '',
            ordem_coleta: preserveCategories ? item.ordem_coleta ?? index : Number(this.job.counters && this.job.counters.ads || 0) + index
        }));
        const response = await this.api('PUT', `${ROOT}/${encodeURIComponent(this.job.id)}/vendedores/${encodeURIComponent(seller.key)}`, {
            seller: { ...seller, categoria: preserveCategories ? seller.categoria || '' : category && category.nome || '' }, anuncios,
            generation: Number(this.job.generation || 0)
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

    async processSeed(seed, retryFailed = false) {
        const key = String(retryFailed ? seed.key || seed.id || canonicalUrl(seed.url) : seed.id || canonicalUrl(seed.url));
        if (!key || !retryFailed && this.seenSeeds.has(key)) return;
        const history = await this.api('GET', `${ROOT}/${encodeURIComponent(this.job.id)}/descoberta?seed_key=${encodeURIComponent(key)}${retryFailed ? '&include_status=true' : ''}`);
        if (history.seen && (!retryFailed || history.status !== 'failed')) { this.seenSeeds.add(key); return; }
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
                const preserved = this.checkpoint.preservedSellerCollection;
                const restore = preserved && preserved.categoryKey === this.checkpoint.currentCategory.key
                    && preserved.collection.sellerKey === seller.key && preserved.collection.url === allAdsUrl;
                if (restore) this.checkpoint.sellerCollection = clone(preserved.collection);
                const pending = this.checkpoint.sellerCollection;
                const result = pending && pending.sellerKey === seller.key && pending.url === allAdsUrl
                    ? clone(pending.result) : await this.attempt(async avantState => {
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
                const complete = await this.complementSellerListings(seller, result, allAdsUrl, seed);
                this.check();
                await this.publishSeller(seller, complete);
                delete this.checkpoint.sellerCollection;
                if (restore) delete this.checkpoint.preservedSellerCollection;
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
            const recovery = this.checkpoint.recovery;
            if (recovery && !recovery.started) {
                const collection = this.checkpoint.sellerCollection;
                if (collection && !(await this.sellerPublished(collection.sellerKey))) {
                    const previous = this.checkpoint.preservedSellerCollection;
                    if (previous && (previous.collection.sellerKey !== collection.sellerKey || previous.collection.url !== collection.url)) {
                        throw new Error('As coletas interrompidas foram preservadas. Retome o progresso anterior antes de recuperar outras categorias.');
                    }
                    const owner = this.checkpoint.categories.find(item => item.key === collection.categoryKey);
                    const seed = collection.seed;
                    const confirmed = owner && seed && allowedUrl(seed.url) && collection.seedKey === String(seed.id || canonicalUrl(seed.url));
                    this.checkpoint.preservedSellerCollection = previous || { categoryKey: confirmed ? owner.key : '',
                        seed: confirmed ? clone(seed) : null, collection: clone(collection) };
                    delete this.checkpoint.sellerCollection;
                }
                recovery.started = true;
                this.checkpoint.page = null;
                this.checkpoint.categorySeenPages = [];
                this.checkpoint.currentCategory = null;
                this.checkpoint.attemptCategoryKey = '';
                await this.save();
            }
            await this.recoverPublishedSales();
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
            while (this.hasNextCategory()) {
                this.check();
                const recovery = this.activeRecovery();
                const category = recovery ? this.checkpoint.categories.find(item => item.key === recovery.categoryKeys[recovery.index])
                    : this.checkpoint.categories[this.checkpoint.categoryIndex];
                if (!category) throw new Error('A fila preservada da pesquisa esta incompleta.');
                if (!recovery && (this.checkpoint.recovery && this.checkpoint.recovery.recoveredCategoryKeys || []).includes(category.key)) {
                    this.checkpoint.categoryIndex += 1;
                    await this.save();
                    continue;
                }
                this.checkpoint.currentCategory = category;
                if (this.checkpoint.attemptCategoryKey !== category.key) {
                    this.checkpoint.attemptCategoryKey = category.key;
                    this.checkpoint.categoryAttemptFailed = false;
                }
                if (recovery && recovery.failedSeedsCategoryKey !== category.key) {
                    const preserved = this.checkpoint.preservedSellerCollection;
                    if (preserved && preserved.categoryKey === category.key && preserved.seed) {
                        try { await this.processSeed(preserved.seed, true); }
                        catch (error) {
                            if (error.waitingLogin || error.avantSessionRejected || error.name === 'AbortError' || error.status === 401 || error.status === 403) throw error;
                            this.incomplete('A coleta interrompida foi preservada e continua pendente.');
                        }
                        await this.save();
                    }
                    await this.retryFailedSeeds(category);
                    recovery.failedSeedsCategoryKey = category.key;
                    await this.save();
                }
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
                            await this.finishCategory();
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
                        const failure = categoryFailure(err, err.navigationDependencyKey ? 'navigation' : 'category');
                        this.incomplete(failure.motivo, failure);
                        if (err.sharedCategoryFailure) throw err;
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
                            if (this.salesRecoveryDeferred || err.avantSessionRejected || err.waitingLogin || err.name === 'AbortError' || err.status === 401 || err.status === 403) throw err;
                            this.incomplete('Um anuncio ou vendedor nao pode ser consultado; a pesquisa continuara.');
                            this.checkpoint.discoveredSeed = { key: String(seed.id || canonicalUrl(seed.url)),
                                id: seed.id || '', url: seed.url, category_key: category.key, status: 'failed' };
                        }
                        current.index += 1;
                        this.checkpoint.phase = 'discovery';
                        await this.save();
                        if (this.salesRecoveryDeferred) await this.recoverPublishedSales();
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
                        const failure = categoryFailure({ ...err, code: err.code || 'category_pagination_failed' }, 'pagination');
                        this.incomplete(failure.motivo, failure);
                        break;
                    }
                }
                await this.finishCategory();
            }
            this.setProgress('finalizing', 'Finalizando a pesquisa e salvando os resultados...');
            const preserved = this.checkpoint.preservedSellerCollection;
            if (preserved) this.checkpoint.progress.warning = preserved.categoryKey
                ? 'A coleta interrompida foi preservada e continua pendente.'
                : 'Uma coleta interrompida foi preservada sem vinculo confirmado a categoria.';
            const pending = this.checkpoint.incompleteCategories.length || this.checkpoint.salesRecoveryPending || preserved;
            const navigationFailed = this.checkpoint.incompleteCategories.some(item => Object.prototype.hasOwnProperty.call(CATEGORY_FAILURES, item.code));
            this.job.status = pending ? navigationFailed && !Number(this.job.counters.pages || 0) && !Number(this.job.counters.ads || 0)
                ? 'failed' : 'completed_partial' : 'completed';
            this.setProgress(this.job.status, this.job.status === 'completed' ? 'Pesquisa concluida.'
                : this.job.status === 'failed' ? 'Nenhuma listagem foi consultada; recupere as categorias pendentes.'
                : 'Pesquisa concluida com pendencias; consulte as categorias incompletas.');
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
        const key = this.checkpoint.currentCategory.key;
        if (!this.checkpoint.categoryAttemptFailed) {
            this.checkpoint.incompleteCategories = this.checkpoint.incompleteCategories.filter(item => item.key !== key);
            this.checkpoint.progress.warning = this.checkpoint.incompleteCategories.at(-1)?.motivo || '';
        }
        const recovery = this.activeRecovery();
        if (recovery) {
            recovery.index += 1;
            recovery.recoveredCategoryKeys = [...new Set([...(recovery.recoveredCategoryKeys || []), key])];
            delete recovery.failedSeedsAfterKey;
        } else this.checkpoint.categoryIndex += 1;
        this.checkpoint.page = null;
        this.checkpoint.categorySeenPages = [];
        this.checkpoint.seenSeedIds = [];
        this.seenSeeds.clear();
        if (!this.checkpoint.countedCategoryKeys.includes(key)) {
            this.checkpoint.countedCategoryKeys.push(key);
            this.job.counters.categories = Number(this.job.counters.categories || 0) + 1;
        }
        await this.save();
    }

    activeRecovery() {
        const recovery = this.checkpoint.recovery;
        return recovery && recovery.index < recovery.categoryKeys.length ? recovery : null;
    }

    hasNextCategory() {
        return !!this.activeRecovery() || this.checkpoint.categoryIndex < this.checkpoint.categories.length;
    }

    async retryFailedSeeds(category) {
        const recovery = this.activeRecovery();
        while (recovery) {
            this.check();
            const response = await this.api('GET', `${ROOT}/${encodeURIComponent(this.job.id)}/failed-seeds?category_key=${encodeURIComponent(category.key)}&limit=100&after_key=${encodeURIComponent(recovery.failedSeedsAfterKey || '')}`);
            if (response.generation !== Number(this.job.generation || 0)) throw new Error('A pesquisa foi atualizada. Reabra o progresso salvo.');
            const items = response.items || [];
            if (!items.length) break;
            for (const seed of items) {
                this.check();
                if (typeof seed.key !== 'string' || seed.key <= (recovery.failedSeedsAfterKey || '')
                    || seed.category_key !== category.key || !allowedUrl(seed.url)) throw new Error('A fila preservada da pesquisa esta incompleta.');
                try { await this.processSeed(seed, true); }
                catch (error) {
                    if (error.waitingLogin || error.avantSessionRejected || error.name === 'AbortError' || error.status === 401 || error.status === 403) throw error;
                    this.incomplete('Um anuncio ou vendedor nao pode ser consultado; a pesquisa continuara.');
                }
                recovery.failedSeedsAfterKey = seed.key;
                await this.save();
            }
        }
    }
}

module.exports = { PesquisaMercadoRunner, allowedUrl, canonicalUrl, sellerFromPage, cleanSellerName, sameSeller, enrichSellerName, normalizeCategory, sameCategoryPage, verifiedCategoryPage, categoryIdFromUrl, categoryPageWaiter, currentPageRequiresLogin, validateCheckpointTaxonomy };
