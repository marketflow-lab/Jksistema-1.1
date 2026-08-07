(function (global) {
  'use strict';

  const searchRanking = global.FavoritosV2.searchRanking;
  const internal = searchRanking.internal;
  if (internal.components.has('04-avant-login-prompt')) return;

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

  function esconderBalaoFavoritosStatus(...args) {
    return dependency('esconderBalaoFavoritosStatus')(...args);
  }

  function obterTermoInicialLoginAvantProFavoritos(...args) {
    return dependency('obterTermoInicialLoginAvantProFavoritos')(...args);
  }

  function limparBotaoContinuarLoginAvantProFavoritos(...args) {
    return dependency('limparBotaoContinuarLoginAvantProFavoritos')(...args);
  }

  function obterEstadoAutenticacaoMercadoLivreFavoritos(...args) {
    return dependency('obterEstadoAutenticacaoMercadoLivreFavoritos')(...args);
  }

  function validarLoginMercadoLivreAntesDeContinuarFavoritos(...args) {
    return dependency('validarLoginMercadoLivreAntesDeContinuarFavoritos')(...args);
  }

  function validarLoginAvantProAntesDeContinuarFavoritos(...args) {
    return dependency('validarLoginAvantProAntesDeContinuarFavoritos')(...args);
  }

  function mostrarBotaoContinuarLoginAvantProFavoritos(...args) {
    return dependency('mostrarBotaoContinuarLoginAvantProFavoritos')(...args);
  }

  function registrarConfirmacaoUsuarioLoginAvantProBestEffortFavoritos(...args) {
    return dependency('registrarConfirmacaoUsuarioLoginAvantProBestEffortFavoritos')(...args);
  }

  function cicloLoginContinuaAtivoFavoritos(contexto, ciclo) {
    return !contexto.concluido
      && contexto.navegadorLoginAberto
      && ciclo === contexto.cicloNavegadorLogin
      && typeof balaoResultadosMlAberto === 'function'
      && balaoResultadosMlAberto();
  }

  function finalizarPerguntaLoginAvantFavoritos(contexto, valor) {
    if (contexto.concluido) return;
    contexto.concluido = true;
    contexto.cicloNavegadorLogin += 1;
    const deveFecharNavegador = contexto.navegadorLoginAberto;
    contexto.navegadorLoginAberto = false;
    if (window.__JK_FAVORITOS_LOGIN_CLOSE_HANDLER__ === contexto.fecharLoginPeloModal) {
      delete window.__JK_FAVORITOS_LOGIN_CLOSE_HANDLER__;
    }
    contexto.fecharLoginPeloModal = null;
    mlFavoritosPerguntaResolver = null;
    limparBotaoContinuarLoginAvantProFavoritos();
    esconderBalaoFavoritosStatus({ restaurarNavegador: false });
    if (deveFecharNavegador && typeof fecharBalaoResultadosMl === 'function') {
      fecharBalaoResultadosMl({
        forcar: true,
        descarregarConteudo: true,
        preserveAvantProSession: true,
        reason: valor === true ? 'favoritos-login-confirmado' : 'favoritos-login-cancelado'
      });
    }
    contexto.resolve(valor);
  }

  function agendarEnquantoLoginFavoritos(contexto, ciclo, callback, atrasoMs) {
    setTimeout(() => {
      if (cicloLoginContinuaAtivoFavoritos(contexto, ciclo)) callback();
    }, atrasoMs);
  }

  function criarBotaoLoginFavoritos(rotulo, handler) {
    const botao = document.createElement('button');
    botao.type = 'button';
    botao.textContent = rotulo;
    botao.addEventListener('click', handler);
    return botao;
  }

  function renderizarControlesLoginAbertoFavoritos(contexto, loginMercadoLivrePendente) {
    const validarEFinalizar = botao => validarLoginMercadoLivreAntesDeContinuarFavoritos(
      () => validarLoginAvantProAntesDeContinuarFavoritos(
        () => finalizarPerguntaLoginAvantFavoritos(contexto, true),
        botao
      ),
      botao
    );
    mostrarBotaoContinuarLoginAvantProFavoritos(validarEFinalizar);
    mlFavoritosBalloonActionsEl.innerHTML = '';
    const continuar = criarBotaoLoginFavoritos('Continuar favoritos', async () => {
      continuar.disabled = true;
      const concluiu = await validarEFinalizar(continuar).catch(() => false);
      if (!concluiu) continuar.disabled = false;
    });
    const abrirNovamente = criarBotaoLoginFavoritos('Abrir busca novamente', () => {
      abrirNovamente.disabled = true;
      abrirTelaLoginAvantFavoritos(contexto).finally(() => { abrirNovamente.disabled = false; });
    });
    const cancelar = criarBotaoLoginFavoritos('Cancelar favoritos', () => {
      esconderBalaoFavoritosStatus();
      finalizarPerguntaLoginAvantFavoritos(contexto, false);
    });
    mlFavoritosBalloonActionsEl.appendChild(continuar);
    mlFavoritosBalloonActionsEl.appendChild(abrirNovamente);
    mlFavoritosBalloonActionsEl.appendChild(cancelar);
    const mensagemLogin = loginMercadoLivrePendente
      ? 'Conclua o login ou verificacao do Mercado Livre. Aguarde a pagina da pesquisa voltar e clique em Continuar favoritos; o navegador sera fechado automaticamente.'
      : 'Faça o login do Avant Pro no navegador interno. Quando terminar, clique em Continuar favoritos; o navegador sera fechado automaticamente.';
    mostrarBalaoFavoritosStatus(mensagemLogin, {
      manterAcoes: true,
      manterNavegadorVisivel: true,
      larga: true,
      titulo: loginMercadoLivrePendente ? 'Login Mercado Livre' : 'Login Avant Pro'
    });
  }

  async function abrirTelaLoginAvantFavoritos(contexto) {
    if (contexto.concluido) return;
    const cicloAtual = ++contexto.cicloNavegadorLogin;
    contexto.navegadorLoginAberto = true;
    contexto.fecharLoginPeloModal = () => finalizarPerguntaLoginAvantFavoritos(contexto, false);
    window.__JK_FAVORITOS_LOGIN_CLOSE_HANDLER__ = contexto.fecharLoginPeloModal;
    if (typeof mudarAba === 'function') {
      try { mudarAba('navegador'); } catch (_err) {}
    }
    abrirBalaoResultadosMl({
      titulo: 'Login Avant Pro',
      subtitulo: '',
      mostrarFavoritos: true,
      browserCompleto: true,
      forcarExibicao: true
    });
    if (typeof abrirMercadoLivreNoPrograma === 'function') {
      const payload = {
        titulo: 'Login Avant Pro',
        subtitulo: '',
        mostrarFavoritos: true,
        browserCompleto: true,
        forcarExibicao: true,
        ...(contexto.termo ? { termoPesquisa: contexto.termo, aguardarPesquisaMs: 900 } : {})
      };
      await abrirMercadoLivreNoPrograma(payload).catch(() => null);
    }
    if (!cicloLoginContinuaAtivoFavoritos(contexto, cicloAtual)) return;
    if (typeof forcarNavegadorMlShellVisivel === 'function') {
      agendarEnquantoLoginFavoritos(contexto, cicloAtual, () => forcarNavegadorMlShellVisivel(), 80);
      agendarEnquantoLoginFavoritos(contexto, cicloAtual, () => forcarNavegadorMlShellVisivel(), 450);
    }
    if (typeof agendarAtualizacaoPosicaoNavegadorMlShell === 'function') {
      agendarEnquantoLoginFavoritos(
        contexto,
        cicloAtual,
        () => agendarAtualizacaoPosicaoNavegadorMlShell(),
        120
      );
    }
    const estadoLoginMl = await obterEstadoAutenticacaoMercadoLivreFavoritos()
      .catch(() => ({ pendente: false }));
    if (!cicloLoginContinuaAtivoFavoritos(contexto, cicloAtual)) return;
    const loginMercadoLivrePendente = !!(estadoLoginMl && estadoLoginMl.pendente);
    if (loginMercadoLivrePendente) {
      abrirBalaoResultadosMl({
        titulo: 'Login Mercado Livre',
        subtitulo: 'Conclua todas as etapas de acesso antes de continuar.',
        mostrarFavoritos: true,
        browserCompleto: true,
        forcarExibicao: true
      });
    }
    renderizarControlesLoginAbertoFavoritos(contexto, loginMercadoLivrePendente);
  }

  function renderizarPerguntaLoginAvantFavoritos(contexto) {
    mlFavoritosPerguntaResolver = valor => finalizarPerguntaLoginAvantFavoritos(contexto, valor);
    const sim = criarBotaoLoginFavoritos('Sim, continuar', () => {
      sim.disabled = true;
      registrarConfirmacaoUsuarioLoginAvantProBestEffortFavoritos('prompt_usuario_confirmou');
      finalizarPerguntaLoginAvantFavoritos(contexto, true);
    });
    const nao = criarBotaoLoginFavoritos('Nao, abrir login', () => {
      nao.disabled = true;
      abrirTelaLoginAvantFavoritos(contexto).finally(() => { nao.disabled = false; });
    });
    const cancelar = criarBotaoLoginFavoritos('Cancelar', () => {
      esconderBalaoFavoritosStatus();
      finalizarPerguntaLoginAvantFavoritos(contexto, false);
    });
    mlFavoritosBalloonActionsEl.appendChild(sim);
    mlFavoritosBalloonActionsEl.appendChild(nao);
    mlFavoritosBalloonActionsEl.appendChild(cancelar);
    mostrarBalaoFavoritosStatus('Antes de fazer favoritos, o Avant Pro ja esta logado?', {
      manterAcoes: true,
      manterNavegadorVisivel: false,
      larga: true,
      titulo: 'Confirmar Avant Pro'
    });
  }

  async function perguntarLoginAvantProAntesFavoritos(opcoes = {}) {
    if (opcoes.avantLoginConfirmadoPeloUsuario === true || opcoes.pularPerguntaAvantLogin === true) {
      return true;
    }
    const termo = String(
      opcoes.termo || obterTermoInicialLoginAvantProFavoritos(opcoes.selecionados, opcoes.quantidade)
    ).trim();
    if (!mlFavoritosBalloonEl || !mlFavoritosBalloonActionsEl) {
      return window.confirm('Antes de fazer favoritos, o Avant Pro ja esta logado?');
    }
    if (mlFavoritosPerguntaResolver) mlFavoritosPerguntaResolver(null);
    mlFavoritosPerguntaResolver = null;
    mlFavoritosBalloonActionsEl.innerHTML = '';
    return new Promise(resolve => {
      renderizarPerguntaLoginAvantFavoritos({
        termo,
        resolve,
        concluido: false,
        cicloNavegadorLogin: 0,
        navegadorLoginAberto: false,
        fecharLoginPeloModal: null
      });
    });
  }

  Object.assign(internal, {
    perguntarLoginAvantProAntesFavoritos
  });
  internal.components.add('04-avant-login-prompt');
})(window);
