function criarProxyNavegadorFavoritosWorkerPool(workerId, initialUrl = '') {
    try {
        const controller = window.FavoritosV2
            && window.FavoritosV2.browser
            && window.FavoritosV2.browser.workerController;
        return controller && typeof controller.criarProxy === 'function'
            ? controller.criarProxy(workerId, initialUrl)
            : null;
    } catch (_err) {
        return null;
    }
}

function formatarContadoresFilaFavoritos(estado = {}) {
    const contadores = `${Number(estado.active) || 0} ativos | ${Number(estado.queued) || 0} na fila | ${Number(estado.completed) || 0} concluidos | ${Number(estado.retrying) || 0} em nova tentativa`;
    const detalhe = String(estado.detail || '').trim();
    return detalhe ? `${contadores} | ${detalhe}` : contadores;
}

let mlFavoritosPoolUltimoEstado = null;
let mlFavoritosPoolRenderTimer = null;
let mlFavoritosPoolStatusTimer = null;
let mlFavoritosPoolStatusPendente = null;

function publicarEstadoFilaFavoritosPool(pendente) {
    if (!pendente) return '';
    const { estado, extra, mensagem } = pendente;
    if (mlWorkModalSubtitleEl) mlWorkModalSubtitleEl.textContent = mensagem;
    emitirEstadoWorkerFavoritosExecucao('jk-favoritos-worker-progress', {
        active: true,
        poolId: extra.poolId || '',
        workerId: extra.workerId || '',
        sku: extra.sku || '',
        attempt: Number(extra.attempt) || 1,
        status: extra.event || 'running',
        phase: extra.phase || '',
        taskStartedAt: Number(extra.taskStartedAt) || 0,
        taskElapsedMs: Number(extra.taskElapsedMs) || 0,
        timings: extra.timings && typeof extra.timings === 'object' ? extra.timings : null,
        message: mensagem,
        detail: extra.detail || '',
        queue: {
            total: Number(estado.total) || 0,
            active: Number(estado.active) || 0,
            queued: Number(estado.queued) || 0,
            completed: Number(estado.completed) || 0,
            succeeded: Number(estado.succeeded) || 0,
            failed: Number(estado.failed) || 0,
            retrying: Number(estado.retrying) || 0
        }
    });
    return mensagem;
}

function limparThrottleEstadoFilaFavoritosPool() {
    if (mlFavoritosPoolStatusTimer) clearTimeout(mlFavoritosPoolStatusTimer);
    mlFavoritosPoolStatusTimer = null;
    mlFavoritosPoolStatusPendente = null;
}

function atualizarEstadoFilaFavoritosPool(estado = {}, extra = {}) {
    mlFavoritosPoolUltimoEstado = { ...estado, ...extra };
    const mensagem = formatarContadoresFilaFavoritos(mlFavoritosPoolUltimoEstado);
    mlFavoritosPoolStatusPendente = { estado: { ...estado }, extra: { ...extra }, mensagem };
    const evento = String(extra.event || '').toLowerCase();
    const imediato = new Set([
        'pool-started', 'started', 'retrying', 'retry-wait', 'completed',
        'failed', 'fatal', 'pool-completed', 'canceled', 'paused', 'resumed'
    ]).has(evento);
    if (imediato) {
        if (mlFavoritosPoolStatusTimer) clearTimeout(mlFavoritosPoolStatusTimer);
        mlFavoritosPoolStatusTimer = null;
        const pendente = mlFavoritosPoolStatusPendente;
        mlFavoritosPoolStatusPendente = null;
        publicarEstadoFilaFavoritosPool(pendente);
    } else if (!mlFavoritosPoolStatusTimer) {
        mlFavoritosPoolStatusTimer = setTimeout(() => {
            mlFavoritosPoolStatusTimer = null;
            const pendente = mlFavoritosPoolStatusPendente;
            mlFavoritosPoolStatusPendente = null;
            publicarEstadoFilaFavoritosPool(pendente);
        }, 250);
    }
    return mensagem;
}

function agendarRenderResultadosFilaFavoritos(grupos) {
    if (mlFavoritosPoolRenderTimer) return;
    mlFavoritosPoolRenderTimer = setTimeout(() => {
        mlFavoritosPoolRenderTimer = null;
        renderizarFavoritosPesquisaResultados((Array.isArray(grupos) ? grupos : []).filter(Boolean));
    }, 80);
}

