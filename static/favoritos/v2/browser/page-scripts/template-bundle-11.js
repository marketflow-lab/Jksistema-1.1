(function (global) {
  'use strict';
  const pageScripts = global.FavoritosV2.browser.pageScripts;
  pageScripts.registerPart('diagnosticar-avant-pro-no-webview-1', 0, `
                (function () {
                    var AVANT_ID = 'jdefnfmbnchmnjkcknaadaddgjbgephh';
                    var normalizar = function (value) {
                        return String(value || '').replace(/\\s+/g, ' ').trim();
                    };
                    var normalizarBusca = function (value) {
                        var text = normalizar(value);
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (e) {}
                        return text.toLowerCase();
                    };
                    var queryAllDeep = function (selector, root) {
                        var found = [];
                        var seen = [];
                        var walk = function (base) {
                            if (!base || seen.indexOf(base) >= 0) return;
                            seen.push(base);
                            try {
                                if (base.querySelectorAll) {
                                    found = found.concat(Array.prototype.slice.call(base.querySelectorAll(selector)));
                                }
                            } catch (_err) {}
                            var nodes = [];
                            try {
                                nodes = base.querySelectorAll ? Array.prototype.slice.call(base.querySelectorAll('*')) : [];
                            } catch (_err2) {}
                            for (var i = 0; i < nodes.length; i += 1) {
                                if (nodes[i] && nodes[i].shadowRoot) walk(nodes[i].shadowRoot);
                            }
                        };
                        walk(root || document);
                        return found.filter(function (node, index) { return found.indexOf(node) === index; });
                    };
                    var textoDeep = function () {
                        var partes = [];
                        var vistos = [];
                        var incluir = function (value) {
                            var text = normalizar(value);
                            if (!text || vistos.indexOf(text) >= 0) return;
                            vistos.push(text);
                            partes.push(text);
                        };
                        incluir(document.body && (document.body.innerText || document.body.textContent));
                        queryAllDeep('*').slice(0, 1800).forEach(function (node) {
                            try {
                                incluir(node.innerText || node.textContent || node.value || '');
                                if (node.getAttribute) {
                                    incluir(node.getAttribute('aria-label'));
                                    incluir(node.getAttribute('title'));
                                    incluir(node.getAttribute('placeholder'));
                                    incluir(node.getAttribute('class'));
                                    incluir(node.getAttribute('id'));
                                }
                            } catch (_err) {}
                        });
                        return partes.join(' ');
                    };
                    var contextoNode = function (node) {
                        var root = null;
                        try {
                            root = node && node.closest && node.closest('form, article, section, aside, li, [role="dialog"], [class*="avant"], [id*="avant"], [class*="login"], [class*="auth"], [class*="poly-card"], [class*="ui-search-result"]');
                        } catch (_err) {}
                        var host = null;
                        try {
                            var nodeRoot = node && node.getRootNode ? node.getRootNode() : null;
                            host = nodeRoot && nodeRoot.host ? nodeRoot.host : null;
                        } catch (_err2) {}
                        return normalizarBusca([
                            node && (node.innerText || node.textContent || node.value),
                            node && node.getAttribute && node.getAttribute('aria-label'),
                            node && node.getAttribute && node.getAttribute('title'),
                            root && (root.innerText || root.textContent),
                            root && root.getAttribute && root.getAttribute('class'),
                            root && root.getAttribute && root.getAttribute('id'),
                            host && (host.innerText || host.textContent),
                            host && host.getAttribute && host.getAttribute('class'),
                            host && host.getAttribute && host.getAttribute('id')
                        ].filter(Boolean).join(' '));
                    };
                    var contemAvant = function (value) {
                        return /avant\\s*pro|avantpro|carregar\\s+dado?s?\\s+avant|informacoes?\\s+avant|ferramentas|vincular\\s+(?:conta|agora)|conectar\\s+conta|use\\s+gratis|usar\\s+gratis|rotulos\\s+visuais|atualizar\\s+dado?s?|extrair\\s+dado?s?/i.test(normalizarBusca(value));
                    };
                    var estaDentroDeCardProduto = function (node) {
                        try {
                            return !!(node && node.closest && node.closest('li.ui-search-layout__item, .poly-card, [class*="poly-card"], [class*="ui-search-result"], [data-testid*="result"], [data-testid*="card"], [class*="shops__layout-item"]'));
                        } catch (_err) {
                            return false;
                        }
                    };
                    var ehControleDadosAvant = function (value) {
                        return /carregar\\s+dado?s?\\s+avant|informacoes?\\s+avant|informacoes?\\s+avantpro|rotulos\\s+visuais|atualizar\\s+dado?s?\\s+avant|extrair\\s+dado?s?\\s+avant/i.test(normalizarBusca(value));
                    };
                    var contemDadosAnuncioAvant = function (value) {
                        return /vendas?\\s+do\\s+(?:produto|anuncio|item)|vendas?\\s+estimad|ritmo\\s+atual|faturamento\\s+do\\s+produto|nome\\s+do\\s+vendedor|visitas\\s+do\\s+anuncio|participacao|anuncio\\s+(?:ganhador\\s+)?criado\\s+em|reputacao\\s+do\\s+vendedor/i.test(normalizarBusca(value));
                    };
                    var contarRotulosAvantNoTexto = function (value) {
                        var busca = normalizarBusca(value);
                        var padroes = [
                            /vendas?\\s+do\\s+produto/,
                            /vendas?\\s+estimad/,
                            /ritmo\\s+atual/,
                            /visitas\\s+do\\s+anuncio/,
                            /participacao\\b/,
                            /\\bmarca\\b/,
                            /faturamento\\s+do\\s+produto/,
                            /nome\\s+do\\s+vendedor/,
                            /localizacao\\s+do\\s+vendedor/,
                            /\\bmarca\\b/,
                            /participacao\\b/,
                            /visitas\\s+do\\s+anuncio/,
                            /comissao\\b/,
                            /reputacao\\s+do\\s+vendedor/,
                            /anuncio\\s+(?:ganhador\\s+)?criado\\s+em/
                        ];
                        return padroes.reduce(function (total, regex) {
                            return total + (regex.test(busca) ? 1 : 0);
                        }, 0);
                    };
                    var deepText = textoDeep();
                    var bodyBusca = normalizarBusca(deepText);
                    var bodyHasAvantInfo = /informacoes?\\s+avant(?:\\s*pro|pro)?/.test(bodyBusca);
                    var loginRealRegex = /iniciar\\s+sessao|insira\\s+suas\\s+credenciais|seu\\s+e-?mail|seu\\s+email|senha|password/;
                    var conviteAvantRegex = /vincule\\s+o\\s+avantpro|vincular\\s+agora|comece\\s+a\\s+usar|use\\s+gratis|usar\\s+gratis|liberar\\s+os\\s+recursos|dica\\s+avantpro|tutoriais/;
                    var modalAvantPromocional = /avant\\s*pro|avantpro|avantprocloud/.test(bodyBusca)
                        && conviteAvantRegex.test(bodyBusca)
                        && !loginRealRegex.test(bodyBusca);
                    var avantLoginDialog = /avant\\s*pro|avantpro|avantprocloud/.test(bodyBusca)
                        && !modalAvantPromocional
                        && /vincular\\s+conta|entre\\s+na\\s+sua\\s+conta|iniciar\\s+sessao|insira\\s+suas\\s+credenciais|seu\\s+e-?mail|seu\\s+email|chame\\s+o\\s+suporte|nao\\s+possui\\s+uma\\s+conta|crie\\s+uma\\s+aqui/.test(bodyBusca);
                    var avantLoginEmailInputs = Array.prototype.slice.call(queryAllDeep('input:not([type="hidden"])')).filter(function (input) {
                        var attrs = normalizarBusca([
                            input.type,
                            input.name,
                            input.id,
                            input.className,
                            input.placeholder,
                            input.getAttribute && input.getAttribute('aria-label')
                        ].join(' '));
                        var root = input.closest && input.closest('form, [role="dialog"], [class*="avant"], [id*="avant"], [class*="modal"], [class*="login"], [class*="auth"]');
                        var ctx = normalizarBusca((root && root.innerText) || '');
                        return /email|e-?mail|mail/.test(attrs + ' ' + ctx)
                            && /avant\\s*pro|avantpro|iniciar\\s+sessao|credenciais/.test(ctx + ' ' + bodyBusca);
                    }).length;
                    var bodyDataLabels = contarRotulosAvantNoTexto(bodyBusca);
                    var cardSelectors = [
                        'li.ui-search-layout__item',
                        'div.ui-search-result__wrapper',
                        'div.ui-search-result',
                        'div.poly-card',
                        'section.poly-card',
                        '[class*="poly-card"]',
                        '[class*="ui-search-result"]',
                        '[class*="ui-search-layout__item"]',
                        '[class*="shops__layout-item"]',
                        '[class*="item__info"]',
                        '[class*="ui-search-gallery"]',
                        '[data-testid*="item"]',
                        '[data-testid*="card"]',
                        '[data-testid*="result"]',
                        'main ol > li',
                        'main ul > li'
                    ].join(',');
                    var productLinkSelectors = [
                        'a[href*="produto.mercadolivre.com.br/MLB-"]',
                        'a[href*="/MLB-"]',
                        'a[href*="/p/MLB"]',
                        'a[href*="/up/MLB"]',
                        'a[href*="item_id=MLB"]',
                        'a[href*="item_id%3AMLB"]',
                        'a[href*="wid=MLB"]'
                    ].join(',');
                    var productLinks = [];
                    try {
                        productLinks = Array.prototype.slice.call(document.querySelectorAll(productLinkSelectors)).filter(function (node) {
                            var href = String(node && node.href || node && node.getAttribute && node.getAttribute('href') || '');
                            return /(?:produto\\.mercadolivre\\.com\\.br\\/MLB-\\d+|\\/MLB-\\d+|\\/p\\/MLB\\d+|\\/up\\/MLB|item_id(?:=|%3A)MLB\\d+|wid=MLB\\d+)/i.test(href);
                        });
                    } catch (_linkErr) {}
                    var cardCount = 0;
                    try {
                        cardCount = document.querySelectorAll(cardSelectors).length;
                    } catch (_cardErr) {}
                    cardCount = Math.max(cardCount, productLinks.length);
                    var loadingScreen = false;
                    try {
                        loadingScreen = document.querySelectorAll('.ui-search-loading-screen, [class*="ui-search-loading-screen"], [class*="loading-screen"], .andes-progress-indicator-circular').length > 0 && cardCount <= 0;
                    } catch (_loadingErr) {}
                    var rows = queryAllDeep('.avantpro-product-info-row').length;
                    var widgets = queryAllDeep('[class*="avantpro"], [id*="avantpro"], [data-testid*="avantpro"], [class*="Avant"], [id*="Avant"]').length;
                    var actionButtons = 0;
                    var infoButtons = 0;
                    var accountLinkButtons = 0;
                    var accountLinkButtonsCards = 0;
                    var accountLinkButtonsGlobais = 0;
                    var botoesLoginSemSeparacao = 0;
                    var toolsButtons = 0;
                    Array.prototype.slice.call(queryAllDeep('button, a, input[type="button"], input[type="submit"], [role="button"]')).forEach(function (node) {
                        var text = [
                            node.innerText,
                            node.textContent,
                            node.value,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('title')
                        ].map(normalizar).join(' ');
                        var contexto = contextoNode(node);
                        if (contemAvant(text)) actionButtons += 1;
                        if (ehControleDadosAvant(text)) infoButtons += 1;
                        var busca = normalizarBusca(text);
                        var ehBotaoContaAvant = /vincular\\s+(?:conta|agora)|conectar\\s+conta|fazer\\s+login|entrar\\s+no\\s+avant|use\\s+gratis|usar\\s+gratis/.test(busca);
                        if (/^login$|\\blogin\\b/.test(busca)
                            && (/avant\\s*pro|avantpro|comece\\s+a\\s+usar|liberar\\s+os\\s+recursos|extensao/.test(contexto)
                                || (/avant\\s*pro|avantpro/.test(bodyBusca) && /comece\\s+a\\s+usar|entre\\s+na\\s+sua\\s+conta|liberar\\s+os\\s+recursos/.test(bodyBusca)))) ehBotaoContaAvant = true;
                        if (ehBotaoContaAvant) {
                            accountLinkButtons += 1;
                            if (estaDentroDeCardProduto(node)) {
                                accountLinkButtonsCards += 1;
                            } else if (contexto) {
                                accountLinkButtonsGlobais += 1;
                            } else {
                                botoesLoginSemSeparacao += 1;
                            }
                        }
                        if (/ferramentas|rotulos\\s+visuais|avant\\s*pro|avantpro/.test(busca)) toolsButtons += 1;
                    });
                    var taggedNodes = 0;
                    queryAllDeep('[class], [id], script').forEach(function (node) {
                        var text = [
                            node.id,
                            node.className,
                            node.getAttribute && node.getAttribute('src')
                        ].map(normalizar).join(' ');
                        if (contemAvant(text)) taggedNodes += 1;
                    });
                    var dataTextNodes = 0;
                    queryAllDeep('[class*="avant"], [id*="avant"], [class*="Avant"], [id*="Avant"], .created-time-card, .avantpro-product-info-row, .avantpro-product-info-row *').forEach(function (node) {
                        var text = normalizar(node.innerText || node.textContent || '');
                        if (text && contemDadosAnuncioAvant(text)) dataTextNodes += 1;
                    });
                    var extensionResources = 0;
                    try {
                        extensionResources = performance.getEntriesByType('resource').filter(function (entry) {
                            var name = String(entry && entry.name || '').toLowerCase();
                            return name.indexOf('chrome-extension://' + AVANT_ID) >= 0 || name.indexOf('avantpro') >= 0;
                        }).length;
                    } catch (_err) {}
                    var hasRealAvantData = rows > 0 || dataTextNodes > 0 || (!avantLoginDialog && bodyDataLabels >= 2);
                    var ok = hasRealAvantData || (!avantLoginDialog && infoButtons > 0);
                    var paginaComCards = cardCount > 0;
                    var avantLoginBloqueante = !hasRealAvantData && avantLoginDialog;
                    var avantLoginEmailInputsBloqueantes = hasRealAvantData ? 0 : avantLoginEmailInputs;
                    var bodySugereLoginAvant = !hasRealAvantData
                        && !modalAvantPromocional
                        && /avant\\s*pro|avantpro|avantprocloud/.test(bodyBusca)
                        && /login|entrar|comece\\s+a\\s+usar|use\\s+gratis|usar\\s+gratis|liberar\\s+os\\s+recursos|nao\\s+possui\\s+uma\\s+conta|crie\\s+uma\\s+aqui/.test(bodyBusca);
                    var extensionDetected = widgets > 0 || actionButtons > 0 || taggedNodes > 0 || extensionResources > 0 || bodyHasAvantInfo || bodyDataLabels > 0;
                    var accountLinkBloqueante = accountLinkButtons > 0 && !modalAvantPromocional;
                    var accountLinkButtonsGlobaisEfetivos = conviteAvantRegex.test(bodyBusca) && !loginRealRegex.test(bodyBusca)
                        ? 0
                        : accountLinkButtonsGlobais;
                    var loginClickAt = Number(window.__JK_AVANT_PRO_LOGIN_CLICKED_AT || 0);
                    var cardClickAt = Number(window.jkAvantCardClickedAt || window.__JK_AVANT_CARD_CLICKED_AT || 0);
                    var legacyPareceLogin = avantLoginBloqueante || bodySugereLoginAvant || accountLinkBloqueante;
                    var loginAvantClicadoRecentemente = loginClickAt > 0 && Date.now() - loginClickAt < __JK_DIAGNOSTICAR_AVANT_PRO_NO_WEBVIEW_1_P0__;
                    var reloadAposLoginAvantRecomendado = loginAvantClicadoRecentemente && (!hasRealAvantData && !avantLoginEmailInputsBloqueantes);
                    var loginGlobalPendente = !!(accountLinkButtonsGlobaisEfetivos > 0 || botoesLoginSemSeparacao > 0 || avantLoginBloqueante || avantLoginEmailInputsBloqueantes > 0 || bodySugereLoginAvant);
                    var apenasCardsPedemLogin = accountLinkButtonsCards > 0 && !loginGlobalPendente;
                    var loginAvantPendenteVisual = loginGlobalPendente;
                    return {
                        ok: !!ok,
                        rows: rows,
                        cardCount: cardCount,
                        productLinkCount: productLinks.length,
                        loadingScreen: !!loadingScreen,
                        widgets: widgets,
                        actionButtons: actionButtons,
                        infoButtons: infoButtons,
                        accountLinkButtons: accountLinkButtons,
                        accountLinkButtonsCards: accountLinkButtonsCards,
                        accountLinkButtonsGlobais: accountLinkButtonsGlobais,
                        botoesLoginSemSeparacao: botoesLoginSemSeparacao,
                        toolsButtons: toolsButtons,
                        needsAccountLink: !reloadAposLoginAvantRecomendado && loginAvantPendenteVisual && !hasRealAvantData,
                        accountActionRequired: !reloadAposLoginAvantRecomendado && loginAvantPendenteVisual && !hasRealAvantData,
                        hasAvantData: !!hasRealAvantData,
                        modalAvantPromocional: !!modalAvantPromocional,
                        avantLoginDialog: !!avantLoginBloqueante,
                        avantLoginEmailInputs: avantLoginEmailInputsBloqueantes,
                        bodySugereLoginAvant: !!bodySugereLoginAvant,
                        apenasCardsPedemLogin: !!apenasCardsPedemLogin,
                        reloadAposLoginAvantRecomendado: !!reloadAposLoginAvantRecomendado,
                        loginAvantClicadoRecentemente: !!loginAvantClicadoRecentemente,
                        legacyPareceLogin: !!legacyPareceLogin,
                        loginClickAt: loginClickAt,
                        cardClickAt: cardClickAt,
                        shellOnly: !ok && extensionDetected,
                        extensionDetected: !!extensionDetected,
                        dataTextNodes: dataTextNodes,
                        bodyHasAvantInfo: !!bodyHasAvantInfo,
                        bodyDataLabels: bodyDataLabels,
                        taggedNodes: taggedNodes,
                        extensionResources: extensionResources,
                        url: location.href,
                        title: document.title || ''
                    };
                })();
            `);
  pageScripts.registerPart('aguardar-primeiros-dados-avant-ou-cards-webview-1', 0, `
                (function () {
                    var timeoutMs = __JK_AGUARDAR_PRIMEIROS_DADOS_AVANT_OU_CARDS_WEBVIEW_1_P0__;
                    var idleMs = __JK_AGUARDAR_PRIMEIROS_DADOS_AVANT_OU_CARDS_WEBVIEW_1_P1__;
                    var pollMs = Math.max(120, Math.min(400, idleMs));
                    var startedAt = Date.now();
                    var timer = null;
                    var normalizarBusca = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                        return text.toLowerCase();
                    };
                    var queryAllDeepLocal = function (selector, root) {
                        var found = [];
                        var seen = [];
                        var walk = function (base) {
                            if (!base || seen.indexOf(base) >= 0) return;
                            seen.push(base);
                            try {
                                if (base.querySelectorAll) {
                                    found = found.concat(Array.prototype.slice.call(base.querySelectorAll(selector)));
                                }
                            } catch (_err) {}
                            var nodes = [];
                            try {
                                nodes = base.querySelectorAll ? Array.prototype.slice.call(base.querySelectorAll('*')) : [];
                            } catch (_err2) {}
                            for (var i = 0; i < nodes.length; i += 1) {
                                if (nodes[i] && nodes[i].shadowRoot) walk(nodes[i].shadowRoot);
                            }
                        };
                        walk(root || document);
                        return found.filter(function (node, index) { return found.indexOf(node) === index; });
                    };
                    var textoDeepLocal = function () {
                        var partes = [];
                        var vistos = [];
                        var incluir = function (value) {
                            var text = String(value || '').replace(/\\s+/g, ' ').trim();
                            if (!text || vistos.indexOf(text) >= 0) return;
                            vistos.push(text);
                            partes.push(text);
                        };
                        incluir(document.body && (document.body.innerText || document.body.textContent));
                        queryAllDeepLocal('*').slice(0, 1500).forEach(function (node) {
                            try {
                                incluir(node.innerText || node.textContent || node.value || '');
                                if (node.getAttribute) {
                                    incluir(node.getAttribute('aria-label'));
                                    incluir(node.getAttribute('title'));
                                    incluir(node.getAttribute('class'));
                                    incluir(node.getAttribute('id'));
                                }
                            } catch (_err) {}
                        });
                        return partes.join(' ');
                    };
                    var contarRotulosAvant = function (value) {
                        var busca = normalizarBusca(value);
                        var padroes = [
                            /informacoes?\\s+avant(?:\\s*pro|pro)?/,
                            /vendas?\\s+do\\s+produto/,
                            /vendas?\\s+estimad/,
                            /ritmo\\s+atual/,
                            /visitas\\s+do\\s+anuncio/,
                            /participacao\\b/,
                            /\\bmarca\\b/,
                            /faturamento\\s+do\\s+produto/,
                            /nome\\s+do\\s+vendedor/,
                            /localizacao\\s+do\\s+vendedor/,
                            /anuncio\\s+(?:ganhador\\s+)?criado\\s+em/,
                            /comissao\\b/
                        ];
                        return padroes.reduce(function (total, regex) {
                            return total + (regex.test(busca) ? 1 : 0);
                        }, 0);
                    };
                    var snapshot = function () {
                        var bodyText = textoDeepLocal();
                        var busca = normalizarBusca(bodyText);
                        var cardSelectors = [
                            'li.ui-search-layout__item',
                            'div.ui-search-result__wrapper',
                            'div.ui-search-result',
                            'div.poly-card',
                            'section.poly-card',
                            'article.poly-card',
                            'article.ui-search-result',
                            '.poly-card__content',
                            '.poly-component__title',
                            '[class*="poly-card__content"]',
                            '[class*="poly-component__title"]',
                            '[class*="ui-search-result__content"]',
                            '[class*="ui-search-item__title"]',
                            '[class*="product-card"]',
                            '[class*="andes-card"]',
                            '[class*="poly-card"]',
                            '[class*="ui-search-result"]',
                            '[class*="ui-search-layout__item"]',
                            '[class*="shops__layout-item"]',
                            '[class*="item__info"]',
                            '[class*="ui-search-gallery"]',
                            '[data-testid*="item"]',
                            '[data-testid*="card"]',
                            '[data-testid*="result"]',
                            'main ol > li',
                            'main ul > li'
                        ].join(',');
                        var productLinkSelectors = [
                            'a[href*="produto.mercadolivre.com.br/MLB-"]',
                            'a[href*="/MLB-"]',
                            'a[href*="/p/MLB"]',
                            'a[href*="/up/MLB"]',
                            'a[href*="item_id=MLB"]',
                            'a[href*="item_id%3AMLB"]',
                            'a[href*="wid=MLB"]'
                        ].join(',');
                        var productLinks = [];
                        try {
                            productLinks = Array.prototype.slice.call(document.querySelectorAll(productLinkSelectors)).filter(function (node) {
                                var href = String(node && node.href || node && node.getAttribute && node.getAttribute('href') || '');
                                return /(?:produto\\.mercadolivre\\.com\\.br\\/MLB-\\d+|\\/MLB-\\d+|\\/p\\/MLB\\d+|\\/up\\/MLB|item_id(?:=|%3A)MLB\\d+|wid=MLB\\d+)/i.test(href);
                            });
                        } catch (_linkErr) {}
                        var cardCount = 0;
                        try {
                            cardCount = document.querySelectorAll(cardSelectors).length;
                        } catch (_cardErr) {}
                        cardCount = Math.max(cardCount, productLinks.length);
                        var resultadoVisual = /\b\d+\s+resultados?\b/.test(busca)
                            || queryAllDeepLocal('h1, [class*="quantity-results"], [class*="ui-search-search-result"], [class*="breadcrumb__title"]').some(function (node) {
                                return /\b\d+\s+resultados?\b/.test(normalizarBusca(node && (node.innerText || node.textContent) || ''));
                            });
                        if (resultadoVisual && cardCount <= 0) cardCount = 1;
                        var cardLoginPanelsAvant = 0;
                        try {
                            Array.prototype.slice.call(document.querySelectorAll(cardSelectors)).forEach(function (card) {
                                var cardBusca = normalizarBusca(card && (card.innerText || card.textContent) || '');
                                if (/avant\\s*pro|avantpro|avantprocloud/.test(cardBusca)
                                    && /vincular\\s+(?:conta|agora)|conectar\\s+conta|fazer\\s+login|entrar\\s+no\\s+avant|comece\\s+a\\s+usar|liberar\\s+os\\s+recursos/.test(cardBusca)) {
                                    cardLoginPanelsAvant += 1;
                                }
                            });
                        } catch (_cardLoginErr) {}
                        var loadingScreen = false;
                        try {
                            loadingScreen = document.querySelectorAll('.ui-search-loading-screen, [class*="ui-search-loading-screen"], [class*="loading-screen"], .andes-progress-indicator-circular').length > 0 && cardCount <= 0 && !resultadoVisual;
                        } catch (_loadingErr) {}
                        var avantLabels = contarRotulosAvant(bodyText);
                        var rows = queryAllDeepLocal('.avantpro-product-info-row').length;
                        var needsLogin =
                            location.href.indexOf('/gz/account-verification') >= 0 ||
                            location.href.indexOf('/jms/mlb/lgz/login') >= 0 ||
                            busca.indexOf('para continuar, acesse sua conta') >= 0;
                        var avantLoginCandidate =
                            /avant\\s*pro|avantpro|avantprocloud/.test(busca) &&
                            /vincule\\s+o\\s+avantpro|vincular\\s+agora|vincular\\s+conta|comece\\s+a\\s+usar|entre\\s+na\\s+sua\\s+conta|liberar\\s+os\\s+recursos|iniciar\\s+sessao|insira\\s+suas\\s+credenciais|seu\\s+e-?mail|seu\\s+email|chame\\s+o\\s+suporte/.test(busca);
                        var hasAvantData = rows > 0 || (!avantLoginCandidate && avantLabels >= 2);
                        var needsAvantLoginGlobal = avantLoginCandidate && !hasAvantData;
                        var needsAvantLoginCards = cardLoginPanelsAvant > 0 && !hasAvantData;
                        var needsAvantLogin = needsAvantLoginGlobal || needsAvantLoginCards;
                        var resultadoMlVisivel = cardCount > 0 || productLinks.length > 0 || !!resultadoVisual;
                        var noResults = !loadingScreen && !resultadoMlVisivel && !hasAvantData && (
                            busca.indexOf('nao encontramos resultados') >= 0 ||
                            busca.indexOf('nao ha resultados') >= 0 ||
                            busca.indexOf('sem resultados') >= 0 ||
                            busca.indexOf('verifique a ortografia') >= 0
                        );
                        return {
                            ready: cardCount > 0 || avantLabels > 0 || rows > 0 || needsLogin || needsAvantLogin || noResults,
                            hasCards: cardCount > 0,
                            hasAvantData: hasAvantData,
                            cardCount: cardCount,
                            productLinkCount: productLinks.length,
                            loadingScreen: !!loadingScreen,
                            avantLabels: avantLabels,
                            rows: rows,
                            needsLogin: needsLogin,
                            needsAvantLogin: needsAvantLogin,
                            needsAvantLoginCards: needsAvantLoginCards,
                            cardLoginPanelsAvant: cardLoginPanelsAvant,
                            noResults: noResults,
                            elapsedMs: Date.now() - startedAt,
                            url: location.href,
                            title: document.title || ''
                        };
                    };
                    return new Promise(function (resolve) {
                        var done = false;
                        var readyAt = 0;
                        var finish = function (result) {
                            if (done) return;
                            done = true;
                            try { clearTimeout(timer); } catch (_err) {}
                            resolve(result || snapshot());
                        };
                        var check = function () {
                            if (done) return;
                            var atual = snapshot();
                            if (atual.ready) {
                                if (!readyAt) readyAt = Date.now();
                                if (Date.now() - readyAt >= idleMs) {
                                    finish(atual);
                                    return;
                                }
                            } else {
                                readyAt = 0;
                            }
                            var elapsedMs = Date.now() - startedAt;
                            if (elapsedMs >= timeoutMs) {
                                atual.timeout = true;
                                finish(atual);
                                return;
                            }
                            timer = setTimeout(check, Math.min(pollMs, Math.max(1, timeoutMs - elapsedMs)));
                        };
                        check();
                    });
                })();
            `);
  pageScripts.registerPart('diagnosticar-resultados-mercado-livre-webview-1', 0, `
                (function () {
                    var normalizarBusca = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                        return text.toLowerCase();
                    };
                    var busca = normalizarBusca(document.body && (document.body.innerText || document.body.textContent) || '');
                    var cardSelectors = [
                        'li.ui-search-layout__item',
                        'div.ui-search-result__wrapper',
                        'div.ui-search-result',
                        'div.poly-card',
                        'section.poly-card',
                        'article.poly-card',
                        'article.ui-search-result',
                        '.poly-card__content',
                        '.poly-component__title',
                        '[class*="poly-card__content"]',
                        '[class*="poly-component__title"]',
                        '[class*="ui-search-result__content"]',
                        '[class*="ui-search-item__title"]',
                        '[class*="product-card"]',
                        '[class*="andes-card"]',
                        '[data-testid="product-card"]',
                        '[data-testid="item-card"]',
                        '[class*="poly-card"]',
                        '[class*="ui-search-result"]',
                        '[class*="ui-search-layout__item"]',
                        '[class*="shops__layout-item"]',
                        '[class*="item__info"]',
                        '[class*="ui-search-gallery"]',
                        '[data-testid*="item"]',
                        '[data-testid*="card"]',
                        '[data-testid*="result"]',
                        'main ol > li',
                        'main ul > li'
                    ].join(',');
                    var productLinkSelectors = [
                        'a[href*="produto.mercadolivre.com.br/MLB-"]',
                        'a[href*="/MLB-"]',
                        'a[href*="/p/MLB"]',
                        'a[href*="/up/MLB"]',
                        'a[href*="item_id=MLB"]',
                        'a[href*="item_id%3AMLB"]',
                        'a[href*="wid=MLB"]'
                    ].join(',');
                    var cardCount = 0;
                    try { cardCount = document.querySelectorAll(cardSelectors).length; } catch (_cardErr) {}
                    var productLinks = [];
                    try {
                        productLinks = Array.prototype.slice.call(document.querySelectorAll(productLinkSelectors)).filter(function (node) {
                            var href = String(node && node.href || node && node.getAttribute && node.getAttribute('href') || '');
                            return /(?:produto\\.mercadolivre\\.com\\.br\\/MLB-\\d+|\\/MLB-\\d+|\\/p\\/MLB\\d+|\\/up\\/MLB|item_id(?:=|%3A)MLB\\d+|wid=MLB\\d+)/i.test(href);
                        });
                    } catch (_linkErr) {}
                    cardCount = Math.max(cardCount, productLinks.length);
                    var resultadoVisual = /\b\d+\s+resultados?\b/.test(busca)
                        || Array.prototype.slice.call(document.querySelectorAll('h1, [class*="quantity-results"], [class*="ui-search-search-result"], [class*="breadcrumb__title"]')).some(function (node) {
                            return /\b\d+\s+resultados?\b/.test(normalizarBusca(node && (node.innerText || node.textContent) || ''));
                        });
                    if (resultadoVisual && cardCount <= 0) cardCount = 1;
                    var loadingScreen = false;
                    try {
                        loadingScreen = document.querySelectorAll('.ui-search-loading-screen, [class*="ui-search-loading-screen"], [class*="loading-screen"], .andes-progress-indicator-circular').length > 0 && cardCount <= 0 && !resultadoVisual;
                    } catch (_loadingErr) {}
                    var avantLabels = [
                        /vendas?\\s+do\\s+produto/,
                        /vendas?\\s+estimad/,
                        /ritmo\\s+atual/,
                        /visitas\\s+do\\s+anuncio/,
                        /nome\\s+do\\s+vendedor/,
                        /anuncio\\s+(?:ganhador\\s+)?criado\\s+em/
                    ].reduce(function (total, regex) {
                        return total + (regex.test(busca) ? 1 : 0);
                    }, 0);
                    var needsLogin =
                        location.href.indexOf('/gz/account-verification') >= 0 ||
                        location.href.indexOf('/jms/mlb/lgz/login') >= 0 ||
                        busca.indexOf('para continuar, acesse sua conta') >= 0;
                    var avantLoginCandidate =
                        /avant\\s*pro|avantpro|avantprocloud/.test(busca) &&
                        /vincule\\s+o\\s+avantpro|vincular\\s+agora|vincular\\s+conta|comece\\s+a\\s+usar|entre\\s+na\\s+sua\\s+conta|liberar\\s+os\\s+recursos|iniciar\\s+sessao|insira\\s+suas\\s+credenciais|seu\\s+e-?mail|seu\\s+email|chame\\s+o\\s+suporte/.test(busca);
                    var hasRealAvantData = avantLabels >= 2 && !avantLoginCandidate;
                    var resultadoMlVisivel = cardCount > 0 || productLinks.length > 0 || !!resultadoVisual;
                    var noResults = !loadingScreen && !resultadoMlVisivel && !hasRealAvantData && (
                        busca.indexOf('nao encontramos resultados') >= 0 ||
                        busca.indexOf('nao ha resultados') >= 0 ||
                        busca.indexOf('sem resultados') >= 0 ||
                        busca.indexOf('verifique a ortografia') >= 0
                    );
                    return {
                        url: location.href,
                        title: document.title || '',
                        cardCount: cardCount,
                        productLinkCount: productLinks.length,
                        resultadoVisual: !!resultadoVisual,
                        hasCards: cardCount > 0,
                        hasAvantData: hasRealAvantData,
                        avantLabels: avantLabels,
                        loadingScreen: !!loadingScreen,
                        noResults: !!noResults,
                        needsLogin: !!needsLogin
                    };
                })();
            `);
})(window);
