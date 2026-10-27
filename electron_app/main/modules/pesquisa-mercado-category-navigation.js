'use strict';

const INDEX_URL = 'https://www.mercadolivre.com.br/categorias';
const ID = /^MLB\d+$/;
// Public links observed in the site's item breadcrumbs/category filters. These
// categories are omitted from their parent's expanded filter. They remain
// route candidates: the destination must prove its category or exact items.
const OBSERVED_ROUTES = Object.freeze({
    MLB122194: 'https://lista.mercadolivre.com.br/esportes-fitness/ciclismo/pecas/elastomeros/*_NoIndex_True',
    MLB459353: 'https://lista.mercadolivre.com.br/esportes-fitness/ciclismo/pecas/freios/kits-cabos-cambios/*_NoIndex_True'
});
const label = value => String(value || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').replace(/\s+/g, ' ').trim().toLowerCase();

class CategoryNavigationError extends Error {
    constructor(code, dependencyKey = '') {
        super('Nao foi possivel confirmar a navegacao oficial da categoria.');
        this.name = 'CategoryNavigationError';
        this.code = code;
        this.categoryUnavailable = true;
        this.navigationDependencyKey = ID.test(dependencyKey) ? dependencyKey : '';
    }
}

function unsupportedCategoryAlias(value) {
    try {
        const url = new URL(value);
        return url.hostname === 'lista.mercadolivre.com.br'
            && /^\/_CatId_(?:MLB)?\d+(?=_|\/|$)/i.test(decodeURIComponent(url.pathname));
    } catch (_error) { return false; }
}

function categoryBasePath(pathname) {
    return pathname.replace(/(?:\/)?\*?_NoIndex_True\/?$/i, '').replace(/\/?\*\/?$/, '').replace(/\/+$/, '') || '/';
}

// The same category ID can also appear in a keyword subset. Only neutral
// listing/pagination suffixes may change when an official category is opened.
function neutralCategoryPage(expected, actual) {
    const base = value => {
        try {
            const url = new URL(value);
            if (url.protocol !== 'https:' || url.username || url.password || url.port && url.port !== '443'
                || !/(^|\.)mercadolivre\.com\.br$/i.test(url.hostname) || url.hash) return null;
            const query = [...url.searchParams];
            if (query.length && !(query.length === 1 && query[0][0] === 'original_category_landing' && query[0][1] === 'true')) return null;
            let path = url.pathname.replace(/\/+$/, '');
            const suffix = path.match(/\/?\*?(?:_Desde_[1-9]\d{0,8}|_NoIndex_True){1,2}$/);
            if (suffix) {
                const tokens = suffix[0].match(/_Desde_[1-9]\d{0,8}|_NoIndex_True/g);
                if (new Set(tokens.map(token => token.startsWith('_Desde_') ? 'page' : 'noindex')).size !== tokens.length) return null;
                path = path.slice(0, suffix.index);
            } else if (path.endsWith('/*')) path = path.slice(0, -2);
            return url.origin + path.replace(/\/+$/, '');
        } catch (_error) { return null; }
    };
    const source = base(expected), destination = base(actual);
    return !!source && source === destination;
}

function officialCategoryMoreUrl(value, currentParentUrl) {
    try {
        const parent = new URL(currentParentUrl), more = new URL(value);
        if (parent.protocol !== 'https:' || more.protocol !== 'https:' || more.origin !== parent.origin
            || parent.username || parent.password || more.username || more.password || more.hash
            || parent.port && parent.port !== '443' || !/(^|\.)mercadolivre\.com\.br$/i.test(more.hostname)) return '';
        const query = [...more.searchParams.entries()];
        if (query.length !== 1 || query[0][0] !== 'filter' || query[0][1] !== 'category') return '';
        if (!/_FiltersAvailableSidebar\/?$/.test(more.pathname)) return '';
        // Expansion belongs to this exact observed document, including its
        // wildcard/NoIndex route. It must not become a guessed category URL.
        const source = more.pathname.replace(/_FiltersAvailableSidebar\/?$/, '').replace(/\/+$/, '');
        if (source !== parent.pathname.replace(/\/+$/, '')) return '';
        return more.href;
    } catch (_error) { return ''; }
}

// Routes come from the Category directory/filter links, never from a guessed
// slug or a free-text search. Each coordinator owns its navigation cache.
class OfficialCategoryNavigation {
    constructor({ ancestry, children, normalize, read }) {
        this.normalize = normalize;
        this.read = read;
        this.ancestry = ancestry;
        this.records = new Map(ancestry.map(node => [node.key, node]));
        this.parents = new Map();
        for (let index = 1; index < ancestry.length; index += 1) {
            this.parents.set(ancestry[index].key, ancestry[index - 1].key);
        }
        for (const [key, descendants] of Object.entries(children)) {
            for (const child of descendants) {
                this.records.set(child.key, child);
                this.parents.set(child.key, key);
            }
        }
        this.routes = new Map();
        this.links = new Map();
    }

    route(raw, expected, parentUrl = '', allowObservedAlias = true) {
        if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
        let candidate = this.normalize({ key: raw.key, nome: raw.nome, url: raw.url });
        const candidateId = candidate && ID.test(candidate.key.toUpperCase()) ? candidate.key.toUpperCase() : '';
        if (!candidate || (candidateId ? candidateId !== expected.key : label(candidate.nome) !== label(expected.nome))
            || unsupportedCategoryAlias(candidate.url)) return null;
        if (candidateId) candidate = { ...candidate, key: candidateId };
        const url = new URL(candidate.url);
        const categoryPath = url.pathname.replace(/(?:\/)?\*?_NoIndex_True\/?$/i, '');
        if (url.search || /_(?:Desde|NoIndex|OrderId|PriceRange|SHIPPING|BRAND|ITEM_CONDITION|Container)_|_FiltersAvailableSidebar/i.test(categoryPath)
            || /\$query/i.test(decodeURIComponent(url.pathname))) return null;
        const ids = [...decodeURIComponent(url.pathname).matchAll(/_(?:CatId|CATEGORY)_(MLB\d+)(?=_|\/|$)/gi)].map(match => match[1].toUpperCase());
        if (ids.some(id => id !== expected.key)) return null;
        if (parentUrl) {
            const parent = new URL(parentUrl);
            if (parent.hostname === 'lista.mercadolivre.com.br') {
                const base = categoryBasePath(parent.pathname), prefix = base + '/';
                if (url.hostname !== parent.hostname || categoryBasePath(url.pathname) === base) return null;
                // ML can retain a legacy public path for a child of a renamed
                // parent. An observed ID-coupled link is still only a route
                // candidate: the runner must prove the destination's own ID.
                if (!url.pathname.startsWith(prefix) && (!candidateId || !allowObservedAlias)) return null;
            }
        }
        return candidate;
    }

    choose(raw, expected, parentUrl = '') {
        let matches = new Map();
        const identified = new Map();
        for (const item of raw || []) {
            const candidate = this.route(item, expected, parentUrl);
            if (candidate) {
                matches.set(candidate.url, candidate);
                if (candidate.key === expected.key && ID.test(candidate.key)) identified.set(candidate.url, candidate);
            }
        }
        if (identified.size) matches = identified;
        if (matches.size !== 1) {
            throw new CategoryNavigationError(matches.size ? 'category_link_ambiguous' : 'category_link_missing', expected.key);
        }
        return [...matches.values()][0];
    }

    async resolve(key) {
        if (this.routes.has(key)) return this.routes.get(key);
        const expected = this.records.get(key);
        if (!expected) throw new Error('Categoria fora da arvore selecionada.');
        const parentKey = this.parents.get(key);
        let selected;
        if (!parentKey) {
            let page;
            try { page = await this.read(INDEX_URL, 'index'); }
            catch (error) { error.navigationDependencyKey = expected.key; error.sharedCategoryFailure = true; throw error; }
            const groups = Array.isArray(page.categoryTree) ? page.categoryTree : [];
            try { selected = this.choose(groups, expected); }
            catch (error) { error.sharedCategoryFailure = true; throw error; }
            const group = groups.find(item => item.url === selected.url && this.route(item, expected));
            if (group && Array.isArray(group.children) && group.children.length) this.links.set(key, group.children);
        } else {
            const parentUrl = await this.resolve(parentKey);
            let links = this.links.get(parentKey);
            // A previously read filter can be incomplete. Refresh it once for
            // this requested child; never retain a negative cache entry.
            try { if (links) selected = this.choose(links, expected, parentUrl); }
            catch (_error) { /* Keep known siblings until a validated refresh replaces the list. */ }
            if (!selected && OBSERVED_ROUTES[key]) {
                // Remembered fallbacks have no fresh parent-document evidence.
                selected = this.route({ key, nome: expected.nome, url: OBSERVED_ROUTES[key] }, expected, parentUrl, false);
            }
            if (!selected) {
                let page;
                try {
                    for (let attempt = 0; attempt < 2; attempt += 1) {
                        try { page = await this.read(parentUrl, 'navigation', parentKey, expected); break; }
                        catch (error) {
                            if (attempt || !['category_filters_unavailable', 'category_load_timeout'].includes(error.code)) throw error;
                        }
                    }
                }
                catch (error) {
                    const failed = error.categoryPage, seen = new Set();
                    let ancestor = this.parents.get(parentKey), ancestorMismatch = false;
                    while (ancestor && !seen.has(ancestor)) {
                        seen.add(ancestor);
                        if (failed?.effectiveCategoryId === ancestor) { ancestorMismatch = true; break; }
                        ancestor = this.parents.get(ancestor);
                    }
                    if ((error.categoryScopeMismatch || error.code === 'category_scope_mismatch') && !error.waitingLogin
                        && error.name !== 'AbortError' && ![401, 403, 429].includes(error.status)
                        && failed?.categoryDocumentIdentity && !failed.categoryIdentityConflict && !failed.authRequired && !failed.avantAuthRequired
                        && ancestorMismatch) {
                        const landing = new URL(parentUrl);
                        if (!landing.search && !landing.hash && /\/\*_NoIndex_True\/?$/.test(landing.pathname)) {
                            landing.pathname = landing.pathname.replace(/\/\*_NoIndex_True\/?$/, '/');
                            if (landing.pathname !== '/' && neutralCategoryPage(parentUrl, landing.href)) {
                                try {
                                    const landingPage = await this.read(landing.href, 'navigation', parentKey, { ...expected, preferTrendsChildren: true });
                                    this.choose(landingPage.categories, expected, landingPage.url || landing.href);
                                    page = landingPage;
                                }
                                catch (landingError) {
                                    if (landingError.name === 'AbortError' || landingError.waitingLogin || [401, 403, 429].includes(landingError.status)
                                        || !['category_scope_mismatch', 'category_load_timeout', 'category_not_found', 'category_link_missing',
                                            'category_link_ambiguous', 'category_filters_unavailable'].includes(landingError.code)) throw landingError;
                                    error.categoryLandingCode = landingError.code;
                                }
                            }
                        }
                    }
                    if (!page) {
                        if (!error.navigationDependencyKey) error.navigationDependencyKey = parentKey;
                        if (['category_filters_unavailable', 'category_not_found'].includes(error.code) || error.categoryScopeMismatch) error.sharedCategoryFailure = true;
                        throw error;
                    }
                }
                links = page.categories || [];
                try { selected = this.choose(links, expected, parentUrl); }
                catch (error) {
                    if (error.code !== 'category_link_missing') throw error;
                    const moreUrl = officialCategoryMoreUrl(page.categoryFilterMoreUrl, page.url || parentUrl);
                    if (!moreUrl) throw error;
                    let expanded;
                    try { expanded = await this.read(moreUrl, 'category-options', parentKey, expected); }
                    catch (expandedError) {
                        if (!expandedError.navigationDependencyKey) expandedError.navigationDependencyKey = parentKey;
                        if (['category_filters_unavailable', 'category_not_found'].includes(expandedError.code) || expandedError.categoryScopeMismatch) expandedError.sharedCategoryFailure = true;
                        throw expandedError;
                    }
                    links = expanded.categories || [];
                    selected = this.choose(links, expected, page.url || parentUrl);
                }
                this.links.set(parentKey, links);
            }
        }
        this.routes.set(key, selected.url);
        return selected.url;
    }
}

module.exports = { OfficialCategoryNavigation, unsupportedCategoryAlias, CategoryNavigationError, categoryLabel: label,
    categoryBasePath, officialCategoryMoreUrl, neutralCategoryPage };
