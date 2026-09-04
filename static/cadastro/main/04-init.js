(function (global) {
    'use strict';

    const cadastro = global.JKCadastro;
    const required = ['runtime', 'core', 'produtos', 'custos-mlb', 'actions', 'fornecedores', 'importacoes-catalogos'];
    if (!cadastro || required.some(component => !cadastro.components.has(component))) {
        throw new Error('Componentes da tela principal do Cadastro incompletos.');
    }
    if (cadastro.__mainInitialized) return;

    const { elements } = cadastro.runtime;
    const tabela = cadastro.produtosTabela;
    const secondary = cadastro.custosMlb;
    const actions = cadastro.actions;
    const fornecedores = cadastro.fornecedores;
    const importacoesCatalogos = cadastro.importacoesCatalogos;

    cadastro.__mainInitialized = true;
    tabela.vincularNavegacaoLinhas();
    elements.filtro.addEventListener('input', () => tabela.renderTabela());
    elements.filtroCustos.addEventListener('input', () => secondary.renderCustos(true));
    elements.lojaCustosSelect.addEventListener('change', () => secondary.renderCustos(true));
    elements.cadastroLojaSelect.addEventListener('change', async () => {
        try {
            await actions.selecionarLoja(elements.cadastroLojaSelect.value);
        } catch (error) {
            cadastro.core.setStatus(`Erro ao trocar de loja: ${error.message}`, 'error');
        }
    });
    elements.tabProdutos.addEventListener('click', () => secondary.trocarAba('produtos'));
    elements.tabCustos.addEventListener('click', () => secondary.trocarAba('custos'));
    elements.tabFornecedores.addEventListener('click', () => secondary.trocarAba('fornecedores'));
    elements.tabImportadorLoja.addEventListener('click', () => secondary.trocarAba('importadorLoja'));
    elements.skuSelect.addEventListener('change', () => secondary.renderDetalhesMlbPorSku(elements.skuSelect.value));
    elements.btnAtualizar.addEventListener('click', actions.carregarProdutos);
    elements.btnSyncNcm.addEventListener('click', actions.sincronizarNcmBackground);
    elements.btnEditarCadastro.addEventListener('click', () => {
        if (cadastro.runtime.state.storeIdSelecionado) global.location.href = global.JKCadastroStore.urlPagina('/cadastro_editar.html', cadastro.runtime.state.storeIdSelecionado);
    });
    elements.btnIncluirCadastro.addEventListener('click', () => {
        if (cadastro.runtime.state.storeIdSelecionado) global.location.href = global.JKCadastroStore.urlPagina('/cadastro_incluir.html', cadastro.runtime.state.storeIdSelecionado);
    });
    elements.btnImportarColunas.addEventListener('click', () => elements.inputImportarColunas.click());
    elements.btnImportarBling.addEventListener('click', () => importacoesCatalogos.abrir('bling'));
    elements.btnImportarMercadoLivre.addEventListener('click', () => importacoesCatalogos.abrir('mercadolivre'));
    elements.btnAtualizarCustosImpostos.addEventListener('click', () => elements.inputAtualizarCustosImpostos.click());
    elements.inputImportarColunas.addEventListener('change', () => {
        const arquivo = elements.inputImportarColunas.files && elements.inputImportarColunas.files[0];
        if (arquivo) actions.importarColunasPorSku(arquivo);
    });
    elements.inputAtualizarCustosImpostos.addEventListener('change', () => {
        const arquivo = elements.inputAtualizarCustosImpostos.files && elements.inputAtualizarCustosImpostos.files[0];
        if (arquivo) actions.atualizarCustosImpostosPorSku(arquivo);
    });
    async function iniciar() {
        actions.inicializarNcm();
        fornecedores.init();
        importacoesCatalogos.init();
        try {
            if (await actions.inicializarLojas()) await actions.carregarProdutos();
        } catch (error) {
            cadastro.core.setStatus(`Erro ao carregar lojas: ${error.message}`, 'error');
        }
    }
    iniciar();
})(window);
