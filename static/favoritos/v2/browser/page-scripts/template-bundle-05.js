(function (global) {
  'use strict';
  const pageScripts = global.FavoritosV2.browser.pageScripts;
  pageScripts.registerPart('fechar-modal-bloqueante-avant-pro-no-webview-1', 0, `
                (async function () {
                    var sleep = function (ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); };
                    var fecharLoginReal = __JK_FECHAR_MODAL_BLOQUEANTE_AVANT_PRO_NO_WEBVIEW_1_P0__;
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
                    var raizContexto = function (node) {
                        var root = null;
                        try {
                            root = node && node.closest && node.closest('form, article, section, aside, li, [role="dialog"], [aria-modal="true"], [class*="modal"], [class*="popup"], [class*="drawer"], [class*="avant"], [id*="avant"], [class*="login"], [class*="auth"]');
                        } catch (_err) {}
                        return root || (node && node.parentElement) || document.body;
                    };
                    var contextoNode = function (node) {
                        var root = raizContexto(node);
                        var host = null;
                        try {
                            var nodeRoot = node && node.getRootNode ? node.getRootNode() : null;
                            host = nodeRoot && nodeRoot.host ? nodeRoot.host : null;
                        } catch (_err) {}
                        return normalizar([
                            textoNode(node),
                            root && textoNode(root),
                            root && (root.innerText || root.textContent),
                            host && textoNode(host),
                            host && (host.innerText || host.textContent)
                        ].filter(Boolean).join(' '));
                    };
                    var contarRotulosDadosAvant = function (value) {
                        var busca = normalizar(value);
                        var padroes = [
                            /informacoes?\\s+avant(?:\\s*pro|pro)?/,
                            /vendas?\\s+do\\s+(?:produto|anuncio|item)/,
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
                    var temDadosAvant = function (value) {
                        return contarRotulosDadosAvant(value) >= 2;
                    };
                    var bodyText = String(document.body && (document.body.innerText || document.body.textContent) || '');
                    var bodyBusca = normalizar(bodyText);
                    var cardSelectorsAvant = 'li.ui-search-layout__item, .poly-card, [class*="poly-card"], [class*="ui-search-result"], [data-testid*="result"], [data-testid*="card"], [class*="shops__layout-item"]';
                    var cardCountAvant = 0;
                    try { cardCountAvant = queryAllDeep(cardSelectorsAvant).length; } catch (_cardCountErr) {}
                    var textoEscopoGlobalAvant = function () {
                        var partes = [];
                        var vistos = [];
                        queryAllDeep('body, main, header, aside, section, div, form, [role="dialog"], [aria-modal="true"], [class*="modal"], [class*="popup"], [class*="drawer"], [class*="avant"], [id*="avant"]').slice(0, 900).forEach(function (node) {
                            try {
                                if (node !== document.body && node.closest && node.closest(cardSelectorsAvant)) return;
                                var text = textoNode(node) || node.innerText || node.textContent || '';
                                text = String(text || '').replace(/\\s+/g, ' ').trim();
                                if (!text || vistos.indexOf(text) >= 0) return;
                                vistos.push(text);
                                partes.push(text);
                            } catch (_err) {}
                        });
                        return partes.join(' ');
                    };
                    var globalBusca = normalizar(textoEscopoGlobalAvant());
                    var escopoAvant = cardCountAvant > 0 ? globalBusca : bodyBusca;
                    var modalAvantPromocional = /avant\\s*pro|avantpro|avantprocloud/.test(escopoAvant)
                        && /vincule\\s+o\\s+avantpro|vincular\\s+agora|comece\\s+a\\s+usar|liberar\\s+os\\s+recursos|use\\s+gratis|usar\\s+gratis|dica\\s+avantpro|tutoriais/.test(escopoAvant);
                    var loginRealVisivel = /avant\\s*pro|avantpro|avantprocloud/.test(escopoAvant)
                        && /iniciar\\s+sessao|insira\\s+suas\\s+credenciais|seu\\s+e-?mail|seu\\s+email/.test(escopoAvant);
                    if (loginRealVisivel && !modalAvantPromocional && !fecharLoginReal) {
                        return { success: true, closed: false, reason: 'login_real_visivel', url: location.href };
                    }
                    if (!modalAvantPromocional && !(fecharLoginReal && loginRealVisivel)) {
                        return { success: true, closed: false, reason: 'modal_avant_bloqueante_nao_detectado', url: location.href };
                    }
                    var modalRaizes = queryAllDeep('div, section, article, aside, [role="dialog"], [aria-modal="true"], [class*="modal"], [class*="popup"], [class*="drawer"], [class*="avant"], [id*="avant"]').filter(function (node) {
                        if (!visivel(node)) return false;
                        var texto = normalizar(node.innerText || node.textContent || '');
                        if (!/avant\\s*pro|avantpro|avantprocloud|vincule\\s+o\\s+avantpro/.test(texto)) return false;
                        if (!/vincule\\s+o\\s+avantpro|vincular\\s+agora|comece\\s+a\\s+usar|liberar\\s+os\\s+recursos|use\\s+gratis|usar\\s+gratis/.test(texto)) return false;
                        var rect = node.getBoundingClientRect();
                        return rect.width >= 220 && rect.height >= 140;
                    }).map(function (node, index) {
                        var rect = node.getBoundingClientRect();
                        return { node: node, index: index, area: rect.width * rect.height, rect: rect };
                    }).sort(function (a, b) {
                        var aDialog = a.node && a.node.matches && a.node.matches('[role="dialog"], [aria-modal="true"], [class*="modal"], [class*="popup"], [class*="drawer"]') ? 1 : 0;
                        var bDialog = b.node && b.node.matches && b.node.matches('[role="dialog"], [aria-modal="true"], [class*="modal"], [class*="popup"], [class*="drawer"]') ? 1 : 0;
                        return bDialog - aDialog || b.area - a.area || a.index - b.index;
                    });
                    var modalPrincipal = modalRaizes.length ? modalRaizes[0].node : document.body;
                    var modalPrincipalRect = modalRaizes.length ? modalRaizes[0].rect : null;
                    var candidatos = [];
                    queryAllDeep('button, a, [role="button"], [aria-label], [title], [class*="close"], [class*="Close"], [class*="fechar"]').forEach(function (node, index) {
                        var alvo = node;
                        try {
                            alvo = node.closest && node.closest('button, a, [role="button"]') || node;
                        } catch (_err) {}
                        if (!alvo || !visivel(alvo)) return;
                        var root = raizContexto(alvo);
                        var contexto = contextoNode(alvo);
                        var label = normalizar(textoNode(alvo));
                        var rect = alvo.getBoundingClientRect();
                        var rootRect = root && root.getBoundingClientRect ? root.getBoundingClientRect() : null;
                        var refRect = modalPrincipalRect || rootRect;
                        var centroX = rect.left + rect.width / 2;
                        var centroY = rect.top + rect.height / 2;
                        var dentroModalPrincipal = !!(modalPrincipal === document.body || (modalPrincipal.contains && modalPrincipal.contains(alvo)) || (refRect
                            && centroX >= refRect.left
                            && centroX <= refRect.right
                            && centroY >= refRect.top
                            && centroY <= refRect.bottom));
                        if (!dentroModalPrincipal && !/avant\\s*pro|avantpro|avantprocloud|vincule\\s+o\\s+avantpro|comece\\s+a\\s+usar/.test(contexto)) return;
                        var labelFecha = /(^|\\b)(fechar|close|dismiss|cancelar|agora\\s+nao|depois)(\\b|$)|^(x|×)$/.test(label);
                        if (!labelFecha && label.charCodeAt(0) === 215) labelFecha = true;
                        var geometriaFecha = !!(refRect && rect.width <= 80 && rect.height <= 80
                            && rect.left >= refRect.right - 120
                            && rect.top <= refRect.top + 120);
                        if (!labelFecha && !geometriaFecha) return;
                        var score = 0;
                        if (labelFecha) score += 40;
                        if (geometriaFecha) score += 25;
                        if (dentroModalPrincipal) score += 20;
                        if (/close|fechar/.test(label)) score += 15;
                        if (/vincule\\s+o\\s+avantpro|vincular\\s+agora|comece\\s+a\\s+usar|liberar\\s+os\\s+recursos/.test(contexto)) score += 15;
                        candidatos.push({
                            node: alvo,
                            index: index,
                            score: score,
                            label: String(alvo.innerText || alvo.textContent || alvo.getAttribute && (alvo.getAttribute('aria-label') || alvo.getAttribute('title')) || '').replace(/\\s+/g, ' ').trim().slice(0, 120)
                        });
                    });
                    candidatos.sort(function (a, b) { return b.score - a.score || a.index - b.index; });
                    if (!candidatos.length) {
                        if (modalPrincipalRect) {
                            var x = Math.max(0, Math.min((window.innerWidth || document.documentElement.clientWidth || 0) - 1, modalPrincipalRect.right - 42));
                            var y = Math.max(0, Math.min((window.innerHeight || document.documentElement.clientHeight || 0) - 1, modalPrincipalRect.top + 46));
                            var alvoPonto = document.elementFromPoint ? document.elementFromPoint(x, y) : null;
                            if (alvoPonto) {
                                var alvoClique = alvoPonto.closest && alvoPonto.closest('button, a, [role="button"], [aria-label], [title]') || alvoPonto;
                                var optsPonto = { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y };
                                try { alvoClique.dispatchEvent(new MouseEvent('mousedown', optsPonto)); } catch (_pDownErr) {}
                                try { alvoClique.dispatchEvent(new MouseEvent('mouseup', optsPonto)); } catch (_pUpErr) {}
                                try { alvoClique.dispatchEvent(new MouseEvent('click', optsPonto)); } catch (_pClickErr) {}
                                try { alvoClique.click(); } catch (_pDirectErr) {}
                                await sleep(120);
                                window.__JK_AVANT_PRO_CLOSED_MODAL_AT = Date.now();
                                return { success: true, closed: true, via: 'top_right_point', reason: 'botao_fechar_avant_por_geometria', modalAvantPromocional: modalAvantPromocional, loginRealVisivel: loginRealVisivel, url: location.href };
                            }
                        }
                        try { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true, cancelable: true })); } catch (_escDownErr) {}
                        try { document.dispatchEvent(new KeyboardEvent('keyup', { key: 'Escape', code: 'Escape', bubbles: true, cancelable: true })); } catch (_escUpErr) {}
                        try { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true, cancelable: true })); } catch (_winEscErr) {}
                        window.__JK_AVANT_PRO_CLOSED_MODAL_AT = Date.now();
                        return { success: true, closed: true, via: 'escape', reason: 'botao_fechar_avant_nao_encontrado', modalAvantPromocional: modalAvantPromocional, loginRealVisivel: loginRealVisivel, url: location.href };
                    }
                    var escolhido = candidatos[0].node;
                    try { escolhido.scrollIntoView({ block: 'center', inline: 'center' }); } catch (_scrollErr) {}
                    await sleep(80);
                    var rect = escolhido.getBoundingClientRect ? escolhido.getBoundingClientRect() : null;
                    var opts = rect ? { bubbles: true, cancelable: true, view: window, clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 } : { bubbles: true, cancelable: true, view: window };
                    try { escolhido.dispatchEvent(new MouseEvent('mousedown', opts)); } catch (_downErr) {}
                    try { escolhido.dispatchEvent(new MouseEvent('mouseup', opts)); } catch (_upErr) {}
                    try { escolhido.dispatchEvent(new MouseEvent('click', opts)); } catch (_clickErr) {}
                    try { escolhido.click(); } catch (_directErr) {}
                    window.__JK_AVANT_PRO_CLOSED_MODAL_AT = Date.now();
                    return { success: true, closed: true, label: candidatos[0].label, url: location.href };
                })();
            `);
  pageScripts.registerPart('abrir-login-avant-pro-no-webview-1', 0, `
                (async function () {
                    var sleep = function (ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); };
                    var normalizar = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                        return text.toLowerCase();
                    };
                    var visivel = function (node) {
                        if (!node || !node.getBoundingClientRect) return false;
                        var rect = node.getBoundingClientRect();
                        var style = window.getComputedStyle ? window.getComputedStyle(node) : null;
                        return rect.width > 0 && rect.height > 0 && (!style || (style.display !== 'none' && style.visibility !== 'hidden'));
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
                    var estaDentroDeCardProduto = function (node) {
                        try {
                            return !!(node && node.closest && node.closest('li.ui-search-layout__item, .poly-card, [class*="poly-card"], [class*="ui-search-result"], [data-testid*="result"], [data-testid*="card"]'));
                        } catch (_err) {
                            return false;
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
                            node.getAttribute && node.getAttribute('href')
                        ].filter(Boolean).join(' ');
                    };
                    var contextoBotao = function (node) {
                        var root = null;
                        try {
                            root = node && node.closest && node.closest('form, article, section, aside, li, [role="dialog"], [class*="avant"], [id*="avant"], [class*="poly-card"], [class*="ui-search-result"], [class*="modal"], [class*="login"], [class*="auth"]');
                        } catch (_err) {}
                        var host = composedHost(node);
                        root = root || (node && node.parentElement) || node;
                        return normalizar([
                            textoBotao(node),
                            root && (root.innerText || root.textContent),
                            root && root.getAttribute && root.getAttribute('class'),
                            root && root.getAttribute && root.getAttribute('id'),
                            host && textoBotao(host),
                            host && (host.innerText || host.textContent)
                        ].filter(Boolean).join(' '));
                    };
                    var emailAvantVisivelAtual = function () {
                        return queryAllDeep('input:not([type="hidden"])').some(function (input) {
                            if (!visivel(input) || input.disabled || input.readOnly) return false;
                            var attrs = normalizar([
                                input.type,
                                input.name,
                                input.id,
                                input.className,
                                input.placeholder,
                                input.getAttribute && input.getAttribute('aria-label'),
                                input.getAttribute && input.getAttribute('autocomplete')
                            ].join(' '));
                            var contexto = contextoBotao(input);
                            return /email|e-?mail|mail/.test(attrs + ' ' + contexto)
                                && /avant\\s*pro|avantpro|iniciar\\s+sessao|credenciais|seu\\s+e-?mail/.test(contexto);
                        });
                    };
                    if (emailAvantVisivelAtual()) {
                        return { success: true, clicked: false, reason: 'campo_email_avant_visivel', url: location.href };
                    }
                    var clicarElemento = async function (node) {
                        if (!node) return false;
                        try { node.scrollIntoView({ block: 'center', inline: 'center' }); } catch (_scrollErr) {}
                        await sleep(120);
                        var rect = node.getBoundingClientRect ? node.getBoundingClientRect() : null;
                        var opts = rect ? { bubbles: true, cancelable: true, view: window, clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 } : { bubbles: true, cancelable: true, view: window };
                        try { node.dispatchEvent(new MouseEvent('mouseover', opts)); } catch (_overErr) {}
                        try { node.dispatchEvent(new MouseEvent('mousedown', opts)); } catch (_downErr) {}
                        try { node.dispatchEvent(new MouseEvent('mouseup', opts)); } catch (_upErr) {}
                        try { node.dispatchEvent(new MouseEvent('click', opts)); } catch (_clickErr) {}
                        try { node.click(); } catch (_directErr) {}
                        return true;
                    };
                    var botaoMenuAvant = queryAllDeep('.avantpro-menu, .avantpro-menu-surface, .avantpro-menu-icon, .avantpro-menu-icon-html, button, a, [role="button"], [tabindex], [aria-label], [title]')
                        .filter(function (node) {
                            if (!visivel(node)) return false;
                            if (estaDentroDeCardProduto(node)) return false;
                            var busca = normalizar(textoBotao(node) + ' ' + (node.getAttribute && (node.getAttribute('class') || '') || '') + ' ' + contextoBotao(node));
                            return /avantpro-menu|abrir\\s+menu\\s+avantpro|avantpro-floating-button|avantpro-speed-dial-fab/.test(busca);
                        })
                        .map(function (node, index) {
                            var rect = node.getBoundingClientRect ? node.getBoundingClientRect() : null;
                            return { node: node, index: index, area: rect ? rect.width * rect.height : 0 };
                        })
                        .sort(function (a, b) { return b.area - a.area || a.index - b.index; })[0];
                    if (botaoMenuAvant) {
                        await clicarElemento(botaoMenuAvant.node);
                        await sleep(500);
                    }
                    var botaoLoginGlobalAvant = queryAllDeep('#sideMenuLogin, #btnLoginMenu, .avantpro-logged-out-combo-cta, .avantpro-logged-out-modern-cta, button, a, input[type="button"], input[type="submit"], [role="button"], [tabindex], [aria-label], [title], [class*="andes-button"], div, span')
                        .filter(function (node) {
                            if (!visivel(node)) return false;
                            if (estaDentroDeCardProduto(node)) return false;
                            var busca = normalizar(textoBotao(node));
                            var contexto = contextoBotao(node);
                            var alvo = busca + ' ' + contexto + ' ' + normalizar(node.getAttribute && (node.getAttribute('class') || '') || '') + ' ' + normalizar(node.id || '');
                            if (/sidemenu(login)?|avantpro-logged-out-combo-cta|btnloginmenu/.test(alvo)) return true;
                            if (/^login$|\\blogin\\b|fazer\\s+login|entrar\\s+no\\s+avant|login\\s+avant/.test(busca)
                                && /avant\\s*pro|avantpro|comece\\s+a\\s+usar|liberar\\s+os\\s+recursos|extensao/.test(alvo)) return true;
                            return false;
                        })
                        .map(function (node, index) {
                            var rect = node.getBoundingClientRect ? node.getBoundingClientRect() : null;
                            var alvo = normalizar(textoBotao(node) + ' ' + contextoBotao(node) + ' ' + (node.id || '') + ' ' + (node.getAttribute && (node.getAttribute('class') || '') || ''));
                            var score = 0;
                            if (/sidemenu(login)?|avantpro-logged-out-combo-cta/.test(alvo)) score += 240;
                            if (/btnloginmenu|fazer\\s+login/.test(alvo)) score += 220;
                            if (rect && rect.left < 320) score += 20;
                            return { node: node, index: index, score: score, area: rect ? rect.width * rect.height : 0, text: textoBotao(node).replace(/\\s+/g, ' ').trim().slice(0, 120) };
                        })
                        .sort(function (a, b) { return b.score - a.score || b.area - a.area || a.index - b.index; })[0];
                    if (botaoLoginGlobalAvant) {
                        window.__JK_AVANT_PRO_CLICKED_AT = Date.now();
                        await clicarElemento(botaoLoginGlobalAvant.node);
                        await sleep(1300);
                        return { success: true, clicked: true, label: botaoLoginGlobalAvant.text || 'Login Avant Pro', reason: 'login_global_avant_clicado', emailVisible: emailAvantVisivelAtual(), url: location.href };
                    }
                    var botaoFerramentasAvant = queryAllDeep('button, a, input[type="button"], input[type="submit"], [role="button"], [tabindex], [aria-label], [title], [class*="andes-button"], div, span')
                        .filter(function (node) {
                            if (!visivel(node)) return false;
                            if (estaDentroDeCardProduto(node)) return false;
                            var busca = normalizar(textoBotao(node));
                            var contexto = contextoBotao(node);
                            var alvo = busca + ' ' + contexto + ' ' + normalizar(node.getAttribute && (node.getAttribute('class') || '') || '');
                            return /\\bferramentas\\b|\\btools\\b/.test(alvo)
                                && /avant\\s*pro|avantpro|speed-dial|ferramentas/.test(alvo);
                        })
                        .map(function (node, index) {
                            var rect = node.getBoundingClientRect ? node.getBoundingClientRect() : null;
                            return { node: node, index: index, area: rect ? rect.width * rect.height : 0, text: textoBotao(node).replace(/\\s+/g, ' ').trim().slice(0, 120) };
                        })
                        .sort(function (a, b) { return b.area - a.area || a.index - b.index; })[0];
                    if (botaoFerramentasAvant) {
                        window.__JK_AVANT_PRO_CLICKED_AT = Date.now();
                        await clicarElemento(botaoFerramentasAvant.node);
                        await sleep(1200);
                        return { success: true, clicked: true, label: botaoFerramentasAvant.text || 'Ferramentas', reason: 'ferramentas_avant_clicada', emailVisible: emailAvantVisivelAtual(), url: location.href };
                    }
                    var candidatos = queryAllDeep('button, a, input[type="button"], input[type="submit"], [role="button"], [tabindex], [class*="andes-button"], div, span')
                        .filter(function (node) {
                            if (!visivel(node)) return false;
                            if (estaDentroDeCardProduto(node)) return false;
                            var busca = normalizar(textoBotao(node));
                            var contexto = contextoBotao(node);
                            var alvo = busca + ' ' + contexto + ' ' + normalizar(node.id || '') + ' ' + normalizar(node.getAttribute && (node.getAttribute('class') || '') || '');
                            if (!busca) return false;
                            if (/sidemenu(login)?|avantpro-logged-out-combo-cta|btnloginmenu/.test(alvo)) return true;
                            if (/\\bferramentas\\b|\\btools\\b/.test(alvo)
                                && /avant\\s*pro|avantpro|speed-dial|ferramentas/.test(alvo)) return true;
                            if (/^login$|\\blogin\\b/.test(busca) && /avant\\s*pro|avantpro|comece\\s+a\\s+usar|liberar\\s+os\\s+recursos|extensao/.test(contexto)) return true;
                            if (/fazer\\s+login|entrar\\s+no\\s+avant|login\\s+avant/.test(busca)) return true;
                            return false;
                        })
                        .map(function (node, index) {
                            var busca = normalizar(textoBotao(node));
                            var contexto = contextoBotao(node);
                            var alvo = busca + ' ' + contexto + ' ' + normalizar(node.id || '') + ' ' + normalizar(node.getAttribute && (node.getAttribute('class') || '') || '');
                            var score = 0;
                            if (/sidemenu(login)?|avantpro-logged-out-combo-cta/.test(alvo)) score += 240;
                            if (/btnloginmenu|fazer\\s+login/.test(alvo) && /avant\\s*pro|avantpro/.test(alvo)) score += 220;
                            if (/\\bferramentas\\b|\\btools\\b/.test(alvo)
                                && /avant\\s*pro|avantpro|speed-dial|ferramentas/.test(alvo)) {
                                score += 180;
                            }
                            if (/^login$|\\blogin\\b/.test(busca) && /avant\\s*pro|avantpro|comece\\s+a\\s+usar|liberar\\s+os\\s+recursos|extensao/.test(contexto)) score += 120;
                            if (/fazer\\s+login|entrar\\s+no\\s+avant|login\\s+avant/.test(busca)) score += 90;
                            if (/avant/.test(busca + ' ' + contexto)) score += 8;
                            return { node: node, index: index, score: score, text: textoBotao(node).replace(/\\s+/g, ' ').trim().slice(0, 120) };
                        })
                        .sort(function (a, b) { return b.score - a.score || a.index - b.index; });
                    if (!candidatos.length) {
                        return { success: false, reason: 'botao_login_avant_nao_encontrado', url: location.href };
                    }
                    var escolhido = candidatos[0];
                    window.__JK_AVANT_PRO_CLICKED_AT = Date.now();
                    await clicarElemento(escolhido.node);
                    await sleep(900);
                    return { success: true, clicked: true, label: escolhido.text, emailVisible: emailAvantVisivelAtual(), url: location.href };
                })();
            `);
  pageScripts.registerPart('detectar-confirmacao-login-avant-pro-no-webview-1', 0, `
                (function () {
                    var normalizar = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                        return text.toLowerCase();
                    };
                    var texto = normalizar([
                        document.title,
                        location.href,
                        document.body && (document.body.innerText || document.body.textContent)
                    ].filter(Boolean).join(' '));
                    var confirmado = /obrigado\\s+por\\s+usar\\s+nossa\\s+extensao|aguarde[,\\s]+a\\s+pagina\\s+sera\\s+recarregada/.test(texto);
                    return {
                        confirmado: confirmado,
                        url: location.href,
                        title: document.title || '',
                        texto: confirmado ? texto.slice(0, 260) : ''
                    };
                })();
            `);
  pageScripts.registerPart('extrair-anuncios-webview-fast-dom-1', 0, `
                (function () {
                    var maxFastDom = Number(window.__JK_ML_FAST_DOM_MAX || __JK_EXTRAIR_ANUNCIOS_WEBVIEW_FAST_DOM_1_P0__) || __JK_EXTRAIR_ANUNCIOS_WEBVIEW_FAST_DOM_1_P1__;
                    var normalizar = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                        return text.toLowerCase();
                    };
                    var parseHumanNumber = function (value, suffix) {
                        var raw = String(value || '').replace(/\\s+/g, '').replace(/\\./g, '').replace(',', '.');
                        var numero = Number(raw);
                        if (!Number.isFinite(numero)) return null;
                        suffix = String(suffix || '').toLowerCase();
                        if (suffix === 'mil' || suffix === 'k') numero *= 1000;
                        return Math.round(numero);
                    };
                    var valorNoTexto = function (card, regex) {
                        var text = String(card && (card.innerText || card.textContent) || '').replace(/\\s+/g, ' ').trim();
                        var match = text.match(regex);
                        return match && match[1] ? parseHumanNumber(match[1], match[2]) : null;
                    };
                    var cleanUrl = function (href) {
                        href = String(href || '').split('#')[0].trim();
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
                    var isProductUrl = function (href) {
                        var url = cleanUrl(href);
                        return /(?:produto\\.mercadolivre\\.com\\.br\\/MLB-\\d+|\\/MLB-\\d+|\\/p\\/MLB\\d+|\\/up\\/MLB|item_id(?:=|%3A)MLB\\d+|wid=MLB\\d+)/i.test(url);
                    };
                    var tituloDe = function (card) {
                        var el = card && card.querySelector && card.querySelector('a.poly-component__title, .poly-component__title, .ui-search-item__title, h2, h3, a[href]');
                        return String((el && (el.innerText || el.textContent || el.getAttribute && (el.getAttribute('title') || el.getAttribute('aria-label')))) || card && (card.innerText || card.textContent) || '')
                            .replace(/\\s+/g, ' ')
                            .trim()
                            .slice(0, 240);
                    };
                    var hrefDe = function (card) {
                        var links = Array.prototype.slice.call(card.querySelectorAll ? card.querySelectorAll('a[href]') : []);
                        for (var i = 0; i < links.length; i += 1) {
                            var rawHref = links[i].href || links[i].getAttribute('href') || '';
                            if (isProductUrl(rawHref)) return rawHref;
                        }
                        return '';
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
                        '[class*="product-card"]',
                        '[class*="poly-card"]',
                        '[class*="ui-search-layout__item"]',
                        '[class*="shops__layout-item"]',
                        'main ol > li',
                        'main ul > li'
                    ].join(',');
                    var cards = Array.prototype.slice.call(document.querySelectorAll(selectors));
                    var vistos = {};
                    var anuncios = [];
                    for (var i = 0; i < cards.length && anuncios.length < maxFastDom; i += 1) {
                        var card = cards[i];
                        var href = hrefDe(card);
                        var id = extrairId(href || card.outerHTML || '');
                        var titulo = tituloDe(card);
                        if (!href && !id) continue;
                        var key = id || href || normalizar(titulo);
                        if (!key || vistos[key]) continue;
                        vistos[key] = true;
                        var vendasProduto = valorNoTexto(card, /vendas?\\s+do\\s+produto\\s+[:\\-]?\\s*(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i);
                        var vendasEstimadas = valorNoTexto(card, /vendas?\\s+estimad[ao]s?\\s*[:\\-]?\\s*(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i);
                        var ritmoAtual = valorNoTexto(card, /ritmo\\s+atual(?:\\s*\\(vendas\\/mes\\))?\\s*[:\\-]?\\s*(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i);
                        var vendas = vendasProduto !== null && vendasProduto !== undefined ? vendasProduto : (vendasEstimadas !== null && vendasEstimadas !== undefined ? vendasEstimadas : ritmoAtual);
                        anuncios.push({
                            posicao: anuncios.length + 1,
                            id: id,
                            url: href,
                            titulo: titulo,
                            vendas: vendas,
                            vendasFonte: vendas !== null && vendas !== undefined ? 'avantpro_fast_dom' : '',
                            vendas_fonte: vendas !== null && vendas !== undefined ? 'avantpro_fast_dom' : '',
                            origem_dados: 'avantpro_fast_dom'
                        });
                    }
                    return {
                        success: true,
                        total: anuncios.length,
                        anuncios: anuncios,
                        debug: { mode: 'fast_dom', maxFastDom: maxFastDom, url: location.href, title: document.title || '' }
                    };
                })();
            `);
})(window);
