(function (global) {
  'use strict';

  const favoritos = global.FavoritosV2 = global.FavoritosV2 || {};
  const execution = favoritos.execution = favoritos.execution || {};
  if (execution.__runtimeInitialized) return;

  execution.__runtimeInitialized = true;
  execution.schema = 'jk.favoritos.execution.v1';
  execution.publicApi = execution.publicApi || {};

  const adapterNames = new Set([
    'abrirMercadoLivreNoPrograma',
    'agendarAtualizacaoPosicaoNavegadorMlShell',
    'aplicarCacheAvantAosAnuncios',
    'cancelarFavoritosEmExecucao',
    'coletarPrimeiraPaginaFavoritosControlada',
    'confirmarSalvamentoHistoricoFavoritosServidor',
    'criarContextoEnriquecimentoFavoritosExecucao',
    'executarComTimeoutFavoritos',
    'favoritosLojaSelecionadaParaApi',
    'fecharBalaoResultadosMl',
    'finalizarDuracaoExecucaoHistoricosFavoritos',
    'mostrarBalaoFavoritosStatus',
    'prepararAvantProAntesDaPesquisaFavoritos',
    'registrarHistoricoFavoritos',
    'renderizarFavoritosMl',
    'salvarCacheAvantDosAnuncios'
  ]);

  function resolveAdapter(name) {
    if (!adapterNames.has(name)) {
      throw new Error('Adaptador de execucao nao permitido: ' + name);
    }
    const adapter = global[name];
    if (typeof adapter !== 'function') {
      throw new Error('Adaptador de execucao indisponivel: ' + name);
    }
    return adapter;
  }

  const state = {};
  Object.defineProperties(state, {
    running: {
      get: () => mlFavoritosEmExecucao,
      set: value => { mlFavoritosEmExecucao = Boolean(value); }
    },
    background: {
      get: () => mlFavoritosExecucaoEmSegundoPlano,
      set: value => { mlFavoritosExecucaoEmSegundoPlano = Boolean(value); }
    },
    startedAtMs: {
      get: () => mlFavoritosExecucaoIniciadaEmMs,
      set: value => { mlFavoritosExecucaoIniciadaEmMs = Number(value) || 0; }
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
    jobId: {
      get: () => mlFavoritosJobIdAtual,
      set: value => { mlFavoritosJobIdAtual = String(value || ''); }
    },
    jobPollTimer: {
      get: () => mlFavoritosJobPollTimer,
      set: value => { mlFavoritosJobPollTimer = value || null; }
    },
    jobPolling: {
      get: () => mlFavoritosJobPollAtivo,
      set: value => { mlFavoritosJobPollAtivo = Boolean(value); }
    },
    lastJobStatus: {
      get: () => mlFavoritosJobUltimoStatus,
      set: value => { mlFavoritosJobUltimoStatus = value || null; }
    },
    selectedSkus: {
      get: () => mlFavoritosJobSelecionadosAtual,
      set: value => { mlFavoritosJobSelecionadosAtual = Array.isArray(value) ? value : []; }
    },
    finalJobHandled: {
      get: () => mlFavoritosJobFinalTratado,
      set: value => { mlFavoritosJobFinalTratado = Boolean(value); }
    },
    resultsBySku: {
      get: () => mlFavoritosResultadosPorSku,
      set: value => { mlFavoritosResultadosPorSku = value instanceof Map ? value : new Map(); }
    },
    promotionOptionsBySku: {
      get: () => mlFavoritosOpcoesPromocaoPorSku,
      set: value => { mlFavoritosOpcoesPromocaoPorSku = value instanceof Map ? value : new Map(); }
    },
    currentPromotionOptions: {
      get: () => mlFavoritosOpcoesPromocaoAtual,
      set: value => { mlFavoritosOpcoesPromocaoAtual = value || null; }
    },
    rankingTypes: {
      get: () => mlFavoritosTiposRankingEmExecucao,
      set: value => { mlFavoritosTiposRankingEmExecucao = value instanceof Set ? value : new Set(); }
    }
  });

  execution.runtime = Object.freeze({
    adapterNames: Object.freeze(Array.from(adapterNames)),
    resolveAdapter,
    state
  });
})(window);
