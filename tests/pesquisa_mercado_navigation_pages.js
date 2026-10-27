'use strict';
const assert = require('assert/strict');
const { normalizeCategory, PesquisaMercadoRunner } = require('../electron_app/main/modules/pesquisa-mercado-runner');
const { OfficialCategoryNavigation, CategoryNavigationError, officialCategoryMoreUrl, categoryBasePath, neutralCategoryPage } = require('../electron_app/main/modules/pesquisa-mercado-category-navigation');
const node = (key, nome, url = `https://lista.mercadolivre.com.br/_CatId_${key}`) => ({ key, nome, url });
const navigation = options => new OfficialCategoryNavigation({ ancestry: [], children: {}, normalize: normalizeCategory,
    read: async () => { throw new Error('Unexpected navigation'); }, ...options });
const cycling = 'https://lista.mercadolivre.com.br/esportes-fitness/ciclismo/';
const parent = cycling + 'pecas/*_NoIndex_True';
const expected = node('MLB122156', 'Coroas');
const href = cycling + 'pecas/coroas/_NoIndex_True';
for (const suffix of ['', '*', '*_NoIndex_True', '*_Desde_49_NoIndex_True', '_NoIndex_True_Desde_49']) {
    assert(neutralCategoryPage(cycling + 'pecas/coroas/', cycling + 'pecas/coroas/' + suffix));
}
for (const suffix of ['keyword_NoIndex_True', '*_Desde_49_NoIndex_True?q=coroa', '*_NoIndex_True_NoIndex_True', '../outra/', '*_OrderId_PRICE']) {
    assert.equal(neutralCategoryPage(cycling + 'pecas/coroas/', cycling + 'pecas/coroas/' + suffix), false, suffix);
}
assert(neutralCategoryPage(cycling + 'pecas/coroas/*_Desde_49_NoIndex_True', cycling + 'pecas/coroas/*_Desde_97_NoIndex_True'));
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
    cycling + 'pecas/*_NoIndex_True', cycling + 'pecas/_CATEGORY_MLB999']) {
    assert.equal(nav.route({ ...identified, url }, expected, parent), null, 'an ID cannot authorize another scope or an unresolved query template');
}

const machinery = node('MLB1514', 'Maquinaria Agrícola', 'https://lista.mercadolivre.com.br/agro/maquinaria-agricola/*_NoIndex_True');
const otherMachines = node('MLB271602', 'Outras Máquinas');
const legacyMachines = { ...otherMachines, url: 'https://lista.mercadolivre.com.br/agro/maquinas-ferramentas/outras/*_NoIndex_True' };
assert.equal(nav.route(legacyMachines, otherMachines, machinery.url)?.url, legacyMachines.url,
    'an exact observed category ID can retain its legacy path after the parent path changes');
assert.equal(nav.route({ ...legacyMachines, key: 'MLB260601' }, otherMachines, machinery.url), null,
    'a homonymous category in the vehicle tree cannot supply the agricultural category');
assert.equal(nav.route({ ...legacyMachines, key: legacyMachines.url }, otherMachines, machinery.url), null,
    'a name-only candidate outside the parent path cannot establish the legacy relationship');
assert.equal(nav.route({ ...legacyMachines, url: legacyMachines.url.replace('lista.', 'servicos.') }, otherMachines, machinery.url), null,
    'an exact ID cannot authorize a change of listing hostname');
assert.equal(nav.route({ ...legacyMachines, url: legacyMachines.url.replace('/outras/', '/outras/_CATEGORY_MLB260601/') }, otherMachines, machinery.url), null,
    'a conflicting category ID in the emitted path remains invalid');

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
        error => error.code === 'category_scope_mismatch' && error.categoryPage.effectiveCategoryId === 'MLB999',
        'an explicitly different parent cannot supply the expanded list and remains available as error evidence');
}

