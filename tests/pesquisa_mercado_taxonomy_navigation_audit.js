'use strict';
const assert = require('assert/strict');
const { createHash } = require('crypto');
const { snapshotFromCache, auditSnapshot } = require('../scripts/audit_pesquisa_mercado_taxonomy_navigation');

const cache = { version: 1, updatedAt: '2026-10-23T12:00:00Z', fetchedAt: '2026-10-23T12:00:01Z', records: [
    ['MLB100', 'Root', 'https://www.mercadolivre.com.br/c/root', ['MLB101', 'MLB102'], ['MLB100']],
    ['MLB101', 'Same name', 'https://lista.mercadolivre.com.br/_CatId_MLB101', [], ['MLB100', 'MLB101']],
    ['MLB102', 'Same name', 'https://lista.mercadolivre.com.br/_CatId_MLB102', [], ['MLB100', 'MLB102']]
] };
const snapshot = snapshotFromCache(cache);
const snapshotSha256 = createHash('sha256').update(JSON.stringify(cache)).digest('hex');
const audit = auditSnapshot(snapshot, { snapshotSha256 });
assert.equal(audit.structural.passed, true);
assert.equal(audit.structural.nodeChecks, 3);
assert.equal(audit.structural.pathChecks, 3);
assert.equal(audit.structural.edgeChecks, 2);
assert.equal(audit.structural.negativeRouteChecks, 12);
assert.equal(audit.cachedRoutes.unresolvedAliases, 2);
assert.equal(audit.cachedRoutes.officialPermalinks, 1);
assert.equal(audit.cachedRoutes.categoryDocumentsOpened, 0);
assert.equal(audit.live.verifiedNodes, 0, 'a valid taxonomy is not a successful live navigation');
assert.equal(audit.live.untestedNodes, 3);
assert.deepEqual(audit.live.untestedCategoryKeys, ['MLB100', 'MLB101', 'MLB102']);

const live = (categoryKey, extra = {}) => ({ categoryKey, source: 'live_document', observedAt: '2026-10-23T13:00:00Z',
    url: 'https://lista.mercadolivre.com.br/observed-official-route/', documentCategoryId: categoryKey,
    statusCode: 200, listingCount: 1, empty: false, categoryNotFound: false, identityConflict: false, ...extra });
const evidence = { version: 1, snapshotSha256, observations: [live('MLB100'), live('MLB101', { listingCount: 0, empty: true })] };
const covered = auditSnapshot(snapshot, { snapshotSha256, evidence });
assert.equal(covered.live.verifiedNodes, 2);
assert.equal(covered.live.verifiedEmpty, 1);
assert.equal(covered.live.verifiedNonempty, 1);
assert.deepEqual(covered.live.untestedCategoryKeys, ['MLB102']);

for (const [extra, outcome] of [
    [{ statusCode: 403 }, 'access_denied'],
    [{ statusCode: 429 }, 'access_denied'],
    [{ categoryNotFound: true }, 'category_not_found'],
    [{ documentCategoryId: 'MLB102' }, 'scope_mismatch'],
    [{ identityConflict: true }, 'scope_mismatch'],
    [{ url: 'https://evil.invalid/category/' }, 'unconfirmed_route'],
    [{ url: 'https://lista.mercadolivre.com.br/_CatId_MLB101' }, 'unconfirmed_route'],
    [{ documentCategoryId: '', listingCount: 100 }, 'unconfirmed_document'],
    [{ listingCount: 0, empty: false }, 'unconfirmed_document']
]) {
    const report = auditSnapshot(snapshot, { snapshotSha256,
        evidence: { version: 1, snapshotSha256, observations: [live('MLB101', extra)] } });
    assert.equal(report.live.verifiedNodes, 0);
    assert.equal(report.live.failedNodes, 1);
    assert.equal(report.live.outcomes[0].outcome, outcome);
}
assert.throws(() => auditSnapshot(snapshot, { snapshotSha256, evidence: { ...evidence, snapshotSha256: '0'.repeat(64) } }));
assert.throws(() => auditSnapshot(snapshot, { snapshotSha256,
    evidence: { version: 1, snapshotSha256, observations: [live('MLB101', { source: 'fixture' })] } }));
