if (!verificarSessao()) {
    // verificarSessao redireciona quando nao ha token.
} else {
    const permissoes = safeJsonParse(localStorage.getItem('permissions') || '{}', {});
    if (!(permissoes.full === true || permissoes.mercado_full === true)) {
        lojasMl = [];
        lojaSelecionada = '';
        variacoesAbertas.clear();
        renderLojasMl();
        renderResumo([]);
        renderCoberturaFull([]);
        renderEnviarFull([]);
        renderTransitoFull();
        setTabelaMensagem('Sem permissao para exibir os anuncios Full.');
        setStatus('Seu usuario nao tem permissao para acessar o modulo Full.', 'error');
    } else {
        carregarLojasMl();
    }
}

buscaEl.addEventListener('input', filtrar);
ordenarEl.addEventListener('change', filtrar);
tbody.addEventListener('click', (event) => {
    const botao = event.target.closest('[data-variation-key]');
    if (!botao) return;
    const chave = botao.dataset.variationKey || '';
    if (!chave) return;
    if (variacoesAbertas.has(chave)) {
        variacoesAbertas.delete(chave);
    } else {
        variacoesAbertas.add(chave);
    }
    filtrar();
});
document.querySelectorAll('[data-account-cards]').forEach(container => {
    container.addEventListener('click', (event) => {
        const card = event.target.closest('[data-account-card]');
        if (!card) return;
        selecionarContaFull(card.dataset.accountTab || container.dataset.accountCards || 'estoque', card.dataset.conta || card.dataset.loja || '');
    });
});
tabButtons.forEach(btn => {
    btn.addEventListener('click', () => trocarAbaFull(btn.dataset.fullTab || 'estoque'));
});
if (btnAplicarVendasFull) {
    btnAplicarVendasFull.addEventListener('click', () => carregarVendasFull(true));
}
if (btnAplicarEnviarFull) {
    btnAplicarEnviarFull.addEventListener('click', () => carregarEnviarFull(true));
}
if (btnAplicarAbcFull) {
    btnAplicarAbcFull.addEventListener('click', () => carregarCurvaAbcFull(true));
}
if (btnAtualizarTransitoFull) {
    btnAtualizarTransitoFull.addEventListener('click', () => carregarTransitoFull(true));
}
if (btnTransitoSelectFiles && transitoFileInput) {
    btnTransitoSelectFiles.addEventListener('click', () => transitoFileInput.click());
    transitoFileInput.addEventListener('change', () => {
        uploadTransitoPdfs(transitoFileInput.files);
        transitoFileInput.value = '';
    });
}
if (transitoDropZone) {
    transitoDropZone.addEventListener('click', (event) => {
        if (event.target.closest('button')) return;
        transitoFileInput?.click();
    });
    ['dragenter', 'dragover'].forEach(evt => {
        transitoDropZone.addEventListener(evt, event => {
            event.preventDefault();
            transitoDropZone.classList.add('dragover');
        });
    });
    ['dragleave', 'drop'].forEach(evt => {
        transitoDropZone.addEventListener(evt, event => {
            event.preventDefault();
            transitoDropZone.classList.remove('dragover');
        });
    });
    transitoDropZone.addEventListener('drop', event => uploadTransitoPdfs(event.dataTransfer?.files));
}
if (btnTransitoManual) btnTransitoManual.addEventListener('click', () => abrirManualTransito());
if (btnTransitoSalvarManual) btnTransitoSalvarManual.addEventListener('click', salvarManualTransito);
if (btnTransitoCancelarManual) btnTransitoCancelarManual.addEventListener('click', fecharManualTransito);
if (btnTransitoMesAnterior) {
    btnTransitoMesAnterior.addEventListener('click', () => {
        transitoCalendarDate = new Date(transitoCalendarDate.getFullYear(), transitoCalendarDate.getMonth() - 1, 1);
        renderCalendarioTransito();
        carregarCalendarioComercialTransito();
    });
}
if (btnTransitoProximoMes) {
    btnTransitoProximoMes.addEventListener('click', () => {
        transitoCalendarDate = new Date(transitoCalendarDate.getFullYear(), transitoCalendarDate.getMonth() + 1, 1);
        renderCalendarioTransito();
        carregarCalendarioComercialTransito();
    });
}
if (transitoCalendarGrid) {
    transitoCalendarGrid.addEventListener('click', event => {
        const tag = event.target.closest('[data-transito-open]');
        if (!tag) return;
        const item = transitoItemPorId(tag.dataset.transitoOpen);
        if (item) mostrarProdutosTransito(item);
    });
}
if (transitoBusca) transitoBusca.addEventListener('input', renderInativosTransito);
if (btnTransitoSortEnvio) {
    btnTransitoSortEnvio.addEventListener('click', () => {
        transitoSort = 'envio';
        btnTransitoSortEnvio.classList.add('sales-primary');
        btnTransitoSortData?.classList.remove('sales-primary');
        renderInativosTransito();
    });
}
if (btnTransitoSortData) {
    btnTransitoSortData.addEventListener('click', () => {
        transitoSort = 'data';
        btnTransitoSortData.classList.add('sales-primary');
        btnTransitoSortEnvio?.classList.remove('sales-primary');
        renderInativosTransito();
    });
}
if (transitoInativosList) {
    transitoInativosList.addEventListener('click', event => {
        const btn = event.target.closest('[data-transito-action]');
        if (!btn) return;
        const card = btn.closest('[data-transito-id]');
        if (!card) return;
        acaoTransito(card.dataset.transitoId, btn.dataset.transitoAction);
    });
}
abcOrderInputs.forEach(input => {
    input.addEventListener('change', () => {
        abcOrder = input.value === 'valor' ? 'valor' : 'quantidade';
        periodoCurvaAbc().forEach(periodo => aplicarClasseAbc(abcRows, periodo.key));
        renderCurvaAbc();
    });
});
if (btnSortCoverageDays) {
    btnSortCoverageDays.addEventListener('click', () => {
        coberturaSort = 'dias';
        btnSortCoverageDays.classList.add('active');
        if (btnSortCoverageSales) btnSortCoverageSales.classList.remove('active');
        renderTabelaCobertura();
    });
}
if (btnSortCoverageSales) {
    btnSortCoverageSales.addEventListener('click', () => {
        coberturaSort = 'venda';
        btnSortCoverageSales.classList.add('active');
        if (btnSortCoverageDays) btnSortCoverageDays.classList.remove('active');
        renderTabelaCobertura();
    });
}
if (btnExportCoverageFull) {
    btnExportCoverageFull.addEventListener('click', exportarCoberturaFull);
}
if (fullCoverageChipsEl) {
    fullCoverageChipsEl.addEventListener('click', (event) => {
        const btn = event.target.closest('[data-coverage-filter]');
        if (!btn) return;
        aplicarFiltroCobertura(btn.dataset.coverageFilter || 'todos');
    });
}
[coverageRevenueStackEl, coverageTurnoverStackEl].forEach(el => {
    if (!el) return;
    el.addEventListener('click', (event) => {
        const btn = event.target.closest('[data-coverage-filter]');
        if (!btn) return;
        aplicarFiltroCobertura(btn.dataset.coverageFilter || 'todos');
    });
});
if (fullCoverageTableBodyEl) {
    fullCoverageTableBodyEl.addEventListener('click', (event) => {
        const btn = event.target.closest('[data-coverage-action]');
        if (!btn) return;
        executarAcaoCobertura(btn.dataset.coverageAction || '', btn.dataset.sku || '', btn.dataset.mlb || '');
    });
}
if (fullAbcTableBodyEl) {
    fullAbcTableBodyEl.addEventListener('click', (event) => {
        const btn = event.target.closest('[data-coverage-action]');
        if (!btn) return;
        executarAcaoCobertura(btn.dataset.coverageAction || '', btn.dataset.sku || '', btn.dataset.mlb || '');
    });
}
if (btnCoverageSummary && btnCoverageDetails && coverageDetailTable) {
    btnCoverageSummary.addEventListener('click', () => {
        btnCoverageSummary.classList.add('active');
        btnCoverageDetails.classList.remove('active');
        coverageDetailTable.hidden = true;
    });
    btnCoverageDetails.addEventListener('click', () => {
        btnCoverageDetails.classList.add('active');
        btnCoverageSummary.classList.remove('active');
        coverageDetailTable.hidden = false;
    });
    coverageDetailTable.hidden = true;
}
btnAtualizar.addEventListener('click', () => {
    if (abaAtiva === 'vendas') {
        carregarVendasFull(true);
        return;
    }
    if (abaAtiva === 'enviar') {
        carregarEnviarFull(true);
        return;
    }
    if (abaAtiva === 'abc') {
        carregarCurvaAbcFull(true);
        return;
    }
    if (abaAtiva === 'transito') {
        carregarTransitoFull(true);
        return;
    }
    if (lojaSelecionada) {
        carregarAnunciosLoja(lojaSelecionada, true);
        return;
    }
    carregarLojasMl(true);
});