async function observedMissingRoutesStayCandidates() {
    const parts = node('MLB1934', 'Peças');
    const elastomer = node('MLB122194', 'Elastômeros');
    const parentUrl = cycling + 'pecas/';
    const candidateUrl = parentUrl + 'elastomeros/*_NoIndex_True';
    const resolver = navigation({ ancestry: [parts, elastomer], children: { [parts.key]: [elastomer], [elastomer.key]: [] } });
    resolver.routes.set(parts.key, parentUrl);
    assert.equal(await resolver.resolve(elastomer.key), candidateUrl, 'a verified public permalink can recover an omitted child after its parent is resolved');
    const currentLink = node(elastomer.key, elastomer.nome, parentUrl + 'current-elastomeros/');
    const fresh = navigation({ ancestry: [parts, elastomer], children: { [parts.key]: [elastomer], [elastomer.key]: [] } });
    fresh.routes.set(parts.key, parentUrl); fresh.links.set(parts.key, [currentLink]);
    assert.equal(await fresh.resolve(elastomer.key), currentLink.url, 'current observed parent links take priority over the fallback candidate');
    const wrong = navigation({ ancestry: [parts, elastomer], children: { [parts.key]: [elastomer], [elastomer.key]: [] },
        read: async url => ({ url, categories: [] }) });
    wrong.routes.set(parts.key, cycling + 'another-branch/');
    await assert.rejects(wrong.resolve(elastomer.key), error => error.code === 'category_link_missing', 'a remembered candidate cannot cross the resolved parent');
    const h = runnerFixture({ [candidateUrl]: { anuncios: [{ id: 'unproven' }], categoryDocumentIdentity: false, effectiveCategoryId: '' } }, elastomer);
    await assert.rejects(h.runner.read(candidateUrl), error => error.code === 'category_load_timeout', 'a remembered URL alone never proves a category');
    const bareUrl = parentUrl + 'elastomeros/';
    const discovered = runnerFixture({ [bareUrl]: { anuncios: [{ id: 'unproven' }], categoryDocumentIdentity: false, effectiveCategoryId: '' } }, elastomer);
    discovered.runner.checkpoint.officialTaxonomy.ancestry = [parts, elastomer];
    discovered.runner.officialCategoryUrl = async () => { throw Object.assign(new Error('Missing link'), { code: 'category_link_missing' }); };
    discovered.runner.discoverOfficialCategoryRoute = async () => ({ ...elastomer, url: bareUrl });
    await assert.rejects(discovered.runner.readCategory(elastomer), error => error.code === 'category_load_timeout',
        'a newly discovered bare route must also prove its document category before returning seeds');

    const keywordUrl = bareUrl + 'only-a-keyword_NoIndex_True';
    for (const discoveredRoute of [false, true]) {
        const redirected = runnerFixture({ [bareUrl]: { url: keywordUrl, anuncios: [{ id: 'subset' }],
            categoryDocumentIdentity: true, effectiveCategoryId: elastomer.key,
            categoryContext: { key: elastomer.key, baseUrl: bareUrl } } }, elastomer);
        redirected.runner.checkpoint.officialTaxonomy.ancestry = [parts, elastomer];
        redirected.runner.officialCategoryUrl = async () => {
            if (discoveredRoute) throw Object.assign(new Error('Missing link'), { code: 'category_link_missing' });
            return bareUrl;
        };
        redirected.runner.discoverOfficialCategoryRoute = async () => ({ ...elastomer, url: bareUrl });
        await assert.rejects(redirected.runner.readCategory(elastomer), error => error.code === 'category_scope_mismatch',
            'a matching category ID cannot turn a keyword redirect into a complete official category');
    }
}

async function observedLegacyPathRequiresDestinationIdentity() {
    const children = { [machinery.key]: [otherMachines], [otherMachines.key]: [] };
    for (const identity of [otherMachines.key, 'MLB260601']) {
        const h = runnerFixture({
            [machinery.url]: { categoryDocumentIdentity: true, effectiveCategoryId: machinery.key,
                categories: [legacyMachines], anuncios: [{ id: 'parent-item' }] },
            [legacyMachines.url]: { categoryDocumentIdentity: true, effectiveCategoryId: identity,
                categoryContext: { key: identity, nome: otherMachines.nome, baseUrl: legacyMachines.url.replace('*_NoIndex_True', '') },
                anuncios: [{ id: 'leaf-item' }] }
        }, otherMachines);
        h.runner.checkpoint.officialTaxonomy = { ancestry: [machinery, otherMachines], children };
        h.runner.officialNavigation = navigation({ ancestry: [machinery, otherMachines], children,
            read: (...args) => h.runner.read(...args) });
        h.runner.officialNavigation.routes.set(machinery.key, machinery.url);
        h.runner.discoverOfficialCategoryRoute = async () => null;
        if (identity === otherMachines.key) {
            const page = await h.runner.readCategory(otherMachines);
            assert.equal(page.effectiveCategoryId, otherMachines.key);
            assert.deepEqual(h.visited, [machinery.url, legacyMachines.url],
                'the real emitted link reaches the destination without guessing a slug or searching by title');
        } else {
            h.runner.tryCategoryItemScope = async () => null;
            await assert.rejects(h.runner.readCategory(otherMachines), error => error.code === 'category_scope_mismatch',
                'an observed ID-coupled legacy link cannot publish a different destination category');
        }
    }
}

