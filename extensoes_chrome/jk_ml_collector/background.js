(function () {
  'use strict';

  var VERSION = '0.7.0';
  var CACHE_TTL_MS = 10 * 60 * 1000;
  var cache = new Map();

  function clean(value) {
    return String(value == null ? '' : value)
      .replace(/\\u002F/g, '/')
      .replace(/\\\//g, '/')
      .replace(/\\n/g, ' ')
      .replace(/\\t/g, ' ')
      .replace(/\\r/g, ' ')
      .replace(/&quot;/g, '"')
      .replace(/&#34;/g, '"')
      .replace(/&#39;/g, "'")
      .replace(/\\"/g, '"')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function itemId(value) {
    var text = String(value || '').toUpperCase();
    var match = text.match(/[?&](?:WID|ITEM_ID|ITEMID|ID)=?(MLB\d{8,})/i);
    if (match) return String(match[1] || '').toUpperCase();
    match = text.match(/MLB-\d{8,}/i);
    if (match) return match[0].replace('MLB-', 'MLB').toUpperCase();
    match = text.match(/\bMLB\d{8,}\b/i);
    if (match) return String(match[0] || '').toUpperCase();
    return '';
  }

  function canonicalUrl(id, fallback) {
    var cleanId = itemId(id || fallback);
    if (fallback && /^https?:\/\//i.test(String(fallback))) return String(fallback);
    return cleanId ? 'https://produto.mercadolivre.com.br/' + cleanId.replace('MLB', 'MLB-') + '-_JM' : '';
  }

  function looksLikeCatalogUrl(value) {
    return /\/p\/MLB\d{8,}/i.test(String(value || ''));
  }

  function parseSales(value) {
    if (value == null || value === '') return null;
    if (typeof value === 'number' && isFinite(value)) return Math.round(value);
    var text = clean(value).toLowerCase();
    var match = text.match(/(?:\+?\s*)?(\d+(?:[\.,]\d+)?\s*(?:mil|k)?)\s+(?:vendid[oa]s?|ventas?|sales?)/i)
      || text.match(/(?:vendid[oa]s?|ventas?|sales?)\s*(?:\+?\s*)?(\d+(?:[\.,]\d+)?\s*(?:mil|k)?)/i)
      || text.match(/^\+?\s*(\d+(?:[\.,]\d+)?)\s*(mil|k)?\s*$/i);
    if (!match) return null;
    var raw = String(match[1] || '').replace(/\s+/g, '');
    var suffix = String(match[2] || (/mil|k/i.test(raw) ? raw.match(/mil|k/i)[0] : '')).toLowerCase();
    raw = raw.replace(/mil|k/ig, '');
    if (raw.indexOf('.') >= 0 && raw.indexOf(',') >= 0) {
      raw = raw.lastIndexOf('.') > raw.lastIndexOf(',') ? raw.replace(/,/g, '') : raw.replace(/\./g, '').replace(',', '.');
    } else if (raw.indexOf(',') >= 0) {
      raw = /^\d{1,3}(?:,\d{3})+$/.test(raw) ? raw.replace(/,/g, '') : raw.replace(',', '.');
    } else if (raw.indexOf('.') >= 0 && /^\d{1,3}(?:\.\d{3})+$/.test(raw)) {
      raw = raw.replace(/\./g, '');
    }
    var parsed = Number(raw);
    if (!isFinite(parsed)) return null;
    if (suffix === 'mil' || suffix === 'k') parsed *= 1000;
    return Math.round(parsed);
  }

  function salesOrZero(value) {
    var parsed = parseSales(value);
    return parsed === null ? 0 : parsed;
  }

  function pick(patterns, text) {
    var source = String(text || '');
    for (var i = 0; i < patterns.length; i += 1) {
      var match = source.match(patterns[i]);
      if (match && match[1] != null) return clean(match[1]);
    }
    return '';
  }

  function visibleDate(value) {
    var text = clean(value).replace(/^[^0-9]*(?=\d)/, '');
    var iso = text.match(/\b(?:19|20)\d{2}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?\b/);
    if (iso) return iso[0];
    var br = text.match(/\b\d{1,2}\/\d{1,2}\/\d{2,4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?\b/);
    if (br) return br[0];
    var longPt = text.match(/\b\d{1,2}\s+de\s+[a-zA-Z\u00c0-\u017f]+\s+de\s+(?:19|20)\d{2}\b/i);
    return longPt ? longPt[0] : '';
  }

  function pickDateFromText(value) {
    var text = clean(value);
    var data = pick([
      /["'](?:date_created|dateCreated|start_time|startTime|creation_date|creationDate|created_at|createdAt|createdDate|item_date_created|itemDateCreated|listing_start_time|listingStartTime|listing_created_at|listingCreatedAt|start_date|startDate|itemStartTime|publication_date|publicationDate|published_at|publishedAt|date_published|datePublished|datePosted|date_posted|first_publication_date|firstPublicationDate|available_since|availableSince|active_since|activeSince)["']\s*:\s*["']([^"']+)["']/i,
      /["'](?:date_created|dateCreated|start_time|startTime|creation_date|creationDate|created_at|createdAt|createdDate|item_date_created|itemDateCreated|listing_start_time|listingStartTime|listing_created_at|listingCreatedAt|start_date|startDate|itemStartTime|publication_date|publicationDate|published_at|publishedAt|date_published|datePublished|datePosted|date_posted|first_publication_date|firstPublicationDate|available_since|availableSince|active_since|activeSince)["']\s*:\s*\{[^{}]{0,320}["'](?:value|date|text|label|display_value|displayValue|raw)["']\s*:\s*["']([^"']+)["']/i,
      /(?:date_created|dateCreated|start_time|startTime|creation_date|creationDate|created_at|createdAt|createdDate|item_date_created|itemDateCreated|listing_start_time|listingStartTime|listing_created_at|listingCreatedAt|publication_date|publicationDate|published_at|publishedAt|datePublished|datePosted|firstPublicationDate|availableSince|activeSince)[^0-9]{0,160}((?:19|20)\d{2}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?|\d{1,2}\/\d{1,2}\/\d{2,4})/i
    ], text);
    if (data) return data;
    var normalized = text.normalize ? text.normalize('NFD').replace(/[\u0300-\u036f]/g, '') : text;
    normalized = normalized.toLowerCase().replace(/\s+/g, ' ');
    var labels = ['anuncio criado em', 'anuncio criado', 'anuncio ganhador criado em', 'criado em', 'publicado em', 'data de criacao', 'disponivel desde', 'ativo desde', 'publicacao'];
    for (var i = 0; i < labels.length; i += 1) {
      var idx = normalized.indexOf(labels[i]);
      if (idx < 0) continue;
      data = visibleDate(text.slice(idx, idx + 220));
      if (data) return data;
    }
    var days = normalized.match(/\b(?:ha|hace|faz)\s+([0-9.]+)\s+dias?\b/i)
      || normalized.match(/\b([0-9.]+)\s+dias?\s+(?:de|desde)\s+(?:publicacao|criacao|criado|anuncio)\b/i);
    if (days) {
      var count = Number(String(days[1] || '').replace(/\./g, ''));
      if (isFinite(count) && count >= 0 && count < 10000) {
        var date = new Date();
        date.setDate(date.getDate() - count);
        return date.toISOString();
      }
    }
    return '';
  }

  function normalizeSeller(value) {
    var seller = clean(value)
      .replace(/^(?:nome\s+do\s+vendedor|vendedor|loja\s+oficial|vendido\s+por)\s*[:\-]?\s*/i, '')
      .trim();
    if (!seller || seller.length > 140 || /^\d+$/.test(seller)) return '';
    return seller;
  }

  function parseHtmlInfo(html, id, url) {
    var text = clean(html);
    var info = {
      id: itemId(id || text),
      url: url || canonicalUrl(id),
      titulo: '',
      vendedor: '',
      seller_id: '',
      data_criacao: '',
      vendas: null,
      visitas: null,
      marca: '',
      localizacao_vendedor: '',
      reputacao_vendedor: '',
      fonte: 'extensao_background_html'
    };
    info.titulo = pick([
      /<h1[^>]*class=["'][^"']*ui-pdp-title[^"']*["'][^>]*>(.*?)<\/h1>/i,
      /<meta[^>]+property=["']og:title["'][^>]+content=["']([^"']+)["']/i,
      /<title[^>]*>(.*?)<\/title>/i,
      /["']title["']\s*:\s*["']([^"']+)["']/i
    ], html).replace(/\s*\|\s*MercadoLivre.*$/i, '');
    info.data_criacao = pickDateFromText(text);
    info.vendedor = normalizeSeller(pick([
      /["']seller_name["']\s*:\s*["']([^"']+)["']/i,
      /["']sellerName["']\s*:\s*["']([^"']+)["']/i,
      /["']official_store_name["']\s*:\s*["']([^"']+)["']/i,
      /["']officialStoreName["']\s*:\s*["']([^"']+)["']/i,
      /["']seller["']\s*:\s*\{[^{}]{0,500}["']nickname["']\s*:\s*["']([^"']+)["']/i,
      /["']seller["']\s*:\s*\{[^{}]{0,500}["']name["']\s*:\s*["']([^"']+)["']/i,
      /class=["'][^"']*ui-pdp-seller__header__title[^"']*["'][^>]*>(.*?)</i
    ], html));
    info.seller_id = pick([
      /["']seller_id["']\s*:\s*"?(\d+)"?/i,
      /["']sellerId["']\s*:\s*"?(\d+)"?/i,
      /["']seller["']\s*:\s*\{[^{}]{0,500}["']id["']\s*:\s*"?(\d+)"?/i
    ], html);
    var sold = pick([
      /["']sold_quantity["']\s*:\s*([0-9,.]+)/i,
      /["']soldQuantity["']\s*:\s*([0-9,.]+)/i,
      /["']sold["']\s*:\s*([0-9,.]+)/i
    ], html);
    info.vendas = salesOrZero(sold || text);
    info.marca = pick([
      /["']brand["']\s*:\s*["']([^"']+)["']/i,
      /["']BRAND["'][^{}]{0,400}["']value_name["']\s*:\s*["']([^"']+)["']/i,
      /["']name["']\s*:\s*["']Marca["'][^{}]{0,400}["']value_name["']\s*:\s*["']([^"']+)["']/i
    ], html);
    var city = pick([/["']city["']\s*:\s*\{[^{}]{0,200}["']name["']\s*:\s*["']([^"']+)["']/i], html);
    var state = pick([/["']state["']\s*:\s*\{[^{}]{0,200}["']name["']\s*:\s*["']([^"']+)["']/i], html);
    info.localizacao_vendedor = [city, state].filter(Boolean).join('/');
    info.reputacao_vendedor = pick([
      /["']power_seller_status["']\s*:\s*["']([^"']+)["']/i,
      /["']seller_reputation["'][^{}]{0,500}["']level_id["']\s*:\s*["']([^"']+)["']/i
    ], html);
    return info;
  }

  async function fetchJson(url) {
    var response = await fetch(url, {
      method: 'GET',
      credentials: 'include',
      headers: {
        Accept: 'application/json,text/plain,*/*',
        'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8'
      }
    });
    if (!response.ok) throw new Error('HTTP ' + response.status);
    return response.json();
  }

  async function fetchText(url) {
    var response = await fetch(url, {
      method: 'GET',
      credentials: 'include',
      headers: {
        Accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8'
      }
    });
    if (!response.ok) throw new Error('HTTP ' + response.status);
    return response.text();
  }

  function mergeInfo(base, extra, source) {
    if (!extra || typeof extra !== 'object') return base;
    Object.keys(extra).forEach(function (key) {
      var value = extra[key];
      if (value === null || value === undefined || value === '') return;
      if (key === 'vendas') {
        var parsedBase = parseSales(base[key]);
        var parsedValue = parseSales(value);
        if (parsedValue !== null && (parsedBase === null || parsedValue > parsedBase)) base[key] = parsedValue;
        return;
      }
      if (key === 'visitas') {
        if (base[key] === null || base[key] === undefined || base[key] === '') base[key] = value;
        return;
      }
      if (key === 'data_criacao') {
        var nextDate = clean(value);
        var currentDate = clean(base[key]);
        if (!currentDate || /html|pagina/i.test(String(source || ''))) {
          base[key] = nextDate;
        }
        return;
      }
      if (!base[key]) base[key] = value;
    });
    if (source) base.fonte = base.fonte && base.fonte !== source ? base.fonte + ' + ' + source : source;
    return base;
  }

  function infoFromApi(item, original) {
    if (!item || typeof item !== 'object') return null;
    var seller = item.seller && typeof item.seller === 'object' ? item.seller : {};
    var address = item.seller_address && typeof item.seller_address === 'object' ? item.seller_address : {};
    var city = address.city && typeof address.city === 'object' ? address.city.name : '';
    var state = address.state && typeof address.state === 'object' ? address.state.name : '';
    return {
      id: item.id || original.id,
      url: item.permalink || original.url,
      titulo: item.title || original.titulo,
      vendedor: normalizeSeller(item.seller_name || seller.nickname || seller.name || ''),
      seller_id: item.seller_id || seller.id || '',
      data_criacao: item.date_created || item.start_time || item.created_at || item.creation_date || item.listing_start_time || '',
      vendas: item.sold_quantity !== undefined ? salesOrZero(item.sold_quantity) : 0,
      marca: '',
      localizacao_vendedor: [city, state].filter(Boolean).join('/'),
      fonte: 'extensao_background_api'
    };
  }

  async function enrichOne(raw) {
    var original = raw && typeof raw === 'object' ? raw : {};
    var id = itemId(original.id || original.url);
    if (!id) return null;
    var cacheKey = id;
    var cached = cache.get(cacheKey);
    if (cached && Date.now() - cached.time < CACHE_TTL_MS) {
      return Object.assign({}, cached.data, {
        posicao: original.posicao || cached.data.posicao,
        titulo: original.titulo || cached.data.titulo,
        url: original.url || cached.data.url
      });
    }

    var url = canonicalUrl(id, original.url);
    var result = {
      posicao: original.posicao || 0,
      id: id,
      url: url,
      titulo: original.titulo || '',
      vendedor: original.vendedor || '',
      seller_id: original.seller_id || '',
      data_criacao: original.data_criacao || '',
      vendas: original.vendas !== undefined ? salesOrZero(original.vendas) : 0,
      visitas: original.visitas || null,
      marca: original.marca || '',
      localizacao_vendedor: original.localizacao_vendedor || '',
      reputacao_vendedor: original.reputacao_vendedor || '',
      fonte: original.fonte || 'extensao_background'
    };

    try {
      var item = await fetchJson('https://api.mercadolibre.com/items/' + encodeURIComponent(id));
      mergeInfo(result, infoFromApi(item, result), 'extensao_background_api');
      if (result.seller_id && !result.vendedor) {
        try {
          var user = await fetchJson('https://api.mercadolibre.com/users/' + encodeURIComponent(result.seller_id));
          mergeInfo(result, {
            vendedor: normalizeSeller(user.nickname || user.official_store_name || (user.official_store && user.official_store.name) || ''),
            reputacao_vendedor: user.seller_reputation && user.seller_reputation.power_seller_status
          }, 'extensao_background_user');
        } catch (_userErr) {}
      }
    } catch (_apiErr) {}

    try {
      var visits = await fetchJson('https://api.mercadolibre.com/items/' + encodeURIComponent(id) + '/visits/time_window?last=30&unit=day');
      var totalVisits = visits && (visits.total_visits || visits.totalVisits || visits.total);
      if (totalVisits !== undefined && totalVisits !== null) result.visitas = parseSales(totalVisits);
    } catch (_visitsErr) {}

    if (!result.vendedor || !result.data_criacao || looksLikeCatalogUrl(original.url || result.url)) {
      try {
        var html = await fetchText(url);
        mergeInfo(result, parseHtmlInfo(html, id, url), 'extensao_background_html');
      } catch (_htmlErr) {}
    }

    if (result.vendas === null || result.vendas === undefined || result.vendas === '') {
      result.vendas = 0;
    }

    cache.set(cacheKey, { time: Date.now(), data: result });
    return result;
  }

  async function mapLimit(items, limit, worker) {
    var list = Array.isArray(items) ? items : [];
    var max = Math.max(1, Math.min(Number(limit) || 4, list.length || 1));
    var output = [];
    var cursor = 0;
    async function next() {
      while (cursor < list.length) {
        var index = cursor++;
        try {
          var value = await worker(list[index], index);
          if (value) output.push(value);
        } catch (_err) {}
      }
    }
    await Promise.all(Array.from({ length: max }, next));
    return output;
  }

  chrome.runtime.onMessage.addListener(function (message, _sender, sendResponse) {
    if (!message || message.action !== 'JK_ML_ENRICH_ITEMS') return false;
    var payload = message.payload || {};
    var items = Array.isArray(payload.items) ? payload.items.slice(0, 80) : [];
    mapLimit(items, 4, enrichOne)
      .then(function (resultados) {
        sendResponse({ success: true, version: VERSION, total: resultados.length, anuncios: resultados });
      })
      .catch(function (err) {
        sendResponse({ success: false, error: err && err.message ? err.message : String(err), anuncios: [] });
      });
    return true;
  });
})();
