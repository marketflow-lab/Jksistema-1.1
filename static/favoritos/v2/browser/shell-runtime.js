(function (global) {
  'use strict';

  const browser = global.FavoritosV2.browser;
        function promiseComTimeout(promise, ms, mensagem) {
            let timer = null;
            const timeout = new Promise((_, reject) => {
                timer = setTimeout(() => reject(new Error(mensagem || 'Tempo limite excedido.')), ms);
            });
            return Promise.race([promise, timeout]).finally(() => {
                if (timer) clearTimeout(timer);
            });
        }

        function esperar(ms) {
            return new Promise(resolve => setTimeout(resolve, ms));
        }

        function usarNavegadorMlNoShellElectron() {
            if (window.FavoritosV2?.browser?.shellBridge?.usarNavegadorMlNoShellElectron?.()) return true;
            try {
                return !!(window.electronAPI && (
                    typeof window.electronAPI.startFavoritosWorkerBrowser === 'function'
                    || typeof window.electronAPI.showEmbeddedMlBrowser === 'function'
                ));
            } catch (_err) {
                return false;
            }
        }

        function obterBoundsNavegadorMl() {
            if (navegadorMlEmSegundoPlano()) {
                return {
                    left: 0,
                    top: 0,
                    width: 1280,
                    height: 900,
                    background: true
                };
            }
            const alvo = mlBrowserHost || mlBrowserFrameWrapEl;
            if (!alvo || typeof alvo.getBoundingClientRect !== 'function') return null;
            const rect = alvo.getBoundingClientRect();
            const limite = balaoResultadosMlAberto() && mlWorkModalDialogEl && typeof mlWorkModalDialogEl.getBoundingClientRect === 'function'
                ? mlWorkModalDialogEl.getBoundingClientRect()
                : null;
            const viewport = {
                left: 0,
                top: 0,
                right: window.innerWidth || rect.right,
                bottom: window.innerHeight || rect.bottom
            };
            const clip = limite
                ? {
                    left: Math.max(limite.left, viewport.left),
                    top: Math.max(limite.top, viewport.top),
                    right: Math.min(limite.right, viewport.right),
                    bottom: Math.min(limite.bottom, viewport.bottom)
                }
                : viewport;
            const left = Math.max(rect.left, clip.left);
            const top = Math.max(rect.top, clip.top);
            const right = Math.min(rect.right, clip.right);
            const bottom = Math.min(rect.bottom, clip.bottom);
            const width = Math.max(0, right - left);
            const height = Math.max(0, bottom - top);
            if (width < 20 || height < 20) return null;
            return {
                left,
                top,
                width,
                height
            };
        }

        const favoritosBrowserShellBridge = window.FavoritosV2?.browser?.shellBridge?.createBridge?.({
            getProxy: () => mlShellBrowserProxy,
            setProxy: (proxy) => {
                mlShellBrowserProxy = proxy;
                return proxy;
            },
            getHost: () => mlBrowserHost,
            getBounds: () => obterBoundsNavegadorMl(),
            getUrlInput: () => mlUrlInput,
            getDefaultUrl: () => ML_DEFAULT_URL,
            isBackground: () => navegadorMlEmSegundoPlano(),
            isBalloonOpen: () => balaoResultadosMlAberto(),
            areUrlsEquivalent: (atual, alvo) => urlsMercadoLivreEquivalentes(atual, alvo),
            getHideOnReturnOptions: () => {
                const ocultandoExecucao = !!(mlFavoritosEmExecucao && mlFavoritosExecucaoEmSegundoPlano);
                return {
                    descarregarConteudo: !ocultandoExecucao,
                    reason: ocultandoExecucao ? 'favoritos-hide-background' : 'favoritos-modal-close'
                };
            }
        }) || null;
        global.favoritosBrowserShellBridge = favoritosBrowserShellBridge;

        function enviarNavegadorMlParaShell(channel, payload = {}) {
            return favoritosBrowserShellBridge?.enviar(channel, payload);
        }

        function ocultarNavegadorMlShellDefinitivo(opcoes = {}) {
            return favoritosBrowserShellBridge?.ocultarDefinitivo(opcoes);
        }

        function atualizarPosicaoNavegadorMlShell() {
            return favoritosBrowserShellBridge?.atualizarPosicao();
        }

        function ocultarNavegadorMlShellTemporariamente() {
            return favoritosBrowserShellBridge?.ocultarTemporariamente();
        }

        function restaurarNavegadorMlShellSeVisivel() {
            return favoritosBrowserShellBridge?.restaurarSeVisivel();
        }

        function agendarAtualizacaoPosicaoNavegadorMlShell() {
            return favoritosBrowserShellBridge?.agendarAtualizacaoPosicao();
        }

        function criarProxyNavegadorMlShell() {
            return favoritosBrowserShellBridge?.criarProxy() || null;
        }

        function forcarProxyNavegadorFavoritosWorker(urlAtual = '') {
            window.__JK_FAVORITOS_WORKER_BROWSER_ACTIVE = true;
            const url = String(urlAtual || '').trim();
            if (mlWebviewEl && mlWebviewEl.__isShellBrowserProxy) {
                if (url) mlWebviewEl.currentUrl = url;
                return mlWebviewEl;
            }
            if (mlWebviewEl && !mlWebviewEl.__isShellBrowserProxy) {
                try {
                    if (mlWebviewEl.parentNode) mlWebviewEl.parentNode.removeChild(mlWebviewEl);
                } catch (_removeErr) {}
            }
            if (mlBrowserHost) {
                mlBrowserHost.innerHTML = `
                    <div class="browser-warning">
                        <strong>Favoritos rodando no navegador trabalhador.</strong>
                        <span>Use o botao Ver para acompanhar a coleta.</span>
                    </div>
                `;
            }
            mlWebviewEl = criarProxyNavegadorMlShell();
            if (mlWebviewEl && url) mlWebviewEl.currentUrl = url;
            aplicarScrollbarsDiscretasNoWebview(mlWebviewEl);
            return mlWebviewEl;
        }

        window.addEventListener('message', (event) => {
            const origemConhecida = event && (
                event.source === window
                || event.source === window.parent
                || event.source === window.top
            );
            const origemCompativel = !event.origin
                || event.origin === 'null'
                || event.origin === window.location.origin;
            if (!origemConhecida || !origemCompativel) return;
            const data = event && event.data ? event.data : {};
            if (!data || typeof data !== 'object') return;
            favoritosBrowserShellBridge?.handleMessage(data);
        });

        function reexibirNavegadorMlShellAoRetornar() {
            return favoritosBrowserShellBridge?.reexibirAoRetornar();
        }

        function forcarNavegadorMlShellVisivel() {
            return !!favoritosBrowserShellBridge?.forcarVisivel();
        }

  const api = { promiseComTimeout, esperar, usarNavegadorMlNoShellElectron, obterBoundsNavegadorMl, enviarNavegadorMlParaShell, ocultarNavegadorMlShellDefinitivo, atualizarPosicaoNavegadorMlShell, ocultarNavegadorMlShellTemporariamente, restaurarNavegadorMlShellSeVisivel, agendarAtualizacaoPosicaoNavegadorMlShell, criarProxyNavegadorMlShell, forcarProxyNavegadorFavoritosWorker, reexibirNavegadorMlShellAoRetornar, forcarNavegadorMlShellVisivel };
  browser.shellRuntime = Object.freeze(api);
  Object.assign(global, api);
})(window);
