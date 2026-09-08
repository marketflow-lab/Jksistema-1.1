document.querySelectorAll('.tab-button').forEach((button) => {
    button.addEventListener('click', () => ativarAba(button.dataset.tab));
});
statusFiltro.addEventListener('change', () => carregarPerguntas(1));
btnRecarregar.addEventListener('click', async () => {
    await carregarPerguntas(state.paginaPerguntas || 1, { forcar: true });
    carregarContadoresNotificacoes(true);
});
posVendaDias.addEventListener('change', () => {
    resetarPaginacaoPosVenda();
    carregarPosVenda(true);
    carregarContadoresNotificacoes(true);
});
btnPosVendaBuscar.addEventListener('click', () => {
    resetarPaginacaoPosVenda();
    carregarPosVenda(true);
});
btnPosVendaLimparBusca.addEventListener('click', () => {
    if (posVendaBusca) posVendaBusca.value = '';
    resetarPaginacaoPosVenda();
    carregarPosVenda(true);
});
posVendaBusca.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
        event.preventDefault();
        resetarPaginacaoPosVenda();
        carregarPosVenda(true);
    }
});
posVendaNaoLidas.addEventListener('change', () => {
    resetarPaginacaoPosVenda();
    carregarPosVenda(true);
});
btnPosVendaRecarregar.addEventListener('click', () => {
    resetarPaginacaoPosVenda();
    carregarPosVenda(true);
    carregarContadoresNotificacoes(true);
});
mediacaoDias.addEventListener('change', () => {
    resetarMediacoes();
    carregarMediacoes(true);
});
btnMediacaoBuscar.addEventListener('click', () => {
    resetarMediacoes();
    carregarMediacoes(true);
});
btnMediacaoLimparBusca.addEventListener('click', () => {
    if (mediacaoBusca) mediacaoBusca.value = '';
    resetarMediacoes();
    carregarMediacoes(true);
});
mediacaoBusca.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
        event.preventDefault();
        resetarMediacoes();
        carregarMediacoes(true);
    }
});
btnMediacaoRecarregar.addEventListener('click', () => {
    resetarMediacoes();
    carregarMediacoes(true);
});
if (aiTrainingScope) {
    aiTrainingScope.addEventListener('change', () => {
        const loja = lojasMercadoLivreConectadas().find((item) => String(item.store_id || '') === aiTrainingScope.value);
        if (loja) selecionarLoja(loja.nome);
    });
}
aiTrainingSku.addEventListener('change', () => {
    renderizarSkuTreinamentoInfo();
    renderizarNotasSkuTreinamento();
    renderizarListaSkusTreinamento();
});
if (aiTrainingSkuSearch) {
    aiTrainingSkuSearch.addEventListener('input', renderizarListaSkusTreinamento);
}
btnAiTrainingAdicionarGeral?.addEventListener('click', abrirEditorOrientacoesGerais);
btnAiTrainingEditarGerais?.addEventListener('click', abrirEditorOrientacoesGerais);
btnAiTrainingCancelarGerais?.addEventListener('click', () => fecharEditorOrientacoesGerais(true));
btnAiTrainingSalvarGerais?.addEventListener('click', salvarOrientacoesGeraisTreinamento);
btnAiTrainingFecharSku?.addEventListener('click', fecharBalaoSkuTreinamento);
btnAiTrainingCancelarSku?.addEventListener('click', () => descartarEdicaoTreinamento('sku'));
btnAiTrainingEditarSku?.addEventListener('click', editarOrientacaoSkuTreinamento);
btnAiTrainingSalvarSku?.addEventListener('click', salvarOrientacaoSkuTreinamento);
aiTrainingSkuPopover?.addEventListener('click', (event) => {
    if (event.target === aiTrainingSkuPopover) fecharBalaoSkuTreinamento();
});
document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !aiTrainingSkuPopover?.classList.contains('hidden')) {
        fecharBalaoSkuTreinamento();
    }
});
aiTrainingTypeTabs.forEach((button) => {
    button.addEventListener('click', () => trocarTipoTreinamento(button.dataset.trainingType));
});
btnAiTrainingAdicionarExemplo.addEventListener('click', adicionarExemploTreinamento);
btnAiTrainingSalvar.addEventListener('click', () => salvarTreinamentoAI().catch(() => {}));
btnAiTrainingSimular.addEventListener('click', simularTreinamentoAI);
aiTrainingPergunta.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        simularTreinamentoAI();
    }
});

iniciarSincronizacaoTreinamento();
carregarLojas();
