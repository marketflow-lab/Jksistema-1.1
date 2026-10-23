'use strict';

// Uses only an explicitly marked development page. No application backend,
// installed application, user database, seller collection or publication runs.
const fs = require('fs/promises');
const path = require('path');
const { createHash } = require('crypto');
const { snapshotFromCache, auditLiveEvidence } = require('./audit_pesquisa_mercado_taxonomy_navigation');
const { PesquisaMercadoRunner, normalizeCategory, verifiedCategoryPage, sameCategoryPage, canonicalUrl } = require('../electron_app/main/modules/pesquisa-mercado-runner');
const { OfficialCategoryNavigation, unsupportedCategoryAlias, officialCategoryMoreUrl, categoryBasePath } = require('../electron_app/main/modules/pesquisa-mercado-category-navigation');
const { scopeCategoryItems, validateCategoryItemScope, categoryItemScopeUrlAllowed } = require('../electron_app/main/modules/pesquisa-mercado-category-item-scope');
const pageScripts = require('../static/pesquisa-mercado/page-scripts');

const FAILURE_CODES = new Set(['category_link_missing', 'category_link_ambiguous', 'category_filters_unavailable',
    'category_scope_mismatch', 'category_not_found', 'category_load_timeout', 'category_access_denied',
    'official_route_not_found', 'item_filter_not_leaf_document', 'discovered_document_unconfirmed']);
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

