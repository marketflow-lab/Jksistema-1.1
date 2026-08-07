(function (global) {
    'use strict';

    const favoritos = global.FavoritosV2 = global.FavoritosV2 || {};
    const skuSidebar = favoritos.skuSidebar = favoritos.skuSidebar || {};
    if (skuSidebar.__runtimeInitialized) return;

    skuSidebar.__runtimeInitialized = true;
    skuSidebar.schema = 'jk.favoritos.sku-sidebar.v1';
    skuSidebar.components = skuSidebar.components || new Set();
    skuSidebar.internal = skuSidebar.internal || {};
    skuSidebar.publicApi = skuSidebar.publicApi || {};

    const adapterNames = new Set([
        'agendarAtualizacaoPosicaoNavegadorMlShell',
        'calcularMetricasMediaVendas',
        'carregarFavoritosAnunciosSku',
        'carregarSkuFavoritos',
        'extrairItemIdAnuncio',
        'favoritosLojaAtualNormalizada',
        'favoritosNomeLojaExibicao',
        'limparFavoritosSkuSelecionado',
        'limparHistoricoSkuSelecionado',
        'mlSkuRenderizarCardsLojas',
        'renderizarHistoricoSkuSidebar',
        'selecionarHistoricoSku',
        'skuBuscarDescricaoManual',
        'skuChaveSku',
        'skuItemIdsDescricao',
        'skuNormalizarLoja',
        'skuObterDescricao',
        'skuObterImagem',
        'skuObterItemIds',
        'skuObterLoja',
        'skuObterPesquisa',
        'skuObterTitulo'
    ]);

    function resolveAdapter(name) {
        if (!adapterNames.has(name)) {
            throw new Error('Adaptador do SKU sidebar nao permitido: ' + name);
        }
        const adapter = global[name];
        if (typeof adapter !== 'function') {
            throw new Error('Adaptador do SKU sidebar indisponivel: ' + name);
        }
        return adapter;
    }

    const state = {};
    Object.defineProperties(state, {
        selected: { get: () => mlSkuSidebarSelecionados },
        filter: {
            get: () => mlSkuSidebarFiltro,
            set: value => { mlSkuSidebarFiltro = String(value || ''); }
        },
        renderLimit: {
            get: () => mlSkuSidebarRenderLimit,
            set: value => { mlSkuSidebarRenderLimit = Number(value) || ML_SKU_SIDEBAR_PAGE_SIZE; }
        },
        currentStoreListings: { get: () => mlSkusAnunciosLojaAtual }
    });

    skuSidebar.runtime = Object.freeze({
        adapterNames: Object.freeze(Array.from(adapterNames)),
        resolveAdapter,
        state
    });
})(window);