function erroFatalFilaFavoritos(err) {
    return (typeof window.FavoritosV2.searchRanking.publicApi.status.erroEhLoginMercadoLivreFavoritos === 'function' && window.FavoritosV2.searchRanking.publicApi.status.erroEhLoginMercadoLivreFavoritos(err))
        || (typeof window.FavoritosV2.searchRanking.publicApi.status.erroEhLoginAvantProFavoritos === 'function' && window.FavoritosV2.searchRanking.publicApi.status.erroEhLoginAvantProFavoritos(err))
        || !!(err && (err.loginMercadoLivreNecessario || err.loginAvantProNecessario));
}

async function processarSkuCompletoFavoritosNoWorker(item, contexto = {}) {
    window.FavoritosV2.searchRanking.publicApi.control.verificarCancelamentoFavoritos();
    await window.FavoritosV2.searchRanking.publicApi.control.aguardarControleFavoritos();
    const inicioTarefa = Number(contexto.taskStartedAt) || Date.now();
    const infoBase = window.FavoritosV2.searchRanking.publicApi.search.montarPesquisasFavoritosSku(item, contexto.quantidadePesquisas);
    const info = { ...infoBase, quantidade_pesquisas: contexto.quantidadePesquisas };
    if (!info.termos.length) {
        return montarGrupoSemPesquisaNovaColetaFavoritos(info, contexto.opcoesPromocao);
    }
    const coleta = await coletarPesquisasSkuFavoritosWorker(info, contexto);
    const enriquecimento = await enriquecerColetaSkuFavoritosWorker(info, coleta, contexto);
    const ranking = await calcularRankingSkuFavoritosWorker(item, info, enriquecimento.unicos, contexto);
    return montarResultadoSkuFavoritosWorker({
        item,
        info,
        contexto,
        inicioTarefa,
        resumosColeta: coleta.resumosColeta,
        unicos: enriquecimento.unicos,
        tempoEnriquecimentoMs: enriquecimento.tempoEnriquecimentoMs,
        ranking
    });
}

async function coletarPesquisasSkuFavoritosWorker(info, contexto) {
    const coletados = [];
    const resumosColeta = [];
    for (let pesquisaIndex = 0; pesquisaIndex < info.termos.length; pesquisaIndex += 1) {
        window.FavoritosV2.searchRanking.publicApi.control.verificarCancelamentoFavoritos();
        await window.FavoritosV2.searchRanking.publicApi.control.aguardarControleFavoritos();
        const pesquisa = info.termos[pesquisaIndex];
        const ordemPesquisa = `${pesquisaIndex + 1}/${info.termos.length}`;
        if (typeof contexto.onDetail === 'function') {
            contexto.onDetail(`SKU ${info.sku}: pesquisa ${ordemPesquisa} - ${pesquisa.termo}.`);
        }
        const anuncios = await coletarAnunciosPrimeiraPaginaFavoritosSemFluxoAntigo(info, pesquisa, {
            primeiraPesquisa: contexto.index === 0 && pesquisaIndex === 0,
            maxAnuncios: contexto.limiteAnunciosPrimeiraPesquisa,
            webview: contexto.webview,
            workerId: contexto.workerId,
            poolId: contexto.poolId,
            attempt: contexto.attempt,
            onStatus: mensagem => {
                if (typeof contexto.onDetail === 'function') contexto.onDetail(mensagem);
            }
        });
        const normalizadosPesquisa = anuncios.map(anuncio => window.FavoritosV2.searchRanking.publicApi.listings.normalizarAnuncioFavoritosPesquisa(anuncio, info.sku, pesquisa));
        normalizadosPesquisa.forEach(anuncio => coletados.push(anuncio));
        const resumoPesquisa = montarResumoPesquisaFavoritos(
            pesquisaIndex + 1,
            Number(anuncios.__favoritosTotalVisiveis) || anuncios.length,
            normalizadosPesquisa,
            anuncios.__favoritosResumo
        );
        resumoPesquisa.tempo_esgotado = !!anuncios.__favoritosTempoEsgotado;
        resumosColeta.push({
            ...resumoPesquisa,
            termo: pesquisa.termo,
            campo: pesquisa.campo,
            tempo_enriquecimento_ms: 0
        });
    }
    return { coletados, resumosColeta };
}

