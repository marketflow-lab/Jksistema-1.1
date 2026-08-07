(function (global) {
  'use strict';

  const browser = global.FavoritosV2.browser;
  const pageScripts = browser.pageScripts;

        async function aguardarDadosAvantProEstaveisWebview(opcoes = {}) {
            const webview = resolverWebviewFavoritosColeta(opcoes);
            if (!webview || typeof webview.executeJavaScript !== 'function') return null;
            const minWaitMs = Math.max(0, Number(opcoes.minWaitMs) || AVANT_PRO_ESTABILIDADE_MIN_MS);
            const stableMs = Math.max(250, Number(opcoes.stableMs) || AVANT_PRO_ESTABILIDADE_MS);
            const maxWaitMs = Math.max(minWaitMs + stableMs, Number(opcoes.maxWaitMs) || AVANT_PRO_ESTABILIDADE_MAX_MS);
            const pollMs = Math.max(120, Number(opcoes.pollMs) || 220);
            return await webview.executeJavaScript(pageScripts.render('aguardar-dados-avant-pro-estaveis-webview-1', { p0: (JSON.stringify(minWaitMs)), p1: (JSON.stringify(stableMs)), p2: (JSON.stringify(maxWaitMs)), p3: (JSON.stringify(pollMs)) }), true);
        }

        async function diagnosticarAvantProNoWebview(webview = mlWebviewEl) {
            if (!webview || typeof webview.executeJavaScript !== 'function') return null;
            return await webview.executeJavaScript(pageScripts.render('diagnosticar-avant-pro-no-webview-1', { p0: (ML_FAVORITOS_AVANT_LOGIN_RECENTE_MS) }), true).catch(() => null);
        }

  const api = { aguardarDadosAvantProEstaveisWebview, diagnosticarAvantProNoWebview };
  browser.diagnostics = Object.freeze(api);
  Object.assign(global, api);
})(window);
