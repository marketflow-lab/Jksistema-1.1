'use strict';
const assert = require('assert/strict');
const { normalizeCategory, PesquisaMercadoRunner } = require('../electron_app/main/modules/pesquisa-mercado-runner');
const { OfficialCategoryNavigation, officialCategoryMoreUrl, categoryBasePath } = require('../electron_app/main/modules/pesquisa-mercado-category-navigation');
const node = (key, nome, url = `https://lista.mercadolivre.com.br/_CatId_${key}`) => ({ key, nome, url });
const navigation = options => new OfficialCategoryNavigation({ ancestry: [], children: {}, normalize: normalizeCategory,
    read: async () => { throw new Error('Unexpected navigation'); }, ...options });
const cycling = 'https://lista.mercadolivre.com.br/esportes-fitness/ciclismo/';
const parent = cycling + 'pecas/*_NoIndex_True';
const expected = node('MLB122156', 'Coroas');
const href = cycling + 'pecas/coroas/_NoIndex_True';
const nav = navigation();
const identified = node(expected.key, 'Coroas de transmissao', href);
assert.equal(nav.route(identified, expected, parent)?.url, href, 'a matching official category ID survives a renamed public label');
assert.equal(categoryBasePath(new URL(parent).pathname), '/esportes-fitness/ciclismo/pecas');
assert.equal(nav.route({ ...identified, key: 'MLB999' }, expected, parent), null);
assert.equal(nav.route({ ...identified, key: 'mlb999' }, expected, parent), null);
assert.equal(nav.route({ ...identified, key: expected.key.toLowerCase() }, expected, parent)?.key, expected.key);
assert.equal(nav.route({ ...identified, key: href }, expected, parent), null, 'an untyped renamed link cannot establish identity');
const named = { key: href, nome: expected.nome, url: href };
assert.equal(nav.route(named, expected, parent)?.url, href);
assert.equal(nav.choose([named, identified], expected, parent).key, expected.key, 'a typed ID is authoritative over a name-only candidate');
assert.throws(() => nav.choose([named, { ...named, key: href + '/', url: href + '/' }], expected, parent), error => error.code === 'category_link_ambiguous');
for (const suffix of ['_NoIndex_False', '_OrderId_PRICE', '_Desde_49', '_PriceRange_10-20', '_SHIPPING_FREE', '_BRAND_123', '_ITEM_CONDITION_NEW', '_Container_ALL']) {
    assert.equal(nav.route({ ...identified, url: cycling + 'pecas/coroas/' + suffix }, expected, parent), null, suffix);
}
for (const url of [cycling + 'pecas/coroas/?q=coroas', cycling + 'pecas/coroas/$query', cycling + 'pecas/coroas/%24query',
    cycling + 'outro-ramo/coroas/', cycling + 'pecas/*_NoIndex_True', cycling + 'pecas/_CATEGORY_MLB999']) {
    assert.equal(nav.route({ ...identified, url }, expected, parent), null, 'an ID cannot authorize another scope or an unresolved query template');
}

const more = parent + '_FiltersAvailableSidebar?filter=category';
assert.equal(officialCategoryMoreUrl(more, parent), more);
assert.equal(officialCategoryMoreUrl(more, cycling + 'pecas/'), '', 'expansion is tied to the actual wildcard document');
for (const value of [more + '&sort=price_asc', more + '&filter=category', more.replace('filter=category', 'filter=brand'),
    more.replace('lista.mercadolivre.com.br', 'example.invalid'), more.replace('/pecas/', '/outras/'),
    more.replace('https://', 'https://user:password@'), more + '#category', parent + '?filter=category']) {
    assert.equal(officialCategoryMoreUrl(value, parent), '');
}
assert.equal(nav.route({ key: more, nome: 'Mostrar mais', url: more }, { key: 'MLB999', nome: 'Mostrar mais' }, parent), null,
    'the category expansion control is never a child category');