async function enriquecerColetaSkuFavoritosWorker(info, coleta, contexto) {
    let unicos = window.FavoritosV2.searchRanking.publicApi.listings.deduplicarAnunciosFavoritos(coleta.coletados)
        .filter(window.FavoritosV2.searchRanking.publicApi.listings.anuncioFavoritosCandidatoRanking);
    if (typeof contexto.onDetail === 'function') {
        contexto.onDetail(`SKU ${info.sku}: completando os dados de ${unicos.length} anuncios coletados.`);
    }
    const contextoEnriquecimento = contexto.contextoEnriquecimento || (
        typeof window.FavoritosV2.searchRanking.publicApi.enrichment.criarContextoEnriquecimentoFavoritosExecucao === 'function'
            ? window.FavoritosV2.searchRanking.publicApi.enrichment.criarContextoEnriquecimentoFavoritosExecucao()
            : null
    );
    const inicioEnriquecimentoFinal = Date.now();
    await window.FavoritosV2.searchRanking.publicApi.enrichment.enriquecerAnunciosFavoritosRanking(unicos, contextoEnriquecimento, { fechamento: true });
    unicos = window.FavoritosV2.searchRanking.publicApi.listings.deduplicarAnunciosFavoritos(unicos)
        .filter(window.FavoritosV2.searchRanking.publicApi.listings.anuncioFavoritosCandidatoRanking);
    await window.FavoritosV2.searchRanking.publicApi.control.aguardarControleFavoritos();
    return {
        unicos,
        tempoEnriquecimentoMs: Math.max(0, Date.now() - inicioEnriquecimentoFinal)
    };
}

async function calcularRankingSkuFavoritosWorker(item, info, unicos, contexto) {
    if (typeof contexto.onDetail === 'function') contexto.onDetail(`SKU ${info.sku}: calculando ranking.`);
    const inicioRanking = Date.now();
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
        anunciosRanking,
        filtroIa,
        tempoRankingMs: Math.max(0, Date.now() - inicioRanking)
    };
}

function montarResultadoSkuFavoritosWorker(dados) {
    const {
        item, info, contexto, inicioTarefa, resumosColeta, unicos,
        tempoEnriquecimentoMs, ranking
    } = dados;
    const duracaoTarefaMs = Math.max(0, Date.now() - inicioTarefa);
    const somarTempo = campo => resumosColeta.reduce(
        (total, resumo) => total + (Number(resumo[campo]) || 0),
        0
    );
    const timings = {
        navigationMs: somarTempo('tempo_navegacao_ms'),
        materializationMs: somarTempo('tempo_materializacao_ms'),
        avantMs: somarTempo('tempo_avant_ms'),
        finalizationMs: somarTempo('tempo_finalizacao_ms'),
        enrichmentMs: tempoEnriquecimentoMs,
        rankingMs: ranking.tempoRankingMs,
        totalTaskMs: duracaoTarefaMs
    };
    return {
        ...info,
        sku: info.sku || item.sku || contexto.skuFallback || '',
        loja: info.loja || item.loja || favoritosLojaSelecionadaParaApi() || '',
        opcoes_promocao: window.FavoritosV2.promotionEffectuation.publicApi.options.clonarOpcoesPromocaoFavoritos(contexto.opcoesPromocao),
        anuncios: ranking.anunciosRanking.slice(0, contexto.limiteRankingFinal),
        removidos_ia: ranking.filtroIa.removidos,
        removidos_ia_total: ranking.filtroIa.removidosTotal,
        usou_ia: !!ranking.filtroIa.usouIa,
        ia_confirmados: ranking.filtroIa.confirmados || 0,
        ia_max_confirmados: ranking.filtroIa.maxConfirmados || (contexto.usarIaRanking ? 8 : 0),
        fonte_coleta: 'avantpro_primeira_pagina_pool',
        worker_id: contexto.workerId || 'w0',
        tentativa: Number(contexto.attempt) || 1,
        duracao_execucao_ms: Math.max(0, Date.now() - mlFavoritosExecucaoIniciadaEmMs),
        duracao_tarefa_ms: duracaoTarefaMs,
        timings,
        resumo_coleta: resumosColeta,
        total_coletado: unicos.length,
        total_com_dados_avant: window.FavoritosV2.searchRanking.publicApi.search.filtrarAnunciosFavoritosComDadosAvant(unicos).length
    };
}

