(function (global) {
    'use strict';

    const cadastro = global.JKCadastro = global.JKCadastro || {};
    if (cadastro.__runtimeInitialized) return;

    const byId = id => document.getElementById(id);
    const elements = {
        status: byId('status'),
        statusNcmGlobal: byId('statusNcmGlobal'),
        cadastroLojaBotoes: byId('cadastroLojaBotoes'),
        cadastroLojaSelect: byId('cadastroLojaSelect'),
        cadastroLojaAviso: byId('cadastroLojaAviso'),
        btnAtualizar: byId('btnAtualizar'),
        btnSyncNcm: byId('btnSyncNcm'),
        btnEditarCadastro: byId('btnEditarCadastro'),
        btnIncluirCadastro: byId('btnIncluirCadastro'),
        btnImportarColunas: byId('btnImportarColunas'),
        btnImportarBling: byId('btnImportarBling'),
        btnImportarMercadoLivre: byId('btnImportarMercadoLivre'),
        inputImportarColunas: byId('inputImportarColunas'),
        filtro: byId('filtro'),
        skuSelect: byId('skuSelect'),
        tCols: byId('tCols'),
        tHead: byId('tHead'),
        tBody: byId('tBody'),
        tBodyMlb: byId('tBodyMlb'),
        tabProdutos: byId('tabProdutos'),
        tabCustos: byId('tabCustos'),
        tabFornecedores: byId('tabFornecedores'),
        tabImportadorLoja: byId('tabImportadorLoja'),
        painelProdutos: byId('painelProdutos'),
        painelCustos: byId('painelCustos'),
        painelFornecedores: byId('painelFornecedores'),
        painelImportadorLoja: byId('painelImportadorLoja'),
        fornecedorStatus: byId('fornecedorStatus'),
        fornecedorForm: byId('fornecedorForm'),
        fornecedorFormTitulo: byId('fornecedorFormTitulo'),
        fornecedorId: byId('fornecedorId'),
        fornecedorNomeEmpresa: byId('fornecedorNomeEmpresa'),
        fornecedorNomeContato: byId('fornecedorNomeContato'),
        fornecedorEnderecoEmpresa: byId('fornecedorEnderecoEmpresa'),
        fornecedorTelefone: byId('fornecedorTelefone'),
        fornecedorEmail: byId('fornecedorEmail'),
        fornecedorMoedaPagamento: byId('fornecedorMoedaPagamento'),
        fornecedorContaBeneficiario: byId('fornecedorContaBeneficiario'),
        fornecedorSwift: byId('fornecedorSwift'),
        fornecedorPaisRegiao: byId('fornecedorPaisRegiao'),
        fornecedorNomeBeneficiario: byId('fornecedorNomeBeneficiario'),
        fornecedorEnderecoBeneficiario: byId('fornecedorEnderecoBeneficiario'),
        fornecedorBancoBeneficiario: byId('fornecedorBancoBeneficiario'),
        fornecedorEnderecoBanco: byId('fornecedorEnderecoBanco'),
        fornecedorCodigoBanco: byId('fornecedorCodigoBanco'),
        fornecedorCodigoAgencia: byId('fornecedorCodigoAgencia'),
        fornecedorObservacaoPagamento: byId('fornecedorObservacaoPagamento'),
        btnSalvarFornecedor: byId('btnSalvarFornecedor'),
        btnLimparFornecedor: byId('btnLimparFornecedor'),
        btnAtualizarFornecedores: byId('btnAtualizarFornecedores'),
        listaFornecedores: byId('listaFornecedores'),
        btnAtualizarCustosImpostos: byId('btnAtualizarCustosImpostos'),
        inputAtualizarCustosImpostos: byId('inputAtualizarCustosImpostos'),
        lojaCustosSelect: byId('lojaCustosSelect'),
        filtroCustos: byId('filtroCustos'),
        tBodyCustos: byId('tBodyCustos'),
        paginacaoCustos: byId('paginacaoCustos'),
        paginacao: byId('paginacao'),
        modalImportacaoCatalogo: byId('modalImportacaoCatalogo'),
        importacaoCatalogoTitulo: byId('importacaoCatalogoTitulo'),
        importacaoCatalogoDescricao: byId('importacaoCatalogoDescricao'),
        importacaoCatalogoLoja: byId('importacaoCatalogoLoja'),
        importacaoCatalogoStatus: byId('importacaoCatalogoStatus'),
        importacaoCatalogoProgress: byId('importacaoCatalogoProgress'),
        importacaoCatalogoProgressText: byId('importacaoCatalogoProgressText'),
        importacaoCatalogoNovos: byId('importacaoCatalogoNovos'),
        importacaoCatalogoPreencher: byId('importacaoCatalogoPreencher'),
        importacaoCatalogoInalterados: byId('importacaoCatalogoInalterados'),
        importacaoCatalogoConflitosTotal: byId('importacaoCatalogoConflitosTotal'),
        importacaoCatalogoIgnorados: byId('importacaoCatalogoIgnorados'),
        importacaoCatalogoConflitos: byId('importacaoCatalogoConflitos'),
        importacaoCatalogoConflitosLista: byId('importacaoCatalogoConflitosLista'),
        tBodyImportacaoCatalogo: byId('tBodyImportacaoCatalogo'),
        btnFecharImportacaoCatalogo: byId('btnFecharImportacaoCatalogo'),
        btnCancelarImportacaoCatalogo: byId('btnCancelarImportacaoCatalogo'),
        btnAplicarImportacaoCatalogo: byId('btnAplicarImportacaoCatalogo'),
    };

    const requiredElements = Object.entries(elements).filter(([, element]) => !element);
    if (requiredElements.length) {
        throw new Error(`Cadastro incompleto: ${requiredElements.map(([name]) => name).join(', ')}`);
    }

    const LS_LARGURAS_KEY = 'cadastro_larguras_colunas';
    let largurasColunas = {};
    try {
        largurasColunas = JSON.parse(global.localStorage.getItem(LS_LARGURAS_KEY) || '{}');
    } catch (_error) {
        largurasColunas = {};
    }

    cadastro.__runtimeInitialized = true;
    cadastro.schema = 'jk.cadastro.frontend.v1';
    cadastro.components = cadastro.components || new Set();
    cadastro.internal = cadastro.internal || {};
    cadastro.publicApi = cadastro.publicApi || {};
    cadastro.runtime = {
        constants: Object.freeze({
            itensPorPagina: 50,
            largurasStorageKey: LS_LARGURAS_KEY,
        }),
        elements: Object.freeze(elements),
        state: {
            paginaAtual: 1,
            paginaCustosAtual: 1,
            clientId: null,
            lojas: [],
            storeIdSelecionado: '',
            carregandoProdutos: false,
            carregamentoProdutosProgresso: { concluidas: 0, total: 0 },
            produtosEscopoCarregado: null,
            ultimaAtualizacaoProdutosEm: 0,
            importacaoCatalogoAtiva: false,
            importacaoCatalogoAplicando: false,
            importacaoCatalogoCancelando: false,
            produtos: [],
            fornecedores: [],
            colunasTabela: [],
            colunasTabelaKey: '',
            largurasColunas,
        },
    };
    cadastro.components.add('runtime');
})(window);