async function staticHubOnlySuppliesObservedChildLinks() {
    const hub = node('MLB1292', 'Ciclismo', cycling), child = node('MLB1934', 'Peças');
    const emitted = { key: cycling + 'pecas/', nome: child.nome, url: cycling + 'pecas/' };
    const descriptor = { version: 1, url: cycling, canonical: cycling, heading: hub.nome };
    const fixture = patch => {
        const value = { categoryTrends: true, categoryDocumentIdentity: false, effectiveCategoryId: '',
            categoryNavigationHub: descriptor, categories: [emitted], anuncios: [{ id: 'recommendation-only' }] };
        const h = runnerFixture({ [cycling]: poll => typeof patch === 'function' ? patch(value, poll) : { ...value, ...patch } }, child);
        const children = { [hub.key]: [child], [child.key]: [] };
        h.runner.checkpoint.officialTaxonomy = { ancestry: [hub, child], children };
        h.runner.officialNavigation = navigation({ ancestry: [hub, child], children });
        return h;
    };
    const valid = fixture();
    const result = await valid.runner.read(cycling, 'navigation', hub.key, child);
    assert.equal(valid.polls(), 2, 'static hub links require two stable reads');
    assert.equal(result.categoryDocumentIdentity, false, 'following a child href never promotes the hub into a category document');
    assert.equal(result.effectiveCategoryId, '', 'the frozen parent ID is never assigned to metadata-free HTML');
    assert.deepEqual(result.categories, [emitted]);
    const changing = fixture((value, poll) => ({ ...value, categoryNavigationHub: poll === 2 ? { ...descriptor, heading: 'Outro hub' } : descriptor }));
    await changing.runner.read(cycling, 'navigation', hub.key, child);
    assert.equal(changing.polls(), 4, 'an inconsistent read resets stability');
    for (const [description, patch] of [
        ['different heading', { categoryNavigationHub: { ...descriptor, heading: child.nome } }],
        ['different canonical', { categoryNavigationHub: { ...descriptor, canonical: cycling + 'outros/' } }],
        ['unknown descriptor field', { categoryNavigationHub: { ...descriptor, categoryId: hub.key } }],
        ['keyword redirect', { url: cycling + 'apenas-uma-busca', categoryNavigationHub: { ...descriptor, url: cycling + 'apenas-uma-busca', canonical: cycling + 'apenas-uma-busca' } }],
        ['conflicting document ID', { categoryDocumentIdentity: true, effectiveCategoryId: 'MLB999' }],
        ['conflicting advisory ID', { categoryContext: { key: 'MLB999', baseUrl: cycling } }],
        ['conflicting advisory route', { categoryContext: { key: hub.key, baseUrl: cycling + 'outros/' } }],
        ['identity conflict', { categoryIdentityConflict: true }],
        ['Mercado Livre login', { authRequired: true }],
        ['Avant login', { avantAuthRequired: true }],
        ['loading document', { readyState: 'loading' }],
        ['wrong child ID', { categories: [{ ...emitted, key: 'MLB999' }] }],
        ['untyped external branch', { categories: [{ ...emitted, key: cycling + 'fora/pecas/', url: 'https://lista.mercadolivre.com.br/fora/pecas/' }] }],
        ['ambiguous child', { categories: [emitted, { ...emitted, key: cycling + 'pecas-2/', url: cycling + 'pecas-2/' }] }]
    ]) {
        const blocked = fixture(patch);
        await assert.rejects(blocked.runner.read(cycling, 'navigation', hub.key, child), undefined, description);
    }
    const unrelated = fixture();
    unrelated.runner.officialNavigation.parents.set(child.key, 'MLB999');
    await assert.rejects(unrelated.runner.read(cycling, 'navigation', hub.key, child),
        'a matching label cannot establish a relationship outside the frozen taxonomy');
    for (const purpose of ['category', 'category-transport']) {
        const blocked = fixture();
        await assert.rejects(blocked.runner.read(cycling, purpose, hub.key, child),
            'a navigation hub cannot make recommendation cards into a category listing');
    }
    const verificationUrl = 'https://www.mercadolivre.com.br/captcha/wall/logged?go_url=https%3A%2F%2Flista.mercadolivre.com.br%2Fcategoria';
    const verification = fixture({ url: verificationUrl, authRequired: true, categoryNavigationHub: null });
    verification.runner.browser.getURL = () => verificationUrl;
    await assert.rejects(verification.runner.read(cycling, 'navigation', hub.key, child),
        error => error.waitingLogin === true && error.message === 'ml_login_required',
        'a CAPTCHA redirect preserves the manual waiting_login flow instead of becoming an incomplete category');
    assert.equal(verification.polls(), 1, 'the verification wall stops category retries immediately');
}

