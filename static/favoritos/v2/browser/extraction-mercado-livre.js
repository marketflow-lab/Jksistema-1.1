(function (global) {
  'use strict';

  const browser = global.FavoritosV2.browser;
  const pageScripts = browser.pageScripts;

        async function extrairCardsMercadoLivreBasicoWebview(opcoes = {}) {
            const webview = resolverWebviewFavoritosColeta(opcoes);
            if (!webview || typeof webview.executeJavaScript !== 'function') return { success: false, total: 0, anuncios: [] };
            const limite = Math.max(1, Math.min(Number(opcoes.limite) || 100, 160));
            return await webview.executeJavaScript(pageScripts.render('extrair-cards-mercado-livre-basico-webview-1', { p0: (JSON.stringify(limite)) }), true).then((resultado) => {
                const anuncios = Array.isArray(resultado && resultado.anuncios)
                    ? resultado.anuncios.map(item => prepararAnuncioMercadoLivreCanonico(item, 'mercado_livre_dom')).filter(item => item.chave_canonica)
                    : [];
                return {
                    ...(resultado || {}),
                    success: true,
                    total: anuncios.length,
                    anuncios
                };
            }).catch((err) => ({
                success: false,
                total: 0,
                anuncios: [],
                error: err && err.message ? err.message : String(err)
            }));
        }

        async function extrairBaseMercadoLivreEmergencialWebview(opcoes = {}) {
            const webview = resolverWebviewFavoritosColeta(opcoes);
            if (!webview || typeof webview.executeJavaScript !== 'function') {
                return { success: false, total: 0, anuncios: [], error: 'webview_indisponivel' };
            }
            const limite = Math.max(1, Math.min(Number(opcoes.limite) || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80, 160));
            let resultado = null;
            try {
                resultado = opcoes.webview
                    ? await extrairCardsMercadoLivreBasicoWebview({ limite, webview })
                    : await extrairAnunciosWebviewVisivel({
                        clicarAvant: false,
                        permitirFerramentasAvant: false,
                        permitirAutoLoginAvant: false,
                        clicarCardsSemDados: false,
                        ignorarLoginAvant: true,
                        fastLinks: false,
                        maxAnuncios: limite,
                        maxFastDom: limite,
                        timeoutMs: Number(opcoes.timeoutMs) || 9000
                    });
            } catch (err) {
                resultado = {
                    success: false,
                    total: 0,
                    anuncios: [],
                    error: err && err.message ? err.message : String(err)
                };
            }
            let anuncios = Array.isArray(resultado && resultado.anuncios)
                ? resultado.anuncios.map(item => prepararAnuncioMercadoLivreCanonico(item, 'mercado_livre_dom_emergencial')).filter(item => item.chave_canonica)
                : [];
            if (!anuncios.length) {
                const rapido = await extrairAnunciosWebviewFastDom({
                    maxFastDom: limite,
                    webview
                }).catch(() => null);
                anuncios = Array.isArray(rapido && rapido.anuncios)
                    ? rapido.anuncios.map(item => prepararAnuncioMercadoLivreCanonico(item, 'mercado_livre_dom_emergencial_links')).filter(item => item.chave_canonica)
                    : [];
                if (anuncios.length) {
                    resultado = {
                        ...(resultado || {}),
                        debug: {
                            ...(resultado && resultado.debug || {}),
                            emergenciaFastDom: rapido && rapido.debug || null
                        }
                    };
                }
            }
            return {
                ...(resultado || {}),
                success: anuncios.length > 0 || !!(resultado && resultado.success),
                total: anuncios.length,
                anuncios,
                debug: {
                    ...(resultado && resultado.debug || {}),
                    mode: 'mercado_livre_dom_emergencial',
                    emergenciaTotal: anuncios.length
                }
            };
        }

        async function aguardarBaseMercadoLivreColetavelFavoritos(opcoes = {}) {
            const webview = resolverWebviewFavoritosColeta(opcoes);
            const limite = Math.max(1, Math.min(Number(opcoes.limite) || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80, 160));
            const timeoutMs = Math.max(4000, Math.min(Number(opcoes.timeoutMs) || 28000, 45000));
            const pollMs = Math.max(250, Math.min(Number(opcoes.pollMs) || 650, 1500));
            const onProgress = opcoes.onProgress;
            const inicio = Date.now();
            const deadline = inicio + timeoutMs;
            let ultimoBasico = null;
            let ultimoStatus = null;

            while (Date.now() < deadline) {
                const restante = Math.max(0, deadline - Date.now());
                ultimoBasico = await extrairCardsMercadoLivreBasicoWebview({ limite, webview }).catch(() => null);
                const anuncios = Array.isArray(ultimoBasico && ultimoBasico.anuncios) ? ultimoBasico.anuncios : [];
                if (anuncios.length) {
                    return {
                        ...(ultimoBasico || {}),
                        success: true,
                        ready: true,
                        total: anuncios.length,
                        anuncios,
                        status: ultimoStatus,
                        elapsedMs: Date.now() - inicio
                    };
                }

                if (typeof aguardarPrimeirosDadosAvantOuCardsWebview === 'function' && restante > 250) {
                    ultimoStatus = await aguardarPrimeirosDadosAvantOuCardsWebview({
                        timeoutMs: Math.min(2600, restante),
                        idleMs: 160,
                        acaoUsuario: true,
                        solicitadoPeloUsuario: true,
                        webview
                    }).catch(() => null);
                } else {
                    await esperar(Math.min(420, restante));
                }

                const statusVisiveis = Math.min(limite, Math.max(
                    Number(ultimoStatus && ultimoStatus.cardCount) || 0,
                    Number(ultimoStatus && ultimoStatus.productLinkCount) || 0
                ));
                const paginaProntaSemBase = !!(
                    ultimoStatus
                    && !ultimoStatus.loadingScreen
                    && !ultimoStatus.needsLogin
                    && !ultimoStatus.noResults
                    && (
                        statusVisiveis > 0
                        || ultimoStatus.hasAvantData
                        || Number(ultimoStatus.avantLabels || 0) >= 2
                    )
                );
                if (paginaProntaSemBase) {
                    const emergencia = await extrairBaseMercadoLivreEmergencialWebview({
                        limite,
                        timeoutMs: Math.min(5000, Math.max(1200, deadline - Date.now())),
                        webview
                    }).catch(() => null);
                    const emergenciaAnuncios = Array.isArray(emergencia && emergencia.anuncios) ? emergencia.anuncios : [];
                    if (emergenciaAnuncios.length) {
                        return {
                            ...(emergencia || {}),
                            success: true,
                            ready: true,
                            total: emergenciaAnuncios.length,
                            anuncios: emergenciaAnuncios,
                            status: ultimoStatus,
                            elapsedMs: Date.now() - inicio
                        };
                    }
                }

                emitirProgressoPrimeiraPaginaFavoritos(onProgress, {
                    etapa: 'aguardando_cards',
                    visiveis: statusVisiveis,
                    coletados: 0,
                    com_titulo: 0,
                    com_foto: 0,
                    com_preco: 0,
                    com_link: 0,
                    com_dados_avant: 0,
                    suspeitos: 0,
                    tempoRestanteMs: Math.max(0, deadline - Date.now()),
                    loadingScreen: !!(ultimoStatus && ultimoStatus.loadingScreen),
                    noResults: !!(ultimoStatus && ultimoStatus.noResults),
                    needsLogin: !!(ultimoStatus && ultimoStatus.needsLogin),
                    url: ultimoStatus && ultimoStatus.url || '',
                    diagnostico: {
                        cardCount: Number(ultimoStatus && ultimoStatus.cardCount) || 0,
                        productLinkCount: Number(ultimoStatus && ultimoStatus.productLinkCount) || 0,
                        avantLabels: Number(ultimoStatus && ultimoStatus.avantLabels) || 0,
                        basicoCardCount: Number(ultimoBasico && ultimoBasico.debug && ultimoBasico.debug.cardCount) || 0,
                        basicoProductLinkCount: Number(ultimoBasico && ultimoBasico.debug && ultimoBasico.debug.productLinkCount) || 0
                    }
                });

                if (ultimoStatus && (ultimoStatus.noResults || ultimoStatus.needsLogin)) {
                    break;
                }
                await esperar(Math.min(pollMs, Math.max(0, deadline - Date.now())));
            }

            return {
                success: false,
                ready: false,
                timeout: !(ultimoStatus && (ultimoStatus.noResults || ultimoStatus.needsLogin)),
                noResults: !!(ultimoStatus && ultimoStatus.noResults),
                needsLogin: !!(ultimoStatus && ultimoStatus.needsLogin),
                total: 0,
                anuncios: [],
                status: ultimoStatus,
                debug: ultimoBasico && ultimoBasico.debug || null,
                elapsedMs: Date.now() - inicio
            };
        }

  const api = { extrairCardsMercadoLivreBasicoWebview, extrairBaseMercadoLivreEmergencialWebview, aguardarBaseMercadoLivreColetavelFavoritos };
  browser.extractionMercadoLivre = Object.freeze(api);
  Object.assign(global, api);
})(window);
