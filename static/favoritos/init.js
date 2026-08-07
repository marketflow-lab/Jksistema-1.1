(function () {
    'use strict';

    function inicializarFavoritosPagina() {
        favoritosTableLayout = window.FavoritosV2.execution.publicApi.carregarLayoutTabelasFavoritos();
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
            favMlSkuSidebarSearchEl.addEventListener('input', window.FavoritosV2.execution.publicApi.renderizarFavoritosSkuSidebar);
        }
        if (histMlSkuSidebarSearchEl) {
            histMlSkuSidebarSearchEl.addEventListener('input', renderizarHistoricoSkuSidebar);
        }
        if (mlSkuSelectAllEl) {
            mlSkuSelectAllEl.addEventListener('click', alternarSelecaoTodosSkuSidebar);
        }
        const fazerFavoritosSkusSelecionadosCompleto = window.FavoritosV2.execution.publicApi.fazerFavoritosSkusSelecionados;
        const executarFavoritos = () => fazerFavoritosSkusSelecionadosCompleto();
        if (mlSkuFazerFavoritosEl) {
            mlSkuFazerFavoritosEl.addEventListener('click', executarFavoritos);
        }
        carregarPreferenciaUsarIaFavoritos();
        if (mlSkuUsarIaFavoritosEl) {
            mlSkuUsarIaFavoritosEl.addEventListener('change', salvarPreferenciaUsarIaFavoritos);
        }
        if (mlSkuCancelarFavoritosEl) {
            mlSkuCancelarFavoritosEl.addEventListener('click', window.FavoritosV2.searchRanking.publicApi.control.cancelarFavoritosEmExecucao);
        }
        if (mlSkuPausarFavoritosEl) {
            mlSkuPausarFavoritosEl.addEventListener('click', window.FavoritosV2.execution.publicApi.pausarFavoritosJobAtual);
        }
        if (mlSkuRetomarFavoritosEl) {
            mlSkuRetomarFavoritosEl.addEventListener('click', window.FavoritosV2.execution.publicApi.retomarFavoritosJobAtual);
        }
        if (favMlEfetivarBtnEl) {
            favMlEfetivarBtnEl.addEventListener('click', window.FavoritosV2.promotionEffectuation.publicApi.execution.efetivarFavoritosMercadoLivreAprovados);
        }
        if (favMlPromocaoBtnEl) {
            favMlPromocaoBtnEl.addEventListener('click', () => window.FavoritosV2.promotionEffectuation.publicApi.options.escolherPromocaoFavoritosSkuAtual({ mostrarStatus: true }));
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
            favMlEfetivarOutrasContasEl.addEventListener('change', window.FavoritosV2.promotionEffectuation.publicApi.execution.atualizarPainelEfetivarFavoritos);
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
            mlWorkModalCloseEl.addEventListener('click', () => {
                const fecharLoginPendente = window.__JK_FAVORITOS_LOGIN_CLOSE_HANDLER__;
                if (typeof fecharLoginPendente === 'function') {
                    fecharLoginPendente();
                    return;
                }
                fecharBalaoResultadosMl();
            });
        }
        if (mlWorkModalCancelEl) {
            mlWorkModalCancelEl.addEventListener('click', window.FavoritosV2.searchRanking.publicApi.control.cancelarFavoritosEmExecucao);
        }
        if (mlWorkModalPauseEl) {
            mlWorkModalPauseEl.addEventListener('click', window.FavoritosV2.execution.publicApi.pausarFavoritosJobAtual);
        }
        if (mlWorkModalResumeEl) {
            mlWorkModalResumeEl.addEventListener('click', window.FavoritosV2.execution.publicApi.retomarFavoritosJobAtual);
        }

        let navegadorMlEncerradoAoSairFavoritos = false;
        const encerrarNavegadorMlAoSairFavoritos = () => {
            if (navegadorMlEncerradoAoSairFavoritos) return;
            navegadorMlEncerradoAoSairFavoritos = true;
            if (typeof ocultarNavegadorMlShellDefinitivo === 'function') {
                ocultarNavegadorMlShellDefinitivo({
                    descarregarConteudo: true,
                    preserveAvantProSession: true,
                    reason: 'favoritos-pagehide'
                });
            }
        };
        window.addEventListener('message', event => {
            const data = event && event.data ? event.data : {};
            if (data && data.channel === 'jk-shell-history-back') encerrarNavegadorMlAoSairFavoritos();
        });
        window.addEventListener('pageshow', event => {
            if (event && event.persisted) navegadorMlEncerradoAoSairFavoritos = false;
        });
        window.addEventListener('pagehide', encerrarNavegadorMlAoSairFavoritos);

        mlUrlInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') abrirMercadoLivreNoPrograma();
        });

        obterCamposPesquisaAvulsaMl().forEach(item => {
            item.input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') window.FavoritosV2.execution.publicApi.rankearAvulsoMercadoLivre();
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
        window.FavoritosV2.execution.publicApi.inicializarLarguraTabelasFavoritos();
        if (typeof window.FavoritosV2.searchRanking.publicApi.control.inicializarSincronizacaoWorkerFavoritos === 'function') {
            window.FavoritosV2.searchRanking.publicApi.control.inicializarSincronizacaoWorkerFavoritos();
        }
        window.FavoritosV2.execution.publicApi.atualizarTabelasFavoritosEditaveis();
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

    const skuReady = window.__FAVORITOS_SKU_READY__;
    const browserReady = window.__FAVORITOS_ML_BROWSER_READY__;
    const layoutReady = window.__FAVORITOS_TABELAS_LAYOUT_READY__;
    const promotionEffectuationReady = window.__FAVORITOS_PROMOCOES_EFETIVACAO_READY__;
    const componentReadiness = [skuReady, browserReady, layoutReady, promotionEffectuationReady]
        .filter(ready => ready && typeof ready.then === 'function');

    if (componentReadiness.length) {
        Promise.all(componentReadiness)
            .then(inicializarFavoritosPagina)
            .catch(error => {
                console.error('[Favoritos] Falha ao inicializar componentes:', error);
                if (typeof setBrowserStatus === 'function') {
                    setBrowserStatus('Falha ao carregar os componentes do Favoritos.');
                }
            });
    } else {
        inicializarFavoritosPagina();
    }
})();
