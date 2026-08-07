(function (global) {
  'use strict';

  const browser = global.FavoritosV2.browser;
  const pageScripts = browser.pageScripts;

        function montarScriptLocalizarFerramentasAvantPro() {
            return pageScripts.render('montar-script-localizar-ferramentas-avant-pro-1', {  });
        }

        async function clicarFerramentasAvantProPorCoordenada() {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') {
                return { success: false, reason: 'navegador_indisponivel' };
            }
            const alvo = await mlWebviewEl.executeJavaScript(montarScriptLocalizarFerramentasAvantPro(), true).catch((err) => ({
                success: false,
                reason: 'erro_ao_localizar_ferramentas_avant',
                error: err && err.message ? err.message : String(err)
            }));
            if (!(alvo && alvo.success)) return alvo || { success: false, reason: 'ferramentas_avant_nao_localizado' };
            const alvosClique = [alvo];
            if (/estimado|rotulo|dom/i.test(String(alvo.source || ''))) {
                const baseX = Number(alvo.x);
                const baseY = Number(alvo.y);
                if (Number.isFinite(baseX) && Number.isFinite(baseY)) {
                    [-45, 45, -75].forEach((dx) => {
                        alvosClique.push({
                            ...alvo,
                            x: Math.max(20, baseX + dx),
                            y: baseY,
                            source: `${alvo.source}_ajuste_${dx}`
                        });
                    });
                    [60, 95, 130].forEach((dy) => {
                        [0, -45, 45].forEach((dx) => {
                            alvosClique.push({
                                ...alvo,
                                x: Math.max(20, baseX + dx),
                                y: Math.max(20, baseY + dy),
                                source: `${alvo.source}_ajuste_${dx}_${dy}`
                            });
                        });
                    });
                }
            }
            const cliques = [];
            let click = null;
            for (let i = 0; i < alvosClique.length; i += 1) {
                const alvoClique = alvosClique[i];
                click = await clicarNavegadorMlPorCoordenada(alvoClique).catch((err) => ({
                    success: false,
                    reason: 'erro_no_clique_real_ferramentas',
                    error: err && err.message ? err.message : String(err)
                }));
                cliques.push({ target: alvoClique, click });
                if (!(click && click.success !== false)) continue;
                await esperar(i === 0 ? 360 : 520);
                const entrada = await diagnosticarEntradaAvantProNoWebview().catch(() => null);
                cliques[cliques.length - 1].entrada = entrada;
                if (entrada && entrada.prontoParaLogin) break;
            }
            return {
                success: !!(click && click.success !== false),
                target: alvo,
                click,
                cliques
            };
        }

        function montarScriptLocalizarVincularContaAvantPro() {
            return pageScripts.render('montar-script-localizar-vincular-conta-avant-pro-1', {  });
        }

        async function clicarVincularContaAvantProPorCoordenada() {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') {
                return { success: false, reason: 'navegador_indisponivel' };
            }
            const alvo = await mlWebviewEl.executeJavaScript(montarScriptLocalizarVincularContaAvantPro(), true).catch((err) => ({
                success: false,
                reason: 'erro_ao_localizar_vincular_conta_avant',
                error: err && err.message ? err.message : String(err)
            }));
            if (!(alvo && alvo.success)) return alvo || { success: false, reason: 'vincular_conta_avant_nao_localizado' };
            const click = await clicarNavegadorMlPorCoordenada(alvo).catch((err) => ({
                success: false,
                reason: 'erro_no_clique_real_vincular_conta_avant',
                error: err && err.message ? err.message : String(err)
            }));
            return {
                success: !!(click && click.success !== false),
                target: alvo,
                click
            };
        }

        async function diagnosticarEntradaAvantProNoWebview() {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') {
                return { ok: false, reason: 'navegador_indisponivel' };
            }
            return await mlWebviewEl.executeJavaScript(pageScripts.render('diagnosticar-entrada-avant-pro-no-webview-1', {  }), true).catch((err) => ({
                ok: false,
                reason: 'erro_ao_diagnosticar_entrada_avant',
                error: err && err.message ? err.message : String(err)
            }));
        }

        async function aguardarEntradaAvantProAposFerramentas(opcoes = {}) {
            const timeoutMs = Math.max(800, Number(opcoes.timeoutMs) || 2600);
            const pollMs = Math.max(160, Number(opcoes.pollMs) || 320);
            const inicio = Date.now();
            let ultimo = null;
            while (Date.now() - inicio < timeoutMs) {
                ultimo = await diagnosticarEntradaAvantProNoWebview().catch(() => null);
                if (ultimo && ultimo.prontoParaLogin) {
                    return { ...ultimo, ok: true, elapsedMs: Date.now() - inicio };
                }
                await esperar(pollMs);
            }
            return ultimo ? { ...ultimo, ok: false, timeout: true } : { ok: false, timeout: true };
        }

        function mostrarStatusConexaoAvantPro(etapa, opcoes = {}) {
            const termo = String(opcoes.termo || '').trim();
            const contexto = termo ? ` para "${termo}"` : '';
            const tentativa = Number(opcoes.tentativa || 0);
            const sufixoTentativa = tentativa > 0 ? ` (tentativa ${tentativa})` : '';
            window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Conectando Avant Pro${contexto}: ${etapa}${sufixoTentativa}...`, {
                manterNavegadorVisivel: true,
                larga: true,
                titulo: 'Conectar Avant Pro'
            });
        }

        async function abrirFerramentasAvantProAntesLogin(opcoes = {}) {
            const maxTentativas = Math.max(1, Math.min(4, Number(opcoes.maxTentativas) || 3));
            const termo = String(opcoes.termo || '').trim();
            const permitirLoginGenerico = opcoes.permitirLoginGenerico !== false;
            const tentativas = [];
            for (let tentativa = 0; tentativa < maxTentativas; tentativa += 1) {
                mostrarStatusConexaoAvantPro('clicando na bolinha do Avant Pro', {
                    termo,
                    tentativa: tentativa + 1
                });
                const bolinha = await abrirBolinhaAvantProSeNecessario().catch((err) => ({
                    success: false,
                    reason: 'erro_ao_abrir_bolinha_avant',
                    error: err && err.message ? err.message : String(err)
                }));
                tentativas.push({ via: 'bolinha', tentativa: tentativa + 1, result: bolinha });
                await esperar(tentativa === 0 ? 420 : 560);
                mostrarStatusConexaoAvantPro('clicando em Ferramentas', {
                    termo,
                    tentativa: tentativa + 1
                });
                const clicksDom = await acionarControlesAvantProNoWebview({
                    forceClick: true,
                    permitirFerramentas: true,
                    somenteFerramentas: true,
                    maxClicks: 2
                }).catch(() => 0);
                tentativas.push({ via: 'dom', tentativa: tentativa + 1, clicks: clicksDom || 0 });
                await esperar(tentativa === 0 ? 520 : 760);
                const entradaDom = await aguardarEntradaAvantProAposFerramentas({
                    timeoutMs: tentativa === 0 ? 1400 : 1900,
                    pollMs: 260
                }).catch(() => null);
                tentativas.push({ via: 'diagnostico_entrada_dom', tentativa: tentativa + 1, result: entradaDom });
                if (entradaDom && entradaDom.prontoParaLogin) {
                    mostrarStatusConexaoAvantPro('Ferramentas abriu; procurando o campo de e-mail', { termo });
                    return { success: true, tentativas, entrada: entradaDom };
                }
                if (entradaDom && entradaDom.ferramentasPainelAberto) {
                    mostrarStatusConexaoAvantPro('Ferramentas abriu, mas o e-mail ainda nao apareceu', { termo });
                    return {
                        success: false,
                        reason: entradaDom.reason || 'avantpro_ferramentas_abriu_sem_email',
                        tentativas,
                        entrada: entradaDom
                    };
                }
                const clickFerramentas = await clicarFerramentasAvantProPorCoordenada().catch((err) => ({
                    success: false,
                    reason: 'erro_no_clique_ferramentas',
                    error: err && err.message ? err.message : String(err)
                }));
                tentativas.push({ via: 'coordenada', tentativa: tentativa + 1, result: clickFerramentas });
                if (clickFerramentas && clickFerramentas.success) {
                    const entrada = await aguardarEntradaAvantProAposFerramentas({
                        timeoutMs: tentativa === 0 ? 2200 : 3200,
                        pollMs: 280
                    }).catch(() => null);
                    tentativas.push({ via: 'diagnostico_entrada', tentativa: tentativa + 1, result: entrada });
                    if (entrada && entrada.prontoParaLogin) {
                        mostrarStatusConexaoAvantPro('Ferramentas abriu; procurando o campo de e-mail', { termo });
                        return { success: true, tentativas, entrada };
                    }
                    if (entrada && entrada.ferramentasPainelAberto) {
                        mostrarStatusConexaoAvantPro('Ferramentas abriu, mas o e-mail ainda nao apareceu', { termo });
                        return {
                            success: false,
                            reason: entrada.reason || 'avantpro_ferramentas_abriu_sem_email',
                            tentativas,
                            entrada
                        };
                    }
                    if (permitirLoginGenerico && typeof tentarLoginAvantProNoWebview === 'function') {
                        const tentativaLogin = await tentarLoginAvantProNoWebview(mlWebviewEl, {
                            timeoutMs: 2600,
                            atrasos: [0, 200, 520, 1000, 1700, 2400]
                        }).catch(() => null);
                        tentativas.push({ via: 'autologin_apos_ferramentas', tentativa: tentativa + 1, result: tentativaLogin });
                        const entradaDepoisLogin = await aguardarEntradaAvantProAposFerramentas({
                            timeoutMs: (tentativaLogin && tentativaLogin.clickedLoginButton) ? 2600 : 1200,
                            pollMs: 240
                        }).catch(() => null);
                        tentativas.push({ via: 'diagnostico_entrada_pos_autologin', tentativa: tentativa + 1, result: entradaDepoisLogin });
                        if (
                            (entradaDepoisLogin && entradaDepoisLogin.prontoParaLogin)
                            || (tentativaLogin && tentativaLogin.success && (
                                tentativaLogin.emailVisible
                                || tentativaLogin.reason === 'campo_email_avant_visivel'
                            ))
                        ) {
                            return { success: true, tentativas, entrada: entradaDepoisLogin || entrada };
                        }
                    }
                }
                await esperar(360);
            }
            return { success: false, tentativas };
        }

  const api = { montarScriptLocalizarFerramentasAvantPro, clicarFerramentasAvantProPorCoordenada, montarScriptLocalizarVincularContaAvantPro, clicarVincularContaAvantProPorCoordenada, diagnosticarEntradaAvantProNoWebview, aguardarEntradaAvantProAposFerramentas, mostrarStatusConexaoAvantPro, abrirFerramentasAvantProAntesLogin };
  browser.avantControls = Object.freeze(api);
  Object.assign(global, api);
})(window);
