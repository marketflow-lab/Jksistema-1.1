async function fazerFavoritosSkusSelecionadosRendererAntigo(opcoes = {}) {
    if (opcoes && opcoes.type && opcoes.target) opcoes = {};
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

async function executarRendererAntigoFavoritosIsolado(dados) {
    const contexto = await prepararRendererAntigoFavoritos(dados);
    try {
        const grupos = await processarSkusRendererAntigoFavoritos(contexto);
        concluirRendererAntigoFavoritos(contexto, grupos);
    } catch (err) {
        contexto.manterNavegadorVisivelAposErro = tratarErroRendererAntigoFavoritos(err);
    } finally {
        limparRendererAntigoFavoritos(contexto);
    }
}

async function prepararRendererAntigoFavoritos(dados) {
    const { opcoes, selecionados, quantidade, opcoesPromocao, usarIaRanking, avantLoginConfirmadoPeloUsuario } = dados;
    mlFavoritosEmExecucao = true;
    const executarEmBackground = opcoes.background === true;
    mlFavoritosExecucaoEmSegundoPlano = executarEmBackground;
    mlFavoritosCancelado = false;
    mlFavoritosPausado = false;
    mlFavoritosJobIdAtual = '';
    mlFavoritosJobUltimoStatus = null;
    mlFavoritosAbortController = typeof AbortController === 'function' ? new AbortController() : null;
    resetarHistoricosIndividuaisFavoritosExecucao();
    const mostrarNavegadorMl = opcoes.mostrarNavegadorMl !== false && !executarEmBackground;
    if (executarEmBackground) await prepararNavegadorFavoritosBackground();
    else if (mostrarNavegadorMl) mudarAba('navegador');
    else ocultarNavegadorMlShellDefinitivo();
    abrirBalaoResultadosMl({
        titulo: 'Fazendo Favorito! Aguarde...',
        subtitulo: `${selecionados.length} SKU(s), ${quantidade} pesquisa(s) por SKU${usarIaRanking ? ', IA ligada' : ''}`,
        mostrarFavoritos: true,
        browserCompleto: true,
        forcarExibicao: mostrarNavegadorMl
    });
    atualizarFiltroAzulFavoritos();
    atualizarContadorSkuSidebarSelecionados();
    if (mlFavoritosPanelEl) mlFavoritosPanelEl.classList.remove('hidden');
    if (mlFavoritosListEl) mlFavoritosListEl.innerHTML = '';
    if (mlFavoritosEmptyEl) mlFavoritosEmptyEl.classList.add('hidden');
    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Iniciando favoritos: ${selecionados.length} SKU(s), ${quantidade} pesquisa(s) por SKU, ${window.FavoritosV2.searchRanking.publicApi.promotions.resumoOpcoesPromocaoFavoritos(opcoesPromocao)}${usarIaRanking ? ', IA verifica anuncios fora do produto' : ''}.`);
    return {
        selecionados,
        quantidade,
        opcoesPromocao,
        usarIaRanking,
        avantLoginConfirmadoPeloUsuario,
        executarEmBackground,
        manterNavegadorVisivelAposErro: false
    };
}

async function processarSkusRendererAntigoFavoritos(contexto) {
    let loginAvantPrimeiraPesquisaPreparado = !!contexto.avantLoginConfirmadoPeloUsuario;
    if (!Array.isArray(skuDados) || !skuDados.length) {
        window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('Carregando dados dos SKUs...');
        await carregarSkuFavoritos();
        await window.FavoritosV2.searchRanking.publicApi.control.aguardarControleFavoritos();
    }
    const grupos = [];
    for (let idx = 0; idx < contexto.selecionados.length; idx += 1) {
        await window.FavoritosV2.searchRanking.publicApi.control.aguardarControleFavoritos();
        const item = contexto.selecionados[idx];
        const resultado = await processarSkuRendererAntigoFavoritos(
            item,
            idx,
            contexto,
            loginAvantPrimeiraPesquisaPreparado
        );
        loginAvantPrimeiraPesquisaPreparado = resultado.loginAvantPrimeiraPesquisaPreparado;
        grupos.push(resultado.grupo);
        guardarResultadoRankingFavorito(resultado.grupo);
        if (Array.isArray(resultado.grupo.anuncios) && resultado.grupo.anuncios.length) {
            const entradaSku = registrarHistoricoRankingSkuFavoritosImediato(resultado.grupo);
            if (!entradaSku) {
                console.warn('Ranking do SKU concluido, mas o historico individual nao foi salvo.', {
                    sku: resultado.grupo.sku,
                    loja: resultado.grupo.loja,
                    anuncios: resultado.grupo.anuncios.length
                });
            }
        }
        renderizarFavoritosPesquisaResultados(grupos);
        desmarcarSkuFavoritosProcessado(item);
    }
    return grupos;
}

async function processarSkuRendererAntigoFavoritos(item, idx, contexto, loginPreparado) {
    const info = window.FavoritosV2.searchRanking.publicApi.search.montarPesquisasFavoritosSku(item, contexto.quantidade);
    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Processando SKU ${info.sku} (${idx + 1}/${contexto.selecionados.length})...`);
    if (!info.termos.length) {
        const grupo = {
            ...info,
            opcoes_promocao: window.FavoritosV2.promotionEffectuation.publicApi.options.clonarOpcoesPromocaoFavoritos(contexto.opcoesPromocao),
            anuncios: [],
            erro: `Nenhum campo Pesquisa 1 a ${contexto.quantidade} preenchido para este SKU.`
        };
        window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`SKU ${info.sku}: nenhum campo Pesquisa 1 a ${contexto.quantidade} preenchido.`, {
            erro: true
        });
        return { grupo, loginAvantPrimeiraPesquisaPreparado: loginPreparado };
    }
    const coleta = await coletarSkuRendererAntigoFavoritos(info, contexto, loginPreparado);
    const unicos = await enriquecerSkuRendererAntigoFavoritos(info, coleta.coletados);
    const grupo = await rankearSkuRendererAntigoFavoritos(item, info, unicos, contexto);
    return { grupo, loginAvantPrimeiraPesquisaPreparado: coleta.loginPreparado };
}

