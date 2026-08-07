(function (global) {
  'use strict';

  const browser = global.FavoritosV2.browser;
  const pageScripts = browser.pageScripts;

        async function acionarCardsAvantProFilaWebview(opcoes = {}) {
            const webview = resolverWebviewFavoritosColeta(opcoes);
            if (!webview || typeof webview.executeJavaScript !== 'function') return { clicked: 0, totalCandidates: 0, keys: [] };
            const maxClicksValor = opcoes.maxClicks === undefined ? 0 : Number(opcoes.maxClicks);
            const maxClicks = Math.max(0, Math.min(8, Number.isFinite(maxClicksValor) ? maxClicksValor : 0));
            const maxTentativasPorCard = Math.max(1, Math.min(3, Number(opcoes.maxTentativasPorCard) || 3));
            const maxRuntimeMsValor = opcoes.maxRuntimeMs === undefined ? 4500 : Number(opcoes.maxRuntimeMs);
            const maxRuntimeMs = Math.max(1200, Math.min(8000, Number.isFinite(maxRuntimeMsValor) ? maxRuntimeMsValor : 4500));
            const deepScan = opcoes.deepScan !== false;
            const checkLogin = opcoes.checkLogin !== false;
            if (maxClicks <= 0) return { clicked: 0, totalCandidates: 0, keys: [], capturados: 0, skipped: true };
            return await webview.executeJavaScript(pageScripts.render('acionar-cards-avant-pro-fila-webview-1', { p0: (JSON.stringify(maxClicks)), p1: (JSON.stringify(maxTentativasPorCard)), p2: (JSON.stringify(maxRuntimeMs)), p3: (JSON.stringify(deepScan)), p4: (JSON.stringify(checkLogin)) }), true).catch((err) => ({
                clicked: 0,
                totalCandidates: 0,
                keys: [],
                capturados: 0,
                error: err && err.message ? err.message : String(err)
            }));
        }

  const api = { acionarCardsAvantProFilaWebview };
  browser.avantCardQueue = Object.freeze(api);
  Object.assign(global, api);
})(window);
