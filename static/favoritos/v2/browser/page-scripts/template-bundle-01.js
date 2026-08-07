(function (global) {
  'use strict';
  const pageScripts = global.FavoritosV2.browser.pageScripts;
  pageScripts.registerPart('legacy-webview-extract', 0, `
            (async function () {
                try {
                    var sleep = function (ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); };
                    var currentUrl = String(window.location.href || '');
                    var fastLinksOnly = !!window.__JK_ML_FAST_LINKS;
                    var queryAllDeep = function (selector, root) {
                        var found = [];
                        var visited = [];
                        var visit = function (base) {
                            if (!base || visited.indexOf(base) >= 0) return;
                            visited.push(base);
                            try {
                                if (base.querySelectorAll) {
                                    var nodes = Array.prototype.slice.call(base.querySelectorAll(selector));
                                    for (var n = 0; n < nodes.length; n += 1) {
                                        if (found.indexOf(nodes[n]) < 0) found.push(nodes[n]);
                                    }
                                    var all = Array.prototype.slice.call(base.querySelectorAll('*'));
                                    for (var i = 0; i < all.length; i += 1) {
                                        if (all[i] && all[i].shadowRoot) visit(all[i].shadowRoot);
                                    }
                                }
                            } catch (e) {}
                        };
                        visit(root || document);
                        return found;
                    };
                    var queryOneDeep = function (selector, root) {
                        var nodes = queryAllDeep(selector, root);
                        return nodes.length ? nodes[0] : null;
                    };
                    var normalizeSearchText = function (value) {
                        var text = String(value || '');
                        return text.normalize ? text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase() : text.toLowerCase();
                    };
                    var nodeSearchText = function (node) {
                        if (!node) return '';
                        var parts = [];
                        try {
                            parts.push(node.innerText || '');
                            parts.push(node.textContent || '');
                            if (node.value) parts.push(node.value);
                            if (node.getAttribute) {
                                parts.push(node.getAttribute('aria-label') || '');
                                parts.push(node.getAttribute('title') || '');
                                parts.push(node.getAttribute('placeholder') || '');
                                parts.push(node.getAttribute('class') || '');
                                parts.push(node.getAttribute('id') || '');
                            }
                        } catch (e) {}
                        return parts.join(' ');
                    };
                    var bodyText = (function () {
                        var parts = [String(document.body && (document.body.innerText || document.body.textContent) || '')];
                        var seen = Object.create(null);
                        try {
                            var nodes = queryAllDeep('*').slice(0, 1800);
                            for (var i = 0; i < nodes.length; i += 1) {
                                var text = nodeSearchText(nodes[i]).replace(/\\s+/g, ' ').trim();
                                if (!text || seen[text]) continue;
                                seen[text] = true;
                                parts.push(text);
                            }
                        } catch (e) {}
                        return parts.join(' ').replace(/\\s+/g, ' ').trim();
                    })();
                    var lowerText = bodyText.toLowerCase();
                    var needsLogin =
                        currentUrl.indexOf('/gz/account-verification') >= 0 ||
                        currentUrl.indexOf('/jms/mlb/lgz/login') >= 0 ||
                        lowerText.indexOf('para continuar, acesse sua conta') >= 0;
                    var plainText = normalizeSearchText(bodyText);
                    var noResults =
                        plainText.indexOf('nao encontramos resultados') >= 0 ||
                        plainText.indexOf('nao ha resultados') >= 0 ||
                        plainText.indexOf('sem resultados') >= 0 ||
                        plainText.indexOf('verifique a ortografia') >= 0;
                    var hasRealAvantData =
                        /vendas?\\s+do\\s+(?:produto|anuncio|item)|vendas?\\s+estimad|ritmo\\s+atual|visitas\\s+do\\s+anuncio|participacao\\b|faturamento\\s+do\\s+produto|nome\\s+do\\s+vendedor|anuncio\\s+(?:ganhador\\s+)?criado\\s+em|comissao\\b|localizacao\\s+do\\s+vendedor/.test(plainText) ||
                        !!queryOneDeep('.avantpro-product-info-row, .created-time-card');
                    var cardSelectorsParaLoginAvant = [
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
                        '[class*="andes-card"]',
                        '[class*="poly-card"]',
                        '[class*="ui-search-result"]',
                        '[class*="ui-search-layout__item"]',
                        '[class*="shops__layout-item"]',
                        '[data-testid*="item"]',
                        '[data-testid*="card"]',
                        '[data-testid*="result"]'
                    ].join(',');
                    var cardLoginPanelsAvant = queryAllDeep(cardSelectorsParaLoginAvant).filter(function (card) {
                        var cardBusca = normalizeSearchText(card && (card.innerText || card.textContent) || '');
                        return /avant\\s*pro|avantpro|avantprocloud/.test(cardBusca)
                            && /vincular\\s+(?:conta|agora)|conectar\\s+conta|fazer\\s+login|entrar\\s+no\\s+avant|comece\\s+a\\s+usar|liberar\\s+os\\s+recursos/.test(cardBusca);
                    }).length;
                    var needsAvantLoginGlobal =
                        /avant\\s*pro|avantpro|avantprocloud/.test(plainText) &&
                        /vincule\\s+o\\s+avantpro|vincular\\s+agora|vincular\\s+conta|comece\\s+a\\s+usar|entre\\s+na\\s+sua\\s+conta|liberar\\s+os\\s+recursos|iniciar\\s+sessao|insira\\s+suas\\s+credenciais|seu\\s+e-?mail|seu\\s+email|chame\\s+o\\s+suporte/.test(plainText) &&
                        !hasRealAvantData;
                    var needsAvantLoginCards = cardLoginPanelsAvant > 0 && !hasRealAvantData;
                    var needsAvantLogin = needsAvantLoginGlobal || needsAvantLoginCards;

                    var selectors = [
                        'li.ui-search-layout__item',
                        'div.ui-search-result__wrapper',
                        'div.ui-search-result',
                        'div.poly-card',
                        'section.poly-card',
                        'article.poly-card',
                        'article.ui-search-result',
                        '[data-testid="product-card"]',
                        '[data-testid="item-card"]',
                        '[data-testid*="product"]',
                        '[class*="product-card"]',
                        '[class*="andes-card"]',
                        '[class*="poly-card"]',
                        '[class*="ui-search-result"]',
                        '[class*="ui-search-layout__item"]',
                        '[class*="ui-search-layout"] > li',
                        '[class*="shops__layout-item"]',
                        '[class*="item__info"]',
                        '[class*="ui-search-gallery"]',
                        '[data-testid*="item"]',
                        '[data-testid*="card"]',
                        '[data-testid*="result"]',
                        'main ol > li',
                        'main ul > li'
                    ].join(',');

                    var hasProductSignal = function () {
                        if (queryOneDeep(selectors)) return true;
                        if (queryOneDeep('a[href*="MLB"], a[href*="/p/MLB"], a[href*="wid=MLB"], a[href*="item_id"]')) return true;
                        return false;
                    };

                    var maxRounds = fastLinksOnly ? 24 : 30;
                    for (var round = 0; round < maxRounds; round += 1) {
                        if (hasProductSignal()) break;
                        await sleep(250);
                    }
                    if (noResults
                        && !document.querySelectorAll('.ui-search-loading-screen, [class*="ui-search-loading-screen"], [class*="loading-screen"], .andes-progress-indicator-circular').length
                        && !hasProductSignal()) {
                        return {
                            success: true,
                            currentUrl: currentUrl,
                            needsLogin: needsLogin,
                            needsAvantLogin: needsAvantLogin,
                            hasAvantData: hasRealAvantData,
                            noResults: true,
                            total: 0,
                            anuncios: [],
                            debug: {
                                linkCount: document.links ? document.links.length : 0,
                                cardCount: 0,
                                title: document.title || '',
                                fastLinksOnly: fastLinksOnly
                            }
                        };
                    }

                    var isValidItemId = function (itemId) {
                        itemId = String(itemId || '').trim().toUpperCase().replace('-', '');
                        var match = itemId.match(/^MLB(\\d+)$/);
                        return !!(match && match[1] && match[1].length >= 8);
                    };

                    var isProductUrl = function (href) {
                        if (!href) return false;
                        href = cleanUrl(String(href));
                        var idMatch = href.match(/\\bMLB-?(\\d{6,})\\b/i);
                        if (idMatch && !isValidItemId('MLB' + idMatch[1])) return false;
                        var normalizedHref = href.toLowerCase();
                        if (normalizedHref.indexOf('mercadolivre.com.br') < 0 && normalizedHref.indexOf('/mlb') !== 0 && normalizedHref.indexOf('/p/mlb') !== 0 && normalizedHref.indexOf('/up/mlb') !== 0) return false;
                        if (/\\/(?:ajuda|ofertas|cupons|categorias|supermercado|moda|mercado-play|vender|contato|compras|favoritos|gz|jms|login|registration|cart|publicidade|navigation|perfil|stores?|loja)\\b/i.test(normalizedHref)) return false;
                        var hasExplicitItemSignal = (
                            href.indexOf('/MLB-') >= 0 ||
                            href.indexOf('/p/MLB') >= 0 ||
                            href.indexOf('/up/MLB') >= 0 ||
                            /\\/up\\/MLBU/i.test(href) ||
                            href.indexOf('pdp_filters=item_id%3AMLB') >= 0 ||
                            href.indexOf('pdp_filters=item_id:MLB') >= 0 ||
                            href.indexOf('wid=MLB') >= 0 ||
                            /\\bMLB-?\\d{6,}\\b/i.test(href)
                        );
                        if (/https?:\\/\\/lista\\.mercadolivre\\.com\\.br\\//i.test(href)) return hasExplicitItemSignal;
                        return hasExplicitItemSignal;
                    };

                    var cleanUrl = function (href) {
                        if (!href) return '';
                        href = String(href).split('#')[0].trim();
                        if (/^\\/\\//.test(href)) return 'https:' + href;
                        if (/^\\//.test(href)) return 'https://www.mercadolivre.com.br' + href;
                        return href;
                    };

                    var urlFromItemId = function (itemId) {
                        itemId = String(itemId || '').trim().toUpperCase().replace('-', '');
                        if (!isValidItemId(itemId)) return '';
                        return 'https://produto.mercadolivre.com.br/' + itemId.replace('MLB', 'MLB-');
                    };
                    var safeDecode = function (value) {
                        var text = String(value || '');
                        try {
                            return decodeURIComponent(text);
                        } catch (e) {
                            try { return decodeURI(text); } catch (e2) { return text; }
                        }
                    };

                    var extractItemId = function (href) {
                        if (!href) return '';
                        href = safeDecode(href);
                        var patterns = [
                            /[?&]wid=(MLB\\d+)/i,
                            /[?&]item_id=(MLB\\d+)/i,
                            /item_id:?(MLB\\d+)/i,
                            /item_id%3A(MLB\\d+)/i,
                            /\\/(MLB-?\\d+)/i,
                            /\\b(MLB-?\\d{6,})\\b/i
                        ];
                        for (var p = 0; p < patterns.length; p += 1) {
                            var match = href.match(patterns[p]);
                            if (match && match[1]) {
                                var candidate = match[1].replace('-', '').toUpperCase();
                                if (isValidItemId(candidate)) return candidate;
                            }
                        }
                        return '';
                    };
                    var extractItemIdFromNode = function (node, fallbackHref) {
                        var sources = [fallbackHref || ''];
                        try {
                            if (node) {
                                sources.push(node.getAttribute('data-item-id') || '');
                                sources.push(node.getAttribute('data-id') || '');
                                sources.push(node.getAttribute('id') || '');
                                sources.push(node.innerHTML || '');
                                var descendants = Array.prototype.slice.call(node.querySelectorAll('[id], [data-item-id], [data-id], [href]'));
                                for (var d = 0; d < descendants.length && d < 80; d += 1) {
                                    sources.push(descendants[d].getAttribute('data-item-id') || '');
                                    sources.push(descendants[d].getAttribute('data-id') || '');
                                    sources.push(descendants[d].getAttribute('id') || '');
                                    sources.push(descendants[d].getAttribute('href') || '');
                                }
                            }
                        } catch (e) {}
                        for (var s = 0; s < sources.length; s += 1) {
                            var id = extractItemId(sources[s]);
                            if (id) return id;
                        }
                        return '';
                    };
                    var findProductHrefInNode = function (node) {
                        if (!node) return '';
                        var sources = [];
                        try {
                            sources.push(node.href || '');
                            sources.push(node.getAttribute && node.getAttribute('href') || '');
                            sources.push(node.getAttribute && node.getAttribute('data-href') || '');
                            sources.push(node.getAttribute && node.getAttribute('data-url') || '');
                            sources.push(node.getAttribute && node.getAttribute('data-permalink') || '');
                            sources.push(node.getAttribute && node.getAttribute('data-item-id') || '');
                            sources.push(node.getAttribute && node.getAttribute('data-id') || '');
                            sources.push(node.getAttribute && node.getAttribute('id') || '');
                            var descendants = Array.prototype.slice.call(node.querySelectorAll('a[href], [href], [data-href], [data-url], [data-permalink], [data-item-id], [data-id], [id]'));
                            for (var d = 0; d < descendants.length && d < 80; d += 1) {
                                var child = descendants[d];
                                sources.push(child.href || '');
                                sources.push(child.getAttribute('href') || '');
                                sources.push(child.getAttribute('data-href') || '');
                                sources.push(child.getAttribute('data-url') || '');
                                sources.push(child.getAttribute('data-permalink') || '');
                                sources.push(child.getAttribute('data-item-id') || '');
                                sources.push(child.getAttribute('data-id') || '');
                                sources.push(child.getAttribute('id') || '');
                            }
                        } catch (e) {}
                        var idFound = '';
                        for (var s = 0; s < sources.length; s += 1) {
                            var value = String(sources[s] || '');
                            if (!idFound) idFound = extractItemId(value);
                            if (isProductUrl(value)) return cleanUrl(value);
                        }
                        try {
                            var html = String(node.outerHTML || '').slice(0, 16000)
                                .replace(/\\u002F/g, '/')
                                .replace(/\\\//g, '/')
                                .replace(/&amp;/g, '&');
                            var hrefMatch = html.match(/https?:\\/\\/(?:www\\.)?mercadolivre\\.com\\.br\\/[^"' <>\\s]*?(?:MLB-?\\d{6,}|\\/p\\/MLB\\d+|wid=MLB\\d+|item_id%3AMLB\\d+|item_id:MLB\\d+)[^"' <>\\s]*/i);
                            if (hrefMatch && hrefMatch[0]) return cleanUrl(hrefMatch[0]);
                            if (!idFound) idFound = extractItemId(html);
                        } catch (e) {}
                        return idFound ? urlFromItemId(idFound) : '';
                    };

                    var titleFrom = function (node) {
                        if (!node) return '';
                        var cleanTitle = function (value) {
                            return String(value || '').replace(/\\s+/g, ' ').trim();
                        };
                        var badTitle = function (value) {
                            var text = cleanTitle(value);
                            if (!text) return true;
                            var normalized = text
                                .normalize('NFD')
                                .replace(/[\\u0300-\\u036f]/g, '')
                                .toLowerCase()
                                .replace(/\\s+/g, ' ')
                                .trim();
                            if (/^(novo|usado|resultados|patrocinado|mais vendido|loja oficial)$/i.test(text)) return true;
                            if (/^(r\\$|frete|chegar|vendid[oa]s?|mercadolider|mercado lider|op[cç][oõ]es de compra|produto relacionado)/i.test(normalized)) return true;
                            if (/^(pecas de|lubrificantes|acessorios|categorias|condicao|tipo de envio|custo de envio|tempo de entrega)/i.test(normalized)) return true;
                            var words = text.split(/\\s+/).filter(Boolean);
                            var hasDigit = /\\d/.test(text);
                            var hasLower = /[a-záéíóúâêôãõç]/.test(text);
                            if (!hasDigit && !hasLower && words.length <= 3) return true;
                            return text.length < 10;
                        };
                        var candidates = [];
                        if (node.querySelectorAll) {
                            var selectors = [
                                'a.poly-component__title',
                                '.poly-component__title',
                                'h2.poly-component__title-wrapper a',
                                'h3.poly-component__title-wrapper a',
                                'a.ui-search-link',
                                '.ui-search-item__title',
                                '[class*="ui-search-item__title"]',
                                'a[href*="/MLB-"][title]',
                                'a[href*="/p/MLB"][title]',
                                'a[href*="wid=MLB"][title]'
                            ];
                            selectors.forEach(function (selector) {
                                Array.prototype.slice.call(node.querySelectorAll(selector)).forEach(function (el) {
                                    candidates.push(el.textContent || '');
                                    candidates.push(el.getAttribute && el.getAttribute('title') || '');
                                    candidates.push(el.getAttribute && el.getAttribute('aria-label') || '');
                                });
                            });
                        }
                        if (node.textContent) {
                            candidates = candidates.concat(String(node.textContent).split('\\n'));
                        }
                        for (var i = 0; i < candidates.length; i += 1) {
                            var txt = cleanTitle(candidates[i]);
                            if (!badTitle(txt)) return txt.slice(0, 240);
                        }
                        return '';
                    };
                    var normalizeListingTitle = function (value) {
                        return String(value || '')
                            .normalize('NFD')
                            .replace(/[\\u0300-\\u036f]/g, '')
                            .toLowerCase()
                            .replace(/\\s+/g, ' ')
                            .trim();
                    };
                    var shouldSkipListingCandidate = function (node, title) {
                        var normalized = normalizeListingTitle(title);
                        if (!normalized) return true;
                        if (normalized === 'resultados') return true;
                        var categoryTitles = {
                            'pecas de motos e quadriciclos': true,
                            'lubrificantes e fluidos': true,
                            'pecas de linha pesada': true,
                            'pecas de carros e caminhonetes': true,
                            'acessorios de motos e quadriciclos': true,
                            'categorias': true,
                            'condicao': true,
                            'tipo de envio': true,
                            'custo de envio': true,
                            'tempo de entrega': true
                        };
                        if (!categoryTitles[normalized]) return false;
                        var text = String(node && (node.innerText || node.textContent) ? (node.innerText || node.textContent) : '');
                        return !/(R\\$|vendid[oa]s?|frete\\s+gr[aá]tis|avantpro|carregar\\s+dados)/i.test(text);
                    };
                    var parseHumanNumber = function (value, suffix) {
                        if (value === null || value === undefined) return null;
                        var raw = String(value).trim().toLowerCase();
                        if (!raw) return null;
                        var normalized = raw.replace(/\\s+/g, '');
                        if (normalized.indexOf('.') >= 0 && normalized.indexOf(',') >= 0) {
                            normalized = normalized.lastIndexOf('.') > normalized.lastIndexOf(',')
                                ? normalized.replace(/,/g, '')
                                : normalized.replace(/\\./g, '').replace(/,/g, '.');
                        } else if (normalized.indexOf(',') >= 0) {
                            normalized = /^\\d{1,3}(?:,\\d{3})+$/.test(normalized)
                                ? normalized.replace(/,/g, '')
                                : normalized.replace(/,/g, '.');
                        } else if (normalized.indexOf('.') >= 0) {
                            normalized = /^\\d{1,3}(?:\\.\\d{3})+$/.test(normalized)
                                ? normalized.replace(/\\./g, '')
                                : normalized;
                        }
                        var parsed = parseFloat(normalized);
                        if (!isFinite(parsed)) return null;
                        suffix = String(suffix || '').toLowerCase();
                        if (suffix === 'mil' || suffix === 'k') parsed *= 1000;
                        return Math.round(parsed);
                    };
                    var looksLikeAvantText = function (text) {
                        return /an[uú]ncio\\s+(?:ganhador\\s+)?criado\\s+em|cat[aá]logo\\s+criado\\s+em|nome\\s+do\\s+vendedor|vendid[oa]s?|\\bvendas\\b|faturamento\\s+do\\s+produto|reputa[cç][aã]o\\s+do\\s+vendedor/i.test(String(text || ''));
                    };
                    var normalizeAvantSearchText = function (value) {
                        return String(value || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase();
                    };
                    looksLikeAvantText = function (text) {
                        var value = normalizeAvantSearchText(text);
                        return /anuncio\\s+(?:ganhador\\s+)?criado\\s+em|catalogo\\s+criado\\s+em|nome\\s+do\\s+vendedor|vendas?\\s+do\\s+(?:produto|catalogo|anuncio)|vendas?\\s+estimad|ritmo\\s+atual|visitas\\s+do\\s+anuncio|participacao\\b|faturamento\\s+do\\s+produto|reputacao\\s+do\\s+vendedor/i.test(value);
                    };
                    var getNearbyInfoNodes = function (node) {
                        var found = [];
                        try {
                            var rect = node.getBoundingClientRect();
                            var selectorsInfo = [
                                '[class*="avant"]',
                                '[id*="avant"]',
                                '[class*="Avant"]',
                                '[id*="Avant"]',
                                '[data-testid*="avant"]',
                                '[class*="product-info"]',
                                '[class*="info-row"]',
                                '[class*="metric"]',
                                '[class*="detail"]',
                                '.created-time-card',
                                '.avantpro-product-info-row',
                                '.avantpro-product-info-row *',
                                'section',
                                'article',
                                'li',
                                'div',
                                'span',
                                'p'
                            ].join(',');
                            var candidates = queryAllDeep(selectorsInfo).slice(0, 5000);
                            var seenNodes = [];
                            for (var nb = 0; nb < candidates.length; nb += 1) {
                                var candidate = candidates[nb];
                                if (!candidate || candidate === node || node.contains(candidate)) continue;
                                var text = String(candidate.innerText || candidate.textContent || '').replace(/\\s+/g, ' ').trim();
                                if (!text || text.length > 1400 || !looksLikeAvantText(text)) continue;
                                var nr = candidate.getBoundingClientRect();
                                if (!nr.width || !nr.height) continue;
                                var overlapX = Math.max(0, Math.min(rect.right, nr.right) - Math.max(rect.left, nr.left));
                                var cardCenterY = (rect.top + rect.bottom) / 2;
                                var nodeCenterY = (nr.top + nr.bottom) / 2;
                                var nearY = Math.abs(nodeCenterY - cardCenterY) < Math.max(560, rect.height * 1.25);
                                var sameColumn = overlapX > Math.max(20, Math.min(rect.width, nr.width) * 0.12);
                                var cardContainsOverlay = nr.left >= rect.left - 45 && nr.right <= rect.right + 45 && nr.top >= rect.top - 100 && nr.top <= rect.bottom + 460;
                                if ((sameColumn && nearY) || cardContainsOverlay) {
                                    var duplicate = seenNodes.some(function (existing) { return existing.contains(candidate); });
                                    if (duplicate) continue;
                                    seenNodes.push(candidate);
                                    found.push(text);
                                }
                            }
                        } catch (e) {}
                        return found;
                    };
                    var textWithNearbyAvant = function (node) {
                        var parts = [String(node && node.innerText ? node.innerText : '')];
                        try {
                            parts = parts.concat(getNearbyInfoNodes(node));
                        } catch (e) {}
                        return parts.join(' ').replace(/\\s+/g, ' ').trim();
                    };
                    var normalizeAvantLabel = function (value) {
                        return String(value || '')
                            .normalize('NFD')
                            .replace(/[\\u0300-\\u036f]/g, '')
                            .toLowerCase()
                            .replace(/\\s+/g, ' ')
                            .trim();
                    };
                    var canonicalAvantLabel = function (value) {
                        return normalizeAvantLabel(value)
                            .replace(/[^a-z0-9]+/g, ' ')
                            .replace(/\\bvendas?\\b/g, 'venda')
                            .replace(/\\banuncios?\\b/g, 'anuncio')
                            .replace(/\\bitens?\\b/g, 'item')
                            .replace(/\\s+/g, ' ')
                            .trim();
                    };
                    var avantLabelMatches = function (label, wantedLabels) {
                        var labelText = normalizeAvantLabel(label).replace(/[:=\\-]+$/g, '').trim();
                        var labelKey = canonicalAvantLabel(labelText);
                        return wantedLabels.some(function (wanted) {
                            var wantedText = normalizeAvantLabel(wanted).replace(/[:=\\-]+$/g, '').trim();
                            var wantedKey = canonicalAvantLabel(wantedText);
                            return labelText === wantedText
                                || labelKey === wantedKey
                                || labelText.indexOf(wantedText + ' ') === 0
                                || labelKey.indexOf(wantedKey + ' ') === 0;
                        });
                    };
                    var isNearAvantCard = function (card, row) {
                        try {
                            var rect = card.getBoundingClientRect();
                            var rr = row.getBoundingClientRect();
                            if (!rr.width || !rr.height) return false;
                            var overlapX = Math.max(0, Math.min(rect.right, rr.right) - Math.max(rect.left, rr.left));
                            var cardCenterY = (rect.top + rect.bottom) / 2;
                            var rowCenterY = (rr.top + rr.bottom) / 2;
                            var nearY = Math.abs(rowCenterY - cardCenterY) < Math.max(420, rect.height * 1.15);
                            var sameColumn = overlapX > Math.max(20, Math.min(rect.width, rr.width) * 0.12);
                            var insideOverlay = rr.left >= rect.left - 60 && rr.right <= rect.right + 60 && rr.top >= rect.top - 80 && rr.top <= rect.bottom + 460;
                            return (sameColumn && nearY) || insideOverlay;
                        } catch (e) {
                            return false;
                        }
                    };
                    var extractValueFromTextByLabels = function (text, labels) {
                        var original = String(text || '').replace(/\\s+/g, ' ').trim();
                        if (!original) return '';
                        var normalized = normalizeAvantLabel(original);
                        for (var i = 0; i < labels.length; i += 1) {
                            var labelOriginal = String(labels[i] || '').replace(/\\s+/g, ' ').trim();
                            var label = normalizeAvantLabel(labelOriginal).replace(/[:=\\-]+$/g, '').trim();
                            if (!label) continue;
                            var idx = normalized.indexOf(label);
                            if (idx < 0) continue;
                            var afterOriginal = original.slice(Math.min(original.length, idx + labelOriginal.length));
                            afterOriginal = afterOriginal.replace(/^[\\s:=\\-]+/, '').trim();
                            if (!afterOriginal) continue;
                            var stop = afterOriginal.search(/\\b(?:marca|vendas?\\s+do|vendas?\\s+estimad|participa[cç][aã]o|ritmo\\s+atual|visitas\\s+do|nome\\s+do\\s+vendedor|localiza[cç][aã]o|comiss[aã]o|reputa[cç][aã]o|an[uú]ncio\\s+criado)\\b/i);
                            if (stop > 0) afterOriginal = afterOriginal.slice(0, stop).trim();
                            return afterOriginal.slice(0, 160).trim();
                        }
                        return '';
                    };
                    var avantInfoRowSelectors = [
                        '.avantpro-product-info-row',
                        '[class*="avant"][class*="row"]',
                        '[class*="Avant"][class*="row"]',
                        '[class*="product-info"]',
                        '[class*="info-row"]',
                        '[class*="metric"]',
                        '[class*="detail"]',
                        '[data-testid*="avant"]',
                        'li',
                        'div'
                    ].join(',');
                    var extractAvantRowValue = function (card, labels) {
                        var rows = queryAllDeep(avantInfoRowSelectors).slice(0, 5000);
                        for (var r = 0; r < rows.length; r += 1) {
                            var row = rows[r];
                            if (!isNearAvantCard(card, row)) continue;
                            var labelNode = queryOneDeep('.avantpro-product-info-row-label', row) || row.querySelector('.avantpro-product-info-row-label');
                            var valueNode = queryOneDeep('.avantpro-product-info-row-value', row) || row.querySelector('.avantpro-product-info-row-value');
                            var label = normalizeAvantLabel(labelNode && labelNode.textContent);
                            if (label && avantLabelMatches(label, labels) && valueNode) {
                                return String(valueNode.textContent || '').replace(/\\s+/g, ' ').trim();
                            }
                            var rowText = String(row && (row.innerText || row.textContent) || '').replace(/\\s+/g, ' ').trim();
                            if (!looksLikeAvantText(rowText)) continue;
                            var parsed = extractValueFromTextByLabels(rowText, labels);
                            if (parsed) return parsed;
                        }
                        return '';
                    };
                    var extractAvantRowInfo = function (card, labels) {
                        var rows = queryAllDeep(avantInfoRowSelectors).slice(0, 5000);
                        for (var r = 0; r < rows.length; r += 1) {
                            var row = rows[r];
                            if (!isNearAvantCard(card, row)) continue;
                            var labelNode = queryOneDeep('.avantpro-product-info-row-label', row) || row.querySelector('.avantpro-product-info-row-label');
                            var valueNode = queryOneDeep('.avantpro-product-info-row-value', row) || row.querySelector('.avantpro-product-info-row-value');
                            var label = normalizeAvantLabel(labelNode && labelNode.textContent);
                            if (label && avantLabelMatches(label, labels) && valueNode) {
                                return {
                                    label: label,
                                    value: String(valueNode.textContent || '').replace(/\\s+/g, ' ').trim()
                                };
                            }
                            var rowText = String(row && (row.innerText || row.textContent) || '').replace(/\\s+/g, ' ').trim();
                            if (!looksLikeAvantText(rowText)) continue;
                            var parsed = extractValueFromTextByLabels(rowText, labels);
                            if (parsed) {
                                return {
                                    label: normalizeAvantLabel(labels[0] || ''),
                                    value: parsed
                                };
                            }
                        }
                        return null;
                    };
                    var sanitizeAvantSeller = function (value) {
                        var seller = String(value || '').replace(/\\s+/g, ' ').trim();
                        seller = seller.replace(/^(?:nome\\s+do\\s+vendedor|vendedor|loja\\s+oficial|vendido\\s+por|atual\\s+ganhador|ganhador)\\s*[:\\-]?\\s*/i, '').trim();
                        if (!seller || seller.length > 120 || /^\\d+$/.test(seller)) return '';
                        if (/^(?:sim|nao|n[aã]o|nao\\s+informado|n[aã]o\\s+informado|carregando|indisponivel|indispon[ií]vel|assinantes?)$/i.test(seller)) return '';
                        return seller;
                    };
                    var extractSeller = function (node) {
                        var exactSeller = extractAvantRowValue(node, [
                            'Nome do vendedor',
                            'Vendedor',
                            'Vendedor do anuncio',
                            'Vendedor do anúncio',
                            'Nome do vendedor ganhador',
                            'Vendedor ganhador',
                            'Loja oficial'
                        ]);
                        return sanitizeAvantSeller(exactSeller);
                    };
                    var extractAvantDate = function (node) {`);
})(window);
