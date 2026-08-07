async function coletarAnunciosPrimeiraPaginaFavoritosSemFluxoAntigo(info, pesquisa, opcoes = {}) {
    const contexto = criarContextoPrimeiraPaginaFavoritos(info, pesquisa, opcoes);
    if (contexto.vazio) return [];
    await abrirPesquisaPrimeiraPaginaFavoritos(contexto);
    await esperarNovaColetaFavoritos(700);
    const tempoNavegacaoMs = Math.max(0, Date.now() - contexto.inicioNavegacao);
    const resultado = await executarColetaPrimeiraPaginaFavoritos(contexto);
    window.FavoritosV2.searchRanking.publicApi.control.verificarCancelamentoFavoritos();
    validarResultadoPrimeiraPaginaFavoritos(resultado, contexto.workerId);
    const coletado = await resolverAnunciosPrimeiraPaginaFavoritos(contexto, resultado);
    return montarSaidaPrimeiraPaginaFavoritos(contexto, resultado, coletado, tempoNavegacaoMs);
}

function criarContextoPrimeiraPaginaFavoritos(info, pesquisa, opcoes = {}) {
    const termo = String(pesquisa && pesquisa.termo || '').trim();
    if (!termo) return { vazio: true, termo, info, pesquisa, opcoes };
    const webview = opcoes.webview || null;
    const workerId = String(opcoes.workerId || '').trim();
    const informarStatus = typeof opcoes.onStatus === 'function'
        ? opcoes.onStatus
        : (mensagem, detalhes = {}) => window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(mensagem, detalhes);
    if (!webview && typeof abrirMercadoLivreNoPrograma !== 'function') {
        throw erroNovaColetaFavoritos('Navegador interno indisponivel para abrir a pesquisa.');
    }
    const limiteAnunciosPrimeiraPesquisa = Number(opcoes.maxAnuncios)
        || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX)
        || 80;
    const url = typeof construirUrlPesquisaMercadoLivre === 'function'
        ? construirUrlPesquisaMercadoLivre(termo)
        : `https://lista.mercadolivre.com.br/${encodeURIComponent(termo)}`;
    if (!webview && typeof mlUrlInput !== 'undefined' && mlUrlInput) mlUrlInput.value = url;
    return {
        info,
        pesquisa,
        opcoes,
        termo,
        webview,
        workerId,
        informarStatus,
        limite: limiteAnunciosPrimeiraPesquisa,
        url,
        inicioNavegacao: Date.now()
    };
}

async function iniciarNavegadorWorkerPrimeiraPaginaFavoritos(contexto) {
    const { webview, workerId, opcoes, info, termo, url } = contexto;
    if (webview && typeof webview.navigate === 'function') {
        const workerStart = await webview.navigate(url, {
            poolId: opcoes.poolId || '',
            sku: info && info.sku || '',
            attempt: Number(opcoes.attempt) || 1,
            show: true,
            message: `Trabalhador ${workerId || ''}: abrindo ${termo}`
        });
        return !!(workerStart && workerStart.success !== false);
    }
    const apiWorker = obterElectronApiFavoritosExecucao();
    if (!navegadorMlEmSegundoPlano() || !apiWorker || typeof apiWorker.startFavoritosWorkerBrowser !== 'function') {
        return false;
    }
    if (typeof forcarProxyNavegadorFavoritosWorker === 'function') {
        forcarProxyNavegadorFavoritosWorker(url);
    }
    const workerStart = await apiWorker.startFavoritosWorkerBrowser(url).catch((err) => {
        console.warn('Nao foi possivel posicionar o worker na URL da pesquisa:', err);
        return null;
    });
    if (typeof forcarProxyNavegadorFavoritosWorker === 'function') {
        const proxy = forcarProxyNavegadorFavoritosWorker(workerStart && workerStart.url ? workerStart.url : url);
        if (proxy && workerStart && workerStart.url) proxy.currentUrl = workerStart.url;
    }
    return !!(workerStart && workerStart.success !== false);
}

