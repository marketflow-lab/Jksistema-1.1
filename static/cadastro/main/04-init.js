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
    let syncPendente = false;
    let recarregandoSync = false;
    let fornecedorEditado = false;
    elements.fornecedorForm?.addEventListener('input', () => { fornecedorEditado = true; });
    elements.fornecedorForm?.addEventListener('reset', () => {
        fornecedorEditado = false;
        setTimeout(atualizarCadastroRecebido, 0);
    });
    global.jkCadastroPodeReceberSync = () => {
        const state = cadastro.runtime.state;
        const active = document.activeElement;
        return !state.carregandoProdutos && !state.importacaoCatalogoAtiva
            && !state.importacaoCatalogoAplicando && !state.importacaoCatalogoCancelando
            && !state.sincronizacaoArquivoAtiva && !fornecedorEditado
            && !elements.fornecedorId?.value
            && !(active && (active.isContentEditable || active.closest?.('#fornecedorForm')));
    };
    async function atualizarCadastroRecebido() {
        if (!syncPendente || recarregandoSync || !global.jkCadastroPodeReceberSync()) return;
        syncPendente = false;
        recarregandoSync = true;
        let sucesso = false;
        try {
            sucesso = await actions.inicializarLojas() && await actions.carregarProdutos();
            if (!sucesso) throw new Error('cadastro_reload_failed');
        } catch (_error) {
            syncPendente = true;
            cadastro.core.setStatus('Dados recebidos. Atualize a lista para conferir o cadastro.', 'error');
        } finally {
            recarregandoSync = false;
            // Um segundo recebimento durante a recarga também precisa aparecer.
            if (sucesso && syncPendente) setTimeout(atualizarCadastroRecebido, 0);
        }
    }
    global.addEventListener?.('jk:machine-sync-updated', event => {
        const scopes = Array.isArray(event.detail?.received_scopes) ? event.detail.received_scopes : [];
        if (!scopes.some(scope => ['cadastro', 'lojas_integracoes'].includes(scope))) return;
        syncPendente = true;
        void atualizarCadastroRecebido();
    });
    global.addEventListener?.('focus', () => { void atualizarCadastroRecebido(); });
    global.addEventListener?.('beforeunload', actions.cancelarCarregamentoProdutos);
    document.addEventListener('focusout', () => { setTimeout(atualizarCadastroRecebido, 0); });
    global.addEventListener?.('jk:cadastro-operation-finished', () => { void atualizarCadastroRecebido(); });

    async function iniciar() {
        actions.inicializarNcm();
        fornecedores.init();
        importacoesCatalogos.init();
        try {
            if (await actions.inicializarLojas()) await actions.carregarProdutos();
        } catch (error) {
            cadastro.core.setStatus(`Erro ao carregar lojas: ${error.message}`, 'error');
        } finally { void atualizarCadastroRecebido(); }
    }
    iniciar();
})(window);
