(function (global) {
  'use strict';

  const favoritos = global.FavoritosV2 = global.FavoritosV2 || {};
  const feature = favoritos.promotionEffectuation = favoritos.promotionEffectuation || {};
  if (feature.__runtimeInitialized) return;

  feature.__runtimeInitialized = true;
  feature.schema = 'jk.favoritos.promotion-effectuation.v1';
  feature.internal = feature.internal || { components: new Set() };
  feature.publicApi = feature.publicApi || {};

  const adapterNames = new Set([
    "anuncioHistoricoPayload",
    "carregarFavoritosAnunciosSku",
    "chavePrecoCentavosFavoritos",
    "confirmarSalvamentoHistoricoFavoritosServidor",
    "criarBotaoIgnorarVendedorFavoritos",
    "extrairItemIdAnuncio",
    "favoritosLojaSelecionadaParaApi",
    "favoritosSalvarEstadoLojaAtual",
    "fonteVendasAvantPro",
    "fonteVendasConfiavel",
    "formatarDescontoPrecoFavoritos",
    "formatarDiasAnuncio",
    "formatarMargemAnuncioFavoritos",
    "formatarMediaVendas",
    "hasNumeroVendas",
    "hasTexto",
    "headersJsonAutenticado",
    "nomeUsuarioHistoricoFavoritosAtual",
    "normalizarNomeVendedorParaBusca",
    "obterAuthHeaders",
    "obterGrupoRankingFavoritosSku",
    "obterIdAnuncioFavoritos",
    "obterImagemAnuncioFavoritos",
    "obterPrecoFinalSimulacaoFavoritos",
    "obterPrecoVigenteAnuncioFavoritos",
    "obterPrecosAnuncioFavoritos",
    "obterRankingFavoritosParaSimulador",
    "parseMargemAnuncioFavoritos",
    "parseNumeroVendas",
    "parsePrecoAnuncioFavoritos",
    "parseTaxaSimuladorFavoritos",
    "parseVendasAvantPro",
    "pesoFonteVendas",
    "pesoFonteVendedor",
    "posicionarBalaoFavoritosStatus",
    "registrarHistoricoAlteracoesFavoritos",
    "renderizarFavoritosAnunciosMl",
    "scoreNomeVendedor",
    "skuChaveSku",
    "skuNormalizarLoja",
    "vendedorValido"
  ]);
  const configuredAdapters = new Map();

  function configure(config = {}) {
    const entries = config.adapters && typeof config.adapters === 'object'
      ? Object.entries(config.adapters)
      : [];
    entries.forEach(([name, implementation]) => {
      if (!adapterNames.has(name)) throw new Error('Adaptador de efetivacao nao permitido: ' + name);
      if (typeof implementation !== 'function') throw new Error('Adaptador de efetivacao invalido: ' + name);
      configuredAdapters.set(name, implementation);
    });
    return feature.runtime;
  }

  function resolveAdapter(name) {
    if (!adapterNames.has(name)) throw new Error('Adaptador de efetivacao nao permitido: ' + name);
    const implementation = configuredAdapters.get(name) || global[name];
    if (typeof implementation !== 'function') {
      throw new Error('Adaptador de efetivacao indisponivel: ' + name);
    }
    return implementation;
  }

  const adapters = {};
  adapterNames.forEach(name => {
    Object.defineProperty(adapters, name, {
      enumerable: true,
      get: () => configuredAdapters.get(name) || global[name]
    });
  });

  const state = {};
  Object.defineProperties(state, {
    FAV_ML_RANKING_ATUAL_ID: { get: () => FAV_ML_RANKING_ATUAL_ID },
    favMlAnunciosNaoAlterarPorSku: { get: () => favMlAnunciosNaoAlterarPorSku, set: value => { favMlAnunciosNaoAlterarPorSku = value; } },
    favMlAnunciosSkuAtual: { get: () => favMlAnunciosSkuAtual, set: value => { favMlAnunciosSkuAtual = value; } },
    favMlEfetivacaoEmExecucao: { get: () => favMlEfetivacaoEmExecucao, set: value => { favMlEfetivacaoEmExecucao = value; } },
    favMlEfetivacaoEmPreparacao: { get: () => favMlEfetivacaoEmPreparacao, set: value => { favMlEfetivacaoEmPreparacao = value; } },
    favMlEfetivarBtnEl: { get: () => favMlEfetivarBtnEl, set: value => { favMlEfetivarBtnEl = value; } },
    favMlEfetivarInfoEl: { get: () => favMlEfetivarInfoEl, set: value => { favMlEfetivarInfoEl = value; } },
    favMlEfetivarLogEl: { get: () => favMlEfetivarLogEl, set: value => { favMlEfetivarLogEl = value; } },
    favMlEfetivarLogHideTimer: { get: () => favMlEfetivarLogHideTimer, set: value => { favMlEfetivarLogHideTimer = value; } },
    favMlEfetivarOutrasContasEl: { get: () => favMlEfetivarOutrasContasEl, set: value => { favMlEfetivarOutrasContasEl = value; } },
    favMlEfetivarPanelEl: { get: () => favMlEfetivarPanelEl, set: value => { favMlEfetivarPanelEl = value; } },
    favMlHistoricoExecucaoSelecionadaId: { get: () => favMlHistoricoExecucaoSelecionadaId, set: value => { favMlHistoricoExecucaoSelecionadaId = value; } },
    favMlLojaSelecionada: { get: () => favMlLojaSelecionada, set: value => { favMlLojaSelecionada = value; } },
    favMlPromocaoBtnEl: { get: () => favMlPromocaoBtnEl, set: value => { favMlPromocaoBtnEl = value; } },
    favMlPromocoesPorLojaCache: { get: () => favMlPromocoesPorLojaCache, set: value => { favMlPromocoesPorLojaCache = value; } },
    favMlSimulacoesSkuAtual: { get: () => favMlSimulacoesSkuAtual, set: value => { favMlSimulacoesSkuAtual = value; } },
    favMlSkuSelecionado: { get: () => favMlSkuSelecionado, set: value => { favMlSkuSelecionado = value; } },
    favMlStatusEl: { get: () => favMlStatusEl, set: value => { favMlStatusEl = value; } },
    mlFavoritosBalloonActionsEl: { get: () => mlFavoritosBalloonActionsEl, set: value => { mlFavoritosBalloonActionsEl = value; } },
    mlFavoritosBalloonEl: { get: () => mlFavoritosBalloonEl, set: value => { mlFavoritosBalloonEl = value; } },
    mlFavoritosBalloonTextEl: { get: () => mlFavoritosBalloonTextEl, set: value => { mlFavoritosBalloonTextEl = value; } },
    mlFavoritosBalloonTimer: { get: () => mlFavoritosBalloonTimer, set: value => { mlFavoritosBalloonTimer = value; } },
    mlFavoritosOpcoesPromocaoAtual: { get: () => mlFavoritosOpcoesPromocaoAtual, set: value => { mlFavoritosOpcoesPromocaoAtual = value; } },
    mlFavoritosOpcoesPromocaoPorSku: { get: () => mlFavoritosOpcoesPromocaoPorSku, set: value => { mlFavoritosOpcoesPromocaoPorSku = value; } },
    mlFavoritosPerguntaResolver: { get: () => mlFavoritosPerguntaResolver, set: value => { mlFavoritosPerguntaResolver = value; } },
    mlFavoritosStatusEl: { get: () => mlFavoritosStatusEl, set: value => { mlFavoritosStatusEl = value; } },
    mlSkuLojaSelecionada: { get: () => mlSkuLojaSelecionada, set: value => { mlSkuLojaSelecionada = value; } },
    skuLojaSelecionada: { get: () => skuLojaSelecionada, set: value => { skuLojaSelecionada = value; } }
  });

  feature.runtime = Object.freeze({
    adapterNames: Object.freeze(Array.from(adapterNames)),
    adapters: Object.freeze(adapters),
    configure,
    resolveAdapter,
    state
  });
  feature.internal.components.add('00-runtime');
})(window);