async function abrirPesquisaPrimeiraPaginaFavoritos(contexto) {
    if (contexto.vazio) return;
    const termo = contexto.termo;
    const usandoWorker = await iniciarNavegadorWorkerPrimeiraPaginaFavoritos(contexto);
    contexto.informarStatus(`Abrindo pesquisa "${termo}" no Mercado Livre...`, {
        manterNavegadorVisivel: !usandoWorker && !navegadorMlEmSegundoPlano(),
        larga: true,
        titulo: contexto.opcoes.primeiraPesquisa ? 'Primeira pesquisa' : 'Pesquisa'
    });
    let abriu = true;
    if (!usandoWorker) {
        abriu = await abrirMercadoLivreNoPrograma({
            termoPesquisa: termo,
            titulo: contexto.opcoes.titulo || 'Fazendo Favorito! Aguarde...',
            subtitulo: contexto.opcoes.subtitulo || `SKU ${contexto.info && contexto.info.sku || ''} - Pesquisa ${contexto.pesquisa && contexto.pesquisa.campo || ''}`,
            mostrarFavoritos: true,
            browserCompleto: true,
            forcarExibicao: !navegadorMlEmSegundoPlano(),
            aguardarPesquisaMs: 900,
            apenasAbrirUrl: true,
            confirmarPesquisa: false,
            agendarPosicaoAntes: false,
            reposicionarDepois: false
        }).catch(() => false);
    } else {
        await confirmarNavegacaoWorkerPrimeiraPaginaFavoritos(contexto);
    }
    if (!abriu) {
        throw erroNovaColetaFavoritos(`Nao consegui abrir a pesquisa "${termo}" no navegador interno.`);
    }
}

