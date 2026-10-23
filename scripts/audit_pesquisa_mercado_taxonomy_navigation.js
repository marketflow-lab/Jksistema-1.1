'use strict';

// Read-only public taxonomy audit. A valid tree is never reported as proof that
// its category documents were opened successfully on Mercado Livre.
const fs = require('fs/promises');
const { createHash } = require('crypto');
const { parseTaxonomyDump, MAX_BYTES } = require('../electron_app/main/modules/pesquisa-mercado-taxonomy');
const { normalizeCategory } = require('../electron_app/main/modules/pesquisa-mercado-runner');
const { OfficialCategoryNavigation, unsupportedCategoryAlias } = require('../electron_app/main/modules/pesquisa-mercado-category-navigation');

function snapshotFromCache(cache) {
    if (!cache || cache.version !== 1 || !Array.isArray(cache.records)
        || !Number.isFinite(Date.parse(cache.updatedAt))
        || Object.keys(cache).some(key => !['version', 'updatedAt', 'fetchedAt', 'records'].includes(key))) {
        throw new Error('Invalid public taxonomy cache.');
    }
    const records = new Map();
    for (const record of cache.records) {
        if (!Array.isArray(record) || record.length !== 5 || records.has(record[0])
            || !Array.isArray(record[3]) || !Array.isArray(record[4])) throw new Error('Invalid public taxonomy record.');
        records.set(record[0], record);
    }
    const dump = Object.create(null);
    for (const [key, nome, url, children, ancestors] of records.values()) {
        dump[key] = { id: key, name: nome, permalink: url,
            children_categories: children.map(id => ({ id, name: records.get(id)?.[1] })),
            path_from_root: ancestors.map(id => ({ id, name: records.get(id)?.[1] })) };
    }
    return parseTaxonomyDump(dump, cache.updatedAt);
}

function auditLiveEvidence(snapshot, snapshotSha256, evidence) {
    const keys = snapshot.cacheRecords().map(record => record[0]);
    if (!evidence) return { source: 'not_provided', attemptedNodes: 0, verifiedNodes: 0, verifiedNonempty: 0,
        verifiedEmpty: 0, failedNodes: 0, untestedNodes: keys.length, untestedCategoryKeys: keys, outcomes: [] };
    if (evidence.version !== 1 || evidence.snapshotSha256 !== snapshotSha256 || !Array.isArray(evidence.observations)
        || Object.keys(evidence).some(key => !['version', 'snapshotSha256', 'observations'].includes(key))) {
        throw new Error('Live evidence must identify the exact audited public snapshot.');
    }
    const allowed = new Set(['categoryKey', 'source', 'observedAt', 'url', 'documentCategoryId', 'statusCode',
        'listingCount', 'empty', 'categoryNotFound', 'identityConflict']);
    const latest = new Map();
    for (const item of evidence.observations) {
        if (!item || typeof item !== 'object' || Array.isArray(item) || Object.keys(item).some(key => !allowed.has(key))
            || !snapshot.get(item.categoryKey) || item.source !== 'live_document' || !Number.isFinite(Date.parse(item.observedAt))) {
            throw new Error('Invalid live public category observation.');
        }
        const category = snapshot.get(item.categoryKey);
        const normalized = normalizeCategory({ ...category, url: item.url });
        let outcome = 'unconfirmed_document';
        if (item.statusCode === 403 || item.statusCode === 429) outcome = 'access_denied';
        else if (item.categoryNotFound === true) outcome = 'category_not_found';
        else if (item.identityConflict === true || item.documentCategoryId && item.documentCategoryId !== item.categoryKey) outcome = 'scope_mismatch';
        else if (!normalized || unsupportedCategoryAlias(item.url)) outcome = 'unconfirmed_route';
        else if (Number.isInteger(item.statusCode) && item.statusCode >= 200 && item.statusCode < 300
            && item.documentCategoryId === item.categoryKey && item.identityConflict === false && item.categoryNotFound === false) {
            if (Number.isSafeInteger(item.listingCount) && item.listingCount > 0) outcome = 'verified_nonempty';
            else if (item.listingCount === 0 && item.empty === true) outcome = 'verified_empty';
        }
        const previous = latest.get(item.categoryKey);
        if (!previous || Date.parse(item.observedAt) >= Date.parse(previous.observedAt)) {
            latest.set(item.categoryKey, { categoryKey: item.categoryKey, outcome, observedAt: item.observedAt });
        }
    }
    const outcomes = [...latest.values()];
    const verifiedNonempty = outcomes.filter(item => item.outcome === 'verified_nonempty').length;
    const verifiedEmpty = outcomes.filter(item => item.outcome === 'verified_empty').length;
    return { source: 'supplied_live_document_evidence', attemptedNodes: latest.size,
        verifiedNodes: verifiedNonempty + verifiedEmpty, verifiedNonempty, verifiedEmpty,
        failedNodes: latest.size - verifiedNonempty - verifiedEmpty, untestedNodes: keys.length - latest.size,
        untestedCategoryKeys: keys.filter(key => !latest.has(key)), outcomes };
}

