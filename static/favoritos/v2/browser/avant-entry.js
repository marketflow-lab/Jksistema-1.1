(function (global) {
  'use strict';

  const browser = global.FavoritosV2.browser;
  const pageScripts = browser.pageScripts;

        async function abrirHomeMercadoLivreParaLoginAvantPro(termo = '') {
            const contexto = termo ? ` para "${termo}"` : '';
            const urlHomeMl = typeof ML_DEFAULT_URL !== 'undefined'
                ? ML_DEFAULT_URL
                : 'https://www.mercadolivre.com.br/';
            if (mlUrlInput) mlUrlInput.value = urlHomeMl;
            window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Conectando Avant Pro${contexto}: abrindo Mercado Livre antes do login...`, {
                manterNavegadorVisivel: true,
                larga: true,
                titulo: 'Conectar Avant Pro'
            });
            if (typeof abrirMercadoLivreNoPrograma === 'function') {
                const abriu = await abrirMercadoLivreNoPrograma({
                    titulo: 'Conectar Avant Pro',
                    subtitulo: 'Mercado Livre',
                    browserCompleto: false,
                    forcarExibicao: true
                }).catch(() => false);
                await esperar(700);
                return !!abriu;
            }
            if (typeof navegarMlWebview === 'function') {
                await navegarMlWebview(urlHomeMl).catch(() => null);
                await esperar(700);
                return true;
            }
            return false;
        }

        function montarScriptAcionarControlesAvantPro(opcoes = {}) {
            const forceClick = !!opcoes.forceClick;
            const permitirFerramentas = opcoes.permitirFerramentas !== false;
            const somenteFerramentas = opcoes.somenteFerramentas === true;
            const clicarCardsSemDados = opcoes.clicarCardsSemDados !== false;
            const maxClicks = Math.max(1, Math.min(16, Number(opcoes.maxClicks) || 10));
            return pageScripts.render('montar-script-acionar-controles-avant-pro-1', { p0: (forceClick ? 'true' : 'false'), p1: (permitirFerramentas ? 'true' : 'false'), p2: (somenteFerramentas ? 'true' : 'false'), p3: (clicarCardsSemDados ? 'true' : 'false'), p4: (JSON.stringify(maxClicks)) });
        }

        async function acionarControlesAvantProNoWebview(opcoes = {}) {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') return 0;
            return await mlWebviewEl.executeJavaScript(montarScriptAcionarControlesAvantPro(opcoes), true).catch(() => 0);
        }

        function montarScriptLocalizarBolinhaAvantPro() {
            return pageScripts.render('montar-script-localizar-bolinha-avant-pro-1', {  });
        }

        async function abrirBolinhaAvantProSeNecessario() {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') {
                return { success: false, reason: 'navegador_indisponivel' };
            }
            const alvo = await mlWebviewEl.executeJavaScript(montarScriptLocalizarBolinhaAvantPro(), true).catch((err) => ({
                success: false,
                reason: 'erro_ao_localizar_bolinha_avant',
                error: err && err.message ? err.message : String(err)
            }));
            if (!(alvo && alvo.success)) return alvo || { success: false, reason: 'bolinha_avant_nao_localizada' };
            if (alvo.skipped) return alvo;
            const alvoClique = String(alvo.source || '') === 'bolinha_avant_dom'
                ? {
                    ...alvo,
                    x: Number(alvo.x) + 24,
                    y: Number(alvo.y) + 30,
                    source: 'bolinha_avant_dom_ajuste_borda'
                }
                : alvo;
            const click = await clicarNavegadorMlPorCoordenada(alvoClique).catch((err) => ({
                success: false,
                reason: 'erro_no_clique_real_bolinha_avant',
                error: err && err.message ? err.message : String(err)
            }));
            await esperar(650);
            return {
                success: !!(click && click.success !== false),
                target: alvoClique,
                originalTarget: alvo,
                click
            };
        }

  const api = { abrirHomeMercadoLivreParaLoginAvantPro, montarScriptAcionarControlesAvantPro, acionarControlesAvantProNoWebview, montarScriptLocalizarBolinhaAvantPro, abrirBolinhaAvantProSeNecessario };
  browser.avantEntry = Object.freeze(api);
  Object.assign(global, api);
})(window);
