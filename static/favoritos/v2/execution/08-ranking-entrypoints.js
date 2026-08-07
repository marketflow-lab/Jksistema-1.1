async function fazerFavoritosSkusSelecionados(opcoes = {}) {
    if (opcoes && opcoes.type && opcoes.target) opcoes = {};
    if (opcoes.usarRendererAntigo === true || opcoes.usarBackendJob === false) {
        return fazerFavoritosSkusSelecionadosRendererAntigo({
            ...opcoes,
            background: false,
            mostrarNavegadorMl: true
        });
    }
    if (mlFavoritosEmExecucao) return;
    mlFavoritosCancelado = false;
    mlFavoritosPausado = false;
    const selecionadosPayload = normalizarSelecionadosFavoritosExecucao(opcoes.selecionadosPayload);
    const selecionados = selecionadosPayload.length ? selecionadosPayload : obterSkusSelecionadosSidebar();
    if (!selecionados.length) {
        window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('Selecione pelo menos um SKU no sidebar antes de fazer favoritos.', {
            erro: true,
            tempoMs: 3500
        });
        return;
    }
    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`${selecionados.length} SKU(s) selecionado(s). Escolha a quantidade de pesquisas.`, {
        manterAcoes: false
    });
    const quantidade = Number(opcoes.quantidade) || await window.FavoritosV2.searchRanking.publicApi.auth.perguntarQuantidadePesquisasFavoritos();
    if (!quantidade) return;
    const opcoesPromocao = opcoes.opcoesPromocao || await window.FavoritosV2.searchRanking.publicApi.promotions.perguntarOpcoesPromocaoFavoritos();
    if (!opcoesPromocao) return;
    mlFavoritosOpcoesPromocaoAtual = window.FavoritosV2.promotionEffectuation.publicApi.options.clonarOpcoesPromocaoFavoritos(opcoesPromocao);
    selecionados.forEach(item => window.FavoritosV2.promotionEffectuation.publicApi.options.salvarOpcoesPromocaoFavoritosSku(item && item.sku, mlFavoritosOpcoesPromocaoAtual));
    const usarIaRanking = favoritosUsarIaRankingAtivo();
    const avantLoginConfirmadoPeloUsuario = opcoes.avantLoginConfirmadoPeloUsuario === true
        || await window.FavoritosV2.searchRanking.publicApi.auth.perguntarLoginAvantProAntesFavoritos({ selecionados, quantidade, opcoesPromocao });
    if (!avantLoginConfirmadoPeloUsuario) return;
    await abrirPrimeiraPesquisaFavoritosAposConfirmacaoAvantPro(selecionados, quantidade, {
        opcoesPromocao,
        usarIaRanking
    });
}