async function navigationChildLinksDoNotReplaceDocumentIdentity() {
    const hub = node('MLB1292', 'Ciclismo', cycling + '*_NoIndex_True');
    const child = node('MLB1934', 'Peças', cycling + 'pecas/*_NoIndex_True');
    // Public Ciclismo failure: five valid filters were already visible while
    // its own document ID and listing had not finished arriving.
    const filters = [node('MLB6790', 'Acessórios', cycling + 'acessorios/*_NoIndex_True'),
        node('MLB459679', 'Bicicletas', cycling + 'bicicletas/*_NoIndex_True'),
        node('MLB439159', 'Bicicletas Ergométricas', cycling + 'bicicletas-ergometricas/*_NoIndex_True'),
        child, node('MLB1935', 'Roupas', cycling + 'roupas/*_NoIndex_True')];
    const partial = { categoryDocumentIdentity: false, effectiveCategoryId: '', categoryIdentityConflict: false,
        categoryContext: { key: hub.key, nome: hub.nome, baseUrl: cycling }, categories: filters, anuncios: [] };
    const children = { [hub.key]: [child], [child.key]: [] };
    const fixture = (patch = {}, source = hub.url) => {
        const h = runnerFixture({ [source]: poll => typeof patch === 'function' ? patch(partial, poll) : { ...partial, ...patch } }, child);
        h.runner.checkpoint.officialTaxonomy = { ancestry: [hub, child], children };
        h.runner.officialNavigation = navigation({ ancestry: [hub, child], children, read: (...args) => h.runner.read(...args) });
        h.runner.officialNavigation.routes.set(hub.key, source);
        return h;
    };
    for (const [source, transition] of [[hub.url, false], [cycling, true]]) {
        const h = fixture({}, source);
        assert.equal(h.runner.officialNavigation.choose(filters, child, source).url, child.url);
        await assert.rejects(h.runner.read(source, 'navigation', hub.key, child, transition), error =>
            error.code === 'category_load_timeout' && error.name === 'CategoryNavigationError' && error.categoryUnavailable
            && error.navigationDependencyKey === hub.key && error.categoryPage.effectiveCategoryId === ''
            && error.categoryPage.categoryDocumentIdentity === false && error.categoryPage.categories.length === 5,
        'an observed child cannot supply the still-missing parent document identity');
        assert.equal(h.polls(), 34);
        assert.deepEqual(h.visited, [source], 'the typed timeout does not introduce three internal retries');
    }
    const eventual = fixture((page, poll) => poll <= 34 ? page : { ...page, categoryDocumentIdentity: true,
        effectiveCategoryId: hub.key, anuncios: [{ id: 'confirmed-parent-item' }] });
    assert.equal(await eventual.runner.officialNavigation.resolve(child.key), child.url);
    assert.equal(eventual.polls(), 36, 'the second parent read must independently settle its own ID');
    assert.deepEqual(eventual.visited, [hub.url, hub.url], 'the existing resolver gets one additional attempt');
    const stalled = fixture();
    await assert.rejects(stalled.runner.officialNavigation.resolve(child.key), error => error.code === 'category_load_timeout');
    assert.equal(stalled.polls(), 68);
    assert.deepEqual(stalled.visited, [hub.url, hub.url], 'persistent missing identity remains bounded to two resolver attempts');

    for (const [description, categories] of [
        ['missing child', filters.filter(item => item.key !== child.key)],
        ['ambiguous child', [child, { ...child, url: cycling + 'pecas-alternativas/*_NoIndex_True' }]],
        ['conflicting child ID', [{ ...child, key: 'MLB999' }]],
        ['conflicting child path ID', [{ ...child, url: cycling + 'pecas/_CATEGORY_MLB999/*_NoIndex_True' }]]
    ]) {
        const h = fixture({ categories });
        await assert.rejects(h.runner.read(hub.url, 'navigation', hub.key, child), error =>
            error.code === 'category_link_missing' && error.categoryPage.categories.length === categories.length, description);
        assert.deepEqual(h.visited, [hub.url], description + ' cannot acquire a new retry');
    }
    const unscoped = fixture({}, cycling);
    const bareNavigation = await unscoped.runner.read(cycling, 'navigation', hub.key, child);
    assert.equal(bareNavigation.categoryDocumentIdentity, false,
        'ordinary bare-route child navigation retains its existing behavior without inventing an own ID');
    assert.deepEqual(unscoped.visited, [cycling]);
    const wrongDocument = fixture({ categoryDocumentIdentity: true, effectiveCategoryId: 'MLB999' });
    await assert.rejects(wrongDocument.runner.read(hub.url, 'navigation', hub.key, child), error => error.code === 'category_scope_mismatch');
    assert.deepEqual(wrongDocument.visited, [hub.url], 'a different own ID must not become a timeout retry');
    const conflict = fixture({ categoryIdentityConflict: true });
    await assert.rejects(conflict.runner.read(hub.url, 'navigation', hub.key, child), error => error.code !== 'category_load_timeout');
    const auth = fixture({ authRequired: true });
    await assert.rejects(auth.runner.read(hub.url, 'navigation', hub.key, child), error => error.waitingLogin === true);
    assert.equal(auth.polls(), 1);
    const blocked = fixture();
    blocked.runner.browser.getNavigationStatus = () => ({ url: hub.url, statusCode: 403 });
    await assert.rejects(blocked.runner.read(hub.url, 'navigation', hub.key, child), error => error.code === 'category_access_denied');
    assert.equal(blocked.polls(), 1);
}

