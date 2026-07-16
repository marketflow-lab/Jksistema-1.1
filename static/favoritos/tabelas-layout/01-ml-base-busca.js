        function encontrarAnuncioAvantCorrespondente(anuncio, anuncios) {
            if (!anuncio || !Array.isArray(anuncios)) return null;
            const itemId = anuncio.id || extrairItemIdAnuncio(anuncio.url);
            const normalizar = (value) => String(value || '').split('#')[0].trim();
            const porIdOuUrl = anuncios.find(item => {
                if (!item) return false;
                const itemCandidateId = item.id || extrairItemIdAnuncio(item.url);
                if (itemId && itemCandidateId && String(itemCandidateId).toUpperCase() === String(itemId).toUpperCase()) return true;
                return normalizar(item.url) && normalizar(item.url) === normalizar(anuncio.url);
            });
            if (porIdOuUrl) return porIdOuUrl;

            return null;
        }

        function extrairItemIdAnuncio(url) {
            let texto = String(url || '');
            try {
                texto = decodeURIComponent(texto);
            } catch (e) {
                try { texto = decodeURI(texto); } catch (e2) {}
            }
            const patterns = [
                /[?&]wid=(MLB\d+)/i,
                /[?&]item_id=(MLB\d+)/i,
                /item_id:?(MLB\d+)/i,
                /item_id%3A(MLB\d+)/i,
                /\/(MLB-?\d+)/i,
                /\b(MLB-?\d{6,})\b/i
            ];
            for (const pattern of patterns) {
                const match = texto.match(pattern);
                if (match && match[1]) {
                    const itemId = match[1].replace('-', '').toUpperCase();
                    const digits = itemId.replace(/^MLB/i, '');
                    if (/^MLB\d+$/i.test(itemId) && digits.length >= 8) return itemId;
                }
            }
            return '';
        }

        function construirUrlItemMercadoLivreFavoritos(itemId) {
            const id = String(itemId || '').trim().toUpperCase().replace('-', '');
            const digitos = id.replace(/^MLB/i, '');
            if (!/^MLB\d+$/i.test(id) || digitos.length < 8) return '';
            return `https://produto.mercadolivre.com.br/${id.replace('MLB', 'MLB-')}`;
        }

        function normalizarUrlItemMercadoLivreFavoritos(valor, itemId = '') {
            const texto = String(valor || '').trim();
            if (/^\/\//.test(texto)) return `https:${texto}`;
            if (/^https?:\/\//i.test(texto)) return texto;
            const id = extrairItemIdAnuncio(texto) || itemId;
            return construirUrlItemMercadoLivreFavoritos(id);
        }

        const ML_API_WORKERS = 4;
        const ML_BROWSER_WORKERS = 6;

        function tituloPareceFiltroOuCategoriaMl(titulo) {
            const normalizado = normalizarTextoMl(titulo);
            if (!normalizado) return true;
            return [
                'resultados',
                'pecas de motos e quadriciclos',
                'lubrificantes e fluidos',
                'pecas de linha pesada',
                'pecas de carros e caminhonetes',
                'acessorios de motos e quadriciclos',
                'categorias',
                'condicao',
                'tipo de envio',
                'custo de envio',
                'tempo de entrega'
            ].includes(normalizado);
        }

        function normalizarCondicaoAnuncioFavoritos(valor) {
            if (valor === null || valor === undefined || valor === '') return '';
            let bruto = valor;
            if (typeof bruto === 'object') {
                bruto = bruto.value_name || bruto.name || bruto.label || bruto.title || bruto.value_id || bruto.id || '';
            }
            const texto = normalizarTextoMl(bruto);
            if (!texto) return '';
            if (['new', 'novo', 'nueva', 'nuevo', '2230284'].includes(texto) || texto.includes('novo') || texto.includes('nuev')) {
                return 'new';
            }
            if (['used', 'usado', 'usada', '2230581'].includes(texto) || texto.includes('usad')) {
                return 'used';
            }
            return texto;
        }

        function obterCondicaoAnuncioFavoritos(anuncio) {
            if (!anuncio || typeof anuncio !== 'object') return '';
            const attrs = Array.isArray(anuncio.attributes) ? anuncio.attributes : [];
            for (const attr of attrs) {
                const attrId = String(attr && attr.id || '').toUpperCase();
                if (attrId === 'ITEM_CONDITION' || attrId === 'CONDITION') {
                    const condicaoAttr = normalizarCondicaoAnuncioFavoritos(attr.value_name || attr.value_id || attr);
                    if (condicaoAttr) return condicaoAttr;
                }
            }
            const candidatos = [
                anuncio.condicao,
                anuncio.condition,
                anuncio.item_condition,
                anuncio.itemCondition,
                anuncio.itemConditionName,
                anuncio.estado,
                anuncio.item_state
            ];
            for (const candidato of candidatos) {
                const condicao = normalizarCondicaoAnuncioFavoritos(candidato);
                if (condicao) return condicao;
            }
            return '';
        }

        function preencherCondicaoAnuncioFavoritos(alvo, fonte) {
            if (!alvo || !fonte) return false;
            const condicao = obterCondicaoAnuncioFavoritos(fonte);
            if (!condicao) return false;
            const atual = obterCondicaoAnuncioFavoritos(alvo);
            if (atual === condicao) return false;
            alvo.condicao = condicao;
            alvo.condition = condicao;
            alvo.item_condition = condicao;
            return true;
        }

        function anuncioFavoritosProdutoNovo(anuncio) {
            return obterCondicaoAnuncioFavoritos(anuncio) !== 'used';
        }

        async function consultarItemApiMercadoLivreSemDedupe(itemId) {
            itemId = String(itemId || '').trim().toUpperCase();
            if (!itemId) return null;
            consultarItemApiMercadoLivre.cache = consultarItemApiMercadoLivre.cache || new Map();
            if (consultarItemApiMercadoLivre.cache.has(itemId)) {
                return consultarItemApiMercadoLivre.cache.get(itemId);
            }

            const temInformacao = (info) => hasTexto(info && info.titulo) || hasTexto(info && info.url) || hasTexto(info && info.imagem) || hasTexto(info && info.data_criacao) || hasTexto(info && info.vendedor) || hasNumeroVendas(info && info.vendas);

            let viaElectron = null;
            if (window.electronAPI && typeof window.electronAPI.getMlPublicItemInfo === 'function') {
                try {
                    const retorno = await window.electronAPI.getMlPublicItemInfo(itemId);
                    if (retorno) {
                        const urlElectron = normalizarUrlItemMercadoLivreFavoritos(retorno.url || retorno.permalink || retorno.link, retorno.id || itemId);
                        const imagemElectron = obterImagemAnuncioFavoritos(retorno);
                        viaElectron = {
                            id: retorno.id || itemId,
                            titulo: hasTexto(retorno.titulo) ? retorno.titulo : '',
                            tituloFonte: hasTexto(retorno.titulo) ? 'mercado_livre_api' : '',
                            titulo_fonte: hasTexto(retorno.titulo) ? 'mercado_livre_api' : '',
                            url: urlElectron,
                            permalink: urlElectron,
                            link: urlElectron,
                            link_normalizado: urlElectron,
                            linkFonte: urlElectron ? 'mercado_livre_api' : '',
                            link_fonte: urlElectron ? 'mercado_livre_api' : '',
                            imagem: imagemElectron,
                            thumbnail: imagemElectron || retorno.thumbnail || '',
                            foto: imagemElectron,
                            fotoFonte: imagemElectron ? 'mercado_livre_api' : '',
                            foto_fonte: imagemElectron ? 'mercado_livre_api' : '',
                            pictures: Array.isArray(retorno.pictures) ? retorno.pictures : [],
                            preco: retorno.preco ?? retorno.price ?? '',
                            price: retorno.price ?? retorno.preco ?? '',
                            preco_original: retorno.preco_original ?? retorno.original_price ?? retorno.standard_price ?? '',
                            original_price: retorno.original_price ?? retorno.preco_original ?? '',
                            standard_price: retorno.standard_price ?? '',
                            preco_promocional: retorno.preco_promocional ?? retorno.promotional_price ?? retorno.sale_price ?? '',
                            discount_pct: retorno.discount_pct ?? retorno.discount_percent ?? retorno.discount_percentage ?? '',
                            fonte_preco: retorno.fonte_preco ?? retorno.price_source ?? retorno.source ?? 'api_item',
                            precoFonte: retorno.fonte_preco ?? retorno.price_source ?? retorno.source ?? 'api_item',
                            preco_fonte: retorno.fonte_preco ?? retorno.price_source ?? retorno.source ?? 'api_item',
                            moeda: retorno.moeda ?? retorno.currency_id ?? retorno.currency ?? 'BRL',
                            currency_id: retorno.currency_id ?? retorno.moeda ?? retorno.currency ?? 'BRL',
                            installments: retorno.installments || null,
                            parcelamento_sem_juros: obterParcelamentoSemJurosFavoritos(retorno),
                            tipo_anuncio: normalizarTipoAnuncioFavoritos(retorno.tipo_anuncio ?? retorno.tipoAnuncio ?? retorno.listing_type_name ?? retorno.listingTypeName ?? retorno.listing_type_id ?? retorno.listingTypeId ?? retorno.listing_type ?? ''),
                            listing_type_id: retorno.listing_type_id ?? retorno.listingTypeId ?? '',
                            listing_type_name: retorno.listing_type_name ?? retorno.tipo_anuncio ?? retorno.tipoAnuncio ?? '',
                            shipping: retorno.shipping || null,
                            logistic_type: retorno.logistic_type ?? retorno.logisticType ?? retorno.shipping_logistic_type ?? '',
                            shipping_mode: retorno.shipping_mode ?? retorno.shippingMode ?? '',
                            is_full: retorno.is_full ?? retorno.isFull ?? retorno.full ?? '',
                            data_criacao: hasTexto(retorno.data_criacao) ? retorno.data_criacao : '',
                            vendedor: normalizarNomeVendedor(retorno.vendedor),
                            vendedorFonte: retorno.vendedorFonte || retorno.vendedor_fonte || retorno.fonte_vendedor || (retorno.vendedor ? 'mercado_livre_api' : ''),
                            vendedor_fonte: retorno.vendedorFonte || retorno.vendedor_fonte || retorno.fonte_vendedor || (retorno.vendedor ? 'mercado_livre_api' : ''),
                            fonte_vendedor: retorno.vendedorFonte || retorno.vendedor_fonte || retorno.fonte_vendedor || (retorno.vendedor ? 'mercado_livre_api' : ''),
                            vendas: parseNumeroVendas(retorno.vendas),
                            seller_id: retorno.seller_id || null,
                            condicao: normalizarCondicaoAnuncioFavoritos(retorno.condicao ?? retorno.condition ?? retorno.item_condition ?? retorno.itemCondition ?? ''),
                            condition: normalizarCondicaoAnuncioFavoritos(retorno.condicao ?? retorno.condition ?? retorno.item_condition ?? retorno.itemCondition ?? ''),
                            item_condition: normalizarCondicaoAnuncioFavoritos(retorno.condicao ?? retorno.condition ?? retorno.item_condition ?? retorno.itemCondition ?? ''),
                            source: retorno.source || null
                        };
                        if (!vendedorValido(viaElectron.vendedor)) {
                            viaElectron.vendedor = '';
                        }
                    }
                } catch (err) {
                    console.warn('Falha ao consultar API ML pelo Electron:', itemId, err);
                }
            }
            try {
                const itemResp = await fetch(`https://api.mercadolibre.com/items/${encodeURIComponent(itemId)}`, {
                    headers: { 'Accept': 'application/json' }
                });
                if (!itemResp.ok) {
                    if (viaElectron && temInformacao(viaElectron)) {
                        consultarItemApiMercadoLivre.cache.set(itemId, viaElectron);
                        return viaElectron;
                    }
                    return null;
                }
                const item = await itemResp.json();
                const seller = item && typeof item.seller === 'object' ? item.seller : {};
                const officialStore = item && typeof item.official_store === 'object' ? item.official_store : {};
                let vendedor = escolherNomeVendedor([
                    { valor: seller.nickname, prioridade: 80 },
                    { valor: seller.name, prioridade: 55 },
                    { valor: item.seller_name, prioridade: 70 },
                    { valor: item.official_store_name, prioridade: 95 },
                    { valor: officialStore.nickname, prioridade: 95 },
                    { valor: officialStore.name, prioridade: 90 }
                ]);
                const sellerId = item.seller_id || (item.seller && item.seller.id);
                const salePrice = item && typeof item.sale_price === 'object' && item.sale_price ? item.sale_price : {};
                const saleAmount = salePrice.amount ?? salePrice.price ?? (item && typeof item.sale_price !== 'object' ? item.sale_price : '');
                const regularAmount = salePrice.regular_amount ?? item.original_price ?? '';
                const shippingInfo = item && typeof item.shipping === 'object' && item.shipping ? item.shipping : {};
                const logisticType = shippingInfo.logistic_type || '';
                const permalink = normalizarUrlItemMercadoLivreFavoritos(item.permalink || item.url || item.link, item.id || itemId);

                if (sellerId) {
                    try {
                        const userResp = await fetch(`https://api.mercadolibre.com/users/${encodeURIComponent(sellerId)}`, {
                            headers: { 'Accept': 'application/json' }
                        });
                        if (userResp.ok) {
                            const user = await userResp.json();
                            const nomeUsuario = escolherNomeVendedor([
                                { valor: user.official_store_name, prioridade: 130 },
                                { valor: user.official_store && user.official_store.name, prioridade: 130 },
                                { valor: user.nickname, prioridade: 120 }
                            ]);
                            if (nomeUsuario) {
                                vendedor = nomeUsuario;
                            }
                        }
                    } catch (e) {}
                }

                const info = {
                    id: item.id || itemId,
                    titulo: item.title || '',
                    tituloFonte: item.title ? 'mercado_livre_api' : '',
                    titulo_fonte: item.title ? 'mercado_livre_api' : '',
                    url: permalink,
                    permalink,
                    link: permalink,
                    link_normalizado: permalink,
                    linkFonte: permalink ? 'mercado_livre_api' : '',
                    link_fonte: permalink ? 'mercado_livre_api' : '',
                    imagem: item.secure_thumbnail || item.thumbnail || ((item.pictures || [])[0] && ((item.pictures || [])[0].secure_url || (item.pictures || [])[0].url)) || '',
                    thumbnail: item.secure_thumbnail || item.thumbnail || '',
                    foto: item.secure_thumbnail || item.thumbnail || ((item.pictures || [])[0] && ((item.pictures || [])[0].secure_url || (item.pictures || [])[0].url)) || '',
                    fotoFonte: (item.secure_thumbnail || item.thumbnail || ((item.pictures || [])[0] && ((item.pictures || [])[0].secure_url || (item.pictures || [])[0].url))) ? 'mercado_livre_api' : '',
                    foto_fonte: (item.secure_thumbnail || item.thumbnail || ((item.pictures || [])[0] && ((item.pictures || [])[0].secure_url || (item.pictures || [])[0].url))) ? 'mercado_livre_api' : '',
                    pictures: Array.isArray(item.pictures) ? item.pictures : [],
                    preco: item.price ?? '',
                    price: item.price ?? '',
                    preco_original: item.original_price ?? regularAmount ?? '',
                    original_price: item.original_price ?? regularAmount ?? '',
                    standard_price: regularAmount || item.original_price || item.price || '',
                    preco_promocional: saleAmount || (item.original_price && item.price && Number(item.original_price) > Number(item.price) ? item.price : ''),
                    discount_pct: item.original_price && item.price && Number(item.original_price) > Number(item.price) ? ((Number(item.original_price) - Number(item.price)) / Number(item.original_price)) * 100 : '',
                    fonte_preco: 'api_item',
                    precoFonte: 'mercado_livre_api',
                    preco_fonte: 'mercado_livre_api',
                    moeda: item.currency_id || 'BRL',
                    currency_id: item.currency_id || 'BRL',
                    installments: item.installments || null,
                    parcelamento_sem_juros: obterParcelamentoSemJurosFavoritos(item),
                    tipo_anuncio: normalizarTipoAnuncioFavoritos(item.listing_type_id || item.listing_type || item.listing_type_name || ''),
                    listing_type_id: item.listing_type_id || (item.listing_type && item.listing_type.id) || '',
                    listing_type_name: normalizarTipoAnuncioFavoritos(item.listing_type_id || item.listing_type || item.listing_type_name || ''),
                    shipping: shippingInfo,
                    logistic_type: logisticType,
                    shipping_mode: shippingInfo.mode || '',
                    is_full: String(logisticType || '').toLowerCase() === 'fulfillment',
                    data_criacao: item.date_created || item.start_time || '',
                    vendedor: vendedor || item.seller_name || '',
                    vendedorFonte: (vendedor || item.seller_name) ? 'mercado_livre_api' : '',
                    vendedor_fonte: (vendedor || item.seller_name) ? 'mercado_livre_api' : '',
                    fonte_vendedor: (vendedor || item.seller_name) ? 'mercado_livre_api' : '',
                    vendas: parseNumeroVendas(item.sold_quantity ?? item.sold ?? item.soldQuantity ?? null),
                    seller_id: sellerId || null,
                    condicao: obterCondicaoAnuncioFavoritos(item),
                    condition: obterCondicaoAnuncioFavoritos(item),
                    item_condition: obterCondicaoAnuncioFavoritos(item)
                };
                info.source = 'api';
                const finalInfo = viaElectron || {};
                if (!hasTexto(finalInfo.titulo)) {
                    finalInfo.titulo = info.titulo;
                    finalInfo.tituloFonte = info.tituloFonte;
                    finalInfo.titulo_fonte = info.titulo_fonte;
                }
                if (!hasTexto(finalInfo.data_criacao)) {
                    finalInfo.data_criacao = info.data_criacao;
                }
                if (!hasTexto(finalInfo.vendedor)) {
                    finalInfo.vendedor = info.vendedor;
                }
                if (!hasTexto(finalInfo.vendedorFonte) && hasTexto(finalInfo.vendedor)) {
                    finalInfo.vendedorFonte = info.vendedorFonte || info.vendedor_fonte || info.fonte_vendedor || 'mercado_livre_api';
                    finalInfo.vendedor_fonte = finalInfo.vendedorFonte;
                    finalInfo.fonte_vendedor = finalInfo.vendedorFonte;
                }
                if (!hasNumeroVendas(finalInfo.vendas)) {
                    finalInfo.vendas = info.vendas;
                }
                if (!hasTexto(finalInfo.id)) {
                    finalInfo.id = info.id;
                }
                const urlFinal = normalizarUrlItemMercadoLivreFavoritos(finalInfo.url || finalInfo.permalink || finalInfo.link || info.url, finalInfo.id || itemId);
                if (!hasTexto(finalInfo.url)) {
                    finalInfo.url = urlFinal;
                    finalInfo.linkFonte = info.linkFonte;
                    finalInfo.link_fonte = info.link_fonte;
                }
                if (!hasTexto(finalInfo.permalink)) {
                    finalInfo.permalink = urlFinal;
                }
                if (!hasTexto(finalInfo.link)) {
                    finalInfo.link = urlFinal;
                }
                if (!hasTexto(finalInfo.link_normalizado)) {
                    finalInfo.link_normalizado = urlFinal;
                }
                if (!hasTexto(finalInfo.imagem)) {
                    finalInfo.imagem = info.imagem;
                    finalInfo.fotoFonte = info.fotoFonte;
                    finalInfo.foto_fonte = info.foto_fonte;
                }
                if (!hasTexto(finalInfo.thumbnail)) {
                    finalInfo.thumbnail = info.thumbnail || info.imagem;
                }
                if (!hasTexto(finalInfo.foto)) {
                    finalInfo.foto = info.foto || info.imagem;
                }
                if (!Array.isArray(finalInfo.pictures) || !finalInfo.pictures.length) {
                    finalInfo.pictures = info.pictures || [];
                }
                preencherPrecoAnuncioFavoritos(finalInfo, info);
                preencherTipoAnuncioFavoritos(finalInfo, info);
                preencherCondicaoAnuncioFavoritos(finalInfo, info);
                if (!finalInfo.seller_id) {
                    finalInfo.seller_id = info.seller_id;
                }
                if (!finalInfo.source) {
                    finalInfo.source = info.source;
                }
                if (!temInformacao(finalInfo)) {
                    if (viaElectron && temInformacao(viaElectron)) {
                        consultarItemApiMercadoLivre.cache.set(itemId, viaElectron);
                        return viaElectron;
                    }
                    return null;
                }
                consultarItemApiMercadoLivre.cache.set(itemId, finalInfo);
                return finalInfo;
            } catch (err) {
                console.warn('Falha ao consultar API pública do ML:', itemId, err);
                return null;
            }
        }

        async function consultarItemApiMercadoLivre(itemId) {
            itemId = String(itemId || '').trim().toUpperCase();
            if (!itemId) return null;
            consultarItemApiMercadoLivre.cache = consultarItemApiMercadoLivre.cache || new Map();
            consultarItemApiMercadoLivre.inflight = consultarItemApiMercadoLivre.inflight || new Map();
            if (consultarItemApiMercadoLivre.cache.has(itemId)) {
                return consultarItemApiMercadoLivre.cache.get(itemId);
            }
            if (consultarItemApiMercadoLivre.inflight.has(itemId)) {
                return consultarItemApiMercadoLivre.inflight.get(itemId);
            }
            const promise = consultarItemApiMercadoLivreSemDedupe(itemId);
            consultarItemApiMercadoLivre.inflight.set(itemId, promise);
            try {
                return await promise;
            } finally {
                if (consultarItemApiMercadoLivre.inflight.get(itemId) === promise) {
                    consultarItemApiMercadoLivre.inflight.delete(itemId);
                }
            }
        }

        async function executarComConcorrencia(items, limite, worker) {
            const lista = Array.isArray(items) ? items : [];
            let index = 0;
            const totalWorkers = Math.min(Math.max(limite || 1, 1), lista.length || 1);
            await Promise.all(Array.from({ length: totalWorkers }, async (_, workerIndex) => {
                while (index < lista.length) {
                    const atual = lista[index];
                    index += 1;
                    await worker(atual, workerIndex);
                }
            }));
        }

        function criarMlWebviewOculto(indice = 0) {
            garantirExtensoesNavegadorMl();
            let webview = document.getElementById(`ml-hidden-date-webview-${indice}`);
            if (webview) return webview;

            webview = document.createElement('webview');
            webview.id = `ml-hidden-date-webview-${indice}`;
            webview.setAttribute('partition', obterParticaoNavegadorPersistente());
            webview.setAttribute('webpreferences', 'contextIsolation=yes,nodeIntegration=no');
            webview.style.cssText = 'position:absolute;left:-10000px;top:-10000px;width:1280px;height:900px;opacity:0.01;pointer-events:none;';
            webview.addEventListener('dom-ready', () => tentarLoginAvantProNoWebview(webview, { somenteSeAutorizado: true, verificarDadosAntes: true }));
            webview.addEventListener('did-finish-load', () => tentarLoginAvantProNoWebview(webview, { somenteSeAutorizado: true, verificarDadosAntes: true }));
            webview.addEventListener('did-finish-load', salvarSessaoNavegadorElectron);
            webview.addEventListener('did-navigate', salvarSessaoNavegadorElectron);
            document.body.appendChild(webview);
            return webview;
        }

        const AVANT_PRO_AUTOLOGIN_SCRIPT = `
            (function () {
                try {
                    var email = ${JSON.stringify(AVANT_PRO_LOGIN_EMAIL)};
                    if (!email) {
                        return { success: false, reason: 'email_usuario_indisponivel', url: String(location.href || '') };
                    }
                    var href = String(location.href || '');
                    var host = String(location.hostname || '');
                    var pageText = String(document.body && document.body.innerText ? document.body.innerText : '');
                    var avantPattern = /avant\\s*pro|avantpro|avantprocloud/i;
                    var avantUiPattern = /avant\\s*pro|avantpro|avantprocloud|ferramentas|vincular\\s+(?:conta|agora)|conectar\\s+conta|use\\s+gratis|usar\\s+gratis|iniciar\\s+sess[aã]o|insira\\s+suas\\s+credenciais|seu\\s+e-?mail|rotulos\\s+visuais|r[oó]tulos\\s+visuais/i;
                    var norm = function (value) {
                        var text = String(value || '');
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (e) {}
                        return text.toLowerCase().replace(/\\s+/g, ' ').trim();
                    };
                    var attrText = function (el) {
                        if (!el || !el.getAttribute) return '';
                        return [
                            el.getAttribute('type'),
                            el.getAttribute('name'),
                            el.getAttribute('id'),
                            el.getAttribute('class'),
                            el.getAttribute('placeholder'),
                            el.getAttribute('aria-label'),
                            el.getAttribute('autocomplete'),
                            el.getAttribute('action'),
                            el.getAttribute('src'),
                            el.getAttribute('href'),
                            el.value
                        ].join(' ');
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
                    var hasSearchSignals = !!document.querySelector('[class*="ui-search"], ol.ui-search-layout, section.ui-search-results, a[href*="/MLB-"], a[href*="/p/MLB"], a[href*="MLB"]');
                    var isMercadoLivreHost = /(^|\\.)mercadolivre\\.com\\.br$|(^|\\.)mercadolibre\\.com/i.test(host);
                    var plainHref = norm(href);
                    var plainPage = norm(pageText);
                    if (/obrigado\\s+por\\s+usar\\s+nossa\\s+extensao|obrigado\\s+por\\s+usar\\s+nossa\\s+extens[aã]o|aguarde[,\\s]+a\\s+pagina\\s+sera\\s+recarregada|aguarde[,\\s]+a\\s+p[aá]gina\\s+ser[aá]\\s+recarregada/.test(plainPage)) {
                        return { success: true, confirmed: true, reason: 'agradecimento_avant_pro', url: href };
                    }
                    var mlLoginUrl = /\\/jms\\/.*\\/lgz\\//i.test(href) || /\\/login\\b|\\/registration\\b|account-verification|access-denied|captcha|recaptcha/i.test(href);
                    var mlLoginText = /entre na sua conta|iniciar sessao|acesse sua conta|insira seu e-?mail|digite seu e-?mail|e-?mail ou telefone|email ou telefone|verificacao|nao sou um robo|captcha/.test(plainHref + ' ' + plainPage);
                    var avantLoginText = /avant\\s*pro|avantpro|avantprocloud/.test(plainPage)
                        && /iniciar sessao|insira suas credenciais|seu e-?mail|seu email|clique aqui|chame o suporte/.test(plainPage);
                    if (isMercadoLivreHost && !avantLoginText && (mlLoginUrl || (mlLoginText && !hasSearchSignals))) {
                        return { success: false, reason: 'mercado_livre_deslogado', url: href };
                    }

                    var resourceHasAvant = queryAllDeep('iframe[src], script[src], link[href], a[href], img[src]').some(function (node) {
                        return avantUiPattern.test(attrText(node));
                    });
                    var isAuthUrl = /auth\\.avantprocloud|avantprocloud.*auth|avantpro.*login|avant\\-?pro.*login/i.test(href);
                    var hasAvantDom = avantLoginText || avantUiPattern.test(href + ' ' + document.title + ' ' + pageText) || resourceHasAvant || !!queryAllDeep('[class*="avant"], [id*="avant"], .avantpro-product-info-row, .created-time-card').length;
                    var recentAvantClick = Number.isFinite(window.__JK_AVANT_PRO_CLICKED_AT) && Date.now() - window.__JK_AVANT_PRO_CLICKED_AT < 30000;
                    if (!isAuthUrl && !hasAvantDom && !recentAvantClick) {
                        return { success: false, reason: 'avant_nao_detectado', url: href };
                    }

                    var isVisible = function (el) {
                        var rect = el && el.getBoundingClientRect ? el.getBoundingClientRect() : { width: 0, height: 0 };
                        var style = el && window.getComputedStyle ? window.getComputedStyle(el) : null;
                        return rect.width > 0 && rect.height > 0 && (!style || (style.visibility !== 'hidden' && style.display !== 'none'));
                    };
                    var contextRoot = function (input) {
                        var root = null;
                        try {
                            root = input.closest('form, [role="dialog"], [class*="avant"], [id*="avant"], [class*="modal"], [class*="login"], [class*="auth"], [class*="popup"], [class*="drawer"]');
                        } catch (_err) {}
                        return root || input.form || input.parentElement || composedHost(input) || document.body;
                    };
                    var contextText = function (input) {
                        var root = contextRoot(input);
                        var host = composedHost(input);
                        return norm([
                            attrText(input),
                            attrText(root),
                            root && root.innerText ? root.innerText : '',
                            host && attrText(host),
                            host && host.innerText ? host.innerText : '',
                            input.labels ? Array.prototype.slice.call(input.labels).map(function (label) { return label.innerText || ''; }).join(' ') : ''
                        ].join(' '));
                    };
                    var isMlLoginField = function (input, ctx) {
                        var root = contextRoot(input);
                        var attrs = norm(attrText(input));
                        if (isMercadoLivreHost && root === document.body && mlLoginText) return true;
                        if (isMercadoLivreHost && mlLoginText && !/avant/.test(ctx)) return true;
                        if (/mercado\\s*livre|mercadolivre|mercadolibre/.test(ctx) && !/avant/.test(ctx)) return true;
                        if (isMercadoLivreHost && /user|usuario|telefone|phone|login_user|login|nickname/.test(attrs + ' ' + ctx) && !/avant/.test(ctx)) return true;
                        return false;
                    };
                    var inputs = queryAllDeep('input:not([type="hidden"])');
                    var emailInput = inputs.find(function (input) {
                        if (!input || input.disabled || input.readOnly || !isVisible(input)) return false;
                        var attrs = norm(attrText(input));
                        var ctx = contextText(input);
                        var isSearch = /search|buscar|pesquisar|as_word|\\bq\\b/.test(attrs);
                        var looksEmail = input.type === 'email' || /email|e-mail|mail/.test(attrs + ' ' + ctx);
                        var hasAvantContext = avantLoginText || avantUiPattern.test(ctx) || isAuthUrl || (recentAvantClick && !isMlLoginField(input, ctx));
                        if (isSearch || !looksEmail || !hasAvantContext) return false;
                        return !isMlLoginField(input, ctx);
                    });
                    if (!emailInput) {
                        var entradaAvant = queryAllDeep('button, a, input[type="button"], input[type="submit"], [role="button"], [tabindex]').find(function (btn) {
                            if (!btn || !isVisible(btn)) return false;
                            var label = norm(btn.innerText || btn.textContent || btn.value || btn.getAttribute && (btn.getAttribute('aria-label') || btn.getAttribute('title')) || '');
                            var root = null;
                            try {
                                root = btn.closest('form, article, section, aside, li, [role="dialog"], [class*="avant"], [id*="avant"], [class*="modal"], [class*="login"], [class*="auth"], [class*="popup"], [class*="drawer"]');
                            } catch (_err) {}
                            var ctx = norm([
                                label,
                                root && (root.innerText || root.textContent),
                                root && root.getAttribute && root.getAttribute('class'),
                                root && root.getAttribute && root.getAttribute('id')
                            ].filter(Boolean).join(' '));
                            var alvo = label + ' ' + ctx;
                            if (/vincular\\s+(?:conta|agora)|conectar\\s+conta|autorizar\\s+(?:mercado\\s+livre|conta)|permitir\\s+acesso/.test(alvo)
                                && /avant\\s*pro|avantpro|mercado\\s+livre|conta|vincul/.test(alvo)) return false;
                            if (!/avant\\s*pro|avantpro|comece\\s+a\\s+usar|liberar\\s+os\\s+recursos|extensao|vincule\\s+o\\s+avant/.test(ctx)) return false;
                            return /^login$|\\blogin\\b|fazer\\s+login|entrar\\s+no\\s+avant|login\\s+avant/.test(label);
                        });
                        if (entradaAvant) {
                            window.__JK_AVANT_PRO_CLICKED_AT = Date.now();
                            try { entradaAvant.scrollIntoView({ block: 'center', inline: 'center' }); } catch (_scrollErr) {}
                            setTimeout(function () {
                                var rect = entradaAvant.getBoundingClientRect ? entradaAvant.getBoundingClientRect() : null;
                                var opts = rect ? { bubbles: true, cancelable: true, view: window, clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 } : { bubbles: true, cancelable: true, view: window };
                                try { entradaAvant.dispatchEvent(new MouseEvent('mousedown', opts)); } catch (e0) {}
                                try { entradaAvant.dispatchEvent(new MouseEvent('mouseup', opts)); } catch (e1) {}
                                try { entradaAvant.dispatchEvent(new MouseEvent('click', opts)); } catch (e2) {}
                                try { entradaAvant.click(); } catch (e3) {}
                            }, 180);
                            return { success: false, reason: 'acao_conta_avant_clicada', clickedLoginButton: true, url: href, avantDetectado: true };
                        }
                        return { success: false, reason: 'campo_email_avant_nao_encontrado', url: href, avantDetectado: !!(isAuthUrl || hasAvantDom || recentAvantClick) };
                    }

                    var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    setter.call(emailInput, email);
                    emailInput.dataset.jkAvantEmailFilled = '1';
                    emailInput.focus();
                    emailInput.dispatchEvent(new Event('input', { bubbles: true }));
                    emailInput.dispatchEvent(new Event('change', { bubbles: true }));
                    emailInput.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'Enter' }));

                    var root = contextRoot(emailInput);
                    var buttons = queryAllDeep('button, input[type="submit"], input[type="button"], [role="button"]', root);
                    var submit = buttons.find(function (btn) {
                        if (!isVisible(btn)) return false;
                        var label = norm(btn.innerText || btn.value || btn.getAttribute('aria-label') || '');
                        return /entrar|acessar|login|iniciar|continuar|enviar|comecar/.test(label);
                    }) || (emailInput.form ? Array.prototype.slice.call(emailInput.form.querySelectorAll('button, input[type="submit"]'))[0] : null);
                    if (submit) {
                        setTimeout(function () {
                            var rect = submit.getBoundingClientRect ? submit.getBoundingClientRect() : null;
                            var opts = rect ? { bubbles: true, cancelable: true, view: window, clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 } : { bubbles: true, cancelable: true, view: window };
                            try { submit.dispatchEvent(new MouseEvent('mousedown', opts)); } catch (e0) {}
                            try { submit.dispatchEvent(new MouseEvent('mouseup', opts)); } catch (e1) {}
                            try { submit.dispatchEvent(new MouseEvent('click', opts)); } catch (e2) {}
                            try { submit.click(); } catch (e) {}
                            try { emailInput.form && emailInput.form.requestSubmit && emailInput.form.requestSubmit(); } catch (e) {}
                        }, 250);
                    }

                    return { success: true, clicked: !!submit, url: href, context: 'avant_pro' };
                } catch (err) {
                    return { success: false, error: err && err.message ? err.message : String(err), url: String(location.href || '') };
                }
            })();
        `;

        function tentarLoginAvantProNoWebview(webview, opcoes = {}) {
            if (!webview || typeof webview.executeJavaScript !== 'function') return Promise.resolve(null);
            const loginAutorizado = typeof loginAvantProAutorizado !== 'function' || loginAvantProAutorizado();
            const duranteFavoritos = typeof mlFavoritosEmExecucao !== 'undefined' && !!mlFavoritosEmExecucao;
            if (opcoes.somenteSeAutorizado === true && !loginAutorizado && !duranteFavoritos) {
                return Promise.resolve({ success: false, skipped: true, reason: 'login_avant_nao_autorizado' });
            }
            const diagnosticarAntes = () => {
                const podeDiagnosticarWebviewAtual = opcoes.verificarDadosAntes === true
                    && typeof diagnosticarAvantProNoWebview === 'function'
                    && typeof mlWebviewEl !== 'undefined'
                    && webview === mlWebviewEl;
                if (!podeDiagnosticarWebviewAtual) return Promise.resolve(null);
                return diagnosticarAvantProNoWebview(webview).then((status) => {
                    const temDados = typeof statusAvantProTemDadosColetaveis === 'function'
                        ? statusAvantProTemDadosColetaveis(status)
                        : !!(status && (status.hasAvantData || status.rows > 0 || status.dataTextNodes > 0 || status.bodyDataLabels > 1));
                    return temDados
                        ? { success: false, skipped: true, reason: 'avant_dados_ja_disponiveis', status }
                        : null;
                }).catch(() => null);
            };
            const executarNoSubframeElectron = (resultadoAnterior = null) => {
                const api = window.electronAPI || null;
                if (!api || typeof api.loginAvantProEmbeddedBrowser !== 'function') {
                    return Promise.resolve(resultadoAnterior);
                }
                return api.loginAvantProEmbeddedBrowser(AVANT_PRO_LOGIN_EMAIL)
                    .then((result) => {
                        if (result && result.success) {
                            console.log('Login Avant Pro preenchido automaticamente no iframe:', result);
                            return {
                                ...result,
                                via: 'electron_auth_subframe'
                            };
                        }
                        return resultadoAnterior || result || null;
                    })
                    .catch(() => resultadoAnterior);
            };
            const executar = () => {
                return webview.executeJavaScript(AVANT_PRO_AUTOLOGIN_SCRIPT, true).then((result) => {
                    if (result && result.success) {
                        console.log('Login Avant Pro preenchido automaticamente:', result);
                        return result;
                    }
                    return executarNoSubframeElectron(result || null);
                }).catch(() => executarNoSubframeElectron(null));
            };
            const atrasos = Array.isArray(opcoes.atrasos) && opcoes.atrasos.length
                ? opcoes.atrasos.map(Number).filter(delay => Number.isFinite(delay) && delay >= 0)
                : [0, 160, 380, 750, 1300, 2200, 3000];
            const timeoutFinal = Math.max(
                atrasos[atrasos.length - 1] + 250,
                Number(opcoes.timeoutMs) || atrasos[atrasos.length - 1] + 400
            );
            return diagnosticarAntes().then((skip) => {
                if (skip && skip.skipped) return skip;
                return new Promise((resolve) => {
                    let resolvido = false;
                    let ultimo = null;
                    const finalizar = (resultado, forcar = false) => {
                        if (resultado) ultimo = resultado;
                        if (resolvido) return;
                        if (forcar || (resultado && resultado.success)) {
                            resolvido = true;
                            resolve(resultado || ultimo || null);
                        }
                    };
                    atrasos.forEach((delay) => {
                        setTimeout(() => {
                            executar().then((resultado) => finalizar(resultado)).catch(() => finalizar(null));
                        }, delay);
                    });
                    setTimeout(() => finalizar(ultimo, true), timeoutFinal);
                });
            });
        }
        function carregarUrlNoWebview(webview, url) {
            return new Promise((resolve, reject) => {
                let done = false;
                const finish = (err) => {
                    if (done) return;
                    done = true;
                    clearTimeout(timer);
                    webview.removeEventListener('did-finish-load', onLoad);
                    webview.removeEventListener('did-fail-load', onFail);
                    if (err) reject(err);
                    else resolve();
                };
                const onLoad = () => finish();
                const onFail = (event) => finish(new Error((event && event.errorDescription) || 'Falha ao carregar anúncio.'));
                const timer = setTimeout(() => finish(new Error('Tempo esgotado ao abrir anúncio.')), 10000);
                webview.addEventListener('did-finish-load', onLoad);
                webview.addEventListener('did-fail-load', onFail);
                webview.src = url;
            });
        }

        const ML_DATE_EXTRACT_SCRIPT = `
            (async function () {
                try {
                    var sleep = function (ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); };
                    for (var wait = 0; wait < 24; wait += 1) {
                        if (document.querySelector('.ui-pdp-seller__header__title, .ui-pdp-seller__link-trigger, .ui-pdp-seller__nickname, [data-testid="seller-info"], [class*="avantpro"], [id*="avantpro"], .created-time-card')) break;
                        await sleep(250);
                    }
                    var html = String(document.documentElement && document.documentElement.outerHTML ? document.documentElement.outerHTML : '');
                    var decodedHtml = html
                        .replace(/\\\\u002F/g, '/')
                        .replace(/\\\\\\//g, '/')
                        .replace(/\\\\n/g, ' ')
                        .replace(/\\\\t/g, ' ')
                        .replace(/\\\\r/g, ' ')
                        .replace(/\\\\&quot;/g, '"')
                        .replace(/&quot;/g, '"')
                        .replace(/\\\\'/g, "'")
                        .replace(/\\\\"/g, '"');
                    var patterns = [
                        /"date_created"\\s*:\\s*"([^"]+)"/i,
                        /"dateCreated"\\s*:\\s*"([^"]+)"/i,
                        /"date_created"\\s*:\\s*\\{\\s*"value"\\s*:\\s*"([^"]+)"/i,
                        /"start_time"\\s*:\\s*"([^"]+)"/i,
                        /"startTime"\\s*:\\s*"([^"]+)"/i,
                        /"item_date_created"\\s*:\\s*"([^"]+)"/i,
                        /"creation_date"\\s*:\\s*"([^"]+)"/i,
                        /"creationDate"\\s*:\\s*"([^"]+)"/i,
                        /"listing_start_time"\\s*:\\s*"([^"]+)"/i,
                        /"start_date"\\s*:\\s*"([^"]+)"/i,
                        /"itemStartTime"\\s*:\\s*"([^"]+)"/i
                    ];
                    var sellerPatterns = [
                        /"seller"\\s*:\\s*\\{[^{}]{0,220}?\"seller_name\"\\s*:\\s*\"([^\"]+)\"/i,
                        /"seller"\\s*:\\s*\\{[^{}]{0,220}?\"nickname\"\\s*:\\s*\"([^\"]+)\"/i,
                        /"official_store"\\s*:\\s*\\{[^{}]{0,220}?\"official_store_name\"\\s*:\\s*\"([^\"]+)\"/i,
                        /"official_store"\\s*:\\s*\\{[^{}]{0,220}?\"name\"\\s*:\\s*\"([^\"]+)\"/i,
                        /"officialStoreName"\\s*:\\s*\"([^\"]+)\"/i,
                        /"sellerName"\\s*:\\s*\"([^\"]+)\"/i
                    ];
                    var dateKeys = {
                        date_created: true,
                        dateCreated: true,
                        start_time: true,
                        startTime: true,
                        item_date_created: true,
                        creation_date: true,
                        creationDate: true,
                        listing_start_time: true,
                        start_date: true,
                        itemStartTime: true
                    };
                    var sellerKeys = {
                        seller_name: true,
                        sellerName: true,
                        nickname: true,
                        official_store_name: true,
                        officialStoreName: true
                    };
                    var normalizarNomeVendedor = function (value) {
                        return String(value || '')
                            .replace(/\\\\u002F/g, '/')
                            .replace(/\\\\\\//g, '/')
                            .replace(/\\\\n/g, ' ')
                            .replace(/\\\\t/g, ' ')
                            .replace(/\\\\r/g, ' ')
                            .replace(/&quot;/g, '\"')
                            .replace(/\\\\'/g, \"'\")
                            .replace(/\\\\"/g, '\"')
                            .replace(/^(vendido\\s*por|loja\\s+oficial|oficial\\s+loja)\\s*/i, '')
                            .trim();
                    };
                    var normalizarNomeVendedorBusca = function (value) {
                        return normalizarNomeVendedor(value)
                            .toLowerCase()
                            .replace(/[^a-z0-9]+/g, ' ')
                            .replace(/\\s+/g, ' ')
                            .trim();
                    };
                    var vendedorValido = function (valor) {
                        var texto = normalizarNomeVendedor(valor);
                        if (!texto) return false;
                        if (texto.length < 2 || texto.length > 120) return false;
                        if (!/[A-Za-z0-9]/.test(texto)) return false;
                        if (/^\\d+$/.test(texto)) return false;
                        var textoBusca = normalizarNomeVendedorBusca(texto);
                        if (!textoBusca) return false;
                        if (/(^|\\b)(anuncio criado|an ncio criado|criado em|catalogo criado|cat logo criado|vendas produto|total vendas|quantidade vendas)(\\b|$)/i.test(textoBusca)) return false;
                        return !/^(vendido|vendedor|anuncio|anunci[oó]|produto|frete|envio|loja|oferta|ofertas|desconto|comprar|comprando|login|entrar|cadastro|email|senha|contato|perfil|busca|filtro|categoria|condi[cç][aã]o|aviso|informa[cç][aã]o|cria[cç][aã]o|valor|pre[cç]o)$/i.test(textoBusca);
                    };
                    var escolherNomeVendedor = function (candidatos) {
                        var opcoes = Array.prototype.slice.call(candidatos || []);
                        var melhor = '';
                        var melhorScore = -1;
                        var melhorPrio = -1;
                        for (var oi = 0; oi < opcoes.length; oi += 1) {
                            var op = opcoes[oi];
                            var valor = typeof op === 'string' ? op : op && op.valor;
                            var prio = Number(op && op.prioridade) || 0;
                            var nome = normalizarNomeVendedor(valor);
                            if (!vendedorValido(nome)) continue;
                            var score = nome.length;
                            if (/\\s/.test(nome)) score += 6;
                            if (score > melhorScore) {
                                melhor = nome;
                                melhorScore = score;
                                melhorPrio = prio;
                            } else if (score === melhorScore && prio > melhorPrio) {
                                melhor = nome;
                                melhorPrio = prio;
                            }
                        }
                        return melhor;
                    };
                    var normalizarTextoMl = function (value) {
                        return String(value || '')
                            .replace(/\\\\u002F/g, '/')
                            .replace(/\\\\\\//g, '/')
                            .replace(/\\\\n/g, ' ')
                            .replace(/\\\\t/g, ' ')
                            .replace(/\\\\r/g, ' ')
                            .replace(/&quot;/g, '"')
                            .replace(/\\\\"/g, '"')
                            .trim();
                    };
                    var procurarDataEmTexto = function (text) {
                        var base = normalizarTextoMl(text);
                        for (var p = 0; p < patterns.length; p += 1) {
                            var textMatch = base.match(patterns[p]);
                            if (textMatch && textMatch[1]) return normalizarTextoMl(textMatch[1]);
                        }
                        return '';
                    };
                    var procurarVendedorEmTexto = function (text) {
                        var base = normalizarTextoMl(text);
                        var melhor = '';
                        var melhorScore = -1;
                        for (var p = 0; p < sellerPatterns.length; p += 1) {
                            var sellerMatchText = base.match(sellerPatterns[p]);
                            if (!sellerMatchText || !sellerMatchText[1]) continue;
                            var candidato = normalizarNomeVendedor(sellerMatchText[1]);
                            if (!vendedorValido(candidato)) continue;
                            var score = candidato.length + (/\\s/.test(candidato) ? 6 : 0);
                            if (score > melhorScore) {
                                melhor = candidato;
                                melhorScore = score;
                            }
                        }
                        return melhor;
                    };
                    var parseVendas = function (value, suffix) {
                        var raw = String(value || '').trim().toLowerCase();
                        if (!raw) return null;
                        var numeroTexto = String(raw).replace(/\\s+/g, '');
                        if (numeroTexto.indexOf('.') >= 0 && numeroTexto.indexOf(',') >= 0) {
                            numeroTexto = numeroTexto.lastIndexOf('.') > numeroTexto.lastIndexOf(',')
                                ? numeroTexto.replace(/,/g, '')
                                : numeroTexto.replace(/\\./g, '').replace(/,/g, '.');
                        } else if (numeroTexto.indexOf(',') >= 0) {
                            numeroTexto = /^\\d{1,3}(?:,\\d{3})+$/.test(numeroTexto)
                                ? numeroTexto.replace(/,/g, '')
                                : numeroTexto.replace(/,/g, '.');
                        } else if (numeroTexto.indexOf('.') >= 0) {
                            numeroTexto = /^\\d{1,3}(?:\\.\\d{3})+$/.test(numeroTexto)
                                ? numeroTexto.replace(/\\./g, '')
                                : numeroTexto;
                        }
                        var parsed = parseFloat(numeroTexto);
                        if (!isFinite(parsed)) return null;
                        suffix = String(suffix || '').toLowerCase();
                        if (suffix === 'mil' || suffix === 'k') parsed *= 1000;
                        return Math.round(parsed);
                    };
                    var normalizarVendas = function (valor) {
                        if (valor === null || valor === undefined || valor === '') return null;
                        if (typeof valor === 'number') return isFinite(valor) ? valor : null;
                        var match = String(valor).trim().match(/([0-9][0-9\.,]*)\s*(k|mil)?\b/i);
                        if (!match) return null;
                        return parseVendas(match[1], match[2]);
                    };
                    var coalesceVendas = function (primario, fallback) {
                        var primarioNum = normalizarVendas(primario);
                        if (Number.isFinite(primarioNum)) return primarioNum;
                        var fallbackNum = normalizarVendas(fallback);
                        return Number.isFinite(fallbackNum) ? fallbackNum : null;
                    };
                    var hasNumeroVendas = function (valor) {
                        return Number.isFinite(normalizarVendas(valor));
                    };
                    var normalizarItemId = function (valor) {
                        var texto = String(valor || '').toUpperCase();
                        var match = texto.match(/MLB-?\d+/i);
                        if (!match) return '';
                        return match[0].replace('-', '');
                    };
                    var itemIdNaPagina = (function () {
                        var href = String(location.href || '');
                        var candidato = normalizarItemId(href);
                        return candidato;
                    })();
                                        var normalizarItemIdTexto = function (valor) {
                        return normalizarItemId(valor);
                    };
                    var objetoTemItemId = function (obj) {
                        if (!obj || !itemIdNaPagina) return false;
                        var candidatos = [];
                        if (typeof obj === 'string' || typeof obj === 'number') {
                            candidatos = [obj];
                        } else if (typeof obj === 'object') {
                            candidatos = [
                                obj.id,
                                obj.item_id,
                                obj.itemId,
                                obj.itemID,
                                obj.item && obj.item.id,
                                obj.item && obj.item.item_id,
                                obj.item && obj.item.itemId,
                                obj.item && obj.item.itemID,
                                obj.product && obj.product.id,
                                obj.product && obj.product.item_id,
                                obj.product && obj.product.itemId,
                                obj.product && obj.product.itemID,
                                obj.item_info && obj.item_info.id,
                                obj.item_info && obj.item_info.item_id,
                                obj.item_info && obj.item_info.itemId,
                                obj.item_info && obj.item_info.itemID
                            ];
                        }
                        for (var ci = 0; ci < candidatos.length; ci += 1) {
                            if (normalizarItemIdTexto(candidatos[ci]) === itemIdNaPagina) return true;
                        }
                        return false;
                    };
                    var coletarVendedorEntrada = function (valor, prioridade) {
                        var prioridadeBase = Number(prioridade) || 0;
                        var out = [];
                        if (!valor) return out;
                        if (typeof valor === 'string' || typeof valor === 'number') {
                            var nomeLiteral = normalizarNomeVendedor(valor);
                            if (vendedorValido(nomeLiteral)) out.push({ valor: nomeLiteral, prioridade: prioridadeBase });
                            return out;
                        }
                        if (typeof valor !== 'object') return out;
                        if (valor.nickname) out = out.concat(coletarVendedorEntrada(valor.nickname, prioridadeBase + 30));
                        if (valor.official_store_name) out = out.concat(coletarVendedorEntrada(valor.official_store_name, prioridadeBase + 40));
                        if (valor.officialStoreName) out = out.concat(coletarVendedorEntrada(valor.officialStoreName, prioridadeBase + 40));
                        if (valor.seller_name) out = out.concat(coletarVendedorEntrada(valor.seller_name, prioridadeBase + 36));
                        if (valor.name) out = out.concat(coletarVendedorEntrada(valor.name, prioridadeBase + 20));
                        if (valor.title) out = out.concat(coletarVendedorEntrada(valor.title, prioridadeBase + 5));
                        return out;
                    };
                    var procurarVendasEmTexto = function (text) {
                        var base = normalizarTextoMl(text);
                        var salesPatterns = [
                            /vendas\\s+do\\s+(?:anuncio|item)(?:\\s+ganhador)?\\s*(?:[:\\-]|=)?\\s*(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i,
                            /vendas\\s+deste\\s+anuncio\\s*(?:[:\\-]|=)?\\s*(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i,
                            /vendas\\s+do\\s+vendedor\\s+neste\\s+anuncio\\s*(?:[:\\-]|=)?\\s*(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i,
                            /(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?\\s+vendid[oa]s?\\b/i
                        ];
                        for (var vp = 0; vp < salesPatterns.length; vp += 1) {
                            var salesMatch = base.match(salesPatterns[vp]);
                            if (salesMatch && salesMatch[1]) return parseVendas(salesMatch[1], salesMatch[2]);
                        }
                        return null;
                    };
                    var procurarEmObjeto = function (root) {
                        var stack = [root];
                        var seen = [];
                        var melhor = {
                            score: -1,
                            data_criacao: '',
                            vendedor: '',
                            vendas: null
                        };

                        var avaliar = function (cand) {
                            if (!cand) return;
                            var data = normalizarTextoMl(cand.data_criacao || '');
                            var vendedor = normalizarNomeVendedor(cand.vendedor || '');
                            var vendedorOk = vendedorValido(vendedor);
                            var vendas = normalizarVendas(cand.vendas);
                            var temDados = !!data || vendedorOk || Number.isFinite(vendas);

                            if (!temDados) return;

                            var score = 0;
                            if (cand.itemMatch) score += 140;
                            if (cand.idMatch) score += 20;
                            if (data) score += 40;
                            if (vendedorOk) score += 24 + (vendedor.length > 12 ? 4 : 0);
                            if (Number.isFinite(vendas)) score += 30;
                            if (cand.prioridade) score += cand.prioridade;
                            if (cand.itemMatch || cand.idMatch) score += 16;

                            if (score > melhor.score) {
                                melhor = {
                                    score: score,
                                    data_criacao: data || melhor.data_criacao,
                                    vendedor: vendedorOk ? vendedor : melhor.vendedor,
                                    vendas: Number.isFinite(vendas) ? vendas : melhor.vendas
                                };
                            }
                        };

                        while (stack.length) {
                            var cur = stack.pop();
                            if (!cur || typeof cur !== 'object') continue;
                            if (seen.indexOf(cur) >= 0) continue;
                            seen.push(cur);
                            if (seen.length > 5000) break;

                            if (Array.isArray(cur)) {
                                for (var ai = 0; ai < cur.length; ai += 1) stack.push(cur[ai]);
                                continue;
                            }

                            var itemMatch = objetoTemItemId(cur);
                            var vendedores = [];
                            var dataTexto = '';
                            var vendasAtual = null;
                            var itemKeyMatch = false;

                            for (var key in cur) {
                                if (!Object.prototype.hasOwnProperty.call(cur, key)) continue;
                                var value = cur[key];
                                if (dateKeys[key] && value) {
                                    if (key === 'date_created' && value && typeof value === 'object' && value.value) {
                                        dataTexto = normalizarTextoMl(value.value);
                                    } else if (typeof value === 'string' || typeof value === 'number') {
                                        dataTexto = normalizarTextoMl(value);
                                    }
                                    continue;
                                }
                                if (key === 'date_created' && value && typeof value === 'object' && value.value) {
                                    dataTexto = normalizarTextoMl(value.value);
                                    continue;
                                }
                                if ((key === 'sold_quantity' || key === 'soldQuantity' || key === 'sold') && value !== null && value !== undefined) {
                                    var candidatoVendas = normalizarVendas(value);
                                    if (Number.isFinite(candidatoVendas)) {
                                        vendasAtual = candidatoVendas;
                                    }
                                }
                                if (sellerKeys[key] && value) {
                                    vendedores = vendedores.concat(coletarVendedorEntrada(value, 22));
                                    continue;
                                }
                                if ((key === 'seller' || key === 'official_store' || key === 'seller_reputation') && value && typeof value === 'object') {
                                    itemKeyMatch = itemKeyMatch || objetoTemItemId(value);
                                    vendedores = vendedores.concat(coletarVendedorEntrada(value, key === 'official_store' ? 90 : 70));
                                }

                                if (value && typeof value === 'object') stack.push(value);
                            }

                            if (!itemMatch && !itemKeyMatch && cur && typeof cur === 'object' && cur.item && cur.item.id) {
                                itemKeyMatch = normalizarItemIdTexto(cur.item.id) === itemIdNaPagina;
                            }

                            avaliar({
                                data_criacao: dataTexto,
                                vendedor: escolherNomeVendedor(vendedores),
                                vendas: vendasAtual,
                                itemMatch: itemMatch,
                                idMatch: itemMatch || itemKeyMatch,
                                prioridade: itemMatch || itemKeyMatch ? 65 : 0
                            });
                        }
                        return melhor.score >= 0 ? melhor : null;
                    };
                    var limparDataVisivel = function (value) {
                        var txt = normalizarTextoMl(value)
                            .replace(/^[^0-9]*(?=\\d)/, '')
                            .replace(/\\s+/g, ' ')
                            .trim();
                        var iso = txt.match(/\\b20\\d{2}-\\d{2}-\\d{2}(?:T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?(?:Z|[+-]\\d{2}:?\\d{2})?)?\\b/);
                        if (iso && iso[0]) return iso[0];
                        var br = txt.match(/\\b\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}(?:\\s+\\d{1,2}:\\d{2}(?::\\d{2})?)?\\b/);
                        if (br && br[0]) return br[0];
                        return '';
                    };
                    var procurarDataAvantPro = function () {
                        var nodes = Array.prototype.slice.call(document.querySelectorAll('[class*="avantpro"], [id*="avantpro"], .created-time-card'));
                        nodes.push(document.body);
                        var labels = [
                            'Anúncio criado em',
                            'Anuncio criado em',
                            'Anúncio ganhador criado em',
                            'Anuncio ganhador criado em',
                            'Catálogo criado em',
                            'Catalogo criado em',
                            'Criado em'
                        ];
                        for (var ni = 0; ni < nodes.length; ni += 1) {
                            var text = String(nodes[ni] && nodes[ni].innerText ? nodes[ni].innerText : '').replace(/\\s+/g, ' ').trim();
                            if (!text) continue;
                            for (var li = 0; li < labels.length; li += 1) {
                                var idx = text.toLowerCase().indexOf(labels[li].toLowerCase());
                                if (idx < 0) continue;
                                var trecho = text.slice(idx + labels[li].length, idx + labels[li].length + 120);
                                var data = limparDataVisivel(trecho);
                                if (data) return { data_criacao: data, label: labels[li] };
                            }
                        }
                        return null;
                    };
                        var procurarVendasAvantPro = function () {
                        var parseVendasRotulada = function (text) {
                            var base = normalizarTextoMl(text)
                                .normalize('NFD')
                                .replace(/[\\u0300-\\u036f]/g, '');
                            var patterns = [
                                /vendas?\\s+do\\s+(?:anuncio|item|produto)(?:\\s+ganhador)?\\b\\s*(?:[:\\-]|=)?\\s*(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i,
                                /vendas?\\s+deste\\s+anuncio\\b\\s*(?:[:\\-]|=)?\\s*(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i,
                                /vendas?\\s+do\\s+vendedor\\s+neste\\s+anuncio\\b\\s*(?:[:\\-]|=)?\\s*(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i
                            ];
                            for (var pi = 0; pi < patterns.length; pi += 1) {
                                var match = base.match(patterns[pi]);
                                if (match && match[1]) return parseVendas(match[1], match[2]);
                            }
                            return null;
                        };
                        var normalizarLabel = function (value) {
                            return normalizarTextoMl(value)
                                .normalize('NFD')
                                .replace(/[\\u0300-\\u036f]/g, '')
                                .toLowerCase()
                                .replace(/[^a-z0-9]+/g, ' ')
                                .replace(/\\bvendas?\\b/g, 'venda')
                                .replace(/\\banuncios?\\b/g, 'anuncio')
                                .replace(/\\bitens?\\b/g, 'item')
                                .replace(/\\s+/g, ' ')
                                .trim();
                        };
                        var rows = Array.prototype.slice.call(document.querySelectorAll('.avantpro-product-info-row'));
                        for (var ri = 0; ri < rows.length; ri += 1) {
                            var row = rows[ri];
                            var labelNode = row.querySelector('.avantpro-product-info-row-label');
                            var valueNode = row.querySelector('.avantpro-product-info-row-value');
                            var label = normalizarLabel(labelNode && labelNode.textContent);
                            if (!valueNode || !/(venda do anuncio|venda deste anuncio|venda do item|venda do produto|venda do anuncio ganhador|venda do vendedor neste anuncio)/i.test(label)) continue;
                            var rowValue = normalizarVendas(valueNode.textContent);
                            if (!Number.isFinite(rowValue)) rowValue = parseVendasRotulada(label + ' ' + valueNode.textContent);
                            if (Number.isFinite(rowValue)) return { vendas: rowValue, fonte: 'avantpro_anuncio' };
                        }
                        var nodes = Array.prototype.slice.call(document.querySelectorAll('[class*="avantpro"], [id*="avantpro"], .created-time-card'));
                        nodes.push(document.body);
                        for (var ni = 0; ni < nodes.length; ni += 1) {
                            var text = String(nodes[ni] && nodes[ni].innerText ? nodes[ni].innerText : '').replace(/\\s+/g, ' ').trim();
                            if (!text) continue;
                            var vendasNode = parseVendasRotulada(text);
                            if (Number.isFinite(vendasNode)) return { vendas: vendasNode, fonte: 'avantpro_anuncio' };
                        }
                        return null;
                    };
                    var vendedor = '';
                    var vendedorFonte = '';
                    var vendasInfo = procurarVendasAvantPro();
                    var vendas = vendasInfo ? vendasInfo.vendas : null;
                    var vendasFonte = vendasInfo ? vendasInfo.fonte : '';
                    if (!Number.isFinite(normalizarVendas(vendas))) {
                        var vendasHtml = procurarVendasEmTexto(html);
                        if (!Number.isFinite(vendasHtml)) vendasHtml = procurarVendasEmTexto(decodedHtml);
                        if (Number.isFinite(vendasHtml)) {
                            vendas = vendasHtml;
                            vendasFonte = 'html_text';
                        }
                    }
                    var sellerNodes = [
                        '.ui-pdp-seller__header__title',
                        '.ui-pdp-seller__link-trigger',
                        '.ui-pdp-seller__nickname',
                        '.ui-pdp-official-store-label',
                        '[data-testid="seller-info"]',
                        '[data-testid="official-store-info"]'
                    ];
                    for (var s = 0; s < sellerNodes.length; s += 1) {
                        var node = document.querySelector(sellerNodes[s]);
                        if (node && node.textContent) {
                            var vendedorTexto = normalizarNomeVendedor(node.textContent.trim());
                            vendedorTexto = vendedorTexto.replace(/^(vendido\\s*por|loja\\s+oficial)\\s*/i, '').trim();
                            if (vendedorValido(vendedorTexto)) {
                                vendedor = vendedorTexto;
                                vendedorFonte = 'pagina_produto';
                                break;
                            }
                            if (vendedor) break;
                        }
                    }
                    if (!vendedor) {
                        vendedor = procurarVendedorEmTexto(html) || procurarVendedorEmTexto(decodedHtml);
                        if (vendedor) vendedorFonte = 'pagina_produto_fonte';
                    }
                    var avantFound = procurarDataAvantPro();
                    if (avantFound && avantFound.data_criacao) {
                        return { success: true, data_criacao: avantFound.data_criacao, vendedor: vendedor, vendedorFonte: vendedorFonte, vendas: vendas, vendasFonte: vendasFonte, source: 'avantpro_dom', avant_label: avantFound.label, url: location.href };
                    }
                    var dataHtml = procurarDataEmTexto(html) || procurarDataEmTexto(decodedHtml);
                    if (dataHtml) {
                        return { success: true, data_criacao: dataHtml, vendedor: vendedor, vendedorFonte: vendedorFonte, vendas: vendas, vendasFonte: vendasFonte, source: 'webview_codigo_fonte', url: location.href };
                    }
                    var scripts = Array.prototype.slice.call(document.querySelectorAll('script'));
                    for (var sc = 0; sc < scripts.length; sc += 1) {
                        var text = scripts[sc] && scripts[sc].textContent ? scripts[sc].textContent : '';
                        if (!text) continue;
                        if (!vendedor) {
                            vendedor = procurarVendedorEmTexto(text);
                            if (vendedor) vendedorFonte = 'pagina_produto_fonte';
                        }
                        var dataScript = procurarDataEmTexto(text);
                        if (dataScript) {
                            return { success: true, data_criacao: dataScript, vendedor: vendedor, vendedorFonte: vendedorFonte, vendas: coalesceVendas(vendas, null), vendasFonte: vendasFonte, source: 'script_texto', url: location.href };
                        }
                        var cleaned = normalizarTextoMl(text);
                            if (cleaned.charAt(0) === '{' || cleaned.charAt(0) === '[') {
                                try {
                                var jsonData = JSON.parse(cleaned);
                                var objFound = procurarEmObjeto(jsonData);
                                if (objFound && (objFound.data_criacao || objFound.vendedor || Number.isFinite(objFound.vendas))) {
                                    var objVendas = coalesceVendas(vendas, objFound.vendas);
                                    var objFonteVendas = Number.isFinite(normalizarVendas(vendas))
                                        ? vendasFonte
                                        : (Number.isFinite(normalizarVendas(objFound.vendas)) ? 'script_json_parseado' : vendasFonte);
                                    return { success: true, data_criacao: objFound.data_criacao, vendedor: vendedor || objFound.vendedor, vendedorFonte: vendedorFonte || (objFound.vendedor ? 'pagina_produto_fonte' : ''), vendas: objVendas, vendasFonte: objFonteVendas, source: 'script_json_parseado', url: location.href };
                                }
                            } catch (jsonErr) {}
                        }
                    }
                    var globals = [
                        window.__PRELOADED_STATE__,
                        window.__NEXT_DATA__,
                        window.__APOLLO_STATE__,
                        window.__INITIAL_STATE__,
                        window.__STATE__
                    ];
                    for (var gi = 0; gi < globals.length; gi += 1) {
                        var globalFound = procurarEmObjeto(globals[gi]);
                            if (globalFound && (globalFound.data_criacao || globalFound.vendedor || Number.isFinite(globalFound.vendas))) {
                                if (!vendedor) {
                                    var stackSeller = [globals[gi]];
                                    var seenSeller = [];
                                    while (stackSeller.length && !vendedor) {
                                    var curSeller = stackSeller.pop();
                                    if (!curSeller || typeof curSeller !== 'object') continue;
                                    if (seenSeller.indexOf(curSeller) >= 0) continue;
                                    seenSeller.push(curSeller);
                                    if (Array.isArray(curSeller)) {
                                        for (var si = 0; si < curSeller.length; si += 1) stackSeller.push(curSeller[si]);
                                    } else {
                                        for (var sk in curSeller) {
                                            if (!Object.prototype.hasOwnProperty.call(curSeller, sk)) continue;
                                            if (sellerKeys[sk] && curSeller[sk]) {
                                                vendedor = normalizarTextoMl(curSeller[sk]);
                                                vendedorFonte = 'pagina_produto_fonte';
                                                break;
                                            }
                                            if (curSeller[sk] && typeof curSeller[sk] === 'object') stackSeller.push(curSeller[sk]);
                                        }
                                        }
                                    }
                                }
                                if (!vendedor && globalFound.vendedor) {
                                    vendedor = globalFound.vendedor;
                                    vendedorFonte = 'pagina_produto_fonte';
                                }
                                var globalVendas = coalesceVendas(vendas, globalFound.vendas);
                                var globalFonteVendas = Number.isFinite(normalizarVendas(vendas))
                                    ? vendasFonte
                                    : (Number.isFinite(normalizarVendas(globalFound.vendas)) ? 'window_state' : vendasFonte);
                                return { success: true, data_criacao: globalFound.data_criacao, vendedor: vendedor, vendedorFonte: vendedorFonte, vendas: globalVendas, vendasFonte: globalFonteVendas, source: 'window_state', url: location.href };
                            }
                        }
                    return { success: !!(vendedor || hasNumeroVendas(vendas)), data_criacao: null, vendedor: vendedor, vendedorFonte: vendedorFonte, vendas: coalesceVendas(vendas, null), vendasFonte: vendasFonte, source: vendedor || hasNumeroVendas(vendas) ? (vendasFonte || 'webview_codigo_fonte') : null, url: location.href, title: document.title || '' };
                } catch (err) {
                    return { success: false, data_criacao: null, vendedor: null, vendas: null, error: err && err.message ? err.message : String(err), url: location.href };
                }
            })();
        `;

        const ML_SOURCE_SCAN_SCRIPT = `
            (function () {
                try {
                    var normalize = function (value) {
                        return String(value || '')
                            .replace(/\\\\u002F/g, '/')
                            .replace(/\\\\\\//g, '/')
                            .replace(/\\\\n/g, ' ')
                            .replace(/\\\\t/g, ' ')
                            .replace(/\\\\r/g, ' ')
                            .replace(/&quot;/g, '"')
                            .replace(/\\\\"/g, '"');
                    };
                    var unique = function (items) {
                        var seen = {};
                        var out = [];
                        for (var i = 0; i < items.length; i += 1) {
                            var value = String(items[i] || '');
                            if (!value || seen[value]) continue;
                            seen[value] = true;
                            out.push(value);
                        }
                        return out;
                    };
                    var html = normalize(document.documentElement && document.documentElement.outerHTML ? document.documentElement.outerHTML : '');
                    var bodyText = String(document.body && document.body.innerText ? document.body.innerText : '');
                    var fieldPatterns = [
                        /"date_created"\\s*:\\s*"([^"]+)"/gi,
                        /"dateCreated"\\s*:\\s*"([^"]+)"/gi,
                        /"start_time"\\s*:\\s*"([^"]+)"/gi,
                        /"startTime"\\s*:\\s*"([^"]+)"/gi,
                        /"creationDate"\\s*:\\s*"([^"]+)"/gi,
                        /"creation_date"\\s*:\\s*"([^"]+)"/gi,
                        /"listing_start_time"\\s*:\\s*"([^"]+)"/gi,
                        /"itemStartTime"\\s*:\\s*"([^"]+)"/gi
                    ];
                    var fields = [];
                    for (var p = 0; p < fieldPatterns.length; p += 1) {
                        var match;
                        while ((match = fieldPatterns[p].exec(html)) !== null) {
                            if (match[1]) fields.push(match[1]);
                        }
                    }
                    var dates = [];
                    var datePattern = /\\b20\\d{2}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?(?:Z|[+-]\\d{2}:?\\d{2})?\\b/g;
                    var dateMatch;
                    while ((dateMatch = datePattern.exec(html)) !== null) dates.push(dateMatch[0]);
                    var hints = [];
                    var hintPattern = /.{0,80}(?:date_created|dateCreated|start_time|startTime|creationDate|listing_start_time).{0,120}/gi;
                    var hintMatch;
                    while ((hintMatch = hintPattern.exec(html)) !== null) hints.push(String(hintMatch[0] || '').replace(/\\s+/g, ' '));
                    return {
                        success: true,
                        url: location.href,
                        title: document.title || '',
                        html_len: html.length,
                        text_preview: bodyText.replace(/\\s+/g, ' ').slice(0, 260),
                        verification: /account-verification|acesse sua conta|captcha|robot|verifica/i.test(location.href + ' ' + bodyText),
                        field_hits: unique(fields).slice(0, 40),
                        iso_date_hits: unique(dates).slice(0, 80),
                        source_hints: unique(hints).slice(0, 20)
                    };
                } catch (err) {
                    return { success: false, error: err && err.message ? err.message : String(err), url: location.href };
                }
            })();
        `;

        async function varrerCodigoFonteAnuncio(anuncio, botao) {
            if (!anuncio || !anuncio.url) return;
            const textoOriginal = botao ? botao.textContent : '';
            if (botao) {
                botao.disabled = true;
                botao.textContent = 'Varrendo...';
            }
            try {
                const dadosVisiveis = await extrairDadosAvantDoWebviewVisivel(anuncio).catch(() => null);
                if (dadosVisiveis) {
                    const atualizado = aplicarDadosAvantNoAnuncio(anuncio, dadosVisiveis);
                    const achouVisivel = atualizado.vendedor || atualizado.data || atualizado.vendas;
                    if (achouVisivel) {
                        const vendasTexto = anuncio && anuncio.vendas !== null && anuncio.vendas !== undefined ? anuncio.vendas : '';
                        mlPrimeiraPaginaStatusEl.textContent = `Dados da Avant Pro extraídos do card visível: data ${formatarDataCriacao(anuncio.data_criacao) || 'não encontrada'}, vendas ${vendasTexto || 0}.`;
                        if (botao) {
                            botao.textContent = 'Extraído';
                            setTimeout(() => {
                                botao.textContent = textoOriginal || 'Extrair Avant';
                                botao.disabled = false;
                            }, 1800);
                        }
                        return;
                    }
                }

                await garantirExtensoesNavegadorMl();
                const webview = criarMlWebviewOculto(98);
                await carregarUrlNoWebview(webview, anuncio.url);
                await new Promise(resolve => setTimeout(resolve, 1800));
                const scan = await webview.executeJavaScript(ML_SOURCE_SCAN_SCRIPT, true);
                const resultado = await webview.executeJavaScript(ML_DATE_EXTRACT_SCRIPT, true);

                const fonteVendedorResultado = resultado && (resultado.vendedorFonte || resultado.vendedor_fonte || 'pagina_produto');
                if (resultado && resultado.vendedor && deveAtualizarVendedor(anuncio.vendedor, anuncio.vendedorFonte, resultado.vendedor, fonteVendedorResultado)) {
                    anuncio.vendedor = resultado.vendedor;
                    anuncio.vendedorFonte = fonteVendedorResultado;
                    atualizarCelulaVendedor(anuncio, resultado.vendedor);
                }
                if (resultado && resultado.data_criacao) {
                    anuncio.data_criacao = resultado.data_criacao;
                    atualizarCelulaDataCriacao(anuncio, resultado.data_criacao);
                }
                const vendasResultado = parseNumeroVendas(resultado && resultado.vendas);
                const fonteVendasResultado = resultado && (resultado.vendasFonte || resultado.vendas_fonte || '');
                if (deveAtualizarVendas(anuncio.vendas, anuncio.vendasFonte, vendasResultado, fonteVendasResultado)) {
                    anuncio.vendas = vendasResultado;
                    anuncio.vendasFonte = fonteVendasResultado;
                    atualizarCelulaVendas(anuncio, vendasResultado);
                }

                const fields = (scan && scan.field_hits) || [];
                const dates = (scan && scan.iso_date_hits) || [];
                const hints = (scan && scan.source_hints) || [];
                const destino = scan && scan.url ? scan.url : anuncio.url;
                const bloqueio = scan && scan.verification ? ' Caiu em verificação/login.' : '';
                const achado = resultado && resultado.data_criacao
                    ? ` Data encontrada: ${formatarDataCriacao(resultado.data_criacao)}.`
                    : ` Campos de data: ${fields.length}. Datas ISO: ${dates.length}. Trechos suspeitos: ${hints.length}.`;
                mlPrimeiraPaginaStatusEl.textContent = `Varredura do código-fonte concluída.${achado}${bloqueio} URL final: ${destino}`;

                if (botao) {
                    botao.textContent = resultado && resultado.data_criacao ? 'Achou data' : 'Sem data';
                    setTimeout(() => {
                        botao.textContent = textoOriginal || 'Varrer fonte';
                        botao.disabled = false;
                    }, 1800);
                }
            } catch (err) {
                mlPrimeiraPaginaStatusEl.textContent = `Falha ao varrer código-fonte: ${err && err.message ? err.message : err}`;
                if (botao) {
                    botao.textContent = textoOriginal || 'Varrer fonte';
                    botao.disabled = false;
                }
            }
        }
