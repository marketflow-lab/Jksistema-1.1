(function (global) {
  'use strict';

  const execution = global.FavoritosV2 && global.FavoritosV2.execution;
  if (!execution || !execution.__runtimeInitialized) {
    throw new Error('Runtime de execucao do Favoritos nao inicializado.');
  }

  const publicApi = {
    obterElectronApiFavoritosExecucao,
    formatarProgressoColetaPrimeiraPaginaFavoritos,
    pararPollingFavoritosJob,
    cancelarFavoritosJobAtualServidor,
    pausarFavoritosJobAtual,
    retomarFavoritosJobAtual,
    pararNavegadorFavoritosBackground,
    fazerFavoritosSkusSelecionados,
    rankearAvulsoMercadoLivre,
    formatarPrecoFavoritosMl,
    renderizarFavoritosSkuSidebar,
    criarCelulaTextoFavoritos,
    criarCelulaMlbLojaFavoritos,
    obterNomeLojaVendedoraHistoricoFavoritos,
    carregarLayoutTabelasFavoritos,
    atualizarTabelasFavoritosEditaveis,
    agendarSincronizarLinhasFavoritos,
    inicializarLarguraTabelasFavoritos
  };

  execution.collection = Object.freeze({
    collectFirstPage: coletarAnunciosPrimeiraPaginaFavoritosSemFluxoAntigo,
    formatProgress: formatarProgressoColetaPrimeiraPaginaFavoritos,
    summarize: montarResumoPesquisaFavoritos
  });
  execution.pool = Object.freeze({
    execute: executarFilaSkusFavoritosPool,
    processSku: processarSkuCompletoFavoritosNoWorker,
    updateStatus: atualizarEstadoFilaFavoritosPool
  });
  execution.jobs = Object.freeze({
    fetch: fetchFavoritosJob,
    poll: pollFavoritosJobAtual,
    receiveStatus: receberStatusFavoritosJob,
    cancel: cancelarFavoritosJobAtualServidor,
    pause: pausarFavoritosJobAtual,
    resume: retomarFavoritosJobAtual
  });
  execution.ranking = Object.freeze({
    executeSelected: fazerFavoritosSkusSelecionados,
    executeManual: rankearAvulsoMercadoLivre
  });
  execution.render = Object.freeze({
    renderSidebar: renderizarFavoritosSkuSidebar,
    createTextCell: criarCelulaTextoFavoritos,
    createListingCell: criarCelulaMlbLojaFavoritos,
    formatPrice: formatarPrecoFavoritosMl
  });
  execution.layout = Object.freeze({
    load: carregarLayoutTabelasFavoritos,
    refresh: atualizarTabelasFavoritosEditaveis,
    scheduleRowSync: agendarSincronizarLinhasFavoritos,
    initializeWidth: inicializarLarguraTabelasFavoritos
  });
  Object.assign(execution.publicApi, publicApi);
  Object.entries(publicApi).forEach(([name, handler]) => {
    global[name] = handler;
  });
  execution.publicApiNames = Object.freeze(Object.keys(publicApi));
  execution.__componentsReady = true;
})(window);