async function coletarSkuRendererAntigoFavoritos(info, contexto, loginPreparado) {
    const coletados = [];
    let loginAvantPrimeiraPesquisaPreparado = loginPreparado;
    for (const pesquisa of info.termos) {
        await window.FavoritosV2.searchRanking.publicApi.control.aguardarControleFavoritos();
        const loginAvantAntesDaColeta = !loginAvantPrimeiraPesquisaPreparado;
        window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`SKU ${info.sku}: pesquisando Pesquisa ${pesquisa.campo} - ${pesquisa.termo}`);
        const anuncios = await window.FavoritosV2.searchRanking.publicApi.search.buscarAnunciosFavoritosPorTermo(pesquisa.termo, {
            sku: info.sku,
            loja: info.loja,
            titulo: 'Fazendo Favorito! Aguarde...',
            subtitulo: `SKU ${info.sku} - Pesquisa ${pesquisa.campo}`,
            exigirAvantPro: true,
            loginAvantAntesDaColeta
        });
        loginAvantPrimeiraPesquisaPreparado = true;
        await window.FavoritosV2.searchRanking.publicApi.control.aguardarControleFavoritos();
        anuncios.forEach(anuncio => {
            coletados.push(window.FavoritosV2.searchRanking.publicApi.listings.normalizarAnuncioFavoritosPesquisa(anuncio, info.sku, pesquisa));
        });
    }
    return { coletados, loginPreparado: loginAvantPrimeiraPesquisaPreparado };
}

async function enriquecerSkuRendererAntigoFavoritos(info, coletados) {
    let unicos = window.FavoritosV2.searchRanking.publicApi.listings.deduplicarAnunciosFavoritos(coletados)
        .filter(window.FavoritosV2.searchRanking.publicApi.listings.anuncioFavoritosCandidatoRanking);
    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`SKU ${info.sku}: enriquecendo ${unicos.length} anuncio(s) para calcular ranking...`);
    await window.FavoritosV2.searchRanking.publicApi.control.aguardarControleFavoritos();
    await window.FavoritosV2.searchRanking.publicApi.enrichment.enriquecerAnunciosFavoritosRanking(unicos);
    for (let i = 0; i < unicos.length; i += 1) {
        const pesquisasOrigem = Array.isArray(unicos[i].pesquisas_origem) ? [...unicos[i].pesquisas_origem] : [];
        const camposOrigem = Array.isArray(unicos[i].campos_origem) ? [...unicos[i].campos_origem] : [];
        const origem = Array.isArray(unicos[i].pesquisas_origem) && unicos[i].pesquisas_origem[0]
            ? { termo: unicos[i].pesquisas_origem[0], campo: '' }
            : { termo: '', campo: '' };
        unicos[i] = window.FavoritosV2.searchRanking.publicApi.listings.normalizarAnuncioFavoritosPesquisa(unicos[i], info.sku, origem);
        if (pesquisasOrigem.length) unicos[i].pesquisas_origem = pesquisasOrigem;
        if (camposOrigem.length) unicos[i].campos_origem = camposOrigem;
    }
    unicos = window.FavoritosV2.searchRanking.publicApi.listings.deduplicarAnunciosFavoritos(unicos)
        .filter(window.FavoritosV2.searchRanking.publicApi.listings.anuncioFavoritosCandidatoRanking);
    await window.FavoritosV2.searchRanking.publicApi.control.aguardarControleFavoritos();
    return unicos;
}

