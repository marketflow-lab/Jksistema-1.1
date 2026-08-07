(function (global) {
  'use strict';

  if (global.__FAVORITOS_PROMOCOES_EFETIVACAO_READY__) return;

  const VERSION = '20260807-favoritos-promotion-effectuation-modular-v1';
  const COMPONENTS = [
  "00-runtime.js",
  "01-contracts.js",
  "02-promotion-options.js",
  "03-pricing-simulation.js",
  "04-client-single-execution.js",
  "05-outcomes.js",
  "06-results-ui.js",
  "07-history.js",
  "08-simulator-ui.js",
  "09-selection.js",
  "10-confirmation.js",
  "11-preflight.js",
  "12-batch-execution.js",
  "13-listing-ui.js",
  "14-merge-policies.js",
  "15-public-api.js"
];

  function componentUrl(fileName) {
    return '/favoritos/v2/promotion-effectuation/' + fileName + '?v=' + VERSION;
  }

  function loadComponent(fileName) {
    return new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = componentUrl(fileName);
      script.onload = resolve;
      script.onerror = () => reject(new Error('Falha ao carregar promotion-effectuation/' + fileName));
      (document.head || document.documentElement).appendChild(script);
    });
  }

  function configureRuntime(feature) {
    feature.runtime.configure({
      adapters: {
        anuncioHistoricoPayload: (...args) => anuncioHistoricoPayload(...args),
        carregarFavoritosAnunciosSku: (...args) => carregarFavoritosAnunciosSku(...args),
        chavePrecoCentavosFavoritos: (...args) => chavePrecoCentavosFavoritos(...args),
        confirmarSalvamentoHistoricoFavoritosServidor: (...args) => confirmarSalvamentoHistoricoFavoritosServidor(...args),
        criarBotaoIgnorarVendedorFavoritos: (...args) => criarBotaoIgnorarVendedorFavoritos(...args),
        extrairItemIdAnuncio: (...args) => extrairItemIdAnuncio(...args),
        favoritosLojaSelecionadaParaApi: (...args) => favoritosLojaSelecionadaParaApi(...args),
        favoritosSalvarEstadoLojaAtual: (...args) => favoritosSalvarEstadoLojaAtual(...args),
        fonteVendasAvantPro: (...args) => fonteVendasAvantPro(...args),
        fonteVendasConfiavel: (...args) => fonteVendasConfiavel(...args),
        formatarDescontoPrecoFavoritos: (...args) => formatarDescontoPrecoFavoritos(...args),
        formatarDiasAnuncio: (...args) => formatarDiasAnuncio(...args),
        formatarMargemAnuncioFavoritos: (...args) => formatarMargemAnuncioFavoritos(...args),
        formatarMediaVendas: (...args) => formatarMediaVendas(...args),
        hasNumeroVendas: (...args) => hasNumeroVendas(...args),
        hasTexto: (...args) => hasTexto(...args),
        headersJsonAutenticado: (...args) => headersJsonAutenticado(...args),
        nomeUsuarioHistoricoFavoritosAtual: (...args) => nomeUsuarioHistoricoFavoritosAtual(...args),
        normalizarNomeVendedorParaBusca: (...args) => normalizarNomeVendedorParaBusca(...args),
        obterAuthHeaders: (...args) => obterAuthHeaders(...args),
        obterGrupoRankingFavoritosSku: (...args) => obterGrupoRankingFavoritosSku(...args),
        obterIdAnuncioFavoritos: (...args) => obterIdAnuncioFavoritos(...args),
        obterImagemAnuncioFavoritos: (...args) => obterImagemAnuncioFavoritos(...args),
        obterPrecoFinalSimulacaoFavoritos: (...args) => obterPrecoFinalSimulacaoFavoritos(...args),
        obterPrecoVigenteAnuncioFavoritos: (...args) => obterPrecoVigenteAnuncioFavoritos(...args),
        obterPrecosAnuncioFavoritos: (...args) => obterPrecosAnuncioFavoritos(...args),
        obterRankingFavoritosParaSimulador: (...args) => obterRankingFavoritosParaSimulador(...args),
        parseMargemAnuncioFavoritos: (...args) => parseMargemAnuncioFavoritos(...args),
        parseNumeroVendas: (...args) => parseNumeroVendas(...args),
        parsePrecoAnuncioFavoritos: (...args) => parsePrecoAnuncioFavoritos(...args),
        parseTaxaSimuladorFavoritos: (...args) => parseTaxaSimuladorFavoritos(...args),
        parseVendasAvantPro: (...args) => parseVendasAvantPro(...args),
        pesoFonteVendas: (...args) => pesoFonteVendas(...args),
        pesoFonteVendedor: (...args) => pesoFonteVendedor(...args),
        posicionarBalaoFavoritosStatus: (...args) => posicionarBalaoFavoritosStatus(...args),
        registrarHistoricoAlteracoesFavoritos: (...args) => registrarHistoricoAlteracoesFavoritos(...args),
        renderizarFavoritosAnunciosMl: (...args) => renderizarFavoritosAnunciosMl(...args),
        scoreNomeVendedor: (...args) => scoreNomeVendedor(...args),
        skuChaveSku: (...args) => skuChaveSku(...args),
        skuNormalizarLoja: (...args) => skuNormalizarLoja(...args),
        vendedorValido: (...args) => vendedorValido(...args)
      }
    });
  }

  function installLegacyGlobals() {
    const feature = global.FavoritosV2 && global.FavoritosV2.promotionEffectuation;
    if (!feature || !feature.legacyGlobals) {
      throw new Error('Efetivacao de promocoes nao publicou os aliases de compatibilidade.');
    }
    configureRuntime(feature);
    Object.entries(feature.legacyGlobals).forEach(([name, implementation]) => {
      global[name] = implementation;
    });
    return feature.publicApi;
  }

  global.__FAVORITOS_PROMOCOES_EFETIVACAO_READY__ = COMPONENTS
    .reduce((chain, fileName) => chain.then(() => loadComponent(fileName)), Promise.resolve())
    .then(installLegacyGlobals);
})(window);
