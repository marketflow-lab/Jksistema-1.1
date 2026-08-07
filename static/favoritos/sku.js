/**
 * Favoritos - catalogo de SKUs, lojas, descricoes, tabela e busca.
 * Fachada de compatibilidade para os componentes em favoritos/v2/sku/.
 */
(function (global) {
    'use strict';

    const VERSION = '20260807-favoritos-sku-modular-v1';
    const COMPONENTS = [
        '00-runtime.js',
        '01-normalization-descriptions.js',
        '02-store-cache.js',
        '03-catalog-table.js',
        '04-search-tabs.js',
        '05-public-api.js'
    ];

    if (global.__FAVORITOS_SKU_READY__) return;

    function componentUrl(fileName) {
        return '/favoritos/v2/sku/' + fileName + '?v=' + VERSION;
    }

    function loadComponent(fileName) {
        return new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = componentUrl(fileName);
            script.onload = resolve;
            script.onerror = () => reject(new Error('Falha ao carregar o SKU: ' + fileName));
            (document.head || document.documentElement).appendChild(script);
        });
    }

    global.__FAVORITOS_SKU_READY__ = COMPONENTS.reduce(
        (chain, fileName) => chain.then(() => loadComponent(fileName)),
        Promise.resolve()
    ).then(() => {
        const feature = global.FavoritosV2 && global.FavoritosV2.sku;
        if (!feature || !feature.__componentsReady) {
            throw new Error('API modular do SKU nao ficou pronta.');
        }
        return feature.publicApi;
    }).catch((error) => {
        console.error('[Favoritos] Nao foi possivel carregar o SKU modular.', error);
        throw error;
    });
})(window);
