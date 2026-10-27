'use strict';

const assert = require('node:assert/strict');
const { PesquisaMercadoRunner, validateCheckpointTaxonomy } = require('../electron_app/main/modules/pesquisa-mercado-runner');
const { OfficialCategoryNavigation } = require('../electron_app/main/modules/pesquisa-mercado-category-navigation');
const { normalizeCategory } = require('../electron_app/main/modules/pesquisa-mercado-runner');
const { scopeCategoryItems, categoryItemScopeUrlAllowed } = require('../electron_app/main/modules/pesquisa-mercado-category-item-scope');

assert.equal(typeof categoryItemScopeUrlAllowed, 'function', 'the URL guard is exported before runner navigation');
const copy = value => JSON.parse(JSON.stringify(value));
const cycling = 'https://lista.mercadolivre.com.br/esportes-fitness/ciclismo/';
const parts = cycling + 'pecas/';
const leafUrl = parts + 'coroas/';
const leaf = Object.freeze({ key: 'MLB122156', nome: 'Coroas', url: 'https://lista.mercadolivre.com.br/_CatId_MLB122156' });
const parent = Object.freeze({ key: 'MLB1934', nome: 'Peças', url: 'https://lista.mercadolivre.com.br/_CatId_MLB1934' });
const ancestry = Object.freeze([
    Object.freeze({ key: 'MLB1276', nome: 'Esportes e Fitness', url: 'https://lista.mercadolivre.com.br/_CatId_MLB1276' }),
    Object.freeze({ key: 'MLB1292', nome: 'Ciclismo', url: 'https://lista.mercadolivre.com.br/_CatId_MLB1292' }), parent, leaf
]);
const observedAncestry = ancestry.map(node => ({ ...node, url: node.key === parent.key ? parts : node.key === leaf.key ? leafUrl : node.url }));
const item = id => ({ id, url: 'https://produto.mercadolivre.com.br/MLB-' + id.slice(3) + '-fixture-_JM',
    _avantEvidence: { present: true, metrics: { vendas: 42 } } });
const pair = (id, categoryId = leaf.key) => ({ id, categoryId });
const raw = (url, ids, nextUrl = '', metadata = ids.map(id => pair(id))) => ({ url, readyState: 'complete',
    categoryDocumentIdentity: true, effectiveCategoryId: parent.key, categoryIdentityConflict: false,
    categoryContext: { key: parent.key, nome: parent.nome, baseUrl: parts }, categoryItems: metadata,
    anuncios: ids.map(item), hasNext: !!nextUrl, nextUrl, empty: false });
const page2 = parts + '*_Desde_49_NoIndex_True';
const page3 = parts + '*_Desde_97_NoIndex_True';
const sellerUrl = 'https://www.mercadolivre.com.br/perfil/public-fixture';
const defaultPages = () => ({
    [cycling]: { ...raw(cycling, ['MLB100009'], '', [pair('MLB100009', 'MLB1292')]), effectiveCategoryId: 'MLB1292',
        categoryContext: { key: 'MLB1292', nome: 'Ciclismo', baseUrl: cycling } },
    [leafUrl]: raw(leafUrl, ['MLB100001'], '', [pair('MLB100001', parent.key)]),
    [parts]: raw(parts, ['MLB100001', 'MLB100002'], page2, [pair('MLB100001'), pair('MLB100002', parent.key)]),
    [page2]: raw(page2, ['MLB100003'], page3, [pair('MLB100003', parent.key)]),
    [page3]: raw(page3, ['MLB100004', 'MLB100005'], '', [pair('MLB100004'), pair('MLB100005', 'MLB999')])
});

