(function (global) {
  'use strict';

  const searchRanking = global.FavoritosV2.searchRanking;
  const internal = searchRanking.internal;
  if (internal.components.has('10-listing-normalization')) return;

  function obterDescricaoAnuncioFavoritosIa(...args) {
    const implementation = internal.obterDescricaoAnuncioFavoritosIa;
    if (typeof implementation !== 'function') throw new Error('Dependencia de search-ranking indisponivel: obterDescricaoAnuncioFavoritosIa');
    return implementation(...args);
  }

  function chaveAnuncioFavoritos(anuncio) {
      if (typeof chaveCanonicaAnuncioFavoritos === 'function') {
          return chaveCanonicaAnuncioFavoritos(anuncio);
      }
      const id = extrairItemIdAnuncio(anuncio && (anuncio.id || anuncio.url || anuncio.permalink || anuncio.link)) || String(anuncio && anuncio.id || '').trim().toUpperCase().replace(/-/g, '');
      if (id) return `mlb:${id}`;
      const url = String(anuncio && (anuncio.url || anuncio.permalink || anuncio.link) || '').split('#')[0].trim().toLowerCase();
      return url ? `link:${url}` : '';
  }

  function chavesAnuncioFavoritos(anuncio) {
      if (!anuncio) return [];
      const chaves = new Set();
      const id = extrairItemIdAnuncio(anuncio.id || anuncio.url || anuncio.permalink || anuncio.link) || String(anuncio.id || '').trim().toUpperCase().replace(/-/g, '');
      const chave = chaveAnuncioFavoritos(anuncio);
      if (id) chaves.add(`id:${id}`);
      [anuncio.url, anuncio.permalink, anuncio.link].forEach(urlValor => {
          const url = typeof limparLinkProdutoMercadoLivreFavoritos === 'function'
              ? limparLinkProdutoMercadoLivreFavoritos(urlValor, id).toLowerCase()
              : String(urlValor || '').split('#')[0].trim().toLowerCase();
          if (url) chaves.add(`url:${url}`);
      });
      if (chave) chaves.add(`chave:${chave}`);
      return Array.from(chaves);
  }

  function construirUrlAnuncioFavoritosRankingPorId(itemId) {
      if (typeof construirUrlAnuncioFavoritosPorItemId === 'function') {
          return construirUrlAnuncioFavoritosPorItemId(itemId);
      }
      if (typeof construirUrlProdutoMercadoLivreCanonico === 'function') {
          return construirUrlProdutoMercadoLivreCanonico(itemId);
      }
      const id = String(itemId || '').trim().toUpperCase().replace('-', '');
      const digitos = id.replace(/^MLB/i, '');
      if (!/^MLB\d+$/i.test(id) || digitos.length < 8) return '';
      return `https://produto.mercadolivre.com.br/${id.replace('MLB', 'MLB-')}`;
  }

  function normalizarUrlAnuncioFavoritosRanking(valor, itemId = '') {
      const texto = String(valor || '').trim();
      if (/^\/\//.test(texto)) return `https:${texto}`;
      if (/^https?:\/\//i.test(texto)) {
          return typeof limparLinkProdutoMercadoLivreFavoritos === 'function'
              ? (limparLinkProdutoMercadoLivreFavoritos(texto, itemId) || texto)
              : texto;
      }
      const id = extrairItemIdAnuncio(texto) || itemId;
      return construirUrlAnuncioFavoritosRankingPorId(id);
  }

  function tituloAnuncioFavoritosPrecisaComplemento(titulo, itemId = '') {
      const texto = String(titulo || '').replace(/\s+/g, ' ').trim();
      if (!texto) return true;
      if (tituloPareceFiltroOuCategoriaMl(texto)) return true;
      const normalizado = normalizarTextoMl(texto);
      if (/^jm$/i.test(normalizado)) return true;
      if (/^mlb\d+$/i.test(normalizado.replace(/-/g, ''))) return true;
      return !!itemId && normalizado.length <= 3;
  }

  function extrairTituloAnuncioFavoritosPorLink(link) {
      let texto = String(link || '');
      if (!texto) return '';
      try { texto = decodeURIComponent(texto); } catch (_err) {}
      const match = texto.match(/\/MLB-?\d+-([^?#]+?)(?:-_?JM|_JM|$)/i);
      if (!match || !match[1]) return '';
      const titulo = String(match[1] || '')
          .replace(/[-_]+/g, ' ')
          .replace(/\bJM\b/ig, ' ')
          .replace(/\s+/g, ' ')
          .trim();
      return tituloAnuncioFavoritosPrecisaComplemento(titulo) ? '' : titulo.slice(0, 240);
  }

  function anuncioFavoritosCandidatoRanking(anuncio) {
      if (!anuncio) return false;
      const id = extrairItemIdAnuncio(anuncio.id || anuncio.url || anuncio.permalink || anuncio.link)
          || String(anuncio.id || '').trim().toUpperCase().replace(/-/g, '');
      if (id) return true;
      const link = typeof limparLinkProdutoMercadoLivreFavoritos === 'function'
          ? limparLinkProdutoMercadoLivreFavoritos(anuncio.url || anuncio.permalink || anuncio.link)
          : String(anuncio.url || anuncio.permalink || anuncio.link || '').trim();
      return !!link;
  }

  function aplicarMetadataBasicaAnuncioFavoritos(alvo, fonte) {
      if (!alvo || !fonte) return false;
      let alterou = false;
      const id = extrairItemIdAnuncio(fonte.id || fonte.mlb || fonte.item_id || fonte.url || fonte.permalink || fonte.link)
          || String(fonte.id || fonte.mlb || fonte.item_id || '').trim().toUpperCase().replace(/-/g, '');
      if (id && !alvo.id) {
          alvo.id = id;
          alterou = true;
      }
      const idFinal = alvo.id || id;
      const url = normalizarUrlAnuncioFavoritosRanking(fonte.url || fonte.permalink || fonte.link, idFinal);
      const urlAtual = normalizarUrlAnuncioFavoritosRanking(alvo.url || alvo.permalink || alvo.link, idFinal);
      if (url && (!urlAtual || /-_JM$/i.test(urlAtual))) {
          alvo.url = url;
          alvo.permalink = url;
          alvo.link = url;
          alvo.link_normalizado = typeof limparLinkProdutoMercadoLivreFavoritos === 'function' ? limparLinkProdutoMercadoLivreFavoritos(url, idFinal) : url;
          alvo.linkFonte = fonte.linkFonte || fonte.link_fonte || fonte.source || fonte.origem_dados || 'mercado_livre_api';
          alvo.link_fonte = alvo.linkFonte;
          alterou = true;
      } else if (urlAtual) {
          if (!alvo.url) alvo.url = urlAtual;
          if (!alvo.permalink) alvo.permalink = urlAtual;
          if (!alvo.link) alvo.link = urlAtual;
      }
      const titulo = String(fonte.titulo || fonte.title || '').replace(/\s+/g, ' ').trim()
          || extrairTituloAnuncioFavoritosPorLink(fonte.url || fonte.permalink || fonte.link || url);
      if (titulo && !tituloAnuncioFavoritosPrecisaComplemento(titulo, idFinal) && tituloAnuncioFavoritosPrecisaComplemento(alvo.titulo || alvo.title, idFinal)) {
          alvo.titulo = titulo;
          alvo.title = titulo;
          alvo.tituloFonte = fonte.tituloFonte || fonte.titulo_fonte || fonte.source || fonte.origem_dados || 'mercado_livre_api';
          alvo.titulo_fonte = alvo.tituloFonte;
          alterou = true;
      }
      if (preencherImagemAnuncioFavoritos(alvo, fonte)) {
          alvo.fotoFonte = fonte.fotoFonte || fonte.foto_fonte || fonte.source || fonte.origem_dados || 'mercado_livre_api';
          alvo.foto_fonte = alvo.fotoFonte;
          alterou = true;
      }
      return alterou;
  }

  function normalizarAnuncioFavoritosPesquisa(anuncio, sku, pesquisa) {
      const id = extrairItemIdAnuncio(anuncio && (anuncio.id || anuncio.url || anuncio.permalink || anuncio.link))
          || String(anuncio && anuncio.id || '').trim().toUpperCase().replace(/-/g, '');
      const urlBase = normalizarUrlAnuncioFavoritosRanking(anuncio && (anuncio.url || anuncio.permalink || anuncio.link), id);
      const vendasFonteOriginal = normalizarFonte(anuncio && (anuncio.vendasFonte || anuncio.vendas_fonte || 'api_search'));
      const vendasFonte = fonteVendasConfiavel(vendasFonteOriginal) ? vendasFonteOriginal : '';
      const vendasAvant = fonteVendasConfiavel(vendasFonte) ? parseNumeroVendas(anuncio && anuncio.vendas) : null;
      const mediaMensalValor = parseNumeroDecimalFavoritos(anuncio && (
          anuncio.media_mensal ??
          anuncio.ritmo_atual ??
          anuncio.ritmo_vendas_mes ??
          anuncio.media_vendas_mensal ??
          ''
      ));
      const mediaMensalBruta = Number.isFinite(mediaMensalValor)
          ? mediaMensalValor
          : (anuncio && (
              anuncio.media_mensal ??
              anuncio.ritmo_atual ??
              anuncio.ritmo_vendas_mes ??
              anuncio.media_vendas_mensal ??
              ''
          ));
      const mediaMensalFonte = normalizarFonte(anuncio && (
          anuncio.media_mensal_fonte ||
          anuncio.ritmo_atual_fonte ||
          anuncio.ritmo_vendas_mes_fonte ||
          ''
      )) || (Number.isFinite(mediaMensalValor) && vendasFonte ? vendasFonte : '');
      const imagem = obterImagemAnuncioFavoritos(anuncio);
      const precos = obterPrecosAnuncioFavoritos(anuncio);
      const descricao = obterDescricaoAnuncioFavoritosIa(anuncio);
      let tituloBruto = String(anuncio && (anuncio.titulo || anuncio.title) || '').trim();
      if (tituloAnuncioFavoritosPrecisaComplemento(tituloBruto, id)) {
          tituloBruto = extrairTituloAnuncioFavoritosPorLink(anuncio && (anuncio.url || anuncio.permalink || anuncio.link) || urlBase);
      }
      const tituloSeguro = typeof tituloValidoFavoritosCanonico === 'function' && !tituloValidoFavoritosCanonico(tituloBruto, id)
          ? ''
          : tituloBruto;
      const chaveCanonica = typeof chaveCanonicaAnuncioFavoritos === 'function'
          ? chaveCanonicaAnuncioFavoritos(anuncio)
          : chaveAnuncioFavoritos(anuncio);
      const linkNormalizado = typeof limparLinkProdutoMercadoLivreFavoritos === 'function'
          ? limparLinkProdutoMercadoLivreFavoritos(urlBase, id)
          : urlBase;
      const fontePreco = fontePrecoFavoritos(anuncio);
      const vendedorFonte = normalizarFonte(anuncio && (anuncio.vendedorFonte || anuncio.vendedor_fonte || (anuncio.vendedor ? 'api_search' : '')));
      const estadoQualidade = typeof classificarQualidadeAnuncioFavoritosCanonico === 'function'
          ? classificarQualidadeAnuncioFavoritosCanonico(anuncio)
          : (anuncio && anuncio.estado_qualidade || '');
      return {
          ...anuncio,
          id,
          mlb: id,
          chave_canonica: chaveCanonica,
          chaveCanonica,
          sku_favorito: sku,
          titulo: tituloSeguro,
          title: tituloSeguro,
          tituloFonte: anuncio && (anuncio.tituloFonte || anuncio.titulo_fonte || ''),
          titulo_fonte: anuncio && (anuncio.tituloFonte || anuncio.titulo_fonte || ''),
          descricao,
          url: urlBase,
          permalink: urlBase,
          link: urlBase,
          link_normalizado: linkNormalizado,
          linkFonte: anuncio && (anuncio.linkFonte || anuncio.link_fonte || ''),
          link_fonte: anuncio && (anuncio.linkFonte || anuncio.link_fonte || ''),
          imagem,
          thumbnail: imagem || (anuncio && anuncio.thumbnail) || '',
          foto: imagem || (anuncio && anuncio.foto) || '',
          fotoFonte: anuncio && (anuncio.fotoFonte || anuncio.foto_fonte || ''),
          foto_fonte: anuncio && (anuncio.fotoFonte || anuncio.foto_fonte || ''),
          preco: precos.preco !== null ? precos.preco : (anuncio && (anuncio.preco ?? anuncio.price ?? '')),
          price: precos.promocional !== null ? precos.promocional : (precos.preco !== null ? precos.preco : (anuncio && (anuncio.price ?? anuncio.preco ?? ''))),
          preco_original: precos.promocional !== null ? precos.preco : (anuncio && (anuncio.preco_original ?? anuncio.original_price ?? '')),
          original_price: precos.promocional !== null ? precos.preco : (anuncio && (anuncio.original_price ?? anuncio.preco_original ?? '')),
          preco_promocional: precos.promocional !== null ? precos.promocional : (anuncio && (anuncio.preco_promocional ?? anuncio.promotional_price ?? '')),
          promotional_price: precos.promocional !== null ? precos.promocional : (anuncio && (anuncio.promotional_price ?? anuncio.preco_promocional ?? '')),
          moeda: anuncio && (anuncio.moeda || anuncio.currency_id || anuncio.currency || 'BRL'),
          currency_id: anuncio && (anuncio.currency_id || anuncio.moeda || anuncio.currency || 'BRL'),
          discount_pct: precos.desconto || '',
          fonte_preco: fontePreco,
          precoFonte: anuncio && (anuncio.precoFonte || anuncio.preco_fonte || anuncio.fonte_preco || fontePreco),
          preco_fonte: anuncio && (anuncio.precoFonte || anuncio.preco_fonte || anuncio.fonte_preco || fontePreco),
          parcelamento_sem_juros: window.FavoritosV2.promotionEffectuation.publicApi.listings.obterParcelamentoSemJurosFavoritos(anuncio),
          tipo_anuncio: window.FavoritosV2.promotionEffectuation.publicApi.listings.obterTipoAnuncioFavoritos(anuncio),
          listing_type_id: anuncio && (anuncio.listing_type_id || anuncio.listingTypeId || ''),
          listing_type_name: anuncio && (anuncio.listing_type_name || anuncio.tipo_anuncio || ''),
          shipping: anuncio && (anuncio.shipping || anuncio.shipping_info || anuncio.shippingInfo || null),
          logistic_type: anuncio && (anuncio.logistic_type || anuncio.logisticType || anuncio.shipping_logistic_type || ''),
          shipping_mode: anuncio && (anuncio.shipping_mode || anuncio.shippingMode || ''),
          is_full: window.FavoritosV2.promotionEffectuation.publicApi.listings.temIndicadorFullFavoritos(anuncio) ? window.FavoritosV2.promotionEffectuation.publicApi.listings.obterFullAnuncioFavoritos(anuncio) : '',
          condicao: obterCondicaoAnuncioFavoritos(anuncio),
          condition: obterCondicaoAnuncioFavoritos(anuncio),
          item_condition: obterCondicaoAnuncioFavoritos(anuncio),
          vendedor: String(anuncio && anuncio.vendedor || '').trim(),
          vendedorFonte,
          vendedor_fonte: vendedorFonte,
          vendas: vendasAvant,
          vendasFonte,
          vendas_fonte: vendasFonte,
          media_mensal: mediaMensalBruta,
          ritmo_atual: mediaMensalBruta,
          ritmo_vendas_mes: mediaMensalBruta,
          media_mensal_fonte: mediaMensalFonte,
          ritmo_atual_fonte: mediaMensalFonte,
          visitas: anuncio && (anuncio.visitas ?? anuncio.views ?? ''),
          data_criacao: anuncio && (anuncio.data_criacao || anuncio.date_created || ''),
          dataCriacaoFonte: anuncio && (anuncio.dataCriacaoFonte || anuncio.data_criacao_fonte || ''),
          data_criacao_fonte: anuncio && (anuncio.dataCriacaoFonte || anuncio.data_criacao_fonte || ''),
          estado_qualidade: estadoQualidade,
          suspeito: !!(anuncio && anuncio.suspeito),
          posicao: Number(anuncio && anuncio.posicao) || 9999,
          pesquisas_origem: [pesquisa.termo],
          campos_origem: [`Pesquisa ${pesquisa.campo}`]
      };
  }

  function deduplicarAnunciosFavoritos(anuncios, opcoes = {}) {
      const mapa = new Map();
      const preservarSemChave = !!(opcoes && opcoes.preservarSemChave);
      (Array.isArray(anuncios) ? anuncios : []).forEach((anuncio, index) => {
          if (!anuncio || typeof anuncio !== 'object') {
              if (preservarSemChave) mapa.set(`sem-chave:${index}`, anuncio);
              return;
          }
          const chave = chaveAnuncioFavoritos(anuncio);
          if (!chave && !preservarSemChave) return;
          const chaveMapa = chave || `sem-chave:${index}`;
          const atual = mapa.get(chaveMapa);
          if (!atual) {
              mapa.set(chaveMapa, {
                  ...anuncio,
                  pesquisas_origem: Array.isArray(anuncio.pesquisas_origem) ? [...anuncio.pesquisas_origem] : [],
                  campos_origem: Array.isArray(anuncio.campos_origem) ? [...anuncio.campos_origem] : []
              });
              return;
          }
          aplicarMetadataBasicaAnuncioFavoritos(atual, anuncio);
          if (tituloAnuncioFavoritosPrecisaComplemento(atual.titulo, atual.id) && anuncio.titulo && !tituloAnuncioFavoritosPrecisaComplemento(anuncio.titulo, anuncio.id)) atual.titulo = anuncio.titulo;
          if (!atual.url && anuncio.url) atual.url = anuncio.url;
          if (!atual.id && anuncio.id) atual.id = anuncio.id;
          const descricao = obterDescricaoAnuncioFavoritosIa(anuncio);
          if (!atual.descricao && descricao) atual.descricao = descricao;
          preencherImagemAnuncioFavoritos(atual, anuncio);
          preencherPrecoAnuncioFavoritos(atual, anuncio);
          window.FavoritosV2.promotionEffectuation.publicApi.listings.preencherTipoAnuncioFavoritos(atual, anuncio);
          preencherCondicaoAnuncioFavoritos(atual, anuncio);
          if (!atual.data_criacao && anuncio.data_criacao) atual.data_criacao = anuncio.data_criacao;
          if (window.FavoritosV2.promotionEffectuation.publicApi.merge.deveAtualizarVendedor(atual.vendedor, atual.vendedorFonte, anuncio.vendedor, anuncio.vendedorFonte)) {
              atual.vendedor = anuncio.vendedor;
              atual.vendedorFonte = anuncio.vendedorFonte;
          }
          if (window.FavoritosV2.promotionEffectuation.publicApi.merge.deveAtualizarVendas(atual.vendas, atual.vendasFonte, anuncio.vendas, anuncio.vendasFonte)) {
              atual.vendas = anuncio.vendas;
              atual.vendasFonte = anuncio.vendasFonte;
          }
          const mediaAtual = parseNumeroDecimalFavoritos(atual.media_mensal ?? atual.ritmo_atual ?? atual.ritmo_vendas_mes ?? '');
          const mediaNova = parseNumeroDecimalFavoritos(anuncio.media_mensal ?? anuncio.ritmo_atual ?? anuncio.ritmo_vendas_mes ?? '');
          const fonteMediaAtual = normalizarFonte(atual.media_mensal_fonte || atual.ritmo_atual_fonte || '');
          const fonteMediaNova = normalizarFonte(anuncio.media_mensal_fonte || anuncio.ritmo_atual_fonte || anuncio.vendasFonte || '');
          if (Number.isFinite(mediaNova) && (!Number.isFinite(mediaAtual) || (!fonteVendasConfiavel(fonteMediaAtual) && fonteVendasConfiavel(fonteMediaNova)))) {
              atual.media_mensal = mediaNova;
              atual.ritmo_atual = mediaNova;
              atual.ritmo_vendas_mes = mediaNova;
              atual.media_mensal_fonte = fonteMediaNova || fonteMediaAtual;
              atual.ritmo_atual_fonte = fonteMediaNova || fonteMediaAtual;
          }
          if ((atual.visitas === null || atual.visitas === undefined || atual.visitas === '') && anuncio.visitas !== null && anuncio.visitas !== undefined && anuncio.visitas !== '') {
              atual.visitas = anuncio.visitas;
          }
          atual.posicao = Math.min(Number(atual.posicao) || 9999, Number(anuncio.posicao) || 9999);
          (Array.isArray(anuncio.pesquisas_origem) ? anuncio.pesquisas_origem : []).forEach(termo => {
              if (termo && !atual.pesquisas_origem.includes(termo)) atual.pesquisas_origem.push(termo);
          });
          (Array.isArray(anuncio.campos_origem) ? anuncio.campos_origem : []).forEach(campo => {
              if (campo && !atual.campos_origem.includes(campo)) atual.campos_origem.push(campo);
          });
      });
      return Array.from(mapa.values()).map(anuncio => {
          if (!anuncio || typeof anuncio !== 'object') return anuncio;
          const idDeclarado = String(anuncio.id || anuncio.mlb || anuncio.item_id || '').trim().toUpperCase().replace(/-/g, '');
          const id = extrairItemIdAnuncio(anuncio.id || anuncio.mlb || anuncio.item_id || anuncio.url || anuncio.permalink || anuncio.link)
              || (/^MLB\d{6,}$/.test(idDeclarado) ? idDeclarado : '');
          if (id) {
              anuncio.id = id;
              anuncio.mlb = id;
              anuncio.chave_canonica = `mlb:${id}`;
              anuncio.chaveCanonica = `mlb:${id}`;
          }
          return anuncio;
      });
  }

  Object.assign(internal, {
    chaveAnuncioFavoritos,
    chavesAnuncioFavoritos,
    construirUrlAnuncioFavoritosRankingPorId,
    normalizarUrlAnuncioFavoritosRanking,
    tituloAnuncioFavoritosPrecisaComplemento,
    extrairTituloAnuncioFavoritosPorLink,
    anuncioFavoritosCandidatoRanking,
    aplicarMetadataBasicaAnuncioFavoritos,
    normalizarAnuncioFavoritosPesquisa,
    deduplicarAnunciosFavoritos
  });
  internal.components.add('10-listing-normalization');
})(window);
