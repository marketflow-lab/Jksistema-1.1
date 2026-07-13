        function atualizarBotaoIncluirAnuncioRankingFavoritos(habilitado = false) {
            if (!favRankingIncluirAnuncioBtnEl) return;
            const skuSelecionado = String(favMlSkuSelecionado || '').trim();
            const podeIncluir = !!(habilitado && skuSelecionado && favMlHistoricoExecucaoSelecionadaId && !mlFavoritosEmExecucao);
            favRankingIncluirAnuncioBtnEl.disabled = !podeIncluir;
            favRankingIncluirAnuncioBtnEl.title = podeIncluir
                ? `Incluir anuncio manualmente no ranking do SKU ${skuSelecionado}`
                : 'Abra um ranking salvo ou atual do SKU para incluir anuncio.';
            if (!podeIncluir) fecharFormularioIncluirAnuncioRankingFavoritos();
        }

        function normalizarEntradaIncluirAnuncioRanking(valor) {
            const texto = String(valor || '').trim();
            if (!texto) return null;
            const idDigitado = texto.match(/\bMLB-?(\d{6,})\b/i);
            const id = extrairItemIdAnuncio(texto)
                || (idDigitado ? `MLB${idDigitado[1]}`.toUpperCase() : '');
            const url = normalizarUrlAnuncioSkuModal(texto) || construirUrlAnuncioFavoritosPorItemId(id);
            if (!id && !url) return null;
            return {
                id: id || extrairItemIdAnuncio(url),
                url
            };
        }

        function mostrarFormularioIncluirAnuncioRankingFavoritos() {
            const skuSelecionado = String(favMlSkuSelecionado || '').trim();
            const rankingAtual = skuSelecionado ? obterGrupoRankingFavoritosSku(skuSelecionado) : null;
            if (!skuSelecionado || !favMlHistoricoExecucaoSelecionadaId || !rankingAtual || !rankingAtual.grupo) {
                alert('Selecione um SKU e abra um ranking antes de incluir anuncio.');
                return;
            }
            if (!favRankingIncluirFormEl) {
                incluirAnuncioRankingFavoritos();
                return;
            }
            favRankingIncluirFormEl.classList.remove('hidden');
            favRankingIncluirFormEl.setAttribute('aria-hidden', 'false');
            if (favRankingIncluirInputEl) {
                favRankingIncluirInputEl.placeholder = `Cole o MLB ou link do anuncio do SKU ${skuSelecionado}`;
                setTimeout(() => favRankingIncluirInputEl.focus(), 0);
            }
        }

        function fecharFormularioIncluirAnuncioRankingFavoritos() {
            if (!favRankingIncluirFormEl) return;
            favRankingIncluirFormEl.classList.add('hidden');
            favRankingIncluirFormEl.setAttribute('aria-hidden', 'true');
        }

        function configurarFormularioIncluirAnuncioProcessando(processando) {
            if (favRankingIncluirInputEl) favRankingIncluirInputEl.disabled = !!processando;
            if (favRankingIncluirConfirmarBtnEl) favRankingIncluirConfirmarBtnEl.disabled = !!processando;
            if (favRankingIncluirCancelarBtnEl) favRankingIncluirCancelarBtnEl.disabled = !!processando;
        }

        const mlFavoritosRankingSyncEmExecucao = new Set();

        function mesclarInfoAnuncioIncluidoRanking(destino, info, fontePadrao = '') {
            if (!destino || !info) return;
            const fonte = normalizarFonte(info.source || info.origem_dados || fontePadrao || '');
            const idInfo = extrairItemIdAnuncio(info.id || info.mlb || info.url || info.permalink || info.link)
                || String(info.id || info.mlb || '').trim().toUpperCase().replace(/-/g, '');
            if (idInfo) destino.id = idInfo;
            const urlInfo = normalizarUrlAnuncioSkuModal(info.url || info.permalink || info.link || '') || construirUrlAnuncioFavoritosPorItemId(idInfo);
            if (urlInfo) destino.url = urlInfo;

            const titulo = String(info.titulo || info.title || '').replace(/\s+/g, ' ').trim();
            if (titulo && (!destino.titulo || destino.titulo.length < 12)) {
                destino.titulo = titulo;
            }

            preencherImagemAnuncioFavoritos(destino, info);
            preencherPrecoAnuncioFavoritos(destino, info);
            preencherTipoAnuncioFavoritos(destino, info);
            preencherCondicaoAnuncioFavoritos(destino, info);

            const vendedor = normalizarNomeVendedor(info.vendedor || info.seller || '');
            const fonteVendedor = normalizarFonte(info.vendedorFonte || info.vendedor_fonte || info.fonte_vendedor || fonte || 'pagina_produto');
            if (deveAtualizarVendedor(destino.vendedor, destino.vendedorFonte, vendedor, fonteVendedor)) {
                destino.vendedor = vendedor;
                destino.vendedorFonte = fonteVendedor;
                destino.vendedor_fonte = fonteVendedor;
            }

            if (info.data_criacao && (!destino.data_criacao || /avantpro/i.test(fonte))) {
                destino.data_criacao = info.data_criacao;
            }

            const fonteVendas = normalizarFonte(
                info.vendasFonte
                || info.vendas_fonte
                || info.fonte_vendas
                || (fonteVendasConfiavel(fonte) ? fonte : '')
            );
            const vendas = parseNumeroVendas(info.vendas);
            if (deveAtualizarVendas(destino.vendas, destino.vendasFonte, vendas, fonteVendas)) {
                destino.vendas = vendas;
                destino.vendasFonte = fonteVendas;
                destino.vendas_fonte = fonteVendas;
            }

            const media = parseNumeroDecimalFavoritos(
                info.media_mensal ??
                info.ritmo_atual ??
                info.ritmo_vendas_mes ??
                info.media_vendas_mensal ??
                ''
            );
            if (Number.isFinite(media)) {
                const fonteMedia = normalizarFonte(
                    info.media_mensal_fonte ||
                    info.ritmo_atual_fonte ||
                    info.ritmo_vendas_mes_fonte ||
                    fonteVendas ||
                    fonte ||
                    'avantpro_produto'
                );
                destino.media_mensal = media;
                destino.ritmo_atual = media;
                destino.ritmo_vendas_mes = media;
                destino.media_vendas_mensal = media;
                destino.media_mensal_fonte = fonteMedia;
                destino.ritmo_atual_fonte = fonteMedia;
                destino.ritmo_vendas_mes_fonte = fonteMedia;
            }
        }

        async function extrairSnapshotProdutoWebviewFavoritos() {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') return null;
            return await mlWebviewEl.executeJavaScript(`
                (function () {
                    try {
                        var text = function (selector) {
                            var node = document.querySelector(selector);
                            return node && node.textContent ? String(node.textContent).replace(/\\s+/g, ' ').trim() : '';
                        };
                        var attr = function (selector, name) {
                            var node = document.querySelector(selector);
                            return node && node.getAttribute ? String(node.getAttribute(name) || '').trim() : '';
                        };
                        var parseMoney = function (value) {
                            var raw = String(value || '').replace(/\\s+/g, ' ').trim();
                            if (!raw) return null;
                            var aria = raw.match(/(\\d[\\d\\.]*)\\s*reais?(?:\\s*(?:e|,)?\\s*(\\d{1,2})\\s*centavos?)?/i);
                            if (aria && aria[1]) {
                                var reais = Number(String(aria[1]).replace(/\\./g, ''));
                                var cents = aria[2] ? Number(aria[2]) : 0;
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
                        var readMoney = function (selector) {
                            var node = document.querySelector(selector);
                            if (!node) return null;
                            return parseMoney(node.getAttribute('aria-label') || node.textContent || '');
                        };
                        var image = attr('.ui-pdp-gallery__figure img, .ui-pdp-image, img.ui-pdp-image, img[data-zoom]', 'src')
                            || attr('.ui-pdp-gallery__figure img, .ui-pdp-image, img.ui-pdp-image, img[data-zoom]', 'data-zoom')
                            || attr('meta[property="og:image"]', 'content');
                        if (image && image.indexOf('//') === 0) image = 'https:' + image;
                        var price = readMoney('.ui-pdp-price__second-line .andes-money-amount, .ui-pdp-price .andes-money-amount, [data-testid="price-part"] .andes-money-amount');
                        var original = readMoney('s .andes-money-amount, .andes-money-amount--previous, .ui-pdp-price__original-value .andes-money-amount');
                        var title = text('h1.ui-pdp-title') || attr('meta[property="og:title"]', 'content') || document.title || '';
                        return {
                            success: true,
                            id: (String(location.href).match(/MLB-?(\\d{6,})/i) || [])[1] ? 'MLB' + (String(location.href).match(/MLB-?(\\d{6,})/i) || [])[1] : '',
                            url: location.href,
                            titulo: title.replace(/\\s*\\|\\s*Mercado\\s*Livre.*$/i, '').trim(),
                            imagem: image || '',
                            thumbnail: image || '',
                            preco: price,
                            price: price,
                            preco_original: original,
                            original_price: original,
                            preco_promocional: Number.isFinite(original) && Number.isFinite(price) && original > price ? price : '',
                            fonte_preco: Number.isFinite(price) ? 'pagina_produto' : ''
                        };
                    } catch (err) {
                        return { success: false, error: err && err.message ? err.message : String(err), url: location.href };
                    }
                })();
            `, true).catch(() => null);
        }

        async function extrairDadosAvantProdutoAtualWebviewFavoritos() {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') return null;
            return await mlWebviewEl.executeJavaScript(`
                (function () {
                    try {
                        var norm = function (value) {
                            return String(value || '')
                                .replace(/\\s+/g, ' ')
                                .trim();
                        };
                        var semAcento = function (value) {
                            return norm(value)
                                .normalize('NFD')
                                .replace(/[\\u0300-\\u036f]/g, '')
                                .toLowerCase();
                        };
                        var parseNumero = function (value) {
                            var texto = String(value === null || value === undefined ? '' : value).trim();
                            if (!texto) return null;
                            var match = texto.match(/([0-9][0-9\\.,]*)\\s*(mil|k)?/i);
                            if (!match) return null;
                            var numeroTexto = String(match[1]).replace(/\\s+/g, '');
                            if (numeroTexto.indexOf('.') >= 0 && numeroTexto.indexOf(',') >= 0) {
                                numeroTexto = numeroTexto.lastIndexOf('.') > numeroTexto.lastIndexOf(',')
                                    ? numeroTexto.replace(/,/g, '')
                                    : numeroTexto.replace(/\\./g, '').replace(',', '.');
                            } else if (numeroTexto.indexOf(',') >= 0) {
                                numeroTexto = /^\\d{1,3}(?:,\\d{3})+$/.test(numeroTexto)
                                    ? numeroTexto.replace(/,/g, '')
                                    : numeroTexto.replace(',', '.');
                            } else if (numeroTexto.indexOf('.') >= 0) {
                                numeroTexto = /^\\d{1,3}(?:\\.\\d{3})+$/.test(numeroTexto)
                                    ? numeroTexto.replace(/\\./g, '')
                                    : numeroTexto;
                            }
                            var numero = Number(numeroTexto);
                            if (!Number.isFinite(numero)) return null;
                            var sufixo = String(match[2] || '').toLowerCase();
                            return (sufixo === 'mil' || sufixo === 'k') ? numero * 1000 : numero;
                        };
                        var textos = [];
                        var nodes = Array.prototype.slice.call(document.querySelectorAll('div, section, article, aside, span, p'));
                        nodes.forEach(function (node) {
                            var texto = norm(node && node.innerText || node && node.textContent || '');
                            if (!texto || texto.length > 900) return;
                            var busca = semAcento(texto);
                            if (/avant|vendidos?|vendas?\\s+mensais|anuncio\\s+criado\\s+em|criado\\s+ha/.test(busca)) {
                                textos.push(texto);
                            }
                        });
                        textos.push(norm(document.body && document.body.innerText || ''));

                        var vendas = null;
                        var media = null;
                        var data = '';
                        var origemVendas = '';
                        var origemMedia = '';
                        var origemData = '';

                        for (var i = 0; i < textos.length; i += 1) {
                            var texto = textos[i];
                            var busca = semAcento(texto);
                            if (!Number.isFinite(vendas) && /vendidos?/.test(busca)) {
                                var vendaMatch = texto.match(/([0-9][0-9\\.,]*)\\s*(mil|k)?\\s+vendidos?\\b/i);
                                if (vendaMatch) {
                                    vendas = parseNumero(vendaMatch[0]);
                                    origemVendas = 'avantpro_produto';
                                }
                            }
                            if (!Number.isFinite(media) && /vendas?\\s+mensais/.test(busca)) {
                                var mediaMatch = texto.match(/vendas?\\s+mensais[^0-9]*([0-9][0-9\\.,]*)\\s*(?:\\/\\s*m[eê]s|por\\s+m[eê]s|m[eê]s)?/i)
                                    || texto.match(/([0-9][0-9\\.,]*)\\s*\\/\\s*m[eê]s/i);
                                if (mediaMatch) {
                                    media = parseNumero(mediaMatch[1] || mediaMatch[0]);
                                    origemMedia = 'avantpro_produto';
                                }
                            }
                            if (!data && /anuncio\\s+criado\\s+em|criado\\s+em/.test(busca)) {
                                var dataMatch = texto.match(/(?:an[uú]ncio\\s+criado\\s+em|criado\\s+em)\\s*([0-9]{1,2}\\/[0-9]{1,2}\\/[0-9]{2,4})/i)
                                    || texto.match(/\\b([0-9]{1,2}\\/[0-9]{1,2}\\/[0-9]{2,4})\\b/);
                                if (dataMatch && dataMatch[1]) {
                                    data = dataMatch[1];
                                    origemData = 'avantpro_produto';
                                }
                            }
                            if (Number.isFinite(vendas) && Number.isFinite(media) && data) break;
                        }

                        return {
                            success: !!(Number.isFinite(vendas) || Number.isFinite(media) || data),
                            vendas: Number.isFinite(vendas) ? Math.round(vendas) : null,
                            vendasFonte: origemVendas,
                            vendas_fonte: origemVendas,
                            media_mensal: Number.isFinite(media) ? media : '',
                            ritmo_atual: Number.isFinite(media) ? media : '',
                            ritmo_vendas_mes: Number.isFinite(media) ? media : '',
                            media_mensal_fonte: origemMedia,
                            ritmo_atual_fonte: origemMedia,
                            data_criacao: data,
                            dataCriacaoFonte: origemData,
                            data_criacao_fonte: origemData,
                            source: 'avantpro_produto',
                            url: location.href
                        };
                    } catch (err) {
                        return { success: false, error: err && err.message ? err.message : String(err), source: 'avantpro_produto' };
                    }
                })();
            `, true).catch(() => null);
        }

        async function coletarAnuncioIncluidoRankingFavoritos(entrada, sku) {
            const alvo = normalizarEntradaIncluirAnuncioRanking(entrada);
            if (!alvo || !alvo.id) {
                throw new Error('Informe um MLB valido ou o link completo do anuncio.');
            }
            const anuncio = {
                id: alvo.id,
                url: alvo.url || construirUrlAnuncioFavoritosPorItemId(alvo.id),
                sku_favorito: sku,
                titulo: '',
                vendedor: '',
                vendedorFonte: '',
                vendas: null,
                vendasFonte: '',
                data_criacao: '',
                origem_dados: 'inclusao_manual',
                pesquisas_origem: ['Incluir anuncio'],
                campos_origem: ['Incluir anuncio']
            };

            const apiInfo = await consultarItemApiMercadoLivre(alvo.id).catch(() => null);
            mesclarInfoAnuncioIncluidoRanking(anuncio, apiInfo, 'api_item');

            if (hasInternalBrowserApi || usarNavegadorMlNoShellElectron()) {
                const url = anuncio.url || construirUrlAnuncioFavoritosPorItemId(alvo.id);
                if (mlUrlInput) mlUrlInput.value = url;
                abrirBalaoResultadosMl({
                    titulo: 'Incluindo anuncio no ranking',
                    subtitulo: url,
                    browserCompleto: true,
                    forcarExibicao: true
                });
                mostrarBalaoFavoritosStatus(`Abrindo ${alvo.id} e aguardando Avant Pro...`);
                await navegarMlWebview(url);
                await aguardarAvantProNoWebview({
                    timeoutMs: 6500,
                    recarregarSeAusente: !mlFavoritosEmExecucao,
                    timeoutAposReloadMs: 8500,
                    mensagemRecarregando: 'Avant Pro nao apareceu no anuncio. Recarregando Mercado Livre...'
                }).catch(() => null);
                await extrairAnunciosWebviewVisivel({
                    clicarAvant: false,
                    cliqueAvantForcadoDesativado: true,
                    maxCliquesAvant: 8,
                    timeoutMs: 8000,
                    aguardarAposCliqueAvant: AVANT_PRO_ESPERA_POS_CLIQUE_MS + 600,
                    aguardarEstabilidadeAvant: true
                }).catch(() => null);
                const snapshot = await extrairSnapshotProdutoWebviewFavoritos();
                mesclarInfoAnuncioIncluidoRanking(anuncio, snapshot, 'pagina_produto');
                const dadosAvant = await mlWebviewEl.executeJavaScript(ML_DATE_EXTRACT_SCRIPT, true).catch(() => null);
                mesclarInfoAnuncioIncluidoRanking(anuncio, dadosAvant, dadosAvant && dadosAvant.source || 'avantpro_dom');
            } else if (window.electronAPI && typeof window.electronAPI.getMlBrowserItemInfo === 'function') {
                const browserInfo = await window.electronAPI.getMlBrowserItemInfo(alvo.id, anuncio.url).catch(() => null);
                mesclarInfoAnuncioIncluidoRanking(anuncio, browserInfo, browserInfo && browserInfo.source || 'browser_item');
            }

            if (!anuncio.titulo) anuncio.titulo = `Anuncio ${alvo.id}`;
            const normalizado = normalizarAnuncioFavoritosPesquisa(anuncio, sku, {
                campo: 'manual',
                termo: 'Incluir anuncio'
            });
            normalizado.origem_dados = 'inclusao_manual_avantpro';
            normalizado.pesquisas_origem = ['Incluir anuncio'];
            normalizado.campos_origem = ['Incluir anuncio'];
            return normalizado;
        }

        async function coletarAtualizacaoAnuncioRankingFavoritos(anuncioOriginal, sku) {
            const idOriginal = extrairItemIdAnuncio(anuncioOriginal && (anuncioOriginal.id || anuncioOriginal.mlb || anuncioOriginal.url || anuncioOriginal.permalink || anuncioOriginal.link))
                || String(anuncioOriginal && (anuncioOriginal.id || anuncioOriginal.mlb) || '').trim().toUpperCase().replace(/-/g, '');
            const urlOriginal = normalizarUrlAnuncioSkuModal(anuncioOriginal && (anuncioOriginal.url || anuncioOriginal.permalink || anuncioOriginal.link) || '')
                || construirUrlAnuncioFavoritosPorItemId(idOriginal);
            const alvo = normalizarEntradaIncluirAnuncioRanking(urlOriginal || idOriginal);
            if (!alvo || !alvo.id) {
                throw new Error('Nao foi possivel identificar o MLB deste anuncio.');
            }

            const anuncio = {
                ...(anuncioOriginal || {}),
                id: alvo.id,
                mlb: alvo.id,
                url: alvo.url || urlOriginal || construirUrlAnuncioFavoritosPorItemId(alvo.id),
                permalink: alvo.url || urlOriginal || construirUrlAnuncioFavoritosPorItemId(alvo.id),
                link: alvo.url || urlOriginal || construirUrlAnuncioFavoritosPorItemId(alvo.id),
                sku_favorito: sku,
                origem_dados: 'sincronizacao_manual_avantpro'
            };

            if (consultarItemApiMercadoLivre.cache && alvo.id) {
                consultarItemApiMercadoLivre.cache.delete(alvo.id);
            }
            const apiInfo = await consultarItemApiMercadoLivre(alvo.id).catch(() => null);
            mesclarInfoAnuncioIncluidoRanking(anuncio, apiInfo, 'api_item');

            if (hasInternalBrowserApi || usarNavegadorMlNoShellElectron()) {
                const url = anuncio.url || construirUrlAnuncioFavoritosPorItemId(alvo.id);
                if (mlUrlInput) mlUrlInput.value = url;
                abrirBalaoResultadosMl({
                    titulo: 'Sincronizando anuncio do ranking',
                    subtitulo: url,
                    browserCompleto: true,
                    forcarExibicao: true
                });
                mostrarBalaoFavoritosStatus(`Abrindo ${alvo.id} e relendo dados do Avant Pro...`);
                await navegarMlWebview(url);
                await aguardarAvantProNoWebview({
                    timeoutMs: 9000,
                    recarregarSeAusente: false,
                    timeoutAposReloadMs: 0
                }).catch(() => null);
                await extrairAnunciosWebviewVisivel({
                    clicarAvant: false,
                    cliqueAvantForcadoDesativado: true,
                    maxCliquesAvant: 0,
                    timeoutMs: 5000,
                    aguardarAposCliqueAvant: 800,
                    aguardarEstabilidadeAvant: true
                }).catch(() => null);
                const snapshot = await extrairSnapshotProdutoWebviewFavoritos();
                mesclarInfoAnuncioIncluidoRanking(anuncio, snapshot, 'pagina_produto');
                const dadosProdutoAvant = await extrairDadosAvantProdutoAtualWebviewFavoritos();
                mesclarInfoAnuncioIncluidoRanking(anuncio, dadosProdutoAvant, 'avantpro_produto');
                const dadosAvant = await mlWebviewEl.executeJavaScript(ML_DATE_EXTRACT_SCRIPT, true).catch(() => null);
                mesclarInfoAnuncioIncluidoRanking(anuncio, dadosAvant, dadosAvant && dadosAvant.source || 'avantpro_dom');
            } else if (window.electronAPI && typeof window.electronAPI.getMlBrowserItemInfo === 'function') {
                const browserInfo = await window.electronAPI.getMlBrowserItemInfo(alvo.id, anuncio.url).catch(() => null);
                mesclarInfoAnuncioIncluidoRanking(anuncio, browserInfo, browserInfo && browserInfo.source || 'browser_item');
            }

            const normalizado = normalizarAnuncioFavoritosPesquisa(anuncio, sku, {
                campo: 'sincronizacao',
                termo: 'Sincronizacao manual'
            });
            normalizado.origem_dados = 'sincronizacao_manual_avantpro';
            normalizado.sincronizado_em = new Date().toISOString();
            normalizado.pesquisas_origem = Array.isArray(anuncioOriginal && anuncioOriginal.pesquisas_origem)
                ? anuncioOriginal.pesquisas_origem.slice()
                : (normalizado.pesquisas_origem || ['Sincronizacao manual']);
            normalizado.campos_origem = Array.isArray(anuncioOriginal && anuncioOriginal.campos_origem)
                ? anuncioOriginal.campos_origem.slice()
                : (normalizado.campos_origem || ['Sincronizacao manual']);
            return normalizado;
        }

        function inserirAnuncioEmGrupoRankingFavoritos(grupo, anuncio, sku, opcoes = {}) {
            if (!grupo || !anuncio) return null;
            const existentes = Array.isArray(grupo.anuncios) ? grupo.anuncios : [];
            const chavesAlvo = new Set(chavesRemocaoAnuncioRankingFavoritos(anuncio));
            const jaExistia = chavesAlvo.size
                ? existentes.some(item => anuncioCorrespondeRemocaoRankingFavoritos(item, chavesAlvo))
                : false;
            const semDuplicado = chavesAlvo.size
                ? existentes.filter(item => !anuncioCorrespondeRemocaoRankingFavoritos(item, chavesAlvo))
                : existentes.slice();
            const combinados = deduplicarAnunciosFavoritos([...semDuplicado, anuncio])
                .filter(item => item && !tituloPareceFiltroOuCategoriaMl(item.titulo));
            grupo.anuncios = limitarAnunciosFavoritosRanking(ordenarAnunciosFavoritosRanking(combinados, sku));
            grupo.total_anuncios = grupo.anuncios.length;
            grupo.ordem_manual = false;
            if (opcoes && opcoes.sincronizacao) {
                grupo.sincronizacao_manual = true;
                grupo.sincronizado_em = new Date().toISOString();
            } else {
                grupo.inclusao_manual = true;
            }
            const posicao = grupo.anuncios.findIndex(item => anuncioCorrespondeRemocaoRankingFavoritos(item, chavesAlvo));
            return {
                jaExistia,
                posicao: posicao >= 0 ? posicao + 1 : null,
                total: grupo.anuncios.length
            };
        }

        function inserirAnuncioRankingFavoritos(sku, anuncio, opcoes = {}) {
            const skuSelecionado = String(sku || favMlSkuSelecionado || '').trim();
            const chaveSku = skuChaveSku(skuSelecionado);
            if (!chaveSku || !anuncio) return null;
            if (anuncioIgnoradoNoSku(skuSelecionado, anuncio)) {
                removerAnuncioIgnoradoSku(skuSelecionado, anuncio);
            }

            const entradaIdBruto = String(opcoes.entradaId || '').trim();
            const entradaId = entradaIdBruto === FAV_ML_RANKING_ATUAL_ID ? '' : entradaIdBruto;
            let resultado = null;
            let mudou = false;
            const grupoAtual = entradaId ? null : mlFavoritosResultadosPorSku.get(chaveSku);
            if (grupoAtual) {
                resultado = inserirAnuncioEmGrupoRankingFavoritos(grupoAtual, anuncio, skuSelecionado, opcoes) || resultado;
                mlFavoritosResultadosPorSku.set(chaveSku, grupoAtual);
                mudou = !!resultado || mudou;
            }

            const historico = lerHistoricoFavoritos();
            let historicoAlterado = false;
            const alvoHistorico = encontrarGrupoHistoricoRankingFavoritos(historico, chaveSku, entradaId);
            if (alvoHistorico) {
                resultado = inserirAnuncioEmGrupoRankingFavoritos(alvoHistorico.grupo, anuncio, skuSelecionado, opcoes) || resultado;
                recalcularTotaisHistoricoFavoritos(alvoHistorico.entrada);
                historicoAlterado = true;
                mudou = true;
                if (!grupoAtual) {
                    mlFavoritosResultadosPorSku.set(chaveSku, alvoHistorico.grupo);
                }
            }
            if (historicoAlterado) salvarHistoricoFavoritos(historico, { imediato: true });
            return mudou ? resultado : null;
        }

        async function sincronizarAnuncioRankingFavoritos(sku, anuncio, opcoes = {}) {
            const skuSelecionado = String(sku || favMlSkuSelecionado || '').trim();
            const entradaSelecionada = String(opcoes.entradaId || favMlHistoricoExecucaoSelecionadaId || '').trim();
            if (!skuSelecionado || !entradaSelecionada || !anuncio) {
                mostrarBalaoFavoritosStatus('Abra um ranking antes de sincronizar o anuncio.', {
                    erro: true,
                    tempoMs: 3500
                });
                return;
            }
            const id = extrairItemIdAnuncio(anuncio.id || anuncio.mlb || anuncio.url || anuncio.permalink || anuncio.link)
                || String(anuncio.id || anuncio.mlb || '').trim().toUpperCase().replace(/-/g, '');
            const chaveSync = `${entradaSelecionada}|${skuChaveSku(skuSelecionado)}|${id || anuncio.url || ''}`;
            if (mlFavoritosRankingSyncEmExecucao.has(chaveSync)) return;
            mlFavoritosRankingSyncEmExecucao.add(chaveSync);

            const botao = opcoes.botao || null;
            const htmlOriginal = botao ? botao.innerHTML : '';
            const titleOriginal = botao ? botao.title : '';
            if (botao) {
                botao.disabled = true;
                botao.innerHTML = '&#8635;';
                botao.title = 'Sincronizando...';
            }
            if (favMlStatusEl) {
                favMlStatusEl.classList.remove('hidden');
                favMlStatusEl.textContent = `Sincronizando ${id || 'anuncio'} com o Avant Pro...`;
            }

            try {
                const atualizado = await coletarAtualizacaoAnuncioRankingFavoritos(anuncio, skuSelecionado);
                const resultado = inserirAnuncioRankingFavoritos(skuSelecionado, atualizado, {
                    entradaId: entradaSelecionada,
                    sincronizacao: true
                });
                if (!resultado) throw new Error('Nao encontrei o grupo de ranking para salvar a sincronizacao.');

                renderizarFavoritosOutrosAnuncios(skuSelecionado);
                renderizarHistoricoFavoritos();
                if (Array.isArray(favMlAnunciosSkuAtual)) {
                    renderizarFavoritosAnunciosMl(favMlAnunciosSkuAtual, favMlSkuSelecionado);
                }
                const posicao = resultado.posicao ? ` na posicao #${resultado.posicao}` : '';
                const alvo = atualizado.id || id || 'Anuncio';
                if (favMlStatusEl) {
                    favMlStatusEl.textContent = `${alvo} sincronizado pelo Avant Pro e reclassificado${posicao}.`;
                }
                mostrarBalaoFavoritosStatus(`${alvo} sincronizado e reclassificado${posicao}.`, { tempoMs: 4500 });
                fecharBalaoResultadosMl({ forcar: true });
            } catch (err) {
                const mensagem = err && err.message ? err.message : String(err);
                if (favMlStatusEl) favMlStatusEl.textContent = `Erro ao sincronizar ${id || 'anuncio'}: ${mensagem}`;
                mostrarBalaoFavoritosStatus(`Erro ao sincronizar anuncio: ${mensagem}`, {
                    erro: true,
                    tempoMs: 6500
                });
            } finally {
                mlFavoritosRankingSyncEmExecucao.delete(chaveSync);
                if (botao) {
                    botao.innerHTML = htmlOriginal || '&#8635;';
                    botao.title = titleOriginal || 'Sincronizar dados deste anuncio pelo Avant Pro';
                    botao.disabled = false;
                }
            }
        }

        async function incluirAnuncioRankingFavoritos(entradaInformada = '') {
            const skuSelecionado = String(favMlSkuSelecionado || '').trim();
            const entradaSelecionada = String(favMlHistoricoExecucaoSelecionadaId || '').trim();
            const rankingAtual = skuSelecionado ? obterGrupoRankingFavoritosSku(skuSelecionado) : null;
            if (!skuSelecionado || !entradaSelecionada || !rankingAtual || !rankingAtual.grupo) {
                alert('Selecione um SKU e abra um ranking antes de incluir anuncio.');
                return;
            }
            const entrada = String(entradaInformada || (favRankingIncluirInputEl ? favRankingIncluirInputEl.value : '') || '').trim();
            if (!entrada) {
                if (favMlStatusEl) {
                    favMlStatusEl.classList.remove('hidden');
                    favMlStatusEl.textContent = 'Cole o MLB ou link do anuncio para incluir no ranking.';
                }
                mostrarBalaoFavoritosStatus('Cole o MLB ou link do anuncio para incluir.', {
                    erro: true,
                    tempoMs: 3500
                });
                if (favRankingIncluirInputEl) favRankingIncluirInputEl.focus();
                return;
            }
            const alvo = normalizarEntradaIncluirAnuncioRanking(entrada);
            if (!alvo || !alvo.id) {
                alert('Informe um MLB valido ou o link completo do anuncio.');
                if (favRankingIncluirInputEl) favRankingIncluirInputEl.focus();
                return;
            }

            const textoOriginal = favRankingIncluirAnuncioBtnEl ? favRankingIncluirAnuncioBtnEl.textContent : '';
            const textoConfirmarOriginal = favRankingIncluirConfirmarBtnEl ? favRankingIncluirConfirmarBtnEl.textContent : '';
            if (favRankingIncluirAnuncioBtnEl) {
                favRankingIncluirAnuncioBtnEl.disabled = true;
                favRankingIncluirAnuncioBtnEl.textContent = 'Incluindo...';
            }
            if (favRankingIncluirConfirmarBtnEl) {
                favRankingIncluirConfirmarBtnEl.textContent = 'Coletando...';
            }
            configurarFormularioIncluirAnuncioProcessando(true);
            if (favMlStatusEl) {
                favMlStatusEl.classList.remove('hidden');
                favMlStatusEl.textContent = `Abrindo ${alvo.id} e coletando dados com Avant Pro...`;
            }

            try {
                const anuncio = await coletarAnuncioIncluidoRankingFavoritos(alvo.url || alvo.id, skuSelecionado);
                const resultado = inserirAnuncioRankingFavoritos(skuSelecionado, anuncio, {
                    entradaId: entradaSelecionada
                });
                if (!resultado) throw new Error('Nao encontrei o grupo de ranking para salvar o anuncio.');

                renderizarFavoritosOutrosAnuncios(skuSelecionado);
                renderizarHistoricoFavoritos();
                if (Array.isArray(favMlAnunciosSkuAtual)) {
                    renderizarFavoritosAnunciosMl(favMlAnunciosSkuAtual, favMlSkuSelecionado);
                }
                const posicao = resultado.posicao ? ` na posicao #${resultado.posicao}` : '';
                const acao = resultado.jaExistia ? 'atualizado e reclassificado' : 'incluido e classificado';
                if (favMlStatusEl) {
                    favMlStatusEl.textContent = `Anuncio ${anuncio.id || alvo.id} ${acao}${posicao} do ranking do SKU ${skuSelecionado}.`;
                }
                mostrarBalaoFavoritosStatus(`Anuncio ${anuncio.id || alvo.id} ${acao}${posicao}.`, { tempoMs: 4500 });
                if (favRankingIncluirInputEl) favRankingIncluirInputEl.value = '';
                fecharFormularioIncluirAnuncioRankingFavoritos();
                fecharBalaoResultadosMl({ forcar: true });
            } catch (err) {
                const mensagem = err && err.message ? err.message : String(err);
                if (favMlStatusEl) favMlStatusEl.textContent = `Erro ao incluir anuncio ${alvo.id}: ${mensagem}`;
                mostrarBalaoFavoritosStatus(`Erro ao incluir anuncio: ${mensagem}`, {
                    erro: true,
                    tempoMs: 6500
                });
            } finally {
                if (favRankingIncluirAnuncioBtnEl) {
                    favRankingIncluirAnuncioBtnEl.textContent = textoOriginal || 'Incluir anuncio';
                }
                if (favRankingIncluirConfirmarBtnEl) {
                    favRankingIncluirConfirmarBtnEl.textContent = textoConfirmarOriginal || 'Adicionar';
                }
                configurarFormularioIncluirAnuncioProcessando(false);
                atualizarBotaoIncluirAnuncioRankingFavoritos(!!obterGrupoRankingFavoritosSku(skuSelecionado));
            }
        }

        function criarCelulaAcoesRankingFavoritos(sku, anuncio, opcoes = {}) {
            const td = document.createElement('td');
            td.className = 'ml-ranking-actions-cell';
            const rank = Number(opcoes.rank);
            if (Number.isFinite(rank) && rank > 0) {
                const rankEl = document.createElement('span');
                rankEl.className = 'ml-ranking-actions-rank';
                rankEl.textContent = `${rank}\u00B0`;
                td.appendChild(rankEl);
            }
            const wrap = document.createElement('div');
            wrap.className = 'ml-ranking-actions-group';

            const criarBotao = (iconeHtml, titulo, classe, handler, disabled = false) => {
                const botao = document.createElement('button');
                botao.type = 'button';
                botao.className = classe;
                botao.innerHTML = iconeHtml;
                botao.title = titulo;
                botao.setAttribute('aria-label', titulo);
                botao.disabled = !!disabled;
                botao.addEventListener('click', event => {
                    event.preventDefault();
                    event.stopPropagation();
                    if (!botao.disabled) handler(botao);
                });
                return botao;
            };

            const arrows = document.createElement('div');
            arrows.className = 'ml-ranking-arrows-row';
            arrows.appendChild(criarBotao('&uarr;', 'Mover este anuncio uma posicao para cima', 'ml-ranking-action-btn', () => {
                moverAnuncioRankingFavoritos(sku, anuncio, -1, opcoes);
            }, opcoes.primeiro));
            arrows.appendChild(criarBotao('&darr;', 'Mover este anuncio uma posicao para baixo', 'ml-ranking-action-btn', () => {
                moverAnuncioRankingFavoritos(sku, anuncio, 1, opcoes);
            }, opcoes.ultimo));
            wrap.appendChild(arrows);
            wrap.appendChild(criarBotao('&#8635;', 'Sincronizar dados deste anuncio pelo Avant Pro', 'ml-ranking-action-btn ml-ranking-sync-btn', (botao) => {
                sincronizarAnuncioRankingFavoritos(sku, anuncio, { ...opcoes, botao });
            }, mlFavoritosEmExecucao));
            wrap.appendChild(criarBotao('&minus;', 'Remover este anuncio do ranking', 'ml-ranking-remove-btn', () => {
                removerAnuncioRankingFavoritos(sku, anuncio, opcoes);
            }));
            td.appendChild(wrap);
            return td;
        }

        function criarCelulaRemoverRankingFavoritos(sku, anuncio) {
            const td = document.createElement('td');
            td.className = 'ml-ranking-actions-cell';
            const botao = document.createElement('button');
            botao.type = 'button';
            botao.className = 'ml-ranking-remove-btn';
            botao.innerHTML = '&minus;';
            botao.title = 'Remover este anuncio do ranking';
            botao.setAttribute('aria-label', 'Remover este anuncio do ranking');
            botao.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                removerAnuncioRankingFavoritos(sku, anuncio);
            });
            td.appendChild(botao);
            return td;
        }

        function formatarDataHistoricoFavoritos(dataIso) {
            const data = new Date(dataIso);
            if (Number.isNaN(data.getTime())) return '';
            return data.toLocaleString('pt-BR', {
                day: '2-digit',
                month: '2-digit',
                year: 'numeric',
                hour: '2-digit',
                minute: '2-digit'
            });
        }

        function criarTabelaHistoricoFavoritos(anuncios, sku = '', opcoes = {}) {
            const wrap = document.createElement('div');
            wrap.className = 'ml-favoritos-table-wrap';
            const table = document.createElement('table');
            table.innerHTML = `
                <thead>
                    <tr>
                        <th>Acoes</th>
                        <th>Foto</th>
                        <th>MLB</th>
                        <th>Media mensal</th>
                        <th>Preco</th>
                        <th>Titulo</th>
                        <th>Link</th>
                    </tr>
                </thead>
                <tbody></tbody>
            `;
            const tbody = table.querySelector('tbody');
            const lista = filtrarAnunciosIgnoradosRanking(anuncios, sku);
            if (!lista.length) {
                const tr = document.createElement('tr');
                const td = document.createElement('td');
                td.colSpan = 7;
                td.className = 'muted';
                td.textContent = 'Todos os anuncios deste historico estao na lista de ignorados.';
                tr.appendChild(td);
                tbody.appendChild(tr);
            }
            lista.forEach((anuncio, index) => {
                const tr = document.createElement('tr');
                tr.appendChild(criarCelulaAcoesRankingFavoritos(sku, anuncio, {
                    entradaId: opcoes.entradaId || '',
                    primeiro: index === 0,
                    rank: index + 1,
                    ultimo: index === lista.length - 1
                }));
                tr.appendChild(criarCelulaFotoAnuncioFavoritos(anuncio));
                const lojaVendedora = obterNomeLojaVendedoraHistoricoFavoritos(anuncio);
                tr.appendChild(criarCelulaMlbLojaFavoritos(
                    anuncio.id || extrairItemIdAnuncio(anuncio.url) || '',
                    lojaVendedora,
                    anuncio.status || '',
                    obterTipoCompletoAnuncioFavoritos(anuncio),
                    anuncio.vendedor || lojaVendedora || ''
                ));
                tr.appendChild(criarCelulaMediaHistoricoFavoritos(anuncio));
                if (typeof criarCelulaPrecoHistoricoFavoritos === 'function') {
                    tr.appendChild(criarCelulaPrecoHistoricoFavoritos(anuncio));
                } else if (typeof criarCelulaPrecoAnuncioFavoritos === 'function') {
                    tr.appendChild(criarCelulaPrecoAnuncioFavoritos(anuncio));
                } else {
                    tr.appendChild(document.createElement('td'));
                }
                [
                    anuncio.titulo || ''
                ].forEach(valor => {
                    const td = document.createElement('td');
                    td.textContent = valor;
                    tr.appendChild(td);
                });
                const tdLink = document.createElement('td');
                if (anuncio.url) {
                    const link = document.createElement('a');
                    link.href = anuncio.url;
                    link.target = '_blank';
                    link.rel = 'noopener';
                    link.className = 'link';
                    link.textContent = 'Abrir';
                    link.addEventListener('click', (event) => abrirAnuncioComAvantPro(anuncio.url, event));
                    tdLink.appendChild(link);
                }
                tr.appendChild(tdLink);
                tbody.appendChild(tr);
            });
            wrap.appendChild(table);
            return wrap;
        }

        function obterRemovidosIaGrupoFavoritos(grupo) {
            return (Array.isArray(grupo && grupo.removidos_ia) ? grupo.removidos_ia : [])
                .filter(Boolean);
        }

        function criarBlocoRemovidosIaFavoritos(grupo) {
            const removidos = obterRemovidosIaGrupoFavoritos(grupo);
            if (!removidos.length) return null;
            const box = document.createElement('div');
            box.className = 'ml-favoritos-ia-removed';

            const titulo = document.createElement('div');
            titulo.className = 'ml-favoritos-ia-removed-title';
            titulo.textContent = `Removidos pela IA (${removidos.length})`;
            box.appendChild(titulo);

            const lista = document.createElement('div');
            lista.className = 'ml-favoritos-ia-removed-list';
            removidos.forEach(anuncio => {
                const item = document.createElement('div');
                item.className = 'ml-favoritos-ia-removed-item';
                const imagem = obterImagemAnuncioFavoritos(anuncio);
                if (imagem) {
                    const img = document.createElement('img');
                    img.className = 'ml-favoritos-ia-removed-thumb';
                    img.src = imagem;
                    img.alt = anuncio && anuncio.id ? `Foto ${anuncio.id}` : 'Foto do anuncio removido';
                    item.appendChild(img);
                } else {
                    const placeholder = document.createElement('span');
                    placeholder.className = 'ml-favoritos-ia-removed-placeholder';
                    placeholder.textContent = 'Sem foto';
                    item.appendChild(placeholder);
                }
                const texto = document.createElement('div');
                const nome = document.createElement('div');
                nome.className = 'ml-favoritos-ia-removed-name';
                nome.textContent = anuncio.titulo || anuncio.title || 'Anuncio removido';
                const meta = document.createElement('div');
                meta.className = 'ml-favoritos-ia-removed-meta';
                meta.textContent = [
                    anuncio.id || extrairItemIdAnuncio(anuncio.url),
                    anuncio.vendedor ? `Vendedor: ${anuncio.vendedor}` : ''
                ].filter(Boolean).join(' | ');
                const motivo = document.createElement('div');
                motivo.className = 'ml-favoritos-ia-removed-reason';
                motivo.textContent = `Motivo: ${anuncio.motivo_ia || anuncio.motivo || 'produto diferente ou nao confirmado pela IA'}`;
                texto.appendChild(nome);
                if (meta.textContent) texto.appendChild(meta);
                texto.appendChild(motivo);
                item.appendChild(texto);
                lista.appendChild(item);
            });
            box.appendChild(lista);
            return box;
        }

        function renderizarHistoricoSkuSidebar() {
            if (!histMlSkuSidebarListEl || !histMlSkuSidebarEmptyEl || !histMlSkuSidebarCountEl) return;
            const todosItens = montarItensSkuSidebarMercadoLivre();
            const termoBusca = histMlSkuSidebarSearchEl ? histMlSkuSidebarSearchEl.value || '' : '';
            const itensFiltrados = filtrarItensSkuSidebarMercadoLivre(todosItens, termoBusca);
            const itens = aplicarSkuExatoHistoricoSidebar(itensFiltrados, termoBusca);
            const selecionadoChave = skuChaveSku(histMlSkuSelecionado);
            const selecionadoTemHistorico = selecionadoChave && obterHistoricoMaisRecenteSku(histMlSkuSelecionado);

            histMlSkuSidebarListEl.innerHTML = '';
            histMlSkuSidebarCountEl.textContent = todosItens.length
                ? (itens.length === todosItens.length ? `${todosItens.length} SKU(s)` : `${itens.length} de ${todosItens.length} SKU(s)`)
                : '';
            histMlSkuSidebarEmptyEl.textContent = termoBusca.trim()
                ? 'Nenhum SKU encontrado para essa pesquisa.'
                : favoritosWarningLojaAtual
                ? favoritosWarningLojaAtual
                : mlSkuLojaSelecionada
                ? 'Nenhum SKU encontrado nos anuncios ativos desta loja.'
                : 'Escolha uma loja integrada para carregar os SKUs dos anuncios ativos.';
            histMlSkuSidebarEmptyEl.classList.toggle('hidden', itens.length > 0);
            if (deveBuscarSkuRemotoParaTermo(itensFiltrados, termoBusca)) {
                agendarBuscaRemotaSkuSidebarMercadoLivre(termoBusca);
            }

            if (selecionadoChave && !selecionadoTemHistorico && !todosItens.some(item => skuChaveSku(item.sku) === selecionadoChave)) {
                histMlSkuSelecionado = '';
            }

            itens.forEach(item => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'ml-sku-sidebar-item' + (skuChaveSku(item.sku) === skuChaveSku(histMlSkuSelecionado) ? ' is-active' : '');

                const main = document.createElement('span');
                main.className = 'ml-sku-sidebar-main';

                const codigo = document.createElement('span');
                codigo.className = 'ml-sku-sidebar-code';
                codigo.textContent = item.sku;

                const titulo = document.createElement('span');
                titulo.className = 'ml-sku-sidebar-name';
                titulo.textContent = item.titulo || 'SKU encontrado em anuncio ativo';

                const meta = document.createElement('span');
                meta.className = 'ml-sku-sidebar-meta';
                const partes = [];
                if (item.loja) partes.push(`Loja: ${item.loja}`);
                if (item.totalAnuncios) partes.push(`${item.totalAnuncios} anuncio(s)`);
                if (item.itemIds && item.itemIds.length) partes.push(item.itemIds.slice(0, 2).join(', '));
                meta.textContent = partes.join(' | ');

                main.appendChild(codigo);
                main.appendChild(titulo);
                if (meta.textContent) main.appendChild(meta);
                button.appendChild(main);
                button.addEventListener('click', () => {
                    if (skuChaveSku(item.sku) === skuChaveSku(histMlSkuSelecionado)) {
                        limparHistoricoSkuSelecionado();
                        return;
                    }
                    selecionarHistoricoSku(item.sku);
                });
                histMlSkuSidebarListEl.appendChild(button);
            });
        }

        function limparHistoricoSkuSelecionado() {
            histMlSkuSelecionado = '';
            histMlHistoricoExecucaoSelecionadaId = '';
            renderizarHistoricoSkuSidebar();
            renderizarSkuSidebarMercadoLivre();
            renderizarHistoricoFavoritos();
        }

        function selecionarHistoricoSku(sku) {
            histMlSkuSelecionado = String(sku || '').trim();
            histMlHistoricoExecucaoSelecionadaId = '';
            renderizarHistoricoSkuSidebar();
            renderizarSkuSidebarMercadoLivre();
            renderizarHistoricoFavoritos();
        }

        function atualizarBotaoVoltarHistoricoFavoritos(mostrar) {
            const toolbar = document.querySelector('.ml-favoritos-historico-toolbar');
            if (!toolbar) return;
            let voltarBtn = document.getElementById('ml-historico-favoritos-voltar-geral');
            if (!voltarBtn) {
                voltarBtn = document.createElement('button');
                voltarBtn.id = 'ml-historico-favoritos-voltar-geral';
                voltarBtn.type = 'button';
                voltarBtn.className = 'btn-back ml-favoritos-historico-voltar-btn';
                voltarBtn.textContent = 'Voltar ao historico geral';
                voltarBtn.title = 'Voltar para a lista geral do historico.';
                voltarBtn.addEventListener('click', limparHistoricoSkuSelecionado);
                if (mlHistoricoFavoritosLimparEl && mlHistoricoFavoritosLimparEl.parentNode === toolbar) {
                    toolbar.insertBefore(voltarBtn, mlHistoricoFavoritosLimparEl);
                } else {
                    toolbar.appendChild(voltarBtn);
                }
            }
            voltarBtn.hidden = !mostrar;
        }

        function prepararAbaHistoricoFavoritos() {
            renderizarHistoricoSkuSidebar();
            renderizarSkuSidebarMercadoLivre();
            renderizarHistoricoFavoritos();
            if (!mlSkusAnunciosLojaAtual.length && !mlSkuCarregamentoEmAndamento) {
                carregarSkuFavoritos(mlSkuLojaSelecionada || skuLojaSelecionada || '');
            }
        }

        function renderizarHistoricoFavoritos() {
            if (!mlHistoricoFavoritosListEl || !mlHistoricoFavoritosEmptyEl) return;
            const historicoCompleto = lerHistoricoFavoritos();
            const lojaAtualTexto = favoritosEhTodasLojas(mlSkuLojaSelecionada || skuLojaSelecionada) ? '' : (mlSkuLojaSelecionada || skuLojaSelecionada || '');
            const skuSelecionado = String(histMlSkuSelecionado || '').trim();
            const skuSelecionadoChave = skuChaveSku(skuSelecionado);
            const historico = filtrarHistoricoFavoritosPorLojaAtual(historicoCompleto, skuSelecionado);
            mlHistoricoFavoritosListEl.innerHTML = '';
            atualizarBotaoVoltarHistoricoFavoritos(!!skuSelecionadoChave);

            if (!skuSelecionadoChave) {
                const recentes = montarUltimosFavoritosRankeados(ML_FAVORITOS_HISTORICO_MAX, historico);
                if (mlHistoricoFavoritosStatusEl) {
                    mlHistoricoFavoritosStatusEl.textContent = recentes.length
                        ? `Ultimos favoritos feitos${lojaAtualTexto ? ` para a loja ${lojaAtualTexto}` : ''}. Clique em um item para abrir os anuncios rankeados.`
                        : `Nenhum favorito salvo no historico do servidor${lojaAtualTexto ? ` para a loja ${lojaAtualTexto}` : ''}.`;
                }
                mlHistoricoFavoritosEmptyEl.textContent = recentes.length
                    ? ''
                    : 'Nenhum favorito salvo ainda.';
                mlHistoricoFavoritosEmptyEl.classList.toggle('hidden', recentes.length > 0);
                if (recentes.length) {
                    const lista = document.createElement('div');
                    lista.className = 'ml-favoritos-recentes-list';
                    recentes.forEach(item => {
                        lista.appendChild(criarBotaoFavoritoRecente(item, abrirFavoritoRecenteNoHistorico));
                    });
                    mlHistoricoFavoritosListEl.appendChild(lista);
                }
                return;
            }

            const historicoComSku = historico
                .filter(entrada => {
                    const execucaoSelecionada = String(histMlHistoricoExecucaoSelecionadaId || '').trim();
                    return !execucaoSelecionada || idEntradaHistoricoFavoritos(entrada) === execucaoSelecionada;
                })
                .map(entrada => ({
                    ...entrada,
                    grupos: (entrada.grupos || []).filter(grupo => skuChaveSku(grupo && grupo.sku) === skuSelecionadoChave)
                }))
                .filter(entrada => entrada.grupos.length);

            const historicoFiltrado = historicoComSku
                .map(entrada => ({
                    ...entrada,
                    grupos: (entrada.grupos || [])
                        .map(grupo => ({
                            ...grupo,
                            anuncios: filtrarAnunciosIgnoradosRanking(grupo && grupo.anuncios, grupo && grupo.sku)
                        }))
                        .filter(grupo => grupo.anuncios.length || obterRemovidosIaGrupoFavoritos(grupo).length)
                }))
                .filter(entrada => entrada.grupos.length);

            mlHistoricoFavoritosEmptyEl.textContent = historicoComSku.length && !historicoFiltrado.length
                ? `Todos os anuncios salvos para o SKU ${skuSelecionado} estao na lista de ignorados.`
                : `Nenhum historico salvo para o SKU ${skuSelecionado}.`;
            mlHistoricoFavoritosEmptyEl.classList.toggle('hidden', historicoFiltrado.length > 0);
            if (mlHistoricoFavoritosStatusEl) {
                mlHistoricoFavoritosStatusEl.textContent = historicoFiltrado.length
                    ? `${historicoFiltrado.length} execucao(oes) encontrada(s) para o SKU ${skuSelecionado}. Cada execucao mostra os ${ML_FAVORITOS_HISTORICO_ANUNCIOS_MAX} melhores anuncios classificados.`
                    : '';
            }
            historicoFiltrado.forEach(entrada => {
                const card = document.createElement('section');
                card.className = 'ml-favoritos-sku-card';

                const head = document.createElement('div');
                head.className = 'ml-favoritos-sku-head';
                const titulo = document.createElement('h4');
                titulo.className = 'ml-favoritos-sku-title';
                titulo.textContent = `Favoritos feitos em ${formatarDataHistoricoFavoritos(entrada.data_iso) || 'data desconhecida'}`;
                const meta = document.createElement('div');
                meta.className = 'ml-favoritos-historico-meta';
                const totalAnunciosSku = (entrada.grupos || []).reduce((acc, grupo) => acc + (Array.isArray(grupo.anuncios) ? grupo.anuncios.length : 0), 0);
                const duracaoExecucao = formatarDuracaoExecucaoFavoritos(entrada.duracao_execucao_ms);
                meta.textContent = `${totalAnunciosSku} anuncio(s) rankeado(s) para o SKU ${skuSelecionado}${duracaoExecucao ? ` | Tempo total: ${duracaoExecucao}` : ''}${entrada.loja ? ` | Loja: ${entrada.loja}` : ''}${sufixoUsuarioHistoricoFavoritos(entrada)}`;
                head.appendChild(titulo);
                head.appendChild(meta);
                card.appendChild(head);

                (entrada.grupos || []).forEach(grupo => {
                    const bloco = document.createElement('div');
                    bloco.className = 'ml-favoritos-historico-grupo';
                    const grupoHead = document.createElement('div');
                    grupoHead.className = 'ml-favoritos-historico-grupo-head';
                    const subtitulo = document.createElement('h5');
                    subtitulo.className = 'ml-favoritos-historico-grupo-title';
                    subtitulo.textContent = `${grupo.sku}${grupo.titulo ? ` - ${grupo.titulo}` : ''}`;
                    const refazerBtn = document.createElement('button');
                    refazerBtn.type = 'button';
                    refazerBtn.className = 'btn-back ml-favoritos-refazer-btn';
                    refazerBtn.textContent = 'Refazer';
                    refazerBtn.disabled = !normalizarTermosPesquisaFavoritos(grupo.termos).length;
                    refazerBtn.title = refazerBtn.disabled
                        ? 'Este histórico não tem termos salvos.'
                        : 'Refazer uma nova consulta com os mesmos termos.';
                    refazerBtn.addEventListener('click', () => refazerRankingHistoricoFavoritos(grupo, entrada));
                    const aprovarBtn = document.createElement('button');
                    aprovarBtn.type = 'button';
                    aprovarBtn.className = 'btn-back ml-favoritos-refazer-btn';
                    aprovarBtn.textContent = 'Aprovar e alterar';
                    aprovarBtn.disabled = grupoRankingFavoritosEhAvulso(grupo) || !Array.isArray(grupo.anuncios) || !grupo.anuncios.length;
                    aprovarBtn.title = aprovarBtn.disabled
                        ? 'Este historico nao tem ranking de SKU para aprovar.'
                        : 'Abrir este ranking no modulo Favoritos para aprovar e alterar.';
                    aprovarBtn.addEventListener('click', () => abrirRankingHistoricoParaAprovarFavoritos(grupo, entrada));
                    const excluirBtn = document.createElement('button');
                    excluirBtn.type = 'button';
                    excluirBtn.className = 'btn-back ml-favoritos-refazer-btn ml-favoritos-excluir-historico-btn';
                    excluirBtn.textContent = 'Excluir favorito';
                    excluirBtn.title = 'Excluir este favorito salvo do historico.';
                    excluirBtn.addEventListener('click', () => removerFavoritoHistoricoIndividual({
                        entrada,
                        entrada_id: idEntradaHistoricoFavoritos(entrada),
                        grupo,
                        sku: grupo.sku
                    }));
                    const termos = document.createElement('div');
                    termos.className = 'ml-favoritos-termos';
                    const termosTexto = formatarTermosPesquisaFavoritos(grupo.termos);
                    termos.textContent = termosTexto
                        ? `Pesquisas usadas: ${termosTexto}`
                        : 'Sem pesquisas registradas.';
                    grupoHead.appendChild(subtitulo);
                    grupoHead.appendChild(refazerBtn);
                    grupoHead.appendChild(aprovarBtn);
                    grupoHead.appendChild(excluirBtn);
                    bloco.appendChild(grupoHead);
                    bloco.appendChild(termos);
                    if (typeof criarBlocoResumoColetaFavoritos === 'function') {
                        const resumoColeta = criarBlocoResumoColetaFavoritos(grupo);
                        if (resumoColeta) bloco.appendChild(resumoColeta);
                    }
                    bloco.appendChild(criarTabelaHistoricoFavoritos(grupo.anuncios, grupo.sku, {
                        entradaId: idEntradaHistoricoFavoritos(entrada)
                    }));
                    const removidosIa = criarBlocoRemovidosIaFavoritos(grupo);
                    if (removidosIa) bloco.appendChild(removidosIa);
                    card.appendChild(bloco);
                });
                mlHistoricoFavoritosListEl.appendChild(card);
            });
        }

        function limparHistoricoFavoritos() {
            if (!confirm('Deseja apagar o historico de favoritos salvo no servidor para este usuario?')) return;
            salvarHistoricoFavoritos([]);
            renderizarHistoricoFavoritos();
            renderizarLinksAlinhadosFavoritos();
        }

        function removerFavoritoHistoricoIndividual(alvo = {}) {
            const entradaId = String(alvo.entrada_id || alvo.entradaId || idEntradaHistoricoFavoritos(alvo.entrada || alvo) || '').trim();
            const skuAlvo = String(alvo.sku || alvo.grupo && alvo.grupo.sku || '').trim();
            const chaveSkuAlvo = skuChaveSku(skuAlvo);
            if (!entradaId && !chaveSkuAlvo) return;
            const rotulo = skuAlvo ? ` do SKU ${skuAlvo}` : '';
            if (!confirm(`Deseja excluir este favorito${rotulo} do historico?`)) return;

            const historico = lerHistoricoFavoritos();
            let removido = false;
            const historicoAtualizado = historico
                .map(entrada => {
                    const entradaAtualId = idEntradaHistoricoFavoritos(entrada);
                    if (entradaId && entradaAtualId !== entradaId) return entrada;
                    if (!chaveSkuAlvo) {
                        removido = true;
                        return null;
                    }
                    const grupos = Array.isArray(entrada && entrada.grupos) ? entrada.grupos : [];
                    const gruposRestantes = grupos.filter(grupo => skuChaveSku(grupo && grupo.sku) !== chaveSkuAlvo);
                    if (gruposRestantes.length === grupos.length) return entrada;
                    removido = true;
                    if (!gruposRestantes.length) return null;
                    const entradaAtualizada = {
                        ...entrada,
                        grupos: gruposRestantes
                    };
                    recalcularTotaisHistoricoFavoritos(entradaAtualizada);
                    return entradaAtualizada;
                })
                .filter(Boolean);

            if (!removido) {
                if (mlHistoricoFavoritosStatusEl && document.getElementById('aba-historico')?.classList.contains('active')) {
                    mlHistoricoFavoritosStatusEl.textContent = 'Nao localizei esse favorito no historico.';
                }
                return;
            }

            if (entradaId && histMlHistoricoExecucaoSelecionadaId === entradaId) {
                histMlHistoricoExecucaoSelecionadaId = '';
            }
            salvarHistoricoFavoritos(historicoAtualizado, { imediato: true });
            renderizarHistoricoSkuSidebar();
            renderizarHistoricoFavoritos();
            renderizarLinksAlinhadosFavoritos();
            if (mlHistoricoFavoritosStatusEl && document.getElementById('aba-historico')?.classList.contains('active')) {
                mlHistoricoFavoritosStatusEl.textContent = `Favorito${rotulo} excluido do historico.`;
            }
        }

        function guardarResultadoRankingFavorito(grupo) {
            const chave = skuChaveSku(grupo && grupo.sku);
            if (!chave) return;
            const opcoesPromocao = resolverOpcoesPromocaoGrupoFavoritos(grupo, grupo && grupo.sku);
            if (opcoesPromocao) {
                grupo.opcoes_promocao = opcoesPromocao;
                salvarOpcoesPromocaoFavoritosSku(grupo.sku || chave, opcoesPromocao);
            }
            if (!grupo.data_iso && !grupo.data_ranking_iso) {
                grupo.data_ranking_iso = new Date().toISOString();
            }
            if (!grupo.loja) {
                grupo.loja = favoritosLojaSelecionadaParaApi();
            }
            mlFavoritosResultadosPorSku.set(chave, grupo);
            if (skuChaveSku(favMlSkuSelecionado) === chave) {
                renderizarFavoritosOutrosAnuncios(favMlSkuSelecionado);
                if (Array.isArray(favMlAnunciosSkuAtual)) {
                    renderizarFavoritosAnunciosMl(favMlAnunciosSkuAtual, favMlSkuSelecionado);
                }
            }
        }
