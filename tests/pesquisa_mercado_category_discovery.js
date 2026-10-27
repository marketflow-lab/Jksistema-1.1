'use strict';

const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { discoverCategoryRoute } = require('../electron_app/main/modules/pesquisa-mercado-category-discovery');

const INDEX = 'https://www.mercadolivre.com.br/categorias';
const SEARCH = 'https://www.mercadolivre.com.br/jm/search';
const RESULTS = 'https://lista.mercadolivre.com.br/fixture-public-search/?sb=all_mercadolibre#D[A:fixture]';
const BASE = 'https://lista.mercadolivre.com.br/esportes-fitness/ciclismo/pecas/';
const LEAF = BASE + 'elastomeros/';
const expected = { key: 'MLB122194', nome: 'Elastômeros' };
const ancestry = [{ key: 'MLB1276', nome: 'Esportes e Fitness', url: 'https://www.mercadolivre.com.br/c/esportes-e-fitness' }, { key: 'MLB1292', nome: 'Ciclismo' },
    { key: 'MLB1934', nome: 'Peças' }, expected];
const contextualTerms = ['Elastômeros Peças Ciclismo', 'Elastômeros Ciclismo'];
const itemUrl = id => 'https://www.mercadolivre.com.br/up/MLBU123?wid=' + id + '#D[A:fixture]';
const seed = (id = 'MLB999', categoryId = expected.key) => ({ id, categoryId, url: itemUrl(id) });
const escape = value => String(value).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
const breadcrumbs = (leaf = LEAF, extra = '') => '<nav class="andes-breadcrumb">'
    + '<a href="https://www.mercadolivre.com.br/c/esportes-e-fitness">Esportes e Fitness</a>'
    + '<a href="https://lista.mercadolivre.com.br/esportes-fitness/ciclismo/">...</a>'
    + '<a href="' + BASE + '">Peças</a><a href="' + escape(leaf) + '">Elastômeros</a>' + extra + '</nav>';
const scripts = { discoveryScript: () => `(() => ({ url: document.querySelector('[data-stale-url]')?.dataset.staleUrl || location.href, readyState: document.readyState,
    authRequired: !!document.querySelector('input[type=password]'), avantAuthRequired: false,
    categoryItems: JSON.parse(document.getElementById('memberships')?.textContent || '[]'),
    categories: JSON.parse(document.getElementById('categories')?.textContent || '[]'),
    anuncios: [...document.querySelectorAll('a[data-item-id]')].map(anchor => ({id:anchor.dataset.itemId,url:anchor.href})) }))()` };

