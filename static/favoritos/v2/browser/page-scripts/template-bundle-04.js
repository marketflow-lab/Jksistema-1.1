(function (global) {
  'use strict';
  const pageScripts = global.FavoritosV2.browser.pageScripts;
  pageScripts.registerPart('montar-script-localizar-vincular-conta-avant-pro-1', 0, `
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
                            root = node && node.closest && node.closest('form, section, aside, [role="dialog"], [aria-modal="true"], [class*="avant"], [id*="avant"], [class*="speed"], [class*="menu"], [class*="drawer"], [class*="popup"]');
                        } catch (_err) {}
                        root = root || (node && node.parentElement) || node;
                        return normalizar([
                            textoNode(node),
                            root && textoNode(root),
                            root && (root.innerText || root.textContent)
                        ].filter(Boolean).join(' '));
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
                    var candidatos = queryAllDeep('button, a, input[type="button"], input[type="submit"], [role="button"], [tabindex], [aria-label], [title], [class*="andes-button"], [class*="avant"], [id*="avant"], [class*="speed"], [class*="menu"], div, span')
                        .filter(function (node) {
                            if (!visivel(node) || dentroCardProduto(node)) return false;
                            var rect = node.getBoundingClientRect();
                            var texto = normalizar(textoNode(node));
                            var contexto = contextoNode(node);
                            var alvo = texto + ' ' + contexto;
                            if (rect.left < vw * 0.55) return false;
                            if (rect.top < 100 || rect.top > vh - 40) return false;
                            if (/conectando\\s+avant|fazendo\\s+favorito|tentativa|sku\\s*\\d|aguarde|status/.test(alvo)) return false;
                            var textoTemVinculo = /vincular\\s+(?:conta|agora)|conectar\\s+conta|autorizar\\s+(?:mercado\\s+livre|conta)|permitir\\s+acesso/.test(texto);
                            return textoTemVinculo
                                && /avant\\s*pro|avantpro|speed-dial|vincular|conta|mercado\\s+livre/.test(alvo);
                        })
                        .map(function (node, index) {
                            var rect = node.getBoundingClientRect();
                            var texto = normalizar(textoNode(node));
                            var contexto = contextoNode(node);
                            var alvo = texto + ' ' + contexto;
                            var score = 0;
                            if (/^vincular\\s+conta$|^conectar\\s+conta$/.test(texto)) score += 260;
                            if (/vincular\\s+(?:conta|agora)|conectar\\s+conta/.test(alvo)) score += 180;
                            if (/avant\\s*pro|avantpro|speed-dial|menu/.test(alvo)) score += 60;
                            if (rect.width >= 90 && rect.width <= 260 && rect.height >= 28 && rect.height <= 90) score += 60;
                            if (rect.top > 60 && rect.top < vh - 80) score += 20;
                            return {
                                index: index,
                                score: score,
                                x: rect.left + rect.width / 2,
                                y: rect.top + rect.height / 2,
                                width: rect.width,
                                height: rect.height,
                                label: String(textoNode(node) || '').replace(/\\s+/g, ' ').trim().slice(0, 120)
                            };
                        })
                        .filter(function (item) { return item.score > 0 && item.width >= 40 && item.height >= 20; })
                        .sort(function (a, b) { return b.score - a.score || a.index - b.index; });
                    if (candidatos.length) {
                        var c = candidatos[0];
                        return {
                            success: true,
                            source: 'vincular_conta_avant_dom',
                            x: c.x,
                            y: c.y,
                            width: c.width,
                            height: c.height,
                            score: c.score,
                            label: c.label || 'Vincular conta',
                            url: location.href
                        };
                    }
                    return { success: false, reason: 'vincular_conta_avant_nao_localizado', url: location.href };
                })();
            `);
  pageScripts.registerPart('diagnosticar-entrada-avant-pro-no-webview-1', 0, `
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
                            node.getAttribute && node.getAttribute('placeholder'),
                            node.getAttribute && node.getAttribute('class'),
                            node.getAttribute && node.getAttribute('id')
                        ].filter(Boolean).join(' ');
                    };
                    var contextoNode = function (node) {
                        var root = null;
                        try {
                            root = node && node.closest && node.closest('form, [role="dialog"], [aria-modal="true"], [class*="avant"], [id*="avant"], [class*="login"], [class*="auth"], [class*="modal"], [class*="popup"], [class*="drawer"], [class*="speed"], [class*="menu"]');
                        } catch (_err) {}
                        var host = null;
                        try {
                            var nodeRoot = node && node.getRootNode ? node.getRootNode() : null;
                            host = nodeRoot && nodeRoot.host ? nodeRoot.host : null;
                        } catch (_err2) {}
                        root = root || (node && node.parentElement) || document.body;
                        return normalizar([
                            textoNode(node),
                            root && textoNode(root),
                            root && (root.innerText || root.textContent),
                            host && textoNode(host),
                            host && (host.innerText || host.textContent)
                        ].filter(Boolean).join(' '));
                    };
                    var bodyText = String(document.body && (document.body.innerText || document.body.textContent) || '');
                    var bodyBusca = normalizar(bodyText);
                    var confirmado = /obrigado\\s+por\\s+usar\\s+nossa\\s+extensao|aguarde[,\\s]+a\\s+pagina\\s+sera\\s+recarregada/.test(bodyBusca);
                    var ferramentasPainelAberto = /ferramentas\\s+avantpro|ferramentas\\s+avant\\s*pro/.test(bodyBusca);
                    var sessaoExpiradaAvant = /session\\s+expired|please\\s+log\\s+in\\s+again|statuscode\\s*[:=]?\\s*401|nao\\s+foi\\s+possivel\\s+carregar/.test(bodyBusca);
                    var contaMercadoLivreNecessaria = /conecte\\s+sua\\s+conta\\s+do\\s+mercado\\s+livre|vincule\\s+sua\\s+conta\\s+para\\s+liberar/.test(bodyBusca);
                    var ferramentasMenuItens = [
                        /calculadora\\s+de\\s+contribuicao/.test(bodyBusca),
                        /metricas\\s+do\\s+anuncio/.test(bodyBusca),
                        /gerador\\s+de\\s+eans/.test(bodyBusca),
                        /teste\\s+a\\/?b/.test(bodyBusca),
                        /rastreio\\s+de\\s+ads/.test(bodyBusca),
                        /tendencias/.test(bodyBusca),
                        /publicar\\s+com\\s+ia/.test(bodyBusca),
                        /gerador\\s+de\\s+titulos/.test(bodyBusca),
                        /gerador\\s+de\\s+descricao/.test(bodyBusca)
                    ].filter(Boolean).length;
                    var ferramentasMenuLogado = ferramentasMenuItens >= 3 && !sessaoExpiradaAvant;
                    var emailInputs = queryAllDeep('input:not([type="hidden"])').filter(function (input) {
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
                        var contexto = contextoNode(input);
                        var pareceEmail = input.type === 'email' || /email|e-?mail|mail/.test(attrs + ' ' + contexto);
                        var pareceBuscaMl = /search|buscar|pesquisar|as_word|\\bq\\b/.test(attrs);
                        var contextoAvant = /avant\\s*pro|avantpro|avantprocloud|iniciar\\s+sessao|insira\\s+suas\\s+credenciais|seu\\s+e-?mail|seu\\s+email/.test(contexto + ' ' + bodyBusca);
                        return pareceEmail && contextoAvant && !pareceBuscaMl;
                    });
                    var botoesLogin = queryAllDeep('button, a, input[type="button"], input[type="submit"], [role="button"], [tabindex], [aria-label], [title], div, span').filter(function (node) {
                        if (!visivel(node)) return false;
                        var texto = normalizar(textoNode(node));
                        var contexto = contextoNode(node);
                        var alvo = texto + ' ' + contexto;
                        if (/assine\\s+ja|assinar|suporte|compras|favoritos|categorias|ofertas/.test(texto)) return false;
                        return /vincular\\s+(?:conta|agora)|conectar\\s+conta|fazer\\s+login|entrar\\s+no\\s+avant|login\\s+avant|^login$|iniciar\\s+sessao|continuar/.test(alvo)
                            && /avant\\s*pro|avantpro|avantprocloud|extensao|conta|credenciais/.test(alvo + ' ' + bodyBusca);
                    });
                    var ferramentasVisiveis = queryAllDeep('button, a, [role="button"], [aria-label], [title], div, span').some(function (node) {
                        if (!visivel(node)) return false;
                        return /\\bferramentas\\b|\\btools\\b/.test(normalizar(textoNode(node)));
                    });
                    var authFrames = queryAllDeep('iframe, frame').filter(function (node) {
                        if (!visivel(node)) return false;
                        var src = normalizar([
                            node.getAttribute && node.getAttribute('src'),
                            node.getAttribute && node.getAttribute('name'),
                            node.getAttribute && node.getAttribute('id'),
                            node.getAttribute && node.getAttribute('title')
                        ].filter(Boolean).join(' '));
                        return /avantprocloud|auth\\.avantpro|avant.*auth|login.*avant/.test(src);
                    });
                    var pronto = confirmado || ferramentasMenuLogado || emailInputs.length > 0 || authFrames.length > 0;
                    return {
                        ok: pronto,
                        prontoParaLogin: pronto,
                        confirmado: confirmado,
                        confirmed: confirmado || ferramentasMenuLogado,
                        ferramentasMenuLogado: ferramentasMenuLogado,
                        ferramentasMenuItens: ferramentasMenuItens,
                        emailInputs: emailInputs.length,
                        authFrames: authFrames.length,
                        botoesLogin: botoesLogin.length,
                        ferramentasVisiveis: ferramentasVisiveis,
                        ferramentasPainelAberto: ferramentasPainelAberto,
                        sessaoExpiradaAvant: sessaoExpiradaAvant,
                        contaMercadoLivreNecessaria: contaMercadoLivreNecessaria,
                        avantText: /avant\\s*pro|avantpro|avantprocloud/.test(bodyBusca),
                        reason: pronto ? 'entrada_avant_pronta'
                            : sessaoExpiradaAvant ? 'avantpro_sessao_expirada_sem_email'
                            : contaMercadoLivreNecessaria ? 'avantpro_conta_mercado_livre_necessaria'
                            : ferramentasPainelAberto ? 'avantpro_ferramentas_abriu_sem_email'
                            : 'entrada_avant_nao_visivel',
                        url: location.href,
                        title: document.title || ''
                    };
                })();
            `);
  pageScripts.registerPart('montar-script-localizar-login-avant-pro-1', 0, `
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
                            node.getAttribute && node.getAttribute('title')
                        ].filter(Boolean).join(' ');
                    };
                    var contextoBotao = function (node) {
                        var root = null;
                        try {
                            root = node && node.closest && node.closest('form, article, section, aside, li, [role="dialog"], [class*="avant"], [id*="avant"], [class*="login"], [class*="auth"], [class*="poly-card"], [class*="ui-search-result"]');
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
                    var candidatos = queryAllDeep('button, a, input[type="button"], input[type="submit"], [role="button"], [tabindex], [class*="andes-button"], div, span')
                        .filter(function (node) {
                            if (!visivel(node)) return false;
                            var busca = normalizar(textoBotao(node));
                            var contexto = contextoBotao(node);
                            var alvo = busca + ' ' + contexto;
                            if (!busca) return false;
                            if (/sidemenu(login)?|avantpro-logged-out-combo-cta|btnloginmenu/.test(alvo)) return true;
                            if (estaDentroDeCardProduto(node) && !/abrir\\s+menu\\s+avantpro|avantpro-floating-button|avantpro-speed-dial-fab|speed-dial/.test(alvo)) return false;
                            if (/abrir\\s+menu\\s+avantpro|avantpro-floating-button|avantpro-speed-dial-fab/.test(alvo)) return true;
                            if (/\\bferramentas\\b|\\btools\\b/.test(alvo)
                                && /avant\\s*pro|avantpro|speed-dial|ferramentas/.test(alvo)) return true;
                            if (/vincular\\s+(?:conta|agora)|conectar\\s+conta|autorizar\\s+(?:mercado\\s+livre|conta)|permitir\\s+acesso/.test(alvo)
                                && /avant\\s*pro|avantpro|mercado\\s+livre|conta|vincul/.test(alvo)) return false;
                            if (/^login$|\\blogin\\b/.test(busca) && /avant\\s*pro|avantpro|comece\\s+a\\s+usar|liberar\\s+os\\s+recursos|extensao/.test(contexto)) return true;
                            if (/fazer\\s+login|entrar\\s+no\\s+avant|login\\s+avant/.test(busca)) return true;
                            return false;
                        })
                        .map(function (node, index) {
                            var busca = normalizar(textoBotao(node));
                            var contexto = contextoBotao(node);
                            var rect = node.getBoundingClientRect();
                            var alvo = busca + ' ' + contexto;
                            var score = 0;
                            if (/sidemenu(login)?|avantpro-logged-out-combo-cta/.test(alvo)) score += 240;
                            if (/btnloginmenu|fazer\\s+login/.test(alvo) && /avant\\s*pro|avantpro/.test(alvo)) score += 220;
                            if (/abrir\\s+menu\\s+avantpro|avantpro-floating-button|avantpro-speed-dial-fab/.test(alvo)) score += 150;
                            if (/\\bferramentas\\b|\\btools\\b/.test(alvo)
                                && /avant\\s*pro|avantpro|speed-dial|ferramentas/.test(alvo)) {
                                score += 160;
                            }
                            if (/vincular\\s+(?:conta|agora)|conectar\\s+conta|autorizar\\s+(?:mercado\\s+livre|conta)|permitir\\s+acesso/.test(alvo)
                                && /avant\\s*pro|avantpro|mercado\\s+livre|conta|vincul/.test(alvo)) {
                                score += /vincular\\s+agora|autorizar|permitir\\s+acesso/.test(alvo) ? 8 : 6;
                            }
                            if (/^login$|\\blogin\\b/.test(busca) && /avant\\s*pro|avantpro|comece\\s+a\\s+usar|liberar\\s+os\\s+recursos|extensao/.test(contexto)) score += 120;
                            if (/fazer\\s+login|entrar\\s+no\\s+avant|login\\s+avant/.test(busca)) score += 90;
                            if (/avant/.test(busca + ' ' + contexto)) score += 10;
                            if (rect.left < 320) score += 8;
                            return {
                                node: node,
                                index: index,
                                score: score,
                                x: rect.left + rect.width / 2,
                                y: rect.top + rect.height / 2,
                                width: rect.width,
                                height: rect.height,
                                label: textoBotao(node).replace(/\\s+/g, ' ').trim().slice(0, 120)
                            };
                        })
                        .sort(function (a, b) { return b.score - a.score || a.index - b.index; });
                    if (candidatos.length) {
                        var c = candidatos[0];
                        return {
                            success: true,
                            source: 'dom',
                            x: c.x,
                            y: c.y,
                            width: c.width,
                            height: c.height,
                            score: c.score,
                            label: c.label,
                            url: location.href
                        };
                    }
                    var pageText = normalizar(document.body && (document.body.innerText || document.body.textContent) || '');
                    var pareceLoginAvant = /comece\\s+a\\s+usar\\s+o\\s+avant\\s*pro|comece\\s+a\\s+usar\\s+o\\s+avantpro|liberar\\s+os\\s+recursos\\s+da\\s+extensao|nao\\s+possui\\s+uma\\s+conta|avantpro/.test(pageText)
                        && /\\blogin\\b/.test(pageText);
                    if (pareceLoginAvant) {
                        var loginLateral = queryAllDeep('#sideMenuLogin, .avantpro-logged-out-combo-cta, .avantpro-logged-out-modern-cta')
                            .filter(function (node) { return visivel(node) && !estaDentroDeCardProduto(node); })
                            .map(function (node, index) {
                                var rect = node.getBoundingClientRect();
                                return {
                                    node: node,
                                    index: index,
                                    x: rect.left + rect.width / 2,
                                    y: rect.top + rect.height / 2,
                                    width: rect.width,
                                    height: rect.height,
                                    label: textoBotao(node).replace(/\\s+/g, ' ').trim().slice(0, 120)
                                };
                            })[0];
                        if (loginLateral) {
                            return {
                                success: true,
                                source: 'login_lateral_avantpro',
                                x: loginLateral.x,
                                y: loginLateral.y,
                                width: loginLateral.width,
                                height: loginLateral.height,
                                score: 260,
                                label: loginLateral.label || 'Login Avant Pro',
                                url: location.href
                            };
                        }
                    }
                    return {
                        success: false,
                        reason: 'login_avant_nao_localizado_para_clique_real',
                        hasAvantText: pareceLoginAvant,
                        url: location.href
                    };
                })();
            `);
  pageScripts.registerPart('montar-script-localizar-campo-email-avant-pro-para-digitacao-1', 0, `
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
                            node.getAttribute && node.getAttribute('placeholder'),
                            node.getAttribute && node.getAttribute('class'),
                            node.getAttribute && node.getAttribute('id'),
                            node.getAttribute && node.getAttribute('src'),
                            node.getAttribute && node.getAttribute('href')
                        ].filter(Boolean).join(' ');
                    };
                    var contextoNode = function (node) {
                        var root = null;
                        try {
                            root = node && node.closest && node.closest('form, [role="dialog"], [aria-modal="true"], [class*="avant"], [id*="avant"], [class*="login"], [class*="auth"], [class*="modal"], [class*="popup"], [class*="drawer"], [class*="speed"], [class*="menu"]');
                        } catch (_err) {}
                        var host = null;
                        try {
                            var nodeRoot = node && node.getRootNode ? node.getRootNode() : null;
                            host = nodeRoot && nodeRoot.host ? nodeRoot.host : null;
                        } catch (_err2) {}
                        root = root || (node && node.parentElement) || document.body;
                        return normalizar([
                            textoNode(node),
                            root && textoNode(root),
                            root && (root.innerText || root.textContent),
                            host && textoNode(host),
                            host && (host.innerText || host.textContent)
                        ].filter(Boolean).join(' '));
                    };
                    var centro = function (node, source, yRatio) {
                        var rect = node.getBoundingClientRect();
                        var x = rect.left + rect.width / 2;
                        var y = rect.top + rect.height * (Number.isFinite(yRatio) ? yRatio : 0.5);
                        var out = {
                            success: true,
                            source: source,
                            x: Math.max(1, Math.min(Math.round(x), (window.innerWidth || document.documentElement.clientWidth || 1) - 1)),
                            y: Math.max(1, Math.min(Math.round(y), (window.innerHeight || document.documentElement.clientHeight || 1) - 1)),
                            width: rect.width,
                            height: rect.height,
                            url: location.href
                        };
                        try { window.__JK_AVANT_EMAIL_NATIVE_TARGET = out; } catch (_err) {}
                        return out;
                    };
                    var bodyText = String(document.body && (document.body.innerText || document.body.textContent) || '');
                    var bodyBusca = normalizar(bodyText);
                    if (/obrigado\\s+por\\s+usar\\s+nossa\\s+extensao|aguarde[,\\s]+a\\s+pagina\\s+sera\\s+recarregada/.test(bodyBusca)) {
                        return { success: false, confirmed: true, reason: 'login_avant_ja_confirmado', url: location.href };
                    }
                    var inputs = queryAllDeep('input:not([type="hidden"]), textarea, [contenteditable="true"], [role="textbox"]').map(function (node, index) {
                        if (!visivel(node) || node.disabled || node.readOnly) return null;
                        var attrs = normalizar([
                            node.type,
                            node.name,
                            node.id,
                            node.className,
                            node.placeholder,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('autocomplete')
                        ].join(' '));
                        var contexto = contextoNode(node);
                        var busca = attrs + ' ' + contexto + ' ' + bodyBusca;
                        if (/search|buscar|pesquisar|as_word|\\bq\\b/.test(attrs)) return null;
                        var score = 0;
                        if (node.type === 'email' || /email|e-?mail|mail/.test(busca)) score += 120;
                        if (/avant\\s*pro|avantpro|avantprocloud|extensao|credenciais|iniciar\\s+sessao/.test(busca)) score += 120;
                        if (/mercado\\s*livre|mercadolivre|mercadolibre/.test(contexto) && !/avant/.test(contexto)) score -= 120;
                        if (score < 120) return null;
                        return { node: node, score: score, index: index };
                    }).filter(Boolean).sort(function (a, b) { return b.score - a.score || a.index - b.index; });
                    if (inputs.length) return centro(inputs[0].node, 'email_dom_avantpro', 0.5);

                    var frames = queryAllDeep('iframe, webview').map(function (node, index) {
                        if (!visivel(node)) return null;
                        var contexto = contextoNode(node);
                        var score = 0;
                        if (/avant\\s*pro|avantpro|avantprocloud|auth|login|credential|extension|jdefnfmbnchmnjkcknaadaddgjbgephh/.test(contexto)) score += 160;
                        if (/ferramentas|seu\\s+e-?mail|email|credenciais|iniciar\\s+sessao/.test(bodyBusca + ' ' + contexto)) score += 80;
                        if (score < 160) return null;
                        return { node: node, score: score, index: index };
                    }).filter(Boolean).sort(function (a, b) { return b.score - a.score || a.index - b.index; });
                    if (frames.length) return centro(frames[0].node, 'email_frame_avantpro_estimado', 0.48);

                    var containers = queryAllDeep('form, [role="dialog"], [aria-modal="true"], section, aside, div[class*="avant"], div[id*="avant"], div[class*="login"], div[class*="auth"], div[class*="modal"], div[class*="popup"], div[class*="drawer"]').map(function (node, index) {
                        if (!visivel(node)) return null;
                        var rect = node.getBoundingClientRect();
                        if (rect.width < 180 || rect.height < 80) return null;
                        var contexto = contextoNode(node);
                        var score = 0;
                        if (/avant\\s*pro|avantpro|avantprocloud/.test(contexto + ' ' + bodyBusca)) score += 90;
                        if (/seu\\s+e-?mail|email|credenciais|iniciar\\s+sessao|ferramentas/.test(contexto + ' ' + bodyBusca)) score += 80;
                        if (/assine\\s+ja|suporte/.test(contexto) && !/email|credenciais|iniciar/.test(contexto)) score -= 80;
                        if (score < 120) return null;
                        return { node: node, score: score, index: index };
                    }).filter(Boolean).sort(function (a, b) { return b.score - a.score || a.index - b.index; });
                    if (containers.length) return centro(containers[0].node, 'email_container_avantpro_estimado', 0.48);

                    return {
                        success: false,
                        reason: 'campo_email_avant_nao_localizado_para_digitacao',
                        avantText: /avant\\s*pro|avantpro|avantprocloud/.test(bodyBusca),
                        ferramentasText: /\\bferramentas\\b/.test(bodyBusca),
                        url: location.href
                    };
                })();
            `);
  pageScripts.registerPart('montar-script-localizar-botao-confirmar-avant-pro-para-clique-1', 0, `
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
                            root = node && node.closest && node.closest('form, [role="dialog"], [aria-modal="true"], [class*="avant"], [id*="avant"], [class*="login"], [class*="auth"], [class*="modal"], [class*="popup"], [class*="drawer"]');
                        } catch (_err) {}
                        root = root || (node && node.parentElement) || document.body;
                        return normalizar([textoNode(node), root && textoNode(root), root && (root.innerText || root.textContent)].filter(Boolean).join(' '));
                    };
                    var bodyBusca = normalizar(String(document.body && (document.body.innerText || document.body.textContent) || ''));
                    var botoes = queryAllDeep('button, input[type="button"], input[type="submit"], [role="button"], [tabindex], a').map(function (node, index) {
                        if (!visivel(node)) return null;
                        var texto = normalizar(textoNode(node));
                        var contexto = contextoNode(node);
                        var alvo = texto + ' ' + contexto + ' ' + bodyBusca;
                        if (/assine\\s+ja|assinar|suporte|compras|favoritos|categorias|ofertas/.test(texto)) return null;
                        var score = 0;
                        if (/confirmar|entrar|acessar|login|iniciar|continuar|enviar|comecar|começar/.test(texto)) score += 120;
                        if (/avant\\s*pro|avantpro|avantprocloud|credenciais|email|e-?mail|extensao/.test(alvo)) score += 80;
                        if (score < 120) return null;
                        return { node: node, score: score, index: index };
                    }).filter(Boolean).sort(function (a, b) { return b.score - a.score || a.index - b.index; });
                    if (botoes.length) {
                        var rect = botoes[0].node.getBoundingClientRect();
                        return {
                            success: true,
                            source: 'confirmar_dom_avantpro',
                            x: Math.max(1, Math.min(Math.round(rect.left + rect.width / 2), (window.innerWidth || document.documentElement.clientWidth || 1) - 1)),
                            y: Math.max(1, Math.min(Math.round(rect.top + rect.height / 2), (window.innerHeight || document.documentElement.clientHeight || 1) - 1)),
                            width: rect.width,
                            height: rect.height,
                            url: location.href
                        };
                    }
                    var alvoEmail = null;
                    try { alvoEmail = window.__JK_AVANT_EMAIL_NATIVE_TARGET || null; } catch (_err3) {}
                    if (alvoEmail && Number.isFinite(Number(alvoEmail.x)) && Number.isFinite(Number(alvoEmail.y))) {
                        return {
                            success: true,
                            source: 'confirmar_estimado_apos_email',
                            x: Math.max(1, Math.min(Math.round(Number(alvoEmail.x)), (window.innerWidth || document.documentElement.clientWidth || 1) - 1)),
                            y: Math.max(1, Math.min(Math.round(Number(alvoEmail.y) + 72), (window.innerHeight || document.documentElement.clientHeight || 1) - 1)),
                            url: location.href
                        };
                    }
                    return { success: false, reason: 'botao_confirmar_avant_nao_localizado', url: location.href };
                })();
            `);
})(window);
