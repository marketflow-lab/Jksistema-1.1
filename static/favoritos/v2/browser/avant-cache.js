(function () {
  'use strict';

  const raiz = window.FavoritosV2 = window.FavoritosV2 || {};
  const browser = raiz.browser = raiz.browser || {};
  const avantCache = browser.avantCache = browser.avantCache || {};

  const CACHE_KEY = 'jk_favoritos_avant_cache_v1';
  const CACHE_TTL_MS = 6 * 60 * 60 * 1000;
  const CACHE_MAX = 1000;

  function callGlobal(name, args, fallback) {
    const fn = window[name];
    if (typeof fn === 'function' && fn !== avantCache[name]) {
      return fn.apply(window, args || []);
    }
    return typeof fallback === 'function' ? fallback() : fallback;
  }

  function normalizarFonteValor(value) {
    try {
      if (typeof normalizarFonte === 'function') return normalizarFonte(value);
    } catch (_err) {}
    return callGlobal('normalizarFonte', [value], String(value || '').trim().toLowerCase());
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

  function normalizarNomeVendedorValor(value) {
    try {
      if (typeof normalizarNomeVendedor === 'function') return normalizarNomeVendedor(value);
    } catch (_err) {}
    return callGlobal('normalizarNomeVendedor', [value], String(value || '').replace(/\s+/g, ' ').trim());
  }

  function extrairItemId(valor) {
    let id = '';
    try {
      if (typeof extrairItemIdAnuncio === 'function') id = extrairItemIdAnuncio(valor);
    } catch (_err) {}
    if (!id) id = callGlobal('extrairItemIdAnuncio', [valor], '');
    if (id) return String(id).trim().toUpperCase();
    const match = String(valor || '').match(/\bMLB-?(\d{6,})\b/i);
    return match ? `MLB${match[1]}`.toUpperCase() : '';
  }

  function normalizarChaveCacheAvant(value) {
    return String(value || '')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLowerCase()
      .replace(/\s+/g, ' ')
      .trim();
  }

  function obterLojaContextoAvant(contexto = {}) {
    return String(
      contexto.loja
      || (typeof favMlLojaSelecionada !== 'undefined' ? favMlLojaSelecionada : window.favMlLojaSelecionada)
      || (typeof mlSkuLojaSelecionada !== 'undefined' ? mlSkuLojaSelecionada : window.mlSkuLojaSelecionada)
      || (typeof skuLojaSelecionada !== 'undefined' ? skuLojaSelecionada : window.skuLojaSelecionada)
      || callGlobal('favoritosLojaSelecionadaParaApi', [], '')
      || ''
    ).trim();
  }

  function obterSkuContextoAvant(contexto = {}) {
    return String(
      contexto.sku
      || contexto.termo
      || (typeof favMlSkuSelecionado !== 'undefined' ? favMlSkuSelecionado : window.favMlSkuSelecionado)
      || (typeof histMlSkuSelecionado !== 'undefined' ? histMlSkuSelecionado : window.histMlSkuSelecionado)
      || callGlobal('obterPrimeiroTermoPesquisaAvulsaMl', [], '')
      || ''
    ).trim();
  }

  function normalizarUrlCacheAvant(url) {
    return String(url || '').split('#')[0].replace(/[?&](?:_?jk_nocache|utm_[^=]+|tracking_id)=[^&]*/gi, '').trim();
  }

  function chaveCacheAvantAnuncio(anuncio, contexto = {}) {
    const loja = normalizarChaveCacheAvant(obterLojaContextoAvant(contexto));
    const sku = normalizarChaveCacheAvant(obterSkuContextoAvant(contexto));
    const id = String(anuncio && (anuncio.id || extrairItemId(anuncio.url)) || '').trim().toUpperCase();
    const url = normalizarUrlCacheAvant(anuncio && anuncio.url);
    const identificador = id || normalizarChaveCacheAvant(url);
    if (!identificador) return '';
    return `${loja || 'loja'}|${sku || 'sku'}|${identificador}`;
  }

  function carregarCacheAvantFavoritos() {
    try {
      const raw = localStorage.getItem(CACHE_KEY);
      const parsed = raw ? JSON.parse(raw) : null;
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (_err) {
      return {};
    }
  }

  function salvarCacheAvantFavoritos(cache) {
    try {
      const entries = Object.entries(cache || {})
        .filter(([, item]) => item && Number(item.updatedAt) > 0)
        .sort((a, b) => Number(b[1].updatedAt || 0) - Number(a[1].updatedAt || 0))
        .slice(0, CACHE_MAX);
      localStorage.setItem(CACHE_KEY, JSON.stringify(Object.fromEntries(entries)));
    } catch (_err) {}
  }

  function cacheAvantAindaValido(item) {
    const updatedAt = Number(item && item.updatedAt || 0);
    return Number.isFinite(updatedAt) && updatedAt > 0 && Date.now() - updatedAt <= CACHE_TTL_MS;
  }

  function dadosAvantCacheaveis(anuncio) {
    const vendas = parseNumeroVendasValor(anuncio && anuncio.vendas);
    const fonteVendas = normalizarFonteValor(anuncio && (anuncio.vendasFonte || anuncio.vendas_fonte || ''));
    const vendedor = normalizarNomeVendedorValor(anuncio && anuncio.vendedor || '');
    const dataCriacao = String(anuncio && anuncio.data_criacao || '').trim();
    return {
      id: String(anuncio && (anuncio.id || extrairItemId(anuncio.url)) || '').trim().toUpperCase(),
      url: normalizarUrlCacheAvant(anuncio && anuncio.url),
      vendas: fonteVendasConfiavelValor(fonteVendas) && Number.isFinite(vendas) ? vendas : null,
      vendasFonte: fonteVendasConfiavelValor(fonteVendas) && Number.isFinite(vendas) ? fonteVendas : '',
      vendedor,
      vendedorFonte: vendedor ? normalizarFonteValor(anuncio && (anuncio.vendedorFonte || anuncio.vendedor_fonte || 'avantpro_card')) : '',
      data_criacao: dataCriacao,
      faturamento: anuncio && (anuncio.faturamento || anuncio.faturamento_produto || ''),
      comissao: anuncio && (anuncio.comissao || ''),
      localizacao: anuncio && (anuncio.localizacao || anuncio.localizacao_vendedor || ''),
      updatedAt: Date.now()
    };
  }

  function aplicarCacheAvantAosAnuncios(anuncios, contexto = {}) {
    if (!Array.isArray(anuncios) || !anuncios.length) return anuncios || [];
    const cache = carregarCacheAvantFavoritos();
    return anuncios.map(anuncio => {
      if (!anuncio) return anuncio;
      const key = chaveCacheAvantAnuncio(anuncio, contexto);
      const cached = key ? cache[key] : null;
      if (!cacheAvantAindaValido(cached)) return anuncio;
      const proximo = { ...anuncio };
      const vendasAtual = parseNumeroVendasValor(proximo.vendas);
      const fonteAtual = normalizarFonteValor(proximo.vendasFonte || proximo.vendas_fonte || '');
      const cachedVendas = cached.vendas !== null && cached.vendas !== undefined ? Number(cached.vendas) : NaN;
      if (!fonteVendasConfiavelValor(fonteAtual) && Number.isFinite(cachedVendas)) {
        proximo.vendas = Number(cached.vendas);
        proximo.vendasFonte = cached.vendasFonte || 'avantpro_cache';
        proximo.vendas_fonte = proximo.vendasFonte;
      } else if (!Number.isFinite(vendasAtual) && Number.isFinite(cachedVendas)) {
        proximo.vendas = Number(cached.vendas);
        proximo.vendasFonte = cached.vendasFonte || 'avantpro_cache';
        proximo.vendas_fonte = proximo.vendasFonte;
      }
      if (!proximo.vendedor && cached.vendedor) {
        proximo.vendedor = cached.vendedor;
        proximo.vendedorFonte = cached.vendedorFonte || 'avantpro_cache';
        proximo.vendedor_fonte = proximo.vendedorFonte;
      }
      if (!proximo.data_criacao && cached.data_criacao) proximo.data_criacao = cached.data_criacao;
      if (!proximo.faturamento && cached.faturamento) proximo.faturamento = cached.faturamento;
      if (!proximo.comissao && cached.comissao) proximo.comissao = cached.comissao;
      if (!proximo.localizacao && cached.localizacao) proximo.localizacao = cached.localizacao;
      proximo.cacheAvant = true;
      return proximo;
    });
  }

  function salvarCacheAvantDosAnuncios(anuncios, contexto = {}) {
    if (!Array.isArray(anuncios) || !anuncios.length) return;
    const cache = carregarCacheAvantFavoritos();
    let alterou = false;
    anuncios.forEach(anuncio => {
      if (!anuncio) return;
      const dados = dadosAvantCacheaveis(anuncio);
      if (!dados.vendedor && !dados.data_criacao && !Number.isFinite(dados.vendas) && !dados.faturamento && !dados.comissao && !dados.localizacao) return;
      const key = chaveCacheAvantAnuncio(anuncio, contexto);
      if (!key) return;
      cache[key] = { ...(cache[key] || {}), ...dados };
      alterou = true;
    });
    if (alterou) salvarCacheAvantFavoritos(cache);
  }

  Object.assign(avantCache, {
    normalizarChaveCacheAvant,
    obterLojaContextoAvant,
    obterSkuContextoAvant,
    normalizarUrlCacheAvant,
    chaveCacheAvantAnuncio,
    carregarCacheAvantFavoritos,
    salvarCacheAvantFavoritos,
    cacheAvantAindaValido,
    dadosAvantCacheaveis,
    aplicarCacheAvantAosAnuncios,
    salvarCacheAvantDosAnuncios
  });
})();
