(function (global) {
  'use strict';

  const VERSION = '20260807-favoritos-promotion-effectuation-modular-v1';
  const COMPONENTS = [
  "00-runtime.js",
  "01-status-errors.js",
  "02-avant-connection.js",
  "03-login-auth.js",
  "04-avant-login-prompt.js",
  "05-promotions.js",
  "06-execution-control.js",
  "07-sku-queries.js",
  "08-legacy-search.js",
  "09-search-entrypoints.js",
  "10-listing-normalization.js",
  "11-enrichment-runtime.js",
  "12-enrichment-sources.js",
  "13-ranking.js",
  "14-ai-filter.js",
  "15-public-api.js"
];

  function componentUrl(fileName) {
    return '/favoritos/v2/search-ranking/' + fileName + '?v=' + VERSION;
  }

  function loadComponent(fileName) {
    return new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = componentUrl(fileName);
      script.onload = resolve;
      script.onerror = () => reject(new Error('Falha ao carregar search-ranking/' + fileName));
      (document.head || document.documentElement).appendChild(script);
    });
  }

  function installLegacyGlobals() {
    const searchRanking = global.FavoritosV2 && global.FavoritosV2.searchRanking;
    if (!searchRanking || !searchRanking.legacyGlobals) {
      throw new Error('Search Ranking nao publicou os aliases de compatibilidade.');
    }
    Object.entries(searchRanking.legacyGlobals).forEach(([name, implementation]) => {
      global[name] = implementation;
    });
    return searchRanking.publicApi;
  }

  global.__FAVORITOS_PROMOCOES_BUSCA_RANKING_READY__ = COMPONENTS
    .reduce((chain, fileName) => chain.then(() => loadComponent(fileName)), Promise.resolve())
    .then(installLegacyGlobals);
})(window);
