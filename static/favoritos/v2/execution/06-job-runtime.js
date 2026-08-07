// Extracted from 07-execucao-render-layout.js lines 1873-2216.
        function montarPayloadFavoritosJob(selecionados, quantidade, opcoesPromocao, usarIaRanking) {
            return {
                loja: favoritosLojaSelecionadaParaApi(mlSkuLojaSelecionada || skuLojaSelecionada || ''),
                quantidade_pesquisas: quantidade,
                usar_ia: !!usarIaRanking,
                max_confirmados_ia: 8,
                opcoes_promocao: window.FavoritosV2.promotionEffectuation.publicApi.options.clonarOpcoesPromocaoFavoritos(opcoesPromocao),
                modo_coleta: 'avantpro_browser',
                selecionados: selecionados.map(item => {
                    const info = window.FavoritosV2.searchRanking.publicApi.search.montarPesquisasFavoritosSku(item, quantidade);
                    return {
                        sku: info.sku,
                        loja: info.loja,
                        titulo: info.titulo,
                        descricao: info.descricao,
                        termos: info.termos,
                        cadastro: info.cadastro
                    };
                })
            };
        }
        function favoritosJobStatusMensagem(status) {
            const etapa = String(status && status.etapa || '').trim();
            const mensagem = String(status && status.mensagem || '').trim();
            const sku = String(status && status.sku_atual || '').trim();
            const indice = Number(status && status.indice) || 0;
            const total = Number(status && status.total) || 0;
            const percentual = Number(status && status.percentual) || 0;
            const partes = [];
            if (total) partes.push(`${Math.min(indice || 0, total)}/${total}`);
            if (percentual) partes.push(`${Math.max(0, Math.min(100, Math.round(percentual)))}%`);
            if (sku) partes.push(`SKU ${sku}`);
            if (etapa) partes.push(etapa);
            if (mensagem) partes.push(mensagem);
            return partes.join(' - ') || 'Favoritos rodando em background.';
        }

        function encontrarSelecionadoFavoritosJob(grupo) {
            const sku = skuChaveSku(grupo && grupo.sku);
            const loja = skuNormalizarLoja(grupo && grupo.loja);
            return (mlFavoritosJobSelecionadosAtual || []).find(item => {
                if (skuChaveSku(item && item.sku) !== sku) return false;
                if (!loja) return true;
                return skuNormalizarLoja(item && item.loja) === loja;
            }) || null;
        }

        function normalizarGrupoFavoritosJob(grupo) {
            if (!grupo || typeof grupo !== 'object') return null;
            const anuncios = Array.isArray(grupo.anuncios) ? grupo.anuncios.filter(Boolean) : [];
            const ordenados = window.FavoritosV2.searchRanking.publicApi.ranking.limitarAnunciosFavoritosRanking(window.FavoritosV2.searchRanking.publicApi.ranking.ordenarAnunciosFavoritosRanking(anuncios, grupo.sku));
            return {
                ...grupo,
                opcoes_promocao: grupo.opcoes_promocao || window.FavoritosV2.promotionEffectuation.publicApi.options.clonarOpcoesPromocaoFavoritos(mlFavoritosOpcoesPromocaoAtual),
                anuncios: ordenados
            };
        }

        function aplicarResultadosParciaisFavoritosJob(status) {
            const grupos = (Array.isArray(status && status.resultados_parciais) ? status.resultados_parciais : [])
                .map(normalizarGrupoFavoritosJob)
                .filter(Boolean);
            grupos.forEach(grupo => {
                guardarResultadoRankingFavorito(grupo);
                registrarHistoricoRankingSkuFavoritosImediato(grupo);
                const itemSelecionado = encontrarSelecionadoFavoritosJob(grupo);
                if (itemSelecionado) desmarcarSkuFavoritosProcessado(itemSelecionado);
            });
            return grupos;
        }

        function agendarRenderFavoritosJob(grupos) {
            mlFavoritosJobGruposRender = Array.isArray(grupos) ? grupos.slice() : [];
            mlFavoritosJobRenderPendente = true;
            if (mlFavoritosJobRenderRaf) return;
            const raf = typeof requestAnimationFrame === 'function'
                ? requestAnimationFrame
                : (callback) => setTimeout(callback, 16);
            mlFavoritosJobRenderRaf = raf(() => {
                mlFavoritosJobRenderRaf = 0;
                if (!mlFavoritosJobRenderPendente) return;
                mlFavoritosJobRenderPendente = false;
                renderizarFavoritosPesquisaResultados(mlFavoritosJobGruposRender);
                mlFavoritosJobUltimaQtdRender = mlFavoritosJobGruposRender.length;
            });
        }

        async function fetchFavoritosJob(url, options = {}) {
            const response = await fetch(url, {
                ...options,
                headers: options.headers || headersJsonAutenticado()
            });
            let data = null;
            try {
                data = await response.json();
            } catch (_err) {}
            if (!response.ok) {
                throw new Error((data && data.detail) || `HTTP ${response.status}`);
            }
            return data || {};
        }

        const FAVORITOS_JOB_PROXIMA_COLETA_PATH = '/proxima-coleta';
        const FAVORITOS_JOB_COLETA_TERMO_PATH = '/coleta-termo';

        async function iniciarWorkerFavoritosAvantProJob(opcoes = {}) {
            if (opcoes.permitirRotinaAntigaAposLoginAvantPro !== true) {
                pararFavoritosAposConfirmacaoAvantProNovaEtapa({
                    manterNavegadorVisivel: false
                });
                return;
            }
            let loginAvantPrimeiraPesquisaPreparado = opcoes.avantLoginConfirmadoPeloUsuario === true;
            while (mlFavoritosEmExecucao && mlFavoritosJobIdAtual && !mlFavoritosCancelado) {
                await window.FavoritosV2.searchRanking.publicApi.control.aguardarControleFavoritos();
                const proxima = await fetchFavoritosJob(`/api/favoritos/jobs/${encodeURIComponent(mlFavoritosJobIdAtual)}${FAVORITOS_JOB_PROXIMA_COLETA_PATH}`, {
                    method: 'GET'
                });
                receberStatusFavoritosJob(proxima);
                if (!proxima.pending) break;
                const destino = proxima.coleta || {};
                if (!destino.termo) throw new Error('Backend nao retornou termo para coleta visual.');
                const loginAvantAntesDaColeta = !loginAvantPrimeiraPesquisaPreparado;
                let avantLoginPrePesquisaConfirmado = false;
                if (loginAvantAntesDaColeta) {
                    if (typeof prepararAvantProAntesDaPesquisaFavoritos !== 'function') {
                        throw new Error('Nao consegui preparar o Avant Pro antes da pesquisa. Reabra a tela de Favoritos e tente novamente.');
                    }
                    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`SKU ${destino.sku || ''}: preparando Avant Pro antes da pesquisa.`, {
                        manterNavegadorVisivel: true,
                        larga: true,
                        titulo: 'Preparando Avant Pro'
                    });
                    if (typeof abrirMercadoLivreNoPrograma === 'function') {
                        if (typeof mlUrlInput !== 'undefined' && mlUrlInput) {
                            mlUrlInput.value = typeof ML_DEFAULT_URL !== 'undefined'
                                ? ML_DEFAULT_URL
                                : 'https://www.mercadolivre.com.br/';
                        }
                        await abrirMercadoLivreNoPrograma({
                            titulo: 'Fazendo Favorito! Aguarde...',
                            subtitulo: `Preparando Avant Pro - SKU ${destino.sku || ''}`,
                            mostrarFavoritos: true,
                            browserCompleto: true,
                            forcarExibicao: true
                        });
                    }
                    const preparacaoAvant = await prepararAvantProAntesDaPesquisaFavoritos({
                        termo: destino.termo,
                        aguardarConexao: true,
                        timeoutMs: 24000,
                        pollMs: 350
                    });
                    if (!window.FavoritosV2.searchRanking.publicApi.search.preparacaoAvantPrePesquisaConfirmada(preparacaoAvant)) {
                        throw new Error(`Avant Pro nao confirmou o login antes da pesquisa "${destino.termo}". Confirme o e-mail em Ferramentas e aguarde o aviso de obrigado antes de pesquisar.`);
                    }
                    avantLoginPrePesquisaConfirmado = true;
                    window.FavoritosV2.searchRanking.publicApi.control.verificarCancelamentoFavoritos();
                }
                window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`SKU ${destino.sku || ''}: pesquisando ${destino.termo} pelo navegador interno.`);
                const limiteAnunciosPrimeiraPesquisa = Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80;
                const anuncios = await window.FavoritosV2.searchRanking.publicApi.search.buscarAnunciosFavoritosPorTermo(destino.termo, {
                    sku: destino.sku,
                    loja: destino.loja,
                    titulo: 'Fazendo Favorito! Aguarde...',
                    subtitulo: `SKU ${destino.sku || ''} - Pesquisa ${destino.campo || ''}`,
                    exigirAvantPro: true,
                    loginAvantAntesDaColeta,
                    avantLoginPrePesquisaConfirmado,
                    maxAnuncios: limiteAnunciosPrimeiraPesquisa,
                    maxCliquesAvant: 12,
                    maxPosicoesRolagem: 12
                });
                loginAvantPrimeiraPesquisaPreparado = true;
                const status = await fetchFavoritosJob(`/api/favoritos/jobs/${encodeURIComponent(mlFavoritosJobIdAtual)}${FAVORITOS_JOB_COLETA_TERMO_PATH}`, {
                    method: 'POST',
                    body: JSON.stringify({
                        sku: destino.sku,
                        loja: destino.loja,
                        campo: destino.campo,
                        termo: destino.termo,
                        url_confirmada: destino.url || '',
                        card_count: Array.isArray(anuncios) ? anuncios.length : 0,
                        anuncios,
                        metricas: {
                            origem: 'avantpro_browser',
                            loginAvantAntesDaColeta
                        }
                    })
                });
                receberStatusFavoritosJob(status);
            }
        }

        function pararPollingFavoritosJob() {
            if (mlFavoritosJobPollTimer) {
                clearTimeout(mlFavoritosJobPollTimer);
                mlFavoritosJobPollTimer = null;
            }
            mlFavoritosJobPollAtivo = false;
        }

        function reagendarPollingFavoritosJob(delayMs = 850) {
            pararPollingFavoritosJob();
            if (!mlFavoritosJobIdAtual || !mlFavoritosEmExecucao) return;
            mlFavoritosJobPollTimer = setTimeout(pollFavoritosJobAtual, Math.max(250, Number(delayMs) || 850));
        }

        async function pollFavoritosJobAtual() {
            if (!mlFavoritosJobIdAtual || mlFavoritosJobPollAtivo) return;
            mlFavoritosJobPollAtivo = true;
            try {
                const status = await fetchFavoritosJob(`/api/favoritos/jobs/${encodeURIComponent(mlFavoritosJobIdAtual)}/status`, {
                    method: 'GET',
                    headers: obterAuthHeaders()
                });
                receberStatusFavoritosJob(status);
                const estado = String(status.status || '').toLowerCase();
                if (!['done', 'error', 'canceled'].includes(estado)) {
                    reagendarPollingFavoritosJob(estado === 'paused' ? 1200 : 850);
                }
            } catch (err) {
                if (!mlFavoritosCancelado) {
                    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Falha ao consultar progresso do job: ${err && err.message ? err.message : err}`, {
                        erro: true,
                        tempoMs: 5000
                    });
                    reagendarPollingFavoritosJob(1600);
                }
            } finally {
                mlFavoritosJobPollAtivo = false;
            }
        }

        function receberStatusFavoritosJob(status) {
            mlFavoritosJobUltimoStatus = status || null;
            const jobIdStatus = String(status && (status.job_id || status.jobId || '') || '').trim();
            if (jobIdStatus) mlFavoritosJobIdAtual = jobIdStatus;
            const estado = String(status && status.status || '').toLowerCase();
            if (mlFavoritosCancelado && estado !== 'canceled') {
                if (typeof limparStatusTerminalFavoritos === 'function') {
                    limparStatusTerminalFavoritos({ status: 'canceled' });
                }
                return;
            }
            const grupos = aplicarResultadosParciaisFavoritosJob(status);
            if (grupos.length !== mlFavoritosJobUltimaQtdRender || ['done', 'error', 'canceled'].includes(estado)) {
                agendarRenderFavoritosJob(grupos);
            }
            if (mlWorkModalSubtitleEl) {
                mlWorkModalSubtitleEl.textContent = favoritosJobStatusMensagem(status);
            }
            window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(favoritosJobStatusMensagem(status), {
                larga: true
            });
            atualizarFiltroAzulFavoritos();
            atualizarContadorSkuSidebarSelecionados();
            if (estado === 'done') {
                finalizarFavoritosJob(status, grupos);
            } else if (estado === 'error') {
                finalizarFavoritosJob(status, grupos, new Error(status.erro || 'Erro no job de favoritos.'));
            } else if (estado === 'canceled') {
                finalizarFavoritosJob(status, grupos, window.FavoritosV2.searchRanking.publicApi.control.criarErroFavoritosCancelado());
            }
        }

        function limparEstadoFavoritosJob() {
            mlFavoritosEmExecucao = false;
            mlFavoritosExecucaoEmSegundoPlano = false;
            mlFavoritosCancelado = false;
            mlFavoritosPausado = false;
            mlFavoritosAbortController = null;
            mlFavoritosJobIdAtual = '';
            mlFavoritosJobSelecionadosAtual = [];
            mlFavoritosJobUltimoStatus = null;
            atualizarFiltroAzulFavoritos();
            atualizarContadorSkuSidebarSelecionados();
        }

        function finalizarFavoritosJob(status, grupos, erro = null) {
            if (mlFavoritosJobFinalTratado) return;
            mlFavoritosJobFinalTratado = true;
            pararPollingFavoritosJob();
            const estado = String(status && status.status || '').toLowerCase();
            const cancelado = estado === 'canceled' || !!(erro && erro.canceladoFavoritos);
            const finalizouEmSegundoPlano = mlFavoritosExecucaoEmSegundoPlano;
            const totalAnuncios = grupos.reduce((acc, grupo) => acc + (Array.isArray(grupo.anuncios) ? grupo.anuncios.length : 0), 0);
            if (!erro && estado === 'done') {
                gruposComRankingFavoritos(grupos).forEach(grupo => {
                    registrarHistoricoRankingSkuFavoritosImediato(grupo);
                });
                const resumoHistorico = resumoHistoricosIndividuaisFavoritos(grupos);
                const entradaHistorico = resumoHistorico.entrada;
                if (!entradaHistorico) {
                    if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Favoritos sem ranking salvo';
                    if (mlWorkModalSubtitleEl) mlWorkModalSubtitleEl.textContent = `${grupos.length} SKU(s), nenhum anuncio rankeado.`;
                    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('Job concluido, mas nenhum anuncio entrou no ranking. Nada foi salvo no historico; confira os campos de pesquisa ou tente novamente.', {
                        erro: true,
                        tempoMs: 9000,
                        larga: true
                    });
                    pararNavegadorFavoritosBackground();
                    limparEstadoFavoritosJob();
                    return;
                }
                const grupoParaAbrir = grupos.find(grupo => grupo && grupo.sku && Array.isArray(grupo.anuncios) && grupo.anuncios.length)
                    || grupos.find(grupo => grupo && grupo.sku);
                if (grupoParaAbrir && grupoParaAbrir.sku) {
                    favMlSkuSelecionado = grupoParaAbrir.sku;
                    favMlLojaSelecionada = grupoParaAbrir.loja || favoritosLojaSelecionadaParaApi() || '';
                    favMlHistoricoExecucaoSelecionadaId = FAV_ML_RANKING_ATUAL_ID;
                }
                if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Favoritos concluidos';
                if (mlWorkModalSubtitleEl) mlWorkModalSubtitleEl.textContent = `${grupos.length} SKU(s), ${totalAnuncios} anuncio(s) rankeado(s).`;
                window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Favoritos concluido: ${grupos.length} SKU(s), ${totalAnuncios} anuncio(s) rankeado(s).`, {
                    tempoMs: 7000
                });
                if (grupoParaAbrir && grupoParaAbrir.sku) {
                    carregarFavoritosAnunciosSku(grupoParaAbrir.sku, grupoParaAbrir.loja || favMlLojaSelecionada, {
                        manterRankingSelecionado: true
                    });
                }
                fecharNavegadorFavoritosAposColeta('favoritos-renderer-finalizado');
                if (!finalizouEmSegundoPlano) mudarAba('favoritos');
            } else if (cancelado) {
                if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Favoritos cancelado';
                if (typeof limparStatusTerminalFavoritos === 'function') {
                    limparStatusTerminalFavoritos({ status: 'canceled' });
                }
            } else {
                if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Erro ao fazer favoritos';
                window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Erro ao fazer favoritos: ${erro && erro.message ? erro.message : (status && status.erro) || 'erro desconhecido'}`, {
                    erro: true
                });
            }
            pararNavegadorFavoritosBackground(cancelado ? {
                status: 'canceled',
                message: '',
                reason: 'favoritos-job-cancelado'
            } : {});
            limparEstadoFavoritosJob();
        }
