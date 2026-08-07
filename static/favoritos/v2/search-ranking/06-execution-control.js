(function (global) {
  'use strict';

  const searchRanking = global.FavoritosV2.searchRanking;
  const internal = searchRanking.internal;
  if (internal.components.has('06-execution-control')) return;

  function esconderBalaoFavoritosStatus(...args) {
    const implementation = internal.esconderBalaoFavoritosStatus;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: esconderBalaoFavoritosStatus');
    return implementation(...args);
  }

  function criarErroFavoritosCancelado() {
      const erro = new Error('Processo de favoritos cancelado pelo usuario.');
      erro.canceladoFavoritos = true;
      return erro;
  }

  function aplicarEstadoWorkerFavoritos(status = {}) {
      const dados = status && typeof status === 'object' ? status : {};
      const statusTexto = String(dados.status || '').toLowerCase();
      const cancelado = dados.cancelRequested === true
          || statusTexto === 'cancel_requested'
          || statusTexto === 'canceling'
          || statusTexto === 'canceled'
          || statusTexto === 'cancelled';
      if (cancelado && mlFavoritosEmExecucao) {
          mlFavoritosCancelado = true;
          mlFavoritosPausado = false;
          if (mlFavoritosAbortController) {
              try {
                  mlFavoritosAbortController.abort();
              } catch (_err) {}
          }
      }
      return cancelado;
  }

  function verificarCancelamentoFavoritos() {
      if (mlFavoritosCancelado) {
          throw criarErroFavoritosCancelado();
      }
  }

  async function sincronizarEstadoWorkerFavoritos() {
      const api = window.FavoritosV2.execution.publicApi.obterElectronApiFavoritosExecucao();
      if (!api) return null;
      try {
          const status = window.__JK_FAVORITOS_WORKERS_POOL_ACTIVE && typeof api.getFavoritosWorkersPoolStatus === 'function'
              ? await api.getFavoritosWorkersPoolStatus()
              : (typeof api.getFavoritosWorkerBrowserStatus === 'function'
                  ? await api.getFavoritosWorkerBrowserStatus()
                  : null);
          if (!status) return null;
          aplicarEstadoWorkerFavoritos(status);
          return status;
      } catch (_err) {
          return null;
      }
  }

  async function aguardarControleFavoritos() {
      await sincronizarEstadoWorkerFavoritos();
      verificarCancelamentoFavoritos();
      while (mlFavoritosPausado && !mlFavoritosCancelado) {
          await sincronizarEstadoWorkerFavoritos();
          await new Promise(resolve => setTimeout(resolve, 180));
      }
      verificarCancelamentoFavoritos();
  }

  function sinalFavoritosAtual() {
      return mlFavoritosAbortController ? mlFavoritosAbortController.signal : undefined;
  }

  function executarComTimeoutFavoritos(tarefa, timeoutMs = 45000, sinalPai = undefined) {
      const limiteMs = Math.max(50, Number(timeoutMs) || 45000);
      const controller = typeof AbortController === 'function' ? new AbortController() : null;
      let timeoutId = null;
      let onAbortPai = null;
      const erroTimeout = new Error(`A etapa excedeu o limite de ${Math.ceil(limiteMs / 1000)}s.`);
      erroTimeout.name = 'TimeoutError';
      erroTimeout.favoritosTimeout = true;
      const abortarPeloPai = () => {
          if (controller && !controller.signal.aborted) controller.abort();
      };
      if (sinalPai && typeof sinalPai.addEventListener === 'function') {
          onAbortPai = abortarPeloPai;
          if (sinalPai.aborted) abortarPeloPai();
          else sinalPai.addEventListener('abort', onAbortPai, { once: true });
      }
      const execucao = Promise.resolve().then(() => tarefa(controller ? controller.signal : sinalPai));
      const prazo = new Promise((_resolve, reject) => {
          timeoutId = setTimeout(() => {
              reject(erroTimeout);
              if (controller && !controller.signal.aborted) controller.abort();
          }, limiteMs);
      });
      return Promise.race([execucao, prazo]).finally(() => {
          if (timeoutId) clearTimeout(timeoutId);
          if (sinalPai && onAbortPai && typeof sinalPai.removeEventListener === 'function') {
              sinalPai.removeEventListener('abort', onAbortPai);
          }
      });
  }

  function cancelarFavoritosEmExecucao() {
      if (!mlFavoritosEmExecucao) return;
      mlFavoritosCancelado = true;
      mlFavoritosPausado = false;
      if (mlFavoritosAbortController) {
          try {
              mlFavoritosAbortController.abort();
          } catch (_err) {}
      }
      if (mlFavoritosJobIdAtual && typeof window.FavoritosV2?.execution?.publicApi?.cancelarFavoritosJobAtualServidor === 'function') {
          window.FavoritosV2.execution.publicApi.cancelarFavoritosJobAtualServidor().catch((err) => {
              console.warn('Nao foi possivel cancelar job de favoritos no backend:', err);
          });
      }
      if (typeof window.FavoritosV2?.execution?.publicApi?.pararPollingFavoritosJob === 'function') window.FavoritosV2.execution.publicApi.pararPollingFavoritosJob();
      if (mlFavoritosJobRenderRaf) {
          try {
              if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(mlFavoritosJobRenderRaf);
              else clearTimeout(mlFavoritosJobRenderRaf);
          } catch (_err) {}
          mlFavoritosJobRenderRaf = 0;
      }
      mlFavoritosJobRenderPendente = false;
      if (typeof limparStatusTerminalFavoritos === 'function') {
          limparStatusTerminalFavoritos({ status: 'canceled' });
      } else {
          esconderBalaoFavoritosStatus();
          atualizarStatusFavoritosNoNavegadorMl('', false, { imediato: true, forcar: true });
      }
      if (typeof window.FavoritosV2?.execution?.publicApi?.pararNavegadorFavoritosBackground === 'function') {
          window.FavoritosV2.execution.publicApi.pararNavegadorFavoritosBackground({
              status: 'canceled',
              message: '',
              reason: 'favoritos-cancelado-pelo-usuario'
          });
      }
      atualizarContadorSkuSidebarSelecionados();
      atualizarFiltroAzulFavoritos();
  }

  function inicializarSincronizacaoWorkerFavoritos() {
      const api = window.FavoritosV2.execution.publicApi.obterElectronApiFavoritosExecucao();
      if (!api || window.__favoritosCancelSyncReady) return;
      window.__favoritosCancelSyncReady = true;
      const ouvir = (nome, handler) => {
          if (typeof api[nome] !== 'function') return;
          try {
              api[nome](handler);
          } catch (_err) {}
      };
      ouvir('onFavoritosWorkerProgress', (status) => aplicarEstadoWorkerFavoritos(status));
      ouvir('onFavoritosWorkerDone', (status) => aplicarEstadoWorkerFavoritos(status));
      ouvir('onFavoritosWorkerError', (status) => aplicarEstadoWorkerFavoritos(status));
  }

  Object.assign(internal, {
    criarErroFavoritosCancelado,
    aplicarEstadoWorkerFavoritos,
    verificarCancelamentoFavoritos,
    sincronizarEstadoWorkerFavoritos,
    aguardarControleFavoritos,
    sinalFavoritosAtual,
    executarComTimeoutFavoritos,
    cancelarFavoritosEmExecucao,
    inicializarSincronizacaoWorkerFavoritos
  });
  internal.components.add('06-execution-control');
})(window);