async function executarBackendJobFavoritosIsolado(dados) {
    const {
        opcoes, selecionados, quantidade, opcoesPromocao,
        usarIaRanking, avantLoginConfirmadoPeloUsuario
    } = dados;
    const executarEmBackground = opcoes.background === true;
    prepararEstadoBackendJobFavoritos(selecionados, executarEmBackground);
    await prepararInterfaceBackendJobFavoritos({
        opcoes, selecionados, quantidade, opcoesPromocao, usarIaRanking, executarEmBackground
    });
    try {
        if (!Array.isArray(skuDados) || !skuDados.length) {
            window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('Carregando dados dos SKUs antes de iniciar o job...');
            await carregarSkuFavoritos();
            window.FavoritosV2.searchRanking.publicApi.control.verificarCancelamentoFavoritos();
        }
        const payload = montarPayloadFavoritosJob(selecionados, quantidade, opcoesPromocao, usarIaRanking);
        const data = await fetchFavoritosJob('/api/favoritos/jobs', {
            method: 'POST',
            body: JSON.stringify(payload)
        });
        mlFavoritosJobIdAtual = data.job_id || '';
        mlFavoritosJobUltimoStatus = data;
        if (!mlFavoritosJobIdAtual) throw new Error('Backend nao retornou job_id para favoritos.');
        receberStatusFavoritosJob(data);
        iniciarWorkerFavoritosAvantProJob({ avantLoginConfirmadoPeloUsuario })
            .then(() => {
                if (mlFavoritosEmExecucao && mlFavoritosJobIdAtual) reagendarPollingFavoritosJob(350);
            })
            .catch(tratarErroColetaVisualBackendJobFavoritos);
    } catch (err) {
        pararPollingFavoritosJob();
        pararNavegadorFavoritosBackground();
        limparEstadoFavoritosJob();
        if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Erro ao iniciar favoritos';
        window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Erro ao iniciar favoritos: ${err && err.message ? err.message : err}`, {
            erro: true
        });
    }
}

function prepararEstadoBackendJobFavoritos(selecionados, executarEmBackground) {
    mlFavoritosEmExecucao = true;
    mlFavoritosExecucaoEmSegundoPlano = executarEmBackground;
    mlFavoritosCancelado = false;
    mlFavoritosPausado = false;
    mlFavoritosAbortController = typeof AbortController === 'function' ? new AbortController() : null;
    mlFavoritosJobIdAtual = '';
    mlFavoritosJobUltimoStatus = null;
    mlFavoritosJobUltimaQtdRender = -1;
    mlFavoritosJobSelecionadosAtual = selecionados.slice();
    mlFavoritosJobFinalTratado = false;
    resetarHistoricosIndividuaisFavoritosExecucao();
    pararPollingFavoritosJob();
}

async function prepararInterfaceBackendJobFavoritos(dados) {
    const { opcoes, selecionados, quantidade, opcoesPromocao, usarIaRanking, executarEmBackground } = dados;
    const mostrarNavegadorMl = opcoes.mostrarNavegadorMl !== false;
    if (executarEmBackground) await prepararNavegadorFavoritosBackground();
    else if (mostrarNavegadorMl && typeof mudarAba === 'function') {
        try { mudarAba('navegador'); } catch (_err) {}
    } else if (!mostrarNavegadorMl) {
        ocultarNavegadorMlShellDefinitivo();
    }
    abrirBalaoResultadosMl({
        titulo: 'Fazendo Favorito! Aguarde...',
        subtitulo: `${selecionados.length} SKU(s), ${quantidade} pesquisa(s) por SKU${usarIaRanking ? ', IA ligada' : ''}`,
        mostrarFavoritos: true,
        browserCompleto: true,
        forcarExibicao: mostrarNavegadorMl && !executarEmBackground
    });
    atualizarFiltroAzulFavoritos();
    atualizarContadorSkuSidebarSelecionados();
    if (mlFavoritosPanelEl) mlFavoritosPanelEl.classList.remove('hidden');
    if (mlFavoritosListEl) mlFavoritosListEl.innerHTML = '';
    if (mlFavoritosEmptyEl) mlFavoritosEmptyEl.classList.add('hidden');
    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Iniciando job de favoritos: ${selecionados.length} SKU(s), ${quantidade} pesquisa(s) por SKU, ${window.FavoritosV2.searchRanking.publicApi.promotions.resumoOpcoesPromocaoFavoritos(opcoesPromocao)}${usarIaRanking ? ', IA verifica anuncios fora do produto' : ''}.`);
}

function tratarErroColetaVisualBackendJobFavoritos(err) {
    if (mlFavoritosCancelado || (err && (err.canceladoFavoritos || err.name === 'AbortError'))) return;
    const mensagemErro = `Erro na coleta visual do Favoritos: ${err && err.message ? err.message : err}`;
    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(mensagemErro, { erro: true, larga: true });
    const jobIdErro = mlFavoritosJobIdAtual;
    const erroColeta = err instanceof Error ? err : new Error(mensagemErro);
    const statusErro = {
        ...(mlFavoritosJobUltimoStatus || {}),
        job_id: jobIdErro,
        status: 'error',
        erro: mensagemErro,
        mensagem: 'A coleta visual do Favoritos falhou.'
    };
    if (jobIdErro) {
        fetchFavoritosJob(`/api/favoritos/jobs/${encodeURIComponent(jobIdErro)}/cancel`, {
            method: 'POST'
        }).catch(() => {});
    }
    finalizarFavoritosJob(statusErro, aplicarResultadosParciaisFavoritosJob(statusErro), erroColeta);
}

async function rankearAvulsoMercadoLivre(opcoes = {}) {
    if (mlFavoritosEmExecucao) return;
    const preparacao = prepararRankingAvulsoFavoritos(opcoes);
    if (preparacao.abortar) return;
    const contexto = preparacao.contexto;
    try {
        const coleta = await coletarRankingAvulsoFavoritos(contexto);
        const unicos = await enriquecerRankingAvulsoFavoritos(contexto, coleta.coletados);
        const grupoRanking = await calcularRankingAvulsoFavoritos(contexto, unicos, coleta.resumosColeta);
        const confirmacao = await salvarRankingAvulsoFavoritos(grupoRanking);
        concluirRankingAvulsoFavoritos(grupoRanking, confirmacao);
    } catch (err) {
        tratarErroRankingAvulsoFavoritos(err);
    } finally {
        limparEstadoRankingAvulsoFavoritos();
    }
}

