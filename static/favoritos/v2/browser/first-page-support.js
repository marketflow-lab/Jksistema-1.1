(function (global) {
  'use strict';

  const browser = global.FavoritosV2.browser;
  const pageScripts = browser.pageScripts;

        function chaveAnuncioPrimeiraPaginaFavoritos(anuncio) {
            return chaveCanonicaAnuncioFavoritos(anuncio);
        }

        function anuncioPrimeiraPaginaTemDadosAvant(anuncio) {
            if (!anuncio) return false;
            const fonte = normalizarFonte(anuncio.vendasFonte || anuncio.vendas_fonte || '');
            if (fonteVendasConfiavel(fonte) && hasNumeroVendas(parseNumeroVendas(anuncio.vendas))) return true;
            return !!(anuncio.vendedor || anuncio.data_criacao || anuncio.cacheAvant || anuncio.vendas_estimadas || anuncio.media_mensal);
        }

        function resumoPrimeiraPaginaFavoritos(totalVisiveis, anuncios, extra = {}) {
            const lista = Array.isArray(anuncios) ? anuncios.filter(Boolean) : [];
            const avantNaoVinculado = Array.isArray(anuncios && anuncios.__avantNaoVinculado)
                ? anuncios.__avantNaoVinculado.length
                : Number(extra.avant_nao_vinculado) || 0;
            const comTitulo = lista.filter(item => tituloValidoFavoritosCanonico(item && (item.titulo || item.title), item && (item.id || item.mlb))).length;
            const comFoto = lista.filter(imagemValidaFavoritosCanonico).length;
            const comPreco = lista.filter(precoValidoFavoritosCanonico).length;
            const comLink = lista.filter(item => !!limparLinkProdutoMercadoLivreFavoritos(item && (item.url || item.permalink || item.link), item && (item.id || item.mlb))).length;
            const comAvant = lista.filter(anuncioPrimeiraPaginaTemDadosAvant).length;
            lista.forEach(item => {
                if (item) item.estado_qualidade = classificarQualidadeAnuncioFavoritosCanonico(item);
            });
            const suspeitos = lista.filter(item => item && item.estado_qualidade === 'suspeito').length;
            return {
                visiveis: Number(totalVisiveis) || lista.length,
                coletados: lista.length,
                com_titulo: comTitulo,
                com_foto: comFoto,
                com_preco: comPreco,
                com_link: comLink,
                com_dados_avant: comAvant,
                incompletos: lista.filter(item => item && item.estado_qualidade === 'incompleto').length,
                suspeitos,
                avant_nao_vinculado: avantNaoVinculado,
                ...extra
            };
        }

        function assinaturaEstabilidadePrimeiraPaginaFavoritos(anuncios) {
            const lista = Array.isArray(anuncios) ? anuncios.filter(Boolean) : [];
            return lista.map(item => {
                const chave = typeof chaveCanonicaAnuncioFavoritos === 'function'
                    ? chaveCanonicaAnuncioFavoritos(item)
                    : String(item && (item.id || item.mlb || item.url || item.link) || '').trim().toLowerCase();
                return JSON.stringify([
                    chave,
                    item && (item.titulo || item.title) || '',
                    obterImagemAnuncioFavoritos(item),
                    item && (item.preco ?? item.price ?? ''),
                    item && (item.preco_original ?? item.original_price ?? ''),
                    item && (item.preco_promocional ?? item.promotional_price ?? ''),
                    item && item.vendedor || '',
                    item ? (item.vendas ?? '') : '',
                    item && (item.vendasFonte || item.vendas_fonte) || '',
                    item && item.data_criacao || '',
                    item && (item.tipo_anuncio || item.listing_type_id) || '',
                    item && (item.is_full ?? item.full ?? ''),
                    item && (item.condicao || item.condition || item.item_condition) || '',
                    item && (item.media_mensal ?? item.ritmo_atual ?? ''),
                    item ? (item.visitas ?? '') : ''
                ]);
            }).sort().join('|');
        }

        function deveEncerrarPlateauPrimeiraPaginaFavoritos(estado = {}) {
            return Number(estado.passada) >= 2
                && estado.passadaCompleta === true
                && !!String(estado.assinaturaAtual || '')
                && String(estado.assinaturaAtual) === String(estado.assinaturaAnterior || '')
                && Number(estado.pendentesAvant) === 0
                && (estado.mutationQuietMs === undefined || Number(estado.mutationQuietMs) >= 800);
        }

        function montarPosicoesVarreduraPrimeiraPaginaFavoritos(alturaPagina, alturaViewport, limiteAnuncios) {
            const viewport = Math.max(560, Number(alturaViewport) || 800);
            const altura = Math.max(viewport, Number(alturaPagina) || viewport);
            const finalPagina = Math.max(0, altura - viewport - 20);
            const passo = Math.max(540, Math.floor(viewport * 0.9));
            const posicoes = [];
            for (let y = 0; y <= finalPagina; y += passo) {
                posicoes.push(Math.max(0, Math.floor(y)));
            }
            posicoes.push(finalPagina);
            const completas = Array.from(new Set(posicoes)).sort((a, b) => a - b);
            const maxPosicoes = Math.max(6, Math.ceil((Number(limiteAnuncios) || 80) / 5) + 3);
            if (completas.length <= maxPosicoes) return completas;
            const amostradas = [];
            for (let indice = 0; indice < maxPosicoes; indice += 1) {
                const origem = Math.round((indice * (completas.length - 1)) / (maxPosicoes - 1));
                amostradas.push(completas[origem]);
            }
            return Array.from(new Set(amostradas)).sort((a, b) => a - b);
        }

        function alturaVarreduraPrimeiraPaginaFavoritos(alturaDocumento, fimResultados, alturaViewport) {
            const viewport = Math.max(560, Number(alturaViewport) || 800);
            const documento = Math.max(viewport, Number(alturaDocumento) || viewport);
            const resultados = Math.max(0, Number(fimResultados) || 0);
            if (resultados <= viewport) return documento;
            return Math.min(documento, Math.max(viewport, resultados + Math.floor(viewport * 0.35)));
        }

        function emitirProgressoPrimeiraPaginaFavoritos(onProgress, payload) {
            if (typeof onProgress !== 'function') return;
            try {
                onProgress(payload || {});
            } catch (_err) {}
        }

        async function executarFilaLimitadaFavoritos(items, limite, worker) {
            const lista = Array.isArray(items) ? items : [];
            const max = Math.max(1, Math.min(Number(limite) || 4, 4));
            let index = 0;
            const runners = Array.from({ length: Math.min(max, lista.length) }, async () => {
                while (index < lista.length) {
                    const atual = lista[index];
                    index += 1;
                    await worker(atual);
                }
            });
            await Promise.all(runners);
        }

        async function completarBaseMercadoLivreComApiFavoritos(anuncios, opcoes = {}) {
            const lista = Array.isArray(anuncios) ? anuncios : [];
            if (!lista.length || typeof consultarItemApiMercadoLivre !== 'function') return lista;
            const deadline = Number(opcoes.deadlineMs) || 0;
            const maxItens = Math.max(0, Number(opcoes.maxItens) || 0);
            const timeoutMs = Math.max(1200, Math.min(Number(opcoes.timeoutMs) || 3500, 8000));
            const pendentesTodos = lista.filter(item => {
                const id = normalizarMlbFavoritosCanonico(item && (item.id || item.mlb || item.url || item.link || item.permalink));
                if (!id) return false;
                return !tituloValidoFavoritosCanonico(item && (item.titulo || item.title), id)
                    || !imagemValidaFavoritosCanonico(item)
                    || !precoValidoFavoritosCanonico(item)
                    || !limparLinkProdutoMercadoLivreFavoritos(item && (item.url || item.permalink || item.link), id);
            });
            const pendentes = maxItens > 0 ? pendentesTodos.slice(0, maxItens) : pendentesTodos;
            if (!pendentes.length) return lista;
            await executarFilaLimitadaFavoritos(pendentes, opcoes.concorrencia || 4, async (alvo) => {
                if (deadline && Date.now() > deadline - 1500) return;
                const itemId = normalizarMlbFavoritosCanonico(alvo && (alvo.id || alvo.mlb || alvo.url || alvo.link || alvo.permalink));
                if (!itemId) return;
                try {
                    const apiInfo = await Promise.race([
                        consultarItemApiMercadoLivre(itemId),
                        esperar(timeoutMs).then(() => null)
                    ]);
                    if (!apiInfo) return;
                    const completo = prepararAnuncioMercadoLivreCanonico(apiInfo, 'mercado_livre_api');
                    const mescladoLista = mesclarAnunciosAvant([alvo], [completo]);
                    const chaveAlvo = chaveCanonicaAnuncioFavoritos(alvo);
                    const chaveApi = chaveCanonicaAnuncioFavoritos(completo);
                    const mesclado = mescladoLista.find(item => {
                        const chaveItem = chaveCanonicaAnuncioFavoritos(item);
                        return chaveItem && (chaveItem === chaveAlvo || chaveItem === chaveApi);
                    });
                    if (!mesclado) return;
                    Object.keys(alvo).forEach(key => delete alvo[key]);
                    Object.assign(alvo, mesclado);
                    alvo.estado_qualidade = classificarQualidadeAnuncioFavoritosCanonico(alvo);
                } catch (err) {
                    console.warn('Nao foi possivel completar base ML pela API:', itemId, err);
                }
            });
            return lista;
        }

        async function extrairCacheAvantProCardsWebview(opcoes = {}) {
            const webview = resolverWebviewFavoritosColeta(opcoes);
            if (!webview || typeof webview.executeJavaScript !== 'function') return { success: false, total: 0, anuncios: [] };
            const limite = Math.max(1, Math.min(Number(opcoes.limite) || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80, 120));
            return await webview.executeJavaScript(pageScripts.render('extrair-cache-avant-pro-cards-webview-1', { p0: (JSON.stringify(limite)) }), true).catch((err) => ({
                success: false,
                total: 0,
                anuncios: [],
                error: err && err.message ? err.message : String(err)
            }));
        }

        async function capturarAvantProCardsVisiveisRapidoWebview(opcoes = {}) {
            const webview = resolverWebviewFavoritosColeta(opcoes);
            if (!webview || typeof webview.executeJavaScript !== 'function') return { success: false, total: 0, anuncios: [] };
            const limite = Math.max(1, Math.min(Number(opcoes.limite) || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80, 120));
            return await webview.executeJavaScript(pageScripts.render('capturar-avant-pro-cards-visiveis-rapido-webview-1', { p0: (JSON.stringify(limite)) }), true).catch((err) => ({
                success: false,
                total: 0,
                anuncios: [],
                error: err && err.message ? err.message : String(err)
            }));
        }

        async function obterMetricaRolagemMercadoLivreFavoritos(webview = mlWebviewEl) {
            if (!webview || typeof webview.executeJavaScript !== 'function') return { y: 0, height: 0, view: 800, atBottom: true };
            return await webview.executeJavaScript(pageScripts.render('obter-metrica-rolagem-mercado-livre-favoritos-1', {  }), true).catch(() => ({ y: 0, height: 0, view: 800, atBottom: true }));
        }

        async function rolarMercadoLivreFavoritos(y, webview = mlWebviewEl) {
            if (!webview || typeof webview.executeJavaScript !== 'function') return null;
            const destino = Math.max(0, Math.floor(Number(y) || 0));
            return await webview.executeJavaScript(pageScripts.render('rolar-mercado-livre-favoritos-1', { p0: (destino), p1: (destino), p2: (destino) }), true).catch(() => null);
        }

        async function materializarCardsPrimeiraPaginaMercadoLivreFavoritos(opcoes = {}) {
            const webview = resolverWebviewFavoritosColeta(opcoes);
            const limite = Math.max(20, Math.min(Number(opcoes.limite) || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80, 100));
            const deadline = Number(opcoes.deadlineMs) || (Date.now() + 25000);
            const onProgress = opcoes.onProgress;
            let anuncios = [];
            let ultimaAssinatura = '';
            let estaveis = 0;
            const metricaInicial = await obterMetricaRolagemMercadoLivreFavoritos(webview);
            const originalY = Math.max(0, Number(metricaInicial && metricaInicial.y) || 0);
            let viewport = Math.max(560, Number(metricaInicial && metricaInicial.view) || 800);
            let y = 0;
            let passos = 0;

            while (Date.now() < deadline) {
                passos += 1;
                await rolarMercadoLivreFavoritos(y, webview);
                await esperar(420);
                const basico = await extrairCardsMercadoLivreBasicoWebview({ limite, webview }).catch(() => null);
                anuncios = mesclarAnunciosAvant(anuncios, (basico && basico.anuncios) || []);
                const metricaAtual = await obterMetricaRolagemMercadoLivreFavoritos(webview);
                viewport = Math.max(560, Number(metricaAtual && metricaAtual.view) || viewport);
                const resumo = resumoPrimeiraPaginaFavoritos(anuncios.length, anuncios, {
                    etapa: 'materializando',
                    y: metricaAtual && metricaAtual.y,
                    height: metricaAtual && metricaAtual.height
                });
                emitirProgressoPrimeiraPaginaFavoritos(onProgress, resumo);

                const assinatura = anuncios.map(chaveAnuncioPrimeiraPaginaFavoritos).filter(Boolean).join('|');
                estaveis = assinatura && assinatura === ultimaAssinatura ? estaveis + 1 : 0;
                ultimaAssinatura = assinatura;
                if (anuncios.length >= limite) break;
                if (metricaAtual && metricaAtual.atBottom && estaveis >= 1 && passos >= 4) break;
                const proximoY = Math.max(y + Math.floor(viewport * 0.9), Number(metricaAtual && metricaAtual.y || 0) + Math.floor(viewport * 0.75));
                if (metricaAtual && metricaAtual.height && proximoY > metricaAtual.height + viewport) break;
                y = proximoY;
            }

            await rolarMercadoLivreFavoritos(originalY, webview);
            return {
                anuncios: anuncios.slice(0, limite),
                totalVisiveis: Math.min(limite, anuncios.length),
                originalY
            };
        }

  const api = { chaveAnuncioPrimeiraPaginaFavoritos, anuncioPrimeiraPaginaTemDadosAvant, resumoPrimeiraPaginaFavoritos, assinaturaEstabilidadePrimeiraPaginaFavoritos, deveEncerrarPlateauPrimeiraPaginaFavoritos, montarPosicoesVarreduraPrimeiraPaginaFavoritos, alturaVarreduraPrimeiraPaginaFavoritos, emitirProgressoPrimeiraPaginaFavoritos, executarFilaLimitadaFavoritos, completarBaseMercadoLivreComApiFavoritos, extrairCacheAvantProCardsWebview, capturarAvantProCardsVisiveisRapidoWebview, obterMetricaRolagemMercadoLivreFavoritos, rolarMercadoLivreFavoritos, materializarCardsPrimeiraPaginaMercadoLivreFavoritos };
  browser.firstPageSupport = Object.freeze(api);
  Object.assign(global, api);
})(window);