function harness(pages = defaultPages(), checkpoint = {}) {
    const visits = [], snapshots = [], processed = [];
    let current = sellerUrl, runner;
    let saved = { id: 'isolated-scope', status: 'paused', revision: 0,
        counters: { categories: 0, pages: 0, sellers: 0, ads: 0, pending: 0 }, checkpoint: {
            scope: { category: leaf }, categories: [leaf], currentCategory: leaf, categoryIndex: 0,
            discoveryInitialized: true, officialTaxonomy: { updatedAt: '2026-10-23T00:00:00Z', ancestry,
                children: { [leaf.key]: [] } }, ...checkpoint
        } };
    const browser = {
        async prepare() {
            runner.officialNavigation = new OfficialCategoryNavigation({ ancestry: runner.checkpoint.officialTaxonomy.ancestry,
                children: runner.checkpoint.officialTaxonomy.children, normalize: normalizeCategory,
                read: () => { throw new Error('The frozen fixture route map already contains the observed routes'); } });
            for (const [key, url] of [['MLB1292', cycling], [parent.key, parts], [leaf.key, leafUrl]]) runner.officialNavigation.routes.set(key, url);
        },
        async navigate(url) { visits.push(url); current = url; }, getURL: () => current, isLoading: () => false,
        getNavigationStatus: () => ({ url: current, statusCode: 200 }),
        async executeJavaScript(code) {
            if (code.startsWith('next:')) {
                assert.equal(code.slice(5), current, 'restore the exact scoped document before clicking its JavaScript next button');
                const target = pages[current] && pages[current].clickNext;
                if (!target) return { clicked: false, hasNext: false, reason: 'no_next_page' };
                visits.push(target); current = target;
                return { clicked: true, hasNext: true, reason: 'next_clicked' };
            }
            assert.equal(code, 'discovery', 'the test never executes UI navigation or collects credentials');
            assert(pages[current], 'all document reads must use a predefined public fixture');
            return copy(pages[current]);
        }, async finish() {}
    };
    runner = new PesquisaMercadoRunner({ browser, pageScripts: { discoveryScript: () => 'discovery', nextCategoryPageScript: url => 'next:' + url }, collector: {}, wait: async () => {},
        api: async (method, path, body) => {
            assert.equal(method, 'PATCH', 'the test only persists checkpoints in memory');
            assert.equal(path, '/api/pesquisa-mercado/jobs/isolated-scope/checkpoint');
            snapshots.push(copy(body));
            saved = { ...saved, ...copy(body), revision: saved.revision + 1 };
            return { job: copy(saved) };
        } });
    runner.processSeed = async seed => {
        processed.push(seed.id);
        assert(['MLB100001', 'MLB100004'].includes(seed.id), 'no item from the transport parent or sibling can reach seller discovery');
        await browser.navigate(sellerUrl);
    };
    const initDirect = () => {
        runner.job = copy(saved); runner.controller = new AbortController(); runner.checkpoint = copy(saved.checkpoint);
    };
    return { runner, browser, visits, snapshots, processed, saved: () => copy(saved), initDirect, initial: () => copy(saved) };
}

async function nextPageRestoresScope() {
    const h = harness(); h.initDirect();
    const first = scopeCategoryItems(defaultPages()[parts], { categoryKey: leaf.key, documentCategoryId: parent.key, categoryBaseUrl: leafUrl }, observedAncestry);
    assert(first);
    await h.browser.navigate(sellerUrl);
    const second = await h.runner.nextCategoryPage(first);
    assert.deepEqual(second.anuncios, [], 'an unmatched intermediate ancestor page remains traversable');
    assert.equal(second.hasNext, true);
    assert.equal(second.empty, false);
    assert.deepEqual(second.categoryItemScope, first.categoryItemScope);
    await h.browser.navigate(sellerUrl);
    const third = await h.runner.nextCategoryPage(second);
    assert.deepEqual(third.anuncios.map(ad => ad.id), ['MLB100004']);
    assert.equal(third.effectiveCategoryId, parent.key, 'the real ancestor identity survives filtering');
    assert.equal(await h.runner.nextCategoryPage(third), null);
    assert.deepEqual(h.visits, [sellerUrl, page2, sellerUrl, page3], 'seller collection cannot change the resumed category transport');

    const wrongPages = defaultPages(); wrongPages[page2].effectiveCategoryId = 'MLB1292';
    const changed = harness(wrongPages); changed.initDirect();
    await assert.rejects(changed.runner.nextCategoryPage(first), error => ['category_scope_mismatch', 'category_load_timeout'].includes(error.code));
    assert.deepEqual(changed.processed, []);
    for (const nextUrl of [parts + 'coroas_NoIndex_True', parts + '*_Desde_49_NoIndex_True?q=coroas', cycling + '*_Desde_49_NoIndex_True']) {
        const invalid = harness(); invalid.initDirect();
        await assert.rejects(invalid.runner.nextCategoryPage({ ...first, nextUrl }), error => error.code === 'category_scope_mismatch');
        assert.deepEqual(invalid.visits, [], 'a changed scope or keyword URL is rejected before browser navigation');
    }
}

