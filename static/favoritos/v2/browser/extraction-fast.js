(function (global) {
  'use strict';

  const browser = global.FavoritosV2.browser;
  const pageScripts = browser.pageScripts;

        async function extrairAnunciosWebviewVisivel(opcoes = {}) {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') return null;
            if (opcoes.clicarAvant) {
                const ignorarLoginAvant = opcoes.ignorarLoginAvant === true;
                let statusAvant = await diagnosticarAvantProNoWebview().catch(() => null);
                const fechamentoAvant = await fecharModalBloqueanteAvantProNoWebview().catch(() => null);
                if (fechamentoAvant && fechamentoAvant.closed) {
                    await esperar(360);
                    statusAvant = await diagnosticarAvantProNoWebview().catch(() => statusAvant);
                }
                const temDadosAvant = statusAvantProTemDadosColetaveis(statusAvant);
                const deveAcionarAvant = !temDadosAvant;
                if (deveAcionarAvant) {
                    const loginAvantNecessario = !!(statusAvant && (
                        statusAvant.needsAccountLink
                        || statusAvant.accountActionRequired
                        || statusAvant.avantLoginDialog
                        || statusAvant.avantLoginEmailInputs > 0
                    ));
                    let autoLogin = null;
                    if (loginAvantNecessario) {
                        if (statusAvant && statusAvant.modalAvantPromocional) {
                            await fecharModalBloqueanteAvantProNoWebview().catch(() => null);
                        } else if (!ignorarLoginAvant) {
                            const resultadoLogin = await abrirLoginAvantProNoWebview().catch(() => null);
                            autoLogin = resultadoLogin && resultadoLogin.autoLogin;
                        }
                    } else {
                        await acionarControlesAvantProNoWebview({
                            forceClick: false,
                            permitirFerramentas: opcoes.permitirFerramentasAvant !== false,
                            clicarCardsSemDados: opcoes.clicarCardsSemDados !== false,
                            maxClicks: opcoes.maxCliquesAvant || 12
                        }).catch(() => 0);
                        await fecharModalBloqueanteAvantProNoWebview().catch(() => null);
                        if (opcoes.permitirAutoLoginAvant !== false) {
                            autoLogin = await tentarLoginAvantProNoWebview(mlWebviewEl).catch(() => null);
                        }
                    }
                    const esperaClique = Number(opcoes.aguardarAposCliqueAvant);
                    const esperaPadrao = autoLogin && autoLogin.success ? 360 : 260;
                    await esperar(Number.isFinite(esperaClique) ? Math.max(esperaPadrao, esperaClique) : esperaPadrao);
                }
                if (opcoes.aguardarEstabilidadeAvant && (deveAcionarAvant || temDadosAvant)) {
                    const estabilidade = opcoes.aguardarEstabilidadeAvant === true ? {} : opcoes.aguardarEstabilidadeAvant;
                    await aguardarDadosAvantProEstaveisWebview(estabilidade).catch(() => null);
                }
                await fecharModalBloqueanteAvantProNoWebview().catch(() => null);
            }
            await fecharModalBloqueanteAvantProNoWebview().catch(() => null);
            await mlWebviewEl.executeJavaScript(`window.__JK_ML_FAST_LINKS = ${opcoes.fastLinks ? 'true' : 'false'};`, true).catch(() => null);
            const maxFastDom = Math.max(20, Math.min(Number(opcoes.maxFastDom) || Number(opcoes.maxAnuncios) || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80, 100));
            await mlWebviewEl.executeJavaScript(`window.__JK_ML_FAST_DOM_MAX = ${JSON.stringify(maxFastDom)};`, true).catch(() => null);
            const resultadoCompleto = await promiseComTimeout(
                mlWebviewEl.executeJavaScript(ML_WEBVIEW_EXTRACT_SCRIPT, true),
                opcoes.timeoutMs || 6000,
                'Tempo limite ao extrair os links do quadro interno.'
            );
            if (!resultadoCompleto || opcoes.fastLinks === true) return resultadoCompleto;
            const avantDom = await extrairAnunciosAvantProDomWebview({ limite: maxFastDom }).catch(() => null);
            if (avantDom && Array.isArray(avantDom.anuncios) && avantDom.anuncios.length) {
                resultadoCompleto.anuncios = mesclarAnunciosAvant(
                    Array.isArray(resultadoCompleto.anuncios) ? resultadoCompleto.anuncios : [],
                    avantDom.anuncios
                );
                resultadoCompleto.debug = {
                    ...(resultadoCompleto.debug || {}),
                    avantDom: avantDom.debug || null,
                    mode: 'complete_avant_dom_merged'
                };
            }
            const anunciosCompletos = Array.isArray(resultadoCompleto.anuncios) ? resultadoCompleto.anuncios : [];
            const totalComVendas = anunciosCompletos.filter(item => {
                const vendas = Number(item && item.vendas);
                return Number.isFinite(vendas) && vendas >= 0;
            }).length;
            if (anunciosCompletos.length && totalComVendas === 0) {
                console.info('Extracao completa sem vendas Avant; tentando leitura rapida dos cards visiveis');
                const rapido = await extrairAnunciosWebviewFastDom({ maxFastDom }).catch(() => null);
                if (rapido && Array.isArray(rapido.anuncios) && rapido.anuncios.length) {
                    return {
                        ...resultadoCompleto,
                        anuncios: mesclarAnunciosAvant(resultadoCompleto.anuncios, rapido.anuncios),
                        debug: { ...(resultadoCompleto.debug || {}), fastDom: rapido.debug || null, mode: 'complete_fast_dom_merged' }
                    };
                }
            } else if (anunciosCompletos.length && totalComVendas < Math.min(anunciosCompletos.length, Math.ceil(maxFastDom * 0.7))) {
                console.info('Extracao completa parcial; tentando leitura rapida dos cards visiveis');
                const rapido = await extrairAnunciosWebviewFastDom({ maxFastDom }).catch(() => null);
                if (rapido && Array.isArray(rapido.anuncios) && rapido.anuncios.length) {
                    return {
                        ...resultadoCompleto,
                        anuncios: mesclarAnunciosAvant(resultadoCompleto.anuncios, rapido.anuncios),
                        debug: { ...(resultadoCompleto.debug || {}), fastDom: rapido.debug || null, mode: 'complete_fast_dom_merged' }
                    };
                }
            }
            return resultadoCompleto;
        }

        function resolverWebviewFavoritosColeta(opcoes = {}) {
            const informado = opcoes && typeof opcoes === 'object' ? opcoes.webview : null;
            return informado || mlWebviewEl || null;
        }

        async function extrairAnunciosWebviewFastDom(opcoes = {}) {
            const webview = resolverWebviewFavoritosColeta(opcoes);
            if (!webview || typeof webview.executeJavaScript !== 'function') return { success: false, total: 0, anuncios: [] };
            const maxFastDom = Math.max(20, Math.min(Number(opcoes.maxFastDom) || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80, 100));
            return await webview.executeJavaScript(pageScripts.render('extrair-anuncios-webview-fast-dom-1', { p0: (JSON.stringify(maxFastDom)), p1: (JSON.stringify(maxFastDom)) }), true).catch((err) => ({
                success: false,
                total: 0,
                anuncios: [],
                error: err && err.message ? err.message : String(err)
            }));
        }

  const api = { extrairAnunciosWebviewVisivel, resolverWebviewFavoritosColeta, extrairAnunciosWebviewFastDom };
  browser.extractionFast = Object.freeze(api);
  Object.assign(global, api);
})(window);
