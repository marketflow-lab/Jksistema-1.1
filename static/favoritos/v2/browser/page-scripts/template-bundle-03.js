(function (global) {
  'use strict';
  const pageScripts = global.FavoritosV2.browser.pageScripts;
  pageScripts.registerPart('montar-script-acionar-controles-avant-pro-1', 0, `
                (async function () {
                    var clicked = 0;
                    var forceClick = __JK_MONTAR_SCRIPT_ACIONAR_CONTROLES_AVANT_PRO_1_P0__;
                    var permitirFerramentas = __JK_MONTAR_SCRIPT_ACIONAR_CONTROLES_AVANT_PRO_1_P1__;
                    var somenteFerramentas = __JK_MONTAR_SCRIPT_ACIONAR_CONTROLES_AVANT_PRO_1_P2__;
                    var clicarCardsSemDados = __JK_MONTAR_SCRIPT_ACIONAR_CONTROLES_AVANT_PRO_1_P3__;
                    var maxClicks = __JK_MONTAR_SCRIPT_ACIONAR_CONTROLES_AVANT_PRO_1_P4__;
                    var lastClickAt = Number(window.__JK_AVANT_PRO_CLICKED_AT || 0);
                    if (!forceClick && lastClickAt && Date.now() - lastClickAt < 3500) return 0;
                    var sleep = function (ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); };
                    var normalizar = function (value) {
                        var text = String(value || '');
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (e) {}
                        return text.toLowerCase().replace(/\\s+/g, ' ').trim();
                    };
                    var isVisible = function (node) {
                        try {
                            var rect = node && node.getBoundingClientRect ? node.getBoundingClientRect() : null;
                            var style = node && window.getComputedStyle ? window.getComputedStyle(node) : null;
                            return !!(rect && rect.width > 0 && rect.height > 0 && (!style || (style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) !== 0)));
                        } catch (_err) {
                            return false;
                        }
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
                    var composedHost = function (node) {
                        try {
                            var root = node && node.getRootNode ? node.getRootNode() : null;
                            return root && root.host ? root.host : null;
                        } catch (_err) {
                            return null;
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
                    var textoVisivelNode = function (node) {
                        if (!node) return '';
                        var text = [
                            node.innerText,
                            node.value,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('title')
                        ].filter(Boolean).join(' ');
                        if (!text && node.textContent) text = node.textContent;
                        return normalizar(text);
                    };
                    var contextoNode = function (node) {
                        if (!node) return '';
                        var root = null;
                        try {
                            root = node.closest && node.closest('form, article, section, aside, li, [role="dialog"], [class*="avant"], [id*="avant"], [class*="poly-card"], [class*="ui-search-result"], [class*="modal"], [class*="login"], [class*="auth"]');
                        } catch (_err) {}
                        var host = composedHost(node);
                        root = root || node.parentElement || node;
                        return normalizar([
                            textoNode(node),
                            root && (root.innerText || root.textContent),
                            root && root.getAttribute && root.getAttribute('class'),
                            root && root.getAttribute && root.getAttribute('id'),
                            host && textoNode(host),
                            host && (host.innerText || host.textContent)
                        ].filter(Boolean).join(' '));
                    };
                    var ehLinkProduto = function (node) {
                        var href = node && node.getAttribute && node.getAttribute('href');
                        if (!href) return false;
                        return /\\/MLB-?\\d{5,}|\\/p\\/MLB|wid=MLB|item_id=/i.test(String(href));
                    };
                    var hrefNode = function (node) {
                        if (!node || !node.getAttribute) return '';
                        return String(node.href || node.getAttribute('href') || node.getAttribute('data-href') || '').trim();
                    };
                    var textoAlvoClique = function (node) {
                        if (!node) return '';
                        return normalizar([
                            node.innerText,
                            node.textContent,
                            node.value,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('title'),
                            node.getAttribute && node.getAttribute('class'),
                            node.getAttribute && node.getAttribute('id'),
                            node.getAttribute && node.getAttribute('data-testid'),
                            node.getAttribute && node.getAttribute('download'),
                            hrefNode(node)
                        ].filter(Boolean).join(' '));
                    };
                    var textoExplicitoClique = function (node) {
                        if (!node) return '';
                        return normalizar([
                            node.innerText,
                            node.textContent,
                            node.value,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('title'),
                            node.getAttribute && node.getAttribute('data-testid')
                        ].filter(Boolean).join(' '));
                    };
                    var ehAncoraNavegavel = function (node) {
                        var tag = String(node && node.tagName || '').toUpperCase();
                        var href = hrefNode(node);
                        return tag === 'A' && !!href && !/^javascript:/i.test(href);
                    };
                    var ehDownloadOuMidiaAvant = function (node) {
                        if (!node) return false;
                        var href = hrefNode(node);
                        var alvo = textoAlvoClique(node);
                        var hrefNormalizado = normalizar(href);
                        try {
                            if (node.closest && node.closest('a[download], [download]')) return true;
                        } catch (_closestErr) {}
                        if (/^(blob|data):/i.test(href)) return true;
                        if (/\\.zip(?:$|[?#])|\\/download\\b|download=|filename=|imagens?\\.zip|images?\\.zip/i.test(href)) return true;
                        return /(^|[\\s_-])(baixar|download|imagens?|images?|fotos?|photos?|foto|photo|zip|exportar?|salvar|gallery|galeria)([\\s_-]|$)/.test(alvo + ' ' + hrefNormalizado);
                    };
                    var clicarCardsAvantSemDados = function (item) {
                        if (!clicarCardsSemDados || !item || !estaDentroDeCardProduto(item.node)) return false;
                        if (!/informacoes?\\s+avant|informacoes?\\s+avantpro|avantpro\\s+info|carregar\\s+dado?s?\\s+avant|atualizar\\s+dado?s?\\s+avant/.test(item.text + ' ' + item.context)) return false;
                        var jkAvantCardClickCount = Number(window.jkAvantCardClickCount || 0);
                        var jkAvantCardClickedAt = Number(window.jkAvantCardClickedAt || 0);
                        var ultimoClique = jkAvantCardClickedAt;
                        if (!forceClick && ultimoClique && Date.now() - ultimoClique < 1800) return false;
                        if (jkAvantCardClickCount > 0 && !forceClick && Date.now() - ultimoClique < 4500) return false;
                        return true;
                    };
                    var estaDentroDeCardProduto = function (node) {
                        try {
                            return !!(node && node.closest && node.closest('li.ui-search-layout__item, .poly-card, [class*="poly-card"], [class*="ui-search-result"], [data-testid*="result"], [data-testid*="card"]'));
                        } catch (_err) {
                            return false;
                        }
                    };
                    var ehAcaoVinculoConta = function (text, context) {
                        var alvo = (text || '') + ' ' + (context || '');
                        return /vincular\\s+(?:conta|agora)|conectar\\s+conta|autorizar\\s+(?:mercado\\s+livre|conta)|permitir\\s+acesso/.test(alvo)
                            && /avant\\s*pro|avantpro|mercado\\s+livre|conta|vincul/.test(alvo);
                    };
                    var ehMenuFlutuanteAvant = function (text, context) {
                        var alvo = (text || '') + ' ' + (context || '');
                        return /abrir\\s+menu\\s+avantpro|avantpro-floating-button|avantpro-speed-dial-fab|speed-dial/.test(alvo)
                            && /avant\\s*pro|avantpro|speed-dial/.test(alvo);
                    };
                    var ehFerramentasAvant = function (text, context, visibleText) {
                        var alvo = (text || '') + ' ' + (context || '');
                        var rotulo = visibleText || text || '';
                        if (/conectando\\s+avant|fazendo\\s+favorito|tentativa|sku\\s*\\d|aguarde|status/.test(alvo)) return false;
                        return /^(ferramentas|tools)$/.test(rotulo)
                            && /avant\\s*pro|avantpro|speed-dial|ferramentas/.test(alvo);
                    };
                    var scoreAcao = function (text, context, visibleText) {
                        if (!text) return 0;
                        if (somenteFerramentas) {
                            if (permitirFerramentas && ehFerramentasAvant(text, context, visibleText)) return 5;
                            if (permitirFerramentas && ehMenuFlutuanteAvant(text, context)) return 10;
                            return 0;
                        }
                        if (/assine\\s+ja|assinar|cancelar\\s+favoritos|ocultar/.test(text)) return 0;
                        if (ehAcaoVinculoConta(text, context)) return 0;
                        if (/\\blogin\\b|fazer\\s+login|entrar\\s+no\\s+avant|login\\s+avant/.test(text)) {
                            if (/avant\\s*pro|avantpro|comece\\s+a\\s+usar|liberar\\s+os\\s+recursos|extensao/.test(context || text)) return 0;
                            return 0;
                        }
                        if (/carregar\\s+dado?s?\\s+avant|atualizar\\s+dado?s?\\s+avant|extrair\\s+dado?s?\\s+avant/.test(text)) return 5;
                        if (/informacoes?\\s+avant|informacoes?\\s+avantpro|avantpro\\s+info/.test(text)) return 15;
                        if (/rotulos\\s+visuais|r[oó]tulos\\s+visuais/.test(text)) return 0;
                        if (permitirFerramentas && ehFerramentasAvant(text, context, visibleText)) return 70;
                        if (permitirFerramentas && ehMenuFlutuanteAvant(text, context)) return 80;
                        return 0;
                    };
                    var clicarCandidatos = async function (incluiFerramentas) {
                        var bodyAtual = normalizar(document.body && (document.body.innerText || document.body.textContent) || '');
                        if (/vincule\\s+o\\s+avantpro|vincular\\s+agora/.test(bodyAtual)
                            && /avant\\s*pro|avantpro/.test(bodyAtual)) {
                            window.__JK_AVANT_AUTO_CLICK_BLOCKED_BY_LINK_MODAL_AT = Date.now();
                            return 0;
                        }
                        var candidates = queryAllDeep('button, a, input[type="button"], input[type="submit"], [role="button"], [aria-label], [title], [class*="andes-button"], div, span').map(function (node) {
                            var text = textoNode(node);
                            var visibleText = textoVisivelNode(node);
                            var context = contextoNode(node);
                            var score = scoreAcao(text, context, visibleText);
                            return { node: node, text: text, visibleText: visibleText, context: context, score: score };
                        }).filter(function (item) {
                            var cardAvantSemDados = clicarCardsAvantSemDados(item);
                            if (!item.score || (((!forceClick && item.node.dataset.jkAvantClicked === '1') && !cardAvantSemDados))) return false;
                            if (ehDownloadOuMidiaAvant(item.node)) return false;
                            if (!somenteFerramentas && ehAncoraNavegavel(item.node)) return false;
                            if (!incluiFerramentas && item.score >= 70) return false;
                            if (ehLinkProduto(item.node)) return false;
                            if (somenteFerramentas && estaDentroDeCardProduto(item.node)) return false;
                            if (!somenteFerramentas && estaDentroDeCardProduto(item.node) && /informacoes?\\s+avant|informacoes?\\s+avantpro|avantpro\\s+info/.test(item.text) && !cardAvantSemDados) return false;
                            if (!isVisible(item.node)) return false;
                            if (ehFerramentasAvant(item.text, item.context, item.visibleText) || ehMenuFlutuanteAvant(item.text, item.context)) {
                                var rect = item.node.getBoundingClientRect ? item.node.getBoundingClientRect() : null;
                                if (!rect || rect.width < 40 || rect.height < 20) return false;
                            }
                            return true;
                        }).sort(function (a, b) {
                            return a.score - b.score;
                        });

                        var clicou = 0;
                        for (var i = 0; i < candidates.length && clicked < maxClicks; i += 1) {
                            var item = candidates[i];
                            item.node.dataset.jkAvantClicked = '1';
                            try {
                                item.node.scrollIntoView && item.node.scrollIntoView({ block: 'center', inline: 'center' });
                            } catch (_err) {}
                            try {
                                var rect = item.node.getBoundingClientRect ? item.node.getBoundingClientRect() : null;
                                var opts = rect ? { bubbles: true, cancelable: true, view: window, clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 } : { bubbles: true, cancelable: true, view: window };
                                window.__JK_AVANT_LAST_AUTO_CLICK = {
                                    at: Date.now(),
                                    text: String(item.text || '').slice(0, 160),
                                    visibleText: String(item.visibleText || '').slice(0, 160),
                                    score: item.score,
                                    insideCard: !!estaDentroDeCardProduto(item.node),
                                    context: String(item.context || '').slice(0, 220)
                                };
                                try { item.node.dispatchEvent(new MouseEvent('mousedown', opts)); } catch (_downErr) {}
                                try { item.node.dispatchEvent(new MouseEvent('mouseup', opts)); } catch (_upErr) {}
                                try { item.node.dispatchEvent(new MouseEvent('click', opts)); } catch (_clickErr) {}
                                item.node.click();
                                if (estaDentroDeCardProduto(item.node)) {
                                    window.jkAvantCardClickCount = Number(window.jkAvantCardClickCount || 0) + 1;
                                    window.jkAvantCardClickedAt = Date.now();
                                    window.__JK_AVANT_CARD_CLICKED_AT = window.jkAvantCardClickedAt;
                                }
                                clicked += 1;
                                clicou += 1;
                            } catch (_err) {}
                            if (item.score >= 70) break;
                            await sleep(120);
                        }
                        return clicou;
                    };

                    var primeiraRodada = await clicarCandidatos(somenteFerramentas);
                    if (primeiraRodada) {
                        await sleep(850);
                        await clicarCandidatos(somenteFerramentas);
                    }
                    if (!clicked && permitirFerramentas) {
                        var abriuFerramentas = await clicarCandidatos(true);
                        if (abriuFerramentas) {
                            await sleep(700);
                            await clicarCandidatos(somenteFerramentas);
                        }
                    }
                    if (clicked) window.__JK_AVANT_PRO_CLICKED_AT = Date.now();
                    return clicked;
                })();
            `);
  pageScripts.registerPart('montar-script-localizar-bolinha-avant-pro-1', 0, `
                (function () {
                    var normalizar = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                        return text.toLowerCase();
                    };
                    var visivel = function (node) {
                        if (!node || !node.getBoundingClientRect) return false;
                        var rect = node.getBoundingClientRect();
                        var style = window.getComputedStyle ? window.getComputedStyle(node) : null;
                        return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0
                            && rect.left < (window.innerWidth || document.documentElement.clientWidth || 0)
                            && rect.top < (window.innerHeight || document.documentElement.clientHeight || 0)
                            && (!style || (style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) !== 0));
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
                    var textoNode = function (node) {
                        if (!node) return '';
                        return [
                            node.innerText,
                            node.textContent,
                            node.value,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('title'),
                            node.getAttribute && node.getAttribute('class'),
                            node.getAttribute && node.getAttribute('id')
                        ].filter(Boolean).join(' ');
                    };
                    var contextoNode = function (node) {
                        var root = null;
                        try {
                            root = node && node.closest && node.closest('[id*="avant"], [class*="avant"], [class*="speed"], [class*="menu"], [role="dialog"], [aria-modal="true"]');
                        } catch (_err) {}
                        root = root || node && node.parentElement || node;
                        return normalizar([textoNode(node), root && textoNode(root), root && (root.innerText || root.textContent)].filter(Boolean).join(' '));
                    };
                    var dentroCardProduto = function (node) {
                        try {
                            return !!(node && node.closest && node.closest('li.ui-search-layout__item, .poly-card, [class*="poly-card"], [class*="ui-search-result"], [class*="dynamic-access"], [class*="recommend"], [class*="andes-card"], [data-testid*="result"], [data-testid*="card"]'));
                        } catch (_err) {
                            return false;
                        }
                    };
                    var vw = window.innerWidth || document.documentElement.clientWidth || 1280;
                    var vh = window.innerHeight || document.documentElement.clientHeight || 900;
                    var bodyBusca = normalizar(document.body && (document.body.innerText || document.body.textContent) || '');
                    var ferramentasVisivel = /\\bferramentas\\b/.test(bodyBusca)
                        && queryAllDeep('button, a, [role="button"], [aria-label], [title], [class*="avant"], [id*="avant"], [class*="speed"], div, span').some(function (node) {
                            if (!visivel(node) || dentroCardProduto(node)) return false;
                            var rect = node.getBoundingClientRect();
                            var texto = normalizar(textoNode(node));
                            var contexto = contextoNode(node);
                            if (/conectando\\s+avant|fazendo\\s+favorito|tentativa|sku\\s*\\d|aguarde|status/.test(texto + ' ' + contexto)) return false;
                            return /\\bferramentas\\b|\\btools\\b/.test(texto)
                                && rect.left > vw * 0.55
                                && /avant\\s*pro|avantpro|speed-dial|ferramentas/.test(texto + ' ' + contexto);
                        });
                    var candidatos = queryAllDeep('button, a, [role="button"], [tabindex], [aria-label], [title], [class*="avant"], [id*="avant"], [class*="speed"], div, span')
                        .map(function (node, index) {
                            if (!visivel(node) || dentroCardProduto(node)) return null;
                            var rect = node.getBoundingClientRect();
                            var texto = normalizar(textoNode(node));
                            var contexto = contextoNode(node);
                            var classeId = normalizar(String(node.className || '') + ' ' + String(node.id || ''));
                            var alvo = texto + ' ' + contexto + ' ' + classeId;
                            if (/assine\\s+ja|suporte|ferramentas|compras|favoritos/.test(texto)) return null;
                            var ehClasseBolinha = /avantpro-speed-dial-fab|speed-dial-fab|avantpro-floating-button|abrir\\s+menu\\s+avantpro|avantpro-menu/.test(alvo);
                            var temSinalAvant = /avant\\s*pro|avantpro|speed-dial|floating|abrir\\s+menu\\s+avantpro/.test(alvo);
                            var ehElementoMercadoLivre = /dynamic-access|andes-card|recommend|navigation|carousel|home|poly-card|ui-search|nav-/.test(alvo);
                            var ehTamanhoBolinha = rect.width >= 42 && rect.height >= 42 && rect.width <= 110 && rect.height <= 110;
                            var ehCantoInferiorDireito = rect.left > vw * 0.78 && rect.top > vh * 0.54;
                            var ehPosicaoFlutuante = false;
                            try {
                                var style = window.getComputedStyle ? window.getComputedStyle(node) : null;
                                ehPosicaoFlutuante = !!(style && /fixed|absolute|sticky/.test(String(style.position || '')));
                            } catch (_styleErr) {}
                            if (ehElementoMercadoLivre && !temSinalAvant) return null;
                            if (!ehClasseBolinha && !temSinalAvant) return null;
                            if (!ehClasseBolinha && !(ehTamanhoBolinha && ehCantoInferiorDireito && ehPosicaoFlutuante)) return null;
                            var score = 0;
                            if (ehClasseBolinha) score += 220;
                            if (temSinalAvant) score += 80;
                            if (rect.left > vw * 0.55) score += 40;
                            if (rect.top > vh * 0.45) score += 35;
                            if (ehTamanhoBolinha) score += 90;
                            if (ehCantoInferiorDireito) score += 120;
                            if (score <= 0) return null;
                            return {
                                node: node,
                                index: index,
                                score: score,
                                area: rect.width * rect.height,
                                x: rect.left + rect.width / 2,
                                y: rect.top + rect.height / 2,
                                width: rect.width,
                                height: rect.height,
                                label: String(textoNode(node) || '').replace(/\\s+/g, ' ').trim().slice(0, 120)
                            };
                        })
                        .filter(Boolean)
                        .sort(function (a, b) { return b.score - a.score || b.area - a.area || a.index - b.index; });
                    if (candidatos.length) {
                        var c = candidatos[0];
                        return {
                            success: true,
                            source: 'bolinha_avant_dom',
                            x: c.x,
                            y: c.y,
                            width: c.width,
                            height: c.height,
                            score: c.score,
                            label: c.label || 'Bolinha Avant Pro',
                            url: location.href
                        };
                    }
                    if (/avant\\s*pro|avantpro|assine\\s+ja|suporte/.test(bodyBusca)) {
                        return {
                            success: true,
                            source: 'bolinha_avant_estimado',
                            x: Math.max(40, vw - 84),
                            y: Math.max(40, vh - 84),
                            width: 64,
                            height: 64,
                            label: 'Bolinha Avant Pro',
                            url: location.href
                        };
                    }
                    return { success: false, reason: 'bolinha_avant_nao_localizada', url: location.href };
                })();
            `);
  pageScripts.registerPart('montar-script-localizar-ferramentas-avant-pro-1', 0, `
                (function () {
                    var normalizar = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                        return text.toLowerCase();
                    };
                    var visivel = function (node) {
                        if (!node || !node.getBoundingClientRect) return false;
                        var rect = node.getBoundingClientRect();
                        var style = window.getComputedStyle ? window.getComputedStyle(node) : null;
                        return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0
                            && rect.left < (window.innerWidth || document.documentElement.clientWidth || 0)
                            && rect.top < (window.innerHeight || document.documentElement.clientHeight || 0)
                            && (!style || (style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) !== 0));
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
                    var composedHost = function (node) {
                        try {
                            var root = node && node.getRootNode ? node.getRootNode() : null;
                            return root && root.host ? root.host : null;
                        } catch (_err) {
                            return null;
                        }
                    };
                    var textoBotao = function (node) {
                        if (!node) return '';
                        return [
                            node.innerText,
                            node.textContent,
                            node.value,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('title'),
                            node.getAttribute && node.getAttribute('class'),
                            node.getAttribute && node.getAttribute('id')
                        ].filter(Boolean).join(' ');
                    };
                    var textoVisivelBotao = function (node) {
                        if (!node) return '';
                        var text = [
                            node.innerText,
                            node.value,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('title')
                        ].filter(Boolean).join(' ');
                        if (!text && node.textContent) text = node.textContent;
                        return text;
                    };
                    var contextoBotao = function (node) {
                        var root = null;
                        try {
                            root = node && node.closest && node.closest('form, article, section, aside, li, [role="dialog"], [aria-modal="true"], [class*="avant"], [id*="avant"], [class*="speed"], [class*="menu"], [class*="modal"], [class*="popup"], [class*="drawer"], [class*="login"], [class*="auth"]');
                        } catch (_err) {}
                        var host = composedHost(node);
                        root = root || (node && node.parentElement) || node;
                        return normalizar([
                            textoBotao(node),
                            root && textoBotao(root),
                            root && (root.innerText || root.textContent),
                            host && textoBotao(host),
                            host && (host.innerText || host.textContent)
                        ].filter(Boolean).join(' '));
                    };
                    var dentroCardProduto = function (node) {
                        try {
                            return !!(node && node.closest && node.closest('li.ui-search-layout__item, .poly-card, [class*="poly-card"], [class*="ui-search-result"], [class*="dynamic-access"], [class*="recommend"], [class*="andes-card"], [data-testid*="result"], [data-testid*="card"]'));
                        } catch (_err) {
                            return false;
                        }
                    };
                    var escolherAlvoClique = function (node) {
                        var candidatos = [];
                        var incluir = function (item, bonus) {
                            if (!item || candidatos.indexOf(item) >= 0 || !visivel(item) || dentroCardProduto(item)) return;
                            var rect = item.getBoundingClientRect();
                            if (rect.width < 24 || rect.height < 12) return;
                            var texto = normalizar(textoVisivelBotao(item));
                            var contexto = contextoBotao(item);
                            var alvo = texto + ' ' + contexto;
                            if (!/^(ferramentas|tools)$/.test(texto)) return;
                            if (/assine\\s+ja|assinar|suporte/.test(texto) && !/^ferramentas$|^tools$/.test(texto)) return;
                            var area = rect.width * rect.height;
                            var score = Number(bonus) || 0;
                            if (/button|a/i.test(item.tagName || '')) score += 80;
                            if (item.getAttribute && item.getAttribute('role') === 'button') score += 70;
                            if (rect.width >= 70 && rect.height >= 28 && rect.width <= 280 && rect.height <= 120) score += 90;
                            if (/avant|speed|dial|menu|tool|ferramentas/.test(String(item.className || '') + ' ' + String(item.id || ''))) score += 50;
                            if (area > 1200 && area < 32000) score += 40;
                            candidatos.push({ node: item, score: score, area: area, rect: rect });
                        };
                        incluir(node, 0);
                        try {
                            incluir(node.closest && node.closest('button, a, [role="button"], [class*="speed-dial-action"], [class*="speed-dial-item"], [class*="floating-button"], [class*="avantpro"]'), 50);
                        } catch (_err) {}
                        var atual = node && node.parentElement;
                        for (var nivel = 0; atual && nivel < 5; nivel += 1) {
                            incluir(atual, 40 - nivel * 5);
                            atual = atual.parentElement;
                        }
                        candidatos.sort(function (a, b) {
                            return b.score - a.score || b.area - a.area;
                        });
                        return candidatos.length ? candidatos[0].node : node;
                    };
                    var vw = window.innerWidth || document.documentElement.clientWidth || 1280;
                    var vh = window.innerHeight || document.documentElement.clientHeight || 900;
                    var todosCandidatos = queryAllDeep('button, a, input[type="button"], input[type="submit"], [role="button"], [tabindex], [aria-label], [title], [class*="andes-button"], [class*="avant"], [id*="avant"], [class*="speed"], [class*="menu"], div, span')
                        .filter(function (node) {
                            if (!visivel(node)) return false;
                            if (dentroCardProduto(node)) return false;
                            var texto = normalizar(textoVisivelBotao(node));
                            var contexto = contextoBotao(node);
                            var alvo = texto + ' ' + contexto;
                            var rect = node.getBoundingClientRect();
                            if (/assine\\s+ja|assinar|suporte|compras|favoritos|categorias|ofertas/.test(texto)) return false;
                            if (/conectando\\s+avant|fazendo\\s+favorito|tentativa|sku\\s*\\d|aguarde|status/.test(alvo)) return false;
                            if (!/^(ferramentas|tools)$/.test(texto)) return false;
                            if (rect.left < Math.max(vw * 0.68, vw - 420)) return false;
                            if (rect.top < Math.max(120, vh - 230)) return false;
                            return /\\bferramentas\\b|\\btools\\b/.test(alvo);
                        })
                        .map(function (node, index) {
                            var clickNode = escolherAlvoClique(node);
                            var rect = clickNode.getBoundingClientRect();
                            var texto = normalizar(textoVisivelBotao(node));
                            var contexto = contextoBotao(clickNode);
                            var alvo = texto + ' ' + contexto;
                            var score = 0;
                            if (/^ferramentas$|^tools$/.test(texto)) score += 260;
                            if (/\\bferramentas\\b|\\btools\\b/.test(texto)) score += 180;
                            if (/avant\\s*pro|avantpro|speed-dial|menu/.test(alvo)) score += 80;
                            if (rect.left > vw * 0.55) score += 60;
                            if (rect.width >= 70 && rect.width <= 260 && rect.height >= 28 && rect.height <= 90) score += 35;
                            if (clickNode !== node && rect.width >= 70 && rect.height >= 24) score += 70;
                            if (rect.left > vw - 260) score += 35;
                            if (rect.top > 80 && rect.top < vh - 80) score += 20;
                            return {
                                node: clickNode,
                                index: index,
                                score: score,
                                x: rect.left + rect.width / 2,
                                y: rect.top + rect.height / 2,
                                width: rect.width,
                                height: rect.height,
                                label: String(textoVisivelBotao(node) || textoBotao(node) || '').replace(/\\s+/g, ' ').trim().slice(0, 120)
                            };
                        });
                    var candidatos = todosCandidatos
                        .filter(function (item) { return item.score > 0 && item.width >= 40 && item.height >= 20; })
                        .sort(function (a, b) { return b.score - a.score || a.index - b.index; });
                    if (candidatos.length) {
                        var c = candidatos[0];
                        return {
                            success: true,
                            source: 'ferramentas_dom',
                            x: c.x,
                            y: c.y,
                            width: c.width,
                            height: c.height,
                            score: c.score,
                            label: c.label || 'Ferramentas',
                            url: location.href
                        };
                    }
                    var bodyBusca = normalizar(document.body && (document.body.innerText || document.body.textContent) || '');
                    var rotuloFerramentasMenuVisivel = queryAllDeep('button, a, [role="button"], [aria-label], [title], [class*="avant"], [id*="avant"], [class*="speed"], [class*="menu"], div, span').some(function (node) {
                        if (!visivel(node) || dentroCardProduto(node)) return false;
                        var rect = node.getBoundingClientRect();
                        if (rect.left < Math.max(vw * 0.68, vw - 420)) return false;
                        if (rect.top < Math.max(120, vh - 260)) return false;
                        var texto = normalizar(textoVisivelBotao(node));
                        var contexto = contextoBotao(node);
                        return /^(ferramentas|tools)$/.test(texto)
                            && /avant\\s*pro|avantpro|speed-dial|ferramentas/.test(texto + ' ' + contexto);
                    });
                    var menuLateralAvantVisivel = rotuloFerramentasMenuVisivel
                        && (/\\bsuporte\\b|assine\\s+ja|avant\\s*pro|avantpro/.test(bodyBusca)
                            || queryAllDeep('[class*="avant"], [id*="avant"], [class*="speed-dial"]').some(visivel));
                    if (menuLateralAvantVisivel) {
                        return {
                            success: true,
                            source: 'ferramentas_menu_lateral_estimado',
                            x: Math.max(40, vw - 110),
                            y: Math.max(40, vh - 180),
                            width: 120,
                            height: 46,
                            label: 'Ferramentas',
                            url: location.href
                        };
                    }
                    var rotulosPequenos = todosCandidatos
                        .filter(function (item) { return item.score > 0 && (item.width < 40 || item.height < 20) && item.x > vw * 0.55; })
                        .sort(function (a, b) { return b.score - a.score || a.index - b.index; });
                    if (rotulosPequenos.length) {
                        var r = rotulosPequenos[0];
                        return {
                            success: true,
                            source: 'ferramentas_rotulo_estimado',
                            x: Math.max(40, Math.min(Math.round(r.x - 30), vw - 1)),
                            y: Math.max(40, Math.min(Math.round(r.y), vh - 1)),
                            width: 120,
                            height: 46,
                            score: r.score,
                            label: r.label || 'Ferramentas',
                            url: location.href
                        };
                    }
                    return {
                        success: false,
                        reason: 'ferramentas_avant_nao_localizado',
                        hasFerramentasText: /\\bferramentas\\b/.test(bodyBusca),
                        url: location.href
                    };
                })();
            `);
})(window);