function assertScopedSnapshots(h) {
    const pages = h.snapshots.map(snapshot => snapshot.checkpoint.page).filter(page => page && page.categoryItemScope);
    assert(pages.some(page => page.url === parts), 'the initial filtered discovery page persists its descriptor');
    assert(pages.some(page => page.url === page2 && !page.anuncios.length && page.hasNext), 'zero-match pagination persists the descriptor and cursor');
    assert(pages.some(page => page.url === page3 && page.anuncios.some(ad => ad.id === 'MLB100004')), 'the next matched page persists the descriptor');
    for (const page of pages) {
        assert.deepEqual(page.categoryItemScope, pages[0].categoryItemScope);
        assert(page.anuncios.every(ad => ['MLB100001', 'MLB100004'].includes(ad.id)), 'checkpoint seeds remain exact leaf items');
        const body = h.snapshots.find(snapshot => snapshot.checkpoint.page && snapshot.checkpoint.page.url === page.url);
        assert.doesNotThrow(() => validateCheckpointTaxonomy(body.checkpoint), 'a serialized checkpoint can resume against the frozen tree');
    }
    assert.deepEqual(h.processed, ['MLB100001', 'MLB100004']);
    assert.equal(h.saved().status, 'completed');
    assert.equal(h.saved().checkpoint.incompleteCategories.length, 0);
}

async function persistEveryPageAndReopenedAlias() {
    const fresh = harness(); fresh.runner.attach(fresh.initial()); await fresh.runner.task; assertScopedSnapshots(fresh);
    const restored = harness(defaultPages(), { page: { categoryKey: leaf.key, url: leaf.url, nextUrl: '', hasNext: false,
        anuncios: [item('MLB100001')], index: 1 }, categorySeenPages: [leaf.url] });
    restored.runner.attach(restored.initial()); await restored.runner.task; assertScopedSnapshots(restored);
    assert(!restored.visits.includes(leaf.url), 'the preserved alias cursor is replaced using an observed official route');
}

async function javascriptNextPreservesItemScope() {
    const pages = defaultPages();
    for (const [url, target] of [[parts, page2], [page2, page3]]) {
        pages[url].nextUrl = ''; pages[url].hasNext = true; pages[url].clickNext = target;
    }
    const h = harness(pages); h.initDirect();
    const first = scopeCategoryItems(pages[parts], { categoryKey: leaf.key, documentCategoryId: parent.key, categoryBaseUrl: leafUrl }, observedAncestry);
    assert.equal(first.nextUrl, '');
    assert.equal(first.hasNext, true);
    await h.browser.navigate(sellerUrl);
    const second = await h.runner.nextCategoryPage(first);
    assert.deepEqual(second.anuncios, [], 'JavaScript pagination still crosses a page with no exact leaf items');
    assert.equal(second.hasNext, true);
    assert.equal(second.nextUrl, '');
    assert.equal(second.empty, false);
    assert.deepEqual(second.categoryItemScope, first.categoryItemScope);
    await h.browser.navigate(sellerUrl);
    const third = await h.runner.nextCategoryPage(second);
    assert.deepEqual(third.anuncios.map(ad => ad.id), ['MLB100004']);
    assert.deepEqual(third.categoryItemScope, first.categoryItemScope);
    assert.equal(third.effectiveCategoryId, parent.key);
    assert.equal(await h.runner.nextCategoryPage(third), null);
    assert.deepEqual(h.visits, [sellerUrl, parts, page2, sellerUrl, page2, page3],
        'each href-less next button is clicked only after restoring its own ancestor document');

    const persisted = harness(pages);
    persisted.runner.attach(persisted.initial()); await persisted.runner.task;
    assertScopedSnapshots(persisted);
    for (const change of [
        { effectiveCategoryId: 'MLB1292' },
        { categoryDocumentIdentity: false },
        { url: parts + 'coroas_NoIndex_True' }
    ]) {
        const unsafePages = copy(pages); Object.assign(unsafePages[page2], change);
        const unsafe = harness(unsafePages); unsafe.initDirect();
        await assert.rejects(unsafe.runner.nextCategoryPage(first), 'a JavaScript transition cannot drop ancestor proof or switch into a keyword search');
        assert.deepEqual(unsafe.processed, []);
        assert.deepEqual(unsafe.snapshots, []);
    }
}

