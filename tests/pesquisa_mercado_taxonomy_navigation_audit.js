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
    const fixture = entryStatus => {
        let url = '', calls = 0;
        const visits = [];
        const pages = {
            [rootUrl]: { categories: [a, b], effectiveCategoryId: 'MLB100', categoryDocumentIdentity: true,
                anuncios: [{ titulo: 'Produto em destaque' }] },
            [a.url]: { effectiveCategoryId: a.key, categoryDocumentIdentity: true, anuncios: [{}] },
            [b.url]: { effectiveCategoryId: b.key, categoryDocumentIdentity: true, anuncios: [{}] }
        };
        return { browser: { async navigate(next) { calls += 1; visits.push(next); url = next; }, getURL: () => url,
            getNavigationStatus: () => ({ url, statusCode: [rootUrl, indexUrl].includes(url) && entryStatus ? entryStatus : 200 }),
            async executeJavaScript() { return { url, readyState: 'complete', ...pages[url] }; } }, calls: () => calls, visits };
    };
    assert.deepEqual(breadthFirstKeys(snapshot), ['MLB100', 'MLB101', 'MLB102']);
    const first = fixture(false);
    let saved;
    const scanner = new LiveNavigationAudit({ snapshot, snapshotSha256, browser: first.browser, wait: async () => {}, intervalMs: 0,
        persist: async state => { saved = state; } });
    assert.equal(scanner.runner.officialNavigation, scanner.navigation, 'production reads share the audited official-route cache');
    const productionReads = [];
    const navReads = [];
    const read = scanner.runner.read.bind(scanner.runner);
    scanner.runner.read = async (url, purpose, key, expected, ...args) => {
        navReads.push([url, purpose, key, expected?.key]);
        return read(url, purpose, key, expected, ...args);
    };
    const productionReadCategory = scanner.runner.readCategory.bind(scanner.runner);
    scanner.runner.readCategory = async node => { productionReads.push(node.key); return productionReadCategory(node); };
    const partial = await scanner.run({ maxNodes: 2 });
    assert.deepEqual(productionReads, ['MLB101'], 'leaf auditing calls the production readCategory workflow');
    assert.equal(scanner.runner.checkpoint.currentCategory.key, 'MLB101', 'ancestor callbacks preserve the requested leaf');
    assert.equal(scanner.runner.checkpoint.scope.category.key, 'MLB101');
    assert.deepEqual(scanner.runner.checkpoint.officialTaxonomy.ancestry.map(node => node.key), ['MLB100', 'MLB101']);
    assert.equal(partial.status, 'paused_limit');
    assert.equal(partial.completedCursor, 2);
    assert.equal(partial.verifiedDocumentNodes, 1, 'department highlights cannot count as an opened category listing');
    assert.equal(partial.verifiedOnlineGroupNodes, 1);
    assert.equal(partial.functionalVerifiedNodes, 2);
    assert.equal(partial.physicalPageReads, 2);
    assert.deepEqual(first.visits, [rootUrl, a.url], 'the taxonomy department is the only entry before the leaf');
    assert.deepEqual(navReads[0], [rootUrl, 'navigation', 'MLB100', a.key]);
    assert.equal(scanner.groupVerification('MLB100').source, 'live_department_navigation');
    assert.equal(scanner.evidence().observations.some(item => item.url === rootUrl), false);
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
    const highlights = fixture(false);
    highlights.browser.executeJavaScript = async () => ({ url: rootUrl, readyState: 'complete', effectiveCategoryId: 'MLB100',
        categoryDocumentIdentity: true, categories: [], anuncios: [{ titulo: 'Oferta de destaque' }] });
    const promotional = new LiveNavigationAudit({ snapshot, snapshotSha256, browser: highlights.browser, onlyKeys: ['MLB100'],
        wait: async () => {}, intervalMs: 0 });
    const unproven = await promotional.run();
    assert.equal(unproven.verifiedDocumentNodes, 0);
    assert.equal(unproven.verifiedOnlineGroupNodes, 0, 'an identified department without child navigation is unverified');
    assert.equal(unproven.observedOfficialEdges, 0);
    assert(highlights.visits.length > 0 && highlights.visits.every(url => url === rootUrl || url === indexUrl));
    for (const mode of ['exact', 'different_permalink', 'denied_index']) {
        let current = '';
        const visits = [];
        const browser = { async navigate(url) { current = url; visits.push(url); }, getURL: () => current,
            getNavigationStatus: () => ({ url: current, statusCode: mode === 'denied_index' && current === indexUrl ? 403 : 200 }),
            async executeJavaScript() {
                const group = { ...snapshot.get('MLB100'), url: mode === 'different_permalink' ? base + '/c/wrong' : rootUrl, children: [a, b] };
                return { url: current, readyState: 'complete', ...(current === indexUrl ? { categoryTree: [group] }
                    : { effectiveCategoryId: 'MLB100', categoryDocumentIdentity: true, categories: [], anuncios: [{}] }) };
            } };
        const scanner = new LiveNavigationAudit({ snapshot, snapshotSha256, browser, onlyKeys: ['MLB100'], wait: async () => {}, intervalMs: 0 });
        const outcome = await scanner.run();
        assert.equal(visits.filter(url => url === indexUrl).length, 1, mode + ' attempts the official directory once');
        assert.equal(outcome.verifiedDocumentNodes, 0, mode);
        assert.equal(outcome.observedOfficialEdges, mode === 'exact' ? 2 : 0, mode);
        assert.equal(outcome.verifiedOnlineGroupNodes, mode === 'exact' ? 1 : 0, mode);
        if (mode === 'denied_index') assert.equal(outcome.status, 'paused_access_denied');
        if (mode === 'exact') {
            assert.equal(scanner.groupVerification('MLB100').source, 'live_category_directory');
            await scanner.read(indexUrl, 'index', 'MLB100', a);
            assert.equal(scanner.summary().observedOfficialEdges, 2, 'reading the same two links again cannot duplicate evidence');
        }
    }
    assert.throws(() => new LiveNavigationAudit({ snapshot, snapshotSha256: '0'.repeat(64), browser: second.browser, state: saved }));
    assert.throws(() => new LiveNavigationAudit({ snapshot, snapshotSha256, implementationSha256: 'changed-code', browser: second.browser, state: saved }));
    const blocked = fixture(403);
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
    assert.deepEqual(blocked.visits, [rootUrl], 'a 403 at the department cannot trigger a directory fallback');
    const limited = fixture(429);
    const limitedAudit = new LiveNavigationAudit({ snapshot, snapshotSha256, browser: limited.browser, wait: async () => {}, intervalMs: 0 });
    const limitedSummary = await limitedAudit.run();
    assert.equal(limitedSummary.status, 'paused_access_denied');
    assert.equal(limitedSummary.completedCursor, 0);
    assert.equal(limited.calls(), 1, 'rate limiting must stop further category visits');
    assert.equal(limitedAudit.classify({ waitingLogin: true }), 'login_required');
    assert.equal(limitedAudit.classify({ status: 403 }), 'login_required');
    const allowed = fixture(false);
    const afterBlock = new LiveNavigationAudit({ snapshot, snapshotSha256, browser: allowed.browser, state: blockedState, wait: async () => {}, intervalMs: 0 });
    assert.equal((await afterBlock.run()).functionalVerifiedNodes, 3);

    const parentListing = 'https://lista.mercadolivre.com.br/observed-parent/';
    const ambiguous = fixture(false);
    ambiguous.browser.getNavigationStatus = () => ({ url: parentListing, statusCode: 200 });
    const conflictAudit = new LiveNavigationAudit({ snapshot, snapshotSha256, browser: ambiguous.browser });
    conflictAudit.runner.read = async () => ({ url: parentListing, effectiveCategoryId: 'MLB100', categoryDocumentIdentity: true,
        categories: [a, b], categoryConflictingRouteIds: [a.key, 'other-text'] });
    await conflictAudit.read(parentListing, 'navigation', 'MLB100', b);
    await conflictAudit.save();
    const restoredConflict = new LiveNavigationAudit({ snapshot, snapshotSha256, browser: fixture(false).browser, state: conflictAudit.state });
    const restoredParent = await restoredConflict.read(parentListing, 'navigation', 'MLB100', b);
    assert.equal(restoredConflict.summary().pageReuseCount, 1);
    assert.deepEqual(restoredParent.categoryConflictingRouteIds, [a.key], 'cached navigation retains only the conflicting public category ID');
    assert.deepEqual(restoredConflict.groupVerification('MLB100').missingRouteKeys, [a.key], 'the resumed audit cannot count a conflicting route as verified');

    const trendsA = { ...a, url: parentListing + 'a/' }, trendsB = { ...b, url: parentListing + 'b/' };
    let parentReads = 0, parentStatus = 200, parentIdentity = 'MLB100';
    const trendsBrowser = { navigate: async url => assert.equal(url, parentListing), getURL: () => parentListing,
        getNavigationStatus: () => ({ url: parentListing, statusCode: parentStatus }), async executeJavaScript() {
            parentReads += 1;
            return { url: parentListing, readyState: 'complete', categoryTrends: true, effectiveCategoryId: parentIdentity,
                categoryDocumentIdentity: true, categoryContext: { key: parentIdentity, baseUrl: parentListing },
                categoryBrowse: { key: parentIdentity, url: parentListing + '*_NoIndex_True' },
                categories: [trendsA, trendsB], anuncios: [{}] };
        } };
    const trendsOptions = { snapshot, snapshotSha256, browser: trendsBrowser, wait: async () => {}, intervalMs: 0 };
    const trendsAudit = new LiveNavigationAudit(trendsOptions);
    await trendsAudit.read(parentListing, 'navigation', 'MLB100', trendsA);
    assert(parentReads >= 2, 'the audited link comes from the production stability checks');
    assert.deepEqual(trendsAudit.groupVerification('MLB100').missingRouteKeys, [trendsB.key]);
    assert.equal(trendsAudit.summary().observedOfficialEdges, 1);
    assert.equal(trendsAudit.summary().verifiedOnlineGroupNodes, 0);
    assert.deepEqual(trendsAudit.state.documentContexts, {});
    assert.deepEqual(trendsAudit.evidence().observations, [], 'Trends cards cannot establish an offer or a listing document');
    assert.deepEqual(trendsAudit.functionalEvidence().groups[0].trendsChildRoutes.map(edge => [edge.childKey, edge.url]), [[trendsA.key, trendsA.url]]);
    await trendsAudit.save();
    const resumedTrends = new LiveNavigationAudit({ ...trendsOptions, state: trendsAudit.state });
    parentStatus = 403;
    await assert.rejects(resumedTrends.read(parentListing, 'navigation', 'MLB100', trendsB), error => error.code === 'marketplace_http_403');
    parentStatus = 200; parentIdentity = 'MLB999';
    await assert.rejects(resumedTrends.read(parentListing, 'navigation', 'MLB100', trendsB), error => error.code === 'category_scope_mismatch');
    assert.deepEqual(resumedTrends.groupVerification('MLB100').missingRouteKeys, [trendsB.key]);
    assert.equal(resumedTrends.summary().verifiedDocumentNodes, 0);
    parentIdentity = 'MLB100';
    await resumedTrends.read(parentListing, 'navigation', 'MLB100', trendsB);
    assert.equal(resumedTrends.summary().observedOfficialEdges, 2, 'each child needs its own observed edge');
    assert.equal(resumedTrends.summary().verifiedOnlineGroupNodes, 1);
    assert.equal(resumedTrends.summary().verifiedDocumentNodes, 0, 'confirming every link still does not open a listing');

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
    namedCache.records[0][2] = 'https://lista.mercadolivre.com.br/_CatId_MLB100';
    namedCache.records[1][1] = 'Alpha'; namedCache.records[2][1] = 'Beta';
    const namedSnapshot = snapshotFromCache(namedCache);
    const namedHash = createHash('sha256').update(JSON.stringify(namedCache)).digest('hex');
    let namedUrl = '', namedState;
    const nameOnlyLinks = [a, b].map((item, index) => ({ key: item.url, nome: index ? 'Beta' : 'Alpha', url: item.url }));
    const namedBrowser = { async navigate(url) { assert.notEqual(url, rootUrl); namedUrl = url; }, getURL: () => namedUrl,
        getNavigationStatus: () => ({ url: namedUrl, statusCode: 200 }), async executeJavaScript() {
            if (namedUrl === indexUrl) return { url: namedUrl, readyState: 'complete', categoryTree: [{ ...namedSnapshot.get('MLB100'), url: rootUrl, children: nameOnlyLinks }] };
            const id = namedUrl === a.url ? 'MLB101' : 'MLB102';
            return { url: namedUrl, readyState: 'complete', categoryDocumentIdentity: true, effectiveCategoryId: id, anuncios: [{}] };
        } };
    const nameOnly = new LiveNavigationAudit({ snapshot: namedSnapshot, snapshotSha256: namedHash, browser: namedBrowser,
        wait: async () => {}, intervalMs: 0, persist: async state => { namedState = state; } });
    const groupOnly = await nameOnly.run({ maxNodes: 1 });
    assert.equal(namedUrl, indexUrl, 'legacy root aliases still resolve through the official directory');
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
        const discover = async (category, primaryError, seedCategoryIds) => {
            // A keyword lookup can help discover an emitted category href. Its
            // result is deliberately not an audit category-document observation.
            await audit.runner.read('https://lista.mercadolivre.com.br/discovery/' + category.key + '/?q=example', 'category');
            if (category.key === 'MLB100') {
                assert.deepEqual([...new Set(seedCategoryIds)].sort(), ['MLB101', 'MLB102']);
                return null;
            }
            assert.equal(primaryError.navigationDependencyKey, 'MLB100');
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
    assert.equal(discovered.audit.state.nodeOutcomes.MLB102.routeSource, 'official_category_href');
    assert.equal(discoveredSummary.completeSelectedCoverage, true);
    assert.equal(discoveredSummary.completeAllSnapshotCoverage, false);
    assert.equal(discoveredSummary.completeLiveCoverage, false, 'a successful two-leaf test is not a successful full-snapshot audit');
    assert.equal(discoveredSummary.snapshotTotalNodes, 3);
    assert.equal(discoveredSummary.totalNodes, 2);
    assert(discovered.audit.evidence().observations.every(item => !item.url.includes('/discovery/') && !new URL(item.url).search));
    assert.equal(discoveredSummary.fallbackDiscoveryAttempts, 3, 'both leaves and their shared ancestor were searched');
    assert.equal(discovered.calls.length, 5, 'the ancestor is searched once before independent leaf reads');

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
    assert.equal(itemSummary.transportPageReadAttempts, 0, 'a confirmed current ancestor needs no extra transport read');
    assert.deepEqual(firstScopedPage.anuncios.map(ad => ad.id), ['MLB900001'], 'matching category metadata cannot retain an ad whose URL belongs to another item ID');
    assert.equal(transportVisits.length, 2, 'each leaf receives a real official-route attempt and reuses its confirmed ancestor redirect');
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

    const failedAudit = new LiveNavigationAudit({ snapshot: itemSnapshot, snapshotSha256: itemHash, browser: transportBrowser,
        onlyKeys: ['MLB102'], wait: async () => {}, intervalMs: 0 });
    failedAudit.runner.readCategory = async () => {
        const error = new Error('Wrong category');
        error.code = 'category_scope_mismatch';
        error.categoryPage = { ...firstScopedPage, categoryItems: [{ id: 'MLB900001', categoryId: 'MLB102' }] };
        throw error;
    };
    const failedSummary = await failedAudit.run();
    assert.equal(failedAudit.state.nodeOutcomes.MLB102.failedDocument.documentCategoryId, 'MLB100');
    assert.equal(failedAudit.state.nodeOutcomes.MLB102.failedDocument.reportedExactItemCount, 1);
    assert.equal(failedSummary.functionalVerifiedNodes, 0, 'failed document diagnostics cannot become successful navigation evidence');
    assert.equal(failedSummary.verifiedDocumentNodes, 0);
    assert(!JSON.stringify(failedAudit.state).includes('MLB900001'), 'failure diagnostics retain counts without ad IDs');

    const contextFixture = fixture(403);
    const contextAudit = new LiveNavigationAudit({ snapshot, snapshotSha256, browser: contextFixture.browser, wait: async () => {}, intervalMs: 0 });
    contextAudit.selectCategory('MLB101');
    const originalCategory = contextAudit.runner.checkpoint.currentCategory;
    const originalScope = contextAudit.runner.checkpoint.scope;
    const originalAncestry = contextAudit.runner.checkpoint.officialTaxonomy.ancestry;
    await assert.rejects(() => contextAudit.read(indexUrl, 'index', ''), error => error.code === 'marketplace_http_403');
    assert.equal(contextAudit.runner.checkpoint.currentCategory, originalCategory, 'failed ancestor/index reads restore the exact original context');
    assert.equal(contextAudit.runner.checkpoint.scope, originalScope);
    assert.equal(contextAudit.runner.checkpoint.officialTaxonomy.ancestry, originalAncestry);
    assert.equal(contextAudit.activeReadKey, '');
    await cachedPositivePathsBypassOnlyEarlierFailures();
    await auditPublishedLeafDestination();
    await auditStoredCategoryDestinations();
    console.log('Pesquisa de Mercado live navigation audit: online groups, real leaf documents, priority, progress, cancellation and resumable block pause OK');
}