async function fixture(browser, options = {}) {
    const page = await browser.newPage();
    const target = options.expected || expected, resultUrl = options.resultUrl || RESULTS, resultPath = new URL(resultUrl).pathname;
    const terms = [], navigations = [], errors = [];
    let status = null, searchReads = 0, swapped = false;
    const rowsFor = term => options.rowsFor ? options.rowsFor(term) : options.rows || [seed()];
    await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        let body = '<main></main>', code = 200;
        if (url.pathname === '/categorias') {
            body = '<main><form method="get" action="' + (options.formAction || SEARCH) + '"><input name="as_word" type="search">'
                + (options.extraInput || '') + '</form>' + (options.login ? '<input type="password">' : '') + '</main>';
            code = options.status || 200;
        } else if (url.pathname === '/jm/search') {
            const term = url.searchParams.get('as_word'); terms.push(term);
            body = '<script>location.replace(' + JSON.stringify(resultUrl) + ')</script>';
        } else if (url.hostname === 'lista.mercadolivre.com.br' && [resultPath, '/fixture-other-search/'].includes(url.pathname)) {
            const rows = rowsFor(terms.at(-1)), memberships = rows.map(({ id, categoryId }) => ({ id, categoryId }));
            const categories = options.categoriesFor ? options.categoriesFor(terms.at(-1)) : options.categories || [];
            const ads = rows.filter(row => row.url).map(row => '<a data-item-id="' + escape(row.id) + '" href="' + escape(row.url) + '">Fixture</a>').join('');
            body = '<main><script type="application/json" id="memberships">' + JSON.stringify(memberships) + '</script><script type="application/json" id="categories">'
                + JSON.stringify(categories) + '</script><div id="ads">' + (options.hydrationDelay && url.pathname === resultPath ? '' : ads) + '</div></main>';
            if (options.hydrationDelay) body += '<script>setTimeout(()=>document.getElementById("ads").innerHTML=' + JSON.stringify(ads) + ',' + options.hydrationDelay + ')</script>';
        } else if (url.pathname.startsWith('/up/')) {
            if (options.wrongItem && url.searchParams.get('wid') !== 'MLB12345') return route.fulfill({ contentType: 'text/html', body: '<script>location.replace(' + JSON.stringify(itemUrl('MLB12345')) + ')</script>' });
            body = '<link rel="canonical" href="' + escape(options.canonical || 'https://www.mercadolivre.com.br/up/MLBU123') + '">'
                + '<main' + (options.staleUrl ? ' data-stale-url="' + escape(RESULTS) + '"' : '') + '>'
                + (options.breadcrumbs === undefined ? breadcrumbs(LEAF, '<a href="' + LEAF + 'reparo">Kit Freio Bicicleta</a>') : options.breadcrumbs) + '</main>';
        }
        await route.fulfill({ status: code, contentType: 'text/html; charset=utf-8', body });
    });
    page.on('response', response => { if (response.request().isNavigationRequest()) status = { statusCode: response.status(), url: response.url() }; });
    const adapter = {
        navigate: async url => { navigations.push(url); await page.goto(url); },
        getURL: () => page.url(), isLoading: () => false, getNavigationStatus: () => status,
        executeJavaScript: async source => {
            if (new URL(page.url()).pathname === resultPath) searchReads += 1;
            let value;
            try { value = await page.evaluate(source); } catch (error) { errors.push(error.message); throw error; }
            if (options.swapDuringBreadcrumb && source.includes('function publicBreadcrumbs') && !swapped) {
                swapped = true; await page.goto(itemUrl('MLB12345'));
            }
            return value;
        }
    };
    const run = signal => discoverCategoryRoute({ browser: adapter, scripts, expected: target, ancestry: options.ancestry || ancestry,
        read: () => { throw new Error('The helper must not validate the final category.'); },
        wait: async () => {
            if (options.abortOnWait) options.abortOnWait();
            if (options.swapSearchAfterRead && searchReads > 0 && !swapped) { swapped = true; await page.goto(RESULTS.replace('/fixture-public-search/', '/fixture-other-search/')); }
            await page.waitForTimeout(5);
        }, signal });
    try { return await options.check({ run, terms, navigations, searchReads: () => searchReads, diagnostics: () => ({ terms, navigations, errors, url: page.url() }) }); }
    finally { await page.close(); }
}

