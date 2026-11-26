'use strict';

// A page only observes this coordinator. Its lifetime belongs to Electron main.
const ACTIVE = new Set(['running', 'paused', 'waiting_login', 'failed']);
const ROOT = '/api/pesquisa-mercado/jobs';
const MAX_CONCURRENT_DETAILS = 8;
const DETAIL_RETRY_DELAYS_MS = Object.freeze([5000, 15000]);
const DETAIL_RETRY_BLOCKS = new Set(['catalogue_proof_missing', 'seller_not_confirmed', 'legacy_seller_not_confirmed']);
const AVANT_PANEL_LOADING_MESSAGE = 'O painel do Avant Pro ainda estava carregando quando a consulta terminou.';
const AVANT_METRIC_FIELDS = [
    'vendas', 'vendasFonte', 'vendas_fonte', 'vendas_rotulo', 'vendas_exibicao', 'vendas_estimadas',
    'vendas_limite_inferior', 'media_mensal', 'media_mensal_fonte', 'media_mensal_rotulo',
    'media_vendas_mensal', 'media_vendas_mensal_fonte', 'media_vendas_mensal_exibicao',
    'ritmo_atual', 'data_criacao', 'dataCriacaoFonte', 'meses_desde_criacao'
];
const SALES_PENDING_WARNINGS = new Set(['Alguns anuncios ficaram sem dados suficientes do Avant.',
    'Alguns vendedores salvos continuam com vendas ou medias pendentes de consulta.']);
const clone = value => JSON.parse(JSON.stringify(value));
const { OfficialCategoryNavigation, unsupportedCategoryAlias, CategoryNavigationError, categoryLabel, categoryBasePath, neutralCategoryPage,
    departmentPermalink, exactDirectoryUrl } = require('./pesquisa-mercado-category-navigation');
const { validCategoryRouteUrl, originalCategoryLandingBase } = require('./pesquisa-mercado-category-route-store');
const { publishedCategoryListing } = require('./pesquisa-mercado-published-listings');
const { discoverCategoryRouteResult } = require('./pesquisa-mercado-category-discovery');
const { scopeCategoryItems, validateCategoryItemScope, categoryItemScopeUrlAllowed } = require('./pesquisa-mercado-category-item-scope');
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

function hasLegacyAvantMetrics(item, version) {
    return item?.rankable === true
        && (!Number.isSafeInteger(item.avant_metrics_version) || item.avant_metrics_version < version);
}

const DISCOVERY_OUTCOMES = new Set(['ambiguous', 'timeout', 'unconfirmed', 'candidate']);