function auditSnapshot(snapshot, { snapshotSha256, evidence = null } = {}) {
    if (!/^[a-f0-9]{64}$/.test(snapshotSha256 || '')) throw new Error('The public snapshot SHA-256 is required.');
    const records = snapshot.cacheRecords(), roots = snapshot.children();
    const failures = [], aliases = [], directLinks = [], routeRejected = [];
    let edges = 0, leaves = 0, nodeChecks = 0, pathChecks = 0, negativeRouteChecks = 0;
    const navigation = new OfficialCategoryNavigation({ ancestry: [], children: {}, normalize: normalizeCategory,
        read: async () => { throw new Error('The structural audit never navigates.'); } });
    for (const [key] of records) {
        const node = snapshot.get(key), children = snapshot.children(key), ancestry = snapshot.pathFromRoot(key);
        nodeChecks += 1;
        if (!normalizeCategory(node)) failures.push({ key, code: 'invalid_category_schema' });
        if (!children.length) leaves += 1;
        pathChecks += 1;
        if (ancestry.at(-1)?.key !== key || ancestry.some((ancestor, index) => index > 0
            && !snapshot.children(ancestry[index - 1].key).some(child => child.key === ancestor.key))) {
            failures.push({ key, code: 'invalid_ancestry' });
        }
        for (const child of children) {
            edges += 1;
            const childPath = snapshot.pathFromRoot(child.key);
            if (childPath.at(-2)?.key !== key) failures.push({ key: child.key, code: 'invalid_parent_edge' });
        }
        if (unsupportedCategoryAlias(node.url)) aliases.push(key);
        else {
            directLinks.push(key);
            const parent = ancestry.at(-2);
            const parentUrl = parent && !unsupportedCategoryAlias(parent.url) ? parent.url : '';
            if (!navigation.route(node, node, parentUrl)) routeRejected.push(key);
        }
        const forbidden = [
            { ...node, url: 'https://example.invalid/category/' },
            { ...node, url: 'https://lista.mercadolivre.com.br/_CustId_123' },
            { ...node, url: node.url + (node.url.includes('?') ? '&' : '?') + 'seller_id=123' },
            { ...node, key: key === 'MLB999999999' ? 'MLB999999998' : 'MLB999999999' }
        ];
        for (const candidate of forbidden) {
            negativeRouteChecks += 1;
            if (navigation.route(candidate, node)) failures.push({ key, code: 'unsafe_route_accepted' });
        }
    }
    return { version: 1, generatedAt: new Date().toISOString(),
        snapshot: { sha256: snapshotSha256, updatedAt: snapshot.updatedAt, ...snapshot.summary,
            leafCount: leaves, branchCount: records.length - leaves, edgeCount: edges },
        structural: { source: 'frozen_public_taxonomy', nodeChecks, pathChecks, edgeChecks: edges,
            negativeRouteChecks, failures, passed: failures.length === 0 },
        cachedRoutes: { source: 'frozen_public_taxonomy', officialPermalinks: directLinks.length,
            unresolvedAliases: aliases.length, directlyEligiblePermalinks: directLinks.length - routeRejected.length,
            rejectedPermalinkCategoryKeys: routeRejected, unresolvedAliasCategoryKeys: aliases,
            categoryDocumentsOpened: 0 },
        live: auditLiveEvidence(snapshot, snapshotSha256, evidence),
        limits: ['Structural checks and route eligibility do not prove that category documents opened.',
            'Unresolved aliases require observed official routes or another verified official navigation source.',
            'Supplied live evidence is counted only with an exact document category ID and an acknowledged page result.'] };
}

async function auditCacheFile(cachePath, { evidencePath = null } = {}) {
    const bytes = await fs.readFile(cachePath);
    if (bytes.length > MAX_BYTES) throw new Error('Public taxonomy cache exceeds the supported size.');
    const snapshotSha256 = createHash('sha256').update(bytes).digest('hex');
    const snapshot = snapshotFromCache(JSON.parse(bytes.toString('utf8')));
    const evidence = evidencePath ? JSON.parse(await fs.readFile(evidencePath, 'utf8')) : null;
    return auditSnapshot(snapshot, { snapshotSha256, evidence });
}

async function main(argv) {
    const options = {};
    for (let index = 0; index < argv.length; index += 2) {
        if (!['--cache', '--evidence', '--out'].includes(argv[index]) || !argv[index + 1]) throw new Error('Use --cache FILE [--evidence FILE] [--out FILE].');
        options[argv[index].slice(2)] = argv[index + 1];
    }
    if (!options.cache) throw new Error('The --cache FILE argument is required; the audit never downloads or refreshes the cache.');
    const report = await auditCacheFile(options.cache, { evidencePath: options.evidence });
    if (options.out) await fs.writeFile(options.out, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
    const { untestedCategoryKeys: _untested, outcomes: _outcomes, ...liveSummary } = report.live;
    process.stdout.write(JSON.stringify({ snapshot: report.snapshot, structural: report.structural,
        cachedRoutes: { officialPermalinks: report.cachedRoutes.officialPermalinks,
            unresolvedAliases: report.cachedRoutes.unresolvedAliases,
            directlyEligiblePermalinks: report.cachedRoutes.directlyEligiblePermalinks, categoryDocumentsOpened: 0 },
        live: liveSummary }) + '\n');
    if (!report.structural.passed) process.exitCode = 1;
}

module.exports = { snapshotFromCache, auditSnapshot, auditLiveEvidence, auditCacheFile };
if (require.main === module) main(process.argv.slice(2)).catch(() => {
    process.stderr.write('Public taxonomy audit failed; check the cache schema, evidence identity and output destination.\n');
    process.exitCode = 1;
});
