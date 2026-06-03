const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
    getMac: () => ipcRenderer.invoke('get-mac'),
    getAppVersion: () => ipcRenderer.invoke('get-app-version'),
    getMachineInfo: () => ipcRenderer.invoke('get-machine-info'),
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
    flushBrowserSession: () => ipcRenderer.invoke('flush-browser-session'),
    setMlAutomationActive: (active, reason) => ipcRenderer.invoke('set-ml-automation-active', !!active, reason || ''),
    getMlPublicItemInfo: (itemId) => ipcRenderer.invoke('ml-public-item-info', itemId),
    getMlBrowserItemInfo: (itemId, url) => ipcRenderer.invoke('ml-browser-item-info', itemId, url),
    openInternalBrowser: (url) => ipcRenderer.invoke('open-internal-browser', url),
    openExternalChrome: (url) => ipcRenderer.invoke('open-external-chrome', url),
    extractMlSearchResults: (url) => ipcRenderer.invoke('extract-ml-search-results', url),
    showEmbeddedMlBrowser: (url, bounds) => ipcRenderer.invoke('embedded-ml-browser-show', url, bounds),
    positionEmbeddedMlBrowser: (bounds) => ipcRenderer.invoke('embedded-ml-browser-position', bounds),
    hideEmbeddedMlBrowser: () => ipcRenderer.invoke('embedded-ml-browser-hide'),
    executeEmbeddedMlBrowser: (code) => ipcRenderer.invoke('embedded-ml-browser-execute', code),
    importCredentials: () => ipcRenderer.invoke('import-credentials')
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
