(function (global) {
  'use strict';

  const VERSION = '20260807-favoritos-promotion-effectuation-modular-v1';
  const STAGES = [
  [
    "/favoritos/v2/browser/browser-runtime.js",
    "/favoritos/v2/browser/page-scripts/runtime.js"
  ],
  [
    "/favoritos/v2/browser/page-scripts/template-bundle-01.js",
    "/favoritos/v2/browser/page-scripts/template-bundle-02.js",
    "/favoritos/v2/browser/page-scripts/template-bundle-03.js",
    "/favoritos/v2/browser/page-scripts/template-bundle-04.js",
    "/favoritos/v2/browser/page-scripts/template-bundle-05.js",
    "/favoritos/v2/browser/page-scripts/template-bundle-06.js",
    "/favoritos/v2/browser/page-scripts/template-bundle-07.js",
    "/favoritos/v2/browser/page-scripts/template-bundle-08.js",
    "/favoritos/v2/browser/page-scripts/template-bundle-09.js",
    "/favoritos/v2/browser/page-scripts/template-bundle-10.js",
    "/favoritos/v2/browser/page-scripts/template-bundle-11.js"
  ],
  [
    "/favoritos/v2/browser/page-scripts/legacy-extract.js"
  ],
  [
    "/favoritos/v2/browser/navigation-search.js",
    "/favoritos/v2/browser/avant-cache-compat.js",
    "/favoritos/v2/browser/browser-ui.js",
    "/favoritos/v2/browser/webview-navigation.js",
    "/favoritos/v2/browser/shell-runtime.js",
    "/favoritos/v2/browser/avant-entry.js",
    "/favoritos/v2/browser/avant-controls.js",
    "/favoritos/v2/browser/avant-login-native.js",
    "/favoritos/v2/browser/avant-login-modal.js",
    "/favoritos/v2/browser/avant-session.js",
    "/favoritos/v2/browser/extraction-fast.js",
    "/favoritos/v2/browser/extraction-avant.js",
    "/favoritos/v2/browser/extraction-mercado-livre.js",
    "/favoritos/v2/browser/first-page-support.js",
    "/favoritos/v2/browser/avant-card-queue.js",
    "/favoritos/v2/browser/first-page-controller.js",
    "/favoritos/v2/browser/diagnostics.js",
    "/favoritos/v2/browser/readiness.js",
    "/favoritos/v2/browser/merge.js"
  ],
  [
    "/favoritos/v2/browser/public-api.js"
  ]
];

  function loadScript(source) {
    return new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = source + '?v=' + VERSION;
      script.onload = resolve;
      script.onerror = () => reject(new Error('Falha ao carregar ' + source));
      (document.head || document.documentElement).appendChild(script);
    });
  }

  function loadStages() {
    return STAGES.reduce(
      (chain, stage) => chain.then(() => Promise.all(stage.map(loadScript))),
      Promise.resolve()
    );
  }

  if (global.__FAVORITOS_ML_BROWSER_READY__) return;
  global.__FAVORITOS_ML_BROWSER_READY__ = loadStages().then(() => {
    const browser = global.FavoritosV2 && global.FavoritosV2.browser;
    if (!browser || !browser.publicApi || !Array.isArray(browser.publicNames)) {
      throw new Error('Favoritos ML Browser V2 nao foi carregado por completo.');
    }
    browser.facadeReady = true;
    return browser.publicApi;
  }).catch((error) => {
    global.__FAVORITOS_ML_BROWSER_READY__ = null;
    throw error;
  });
})(window);
