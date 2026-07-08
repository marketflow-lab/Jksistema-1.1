document.querySelectorAll('.tab-button').forEach((button) => {
    button.addEventListener('click', () => ativarAba(button.dataset.tab));
});
statusFiltro.addEventListener('change', carregarPerguntas);
btnRecarregar.addEventListener('click', async () => {
    await carregarPerguntas();
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
        state.treinamentoEscopoLoja = String(aiTrainingScope.value || '').trim();
        state.treinamentoCarregado = false;
        limparChatTreinamento();
        carregarTreinamentoAI(true);
    });
}
aiTrainingSku.addEventListener('change', () => {
    salvarNotasSkuTreinamentoAtual();
    renderizarSkuTreinamentoInfo();
    renderizarNotasSkuTreinamento();
});
aiTrainingTypeTabs.forEach((button) => {
    button.addEventListener('click', () => trocarTipoTreinamento(button.dataset.trainingType));
});
btnAiTrainingAdicionarExemplo.addEventListener('click', adicionarExemploTreinamento);
btnAiTrainingSalvar.addEventListener('click', salvarTreinamentoAI);
btnAiTrainingSimular.addEventListener('click', simularTreinamentoAI);
aiTrainingPergunta.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        simularTreinamentoAI();
    }
});

carregarLojas();
