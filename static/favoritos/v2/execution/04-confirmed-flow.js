async function abrirPrimeiraPesquisaFavoritosAposConfirmacaoAvantPro(selecionados = [], quantidade = 1, opcoes = {}) {
    const preparacao = prepararFluxoConfirmadoFavoritos(selecionados, quantidade, opcoes);
    if (preparacao.resultado) return preparacao.resultado;
    const { contexto } = preparacao;
    const grupos = [];
    let duracaoExecucaoPersistidaMs = null;
    try {
        await garantirDadosSkuFluxoConfirmadoFavoritos();
        await executarColetaFluxoConfirmadoFavoritos(contexto, grupos);
        const historico = validarHistoricosFluxoConfirmadoFavoritos(grupos);
        if (historico.resultado) return historico.resultado;
        const confirmacao = await confirmarDuracaoHistoricoFluxoFavoritos(
            historico.entradaHistorico,
            grupos
        );
        if (confirmacao.resultado) return confirmacao.resultado;
        duracaoExecucaoPersistidaMs = confirmacao.duracaoExecucaoPersistidaMs;
        return concluirFluxoConfirmadoFavoritos(contexto, grupos, historico, duracaoExecucaoPersistidaMs);
    } catch (err) {
        return tratarErroFluxoConfirmadoFavoritos(err, grupos);
    } finally {
        limparEstadoFluxoConfirmadoFavoritos();
    }
}

function prepararFluxoConfirmadoFavoritos(selecionados, quantidade, opcoes) {
    if (typeof window.FavoritosV2.searchRanking.publicApi.auth.limparBotaoContinuarLoginAvantProFavoritos === 'function') {
        window.FavoritosV2.searchRanking.publicApi.auth.limparBotaoContinuarLoginAvantProFavoritos();
    }
    if (mlFavoritosEmExecucao) return { resultado: { success: false, reason: 'execucao_em_andamento' } };
    const selecionadosLista = Array.isArray(selecionados) ? selecionados.filter(Boolean) : [];
    const quantidadePesquisas = normalizarQuantidadePesquisasNovaColetaFavoritos(quantidade);
    const opcoesPromocao = opcoes.opcoesPromocao || mlFavoritosOpcoesPromocaoAtual || null;
    const usarIaRanking = opcoes.usarIaRanking !== undefined
        ? !!opcoes.usarIaRanking
        : (typeof favoritosUsarIaRankingAtivo === 'function' && favoritosUsarIaRankingAtivo());
    const limiteAnunciosPrimeiraPesquisa = Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80;
    const limiteRankingFinal = Number(ML_FAVORITOS_RANKING_ANUNCIOS_MAX) || 80;
    if (!selecionadosLista.length) {
        window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('Avant Pro confirmado, mas nenhum SKU foi selecionado para pesquisar.', {
            erro: true,
            tempoMs: 4500,
            titulo: 'Favoritos'
        });
        return { resultado: { success: false, reason: 'sem_skus' } };
    }
    iniciarEstadoFluxoConfirmadoFavoritos();
    const termo = String(
        opcoes.termo
        || (typeof window.FavoritosV2.searchRanking.publicApi.auth.obterTermoInicialLoginAvantProFavoritos === 'function'
            ? window.FavoritosV2.searchRanking.publicApi.auth.obterTermoInicialLoginAvantProFavoritos(selecionadosLista, quantidadePesquisas)
            : '')
    ).trim();
    const sku = selecionadosLista[0] && selecionadosLista[0].sku
        ? String(selecionadosLista[0].sku).trim()
        : '';
    if (!termo) return prepararFalhaTermoFluxoConfirmadoFavoritos();
    const urlPrimeiraPesquisaMl = typeof construirUrlPesquisaMercadoLivre === 'function'
        ? construirUrlPesquisaMercadoLivre(termo)
        : `https://lista.mercadolivre.com.br/${encodeURIComponent(termo)}`;
    exibirInicioFluxoConfirmadoFavoritos(termo, selecionadosLista.length, quantidadePesquisas);
    return {
        contexto: {
            selecionadosLista,
            quantidadePesquisas,
            opcoesPromocao,
            usarIaRanking,
            limiteAnunciosPrimeiraPesquisa,
            limiteRankingFinal,
            termo,
            sku,
            urlPrimeiraPesquisaMl,
            rollbackR6: opcoes.rollbackR6 === true
        }
    };
}

