(function (global) {
  'use strict';

  const searchRanking = global.FavoritosV2.searchRanking;
  const internal = searchRanking.internal;
  if (internal.components.has('12-enrichment-sources')) return;

  function dependency(name) {
    const implementation = internal[name];
    if (typeof implementation !== 'function') {
      throw new Error('Dependencia de search-ranking indisponivel: ' + name);
    }
    return implementation;
  }

  function mostrarBalaoFavoritosStatus(...args) {
    return dependency('mostrarBalaoFavoritosStatus')(...args);
  }

  function verificarCancelamentoFavoritos(...args) {
    return dependency('verificarCancelamentoFavoritos')(...args);
  }

  function sinalFavoritosAtual(...args) {
    return dependency('sinalFavoritosAtual')(...args);
  }

  function executarComTimeoutFavoritos(...args) {
    return dependency('executarComTimeoutFavoritos')(...args);
  }

  function chaveAnuncioFavoritos(...args) {
    return dependency('chaveAnuncioFavoritos')(...args);
  }

  function chavesAnuncioFavoritos(...args) {
    return dependency('chavesAnuncioFavoritos')(...args);
  }

  function tituloAnuncioFavoritosPrecisaComplemento(...args) {
    return dependency('tituloAnuncioFavoritosPrecisaComplemento')(...args);
  }

  function anuncioFavoritosEnriquecimentoCompleto(...args) {
    return dependency('anuncioFavoritosEnriquecimentoCompleto')(...args);
  }

  function aplicarInfoEnriquecimentoFavoritos(...args) {
    return dependency('aplicarInfoEnriquecimentoFavoritos')(...args);
  }

  function aplicarCacheEnriquecimentoFavoritos(...args) {
    return dependency('aplicarCacheEnriquecimentoFavoritos')(...args);
  }

  function registrarCacheEnriquecimentoFavoritos(...args) {
    return dependency('registrarCacheEnriquecimentoFavoritos')(...args);
  }

  function normalizarPreloadEnriquecimentoFavoritos(item) {
    if (!normalizarFonte(item && item.origem_dados || '').includes('mercadolivre_html_preload')) return;
    if (!window.FavoritosV2.promotionEffectuation.publicApi.listings.obterTipoAnuncioFavoritos(item)) {
      item.tipo_anuncio = 'Classico';
      item.listing_type_id = item.listing_type_id || 'gold_special';
      item.listing_type_name = item.listing_type_name || 'Classico';
    }
    if (!obterCondicaoAnuncioFavoritos(item)) {
      item.condicao = 'new';
      item.condition = 'new';
      item.item_condition = 'new';
    }
    if (window.FavoritosV2.promotionEffectuation.publicApi.listings.fullAnuncioDesconhecidoFavoritos(item)) {
      item.is_full = false;
      item.full = false;
      item._fullAnuncioVerificado = true;
    }
  }

  function payloadEnriquecimentoFavoritos(item) {
    return {
      id: item.id || '',
      url: item.url || '',
      imagem: obterImagemAnuncioFavoritos(item),
      thumbnail: item.thumbnail || '',
      vendedor: item.vendedor || '',
      fonte_vendedor: item.fonte_vendedor || item.vendedorFonte || item.vendedor_fonte || '',
      vendas: item.vendas,
      fonte_vendas: item.fonte_vendas || item.vendasFonte || item.vendas_fonte || '',
      data_criacao: item.data_criacao || item.date_created || '',
      fonte_data_criacao: item.fonte_data_criacao || item.fonte || '',
      data_criacao_confianca: item.data_criacao_confianca || '',
      sku: item.sku || item.sku_favorito || '',
      listing_type_id: item.listing_type_id || item.listingTypeId || '',
      listing_type_name: item.listing_type_name || '',
      tipo_anuncio: window.FavoritosV2.promotionEffectuation.publicApi.listings.obterTipoAnuncioFavoritos(item),
      parcelamento_sem_juros: window.FavoritosV2.promotionEffectuation.publicApi.listings.obterParcelamentoSemJurosFavoritos(item),
      shipping: item.shipping || null,
      logistic_type: item.logistic_type || item.logisticType || '',
      shipping_mode: item.shipping_mode || item.shippingMode || '',
      is_full: window.FavoritosV2.promotionEffectuation.publicApi.listings.fullAnuncioDesconhecidoFavoritos(item) ? null : window.FavoritosV2.promotionEffectuation.publicApi.listings.obterFullAnuncioFavoritos(item),
      condicao: obterCondicaoAnuncioFavoritos(item),
      condition: obterCondicaoAnuncioFavoritos(item),
      item_condition: obterCondicaoAnuncioFavoritos(item)
    };
  }

  function criarEstadoEnriquecimentoFavoritos(anuncios, contextoEnriquecimento, opcoes) {
    const pendentes = (anuncios || [])
      .filter(item => item && (item.url || item.id))
      .filter(item => !(typeof anuncioRankingHistoricoEstaticoFavoritos === 'function'
        && anuncioRankingHistoricoEstaticoFavoritos(item)));
    pendentes.forEach(normalizarPreloadEnriquecimentoFavoritos);
    const contexto = contextoEnriquecimento && contextoEnriquecimento.cache instanceof Map
      ? contextoEnriquecimento
      : null;
    const mapaAlvos = new Map();
    pendentes.forEach(item => {
      chavesAnuncioFavoritos(item).forEach(chave => {
        if (!mapaAlvos.has(chave)) mapaAlvos.set(chave, new Set());
        mapaAlvos.get(chave).add(item);
      });
    });
    return { anuncios, opcoes, pendentes, contexto, mapaAlvos, resultados: [] };
  }

  function prepararPendentesBackendFavoritos(estado) {
    const gruposBackend = new Map();
    estado.pendentes.forEach(item => {
      if (anuncioFavoritosEnriquecimentoCompleto(item)) {
        registrarCacheEnriquecimentoFavoritos(estado.contexto, item);
        if (estado.contexto) estado.contexto.estatisticas.completos_na_coleta += 1;
        return;
      }
      if (aplicarCacheEnriquecimentoFavoritos(estado.contexto, item, estado.opcoes)) return;
      const chave = chaveAnuncioFavoritos(item) || chavesAnuncioFavoritos(item)[0];
      if (!chave) return;
      registrarCacheEnriquecimentoFavoritos(estado.contexto, item);
      if (!gruposBackend.has(chave)) gruposBackend.set(chave, item);
    });
    estado.pendentesBackend = Array.from(gruposBackend.values());
    if (estado.contexto) {
      estado.contexto.estatisticas.cache_misses += estado.pendentesBackend.length;
      estado.contexto.estatisticas.backend_solicitados += estado.pendentesBackend.length;
    }
  }

  function criarFilaBackendFavoritos(estado) {
    if (estado.contexto && !(estado.contexto.backendInflight instanceof Map)) {
      estado.contexto.backendInflight = new Map();
    }
    const novosBackend = [];
    const aguardandoBackend = [];
    estado.pendentesBackend.forEach(item => {
      if (!estado.contexto) {
        novosBackend.push({ item, entrada: null });
        return;
      }
      const chaves = chavesAnuncioFavoritos(item);
      const existente = chaves.map(chave => estado.contexto.backendInflight.get(chave)).find(Boolean);
      if (existente) {
        aguardandoBackend.push(existente.promise);
        return;
      }
      let resolveEntrada;
      let rejectEntrada;
      const promise = new Promise((resolve, reject) => {
        resolveEntrada = resolve;
        rejectEntrada = reject;
      });
      const entrada = { promise, resolve: resolveEntrada, reject: rejectEntrada, chaves };
      chaves.forEach(chave => estado.contexto.backendInflight.set(chave, entrada));
      novosBackend.push({ item, entrada });
    });
    return { novosBackend, aguardandoBackend };
  }

  function finalizarEntradaBackendFavoritos(contexto, entrada, info, erro = null) {
    if (!entrada) return;
    if (erro && (mlFavoritosCancelado || (sinalFavoritosAtual() && sinalFavoritosAtual().aborted))) {
      entrada.reject(erro);
    } else {
      entrada.resolve(info);
    }
    entrada.chaves.forEach(chave => {
      if (contexto.backendInflight.get(chave) === entrada) contexto.backendInflight.delete(chave);
    });
  }

  async function executarLoteBackendFavoritos(estado, loteEntradas, numeroLote) {
    const lote = loteEntradas.map(entrada => entrada.item);
    lote.forEach(item => {
      if (!estado.contexto) return;
      const registro = chavesAnuncioFavoritos(item)
        .map(chave => estado.contexto.cache.get(chave))
        .find(Boolean);
      if (!registro) return;
      registro.tentativasBackend = Math.max(0, Number(registro.tentativasBackend) || 0) + 1;
      if (registro.tentativasBackend > 1) estado.contexto.estatisticas.backend_retries += 1;
    });
    try {
      if (typeof setTimeout === 'function') await new Promise(resolve => setTimeout(resolve, 25));
      const executarRequest = () => executarComTimeoutFavoritos(async signal => {
        const response = await fetch('/api/favoritos/ml/enriquecer-datas', {
          method: 'POST',
          headers: headersJsonAutenticado(),
          signal,
          body: JSON.stringify({
            max_anuncios: lote.length,
            anuncios: lote.map(payloadEnriquecimentoFavoritos)
          })
        });
        if (!response.ok) return [];
        const data = await response.json();
        return Array.isArray(data.resultados) ? data.resultados : [];
      }, 45000, sinalFavoritosAtual());
      const resultados = estado.contexto
        && estado.contexto.backendLimiter
        && typeof estado.contexto.backendLimiter.executar === 'function'
        ? await estado.contexto.backendLimiter.executar(executarRequest)
        : await executarRequest();
      estado.resultados.push(...resultados);
      loteEntradas.forEach(({ entrada }) => {
        if (!entrada) return;
        const info = resultados.find(resultado => (
          chavesAnuncioFavoritos(resultado).some(chave => entrada.chaves.includes(chave))
        )) || null;
        finalizarEntradaBackendFavoritos(estado.contexto, entrada, info);
      });
    } catch (err) {
      if (estado.contexto && err && err.favoritosTimeout) estado.contexto.estatisticas.backend_timeouts += 1;
      loteEntradas.forEach(({ entrada }) => {
        if (entrada) finalizarEntradaBackendFavoritos(estado.contexto, entrada, null, err);
      });
      if (mlFavoritosCancelado || (sinalFavoritosAtual() && sinalFavoritosAtual().aborted)) throw err;
      console.warn(`Nao foi possivel enriquecer o lote ${numeroLote} do ranking pelo backend:`, err);
    }
  }

  async function executarBackendEnriquecimentoFavoritos(estado) {
    prepararPendentesBackendFavoritos(estado);
    if (!estado.pendentesBackend.length) return;
    const fila = criarFilaBackendFavoritos(estado);
    for (let inicio = 0; inicio < fila.novosBackend.length; inicio += 200) {
      await executarLoteBackendFavoritos(
        estado,
        fila.novosBackend.slice(inicio, inicio + 200),
        Math.floor(inicio / 200) + 1
      );
    }
    if (fila.aguardandoBackend.length) {
      const compartilhados = await Promise.all(fila.aguardandoBackend);
      estado.resultados.push(...compartilhados.filter(Boolean));
    }
  }

  function aplicarResultadosBackendFavoritos(estado) {
    const mapa = new Map();
    estado.anuncios.forEach(item => {
      chavesAnuncioFavoritos(item).forEach(chave => {
        if (!mapa.has(chave)) mapa.set(chave, new Set());
        mapa.get(chave).add(item);
      });
    });
    estado.resultados.forEach(info => {
      const alvos = new Set();
      chavesAnuncioFavoritos(info).forEach(chave => {
        (mapa.get(chave) || estado.mapaAlvos.get(chave) || []).forEach(alvo => alvos.add(alvo));
        const registro = estado.contexto && estado.contexto.cache.get(chave);
        if (registro) registro.backendConcluido = true;
      });
      alvos.forEach(alvo => aplicarInfoEnriquecimentoFavoritos(alvo, info));
    });
    if (estado.contexto) estado.contexto.estatisticas.backend_resultados += estado.resultados.length;
  }

  function precisaDadosAvantFavoritos(item) {
    if (!item || !item.url) return false;
    const fonteVendas = normalizarFonte(item.vendasFonte || item.vendas_fonte || item.fonte_vendas || '');
    return !vendedorValido(item.vendedor)
      || !item.data_criacao
      || !(fonteVendasConfiavel(fonteVendas) && hasNumeroVendas(item.vendas));
  }

  async function complementarEnriquecimentoAvantFavoritos(estado) {
    const pendentesAvant = estado.pendentes
      .filter(precisaDadosAvantFavoritos)
      .slice(0, ML_FAVORITOS_HISTORICO_ANUNCIOS_MAX);
    if (mlFavoritosEmExecucao || !pendentesAvant.length || typeof tentarDataCriacaoPeloElectron !== 'function') {
      return;
    }
    try {
      mostrarBalaoFavoritosStatus(`Completando data, vendedor e vendas de ${pendentesAvant.length} anuncio(s) pelo navegador/Avant Pro...`, {
        manterNavegadorVisivel: true
      });
      await tentarDataCriacaoPeloElectron(pendentesAvant);
    } catch (err) {
      if (mlFavoritosCancelado || (err && err.name === 'AbortError') || (err && err.canceladoFavoritos)) throw err;
      console.warn('Nao foi possivel completar dados do ranking pelo navegador/Avant Pro:', err);
      mostrarBalaoFavoritosStatus(`Nao consegui completar todos os dados Avant de ${pendentesAvant.length} anuncio(s), mas vou salvar o ranking com os dados coletados.`, {
        tempoMs: 5500,
        larga: true
      });
    }
  }

  function precisaComplementoApiFavoritos(item) {
    return item && (
      !item.url
      || tituloAnuncioFavoritosPrecisaComplemento(item.titulo, item.id)
      || !vendedorValido(item.vendedor)
      || !obterImagemAnuncioFavoritos(item)
      || precisaComplementoPrecoFavoritos(item)
      || !window.FavoritosV2.promotionEffectuation.publicApi.listings.obterTipoAnuncioFavoritos(item)
      || window.FavoritosV2.promotionEffectuation.publicApi.listings.fullAnuncioDesconhecidoFavoritos(item)
      || !obterCondicaoAnuncioFavoritos(item)
    );
  }

  async function complementarEnriquecimentoApiFavoritos(estado) {
    const unicos = Array.from(new Map(estado.pendentes
      .filter(precisaComplementoApiFavoritos)
      .map(item => [chaveAnuncioFavoritos(item) || chavesAnuncioFavoritos(item)[0], item])
      .filter(([chave]) => Boolean(chave))).values());
    const deadline = Date.now() + (mlFavoritosEmExecucao ? 15000 : 45000);
    await executarComConcorrencia(unicos, Math.min(ML_API_WORKERS, 4), async alvo => {
      verificarCancelamentoFavoritos();
      const restanteMs = deadline - Date.now();
      if (restanteMs <= 0) return;
      const itemId = alvo.id || extrairItemIdAnuncio(alvo.url);
      if (!itemId) return;
      try {
        const consultar = () => executarComTimeoutFavoritos(
          () => consultarItemApiMercadoLivre(itemId),
          Math.min(6000, restanteMs),
          sinalFavoritosAtual()
        );
        const info = estado.contexto
          && estado.contexto.apiLimiter
          && typeof estado.contexto.apiLimiter.executar === 'function'
          ? await estado.contexto.apiLimiter.executar(consultar)
          : await consultar();
        if (!info) return;
        const alvos = new Set([alvo]);
        chavesAnuncioFavoritos(alvo).forEach(chave => {
          (estado.mapaAlvos.get(chave) || []).forEach(item => alvos.add(item));
        });
        alvos.forEach(item => aplicarInfoEnriquecimentoFavoritos(item, info));
      } catch (err) {
        if (mlFavoritosCancelado || (err && err.canceladoFavoritos)) throw err;
        if (err && err.favoritosTimeout) return;
        console.warn('Nao foi possivel preencher vendedor do ranking pelo MLB:', itemId, err);
      }
    });
  }

  async function enriquecerAnunciosFavoritosRanking(anuncios, contextoEnriquecimento = null, opcoes = {}) {
    const estado = criarEstadoEnriquecimentoFavoritos(anuncios, contextoEnriquecimento, opcoes);
    if (!estado.pendentes.length) return;
    await executarBackendEnriquecimentoFavoritos(estado);
    aplicarResultadosBackendFavoritos(estado);
    await complementarEnriquecimentoAvantFavoritos(estado);
    await complementarEnriquecimentoApiFavoritos(estado);
    estado.pendentes.forEach(item => registrarCacheEnriquecimentoFavoritos(estado.contexto, item));
  }

  Object.assign(internal, {
    enriquecerAnunciosFavoritosRanking
  });
  internal.components.add('12-enrichment-sources');
})(window);