async function confirmarNavegacaoWorkerPrimeiraPaginaFavoritos(contexto) {
    const navegador = contexto.webview || mlWebviewEl;
    if (!navegador || typeof navegador.executeJavaScript !== 'function') return;
    const urlConfirmada = await navegador.executeJavaScript('location.href', true).catch(() => '');
    if (urlConfirmada && /^https?:\/\//i.test(urlConfirmada)) navegador.currentUrl = urlConfirmada;
    await verificarBloqueioGlobalMercadoLivreFavoritos(navegador, contexto.workerId);
    await esperarNovaColetaFavoritos(350);
}

async function executarColetaPrimeiraPaginaFavoritos(contexto) {
    if (contexto.vazio) return null;
    const limiteAnunciosPrimeiraPesquisa = contexto.limite;
    contexto.informarStatus(`Coletando todos os anuncios da primeira pagina de "${contexto.termo}"...`, {
        manterNavegadorVisivel: true,
        larga: true,
        titulo: 'Coleta da pagina'
    });
    if (typeof coletarPrimeiraPaginaFavoritosControlada !== 'function') return null;
    const executarColetaControlada = (signal) => coletarPrimeiraPaginaFavoritosControlada({
        maxAnuncios: limiteAnunciosPrimeiraPesquisa,
        tempoLimiteMs: 90000,
        maxPassadas: 2,
        loteCliques: 6,
        signal: signal || window.FavoritosV2.searchRanking.publicApi.control.sinalFavoritosAtual(),
        webview: contexto.webview || undefined,
        onProgress: progresso => publicarProgressoPrimeiraPaginaFavoritos(contexto, progresso)
    });
    const promise = typeof window.FavoritosV2.searchRanking.publicApi.control.executarComTimeoutFavoritos === 'function'
        ? window.FavoritosV2.searchRanking.publicApi.control.executarComTimeoutFavoritos(executarColetaControlada, 92000, window.FavoritosV2.searchRanking.publicApi.control.sinalFavoritosAtual())
        : executarColetaControlada(window.FavoritosV2.searchRanking.publicApi.control.sinalFavoritosAtual());
    return promise.catch((err) => {
        if (mlFavoritosCancelado || (err && (
            err.loginMercadoLivreNecessario
            || err.name === 'AbortError'
            || err.name === 'TimeoutError'
            || err.favoritosTimeout
        ))) throw err;
        console.warn('Coleta controlada da primeira pagina falhou:', err);
        return null;
    });
}

function publicarProgressoPrimeiraPaginaFavoritos(contexto, progresso) {
    if (mlFavoritosCancelado || !mlFavoritosEmExecucao) return;
    contexto.informarStatus(
        formatarProgressoColetaPrimeiraPaginaFavoritos(contexto.pesquisa && contexto.pesquisa.campo || 1, progresso),
        { manterNavegadorVisivel: true, larga: true, titulo: 'Coleta da pagina' }
    );
}

function validarResultadoPrimeiraPaginaFavoritos(resultado, workerId = '') {
    if (!resultado || !(
        resultado.loginAvantBloqueado
        || (resultado.resumo && (resultado.resumo.loginAvantBloqueado || resultado.resumo.login_avant_bloqueado))
    )) return;
    throw erroNovaColetaFavoritos('A sessao do Avant Pro precisa ser confirmada.', {
        loginAvantProNecessario: true,
        workerId
    });
}

async function resolverAnunciosPrimeiraPaginaFavoritos(contexto, resultado) {
    let anuncios = listaAnunciosResultadoColetaFavoritos(resultado);
    let totalVisiveis = totalVisiveisResultadoColetaFavoritos(resultado, anuncios);
    if (!anuncios.length && typeof rolarMercadoLivreFavoritos === 'function') {
        await rolarMercadoLivreFavoritos(0, contexto.webview || undefined).catch(() => null);
        await esperarNovaColetaFavoritos(900);
    }
    if (!anuncios.length && typeof extrairCardsMercadoLivreBasicoWebview === 'function') {
        const basico = await extrairCardsMercadoLivreBasicoWebview({
            limite: contexto.limite,
            webview: contexto.webview || undefined
        }).catch(() => null);
        anuncios = listaAnunciosResultadoColetaFavoritos(basico);
        totalVisiveis = totalVisiveisResultadoColetaFavoritos(basico, anuncios);
    }
    if (!anuncios.length && typeof extrairBaseMercadoLivreEmergencialWebview === 'function') {
        const emergencia = await extrairBaseMercadoLivreEmergencialWebview({
            limite: contexto.limite,
            timeoutMs: 12000,
            webview: contexto.webview || undefined
        }).catch(() => null);
        anuncios = listaAnunciosResultadoColetaFavoritos(emergencia);
        totalVisiveis = totalVisiveisResultadoColetaFavoritos(emergencia, anuncios);
    }
    if (!anuncios.length) {
        throw erroNovaColetaFavoritos(`Nenhum anuncio foi coletado para "${contexto.termo}".`, {
            falhaColetaFavoritos: true,
            workerId: contexto.workerId
        });
    }
    return { anuncios, totalVisiveis };
}

function aplicarCachePrimeiraPaginaFavoritos(contexto, anuncios) {
    let saida = anuncios;
    const cacheContext = { termo: contexto.termo, sku: contexto.info && contexto.info.sku || contexto.termo };
    if (typeof aplicarCacheAvantAosAnuncios === 'function') {
        saida = aplicarCacheAvantAosAnuncios(saida, cacheContext);
    }
    if (typeof salvarCacheAvantDosAnuncios === 'function') {
        salvarCacheAvantDosAnuncios(saida, cacheContext);
    }
    return saida;
}

function montarSaidaPrimeiraPaginaFavoritos(contexto, resultado, coletado, tempoNavegacaoMs) {
    if (contexto.vazio) return [];
    const anuncios = aplicarCachePrimeiraPaginaFavoritos(contexto, coletado.anuncios);
    const saida = anuncios.slice(0, contexto.limite).map(item => ({
        ...item,
        origem_dados: item && item.origem_dados || 'avantpro_primeira_pagina_controlada'
    }));
    const resumo = {
        ...(resultado && resultado.resumo || (
            typeof resumoPrimeiraPaginaFavoritos === 'function'
                ? resumoPrimeiraPaginaFavoritos(coletado.totalVisiveis, anuncios, { etapa: 'fallback_final' })
                : {}
        )),
        tempo_navegacao_ms: tempoNavegacaoMs
    };
    anexarMetadadosPrimeiraPaginaFavoritos(saida, resultado, coletado.totalVisiveis, resumo);
    return saida;
}

function anexarMetadadosPrimeiraPaginaFavoritos(saida, resultado, totalVisiveis, resumo) {
    const avantNaoVinculado = Number(resultado && resultado.resumo && resultado.resumo.avant_nao_vinculado) || 0;
    const loginBloqueado = !!(resultado && (
        resultado.loginAvantBloqueado
        || (resultado.resumo && (resultado.resumo.loginAvantBloqueado || resultado.resumo.login_avant_bloqueado))
    ));
    const metadados = {
        __favoritosTotalVisiveis: totalVisiveis,
        __favoritosResumo: resumo,
        __favoritosAvantNaoVinculado: avantNaoVinculado,
        __favoritosTempoEsgotado: !!(resultado && resultado.tempoEsgotado),
        __favoritosLoginAvantBloqueado: loginBloqueado
    };
    try {
        Object.entries(metadados).forEach(([name, value]) => {
            Object.defineProperty(saida, name, { value, enumerable: false });
        });
    } catch (_err) {
        Object.assign(saida, metadados);
    }
}
