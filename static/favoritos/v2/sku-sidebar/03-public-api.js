(function (global) {
    'use strict';

    const skuSidebar = global.FavoritosV2 && global.FavoritosV2.skuSidebar;
    if (!skuSidebar || !skuSidebar.__runtimeInitialized) {
        throw new Error('Runtime do SKU sidebar nao inicializado.');
    }
    if (!skuSidebar.components.has('core') || !skuSidebar.components.has('ui')) {
        throw new Error('Componentes do SKU sidebar incompletos.');
    }
    if (skuSidebar.__componentsReady) return;

    const publicNames = [
        'selecionarLinhaAnuncio', 'encontrarAnuncioPrimeiraPaginaAtual',
        'atualizarCelulaMediaVendas', 'dividirSkusMl', 'extrairSkusAtributosMl',
        'temSkuOficialAtributosMl', 'extrairSkusAnuncioMl', 'numeroOrdenacaoSkuMl',
        'compararItensSkuSidebar', 'chaveSkuSidebarMercadoLivre',
        'mesclarSkusAnunciosMercadoLivre', 'agendarBuscaRemotaSkuSidebarMercadoLivre',
        'montarItensSkuSidebarMercadoLivre', 'filtrarItensSkuSidebarMercadoLivre',
        'skuSidebarTemSkuExatoBusca', 'deveBuscarSkuRemotoParaTermo',
        'buscarSkuExatoHistoricoSidebar', 'aplicarSkuExatoHistoricoSidebar',
        'extrairMlbsItemSkuSidebar', 'encontrarItemSkuSidebarMercadoLivre',
        'executarRenderFavoritosSeguro', 'carregarMaisSkusSidebarMercadoLivre',
        'atualizarContadorSkuSidebarSelecionados', 'atualizarFiltroAzulFavoritos',
        'atualizarAnimacaoAzulNoNavegadorMl', 'atualizarStatusFavoritosNoNavegadorMl',
        'alternarSelecaoTodosSkuSidebar', 'obterPesquisasSkuSidebar',
        'criarBalaoPesquisasSkuSidebar', 'posicionarBalaoPesquisasSkuSidebar',
        'garantirModalPesquisasSku', 'elementosModalPesquisasSku',
        'construirUrlAnuncioFavoritosPorItemId', 'normalizarUrlAnuncioSkuModal',
        'coletarFontesAnuncioSkuModal', 'obterImagemSkuModal', 'obterUrlAnuncioSkuModal',
        'sincronizarDadosAnuncioLinhaSku', 'atualizarFotoModalPesquisasSku',
        'definirStatusModalPesquisasSku', 'obterDescricaoModalPesquisasSku',
        'atualizarDescricaoModalPesquisasSku', 'verDescricaoModalPesquisasSku',
        'fecharModalPesquisasSku', 'obterOuCriarLinhaPesquisaSku',
        'preencherCamposModalPesquisasSku', 'aplicarCamposModalNaLinha',
        'abrirModalPesquisasSkuSidebar', 'salvarModalPesquisasSku',
        'preencherModalPesquisasSkuIa', 'editarPesquisasSkuSidebar',
        'abaFavoritosMlAtiva', 'itemSkuSidebarAtivoNaAbaAtual',
        'selecionarSkuSidebarParaAbaAtual', 'renderizarSkuSidebarMercadoLivre',
        'obterSkusSelecionadosSidebar', 'normalizarSelecionadosFavoritosExecucao',
        'aplicarDesmarcacaoSkuFavoritos', 'publicarDesmarcacaoSkuFavoritos',
        'desmarcarSkuFavoritosProcessado', 'inicializarSincronizacaoSelecaoFavoritos'
    ];
    const publicApi = {};

    publicNames.forEach((name) => {
        const handler = skuSidebar.internal[name];
        if (typeof handler !== 'function') {
            throw new Error('Funcao publica ausente no SKU sidebar: ' + name);
        }
        publicApi[name] = handler;
        global[name] = handler;
    });

    Object.assign(skuSidebar.publicApi, publicApi);
    skuSidebar.publicApiNames = Object.freeze([...publicNames]);
    skuSidebar.__componentsReady = true;
})(window);
