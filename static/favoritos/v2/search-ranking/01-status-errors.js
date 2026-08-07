(function (global) {
  'use strict';

  const searchRanking = global.FavoritosV2.searchRanking;
  const internal = searchRanking.internal;
  if (internal.components.has('01-status-errors')) return;


  function mostrarBalaoFavoritosStatus(mensagem, opcoes = {}) {
      return window.FavoritosV2?.ui?.statusModal?.mostrarBalaoFavoritosStatus?.(mensagem, opcoes);
  }

  function esconderBalaoFavoritosStatus(opcoes = {}) {
      return window.FavoritosV2?.ui?.statusModal?.esconderBalaoFavoritosStatus?.(opcoes);
  }

  function resolverAcaoBalaoFavoritos(resolve, valor, botao, opcoes = {}) {
      if (typeof window.favoritosResolverAcaoBalao === 'function') {
          window.favoritosResolverAcaoBalao(resolve, valor, botao, opcoes);
          return;
      }
      if (opcoes.esconder) esconderBalaoFavoritosStatus();
      setTimeout(() => resolve(valor), 0);
  }

  function erroLoginMercadoLivreFavoritos(mensagem) {
      const erro = new Error(mensagem || 'O Mercado Livre pediu login/verificacao no navegador interno. Abra o navegador interno, conclua o acesso e tente Fazer favoritos novamente.');
      erro.loginMercadoLivreNecessario = true;
      return erro;
  }

  function erroEhLoginMercadoLivreFavoritos(err) {
      if (err && err.loginMercadoLivreNecessario) return true;
      const texto = String(err && err.message || err || '').toLowerCase();
      return texto.indexOf('mercado livre pediu login') >= 0
          || texto.indexOf('pediu login/verificacao') >= 0
          || texto.indexOf('account-verification') >= 0
          || texto.indexOf('acesse sua conta') >= 0;
  }

  function erroLoginAvantProFavoritos(mensagem) {
      const erro = new Error(mensagem || 'Avant Pro nao retornou dados coletaveis. Confirme manualmente no navegador interno se o Avant Pro esta pronto e tente Fazer favoritos novamente.');
      erro.loginAvantProNecessario = true;
      return erro;
  }

  function erroEhLoginAvantProFavoritos(err) {
      if (err && err.loginAvantProNecessario) return true;
      const texto = String(err && err.message || err || '').toLowerCase();
      return (texto.indexOf('avant pro') >= 0 || texto.indexOf('avantpro') >= 0)
          && (texto.indexOf('login') >= 0
              || texto.indexOf('vincul') >= 0
              || texto.indexOf('conexao') >= 0
              || texto.indexOf('conect') >= 0
              || texto.indexOf('account') >= 0);
  }

  function erroColetaMercadoLivreFavoritos(mensagem) {
      const erro = new Error(mensagem || 'Mercado Livre/Avant Pro nao retornou dados coletaveis. Abra o navegador interno, confira se a pagina carregou e tente Fazer favoritos novamente.');
      erro.coletaMercadoLivreFalhou = true;
      return erro;
  }

  function erroEhColetaMercadoLivreFavoritos(err) {
      if (err && err.coletaMercadoLivreFalhou) return true;
      const texto = String(err && err.message || err || '').toLowerCase();
      return texto.indexOf('mercado livre/avant pro nao retornou dados') >= 0
          || texto.indexOf('nao retornou dados coletaveis') >= 0;
  }

  function statusAvantProSemDadosColetaveis(status) {
      if (!status || status.needsAccountLink || status.accountActionRequired) return false;
      if (status.loadingScreen && Number(status.cardCount || 0) <= 0) return false;
      if (typeof statusAvantProTemDadosColetaveis === 'function' && statusAvantProTemDadosColetaveis(status)) return false;
      if (status.rows > 0 || status.dataTextNodes > 0 || status.bodyDataLabels > 1 || status.hasAvantData) return false;
      return !!(status.shellOnly || status.extensionDetected || status.widgets > 0 || status.actionButtons > 0 || status.toolsButtons > 0);
  }

  function statusAvantProLoginConcluidoSemDados(status) {
      if (!status) return false;
      const reloadAposLoginAvantRecomendado = !!status.reloadAposLoginAvantRecomendado;
      if (reloadAposLoginAvantRecomendado) return true;
      if (status.needsAccountLink && !status.loginAvantClicadoRecentemente) return false;
      return !!(
          status.avantShellProntoParaColeta
          || status.resumedAfterAvantLogin
          || status.extensionDetected
          || Number(status.infoButtons || 0) > 0
          || Number(status.toolsButtons || 0) > 0
          || Number(status.widgets || 0) > 0
          || status.bodyHasAvantInfo
      );
  }

  async function recarregarAposLoginAvantProFavoritosSePossivel(status = {}, opcoes = {}) {
      if (typeof recarregarNavegadorMlAposLoginAvantProFavoritos !== 'function') return status;
      return await recarregarNavegadorMlAposLoginAvantProFavoritos(status, opcoes).catch(() => status);
  }

  function statusAvantProPodeRetomarColeta(status) {
      if (!status) return false;
      if (typeof statusAvantProTemDadosColetaveis === 'function' && statusAvantProTemDadosColetaveis(status)) return true;
      if (typeof statusAvantProPedeLoginOuVinculo === 'function' && statusAvantProPedeLoginOuVinculo(status)) return false;
      const accountActionRequired = !!status.accountActionRequired;
      const infoButtons = Number(status.infoButtons || 0);
      const bodyHasAvantInfo = !!status.bodyHasAvantInfo;
      if (accountActionRequired && !statusAvantProLoginConcluidoSemDados(status)) return false;
      return !!(
          statusAvantProLoginConcluidoSemDados(status)
          || infoButtons > 0
          || bodyHasAvantInfo
          || status.avantShellProntoParaColeta
      );
  }

  Object.assign(internal, {
    mostrarBalaoFavoritosStatus,
    esconderBalaoFavoritosStatus,
    resolverAcaoBalaoFavoritos,
    erroLoginMercadoLivreFavoritos,
    erroEhLoginMercadoLivreFavoritos,
    erroLoginAvantProFavoritos,
    erroEhLoginAvantProFavoritos,
    erroColetaMercadoLivreFavoritos,
    erroEhColetaMercadoLivreFavoritos,
    statusAvantProSemDadosColetaveis,
    statusAvantProLoginConcluidoSemDados,
    recarregarAposLoginAvantProFavoritosSePossivel,
    statusAvantProPodeRetomarColeta
  });
  internal.components.add('01-status-errors');
})(window);
