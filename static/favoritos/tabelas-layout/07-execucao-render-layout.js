/**
 * Favoritos - execucao, ranking e layout.
 * Fachada compativel que carrega os componentes em ordem.
 */
(function (global) {
  'use strict';

  const VERSION = '20260807-favoritos-promotion-effectuation-modular-v1';
  const COMPONENTS = [
    "00-runtime.js",
    "01-collection-evidence.js",
    "02-collection-controller.js",
    "03-worker-pool.js",
    "04-confirmed-flow.js",
    "05-legacy-renderer.js",
    "06-job-runtime.js",
    "07-job-controls.js",
    "08-ranking-entrypoints.js",
    "09-sidebar-render.js",
    "10-table-layout.js",
    "11-public-api.js"
];

  if (global.__FAVORITOS_EXECUCAO_RENDER_LAYOUT_LOADED__) return;
  global.__FAVORITOS_EXECUCAO_RENDER_LAYOUT_LOADED__ = true;

  function componentUrl(fileName) {
    return '/favoritos/v2/execution/' + fileName + '?v=' + VERSION;
  }

  function loadComponent(fileName) {
    return new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = componentUrl(fileName);
      script.onload = resolve;
      script.onerror = () => reject(new Error('Falha ao carregar ' + fileName));
      (document.head || document.documentElement).appendChild(script);
    });
  }

  global.__FAVORITOS_EXECUCAO_RENDER_LAYOUT_READY__ = COMPONENTS
    .reduce((chain, fileName) => chain.then(() => loadComponent(fileName)), Promise.resolve())
    .catch((error) => {
      global.__FAVORITOS_EXECUCAO_RENDER_LAYOUT_LOADED__ = false;
      console.error('[Favoritos] Falha ao carregar execucao e layout.', error);
      throw error;
    });
})(window);