async function executarFilaSkusFavoritosPool(selecionadosLista, grupos, contexto = {}) {
    const api = obterElectronApiFavoritosExecucao();
    prepararEstadoFilaFavoritosPool();
    const scheduler = obterSchedulerFilaFavoritosPool();
    const pool = await iniciarNavegadoresFilaFavoritosPool(api, selecionadosLista, contexto);
    const proxies = criarProxiesFilaFavoritosPool(pool.workerIds);
    const indicesFinalizados = new Set();
    const concluirResultado = criarFinalizadorResultadoFilaFavoritos(
        indicesFinalizados,
        grupos,
        contexto
    );
    const contextoEnriquecimento = typeof window.FavoritosV2.searchRanking.publicApi.enrichment.criarContextoEnriquecimentoFavoritosExecucao === 'function'
        ? window.FavoritosV2.searchRanking.publicApi.enrichment.criarContextoEnriquecimentoFavoritosExecucao()
        : null;
    let resultadoFila;
    try {
        resultadoFila = await scheduler.runQueue(criarOpcoesExecucaoFilaFavoritos({
            api,
            pool,
            proxies,
            contexto,
            concluirResultado,
            contextoEnriquecimento,
            selecionadosLista
        }));
    } catch (err) {
        limparThrottleEstadoFilaFavoritosPool();
        throw err;
    }
    return finalizarExecucaoFilaFavoritos(resultadoFila, selecionadosLista, grupos, concluirResultado);
}

function prepararEstadoFilaFavoritosPool() {
    mlFavoritosPoolUltimoEstado = null;
    limparThrottleEstadoFilaFavoritosPool();
    if (mlFavoritosPoolRenderTimer) clearTimeout(mlFavoritosPoolRenderTimer);
    mlFavoritosPoolRenderTimer = null;
}

function obterSchedulerFilaFavoritosPool() {
    const scheduler = window.FavoritosV2
        && window.FavoritosV2.browser
        && window.FavoritosV2.browser.workerPool;
    if (!scheduler || typeof scheduler.runQueue !== 'function') {
        throw erroNovaColetaFavoritos('Agendador dos trabalhadores do Favoritos indisponivel.');
    }
    return scheduler;
}

async function iniciarNavegadoresFilaFavoritosPool(api, selecionadosLista, contexto) {
    const suportaPool = !!(
        api
        && typeof api.startFavoritosWorkersPool === 'function'
        && typeof api.stopFavoritosWorkersPool === 'function'
    );
    const quantidadeWorkers = suportaPool ? Math.min(4, selecionadosLista.length) : 1;
    let poolId = '';
    let workerIds = ['w0'];
    if (suportaPool) {
        const statusPool = await api.startFavoritosWorkersPool({
            size: quantidadeWorkers,
            visible: true,
            initialUrl: contexto.initialUrl,
            deferInitialNavigation: true,
            startedAt: mlFavoritosExecucaoIniciadaEmMs,
            message: `${quantidadeWorkers} trabalhador(es) iniciado(s).`
        });
        poolId = String(statusPool && statusPool.poolId || '');
        workerIds = Array.from({ length: quantidadeWorkers }, (_item, index) => `w${index + 1}`);
        window.__JK_FAVORITOS_WORKERS_POOL_ACTIVE = true;
    } else {
        await prepararNavegadorFavoritosBackground(contexto.initialUrl);
    }
    return { suportaPool, quantidadeWorkers, poolId, workerIds };
}

function criarProxiesFilaFavoritosPool(workerIds) {
    const proxies = new Map(workerIds.map(workerId => [
        workerId,
        workerId === 'w0' ? mlWebviewEl : criarProxyNavegadorFavoritosWorkerPool(workerId, '')
    ]));
    if ([...proxies.values()].some(proxy => !proxy)) {
        throw erroNovaColetaFavoritos('Nao foi possivel vincular todos os navegadores trabalhadores.');
    }
    return proxies;
}