function iniciarEstadoFluxoConfirmadoFavoritos() {
    mlFavoritosExecucaoIniciadaEmMs = Date.now();
    mlFavoritosEmExecucao = true;
    mlFavoritosExecucaoEmSegundoPlano = true;
    mlFavoritosCancelado = false;
    mlFavoritosPausado = false;
    mlFavoritosAbortController = typeof AbortController === 'function' ? new AbortController() : null;
    resetarHistoricosIndividuaisFavoritosExecucao();
}

function prepararFalhaTermoFluxoConfirmadoFavoritos() {
    mlFavoritosEmExecucao = false;
    mlFavoritosExecucaoEmSegundoPlano = false;
    mlFavoritosExecucaoIniciadaEmMs = 0;
    mlFavoritosAbortController = null;
    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('Avant Pro confirmado, mas nao encontrei termo para abrir a primeira pesquisa.', {
        erro: true,
        manterNavegadorVisivel: true,
        larga: true,
        titulo: 'Primeira pesquisa'
    });
    return { resultado: { success: false, reason: 'termo_indisponivel' } };
}

function exibirInicioFluxoConfirmadoFavoritos(termo, totalSkus, quantidadePesquisas) {
    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Avant Pro confirmado. Abrindo a primeira pesquisa de "${termo}"...`, {
        erro: false,
        manterNavegadorVisivel: false,
        larga: true,
        titulo: 'Primeira pesquisa'
    });
    abrirBalaoResultadosMl({
        titulo: 'Fazendo Favorito! Aguarde...',
        subtitulo: `${totalSkus} SKU(s), ${quantidadePesquisas} pesquisa(s) por SKU`,
        mostrarFavoritos: true,
        browserCompleto: true,
        forcarExibicao: false
    });
}

async function garantirDadosSkuFluxoConfirmadoFavoritos() {
    if (Array.isArray(skuDados) && skuDados.length) return;
    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('Carregando dados dos SKUs antes da primeira pesquisa...');
    await carregarSkuFavoritos();
    await window.FavoritosV2.searchRanking.publicApi.control.aguardarControleFavoritos();
}

async function executarColetaFluxoConfirmadoFavoritos(contexto, grupos) {
    if (contexto.rollbackR6) {
        await executarColetaSequencialFluxoConfirmadoFavoritos(contexto, grupos);
        return;
    }
    await executarFilaSkusFavoritosPool(contexto.selecionadosLista, grupos, {
        initialUrl: contexto.urlPrimeiraPesquisaMl,
        quantidadePesquisas: contexto.quantidadePesquisas,
        opcoesPromocao: contexto.opcoesPromocao,
        usarIaRanking: contexto.usarIaRanking,
        limiteAnunciosPrimeiraPesquisa: contexto.limiteAnunciosPrimeiraPesquisa,
        limiteRankingFinal: contexto.limiteRankingFinal,
        skuFallback: contexto.sku
    });
}

async function executarColetaSequencialFluxoConfirmadoFavoritos(contexto, grupos) {
    await prepararNavegadorFavoritosBackground(contexto.urlPrimeiraPesquisaMl);
    const contextoEnriquecimentoFavoritos = typeof window.FavoritosV2.searchRanking.publicApi.enrichment.criarContextoEnriquecimentoFavoritosExecucao === 'function'
        ? window.FavoritosV2.searchRanking.publicApi.enrichment.criarContextoEnriquecimentoFavoritosExecucao()
        : null;
    for (let idx = 0; idx < contexto.selecionadosLista.length; idx += 1) {
        window.FavoritosV2.searchRanking.publicApi.control.verificarCancelamentoFavoritos();
        await window.FavoritosV2.searchRanking.publicApi.control.aguardarControleFavoritos();
        const item = contexto.selecionadosLista[idx];
        const grupo = await processarSkuSequencialFluxoConfirmadoFavoritos(
            item,
            idx,
            contexto,
            contextoEnriquecimentoFavoritos
        );
        grupos.push(grupo);
        guardarResultadoRankingFavorito(grupo);
        if (Array.isArray(grupo.anuncios) && grupo.anuncios.length) {
            const entradaSku = registrarHistoricoRankingSkuFavoritosImediato(grupo);
            if (!entradaSku) {
                console.warn('Ranking do SKU concluido, mas o historico individual nao foi salvo.', {
                    sku: grupo.sku,
                    loja: grupo.loja,
                    anuncios: grupo.anuncios.length
                });
            }
        }
        renderizarFavoritosPesquisaResultados(grupos);
        desmarcarSkuFavoritosProcessado(item);
    }
}

async function processarSkuSequencialFluxoConfirmadoFavoritos(item, idx, contexto, contextoEnriquecimento) {
    const infoBase = window.FavoritosV2.searchRanking.publicApi.search.montarPesquisasFavoritosSku(item, contexto.quantidadePesquisas);
    const info = { ...infoBase, quantidade_pesquisas: contexto.quantidadePesquisas };
    if (!info.termos.length) {
        return montarGrupoSemPesquisaNovaColetaFavoritos(info, contexto.opcoesPromocao);
    }
    const coleta = await coletarPesquisasSequenciaisFluxoConfirmadoFavoritos(
        info,
        idx,
        contexto,
        contextoEnriquecimento
    );
    let unicos = window.FavoritosV2.searchRanking.publicApi.listings.deduplicarAnunciosFavoritos(coleta.coletados)
        .filter(window.FavoritosV2.searchRanking.publicApi.listings.anuncioFavoritosCandidatoRanking);
    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`SKU ${info.sku}: calculando ranking dos anuncios coletados...`, {
        manterNavegadorVisivel: true,
        larga: true,
        titulo: 'Ranking'
    });
    await window.FavoritosV2.searchRanking.publicApi.enrichment.enriquecerAnunciosFavoritosRanking(unicos, contextoEnriquecimento, { fechamento: true });
    unicos = window.FavoritosV2.searchRanking.publicApi.listings.deduplicarAnunciosFavoritos(unicos)
        .filter(window.FavoritosV2.searchRanking.publicApi.listings.anuncioFavoritosCandidatoRanking);
    await window.FavoritosV2.searchRanking.publicApi.control.aguardarControleFavoritos();
    return calcularGrupoSequencialFluxoConfirmadoFavoritos(item, info, unicos, coleta.resumosColeta, contexto);
}

async function coletarPesquisasSequenciaisFluxoConfirmadoFavoritos(info, idx, contexto, contextoEnriquecimento) {
    const coletados = [];
    const resumosColeta = [];
    for (let pesquisaIndex = 0; pesquisaIndex < info.termos.length; pesquisaIndex += 1) {
        window.FavoritosV2.searchRanking.publicApi.control.verificarCancelamentoFavoritos();
        await window.FavoritosV2.searchRanking.publicApi.control.aguardarControleFavoritos();
        const pesquisa = info.termos[pesquisaIndex];
        const ordemPesquisa = `${pesquisaIndex + 1}/${info.termos.length}`;
        window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`SKU ${info.sku}: pesquisa ${ordemPesquisa} - ${pesquisa.termo}. Aguardando dados do Avant Pro...`, {
            manterNavegadorVisivel: true,
            larga: true,
            titulo: pesquisaIndex === 0 ? 'Primeira pesquisa' : 'Proxima pesquisa'
        });
        const anuncios = await coletarAnunciosPrimeiraPaginaFavoritosSemFluxoAntigo(info, pesquisa, {
            primeiraPesquisa: idx === 0 && pesquisaIndex === 0,
            maxAnuncios: contexto.limiteAnunciosPrimeiraPesquisa
        });
        let normalizadosPesquisa = anuncios.map(anuncio => window.FavoritosV2.searchRanking.publicApi.listings.normalizarAnuncioFavoritosPesquisa(anuncio, info.sku, pesquisa));
        await window.FavoritosV2.searchRanking.publicApi.enrichment.enriquecerAnunciosFavoritosRanking(normalizadosPesquisa, contextoEnriquecimento);
        normalizadosPesquisa = normalizadosPesquisa.map(anuncio => window.FavoritosV2.searchRanking.publicApi.listings.normalizarAnuncioFavoritosPesquisa(anuncio, info.sku, pesquisa));
        normalizadosPesquisa.forEach(anuncio => coletados.push(anuncio));
        const resumoPesquisa = montarResumoPesquisaFavoritos(
            pesquisaIndex + 1,
            Number(anuncios.__favoritosTotalVisiveis) || anuncios.length,
            normalizadosPesquisa,
            anuncios.__favoritosResumo
        );
        resumoPesquisa.tempo_esgotado = !!anuncios.__favoritosTempoEsgotado;
        resumosColeta.push({ ...resumoPesquisa, termo: pesquisa.termo, campo: pesquisa.campo });
        window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(formatarResumoPesquisaFavoritos(resumoPesquisa), {
            manterNavegadorVisivel: true,
            larga: true,
            titulo: 'Resumo da coleta'
        });
    }
    return { coletados, resumosColeta };
}

async function calcularGrupoSequencialFluxoConfirmadoFavoritos(item, info, unicos, resumosColeta, contexto) {
    const anunciosNovos = unicos.filter(anuncioFavoritosProdutoNovo);
    const anunciosElegiveis = anunciosNovos.length ? anunciosNovos : unicos;
    const preparoDadosAvant = prepararAnunciosFavoritosRankingComDadosAvant(anunciosElegiveis);
    const rankingBase = montarRankingFavoritosComFallback(preparoDadosAvant.anuncios, info.sku);
    let filtroIa = {
        anuncios: window.FavoritosV2.searchRanking.publicApi.ranking.limitarAnunciosFavoritosRanking(rankingBase),
        removidos: [],
        removidosTotal: 0,
        usouIa: false
    };
    if (contexto.usarIaRanking) {
        try {
            filtroIa = await window.FavoritosV2.searchRanking.publicApi.ai.filtrarAnunciosFavoritosPorIa(info, rankingBase, {
                usarIa: true,
                itemSidebar: item,
                maxConfirmados: 8
            });
        } catch (err) {
            if (mlFavoritosCancelado || (err && (err.canceladoFavoritos || err.name === 'AbortError'))) throw err;
            console.warn('IA de favoritos falhou; mantendo ranking normal:', err);
        }
    }
    let anunciosRanking = contexto.usarIaRanking && filtroIa.usouIa
        ? filtroIa.anuncios
        : window.FavoritosV2.searchRanking.publicApi.ranking.limitarAnunciosFavoritosRanking(rankingBase);
    if (!anunciosRanking.length && rankingBase.length) anunciosRanking = window.FavoritosV2.searchRanking.publicApi.ranking.limitarAnunciosFavoritosRanking(rankingBase);
    if (!anunciosRanking.length && unicos.length) anunciosRanking = montarRankingFavoritosComFallback(unicos, info.sku);
    return {
        ...info,
        sku: info.sku || item.sku || contexto.sku || '',
        loja: info.loja || item.loja || favoritosLojaSelecionadaParaApi() || '',
        opcoes_promocao: window.FavoritosV2.promotionEffectuation.publicApi.options.clonarOpcoesPromocaoFavoritos(contexto.opcoesPromocao),
        anuncios: anunciosRanking.slice(0, contexto.limiteRankingFinal),
        removidos_ia: filtroIa.removidos,
        removidos_ia_total: filtroIa.removidosTotal,
        usou_ia: !!filtroIa.usouIa,
        ia_confirmados: filtroIa.confirmados || 0,
        ia_max_confirmados: filtroIa.maxConfirmados || (contexto.usarIaRanking ? 8 : 0),
        fonte_coleta: 'avantpro_primeira_pagina_nova',
        duracao_execucao_ms: Math.max(0, Date.now() - mlFavoritosExecucaoIniciadaEmMs),
        resumo_coleta: resumosColeta,
        total_coletado: unicos.length,
        total_com_dados_avant: window.FavoritosV2.searchRanking.publicApi.search.filtrarAnunciosFavoritosComDadosAvant(unicos).length
    };
}

function validarHistoricosFluxoConfirmadoFavoritos(grupos) {
    const gruposComRanking = gruposComRankingFavoritos(grupos);
    const totalAnuncios = gruposComRanking.reduce((acc, grupo) => acc + grupo.anuncios.length, 0);
    const resumoHistorico = resumoHistoricosIndividuaisFavoritos(grupos);
    const entradaHistorico = resumoHistorico.entrada;
    if (resumoHistorico.total && resumoHistorico.salvos.length !== resumoHistorico.total) {
        console.warn('Nem todos os SKUs com ranking tiveram historico individual confirmado.', {
            total: resumoHistorico.total,
            salvos: resumoHistorico.salvos.length
        });
        window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`O ranking foi concluido, mas apenas ${resumoHistorico.salvos.length} de ${resumoHistorico.total} SKU(s) geraram historico.`, {
            erro: true, tempoMs: 12000, larga: true, titulo: 'Historico incompleto'
        });
        fecharNavegadorFavoritosAposColeta('favoritos-historico-parcial', {
            status: 'error', message: 'Favoritos encerrado: historico incompleto.'
        });
        return {
            resultado: { success: false, reason: 'historico_parcial', grupos, resumo_historico: resumoHistorico }
        };
    }
    if (!entradaHistorico && gruposComRanking.length) {
        console.warn('Ranking de favoritos foi montado, mas o historico nao retornou entrada salva.', {
            grupos: gruposComRanking.length,
            anuncios: totalAnuncios
        });
        window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Ranking montado com ${totalAnuncios} anuncio(s), mas o historico nao confirmou o salvamento.`, {
            erro: true, tempoMs: 7000, larga: true, titulo: 'Ranking montado'
        });
        fecharNavegadorFavoritosAposColeta('favoritos-historico-nao-confirmado', {
            status: 'error', message: 'Favoritos encerrado: o historico nao foi gerado.'
        });
        return { resultado: { success: false, reason: 'historico_nao_confirmado', grupos } };
    }
    if (!entradaHistorico && !gruposComRanking.length) {
        if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Favoritos sem ranking salvo';
        if (mlWorkModalSubtitleEl) mlWorkModalSubtitleEl.textContent = `${grupos.length} SKU(s), nenhum anuncio rankeado.`;
        const detalheFalha = formatarResumoFalhaRankingFavoritos(grupos);
        window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`A coleta terminou, mas nenhum anuncio entrou no ranking.${detalheFalha || ' Confira os campos de pesquisa e tente novamente.'}`, {
            erro: true, tempoMs: 9000, larga: true
        });
        fecharNavegadorFavoritosAposColeta('favoritos-coleta-sem-ranking', {
            status: 'error', message: 'Favoritos encerrado sem ranking para salvar.'
        });
        return { resultado: { success: false, reason: 'sem_ranking', grupos } };
    }
    if (entradaHistorico && (
        typeof confirmarSalvamentoHistoricoFavoritosServidor !== 'function'
        || typeof finalizarDuracaoExecucaoHistoricosFavoritos !== 'function'
    )) {
        window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('O historico foi mantido localmente, mas o modulo de confirmacao do servidor nao esta disponivel.', {
            erro: true, tempoMs: 12000, larga: true, titulo: 'Falha ao confirmar historico'
        });
        fecharNavegadorFavoritosAposColeta('favoritos-confirmacao-historico-indisponivel', {
            status: 'error', message: 'Favoritos encerrado: confirmacao do historico indisponivel.'
        });
        return { resultado: { success: false, reason: 'confirmacao_historico_indisponivel', grupos } };
    }
    return { gruposComRanking, totalAnuncios, resumoHistorico, entradaHistorico };
}