assert.throws(() => auditSnapshot(snapshot, { snapshotSha256,
    evidence: { version: 1, snapshotSha256, observations: [live('MLB999')] } }));
assert.throws(() => snapshotFromCache({ ...cache, records: [...cache.records, cache.records[0]] }));
assert.throws(() => snapshotFromCache({ ...cache, records: cache.records.slice(0, 2) }));
const immutable = JSON.stringify(cache);
snapshotFromCache(cache);
assert.equal(JSON.stringify(cache), immutable, 'the audit cannot rewrite its input cache');
console.log('Pesquisa de Mercado taxonomy audit: exhaustive structure, unresolved routes and strict live evidence coverage OK');

async function testResumableProductionNavigation() {
    const { LiveNavigationAudit, breadthFirstKeys, auditSelection, safePublicUrl } = require('../scripts/pesquisa_mercado_live_navigation_audit');
    const base = 'https://www.mercadolivre.com.br';
    const rootUrl = snapshot.get('MLB100').url;
    const indexUrl = base + '/categorias';
    const a = { ...snapshot.get('MLB101'), url: 'https://lista.mercadolivre.com.br/observed-child-a/' };
    const b = { ...snapshot.get('MLB102'), url: 'https://lista.mercadolivre.com.br/observed-child-b/' };
    const fixture = denied => {
        let url = '', calls = 0;
        const visits = [];
        const pages = {
            [indexUrl]: { categoryTree: [{ ...snapshot.get('MLB100'), children: [a, b] }] },
            [a.url]: { effectiveCategoryId: a.key, categoryDocumentIdentity: true, anuncios: [{}] },
            [b.url]: { effectiveCategoryId: b.key, categoryDocumentIdentity: true, anuncios: [{}] }
        };
        return { browser: { async navigate(next) { assert.notEqual(next, rootUrl, 'marketing root is not a listing destination'); calls += 1; visits.push(next); url = next; }, getURL: () => url,
            getNavigationStatus: () => ({ url, statusCode: denied && url === indexUrl ? 403 : 200 }),
            async executeJavaScript() { return { url, readyState: 'complete', ...pages[url] }; } }, calls: () => calls, visits };
    };
    assert.deepEqual(breadthFirstKeys(snapshot), ['MLB100', 'MLB101', 'MLB102']);
    const first = fixture(false);
    let saved;
    const scanner = new LiveNavigationAudit({ snapshot, snapshotSha256, browser: first.browser, wait: async () => {}, intervalMs: 0,
        persist: async state => { saved = state; } });
    assert.equal(scanner.runner.officialNavigation, scanner.navigation, 'production reads share the audited official-route cache');
    const productionReads = [];
    const productionReadCategory = scanner.runner.readCategory.bind(scanner.runner);
    scanner.runner.readCategory = async node => { productionReads.push(node.key); return productionReadCategory(node); };
    const partial = await scanner.run({ maxNodes: 2 });
    assert.deepEqual(productionReads, ['MLB101'], 'leaf auditing calls the production readCategory workflow');
    assert.equal(scanner.runner.checkpoint.currentCategory.key, 'MLB101', 'ancestor callbacks preserve the requested leaf');
    assert.equal(scanner.runner.checkpoint.scope.category.key, 'MLB101');
    assert.deepEqual(scanner.runner.checkpoint.officialTaxonomy.ancestry.map(node => node.key), ['MLB100', 'MLB101']);
    assert.equal(partial.status, 'paused_limit');
    assert.equal(partial.completedCursor, 2);
    assert.equal(partial.verifiedDocumentNodes, 1, 'a real directory group is not counted as an opened category listing');
    assert.equal(partial.verifiedOnlineGroupNodes, 1);
    assert.equal(partial.functionalVerifiedNodes, 2);
    assert.equal(partial.physicalPageReads, 2, 'directory evidence validates the group without timing out on a marketing page');
    assert.equal(partial.physicalPageReads, first.calls(), 'navigation retries and direct reads count their physical navigations');
    const second = fixture(false);
    const resumed = new LiveNavigationAudit({ snapshot, snapshotSha256, browser: second.browser, state: saved, wait: async () => {}, intervalMs: 0 });
    const complete = await resumed.run();
    assert.equal(complete.status, 'traversal_completed');
    assert.equal(complete.verifiedDocumentNodes, 2);
    assert.equal(complete.verifiedOnlineGroupNodes, 1);
    assert.equal(complete.functionalVerifiedNodes, 3);
    assert.equal(complete.completeLiveCoverage, true);
    assert.equal(complete.completeDocumentCoverage, false, 'functional group validation cannot masquerade as full document coverage');
    assert.equal(second.calls(), 1, 'resume visits only the remaining document using the frozen official routes');
    assert.throws(() => new LiveNavigationAudit({ snapshot, snapshotSha256: '0'.repeat(64), browser: second.browser, state: saved }));
    assert.throws(() => new LiveNavigationAudit({ snapshot, snapshotSha256, implementationSha256: 'changed-code', browser: second.browser, state: saved }));
    const blocked = fixture(true);
    let blockedState;
    const denial = new LiveNavigationAudit({ snapshot, snapshotSha256, browser: blocked.browser, wait: async () => {}, intervalMs: 0,
        persist: async state => { blockedState = state; } });
    const paused = await denial.run();
    assert.equal(paused.status, 'paused_access_denied');
    assert.equal(paused.completedCursor, 0, 'denial preserves the current node for a later authenticated resume');
    assert.equal(paused.verifiedDocumentNodes, 0);
    assert.equal(paused.verifiedOnlineGroupNodes, 0);
    assert.equal(paused.completeLiveCoverage, false);
    assert.equal(blocked.calls(), 1, 'an HTTP block cannot launch a cascade through the remaining categories');
    const allowed = fixture(false);
    const afterBlock = new LiveNavigationAudit({ snapshot, snapshotSha256, browser: allowed.browser, state: blockedState, wait: async () => {}, intervalMs: 0 });
    assert.equal((await afterBlock.run()).functionalVerifiedNodes, 3);

    const prioritized = fixture(false);
    let priorityState;
    const priorityAudit = new LiveNavigationAudit({ snapshot, snapshotSha256, browser: prioritized.browser,
        priorityKeys: ['MLB102'], intervalMs: 0, wait: async () => {}, persist: async state => { priorityState = state; } });
    const prioritySummary = await priorityAudit.run({ maxNodes: 1 });
    assert.equal(priorityState.queue[0], 'MLB102');
    assert.equal(prioritySummary.verifiedLeafDocumentNodes, 1);
    assert.equal(prioritySummary.completedCursor, 1);
    const priorityResume = new LiveNavigationAudit({ snapshot, snapshotSha256, browser: fixture(false).browser,
        state: priorityState, intervalMs: 0, wait: async () => {} });
    assert.equal((await priorityResume.run()).functionalVerifiedNodes, 3, 'priority order survives a resume without duplicate nodes');

    let cancelRequested = false, cancelledState;
    const progress = [];
    const cancellable = new LiveNavigationAudit({ snapshot, snapshotSha256, browser: fixture(false).browser,
        intervalMs: 0, wait: async () => {}, shouldCancel: async () => cancelRequested,
        persist: async state => { cancelledState = state; }, onProgress: async event => {
            progress.push(event);
            if (event.stage === 'node_completed' && event.cursor === 1) cancelRequested = true;
        } });
    const stopped = await cancellable.run();
    assert.equal(stopped.status, 'paused_cancel_file');
    assert.equal(stopped.completedCursor, 1);
    assert(progress.some(event => event.stage === 'navigating'));
    assert(progress.some(event => event.stage === 'paused' && event.cancellationRequested));
    const cancellationResume = new LiveNavigationAudit({ snapshot, snapshotSha256, browser: fixture(false).browser,
        state: cancelledState, intervalMs: 0, wait: async () => {} });
    assert.equal((await cancellationResume.run()).functionalVerifiedNodes, 3);
    assert.equal(safePublicUrl('https://lista.mercadolivre.com.br/category/?access_token=private'), '');
    assert.equal(safePublicUrl('https://www.mercadolivre.com.br/login?token=private'), '');
    assert.equal(safePublicUrl(indexUrl), indexUrl);
    assert.equal(new LiveNavigationAudit({ snapshot, snapshotSha256, browser: fixture(false).browser }).summary().functionalVerifiedNodes, 0,
        'a complete frozen API tree alone cannot validate any online group or leaf');

    const namedCache = JSON.parse(JSON.stringify(cache));
    namedCache.records[1][1] = 'Alpha'; namedCache.records[2][1] = 'Beta';
    const namedSnapshot = snapshotFromCache(namedCache);
    const namedHash = createHash('sha256').update(JSON.stringify(namedCache)).digest('hex');
    let namedUrl = '', namedState;
    const nameOnlyLinks = [a, b].map((item, index) => ({ key: item.url, nome: index ? 'Beta' : 'Alpha', url: item.url }));
    const namedBrowser = { async navigate(url) { assert.notEqual(url, rootUrl); namedUrl = url; }, getURL: () => namedUrl,
        getNavigationStatus: () => ({ url: namedUrl, statusCode: 200 }), async executeJavaScript() {
            if (namedUrl === indexUrl) return { url: namedUrl, readyState: 'complete', categoryTree: [{ ...namedSnapshot.get('MLB100'), children: nameOnlyLinks }] };
            const id = namedUrl === a.url ? 'MLB101' : 'MLB102';
            return { url: namedUrl, readyState: 'complete', categoryDocumentIdentity: true, effectiveCategoryId: id, anuncios: [{}] };
        } };
    const nameOnly = new LiveNavigationAudit({ snapshot: namedSnapshot, snapshotSha256: namedHash, browser: namedBrowser,
        wait: async () => {}, intervalMs: 0, persist: async state => { namedState = state; } });
    const groupOnly = await nameOnly.run({ maxNodes: 1 });
    assert.equal(groupOnly.observedOfficialEdges, 2);
    assert.equal(groupOnly.verifiedOnlineGroupNodes, 0, 'name-only directory links need child document identity before a group can pass');
    assert.equal(groupOnly.functionalVerifiedNodes, 0);
    assert.deepEqual(nameOnly.functionalEvidence().groups[0].unconfirmedIdentityKeys, ['MLB101', 'MLB102']);
    const namesResumed = new LiveNavigationAudit({ snapshot: namedSnapshot, snapshotSha256: namedHash, browser: namedBrowser,
        state: namedState, wait: async () => {}, intervalMs: 0 });
    const provenNames = await namesResumed.run();
    assert.equal(provenNames.verifiedOnlineGroupNodes, 1, 'later live child identity completes the observed directory proof');
    assert.equal(provenNames.functionalVerifiedNodes, 3);
    assert.deepEqual(auditSelection(snapshot, { scopeKey: 'MLB101' }).keys, ['MLB101']);
    assert.deepEqual(auditSelection(snapshot, { scopeKey: 'MLB100' }).keys, ['MLB100', 'MLB101', 'MLB102']);
    assert.throws(() => auditSelection(snapshot, { scopeKey: 'MLB100', onlyKeys: ['MLB101'] }));
    assert.throws(() => new LiveNavigationAudit({ snapshot, snapshotSha256, browser: fixture(false).browser,
        onlyKeys: ['MLB101'], priorityKeys: ['MLB102'] }));

    const fallbackFixture = ({ keywordFinal = false, invalidReturnedQuery = false, itemFilter = false, blockDiscovery = false } = {}) => {
        let current = '', audit;
        const calls = [];
        const pureBase = key => 'https://lista.mercadolivre.com.br/official-observed/' + key.toLowerCase() + '/';
        const browser = { async navigate(url) {
            calls.push(url);
            current = keywordFinal && url.startsWith('https://lista.mercadolivre.com.br/official-observed/') ? url + 'keyword-subset/' : url;
        }, getURL: () => current, getNavigationStatus: () => ({ url: current,
            statusCode: blockDiscovery && current.includes('/discovery/') ? 403 : 200 }), async executeJavaScript() {
            const key = current.includes('mlb102') || current.includes('MLB102') ? 'MLB102' : 'MLB101';
            return { url: current, readyState: 'complete', effectiveCategoryId: key, categoryDocumentIdentity: true,
                categoryIdentityConflict: false, categoryNotFound: false, anuncios: [{}] };
        } };
        const discover = async category => {
            // A keyword lookup can help discover an emitted category href. Its
            // result is deliberately not an audit category-document observation.
            await audit.runner.read('https://lista.mercadolivre.com.br/discovery/' + category.key + '/?q=example', 'category');
            if (itemFilter) return { key: category.key, nome: category.nome, url: pureBase(category.key) + '?q=example',
                categoryBaseUrl: pureBase(category.key), source: 'verified_item_category_filter', exactItemCategoryId: category.key };
            return { key: category.key, nome: category.nome, url: pureBase(category.key) + (invalidReturnedQuery ? '?q=example' : ''),
                categoryBaseUrl: pureBase(category.key), source: 'official_category_href' };
        };
        audit = new LiveNavigationAudit({ snapshot, snapshotSha256, browser, onlyKeys: ['MLB101', 'MLB102'],
            wait: async () => {}, intervalMs: 0, discoverOfficialCategoryRoute: discover });
        audit.state.dependencyFailures.MLB100 = 'category_filters_unavailable';
        return { audit, calls };
    };
    const discovered = fallbackFixture();
    const discoveredSummary = await discovered.audit.run();
    assert.equal(discoveredSummary.fallbackDiscoveryNodes, 2, 'a shared ancestor failure cannot skip independently discoverable destinations');
    assert.equal(discoveredSummary.finalLeafDocumentAttempts, 2);
    assert.equal(discoveredSummary.nodeSpecificOnlineAttempts, 2);
    assert.equal(discoveredSummary.dependencyUnresolvedNodes, 0);
    assert.equal(discoveredSummary.verifiedLeafDocumentNodes, 2);
    assert.equal(discoveredSummary.completeSelectedCoverage, true);
    assert.equal(discoveredSummary.completeAllSnapshotCoverage, false);
    assert.equal(discoveredSummary.completeLiveCoverage, false, 'a successful two-leaf test is not a successful full-snapshot audit');
    assert.equal(discoveredSummary.snapshotTotalNodes, 3);
    assert.equal(discoveredSummary.totalNodes, 2);
    assert(discovered.audit.evidence().observations.every(item => !item.url.includes('/discovery/') && !new URL(item.url).search));
    assert.equal(discovered.calls.length, 4);

    const subset = fallbackFixture({ keywordFinal: true });
    const subsetSummary = await subset.audit.run();
    assert.equal(subsetSummary.verifiedDocumentNodes, 0, 'exact category IDs on a keyword subset cannot confirm the complete leaf document');
    assert.equal(subsetSummary.failureCodes.category_scope_mismatch, 2, 'the production runner rejects a keyword redirect before audit confirmation');
    assert.equal(subset.audit.evidence().observations.length, 0, 'unconfirmed final discovery pages remain quarantined from the live evidence manifest');
    const queryRoute = fallbackFixture({ invalidReturnedQuery: true });
    const querySummary = await queryRoute.audit.run();
    assert.equal(querySummary.verifiedDocumentNodes, 0);
    assert.equal(querySummary.finalLeafDocumentAttempts, 0);
    assert.equal(querySummary.failureCodes.official_route_not_found, 2);
    const filteredItem = fallbackFixture({ itemFilter: true });
    const filteredSummary = await filteredItem.audit.run();
    assert.equal(filteredSummary.filteredItemCandidates, 2);
    assert.equal(filteredSummary.verifiedLeafDocumentNodes, 0);
    assert.equal(filteredSummary.failureCodes.item_filter_not_leaf_document, 2);
    assert(Object.values(filteredItem.audit.state.fallbackCandidates).every(candidate => !new URL(candidate.url).search));
    const blockedFallback = fallbackFixture({ blockDiscovery: true });
    const blockedSummary = await blockedFallback.audit.run();
    assert.equal(blockedSummary.status, 'paused_access_denied');
    assert.equal(blockedSummary.completedCursor, 0);
    assert.equal(blockedFallback.calls.length, 1, 'HTTP denial during discovery cannot trigger a final navigation or a sibling attempt');

    const itemCache = JSON.parse(JSON.stringify(cache));
    itemCache.records[0][2] = 'https://lista.mercadolivre.com.br/observed-parent/';
    const itemSnapshot = snapshotFromCache(itemCache);
    const itemHash = createHash('sha256').update(JSON.stringify(itemCache)).digest('hex');
    const transportUrl = itemSnapshot.get('MLB100').url;
    const itemRoutes = { MLB101: transportUrl + 'observed-child-a/', MLB102: transportUrl + 'observed-child-b/' };
    let transportCurrent = '', itemState;
    const transportVisits = [];
    const sourcePage = () => ({ url: transportCurrent, readyState: 'complete', effectiveCategoryId: 'MLB100', categoryDocumentIdentity: true,
        categoryContext: { key: 'MLB100', baseUrl: transportUrl }, nextUrl: transportUrl + '_Desde_49_NoIndex_True', hasNext: true,
        anuncios: [{ id: 'MLB900001', url: 'https://produto.mercadolivre.com.br/MLB-900001-anuncio-_JM' },
            { id: 'MLB900002', url: 'https://produto.mercadolivre.com.br/MLB-900002-anuncio-_JM' },
            { id: 'MLB900003', url: 'https://produto.mercadolivre.com.br/MLB-900003-anuncio-_JM' },
            { id: 'MLB900004', url: 'https://produto.mercadolivre.com.br/MLB-900005-outro-anuncio-_JM' }],
        categoryItems: [{ id: 'MLB900001', categoryId: 'MLB101' }, { id: 'MLB900002', categoryId: 'MLB102' },
            { id: 'MLB900004', categoryId: 'MLB101' }] });
    const transportBrowser = { async navigate(url) { transportVisits.push(url); transportCurrent = transportUrl; }, getURL: () => transportCurrent,
        getNavigationStatus: () => ({ url: transportCurrent, statusCode: 200 }), async executeJavaScript() { return sourcePage(); } };
    const itemAudit = new LiveNavigationAudit({ snapshot: itemSnapshot, snapshotSha256: itemHash, browser: transportBrowser,
        onlyKeys: ['MLB101', 'MLB102'], wait: async () => {}, intervalMs: 0, persist: async state => { itemState = state; } });
    itemAudit.navigation.routes.set('MLB100', transportUrl);
    for (const [key, url] of Object.entries(itemRoutes)) itemAudit.navigation.routes.set(key, url);
    let firstScopedPage;
    const productionItemRead = itemAudit.runner.readCategory.bind(itemAudit.runner);
    itemAudit.runner.readCategory = async node => {
        const page = await productionItemRead(node);
        if (!firstScopedPage) firstScopedPage = page;
        return page;
    };
    const itemSummary = await itemAudit.run();
    assert.equal(itemSummary.verifiedExactItemScopeNodes, 2, 'the production ancestor/item-metadata fallback works separately for each requested leaf');
    assert.equal(itemSummary.verifiedLeafDocumentNodes, 0, 'an ancestor remains an ancestor when exact leaf items are filtered');
    assert.equal(itemSummary.verifiedDocumentNodes, 0, 'ancestor documents outside the selected leaves do not inflate selected document coverage');
    assert.equal(itemSummary.functionalVerifiedNodes, 2);
    assert.equal(itemSummary.completeSelectedCoverage, true);
    assert.equal(itemSummary.completeSelectedDocumentCoverage, false);
    assert.equal(itemSummary.completeLiveCoverage, false);
    assert.equal(itemSummary.transportPageReadAttempts, 2);
    assert.deepEqual(firstScopedPage.anuncios.map(ad => ad.id), ['MLB900001'], 'matching category metadata cannot retain an ad whose URL belongs to another item ID');
    assert.equal(transportVisits.length, 4, 'each leaf receives a real official-route attempt and a confirmed ancestor read');
    assert.equal(itemAudit.runner.checkpoint.currentCategory.key, 'MLB102');
    assert.deepEqual(itemAudit.functionalEvidence().exactItemScopes.map(proof => [proof.categoryKey, proof.documentCategoryId, proof.verifiedExactItemCount]),
        [['MLB101', 'MLB100', 1], ['MLB102', 'MLB100', 1]]);
    assert(itemAudit.functionalEvidence().exactItemScopes.every(proof => proof.paginationCoverage === 'not_audited' && proof.hasNextTransportPage));
    assert(itemAudit.evidence().observations.every(observation => observation.categoryKey === 'MLB100' && observation.documentCategoryId === 'MLB100'));
    assert(!JSON.stringify(itemState).includes('MLB90000'), 'checkpoint and evidence keep counts/descriptors without ad IDs or coupled item metadata');
    const itemResume = new LiveNavigationAudit({ snapshot: itemSnapshot, snapshotSha256: itemHash, browser: transportBrowser,
        state: itemState, wait: async () => {}, intervalMs: 0 });
    assert.equal((await itemResume.run()).verifiedExactItemScopeNodes, 2, 'resume retains the separate item-scope proof without replaying leaves');

    const wrongLeaf = new LiveNavigationAudit({ snapshot: itemSnapshot, snapshotSha256: itemHash, browser: transportBrowser,
        onlyKeys: ['MLB102'], wait: async () => {}, intervalMs: 0 });
    wrongLeaf.runner.readCategory = async () => firstScopedPage;
    const wrongLeafSummary = await wrongLeaf.run();
    assert.equal(wrongLeafSummary.verifiedExactItemScopeNodes, 0, 'a previous leaf scope cannot be transferred when the selected category changes');
    assert.equal(wrongLeafSummary.functionalVerifiedNodes, 0);
    assert.equal(wrongLeafSummary.failureCodes.item_filter_not_leaf_document, 1);
    assert.equal(wrongLeaf.runner.checkpoint.currentCategory.key, 'MLB102');

    const contextFixture = fixture(true);
    const contextAudit = new LiveNavigationAudit({ snapshot, snapshotSha256, browser: contextFixture.browser, wait: async () => {}, intervalMs: 0 });
    contextAudit.selectCategory('MLB101');
    const originalCategory = contextAudit.runner.checkpoint.currentCategory;
    const originalScope = contextAudit.runner.checkpoint.scope;
    const originalAncestry = contextAudit.runner.checkpoint.officialTaxonomy.ancestry;
    await assert.rejects(() => contextAudit.read(indexUrl, 'index', ''), error => error.code === 'category_access_denied');
    assert.equal(contextAudit.runner.checkpoint.currentCategory, originalCategory, 'failed ancestor/index reads restore the exact original context');
    assert.equal(contextAudit.runner.checkpoint.scope, originalScope);
    assert.equal(contextAudit.runner.checkpoint.officialTaxonomy.ancestry, originalAncestry);
    assert.equal(contextAudit.activeReadKey, '');
    console.log('Pesquisa de Mercado live navigation audit: online groups, real leaf documents, priority, progress, cancellation and resumable block pause OK');
}
testResumableProductionNavigation().catch(error => { console.error(error); process.exitCode = 1; });
