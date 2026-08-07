(function (global) {
  'use strict';

  const browser = global.FavoritosV2.browser;

  function fonteMercadoLivreMesclagem(item) {
    return /mercado_livre|api_item|api|ml_dom|card_visivel/i.test(String(
      item && (item.origem_dados || item.source || item.tituloFonte || item.titulo_fonte || item.precoFonte || item.fonte_preco || '')
    ));
  }

  function normalizarItemMesclagem(item, padraoFonte = '') {
    const fontePadrao = padraoFonte || (fonteMercadoLivreMesclagem(item) ? 'mercado_livre_dom' : '');
    const normalizado = prepararAnuncioMercadoLivreCanonico(item || {}, fontePadrao || 'coleta_mesmo_mlb');
    normalizado.chave_canonica = chaveCanonicaAnuncioFavoritos(normalizado);
    normalizado.chaveCanonica = normalizado.chave_canonica;
    return normalizado;
  }

  function copiarCampoMesclagemSeVazio(dest, src, campo, alias, fonteCampo, fonteValor) {
    const atual = String(dest[campo] || dest[alias] || '').trim();
    const novo = String(src[campo] || src[alias] || '').trim();
    const id = dest.id || src.id || '';
    if (!novo || (campo === 'titulo' && !tituloValidoFavoritosCanonico(novo, id))) return;
    if (atual && (campo !== 'titulo' || tituloValidoFavoritosCanonico(atual, id))) return;
    dest[campo] = novo;
    if (alias) dest[alias] = novo;
    if (!fonteCampo) return;
    const aliasFonte = fonteCampo.replace(/[A-Z]/g, m => `_${m.toLowerCase()}`);
    dest[fonteCampo] = src[fonteCampo] || src[aliasFonte] || fonteValor;
    dest[aliasFonte] = dest[fonteCampo];
  }

  function copiarPrecoMesclagemSeguro(dest, src, permitirSobrescrever) {
    if (!precoValidoFavoritosCanonico(src)) return;
    const fonteAtual = typeof fontePrecoFavoritos === 'function' ? fontePrecoFavoritos(dest) : (dest.precoFonte || dest.fonte_preco || '');
    const fonteNova = src.precoFonte || src.preco_fonte || src.fonte_preco || src.source || src.origem_dados || 'mercado_livre_dom';
    const prioridadeAtual = typeof prioridadeFontePrecoFavoritos === 'function' ? prioridadeFontePrecoFavoritos(fonteAtual) : (fonteAtual ? 10 : 0);
    const prioridadeNova = typeof prioridadeFontePrecoFavoritos === 'function' ? prioridadeFontePrecoFavoritos(fonteNova) : 20;
    if (precoValidoFavoritosCanonico(dest) && !(permitirSobrescrever && prioridadeNova >= prioridadeAtual)) return;
    [
      'preco', 'price', 'preco_original', 'original_price', 'standard_price', 'preco_promocional',
      'promotional_price', 'promotion_price', 'sale_price', 'discount_pct', 'moeda', 'currency_id'
    ].forEach(campo => {
      if (src[campo] !== null && src[campo] !== undefined && src[campo] !== '') dest[campo] = src[campo];
    });
    dest.precoFonte = fonteNova;
    dest.preco_fonte = fonteNova;
    dest.fonte_preco = fonteNova;
  }

  function aplicarIdentidadeMesclagem(combinado, atual, item, naoVinculados) {
    const idAtual = normalizarMlbFavoritosCanonico(atual.id || atual.mlb || atual.url || atual.link);
    const idItem = normalizarMlbFavoritosCanonico(item.id || item.mlb || item.url || item.link);
    const linkAtual = limparLinkProdutoMercadoLivreFavoritos(atual.url || atual.permalink || atual.link, idAtual);
    const linkItem = limparLinkProdutoMercadoLivreFavoritos(item.url || item.permalink || item.link, idItem);
    const mesmoId = !!(idAtual && idItem && idAtual === idItem);
    const mesmoLink = !!(linkAtual && linkItem && linkAtual.toLowerCase() === linkItem.toLowerCase());
    if (!mesmoId && !mesmoLink) {
      naoVinculados.push({ ...item, motivo: 'sem_match_mlb_link' });
      return false;
    }
    if (idAtual || idItem) {
      combinado.id = idAtual || idItem;
      combinado.mlb = combinado.id;
    }
    if (linkAtual || linkItem) {
      const link = linkAtual || linkItem;
      combinado.url = link;
      combinado.permalink = link;
      combinado.link = link;
      combinado.link_normalizado = link;
      combinado.linkFonte = combinado.linkFonte || atual.linkFonte || item.linkFonte || 'mercado_livre_dom';
      combinado.link_fonte = combinado.linkFonte;
    }
    return true;
  }

  function aplicarImagemPrecoTituloMesclagem(combinado, item) {
    copiarCampoMesclagemSeVazio(
      combinado, item, 'titulo', 'title', 'tituloFonte', item.tituloFonte || item.titulo_fonte || 'coleta_mesmo_mlb'
    );
    if (!imagemValidaFavoritosCanonico(combinado) && imagemValidaFavoritosCanonico(item)) {
      const imagem = typeof obterImagemAnuncioFavoritos === 'function'
        ? obterImagemAnuncioFavoritos(item)
        : String(item.imagem || item.thumbnail || item.foto || '').trim();
      combinado.imagem = imagem;
      combinado.thumbnail = imagem;
      combinado.foto = combinado.foto || imagem;
      combinado.fotoFonte = item.fotoFonte || item.foto_fonte || 'coleta_mesmo_mlb';
      combinado.foto_fonte = combinado.fotoFonte;
    }
    copiarPrecoMesclagemSeguro(combinado, item, fonteMercadoLivreMesclagem(item));
    const tituloAtual = String(combinado.titulo || combinado.title || '').trim();
    const tituloItem = String(item.titulo || item.title || '').trim();
    if (tituloValidoFavoritosCanonico(tituloAtual, combinado.id)
      && tituloValidoFavoritosCanonico(tituloItem, combinado.id)
      && similaridadeTitulosFavoritosCanonico(tituloAtual, tituloItem) < 0.22) {
      combinado.suspeito = true;
      combinado.motivo_suspeito = 'titulo_divergente_mesmo_mlb_link';
    }
  }

  function aplicarVendedorEVendasMesclagem(combinado, item) {
    const vendedorOrigem = normalizarNomeVendedor(item && item.vendedor || '');
    const vendedorAtual = normalizarNomeVendedor(combinado && combinado.vendedor || '');
    const fonteOrigemVendedor = item && (item.vendedorFonte || item.vendedor_fonte || item.fonte_vendedor || '');
    const fonteAtualVendedor = combinado && (combinado.vendedorFonte || combinado.vendedor_fonte || combinado.fonte_vendedor || '');
    if (window.FavoritosV2.promotionEffectuation.publicApi.merge.deveAtualizarVendedor(vendedorAtual, fonteAtualVendedor, vendedorOrigem, fonteOrigemVendedor)) {
      combinado.vendedor = vendedorOrigem;
      combinado.vendedorFonte = fonteOrigemVendedor || 'avantpro';
      combinado.vendedor_fonte = combinado.vendedorFonte;
    }
    const fonteItemVendas = item && (item.vendasFonte || item.vendas_fonte || item.fonte_vendas || '');
    const fonteAtualVendas = combinado && (combinado.vendasFonte || combinado.vendas_fonte || combinado.fonte_vendas || '');
    const vendasItem = parseNumeroVendas(item && item.vendas);
    const vendasAtual = parseNumeroVendas(combinado && combinado.vendas);
    if (window.FavoritosV2.promotionEffectuation.publicApi.merge.deveAtualizarVendas(vendasAtual, fonteAtualVendas, vendasItem, fonteItemVendas)) {
      combinado.vendas = vendasItem;
      combinado.vendasFonte = normalizarFonte(fonteItemVendas || 'avantpro');
      combinado.vendas_fonte = combinado.vendasFonte;
    }
  }

  function aplicarCamposExtrasMesclagem(combinado, item) {
    if (!combinado.data_criacao && item.data_criacao) {
      combinado.data_criacao = item.data_criacao;
      combinado.dataCriacaoFonte = item.dataCriacaoFonte || item.data_criacao_fonte || item.source || 'avantpro';
      combinado.data_criacao_fonte = combinado.dataCriacaoFonte;
    }
    ['media_mensal', 'vendas_estimadas', 'visitas', 'participacao', 'taxa_categoria', 'comissao', 'reputacao_vendedor'].forEach(campo => {
      if ((combinado[campo] === null || combinado[campo] === undefined || combinado[campo] === '')
        && item[campo] !== null && item[campo] !== undefined && item[campo] !== '') combinado[campo] = item[campo];
    });
    combinado.chave_canonica = chaveCanonicaAnuncioFavoritos(combinado);
    combinado.chaveCanonica = combinado.chave_canonica;
    combinado.estado_qualidade = classificarQualidadeAnuncioFavoritosCanonico(combinado);
  }

  function aplicarDadosComplementaresMesclagem(atual, item, naoVinculados) {
    const combinado = { ...atual };
    if (!aplicarIdentidadeMesclagem(combinado, atual, item, naoVinculados)) return combinado;
    aplicarImagemPrecoTituloMesclagem(combinado, item);
    aplicarVendedorEVendasMesclagem(combinado, item);
    aplicarCamposExtrasMesclagem(combinado, item);
    return combinado;
  }

  function anexarNaoVinculadosMesclagem(saida, naoVinculados) {
    try {
      Object.defineProperty(saida, '__avantNaoVinculado', { value: naoVinculados, enumerable: false });
    } catch (_err) {
      saida.__avantNaoVinculado = naoVinculados;
    }
    return saida;
  }

  function mesclarAnunciosAvant(destino, origem) {
    const mapa = new Map();
    const naoVinculados = []
      .concat(Array.isArray(destino && destino.__avantNaoVinculado) ? destino.__avantNaoVinculado : [])
      .concat(Array.isArray(origem && origem.__avantNaoVinculado) ? origem.__avantNaoVinculado : []);
    (destino || []).forEach(item => {
      const normalizado = normalizarItemMesclagem(item, fonteMercadoLivreMesclagem(item) ? 'mercado_livre_dom' : '');
      const chave = chaveCanonicaAnuncioFavoritos(normalizado);
      if (chave) mapa.set(chave, normalizado);
    });
    (origem || []).forEach(item => {
      const normalizado = normalizarItemMesclagem(item, fonteMercadoLivreMesclagem(item) ? 'mercado_livre_dom' : '');
      const chave = chaveCanonicaAnuncioFavoritos(normalizado);
      if (!chave) {
        naoVinculados.push({ ...(item || {}), motivo: 'sem_chave_canonica' });
        return;
      }
      const atual = mapa.get(chave);
      if (!atual) {
        if (fonteMercadoLivreMesclagem(normalizado)) mapa.set(chave, normalizado);
        else naoVinculados.push({ ...normalizado, motivo: 'sem_base_mercado_livre' });
        return;
      }
      mapa.set(chave, aplicarDadosComplementaresMesclagem(atual, normalizado, naoVinculados));
    });
    const saida = Array.from(mapa.values()).map(item => {
      item.estado_qualidade = classificarQualidadeAnuncioFavoritosCanonico(item);
      return item;
    });
    return anexarNaoVinculadosMesclagem(saida, naoVinculados);
  }

  async function coletarDadosAvantComRolagem(opcoes = {}) {
    if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') return [];
    if (typeof coletarPrimeiraPaginaFavoritosControlada !== 'function') return [];
    const resultado = await coletarPrimeiraPaginaFavoritosControlada({
      maxAnuncios: Math.max(20, Math.min(Number(opcoes.maxAnuncios) || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80, 100)),
      tempoLimiteMs: Math.max(15000, Math.min(Number(opcoes.tempoLimiteMs) || 90000, 180000)),
      maxPassadas: Math.max(1, Math.min(Number(opcoes.maxPassadas) || 2, 3)),
      loteCliques: opcoes.clicarAvant === false ? 0 : Math.max(0, Math.min(Number(opcoes.loteCliques) || 6, 8)),
      onProgress: opcoes.onProgress
    }).catch(() => null);
    return Array.isArray(resultado && resultado.anuncios) ? resultado.anuncios : [];
  }

  async function extrairDadosAvantDoWebviewVisivel(anuncio) {
    if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') return null;
    const resultado = typeof extrairAnunciosAvantProDomWebview === 'function'
      ? await extrairAnunciosAvantProDomWebview({ limite: Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80 })
      : null;
    return encontrarAnuncioAvantCorrespondente(anuncio, (resultado && resultado.anuncios) || []);
  }

  function normalizarTextoMl(value) {
    return String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
      .replace(/[^a-z0-9]+/g, ' ').replace(/\s+/g, ' ').trim();
  }

  function normalizarSkuBuscaMl(value) {
    return String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
      .replace(/[^a-zA-Z0-9]+/g, '').toUpperCase().trim();
  }

  function normalizarNomeVendedor(value) {
    return String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
      .replace(/^(vendido\s+por|loja\s+oficial|oficial\s+loja)\s*/i, '')
      .replace(/&quot;|\\"/g, '"').replace(/\s+/g, ' ').trim();
  }

  const api = {
    mesclarAnunciosAvant,
    coletarDadosAvantComRolagem,
    extrairDadosAvantDoWebviewVisivel,
    normalizarTextoMl,
    normalizarSkuBuscaMl,
    normalizarNomeVendedor
  };
  browser.merge = Object.freeze(api);
  Object.assign(global, api);
})(window);