async function cachedSiblingLinksSurviveFailedRefresh() {
    const hub = node('MLB1292', 'Ciclismo', cycling);
    const known = node('MLB1934', 'Peças', cycling + 'pecas/*_NoIndex_True');
    const wanted = node('MLB1935', 'Roupas', cycling + 'roupas/*_NoIndex_True');
    const children = { [hub.key]: [known, wanted], [known.key]: [], [wanted.key]: [] };
    for (const ambiguous of [false, true]) for (const failure of ['filters', 'auth', 'access']) {
        const previous = [known, ...(ambiguous ? [wanted, { ...wanted, url: cycling + 'roupas-alternativas/*_NoIndex_True' }] : [])];
        const refreshed = [wanted];
        let reads = 0, failRefresh = true;
        const error = failure === 'auth' ? Object.assign(new Error('Manual login required'), { waitingLogin: true })
            : new CategoryNavigationError(failure === 'access' ? 'category_access_denied' : 'category_filters_unavailable', hub.key);
        if (failure === 'access') error.status = 403;
        const resolver = navigation({ ancestry: [hub], children, read: async (url, purpose, key, expected) => {
            reads += 1;
            assert.equal(url, hub.url); assert.equal(purpose, 'navigation'); assert.equal(key, hub.key); assert.equal(expected.key, wanted.key);
            if (failRefresh) throw error;
            return { url, categories: refreshed, categoryDocumentIdentity: false, effectiveCategoryId: '' };
        } });
        resolver.routes.set(hub.key, hub.url); resolver.links.set(hub.key, previous);
        assert.equal(resolver.choose(previous, known, hub.url).url, known.url);
        await assert.rejects(resolver.resolve(wanted.key), actual => actual === error,
            'missing/ambiguous siblings must retain the refresh failure, including login and HTTP blocks');
        const failedReads = failure === 'filters' ? 2 : 1;
        assert.equal(reads, failedReads, 'auth/access errors interrupt immediately; loading retains the existing retry bound');
        assert.equal(resolver.links.get(hub.key), previous, 'failed refresh must preserve previously observed sibling links');
        assert.equal(resolver.routes.has(wanted.key), false, 'a failed refresh cannot resolve the missing sibling');
        assert.equal(await resolver.resolve(known.key), known.url);
        assert.equal(reads, failedReads, 'an unresolved sibling with a known href needs no repeated parent request');
        failRefresh = false;
        assert.equal(await resolver.resolve(wanted.key), wanted.url);
        assert.equal(reads, failedReads + 1, 'a later explicit retry still refreshes the missing or ambiguous child');
        assert.equal(resolver.links.get(hub.key), refreshed, 'a validated refresh replaces the old list without merging aliases');
    }
}