async function rankearSkuRendererAntigoFavoritos(item, info, unicos, contexto) {
    const anunciosNovos = unicos.filter(anuncioFavoritosProdutoNovo);
    const removidosPorCondicao = unicos.length - anunciosNovos.length;
    if (removidosPorCondicao > 0) {
        window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`SKU ${info.sku}: removidos ${removidosPorCondicao} anuncio(s) marcados como usados.`);
    }
    const preparoDadosAvant = prepararAnunciosFavoritosRankingComDadosAvant(anunciosNovos);
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
            window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`SKU ${info.sku}: IA nao concluiu a comparacao. Mantive o ranking normal. ${err && err.message ? err.message : ''}`, {
                erro: true,
                tempoMs: 6500
            });
        }
    }
    await window.FavoritosV2.searchRanking.publicApi.control.aguardarControleFavoritos();
    if (filtroIa.removidosTotal) {
        window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`SKU ${info.sku}: IA removeu ${filtroIa.removidosTotal} anuncio(s) fora do produto do cadastro.`);
    }
    return {
        ...info,
        sku: info.sku || item.sku || '',
        loja: info.loja || item.loja || favoritosLojaSelecionadaParaApi() || '',
        opcoes_promocao: window.FavoritosV2.promotionEffectuation.publicApi.options.clonarOpcoesPromocaoFavoritos(contexto.opcoesPromocao),
        anuncios: contexto.usarIaRanking && filtroIa.usouIa
            ? filtroIa.anuncios
            : window.FavoritosV2.searchRanking.publicApi.ranking.limitarAnunciosFavoritosRanking(rankingBase),
        removidos_ia: filtroIa.removidos,
        removidos_ia_total: filtroIa.removidosTotal,
        usou_ia: !!filtroIa.usouIa,
        ia_confirmados: filtroIa.confirmados || 0,
        ia_max_confirmados: filtroIa.maxConfirmados || (contexto.usarIaRanking ? 8 : 0)
    };
}

