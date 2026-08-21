(function (global) {
    'use strict';

    const cadastro = global.JKCadastro;
    const required = ['runtime', 'core', 'produtos', 'custos-mlb', 'actions'];
    if (!cadastro || required.some(component => !cadastro.components.has(component))) {
        throw new Error('Componentes da tela principal do Cadastro incompletos.');
    }
    if (cadastro.__mainInitialized) return;

    const { elements } = cadastro.runtime;
    const tabela = cadastro.produtosTabela;
    const secondary = cadastro.custosMlb;
    const actions = cadastro.actions;

    cadastro.__mainInitialized = true;
    tabela.vincularNavegacaoLinhas();
    elements.filtro.addEventListener('input', () => tabela.renderTabela());
    elements.filtroCustos.addEventListener('input', () => secondary.renderCustos(true));
    elements.lojaCustosSelect.addEventListener('change', () => secondary.renderCustos(true));
    elements.tabProdutos.addEventListener('click', () => secondary.trocarAba('produtos'));
    elements.tabCustos.addEventListener('click', () => secondary.trocarAba('custos'));
    elements.skuSelect.addEventListener('change', () => secondary.renderDetalhesMlbPorSku(elements.skuSelect.value));
    elements.btnAtualizar.addEventListener('click', actions.carregarProdutos);
    elements.btnSyncNcm.addEventListener('click', actions.sincronizarNcmBackground);
    elements.btnEditarCadastro.addEventListener('click', () => { global.location.href = '/cadastro_editar.html'; });
    elements.btnIncluirCadastro.addEventListener('click', () => { global.location.href = '/cadastro_incluir.html'; });
    elements.btnImportarColunas.addEventListener('click', () => elements.inputImportarColunas.click());
    elements.btnAtualizarCustosImpostos.addEventListener('click', () => elements.inputAtualizarCustosImpostos.click());
    elements.inputImportarColunas.addEventListener('change', () => {
        const arquivo = elements.inputImportarColunas.files && elements.inputImportarColunas.files[0];
        if (arquivo) actions.importarColunasPorSku(arquivo);
    });
    elements.inputAtualizarCustosImpostos.addEventListener('change', () => {
        const arquivo = elements.inputAtualizarCustosImpostos.files && elements.inputAtualizarCustosImpostos.files[0];
        if (arquivo) actions.atualizarCustosImpostosPorSku(arquivo);
    });
    actions.inicializarNcm();
    actions.carregarProdutos();
})(window);
