(function () {
  'use strict';

  const raiz = window.FavoritosV2 = window.FavoritosV2 || {};
  const browser = raiz.browser = raiz.browser || {};
  const workerController = browser.workerController = browser.workerController || {};
  const METODOS = new Set([
    'startFavoritosWorkerBrowser',
    'pauseFavoritosWorkerBrowser',
    'resumeFavoritosWorkerBrowser',
    'cancelFavoritosWorkerBrowser',
    'getFavoritosWorkerBrowserStatus',
    'showFavoritosWorkerBrowser',
    'hideFavoritosWorkerBrowser',
    'stopFavoritosWorkerBrowser',
    'executeFavoritosWorkerBrowser',
    'clickFavoritosWorkerBrowser',
    'typeFavoritosWorkerBrowser',
    'startFavoritosWorkersPool',
    'getFavoritosWorkersPoolStatus',
    'pauseFavoritosWorkersPool',
    'resumeFavoritosWorkersPool',
    'showFavoritosWorkersPool',
    'hideFavoritosWorkersPool',
    'stopFavoritosWorkersPool'
  ]);

  function normalizarWorkerId(value = 'w0') {
    const workerId = String(value || 'w0').trim().toLowerCase();
    if (workerId === 'w0' || /^w[1-4]$/.test(workerId)) return workerId;
    throw new Error('Identificador invalido para o navegador trabalhador do Favoritos.');
  }

  function electronApi() {
    try {
      if (window.electronAPI) return window.electronAPI;
    } catch (_err) {}
    try {
      if (window.top && window.top !== window && window.top.electronAPI) return window.top.electronAPI;
    } catch (_err) {}
    return null;
  }

  function disponivel(name) {
    const api = electronApi();
    return METODOS.has(name) && !!api && typeof api[name] === 'function';
  }

  function invoke(name, args = []) {
    if (!METODOS.has(name)) {
      return Promise.reject(new Error('Comando nao permitido no navegador trabalhador do Favoritos.'));
    }
    const api = electronApi();
    if (!api || typeof api[name] !== 'function') return null;
    try {
      return Promise.resolve(api[name].apply(api, Array.isArray(args) ? args : []));
    } catch (error) {
      return Promise.reject(error);
    }
  }

  function urlPermitida(value) {
    try {
      const url = new URL(String(value || ''), 'https://www.mercadolivre.com.br/');
      const host = String(url.hostname || '').toLowerCase();
      return url.protocol === 'https:' && (
        host === 'mercadolivre.com.br'
        || host.endsWith('.mercadolivre.com.br')
        || host === 'mercadolibre.com'
        || host.endsWith('.mercadolibre.com')
        || host === 'avantprocloud.com.br'
        || host.endsWith('.avantprocloud.com.br')
      );
    } catch (_err) {
      return false;
    }
  }

  function criarProxy(workerId, initialUrl = '') {
    const id = normalizarWorkerId(workerId);
    const listeners = new Map();
    const proxy = {
      __isShellBrowserProxy: true,
      __isFavoritosWorkerProxy: true,
      __visible: true,
      workerId: id,
      currentUrl: String(initialUrl || ''),
      addEventListener(name, handler) {
        if (!listeners.has(name)) listeners.set(name, new Set());
        listeners.get(name).add(handler);
      },
      removeEventListener(name, handler) {
        if (listeners.has(name)) listeners.get(name).delete(handler);
      },
      dispatchEvent(name, event = {}) {
        const handlers = listeners.get(name);
        if (!handlers) return;
        handlers.forEach(handler => {
          try { handler(event); } catch (_err) {}
        });
      },
      getURL() {
        return proxy.currentUrl || '';
      },
      async navigate(url, options = {}) {
        const target = String(url || '').trim();
        if (!urlPermitida(target)) throw new Error('URL nao permitida no navegador trabalhador do Favoritos.');
        proxy.currentUrl = target;
        const result = await invoke('startFavoritosWorkerBrowser', [target, id, options || {}]);
        proxy.currentUrl = String(result && result.url || target);
        proxy.dispatchEvent(result && result.success === false ? 'did-fail-load' : 'did-finish-load', {
          url: proxy.currentUrl,
          warning: result && result.loadWarning || '',
          errorDescription: result && result.reason || ''
        });
        return result;
      },
      executeJavaScript(code) {
        return invoke('executeFavoritosWorkerBrowser', [code, id]);
      },
      clickAt(point) {
        return invoke('clickFavoritosWorkerBrowser', [point || {}, id]);
      },
      typeText(payload) {
        return invoke('typeFavoritosWorkerBrowser', [payload || {}, id]);
      },
      insertCSS(css) {
        return proxy.executeJavaScript(`
          (function () {
            var style = document.getElementById('jk-discreet-scrollbar-style');
            if (!style) {
              style = document.createElement('style');
              style.id = 'jk-discreet-scrollbar-style';
              document.head.appendChild(style);
            }
            style.textContent = ${JSON.stringify(css || '')};
            return true;
          })();
        `).catch(() => null);
      }
    };
    Object.defineProperty(proxy, 'src', {
      get() { return proxy.currentUrl; },
      set(url) {
        proxy.navigate(url).catch(err => {
          proxy.dispatchEvent('did-fail-load', {
            url: String(url || ''),
            errorDescription: err && err.message ? err.message : String(err),
            errorCode: -1
          });
        });
      }
    });
    return proxy;
  }

  Object.assign(workerController, {
    electronApi,
    disponivel,
    invoke,
    urlPermitida,
    normalizarWorkerId,
    criarProxy
  });
})();
