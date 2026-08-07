        function skuColetarSkusParaIa(opcoes = {}) {
            const skusAlvo = Array.isArray(opcoes.skus)
                ? new Set(opcoes.skus.map(skuChaveSku).filter(Boolean))
                : new Set();
            const lojasAlvo = Array.isArray(opcoes.lojas)
                ? new Set(opcoes.lojas.map(skuNormalizarLoja).filter(Boolean))
                : new Set();
            let linhas = opcoes.todos
                ? (Array.isArray(skuDados) ? [...skuDados] : [])
                : skuFiltrarDados();
            if (opcoes.todos) {
                const lojaSelecionadaNorm = skuNormalizarLoja(skuLojaSelecionada);
                linhas = linhas.filter(row => {
                    if (lojaSelecionadaNorm && !favoritosEhTodasLojas(skuLojaSelecionada) && skuNormalizarLoja(skuObterLoja(row)) !== lojaSelecionadaNorm) return false;
                    return skuMostrarOcultos || !skuEstaOculto(row);
                });
            }
            if (lojasAlvo.size) {
                linhas = linhas.filter(row => lojasAlvo.has(skuNormalizarLoja(skuObterLoja(row))));
            }
            if (skusAlvo.size) {
                const filtradas = linhas.filter(row => skusAlvo.has(skuChaveSku(skuObterSku(row))));
                const fallback = Array.isArray(skuDados) ? skuDados.filter(row => {
                    if (!skusAlvo.has(skuChaveSku(skuObterSku(row)))) return false;
                    return !lojasAlvo.size || lojasAlvo.has(skuNormalizarLoja(skuObterLoja(row)));
                }) : [];
                linhas = filtradas.length
                    ? filtradas
                    : fallback;
            }
            const mapa = new Map();
            linhas.forEach(row => {
                const sku = skuObterSku(row);
                const chave = skuChaveSku(sku);
                const loja = skuObterLoja(row);
                const chaveLinha = chave;
                if (!chave || mapa.has(chaveLinha)) return;
                mapa.set(chaveLinha, {
                    sku,
                    loja,
                    titulo: skuObterProduto(row),
                    produto: skuObterProduto(row),
                    descricao: String(row.descricao_ml || row.descricao || row['descrição'] || row.description || '').trim(),
                    pesquisa_1: skuObterPesquisa(row, 1),
                    pesquisa_2: skuObterPesquisa(row, 2),
                    pesquisa_3: skuObterPesquisa(row, 3),
                });
            });
            return Array.from(mapa.values());
        }

        async function skuGerarPesquisasComIa(opcoes = {}) {
            const chamadaSilenciosa = !!(opcoes && opcoes.silencioso);
            const botaoAcao = opcoes && opcoes.botao ? opcoes.botao : null;
            const itens = skuColetarSkusParaIa(opcoes);
            if (!itens.length) {
                const mensagem = 'Nenhum SKU disponivel para preencher com IA.';
                if (chamadaSilenciosa) return { success: false, total: 0, atualizadas: 0, error: mensagem };
                alert('Nenhum SKU disponível para preencher com IA.');
                return;
            }

            const textoOriginal = botaoAcao ? botaoAcao.textContent : '';
            const tamanhoLote = Math.max(1, Math.min(8, Number(opcoes.tamanhoLote || 3) || 3));
            const totalLotes = Math.max(1, Math.ceil(itens.length / tamanhoLote));
            const atualizadasChaves = new Set();
            const resultadosAcumulados = [];
            const errosAcumulados = [];
            const startedAt = Date.now();
            if (botaoAcao) {
                botaoAcao.disabled = true;
                botaoAcao.textContent = 'IA trabalhando...';
            }
            skuDefinirStatus('');
            skuAtualizarBarraIa({
                ativo: true,
                total: itens.length,
                processados: 0,
                startedAt,
                message: `Preparando ${itens.length} SKU(s) para IA.`
            });

            try {
                for (let inicio = 0; inicio < itens.length; inicio += tamanhoLote) {
                    const lote = itens.slice(inicio, inicio + tamanhoLote);
                    const fim = Math.min(itens.length, inicio + lote.length);
                    const loteAtual = Math.floor(inicio / tamanhoLote) + 1;
                    const mensagemLote = `IA trabalhando no lote ${loteAtual}/${totalLotes}: ${inicio + 1}-${fim} de ${itens.length} SKU(s).`;
                    skuAtualizarBarraIa({
                        ativo: true,
                        total: itens.length,
                        processados: inicio,
                        loteInicio: inicio + 1,
                        loteFim: fim,
                        startedAt,
                        erros: errosAcumulados.length,
                        message: `${mensagemLote} Aguardando resposta da IA...`
                    });
                    await skuPausarUi(30);

                    const gruposPorLoja = new Map();
                    lote.forEach(item => {
                        const lojaItem = String(item && item.loja || favoritosLojaSelecionadaParaApi() || 'Todas as lojas').trim();
                        if (!gruposPorLoja.has(lojaItem)) gruposPorLoja.set(lojaItem, []);
                        gruposPorLoja.get(lojaItem).push(item);
                    });

                    let resultadosLote = [];
                    for (const [lojaLote, itensLoja] of gruposPorLoja.entries()) {
                        const response = await fetch('/api/favoritos/skus/pesquisas/ia', {
                            method: 'POST',
                            headers: headersJsonAutenticado(),
                            body: JSON.stringify({
                                itens: itensLoja,
                                loja: lojaLote,
                                chunk_tamanho: tamanhoLote,
                                sobrescrever: !!(opcoes && opcoes.sobrescrever)
                            })
                        });
                        if (!response.ok) {
                            let detalhe = `HTTP ${response.status}`;
                            try {
                                const dataErro = await response.json();
                                detalhe = dataErro.detail || detalhe;
                            } catch (_err) {}
                            throw new Error(detalhe);
                        }

                        const data = await response.json();
                        const resultados = Array.isArray(data.resultados) ? data.resultados : [];
                        const erros = Array.isArray(data.erros) ? data.erros : [];
                        errosAcumulados.push(...erros.filter(item => item && item.erro));
                        resultadosAcumulados.push(...resultados);
                        resultadosLote = resultadosLote.concat(resultados);
                        const mapaResultados = new Map(resultados.map(item => [
                            skuChaveSku(item && item.sku),
                            item
                        ]));

                        const aplicadosLote = new Set();
                        skuDados.forEach(row => {
                            const chave = skuChaveSku(skuObterSku(row));
                            const item = mapaResultados.get(chave);
                            if (!item || aplicadosLote.has(chave)) return;
                            aplicarPesquisasGlobaisSkuLocal(item.sku || skuObterSku(row), item, { preservarVazios: true });
                            atualizadasChaves.add(chave);
                            aplicadosLote.add(chave);
                        });
                    }
                    skuRenderizarTabela();
                    skuAtualizarBarraIa({
                        ativo: true,
                        total: itens.length,
                        processados: fim,
                        loteInicio: inicio + 1,
                        loteFim: fim,
                        startedAt,
                        erros: errosAcumulados.length,
                        message: `Lote ${loteAtual}/${totalLotes} concluido: ${resultadosLote.length} SKU(s) retornado(s).`
                    });
                    await skuPausarUi(60);
                }

                const atualizadas = atualizadasChaves.size;
                const mensagemFinal = atualizadas
                    ? `${atualizadas} SKU(s) atualizados pela IA para todas as contas com o mesmo SKU.`
                    : (errosAcumulados.length ? `IA terminou, mas nenhum SKU foi atualizado. Falhas: ${errosAcumulados.length}.` : 'Nenhum SKU foi atualizado pela IA.');
                skuDefinirStatus('');
                skuAtualizarBarraIa({
                    ativo: false,
                    total: itens.length,
                    processados: itens.length,
                    startedAt,
                    erros: errosAcumulados.length,
                    phase: atualizadas || !errosAcumulados.length ? 'complete' : 'error',
                    message: mensagemFinal
                });
                return { success: true, total: itens.length, atualizadas, resultados: resultadosAcumulados, erros: errosAcumulados };
            } catch (err) {
                const mensagemErro = err && err.message ? err.message : String(err);
                skuDefinirStatus('');
                skuAtualizarBarraIa({
                    ativo: false,
                    total: itens.length,
                    processados: atualizadasChaves.size,
                    startedAt,
                    erros: Math.max(1, errosAcumulados.length),
                    phase: 'error',
                    message: `Erro ao preencher pesquisas com IA: ${mensagemErro}`
                });
                return { success: false, total: itens.length, atualizadas: 0, error: mensagemErro };
            } finally {
                if (botaoAcao) {
                    botaoAcao.disabled = false;
                    botaoAcao.textContent = textoOriginal || 'IA';
                }
            }
        }

        window.JKFavoritosPreencherPesquisasIA = function(opcoes = {}) {
            return skuGerarPesquisasComIa({ ...opcoes, silencioso: true });
        };

        async function tentarDataCriacaoPeloElectron(anuncios) {
            const pendentes = (anuncios || []).filter(item => item && item.url);
            if (!pendentes.length) return { datas: 0, vendedores: 0, vendas: 0 };

            const contagem = { datas: 0, vendedores: 0, vendas: 0 };
            const precisaRever = (anuncio) => !anuncio || !anuncio.url
                ? true
                : (!anuncio.vendedor || !anuncio.data_criacao || !(fonteVendasConfiavel(anuncio.vendasFonte || anuncio.vendas_fonte) && hasNumeroVendas(anuncio.vendas)));
            const avantStatusTemDados = (status) => {
                if (typeof statusAvantProTemDadosColetaveis === 'function') {
                    return statusAvantProTemDadosColetaveis(status);
                }
                return !!(status && (
                    status.hasAvantData
                    || status.rows > 0
                    || status.dataTextNodes > 0
                    || status.bodyDataLabels > 1
                    || (status.bodyHasAvantInfo && status.bodyDataLabels > 0)
                ));
            };
            const avantStatusPrecisaAcao = (status) => !!(status && (
                status.needsAccountLink
                || status.accountActionRequired
                || status.avantLoginDialog
                || status.avantLoginEmailInputs > 0
            ));
            const avantStatusSemDados = (status) => {
                if (!status || avantStatusTemDados(status) || avantStatusPrecisaAcao(status)) return false;
                if (typeof window.FavoritosV2.searchRanking.publicApi.status.statusAvantProSemDadosColetaveis === 'function') {
                    return window.FavoritosV2.searchRanking.publicApi.status.statusAvantProSemDadosColetaveis(status);
                }
                return !!(status.shellOnly || status.extensionDetected || status.widgets > 0 || status.actionButtons > 0 || status.toolsButtons > 0);
            };
            const erroDadosRicosFaltando = (anuncio, detalhe) => {
                const itemId = anuncio && (anuncio.id || extrairItemIdAnuncio(anuncio.url));
                const alvo = itemId || (anuncio && anuncio.titulo) || (anuncio && anuncio.url) || 'anuncio';
                const mensagem = `Mercado Livre/Avant Pro nao retornou vendedor, data e vendas para "${alvo}". ${detalhe || 'Conecte o Avant Pro no navegador interno e tente novamente.'}`;
                if (typeof window.FavoritosV2.searchRanking.publicApi.status.erroColetaMercadoLivreFavoritos === 'function') {
                    return window.FavoritosV2.searchRanking.publicApi.status.erroColetaMercadoLivreFavoritos(mensagem);
                }
                const erro = new Error(mensagem);
                erro.coletaMercadoLivreFalhou = true;
                return erro;
            };
            const aplicarResultadoRico = (anuncio, resultado, fontePadrao = 'pagina_produto') => {
                const atualizado = { vendedor: false, data: false, vendas: false };
                if (!anuncio || !resultado) return atualizado;
                const fonteVendedorResultado = resultado.vendedorFonte || resultado.vendedor_fonte || fontePadrao;
                if (window.FavoritosV2.promotionEffectuation.publicApi.merge.deveAtualizarVendedor(anuncio.vendedor, anuncio.vendedorFonte, resultado.vendedor, fonteVendedorResultado)) {
                    anuncio.vendedor = resultado.vendedor;
                    anuncio.vendedorFonte = fonteVendedorResultado;
                    atualizarCelulaVendedor(anuncio, resultado.vendedor);
                    atualizado.vendedor = true;
                }
                if (resultado.data_criacao && !anuncio.data_criacao) {
                    anuncio.data_criacao = resultado.data_criacao;
                    atualizarCelulaDataCriacao(anuncio, resultado.data_criacao);
                    atualizado.data = true;
                }
                const vendasResultado = parseNumeroVendas(resultado.vendas);
                const fonteVendasResultado = resultado.vendasFonte || resultado.vendas_fonte || '';
                if (window.FavoritosV2.promotionEffectuation.publicApi.merge.deveAtualizarVendas(anuncio.vendas, anuncio.vendasFonte, vendasResultado, fonteVendasResultado)) {
                    anuncio.vendas = vendasResultado;
                    anuncio.vendasFonte = fonteVendasResultado;
                    atualizarCelulaVendas(anuncio, vendasResultado);
                    atualizado.vendas = true;
                }
                return atualizado;
            };
            const somarAtualizacao = (atualizado) => {
                if (!atualizado) return;
                if (atualizado.vendedor) contagem.vendedores += 1;
                if (atualizado.data) contagem.datas += 1;
                if (atualizado.vendas) contagem.vendas += 1;
            };
            const extrairNoNavegadorPrincipal = async (anuncio) => {
                if (!anuncio || !anuncio.url || !mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') return null;
                if (typeof mudarAba === 'function') {
                    try { mudarAba('navegador'); } catch (_err) {}
                }
                if (typeof forcarNavegadorMlShellVisivel === 'function') {
                    setTimeout(() => forcarNavegadorMlShellVisivel(), 80);
                    setTimeout(() => forcarNavegadorMlShellVisivel(), 500);
                }
                const itemId = anuncio.id || extrairItemIdAnuncio(anuncio.url);
                window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Abrindo anuncio ${itemId || ''} para ler data, vendedor e vendas no Avant Pro...`, {
                    manterNavegadorVisivel: true
                });
                await carregarUrlNoWebview(mlWebviewEl, anuncio.url);
                if (typeof forcarNavegadorMlShellVisivel === 'function') forcarNavegadorMlShellVisivel();
                if (typeof montarScriptAcionarControlesAvantPro === 'function') {
                    await mlWebviewEl.executeJavaScript(montarScriptAcionarControlesAvantPro({
                        forceClick: true,
                        permitirFerramentas: true,
                        maxClicks: 14
                    }), true).catch(() => 0);
                    await esperar(1200);
                }
                if (typeof aguardarDadosAvantProEstaveisWebview === 'function') {
                    await aguardarDadosAvantProEstaveisWebview({
                        minWaitMs: 250,
                        stableMs: 650,
                        maxWaitMs: 3500
                    }).catch(() => null);
                }
                let statusAvant = typeof diagnosticarAvantProNoWebview === 'function'
                    ? await diagnosticarAvantProNoWebview(mlWebviewEl).catch(() => null)
                    : null;
                if (avantStatusPrecisaAcao(statusAvant) || avantStatusSemDados(statusAvant)) {
                    if (avantStatusSemDados(statusAvant) && typeof aguardarAvantProNoWebview === 'function') {
                        window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Aguardando Avant Pro liberar dados do anuncio ${itemId || ''}...`, {
                            manterNavegadorVisivel: true,
                            larga: true,
                            titulo: 'Aguardando Avant Pro'
                        });
                        const statusAguardado = await aguardarAvantProNoWebview({
                            timeoutMs: 18000,
                            pollMs: 420,
                            recarregarSeAusente: !mlFavoritosEmExecucao,
                            timeoutAposReloadMs: 14000,
                            mensagemRecarregando: `Avant Pro ainda nao liberou dados do anuncio ${itemId || ''}. Recarregando Mercado Livre...`
                        }).catch(() => null);
                        if (statusAguardado) statusAvant = statusAguardado;
                    }
                }
                if (avantStatusPrecisaAcao(statusAvant)) {
                    await window.FavoritosV2.searchRanking.publicApi.auth.aguardarConexaoAvantProFavoritos(statusAvant, {
                        termo: anuncio.titulo || itemId || anuncio.url
                    });
                    if (typeof montarScriptAcionarControlesAvantPro === 'function') {
                        await mlWebviewEl.executeJavaScript(montarScriptAcionarControlesAvantPro({
                            forceClick: true,
                            permitirFerramentas: true,
                            maxClicks: 14
                        }), true).catch(() => 0);
                        await esperar(1200);
                    }
                    if (typeof aguardarDadosAvantProEstaveisWebview === 'function') {
                        await aguardarDadosAvantProEstaveisWebview({
                            minWaitMs: 300,
                            stableMs: 700,
                            maxWaitMs: 4500
                        }).catch(() => null);
                    }
                    statusAvant = typeof diagnosticarAvantProNoWebview === 'function'
                        ? await diagnosticarAvantProNoWebview(mlWebviewEl).catch(() => statusAvant)
                        : statusAvant;
                }
                if (avantStatusSemDados(statusAvant)) {
                    throw erroDadosRicosFaltando(anuncio, 'O Avant Pro abriu, mas nao mostrou dados coletaveis.');
                }
                const resultado = await mlWebviewEl.executeJavaScript(ML_DATE_EXTRACT_SCRIPT, true);
                if (!resultado || (!resultado.vendedor && !resultado.data_criacao && !hasNumeroVendas(resultado.vendas))) {
                    throw erroDadosRicosFaltando(anuncio, 'O navegador abriu o anuncio, mas o Avant Pro nao entregou os campos ricos.');
                }
                return resultado;
            };

            const executarPassada = async (itens) => {
                await executarComConcorrencia(itens, ML_API_WORKERS, async (anuncio) => {
                    try {
                        const itemId = anuncio.id || extrairItemIdAnuncio(anuncio.url);
                        if (!itemId) return;
                        anuncio.id = itemId;
                        const apiInfo = await consultarItemApiMercadoLivre(itemId);
                        if (apiInfo) {
                            const fonteApi = apiInfo.source || 'api';
                            if (apiInfo.titulo && apiInfo.titulo !== anuncio.titulo) {
                                anuncio.titulo = apiInfo.titulo;
                                atualizarCelulaTitulo(anuncio, apiInfo.titulo);
                            }
                            if (apiInfo.vendedor && window.FavoritosV2.promotionEffectuation.publicApi.merge.deveAtualizarVendedor(anuncio.vendedor, anuncio.vendedorFonte, apiInfo.vendedor, fonteApi)) {
                                anuncio.vendedor = apiInfo.vendedor;
                                anuncio.vendedorFonte = fonteApi;
                                atualizarCelulaVendedor(anuncio, apiInfo.vendedor);
                                contagem.vendedores += 1;
                            }
                            if (apiInfo.data_criacao && !anuncio.data_criacao) {
                                anuncio.data_criacao = apiInfo.data_criacao;
                                atualizarCelulaDataCriacao(anuncio, apiInfo.data_criacao);
                                contagem.datas += 1;
                            }
                            const vendasApi = parseNumeroVendas(apiInfo.vendas);
                            if (window.FavoritosV2.promotionEffectuation.publicApi.merge.deveAtualizarVendas(anuncio.vendas, anuncio.vendasFonte, vendasApi, fonteApi)) {
                                anuncio.vendas = vendasApi;
                                anuncio.vendasFonte = fonteApi;
                                atualizarCelulaVendas(anuncio, vendasApi);
                                contagem.vendas += 1;
                            }
                        }
                    } catch (err) {
                        console.warn('Não foi possível consultar API ML:', anuncio.url, err);
                    }
                });

                const restantesApi = itens.filter(item => precisaRever(item));
                if (!restantesApi.length) {
                    return;
                }

                if (mlWebviewEl && typeof mlWebviewEl.executeJavaScript === 'function') {
                    for (const anuncio of restantesApi) {
                        if (!precisaRever(anuncio)) continue;
                        try {
                            window.FavoritosV2.searchRanking.publicApi.control.verificarCancelamentoFavoritos();
                            const resultadoVisivel = await extrairNoNavegadorPrincipal(anuncio);
                            somarAtualizacao(aplicarResultadoRico(anuncio, resultadoVisivel));
                            if (precisaRever(anuncio)) {
                                throw erroDadosRicosFaltando(anuncio, 'Ainda faltam campos depois da leitura no navegador visivel.');
                            }
                        } catch (err) {
                            if (mlFavoritosCancelado || (err && err.canceladoFavoritos)) throw err;
                            console.warn('Nao foi possivel ler dados no navegador visivel:', anuncio.url, err);
                        }
                    }
                }

                const restantesVisivel = itens.filter(item => precisaRever(item));
                if (!restantesVisivel.length) {
                    return;
                }

                await garantirExtensoesNavegadorMl();
                await executarComConcorrencia(restantesVisivel, ML_BROWSER_WORKERS, async (anuncio, workerIndex) => {
                    const webview = criarMlWebviewOculto(workerIndex);
                    if (typeof webview.executeJavaScript !== 'function') return;
                    try {
                        await carregarUrlNoWebview(webview, anuncio.url);
                        if (typeof montarScriptAcionarControlesAvantPro === 'function') {
                            await webview.executeJavaScript(montarScriptAcionarControlesAvantPro({
                                forceClick: true,
                                permitirFerramentas: true,
                                maxClicks: 10
                            }), true).catch(() => 0);
                            await esperar(900);
                        }
                        const resultado = await webview.executeJavaScript(ML_DATE_EXTRACT_SCRIPT, true);
                        somarAtualizacao(aplicarResultadoRico(anuncio, resultado));
                        if (precisaRever(anuncio) && window.electronAPI && typeof window.electronAPI.getMlBrowserItemInfo === 'function') {
                            const itemId = anuncio.id || extrairItemIdAnuncio(anuncio.url);
                            if (itemId) {
                                const browserInfo = await window.electronAPI.getMlBrowserItemInfo(itemId, anuncio.url);
                                if (browserInfo && browserInfo.vendedor && window.FavoritosV2.promotionEffectuation.publicApi.merge.deveAtualizarVendedor(anuncio.vendedor, anuncio.vendedorFonte, browserInfo.vendedor, 'browser_item')) {
                                    anuncio.vendedor = browserInfo.vendedor;
                                    anuncio.vendedorFonte = 'browser_item';
                                    atualizarCelulaVendedor(anuncio, browserInfo.vendedor);
                                    contagem.vendedores += 1;
                                }
                                if (browserInfo && browserInfo.data_criacao && !anuncio.data_criacao) {
                                    anuncio.data_criacao = browserInfo.data_criacao;
                                    atualizarCelulaDataCriacao(anuncio, browserInfo.data_criacao);
                                    contagem.datas += 1;
                                }
                                const vendasBrowser = parseNumeroVendas(browserInfo && browserInfo.vendas);
                                const fonteVendasBrowser = browserInfo && (browserInfo.vendasFonte || browserInfo.vendas_fonte || browserInfo.source || 'browser_item');
                                if (window.FavoritosV2.promotionEffectuation.publicApi.merge.deveAtualizarVendas(anuncio.vendas, anuncio.vendasFonte, vendasBrowser, fonteVendasBrowser)) {
                                    anuncio.vendasFonte = fonteVendasBrowser;
                                    anuncio.vendas = vendasBrowser;
                                    atualizarCelulaVendas(anuncio, vendasBrowser);
                                    contagem.vendas += 1;
                                }
                            }
                        }
                    } catch (err) {
                        console.warn('Não foi possível ler data pelo Electron:', anuncio.url, err);
                    }
                });
            };

            await executarPassada(pendentes);

            const segundaChamada = pendentes.filter(item => precisaRever(item));
            if (!segundaChamada.length) {
                return contagem;
            }

            mlPrimeiraPaginaStatusEl && (mlPrimeiraPaginaStatusEl.textContent = 'Executando segunda verificação de vendedor, data e vendas...');
            await esperar(1500);
            await executarPassada(segundaChamada);

            return contagem;
        }
