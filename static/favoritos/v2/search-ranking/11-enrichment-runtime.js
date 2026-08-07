(function (global) {
  'use strict';

  const searchRanking = global.FavoritosV2.searchRanking;
  const internal = searchRanking.internal;
  if (internal.components.has('11-enrichment-runtime')) return;

  function chavesAnuncioFavoritos(...args) {
    const implementation = internal.chavesAnuncioFavoritos;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: chavesAnuncioFavoritos');
    return implementation(...args);
  }

  function normalizarUrlAnuncioFavoritosRanking(...args) {
    const implementation = internal.normalizarUrlAnuncioFavoritosRanking;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: normalizarUrlAnuncioFavoritosRanking');
    return implementation(...args);
  }

  function tituloAnuncioFavoritosPrecisaComplemento(...args) {
    const implementation = internal.tituloAnuncioFavoritosPrecisaComplemento;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: tituloAnuncioFavoritosPrecisaComplemento');
    return implementation(...args);
  }

  function aplicarMetadataBasicaAnuncioFavoritos(...args) {
    const implementation = internal.aplicarMetadataBasicaAnuncioFavoritos;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: aplicarMetadataBasicaAnuncioFavoritos');
    return implementation(...args);
  }

  function criarContextoEnriquecimentoFavoritosExecucao() {
      const criarLimitador = (limite) => {
          const estado = { limite: Math.max(1, Number(limite) || 1), ativos: 0, fila: [] };
          const liberar = () => {
              while (estado.ativos < estado.limite && estado.fila.length) {
                  const entrada = estado.fila.shift();
                  estado.ativos += 1;
                  Promise.resolve()
                      .then(entrada.tarefa)
                      .then(entrada.resolve, entrada.reject)
                      .finally(() => {
                          estado.ativos = Math.max(0, estado.ativos - 1);
                          liberar();
                      });
              }
          };
          return {
              estado,
              executar(tarefa) {
                  return new Promise((resolve, reject) => {
                      estado.fila.push({ tarefa, resolve, reject });
                      liberar();
                  });
              }
          };
      };
      return {
          cache: new Map(),
          backendInflight: new Map(),
          backendLimiter: criarLimitador(4),
          apiLimiter: criarLimitador(8),
          estatisticas: {
              cache_hits: 0,
              cache_misses: 0,
              backend_solicitados: 0,
              backend_resultados: 0,
              backend_retries: 0,
              backend_timeouts: 0,
              completos_na_coleta: 0
          }
      };
  }

  function anuncioFavoritosEnriquecimentoCompleto(item) {
      if (!item || !(item.id || item.url || item.permalink || item.link)) return false;
      const fonteVendas = normalizarFonte(item.vendasFonte || item.vendas_fonte || item.fonte_vendas || '');
      return !tituloAnuncioFavoritosPrecisaComplemento(item.titulo || item.title, item.id)
          && !!normalizarUrlAnuncioFavoritosRanking(item.url || item.permalink || item.link, item.id)
          && !!obterImagemAnuncioFavoritos(item)
          && !precisaComplementoPrecoFavoritos(item)
          && vendedorValido(item.vendedor)
          && !!String(item.data_criacao || item.date_created || '').trim()
          && fonteVendasConfiavel(fonteVendas)
          && hasNumeroVendas(item.vendas)
          && !!window.FavoritosV2.promotionEffectuation.publicApi.listings.obterTipoAnuncioFavoritos(item)
          && !window.FavoritosV2.promotionEffectuation.publicApi.listings.fullAnuncioDesconhecidoFavoritos(item)
          && !!obterCondicaoAnuncioFavoritos(item);
  }

  function aplicarInfoEnriquecimentoFavoritos(alvo, info) {
      if (!alvo || !info) return false;
      let alterou = false;
      if (aplicarMetadataBasicaAnuncioFavoritos(alvo, info)) alterou = true;
      if (preencherPrecoAnuncioFavoritos(alvo, info)) alterou = true;
      if (window.FavoritosV2.promotionEffectuation.publicApi.listings.preencherTipoAnuncioFavoritos(alvo, info)) alterou = true;
      if (preencherCondicaoAnuncioFavoritos(alvo, info)) alterou = true;
      const dataCriacao = String(info.data_criacao || info.date_created || '').trim();
      if (dataCriacao && alvo.data_criacao !== dataCriacao) {
          alvo.data_criacao = dataCriacao;
          alterou = true;
      }
      const fonteData = info.fonte_data_criacao || info.dataCriacaoFonte || info.data_criacao_fonte || '';
      if (fonteData && !alvo.dataCriacaoFonte) {
          alvo.dataCriacaoFonte = fonteData;
          alvo.data_criacao_fonte = fonteData;
      }
      const vendedor = String(info.vendedor || '').trim();
      const fonteVendedor = normalizarFonte(info.fonte_vendedor || info.vendedorFonte || info.vendedor_fonte || 'pagina_produto');
      if (window.FavoritosV2.promotionEffectuation.publicApi.merge.deveAtualizarVendedor(alvo.vendedor, alvo.vendedorFonte, vendedor, fonteVendedor)) {
          alvo.vendedor = vendedor;
          alvo.vendedorFonte = fonteVendedor;
          alvo.vendedor_fonte = fonteVendedor;
          alterou = true;
      }
      const vendas = parseNumeroVendas(info.vendas);
      const fonteVendas = normalizarFonte(info.fonte_vendas || info.vendasFonte || info.vendas_fonte || '');
      if (window.FavoritosV2.promotionEffectuation.publicApi.merge.deveAtualizarVendas(alvo.vendas, alvo.vendasFonte, vendas, fonteVendas)) {
          alvo.vendas = vendas;
          alvo.vendasFonte = fonteVendas;
          alvo.vendas_fonte = fonteVendas;
          alterou = true;
      }
      const mediaNova = parseNumeroDecimalFavoritos(info.media_mensal ?? info.ritmo_atual ?? info.ritmo_vendas_mes ?? '');
      const mediaAtual = parseNumeroDecimalFavoritos(alvo.media_mensal ?? alvo.ritmo_atual ?? alvo.ritmo_vendas_mes ?? '');
      const fonteMediaNova = normalizarFonte(info.media_mensal_fonte || info.ritmo_atual_fonte || fonteVendas || '');
      const fonteMediaAtual = normalizarFonte(alvo.media_mensal_fonte || alvo.ritmo_atual_fonte || '');
      if (Number.isFinite(mediaNova) && (!Number.isFinite(mediaAtual) || (!fonteVendasConfiavel(fonteMediaAtual) && fonteVendasConfiavel(fonteMediaNova)))) {
          alvo.media_mensal = mediaNova;
          alvo.ritmo_atual = mediaNova;
          alvo.ritmo_vendas_mes = mediaNova;
          alvo.media_mensal_fonte = fonteMediaNova;
          alvo.ritmo_atual_fonte = fonteMediaNova;
          alterou = true;
      }
      if ((alvo.visitas === null || alvo.visitas === undefined || alvo.visitas === '') && info.visitas !== null && info.visitas !== undefined && info.visitas !== '') {
          alvo.visitas = info.visitas;
          alterou = true;
      }
      return alterou;
  }

  function aplicarCacheEnriquecimentoFavoritos(contexto, item, opcoes = {}) {
      if (!contexto || !(contexto.cache instanceof Map) || !item) return false;
      const registro = chavesAnuncioFavoritos(item)
          .map(chave => contexto.cache.get(chave))
          .find(Boolean);
      if (!registro) return false;
      aplicarInfoEnriquecimentoFavoritos(item, registro.metadata);
      const requisicaoEmAndamento = contexto.backendInflight instanceof Map
          && chavesAnuncioFavoritos(item).some(chave => contexto.backendInflight.has(chave));
      if (requisicaoEmAndamento) return false;
      const completo = !!registro.completo && anuncioFavoritosEnriquecimentoCompleto(item);
      const tentativasBackend = Math.max(0, Number(registro.tentativasBackend) || 0);
      const backendConcluido = registro.backendConcluido === true;
      const fechamento = opcoes && opcoes.fechamento === true;
      const deveRepetirNoFechamento = fechamento && !backendConcluido && tentativasBackend < 2;
      const reutilizar = completo
          || backendConcluido
          || (tentativasBackend > 0 && !deveRepetirNoFechamento);
      if (reutilizar) contexto.estatisticas.cache_hits += 1;
      return reutilizar;
  }

  function registrarCacheEnriquecimentoFavoritos(contexto, item) {
      if (!contexto || !(contexto.cache instanceof Map) || !item) return null;
      const chavesIniciais = chavesAnuncioFavoritos(item);
      if (!chavesIniciais.length) return null;
      let registro = chavesIniciais.map(chave => contexto.cache.get(chave)).find(Boolean) || null;
      const metadata = { ...item };
      if (registro && registro.metadata) aplicarInfoEnriquecimentoFavoritos(metadata, registro.metadata);
      if (!registro) {
          registro = {
              metadata: {},
              completo: false,
              chaves: new Set(),
              tentativasBackend: 0,
              backendConcluido: false
          };
      }
      registro.metadata = metadata;
      registro.completo = anuncioFavoritosEnriquecimentoCompleto(metadata);
      [...chavesIniciais, ...chavesAnuncioFavoritos(metadata)].forEach(chave => {
          if (!chave) return;
          registro.chaves.add(chave);
          contexto.cache.set(chave, registro);
      });
      return registro;
  }

  Object.assign(internal, {
    criarContextoEnriquecimentoFavoritosExecucao,
    anuncioFavoritosEnriquecimentoCompleto,
    aplicarInfoEnriquecimentoFavoritos,
    aplicarCacheEnriquecimentoFavoritos,
    registrarCacheEnriquecimentoFavoritos
  });
  internal.components.add('11-enrichment-runtime');
})(window);
