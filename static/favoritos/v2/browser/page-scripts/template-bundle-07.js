(function (global) {
  'use strict';
  const pageScripts = global.FavoritosV2.browser.pageScripts;
  pageScripts.registerPart('extrair-cards-mercado-livre-basico-webview-1', 0, `
                (function () {
                    var limite = __JK_EXTRAIR_CARDS_MERCADO_LIVRE_BASICO_WEBVIEW_1_P0__;
                    var cleanUrl = function (href) {
                        href = String(href || '').split('#')[0].trim();
                        if (!href) return '';
                        if (/^\\/\\//.test(href)) return 'https:' + href;
                        if (/^\\//.test(href)) return 'https://www.mercadolivre.com.br' + href;
                        return href;
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
                        } catch (e) {
                            return href;
                        }
                    };
                    var normalizar = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (e) {}
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
                            } catch (e) {}
                        };
                        visit(root || document);
                        return found;
                    };
                    var extrairId = function (value) {
                        var text = String(value || '');
                        try { text = decodeURIComponent(text); } catch (e) {}
                        var match = text.match(/\\bMLB-?(\\d{6,})\\b/i) || text.match(/[?&](?:wid|item_id)=(MLB\\d{6,})/i);
                        if (!match) return '';
                        var raw = String(match[1] || '').replace('-', '').toUpperCase();
                        return raw.indexOf('MLB') === 0 ? raw : ('MLB' + raw);
                    };
                    var isProductUrlBasico = function (href) {
                        var url = cleanUrl(href);
                        if (!url || url.toLowerCase().indexOf('mercadolivre.com.br') < 0) return false;
                        var urlLower = url.toLowerCase();
                        var hasExplicitItemSignal = (
                            /\\bMLB-?\\d{6,}\\b/i.test(url) ||
                            /\\/p\\/MLB/i.test(url) ||
                            /\\/up\\/MLB[A-Z0-9]*/i.test(url) ||
                            /[?&](?:wid|item_id)=MLB\\d{6,}/i.test(url) ||
                            /produto\\.mercadolivre\\.com\\.br/i.test(url)
                        );
                        if (urlLower.indexOf('https://lista.mercadolivre.com.br/') === 0 || urlLower.indexOf('http://lista.mercadolivre.com.br/') === 0) return hasExplicitItemSignal;
                        if (/\\/(?:ajuda|ofertas|cupons|categorias|supermercado|moda|mercado-play|vender|contato|compras|favoritos|gz|jms|login|registration|cart|publicidade|navigation|perfil|stores?|loja|post-purchase)\\b/i.test(url)) return false;
                        return hasExplicitItemSignal;
                    };
                    var isNavUrl = function (href) {
                        var url = cleanUrl(href).toLowerCase();
                        return !isProductUrlBasico(url);
                    };
                    var tituloFraco = function (value) {
                        var norm = normalizar(value);
                        if (!norm) return true;
                        if (/^(jm|novo|usado|patrocinado|mais vendido|r\\$|frete|chegar|vendid|mercado livre|favoritos|compras|produto relacionado|opcoes de compra)$/i.test(norm)) return true;
                        if (/^mlb\\d+$/i.test(norm.replace(/-/g, ''))) return true;
                        return norm.length <= 3;
                    };
                    var tituloDoHref = function (href) {
                        var text = String(href || '');
                        try { text = decodeURIComponent(text); } catch (e) {}
                        var match = text.match(/\\/MLB-?\\d+-([^?#]+?)(?:-_?JM|_JM|$)/i)
                            || text.match(/mercadolivre\\.com\\.br\\/([^/?#]+?)\\/up\\/MLB[A-Z0-9]+/i)
                            || text.match(/\\/([^/?#]+?)\\/up\\/MLB[A-Z0-9]+/i);
                        if (!match || !match[1]) return '';
                        var titulo = String(match[1])
                            .replace(/[-_]+/g, ' ')
                            .replace(/\\bJM\\b/ig, ' ')
                            .replace(/\\s+/g, ' ')
                            .trim();
                        return tituloFraco(titulo) ? '' : titulo.slice(0, 240);
                    };
                    var tituloDe = function (card, hrefProduto) {
                        var candidatos = []
                            .concat(Array.prototype.slice.call(card.querySelectorAll('h2, h3')))
                            .concat(Array.prototype.slice.call(card.querySelectorAll('[class*="title"], [class*="name"], [class*="poly-component__title"], [class*="ui-search-item__title"], [data-testid*="title"], [aria-label]')))
                            .concat(Array.prototype.slice.call(card.querySelectorAll('a[href]')));
                        candidatos.push(card);
                        for (var i = 0; i < candidatos.length; i += 1) {
                            var el = candidatos[i];
                            var text = String((el && (
                                el.getAttribute && (el.getAttribute('title') || el.getAttribute('aria-label')) ||
                                el.innerText ||
                                el.textContent
                            )) || '').replace(/\\s+/g, ' ').trim();
                            if (text.length >= 8 && !tituloFraco(text)) {
                                return text.slice(0, 240);
                            }
                        }
                        var tituloHref = tituloDoHref(hrefProduto);
                        if (tituloHref) return tituloHref;
                        return '';
                    };
                    var hrefDe = function (card) {
                        var links = Array.prototype.slice.call(card.querySelectorAll('a[href]'));
                        for (var i = 0; i < links.length; i += 1) {
                            var rawHref = links[i].href || links[i].getAttribute('href') || '';
                            if (!isNavUrl(rawHref)) return rawHref;
                        }
                        return '';
                    };
                    var linksProdutoDe = function (root) {
                        var links = [];
                        try {
                            if (root && root.matches && root.matches('a[href]')) links.push(root);
                        } catch (_selfLinkErr) {}
                        try {
                            links = links.concat(Array.prototype.slice.call(root && root.querySelectorAll ? root.querySelectorAll('a[href], [data-href], [data-url]') : []));
                        } catch (_linksErr) {}
                        var vistosLinks = {};
                        return links.map(function (node) {
                            return cleanUrl(node && (
                                node.href
                                || node.getAttribute && (node.getAttribute('href') || node.getAttribute('data-href') || node.getAttribute('data-url'))
                                || ''
                            ) || '');
                        }).filter(function (href) {
                            if (!href || isNavUrl(href)) return false;
                            var limpo = cleanProductUrl(href, extrairId(href)).toLowerCase();
                            if (!limpo || vistosLinks[limpo]) return false;
                            vistosLinks[limpo] = true;
                            return true;
                        });
                    };
                    var quantidadeLinksProduto = function (root) {
                        return linksProdutoDe(root).length;
                    };
                    var rootMuitoGenerico = function (root) {
                        if (!root || root === document || root === document.body || root === document.documentElement) return true;
                        var tag = String(root.tagName || '').toUpperCase();
                        if (/^(HTML|BODY|MAIN|OL|UL|NAV|HEADER|FOOTER|FORM)$/.test(tag)) return true;
                        try {
                            var rect = root.getBoundingClientRect && root.getBoundingClientRect();
                            var area = rect ? Math.max(0, rect.width) * Math.max(0, rect.height) : 0;
                            var viewportArea = Math.max(1, (window.innerWidth || 1280) * (window.innerHeight || 900));
                            if (area > viewportArea * 1.8 && quantidadeLinksProduto(root) > 1) return true;
                        } catch (_areaErr) {}
                        return false;
                    };
                    var temSinalProdutoVisual = function (root) {
                        if (!root || rootMuitoGenerico(root)) return false;
                        var texto = normalizar(root.innerText || root.textContent || '');
                        if (/categorias|ofertas|cupons|compras|favoritos|ordenar por|dados carregados|avantpro control/.test(texto) && quantidadeLinksProduto(root) !== 1) return false;
                        var temTitulo = !!tituloDe(root, linksProdutoDe(root)[0] || '');
                        var temImagem = !!imagemDe(root);
                        var precos = precosDe(root);
                        var temPreco = precos && precos.preco !== null && precos.preco !== undefined;
                        return quantidadeLinksProduto(root) === 1 && (temTitulo || temImagem || temPreco);
                    };
                    var imagemValida = function (url) {
                        var text = String(url || '').trim();
                        if (!text || /^data:/i.test(text)) return false;
                        if (text.indexOf('http://') !== 0 && text.indexOf('https://') !== 0 && text.indexOf('//') !== 0) return false;
                        return !/(logo|avatar|sprite|icon|favicon|badge|medal|avantpro|meliplus)/i.test(text);
                    };
                    var primeiraSrcset = function (value) {
                        var text = String(value || '').trim();
                        if (!text) return '';
                        var partes = text.split(',').map(function (item) {
                            var bits = item.trim().split(/\\s+/);
                            return {
                                url: bits[0] || '',
                                peso: parseFloat((bits[1] || '').replace(/[^\\d.]/g, '')) || 0
                            };
                        }).filter(function (item) { return imagemValida(item.url); });
                        partes.sort(function (a, b) { return b.peso - a.peso; });
                        return partes[0] && partes[0].url || '';
                    };
                    var imagemDe = function (card) {
                        var imgs = Array.prototype.slice.call(card.querySelectorAll('img, source[srcset], source[data-srcset]'));
                        var candidatos = imgs.map(function (img, index) {
                            var srcset = img.getAttribute && (img.getAttribute('srcset') || img.getAttribute('data-srcset'));
                            var url = primeiraSrcset(srcset)
                                || String(img.currentSrc || '')
                                || String(img.src || '')
                                || String(img.getAttribute && (img.getAttribute('src') || img.getAttribute('data-src') || img.getAttribute('data-original') || img.getAttribute('data-lazy') || '') || '');
                            var rect = null;
                            try { rect = img.getBoundingClientRect && img.getBoundingClientRect(); } catch (e) {}
                            var area = rect ? Math.max(0, rect.width) * Math.max(0, rect.height) : 0;
                            return { url: url, area: area, index: index };
                        }).filter(function (item) {
                            return imagemValida(item.url);
                        });
                        candidatos.sort(function (a, b) {
                            return b.area - a.area || a.index - b.index;
                        });
                        return candidatos[0] && candidatos[0].url || '';
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
                    var precosDe = function (card) {
                        var selectorsPreco = [
                            '.poly-price__current .andes-money-amount',
                            '.poly-component__price .andes-money-amount',
                            '.ui-search-price__second-line .andes-money-amount',
                            '[class*="price"] .andes-money-amount',
                            '.andes-money-amount',
                            '.price-tag'
                        ].join(',');
                        var precoPrincipalDireto = Array.prototype.slice.call(card.querySelectorAll ? card.querySelectorAll('.poly-price__current .andes-money-amount, .ui-search-price__second-line .andes-money-amount') : [])
                            .map(function (node) { return parsePrecoTexto(node && (node.innerText || node.textContent)); })
                            .filter(function (value) { return value !== null && value > 0; });
                        var nodes = Array.prototype.slice.call(card.querySelectorAll ? card.querySelectorAll(selectorsPreco) : [])
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
                            var direto = String(card.innerText || card.textContent || '').split(/frete|comiss[aã]o|faturamento|taxa|total\\s+de\\s+vendas/i)[0];
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
                        '[data-testid*="item"]',
                        '[class*="product-card"]',
                        '[class*="poly-card"]',
                        '[class*="ui-search-layout__item"]',
                        '[class*="ui-search-layout"] > li',
                        '[class*="shops__layout-item"]',
                        '[data-testid*="result"]',
                        'main ol > li',
                        'main ul > li'
                    ].join(',');
                    var cardMaisProximo = function (anchor) {
                        if (!anchor || !anchor.closest) return anchor;
                        return anchor.closest([
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
                        ].join(',')) || anchor;
                    };
                    var cards = queryAllDeep(selectors);
                    queryAllDeep('a[href]').forEach(function (anchor) {
                        var href = cleanUrl(anchor.href || anchor.getAttribute('href') || '');
                        if (isNavUrl(href)) return;
                        var card = cardMaisProximo(anchor);
                        if (card && cards.indexOf(card) < 0) cards.push(card);
                    });
                    var vistos = {};
                    var out = [];
                    var adicionarCardAoResultado = function (card, hrefPreferencial, origem) {
                        if (!card || out.length >= limite) return false;
                        var href = hrefPreferencial || hrefDe(card);
                        var id = extrairId(href || card.outerHTML || '');
                        href = cleanProductUrl(href, id);
                        var titulo = tituloDe(card, href);
                        if (!href && !id) return false;
                        var key = id ? ('mlb:' + id) : (href ? ('link:' + href.toLowerCase()) : '');
                        if (!key || vistos[key]) return false;
                        vistos[key] = true;
                        var imagem = imagemDe(card);
                        var precos = precosDe(card);
                        out.push({
                            posicao: out.length + 1,
                            id: id,
                            mlb: id,
                            url: href,
                            permalink: href,
                            link: href,
                            titulo: titulo,
                            title: titulo,
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
                            tituloFonte: titulo ? origem : '',
                            fotoFonte: imagem ? origem : '',
                            linkFonte: href ? origem : '',
                            precoFonte: precos.preco !== null ? origem : '',
                            fonte_preco: precos.preco !== null ? origem : '',
                            chave_canonica: key,
                            link_normalizado: href,
                            origem_dados: origem
                        });
                        return true;
                    };
                    var adicionarItemDireto = function (item, origem) {
                        if (!item || out.length >= limite) return false;
                        var href = cleanProductUrl(item.url || item.permalink || item.link || '', item.id || item.mlb || '');
                        var id = extrairId(item.id || item.mlb || href || '');
                        href = cleanProductUrl(href, id);
                        if (!href && !id) return false;
                        var key = id ? ('mlb:' + id) : (href ? ('link:' + href.toLowerCase()) : '');
                        if (!key || vistos[key]) return false;
                        var titulo = String(item.titulo || item.title || item.name || '').replace(/\\s+/g, ' ').trim();
                        if (tituloFraco(titulo)) titulo = tituloDoHref(href);
                        var imagem = '';
                        if (Array.isArray(item.imagem || item.image)) {
                            imagem = (item.imagem || item.image).filter(imagemValida)[0] || '';
                        } else {
                            imagem = String(item.imagem || item.image || item.thumbnail || item.foto || '').trim();
                        }
                        if (!imagemValida(imagem)) imagem = '';
                        var preco = item.preco;
                        if (preco === null || preco === undefined || preco === '') preco = item.price;
                        if (preco === null || preco === undefined || preco === '') preco = item.offers && item.offers.price;
                        if (preco === null || preco === undefined || preco === '') preco = null;
                        preco = preco === null || preco === undefined || preco === '' ? null : Number(String(preco).replace(/\\./g, '').replace(',', '.').replace(/[^\\d.]/g, ''));
                        if (!Number.isFinite(preco) || preco <= 0) preco = null;
                        vistos[key] = true;
                        out.push({
                            posicao: out.length + 1,
                            id: id,
                            mlb: id,
                            url: href,
                            permalink: href,
                            link: href,
                            titulo: titulo,
                            title: titulo,
                            imagem: imagem,
                            thumbnail: imagem,
                            foto: imagem,
                            preco: preco,
                            price: preco,
                            preco_original: '',
                            original_price: '',
                            preco_promocional: '',
                            promotional_price: '',
                            moeda: preco ? 'BRL' : '',
                            currency_id: preco ? 'BRL' : '',
                            tituloFonte: titulo ? origem : '',
                            fotoFonte: imagem ? origem : '',
                            linkFonte: href ? origem : '',
                            precoFonte: preco !== null ? origem : '',
                            fonte_preco: preco !== null ? origem : '',
                            chave_canonica: key,
                            link_normalizado: href,
                            origem_dados: origem
                        });
                        return true;
                    };
                    for (var c = 0; c < cards.length && out.length < limite; c += 1) {
                        adicionarCardAoResultado(cards[c], '', 'mercado_livre_dom');
                    }
                    if (out.length < limite) {
                        queryAllDeep('article, section, li, div, [role="listitem"], [class*="result"], [class*="card"]').slice(0, 2500).forEach(function (root) {
                            if (out.length >= limite || !temSinalProdutoVisual(root)) return;
                            adicionarCardAoResultado(root, linksProdutoDe(root)[0] || '', 'mercado_livre_dom_bloco_visual');
                        });
                    }
                    if (out.length < limite) {
                        var caminharJson = function (value, depth) {
                            if (!value || depth > 7 || out.length >= limite) return;
                            if (Array.isArray(value)) {
                                value.forEach(function (item) { caminharJson(item, depth + 1); });
                                return;
                            }
                            if (typeof value !== 'object') return;
                            var candidato = value.item && typeof value.item === 'object' ? value.item : value;
                            var href = candidato.url || candidato.permalink || candidato.link || value.url || '';
                            var nome = candidato.name || candidato.title || value.name || value.title || '';
                            var image = candidato.image || candidato.thumbnail || value.image || value.thumbnail || '';
                            var offers = candidato.offers || value.offers || {};
                            var preco = candidato.price || value.price || offers.price || '';
                            if (href && isProductUrlBasico(href)) {
                                adicionarItemDireto({
                                    url: href,
                                    titulo: nome,
                                    title: nome,
                                    imagem: image,
                                    image: image,
                                    preco: preco,
                                    price: preco
                                }, 'mercado_livre_json_ld');
                            }
                            Object.keys(value).slice(0, 80).forEach(function (key) {
                                caminharJson(value[key], depth + 1);
                            });
                        };
                        queryAllDeep('script[type="application/ld+json"], script[type="application/json"]').slice(0, 80).forEach(function (script) {
                            if (out.length >= limite) return;
                            var text = String(script && script.textContent || '').trim();
                            if (!text || text.length > 120000 || !/(MLB|mercadolivre|offers|ItemList|Product)/i.test(text)) return;
                            try {
                                caminharJson(JSON.parse(text), 0);
                            } catch (_jsonErr) {}
                        });
                        [
                            '__PRELOADED_STATE__',
                            '__STATE__',
                            '__APOLLO_STATE__',
                            '__NEXT_DATA__',
                            '__MELI_STATE__'
                        ].forEach(function (globalName) {
                            if (out.length >= limite) return;
                            try {
                                if (window[globalName]) caminharJson(window[globalName], 0);
                            } catch (_globalJsonErr) {}
                        });
                    }
                    if (out.length < limite) {
                        var textoParaLinks = '';
                        try {
                            textoParaLinks = [
                                document.documentElement && document.documentElement.innerHTML,
                                queryAllDeep('script').slice(0, 120).map(function (script) {
                                    return String(script && script.textContent || '').slice(0, 220000);
                                }).join(' ')
                            ].filter(Boolean).join(' ');
                        } catch (_htmlErr) {
                            textoParaLinks = '';
                        }
                        textoParaLinks = String(textoParaLinks || '')
                            .replace(/\\u002F/g, '/')
                            .replace(/\\\\\\\//g, '/')
                            .replace(/&amp;/g, '&')
                            .replace(/\\u0026/g, '&');
                        var regexUrls = /https?:\\/\\/(?:www\\.|lista\\.)?mercadolivre\\.com\\.br\\/[^"'<>\\s]*?(?:MLB-?\\d{6,}|\\/p\\/MLB\\d+|\\/up\\/MLB[A-Z0-9]+|item_id(?:%3A|:|=)MLB\\d+|wid=MLB\\d+)[^"'<>\\s]*/ig;
                        var regexRelativas = /\\/[A-Za-z0-9][^"'<>\\s]{8,}?\\/up\\/MLB[A-Z0-9]+[^"'<>\\s]*/ig;
                        var coletarRegex = function (regex) {
                            var match = null;
                            var guard = 0;
                            while (out.length < limite && guard < 400 && (match = regex.exec(textoParaLinks))) {
                                guard += 1;
                                var href = match && match[0] ? match[0] : '';
                                href = cleanProductUrl(href, extrairId(href));
                                if (!href || !isProductUrlBasico(href)) continue;
                                adicionarItemDireto({
                                    url: href,
                                    titulo: tituloDoHref(href)
                                }, 'mercado_livre_html_links');
                            }
                        };
                        coletarRegex(regexUrls);
                        coletarRegex(regexRelativas);
                    }
                    return {
                        success: true,
                        total: out.length,
                        anuncios: out,
                        debug: {
                            cardCount: cards.length,
                            linkCount: document.links ? document.links.length : 0,
                            productLinkCount: queryAllDeep('a[href], [data-href], [data-url]').filter(function (node) {
                                return linksProdutoDe(node).length > 0;
                            }).length,
                            title: document.title || '',
                            url: location.href
                        }
                    };
                })();
            `);
  pageScripts.registerPart('extrair-cache-avant-pro-cards-webview-1', 0, `
                (function () {
                    var cache = window.__JK_AVANT_CARD_DATA_CACHE || {};
                    var anuncios = Object.keys(cache)
                        .map(function (key) {
                            var item = cache[key];
                            if (!item || typeof item !== 'object') return null;
                            return Object.assign({}, item, {
                                chave_canonica: item.chave_canonica || key,
                                chaveCanonica: item.chaveCanonica || item.chave_canonica || key,
                                origem_dados: item.origem_dados || 'avantpro_card_panel_cache'
                            });
                        })
                        .filter(Boolean)
                        .slice(0, __JK_EXTRAIR_CACHE_AVANT_PRO_CARDS_WEBVIEW_1_P0__);
                    return {
                        success: true,
                        total: anuncios.length,
                        anuncios: anuncios
                    };
                })();
            `);
})(window);
