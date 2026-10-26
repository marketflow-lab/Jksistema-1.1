'use strict';
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const scripts = require('../static/pesquisa-mercado/page-scripts');

async function main() {
    const browser = await chromium.launch({ headless: true });
    try {
        const page = await browser.newPage();
        await page.route('**/*', route => route.fulfill({ contentType: 'text/html', body: '<main></main>' }));
        const listing = 'https://lista.mercadolivre.com.br/esportes-fitness/ciclismo/';
        await page.goto(listing);
        const read = async (payload, suffix = '', prefix = '_n.ctx.r=', id = '__NORDIC_RENDERING_CTX__', body = '<h1>Categoria de teste</h1>') => {
            const literal = typeof payload === 'string' ? payload : JSON.stringify(payload);
            await page.setContent('<main>' + body + '</main><script type="application/json" id="' + id + '">' + prefix + literal + suffix + '</script>');
            return page.evaluate(scripts.discoveryScript());
        };
        const applied = { appProps: { pageProps: { initialState: { filters: [{ id: 'category', values: [{ id: 'MLB122156' }] }],
            note: 'Brace } and escaped " quote with \\ slash' } } } };
        const valid = await read(applied, ';globalThis.__nordicUnsafe=true;');
        assert.equal(valid.effectiveCategoryId, 'MLB122156');
        assert.equal(valid.categoryDocumentIdentity, true);
        assert.equal(await page.evaluate(() => globalThis.__nordicUnsafe), undefined, 'JSON literal parsing never executes trailing runtime code');
        const trends = { appProps: { pageProps: { initialState: { filters: [],
            search_filters: [{ id: 'category', value: 'MLB1292', default_checked: false }, { id: 'allML', default_checked: true }],
            available_filters: [{ id: 'category', values: [{ id: 'MLB1292' }] }],
            recommendations: { category_id: 'MLB1292' }, results: [{ category_id: 'MLB1292', trend: true }] } } } };
        const optional = await read(trends);
        assert.equal(optional.effectiveCategoryId, '');
        assert.equal(optional.categoryDocumentIdentity, false, 'trend options and available category filters cannot prove a listing category');
        await page.goto(listing + '?category_id=MLB122156');
        const urlOnly = await read(trends);
        assert.equal(urlOnly.effectiveCategoryId, 'MLB122156');
        assert.equal(urlOnly.categoryDocumentIdentity, false, 'a URL category remains separate from document evidence');
        const conflict = await read({ appProps: { pageProps: { initialState: { category_id: 'MLB1292' } } } });
        assert.equal(conflict.effectiveCategoryId, '');
        assert.equal(conflict.categoryIdentityConflict, true);
        await page.goto(listing);
        const malformed = await read('{"appProps":{"pageProps":{"initialState":{"category_id":(globalThis.__nordicUnsafe=true,"MLB122156")}}}}');
        assert.equal(malformed.categoryDocumentIdentity, false, 'JavaScript expressions are not JSON category evidence');
        assert.equal(await page.evaluate(() => globalThis.__nordicUnsafe), undefined);
        const prefixed = await read(applied, '', 'globalThis.__nordicUnsafe=true;_n.ctx.r=');
        assert.equal(prefixed.categoryDocumentIdentity, false, 'only the known initial Nordic assignment is parsed');
        const unrelated = await read(applied, '', '_n.ctx.r=', 'unrelated-state');
        assert.equal(unrelated.categoryDocumentIdentity, false, 'unrelated script payloads do not prove document identity');
        const unrecognized = await read({ title: 'Normal', scopes: { dynamic: { props: { initialState: { category_id: 'MLB1292' } } } }, flags: {}, support: {}, framework: {}, mainEntry: 'fixture' });
        assert.equal(unrecognized.categoryDocumentIdentity, false, 'unobserved scopes are not searched recursively for category IDs');
        const base = 'https://lista.mercadolivre.com.br/esportes-fitness/ciclismo/pecas/coroas/';
        const current = base + 'coroas_NoIndex_True';
        const regular = () => ({ optimizedResponse: {
            search_filter: { name: 'Somente em Coroas', permalink: base + '$query', default_checked: false },
            search_filters: [{ id: 'category', value: 'MLB122156', name: 'Coroas', permalink: base + '$query', default_checked: false },
                { id: 'all_mercadolibre', default_checked: true }],
            canonical_info: { canonical: current },
            sidebar: { components: [{ type: 'BREADCRUMB', label: { text: 'Coroas' }, nodes: [
                { text: 'Ciclismo', url: listing }, { text: 'Peças ', url: base.replace(/coroas\/$/, '') }, { text: 'Coroas', url: base }] },
            { type: 'APPLIED_FILTERS', filters: [] }, { type: 'AVAILABLE_FILTERS', filters: [] }] },
            results: [{ id: 'fixture', state: '__UNDEFINED__', polycard: { note: 'literal u @65 NaN $0', optional: '__REF__', number: '__NAN__', render: '__DOLLAR__' } }]
        } });
        const readRsc = async (payload, { trend = false, split = false } = {}) => {
            const raw = '0:' + JSON.stringify(payload).replace(/"__UNDEFINED__"/g, 'u').replace(/"__REF__"/g, '@65')
                .replace(/"__NAN__"/g, 'NaN').replace(/"__DOLLAR__"/g, '$0') + '\n';
            const parts = split ? [raw.slice(0, 87), raw.slice(87)] : [raw];
            await page.setContent('<main><ol class="ui-search-layout"><li>Fixture</li></ol>' + (trend ? '<div class="ui-category-trends-fixture"></div>' : '')
                + '</main>' + parts.map(part => '<script type="application/json">_n.ctx.s["nr"].q(' + JSON.stringify(part) + ');globalThis.__nordicUnsafe=true;</script>').join(''));
            return page.evaluate(scripts.discoveryScript());
        };
        await page.goto(current);
        const rsc = await readRsc(regular(), { split: true });
        assert.equal(rsc.effectiveCategoryId, 'MLB122156');
        assert.equal(rsc.categoryDocumentIdentity, true, 'listing, canonical path, linked category breadcrumb and coupled category template prove document context');
        assert.deepEqual(rsc.categoryContext, { key: 'MLB122156', nome: 'Coroas', baseUrl: base });
        assert.deepEqual(rsc.categoryStructuredNavigation.listingTemplate, { key: 'MLB122156', nome: 'Coroas', url: base + '$query' });
        assert.equal(rsc.categoryStructuredNavigation.breadcrumbs.length, 3);
        const filters = regular();
        const child = { id: 'MLB122157', name: 'Fixture pública', permalink: base + 'fixture/', tracking: { user_id: 'untrusted-fixture' } };
        filters.optimizedResponse.sidebar.components[2].filters = [{ id: 'category', values: [child,
            { ...child, id: 'MLB122158', permalink: 'https://user:secret@lista.mercadolivre.com.br/fixture/' },
            { ...child, id: 'MLB122159', permalink: base + 'fixture/?token=fixture' }] }];
        const navigation = await readRsc(filters);
        assert.deepEqual(navigation.categoryStructuredNavigation.categories, [{ key: child.id, nome: child.name, url: child.permalink }], 'only coupled public category fields are exposed');
        assert.ok(!JSON.stringify(navigation.categoryStructuredNavigation).includes('untrusted-fixture'));
        const tracking = (key, suffix = '') => '#' + encodeURIComponent('applied_filter_id=category&applied_value_id=' + key + '&applied_value_name=Fixture&view_more_flag=true' + suffix);
        const expanded = regular(), emittedMore = current + '_FiltersAvailableSidebar?filter=category';
        const tracked = { id: 'MLB122160', name: 'Expanded only', url: base + 'expanded/*_NoIndex_True' + tracking('MLB122160') };
        const repeated = { id: 'MLB122161', name: 'Repeated', url: base + 'repeated/*_NoIndex_True' + tracking('MLB122161') };
        const conflicted = { id: 'MLB122162', name: 'Conflict', url: base + 'conflict/*_NoIndex_True' + tracking('MLB122162') };
        expanded.optimizedResponse.sidebar.components[2].filters = [{ id: 'category', url: emittedMore, values: [repeated, conflicted],
            expanded_values: [tracked, repeated, { ...conflicted, url: base + 'different/*_NoIndex_True' + tracking(conflicted.id) }] }];
        const expandedPage = await readRsc(expanded);
        assert.deepEqual(expandedPage.categoryStructuredNavigation.categories, [
            { key: repeated.id, nome: repeated.name, url: repeated.url.split('#')[0] },
            { key: tracked.id, nome: tracked.name, url: tracked.url.split('#')[0] }
        ], 'RSC category values and expanded_values retain coupled IDs, deduplicate equal routes and discard conflicting IDs');
        assert.equal(expandedPage.categoryFilterMoreUrl, emittedMore, 'the RSC category group exposes its emitted exact-document expansion URL');
        assert.equal(expandedPage.categories.some(value => value.key === tracked.id), true, 'an expanded-only child reaches the production navigation list');
        assert.deepEqual([expandedPage.effectiveCategoryId, expandedPage.categoryDocumentIdentity, expandedPage.categoryIdentityConflict],
            [rsc.effectiveCategoryId, rsc.categoryDocumentIdentity, rsc.categoryIdentityConflict], 'navigation expansion cannot change document identity');
        assert.ok(!JSON.stringify(expandedPage.categoryStructuredNavigation).includes('applied_filter'), 'tracking fields are validated then discarded');
        for (const hash of [tracking('MLB999'), tracking(tracked.id, '&applied_value_id=' + tracked.id), tracking(tracked.id, '&token=fixture'),
            '#applied_filter_id=brand&applied_value_id=' + tracked.id, '#%E0%A4%A', '#wid=MLB123456789']) {
            const invalid = regular(); invalid.optimizedResponse.available_filters = [{ id: 'category', expanded_values: [{ ...tracked, url: tracked.url.split('#')[0] + hash }] }];
            assert.deepEqual((await readRsc(invalid)).categoryStructuredNavigation.categories, [], 'malformed or uncoupled tracking cannot supply a category route');
        }
        for (const wrongMore of [emittedMore + '&filter=category', emittedMore.replace('filter=category', 'filter=brand'),
            emittedMore.replace('/coroas/', '/other/'), emittedMore.replace('lista.mercadolivre.com.br', 'example.com'), emittedMore + '#category',
            emittedMore.replace('https://', 'https://user:secret@'), current + '?filter=category']) {
            const invalid = regular(); invalid.optimizedResponse.available_filters = [{ id: 'category', url: wrongMore, expanded_values: [tracked] }];
            assert.equal((await readRsc(invalid)).categoryFilterMoreUrl, '', 'an expansion endpoint must belong to this exact document and only the category filter');
        }
        const unrelatedFilter = regular(); unrelatedFilter.optimizedResponse.available_filters = [{ id: 'brand', url: emittedMore, expanded_values: [tracked] }];
        const unrelatedNavigation = await readRsc(unrelatedFilter);
        assert.deepEqual(unrelatedNavigation.categoryStructuredNavigation.categories, []); assert.equal(unrelatedNavigation.categoryFilterMoreUrl, '');
        const bounded = regular(); bounded.optimizedResponse.available_filters = [{ id: 'category', expanded_values: [...Array(1000).fill(null), tracked] }];
        assert.deepEqual((await readRsc(bounded)).categoryStructuredNavigation.categories, [], 'expanded category metadata is bounded before extraction');
        const availableOnly = await readRsc({ optimizedResponse: { available_filters: [{ id: 'category', url: emittedMore, expanded_values: [tracked] }] } });
        assert.equal(availableOnly.categoryDocumentIdentity, false); assert.equal(availableOnly.effectiveCategoryId, '', 'available category IDs remain navigation candidates only');
        assert.equal(await page.evaluate(() => globalThis.__nordicUnsafe), undefined);
        assert.equal((await readRsc(regular(), { trend: true })).categoryDocumentIdentity, false, 'trend documents do not prove a regular category listing');
        const wrongLabel = regular(); wrongLabel.optimizedResponse.sidebar.components[0].nodes.at(-1).text = 'Pedais';
        assert.equal((await readRsc(wrongLabel)).categoryDocumentIdentity, false, 'the linked category name must match the coupled category ID');
        const labelOnly = regular(); labelOnly.optimizedResponse.sidebar.components[0].nodes.pop();
        assert.equal((await readRsc(labelOnly)).categoryDocumentIdentity, false, 'a search term equal to the category name is not category evidence');
        const wrongBreadcrumbUrl = regular(); wrongBreadcrumbUrl.optimizedResponse.sidebar.components[0].nodes.at(-1).url = base + 'other/';
        assert.equal((await readRsc(wrongBreadcrumbUrl)).categoryDocumentIdentity, false, 'a prefix below the category is not its exact breadcrumb URL');
        for (const term of ['*', 'outra-palavra']) {
            const searched = base + encodeURIComponent(term) + '_NoIndex_True';
            await page.goto(searched);
            const query = regular(); query.optimizedResponse.canonical_info.canonical = searched;
            query.optimizedResponse.sidebar.components[0].label.text = term;
            const scoped = await readRsc(query);
            assert.equal(scoped.effectiveCategoryId, 'MLB122156', 'query text does not replace linked category identity');
            assert.equal(scoped.categoryDocumentIdentity, true);
        }
        await page.goto(current);
        const missingBreadcrumb = regular(); missingBreadcrumb.optimizedResponse.sidebar.components = [];
        missingBreadcrumb.optimizedResponse.search_filters[0].default_checked = true;
        assert.equal((await readRsc(missingBreadcrumb)).categoryDocumentIdentity, false, 'checked search options alone are not applied listing filters');
        const wrongCanonical = regular(); wrongCanonical.optimizedResponse.canonical_info.canonical = listing;
        assert.equal((await readRsc(wrongCanonical)).categoryDocumentIdentity, false);
        await page.goto('https://lista.mercadolivre.com.br/coroas/');
        const global = regular(); global.optimizedResponse.canonical_info.canonical = 'https://lista.mercadolivre.com.br/coroas/';
        assert.equal((await readRsc(global)).categoryDocumentIdentity, false, 'generic title search cannot borrow a category template');
        await page.goto(current);
        const appliedOnly = { optimizedResponse: { filters: [{ id: 'category', values: [{ id: 'MLB122156' }] }],
            results: [{ category_id: 'MLB1292' }], available_filters: [{ id: 'category', values: [{ id: 'MLB1292' }] }] } };
        assert.equal((await readRsc(appliedOnly)).effectiveCategoryId, 'MLB122156', 'explicit applied filters remain authoritative without inspecting recommendations');
        appliedOnly.optimizedResponse.filters[0].values[0].id = '__REF__';
        assert.equal((await readRsc(appliedOnly)).categoryDocumentIdentity, false, 'opaque rendering references cannot fabricate category identity');
        const literalListing = () => ({ appProps: { pageProps: { initialState: {
            filters: [],
            search_filter: { name: 'Somente em Coroas', permalink: base + '$query', default_checked: false },
            search_filters: [{ id: 'category', value: 'MLB122156', name: 'Coroas', permalink: base + '$query', default_checked: false }],
            canonical_info: { canonical: base },
            sidebar: { components: [
                { type: 'BREADCRUMB', html_tag: 'h1', label: { text: 'Peças Coroas' }, nodes: [
                    { text: 'Ciclismo', url: listing }, { text: 'Peças', url: base.replace(/coroas\/$/, '') }] },
                { type: 'APPLIED_FILTERS', filters: [] },
                { type: 'AVAILABLE_FILTERS', filters: [{ id: 'category', values: [{ id: 'MLB122157', name: 'Fixture pública', permalink: base + 'fixture/' }] }] }
            ] },
            results: [{ polycard: { metadata: { id: 'MLB123456789', category_id: 'MLB122156' } } }]
        } } } });
        const literalBody = '<h1>Peças Coroas</h1><ol class="ui-search-layout"><li>Fixture pública</li></ol>';
        const readLiteralListing = (payload, body = literalBody) => read(payload, '', '_n.ctx.r=', '__NORDIC_RENDERING_CTX__', body);
        for (const destination of [base, base + '_NoIndex_True', base + '_NoIndex_True?original_category_landing=true']) {
            await page.goto(destination);
            const literal = await readLiteralListing(literalListing());
            assert.equal(literal.effectiveCategoryId, 'MLB122156');
            assert.equal(literal.categoryDocumentIdentity, true, 'a literal listing needs the exact selected base, DOM heading and ordered ancestor breadcrumbs');
            assert.deepEqual(literal.categoryContext, { key: 'MLB122156', nome: 'Coroas', baseUrl: base });
            assert.deepEqual(literal.categoryStructuredNavigation.categories, [{ key: 'MLB122157', nome: 'Fixture pública', url: base + 'fixture/' }]);
            assert.equal(literal.categoryStructuredNavigation.breadcrumbs.length, 2);
            assert.deepEqual(literal.categoryItems, [{ id: 'MLB123456789', categoryId: 'MLB122156' }]);
        }
        const literalWithRoot = (includeCurrent = false) => {
            const state = literalListing(), linked = state.appProps.pageProps.initialState.sidebar.components[0].nodes;
            linked.unshift({ text: 'Esportes e Fitness', url: 'https://www.mercadolivre.com.br/c/esportes-e-fitness' });
            if (includeCurrent) linked.push({ text: 'Coroas', url: base, id: 'MLB122156' });
            return state;
        };
        for (const destination of [base, base + '_NoIndex_True', base + '_NoIndex_True?original_category_landing=true']) {
            await page.goto(destination);
            for (const includeCurrent of [false, true]) {
                const literal = await readLiteralListing(literalWithRoot(includeCurrent));
                assert.equal(literal.effectiveCategoryId, 'MLB122156');
                assert.equal(literal.categoryDocumentIdentity, true, 'the observed www/c root and optional final current-category link preserve literal document identity');
                assert.equal(literal.categoryStructuredNavigation.breadcrumbs[0].url, 'https://www.mercadolivre.com.br/c/esportes-e-fitness');
            }
        }
        await page.goto(base);
        for (const mutate of [
            state => { const linked = state.sidebar.components[0].nodes; linked.splice(1, 0, linked.shift()); },
            state => { state.sidebar.components[0].nodes[0].url = 'https://m.mercadolivre.com.br/c/esportes-e-fitness'; },
            state => { state.sidebar.components[0].nodes[0].url = 'https://example.com/c/esportes-e-fitness'; },
            state => { state.sidebar.components[0].nodes[0].url = 'https://www.mercadolivre.com.br/categorias/esportes-e-fitness'; },
            state => { state.sidebar.components[0].nodes[0].url = 'https://www.mercadolivre.com.br/c/'; },
            state => { state.sidebar.components[0].nodes[0].url = 'https://www.mercadolivre.com.br/c/esportes-e-fitness/outro'; },
            state => { state.sidebar.components[0].nodes[0].url = 'https://www.mercadolivre.com.br/c/esportes%2Fe-fitness'; },
            state => { state.sidebar.components[0].nodes[0].url = 'https://www.mercadolivre.com.br:8443/c/esportes-e-fitness'; },
            state => { state.sidebar.components[0].nodes[0].url += '?category_id=MLB1276'; },
            state => { state.sidebar.components[0].nodes.at(-1).text = 'Pedais'; },
            state => { state.sidebar.components[0].nodes.at(-1).url = base.replace(/coroas\/$/, 'pedais/'); },
            state => { state.sidebar.components[0].nodes.at(-1).id = 'MLB1934'; },
            state => { state.sidebar.components[0].nodes.push({ text: 'Peças', url: base.replace(/coroas\/$/, '') }); },
            state => { state.sidebar.components[0].nodes.push({ text: 'Coroas', url: base }); },
            state => { state.canonical_info.canonical = base + 'outra-palavra'; }
        ]) {
            const invalid = literalWithRoot(true); mutate(invalid.appProps.pageProps.initialState);
            assert.equal((await readLiteralListing(invalid)).categoryDocumentIdentity, false, 'root position/route and current-category conflicts cannot establish literal identity');
        }
        assert.equal((await readLiteralListing(literalWithRoot(true), literalBody + '<div class="ui-category-trends-fixture"></div>')).categoryDocumentIdentity, false, 'a full category breadcrumb cannot promote a Trends hub to a listing');
        assert.equal((await readLiteralListing(literalWithRoot(true), literalBody.replace('Peças Coroas', 'Peças Pedais'))).categoryDocumentIdentity, false, 'a linked current category still needs the compatible DOM h1');
        await page.goto(base + 'coroas_NoIndex_True');
        const literalKeyword = literalWithRoot(true); literalKeyword.appProps.pageProps.initialState.canonical_info.canonical = base + 'coroas_NoIndex_True';
        assert.equal((await readLiteralListing(literalKeyword)).categoryDocumentIdentity, false, 'a category-named keyword URL cannot borrow the current linked category');
        await page.goto(base);
        for (const mutate of [
            state => { state.sidebar.components[0].html_tag = 'span'; },
            state => { state.sidebar.components[0].label.text = 'Peças Pedais'; },
            state => { state.sidebar.components[0].nodes.reverse(); },
            state => { state.sidebar.components[0].nodes = []; },
            state => { state.sidebar.components[0].nodes = [{ text: 'Início', url: 'https://lista.mercadolivre.com.br/' }]; },
            state => { state.sidebar.components[0].nodes.at(-1).url = base; },
            state => { state.sidebar.components[0].nodes.at(-1).url = listing + 'outra/'; },
            state => { state.sidebar.components[0].nodes.at(-1).url += '?category_id=MLB1934'; },
            state => { state.search_filter.permalink = listing + '$query'; },
            state => { state.search_filter.permalink = state.search_filters[0].permalink = listing + 'pecas/%ZZ/$query'; },
            state => { state.search_filters.push(state.search_filters[0]); },
            state => { state.canonical_info.canonical = listing; },
            state => { state.results = [{ recommendations: { polycard: {} } }]; }
        ]) {
            const invalid = literalListing(); mutate(invalid.appProps.pageProps.initialState);
            assert.equal((await readLiteralListing(invalid)).categoryDocumentIdentity, false, 'literal metadata cannot replace missing or conflicting page evidence');
        }
        for (const body of [literalBody.replace('Peças Coroas', 'Peças Pedais'), literalBody + '<h1>Outra categoria</h1>', literalBody.replace('ui-search-layout', 'unrelated'), literalBody + '<div class="ui-category-trends-fixture"></div>']) {
            assert.equal((await readLiteralListing(literalListing(), body)).categoryDocumentIdentity, false, 'the literal alone cannot prove a listing without the matching unique DOM heading');
        }
        for (const destination of [base + 'outra-palavra', base + '*_NoIndex_True', base + '_Desde_49_NoIndex_True',
            base + '?q=coroas', base + '?original_category_landing=false', base + '?original_category_landing=true&seller_id=123']) {
            await page.goto(destination);
            const invalid = literalListing(); invalid.appProps.pageProps.initialState.canonical_info.canonical = destination;
            assert.equal((await readLiteralListing(invalid)).categoryDocumentIdentity, false, 'a query, pagination or deeper path cannot borrow literal category context');
        }
        const vBrakeBase = listing + 'pecas/freios/v-brake/';
        const childBase = vBrakeBase + 'kits-freios-v-brake/';
        await page.goto(childBase + '_NoIndex_True?original_category_landing=true');
        const parentContext = literalListing(), parentState = parentContext.appProps.pageProps.initialState;
        parentState.search_filter.permalink = vBrakeBase + '$query';
        parentState.search_filters[0] = { id: 'category', value: 'MLB371933', name: 'V-Brake', permalink: vBrakeBase + '$query', default_checked: false };
        parentState.canonical_info.canonical = childBase + '_NoIndex_True';
        parentState.sidebar.components[0].label.text = 'Peças Freios V-Brake';
        parentState.sidebar.components[0].nodes.push({ text: 'Freios', url: listing + 'pecas/freios/' });
        parentState.sidebar.components[2].filters[0].values = [{ id: 'MLB122186', name: 'Kits de Freios V-brake', permalink: childBase }];
        const scopedParent = await readLiteralListing(parentContext, literalBody.replace('Peças Coroas', 'Peças Freios V-Brake'));
        assert.equal(scopedParent.categoryDocumentIdentity, false, 'the observed child canonical with parent context and mixed cards cannot prove the child or borrow the parent base');
        assert.equal(scopedParent.effectiveCategoryId, '');
        assert.equal(scopedParent.categoryContext.key, 'MLB371933');
        parentState.sidebar.components[0].nodes.unshift({ text: 'Esportes e Fitness', url: 'https://www.mercadolivre.com.br/c/esportes-e-fitness' });
        parentState.sidebar.components[0].nodes.push({ text: 'V-Brake', url: vBrakeBase, id: 'MLB371933' });
        assert.equal((await readLiteralListing(parentContext, literalBody.replace('Peças Coroas', 'Peças Freios V-Brake'))).categoryDocumentIdentity, false, 'even a complete parent breadcrumb cannot give a deeper child URL its parent identity');
        await page.goto(base);
        const literalConflict = literalListing();
        literalConflict.appProps.pageProps.initialState.sidebar.components[1].filters = [{ id: 'category', values: [{ id: 'MLB1292' }] }];
        assert.equal((await readLiteralListing(literalConflict)).categoryIdentityConflict, true, 'literal sidebar applied filters remain authoritative and conflicting identities block the document');
        const literalApplied = { appProps: { pageProps: { initialState: { sidebar: { components: [{ type: 'APPLIED_FILTERS', filters: [{ id: 'category', values: [{ id: 'MLB122156' }] }] }] } } } } };
        assert.equal((await readLiteralListing(literalApplied)).effectiveCategoryId, 'MLB122156', 'literal sidebar applied filters work independently of contextual inference');
        const itemResult = (id, categoryId, extra = {}) => ({ polycard: { metadata: { id, category_id: categoryId, ...extra } } });
        const itemState = literalListing();
        itemState.appProps.pageProps.initialState.results = [
            itemResult('MLB999', 'MLB122186', { category_path: ['public fixture'], domain_id: 'MLB-FIXTURE', seller_id: 'private-fixture', token: 'secret-fixture' }),
            itemResult('MLB999', 'MLB122186'),
            itemResult('MLB1000', 'MLB278054'),
            itemResult('MLB1001', 'MLB122186'), itemResult('MLB1001', 'MLB278054'), itemResult('MLB1001', 'MLB122186'),
            { polycard: { metadata: { id: 'MLB1002' }, category_id: 'MLB122186' } },
            { polycard: { id: 'MLB1003', metadata: { category_id: 'MLB122186' } } },
            { metadata: { id: 'MLB1004', category_id: 'MLB122186' } },
            { recommendations: { polycard: { metadata: { id: 'MLB1005', category_id: 'MLB122186' } } } },
            itemResult('MLB-1006', 'MLB122186'), itemResult('MLB1007', 'invalid'), itemResult(1008, 'MLB122186'), itemResult('MLB1009', 122186)
        ];
        const literalItems = await readLiteralListing(itemState);
        assert.deepEqual(literalItems.categoryItems, [{ id: 'MLB999', categoryId: 'MLB122186' }, { id: 'MLB1000', categoryId: 'MLB278054' }], 'only IDs coupled in the same polycard metadata are exposed; conflicting item categories are removed permanently');
        assert.equal(literalItems.effectiveCategoryId, 'MLB122156', 'item membership is separate from global document identity');
        assert.equal(literalItems.categoryIdentityConflict, false, 'item conflicts do not fabricate a global category conflict');
        assert.ok(!JSON.stringify(literalItems.categoryItems).includes('private-fixture'));
        assert.ok(!JSON.stringify(literalItems.categoryItems).includes('secret-fixture'));
        const rscItems = await readRsc({ optimizedResponse: { results: [itemResult('MLB2000', 'MLB122186'), itemResult('MLB2001', 'MLB278054')] } });
        assert.deepEqual(rscItems.categoryItems, [{ id: 'MLB2000', categoryId: 'MLB122186' }, { id: 'MLB2001', categoryId: 'MLB278054' }]);
        assert.equal(rscItems.categoryDocumentIdentity, false, 'RSC result metadata alone cannot establish document scope');
        const mixedState = literalListing(); mixedState.appProps.pageProps.initialState.results = [itemResult('MLB3000', 'MLB122186'), itemResult('MLB3001', 'MLB278054')];
        await readLiteralListing(mixedState);
        const mixedRsc = '0:' + JSON.stringify({ optimizedResponse: { results: [itemResult('MLB3000', 'MLB278054'), itemResult('MLB3002', 'MLB122186')] } }) + '\n';
        await page.addScriptTag({ type: 'application/json', content: '_n.ctx.s["nr"].q(' + JSON.stringify(mixedRsc) + ');' });
        const mixedItems = await page.evaluate(scripts.discoveryScript());
        assert.deepEqual(mixedItems.categoryItems, [{ id: 'MLB3001', categoryId: 'MLB278054' }, { id: 'MLB3002', categoryId: 'MLB122186' }], 'conflicting item membership across literal and RSC transport is excluded');
        assert.equal(mixedItems.categoryDocumentIdentity, true);
        const browseState = () => ({ appProps: { pageProps: {
            categories: [{ id: 'category', name: 'em Coroas', permalink: base + '$query_NoIndex_True', default_checked: false },
                { id: 'all_mercadolibre', permalink: null, default_checked: true }],
            initialState: {
                filters: [], results: [{ trend: true }],
                search_filter: { name: 'Somente em Coroas', permalink: base + '$query', default_checked: false },
                search_filters: [{ id: 'category', value: 'MLB122156', name: 'Coroas', permalink: base + '$query', default_checked: false }],
                breadcrumb: [{ text: 'Ciclismo', url: listing }, { text: 'Coroas', url: base }],
                canonical_info: { canonical: base }
            }
        } } });
        const trendBody = '<div class="ui-category-trends-fixture">Fixture pública de tendências</div>';
        const readBrowse = payload => read(payload, '', '_n.ctx.r=', '__NORDIC_RENDERING_CTX__', trendBody);
        await page.goto(base);
        const browse = await readBrowse(browseState());
        assert.deepEqual(browse.categoryBrowse, { key: 'MLB122156', nome: 'Coroas', url: base + '*_NoIndex_True' }, 'only the exact emitted query placeholder is materialized');
        assert.equal(browse.categoryDocumentIdentity, false, 'a transition is not evidence for the next document');
        assert.equal(browse.effectiveCategoryId, '');
        assert.equal((await read(browseState())).categoryBrowse, null, 'regular documents do not emit trend transitions');
        for (const mutate of [
            state => { state.appProps.pageProps.categories = []; },
            state => { state.appProps.pageProps.categories[0].permalink = base + '$query'; },
            state => { state.appProps.pageProps.categories[0].permalink = listing + '$query_NoIndex_True'; },
            state => { state.appProps.pageProps.categories[0].permalink += '?token=fixture'; },
            state => { state.appProps.pageProps.initialState.search_filter.permalink = listing + '$query'; },
            state => { state.appProps.pageProps.initialState.search_filters[0].value = 'unknown'; },
            state => { state.appProps.pageProps.initialState.search_filters.push(state.appProps.pageProps.initialState.search_filters[0]); },
            state => { state.appProps.pageProps.initialState.canonical_info.canonical = listing; },
            state => { state.appProps.pageProps.initialState.breadcrumb.at(-1).url = listing; },
            state => { state.appProps.pageProps.initialState.breadcrumb.at(-1).text = 'Pedais'; }
        ]) {
            const invalid = browseState(); mutate(invalid);
            assert.equal((await readBrowse(invalid)).categoryBrowse, null, 'uncoupled or incomplete templates cannot create a browse URL');
        }
        for (const destination of [base + '*_NoIndex_True', base + 'outra-palavra']) {
            await page.goto(destination);
            assert.equal((await readBrowse(browseState())).categoryBrowse, null, 'an already opened query cannot produce another browse transition');
        }
        const parentListing = listing + '*_NoIndex_True';
        const moreUrl = parentListing + '_FiltersAvailableSidebar?filter=category';
        const piecesUrl = listing + 'pecas/';
        const clothingUrl = listing + 'roupas/';
        const modalBody = '<div class="ui-search-modal"><a class="ui-search-search-modal-filter" href="' + piecesUrl + '?filter=category">Peças</a>'
            + '<a class="ui-search-search-modal-filter" href="' + clothingUrl + '?filter=category">Roupas</a>'
            + '<section class="carousel"><a class="ui-search-search-modal-filter" href="' + listing + 'promocao/?filter=category">Oferta recomendada</a></section>'
            + '<a class="ui-search-search-modal-filter" href="https://lista.mercadolivre.com.br/_CustId_123?filter=category">Vendedor</a></div>'
            + '<a class="ui-search-search-modal-filter" href="' + listing + 'fora-do-modal/">Link fora do modal</a>';
        const readMarkup = async body => {
            await page.setContent('<main>' + body + '</main>');
            return page.evaluate(scripts.discoveryScript());
        };
        await page.goto(moreUrl);
        const modal = await readMarkup(modalBody);
        assert.equal(modal.categoryFilterPresent, false, 'the observed expanded modal does not need a category heading');
        assert.deepEqual(modal.categories.map(({ nome, url }) => ({ nome, url })), [
            { nome: 'Peças', url: piecesUrl }, { nome: 'Roupas', url: clothingUrl }
        ], 'only links inside the category modal are read and its exact functional query is removed');
        assert.equal(modal.categoryFilterMoreUrl, '');
        assert.equal(modal.categoryDocumentIdentity, false, 'category options are not applied category evidence');
        await page.goto(parentListing + '_FiltersAvailableSidebar?filter=brand');
        assert.deepEqual((await readMarkup(modalBody)).categories, [], 'another filter modal cannot provide category navigation');
        await page.goto(moreUrl);
        assert.deepEqual((await readMarkup(modalBody.replace('class="ui-search-modal"', 'class="unrelated-modal"'))).categories, [], 'the modal route alone cannot authorize arbitrary links');
        const moreGroup = (href = moreUrl, heading = 'Categorias') => '<dl class="ui-search-filter-dl"><h3>' + heading + '</h3>'
            + '<a href="' + piecesUrl + '">Peças</a><a href="' + href + '">Mostrar mais</a></dl>';
        await page.goto(parentListing);
        const more = await readMarkup(moreGroup());
        assert.equal(more.categoryFilterMoreUrl, moreUrl, 'the category filter exposes its observed exact-document expansion URL');
        assert.deepEqual(more.categories.map(item => item.nome), ['Peças'], 'Mostrar mais is a control and never a child category');
        for (const label of ['Show more', 'Ver más', '']) {
            const translated = await readMarkup(moreGroup().replace('Mostrar mais', label));
            assert.equal(translated.categoryFilterMoreUrl, moreUrl, 'the exact endpoint identifies expansion independently of its visible language');
            assert.deepEqual(translated.categories.map(item => item.nome), ['Peças'], 'a translated or icon-only expansion is never a child category');
        }
        const typedGroup = await readMarkup(moreGroup().replace('<dl ', '<dl data-filter-id="category" ').replace('Categorias', 'Categories').replace('Mostrar mais', 'See all'));
        assert.equal(typedGroup.categoryFilterMoreUrl, moreUrl, 'a typed category group does not depend on a Portuguese heading or control label');
        for (const [description, body] of [
            ['brand group', moreGroup(moreUrl, 'Marca')],
            ['nested brand group', '<dl data-filter-id="category">' + moreGroup(moreUrl, 'Marca') + '</dl>'],
            ['another filter', moreGroup(moreUrl.replace('?filter=category', '?filter=brand'))],
            ['seller filter', moreGroup(moreUrl + '&seller_id=123')],
            ['another document', moreGroup(listing + 'outro_NoIndex_True_FiltersAvailableSidebar?filter=category')],
            ['another host', moreGroup(moreUrl.replace('lista.mercadolivre.com.br', 'www.mercadolivre.com.br'))],
            ['unrelated footer', '<footer><a href="' + moreUrl + '">Mostrar mais</a></footer>'],
            ['promotional carousel', '<section class="carousel">' + moreGroup() + '</section>']
        ]) assert.equal((await readMarkup(body)).categoryFilterMoreUrl, '', description + ' cannot provide the category expansion control');
        await page.goto(listing);
        const hubMarkup = '<link rel="canonical" href="' + listing + '"><section class="ui-category-trends-desktop-content"><h1>Ciclismo</h1>'
            + '<dl><dt>Categorias</dt><dd><a href="' + listing + 'pecas/">Peças</a></dd></dl></section>';
        const staticHub = await readMarkup(hubMarkup);
        assert.deepEqual(staticHub.categoryNavigationHub, { version: 1, url: listing, canonical: listing, heading: 'Ciclismo' });
        assert.equal(staticHub.categoryDocumentIdentity, false);
        assert.equal(staticHub.effectiveCategoryId, '');
        for (const [description, markup] of [
            ['missing canonical', hubMarkup.replace(/<link[^>]+>/, '')],
            ['different canonical', hubMarkup.replace('href="' + listing + '"', 'href="' + listing + 'outros/"')],
            ['duplicate canonical', hubMarkup + '<link rel="canonical" href="' + listing + '">'],
            ['duplicate heading', hubMarkup + '<h1>Outra categoria</h1>'],
            ['missing component', hubMarkup.replace('ui-category-trends-desktop-content', 'ordinary-content')]
        ]) assert.equal((await readMarkup(markup)).categoryNavigationHub, null, description);
        await page.goto(listing + '?q=pecas');
        assert.equal((await readMarkup(hubMarkup.replace('href="' + listing + '"', 'href="' + listing + '?q=pecas"'))).categoryNavigationHub, null,
            'a keyword/query route cannot provide static hub navigation');
        for (const path of ['/captcha', '/captcha/wall/logged']) {
            await page.goto('https://www.mercadolivre.com.br' + path + '?go_url=https%3A%2F%2Flista.mercadolivre.com.br%2Fcategoria');
            const verification = await read(applied, '', '_n.ctx.r=', '__NORDIC_RENDERING_CTX__', hubMarkup);
            assert.equal(verification.authRequired, true, 'a verification wall pauses even without captcha inputs or frames');
            assert.equal(verification.categoryDocumentIdentity, false, 'verification pages never acquire category identity from embedded state');
            assert.equal(verification.effectiveCategoryId, '');
            assert.equal(verification.categoryNavigationHub, null);
        }
        await page.evaluate(() => document.replaceChildren());
        const transitioning = await page.evaluate(scripts.discoveryScript());
        assert.equal(transitioning.anuncios.length, 0, 'a transient document without body has no discovery seeds');
        assert.equal(transitioning.categoryDocumentIdentity, false, 'an empty transition document cannot prove category identity');
        assert.equal(transitioning.empty, false, 'an unloaded document is not a confirmed empty category');
        console.log('Nordic category identity: bounded JSON literal, applied filters, trend exclusion, URL separation, conflict, transient document and no execution OK');
    } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
