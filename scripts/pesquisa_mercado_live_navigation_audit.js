'use strict';

// Uses only an explicitly marked development page. No application backend,
// installed application, user database, seller collection or publication runs.
const fs = require('fs/promises');
const path = require('path');
const { createHash } = require('crypto');
const { snapshotFromCache, auditLiveEvidence } = require('./audit_pesquisa_mercado_taxonomy_navigation');
const { PesquisaMercadoRunner, normalizeCategory, verifiedCategoryPage, sameCategoryPage, canonicalUrl } = require('../electron_app/main/modules/pesquisa-mercado-runner');
const { OfficialCategoryNavigation, unsupportedCategoryAlias, officialCategoryMoreUrl } = require('../electron_app/main/modules/pesquisa-mercado-category-navigation');
const pageScripts = require('../static/pesquisa-mercado/page-scripts');

const FAILURE_CODES = new Set(['category_link_missing', 'category_link_ambiguous', 'category_filters_unavailable',
    'category_scope_mismatch', 'category_not_found', 'category_load_timeout', 'category_access_denied']);
const clone = value => JSON.parse(JSON.stringify(value));

function safePublicUrl(value) {
    if (value === 'https://www.mercadolivre.com.br/categorias' || value === 'https://www.mercadolivre.com.br/categorias/') return value;
    return normalizeCategory({ key: 'MLB1', nome: 'Categoria', url: value })?.url || '';
}

function breadthFirstKeys(snapshot) {
    const keys = snapshot.children().map(node => node.key);
    for (let index = 0; index < keys.length; index += 1) keys.push(...snapshot.children(keys[index]).map(node => node.key));
    if (keys.length !== snapshot.summary.total || new Set(keys).size !== keys.length) throw new Error('Invalid frozen category traversal.');
    return keys;
}

function safePage(page) {
    const categories = items => (Array.isArray(items) ? items : []).map(item => normalizeCategory({ key: item.key, nome: item.nome, url: item.url })).filter(Boolean);
    const count = Number.isSafeInteger(page.listingCount) && page.listingCount >= 0 ? page.listingCount : (page.anuncios || []).length;
    return { url: safePublicUrl(page.url), effectiveCategoryId: /^MLB\d+$/.test(page.effectiveCategoryId || '') ? page.effectiveCategoryId : '',
        documentCategoryId: /^MLB\d+$/.test(page.documentCategoryId || '') ? page.documentCategoryId
            : page.categoryDocumentIdentity === true && /^MLB\d+$/.test(page.effectiveCategoryId || '') ? page.effectiveCategoryId : '',
        categoryIdentityConflict: page.categoryIdentityConflict === true, categoryNotFound: page.categoryNotFound === true,
        categoryDocumentIdentity: page.categoryDocumentIdentity === true || !!page.documentCategoryId && page.documentCategoryId === page.effectiveCategoryId && page.categoryIdentityConflict !== true,
        categoryFilterMoreUrl: officialCategoryMoreUrl(page.categoryFilterMoreUrl, page.url) || '',
        categoryFilterPresent: page.categoryFilterPresent === true, readyState: page.readyState === 'loading' ? 'loading' : 'complete',
        empty: page.empty === true, listingCount: count, categories: categories(page.categories),
        categoryTree: (page.categoryTree || []).map(item => {
            const category = normalizeCategory({ key: item.key, nome: item.nome, url: item.url });
            return category ? { ...category, children: categories(item.children) } : null;
        }).filter(Boolean) };
}

function restoredPage(value) {
    return { ...value, anuncios: value.listingCount > 0 ? [{}] : [] };
}