function auditSelection(snapshot, { scopeKey = '', onlyKeys = [] } = {}) {
    if (scopeKey && onlyKeys.length || !Array.isArray(onlyKeys) || scopeKey && !snapshot.get(scopeKey)
        || onlyKeys.some(key => !snapshot.get(key))) throw new Error('Invalid public audit selection.');
    let keys;
    if (scopeKey) {
        keys = [scopeKey];
        for (let index = 0; index < keys.length; index += 1) keys.push(...snapshot.children(keys[index]).map(node => node.key));
    } else keys = onlyKeys.length ? [...new Set(onlyKeys)] : breadthFirstKeys(snapshot);
    return { kind: scopeKey ? 'subtree' : onlyKeys.length ? 'explicit_keys' : 'all', rootKey: scopeKey,
        onlyKeys: onlyKeys.length ? [...new Set(onlyKeys)] : [], keys };
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
        intervalMs = 1000, checkpointEvery = 25, priorityKeys = [], scopeKey = '', onlyKeys = [],
        discoverOfficialCategoryRoute = null, persist = async () => {}, onProgress = () => {},
        shouldCancel = async () => false, now = () => new Date().toISOString() }) {
        this.snapshot = snapshot;
        this.snapshotSha256 = snapshotSha256;
        this.implementationSha256 = implementationSha256;
        this.browser = browser;
        this.wait = wait;
        this.intervalMs = intervalMs;
        this.persist = persist;
        this.checkpointEvery = checkpointEvery;
        this.onProgress = onProgress;
        this.shouldCancel = shouldCancel;
        this.now = now;
        const breadthFirst = breadthFirstKeys(snapshot);
        const savedSelection = state && state.selection;
        const selection = auditSelection(snapshot, state && !scopeKey && !onlyKeys.length
            ? { scopeKey: savedSelection?.rootKey || '', onlyKeys: savedSelection?.onlyKeys || [] } : { scopeKey, onlyKeys });
        if (savedSelection && (savedSelection.kind !== selection.kind || savedSelection.rootKey !== selection.rootKey
            || JSON.stringify(savedSelection.onlyKeys) !== JSON.stringify(selection.onlyKeys))) throw new Error('The saved audit uses a different selection.');
        this.selection = { kind: selection.kind, rootKey: selection.rootKey, onlyKeys: selection.onlyKeys };
        this.selectedKeys = new Set(selection.keys);
        this.discoverOfficialCategoryRoute = discoverOfficialCategoryRoute;
        if (!Array.isArray(priorityKeys) || priorityKeys.some(key => !this.selectedKeys.has(key))) throw new Error('Invalid priority category.');
        this.keys = state ? state.queue : [...new Set([...priorityKeys, ...selection.keys])];
        if (!Array.isArray(this.keys) || this.keys.length !== selection.keys.length || new Set(this.keys).size !== selection.keys.length
            || this.keys.some(key => !this.selectedKeys.has(key))) throw new Error('Invalid saved category queue.');
        if (state && priorityKeys.some((key, index) => this.keys[index] !== key)) throw new Error('The saved audit uses a different priority order.');
        if (state && (state.version !== 3 || state.snapshotSha256 !== snapshotSha256 || state.implementationSha256 !== implementationSha256 || state.total !== this.keys.length
            || !Number.isSafeInteger(state.cursor) || state.cursor < 0 || state.cursor > this.keys.length)) throw new Error('The saved audit belongs to another snapshot.');
        this.state = state ? clone(state) : { version: 3, snapshotSha256, implementationSha256, mode: 'functional_destinations', selection: this.selection, queue: this.keys,
            total: this.keys.length, cursor: 0,
            startedAt: now(), updatedAt: now(), status: 'ready', physicalPageReads: 0, pageReuseCount: 0,
            nodeOutcomes: {}, observations: [], resolvedRoutes: [], parentLinks: [], cachedPages: [], dependencyFailures: {},
            groupSources: {}, observedGroupLinks: {}, documentContexts: {}, nodeAttempts: {}, fallbackCandidates: {}, exactItemScopes: {} };
        this.state.exactItemScopes ||= {};
        this.pageCache = new Map();
        for (const [url, page] of this.state.cachedPages || []) {
            if (safePublicUrl(url) && page && safePublicUrl(page.url)) this.pageCache.set(url, safePage(page));
        }
        this.controller = new AbortController();
        const children = Object.fromEntries(breadthFirst.map(key => [key, snapshot.children(key)]));
        this.lastReadAt = 0;
        const trackedBrowser = Object.create(browser);
        trackedBrowser.navigate = async (url, signal) => {
            await this.checkCancellation();
            const delay = Math.max(0, this.intervalMs - (Date.now() - this.lastReadAt));
            if (delay) await this.wait(delay);
            await this.checkCancellation();
            this.lastReadAt = Date.now();
            this.state.physicalPageReads += 1;
            if (this.currentNodeKey) {
                const attempt = this.attempt(this.currentNodeKey);
                attempt.networkNavigations += 1;
                if (this.discoveringRoute) attempt.fallbackNetworkNavigations += 1;
            }
            await this.progress('navigating');
            return browser.navigate(url, signal);
        };
        this.runner = new PesquisaMercadoRunner({ browser: trackedBrowser, pageScripts, collector: {}, taxonomy: { load: async () => snapshot }, wait: async ms => {
            await this.checkCancellation(); await wait(ms); await this.checkCancellation();
        },
            api: async () => { throw new Error('A public navigation audit cannot call the application backend.'); } });
        this.runner.controller = this.controller;
        this.runner.job = { id: 'public-navigation-audit', status: 'running', counters: {} };
        this.runner.checkpoint = { categories: [], scope: {}, officialTaxonomy: { children }, currentCategory: null };
        this.navigation = new OfficialCategoryNavigation({ ancestry: [], children, normalize: normalizeCategory,
            read: (url, purpose, key, expected) => this.read(url, purpose, key, expected) });
        this.runner.officialNavigation = this.navigation;
        const productionRead = this.runner.read.bind(this.runner);
        this.runner.read = async (url, purpose = 'category', ...args) => {
            if (this.currentNodeKey) {
                const attempt = this.attempt(this.currentNodeKey);
                if (purpose === 'category' && !this.discoveringRoute) attempt.finalDocumentReadAttempts += 1;
                if (purpose === 'category-transport') {
                    if (args[0] === this.currentNodeKey && !this.discoveringRoute) attempt.finalDocumentReadAttempts += 1;
                    else attempt.transportPageReadAttempts += 1;
                }
            }
            return productionRead(url, purpose, ...args);
        };
        const productionCategoryUrl = this.runner.officialCategoryUrl.bind(this.runner);
        this.runner.officialCategoryUrl = async node => {
            const dependency = snapshot.pathFromRoot(node.key).find(item => this.state.dependencyFailures[item.key]);
            if (dependency) {
                const error = new Error('The previously observed ancestor route remains unresolved.');
                error.code = this.state.dependencyFailures[dependency.key];
                error.sharedCategoryFailure = true; error.navigationDependencyKey = dependency.key;
                throw error;
            }
            try { return await productionCategoryUrl(node); }
            catch (error) {
                if (error.sharedCategoryFailure && this.snapshot.get(error.navigationDependencyKey)
                    && !['category_access_denied', 'login_required', 'audit_interrupted'].includes(this.classify(error))) {
                    this.state.dependencyFailures[error.navigationDependencyKey] = this.classify(error);
                }
                throw error;
            }
        };
        const productionDiscover = this.runner.discoverOfficialCategoryRoute.bind(this.runner);
        this.runner.discoverOfficialCategoryRoute = async node => {
            this.attempt(node.key).fallbackDiscoveryAttempts += 1;
            this.discoveringRoute = true;
            await this.progress('discovering_official_route');
            try {
                const discovered = await (this.discoverOfficialCategoryRoute || productionDiscover)(node);
                if (!discovered) return null;
                const candidate = this.discoveryCandidate(discovered, node);
                if (!candidate) {
                    const error = new Error('An official category route was not confirmed.'); error.code = 'official_route_not_found'; throw error;
                }
                this.state.fallbackCandidates[node.key] = candidate;
                if (!candidate.eligibleAsLeafDocument) {
                    const error = new Error('An item filter candidate alone cannot verify a category.'); error.code = 'item_filter_not_leaf_document'; throw error;
                }
                return { key: node.key, nome: node.nome, url: candidate.url, categoryBaseUrl: candidate.categoryBaseUrl, source: candidate.source };
            } finally { this.discoveringRoute = false; }
        };
        for (const root of snapshot.children()) this.navigation.records.set(root.key, root);
        for (const [key, url] of this.state.resolvedRoutes || []) {
            const node = snapshot.get(key);
            if (node && this.navigation.route({ ...node, url }, node)) this.navigation.routes.set(key, url);
        }
        for (const [key, links] of this.state.parentLinks || []) {
            if (snapshot.get(key) && Array.isArray(links)) this.navigation.links.set(key, links.map(item => normalizeCategory(item)).filter(Boolean));
        }
        this.cancelled = false;
        this.cancelReason = '';
        this.currentNodeKey = '';
        this.activeReadKey = '';
    }

    cancel(reason = 'audit_interrupted') {
        if (this.cancelled) return;
        this.cancelled = true; this.cancelReason = reason;
        this.controller.abort(); if (typeof this.browser.stop === 'function') this.browser.stop();
    }

    async checkCancellation() {
        if (!this.cancelled && await this.shouldCancel()) this.cancel('cancel_file');
        if (this.cancelled) { const error = new Error('Audit interrupted.'); error.name = 'AbortError'; throw error; }
    }

    async progress(stage) {
        await this.onProgress({ version: 1, snapshotSha256: this.snapshotSha256, status: this.state.status,
            cursor: this.state.cursor, total: this.keys.length, currentCategoryKey: this.currentNodeKey,
            snapshotTotalNodes: this.snapshot.summary.total, selectionKind: this.selection.kind, scopeRootKey: this.selection.rootKey,
            activeReadCategoryKey: this.activeReadKey, stage, physicalPageReads: this.state.physicalPageReads,
            cancellationRequested: this.cancelled, updatedAt: this.now() });
    }

    selectCategory(key) {
        const node = key && this.snapshot.get(key);
        this.runner.checkpoint.currentCategory = node || null;
        this.runner.checkpoint.scope = node ? { category: node } : {};
        this.runner.checkpoint.officialTaxonomy.ancestry = node ? this.snapshot.pathFromRoot(key) : [];
    }

    attempt(key) {
        if (!this.state.nodeAttempts[key]) this.state.nodeAttempts[key] = { primaryRouteAttempts: 0, fallbackDiscoveryAttempts: 0,
            finalDocumentReadAttempts: 0, transportPageReadAttempts: 0, networkNavigations: 0, fallbackNetworkNavigations: 0 };
        this.state.nodeAttempts[key].transportPageReadAttempts ||= 0;
        return this.state.nodeAttempts[key];
    }

    discoveryCandidate(value, node) {
        if (!value || typeof value !== 'object' || value.key !== node.key
            || !['official_category_href', 'official_category_template', 'verified_item_category_filter'].includes(value.source)) return null;
        const category = normalizeCategory({ key: value.key, nome: value.nome, url: value.url });
        const base = normalizeCategory({ key: value.key, nome: value.nome, url: value.categoryBaseUrl });
        if (!category || !base || unsupportedCategoryAlias(category.url) || unsupportedCategoryAlias(base.url)) return null;
        const url = new URL(category.url), baseUrl = new URL(base.url);
        if (value.source === 'verified_item_category_filter') {
            if (value.exactItemCategoryId !== node.key || baseUrl.search) return null;
            url.search = ''; url.hash = '';
            return { categoryKey: node.key, url: url.href, categoryBaseUrl: base.url, source: value.source,
                exactItemCategoryId: value.exactItemCategoryId, eligibleAsLeafDocument: false };
        }
        if (!this.navigation.route(category, node) || url.search || baseUrl.search || url.origin !== baseUrl.origin
            || categoryBasePath(url.pathname) !== categoryBasePath(baseUrl.pathname)) return null;
        return { categoryKey: node.key, url: category.url, categoryBaseUrl: base.url, source: value.source, eligibleAsLeafDocument: true };
    }

    itemScopeAncestry(scope) {
        return this.snapshot.pathFromRoot(scope.categoryKey).map(node => ({ ...node,
            url: node.key === scope.categoryKey ? scope.categoryBaseUrl : node.key === scope.transportCategoryId ? scope.transportBaseUrl : node.url }));
    }

    exactItemScopeProof(node, page, statusCode) {
        const scope = page.categoryItemScope;
        if (!scope || scope.categoryKey !== node.key || !(statusCode >= 200 && statusCode < 300)) return null;
        const ancestry = this.itemScopeAncestry(scope);
        if (!validateCategoryItemScope(scope, ancestry) || !categoryItemScopeUrlAllowed(page.url, scope)) return null;
        const verified = scopeCategoryItems(page, { categoryKey: node.key, documentCategoryId: scope.transportCategoryId,
            categoryBaseUrl: scope.categoryBaseUrl }, ancestry);
        if (!verified || !verified.anuncios.length || verified.anuncios.length !== page.anuncios.length) return null;
        return { categoryKey: node.key, source: 'live_ancestor_document_exact_item_categories', observedAt: this.now(),
            url: safePublicUrl(page.url), documentCategoryId: scope.transportCategoryId, statusCode,
            descriptor: clone(scope), verifiedExactItemCount: verified.anuncios.length,
            transportPageReadAttempts: this.attempt(node.key).transportPageReadAttempts,
            paginationCoverage: 'not_audited', hasNextTransportPage: !!(page.nextUrl || page.hasNext) };
    }

    async auditLeaf(node) {
        this.attempt(node.key).primaryRouteAttempts += 1;
        this.selectCategory(node.key);
        try {
            const observed = await this.runner.readCategory(node);
            this.selectCategory(node.key);
            const page = safePage(observed);
            const status = typeof this.browser.getNavigationStatus === 'function' ? this.browser.getNavigationStatus() : null;
            const statusCode = status && canonicalUrl(status.url) === canonicalUrl(page.url) ? status.statusCode : 0;
            if (observed.categoryItemScope) {
                const proof = this.exactItemScopeProof(node, observed, statusCode);
                if (!proof) { const error = new Error('Exact item-category scope was not confirmed.'); error.code = 'item_filter_not_leaf_document'; throw error; }
                this.state.exactItemScopes[node.key] = proof;
                // The source document retains the ancestor's identity. It is
                // never written as a leaf-document observation.
                this.recordOnlineContext(page, 'category-transport', proof.documentCategoryId, statusCode);
                this.observation(proof.documentCategoryId, page, statusCode);
                this.state.nodeOutcomes[node.key] = { outcome: 'production_read_accepted', routeSource: 'verified_exact_item_scope' };
                return;
            }
            const route = this.state.fallbackCandidates[node.key]?.categoryBaseUrl || this.navigation.routes.get(node.key) || node.url;
            const actual = new URL(page.url), base = new URL(route);
            if (!page.categoryDocumentIdentity || page.documentCategoryId !== node.key || page.categoryIdentityConflict || page.categoryNotFound
                || !(statusCode >= 200 && statusCode < 300) || actual.search || actual.origin !== base.origin
                || categoryBasePath(actual.pathname) !== categoryBasePath(base.pathname) || !(page.listingCount > 0 || page.empty)) {
                const error = new Error('The route did not prove a complete category document.'); error.code = 'discovered_document_unconfirmed'; throw error;
            }
            this.recordOnlineContext(page, 'category', node.key, statusCode);
            this.observation(node.key, page, statusCode);
            this.state.nodeOutcomes[node.key] = { outcome: 'production_read_accepted',
                routeSource: this.state.fallbackCandidates[node.key]?.source || 'official_category_navigation' };
        } finally { this.selectCategory(node.key); }
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

    recordOnlineContext(page, purpose, key, statusCode) {
        if (!(Number.isInteger(statusCode) && statusCode >= 200 && statusCode < 300)
            || page.categoryIdentityConflict || page.categoryNotFound) return;
        const observedAt = this.now();
        if (purpose === 'index') {
            for (const root of this.snapshot.children()) {
                let selected;
                try { selected = this.navigation.choose(page.categoryTree, root); } catch (_error) { continue; }
                const group = page.categoryTree.find(item => item.url === selected.url && this.navigation.route(item, root));
                if (!group) continue;
                this.state.groupSources[root.key] = { kind: 'live_category_directory', pageUrl: page.url,
                    parentUrl: selected.url, statusCode, observedAt };
                this.state.observedGroupLinks[root.key] = clone(group.children || []);
            }
            return;
        }
        if (!key || !this.snapshot.get(key)) return;
        if (purpose !== 'category-options' && page.documentCategoryId === key && page.categoryDocumentIdentity) {
            this.state.documentContexts[key] = { url: page.url, documentCategoryId: key, statusCode, observedAt };
            if (this.snapshot.children(key).length && purpose !== 'category-options') {
                this.state.groupSources[key] = { kind: 'live_category_filters', pageUrl: page.url,
                    parentUrl: page.url, statusCode, observedAt };
            }
        }
        if (purpose === 'category-options') {
            const source = this.state.groupSources[key];
            if (!source || !officialCategoryMoreUrl(page.url, source.parentUrl)) return;
            this.state.groupSources[key] = { ...source, kind: 'live_expanded_category_filters', expansionUrl: page.url, observedAt };
        }
        if (page.categories.length && this.state.groupSources[key]) this.state.observedGroupLinks[key] = clone(page.categories);
    }

    groupVerification(key) {
        const children = this.snapshot.children(key), source = this.state.groupSources[key];
        const links = this.state.observedGroupLinks[key] || this.navigation.links.get(key) || [];
        const parentUrl = source && source.parentUrl || this.navigation.routes.get(key) || '';
        const missingRouteKeys = [], unconfirmedIdentityKeys = [], matched = [];
        for (const child of children) {
            let selected;
            try { selected = this.navigation.choose(links, child, parentUrl); }
            catch (_error) { missingRouteKeys.push(child.key); continue; }
            matched.push(child.key);
            const document = this.state.documentContexts[child.key];
            if (selected.key !== child.key && !(document && verifiedCategoryPage(selected.url, document.url, child.key, document.documentCategoryId))) {
                unconfirmedIdentityKeys.push(child.key);
            }
        }
        return { categoryKey: key, expectedChildCount: children.length, observedChildRoutes: matched.length,
            confirmedChildIdentities: matched.length - unconfirmedIdentityKeys.length,
            source: source ? source.kind : 'not_observed_online', missingRouteKeys, unconfirmedIdentityKeys,
            verified: children.length > 0 && !!source && !missingRouteKeys.length && !unconfirmedIdentityKeys.length };
    }

    async auditGroup(key) {
        // A /c/ marketing page is not a product listing. Resolve only the
        // destinations needed by the same official child-navigation workflow.
        await this.navigation.resolve(key);
        let check = this.groupVerification(key);
        if (check.missingRouteKeys.length) {
            await this.navigation.resolve(check.missingRouteKeys[0]);
            check = this.groupVerification(key);
        }
        this.state.nodeOutcomes[key] = { outcome: 'group_routes_observed',
            ...(check.missingRouteKeys.length ? { code: 'category_link_missing' } : {}) };
    }

    async read(url, purpose = 'category', key = '', expected = null, recordEvidence = true) {
        await this.checkCancellation();
        const previousReadKey = this.activeReadKey;
        this.activeReadKey = key;
        const cached = this.pageCache.get(url);
        if (this.reusable(cached, url, purpose, key, expected)) {
            this.state.pageReuseCount += 1;
            this.activeReadKey = previousReadKey;
            return restoredPage(cached);
        }
        const checkpoint = this.runner.checkpoint;
        const previous = { currentCategory: checkpoint.currentCategory, scope: checkpoint.scope,
            ancestry: checkpoint.officialTaxonomy.ancestry,
            hadAncestry: Object.prototype.hasOwnProperty.call(checkpoint.officialTaxonomy, 'ancestry') };
        this.selectCategory(key);
        try {
            const observed = await this.runner.read(url, purpose, key, expected);
            const page = safePage(observed);
            const status = typeof this.browser.getNavigationStatus === 'function' ? this.browser.getNavigationStatus() : null;
            const statusCode = status && canonicalUrl(status.url) === canonicalUrl(page.url) ? status.statusCode : 0;
            if (recordEvidence && (purpose === 'index' || page.categories.length || key && this.snapshot.children(key).length)) this.pageCache.set(url, page);
            if (recordEvidence) this.recordOnlineContext(page, purpose, key, statusCode);
            // An expansion control page proves observed child links, not that
            // the corresponding category document displayed a listing.
            if (recordEvidence && purpose !== 'category-options') this.observation(key, page, statusCode);
            return observed;
        } catch (error) {
            const status = typeof this.browser.getNavigationStatus === 'function' ? this.browser.getNavigationStatus() : null;
            if (purpose !== 'category-options' && key && status && [403, 429].includes(status.statusCode)) {
                this.observation(key, { url: status.url, documentCategoryId: '', listingCount: 0, empty: false,
                    categoryNotFound: false, categoryIdentityConflict: false }, status.statusCode);
            }
            throw error;
        } finally {
            checkpoint.currentCategory = previous.currentCategory;
            checkpoint.scope = previous.scope;
            if (previous.hadAncestry) checkpoint.officialTaxonomy.ancestry = previous.ancestry;
            else delete checkpoint.officialTaxonomy.ancestry;
            this.activeReadKey = previousReadKey;
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
        await this.persist(clone(this.state), this.summary(), this.evidence(), this.functionalEvidence());
    }

    evidence() { return { version: 1, snapshotSha256: this.snapshotSha256, observations: clone(this.state.observations) }; }

    functionalEvidence() {
        return { version: 2, snapshotSha256: this.snapshotSha256, implementationSha256: this.implementationSha256,
            verificationMode: 'online_group_routes_leaf_documents_or_exact_item_scope', selection: this.selection,
            groups: this.keys.filter(key => this.snapshot.children(key).length).map(key => this.groupVerification(key)),
            exactItemScopes: this.keys.filter(key => this.state.exactItemScopes[key]).map(key => clone(this.state.exactItemScopes[key])) };
    }

    summary() {
        const outcomes = Object.values(this.state.nodeOutcomes);
        const groups = this.functionalEvidence().groups;
        const verifiedGroups = groups.filter(group => group.verified);
        const verifiedGroupKeys = new Set(verifiedGroups.map(group => group.categoryKey));
        const codes = {};
        for (const [key, item] of Object.entries(this.state.nodeOutcomes)) if (item.code && !verifiedGroupKeys.has(key)) codes[item.code] = (codes[item.code] || 0) + 1;
        const live = auditLiveEvidence(this.snapshot, this.snapshotSha256, this.evidence());
        const selectedObservations = live.outcomes.filter(item => this.selectedKeys.has(item.categoryKey));
        const selectedVerifiedDocuments = selectedObservations.filter(item => ['verified_nonempty', 'verified_empty'].includes(item.outcome));
        const verifiedLeafKeys = new Set(selectedVerifiedDocuments.filter(item => !this.snapshot.children(item.categoryKey).length).map(item => item.categoryKey));
        const verifiedExactItemScopeKeys = this.keys.filter(key => !this.snapshot.children(key).length && this.state.exactItemScopes[key]);
        const functionalVerifiedNodes = verifiedGroups.length + new Set([...verifiedLeafKeys, ...verifiedExactItemScopeKeys]).size;
        const observedOfficialEdges = groups.filter(group => group.source !== 'not_observed_online').reduce((sum, group) => sum + group.observedChildRoutes, 0);
        const attempts = Object.values(this.state.nodeAttempts);
        const completeSelectedCoverage = functionalVerifiedNodes === this.keys.length;
        const wholeSnapshotSelected = this.keys.length === this.snapshot.summary.total;
        return { version: 1, snapshotSha256: this.snapshotSha256, implementationSha256: this.implementationSha256, status: this.state.status, totalNodes: this.keys.length,
            snapshotTotalNodes: this.snapshot.summary.total, selection: this.selection,
            completedCursor: this.state.cursor, requestedNodes: outcomes.length,
            productionReadAcceptedNodes: outcomes.filter(item => item.outcome === 'production_read_accepted').length,
            dependencyUnresolvedNodes: outcomes.filter(item => item.outcome === 'dependency_unresolved').length,
            physicalPageReads: this.state.physicalPageReads, pageReuseCount: this.state.pageReuseCount,
            observedOfficialEdges, totalOfficialEdges: groups.reduce((sum, group) => sum + group.expectedChildCount, 0),
            snapshotOfficialEdges: this.snapshot.summary.total - this.snapshot.summary.rootCount,
            verifiedDocumentNodes: selectedVerifiedDocuments.length,
            verifiedNonempty: selectedVerifiedDocuments.filter(item => item.outcome === 'verified_nonempty').length,
            verifiedEmpty: selectedVerifiedDocuments.filter(item => item.outcome === 'verified_empty').length,
            verifiedOnlineGroupNodes: verifiedGroups.length, totalGroupNodes: groups.length,
            verifiedLeafDocumentNodes: verifiedLeafKeys.size, verifiedExactItemScopeNodes: verifiedExactItemScopeKeys.length,
            totalLeafNodes: this.keys.length - groups.length,
            functionalVerifiedNodes, functionalUnverifiedNodes: this.keys.length - functionalVerifiedNodes,
            observedDocumentNodes: selectedObservations.length, untestedDocumentNodes: this.keys.length - selectedObservations.length,
            snapshotObservedDocumentNodes: live.attemptedNodes,
            nodeSpecificOnlineAttempts: attempts.filter(item => item.networkNavigations > 0).length,
            finalLeafDocumentAttempts: attempts.filter(item => item.finalDocumentReadAttempts > 0).length,
            fallbackDiscoveryNodes: attempts.filter(item => item.fallbackDiscoveryAttempts > 0).length,
            fallbackDiscoveryAttempts: attempts.reduce((sum, item) => sum + item.fallbackDiscoveryAttempts, 0),
            transportPageReadAttempts: attempts.reduce((sum, item) => sum + (item.transportPageReadAttempts || 0), 0),
            filteredItemCandidates: Object.values(this.state.fallbackCandidates).filter(item => !item.eligibleAsLeafDocument).length,
            failureCodes: codes, updatedAt: this.state.updatedAt,
            completeSelectedCoverage, completeAllSnapshotCoverage: wholeSnapshotSelected && completeSelectedCoverage,
            completeLiveCoverage: wholeSnapshotSelected && completeSelectedCoverage,
            completeSelectedDocumentCoverage: selectedVerifiedDocuments.length === this.keys.length,
            completeDocumentCoverage: wholeSnapshotSelected && selectedVerifiedDocuments.length === this.keys.length,
            verificationMode: 'online_group_routes_leaf_documents_or_exact_item_scope',
            limitations: ['A completed traversal may still contain unresolved or unconfirmed category documents.',
                'Online group-route validation is reported separately from opening a category listing document.',
                'Exact item-category scope verifies a functional leaf using sampled ancestor pages; full pagination coverage is not audited.',
                'Frozen taxonomy or API records alone never count as online group or document verification.'] };
    }

    async run({ maxNodes = Infinity } = {}) {
        this.state.status = 'running';
        let visited = 0;
        await this.save();
        while (this.state.cursor < this.keys.length && visited < maxNodes) {
            if (!this.cancelled && await this.shouldCancel()) this.cancel('cancel_file');
            if (this.controller.signal.aborted) {
                this.state.status = this.cancelReason === 'cancel_file' ? 'paused_cancel_file' : 'paused_interrupted';
                await this.save();
                await this.progress('paused');
                return this.summary();
            }
            const key = this.keys[this.state.cursor], node = this.snapshot.get(key);
            this.currentNodeKey = key;
            this.activeReadKey = '';
            await this.progress('starting_node');
            try {
                this.selectCategory(key);
                if (this.snapshot.children(key).length) {
                    this.attempt(key).primaryRouteAttempts += 1;
                    await this.auditGroup(key);
                } else await this.auditLeaf(node);
                this.state.cursor += 1; visited += 1;
            } catch (error) {
                const code = this.classify(error);
                this.state.nodeOutcomes[key] = { outcome: 'unresolved', code };
                if (code === 'category_access_denied' || code === 'login_required' || code === 'audit_interrupted') {
                    this.state.status = code === 'category_access_denied' ? 'paused_access_denied' : code === 'login_required' ? 'paused_login'
                        : this.cancelReason === 'cancel_file' ? 'paused_cancel_file' : 'paused_interrupted';
                    await this.save();
                    await this.progress('paused');
                    return this.summary();
                }
                if (error.sharedCategoryFailure && this.snapshot.get(error.navigationDependencyKey)) {
                    this.state.dependencyFailures[error.navigationDependencyKey] = code;
                }
                this.state.cursor += 1; visited += 1;
            }
            await this.progress('node_completed');
            if (visited % this.checkpointEvery === 0) await this.save();
        }
        this.state.status = this.state.cursor === this.keys.length ? 'traversal_completed' : 'paused_limit';
        await this.save();
        await this.progress('finished');
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
        if (!['--target-ws', '--target-marker', '--taxonomy-file', '--output-dir', '--max-nodes', '--interval-ms', '--checkpoint-every', '--priority-keys', '--only-keys', '--scope-key', '--cancel-file'].includes(arg) || !argv[index + 1]) throw new Error('Invalid audit arguments.');
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
        '../electron_app/main/modules/pesquisa-mercado-category-discovery.js', '../electron_app/main/modules/pesquisa-mercado-category-item-scope.js',
        '../static/pesquisa-mercado/page-scripts.js', './audit_pesquisa_mercado_taxonomy_navigation.js', './pesquisa_mercado_live_navigation_audit.js']) {
        implementation.update(filename); implementation.update(await fs.readFile(path.resolve(__dirname, filename)));
    }
    const implementationSha256 = implementation.digest('hex');
    const snapshot = snapshotFromCache(JSON.parse(bytes.toString('utf8')));
    const priorityKeys = options['priority-keys'] ? [...new Set(options['priority-keys'].split(',').map(key => key.trim()).filter(Boolean))] : [];
    const onlyKeys = options['only-keys'] ? [...new Set(options['only-keys'].split(',').map(key => key.trim()).filter(Boolean))] : [];
    const scopeKey = options['scope-key'] || '';
    if (options['only-keys'] !== undefined && !onlyKeys.length) throw new Error('An explicit audit selection cannot be empty.');
    auditSelection(snapshot, { scopeKey, onlyKeys });
    if (priorityKeys.some(key => !/^MLB\d+$/.test(key) || !snapshot.get(key))) throw new Error('Invalid priority category.');
    const directory = path.resolve(options['output-dir']);
    await fs.mkdir(directory, { recursive: true });
    const statePath = path.join(directory, 'state.json'), summaryPath = path.join(directory, 'summary.json'), evidencePath = path.join(directory, 'evidence.json');
    const progressPath = path.join(directory, 'progress.json'), functionalPath = path.join(directory, 'functional-evidence.json');
    const cancelPath = path.resolve(options['cancel-file'] || path.join(directory, 'CANCEL'));
    const shouldCancel = async () => {
        try { await fs.access(cancelPath); return true; }
        catch (error) { if (error.code === 'ENOENT') return false; throw error; }
    };
    const state = options.resume ? JSON.parse(await fs.readFile(statePath, 'utf8')) : null;
    if (!options.resume) {
        try { await fs.access(statePath); throw new Error('Use --resume for an existing audit.'); }
        catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
    const lockPath = path.join(directory, 'audit.lock');
    const lock = await acquireAuditLock(lockPath, options.resume);
    let browser, audit, cancelTimer;
    try {
        // Imported only in CLI mode; unit fixtures never attach to a browser.
        const { createCdpAdapter } = require('./pesquisa_mercado_live_smoke');
        browser = await createCdpAdapter({ targetWs: options['target-ws'], marker: options['target-marker'] });
        audit = new LiveNavigationAudit({ snapshot, snapshotSha256, implementationSha256, browser, state, intervalMs, checkpointEvery,
            priorityKeys, onlyKeys, scopeKey, shouldCancel,
            onProgress: async progress => {
                await writeJsonAtomic(progressPath, progress);
                if (progress.stage === 'node_completed' || progress.stage === 'paused' || progress.stage === 'finished') process.stdout.write(JSON.stringify(progress) + '\n');
            },
            persist: async (nextState, summary, evidence, functional) => {
                await writeJsonAtomic(statePath, nextState);
                await writeJsonAtomic(evidencePath, evidence);
                await writeJsonAtomic(functionalPath, functional);
                await writeJsonAtomic(summaryPath, summary);
            } });
        cancelTimer = setInterval(() => { shouldCancel().then(cancel => { if (cancel) audit.cancel('cancel_file'); }).catch(() => audit.cancel()); }, 1000);
        cancelTimer.unref();
        const stop = () => audit.cancel();
        process.once('SIGINT', stop); process.once('SIGTERM', stop);
        try {
            const summary = await audit.run({ maxNodes });
            process.stdout.write(JSON.stringify(summary) + '\n');
            if (summary.status !== 'traversal_completed' || !summary.completeSelectedCoverage) process.exitCode = 2;
        } finally { process.removeListener('SIGINT', stop); process.removeListener('SIGTERM', stop); }
    } finally {
        clearInterval(cancelTimer);
        if (browser && typeof browser.detach === 'function') await browser.detach();
        await lock.close();
        await fs.unlink(lockPath).catch(() => {});
    }
}

module.exports = { LiveNavigationAudit, breadthFirstKeys, auditSelection, safePage, safePublicUrl, writeJsonAtomic };
if (require.main === module) main(process.argv.slice(2)).catch(() => {
    process.stderr.write('Live public navigation audit stopped. Review the saved summary and the explicitly marked development target.\n');
    process.exitCode = 1;
});
