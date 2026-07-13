(function () {
  'use strict';

  const raiz = window.FavoritosV2 = window.FavoritosV2 || {};
  const browser = raiz.browser = raiz.browser || {};
  const shellBridge = browser.shellBridge = browser.shellBridge || {};

  function usarNavegadorMlNoShellElectron() {
    try {
      if (window.electronAPI && (
        typeof window.electronAPI.startFavoritosWorkerBrowser === 'function'
        || typeof window.electronAPI.showEmbeddedMlBrowser === 'function'
      )) {
        return true;
      }
    } catch (_err) {}
    try {
      return !!(window.top && window.top !== window && typeof window.top.postMessage === 'function');
    } catch (_err) {
      return false;
    }
  }

  function createBridge(contexto = {}) {
    let requestId = 0;
    let lastShow = null;
    let positionTimer = null;
    let ocultoPorBalao = false;
    const pending = new Map();

    const get = (name, fallback = null) => {
      const value = contexto[name];
      return typeof value === 'function' ? value() : fallback;
    };
    const call = (name, args = [], fallback = null) => {
      const fn = contexto[name];
      if (typeof fn === 'function') return fn.apply(contexto, args);
      return fallback;
    };
    const getProxy = () => call('getProxy', [], null);
    const setProxy = proxy => call('setProxy', [proxy], null);
    const getUrlInput = () => call('getUrlInput', [], null);
    const getDefaultUrl = () => call('getDefaultUrl', [], 'https://www.mercadolivre.com.br/');
    const isBackground = () => !!call('isBackground', [], false);
    const isBalloonOpen = () => !!call('isBalloonOpen', [], false);
    const getBounds = () => call('getBounds', [], null);
    const areUrlsEquivalent = (a, b) => !!call('areUrlsEquivalent', [a, b], false);
    const usarWorkerFavoritos = () => !!window.__JK_FAVORITOS_WORKER_BROWSER_ACTIVE;
    const electronApi = () => {
      try {
        if (window.electronAPI) return window.electronAPI;
      } catch (_err) {}
      try {
        if (window.top && window.top !== window && window.top.electronAPI) return window.top.electronAPI;
      } catch (_err) {}
      return null;
    };
    const chamarWorkerDireto = (name, args = []) => {
      const controller = browser.workerController;
      if (usarWorkerFavoritos() && controller && typeof controller.invoke === 'function') {
        return controller.invoke(name, args);
      }
      const api = electronApi();
      if (!usarWorkerFavoritos() || !api || typeof api[name] !== 'function') return null;
      try {
        return Promise.resolve(api[name].apply(api, args));
      } catch (err) {
        return Promise.reject(err);
      }
    };

    function enviar(channel, payload = {}) {
      if (!usarNavegadorMlNoShellElectron()) return;
      const payloadFinal = usarWorkerFavoritos()
        ? { ...(payload || {}), worker: true }
        : (payload || {});
      if (channel === 'jk-ml-browser-show') {
        const url = String(payloadFinal && payloadFinal.url || '').trim();
        const bounds = payloadFinal && payloadFinal.bounds && typeof payloadFinal.bounds === 'object' ? payloadFinal.bounds : {};
        const normalizar = browser.urlUtils && browser.urlUtils.normalizarUrlMercadoLivreParaComparacao;
        const comparable = {
          url: typeof normalizar === 'function' ? normalizar(url) : url,
          left: Math.round(Number(bounds.left) || 0),
          top: Math.round(Number(bounds.top) || 0),
          width: Math.round(Number(bounds.width) || 0),
          height: Math.round(Number(bounds.height) || 0),
          background: !!bounds.background
        };
        const agora = Date.now();
        if (
          lastShow
          && lastShow.url === comparable.url
          && lastShow.left === comparable.left
          && lastShow.top === comparable.top
          && lastShow.width === comparable.width
          && lastShow.height === comparable.height
          && lastShow.background === comparable.background
          && agora - lastShow.at < 2500
        ) {
          return;
        }
        lastShow = { ...comparable, at: agora };
      }
      try {
        window.top.postMessage({ channel, payload: payloadFinal }, '*');
      } catch (_err) {}
    }

    function ocultarDefinitivo(opcoes = {}) {
      ocultoPorBalao = false;
      const proxy = getProxy();
      if (proxy) proxy.__visible = false;
      const descarregarConteudo = !!(opcoes && (opcoes.descarregarConteudo || opcoes.destroy || opcoes.unload));
      const payload = {
        reason: opcoes.reason || 'favoritos-hide-definitivo',
        preserveAvantProSession: opcoes.preserveAvantProSession !== false
      };
      if (descarregarConteudo) payload.destroy = true;
      enviar('jk-ml-browser-hide', payload);
    }

    function atualizarPosicao() {
      const proxy = getProxy();
      if (!proxy || !proxy.__visible) return;
      const bounds = getBounds();
      if (!bounds) return;
      enviar('jk-ml-browser-position', { bounds });
    }

    function ocultarTemporariamente() {
      const proxy = getProxy();
      if (!proxy || !proxy.__visible) return;
      ocultoPorBalao = true;
      enviar('jk-ml-browser-hide');
    }

    function restaurarSeVisivel() {
      if (!ocultoPorBalao) return;
      ocultoPorBalao = false;
      const proxy = getProxy();
      if (!proxy || !proxy.__visible) return;
      if (!isBalloonOpen()) return;
      atualizarPosicao();
    }

    function agendarAtualizacaoPosicao() {
      const proxy = getProxy();
      if (!proxy || !proxy.__visible) return;
      if (positionTimer) return;
      positionTimer = setTimeout(() => {
        positionTimer = null;
        atualizarPosicao();
      }, 80);
    }

    function criarProxy() {
      const atual = getProxy();
      if (atual) return atual;
      const listeners = new Map();
      const proxy = {
        parentNode: get('getHost'),
        __isShellBrowserProxy: true,
        __visible: false,
        currentUrl: '',
        addEventListener(name, handler) {
          if (!listeners.has(name)) listeners.set(name, new Set());
          listeners.get(name).add(handler);
        },
        removeEventListener(name, handler) {
          if (listeners.has(name)) listeners.get(name).delete(handler);
        },
        dispatchEvent(name, event = {}) {
          const set = listeners.get(name);
          if (!set) return;
          set.forEach(handler => {
            try { handler(event); } catch (err) { console.warn('Falha em listener do navegador ML:', err); }
          });
        },
        getURL() {
          return proxy.currentUrl || '';
        },
        executeJavaScript(code) {
          const direto = chamarWorkerDireto('executeFavoritosWorkerBrowser', [code]);
          if (direto) return direto;
          const req = `ml-shell-${Date.now()}-${++requestId}`;
          return new Promise((resolve, reject) => {
            pending.set(req, { resolve, reject });
            enviar('jk-ml-browser-execute', { requestId: req, code });
            setTimeout(() => {
              if (!pending.has(req)) return;
              pending.delete(req);
              reject(new Error('Tempo limite ao executar script no navegador do Mercado Livre.'));
            }, 20000);
          });
        },
        clickAt(point) {
          const direto = chamarWorkerDireto('clickFavoritosWorkerBrowser', [point || {}]);
          if (direto) return direto;
          const req = `ml-shell-click-${Date.now()}-${++requestId}`;
          return new Promise((resolve, reject) => {
            pending.set(req, { resolve, reject });
            enviar('jk-ml-browser-click', { requestId: req, point: point || {} });
            setTimeout(() => {
              if (!pending.has(req)) return;
              pending.delete(req);
              reject(new Error('Tempo limite ao clicar no navegador do Mercado Livre.'));
            }, 6000);
          });
        },
        typeText(payload) {
          const direto = chamarWorkerDireto('typeFavoritosWorkerBrowser', [payload || {}]);
          if (direto) return direto;
          const req = `ml-shell-type-${Date.now()}-${++requestId}`;
          return new Promise((resolve, reject) => {
            pending.set(req, { resolve, reject });
            enviar('jk-ml-browser-type', { requestId: req, ...(payload || {}) });
            setTimeout(() => {
              if (!pending.has(req)) return;
              pending.delete(req);
              reject(new Error('Tempo limite ao digitar no navegador do Mercado Livre.'));
            }, 7000);
          });
        },
        insertCSS(css) {
          const code = `
            (function () {
              var style = document.getElementById('jk-discreet-scrollbar-style');
              if (!style) {
                style = document.createElement('style');
                style.id = 'jk-discreet-scrollbar-style';
                document.head.appendChild(style);
              }
              style.textContent = ${JSON.stringify(css || '')};
              return true;
            })();
          `;
          return proxy.executeJavaScript(code).catch(() => null);
        }
      };
      Object.defineProperty(proxy, 'src', {
        get() {
          return proxy.currentUrl;
        },
        set(url) {
          const proximaUrl = String(url || '');
          const segundoPlano = isBackground();
          const mesmaPesquisa = areUrlsEquivalent(proxy.currentUrl, proximaUrl);
          if (mesmaPesquisa && proxy.__visible && !segundoPlano) {
            setTimeout(atualizarPosicao, 80);
            return;
          }
          proxy.currentUrl = proximaUrl;
          proxy.__visible = !segundoPlano;
          const direto = chamarWorkerDireto('startFavoritosWorkerBrowser', [proxy.currentUrl]);
          if (direto) {
            direto
              .then(result => {
                const urlFinal = result && result.url || proxy.currentUrl;
                proxy.currentUrl = urlFinal;
                proxy.dispatchEvent(result && result.success === false ? 'did-fail-load' : 'did-finish-load', {
                  url: urlFinal,
                  warning: result && result.loadWarning || '',
                  errorDescription: result && result.reason || ''
                });
              })
              .catch(err => {
                proxy.dispatchEvent('did-fail-load', {
                  url: proxy.currentUrl,
                  errorDescription: err && err.message ? err.message : String(err),
                  errorCode: -1
                });
              });
            return;
          }
          const bounds = getBounds();
          enviar('jk-ml-browser-show', { url: proxy.currentUrl, bounds });
          if (!segundoPlano) setTimeout(atualizarPosicao, 120);
        }
      });
      setProxy(proxy);
      return proxy;
    }

    function handleMessage(data = {}) {
      const proxy = getProxy();
      if (data.channel === 'jk-ml-browser-event' && proxy) {
        if (data.url) proxy.currentUrl = data.url;
        proxy.dispatchEvent(data.event, data);
        return true;
      }
      if (
        data.channel === 'jk-ml-browser-execute-result'
        || data.channel === 'jk-ml-browser-click-result'
        || data.channel === 'jk-ml-browser-type-result'
      ) {
        const item = pending.get(data.requestId);
        if (!item) return false;
        pending.delete(data.requestId);
        if (data.error) item.reject(new Error(data.error));
        else item.resolve(data.result);
        return true;
      }
      return false;
    }

    function reexibirAoRetornar() {
      if (!usarNavegadorMlNoShellElectron()) return;
      const proxy = getProxy();
      if (!proxy || !proxy.__visible) return;
      if (document.visibilityState && document.visibilityState !== 'visible') return;
      if (!isBalloonOpen() || isBackground()) {
        ocultarDefinitivo(call('getHideOnReturnOptions', [], {}));
        return;
      }
      const input = getUrlInput();
      const url = String(proxy.currentUrl || (input && input.value) || '').trim();
      if (!/^https?:\/\//i.test(url)) return;
      const bounds = getBounds();
      if (!bounds) return;
      enviar('jk-ml-browser-show', { url, bounds });
      setTimeout(atualizarPosicao, 120);
    }

    function forcarVisivel() {
      if (!usarNavegadorMlNoShellElectron()) return false;
      let proxy = getProxy();
      if (!proxy) proxy = criarProxy();
      if (!proxy) return false;
      const input = getUrlInput();
      const url = String(proxy.currentUrl || (input && input.value) || getDefaultUrl() || '').trim();
      if (!/^https?:\/\//i.test(url)) return false;
      proxy.__visible = true;
      let bounds = getBounds();
      if (!bounds && typeof window.mudarAba === 'function') {
        try { window.mudarAba('navegador'); } catch (_err) {}
        bounds = getBounds();
      }
      if (!bounds) return false;
      enviar('jk-ml-browser-show', { url, bounds, forceVisible: true });
      setTimeout(atualizarPosicao, 120);
      return true;
    }

    return {
      usarNavegadorMlNoShellElectron,
      enviar,
      ocultarDefinitivo,
      atualizarPosicao,
      ocultarTemporariamente,
      restaurarSeVisivel,
      agendarAtualizacaoPosicao,
      criarProxy,
      handleMessage,
      reexibirAoRetornar,
      forcarVisivel
    };
  }

  Object.assign(shellBridge, {
    usarNavegadorMlNoShellElectron,
    createBridge
  });
})();
