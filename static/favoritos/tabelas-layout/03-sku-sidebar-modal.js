/**
 * Favoritos - SKU sidebar e modal de pesquisas.
 * Fachada de compatibilidade para os componentes em favoritos/v2/sku-sidebar/.
 */
(function (global) {
    'use strict';

    const VERSION = '20260807-favoritos-sku-modular-v1';
    const COMPONENTS = [
        '00-runtime.js',
        '01-core.js',
        '02-ui.js',
        '03-public-api.js'
    ];

    if (global.__FAVORITOS_SKU_SIDEBAR_READY__) return;

    function componentUrl(fileName) {
        return '/favoritos/v2/sku-sidebar/' + fileName + '?v=' + VERSION;
    }

    function loadComponent(fileName) {
        return new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = componentUrl(fileName);
            script.onload = resolve;
            script.onerror = () => reject(new Error('Falha ao carregar o SKU sidebar: ' + fileName));
            (document.head || document.documentElement).appendChild(script);
        });
    }

    global.__FAVORITOS_SKU_SIDEBAR_READY__ = COMPONENTS.reduce(
        (chain, fileName) => chain.then(() => loadComponent(fileName)),
        Promise.resolve()
    ).then(() => {
        const skuSidebar = global.FavoritosV2 && global.FavoritosV2.skuSidebar;
        if (!skuSidebar || !skuSidebar.__componentsReady) {
            throw new Error('API modular do SKU sidebar nao ficou pronta.');
        }
        return skuSidebar.publicApi;
    }).catch((error) => {
        console.error('[Favoritos] Nao foi possivel carregar o SKU sidebar modular.', error);
        throw error;
    });
})(window);
