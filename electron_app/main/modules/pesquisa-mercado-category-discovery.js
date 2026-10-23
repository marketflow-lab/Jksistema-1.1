'use strict';

const INDEX_URL = 'https://www.mercadolivre.com.br/categorias';
const SEARCH_ACTION = 'https://www.mercadolivre.com.br/jm/search';
const ID = /^MLB\d+$/;
const label = value => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/\s+/g, ' ').trim().toLowerCase();
const navigationContextChanged = error => /execution context was destroyed|cannot find context with specified id|context.*(?:destroyed|invalidated)/i.test(String(error && error.message || ''));

function publicUrl(value) {
    try {
        const url = new URL(value);
        if (url.protocol !== 'https:' || !/(^|\.)mercadolivre\.com\.br$/i.test(url.hostname)
            || url.username || url.password || url.port && url.port !== '443'
            || [...url.searchParams.keys()].some(key => /token|password|senha|secret|authorization|api[_-]?key|cookie/i.test(key))) return '';
        url.hash = '';
        for (const key of ['tracking_id', 'position', 'polycard_client', 'sid', 'reco_id', 'searchVariation']) url.searchParams.delete(key);
        url.searchParams.sort();
        return url.href;
    } catch (_error) { return ''; }
}

function itemUrl(value, expectedId) {
    const normalized = publicUrl(value);
    if (!normalized) return '';
    const url = new URL(normalized), ids = new Set();
    if (!/\/MLB-\d+(?:-|\/|$)|\/(?:p|up)\/MLBU?\d+(?:\/|$)/i.test(url.pathname)) return '';
    for (const match of url.pathname.matchAll(/\/(MLB-\d+)(?=-|\/|$)/g)) ids.add(match[1].replace('-', ''));
    for (const key of ['wid', 'item_id']) for (const id of url.searchParams.getAll(key)) {
        if (!ID.test(id)) return '';
        ids.add(id);
    }
    if (ids.size > 1 || expectedId && ids.size && !ids.has(expectedId)) return '';
    return { url: normalized, id: ids.size === 1 ? [...ids][0] : '' };
}

function submitObservedSearch(term, action) {
    const forms = [...document.forms].filter(form => form.action === action && form.method.toLowerCase() === 'get');
    const matches = forms.map(form => ({ form, input: form.querySelector('input[name="as_word"]') }))
        .filter(({ form, input }) => input && !input.disabled && ['text', 'search'].includes(input.type)
            && typeof form.requestSubmit === 'function'
            && ![...form.elements].some(element => element.name && element.name !== 'as_word'
                && !element.disabled && !['submit', 'button'].includes(element.type)
                && !(element.type === 'radio' && (!element.checked || element.name === 'navigations-options'))));
    if (matches.length !== 1) return false;
    const { form, input } = matches[0];
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, term);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    form.requestSubmit();
    return true;
}

function publicBreadcrumbs() {
    let containers = [...document.querySelectorAll('.andes-breadcrumb')];
    if (!containers.length) containers = [...document.querySelectorAll('.ui-pdp-breadcrumb')];
    containers = containers.filter(node => !containers.some(other => other !== node && other.contains(node)));
    return {
        url: location.href,
        canonical: document.querySelector('link[rel="canonical"]')?.href || '',
        groups: containers.slice(0, 4).map(container => [...container.querySelectorAll('a[href]')].slice(0, 40)
            .map(anchor => ({ nome: (anchor.textContent || '').replace(/\s+/g, ' ').trim(), url: anchor.href })))
    };
}

function categoryUrl(value) {
    const normalized = publicUrl(value);
    if (!normalized) return '';
    const url = new URL(normalized);
    let path;
    try { path = decodeURIComponent(url.pathname); } catch (_error) { return ''; }
    if (url.hostname !== 'lista.mercadolivre.com.br' || url.search || path === '/'
        || /\*|\$query|(?:^|\/)_[^/]+|_(?:Desde|NoIndex|OrderId|DisplayType|CustId|SellerId)_|\/MLB-\d+|\/(?:p|up|perfil|loja)(?:\/|$)/i.test(path)) return '';
    return normalized;
}

