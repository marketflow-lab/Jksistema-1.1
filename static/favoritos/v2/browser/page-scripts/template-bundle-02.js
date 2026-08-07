(function (global) {
  'use strict';
  const pageScripts = global.FavoritosV2.browser.pageScripts;
  pageScripts.registerPart('legacy-webview-extract', 1, `                        if (fastLinksOnly) return '';
                        var exactDate = extractAvantRowValue(node, ['Anúncio criado em', 'Anuncio criado em', 'Anúncio ganhador criado em', 'Anuncio ganhador criado em']);
                        if (exactDate) {
                            var exactBr = exactDate.match(/\\b\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}\\b/);
                            if (exactBr && exactBr[0]) return exactBr[0];
                            var exactIso = exactDate.match(/\\b20\\d{2}-\\d{2}-\\d{2}(?:T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?(?:Z|[+-]\\d{2}:?\\d{2})?)?\\b/);
                            if (exactIso && exactIso[0]) return exactIso[0];
                        }
                        var text = textWithNearbyAvant(node);
                        var labels = [
                            'Anúncio criado em',
                            'Anuncio criado em',
                            'Anúncio ganhador criado em',
                            'Anuncio ganhador criado em',
                            'Catálogo criado em',
                            'Catalogo criado em',
                            'Criado em'
                        ];
                        for (var l = 0; l < labels.length; l += 1) {
                            var idx = text.toLowerCase().indexOf(labels[l].toLowerCase());
                            if (idx < 0) continue;
                            var trecho = text.slice(idx + labels[l].length, idx + labels[l].length + 120);
                            var br = trecho.match(/\\b\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}\\b/);
                            if (br && br[0]) return br[0];
                            var iso = trecho.match(/\\b20\\d{2}-\\d{2}-\\d{2}(?:T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?(?:Z|[+-]\\d{2}:?\\d{2})?)?\\b/);
                            if (iso && iso[0]) return iso[0];
                        }
                        return '';
                    };
                    var extractVendas = function (node) {
                        if (fastLinksOnly) return null;
                        var labelEhVendasAnuncio = function (value) {
                            var label = canonicalAvantLabel(value).replace(/[:=\\-]+$/g, '').trim();
                            return (
                                /^venda\\s+do\\s+(?:anuncio|item)(?:\\s+ganhador)?\\b/.test(label) ||
                                /^venda\\s+do\\s+produto\\b/.test(label) ||
                                /^venda\\s+estimad/.test(label) ||
                                /^venda\\s+deste\\s+anuncio\\b/.test(label) ||
                                /^venda\\s+do\\s+vendedor\\s+neste\\s+anuncio\\b/.test(label)
                            );
                        };
                        var parseValorVendas = function (value) {
                            var match = String(value || '').match(/(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i);
                            return match && match[1] ? parseHumanNumber(match[1], match[2]) : null;
                        };
                        var scoreLinhaVendas = function (row) {
                            try {
                                var rect = node.getBoundingClientRect();
                                var rr = row.getBoundingClientRect();
                                if (!rr.width || !rr.height) return null;
                                var overlapX = Math.max(0, Math.min(rect.right, rr.right) - Math.max(rect.left, rr.left));
                                var cardCenterY = (rect.top + rect.bottom) / 2;
                                var rowCenterY = (rr.top + rr.bottom) / 2;
                                var distanceY = Math.abs(rowCenterY - cardCenterY);
                                if (isNearAvantCard(node, row)) return distanceY - Math.min(overlapX, rect.width) * 0.05;
                                var alignedX = overlapX > Math.max(14, Math.min(rect.width, rr.width) * 0.08)
                                    || (rr.left <= rect.right + 140 && rr.right >= rect.left - 140);
                                var closeY = rr.top >= rect.top - 80 && rr.top <= rect.bottom + Math.max(420, rect.height * 1.5);
                                if (!alignedX || !closeY || distanceY > Math.max(520, rect.height * 1.9)) return null;
                                return 1000 + distanceY - Math.min(overlapX, rect.width) * 0.04;
                            } catch (e) {
                                return null;
                            }
                        };
                        var rows = queryAllDeep('.avantpro-product-info-row');
                        var melhor = null;
                        for (var r = 0; r < rows.length; r += 1) {
                            var row = rows[r];
                            var score = scoreLinhaVendas(row);
                            if (!Number.isFinite(score)) continue;
                            var labelNode = queryOneDeep('.avantpro-product-info-row-label', row) || row.querySelector('.avantpro-product-info-row-label');
                            var valueNode = queryOneDeep('.avantpro-product-info-row-value', row) || row.querySelector('.avantpro-product-info-row-value');
                            if (!valueNode || !labelEhVendasAnuncio(labelNode && labelNode.textContent)) continue;
                            var valor = parseValorVendas(valueNode.textContent);
                            if (Number.isFinite(valor) && (!melhor || score < melhor.score)) {
                                melhor = { valor: valor, score: score };
                            }
                        }
                        return melhor ? melhor.valor : null;
                    };
                    var extractVendasTextoSimples = function (node) {
                        if (fastLinksOnly) return null;
                        var text = textWithNearbyAvant(node)
                            .normalize('NFD')
                            .replace(/[\\u0300-\\u036f]/g, '')
                            .replace(/\\s+/g, ' ')
                            .trim();
                        try {
                            var rows = queryAllDeep('.avantpro-product-info-row, [class*="avantpro"], [class*="Avantpro"], [class*="product-info"]');
                            var extras = [];
                            for (var r = 0; r < rows.length && extras.length < 80; r += 1) {
                                var row = rows[r];
                                if (!isNearAvantCard(node, row)) continue;
                                var rowText = String(row && (row.innerText || row.textContent) || '')
                                    .normalize('NFD')
                                    .replace(/[\\u0300-\\u036f]/g, '')
                                    .replace(/\\s+/g, ' ')
                                    .trim();
                                if (rowText) extras.push(rowText);
                            }
                            if (extras.length) text = (text + ' ' + extras.join(' ')).replace(/\\s+/g, ' ').trim();
                        } catch (e) {}
                        var patterns = [
                            /vendas?\\s+do\\s+(?:produto|anuncio|item)(?:\\s+ganhador)?\\s*[:\\-]?\\s*(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i,
                            /vendas?\\s+estimad[ao]s?\\s*[:\\-]?\\s*(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i,
                            /ritmo\\s+atual(?:\\s*\\(vendas\\/mes\\))?\\s*[:\\-]?\\s*(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i
                        ];
                        for (var p = 0; p < patterns.length; p += 1) {
                            var match = text.match(patterns[p]);
                            if (match && match[1]) {
                                var parsed = parseHumanNumber(match[1], match[2]);
                                if (Number.isFinite(parsed)) return parsed;
                            }
                        }
                        return null;
                    };
                    var extractImage = function (node) {
                        try {
                            var imgs = Array.prototype.slice.call(node.querySelectorAll('img'));
                            for (var imgIndex = 0; imgIndex < imgs.length; imgIndex += 1) {
                                var img = imgs[imgIndex];
                                var src = img.currentSrc
                                    || img.getAttribute('data-src')
                                    || img.getAttribute('data-original')
                                    || img.getAttribute('data-lazy')
                                    || img.getAttribute('src')
                                    || '';
                                src = String(src || '').trim();
                                if (!src || /^data:/i.test(src) || /sprite|logo|placeholder/i.test(src)) continue;
                                if (src.indexOf('//') === 0) return 'https:' + src;
                                if (/^https?:\\/\\//i.test(src)) return src;
                            }
                        } catch (e) {}
                        return '';
                    };
                    var parseMoneyValue = function (value) {
                        var raw = String(value || '').replace(/\\s+/g, ' ').trim();
                        if (!raw) return null;
                        var ariaReais = raw.match(/(\\d[\\d\\.]*)\\s*reais?(?:\\s*(?:e|,)?\\s*(\\d{1,2})\\s*centavos?)?/i);
                        if (ariaReais && ariaReais[1]) {
                            var reais = Number(String(ariaReais[1]).replace(/\\./g, ''));
                            var cents = ariaReais[2] ? Number(ariaReais[2]) : 0;
                            if (Number.isFinite(reais)) return reais + (Number.isFinite(cents) ? cents / 100 : 0);
                        }
                        var match = raw.replace(/R\\$\\s*/gi, '').replace(/\\s+/g, '').match(/\\d[\\d\\.,]*/);
                        if (!match) return null;
                        var normalized = match[0];
                        if (normalized.indexOf('.') >= 0 && normalized.indexOf(',') >= 0) {
                            normalized = normalized.lastIndexOf('.') > normalized.lastIndexOf(',')
                                ? normalized.replace(/,/g, '')
                                : normalized.replace(/\\./g, '').replace(/,/g, '.');
                        } else if (normalized.indexOf(',') >= 0) {
                            normalized = normalized.replace(/\\./g, '').replace(/,/g, '.');
                        }
                        var parsed = Number(normalized);
                        return Number.isFinite(parsed) ? parsed : null;
                    };
                    var readMoneyAmount = function (el) {
                        if (!el) return null;
                        var aria = el.getAttribute && (el.getAttribute('aria-label') || el.getAttribute('title'));
                        var ariaValue = parseMoneyValue(aria);
                        if (Number.isFinite(ariaValue)) return ariaValue;
                        var fraction = el.querySelector && el.querySelector('.andes-money-amount__fraction');
                        var cents = el.querySelector && el.querySelector('.andes-money-amount__cents, .andes-money-amount__cents-superscript');
                        if (fraction && String(fraction.textContent || '').trim()) {
                            var composed = String(fraction.textContent || '').trim();
                            if (cents && String(cents.textContent || '').trim()) composed += ',' + String(cents.textContent || '').trim();
                            var composedValue = parseMoneyValue(composed);
                            if (Number.isFinite(composedValue)) return composedValue;
                        }
                        return parseMoneyValue(el.textContent || '');
                    };
                    var extractPrice = function (node) {
                        var result = { preco: null, preco_original: null, preco_promocional: null };
                        try {
                            var amounts = Array.prototype.slice.call(node.querySelectorAll('.andes-money-amount, [class*="money-amount"], [class*="price-tag"]'));
                            for (var ai = 0; ai < amounts.length; ai += 1) {
                                var amountEl = amounts[ai];
                                var value = readMoneyAmount(amountEl);
                                if (!Number.isFinite(value)) continue;
                                var textContext = normalizeListingTitle(String((amountEl.className || '') + ' ' + (amountEl.closest && amountEl.closest('s, del, [class*="previous"], [class*="original"], [class*="old"], [class*="strike"]') ? ' previous' : '') + ' ' + (amountEl.parentElement && amountEl.parentElement.className || '')));
                                var isOriginal = /previous|original|old|strike|tachado|riscado/.test(textContext) || !!(amountEl.closest && amountEl.closest('s, del'));
                                if (isOriginal && result.preco_original === null) {
                                    result.preco_original = value;
                                } else if (!isOriginal && result.preco === null) {
                                    result.preco = value;
                                }
                            }
                            if (result.preco_original !== null && result.preco !== null && result.preco_original > result.preco) {
                                result.preco_promocional = result.preco;
                            }
                        } catch (e) {}
                        return result;
                    };
                    var extractParcelamentoSemJuros = function (node) {
                        try {
                            var text = String(node && (node.innerText || node.textContent) ? (node.innerText || node.textContent) : '')
                                .normalize('NFD')
                                .replace(/[\\u0300-\\u036f]/g, '')
                                .toLowerCase()
                                .replace(/\\s+/g, ' ')
                                .trim();
                            return /\\bsem\\s+juros\\b|\\b0\\s*%?\\s*de?\\s*juros\\b/.test(text);
                        } catch (e) {
                            return false;
                        }
                    };
                    var extractFull = function (node) {
                        try {
                            if (!node || !node.querySelectorAll) return false;
                            var attrs = Array.prototype.slice.call(node.querySelectorAll('[aria-label], [title], img[alt], [class], [data-testid], [data-full]'));
                            for (var fi = 0; fi < attrs.length; fi += 1) {
                                var el = attrs[fi];
                                var texto = String([
                                    el.getAttribute && el.getAttribute('aria-label'),
                                    el.getAttribute && el.getAttribute('title'),
                                    el.getAttribute && el.getAttribute('alt'),
                                    el.getAttribute && el.getAttribute('class'),
                                    el.getAttribute && el.getAttribute('data-testid'),
                                    el.getAttribute && el.getAttribute('data-full')
                                ].filter(Boolean).join(' '))
                                    .normalize('NFD')
                                    .replace(/[\\u0300-\\u036f]/g, '')
                                    .toLowerCase()
                                    .replace(/\\s+/g, ' ')
                                    .trim();
                                if (/\\bfull\\b|fulfillment/.test(texto)) return true;
                            }
                        } catch (e) {}
                        return false;
                    };

                    var out = [];
                    var seen = {};
                    var cardMaisProximo = function (anchor) {
                        if (!anchor || !anchor.closest) return anchor;
                        var atual = anchor;
                        for (var nivel = 0; atual && nivel < 10; nivel += 1) {
                            if (temSinalProdutoVisual(atual)) return atual;
                            atual = atual.parentElement;
                        }
                        var candidato = anchor.closest([
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
                            '[class*="shops__layout-item"]',
                            'main ol > li',
                            'main ul > li',
                            'li',
                            'article',
                            'section'
                        ].join(',')) || anchor;
                        if (temSinalProdutoVisual(candidato)) return candidato;
                        return anchor;
                    };
                    var cards = queryAllDeep(selectors);
                    queryAllDeep('a[href]').forEach(function (anchor) {
                        var hrefAnchor = cleanUrl(anchor.href || anchor.getAttribute('href') || '');
                        if (!isProductUrl(hrefAnchor)) return;
                        var cardAnchor = cardMaisProximo(anchor);
                        if (cardAnchor && cards.indexOf(cardAnchor) < 0) cards.push(cardAnchor);
                    });
                    for (var i = 0; i < cards.length; i += 1) {
                        var card = cards[i];
                        var href = findProductHrefInNode(card);
                        if (!href || seen[href]) continue;
                        var idCard = extractItemIdFromNode(card, href);
                        var titleCard = titleFrom(card);
                        if (shouldSkipListingCandidate(card, titleCard)) continue;
                        seen[href] = true;
                        var vendasCard = extractVendas(card);
                        if (vendasCard === null || vendasCard === undefined) vendasCard = extractVendasTextoSimples(card);
                        var vendedorCard = extractSeller(card);
                        var imagemCard = extractImage(card);
                        var precoCard = extractPrice(card);
                        var semJurosCard = extractParcelamentoSemJuros(card);
                        var fullCard = extractFull(card);
                        out.push({ posicao: out.length + 1, id: idCard || '', url: href, titulo: titleCard, imagem: imagemCard, thumbnail: imagemCard, preco: precoCard.preco, price: precoCard.preco, preco_original: precoCard.preco_original, original_price: precoCard.preco_original, preco_promocional: precoCard.preco_promocional, parcelamento_sem_juros: semJurosCard, tipo_anuncio: semJurosCard ? 'Premium' : 'Classico', is_full: fullCard ? true : '', full: fullCard ? true : '', vendedor: vendedorCard, vendedorFonte: vendedorCard ? 'avantpro_vendedor' : '', vendedor_fonte: vendedorCard ? 'avantpro_vendedor' : '', data_criacao: extractAvantDate(card), vendas: vendasCard, vendasFonte: vendasCard !== null && vendasCard !== undefined ? 'avantpro_anuncio' : '' });
                    }

                    if (!out.length) {
                        var links = queryAllDeep('a[href]');
                        for (var k = 0; k < links.length; k += 1) {
                            var linkHref = cleanUrl(links[k].href);
                            if (!isProductUrl(linkHref) || seen[linkHref]) continue;
                            var linkId = extractItemId(linkHref);
                            var linkTitle = (links[k].textContent || links[k].getAttribute('title') || '').trim().slice(0, 240);
                            if (shouldSkipListingCandidate(links[k], linkTitle)) continue;
                            seen[linkHref] = true;
                                var linkCard = links[k].closest && (links[k].closest('li, article, section, div.poly-card, div.ui-search-result') || links[k]) || links[k];
                                var precoLink = extractPrice(linkCard);
                                var semJurosLink = extractParcelamentoSemJuros(linkCard);
                                var fullLink = extractFull(linkCard);
                                var vendasLink = extractVendas(linkCard);
                                if (vendasLink === null || vendasLink === undefined) vendasLink = extractVendasTextoSimples(linkCard);
                                out.push({ posicao: out.length + 1, id: linkId || '', url: linkHref, titulo: linkTitle, imagem: extractImage(linkCard), preco: precoLink.preco, price: precoLink.preco, preco_original: precoLink.preco_original, original_price: precoLink.preco_original, preco_promocional: precoLink.preco_promocional, parcelamento_sem_juros: semJurosLink, tipo_anuncio: semJurosLink ? 'Premium' : 'Classico', is_full: fullLink ? true : '', full: fullLink ? true : '', vendas: vendasLink, vendasFonte: vendasLink !== null && vendasLink !== undefined ? 'avantpro_card' : '' });
                            if (out.length >= 100) break;
                        }
                    }

                    if (!out.length) {
                        var candidates = Array.prototype.slice.call(document.querySelectorAll(selectors + ', a[href*="MLB"], a[href*="/p/MLB"], a[href*="wid=MLB"], a[href*="item_id"], [data-item-id], [data-id*="MLB"]'));
                        for (var c = 0; c < candidates.length && out.length < 100; c += 1) {
                            var node = candidates[c];
                            var hrefFound = findProductHrefInNode(node);
                            if (!hrefFound || seen[hrefFound]) continue;
                            var cardNode = node.closest && (node.closest('li, article, section, div.poly-card, div.ui-search-result, div[class*="poly"], div[class*="search"]') || node);
                            var candidateId = extractItemId(hrefFound);
                            var candidateTitle = titleFrom(cardNode) || String(node.textContent || '').trim().slice(0, 240);
                            if (shouldSkipListingCandidate(cardNode, candidateTitle)) continue;
                            seen[hrefFound] = true;
                                var vendasCardNode = extractVendas(cardNode);
                                if (vendasCardNode === null || vendasCardNode === undefined) vendasCardNode = extractVendasTextoSimples(cardNode);
                            var vendedorCardNode = extractSeller(cardNode);
                            var imagemCardNode = extractImage(cardNode);
                            var precoCardNode = extractPrice(cardNode);
                            var semJurosCardNode = extractParcelamentoSemJuros(cardNode);
                            var fullCardNode = extractFull(cardNode);
                            out.push({
                                posicao: out.length + 1,
                                id: candidateId || '',
                                url: hrefFound,
                                titulo: candidateTitle,
                                imagem: imagemCardNode,
                                thumbnail: imagemCardNode,
                                preco: precoCardNode.preco,
                                price: precoCardNode.preco,
                                preco_original: precoCardNode.preco_original,
                                original_price: precoCardNode.preco_original,
                                preco_promocional: precoCardNode.preco_promocional,
                                parcelamento_sem_juros: semJurosCardNode,
                                tipo_anuncio: semJurosCardNode ? 'Premium' : 'Classico',
                                is_full: fullCardNode ? true : '',
                                full: fullCardNode ? true : '',
                                vendedor: vendedorCardNode,
                                vendedorFonte: vendedorCardNode ? 'avantpro_vendedor' : '',
                                vendedor_fonte: vendedorCardNode ? 'avantpro_vendedor' : '',
                                data_criacao: extractAvantDate(cardNode),
                                vendas: vendasCardNode,
                                vendasFonte: vendasCardNode !== null && vendasCardNode !== undefined ? 'avantpro_anuncio' : ''
                            });
                        }
                    }

                    if (!out.length) {
                        var html = String(document.documentElement && document.documentElement.outerHTML ? document.documentElement.outerHTML : '');
                        var decodeHtmlMl = function(rawValue) {
                            var text = String(rawValue || '');
                            for (var pass = 0; pass < 4; pass += 1) {
                                text = text
                                    .split('\\\\u002F').join('/')
                                    .split('\\\\u002f').join('/')
                                    .split('\\\\u003A').join(':')
                                    .split('\\\\u003a').join(':')
                                    .split('\\\\u003D').join('=')
                                    .split('\\\\u003d').join('=')
                                    .split('\\\\u0026').join('&')
                                    .split('\\\\u002D').join('-')
                                    .split('\\\\u002d').join('-')
                                    .split('\\\\u002E').join('.')
                                    .split('\\\\u002e').join('.')
                                    .split('\\\\/').join('/')
                                    .split('\\\\\\"').join('"')
                                    .split('&quot;').join('"')
                                    .split('&amp;').join('&')
                                    .split('\\\\&').join('&');
                                try {
                                    var decoded = decodeURIComponent(text);
                                    if (decoded === text) break;
                                    text = decoded;
                                } catch (_decodeErr) {
                                    break;
                                }
                            }
                            return text;
                        };
                        var decodedHtml = decodeHtmlMl(html);
                        var decodeMaybe = function(value) {
                            var text = decodeHtmlMl(value);
                            for (var pass = 0; pass < 3; pass += 1) {
                                try {
                                    var decoded = decodeURIComponent(text);
                                    if (decoded === text) break;
                                    text = decoded;
                                } catch (_err) {
                                    break;
                                }
                            }
                            return text;
                        };
                        var tituloFromUrl = function(urlValue) {
                            var text = String(urlValue || '');
                            var match = text.match(/\\/MLB-?\\d+-([^?#]+?)(?:-_JM|_JM|$)/i);
                            if (!match) return '';
                            return decodeMaybe(match[1])
                                .replace(/[-_]+/g, ' ')
                                .replace(/\\s+/g, ' ')
                                .trim();
                        };
                        var limparLinkPreload = function(urlValue, itemIdValue) {
                            var href = decodeMaybe(urlValue || '')
                                .replace(/\\+/g, '')
                                .replace(/&quot;/g, '"')
                                .replace(/"/g, '')
                                .trim();
                            var urldestMatch = href.match(/[?&]urldest=([^&#]+)/i);
                            if (urldestMatch && urldestMatch[1]) {
                                href = decodeMaybe(urldestMatch[1]);
                            }
                            href = href.split('#').shift();
                            href = href.replace(/[),.;]+$/g, '');
                            if (href && href.indexOf('produto.mercadolivre.com.br') === -1) {
                                var insideMatch = href.match(/(https?:\\/\\/produto\\.mercadolivre\\.com\\.br\\/[^"'<>\\\\\\s]+MLB-?\\d+[^"'<>\\\\\\s]*)/i);
                                if (insideMatch) href = insideMatch[1];
                            }
                            if (!isProductUrl(href) && itemIdValue) {
                                href = urlFromItemId(itemIdValue);
                            }
                            return href;
                        };
                        var htmlPreloadMax = 20;
                        var adicionarPreload = function(rawItemId, rawUrl, rawTitle, rawPrice, rawImage) {
                            var itemId = String(rawItemId || '').replace('-', '').toUpperCase();
                            if (!itemId || !/^MLB\\d{6,}$/.test(itemId)) return false;
                            var href = limparLinkPreload(rawUrl, itemId);
                            if (!href || !isProductUrl(href)) return false;
                            var hrefId = extractItemId(href);
                            if (hrefId && hrefId !== itemId) {
                                href = urlFromItemId(itemId);
                            }
                            var key = itemId || href;
                            if (seen['html:' + key]) return false;
                            seen['html:' + key] = true;
                            seen[href] = true;
                            var preco = rawPrice !== null && rawPrice !== undefined && rawPrice !== '' ? Number(rawPrice) : null;
                            if (!Number.isFinite(preco)) preco = null;
                            var imagem = rawImage ? decodeMaybe(rawImage) : '';
                            var titulo = decodeMaybe(rawTitle || '')
                                .replace(/<[^>]+>/g, ' ')
                                .replace(/\\s+/g, ' ')
                                .trim()
                                .slice(0, 240);
                            if (!titulo) titulo = tituloFromUrl(href);
                            out.push({
                                posicao: out.length + 1,
                                id: itemId,
                                url: href,
                                titulo: titulo,
                                imagem: imagem,
                                thumbnail: imagem,
                                preco: preco,
                                price: preco,
                                origem_dados: 'mercadolivre_html_preload'
                            });
                            return true;
                        };
                        var preloadIdRegex = /"id"\\s*:\\s*"(MLB\\d{6,})"/gi;
                        var preloadMatch = null;
                        while ((preloadMatch = preloadIdRegex.exec(decodedHtml)) && out.length < htmlPreloadMax) {
                            var start = Math.max(0, preloadMatch.index - 2600);
                            var end = Math.min(decodedHtml.length, preloadMatch.index + 4200);
                            var chunk = decodedHtml.slice(start, end);
                            var idPreload = preloadMatch[1];
                            var linkMatch = chunk.match(/"link"\\s*:\\s*"(https?:\\/\\/[^"]*?(?:MLB-?\\d+|item_id=MLB\\d+|item_id%3DMLB\\d+)[^"]*)"/i)
                                || chunk.match(/(https?:\\/\\/produto\\.mercadolivre\\.com\\.br\\/[^"'<>\\\\\\s]*MLB-?\\d+[^"'<>\\\\\\s]*)/i);
                            var urlPreload = linkMatch && linkMatch[1] ? linkMatch[1] : '';
                            if (!urlPreload) {
                                var urlDestMatch = chunk.match(/urldest=([^"'<>\\\\\\s&]+)/i);
                                if (urlDestMatch && urlDestMatch[1]) urlPreload = urlDestMatch[1];
                            }
                            var titleMatch = chunk.match(/"title"\\s*:\\s*"([^"]{3,220})"/i)
                                || chunk.match(/"name"\\s*:\\s*"([^"]{3,220})"/i);
                            var priceMatch = chunk.match(/"price"\\s*:\\s*\\{[^}]*"amount"\\s*:\\s*([0-9]+(?:\\.[0-9]+)?)/i)
                                || chunk.match(/"amount"\\s*:\\s*([0-9]+(?:\\.[0-9]+)?)/i);
                            var pictureMatch = chunk.match(/"picture"\\s*:\\s*"(https?:\\/\\/[^"]+)"/i)
                                || chunk.match(/"thumbnail"\\s*:\\s*"(https?:\\/\\/[^"]+)"/i);
                            adicionarPreload(
                                idPreload,
                                urlPreload,
                                titleMatch && titleMatch[1] ? decodeMaybe(titleMatch[1]) : '',
                                priceMatch && priceMatch[1] ? priceMatch[1] : null,
                                pictureMatch && pictureMatch[1] ? pictureMatch[1] : ''
                            );
                        }
                        var productUrlRegex = new RegExp("https?:\\\\/\\\\/(?:www\\\\.)?mercadolivre\\\\.com\\\\.br\\\\/(?:[^\\\"'<>\\\\s]*?(?:MLB-?\\\\d{6,}|\\\\/p\\\\/MLB\\\\d+|wid=MLB\\\\d+|item_id%3AMLB\\\\d+|item_id:MLB\\\\d+)[^\\\"'<>\\\\s]*)", "gi");
                        var matches = decodedHtml.match(productUrlRegex) || [];
                        for (var m = 0; m < matches.length; m += 1) {
                            var matchHref = limparLinkPreload(matches[m], '');
                            if (!isProductUrl(matchHref) || seen[matchHref]) continue;
                            var matchId = extractItemId(matchHref);
                            if (!matchId) continue;
                            seen[matchHref] = true;
                            adicionarPreload(matchId, matchHref, tituloFromUrl(matchHref), null, '');
                            if (out.length >= htmlPreloadMax) break;
                        }
                        if (!out.length) {
                            var idMatches = decodedHtml.match(/\\bMLB-?\\d{6,}\\b/gi) || [];
                            for (var im = 0; im < idMatches.length && out.length < htmlPreloadMax; im += 1) {
                                var htmlId = String(idMatches[im] || '').replace('-', '').toUpperCase();
                                var htmlUrl = urlFromItemId(htmlId);
                                if (!htmlUrl || seen[htmlUrl]) continue;
                                seen[htmlUrl] = true;
                                out.push({ posicao: out.length + 1, id: htmlId, url: htmlUrl, titulo: '' });
                            }
                        }
                    }

                    return {
                        success: true,
                        currentUrl: currentUrl,
                        needsLogin: needsLogin,
                        needsAvantLogin: needsAvantLogin,
                        hasAvantData: hasRealAvantData,
                        noResults: noResults && !out.length,
                        total: out.length,
                        anuncios: out,
                        debug: {
                            linkCount: document.links ? document.links.length : 0,
                            cardCount: cards.length,
                            title: document.title || '',
                            fastLinksOnly: fastLinksOnly
                        }
                    };
                } catch (err) {
                    return { success: false, total: 0, anuncios: [], error: err && (err.stack || err.message) ? String(err.stack || err.message) : String(err) };
                }
            })();`);
  pageScripts.registerPart('montar-script-garantir-pesquisa-mercado-livre-submetida-1', 0, `
                (function () {
                    var valor = __JK_MONTAR_SCRIPT_GARANTIR_PESQUISA_MERCADO_LIVRE_SUBMETIDA_1_P0__;
                    var urlAlvo = __JK_MONTAR_SCRIPT_GARANTIR_PESQUISA_MERCADO_LIVRE_SUBMETIDA_1_P1__;
                    var normalizar = function (value) {
                        return String(value || '')
                            .normalize('NFD')
                            .replace(/[\\u0300-\\u036f]/g, '')
                            .toLowerCase()
                            .replace(/[^a-z0-9]+/g, ' ')
                            .replace(/\\s+/g, ' ')
                            .trim();
                    };
                    var candidatos = Array.prototype.slice.call(document.querySelectorAll(
                        'input[name="as_word"], input[name="q"], input[type="search"], input[placeholder*="Buscar"], input[aria-label*="Buscar"]'
                    ));
                    var input = candidatos.find(function (node) {
                        if (!node || node.disabled || node.readOnly) return false;
                        var rect = node.getBoundingClientRect ? node.getBoundingClientRect() : null;
                        var visivel = !rect || (rect.width > 0 && rect.height > 0);
                        var alvo = normalizar([node.name, node.id, node.placeholder, node.getAttribute && node.getAttribute('aria-label')].join(' '));
                        return visivel && (/buscar|search|as word|q/.test(alvo) || node.type === 'search');
                    }) || candidatos[0] || null;
                    var form = input && input.form ? input.form : document.querySelector('form[action*="mercadolivre"], form[action*="lista"], form');
                    if (input) {
                        input.focus();
                        try { input.value = valor; } catch (_valueErr) {}
                        try { input.setAttribute('value', valor); } catch (_attrErr) {}
                        ['input', 'change'].forEach(function (name) {
                            try { input.dispatchEvent(new Event(name, { bubbles: true, cancelable: true })); } catch (_eventErr) {}
                        });
                        ['keydown', 'keypress', 'keyup'].forEach(function (name) {
                            try { input.dispatchEvent(new KeyboardEvent(name, { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true })); } catch (_keyErr) {}
                        });
                    }
                    if (form) {
                        try {
                            if (typeof form.requestSubmit === 'function') {
                                form.requestSubmit();
                                return { ok: true, method: 'requestSubmit', url: location.href };
                            }
                        } catch (_requestSubmitErr) {}
                        try {
                            form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
                            if (typeof form.submit === 'function') {
                                form.submit();
                                return { ok: true, method: 'formSubmit', url: location.href };
                            }
                        } catch (_formErr) {}
                    }
                    if (urlAlvo) {
                        setTimeout(function () {
                            try { location.assign(urlAlvo); } catch (_assignErr) { location.href = urlAlvo; }
                        }, input || form ? 180 : 0);
                        return { ok: true, method: 'location.assign', url: urlAlvo };
                    }
                    return { ok: false, reason: 'sem_input_form_url', url: location.href };
                })();
            `);
  pageScripts.registerPart('montar-script-diagnosticar-pesquisa-mercado-livre-atual-1', 0, `
                (function () {
                    var termo = __JK_MONTAR_SCRIPT_DIAGNOSTICAR_PESQUISA_MERCADO_LIVRE_ATUAL_1_P0__;
                    var urlAlvo = __JK_MONTAR_SCRIPT_DIAGNOSTICAR_PESQUISA_MERCADO_LIVRE_ATUAL_1_P1__;
                    var normalizar = function (value) {
                        return String(value || '')
                            .normalize('NFD')
                            .replace(/[\\u0300-\\u036f]/g, '')
                            .toLowerCase()
                            .replace(/[^a-z0-9]+/g, ' ')
                            .replace(/\\s+/g, ' ')
                            .trim();
                    };
                    var termoNorm = normalizar(termo);
                    var tokens = termoNorm.split(' ').filter(function (token) { return token.length >= 3; }).slice(0, 6);
                    var urlAtual = String(location.href || '');
                    var tituloPagina = normalizar(document.title || '');
                    var textoBusca = normalizar([
                        urlAtual,
                        tituloPagina,
                        document.querySelector('input[name="as_word"], input[name="q"], input[type="search"]') && document.querySelector('input[name="as_word"], input[name="q"], input[type="search"]').value,
                        document.querySelector('h1, .ui-search-breadcrumb__title, .ui-search-search-result__quantity-results') && document.querySelector('h1, .ui-search-breadcrumb__title, .ui-search-search-result__quantity-results').textContent
                    ].filter(Boolean).join(' '));
                    var cards = document.querySelectorAll('li.ui-search-layout__item, .poly-card, [class*="poly-card"], [class*="ui-search-result"], [data-testid*="result"], [data-testid*="card"], [class*="product-card"], [class*="andes-card"]').length;
                    var resultadoVisual = /\b\d+\s+resultados?\b/.test(textoBusca)
                        || !!document.querySelector('[class*="quantity-results"], [class*="ui-search-search-result"], [class*="breadcrumb__title"]');
                    var paginaProduto = !!(
                        document.querySelector('.ui-pdp-title, [class*="ui-pdp-title"], .ui-pdp-container, [class*="pdp"] h1')
                    );
                    var paginaCombinaComTermo = !tokens.length || tokens.some(function (token) {
                        return textoBusca.indexOf(token) >= 0;
                    });
                    var carregando = !!document.querySelector('[class*="loading"], [aria-busy="true"], .ui-search-loader');
                    var semResultados = /sem resultados|nao encontramos|não encontramos|no encontramos/.test(textoBusca);
                    if (cards > 0 && paginaCombinaComTermo && !paginaProduto) {
                        return { ok: true, cards: cards, paginaProduto: paginaProduto, url: urlAtual, termo: termo };
                    }
                    if (resultadoVisual && paginaCombinaComTermo && !paginaProduto) {
                        cards = Math.max(cards, 1);
                        return { ok: true, cards: cards, resultadoVisual: true, paginaProduto: paginaProduto, url: urlAtual, termo: termo };
                    }
                    if (semResultados && paginaCombinaComTermo && !paginaProduto) {
                        return { ok: true, noResults: true, cards: cards, paginaProduto: paginaProduto, url: urlAtual, termo: termo };
                    }
                    return {
                        ok: false,
                        reason: paginaProduto ? 'pagina_produto' : (paginaCombinaComTermo ? 'aguardando_resultados' : 'termo_nao_confere'),
                        cards: cards,
                        paginaProduto: paginaProduto,
                        loading: carregando,
                        url: urlAtual,
                        urlAlvo: urlAlvo,
                        termo: termo
                    };
                })();
            `);
})(window);
