const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
    getMac: () => ipcRenderer.invoke('get-mac'),
    getAppVersion: () => ipcRenderer.invoke('get-app-version'),
    getNativeWindowHandle: () => ipcRenderer.invoke('get-native-window-handle'),
    openRustDeskInApp: (payload, authToken) => ipcRenderer.invoke('rustdesk-open-in-app', payload || {}, authToken || ''),
    dockRustDeskInApp: (payload, authToken) => ipcRenderer.invoke('rustdesk-dock-in-app', payload || {}, authToken || ''),
    hideRustDeskInApp: (payload, authToken) => ipcRenderer.invoke('rustdesk-hide-in-app', payload || {}, authToken || ''),
    getMachineInfo: () => ipcRenderer.invoke('get-machine-info'),
    openContextVault: (authToken) => ipcRenderer.invoke(
        'context-vault-open',
        typeof authToken === 'string' ? authToken : ''
    ),
    getClientConfig: () => ipcRenderer.invoke('get-client-config'),
    saveClientConfig: (appUrl) => ipcRenderer.invoke('save-client-config', appUrl),
    checkForUpdates: () => ipcRenderer.invoke('check-for-updates'),
    installUpdateNow: () => ipcRenderer.invoke('install-update-now'),
    onAutoUpdateStatus: (callback) => {
        if (typeof callback !== 'function') return () => {};
        const listener = (_event, payload) => callback(payload);
        ipcRenderer.on('auto-update-status', listener);
        return () => ipcRenderer.removeListener('auto-update-status', listener);
    },
    getBrowserSessionPartition: () => ipcRenderer.invoke('get-browser-session-partition'),
    ensureBrowserExtensions: () => ipcRenderer.invoke('ensure-browser-extensions'),
    getBrowserExtensionSettings: () => ipcRenderer.invoke('get-browser-extension-settings'),
    setAvantProExtensionEnabled: (enabled) => ipcRenderer.invoke('set-avantpro-extension-enabled', !!enabled),
    getAvantProStorageStatus: () => ipcRenderer.invoke('avantpro-storage-status'),
    saveAvantProStorageSnapshot: (reason, details) => ipcRenderer.invoke('avantpro-storage-save-current', reason || 'manual', details || {}),
    restoreAvantProStorageSnapshot: (reason, details, options) => ipcRenderer.invoke('avantpro-storage-restore-last-good', reason || 'manual', details || {}, options || {}),
    flushBrowserSession: () => ipcRenderer.invoke('flush-browser-session'),
    setMlAutomationActive: (active, reason) => ipcRenderer.invoke('set-ml-automation-active', !!active, reason || ''),
    getMlPublicItemInfo: (itemId) => ipcRenderer.invoke('ml-public-item-info', itemId),
    getMlBrowserItemInfo: (itemId, url) => ipcRenderer.invoke('ml-browser-item-info', itemId, url),
    openInternalBrowser: (url) => ipcRenderer.invoke('open-internal-browser', url),
    openDetachedInternalBrowser: (url, title) => ipcRenderer.invoke('open-detached-internal-browser', url, title || ''),
    openExternalChrome: (url) => ipcRenderer.invoke('open-external-chrome', url),
    extractMlSearchResults: (url) => ipcRenderer.invoke('extract-ml-search-results', url),
    startFavoritosJobBrowserBackground: (url) => ipcRenderer.invoke('favoritos-job-browser-start', url || ''),
    stopFavoritosJobBrowserBackground: () => ipcRenderer.invoke('favoritos-job-browser-stop'),
    startFavoritosWorkerBrowser: (url, workerId = 'w0', options = {}) => ipcRenderer.invoke('favoritos-worker:start', url || '', workerId || 'w0', options || {}),
    pauseFavoritosWorkerBrowser: (workerId = 'w0') => ipcRenderer.invoke('favoritos-worker:pause', workerId || 'w0'),
    resumeFavoritosWorkerBrowser: (workerId = 'w0') => ipcRenderer.invoke('favoritos-worker:resume', workerId || 'w0'),
    cancelFavoritosWorkerBrowser: (workerId = 'w0') => ipcRenderer.invoke('favoritos-worker:cancel', workerId || 'w0'),
    getFavoritosWorkerBrowserStatus: (workerId = 'w0') => ipcRenderer.invoke('favoritos-worker:status', workerId || 'w0'),
    showFavoritosWorkerBrowser: (workerId = 'w0') => ipcRenderer.invoke('favoritos-worker:show', workerId || 'w0'),
    hideFavoritosWorkerBrowser: (workerId = 'w0') => ipcRenderer.invoke('favoritos-worker:hide', workerId || 'w0'),
    stopFavoritosWorkerBrowser: (options, workerId = 'w0') => ipcRenderer.invoke('favoritos-worker:stop', options || {}, workerId || 'w0'),
    executeFavoritosWorkerBrowser: (code, workerId = 'w0') => ipcRenderer.invoke('favoritos-worker:execute', code, workerId || 'w0'),
    clickFavoritosWorkerBrowser: (point, workerId = 'w0') => ipcRenderer.invoke('favoritos-worker:click', point || {}, workerId || 'w0'),
    typeFavoritosWorkerBrowser: (payload, workerId = 'w0') => ipcRenderer.invoke('favoritos-worker:type', payload || {}, workerId || 'w0'),
    startFavoritosWorkersPool: (payload) => ipcRenderer.invoke('favoritos-workers:start-pool', payload || {}),
    getFavoritosWorkersPoolStatus: () => ipcRenderer.invoke('favoritos-workers:status'),
    pauseFavoritosWorkersPool: () => ipcRenderer.invoke('favoritos-workers:pause'),
    resumeFavoritosWorkersPool: () => ipcRenderer.invoke('favoritos-workers:resume'),
    showFavoritosWorkersPool: () => ipcRenderer.invoke('favoritos-workers:show'),
    hideFavoritosWorkersPool: () => ipcRenderer.invoke('favoritos-workers:hide'),
    stopFavoritosWorkersPool: (options) => ipcRenderer.invoke('favoritos-workers:stop-pool', options || {}),
    startFavoritosWorkerPool: (payload) => ipcRenderer.invoke('favoritos-workers:start-pool', payload || {}),
    getFavoritosWorkerPoolStatus: () => ipcRenderer.invoke('favoritos-workers:status'),
    stopFavoritosWorkerPool: (options) => ipcRenderer.invoke('favoritos-workers:stop-pool', options || {}),
    onFavoritosWorkersProgress: (callback) => {
        if (typeof callback !== 'function') return () => {};
        const listener = (_event, payload) => callback(payload);
        ipcRenderer.on('favoritos-workers:progress', listener);
        return () => ipcRenderer.removeListener('favoritos-workers:progress', listener);
    },
    onFavoritosWorkersDone: (callback) => {
        if (typeof callback !== 'function') return () => {};
        const listener = (_event, payload) => callback(payload);
        ipcRenderer.on('favoritos-workers:done', listener);
        return () => ipcRenderer.removeListener('favoritos-workers:done', listener);
    },
    onFavoritosWorkerProgress: (callback) => {
        if (typeof callback !== 'function') return () => {};
        const listener = (_event, payload) => callback(payload);
        ipcRenderer.on('favoritos-worker:progress', listener);
        return () => ipcRenderer.removeListener('favoritos-worker:progress', listener);
    },
    onFavoritosWorkerDone: (callback) => {
        if (typeof callback !== 'function') return () => {};
        const listener = (_event, payload) => callback(payload);
        ipcRenderer.on('favoritos-worker:done', listener);
        return () => ipcRenderer.removeListener('favoritos-worker:done', listener);
    },
    onFavoritosWorkerError: (callback) => {
        if (typeof callback !== 'function') return () => {};
        const listener = (_event, payload) => callback(payload);
        ipcRenderer.on('favoritos-worker:error', listener);
        return () => ipcRenderer.removeListener('favoritos-worker:error', listener);
    },
    showEmbeddedMlBrowser: (url, bounds) => ipcRenderer.invoke('embedded-ml-browser-show', url, bounds),
    getEmbeddedMlBrowserState: () => ipcRenderer.invoke('embedded-ml-browser-state'),
    positionEmbeddedMlBrowser: (bounds) => ipcRenderer.invoke('embedded-ml-browser-position', bounds),
    hideEmbeddedMlBrowser: (options) => ipcRenderer.invoke('embedded-ml-browser-hide', options || {}),
    executeEmbeddedMlBrowser: (code) => ipcRenderer.invoke('embedded-ml-browser-execute', code),
    clickEmbeddedMlBrowser: (point) => ipcRenderer.invoke('embedded-ml-browser-click', point || {}),
    typeEmbeddedMlBrowser: (payload) => ipcRenderer.invoke('embedded-ml-browser-type', payload || {}),
    loginAvantProEmbeddedBrowser: (email) => ipcRenderer.invoke('embedded-ml-browser-login-avantpro', email || ''),
    chooseDisplayMediaSource: () => ipcRenderer.invoke('choose-display-media-source'),
    showWindowsNotification: (payload) => ipcRenderer.invoke('show-windows-notification', payload || {})
});

