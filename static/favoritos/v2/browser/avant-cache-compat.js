(function (global) {
  'use strict';

  const browser = global.FavoritosV2.browser;
  const pageScripts = browser.pageScripts;

        function obterElectronApiFavoritosMlBrowser() {
            try {
                if (window.electronAPI) return window.electronAPI;
            } catch (_err) {}
            try {
                if (window.top && window.top !== window && window.top.electronAPI) return window.top.electronAPI;
            } catch (_err) {}
            return null;
        }

        function salvarMemoriaAvantProConfirmadaFavoritos(reason = 'favoritos_login_confirmado') {
            const api = obterElectronApiFavoritosMlBrowser();
            if (!api || typeof api.saveAvantProStorageSnapshot !== 'function') {
                return Promise.resolve(null);
            }
            const agora = Date.now();
            if (mlFavoritosAvantSnapshotPromise && agora - mlFavoritosAvantSnapshotAt < 7000) {
                return mlFavoritosAvantSnapshotPromise;
            }
            mlFavoritosAvantSnapshotAt = agora;
            mlFavoritosAvantSnapshotPromise = api.saveAvantProStorageSnapshot(reason, {
                source: 'favoritos',
                confirmedAt: agora
            })
                .then((resultado) => {
                    if (resultado && resultado.success) {
                        console.info('Sessao Avant Pro salva para reutilizacao.', resultado);
                    } else if (resultado && !resultado.skipped) {
                        console.warn('Sessao Avant Pro nao foi salva:', resultado);
                    }
                    return resultado || null;
                })
                .catch((err) => {
                    console.warn('Falha ao salvar sessao Avant Pro:', err && err.message ? err.message : err);
                    return null;
                })
                .finally(() => {
                    setTimeout(() => {
                        mlFavoritosAvantSnapshotPromise = null;
                    }, 500);
                });
            return mlFavoritosAvantSnapshotPromise;
        }

        function favoritosBrowserAvantCache() {
            return window.FavoritosV2?.browser?.avantCache || {};
        }

        function normalizarChaveCacheAvant(value) {
            return favoritosBrowserAvantCache().normalizarChaveCacheAvant(value);
        }

        function obterLojaContextoAvant(contexto = {}) {
            return favoritosBrowserAvantCache().obterLojaContextoAvant(contexto);
        }

        function obterSkuContextoAvant(contexto = {}) {
            return favoritosBrowserAvantCache().obterSkuContextoAvant(contexto);
        }

        function normalizarUrlCacheAvant(url) {
            return favoritosBrowserAvantCache().normalizarUrlCacheAvant(url);
        }

        function chaveCacheAvantAnuncio(anuncio, contexto = {}) {
            return favoritosBrowserAvantCache().chaveCacheAvantAnuncio(anuncio, contexto);
        }

        function carregarCacheAvantFavoritos() {
            return favoritosBrowserAvantCache().carregarCacheAvantFavoritos();
        }

        function salvarCacheAvantFavoritos(cache) {
            return favoritosBrowserAvantCache().salvarCacheAvantFavoritos(cache);
        }

        function cacheAvantAindaValido(item) {
            return favoritosBrowserAvantCache().cacheAvantAindaValido(item);
        }

        function dadosAvantCacheaveis(anuncio) {
            return favoritosBrowserAvantCache().dadosAvantCacheaveis(anuncio);
        }

        function aplicarCacheAvantAosAnuncios(anuncios, contexto = {}) {
            return favoritosBrowserAvantCache().aplicarCacheAvantAosAnuncios(anuncios, contexto);
        }

        function salvarCacheAvantDosAnuncios(anuncios, contexto = {}) {
            return favoritosBrowserAvantCache().salvarCacheAvantDosAnuncios(anuncios, contexto);
        }

  const api = { obterElectronApiFavoritosMlBrowser, salvarMemoriaAvantProConfirmadaFavoritos, favoritosBrowserAvantCache, normalizarChaveCacheAvant, obterLojaContextoAvant, obterSkuContextoAvant, normalizarUrlCacheAvant, chaveCacheAvantAnuncio, carregarCacheAvantFavoritos, salvarCacheAvantFavoritos, cacheAvantAindaValido, dadosAvantCacheaveis, aplicarCacheAvantAosAnuncios, salvarCacheAvantDosAnuncios };
  browser.avantCacheCompat = Object.freeze(api);
  Object.assign(global, api);
})(window);
