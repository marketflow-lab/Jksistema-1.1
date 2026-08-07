(function (global) {
        'use strict';

        const feature = global.FavoritosV2 && global.FavoritosV2.sku;
        if (!feature || !feature.__runtimeInitialized) {
            throw new Error('Runtime do SKU nao inicializado.');
        }
        if (feature.components.has('storeCache')) return;

        function skuLojasComDados() {
            const mapa = new Map();
            (skuLojasDisponiveis || []).forEach(loja => {
                const nome = String((loja && loja.nome) || loja || '').trim();
                if (nome) mapa.set(skuNormalizarLoja(nome), { nome, total: 0 });
            });
            const limitarPorIntegracoes = mapa.size > 0;
            (skuDados || []).forEach(row => {
                const nome = skuObterLoja(row);
                const chave = skuNormalizarLoja(nome);
                if (!mapa.has(chave)) {
                    if (limitarPorIntegracoes) return;
                    mapa.set(chave, { nome, total: 0 });
                }
                mapa.get(chave).total += 1;
            });
            return Array.from(mapa.values())
                .filter(loja => loja.nome)
                .sort((a, b) => a.nome.localeCompare(b.nome, 'pt-BR', { numeric: true, sensitivity: 'base' }));
        }

        function skuRenderizarCardsLojas() {
            const lojas = skuLojasComDados();
            const lojasValidas = new Set(lojas.map(loja => skuNormalizarLoja(loja.nome)));
            if (!lojas.length) {
                skuLojaSelecionada = '';
                skuSalvarPreferenciaLoja();
                favoritosAtualizarCardLojaCabecalho();
                if (skuLojaCardsEl) skuLojaCardsEl.innerHTML = '<div class="muted">Nenhuma loja com Bling e Mercado Livre conectados.</div>';
                return;
            }
            if (!skuLojaSelecionada || favoritosEhTodasLojas(skuLojaSelecionada) || !lojasValidas.has(skuNormalizarLoja(skuLojaSelecionada))) {
                skuLojaSelecionada = FAVORITOS_TODAS_LOJAS;
                skuSalvarPreferenciaLoja();
            }
            favoritosAtualizarCardLojaCabecalho();

            if (!skuLojaCardsEl) return;

            skuLojaCardsEl.innerHTML = '';
            const criarCard = (nome, total, valor) => {
                const card = document.createElement('button');
                card.type = 'button';
                card.className = 'sku-store-card' + ((favoritosEhTodasLojas(skuLojaSelecionada) && favoritosEhTodasLojas(valor)) || skuLojaSelecionada === valor ? ' active' : '');

                const nomeEl = document.createElement('span');
                nomeEl.className = 'sku-store-name';
                nomeEl.textContent = nome;

                const metaEl = document.createElement('span');
                metaEl.className = 'sku-store-meta';
                metaEl.textContent = `${total} SKU(s)`;

                card.appendChild(nomeEl);
                card.appendChild(metaEl);
                card.addEventListener('click', () => {
                    favoritosSelecionarLojaModulo(valor);
                });
                skuLojaCardsEl.appendChild(card);
            };

            criarCard(FAVORITOS_TODAS_LOJAS_LABEL, lojas.reduce((acc, loja) => acc + Number(loja.total || 0), 0), FAVORITOS_TODAS_LOJAS);
            lojas.forEach(loja => criarCard(loja.nome, loja.total, loja.nome));
        }

        function mlSkuSalvarPreferenciaLoja() {
            try {
                if (mlSkuLojaSelecionada) {
                    localStorage.setItem(ML_SKU_LOJA_KEY, mlSkuLojaSelecionada);
                } else {
                    localStorage.removeItem(ML_SKU_LOJA_KEY);
                }
            } catch (_err) {}
        }

        function mlSkuCarregarPreferenciaLoja() {
            try {
                return localStorage.getItem(ML_SKU_LOJA_KEY) || '';
            } catch (_err) {
                return '';
            }
        }

        function favoritosMostrarTelaPrincipal() {
            document.body.classList.remove('favoritos-store-pending');
            if (favoritosStoreGateEl) favoritosStoreGateEl.classList.add('hidden');
            favoritosAtualizarCardLojaCabecalho();
            atualizarEstadoSidebarRanking();
        }

        function favoritosOcultarTelaPrincipal() {
            document.body.classList.add('favoritos-store-pending');
            if (favoritosStoreGateEl) favoritosStoreGateEl.classList.remove('hidden');
            favoritosAtualizarCardLojaCabecalho();
            atualizarEstadoSidebarRanking();
        }

        function favoritosLojaAtualNormalizada() {
            const atual = mlSkuLojaSelecionada || skuLojaSelecionada || '';
            return favoritosEhTodasLojas(atual) ? FAVORITOS_TODAS_LOJAS : skuNormalizarLoja(atual);
        }

        function favoritosChaveCacheLoja(valor = '') {
            const loja = String(valor || mlSkuLojaSelecionada || skuLojaSelecionada || '').trim();
            if (!loja) return '';
            return favoritosEhTodasLojas(loja) ? FAVORITOS_TODAS_LOJAS : skuNormalizarLoja(loja);
        }

        function favoritosResolverLojaRespostaSkus(data, fallback = '') {
            if (data && data.todas_lojas) return FAVORITOS_TODAS_LOJAS;
            return String((data && data.loja) || fallback || '').trim();
        }

        function favoritosRespostaSkusConfereComAlvo(data, chaveEsperada, fallback = '') {
            if (!chaveEsperada) return true;
            const lojaResposta = favoritosResolverLojaRespostaSkus(data, fallback);
            const chaveResposta = favoritosChaveCacheLoja(lojaResposta || fallback);
            return !chaveResposta || chaveResposta === chaveEsperada;
        }

        function favoritosClonarListaObjetos(lista) {
            return (Array.isArray(lista) ? lista : []).map(item => (
                item && typeof item === 'object' ? { ...item } : item
            ));
        }

        function favoritosClonarSkusComLoja(lista, lojaPadrao = '') {
            const loja = favoritosEhTodasLojas(lojaPadrao) ? '' : String(lojaPadrao || '').trim();
            return favoritosClonarListaObjetos(lista).map(item => {
                if (!item || typeof item !== 'object' || !loja) return item;
                if (String(item.loja || item.loja_sync || '').trim()) return item;
                return { ...item, loja, loja_sync: loja };
            });
        }

        function favoritosCacheLojaSelecionadaSegura(cache, nomeLoja, chaveCache = '') {
            const fallback = favoritosEhTodasLojas(nomeLoja) ? FAVORITOS_TODAS_LOJAS : String(nomeLoja || '').trim();
            const selecionada = String(cache && cache.lojaSelecionada || fallback || '').trim();
            const chaveSelecionada = favoritosChaveCacheLoja(selecionada);
            if (chaveCache && chaveSelecionada && chaveSelecionada !== chaveCache) return fallback;
            return selecionada || fallback;
        }

        function favoritosClonarValorCache(valor) {
            if (Array.isArray(valor)) return favoritosClonarListaObjetos(valor);
            if (!valor || typeof valor !== 'object') return valor;
            return {
                ...valor,
                anuncios: favoritosClonarListaObjetos(valor.anuncios),
                removidos_ia: favoritosClonarListaObjetos(valor.removidos_ia),
                termos: Array.isArray(valor.termos) ? valor.termos.slice() : valor.termos
            };
        }

        function favoritosClonarEntradasMapCache(mapa) {
            if (!(mapa instanceof Map)) return [];
            return Array.from(mapa.entries()).map(([chave, valor]) => [chave, favoritosClonarValorCache(valor)]);
        }

        function favoritosCacheSkusTemDados(cache) {
            if (!cache || typeof cache !== 'object') return false;
            return (Array.isArray(cache.skus) && cache.skus.length > 0)
                || (Array.isArray(cache.skuDados) && cache.skuDados.length > 0);
        }

        function favoritosObterCacheSkusLoja(nomeLoja) {
            const chave = favoritosChaveCacheLoja(nomeLoja);
            return chave ? mlSkuDadosPorLojaCache.get(chave) : null;
        }

        function favoritosCacheSkusRecente(cache) {
            const salvoEm = Number(cache && cache.salvoEm || 0);
            return !!salvoEm && (Date.now() - salvoEm) < ML_SKU_CACHE_REFRESH_MS;
        }

        function favoritosDefinirCarregamentoSkus(nomeLoja, carregando, chaveForcada = '') {
            mlSkuCarregamentoEmAndamento = !!carregando;
            mlSkuCarregamentoLoja = carregando ? String(nomeLoja || '').trim() : '';
            mlSkuCarregamentoChave = carregando ? (chaveForcada || favoritosChaveCacheLoja(nomeLoja)) : '';
            renderizarSkuSidebarMercadoLivre();
        }

        function favoritosSalvarEstadoLojaAtual(chaveForcada = '') {
            const chave = chaveForcada || favoritosLojaAtualNormalizada();
            if (!chave) return;
            mlSkuEstadoPorLojaCache.set(chave, {
                selecionados: Array.from(mlSkuSidebarSelecionados || []),
                filtro: mlSkuSidebarFiltro || '',
                renderLimit: mlSkuSidebarRenderLimit || ML_SKU_SIDEBAR_PAGE_SIZE,
                pagina: skuPaginaAtual || 1,
                favSku: favMlSkuSelecionado || '',
                favLoja: favMlLojaSelecionada || '',
                favHistoricoId: favMlHistoricoExecucaoSelecionadaId || '',
                histSku: histMlSkuSelecionado || '',
                histHistoricoId: histMlHistoricoExecucaoSelecionadaId || '',
                favAnuncios: favoritosClonarListaObjetos(favMlAnunciosSkuAtual),
                primeiraPagina: favoritosClonarListaObjetos(mlAnunciosPrimeiraPaginaAtuais),
                resultados: favoritosClonarEntradasMapCache(mlFavoritosResultadosPorSku),
                opcoesPromocao: favoritosClonarEntradasMapCache(mlFavoritosOpcoesPromocaoPorSku),
                opcoesPromocaoAtual: favoritosClonarValorCache(mlFavoritosOpcoesPromocaoAtual)
            });
        }

        function favoritosRestaurarEstadoLoja(chave) {
            const estado = mlSkuEstadoPorLojaCache.get(chave);
            mlSkuSidebarSelecionados = new Set(Array.isArray(estado && estado.selecionados) ? estado.selecionados : []);
            mlSkuSidebarFiltro = estado ? String(estado.filtro || '') : '';
            mlSkuSidebarRenderLimit = estado ? Math.max(ML_SKU_SIDEBAR_PAGE_SIZE, Number(estado.renderLimit || ML_SKU_SIDEBAR_PAGE_SIZE)) : ML_SKU_SIDEBAR_PAGE_SIZE;
            if (mlSkuSidebarSearchEl) mlSkuSidebarSearchEl.value = mlSkuSidebarFiltro;
            favMlSkuSelecionado = estado ? String(estado.favSku || '') : '';
            favMlLojaSelecionada = estado ? String(estado.favLoja || '') : '';
            favMlHistoricoExecucaoSelecionadaId = estado ? String(estado.favHistoricoId || '') : '';
            histMlSkuSelecionado = estado ? String(estado.histSku || '') : '';
            histMlHistoricoExecucaoSelecionadaId = estado ? String(estado.histHistoricoId || '') : '';
            favMlAnunciosSkuAtual = estado ? favoritosClonarListaObjetos(estado.favAnuncios) : [];
            mlAnunciosPrimeiraPaginaAtuais = estado ? favoritosClonarListaObjetos(estado.primeiraPagina) : [];
            skuPaginaAtual = estado ? Math.max(1, Number(estado.pagina || 1)) : 1;
            if (estado) {
                mlFavoritosResultadosPorSku = new Map(Array.isArray(estado.resultados) ? estado.resultados.map(([chave, valor]) => [chave, favoritosClonarValorCache(valor)]) : []);
                mlFavoritosOpcoesPromocaoPorSku = new Map(Array.isArray(estado.opcoesPromocao) ? estado.opcoesPromocao.map(([chave, valor]) => [chave, favoritosClonarValorCache(valor)]) : []);
                mlFavoritosOpcoesPromocaoAtual = favoritosClonarValorCache(estado.opcoesPromocaoAtual) || null;
            } else {
                mlFavoritosResultadosPorSku = new Map();
                mlFavoritosOpcoesPromocaoPorSku = new Map();
                mlFavoritosOpcoesPromocaoAtual = null;
                if (favRankingDataEl) favRankingDataEl.textContent = '';
            }
        }

        function favoritosSalvarCacheSkusAtual() {
            const chave = favoritosLojaAtualNormalizada();
            if (!chave) return;
            const temDados = (Array.isArray(mlSkusAnunciosLojaAtual) && mlSkusAnunciosLojaAtual.length > 0)
                || (Array.isArray(skuDados) && skuDados.length > 0);
            if (!temDados) {
                const cacheExistente = mlSkuDadosPorLojaCache.get(chave);
                if (!favoritosCacheSkusTemDados(cacheExistente)) {
                    mlSkuDadosPorLojaCache.delete(chave);
                }
                return;
            }
            mlSkuDadosPorLojaCache.set(chave, {
                lojaSelecionada: favoritosEhTodasLojas(mlSkuLojaSelecionada || skuLojaSelecionada) ? FAVORITOS_TODAS_LOJAS : String(mlSkuLojaSelecionada || skuLojaSelecionada || '').trim(),
                lojas: favoritosClonarListaObjetos(mlSkuLojasDisponiveis),
                skus: favoritosClonarListaObjetos(mlSkusAnunciosLojaAtual),
                skuDados: favoritosClonarListaObjetos(skuDados),
                todasLojas: !!favoritosSkusTodasLojasCarregados,
                totalAnuncios: Number(favoritosTotalAnunciosLojaAtual || 0),
                warning: favoritosWarningLojaAtual || '',
                cacheMeta: favoritosClonarValorCache(favoritosMlSkuCacheMetaAtual),
                ocultos: Array.from(skuSkusOcultos || []),
                salvoEm: Date.now()
            });
        }

        function favoritosLimparDadosSkusLojaSemCache(nomeLoja) {
            mlSkusAnunciosLojaAtual = [];
            skuDados = [];
            favoritosSkusTodasLojasCarregados = favoritosEhTodasLojas(nomeLoja);
            favoritosTotalAnunciosLojaAtual = 0;
            favoritosWarningLojaAtual = '';
            favoritosMlSkuCacheMetaAtual = null;
            skuCancelarBuscaDescricoesAutomaticas();
        }

        function favoritosAplicarCacheSkusLoja(nomeLoja, opcoes = {}) {
            const chave = favoritosChaveCacheLoja(nomeLoja);
            const cache = chave ? mlSkuDadosPorLojaCache.get(chave) : null;
            if (!cache) return false;
            if (!favoritosCacheSkusTemDados(cache)) {
                mlSkuDadosPorLojaCache.delete(chave);
                return false;
            }
            skuLojasDisponiveis = favoritosClonarListaObjetos(cache.lojas);
            mlSkuLojasDisponiveis = favoritosClonarListaObjetos(cache.lojas);
            mlSkuLojaSelecionada = favoritosCacheLojaSelecionadaSegura(cache, nomeLoja, chave);
            skuLojaSelecionada = mlSkuLojaSelecionada;
            mlSkusAnunciosLojaAtual = favoritosClonarSkusComLoja(cache.skus, mlSkuLojaSelecionada);
            skuDados = favoritosClonarListaObjetos(cache.skuDados);
            if (!skuDados.length && mlSkusAnunciosLojaAtual.length) {
                skuDados = mlSkusAnunciosLojaAtual
                    .map(item => skuNormalizarLinhaApiMercadoLivre(item, mlSkuLojaSelecionada))
                    .filter(item => skuObterSku(item));
            }
            favoritosSkusTodasLojasCarregados = !!cache.todasLojas;
            favoritosTotalAnunciosLojaAtual = Number(cache.totalAnuncios || 0);
            favoritosWarningLojaAtual = cache.warning || '';
            favoritosMlSkuCacheMetaAtual = favoritosClonarValorCache(cache.cacheMeta) || favoritosMlSkuCacheMetaAtual;
            skuSkusOcultos = new Set(Array.isArray(cache.ocultos) ? cache.ocultos : []);
            skuCancelarBuscaDescricoesAutomaticas();
            if (opcoes.render !== false) {
                skuRenderizarCardsLojas();
                skuRenderizarTabela();
                favoritosAtualizarCardLojaCabecalho();
                mlSkuRenderizarCardsLojas();
                renderizarSkuSidebarMercadoLivre();
                window.FavoritosV2.execution.publicApi.renderizarFavoritosSkuSidebar();
                renderizarHistoricoSkuSidebar();
                atualizarEstadoSidebarRanking();
            }
            if (skuStatusEl && opcoes.status !== false) {
                const lojaTexto = favoritosNomeLojaExibicao(mlSkuLojaSelecionada).toLowerCase();
                skuStatusEl.textContent = skuDados.length
                    ? `${skuDados.length} SKU(s) mantido(s) carregado(s) em ${lojaTexto}.`
                    : `Nenhum SKU carregado em cache para ${lojaTexto}.`;
            }
            return true;
        }

        function favoritosLojasCabecalhoDisponiveis() {
            const mapa = new Map();
            const adicionar = (nome, meta = '', valor = '') => {
                const nomeLoja = String(nome || '').trim();
                const valorLoja = String(valor || nomeLoja).trim();
                const chave = favoritosEhTodasLojas(valorLoja) ? FAVORITOS_TODAS_LOJAS : skuNormalizarLoja(nomeLoja);
                if (!nomeLoja || !chave) return;
                if (!mapa.has(chave)) {
                    mapa.set(chave, { nome: nomeLoja, valor: valorLoja, meta: String(meta || '').trim() });
                } else if (meta && !mapa.get(chave).meta) {
                    mapa.get(chave).meta = String(meta || '').trim();
                }
            };

            if ((Array.isArray(mlSkuLojasDisponiveis) && mlSkuLojasDisponiveis.length) || (Array.isArray(skuDados) && skuDados.length)) {
                adicionar(
                    FAVORITOS_TODAS_LOJAS_LABEL,
                    skuDados.length ? `${skuDados.length} SKU(s) ML` : 'Todas as contas',
                    FAVORITOS_TODAS_LOJAS
                );
            }
            (Array.isArray(mlSkuLojasDisponiveis) ? mlSkuLojasDisponiveis : []).forEach(loja => {
                adicionar((loja && loja.nome) || loja, 'Mercado Livre');
            });
            skuLojasComDados().forEach(loja => {
                adicionar(loja.nome, `${loja.total} SKU(s)`);
            });
            const atual = String(mlSkuLojaSelecionada || skuLojaSelecionada || '').trim();
            if (atual && !favoritosEhTodasLojas(atual)) adicionar(atual, '');

            return Array.from(mapa.values())
                .sort((a, b) => {
                    if (favoritosEhTodasLojas(a.valor)) return -1;
                    if (favoritosEhTodasLojas(b.valor)) return 1;
                    return a.nome.localeCompare(b.nome, 'pt-BR', { numeric: true, sensitivity: 'base' });
                });
        }

        function favoritosMetaLojaCabecalho(nome) {
            if (favoritosEhTodasLojas(nome)) {
                return skuDados.length ? `${skuDados.length} SKU(s) ML` : 'Todas as contas';
            }
            const nomeNorm = skuNormalizarLoja(nome);
            if (nomeNorm && nomeNorm === skuNormalizarLoja(mlSkuLojaSelecionada) && mlSkusAnunciosLojaAtual.length) {
                return `${mlSkusAnunciosLojaAtual.length} SKU(s) ML`;
            }
            const lojaSku = skuLojasComDados().find(loja => skuNormalizarLoja(loja.nome) === nomeNorm);
            if (lojaSku && lojaSku.total) return `${lojaSku.total} SKU(s)`;
            return 'Bling + Mercado Livre';
        }

        function favoritosAtualizarCardLojaCabecalho() {
            if (!favoritosHeaderStoreCardsEl) return;
            const lojas = favoritosLojasCabecalhoDisponiveis();
            const ativa = favoritosLojaAtualNormalizada() || skuNormalizarLoja(mlSkuCarregarPreferenciaLoja() || skuCarregarPreferenciaLoja());
            favoritosHeaderStoreCardsEl.innerHTML = '';
            favoritosHeaderStoreCardsEl.classList.toggle('hidden', !lojas.length);
            lojas.forEach(loja => {
                const valor = loja.valor || loja.nome;
                const chaveCard = favoritosEhTodasLojas(valor) ? FAVORITOS_TODAS_LOJAS : skuNormalizarLoja(valor);
                const card = document.createElement('button');
                card.type = 'button';
                card.className = 'module-store-card' + (ativa && ativa === chaveCard ? ' active' : '');

                const labelEl = document.createElement('span');
                labelEl.className = 'module-store-card-label';
                labelEl.textContent = ativa && ativa === chaveCard ? 'Loja selecionada' : 'Loja';

                const nomeEl = document.createElement('span');
                nomeEl.className = 'module-store-card-name';
                nomeEl.textContent = loja.nome;

                const metaEl = document.createElement('span');
                metaEl.className = 'sku-store-meta';
                metaEl.textContent = loja.meta || favoritosMetaLojaCabecalho(valor) || 'Mercado Livre';

                card.appendChild(labelEl);
                card.appendChild(nomeEl);
                card.appendChild(metaEl);
                card.addEventListener('click', async () => {
                    if (card.dataset.carregando === '1') return;
                    card.dataset.carregando = '1';
                    try {
                        if (favoritosLojaEntradaSelecionada) {
                            await favoritosSelecionarLojaModulo(valor);
                        } else {
                            await favoritosSelecionarLojaEntrada(valor);
                        }
                        favoritosAtualizarAbaAtualAoTrocarLoja();
                    } finally {
                        delete card.dataset.carregando;
                    }
                });
                favoritosHeaderStoreCardsEl.appendChild(card);
            });
        }

        function favoritosObterAbaAtual() {
            const abaAtiva = document.querySelector('.aba-conteudo.active');
            const id = String(abaAtiva && abaAtiva.id || '').trim();
            return id.startsWith('aba-') ? id.slice(4) : 'pesquisa';
        }

        function favoritosAtualizarAbaAtualAoTrocarLoja() {
            const abaAtual = favoritosObterAbaAtual();
            atualizarEstadoSidebarRanking();
            renderizarSkuSidebarMercadoLivre();
            if (abaAtual === 'pesquisa') {
                skuRenderizarTabela();
            } else if (abaAtual === 'favoritos') {
                prepararAbaFavoritosMl();
            } else if (abaAtual === 'historico') {
                prepararAbaHistoricoFavoritos();
            } else if (abaAtual === 'planilhas') {
                prepararAbaPlanilhasFavoritos();
            } else if (abaAtual === 'vendedores') {
                renderizarVendedoresIgnoradosRanking();
            } else if (abaAtual === 'anuncios-ignorados') {
                renderizarAnunciosIgnoradosSku();
            }
        }

        function favoritosResetarEstadoLoja() {
            mlSkuSidebarSelecionados.clear();
            mlSkuSidebarFiltro = '';
            mlSkuSidebarRenderLimit = ML_SKU_SIDEBAR_PAGE_SIZE;
            if (mlSkuSidebarSearchEl) mlSkuSidebarSearchEl.value = '';
            mlFavoritosResultadosPorSku = new Map();
            mlFavoritosOpcoesPromocaoPorSku = new Map();
            mlFavoritosOpcoesPromocaoAtual = null;
            favMlSkuSelecionado = '';
            favMlLojaSelecionada = '';
            favMlHistoricoExecucaoSelecionadaId = '';
            histMlSkuSelecionado = '';
            histMlHistoricoExecucaoSelecionadaId = '';
            favMlAnunciosSkuAtual = [];
            mlAnunciosPrimeiraPaginaAtuais = [];
            if (favRankingDataEl) favRankingDataEl.textContent = '';
        }

        function favoritosDefinirLojaSelecionada(nome) {
            const nomeLoja = favoritosEhTodasLojas(nome) ? FAVORITOS_TODAS_LOJAS : String(nome || '').trim();
            if (!nomeLoja) return false;
            const anterior = favoritosLojaAtualNormalizada();
            const nova = favoritosEhTodasLojas(nomeLoja) ? FAVORITOS_TODAS_LOJAS : skuNormalizarLoja(nomeLoja);
            if (anterior && anterior !== nova) {
                favoritosSalvarEstadoLojaAtual(anterior);
            }
            skuLojaSelecionada = nomeLoja;
            mlSkuLojaSelecionada = nomeLoja;
            skuSalvarPreferenciaLoja();
            mlSkuSalvarPreferenciaLoja();
            favoritosAtualizarCardLojaCabecalho();
            if (anterior && anterior !== nova) {
                favoritosRestaurarEstadoLoja(nova);
                if (!favoritosAplicarCacheSkusLoja(nomeLoja, { render: false, status: false })) {
                    favoritosLimparDadosSkusLojaSemCache(nomeLoja);
                }
            }
            return anterior !== nova;
        }

        function favoritosRenderizarCardsEntrada(lojas) {
            const lista = Array.isArray(lojas) ? lojas : [];
            mlSkuLojasDisponiveis = lista;
            favoritosAtualizarCardLojaCabecalho();
            if (!lista.length) {
                if (favoritosStoreGateStatusEl) {
                    favoritosStoreGateStatusEl.textContent = 'Nenhuma loja com Bling e Mercado Livre conectados foi encontrada em Integracoes.';
                }
                return;
            }
        }

        function favoritosResolverLojaInicialRapida(lojas) {
            const lista = (Array.isArray(lojas) ? lojas : [])
                .map(loja => ({ nome: String((loja && loja.nome) || loja || '').trim() }))
                .filter(loja => loja.nome);
            if (!lista.length) return FAVORITOS_TODAS_LOJAS;
            const preferencias = [mlSkuCarregarPreferenciaLoja(), skuCarregarPreferenciaLoja()];
            for (const pref of preferencias) {
                const prefTxt = String(pref || '').trim();
                if (!prefTxt || favoritosEhTodasLojas(prefTxt)) continue;
                const prefNorm = skuNormalizarLoja(prefTxt);
                const encontrada = lista.find(loja => skuNormalizarLoja(loja.nome) === prefNorm);
                if (encontrada) return encontrada.nome;
            }
            return lista[0].nome;
        }

        async function favoritosCarregarLojasEntrada() {
            favoritosLojaEntradaSelecionada = false;
            favoritosOcultarTelaPrincipal();
            if (favoritosStoreGateStatusEl) {
                favoritosStoreGateStatusEl.classList.remove('error');
                favoritosStoreGateStatusEl.textContent = 'Carregando lojas cadastradas em Integracoes...';
            }
            try {
                const response = await fetchFavoritosComTimeout('/api/favoritos/ml/skus-anuncios?apenas_lojas=1', {
                    headers: obterAuthHeaders(),
                    cache: 'no-store'
                }, 12000);
                if (!response.ok) {
                    let detalhe = `HTTP ${response.status}`;
                    try {
                        const dataErro = await response.json();
                        detalhe = dataErro.detail || detalhe;
                    } catch (_err) {}
                    throw new Error(detalhe);
                }
                const data = await response.json();
                const lojas = Array.isArray(data.lojas) ? data.lojas : [];
                mlSkuLojasDisponiveis = lojas;
                favoritosRenderizarCardsEntrada(lojas);
                if (favoritosStoreGateStatusEl) {
                    favoritosStoreGateStatusEl.classList.remove('error');
                    favoritosStoreGateStatusEl.textContent = data.warning || (lojas.length
                        ? 'Lojas carregadas. Carregando SKUs em segundo plano...'
                        : 'Nenhuma loja disponivel para fazer favoritos.');
                }
                if (lojas.length) {
                    const lojaInicial = favoritosResolverLojaInicialRapida(lojas);
                    favoritosDefinirLojaSelecionada(lojaInicial);
                    favoritosLojaEntradaSelecionada = true;
                    favoritosMostrarTelaPrincipal();
                    if (skuStatusEl) {
                        skuStatusEl.textContent = `Carregando SKUs da loja ${favoritosNomeLojaExibicao(lojaInicial)} em segundo plano...`;
                    }
                    carregarSkuFavoritos(lojaInicial).catch(err => {
                        console.warn('[Favoritos ML] erro ao carregar SKUs em segundo plano:', err);
                        if (skuStatusEl) {
                            skuStatusEl.textContent = `Erro ao carregar SKUs do Mercado Livre: ${err && err.message ? err.message : err}`;
                        }
                    });
                }
            } catch (err) {
                favoritosRenderizarCardsEntrada([]);
                if (favoritosStoreGateStatusEl) {
                    favoritosStoreGateStatusEl.classList.add('error');
                    favoritosStoreGateStatusEl.textContent = `Erro ao carregar lojas: ${err && err.message ? err.message : err}`;
                }
            }
        }

        async function favoritosSelecionarLojaEntrada(nome) {
            const nomeLoja = String(nome || '').trim();
            if (!nomeLoja) return;
            favoritosDefinirLojaSelecionada(nomeLoja);
            favoritosLojaEntradaSelecionada = true;
            favoritosMostrarTelaPrincipal();
            if (favoritosStoreGateStatusEl) {
                favoritosStoreGateStatusEl.classList.remove('error');
                favoritosStoreGateStatusEl.textContent = '';
            }
            await carregarSkuFavoritos();
        }

        async function favoritosSelecionarLojaModulo(nome) {
            const nomeLoja = String(nome || '').trim();
            if (!nomeLoja) return;
            const mudou = favoritosDefinirLojaSelecionada(nomeLoja);
            favoritosLojaEntradaSelecionada = true;
            favoritosMostrarTelaPrincipal();
            skuRenderizarCardsLojas();
            skuRenderizarTabela();
            mlSkuRenderizarCardsLojas();
            renderizarSkuSidebarMercadoLivre();
            window.FavoritosV2.execution.publicApi.renderizarFavoritosSkuSidebar();
            renderizarHistoricoSkuSidebar();
            atualizarEstadoSidebarRanking();
            if (mudou || !skuDados.length || !mlSkusAnunciosLojaAtual.length) {
                await carregarSkuFavoritos(nomeLoja);
            }
        }

        function mlSkuRenderizarCardsLojas() {
            const lojas = Array.isArray(mlSkuLojasDisponiveis) ? mlSkuLojasDisponiveis : [];
            if (!lojas.length) {
                favoritosAtualizarCardLojaCabecalho();
                if (mlSkuLojaCardsEl) mlSkuLojaCardsEl.innerHTML = '<div class="muted">Nenhuma loja com Mercado Livre conectado em Integrações.</div>';
                return;
            }
            favoritosAtualizarCardLojaCabecalho();
            if (!mlSkuLojaCardsEl) return;
            mlSkuLojaCardsEl.innerHTML = '';
            lojas.forEach(loja => {
                const nome = String((loja && loja.nome) || loja || '').trim();
                if (!nome) return;
                const ativo = skuNormalizarLoja(nome) === skuNormalizarLoja(mlSkuLojaSelecionada);
                const card = document.createElement('button');
                card.type = 'button';
                card.className = 'sku-store-card' + (ativo ? ' active' : '');

                const nomeEl = document.createElement('span');
                nomeEl.className = 'sku-store-name';
                nomeEl.textContent = nome;

                const metaEl = document.createElement('span');
                metaEl.className = 'sku-store-meta';
                metaEl.textContent = ativo && mlSkusAnunciosLojaAtual.length
                    ? `${mlSkusAnunciosLojaAtual.length} SKU(s) ML`
                    : 'Mercado Livre';

                card.appendChild(nomeEl);
                card.appendChild(metaEl);
                card.addEventListener('click', () => {
                    if (skuNormalizarLoja(mlSkuLojaSelecionada) === skuNormalizarLoja(nome)) return;
                    favoritosSelecionarLojaModulo(nome);
                });
                mlSkuLojaCardsEl.appendChild(card);
            });
        }

        async function mlSkuCarregarSkusAnuncios(loja = '', opcoes = {}) {
            if (!mlSkuLojaCardsEl && !mlSkuSidebarListEl) return;
            if (opcoes && opcoes.type && opcoes.target) opcoes = {};
            const forcarAtualizacao = !!(opcoes && opcoes.atualizar);
            const preferida = loja || mlSkuLojaSelecionada || mlSkuCarregarPreferenciaLoja();
            const carregarTodas = !preferida || favoritosEhTodasLojas(preferida);
            const alvoCache = preferida || FAVORITOS_TODAS_LOJAS;
            const chaveCache = favoritosChaveCacheLoja(alvoCache);
            const cacheAplicado = !forcarAtualizacao && favoritosAplicarCacheSkusLoja(alvoCache, { status: false });
            if (cacheAplicado) {
                return;
            }
            const promiseKey = chaveCache ? `${chaveCache}:${forcarAtualizacao ? 'atualizar' : 'cache'}` : '';
            const promiseEmAndamento = promiseKey ? mlSkuCarregamentoPromises.get(promiseKey) : null;
            if (promiseEmAndamento) return promiseEmAndamento;
            const runId = ++mlSkuCarregamentoRunId;
            favoritosDefinirCarregamentoSkus(alvoCache, true, chaveCache);
            if (mlSkuStatusEl) {
                if (forcarAtualizacao) {
                    mlSkuStatusEl.textContent = carregarTodas
                        ? 'Atualizando SKUs e anuncios de todas as lojas no Mercado Livre...'
                        : `Atualizando SKUs e anuncios da loja ${preferida} no Mercado Livre...`;
                } else {
                mlSkuStatusEl.textContent = carregarTodas
                    ? 'Carregando SKUs dos anuncios de todas as lojas...'
                    : preferida
                    ? `Carregando SKUs dos anúncios da loja ${preferida}...`
                    : 'Carregando lojas integradas ao Mercado Livre...';
                }
            }
            const promessa = (async () => {
            try {
                const params = new URLSearchParams();
                if (carregarTodas) {
                    params.set('todas_lojas', '1');
                } else if (preferida) {
                    params.set('loja', preferida);
                }
                if (forcarAtualizacao) params.set('atualizar', '1');
                const response = await fetch(`/api/favoritos/ml/skus-anuncios${params.toString() ? `?${params.toString()}` : ''}`, {
                    headers: obterAuthHeaders(),
                    cache: 'no-store'
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
                if (runId !== mlSkuCarregamentoRunId || !favoritosRespostaSkusConfereComAlvo(data, chaveCache, preferida || alvoCache)) {
                    return;
                }
                mlSkuLojasDisponiveis = Array.isArray(data.lojas) ? data.lojas : [];
                mlSkuLojaSelecionada = favoritosResolverLojaRespostaSkus(data, preferida || alvoCache);
                if (mlSkuLojaSelecionada) skuLojaSelecionada = mlSkuLojaSelecionada;
                mlSkusAnunciosLojaAtual = favoritosClonarSkusComLoja(data.skus, mlSkuLojaSelecionada);
                favoritosSkusTodasLojasCarregados = !!data.todas_lojas;
                favoritosTotalAnunciosLojaAtual = Number(data.total_anuncios || 0);
                favoritosWarningLojaAtual = data.warning || '';
                favoritosMlSkuCacheMetaAtual = favoritosCacheMlMeta(data);
                skuLojasDisponiveis = mlSkuLojasDisponiveis;
                skuAtualizarDadosComApiMercadoLivre(mlSkusAnunciosLojaAtual, mlSkuLojaSelecionada);
                favoritosSalvarCacheSkusAtual();
                if (!mlSkusAnunciosLojaAtual.length && !skuDados.length && chaveCache) {
                    mlSkuDadosPorLojaCache.delete(chaveCache);
                }
                mlSkuSidebarRenderLimit = ML_SKU_SIDEBAR_PAGE_SIZE;
                if (mlSkuLojaSelecionada) mlSkuSalvarPreferenciaLoja();
                favoritosAtualizarCardLojaCabecalho();
                mlSkuRenderizarCardsLojas();
                renderizarSkuSidebarMercadoLivre();
                window.FavoritosV2.execution.publicApi.renderizarFavoritosSkuSidebar();
                renderizarHistoricoSkuSidebar();
                atualizarEstadoSidebarRanking();
                if (mlSkuStatusEl) {
                    if (data.warning) {
                        mlSkuStatusEl.textContent = data.warning;
                    } else {
                        mlSkuStatusEl.textContent = data.todas_lojas
                            ? `${mlSkusAnunciosLojaAtual.length} SKU(s) unicos carregados de ${data.total_anuncios || 0} anuncio(s) ativos em todas as lojas.`
                            : mlSkuLojaSelecionada
                            ? `${mlSkusAnunciosLojaAtual.length} SKU(s) únicos carregados de ${data.total_anuncios || 0} anúncio(s) ativos da loja ${mlSkuLojaSelecionada}.`
                            : 'Selecione uma loja para carregar os SKUs do Mercado Livre.';
                    }
                }
                if (mlSkuStatusEl && !data.warning) {
                    const statusCache = favoritosMensagemCacheMl(data);
                    if (statusCache) mlSkuStatusEl.textContent = `${mlSkuStatusEl.textContent} ${statusCache}`;
                }
            } catch (err) {
                favoritosWarningLojaAtual = `Erro ao carregar SKUs do Mercado Livre: ${err && err.message ? err.message : err}`;
                if (!cacheAplicado) {
                    renderizarSkuSidebarMercadoLivre();
                    window.FavoritosV2.execution.publicApi.renderizarFavoritosSkuSidebar();
                    renderizarHistoricoSkuSidebar();
                    atualizarEstadoSidebarRanking();
                }
                if (mlSkuStatusEl) {
                    mlSkuStatusEl.textContent = favoritosWarningLojaAtual;
                }
            } finally {
                if (runId === mlSkuCarregamentoRunId) {
                    favoritosDefinirCarregamentoSkus('', false);
                }
                if (promiseKey) mlSkuCarregamentoPromises.delete(promiseKey);
            }
            })();
            if (promiseKey) mlSkuCarregamentoPromises.set(promiseKey, promessa);
            return promessa;
        }


        Object.assign(feature.internal, {
            skuLojasComDados,
            skuRenderizarCardsLojas,
            mlSkuSalvarPreferenciaLoja,
            mlSkuCarregarPreferenciaLoja,
            favoritosMostrarTelaPrincipal,
            favoritosOcultarTelaPrincipal,
            favoritosLojaAtualNormalizada,
            favoritosChaveCacheLoja,
            favoritosResolverLojaRespostaSkus,
            favoritosRespostaSkusConfereComAlvo,
            favoritosClonarListaObjetos,
            favoritosClonarSkusComLoja,
            favoritosCacheLojaSelecionadaSegura,
            favoritosClonarValorCache,
            favoritosClonarEntradasMapCache,
            favoritosCacheSkusTemDados,
            favoritosObterCacheSkusLoja,
            favoritosCacheSkusRecente,
            favoritosDefinirCarregamentoSkus,
            favoritosSalvarEstadoLojaAtual,
            favoritosRestaurarEstadoLoja,
            favoritosSalvarCacheSkusAtual,
            favoritosLimparDadosSkusLojaSemCache,
            favoritosAplicarCacheSkusLoja,
            favoritosLojasCabecalhoDisponiveis,
            favoritosMetaLojaCabecalho,
            favoritosAtualizarCardLojaCabecalho,
            favoritosObterAbaAtual,
            favoritosAtualizarAbaAtualAoTrocarLoja,
            favoritosResetarEstadoLoja,
            favoritosDefinirLojaSelecionada,
            favoritosRenderizarCardsEntrada,
            favoritosResolverLojaInicialRapida,
            favoritosCarregarLojasEntrada,
            favoritosSelecionarLojaEntrada,
            favoritosSelecionarLojaModulo,
            mlSkuRenderizarCardsLojas,
            mlSkuCarregarSkusAnuncios
        });
        feature.components.add('storeCache');
    })(window);
