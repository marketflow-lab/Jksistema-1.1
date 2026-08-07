(function (global) {
  'use strict';

  const searchRanking = global.FavoritosV2.searchRanking;
  const internal = searchRanking.internal;
  if (internal.components.has('13-ranking')) return;

  function chavesAnuncioFavoritos(...args) {
    const implementation = internal.chavesAnuncioFavoritos;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: chavesAnuncioFavoritos');
    return implementation(...args);
  }

  async function complementarTiposRankingFavoritos(sku, anuncios) {
      const chaveSku = skuChaveSku(sku);
      const pendentes = (Array.isArray(anuncios) ? anuncios : [])
          .filter(anuncio => anuncio && (!window.FavoritosV2.promotionEffectuation.publicApi.listings.obterTipoAnuncioFavoritos(anuncio) || window.FavoritosV2.promotionEffectuation.publicApi.listings.fullAnuncioDesconhecidoFavoritos(anuncio)) && !anuncio._tipoAnuncioVerificado && (anuncio.id || anuncio.url))
          .filter(anuncio => !(typeof anuncioRankingHistoricoEstaticoFavoritos === 'function' && anuncioRankingHistoricoEstaticoFavoritos(anuncio)));
      if (!chaveSku || !pendentes.length || mlFavoritosTiposRankingEmExecucao.has(chaveSku)) return;
      mlFavoritosTiposRankingEmExecucao.add(chaveSku);
      let alterou = false;
      try {
          let resultados = [];
          try {
              const response = await fetch('/api/favoritos/ml/enriquecer-datas', {
                  method: 'POST',
                  headers: headersJsonAutenticado(),
                  body: JSON.stringify({
                      max_anuncios: Math.min(200, pendentes.length),
                      anuncios: pendentes.map(item => ({ id: item.id || '', url: item.url || '' }))
                  })
              });
              if (response.ok) {
                  const data = await response.json();
                  resultados = Array.isArray(data.resultados) ? data.resultados : [];
              }
          } catch (err) {
              console.warn('Nao foi possivel completar tipo do ranking pelo backend:', err);
          }

          const mapa = new Map();
          pendentes.forEach(item => chavesAnuncioFavoritos(item).forEach(chave => mapa.set(chave, item)));
          resultados.forEach(info => {
              const alvo = chavesAnuncioFavoritos(info).map(chave => mapa.get(chave)).find(Boolean);
              if (alvo && window.FavoritosV2.promotionEffectuation.publicApi.listings.preencherTipoAnuncioFavoritos(alvo, info)) alterou = true;
          });

          const aindaPendentes = pendentes.filter(item => !window.FavoritosV2.promotionEffectuation.publicApi.listings.obterTipoAnuncioFavoritos(item) || window.FavoritosV2.promotionEffectuation.publicApi.listings.fullAnuncioDesconhecidoFavoritos(item));
          await executarComConcorrencia(aindaPendentes, Math.min(ML_API_WORKERS, 8), async (alvo) => {
              const itemId = alvo.id || extrairItemIdAnuncio(alvo.url);
              if (!itemId) return;
              try {
                  const apiInfo = await consultarItemApiMercadoLivre(itemId);
                  if (apiInfo && window.FavoritosV2.promotionEffectuation.publicApi.listings.preencherTipoAnuncioFavoritos(alvo, apiInfo)) alterou = true;
              } catch (err) {
                  console.warn('Nao foi possivel completar tipo do anuncio:', itemId, err);
              } finally {
                  alvo._tipoAnuncioVerificado = true;
              }
          });
      } finally {
          mlFavoritosTiposRankingEmExecucao.delete(chaveSku);
      }
      if (alterou) {
          const historico = lerHistoricoFavoritos();
          if (historico.length) salvarHistoricoFavoritos(historico);
          if (skuChaveSku(favMlSkuSelecionado) === chaveSku) {
              renderizarFavoritosOutrosAnuncios(favMlSkuSelecionado);
          }
          if (document.getElementById('aba-historico')?.classList.contains('active')) {
              renderizarHistoricoFavoritos();
          }
      }
  }

  function ordenarAnunciosFavoritosRanking(anuncios, sku = '') {
      return filtrarAnunciosIgnoradosRanking(anuncios, sku)
          .map((anuncio, index) => ({ anuncio, index, metrica: calcularMetricasMediaVendas(anuncio) }))
          .sort((a, b) => {
              const mediaA = Number.isFinite(a.metrica.media) ? a.metrica.media : -1;
              const mediaB = Number.isFinite(b.metrica.media) ? b.metrica.media : -1;
              if (Math.abs(mediaB - mediaA) > 0.0001) return mediaB - mediaA;
              const vendasB = Number.isFinite(b.metrica.vendas) ? b.metrica.vendas : -1;
              const vendasA = Number.isFinite(a.metrica.vendas) ? a.metrica.vendas : -1;
              if (vendasB !== vendasA) return vendasB - vendasA;
              const posicaoA = Number(a.anuncio.posicao) || 9999;
              const posicaoB = Number(b.anuncio.posicao) || 9999;
              if (posicaoA !== posicaoB) return posicaoA - posicaoB;
              return a.index - b.index;
          })
          .map(item => {
              const mediaCalculada = Number.isFinite(item.metrica.media) ? item.metrica.media : null;
              item.anuncio.media_vendas_mensal = mediaCalculada;
              item.anuncio.meses_desde_criacao = Number.isFinite(item.metrica.meses) ? item.metrica.meses : null;
              if (mediaCalculada !== null && Number.isFinite(item.metrica.vendas) && item.metrica.dataCriacao) {
                  item.anuncio.media_mensal = mediaCalculada;
                  item.anuncio.ritmo_atual = mediaCalculada;
                  item.anuncio.ritmo_vendas_mes = mediaCalculada;
                  item.anuncio.media_mensal_fonte = 'calculado_vendas_dias';
                  item.anuncio.ritmo_atual_fonte = 'calculado_vendas_dias';
              }
              return item.anuncio;
          });
  }

  function limitarAnunciosFavoritosRanking(anuncios) {
      return (Array.isArray(anuncios) ? anuncios : [])
          .filter(Boolean)
          .slice(0, ML_FAVORITOS_RANKING_ANUNCIOS_MAX);
  }

  function normalizarIdAnuncioFavoritosIa(anuncio) {
      return (extrairItemIdAnuncio(anuncio && (anuncio.id || anuncio.mlb || anuncio.url || anuncio.permalink || anuncio.link))
          || String(anuncio && (anuncio.id || anuncio.mlb || '') || '').trim().toUpperCase().replace(/-/g, ''));
  }

  function obterDescricaoAnuncioFavoritosIa(anuncio) {
      if (!anuncio || typeof anuncio !== 'object') return '';
      const candidatos = [
          anuncio.descricao,
          anuncio.descricao_ml,
          anuncio.description,
          anuncio.description_plain,
          anuncio.plain_text,
          anuncio.text,
          anuncio.subtitle
      ];
      for (const valor of candidatos) {
          if (typeof valor === 'string' && valor.trim()) {
              return valor.replace(/\s+/g, ' ').trim();
          }
      }
      const info = anuncio.description_info || anuncio.descriptionInfo || anuncio.descricao_info;
      if (info && typeof info === 'object') {
          for (const chave of ['plain_text', 'text', 'content', 'description']) {
              const valor = info[chave];
              if (typeof valor === 'string' && valor.trim()) {
                  return valor.replace(/\s+/g, ' ').trim();
              }
              if (valor && typeof valor === 'object') {
                  for (const subchave of ['plain_text', 'text', 'content']) {
                      const subvalor = valor[subchave];
                      if (typeof subvalor === 'string' && subvalor.trim()) {
                          return subvalor.replace(/\s+/g, ' ').trim();
                      }
                  }
              }
          }
      }
      return '';
  }

  function anuncioFavoritosIaPayload(anuncio, index = 0) {
      const id = normalizarIdAnuncioFavoritosIa(anuncio);
      const imagem = obterImagemAnuncioFavoritos(anuncio);
      return {
          rank: index + 1,
          id,
          mlb: id,
          url: String(anuncio && (anuncio.url || anuncio.permalink || anuncio.link) || '').trim(),
          titulo: String(anuncio && (anuncio.titulo || anuncio.title) || '').trim().slice(0, 320),
          descricao: obterDescricaoAnuncioFavoritosIa(anuncio).slice(0, 1800),
          vendedor: String(anuncio && anuncio.vendedor || '').trim().slice(0, 140),
          imagem,
          thumbnail: imagem,
          preco: anuncio && (anuncio.preco ?? anuncio.price ?? ''),
          vendas: anuncio && anuncio.vendas,
          data_criacao: anuncio && (anuncio.data_criacao || anuncio.date_created || ''),
          tipo_anuncio: window.FavoritosV2.promotionEffectuation.publicApi.listings.obterTipoAnuncioFavoritos(anuncio),
          condicao: obterCondicaoAnuncioFavoritos(anuncio),
          loja: String(anuncio && (anuncio.loja || anuncio.loja_sync || anuncio.loja_conta) || '').trim(),
          sku: String(anuncio && (anuncio.sku || anuncio.seller_sku || anuncio.sku_favorito) || '').trim()
      };
  }

  function normalizarAnuncioRemovidoIa(anuncio, motivo = '') {
      const base = { ...(anuncio || {}) };
      base.id = normalizarIdAnuncioFavoritosIa(base);
      base.imagem = obterImagemAnuncioFavoritos(base);
      base.thumbnail = base.imagem || base.thumbnail || '';
      base.motivo_ia = String(motivo || base.motivo_ia || base.motivo || 'Removido pela IA por nao parecer o mesmo produto.').trim();
      return base;
  }

  Object.assign(internal, {
    complementarTiposRankingFavoritos,
    ordenarAnunciosFavoritosRanking,
    limitarAnunciosFavoritosRanking,
    normalizarIdAnuncioFavoritosIa,
    obterDescricaoAnuncioFavoritosIa,
    anuncioFavoritosIaPayload,
    normalizarAnuncioRemovidoIa
  });
  internal.components.add('13-ranking');
})(window);