async function auditStoredCategoryDestinations() {
    const { LiveNavigationAudit } = require('../scripts/pesquisa_mercado_live_navigation_audit');
    const group = { ...snapshot.get('MLB100'), url: 'https://lista.mercadolivre.com.br/parent-known/' };
    const children = snapshot.children(group.key).map((node, i) => ({ ...node, url: group.url + 'child-' + i + '/' }));
    const leaf = children[0];
    for (const mode of ['listing', 'trends_group', 'denied', 'wrong_leaf']) {
        let current = '';
        const visits = [], forgotten = [];
        const store = { get: async ids => ({ MLB100: group.url, 'MLB100/MLB101': leaf.url })[ids.join('/')],
            put: async () => {}, forget: async (ids, url) => { forgotten.push([ids.join('/'), url]); } };
        const browser = { async navigate(url) { current = url; visits.push(url); }, getURL: () => current,
            getNavigationStatus: () => ({ url: current,
                statusCode: mode === 'denied' || mode === 'wrong_leaf' && current !== leaf.url ? 403 : 200 }),
            async executeJavaScript() {
                if (mode === 'trends_group' && current === group.url) return { url: current, readyState: 'complete', categoryTrends: true,
                    categoryNavigationHub: { version: 1, url: current, canonical: current, heading: group.nome },
                    categoryContext: { key: group.key, baseUrl: current }, categories: children, anuncios: [{}] };
                return { url: current, readyState: 'complete', categoryDocumentIdentity: true, anuncios: [{}],
                    effectiveCategoryId: mode === 'wrong_leaf' ? 'MLB999' : current === group.url ? group.key : leaf.key,
                    categories: current === group.url ? children : [] };
            } };
        const audit = new LiveNavigationAudit({ snapshot, snapshotSha256, browser, categoryRouteStore: store, intervalMs: 0, wait: async () => {},
            onlyKeys: mode === 'wrong_leaf' ? [leaf.key] : [group.key, leaf.key], discoverOfficialCategoryRoute: async () => null });
        const partial = await audit.run({ maxNodes: 1 });
        assert.equal(partial.verifiedLeafDocumentNodes, 0, 'a group or a wrong ID cannot prove a leaf');
        if (mode === 'denied' || mode === 'wrong_leaf') {
            assert.equal(partial.status, 'paused_access_denied');
            assert.equal(partial.functionalVerifiedNodes, 0);
            assert.deepEqual(visits, mode === 'denied' ? [group.url] : [leaf.url, group.url]);
            assert.deepEqual(forgotten, mode === 'wrong_leaf' ? [['MLB100/MLB101', leaf.url]] : []);
            continue;
        }
        assert.deepEqual(visits, [group.url], 'an identified direct group is visited before the marketing department');
        assert.equal(partial.verifiedOnlineGroupNodes, mode === 'listing' ? 1 : 0);
        assert.equal(partial.observedOfficialEdges, mode === 'listing' ? 2 : 1);
        if (mode === 'trends_group') assert.equal(partial.verifiedDocumentNodes, 0, 'the Trends edge cannot verify its source as a listing');
        const completed = await audit.run();
        assert.deepEqual(visits, [group.url, leaf.url]);
        assert.equal(completed.verifiedLeafDocumentNodes, 1);
        assert.equal(completed.finalLeafDocumentAttempts, 1);
        assert.equal(audit.state.documentContexts[leaf.key].url, leaf.url);
        assert.equal(audit.state.nodeOutcomes[leaf.key].routeSource, 'verified_direct_category_listing');
    }
}