async function confirmarDuracaoHistoricoFluxoFavoritos(entradaHistorico, grupos) {
    if (!entradaHistorico) return { duracaoExecucaoPersistidaMs: null };
    const duracaoExecucaoProvisoriaMs = Math.max(0, Date.now() - mlFavoritosExecucaoIniciadaEmMs);
    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Ranking concluido. Salvando o tempo total de ${formatarDuracaoExecucaoFavoritos(duracaoExecucaoProvisoriaMs)} no historico...`, {
        manterNavegadorVisivel: false,
        larga: true,
        titulo: 'Salvando historico'
    });
    const confirmacaoHistorico = await finalizarDuracaoExecucaoHistoricosFavoritos(
        entradaHistorico.ids || [],
        mlFavoritosExecucaoIniciadaEmMs
    );
    window.FavoritosV2.searchRanking.publicApi.control.verificarCancelamentoFavoritos();
    if (confirmacaoHistorico && confirmacaoHistorico.success === true) {
        return { duracaoExecucaoPersistidaMs: Number(confirmacaoHistorico.duracao_execucao_ms) };
    }
    const detalhe = confirmacaoHistorico && confirmacaoHistorico.erro ? ` ${confirmacaoHistorico.erro}` : '';
    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`O ranking foi mantido localmente, mas o servidor nao confirmou o historico.${detalhe}`, {
        erro: true, tempoMs: 12000, larga: true, titulo: 'Falha ao salvar historico'
    });
    fecharNavegadorFavoritosAposColeta('favoritos-historico-servidor-falhou', {
        status: 'error', message: 'Favoritos encerrado: falha ao confirmar o historico.'
    });
    return {
        resultado: {
            success: false,
            reason: 'historico_servidor_nao_confirmado',
            grupos,
            confirmacao_historico: confirmacaoHistorico || null
        }
    };
}

function concluirFluxoConfirmadoFavoritos(contexto, grupos, historico, duracaoExecucaoPersistidaMs) {
    const grupoParaAbrir = historico.gruposComRanking[0] || grupos.find(grupo => grupo && grupo.sku);
    if (grupoParaAbrir && grupoParaAbrir.sku) {
        favMlSkuSelecionado = grupoParaAbrir.sku;
        favMlLojaSelecionada = grupoParaAbrir.loja || favoritosLojaSelecionadaParaApi() || '';
        favMlHistoricoExecucaoSelecionadaId = FAV_ML_RANKING_ATUAL_ID;
    }
    renderizarFavoritosPesquisaResultados(grupos);
    if (grupoParaAbrir && grupoParaAbrir.sku) {
        carregarFavoritosAnunciosSku(grupoParaAbrir.sku, grupoParaAbrir.loja || favMlLojaSelecionada, {
            manterRankingSelecionado: true
        });
    }
    if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Favoritos concluidos';
    if (mlWorkModalSubtitleEl) {
        mlWorkModalSubtitleEl.textContent = `${historico.gruposComRanking.length} SKU(s), ${historico.totalAnuncios} anuncio(s) no ranking.`;
    }
    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Favoritos concluido. Ranking salvo e historico confirmado com ate ${contexto.limiteRankingFinal} anuncio(s) por SKU, em ordem de media de venda. A rotina antiga de coleta continua desligada.`, {
        tempoMs: 8000, larga: true, titulo: 'Ranking salvo'
    });
    if (typeof mudarAba === 'function') {
        try { mudarAba('favoritos'); } catch (_err) {}
    }
    fecharNavegadorFavoritosAposColeta('favoritos-coleta-concluida', {
        finishedAt: Number.isFinite(duracaoExecucaoPersistidaMs)
            ? mlFavoritosExecucaoIniciadaEmMs + duracaoExecucaoPersistidaMs
            : Date.now()
    });
    return {
        success: true,
        termo: contexto.termo,
        sku: contexto.sku,
        grupos,
        entradaHistorico: historico.entradaHistorico
    };
}

