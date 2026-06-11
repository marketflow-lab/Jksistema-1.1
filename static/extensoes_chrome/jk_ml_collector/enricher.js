(function () {
  'use strict';

  var VERSION = '0.7.0';
  var scheduled = 0;
  var lastSignature = '';
  var lastRunAt = 0;

  function clean(value) {
    return String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
  }

  function absoluteUrl(href) {
    try {
      return new URL(href || '', location.href).href;
    } catch (_err) {
      return '';
    }
  }

  function itemId(value) {
    var text = String(value || '').toUpperCase();
    var match = text.match(/[?&](?:WID|ITEM_ID|ITEMID|ID)=?(MLB\d{8,})/i);
    if (match) return String(match[1] || '').toUpperCase();
    match = text.match(/MLB-\d{8,}/i);
    if (match) return match[0].replace('MLB-', 'MLB').toUpperCase();
    match = text.match(/\/P\/(MLB\d{8,})/i);
    if (match) return String(match[1] || '').toUpperCase();
    match = text.match(/\bMLB\d{8,}\b/i);
    if (match) return String(match[0] || '').toUpperCase();
    return '';
  }

  function firstText(root, selectors) {
    if (!root || !root.querySelector) return '';
    for (var i = 0; i < selectors.length; i += 1) {
      var node = root.querySelector(selectors[i]);
      var text = clean(node && (node.innerText || node.textContent));
      if (text) return text;
    }
    return '';
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
    raw = raw.indexOf(',') >= 0 ? raw.replace(/\./g, '').replace(',', '.') : raw.replace(/\./g, '');
    var parsed = Number(raw);
    if (!isFinite(parsed)) return null;
    if (suffix === 'mil' || suffix === 'k') parsed *= 1000;
    return Math.round(parsed);
  }

  function salesOrZero(value) {
    var parsed = parseSales(value);
    return parsed === null ? 0 : parsed;
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function displayValue(value) {
    var text = clean(value);
    return text && text !== 'null' && text !== 'undefined' ? text : '-';
  }

  function mergeSources() {
    var seen = {};
    var out = [];
    Array.prototype.slice.call(arguments).join(' + ').split('+').forEach(function (part) {
      var value = clean(part);
      if (!value || seen[value]) return;
      seen[value] = true;
      out.push(value);
    });
    return out.join(' + ');
  }

  function isBlockedExternalSource(value) {
    var source = typeof value === 'string' ? value : (value && value.fonte);
    return /rotulos_visuais|rotulos visuais|avantpro|avant pro|extensao_card_visual|extensao card visual/i.test(clean(source));
  }

  function cleanSource(value) {
    var seen = {};
    var output = [];
    String(value || '').split('+').forEach(function (part) {
      var source = clean(part);
      if (!source || isBlockedExternalSource(source) || seen[source]) return;
      seen[source] = true;
      output.push(source);
    });
    return output.join(' + ');
  }

  function trustedSource(item) {
    return !isBlockedExternalSource(item);
  }

  function hasTrustedEnrichment(item) {
    return /backend|sistema_enriquecimento|extensao_background|api_item|codigo_fonte|mercadolivre_api/i.test(clean(item && item.fonte));
  }

  function chooseTrustedValue(current, normalized, field, reader) {
    var currentValue = reader ? reader(current) : clean(current && current[field]);
    var normalizedValue = reader ? reader(normalized) : clean(normalized && normalized[field]);
    if (trustedSource(normalized) && normalizedValue) return normalizedValue;
    if (trustedSource(current) && currentValue) return currentValue;
    return normalizedValue || currentValue || '';
  }

  function chooseTrustedSales(current, normalized) {
    var vendas = parseSales(normalized && normalized.vendas);
    var vendasAtual = parseSales(current && current.vendas);
    if (trustedSource(normalized) && vendas !== null) {
      return vendasAtual !== null && vendas === 0 && !hasTrustedEnrichment(normalized) ? vendasAtual : vendas;
    }
    if (trustedSource(current) && vendasAtual !== null) return vendasAtual;
    if (vendas !== null) return vendasAtual !== null && vendas === 0 ? vendasAtual : vendas;
    return vendasAtual === null ? 0 : vendasAtual;
  }

  function formatDate(value) {
    var text = clean(value);
    if (!text || text === '-') return '-';
    var br = text.match(/\b\d{1,2}\/\d{1,2}\/\d{2,4}\b/);
    if (br) return br[0];
    var iso = text.match(/\b(?:19|20)\d{2}-\d{2}-\d{2}/);
    if (iso) {
      var parts = iso[0].split('-');
      return parts[2] + '/' + parts[1] + '/' + parts[0];
    }
    return text;
  }

  function textWithoutOwnPanels(root) {
    if (!root) return '';
    try {
      var clone = root.cloneNode(true);
      Array.prototype.slice.call(clone.querySelectorAll('.jk-ml-info-card, [data-jk-ml-info-card]')).forEach(function (node) {
        if (node && node.parentNode) node.parentNode.removeChild(node);
      });
      return clean(clone.innerText || clone.textContent || '');
    } catch (_err) {
      return clean(root.innerText || root.textContent || '');
    }
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

  function dateFromDaysText(value) {
    var text = clean(value);
    var normalized = text.normalize ? text.normalize('NFD').replace(/[\u0300-\u036f]/g, '') : text;
    normalized = normalized.toLowerCase();
    var match = normalized.match(/\b(?:ha|hace|faz)\s+([0-9.]+)\s+dias?\b/i)
      || normalized.match(/\b([0-9.]+)\s+dias?\s+(?:de|desde|apos)\s+(?:publicacao|criacao|criado|anuncio)\b/i);
    if (!match) return '';
    var days = Number(String(match[1] || '').replace(/\./g, ''));
    if (!isFinite(days) || days < 0 || days > 10000) return '';
    var date = new Date();
    date.setHours(12, 0, 0, 0);
    date.setDate(date.getDate() - days);
    return date.toISOString();
  }

  function fieldValue(item, names) {
    for (var i = 0; i < names.length; i += 1) {
      var value = item && item[names[i]];
      if (value !== null && value !== undefined && clean(value) !== '') return value;
    }
    return '';
  }

  function dateValue(item) {
    var value = fieldValue(item, [
      'data_criacao',
      'dataCriacao',
      'date_created',
      'dateCreated',
      'createdAt',
      'createdDate',
      'listingCreatedAt',
      'listing_start_time',
      'listingStartTime',
      'start_time',
      'startTime',
      'start_date',
      'startDate',
      'item_date_created',
      'itemDateCreated',
      'publication_date',
      'publicationDate',
      'datePosted',
      'firstPublicationDate',
      'availableSince',
      'activeSince'
    ]);
    if (value && typeof value === 'object') {
      value = value.value || value.date || value.text || value.label || value.display_value || value.displayValue || '';
    }
    return visibleDate(value) || dateFromDaysText(value) || clean(value);
  }

  function itemKey(item) {
    var id = item && item.id;
    if (id) return 'id:' + String(id).toUpperCase();
    var url = clean(item && item.url).split('#')[0].split('?')[0];
    return url ? 'url:' + url : '';
  }

  function mergeItems(base, extra) {
    var map = new Map();
    function add(item) {
      if (!item || typeof item !== 'object') return;
      var id = itemId(item.id || item.url);
      if (!id) return;
      var normalized = Object.assign({}, item, { id: id });
      var source = mergeSources(normalized.fonte, normalized.source);
      if (isBlockedExternalSource(source)) {
        normalized.vendedor = '';
        normalized.data_criacao = '';
        normalized.vendas = null;
        normalized.visitas = null;
        normalized.localizacao_vendedor = '';
      }
      normalized.fonte = cleanSource(source) || clean(normalized.fonte) || 'extensao_card';
      var key = itemKey(normalized);
      var current = map.get(key);
      if (!current) {
        map.set(key, normalized);
        return;
      }
      var visitas = parseSales(normalized.visitas);
      var visitasAtual = parseSales(current.visitas);
      var visitasFinal = visitas !== null && trustedSource(normalized)
        ? visitas
        : (visitasAtual !== null ? visitasAtual : null);
      map.set(key, Object.assign({}, current, normalized, {
        posicao: current.posicao || normalized.posicao,
        titulo: normalized.titulo || current.titulo || '',
        url: normalized.url || current.url || '',
        vendedor: chooseTrustedValue(current, normalized, 'vendedor'),
        seller_id: normalized.seller_id || current.seller_id || '',
        data_criacao: chooseTrustedValue(current, normalized, 'data_criacao', dateValue),
        vendas: chooseTrustedSales(current, normalized),
        visitas: visitasFinal,
        marca: normalized.marca || current.marca || '',
        localizacao_vendedor: chooseTrustedValue(current, normalized, 'localizacao_vendedor'),
        reputacao_vendedor: normalized.reputacao_vendedor || current.reputacao_vendedor || '',
        fonte: mergeSources(current.fonte, normalized.fonte)
      }));
    }
    (Array.isArray(base) ? base : []).forEach(add);
    (Array.isArray(extra) ? extra : []).forEach(add);
    return Array.from(map.values()).sort(function (a, b) {
      return (a.posicao || 9999) - (b.posicao || 9999);
    });
  }

  function readBridge() {
    var node = document.getElementById('jk-ml-collector-data');
    if (!node || !node.textContent) return null;
    try {
      return JSON.parse(node.textContent);
    } catch (_err) {
      return null;
    }
  }

  function ensureInfoCardStyle() {
    if (document.getElementById('jk-ml-info-card-style')) return;
    var style = document.createElement('style');
    style.id = 'jk-ml-info-card-style';
    style.textContent = [
      '.jk-ml-info-card{font-family:Arial,Helvetica,sans-serif;background:#fff;border:1px solid #e5e9f2;border-radius:8px;box-shadow:0 2px 8px rgba(15,23,42,.08);margin:10px 0 0;padding:10px;color:#172033;max-width:100%;box-sizing:border-box;}',
      '.jk-ml-info-card *{box-sizing:border-box;}',
      '.jk-ml-info-head{display:flex;align-items:center;justify-content:space-between;gap:8px;border-bottom:1px solid #eef2f7;padding-bottom:7px;margin-bottom:8px;}',
      '.jk-ml-info-title{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:700;color:#172033;line-height:1.2;}',
      '.jk-ml-info-logo{width:18px;height:18px;border-radius:50%;background:#1f4fff;color:#fff;display:inline-flex;align-items:center;justify-content:center;font-size:11px;font-weight:800;}',
      '.jk-ml-info-status{font-size:10px;font-weight:700;border-radius:999px;padding:3px 7px;background:#eef4ff;color:#1f4fff;white-space:nowrap;}',
      '.jk-ml-info-status.is-pending{background:#fff4e5;color:#995c00;}',
      '.jk-ml-info-status.is-ok{background:#e8f8ef;color:#087a3d;}',
      '.jk-ml-info-grid{display:grid;grid-template-columns:1fr;gap:7px;}',
      '.jk-ml-info-row{display:grid;grid-template-columns:minmax(78px,auto) 1fr;align-items:start;gap:8px;font-size:11px;line-height:1.25;}',
      '.jk-ml-info-label{color:#64748b;}',
      '.jk-ml-info-value{color:#172033;font-weight:700;word-break:break-word;}',
      '.jk-ml-info-muted{color:#94a3b8;font-weight:600;}',
      '.jk-ml-info-foot{margin-top:8px;padding-top:7px;border-top:1px solid #eef2f7;font-size:10px;color:#64748b;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}'
    ].join('');
    (document.head || document.documentElement).appendChild(style);
  }

  function findSearchCards() {
    return Array.prototype.slice.call(document.querySelectorAll([
      'li.ui-search-layout__item',
      '.ui-search-result__wrapper',
      '.ui-search-result',
      '.poly-card',
      '[class*="poly-card"]',
      '[data-testid*="result"]',
      'article'
    ].join(',')));
  }

  function findRenderedCardsById(id) {
    var target = itemId(id);
    if (!target) return [];
    return findSearchCards().filter(function (card) {
      var attr = itemId(card.getAttribute('data-jk-ml-item-id') || '');
      if (attr && attr === target) return true;
      return itemId(card.innerHTML || card.textContent || '') === target;
    });
  }

  function infoCompleteness(item) {
    var vendedor = clean(fieldValue(item, ['vendedor', 'seller_name', 'sellerName', 'nickname']));
    var data = clean(dateValue(item));
    var vendas = salesOrZero(fieldValue(item, ['vendas', 'sales', 'sold_quantity', 'soldQuantity']));
    var missing = [];
    if (!vendedor) missing.push('vendedor');
    if (!data) missing.push('data');
    return { missing: missing, complete: !missing.length };
  }

  function infoRows(item) {
    var vendas = salesOrZero(fieldValue(item, ['vendas', 'sales', 'sold_quantity', 'soldQuantity']));
    var visitas = parseSales(fieldValue(item, ['visitas', 'visits', 'total_visits', 'totalVisits']));
    return [
      ['mlb', 'MLB', itemId(item && (item.id || item.url)) || '-'],
      ['vendedor', 'Vendedor', fieldValue(item, ['vendedor', 'seller_name', 'sellerName', 'nickname']) || '-'],
      ['data_criacao', 'Criado em', formatDate(dateValue(item))],
      ['vendas', 'Vendas', String(vendas)],
      ['visitas', 'Visitas', visitas === null ? '-' : String(visitas)],
      ['marca', 'Marca', fieldValue(item, ['marca', 'brand', 'brand_name']) || '-'],
      ['localizacao_vendedor', 'Localizacao', fieldValue(item, ['localizacao_vendedor', 'seller_location', 'location']) || '-']
    ];
  }

  function cardHtml(item, reason) {
    var completeness = infoCompleteness(item);
    var statusClass = completeness.complete ? 'is-ok' : 'is-pending';
    var statusText = completeness.complete ? 'Completo' : 'Pendente';
    var rows = infoRows(item).map(function (row) {
      var value = displayValue(row[2]);
      return '<div class="jk-ml-info-row" data-jk-ml-field="' + escapeHtml(row[0]) + '">' +
        '<span class="jk-ml-info-label">' + escapeHtml(row[1]) + '</span>' +
        '<span class="jk-ml-info-value' + (value === '-' ? ' jk-ml-info-muted' : '') + '">' + escapeHtml(value) + '</span>' +
        '</div>';
    }).join('');
    var fonte = clean(item && item.fonte) || clean(reason) || 'extensao_local';
    return '<div class="jk-ml-info-head">' +
      '<div class="jk-ml-info-title"><span class="jk-ml-info-logo">JK</span><span>Informacoes JK</span></div>' +
      '<div class="jk-ml-info-status ' + statusClass + '">' + statusText + '</div>' +
      '</div>' +
      '<div class="jk-ml-info-grid">' + rows + '</div>' +
      '<div class="jk-ml-info-foot" data-jk-ml-field="fonte">Fonte: ' + escapeHtml(fonte) + '</div>';
  }

  function canonicalUrl(id, fallback) {
    var cleanId = itemId(id || fallback);
    if (fallback && /^https?:\/\//i.test(String(fallback))) return String(fallback);
    return cleanId ? 'https://produto.mercadolivre.com.br/' + cleanId.replace('MLB', 'MLB-') + '-_JM' : '';
  }

  async function fetchJsonOwnContext(url) {
    var controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    var timer = controller ? setTimeout(function () { controller.abort(); }, 6500) : null;
    try {
      var response = await fetch(url, {
        method: 'GET',
        credentials: 'include',
        cache: 'no-store',
        signal: controller ? controller.signal : undefined,
        headers: {
          Accept: 'application/json,text/plain,*/*',
          'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8'
        }
      });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      return response.json();
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  async function fetchTextOwnContext(url) {
    var controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    var timer = controller ? setTimeout(function () { controller.abort(); }, 6500) : null;
    try {
      var response = await fetch(url, {
        method: 'GET',
        credentials: 'include',
        cache: 'no-store',
        signal: controller ? controller.signal : undefined,
        headers: {
          Accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
          'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8'
        }
      });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      return response.text();
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  function sellerFromApi(item) {
    if (!item || typeof item !== 'object') return '';
    var seller = item.seller && typeof item.seller === 'object' ? item.seller : {};
    var store = item.official_store && typeof item.official_store === 'object' ? item.official_store : {};
    return clean(item.seller_name || item.sellerName || item.nickname || item.official_store_name || item.officialStoreName || store.name || store.nickname || seller.nickname || seller.name || '');
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
      vendedor: sellerFromApi(item),
      seller_id: item.seller_id || seller.id || '',
      data_criacao: item.date_created || item.start_time || item.created_at || item.creation_date || item.listing_start_time || '',
      vendas: item.sold_quantity !== undefined ? salesOrZero(item.sold_quantity) : 0,
      marca: '',
      localizacao_vendedor: [city, state].filter(Boolean).join('/'),
      fonte: 'ml_pagina_api'
    };
  }

  function pick(patterns, text) {
    var source = String(text || '');
    for (var i = 0; i < patterns.length; i += 1) {
      var match = source.match(patterns[i]);
      if (match && match[1] != null) return clean(match[1]);
    }
    return '';
  }

  function infoFromHtml(html, original) {
    var text = clean(html);
    return {
      id: itemId(original.id || text),
      url: original.url,
      titulo: pick([
        /<h1[^>]*class=["'][^"']*ui-pdp-title[^"']*["'][^>]*>(.*?)<\/h1>/i,
        /<meta[^>]+property=["']og:title["'][^>]+content=["']([^"']+)["']/i,
        /<title[^>]*>(.*?)<\/title>/i,
        /["']title["']\s*:\s*["']([^"']+)["']/i
      ], html).replace(/\s*\|\s*MercadoLivre.*$/i, ''),
      vendedor: pick([
        /["']seller_name["']\s*:\s*["']([^"']+)["']/i,
        /["']sellerName["']\s*:\s*["']([^"']+)["']/i,
        /["']official_store_name["']\s*:\s*["']([^"']+)["']/i,
        /["']seller["']\s*:\s*\{[^{}]{0,500}["']nickname["']\s*:\s*["']([^"']+)["']/i,
        /class=["'][^"']*ui-pdp-seller__header__title[^"']*["'][^>]*>(.*?)</i
      ], html).replace(/^(?:nome\s+do\s+vendedor|vendedor|loja\s+oficial|vendido\s+por)\s*[:\-]?\s*/i, '').trim(),
      seller_id: pick([
        /["']seller_id["']\s*:\s*"?(\d+)"?/i,
        /["']sellerId["']\s*:\s*"?(\d+)"?/i,
        /["']seller["']\s*:\s*\{[^{}]{0,500}["']id["']\s*:\s*"?(\d+)"?/i
      ], html),
      data_criacao: pickDateFromText(text),
      vendas: salesOrZero(pick([
        /["']sold_quantity["']\s*:\s*([0-9,.]+)/i,
        /["']soldQuantity["']\s*:\s*([0-9,.]+)/i
      ], html) || text),
      fonte: 'ml_pagina_html'
    };
  }

  async function enrichOneOwnContext(raw) {
    var original = raw && typeof raw === 'object' ? raw : {};
    var id = itemId(original.id || original.url);
    if (!id) return null;
    var url = canonicalUrl(id, original.url);
    var result = {
      posicao: original.posicao || 0,
      id: id,
      url: url,
      titulo: original.titulo || original.title || '',
      vendedor: '',
      seller_id: '',
      data_criacao: '',
      vendas: 0,
      visitas: null,
      marca: '',
      localizacao_vendedor: '',
      fonte: 'ml_pagina'
    };
    try {
      var item = await fetchJsonOwnContext('https://api.mercadolibre.com/items/' + encodeURIComponent(id));
      result = mergeItems([result], [infoFromApi(item, result)])[0] || result;
      if (result.seller_id && !result.vendedor) {
        try {
          var user = await fetchJsonOwnContext('https://api.mercadolibre.com/users/' + encodeURIComponent(result.seller_id));
          result.vendedor = clean(user.nickname || user.official_store_name || (user.official_store && user.official_store.name) || result.vendedor);
          result.fonte = mergeSources(result.fonte, 'ml_pagina_user');
        } catch (_userErr) {}
      }
    } catch (_apiErr) {}
    try {
      var visits = await fetchJsonOwnContext('https://api.mercadolibre.com/items/' + encodeURIComponent(id) + '/visits/time_window?last=30&unit=day');
      var totalVisits = visits && (visits.total_visits || visits.totalVisits || visits.total);
      if (totalVisits !== undefined && totalVisits !== null) result.visitas = parseSales(totalVisits);
      result.fonte = mergeSources(result.fonte, 'ml_pagina_visitas');
    } catch (_visitsErr) {}
    if (!result.vendedor || !result.data_criacao) {
      try {
        var html = await fetchTextOwnContext(url);
        result = mergeItems([result], [infoFromHtml(html, result)])[0] || result;
      } catch (_htmlErr) {}
    }
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

  async function sendPageEnrichment(items) {
    var resultados = await mapLimit(items, 4, enrichOneOwnContext);
    return resultados.length
      ? { success: true, source: 'ml_page_context', anuncios: resultados }
      : { success: false, error: 'ml_page_sem_resultado', anuncios: [] };
  }

  function renderInfoCards(items, reason) {
    ensureInfoCardStyle();
    var list = Array.isArray(items) ? items : [];
    list.forEach(function (item) {
      var id = itemId(item && (item.id || item.url));
      if (!id) return;
      var cards = findRenderedCardsById(id);
      cards.forEach(function (card) {
        card.setAttribute('data-jk-ml-item-id', id);
        var host = card.querySelector('.poly-card__content, .ui-search-result__content, .ui-search-item__group, [class*="content"]') || card;
        var panel = card.querySelector('.jk-ml-info-card');
        if (!panel) {
          panel = document.createElement('div');
          panel.className = 'jk-ml-info-card';
          panel.setAttribute('data-jk-ml-info-card', id);
          host.appendChild(panel);
        }
        panel.setAttribute('data-jk-ml-info-card', id);
        panel.setAttribute('data-jk-ml-vendedor', clean(fieldValue(item, ['vendedor', 'seller_name', 'sellerName', 'nickname'])));
        panel.setAttribute('data-jk-ml-data-criacao', clean(dateValue(item)));
        panel.setAttribute('data-jk-ml-vendas', String(salesOrZero(fieldValue(item, ['vendas', 'sales', 'sold_quantity', 'soldQuantity']))));
        panel.setAttribute('data-jk-ml-fonte', clean(item && item.fonte) || clean(reason));
        var html = cardHtml(item, reason);
        if (panel.__jkLastHtml !== html) {
          panel.innerHTML = html;
          panel.__jkLastHtml = html;
        }
      });
    });
  }

  function publishBridge(items, reason) {
    var previous = readBridge() || {};
    var merged = mergeItems(previous.anuncios || [], items || []);
    var payload = Object.assign({}, previous, {
      version: VERSION,
      ready: true,
      updatedAt: Date.now(),
      url: location.href,
      title: document.title || '',
      total: merged.length,
      lastReason: reason || 'background_enrichment',
      anuncios: merged
    });
    var node = document.getElementById('jk-ml-collector-data');
    if (!node) {
      node = document.createElement('script');
      node.id = 'jk-ml-collector-data';
      node.type = 'application/json';
      (document.head || document.documentElement).appendChild(node);
    }
    node.textContent = JSON.stringify(payload);
    document.documentElement.setAttribute('data-jk-ml-collector-ready', '1');
    document.documentElement.setAttribute('data-jk-ml-collector-version', VERSION);
    document.documentElement.setAttribute('data-jk-ml-collector-total', String(merged.length || 0));
    renderInfoCards(merged, reason || 'background_enrichment');
  }

  function collectCards() {
    var cards = findSearchCards();
    var items = [];
    var seen = {};
    cards.forEach(function (card) {
      if (items.length >= 80) return;
      var links = Array.prototype.slice.call(card.querySelectorAll('a[href]'));
      var link = links.find(function (node) {
        return /MLB-?\d{5,}|\/p\/MLB|[?&](?:wid|item_id)=MLB/i.test(node.getAttribute('href') || '');
      }) || links[0];
      var url = absoluteUrl(link && link.getAttribute('href'));
      var cleanCardText = textWithoutOwnPanels(card);
      var id = itemId(url || card.innerHTML || cleanCardText || '');
      var title = firstText(card, [
        'a.poly-component__title',
        '.poly-component__title',
        'h2',
        '.ui-search-item__title',
        '.ui-search-result__content-title',
        '[class*="title"]'
      ]) || clean(link && (link.innerText || link.textContent));
      if (!id || !title || /^publicidade$/i.test(title) || seen[id]) return;
      seen[id] = true;
      card.setAttribute('data-jk-ml-item-id', id);
      var item = {
        posicao: items.length + 1,
        id: id,
        titulo: title,
        url: url,
        vendas: salesOrZero(cleanCardText),
        fonte: 'extensao_card'
      };
      items.push(item);
    });
    return items;
  }

  function needsEnrichment(item) {
    if (!item) return false;
    if (!hasTrustedEnrichment(item)) return true;
    return !fieldValue(item, ['vendedor', 'seller_name', 'sellerName', 'nickname'])
      || !dateValue(item)
      || parseSales(fieldValue(item, ['visitas', 'visits', 'total_visits', 'totalVisits'])) === null;
  }

  function collectorConfig() {
    try {
      var config = window.__JK_ML_COLLECTOR_CONFIG__;
      return config && typeof config === 'object' ? config : {};
    } catch (_err) {
      return {};
    }
  }

  async function sendBackendEnrichment(items) {
    var config = collectorConfig();
    var token = clean(config.token);
    if (!token) return { success: false, error: 'token_indisponivel', anuncios: [] };
    var base = clean(config.backendBase || 'http://127.0.0.1:8001').replace(/\/+$/g, '');
    var lista = (Array.isArray(items) ? items : []).slice(0, 80).map(function (item) {
      return {
        id: itemId(item && (item.id || item.url)),
        url: item && item.url,
        titulo: item && (item.titulo || item.title),
        posicao: item && item.posicao
      };
    }).filter(function (item) { return item.id || item.url; });
    if (!lista.length) return { success: false, error: 'sem_itens', anuncios: [] };
    try {
      var response = await fetch(base + '/api/favoritos/ml/enriquecer-datas', {
        method: 'POST',
        mode: 'cors',
        cache: 'no-store',
        headers: {
          'Content-Type': 'application/json',
          Authorization: 'Bearer ' + token
        },
        body: JSON.stringify({
          anuncios: lista,
          max_anuncios: lista.length
        })
      });
      var data = await response.json().catch(function () { return {}; });
      if (!response.ok || data.success === false) {
        return { success: false, error: data.detail || data.message || ('HTTP ' + response.status), anuncios: [] };
      }
      var anuncios = Array.isArray(data.resultados) ? data.resultados.map(function (item) {
        return Object.assign({}, item, {
          fonte: mergeSources(item.fonte, 'jk_backend')
        });
      }) : [];
      return { success: true, source: 'backend_local', anuncios: anuncios };
    } catch (err) {
      return { success: false, error: err && err.message ? err.message : String(err), anuncios: [] };
    }
  }

  async function sendEnrichment(items) {
    var backend = await sendBackendEnrichment(items);
    var backendItems = backend && backend.success && Array.isArray(backend.anuncios) ? backend.anuncios : [];
    var mergedBackend = mergeItems(items, backendItems);
    var pendingPage = mergedBackend.filter(needsEnrichment);
    if (pendingPage.length) {
      var page = await sendPageEnrichment(pendingPage);
      if (page && page.success && Array.isArray(page.anuncios)) {
        return {
          success: true,
          source: backend && backend.success ? 'backend_local+ml_page_context' : 'ml_page_context',
          anuncios: mergeItems(backendItems.length ? backendItems : items, page.anuncios)
        };
      }
    }
    if (backend && backend.success) return backend;
    return new Promise(function (resolve) {
      if (typeof chrome === 'undefined' || !chrome.runtime || typeof chrome.runtime.sendMessage !== 'function') {
        resolve({ success: false, error: backend && backend.error ? backend.error : 'runtime_indisponivel', anuncios: [] });
        return;
      }
      chrome.runtime.sendMessage({
        action: 'JK_ML_ENRICH_ITEMS',
        payload: { items: items }
      }, function (response) {
        if (chrome.runtime.lastError) {
          resolve({ success: false, error: chrome.runtime.lastError.message, anuncios: [] });
          return;
        }
        var finalResponse = response || { success: false, error: 'sem_resposta', anuncios: [] };
        if (finalResponse.success) finalResponse.source = finalResponse.source || 'extension_background';
        resolve(finalResponse);
      });
    });
  }

  async function run(reason) {
    var now = Date.now();
    var cards = collectCards();
    if (!cards.length) return;
    publishBridge(cards, reason || 'cards');
    var signature = cards.map(function (item) { return item.id; }).join('|');
    if (signature === lastSignature && now - lastRunAt < 30000) return;
    lastSignature = signature;
    lastRunAt = now;
    var current = readBridge();
    var currentById = {};
    (current && Array.isArray(current.anuncios) ? current.anuncios : []).forEach(function (item) {
      var id = itemId(item && (item.id || item.url));
      if (id) currentById[id] = item;
    });
    var pending = cards.map(function (item) {
      return Object.assign({}, item, currentById[item.id] || {});
    }).filter(needsEnrichment);
    if (!pending.length) return;
    publishBridge(cards, 'background_enrichment_started');
    var response = await sendEnrichment(pending);
    if (response && response.success && Array.isArray(response.anuncios)) {
      publishBridge(response.anuncios, /backend_local|ml_page_context/.test(String(response.source || '')) ? 'backend_enrichment_done' : 'background_enrichment_done');
    } else {
      publishBridge(cards, 'background_enrichment_failed:' + clean(response && response.error));
    }
  }

  function schedule(reason, delay) {
    if (scheduled) clearTimeout(scheduled);
    scheduled = setTimeout(function () {
      scheduled = 0;
      run(reason || 'scheduled').catch(function () {});
    }, delay == null ? 180 : delay);
  }

  function setupObserver() {
    if (!document.documentElement || !window.MutationObserver) return;
    try {
      var observer = new MutationObserver(function () {
        schedule('mutation', 450);
      });
      observer.observe(document.documentElement, { childList: true, subtree: true });
    } catch (_err) {}
  }

  try {
    window.__JK_ML_ENRICHER__ = {
      version: VERSION,
      publish: function (items, reason) {
        publishBridge(Array.isArray(items) ? items : [], reason || 'external_publish');
        return true;
      }
    };
  } catch (_err) {}

  setupObserver();
  if (document.readyState === 'loading') {
    schedule('early-loading', 80);
    document.addEventListener('DOMContentLoaded', function () { schedule('domcontentloaded', 80); }, { once: true });
  } else {
    schedule('initial', 80);
  }
  setTimeout(function () { schedule('delayed-1', 0); }, 350);
  setTimeout(function () { schedule('delayed-2', 0); }, 900);
  setTimeout(function () { schedule('delayed-3', 0); }, 1800);
  setTimeout(function () { schedule('delayed-4', 0); }, 3500);
  setTimeout(function () { schedule('delayed-5', 0); }, 7000);
})();
