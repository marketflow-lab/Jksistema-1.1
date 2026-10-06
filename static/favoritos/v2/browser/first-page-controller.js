(function (global) {
  'use strict';

  const browser = global.FavoritosV2.browser;
  const pageScripts = browser.pageScripts;

  const tentativasPorNavegador = new WeakMap();
  const tentativasPorWorker = new Map();
  let ultimaGeracao = 0;

  function erroPrazoPrimeiraPagina() {
    const err = new Error('Orcamento da coleta da primeira pagina esgotado.');
    err.favoritosOrcamentoEsgotado = true;
    return err;
  }

  function criarControleCancelamentoPrimeiraPagina(signal, deadline, atual) {
    let fechado = false;
    const verificar = () => {
      if (signal && signal.aborted || !atual()) {
        const err = new Error('Coleta de favoritos cancelada.');
        err.name = 'AbortError';
        err.canceladoFavoritos = true;
        throw err;
      }
      if (fechado || Date.now() >= deadline) throw erroPrazoPrimeiraPagina();
    };
    const restante = () => Math.max(0, deadline - Date.now());
    const aguardar = factory => {
      verificar();
      let timer = null;
      let onAbort = null;
      const execucao = Promise.resolve().then(() => {
        verificar();
        return typeof factory === 'function' ? factory() : factory;
      }).then(value => { verificar(); return value; });
      const interrupcao = new Promise((_resolve, reject) => {
        timer = setTimeout(() => reject(erroPrazoPrimeiraPagina()), restante());
        if (signal && typeof signal.addEventListener === 'function') {
          onAbort = () => { try { verificar(); } catch (err) { reject(err); } };
          signal.addEventListener('abort', onAbort, { once: true });
        }
      });
      return Promise.race([execucao, interrupcao]).finally(() => {
        if (timer !== null) clearTimeout(timer);
        if (onAbort) signal.removeEventListener('abort', onAbort);
      });
    };
    const esperarControlado = ms => {
      let timer = null;
      return aguardar(() => new Promise(resolve => {
        timer = setTimeout(resolve, Math.min(Math.max(0, ms), restante()));
      })).finally(() => { if (timer !== null) clearTimeout(timer); });
    };
    return { verificar, aguardar, esperar: esperarControlado, restante, deadline, fechar: () => { fechado = true; } };
  }

  function clonarAnunciosPrimeiraPagina(anuncios) {
    const copia = JSON.parse(JSON.stringify(Array.isArray(anuncios) ? anuncios : []));
    if (Array.isArray(anuncios && anuncios.__avantNaoVinculado)) {
      Object.defineProperty(copia, '__avantNaoVinculado', {
        value: JSON.parse(JSON.stringify(anuncios.__avantNaoVinculado)), enumerable: false
      });
    }
    return copia;
  }

  function confirmadoAvantPrimeiraPagina(item) {
    const fonte = normalizarFonte(item && (item.vendasFonte || item.vendas_fonte) || '');
    return !!(item && /(?:^|[+,|/])\s*avantpro_(?:anuncio|dom|fast_dom|cache|card|produto)(?:$|[+,|/])/.test(fonte)
      && fonteVendasConfiavel(fonte) && hasNumeroVendas(parseNumeroVendas(item.vendas)));
  }

  function publicarSnapshotPrimeiraPagina(ctx) {
    if (!ctx.atual() || ctx.opcoes.signal && ctx.opcoes.signal.aborted) return;
    ctx.anuncios.forEach(item => {
      if (confirmadoAvantPrimeiraPagina(item)) ctx.chavesCapturadasAvant.add(normalizarChavePendenciaAvant(chaveCanonicaAnuncioFavoritos(item)));
    });
    if (typeof ctx.opcoes.onSnapshot !== 'function') return;
    ctx.opcoes.onSnapshot({
      anuncios: clonarAnunciosPrimeiraPagina(ctx.anuncios), totalVisiveis: ctx.totalVisiveis,
      confirmedKeys: Array.from(ctx.chavesCapturadasAvant), tempoEsgotado: ctx.motivoEncerramento === 'tempo_limite',
      loginAvantBloqueado: ctx.loginAvantBloqueado
    });
  }

  function criarNavegadorControladoPrimeiraPagina(ctx, controle) {
    const adapter = Object.create(ctx.navegadorOriginal);
    adapter.executeJavaScript = (code, userGesture) => controle.aguardar(() => {
      const body = String(code || '').trim().replace(/;\s*$/, '');
      const seed = ctx.anuncios.filter(item => ctx.chavesCapturadasAvant.has(normalizarChavePendenciaAvant(chaveCanonicaAnuncioFavoritos(item))));
      const guarded = `(function () {
        var __jkFavoritosGeracao = ${JSON.stringify(ctx.geracao)};
        var __jkFavoritosDeadline = ${JSON.stringify(controle.deadline)};
        var __jkFavoritosSeedConfirmed = ${JSON.stringify(seed)};
        var __jkFavoritosConfirmedKeys = ${JSON.stringify(Array.from(ctx.chavesCapturadasAvant))};
        var anterior = window.__JK_FAVORITOS_COLETA_TENTATIVA;
        if (Date.now() >= __jkFavoritosDeadline || anterior && anterior.geracao > __jkFavoritosGeracao) {
          throw new Error('Tentativa da coleta expirada.');
        }
        window.__JK_FAVORITOS_COLETA_TENTATIVA = { geracao: __jkFavoritosGeracao };
        var __jkFavoritosVerificar = function () {
          if (Date.now() >= __jkFavoritosDeadline || !window.__JK_FAVORITOS_COLETA_TENTATIVA
            || window.__JK_FAVORITOS_COLETA_TENTATIVA.geracao !== __jkFavoritosGeracao) {
            throw new Error('Tentativa da coleta expirada.');
          }
        };
        __jkFavoritosVerificar();
        return (${body});
      })();`;
      return ctx.navegadorOriginal.executeJavaScript(guarded, userGesture);
    });
    return adapter;
  }

  function criarContextoPrimeiraPagina(opcoes, webview) {
    const loteValor = opcoes.loteCliques === undefined ? 0 : Number(opcoes.loteCliques);
    const tempoLimiteMs = Math.max(30000, Math.min(Number(opcoes.tempoLimiteMs) || 180000, 180000));
    const inicio = Date.now();
    const loteCliques = Math.max(0, Math.min(Number.isFinite(loteValor) ? loteValor : 0, 8));
    const reservaAvantMs = loteCliques > 0
      ? Math.min(Math.max(15000, Math.floor(tempoLimiteMs * 0.55)), Math.max(12000, tempoLimiteMs - 12000))
      : 0;
    const deadlineTotal = inicio + tempoLimiteMs;
    const deadline = deadlineTotal - Math.min(5000, tempoLimiteMs / 2);
    const geracao = ultimaGeracao = Math.max(ultimaGeracao + 1, inicio);
    const registro = webview.workerId ? tentativasPorWorker : tentativasPorNavegador;
    const chave = webview.workerId || webview;
    let ativo = true;
    const atual = () => ativo && registro.get(chave) === geracao;
    registro.set(chave, geracao);
    const controle = criarControleCancelamentoPrimeiraPagina(opcoes.signal || null, deadline, atual);
    const anunciosSeed = clonarAnunciosPrimeiraPagina(opcoes.anunciosSeed);
    const requestedKeys = new Set((opcoes.confirmedKeys || []).map(normalizarChavePendenciaAvant));
    const confirmedKeys = anunciosSeed.filter(item => confirmadoAvantPrimeiraPagina(item)
      && requestedKeys.has(normalizarChavePendenciaAvant(chaveCanonicaAnuncioFavoritos(item))))
      .map(item => normalizarChavePendenciaAvant(chaveCanonicaAnuncioFavoritos(item)));
    const ctx = {
      opcoes,
      navegadorOriginal: webview, controle, geracao, atual,
      fechar: () => { ativo = false; controle.fechar(); },
      limite: Math.max(20, Math.min(Number(opcoes.maxAnuncios) || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80, 100)),
      maxPassadas: Math.max(1, Math.min(Number(opcoes.maxPassadas) || 3, 3)),
      loteCliques,
      inicio,
      deadline, deadlineTotal,
      deadlinePreparacao: reservaAvantMs > 0 ? Math.max(inicio + 8000, deadline - reservaAvantMs) : deadline,
      onProgress: payload => {
        if (atual() && !(opcoes.signal && opcoes.signal.aborted) && Date.now() < deadlineTotal
          && typeof opcoes.onProgress === 'function') opcoes.onProgress(payload);
      },
      incrementalAtivo: false,
      materializado: null,
      anuncios: anunciosSeed,
      totalVisiveis: anunciosSeed.length,
      cliquesAvantDesligados: false,
      loginAvantBloqueado: false,
      assinaturaPassadaAnterior: '',
      motivoEncerramento: '',
      passadasExecutadas: 0,
      posicoesPercorridasTotal: 0,
      totalCliquesAvant: 0,
      totalCapturadosAvant: 0,
      chavesCapturadasAvant: new Set(confirmedKeys),
      fimMaterializacao: inicio, fimAvant: inicio
    };
    ctx.webview = criarNavegadorControladoPrimeiraPagina(ctx, controle);
    ctx.checkpoint = snapshot => {
      controle.verificar();
      ctx.anuncios = mesclarAnunciosAvant(ctx.anuncios, snapshot.anuncios || []);
      ctx.totalVisiveis = Math.max(ctx.totalVisiveis, Number(snapshot.totalVisiveis) || ctx.anuncios.length);
      publicarSnapshotPrimeiraPagina(ctx);
    };
    return ctx;
  }

  async function aplicarEmergenciaInicial(ctx, originalY) {
    if (ctx.anuncios.length || Date.now() >= ctx.deadlinePreparacao) return;
    const emergencia = await ctx.controle.aguardar(() => extrairBaseMercadoLivreEmergencialWebview({
      limite: ctx.limite,
      timeoutMs: Math.max(0, Math.min(9000, ctx.deadlinePreparacao - Date.now())),
      webview: ctx.webview
    }).catch(() => null));
    if (!emergencia || !Array.isArray(emergencia.anuncios) || !emergencia.anuncios.length) return;
    ctx.anuncios = emergencia.anuncios;
    ctx.totalVisiveis = Math.min(ctx.limite, Number(emergencia.total) || ctx.anuncios.length);
    ctx.materializado = { ...(ctx.materializado || {}), anuncios: ctx.anuncios, totalVisiveis: ctx.totalVisiveis, emergencia: true };
    if (originalY !== undefined) ctx.materializado.originalY = originalY;
  }

  async function materializarBaseComCliques(ctx) {
    const metrica = await ctx.controle.aguardar(() => obterMetricaRolagemMercadoLivreFavoritos(ctx.webview));
    const originalY = Math.max(0, Number(metrica && metrica.y) || 0);
    await ctx.controle.aguardar(() => rolarMercadoLivreFavoritos(0, ctx.webview));
    await ctx.controle.esperar(350);
    const basico = await ctx.controle.aguardar(() => aguardarBaseMercadoLivreColetavelFavoritos({
      limite: ctx.limite,
      timeoutMs: Math.max(0, Math.min(30000, ctx.deadlinePreparacao - Date.now())),
      onProgress: ctx.onProgress, controle: ctx.controle, onSnapshot: ctx.checkpoint,
      webview: ctx.webview
    }).catch(() => null));
    ctx.anuncios = mesclarAnunciosAvant(ctx.anuncios, (basico && basico.anuncios) || []);
    ctx.totalVisiveis = Math.max(ctx.totalVisiveis, Math.min(ctx.limite, Number(basico && basico.total) || ctx.anuncios.length));
    ctx.materializado = { anuncios: ctx.anuncios, totalVisiveis: ctx.totalVisiveis, originalY };
    if (!ctx.anuncios.length && Date.now() < ctx.deadlinePreparacao) {
      ctx.materializado = await ctx.controle.aguardar(() => materializarCardsPrimeiraPaginaMercadoLivreFavoritos({
        limite: ctx.limite,
        deadlineMs: Math.min(ctx.deadlinePreparacao, Date.now() + 16000),
        onProgress: ctx.onProgress, controle: ctx.controle, onSnapshot: ctx.checkpoint,
        webview: ctx.webview
      }).catch(() => null));
      ctx.anuncios = mesclarAnunciosAvant(ctx.anuncios, (ctx.materializado && ctx.materializado.anuncios) || []);
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
    ctx.materializado = await ctx.controle.aguardar(() => materializarCardsPrimeiraPaginaMercadoLivreFavoritos({
      limite: ctx.limite,
      deadlineMs: Math.min(ctx.deadlinePreparacao, Date.now() + 28000),
      onProgress: ctx.onProgress, controle: ctx.controle, onSnapshot: ctx.checkpoint,
      webview: ctx.webview
    }));
    ctx.anuncios = mesclarAnunciosAvant(ctx.anuncios, (ctx.materializado && ctx.materializado.anuncios) || []);
    ctx.totalVisiveis = Math.min(ctx.limite, Number(ctx.materializado && ctx.materializado.totalVisiveis) || ctx.anuncios.length);
    await aplicarEmergenciaInicial(ctx);
  }

  async function prepararMaterializacaoPrimeiraPagina(ctx) {
    const preparado = await ctx.controle.aguardar(() => ctx.webview.executeJavaScript(
      pageScripts.render('coletar-primeira-pagina-favoritos-controlada-1', {}), true
    ).catch(() => false));
    ctx.incrementalAtivo = ctx.opcoes.incremental !== false && preparado === true;
    ctx.controle.verificar();
    if (!ctx.anuncios.length) {
      if (ctx.loteCliques > 0) await materializarBaseComCliques(ctx);
      else await materializarBaseSemCliques(ctx);
    }
    ctx.controle.verificar();
    ctx.anuncios = await ctx.controle.aguardar(() => completarBaseMercadoLivreComApiFavoritos(ctx.anuncios, {
      concorrencia: 4, controle: ctx.controle,
      deadlineMs: Math.min(ctx.deadlinePreparacao, Date.now() + (ctx.loteCliques > 0 ? 2500 : 18000)),
      maxItens: ctx.loteCliques > 0 ? 4 : 0,
      timeoutMs: ctx.loteCliques > 0 ? 1200 : 3500
    }));
    ctx.fimMaterializacao = Date.now();
    const metrica = await ctx.controle.aguardar(() => obterMetricaRolagemMercadoLivreFavoritos(ctx.webview));
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
      if (!chave || !confirmadoAvantPrimeiraPagina(item)) return;
      pendentes.delete(chave);
      ctx.chavesCapturadasAvant.add(chave);
    });
  }

  async function acionarAvantNaPosicao(ctx, index, avantAntes, pendentes) {
    const ultimaPosicao = index === ctx.posicoesUnicas.length - 1;
    const buscaProfunda = !ctx.incrementalAtivo || ultimaPosicao || !(avantAntes && Number(avantAntes.total) > 0);
    const verificarLogin = !ctx.incrementalAtivo || index === 0 || ultimaPosicao;
    const clickInfo = ctx.loteCliques > 0 && !ctx.cliquesAvantDesligados
      ? await ctx.controle.aguardar(() => acionarCardsAvantProFilaWebview({
        maxClicks: ctx.loteCliques,
        maxRuntimeMs: ctx.cliquesAvantDesligados
          ? Math.min(1600, ctx.controle.restante())
          : Math.min(4000, ctx.controle.restante()),
        deepScan: buscaProfunda,
        checkLogin: verificarLogin, controle: ctx.controle,
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
      await ctx.controle.aguardar(() => aguardarDadosAvantProEstaveisWebview({
        minWaitMs: 120, stableMs: 300, maxWaitMs: 1600, webview: ctx.webview
      }).catch(() => null));
    }
    const avantDepois = clickInfo && clickInfo.clicked
      ? await ctx.controle.aguardar(() => capturarAvantProCardsVisiveisRapidoWebview({ limite: ctx.limite, webview: ctx.webview }).catch(() => null))
      : null;
    ctx.anuncios = mesclarAnunciosAvant(ctx.anuncios, (avantDepois && avantDepois.anuncios) || []);
    publicarSnapshotPrimeiraPagina(ctx);
    const avantDom = (!ctx.incrementalAtivo || buscaProfunda || (clickInfo && clickInfo.clicked))
      ? await ctx.controle.aguardar(() => extrairAnunciosAvantProDomWebview({ limite: ctx.limite, webview: ctx.webview }).catch(() => null))
      : null;
    ctx.anuncios = mesclarAnunciosAvant(ctx.anuncios, (avantDom && avantDom.anuncios) || []);
    publicarSnapshotPrimeiraPagina(ctx);
    const avantCache = await ctx.controle.aguardar(() => extrairCacheAvantProCardsWebview({ limite: ctx.limite, webview: ctx.webview }).catch(() => null));
    ctx.anuncios = mesclarAnunciosAvant(ctx.anuncios, (avantCache && avantCache.anuncios) || []);
    publicarSnapshotPrimeiraPagina(ctx);
    const atualizarBase = index === 0 || index === ctx.posicoesUnicas.length - 1 || index % 4 === 3 || Date.now() + 4500 >= ctx.deadline;
    const basico = atualizarBase
      ? await ctx.controle.aguardar(() => extrairCardsMercadoLivreBasicoWebview({ limite: ctx.limite, webview: ctx.webview }).catch(() => null))
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
    await ctx.controle.aguardar(() => rolarMercadoLivreFavoritos(ctx.posicoesUnicas[index], ctx.webview));
    await ctx.controle.esperar(100);
    const avantAntes = await ctx.controle.aguardar(() => capturarAvantProCardsVisiveisRapidoWebview({
      limite: ctx.limite, webview: ctx.webview
    }).catch(() => null));
    ctx.anuncios = mesclarAnunciosAvant(ctx.anuncios, (avantAntes && avantAntes.anuncios) || []);
    registrarCapturasAvant(ctx, (avantAntes && avantAntes.anuncios) || [], pendentes);
    publicarSnapshotPrimeiraPagina(ctx);
    const { clickInfo, buscaProfunda } = await acionarAvantNaPosicao(ctx, index, avantAntes, pendentes);
    await capturarResultadosPosClique(ctx, index, clickInfo, buscaProfunda);
    publicarSnapshotPrimeiraPagina(ctx);
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
    return ctx.controle.aguardar(() => ctx.webview.executeJavaScript(`
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
      totalCliquesAvant: ctx.totalCliquesAvant, totalCapturadosAvant: ctx.chavesCapturadasAvant.size,
      confirmedKeys: Array.from(ctx.chavesCapturadasAvant)
    };
  }

  async function finalizarPrimeiraPagina(ctx) {
    try {
      const basico = await ctx.controle.aguardar(() => extrairCardsMercadoLivreBasicoWebview({ limite: ctx.limite, webview: ctx.webview }));
      ctx.anuncios = mesclarAnunciosAvant((basico && basico.anuncios) || [], ctx.anuncios);
      ctx.totalVisiveis = Math.max(ctx.totalVisiveis, Number(basico && basico.total) || ctx.anuncios.length);
      publicarSnapshotPrimeiraPagina(ctx);
      const cache = await ctx.controle.aguardar(() => extrairCacheAvantProCardsWebview({ limite: ctx.limite, webview: ctx.webview }));
      ctx.anuncios = mesclarAnunciosAvant(ctx.anuncios, (cache && cache.anuncios) || []);
      publicarSnapshotPrimeiraPagina(ctx);
      const dom = await ctx.controle.aguardar(() => extrairAnunciosAvantProDomWebview({ limite: ctx.limite, webview: ctx.webview }));
      ctx.anuncios = mesclarAnunciosAvant(ctx.anuncios, (dom && dom.anuncios) || []);
      if (!ctx.anuncios.length) {
        const emergencia = await ctx.controle.aguardar(() => extrairBaseMercadoLivreEmergencialWebview({
          limite: ctx.limite, timeoutMs: Math.min(4500, ctx.controle.restante()),
          controle: ctx.controle, webview: ctx.webview
        }));
        ctx.anuncios = mesclarAnunciosAvant(ctx.anuncios, (emergencia && emergencia.anuncios) || []);
      }
      ctx.anuncios = await ctx.controle.aguardar(() => completarBaseMercadoLivreComApiFavoritos(ctx.anuncios, {
        concorrencia: 4, deadlineMs: ctx.deadlineTotal, controle: ctx.controle
      }));
      await ctx.controle.aguardar(() => rolarMercadoLivreFavoritos(Number(ctx.materializado && ctx.materializado.originalY) || 0, ctx.webview));
    } catch (err) {
      if (!err.favoritosOrcamentoEsgotado) throw err;
      ctx.motivoEncerramento = 'tempo_limite';
    }
    ctx.totalVisiveis = Math.max(ctx.totalVisiveis, ctx.anuncios.length);
    const resumo = resumoPrimeiraPaginaFavoritos(ctx.totalVisiveis, ctx.anuncios, {
      etapa: 'final', loginAvantBloqueado: ctx.loginAvantBloqueado,
      login_avant_bloqueado: ctx.loginAvantBloqueado,
      tempo_esgotado: ctx.motivoEncerramento === 'tempo_limite' || Date.now() >= ctx.deadlineTotal,
      motivo_encerramento: ctx.motivoEncerramento, passadas: ctx.passadasExecutadas,
      posicoes_percorridas: ctx.posicoesPercorridasTotal, cliques_avant: ctx.totalCliquesAvant,
      capturados_avant: ctx.chavesCapturadasAvant.size,
      tempo_materializacao_ms: Math.max(0, ctx.fimMaterializacao - ctx.inicio),
      tempo_avant_ms: Math.max(0, ctx.fimAvant - ctx.fimMaterializacao),
      tempo_finalizacao_ms: Math.max(0, Date.now() - ctx.fimAvant), elapsedMs: Date.now() - ctx.inicio
    });
    publicarSnapshotPrimeiraPagina(ctx);
    emitirProgressoPrimeiraPaginaFavoritos(ctx.onProgress, resumo);
    return montarResultadoPrimeiraPagina(ctx, resumo);
  }

  async function coletarPrimeiraPaginaFavoritosControlada(opcoes = {}) {
    const webview = resolverWebviewFavoritosColeta(opcoes);
    if (!webview || typeof webview.executeJavaScript !== 'function') {
      return { success: false, totalVisiveis: 0, anuncios: [], error: 'webview_indisponivel' };
    }
    const ctx = criarContextoPrimeiraPagina(opcoes, webview);
    try {
      try {
        await prepararMaterializacaoPrimeiraPagina(ctx);
        await executarVarreduraPrimeiraPagina(ctx);
      } catch (err) {
        if (!err.favoritosOrcamentoEsgotado) throw err;
        ctx.motivoEncerramento = 'tempo_limite';
      }
      ctx.fimAvant = Date.now();
      ctx.controle.fechar();
      ctx.controle = criarControleCancelamentoPrimeiraPagina(opcoes.signal || null,
        Math.min(ctx.deadlineTotal, Date.now() + 5000), ctx.atual);
      ctx.webview = criarNavegadorControladoPrimeiraPagina(ctx, ctx.controle);
      return await finalizarPrimeiraPagina(ctx);
    } finally {
      ctx.controle.fechar();
      ctx.fechar();
    }
  }

  const api = { coletarPrimeiraPaginaFavoritosControlada };
  browser.firstPageController = Object.freeze(api);
  Object.assign(global, api);
})(window);
