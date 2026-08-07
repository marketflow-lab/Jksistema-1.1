(function (global) {
  'use strict';
  const pageScripts = global.FavoritosV2.browser.pageScripts;
  pageScripts.registerPart('capturar-avant-pro-cards-visiveis-rapido-webview-1', 0, `
                (function () {
                    var limite = __JK_CAPTURAR_AVANT_PRO_CARDS_VISIVEIS_RAPIDO_WEBVIEW_1_P0__;
                    var normalizar = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                        return text.toLowerCase();
                    };
                    var visible = function (node) {
                        try {
                            var rect = node && node.getBoundingClientRect ? node.getBoundingClientRect() : null;
                            var style = node && window.getComputedStyle ? window.getComputedStyle(node) : null;
                            var vw = window.innerWidth || document.documentElement.clientWidth || 1280;
                            var vh = window.innerHeight || document.documentElement.clientHeight || 900;
                            return !!(rect && rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0 && rect.top < vh && rect.left < vw && (!style || (style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) !== 0)));
                        } catch (_err) {
                            return false;
                        }
                    };
                    var idDe = function (value) {
                        var text = String(value || '');
                        try { text = decodeURIComponent(text); } catch (_err) {}
                        var match = text.match(/\\bMLB-?(\\d{6,})\\b/i) || text.match(/[?&](?:wid|item_id)=(MLB\\d{6,})/i);
                        if (!match) return '';
                        var raw = String(match[1] || '').replace('-', '').toUpperCase();
                        return raw.indexOf('MLB') === 0 ? raw : ('MLB' + raw);
                    };
                    var cleanUrl = function (href, id) {
                        href = String(href || '').split('#')[0].trim();
                        if (/^\\/\\//.test(href)) href = 'https:' + href;
                        if (/^\\//.test(href)) href = 'https://www.mercadolivre.com.br' + href;
                        if (!href && id) return 'https://produto.mercadolivre.com.br/' + String(id).replace('MLB', 'MLB-');
                        return href;
                    };
                    var numeroHumano = function (value, decimal) {
                        var raw = String(value || '').trim().toLowerCase();
                        if (!raw) return null;
                        var suffix = /\\b(mil|k)\\b/i.test(raw) ? 'k' : '';
                        var n = raw.replace(/[^0-9,.-]/g, '');
                        if (n.indexOf('.') >= 0 && n.indexOf(',') >= 0) {
                            n = n.lastIndexOf('.') > n.lastIndexOf(',') ? n.replace(/,/g, '') : n.replace(/\\./g, '').replace(/,/g, '.');
                        } else if (n.indexOf(',') >= 0) {
                            n = n.replace(/\\./g, '').replace(/,/g, '.');
                        }
                        var parsed = parseFloat(n);
                        if (!isFinite(parsed)) return null;
                        if (suffix) parsed *= 1000;
                        return decimal ? parsed : Math.round(parsed);
                    };
                    var labelValue = function (row) {
                        var labelNode = row && row.querySelector && row.querySelector('.avantpro-product-info-row-label, [class*="label"]');
                        var valueNode = row && row.querySelector && row.querySelector('.avantpro-product-info-row-value, [class*="value"]');
                        var label = String(labelNode && (labelNode.innerText || labelNode.textContent) || '').replace(/\\s+/g, ' ').trim();
                        var value = String(valueNode && (valueNode.innerText || valueNode.textContent) || '').replace(/\\s+/g, ' ').trim();
                        var text = String(row && (row.innerText || row.textContent) || '').replace(/\\s+/g, ' ').trim();
                        if (!value && label && text.indexOf(label) >= 0) value = text.slice(text.indexOf(label) + label.length).replace(/^\\s*[:\\-]?\\s*/, '').trim();
                        return { label: label, value: value, text: text, busca: normalizar(label + ' ' + text) };
                    };
                    var preencher = function (item, row) {
                        var lv = labelValue(row);
                        var busca = lv.busca;
                        var value = lv.value || lv.text;
                        if (/vendas?\\s+do\\s+(produto|anuncio|item)|vendas?\\s+estimad/.test(busca)) {
                            var vendas = numeroHumano(value, false);
                            if (Number.isFinite(vendas)) item.vendas = vendas;
                        } else if (/ritmo\\s+atual|vendas?\\s*\\/\\s*mes|media\\s+mensal/.test(busca)) {
                            var ritmo = numeroHumano(value, true);
                            if (Number.isFinite(ritmo)) {
                                item.media_mensal = ritmo;
                                item.ritmo_atual = ritmo;
                            }
                        } else if (/nome\\s+do\\s+vendedor/.test(busca) || (/\\bvendedor\\b/.test(busca) && !/reputacao|localizacao/.test(busca))) {
                            var vendedor = String(lv.value || '').replace(/^\\s*[:\\-]?\\s*/, '').trim();
                            if (vendedor && vendedor.length <= 120 && !/^\\d+$/.test(vendedor)) item.vendedor = vendedor;
                        } else if (/anuncio\\s+criado\\s+em|catalogo\\s+criado\\s+em|criado\\s+em/.test(busca)) {
                            var data = String(value || '').match(/\\b\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}\\b|\\b20\\d{2}-\\d{2}-\\d{2}\\b/);
                            if (data && data[0]) item.data_criacao = data[0];
                        } else if (/visitas?\\s+do\\s+anuncio|\\bvisitas?\\b/.test(busca)) {
                            var visitas = numeroHumano(value, false);
                            if (Number.isFinite(visitas)) item.visitas = visitas;
                        } else if (/participacao/.test(busca)) {
                            item.participacao = lv.value || '';
                        } else if (/comissao/.test(busca)) {
                            item.comissao = lv.value || '';
                        } else if (/reputacao\\s+do\\s+vendedor/.test(busca)) {
                            item.reputacao_vendedor = lv.value || '';
                        } else if (/localizacao\\s+do\\s+vendedor/.test(busca)) {
                            item.localizacao_vendedor = lv.value || '';
                        }
                    };
                    var rowSelector = '.avantpro-product-info-row, .created-time-card, [class*="avantpro-product-info"], [class*="created-time-card"], [class*="Avantpro"][class*="row"], [class*="avantpro"][class*="row"]';
                    var primeiraLinhaAvant = null;
                    try { primeiraLinhaAvant = document.querySelector('.avantpro-product-info-row, .created-time-card'); } catch (_rowExactErr) {}
                    if (!primeiraLinhaAvant) {
                        try { primeiraLinhaAvant = document.querySelector('[class*="avantpro-product-info"], [class*="created-time-card"]'); } catch (_rowFallbackErr) {}
                    }
                    if (!primeiraLinhaAvant) return { success: true, total: 0, anuncios: [] };
                    var cardSelectors = [
                        'li.ui-search-layout__item',
                        'div.ui-search-result__wrapper',
                        'div.ui-search-result',
                        'div.poly-card',
                        'section.poly-card',
                        'article.poly-card',
                        'article.ui-search-result',
                        '[data-testid="product-card"]',
                        '[data-testid="item-card"]',
                        '[class*="product-card"]',
                        '[class*="poly-card"]',
                        '[class*="shops__layout-item"]',
                        'main ol > li',
                        'main ul > li'
                    ].join(',');
                    var cards = [];
                    try { cards = Array.prototype.slice.call(document.querySelectorAll(cardSelectors)); } catch (_cardsErr) {}
                    var anuncios = [];
                    cards.filter(visible).forEach(function (card) {
                        if (anuncios.length >= limite) return;
                        var rows = [];
                        try { rows = Array.prototype.slice.call(card.querySelectorAll(rowSelector)); } catch (_rowsErr) {}
                        rows = rows.filter(function (row) {
                            var text = normalizar(String(row && (row.innerText || row.textContent) || ''));
                            return /vendas?|ritmo|vendedor|criado|visitas|participa|comissao|reputacao/.test(text);
                        });
                        if (!rows.length) return;
                        var href = '';
                        try {
                            href = Array.prototype.slice.call(card.querySelectorAll('a[href]'))
                                .map(function (a) { return a.href || a.getAttribute('href') || ''; })
                                .filter(function (link) { return /\\bMLB-?\\d{6,}\\b|[?&](?:wid|item_id)=MLB\\d{6,}|\\/p\\/MLB|\\/up\\/MLB|produto\\.mercadolivre\\.com\\.br/i.test(link); })[0] || '';
                        } catch (_hrefErr) {}
                        var id = idDe(href);
                        href = cleanUrl(href, id);
                        var canonical = id ? ('mlb:' + id) : (href ? ('link:' + href.toLowerCase()) : '');
                        if (!canonical) return;
                        var item = {
                            id: id,
                            mlb: id,
                            url: href,
                            permalink: href,
                            link: href,
                            chave_canonica: canonical,
                            chaveCanonica: canonical,
                            origem_dados: 'avantpro_dom_visivel'
                        };
                        rows.forEach(function (row) { preencher(item, row); });
                        var temDados = item.vendas !== null && item.vendas !== undefined && item.vendas !== ''
                            || item.media_mensal !== null && item.media_mensal !== undefined && item.media_mensal !== ''
                            || item.vendedor
                            || item.data_criacao
                            || item.visitas !== null && item.visitas !== undefined && item.visitas !== '';
                        if (!temDados) return;
                        if (item.vendas !== null && item.vendas !== undefined && item.vendas !== '') {
                            item.vendasFonte = 'avantpro_dom';
                            item.vendas_fonte = 'avantpro_dom';
                        }
                        if (item.vendedor) {
                            item.vendedorFonte = 'avantpro_dom';
                            item.vendedor_fonte = 'avantpro_dom';
                        }
                        if (item.data_criacao) {
                            item.dataCriacaoFonte = 'avantpro_dom';
                            item.data_criacao_fonte = 'avantpro_dom';
                        }
                        if (item.media_mensal !== null && item.media_mensal !== undefined && item.media_mensal !== '') {
                            item.media_mensal_fonte = 'avantpro_dom';
                        }
                        anuncios.push(item);
                    });
                    var cache = window.__JK_AVANT_CARD_DATA_CACHE || {};
                    anuncios.forEach(function (item) {
                        cache[item.chave_canonica] = Object.assign({}, cache[item.chave_canonica] || {}, item);
                        var incremental = window.__JK_FAVORITOS_INCREMENTAL_COLLECTOR_V1;
                        if (incremental && incremental.resolvedKeys) {
                            incremental.resolvedKeys[item.chave_canonica] = Date.now();
                            if (incremental.pendingKeys) delete incremental.pendingKeys[item.chave_canonica];
                        }
                    });
                    window.__JK_AVANT_CARD_DATA_CACHE = cache;
                    return { success: true, total: anuncios.length, anuncios: anuncios };
                })();
            `);
  pageScripts.registerPart('obter-metrica-rolagem-mercado-livre-favoritos-1', 0, `
                (function () {
                    var candidatos = [document.scrollingElement, document.documentElement, document.body]
                        .concat(Array.prototype.slice.call(document.querySelectorAll('main, section, div, ol, ul')));
                    var melhor = null;
                    var melhorDelta = 0;
                    candidatos.forEach(function (node) {
                        if (!node) return;
                        var delta = Math.max(0, Number(node.scrollHeight || 0) - Number(node.clientHeight || 0));
                        if (delta > melhorDelta) {
                            melhor = node;
                            melhorDelta = delta;
                        }
                    });
                    var doc = melhor || document.scrollingElement || document.documentElement || document.body;
                    var y = Math.max(window.scrollY || 0, (doc && doc.scrollTop) || 0);
                    var height = Math.max(
                        (doc && doc.scrollHeight) || 0,
                        document.documentElement ? document.documentElement.scrollHeight || 0 : 0,
                        document.body ? document.body.scrollHeight || 0 : 0
                    );
                    var view = Math.max(
                        window.innerHeight || 0,
                        (doc && doc.clientHeight) || 0,
                        document.documentElement ? document.documentElement.clientHeight || 0 : 0,
                        800
                    );
                    var rootsResultados = Array.prototype.slice.call(document.querySelectorAll(
                        'ol.ui-search-layout, ul.ui-search-layout, main .ui-search-layout, [class*="ui-search-layout"][class*="results"]'
                    ));
                    var cardsResultados = Array.prototype.slice.call(document.querySelectorAll(
                        'li.ui-search-layout__item, div.ui-search-result__wrapper, article.ui-search-result, div.poly-card, article.poly-card'
                    ));
                    var alvosResultados = rootsResultados.length ? rootsResultados : cardsResultados;
                    var fimResultados = alvosResultados.reduce(function (maior, node) {
                        if (!node || !node.getBoundingClientRect) return maior;
                        var rect = node.getBoundingClientRect();
                        if (!rect || rect.height <= 0 || rect.width <= 0) return maior;
                        return Math.max(maior, y + rect.bottom);
                    }, 0);
                    return { y: y, height: height, view: view, resultsBottom: fimResultados, atBottom: y + view >= height - 48 };
                })();
            `);
  pageScripts.registerPart('rolar-mercado-livre-favoritos-1', 0, `
                (function () {
                    var doc = document.scrollingElement || document.documentElement || document.body;
                    window.scrollTo(0, __JK_ROLAR_MERCADO_LIVRE_FAVORITOS_1_P0__);
                    if (doc) doc.scrollTop = __JK_ROLAR_MERCADO_LIVRE_FAVORITOS_1_P1__;
                    try {
                        window.dispatchEvent(new WheelEvent('wheel', {
                            deltaY: __JK_ROLAR_MERCADO_LIVRE_FAVORITOS_1_P2__,
                            bubbles: true,
                            cancelable: true
                        }));
                    } catch (_wheelErr) {}
                    return {
                        y: Math.max(window.scrollY || 0, (doc && doc.scrollTop) || 0),
                        height: Math.max(
                            (doc && doc.scrollHeight) || 0,
                            document.documentElement ? document.documentElement.scrollHeight || 0 : 0,
                            document.body ? document.body.scrollHeight || 0 : 0
                        ),
                        view: Math.max(window.innerHeight || 0, (doc && doc.clientHeight) || 0, 800)
                    };
                })();
            `);
})(window);
