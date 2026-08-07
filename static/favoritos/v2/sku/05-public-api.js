(function (global) {
    'use strict';

    const feature = global.FavoritosV2 && global.FavoritosV2.sku;
    if (!feature || !feature.__runtimeInitialized) {
        throw new Error('Runtime do SKU nao inicializado.');
    }
    const requiredComponents = ['normalizationDescriptions', 'storeCache', 'catalogTable', 'searchTabs'];
    requiredComponents.forEach((component) => {
        if (!feature.components.has(component)) {
            throw new Error('Componente do SKU ausente: ' + component);
        }
    });
    if (feature.__componentsReady) return;

    const groups = {
        "normalizationDescriptions": [
            "skuTexto",
            "skuNumero",
            "skuFormatarNumero",
            "skuObterSku",
            "skuChaveOculto",
            "skuEstaOculto",
            "skuObterProduto",
            "skuObterLoja",
            "skuObterSaldoLoja",
            "skuObterSaldoFull",
            "skuObterTotal",
            "skuObterPesquisa",
            "skuChaveSku",
            "aplicarPesquisasGlobaisSkuLocal",
            "skuChavePreferenciaLoja",
            "skuCarregarPreferenciaLoja",
            "skuSalvarPreferenciaLoja",
            "skuResumoDescricao",
            "skuChaveDescricao",
            "skuItemIdsDescricao",
            "skuItemIdsPorSkuDescricao",
            "skuAplicarDescricaoResultado",
            "skuDescricaoCacheKey",
            "skuDescricaoCacheGet",
            "skuDescricaoCacheSet",
            "skuCancelarBuscaDescricoesAutomaticas",
            "skuNormalizarLinhaApiMercadoLivre",
            "skuAtualizarDadosComApiMercadoLivre",
            "skuAgendarBuscaDescricoesAutomaticas",
            "skuBuscarDescricoesAutomaticas",
            "skuBuscarDescricaoManual"
        ],
        "storeCache": [
            "skuLojasComDados",
            "skuRenderizarCardsLojas",
            "mlSkuSalvarPreferenciaLoja",
            "mlSkuCarregarPreferenciaLoja",
            "favoritosMostrarTelaPrincipal",
            "favoritosOcultarTelaPrincipal",
            "favoritosLojaAtualNormalizada",
            "favoritosChaveCacheLoja",
            "favoritosResolverLojaRespostaSkus",
            "favoritosRespostaSkusConfereComAlvo",
            "favoritosClonarListaObjetos",
            "favoritosClonarSkusComLoja",
            "favoritosCacheLojaSelecionadaSegura",
            "favoritosClonarValorCache",
            "favoritosClonarEntradasMapCache",
            "favoritosCacheSkusTemDados",
            "favoritosObterCacheSkusLoja",
            "favoritosCacheSkusRecente",
            "favoritosDefinirCarregamentoSkus",
            "favoritosSalvarEstadoLojaAtual",
            "favoritosRestaurarEstadoLoja",
            "favoritosSalvarCacheSkusAtual",
            "favoritosLimparDadosSkusLojaSemCache",
            "favoritosAplicarCacheSkusLoja",
            "favoritosLojasCabecalhoDisponiveis",
            "favoritosMetaLojaCabecalho",
            "favoritosAtualizarCardLojaCabecalho",
            "favoritosObterAbaAtual",
            "favoritosAtualizarAbaAtualAoTrocarLoja",
            "favoritosResetarEstadoLoja",
            "favoritosDefinirLojaSelecionada",
            "favoritosRenderizarCardsEntrada",
            "favoritosResolverLojaInicialRapida",
            "favoritosCarregarLojasEntrada",
            "favoritosSelecionarLojaEntrada",
            "favoritosSelecionarLojaModulo",
            "mlSkuRenderizarCardsLojas",
            "mlSkuCarregarSkusAnuncios"
        ],
        "catalogTable": [
            "skuFiltrarDados",
            "skuCriarCelulaTexto",
            "skuCriarCelulaSku",
            "skuSalvarPesquisasCadastro",
            "skuAgendarSalvarPesquisasCadastro",
            "skuCriarCelulaPesquisa",
            "skuCriarCelulaDescricao",
            "skuCriarCelulaIa",
            "skuSalvarOcultosServidor",
            "skuAlternarOculto",
            "skuCriarBotaoOcultar",
            "skuContarOcultosLojaAtual",
            "skuAtualizarBotaoOcultos",
            "skuRenderizarPaginacao",
            "skuRenderizarTabela",
            "carregarSkuFavoritos",
            "atualizarSkusMercadoLivreAgora"
        ],
        "searchTabs": [
            "garantirSidebarSkuUnico",
            "buscar",
            "displayLinkProduto",
            "displayBuscaTermos",
            "renderTable",
            "mudarAba"
        ]
    };
    const semanticGroups = {
        "normalization": [
            "skuTexto",
            "skuNumero",
            "skuFormatarNumero",
            "skuObterSku",
            "skuChaveOculto",
            "skuEstaOculto",
            "skuObterProduto",
            "skuObterLoja",
            "skuObterSaldoLoja",
            "skuObterSaldoFull",
            "skuObterTotal",
            "skuObterPesquisa",
            "skuChaveSku",
            "aplicarPesquisasGlobaisSkuLocal",
            "skuChavePreferenciaLoja",
            "skuCarregarPreferenciaLoja",
            "skuSalvarPreferenciaLoja"
        ],
        "descriptions": [
            "skuResumoDescricao",
            "skuChaveDescricao",
            "skuItemIdsDescricao",
            "skuItemIdsPorSkuDescricao",
            "skuAplicarDescricaoResultado",
            "skuDescricaoCacheKey",
            "skuDescricaoCacheGet",
            "skuDescricaoCacheSet",
            "skuCancelarBuscaDescricoesAutomaticas",
            "skuNormalizarLinhaApiMercadoLivre",
            "skuAtualizarDadosComApiMercadoLivre",
            "skuAgendarBuscaDescricoesAutomaticas",
            "skuBuscarDescricoesAutomaticas",
            "skuBuscarDescricaoManual"
        ],
        "stores": [
            "skuLojasComDados",
            "skuRenderizarCardsLojas",
            "mlSkuSalvarPreferenciaLoja",
            "mlSkuCarregarPreferenciaLoja",
            "favoritosMostrarTelaPrincipal",
            "favoritosOcultarTelaPrincipal",
            "favoritosLojaAtualNormalizada",
            "favoritosChaveCacheLoja",
            "favoritosResolverLojaRespostaSkus",
            "favoritosRespostaSkusConfereComAlvo",
            "favoritosClonarListaObjetos",
            "favoritosClonarSkusComLoja",
            "favoritosCacheLojaSelecionadaSegura",
            "favoritosClonarValorCache",
            "favoritosClonarEntradasMapCache",
            "favoritosCacheSkusTemDados",
            "favoritosObterCacheSkusLoja",
            "favoritosCacheSkusRecente",
            "favoritosDefinirCarregamentoSkus",
            "favoritosSalvarEstadoLojaAtual",
            "favoritosRestaurarEstadoLoja",
            "favoritosSalvarCacheSkusAtual",
            "favoritosLimparDadosSkusLojaSemCache",
            "favoritosAplicarCacheSkusLoja",
            "favoritosLojasCabecalhoDisponiveis",
            "favoritosMetaLojaCabecalho",
            "favoritosAtualizarCardLojaCabecalho",
            "favoritosObterAbaAtual",
            "favoritosAtualizarAbaAtualAoTrocarLoja",
            "favoritosResetarEstadoLoja",
            "favoritosDefinirLojaSelecionada",
            "favoritosRenderizarCardsEntrada",
            "favoritosResolverLojaInicialRapida",
            "favoritosCarregarLojasEntrada",
            "favoritosSelecionarLojaEntrada",
            "favoritosSelecionarLojaModulo",
            "mlSkuRenderizarCardsLojas",
            "mlSkuCarregarSkusAnuncios"
        ],
        "catalog": [
            "skuFiltrarDados",
            "skuCriarCelulaTexto",
            "skuCriarCelulaSku",
            "skuSalvarPesquisasCadastro",
            "skuAgendarSalvarPesquisasCadastro",
            "skuCriarCelulaPesquisa",
            "skuCriarCelulaDescricao",
            "skuCriarCelulaIa",
            "skuSalvarOcultosServidor",
            "skuAlternarOculto",
            "skuCriarBotaoOcultar",
            "skuContarOcultosLojaAtual",
            "skuAtualizarBotaoOcultos",
            "skuRenderizarPaginacao",
            "skuRenderizarTabela",
            "carregarSkuFavoritos",
            "atualizarSkusMercadoLivreAgora"
        ],
        "search": [
            "buscar",
            "displayLinkProduto",
            "displayBuscaTermos",
            "renderTable"
        ],
        "tabs": [
            "garantirSidebarSkuUnico",
            "mudarAba"
        ]
    };
    const publicApi = {};
    const legacyGlobals = {};

    Object.entries(groups).forEach(([groupName, names]) => {
        const groupApi = {};
        names.forEach((name) => {
            const handler = feature.internal[name];
            if (typeof handler !== 'function') {
                throw new Error('Funcao publica ausente no SKU: ' + name);
            }
            groupApi[name] = handler;
            publicApi[name] = handler;
            legacyGlobals[name] = handler;
            global[name] = handler;
        });
        publicApi[groupName] = Object.freeze(groupApi);
    });

    Object.entries(semanticGroups).forEach(([groupName, names]) => {
        publicApi[groupName] = Object.freeze(Object.fromEntries(
            names.map(name => [name, feature.internal[name]])
        ));
    });

    Object.assign(feature.publicApi, publicApi);
    feature.publicApiNames = Object.freeze([
        "skuTexto",
        "skuNumero",
        "skuFormatarNumero",
        "skuObterSku",
        "skuChaveOculto",
        "skuEstaOculto",
        "skuObterProduto",
        "skuObterLoja",
        "skuObterSaldoLoja",
        "skuObterSaldoFull",
        "skuObterTotal",
        "skuObterPesquisa",
        "skuChaveSku",
        "aplicarPesquisasGlobaisSkuLocal",
        "skuChavePreferenciaLoja",
        "skuCarregarPreferenciaLoja",
        "skuSalvarPreferenciaLoja",
        "skuResumoDescricao",
        "skuChaveDescricao",
        "skuItemIdsDescricao",
        "skuItemIdsPorSkuDescricao",
        "skuAplicarDescricaoResultado",
        "skuDescricaoCacheKey",
        "skuDescricaoCacheGet",
        "skuDescricaoCacheSet",
        "skuCancelarBuscaDescricoesAutomaticas",
        "skuNormalizarLinhaApiMercadoLivre",
        "skuAtualizarDadosComApiMercadoLivre",
        "skuAgendarBuscaDescricoesAutomaticas",
        "skuBuscarDescricoesAutomaticas",
        "skuBuscarDescricaoManual",
        "skuLojasComDados",
        "skuRenderizarCardsLojas",
        "mlSkuSalvarPreferenciaLoja",
        "mlSkuCarregarPreferenciaLoja",
        "favoritosMostrarTelaPrincipal",
        "favoritosOcultarTelaPrincipal",
        "favoritosLojaAtualNormalizada",
        "favoritosChaveCacheLoja",
        "favoritosResolverLojaRespostaSkus",
        "favoritosRespostaSkusConfereComAlvo",
        "favoritosClonarListaObjetos",
        "favoritosClonarSkusComLoja",
        "favoritosCacheLojaSelecionadaSegura",
        "favoritosClonarValorCache",
        "favoritosClonarEntradasMapCache",
        "favoritosCacheSkusTemDados",
        "favoritosObterCacheSkusLoja",
        "favoritosCacheSkusRecente",
        "favoritosDefinirCarregamentoSkus",
        "favoritosSalvarEstadoLojaAtual",
        "favoritosRestaurarEstadoLoja",
        "favoritosSalvarCacheSkusAtual",
        "favoritosLimparDadosSkusLojaSemCache",
        "favoritosAplicarCacheSkusLoja",
        "favoritosLojasCabecalhoDisponiveis",
        "favoritosMetaLojaCabecalho",
        "favoritosAtualizarCardLojaCabecalho",
        "favoritosObterAbaAtual",
        "favoritosAtualizarAbaAtualAoTrocarLoja",
        "favoritosResetarEstadoLoja",
        "favoritosDefinirLojaSelecionada",
        "favoritosRenderizarCardsEntrada",
        "favoritosResolverLojaInicialRapida",
        "favoritosCarregarLojasEntrada",
        "favoritosSelecionarLojaEntrada",
        "favoritosSelecionarLojaModulo",
        "mlSkuRenderizarCardsLojas",
        "mlSkuCarregarSkusAnuncios",
        "skuFiltrarDados",
        "skuCriarCelulaTexto",
        "skuCriarCelulaSku",
        "skuSalvarPesquisasCadastro",
        "skuAgendarSalvarPesquisasCadastro",
        "skuCriarCelulaPesquisa",
        "skuCriarCelulaDescricao",
        "skuCriarCelulaIa",
        "skuSalvarOcultosServidor",
        "skuAlternarOculto",
        "skuCriarBotaoOcultar",
        "skuContarOcultosLojaAtual",
        "skuAtualizarBotaoOcultos",
        "skuRenderizarPaginacao",
        "skuRenderizarTabela",
        "carregarSkuFavoritos",
        "atualizarSkusMercadoLivreAgora",
        "garantirSidebarSkuUnico",
        "buscar",
        "displayLinkProduto",
        "displayBuscaTermos",
        "renderTable",
        "mudarAba"
    ]);
    feature.semanticGroups = Object.freeze(Object.fromEntries(
        Object.entries(semanticGroups).map(([name, names]) => [name, Object.freeze([...names])])
    ));
    feature.legacyGlobals = Object.freeze(legacyGlobals);
    feature.__componentsReady = true;
})(window);
