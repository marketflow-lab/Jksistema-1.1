(function (global) {
  'use strict';

  const browser = global.FavoritosV2.browser;
  const pageScripts = browser.pageScripts;

  function criarControleCancelamentoPrimeiraPagina(signal) {
    const erroCancelamento = () => {
      const err = new Error('Coleta de favoritos cancelada pelo usuario.');
      err.name = 'AbortError';
      err.canceladoFavoritos = true;
      return err;
    };
    const verificar = () => {
      if (signal && signal.aborted) throw erroCancelamento();
    };
    const aguardar = (promise) => {
      verificar();
      if (!signal || typeof signal.addEventListener !== 'function') return Promise.resolve(promise);
      let onAbort = null;
      const cancelamento = new Promise((_resolve, reject) => {
        onAbort = () => reject(erroCancelamento());
        signal.addEventListener('abort', onAbort, { once: true });
      });
      return Promise.race([Promise.resolve(promise), cancelamento]).finally(() => {
        if (onAbort) signal.removeEventListener('abort', onAbort);
      });
    };
    return { verificar, aguardar, esperar: ms => aguardar(esperar(ms)) };
  }

  function criarContextoPrimeiraPagina(opcoes, webview) {
    const loteValor = opcoes.loteCliques === undefined ? 0 : Number(opcoes.loteCliques);
    const tempoLimiteMs = Math.max(30000, Math.min(Number(opcoes.tempoLimiteMs) || 180000, 180000));
    const inicio = Date.now();
    const loteCliques = Math.max(0, Math.min(Number.isFinite(loteValor) ? loteValor : 0, 8));
    const reservaAvantMs = loteCliques > 0
      ? Math.min(Math.max(15000, Math.floor(tempoLimiteMs * 0.55)), Math.max(12000, tempoLimiteMs - 12000))
      : 0;
    const deadline = inicio + tempoLimiteMs;
    return {
      opcoes,
      webview,
      controle: criarControleCancelamentoPrimeiraPagina(opcoes.signal || null),
      limite: Math.max(20, Math.min(Number(opcoes.maxAnuncios) || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80, 100)),
      maxPassadas: Math.max(1, Math.min(Number(opcoes.maxPassadas) || 3, 3)),
      loteCliques,
      inicio,
      deadline,
      deadlinePreparacao: reservaAvantMs > 0 ? Math.max(inicio + 8000, deadline - reservaAvantMs) : deadline,
      onProgress: opcoes.onProgress,
      incrementalAtivo: false,
      materializado: null,
      anuncios: [],
      totalVisiveis: 0,
      cliquesAvantDesligados: false,
      loginAvantBloqueado: false,
      assinaturaPassadaAnterior: '',
      motivoEncerramento: '',
      passadasExecutadas: 0,
      posicoesPercorridasTotal: 0,
      totalCliquesAvant: 0,
      totalCapturadosAvant: 0,
      chavesCapturadasAvant: new Set()
    };
  }

  async function aplicarEmergenciaInicial(ctx, originalY) {
    if (ctx.anuncios.length || Date.now() >= ctx.deadlinePreparacao) return;
    const emergencia = await ctx.controle.aguardar(extrairBaseMercadoLivreEmergencialWebview({
      limite: ctx.limite,
      timeoutMs: Math.min(9000, Math.max(3500, ctx.deadlinePreparacao - Date.now())),
      webview: ctx.webview
    }).catch(() => null));
    if (!emergencia || !Array.isArray(emergencia.anuncios) || !emergencia.anuncios.length) return;
    ctx.anuncios = emergencia.anuncios;
    ctx.totalVisiveis = Math.min(ctx.limite, Number(emergencia.total) || ctx.anuncios.length);
    ctx.materializado = { ...(ctx.materializado || {}), anuncios: ctx.anuncios, totalVisiveis: ctx.totalVisiveis, emergencia: true };
    if (originalY !== undefined) ctx.materializado.originalY = originalY;
  }

  async function materializarBaseComCliques(ctx) {
    const metrica = await ctx.controle.aguardar(obterMetricaRolagemMercadoLivreFavoritos(ctx.webview));
    const originalY = Math.max(0, Number(metrica && metrica.y) || 0);
    await ctx.controle.aguardar(rolarMercadoLivreFavoritos(0, ctx.webview));
    await ctx.controle.esperar(350);
    const basico = await ctx.controle.aguardar(aguardarBaseMercadoLivreColetavelFavoritos({
      limite: ctx.limite,
      timeoutMs: Math.min(30000, Math.max(8000, ctx.deadlinePreparacao - Date.now())),
      onProgress: ctx.onProgress,
      webview: ctx.webview
    }).catch(() => null));
    ctx.anuncios = (basico && basico.anuncios) || [];
    ctx.totalVisiveis = Math.min(ctx.limite, Number(basico && basico.total) || ctx.anuncios.length);
    ctx.materializado = { anuncios: ctx.anuncios, totalVisiveis: ctx.totalVisiveis, originalY };
    if (!ctx.anuncios.length && Date.now() < ctx.deadlinePreparacao) {
      ctx.materializado = await ctx.controle.aguardar(materializarCardsPrimeiraPaginaMercadoLivreFavoritos({
        limite: ctx.limite,
        deadlineMs: Math.min(ctx.deadlinePreparacao, Date.now() + 16000),
        onProgress: ctx.onProgress,
        webview: ctx.webview
      }).catch(() => null));
      ctx.anuncios = (ctx.materializado && ctx.materializado.anuncios) || [];
      ctx.totalVisiveis = Math.min(ctx.limite, Number(ctx.materializado && ctx.materializado.totalVisiveis) || ctx.anuncios.length);
      ctx.materializado = ctx.materializado || { anuncios: ctx.anuncios, totalVisiveis: ctx.totalVisiveis };
      ctx.materializado.originalY = originalY;
    }
    await aplicarEmergenciaInicial(ctx, originalY);
    emitirProgressoPrimeiraPaginaFavoritos(ctx.onProgress, resumoPrimeiraPaginaFavoritos(ctx.totalVisiveis, ctx.anuncios, {
      etapa: 'materializando', y: 0, height: metrica && metrica.height
    }));
  }

  async function materializarBaseSemCliques(ctx) {
    ctx.materializado = await ctx.controle.aguardar(materializarCardsPrimeiraPaginaMercadoLivreFavoritos({
      limite: ctx.limite,
      deadlineMs: Math.min(ctx.deadlinePreparacao, Date.now() + 28000),
      onProgress: ctx.onProgress,
      webview: ctx.webview
    }));
    ctx.anuncios = (ctx.materializado && ctx.materializado.anuncios) || [];
    ctx.totalVisiveis = Math.min(ctx.limite, Number(ctx.materializado && ctx.materializado.totalVisiveis) || ctx.anuncios.length);
    await aplicarEmergenciaInicial(ctx);
  }

  async function prepararMaterializacaoPrimeiraPagina(ctx) {
    const preparado = await ctx.controle.aguardar(ctx.webview.executeJavaScript(
      pageScripts.render('coletar-primeira-pagina-favoritos-controlada-1', {}), true
    ).catch(() => false));
    ctx.incrementalAtivo = ctx.opcoes.incremental !== false && preparado === true;
    ctx.controle.verificar();
    if (ctx.loteCliques > 0) await materializarBaseComCliques(ctx);
    else await materializarBaseSemCliques(ctx);
    ctx.controle.verificar();
    ctx.anuncios = await ctx.controle.aguardar(completarBaseMercadoLivreComApiFavoritos(ctx.anuncios, {
      concorrencia: 4,
      deadlineMs: Math.min(ctx.deadlinePreparacao, Date.now() + (ctx.loteCliques > 0 ? 2500 : 18000)),
      maxItens: ctx.loteCliques > 0 ? 4 : 0,
      timeoutMs: ctx.loteCliques > 0 ? 1200 : 3500
    }));
    ctx.fimMaterializacao = Date.now();
    const metrica = await ctx.controle.aguardar(obterMetricaRolagemMercadoLivreFavoritos(ctx.webview));
    const viewport = Math.max(560, Number(metrica && metrica.view) || 800);
    const altura = alturaVarreduraPrimeiraPaginaFavoritos(
      Number(metrica && metrica.height) || viewport,
      Number(metrica && metrica.resultsBottom) || 0,
      viewport
    );
    ctx.posicoesUnicas = montarPosicoesVarreduraPrimeiraPaginaFavoritos(altura, viewport, ctx.limite);
  }

  function normalizarChavePendenciaAvant(chave) {
    return String(chave || '').replace(/^id:/i, 'mlb:').replace(/^url:/i, 'link:').toLowerCase();
  }

  function registrarCapturasAvant(ctx, itens, pendentes) {
    (itens || []).forEach(item => {
      const chave = normalizarChavePendenciaAvant(chaveCanonicaAnuncioFavoritos(item));
      if (!chave) return;
      pendentes.delete(chave);
      ctx.chavesCapturadasAvant.add(chave);
    });
  }

  async function acionarAvantNaPosicao(ctx, index, avantAntes, pendentes) {
    const ultimaPosicao = index === ctx.posicoesUnicas.length - 1;
    const buscaProfunda = !ctx.incrementalAtivo || ultimaPosicao || !(avantAntes && Number(avantAntes.total) > 0);
    const verificarLogin = !ctx.incrementalAtivo || index === 0 || ultimaPosicao;
    const clickInfo = ctx.loteCliques > 0 && !ctx.cliquesAvantDesligados
      ? await ctx.controle.aguardar(acionarCardsAvantProFilaWebview({
        maxClicks: ctx.loteCliques,
        maxRuntimeMs: ctx.cliquesAvantDesligados
          ? Math.min(1600, Math.max(1200, ctx.deadline - Date.now() - 500))
          : Math.min(4000, Math.max(1500, ctx.deadline - Date.now() - 500)),
        deepScan: buscaProfunda,
        checkLogin: verificarLogin,
        webview: ctx.webview
      }))
      : { clicked: 0, totalCandidates: 0, keys: [], capturados: 0, skipped: true, slowDisabled: ctx.cliquesAvantDesligados };
    if (clickInfo && clickInfo.slowDisabled) ctx.cliquesAvantDesligados = true;
    if (clickInfo && clickInfo.error && ctx.incrementalAtivo) ctx.incrementalAtivo = false;
    (Array.isArray(clickInfo && clickInfo.pendingKeys) ? clickInfo.pendingKeys : []).forEach(chave => {
      const normalizada = normalizarChavePendenciaAvant(chave);
      if (normalizada) pendentes.add(normalizada);
    });
    (Array.isArray(clickInfo && clickInfo.resolvedKeys) ? clickInfo.resolvedKeys : []).forEach(chave => {
      const normalizada = normalizarChavePendenciaAvant(chave);
      if (!normalizada) return;
      pendentes.delete(normalizada);
      ctx.chavesCapturadasAvant.add(normalizada);
    });
    if (ctx.loteCliques > 0 && ctx.cliquesAvantDesligados && pendentes.size === 0) pendentes.add('__avant_slow_disabled__');
    ctx.totalCliquesAvant += Math.max(0, Number(clickInfo && clickInfo.clicked) || 0);
    ctx.totalCapturadosAvant = Math.max(ctx.chavesCapturadasAvant.size, ctx.totalCapturadosAvant);
    if (clickInfo && clickInfo.loginBlocked) {
      ctx.cliquesAvantDesligados = true;
      ctx.loginAvantBloqueado = true;
    }
    return { clickInfo, buscaProfunda };
  }

  async function capturarResultadosPosClique(ctx, index, clickInfo, buscaProfunda) {
    if (clickInfo && clickInfo.clicked) {
      await ctx.controle.esperar(Math.min(1800, 500 + (clickInfo.clicked * 200)));
      await ctx.controle.aguardar(aguardarDadosAvantProEstaveisWebview({
        minWaitMs: 120, stableMs: 300, maxWaitMs: 1600, webview: ctx.webview
      }).catch(() => null));
    }
    const avantDepois = clickInfo && clickInfo.clicked
      ? await ctx.controle.aguardar(capturarAvantProCardsVisiveisRapidoWebview({ limite: ctx.limite, webview: ctx.webview }).catch(() => null))
      : null;
    ctx.anuncios = mesclarAnunciosAvant(ctx.anuncios, (avantDepois && avantDepois.anuncios) || []);
    const avantDom = (!ctx.incrementalAtivo || buscaProfunda || (clickInfo && clickInfo.clicked))
      ? await ctx.controle.aguardar(extrairAnunciosAvantProDomWebview({ limite: ctx.limite, webview: ctx.webview }).catch(() => null))
      : null;
    ctx.anuncios = mesclarAnunciosAvant(ctx.anuncios, (avantDom && avantDom.anuncios) || []);
    const avantCache = await ctx.controle.aguardar(extrairCacheAvantProCardsWebview({ limite: ctx.limite, webview: ctx.webview }).catch(() => null));
    ctx.anuncios = mesclarAnunciosAvant(ctx.anuncios, (avantCache && avantCache.anuncios) || []);
    const atualizarBase = index === 0 || index === ctx.posicoesUnicas.length - 1 || index % 4 === 3 || Date.now() + 4500 >= ctx.deadline;
    const basico = atualizarBase
      ? await ctx.controle.aguardar(extrairCardsMercadoLivreBasicoWebview({ limite: ctx.limite, webview: ctx.webview }).catch(() => null))
      : null;
    if (basico && Array.isArray(basico.anuncios)) ctx.anuncios = mesclarAnunciosAvant(basico.anuncios, ctx.anuncios);
    ctx.totalVisiveis = Math.max(
      ctx.totalVisiveis,
      Math.min(ctx.limite, Number(basico && basico.total) || ((basico && basico.anuncios && basico.anuncios.length) || 0)),
      Math.min(ctx.limite, Number(avantCache && avantCache.total) || ((avantCache && avantCache.anuncios && avantCache.anuncios.length) || 0)),
      ctx.anuncios.length
    );
  }

  async function processarPosicaoPrimeiraPagina(ctx, passada, index, pendentes) {
    ctx.posicoesPercorridasTotal += 1;
    ctx.controle.verificar();
    await ctx.controle.aguardar(rolarMercadoLivreFavoritos(ctx.posicoesUnicas[index], ctx.webview));
    await ctx.controle.esperar(100);
    const avantAntes = await ctx.controle.aguardar(capturarAvantProCardsVisiveisRapidoWebview({
      limite: ctx.limite, webview: ctx.webview
    }).catch(() => null));
    ctx.anuncios = mesclarAnunciosAvant(ctx.anuncios, (avantAntes && avantAntes.anuncios) || []);
    registrarCapturasAvant(ctx, (avantAntes && avantAntes.anuncios) || [], pendentes);
    const { clickInfo, buscaProfunda } = await acionarAvantNaPosicao(ctx, index, avantAntes, pendentes);
    await capturarResultadosPosClique(ctx, index, clickInfo, buscaProfunda);
    const resumo = resumoPrimeiraPaginaFavoritos(ctx.totalVisiveis, ctx.anuncios, {
      etapa: 'avant', passada, maxPassadas: ctx.maxPassadas, posicao: index + 1,
      posicoes: ctx.posicoesUnicas.length, clicados: clickInfo && clickInfo.clicked || 0,
      candidatosAvant: clickInfo && clickInfo.totalCandidates || 0,
      capturadosAvant: clickInfo && clickInfo.capturados || 0,
      erroCliqueAvant: clickInfo && clickInfo.error || '',
      loginAvantBloqueado: !!(clickInfo && clickInfo.loginBlocked),
      cliquesAvantDesligados: !!(clickInfo && clickInfo.slowDisabled),
      tempoRestanteMs: Math.max(0, ctx.deadline - Date.now())
    });
    ctx.controle.verificar();
    emitirProgressoPrimeiraPaginaFavoritos(ctx.onProgress, resumo);
    if (!(ctx.loginAvantBloqueado && ctx.anuncios.length)
      && !(ctx.totalVisiveis > 0 && resumo.com_dados_avant >= ctx.totalVisiveis)) await ctx.controle.esperar(20);
    return resumo;
  }

  async function lerEstadoIncrementalPrimeiraPagina(ctx) {
    if (!ctx.incrementalAtivo) return null;
    await ctx.controle.esperar(800);
    return ctx.controle.aguardar(ctx.webview.executeJavaScript(`
      (function () {
        var state = window.__JK_FAVORITOS_INCREMENTAL_COLLECTOR_V1;
        return state ? {
          mutationVersion: Number(state.mutationVersion) || 0,
          lastMutationAt: Number(state.lastMutationAt) || 0,
          quietMs: Math.max(0, Date.now() - (Number(state.lastMutationAt) || Date.now()))
        } : null;
      })();
    `, true).catch(() => null));
  }

  async function concluirPassadaPrimeiraPagina(ctx, passada, percorridas, pendentes) {
    ctx.passadasExecutadas = passada;
    const estado = await lerEstadoIncrementalPrimeiraPagina(ctx);
    const assinatura = assinaturaEstabilidadePrimeiraPaginaFavoritos(ctx.anuncios);
    const passadaCompleta = percorridas >= ctx.posicoesUnicas.length;
    const plateau = deveEncerrarPlateauPrimeiraPaginaFavoritos({
      passada,
      passadaCompleta,
      assinaturaAtual: assinatura,
      assinaturaAnterior: ctx.assinaturaPassadaAnterior,
      pendentesAvant: pendentes.size,
      mutationQuietMs: estado ? estado.quietMs : undefined
    });
    const resumo = resumoPrimeiraPaginaFavoritos(ctx.totalVisiveis, ctx.anuncios, {
      etapa: 'passada', passada, maxPassadas: ctx.maxPassadas,
      loginAvantBloqueado: ctx.loginAvantBloqueado,
      login_avant_bloqueado: ctx.loginAvantBloqueado,
      passada_completa: passadaCompleta, pendentes_avant: pendentes.size,
      mutation_quiet_ms: estado ? estado.quietMs : 0,
      incremental: ctx.incrementalAtivo, plateau_estavel: plateau,
      tempoRestanteMs: Math.max(0, ctx.deadline - Date.now())
    });
    emitirProgressoPrimeiraPaginaFavoritos(ctx.onProgress, resumo);
    if (ctx.loginAvantBloqueado && ctx.anuncios.length) ctx.motivoEncerramento = 'login_avant';
    else if (ctx.totalVisiveis > 0 && resumo.com_dados_avant >= ctx.totalVisiveis) ctx.motivoEncerramento = 'completude_avant';
    else if (plateau) ctx.motivoEncerramento = 'stable_plateau';
    else ctx.assinaturaPassadaAnterior = assinatura;
  }

  async function executarVarreduraPrimeiraPagina(ctx) {
    for (let passada = 1; passada <= ctx.maxPassadas && Date.now() < ctx.deadline && !ctx.loginAvantBloqueado; passada += 1) {
      const pendentes = new Set();
      let percorridas = 0;
      for (let index = 0; index < ctx.posicoesUnicas.length && Date.now() < ctx.deadline && !ctx.loginAvantBloqueado; index += 1) {
        percorridas = index + 1;
        const resumo = await processarPosicaoPrimeiraPagina(ctx, passada, index, pendentes);
        if ((ctx.loginAvantBloqueado && ctx.anuncios.length)
          || (ctx.totalVisiveis > 0 && resumo.com_dados_avant >= ctx.totalVisiveis)) break;
      }
      await concluirPassadaPrimeiraPagina(ctx, passada, percorridas, pendentes);
      if (ctx.motivoEncerramento) break;
    }
    ctx.fimAvant = Date.now();
    if (!ctx.motivoEncerramento) ctx.motivoEncerramento = Date.now() >= ctx.deadline ? 'tempo_limite' : 'max_passadas';
  }

  function montarResultadoPrimeiraPagina(ctx, resumoFinal) {
    const anunciosSaida = ctx.anuncios.slice(0, ctx.limite);
    try {
      Object.defineProperty(anunciosSaida, '__avantNaoVinculado', {
        value: Array.isArray(ctx.anuncios.__avantNaoVinculado) ? ctx.anuncios.__avantNaoVinculado : [],
        enumerable: false
      });
    } catch (_err) {
      anunciosSaida.__avantNaoVinculado = Array.isArray(ctx.anuncios.__avantNaoVinculado) ? ctx.anuncios.__avantNaoVinculado : [];
    }
    return {
      success: true, totalVisiveis: ctx.totalVisiveis, anuncios: anunciosSaida, resumo: resumoFinal,
      loginAvantBloqueado: ctx.loginAvantBloqueado, tempoEsgotado: !!resumoFinal.tempo_esgotado,
      elapsedMs: resumoFinal.elapsedMs, motivoEncerramento: ctx.motivoEncerramento,
      passadasExecutadas: ctx.passadasExecutadas, posicoesPercorridas: ctx.posicoesPercorridasTotal,
      totalCliquesAvant: ctx.totalCliquesAvant, totalCapturadosAvant: ctx.totalCapturadosAvant
    };
  }

  async function finalizarPrimeiraPagina(ctx) {
    ctx.controle.verificar();
    const basico = await ctx.controle.aguardar(extrairCardsMercadoLivreBasicoWebview({ limite: ctx.limite, webview: ctx.webview }).catch(() => null));
    const cache = Date.now() < ctx.deadline
      ? await ctx.controle.aguardar(extrairCacheAvantProCardsWebview({ limite: ctx.limite, webview: ctx.webview }).catch(() => null)) : null;
    const dom = Date.now() < ctx.deadline
      ? await ctx.controle.aguardar(extrairAnunciosAvantProDomWebview({ limite: ctx.limite, webview: ctx.webview }).catch(() => null)) : null;
    ctx.anuncios = mesclarAnunciosAvant((basico && basico.anuncios) || [], ctx.anuncios);
    ctx.anuncios = mesclarAnunciosAvant(ctx.anuncios, (cache && cache.anuncios) || []);
    ctx.anuncios = mesclarAnunciosAvant(ctx.anuncios, (dom && dom.anuncios) || []);
    if (!ctx.anuncios.length) {
      const emergencia = await ctx.controle.aguardar(extrairBaseMercadoLivreEmergencialWebview({
        limite: ctx.limite,
        timeoutMs: Math.min(12000, Math.max(4500, ctx.deadline - Date.now())),
        webview: ctx.webview
      }).catch(() => null));
      if (emergencia && Array.isArray(emergencia.anuncios) && emergencia.anuncios.length) {
        ctx.anuncios = mesclarAnunciosAvant(emergencia.anuncios, ctx.anuncios);
      }
    }
    ctx.anuncios = await ctx.controle.aguardar(completarBaseMercadoLivreComApiFavoritos(ctx.anuncios, {
      concorrencia: 4, deadlineMs: ctx.deadline
    }));
    const fimFinalizacao = Date.now();
    ctx.totalVisiveis = Math.max(
      ctx.totalVisiveis,
      Math.min(ctx.limite, Number(basico && basico.total) || ((basico && basico.anuncios && basico.anuncios.length) || 0)),
      Math.min(ctx.limite, Number(cache && cache.total) || ((cache && cache.anuncios && cache.anuncios.length) || 0)),
      Math.min(ctx.limite, Number(dom && dom.total) || ((dom && dom.anuncios && dom.anuncios.length) || 0)),
      ctx.anuncios.length
    );
    await ctx.controle.aguardar(rolarMercadoLivreFavoritos(Number(ctx.materializado && ctx.materializado.originalY) || 0, ctx.webview));
    ctx.controle.verificar();
    const resumo = resumoPrimeiraPaginaFavoritos(ctx.totalVisiveis, ctx.anuncios, {
      etapa: 'final', loginAvantBloqueado: ctx.loginAvantBloqueado,
      login_avant_bloqueado: ctx.loginAvantBloqueado, tempo_esgotado: Date.now() >= ctx.deadline,
      motivo_encerramento: ctx.motivoEncerramento, passadas: ctx.passadasExecutadas,
      posicoes_percorridas: ctx.posicoesPercorridasTotal, cliques_avant: ctx.totalCliquesAvant,
      capturados_avant: ctx.totalCapturadosAvant,
      tempo_materializacao_ms: Math.max(0, ctx.fimMaterializacao - ctx.inicio),
      tempo_avant_ms: Math.max(0, ctx.fimAvant - ctx.fimMaterializacao),
      tempo_finalizacao_ms: Math.max(0, fimFinalizacao - ctx.fimAvant), elapsedMs: Date.now() - ctx.inicio
    });
    emitirProgressoPrimeiraPaginaFavoritos(ctx.onProgress, resumo);
    return montarResultadoPrimeiraPagina(ctx, resumo);
  }

  async function coletarPrimeiraPaginaFavoritosControlada(opcoes = {}) {
    const webview = resolverWebviewFavoritosColeta(opcoes);
    if (!webview || typeof webview.executeJavaScript !== 'function') {
      return { success: false, totalVisiveis: 0, anuncios: [], error: 'webview_indisponivel' };
    }
    const ctx = criarContextoPrimeiraPagina(opcoes, webview);
    await prepararMaterializacaoPrimeiraPagina(ctx);
    await executarVarreduraPrimeiraPagina(ctx);
    return finalizarPrimeiraPagina(ctx);
  }

  const api = { coletarPrimeiraPaginaFavoritosControlada };
  browser.firstPageController = Object.freeze(api);
  Object.assign(global, api);
})(window);
