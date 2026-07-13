(function () {
    'use strict';

    function inicializarFavoritosPagina() {
        favoritosTableLayout = carregarLayoutTabelasFavoritos();
        // Bootstrap moved from the former inline script so split files can load safely.
        if (btnSkuIaTodos) {
            btnSkuIaTodos.addEventListener('click', () => {
                skuGerarPesquisasComIa({
                    todos: true,
                    sobrescrever: true,
                    tamanhoLote: 3,
                    botao: btnSkuIaTodos
                });
            });
        }

        if (btnBuscar && termoEl) {
            btnBuscar.addEventListener('click', buscar);
            termoEl.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') buscar();
            });
        }

        if (mlRankingToggleEl) {
            mlRankingToggleEl.addEventListener('click', alternarRankingSidebar);
            aplicarEstadoRankingSidebar(rankingSidebarEstaMinimizado());
        }
        if (mlSidebarResizerEl) {
            aplicarLarguraSidebarRanking(carregarLarguraSidebarRanking());
            mlSidebarResizerEl.addEventListener('pointerdown', iniciarAjusteLarguraSidebar);
        }
        if (mlSkuSidebarToggleEls.length) {
            mlSkuSidebarToggleEls.forEach(botao => {
                botao.addEventListener('click', alternarSkuSidebar);
            });
            aplicarEstadoSkuSidebar(skuSidebarEstaMinimizado());
        }
        if (mlSkuSidebarResizerEl) {
            aplicarLarguraSkuSidebar(carregarLarguraSkuSidebar());
            mlSkuSidebarResizerEl.addEventListener('pointerdown', iniciarAjusteLarguraSkuSidebar);
        }
        if (mlSkuSidebarSearchEl) {
            mlSkuSidebarSearchEl.addEventListener('input', () => {
                mlSkuSidebarFiltro = mlSkuSidebarSearchEl.value || '';
                mlSkuSidebarRenderLimit = ML_SKU_SIDEBAR_PAGE_SIZE;
                renderizarSkuSidebarMercadoLivre();
            });
        }
        if (favMlSkuSidebarSearchEl) {
            favMlSkuSidebarSearchEl.addEventListener('input', renderizarFavoritosSkuSidebar);
        }
        if (histMlSkuSidebarSearchEl) {
            histMlSkuSidebarSearchEl.addEventListener('input', renderizarHistoricoSkuSidebar);
        }
        if (mlSkuSelectAllEl) {
            mlSkuSelectAllEl.addEventListener('click', alternarSelecaoTodosSkuSidebar);
        }
        const fazerFavoritosSkusSelecionadosCompleto = fazerFavoritosSkusSelecionados;
        const executarFavoritos = () => fazerFavoritosSkusSelecionadosCompleto();
        if (mlSkuFazerFavoritosEl) {
            mlSkuFazerFavoritosEl.addEventListener('click', executarFavoritos);
        }
        carregarPreferenciaUsarIaFavoritos();
        if (mlSkuUsarIaFavoritosEl) {
            mlSkuUsarIaFavoritosEl.addEventListener('change', salvarPreferenciaUsarIaFavoritos);
        }
        if (mlSkuCancelarFavoritosEl) {
            mlSkuCancelarFavoritosEl.addEventListener('click', cancelarFavoritosEmExecucao);
        }
        if (mlSkuPausarFavoritosEl) {
            mlSkuPausarFavoritosEl.addEventListener('click', pausarFavoritosJobAtual);
        }
        if (mlSkuRetomarFavoritosEl) {
            mlSkuRetomarFavoritosEl.addEventListener('click', retomarFavoritosJobAtual);
        }
        if (favMlEfetivarBtnEl) {
            favMlEfetivarBtnEl.addEventListener('click', efetivarFavoritosMercadoLivreAprovados);
        }
        if (favMlPromocaoBtnEl) {
            favMlPromocaoBtnEl.addEventListener('click', () => escolherPromocaoFavoritosSkuAtual({ mostrarStatus: true }));
        }
        if (favRankingIncluirAnuncioBtnEl) {
            favRankingIncluirAnuncioBtnEl.addEventListener('click', mostrarFormularioIncluirAnuncioRankingFavoritos);
        }
        if (favRankingIncluirConfirmarBtnEl) {
            favRankingIncluirConfirmarBtnEl.addEventListener('click', () => incluirAnuncioRankingFavoritos());
        }
        if (favRankingIncluirCancelarBtnEl) {
            favRankingIncluirCancelarBtnEl.addEventListener('click', fecharFormularioIncluirAnuncioRankingFavoritos);
        }
        if (favRankingIncluirInputEl) {
            favRankingIncluirInputEl.addEventListener('keydown', ev => {
                if (ev.key === 'Enter') {
                    ev.preventDefault();
                    incluirAnuncioRankingFavoritos();
                } else if (ev.key === 'Escape') {
                    ev.preventDefault();
                    fecharFormularioIncluirAnuncioRankingFavoritos();
                }
            });
        }
        if (favMlEfetivarOutrasContasEl) {
            favMlEfetivarOutrasContasEl.addEventListener('change', atualizarPainelEfetivarFavoritos);
        }
        if (mlHistoricoFavoritosLimparEl) {
            mlHistoricoFavoritosLimparEl.addEventListener('click', limparHistoricoFavoritos);
        }
        if (mlLinksAlinhadosAtualizarEl) {
            mlLinksAlinhadosAtualizarEl.addEventListener('click', renderizarLinksAlinhadosFavoritos);
        }

        garantirSidebarSkuUnico();
        atualizarEstadoSidebarRanking();

        window.addEventListener('resize', agendarAtualizacaoPosicaoNavegadorMlShell);
        window.addEventListener('scroll', agendarAtualizacaoPosicaoNavegadorMlShell, true);
        window.addEventListener('focus', reexibirNavegadorMlShellAoRetornar);
        document.addEventListener('visibilitychange', reexibirNavegadorMlShellAoRetornar);
        if (mlWorkModalCloseEl) {
            mlWorkModalCloseEl.addEventListener('click', fecharBalaoResultadosMl);
        }
        if (mlWorkModalCancelEl) {
            mlWorkModalCancelEl.addEventListener('click', cancelarFavoritosEmExecucao);
        }
        if (mlWorkModalPauseEl) {
            mlWorkModalPauseEl.addEventListener('click', pausarFavoritosJobAtual);
        }
        if (mlWorkModalResumeEl) {
            mlWorkModalResumeEl.addEventListener('click', retomarFavoritosJobAtual);
        }

        mlUrlInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') abrirMercadoLivreNoPrograma();
        });

        obterCamposPesquisaAvulsaMl().forEach(item => {
            item.input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') rankearAvulsoMercadoLivre();
            });
        });

        if (favoritosPlanilhaLojaAtualSalvarEl) {
            favoritosPlanilhaLojaAtualSalvarEl.addEventListener('click', () => {
                const loja = favoritosLojaSelecionadaParaApi(mlSkuLojaSelecionada || skuLojaSelecionada || '');
                favoritosPlanilhasSalvarLoja(loja, favoritosPlanilhaLojaAtualUrlEl ? favoritosPlanilhaLojaAtualUrlEl.value : '', favoritosPlanilhaLojaAtualSalvarEl);
            });
        }
        if (favoritosPlanilhaLojaAtualUrlEl) {
            favoritosPlanilhaLojaAtualUrlEl.addEventListener('keydown', (event) => {
                if (event.key !== 'Enter') return;
                const loja = favoritosLojaSelecionadaParaApi(mlSkuLojaSelecionada || skuLojaSelecionada || '');
                favoritosPlanilhasSalvarLoja(loja, favoritosPlanilhaLojaAtualUrlEl.value, favoritosPlanilhaLojaAtualSalvarEl);
            });
        }

        inicializarBalaoResultadosMl();
        inicializarLarguraTabelasFavoritos();
        if (typeof inicializarSincronizacaoWorkerFavoritos === 'function') {
            inicializarSincronizacaoWorkerFavoritos();
        }
        atualizarTabelasFavoritosEditaveis();
        renderizarVendedoresIgnoradosRanking();
        inicializarSincronizacaoSelecaoFavoritos();
        inicializarSincronizacaoHistoricoFavoritos();
        carregarVendedoresIgnoradosRankingServidor();
        renderizarAnunciosIgnoradosSku();
        carregarAnunciosIgnoradosSkuServidor();
        carregarHistoricoFavoritosServidor();
        favoritosCarregarLojasEntrada();
        setBrowserStatus('Pronto para abrir dentro do programa.');
    }

    const layoutReady = window.__FAVORITOS_TABELAS_LAYOUT_READY__;
    if (layoutReady && typeof layoutReady.then === 'function') {
        layoutReady
            .then(inicializarFavoritosPagina)
            .catch(error => {
                console.error('[Favoritos] Falha ao inicializar layout:', error);
                if (typeof setBrowserStatus === 'function') {
                    setBrowserStatus('Falha ao carregar os componentes do Favoritos.');
                }
            });
    } else {
        inicializarFavoritosPagina();
    }
})();
