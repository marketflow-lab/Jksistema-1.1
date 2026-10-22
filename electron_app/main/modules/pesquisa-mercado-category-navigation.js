'use strict';

const INDEX_URL = 'https://www.mercadolivre.com.br/categorias';
const ID = /^MLB\d+$/;
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

    route(raw, expected, parentUrl = '') {
        if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
        const candidate = this.normalize({ key: raw.key, nome: raw.nome, url: raw.url });
        if (!candidate || label(candidate.nome) !== label(expected.nome)
            || ID.test(candidate.key) && candidate.key !== expected.key
            || unsupportedCategoryAlias(candidate.url)) return null;
        const url = new URL(candidate.url);
        if (url.search || /_(?:Desde|NoIndex|OrderId|PriceRange|SHIPPING|BRAND|ITEM_CONDITION|Container)_/i.test(url.pathname)) return null;
        if (parentUrl) {
            const parent = new URL(parentUrl);
            if (parent.hostname === 'lista.mercadolivre.com.br') {
                const prefix = parent.pathname.replace(/\/+$/, '') + '/';
                if (url.hostname !== parent.hostname || !url.pathname.startsWith(prefix)) return null;
            }
        }
        return candidate;
    }

    choose(raw, expected, parentUrl = '') {
        const matches = new Map();
        for (const item of raw || []) {
            const candidate = this.route(item, expected, parentUrl);
            if (candidate) matches.set(candidate.url, candidate);
        }
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
            const group = groups.find(item => item.url === selected.url && label(item.nome) === label(expected.nome));
            if (group && Array.isArray(group.children) && group.children.length) this.links.set(key, group.children);
        } else {
            const parentUrl = await this.resolve(parentKey);
            let links = this.links.get(parentKey);
            // A previously read filter can be incomplete. Refresh it once for
            // this requested child; never retain a negative cache entry.
            try { if (links) selected = this.choose(links, expected, parentUrl); }
            catch (_error) { this.links.delete(parentKey); }
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
                    if (!error.navigationDependencyKey) error.navigationDependencyKey = parentKey;
                    if (['category_filters_unavailable', 'category_not_found'].includes(error.code) || error.categoryScopeMismatch) error.sharedCategoryFailure = true;
                    throw error;
                }
                links = page.categories || [];
                selected = this.choose(links, expected, parentUrl);
                this.links.set(parentKey, links);
            }
        }
        this.routes.set(key, selected.url);
        return selected.url;
    }
}

module.exports = { OfficialCategoryNavigation, unsupportedCategoryAlias, CategoryNavigationError, categoryLabel: label };
