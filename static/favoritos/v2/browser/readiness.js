(function (global) {
  'use strict';

  const browser = global.FavoritosV2.browser;
  const pageScripts = browser.pageScripts;

        async function aguardarPrimeirosDadosAvantOuCardsWebview(opcoes = {}) {
            const webview = resolverWebviewFavoritosColeta(opcoes);
            if (!webview || typeof webview.executeJavaScript !== 'function') return null;
            if (!monitoramentoPaginaFavoritosAutomaticoAtivo() && !acaoUsuarioFavoritosPermiteLeituraPagina(opcoes)) {
                return statusMonitoramentoPaginaFavoritosDesativado({
                    etapa: 'aguardar_primeiros_dados'
                });
            }
            const timeoutMs = Math.max(800, Number(opcoes.timeoutMs) || 3200);
            const idleMs = Math.max(120, Number(opcoes.idleMs) || 260);
            return await webview.executeJavaScript(pageScripts.render('aguardar-primeiros-dados-avant-ou-cards-webview-1', { p0: (JSON.stringify(timeoutMs)), p1: (JSON.stringify(idleMs)) }), true).catch(() => null);
        }

        async function diagnosticarResultadosMercadoLivreWebview() {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') return null;
            return await mlWebviewEl.executeJavaScript(pageScripts.render('diagnosticar-resultados-mercado-livre-webview-1', {  }), true).catch(() => null);
        }

        async function aguardarResultadosMercadoLivreWebview(opcoes = {}) {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') return null;
            const timeoutMs = Math.max(2500, Number(opcoes.timeoutMs) || 22000);
            const pollMs = Math.max(250, Number(opcoes.pollMs) || 600);
            const reloadAfterMs = Math.max(3000, Number(opcoes.reloadAfterMs) || 9000);
            const inicio = Date.now();
            let recarregou = false;
            let ultimo = null;
            while (Date.now() - inicio < timeoutMs) {
                ultimo = await diagnosticarResultadosMercadoLivreWebview().catch(() => null);
                if (ultimo && (ultimo.hasCards || ultimo.hasAvantData || ultimo.noResults || ultimo.needsLogin)) {
                    return { ...ultimo, elapsedMs: Date.now() - inicio, reloaded: recarregou };
                }
                if (
                    !recarregou
                    && opcoes.recarregarSeTravado !== false
                    && !mlFavoritosEmExecucao
                    && ultimo
                    && ultimo.loadingScreen
                    && Date.now() - inicio >= reloadAfterMs
                ) {
                    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(opcoes.mensagemRecarregando || 'Mercado Livre ainda esta carregando. Recarregando a pesquisa uma vez...', {
                        larga: true,
                        titulo: 'Aguardando Mercado Livre'
                    });
                    recarregou = true;
                    await mlWebviewEl.executeJavaScript(`
                        (function () {
                            if (/^https?:\\/\\//i.test(location.href)) {
                                location.reload();
                                return true;
                            }
                            return false;
                        })();
                    `, true).catch(() => false);
                    await esperar(2800);
                    continue;
                }
                await esperar(pollMs);
            }
            return ultimo ? { ...ultimo, elapsedMs: Date.now() - inicio, reloaded: recarregou, timeout: true } : null;
        }

        async function recarregarNavegadorMlParaAvantPro() {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') return false;
            if (mlFavoritosEmExecucao) return false;
            await garantirExtensoesNavegadorMl().catch(() => []);
            const recarregou = await mlWebviewEl.executeJavaScript(`
                (function () {
                    if (window.__JK_ML_FAVORITOS_EM_EXECUCAO) return false;
                    if (!/^https?:\\/\\//i.test(location.href)) return false;
                    location.reload();
                    return true;
                })();
            `, true).catch(() => false);
            if (!recarregou) return false;
            await esperar(3400);
            tentarLoginAvantProNoWebview(mlWebviewEl);
            return true;
        }

        async function aguardarAvantProNoWebview(opcoes = {}) {
            if (!monitoramentoPaginaFavoritosAutomaticoAtivo() && !acaoUsuarioFavoritosPermiteLeituraPagina(opcoes)) {
                return statusMonitoramentoPaginaFavoritosDesativado({
                    etapa: 'aguardar_avant_pro'
                });
            }
            const timeoutMs = Math.max(800, Number(opcoes.timeoutMs) || 14000);
            const pollMs = Math.max(150, Number(opcoes.pollMs) || 350);
            const inicio = Date.now();
            let ultimo = null;
            let tentouAcionar = false;
            let tentouLoginAvant = false;
            while (Date.now() - inicio < timeoutMs) {
                ultimo = await diagnosticarAvantProNoWebview().catch(() => null);
                if (ultimo && ultimo.modalAvantPromocional) {
                    const fechamentoAvant = await fecharModalBloqueanteAvantProNoWebview().catch(() => null);
                    if (fechamentoAvant && fechamentoAvant.closed) {
                        await esperar(350);
                        continue;
                    }
                }
                if (statusAvantProTemDadosColetaveis(ultimo)) {
                    return {
                        ...normalizarStatusAvantProPronto(ultimo),
                        elapsedMs: Date.now() - inicio
                    };
                }
                const cardsComExtensao = !!(ultimo && Number(ultimo.cardCount || 0) > 0 && (
                    ultimo.extensionDetected
                    || Number(ultimo.widgets || 0) > 0
                    || Number(ultimo.actionButtons || 0) > 0
                    || Number(ultimo.toolsButtons || 0) > 0
                ));
                if (statusAvantProPedeLoginOuVinculo(ultimo)) {
                    const fechamentoAvant = await fecharModalBloqueanteAvantProNoWebview().catch(() => null);
                    if (fechamentoAvant && fechamentoAvant.closed) {
                        await esperar(450);
                        continue;
                    }
                    mostrarAcaoConectarAvantPro(ultimo);
                    return {
                        ...ultimo,
                        ok: false,
                        accountActionRequired: true,
                        message: 'Avant Pro nao retornou dados coletaveis. Confirme manualmente o login no navegador interno.'
                    };
                }
                if (!tentouAcionar && ultimo && !statusAvantProPedeLoginOuVinculo(ultimo) && (ultimo.infoButtons > 0 || ultimo.toolsButtons > 0 || ultimo.shellOnly)) {
                    tentouAcionar = true;
                    const clicados = await acionarControlesAvantProNoWebview({
                        forceClick: true,
                        permitirFerramentas: true,
                        maxClicks: 10
                    }).catch(() => 0);
                    if (clicados) await esperar(900);
                }
                if (tentouAcionar && !statusAvantProPedeLoginOuVinculo(ultimo) && statusAvantProShellSemDados(ultimo)) {
                    return {
                        ...normalizarStatusAvantProCarregadoParaColeta(ultimo),
                        elapsedMs: Date.now() - inicio
                    };
                }
                if (tentouAcionar && !tentouLoginAvant && opcoes.autoLoginAvant !== false && statusAvantProShellSemDados(ultimo) && !cardsComExtensao) {
                    tentouLoginAvant = true;
                    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('Avant Pro apareceu sem dados. Tentando abrir login automaticamente...', {
                        larga: true,
                        titulo: 'Conectar Avant Pro'
                    });
                    const resultadoLogin = await abrirLoginAvantProNoWebview().catch(() => null);
                    await esperar((resultadoLogin && resultadoLogin.success) ? 650 : 420);
                    continue;
                }
                await esperar(pollMs);
            }

            const paginaPronta = await aguardarPrimeirosDadosAvantOuCardsWebview({
                timeoutMs: 650,
                idleMs: 120,
                acaoUsuario: acaoUsuarioFavoritosPermiteLeituraPagina(opcoes)
            }).catch(() => null);
            if (paginaPronta && paginaPronta.hasAvantData) {
                return {
                    ...(ultimo || {}),
                    ok: true,
                    hasCards: !!paginaPronta.hasCards,
                    hasAvantData: true,
                    cardCount: paginaPronta.cardCount || 0,
                    elapsedMs: Date.now() - inicio
                };
            }

            if (opcoes.recarregarSeAusente && mlFavoritosEmExecucao) {
                return ultimo ? {
                    ...ultimo,
                    ok: false,
                    reloadBlockedDuringFavoritos: true
                } : {
                    ok: false,
                    unavailable: true,
                    reloadBlockedDuringFavoritos: true
                };
            }

            if (opcoes.recarregarSeAusente && !(ultimo && ultimo.needsAccountLink)) {
                window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(opcoes.mensagemRecarregando || 'Avant Pro nao carregou de primeira. Recarregando Mercado Livre...');
                const recarregou = await recarregarNavegadorMlParaAvantPro().catch(() => false);
                if (recarregou) {
                    const depoisReload = await aguardarAvantProNoWebview({
                        ...opcoes,
                        recarregarSeAusente: false,
                        timeoutMs: Number(opcoes.timeoutAposReloadMs) || timeoutMs
                    }).catch(() => null);
                    if (depoisReload) return { ...depoisReload, reloaded: true };
                }
            }

            return ultimo ? { ...ultimo, ok: false } : { ok: false, unavailable: true };
        }

        async function recarregarNavegadorMlAposLoginAvantProFavoritos(status = {}, opcoes = {}) {
            if (!monitoramentoPaginaFavoritosAutomaticoAtivo() && !acaoUsuarioFavoritosPermiteLeituraPagina(opcoes)) {
                return {
                    ...(status || {}),
                    ...statusMonitoramentoPaginaFavoritosDesativado({
                        etapa: 'retomada_pos_login_avant'
                    })
                };
            }
            const termo = String(opcoes.termo || '').trim();
            const statusInicial = status || {};
            const motivoPendente = 'login_ou_vinculo_ainda_pendente';
            const chavesRetomada = ML_FAVORITOS_AVANT_RELOAD_APOS_LOGIN_KEYS;
            if (statusAvantProPedeLoginOuVinculo(statusInicial)) {
                return {
                    ...statusInicial,
                    ok: false,
                    accountActionRequired: true,
                    reason: motivoPendente
                };
            }
            chavesRetomada.forEach((key, index) => {
                try {
                    localStorage.setItem(key, index === 0 ? '1' : String(Date.now()));
                } catch (_err) {}
            });
            if (termo) {
                window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Avant Pro liberado para "${termo}". Retomando favoritos sem atualizar a pagina...`, {
                    larga: true,
                    titulo: 'Retomando Favoritos'
                });
            }
            await acionarControlesAvantProNoWebview({
                forceClick: true,
                permitirFerramentas: true,
                maxClicks: 12
            }).catch(() => 0);
            const aguardado = await aguardarAvantProNoWebview({
                recarregarSeAusente: false,
                autoLoginAvant: false,
                timeoutMs: Number(opcoes.timeoutMs) || 6500,
                pollMs: Number(opcoes.pollMs) || 350,
                acaoUsuario: acaoUsuarioFavoritosPermiteLeituraPagina(opcoes)
            }).catch(() => null);
            let finalStatus = aguardado || await diagnosticarAvantProNoWebview().catch(() => statusInicial);
            if (statusAvantProTemDadosColetaveis(finalStatus)) {
                finalStatus = normalizarStatusAvantProPronto(finalStatus);
            } else if (!statusAvantProPedeLoginOuVinculo(finalStatus) && statusAvantProShellSemDados(finalStatus)) {
                finalStatus = normalizarStatusAvantProCarregadoParaColeta(finalStatus);
            }
            return {
                ...(finalStatus || {}),
                resumedAfterAvantLogin: true,
                reloadedAfterAvantLogin: false,
                reason: 'retomada_pos_login_sem_reload'
            };
        }

  const api = { aguardarPrimeirosDadosAvantOuCardsWebview, diagnosticarResultadosMercadoLivreWebview, aguardarResultadosMercadoLivreWebview, recarregarNavegadorMlParaAvantPro, aguardarAvantProNoWebview, recarregarNavegadorMlAposLoginAvantProFavoritos };
  browser.readiness = Object.freeze(api);
  Object.assign(global, api);
})(window);
