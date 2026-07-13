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
    'typeFavoritosWorkerBrowser'
  ]);

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

  Object.assign(workerController, {
    electronApi,
    disponivel,
    invoke,
    urlPermitida
  });
})();
