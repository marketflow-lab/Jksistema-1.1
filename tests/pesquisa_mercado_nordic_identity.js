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
        for (const [description, body] of [
            ['brand group', moreGroup(moreUrl, 'Marca')],
            ['another filter', moreGroup(moreUrl.replace('?filter=category', '?filter=brand'))],
            ['seller filter', moreGroup(moreUrl + '&seller_id=123')],
            ['another document', moreGroup(listing + 'outro_NoIndex_True_FiltersAvailableSidebar?filter=category')],
            ['another host', moreGroup(moreUrl.replace('lista.mercadolivre.com.br', 'www.mercadolivre.com.br'))],
            ['unrelated footer', '<footer><a href="' + moreUrl + '">Mostrar mais</a></footer>'],
            ['promotional carousel', '<section class="carousel">' + moreGroup() + '</section>']
        ]) assert.equal((await readMarkup(body)).categoryFilterMoreUrl, '', description + ' cannot provide the category expansion control');
        console.log('Nordic category identity: bounded JSON literal, applied filters, trend exclusion, URL separation, conflict and no execution OK');
    } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
