(function installJKMediasState(global) {
    'use strict';

    const app = global.JKMedias;
    app.defineModule('state', ['core'], ({ state, modules }) => {
        const parseJsonLocalStorage = modules.core.parseJsonLocalStorage;
        const userData = parseJsonLocalStorage('user_data', null);
        const clientIdAutenticado = String((userData && userData.client_id) || '').trim();
        const tenantKey = String(
            clientIdAutenticado
            || localStorage.getItem('client_id')
            || localStorage.getItem('x_client_id')
            || 'default',
        );
        const usernameNormalizado = String((userData && userData.username) || '').trim().toLowerCase();
        const temEscopoPersistenciaLarguras = Boolean(clientIdAutenticado && usernameNormalizado);

        function hashEscopoPreferencias(valor) {
            let hashA = 0x811c9dc5;
            let hashB = 0x9e3779b9;
            const texto = String(valor || '');
            for (let indice = 0; indice < texto.length; indice += 1) {
                const codigo = texto.charCodeAt(indice);
                hashA = Math.imul(hashA ^ codigo, 0x01000193);
                hashB = Math.imul(hashB ^ codigo, 0x85ebca6b);
                hashB ^= hashB >>> 13;
            }
            return (hashA >>> 0).toString(16).padStart(8, '0')
                + (hashB >>> 0).toString(16).padStart(8, '0');
        }

        const userScopeKey = temEscopoPersistenciaLarguras
            ? hashEscopoPreferencias('jk-medias-widths-v3\u0000' + clientIdAutenticado + '\u0000' + usernameNormalizado)
            : '';
        const hidden = parseJsonLocalStorage('medias_compras_skus_ocultos_' + tenantKey, []);
        const chaveLargurasColunas = userScopeKey
            ? 'medias_compras_larguras_colunas_v3_' + userScopeKey
            : '';
        const chaveLargurasColunasLegado = 'medias_compras_larguras_colunas';
        const PERFIL_LARGURAS_COLUNAS_SCHEMA = 'jk.medias.column-widths.v3';

        const LARGURAS_MINIMAS_COLUNAS = Object.freeze({
            sku: 80,
            foto: 64,
            titulo_anuncio: 200,
            mes: 52,
            total_periodo: 72,
            media_mensal: 56,
            saldo_estoque: 80,
            estoque_transito: 80,
            posicao_estoque: 96,
            cobertura_meses: 92,
            compra_sugerida: 92,
            acao: 72,
        });
        const LARGURAS_PADRAO_COLUNAS = Object.freeze({
            sku: 88,
            foto: 64,
            titulo_anuncio: 220,
            mes: 52,
            total_periodo: 76,
            media_mensal: 64,
            saldo_estoque: 80,
            estoque_transito: 80,
            posicao_estoque: 96,
            cobertura_meses: 92,
            compra_sugerida: 92,
            acao: 100,
        });
        const LARGURAS_MAXIMAS_COLUNAS = Object.freeze({
            sku: 220,
            foto: 80,
            titulo_anuncio: 380,
            mes: 110,
            total_periodo: 150,
            media_mensal: 130,
            saldo_estoque: 150,
            estoque_transito: 150,
            posicao_estoque: 160,
            cobertura_meses: 170,
            compra_sugerida: 160,
            acao: 110,
        });

        Object.assign(state, {
            LS_COL_WIDTHS_KEY: chaveLargurasColunas,
            LS_COL_WIDTHS_LEGACY_KEY: chaveLargurasColunasLegado,
            PERFIL_LARGURAS_COLUNAS_SCHEMA,
            tenantKey,
            userScopeKey,
            temEscopoPersistenciaLarguras,
            LS_HIDDEN_SKUS_KEY: 'medias_compras_skus_ocultos_' + tenantKey,
            LS_PEDIDOS_COL_WIDTHS_KEY: 'medias_pedidos_larguras_colunas_' + tenantKey,
            CHAVES_COLUNAS_PEDIDOS: ['sku', 'foto', 'titulo', 'oem', 'cor_lado', 'link', 'quantidade', 'valor_unidade', 'valor_total', 'cbm', 'peso', 'embalagem', 'acoes'],
            LARGURAS_PADRAO_COLUNAS_PEDIDOS: {
                sku: 96, foto: 92, titulo: 220, oem: 130, cor_lado: 108, link: 140,
                quantidade: 102, valor_unidade: 128, valor_total: 138, cbm: 118,
                peso: 118, embalagem: 150, acoes: 56,
            },
            LARGURAS_MINIMAS_COLUNAS,
            LARGURAS_PADRAO_COLUNAS,
            LARGURAS_MAXIMAS_COLUNAS,
            STATUS_LISTA_OPCOES: ['Lista gerada', 'Em Orçamento', 'Analisando orçamento', 'Pedido Aprovado', 'Pedido emitido', 'Em produção', 'Em trânsito', 'Em desembaraço', 'Recebido', 'Pedido cancelado'],
            largurasColunas: {},
            largurasColunasPedidos: parseJsonLocalStorage('medias_pedidos_larguras_colunas_' + tenantKey, {}),
            chavesColunasAtuais: [],
            colElementsAtuais: [],
            colElementsPedidos: [],
            lojaSelecionada: '__todas',
            storeIdSelecionado: '',
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
            'LS_COL_WIDTHS_KEY', 'LS_COL_WIDTHS_LEGACY_KEY', 'PERFIL_LARGURAS_COLUNAS_SCHEMA', 'tenantKey', 'userScopeKey', 'temEscopoPersistenciaLarguras', 'LS_HIDDEN_SKUS_KEY', 'LS_PEDIDOS_COL_WIDTHS_KEY',
            'CHAVES_COLUNAS_PEDIDOS', 'LARGURAS_PADRAO_COLUNAS_PEDIDOS', 'LARGURAS_MINIMAS_COLUNAS',
            'LARGURAS_PADRAO_COLUNAS', 'LARGURAS_MAXIMAS_COLUNAS', 'STATUS_LISTA_OPCOES',
            'largurasColunas', 'largurasColunasPedidos', 'chavesColunasAtuais', 'colElementsAtuais',
            'colElementsPedidos', 'lojaSelecionada', 'storeIdSelecionado', 'lojasDisponiveis', 'skusOcultosSet',
            'itensVisaoAtual', 'comprasSugeridasEditadas', 'colunasMesesAtuais', 'abaAtual',
            'filtroSkuAtual', 'listasPedidosResumo', 'listaPedidoAtual', 'geracaoCarregamentoVisao',
            'alvoBalaoEstoqueEmTransitoAtual', 'frameBalaoEstoqueEmTransito',
            'eventosGlobaisBalaoEstoqueEmTransitoConfigurados', 'periodoAtual',
            'carregandoSkusPesquisaListas',
        ];
        const readonlyNames = [
            'LS_COL_WIDTHS_KEY', 'LS_COL_WIDTHS_LEGACY_KEY', 'PERFIL_LARGURAS_COLUNAS_SCHEMA', 'tenantKey', 'userScopeKey', 'temEscopoPersistenciaLarguras', 'LS_HIDDEN_SKUS_KEY', 'LS_PEDIDOS_COL_WIDTHS_KEY',
            'CHAVES_COLUNAS_PEDIDOS', 'LARGURAS_PADRAO_COLUNAS_PEDIDOS', 'LARGURAS_MINIMAS_COLUNAS',
            'LARGURAS_PADRAO_COLUNAS', 'LARGURAS_MAXIMAS_COLUNAS', 'STATUS_LISTA_OPCOES',
        ];
        app.exposeState(exposedNames, readonlyNames);
        return { state };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
