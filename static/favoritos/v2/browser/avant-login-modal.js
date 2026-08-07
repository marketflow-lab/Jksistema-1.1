(function (global) {
  'use strict';

  const browser = global.FavoritosV2.browser;
  const pageScripts = browser.pageScripts;

        async function clicarLoginAvantProPorCoordenada() {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') {
                return { success: false, reason: 'navegador_indisponivel' };
            }
            const alvo = await mlWebviewEl.executeJavaScript(montarScriptLocalizarLoginAvantPro(), true).catch((err) => ({
                success: false,
                reason: 'erro_ao_localizar_login_avant',
                error: err && err.message ? err.message : String(err)
            }));
            if (!(alvo && alvo.success)) return alvo || { success: false, reason: 'login_avant_nao_localizado' };
            const click = await clicarNavegadorMlPorCoordenada(alvo).catch((err) => ({
                success: false,
                reason: 'erro_no_clique_real',
                error: err && err.message ? err.message : String(err)
            }));
            return {
                success: !!(click && click.success !== false),
                target: alvo,
                click
            };
        }

        async function fecharModalBloqueanteAvantProNoWebview(opcoes = {}) {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') {
                return { success: false, closed: false, reason: 'navegador_indisponivel' };
            }
            const fecharLoginReal = opcoes.fecharLoginReal === true;
            return await mlWebviewEl.executeJavaScript(pageScripts.render('fechar-modal-bloqueante-avant-pro-no-webview-1', { p0: (fecharLoginReal ? 'true' : 'false') }), true).catch((err) => ({
                success: false,
                closed: false,
                reason: 'erro_ao_fechar_modal_avant',
                error: err && err.message ? err.message : String(err)
            }));
        }

        async function abrirLoginAvantProNoWebview() {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') {
                return { success: false, reason: 'navegador_indisponivel' };
            }
            if (typeof autorizarLoginAvantProTemporario === 'function') {
                autorizarLoginAvantProTemporario(180000);
            }
            await mlWebviewEl.executeJavaScript(`
                (function () {
                    window.__JK_AVANT_PRO_LOGIN_CLICKED_AT = Date.now();
                    return true;
                })();
            `, true).catch(() => false);
            await fecharModalBloqueanteAvantProNoWebview({ fecharLoginReal: false }).catch(() => null);
            const resultado = await mlWebviewEl.executeJavaScript(pageScripts.render('abrir-login-avant-pro-no-webview-1', {  }), true).catch((err) => ({
                success: false,
                reason: 'erro_ao_clicar_login_avant',
                error: err && err.message ? err.message : String(err)
            }));
            let autoLogin = await promiseComTimeout(
                tentarLoginAvantProNoWebview(mlWebviewEl, {
                    timeoutMs: 3200,
                    atrasos: [0, 140, 320, 650, 1100, 1800, 2800]
                }),
                3600,
                'Aguardando abertura do login Avant Pro.'
            ).catch(() => null);
            let fallbackClick = null;
            let ferramentasClick = null;
            if (!(autoLogin && autoLogin.success)) {
                ferramentasClick = await clicarFerramentasAvantProPorCoordenada().catch((err) => ({
                    success: false,
                    reason: 'erro_no_fallback_clique_ferramentas_avant',
                    error: err && err.message ? err.message : String(err)
                }));
                if (ferramentasClick && ferramentasClick.success) {
                    await esperar(900);
                    autoLogin = await promiseComTimeout(
                        tentarLoginAvantProNoWebview(mlWebviewEl, {
                            timeoutMs: 4200,
                            atrasos: [0, 180, 420, 850, 1500, 2600, 3800]
                        }),
                        4600,
                        'Aguardando formulario do Avant Pro apos clicar em Ferramentas.'
                    ).catch(() => autoLogin);
                }
            }
            if (!(autoLogin && autoLogin.success)) {
                fallbackClick = await clicarLoginAvantProPorCoordenada().catch((err) => ({
                    success: false,
                    reason: 'erro_no_fallback_clique_login_avant',
                    error: err && err.message ? err.message : String(err)
                }));
                if (fallbackClick && fallbackClick.success) {
                    await esperar(280);
                    autoLogin = await promiseComTimeout(
                        tentarLoginAvantProNoWebview(mlWebviewEl, {
                            timeoutMs: 3600,
                            atrasos: [0, 180, 420, 850, 1500, 2400, 3400]
                        }),
                        4000,
                        'Aguardando iframe de login Avant Pro.'
                    ).catch(() => autoLogin);
                }
            }
            return {
                ...(resultado || {}),
                success: !!((resultado && resultado.success) || (ferramentasClick && ferramentasClick.success) || (fallbackClick && fallbackClick.success) || (autoLogin && autoLogin.success)),
                autoLogin,
                ferramentasClick,
                fallbackClick
            };
        }

  const api = { clicarLoginAvantProPorCoordenada, fecharModalBloqueanteAvantProNoWebview, abrirLoginAvantProNoWebview };
  browser.avantLoginModal = Object.freeze(api);
  Object.assign(global, api);
})(window);