class LiveNavigationAudit {
    constructor({ snapshot, snapshotSha256, implementationSha256 = 'fixture', browser, state = null, wait = ms => new Promise(resolve => setTimeout(resolve, ms)),
        intervalMs = 1000, checkpointEvery = 25, persist = async () => {}, onProgress = () => {}, now = () => new Date().toISOString() }) {
        this.snapshot = snapshot;
        this.snapshotSha256 = snapshotSha256;
        this.implementationSha256 = implementationSha256;
        this.browser = browser;
        this.wait = wait;
        this.intervalMs = intervalMs;
        this.persist = persist;
        this.checkpointEvery = checkpointEvery;
        this.onProgress = onProgress;
        this.now = now;
        this.keys = breadthFirstKeys(snapshot);
        if (state && (state.version !== 1 || state.snapshotSha256 !== snapshotSha256 || state.implementationSha256 !== implementationSha256 || state.total !== this.keys.length
            || !Number.isSafeInteger(state.cursor) || state.cursor < 0 || state.cursor > this.keys.length)) throw new Error('The saved audit belongs to another snapshot.');
        this.state = state ? clone(state) : { version: 1, snapshotSha256, implementationSha256, total: this.keys.length, cursor: 0,
            startedAt: now(), updatedAt: now(), status: 'ready', physicalPageReads: 0, pageReuseCount: 0,
            nodeOutcomes: {}, observations: [], resolvedRoutes: [], parentLinks: [], cachedPages: [], dependencyFailures: {} };
        this.pageCache = new Map();
        for (const [url, page] of this.state.cachedPages || []) {
            if (safePublicUrl(url) && page && safePublicUrl(page.url)) this.pageCache.set(url, safePage(page));
        }
        this.controller = new AbortController();
        const children = Object.fromEntries(this.keys.map(key => [key, snapshot.children(key)]));
        this.lastReadAt = 0;
        const trackedBrowser = Object.create(browser);
        trackedBrowser.navigate = async (url, signal) => {
            const delay = Math.max(0, this.intervalMs - (Date.now() - this.lastReadAt));
            if (delay) await this.wait(delay);
            this.lastReadAt = Date.now();
            this.state.physicalPageReads += 1;
            return browser.navigate(url, signal);
        };
        this.runner = new PesquisaMercadoRunner({ browser: trackedBrowser, pageScripts, collector: {}, wait,
            api: async () => { throw new Error('A public navigation audit cannot call the application backend.'); } });
        this.runner.controller = this.controller;
        this.runner.job = { id: 'public-navigation-audit', status: 'running', counters: {} };
        this.runner.checkpoint = { categories: [], scope: {}, officialTaxonomy: { children }, currentCategory: null };
        this.navigation = new OfficialCategoryNavigation({ ancestry: [], children, normalize: normalizeCategory,
            read: (url, purpose, key, expected) => this.read(url, purpose, key, expected) });
        for (const root of snapshot.children()) this.navigation.records.set(root.key, root);
        for (const [key, url] of this.state.resolvedRoutes || []) {
            const node = snapshot.get(key);
            if (node && this.navigation.route({ ...node, url }, node)) this.navigation.routes.set(key, url);
        }
        for (const [key, links] of this.state.parentLinks || []) {
            if (snapshot.get(key) && Array.isArray(links)) this.navigation.links.set(key, links.map(item => normalizeCategory(item)).filter(Boolean));
        }
        this.cancelled = false;
    }

    cancel() { this.cancelled = true; this.controller.abort(); if (typeof this.browser.stop === 'function') this.browser.stop(); }

    selectCategory(key) {
        const node = key && this.snapshot.get(key);
        this.runner.checkpoint.currentCategory = node || null;
        this.runner.checkpoint.scope = node ? { category: node } : {};
    }

    reusable(page, url, purpose, key, expected) {
        if (!page || page.categoryIdentityConflict || page.categoryNotFound) return false;
        if (purpose === 'index') return sameCategoryPage(url, page.url) && page.categoryTree.length > 0;
        if (!verifiedCategoryPage(url, page.url, key, page.effectiveCategoryId)) return false;
        if (key && page.effectiveCategoryId && page.effectiveCategoryId !== key) return false;
        if ((purpose === 'navigation' || purpose === 'category-options') && expected) {
            const parentUrl = purpose === 'category-options' ? this.navigation.routes.get(key) || url : url;
            try { return !!this.navigation.choose(page.categories, expected, parentUrl); }
            catch (_error) { return purpose === 'navigation' && !!page.categoryFilterMoreUrl; }
        }
        return page.listingCount > 0 || page.empty || page.categories.length > 0;
    }