function concluirRendererAntigoFavoritos(contexto, grupos) {
    const gruposComRanking = gruposComRankingFavoritos(grupos);
    const totalAnuncios = gruposComRanking.reduce((acc, grupo) => acc + (grupo.anuncios || []).length, 0);
    const resumoHistorico = resumoHistoricosIndividuaisFavoritos(grupos);
    if (!resumoHistorico.entrada) {
        if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Favoritos sem ranking salvo';
        if (mlWorkModalSubtitleEl) mlWorkModalSubtitleEl.textContent = `${grupos.length} SKU(s), nenhum anuncio rankeado.`;
        window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('Favoritos terminou sem anuncios rankeados. Nada foi salvo no historico; o Mercado Livre/Avant Pro nao retornou dados para as pesquisas.', {
            erro: true, tempoMs: 9000, larga: true
        });
        fecharNavegadorFavoritosAposColeta('favoritos-sem-ranking');
        return;
    }
    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Favoritos concluido: ${grupos.length} SKU(s), ${totalAnuncios} anuncio(s) rankeado(s).`, {
        tempoMs: 7000
    });
    const grupoParaAbrir = grupos.find(grupo => grupo && grupo.sku && Array.isArray(grupo.anuncios) && grupo.anuncios.length)
        || grupos.find(grupo => grupo && grupo.sku);
    if (grupoParaAbrir && grupoParaAbrir.sku) {
        favMlSkuSelecionado = grupoParaAbrir.sku;
        favMlLojaSelecionada = grupoParaAbrir.loja || favoritosLojaSelecionadaParaApi() || '';
        favMlHistoricoExecucaoSelecionadaId = FAV_ML_RANKING_ATUAL_ID;
        carregarFavoritosAnunciosSku(grupoParaAbrir.sku, grupoParaAbrir.loja || favMlLojaSelecionada, {
            manterRankingSelecionado: true
        });
    }
    if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Favoritos concluidos';
    if (mlWorkModalSubtitleEl) mlWorkModalSubtitleEl.textContent = `${grupos.length} SKU(s), ${totalAnuncios} anuncio(s) rankeado(s).`;
    const finalizouEmSegundoPlano = mlFavoritosExecucaoEmSegundoPlano;
    mlFavoritosEmExecucao = false;
    mlFavoritosExecucaoEmSegundoPlano = false;
    mlFavoritosPausado = false;
    mlFavoritosJobUltimoStatus = null;
    atualizarFiltroAzulFavoritos();
    atualizarContadorSkuSidebarSelecionados();
    window.FavoritosV2.searchRanking.publicApi.status.esconderBalaoFavoritosStatus();
    fecharNavegadorFavoritosAposColeta('favoritos-renderer-concluido');
    if (!finalizouEmSegundoPlano) mudarAba('favoritos');
}

function tratarErroRendererAntigoFavoritos(err) {
    if (mlFavoritosCancelado || (err && (err.canceladoFavoritos || err.name === 'AbortError'))) {
        if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Favoritos cancelado';
        if (typeof limparStatusTerminalFavoritos === 'function') limparStatusTerminalFavoritos({ status: 'canceled' });
        return false;
    }
    if (typeof window.FavoritosV2.searchRanking.publicApi.status.erroEhLoginMercadoLivreFavoritos === 'function' && window.FavoritosV2.searchRanking.publicApi.status.erroEhLoginMercadoLivreFavoritos(err)) {
        exibirErroAcessoRendererAntigoFavoritos(
            err,
            'Login do Mercado Livre necessario',
            'Conclua o acesso no navegador interno e tente novamente.',
            'Conclua o acesso ou verificacao no navegador interno.',
            'O Mercado Livre pediu login/verificacao no navegador interno. Conclua o acesso e tente Fazer favoritos novamente.',
            12000
        );
        return true;
    }
    if (typeof window.FavoritosV2.searchRanking.publicApi.status.erroEhLoginAvantProFavoritos === 'function' && window.FavoritosV2.searchRanking.publicApi.status.erroEhLoginAvantProFavoritos(err)) {
        exibirErroAcessoRendererAntigoFavoritos(
            err,
            'Avant Pro sem dados',
            'Confirme manualmente no navegador interno se o Avant Pro esta pronto e tente novamente.',
            'Confirme manualmente no navegador interno se o Avant Pro esta pronto.',
            'Avant Pro nao retornou dados coletaveis. Confirme manualmente no navegador interno se o Avant Pro esta pronto e tente Fazer favoritos novamente.',
            15000
        );
        return true;
    }
    if (typeof window.FavoritosV2.searchRanking.publicApi.status.erroEhColetaMercadoLivreFavoritos === 'function' && window.FavoritosV2.searchRanking.publicApi.status.erroEhColetaMercadoLivreFavoritos(err)) {
        exibirErroAcessoRendererAntigoFavoritos(
            err,
            'Coleta ML/Avant sem dados',
            'Confira o navegador interno e tente novamente.',
            'Confira se o Mercado Livre carregou e se o Avant Pro esta conectado.',
            'Mercado Livre/Avant Pro nao retornou dados coletaveis. Confira o navegador interno e tente novamente.',
            15000
        );
        return true;
    }
    if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Erro ao fazer favoritos';
    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Erro ao fazer favoritos: ${err && err.message ? err.message : err}`, { erro: true });
    return false;
}

function exibirErroAcessoRendererAntigoFavoritos(err, titulo, subtituloModal, subtituloBalao, fallback, tempoMs) {
    mlFavoritosExecucaoEmSegundoPlano = false;
    if (typeof mudarAba === 'function') {
        try { mudarAba('navegador'); } catch (_err) {}
    }
    if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = titulo;
    if (mlWorkModalSubtitleEl) mlWorkModalSubtitleEl.textContent = subtituloModal;
    abrirBalaoResultadosMl({
        titulo,
        subtitulo: subtituloBalao,
        mostrarFavoritos: true,
        browserCompleto: true,
        forcarExibicao: true
    });
    if (typeof agendarAtualizacaoPosicaoNavegadorMlShell === 'function') {
        setTimeout(() => agendarAtualizacaoPosicaoNavegadorMlShell(), 120);
    }
    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(err && err.message ? err.message : fallback, {
        erro: true,
        larga: true,
        titulo,
        tempoMs
    });
}

function limparRendererAntigoFavoritos(contexto) {
    const canceladoAoFinal = !!mlFavoritosCancelado;
    if (canceladoAoFinal && typeof limparStatusTerminalFavoritos === 'function') {
        limparStatusTerminalFavoritos({ status: 'canceled' });
    }
    mlFavoritosEmExecucao = false;
    mlFavoritosExecucaoEmSegundoPlano = false;
    mlFavoritosCancelado = false;
    mlFavoritosPausado = false;
    mlFavoritosJobUltimoStatus = null;
    mlFavoritosAbortController = null;
    if (contexto.executarEmBackground && !contexto.manterNavegadorVisivelAposErro) {
        pararNavegadorFavoritosBackground(canceladoAoFinal ? {
            status: 'canceled',
            message: '',
            reason: 'favoritos-renderer-cancelado'
        } : {});
    }
    atualizarFiltroAzulFavoritos();
    atualizarContadorSkuSidebarSelecionados();
}
