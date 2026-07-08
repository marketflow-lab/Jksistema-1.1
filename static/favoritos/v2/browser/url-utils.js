(function () {
  'use strict';

  const raiz = window.FavoritosV2 = window.FavoritosV2 || {};
  const browser = raiz.browser = raiz.browser || {};
  const urlUtils = browser.urlUtils = browser.urlUtils || {};

  const ML_FALLBACK_URL = 'https://www.mercadolivre.com.br/';
  const MERCADO_LIVRE_HOST_RE = /mercadolivre\.com\.br|mercadolibre\.com/i;
  const LISTA_MERCADO_LIVRE_HOST_RE = /lista\.mercadolivre\.com\.br/i;
  const TRACKING_PARAMS = [
    'loader',
    'noIndex',
    'tracking_id',
    'position',
    'polycard_client',
    'sid',
    'searchVariation',
    'backend_model',
    'backend_type',
    'client',
    'reco_item_pos',
    'reco_backend',
    'reco_backend_type',
    'reco_client',
    'reco_id',
    'c_id',
    'pdp_filters',
    'picker_url',
    'quantity',
    'variation',
    'matt_tool',
    'matt_word',
    'matt_source',
    'matt_campaign_id',
    'matt_ad_group_id',
    'matt_match_type',
    'matt_network',
    'matt_device',
    'matt_creative',
    'matt_keyword',
    'matt_ad_position',
    'matt_ad_type',
    'matt_merchant_id'
  ];

  function defaultUrl() {
    try {
      if (typeof ML_DEFAULT_URL !== 'undefined' && ML_DEFAULT_URL) return ML_DEFAULT_URL;
    } catch (_err) {}
    return ML_FALLBACK_URL;
  }

  function callGlobal(name, args, fallback) {
    const fn = window[name];
    if (typeof fn === 'function' && fn !== urlUtils[name]) {
      return fn.apply(window, args || []);
    }
    return typeof fallback === 'function' ? fallback() : fallback;
  }

  function normalizarTexto(value) {
    return String(value || '')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function normalizarUrl(url) {
    const valor = (url || '').trim();
    if (!valor) return defaultUrl();
    if (/^https?:\/\//i.test(valor)) return valor;
    return `https://${valor}`;
  }

  function normalizarUrlMercadoLivreParaComparacao(url) {
    const valor = String(url || '').trim();
    if (!valor) return '';
    try {
      const parsed = new URL(valor, defaultUrl());
      parsed.hash = '';
      if (MERCADO_LIVRE_HOST_RE.test(parsed.hostname)) {
        TRACKING_PARAMS.forEach(param => parsed.searchParams.delete(param));
        parsed.pathname = parsed.pathname.replace(/\/+$/, '').toLowerCase();
        parsed.hostname = parsed.hostname.toLowerCase();
      }
      return parsed.toString();
    } catch (_err) {
      return valor.replace(/#.*$/, '').replace(/[?&]loader=true\b/i, '').replace(/\/+$/, '').toLowerCase();
    }
  }

  function normalizarMlbFavoritosCanonico(valor) {
    const bruto = String(valor || '').trim();
    if (!bruto) return '';
    let extraido = '';
    try {
      if (typeof extrairItemIdAnuncio === 'function') extraido = extrairItemIdAnuncio(bruto);
    } catch (_err) {}
    if (!extraido) extraido = callGlobal('extrairItemIdAnuncio', [bruto], '');
    if (extraido) return String(extraido).replace('-', '').toUpperCase();
    let texto = bruto;
    try { texto = decodeURIComponent(texto); } catch (_err) {}
    const match = texto.match(/\bMLB-?(\d{6,})\b/i)
      || texto.match(/[?&](?:wid|item_id)=(MLB\d{6,})/i);
    if (!match) return /^MLB\d{6,}$/i.test(texto.replace('-', '')) ? texto.replace('-', '').toUpperCase() : '';
    const raw = String(match[1] || '').replace('-', '').toUpperCase();
    return raw.indexOf('MLB') === 0 ? raw : `MLB${raw}`;
  }

  function construirUrlProdutoMercadoLivreCanonico(itemId) {
    const id = normalizarMlbFavoritosCanonico(itemId);
    if (!id) return '';
    return `https://produto.mercadolivre.com.br/${id.replace('MLB', 'MLB-')}`;
  }

  function limparLinkProdutoMercadoLivreFavoritos(valor, itemId = '') {
    const texto = String(valor || '').trim();
    const id = normalizarMlbFavoritosCanonico(itemId || texto);
    if (!texto && id) return construirUrlProdutoMercadoLivreCanonico(id);
    if (!texto) return '';
    try {
      const parsed = new URL(texto, 'https://www.mercadolivre.com.br');
      if (!MERCADO_LIVRE_HOST_RE.test(parsed.hostname)) return '';
      parsed.hash = '';
      const idUrl = normalizarMlbFavoritosCanonico(parsed.href) || id;
      if (LISTA_MERCADO_LIVRE_HOST_RE.test(parsed.hostname) && idUrl) {
        return construirUrlProdutoMercadoLivreCanonico(idUrl);
      }
      TRACKING_PARAMS.forEach(param => parsed.searchParams.delete(param));
      parsed.pathname = parsed.pathname.replace(/\/+$/, '');
      return parsed.toString().replace(/[?&]$/, '');
    } catch (_err) {
      return id ? construirUrlProdutoMercadoLivreCanonico(id) : texto.split('#')[0].trim();
    }
  }

  function chaveCanonicaAnuncioFavoritos(anuncio) {
    if (!anuncio) return '';
    const id = normalizarMlbFavoritosCanonico(
      anuncio.mlb || anuncio.id || anuncio.item_id || anuncio.url || anuncio.permalink || anuncio.link
    );
    if (id) return `mlb:${id}`;
    const link = limparLinkProdutoMercadoLivreFavoritos(anuncio.url || anuncio.permalink || anuncio.link || '');
    return link ? `link:${link.toLowerCase()}` : '';
  }

  function tituloFracoFavoritosCanonico(value, itemId = '') {
    const texto = String(value || '').replace(/\s+/g, ' ').trim();
    if (!texto) return true;
    let normalizado = '';
    try {
      if (typeof normalizarTextoMl === 'function') normalizado = normalizarTextoMl(texto);
    } catch (_err) {}
    if (!normalizado) normalizado = typeof window.normalizarTextoMl === 'function' ? window.normalizarTextoMl(texto) : normalizarTexto(texto);
    if (/^jm$/i.test(normalizado)) return true;
    if (/^mlb\d+$/i.test(normalizado.replace(/-/g, ''))) return true;
    if (/^(novo|usado|resultados|patrocinado|mais vendido|loja oficial|frete gratis|catalogo|produto relacionado)$/i.test(normalizado)) return true;
    if (/^(r\$|frete|chegar|vendid[oa]s?|mercadolider|mercado lider|opcoes de compra|ver mais|comprar agora)/i.test(normalizado)) return true;
    return !!itemId && normalizado.length <= 3;
  }

  function tituloValidoFavoritosCanonico(value, itemId = '') {
    return !tituloFracoFavoritosCanonico(value, itemId);
  }

  function imagemValidaFavoritosCanonico(anuncio) {
    if (!anuncio) return false;
    let imagem = null;
    try {
      if (typeof obterImagemAnuncioFavoritos === 'function') imagem = obterImagemAnuncioFavoritos(anuncio);
    } catch (_err) {}
    if (!imagem) imagem = callGlobal('obterImagemAnuncioFavoritos', [anuncio], null);
    if (imagem) return true;
    const url = String(anuncio.imagem || anuncio.thumbnail || anuncio.secure_thumbnail || anuncio.foto || anuncio.picture || '').trim();
    return /^https?:\/\//i.test(url) || /^\/\//.test(url);
  }

  function precoValidoFavoritosCanonico(anuncio) {
    if (!anuncio) return false;
    let precos = null;
    try {
      if (typeof obterPrecosAnuncioFavoritos === 'function') precos = obterPrecosAnuncioFavoritos(anuncio);
    } catch (_err) {}
    if (!precos) precos = callGlobal('obterPrecosAnuncioFavoritos', [anuncio], null);
    if (precos) return !!(precos.preco !== null || precos.promocional !== null);
    return anuncio.preco !== null && anuncio.preco !== undefined && anuncio.preco !== '';
  }

  function normalizarFonteValor(value) {
    try {
      if (typeof normalizarFonte === 'function') return normalizarFonte(value);
    } catch (_err) {}
    return callGlobal('normalizarFonte', [value], String(value || '').toLowerCase());
  }

  function parseNumeroVendasValor(value) {
    try {
      if (typeof parseNumeroVendas === 'function') return parseNumeroVendas(value);
    } catch (_err) {}
    return callGlobal('parseNumeroVendas', [value], Number(value));
  }

  function fonteVendasConfiavelValor(value) {
    try {
      if (typeof fonteVendasConfiavel === 'function') return !!fonteVendasConfiavel(value);
    } catch (_err) {}
    return !!callGlobal('fonteVendasConfiavel', [value], false);
  }

  function hasNumeroVendasValor(value) {
    try {
      if (typeof hasNumeroVendas === 'function') return !!hasNumeroVendas(value);
    } catch (_err) {}
    if (typeof window.hasNumeroVendas === 'function') return !!window.hasNumeroVendas(value);
    return Number.isFinite(Number(value));
  }

  function anuncioTemDadosAvantFavoritosCanonico(anuncio) {
    if (!anuncio) return false;
    const fonte = normalizarFonteValor(anuncio.vendasFonte || anuncio.vendas_fonte || '');
    const vendas = parseNumeroVendasValor(anuncio.vendas);
    if (fonteVendasConfiavelValor(fonte) && hasNumeroVendasValor(vendas)) return true;
    return !!(anuncio.vendedor || anuncio.data_criacao || anuncio.media_mensal || anuncio.vendas_estimadas);
  }

  function similaridadeTitulosFavoritosCanonico(a, b) {
    let normalizar = normalizarTexto;
    try {
      if (typeof normalizarTextoMl === 'function') normalizar = normalizarTextoMl;
    } catch (_err) {}
    if (normalizar === normalizarTexto && typeof window.normalizarTextoMl === 'function') normalizar = window.normalizarTextoMl;
    const tokensA = normalizar(a).split(' ').filter(token => token.length >= 3);
    const tokensB = normalizar(b).split(' ').filter(token => token.length >= 3);
    if (!tokensA.length || !tokensB.length) return 1;
    const setB = new Set(tokensB);
    const inter = tokensA.filter(token => setB.has(token)).length;
    return inter / Math.max(1, Math.min(tokensA.length, tokensB.length));
  }

  function classificarQualidadeAnuncioFavoritosCanonico(anuncio) {
    if (!anuncio) return 'incompleto';
    if (anuncio.suspeito === true || anuncio.estado_qualidade === 'suspeito') return 'suspeito';
    const id = normalizarMlbFavoritosCanonico(anuncio.mlb || anuncio.id || anuncio.url || anuncio.link || anuncio.permalink);
    const temTitulo = tituloValidoFavoritosCanonico(anuncio.titulo || anuncio.title, id);
    const temLink = !!limparLinkProdutoMercadoLivreFavoritos(anuncio.url || anuncio.permalink || anuncio.link || '', id);
    const temFoto = imagemValidaFavoritosCanonico(anuncio);
    const temPreco = precoValidoFavoritosCanonico(anuncio);
    const temAvant = anuncioTemDadosAvantFavoritosCanonico(anuncio);
    return temTitulo && temLink && temFoto && temPreco && temAvant ? 'completo' : 'incompleto';
  }

  function prepararAnuncioMercadoLivreCanonico(anuncio, fontePadrao = 'mercado_livre_dom') {
    const item = { ...(anuncio || {}) };
    const id = normalizarMlbFavoritosCanonico(item.mlb || item.id || item.item_id || item.url || item.permalink || item.link);
    const link = limparLinkProdutoMercadoLivreFavoritos(item.url || item.permalink || item.link || '', id);
    if (id) {
      item.id = id;
      item.mlb = id;
    }
    if (link) {
      item.url = link;
      item.permalink = link;
      item.link = link;
      item.link_normalizado = link;
    }
    item.chave_canonica = chaveCanonicaAnuncioFavoritos(item);
    item.chaveCanonica = item.chave_canonica;
    const titulo = String(item.titulo || item.title || '').replace(/\s+/g, ' ').trim();
    if (tituloValidoFavoritosCanonico(titulo, id)) {
      item.titulo = titulo;
      item.title = titulo;
      item.tituloFonte = item.tituloFonte || item.titulo_fonte || fontePadrao;
      item.titulo_fonte = item.tituloFonte;
    } else {
      item.titulo = '';
      item.title = '';
    }
    let imagem = null;
    try {
      if (typeof obterImagemAnuncioFavoritos === 'function') imagem = obterImagemAnuncioFavoritos(item);
    } catch (_err) {}
    if (!imagem) imagem = callGlobal('obterImagemAnuncioFavoritos', [item], String(item.imagem || item.thumbnail || item.secure_thumbnail || item.foto || '').trim());
    if (imagem) {
      item.imagem = imagem;
      item.thumbnail = imagem;
      item.foto = item.foto || imagem;
      item.fotoFonte = item.fotoFonte || item.foto_fonte || fontePadrao;
      item.foto_fonte = item.fotoFonte;
    }
    if (link) {
      item.linkFonte = item.linkFonte || item.link_fonte || fontePadrao;
      item.link_fonte = item.linkFonte;
    }
    if (precoValidoFavoritosCanonico(item)) {
      item.moeda = item.moeda || item.currency_id || item.currency || 'BRL';
      item.precoFonte = item.precoFonte || item.preco_fonte || item.fonte_preco || fontePadrao;
      item.preco_fonte = item.precoFonte;
      item.fonte_preco = item.fonte_preco || item.precoFonte;
    }
    item.estado_qualidade = classificarQualidadeAnuncioFavoritosCanonico(item);
    return item;
  }

  function urlsMercadoLivreEquivalentes(urlAtual, urlAlvo) {
    const atual = normalizarUrlMercadoLivreParaComparacao(urlAtual);
    const alvo = normalizarUrlMercadoLivreParaComparacao(urlAlvo);
    return !!(atual && alvo && atual === alvo);
  }

  Object.assign(urlUtils, {
    normalizarUrl,
    normalizarUrlMercadoLivreParaComparacao,
    normalizarMlbFavoritosCanonico,
    construirUrlProdutoMercadoLivreCanonico,
    limparLinkProdutoMercadoLivreFavoritos,
    chaveCanonicaAnuncioFavoritos,
    tituloFracoFavoritosCanonico,
    tituloValidoFavoritosCanonico,
    imagemValidaFavoritosCanonico,
    precoValidoFavoritosCanonico,
    anuncioTemDadosAvantFavoritosCanonico,
    similaridadeTitulosFavoritosCanonico,
    classificarQualidadeAnuncioFavoritosCanonico,
    prepararAnuncioMercadoLivreCanonico,
    urlsMercadoLivreEquivalentes
  });
})();
