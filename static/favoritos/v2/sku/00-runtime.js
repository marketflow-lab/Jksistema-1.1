(function (global) {
    'use strict';

    const favoritos = global.FavoritosV2 = global.FavoritosV2 || {};
    const feature = favoritos.sku = favoritos.sku || {};
    if (feature.__runtimeInitialized) return;

    feature.__runtimeInitialized = true;
    feature.schema = 'jk.favoritos.sku.v1';
    feature.components = feature.components || new Set();
    feature.internal = feature.internal || {};
    feature.publicApi = feature.publicApi || {};

    const adapterNames = new Set([
        "abrirAnuncioComAvantPro",
        "atualizarEstadoSidebarRanking",
        "carregarFavoritosAnunciosSku",
        "cancelarAberturaMercadoLivreAoEntrar",
        "copiarLinkAnuncio",
        "favoritosBotoesAtualizarSkuMl",
        "favoritosEhTodasLojas",
        "favoritosLojaSelecionadaParaApi",
        "favoritosNomeLojaExibicao",
        "fetchFavoritosComTimeout",
        "headersJsonAutenticado",
        "obterAuthHeaders",
        "ocultarNavegadorMlShellDefinitivo",
        "prepararAbaFavoritosMl",
        "prepararAbaHistoricoFavoritos",
        "prepararAbaPlanilhasFavoritos",
        "renderizarAnunciosIgnoradosSku",
        "renderizarLinksAlinhadosFavoritos",
        "renderizarSkuSidebarMercadoLivre",
        "renderizarVendedoresIgnoradosRanking",
        "skuNormalizarLoja"
    ]);

    function resolveAdapter(name) {
        if (!adapterNames.has(name)) {
            throw new Error('Adaptador do SKU nao permitido: ' + name);
        }
        const adapter = global[name];
        if (typeof adapter !== 'function') {
            throw new Error('Adaptador do SKU indisponivel: ' + name);
        }
        return adapter;
    }

    const state = {};
    Object.defineProperties(state, {
        data: {
            get: () => skuDados,
            set: value => { skuDados = Array.isArray(value) ? value : []; }
        },
        availableStores: {
            get: () => skuLojasDisponiveis,
            set: value => { skuLojasDisponiveis = Array.isArray(value) ? value : []; }
        },
        selectedStore: {
            get: () => skuLojaSelecionada,
            set: value => { skuLojaSelecionada = String(value || ''); }
        },
        currentPage: {
            get: () => skuPaginaAtual,
            set: value => { skuPaginaAtual = Math.max(1, Number(value) || 1); }
        },
        showHidden: {
            get: () => skuMostrarOcultos,
            set: value => { skuMostrarOcultos = !!value; }
        },
        hiddenSkus: { get: () => skuSkusOcultos },
        descriptionCache: { get: () => skuDescricaoMemCache },
        currentStoreListings: {
            get: () => mlSkusAnunciosLojaAtual,
            set: value => { mlSkusAnunciosLojaAtual = Array.isArray(value) ? value : []; }
        },
        selectedMarketplaceStore: {
            get: () => mlSkuLojaSelecionada,
            set: value => { mlSkuLojaSelecionada = String(value || ''); }
        },
        storeCache: { get: () => mlSkuEstadoPorLojaCache },
        loadingPromises: { get: () => mlSkuCarregamentoPromises }
    });

    feature.runtime = Object.freeze({
        adapterNames: Object.freeze(Array.from(adapterNames)),
        resolveAdapter,
        state
    });
})(window);
