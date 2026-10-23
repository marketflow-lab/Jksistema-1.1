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
    const { LiveNavigationAudit, breadthFirstKeys, safePublicUrl } = require('../scripts/pesquisa_mercado_live_navigation_audit');
    const base = 'https://www.mercadolivre.com.br';
    const rootUrl = snapshot.get('MLB100').url;
    const indexUrl = base + '/categorias';
    const a = { ...snapshot.get('MLB101'), url: 'https://lista.mercadolivre.com.br/observed-child-a/' };
    const b = { ...snapshot.get('MLB102'), url: 'https://lista.mercadolivre.com.br/observed-child-b/' };
    const fixture = denied => {
        let url = '', calls = 0;
        const pages = {
            [rootUrl]: { effectiveCategoryId: 'MLB100', categoryDocumentIdentity: true, empty: true, categories: [a, b] },
            [indexUrl]: { categoryTree: [{ ...snapshot.get('MLB100'), children: [] }] },
            [a.url]: { effectiveCategoryId: a.key, categoryDocumentIdentity: true, anuncios: [{}] },
            [b.url]: { effectiveCategoryId: b.key, categoryDocumentIdentity: true, anuncios: [{}] }
        };
        return { browser: { async navigate(next) { calls += 1; url = next; }, getURL: () => url,
            getNavigationStatus: () => ({ url, statusCode: denied && url === indexUrl ? 403 : 200 }),
            async executeJavaScript() { return { url, readyState: 'complete', ...pages[url] }; } }, calls: () => calls };
    };
    assert.deepEqual(breadthFirstKeys(snapshot), ['MLB100', 'MLB101', 'MLB102']);
    const first = fixture(false);
    let saved;
    const scanner = new LiveNavigationAudit({ snapshot, snapshotSha256, browser: first.browser, wait: async () => {}, intervalMs: 0,
        persist: async state => { saved = state; } });
    const partial = await scanner.run({ maxNodes: 2 });
    assert.equal(partial.status, 'paused_limit');
    assert.equal(partial.completedCursor, 2);
    assert.equal(partial.verifiedDocumentNodes, 2);
    assert.equal(partial.pageReuseCount, 1, 'the observed parent document is reused by the production resolver');
    assert.equal(partial.physicalPageReads, first.calls(), 'navigation retries and direct reads count their physical navigations');
    const second = fixture(false);
    const resumed = new LiveNavigationAudit({ snapshot, snapshotSha256, browser: second.browser, state: saved, wait: async () => {}, intervalMs: 0 });
    const complete = await resumed.run();
    assert.equal(complete.status, 'traversal_completed');
    assert.equal(complete.verifiedDocumentNodes, 3);
    assert.equal(complete.completeLiveCoverage, true);
    assert.equal(second.calls(), 1, 'resume visits only the remaining document using the frozen official routes');
    assert.throws(() => new LiveNavigationAudit({ snapshot, snapshotSha256: '0'.repeat(64), browser: second.browser, state: saved }));
    assert.throws(() => new LiveNavigationAudit({ snapshot, snapshotSha256, implementationSha256: 'changed-code', browser: second.browser, state: saved }));
    const blocked = fixture(true);
    let blockedState;
    const denial = new LiveNavigationAudit({ snapshot, snapshotSha256, browser: blocked.browser, wait: async () => {}, intervalMs: 0,
        persist: async state => { blockedState = state; } });
    const paused = await denial.run();
    assert.equal(paused.status, 'paused_access_denied');
    assert.equal(paused.completedCursor, 1, 'denial preserves the current node for a later authenticated resume');
    assert.equal(paused.verifiedDocumentNodes, 1);
    assert.equal(paused.completeLiveCoverage, false);
    assert.equal(blocked.calls(), 2, 'an HTTP block cannot launch a cascade through the remaining categories');
    const allowed = fixture(false);
    const afterBlock = new LiveNavigationAudit({ snapshot, snapshotSha256, browser: allowed.browser, state: blockedState, wait: async () => {}, intervalMs: 0 });
    assert.equal((await afterBlock.run()).verifiedDocumentNodes, 3);
    assert.equal(safePublicUrl('https://lista.mercadolivre.com.br/category/?access_token=private'), '');
    assert.equal(safePublicUrl('https://www.mercadolivre.com.br/login?token=private'), '');
    assert.equal(safePublicUrl(indexUrl), indexUrl);
    console.log('Pesquisa de Mercado live navigation audit: production resolver, shared parent reuse, resume and HTTP block pause OK');
}
testResumableProductionNavigation().catch(error => { console.error(error); process.exitCode = 1; });
