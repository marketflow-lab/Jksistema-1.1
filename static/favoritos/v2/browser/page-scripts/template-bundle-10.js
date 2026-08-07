(function (global) {
  'use strict';
  const pageScripts = global.FavoritosV2.browser.pageScripts;
  pageScripts.registerPart('acionar-cards-avant-pro-fila-webview-1', 1, `                        return item;
                    };
                    var cardTemDadosAvant = function (card) {
                        return linhasAvantPainel(card).length > 0;
                    };
                    var ehLinkProduto = function (node) {
                        var href = node && node.getAttribute && node.getAttribute('href');
                        return !!(href && /\\bMLB-?\\d{6,}\\b|[?&](?:wid|item_id)=MLB\\d{6,}|produto\\.mercadolivre\\.com\\.br/i.test(cleanUrl(href)));
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
                    var ehControleAvant = function (node, card) {
                        var alvo = textoAlvoClique(node);
                        var explicito = textoExplicitoClique(node);
                        var contexto = textoNode(card);
                        if (!alvo) return false;
                        if (!explicito) return false;
                        if (ehAncoraNavegavel(node)) return false;
                        if (ehDownloadOuMidiaAvant(node)) return false;
                        if (/\\blogin\\b|fazer\\s+login|entrar\\s+no\\s+avant|vincular\\s+(?:conta|agora)|ferramentas|tools|assine\\s+ja|assinar|suporte|comunidade/.test(alvo + ' ' + explicito)) return false;
                        if (/baixar|download|imagens?|images?|fotos?|photos?|zip|exportar?|salvar|gallery|galeria|rotulos\\s+visuais/.test(alvo + ' ' + explicito)) return false;
                        if (ehLinkProduto(node)) return false;
                        return /informacoes?\\s+avant|informacoes?\\s+avantpro|avantpro\\s+info|carregar\\s+dados\\s+avant|dados\\s+avantpro|dados\\s+avant\\s*pro/.test(explicito)
                            && /avant\\s*pro|avantpro|informacoes?\\s+avant/.test(alvo + ' ' + contexto);
                    };
                    var store = window.__JK_AVANT_CARD_QUEUE_CLICKED_KEYS || {};
                    window.__JK_AVANT_CARD_QUEUE_CLICKED_KEYS = store;
                    window.__JK_AVANT_CARD_DATA_CACHE = window.__JK_AVANT_CARD_DATA_CACHE || {};
                    var cardMaisProximoFila = function (anchor) {
                        if (!anchor || !anchor.closest) return anchor;
                        try {
                            return anchor.closest(cardSelectors + ', li, article, section') || anchor;
                        } catch (_closestErr) {
                            return anchor.closest('li, article, section, div') || anchor;
                        }
                    };
                    var cards = [];
                    var cardsPorKey = {};
                    var debugFila = {
                        selectorNodes: 0,
                        anchorNodes: 0,
                        includedBySelector: 0,
                        includedByAnchor: 0,
                        duplicateKeys: 0,
                        replacedDuplicate: 0,
                        skippedNoKey: 0,
                        sampleLinks: []
                    };
                    var pontuarCardFila = function (card) {
                        if (!card) return -1;
                        var score = 0;
                        if (visible(card)) score += 1000000;
                        var text = textoNode(card);
                        if (/carregar\s+dados\s+avant|informacoes?\s+avant|dados\s+avantpro/.test(text)) score += 200000;
                        try {
                            var rect = card.getBoundingClientRect ? card.getBoundingClientRect() : null;
                            if (rect) {
                                score += Math.min(120000, Math.max(0, rect.width) * Math.max(0, rect.height));
                                if (rect.top >= -80) score += Math.max(0, 20000 - Math.abs(rect.top));
                            }
                        } catch (_scoreRectErr) {}
                        try {
                            if (card.querySelector && card.querySelector('button, [role="button"], [aria-label], [title]')) score += 5000;
                        } catch (_scoreControlErr) {}
                        return score;
                    };
                    var incluirCard = function (card) {
                        if (!card) return false;
                        var key = keyCard(card);
                        if (!key) {
                            debugFila.skippedNoKey += 1;
                            return false;
                        }
                        var existente = cardsPorKey[key];
                        if (existente) {
                            debugFila.duplicateKeys += 1;
                            if (pontuarCardFila(card) > pontuarCardFila(existente)) {
                                var pos = cards.indexOf(existente);
                                if (pos >= 0) cards[pos] = card;
                                cardsPorKey[key] = card;
                                debugFila.replacedDuplicate += 1;
                            }
                            return false;
                        }
                        if (cards.indexOf(card) >= 0) return false;
                        cardsPorKey[key] = card;
                        cards.push(card);
                        return true;
                    };
                    try {
                        var cardsSelector = deepScan
                            ? queryAllDeep(cardSelectors)
                            : Array.prototype.slice.call(document.querySelectorAll(cardSelectors));
                        debugFila.selectorNodes = cardsSelector.length;
                        cardsSelector.forEach(function (card) {
                            if (incluirCard(card)) debugFila.includedBySelector += 1;
                        });
                    } catch (_cardsErr) {}
                    try {
                        var anchorsProduto = deepScan
                            ? queryAllDeep('a[href]')
                            : Array.prototype.slice.call(document.querySelectorAll('a[href]'));
                        debugFila.anchorNodes = anchorsProduto.length;
                        anchorsProduto.forEach(function (anchor) {
                            var href = anchor.href || anchor.getAttribute('href') || '';
                            if (!/\\bMLB-?\\d{6,}\\b|[?&](?:wid|item_id)=MLB\\d{6,}|\\/p\\/MLB|\\/up\\/MLB|produto\\.mercadolivre\\.com\\.br/i.test(href)) return;
                            if (debugFila.sampleLinks.length < 5) debugFila.sampleLinks.push(String(href).slice(0, 180));
                            if (incluirCard(cardMaisProximoFila(anchor))) debugFila.includedByAnchor += 1;
                        });
                    } catch (_anchorCardsErr) {}
                    cards.sort(function (left, right) {
                        var leftVisible = visible(left) ? 0 : 1;
                        var rightVisible = visible(right) ? 0 : 1;
                        if (leftVisible !== rightVisible) return leftVisible - rightVisible;
                        try {
                            var lr = left.getBoundingClientRect ? left.getBoundingClientRect() : null;
                            var rr = right.getBoundingClientRect ? right.getBoundingClientRect() : null;
                            return Math.abs((lr && lr.top) || 0) - Math.abs((rr && rr.top) || 0);
                        } catch (_sortErr) {
                            return 0;
                        }
                    });
                    window.__JK_AVANT_CARD_QUEUE_DEBUG = Object.assign({}, debugFila, {
                        totalCards: cards.length,
                        uniqueKeys: Object.keys(cardsPorKey).length,
                        candidateKeys: Object.keys(cardsPorKey).slice(0, 120)
                    });
                    var permitirClique = window.__JK_AVANT_CARD_QUEUE_CLICK_SLOW !== true;
                    var clicked = 0;
                    var eligiblePending = 0;
                    var pendingKeys = [];
                    var resolvedKeys = [];
                    var keys = [];
                    var capturados = [];
                    var controleSelector = 'button, [role="button"], input[type="button"], input[type="submit"], [aria-label], [title], [class*="andes-button"]';
                    for (var c = 0; c < cards.length && hasTime(550); c += 1) {
                        var card = cards[c];
                        var baseParaCache = baseCardDataLeve(card);
                        var key = baseParaCache.mlb ? ('id:' + baseParaCache.mlb) : (baseParaCache.link ? ('url:' + baseParaCache.link) : keyCard(card));
                        var tentativas = Number((store[key] && store[key].count) || store[key] || 0);
                        var canonicalCache = baseParaCache.mlb ? ('mlb:' + baseParaCache.mlb) : (baseParaCache.link ? ('link:' + String(baseParaCache.link).toLowerCase()) : '');
                        var cacheAtual = canonicalCache && window.__JK_AVANT_CARD_DATA_CACHE && window.__JK_AVANT_CARD_DATA_CACHE[canonicalCache];
                        var cacheTemDados = !!(cacheAtual && (cacheAtual.vendasFonte || cacheAtual.vendedorFonte || cacheAtual.data_criacao || cacheAtual.media_mensal || cacheAtual.visitas));
                        if (!key || tentativas >= maxTentativasPorCard) continue;
                        if (cacheTemDados) {
                            resolvedKeys.push(key);
                            continue;
                        }
                        if (cardTemDadosAvant(card)) {
                            var capturadoExistente = await capturarPainelAvantParaCard(card, '', baseParaCache);
                            if (capturadoExistente) {
                                store[key] = { count: tentativas + 1, at: Date.now(), semClique: true };
                                keys.push(key);
                                capturados.push(capturadoExistente);
                                resolvedKeys.push(key);
                                continue;
                            }
                        }
                        if (!permitirClique || clicked >= maxClicks) {
                            eligiblePending += 1;
                            pendingKeys.push(key);
                            continue;
                        }
                        var controles = [];
                        try { controles = Array.prototype.slice.call(card && card.querySelectorAll ? card.querySelectorAll(controleSelector) : []); } catch (_directControlErr) {}
                        if (!controles.length && deepScan) controles = queryAllDeep(controleSelector, card);
                        controles = controles
                            .filter(function (node) { return visible(node) && ehControleAvant(node, card); });
                        if (!controles.length) continue;
                        var alvo = controles[0];
                        var snapshotAntes = window.__JK_AVANT_LAST_PANEL_SNAPSHOT || '';
                        store[key] = { count: tentativas + 1, at: Date.now() };
                        try { alvo.scrollIntoView && alvo.scrollIntoView({ block: 'center', inline: 'center' }); } catch (_scrollErr) {}
                        await sleep(80);
                        try {
                            var rect = alvo.getBoundingClientRect ? alvo.getBoundingClientRect() : null;
                            var opts = rect ? { bubbles: true, cancelable: true, view: window, clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 } : { bubbles: true, cancelable: true, view: window };
                            if (typeof alvo.click === 'function') {
                                alvo.click();
                            } else {
                                try { alvo.dispatchEvent(new MouseEvent('click', opts)); } catch (_clickErr) {}
                            }
                            clicked += 1;
                            keys.push(key);
                            window.__JK_AVANT_CARD_CLICKED_AT = Date.now();
                            window.__JK_AVANT_LAST_CARD_CLICKED = Object.assign({}, baseParaCache, {
                                key: key,
                                chave_canonica: canonicalCache,
                                at: Date.now()
                            });
                        } catch (_clickOuterErr) {}
                        var capturado = await capturarPainelAvantParaCard(card, snapshotAntes, baseParaCache);
                        if (capturado) {
                            capturados.push(capturado);
                            resolvedKeys.push(key);
                        } else {
                            pendingKeys.push(key);
                        }
                        await sleep(80);
                    }
                    var textoPaginaAvant = checkLogin ? normalizar([
                        document.body && (document.body.innerText || document.body.textContent),
                        queryAllDeep('[role="dialog"], [aria-modal="true"], [class*="modal"], [class*="Modal"], [class*="avant"], [class*="login"], form, input, button, label, h1, h2, h3, p')
                            .slice(0, 360)
                            .map(function (node) {
                                return [
                                    node.innerText,
                                    node.textContent,
                                    node.value,
                                    node.getAttribute && node.getAttribute('placeholder'),
                                    node.getAttribute && node.getAttribute('aria-label'),
                                    node.getAttribute && node.getAttribute('title'),
                                    node.getAttribute && node.getAttribute('name'),
                                    node.getAttribute && node.getAttribute('type'),
                                    node.getAttribute && node.getAttribute('class'),
                                    node.getAttribute && node.getAttribute('id')
                                ].filter(Boolean).join(' ');
                            })
                            .join(' ')
                    ].filter(Boolean).join(' ')) : '';
                    var inputsLoginAvant = checkLogin && queryAllDeep('input, textarea').some(function (node) {
                        if (!visible(node)) return false;
                        var alvo = textoAlvoClique(node);
                        return /e\s*mail|email|senha|password|credential|credencial/.test(alvo);
                    });
                    var textoLoginAvant = checkLogin ? queryAllDeep('[role="dialog"], [aria-modal="true"], [class*="modal"], [class*="Modal"], [class*="avant"], [class*="login"], form')
                        .filter(visible)
                        .slice(0, 16)
                        .map(textoNode)
                        .join(' ') : '';
                    var marcaAvantLogin = /avant\s*pro|avantpro/.test(textoPaginaAvant + ' ' + textoLoginAvant);
                    var sinaisLoginAvant = /iniciar\s+sessao|insira\s+suas\s+credenciais|credenciais\s+para\s+acessar|seu\s+e\s*mail|seu\s+email|e-?mail|entrar\s+na\s+sua\s+conta|login\s+avant|avantpro\s+mercado\s+livre|ainda\s+nao\s+tem\s+um\s+cadastro/.test(textoPaginaAvant + ' ' + textoLoginAvant);
                    var loginAvantBloqueando = !!((marcaAvantLogin && sinaisLoginAvant) || (inputsLoginAvant && marcaAvantLogin));
                    var elapsed = Date.now() - startedAt;
                    if (clicked > 0 && capturados.length === 0 && loginAvantBloqueando) {
                        window.__JK_AVANT_CARD_QUEUE_CLICK_SLOW = true;
                    }
                    var incrementalState = window.__JK_FAVORITOS_INCREMENTAL_COLLECTOR_V1;
                    if (incrementalState) {
                        Array.from(new Set(pendingKeys)).forEach(function (key) {
                            incrementalState.pendingKeys[key] = Date.now();
                        });
                        Array.from(new Set(resolvedKeys)).forEach(function (key) {
                            incrementalState.resolvedKeys[key] = Date.now();
                            delete incrementalState.pendingKeys[key];
                        });
                    }
                    var resultadoFila = { clicked: clicked, totalCandidates: cards.length, eligiblePending: eligiblePending, pendingKeys: Array.from(new Set(pendingKeys)), resolvedKeys: Array.from(new Set(resolvedKeys)), keys: keys, capturados: capturados.length, elapsedMs: elapsed, timedOut: !hasTime(1), slowDisabled: window.__JK_AVANT_CARD_QUEUE_CLICK_SLOW === true, loginBlocked: loginAvantBloqueando, deepScan: deepScan, checkLogin: checkLogin, mutationVersion: incrementalState ? Number(incrementalState.mutationVersion) || 0 : 0, debug: window.__JK_AVANT_CARD_QUEUE_DEBUG || debugFila };
                    window.__JK_AVANT_LAST_QUEUE_RESULT = resultadoFila;
                    return resultadoFila;
                })();
            `);
  pageScripts.registerPart('coletar-primeira-pagina-favoritos-controlada-1', 0, `
                (function () {
                    window.__JK_AVANT_CARD_QUEUE_CLICKED_KEYS = {};
                    window.__JK_AVANT_CARD_DATA_CACHE = {};
                    window.__JK_AVANT_LAST_PANEL_SNAPSHOT = '';
                    window.__JK_AVANT_LAST_CARD_CLICKED = null;
                    window.__JK_AVANT_CARD_CLICKED_AT = 0;
                    window.__JK_AVANT_CARD_QUEUE_CLICK_SLOW = false;
                    try {
                        var anterior = window.__JK_FAVORITOS_INCREMENTAL_COLLECTOR_V1;
                        if (anterior && anterior.observer && typeof anterior.observer.disconnect === 'function') {
                            anterior.observer.disconnect();
                        }
                        var incremental = {
                            version: 1,
                            mutationVersion: 0,
                            lastMutationAt: Date.now(),
                            resolvedKeys: {},
                            pendingKeys: {},
                            observer: null
                        };
                        incremental.observer = new MutationObserver(function () {
                            incremental.mutationVersion += 1;
                            incremental.lastMutationAt = Date.now();
                        });
                        incremental.observer.observe(document.body || document.documentElement, {
                            subtree: true,
                            childList: true,
                            characterData: true
                        });
                        window.__JK_FAVORITOS_INCREMENTAL_COLLECTOR_V1 = incremental;
                    } catch (_incrementalErr) {
                        window.__JK_FAVORITOS_INCREMENTAL_COLLECTOR_V1 = null;
                    }
                    if (!window.__JK_FAVORITOS_DOWNLOAD_GUARD_REGISTERED) {
                        window.__JK_FAVORITOS_DOWNLOAD_GUARD_REGISTERED = true;
                        document.addEventListener('click', function (event) {
                            var node = event && event.target;
                            var alvo = node && node.closest ? node.closest('a, button, [role="button"], [download]') : null;
                            if (!alvo) return;
                            var href = String(alvo.href || (alvo.getAttribute && (alvo.getAttribute('href') || alvo.getAttribute('data-href') || '')) || '');
                            var texto = String([
                                alvo.innerText,
                                alvo.textContent,
                                alvo.getAttribute && alvo.getAttribute('aria-label'),
                                alvo.getAttribute && alvo.getAttribute('title'),
                                alvo.getAttribute && alvo.getAttribute('download'),
                                href
                            ].filter(Boolean).join(' ')).toLowerCase();
                            if (/^(blob|data):/i.test(href)
                                || /\\.zip(?:$|[?#])|\\/download\\b|download=|filename=|imagens?\\.zip|images?\\.zip/i.test(href)
                                || /baixar|download|imagens?|images?|fotos?|photos?|zip|exportar|salvar/.test(texto)) {
                                event.preventDefault();
                                event.stopPropagation();
                                event.stopImmediatePropagation();
                            }
                        }, true);
                    }
                    return !!window.__JK_FAVORITOS_INCREMENTAL_COLLECTOR_V1;
                })();
            `);
  pageScripts.registerPart('aguardar-dados-avant-pro-estaveis-webview-1', 0, `
                (async function () {
                    var minWaitMs = __JK_AGUARDAR_DADOS_AVANT_PRO_ESTAVEIS_WEBVIEW_1_P0__;
                    var stableMs = __JK_AGUARDAR_DADOS_AVANT_PRO_ESTAVEIS_WEBVIEW_1_P1__;
                    var maxWaitMs = __JK_AGUARDAR_DADOS_AVANT_PRO_ESTAVEIS_WEBVIEW_1_P2__;
                    var pollMs = __JK_AGUARDAR_DADOS_AVANT_PRO_ESTAVEIS_WEBVIEW_1_P3__;
                    var sleep = function (ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); };
                    var queryAllDeepLocal = function (selector, root) {
                        var found = [];
                        var visited = [];
                        var visit = function (base) {
                            if (!base || visited.indexOf(base) >= 0) return;
                            visited.push(base);
                            try {
                                if (base.querySelectorAll) {
                                    var nodes = Array.prototype.slice.call(base.querySelectorAll(selector));
                                    nodes.forEach(function (node) {
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
                    var queryOneDeepLocal = function (selector, root) {
                        var nodes = queryAllDeepLocal(selector, root);
                        return nodes.length ? nodes[0] : null;
                    };
                    var snapshot = function () {
                        var labelsAvant = /informacoes?\\s+avant(?:\\s*pro|pro)?|vendas?\\s+do\\s+produto|vendas?\\s+estimad|ritmo\\s+atual|faturamento\\s+do\\s+produto|nome\\s+do\\s+vendedor|localizacao\\s+do\\s+vendedor|participacao|visitas\\s+do\\s+anuncio|comissao|reputacao\\s+do\\s+vendedor|anuncio\\s+(?:ganhador\\s+)?criado\\s+em/i;
                        var normalizarBuscaLocal = function (value) {
                            var text = String(value || '').replace(/\\s+/g, ' ').trim();
                            try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (e) {}
                            return text.toLowerCase();
                        };
                        var rows = queryAllDeepLocal('.avantpro-product-info-row');
                        if (rows.length) {
                            return rows.map(function (row) {
                                var label = queryOneDeepLocal('.avantpro-product-info-row-label', row) || row.querySelector('.avantpro-product-info-row-label');
                                var value = queryOneDeepLocal('.avantpro-product-info-row-value', row) || row.querySelector('.avantpro-product-info-row-value');
                                var labelText = String(label && label.textContent || '').replace(/\\s+/g, ' ').trim();
                                var valueText = String(value && value.textContent || '').replace(/\\s+/g, ' ').trim();
                                return labelText || valueText ? [labelText, valueText].join('=') : '';
                            }).filter(Boolean).join('|');
                        }
                        var parts = [String(document.body && (document.body.innerText || document.body.textContent) || '')];
                        queryAllDeepLocal('*').slice(0, 1500).forEach(function (node) {
                            var nodeText = String(node && (node.innerText || node.textContent) || '').replace(/\\s+/g, ' ').trim();
                            if (nodeText) parts.push(nodeText);
                        });
                        return parts.join('\\n')
                            .split(/\\n+/)
                            .map(function (line) { return line.replace(/\\s+/g, ' ').trim(); })
                            .filter(function (line) { return line && labelsAvant.test(normalizarBuscaLocal(line)); })
                            .slice(0, 120)
                            .join('|');
                    };
                    var inicio = Date.now();
                    var ultimo = snapshot();
                    var ultimaMudanca = Date.now();
                    while (Date.now() - inicio < maxWaitMs) {
                        await sleep(pollMs);
                        var atual = snapshot();
                        if (atual !== ultimo) {
                            ultimo = atual;
                            ultimaMudanca = Date.now();
                        }
                        var decorrido = Date.now() - inicio;
                        if (ultimo && decorrido >= minWaitMs && Date.now() - ultimaMudanca >= stableMs) {
                            return { stable: true, elapsedMs: decorrido, rows: ultimo.split('|').filter(Boolean).length };
                        }
                    }
                    return { stable: false, elapsedMs: Date.now() - inicio, rows: ultimo ? ultimo.split('|').filter(Boolean).length : 0 };
                })();
            `);
})(window);