function tratarErroFluxoConfirmadoFavoritos(err, grupos) {
    if (mlFavoritosCancelado || (err && (err.canceladoFavoritos || err.name === 'AbortError'))) {
        if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Favoritos cancelado';
        if (typeof limparStatusTerminalFavoritos === 'function') {
            limparStatusTerminalFavoritos({ status: 'canceled' });
        }
        pararNavegadorFavoritosBackground({
            status: 'canceled', message: '', reason: 'favoritos-coleta-cancelada'
        });
        return { success: false, reason: 'cancelado', grupos };
    }
    if ((err && err.favoritosPoolFatal) || erroFatalFilaFavoritos(err)) {
        window.__JK_FAVORITOS_WORKERS_POOL_ACTIVE = false;
        const mensagemLogin = err && err.message
            ? err.message
            : 'Corrija o login ou a verificacao na janela trabalhadora exibida e execute novamente.';
        window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(mensagemLogin, {
            erro: true,
            manterNavegadorVisivel: true,
            larga: true,
            tempoMs: 0,
            titulo: 'Acesso precisa de confirmacao'
        });
        emitirEstadoWorkerFavoritosExecucao('jk-favoritos-worker-done', {
            active: false,
            status: 'error',
            message: mensagemLogin,
            keepProblemWorkerVisible: true
        });
        return { success: false, reason: 'acesso_global', error: mensagemLogin, grupos };
    }
    console.error('Falha na nova coleta de favoritos:', err);
    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Erro ao fazer favoritos: ${err && err.message ? err.message : err}`, {
        erro: true,
        manterNavegadorVisivel: true,
        larga: true,
        tempoMs: 12000,
        titulo: 'Erro ao fazer favoritos'
    });
    fecharNavegadorFavoritosAposColeta('favoritos-coleta-erro', {
        status: 'error',
        message: 'Favoritos encerrado com erro antes de salvar o historico.'
    });
    return { success: false, reason: 'erro', error: err && err.message ? err.message : String(err), grupos };
}

function limparEstadoFluxoConfirmadoFavoritos() {
    const canceladoAoFinal = !!mlFavoritosCancelado;
    if (canceladoAoFinal && typeof limparStatusTerminalFavoritos === 'function') {
        limparStatusTerminalFavoritos({ status: 'canceled' });
    }
    mlFavoritosEmExecucao = false;
    mlFavoritosExecucaoEmSegundoPlano = false;
    mlFavoritosExecucaoIniciadaEmMs = 0;
    mlFavoritosCancelado = false;
    mlFavoritosPausado = false;
    mlFavoritosAbortController = null;
    mlFavoritosJobUltimoStatus = null;
    atualizarFiltroAzulFavoritos();
    atualizarContadorSkuSidebarSelecionados();
}
