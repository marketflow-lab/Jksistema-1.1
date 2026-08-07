(function (global) {
        'use strict';

        const feature = global.FavoritosV2 && global.FavoritosV2.sku;
        if (!feature || !feature.__runtimeInitialized) {
            throw new Error('Runtime do SKU nao inicializado.');
        }
        if (feature.components.has('catalogTable')) return;

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
                window.FavoritosV2.execution.publicApi.renderizarFavoritosSkuSidebar();
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
                window.FavoritosV2.execution.publicApi.renderizarFavoritosSkuSidebar();
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




        Object.assign(feature.internal, {
            skuFiltrarDados,
            skuCriarCelulaTexto,
            skuCriarCelulaSku,
            skuSalvarPesquisasCadastro,
            skuAgendarSalvarPesquisasCadastro,
            skuCriarCelulaPesquisa,
            skuCriarCelulaDescricao,
            skuCriarCelulaIa,
            skuSalvarOcultosServidor,
            skuAlternarOculto,
            skuCriarBotaoOcultar,
            skuContarOcultosLojaAtual,
            skuAtualizarBotaoOcultos,
            skuRenderizarPaginacao,
            skuRenderizarTabela,
            carregarSkuFavoritos,
            atualizarSkusMercadoLivreAgora
        });
        feature.components.add('catalogTable');
    })(window);