async function main() {
    const browser = await chromium.launch({ headless: true });
    try {
        await fixture(browser, { hydrationDelay: 35, check: async ({ run, terms, navigations, searchReads, diagnostics }) => {
            assert.deepEqual(await run(), { ...expected, url: LEAF }, JSON.stringify(diagnostics()));
            assert.deepEqual(terms, [contextualTerms[0]]);
            assert.equal(navigations[1], itemUrl('MLB999'), 'the original observed item URL and wid are retained');
            assert.ok(searchReads() >= 3, 'literal item metadata must wait for hydrated DOM ads and two stable reads');
            assert.equal(navigations.length, 2, 'the returned category is never opened or counted by discovery');
        } });
        await fixture(browser, { rowsFor: term => term === contextualTerms[1] ? [seed()] : [], check: async ({ run, terms }) => {
            assert.deepEqual(await run(), { ...expected, url: LEAF });
            assert.deepEqual(terms, contextualTerms, 'both bounded searches retain the requested leaf and its domain');
        } });
        await fixture(browser, { expected: { ...expected, nome: 'Outros' }, rows: [], check: async ({ run, terms }) => {
            assert.equal(await run(), null);
            assert.deepEqual(terms, ['Outros Peças Ciclismo', 'Outros Ciclismo'], 'generic leaf labels never become an isolated parent query');
        } });
        await fixture(browser, { ancestry: [ancestry[0], ancestry[1], expected], rows: [], check: async ({ run, terms }) => {
            assert.equal(await run(), null);
            assert.deepEqual(terms, ['Elastômeros Ciclismo'], 'identical parent/domain labels and resulting terms are deduplicated');
        } });
        const cables = { key: 'MLB459353', nome: 'Kits de cabos e câmbios' };
        const cableAncestry = [...ancestry.slice(0, -1), { key: 'MLB9888', nome: 'Freios' }, cables];
        const cableTerms = [cables.nome + ' Freios Ciclismo', cables.nome + ' Ciclismo'];
        const cableBase = BASE + 'freios/kits-cabos-cambios/';
        const typed = { ...cables, url: cableBase + 'cabo-de-freio_NoIndex_True' };
        const globalResult = 'https://lista.mercadolivre.com.br/cabo-de-freio?sb=all_mercadolibre#D[A:fixture]';
        for (const resultUrl of [globalResult, 'https://lista.mercadolivre.com.br/cabo-de-freio/']) {
            await fixture(browser, { expected: cables, ancestry: cableAncestry, resultUrl, categories: [typed], rows: [], check: async ({ run, terms, navigations }) => {
                const candidate = await run();
                assert.deepEqual(candidate, { ...cables, url: cableBase }, 'only the exact observed query suffix is removed from an ID-coupled filter URL');
                assert.deepEqual(Object.keys(candidate).sort(), ['key', 'nome', 'url'], 'a discovered filter route does not promote document identity');
                assert.deepEqual(terms, [cableTerms[0]]); assert.deepEqual(navigations, [INDEX], 'typed routes are returned before opening item seeds or final categories');
            } });
        }
        await fixture(browser, { expected: cables, ancestry: cableAncestry, resultUrl: globalResult,
            categoriesFor: term => term === cableTerms[1] ? [typed] : [],
            rowsFor: term => term === cableTerms[0] ? [seed('MLB999', 'MLB47097')] : [], check: async ({ run, terms, navigations }) => {
            assert.deepEqual(await run(), { ...cables, url: cableBase });
            assert.deepEqual(terms, cableTerms, 'a second contextual query cannot broaden to car brakes through the isolated parent label');
            assert.deepEqual(navigations, [INDEX, INDEX], 'wrong-domain item metadata cannot authorize opening a PDP');
        } });
        for (const [description, options] of [
            ['different category ID', { categories: [{ ...typed, key: 'MLB9888' }] }],
            ['different category label', { categories: [{ ...typed, nome: 'Freios' }] }],
            ['different observed term', { categories: [{ ...typed, url: cableBase + 'outro-termo_NoIndex_True' }] }],
            ['external filter', { categories: [{ ...typed, url: 'https://example.com/kits-cabos-cambios/cabo-de-freio_NoIndex_True' }] }],
            ['filter query', { categories: [{ ...typed, url: typed.url + '?sb=all_mercadolibre' }] }],
            ['no category prefix', { categories: [{ ...typed, url: 'https://lista.mercadolivre.com.br/cabo-de-freio_NoIndex_True' }] }],
            ['nested search context', { resultUrl: 'https://lista.mercadolivre.com.br/pecas/cabo-de-freio' }],
            ['search operator', { resultUrl: 'https://lista.mercadolivre.com.br/cabo-de-freio_NoIndex_True' }],
            ['different query parameter', { resultUrl: 'https://lista.mercadolivre.com.br/cabo-de-freio?category_id=MLB459353' }],
            ['repeated query parameter', { resultUrl: 'https://lista.mercadolivre.com.br/cabo-de-freio?sb=all_mercadolibre&sb=all_mercadolibre' }],
            ['ambiguous exact filter', { categories: [typed, { ...typed, url: BASE + 'freios/outros-cabos/cabo-de-freio_NoIndex_True' }] }]
        ]) await fixture(browser, { expected: cables, ancestry: cableAncestry, resultUrl: globalResult, categories: [typed], rows: [], ...options,
            check: async ({ run, navigations }) => { assert.equal(await run(), null, description); assert.equal(navigations.some(url => url.includes('/up/')), false); } });
        await fixture(browser, { hydrationDelay: 1000, swapSearchAfterRead: true, check: async ({ run, terms, navigations }) => {
            assert.equal(await run(), null, 'a different result document cannot replace the observed search while cards hydrate');
            assert.equal(terms.length, 1); assert.equal(navigations.length, 1);
        } });
        await fixture(browser, { extraInput: '<input type="radio" name="navigations-options" value="all" checked><input type="radio" name="navigations-options" value="scope"><input type="radio" name="unused-fixture" value="ignored">',
            check: async ({ run, terms }) => { assert.deepEqual(await run(), { ...expected, url: LEAF }); assert.equal(terms.length, 1); } });
        await fixture(browser, { breadcrumbs: breadcrumbs().replace('>Esportes e Fitness<', '>...<'), check: async ({ run }) => {
            assert.deepEqual(await run(), { ...expected, url: LEAF }, 'the collapsed root is tied to the known taxonomy root URL');
        } });
        await fixture(browser, { breadcrumbs: breadcrumbs().replace('https://www.mercadolivre.com.br/c/esportes-e-fitness', 'https://lista.mercadolivre.com.br/esportes-fitness/'),
            check: async ({ run }) => { assert.deepEqual(await run(), { ...expected, url: LEAF }); } });
        for (const [description, rows] of [
            ['wrong category', [seed('MLB999', 'MLB1934')]],
            ['conflicting category', [seed(), seed('MLB999', 'MLB1934')]],
            ['external item URL', [{ ...seed(), url: 'https://example.com/MLB-999-fixture' }]],
            ['different item wid', [{ ...seed(), url: itemUrl('MLB1000') }]],
            ['uncoupled item', [{ id: 'MLB999', categoryId: expected.key }, { id: 'MLB1000', categoryId: 'MLB1934', url: itemUrl('MLB1000') }]]
        ]) await fixture(browser, { rows, check: async ({ run, terms, navigations }) => {
            assert.equal(await run(), null, description);
            assert.equal(terms.length, 2);
            assert.equal(navigations.filter(url => url.includes('/up/')).length, 0, 'unproved seeds are never opened');
        } });
        for (const [description, options] of [
            ['different opened item', { wrongItem: true }],
            ['stale discovery document', { staleUrl: true }],
            ['external canonical', { canonical: 'https://example.com/up/MLBU123?wid=MLB999' }],
            ['different canonical item', { canonical: itemUrl('MLB1000') }],
            ['external breadcrumb', { breadcrumbs: breadcrumbs('https://example.com/elastomeros/') }],
            ['query breadcrumb', { breadcrumbs: breadcrumbs(LEAF + '?as_word=elastomeros') }],
            ['reordered ancestry', { breadcrumbs: '<nav class="andes-breadcrumb"><a href="' + BASE + '">Peças</a><a href="https://lista.mercadolivre.com.br/esportes-fitness/ciclismo/">Ciclismo</a><a href="' + LEAF + '">Elastômeros</a></nav>' }],
            ['wrong visible ancestor', { breadcrumbs: breadcrumbs().replace('>Peças<', '>Roupas<') }],
            ['wrong root URL', { breadcrumbs: breadcrumbs().replace('/c/esportes-e-fitness', '/c/roupas') }],
            ['wrong root label', { breadcrumbs: breadcrumbs().replace('>Esportes e Fitness<', '>Roupas<') }],
            ['root outside first position', { breadcrumbs: breadcrumbs().replace('<a href="' + BASE + '">Peças</a>', '<a href="https://www.mercadolivre.com.br/c/esportes-e-fitness">Esportes e Fitness</a>') }],
            ['ambiguous breadcrumb', { breadcrumbs: breadcrumbs() + breadcrumbs(BASE + 'outros-elastomeros/') }],
            ['source changed after DOM read', { swapDuringBreadcrumb: true }]
        ]) await fixture(browser, { ...options, check: async ({ run, terms }) => {
            assert.equal(await run(), null, description);
            assert.equal(terms.length, 1, 'a failed breadcrumb does not broaden or repeat the discovery search');
        } });
        await fixture(browser, { rows: [seed('MLB1'), seed('MLB2'), seed('MLB3'), seed('MLB4')], check: async ({ run, navigations }) => {
            assert.deepEqual(await run(), { ...expected, url: LEAF });
            assert.equal(navigations.filter(url => url.includes('/up/')).length, 3, 'at most three exact item seeds are opened');
        } });
        for (const options of [{ formAction: 'https://example.com/search' }, { extraInput: '<input name="seller_id" value="123">' }, { extraInput: '<input type="radio" name="unexpected" value="scope" checked>' }]) {
            await fixture(browser, { ...options, check: async ({ run, terms }) => { assert.equal(await run(), null); assert.equal(terms.length, 0); } });
        }
        await fixture(browser, { login: true, check: async ({ run }) => { await assert.rejects(run(), error => error.waitingLogin === true); } });
        for (const status of [403, 429]) await fixture(browser, { status, check: async ({ run }) => {
            await assert.rejects(run(), error => error.code === 'category_access_denied' && error.categoryUnavailable === true);
        } });
        const before = new AbortController(); before.abort();
        await fixture(browser, { check: async ({ run, navigations }) => {
            await assert.rejects(run(before.signal), error => error.name === 'AbortError'); assert.equal(navigations.length, 0);
        } });
        const during = new AbortController();
        await fixture(browser, { abortOnWait: () => during.abort(), check: async ({ run }) => { await assert.rejects(run(during.signal), error => error.name === 'AbortError'); } });
        await fixture(browser, { rows: [], check: async ({ run, terms, navigations }) => {
            const original = global.setTimeout;
            global.setTimeout = (callback, delay, ...args) => original(callback, delay === 60000 ? 0 : delay, ...args);
            try { assert.equal(await run(), null, 'the discovery deadline remains inconclusive rather than creating a route'); }
            finally { global.setTimeout = original; }
            assert.equal(terms.length, 0);
            assert(navigations.length <= 1, 'timeout cannot start another search');
        } });
        console.log('Category discovery: observed form, hydration, exact item membership, bounded seeds, public breadcrumbs, ambiguity, source identity, login/access and abort OK');
    } finally { await browser.close(); }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
