'use strict';

// A page only observes this coordinator. Its lifetime belongs to Electron main.
const ACTIVE = new Set(['running', 'paused', 'waiting_login', 'failed']);
const ROOT = '/api/pesquisa-mercado/jobs';
const clone = value => JSON.parse(JSON.stringify(value));

function allowedUrl(value) {
    try {
        const url = new URL(String(value || ''));
        return url.protocol === 'https:' && !url.username && !url.password && (!url.port || url.port === '443')
            && /(^|\.)mercadolivre\.com\.br$/i.test(url.hostname);
    } catch (_err) { return false; }
}

function canonicalUrl(value) {
    if (!allowedUrl(value)) return '';
    const url = new URL(value);
    url.hash = '';
    for (const key of [...url.searchParams.keys()]) {
        if (/^(?:tracking|utm_|matt_|position|type|search_layout)/i.test(key)) url.searchParams.delete(key);
    }
    return url.href;
}

function normalizeCategory(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    if (Object.keys(value).some(key => !['key', 'nome', 'url'].includes(key))) return null;
    const url = canonicalUrl(value.url);
    if (!url) return null;
    const parsed = new URL(url);
    const listingHost = ['lista.mercadolivre.com.br', 'imoveis.mercadolivre.com.br', 'servicos.mercadolivre.com.br'].includes(parsed.hostname);
    if (!listingHost && !['www.mercadolivre.com.br', 'mercadolivre.com.br'].includes(parsed.hostname)) return null;
    let path;
    try { path = decodeURIComponent(parsed.pathname); } catch (_error) { return null; }
    if (/\/(?:p|up|perfil|loja|stores?|vendedor)(?:\/|$)|\/MLB-\d+|_CustId_|_SellerId_/i.test(path)) return null;
    if ([...parsed.searchParams.keys()].some(key => /^(?:seller|sellerid|custid|customerid|user|userid|wid|itemid|productid|catalogproductid)$/i.test(key.replace(/[_-]/g, '')) || /token|password|senha|secret|authorization|api[_-]?key|cookie/i.test(key))) return null;
    if ([...parsed.searchParams.values()].some(value => /_CustId_|_SellerId_|(?:^|[|,;])(?:item_id|catalog_product_id|seller[_-]?id|cust[_-]?id):/i.test(value))) return null;
    if (listingHost ? !path.replace(/\//g, '') : !/^\/(?:c|categorias)\/[^/]+/i.test(parsed.pathname)) return null;
    const key = String(value.key || '').trim();
    const nome = String(value.nome || '').replace(/\s+/g, ' ').trim();
    // Keep the pathname discovered in navigation: ML can interpret a listing
    // URL without its trailing slash as a generic search outside that category.
    return key && key.length <= 500 && nome && nome.length <= 400 && url.length <= 4000 ? { key, nome, url: parsed.href } : null;
}

function sameCategoryPage(expected, actual) {
    if (!allowedUrl(expected) || !allowedUrl(actual)) return false;
    const identity = value => {
        const parsed = new URL(value);
        const path = decodeURIComponent(parsed.pathname)
            .replace(/(?:\/)?_(?:Desde_\d+|NoIndex_(?:True|False)|OrderId_[A-Za-z0-9]+|DisplayType_[A-Za-z0-9]+)/gi, '')
            .replace(/\/+$/, '').toLowerCase();
        const categories = [...parsed.searchParams.entries()].filter(([key]) => /^(?:category|category_id|categoryid|categories)$/i.test(key))
            .map(([key, value]) => [key.toLowerCase(), value]).sort();
        return JSON.stringify([parsed.hostname.replace(/^www\./, ''), path, categories]);
    };
    try { return identity(expected) === identity(actual); } catch (_error) { return false; }
}

// Navigation may still expose the previous document or a loading shell. Never
// consume that tree, and give the requested document time to replace it.
function categoryPageWaiter(expected) {
    let lastUrl = '', stableReads = 0;
    return {
        actualUrl: '', mismatch: false,
        observe(actualUrl, ready, loading) {
            this.actualUrl = actualUrl;
            this.mismatch = !!expected && !sameCategoryPage(expected, actualUrl);
            if (this.mismatch || !ready || loading) {
                lastUrl = ''; stableReads = 0;
                return false;
            }
            const current = canonicalUrl(actualUrl);
            if (!current) return false;
            stableReads = lastUrl === current ? stableReads + 1 : 1;
            lastUrl = current;
            return stableReads >= 2;
        }
    };
}

function currentPageRequiresLogin(page, browser) {
    if (!page.authRequired && !page.avantAuthRequired) return false;
    if (page.readyState === 'loading' || typeof browser.isLoading === 'function' && browser.isLoading()) return false;
    // A login redirect is allowed, but its document must be the one currently
    // displayed. Avant pages have no ML URL in the parser and use getURL().
    if (!page.url) {
        try {
            const current = new URL(browser.getURL());
            return current.protocol === 'https:' && !current.username && !current.password && (!current.port || current.port === '443')
                && (current.hostname === 'avantprocloud.com.br' || current.hostname.endsWith('.avantprocloud.com.br'));
        } catch (_error) { return false; }
    }
    const documentUrl = value => {
        if (!allowedUrl(value)) return '';
        const parsed = new URL(value);
        // Match the parser's URL cleanup without removing pagination. The
        // previous page belongs to the same category but is another document.
        const fragment = parsed.hash.replace(/^#/, '');
        let wid = new URLSearchParams(fragment).get('wid');
        if (!wid) {
            try { wid = new URLSearchParams(decodeURIComponent(fragment)).get('wid'); } catch (_error) {}
        }
        if (!parsed.searchParams.has('wid') && /^MLB\d{6,}$/i.test(wid || '')) parsed.searchParams.set('wid', wid.toUpperCase());
        for (const key of ['polycard_client', 'sid', 'reco_id', 'searchVariation']) parsed.searchParams.delete(key);
        const normalized = new URL(canonicalUrl(parsed.href));
        normalized.searchParams.sort();
        return normalized.href;
    };
    const observed = documentUrl(page.url);
    return !!observed && observed === documentUrl(browser.getURL());
}

function sellerFromPage(page) {
    const profile = canonicalUrl(page.perfilUrl || page.sellerUrl);
    const match = profile.match(/(?:_CustId_|[?&]seller_id=)(\d+)/i);
    const id = String(page.sellerId || page.seller_id || match && match[1] || '').trim();
    const key = id ? `id:${id}` : profile ? `perfil:${profile}` : '';
    return key ? { key, id, nome: String(page.vendedor || page.sellerName || ''), perfilUrl: profile } : null;
}

function initialCheckpoint(saved) {
    return {
        version: 1, phase: 'categories', categories: [], categoryIndex: 0,
        currentCategory: null, page: null, categorySeenPages: [], seenSeedIds: [],
        seenSellerKeys: [], incompleteCategories: [], discoveryInitialized: false,
        ...clone(saved || {})
    };
}

class PesquisaMercadoRunner {
    constructor(options) {
        this.api = options.api;
        this.browser = options.browser;
        this.scripts = options.pageScripts;
        this.collector = options.collector;
        this.emit = options.emit || (() => {});
        this.wait = options.wait || (ms => new Promise(resolve => setTimeout(resolve, ms)));
        this.job = null;
        this.checkpoint = null;
        this.task = null;
        this.controller = null;
        this.stopStatus = '';
        this.saveQueue = Promise.resolve();
    }

    snapshot() {
        if (!this.job) return { job: null };
        // No authentication context or raw checkpoint is sent as a progress event.
        const { id, status, revision, counters, message, created_at, updated_at } = this.job;
        return { job: { id, status, revision, counters, message, created_at, updated_at,
            scopeCategory: this.checkpoint && this.checkpoint.scope && this.checkpoint.scope.category || null,
            currentCategory: this.checkpoint && this.checkpoint.currentCategory || null } };
    }

    async start(category) {
        const selected = category === undefined ? null : normalizeCategory(category);
        if (category !== undefined && !selected) throw new Error('Escolha uma categoria ou subcategoria valida.');
        const response = await this.api('POST', ROOT, selected ? { category: selected } : {});
        if (selected) {
            const existing = normalizeCategory(response.job && response.job.checkpoint && response.job.checkpoint.scope && response.job.checkpoint.scope.category);
            if (!existing || existing.key !== selected.key || existing.url !== selected.url) {
                throw new Error('Existe uma pesquisa ativa com outro escopo. Retome ou cancele essa pesquisa antes de iniciar a categoria escolhida.');
            }
            if (['paused', 'waiting_login'].includes(response.job.status)) return response;
        }
        return this.attach(response.job);
    }

    attach(job) {
        if (!job || !job.id) throw new Error('A pesquisa nao retornou uma execucao valida.');
        if (this.task) {
            if (this.job.id !== job.id) throw new Error('Outra pesquisa ja esta em execucao neste aplicativo.');
            return this.snapshot();
        }
        this.job = clone(job);
        this.job.counters = { categories: 0, pages: 0, sellers: 0, ads: 0, pending: 0, ...(job.counters || {}) };
        this.checkpoint = initialCheckpoint(job.checkpoint);
        this.seenSeeds = new Set(this.checkpoint.seenSeedIds);
        this.seenSellers = new Set(this.checkpoint.seenSellerKeys);
        this.stopStatus = '';
        this.controller = new AbortController();
        this.job.status = 'running';
        this.task = this.run().finally(() => { this.task = null; });
        // Caller receives an immediate response; failures are represented in job status.
        return this.snapshot();
    }

    async status(jobId = '') {
        const response = await this.api('GET', jobId ? `${ROOT}/${encodeURIComponent(jobId)}` : `${ROOT}/latest`);
        if (this.task && this.job && response.job && this.job.id === response.job.id) return this.snapshot();
        if (response.job && response.job.status === 'running' && !this.task) {
            return this.api('PATCH', `${ROOT}/${encodeURIComponent(response.job.id)}/checkpoint`, {
                checkpoint: response.job.checkpoint, counters: response.job.counters, status: 'paused',
                message: 'Pesquisa recuperada. Clique em Retomar para continuar.'
            });
        }
        return response;
    }

    async resume(jobId) {
        if (this.task) {
            if (this.job.id !== jobId) throw new Error('Outra pesquisa ja esta em execucao.');
            if (!this.stopStatus) return this.snapshot();
            await this.task;
        }
        const response = await this.api('GET', `${ROOT}/${encodeURIComponent(jobId)}`);
        if (!response.job || !ACTIVE.has(response.job.status)) throw new Error('Esta pesquisa ja foi encerrada.');
        return this.attach(response.job);
    }

    async stop(status = 'paused') {
        if (!this.job) return this.snapshot();
        if (!ACTIVE.has(this.job.status) && !this.task) return this.snapshot();
        this.stopStatus = status;
        this.job.status = status;
        if (this.controller) this.controller.abort();
        if (typeof this.browser.stop === 'function') this.browser.stop();
        this.job.message = status === 'cancelled' ? 'Pesquisa cancelada; resultados preservados.' : 'Pesquisa pausada; progresso salvo.';
        await this.save();
        return this.snapshot();
    }

    async save() {
        const checkpoint = clone(this.checkpoint);
        const status = this.stopStatus || this.job.status;
        const counters = clone(this.job.counters || {});
        const message = this.job.message || '';
        this.saveQueue = this.saveQueue.catch(() => {}).then(async () => {
            const response = await this.api('PATCH', `${ROOT}/${encodeURIComponent(this.job.id)}/checkpoint`, {
                checkpoint, status: this.stopStatus || status, counters, message
            });
            this.job = { ...this.job, ...response.job, status: this.stopStatus || response.job.status };
            if (checkpoint.discoveredSeed && this.checkpoint.discoveredSeed && checkpoint.discoveredSeed.key === this.checkpoint.discoveredSeed.key) {
                delete this.checkpoint.discoveredSeed;
            }
            this.emit(this.snapshot());
        });
        return this.saveQueue;
    }

    check() {
        if (this.controller.signal.aborted) {
            const err = new Error('Pesquisa interrompida.');
            err.name = 'AbortError';
            throw err;
        }
    }

    async attempt(operation) {
        let last;
        for (let index = 0; index < 3; index += 1) {
            this.check();
            try {
                const result = await operation();
                this.check();
                if (result && result.authRequired) {
                    const err = new Error(result.reason || 'Conclua o login do Mercado Livre ou Avant e clique em Retomar.');
                    err.waitingLogin = true;
                    throw err;
                }
                return result;
            } catch (err) {
                if (err.name === 'AbortError' || err.waitingLogin || err.categoryScopeMismatch || err.status === 401 || err.status === 403) throw err;
                last = err;
                if (index < 2) await this.wait(600 * (index + 1));
            }
        }
        throw last;
    }

    async read(url, purpose = 'category') {
        if (!allowedUrl(url)) throw new Error('Link de pesquisa fora do Mercado Livre.');
        return this.attempt(async () => {
            this.check();
            await this.browser.navigate(url, this.controller.signal);
            let page;
            const destination = categoryPageWaiter(this.checkpoint && this.checkpoint.scope && this.checkpoint.scope.category ? url : null);
            for (let poll = 0; poll < 34; poll += 1) {
                await this.wait(450);
                this.check();
                page = await this.browser.executeJavaScript(this.scripts.discoveryScript());
                if (page.authRequired || page.avantAuthRequired) {
                    if (currentPageRequiresLogin(page, this.browser)) return { ...page, authRequired: true };
                    if (purpose === 'category') destination.observe(page.url || this.browser.getURL(), false, true);
                    continue;
                }
                const listings = (page.anuncios || []).length > 0;
                const ready = purpose === 'seller' ? !!page.allAdsUrl
                    : purpose === 'item' ? !!sellerFromPage(page)
                    : purpose === 'first-page' ? true // The collector owns settling and scrolling this page.
                    : listings || (page.categories || []).length > 0 || page.listingUrl || (purpose === 'index' && page.categoriesIndexUrl) || page.empty;
                if (purpose === 'category') {
                    const loading = page.readyState === 'loading' || typeof this.browser.isLoading === 'function' && this.browser.isLoading();
                    if (destination.observe(page.url || this.browser.getURL(), ready, loading)) return page;
                } else if (ready) return page;
            }
            if (purpose === 'category' && destination.mismatch) this.assertCategoryDestination(url, destination.actualUrl);
            if (purpose === 'index' && !page.authRequired && !page.avantAuthRequired) return page; // The homepage can link to the full category index instead.
            throw new Error('A pagina ainda nao apresentou os dados para continuar a pesquisa.');
        });
    }

    enqueueCategories(categories) {
        const known = new Set(this.checkpoint.categories.map(item => item.key));
        for (const raw of categories || []) {
            const url = canonicalUrl(raw.url);
            const key = String(raw.key || url);
            if (!url || !key || known.has(key)) continue;
            known.add(key);
            this.checkpoint.categories.push({ key, nome: String(raw.nome || raw.name || key), url });
        }
    }

    assertCategoryDestination(expected, actual) {
        if (this.checkpoint && this.checkpoint.scope && this.checkpoint.scope.category && !sameCategoryPage(expected, actual)) {
            const error = new Error('O Mercado Livre abriu outra categoria. O escopo selecionado foi preservado.');
            error.categoryScopeMismatch = true;
            throw error;
        }
    }

    async readCategory(category) {
        // Category hubs contain promotional carousels. The navigation index is
        // the canonical relationship between a hub and its actual categories.
        if (/^\/c\//.test(new URL(category.url).pathname)) {
            if (!Array.isArray(this.checkpoint.categoryTree)) {
                const index = await this.read('https://www.mercadolivre.com.br/categorias', 'index');
                this.checkpoint.categoryTree = Array.isArray(index.categoryTree) ? index.categoryTree : [];
                await this.save();
            }
            const group = this.checkpoint.categoryTree.find(item => canonicalUrl(item.url) === canonicalUrl(category.url));
            if (group && Array.isArray(group.children) && group.children.length) {
                return { url: category.url, categories: group.children, hasChildren: true };
            }
        }
        return this.read(category.url);
    }

    async nextCategoryPage(current) {
        const next = canonicalUrl(current.nextUrl);
        if (next) {
            this.assertCategoryDestination(current.url, next);
            return this.read(next);
        }
        if (!current.hasNext) return null;
        if (typeof this.scripts.nextCategoryPageScript !== 'function') throw new Error('Controle de paginacao indisponivel.');
        // Visiting a seller moves the browser away from discovery. Restore the
        // saved category page before using its own JavaScript pagination control.
        return this.attempt(async () => {
            await this.read(current.url);
            const click = await this.browser.executeJavaScript(this.scripts.nextCategoryPageScript(current.url));
            if (!click || !click.clicked) {
                if (click && click.hasNext === false && ['last_page', 'no_next_page'].includes(click.reason)) return null;
                throw new Error('O controle da proxima pagina da categoria nao respondeu.');
            }
            let page;
            const destination = categoryPageWaiter(this.checkpoint && this.checkpoint.scope && this.checkpoint.scope.category ? current.url : null);
            for (let poll = 0; poll < 34; poll += 1) {
                this.check();
                await this.wait(450);
                page = await this.browser.executeJavaScript(this.scripts.discoveryScript());
                this.check();
                if (page.authRequired || page.avantAuthRequired) {
                    if (currentPageRequiresLogin(page, this.browser)) return { ...page, authRequired: true };
                    destination.observe(page.url || this.browser.getURL(), false, true);
                    continue;
                }
                const ready = canonicalUrl(page.url) !== canonicalUrl(current.url) && ((page.anuncios || []).length || page.empty);
                const loading = page.readyState === 'loading' || typeof this.browser.isLoading === 'function' && this.browser.isLoading();
                if (destination.observe(page.url || this.browser.getURL(), ready, loading)) return page;
            }
            if (destination.mismatch) this.assertCategoryDestination(current.url, destination.actualUrl);
            throw new Error('A proxima pagina da categoria nao terminou de carregar.');
        });
    }

    incomplete(message) {
        const category = this.checkpoint.currentCategory;
        if (category && !this.checkpoint.incompleteCategories.some(item => item.key === category.key)) {
            this.checkpoint.incompleteCategories.push({ ...category, motivo: String(message || 'Coleta incompleta.') });
        }
        this.job.message = message;
    }

    async publishSeller(seller, result) {
        const category = this.checkpoint.currentCategory;
        const anuncios = (result.anuncios || []).map((item, index) => ({
            ...item, seller_key: seller.key, vendedor: item.vendedor || seller.nome,
            categoria: category && category.nome || '',
            categoria_key: category && category.key || '',
            ordem_coleta: Number(this.job.counters && this.job.counters.ads || 0) + index
        }));
        const response = await this.api('PUT', `${ROOT}/${encodeURIComponent(this.job.id)}/vendedores/${encodeURIComponent(seller.key)}`, {
            seller: { ...seller, categoria: category && category.nome || '' }, anuncios
        });
        this.job = { ...this.job, ...response.job };
        this.rememberSeller(seller.key);
        if (result.incomplete) this.incomplete('Alguns anuncios ficaram sem dados suficientes do Avant.');
        this.emit(this.snapshot());
    }

    rememberSeller(key) {
        if (!this.seenSellers.has(key)) {
            this.seenSellers.add(key);
            this.checkpoint.seenSellerKeys.push(key);
        }
    }

    async sellerPublished(key) {
        if (this.seenSellers.has(key)) return true;
        const result = await this.api('GET', `${ROOT}/${encodeURIComponent(this.job.id)}/vendedores?seller_key=${encodeURIComponent(key)}`);
        const found = (result.items || []).some(item => item.seller_key === key);
        if (found) this.rememberSeller(key);
        return found;
    }

    async processSeed(seed) {
        const key = String(seed.id || canonicalUrl(seed.url));
        if (!key || this.seenSeeds.has(key)) return;
        const history = await this.api('GET', `${ROOT}/${encodeURIComponent(this.job.id)}/descoberta?seed_key=${encodeURIComponent(key)}`);
        if (history.seen) { this.seenSeeds.add(key); return; }
        const page = await this.read(seed.url, 'item');
        let seller = sellerFromPage(page);
        if (!seller) throw new Error('Nao foi possivel confirmar o perfil do vendedor deste anuncio.');
        const discoveredKey = seller.key;
        if (!(await this.sellerPublished(seller.key))) {
            this.job.message = `Consultando a primeira pagina de ${seller.nome || seller.id || 'vendedor'}...`;
            this.checkpoint.phase = 'seller';
            await this.save();
            const profile = await this.read(seller.perfilUrl, 'seller');
            const confirmed = sellerFromPage(profile);
            if (seller.id && confirmed && confirmed.id && seller.id !== confirmed.id) throw new Error('O perfil retornou um vendedor diferente do anuncio consultado.');
            if (confirmed && confirmed.id) seller = { ...seller, ...confirmed, nome: confirmed.nome || seller.nome };
            const allAdsUrl = canonicalUrl(profile.allAdsUrl || page.allAdsUrl);
            if (!allAdsUrl) throw new Error('A opcao ver todos os anuncios deste vendedor nao foi encontrada.');
            const allAdsPage = await this.read(allAdsUrl, 'first-page');
            const listingSeller = sellerFromPage(allAdsPage);
            if (seller.id && listingSeller && listingSeller.id && seller.id !== listingSeller.id) throw new Error('A listagem retornou anuncios de outro vendedor.');
            if (!seller.id && listingSeller && listingSeller.id) seller = { ...seller, ...listingSeller, nome: listingSeller.nome || seller.nome };
            if (!(await this.sellerPublished(seller.key))) {
                const result = await this.attempt(() => this.collector.collectFirstPage({
                    browser: this.browser, signal: this.controller.signal, seller: {
                        ...seller, sellerId: seller.id, vendedor: seller.nome
                    }, wait: this.wait,
                    onProgress: progress => {
                        if (this.stopStatus) return;
                        this.job.message = `Coletando ${seller.nome || seller.id}: ${Number(progress.coletados || progress.total || 0)} anuncio(s).`;
                        this.emit(this.snapshot());
                    }
                }));
                this.check();
                await this.publishSeller(seller, result);
            }
            this.rememberSeller(discoveredKey);
        }
        this.checkpoint.seenSeedIds.push(key);
        this.seenSeeds.add(key);
        this.checkpoint.discoveredSeed = { key, id: seed.id || '', url: seed.url,
            category_key: this.checkpoint.currentCategory.key, seller_key: seller.key, status: 'consulted' };
        this.checkpoint.phase = 'discovery';
    }

    async run() {
        try {
            if (typeof this.browser.prepare === 'function') await this.browser.prepare();
            this.check();
            await this.save();
            if (!this.checkpoint.discoveryInitialized) {
                const root = await this.read('https://www.mercadolivre.com.br/', 'index');
                this.enqueueCategories(root.categories);
                if (!this.checkpoint.categories.length) {
                    const index = await this.read(canonicalUrl(root.categoriesIndexUrl) || 'https://www.mercadolivre.com.br/categorias', 'index');
                    this.enqueueCategories(index.categories);
                }
                if (!this.checkpoint.categories.length) throw new Error('Nenhuma categoria foi localizada na navegacao do Mercado Livre.');
                this.checkpoint.discoveryInitialized = true;
                await this.save();
            }
            while (this.checkpoint.categoryIndex < this.checkpoint.categories.length) {
                this.check();
                const category = this.checkpoint.categories[this.checkpoint.categoryIndex];
                this.checkpoint.currentCategory = category;
                if (!this.checkpoint.page) {
                    this.job.message = `Descobrindo vendedores em ${category.nome}...`;
                    try {
                        let page = await this.readCategory(category);
                        this.enqueueCategories(page.categories);
                        if (page.hasChildren && page.categories && page.categories.length) {
                            this.checkpoint.categoryIndex += 1;
                            this.job.counters.categories = Number(this.job.counters.categories || 0) + 1;
                            await this.save();
                            continue;
                        }
                        if (page.listingUrl && canonicalUrl(page.listingUrl) !== canonicalUrl(page.url || category.url)) {
                            this.assertCategoryDestination(page.url || category.url, page.listingUrl);
                            page = await this.read(canonicalUrl(page.listingUrl));
                        }
                        this.checkpoint.page = {
                            url: canonicalUrl(page.url || category.url), nextUrl: canonicalUrl(page.nextUrl),
                            hasNext: !!page.hasNext, anuncios: page.anuncios || [], index: 0
                        };
                        this.checkpoint.categorySeenPages = [this.checkpoint.page.url];
                        this.job.counters.pages = Number(this.job.counters.pages || 0) + 1;
                        await this.save();
                    } catch (err) {
                        if (err.waitingLogin || err.name === 'AbortError' || err.status === 401 || err.status === 403) throw err;
                        this.incomplete(`Categoria indisponivel: ${category.nome}.`);
                        await this.finishCategory();
                        continue;
                    }
                }
                while (this.checkpoint.page) {
                    const current = this.checkpoint.page;
                    while (current.index < current.anuncios.length) {
                        this.check();
                        const seed = current.anuncios[current.index];
                        try { await this.processSeed(seed); }
                        catch (err) {
                            if (err.waitingLogin || err.name === 'AbortError' || err.status === 401 || err.status === 403) throw err;
                            this.incomplete('Um anuncio ou vendedor nao pode ser consultado; a pesquisa continuara.');
                            this.checkpoint.discoveredSeed = { key: String(seed.id || canonicalUrl(seed.url)),
                                id: seed.id || '', url: seed.url, category_key: category.key, status: 'failed' };
                        }
                        current.index += 1;
                        this.checkpoint.phase = 'discovery';
                        await this.save();
                    }
                    const next = canonicalUrl(current.nextUrl);
                    if (!next && !current.hasNext) break;
                    if (next && this.checkpoint.categorySeenPages.includes(next)) {
                        this.incomplete('A categoria retornou uma pagina repetida.');
                        break;
                    }
                    try {
                        const page = await this.nextCategoryPage(current);
                        if (!page) break;
                        const loaded = canonicalUrl(page.url || next);
                        if (this.checkpoint.categorySeenPages.includes(loaded)) {
                            this.incomplete('O Mercado Livre repetiu a pagina da categoria.');
                            break;
                        }
                        this.checkpoint.categorySeenPages.push(loaded);
                        this.checkpoint.page = { url: loaded, nextUrl: canonicalUrl(page.nextUrl), hasNext: !!page.hasNext, anuncios: page.anuncios || [], index: 0 };
                        this.job.counters.pages = Number(this.job.counters.pages || 0) + 1;
                        await this.save();
                    } catch (err) {
                        if (err.waitingLogin || err.name === 'AbortError' || err.status === 401 || err.status === 403) throw err;
                        this.incomplete('Nao foi possivel abrir a proxima pagina da categoria.');
                        break;
                    }
                }
                await this.finishCategory();
            }
            this.job.status = this.checkpoint.incompleteCategories.length ? 'completed_partial' : 'completed';
            this.job.message = this.job.status === 'completed' ? 'Pesquisa concluida.' : 'Pesquisa concluida com pendencias; consulte as categorias incompletas.';
            await this.save();
        } catch (err) {
            this.job.status = this.stopStatus || (err.waitingLogin || err.status === 401 || err.status === 403 ? 'waiting_login' : 'failed');
            this.job.message = this.stopStatus ? this.job.message : err.waitingLogin ? err.message :
                this.job.status === 'waiting_login' ? 'Atualize o login e clique em Retomar.' : 'Pesquisa interrompida; o progresso foi preservado.';
            try { await this.save(); } catch (_saveError) { this.emit(this.snapshot()); }
        } finally {
            if (typeof this.browser.finish === 'function') await this.browser.finish(this.job.status).catch(() => {});
        }
    }

    async finishCategory() {
        this.checkpoint.categoryIndex += 1;
        this.checkpoint.page = null;
        this.checkpoint.categorySeenPages = [];
        this.checkpoint.seenSeedIds = [];
        this.seenSeeds.clear();
        this.job.counters.categories = Number(this.job.counters.categories || 0) + 1;
        await this.save();
    }
}

module.exports = { PesquisaMercadoRunner, allowedUrl, canonicalUrl, sellerFromPage, normalizeCategory, sameCategoryPage, categoryPageWaiter, currentPageRequiresLogin };