    observation(key, page, statusCode) {
        if (!key || !this.snapshot.get(key)) return;
        this.state.observations.push({ categoryKey: key, source: 'live_document', observedAt: this.now(),
            url: safePublicUrl(page.url), documentCategoryId: page.documentCategoryId || '', statusCode: Number.isInteger(statusCode) ? statusCode : 0,
            listingCount: page.listingCount, empty: page.empty, categoryNotFound: page.categoryNotFound,
            identityConflict: page.categoryIdentityConflict });
    }

    async read(url, purpose = 'category', key = '', expected = null) {
        const cached = this.pageCache.get(url);
        if (this.reusable(cached, url, purpose, key, expected)) {
            this.state.pageReuseCount += 1;
            return restoredPage(cached);
        }
        this.selectCategory(key);
        try {
            const observed = await this.runner.read(url, purpose, key, expected);
            const page = safePage(observed);
            const status = typeof this.browser.getNavigationStatus === 'function' ? this.browser.getNavigationStatus() : null;
            if (purpose === 'index' || page.categories.length || key && this.snapshot.children(key).length) this.pageCache.set(url, page);
            // An expansion control page proves observed child links, not that
            // the corresponding category document displayed a listing.
            if (purpose !== 'category-options') this.observation(key, page, status && canonicalUrl(status.url) === canonicalUrl(page.url) ? status.statusCode : 0);
            return observed;
        } catch (error) {
            const status = typeof this.browser.getNavigationStatus === 'function' ? this.browser.getNavigationStatus() : null;
            if (purpose !== 'category-options' && key && status && [403, 429].includes(status.statusCode)) {
                this.observation(key, { url: status.url, documentCategoryId: '', listingCount: 0, empty: false,
                    categoryNotFound: false, categoryIdentityConflict: false }, status.statusCode);
            }
            throw error;
        }
    }

    classify(error) {
        if (error.waitingLogin || error.status === 401 || error.status === 403) return 'login_required';
        if (error.name === 'AbortError') return 'audit_interrupted';
        return FAILURE_CODES.has(error.code) ? error.code : 'navigation_read_failed';
    }

    async save() {
        this.state.updatedAt = this.now();
        this.state.resolvedRoutes = [...this.navigation.routes.entries()];
        this.state.parentLinks = [...this.navigation.links.entries()].map(([key, links]) => [key, links.map(item => normalizeCategory(item)).filter(Boolean)]);
        this.state.cachedPages = [...this.pageCache.entries()];
        await this.persist(clone(this.state), this.summary(), this.evidence());
    }

    evidence() { return { version: 1, snapshotSha256: this.snapshotSha256, observations: clone(this.state.observations) }; }

    summary() {
        const outcomes = Object.values(this.state.nodeOutcomes);
        const codes = {};
        for (const item of outcomes) if (item.code) codes[item.code] = (codes[item.code] || 0) + 1;
        const live = auditLiveEvidence(this.snapshot, this.snapshotSha256, this.evidence());
        let observedOfficialEdges = 0;
        for (const [parentKey, links] of this.navigation.links) {
            const parent = this.snapshot.get(parentKey);
            if (!parent) continue;
            const parentUrl = this.navigation.routes.get(parentKey) || (!unsupportedCategoryAlias(parent.url) ? parent.url : '');
            if (!parentUrl) continue;
            for (const child of this.snapshot.children(parentKey)) {
                try { if (this.navigation.choose(links, child, parentUrl)) observedOfficialEdges += 1; } catch (_error) {}
            }
        }
        return { version: 1, snapshotSha256: this.snapshotSha256, implementationSha256: this.implementationSha256, status: this.state.status, totalNodes: this.keys.length,
            completedCursor: this.state.cursor, requestedNodes: outcomes.length,
            productionReadAcceptedNodes: outcomes.filter(item => item.outcome === 'production_read_accepted').length,
            dependencyUnresolvedNodes: outcomes.filter(item => item.outcome === 'dependency_unresolved').length,
            physicalPageReads: this.state.physicalPageReads, pageReuseCount: this.state.pageReuseCount,
            observedOfficialEdges, totalOfficialEdges: this.keys.length - this.snapshot.summary.rootCount,
            verifiedDocumentNodes: live.verifiedNodes, verifiedNonempty: live.verifiedNonempty, verifiedEmpty: live.verifiedEmpty,
            observedDocumentNodes: live.attemptedNodes, untestedDocumentNodes: live.untestedNodes,
            failureCodes: codes, updatedAt: this.state.updatedAt,
            completeLiveCoverage: live.verifiedNodes === this.keys.length,
            limitations: ['A completed traversal may still contain unresolved or unconfirmed category documents.',
                'Only exact document identity plus a confirmed page result counts as verified live coverage.'] };
    }

