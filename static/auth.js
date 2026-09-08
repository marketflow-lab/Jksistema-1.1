/**
 * JK Sistema - Auth loader
 * Mantem /auth.js como fachada e carrega os blocos em static/auth/.
 */
(function () {
  'use strict';

  const VERSION = '20260908-sync-preserve-connections-v1';
  const CHUNKS = [
    "session.js",
    "navigation.js",
    "shared-sync-boot.js",
    "guards.js",
    "permissions.js",
    "global-ui.js"
  ];

  if (window.__JK_AUTH_LOADER_LOADED__) return;
  window.__JK_AUTH_LOADER_LOADED__ = true;

  function chunkUrl(fileName) {
    return '/auth/' + fileName + '?v=' + VERSION;
  }

  function scriptTag(fileName) {
    return '<script src="' + chunkUrl(fileName) + '"><\/script>';
  }

  function carregarEmOrdem() {
    return CHUNKS.reduce((chain, fileName) => chain.then(() => new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = chunkUrl(fileName);
      script.onload = resolve;
      script.onerror = () => reject(new Error('Falha ao carregar ' + fileName));
      (document.head || document.documentElement).appendChild(script);
    })), Promise.resolve());
  }

  if (document.currentScript && document.readyState === 'loading') {
    document.write(CHUNKS.map(scriptTag).join('\n'));
    return;
  }

  window.__JK_AUTH_READY__ = carregarEmOrdem().catch((error) => {
    window.__JK_AUTH_LOADER_LOADED__ = false;
    console.error('[Auth] Nao foi possivel carregar os arquivos isolados.', error);
    throw error;
  });
})();