async function boundedOpeningNeedsAnExactItem() {
    const pages = defaultPages();
    pages[parts] = raw(parts, ['MLB100002'], page2, [pair('MLB100002', parent.key)]);
    pages[page2] = raw(page2, ['MLB100001'], page3, [pair('MLB100001')]);
    const laterMatch = harness(pages); laterMatch.initDirect(); await laterMatch.browser.prepare();
    const accepted = await laterMatch.runner.tryCategoryItemScope(leaf, pages[leafUrl], leafUrl);
    assert(accepted, 'a rare exact category can be proved after the first unmatched ancestor page');
    assert.equal(accepted.url, page2);
    assert.deepEqual(accepted.anuncios.map(ad => ad.id), ['MLB100001']);
    assert.deepEqual(laterMatch.visits, [parts, cycling, page2], 'each known ancestor gets one neutral page before further pagination');
    assert.deepEqual(laterMatch.snapshots, [], 'unmatched opening probes do not become completed research pages');
    const buttonPages = copy(pages);
    buttonPages[parts].nextUrl = ''; buttonPages[parts].hasNext = true; buttonPages[parts].clickNext = page2;
    const buttonMatch = harness(buttonPages); buttonMatch.initDirect(); await buttonMatch.browser.prepare();
    const buttonAccepted = await buttonMatch.runner.tryCategoryItemScope(leaf, buttonPages[leafUrl], leafUrl);
    assert(buttonAccepted, 'the first exact item can also be reached through an ancestor button without an href');
    assert.equal(buttonAccepted.url, page2);
    assert.deepEqual(buttonAccepted.anuncios.map(ad => ad.id), ['MLB100001']);
    assert.deepEqual(buttonMatch.visits, [parts, cycling, parts, page2], 'opening probes restore the ancestor before using its own JavaScript control');
    assert.deepEqual(buttonMatch.snapshots, []);

    const missing = { [leafUrl]: pages[leafUrl], [cycling]: pages[cycling] };
    const transportUrls = [parts, ...Array.from({ length: 11 }, (_, index) => parts + '*_Desde_' + (49 + index * 48) + '_NoIndex_True')];
    for (let index = 0; index < transportUrls.length; index += 1) {
        const url = transportUrls[index];
        missing[url] = raw(url, ['MLB100002'], transportUrls[index + 1] || '', [pair('MLB100002', parent.key)]);
    }
    const bounded = harness(missing); bounded.initDirect(); await bounded.browser.prepare();
    const budget = { remaining: 10 };
    assert.equal(await bounded.runner.tryCategoryItemScope(leaf, missing[leafUrl], leafUrl, { budget }), null, 'absence within the bounded probe remains inconclusive');
    assert.equal(bounded.visits.length, 10, 'the ten-page limit is shared by all ancestors, never reset for each one');
    assert.equal(bounded.visits.filter(url => url.startsWith(parts)).length, 9,
        'one reserved page for another ancestor reduces the remaining pagination budget');
    assert.equal(bounded.visits.filter(url => url === cycling).length, 1, 'an exhausted closer ancestor does not hide another known ancestor');
    assert.equal(budget.remaining, 0);
    assert.equal(await bounded.runner.tryCategoryItemScope(leaf, missing[leafUrl], leafUrl, { budget }), null);
    assert.equal(bounded.visits.length, 10, 'a second distinct route cannot reset the category-wide ancestor budget');
    assert.deepEqual(bounded.snapshots, []);
    const pending = harness(missing);
    pending.runner.discoverOfficialCategoryRoute = async () => null;
    pending.runner.attach(pending.initial()); await pending.runner.task;
    assert.equal(pending.saved().status, 'failed', 'no exact item proof leaves the category pending rather than complete');
    assert.equal(pending.saved().counters.pages, 0);
    assert.equal(pending.saved().checkpoint.incompleteCategories.length, 1);
    assert.equal(pending.snapshots.some(snapshot => snapshot.checkpoint.page), false, 'ancestor-only results never enter a leaf checkpoint');
}