function routeFromQueryFilter(page, currentUrl, expected) {
    const normalized = publicUrl(currentUrl);
    if (!normalized) return { route: null, ambiguous: false };
    const source = new URL(normalized), query = [...source.searchParams.entries()], match = source.pathname.match(/^\/([^/]+)\/?$/);
    if (source.hostname !== 'lista.mercadolivre.com.br' || !match
        || query.length && (query.length !== 1 || query[0][0] !== 'sb' || query[0][1] !== 'all_mercadolibre')) return { route: null, ambiguous: false };
    let term;
    try { term = decodeURIComponent(match[1]); } catch (_error) { return { route: null, ambiguous: false }; }
    if (!term || /[/*]|\$query|(?:^|_)\w+_|^_/i.test(term)) return { route: null, ambiguous: false };
    const suffix = match[1] + '_NoIndex_True', routes = new Map();
    for (const node of Array.isArray(page.categories) ? page.categories.slice(0, 1000) : []) {
        if (!node || node.key !== expected.key || label(node.nome) !== label(expected.nome)) continue;
        const href = publicUrl(node.url);
        if (!href) continue;
        const filtered = new URL(href);
        if (filtered.hostname !== source.hostname || filtered.search || !filtered.pathname.endsWith('/' + suffix)) continue;
        // The filter supplies the entire category path. Remove only the exact
        // observed query component; no category slug is derived from its name.
        filtered.pathname = filtered.pathname.slice(0, -suffix.length);
        const candidate = categoryUrl(filtered.href);
        if (candidate) routes.set(candidate, { key: expected.key, nome: expected.nome, url: candidate });
    }
    return { route: routes.size === 1 ? [...routes.values()][0] : null, ambiguous: routes.size > 1 };
}

function routeFromBreadcrumbs(snapshot, expected, ancestors) {
    const routes = new Map();
    for (const group of Array.isArray(snapshot.groups) ? snapshot.groups.slice(0, 4) : []) {
        if (!Array.isArray(group)) continue;
        for (let index = 0; index < Math.min(group.length, 40); index += 1) {
            const node = group[index];
            if (!node || label(node.nome) !== label(expected.nome)) continue;
            const candidate = categoryUrl(node.url);
            if (!candidate || index === 0) continue;
            const target = new URL(candidate), targetPath = target.pathname.replace(/\/+$/, '');
            let previousPath = '', previousLabelIndex = -1, valid = true;
            for (const [ancestorIndex, ancestor] of group.slice(0, index).entries()) {
                const rootHref = publicUrl(ancestor && ancestor.url), rootUrl = rootHref && new URL(rootHref);
                if (ancestorIndex === 0 && rootUrl && ['www.mercadolivre.com.br', 'mercadolivre.com.br'].includes(rootUrl.hostname)
                    && !rootUrl.search && /^\/c\/[^/]+\/?$/.test(rootUrl.pathname)) {
                    const knownRoot = publicUrl(ancestors[0].url), text = label(ancestor.nome);
                    const exactRoot = knownRoot && knownRoot.replace(/\/+$/, '') === rootHref.replace(/\/+$/, '');
                    if (text !== label(ancestors[0].nome) && !(exactRoot && /^(?:\.{3}|…)$/.test(text))
                        || knownRoot && new URL(knownRoot).pathname.startsWith('/c/') && !exactRoot) { valid = false; break; }
                    previousLabelIndex = 0;
                    continue;
                }
                const href = categoryUrl(ancestor && ancestor.url);
                if (!href) { valid = false; break; }
                const parsed = new URL(href), path = parsed.pathname.replace(/\/+$/, '');
                if (parsed.origin !== target.origin || !targetPath.startsWith(path + '/')
                    || previousPath && !path.startsWith(previousPath + '/')) { valid = false; break; }
                previousPath = path;
                const text = label(ancestor.nome);
                if (!text || /^(?:\.{3}|…)$/.test(text)) continue;
                const position = ancestors.findIndex((entry, offset) => offset > previousLabelIndex && label(entry.nome) === text);
                if (position < 0) { valid = false; break; }
                previousLabelIndex = position;
            }
            if (valid) routes.set(candidate, { key: expected.key, nome: expected.nome, url: candidate });
        }
    }
    return { route: routes.size === 1 ? [...routes.values()][0] : null, ambiguous: routes.size > 1 };
}

function exactSeeds(page, expectedId) {
    const categories = new Map(), urls = new Map();
    for (const item of Array.isArray(page.categoryItems) ? page.categoryItems.slice(0, 1000) : []) {
        if (!item || !ID.test(item.id || '') || !ID.test(item.categoryId || '')) continue;
        if (!categories.has(item.id)) categories.set(item.id, item.categoryId);
        else if (categories.get(item.id) !== item.categoryId) categories.set(item.id, '');
    }
    for (const item of Array.isArray(page.anuncios) ? page.anuncios.slice(0, 1000) : []) {
        if (!item || categories.get(item.id) !== expectedId) continue;
        const target = itemUrl(item.url, item.id);
        if (!target || target.id !== item.id) continue;
        if (!urls.has(item.id)) urls.set(item.id, item.url);
        else if (publicUrl(urls.get(item.id)) !== target.url) urls.set(item.id, '');
    }
    return [...urls].filter(([, url]) => url).slice(0, 3).map(([id, url]) => ({ id, url }));
}

async function discoverCategoryRoute({ browser, scripts, expected, ancestry, wait, signal }) {
    if (!expected || !ID.test(expected.key || '') || typeof expected.nome !== 'string' || !expected.nome.trim() || expected.nome.length > 400
        || !Array.isArray(ancestry) || !ancestry.length || ancestry.length > 32
        || ancestry.some(node => !node || !ID.test(node.key || '') || typeof node.nome !== 'string' || !node.nome.trim())
        || new Set(ancestry.map(node => node.key)).size !== ancestry.length) return null;
    const ancestors = ancestry.at(-1).key === expected.key ? ancestry.slice(0, -1) : ancestry;
    if (!ancestors.length || ancestors.some(node => node.key === expected.key)) return null;
    const parentName = ancestors.at(-1).nome;
    const terms = [...new Set([label(expected.nome) === 'outros' ? `${expected.nome} ${parentName}` : expected.nome, parentName]
        .map(value => value.replace(/\s+/g, ' ').trim()))];
    const controller = new AbortController();
    const abortError = () => Object.assign(new Error('Descoberta de categoria interrompida.'), { name: 'AbortError' });
    const timeoutError = () => Object.assign(new Error('Tempo de descoberta de categoria esgotado.'), { discoveryTimedOut: true });
    const forwardAbort = () => controller.abort();
    signal?.addEventListener('abort', forwardAbort, { once: true });
    const timer = setTimeout(() => controller.abort(), 60000);
    const check = () => { if (signal?.aborted) throw abortError(); if (controller.signal.aborted) throw timeoutError(); };
    const step = async operation => {
        check();
        let rejectAbort;
        const interrupted = new Promise((_resolve, reject) => { rejectAbort = () => reject(signal?.aborted ? abortError() : timeoutError()); controller.signal.addEventListener('abort', rejectAbort, { once: true }); });
        try { const result = await Promise.race([Promise.resolve().then(operation), interrupted]); check(); return result; }
        finally { controller.signal.removeEventListener('abort', rejectAbort); }
    };
    const pause = () => step(() => wait ? wait(350, controller.signal) : new Promise(resolve => setTimeout(resolve, 350)));
    const pageState = async () => {
        let page;
        try { page = await step(() => browser.executeJavaScript(scripts.discoveryScript())); }
        catch (error) { if (navigationContextChanged(error)) return { page: {}, url: '', settled: false }; throw error; }
        if (!page || typeof page !== 'object') return { page: {}, url: '', settled: false };
        if (page.authRequired || page.avantAuthRequired) throw Object.assign(new Error('Conclua o login para continuar a descoberta de categorias.'), { waitingLogin: true, reason: 'ml_login_required' });
        const actual = publicUrl(browser.getURL()), observed = publicUrl(page.url);
        const status = typeof browser.getNavigationStatus === 'function' && browser.getNavigationStatus();
        if (status && [403, 429].includes(status.statusCode) && publicUrl(status.url) === actual) {
            throw Object.assign(new Error('O Mercado Livre bloqueou temporariamente a pagina de descoberta.'), {
                code: 'category_access_denied', categoryUnavailable: true, navigationDependencyKey: expected.key
            });
        }
        return { page, url: actual, settled: !!actual && actual === observed && page.readyState !== 'loading'
            && !(typeof browser.isLoading === 'function' && browser.isLoading()) };
    };
    try {
        let seeds = [];
        for (const term of terms.slice(0, 2)) {
            await step(() => browser.navigate(INDEX_URL, controller.signal));
            let submitted = false;
            for (let poll = 0; poll < 20; poll += 1) {
                await pause();
                const state = await pageState();
                if (!state.settled) continue;
                if (state.url !== INDEX_URL) return null;
                try { submitted = await step(() => browser.executeJavaScript(`(${submitObservedSearch.toString()})(${JSON.stringify(term)},${JSON.stringify(SEARCH_ACTION)})`)); }
                catch (error) {
                    if (!navigationContextChanged(error)) throw error;
                    // requestSubmit may replace its own execution context. Do
                    // not submit twice; require the result document to settle.
                    submitted = true;
                }
                if (submitted) break;
            }
            if (!submitted) return null;
            let previous = '', stable = 0, searchUrl = '';
            for (let poll = 0; poll < 24; poll += 1) {
                await pause();
                const state = await pageState();
                if (!state.settled || state.url === INDEX_URL) { previous = ''; stable = 0; continue; }
                if (new URL(state.url).hostname !== 'lista.mercadolivre.com.br') return null;
                if (searchUrl && searchUrl !== state.url) return null;
                searchUrl = state.url;
                const filtered = routeFromQueryFilter(state.page, state.url, expected);
                if (filtered.ambiguous) return null;
                const found = exactSeeds(state.page, expected.key), signature = JSON.stringify([state.url, filtered.route, found]);
                stable = signature === previous ? stable + 1 : 1; previous = signature;
                if (filtered.route && stable >= 2) return filtered.route;
                if (found.length && stable >= 2) { seeds = found; break; }
            }
            if (seeds.length) break;
        }
        const routes = new Map();
        for (const seed of seeds.slice(0, 3)) {
            await step(() => browser.navigate(seed.url, controller.signal));
            let previous = '', stable = 0, openedUrl = '';
            for (let poll = 0; poll < 24; poll += 1) {
                await pause();
                const state = await pageState();
                if (!state.settled) { previous = ''; stable = 0; continue; }
                const opened = itemUrl(state.url, seed.id);
                if (!opened) return null;
                if (openedUrl && openedUrl !== state.url) return null;
                openedUrl = state.url;
                const snapshot = await step(() => browser.executeJavaScript(`(${publicBreadcrumbs.toString()})()`));
                if (publicUrl(snapshot.url) !== state.url || publicUrl(browser.getURL()) !== state.url) return null;
                const canonical = itemUrl(snapshot.canonical, seed.id);
                if (!canonical || !opened.id && !canonical.id) return null;
                const breadcrumb = routeFromBreadcrumbs(snapshot, expected, ancestors);
                if (breadcrumb.ambiguous) return null;
                const candidate = breadcrumb.route, signature = JSON.stringify([state.url, candidate]);
                stable = signature === previous ? stable + 1 : 1; previous = signature;
                if (candidate && stable >= 2) { routes.set(candidate.url, candidate); break; }
            }
        }
        // Only an observed route is returned. The caller must validate its final
        // document ID with read(); keyword search results never become results.
        return routes.size === 1 ? [...routes.values()][0] : null;
    } catch (error) {
        if (signal?.aborted) throw abortError();
        if (error.discoveryTimedOut || controller.signal.aborted) return null;
        if (error.waitingLogin || error.code === 'category_access_denied') throw error;
        return null;
    } finally {
        clearTimeout(timer);
        signal?.removeEventListener('abort', forwardAbort);
    }
}

module.exports = { discoverCategoryRoute };
