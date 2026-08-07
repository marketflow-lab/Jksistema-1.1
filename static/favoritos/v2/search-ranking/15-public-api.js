(function (global) {
  'use strict';

  const searchRanking = global.FavoritosV2.searchRanking;
  const internal = searchRanking.internal;
  if (searchRanking.__publicApiInitialized) return;

  function pick(names) {
    const api = {};
    names.forEach(name => {
      const implementation = internal[name];
      if (typeof implementation !== 'function') {
        throw new Error('Implementacao de Search Ranking ausente: ' + name);
      }
      api[name] = implementation;
    });
    return Object.freeze(api);
  }

  const groupNames = {
  "status": [
    "mostrarBalaoFavoritosStatus",
    "esconderBalaoFavoritosStatus",
    "resolverAcaoBalaoFavoritos",
    "erroLoginMercadoLivreFavoritos",
    "erroEhLoginMercadoLivreFavoritos",
    "erroLoginAvantProFavoritos",
    "erroEhLoginAvantProFavoritos",
    "erroColetaMercadoLivreFavoritos",
    "erroEhColetaMercadoLivreFavoritos",
    "statusAvantProSemDadosColetaveis",
    "statusAvantProLoginConcluidoSemDados",
    "recarregarAposLoginAvantProFavoritosSePossivel",
    "statusAvantProPodeRetomarColeta"
  ],
  "auth": [
    "aguardarConexaoAvantProFavoritos",
    "perguntarQuantidadePesquisasFavoritos",
    "obterTermoInicialLoginAvantProFavoritos",
    "limparBotaoContinuarLoginAvantProFavoritos",
    "urlEmFluxoAutenticacaoMercadoLivreFavoritos",
    "urlHttpValidaFavoritos",
    "urlPertenceAoMercadoLivreFavoritos",
    "obterEstadoFrescoBrowserShellFavoritos",
    "obterEstadoAutenticacaoMercadoLivreFavoritos",
    "validarLoginMercadoLivreAntesDeContinuarFavoritos",
    "obterElectronApiPersistenciaAvantProFavoritos",
    "obterStatusPersistenciaAvantProFavoritos",
    "obterEstadoAutenticacaoAvantProFavoritos",
    "validarLoginAvantProAntesDeContinuarFavoritos",
    "mostrarBotaoContinuarLoginAvantProFavoritos",
    "registrarConfirmacaoUsuarioLoginAvantProFavoritos",
    "registrarConfirmacaoUsuarioLoginAvantProBestEffortFavoritos",
    "perguntarLoginAvantProAntesFavoritos"
  ],
  "promotions": [
    "nomePromocaoFavoritos",
    "grupoPromocaoFavoritos",
    "tituloGrupoPromocaoFavoritos",
    "perguntarSimNaoPromocaoFavoritos",
    "carregarPromocoesAtivasFavoritos",
    "perguntarSelecionarPromocaoFavoritos",
    "perguntarContinuarSemPromocaoFavoritos",
    "perguntarModoDescontoPromocaoFavoritos",
    "perguntarOpcoesPromocaoFavoritos",
    "resumoOpcoesPromocaoFavoritos"
  ],
  "control": [
    "criarErroFavoritosCancelado",
    "aplicarEstadoWorkerFavoritos",
    "verificarCancelamentoFavoritos",
    "sincronizarEstadoWorkerFavoritos",
    "aguardarControleFavoritos",
    "sinalFavoritosAtual",
    "executarComTimeoutFavoritos",
    "cancelarFavoritosEmExecucao",
    "inicializarSincronizacaoWorkerFavoritos"
  ],
  "search": [
    "obterCadastroSkuFavoritos",
    "normalizarTermoPesquisaFavoritos",
    "montarPesquisasFavoritosSku",
    "validarAnunciosFavoritosPertencemAoTermo",
    "filtrarAnunciosFavoritosComDadosAvant",
    "preparacaoAvantPrePesquisaConfirmada",
    "buscarAnunciosFavoritosPorTermoAvant",
    "buscarAnunciosFavoritosPorTermoFluxoControlado",
    "buscarAnunciosFavoritosPorTermo",
    "buscarAnunciosFavoritosPorTermoComAvantObrigatorio"
  ],
  "listings": [
    "chaveAnuncioFavoritos",
    "chavesAnuncioFavoritos",
    "construirUrlAnuncioFavoritosRankingPorId",
    "normalizarUrlAnuncioFavoritosRanking",
    "tituloAnuncioFavoritosPrecisaComplemento",
    "extrairTituloAnuncioFavoritosPorLink",
    "anuncioFavoritosCandidatoRanking",
    "aplicarMetadataBasicaAnuncioFavoritos",
    "normalizarAnuncioFavoritosPesquisa",
    "deduplicarAnunciosFavoritos"
  ],
  "enrichment": [
    "criarContextoEnriquecimentoFavoritosExecucao",
    "anuncioFavoritosEnriquecimentoCompleto",
    "aplicarInfoEnriquecimentoFavoritos",
    "aplicarCacheEnriquecimentoFavoritos",
    "registrarCacheEnriquecimentoFavoritos",
    "enriquecerAnunciosFavoritosRanking"
  ],
  "ranking": [
    "complementarTiposRankingFavoritos",
    "ordenarAnunciosFavoritosRanking",
    "limitarAnunciosFavoritosRanking",
    "normalizarIdAnuncioFavoritosIa",
    "obterDescricaoAnuncioFavoritosIa",
    "anuncioFavoritosIaPayload",
    "normalizarAnuncioRemovidoIa"
  ],
  "ai": [
    "buscarAnunciosPropriosFavoritosIa",
    "filtrarAnunciosFavoritosPorIa"
  ]
};
  const publicApi = {};
  Object.entries(groupNames).forEach(([groupName, names]) => {
    publicApi[groupName] = pick(names);
  });

  const flat = {};
  Object.values(publicApi).forEach(group => Object.assign(flat, group));
  publicApi.flat = Object.freeze(flat);

  searchRanking.publicApi = Object.freeze(publicApi);
  searchRanking.legacyGlobals = pick([
  "mostrarBalaoFavoritosStatus",
  "esconderBalaoFavoritosStatus",
  "erroLoginMercadoLivreFavoritos",
  "erroEhLoginMercadoLivreFavoritos",
  "erroEhLoginAvantProFavoritos",
  "erroColetaMercadoLivreFavoritos",
  "erroEhColetaMercadoLivreFavoritos",
  "statusAvantProSemDadosColetaveis",
  "aguardarConexaoAvantProFavoritos",
  "perguntarQuantidadePesquisasFavoritos",
  "obterTermoInicialLoginAvantProFavoritos",
  "limparBotaoContinuarLoginAvantProFavoritos",
  "perguntarLoginAvantProAntesFavoritos",
  "carregarPromocoesAtivasFavoritos",
  "perguntarOpcoesPromocaoFavoritos",
  "resumoOpcoesPromocaoFavoritos",
  "criarErroFavoritosCancelado",
  "verificarCancelamentoFavoritos",
  "aguardarControleFavoritos",
  "sinalFavoritosAtual",
  "executarComTimeoutFavoritos",
  "cancelarFavoritosEmExecucao",
  "inicializarSincronizacaoWorkerFavoritos",
  "obterCadastroSkuFavoritos",
  "montarPesquisasFavoritosSku",
  "filtrarAnunciosFavoritosComDadosAvant",
  "preparacaoAvantPrePesquisaConfirmada",
  "buscarAnunciosFavoritosPorTermo",
  "chavesAnuncioFavoritos",
  "tituloAnuncioFavoritosPrecisaComplemento",
  "extrairTituloAnuncioFavoritosPorLink",
  "anuncioFavoritosCandidatoRanking",
  "normalizarAnuncioFavoritosPesquisa",
  "deduplicarAnunciosFavoritos",
  "criarContextoEnriquecimentoFavoritosExecucao",
  "enriquecerAnunciosFavoritosRanking",
  "complementarTiposRankingFavoritos",
  "ordenarAnunciosFavoritosRanking",
  "limitarAnunciosFavoritosRanking",
  "normalizarIdAnuncioFavoritosIa",
    "filtrarAnunciosFavoritosPorIa"
  ]);
  searchRanking.__publicApiInitialized = true;
})(window);
