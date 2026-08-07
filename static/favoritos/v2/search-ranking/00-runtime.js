(function (global) {
  'use strict';

  const favoritos = global.FavoritosV2 = global.FavoritosV2 || {};
  const searchRanking = favoritos.searchRanking = favoritos.searchRanking || {};
  if (searchRanking.__runtimeInitialized) return;

  searchRanking.__runtimeInitialized = true;
  searchRanking.schema = 'jk.favoritos.search-ranking.v1';
  searchRanking.internal = searchRanking.internal || { components: new Set() };
  searchRanking.publicApi = searchRanking.publicApi || {};

  const adapterNames = new Set([
    'abrirBalaoResultadosMl',
    'abrirLoginAvantProNoWebview',
    'abrirMercadoLivreNoPrograma',
    'acaoUsuarioFavoritosPermiteLeituraPagina',
    'acionarControlesAvantProNoWebview',
    'aguardarAvantProNoWebview',
    'aguardarPrimeirosDadosAvantOuCardsWebview',
    'coletarPrimeiraPaginaFavoritosControlada',
    'diagnosticarAvantProNoWebview',
    'extrairAnunciosWebviewVisivel',
    'extrairCardsMercadoLivreBasicoWebview',
    'favoritosLojaSelecionadaParaApi',
    'fecharBalaoResultadosMl',
    'headersJsonAutenticado',
    'obterAuthHeaders',
    'prepararAvantProAntesDaPesquisaFavoritos'
  ]);

  function resolveAdapter(name) {
    if (!adapterNames.has(name)) throw new Error('Adaptador de search-ranking nao permitido: ' + name);
    const adapter = global[name];
    if (typeof adapter !== 'function') throw new Error('Adaptador de search-ranking indisponivel: ' + name);
    return adapter;
  }

  const state = {};
  Object.defineProperties(state, {
    background: {
      get: () => mlFavoritosExecucaoEmSegundoPlano,
      set: value => { mlFavoritosExecucaoEmSegundoPlano = Boolean(value); }
    },
    cancelled: {
      get: () => mlFavoritosCancelado,
      set: value => { mlFavoritosCancelado = Boolean(value); }
    },
    paused: {
      get: () => mlFavoritosPausado,
      set: value => { mlFavoritosPausado = Boolean(value); }
    },
    abortController: {
      get: () => mlFavoritosAbortController,
      set: value => { mlFavoritosAbortController = value || null; }
    },
    questionResolver: {
      get: () => mlFavoritosPerguntaResolver,
      set: value => { mlFavoritosPerguntaResolver = value || null; }
    },
    jobId: {
      get: () => mlFavoritosJobIdAtual,
      set: value => { mlFavoritosJobIdAtual = String(value || ''); }
    },
    jobRenderRaf: {
      get: () => mlFavoritosJobRenderRaf,
      set: value => { mlFavoritosJobRenderRaf = Number(value) || 0; }
    },
    jobRenderPending: {
      get: () => mlFavoritosJobRenderPendente,
      set: value => { mlFavoritosJobRenderPendente = Boolean(value); }
    },
    promotionsByStore: { get: () => favMlPromocoesPorLojaCache },
    ownListingsAiCache: { get: () => mlFavoritosAnunciosPropriosIaCache }
  });

  searchRanking.runtime = Object.freeze({
    adapterNames: Object.freeze(Array.from(adapterNames)),
    resolveAdapter,
    state
  });
})(window);
