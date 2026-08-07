(function (global) {
  'use strict';

  const searchRanking = global.FavoritosV2.searchRanking;
  const internal = searchRanking.internal;
  if (internal.components.has('09-search-entrypoints')) return;

  function mostrarBalaoFavoritosStatus(...args) {
    const implementation = internal.mostrarBalaoFavoritosStatus;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: mostrarBalaoFavoritosStatus');
    return implementation(...args);
  }

  function erroEhLoginMercadoLivreFavoritos(...args) {
    const implementation = internal.erroEhLoginMercadoLivreFavoritos;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: erroEhLoginMercadoLivreFavoritos');
    return implementation(...args);
  }

  function erroLoginAvantProFavoritos(...args) {
    const implementation = internal.erroLoginAvantProFavoritos;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: erroLoginAvantProFavoritos');
    return implementation(...args);
  }

  function erroEhLoginAvantProFavoritos(...args) {
    const implementation = internal.erroEhLoginAvantProFavoritos;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: erroEhLoginAvantProFavoritos');
    return implementation(...args);
  }

  function erroColetaMercadoLivreFavoritos(...args) {
    const implementation = internal.erroColetaMercadoLivreFavoritos;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: erroColetaMercadoLivreFavoritos');
    return implementation(...args);
  }

  function erroEhColetaMercadoLivreFavoritos(...args) {
    const implementation = internal.erroEhColetaMercadoLivreFavoritos;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: erroEhColetaMercadoLivreFavoritos');
    return implementation(...args);
  }

  function aguardarConexaoAvantProFavoritos(...args) {
    const implementation = internal.aguardarConexaoAvantProFavoritos;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: aguardarConexaoAvantProFavoritos');
    return implementation(...args);
  }

  function verificarCancelamentoFavoritos(...args) {
    const implementation = internal.verificarCancelamentoFavoritos;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: verificarCancelamentoFavoritos');
    return implementation(...args);
  }

  function buscarAnunciosFavoritosPorTermoAvant(...args) {
    const implementation = internal.buscarAnunciosFavoritosPorTermoAvant;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: buscarAnunciosFavoritosPorTermoAvant');
    return implementation(...args);
  }

  async function buscarAnunciosFavoritosPorTermoFluxoControlado(termo, opcoes = {}) {
      const termoPesquisa = String(termo || '').trim();
      if (!termoPesquisa) return [];
      if (typeof coletarPrimeiraPaginaFavoritosControlada !== 'function') return null;
      if (typeof abrirMercadoLivreNoPrograma !== 'function') return null;
      const limite = Number(opcoes.maxAnuncios) || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80;
      const url = typeof construirUrlPesquisaMercadoLivre === 'function'
          ? construirUrlPesquisaMercadoLivre(termoPesquisa)
          : `https://lista.mercadolivre.com.br/${encodeURIComponent(termoPesquisa)}`;
      if (typeof mlUrlInput !== 'undefined' && mlUrlInput) {
          mlUrlInput.value = url;
      }
      mostrarBalaoFavoritosStatus(`Abrindo "${termoPesquisa}" e coletando a primeira pagina pelo fluxo novo...`, {
          manterNavegadorVisivel: true,
          larga: true,
          titulo: opcoes.titulo || 'Coleta da primeira pagina'
      });
      const abriu = await abrirMercadoLivreNoPrograma({
          termoPesquisa: termoPesquisa,
          titulo: opcoes.titulo || 'Fazendo Favorito! Aguarde...',
          subtitulo: opcoes.subtitulo || `Pesquisa: ${termoPesquisa}`,
          mostrarFavoritos: true,
          browserCompleto: true,
          forcarExibicao: true,
          apenasAbrirUrl: true,
          confirmarPesquisa: false,
          agendarPosicaoAntes: false,
          reposicionarDepois: false
      }).catch(() => false);
      if (!abriu) return null;
      await new Promise(resolve => setTimeout(resolve, Number(opcoes.aguardarPesquisaMs) || 1200));
      const resultado = await coletarPrimeiraPaginaFavoritosControlada({
          maxAnuncios: limite,
          tempoLimiteMs: Number(opcoes.tempoLimiteMs) || 180000,
          maxPassadas: Number(opcoes.maxPassadas) || 3,
          loteCliques: opcoes.loteCliques === undefined ? 6 : Number(opcoes.loteCliques),
          onProgress: (progresso) => {
              if (typeof window.FavoritosV2?.execution?.publicApi?.formatarProgressoColetaPrimeiraPaginaFavoritos === 'function') {
                  mostrarBalaoFavoritosStatus(window.FavoritosV2.execution.publicApi.formatarProgressoColetaPrimeiraPaginaFavoritos(opcoes.campo || 1, progresso), {
                      manterNavegadorVisivel: true,
                      larga: true,
                      titulo: 'Coleta da primeira pagina'
                  });
              }
          }
      }).catch((err) => {
          console.warn('Coleta controlada pelo wrapper legado falhou:', err);
          return null;
      });
      let anuncios = Array.isArray(resultado && resultado.anuncios) ? resultado.anuncios : [];
      if (!anuncios.length && typeof extrairCardsMercadoLivreBasicoWebview === 'function') {
          const basico = await extrairCardsMercadoLivreBasicoWebview({ limite }).catch(() => null);
          anuncios = Array.isArray(basico && basico.anuncios) ? basico.anuncios : [];
      }
      if (!anuncios.length && typeof extrairBaseMercadoLivreEmergencialWebview === 'function') {
          const emergencia = await extrairBaseMercadoLivreEmergencialWebview({
              limite,
              timeoutMs: 12000
          }).catch(() => null);
          anuncios = Array.isArray(emergencia && emergencia.anuncios) ? emergencia.anuncios : [];
      }
      if (typeof aplicarCacheAvantAosAnuncios === 'function') {
          anuncios = aplicarCacheAvantAosAnuncios(anuncios, {
              termo: termoPesquisa,
              sku: opcoes.sku || termoPesquisa
          });
      }
      if (typeof salvarCacheAvantDosAnuncios === 'function') {
          salvarCacheAvantDosAnuncios(anuncios, {
              termo: termoPesquisa,
              sku: opcoes.sku || termoPesquisa
          });
      }
      return anuncios.slice(0, limite).map(item => ({
          ...item,
          origem_dados: item && item.origem_dados || 'primeira_pagina_controlada_wrapper'
      }));
  }

  async function buscarAnunciosFavoritosPorTermo(termo, opcoes = {}) {
      const anunciosFluxoNovo = await buscarAnunciosFavoritosPorTermoFluxoControlado(termo, opcoes);
      if (Array.isArray(anunciosFluxoNovo)) return anunciosFluxoNovo;

      if (!(hasInternalBrowserApi || usarNavegadorMlNoShellElectron())) {
          throw erroColetaMercadoLivreFavoritos('Navegador interno indisponivel para coletar dados pelo Avant Pro.');
      }

      const tentarColetaAvant = async (extras = {}) => {
          mostrarBalaoFavoritosStatus(`Abrindo "${termo}" e lendo vendas pelo Avant Pro...`);
          const anunciosAvant = await buscarAnunciosFavoritosPorTermoAvant(termo, {
              ...opcoes,
              ...extras,
              exigirAvantPro: true
          });
          verificarCancelamentoFavoritos();
          return anunciosAvant;
      };

      try {
          const anunciosAvant = await tentarColetaAvant();
          if (anunciosAvant.length) return anunciosAvant;

          const statusFinalAvantWrapper = typeof diagnosticarAvantProNoWebview === 'function'
              ? await diagnosticarAvantProNoWebview().catch(() => null)
              : null;
          const precisaConectarAvantWrapper = !!(statusFinalAvantWrapper && (
              typeof statusAvantProPedeLoginOuVinculo === 'function'
                  ? statusAvantProPedeLoginOuVinculo(statusFinalAvantWrapper)
                  : (statusFinalAvantWrapper.needsAccountLink || statusFinalAvantWrapper.accountActionRequired)
          ));
          if (precisaConectarAvantWrapper) {
              await aguardarConexaoAvantProFavoritos(statusFinalAvantWrapper, { termo });
              const retryAvantConectado = await tentarColetaAvant({ retryAvantConectado: true });
              if (retryAvantConectado.length) return retryAvantConectado;
          }
          throw erroColetaMercadoLivreFavoritos(`Avant Pro nao retornou anuncios coletaveis para "${termo}". Confira se o Mercado Livre carregou resultados e se o Avant Pro esta conectado no navegador interno.`);
      } catch (err) {
          if (mlFavoritosCancelado || (err && err.canceladoFavoritos)) throw err;
          if (erroEhLoginMercadoLivreFavoritos(err)) {
              mostrarBalaoFavoritosStatus(err && err.message ? err.message : 'O Mercado Livre pediu login/verificacao no navegador interno.', {
                  erro: true,
                  larga: true,
                  titulo: 'Login Mercado Livre necessario',
                  tempoMs: 12000
              });
              throw err;
          }
          if (erroEhLoginAvantProFavoritos(err)) {
              const statusLogin = (err && err.avantStatus) || {
                  accountActionRequired: true,
                  message: err && err.message ? err.message : 'Avant Pro nao retornou dados coletaveis.'
              };
              await aguardarConexaoAvantProFavoritos(statusLogin, { termo });
              const retryAvantConectado = await tentarColetaAvant({ retryAvantConectado: true });
              if (retryAvantConectado.length) return retryAvantConectado;
              throw erroLoginAvantProFavoritos(`Avant Pro ainda nao retornou dados coletaveis para "${termo}". Confirme manualmente no navegador interno se o Avant Pro esta pronto e tente novamente.`);
          }
          if (err && err.semResultadosMl) {
              const statusSemResultados = typeof diagnosticarAvantProNoWebview === 'function'
                  ? await diagnosticarAvantProNoWebview().catch(() => null)
                  : null;
              if (statusSemResultados && statusSemResultados.loadingScreen && Number(statusSemResultados.cardCount || 0) <= 0) {
                  throw erroColetaMercadoLivreFavoritos(`Mercado Livre ficou carregando a busca "${termo}" e nao exibiu anuncios. Reabra a pesquisa no navegador interno e tente novamente.`);
              }
              throw erroColetaMercadoLivreFavoritos(`O Mercado Livre indicou sem resultados para "${termo}" no navegador interno. Ajuste a pesquisa e tente novamente.`);
          }
          if (erroEhColetaMercadoLivreFavoritos(err)) throw err;
          throw erroColetaMercadoLivreFavoritos(`Nao consegui ler dados coletaveis do Avant Pro para "${termo}". ${err && err.message ? err.message : 'Confira o navegador interno e tente novamente.'}`);
      }
  }

  async function buscarAnunciosFavoritosPorTermoComAvantObrigatorio(termo, opcoes = {}) {
      const anuncios = await buscarAnunciosFavoritosPorTermo(termo, {
          ...opcoes,
          exigirAvantPro: true
      });
      if (!anuncios.length) {
          throw erroColetaMercadoLivreFavoritos(`Avant Pro nao retornou anuncios coletaveis para "${termo}". Confira se o Mercado Livre carregou resultados e se o Avant Pro esta conectado no navegador interno.`);
      }
      return anuncios;
  }

  Object.assign(internal, {
    buscarAnunciosFavoritosPorTermoFluxoControlado,
    buscarAnunciosFavoritosPorTermo,
    buscarAnunciosFavoritosPorTermoComAvantObrigatorio
  });
  internal.components.add('09-search-entrypoints');
})(window);
