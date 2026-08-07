(function (global) {
  'use strict';

  const browser = global.FavoritosV2.browser;
  const pageScripts = browser.pageScripts;

        function criarMlWebview() {
            const deveUsarWorkerFavoritos = navegadorMlEmSegundoPlano()
                && usarNavegadorMlNoShellElectron();
            if (deveUsarWorkerFavoritos) {
                if (mlWebviewEl && !mlWebviewEl.__isShellBrowserProxy) {
                    try {
                        if (mlWebviewEl.parentNode) mlWebviewEl.parentNode.removeChild(mlWebviewEl);
                    } catch (_removeErr) {}
                    mlWebviewEl = null;
                }
                mlBrowserHost.innerHTML = `
                    <div class="browser-warning">
                        <strong>Favoritos rodando no navegador trabalhador.</strong>
                        <span>Use o botao Ver para acompanhar a coleta.</span>
                    </div>
                `;
                mlWebviewEl = criarProxyNavegadorMlShell();
                aplicarScrollbarsDiscretasNoWebview(mlWebviewEl);
                return mlWebviewEl;
            }

            if (mlWebviewEl && mlWebviewEl.parentNode) {
                aplicarScrollbarsDiscretasNoWebview(mlWebviewEl);
                return mlWebviewEl;
            }

            mlBrowserHost.innerHTML = '';
            if (usarNavegadorMlNoShellElectron()) {
                mlBrowserHost.innerHTML = `
                    <div class="browser-warning">
                        <strong>Carregando Mercado Livre no navegador interno...</strong>
                        <span>Se a pagina pedir login, conclua no quadro abaixo.</span>
                    </div>
                `;
                mlWebviewEl = criarProxyNavegadorMlShell();
                aplicarScrollbarsDiscretasNoWebview(mlWebviewEl);
                return mlWebviewEl;
            }

            mlWebviewEl = document.createElement('webview');
            mlWebviewEl.className = 'browser-webview';
            mlWebviewEl.setAttribute('partition', obterParticaoNavegadorPersistente());
            mlWebviewEl.setAttribute('allowpopups', 'true');
            mlWebviewEl.setAttribute('webpreferences', 'contextIsolation=yes,nodeIntegration=no');

            const isAllowedWebUrl = (targetUrl) => {
                const value = String(targetUrl || '').trim();
                if (!value) return true;
                return /^(https?:\/\/|about:blank$|chrome-extension:\/\/)/i.test(value);
            };
            const blockIfExternalProtocol = (event) => {
                const targetUrl = event && event.url ? event.url : '';
                if (!isAllowedWebUrl(targetUrl)) {
                    event.preventDefault();
                }
            };

            const syncUrl = (event) => {
                const nextUrl = (event && event.url) || (typeof mlWebviewEl.getURL === 'function' ? mlWebviewEl.getURL() : '');
                if (nextUrl && /^https?:\/\//i.test(nextUrl)) {
                    mlUrlInput.value = nextUrl;
                }
            };

            mlWebviewEl.addEventListener('did-navigate', syncUrl);
            mlWebviewEl.addEventListener('did-navigate-in-page', syncUrl);
            mlWebviewEl.addEventListener('dom-ready', () => {
                aplicarScrollbarsDiscretasNoWebview(mlWebviewEl);
                tentarLoginAvantProNoWebview(mlWebviewEl, { somenteSeAutorizado: true, verificarDadosAntes: true });
            });
            mlWebviewEl.addEventListener('did-finish-load', () => {
                aplicarScrollbarsDiscretasNoWebview(mlWebviewEl);
                tentarLoginAvantProNoWebview(mlWebviewEl, { somenteSeAutorizado: true, verificarDadosAntes: true });
            });
            mlWebviewEl.addEventListener('did-finish-load', salvarSessaoNavegadorElectron);
            mlWebviewEl.addEventListener('did-navigate', salvarSessaoNavegadorElectron);
            mlWebviewEl.addEventListener('will-navigate', blockIfExternalProtocol);
            mlWebviewEl.addEventListener('will-frame-navigate', blockIfExternalProtocol);
            mlWebviewEl.addEventListener('new-window', (event) => {
                if (event && event.url) {
                    event.preventDefault();
                    if (isAllowedWebUrl(event.url)) {
                        navegarMlWebview(event.url).catch(() => {});
                    }
                }
            });

            mlBrowserHost.appendChild(mlWebviewEl);
            return mlWebviewEl;
        }

        function navegarMlWebviewUmaTentativa(webview, url, opcoes = {}) {
            const timeoutMs = Number(opcoes.timeoutMs) > 0 ? Number(opcoes.timeoutMs) : 25000;
            const aceitarDidNavigate = !!opcoes.aceitarDidNavigate;
            const exigirUrlAlvo = !!opcoes.exigirUrlAlvo;
            const timeoutMessage = opcoes.timeoutMessage || 'Tempo limite ao abrir a pagina no quadro interno.';
            const urlComparavel = (value) => {
                try {
                    const parsed = new URL(String(value || ''), window.location.href);
                    parsed.hash = '';
                    return parsed.toString();
                } catch (_err) {
                    return String(value || '').replace(/#.*$/, '');
                }
            };
            const urlCorrespondeAoAlvo = (alvo) => {
                if (!exigirUrlAlvo) return true;
                const atual = urlComparavel(alvo || '');
                const esperado = urlComparavel(url || '');
                return !!(atual && esperado && atual === esperado);
            };
            const erroNavegacaoTransitorio = (event) => {
                const codigo = Number(event && event.errorCode);
                const descricao = String((event && event.errorDescription) || event && event.message || '');
                const alvo = String((event && event.url) || (webview && (webview.currentUrl || (typeof webview.getURL === 'function' ? webview.getURL() : ''))) || url || '');
                if (!/mercadolivre\.com\.br|mercadolibre\.com/i.test(alvo)) return false;
                return codigo === -3
                    || /\bERR_ABORTED\b|\(-3\)|falha ao carregar pagina \(-3\)|timeout/i.test(descricao);
            };
            return new Promise((resolve, reject) => {
                let done = false;
                const finish = (err) => {
                    if (done) return;
                    done = true;
                    clearTimeout(timer);
                    webview.removeEventListener('did-finish-load', onLoad);
                    webview.removeEventListener('did-fail-load', onFail);
                    webview.removeEventListener('did-navigate', onNavigate);
                    webview.removeEventListener('did-navigate-in-page', onNavigate);
                    if (err) reject(err);
                    else resolve();
                };
                const onLoad = (event) => {
                    const alvo = event && event.url
                        ? event.url
                        : (webview && (webview.currentUrl || (typeof webview.getURL === 'function' ? webview.getURL() : '')));
                    if (!urlCorrespondeAoAlvo(alvo)) {
                        setBrowserStatus('Aguardando o Mercado Livre abrir a pesquisa correta...');
                        return;
                    }
                    finish();
                };
                const onFail = (event) => {
                    if (erroNavegacaoTransitorio(event)) {
                        setBrowserStatus('Mercado Livre ainda esta carregando no navegador interno...');
                        return;
                    }
                    finish(new Error((event && event.errorDescription) || 'Falha ao carregar pagina.'));
                };
                const onNavigate = (event) => {
                    if (!aceitarDidNavigate) return;
                    const alvo = String((event && event.url) || '').trim();
                    if (!/^https?:\/\//i.test(alvo)) return;
                    if (!urlCorrespondeAoAlvo(alvo)) return;
                    finish();
                };
                const timer = setTimeout(() => finish(new Error(timeoutMessage)), timeoutMs);

                webview.addEventListener('did-finish-load', onLoad);
                webview.addEventListener('did-fail-load', onFail);
                if (aceitarDidNavigate) {
                    webview.addEventListener('did-navigate', onNavigate);
                    webview.addEventListener('did-navigate-in-page', onNavigate);
                }
                webview.src = url;
            });
        }

        function urlPertenceAoMercadoLivre(url) {
            try {
                const host = new URL(String(url || '')).hostname.toLowerCase();
                return host === 'mercadolivre.com.br'
                    || host.endsWith('.mercadolivre.com.br')
                    || host === 'mercadolibre.com'
                    || host.endsWith('.mercadolibre.com');
            } catch (_err) {
                return false;
            }
        }

        function urlNavegadorMlSeguraParaLog(url) {
            try {
                const parsed = new URL(String(url || ''));
                return `${parsed.origin}${parsed.pathname}`;
            } catch (_err) {
                return '';
            }
        }

        async function confirmarAberturaNavegadorMlAposTimeout(urlEsperada, erroOriginal = null) {
            if (!usarNavegadorMlNoShellElectron()) {
                return { confirmado: false, reason: 'shell-indisponivel', url: '' };
            }
            const bridge = favoritosBrowserShellBridge;
            if (!bridge || typeof bridge.verificarEstado !== 'function') {
                return { confirmado: false, reason: 'verificacao-indisponivel', url: '' };
            }
            try {
                const estado = await bridge.verificarEstado(urlEsperada, 2600);
                const urlAtual = String(estado && estado.url || '').trim();
                const confirmado = !!(
                    estado
                    && estado.success !== false
                    && estado.attached !== false
                    && urlPertenceAoMercadoLivre(urlEsperada)
                    && urlPertenceAoMercadoLivre(urlAtual)
                );
                if (!confirmado) {
                    return {
                        confirmado: false,
                        reason: estado && estado.reason || 'url-real-nao-confirmada',
                        url: urlAtual,
                        estado
                    };
                }
                if (mlWebviewEl && mlWebviewEl.__isShellBrowserProxy) {
                    mlWebviewEl.currentUrl = urlAtual;
                    mlWebviewEl.__visible = !navegadorMlEmSegundoPlano();
                }
                if (mlUrlInput && urlAtual) mlUrlInput.value = urlAtual;
                if (!navegadorMlEmSegundoPlano()) agendarAtualizacaoPosicaoNavegadorMlShell();
                console.warn('A confirmacao de carregamento nao chegou, mas o navegador interno esta aberto.', {
                    erro: erroOriginal && erroOriginal.message ? erroOriginal.message : String(erroOriginal || ''),
                    urlEsperada: urlNavegadorMlSeguraParaLog(urlEsperada),
                    urlAtual: urlNavegadorMlSeguraParaLog(urlAtual)
                });
                return { confirmado: true, url: urlAtual, estado };
            } catch (err) {
                return {
                    confirmado: false,
                    reason: err && err.message ? err.message : String(err),
                    url: ''
                };
            }
        }

        async function navegarMlWebview(url) {
            await garantirExtensoesNavegadorMl();
            const webview = criarMlWebview();
            const viaShell = !!(webview && webview.__isShellBrowserProxy);
            if (viaShell) {
                return (async () => {
                    const timeoutShellMs = navegadorMlEmSegundoPlano() ? 18000 : 9000;
                    try {
                        await navegarMlWebviewUmaTentativa(webview, url, {
                            timeoutMs: timeoutShellMs,
                            aceitarDidNavigate: true,
                            exigirUrlAlvo: true,
                            timeoutMessage: 'Tempo limite ao abrir a pagina no quadro interno.'
                        });
                        return;
                    } catch (primeiroErro) {
                        if (mlFavoritosEmExecucao) {
                            setBrowserStatus('Mercado Livre demorou para confirmar a pesquisa no quadro. Seguindo para a coleta sem reenviar a navegacao.');
                            webview.currentUrl = webview.currentUrl || url;
                            await esperar(700);
                            return;
                        }
                        setBrowserStatus('Mercado Livre demorou para abrir no quadro. Tentando novamente...');
                        await esperar(220);
                        agendarAtualizacaoPosicaoNavegadorMlShell();
                        try {
                            await navegarMlWebviewUmaTentativa(webview, url, {
                                timeoutMs: timeoutShellMs,
                                aceitarDidNavigate: true,
                                exigirUrlAlvo: true,
                                timeoutMessage: 'Tempo limite ao abrir a pagina no quadro interno.'
                            });
                        } catch (segundoErro) {
                            webview.currentUrl = url;
                            await esperar(700);
                            throw segundoErro;
                        }
                    }
                })();
            }
            return new Promise((resolve, reject) => {
                let done = false;
                const finish = (err) => {
                    if (done) return;
                    done = true;
                    clearTimeout(timer);
                    webview.removeEventListener('did-finish-load', onLoad);
                    webview.removeEventListener('did-fail-load', onFail);
                    if (err) reject(err);
                    else resolve();
                };
                const onLoad = () => finish();
                const onFail = (event) => finish(new Error((event && event.errorDescription) || 'Falha ao carregar página.'));
                const timer = setTimeout(() => finish(new Error('Tempo limite ao abrir a pagina no quadro interno.')), 25000);

                webview.addEventListener('did-finish-load', onLoad);
                webview.addEventListener('did-fail-load', onFail);
                webview.src = url;
            });
        }

  const api = { criarMlWebview, navegarMlWebviewUmaTentativa, urlPertenceAoMercadoLivre, urlNavegadorMlSeguraParaLog, confirmarAberturaNavegadorMlAposTimeout, navegarMlWebview };
  browser.webviewNavigation = Object.freeze(api);
  Object.assign(global, api);
})(window);