function resumedCheckpointCannotChangeTransport() {
    const first = scopeCategoryItems(defaultPages()[parts], { categoryKey: leaf.key, documentCategoryId: parent.key, categoryBaseUrl: leafUrl }, observedAncestry);
    const h = harness();
    const initial = h.initial();
    initial.checkpoint.page = { categoryKey: leaf.key, url: first.url, nextUrl: first.nextUrl, hasNext: true,
        anuncios: first.anuncios, index: 0, categoryItemScope: first.categoryItemScope };
    assert.doesNotThrow(() => validateCheckpointTaxonomy(initial.checkpoint));
    for (const change of [
        { url: parts + 'coroas_NoIndex_True' },
        { url: cycling + '*_NoIndex_True' },
        { nextUrl: parts + '*_Desde_49_NoIndex_True?q=coroas' },
        { nextUrl: leafUrl + '*_Desde_49_NoIndex_True' }
    ]) {
        const changed = copy(initial); Object.assign(changed.checkpoint.page, change);
        assert.throws(() => validateCheckpointTaxonomy(changed.checkpoint), /arvore preservada/i,
            'a serialized URL mismatch is rejected before any saved seed is consumed');
        const rejected = harness();
        assert.throws(() => rejected.runner.attach(changed), /arvore preservada/i);
        assert.deepEqual(rejected.visits, []);
        assert.deepEqual(rejected.processed, []);
        assert.deepEqual(rejected.snapshots, []);
    }
}

async function javascriptNextStopsOnAccessDenied() {
    for (const statusCode of [403, 429]) {
        const pages = defaultPages();
        pages[parts].nextUrl = ''; pages[parts].hasNext = true; pages[parts].clickNext = page2;
        pages[page2] = { url: page2, readyState: 'complete', anuncios: [], categoryDocumentIdentity: false };
        const h = harness(pages); h.initDirect();
        let blockedStatusReads = 0;
        h.browser.getNavigationStatus = () => {
            const blocked = h.browser.getURL() === page2;
            if (blocked) blockedStatusReads += 1;
            return { url: h.browser.getURL(), statusCode: blocked ? statusCode : 200 };
        };
        const first = scopeCategoryItems(pages[parts], { categoryKey: leaf.key, documentCategoryId: parent.key, categoryBaseUrl: leafUrl }, observedAncestry);
        await assert.rejects(h.runner.nextCategoryPage(first), error => error.code === 'category_access_denied' && error.sharedCategoryFailure === true);
        assert.equal(h.visits.filter(url => url === page2).length, 1, 'a blocked next document is not clicked or retried repeatedly');
        assert.equal(blockedStatusReads, 1, 'access status is checked before waiting for document identity on a blocked page');
        assert.deepEqual(h.snapshots, []);
    }
}

async function hrefPaginationRejectsKeywordRedirect() {
    const nextUrl = leafUrl + '_Desde_49_NoIndex_True';
    const keyword = leafUrl + 'keyword_NoIndex_True';
    const h = harness({ [nextUrl]: { url: keyword, readyState: 'complete', categoryDocumentIdentity: true,
        effectiveCategoryId: leaf.key, categoryContext: { key: leaf.key, baseUrl: leafUrl }, anuncios: [item('MLB100001')] } });
    h.initDirect();
    await assert.rejects(h.runner.nextCategoryPage({ url: leafUrl, hasNext: true, nextUrl }), error => error.code === 'category_scope_mismatch');
    assert.deepEqual(h.processed, []);
    assert.deepEqual(h.snapshots, []);
}

async function openingCanUseAnotherConfirmedAncestor() {
    const pages = defaultPages();
    pages[parts] = raw(parts, ['MLB100002'], '', [pair('MLB100002', parent.key)]);
    pages[cycling] = { ...raw(cycling, ['MLB100001', 'MLB100009'], '', [pair('MLB100001'), pair('MLB100009', 'MLB1292')]),
        effectiveCategoryId: 'MLB1292', categoryContext: { key: 'MLB1292', nome: 'Ciclismo', baseUrl: cycling } };
    const h = harness(pages); h.initDirect(); await h.browser.prepare();
    const scoped = await h.runner.tryCategoryItemScope(leaf, pages[leafUrl], leafUrl);
    assert(scoped, 'a closer ancestor without an exact item cannot prevent probing another confirmed ancestor');
    assert.deepEqual(h.visits, [parts, cycling]);
    assert.equal(scoped.categoryItemScope.transportCategoryId, 'MLB1292');
    assert.equal(scoped.effectiveCategoryId, 'MLB1292', 'the source keeps its actual ancestor identity');
    assert.deepEqual(scoped.anuncios.map(ad => ad.id), ['MLB100001']);
    assert.deepEqual(h.snapshots, []);

    const absentPage = harness(); absentPage.initDirect(); await absentPage.browser.prepare();
    const recovered = await absentPage.runner.tryCategoryItemScope(leaf, undefined, leafUrl);
    assert(recovered, 'a typed navigation timeout without HTML can still probe a frozen ancestor route');
    assert.deepEqual(absentPage.visits, [parts]);
    const quickOnly = harness(); quickOnly.initDirect(); await quickOnly.browser.prepare();
    assert.equal(await quickOnly.runner.tryCategoryItemScope(leaf, pages[leafUrl], leafUrl, { maxPages: 0 }), null);
    assert.deepEqual(quickOnly.visits, [], 'a quick reuse check must not start ancestor navigation');
    for (const flags of [{ authRequired: true }, { avantAuthRequired: true }, { categoryIdentityConflict: true },
        { categoryDocumentIdentity: true, effectiveCategoryId: 'MLB999' }]) {
        const blocked = harness(); blocked.initDirect(); await blocked.browser.prepare();
        assert.equal(await blocked.runner.tryCategoryItemScope(leaf, { ...pages[leafUrl], ...flags }, leafUrl), null);
        assert.deepEqual(blocked.visits, [], 'auth or an unrelated own identity cannot authorize ancestor probes');
    }
}

