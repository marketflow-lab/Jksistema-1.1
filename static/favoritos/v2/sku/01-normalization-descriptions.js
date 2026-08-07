(function (global) {
        'use strict';

        const feature = global.FavoritosV2 && global.FavoritosV2.sku;
        if (!feature || !feature.__runtimeInitialized) {
            throw new Error('Runtime do SKU nao inicializado.');
        }
        if (feature.components.has('normalizationDescriptions')) return;

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


        Object.assign(feature.internal, {
            skuTexto,
            skuNumero,
            skuFormatarNumero,
            skuObterSku,
            skuChaveOculto,
            skuEstaOculto,
            skuObterProduto,
            skuObterLoja,
            skuObterSaldoLoja,
            skuObterSaldoFull,
            skuObterTotal,
            skuObterPesquisa,
            skuChaveSku,
            aplicarPesquisasGlobaisSkuLocal,
            skuChavePreferenciaLoja,
            skuCarregarPreferenciaLoja,
            skuSalvarPreferenciaLoja,
            skuResumoDescricao,
            skuChaveDescricao,
            skuItemIdsDescricao,
            skuItemIdsPorSkuDescricao,
            skuAplicarDescricaoResultado,
            skuDescricaoCacheKey,
            skuDescricaoCacheGet,
            skuDescricaoCacheSet,
            skuCancelarBuscaDescricoesAutomaticas,
            skuNormalizarLinhaApiMercadoLivre,
            skuAtualizarDadosComApiMercadoLivre,
            skuAgendarBuscaDescricoesAutomaticas,
            skuBuscarDescricoesAutomaticas,
            skuBuscarDescricaoManual
        });
        feature.components.add('normalizationDescriptions');
    })(window);
