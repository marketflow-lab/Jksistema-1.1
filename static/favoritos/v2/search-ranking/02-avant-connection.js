(function (global) {
  'use strict';

  const searchRanking = global.FavoritosV2.searchRanking;
  const internal = searchRanking.internal;
  if (internal.components.has('02-avant-connection')) return;

  function mostrarBalaoFavoritosStatus(...args) {
    const implementation = internal.mostrarBalaoFavoritosStatus;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: mostrarBalaoFavoritosStatus');
    return implementation(...args);
  }

  function esconderBalaoFavoritosStatus(...args) {
    const implementation = internal.esconderBalaoFavoritosStatus;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: esconderBalaoFavoritosStatus');
    return implementation(...args);
  }

  function recarregarAposLoginAvantProFavoritosSePossivel(...args) {
    const implementation = internal.recarregarAposLoginAvantProFavoritosSePossivel;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: recarregarAposLoginAvantProFavoritosSePossivel');
    return implementation(...args);
  }

  function statusAvantProPodeRetomarColeta(...args) {
    const implementation = internal.statusAvantProPodeRetomarColeta;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: statusAvantProPodeRetomarColeta');
    return implementation(...args);
  }

  function verificarCancelamentoFavoritos(...args) {
    const implementation = internal.verificarCancelamentoFavoritos;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: verificarCancelamentoFavoritos');
    return implementation(...args);
  }

  function prepararTelaConexaoAvantProFavoritos(contexto) {
    mlFavoritosExecucaoEmSegundoPlano = false;
    if (typeof mudarAba === 'function') {
      try { mudarAba('navegador'); } catch (_err) {}
    }
    abrirBalaoResultadosMl({
      titulo: 'Conectar Avant Pro',
      subtitulo: contexto.termo
        ? `Conclua a conexao do Avant Pro para continuar: ${contexto.termo}`
        : 'Conclua a conexao do Avant Pro para continuar.',
      mostrarFavoritos: true,
      browserCompleto: true,
      forcarExibicao: true
    });
    if (typeof forcarNavegadorMlShellVisivel === 'function') {
      [80, 420, 950].forEach(delay => setTimeout(() => forcarNavegadorMlShellVisivel(), delay));
    }
    if (typeof agendarAtualizacaoPosicaoNavegadorMlShell === 'function') {
      setTimeout(() => agendarAtualizacaoPosicaoNavegadorMlShell(), 100);
    }
  }

  function podeRetomarConexaoAvantProFavoritos(contexto, statusAtual) {
    if (contexto.opcoes.exigirEntradaAvantAntesPesquisa !== true) return true;
    if (typeof statusAvantProPedeEntradaAntesPesquisa !== 'function') return true;
    return !statusAvantProPedeEntradaAntesPesquisa(statusAtual);
  }

  function finalizarConexaoAvantProFavoritos(contexto, valor, erro = null) {
    if (contexto.concluido) return;
    contexto.concluido = true;
    mlFavoritosExecucaoEmSegundoPlano = contexto.estadoSegundoPlanoAnterior;
    if (erro) contexto.reject(erro);
    else contexto.resolve(valor);
  }

  async function concluirRetomadaConexaoAvantProFavoritos(contexto, statusAtual = {}) {
    const retomada = await recarregarAposLoginAvantProFavoritosSePossivel(statusAtual, {
      termo: contexto.termo,
      acaoUsuario: true
    });
    esconderBalaoFavoritosStatus();
    finalizarConexaoAvantProFavoritos(contexto, retomada || statusAtual);
  }

  function cancelarConexaoAvantProFavoritos(contexto) {
    mlFavoritosCancelado = true;
    try {
      if (mlFavoritosAbortController) mlFavoritosAbortController.abort();
    } catch (_err) {}
    const erro = new Error('Favoritos cancelado pelo usuario.');
    erro.canceladoFavoritos = true;
    finalizarConexaoAvantProFavoritos(contexto, null, erro);
  }

  function statusConexaoAvantTemDadosFavoritos(status) {
    if (typeof statusAvantProTemDadosColetaveis === 'function') {
      return statusAvantProTemDadosColetaveis(status);
    }
    return !!(status && (
      status.hasAvantData
      || status.rows > 0
      || status.dataTextNodes > 0
      || status.bodyDataLabels > 1
    ));
  }

  async function verificarConexaoAvantProFavoritos(contexto) {
    if (contexto.concluido) return;
    verificarCancelamentoFavoritos();
    mostrarBalaoFavoritosStatus('Verificando se o Avant Pro ja liberou os dados...', {
      manterAcoes: true,
      manterNavegadorVisivel: true,
      larga: true,
      titulo: 'Conectar Avant Pro'
    });
    await acionarControlesAvantProNoWebview({
      acaoUsuario: true,
      forceClick: true,
      permitirFerramentas: true,
      maxClicks: 12
    }).catch(() => 0);
    const novoStatus = await aguardarAvantProNoWebview({
      recarregarSeAusente: false,
      autoLoginAvant: false,
      timeoutMs: 9000,
      pollMs: 350,
      acaoUsuario: true
    }).catch(() => null);
    if (statusAvantProPodeRetomarColeta(novoStatus)
        && podeRetomarConexaoAvantProFavoritos(contexto, novoStatus)) {
      await concluirRetomadaConexaoAvantProFavoritos(contexto, novoStatus);
      return;
    }
    if (statusConexaoAvantTemDadosFavoritos(novoStatus)) {
      esconderBalaoFavoritosStatus();
      finalizarConexaoAvantProFavoritos(contexto, novoStatus);
      return;
    }
    mostrarControlesConexaoAvantProFavoritos(contexto, novoStatus || contexto.statusInicial);
  }

  async function abrirLoginConexaoAvantProFavoritos(contexto) {
    if (contexto.concluido) return;
    const statusAntesLogin = await diagnosticarAvantProNoWebview().catch(() => null);
    if (statusAvantProPodeRetomarColeta(statusAntesLogin)
        && podeRetomarConexaoAvantProFavoritos(contexto, statusAntesLogin)) {
      await concluirRetomadaConexaoAvantProFavoritos(contexto, statusAntesLogin);
      return;
    }
    mostrarBalaoFavoritosStatus('Abrindo login do Avant Pro no navegador interno...', {
      manterAcoes: true,
      manterNavegadorVisivel: true,
      larga: true,
      titulo: 'Login Avant Pro'
    });
    const resultado = await abrirLoginAvantProNoWebview().catch(() => null);
    mostrarBalaoFavoritosStatus(
      resultado && resultado.success
        ? 'Conclua o login no navegador interno. Depois clique em Tentar novamente.'
        : 'Nao consegui acionar o login automaticamente. Use o navegador interno para entrar no Avant Pro e clique em Tentar novamente.',
      {
        manterAcoes: true,
        manterNavegadorVisivel: true,
        larga: true,
        erro: !(resultado && resultado.success),
        titulo: 'Login Avant Pro'
      }
    );
    mostrarControlesConexaoAvantProFavoritos(contexto, contexto.statusInicial);
  }

  function criarBotaoConexaoAvantProFavoritos(rotulo, handler) {
    const botao = document.createElement('button');
    botao.type = 'button';
    botao.textContent = rotulo;
    botao.addEventListener('click', () => {
      botao.disabled = true;
      Promise.resolve(handler()).finally(() => { botao.disabled = false; });
    });
    return botao;
  }

  function mostrarControlesConexaoAvantProFavoritos(contexto, statusAtual = {}) {
    if (contexto.concluido) return;
    if (!mlFavoritosBalloonActionsEl) {
      mostrarBalaoFavoritosStatus('Avant Pro esta aguardando login ou vinculacao. Conclua no navegador interno para continuar.', {
        erro: true,
        larga: true,
        titulo: 'Conectar Avant Pro'
      });
      return;
    }
    mlFavoritosBalloonActionsEl.innerHTML = '';
    const conectar = criarBotaoConexaoAvantProFavoritos(
      'Conectar Avant Pro',
      () => abrirLoginConexaoAvantProFavoritos(contexto)
    );
    const tentar = criarBotaoConexaoAvantProFavoritos(
      'Tentar novamente',
      () => verificarConexaoAvantProFavoritos(contexto)
    );
    const cancelar = document.createElement('button');
    cancelar.type = 'button';
    cancelar.textContent = 'Cancelar favoritos';
    cancelar.addEventListener('click', () => cancelarConexaoAvantProFavoritos(contexto));
    mlFavoritosBalloonActionsEl.appendChild(conectar);
    mlFavoritosBalloonActionsEl.appendChild(tentar);
    mlFavoritosBalloonActionsEl.appendChild(cancelar);
    const mensagem = statusAtual && statusAtual.message
      ? statusAtual.message
      : 'Avant Pro esta aguardando login ou vinculacao da conta.';
    mostrarBalaoFavoritosStatus(`${mensagem} Conclua no navegador interno e clique em Tentar novamente para continuar o Favoritos.`, {
      manterAcoes: true,
      manterNavegadorVisivel: true,
      erro: true,
      larga: true,
      titulo: 'Conectar Avant Pro'
    });
  }

  function aguardarConexaoAvantProFavoritos(status = {}, opcoes = {}) {
    const contexto = {
      statusInicial: status,
      opcoes,
      termo: String(opcoes.termo || '').trim(),
      estadoSegundoPlanoAnterior: mlFavoritosExecucaoEmSegundoPlano,
      concluido: false,
      resolve: null,
      reject: null
    };
    prepararTelaConexaoAvantProFavoritos(contexto);
    return new Promise((resolve, reject) => {
      contexto.resolve = resolve;
      contexto.reject = reject;
      mostrarControlesConexaoAvantProFavoritos(contexto, status);
    });
  }

  Object.assign(internal, {
    aguardarConexaoAvantProFavoritos
  });
  internal.components.add('02-avant-connection');
})(window);
