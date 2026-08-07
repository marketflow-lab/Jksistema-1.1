(function (global) {
  'use strict';

  const browser = global.FavoritosV2.browser;
  const pageScripts = browser.pageScripts;

        async function extrairAnunciosAvantProDomWebview(opcoes = {}) {
            const webview = resolverWebviewFavoritosColeta(opcoes);
            if (!webview || typeof webview.executeJavaScript !== 'function') return { success: false, total: 0, anuncios: [] };
            const limite = Math.max(1, Math.min(Number(opcoes.limite) || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80, 120));
            return await webview.executeJavaScript(pageScripts.render('extrair-anuncios-avant-pro-dom-webview-1', { p0: (JSON.stringify(limite)) }), true).then((resultado) => {
                const anuncios = Array.isArray(resultado && resultado.anuncios)
                    ? resultado.anuncios.map(item => prepararAnuncioMercadoLivreCanonico(item, 'avantpro_dom')).filter(item => item.chave_canonica)
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

  const api = { extrairAnunciosAvantProDomWebview };
  browser.extractionAvant = Object.freeze(api);
  Object.assign(global, api);
})(window);
