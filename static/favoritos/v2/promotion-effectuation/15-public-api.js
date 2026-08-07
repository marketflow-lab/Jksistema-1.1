(function (global) {
  'use strict';

  const feature = global.FavoritosV2.promotionEffectuation;
  const internal = feature.internal;
  if (feature.__publicApiInitialized) return;

  function pick(names) {
    const api = {};
    names.forEach(name => {
      const implementation = internal[name];
      if (typeof implementation !== 'function') {
        throw new Error('Implementacao de efetivacao ausente: ' + name);
      }
      api[name] = implementation;
    });
    return Object.freeze(api);
  }

  const groupNames = {
    "options": [
      "clonarOpcoesPromocaoFavoritos",
      "salvarOpcoesPromocaoFavoritosSku",
      "resolverOpcoesPromocaoGrupoFavoritos",
      "obterOpcoesPromocaoFavoritosSku",
      "escolherPromocaoFavoritosSkuAtual",
      "garantirPromocaoFavoritosSkuAtual"
    ],
    "pricing": [
      "calcularSimulacaoPrecoFavoritos",
      "obterPrecosObservadosEfetivacaoFavoritos"
    ],
    "client": [
      "definirProtecaoAutomacaoMlFavoritos"
    ],
    "outcomes": [
      "normalizarErroEfetivacaoFavoritos",
      "limparLogEfetivarFavoritos",
      "agendarOcultarStatusEfetivarFavoritos",
      "adicionarStatusEfetivarFavoritos",
      "descreverSucessoEfetivacaoFavoritos",
      "resultadoEfetivacaoTerminalFavoritos",
      "descreverTipoFalhaEfetivacaoFavoritos",
      "descreverEtapasFalhaEfetivacaoFavoritos",
      "descreverFalhaEfetivacaoFavoritos"
    ],
    "results": [
      "renderizarComparativoEfetivacaoFavoritos"
    ],
    "history": [
      "montarSimulacaoHistoricoAlteracaoFavoritos",
      "montarVinculoHistoricoAlteracaoFavoritos",
      "montarHistoricoAlteracoesFavoritosPayload",
      "salvarHistoricoAlteracoesFavoritosProcesso"
    ],
    "simulator": [
      "criarCelulaSimuladorPrecoFavoritos"
    ],
    "selection": [
      "anuncioSelecionadoAlteracaoFavoritos",
      "definirAnuncioSelecionadoAlteracaoFavoritos",
      "limparSelecaoAlteracaoFavoritosSku",
      "filtrarRegistrosSelecionadosAlteracaoFavoritos",
      "obterRegistrosSimulacaoFavoritos",
      "filtrarRegistrosEfetivaveisFavoritos",
      "explicarRegistrosNaoEfetivaveisFavoritos"
    ],
    "confirmation": [
      "perguntarConfirmacaoEfetivarFavoritos"
    ],
    "preflight": [
      "carregarFavoritosAnunciosSkuTodasContas",
      "registroFavoritosExigeTrocaTipoAnuncio",
      "textoTipoEnvioFavoritos",
      "validarRegistrosEfetivaveisFavoritosMercadoLivre",
      "resolverOpcoesPromocaoEfetivacaoParaLoja"
    ],
    "execution": [
      "atualizarPainelEfetivarFavoritos",
      "efetivarFavoritosMercadoLivreAprovados"
    ],
    "listings": [
      "criarCelulaMargemAnuncioFavoritos",
      "normalizarTipoAnuncioFavoritos",
      "obterParcelamentoSemJurosFavoritos",
      "obterTipoAnuncioFavoritos",
      "nomeTipoPorListingTypeFavoritos",
      "temIndicadorFullFavoritos",
      "obterFullAnuncioFavoritos",
      "fullAnuncioDesconhecidoFavoritos",
      "obterTipoCompletoAnuncioFavoritos",
      "preencherTipoAnuncioFavoritos",
      "criarCelulaTipoAnuncioFavoritos",
      "criarCelulaMediaHistoricoFavoritos"
    ],
    "merge": [
      "deveAtualizarVendedor",
      "deveAtualizarVendas"
    ]
  };
  const publicApi = {};
  Object.entries(groupNames).forEach(([groupName, names]) => {
    publicApi[groupName] = pick(names);
  });
  const flat = {};
  Object.values(publicApi).forEach(group => Object.assign(flat, group));
  publicApi.flat = Object.freeze(flat);

  feature.publicApi = Object.freeze(publicApi);
  feature.legacyGlobals = pick([
    "anuncioSelecionadoAlteracaoFavoritos",
    "atualizarPainelEfetivarFavoritos",
    "calcularSimulacaoPrecoFavoritos",
    "clonarOpcoesPromocaoFavoritos",
    "criarCelulaMargemAnuncioFavoritos",
    "criarCelulaMediaHistoricoFavoritos",
    "criarCelulaSimuladorPrecoFavoritos",
    "criarCelulaTipoAnuncioFavoritos",
    "definirAnuncioSelecionadoAlteracaoFavoritos",
    "deveAtualizarVendas",
    "deveAtualizarVendedor",
    "efetivarFavoritosMercadoLivreAprovados",
    "escolherPromocaoFavoritosSkuAtual",
    "fullAnuncioDesconhecidoFavoritos",
    "limparSelecaoAlteracaoFavoritosSku",
    "nomeTipoPorListingTypeFavoritos",
    "normalizarTipoAnuncioFavoritos",
    "obterFullAnuncioFavoritos",
    "obterOpcoesPromocaoFavoritosSku",
    "obterParcelamentoSemJurosFavoritos",
    "obterTipoAnuncioFavoritos",
    "obterTipoCompletoAnuncioFavoritos",
    "preencherTipoAnuncioFavoritos",
    "resolverOpcoesPromocaoGrupoFavoritos",
    "salvarOpcoesPromocaoFavoritosSku",
    "temIndicadorFullFavoritos"
  ]);
  feature.__publicApiInitialized = true;
  internal.components.add('15-public-api');
})(window);