function prepararRankingAvulsoFavoritos(opcoes) {
    const termos = normalizarTermosPesquisaFavoritos(opcoes.termos || obterTermosPesquisaAvulsaMl());
    if (!termos.length) {
        window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('Preencha pelo menos uma pesquisa para fazer o rankeamento avulso.', {
            erro: true,
            tempoMs: 3500
        });
        const primeiroCampo = obterCamposPesquisaAvulsaMl()[0];
        if (primeiroCampo && primeiroCampo.input) primeiroCampo.input.focus();
        return { abortar: true };
    }
    if (opcoes.termos) preencherCamposPesquisaAvulsaMl(termos);
    const skuRanking = String(opcoes.sku || 'AVULSO').trim() || 'AVULSO';
    const rankingAvulso = opcoes.avulso !== undefined ? !!opcoes.avulso : skuChaveSku(skuRanking) === 'avulso';
    const tituloProcesso = opcoes.tituloProcesso || (rankingAvulso ? 'Ranqueamento avulso' : `Refazendo ranking ${skuRanking}`);
    mlFavoritosExecucaoIniciadaEmMs = Date.now();
    mlFavoritosEmExecucao = true;
    mlFavoritosExecucaoEmSegundoPlano = false;
    mlFavoritosCancelado = false;
    mlFavoritosAbortController = typeof AbortController === 'function' ? new AbortController() : null;
    abrirBalaoResultadosMl({
        titulo: tituloProcesso,
        subtitulo: `${termos.length} pesquisa(s)`,
        mostrarFavoritos: true,
        browserCompleto: true
    });
    atualizarFiltroAzulFavoritos();
    if (mlFavoritosPanelEl) mlFavoritosPanelEl.classList.remove('hidden');
    if (mlFavoritosListEl) mlFavoritosListEl.innerHTML = '';
    if (mlFavoritosEmptyEl) {
        mlFavoritosEmptyEl.textContent = 'Ranqueando pesquisas avulsas...';
        mlFavoritosEmptyEl.classList.remove('hidden');
    }
    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Ranqueamento avulso iniciado com ${termos.length} pesquisa(s).`);
    const tituloAvulso = String(opcoes.titulo || '').trim()
        || termos.map(item => item.termo).filter(Boolean).join(' | ').slice(0, 180)
        || 'Pesquisas avulsas';
    return {
        contexto: {
            opcoes,
            termos,
            skuRanking,
            tituloProcesso,
            info: {
                sku: skuRanking,
                loja: opcoes.loja || favoritosLojaSelecionadaParaApi(),
                titulo: tituloAvulso,
                termos,
                avulso: rankingAvulso,
                pesquisa_avulsa: rankingAvulso
            },
            contextoEnriquecimento: typeof window.FavoritosV2.searchRanking.publicApi.enrichment.criarContextoEnriquecimentoFavoritosExecucao === 'function'
                ? window.FavoritosV2.searchRanking.publicApi.enrichment.criarContextoEnriquecimentoFavoritosExecucao()
                : null
        }
    };
}

async function coletarRankingAvulsoFavoritos(contexto) {
    const coletados = [];
    const resumosColeta = [];
    for (let pesquisaIndex = 0; pesquisaIndex < contexto.termos.length; pesquisaIndex += 1) {
        window.FavoritosV2.searchRanking.publicApi.control.verificarCancelamentoFavoritos();
        const pesquisa = contexto.termos[pesquisaIndex];
        window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Ranqueamento avulso: pesquisando Pesquisa ${pesquisa.campo} - ${pesquisa.termo}`);
        const anuncios = await coletarAnunciosPrimeiraPaginaFavoritosSemFluxoAntigo(contexto.info, pesquisa, {
            primeiraPesquisa: pesquisaIndex === 0,
            maxAnuncios: Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80,
            titulo: contexto.tituloProcesso,
            subtitulo: `Pesquisa ${pesquisa.campo}: ${pesquisa.termo}`,
            avulso: true
        });
        window.FavoritosV2.searchRanking.publicApi.control.verificarCancelamentoFavoritos();
        let normalizadosPesquisa = anuncios.map(anuncio => window.FavoritosV2.searchRanking.publicApi.listings.normalizarAnuncioFavoritosPesquisa(anuncio, contexto.skuRanking, pesquisa));
        await window.FavoritosV2.searchRanking.publicApi.enrichment.enriquecerAnunciosFavoritosRanking(normalizadosPesquisa, contexto.contextoEnriquecimento);
        normalizadosPesquisa = normalizadosPesquisa.map(anuncio => window.FavoritosV2.searchRanking.publicApi.listings.normalizarAnuncioFavoritosPesquisa(anuncio, contexto.skuRanking, pesquisa));
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

