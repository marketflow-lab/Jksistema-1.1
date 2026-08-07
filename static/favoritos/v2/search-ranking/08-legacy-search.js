(function (global) {
  'use strict';

  const searchRanking = global.FavoritosV2.searchRanking;
  const internal = searchRanking.internal;
  if (internal.components.has('08-legacy-search')) return;

  function dependency(name) {
    const implementation = internal[name];
    if (typeof implementation !== 'function') {
      throw new Error('Dependencia de search-ranking indisponivel: ' + name);
    }
    return implementation;
  }

  function mostrarBalaoFavoritosStatus(...args) {
    return dependency('mostrarBalaoFavoritosStatus')(...args);
  }

  function erroLoginMercadoLivreFavoritos(...args) {
    return dependency('erroLoginMercadoLivreFavoritos')(...args);
  }

  function erroLoginAvantProFavoritos(...args) {
    return dependency('erroLoginAvantProFavoritos')(...args);
  }

  function erroEhLoginAvantProFavoritos(...args) {
    return dependency('erroEhLoginAvantProFavoritos')(...args);
  }

  function erroColetaMercadoLivreFavoritos(...args) {
    return dependency('erroColetaMercadoLivreFavoritos')(...args);
  }

  function statusAvantProSemDadosColetaveis(...args) {
    return dependency('statusAvantProSemDadosColetaveis')(...args);
  }

  function aguardarConexaoAvantProFavoritos(...args) {
    return dependency('aguardarConexaoAvantProFavoritos')(...args);
  }

  function verificarCancelamentoFavoritos(...args) {
    return dependency('verificarCancelamentoFavoritos')(...args);
  }

  function validarAnunciosFavoritosPertencemAoTermo(...args) {
    return dependency('validarAnunciosFavoritosPertencemAoTermo')(...args);
  }

  function preparacaoAvantPrePesquisaConfirmada(...args) {
    return dependency('preparacaoAvantPrePesquisaConfirmada')(...args);
  }

  function statusPedeLoginAvantFavoritos(status) {
    if (!status) return false;
    if (typeof statusAvantProPedeLoginOuVinculo === 'function') {
      return statusAvantProPedeLoginOuVinculo(status);
    }
    return !!(status.needsAvantLogin || status.needsAccountLink || status.accountActionRequired);
  }

  function erroSemResultadosFavoritos() {
    const erro = new Error('Mercado Livre nao encontrou resultados para esta pesquisa.');
    erro.semResultadosMl = true;
    return erro;
  }

  async function prepararAvantAntesDaBuscaFavoritos(contexto) {
    const { termo, opcoes } = contexto;
    if (opcoes.loginAvantAntesDaColeta !== true || opcoes.avantLoginPrePesquisaConfirmado === true) {
      return;
    }
    if (typeof prepararAvantProAntesDaPesquisaFavoritos !== 'function') {
      throw erroLoginAvantProFavoritos(`Nao consegui preparar o Avant Pro antes da pesquisa de "${termo}". Reabra a tela de Favoritos e tente novamente.`);
    }
    mostrarBalaoFavoritosStatus(`Preparando Avant Pro antes da pesquisa de "${termo}"...`, {
      manterNavegadorVisivel: true,
      larga: true,
      titulo: 'Preparando Avant Pro'
    });
    if (typeof abrirMercadoLivreNoPrograma === 'function') {
      if (typeof mlUrlInput !== 'undefined' && mlUrlInput) {
        mlUrlInput.value = typeof ML_DEFAULT_URL !== 'undefined'
          ? ML_DEFAULT_URL
          : 'https://www.mercadolivre.com.br/';
      }
      await abrirMercadoLivreNoPrograma({
        titulo: opcoes.titulo || 'Fazendo Favorito! Aguarde...',
        subtitulo: opcoes.subtitulo || `Preparando Avant Pro - ${termo}`,
        mostrarFavoritos: true,
        browserCompleto: true,
        forcarExibicao: true
      });
    }
    const preparacaoAvant = await prepararAvantProAntesDaPesquisaFavoritos({
      termo,
      aguardarConexao: true,
      timeoutMs: Math.max(9000, Number(opcoes.timeoutAvantPrePesquisaMs) || 22000),
      pollMs: opcoes.segundoPlano ? 650 : 350
    });
    if (!preparacaoAvantPrePesquisaConfirmada(preparacaoAvant)) {
      throw erroLoginAvantProFavoritos(`Avant Pro nao confirmou o login antes da pesquisa de "${termo}". Confirme o e-mail em Ferramentas e aguarde o aviso de obrigado antes de pesquisar.`);
    }
    verificarCancelamentoFavoritos();
  }

  async function abrirPesquisaMercadoLivreFavoritos(contexto) {
    const { termo, opcoes } = contexto;
    contexto.urlPesquisaMl = construirUrlPesquisaMercadoLivre(termo);
    mlUrlInput.value = contexto.urlPesquisaMl;
    const abriu = await abrirMercadoLivreNoPrograma({
      termoPesquisa: termo,
      titulo: opcoes.titulo || 'Fazendo Favorito! Aguarde...',
      subtitulo: opcoes.subtitulo || `Pesquisa: ${termo}`,
      mostrarFavoritos: true,
      browserCompleto: true,
      aguardarPesquisaMs: opcoes.segundoPlano ? 1200 : 900
    });
    verificarCancelamentoFavoritos();
    if (!abriu) {
      throw erroColetaMercadoLivreFavoritos(`Nao consegui abrir o Mercado Livre no navegador interno para "${termo}". Confira se a pagina carregou e se o Avant Pro esta conectado.`);
    }
    mostrarBalaoFavoritosStatus(`Abrindo resultados de "${termo}"...`);
    contexto.segundoPlano = typeof navegadorMlEmSegundoPlano === 'function' && navegadorMlEmSegundoPlano();
    contexto.leituraPaginaAutorizada = typeof acaoUsuarioFavoritosPermiteLeituraPagina === 'function'
      ? acaoUsuarioFavoritosPermiteLeituraPagina(opcoes)
      : true;
    const pesquisaConfirmada = typeof aguardarPesquisaMercadoLivreAtual === 'function'
      ? await aguardarPesquisaMercadoLivreAtual(termo, {
        url: contexto.urlPesquisaMl,
        timeoutMs: contexto.segundoPlano ? 12000 : 9000,
        pollMs: contexto.segundoPlano ? 420 : 260
      }).catch(() => null)
      : null;
    if (pesquisaConfirmada && pesquisaConfirmada.ok) {
      mostrarBalaoFavoritosStatus(`Pesquisa confirmada para "${termo}". Acionando Avant Pro para carregar dados...`, {
        larga: true,
        titulo: 'Pesquisa confirmada'
      });
    } else if (typeof garantirPesquisaMercadoLivreSubmetida === 'function') {
      await garantirPesquisaMercadoLivreSubmetida(termo, contexto.urlPesquisaMl).catch(() => null);
    }
  }

  async function aguardarEstadoInicialPesquisaFavoritos(contexto) {
    const { opcoes, segundoPlano, leituraPaginaAutorizada } = contexto;
    contexto.avantStatusPreColeta = null;
    if (opcoes.loginAvantAntesDaColeta === true && typeof diagnosticarAvantProNoWebview === 'function') {
      contexto.avantStatusPreColeta = await diagnosticarAvantProNoWebview().catch(() => null);
    }
    contexto.pronto = await aguardarPrimeirosDadosAvantOuCardsWebview({
      timeoutMs: segundoPlano ? 12000 : 9000,
      idleMs: segundoPlano ? 420 : 320,
      acaoUsuario: leituraPaginaAutorizada
    }).catch(() => null);
    if (contexto.pronto && contexto.pronto.needsLogin) throw erroLoginMercadoLivreFavoritos();
    if (contexto.pronto && contexto.pronto.needsAvantLogin && opcoes.exigirAvantPro !== true) {
      mostrarBalaoFavoritosStatus(
        segundoPlano
          ? 'Avant Pro nao retornou dados coletaveis. Vou seguir com os anuncios visiveis do Mercado Livre sem travar a fila.'
          : 'Avant Pro nao retornou dados coletaveis. Confirme manualmente o login no navegador interno.',
        { larga: true, titulo: 'Avant Pro sem dados' }
      );
      await fecharModalBloqueanteAvantProNoWebview().catch(() => null);
      await aguardarPrimeirosDadosAvantOuCardsWebview({
        timeoutMs: 900,
        idleMs: 160,
        acaoUsuario: leituraPaginaAutorizada
      }).catch(() => null);
    }
    if (contexto.pronto && contexto.pronto.noResults && !contexto.pronto.loadingScreen) {
      throw erroSemResultadosFavoritos();
    }
  }

  async function resolverCarregamentoMercadoLivreFavoritos(contexto) {
    const { termo, segundoPlano, leituraPaginaAutorizada } = contexto;
    if (!(contexto.pronto
        && contexto.pronto.loadingScreen
        && !contexto.pronto.hasCards
        && !contexto.pronto.hasAvantData
        && typeof aguardarResultadosMercadoLivreWebview === 'function')) return;
    mostrarBalaoFavoritosStatus(`Mercado Livre ainda esta carregando "${termo}". Aguardando os resultados antes do Avant Pro...`, {
      larga: true,
      titulo: 'Aguardando Mercado Livre'
    });
    const prontoMl = await aguardarResultadosMercadoLivreWebview({
      timeoutMs: segundoPlano ? 45000 : 26000,
      pollMs: segundoPlano ? 850 : 600,
      reloadAfterMs: segundoPlano ? 15000 : 8500,
      recarregarSeTravado: !mlFavoritosEmExecucao,
      mensagemRecarregando: `Mercado Livre ficou preso no carregamento de "${termo}". Recarregando a pesquisa uma vez...`
    }).catch(() => null);
    if (prontoMl) {
      contexto.pronto = {
        ...contexto.pronto,
        ...prontoMl,
        hasCards: !!(prontoMl.hasCards || Number(prontoMl.cardCount || 0) > 0),
        hasAvantData: !!(prontoMl.hasAvantData || contexto.pronto.hasAvantData)
      };
    }
    if (contexto.pronto && contexto.pronto.needsLogin) throw erroLoginMercadoLivreFavoritos();
    if (contexto.pronto && contexto.pronto.noResults && !contexto.pronto.loadingScreen) {
      throw erroSemResultadosFavoritos();
    }
    if (!(contexto.pronto && contexto.pronto.loadingScreen
        && !contexto.pronto.hasCards && !contexto.pronto.hasAvantData)) return;
    const statusAvant = typeof diagnosticarAvantProNoWebview === 'function'
      ? await diagnosticarAvantProNoWebview().catch(() => null)
      : null;
    const pedeConta = statusPedeLoginAvantFavoritos(statusAvant);
    const shellAvant = !!(statusAvant && (
      pedeConta
      || statusAvant.extensionDetected
      || statusAvant.shellOnly
      || Number(statusAvant.widgets || 0) > 0
      || Number(statusAvant.toolsButtons || 0) > 0
      || Number(statusAvant.actionButtons || 0) > 0
    ));
    if (!shellAvant) {
      throw erroColetaMercadoLivreFavoritos(`Mercado Livre ficou carregando a busca "${termo}" e nao exibiu anuncios. Reabra a pesquisa no navegador interno e tente novamente.`);
    }
    contexto.pronto = {
      ...contexto.pronto,
      loadingScreen: false,
      aguardandoAvantMesmoSemCards: true,
      avantStatusDuranteCarregamento: statusAvant
    };
    mostrarBalaoFavoritosStatus(
      pedeConta
        ? `Avant Pro nao retornou dados coletaveis para "${termo}". Confirme manualmente o login no navegador interno.`
        : `Avant Pro carregou antes dos cards de "${termo}". Aguardando dados do Avant Pro...`,
      { larga: true, titulo: pedeConta ? 'Avant Pro sem dados' : 'Aguardando Avant Pro' }
    );
    if (pedeConta && !segundoPlano) {
      await fecharModalBloqueanteAvantProNoWebview().catch(() => null);
      await aguardarPrimeirosDadosAvantOuCardsWebview({
        timeoutMs: 1400,
        idleMs: 200,
        acaoUsuario: leituraPaginaAutorizada
      }).catch(() => null);
    }
  }

  async function obterStatusAvantBuscaFavoritos(contexto) {
    const { termo, opcoes, segundoPlano, leituraPaginaAutorizada } = contexto;
    contexto.avantStatus = contexto.avantStatusPreColeta || null;
    contexto.statusFinal = null;
    contexto.reloadedAfterAvantLogin = false;
    if (opcoes.exigirAvantPro === true && typeof garantirAvantProProntoParaFavoritos === 'function') {
      contexto.avantStatus = await garantirAvantProProntoParaFavoritos({
        termo,
        timeoutMs: contexto.pronto && contexto.pronto.aguardandoAvantMesmoSemCards
          ? (segundoPlano ? 95000 : 65000)
          : (segundoPlano ? 14000 : 9500),
        pollMs: segundoPlano ? 650 : 350,
        acaoUsuario: leituraPaginaAutorizada
      });
      contexto.statusFinal = contexto.avantStatus;
      contexto.reloadedAfterAvantLogin = !!(contexto.avantStatus && contexto.avantStatus.reloadedAfterAvantLogin);
    } else if (!contexto.avantStatus) {
      contexto.avantStatus = await diagnosticarAvantProNoWebview().catch(() => null);
      contexto.statusFinal = contexto.avantStatus;
    }
    if (!statusPedeLoginAvantFavoritos(contexto.avantStatus)) return;
    if (opcoes.exigirAvantPro === true) {
      if (typeof mostrarAcaoConectarAvantPro === 'function') mostrarAcaoConectarAvantPro(contexto.avantStatus);
      contexto.statusFinal = await aguardarConexaoAvantProFavoritos(contexto.avantStatus, { termo });
      contexto.reloadedAfterAvantLogin = contexto.reloadedAfterAvantLogin
        || !!(contexto.statusFinal && contexto.statusFinal.reloadedAfterAvantLogin);
      contexto.avantStatus = contexto.statusFinal || contexto.avantStatus;
    } else {
      mostrarBalaoFavoritosStatus(
        segundoPlano
          ? 'Avant Pro nao retornou dados coletaveis. Coletando os anuncios visiveis do Mercado Livre em background.'
          : 'Avant Pro nao retornou dados coletaveis. Confirme manualmente o login no navegador interno.',
        { larga: true, titulo: 'Avant Pro sem dados' }
      );
      const fechamento = await fecharModalBloqueanteAvantProNoWebview().catch(() => null);
      const resultadoLogin = segundoPlano
        ? fechamento
        : ((fechamento && fechamento.closed) ? fechamento : await abrirLoginAvantProNoWebview().catch(() => null));
      await aguardarPrimeirosDadosAvantOuCardsWebview({
        timeoutMs: resultadoLogin && resultadoLogin.success ? 1200 : 700,
        idleMs: 180,
        acaoUsuario: leituraPaginaAutorizada
      }).catch(() => null);
      contexto.avantStatus = await diagnosticarAvantProNoWebview().catch(() => contexto.avantStatus);
    }
    if (!segundoPlano && statusPedeLoginAvantFavoritos(contexto.avantStatus)) {
      contexto.avantStatus = await aguardarConexaoAvantProFavoritos(contexto.avantStatus, { termo });
      contexto.statusFinal = contexto.avantStatus;
      contexto.reloadedAfterAvantLogin = contexto.reloadedAfterAvantLogin
        || !!(contexto.statusFinal && contexto.statusFinal.reloadedAfterAvantLogin);
      await aguardarPrimeirosDadosAvantOuCardsWebview({
        timeoutMs: segundoPlano ? 4500 : 1800,
        idleMs: segundoPlano ? 320 : 180,
        acaoUsuario: leituraPaginaAutorizada
      }).catch(() => null);
      contexto.avantStatus = await diagnosticarAvantProNoWebview().catch(() => contexto.avantStatus);
    }
    if (opcoes.exigirAvantPro === true && statusPedeLoginAvantFavoritos(contexto.avantStatus)) {
      if (typeof mostrarAcaoConectarAvantPro === 'function') mostrarAcaoConectarAvantPro(contexto.avantStatus);
      throw erroLoginAvantProFavoritos(`Avant Pro nao retornou dados coletaveis para "${termo}". Confirme manualmente o login no navegador interno e tente Fazer favoritos novamente.`);
    }
  }

  async function aguardarLiberacaoAvantFavoritos(contexto) {
    const { termo, opcoes, segundoPlano } = contexto;
    const ignorarLogin = () => !!(segundoPlano && contexto.avantStatus && (
      contexto.avantStatus.needsAccountLink
      || contexto.avantStatus.accountActionRequired
      || contexto.avantStatus.avantLoginDialog
      || contexto.avantStatus.avantLoginEmailInputs > 0
    ));
    contexto.ignorarLoginAvantBackground = ignorarLogin();
    contexto.resultadosMlVisiveis = !!(contexto.pronto && contexto.pronto.hasCards);
    const permitirReload = !mlFavoritosEmExecucao && opcoes.recarregarAvantSeAusente !== false;
    if (statusAvantProSemDadosColetaveis(contexto.avantStatus) && !contexto.resultadosMlVisiveis) {
      mostrarBalaoFavoritosStatus(
        permitirReload
          ? `Avant Pro ainda nao liberou dados para "${termo}". Aguardando e recarregando se necessario...`
          : `Avant Pro ainda nao liberou dados para "${termo}". Vou continuar sem recarregar a pagina.`,
        { larga: true, titulo: 'Aguardando Avant Pro' }
      );
      const statusAguardado = await aguardarAvantProNoWebview({
        timeoutMs: permitirReload ? (segundoPlano ? 95000 : 18000) : (segundoPlano ? 22000 : 6500),
        pollMs: segundoPlano ? 750 : 420,
        recarregarSeAusente: permitirReload,
        timeoutAposReloadMs: segundoPlano ? 45000 : 14000,
        mensagemRecarregando: `Avant Pro ainda nao liberou dados para "${termo}". Recarregando Mercado Livre...`
      }).catch(() => null);
      if (statusAguardado) contexto.avantStatus = statusAguardado;
      verificarCancelamentoFavoritos();
      if (!segundoPlano && statusPedeLoginAvantFavoritos(contexto.avantStatus)) {
        contexto.avantStatus = await aguardarConexaoAvantProFavoritos(contexto.avantStatus, { termo });
        await aguardarPrimeirosDadosAvantOuCardsWebview({
          timeoutMs: segundoPlano ? 4500 : 1800,
          idleMs: segundoPlano ? 320 : 180
        }).catch(() => null);
        contexto.avantStatus = await diagnosticarAvantProNoWebview().catch(() => contexto.avantStatus);
      }
    }
    contexto.ignorarLoginAvantBackground = ignorarLogin();
    contexto.avantProntoParaColeta = typeof statusAvantProTemDadosColetaveis === 'function'
      ? statusAvantProTemDadosColetaveis(contexto.avantStatus)
      : !!(contexto.avantStatus && (
        contexto.avantStatus.hasAvantData
        || contexto.avantStatus.rows > 0
        || contexto.avantStatus.dataTextNodes > 0
        || contexto.avantStatus.bodyDataLabels > 1
      ));
    if (contexto.avantProntoParaColeta) {
      mostrarBalaoFavoritosStatus(`Avant Pro detectado. Coletando anuncios visiveis de "${termo}"...`);
    } else if (contexto.pronto && contexto.pronto.hasCards) {
      mostrarBalaoFavoritosStatus(`${contexto.pronto.cardCount || 0} anuncio(s) encontrados. Coletando dados visiveis...`);
    } else {
      mostrarBalaoFavoritosStatus(`Coletando resultados visiveis de "${termo}"...`);
    }
  }

  function opcoesExtracaoBuscaFavoritos(contexto, extras = {}) {
    const base = {
      clicarAvant: false,
      ignorarLoginAvant: contexto.ignorarLoginAvantBackground,
      cliqueAvantForcadoDesativado: true,
      clicarCardsSemDados: false,
      permitirFerramentasAvant: false,
      permitirAutoLoginAvant: false,
      fastLinks: false,
      timeoutMs: contexto.segundoPlano ? 10000 : 6200,
      aguardarAposCliqueAvant: 500,
      aguardarEstabilidadeAvant: {
        minWaitMs: contexto.segundoPlano ? 380 : 180,
        stableMs: contexto.segundoPlano ? 520 : 320,
        maxWaitMs: contexto.segundoPlano ? 2400 : 1200
      }
    };
    return { ...base, ...extras };
  }

  async function extrairResultadoInicialBuscaFavoritos(contexto) {
    verificarCancelamentoFavoritos();
    contexto.maxAnunciosColeta = Math.max(
      20,
      Math.min(Number(contexto.opcoes.maxAnuncios) || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80, 80)
    );
    contexto.maxCliquesAvantColeta = Math.min(
      100,
      Number(contexto.opcoes.maxCliquesAvant) || contexto.maxAnunciosColeta
    );
    contexto.resultado = await extrairAnunciosWebviewVisivel(opcoesExtracaoBuscaFavoritos(contexto, {
      maxCliquesAvant: contexto.maxCliquesAvantColeta,
      timeoutMs: contexto.segundoPlano ? 9500 : 5500,
      aguardarAposCliqueAvant: contexto.avantProntoParaColeta ? 0 : 300,
      aguardarEstabilidadeAvant: contexto.avantProntoParaColeta
        ? { minWaitMs: 0, stableMs: contexto.segundoPlano ? 420 : 240, maxWaitMs: contexto.segundoPlano ? 1400 : 700 }
        : { minWaitMs: contexto.segundoPlano ? 350 : 160, stableMs: contexto.segundoPlano ? 520 : 320, maxWaitMs: contexto.segundoPlano ? 2200 : 1100 }
    })).catch(() => null);
    verificarCancelamentoFavoritos();
  }

  async function resolverLoginResultadoBuscaFavoritos(contexto) {
    if (!(contexto.resultado && !contexto.segundoPlano && statusPedeLoginAvantFavoritos(contexto.resultado))) return;
    if (contexto.opcoes.exigirAvantPro === true) {
      if (typeof mostrarAcaoConectarAvantPro === 'function') mostrarAcaoConectarAvantPro(contexto.resultado);
      contexto.statusFinal = await aguardarConexaoAvantProFavoritos(contexto.resultado, { termo: contexto.termo });
      contexto.reloadedAfterAvantLogin = contexto.reloadedAfterAvantLogin
        || !!(contexto.statusFinal && contexto.statusFinal.reloadedAfterAvantLogin);
      contexto.resultado = await extrairAnunciosWebviewVisivel(
        opcoesExtracaoBuscaFavoritos(contexto, { ignorarLoginAvant: false })
      ).catch(() => contexto.resultado);
    } else {
      mostrarBalaoFavoritosStatus('Avant Pro nao retornou dados coletaveis. Vou seguir apenas com os dados visiveis do Mercado Livre.', {
        larga: true,
        titulo: 'Avant Pro sem dados'
      });
      await fecharModalBloqueanteAvantProNoWebview().catch(() => null);
      await aguardarPrimeirosDadosAvantOuCardsWebview({
        timeoutMs: 1100,
        idleMs: 180,
        acaoUsuario: contexto.leituraPaginaAutorizada
      }).catch(() => null);
      const retry = await extrairAnunciosWebviewVisivel(opcoesExtracaoBuscaFavoritos(contexto, {
        ignorarLoginAvant: contexto.segundoPlano
      })).catch(() => null);
      if (retry) contexto.resultado = retry;
    }
    verificarCancelamentoFavoritos();
    if (!contexto.segundoPlano && statusPedeLoginAvantFavoritos(contexto.resultado)) {
      contexto.statusFinal = await aguardarConexaoAvantProFavoritos(contexto.resultado, { termo: contexto.termo });
      contexto.reloadedAfterAvantLogin = contexto.reloadedAfterAvantLogin
        || !!(contexto.statusFinal && contexto.statusFinal.reloadedAfterAvantLogin);
      contexto.resultado = await extrairAnunciosWebviewVisivel(
        opcoesExtracaoBuscaFavoritos(contexto, { ignorarLoginAvant: false })
      ).catch(() => contexto.resultado);
    }
  }

  async function repetirAposRetomadaAvantFavoritos(contexto) {
    contexto.statusFinal = contexto.statusFinal || {};
    if (!(contexto.reloadedAfterAvantLogin || contexto.statusFinal.resumedAfterAvantLogin)) return;
    const retry = await extrairAnunciosWebviewVisivel(
      opcoesExtracaoBuscaFavoritos(contexto, {
        ignorarLoginAvant: false,
        aguardarAposCliqueAvant: 450
      })
    ).catch(() => null);
    if (retry) contexto.resultado = retry;
  }

  async function completarResultadoVazioBuscaFavoritos(contexto) {
    contexto.anunciosAvant = (contexto.resultado && contexto.resultado.anuncios) || [];
    if (contexto.resultado && contexto.resultado.needsLogin) throw erroLoginMercadoLivreFavoritos();
    if (contexto.resultado && contexto.resultado.noResults && !contexto.anunciosAvant.length) {
      throw erroSemResultadosFavoritos();
    }
    if (contexto.anunciosAvant.length) return;
    await aguardarPrimeirosDadosAvantOuCardsWebview({
      timeoutMs: contexto.segundoPlano ? 6500 : 1800,
      idleMs: contexto.segundoPlano ? 360 : 220,
      acaoUsuario: contexto.leituraPaginaAutorizada
    }).catch(() => null);
    verificarCancelamentoFavoritos();
    const retry = await extrairAnunciosWebviewVisivel(opcoesExtracaoBuscaFavoritos(contexto, {
      ignorarLoginAvant: contexto.segundoPlano,
      timeoutMs: contexto.segundoPlano ? 9500 : 5200,
      aguardarAposCliqueAvant: 350,
      aguardarEstabilidadeAvant: {
        minWaitMs: contexto.segundoPlano ? 380 : 180,
        stableMs: contexto.segundoPlano ? 520 : 320,
        maxWaitMs: contexto.segundoPlano ? 2400 : 1000
      }
    })).catch(() => null);
    verificarCancelamentoFavoritos();
    contexto.anunciosAvant = mesclarAnunciosAvant(contexto.anunciosAvant, (retry && retry.anuncios) || []);
    if (retry && statusPedeLoginAvantFavoritos(retry)) {
      if (contexto.opcoes.exigirAvantPro === true) {
        if (typeof mostrarAcaoConectarAvantPro === 'function') mostrarAcaoConectarAvantPro(retry);
        throw erroLoginAvantProFavoritos(`Avant Pro nao retornou dados coletaveis para "${contexto.termo}". Confirme manualmente o login no navegador interno e tente Fazer favoritos novamente.`);
      }
      if (!contexto.segundoPlano) {
        await aguardarConexaoAvantProFavoritos(retry, { termo: contexto.termo });
      }
      const retryDepoisAvant = await extrairAnunciosWebviewVisivel(opcoesExtracaoBuscaFavoritos(contexto, {
        ignorarLoginAvant: contexto.segundoPlano
      })).catch(() => null);
      contexto.anunciosAvant = mesclarAnunciosAvant(
        contexto.anunciosAvant,
        (retryDepoisAvant && retryDepoisAvant.anuncios) || []
      );
    }
    if (retry && retry.noResults && !contexto.anunciosAvant.length) throw erroSemResultadosFavoritos();
    if (!contexto.anunciosAvant.length
        && contexto.opcoes.exigirAvantPro !== true
        && typeof extrairCardsMercadoLivreBasicoWebview === 'function') {
      mostrarBalaoFavoritosStatus(`Lendo cards visiveis do Mercado Livre para "${contexto.termo}"...`, { larga: true });
      const basico = await extrairCardsMercadoLivreBasicoWebview({
        limite: Math.max(60, Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80)
      }).catch(() => null);
      contexto.anunciosAvant = mesclarAnunciosAvant(
        contexto.anunciosAvant,
        (basico && basico.anuncios) || []
      );
      verificarCancelamentoFavoritos();
    }
  }

  async function coletarRolagemBuscaFavoritos(contexto) {
    if (contexto.opcoes.exigirAvantPro === true
        && typeof diagnosticarAvantProNoWebview === 'function') {
      const statusAntesRolagem = await diagnosticarAvantProNoWebview().catch(() => null);
      if (statusPedeLoginAvantFavoritos(statusAntesRolagem)) {
        if (typeof mostrarAcaoConectarAvantPro === 'function') mostrarAcaoConectarAvantPro(statusAntesRolagem);
        throw erroLoginAvantProFavoritos(`Avant Pro nao retornou dados coletaveis para "${contexto.termo}". Confirme manualmente o login no navegador interno e tente Fazer favoritos novamente.`);
      }
    }
    const maxPosicoes = Number(contexto.opcoes.maxPosicoesRolagem)
      || (contexto.segundoPlano ? 28 : 24);
    if (typeof coletarDadosAvantComRolagem !== 'function'
        || !(contexto.anunciosAvant.length || contexto.resultadosMlVisiveis)
        || contexto.anunciosAvant.length >= contexto.maxAnunciosColeta) return;
    mostrarBalaoFavoritosStatus(
      contexto.anunciosAvant.length
        ? `${contexto.anunciosAvant.length} anuncio(s) encontrados. Percorrendo a primeira pagina para completar a coleta...`
        : `Percorrendo a primeira pagina de "${contexto.termo}" para coletar os anuncios visiveis...`,
      { larga: true }
    );
    const anunciosRolagem = await coletarDadosAvantComRolagem({
      maxAnuncios: contexto.maxAnunciosColeta,
      maxPosicoes,
      clicarCardsSemDados: false,
      permitirFerramentasAvant: false,
      permitirAutoLoginAvant: false,
      permitirBasico: contexto.opcoes.exigirAvantPro !== true
    }).catch(err => {
      if (erroEhLoginAvantProFavoritos(err) || (err && err.loginAvantProNecessario)) throw err;
      return [];
    });
    contexto.anunciosAvant = mesclarAnunciosAvant(contexto.anunciosAvant, anunciosRolagem);
    verificarCancelamentoFavoritos();
  }

  async function validarResultadoBuscaAvantFavoritos(contexto) {
    if (contexto.anunciosAvant.length || contexto.opcoes.exigirAvantPro !== true) return;
    const diagnostico = typeof diagnosticarAvantProNoWebview === 'function'
      ? await diagnosticarAvantProNoWebview().catch(() => null)
      : null;
    const cardCount = Number((diagnostico && diagnostico.cardCount) || 0);
    const rows = Number((diagnostico && diagnostico.rows) || 0);
    const widgets = Number((diagnostico && diagnostico.widgets) || 0);
    const labels = Number((diagnostico && (diagnostico.bodyDataLabels || diagnostico.avantLabels)) || 0);
    throw erroColetaMercadoLivreFavoritos(
      `Nao consegui transformar os resultados visiveis em anuncios para "${contexto.termo}". Diagnostico: cards=${cardCount}, linhasAvant=${rows}, widgetsAvant=${widgets}, rotulosAvant=${labels}.`
    );
  }

  function finalizarBuscaAvantFavoritos(contexto) {
    validarAnunciosFavoritosPertencemAoTermo(contexto.termo, contexto.anunciosAvant);
    contexto.anunciosAvant = aplicarCacheAvantAosAnuncios(contexto.anunciosAvant, {
      termo: contexto.termo,
      sku: contexto.opcoes.sku || contexto.termo
    });
    salvarCacheAvantDosAnuncios(contexto.anunciosAvant, {
      termo: contexto.termo,
      sku: contexto.opcoes.sku || contexto.termo
    });
    const totalComDadosAvant = contexto.anunciosAvant.filter(item => {
      const fonte = normalizarFonte(item && (item.vendasFonte || item.vendas_fonte || ''));
      return fonteVendasConfiavel(fonte)
        || !!(item && (item.vendedor || item.data_criacao || item.cacheAvant));
    }).length;
    if (contexto.anunciosAvant.length) {
      mostrarBalaoFavoritosStatus(`${contexto.anunciosAvant.length} anuncio(s) encontrados. ${totalComDadosAvant} com dados Avant. Coletando mais resultados em segundo plano...`);
    }
    return contexto.anunciosAvant.map(item => {
      const fonteVendas = normalizarFonte(item && (item.vendasFonte || item.vendas_fonte || ''));
      const vendas = fonteVendasConfiavel(fonteVendas) ? parseNumeroVendas(item && item.vendas) : null;
      return {
        ...item,
        origem_dados: 'avantpro',
        vendas,
        vendasFonte: vendas !== null ? fonteVendas : '',
        vendas_fonte: vendas !== null ? fonteVendas : ''
      };
    });
  }

  async function buscarAnunciosFavoritosPorTermoAvant(termo, opcoes = {}) {
    if (!hasInternalBrowserApi && !usarNavegadorMlNoShellElectron()) return [];
    const contexto = { termo, opcoes };
    verificarCancelamentoFavoritos();
    await prepararAvantAntesDaBuscaFavoritos(contexto);
    await abrirPesquisaMercadoLivreFavoritos(contexto);
    await aguardarEstadoInicialPesquisaFavoritos(contexto);
    await resolverCarregamentoMercadoLivreFavoritos(contexto);
    await obterStatusAvantBuscaFavoritos(contexto);
    await aguardarLiberacaoAvantFavoritos(contexto);
    await extrairResultadoInicialBuscaFavoritos(contexto);
    await resolverLoginResultadoBuscaFavoritos(contexto);
    await repetirAposRetomadaAvantFavoritos(contexto);
    await completarResultadoVazioBuscaFavoritos(contexto);
    await coletarRolagemBuscaFavoritos(contexto);
    await validarResultadoBuscaAvantFavoritos(contexto);
    return finalizarBuscaAvantFavoritos(contexto);
  }

  Object.assign(internal, {
    buscarAnunciosFavoritosPorTermoAvant
  });
  internal.components.add('08-legacy-search');
})(window);