    async run({ maxNodes = Infinity } = {}) {
        this.state.status = 'running';
        let visited = 0;
        await this.save();
        while (this.state.cursor < this.keys.length && visited < maxNodes) {
            if (this.controller.signal.aborted) {
                this.state.status = 'paused_interrupted';
                await this.save();
                return this.summary();
            }
            const key = this.keys[this.state.cursor], node = this.snapshot.get(key);
            const dependency = this.snapshot.pathFromRoot(key).find(item => this.state.dependencyFailures[item.key]);
            if (dependency) {
                this.state.nodeOutcomes[key] = { outcome: 'dependency_unresolved', code: this.state.dependencyFailures[dependency.key], dependencyKey: dependency.key };
                this.state.cursor += 1; visited += 1;
                this.onProgress({ cursor: this.state.cursor, total: this.keys.length, status: 'running', physicalPageReads: this.state.physicalPageReads });
                if (visited % this.checkpointEvery === 0) await this.save();
                continue;
            }
            try {
                this.selectCategory(key);
                const url = unsupportedCategoryAlias(node.url) ? await this.navigation.resolve(key) : node.url;
                await this.read(url, 'category', key);
                this.state.nodeOutcomes[key] = { outcome: 'production_read_accepted' };
                this.state.cursor += 1; visited += 1;
            } catch (error) {
                const code = this.classify(error);
                this.state.nodeOutcomes[key] = { outcome: 'unresolved', code };
                if (code === 'category_access_denied' || code === 'login_required' || code === 'audit_interrupted') {
                    this.state.status = code === 'category_access_denied' ? 'paused_access_denied' : code === 'login_required' ? 'paused_login' : 'paused_interrupted';
                    await this.save();
                    return this.summary();
                }
                if (error.sharedCategoryFailure && this.snapshot.get(error.navigationDependencyKey)) {
                    this.state.dependencyFailures[error.navigationDependencyKey] = code;
                }
                this.state.cursor += 1; visited += 1;
            }
            this.onProgress({ cursor: this.state.cursor, total: this.keys.length, status: 'running', physicalPageReads: this.state.physicalPageReads });
            if (visited % this.checkpointEvery === 0) await this.save();
        }
        this.state.status = this.state.cursor === this.keys.length ? 'traversal_completed' : 'paused_limit';
        await this.save();
        return this.summary();
    }
}

async function writeJsonAtomic(file, value) {
    const temporary = file + '.' + process.pid + '.tmp';
    await fs.writeFile(temporary, JSON.stringify(value) + '\n', { flag: 'w' });
    await fs.rename(temporary, file);
}

async function acquireAuditLock(lockPath, resume) {
    try {
        const lock = await fs.open(lockPath, 'wx');
        await lock.writeFile(JSON.stringify({ pid: process.pid }) + '\n');
        return lock;
    } catch (error) {
        if (error.code !== 'EEXIST' || !resume) throw error;
        const previous = JSON.parse(await fs.readFile(lockPath, 'utf8'));
        if (!Number.isSafeInteger(previous.pid) || previous.pid <= 0) throw new Error('Invalid audit lock.');
        let alive = true;
        try { process.kill(previous.pid, 0); } catch (probeError) { if (probeError.code === 'ESRCH') alive = false; }
        if (alive) throw new Error('Another audit process still owns this output directory.');
        await fs.unlink(lockPath);
        return acquireAuditLock(lockPath, false);
    }
}

