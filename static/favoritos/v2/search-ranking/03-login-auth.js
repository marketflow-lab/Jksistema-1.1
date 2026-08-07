(function (global) {
  'use strict';

  const searchRanking = global.FavoritosV2.searchRanking;
  const internal = searchRanking.internal;
  if (internal.components.has('03-login-auth')) return;

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

  function normalizarTermoPesquisaFavoritos(...args) {
    const implementation = internal.normalizarTermoPesquisaFavoritos;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: normalizarTermoPesquisaFavoritos');
    return implementation(...args);
  }

  function montarPesquisasFavoritosSku(...args) {
    const implementation = internal.montarPesquisasFavoritosSku;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: montarPesquisasFavoritosSku');
    return implementation(...args);
  }

  function perguntarQuantidadePesquisasFavoritos() {
      if (!mlFavoritosBalloonEl || !mlFavoritosBalloonActionsEl) {
          const resposta = window.prompt('Quantas pesquisas deseja fazer por SKU? Digite 1, 2 ou 3.', '3');
          if (resposta === null) return Promise.resolve(null);
          const quantidade = Number(String(resposta).trim());
          if (![1, 2, 3].includes(quantidade)) {
              alert('Informe apenas 1, 2 ou 3 pesquisas.');
              return Promise.resolve(null);
          }
          return Promise.resolve(quantidade);
      }

      if (mlFavoritosPerguntaResolver) {
          mlFavoritosPerguntaResolver(null);
          mlFavoritosPerguntaResolver = null;
      }

      mlFavoritosBalloonActionsEl.innerHTML = '';
      return new Promise(resolve => {
          mlFavoritosPerguntaResolver = resolve;
          [1, 2, 3].forEach(qtd => {
              const botao = document.createElement('button');
              botao.type = 'button';
              botao.textContent = `${qtd} pesquisa${qtd > 1 ? 's' : ''}`;
              botao.addEventListener('click', () => {
                  mlFavoritosPerguntaResolver = null;
                  resolve(qtd);
              });
              mlFavoritosBalloonActionsEl.appendChild(botao);
          });
          const cancelar = document.createElement('button');
          cancelar.type = 'button';
          cancelar.textContent = 'Cancelar';
          cancelar.addEventListener('click', () => {
              mlFavoritosPerguntaResolver = null;
              esconderBalaoFavoritosStatus();
              resolve(null);
          });
          mlFavoritosBalloonActionsEl.appendChild(cancelar);
          mostrarBalaoFavoritosStatus('Escolha quantas pesquisas deseja fazer para os SKUs selecionados.', {
              manterAcoes: true
          });
      });
  }

  function obterTermoInicialLoginAvantProFavoritos(selecionados = [], quantidade = 1) {
      const limite = Math.max(1, Math.min(3, Number(quantidade) || 1));
      const itens = Array.isArray(selecionados) ? selecionados : [];
      for (const item of itens) {
          if (!item) continue;
          try {
              if (typeof montarPesquisasFavoritosSku === 'function') {
                  const info = montarPesquisasFavoritosSku(item, limite);
                  const termoInfo = info && Array.isArray(info.termos)
                      ? info.termos.map(pesquisa => pesquisa && pesquisa.termo).find(Boolean)
                      : '';
                  if (termoInfo) return normalizarTermoPesquisaFavoritos(termoInfo);
              }
          } catch (_err) {}
          for (let numero = 1; numero <= limite; numero += 1) {
              const chaves = [`pesquisa_${numero}`, `pesquisa${numero}`, `Pesquisa ${numero}`];
              const termo = normalizarTermoPesquisaFavoritos(chaves.map(chave => item[chave]).find(Boolean) || '');
              if (termo) return termo;
          }
      }
      return '';
  }

  function limparBotaoContinuarLoginAvantProFavoritos() {
      const existente = document.getElementById('ml-work-modal-continue-login-favoritos');
      if (existente && existente.parentElement) existente.parentElement.removeChild(existente);
  }

  function urlEmFluxoAutenticacaoMercadoLivreFavoritos(value) {
      const raw = String(value || '').trim();
      if (!raw) return false;
      try {
          const url = new URL(raw);
          const host = String(url.hostname || '').toLowerCase();
          const mercadoLivreHost = host === 'mercadolivre.com'
              || host.endsWith('.mercadolivre.com')
              || host === 'mercadolivre.com.br'
              || host.endsWith('.mercadolivre.com.br')
              || host === 'mercadolibre.com'
              || host.endsWith('.mercadolibre.com');
          if (!mercadoLivreHost) return false;
          const pathname = String(url.pathname || '/').toLowerCase().replace(/\/{2,}/g, '/');
          const specificAuthRoute = /^\/gz\/account-verification(?:\/|$)|^\/jms\/[^/]+\/lgz(?:\/|$)|^\/password\/validation(?:\/|$)|^\/totp(?:\/|$)|^\/login\/challenges?(?:\/|$)/.test(pathname);
          const genericAuthHost = host === 'mercadolivre.com'
              || host === 'mercadolivre.com.br'
              || host === 'mercadolibre.com'
              || /^(?:www|auth|accounts?|account)\./.test(host);
          const genericAuthRoute = /^\/login(?:\/|$)|^\/(?:captcha|recaptcha|security[-_/]?check|identity[-_/]?verification)(?:\/|$)/.test(pathname);
          let negativeTraffic = false;
          let explicitAuthParam = false;
          for (const [name, itemValueRaw] of url.searchParams.entries()) {
              const key = String(name || '').toLowerCase();
              const itemValue = String(itemValueRaw || '').toLowerCase();
              if (key === 'logintype' && itemValue === 'negative_traffic') negativeTraffic = true;
              if (['captcha', 'recaptcha', 'security_check', 'identity_verification'].includes(key) && itemValue) {
                  explicitAuthParam = true;
              }
          }
          return specificAuthRoute
              || negativeTraffic
              || (genericAuthHost && (genericAuthRoute || explicitAuthParam));
      } catch (_err) {
          return false;
      }
  }

  function urlHttpValidaFavoritos(value) {
      const raw = String(value || '').trim();
      if (!raw) return false;
      try {
          const url = new URL(raw);
          return (url.protocol === 'http:' || url.protocol === 'https:') && !!url.hostname;
      } catch (_err) {
          return false;
      }
  }

  function urlPertenceAoMercadoLivreFavoritos(value) {
      if (!urlHttpValidaFavoritos(value)) return false;
      try {
          const host = new URL(String(value || '').trim()).hostname.toLowerCase();
          return host === 'mercadolivre.com'
              || host.endsWith('.mercadolivre.com')
              || host === 'mercadolivre.com.br'
              || host.endsWith('.mercadolivre.com.br')
              || host === 'mercadolibre.com'
              || host.endsWith('.mercadolibre.com');
      } catch (_err) {
          return false;
      }
  }

  async function obterEstadoFrescoBrowserShellFavoritos(urlEsperada = '') {
      const bridge = typeof favoritosBrowserShellBridge !== 'undefined'
          ? favoritosBrowserShellBridge
          : null;
      if (!bridge || typeof bridge.verificarEstado !== 'function') return null;
      const estado = await bridge.verificarEstado(String(urlEsperada || '').trim(), 1200).catch(() => null);
      const url = String(estado && estado.url || '').trim();
      if (!estado
          || estado.success !== true
          || estado.available !== true
          || estado.attached !== true
          || !urlPertenceAoMercadoLivreFavoritos(url)) {
          return null;
      }
      return {
          url,
          authFlow: estado.authFlow === true
      };
  }

  async function obterEstadoAutenticacaoMercadoLivreFavoritos() {
      let urlAtual = '';
      let needsLogin = false;
      let leituraConfiavel = false;
      let authFlowShell = false;
      const podeExecutarDireto = !!(mlWebviewEl && typeof mlWebviewEl.executeJavaScript === 'function');
      if (mlWebviewEl && typeof mlWebviewEl.getURL === 'function') {
          try { urlAtual = String(mlWebviewEl.getURL() || '').trim(); } catch (_err) {}
          if (!podeExecutarDireto && urlPertenceAoMercadoLivreFavoritos(urlAtual)) leituraConfiavel = true;
      }
      if (podeExecutarDireto) {
          let timeoutLeituraDom = null;
          const leituraDom = Promise.resolve().then(() => mlWebviewEl.executeJavaScript(`
                  (function () {
                      var texto = String(document.body && (document.body.innerText || document.body.textContent) || '').toLowerCase();
                      try { texto = texto.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                      return {
                          url: String(location.href || ''),
                          needsLogin: texto.indexOf('digite seu e-mail') >= 0
                              || texto.indexOf('digite seu email') >= 0
                              || texto.indexOf('para iniciar sessao') >= 0
                              || texto.indexOf('codigo de verificacao') >= 0
                              || texto.indexOf('verifique sua identidade') >= 0
                      };
                  })();
              `, true)).catch(() => null);
          const limiteLeituraDom = new Promise(resolve => {
              timeoutLeituraDom = setTimeout(() => resolve(null), 800);
          });
          let estado = null;
          try {
              estado = await Promise.race([leituraDom, limiteLeituraDom]);
          } finally {
              if (timeoutLeituraDom) clearTimeout(timeoutLeituraDom);
          }
          if (estado && urlPertenceAoMercadoLivreFavoritos(estado.url)) {
              urlAtual = String(estado.url).trim();
              leituraConfiavel = true;
          }
          needsLogin = !!(estado && estado.needsLogin);
      }
      if (!leituraConfiavel) {
          const estadoShell = await obterEstadoFrescoBrowserShellFavoritos(urlAtual).catch(() => null);
          if (estadoShell) {
              urlAtual = estadoShell.url;
              authFlowShell = estadoShell.authFlow;
              leituraConfiavel = true;
          }
      }
      return {
          url: urlAtual,
          indeterminado: !leituraConfiavel,
          pendente: needsLogin
              || authFlowShell
              || urlEmFluxoAutenticacaoMercadoLivreFavoritos(urlAtual)
      };
  }

  async function validarLoginMercadoLivreAntesDeContinuarFavoritos(onContinuar, botao) {
      const estado = await obterEstadoAutenticacaoMercadoLivreFavoritos().catch(() => ({ url: '', pendente: false, indeterminado: true }));
      if (!estado || estado.indeterminado) {
          if (botao) botao.disabled = false;
          mostrarBalaoFavoritosStatus('A pagina de login ainda esta mudando e nao foi possivel confirmar seu estado. Aguarde alguns segundos e clique em Continuar favoritos novamente.', {
              manterAcoes: true,
              manterNavegadorVisivel: true,
              larga: true,
              titulo: 'Aguardando login'
          });
          return false;
      }
      if (estado && estado.pendente) {
          if (botao) botao.disabled = false;
          abrirBalaoResultadosMl({
              titulo: 'Login Mercado Livre',
              subtitulo: 'Conclua todas as etapas de acesso antes de continuar.',
              mostrarFavoritos: true,
              browserCompleto: true,
              forcarExibicao: true
          });
          mostrarBalaoFavoritosStatus('Conclua o e-mail, senha e eventual codigo de verificacao do Mercado Livre. Aguarde a pagina da pesquisa voltar e so entao clique em Continuar favoritos.', {
              manterAcoes: true,
              manterNavegadorVisivel: true,
              larga: true,
              titulo: 'Login Mercado Livre'
          });
          return false;
      }
      if (typeof onContinuar === 'function') {
          const resultado = await onContinuar();
          return resultado !== false;
      }
      return true;
  }

  function obterElectronApiPersistenciaAvantProFavoritos() {
      try {
          const controller = window.FavoritosV2?.browser?.workerController;
          if (controller && typeof controller.electronApi === 'function') {
              const apiController = controller.electronApi();
              if (apiController) return apiController;
          }
      } catch (_err) {}
      try {
          if (window.electronAPI) return window.electronAPI;
      } catch (_err) {}
      try {
          if (window.top && window.top !== window && window.top.electronAPI) return window.top.electronAPI;
      } catch (_err) {}
      return null;
  }

  async function obterStatusPersistenciaAvantProFavoritos() {
      const api = obterElectronApiPersistenciaAvantProFavoritos();
      if (!api || typeof api.getAvantProStorageStatus !== 'function') {
          return {
              disponivel: false,
              currentUsable: false,
              snapshotUsable: false,
              usable: false
          };
      }
      const status = await api.getAvantProStorageStatus().catch(() => null);
      const currentUsable = !!(status && status.currentUsable);
      const snapshotUsable = !!(status && status.snapshotUsable);
      return {
          disponivel: !!(status && status.success !== false),
          currentUsable,
          snapshotUsable,
          usable: currentUsable || snapshotUsable,
          manifest: status && status.manifest || null
      };
  }

  async function obterEstadoAutenticacaoAvantProFavoritos() {
      const persistencia = await obterStatusPersistenciaAvantProFavoritos().catch(() => ({
          disponivel: false,
          currentUsable: false,
          snapshotUsable: false,
          usable: false
      }));
      const podeExecutarDireto = !!(mlWebviewEl && typeof mlWebviewEl.executeJavaScript === 'function');
      if (!podeExecutarDireto) {
          return {
              indeterminado: true,
              pendente: false,
              detectado: false,
              storageUsable: !!persistencia.usable,
              currentStorageUsable: !!persistencia.currentUsable,
              snapshotStorageUsable: !!persistencia.snapshotUsable
          };
      }
      const estado = await mlWebviewEl.executeJavaScript(`
          (function () {
              var normalizar = function (value) {
                  var text = String(value || '').replace(/\\s+/g, ' ').trim();
                  try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                  return text.toLowerCase();
              };
              var texto = normalizar(document.body && (document.body.innerText || document.body.textContent) || '');
              var marcadores = 0;
              try { marcadores = document.querySelectorAll('[class*="avant" i], [id*="avant" i]').length; } catch (_err) {}
              var recursos = [];
              try {
                  recursos = performance.getEntriesByType('resource')
                      .map(function (entry) { return String(entry && entry.name || ''); })
                      .filter(function (name) { return /chrome-extension:\/\/jdefnfmbnchmnjkcknaadaddgjbgephh|avantpro/i.test(name); });
              } catch (_err) {}
              var pendente = /comece\\s+a\\s+usar\\s+o?\\s*avantpro|entre\\s+na\\s+sua\\s+conta\\s+para\\s+liberar\\s+os\\s+recursos\\s+da\\s+extensao|nao\\s+possui\\s+uma\\s+conta\\?\\s*crie\\s+uma\\s+aqui|fazer\\s+login\\s+no\\s+avant|entrar\\s+no\\s+avant|login\\s+avant\\s*pro/.test(texto);
              var detectado = marcadores > 0 || recursos.length > 0 || /avant\\s*pro|avantpro/.test(texto);
              return {
                  url: String(location.href || ''),
                  pendente: pendente,
                  detectado: detectado,
                  marcadores: marcadores,
                  recursos: recursos.length
              };
          })();
      `, true).catch(() => null);
      return {
          url: String(estado && estado.url || ''),
          indeterminado: !(estado && estado.detectado),
          pendente: !!(estado && estado.pendente),
          detectado: !!(estado && estado.detectado),
          marcadores: Number(estado && estado.marcadores || 0),
          recursos: Number(estado && estado.recursos || 0),
          storageUsable: !!persistencia.usable,
          currentStorageUsable: !!persistencia.currentUsable,
          snapshotStorageUsable: !!persistencia.snapshotUsable
      };
  }

  async function validarLoginAvantProAntesDeContinuarFavoritos(onContinuar, botao) {
      mostrarBalaoFavoritosStatus('Validando login e sessao salva do Avant Pro...', {
          manterAcoes: true,
          manterNavegadorVisivel: true,
          larga: true,
          titulo: 'Validando Avant Pro'
      });
      const estado = await obterEstadoAutenticacaoAvantProFavoritos().catch(() => ({ indeterminado: true, pendente: false }));
      const storageUsable = !!(estado && estado.storageUsable);
      if (!estado || estado.pendente || (estado.indeterminado && !storageUsable)) {
          if (botao) botao.disabled = false;
          abrirBalaoResultadosMl({
              titulo: 'Login Avant Pro',
              subtitulo: 'Conclua o login antes de continuar.',
              mostrarFavoritos: true,
              browserCompleto: true,
              forcarExibicao: true
          });
          mostrarBalaoFavoritosStatus(
              estado && estado.pendente
                  ? 'O Avant Pro ainda mostra a tela de login. Entre na conta e clique em Continuar favoritos novamente.'
                  : 'Ainda nao encontrei login ativo nem uma sessao salva do Avant Pro. Aguarde a extensao carregar e clique em Continuar favoritos novamente.',
              {
                  manterAcoes: true,
                  manterNavegadorVisivel: true,
                  larga: true,
                  titulo: 'Login Avant Pro'
              }
          );
          return false;
      }
      const snapshot = estado.indeterminado && storageUsable
          ? { success: true, skipped: true, reason: 'persisted-session-reused' }
          : await registrarConfirmacaoUsuarioLoginAvantProFavoritos('validacao_dom_apos_login', estado);
      if (!snapshot || snapshot.success !== true) {
          console.warn('Login Avant Pro reconhecido; snapshot sera tentado novamente sem bloquear Favoritos.', {
              reason: snapshot && snapshot.reason || '',
              storageUsable
          });
      }
      if (typeof onContinuar === 'function') {
          const resultado = await onContinuar();
          return resultado !== false;
      }
      return true;
  }

  function mostrarBotaoContinuarLoginAvantProFavoritos(onContinuar) {
      const acoes = document.querySelector('.ml-work-modal-actions');
      const fechar = document.getElementById('ml-work-modal-close');
      if (!acoes) return null;
      let botao = document.getElementById('ml-work-modal-continue-login-favoritos');
      if (!botao) {
          botao = document.createElement('button');
          botao.id = 'ml-work-modal-continue-login-favoritos';
          botao.type = 'button';
          botao.className = 'ml-work-modal-control';
          botao.textContent = 'Continuar favoritos';
          botao.title = 'Clique depois de concluir o login do Mercado Livre e do Avant Pro';
          if (fechar && fechar.parentElement === acoes) {
              acoes.insertBefore(botao, fechar);
          } else {
              acoes.appendChild(botao);
          }
      }
      botao.classList.remove('hidden');
      botao.onclick = async () => {
          botao.disabled = true;
          try {
              const concluiu = typeof onContinuar === 'function' ? await onContinuar(botao) : true;
              if (concluiu === false) botao.disabled = false;
          } catch (err) {
              botao.disabled = false;
              console.warn('Falha ao validar login antes de continuar Favoritos:', err);
          }
      };
      return botao;
  }

  async function registrarConfirmacaoUsuarioLoginAvantProFavoritos(origem = 'prompt', estado = {}) {
      const api = obterElectronApiPersistenciaAvantProFavoritos();
      let memoriaLocalSalva = false;
      try {
          localStorage.setItem('jk_favoritos_avant_login_confirmado_usuario_at', String(Date.now()));
          memoriaLocalSalva = true;
      } catch (_err) {}
      let resultadoSnapshot = { success: true, skipped: true };
      if (api && typeof api.saveAvantProStorageSnapshot === 'function') {
          resultadoSnapshot = await api.saveAvantProStorageSnapshot('favoritos_usuario_confirmou_login_avant', {
              source: 'favoritos',
              origem,
              confirmedAt: Date.now(),
              userConfirmed: true,
              liveAuthConfirmed: estado && estado.liveAuthConfirmed !== undefined
                  ? estado.liveAuthConfirmed === true
                  : true,
              validation: {
                  url: String(estado && estado.url || '').split('#')[0],
                  markers: Number(estado && estado.marcadores || 0),
                  resources: Number(estado && estado.recursos || 0)
              }
          }).catch(() => null);
      }
      if (!resultadoSnapshot || resultadoSnapshot.success !== true) {
          return {
              success: false,
              reason: String(resultadoSnapshot && resultadoSnapshot.reason || 'snapshot-unavailable'),
              memoriaLocalSalva
          };
      }
      return { success: true, memoriaLocalSalva };
  }

  function registrarConfirmacaoUsuarioLoginAvantProBestEffortFavoritos(origem = 'prompt') {
      registrarConfirmacaoUsuarioLoginAvantProFavoritos(origem, {
          userConfirmed: true,
          liveAuthConfirmed: false
      }).then(resultado => {
          if (!resultado || resultado.success !== true) {
              console.warn('Confirmacao do usuario registrada sem bloquear Favoritos; o snapshot do Avant Pro podera ser tentado novamente.', {
                  reason: String(resultado && resultado.reason || '')
              });
          }
      }).catch(err => {
          console.warn('Falha secundaria ao registrar a confirmacao do usuario no Avant Pro.', {
              reason: String(err && err.message || '')
          });
      });
      return true;
  }

  Object.assign(internal, {
    perguntarQuantidadePesquisasFavoritos,
    obterTermoInicialLoginAvantProFavoritos,
    limparBotaoContinuarLoginAvantProFavoritos,
    urlEmFluxoAutenticacaoMercadoLivreFavoritos,
    urlHttpValidaFavoritos,
    urlPertenceAoMercadoLivreFavoritos,
    obterEstadoFrescoBrowserShellFavoritos,
    obterEstadoAutenticacaoMercadoLivreFavoritos,
    validarLoginMercadoLivreAntesDeContinuarFavoritos,
    obterElectronApiPersistenciaAvantProFavoritos,
    obterStatusPersistenciaAvantProFavoritos,
    obterEstadoAutenticacaoAvantProFavoritos,
    validarLoginAvantProAntesDeContinuarFavoritos,
    mostrarBotaoContinuarLoginAvantProFavoritos,
    registrarConfirmacaoUsuarioLoginAvantProFavoritos,
    registrarConfirmacaoUsuarioLoginAvantProBestEffortFavoritos
  });
  internal.components.add('03-login-auth');
})(window);
