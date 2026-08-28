(function installJKMediasState(global) {
    'use strict';

    const app = global.JKMedias;
    app.defineModule('state', ['core'], ({ state, modules }) => {
        const parseJsonLocalStorage = modules.core.parseJsonLocalStorage;
        const tenantKey = String(localStorage.getItem('client_id') || localStorage.getItem('x_client_id') || 'default');
        const hidden = parseJsonLocalStorage('medias_compras_skus_ocultos_' + tenantKey, []);

        Object.assign(state, {
            LS_COL_WIDTHS_KEY: 'medias_compras_larguras_colunas',
            tenantKey,
            LS_HIDDEN_SKUS_KEY: 'medias_compras_skus_ocultos_' + tenantKey,
            LS_PEDIDOS_COL_WIDTHS_KEY: 'medias_pedidos_larguras_colunas_' + tenantKey,
            CHAVES_COLUNAS_PEDIDOS: ['sku', 'foto', 'titulo', 'oem', 'cor_lado', 'link', 'quantidade', 'valor_unidade', 'valor_total', 'cbm', 'peso', 'embalagem', 'acoes'],
            LARGURAS_PADRAO_COLUNAS_PEDIDOS: {
                sku: 96, foto: 92, titulo: 220, oem: 130, cor_lado: 108, link: 140,
                quantidade: 102, valor_unidade: 128, valor_total: 138, cbm: 118,
                peso: 118, embalagem: 150, acoes: 56,
            },
            STATUS_LISTA_OPCOES: ['Lista gerada', 'Em Orçamento', 'Analisando orçamento', 'Pedido Aprovado', 'Pedido emitido', 'Em produção', 'Em trânsito', 'Em desembaraço', 'Recebido', 'Pedido cancelado'],
            largurasColunas: parseJsonLocalStorage('medias_compras_larguras_colunas', {}),
            largurasColunasPedidos: parseJsonLocalStorage('medias_pedidos_larguras_colunas_' + tenantKey, {}),
            chavesColunasAtuais: [],
            colElementsAtuais: [],
            colElementsPedidos: [],
            lojaSelecionada: '__todas',
            lojasDisponiveis: [],
            skusOcultosSet: new Set((Array.isArray(hidden) ? hidden : []).map((sku) => String(sku || '').trim().toUpperCase()).filter(Boolean)),
            itensVisaoAtual: [],
            comprasSugeridasEditadas: new Map(),
            colunasMesesAtuais: [],
            abaAtual: 'lista',
            filtroSkuAtual: '',
            listasPedidosResumo: [],
            listaPedidoAtual: null,
            geracaoCarregamentoVisao: 0,
            alvoBalaoEstoqueEmTransitoAtual: null,
            frameBalaoEstoqueEmTransito: null,
            eventosGlobaisBalaoEstoqueEmTransitoConfigurados: false,
            periodoAtual: 12,
            carregandoSkusPesquisaListas: false,
        });

        const exposedNames = [
            'LS_COL_WIDTHS_KEY', 'tenantKey', 'LS_HIDDEN_SKUS_KEY', 'LS_PEDIDOS_COL_WIDTHS_KEY',
            'CHAVES_COLUNAS_PEDIDOS', 'LARGURAS_PADRAO_COLUNAS_PEDIDOS', 'STATUS_LISTA_OPCOES',
            'largurasColunas', 'largurasColunasPedidos', 'chavesColunasAtuais', 'colElementsAtuais',
            'colElementsPedidos', 'lojaSelecionada', 'lojasDisponiveis', 'skusOcultosSet',
            'itensVisaoAtual', 'comprasSugeridasEditadas', 'colunasMesesAtuais', 'abaAtual',
            'filtroSkuAtual', 'listasPedidosResumo', 'listaPedidoAtual', 'geracaoCarregamentoVisao',
            'alvoBalaoEstoqueEmTransitoAtual', 'frameBalaoEstoqueEmTransito',
            'eventosGlobaisBalaoEstoqueEmTransitoConfigurados', 'periodoAtual',
            'carregandoSkusPesquisaListas',
        ];
        const readonlyNames = [
            'LS_COL_WIDTHS_KEY', 'tenantKey', 'LS_HIDDEN_SKUS_KEY', 'LS_PEDIDOS_COL_WIDTHS_KEY',
            'CHAVES_COLUNAS_PEDIDOS', 'LARGURAS_PADRAO_COLUNAS_PEDIDOS', 'STATUS_LISTA_OPCOES',
        ];
        app.exposeState(exposedNames, readonlyNames);
        return { state };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