async function main(argv) {
    const options = {};
    for (let index = 0; index < argv.length; index += 1) {
        const arg = argv[index];
        if (arg === '--resume') { options.resume = true; continue; }
        if (!['--target-ws', '--target-marker', '--taxonomy-file', '--output-dir', '--max-nodes', '--interval-ms', '--checkpoint-every'].includes(arg) || !argv[index + 1]) throw new Error('Invalid audit arguments.');
        options[arg.slice(2)] = argv[++index];
    }
    if (!options['target-ws'] || !options['target-marker'] || !options['taxonomy-file'] || !options['output-dir']) throw new Error('An explicitly marked development target and frozen public taxonomy are required.');
    const maxNodes = options['max-nodes'] === undefined ? Infinity : Number(options['max-nodes']);
    const intervalMs = options['interval-ms'] === undefined ? 1000 : Number(options['interval-ms']);
    const checkpointEvery = options['checkpoint-every'] === undefined ? 25 : Number(options['checkpoint-every']);
    if (!(maxNodes === Infinity || Number.isSafeInteger(maxNodes) && maxNodes > 0) || !Number.isSafeInteger(intervalMs) || intervalMs < 500
        || !Number.isSafeInteger(checkpointEvery) || checkpointEvery < 1 || checkpointEvery > 500) throw new Error('Invalid audit limits.');
    const bytes = await fs.readFile(options['taxonomy-file']);
    const snapshotSha256 = createHash('sha256').update(bytes).digest('hex');
    const implementation = createHash('sha256');
    for (const filename of ['../electron_app/main/modules/pesquisa-mercado-runner.js', '../electron_app/main/modules/pesquisa-mercado-category-navigation.js',
        '../static/pesquisa-mercado/page-scripts.js', './pesquisa_mercado_live_navigation_audit.js']) {
        implementation.update(filename); implementation.update(await fs.readFile(path.resolve(__dirname, filename)));
    }
    const implementationSha256 = implementation.digest('hex');
    const snapshot = snapshotFromCache(JSON.parse(bytes.toString('utf8')));
    const directory = path.resolve(options['output-dir']);
    await fs.mkdir(directory, { recursive: true });
    const statePath = path.join(directory, 'state.json'), summaryPath = path.join(directory, 'summary.json'), evidencePath = path.join(directory, 'evidence.json');
    const state = options.resume ? JSON.parse(await fs.readFile(statePath, 'utf8')) : null;
    if (!options.resume) {
        try { await fs.access(statePath); throw new Error('Use --resume for an existing audit.'); }
        catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
    const lockPath = path.join(directory, 'audit.lock');
    const lock = await acquireAuditLock(lockPath, options.resume);
    let browser, audit;
    try {
        // Imported only in CLI mode; unit fixtures never attach to a browser.
        const { createCdpAdapter } = require('./pesquisa_mercado_live_smoke');
        browser = await createCdpAdapter({ targetWs: options['target-ws'], marker: options['target-marker'] });
        audit = new LiveNavigationAudit({ snapshot, snapshotSha256, implementationSha256, browser, state, intervalMs, checkpointEvery,
            onProgress: progress => { if (progress.cursor % 25 === 0) process.stdout.write(JSON.stringify(progress) + '\n'); },
            persist: async (nextState, summary, evidence) => {
                await writeJsonAtomic(statePath, nextState);
                await writeJsonAtomic(evidencePath, evidence);
                await writeJsonAtomic(summaryPath, summary);
            } });
        const stop = () => audit.cancel();
        process.once('SIGINT', stop); process.once('SIGTERM', stop);
        try {
            const summary = await audit.run({ maxNodes });
            process.stdout.write(JSON.stringify(summary) + '\n');
            if (summary.status !== 'traversal_completed') process.exitCode = 2;
        } finally { process.removeListener('SIGINT', stop); process.removeListener('SIGTERM', stop); }
    } finally {
        if (browser && typeof browser.detach === 'function') await browser.detach();
        await lock.close();
        await fs.unlink(lockPath).catch(() => {});
    }
}

module.exports = { LiveNavigationAudit, breadthFirstKeys, safePage, safePublicUrl, writeJsonAtomic };
if (require.main === module) main(process.argv.slice(2)).catch(() => {
    process.stderr.write('Live public navigation audit stopped. Review the saved summary and the explicitly marked development target.\n');
    process.exitCode = 1;
});
