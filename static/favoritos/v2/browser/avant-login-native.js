(function (global) {
  'use strict';

  const browser = global.FavoritosV2.browser;
  const pageScripts = browser.pageScripts;

        function montarScriptLocalizarLoginAvantPro() {
            return pageScripts.render('montar-script-localizar-login-avant-pro-1', {  });
        }

        async function clicarNavegadorMlPorCoordenada(point) {
            if (!point) return { success: false, reason: 'coordenada_ausente' };
            const x = Number(point.x ?? point.left);
            const y = Number(point.y ?? point.top);
            if (!Number.isFinite(x) || !Number.isFinite(y)) {
                return { success: false, reason: 'coordenada_invalida' };
            }
            const payload = {
                x,
                y,
                clickCount: 1,
                source: point.source || '',
                label: point.label || ''
            };
            if (mlWebviewEl && mlWebviewEl.__isShellBrowserProxy && typeof mlWebviewEl.clickAt === 'function') {
                const result = await mlWebviewEl.clickAt(payload);
                return result || { success: true, x, y };
            }
            if (mlWebviewEl && typeof mlWebviewEl.sendInputEvent === 'function') {
                mlWebviewEl.sendInputEvent({ type: 'mouseMove', x, y, movementX: 0, movementY: 0 });
                mlWebviewEl.sendInputEvent({ type: 'mouseDown', x, y, button: 'left', clickCount: 1 });
                await esperar(45);
                mlWebviewEl.sendInputEvent({ type: 'mouseUp', x, y, button: 'left', clickCount: 1 });
                return { success: true, x, y };
            }
            const api = obterElectronApiFavoritosMlBrowser();
            if (api && typeof api.clickEmbeddedMlBrowser === 'function') {
                return await api.clickEmbeddedMlBrowser(payload);
            }
            return { success: false, reason: 'clique_real_indisponivel' };
        }

        function montarScriptLocalizarCampoEmailAvantProParaDigitacao() {
            return pageScripts.render('montar-script-localizar-campo-email-avant-pro-para-digitacao-1', {  });
        }

        function montarScriptLocalizarBotaoConfirmarAvantProParaClique() {
            return pageScripts.render('montar-script-localizar-botao-confirmar-avant-pro-para-clique-1', {  });
        }

        async function digitarTextoNativoNoNavegadorMl(text, opcoes = {}) {
            const payload = {
                text: String(text || ''),
                clearFirst: opcoes.clearFirst !== false,
                pressEnter: opcoes.pressEnter === true
            };
            if (mlWebviewEl && mlWebviewEl.__isShellBrowserProxy && typeof mlWebviewEl.typeText === 'function') {
                return await mlWebviewEl.typeText(payload);
            }
            if (mlWebviewEl && typeof mlWebviewEl.sendInputEvent === 'function') {
                const tapKey = async (keyCode, modifiers = []) => {
                    mlWebviewEl.sendInputEvent({ type: 'keyDown', keyCode, modifiers });
                    await esperar(25);
                    mlWebviewEl.sendInputEvent({ type: 'keyUp', keyCode, modifiers });
                };
                try { if (typeof mlWebviewEl.focus === 'function') mlWebviewEl.focus(); } catch (_err) {}
                if (payload.clearFirst) {
                    await tapKey('A', ['control']);
                    await esperar(20);
                    await tapKey('Backspace');
                }
                if (payload.text) {
                    if (typeof mlWebviewEl.insertText === 'function') {
                        await Promise.resolve(mlWebviewEl.insertText(payload.text));
                    } else {
                        for (const ch of payload.text) {
                            mlWebviewEl.sendInputEvent({ type: 'char', keyCode: ch });
                            await esperar(2);
                        }
                    }
                }
                if (payload.pressEnter) {
                    await esperar(80);
                    await tapKey('Enter');
                }
                return { success: true, typed: payload.text.length, enter: payload.pressEnter };
            }
            const api = obterElectronApiFavoritosMlBrowser();
            if (api && typeof api.typeEmbeddedMlBrowser === 'function') {
                return await api.typeEmbeddedMlBrowser(payload);
            }
            return { success: false, reason: 'digitacao_real_indisponivel' };
        }

        async function tentarLoginAvantProPorElectronNativo(opcoes = {}) {
            const email = typeof AVANT_PRO_LOGIN_EMAIL !== 'undefined' ? String(AVANT_PRO_LOGIN_EMAIL || '').trim() : '';
            const termo = String(opcoes.termo || '').trim();
            if (!email) return { success: false, reason: 'email_avant_indisponivel' };
            const api = obterElectronApiFavoritosMlBrowser();
            if (!api || typeof api.loginAvantProEmbeddedBrowser !== 'function') {
                return { success: false, reason: 'login_avant_electron_indisponivel' };
            }
            mostrarStatusConexaoAvantPro('preenchendo o e-mail pelo Electron', { termo });
            const resultado = await api.loginAvantProEmbeddedBrowser(email).catch((err) => ({
                success: false,
                reason: 'erro_no_login_avant_electron',
                error: err && err.message ? err.message : String(err)
            }));
            await esperar(Number(opcoes.esperaAposConfirmarMs) || 900);
            mostrarStatusConexaoAvantPro('aguardando a mensagem de agradecimento', { termo });
            const confirmado = typeof aguardarConfirmacaoLoginAvantProNoWebview === 'function'
                ? await aguardarConfirmacaoLoginAvantProNoWebview({
                    timeoutMs: Number(opcoes.timeoutConfirmacaoMs) || 6500,
                    pollMs: Number(opcoes.pollMs) || 350,
                    tentarPreencherEmail: false
                }).catch(() => null)
                : await detectarConfirmacaoLoginAvantProNoWebview().catch(() => null);
            if (confirmado && (confirmado.confirmado || confirmado.confirmed)) {
                registrarLoginAvantProConfirmadoFavoritos();
            }
            return {
                ...(resultado || {}),
                success: !!(resultado && resultado.success),
                confirmed: !!(confirmado && (confirmado.confirmado || confirmado.confirmed)),
                confirmacao: confirmado || null,
                via: 'electron_native_login'
            };
        }

        async function tentarLoginAvantProPorDigitacaoNativa(opcoes = {}) {
            const email = typeof AVANT_PRO_LOGIN_EMAIL !== 'undefined' ? String(AVANT_PRO_LOGIN_EMAIL || '').trim() : '';
            if (!email) return { success: false, reason: 'email_avant_indisponivel' };
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') {
                return { success: false, reason: 'navegador_indisponivel' };
            }
            const termo = String(opcoes.termo || '').trim();
            mostrarStatusConexaoAvantPro('localizando o campo de e-mail', { termo });
            const alvo = await mlWebviewEl.executeJavaScript(montarScriptLocalizarCampoEmailAvantProParaDigitacao(), true).catch((err) => ({
                success: false,
                reason: 'erro_ao_localizar_campo_avant_para_digitacao',
                error: err && err.message ? err.message : String(err)
            }));
            if (alvo && alvo.confirmed) {
                registrarLoginAvantProConfirmadoFavoritos();
                return { success: true, confirmed: true, alvo };
            }
            if (!(alvo && alvo.success)) return alvo || { success: false, reason: 'campo_email_avant_nao_localizado_para_digitacao' };
            const clickCampo = await clicarNavegadorMlPorCoordenada(alvo).catch((err) => ({
                success: false,
                reason: 'erro_no_clique_real_campo_email_avant',
                error: err && err.message ? err.message : String(err)
            }));
            if (!(clickCampo && clickCampo.success !== false)) {
                return { success: false, reason: 'falha_ao_focar_campo_email_avant', alvo, clickCampo };
            }
            await esperar(160);
            mostrarStatusConexaoAvantPro('digitando o e-mail do Avant Pro', { termo });
            const digitacao = await digitarTextoNativoNoNavegadorMl(email, {
                clearFirst: true,
                pressEnter: false
            }).catch((err) => ({
                success: false,
                reason: 'erro_ao_digitar_email_avant',
                error: err && err.message ? err.message : String(err)
            }));
            if (!(digitacao && digitacao.success !== false)) {
                return { success: false, reason: 'falha_ao_digitar_email_avant', alvo, clickCampo, digitacao };
            }
            await esperar(Number(opcoes.esperaAntesConfirmarMs) || 220);
            mostrarStatusConexaoAvantPro('confirmando o e-mail', { termo });
            const botao = await mlWebviewEl.executeJavaScript(montarScriptLocalizarBotaoConfirmarAvantProParaClique(), true).catch((err) => ({
                success: false,
                reason: 'erro_ao_localizar_confirmacao_avant',
                error: err && err.message ? err.message : String(err)
            }));
            let confirmar = null;
            if (botao && botao.success) {
                confirmar = await clicarNavegadorMlPorCoordenada(botao).catch((err) => ({
                    success: false,
                    reason: 'erro_no_clique_real_confirmacao_avant',
                    error: err && err.message ? err.message : String(err)
                }));
            }
            if (!(confirmar && confirmar.success !== false)) {
                confirmar = await digitarTextoNativoNoNavegadorMl('', {
                    clearFirst: false,
                    pressEnter: true
                }).catch((err) => ({
                    success: false,
                    reason: 'erro_ao_confirmar_avant_por_enter',
                    error: err && err.message ? err.message : String(err)
                }));
            }
            await esperar(Number(opcoes.esperaAposConfirmarMs) || 600);
            mostrarStatusConexaoAvantPro('aguardando a mensagem de agradecimento', { termo });
            const confirmado = await detectarConfirmacaoLoginAvantProNoWebview().catch(() => null);
            if (confirmado && confirmado.confirmado) {
                registrarLoginAvantProConfirmadoFavoritos();
            }
            return {
                success: true,
                alvo,
                clickCampo,
                digitacao,
                botao,
                confirmar,
                confirmed: !!(confirmado && confirmado.confirmado),
                confirmacao: confirmado || null
            };
        }

  const api = { montarScriptLocalizarLoginAvantPro, clicarNavegadorMlPorCoordenada, montarScriptLocalizarCampoEmailAvantProParaDigitacao, montarScriptLocalizarBotaoConfirmarAvantProParaClique, digitarTextoNativoNoNavegadorMl, tentarLoginAvantProPorElectronNativo, tentarLoginAvantProPorDigitacaoNativa };
  browser.avantLoginNative = Object.freeze(api);
  Object.assign(global, api);
})(window);