async function expandedCategoryPages() {
    const root = node('MLB1276', 'Esportes e Fitness');
    const scope = node('MLB1292', 'Ciclismo');
    const parts = node('MLB1934', 'Peças');
    const crown = node('MLB122156', 'Coroas');
    const rootLink = node(root.key, 'Esportes e Fitness', 'https://www.mercadolivre.com.br/c/esportes-fitness');
    const cyclingDocument = cycling + '*_NoIndex_True';
    const cyclingMore = cyclingDocument + '_FiltersAvailableSidebar?filter=category';
    const partsUrl = cycling + 'pecas/_NoIndex_True';
    const partsDocument = cycling + 'pecas/*_NoIndex_True';
    const partsMore = partsDocument + '_FiltersAvailableSidebar?filter=category';
    const partsLink = node(parts.key, 'Peças para bicicleta', partsUrl);
    const crownLink = node(crown.key, 'Coroas para transmissao', href);
    const reads = [];
    const pages = {
        'https://www.mercadolivre.com.br/categorias': { categoryTree: [{ ...rootLink, children: [node(scope.key, scope.nome, cycling)] }] },
        [cycling]: { url: cyclingDocument, categories: [], categoryFilterMoreUrl: cyclingMore },
        [cyclingMore]: { url: cyclingMore, categories: [partsLink] },
        [partsUrl]: { url: partsDocument, categories: [], categoryFilterMoreUrl: partsMore },
        [partsMore]: { url: partsMore, categories: [crownLink] }
    };
    const resolver = navigation({ ancestry: [root, scope], children: { [scope.key]: [parts], [parts.key]: [crown], [crown.key]: [] },
        read: async (url, purpose, key, requested) => {
            reads.push({ url, purpose, key, requested: requested?.key || '' });
            assert(pages[url], 'every navigation must use an observed fixture href');
            return pages[url];
        } });
    assert.equal(await resolver.resolve(crown.key), href);
    assert.deepEqual(reads.map(item => item.purpose), ['index', 'navigation', 'category-options', 'navigation', 'category-options']);
    assert.deepEqual(reads.filter(item => item.purpose === 'category-options').map(item => item.key), [scope.key, parts.key]);
    assert.equal(await resolver.resolve(crown.key), href);
    assert.equal(reads.length, 5, 'resolved routes are reused without reopening the category expansion');
    assert(reads.every(item => !new URL(item.url).search || new URL(item.url).search === '?filter=category'));

    let expanded = false;
    const unsafe = navigation({ ancestry: [root, scope], children: { [scope.key]: [parts], [parts.key]: [] }, read: async (url, purpose) => {
        if (purpose === 'index') return pages['https://www.mercadolivre.com.br/categorias'];
        if (purpose === 'category-options') { expanded = true; throw new Error('Unsafe expansion'); }
        return { url: cyclingDocument, categories: [], categoryFilterMoreUrl: cyclingMore + '&q=pecas' };
    } });
    await assert.rejects(unsafe.resolve(parts.key), error => error.code === 'category_link_missing');
    assert.equal(expanded, false);
}
function runnerFixture(pages, selected) {
    let current = '', polls = 0;
    const visited = [];
    const runner = new PesquisaMercadoRunner({ api: async () => { throw new Error('Navigation fixtures cannot use a backend'); },
        collector: {}, wait: async () => {}, pageScripts: { discoveryScript: () => 'discovery' },
        browser: { async navigate(url) { current = url; visited.push(url); }, getURL: () => current,
            getNavigationStatus: () => ({ url: current, statusCode: 200 }),
            async executeJavaScript() {
                polls += 1;
                const fixture = pages[current];
                assert(fixture, 'runner navigation must use an observed fixture route');
                return { url: current, readyState: 'complete', ...(typeof fixture === 'function' ? fixture(polls) : fixture) };
            } } });
    runner.controller = new AbortController();
    runner.checkpoint = { scope: { category: selected }, currentCategory: selected, officialTaxonomy: { children: { [selected.key]: [] } } };
    return { runner, visited, polls: () => polls };
}

