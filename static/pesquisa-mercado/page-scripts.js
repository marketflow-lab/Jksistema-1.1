(function (root, factory) {
    'use strict';
    if (typeof module === 'object' && module.exports) module.exports = factory(null, require);
    else root.PesquisaMercadoPageScripts = factory(root, null);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (root, nodeRequire) {
    'use strict';
    let nodeRegistry;

    function defaultRender(name, params) {
        if (root && root.FavoritosV2 && root.FavoritosV2.browser.pageScripts) {
            return root.FavoritosV2.browser.pageScripts.render(name, params);
        }
        if (!nodeRequire) throw new Error('Scripts do Avant Pro indisponiveis.');
        if (!nodeRegistry) {
            // These are the same versioned templates used by Favoritos, not page input.
            const fs = nodeRequire('fs'), path = nodeRequire('path'), vm = nodeRequire('vm');
            const context = { window: {} };
            const directory = path.join(__dirname, '..', 'favoritos', 'v2', 'browser', 'page-scripts');
            for (const file of ['runtime.js', 'template-bundle-06.js', 'template-bundle-09.js', 'template-bundle-10.js']) {
                vm.runInNewContext(fs.readFileSync(path.join(directory, file), 'utf8'), context, { filename: file, timeout: 1000 });
            }
            nodeRegistry = context.window.FavoritosV2.browser.pageScripts;
        }
        return nodeRegistry.render(name, params);
    }

    // Self contained: serialized into the Mercado Livre renderer. Never follows pagination.
    function readPage(options) {
        const clean = value => String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
        const norm = value => clean(value).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
        const nodes = (selector, scope) => Array.from((scope || document).querySelectorAll(selector));
        const absolute = value => {
            if (!clean(value)) return '';
            try {
                const url = new URL(value, location.href);
                if (url.protocol !== 'https:' || !/(^|\.)mercadolivre\.com\.br$/i.test(url.hostname)) return '';
                const fragmentText = url.hash.replace(/^#/, '');
                let fragmentWid = new URLSearchParams(fragmentText).get('wid');
                if (!fragmentWid) {
                    try { fragmentWid = new URLSearchParams(decodeURIComponent(fragmentText)).get('wid'); } catch (_error) {}
                }
                if (!url.searchParams.has('wid') && /^MLB\d{6,}$/i.test(fragmentWid || '')) {
                    url.searchParams.set('wid', fragmentWid.toUpperCase());
                }
                url.hash = '';
                for (const key of ['tracking_id', 'position', 'polycard_client', 'sid', 'reco_id', 'searchVariation']) url.searchParams.delete(key);
                return url.href;
            } catch (_error) { return ''; }
        };
        const identity = value => {
            const url = absolute(value);
            const ids = new Set();
            for (const match of url.matchAll(/_(?:CustId|SellerId)_(\d+)/gi)) ids.add(match[1]);
            if (url) for (const [key, id] of new URL(url).searchParams) if (/^(?:seller_id|cust_?id)$/i.test(key) && /^\d+$/.test(id)) ids.add(id);
            if (ids.size > 1) return { id: '', perfilUrl: '', store: false, conflict: true };
            const match = ids.size ? [null, [...ids][0]] : null;
            const profile = url.match(/^(https:\/\/[^/]+\/perfil\/[^/?#]+)/i);
            const store = url.match(/^https:\/\/[^/]+\/loja\/([^/?#]+)/i);
            if (store) return { id: '', perfilUrl: 'https://www.mercadolivre.com.br/loja/' + store[1].toLowerCase(), store: true };
            return { id: match ? match[1] : '', perfilUrl: profile ? profile[1].replace(/\/$/, '') : (match ? 'https://lista.mercadolivre.com.br/_CustId_' + match[1] : ''), store: false };
        };
        const productId = (value, element) => {
            const explicit = clean(element && (element.getAttribute('data-item-id') || element.getAttribute('data-id')));
            const raw = clean(value);
            const match = raw.match(/[?&](?:wid|item_id)=(MLB\d{6,})(?:[&#]|$)/i)
                || explicit.match(/\b(MLB-?\d{6,})\b/i)
                || raw.match(/\b(MLB-\d{6,})\b/i);
            return match ? match[1].replace('-', '').toUpperCase() : '';
        };
        const isProduct = value => /\/MLB-\d{6,}|\/(?:p|up)\/MLB|[?&](?:wid|item_id)=MLB/i.test(value);
        const firstLink = (scope, selector) => nodes(selector, scope).find(item => absolute(item.getAttribute('href')));
        // A page transition briefly has neither main nor body. Read an empty
        // document safely while the coordinator waits for the new listing.
        const content = document.querySelector('main') || document.body || document;
        const url = absolute(location.href);
        const mlVerificationRequired = /\/captcha(?:\/|$)/i.test(location.pathname);
        // Read only category identity for this listing. Product/recommendation
        // category IDs and available_filters must never prove a redirect scope.
        let categoryIdentityConflict = false, categoryDocumentIdentity = false, categoryContext = null;
        let categoryStructuredNavigation = null, categoryBrowse = null, categoryFilterMoreUrl = '', categoryItems = [];
        const categoryExpansionUrls = new Set();
        const categoryExpansionUrl = value => {
            if (typeof value !== 'string' || value.length > 4000 || /[\\\u0000-\u0020]/.test(value)) return '';
            try {
                const current = new URL(url), more = new URL(value, url);
                if (more.protocol !== 'https:' || more.origin !== current.origin || more.username || more.password || more.hash
                    || current.username || current.password || more.port && more.port !== '443'
                    || !/(^|\.)mercadolivre\.com\.br$/i.test(more.hostname)) return '';
                const query = [...more.searchParams];
                if (query.length !== 1 || query[0][0] !== 'filter' || query[0][1] !== 'category'
                    || !/_FiltersAvailableSidebar\/?$/.test(more.pathname)
                    || more.pathname.replace(/_FiltersAvailableSidebar\/?$/, '').replace(/\/+$/, '') !== current.pathname.replace(/\/+$/, '')) return '';
                return more.href;
            } catch (_error) { return ''; }
        };
        const rememberCategoryExpansion = value => {
            const target = categoryExpansionUrl(value);
            if (target) categoryExpansionUrls.add(target);
            categoryFilterMoreUrl = categoryExpansionUrls.size === 1 ? [...categoryExpansionUrls][0] : '';
        };
        const effectiveCategoryId = (() => {
            if (mlVerificationRequired || isProduct(url) || currentListingIsSeller()) return '';
            const evidence = new Set(), documentEvidence = new Set();
            const add = (value, fromDocument = false) => {
                if (typeof value !== 'string' || !/^MLB\d+$/.test(value)) return;
                evidence.add(value);
                if (fromDocument) documentEvidence.add(value);
            };
            function currentListingIsSeller() { const seller = identity(url); return !!(seller.id || seller.perfilUrl); }
            try {
                const parsed = new URL(url);
                for (const match of decodeURIComponent(parsed.pathname).matchAll(/_(?:CatId|CATEGORY)_(MLB\d+)(?=_|\/|$)/gi)) add(match[1].toUpperCase());
                for (const [key, value] of parsed.searchParams) if (/^(?:category|category_id|categoryid)$/i.test(key)) add(value.toUpperCase());
            } catch (_error) {}
            for (const element of nodes('meta[name="category_id"], meta[property="category_id"], main[data-category-id]')) add(element.getAttribute('content') || element.getAttribute('data-category-id'), true);
            const inspect = state => {
                if (!state || typeof state !== 'object' || Array.isArray(state)) return;
                add(state.category_id, true); add(state.categoryId, true);
                if (Array.isArray(state.filters)) {
                    for (const filter of state.filters) {
                        if (!filter || filter.id !== 'category' || !Array.isArray(filter.values) || filter.values.length !== 1) continue;
                        add(filter.values[0] && filter.values[0].id, true);
                    }
                }
            };
            const inspectDocumentState = state => {
                if (!state || typeof state !== 'object') return;
                const initial = state.initialState, props = state.props && state.props.pageProps;
                for (const source of [state, state.pageState, state.search, initial, initial && initial.pageState,
                    initial && initial.search, initial && initial.pageState && initial.pageState.search,
                    props, props && props.initialState, props && props.initialState && props.initialState.pageState]) inspect(source);
            };
            const assignedJson = (text, pattern) => {
                if (text.length > 8 * 1024 * 1024) return null;
                const match = text.match(pattern);
                if (!match) return null;
                const start = match.index + match[0].length;
                if (text[start] !== '{') return null;
                let depth = 0, quoted = false, escaped = false;
                for (let index = start; index < text.length; index += 1) {
                    const char = text[index];
                    if (quoted) { if (escaped) escaped = false; else if (char === '\\') escaped = true; else if (char === '"') quoted = false; }
                    else if (char === '"') quoted = true;
                    else if (char === '{') depth += 1;
                    else if (char === '}' && --depth === 0) {
                        try { return JSON.parse(text.slice(start, index + 1)); } catch (_error) { return null; }
                    }
                }
                return null;
            };
            for (const script of nodes('script#__PRELOADED_STATE__, script#__NEXT_DATA__').slice(0, 12)) {
                const text = script.textContent || '';
                if (text.length > 8 * 1024 * 1024) continue;
                try { inspectDocumentState(JSON.parse(text)); } catch (_error) {}
            }
            // ML may serialize the same state as an assignment. Parse its JSON
            // literal; never execute script text or walk arbitrary nested data.
            for (const script of nodes('script:not([src])').slice(0, 40)) {
                const text = script.textContent || '';
                inspectDocumentState(assignedJson(text, /(?:window\.)?__PRELOADED_STATE__\s*=\s*/));
            }
            const nordicPageProps = [];
            for (const script of nodes('script#__NORDIC_RENDERING_CTX__').slice(0, 4)) {
                // Nordic appProps are serialized JSON followed by runtime code.
                // Read only its initial literal and known pageProps path. Optional
                // search_filters and trend/recommendation payloads are not applied filters.
                const state = assignedJson(script.textContent || '', /^\s*_n\.ctx\.r\s*=\s*/);
                if (state && state.appProps && typeof state.appProps === 'object' && !Array.isArray(state.appProps)) {
                    inspectDocumentState({ props: state.appProps });
                    if (state.appProps.pageProps && typeof state.appProps.pageProps === 'object') nordicPageProps.push(state.appProps.pageProps);
                }
            }
            // Nordic's RSC transport is a JSON string carrying extended JSON.
            // Undefined/NaN and opaque rendering references have no category
            // authority. Keep them absent rather than execute or guess a decoder.
            const rscParts = [];
            let rscBytes = 0;
            for (const script of nodes('script:not([src])').slice(0, 128)) {
                const text = script.textContent || '';
                if (text.length > 8 * 1024 * 1024) continue;
                const match = text.match(/^\s*_n\.ctx\.s\["nr"\]\.q\(\s*/);
                if (!match || text[match[0].length] !== '"') continue;
                const start = match[0].length;
                let escaped = false;
                for (let end = start + 1; end < text.length; end += 1) {
                    if (escaped) { escaped = false; continue; }
                    if (text[end] === '\\') { escaped = true; continue; }
                    if (text[end] !== '"') continue;
                    try {
                        const part = JSON.parse(text.slice(start, end + 1));
                        if (typeof part === 'string' && rscBytes + part.length <= 8 * 1024 * 1024) { rscParts.push(part); rscBytes += part.length; }
                    } catch (_error) {}
                    break;
                }
            }
            const extendedJson = text => {
                let result = '', quoted = false, escaped = false, references = 0;
                for (let index = 0; index < text.length; index += 1) {
                    const char = text[index];
                    if (quoted) {
                        result += char;
                        if (escaped) escaped = false;
                        else if (char === '\\') escaped = true;
                        else if (char === '"') quoted = false;
                    } else if (char === '"') { quoted = true; result += char; }
                    else {
                        const token = ['@', '$', 'N', 'u'].includes(char) && text.slice(index).match(/^(?:@[0-9]+|\$[0-9]+|NaN|u)(?=[\s,}\]]|$)/);
                        if (token) {
                            if (++references > 2048) return null;
                            result += 'null'; index += token[0].length - 1;
                        } else result += char;
                    }
                }
                try { return JSON.parse(result); } catch (_error) { return null; }
            };
            const coupledCategory = value => {
                if (!value || typeof value !== 'object') return null;
                const key = value.id === 'category' ? value.value : value.id || value.value;
                if (typeof key !== 'string' || key.length > 500 || !/^MLB\d+$/.test(key)) return null;
                const nome = clean(value.name || value.text), target = absolute(value.permalink || value.url);
                if (!nome || nome.length > 400 || !target || target.length > 4000) return null;
                const parsed = new URL(target);
                if (parsed.search || parsed.username || parsed.password || parsed.port && parsed.port !== '443'
                    || identity(target).id || identity(target).perfilUrl || isProduct(target)) return null;
                return { key, nome, url: target };
            };
            const availableCategory = value => {
                if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
                const key = value.id === 'category' ? value.value : value.id || value.value;
                const raw = value.permalink || value.url;
                if (typeof key !== 'string' || !/^MLB\d+$/.test(key) || typeof raw !== 'string' || raw.length > 4000 || /[\\\u0000-\u0020]/.test(raw)) return null;
                try {
                    const target = new URL(raw, url);
                    if (target.hash) {
                        const params = new URLSearchParams(decodeURIComponent(target.hash.slice(1)));
                        const allowed = new Set(['applied_filter_id', 'applied_filter_name', 'applied_filter_order', 'applied_value_id', 'applied_value_name',
                            'applied_value_order', 'applied_value_results', 'is_custom', 'view_more_flag']);
                        if ([...params.keys()].some(name => !allowed.has(name)) || params.getAll('applied_filter_id').length !== 1
                            || params.get('applied_filter_id') !== 'category' || params.getAll('applied_value_id').length !== 1
                            || params.get('applied_value_id') !== key) return null;
                        target.hash = '';
                    }
                    return coupledCategory({ id: key, name: value.name || value.text, permalink: target.href });
                } catch (_error) { return null; }
            };
            const itemCategories = new Map();
            const inspectCategoryItems = results => {
                for (const result of Array.isArray(results) ? results.slice(0, 1000) : []) {
                    const metadata = result && result.polycard && result.polycard.metadata;
                    if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)
                        || typeof metadata.id !== 'string' || !/^MLB\d+$/.test(metadata.id)
                        || typeof metadata.category_id !== 'string' || !/^MLB\d+$/.test(metadata.category_id)) continue;
                    if (!itemCategories.has(metadata.id)) itemCategories.set(metadata.id, metadata.category_id);
                    else if (itemCategories.get(metadata.id) !== metadata.category_id) itemCategories.set(metadata.id, '');
                }
            };
            if (document.querySelector('[class*="ui-category-trends"]')) for (const props of nordicPageProps) {
                const initial = props.initialState;
                if (!initial || typeof initial !== 'object' || !Array.isArray(initial.search_filters) || !Array.isArray(props.categories)) continue;
                const selectedOptions = initial.search_filters.filter(value => value && value.id === 'category');
                const emittedOptions = props.categories.filter(value => value && value.id === 'category');
                if (selectedOptions.length !== 1 || emittedOptions.length !== 1) continue;
                const option = selectedOptions[0];
                const selected = coupledCategory({ ...option, id: option.value });
                const searchTemplate = absolute(initial.search_filter && initial.search_filter.permalink);
                const emittedTemplate = absolute(emittedOptions[0].permalink);
                if (!selected || selected.url !== searchTemplate || !emittedTemplate) continue;
                const search = new URL(searchTemplate), emitted = new URL(emittedTemplate);
                if (!/\/\$query$/.test(search.pathname) || !/\/\$query_NoIndex_True$/.test(emitted.pathname)
                    || emitted.search || emitted.username || emitted.password || emitted.port && emitted.port !== '443') continue;
                const base = new URL(search.href); base.pathname = base.pathname.slice(0, -'$query'.length);
                const emittedBase = emitted.pathname.slice(0, -'$query_NoIndex_True'.length);
                if (emitted.origin !== base.origin || emittedBase !== base.pathname) continue;
                const canonical = absolute(initial.canonical_info && initial.canonical_info.canonical);
                const sameBase = target => {
                    if (!target) return false;
                    const parsed = new URL(target);
                    return !parsed.search && parsed.origin === base.origin && parsed.pathname.replace(/\/+$/, '') === base.pathname.replace(/\/+$/, '');
                };
                if (!sameBase(url) || !sameBase(canonical) || !Array.isArray(initial.breadcrumb)) continue;
                const breadcrumb = initial.breadcrumb.at(-1);
                if (!breadcrumb || norm(breadcrumb.text) !== norm(selected.nome) || !sameBase(absolute(breadcrumb.url))) continue;
                // This is an observed form template, not a category document.
                // Retain its emitted NoIndex suffix and substitute only its
                // explicit query placeholder; the runner must verify the next document.
                categoryBrowse = { key: selected.key, nome: selected.nome, url: emitted.href.replace('$query', '*') };
            }
            for (const props of nordicPageProps) {
                const initial = props.initialState;
                if (!initial || typeof initial !== 'object' || Array.isArray(initial)) continue;
                inspectCategoryItems(initial.results);
                const components = initial.sidebar && Array.isArray(initial.sidebar.components) ? initial.sidebar.components.slice(0, 128) : [];
                const categories = [], breadcrumbs = [], breadcrumbComponents = [];
                const appendAvailable = filters => {
                    for (const filter of Array.isArray(filters) ? filters.slice(0, 128) : []) {
                        if (!filter || filter.id !== 'category') continue;
                        for (const value of Array.isArray(filter.values) ? filter.values.slice(0, 1000) : []) {
                            const category = coupledCategory(value);
                            if (category && !category.url.includes('$query')) categories.push(category);
                        }
                    }
                };
                appendAvailable(initial.available_filters);
                for (const component of components) {
                    if (!component || typeof component !== 'object') continue;
                    if (component.type === 'APPLIED_FILTERS') inspect({ filters: component.filters });
                    if (component.type === 'AVAILABLE_FILTERS') appendAvailable(component.filters);
                    if (component.type !== 'BREADCRUMB') continue;
                    breadcrumbComponents.push(component);
                    for (const node of Array.isArray(component.nodes) ? component.nodes.slice(0, 32) : []) {
                        const target = absolute(node && node.url), nome = clean(node && node.text);
                        if (target && target.length <= 4000 && nome && nome.length <= 400 && !new URL(target).search
                            && !new URL(target).username && !new URL(target).password && !isProduct(target) && !identity(target).perfilUrl) breadcrumbs.push({ nome, url: target });
                    }
                }
                categoryStructuredNavigation = { categories, breadcrumbs };
                const options = Array.isArray(initial.search_filters) ? initial.search_filters.filter(option => option && option.id === 'category') : [];
                if (options.length !== 1) continue;
                const option = options[0], selected = coupledCategory({ ...option, id: option.value });
                if (!selected || !/\/\$query\/?$/.test(new URL(selected.url).pathname)
                    || absolute(initial.search_filter && initial.search_filter.permalink) !== selected.url) continue;
                const template = new URL(selected.url);
                template.pathname = template.pathname.replace(/\$query\/?$/, '');
                categoryContext = { key: selected.key, nome: selected.nome, baseUrl: template.href };
                categoryStructuredNavigation.listingTemplate = selected;
                const basePath = template.pathname.replace(/\/+$/, '');
                let decodedBase;
                try { decodedBase = decodeURIComponent(basePath); } catch (_error) { continue; }
                if (/\*|\$query|(?:^|\/)_[^/]+/.test(decodedBase)) continue;
                const exactCategoryBase = target => {
                    if (!target) return false;
                    const parsed = new URL(target), query = [...parsed.searchParams.entries()];
                    if (parsed.origin !== template.origin || parsed.username || parsed.password || parsed.port && parsed.port !== '443'
                        || query.length && (query.length !== 1 || query[0][0] !== 'original_category_landing' || query[0][1] !== 'true')) return false;
                    return parsed.pathname.replace(/\/_NoIndex_True\/?$/, '/').replace(/\/+$/, '') === basePath;
                };
                const component = breadcrumbComponents.length === 1 && breadcrumbComponents[0];
                const label = norm(component && component.label && component.label.text), name = norm(selected.nome);
                const headings = nodes('main h1');
                const ancestorNodes = component && Array.isArray(component.nodes) && component.nodes;
                let previousPath = '';
                const ancestors = ancestorNodes && ancestorNodes.length > 0 && ancestorNodes.length <= 32 && breadcrumbs.length === ancestorNodes.length
                    && breadcrumbs.every((node, index) => {
                        const parsed = new URL(node.url), path = parsed.pathname.replace(/\/+$/, '');
                        if (index === 0 && parsed.hostname === 'www.mercadolivre.com.br' && (!parsed.port || parsed.port === '443')) {
                            let rootPath;
                            try { rootPath = decodeURIComponent(parsed.pathname); } catch (_error) { return false; }
                            return /^\/c\/[^/_*$?#\u0000-\u0020]+\/?$/.test(rootPath);
                        }
                        if (parsed.origin === template.origin && path === basePath) {
                            const rawNode = ancestorNodes[index];
                            const linkedIds = [rawNode && rawNode.id, rawNode && rawNode.key, rawNode && rawNode.category_id].filter(value => typeof value === 'string' && /^MLB\d+$/.test(value));
                            return index === breadcrumbs.length - 1 && norm(node.nome) === name
                                && linkedIds.every(value => value === selected.key) && (!previousPath || path.startsWith(previousPath + '/'));
                        }
                        const valid = !!path && parsed.origin === template.origin && basePath.startsWith(path + '/')
                            && (!previousPath || path.startsWith(previousPath + '/'));
                        previousPath = path;
                        return valid;
                    });
                const listing = !!document.querySelector('main .ui-search-layout');
                const trends = !!document.querySelector('[class*="ui-category-trends"]');
                const cards = Array.isArray(initial.results) && initial.results.some(item => item && typeof item.polycard === 'object' && item.polycard !== null);
                // The root can use www/c and the final linked node can be the
                // selected category itself. Every other node remains an ordered
                // strict ancestor; a child URL cannot borrow its parent's base.
                if (listing && !trends && cards && exactCategoryBase(url) && exactCategoryBase(absolute(initial.canonical_info && initial.canonical_info.canonical))
                    && component && component.html_tag === 'h1' && label && (label === name || label.endsWith(' ' + name))
                    && headings.length === 1 && norm(headings[0].textContent) === label && ancestors) add(selected.key, true);
            }
            for (const line of rscParts.join('').split('\n').slice(0, 2048)) {
                if (!line.startsWith('0:')) continue;
                const state = extendedJson(line.slice(2));
                const optimized = state && state.optimizedResponse;
                if (!optimized || typeof optimized !== 'object' || Array.isArray(optimized)) continue;
                inspectCategoryItems(optimized.results);
                // Applied filters have a different meaning from search options.
                inspect({ filters: optimized.filters });
                const components = optimized.sidebar && Array.isArray(optimized.sidebar.components) ? optimized.sidebar.components : [];
                const categoryCandidates = new Map(), categoryCandidateConflicts = new Set(), breadcrumbs = [];
                const appendAvailable = filters => {
                    for (const filter of Array.isArray(filters) ? filters.slice(0, 128) : []) {
                        if (!filter || filter.id !== 'category') continue;
                        rememberCategoryExpansion(filter.url);
                        const values = [...(Array.isArray(filter.values) ? filter.values.slice(0, 1000) : []),
                            ...(Array.isArray(filter.expanded_values) ? filter.expanded_values.slice(0, 1000) : [])];
                        for (const value of values) {
                            const category = availableCategory(value);
                            if (!category || category.url.includes('$query')) continue;
                            const previous = categoryCandidates.get(category.key);
                            if (previous && previous.url !== category.url) categoryCandidateConflicts.add(category.key);
                            else categoryCandidates.set(category.key, category);
                        }
                    }
                };
                appendAvailable(optimized.available_filters);
                for (const component of components.slice(0, 128)) {
                    if (!component || typeof component !== 'object') continue;
                    if (component.type === 'APPLIED_FILTERS') inspect({ filters: component.filters });
                    if (component.type === 'AVAILABLE_FILTERS') appendAvailable(component.filters);
                    if (component.type === 'BREADCRUMB') {
                        // label.text is the searched term, even when it happens
                        // to equal the category name. Only the linked nodes can
                        // establish a category breadcrumb.
                        for (const node of Array.isArray(component.nodes) ? component.nodes.slice(0, 32) : []) {
                            const target = absolute(node && node.url), nome = clean(node && node.text);
                            if (target && target.length <= 4000 && nome && nome.length <= 400 && !new URL(target).search && !new URL(target).username && !new URL(target).password
                                && !isProduct(target) && !identity(target).perfilUrl) breadcrumbs.push({ nome, url: target });
                        }
                    }
                }
                const categories = [...categoryCandidates.values()].filter(category => !categoryCandidateConflicts.has(category.key));
                categoryStructuredNavigation = { categories, breadcrumbs };
                const options = Array.isArray(optimized.search_filters) ? optimized.search_filters.filter(option => option && option.id === 'category') : [];
                if (options.length !== 1) continue;
                const option = options[0];
                const selected = coupledCategory({ ...option, id: option.value });
                if (!selected || !/\/\$query\/?$/.test(new URL(selected.url).pathname)) continue;
                const template = new URL(selected.url);
                template.pathname = template.pathname.replace(/\$query\/?$/, '');
                categoryContext = { key: selected.key, nome: selected.nome, baseUrl: template.href };
                categoryStructuredNavigation.listingTemplate = selected;
                const canonical = absolute(optimized.canonical_info && optimized.canonical_info.canonical);
                const inside = target => {
                    if (!target) return false;
                    const parsed = new URL(target), base = template.pathname.replace(/\/+$/, '');
                    return parsed.origin === template.origin && (parsed.pathname.replace(/\/+$/, '') === base || parsed.pathname.startsWith(base + '/'));
                };
                const listing = !!document.querySelector('main .ui-search-layout');
                const trends = !!document.querySelector('[class*="ui-category-trends"]');
                const cards = Array.isArray(optimized.results) && optimized.results.some(item => item && typeof item.polycard === 'object' && item.polycard !== null);
                const lastBreadcrumb = breadcrumbs.at(-1);
                const linkedCategory = lastBreadcrumb && norm(lastBreadcrumb.nome) === norm(selected.nome)
                    && new URL(lastBreadcrumb.url).origin === template.origin
                    && new URL(lastBreadcrumb.url).pathname.replace(/\/+$/, '') === template.pathname.replace(/\/+$/, '');
                if (listing && !trends && cards && canonical && inside(url) && inside(canonical)
                    && new URL(canonical).pathname.replace(/\/+$/, '') === new URL(url).pathname.replace(/\/+$/, '')
                    && linkedCategory) add(selected.key, true);
            }
            categoryItems = [...itemCategories].filter(([, categoryId]) => categoryId).map(([id, categoryId]) => ({ id, categoryId }));
            categoryIdentityConflict = evidence.size > 1;
            categoryDocumentIdentity = documentEvidence.size === 1 && !categoryIdentityConflict;
            return evidence.size === 1 ? [...evidence][0] : '';
        })();
        const bodyText = norm(document.body && (document.body.innerText || document.body.textContent));
        const loginForm = document.querySelector('input[type="password"], form[action*="login"], #login_user_form');
        const authRequired = mlVerificationRequired || /\/(?:jms\/[^/]+\/)?login|\/account-verification|\/challenge\b/i.test(location.pathname)
            || !!loginForm || !!document.querySelector('[data-testid="captcha"], iframe[src*="captcha"]');
        const avantDialog = nodes('[role="dialog"], [class*="avantpro"][class*="modal"], [class*="avantpro"][class*="login"]')
            .find(node => /avant\s*pro|avantpro/.test(norm(node.textContent)) && /login|entrar|credenciais|seu e.?mail|vincular conta/.test(norm(node.textContent)));
        const officialStore = nodes('a[href*="/loja/"]', content).find(anchor => {
            if (!identity(anchor.getAttribute('href')).store || anchor.closest('[class*="recommend"], [class*="carousel"], [class*="eshop"], [class*="same-seller"]')) return false;
            const label = norm([anchor.textContent, anchor.getAttribute('aria-label'), anchor.getAttribute('title')].join(' '));
            const nearbyLabel = norm(anchor.parentElement && anchor.parentElement.textContent);
            return /\bloja\b/.test(label) || (nearbyLabel.length < 160 && /\bloja oficial/.test(nearbyLabel)) || !!anchor.closest('[class*="official-store"], [data-testid*="official-store"]');
        });
        const headerSeller = firstLink(content, '.ui-pdp-seller__link, .ui-pdp-seller__link-trigger, .ui-pdp-seller__header__info-container a[href], .ui-seller-info a[href], .seller-info a[href], [data-testid="seller-info"] a[href]') || officialStore;
        const currentIdentity = identity(url);
        const headerIdentity = identity(headerSeller && headerSeller.href);
        const primaryStore = currentIdentity.store ? currentIdentity : (headerIdentity.store ? headerIdentity : null);
        const allAds = nodes('a[href]', content).find(anchor => {
            if (anchor.closest('[class*="recommend"], [class*="carousel"], [class*="eshop"], [class*="same-seller"]')) return false;
            const label = norm(anchor.textContent);
            if (!/^(?:todos os (?:produtos|anuncios)|(?:ver|veja|ir para)\s+(?:todos?|mais|os|as)?\s*(?:os\s+|as\s+)?(?:anuncios|produtos))/.test(label)) return false;
            const candidate = absolute(anchor.getAttribute('href'));
            if (!candidate) return false;
            return !primaryStore || identity(candidate).perfilUrl === primaryStore.perfilUrl;
        });
        const allIdentity = identity(allAds && allAds.href);
        const primaryIdentity = currentIdentity.id || currentIdentity.perfilUrl ? currentIdentity : (headerIdentity.id || headerIdentity.perfilUrl ? headerIdentity : allIdentity);
        const headerConflict = !!currentIdentity.conflict || !!headerIdentity.conflict || !!(
            currentIdentity.id && headerIdentity.id && currentIdentity.id !== headerIdentity.id
            || currentIdentity.perfilUrl && headerIdentity.perfilUrl && !currentIdentity.id && !headerIdentity.id
                && currentIdentity.perfilUrl.toLowerCase() !== headerIdentity.perfilUrl.toLowerCase());
        const sellerId = primaryIdentity.id || '';
        const perfilUrl = primaryIdentity.perfilUrl || '';
        const nameText = value => {
            const name = clean(value), normalized = norm(name);
            return !name || name === sellerId || /^(?:id:)?[\d\s.,_-]+$/.test(normalized)
                || /^(?:(?:ir para|visitar|acessar|ver|conhecer) (?:a |o )?)?(?:loja(?: oficial)?|perfil(?: do vendedor)?|vendedor|todos os (?:produtos|anuncios))$/.test(normalized)
                || /^(?:ver|veja) (?:todos os |mais )?(?:produtos|anuncios)$/.test(normalized)
                || /^(?:nome (?:pendente|nao identificado)|vendedor (?:nao identificado|desconhecido))\b/.test(normalized)
                || /^(?:vendedor|seller|comprar|saiba mais)$/.test(normalized) ? '' : name;
        };
        const nameSelectors = '.ui-pdp-seller__header__title, .ui-pdp-seller__nickname, .ui-seller-profile__name, .seller-profile__name, [data-testid="seller-name"], .ui-ms-profile__name, .ui-ms-profile__title';
        const named = nodes(nameSelectors, content).filter(node => !node.closest('[class*="recommend"], [class*="carousel"], [class*="eshop"], [class*="same-seller"]'))
            .map(node => nameText(node.textContent)).find(Boolean);
        const storeTitle = currentIdentity.store && !isProduct(url) ? nameText((content.querySelector('h1') || {}).textContent) : '';
        const vendedor = headerConflict ? '' : named || nameText(headerSeller && headerSeller.textContent) || storeTitle;
        const sellerFirstPage = (currentIdentity.id || (currentIdentity.store && new URL(url).hostname === 'lista.mercadolivre.com.br')) && !isProduct(url)
            && !/_Desde_(?:[2-9]\d*|1\d+)|[?&](?:page|offset)=(?:[2-9]\d*|1\d+)/i.test(url);
        const allAdsUrl = absolute(allAds && allAds.getAttribute('href')) || (sellerFirstPage ? url : '');
        const categoryPath = value => {
            try { return new URL(value).pathname.replace(/\/_NoIndex_True\/?$/i, '').replace(/\/$/, ''); } catch (_error) { return ''; }
        };
        const categoryListing = !isProduct(url) && !currentIdentity.id && !currentIdentity.perfilUrl
            ? nodes('a[href]', content).find(anchor => {
                if (!/^ver (?:tudo|todos(?: os produtos)?)$/.test(norm(anchor.textContent))) return false;
                if (anchor.closest('[class*="carousel"], [class*="recommend"], [class*="trending"], [data-testid="trends"]')) return false;
                const candidate = absolute(anchor.getAttribute('href'));
                return candidate && candidate !== url && !isProduct(candidate)
                    && new URL(candidate).hostname === 'lista.mercadolivre.com.br'
                    && categoryPath(candidate) === categoryPath(url);
            }) : null;
        const listingUrl = absolute(categoryListing && categoryListing.getAttribute('href'));
        const next = document.querySelector('a[rel="next"], .andes-pagination__button--next a, [data-andes-pagination-control="next"], a.andes-pagination__link[title="Seguinte"], a[aria-label="Seguinte"]');
        const nextDisabled = next && (next.matches('[disabled], [aria-disabled="true"]') || next.closest('[aria-disabled="true"], .andes-pagination__button--disabled'));
        const hasNext = !!(next && !nextDisabled);
        const nextUrl = hasNext ? absolute(next.getAttribute('href')) : '';
        const cards = [];
        const seenElements = new Set();
        const cardSelector = '.ui-search-layout__item, .poly-card, .ui-search-result, [data-testid="search-result"], .ui-search-result__wrapper';
        // Avant uses structured rows in search results and stat cards in the PDP.
        // The PDP templates in bundled 7.0.3 and Chrome 7.32.3 mount this exact root.
        const avantDetailRoot = '#productInfo.avantpro-in-product-content';
        const excludedMetricArea = '[class*="recommend"], [class*="carousel"], [class*="same-seller"], [class*="eshop"]';
        const currentItemId = productId(url);
        const expectedSellerId = clean(options && options.seller && (options.seller.id || options.seller.sellerId || options.seller.seller_id));
        const scopedSellerId = sellerId || expectedSellerId;
        const expectedSellerProfile = identity(options && options.seller && (options.seller.perfilUrl || options.seller.seller_perfil_url));
        const namedProfile = value => /^https:\/\/[^/]+\/(?:perfil|loja)\//i.test(value || '');
        const currentUpId = (url.match(/\/up\/(MLBU\d+)/i) || [])[1] || '';
        const currentCatalogId = (url.match(/\/p\/(MLB\d+)/i) || [])[1] || '';
        const rootMatchesDocument = root => {
            const declaredItems = ['data-item-id', 'data-mlb'].map(field => clean(root.getAttribute(field)).replace('-', '').toUpperCase()).filter(Boolean);
            const declaredUp = clean(root.getAttribute('data-user-product-id')).toUpperCase();
            const declaredProduct = clean(root.getAttribute('data-product-id')).toUpperCase();
            const declaredCatalog = clean(root.getAttribute('data-catalog-product-id')).toUpperCase();
            for (const declaredItem of declaredItems) {
                if (/^MLB\d+$/.test(declaredItem) && declaredItem !== currentItemId) return false;
                if (/^MLBU\d+$/.test(declaredItem) && declaredItem !== currentUpId.toUpperCase()) return false;
            }
            if (/^MLBU\d+$/.test(declaredUp) && declaredUp !== currentUpId.toUpperCase()) return false;
            if (/^MLBU\d+$/.test(declaredProduct) && declaredProduct !== currentUpId.toUpperCase()) return false;
            if (/^MLB\d+$/.test(declaredProduct) && ![currentCatalogId.toUpperCase(), currentItemId].includes(declaredProduct)) return false;
            if (/^MLB\d+$/.test(declaredCatalog) && declaredCatalog !== currentCatalogId.toUpperCase()) return false;
            const declaredSeller = clean(root.getAttribute('data-seller-id'));
            if (declaredSeller && scopedSellerId && declaredSeller !== scopedSellerId) return false;
            const declaredProfileUrl = clean(root.getAttribute('data-seller-profile-url'));
            const declaredProfile = identity(declaredProfileUrl);
            if (declaredProfileUrl && !absolute(declaredProfileUrl) || declaredProfile.conflict
                || declaredSeller && declaredProfile.id && declaredSeller !== declaredProfile.id
                || declaredProfile.id && scopedSellerId && declaredProfile.id !== scopedSellerId) return false;
            if (namedProfile(declaredProfile.perfilUrl)
                && [perfilUrl, expectedSellerProfile.perfilUrl].filter(namedProfile)
                    .some(profile => profile.toLowerCase() !== declaredProfile.perfilUrl.toLowerCase())) return false;
            const namedSellerProfiles = new Set([declaredProfile.perfilUrl].filter(namedProfile).map(profile => profile.toLowerCase()));
            for (const anchor of nodes('a[href]', root)) {
                if (anchor.closest(excludedMetricArea + ', [role="dialog"], aside')) continue;
                const target = absolute(anchor.getAttribute('href'));
                if (!target) continue;
                const linkedItem = productId(target), linkedUp = (target.match(/\/up\/(MLBU\d+)/i) || [])[1] || '';
                const linkedCatalog = (target.match(/\/p\/(MLB\d+)/i) || [])[1] || '';
                if (linkedItem && currentItemId && linkedItem !== currentItemId) return false;
                if (linkedUp && linkedUp.toUpperCase() !== currentUpId.toUpperCase()) return false;
                if (linkedCatalog && linkedCatalog.toUpperCase() !== currentCatalogId.toUpperCase()) return false;
                const linkedSeller = identity(target);
                if (linkedSeller.conflict || linkedSeller.id && scopedSellerId && linkedSeller.id !== scopedSellerId) return false;
                if (namedProfile(linkedSeller.perfilUrl)) {
                    if ([perfilUrl, expectedSellerProfile.perfilUrl].filter(namedProfile)
                        .some(profile => profile.toLowerCase() !== linkedSeller.perfilUrl.toLowerCase())) return false;
                    namedSellerProfiles.add(linkedSeller.perfilUrl.toLowerCase());
                    if (namedSellerProfiles.size > 1) return false;
                }
            }
            return true;
        };
        const modernRootState = (() => {
            if (!isProduct(url)) return { root: null, conflict: false };
            if (sellerId && expectedSellerId && sellerId !== expectedSellerId) return { root: null, conflict: true };
            const candidates = nodes(avantDetailRoot, content).filter(root => {
                if (root.closest(cardSelector + ', ' + excludedMetricArea)) return false;
                const rect = root.getBoundingClientRect(), style = window.getComputedStyle(root);
                if (rect.width <= 0 || rect.height <= 0 || style.display === 'none' || style.visibility === 'hidden') return false;
                const panel = root.closest('[role="dialog"], aside, [class*="avantpro"][class*="panel"]');
                return !panel || nodes('a[href]', panel).some(anchor => {
                    const target = absolute(anchor.getAttribute('href'));
                    return currentItemId ? productId(target) === currentItemId : target === url;
                });
            });
            const matching = candidates.filter(rootMatchesDocument);
            return { root: matching.length === 1 ? matching[0] : null,
                conflict: matching.length > 1 || candidates.length > 0 && matching.length === 0 };
        })();
        const metricRows = scope => nodes('.avantpro-product-info-row', scope).concat(
            nodes('.avt-rounded-lg.avt-p-3.avt-text-center, .avt-rounded-full.avt-font-semibold', scope)
                .filter(node => !!modernRootState.root && node.closest(avantDetailRoot) === modernRootState.root)
        );
        const metricLabelValue = row => {
            const modernRoot = row.closest(avantDetailRoot);
            if (modernRoot && modernRoot !== modernRootState.root) return null;
            if (row.matches('.avantpro-product-info-row')) {
                const label = row.querySelector('.avantpro-product-info-row-label');
                const value = row.querySelector('.avantpro-product-info-row-value');
                return label && value ? { label: norm(label.textContent), value: clean(value.getAttribute('title') || value.textContent) } : null;
            }
            if (!modernRoot) return null;
            if (row.matches('.avt-rounded-full')) {
                const value = row.querySelector(':scope > span.avt-min-w-0.avt-flex-1');
                const text = clean(value && value.textContent);
                const sold = text.match(/^(\+?\d[\d.,]*(?:\s*(?:mil|k|mi|milh[aoõ]+es?))?)\s+vendidos?$/i);
                return sold ? { label: 'vendidos', value: sold[1] }
                    : (norm(text) === 'catalogo' ? { label: 'catalogo', value: 'catalogo' } : null);
            }
            const label = row.querySelector(':scope > .avt-mb-1 > span');
            const value = row.querySelector(':scope > .avt-text-base.avt-font-semibold');
            return label && value ? { label: norm(label.textContent), value: clean(value.textContent) } : null;
        };
        const metricNumber = value => {
            let text = clean(value).toLowerCase().replace(/^\+\s*/, '');
            const matched = text.match(/^(\d[\d.,]*)(?:\s*(mil|k|mi|milh[aoõ]+es?))?$/i);
            if (!matched) return null;
            text = matched[1];
            if (/^\d{1,3}(?:\.\d{3})+(?:,\d+)?$/.test(text)) text = text.replace(/\./g, '').replace(',', '.');
            else if (/^\d+(?:,\d+)?$/.test(text)) text = text.replace(',', '.');
            else if (!/^\d+\.\d{1,2}$/.test(text)) return null;
            const suffix = matched[2] || '';
            const amount = Number(text) * (/^(mil|k)$/i.test(suffix) ? 1000 : (suffix ? 1000000 : 1));
            return Number.isFinite(amount) && amount >= 0 && amount <= Number.MAX_SAFE_INTEGER ? amount : null;
        };
        const metricDate = value => {
            const text = clean(value);
            const br = text.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})(?:\s|$)/);
            const iso = text.match(/^(\d{4})-(\d{2})-(\d{2})(?:[T\s]|$)/);
            if (!br && !iso) return '';
            const year = Number(br ? br[3] : iso[1]), month = Number(br ? br[2] : iso[2]), day = Number(br ? br[1] : iso[3]);
            const date = new Date(Date.UTC(year, month - 1, day));
            if (year < 1900 || date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return '';
            return String(day).padStart(2, '0') + '/' + String(month).padStart(2, '0') + '/' + year;
        };
        const readMetrics = rows => {
            const modernRoot = modernRootState.root;
            const metrics = { total: null, totalLabel: '', totalPriority: 0, estimated: null, monthly: null, monthlyLabel: '', monthlyHistorical: null, catalog: false,
                identityConflict: modernRootState.conflict, created: '', sellerName: '',
                sellerId: clean(modernRoot && modernRoot.getAttribute('data-seller-id')),
                perfilUrl: absolute(modernRoot && modernRoot.getAttribute('data-seller-profile-url')), rows: 0 };
            for (const row of rows) {
                const pair = metricLabelValue(row);
                if (!pair) continue;
                const { label, value } = pair;
                if (!label || !value) continue;
                metrics.rows += 1;
                if (/^(?:vendas? (?:do|deste) (?:produto|anuncio|item|catalogo)|quantidade vendida|vendas? do vendedor neste anuncio|vendidos?)$/.test(label)) {
                    const total = metricNumber(value);
                    const priority = /(?:anuncio|item)/.test(label) ? 2 : 1;
                    if (Number.isSafeInteger(total) && priority >= metrics.totalPriority) {
                        metrics.total = total;
                        metrics.totalLabel = label;
                        metrics.totalPriority = priority;
                    }
                } else if (label === 'catalogo') metrics.catalog = true;
                else if (/^vendas? estimadas?$/.test(label)) metrics.estimated = metricNumber(value);
                else if (/^vendas? mensais$/.test(label)) metrics.monthlyHistorical = metricNumber(value.replace(/\s*\/\s*m[eê]s\s*$/i, ''));
                else if (/^ritmo atual(?:\s|\(|$)/.test(label)) {
                    metrics.monthly = metricNumber(value);
                    metrics.monthlyLabel = label;
                } else if (/^(?:anuncio|catalogo) criado em$|^criado em$/.test(label)) metrics.created = metricDate(value);
                else if (/^(?:nome do )?vendedor$/.test(label)) {
                    metrics.sellerName = value;
                    const anchor = row.querySelector('a[href]');
                    const evidence = identity(anchor && anchor.getAttribute('href'));
                    const conflictingProfile = namedProfile(metrics.perfilUrl) && namedProfile(evidence.perfilUrl)
                        && metrics.perfilUrl.toLowerCase() !== evidence.perfilUrl.toLowerCase();
                    metrics.identityConflict = metrics.identityConflict || !!evidence.conflict || conflictingProfile
                        || !!(metrics.sellerId && evidence.id && metrics.sellerId !== evidence.id);
                    metrics.sellerId = metrics.sellerId || evidence.id;
                    metrics.perfilUrl = metrics.perfilUrl || evidence.perfilUrl;
                }
            }
            return metrics;
        };
        const combineMetrics = (previous, current) => {
            if (!previous || current.identityConflict
                || current.sellerId && sellerId && current.sellerId !== sellerId
                || current.sellerName && previous.sellerName && norm(current.sellerName) !== norm(previous.sellerName)
                || current.sellerId && previous.sellerId && current.sellerId !== previous.sellerId
                || current.perfilUrl && previous.perfilUrl && current.perfilUrl !== previous.perfilUrl) return current;
            const combined = { ...previous, rows: Math.max(previous.rows || 0, current.rows || 0) };
            if (Number.isFinite(current.total)) {
                combined.total = current.total; combined.totalLabel = current.totalLabel; combined.totalPriority = current.totalPriority;
            }
            for (const field of ['estimated', 'monthly', 'monthlyHistorical']) if (Number.isFinite(current[field])) combined[field] = current[field];
            for (const field of ['monthlyLabel', 'created', 'sellerName', 'sellerId', 'perfilUrl']) if (current[field]) combined[field] = current[field];
            if (current.sellerId || current.perfilUrl) combined.identityConflict = !!current.identityConflict;
            combined.catalog = !!(previous.catalog || current.catalog);
            return combined;
        };
        const floatingRows = metricRows(document).filter(row => {
            if (row.closest(cardSelector + ', [class*="recommend"], [class*="carousel"], [class*="same-seller"], [class*="eshop"]')) return false;
            const rect = row.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0 && window.getComputedStyle(row).visibility !== 'hidden';
        });
        const floatingRowsFor = (id, itemUrl) => floatingRows.filter(row => {
            const panel = row.closest('[role="dialog"], aside, [class*="avantpro"][class*="panel"]');
            if (!panel) return false;
            return nodes('a[href]', panel).some(anchor => {
                const target = absolute(anchor.getAttribute('href'));
                return id ? productId(target) === id : target === itemUrl;
            });
        });
        const detailNodeBelongs = (node, id, itemUrl) => {
            const modernRoot = node.closest(avantDetailRoot);
            if (modernRoot && modernRoot !== modernRootState.root) return false;
            const panel = node.closest('[role="dialog"], aside, [class*="avantpro"][class*="panel"]');
            if (panel) {
                return nodes('a[href]', panel).some(anchor => {
                    const target = absolute(anchor.getAttribute('href'));
                    return id ? productId(target) === id : target === itemUrl;
                });
            }
            return content.contains(node) && !node.closest(cardSelector + ', [class*="recommend"], [class*="carousel"], [class*="same-seller"], [class*="eshop"]');
        };
        const structuredCache = window.__JK_PM_AVANT_STRUCTURED_CACHE || (window.__JK_PM_AVANT_STRUCTURED_CACHE = Object.create(null));
        const currentClick = window.__JK_AVANT_LAST_CARD_CLICKED || {};
        const resolvedKeys = (window.__JK_AVANT_LAST_QUEUE_RESULT || {}).resolvedKeys || [];
        const resultRoot = content.querySelector('.ui-search-layout, [data-testid="search-results"], .ui-search-results') || content;
        nodes(cardSelector, resultRoot).forEach(node => {
            if (node.closest('[class*="recommend"], [class*="carousel"], .ui-recommendations, [class*="ui-category-trends"]')) return;
            const outer = node.closest('.ui-search-layout__item, [data-testid="search-result"]') || node;
            if (seenElements.has(outer)) return;
            seenElements.add(outer);
            cards.push(outer);
        });
        const anuncios = [], known = new Set();
        for (const card of options && options.detail ? [] : cards) {
            const link = nodes('a[href]', card).find(anchor => isProduct(absolute(anchor.getAttribute('href'))));
            if (!link) continue;
            const productUrl = absolute(link.getAttribute('href'));
            const id = productId(productUrl, card);
            const key = id || productUrl;
            if (known.has(key)) continue;
            known.add(key);
            const sellerLink = nodes('a[href]', card).find(anchor => {
                const info = identity(anchor.getAttribute('href'));
                return info.id || info.perfilUrl;
            });
            const cardIdentity = identity(sellerLink && sellerLink.href);
            const attributedSeller = clean(card.getAttribute('data-seller-id')) || cardIdentity.id;
            const avantRoots = nodes('[class*="avantpro"], [id*="avantpro"], [data-jk-field]', card);
            const avantText = norm(avantRoots.map(node => node.textContent).join(' '));
            const avantLink = avantRoots.flatMap(node => nodes('a[href]', node)).find(anchor => {
                const info = identity(anchor.href); return info.id || info.perfilUrl;
            });
            const avantIdentity = identity(avantLink && avantLink.href);
            const localRows = metricRows(card);
            let metrics = readMetrics(localRows);
            const clickedId = clean(currentClick.id || currentClick.mlb).replace('-', '').toUpperCase();
            const capturedCurrent = !!(id && clickedId === id && resolvedKeys.includes('id:' + id));
            metrics = combineMetrics(structuredCache[key], metrics);
            if (capturedCurrent) metrics = combineMetrics(metrics, readMetrics(floatingRowsFor(id, productUrl)));
            if (metrics.identityConflict) delete structuredCache[key];
            else if (metrics.rows) structuredCache[key] = metrics;
            const image = card.querySelector('img');
            const title = card.querySelector('.poly-component__title, .ui-search-item__title, h2, h3') || link;
            const oldPrice = '.poly-price__old, .andes-money-amount--previous, .andes-money-amount--previous-price, [class*="previous-price"], .price-tag__disabled, s, del';
            const priceScopes = nodes('.poly-price__current, .ui-search-price__second-line', card);
            const priceFractions = priceScopes.flatMap(scope => nodes('.andes-money-amount__fraction, .price-tag-fraction', scope))
                .concat(nodes('.andes-money-amount__fraction, .price-tag-fraction', card));
            const price = priceFractions.find(node => !node.closest(oldPrice));
            const amount = price && (price.closest('.andes-money-amount, .price-tag') || price.parentElement);
            const cents = amount && amount.querySelector('.andes-money-amount__cents, .price-tag-cents');
            const integerPart = price ? Number(clean(price.textContent).replace(/\./g, '').replace(',', '.')) : NaN;
            const centsText = clean(cents && cents.textContent).replace(/\D/g, '');
            const priceValue = Number.isFinite(integerPart) ? integerPart + (centsText ? Number(centsText) / 100 : 0) : null;
            anuncios.push({
                id, key, url: productUrl, titulo: clean(title.textContent), imagem: image ? (image.getAttribute('data-src') || image.currentSrc || image.src || '') : '',
                preco: priceValue,
                posicao: anuncios.length + 1, catalogo: /\/p\/MLB/i.test(productUrl) || !!metrics.catalog || /\bcatalogo\b/.test(metrics.totalLabel),
                seller_id: attributedSeller, seller_perfil_url: cardIdentity.perfilUrl,
                vendedor: clean(sellerLink && sellerLink.textContent),
                _avantEvidence: {
                    present: avantRoots.length > 0,
                    sellerId: avantIdentity.id || metrics.sellerId, perfilUrl: avantIdentity.perfilUrl || metrics.perfilUrl,
                    identityConflict: !!(avantIdentity.conflict || metrics.identityConflict), sellerMetric: metrics.totalPriority === 2, metrics
                }
            });
        }
        // Item pages are used for discovery and optional explicit detail collection.
        if (!anuncios.length && isProduct(url) && document.querySelector('.ui-pdp-title, [data-testid="product-title"]')) {
            const title = document.querySelector('.ui-pdp-title, [data-testid="product-title"]');
            const avantRoots = nodes('[class*="avantpro"], [id*="avantpro"]', content).filter(node => detailNodeBelongs(node, productId(url), url));
            const text = norm(avantRoots.map(node => node.textContent).join(' '));
            const avantLink = avantRoots.flatMap(node => nodes('a[href]', node)).filter(anchor => detailNodeBelongs(anchor, productId(url), url))
                .find(anchor => identity(anchor.href).id || identity(anchor.href).perfilUrl);
            const proof = identity(avantLink && avantLink.href);
            const detailKey = productId(url) || url;
            const metrics = combineMetrics(structuredCache[detailKey], readMetrics(floatingRows.filter(row => detailNodeBelongs(row, productId(url), url))));
            if (metrics.identityConflict) delete structuredCache[detailKey];
            else if (metrics.rows) structuredCache[detailKey] = metrics;
            anuncios.push({ id: productId(url), key: productId(url) || url, url, titulo: clean(title.textContent), catalogo: /\/p\/MLB/i.test(url) || !!metrics.catalog || /\bcatalogo\b/.test(metrics.totalLabel),
                seller_id: sellerId, seller_perfil_url: perfilUrl, vendedor,
                _avantEvidence: { present: avantRoots.length > 0, sellerId: proof.id || metrics.sellerId, perfilUrl: proof.perfilUrl || metrics.perfilUrl, identityConflict: !!(proof.conflict || metrics.identityConflict), sellerMetric: metrics.totalPriority === 2, metrics }
            });
        }
        const categories = [], categorySeen = new Set();
        const categoryIndexPage = /^\/categorias\/?$/.test(location.pathname);
        const excludedCategoryArea = '[class*="carousel"], [class*="recommend"], [class*="trending"], [data-testid="trends"], [class*="breadcrumb"], [aria-label*="breadcrumb" i], [aria-label*="caminho" i], [data-testid="breadcrumb"], .ui-search-filter-hierarchy--parent, .ui-search-filter-hierarchy--selected';
        const parentCategoryPaths = new Set(nodes('[class*="breadcrumb"] a[href], [aria-label*="breadcrumb" i] a[href], [data-testid="breadcrumb"] a[href]', content)
            .map(anchor => categoryPath(absolute(anchor.getAttribute('href')))));
        const currentCategoryPath = categoryPath(url);
        const containers = categoryIndexPage ? nodes('.categories__container, .categories, .category-list, .categories-list, [data-testid="categories"]', content)
            .filter(group => !group.closest(excludedCategoryArea)) : [];
        let categoryLinks = [];
        for (const group of containers.filter(group => !containers.some(other => other !== group && other.contains(group)))) {
            let links = nodes('a[href]', group);
            if (categoryIndexPage) {
                // Category directories can also list grandchildren. Prefer their
                // highest heading links, then the outermost list level.
                const headingLevel = anchor => {
                    const heading = anchor.closest('h1, h2, h3, h4, h5, h6') || anchor.querySelector('h1, h2, h3, h4, h5, h6');
                    return heading ? Number(heading.tagName.slice(1)) : Infinity;
                };
                const titles = links.filter(anchor => anchor.matches('.categories__title-link') || anchor.closest('.categories__title'));
                const bestHeading = Math.min(Infinity, ...links.map(headingLevel));
                if (titles.length) links = titles;
                else if (Number.isFinite(bestHeading)) links = links.filter(anchor => headingLevel(anchor) === bestHeading);
                else {
                    const listDepth = anchor => {
                        let depth = 0;
                        for (let parent = anchor.parentElement; parent && parent !== group; parent = parent.parentElement) {
                            if (parent.matches('ul, ol')) depth += 1;
                        }
                        return depth;
                    };
                    const shallowest = Math.min(Infinity, ...links.map(listDepth));
                    links = links.filter(anchor => listDepth(anchor) === shallowest);
                }
            }
            categoryLinks = categoryLinks.concat(links);
        }
        // Mercado Livre has many filter groups. Only Category is a tree edge;
        // price, location, shipping and sort links are not child categories.
        const groupSelector = '.ui-search-filter-container, .ui-search-filter-dl, [data-filter-id], [data-testid="category-filter"]';
        const groupKind = group => {
            const title = nodes('.ui-search-filter-dt, .ui-search-filter-dt-title, .ui-search-filter-title, h2, h3, legend', group)
                .find(node => node.closest(groupSelector) === group);
            const filterId = group.getAttribute('data-filter-id');
            if (filterId) return filterId === 'category' ? 'category' : 'other';
            if (group.getAttribute('data-testid') === 'category-filter') return 'category';
            return title ? (/^categorias?$/.test(norm(title.textContent)) ? 'category' : 'other') : '';
        };
        const groups = nodes(groupSelector, content);
        const belongsToCategoryGroup = (anchor, group) => {
            for (let parent = anchor.parentElement; parent && parent !== group; parent = parent.parentElement) {
                if (parent.matches(groupSelector) && groupKind(parent) === 'other') return false;
            }
            return true;
        };
        const categoryModal = /_FiltersAvailableSidebar$/.test(location.pathname)
            && location.search === '?filter=category' && content.querySelector('.ui-search-modal');
        if (categoryModal) categoryLinks.push(...nodes('a.ui-search-search-modal-filter[href]', categoryModal));
        for (const group of groups) {
            if (groupKind(group) === 'category') {
                for (const anchor of nodes('a[href]', group)) {
                    if (anchor.closest(excludedCategoryArea) || !belongsToCategoryGroup(anchor, group)) continue;
                    rememberCategoryExpansion(anchor.getAttribute('href'));
                }
                categoryLinks = categoryLinks.concat(nodes('a[href]', group).filter(anchor => belongsToCategoryGroup(anchor, group)));
            }
        }
        const categoryData = anchor => {
            // The public category index still links this one root through HTTP.
            // Its confirmed HTTPS destination is a category hub, not a global
            // permission to follow HTTP links in listings or seller collection.
            const href = anchor.getAttribute('href');
            let categoryUrl = absolute(categoryIndexPage && href === 'http://www.mercadolivre.com.br/imoveis/'
                ? 'https://www.mercadolivre.com.br/c/imoveis' : href);
            if (categoryModal && categoryUrl) {
                const modalLink = new URL(categoryUrl);
                if (modalLink.search === '?filter=category') { modalLink.search = ''; categoryUrl = modalLink.href; }
            }
            const name = clean(anchor.textContent);
            const label = norm(name);
            if (anchor.closest(excludedCategoryArea) || anchor.matches('[aria-current="page"], [aria-disabled="true"]')) return null;
            if (categoryExpansionUrl(href)) return null;
            if (/^(?:mostrar (?:mais|menos)|ver (?:tudo|todos(?: os produtos)?|mais)|(?:ver )?todas?(?: as)? categorias|voltar(?:\b.*)?|retornar(?:\b.*)?|pagina anterior|anterior)$/.test(label)) return null;
            if (!categoryUrl || !name || isProduct(categoryUrl) || identity(categoryUrl).id || identity(categoryUrl).perfilUrl || categoryUrl === url) return null;
            const destination = new URL(categoryUrl), candidatePath = categoryPath(categoryUrl);
            if (!(['lista.mercadolivre.com.br', 'imoveis.mercadolivre.com.br', 'servicos.mercadolivre.com.br'].includes(destination.hostname)
                || /^\/(?:c|categorias)\//.test(destination.pathname))) return null;
            if (!candidatePath || candidatePath === '/categorias' || candidatePath === currentCategoryPath || parentCategoryPaths.has(candidatePath)) return null;
            if (!categoryIndexPage && currentCategoryPath.startsWith(candidatePath + '/')) return null;
            if (/_?(?:PriceRange|OrderId|Desde|CustoEnvio|SHIPPING|ITEM_CONDITION|BRAND|LOCATION)_|[?&](?:page|offset|sort|price|shipping|brand|state|city)=/i.test(categoryUrl)) return null;
            const id = categoryUrl.match(/(?:_CATEGORY_|\/)(MLB\d+)/i);
            return { key: id ? id[1].toUpperCase() : categoryUrl, nome: name, url: categoryUrl };
        };
        for (const anchor of categoryLinks) {
            const category = categoryData(anchor);
            if (!category || categorySeen.has(category.url)) continue;
            categorySeen.add(category.url);
            categories.push(category);
        }
        // Nordic exposes the complete Category filter, including values hidden
        // behind "Mostrar mais". IDs are read together with their own permalinks.
        for (const category of categoryStructuredNavigation && categoryStructuredNavigation.categories || []) {
            const destination = new URL(category.url);
            if (!/^MLB\d+$/.test(category.key) || destination.search || destination.username || destination.password
                || /_(?:Desde|OrderId|PriceRange|SHIPPING|BRAND|ITEM_CONDITION|Container)_/i.test(destination.pathname)) continue;
            const previous = categories.findIndex(item => item.url === category.url);
            if (previous >= 0) categories[previous] = category;
            else { categorySeen.add(category.url); categories.push(category); }
        }
        // The canonical index exposes both roots and their direct children.
        // /c/... can be a marketing landing; do not infer its tree from carousels.
        const categoryTree = categoryIndexPage ? categories.map(category => ({ ...category, children: [] })) : [];
        if (categoryIndexPage) {
            for (const group of nodes('.categories__container', content)) {
                const title = group.querySelector('.categories__title-link, .categories__title a[href]');
                const rootCategory = title && categoryData(title);
                const tree = rootCategory && categoryTree.find(category => category.url === rootCategory.url);
                if (!tree) continue;
                const seenChildren = new Set();
                tree.children = nodes('.categories__list a[href]', group)
                    .filter(anchor => anchor.closest('.categories__container') === group)
                    .map(categoryData)
                    .filter(child => {
                        if (!child || child.url === tree.url || seenChildren.has(child.url)) return false;
                        seenChildren.add(child.url);
                        return true;
                    });
            }
        }
        const categoryIndex = nodes('a[href]').find(anchor => /^(?:ver (?:mais|todas as)|todas as|mais) categorias$/.test(norm(anchor.textContent)) && /\/categorias\/?(?:$|\?)/.test(absolute(anchor.href)));
        const categoriesIndexUrl = absolute(categoryIndex && categoryIndex.href);
        // The ML rescue component marks an unrecognized search route. An ID
        // merely present in that route cannot prove a legitimate empty category.
        const categoryRescue = nodes('.ui-search-rescue .ui-search-rescue__title', content)
            .some(title => !title.closest(excludedCategoryArea) && title.getClientRects().length > 0
                && norm(title.textContent) === 'nao encontramos resultados para a sua busca');
        const categoryHeading = nodes('h1', content).some(heading => clean(heading.textContent)
            && !heading.closest(excludedCategoryArea + ', .ui-search-rescue'));
        const categoryBreadcrumb = nodes('[class*="breadcrumb"] a[href], [aria-label*="breadcrumb" i] a[href], [aria-label*="caminho" i] a[href], [data-testid="breadcrumb"] a[href]', content)
            .some(anchor => {
                const href = absolute(anchor.getAttribute('href'));
                if (!href || isProduct(href) || identity(href).id || identity(href).perfilUrl) return false;
                const parsed = new URL(href), pathname = categoryPath(href);
                return (['lista.mercadolivre.com.br', 'imoveis.mercadolivre.com.br', 'servicos.mercadolivre.com.br'].includes(parsed.hostname)
                    && pathname && pathname !== '/') || (['www.mercadolivre.com.br', 'mercadolivre.com.br'].includes(parsed.hostname)
                    && /^\/(?:c|categorias)\/[^/]+/.test(pathname));
            });
        const categoryNotFound = categoryRescue && anuncios.length === 0 && !isProduct(url)
            && !currentIdentity.id && !currentIdentity.perfilUrl && !authRequired
            && !categoryHeading && !categoryBreadcrumb && !categoryDocumentIdentity;
        const scrolling = document.scrollingElement || document.documentElement || document.body || {};
        const y = Number(window.scrollY || scrolling.scrollTop || 0), height = Number(scrolling.scrollHeight || 0), viewport = Number(window.innerHeight || 800);
        const categoryTrends = !!document.querySelector('[class*="ui-category-trends"]');
        // Some official Trends hubs are static HTML without category metadata.
        // This descriptor authorizes only examining child hrefs; it cannot
        // establish a category ID or make recommendation cards into listings.
        const categoryNavigationHub = (() => {
            if (!categoryTrends || categoryBrowse || categoryDocumentIdentity || categoryIdentityConflict
                || authRequired || avantDialog || currentIdentity.id || currentIdentity.perfilUrl || isProduct(url)) return null;
            const headings = nodes('h1', content).filter(node => !node.closest(excludedCategoryArea));
            const canonicals = nodes('link[rel="canonical"]');
            if (headings.length !== 1 || canonicals.length !== 1) return null;
            const heading = clean(headings[0].textContent);
            if (!heading || heading.length > 400) return null;
            try {
                const current = new URL(location.href), canonical = new URL(canonicals[0].getAttribute('href'), current.href);
                if (current.protocol !== 'https:' || current.hostname !== 'lista.mercadolivre.com.br'
                    || current.username || current.password || current.port && current.port !== '443'
                    || current.search || current.hash || canonical.href !== current.href) return null;
                return { version: 1, url: current.href, canonical: canonical.href, heading };
            } catch (_error) { return null; }
        })();
        return { url, effectiveCategoryId, categoryDocumentIdentity, categoryIdentityConflict, categoryContext, categoryStructuredNavigation, categoryBrowse, categoryFilterMoreUrl, categoryItems,
            categoryTrends, categoryNavigationHub,
            readyState: document.readyState, categories, categoryTree, categoriesIndexUrl, listingUrl, anuncios, nextUrl, hasNext, perfilUrl, sellerId, vendedor, seller_identity_conflict: !!primaryIdentity.conflict || headerConflict, seller_name_kind: primaryStore && !headerConflict ? 'store_brand' : 'seller', allAdsUrl,
            authRequired: !!authRequired, avantAuthRequired: !!avantDialog, hasChildren: categories.length > 0, categoryNotFound,
            categoryFilterPresent: groups.some(group => groupKind(group) === 'category'),
            empty: /nao (?:ha|encontramos|temos) (?:anuncios|produtos|resultados)|nenhum (?:anuncio|produto|resultado)/.test(bodyText),
            y, height, viewport, atBottom: y + viewport >= height - 16
        };
    }

    function clickNextCategoryPage(expectedUrl) {
        const canonical = value => {
            try {
                const url = new URL(value, location.href);
                if (url.protocol !== 'https:' || !/(^|\.)mercadolivre\.com\.br$/i.test(url.hostname)) return '';
                url.hash = '';
                for (const key of ['tracking_id', 'position', 'polycard_client', 'sid', 'reco_id', 'searchVariation']) url.searchParams.delete(key);
                return url.href;
            } catch (_error) { return ''; }
        };
        const current = canonical(location.href);
        if (!expectedUrl || !current || current !== canonical(expectedUrl)) return { clicked: false, hasNext: false, reason: 'category_url_mismatch' };
        if (/_CustId_|_SellerId_|[?&](?:seller_id|custid|cust_id)=|\/(?:perfil|loja|stores?|p|up)\/|\/MLB-\d+/i.test(current)) return { clicked: false, hasNext: false, reason: 'not_category_page' };
        const next = document.querySelector('a[rel="next"], .andes-pagination__button--next a, [data-andes-pagination-control="next"], a.andes-pagination__link[title="Seguinte"], a[aria-label="Seguinte"]');
        if (!next || next.matches('[disabled], [aria-disabled="true"]') || next.closest('[aria-disabled="true"], .andes-pagination__button--disabled')) return { clicked: false, hasNext: false, reason: 'no_next_page' };
        const destination = next.getAttribute('href');
        if (destination && !canonical(destination)) return { clicked: false, hasNext: false, reason: 'invalid_next_url' };
        next.click();
        return { clicked: true, hasNext: true, reason: 'next_clicked' };
    }

    async function withItemLinks(extract) {
        // Favoritos' parser predates #wid links. Preserve that identity while
        // its existing DOM parser runs, then restore the exact original hrefs.
        const changed = [];
        for (const anchor of document.querySelectorAll('a[href*="#"]')) {
            try {
                const original = anchor.getAttribute('href');
                const url = new URL(original, location.href);
                if (url.protocol !== 'https:' || !/(^|\.)mercadolivre\.com\.br$/i.test(url.hostname) || url.searchParams.has('wid')) continue;
                let wid = new URLSearchParams(url.hash.slice(1)).get('wid');
                if (!wid) {
                    try { wid = new URLSearchParams(decodeURIComponent(url.hash.slice(1))).get('wid'); } catch (_error) {}
                }
                if (!/^MLB\d{6,}$/i.test(wid || '')) continue;
                url.searchParams.set('wid', wid.toUpperCase());
                url.hash = '';
                const canonical = url.href;
                anchor.setAttribute('href', canonical);
                changed.push({ anchor, original, canonical });
            } catch (_error) {}
        }
        try { return await extract(); }
        finally {
            for (const item of changed) {
                if (item.anchor.getAttribute('href') === item.canonical) item.anchor.setAttribute('href', item.original);
            }
        }
    }

    function mergeAvant(page, raw, seller) {
        const norm = value => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]/g, '');
        const profile = value => {
            try {
                const url = new URL(value);
                const store = url.pathname.match(/^\/loja\/([^/]+)/i);
                if (store) return 'https://www.mercadolivre.com.br/loja/' + store[1].toLowerCase();
                return (url.origin + url.pathname.replace(/\/$/, '')).toLowerCase();
            } catch (_err) { return ''; }
        };
        const wantedId = String(seller.sellerId || seller.seller_id || seller.id || '').replace(/^seller:/, '');
        const wantedProfile = profile(seller.perfilUrl || seller.seller_perfil_url);
        const same = (id, url) => wantedId && id ? wantedId === String(id) : !!(wantedProfile && profile(url) === wantedProfile);
        const pageMatches = same(page.sellerId, page.perfilUrl);
        const avant = Array.isArray(raw && raw.anuncios) ? raw.anuncios : [];
        page.anuncios = page.anuncios.map(base => {
            const found = avant.find(item => (base.id && String(item.id || item.mlb).replace('-', '') === base.id) || (item.url && item.url.split('#')[0] === base.url.split('#')[0])) || {};
            const evidence = base._avantEvidence || {};
            const metrics = evidence.metrics || {};
            const idConflict = !!(base.seller_id && wantedId && String(base.seller_id) !== wantedId);
            const directMatches = same(base.seller_id, base.seller_perfil_url);
            const scoped = !idConflict && (directMatches || ((!base.seller_id && !base.seller_perfil_url) && pageMatches));
            const sellerId = base.seller_id || (scoped ? page.sellerId : '');
            const sellerProfile = base.seller_perfil_url || (scoped ? page.perfilUrl : '');
            const strongNumeric = wantedId && (String(base.seller_id || '') === wantedId || String(evidence.sellerId || '') === wantedId);
            const storeBrand = /\/loja\//.test(wantedProfile) || seller.seller_name_kind === 'store_brand';
            const meaningfulName = value => {
                const text = String(value || '').replace(/\s+/g, ' ').trim();
                const normalized = text.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
                return !text || text === wantedId || /^(?:id:)?[\d\s.,_-]+$/.test(normalized)
                    || /^(?:(?:ir para|visitar|acessar|ver|conhecer) (?:a |o )?)?(?:loja(?: oficial)?|perfil(?: do vendedor)?|vendedor|todos os (?:produtos|anuncios))$/.test(normalized)
                    || /^(?:ver|veja) (?:todos os |mais )?(?:produtos|anuncios)$/.test(normalized)
                    || /^(?:nome (?:pendente|nao identificado)|vendedor (?:nao identificado|desconhecido))\b/.test(normalized)
                || /^(?:vendedor|seller|comprar|saiba mais)$/.test(normalized) ? '' : text;
            };
            const observedName = meaningfulName(metrics.sellerName) || meaningfulName(found.vendedor);
            const identityConflict = !!evidence.identityConflict || !!((evidence.sellerId || evidence.perfilUrl) && !same(evidence.sellerId, evidence.perfilUrl));
            const targetName = meaningfulName(seller.vendedor || seller.nome);
            const nameConflict = identityConflict || !!(!strongNumeric && !storeBrand && targetName && observedName && norm(targetName) !== norm(observedName));
            // Only the explicit structured label is a total. Favoritos' historical
            // fallback may substitute estimated sales or parse PT-BR thousands as decimals.
            const source = 'avantpro_dom_estruturado';
            const monthlySource = 'avantpro_dom_estruturado';
            const sales = metrics.total;
            const monthly = Number.isFinite(metrics.monthlyHistorical) ? metrics.monthlyHistorical : metrics.monthly;
            const validSales = Number.isFinite(sales) && sales >= 0;
            const validMonthly = Number.isFinite(monthly) && monthly >= 0;
            const catalogueProof = !base.catalogo || (evidence.sellerMetric && same(evidence.sellerId, evidence.perfilUrl));
            const rankable = scoped && !nameConflict && catalogueProof && validSales && (validMonthly || !!metrics.created);
            const item = { ...base, vendedor: nameConflict ? '' : observedName || meaningfulName(base.vendedor) || meaningfulName(page.vendedor),
                seller_name_kind: observedName ? 'seller' : page.seller_name_kind || 'seller',
                seller_name_source: observedName ? 'avant' : 'perfil',
                seller_id: sellerId, seller_perfil_url: sellerProfile,
                avant_seller_id: evidence.sellerId || '', avant_seller_perfil_url: evidence.perfilUrl || '',
                seller_confirmed: scoped && !nameConflict, vendas: validSales ? sales : null,
                vendasFonte: validSales ? source : '', vendas_fonte: validSales ? source : '',
                vendas_rotulo: metrics.totalLabel || '', vendas_estimadas: Number.isFinite(metrics.estimated) ? metrics.estimated : null,
                media_mensal: validMonthly ? monthly : null, media_mensal_fonte: validMonthly ? monthlySource : '',
                ritmo_atual: Number.isFinite(metrics.monthly) ? metrics.monthly : null,
                data_criacao: metrics.created || '', dataCriacaoFonte: metrics.created ? source : '',
                rankable, status: rankable ? 'analisado' : (!scoped || nameConflict ? 'vendedor_nao_confirmado' : (!catalogueProof ? 'catalogo_sem_evidencia_vendedor' : (!validSales ? 'sem_quantidade_avant' : 'sem_dados_avant')))
            };
            item.erro = rankable ? '' : (!scoped || nameConflict ? 'Nao foi possivel confirmar que o anuncio pertence ao vendedor pesquisado.' : (!catalogueProof ? 'O catalogo nao apresentou vendas Avant Pro comprovadas para este vendedor.' : (!validSales ? 'O Avant Pro ainda nao apresentou a quantidade de vendas deste anuncio.' : 'O Avant Pro ainda nao apresentou ritmo mensal ou data de criacao para calcular a media deste anuncio.')));
            delete item._avantEvidence;
            return item;
        });
        return page;
    }

    async function clickDetailAvant(pending) {
        const item = Array.isArray(pending) && pending[0];
        if (!item) return { clicked: 0, pendingKeys: [] };
        const key = item.id ? 'id:' + item.id : 'url:' + item.url;
        const attempts = window.__JK_PM_AVANT_DETAIL_CLICKS || (window.__JK_PM_AVANT_DETAIL_CLICKS = Object.create(null));
        if ((attempts[key] || 0) >= 3) return { clicked: 0, pendingKeys: [] };
        const normalize = value => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
        const visible = node => {
            const rect = node.getBoundingClientRect(), style = window.getComputedStyle(node);
            return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
        };
        const scope = document.querySelector('main') || document.body;
        const controls = Array.from(scope.querySelectorAll('button, [role="button"], input[type="button"], input[type="submit"], [class*="andes-button"]'));
        const control = controls.find(node => {
            if (!visible(node) || node.matches('[disabled], [aria-disabled="true"]')
                || node.closest('[aria-disabled="true"], [class*="recommend"], [class*="carousel"], [class*="eshop"], [class*="same-seller"], a[href]')) return false;
            const text = normalize([node.textContent, node.value, node.getAttribute('aria-label'), node.getAttribute('title')].join(' '));
            if (/login|entrar|vincular|baixar|download|imagens?|images?|fotos?|photos?|zip|exportar|salvar|gallery|galeria|assinar|suporte/.test(text)) return false;
            return /informacoes?\s+avant|informacoes?\s+avantpro|avantpro\s+info|carregar\s+dados\s+avant|dados\s+avantpro|dados\s+avant\s*pro/.test(text);
        });
        if (!control) return { clicked: 0, pendingKeys: [] };
        attempts[key] = (attempts[key] || 0) + 1;
        control.scrollIntoView({ block: 'center', inline: 'center' });
        control.click();
        await new Promise(resolve => setTimeout(resolve, 160));
        return { clicked: 1, pendingKeys: [key] };
    }

    function create(options) {
        const render = options && options.renderAvantScript || defaultRender;
        const discoveryScript = () => '/*pm:discovery*/(' + readPage.toString() + ')()';
        const snapshotScript = options => {
            const seller = options && options.seller || {};
            const extract = render('extrair-anuncios-avant-pro-dom-webview-1', { p0: String(Number.MAX_SAFE_INTEGER) }).trim().replace(/;\s*$/, '');
            return '/*pm:snapshot*/(async function(){const page=(' + readPage.toString() + ')(' + JSON.stringify({ detail: !!(options && options.detail), seller }) + ');const avant=await (' + withItemLinks.toString() + ')(()=>(' + extract + '));return (' + mergeAvant.toString() + ')(page,avant,' + JSON.stringify(seller) + ');})()';
        };
        const clickAvantScript = options => {
            if (options && options.detail) return '/*pm:avant-click*/(' + clickDetailAvant.toString() + ')(' + JSON.stringify(options.pending || []) + ')';
            const script = render('acionar-cards-avant-pro-fila-webview-1', { p0: '1', p1: '3', p2: '4500', p3: 'true', p4: 'true' }).trim().replace(/;\s*$/, '');
            const pending = options && Array.isArray(options.pending) ? options.pending : null;
            const keys = pending && pending.map(item => item.id ? 'id:' + String(item.id).replace('-', '').toUpperCase() : 'url:' + String(item.url || item.key || ''));
            return '/*pm:avant-click*/(async function(){const previous=window.__JK_PM_AVANT_PENDING_KEYS;try{'
                + (keys ? 'window.__JK_PM_AVANT_PENDING_KEYS=Object.fromEntries(' + JSON.stringify(keys) + '.map(key=>[key,true]));' : '')
                + 'return await (' + withItemLinks.toString() + ')(()=>(' + script + '));'
                + '}finally{if(previous===undefined)delete window.__JK_PM_AVANT_PENDING_KEYS;else window.__JK_PM_AVANT_PENDING_KEYS=previous;}})()';
        };
        const scrollScript = target => '/*pm:scroll*/(function(){const el=document.scrollingElement||document.documentElement;const y=' + (target === 'top' ? '0' : 'Math.min(el.scrollHeight, (window.scrollY||el.scrollTop||0)+Math.max(350,window.innerHeight*0.75))') + ';window.scrollTo(0,y);return {y:y,height:el.scrollHeight};})()';
        const prepareScript = () => '/*pm:prepare*/window.__JK_PM_AVANT_STRUCTURED_CACHE=Object.create(null);window.__JK_PM_AVANT_DETAIL_CLICKS=Object.create(null);' + render('coletar-primeira-pagina-favoritos-controlada-1', {});
        const nextCategoryPageScript = expectedUrl => '/*pm:next-category*/(' + clickNextCategoryPage.toString() + ')(' + JSON.stringify(String(expectedUrl || '')) + ')';
        return Object.freeze({ discoveryScript, sellerInfoScript: discoveryScript, snapshotScript, clickAvantScript, scrollScript, prepareScript, nextCategoryPageScript });
    }
    return Object.freeze({ create, ...create() });
});