function categoryFailure(error, stage = 'category') {
    const code = Object.prototype.hasOwnProperty.call(CATEGORY_FAILURES, error && error.code) ? error.code
        : error && error.categoryScopeMismatch ? 'category_scope_mismatch' : 'category_read_failed';
    return { code, stage: ['category', 'navigation', 'pagination'].includes(stage) ? stage : 'category',
        dependency_key: /^MLB\d+$/.test(error && error.navigationDependencyKey || '') ? error.navigationDependencyKey : '',
        ...(DISCOVERY_OUTCOMES.has(error?.discoveryOutcome) ? { discovery_outcome: error.discoveryOutcome } : {}),
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

const MARKETPLACE_WAIT_DEFAULT_MS = 60_000;
const MARKETPLACE_STAGES = {
    category: 'na categoria', seller: 'na pagina do vendedor', product: 'no anuncio', pagination: 'na paginacao'
};

function isMarketplaceAccessPause(error) {
    return error?.code === 'marketplace_http_403' || error?.code === 'marketplace_http_429';
}

function publicMarketplacePage(value) {
    if (typeof value !== 'string' || value.length > 4000 || /[\\\u0000-\u0020]/.test(value) || !allowedUrl(value)) return '';
    const url = new URL(value);
    if (url.hash || /%(?:2f|5c|00|25)/i.test(url.pathname)
        || !/^(?:(?:www|lista|produto|imoveis|servicos)\.)?mercadolivre\.com\.br$/.test(url.hostname)) return '';
    let path;
    try { path = decodeURIComponent(url.pathname); } catch (_error) { return ''; }
    const listingPath = path.replace(/\/\*(?=_NoIndex_True(?:_|\/|$)|\/?$)/g, '/');
    if (!/^\/[\p{L}\p{N}_./-]*$/u.test(listingPath)
        || /\/(?:login|captcha|account-verification|challenge|oauth|auth|registration|logout)(?:\/|$)/i.test(path)) return '';
    const publicPath = /^(?:lista|imoveis|servicos)\./.test(url.hostname)
        || /^(?:\/$|\/(?:categorias|c|loja|perfil|pagina)(?:\/|$))/.test(path)
        || /\/p\/MLB\d+(?:\/|$)|\/up\/MLBU?\d+(?:\/|$)|\/MLB-\d+(?:-|\/|$)|\/_CustId_\d+/i.test(path);
    if (!publicPath) return '';
    const sellerIdInPath = path.match(/_CustId_(\d+)/i)?.[1];
    for (const [key, val] of url.searchParams) {
        if (['wid', 'item_id', 'virtual_store_id'].includes(key) && /^MLB\d+$/i.test(val)) continue;
        if (key === 'pdp_filters' && /^(?:item_id:MLB\d+|seller_id:\d+)(?:\|(?:item_id:MLB\d+|seller_id:\d+))*$/i.test(val)) continue;
        if (key === 'pdp_filters' && /^official_store:\d+(?:\|virtual_stores:MLB\d+)?$/i.test(val)) continue;
        if (key === 'category_id' && /^MLB\d+$/i.test(val)) continue;
        if (key === 'seller_id' && /^\d+$/.test(val) && (!sellerIdInPath || sellerIdInPath === val)
            && url.searchParams.getAll(key).length === 1) continue;
        if (key === 'original_category_landing' && val === 'true') continue;
        return '';
    }
    return url.href;
}

function marketplacePause(job, checkpoint = job?.checkpoint) {
    const access = checkpoint?.marketplaceAccess;
    if (job?.status !== 'paused' || ![403, 429].includes(access?.status)) return null;
    const stage = Object.hasOwn(MARKETPLACE_STAGES, access.stage) ? access.stage : '';
    const url = access.status === 403 && publicMarketplacePage(access.url);
    return { status: access.status, ...(stage ? { stage } : {}), ...(url ? { url } : {}) };
}

function assertMarketplaceNavigation(browser, stage, now = Date.now()) {
    const response = browser.getNavigationStatus?.();
    if (response?.statusCode !== 403 && response?.statusCode !== 429) return;
    const responseUrl = canonicalUrl(response.url);
    if (!responseUrl || response.didNotCommit !== true && canonicalUrl(browser.getURL()) !== responseUrl) return;
    const status = response.statusCode;
    const location = Object.hasOwn(MARKETPLACE_STAGES, stage) ? MARKETPLACE_STAGES[stage] : 'na pesquisa';
    const message = status === 403
        ? `Mercado Livre retornou HTTP 403 ${location}. Abra o navegador da pesquisa, confira o acesso e retome.`
        : `Mercado Livre retornou HTTP 429 ${location}. Pesquisa pausada para respeitar o limite de consultas.`;
    const error = Object.assign(new Error(message), { code: `marketplace_http_${status}`, status, stage });
    if (status === 403) error.publicUrl = publicMarketplacePage(response.url);
    if (status === 429) {
        const delay = Number.isSafeInteger(response.retryAfterMs) && response.retryAfterMs >= 0
            ? response.retryAfterMs : MARKETPLACE_WAIT_DEFAULT_MS;
        error.retryAt = now + Math.min(delay, Number.MAX_SAFE_INTEGER - now);
    }
    throw error;
}

function assertMarketplaceRetryAllowed(checkpoint, now = Date.now()) {
    const block = checkpoint?.marketplaceAccess;
    if (block?.status !== 429 || !Number.isSafeInteger(block.retryAt) || block.retryAt <= now) return;
    const seconds = Math.ceil((block.retryAt - now) / 1000);
    throw new Error(`Mercado Livre retornou HTTP 429. Aguarde ${seconds} s antes de retomar.`);
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
    if (/\/(?:p|up|perfil|loja|pagina|stores?|vendedor)(?:\/|$)|\/MLB-\d+|_CustId_|_SellerId_/i.test(path)) return null;
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

function sellerIdentity(value, { ignoreInvalidProfile = false } = {}) {
    const ids = new Set(), profileIds = new Set();
    for (const field of ['id', 'sellerId', 'seller_id']) {
        const declared = String(value[field] || '').trim();
        if (/^\d+$/.test(declared)) ids.add(declared);
    }
    const url = canonicalUrl(value.perfilUrl || value.seller_perfil_url || value.sellerUrl);
    let profile = '', pageSlug = '';
    if (url) {
        const parsed = new URL(url);
        for (const match of parsed.pathname.matchAll(/_(?:CustId|SellerId)_(\d+)/gi)) profileIds.add(match[1]);
        for (const [key, id] of parsed.searchParams) if (/^(?:seller_id|cust_?id)$/i.test(key) && /^\d+$/.test(id)) profileIds.add(id);
        for (const id of profileIds) ids.add(id);
        try {
            if (/^(?:www|lista)\.mercadolivre\.com\.br$/.test(parsed.hostname)) {
                pageSlug = parsed.pathname.match(/^\/pagina\/([\w-]+)\/?$/i)?.[1].toLowerCase() || '';
            }
            if (profileIds.size === 1) profile = `id:${[...profileIds][0]}`;
            else if (/^\/loja\//i.test(parsed.pathname)) profile = `store:${decodeURIComponent(parsed.pathname).replace(/\/+$/, '').toLowerCase()}`;
            else if (/^\/perfil\//i.test(parsed.pathname)) profile = `profile:${decodeURIComponent(parsed.pathname).replace(/\/+$/, '').toLowerCase()}`;
            else if (pageSlug) profile = 'page:/pagina/' + pageSlug;
        } catch (_error) {
            return ignoreInvalidProfile ? { id: ids.size === 1 ? [...ids][0] : '', profile: '', conflict: ids.size > 1 }
                : { id: '', profile: '', conflict: true };
        }
    }
    return { id: ids.size === 1 ? [...ids][0] : '', profile, pageSlug, conflict: ids.size > 1 };
}

function sameSeller(expected, observed) {
    if (expected.seller_identity_conflict || observed.seller_identity_conflict) return false;
    const left = sellerIdentity(expected), right = sellerIdentity(observed);
    if (left.conflict || right.conflict) return false;
    if (left.pageSlug && right.pageSlug) return left.pageSlug === right.pageSlug && (!left.id || !right.id || left.id === right.id);
    if (left.profile && right.profile && !left.profile.startsWith('id:') && !right.profile.startsWith('id:') && left.profile !== right.profile) return false;
    if (left.id && right.id) return left.id === right.id;
    return !!(left.profile && right.profile && left.profile === right.profile);
}

function conflictingSeller(expected, observed) {
    if (expected.seller_identity_conflict || observed.seller_identity_conflict) return true;
    // An unreadable profile still fails ranking proof, but does not establish
    // another seller. Retain any explicit numeric identity in that case.
    const left = sellerIdentity(expected, { ignoreInvalidProfile: true });
    const right = sellerIdentity(observed, { ignoreInvalidProfile: true });
    if (left.conflict || right.conflict) return true;
    if (left.pageSlug && right.pageSlug && left.pageSlug !== right.pageSlug) return true;
    if (left.id && right.id) return left.id !== right.id;
    return !!(left.profile && right.profile && left.profile.split(':', 1)[0] === right.profile.split(':', 1)[0]
        && left.profile !== right.profile);
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
    const pending = checkpoint.incompleteCategories || [];
    const dependencies = new Map();
    const needed = new Set(pending.filter(item => /^MLB\d+$/.test(item?.dependency_key || '')
        && item.dependency_key !== item.key).map(item => item.dependency_key));
    if (needed.size) {
        const tree = checkpoint.officialTaxonomy || {};
        for (const list of [tree.ancestry, ...Object.values(tree.children || {})]) {
            if (!Array.isArray(list)) continue;
            for (const node of list) {
                if (needed.has(node?.key) && typeof node.nome === 'string') dependencies.set(node.key, clean(node.nome).slice(0, 400));
            }
            if (dependencies.size === needed.size) break;
        }
    }
    return pending.map(raw => {
        const item = raw && typeof raw === 'object' && !Array.isArray(raw) ? { ...raw } : { nome: clean(raw) };
        delete item.dependency_name;
        if (!DISCOVERY_OUTCOMES.has(item.discovery_outcome)) delete item.discovery_outcome;
        const name = clean(item.nome || item.categoria);
        const matches = item.key ? [records.get(item.key)].filter(Boolean) : names.get(name.toLowerCase()) || [];
        const category = matches.length === 1 && !ambiguous.has(matches[0].key) ? matches[0] : null;
        // A unique record in the frozen queue supplies identity. Homonyms and
        // unknown legacy warnings remain unassigned until evidence exists.
        return { ...item, ...(category || { nome: name }),
            ...(dependencies.get(item.dependency_key) ? { dependency_name: dependencies.get(item.dependency_key) } : {}),
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

function officialTaxonomyPath(checkpoint, key) {
    const tree = checkpoint && checkpoint.officialTaxonomy;
    if (!tree || !Array.isArray(tree.ancestry)) return [];
    const ancestorIndex = tree.ancestry.findIndex(node => node.key === key);
    if (ancestorIndex >= 0) return tree.ancestry.slice(0, ancestorIndex + 1);
    const records = new Map(tree.ancestry.map(node => [node.key, node]));
    const parents = new Map();
    for (const [parent, children] of Object.entries(tree.children || {})) {
        for (const child of children) { records.set(child.key, child); parents.set(child.key, parent); }
    }
    const tail = [], seen = new Set(), scope = tree.ancestry.at(-1).key;
    while (key !== scope) {
        if (!records.has(key) || seen.has(key)) return [];
        seen.add(key); tail.unshift(records.get(key)); key = parents.get(key);
    }
    return [...tree.ancestry, ...tail];
}

// The taxonomy API omits the permalink for this leaf. The published landing
// links to its listing, whose document must still prove the selected ID.
function publishedLeafPermalink(checkpoint, key) {
    if (key !== 'MLB411819') return '';
    const path = officialTaxonomyPath(checkpoint, key).map(node => node.key);
    if (path.join('/') !== 'MLB1574/MLB7069/MLB412355/MLB411819') return '';
    return 'https://lista.mercadolivre.com.br/casa-moveis-decoracao/seguranca-casa/automacao-portoes/pecas/';
}

function itemScopeAncestry(checkpoint, scope) {
    return officialTaxonomyPath(checkpoint, scope.categoryKey).map(node => ({ ...node,
        url: node.key === scope.categoryKey ? scope.categoryBaseUrl
            : node.key === scope.transportCategoryId ? scope.transportBaseUrl : node.url }));
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
    if (checkpoint.page && checkpoint.page.categoryItemScope) {
        const descriptor = checkpoint.page.categoryItemScope;
        if (descriptor.categoryKey !== checkpoint.currentCategory.key
            || !validateCategoryItemScope(descriptor, itemScopeAncestry(checkpoint, descriptor))
            || !categoryItemScopeUrlAllowed(checkpoint.page.url, descriptor)
            || checkpoint.page.nextUrl && !categoryItemScopeUrlAllowed(checkpoint.page.nextUrl, descriptor)) fail();
    }
}

class PesquisaMercadoRunner {
    constructor(options) {
        this.api = options.api;
        this.browser = options.browser;
        this.createDetailBrowser = options.createDetailBrowser;
        this.detailBrowsers = [this.browser];
        this.loginBrowser = null;
        this.scripts = options.pageScripts;
        this.collector = options.collector;
        this.taxonomy = options.taxonomy || null;
        this.categoryRouteStore = options.categoryRouteStore || null;
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
        this.trendsChildRoutes = new Map();
        this.ancestorRecovery = new Map();
        this.salesRecoveryFinished = false;
        this.salesRecoveryDeferred = false;
        this.namesBudgetMs = Math.max(1, options.namesBudgetMs || 120000);
        this.namesSellerTimeoutMs = Math.max(1, options.namesSellerTimeoutMs || 18000);
        this.detailTimeoutMs = Math.max(1, options.detailTimeoutMs || 30000);
        this.detailNavigationTimeoutMs = Math.max(1, options.detailNavigationTimeoutMs || 35000);
        this.detailRetryDelaysMs = (Array.isArray(options.detailRetryDelaysMs) ? options.detailRetryDelaysMs : DETAIL_RETRY_DELAYS_MS)
            .slice(0, 2).map(ms => Math.max(0, Math.min(60000, Number(ms) || 0)));
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
                dependency_key: /^MLB\d+$/.test(item.dependency_key || '') ? item.dependency_key : '',
                ...(typeof item.dependency_name === 'string' ? { dependency_name: item.dependency_name.slice(0, 400) } : {}),
                ...(DISCOVERY_OUTCOMES.has(item.discovery_outcome) ? { discovery_outcome: item.discovery_outcome } : {}) };
        });
        const recovery = this.checkpoint && this.checkpoint.recovery;
        const access = marketplacePause(this.job, this.checkpoint);
        return { job: { id, status, revision, counters, message, created_at, updated_at, generation: this.job.generation || 0,
            ...(access ? { marketplaceAccess: access } : {}),
            scopeCategory: this.checkpoint && this.checkpoint.scope && this.checkpoint.scope.category || null,
            scopePath: this.checkpoint && this.checkpoint.officialTaxonomy && this.checkpoint.officialTaxonomy.ancestry || [],
            currentCategory: this.checkpoint && this.checkpoint.currentCategory || null,
            incompleteCategories, incompleteCount: incompleteCategories.length,
            salesRecoveryPending: !!(this.checkpoint && this.checkpoint.salesRecoveryPending),
            legacyAvantMetricsPending: this.job.legacyAvantMetricsPending === true,
            recovery: recovery ? { generation: recovery.generation, index: recovery.index, total: recovery.categoryKeys.length } : null,
            progress: progressState(this.checkpoint && this.checkpoint.progress) } };
    }

    setProgress(phase, detail) {
        if (this.stopStatus && phase !== this.stopStatus) return;
        this.checkpoint.progress = progressState({ ...this.checkpoint.progress, phase, detail });
        this.job.message = this.checkpoint.progress.detail;
        this.emit(this.snapshot());
    }

    async start(category, previousCheckpoint) {
        assertMarketplaceRetryAllowed(previousCheckpoint);
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
        assertMarketplaceRetryAllowed(job.checkpoint);
        for (const browser of this.detailBrowsers.slice(1)) {
            if (typeof browser.destroy === 'function') browser.destroy();
        }
        this.detailBrowsers = [this.browser];
        this.loginBrowser = null;
        this.job = clone(job);
        this.job.counters = { categories: 0, pages: 0, sellers: 0, ads: 0, pending: 0, ...(job.counters || {}) };
        this.checkpoint = initialCheckpoint(job.checkpoint);
        const metricsVersion = this.collector.AVANT_METRICS_VERSION;
        const savedCollections = [this.checkpoint.sellerCollection, this.checkpoint.preservedSellerCollection?.collection];
        this.refreshLegacyAvant = !!(job.legacyAvantMetricsPending || this.checkpoint.legacyAvantMetricsPending
            || metricsVersion && savedCollections.some(collection => {
                const anuncios = collection?.result?.anuncios;
                return Array.isArray(anuncios) && anuncios.some(item => hasLegacyAvantMetrics(item, metricsVersion));
            }));
        if (job.legacyAvantMetricsPending && !this.checkpoint.legacyAvantMetricsPending && metricsVersion) {
            delete this.checkpoint.salesRecoverySellerKeys;
            this.checkpoint.legacyAvantMetricsPending = true;
        }
        this.officialNavigation = null;
        this.trendsChildRoutes.clear();
        this.ancestorRecovery.clear();
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
        let response = await this.api('GET', jobId ? `${ROOT}/${encodeURIComponent(jobId)}` : `${ROOT}/latest`);
        if (this.task && this.job && response.job && this.job.id === response.job.id) return this.snapshot();
        if (response.job && response.job.checkpoint && Object.prototype.hasOwnProperty.call(response.job.checkpoint, 'incompleteCategories')) {
            response.job.checkpoint.incompleteCategories = normalizedIncompleteCategories(response.job.checkpoint);
        }
        if (response.job && response.job.status === 'running' && !this.task) {
            const message = 'Pesquisa recuperada. Clique em Retomar para continuar.';
            const checkpoint = clone(response.job.checkpoint || {});
            checkpoint.progress = progressState({ ...initialCheckpoint(checkpoint).progress, phase: 'paused', detail: message });
            response = await this.api('PATCH', `${ROOT}/${encodeURIComponent(response.job.id)}/checkpoint`, {
                checkpoint, counters: response.job.counters, status: 'paused', message, generation: response.job.generation || 0
            });
        }
        const access = marketplacePause(response.job);
        if (access) response.job = { ...response.job, marketplaceAccess: access };
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

    async ensureAvantSession(signal, state, { force = false, rankingOnly = false, browser = this.browser } = {}) {
        if (typeof browser.ensureAvantSession !== 'function') return true;
        if (!force && state.checked) return true;
        state.checked = true;
        let result;
        try {
            result = await this.nameStep(() => browser.ensureAvantSession({ signal, force, rankingOnly }), signal);
        } catch (error) {
            if (error && error.status === 404) error.avantSessionRejected = true;
            if (error && (error.name === 'AbortError' || error.code === 'research_browser_closed' || error.status === 401 || error.status === 403 || error.status === 404)) throw error;
        }
        if (signal.aborted) { const error = new Error('Pesquisa interrompida.'); error.name = 'AbortError'; throw error; }
        state.attempted = state.attempted || !!(result && result.attempted);
        if (result?.authenticated === true || !force && !rankingOnly && result?.collectionAllowed === true) return true;
        if (rankingOnly && result?.reason === 'avant_ranking_login_not_shown') return false;
        const reason = result?.reason;
        const error = new Error(['ml_verification_required', 'ml_login_required', 'avant_login_required'].includes(reason)
            ? reason : 'Nao foi possivel autenticar no Avant Pro. Conclua o login no navegador e tente novamente.');
        error.waitingLogin = true;
        throw error;
    }

    async recoverAvantSession(result, signal, state, browser = this.browser) {
        if (!requiresAvantLogin(result) || state.forced || state.attempted
            || typeof browser.ensureAvantSession !== 'function') return false;
        state.forced = true;
        await this.ensureAvantSession(signal, state, { force: true, browser });
        return true;
    }

    async readSellerName(seller, signal) {
        if (!sameSeller(seller, seller)) return null;
        const profile = canonicalUrl(seller.perfilUrl) || (/^\d+$/.test(String(seller.id || ''))
            ? `https://lista.mercadolivre.com.br/_CustId_${seller.id}` : '');
        if (!profile) return null;
        const avantState = { checked: false, attempted: false, forced: false };
        await this.nameStep(() => this.browser.navigate(profile, signal), signal);
        assertMarketplaceNavigation(this.browser, 'seller');
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
                assertMarketplaceNavigation(this.browser, 'seller');
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
            assertMarketplaceRetryAllowed(job.checkpoint);
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
                    if (isMarketplaceAccessPause(error)) { await flush(); throw error; }
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
        assertMarketplaceRetryAllowed(current.job.checkpoint);
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
        this.stopBrowsers();
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

    stopBrowsers() {
        for (const browser of this.detailBrowsers) {
            if (typeof browser.stop === 'function') browser.stop();
        }
    }

    check(signal = this.controller.signal) {
        if (this.controller.signal.aborted || signal.aborted) {
            const err = new Error('Pesquisa interrompida.');
            err.name = 'AbortError';
            throw err;
        }
    }

    checkMutationGeneration() {
        const recovery = this.checkpoint && this.checkpoint.recovery;
        if (recovery && recovery.generation !== Number(this.job.generation || 0)) throw new Error('A pesquisa foi atualizada. Reabra o progresso salvo.');
    }

    async attempt(operation, signal = this.controller.signal, browser = this.browser, maxAttempts = 3) {
        let last;
        const avantState = { checked: false, attempted: false, forced: false };
        for (let index = 0; index < maxAttempts; index += 1) {
            this.check();
            try {
                const result = await this.nameStep(() => operation(avantState), signal);
                this.check();
                if (result && (result.authRequired || result.avantAuthRequired || result.reason === 'avant_login_required')) {
                    if (await this.nameStep(() => this.recoverAvantSession(result, signal, avantState, browser), signal)) { index -= 1; continue; }
                    const err = new Error(result.reason || 'Conclua o login do Mercado Livre ou Avant e clique em Retomar.');
                    err.waitingLogin = true;
                    throw err;
                }
                return result;
            } catch (err) {
                if (err.waitingLogin || err.status === 401 || err.status === 403) this.loginBrowser = browser;
                if (err.name === 'AbortError' || isMarketplaceAccessPause(err) || err.code === 'research_browser_closed' || err.waitingLogin || err.categoryScopeMismatch || err.categoryUnavailable || err.status === 401 || err.status === 403 || err.status === 404) throw err;
                last = err;
                if (index + 1 < maxAttempts) await this.nameStep(() => this.wait(600 * (index + 1)), signal);
            }
        }
        throw last;
    }

    requireTrendsChildId(page, parentKey, child) {
        const siblings = this.checkpoint.officialTaxonomy?.children?.[parentKey];
        const sameName = entry => entry.key !== child.key && categoryLabel(entry.nome) === categoryLabel(child.nome);
        return !siblings?.some(entry => entry.key === child.key) || siblings.some(sameName)
            || (page.categories || []).some(entry => /^MLB\d+$/i.test(entry?.key) && sameName(entry));
    }

    navigationHubCandidate(page, requestedUrl, parentKey, expectedChild, requireContext) {
        const hub = page && page.categoryNavigationHub, navigation = this.officialNavigation;
        const parent = navigation && navigation.records.get(parentKey), child = expectedChild && navigation && navigation.records.get(expectedChild.key);
        if (!hub || hub.version !== 1 || Object.keys(hub).some(key => !['version', 'url', 'canonical', 'heading'].includes(key))
            || !parent || !child || navigation.parents.get(child.key) !== parentKey
            || child.nome !== expectedChild.nome || categoryLabel(hub.heading) !== categoryLabel(parent.nome)
            || !page.categoryTrends || page.categoryBrowse || page.categoryDocumentIdentity || page.categoryIdentityConflict
            || page.authRequired || page.avantAuthRequired || page.categoryNotFound
            || page.effectiveCategoryId && page.effectiveCategoryId !== parentKey
            || page.categoryContext && page.categoryContext.key !== parentKey) return '';
        try {
            const actual = new URL(this.browser.getURL());
            if (actual.protocol !== 'https:' || actual.hostname !== 'lista.mercadolivre.com.br'
                || actual.username || actual.password || actual.port && actual.port !== '443'
                || actual.search || actual.hash || actual.href !== page.url || actual.href !== hub.url || actual.href !== hub.canonical
                || !neutralCategoryPage(requestedUrl, actual.href)
                || page.categoryContext && !neutralCategoryPage(page.categoryContext.baseUrl, actual.href)) return '';
            if (requireContext && (page.categoryContext?.key !== parentKey || page.readyState !== 'complete' || !validCategoryRouteUrl(actual.href)
                || !exactDirectoryUrl(page.categoryContext.baseUrl, actual.href))) return null;
            const selected = navigation.choosePage(page, child, actual.href, requireContext && this.requireTrendsChildId(page, parentKey, child));
            return { signature: JSON.stringify([actual.href, hub.heading, page.categoryContext?.key, selected.key, selected.url]), selected };
        } catch (_error) { return ''; }
    }

    async read(url, purpose = 'category', navigationCategoryId = '', expectedChild = null, browseTransition = false) {
        if (!allowedUrl(url)) throw new Error('Link de pesquisa fora do Mercado Livre.');
        let pendingBrowseUrl = '';
        const isOfficialIndex = purpose === 'index' && exactDirectoryUrl(url, 'https://www.mercadolivre.com.br/categorias');
        const directoryEvidence = isOfficialIndex || purpose === 'navigation' && departmentPermalink(url);
        const result = await this.attempt(async avantState => {
            this.check();
            await this.browser.navigate(url, this.controller.signal);
            let page;
            const isCategory = purpose === 'category' || purpose === 'navigation' || purpose === 'category-transport' || purpose === 'category-route';
            const stage = purpose === 'first-page' || purpose === 'seller' ? 'seller' : purpose === 'item' ? 'product' : 'category';
            const isCategoryOptions = purpose === 'category-options';
            const recoveringTrendsLanding = purpose === 'navigation' && expectedChild?.preferTrendsChildren === true;
            const verifyDestination = isCategory || isOfficialIndex;
            const categoryId = isOfficialIndex ? '' : ['navigation', 'category-transport', 'category-route'].includes(purpose) ? navigationCategoryId : this.officialCategoryId();
            const destination = categoryPageWaiter(isOfficialIndex || isCategory && this.checkpoint && this.checkpoint.scope && this.checkpoint.scope.category ? url : null,
                verifyDestination ? categoryId : '');
            let mismatchSignature = '', mismatchReads = 0, hubSignature = '', hubReads = 0;
            const maxPolls = 34;
            for (let poll = 0; poll < maxPolls; poll += 1) {
                await this.wait(450);
                this.check();
                assertMarketplaceNavigation(this.browser, stage);
                if (purpose === 'category-route') {
                    const status = this.categoryRouteStatus(this.browser.getURL());
                    if (status >= 400) throw Object.assign(new Error('A listagem da categoria nao esta disponivel.'), {
                        status, waitingLogin: status === 401
                    });
                }
                page = await this.browser.executeJavaScript(this.scripts.discoveryScript());
                if (page.authRequired || page.avantAuthRequired) {
                    if (currentPageRequiresLogin(page, this.browser)) return { ...page, authRequired: true,
                        reason: page.authReason || (page.authRequired ? 'ml_login_required' : 'avant_login_required') };
                    if (verifyDestination) destination.observe(page.url || this.browser.getURL(), false, true);
                    continue;
                }
                const listings = (page.anuncios || []).length > 0;
                const loading = page.readyState === 'loading' || typeof this.browser.isLoading === 'function' && this.browser.isLoading();
                if (directoryEvidence) {
                    const response = this.browser.getNavigationStatus?.();
                    page.categoryHttpStatus = !loading && response && !response.didNotCommit && exactDirectoryUrl(response.url, url)
                        && exactDirectoryUrl(this.browser.getURL(), url) && exactDirectoryUrl(page.url, url) ? response.statusCode : 0;
                }
                const observedId = page.categoryDocumentIdentity ? page.effectiveCategoryId
                    : page.categoryTrends && page.categoryContext && page.categoryContext.key;
                if (isCategory && categoryId && observedId && observedId !== categoryId && !loading
                    && sameCategoryPage(url, page.url) && canonicalUrl(page.url) === canonicalUrl(this.browser.getURL())) {
                    const signature = JSON.stringify([page.url, observedId]);
                    mismatchReads = mismatchSignature === signature ? mismatchReads + 1 : 1;
                    mismatchSignature = signature;
                    if (mismatchReads >= 2) {
                        const error = new CategoryNavigationError('category_scope_mismatch', navigationCategoryId);
                        error.categoryScopeMismatch = true;
                        error.categoryPage = page;
                        throw error;
                    }
                } else { mismatchSignature = ''; mismatchReads = 0; }
                // Avant adds the ranking button after the category document is ready.
                if (isCategory && page.categoryTrends && !loading && !avantState.checked
                    && !page.categoryIdentityConflict && !page.categoryNotFound && page.url
                    && canonicalUrl(page.url) === canonicalUrl(this.browser.getURL())
                    && verifiedCategoryPage(url, page.url, categoryId, observedId)) {
                    await this.ensureAvantSession(this.controller.signal, avantState, { rankingOnly: true });
                    // The login can reload the ranking. Use the new document before following its links.
                    if (avantState.attempted) { poll -= 1; continue; }
                }
                if (purpose === 'navigation' && expectedChild
                    && (recoveringTrendsLanding || !browseTransition && page.categoryTrends && page.categoryBrowse)
                    && (page.categoryTrends || !page.categoryDocumentIdentity || page.effectiveCategoryId !== categoryId)) {
                    let signature = '', selected;
                    const navigation = this.officialNavigation, context = page.categoryContext, browse = page.categoryBrowse;
                    const child = navigation && navigation.records.get(expectedChild.key);
                    if (!loading && page.categoryTrends && !page.categoryIdentityConflict && !page.categoryNotFound
                        && (!page.categoryDocumentIdentity || page.effectiveCategoryId === categoryId)
                        && (!page.effectiveCategoryId || page.effectiveCategoryId === categoryId)
                        && context?.key === categoryId && browse?.key === categoryId && child && child.nome === expectedChild.nome
                        && navigation.parents.get(child.key) === categoryId
                        && canonicalUrl(page.url) === canonicalUrl(this.browser.getURL())
                        && neutralCategoryPage(url, page.url) && neutralCategoryPage(context.baseUrl, page.url)
                        && neutralCategoryPage(browse.url, page.url)) {
                        try {
                            selected = navigation.choosePage(page, child, page.url, this.requireTrendsChildId(page, categoryId, child));
                            signature = JSON.stringify([page.url, context.baseUrl, browse.url, selected.key, selected.url]);
                        } catch (_error) { /* Missing or ambiguous hrefs cannot become child navigation. */ }
                    }
                    hubReads = signature && signature === hubSignature ? hubReads + 1 : signature ? 1 : 0;
                    hubSignature = signature;
                    if (hubReads >= 2) {
                        if (recoveringTrendsLanding) navigation.routes.set(categoryId, page.url);
                        this.trendsChildRoutes.set(child.key, selected.url);
                        return { ...page, categories: [selected], categoryTrendsChild: { parentKey: categoryId, key: child.key, url: selected.url } };
                    }
                    // Never reopen a wildcard that has already returned an ancestor.
                    // On the first visit it remains available if the child link cannot be proven.
                    if (recoveringTrendsLanding || signature && poll + 1 < maxPolls) continue;
                }
                if (isCategory && page.categoryBrowse && !browseTransition && !loading
                    && canonicalUrl(page.url) === canonicalUrl(this.browser.getURL())) {
                    const browse = page.categoryBrowse;
                    const target = allowedUrl(browse.url) && new URL(browse.url);
                    if (browse.key === categoryId && target && !target.search
                        && target.hostname === 'lista.mercadolivre.com.br' && /\/\*_NoIndex_True$/.test(target.pathname)
                        && (purpose !== 'category-route' || validCategoryRouteUrl(browse.url) && neutralCategoryPage(url, browse.url))
                        && sameCategoryPage(url, page.url) && !page.categoryIdentityConflict) {
                        // The template is emitted by this category's Trends
                        // document. Its destination must then prove the same ID.
                        pendingBrowseUrl = browse.url;
                        return null;
                    }
                }
                if ((purpose === 'navigation' || purpose === 'category-route') && expectedChild) {
                    const scopedHub = !loading && this.navigationHubCandidate(page, url, navigationCategoryId, expectedChild, true);
                    const hub = scopedHub || purpose === 'navigation' && !loading && this.navigationHubCandidate(page, url, navigationCategoryId, expectedChild, false);
                    const signature = hub && JSON.stringify([!!scopedHub, hub.signature]);
                    hubReads = signature && signature === hubSignature ? hubReads + 1 : signature ? 1 : 0;
                    hubSignature = signature || '';
                    if (hubReads >= 2) {
                        if (!scopedHub) return page;
                        return { ...page, categories: [hub.selected], anuncios: [], listingUrl: '', nextUrl: '', hasNext: false,
                            categoryTrendsChild: { parentKey: categoryId, key: expectedChild.key, url: hub.selected.url } };
                    }
                }
                if (isCategory && (page.categoryBrowse || page.categoryTrends)) continue;
                const childConflict = expectedChild && page.categoryConflictingRouteIds?.includes(expectedChild.key);
                if (isCategoryOptions) {
                    const hasExpected = !expectedChild || (page.categories || []).some(child => child.key === expectedChild.key
                        || categoryLabel(child.nome) === categoryLabel(expectedChild.nome));
                    const conflictingParent = page.categoryDocumentIdentity && page.effectiveCategoryId
                        && navigationCategoryId && page.effectiveCategoryId !== navigationCategoryId;
                    if (!loading && !page.categoryIdentityConflict && !conflictingParent && canonicalUrl(page.url) === canonicalUrl(url)
                        && (childConflict || (page.categories || []).length && hasExpected)) return page;
                    continue;
                }
                const requiresDocumentIdentity = isCategory && (purpose === 'category-transport' || purpose === 'category-route' || browseTransition
                    || this.trendsChildRoutes.get(categoryId) === url || /\/\*_NoIndex_True(?:_|$)/.test(new URL(url).pathname));
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
                const ready = (purpose === 'navigation' || purpose === 'category-route') && expectedChild ? page.categoryNotFound || childConflict || !!page.categoryFilterMoreUrl || (page.categories || []).some(child =>
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
                            error.categoryPage = page;
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
                error.categoryPage = page;
                throw error;
            }
            if (purpose === 'index' && !isOfficialIndex && !page.authRequired && !page.avantAuthRequired) return page; // The homepage can link to the full category index instead.
            if (isCategoryOptions) {
                const error = new CategoryNavigationError(page && (page.categoryIdentityConflict
                    || page.categoryDocumentIdentity && page.effectiveCategoryId && navigationCategoryId && page.effectiveCategoryId !== navigationCategoryId)
                    ? 'category_scope_mismatch' : 'category_link_missing', expectedChild && expectedChild.key || navigationCategoryId);
                error.categoryPage = page;
                throw error;
            }
            if (purpose === 'navigation' && expectedChild) {
                if (page && !page.categoryDocumentIdentity && !page.categoryIdentityConflict && !page.categoryNotFound
                    && !page.authRequired && !page.avantAuthRequired && this.officialNavigation
                    && (browseTransition || /\/\*_NoIndex_True(?:_|$)/.test(new URL(url).pathname))) {
                    let child = null;
                    try { child = this.officialNavigation.choosePage(page, expectedChild, page.url || url); }
                    catch (_error) { /* Invalid or ambiguous links keep their existing classification below. */ }
                    if (child) {
                        // The child href is ready; its parent's own document ID
                        // is still missing. Keep the resolver's bounded retry.
                        const error = new CategoryNavigationError('category_load_timeout', navigationCategoryId);
                        error.categoryPage = page;
                        throw error;
                    }
                }
                const error = new CategoryNavigationError((page && page.categories || []).length ? 'category_link_missing' : 'category_filters_unavailable',
                    (page && page.categories || []).length ? expectedChild.key : navigationCategoryId);
                error.categoryPage = page;
                throw error;
            }
            const error = new Error('A pagina ainda nao apresentou os dados para continuar a pesquisa.');
            error.code = 'category_load_timeout';
            error.categoryPage = page;
            throw error;
        }, this.controller.signal, this.browser, purpose === 'category-route' || isOfficialIndex && expectedChild ? 1 : 3);
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

    categoryRouteStatus(url) {
        const response = this.browser.getNavigationStatus?.();
        return url && response && !response.didNotCommit && this.browser.getURL() === url && response.url === url
            && Number.isInteger(response.statusCode) ? response.statusCode : 0;
    }

    provenCategoryRoute(key, requested, page) {
        if (!page?.categoryDocumentIdentity || page.effectiveCategoryId !== key || page.categoryIdentityConflict
            || page.categoryContext?.key && page.categoryContext.key !== key || page.categoryNotFound || page.categoryTrends
            || page.categoryBrowse || page.categoryItemScope || page.authRequired || page.avantAuthRequired || page.readyState !== 'complete'
            || this.browser.isLoading?.() || !validCategoryRouteUrl(requested) || !validCategoryRouteUrl(page.url)
            || !neutralCategoryPage(requested, page.url)
            || page.categoryContext?.baseUrl && !neutralCategoryPage(page.categoryContext.baseUrl, page.url)) return false;
        const landingBase = originalCategoryLandingBase(requested) || originalCategoryLandingBase(page.url);
        if (landingBase && (page.categoryContext?.key !== key || !exactDirectoryUrl(page.categoryContext.baseUrl, landingBase))) return false;
        const status = this.categoryRouteStatus(page.url);
        return status >= 200 && status < 300;
    }

    provenGroupRoute(key, requested, page, child) {
        const edge = page?.categoryTrendsChild;
        if (!child || !edge || !page.categoryTrends || !page.categoryNavigationHub || page.categoryBrowse || page.categoryDocumentIdentity
            || page.categoryIdentityConflict || page.categoryNotFound || page.authRequired || page.avantAuthRequired
            || page.categoryContext?.key !== key || edge.parentKey !== key || edge.key !== child.key
            || !validCategoryRouteUrl(requested) || !validCategoryRouteUrl(page.url) || !neutralCategoryPage(requested, page.url)) return false;
        const status = this.categoryRouteStatus(page.url);
        return status >= 200 && status < 300;
    }

    async categoryRouteStorage(method, pathIds, url) {
        if (!this.categoryRouteStore || !pathIds.length) return null;
        try { return await this.categoryRouteStore[method](pathIds, url); }
        catch (_error) { return null; } // Local availability does not change marketplace results.
    }

    async readKnownCategory(key, expectedChild = null, rejectedRoutes = null) {
        const pathIds = this.officialNavigation?.pathIds?.(key);
        if (!pathIds?.length) return null;
        const cached = await this.categoryRouteStorage('get', pathIds);
        const candidates = new Set([cached, publishedCategoryListing(pathIds)]);
        for (const url of candidates) {
            if (!validCategoryRouteUrl(url)) continue;
            let page, error;
            try { page = await this.read(url, 'category-route', key, expectedChild); }
            catch (failure) {
                if (failure.name === 'AbortError' || failure.waitingLogin || failure.avantSessionRejected
                    || failure.code === 'research_browser_closed' || [401, 403, 429].includes(failure.status)) throw failure;
                error = failure;
            }
            if (this.provenGroupRoute(key, url, page, expectedChild)
                || this.provenCategoryRoute(key, url, page) && (expectedChild || page.empty || page.anuncios?.length)) {
                if (cached !== page.url) await this.categoryRouteStorage('put', pathIds, page.url);
                return page;
            }
            const observed = error?.categoryPage || page, failedStatus = this.categoryRouteStatus(observed?.url);
            const stale = error?.status === 404 || failedStatus >= 200 && failedStatus < 300 && observed
                && !observed.authRequired && !observed.avantAuthRequired && (observed.categoryNotFound || observed.categoryIdentityConflict
                    || observed.categoryDocumentIdentity && observed.effectiveCategoryId && observed.effectiveCategoryId !== key
                    || observed.categoryTrends && observed.categoryContext?.key && observed.categoryContext.key !== key);
            if (cached === url && stale) await this.categoryRouteStorage('forget', pathIds, url);
            const code = error?.status === 404 || observed?.categoryNotFound ? 'category_not_found' : stale ? 'category_scope_mismatch' : 'category_load_timeout';
            rejectedRoutes?.set(url, new CategoryNavigationError(code, key));
        }
        return null;
    }

    async rememberCategoryRoute(key, url, page, child) {
        if (this.categoryRouteStore && (this.provenCategoryRoute(key, url, page) || this.provenGroupRoute(key, url, page, child))) {
            const pathIds = this.officialNavigation?.pathIds?.(key);
            if (pathIds?.length) await this.categoryRouteStorage('put', pathIds, page.url);
        }
    }

    prepareOfficialNavigation() {
        const tree = this.checkpoint.officialTaxonomy;
        if (!this.officialNavigation && tree && tree.ancestry) {
            this.officialNavigation = new OfficialCategoryNavigation({
                ancestry: tree.ancestry, children: tree.children,
                normalize: normalizeCategory, readKnownGroup: (key, child, rejected) => this.readKnownCategory(key, child, rejected),
                read: async (url, purpose, key, expected) => {
                    const page = await this.read(url, purpose, key, expected);
                    if (purpose === 'navigation') await this.rememberCategoryRoute(key, url, page, expected);
                    return page;
                }
            });
        }
    }

    async officialCategoryUrl(category) {
        this.prepareOfficialNavigation();
        if (!unsupportedCategoryAlias(category.url)) return category.url;
        if (!this.officialNavigation) throw new Error('O caminho oficial da categoria nao esta disponivel.');
        return this.officialNavigation.resolve(category.key);
    }

    async discoverOfficialCategoryRoute(category, primaryError, seedCategoryIds = []) {
        const ancestry = officialTaxonomyPath(this.checkpoint, category.key).map(node => ({ ...node,
            url: this.officialNavigation && this.officialNavigation.routes.get(node.key) || node.url }));
        if (!ancestry.length) return null;
        const result = await this.nameStep(() => discoverCategoryRouteResult({ browser: this.browser, scripts: this.scripts,
            expected: category, ancestry, seedCategoryIds, wait: this.wait, signal: this.controller.signal,
            assertNavigation: () => assertMarketplaceNavigation(this.browser, 'category') }), this.controller.signal);
        const route = result.route && this.officialNavigation?.route(result.route, category);
        if (primaryError) primaryError.discoveryOutcome = result.route && !route ? 'unconfirmed' : result.outcome;
        if (!route) return null;
        return { ...route, categoryBaseUrl: route.url, source: 'official_category_href' };
    }

    async recoverCategoryAncestor(category, ancestor) {
        const navigation = this.officialNavigation;
        if (!navigation || this.ancestorRecovery.has(ancestor.key)) return false;
        const children = this.checkpoint.officialTaxonomy.children[ancestor.key] || [];
        const seedCategoryIds = [category.key, ...children.map(child => child.key)];
        const route = await this.discoverOfficialCategoryRoute(ancestor, null, seedCategoryIds);
        this.ancestorRecovery.set(ancestor.key, false);
        if (!route) return false;
        const page = await this.read(route.url, 'category-transport', ancestor.key);
        if (!page.categoryDocumentIdentity || page.categoryIdentityConflict || page.effectiveCategoryId !== ancestor.key
            || !neutralCategoryPage(route.url, page.url)) return false;
        navigation.routes.set(ancestor.key, route.url);
        navigation.links.delete(ancestor.key);
        if (!page.categoryConflictingRouteIds?.length && page.categories?.length) navigation.links.set(ancestor.key, page.categories);
        this.ancestorRecovery.set(ancestor.key, true);
        return true;
    }

    findNavigationAncestor(key, matches) {
        const navigation = this.officialNavigation;
        for (let parent = navigation?.parents?.get(key); parent; parent = navigation.parents.get(parent)) {
            if (matches(parent)) return navigation.records.get(parent);
        }
        return null;
    }

    async tryCategoryItemScope(category, page, categoryUrl, { maxPages = 10, budget = null } = {}) {
        if (page && (page.categoryIdentityConflict || page.authRequired || page.avantAuthRequired)) return null;
        const ancestry = officialTaxonomyPath(this.checkpoint, category.key);
        const base = value => { const parsed = new URL(value); parsed.pathname = categoryBasePath(parsed.pathname) + '/'; return parsed.href; };
        const ancestors = ancestry.slice(0, -1);
        if (page && page.categoryDocumentIdentity && !ancestors.some(node => node.key === page.effectiveCategoryId)) return null;
        const candidates = [];
        for (const transport of [...ancestors].reverse()) {
            const known = this.officialNavigation && this.officialNavigation.routes.get(transport.key);
            if (!known) continue;
            const descriptor = { version: 1, categoryKey: category.key, transportCategoryId: transport.key,
                transportBaseUrl: base(known), categoryBaseUrl: base(categoryUrl) };
            const path = itemScopeAncestry(this.checkpoint, descriptor);
            if (!validateCategoryItemScope(descriptor, path)) continue;
            candidates.push({ transport, descriptor, path });
        }
        if (page && page.readyState === 'complete' && !(typeof this.browser.isLoading === 'function' && this.browser.isLoading())
            && canonicalUrl(page.url) && canonicalUrl(page.url) === canonicalUrl(this.browser.getURL())) {
            for (const { transport, path } of candidates) {
                const scoped = scopeCategoryItems(page, { categoryKey: category.key, documentCategoryId: transport.key,
                    categoryBaseUrl: categoryUrl }, path);
                if (scoped) return scoped;
            }
        }
        let remaining = Number.isSafeInteger(maxPages) ? Math.max(0, Math.min(10, maxPages)) : 10;
        const sharedBudget = budget && Number.isSafeInteger(budget.remaining) && budget.remaining >= 0 ? budget : null;
        if (sharedBudget) remaining = Math.min(remaining, sharedBudget.remaining);
        const pending = [];
        // Reserve one first page per known ancestor before spending the shared
        // budget on pagination of the closest confirmed source.
        for (const candidate of candidates) {
            if (!remaining) break;
            remaining -= 1;
            if (sharedBudget) sharedBudget.remaining -= 1;
            const { transport, descriptor, path } = candidate;
            let observed;
            try { observed = await this.read(descriptor.transportBaseUrl, 'category-transport', transport.key); }
            catch (error) {
                if (['category_scope_mismatch', 'category_load_timeout', 'category_not_found'].includes(error.code)) continue;
                throw error;
            }
            const scoped = scopeCategoryItems(observed, { categoryKey: category.key, documentCategoryId: transport.key,
                categoryBaseUrl: categoryUrl }, path);
            if (scoped) return scoped;
            pending.push({ ...candidate, observed });
        }
        const closest = pending[0];
        if (closest) {
            const { transport, descriptor, path } = closest;
            let observed = closest.observed;
            const seen = new Set([observed.url]);
            while (remaining > 0 && (observed.nextUrl || observed.hasNext)) {
                const next = canonicalUrl(observed.nextUrl);
                if (next && (seen.has(next) || !categoryItemScopeUrlAllowed(next, descriptor))) break;
                remaining -= 1;
                if (sharedBudget) sharedBudget.remaining -= 1;
                const advanced = await this.nextCategoryPage(observed, transport.key);
                if (!advanced || seen.has(advanced.url) || !categoryItemScopeUrlAllowed(advanced.url, descriptor)) break;
                seen.add(advanced.url);
                const scoped = scopeCategoryItems(advanced, { categoryKey: category.key, documentCategoryId: transport.key,
                    categoryBaseUrl: categoryUrl }, path);
                if (scoped) return scoped;
                observed = advanced;
            }
        }
        return null;
    }

    async readScopedCategoryPage(url, scope) {
        const ancestry = itemScopeAncestry(this.checkpoint, scope);
        if (!validateCategoryItemScope(scope, ancestry) || scope.categoryKey !== this.officialCategoryId()
            || !categoryItemScopeUrlAllowed(url, scope)) {
            throw new CategoryNavigationError('category_scope_mismatch');
        }
        const page = await this.read(url, 'category-transport', scope.transportCategoryId);
        const scoped = scopeCategoryItems(page, { categoryKey: scope.categoryKey, documentCategoryId: scope.transportCategoryId,
            categoryBaseUrl: scope.categoryBaseUrl, firstPage: false, previousScope: scope }, ancestry);
        if (!scoped) throw new CategoryNavigationError('category_scope_mismatch');
        return scoped;
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
            let url = '';
            let landingAttempted = false;
            this.prepareOfficialNavigation();
            const rejectedRoutes = new Map();
            const knownPage = await this.readKnownCategory(category.key, null, rejectedRoutes);
            if (knownPage) return { ...knownPage, categories: [], hasChildren: false };
            const attemptedRoutes = new Set(rejectedRoutes.keys());
            const ancestorBudget = { remaining: 10 };
            const navigationInterrupted = error => error.name === 'AbortError' || error.waitingLogin || [401, 403, 429].includes(error.status);
            const recoverable = error => !navigationInterrupted(error)
                && ['category_scope_mismatch', 'category_load_timeout', 'category_not_found'].includes(error.code);
            const canDiscover = error => !navigationInterrupted(error) && (recoverable(error)
                || ['category_link_missing', 'category_filters_unavailable'].includes(error.code));
            const validateRoutePage = (route, page) => {
                const marked = [route, page.url].some(value => value?.includes('?') && allowedUrl(value)
                    && new URL(value).searchParams.has('original_category_landing'));
                if (marked && !this.provenCategoryRoute(category.key, route, page)) {
                    throw new CategoryNavigationError('category_scope_mismatch', category.key);
                }
                const renamedCategoryBase = page.categoryDocumentIdentity && !page.categoryIdentityConflict
                    && page.effectiveCategoryId === category.key && page.categoryContext?.key === category.key
                    && neutralCategoryPage(page.categoryContext.baseUrl, page.url);
                if (!neutralCategoryPage(route, page.url) && !renamedCategoryBase) {
                    const error = new CategoryNavigationError('category_scope_mismatch', category.key);
                    error.categoryScopeMismatch = true;
                    error.categoryPage = page;
                    throw error;
                }
                return page;
            };
            const readRoute = async (route, strict = false) => {
                if (rejectedRoutes.has(route)) throw rejectedRoutes.get(route);
                attemptedRoutes.add(route);
                try {
                    const page = await this.read(route, strict ? 'category-transport' : 'category', strict ? category.key : '');
                    return validateRoutePage(route, page);
                }
                catch (error) {
                    if (error.categoryScopeMismatch || error.code === 'category_load_timeout') {
                        let fallbackPage = error.categoryPage;
                        try {
                            const reused = await this.tryCategoryItemScope(category, fallbackPage, route, { maxPages: 0 });
                            if (reused) return reused;
                        } catch (fallbackError) {
                            if (!recoverable(fallbackError)) throw fallbackError;
                            error.categoryFallbackCode = fallbackError.code;
                        }
                        const knownAncestor = fallbackPage?.categoryDocumentIdentity && !fallbackPage.categoryIdentityConflict
                            && !fallbackPage.authRequired && !fallbackPage.avantAuthRequired
                            && officialTaxonomyPath(this.checkpoint, category.key).slice(0, -1).some(node => node.key === fallbackPage.effectiveCategoryId);
                        const landing = new URL(route);
                        if (!landingAttempted && knownAncestor && !landing.search && !landing.hash && /\/\*_NoIndex_True\/?$/.test(landing.pathname)) {
                            landing.pathname = landing.pathname.replace(/\/\*_NoIndex_True\/?$/, '/');
                            if (landing.pathname !== '/' && neutralCategoryPage(route, landing.href) && !attemptedRoutes.has(landing.href)) {
                                landingAttempted = true; attemptedRoutes.add(landing.href);
                                try {
                                    // Removing a neutral suffix supplies only a route candidate.
                                    // The landing/template must still prove this leaf's own ID.
                                    return validateRoutePage(route, await this.read(landing.href, 'category-transport', category.key));
                                } catch (landingError) {
                                    if (!recoverable(landingError)) throw landingError;
                                    error.categoryLandingCode = landingError.code;
                                    fallbackPage = landingError.categoryPage || fallbackPage;
                                }
                            }
                        }
                        try {
                            const scoped = await this.tryCategoryItemScope(category, fallbackPage, route, { budget: ancestorBudget });
                            if (scoped) return scoped;
                        } catch (fallbackError) {
                            if (!recoverable(fallbackError)) throw fallbackError;
                            error.categoryFallbackCode = fallbackError.code;
                        }
                    }
                    throw error;
                }
            };
            let page;
            try {
                const permalink = publishedLeafPermalink(this.checkpoint, category.key);
                const entry = permalink && !attemptedRoutes.has(permalink) && unsupportedCategoryAlias(category.url) ? { ...category, url: permalink } : category;
                url = await this.officialCategoryUrl(entry);
                const fromDepartment = this.officialNavigation?.departmentUrl && unsupportedCategoryAlias(category.url);
                const recoveredAncestor = this.ancestorRecovery.size > 0 && this.findNavigationAncestor(category.key, key => this.ancestorRecovery.get(key));
                page = await readRoute(url, url === permalink || fromDepartment || recoveredAncestor);
                if (!recoveredAncestor && (page.empty || page.anuncios?.length)) await this.rememberCategoryRoute(category.key, url, page);
            }
            catch (error) {
                if (!canDiscover(error)) throw error;
                const ancestor = error.navigationDependencyKey && this.findNavigationAncestor(category.key, key => key === error.navigationDependencyKey);
                if (ancestor && !url) {
                    try {
                        if (await this.recoverCategoryAncestor(category, ancestor)) {
                            url = await this.officialCategoryUrl(category);
                            page = await readRoute(url, true);
                        }
                    } catch (ancestorError) {
                        if (!canDiscover(ancestorError)) throw ancestorError;
                        if (this.ancestorRecovery.get(ancestor.key)) error = ancestorError;
                    }
                }
                if (page) return { ...page, categories: [], hasChildren: false };
                const discovered = await this.discoverOfficialCategoryRoute(category, error);
                if (!discovered || attemptedRoutes.has(discovered.url)) throw error;
                try { page = await readRoute(discovered.url, true); }
                catch (routeError) {
                    if (error.discoveryOutcome) routeError.discoveryOutcome = error.discoveryOutcome;
                    throw routeError;
                }
                this.officialNavigation.routes.set(category.key, discovered.url);
            }
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

    async nextCategoryPage(current, transportCategoryId = '') {
        const itemScope = current.categoryItemScope;
        if (transportCategoryId && !officialTaxonomyPath(this.checkpoint, this.officialCategoryId()).slice(0, -1)
            .some(node => node.key === transportCategoryId)) throw new CategoryNavigationError('category_scope_mismatch');
        const leaf = this.checkpoint?.currentCategory;
        // A resumed official leaf already carries its ID even when its public route was discovered in a previous run.
        const fromOfficialLeaf = !itemScope && leaf && leaf.key === current.categoryKey && unsupportedCategoryAlias(leaf.url)
            && this.checkpoint.officialTaxonomy?.children?.[leaf.key]?.length === 0;
        const requiredDocumentId = transportCategoryId || (fromOfficialLeaf ? leaf.key : '');
        const next = canonicalUrl(current.nextUrl);
        if (next) {
            if (itemScope) return this.readScopedCategoryPage(next, itemScope);
            if ((transportCategoryId || this.officialCategoryId()) && !neutralCategoryPage(current.url, next)) throw new CategoryNavigationError('category_scope_mismatch');
            if (!transportCategoryId) this.assertCategoryDestination(current.url, next);
            const page = await this.read(next, requiredDocumentId ? 'category-transport' : 'category', requiredDocumentId);
            if ((transportCategoryId || this.officialCategoryId()) && !neutralCategoryPage(current.url, page.url)) throw new CategoryNavigationError('category_scope_mismatch');
            return page;
        }
        if (!current.hasNext) return null;
        if (typeof this.scripts.nextCategoryPageScript !== 'function') throw new Error('Controle de paginacao indisponivel.');
        // Visiting a seller moves the browser away from discovery. Restore the
        // saved category page before using its own JavaScript pagination control.
        return this.attempt(async avantState => {
            if (itemScope) await this.readScopedCategoryPage(current.url, itemScope);
            else if (requiredDocumentId) await this.read(current.url, 'category-transport', requiredDocumentId);
            else await this.read(current.url);
            const click = await this.browser.executeJavaScript(this.scripts.nextCategoryPageScript(current.url));
            if (!click || !click.clicked) {
                if (click && click.hasNext === false && ['last_page', 'no_next_page'].includes(click.reason)) return null;
                throw new Error('O controle da proxima pagina da categoria nao respondeu.');
            }
            let page;
            const destination = categoryPageWaiter(this.checkpoint && this.checkpoint.scope && this.checkpoint.scope.category ? current.url : null,
                itemScope ? itemScope.transportCategoryId : transportCategoryId || this.officialCategoryId());
            for (let poll = 0; poll < 34; poll += 1) {
                this.check();
                await this.wait(450);
                assertMarketplaceNavigation(this.browser, 'pagination');
                page = await this.browser.executeJavaScript(this.scripts.discoveryScript());
                this.check();
                if (page.authRequired || page.avantAuthRequired) {
                    if (currentPageRequiresLogin(page, this.browser)) return { ...page, authRequired: true,
                        reason: page.authReason || (page.authRequired ? 'ml_login_required' : 'avant_login_required') };
                    destination.observe(page.url || this.browser.getURL(), false, true);
                    continue;
                }
                const ready = canonicalUrl(page.url) !== canonicalUrl(current.url) && ((page.anuncios || []).length || page.empty);
                const loading = page.readyState === 'loading' || typeof this.browser.isLoading === 'function' && this.browser.isLoading();
                if (ready && !loading && !itemScope && (transportCategoryId || this.officialCategoryId())
                    && !neutralCategoryPage(current.url, page.url)) throw new CategoryNavigationError('category_scope_mismatch');
                if (itemScope && (!page.categoryDocumentIdentity || page.effectiveCategoryId !== itemScope.transportCategoryId)) continue;
                if (requiredDocumentId && (!page.categoryDocumentIdentity || page.effectiveCategoryId !== requiredDocumentId)) continue;
                if (destination.observe(page.url || this.browser.getURL(), ready, loading, page.effectiveCategoryId, page.categoryIdentityConflict)) {
                    if (page.categoryNotFound && !(page.anuncios || []).length) {
                        const error = new Error('O Mercado Livre nao reconheceu a pagina da categoria.');
                        error.categoryUnavailable = true;
                        throw error;
                    }
                    if (!itemScope) return page;
                    const scoped = scopeCategoryItems(page, { categoryKey: itemScope.categoryKey, documentCategoryId: itemScope.transportCategoryId,
                        categoryBaseUrl: itemScope.categoryBaseUrl, firstPage: false, previousScope: itemScope }, itemScopeAncestry(this.checkpoint, itemScope));
                    if (!scoped) throw new CategoryNavigationError('category_scope_mismatch');
                    return scoped;
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
            if (failure && failure.dependency_key && failure.dependency_key !== category.key) {
                const dependency = this.officialNavigation?.records?.get(failure.dependency_key);
                if (dependency) failure.dependency_name = dependency.nome;
            }
            if (!previous) this.checkpoint.incompleteCategories.push({ ...category, motivo: String(message || 'Coleta incompleta.'), ...(failure || {}) });
            else if (failure) {
                delete previous.dependency_name;
                delete previous.discovery_outcome;
                Object.assign(previous, failure);
            }
            else if (SALES_PENDING_WARNINGS.has(previous.motivo) && !SALES_PENDING_WARNINGS.has(message)) {
                previous.motivo = String(message || 'Coleta incompleta.');
            }
        }
        this.checkpoint.progress = progressState({ ...this.checkpoint.progress, warning: message });
    }

    detailRetryState() {
        const generation = Number(this.job.generation || 0);
        const saved = this.checkpoint.detailRetryState;
        if (!saved || ![1, 2].includes(saved.version) || saved.generation !== generation
            || !saved.blocked || typeof saved.blocked !== 'object' || Array.isArray(saved.blocked)) {
            this.checkpoint.detailRetryState = { version: 2, generation, blocked: {} };
        } else if (saved.version === 1) {
            for (const key of Object.keys(saved.blocked)) {
                if (saved.blocked[key] === 'seller_not_confirmed') saved.blocked[key] = 'legacy_seller_not_confirmed';
            }
            saved.version = 2;
        }
        return this.checkpoint.detailRetryState;
    }

    detailRetryKey(seller, item) {
        const id = String(item.id || '').replace(/-/g, '').toUpperCase();
        const key = /^MLB\d+$/.test(id) ? `id:${id}` : `url:${canonicalUrl(item.url)}`;
        // The seller is part of the identity; the checkpoint itself belongs to
        // one job/owner. JSON encoding also keeps arbitrary names off prototypes.
        return JSON.stringify([seller.key, key]);
    }

    detailRetryBlocked(seller, item) {
        const reason = this.detailRetryState().blocked[this.detailRetryKey(seller, item)];
        return reason === 'legacy_seller_not_confirmed' ? !this.canRecheckLegacySeller(seller, item) : DETAIL_RETRY_BLOCKS.has(reason);
    }

    canRecheckLegacySeller(seller, item) {
        if (this.detailRetryState().blocked[this.detailRetryKey(seller, item)] !== 'legacy_seller_not_confirmed'
            || item.rankable || item.catalogo || !allowedUrl(item.url) || conflictingSeller(seller, item)
            || conflictingSeller(seller, { id: item.avant_seller_id, perfilUrl: item.avant_seller_perfil_url })) return false;
        // A catalog wid identifies an offer but cannot attribute product estimates to its seller.
        const url = new URL(item.url);
        const direct = /^\/MLB-(\d{6,})(?:-|\/|$)/i.exec(url.pathname);
        if (url.hostname !== 'produto.mercadolivre.com.br' || !direct || `MLB${direct[1]}` !== item.id) return false;
        const parameters = new URLSearchParams(url.search);
        for (const [key, value] of new URLSearchParams(url.hash.slice(1))) parameters.append(key, value);
        return ![...parameters].some(([key, value]) => /^(wid|item_id)$/i.test(key) && value.toUpperCase() !== item.id
            || /^pdp_filters$/i.test(key));
    }

    needsLegacyAvantRefresh(item) {
        const version = this.collector.AVANT_METRICS_VERSION;
        return this.refreshLegacyAvant && !!version && hasLegacyAvantMetrics(item, version);
    }

    areAvantMetricsComplete(item) {
        return !this.needsLegacyAvantRefresh(item) && this.collector.isComplete(item);
    }

    canConsultDetail(seller, item, enrichComplete = true) {
        const metricsVersion = this.collector.AVANT_METRICS_VERSION;
        const needsPanel = enrichComplete && metricsVersion && item.avant_metrics_version === metricsVersion
            && !item.avant_detail_observed && (item.media_mensal_rotulo !== 'vendas mensais' || !item.data_criacao);
        return (!this.areAvantMetricsComplete(item) || needsPanel) && allowedUrl(item.url) && !this.detailRetryBlocked(seller, item);
    }

    detailProgressCounts(seller, items) {
        const pending = items.filter(item => !this.areAvantMetricsComplete(item));
        const blocked = pending.filter(item => !allowedUrl(item.url) || this.detailRetryBlocked(seller, item)).length;
        const loading = pending.filter(item => String(item.erro || '').includes(AVANT_PANEL_LOADING_MESSAGE)).length;
        return `validados: ${items.length - pending.length}; pendentes: ${pending.length}; sem reconsulta automatica: ${blocked}`
            + (loading ? `; painel do Avant ainda carregando ao encerrar a consulta: ${loading}` : '');
    }

    async detailBrowserPool(size, signal) {
        const count = typeof this.createDetailBrowser === 'function' ? Math.min(MAX_CONCURRENT_DETAILS, size) : 1;
        while (this.detailBrowsers.length < count) {
            this.check(signal);
            const browser = this.createDetailBrowser(this.detailBrowsers.length);
            if (!browser || this.detailBrowsers.includes(browser)) throw new Error('Navegador de consulta indisponivel.');
            this.detailBrowsers.push(browser);
            if (typeof browser.prepare === 'function') await this.nameStep(() => browser.prepare(), signal);
        }
        return this.detailBrowsers.slice(0, count);
    }

    async collectSellerDetail(seller, items, index, browser, signal, onLoadingChange) {
        this.check(signal);
        const item = items[index];
        const detailUrl = canonicalUrl(item.url);
        const matchesItem = candidate => !!candidate && (item.id
            ? String(candidate.id || '').replace(/-/g, '').toUpperCase() === String(item.id).replace(/-/g, '').toUpperCase()
            : canonicalUrl(candidate.url) === detailUrl);
        const controller = new AbortController();
        const abort = () => controller.abort();
        signal.addEventListener('abort', abort, { once: true });
        let timer, latestPartial = null, collectionTimedOut = false, active = true, usedPartial = false;
        let panelLoading = false;
        let releaseRecommendationMedia;
        const restoreRecommendationMedia = () => {
            const release = releaseRecommendationMedia;
            releaseRecommendationMedia = undefined;
            release?.();
        };
        const armTimeout = (milliseconds, phase) => {
            if (!active || controller.signal.aborted) return;
            clearTimeout(timer);
            timer = setTimeout(() => {
                collectionTimedOut = phase !== 'navigation';
                controller.abort();
                if (typeof browser.stop === 'function') browser.stop();
            }, milliseconds);
        };
        let waitingForPanel = false;
        const setWaitingForPanel = waiting => {
            if (waitingForPanel === waiting) return;
            waitingForPanel = waiting;
            if (waiting) clearTimeout(timer);
            else armTimeout(this.detailTimeoutMs, 'collection');
            onLoadingChange?.(waiting);
        };
        // Legacy collectors retain a bounded call. Current collectors give
        // navigation and the loaded Avant panel separate time budgets.
        armTimeout(this.detailTimeoutMs, 'collection');
        try {
            let detail;
            try {
                detail = await this.attempt(async () => {
                    let attemptActive = true;
                    latestPartial = null;
                    const alive = () => attemptActive && active && !controller.signal.aborted;
                    const capture = partial => {
                        if (!alive()) return false;
                        const invalid = !partial || partial.authRequired || partial.avantAuthRequired
                            || ['avant_login_required', 'ml_login_required', 'ml_verification_required', 'page_changed', 'invalid_page_result'].includes(partial.reason);
                        latestPartial = invalid ? null : clone(partial);
                        setWaitingForPanel(!invalid && partial.reason === 'partial' && partial.avantPanelLoading === true
                            && Array.isArray(partial.anuncios) && (partial.anuncios.some(matchesItem)
                                || !partial.anuncios.length && matchesItem(partial.avantLoadingOffer)));
                        return !invalid;
                    };
                    try {
                        const observed = await this.collector.collectDetail({
                            browser, signal: controller.signal, url: detailUrl,
                            expectedItem: { id: item.id || '', url: item.url },
                            seller: { ...seller, sellerId: seller.id, vendedor: seller.nome },
                            waitForLoadingPanel: true,
                            timeoutMs: this.detailTimeoutMs, missingWaitMs: Math.min(15000, this.detailTimeoutMs), wait: this.wait,
                            onNavigationStart: () => {
                                if (!alive()) return;
                                latestPartial = null;
                                restoreRecommendationMedia();
                                releaseRecommendationMedia = browser.deferRecommendationMedia?.(detailUrl, item.id || '');
                                armTimeout(this.detailNavigationTimeoutMs, 'navigation');
                            },
                            onNavigationComplete: () => {
                                if (!alive()) return;
                                assertMarketplaceNavigation(browser, 'product');
                                armTimeout(this.detailTimeoutMs, 'collection');
                            },
                            onPartial: capture
                        });
                        // Authentication or a changed document also vetoes
                        // metrics observed before the terminal result.
                        const valid = capture(observed);
                        return valid ? observed : { ...observed, anuncios: [] };
                    } finally {
                        attemptActive = false;
                        setWaitingForPanel(false);
                        restoreRecommendationMedia();
                    }
                }, controller.signal, browser);
            } catch (error) {
                this.check(signal);
                if (!collectionTimedOut || error.name !== 'AbortError' || !latestPartial) throw error;
                detail = latestPartial;
                usedPartial = true;
            }
            this.check(signal);
            const found = (detail.anuncios || []).find(matchesItem);
            panelLoading = !!found && detail.avantPanelLoading === true;
            const avantSource = value => /^(?:avant|avant_pro|avant-pro)$/.test(String(value || '').toLowerCase())
                || String(value || '').toLowerCase().startsWith('avantpro');
            if (found && found.seller_confirmed === true && sameSeller(seller, found)
                && (!(found.avant_seller_id || found.avant_seller_perfil_url) || sameSeller(seller, {
                    id: found.avant_seller_id, perfilUrl: found.avant_seller_perfil_url
                }))) {
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
                const currentVersion = this.collector.AVANT_METRICS_VERSION;
                if (this.needsLegacyAvantRefresh(item) && found.avant_metrics_version !== currentVersion) return;
                if (currentVersion && found.avant_metrics_version === currentVersion && item.avant_metrics_version !== currentVersion) {
                    const settled = !usedPartial && !['partial', 'timeout', 'page_changed', 'invalid_page_result', 'no_listings_detected'].includes(detail.reason);
                    const showedPanelMetrics = !!(found.vendas_exibicao || found.data_criacao || found.media_vendas_mensal_exibicao);
                    if (freshComplete || !this.needsLegacyAvantRefresh(item) || settled && showedPanelMetrics) {
                        // Never complete a fresh observation with values derived by the old parser.
                        for (const field of AVANT_METRIC_FIELDS) {
                            if (!Object.hasOwn(found, field)) delete merged[field];
                        }
                        merged.rankable = freshComplete;
                        items[index] = merged;
                    }
                    if (found.status === 'catalogo_sem_evidencia_vendedor' && settled && showedPanelMetrics) return 'catalogue_proof_missing';
                    return;
                }
                let reusedUnconfirmed = false;
                // A detail panel may expose only the metric missing from the card.
                // Keep existing values visible, but only reuse proven provenance
                // to construct a complete rank from separate observations.
                for (const [value, sources] of [
                    ['vendas', ['vendasFonte', 'vendas_fonte', 'vendas_rotulo', 'vendas_exibicao', 'vendas_estimadas', 'vendas_limite_inferior']],
                    ['media_mensal', ['media_mensal_fonte', 'media_mensal_rotulo', 'media_vendas_mensal_exibicao']],
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
            // Only a settled response for this exact offer can stop automatic
            // retries. A timeout/partial may still acquire seller proof later.
            if (found && !this.collector.isComplete(items[index]) && !usedPartial
                && !['partial', 'timeout', 'page_changed', 'invalid_page_result', 'no_listings_detected'].includes(detail.reason)) {
                // A missing/partial Avant widget can carry these same statuses.
                // Classify only its fresh metrics, never the merged card or rank.
                const readAvantMetrics = avantSource(found.vendasFonte || found.vendas_fonte)
                    && this.collector.isComplete({ ...found, rankable: true });
                if (readAvantMetrics && found.status === 'catalogo_sem_evidencia_vendedor') return 'catalogue_proof_missing';
                if (conflictingSeller(seller, found) || conflictingSeller(seller, {
                    id: found.avant_seller_id, perfilUrl: found.avant_seller_perfil_url
                }) || readAvantMetrics && (found.status === 'vendedor_nao_confirmado' || found.seller_confirmed === false)) {
                    return 'seller_not_confirmed';
                }
            }
        } catch (error) {
            this.check(signal);
            if (isMarketplaceAccessPause(error) || error.waitingLogin || error.avantSessionRejected || error.status === 401 || error.status === 403 || error.status === 404) throw error;
            // A bounded unavailable detail keeps the original card's evidence.
        } finally {
            active = false;
            clearTimeout(timer);
            signal.removeEventListener('abort', abort);
            restoreRecommendationMedia();
            const current = items[index];
            if (panelLoading && !this.collector.isComplete(current)
                && !String(current.erro || '').includes(AVANT_PANEL_LOADING_MESSAGE)) {
                current.erro = [current.erro, AVANT_PANEL_LOADING_MESSAGE].filter(Boolean).join(' ');
            }
        }
    }

    async collectDetailRound(seller, collection, legacySellerOnly = false) {
        const items = collection.result.anuncios;
        const enrichComplete = collection.detailRound === 0;
        // Keep out-of-order completions until the contiguous cursor catches up.
        // Unavailable details also count as attempted within this round.
        const completed = new Set((Array.isArray(collection.completedIndices) ? collection.completedIndices : []).filter(index =>
            Number.isSafeInteger(index) && index >= collection.index && index < items.length));
        const advance = () => {
            while (collection.index < items.length && (completed.has(collection.index)
                || !this.canConsultDetail(seller, items[collection.index], enrichComplete))) {
                completed.delete(collection.index);
                collection.index += 1;
            }
            collection.completedIndices = [...completed].sort((a, b) => a - b);
        };
        advance();
        const pending = [];
        for (let index = 0; index < items.length; index += 1) {
            if (this.canRecheckLegacySeller(seller, items[index]) || (!legacySellerOnly && index >= collection.index
                && !completed.has(index) && this.canConsultDetail(seller, items[index], enrichComplete))) pending.push(index);
        }
        if (!pending.length) {
            this.setProgress('collecting', `Sem novas consultas automaticas de ${seller.nome || seller.id || 'vendedor'}; ${this.detailProgressCounts(seller, items)}.`);
            return false;
        }
        const controller = new AbortController();
        const abort = () => controller.abort();
        this.controller.signal.addEventListener('abort', abort, { once: true });
        let cursor = 0, finished = 0, firstError;
        try {
            const browsers = await this.detailBrowserPool(pending.length, controller.signal);
            const loadingPanels = new Set();
            const progress = () => {
                if (controller.signal.aborted) return;
                const label = collection.detailRound ? 'Reconsultando apenas falhas temporarias' : 'Consultando anuncios pendentes';
                const waiting = loadingPanels.size ? `Aguardando o Avant Pro carregar dados em ${loadingPanels.size} de ${browsers.length} consultas. ` : '';
                this.setProgress('collecting', `${waiting}${label} de ${seller.nome || seller.id || 'vendedor'}: consultados: ${finished} de ${pending.length} nesta tentativa; ${this.detailProgressCounts(seller, items)}; ate ${browsers.length} consultas simultaneas; tentativa ${collection.detailRound + 1} de ${this.detailRetryDelaysMs.length + 1}...`);
            };
            const work = async browser => {
                try {
                    while (cursor < pending.length) {
                        this.check(controller.signal);
                        const index = pending[cursor++];
                        progress();
                        const legacy = this.canRecheckLegacySeller(seller, items[index]);
                        const blockedReason = await this.collectSellerDetail(seller, items, index, browser, controller.signal, waiting => {
                            if (waiting) loadingPanels.add(index);
                            else loadingPanels.delete(index);
                            progress();
                        });
                        this.check(controller.signal);
                        const blocks = this.detailRetryState().blocked, key = this.detailRetryKey(seller, items[index]);
                        if (DETAIL_RETRY_BLOCKS.has(blockedReason)) blocks[key] = blockedReason;
                        else if (legacy && !this.collector.isComplete(items[index])) blocks[key] = 'seller_not_confirmed';
                        else if (legacy) delete blocks[key];
                        if (index >= collection.index) completed.add(index);
                        finished += 1;
                        progress();
                        advance();
                        await this.save();
                    }
                } catch (error) {
                    if (!firstError) firstError = error;
                    controller.abort();
                    this.stopBrowsers();
                }
            };
            // All workers must settle before run() saves a terminal status or a
            // later seller can reuse these windows and mutate the checkpoint.
            await Promise.all(browsers.map(work));
            if (firstError) throw firstError;
            this.check(controller.signal);
            return true;
        } finally {
            this.controller.signal.removeEventListener('abort', abort);
        }
    }

    async complementSellerListings(seller, result, allAdsUrl, discoverySeed = null, legacySellerOnly = false) {
        if (typeof this.collector.collectDetail !== 'function' || typeof this.collector.isComplete !== 'function') return result;
        const saved = this.checkpoint.sellerCollection;
        const collection = saved && saved.sellerKey === seller.key && saved.url === allAdsUrl
            ? saved : { sellerKey: seller.key, url: allAdsUrl, result: clone(result), index: 0,
                ...(discoverySeed ? { categoryKey: this.checkpoint.currentCategory.key,
                    seedKey: String(discoverySeed.id || canonicalUrl(discoverySeed.url)), seed: clone(discoverySeed) } : {}) };
        if (!Array.isArray(collection.result.anuncios)) return result;
        if (legacySellerOnly && !Array.isArray(collection.legacySellerProofKeys)) {
            collection.legacySellerProofKeys = collection.result.anuncios.filter(item => this.canRecheckLegacySeller(seller, item))
                .map(item => this.detailRetryKey(seller, item));
        }
        const generation = Number(this.job.generation || 0);
        const metricsVersion = this.collector.AVANT_METRICS_VERSION;
        const refreshLegacyCursor = !!(metricsVersion && collection.detailMetricsVersion !== metricsVersion
            && collection.result.anuncios.some(item => this.needsLegacyAvantRefresh(item)));
        if (collection.detailGeneration !== generation || refreshLegacyCursor) {
            // A manual recovery is a fresh consultation, including an exhausted
            // collection preserved before publication. Completed ads still skip.
            // Legacy generation-zero cursors retain their interrupted position.
            if (Number.isSafeInteger(collection.detailGeneration) || generation > 0 || refreshLegacyCursor) {
                collection.index = 0;
                collection.detailRound = 0;
                collection.completedIndices = [];
                delete collection.retryDelayMs;
            }
            collection.detailGeneration = generation;
        }
        if (metricsVersion) collection.detailMetricsVersion = metricsVersion;
        if (!Number.isSafeInteger(collection.detailRound) || collection.detailRound < 0
            || collection.detailRound > this.detailRetryDelaysMs.length) collection.detailRound = 0;
        this.checkpoint.sellerCollection = collection;
        await this.save();
        const items = collection.result.anuncios;
        let navigated = false;
        while (true) {
            this.check();
            if (collection.retryDelayMs !== undefined) {
                const pending = items.filter(item => this.canConsultDetail(seller, item, false)).length;
                if (pending) {
                    this.setProgress('collecting', `Aguardando para reconsultar ${pending} anuncio(s) com falha temporaria de ${seller.nome || seller.id || 'vendedor'}; ${this.detailProgressCounts(seller, items)}; tentativa ${collection.detailRound + 1} de ${this.detailRetryDelaysMs.length + 1}...`);
                    await this.nameStep(() => this.wait(this.detailRetryDelaysMs[collection.detailRound - 1] || 0), this.controller.signal);
                }
                this.check();
                delete collection.retryDelayMs;
                await this.save();
            }
            navigated = await this.collectDetailRound(seller, collection, legacySellerOnly) || navigated;
            if (legacySellerOnly || collection.detailRound >= this.detailRetryDelaysMs.length
                || !items.some(item => this.canConsultDetail(seller, item, false))) break;
            // An empty successful response also needs a fresh consultation. Save
            // the next round before waiting so pause/login keeps its exact cursor.
            collection.retryDelayMs = this.detailRetryDelaysMs[collection.detailRound];
            collection.detailRound += 1;
            collection.index = 0;
            collection.completedIndices = [];
            await this.save();
        }
        this.check();
        if (navigated) await this.read(allAdsUrl, 'first-page');
        const missing = items.filter(item => !this.areAvantMetricsComplete(item)).length;
        const previousReason = collection.result.reason || 'complete';
        const reason = !missing && ['complete', 'avant_data_incomplete'].includes(previousReason)
            ? 'complete' : missing && previousReason === 'complete' ? 'avant_data_incomplete' : previousReason;
        const rechecked = legacySellerOnly ? new Set(collection.legacySellerProofKeys) : null;
        const preserved = new Map(items.filter(item => this.needsLegacyAvantRefresh(item)
            || rechecked && !rechecked.has(this.detailRetryKey(seller, item)))
            .map(item => [this.detailRetryKey(seller, item), item]));
        const anuncios = typeof this.collector.finalizeListings === 'function'
            ? this.collector.finalizeListings(items).map(item => preserved.get(this.detailRetryKey(seller, item)) || item)
            : items;
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
                const pendingIndex = saved.result.anuncios.findIndex(item => !this.areAvantMetricsComplete(item));
                saved.index = pendingIndex < 0 ? saved.result.anuncios.length : pendingIndex;
            }
            await this.save();
        }
        // These keys mean visited in this interrupted round, never resolved.
        const done = new Set(this.checkpoint.salesRecoverySellerKeys);
        const legacySellers = new Set();
        for (const [key, reason] of Object.entries(this.detailRetryState().blocked)) {
            if (reason === 'legacy_seller_not_confirmed') {
                try { legacySellers.add(JSON.parse(key)[0]); } catch (_error) { /* Malformed historical key. */ }
            }
        }
        const sellers = [...(response.items || [])];
        if (saved) {
            const activeIndex = sellers.findIndex(seller => (seller.seller_key || seller.key) === saved.sellerKey);
            if (activeIndex > 0) sellers.unshift(...sellers.splice(activeIndex, 1));
        }
        for (const previous of sellers) {
            this.check();
            const key = previous.seller_key || previous.key;
            const legacySellerOnly = done.has(key);
            // Publication can be interrupted after the final consultation is checkpointed.
            const republish = this.checkpoint.sellerCollection?.sellerKey === key;
            if (!key || legacySellerOnly && !legacySellers.has(key) && !republish) continue;
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
                if (legacySellerOnly && !republish && !anuncios.some(item => this.canRecheckLegacySeller(seller, item))) continue;
                if (!republish && anuncios.every(item => this.areAvantMetricsComplete(item))
                    && !anuncios.some(item => this.canConsultDetail(seller, item))) {
                    done.add(key);
                    this.checkpoint.salesRecoverySellerKeys = [...done];
                    await this.save();
                    continue;
                }
                if (!republish && !anuncios.some(item => this.canConsultDetail(seller, item))) {
                    // Published ads are normalized by the backend. Keep terminal
                    // proof failures in the private checkpoint across app restarts.
                    this.checkpoint.salesRecoveryPending = true;
                    this.setProgress('collecting', `Sem novas consultas automaticas de ${seller.nome || seller.id || 'vendedor'}; ${this.detailProgressCounts(seller, anuncios)}.`);
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
                }, allAdsUrl, null, legacySellerOnly);
                this.check();
                await this.publishSeller(seller, result, true);
                delete this.checkpoint.sellerCollection;
                if (result.incomplete) this.checkpoint.salesRecoveryPending = true;
            } catch (error) {
                this.check();
                if (isMarketplaceAccessPause(error) || error.waitingLogin || error.avantSessionRejected || error.status === 401 || error.status === 403) throw error;
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
        delete this.checkpoint.legacyAvantMetricsPending;
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
            const namedSellerPage = seller.perfilUrl.includes('/pagina/');
            if (profile.seller_identity_conflict || (confirmed && conflictingSeller(seller, confirmed))
                || (namedSellerPage && (!confirmed || !sameSeller(seller, confirmed)))) {
                throw new Error('O perfil retornou um vendedor diferente do anuncio consultado.');
            }
            if (confirmed && sameSeller(seller, confirmed)) seller = { ...seller, ...confirmed, key: seller.key, id: confirmed.id || seller.id, nome: confirmed.nome || seller.nome };
            const allAdsUrl = canonicalUrl(profile.allAdsUrl || page.allAdsUrl);
            if (!allAdsUrl) throw new Error('A opcao ver todos os anuncios deste vendedor nao foi encontrada.');
            const allAdsPage = await this.read(allAdsUrl, 'first-page');
            const listingSeller = sellerFromPage(allAdsPage);
            if (allAdsPage.seller_identity_conflict || (listingSeller && conflictingSeller(seller, listingSeller))
                || (namedSellerPage && (!listingSeller || !sameSeller(seller, listingSeller)))) {
                throw new Error('A listagem retornou anuncios de outro vendedor.');
            }
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
            if (typeof this.browser.prepare === 'function') await this.browser.prepare({ collectVisible: this.checkpoint.collectVisible === true });
            this.check();
            delete this.checkpoint.marketplaceAccess;
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
            if (this.checkpoint.legacyAvantMetricsOnly) {
                delete this.checkpoint.legacyAvantMetricsOnly;
                this.job.status = 'cancelled';
                this.setProgress('cancelled', this.job.legacyAvantMetricsPending
                    ? 'Reprocessamento encerrado com anuncios ainda pendentes.'
                    : 'Reprocessamento das metricas concluido.');
                await this.save();
                return;
            }
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
                            if (isMarketplaceAccessPause(error) || error.waitingLogin || error.avantSessionRejected || error.name === 'AbortError' || error.status === 401 || error.status === 403) throw error;
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
                            hasNext: !!page.hasNext, anuncios: page.anuncios || [], index: 0,
                            ...(page.categoryItemScope ? { categoryItemScope: clone(page.categoryItemScope) } : {})
                        };
                        this.checkpoint.categorySeenPages = [this.checkpoint.page.url];
                        this.job.counters.pages = Number(this.job.counters.pages || 0) + 1;
                        await this.save();
                    } catch (err) {
                        if (isMarketplaceAccessPause(err) || err.avantSessionRejected || err.waitingLogin || err.name === 'AbortError' || err.status === 401 || err.status === 403) throw err;
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
                            if (this.salesRecoveryDeferred || isMarketplaceAccessPause(err) || err.avantSessionRejected || err.waitingLogin || err.name === 'AbortError' || err.status === 401 || err.status === 403) throw err;
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
                                anuncios: page.anuncios || [], index: 0,
                                ...(page.categoryItemScope ? { categoryItemScope: clone(page.categoryItemScope) } : {}) };
                            this.job.counters.pages = Number(this.job.counters.pages || 0) + 1;
                            await this.save();
                            continue;
                        } catch (err) {
                            if (isMarketplaceAccessPause(err) || err.avantSessionRejected || err.waitingLogin || err.name === 'AbortError' || err.status === 401 || err.status === 403) throw err;
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
                        this.checkpoint.page = { categoryKey: this.officialCategoryId(), url: loaded, nextUrl: canonicalUrl(page.nextUrl), hasNext: !!page.hasNext, anuncios: page.anuncios || [], index: 0,
                            ...(page.categoryItemScope ? { categoryItemScope: clone(page.categoryItemScope) } : {}) };
                        this.job.counters.pages = Number(this.job.counters.pages || 0) + 1;
                        await this.save();
                    } catch (err) {
                        if (isMarketplaceAccessPause(err) || err.avantSessionRejected || err.waitingLogin || err.name === 'AbortError' || err.status === 401 || err.status === 403) throw err;
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
            const browserMessages = {
                research_browser_closed: 'O Chrome da pesquisa foi fechado. Clique em Retomar para continuar.',
                research_chrome_unavailable: 'Nao foi possivel iniciar o Chrome da pesquisa. Confira a instalacao e feche outra janela deste perfil.',
                avant_extension_missing: 'avant_extension_missing',
                research_chrome_manual_open: 'Feche a janela do Chrome dedicada ao login e clique em Retomar.'
            };
            const marketplacePaused = isMarketplaceAccessPause(err);
            const browserPaused = ['research_browser_closed', 'research_chrome_manual_open'].includes(err.code);
            this.job.status = this.stopStatus || (marketplacePaused || browserPaused ? 'paused'
                : err.waitingLogin || err.status === 401 || err.status === 403 ? 'waiting_login' : 'failed');
            if (marketplacePaused) {
                this.checkpoint.marketplaceAccess = { status: err.status, stage: err.stage, retryAt: err.retryAt };
                if (err.status === 403 && err.publicUrl) this.checkpoint.marketplaceAccess.url = err.publicUrl;
            }
            if (!this.stopStatus) {
                const browserMessage = marketplacePaused ? err.message : Object.hasOwn(browserMessages, err.code) ? browserMessages[err.code] : '';
                this.setProgress(this.job.status, browserMessage || (err.waitingLogin ? err.message
                    : this.job.status === 'waiting_login' ? 'Atualize o login e clique em Retomar.' : 'Pesquisa interrompida; o progresso foi preservado.'));
            }
            try { await this.save(); } catch (_saveError) { this.emit(this.snapshot()); }
        } finally {
            await Promise.allSettled(this.detailBrowsers.map(browser =>
                typeof browser.finish === 'function' ? browser.finish(this.job.status) : undefined));
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
                    if (isMarketplaceAccessPause(error) || error.waitingLogin || error.avantSessionRejected || error.name === 'AbortError' || error.status === 401 || error.status === 403) throw error;
                    this.incomplete('Um anuncio ou vendedor nao pode ser consultado; a pesquisa continuara.');
                }
                recovery.failedSeedsAfterKey = seed.key;
                await this.save();
            }
        }
    }
}

module.exports = { PesquisaMercadoRunner, MAX_CONCURRENT_DETAILS, assertMarketplaceNavigation, assertMarketplaceRetryAllowed, marketplacePause, publicMarketplacePage, allowedUrl, canonicalUrl, sellerFromPage, cleanSellerName, sameSeller, enrichSellerName, normalizeCategory, sameCategoryPage, verifiedCategoryPage, categoryIdFromUrl, categoryPageWaiter, currentPageRequiresLogin, validateCheckpointTaxonomy };