async function runnerTrendsTransition() {
    const source = cycling + 'pecas/coroas/';
    const browse = source + '*_NoIndex_True';
    const selected = node('MLB122156', 'Coroas', source);
    const recommendations = Array.from({ length: 6 }, (_, index) => ({ id: 'recommendation-' + index }));
    const listings = Array.from({ length: 48 }, (_, index) => ({ id: 'scoped-result-' + index }));
    const trends = { anuncios: recommendations, categoryTrends: true, categoryDocumentIdentity: false, categoryIdentityConflict: false,
        categoryBrowse: { key: selected.key, nome: selected.nome, url: browse } };
    const h = runnerFixture({ [source]: trends, [browse]: { anuncios: listings, effectiveCategoryId: selected.key,
        categoryDocumentIdentity: true, categoryIdentityConflict: false } }, selected);
    const result = await h.runner.read(source);
    assert.equal(result.anuncios.length, 48, 'six Trends recommendations never become the selected category listing');
    assert.equal(result.categoryDocumentIdentity, true);
    assert.equal(result.effectiveCategoryId, selected.key);
    assert.deepEqual(h.visited, [source, browse], 'one observed category template transition reaches a proven document');

    for (const proof of [
        { effectiveCategoryId: selected.key, categoryDocumentIdentity: false },
        { effectiveCategoryId: 'MLB999', categoryDocumentIdentity: true },
        { effectiveCategoryId: selected.key, categoryDocumentIdentity: true, categoryIdentityConflict: true },
        { effectiveCategoryId: selected.key, categoryDocumentIdentity: true,
            categoryBrowse: { key: selected.key, nome: selected.nome, url: browse + '/another-transition' } }
    ]) {
        const blocked = runnerFixture({ [source]: trends, [browse]: { anuncios: listings, ...proof } }, selected);
        await assert.rejects(blocked.runner.read(source), error => ['category_load_timeout', 'category_scope_mismatch'].includes(error.code));
        assert(blocked.visited.every(url => url === source || url === browse));
        assert.equal(blocked.visited.filter(url => url === source).length, 1, 'destination retries cannot repeat the Trends transition');
        assert(blocked.visited.filter(url => url === browse).length <= 3, 'the destination retains the production retry limit');
    }
    for (const invalidBrowse of [
        { key: 'MLB999', nome: selected.nome, url: browse },
        { key: selected.key, nome: selected.nome, url: browse + '?q=coroas' }
    ]) {
        const blocked = runnerFixture({ [source]: { ...trends, categoryBrowse: invalidBrowse } }, selected);
        await assert.rejects(blocked.runner.read(source), error => ['category_load_timeout', 'category_scope_mismatch'].includes(error.code),
            'an invalid Trends transition cannot make its recommendation cards eligible as category results');
        assert(blocked.visited.every(url => url === source));
    }
    const noBrowse = runnerFixture({ [source]: { ...trends, categoryBrowse: null } }, selected);
    await assert.rejects(noBrowse.runner.read(source), error => error.code === 'category_load_timeout',
        'Trends without a confirmed transition still cannot publish its six recommendation cards');
}

async function runnerExpandedOptionsWaitForDestination() {
    const selected = node('MLB1934', 'Peças', cycling + 'pecas/_NoIndex_True');
    const modal = cycling + 'pecas/*_NoIndex_True_FiltersAvailableSidebar?filter=category';
    const stale = node('MLB999', 'Outra categoria', cycling + 'outra/');
    const wanted = node('MLB122156', 'Coroas', href);
    const h = runnerFixture({ [modal]: poll => poll === 1
        ? { url: cycling, categories: [stale], readyState: 'complete' }
        : poll === 2 ? { categories: [stale], readyState: 'loading' }
        : { categories: [wanted], readyState: 'complete' } }, selected);
    const result = await h.runner.read(modal, 'category-options', selected.key, wanted);
    assert.deepEqual(result.categories, [wanted], 'an old document or a loading modal cannot supply the expanded category list');
    assert(h.polls() >= 3);
    assert.deepEqual(h.visited, [modal]);
    const conflict = runnerFixture({ [modal]: { categories: [wanted], categoryDocumentIdentity: true,
        effectiveCategoryId: 'MLB999' } }, selected);
    await assert.rejects(conflict.runner.read(modal, 'category-options', selected.key, wanted),
        error => error.code === 'category_scope_mismatch', 'an explicitly different parent cannot supply the expanded list');
}

(async () => {
    await expandedCategoryPages();
    await runnerTrendsTransition();
    await runnerExpandedOptionsWaitForDestination();
    console.log('Pesquisa de Mercado navigation: observed NoIndex routes, category IDs, safe expanded filters and proven Trends transitions OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
