(function (global) {
  'use strict';
  const pageScripts = global.FavoritosV2.browser.pageScripts;
  pageScripts.registerPart('extrair-anuncios-avant-pro-dom-webview-1', 0, `
                (function () {
                    var limite = __JK_EXTRAIR_ANUNCIOS_AVANT_PRO_DOM_WEBVIEW_1_P0__;
                    var normalizar = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                        return text.toLowerCase();
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
                    var cleanUrl = function (href) {
                        href = String(href || '').split('#')[0].trim();
                        if (!href) return '';
                        if (/^\\/\\//.test(href)) return 'https:' + href;
                        if (/^\\//.test(href)) return 'https://www.mercadolivre.com.br' + href;
                        return href;
                    };
                    var extrairId = function (value) {
                        var text = String(value || '');
                        try { text = decodeURIComponent(text); } catch (_err) {}
                        var match = text.match(/\\bMLB-?(\\d{6,})\\b/i) || text.match(/[?&](?:wid|item_id)=(MLB\\d{6,})/i);
                        if (!match) return '';
                        var raw = String(match[1] || '').replace('-', '').toUpperCase();
                        return raw.indexOf('MLB') === 0 ? raw : ('MLB' + raw);
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
                    var isProductUrl = function (href) {
                        var url = cleanUrl(href);
                        if (!url || url.toLowerCase().indexOf('mercadolivre.com.br') < 0) return false;
                        var hasExplicitItemSignal = /\\bMLB-?\\d{6,}\\b/i.test(url)
                            || /[?&](?:wid|item_id)=MLB\\d{6,}/i.test(url)
                            || /\\/p\\/MLB/i.test(url)
                            || /\\/up\\/MLB[A-Z0-9]*/i.test(url)
                            || /produto\\.mercadolivre\\.com\\.br/i.test(url);
                        if (/https?:\\/\\/lista\\.mercadolivre\\.com\\.br\\//i.test(url)) return hasExplicitItemSignal;
                        if (/\\/(?:ajuda|ofertas|cupons|categorias|supermercado|moda|mercado-play|vender|contato|compras|favoritos|login|registration|cart|publicidade|navigation|perfil|stores?|loja|post-purchase)\\b/i.test(url)) return false;
                        return hasExplicitItemSignal;
                    };
                    var tituloDoHref = function (href) {
                        var text = String(href || '');
                        try { text = decodeURIComponent(text); } catch (_err) {}
                        var match = text.match(/\\/MLB-?\\d+-([^?#]+?)(?:-_?JM|_JM|$)/i)
                            || text.match(/mercadolivre\\.com\\.br\\/([^/?#]+?)\\/up\\/MLB[A-Z0-9]+/i)
                            || text.match(/\\/([^/?#]+?)\\/up\\/MLB[A-Z0-9]+/i);
                        if (!match || !match[1]) return '';
                        return String(match[1])
                            .replace(/[-_]+/g, ' ')
                            .replace(/\\bJM\\b/ig, ' ')
                            .replace(/\\s+/g, ' ')
                            .trim()
                            .slice(0, 240);
                    };
                    var tituloFraco = function (value) {
                        var text = normalizar(value);
                        if (!text) return true;
                        if (/^(jm|novo|usado|patrocinado|mais vendido|r\\$|frete|chegar|vendid|mercado livre|favoritos|compras|produto relacionado|opcoes de compra)$/i.test(text)) return true;
                        if (/^mlb\\d+$/i.test(text.replace(/-/g, ''))) return true;
                        return text.length <= 3;
                    };
                    var parseHumanNumber = function (value, suffix, decimal) {
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
                    var numeroNoTexto = function (text, regex, decimal) {
                        var match = String(text || '').match(regex);
                        return match && match[1] ? parseHumanNumber(match[1], match[2], decimal) : null;
                    };
                    var parsePrecoTexto = function (valor) {
                        var match = String(valor || '').match(/R\\$\\s*([0-9.]+)(?:\\s*,\\s*([0-9]{1,2}))?/);
                        if (!match) return null;
                        var inteiro = String(match[1] || '').replace(/\\./g, '');
                        var cents = String(match[2] || '0').padEnd(2, '0').slice(0, 2);
                        var value = Number(inteiro + '.' + cents);
                        return Number.isFinite(value) ? value : null;
                    };
                    var nodeDentroAvant = function (node) {
                        var atual = node;
                        for (var i = 0; atual && i < 8; i += 1) {
                            var cls = String(atual.className || '');
                            var id = String(atual.id || '');
                            if (/avant|created-time-card|product-info-row|faturamento|comissao|frete/i.test(cls + ' ' + id)) return true;
                            atual = atual.parentElement;
                        }
                        return false;
                    };
                    var textoPrecoIndesejado = function (node) {
                        var texto = String(node && (node.innerText || node.textContent) || '').replace(/\\s+/g, ' ');
                        var parent = node && node.parentElement ? String(node.parentElement.innerText || node.parentElement.textContent || '').replace(/\\s+/g, ' ') : texto;
                        return /frete|comiss[aã]o|faturamento|taxa|categoria|total\\s+de\\s+vendas|vendas\\s+do\\s+produto|ritmo|visitas/i.test(texto + ' ' + parent);
                    };
                    var parsePrecoNode = function (node) {
                        if (!node || nodeDentroAvant(node) || textoPrecoIndesejado(node)) return null;
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
                        return parsePrecoTexto(textNode);
                    };
                    var isPrecoOriginalNode = function (node) {
                        var atual = node;
                        for (var i = 0; atual && i < 5; i += 1) {
                            var info = String(atual.className || '') + ' ' + String(atual.getAttribute && (atual.getAttribute('aria-label') || atual.getAttribute('role') || '') || '');
                            if (/previous|original|old|strikethrough|discount|antes|tachado/i.test(info)) return true;
                            atual = atual.parentElement;
                        }
                        return false;
                    };
                    var isPrecoPrincipalNode = function (node) {
                        var atual = node;
                        for (var i = 0; atual && i < 5; i += 1) {
                            var cls = String(atual.className || '');
                            if (/poly-price__current|ui-search-price__second-line/i.test(cls)) return true;
                            atual = atual.parentElement;
                        }
                        return false;
                    };
                    var isPrecoSecundarioNode = function (node) {
                        var atual = node;
                        for (var i = 0; atual && i < 5; i += 1) {
                            var info = String(atual.className || '') + ' ' + String(atual.innerText || atual.textContent || '');
                            if (/installments|parcel|per[_-]?quantity|price-per-quantity|levando\\s+\\d+\\s+ou\\s+mais|\\b\\d+x\\s*r\\$/i.test(info)) return true;
                            atual = atual.parentElement;
                        }
                        return false;
                    };
                    var precosDe = function (root) {
                        var selectorsPreco = [
                            '.poly-price__current .andes-money-amount',
                            '.poly-component__price .andes-money-amount',
                            '.ui-search-price__second-line .andes-money-amount',
                            '[class*="price"] .andes-money-amount',
                            '.andes-money-amount',
                            '.price-tag'
                        ].join(',');
                        var precoPrincipalDireto = Array.prototype.slice.call(root && root.querySelectorAll ? root.querySelectorAll('.poly-price__current .andes-money-amount, .ui-search-price__second-line .andes-money-amount') : [])
                            .map(function (node) { return parsePrecoTexto(node && (node.innerText || node.textContent)); })
                            .filter(function (value) { return value !== null && value > 0; });
                        var nodes = Array.prototype.slice.call(root && root.querySelectorAll ? root.querySelectorAll(selectorsPreco) : [])
                            .filter(function (node) { return !nodeDentroAvant(node) && !textoPrecoIndesejado(node); });
                        var atuaisPrincipais = [];
                        var atuais = [];
                        var originais = [];
                        nodes.forEach(function (node) {
                            var value = parsePrecoNode(node);
                            if (value === null || value <= 0) return;
                            if (isPrecoOriginalNode(node)) originais.push(value);
                            else if (isPrecoPrincipalNode(node)) atuaisPrincipais.push(value);
                            else if (isPrecoSecundarioNode(node)) return;
                            else atuais.push(value);
                        });
                        var precoAtual = precoPrincipalDireto.length ? precoPrincipalDireto[0] : (atuaisPrincipais.length ? atuaisPrincipais[0] : (atuais.length ? atuais[0] : null));
                        var precoOriginal = originais.length ? originais[0] : '';
                        if (precoAtual === null) {
                            var direto = String(root && (root.innerText || root.textContent) || '').split(/frete|comiss[aã]o|faturamento|taxa|total\\s+de\\s+vendas/i)[0];
                            precoAtual = parsePrecoTexto(direto);
                        }
                        if (precoAtual === null) return { preco: null, preco_original: '', preco_promocional: '', moeda: '' };
                        if (precoOriginal && precoOriginal > precoAtual) {
                            return {
                                preco: precoAtual,
                                preco_original: precoOriginal,
                                preco_promocional: precoAtual,
                                moeda: 'BRL'
                            };
                        }
                        return { preco: precoAtual, preco_original: '', preco_promocional: '', moeda: 'BRL' };
                    };
                    var imagemValida = function (url) {
                        var text = String(url || '').trim();
                        if (!text || /^data:/i.test(text)) return false;
                        if (!/^https?:\\/\\//i.test(text) && !/^\\/\\//.test(text)) return false;
                        return !/(logo|avatar|sprite|icon|favicon|badge|medal|avantpro|meliplus)/i.test(text);
                    };
                    var imagemDe = function (root) {
                        var imgs = Array.prototype.slice.call(root && root.querySelectorAll ? root.querySelectorAll('img, source[srcset], source[data-srcset]') : []);
                        var candidatos = imgs.map(function (img, index) {
                            var srcset = img.getAttribute && (img.getAttribute('srcset') || img.getAttribute('data-srcset'));
                            var srcsetUrl = '';
                            if (srcset) {
                                srcsetUrl = String(srcset).split(',').map(function (part) {
                                    return part.trim().split(/\\s+/)[0] || '';
                                }).filter(imagemValida)[0] || '';
                            }
                            var url = srcsetUrl
                                || String(img.currentSrc || '')
                                || String(img.src || '')
                                || String(img.getAttribute && (img.getAttribute('src') || img.getAttribute('data-src') || img.getAttribute('data-original') || img.getAttribute('data-lazy') || '') || '');
                            var rect = null;
                            try { rect = img.getBoundingClientRect && img.getBoundingClientRect(); } catch (_err) {}
                            var area = rect ? Math.max(0, rect.width) * Math.max(0, rect.height) : 0;
                            return { url: url, area: area, index: index };
                        }).filter(function (item) { return imagemValida(item.url); });
                        candidatos.sort(function (a, b) { return b.area - a.area || a.index - b.index; });
                        return candidatos[0] && candidatos[0].url || '';
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
                        'main ul > li',
                        'li',
                        'article',
                        'section'
                    ].join(',');
                    var linksProdutoDe = function (root) {
                        var links = Array.prototype.slice.call(root && root.querySelectorAll ? root.querySelectorAll('a[href]') : []);
                        var out = [];
                        for (var i = 0; i < links.length; i += 1) {
                            var rawHref = links[i].href || links[i].getAttribute('href') || '';
                            if (isProductUrl(rawHref)) out.push(rawHref);
                        }
                        return out;
                    };
                    var linkProdutoDe = function (root) {
                        return linksProdutoDe(root)[0] || '';
                    };
                    var chavesProdutoDe = function (root) {
                        var vistos = {};
                        return linksProdutoDe(root).map(function (href) {
                            var id = extrairId(href);
                            return id ? ('mlb:' + id) : ('link:' + cleanProductUrl(href, '').toLowerCase());
                        }).filter(function (key) {
                            if (!key || vistos[key]) return false;
                            vistos[key] = true;
                            return true;
                        });
                    };
                    var rootGenericoDemais = function (node) {
                        var tag = String(node && node.tagName || '').toUpperCase();
                        return !node || node === document.body || node === document.documentElement || /^(HTML|BODY|MAIN|OL|UL)$/.test(tag);
                    };
                    var rootProdutoSeguro = function (root) {
                        return !!(root && !rootGenericoDemais(root) && chavesProdutoDe(root).length === 1);
                    };
                    var tituloDe = function (root, href) {
                        var candidatos = []
                            .concat(Array.prototype.slice.call(root && root.querySelectorAll ? root.querySelectorAll('h2, h3, [class*="title"], [class*="name"], [class*="poly-component__title"], [class*="ui-search-item__title"], [data-testid*="title"], a[href]') : []));
                        candidatos.push(root);
                        for (var i = 0; i < candidatos.length; i += 1) {
                            var el = candidatos[i];
                            var text = String((el && (
                                el.getAttribute && (el.getAttribute('title') || el.getAttribute('aria-label')) ||
                                el.innerText ||
                                el.textContent
                            )) || '').replace(/\\s+/g, ' ').trim();
                            if (text.length >= 8 && !tituloFraco(text)) return text.slice(0, 240);
                        }
                        return tituloDoHref(href);
                    };
                    var nearestProductLink = function (node) {
                        var links = queryAllDeep('a[href]').filter(function (link) {
                            return isProductUrl(link.href || link.getAttribute('href') || '');
                        });
                        var nr = null;
                        try { nr = node.getBoundingClientRect && node.getBoundingClientRect(); } catch (_err) {}
                        if (!nr) return '';
                        var best = null;
                        links.forEach(function (link) {
                            var lr = null;
                            try { lr = link.getBoundingClientRect && link.getBoundingClientRect(); } catch (_err) {}
                            if (!lr || !lr.width || !lr.height) return;
                            var dy = Math.abs(((lr.top + lr.bottom) / 2) - ((nr.top + nr.bottom) / 2));
                            var dx = Math.abs(((lr.left + lr.right) / 2) - ((nr.left + nr.right) / 2));
                            var score = dy + dx * 0.35;
                            if (!best || score < best.score) best = { href: link.href || link.getAttribute('href') || '', score: score };
                        });
                        return best && best.href || '';
                    };
                    var rootDe = function (node) {
                        var root = node && node.closest && node.closest(cardSelectors);
                        if (rootProdutoSeguro(root)) return root;
                        var atual = node;
                        for (var i = 0; atual && i < 6; i += 1) {
                            if (rootProdutoSeguro(atual)) return atual;
                            atual = atual.parentElement;
                        }
                        return root || node;
                    };
                    var visibleAvantNode = function (node) {
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
                    var linhasTextoAvant = function (node) {
                        return String(node && (node.innerText || node.textContent) || '')
                            .split(/\\n+/)
                            .map(function (line) { return line.replace(/\\s+/g, ' ').trim(); })
                            .filter(Boolean);
                    };
                    var definicoesLabelsAvant = [
                        { label: 'Vendas do produto', tokens: ['vendas do produto', 'venda do produto', 'vendas do anuncio', 'venda do anuncio', 'vendas do item', 'venda do item'] },
                        { label: 'Vendas estimadas', tokens: ['vendas estimad', 'venda estimad'] },
                        { label: 'Ritmo atual', tokens: ['ritmo atual', 'media mensal', 'vendas/mes', 'vendas mes'] },
                        { label: 'Nome do vendedor', tokens: ['nome do vendedor'] },
                        { label: 'Localizacao do vendedor', tokens: ['localizacao do vendedor'] },
                        { label: 'Anuncio criado em', tokens: ['anuncio criado em', 'catalogo criado em', 'criado em'] },
                        { label: 'Visitas do anuncio', tokens: ['visitas do anuncio', 'visitas'] },
                        { label: 'Participacao', tokens: ['participacao'] },
                        { label: 'Comissao', tokens: ['comissao'] },
                        { label: 'Reputacao do vendedor', tokens: ['reputacao do vendedor'] },
                        { label: 'Frete', tokens: ['frete'] },
                        { label: 'Taxa da categoria', tokens: ['taxa da categoria'] },
                        { label: 'Marca', tokens: ['marca'] }
                    ];
                    var labelAvantPorLinha = function (line) {
                        var norm = normalizar(line);
                        for (var i = 0; i < definicoesLabelsAvant.length; i += 1) {
                            var def = definicoesLabelsAvant[i];
                            for (var j = 0; j < def.tokens.length; j += 1) {
                                var token = def.tokens[j];
                                if (norm === token || norm.indexOf(token + ' ') === 0 || norm.indexOf(token + ':') === 0) return def;
                            }
                        }
                        return null;
                    };
                    var linhaEhLabelAvant = function (line) {
                        return !!labelAvantPorLinha(line);
                    };
                    var textoValorNaMesmaLinhaAvant = function (line, def) {
                        var text = String(line || '').trim();
                        var norm = normalizar(text);
                        var melhor = '';
                        (def && def.tokens || []).forEach(function (token) {
                            if (norm.indexOf(token) !== 0) return;
                            var resto = text.slice(token.length).replace(/^[\\s:=-]+/, '').trim();
                            if (resto && resto.length > melhor.length) melhor = resto;
                        });
                        return melhor;
                    };
                    var cardProdutoMaisProximoDe = function (node) {
                        var candidatos = queryAllDeep(cardSelectors).filter(rootProdutoSeguro).filter(visibleAvantNode);
                        if (!candidatos.length) return null;
                        var nr = null;
                        try { nr = node && node.getBoundingClientRect && node.getBoundingClientRect(); } catch (_err) {}
                        if (!nr) return candidatos[0];
                        var ncx = (nr.left + nr.right) / 2;
                        var ncy = (nr.top + nr.bottom) / 2;
                        var best = null;
                        candidatos.forEach(function (card) {
                            var cr = null;
                            try { cr = card.getBoundingClientRect && card.getBoundingClientRect(); } catch (_err) {}
                            if (!cr) return;
                            var ccx = (cr.left + cr.right) / 2;
                            var ccy = (cr.top + cr.bottom) / 2;
                            var score = Math.abs(ccy - ncy) + Math.abs(ccx - ncx) * 0.25;
                            if (!best || score < best.score) best = { card: card, score: score };
                        });
                        return best && best.card || candidatos[0];
                    };
                    var extrairLinhasVirtuaisAvant = function (root) {
                        var linhas = linhasTextoAvant(root);
                        if (!linhas.length) return [];
                        var rootProduto = rootProdutoSeguro(root) ? root : cardProdutoMaisProximoDe(root);
                        var out = [];
                        for (var i = 0; i < linhas.length; i += 1) {
                            var line = linhas[i];
                            var def = labelAvantPorLinha(line);
                            if (!def) continue;
                            var value = textoValorNaMesmaLinhaAvant(line, def);
                            if (!value) {
                                for (var j = i + 1; j < Math.min(linhas.length, i + 5); j += 1) {
                                    var candidato = linhas[j];
                                    if (!candidato || linhaEhLabelAvant(candidato)) break;
                                    if (/^(informacoes?\\s+avant(?:pro)?|carregar\\s+dados\\s+avantpro?)$/i.test(normalizar(candidato))) continue;
                                    value = candidato;
                                    break;
                                }
                            }
                            out.push({
                                __jkLabel: def.label,
                                __jkValue: value || '',
                                __jkText: [def.label, value || ''].filter(Boolean).join(' '),
                                __jkRoot: rootProduto || root
                            });
                        }
                        return out;
                    };
                    var coletarLinhasVirtuaisAvant = function () {
                        var candidatos = queryAllDeep('[class*="avant"], [class*="Avant"], [role="dialog"], aside, section, div')
                            .filter(function (node) {
                                if (!visibleAvantNode(node) || rootGenericoDemais(node)) return false;
                                var text = normalizar(node.innerText || node.textContent || '');
                                if (!/avant|vendas?\\s+do\\s+produto|ritmo\\s+atual|nome\\s+do\\s+vendedor|anuncio\\s+criado\\s+em/.test(text)) return false;
                                var labels = 0;
                                definicoesLabelsAvant.forEach(function (def) {
                                    if (def.tokens.some(function (token) { return text.indexOf(token) >= 0; })) labels += 1;
                                });
                                return labels >= 2;
                            });
                        var usados = [];
                        var linhas = [];
                        candidatos.sort(function (a, b) {
                            var ar = null;
                            var br = null;
                            try { ar = a.getBoundingClientRect && a.getBoundingClientRect(); } catch (_err) {}
                            try { br = b.getBoundingClientRect && b.getBoundingClientRect(); } catch (_err) {}
                            var aa = ar ? ar.width * ar.height : 0;
                            var ba = br ? br.width * br.height : 0;
                            return aa - ba;
                        }).forEach(function (node) {
                            if (usados.some(function (parent) { return parent !== node && parent.contains && parent.contains(node); })) return;
                            var extraidas = extrairLinhasVirtuaisAvant(node);
                            if (extraidas.length < 2) return;
                            usados.push(node);
                            linhas = linhas.concat(extraidas);
                        });
                        return linhas;
                    };
                    var labelValue = function (row) {
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
                        return { label: label, value: value, text: text };
                    };
                    var labelTem = function (label, partes) {
                        var n = normalizar(label);
                        return partes.some(function (parte) { return n.indexOf(parte) >= 0; });
                    };
                    var preencherPorLinha = function (grupo, row) {
                        var lv = labelValue(row);
                        var label = lv.label;
                        var value = lv.value;
                        var text = lv.text;
                        var busca = normalizar(label + ' ' + text);
                        if (!value && label && text.indexOf(label) >= 0) {
                            value = text.slice(text.indexOf(label) + label.length).replace(/^\\s*[:\\-]?\\s*/, '').trim();
                        }
                        if (labelTem(label || busca, ['vendas do produto', 'venda do produto', 'vendas do anuncio', 'venda do anuncio', 'vendas do item', 'venda do item'])) {
                            var vendas = parseHumanNumber(value || text, '', false);
                            if (Number.isFinite(vendas)) grupo.vendas = vendas;
                        } else if (labelTem(label || busca, ['vendas estimad', 'venda estimad'])) {
                            var estimadas = parseHumanNumber(value || text, '', false);
                            if (Number.isFinite(estimadas) && !Number.isFinite(grupo.vendas)) grupo.vendas = estimadas;
                            if (Number.isFinite(estimadas)) grupo.vendas_estimadas = estimadas;
                        } else if (labelTem(label || busca, ['ritmo atual'])) {
                            var ritmo = parseHumanNumber(value || text, '', true);
                            if (Number.isFinite(ritmo)) {
                                grupo.media_mensal = ritmo;
                                grupo.ritmo_atual = ritmo;
                            }
                        } else if (labelTem(label || busca, ['reputacao do vendedor'])) {
                            grupo.reputacao_vendedor = value || '';
                        } else if (labelTem(label || busca, ['localizacao do vendedor'])) {
                            grupo.localizacao_vendedor = value || '';
                        } else if (labelTem(label || busca, ['nome do vendedor']) || (labelTem(label || busca, ['vendedor']) && !labelTem(label || busca, ['reputacao do vendedor', 'localizacao do vendedor']))) {
                            var vendedor = String(value || '').replace(/^\\s*[:\\-]?\\s*/, '').trim();
                            if (vendedor && vendedor.length <= 120 && !/^\\d+$/.test(vendedor)) grupo.vendedor = vendedor;
                        } else if (labelTem(label || busca, ['anuncio criado em', 'catalogo criado em', 'criado em'])) {
                            var data = String(value || text || '').match(/\\b\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}\\b|\\b20\\d{2}-\\d{2}-\\d{2}\\b/);
                            if (data && data[0]) grupo.data_criacao = data[0];
                        } else if (labelTem(label || busca, ['visitas do anuncio', 'visitas'])) {
                            var visitas = parseHumanNumber(value || text, '', false);
                            if (Number.isFinite(visitas)) grupo.visitas = visitas;
                        } else if (labelTem(label || busca, ['participacao'])) {
                            grupo.participacao = value || '';
                        } else if (labelTem(label || busca, ['comissao'])) {
                            grupo.comissao = value || '';
                        }
                    };
                    var rows = queryAllDeep('.avantpro-product-info-row, .created-time-card, [class*="avantpro-product-info"], [class*="created-time-card"], [class*="Avantpro"][class*="row"], [class*="avantpro"][class*="row"]');
                    rows = rows.concat(coletarLinhasVirtuaisAvant());
                    var mapa = {};
                    var ordem = [];
                    try {
                        var cachePainel = window.__JK_AVANT_CARD_DATA_CACHE || {};
                        Object.keys(cachePainel).forEach(function (cacheKey) {
                            var cached = Object.assign({}, cachePainel[cacheKey] || {});
                            var id = extrairId(cached.id || cached.mlb || cached.url || cached.link || cached.permalink || cacheKey);
                            var href = cleanProductUrl(cached.url || cached.permalink || cached.link || '', id);
                            var key = id ? ('mlb:' + id) : (href ? ('link:' + href.toLowerCase()) : (/^(?:mlb|link):/i.test(cacheKey) ? cacheKey.toLowerCase().replace(/^mlb:/, 'mlb:') : ''));
                            if (!key) return;
                            if (id) {
                                cached.id = id;
                                cached.mlb = id;
                            }
                            if (href) {
                                cached.url = href;
                                cached.permalink = href;
                                cached.link = href;
                            }
                            cached.origem_dados = cached.origem_dados || 'avantpro_card_panel_cache';
                            cached.chave_canonica = key;
                            cached.chaveCanonica = key;
                            if (!mapa[key]) {
                                mapa[key] = cached;
                                ordem.push(key);
                            } else {
                                mapa[key] = Object.assign({}, mapa[key], cached);
                            }
                        });
                    } catch (_cacheErr) {}
                    rows.forEach(function (row) {
                        var text = String(row && (row.__jkText || row.innerText || row.textContent) || '').replace(/\\s+/g, ' ').trim();
                        if (!/vendas?|ritmo|vendedor|criado|visitas|participa|comissao|reputacao/i.test(normalizar(text))) return;
                        var root = row && row.__jkRoot || rootDe(row);
                        var href = linkProdutoDe(root) || nearestProductLink(row);
                        var id = extrairId(href || (root && root.outerHTML) || (row && row.outerHTML) || '');
                        href = cleanProductUrl(href, id);
                        var key = id ? ('mlb:' + id) : (href ? ('link:' + href.toLowerCase()) : '');
                        if (!key) return;
                        if (!mapa[key]) {
                            var precos = precosDe(root);
                            mapa[key] = {
                                posicao: ordem.length + 1,
                                id: id,
                                mlb: id,
                                url: href,
                                permalink: href,
                                link: href,
                                titulo: tituloDe(root, href),
                                title: tituloDe(root, href),
                                imagem: imagemDe(root),
                                thumbnail: imagemDe(root),
                                foto: imagemDe(root),
                                preco: precos.preco,
                                price: precos.preco_promocional || precos.preco,
                                preco_original: precos.preco_original,
                                original_price: precos.preco_original,`);
  pageScripts.registerPart('extrair-anuncios-avant-pro-dom-webview-1', 1, `                                preco_promocional: precos.preco_promocional,
                                promotional_price: precos.preco_promocional,
                                moeda: precos.moeda,
                                currency_id: precos.moeda,
                                tituloFonte: 'mercado_livre_dom_contexto_avant',
                                fotoFonte: imagemDe(root) ? 'mercado_livre_dom_contexto_avant' : '',
                                linkFonte: href ? 'mercado_livre_dom_contexto_avant' : '',
                                precoFonte: precos.preco !== null ? 'mercado_livre_dom_contexto_avant' : '',
                                fonte_preco: precos.preco !== null ? 'mercado_livre_dom_contexto_avant' : '',
                                origem_dados: 'avantpro_dom',
                                vendasFonte: '',
                                vendas_fonte: '',
                                vendedorFonte: '',
                                vendedor_fonte: ''
                            };
                            ordem.push(key);
                        }
                        preencherPorLinha(mapa[key], row);
                    });
                    var anuncios = ordem.map(function (key) {
                        var item = mapa[key];
                        var texto = [
                            item.vendas,
                            item.media_mensal,
                            item.vendedor,
                            item.data_criacao,
                            item.visitas,
                            item.participacao
                        ].join(' ');
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
                        return item;
                    }).filter(function (item) {
                        return item && (item.vendasFonte || item.vendedorFonte || item.data_criacao || item.media_mensal || item.url || item.id);
                    }).slice(0, limite);
                    return {
                        success: true,
                        total: anuncios.length,
                        anuncios: anuncios,
                        debug: {
                            mode: 'avantpro_dom',
                            rows: rows.length,
                            url: location.href,
                            title: document.title || ''
                        }
                    };
                })();
            `);
})(window);