async function enriquecerRankingAvulsoFavoritos(contexto, coletados) {
    let unicos = window.FavoritosV2.searchRanking.publicApi.listings.deduplicarAnunciosFavoritos(coletados)
        .filter(window.FavoritosV2.searchRanking.publicApi.listings.anuncioFavoritosCandidatoRanking);
    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Ranqueamento avulso: enriquecendo ${unicos.length} anuncio(s)...`);
    window.FavoritosV2.searchRanking.publicApi.control.verificarCancelamentoFavoritos();
    await window.FavoritosV2.searchRanking.publicApi.enrichment.enriquecerAnunciosFavoritosRanking(unicos, contexto.contextoEnriquecimento, { fechamento: true });
    for (let i = 0; i < unicos.length; i += 1) {
        const pesquisasOrigem = Array.isArray(unicos[i].pesquisas_origem) ? [...unicos[i].pesquisas_origem] : [];
        const camposOrigem = Array.isArray(unicos[i].campos_origem) ? [...unicos[i].campos_origem] : [];
        const origem = Array.isArray(unicos[i].pesquisas_origem) && unicos[i].pesquisas_origem[0]
            ? { termo: unicos[i].pesquisas_origem[0], campo: '' }
            : { termo: '', campo: '' };
        unicos[i] = window.FavoritosV2.searchRanking.publicApi.listings.normalizarAnuncioFavoritosPesquisa(unicos[i], contexto.skuRanking, origem);
        if (pesquisasOrigem.length) unicos[i].pesquisas_origem = pesquisasOrigem;
        if (camposOrigem.length) unicos[i].campos_origem = camposOrigem;
    }
    window.FavoritosV2.searchRanking.publicApi.control.verificarCancelamentoFavoritos();
    return window.FavoritosV2.searchRanking.publicApi.listings.deduplicarAnunciosFavoritos(unicos).filter(window.FavoritosV2.searchRanking.publicApi.listings.anuncioFavoritosCandidatoRanking);
}

async function calcularRankingAvulsoFavoritos(contexto, unicos, resumosColeta) {
    const anunciosNovos = unicos.filter(anuncioFavoritosProdutoNovo);
    const anunciosElegiveis = anunciosNovos.length ? anunciosNovos : unicos;
    const preparoDadosAvant = prepararAnunciosFavoritosRankingComDadosAvant(anunciosElegiveis);
    const rankingBaseAvulso = montarRankingFavoritosComFallback(preparoDadosAvant.anuncios, contexto.info.sku);
    let filtroIa = {
        anuncios: window.FavoritosV2.searchRanking.publicApi.ranking.limitarAnunciosFavoritosRanking(rankingBaseAvulso),
        removidos: [],
        removidosTotal: 0,
        usouIa: false
    };
    if (contexto.opcoes.usarIaRanking === true) {
        try {
            filtroIa = await window.FavoritosV2.searchRanking.publicApi.ai.filtrarAnunciosFavoritosPorIa(contexto.info, preparoDadosAvant.anuncios);
        } catch (err) {
            if (mlFavoritosCancelado || (err && (err.canceladoFavoritos || err.name === 'AbortError'))) throw err;
            console.warn('IA de favoritos avulso falhou; mantendo ranking normal:', err);
        }
    }
    window.FavoritosV2.searchRanking.publicApi.control.verificarCancelamentoFavoritos();
    let anunciosAvulsosRanking = montarRankingFavoritosComFallback(filtroIa.anuncios, contexto.info.sku);
    if (!anunciosAvulsosRanking.length && rankingBaseAvulso.length) anunciosAvulsosRanking = rankingBaseAvulso;
    if (!anunciosAvulsosRanking.length && unicos.length) {
        anunciosAvulsosRanking = montarRankingFavoritosComFallback(unicos, contexto.info.sku);
    }
    return {
        ...contexto.info,
        sku: contexto.info.sku || contexto.skuRanking,
        loja: contexto.info.loja || favoritosLojaSelecionadaParaApi() || '',
        avulso: true,
        anuncios: window.FavoritosV2.searchRanking.publicApi.ranking.limitarAnunciosFavoritosRanking(anunciosAvulsosRanking),
        removidos_ia: filtroIa.removidos,
        removidos_ia_total: filtroIa.removidosTotal,
        usou_ia: !!filtroIa.usouIa,
        ia_confirmados: filtroIa.confirmados || 0,
        ia_max_confirmados: filtroIa.maxConfirmados || 0,
        fonte_coleta: 'avantpro_primeira_pagina_nova',
        duracao_execucao_ms: Math.max(0, Date.now() - mlFavoritosExecucaoIniciadaEmMs),
        resumo_coleta: resumosColeta,
        total_coletado: unicos.length,
        total_com_dados_avant: window.FavoritosV2.searchRanking.publicApi.search.filtrarAnunciosFavoritosComDadosAvant(unicos).length
    };
}

async function salvarRankingAvulsoFavoritos(grupoRanking) {
    guardarResultadoRankingFavorito(grupoRanking);
    const entradaHistoricoAvulso = registrarHistoricoFavoritos([grupoRanking]);
    if (!entradaHistoricoAvulso) {
        throw new Error('O ranking foi concluido, mas o historico nao foi gerado.');
    }
    const confirmacaoHistoricoAvulso = await finalizarDuracaoExecucaoHistoricosFavoritos(
        [entradaHistoricoAvulso.id],
        mlFavoritosExecucaoIniciadaEmMs
    );
    if (!confirmacaoHistoricoAvulso || confirmacaoHistoricoAvulso.success !== true) {
        throw new Error(confirmacaoHistoricoAvulso && confirmacaoHistoricoAvulso.erro || 'O servidor nao confirmou o historico com o tempo de execucao.');
    }
    return confirmacaoHistoricoAvulso;
}

function concluirRankingAvulsoFavoritos(grupoRanking, confirmacaoHistoricoAvulso) {
    favMlSkuSelecionado = grupoRanking.sku;
    favMlLojaSelecionada = grupoRanking.loja || favoritosLojaSelecionadaParaApi() || '';
    favMlHistoricoExecucaoSelecionadaId = '';
    favMlAnunciosSkuAtual = [];
    renderizarFavoritosPesquisaResultados([grupoRanking]);
    renderizarFavoritosOutrosAnuncios(grupoRanking.sku);
    if (grupoRankingFavoritosEhAvulso(grupoRanking)) renderizarFavoritosAnunciosMl([], grupoRanking.sku);
    else carregarFavoritosAnunciosSku(grupoRanking.sku, grupoRanking.loja || favMlLojaSelecionada);
    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Ranqueamento avulso concluido: ${grupoRanking.anuncios.length} anuncio(s) rankeado(s).`, {
        tempoMs: 7000
    });
    if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Ranqueamento avulso concluido';
    if (mlWorkModalSubtitleEl) {
        mlWorkModalSubtitleEl.textContent = `${grupoRanking.anuncios.length} anuncio(s) rankeado(s).`;
    }
    const finalizouEmSegundoPlano = mlFavoritosExecucaoEmSegundoPlano;
    mlFavoritosEmExecucao = false;
    mlFavoritosExecucaoEmSegundoPlano = false;
    atualizarFiltroAzulFavoritos();
    window.FavoritosV2.searchRanking.publicApi.status.esconderBalaoFavoritosStatus();
    fecharNavegadorFavoritosAposColeta('favoritos-avulso-concluido', {
        finishedAt: mlFavoritosExecucaoIniciadaEmMs + Number(confirmacaoHistoricoAvulso.duracao_execucao_ms || 0)
    });
    if (!finalizouEmSegundoPlano) mudarAba('favoritos');
}

