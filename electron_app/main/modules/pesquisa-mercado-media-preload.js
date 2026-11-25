'use strict';

function productPage(value) {
    try {
        const url = new URL(value);
        if (url.protocol !== 'https:' || url.port || url.username || url.password
            || !/^(?:(?:www|produto)\.)?mercadolivre\.com\.br$/i.test(url.hostname)) return null;
        const direct = url.pathname.match(/^\/MLB-(\d{6,})(?:-|\/|$)/i);
        if (!direct && !/\/(?:p\/MLB|up\/MLBU?)\d{6,}\/?$/i.test(url.pathname)) return null;
        const fragment = url.hash.slice(1);
        let hashWid = new URLSearchParams(fragment).get('wid');
        if (!hashWid && fragment.includes('%')) hashWid = new URLSearchParams(decodeURIComponent(fragment)).get('wid');
        const ids = [direct && 'MLB' + direct[1], ...url.searchParams.getAll('wid'), hashWid].filter(Boolean);
        if (ids.some(id => !/^MLB\d{6,}$/i.test(id) || id.toUpperCase() !== ids[0].toUpperCase())) return null;
        return { href: url.href, id: ids.length ? ids[0].toUpperCase() : '' };
    } catch (_error) { return null; }
}

function deferNativeRecommendationMedia(state, productPage, onRelease) {
    if (window.top !== window || !productPage(location.href)) return;
    if (!state || !Number.isSafeInteger(state.token)) return;
    const expected = productPage(state.url);
    if (!expected) return;
    const itemId = String(state.itemId || '').replace(/^MLB-/i, 'MLB').toUpperCase();
    if (itemId && (!/^MLB\d{6,}$/.test(itemId) || (expected.id && expected.id !== itemId))) return;
    const expectedId = itemId || expected.id;
    const matchesPage = () => {
        const page = productPage(location.href);
        return page && (page.id && expectedId ? page.id === expectedId : page.href === expected.href);
    };
    if (!matchesPage()) return;

    const recommendations = '.ui-recommendations, .ui-recommendations-container, .ui-recommendations-grid, .ui-recommendations-card';
    const protectedContent = '[class*="ui-pdp-gallery"], #productInfo, [class*="avantpro" i], [id*="avantpro" i]';
    const held = new Map();
    let currentPage = location.href;

    function isHttpSource(value, node) {
        if (!value || !value.trim()) return false;
        try { return /^https?:$/.test(new URL(value, node.baseURI).protocol); }
        catch (_error) { return false; }
    }

    function sourceNodes(video) {
        return [video, ...Array.from(video.children).filter(node => node.localName === 'source')];
    }

    function usesNativeUrls(video, sources) {
        return !video.srcObject && (!video.currentSrc || isHttpSource(video.currentSrc, video))
            && sources.every(node => !node.hasAttribute('src') || isHttpSource(node.getAttribute('src'), node));
    }

    function restore(node) {
        if (!held.has(node)) return false;
        const value = held.get(node);
        held.delete(node);
        // A replacement made by the page takes precedence over the deferred URL.
        if (node.hasAttribute('src')) return false;
        node.setAttribute('src', value);
        return true;
    }

    function inspect(video) {
        if (!video.isConnected) return;
        const sources = sourceNodes(video);
        const native = usesNativeUrls(video, sources);
        const defer = native && video.closest(recommendations) && !video.closest(protectedContent);
        let removed = false, restoredChild = false;
        for (const node of sources) {
            if (defer) {
                if (!node.hasAttribute('src')) continue;
                held.set(node, node.getAttribute('src'));
                node.removeAttribute('src');
                removed = true;
            } else if (restore(node) && node !== video) restoredChild = true;
        }
        // Removing a selected <source> alone does not cancel its current download.
        if (removed && (video.currentSrc || video.networkState === video.NETWORK_LOADING)) video.load();
        if (restoredChild && native && !video.hasAttribute('src')) video.load();
    }

    function inspectTree(node) {
        if (node.nodeType !== 1) return;
        if (node.localName === 'video') inspect(node);
        else if (node.localName === 'source') {
            if (node.parentElement?.localName === 'video') inspect(node.parentElement);
            else restore(node);
        } else if (node.childElementCount) node.querySelectorAll('video').forEach(inspect);
    }

    const observer = new MutationObserver(records => {
        if (currentPage !== location.href) {
            if (!matchesPage()) { release(); return; }
            currentPage = location.href;
        }
        for (const record of records) {
            if (record.type === 'attributes') {
                if (record.target.matches('video, source')) inspectTree(record.target);
            } else record.addedNodes.forEach(inspectTree);
        }
    });
    const unlisten = onRelease(token => { if (token === state.token) release(); });
    function release() {
        observer.disconnect();
        unlisten();
        window.removeEventListener('pagehide', release);
        const players = new Set();
        for (const node of held.keys()) {
            if (restore(node) && node.localName === 'source' && node.parentElement?.localName === 'video') {
                players.add(node.parentElement);
            }
        }
        for (const video of players) {
            if (video.isConnected && !video.hasAttribute('src') && usesNativeUrls(video, sourceNodes(video))) video.load();
        }
    }

    window.addEventListener('pagehide', release, { once: true });
    observer.observe(document, { subtree: true, childList: true, attributes: true, attributeFilter: ['src'] });
    document.querySelectorAll('video').forEach(inspect);
}

const releaseChannel = 'jk-pesquisa-mercado:release-recommendation-media';

if (process.type === 'renderer') {
    if (window.top === window && productPage(location.href)) {
        const { ipcRenderer } = require('electron');
        const state = ipcRenderer.sendSync('jk-pesquisa-mercado:recommendation-media-state');
        deferNativeRecommendationMedia(state, productPage, callback => {
            const listener = (_event, token) => callback(token);
            ipcRenderer.on(releaseChannel, listener);
            return () => ipcRenderer.removeListener(releaseChannel, listener);
        });
    }
} else {
    module.exports.createMediaScript = state => `(${deferNativeRecommendationMedia})(${JSON.stringify(state)}, ${productPage}, callback => {
        const listener = event => callback(event.detail);
        window.addEventListener(${JSON.stringify(releaseChannel)}, listener);
        return () => window.removeEventListener(${JSON.stringify(releaseChannel)}, listener);
    });`;
}