async function auditPublishedLeafDestination() {
    const { LiveNavigationAudit } = require('../scripts/pesquisa_mercado_live_navigation_audit');
    const ids = ['MLB1574', 'MLB7069', 'MLB412355', 'MLB411819'], leaf = ids[3];
    const names = ['Casa, Móveis e Decoração', 'Segurança para Casa', 'Automação para Portões', 'Peças'];
    const root = 'https://www.mercadolivre.com.br/c/casa-moveis-e-decoracao';
    const base = 'https://lista.mercadolivre.com.br/casa-moveis-decoracao/seguranca-casa/automacao-portoes/pecas/';
    const listing = base + '*_NoIndex_True';
    const frozen = { ...cache, records: ids.map((id, i) => [id, names[i], i ? 'https://lista.mercadolivre.com.br/_CatId_' + id : root,
        ids.slice(i + 1, i + 2), ids.slice(0, i + 1)]) };
    const tree = snapshotFromCache(frozen), hash = createHash('sha256').update(JSON.stringify(frozen)).digest('hex');
    for (const mode of ['published', 'stale_fallback', 'different_leaf', 'denied']) {
        let current = '';
        const visits = [];
        const browser = { async navigate(url) { current = url; visits.push(url); }, getURL: () => current,
            getNavigationStatus: () => ({ url: current, statusCode: mode === 'denied' && current === listing ? 403 : 200 }),
            async executeJavaScript() {
                if (current === base) return { url: current, readyState: 'complete', categoryTrends: true,
                    categoryContext: { key: leaf, baseUrl: base }, categoryBrowse: { key: leaf, url: listing } };
                return { url: current, readyState: 'complete', categoryDocumentIdentity: true,
                    effectiveCategoryId: mode === 'different_leaf' ? 'MLB999' : leaf, anuncios: [{}] };
            } };
        const scan = new LiveNavigationAudit({ snapshot: tree, snapshotSha256: hash, onlyKeys: [leaf], browser,
            wait: async () => {}, intervalMs: 0, discoverOfficialCategoryRoute: async () => null });
        if (mode === 'stale_fallback') scan.state.fallbackCandidates[leaf] = {
            categoryBaseUrl: 'https://lista.mercadolivre.com.br/old-candidate/', source: 'official_category_href' };
        const result = await scan.run();
        assert.deepEqual(visits, [base, listing], mode);
        assert.equal(scan.navigation.routes.has(leaf), false, 'published entries need no cached alias resolution');
        if (mode === 'published' || mode === 'stale_fallback') {
            assert.equal(result.verifiedLeafDocumentNodes, 1, mode + ' must validate against the route emitted by production');
            assert.equal(scan.state.nodeOutcomes[leaf].routeSource, 'official_category_navigation');
            assert.equal(scan.state.documentContexts[leaf].url, listing);
        } else {
            assert.equal(result.verifiedLeafDocumentNodes, 0, mode);
            assert.equal(result.failureCodes[mode === 'denied' ? 'category_access_denied' : 'category_scope_mismatch'], 1, mode);
        }
    }
}
async function cachedPositivePathsBypassOnlyEarlierFailures() {
    const { LiveNavigationAudit } = require('../scripts/pesquisa_mercado_live_navigation_audit');
    const frozen = { version: 1, updatedAt: cache.updatedAt, fetchedAt: cache.fetchedAt, records: [
        ['MLB200', 'Root', 'https://www.mercadolivre.com.br/c/positive-root', ['MLB210', 'MLB220'], ['MLB200']],
        ['MLB210', 'Branch', 'https://lista.mercadolivre.com.br/_CatId_MLB210', ['MLB211'], ['MLB200', 'MLB210']],
        ['MLB211', 'Leaf', 'https://lista.mercadolivre.com.br/_CatId_MLB211', [], ['MLB200', 'MLB210', 'MLB211']],
        ['MLB220', 'Sibling', 'https://lista.mercadolivre.com.br/_CatId_MLB220', [], ['MLB200', 'MLB220']]
    ] };
    const tree = snapshotFromCache(frozen), hash = createHash('sha256').update(JSON.stringify(frozen)).digest('hex');
    const rootUrl = 'https://lista.mercadolivre.com.br/positive-root/';
    const branch = { ...tree.get('MLB210'), url: rootUrl + 'branch/' };
    const leaf = { ...tree.get('MLB211'), url: branch.url + 'leaf/' };
    const fixture = () => {
        let calls = 0;
        const forbidden = async () => { calls += 1; throw new Error('Cached route checks cannot use transport'); };
        const scanner = new LiveNavigationAudit({ snapshot: tree, snapshotSha256: hash, onlyKeys: [leaf.key], wait: async () => {}, intervalMs: 0,
            browser: { navigate: forbidden, executeJavaScript: forbidden, getURL: () => '', getNavigationStatus: () => null } });
        scanner.state.dependencyFailures.MLB200 = 'category_filters_unavailable';
        scanner.state.documentContexts.MLB210 = { url: branch.url, documentCategoryId: branch.key, statusCode: 200, observedAt: cache.updatedAt };
        return { scanner, calls: () => calls };
    };
    for (const mode of ['published_leaf', 'target_route', 'descendant_route', 'positive_chain']) {
        const { scanner, calls } = fixture();
        if (mode === 'target_route') scanner.navigation.routes.set(leaf.key, leaf.url);
        if (mode === 'descendant_route') scanner.navigation.routes.set(branch.key, branch.url);
        if (mode === 'positive_chain') {
            scanner.navigation.routes.set('MLB200', rootUrl);
            scanner.navigation.links.set('MLB200', [branch]);
        }
        if (mode !== 'target_route') scanner.navigation.links.set(branch.key, [leaf]);
        const contexts = JSON.stringify(scanner.state.documentContexts);
        assert.equal(await scanner.runner.officialCategoryUrl(mode === 'published_leaf' ? leaf : tree.get(leaf.key)), leaf.url, mode);
        assert.equal(calls(), 0, mode + ' follows only existing route candidates or a positive cached chain');
        assert.equal(JSON.stringify(scanner.state.documentContexts), contexts, 'route-cache use cannot alter verified document contexts');
        assert.equal(scanner.state.observations.length, 0, 'route-cache use cannot fabricate a live document observation');
        assert.equal(scanner.summary().functionalVerifiedNodes, 0, 'a candidate route is not online coverage');
    }
    for (const [description, links] of [
        ['parent URL alone', []],
        ['unrelated sibling', [{ ...tree.get('MLB220'), url: rootUrl + 'sibling/' }]],
        ['conflicting child ID', [{ ...branch, key: 'MLB999' }]],
        ['ambiguous child links', [branch, { ...branch, url: rootUrl + 'branch-alternative/' }]],
        ['unresolved alias', [{ ...branch, url: tree.get(branch.key).url }]]
    ]) {
        const { scanner, calls } = fixture();
        scanner.navigation.routes.set('MLB200', rootUrl); scanner.navigation.links.set('MLB200', links);
        await assert.rejects(scanner.runner.officialCategoryUrl(tree.get(leaf.key)), error =>
            error.code === 'category_filters_unavailable' && error.navigationDependencyKey === 'MLB200', description);
        assert.equal(calls(), 0, description + ' must not bypass the failed filter dependency');
    }
    const { scanner, calls } = fixture();
    scanner.navigation.routes.set('MLB200', rootUrl); scanner.navigation.links.set('MLB200', [branch]);
    scanner.state.dependencyFailures.MLB210 = 'category_filters_unavailable';
    await assert.rejects(scanner.runner.officialCategoryUrl(tree.get(leaf.key)), error =>
        error.code === 'category_filters_unavailable' && error.navigationDependencyKey === branch.key,
    'passing an earlier failure cannot bypass a later failed parent using only that parent URL');
    assert.equal(calls(), 0);
}

testResumableProductionNavigation().catch(error => { console.error(error); process.exitCode = 1; });