function criarFinalizadorResultadoFilaFavoritos(indicesFinalizados, grupos, contexto) {
    return (index, resultado, item) => {
        if (indicesFinalizados.has(index)) return;
        if (resultado && resultado.success && resultado.value) {
            const grupo = resultado.value;
            grupos[index] = grupo;
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
            desmarcarSkuFavoritosProcessado(item);
            indicesFinalizados.add(index);
            agendarRenderResultadosFilaFavoritos(grupos);
            return;
        }
        if (resultado && !resultado.fatal && !resultado.canceled) {
            const infoBase = window.FavoritosV2.searchRanking.publicApi.search.montarPesquisasFavoritosSku(item, contexto.quantidadePesquisas);
            grupos[index] = {
                ...infoBase,
                quantidade_pesquisas: contexto.quantidadePesquisas,
                anuncios: [],
                erro: resultado.error && resultado.error.message ? resultado.error.message : 'Falha apos duas tentativas.',
                worker_id: resultado.workerId || '',
                tentativa: resultado.attempt || 2,
                total_coletado: 0,
                total_com_dados_avant: 0
            };
            indicesFinalizados.add(index);
            agendarRenderResultadosFilaFavoritos(grupos);
        }
    };
}

function criarOpcoesExecucaoFilaFavoritos(dados) {
    const { api, pool, proxies, contexto, concluirResultado, contextoEnriquecimento, selecionadosLista } = dados;
    return {
        items: selecionadosLista,
        concurrency: pool.quantidadeWorkers,
        workerIds: pool.workerIds,
        retryCount: 1,
        retryDelayMs: 1000,
        signal: window.FavoritosV2.searchRanking.publicApi.control.sinalFavoritosAtual(),
        waitUntilRunnable: async () => window.FavoritosV2.searchRanking.publicApi.control.aguardarControleFavoritos(),
        isFatal: erroFatalFilaFavoritos,
        onFatal: async (err, meta) => encerrarFilaFatalFavoritos(api, pool, err, meta),
        onState: estado => receberEstadoExecucaoFilaFavoritos(estado, pool.poolId, concluirResultado),
        processItem: async (item, meta) => processarSkuCompletoFavoritosNoWorker(item, {
            ...contexto,
            poolId: pool.poolId,
            workerId: meta.workerId,
            index: meta.index,
            attempt: meta.attempt,
            taskStartedAt: meta.taskStartedAt,
            contextoEnriquecimento,
            webview: proxies.get(meta.workerId),
            onDetail: detail => atualizarEstadoFilaFavoritosPool(mlFavoritosPoolUltimoEstado || meta.state, {
                poolId: pool.poolId,
                workerId: meta.workerId,
                sku: item && item.sku || '',
                attempt: meta.attempt,
                event: meta.attempt > 1 ? 'retrying' : 'running',
                detail
            })
        })
    };
}

async function encerrarFilaFatalFavoritos(api, pool, err, meta) {
    if (mlFavoritosAbortController && !mlFavoritosAbortController.signal.aborted) {
        mlFavoritosAbortController.abort();
    }
    if (!pool.suportaPool) return;
    await api.stopFavoritosWorkersPool({
        status: 'error',
        reason: 'favoritos-pool-login-global',
        message: err && err.message ? err.message : 'Corrija o acesso na janela indicada.',
        destroy: true,
        keepWorkerId: meta.workerId
    }).catch(() => null);
}

function receberEstadoExecucaoFilaFavoritos(estado, poolId, concluirResultado) {
    const item = estado.item || {};
    if (estado.event === 'completed') {
        concluirResultado(estado.index, {
            success: true,
            value: estado.value,
            workerId: estado.workerId,
            attempt: estado.attempt
        }, item);
    } else if (estado.event === 'failed') {
        concluirResultado(estado.index, {
            success: false,
            fatal: false,
            error: estado.error,
            workerId: estado.workerId,
            attempt: estado.attempt
        }, item);
    }
    atualizarEstadoFilaFavoritosPool(estado, {
        poolId,
        workerId: estado.workerId || '',
        sku: item.sku || '',
        attempt: estado.attempt || 1,
        event: estado.event,
        taskStartedAt: estado.taskStartedAt,
        taskElapsedMs: estado.taskElapsedMs,
        timings: estado.timings || null
    });
}

function finalizarExecucaoFilaFavoritos(resultadoFila, selecionadosLista, grupos, concluirResultado) {
    resultadoFila.results.forEach((resultado, index) => {
        concluirResultado(index, resultado, selecionadosLista[index]);
    });
    if (mlFavoritosPoolRenderTimer) clearTimeout(mlFavoritosPoolRenderTimer);
    mlFavoritosPoolRenderTimer = null;
    limparThrottleEstadoFilaFavoritosPool();
    renderizarFavoritosPesquisaResultados(grupos.filter(Boolean));
    return resultadoFila;
}
