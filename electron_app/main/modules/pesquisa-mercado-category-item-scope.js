'use strict';

const ID = /^MLB\d+$/;
const MAX_ITEMS = 10000;
const SCOPE_FIELDS = ['version', 'categoryKey', 'transportCategoryId', 'transportBaseUrl', 'categoryBaseUrl'];
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);

function listingUrl(value) {
    if (typeof value !== 'string' || value.length > 4000 || /[\\\u0000-\u0020]/.test(value)) return null;
    try {
        const parsed = new URL(value);
        if (parsed.protocol !== 'https:' || parsed.hostname !== 'lista.mercadolivre.com.br'
            || parsed.username || parsed.password || parsed.port && parsed.port !== '443' || parsed.search || parsed.hash
            || /%(?:2f|5c|00|25)/i.test(parsed.pathname)) return null;
        return parsed;
    } catch (_error) { return null; }
}

// An observed official route can carry the site's neutral listing suffix. Its
// base still has to be a category path, never an alias, item, seller or query.
function observedBase(value) {
    const parsed = listingUrl(value);
    if (!parsed) return '';
    const path = parsed.pathname.replace(/\/(?:\*_NoIndex_True|_NoIndex_True|\*)\/?$/, '').replace(/\/+$/, '');
    let decoded;
    try { decoded = decodeURIComponent(path); } catch (_error) { return ''; }
    if (!decoded || /[_*$?#\u0000-\u0020]/.test(decoded)
        || /\/(?:p|up|perfil|loja|stores?|vendedor|categorias|busca|search)(?:\/|$)|\/MLB-?\d+(?:\/|$)/i.test(decoded)) return '';
    parsed.pathname = path + '/';
    return parsed.href;
}

function ancestryNodes(ancestry) {
    if (!Array.isArray(ancestry) || !ancestry.length || ancestry.length > 32) return null;
    const seen = new Set();
    let previousBase = '';
    for (const node of ancestry) {
        if (!object(node) || typeof node.key !== 'string' || !ID.test(node.key) || seen.has(node.key)) return null;
        seen.add(node.key);
        const base = observedBase(node.url);
        if (base) {
            if (previousBase && (!base.startsWith(previousBase) || base === previousBase)) return null;
            previousBase = base;
        }
    }
    return ancestry;
}

function validateCategoryItemScope(scope, ancestry) {
    if (!object(scope) || Object.keys(scope).length !== SCOPE_FIELDS.length
        || Object.keys(scope).some(key => !SCOPE_FIELDS.includes(key)) || scope.version !== 1
        || typeof scope.categoryKey !== 'string' || !ID.test(scope.categoryKey)
        || typeof scope.transportCategoryId !== 'string' || !ID.test(scope.transportCategoryId)) return false;
    const nodes = ancestryNodes(ancestry);
    if (!nodes || nodes.at(-1).key !== scope.categoryKey) return false;
    const transportIndex = nodes.findIndex(node => node.key === scope.transportCategoryId);
    if (transportIndex < 0 || transportIndex >= nodes.length - 1) return false;
    const transport = observedBase(nodes[transportIndex].url), leaf = observedBase(nodes.at(-1).url);
    if (!transport || !leaf || scope.transportBaseUrl !== transport || scope.categoryBaseUrl !== leaf) return false;
    const parent = new URL(transport), child = new URL(leaf);
    return parent.origin === child.origin && child.pathname.startsWith(parent.pathname) && child.pathname !== parent.pathname;
}

function scopedPageUrl(value, transportBaseUrl) {
    const page = listingUrl(value), base = listingUrl(transportBaseUrl);
    if (!page || !base || page.origin !== base.origin) return false;
    const root = base.pathname.replace(/\/+$/, ''), path = page.pathname.replace(/\/+$/, '');
    if (path === root) return true;
    let suffix;
    if (path.startsWith(root + '/')) suffix = path.slice(root.length + 1);
    else if (path.startsWith(root + '_')) suffix = path.slice(root.length);
    else return false;
    if (suffix.startsWith('*')) suffix = suffix.slice(1);
    if (!suffix) return path === root + '/*';
    const tokens = suffix.match(/_Desde_[1-9]\d{0,8}|_NoIndex_True/g) || [];
    return tokens.length > 0 && tokens.length <= 2 && tokens.join('') === suffix
        && new Set(tokens.map(token => token.startsWith('_Desde_') ? 'page' : 'noindex')).size === tokens.length;
}

function categoryItemScopeUrlAllowed(value, scope) {
    return object(scope) && scope.version === 1 && observedBase(scope.transportBaseUrl) === scope.transportBaseUrl
        && scopedPageUrl(value, scope.transportBaseUrl);
}

function itemUrlMatches(value, expectedId) {
    if (typeof value !== 'string' || value.length > 4000 || /[\\\u0000-\u0020]/.test(value)) return false;
    try {
        const url = new URL(value);
        if (url.protocol !== 'https:' || !/(^|\.)mercadolivre\.com\.br$/i.test(url.hostname)
            || url.username || url.password || url.port && url.port !== '443'
            || !/\/MLB-\d+(?=-|\/|$)|\/(?:p|up)\/MLBU?\d+(?=\/|$)/i.test(url.pathname)) return false;
        const ids = new Set();
        for (const match of url.pathname.matchAll(/\/(MLB-\d+)(?=-|\/|$)/gi)) ids.add(match[1].replace('-', '').toUpperCase());
        const readIds = params => {
            for (const [key, id] of params) {
                if (/token|password|senha|secret|authorization|api[_-]?key|cookie/i.test(key)) return false;
                if (!/^(?:wid|item_id)$/i.test(key)) continue;
                if (!/^MLB\d+$/i.test(id)) return false;
                ids.add(id.toUpperCase());
            }
            return true;
        };
        if (!readIds(url.searchParams)) return false;
        const fragment = url.hash.slice(1);
        if (fragment) {
            if (!readIds(new URLSearchParams(fragment))) return false;
            try {
                const decoded = decodeURIComponent(fragment);
                if (decoded !== fragment && !readIds(new URLSearchParams(decoded))) return false;
            } catch (_error) { return false; }
        }
        return ids.size === 1 && ids.has(expectedId);
    } catch (_error) { return false; }
}

/**
 * Filter a confirmed ancestor document by coupled item/category metadata. The
 * document's identity remains the ancestor's; the descriptor records the exact
 * requested leaf separately and must be revalidated on every continuation.
 */
function scopeCategoryItems(page, options, ancestry) {
    if (!object(page) || !object(options) || typeof options.categoryKey !== 'string' || !ID.test(options.categoryKey)
        || typeof options.documentCategoryId !== 'string' || !ID.test(options.documentCategoryId)
        || page.categoryDocumentIdentity !== true || page.categoryIdentityConflict === true
        || page.effectiveCategoryId !== options.documentCategoryId
        || page.documentCategoryId !== undefined && page.documentCategoryId !== options.documentCategoryId
        || page.authRequired || page.avantAuthRequired || page.categoryTrends || page.categoryNotFound
        || page.sellerId || page.perfilUrl || page.allAdsUrl || page.seller_identity_conflict
        || !Array.isArray(page.anuncios) || page.anuncios.length > MAX_ITEMS
        || !Array.isArray(page.categoryItems) || page.categoryItems.length > MAX_ITEMS) return null;
    const nodes = ancestryNodes(ancestry);
    if (!nodes) return null;
    const transportNode = nodes.find(node => node.key === options.documentCategoryId);
    const scope = {
        version: 1,
        categoryKey: options.categoryKey,
        transportCategoryId: options.documentCategoryId,
        transportBaseUrl: observedBase(transportNode && transportNode.url),
        categoryBaseUrl: observedBase(options.categoryBaseUrl)
    };
    if (!validateCategoryItemScope(scope, nodes) || !scopedPageUrl(page.url, scope.transportBaseUrl)
        || page.nextUrl && !scopedPageUrl(page.nextUrl, scope.transportBaseUrl)) return null;
    if (page.categoryContext !== undefined && page.categoryContext !== null) {
        const context = page.categoryContext;
        if (!object(context) || context.key !== scope.transportCategoryId || observedBase(context.baseUrl) !== scope.transportBaseUrl) return null;
    }
    const firstPage = options.firstPage === undefined ? true : options.firstPage;
    if (typeof firstPage !== 'boolean' || firstPage && options.previousScope != null
        || !firstPage && (!validateCategoryItemScope(options.previousScope, nodes)
            || SCOPE_FIELDS.some(key => options.previousScope[key] !== scope[key]))) return null;
    if (page.categoryItemScope !== undefined && (!validateCategoryItemScope(page.categoryItemScope, nodes)
        || SCOPE_FIELDS.some(key => page.categoryItemScope[key] !== scope[key]))) return null;

    const categories = new Map();
    for (const pair of page.categoryItems) {
        if (!object(pair) || typeof pair.id !== 'string' || !ID.test(pair.id)) continue;
        const category = typeof pair.categoryId === 'string' && ID.test(pair.categoryId) ? pair.categoryId : null;
        if (!categories.has(pair.id)) categories.set(pair.id, category);
        else if (categories.get(pair.id) !== category) categories.set(pair.id, null);
    }
    const candidates = new Map(), invalid = new Set();
    for (const ad of page.anuncios) {
        if (!object(ad) || typeof ad.id !== 'string' || !ID.test(ad.id) || categories.get(ad.id) !== scope.categoryKey) continue;
        if (!itemUrlMatches(ad.url, ad.id) || ad.categoryId !== undefined && ad.categoryId !== scope.categoryKey
            || ad.category_id !== undefined && ad.category_id !== scope.categoryKey) { invalid.add(ad.id); continue; }
        if (!candidates.has(ad.id)) candidates.set(ad.id, ad);
    }
    const anuncios = [...candidates].filter(([id]) => !invalid.has(id)).map(([, ad]) => ad);
    if (firstPage && !anuncios.length) return null;
    const kept = new Set(anuncios.map(ad => ad.id));
    return { ...page, anuncios, categoryItems: [...categories].filter(([id, categoryId]) => kept.has(id) && categoryId === scope.categoryKey)
        .map(([id, categoryId]) => ({ id, categoryId })), categoryItemScope: scope, empty: false };
}

module.exports = { scopeCategoryItems, validateCategoryItemScope, categoryItemScopeUrlAllowed };
