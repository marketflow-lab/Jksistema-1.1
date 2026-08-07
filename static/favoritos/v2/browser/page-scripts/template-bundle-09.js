(function (global) {
  'use strict';
  const pageScripts = global.FavoritosV2.browser.pageScripts;
  pageScripts.registerPart('acionar-cards-avant-pro-fila-webview-1', 0, `
                (async function () {
                    var maxClicks = __JK_ACIONAR_CARDS_AVANT_PRO_FILA_WEBVIEW_1_P0__;
                    var maxTentativasPorCard = __JK_ACIONAR_CARDS_AVANT_PRO_FILA_WEBVIEW_1_P1__;
                    var maxRuntimeMs = __JK_ACIONAR_CARDS_AVANT_PRO_FILA_WEBVIEW_1_P2__;
                    var deepScan = __JK_ACIONAR_CARDS_AVANT_PRO_FILA_WEBVIEW_1_P3__;
                    var checkLogin = __JK_ACIONAR_CARDS_AVANT_PRO_FILA_WEBVIEW_1_P4__;
                    var startedAt = Date.now();
                    var endAt = startedAt + maxRuntimeMs;
                    var hasTime = function (bufferMs) { return Date.now() + (Number(bufferMs) || 0) < endAt; };
                    var sleep = function (ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); };
                    var normalizar = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (e) {}
                        return text.toLowerCase();
                    };
                    var cleanUrl = function (href) {
                        href = String(href || '').split('#')[0].trim();
                        if (/^\\/\\//.test(href)) return 'https:' + href;
                        if (/^\\//.test(href)) return 'https://www.mercadolivre.com.br' + href;
                        return href;
                    };
                    var queryAllDeep = function (selector, root) {
                        var found = [];
                        var visited = [];
                        var visit = function (base) {
                            if (!base || visited.indexOf(base) >= 0) return;
                            visited.push(base);
                            try {
                                if (base.querySelectorAll) {
                                    Array.prototype.slice.call(base.querySelectorAll(selector)).forEach(function (node) {
                                        if (found.indexOf(node) < 0) found.push(node);
                                    });
                                    Array.prototype.slice.call(base.querySelectorAll('*')).forEach(function (node) {
                                        if (node && node.shadowRoot) visit(node.shadowRoot);
                                    });
                                }
                            } catch (_err) {}
                        };
                        visit(root || document);
                        return found;
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
                    var textoNode = function (node) {
                        if (!node) return '';
                        return normalizar([
                            node.innerText,
                            node.textContent,
                            node.value,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('title'),
                            node.getAttribute && node.getAttribute('class'),
                            node.getAttribute && node.getAttribute('id')
                        ].filter(Boolean).join(' '));
                    };
                    var idDe = function (value) {
                        var text = String(value || '');
                        try { text = decodeURIComponent(text); } catch (_err) {}
                        var match = text.match(/\\bMLB-?(\\d{6,})\\b/i) || text.match(/[?&](?:wid|item_id)=(MLB\\d{6,})/i);
                        if (!match) return '';
                        var raw = String(match[1] || '').replace('-', '').toUpperCase();
                        return raw.indexOf('MLB') === 0 ? raw : ('MLB' + raw);
                    };
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
                    var productHref = function (card) {
                        var links = [];
                        try { links = Array.prototype.slice.call(card && card.querySelectorAll ? card.querySelectorAll('a[href]') : []); } catch (_directHrefErr) {}
                        try {
                            if (card && card.matches && card.matches('a[href]') && links.indexOf(card) < 0) {
                                links.unshift(card);
                            }
                        } catch (_selfHrefErr) {}
                        if (!links.length) links = queryAllDeep('a[href]', card);
                        for (var i = 0; i < links.length; i += 1) {
                            var rawHref = links[i].href || links[i].getAttribute('href') || '';
                            if (/\\bMLB-?\\d{6,}\\b|[?&](?:wid|item_id)=MLB\\d{6,}|\\/p\\/MLB|\\/up\\/MLB|produto\\.mercadolivre\\.com\\.br/i.test(rawHref)) return rawHref;
                        }
                        return '';
                    };
                    var keyCard = function (card) {
                        var href = productHref(card);
                        var attrText = [
                            card && card.getAttribute && card.getAttribute('data-item-id'),
                            card && card.getAttribute && card.getAttribute('data-id'),
                            card && card.getAttribute && card.getAttribute('id'),
                            card && card.getAttribute && card.getAttribute('aria-label')
                        ].filter(Boolean).join(' ');
                        var id = idDe(href || attrText || '');
                        if (id) return 'id:' + id;
                        if (href) {
                            var linkLimpo = cleanProductUrl(href, '').toLowerCase();
                            return linkLimpo ? ('url:' + linkLimpo) : ('url:' + cleanUrl(href).split('#')[0].toLowerCase());
                        }
                        return '';
                    };
                    var cleanProductUrl = function (href, id) {
                        href = cleanUrl(href);
                        if (!href && id) return 'https://produto.mercadolivre.com.br/' + String(id).replace('MLB', 'MLB-');
                        if (!href) return '';
                        try {
                            var parsed = new URL(href, 'https://www.mercadolivre.com.br');
                            parsed.hash = '';
                            [
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
                                'loader',
                                'noIndex'
                            ].forEach(function (param) { parsed.searchParams.delete(param); });
                            parsed.pathname = parsed.pathname.replace(/\\/+$/, '');
                            return parsed.toString().replace(/[?&]$/, '');
                        } catch (_err) {
                            return href;
                        }
                    };
                    var tituloFracoCard = function (value) {
                        var text = normalizar(value);
                        if (!text) return true;
                        if (/^(jm|novo|usado|patrocinado|mais vendido|r\\$|frete|chegar|vendid|mercado livre|favoritos|compras|produto relacionado|opcoes de compra)$/i.test(text)) return true;
                        if (/^mlb\\d+$/i.test(text.replace(/-/g, ''))) return true;
                        return text.length <= 3;
                    };
                    var tituloDoCard = function (card) {
                        var candidatos = queryAllDeep('h2, h3, [class*="title"], [class*="name"], [class*="poly-component__title"], [class*="ui-search-item__title"], [data-testid*="title"], a[href]', card);
                        candidatos.push(card);
                        for (var i = 0; i < candidatos.length; i += 1) {
                            var node = candidatos[i];
                            var text = String((node && (
                                node.getAttribute && (node.getAttribute('title') || node.getAttribute('aria-label')) ||
                                node.innerText ||
                                node.textContent
                            )) || '').replace(/\\s+/g, ' ').trim();
                            if (text.length >= 8 && !tituloFracoCard(text)) return text.slice(0, 240);
                        }
                        return '';
                    };
                    var imagemValidaCard = function (url) {
                        var text = String(url || '').trim();
                        if (!text || /^data:/i.test(text)) return false;
                        if (!/^https?:\\/\\//i.test(text) && !/^\\/\\//.test(text)) return false;
                        return !/(logo|avatar|sprite|icon|favicon|badge|medal|avantpro|meliplus)/i.test(text);
                    };
                    var imagemDoCard = function (card) {
                        var imgs = queryAllDeep('img, source[srcset], source[data-srcset]', card);
                        var candidatos = imgs.map(function (img, index) {
                            var srcset = img.getAttribute && (img.getAttribute('srcset') || img.getAttribute('data-srcset'));
                            var srcsetUrl = '';
                            if (srcset) {
                                srcsetUrl = String(srcset).split(',').map(function (part) {
                                    return part.trim().split(/\\s+/)[0] || '';
                                }).filter(imagemValidaCard)[0] || '';
                            }
                            var url = srcsetUrl
                                || String(img.currentSrc || '')
                                || String(img.src || '')
                                || String(img.getAttribute && (img.getAttribute('src') || img.getAttribute('data-src') || img.getAttribute('data-original') || img.getAttribute('data-lazy') || '') || '');
                            var rect = null;
                            try { rect = img.getBoundingClientRect && img.getBoundingClientRect(); } catch (_rectErr) {}
                            return {
                                url: url,
                                area: rect ? Math.max(0, rect.width) * Math.max(0, rect.height) : 0,
                                index: index
                            };
                        }).filter(function (item) { return imagemValidaCard(item.url); });
                        candidatos.sort(function (a, b) { return b.area - a.area || a.index - b.index; });
                        return candidatos[0] && candidatos[0].url || '';
                    };
                    var parsePrecoTextoCard = function (valor) {
                        var match = String(valor || '').match(/R\\$\\s*([0-9.]+)(?:\\s*,\\s*([0-9]{1,2}))?/);
                        if (!match) return null;
                        var inteiro = String(match[1] || '').replace(/\\./g, '');
                        var cents = String(match[2] || '0').padEnd(2, '0').slice(0, 2);
                        var value = Number(inteiro + '.' + cents);
                        return Number.isFinite(value) ? value : null;
                    };
                    var nodeDentroAvantCard = function (node) {
                        var atual = node;
                        for (var i = 0; atual && i < 8; i += 1) {
                            var cls = String(atual.className || '');
                            var id = String(atual.id || '');
                            if (/avant|created-time-card|product-info-row|faturamento|comissao|frete/i.test(cls + ' ' + id)) return true;
                            atual = atual.parentElement;
                        }
                        return false;
                    };
                    var textoPrecoIndesejadoCard = function (node) {
                        var texto = String(node && (node.innerText || node.textContent) || '').replace(/\\s+/g, ' ');
                        var parent = node && node.parentElement ? String(node.parentElement.innerText || node.parentElement.textContent || '').replace(/\\s+/g, ' ') : texto;
                        return /frete|comiss[aã]o|faturamento|taxa|categoria|total\\s+de\\s+vendas|vendas\\s+do\\s+produto|ritmo|visitas/i.test(texto + ' ' + parent);
                    };
                    var parsePrecoNodeCard = function (node) {
                        if (!node || nodeDentroAvantCard(node) || textoPrecoIndesejadoCard(node)) return null;
                        var fractionNode = node.querySelector && node.querySelector('.andes-money-amount__fraction, .price-tag-fraction, [class*="fraction"]');
                        var centsNode = node.querySelector && node.querySelector('.andes-money-amount__cents, .price-tag-cents, [class*="cents"], [class*="decimal"]');
                        var fraction = fractionNode ? String(fractionNode.innerText || fractionNode.textContent || '').replace(/[^0-9.]/g, '') : '';
                        var textNode = String(node.innerText || node.textContent || '');
                        var cents = centsNode ? String(centsNode.innerText || centsNode.textContent || '').replace(/[^0-9]/g, '') : '';
                        if (!cents) {
                            var centsMatch = textNode.match(/[,.]\\s*(\\d{1,2})\\s*$/);
                            cents = centsMatch && centsMatch[1] ? centsMatch[1] : '';
                        }
                        if (fraction) {
                            var inteiro = fraction.replace(/\\./g, '');
                            var centavos = cents ? cents.padEnd(2, '0').slice(0, 2) : '00';
                            var parsed = Number(inteiro + '.' + centavos);
                            return Number.isFinite(parsed) ? parsed : null;
                        }
                        return parsePrecoTextoCard(textNode);
                    };
                    var isPrecoOriginalNodeCard = function (node) {
                        var atual = node;
                        for (var i = 0; atual && i < 5; i += 1) {
                            var info = String(atual.className || '') + ' ' + String(atual.getAttribute && (atual.getAttribute('aria-label') || atual.getAttribute('role') || '') || '');
                            if (/previous|original|old|strikethrough|discount|antes|tachado/i.test(info)) return true;
                            atual = atual.parentElement;
                        }
                        return false;
                    };
                    var isPrecoPrincipalNodeCard = function (node) {
                        var atual = node;
                        for (var i = 0; atual && i < 5; i += 1) {
                            var cls = String(atual.className || '');
                            if (/poly-price__current|ui-search-price__second-line/i.test(cls)) return true;
                            atual = atual.parentElement;
                        }
                        return false;
                    };
                    var isPrecoSecundarioNodeCard = function (node) {
                        var atual = node;
                        for (var i = 0; atual && i < 5; i += 1) {
                            var info = String(atual.className || '') + ' ' + String(atual.innerText || atual.textContent || '');
                            if (/installments|parcel|per[_-]?quantity|price-per-quantity|levando\\s+\\d+\\s+ou\\s+mais|\\b\\d+x\\s*r\\$/i.test(info)) return true;
                            atual = atual.parentElement;
                        }
                        return false;
                    };
                    var precosDoCard = function (card) {
                        var selectorsPreco = [
                            '.poly-price__current .andes-money-amount',
                            '.poly-component__price .andes-money-amount',
                            '.ui-search-price__second-line .andes-money-amount',
                            '[class*="price"] .andes-money-amount',
                            '.andes-money-amount',
                            '.price-tag'
                        ].join(',');
                        var precoPrincipalDireto = queryAllDeep('.poly-price__current .andes-money-amount, .ui-search-price__second-line .andes-money-amount', card)
                            .map(function (node) { return parsePrecoTextoCard(node && (node.innerText || node.textContent)); })
                            .filter(function (value) { return value !== null && value > 0; });
                        var nodes = queryAllDeep(selectorsPreco, card)
                            .filter(function (node) { return !nodeDentroAvantCard(node) && !textoPrecoIndesejadoCard(node); });
                        var atuaisPrincipais = [];
                        var atuais = [];
                        var originais = [];
                        nodes.forEach(function (node) {
                            var value = parsePrecoNodeCard(node);
                            if (value === null || value <= 0) return;
                            if (isPrecoOriginalNodeCard(node)) originais.push(value);
                            else if (isPrecoPrincipalNodeCard(node)) atuaisPrincipais.push(value);
                            else if (isPrecoSecundarioNodeCard(node)) return;
                            else atuais.push(value);
                        });
                        var precoAtual = precoPrincipalDireto.length ? precoPrincipalDireto[0] : (atuaisPrincipais.length ? atuaisPrincipais[0] : (atuais.length ? atuais[0] : null));
                        var precoOriginal = originais.length ? originais[0] : '';
                        if (precoAtual === null) {
                            var direto = String(card && (card.innerText || card.textContent) || '').split(/frete|comiss[aã]o|faturamento|taxa|total\\s+de\\s+vendas/i)[0];
                            precoAtual = parsePrecoTextoCard(direto);
                        }
                        if (precoAtual === null) return { preco: null, preco_original: '', preco_promocional: '', moeda: '' };
                        if (precoOriginal && precoOriginal > precoAtual) {
                            return { preco: precoAtual, preco_original: precoOriginal, preco_promocional: precoAtual, moeda: 'BRL' };
                        }
                        return { preco: precoAtual, preco_original: '', preco_promocional: '', moeda: 'BRL' };
                    };
                    var baseCardData = function (card) {
                        var href = productHref(card);
                        var id = idDe(href || (card && card.outerHTML) || '');
                        href = cleanProductUrl(href, id);
                        var precos = precosDoCard(card);
                        var imagem = imagemDoCard(card);
                        return {
                            id: id,
                            mlb: id,
                            url: href,
                            permalink: href,
                            link: href,
                            titulo: tituloDoCard(card),
                            title: tituloDoCard(card),
                            imagem: imagem,
                            thumbnail: imagem,
                            foto: imagem,
                            preco: precos.preco,
                            price: precos.preco_promocional || precos.preco,
                            preco_original: precos.preco_original,
                            original_price: precos.preco_original,
                            preco_promocional: precos.preco_promocional,
                            promotional_price: precos.preco_promocional,
                            moeda: precos.moeda,
                            currency_id: precos.moeda,
                            tituloFonte: 'mercado_livre_dom_card_clicado',
                            fotoFonte: imagem ? 'mercado_livre_dom_card_clicado' : '',
                            linkFonte: href ? 'mercado_livre_dom_card_clicado' : '',
                            precoFonte: precos.preco !== null ? 'mercado_livre_dom_card_clicado' : '',
                            fonte_preco: precos.preco !== null ? 'mercado_livre_dom_card_clicado' : '',
                            origem_dados: 'avantpro_card_panel_cache'
                        };
                    };
                    var baseCardDataLeve = function (card) {
                        var href = productHref(card);
                        var attrText = [
                            card && card.getAttribute && card.getAttribute('data-item-id'),
                            card && card.getAttribute && card.getAttribute('data-id'),
                            card && card.getAttribute && card.getAttribute('id'),
                            card && card.getAttribute && card.getAttribute('aria-label')
                        ].filter(Boolean).join(' ');
                        var id = idDe(href || attrText || '');
                        href = cleanProductUrl(href, id);
                        return {
                            id: id,
                            mlb: id,
                            url: href,
                            permalink: href,
                            link: href,
                            linkFonte: href ? 'mercado_livre_dom_card_clicado' : '',
                            origem_dados: 'avantpro_card_panel_cache'
                        };
                    };
                    var parseHumanNumberCard = function (value, suffix, decimal) {
                        if (value === null || value === undefined) return null;
                        var raw = String(value).trim().toLowerCase();
                        if (!raw) return null;
                        var normalized = raw.replace(/\\s+/g, '');
                        if (normalized.indexOf('.') >= 0 && normalized.indexOf(',') >= 0) {
                            normalized = normalized.lastIndexOf('.') > normalized.lastIndexOf(',')
                                ? normalized.replace(/,/g, '')
                                : normalized.replace(/\\./g, '').replace(/,/g, '.');
                        } else if (normalized.indexOf(',') >= 0) {
                            normalized = normalized.replace(/\\./g, '').replace(/,/g, '.');
                        }
                        var parsed = parseFloat(normalized);
                        if (!isFinite(parsed)) return null;
                        suffix = String(suffix || '').toLowerCase();
                        if (suffix === 'mil' || suffix === 'k') parsed *= 1000;
                        return decimal ? parsed : Math.round(parsed);
                    };
                    var labelValueAvantCard = function (row) {
                        if (row && row.__jkLabel) {
                            return {
                                label: row.__jkLabel || '',
                                value: row.__jkValue || '',
                                text: row.__jkText || [row.__jkLabel, row.__jkValue].filter(Boolean).join(' ')
                            };
                        }
                        var labelNode = row && row.querySelector && row.querySelector('.avantpro-product-info-row-label, [class*="label"]');
                        var valueNode = row && row.querySelector && row.querySelector('.avantpro-product-info-row-value, [class*="value"]');
                        var label = String(labelNode && (labelNode.innerText || labelNode.textContent) || '').replace(/\\s+/g, ' ').trim();
                        var value = String(valueNode && (valueNode.innerText || valueNode.textContent) || '').replace(/\\s+/g, ' ').trim();
                        var text = String(row && (row.innerText || row.textContent) || '').replace(/\\s+/g, ' ').trim();
                        if (!value && label && text.indexOf(label) >= 0) {
                            value = text.slice(text.indexOf(label) + label.length).replace(/^\\s*[:\\-]?\\s*/, '').trim();
                        }
                        return { label: label, value: value, text: text };
                    };
                    var labelTemAvantCard = function (label, partes) {
                        var n = normalizar(label);
                        return partes.some(function (parte) { return n.indexOf(parte) >= 0; });
                    };
                    var definicoesLabelsAvantCard = [
                        { label: 'Vendas do produto', tokens: ['vendas do produto', 'venda do produto', 'vendas do anuncio', 'venda do anuncio', 'vendas do item', 'venda do item'] },
                        { label: 'Vendas estimadas', tokens: ['vendas estimad', 'venda estimad'] },
                        { label: 'Ritmo atual', tokens: ['ritmo atual', 'media mensal', 'vendas/mes', 'vendas mes'] },
                        { label: 'Nome do vendedor', tokens: ['nome do vendedor'] },
                        { label: 'Localizacao do vendedor', tokens: ['localizacao do vendedor'] },
                        { label: 'Anuncio criado em', tokens: ['anuncio criado em', 'catalogo criado em', 'criado em'] },
                        { label: 'Visitas do anuncio', tokens: ['visitas do anuncio', 'visitas'] },
                        { label: 'Participacao', tokens: ['participacao'] },
                        { label: 'Comissao', tokens: ['comissao'] },
                        { label: 'Reputacao do vendedor', tokens: ['reputacao do vendedor'] }
                    ];
                    var labelAvantCardPorLinha = function (line) {
                        var norm = normalizar(line);
                        for (var i = 0; i < definicoesLabelsAvantCard.length; i += 1) {
                            var def = definicoesLabelsAvantCard[i];
                            for (var j = 0; j < def.tokens.length; j += 1) {
                                var token = def.tokens[j];
                                if (norm === token || norm.indexOf(token + ' ') === 0 || norm.indexOf(token + ':') === 0 || norm.indexOf(token + ' (') === 0) return def;
                            }
                        }
                        return null;
                    };
                    var linhaEhLabelAvantCard = function (line) {
                        return !!labelAvantCardPorLinha(line);
                    };
                    var textoValorNaMesmaLinhaAvantCard = function (line, def) {
                        var text = String(line || '').trim();
                        var norm = normalizar(text);
                        var melhor = '';
                        (def && def.tokens || []).forEach(function (token) {
                            if (norm.indexOf(token) !== 0) return;
                            var resto = text.slice(token.length).replace(/^[\\s:()\\-/=]+/, '').trim();
                            if (resto && resto.length > melhor.length) melhor = resto;
                        });
                        return melhor;
                    };
                    var linhasTextoAvantCard = function (node) {
                        return String(node && (node.innerText || node.textContent) || '')
                            .split(/\\n+/)
                            .map(function (line) { return line.replace(/\\s+/g, ' ').trim(); })
                            .filter(Boolean);
                    };
                    var extrairLinhasVirtuaisAvantCard = function (root) {
                        var linhas = linhasTextoAvantCard(root);
                        if (!linhas.length) return [];
                        var out = [];
                        for (var i = 0; i < linhas.length; i += 1) {
                            var line = linhas[i];
                            var def = labelAvantCardPorLinha(line);
                            if (!def) continue;
                            var value = textoValorNaMesmaLinhaAvantCard(line, def);
                            if (!value) {
                                for (var j = i + 1; j < Math.min(linhas.length, i + 5); j += 1) {
                                    var candidato = linhas[j];
                                    if (!candidato || linhaEhLabelAvantCard(candidato)) break;
                                    if (/^(informacoes?\\s+avant(?:pro)?|carregar\\s+dados\\s+avantpro?)$/i.test(normalizar(candidato))) continue;
                                    value = candidato;
                                    break;
                                }
                            }
                            out.push({
                                __jkLabel: def.label,
                                __jkValue: value || '',
                                __jkText: [def.label, value || ''].filter(Boolean).join(' ')
                            });
                        }
                        return out;
                    };
                    var preencherDadosAvantCard = function (item, row) {
                        var lv = labelValueAvantCard(row);
                        var label = lv.label;
                        var value = lv.value;
                        var text = lv.text;
                        var busca = normalizar(label + ' ' + text);
                        if (labelTemAvantCard(label || busca, ['vendas do produto', 'venda do produto', 'vendas do anuncio', 'venda do anuncio', 'vendas do item', 'venda do item'])) {
                            var vendas = parseHumanNumberCard(value || text, '', false);
                            if (Number.isFinite(vendas)) item.vendas = vendas;
                        } else if (labelTemAvantCard(label || busca, ['vendas estimad', 'venda estimad'])) {
                            var estimadas = parseHumanNumberCard(value || text, '', false);
                            if (Number.isFinite(estimadas) && !Number.isFinite(item.vendas)) item.vendas = estimadas;
                            if (Number.isFinite(estimadas)) item.vendas_estimadas = estimadas;
                        } else if (labelTemAvantCard(label || busca, ['ritmo atual'])) {
                            var ritmo = parseHumanNumberCard(value || text, '', true);
                            if (Number.isFinite(ritmo)) {
                                item.media_mensal = ritmo;
                                item.ritmo_atual = ritmo;
                            }
                        } else if (labelTemAvantCard(label || busca, ['reputacao do vendedor'])) {
                            item.reputacao_vendedor = value || '';
                        } else if (labelTemAvantCard(label || busca, ['localizacao do vendedor'])) {
                            item.localizacao_vendedor = value || '';
                        } else if (labelTemAvantCard(label || busca, ['nome do vendedor']) || (labelTemAvantCard(label || busca, ['vendedor']) && !labelTemAvantCard(label || busca, ['reputacao do vendedor', 'localizacao do vendedor']))) {
                            var vendedor = String(value || '').replace(/^\\s*[:\\-]?\\s*/, '').trim();
                            if (vendedor && vendedor.length <= 120 && !/^\\d+$/.test(vendedor)) item.vendedor = vendedor;
                        } else if (labelTemAvantCard(label || busca, ['anuncio criado em', 'catalogo criado em', 'criado em'])) {
                            var data = String(value || text || '').match(/\\b\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}\\b|\\b20\\d{2}-\\d{2}-\\d{2}\\b/);
                            if (data && data[0]) item.data_criacao = data[0];
                        } else if (labelTemAvantCard(label || busca, ['visitas do anuncio', 'visitas'])) {
                            var visitas = parseHumanNumberCard(value || text, '', false);
                            if (Number.isFinite(visitas)) item.visitas = visitas;
                        } else if (labelTemAvantCard(label || busca, ['participacao'])) {
                            item.participacao = value || '';
                        } else if (labelTemAvantCard(label || busca, ['comissao'])) {
                            item.comissao = value || '';
                        }
                    };
                    var linhasAvantPainel = function (root) {
                        var rows = queryAllDeep('.avantpro-product-info-row, .created-time-card, [class*="avantpro-product-info"], [class*="created-time-card"], [class*="Avantpro"][class*="row"], [class*="avantpro"][class*="row"]', root || document)
                            .filter(function (row) {
                                var text = String(row && (row.innerText || row.textContent) || '').replace(/\\s+/g, ' ').trim();
                                return /vendas?|ritmo|vendedor|criado|visitas|participa|comissao|reputacao/i.test(normalizar(text));
                            });
                        var virtuais = [];
                        if (root && root !== document) {
                            virtuais = extrairLinhasVirtuaisAvantCard(root);
                        } else {
                            var candidatos = queryAllDeep('[class*="avant"], [class*="Avant"], [role="dialog"], aside, section, div')
                                .filter(function (node) {
                                    if (!visible(node)) return false;
                                    var tag = String(node && node.tagName || '').toUpperCase();
                                    if (tag === 'HTML' || tag === 'BODY' || tag === 'MAIN') return false;
                                    var text = normalizar(node && (node.innerText || node.textContent) || '');
                                    if (!/avant|vendas?\\s+do\\s+produto|ritmo\\s+atual|nome\\s+do\\s+vendedor|anuncio\\s+criado\\s+em/.test(text)) return false;
                                    var labels = 0;
                                    definicoesLabelsAvantCard.forEach(function (def) {
                                        if (def.tokens.some(function (token) { return text.indexOf(token) >= 0; })) labels += 1;
                                    });
                                    return labels >= 2;
                                });
                            var usados = [];
                            candidatos.sort(function (a, b) {
                                var ar = null;
                                var br = null;
                                try { ar = a.getBoundingClientRect && a.getBoundingClientRect(); } catch (_arErr) {}
                                try { br = b.getBoundingClientRect && b.getBoundingClientRect(); } catch (_brErr) {}
                                var aa = ar ? ar.width * ar.height : 0;
                                var ba = br ? br.width * br.height : 0;
                                return aa - ba;
                            }).forEach(function (node) {
                                if (usados.some(function (parent) { return parent !== node && parent.contains && parent.contains(node); })) return;
                                var extraidas = extrairLinhasVirtuaisAvantCard(node);
                                if (extraidas.length < 2) return;
                                usados.push(node);
                                virtuais = virtuais.concat(extraidas);
                            });
                        }
                        return rows.concat(virtuais);
                    };
                    var snapshotAvantPainel = function (root) {
                        return linhasAvantPainel(root).map(function (row) {
                            return String(row && (row.innerText || row.textContent) || '').replace(/\\s+/g, ' ').trim();
                        }).filter(Boolean).join('|');
                    };
                    var capturarPainelAvantParaCard = async function (card, snapshotAntes, baseLeve) {
                        var baseMinima = baseLeve || baseCardDataLeve(card);
                        var base = null;
                        var canonical = baseMinima.mlb ? ('mlb:' + baseMinima.mlb) : (baseMinima.link ? ('link:' + String(baseMinima.link).toLowerCase()) : '');
                        if (!canonical) return null;
                        var finalSnapshot = '';
                        var rowsCaptura = [];
                        var origemCard = false;
                        for (var tentativa = 0; tentativa < 3 && hasTime(260); tentativa += 1) {
                            await sleep(tentativa === 0 ? 120 : 180);
                            rowsCaptura = linhasAvantPainel(card);
                            if (rowsCaptura.length) {
                                origemCard = true;
                                finalSnapshot = snapshotAvantPainel(card);
                                break;
                            }
                            finalSnapshot = snapshotAvantPainel();
                            if (finalSnapshot && finalSnapshot !== snapshotAntes) {
                                rowsCaptura = linhasAvantPainel();
                                break;
                            }
                        }
                        if (!origemCard) {
                            finalSnapshot = finalSnapshot || snapshotAvantPainel();
                        }
                        if (!origemCard && (!finalSnapshot || finalSnapshot === snapshotAntes)) return null;
                        if (!rowsCaptura.length) rowsCaptura = linhasAvantPainel();
                        if (!rowsCaptura.length) return null;
                        base = Object.assign({}, baseMinima, baseCardData(card));
                        var item = Object.assign({}, base);
                        rowsCaptura.forEach(function (row) { preencherDadosAvantCard(item, row); });
                        var temDados = item.vendas !== null && item.vendas !== undefined && item.vendas !== ''
                            || item.media_mensal !== null && item.media_mensal !== undefined && item.media_mensal !== ''
                            || item.vendedor
                            || item.data_criacao
                            || item.visitas !== null && item.visitas !== undefined && item.visitas !== '';
                        if (!temDados) return null;
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
                        item.chave_canonica = canonical;
                        item.chaveCanonica = canonical;
                        item.__capturadoDoPainelAvant = true;
                        item.__capturadoEm = Date.now();
                        var cache = window.__JK_AVANT_CARD_DATA_CACHE || {};
                        cache[canonical] = Object.assign({}, cache[canonical] || {}, item);
                        window.__JK_AVANT_CARD_DATA_CACHE = cache;
                        window.__JK_AVANT_LAST_PANEL_SNAPSHOT = finalSnapshot;`);
})(window);
