/**
 * Favoritos - tabelas e layout
 * Loader pequeno para os blocos isolados em static/favoritos/tabelas-layout/.
 */
(function () {
  'use strict';

  const VERSION = '20260721-favoritos-ranking-no-arrows-v1';
  const CHUNKS = [
    "01-ml-base-busca.js",
    "02-ia-datas-selecao.js",
    "03-sku-sidebar-modal.js",
    "04-promocoes-busca-ranking.js",
    "05-resultados-historico.js",
    "06-ranking-manual-historico-ui.js",
    "07-execucao-render-layout.js",
    "08-render-avant-mercadolivre.js"
  ];

  if (window.__FAVORITOS_TABELAS_LAYOUT_LOADED__) return;
  window.__FAVORITOS_TABELAS_LAYOUT_LOADED__ = true;

  function chunkUrl(fileName) {
    return '/favoritos/tabelas-layout/' + fileName + '?v=' + VERSION;
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

  window.__FAVORITOS_TABELAS_LAYOUT_READY__ = carregarEmOrdem().catch((error) => {
    window.__FAVORITOS_TABELAS_LAYOUT_LOADED__ = false;
    console.error('[Favoritos] Nao foi possivel carregar tabelas-layout isolado.', error);
    throw error;
  });
})();