function tratarErroRankingAvulsoFavoritos(err) {
    if (mlFavoritosCancelado || (err && (err.canceladoFavoritos || err.name === 'AbortError'))) {
        if (typeof limparStatusTerminalFavoritos === 'function') {
            limparStatusTerminalFavoritos({ status: 'canceled' });
        }
        pararNavegadorFavoritosBackground({
            status: 'canceled',
            message: '',
            reason: 'favoritos-avulso-cancelado'
        });
        return;
    }
    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Erro no rankeamento avulso: ${err && err.message ? err.message : err}`, {
        erro: true
    });
    fecharNavegadorFavoritosAposColeta('favoritos-avulso-erro', {
        status: 'error',
        message: 'Ranqueamento avulso encerrado com erro.'
    });
}

function limparEstadoRankingAvulsoFavoritos() {
    const canceladoAoFinal = !!mlFavoritosCancelado;
    if (canceladoAoFinal && typeof limparStatusTerminalFavoritos === 'function') {
        limparStatusTerminalFavoritos({ status: 'canceled' });
    }
    mlFavoritosEmExecucao = false;
    mlFavoritosExecucaoEmSegundoPlano = false;
    mlFavoritosExecucaoIniciadaEmMs = 0;
    mlFavoritosCancelado = false;
    mlFavoritosAbortController = null;
    atualizarFiltroAzulFavoritos();
}