async function neutralLandingMustProveTheLeaf() {
    const brakeUrl = cycling + 'pecas/freios/';
    const brakes = node('MLB9888', 'Freios', brakeUrl);
    const landing = brakeUrl + 'kits-cabos-cambios/';
    const wildcard = landing + '*_NoIndex_True';
    const kits = node('MLB459353', 'Kits de cabos e câmbios', wildcard);
    const parentPage = { categoryDocumentIdentity: true, effectiveCategoryId: brakes.key,
        categoryContext: { key: brakes.key, baseUrl: brakeUrl }, anuncios: [{ id: 'mixed-parent-item' }], categoryItems: [] };
    const leafPage = { categoryDocumentIdentity: true, effectiveCategoryId: kits.key,
        categoryContext: { key: kits.key, baseUrl: landing }, anuncios: [{ id: 'confirmed-leaf-item' }] };
    const fixture = pages => {
        const h = runnerFixture({ [wildcard]: parentPage, [brakeUrl]: parentPage, ...pages }, kits);
        const children = { [brakes.key]: [kits], [kits.key]: [] };
        h.runner.checkpoint.officialTaxonomy = { ancestry: [brakes, kits], children };
        h.runner.officialNavigation = navigation({ ancestry: [brakes, kits], children });
        h.runner.officialNavigation.routes.set(brakes.key, brakeUrl); h.runner.officialNavigation.routes.set(kits.key, wildcard);
        h.runner.discoverOfficialCategoryRoute = async () => null;
        return h;
    };
    const recovered = fixture({ [landing]: leafPage });
    recovered.runner.discoverOfficialCategoryRoute = async () => assert.fail('a confirmed neutral landing needs no keyword discovery');
    const page = await recovered.runner.readCategory(kits);
    assert.equal(page.effectiveCategoryId, kits.key);
    assert.deepEqual(recovered.visited, [wildcard, landing], 'only the exact observed neutral suffix is removed before extensive probes');

    let transitioning;
    transitioning = fixture({ [landing]: { categoryTrends: true, categoryBrowse: { key: kits.key, url: wildcard }, categoryDocumentIdentity: false, anuncios: [] },
        [wildcard]: () => transitioning.visited.filter(url => url === wildcard).length === 1 ? parentPage : leafPage });
    assert.equal((await transitioning.runner.readCategory(kits)).effectiveCategoryId, kits.key);
    assert.deepEqual(transitioning.visited, [wildcard, landing, wildcard], 'a landing may use only its emitted, ID-coupled Trends template');

    const wrong = fixture({ [landing]: parentPage });
    let primary;
    const read = wrong.runner.read.bind(wrong.runner);
    wrong.runner.read = async (...args) => { try { return await read(...args); } catch (error) { if (!primary) primary = error; throw error; } };
    wrong.runner.discoverOfficialCategoryRoute = async () => ({ ...kits, url: landing });
    await assert.rejects(wrong.runner.readCategory(kits), error => error === primary && error.categoryPage.effectiveCategoryId === brakes.key,
        'a neutral spelling cannot turn parent results into the requested leaf or discard the primary mismatch');
    assert.equal(wrong.visited.filter(url => url === landing).length, 1, 'discovery cannot repeat a normalized route already rejected');
    assert.deepEqual(wrong.visited, [wildcard, landing, brakeUrl]);

    for (const source of [landing, wildcard + '?q=kits', landing + 'keyword_NoIndex_True']) {
        const h = fixture({ [source]: parentPage });
        h.runner.officialCategoryUrl = async () => source;
        await assert.rejects(h.runner.readCategory(kits), error => error.code === 'category_scope_mismatch');
        assert.equal(h.visited.filter(url => url === source).length, 1, 'bare, query and keyword routes cannot produce another normalized guess');
        assert(h.visited.every(url => url === source || url === brakeUrl));
    }
    for (const kind of ['auth', 'access', 'abort']) {
        const h = fixture({ [landing]: { ...leafPage, ...(kind === 'auth' ? { authRequired: true } : {}) } });
        if (kind === 'access') h.runner.browser.getNavigationStatus = () => ({ url: h.runner.browser.getURL(), statusCode: h.runner.browser.getURL() === landing ? 403 : 200 });
        if (kind === 'abort') { const navigate = h.runner.browser.navigate; h.runner.browser.navigate = async url => { await navigate(url); if (url === landing) throw Object.assign(new Error('Cancelled'), { name: 'AbortError' }); }; }
        await assert.rejects(h.runner.readCategory(kits), error => kind === 'auth' ? error.waitingLogin === true
            : kind === 'access' ? error.code === 'category_access_denied' : error.name === 'AbortError');
        assert.deepEqual(h.visited, [wildcard, landing], 'a security or cancellation barrier stops before ancestor probes');
    }
}

