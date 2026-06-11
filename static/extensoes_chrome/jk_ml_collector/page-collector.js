(function () {
  'use strict';

  var VERSION = '0.7.0';

  if (window.__JK_ML_COLLECTOR__ && window.__JK_ML_COLLECTOR__.version === VERSION) {
    return;
  }

  var MAX_TEXT_LENGTH = 5000000;
  var state = {
    version: VERSION,
    ready: false,
    updatedAt: 0,
    url: '',
    title: '',
    total: 0,
    lastReason: 'init',
    anuncios: [],
    sources: {}
  };
  var byKey = {};
  var scheduled = 0;
  var lastDeepScanUrl = '';
  var lastDeepScanAt = 0;
  var mergingBridge = false;

  function clean(value) {
    return String(value == null ? '' : value)
      .replace(/\\u002F/g, '/')
      .replace(/\\\//g, '/')
      .replace(/\\n/g, ' ')
      .replace(/\\t/g, ' ')
      .replace(/\\r/g, ' ')
      .replace(/&quot;/g, '"')
      .replace(/\\"/g, '"')
      .replace(/\s+/g, ' ')
      .trim();
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
    match = text.match(/[?&][^=]*(?:ITEM|WID)[^=]*=(MLB\d{8,})/i);
    if (match) return String(match[1] || '').toUpperCase();
    match = text.match(/MLB-\d{8,}/i);
    if (match) return match[0].replace('MLB-', 'MLB').toUpperCase();
    match = text.match(/\/P\/(MLB\d{8,})/i);
    if (match) return String(match[1] || '').toUpperCase();
    match = text.match(/\bMLB\d{8,}\b/i);
    if (match) return String(match[0] || '').toUpperCase();
    return '';
  }

  function parseSales(value) {
    if (value == null || value === '') return null;
    if (typeof value === 'number' && isFinite(value)) return Math.round(value);
    var text = clean(value).toLowerCase();
    if (!text) return null;
    var match = text.match(/(?:\+?\s*)?(\d+(?:[\.,]\d+)?\s*(?:mil|k)?)\s+(?:vendid[oa]s?|ventas?|sales?)/i)
      || text.match(/(?:vendid[oa]s?|ventas?|sales?)\s*(?:\+?\s*)?(\d+(?:[\.,]\d+)?\s*(?:mil|k)?)/i);
    if (!match) {
      match = text.match(/^\+?\s*(\d+(?:[\.,]\d+)?)\s*(mil|k)?\s*$/i);
    }
    if (!match) return null;
    var raw = String(match[1] || '').replace(/\s+/g, '');
    var suffix = String(match[2] || (/mil|k/i.test(raw) ? raw.match(/mil|k/i)[0] : '')).toLowerCase();
    raw = raw.replace(/mil|k/ig, '');
    if (raw.indexOf('.') >= 0 && raw.indexOf(',') >= 0) {
      raw = raw.lastIndexOf('.') > raw.lastIndexOf(',')
        ? raw.replace(/,/g, '')
        : raw.replace(/\./g, '').replace(',', '.');
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

  function firstText(root, selectors) {
    if (!root || !root.querySelector) return '';
    for (var i = 0; i < selectors.length; i += 1) {
      var node = root.querySelector(selectors[i]);
      var text = clean(node && (node.innerText || node.textContent));
      if (text) return text;
    }
    return '';
  }

  function sellerName(value) {
    return clean(value)
      .replace(/^(vendido\s+por|loja\s+oficial|oficial\s+loja)\s+/i, '')
      .trim();
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

  function trustedSource(value) {
    return !isBlockedExternalSource(value);
  }

  function cleanSource(value) {
    var seen = {};
    var out = [];
    String(value || '').split('+').forEach(function (part) {
      var source = clean(part);
      if (!source || isBlockedExternalSource(source) || seen[source]) return;
      seen[source] = true;
      out.push(source);
    });
    return out.join(' + ');
  }

  function chooseTrustedValue(current, item, field) {
    var currentValue = clean(current && current[field]);
    var itemValue = clean(item && item[field]);
    if (trustedSource(item) && itemValue) return itemValue;
    if (trustedSource(current) && currentValue) return currentValue;
    return itemValue || currentValue || '';
  }

  function chooseTrustedSales(current, item) {
    var currentSales = parseSales(current && current.vendas);
    var itemSales = parseSales(item && item.vendas);
    if (trustedSource(item) && itemSales !== null) return itemSales;
    if (trustedSource(current) && currentSales !== null) return currentSales;
    if (itemSales !== null) return currentSales !== null && itemSales === 0 ? currentSales : itemSales;
    return currentSales;
  }

  function visibleDate(value) {
    var text = clean(value).replace(/^[^0-9]*(?=\d)/, '');
    var iso = text.match(/\b(?:19|20)\d{2}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?\b/);
    if (iso) return iso[0];
    var br = text.match(/\b\d{1,2}\/\d{1,2}\/\d{2,4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?\b/);
    if (br) return br[0];
    var longPt = text.match(/\b\d{1,2}\s+de\s+[a-zA-Z\u00c0-\u017f]+\s+de\s+(?:19|20)\d{2}\b/i);
    if (longPt) return longPt[0];
    return '';
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

  function pickDateFromText(value) {
    var text = clean(value);
    if (!text) return '';
    var patterns = [
      /["'](?:date_created|dateCreated|start_time|startTime|creation_date|creationDate|created_at|createdAt|createdDate|item_date_created|itemDateCreated|listing_start_time|listingStartTime|listing_created_at|listingCreatedAt|start_date|startDate|itemStartTime|publication_date|publicationDate|published_at|publishedAt|date_published|datePublished|datePosted|date_posted|first_publication_date|firstPublicationDate|available_since|availableSince|active_since|activeSince)["']\s*:\s*["']([^"']+)["']/i,
      /["'](?:date_created|dateCreated|start_time|startTime|creation_date|creationDate|created_at|createdAt|createdDate|item_date_created|itemDateCreated|listing_start_time|listingStartTime|listing_created_at|listingCreatedAt|start_date|startDate|itemStartTime|publication_date|publicationDate|published_at|publishedAt|date_published|datePublished|datePosted|date_posted|first_publication_date|firstPublicationDate|available_since|availableSince|active_since|activeSince)["']\s*:\s*\{[^{}]{0,320}["'](?:value|date|text|label|display_value|displayValue|raw)["']\s*:\s*["']([^"']+)["']/i,
      /(?:date_created|dateCreated|start_time|startTime|creation_date|creationDate|created_at|createdAt|createdDate|item_date_created|itemDateCreated|listing_start_time|listingStartTime|listing_created_at|listingCreatedAt|publication_date|publicationDate|published_at|publishedAt|datePublished|datePosted|firstPublicationDate|availableSince|activeSince)[^0-9]{0,160}((?:19|20)\d{2}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?|\d{1,2}\/\d{1,2}\/\d{2,4})/i
    ];
    for (var i = 0; i < patterns.length; i += 1) {
      var match = text.match(patterns[i]);
      if (match && match[1]) return clean(match[1]);
    }

    var normalized = text.normalize ? text.normalize('NFD').replace(/[\u0300-\u036f]/g, '') : text;
    normalized = normalized.toLowerCase().replace(/\s+/g, ' ');
    var labels = [
      'anuncio criado em',
      'anuncio ganhador criado em',
      'catalogo criado em',
      'criado em',
      'publicado em',
      'publicacao',
      'data de criacao',
      'criacao do anuncio',
      'disponivel desde',
      'ativo desde'
    ];
    for (var li = 0; li < labels.length; li += 1) {
      var idx = normalized.indexOf(labels[li]);
      if (idx < 0) continue;
      var data = visibleDate(text.slice(idx, idx + 180));
      if (data) return data;
    }
    return dateFromDaysText(text);
  }

  function itemKey(item) {
    var id = item && item.id;
    if (id) return 'id:' + String(id).toUpperCase();
    var url = clean(item && item.url).split('#')[0].split('?')[0];
    if (url) return 'url:' + url;
    return '';
  }

  function readBridgeState() {
    var node = document.getElementById('jk-ml-collector-data');
    if (!node || !node.textContent) return null;
    try {
      return JSON.parse(node.textContent);
    } catch (_err) {
      return null;
    }
  }

  function mergeExistingBridge() {
    if (mergingBridge) return;
    var previous = readBridgeState();
    if (!previous || !Array.isArray(previous.anuncios) || !previous.anuncios.length) return;
    mergingBridge = true;
    try {
      previous.anuncios.forEach(function (item) {
        mergeItem(item, item && item.fonte ? item.fonte : 'ponte_existente');
      });
    } finally {
      mergingBridge = false;
    }
  }

  function publish(reason) {
    mergeExistingBridge();
    var list = Object.keys(byKey)
      .map(function (key) { return byKey[key]; })
      .filter(function (item) { return item && item.id && item.titulo && !/^publicidade$/i.test(item.titulo); })
      .sort(function (a, b) { return (a.posicao || 9999) - (b.posicao || 9999); });
    state.ready = true;
    state.updatedAt = Date.now();
    state.url = location.href;
    state.title = document.title || '';
    state.total = list.length;
    state.lastReason = reason || state.lastReason || 'update';
    state.anuncios = list;
    try {
      var payload = JSON.stringify(state);
      document.documentElement.setAttribute('data-jk-ml-collector-ready', '1');
      document.documentElement.setAttribute('data-jk-ml-collector-version', VERSION);
      document.documentElement.setAttribute('data-jk-ml-collector-total', String(state.total || 0));
      var bridge = document.getElementById('jk-ml-collector-data');
      if (!bridge) {
        bridge = document.createElement('script');
        bridge.id = 'jk-ml-collector-data';
        bridge.type = 'application/json';
        (document.head || document.documentElement).appendChild(bridge);
      }
      bridge.textContent = payload;
    } catch (_bridgeErr) {}
    try {
      window.dispatchEvent(new CustomEvent('jk-ml-collector-updated', { detail: state }));
    } catch (_err) {}
  }

  function mergeItem(raw, source) {
    if (!raw || typeof raw !== 'object') return;
    var id = itemId(raw.id || raw.item_id || raw.itemId || raw.itemID || raw.permalink || raw.url || raw.share_url || '');
    var url = clean(raw.url || raw.permalink || raw.link || raw.href || raw.share_url || '');
    if (!id) id = itemId(url);
    if (!id) return;
    var seller = raw.seller && typeof raw.seller === 'object' ? raw.seller : {};
    var store = raw.official_store && typeof raw.official_store === 'object' ? raw.official_store : {};
    var dataRaw = raw.data_criacao || raw.dataCriacao || raw.date_created || raw.dateCreated || raw.start_time || raw.startTime || raw.creation_date || raw.creationDate || raw.created_at || raw.createdAt || raw.createdDate || raw.item_date_created || raw.itemDateCreated || raw.listing_start_time || raw.listingStartTime || raw.listing_created_at || raw.listingCreatedAt || raw.start_date || raw.startDate || raw.itemStartTime || raw.publication_date || raw.publicationDate || raw.published_at || raw.publishedAt || raw.date_published || raw.datePublished || raw.datePosted || raw.firstPublicationDate || raw.availableSince || raw.activeSince || '';
    if (dataRaw && typeof dataRaw === 'object') {
      dataRaw = dataRaw.value || dataRaw.date || dataRaw.text || dataRaw.label || dataRaw.display_value || dataRaw.displayValue || '';
    }
    var data = visibleDate(dataRaw) || dateFromDaysText(dataRaw) || clean(dataRaw);
    var vendasRaw = raw.vendas !== undefined ? raw.vendas
      : (raw.sales !== undefined ? raw.sales
        : (raw.soldQuantity !== undefined ? raw.soldQuantity
          : (raw.sold_quantity !== undefined ? raw.sold_quantity
            : (raw.sold !== undefined ? raw.sold : raw.total_sold))));
    var visitasRaw = raw.visitas !== undefined ? raw.visitas
      : (raw.visits !== undefined ? raw.visits
        : (raw.total_visits !== undefined ? raw.total_visits
          : (raw.totalVisits !== undefined ? raw.totalVisits
            : (raw.view_count !== undefined ? raw.view_count : raw.viewCount))));
    var rawSource = mergeSources(raw.fonte, raw.source, source || 'jk_ml_collector');
    var blockedSource = isBlockedExternalSource(rawSource);
    var itemSource = cleanSource(rawSource) || 'jk_ml_collector';
    var item = {
      posicao: Number(raw.posicao || raw.position || raw.index || 0) || 0,
      id: id,
      titulo: clean(raw.titulo || raw.title || raw.name || raw.description || ''),
      url: url || (id ? 'https://produto.mercadolivre.com.br/' + id.replace('MLB', 'MLB-') + '-_JM' : ''),
      vendedor: blockedSource ? '' : sellerName(raw.vendedor || raw.seller_name || raw.sellerName || raw.nickname || raw.official_store_name || raw.officialStoreName || store.name || store.nickname || seller.nickname || seller.name || ''),
      data_criacao: blockedSource ? '' : data,
      vendas: blockedSource ? null : parseSales(vendasRaw),
      visitas: blockedSource ? null : parseSales(visitasRaw),
      localizacao_vendedor: blockedSource ? '' : clean(raw.localizacao_vendedor || raw.seller_location || raw.location || raw.sellerLocation || ''),
      fonte: itemSource
    };
    if (!item.titulo && !item.url) return;
    var key = itemKey(item);
    if (!key) return;
    var current = byKey[key] || {};
    var visitasItem = parseSales(item.visitas);
    var visitasAtual = parseSales(current.visitas);
    byKey[key] = {
      posicao: current.posicao || item.posicao,
      id: current.id || item.id,
      titulo: item.titulo || current.titulo || '',
      url: item.url || current.url || '',
      vendedor: chooseTrustedValue(current, item, 'vendedor'),
      data_criacao: chooseTrustedValue(current, item, 'data_criacao'),
      vendas: chooseTrustedSales(current, item),
      visitas: visitasItem !== null ? visitasItem : (visitasAtual !== null ? visitasAtual : null),
      localizacao_vendedor: chooseTrustedValue(current, item, 'localizacao_vendedor'),
      fonte: mergeSources(current.fonte, item.fonte, 'jk_ml_collector')
    };
    state.sources[item.fonte || source || 'jk_ml_collector'] = (state.sources[item.fonte || source || 'jk_ml_collector'] || 0) + 1;
  }

  function collectDom(reason) {
    var cards = Array.prototype.slice.call(document.querySelectorAll([
      'li.ui-search-layout__item',
      '.ui-search-result__wrapper',
      '.ui-search-result',
      '.poly-card',
      '[class*="poly-card"]',
      '[data-testid*="result"]',
      'article'
    ].join(',')));
    var position = 0;
    cards.forEach(function (card) {
      var links = Array.prototype.slice.call(card.querySelectorAll('a[href]'));
      var link = links.find(function (node) {
        return /MLB-?\d{5,}|\/p\/MLB|[?&](?:wid|item_id)=MLB/i.test(node.getAttribute('href') || '');
      }) || links[0];
      var url = absoluteUrl(link && link.getAttribute('href'));
      var cleanCardText = textWithoutOwnPanels(card);
      var id = itemId(url || card.innerHTML || cleanCardText || '');
      var titulo = firstText(card, [
        'a.poly-component__title',
        '.poly-component__title',
        'h2',
        '.ui-search-item__title',
        '.ui-search-result__content-title',
        '[class*="title"]'
      ]) || clean(link && (link.innerText || link.textContent));
      if (!id || !titulo || /^publicidade$/i.test(titulo)) return;
      position += 1;
      var item = {
        posicao: position,
        id: id,
        titulo: titulo,
        url: url,
        vendedor: firstText(card, [
          '.poly-component__seller',
          '[class*="seller"]',
          '[class*="official-store"]',
          '.ui-search-official-store-label'
        ]),
        data_criacao: pickDateFromText(cleanCardText),
        vendas: cleanCardText
      };
      mergeItem(item, mergeSources('extensao_dom', item.fonte));
    });

    var currentId = itemId(location.href);
    if (currentId) {
      mergeItem({
        posicao: 1,
        id: currentId,
        titulo: firstText(document, ['h1.ui-pdp-title', 'h1', '[data-testid="title"]']) || clean(document.title),
        url: location.href,
        vendedor: firstText(document, [
          '.ui-pdp-seller__header__title',
          '.ui-pdp-seller__link-trigger',
          '.ui-pdp-seller__nickname',
          '.ui-pdp-official-store-label',
          '[data-testid="seller-info"]',
          '[data-testid="official-store-info"]'
        ]),
        data_criacao: pickDateFromText(document.body && document.body.innerText),
        vendas: document.body && document.body.innerText
      }, 'extensao_produto_dom');
    }

    deepScanPageState(reason || 'dom');
    publish(reason || 'dom');
  }

  function shouldDeepScan(reason) {
    var now = Date.now();
    var currentUrl = String(location.href || '');
    if (currentUrl !== lastDeepScanUrl) return true;
    if (/force|snapshot|initial|domcontentloaded|delayed/i.test(String(reason || ''))) return true;
    return now - lastDeepScanAt > 5000;
  }

  function markDeepScan() {
    lastDeepScanUrl = String(location.href || '');
    lastDeepScanAt = Date.now();
  }

  function extractBalancedJson(text, startIndex) {
    var source = String(text || '');
    var start = Number(startIndex) || 0;
    var opener = source[start];
    var closer = opener === '[' ? ']' : '}';
    if (opener !== '{' && opener !== '[') return '';
    var depth = 0;
    var inString = false;
    var quote = '';
    var escaped = false;
    for (var i = start; i < source.length; i += 1) {
      var ch = source[i];
      if (inString) {
        if (escaped) {
          escaped = false;
        } else if (ch === '\\') {
          escaped = true;
        } else if (ch === quote) {
          inString = false;
          quote = '';
        }
        continue;
      }
      if (ch === '"' || ch === "'") {
        inString = true;
        quote = ch;
        continue;
      }
      if (ch === opener) depth += 1;
      if (ch === closer) {
        depth -= 1;
        if (depth === 0) return source.slice(start, i + 1);
      }
    }
    return '';
  }

  function inspectJsonFragments(text, source) {
    var value = String(text || '');
    if (!value || value.length > MAX_TEXT_LENGTH) return;
    var markers = [
      '__PRELOADED_STATE__',
      '__NEXT_DATA__',
      '__INITIAL_STATE__',
      '__APOLLO_STATE__',
      '__ML_STATE__',
      'initialState',
      'pageState',
      'itemState',
      'search',
      'results',
      'items'
    ];
    for (var mi = 0; mi < markers.length; mi += 1) {
      var marker = markers[mi];
      var from = 0;
      var guard = 0;
      while (guard < 4) {
        guard += 1;
        var idx = value.indexOf(marker, from);
        if (idx < 0) break;
        from = idx + marker.length;
        var openObj = value.indexOf('{', from);
        var openArr = value.indexOf('[', from);
        var open = -1;
        if (openObj >= 0 && openArr >= 0) open = Math.min(openObj, openArr);
        else open = openObj >= 0 ? openObj : openArr;
        if (open < 0 || open - idx > 2500) continue;
        var fragment = extractBalancedJson(value, open);
        if (!fragment || fragment.length > MAX_TEXT_LENGTH) continue;
        try {
          inspectObject(JSON.parse(fragment), source || ('script_fragment:' + marker));
        } catch (_jsonErr) {}
      }
    }
  }

  function inspectKnownGlobals(source) {
    [
      window.__PRELOADED_STATE__,
      window.__NEXT_DATA__,
      window.__NUXT__,
      window.__APOLLO_STATE__,
      window.__MELIDATA__,
      window.__ML_STATE__,
      window.__INITIAL_STATE__,
      window.pageState,
      window.__PAGE_STATE__,
      window.itemState,
      window.__ITEM_STATE__,
      window.productState,
      window.__PRODUCT_STATE__
    ].forEach(function (value, index) {
      try {
        if (value) inspectObject(value, (source || 'global') + ':' + index);
      } catch (_err) {}
    });
  }

  function inspectPageScripts(source) {
    var scripts = Array.prototype.slice.call(document.scripts || []);
    var scanned = 0;
    for (var i = 0; i < scripts.length; i += 1) {
      if (scanned >= 90) break;
      var script = scripts[i];
      var text = script && script.textContent ? script.textContent : '';
      if (!text || text.length > MAX_TEXT_LENGTH) continue;
      var type = String(script.type || '').toLowerCase();
      if (!/json|ld\+json|application\/javascript|text\/javascript|module/.test(type)
        && !/MLB-?\d{5,}|date_created|dateCreated|sold_quantity|soldQuantity|seller|results|items|__PRELOADED_STATE__|__NEXT_DATA__/i.test(text)) {
        continue;
      }
      scanned += 1;
      inspectText(text, (source || 'script') + ':' + scanned);
      inspectJsonFragments(text, (source || 'script_fragment') + ':' + scanned);
    }
  }

  function deepScanPageState(reason) {
    if (!shouldDeepScan(reason)) return;
    markDeepScan();
    inspectKnownGlobals('extensao_global');
    inspectPageScripts('extensao_script');
  }

  function inspectObject(root, source) {
    if (!root || typeof root !== 'object') return;
    var stack = [root];
    var seen = [];
    while (stack.length) {
      var cur = stack.pop();
      if (!cur || typeof cur !== 'object') continue;
      if (seen.indexOf(cur) >= 0) continue;
      seen.push(cur);
      if (seen.length > 7000) break;
      if (Array.isArray(cur)) {
        for (var ai = 0; ai < Math.min(cur.length, 500); ai += 1) stack.push(cur[ai]);
        continue;
      }
      mergeItem(cur, source);
      var keys = Object.keys(cur);
      for (var i = 0; i < Math.min(keys.length, 140); i += 1) {
        var value = cur[keys[i]];
        if (value && typeof value === 'object') stack.push(value);
      }
    }
    publish(source || 'object');
  }

  function inspectText(text, source) {
    var value = String(text || '');
    if (!value || value.length > MAX_TEXT_LENGTH) return;
    if (!/MLB-?\d{5,}|date_created|dateCreated|sold_quantity|soldQuantity|seller|results|items/i.test(value)) return;
    try {
      inspectObject(JSON.parse(value), source || 'json');
      return;
    } catch (_err) {}
    var ids = value.match(/MLB-?\d{5,}/ig) || [];
    ids.slice(0, 120).forEach(function (rawId, index) {
      var id = itemId(rawId);
      if (!id) return;
      var idx = value.toUpperCase().indexOf(rawId.toUpperCase());
      var area = idx >= 0 ? value.slice(Math.max(0, idx - 1200), idx + 2400) : value;
      mergeItem({
        posicao: index + 1,
        id: id,
        titulo: '',
        url: '',
        data_criacao: pickDateFromText(area),
        vendas: area
      }, source || 'texto');
    });
    publish(source || 'texto');
  }

  function inspectResponse(response, source) {
    if (!response || !response.clone) return;
    var url = String(response.url || '');
    if (!/mercadolivre|mercadolibre|api|items|search|products|pdp/i.test(url)) return;
    try {
      response.clone().text().then(function (text) {
        inspectText(text, source || 'fetch');
      }).catch(function () {});
    } catch (_err) {}
  }

  function patchFetch() {
    if (!window.fetch || window.fetch.__jkMlCollectorPatched) return;
    var originalFetch = window.fetch;
    var patched = function () {
      var args = arguments;
      var promise = originalFetch.apply(this, args);
      try {
        promise.then(function (response) {
          inspectResponse(response, 'fetch');
        }).catch(function () {});
      } catch (_err) {}
      return promise;
    };
    patched.__jkMlCollectorPatched = true;
    window.fetch = patched;
  }

  function patchXhr() {
    if (!window.XMLHttpRequest || window.XMLHttpRequest.prototype.__jkMlCollectorPatched) return;
    var proto = window.XMLHttpRequest.prototype;
    var originalOpen = proto.open;
    var originalSend = proto.send;
    proto.open = function (method, url) {
      this.__jkMlCollectorUrl = String(url || '');
      return originalOpen.apply(this, arguments);
    };
    proto.send = function () {
      try {
        this.addEventListener('load', function () {
          try {
            var url = String(this.__jkMlCollectorUrl || '');
            if (!/mercadolivre|mercadolibre|api|items|search|products|pdp/i.test(url)) return;
            inspectText(this.responseText || '', 'xhr');
          } catch (_err) {}
        });
      } catch (_err) {}
      return originalSend.apply(this, arguments);
    };
    proto.__jkMlCollectorPatched = true;
  }

  function scheduleCollect(reason) {
    if (scheduled) clearTimeout(scheduled);
    scheduled = setTimeout(function () {
      scheduled = 0;
      collectDom(reason || 'scheduled');
    }, 180);
  }

  function setupObserver() {
    if (!document.documentElement || !window.MutationObserver) return;
    try {
      var observer = new MutationObserver(function () {
        scheduleCollect('mutation');
      });
      observer.observe(document.documentElement, { childList: true, subtree: true });
    } catch (_err) {}
  }

  window.__JK_ML_COLLECTOR__ = {
    version: VERSION,
    getSnapshot: function () {
      collectDom('snapshot');
      return JSON.parse(JSON.stringify(state));
    },
    forceCollect: function (reason) {
      collectDom(reason || 'force');
      return JSON.parse(JSON.stringify(state));
    }
  };

  patchFetch();
  patchXhr();
  setupObserver();

  if (document.readyState === 'loading') {
    scheduleCollect('early-loading');
    document.addEventListener('DOMContentLoaded', function () {
      collectDom('domcontentloaded');
    }, { once: true });
  } else {
    collectDom('initial');
  }
  setTimeout(function () { collectDom('delayed-1'); }, 350);
  setTimeout(function () { collectDom('delayed-2'); }, 900);
  setTimeout(function () { collectDom('delayed-3'); }, 1800);
  setTimeout(function () { collectDom('delayed-4'); }, 3500);
  setTimeout(function () { collectDom('delayed-5'); }, 7000);
})();
