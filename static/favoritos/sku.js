        function skuTexto(row, campos, fallback = '') {
            for (const campo of campos) {
                const valor = row && row[campo];
                if (valor !== undefined && valor !== null && String(valor).trim() !== '') {
                    return String(valor).trim();
                }
            }
            return fallback;
        }

        function skuNumero(valor) {
            if (typeof valor === 'number') return Number.isFinite(valor) ? valor : 0;
            const texto = String(valor || '').trim();
            if (!texto) return 0;
            const normalizado = texto.replace(/\./g, '').replace(',', '.').replace(/[^\d.-]/g, '');
            const numero = Number(normalizado);
            return Number.isFinite(numero) ? numero : 0;
        }

        function skuFormatarNumero(valor) {
            const numero = skuNumero(valor);
            return numero.toLocaleString('pt-BR', { maximumFractionDigits: 2 });
        }

        function skuObterSku(row) {
            return skuTexto(row, ['sku', 'SKU', 'codigo', 'Codigo', 'código'], '');
        }

        function skuChaveOculto(valor) {
            return String(valor || '').trim().toUpperCase();
        }

        function skuEstaOculto(row) {
            const sku = skuChaveOculto(skuObterSku(row));
            return !!sku && skuSkusOcultos.has(sku);
        }

        function skuObterProduto(row) {
            return skuTexto(row, ['nome', 'produto', 'produto_bling', 'nome_bling', 'titulo'], '-');
        }

        function skuObterLoja(row) {
            return skuTexto(row, ['loja_sync', 'loja', 'Loja'], 'Sem loja');
        }

        function skuObterSaldoLoja(row) {
            return skuNumero(row && (row.saldo_loja ?? row.estoque_loja ?? row.loja_saldo));
        }

        function skuObterSaldoFull(row) {
            return skuNumero(row && (row.saldo_full ?? row.estoque_full ?? row.full_saldo));
        }

        function skuObterTotal(row) {
            const total = row && (row.saldo_total ?? row.estoque_total ?? row.total_estoque ?? row.quantidade);
            if (total !== undefined && total !== null && String(total).trim() !== '') {
                return skuNumero(total);
            }
            return skuObterSaldoLoja(row) + skuObterSaldoFull(row);
        }

        function skuObterPesquisa(row, numero) {
            const campos = numero === 3
                ? ['pesquisa_3', 'pesquisa3', 'Pesquisa 3']
                : (numero === 2
                    ? ['pesquisa_2', 'pesquisa2', 'Pesquisa 2']
                    : ['pesquisa_1', 'pesquisa1', 'Pesquisa 1']);
            return skuTexto(row, campos, '');
        }

        function skuChaveSku(valor) {
            return String(valor || '').trim().toLowerCase();
        }

        function aplicarPesquisasGlobaisSkuLocal(sku, campos = {}, opcoes = {}) {
            const chave = skuChaveSku(sku);
            if (!chave) return 0;
            const preservarVazios = !!(opcoes && opcoes.preservarVazios);
            const valores = {
                pesquisa_1: String(campos.pesquisa_1 ?? campos.pesquisa1 ?? campos['Pesquisa 1'] ?? '').trim(),
                pesquisa_2: String(campos.pesquisa_2 ?? campos.pesquisa2 ?? campos['Pesquisa 2'] ?? '').trim(),
                pesquisa_3: String(campos.pesquisa_3 ?? campos.pesquisa3 ?? campos['Pesquisa 3'] ?? '').trim()
            };
            let atualizados = 0;
            const aplicar = (row) => {
                if (!row || skuChaveSku(skuObterSku(row) || row.sku) !== chave) return;
                ['pesquisa_1', 'pesquisa_2', 'pesquisa_3'].forEach(campo => {
                    if (preservarVazios && !valores[campo]) return;
                    row[campo] = valores[campo];
                });
                atualizados += 1;
            };
            if (Array.isArray(skuDados)) skuDados.forEach(aplicar);
            if (Array.isArray(mlSkusAnunciosLojaAtual)) mlSkusAnunciosLojaAtual.forEach(aplicar);
            return atualizados;
        }

        function skuChavePreferenciaLoja() {
            const cid = userData && userData.client_id ? userData.client_id : 'default';
            return `favoritos_sku_loja_${cid}`;
        }

        function skuCarregarPreferenciaLoja() {
            try {
                return localStorage.getItem(skuChavePreferenciaLoja()) || '';
            } catch (_err) {
                return '';
            }
        }

        function skuSalvarPreferenciaLoja() {
            try {
                if (skuLojaSelecionada) {
                    localStorage.setItem(skuChavePreferenciaLoja(), skuLojaSelecionada);
                } else {
                    localStorage.removeItem(skuChavePreferenciaLoja());
                }
            } catch (_err) {}
        }

        function skuResumoDescricao(texto) {
            return String(texto || '').replace(/\s+/g, ' ').trim();
        }

        function skuChaveDescricao(row) {
            return skuObterSku(row).trim().toLowerCase();
        }

        function skuItemIdsDescricao(row) {
            const ids = Array.isArray(row && row.item_ids) ? row.item_ids : [];
            return ids.map(id => String(id || '').trim()).filter(Boolean);
        }

        function skuItemIdsPorSkuDescricao(rows, skus = []) {
            const filtro = new Set((skus || []).map(sku => String(sku || '').trim().toLowerCase()).filter(Boolean));
            const mapa = {};
            (rows || []).forEach(row => {
                const sku = skuObterSku(row);
                const chave = String(sku || '').trim().toLowerCase();
                if (!chave || (filtro.size && !filtro.has(chave))) return;
                const idsAtuais = new Set(mapa[sku] || []);
                skuItemIdsDescricao(row).forEach(id => idsAtuais.add(id));
                if (idsAtuais.size) mapa[sku] = Array.from(idsAtuais);
            });
            return mapa;
        }

        function skuAplicarDescricaoResultado(row, resultado) {
            const status = String(resultado && resultado.status || '').trim();
            const descricao = String(resultado && resultado.descricao || '').trim();
            row.descricao_ml_status = status || (descricao ? 'ok' : 'nao_encontrado');
            row.descricao_ml = descricao;
            row.descricao_ml_titulo = (resultado && resultado.titulo) || '';
            row.descricao_ml_item_id = (resultado && resultado.item_id) || '';
            row.descricao_ml_link = (resultado && resultado.permalink) || '';
            row.descricao_ml_loja = (resultado && resultado.loja) || skuObterLoja(row);
            row.descricao_ml_erro = (resultado && resultado.erro) || '';
        }

        function skuDescricaoCacheKey(row, loja = '') {
            const sku = skuChaveDescricao(row);
            const lojaKey = String(
                loja
                || favoritosLojaSelecionadaParaApi(skuObterLoja(row))
                || skuObterLoja(row)
                || ''
            ).trim().toLowerCase();
            const itemIds = Array.from(new Set(
                (Array.isArray(row && row.item_ids) ? row.item_ids : [])
                    .concat(row && row.descricao_ml_item_id ? [row.descricao_ml_item_id] : [])
                    .map(value => String(value || '').trim().toUpperCase())
                    .filter(Boolean)
            )).sort().join(',');
            return `${lojaKey}|${sku}|${itemIds}`;
        }

        function skuDescricaoCacheGet(row, loja = '') {
            const key = skuDescricaoCacheKey(row, loja);
            const cached = key ? skuDescricaoMemCache.get(key) : null;
            if (!cached) return null;
            if (Number(cached.expiresAt || 0) <= Date.now()) {
                skuDescricaoMemCache.delete(key);
                return null;
            }
            return cached.resultado && typeof cached.resultado === 'object'
                ? { ...cached.resultado, cache_hit: true }
                : null;
        }

        function skuDescricaoCacheSet(row, resultado, loja = '') {
            const key = skuDescricaoCacheKey(row, loja);
            if (!key || !resultado || typeof resultado !== 'object') return;
            const temDescricao = !!String(resultado.descricao || '').trim();
            const ttl = temDescricao ? SKU_DESCRICAO_CACHE_TTL_OK_MS : SKU_DESCRICAO_CACHE_TTL_EMPTY_MS;
            skuDescricaoMemCache.set(key, {
                expiresAt: Date.now() + ttl,
                resultado: { ...resultado }
            });
        }

        function skuCancelarBuscaDescricoesAutomaticas() {
            skuDescricaoAutoRunId++;
            if (skuDescricaoAutoTimer) {
                clearTimeout(skuDescricaoAutoTimer);
                skuDescricaoAutoTimer = null;
            }
            if (skuDescricaoAutoAbortController) {
                try { skuDescricaoAutoAbortController.abort(); } catch (_err) {}
                skuDescricaoAutoAbortController = null;
            }
            (Array.isArray(skuDados) ? skuDados : []).forEach(row => {
                if (String(row && row.descricao_ml_status || '') === 'loading') {
                    row.descricao_ml_status = '';
                }
            });
        }

        function skuNormalizarLinhaApiMercadoLivre(row, lojaPadrao = '') {
            const base = row && typeof row === 'object' ? row : {};
            const lojaApi = String(base.loja || base.loja_sync || lojaPadrao || mlSkuLojaSelecionada || skuLojaSelecionada || '').trim();
            const titulo = String(base.titulo || base.title || base.produto || base.nome || '').trim();
            const itemIds = Array.isArray(base.item_ids) ? base.item_ids : [];
            const links = Array.isArray(base.links) ? base.links : [];
            return {
                ...base,
                sku: String(base.sku || base.SKU || '').trim(),
                nome: titulo,
                produto: titulo,
                titulo,
                loja: lojaApi,
                loja_sync: lojaApi,
                saldo_loja: base.estoque_cadastro_loja ?? base.saldo_loja ?? base.estoque_loja ?? base.loja_saldo ?? 0,
                estoque_loja: base.estoque_cadastro_loja ?? base.estoque_loja ?? base.saldo_loja ?? base.loja_saldo ?? 0,
                item_ids: itemIds,
                links,
                total_anuncios: base.total_anuncios ?? itemIds.length,
                fonte: base.fonte || 'mercadolivre_api'
            };
        }

        function skuAtualizarDadosComApiMercadoLivre(skus, loja) {
            skuCancelarBuscaDescricoesAutomaticas();
            skuDados = (Array.isArray(skus) ? skus : [])
                .map(item => skuNormalizarLinhaApiMercadoLivre(item, loja))
                .filter(item => skuObterSku(item));
            skuPaginaAtual = 1;
            skuRenderizarCardsLojas();
            skuRenderizarTabela();
            skuAgendarBuscaDescricoesAutomaticas(400);
        }

        function skuAgendarBuscaDescricoesAutomaticas(delay = 250) {
            skuCancelarBuscaDescricoesAutomaticas();
            skuDescricaoAutoTimer = setTimeout(() => {
                skuDescricaoAutoTimer = null;
                skuBuscarDescricoesAutomaticas().catch(err => {
                    if (err && err.name === 'AbortError') return;
                    skuCancelarBuscaDescricoesAutomaticas();
                    skuRenderizarTabela();
                    if (skuStatusEl) skuStatusEl.textContent = `Erro ao buscar descrições: ${err && err.message ? err.message : err}`;
                });
            }, delay);
        }

        async function skuBuscarDescricoesAutomaticas() {
            const runId = ++skuDescricaoAutoRunId;
            if (skuDescricaoAutoAbortController) {
                try { skuDescricaoAutoAbortController.abort(); } catch (_err) {}
            }
            const controller = new AbortController();
            skuDescricaoAutoAbortController = controller;
            const todasLinhas = skuFiltrarDados()
                .filter(row => {
                    const status = String(row.descricao_ml_status || '').trim();
                    return skuObterSku(row) && !row.descricao_ml && status !== 'loading' && status !== 'ok' && status !== 'nao_encontrado' && status !== 'sem_descricao';
                });
            if (!todasLinhas.length) {
                if (skuDescricaoAutoAbortController === controller) skuDescricaoAutoAbortController = null;
                return;
            }

            const inicioPagina = Math.max(0, (Math.max(1, Number(skuPaginaAtual) || 1) - 1) * SKU_API_PAGE_SIZE);
            const linhasVisiveis = todasLinhas.slice(inicioPagina, inicioPagina + SKU_API_PAGE_SIZE);
            const visiveis = new Set(linhasVisiveis);
            const linhas = linhasVisiveis.concat(todasLinhas.filter(row => !visiveis.has(row)));
            const lojaDescricoes = favoritosLojaSelecionadaParaApi();
            const pendentes = [];
            linhas.forEach(row => {
                const cached = skuDescricaoCacheGet(row, lojaDescricoes);
                if (cached) {
                    skuAplicarDescricaoResultado(row, cached);
                } else {
                    pendentes.push(row);
                }
            });
            if (pendentes.length !== linhas.length) skuRenderizarTabela();
            if (!pendentes.length) {
                if (skuDescricaoAutoAbortController === controller) skuDescricaoAutoAbortController = null;
                return;
            }

            const skus = Array.from(new Set(pendentes.map(row => skuObterSku(row)).filter(Boolean)));
            pendentes.forEach(row => {
                row.descricao_ml_status = 'loading';
                row.descricao_ml_erro = '';
            });
            skuRenderizarTabela();

            const tamanhoLote = 60;
            let processadas = 0;
            let encontradas = 0;

            for (let inicio = 0; inicio < skus.length; inicio += tamanhoLote) {
                if (runId !== skuDescricaoAutoRunId) return;
                const lote = skus.slice(inicio, inicio + tamanhoLote);
                const body = { skus: lote, force_refresh: false };
                const itemIdsPorSku = skuItemIdsPorSkuDescricao(pendentes, lote);
                if (Object.keys(itemIdsPorSku).length) {
                    body.item_ids_por_sku = itemIdsPorSku;
                }
                if (lojaDescricoes) {
                    body.loja = lojaDescricoes;
                }

                if (skuStatusEl) {
                    skuStatusEl.textContent = `Buscando descricoes: ${processadas}/${skus.length} SKU(s)...`;
                }

                const response = await fetch('/api/favoritos/skus/descricoes', {
                    method: 'POST',
                    headers: headersJsonAutenticado(),
                    body: JSON.stringify(body),
                    signal: controller.signal
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
                if (runId !== skuDescricaoAutoRunId) return;
                const resultados = Array.isArray(data.results) ? data.results : [];
                const mapaResultados = new Map(resultados.map(item => [String(item.sku || '').trim().toLowerCase(), item]));
                skuDados.forEach(row => {
                    const resultado = mapaResultados.get(skuChaveDescricao(row));
                    if (resultado) {
                        skuAplicarDescricaoResultado(row, resultado);
                        skuDescricaoCacheSet(row, resultado, lojaDescricoes);
                    }
                });
                processadas += lote.length;
                encontradas += resultados.filter(item => String(item.descricao || '').trim()).length;
                skuRenderizarTabela();

                if (skuStatusEl) {
                    skuStatusEl.textContent = `Descricoes carregadas: ${encontradas}/${processadas} SKU(s) processados de ${skus.length}.`;
                }
            }
            if (skuDescricaoAutoAbortController === controller) skuDescricaoAutoAbortController = null;
        }
        async function skuBuscarDescricaoManual(row, botao) {
            const sku = skuObterSku(row);
            if (!sku) return;
            skuCancelarBuscaDescricoesAutomaticas();
            if (botao) botao.disabled = true;
            row.descricao_ml_status = 'loading';
            row.descricao_ml_erro = '';
            skuRenderizarTabela();
            if (skuStatusEl) skuStatusEl.textContent = `Buscando descricao do SKU ${sku}...`;

            try {
                const body = { skus: [sku], force_refresh: true };
                const itemIdsPorSku = skuItemIdsPorSkuDescricao([row], [sku]);
                if (Object.keys(itemIdsPorSku).length) body.item_ids_por_sku = itemIdsPorSku;
                const lojaDescricao = favoritosLojaSelecionadaParaApi(skuObterLoja(row));
                if (lojaDescricao) body.loja = lojaDescricao;
                const response = await fetch('/api/favoritos/skus/descricoes', {
                    method: 'POST',
                    headers: headersJsonAutenticado(),
                    body: JSON.stringify(body)
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
                const resultados = Array.isArray(data.results) ? data.results : [];
                const chave = skuChaveDescricao(row);
                const resultado = resultados.find(item => String(item.sku || '').trim().toLowerCase() === chave);
                if (resultado) {
                    skuDados.forEach(item => {
                        if (skuChaveDescricao(item) === chave) {
                            skuAplicarDescricaoResultado(item, resultado);
                            skuDescricaoCacheSet(item, resultado, lojaDescricao);
                        }
                    });
                } else {
                    row.descricao_ml_status = 'nao_encontrado';
                }
                skuRenderizarTabela();
                if (skuStatusEl) {
                    const temDescricao = resultado && String(resultado.descricao || '').trim();
                    skuStatusEl.textContent = temDescricao
                        ? `Descricao do SKU ${sku} carregada da API do Mercado Livre.`
                        : `Nenhuma descricao encontrada para o SKU ${sku}.`;
                }
            } catch (err) {
                row.descricao_ml_status = 'erro';
                row.descricao_ml_erro = err && err.message ? err.message : String(err);
                skuRenderizarTabela();
                if (skuStatusEl) skuStatusEl.textContent = `Erro ao buscar descricao do SKU ${sku}: ${row.descricao_ml_erro}`;
            } finally {
                if (botao) botao.disabled = false;
                skuAgendarBuscaDescricoesAutomaticas(500);
            }
        }

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
                renderizarFavoritosSkuSidebar();
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
            renderizarFavoritosSkuSidebar();
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
                renderizarFavoritosSkuSidebar();
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
                    renderizarFavoritosSkuSidebar();
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

        function skuFiltrarDados() {
            const termo = String(skuFiltroEl && skuFiltroEl.value || '').trim().toLowerCase();
            let lista = Array.isArray(skuDados) ? [...skuDados] : [];
            const lojasPermitidas = new Set((skuLojasDisponiveis || [])
                .map(loja => skuNormalizarLoja((loja && loja.nome) || loja))
                .filter(Boolean));
            if (lojasPermitidas.size) {
                lista = lista.filter(row => lojasPermitidas.has(skuNormalizarLoja(skuObterLoja(row))));
            }
            if (skuLojaSelecionada && !favoritosEhTodasLojas(skuLojaSelecionada)) {
                const lojaSelecionadaNorm = skuNormalizarLoja(skuLojaSelecionada);
                lista = lista.filter(row => skuNormalizarLoja(skuObterLoja(row)) === lojaSelecionadaNorm);
            }
            if (!skuMostrarOcultos) {
                lista = lista.filter(row => !skuEstaOculto(row));
            }
            if (termo) {
                lista = lista.filter(row => {
                    const alvo = [
                        skuObterSku(row),
                        skuObterProduto(row),
                        skuObterLoja(row),
                        skuObterPesquisa(row, 1),
                        skuObterPesquisa(row, 2),
                        skuObterPesquisa(row, 3),
                        row.descricao_ml || ''
                    ].join(' ').toLowerCase();
                    return alvo.includes(termo);
                });
            }
            return lista.sort((a, b) => {
                const lojaCmp = skuObterLoja(a).localeCompare(skuObterLoja(b), 'pt-BR', { numeric: true, sensitivity: 'base' });
                if (lojaCmp) return lojaCmp;
                return skuObterSku(a).localeCompare(skuObterSku(b), 'pt-BR', { numeric: true, sensitivity: 'base' });
            });
        }

        function skuCriarCelulaTexto(valor) {
            const td = document.createElement('td');
            td.textContent = valor;
            return td;
        }

        function skuCriarCelulaSku(row) {
            const td = document.createElement('td');
            td.className = 'sku-code-cell';
            const wrap = document.createElement('div');
            wrap.className = 'sku-code-wrap';

            const codigo = document.createElement('span');
            codigo.className = 'sku-code-text';
            codigo.textContent = skuObterSku(row);

            wrap.appendChild(codigo);
            wrap.appendChild(skuCriarBotaoOcultar(row));
            td.appendChild(wrap);
            return td;
        }

        async function skuSalvarPesquisasCadastro(row, inputEl) {
            const sku = skuObterSku(row);
            if (!sku) return;
            if (inputEl && skuPesquisaSaveTimers.has(inputEl)) {
                clearTimeout(skuPesquisaSaveTimers.get(inputEl));
                skuPesquisaSaveTimers.delete(inputEl);
            }
            inputEl.classList.remove('saved', 'error');
            inputEl.classList.add('saving');
            const lojaPesquisa = skuObterLoja(row) || skuLojaSelecionada || mlSkuLojaSelecionada || '';
            if (skuStatusEl) skuStatusEl.textContent = `Salvando pesquisas do SKU ${sku} para todas as contas...`;
            try {
                const response = await fetch('/api/favoritos/skus/pesquisas', {
                    method: 'PUT',
                    headers: headersJsonAutenticado(),
                    body: JSON.stringify({
                        sku,
                        produto: skuObterProduto(row),
                        loja: lojaPesquisa,
                        pesquisa_1: skuObterPesquisa(row, 1),
                        pesquisa_2: skuObterPesquisa(row, 2),
                        pesquisa_3: skuObterPesquisa(row, 3)
                    })
                });
                if (!response.ok) {
                    let detalhe = `HTTP ${response.status}`;
                    try {
                        const data = await response.json();
                        detalhe = data.detail || detalhe;
                    } catch (_err) {}
                    throw new Error(detalhe);
                }
                const data = await response.json();
                row.pesquisa_1 = data.pesquisa_1 || '';
                row.pesquisa_2 = data.pesquisa_2 || '';
                row.pesquisa_3 = data.pesquisa_3 || '';
                aplicarPesquisasGlobaisSkuLocal(data.sku || sku, data);
                if (inputEl) inputEl.dataset.valorSalvo = inputEl.value.trim();
                inputEl.classList.add('saved');
                if (skuStatusEl) skuStatusEl.textContent = `Pesquisas do SKU ${sku} salvas para todas as contas com esse SKU.`;
                setTimeout(() => inputEl.classList.remove('saved'), 1200);
                return data;
            } catch (err) {
                inputEl.classList.add('error');
                if (skuStatusEl) skuStatusEl.textContent = `Erro ao salvar pesquisas do SKU ${sku}: ${err && err.message ? err.message : err}`;
                return null;
            } finally {
                inputEl.classList.remove('saving');
            }
        }

        function skuAgendarSalvarPesquisasCadastro(row, inputEl, campo, delay = 900) {
            if (!row || !inputEl) return;
            const valorAtual = inputEl.value.trim();
            row[campo] = valorAtual;
            if (valorAtual === String(inputEl.dataset.valorSalvo || '')) {
                if (skuPesquisaSaveTimers.has(inputEl)) {
                    clearTimeout(skuPesquisaSaveTimers.get(inputEl));
                    skuPesquisaSaveTimers.delete(inputEl);
                }
                return;
            }
            if (skuPesquisaSaveTimers.has(inputEl)) {
                clearTimeout(skuPesquisaSaveTimers.get(inputEl));
            }
            const executar = () => {
                skuPesquisaSaveTimers.delete(inputEl);
                skuSalvarPesquisasCadastro(row, inputEl);
            };
            if (delay <= 0) {
                executar();
            } else {
                skuPesquisaSaveTimers.set(inputEl, setTimeout(executar, delay));
            }
        }

        function skuCriarCelulaPesquisa(row, campo, numero) {
            const td = document.createElement('td');
            const input = document.createElement('input');
            input.type = 'text';
            input.className = 'sku-pesquisa-input';
            input.value = skuObterPesquisa(row, numero);
            input.placeholder = `Pesquisa ${numero}`;
            input.dataset.valorOriginal = input.value;
            input.dataset.valorSalvo = input.value;
            input.addEventListener('keydown', (event) => {
                if (event.key === 'Enter') {
                    event.preventDefault();
                    input.value = input.value.trim();
                    skuAgendarSalvarPesquisasCadastro(row, input, campo, 0);
                    input.dataset.skipBlurSave = '1';
                    input.blur();
                }
            });
            input.addEventListener('input', () => {
                skuAgendarSalvarPesquisasCadastro(row, input, campo, 900);
            });
            input.addEventListener('blur', () => {
                if (input.dataset.skipBlurSave === '1') {
                    delete input.dataset.skipBlurSave;
                    return;
                }
                input.value = input.value.trim();
                skuAgendarSalvarPesquisasCadastro(row, input, campo, 0);
            });
            td.appendChild(input);
            return td;
        }

        function skuCriarCelulaDescricao(row) {
            const td = document.createElement('td');
            td.className = 'sku-descricao-cell';

            const meta = document.createElement('span');
            meta.className = 'sku-descricao-meta';
            meta.textContent = [
                row.descricao_ml_item_id ? `Anúncio: ${row.descricao_ml_item_id}` : '',
                row.descricao_ml_loja ? `Loja: ${row.descricao_ml_loja}` : ''
            ].filter(Boolean).join(' | ');

            const preview = document.createElement('div');
            preview.className = 'sku-descricao-preview';
            if (row.descricao_ml_status === 'loading') {
                preview.textContent = 'Buscando descricao...';
            } else if (row.descricao_ml_erro) {
                preview.classList.add('error');
                preview.textContent = row.descricao_ml_erro;
            } else if (row.descricao_ml) {
                preview.textContent = skuResumoDescricao(row.descricao_ml);
            } else if (row.descricao_ml_status === 'sem_descricao') {
                preview.textContent = 'Anúncio encontrado sem descrição.';
            } else if (row.descricao_ml_status === 'nao_encontrado') {
                preview.textContent = 'Nenhum anúncio ativo encontrado para este SKU.';
            } else if (row.fonte === 'mercadolivre_api') {
                const ids = Array.isArray(row.item_ids) ? row.item_ids.filter(Boolean) : [];
                const total = Number(row.total_anuncios || ids.length || 0);
                preview.textContent = total
                    ? `Dados da API do Mercado Livre: ${total} anuncio(s) ativo(s). ${ids.slice(0, 3).join(', ')}${ids.length > 3 ? '...' : ''}`
                    : 'Dados carregados da API do Mercado Livre.';
            } else {
                preview.textContent = 'Clique em Buscar descricao para consultar o Mercado Livre.';
            }

            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'sku-descricao-btn';
            btn.textContent = row.descricao_ml ? 'Atualizar descricao' : 'Buscar descricao';
            btn.disabled = row.descricao_ml_status === 'loading';
            btn.addEventListener('click', () => skuBuscarDescricaoManual(row, btn));

            td.appendChild(meta);
            td.appendChild(preview);
            td.appendChild(btn);
            return td;
        }

        function skuCriarCelulaIa(row) {
            const td = document.createElement('td');
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'sku-ia-btn';
            btn.textContent = 'IA';
            btn.title = 'Preencher Pesquisa 1, 2 e 3 deste SKU com IA';
            btn.addEventListener('click', () => {
                skuGerarPesquisasComIa({
                    skus: [skuObterSku(row)],
                    sobrescrever: true,
                    botao: btn
                });
            });
            td.appendChild(btn);
            return td;
        }

        async function skuSalvarOcultosServidor() {
            const response = await fetch('/api/favoritos/skus/ocultos', {
                method: 'PUT',
                headers: headersJsonAutenticado(),
                body: JSON.stringify({
                    skus_ocultos: Array.from(skuSkusOcultos)
                })
            });
            if (!response.ok) {
                let detalhe = `HTTP ${response.status}`;
                try {
                    const data = await response.json();
                    detalhe = data.detail || detalhe;
                } catch (_err) {}
                throw new Error(detalhe);
            }
            const data = await response.json();
            skuSkusOcultos = new Set((data.skus_ocultos || []).map(skuChaveOculto).filter(Boolean));
            return data;
        }

        async function skuAlternarOculto(row, botao) {
            const sku = skuChaveOculto(skuObterSku(row));
            if (!sku) return;
            const estavaOculto = skuSkusOcultos.has(sku);
            if (estavaOculto) {
                skuSkusOcultos.delete(sku);
            } else {
                skuSkusOcultos.add(sku);
            }
            if (botao) botao.disabled = true;
            if (skuStatusEl) {
                skuStatusEl.textContent = estavaOculto
                    ? `Reexibindo SKU ${sku}...`
                    : `Ocultando SKU ${sku}...`;
            }
            try {
                await skuSalvarOcultosServidor();
                skuRenderizarTabela();
                if (skuStatusEl) {
                    skuStatusEl.textContent = estavaOculto
                        ? `SKU ${sku} reexibido.`
                        : `SKU ${sku} ocultado para este usuario.`;
                }
            } catch (err) {
                if (estavaOculto) {
                    skuSkusOcultos.add(sku);
                } else {
                    skuSkusOcultos.delete(sku);
                }
                skuRenderizarTabela();
                if (skuStatusEl) {
                    skuStatusEl.textContent = `Erro ao salvar ocultacao do SKU ${sku}: ${err && err.message ? err.message : err}`;
                }
            } finally {
                if (botao) botao.disabled = false;
            }
        }

        function skuCriarBotaoOcultar(row) {
            const oculto = skuEstaOculto(row);
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = `sku-hide-btn${oculto ? ' is-hidden' : ''}`;
            btn.textContent = oculto ? 'Reexibir' : 'Ocultar';
            btn.title = oculto ? 'Voltar a mostrar este SKU' : 'Ocultar este SKU da aba SKU';
            btn.addEventListener('click', () => skuAlternarOculto(row, btn));
            return btn;
        }

        function skuContarOcultosLojaAtual() {
            return (skuDados || []).filter(row => {
                if (!skuEstaOculto(row)) return false;
                if (!skuLojaSelecionada || favoritosEhTodasLojas(skuLojaSelecionada)) return true;
                return skuNormalizarLoja(skuObterLoja(row)) === skuNormalizarLoja(skuLojaSelecionada);
            }).length;
        }

        function skuAtualizarBotaoOcultos() {
            if (!btnSkuToggleOcultos) return;
            const totalOcultos = skuContarOcultosLojaAtual();
            btnSkuToggleOcultos.textContent = skuMostrarOcultos
                ? 'Esconder ocultos'
                : `Mostrar ocultos${totalOcultos ? ` (${totalOcultos})` : ''}`;
            btnSkuToggleOcultos.disabled = totalOcultos === 0 && !skuMostrarOcultos;
        }

        function skuRenderizarPaginacao(totalItens) {
            if (!skuPaginationEl) return;
            const total = Number(totalItens || 0);
            const totalPaginas = Math.max(1, Math.ceil(total / SKU_API_PAGE_SIZE));
            skuPaginaAtual = Math.min(Math.max(1, skuPaginaAtual), totalPaginas);
            skuPaginationEl.innerHTML = '';
            skuPaginationEl.classList.toggle('hidden', total <= SKU_API_PAGE_SIZE);
            if (total <= SKU_API_PAGE_SIZE) return;

            const anterior = document.createElement('button');
            anterior.type = 'button';
            anterior.textContent = 'Anterior';
            anterior.disabled = skuPaginaAtual <= 1;
            anterior.addEventListener('click', () => {
                if (skuPaginaAtual <= 1) return;
                skuPaginaAtual -= 1;
                skuRenderizarTabela();
            });

            const info = document.createElement('span');
            info.textContent = `Pagina ${skuPaginaAtual} de ${totalPaginas} | 20 anuncios por pagina`;

            const proxima = document.createElement('button');
            proxima.type = 'button';
            proxima.textContent = 'Proxima';
            proxima.disabled = skuPaginaAtual >= totalPaginas;
            proxima.addEventListener('click', () => {
                if (skuPaginaAtual >= totalPaginas) return;
                skuPaginaAtual += 1;
                skuRenderizarTabela();
            });

            skuPaginationEl.appendChild(anterior);
            skuPaginationEl.appendChild(info);
            skuPaginationEl.appendChild(proxima);
        }

        function skuRenderizarTabela() {
            if (!skuBodyEl || !skuTableWrapEl || !skuEmptyEl) return;
            const lista = skuFiltrarDados();
            const totalPaginas = Math.max(1, Math.ceil(lista.length / SKU_API_PAGE_SIZE));
            skuPaginaAtual = Math.min(Math.max(1, skuPaginaAtual), totalPaginas);
            const inicioPagina = (skuPaginaAtual - 1) * SKU_API_PAGE_SIZE;
            const listaPagina = lista.slice(inicioPagina, inicioPagina + SKU_API_PAGE_SIZE);
            skuBodyEl.innerHTML = '';
            const frag = document.createDocumentFragment();

            listaPagina.forEach(row => {
                const tr = document.createElement('tr');
                tr.appendChild(skuCriarCelulaSku(row));
                tr.appendChild(skuCriarCelulaTexto(skuObterProduto(row)));
                tr.appendChild(skuCriarCelulaDescricao(row));
                tr.appendChild(skuCriarCelulaTexto(skuObterLoja(row)));
                tr.appendChild(skuCriarCelulaTexto(skuFormatarNumero(skuObterSaldoLoja(row))));
                tr.appendChild(skuCriarCelulaPesquisa(row, 'pesquisa_1', 1));
                tr.appendChild(skuCriarCelulaPesquisa(row, 'pesquisa_2', 2));
                tr.appendChild(skuCriarCelulaPesquisa(row, 'pesquisa_3', 3));
                tr.appendChild(skuCriarCelulaIa(row));
                if (skuEstaOculto(row)) tr.classList.add('sku-row-hidden');
                frag.appendChild(tr);
            });

            skuBodyEl.appendChild(frag);
            skuEmptyEl.classList.toggle('hidden', lista.length > 0);
            skuTableWrapEl.classList.toggle('hidden', lista.length === 0);
            if (skuContadorEl) {
                const lojaTexto = favoritosNomeLojaExibicao(skuLojaSelecionada || 'loja selecionada').toLowerCase();
                const ocultos = skuContarOcultosLojaAtual();
                const inicio = lista.length ? inicioPagina + 1 : 0;
                const fim = Math.min(lista.length, inicioPagina + listaPagina.length);
                skuContadorEl.textContent = `${inicio}-${fim} de ${lista.length} SKU(s) da API em ${lojaTexto}${ocultos ? ` | ${ocultos} oculto(s)` : ''}`;
            }
            skuRenderizarPaginacao(lista.length);
            skuAtualizarBotaoOcultos();
        }

        async function carregarSkuFavoritos(loja = '', opcoes = {}) {
            if (!skuBodyEl) return;
            if (opcoes && opcoes.type && opcoes.target) opcoes = {};
            const forcarAtualizacao = !!(opcoes && opcoes.atualizar);
            const preferida = loja || skuLojaSelecionada || mlSkuLojaSelecionada || mlSkuCarregarPreferenciaLoja() || skuCarregarPreferenciaLoja();
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
            if (skuStatusEl) {
                skuStatusEl.textContent = forcarAtualizacao
                    ? (carregarTodas
                        ? 'Atualizando SKUs e anuncios de todas as contas no Mercado Livre...'
                        : `Atualizando SKUs e anuncios da loja ${preferida} no Mercado Livre...`)
                    : (carregarTodas
                        ? 'Carregando SKUs salvos de todas as contas...'
                        : `Carregando SKUs salvos da loja ${preferida}...`);
            }
            const runId = ++mlSkuCarregamentoRunId;
            favoritosDefinirCarregamentoSkus(alvoCache, true, chaveCache);
            const promessa = (async () => {
            try {
                const params = new URLSearchParams();
                if (carregarTodas) {
                    params.set('todas_lojas', '1');
                } else {
                    params.set('loja', preferida);
                }
                if (forcarAtualizacao) params.set('atualizar', '1');
                const [response, ocultosResponse] = await Promise.all([
                    fetch(`/api/favoritos/ml/skus-anuncios${params.toString() ? `?${params.toString()}` : ''}`, {
                        headers: obterAuthHeaders(),
                        cache: 'no-store'
                    }),
                    fetch('/api/favoritos/skus/ocultos', {
                        headers: obterAuthHeaders(),
                        cache: 'no-store'
                    })
                ]);
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const data = await response.json();
                if (runId !== mlSkuCarregamentoRunId || !favoritosRespostaSkusConfereComAlvo(data, chaveCache, preferida || alvoCache)) {
                    return;
                }
                if (ocultosResponse.ok) {
                    const ocultosData = await ocultosResponse.json();
                    skuSkusOcultos = new Set((ocultosData.skus_ocultos || []).map(skuChaveOculto).filter(Boolean));
                } else {
                    skuSkusOcultos = new Set();
                }
                skuLojasDisponiveis = Array.isArray(data.lojas) ? data.lojas : [];
                mlSkuLojasDisponiveis = skuLojasDisponiveis;
                mlSkuLojaSelecionada = favoritosResolverLojaRespostaSkus(data, preferida || alvoCache);
                skuLojaSelecionada = mlSkuLojaSelecionada;
                mlSkusAnunciosLojaAtual = favoritosClonarSkusComLoja(data.skus, mlSkuLojaSelecionada);
                favoritosSkusTodasLojasCarregados = !!data.todas_lojas;
                favoritosTotalAnunciosLojaAtual = Number(data.total_anuncios || 0);
                favoritosWarningLojaAtual = data.warning || '';
                favoritosMlSkuCacheMetaAtual = favoritosCacheMlMeta(data);
                skuCancelarBuscaDescricoesAutomaticas();
                skuDados = mlSkusAnunciosLojaAtual
                    .map(item => skuNormalizarLinhaApiMercadoLivre(item, mlSkuLojaSelecionada))
                    .filter(item => skuObterSku(item));
                skuPaginaAtual = 1;
                favoritosSalvarCacheSkusAtual();
                if (!mlSkusAnunciosLojaAtual.length && !skuDados.length && chaveCache) {
                    mlSkuDadosPorLojaCache.delete(chaveCache);
                }
                skuRenderizarCardsLojas();
                skuRenderizarTabela();
                favoritosAtualizarCardLojaCabecalho();
                mlSkuRenderizarCardsLojas();
                renderizarSkuSidebarMercadoLivre();
                renderizarFavoritosSkuSidebar();
                renderizarHistoricoSkuSidebar();
                atualizarEstadoSidebarRanking();
                if (skuStatusEl) {
                    skuStatusEl.textContent = skuDados.length
                        ? data.todas_lojas
                            ? `Total de ${skuDados.length} SKU(s) carregado(s) de todas as contas pela API do Mercado Livre (${data.total_anuncios || 0} anuncio(s)).`
                            : `Total de ${skuDados.length} SKU(s) carregado(s) da loja ${mlSkuLojaSelecionada} pela API do Mercado Livre (${data.total_anuncios || 0} anuncio(s)).`
                        : data.todas_lojas
                            ? 'Nenhum SKU encontrado na API do Mercado Livre para as contas integradas.'
                            : `Nenhum SKU encontrado na API do Mercado Livre para a loja ${mlSkuLojaSelecionada}.`;
                    const statusCache = favoritosMensagemCacheMl(data);
                    if (statusCache) skuStatusEl.textContent = `${skuStatusEl.textContent} ${statusCache}`;
                }
                if (skuDados.length) skuAgendarBuscaDescricoesAutomaticas(500);
            } catch (err) {
                favoritosWarningLojaAtual = `Erro ao carregar SKUs: ${err && err.message ? err.message : err}`;
                renderizarSkuSidebarMercadoLivre();
                renderizarFavoritosSkuSidebar();
                renderizarHistoricoSkuSidebar();
                atualizarEstadoSidebarRanking();
                if (skuStatusEl) skuStatusEl.textContent = favoritosWarningLojaAtual;
                if (mlSkuStatusEl) mlSkuStatusEl.textContent = favoritosWarningLojaAtual;
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

        async function atualizarSkusMercadoLivreAgora() {
            if (mlSkuCarregamentoEmAndamento) return;
            const lojaAtual = mlSkuLojaSelecionada || skuLojaSelecionada || mlSkuCarregarPreferenciaLoja() || skuCarregarPreferenciaLoja() || FAVORITOS_TODAS_LOJAS;
            favoritosDefinirBotoesAtualizarSkuMl(true);
            try {
                await carregarSkuFavoritos(lojaAtual, { atualizar: true });
                const skuAberto = String(favMlSkuSelecionado || '').trim();
                if (skuAberto && typeof carregarFavoritosAnunciosSku === 'function') {
                    await carregarFavoritosAnunciosSku(skuAberto, favMlLojaSelecionada || mlSkuLojaSelecionada || skuLojaSelecionada || lojaAtual, {
                        atualizar: true,
                        manterRankingSelecionado: true
                    });
                }
            } catch (err) {
                const mensagem = err && err.message ? err.message : String(err || 'erro desconhecido');
                if (skuStatusEl) skuStatusEl.textContent = `Erro ao atualizar SKUs do Mercado Livre: ${mensagem}`;
                if (mlSkuStatusEl) mlSkuStatusEl.textContent = `Erro ao atualizar SKUs do Mercado Livre: ${mensagem}`;
            } finally {
                favoritosDefinirBotoesAtualizarSkuMl(false);
            }
        }

        favoritosBotoesAtualizarSkuMl().forEach(botao => {
            botao.addEventListener('click', atualizarSkusMercadoLivreAgora);
        });

        if (skuFiltroEl) {
            skuFiltroEl.addEventListener('input', () => {
                skuPaginaAtual = 1;
                skuRenderizarTabela();
            });
        }

        if (btnSkuToggleOcultos) {
            btnSkuToggleOcultos.addEventListener('click', () => {
                skuMostrarOcultos = !skuMostrarOcultos;
                skuPaginaAtual = 1;
                skuRenderizarTabela();
            });
        }



        function garantirSidebarSkuUnico() {
            const tabsEl = document.querySelector('.tabs');
            const containerEl = document.querySelector('.container');
            if (mlSkuSidebarSectionEl && tabsEl && containerEl && mlSkuSidebarSectionEl.parentElement !== containerEl) {
                tabsEl.insertAdjacentElement('afterend', mlSkuSidebarSectionEl);
            }
            document.querySelectorAll('.favoritos-ml-sidebar').forEach(sidebar => {
                sidebar.remove();
            });
        }



        async function buscar() {
            const termo = termoEl.value.trim();
            if (!termo) {
                alert('Informe um termo ou link para pesquisar.');
                return;
            }

            if (!/^https?:\/\//i.test(termo) && hasInternalBrowserApi) {
                mlSearchTermInput.value = termo;
                mudarAba('navegador');
                await pesquisarNoMercadoLivreNoPrograma();
                return;
            }

            statusEl.textContent = 'Buscando. Aguarde...';
            resultadosEl.classList.add('hidden');
            topBody.innerHTML = '';
            allBody.innerHTML = '';
            linkProdutoInfo.innerHTML = '';
            linkProdutoUrls.innerHTML = '';

            try {
                const response = await fetch('/api/favoritos/pesquisar', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ termo: termo })
                });

                if (!response.ok) {
                    let detail = 'Erro ao buscar.';
                    try {
                        const errJson = await response.json();
                        detail = errJson.detail || detail;
                    } catch (e) {}
                    throw new Error(detail);
                }

                const data = await response.json();

                if (data.tipo === 'link_produto') {
                    displayLinkProduto(data);
                } else if (data.tipo === 'busca_termo') {
                    displayBuscaTermos(data);
                } else {
                    throw new Error('Tipo de resposta desconhecido: ' + data.tipo);
                }

                resultadosEl.classList.remove('hidden');
            } catch (err) {
                statusEl.textContent = '';
                alert(err.message || 'Erro inesperado.');
            }
        }

        function displayLinkProduto(data) {
            statusEl.textContent = 'Produto encontrado. Selecione uma estratégia de busca:';

            buscaTermoWrap.classList.add('hidden');
            linkProdutoWrap.classList.remove('hidden');

            const dados = data.dados || {};
            if (dados.titulo) {
                const tr = document.createElement('tr');
                tr.innerHTML = `<td><strong>Título:</strong></td><td>${dados.titulo}</td>`;
                linkProdutoInfo.appendChild(tr);
            }
            if (dados.codigo) {
                const tr = document.createElement('tr');
                tr.innerHTML = `<td><strong>Código OEM:</strong></td><td>${dados.codigo}</td>`;
                linkProdutoInfo.appendChild(tr);
            }
            if (dados.veiculo) {
                const tr = document.createElement('tr');
                tr.innerHTML = `<td><strong>Veículo:</strong></td><td>${dados.veiculo}</td>`;
                linkProdutoInfo.appendChild(tr);
            }
            if (dados.anos && dados.anos.length > 0) {
                const tr = document.createElement('tr');
                tr.innerHTML = `<td><strong>Anos:</strong></td><td>${dados.anos.join(', ')}</td>`;
                linkProdutoInfo.appendChild(tr);
            }

            const urls = data.urls_busca || [];
            linkProdutoUrls.innerHTML = '';
            urls.forEach(item => {
                const button = document.createElement('a');
                button.href = item.url;
                button.target = '_blank';
                button.rel = 'noopener';
                button.className = 'search-button';
                button.innerHTML = `<strong>${item.label}</strong><span class="tipo">${item.tipo}</span>`;
                linkProdutoUrls.appendChild(button);
            });
        }

        function displayBuscaTermos(data) {
            statusEl.textContent = `Total de anúncios analisados: ${data.total}`;

            linkProdutoWrap.classList.add('hidden');
            buscaTermoWrap.classList.remove('hidden');

            renderTable(topBody, data.top || []);
            renderTable(allBody, data.resultados || []);

            topEmpty.classList.toggle('hidden', (data.top || []).length > 0);
            topWrap.classList.toggle('hidden', (data.top || []).length === 0);
            allEmpty.classList.toggle('hidden', (data.resultados || []).length > 0);
            allWrap.classList.toggle('hidden', (data.resultados || []).length === 0);
        }

        function renderTable(tbody, rows) {
            tbody.innerHTML = '';
            rows.forEach(row => {
                const tr = document.createElement('tr');

                const tdTitulo = document.createElement('td');
                tdTitulo.textContent = row.titulo || '';

                const tdPreco = document.createElement('td');
                tdPreco.textContent = row.preco || '';

                const tdVendas = document.createElement('td');
                tdVendas.textContent = row.vendas !== null && row.vendas !== undefined ? row.vendas : '';

                const tdMeses = document.createElement('td');
                tdMeses.textContent = row.meses !== null && row.meses !== undefined ? row.meses : '';

                const tdMedia = document.createElement('td');
                tdMedia.textContent = row.media_vendas !== null && row.media_vendas !== undefined ? row.media_vendas : '';

                const tdLink = document.createElement('td');
                if (row.url) {
                    const actions = document.createElement('span');
                    actions.className = 'link-actions';

                    const a = document.createElement('a');
                    a.className = 'link';
                    a.href = row.url;
                    a.target = '_blank';
                    a.rel = 'noopener';
                    a.textContent = 'Abrir';
                    a.addEventListener('click', (event) => abrirAnuncioComAvantPro(row.url, event));

                    const copyBtn = document.createElement('button');
                    copyBtn.type = 'button';
                    copyBtn.className = 'copy-link-btn';
                    copyBtn.textContent = 'Copiar';
                    copyBtn.addEventListener('click', () => copiarLinkAnuncio(row.url, copyBtn));

                    actions.appendChild(a);
                    actions.appendChild(copyBtn);
                    tdLink.appendChild(actions);
                }

                tr.appendChild(tdTitulo);
                tr.appendChild(tdPreco);
                tr.appendChild(tdVendas);
                tr.appendChild(tdMeses);
                tr.appendChild(tdMedia);
                tr.appendChild(tdLink);
                tbody.appendChild(tr);
            });
        }

        function mudarAba(nomeAba, evt) {
            document.querySelectorAll('.aba-conteudo').forEach(aba => aba.classList.remove('active'));
            document.querySelectorAll('.tab-button').forEach(btn => btn.classList.remove('active'));
            document.body.classList.toggle('favoritos-tab-favoritos', nomeAba === 'favoritos');
            document.body.classList.toggle('favoritos-tab-historico', nomeAba === 'historico');

            const abaElement = document.getElementById('aba-' + nomeAba);
            if (abaElement) {
                abaElement.classList.add('active');
            }
            if (evt && evt.target) {
                evt.target.classList.add('active');
            } else {
                const botaoAba = Array.from(document.querySelectorAll('.tab-button'))
                    .find(btn => String(btn.getAttribute('onclick') || '').includes(`'${nomeAba}'`));
                if (botaoAba) botaoAba.classList.add('active');
            }

            atualizarEstadoSidebarRanking();
            renderizarSkuSidebarMercadoLivre();
            if (nomeAba === 'navegador') {
                cancelarAberturaMercadoLivreAoEntrar();
            }
            if (nomeAba === 'favoritos') {
                prepararAbaFavoritosMl();
            }
            if (nomeAba === 'historico') {
                prepararAbaHistoricoFavoritos();
            }
            if (nomeAba === 'planilhas') {
                prepararAbaPlanilhasFavoritos();
            }
            if (nomeAba === 'links-alinhados') {
                renderizarLinksAlinhadosFavoritos();
            }
            if (nomeAba === 'vendedores') {
                renderizarVendedoresIgnoradosRanking();
            }
            if (nomeAba === 'anuncios-ignorados') {
                renderizarAnunciosIgnoradosSku();
            }
            if (nomeAba !== 'navegador') {
                ocultarNavegadorMlShellDefinitivo();
            }
            // A pesquisa abre/carrega o quadro interno sob demanda.
        }