async function failedWildcardCanUseStableTrendsChildLinks() {
    const parts = node('MLB1934', 'Peças', cycling + 'pecas/');
    const base = parts.url + 'kits-motor/', wildcard = base + '*_NoIndex_True';
    const hub = node('MLB417791', 'Kits de Motor', wildcard);
    const child = node('MLB420386', 'Kits Elétricos', base + 'eletricos/');
    const sibling = node('MLB420395', 'Kits a Combustão', base + 'combustao/');
    const parentPage = { categoryDocumentIdentity: true, effectiveCategoryId: parts.key,
        categoryContext: { key: parts.key, baseUrl: parts.url }, anuncios: [{ id: 'ancestor-item' }] };
    const trends = { categoryTrends: true, categoryDocumentIdentity: false, effectiveCategoryId: '',
        categoryContext: { key: hub.key, baseUrl: base }, categoryBrowse: { key: hub.key, url: wildcard }, categories: [child, sibling], anuncios: [] };
    const fixture = (patch = {}) => {
        const h = runnerFixture({ [wildcard]: parentPage, [base]: poll => typeof patch === 'function' ? patch(trends, poll) : { ...trends, ...patch },
            [child.url]: { categoryDocumentIdentity: true, effectiveCategoryId: child.key, anuncios: [{ id: 'leaf-item' }] },
            [sibling.url]: { categoryDocumentIdentity: true, effectiveCategoryId: sibling.key, anuncios: [{ id: 'sibling-item' }] } }, child);
        const children = { [parts.key]: [hub], [hub.key]: [child, sibling], [child.key]: [], [sibling.key]: [] };
        h.runner.checkpoint.officialTaxonomy = { ancestry: [parts, hub, child], children };
        h.runner.officialNavigation = navigation({ ancestry: [parts, hub, child], children, read: (...args) => h.runner.read(...args) });
        h.runner.officialNavigation.routes.set(parts.key, parts.url); h.runner.officialNavigation.routes.set(hub.key, wildcard);
        h.runner.discoverOfficialCategoryRoute = async () => null;
        return h;
    };
    const valid = fixture();
    const page = await valid.runner.readCategory(node(child.key, child.nome));
    assert.equal(page.effectiveCategoryId, child.key, 'the destination still proves its own leaf identity');
    assert.deepEqual(valid.visited, [wildcard, base, child.url], 'the fallback follows one neutral landing and its observed child without reopening the failed wildcard');
    assert.equal(valid.runner.officialNavigation.links.get(hub.key)[0].key, child.key);
    assert.equal(trends.categoryDocumentIdentity, false); assert.equal(trends.effectiveCategoryId, '', 'Trends never receives the frozen parent ID as document proof');
    assert.equal(await valid.runner.officialNavigation.resolve(sibling.key), sibling.url);
    assert.deepEqual(valid.visited, [wildcard, base, child.url], 'the other observed child remains available without reloading the parent');
    const ordinaryDocument = fixture({ categoryTrends: false, categoryBrowse: null, categoryDocumentIdentity: true, effectiveCategoryId: hub.key });
    assert.equal(await ordinaryDocument.runner.officialNavigation.resolve(child.key), child.url,
        'the fallback also retains normal navigation when a real parent document proves its own ID');
    let reads = 0;
    const changing = fixture(value => ({ ...value, categories: ++reads === 2 ? [{ ...child, url: base + 'alternative/' }] : [child, sibling] }));
    assert.equal(await changing.runner.officialNavigation.resolve(child.key), child.url);
    assert.equal(reads, 4, 'an inconsistent child href resets the two-read stability guard');
    for (const [description, patch] of [
        ['missing child', { categories: [sibling] }],
        ['ambiguous child', { categories: [child, { ...child, url: base + 'alternative/' }] }],
        ['conflicting child ID', { categories: [{ ...child, key: 'MLB999' }] }],
        ['different context ID', { categoryContext: { key: parts.key, baseUrl: base } }],
        ['different context route', { categoryContext: { key: hub.key, baseUrl: parts.url } }],
        ['different browse ID', { categoryBrowse: { key: parts.key, url: wildcard } }],
        ['different browse route', { categoryBrowse: { key: hub.key, url: parts.url + '*_NoIndex_True' } }],
        ['conflicting own ID', { categoryDocumentIdentity: true, effectiveCategoryId: 'MLB999' }],
        ['identity conflict', { categoryIdentityConflict: true }],
        ['ordinary incomplete document', { categoryTrends: false, categoryBrowse: null }]
    ]) {
        const h = fixture(patch);
        await assert.rejects(h.runner.officialNavigation.resolve(child.key), error => error.code === 'category_scope_mismatch'
            && error.categoryPage.url === wildcard && error.categoryPage.effectiveCategoryId === parts.key, description);
        assert.deepEqual(h.visited, [wildcard, base], description + ' preserves the primary failure instead of forcing its wildcard');
    }
    const unrelatedAncestor = fixture();
    const execute = unrelatedAncestor.runner.browser.executeJavaScript;
    unrelatedAncestor.runner.browser.executeJavaScript = async (...args) => {
        const page = await execute(...args);
        return page.url === wildcard ? { ...page, effectiveCategoryId: 'MLB999', categoryContext: { key: 'MLB999', baseUrl: parts.url } } : page;
    };
    await assert.rejects(unrelatedAncestor.runner.officialNavigation.resolve(child.key), error => error.code === 'category_scope_mismatch');
    assert.deepEqual(unrelatedAncestor.visited, [wildcard], 'an unrelated own ID cannot authorize the parent landing fallback');
    for (const kind of ['auth', 'access', 'abort']) {
        const h = fixture(kind === 'auth' ? { authRequired: true } : {});
        if (kind === 'access') h.runner.browser.getNavigationStatus = () => ({ url: h.runner.browser.getURL(), statusCode: h.runner.browser.getURL() === base ? 403 : 200 });
        if (kind === 'abort') { const navigate = h.runner.browser.navigate; h.runner.browser.navigate = async url => { await navigate(url); if (url === base) throw Object.assign(new Error('Cancelled'), { name: 'AbortError' }); }; }
        await assert.rejects(h.runner.officialNavigation.resolve(child.key), error => kind === 'auth' ? error.waitingLogin === true
            : kind === 'access' ? error.code === 'category_access_denied' : error.name === 'AbortError');
        assert.deepEqual(h.visited, [wildcard, base]);
    }
    const ordinary = fixture();
    await assert.rejects(ordinary.runner.read(base, 'navigation', hub.key, child), error => error.code === 'category_scope_mismatch');
    assert.deepEqual(ordinary.visited, [base, wildcard], 'ordinary navigation retains its existing Trends transition behavior');
    const strict = fixture();
    await assert.rejects(strict.runner.read(base, 'category-transport', hub.key, { ...child, preferTrendsChildren: true }),
        error => error.code === 'category_scope_mismatch', 'the private navigation flag cannot verify a parent listing');
}

(async () => {
    await expandedCategoryPages();
    await runnerTrendsTransition();
    await runnerExpandedOptionsWaitForDestination();
    await observedMissingRoutesStayCandidates();
    await observedLegacyPathRequiresDestinationIdentity();
    await staticHubOnlySuppliesObservedChildLinks();
    await navigationChildLinksDoNotReplaceDocumentIdentity();
    await cachedSiblingLinksSurviveFailedRefresh();
    await neutralLandingMustProveTheLeaf();
    await failedWildcardCanUseStableTrendsChildLinks();
    console.log('Pesquisa de Mercado navigation: observed NoIndex routes, category IDs, safe expanded filters and proven Trends transitions OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