function isMercadoLivreHost(hostname) {
    const host = String(hostname || '').toLowerCase();
    return host === 'mercadolivre.com.br'
        || host.endsWith('.mercadolivre.com.br')
        || host === 'mercadolibre.com'
        || host.endsWith('.mercadolibre.com');
}

function isMercadoLivreAdUrl(targetUrl) {
    try {
        const url = new URL(String(targetUrl || ''), window.location.href);
        if (!isMercadoLivreHost(url.hostname)) return false;
        const pathAndQuery = `${url.pathname}${url.search}${url.hash}`;
        return /\/MLB-?\d{5,}/i.test(pathAndQuery)
            || /\/p\/MLB\d{5,}/i.test(pathAndQuery)
            || /(?:[?&#]|%26)(?:wid|item_id)=MLB-?\d{5,}/i.test(pathAndQuery)
            || /\/anuncios(?:\/|$)/i.test(url.pathname);
    } catch (_err) {
        return false;
    }
}

function openAdLinkInChrome(url) {
    return ipcRenderer.invoke('open-external-chrome', url);
}

function cleanTabTitle(value) {
    return String(value || '')
        .replace(/\s+/g, ' ')
        .replace(/^[^\p{L}\p{N}]+/u, '')
        .trim()
        .slice(0, 80);
}

function postToTabShell(channel, payload) {
    try {
        if (window.top && window.top !== window) {
            window.top.postMessage({ channel, payload }, '*');
        }
    } catch (_err) {}
}

window.addEventListener('DOMContentLoaded', () => {
    postToTabShell('jk-tab-title', { title: document.title || '' });

    document.addEventListener('click', (event) => {
        if (event.defaultPrevented || event.button !== 0) {
            return;
        }

        const adLink = event.target && event.target.closest ? event.target.closest('a[href]') : null;
        if (adLink) {
            let adUrl;
            try {
                adUrl = new URL(adLink.getAttribute('href'), window.location.href);
            } catch (_err) {
                adUrl = null;
            }

            if (adUrl && isMercadoLivreAdUrl(adUrl.href)) {
                event.preventDefault();
                event.stopImmediatePropagation();
                openAdLinkInChrome(adUrl.href).catch((err) => {
                    console.warn('Falha ao abrir anuncio no Chrome:', err && err.message ? err.message : err);
                });
                return;
            }
        }

        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) {
            return;
        }

        const link = event.target && event.target.closest ? event.target.closest('a.card[href]') : null;
        if (!link) return;

        let url;
        try {
            url = new URL(link.getAttribute('href'), window.location.href);
        } catch (_err) {
            return;
        }

        if (url.origin !== window.location.origin || !/\.html(?:$|[?#])/i.test(url.pathname)) {
            return;
        }

        event.preventDefault();
        const title = cleanTabTitle(link.querySelector('h2')?.textContent || link.textContent || url.pathname.split('/').pop());
        postToTabShell('jk-open-module-tab', {
            url: url.href,
            title: title || 'Modulo'
        });
    }, true);
});