async function openingReusesOnlyTheCurrentProvenTransport() {
    const h = harness(); h.initDirect(); await h.browser.prepare(); await h.browser.navigate(parts);
    const observed = defaultPages()[parts];
    const scoped = await h.runner.tryCategoryItemScope(leaf, observed, leafUrl);
    assert(scoped);
    assert.deepEqual(h.visits, [parts], 'an already observed neutral ancestor with exact items needs no reload');
    assert.equal(scoped.effectiveCategoryId, parent.key);
    for (const patch of [{ readyState: 'loading' }, { url: leafUrl }]) {
        const fresh = harness(); fresh.initDirect(); await fresh.browser.prepare(); await fresh.browser.navigate(parts);
        const stale = { ...observed, ...patch, anuncios: [item('MLB900001')], categoryItems: [pair('MLB900001')] };
        const result = await fresh.runner.tryCategoryItemScope(leaf, stale, leafUrl);
        assert(result);
        assert.deepEqual(result.anuncios.map(ad => ad.id), ['MLB100001'], 'loading or non-neutral snapshots cannot replace a fresh confirmed ancestor read');
        assert.deepEqual(fresh.visits, [parts, parts]);
    }
}

async function recoveryKeepsThePrimaryFailurePage() {
    for (const code of ['category_scope_mismatch', 'category_load_timeout', 'category_not_found']) {
        const h = harness(); h.initDirect(); await h.browser.prepare();
        const primaryPage = defaultPages()[leafUrl];
        const primary = Object.assign(new Error('Primary destination mismatch'), { code: 'category_scope_mismatch', categoryScopeMismatch: true, categoryPage: primaryPage });
        h.runner.read = async () => { throw primary; };
        h.runner.tryCategoryItemScope = async () => { throw Object.assign(new Error('Secondary probe failed'), { code }); };
        h.runner.discoverOfficialCategoryRoute = async () => null;
        await assert.rejects(h.runner.readCategory(leaf), error => error === primary && error.categoryPage === primaryPage && error.categoryFallbackCode === code,
            'an inconclusive secondary probe cannot discard the original destination evidence');
    }
    for (const secondary of [Object.assign(new Error('Manual login'), { waitingLogin: true }),
        Object.assign(new Error('Blocked'), { code: 'category_access_denied' }),
        Object.assign(new Error('Cancelled'), { name: 'AbortError' }), new Error('Unexpected error')]) {
        const h = harness(); h.initDirect(); await h.browser.prepare();
        const primary = Object.assign(new Error('Primary timeout'), { code: 'category_load_timeout' });
        h.runner.read = async () => { throw primary; };
        h.runner.tryCategoryItemScope = async () => { throw secondary; };
        h.runner.discoverOfficialCategoryRoute = async () => { assert.fail('fatal recovery errors must stop discovery'); };
        await assert.rejects(h.runner.readCategory(leaf), error => error === secondary);
    }
}

(async () => {
    await nextPageRestoresScope();
    await persistEveryPageAndReopenedAlias();
    await javascriptNextPreservesItemScope();
    await boundedOpeningNeedsAnExactItem();
    await javascriptNextStopsOnAccessDenied();
    await hrefPaginationRejectsKeywordRedirect();
    await openingCanUseAnotherConfirmedAncestor();
    await openingReusesOnlyTheCurrentProvenTransport();
    await recoveryKeepsThePrimaryFailurePage();
    resumedCheckpointCannotChangeTransport();
    console.log('Pesquisa de Mercado scoped navigation: seller restoration, unmatched pagination, safe URLs and all checkpoint paths OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
