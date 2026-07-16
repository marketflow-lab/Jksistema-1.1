        function atualizarBotaoRecarregarAnunciosSkuFavoritos(visible = false, carregando = false) {
            if (!favMlAnunciosRecarregarBtnEl) return;
            favMlAnunciosRecarregarBtnEl.classList.toggle('hidden', !visible);
            favMlAnunciosRecarregarBtnEl.disabled = !!carregando;
            favMlAnunciosRecarregarBtnEl.textContent = carregando ? 'Recarregando...' : 'Recarregar anuncios do SKU';
        }

        function renderizarFavoritosAnunciosMl(anuncios, sku, carregando = false) {
            if (!favMlAnunciosBodyEl || !favMlAnunciosEmptyEl) return;
            const lista = Array.isArray(anuncios) ? anuncios : [];
            favMlAnunciosBodyEl.innerHTML = '';
            favMlSimulacoesSkuAtual = [];

            if (carregando) {
                atualizarBotaoRecarregarAnunciosSkuFavoritos(false, true);
                favMlAnunciosEmptyEl.textContent = `Carregando anuncios do SKU ${sku}...`;
                favMlAnunciosEmptyEl.classList.remove('hidden');
                atualizarPainelEfetivarFavoritos();
                atualizarTabelasFavoritosEditaveis();
                return;
            }

            if (skuChaveSku(sku) === 'avulso') {
                atualizarBotaoRecarregarAnunciosSkuFavoritos(false);
                favMlAnunciosEmptyEl.textContent = 'Ranqueamento avulso: veja a lista classificada no painel Ranking do SKU selecionado.';
                favMlAnunciosEmptyEl.classList.remove('hidden');
                atualizarPainelEfetivarFavoritos();
                atualizarTabelasFavoritosEditaveis();
                return;
            }

            if (!sku) {
                atualizarBotaoRecarregarAnunciosSkuFavoritos(false);
                favMlAnunciosEmptyEl.textContent = 'Selecione um SKU para carregar os anuncios do Mercado Livre.';
                favMlAnunciosEmptyEl.classList.remove('hidden');
                atualizarPainelEfetivarFavoritos();
                atualizarTabelasFavoritosEditaveis();
                return;
            }

            if (!lista.length) {
                atualizarBotaoRecarregarAnunciosSkuFavoritos(true);
                favMlAnunciosEmptyEl.textContent = `Nenhum anuncio do Mercado Livre encontrado para o SKU ${sku}.`;
                favMlAnunciosEmptyEl.classList.remove('hidden');
                atualizarPainelEfetivarFavoritos();
                atualizarTabelasFavoritosEditaveis();
                return;
            }

            atualizarBotaoRecarregarAnunciosSkuFavoritos(false);
            favMlAnunciosEmptyEl.classList.add('hidden');
            const anunciosRanking = obterRankingFavoritosParaSimulador(sku);
            const opcoesPromocaoSku = obterOpcoesPromocaoFavoritosSku(sku);
            const precosFinaisReservados = new Set();
            const precosCheiosReservados = new Set();
            lista.forEach((anuncio, index) => {
                const sim = calcularSimulacaoPrecoFavoritos(anuncio, anunciosRanking[index], opcoesPromocaoSku, {
                    precosFinaisReservados,
                    precosCheiosReservados
                });
                favMlSimulacoesSkuAtual.push({
                    anuncio,
                    ranking: anunciosRanking[index],
                    sim,
                    itemId: obterIdAnuncioFavoritos(anuncio)
                });
                const tr = document.createElement('tr');
                const tdOrdem = document.createElement('td');
                tdOrdem.className = 'ml-favoritos-ordem-cell';
                tdOrdem.textContent = `${index + 1}º`;
                tr.appendChild(tdOrdem);
                const tdAlterar = document.createElement('td');
                tdAlterar.className = 'ml-favoritos-alterar-cell';
                const labelAlterar = document.createElement('label');
                labelAlterar.className = 'ml-favoritos-alterar-check';
                labelAlterar.title = 'Desmarque para nao alterar este anuncio com base no ranking alinhado.';
                const inputAlterar = document.createElement('input');
                inputAlterar.type = 'checkbox';
                inputAlterar.checked = anuncioSelecionadoAlteracaoFavoritos(sku, anuncio);
                inputAlterar.setAttribute('aria-label', `Alterar anuncio ${obterIdAnuncioFavoritos(anuncio) || index + 1}`);
                tr.classList.toggle('is-favoritos-nao-alterar', !inputAlterar.checked);
                inputAlterar.addEventListener('change', () => {
                    definirAnuncioSelecionadoAlteracaoFavoritos(sku, anuncio, inputAlterar.checked);
                    tr.classList.toggle('is-favoritos-nao-alterar', !inputAlterar.checked);
                    atualizarPainelEfetivarFavoritos();
                });
                labelAlterar.appendChild(inputAlterar);
                tdAlterar.appendChild(labelAlterar);
                tr.appendChild(tdAlterar);
                tr.appendChild(criarCelulaFotoAnuncioFavoritos(anuncio));
                tr.appendChild(criarCelulaMlbLojaFavoritos(
                    anuncio.mlb || anuncio.id || '',
                    anuncio.loja || anuncio.loja_sync || anuncio.loja_conta || '',
                    anuncio.status || '',
                    obterTipoCompletoAnuncioFavoritos(anuncio)
                ));
                tr.appendChild(criarCelulaTextoFavoritos(anuncio.titulo || '', { long: true }));

                tr.appendChild(criarCelulaPrecoAnuncioFavoritos(anuncio, [
                    { rotulo: 'Estoque', valor: anuncio.estoque ?? '' },
                    { rotulo: 'Vendas', valor: anuncio.vendidos ?? '' }
                ]));
                tr.appendChild(criarCelulaSimuladorPrecoFavoritos(anuncio, anunciosRanking[index], opcoesPromocaoSku, sim));
                favMlAnunciosBodyEl.appendChild(tr);
            });
            atualizarPainelEfetivarFavoritos();
            atualizarTabelasFavoritosEditaveis();
        }

        if (favMlAnunciosRecarregarBtnEl) {
            favMlAnunciosRecarregarBtnEl.addEventListener('click', () => {
                const sku = String(favMlSkuSelecionado || '').trim();
                if (!sku) return;
                carregarFavoritosAnunciosSku(sku, favMlLojaSelecionada || mlSkuLojaSelecionada || skuLojaSelecionada || '', {
                    atualizar: true,
                    manterRankingSelecionado: true
                });
            });
        }

        function renderizarFavoritosHistoricoDisponivel(sku = '') {
            if (!favOutrosAnunciosBodyEl || !favOutrosAnunciosEmptyEl) return false;
            const skuSelecionado = String(sku || '').trim();
            const chaveSku = skuChaveSku(skuSelecionado);
            const limiteBusca = chaveSku ? Math.max(200, ML_FAVORITOS_HISTORICO_MAX * 20) : ML_FAVORITOS_HISTORICO_MAX;
            const historicoBase = chaveSku
                ? filtrarHistoricoFavoritosPorLojaAtual(lerHistoricoFavoritos(), skuSelecionado)
                : null;
            const recentes = montarUltimosFavoritosRankeados(limiteBusca, historicoBase)
                .filter(item => !chaveSku || skuChaveSku(item && item.sku) === chaveSku)
                .slice(0, ML_FAVORITOS_HISTORICO_MAX);
            if (!recentes.length) return false;

            favOutrosAnunciosEmptyEl.classList.add('hidden');
            if (favRankingDataEl) {
                favRankingDataEl.textContent = chaveSku
                    ? `Historico de favoritos do SKU ${skuSelecionado}. Clique em um item para abrir o ranking dessa execucao.`
                    : 'Historico de favoritos. Clique em um item para abrir o ranking dessa execucao.';
            }

            const tr = document.createElement('tr');
            tr.className = 'is-history-list-row';
            const td = document.createElement('td');
            td.colSpan = 5;
            const lista = document.createElement('div');
            lista.className = 'ml-favoritos-recentes-list';
            recentes.forEach(item => {
                lista.appendChild(criarBotaoFavoritoRecente(item, abrirFavoritoRecenteNaAbaFavoritos));
            });
            td.appendChild(lista);
            tr.appendChild(td);
            favOutrosAnunciosBodyEl.appendChild(tr);
            atualizarTabelasFavoritosEditaveis();
            return true;
        }

        function renderizarFavoritosUltimosRankingsDisponiveis() {
            return renderizarFavoritosHistoricoDisponivel('');
        }

        function renderizarFavoritosOutrosAnuncios(sku) {
            if (favOutrosAnunciosBodyEl) favOutrosAnunciosBodyEl.innerHTML = '';
            if (favRankingDataEl) favRankingDataEl.textContent = '';
            if (!favOutrosAnunciosEmptyEl) return;
            const skuSelecionado = String(sku || '').trim();
            atualizarBotaoIncluirAnuncioRankingFavoritos(false);
            if (!skuSelecionado) {
                if (renderizarFavoritosHistoricoDisponivel('')) return;
                favOutrosAnunciosEmptyEl.textContent = 'Nenhum historico de favoritos salvo ainda.';
                favOutrosAnunciosEmptyEl.classList.remove('hidden');
                atualizarTabelasFavoritosEditaveis();
                return;
            }

            const entradaSelecionada = String(favMlHistoricoExecucaoSelecionadaId || '').trim();
            if (!entradaSelecionada) {
                if (renderizarFavoritosHistoricoDisponivel(skuSelecionado)) return;
                favOutrosAnunciosEmptyEl.textContent = `Nenhum historico de favoritos salvo para o SKU ${skuSelecionado}. Use Fazer favoritos na aba Pagina de Pesquisa.`;
                favOutrosAnunciosEmptyEl.classList.remove('hidden');
                atualizarTabelasFavoritosEditaveis();
                return;
            }

            const resultadoRanking = obterGrupoRankingFavoritosSku(skuSelecionado);
            const grupo = resultadoRanking && resultadoRanking.grupo;
            if (!grupo) {
                favOutrosAnunciosEmptyEl.textContent = entradaSelecionada === FAV_ML_RANKING_ATUAL_ID
                    ? 'Nenhum ranking atual carregado para este SKU.'
                    : 'Esse item do historico nao foi localizado para este SKU.';
                favOutrosAnunciosEmptyEl.classList.remove('hidden');
                atualizarTabelasFavoritosEditaveis();
                return;
            }
            atualizarBotaoIncluirAnuncioRankingFavoritos(true);
            if (favRankingDataEl) {
                const dataRanking = formatarDataHistoricoFavoritos(resultadoRanking.data_iso || grupo.data_iso || grupo.data_ranking_iso);
                const lojaRanking = resultadoRanking.loja || grupo.loja || '';
                const usuarioRanking = obterUsuarioHistoricoFavoritos(resultadoRanking.entrada || resultadoRanking || grupo);
                favRankingDataEl.textContent = dataRanking
                    ? `${entradaSelecionada === FAV_ML_RANKING_ATUAL_ID ? 'Ranking atual feito em' : 'Ranking do historico feito em'} ${dataRanking}${lojaRanking ? ` | Loja: ${lojaRanking}` : ''}${usuarioRanking ? ` | Usuario: ${usuarioRanking}` : ''}`
                    : (entradaSelecionada === FAV_ML_RANKING_ATUAL_ID ? 'Ranking atual para este SKU.' : 'Ranking do historico selecionado para este SKU.');
            }
            if (grupo.erro) {
                favOutrosAnunciosEmptyEl.textContent = grupo.erro;
                favOutrosAnunciosEmptyEl.classList.remove('hidden');
                atualizarTabelasFavoritosEditaveis();
                return;
            }

            const anuncios = filtrarAnunciosIgnoradosRanking(grupo.anuncios, skuSelecionado);
            if (!anuncios.length) {
                const removidosIaVazio = criarBlocoRemovidosIaFavoritos(grupo);
                if (removidosIaVazio && favOutrosAnunciosBodyEl) {
                    favOutrosAnunciosEmptyEl.classList.add('hidden');
                    const trRemovidos = document.createElement('tr');
                    trRemovidos.className = 'is-history-list-row';
                    const tdRemovidos = document.createElement('td');
                    tdRemovidos.colSpan = 5;
                    tdRemovidos.appendChild(removidosIaVazio);
                    trRemovidos.appendChild(tdRemovidos);
                    favOutrosAnunciosBodyEl.appendChild(trRemovidos);
                    atualizarTabelasFavoritosEditaveis();
                    return;
                }
                favOutrosAnunciosEmptyEl.textContent = Array.isArray(grupo.anuncios) && grupo.anuncios.length
                    ? 'Todos os anuncios rankeados para este SKU estao na lista de ignorados.'
                    : 'Nenhum anuncio rankeado para este SKU.';
                favOutrosAnunciosEmptyEl.classList.remove('hidden');
                atualizarTabelasFavoritosEditaveis();
                return;
            }

            favOutrosAnunciosEmptyEl.classList.add('hidden');
            const rankingHistoricoEstatico = typeof grupoRankingHistoricoEstaticoFavoritos === 'function'
                && grupoRankingHistoricoEstaticoFavoritos(grupo, entradaSelecionada);
            if (!rankingHistoricoEstatico) {
                complementarTiposRankingFavoritos(skuSelecionado, anuncios);
            }
            anuncios.forEach((anuncio, index) => {
                const anuncioRender = aplicarMargemRankingFavoritos(rankingHistoricoEstatico ? { ...anuncio } : anuncio);
                const tr = document.createElement('tr');
                tr.appendChild(criarCelulaAcoesRankingFavoritos(skuSelecionado, anuncioRender, {
                    entradaId: entradaSelecionada,
                    primeiro: index === 0,
                    rank: index + 1,
                    ultimo: index === anuncios.length - 1
                }));
                tr.appendChild(criarCelulaFotoAnuncioFavoritos(anuncioRender));
                const valores = [
                    { valor: anuncioRender.titulo || '', long: true }
                ];
                const lojaVendedoraHistorico = obterNomeLojaVendedoraHistoricoFavoritos(anuncioRender);
                tr.appendChild(criarCelulaMlbLojaFavoritos(
                    anuncioRender.id || extrairItemIdAnuncio(anuncioRender.url) || '',
                    lojaVendedoraHistorico,
                    anuncioRender.status || '',
                    obterTipoCompletoAnuncioFavoritos(anuncioRender),
                    anuncioRender.vendedor || lojaVendedoraHistorico || ''
                ));
                tr.appendChild(criarCelulaMediaHistoricoFavoritos(anuncioRender));
                valores.forEach(item => {
                    tr.appendChild(criarCelulaTextoFavoritos(item.valor, item));
                });
                favOutrosAnunciosBodyEl.appendChild(tr);
            });
            const removidosIa = criarBlocoRemovidosIaFavoritos(grupo);
            if (removidosIa) {
                const trRemovidos = document.createElement('tr');
                trRemovidos.className = 'is-history-list-row';
                const tdRemovidos = document.createElement('td');
                tdRemovidos.colSpan = 5;
                tdRemovidos.appendChild(removidosIa);
                trRemovidos.appendChild(tdRemovidos);
                favOutrosAnunciosBodyEl.appendChild(trRemovidos);
            }
            atualizarTabelasFavoritosEditaveis();
        }

        function prepararAbaFavoritosMl() {
            renderizarFavoritosSkuSidebar();
            renderizarSkuSidebarMercadoLivre();
            renderizarFavoritosOutrosAnuncios(favMlSkuSelecionado);
            if (favMlSkuSelecionado) {
                renderizarFavoritosAnunciosMl(favMlAnunciosSkuAtual, favMlSkuSelecionado);
            } else {
                renderizarFavoritosAnunciosMl([], '');
                if (favMlStatusEl) {
                    favMlStatusEl.textContent = mlSkusAnunciosLojaAtual.length
                        ? 'Selecione um SKU na barra lateral para listar os anuncios.'
                        : 'Carregando SKUs dos anuncios ativos do Mercado Livre...';
                }
            }

            if (!mlSkusAnunciosLojaAtual.length && !mlSkuCarregamentoEmAndamento) {
                carregarSkuFavoritos(mlSkuLojaSelecionada || skuLojaSelecionada || '');
            }
        }

        function limparFavoritosSkuSelecionado() {
            favMlSkuSelecionado = '';
            favMlLojaSelecionada = '';
            favMlHistoricoExecucaoSelecionadaId = '';
            favMlAnunciosSkuAtual = [];
            favMlSimulacoesSkuAtual = [];
            atualizarBotaoRecarregarAnunciosSkuFavoritos(false, false);
            executarRenderFavoritosSeguro('sidebar favoritos', () => renderizarFavoritosSkuSidebar());
            executarRenderFavoritosSeguro('sidebar Mercado Livre', () => renderizarSkuSidebarMercadoLivre());
            executarRenderFavoritosSeguro('anuncios do SKU', () => renderizarFavoritosAnunciosMl([], ''));
            executarRenderFavoritosSeguro('historico do SKU', () => renderizarFavoritosOutrosAnuncios(''));
            if (favMlStatusEl) {
                favMlStatusEl.textContent = mlSkusAnunciosLojaAtual.length
                    ? 'Selecione um SKU na barra lateral para listar os anuncios.'
                    : 'Carregando SKUs dos anuncios ativos do Mercado Livre...';
            }
        }

        async function carregarFavoritosAnunciosSku(sku, loja = '', opcoes = {}) {
            const skuSelecionado = String(sku || '').trim();
            if (!skuSelecionado) return;
            const manterRankingSelecionado = !!(opcoes && opcoes.manterRankingSelecionado);
            // Nesta tela o preco precisa refletir o valor atual do anuncio no ML.
            // Cache pode ser usado para listas de SKU, mas nao para precificacao de alteracao.
            const forcarAtualizacao = true;
            const itemSidebar = (opcoes && opcoes.itemSidebar)
                || encontrarItemSkuSidebarMercadoLivre(skuSelecionado, loja || favMlLojaSelecionada || mlSkuLojaSelecionada || '');

            favMlSkuSelecionado = skuSelecionado;
            favMlLojaSelecionada = String(loja || '').trim();
            if (!manterRankingSelecionado) {
                favMlHistoricoExecucaoSelecionadaId = '';
            }
            favMlAnunciosSkuAtual = [];
            favMlSimulacoesSkuAtual = [];
            limparSelecaoAlteracaoFavoritosSku(skuSelecionado);
            executarRenderFavoritosSeguro('sidebar favoritos', () => renderizarFavoritosSkuSidebar());
            executarRenderFavoritosSeguro('sidebar Mercado Livre', () => renderizarSkuSidebarMercadoLivre());
            executarRenderFavoritosSeguro('anuncios do SKU', () => renderizarFavoritosAnunciosMl([], skuSelecionado, true));
            executarRenderFavoritosSeguro('historico do SKU', () => renderizarFavoritosOutrosAnuncios(skuSelecionado));
            if (favMlStatusEl) {
                favMlStatusEl.textContent = `Carregando anuncios do SKU ${skuSelecionado}...`;
            }

            try {
                const params = new URLSearchParams({ sku: skuSelecionado });
                const lojaApi = favoritosLojaSelecionadaParaApi(favMlLojaSelecionada || undefined);
                if (lojaApi) params.set('loja', lojaApi);
                const carregarCompartilhado = !!(opcoes && opcoes.compartilharSku);
                if (carregarCompartilhado) params.set('compartilhar_sku', '1');
                const mlbsSidebar = extrairMlbsItemSkuSidebar(itemSidebar);
                if (mlbsSidebar.length) params.set('mlbs', mlbsSidebar.join(','));
                params.set('atualizar', '1');
                params.set('preco_atual', '1');
                const controller = typeof AbortController === 'function' ? new AbortController() : null;
                const timeoutId = controller ? window.setTimeout(() => controller.abort(), 90000) : null;
                const response = await fetch(`/api/favoritos/ml/anuncios-sku?${params.toString()}`, {
                    headers: obterAuthHeaders(),
                    cache: 'no-store',
                    ...(controller ? { signal: controller.signal } : {})
                });
                if (timeoutId) window.clearTimeout(timeoutId);
                if (!response.ok) {
                    let detalhe = `HTTP ${response.status}`;
                    try {
                        const dataErro = await response.json();
                        detalhe = dataErro.detail || detalhe;
                    } catch (_err) {}
                    throw new Error(detalhe);
                }
                const data = await response.json();
                if (favMlSkuSelecionado !== skuSelecionado) return;

                favMlAnunciosSkuAtual = Array.isArray(data.anuncios) ? data.anuncios : [];
                const lojasCompartilhadas = registrarLojasCompartilhadasFavoritosSku(
                    skuSelecionado,
                    Array.isArray(data.lojas_com_sku) ? data.lojas_com_sku : []
                );
                renderizarFavoritosAnunciosMl(favMlAnunciosSkuAtual, skuSelecionado);
                renderizarFavoritosOutrosAnuncios(skuSelecionado);
                if (favMlStatusEl) {
                    const loja = data.loja || favMlLojaSelecionada || favoritosLojaSelecionadaParaApi();
                    const lojasTexto = lojasCompartilhadas.length > 1
                        ? ` nas lojas ${lojasCompartilhadas.join(', ')}`
                        : (loja ? ` em ${loja}` : '');
                    favMlStatusEl.textContent = favoritosComporStatusCacheMl(
                        `${favMlAnunciosSkuAtual.length} anuncio(s) do Mercado Livre para o SKU ${skuSelecionado}${lojasTexto}.`,
                        data
                    );
                }
            } catch (err) {
                if (favMlSkuSelecionado !== skuSelecionado) return;
                favMlAnunciosSkuAtual = [];
                renderizarFavoritosAnunciosMl([], skuSelecionado);
                if (favMlStatusEl) {
                    const mensagem = err && err.name === 'AbortError'
                        ? 'tempo limite ao consultar o Mercado Livre'
                        : (err && err.message ? err.message : err);
                    favMlStatusEl.textContent = `Erro ao carregar anuncios do SKU ${skuSelecionado}: ${mensagem}`;
                }
            }
        }

        function agendarAtualizacaoRankingMediaVendas() {
            if (mlRankingMediaFrame) return;
            const schedule = typeof window.requestAnimationFrame === 'function'
                ? window.requestAnimationFrame.bind(window)
                : (callback) => window.setTimeout(callback, 50);
            mlRankingMediaFrame = schedule(() => {
                mlRankingMediaFrame = null;
                atualizarRankingMediaVendas();
            });
        }

        function atualizarRankingMediaVendas() {
            if (!mlRankingMediaListEl || !mlRankingMediaEmptyEl || !mlRankingMediaCountEl) return;

            renderizarVendedoresIgnoradosRanking();

            const metricas = (mlAnunciosPrimeiraPaginaAtuais || [])
                .map((anuncio, index) => {
                    const metrica = calcularMetricasMediaVendas(anuncio);
                    anuncio.media_vendas_mensal = Number.isFinite(metrica.media) ? metrica.media : null;
                    anuncio.meses_desde_criacao = Number.isFinite(metrica.meses) ? metrica.meses : null;
                    return { anuncio, index, metrica };
                })
                .filter(item => Number.isFinite(item.metrica.media));

            const ignorados = metricas.filter(item => vendedorIgnoradoNoRanking(item.anuncio.vendedor)).length;
            const ranking = metricas
                .filter(item => !vendedorIgnoradoNoRanking(item.anuncio.vendedor))
                .sort((a, b) => {
                    const diffMedia = b.metrica.media - a.metrica.media;
                    if (Math.abs(diffMedia) > 0.0001) return diffMedia;
                    const diffVendas = (b.metrica.vendas || 0) - (a.metrica.vendas || 0);
                    if (diffVendas !== 0) return diffVendas;
                    return a.index - b.index;
                });

            const total = (mlAnunciosPrimeiraPaginaAtuais || []).length;
            mlRankingMediaCountEl.textContent = total
                ? `${ranking.length}/${total}${ignorados ? ` · ${ignorados} fora` : ''}`
                : '';
            mlRankingMediaEmptyEl.textContent = ignorados && metricas.length && !ranking.length
                ? 'Todos os anúncios com média calculada pertencem a vendedores fora do ranking.'
                : 'Assim que vendas e data forem carregadas, os anúncios aparecem aqui em ordem da maior média mensal.';
            mlRankingMediaEmptyEl.classList.toggle('hidden', ranking.length > 0);
            mlRankingMediaListEl.innerHTML = '';

            ranking.slice(0, ML_FAVORITOS_HISTORICO_ANUNCIOS_MAX).forEach((item, rankIndex) => {
                const anuncio = item.anuncio;
                const metrica = item.metrica;
                const card = document.createElement('div');
                card.className = 'ml-ranking-item';

                const topLine = document.createElement('div');
                topLine.className = 'ml-ranking-topline';

                const rank = document.createElement('span');
                rank.className = 'ml-ranking-rank';
                rank.textContent = `#${rankIndex + 1}`;

                const media = document.createElement('span');
                media.className = 'ml-ranking-media';
                media.textContent = `${formatarMediaVendas(anuncio)}/mês`;

                topLine.appendChild(rank);
                topLine.appendChild(media);

                const nome = document.createElement(anuncio.url ? 'a' : 'span');
                nome.className = 'ml-ranking-name';
                nome.textContent = anuncio.titulo || anuncio.id || anuncio.url || 'Anúncio sem título';
                if (anuncio.url) {
                    nome.href = anuncio.url;
                    nome.target = '_blank';
                    nome.rel = 'noopener';
                }

                const meta = document.createElement('div');
                meta.className = 'ml-ranking-meta';
                const partesMeta = [];
                if (anuncio.vendedor) partesMeta.push(`Vendedor: ${anuncio.vendedor}`);
                partesMeta.push(`Vendas: ${metrica.vendas}`);
                const diasAnuncio = formatarDiasAnuncio(anuncio);
                partesMeta.push(`Dias: ${diasAnuncio || formatarMesesMedia(metrica.meses)}`);
                meta.textContent = partesMeta.join(' | ');

                card.appendChild(topLine);
                card.appendChild(nome);
                card.appendChild(meta);
                mlRankingMediaListEl.appendChild(card);
            });
        }

        function atualizarCelulaDataCriacao(item, dataCriacao) {
            if (typeof anuncioRankingHistoricoEstaticoFavoritos === 'function' && anuncioRankingHistoricoEstaticoFavoritos(item)) return false;
            const alvo = encontrarAnuncioPrimeiraPaginaAtual(item);
            if (alvo) alvo.data_criacao = dataCriacao;
            const escapeCss = window.CSS && CSS.escape ? CSS.escape : (value) => String(value).replace(/["\\]/g, '\\$&');
            const selectors = [];
            if (item.id) selectors.push(`tr[data-item-id="${escapeCss(item.id)}"] .ml-data-criacao`);
            if (item.url) selectors.push(`tr[data-url="${escapeCss(item.url || '')}"] .ml-data-criacao`);
            const cell = selectors.map(selector => document.querySelector(selector)).find(Boolean);
            atualizarCelulaMediaVendas(alvo || item);
            agendarAtualizacaoRankingMediaVendas();
            if (cell) {
                cell.textContent = formatarDataCriacao(dataCriacao);
                return true;
            }
            return false;
        }

        function atualizarCelulaVendedor(item, vendedor) {
            if (typeof anuncioRankingHistoricoEstaticoFavoritos === 'function' && anuncioRankingHistoricoEstaticoFavoritos(item)) return false;
            const alvo = encontrarAnuncioPrimeiraPaginaAtual(item);
            if (alvo) alvo.vendedor = vendedor || '';
            const escapeCss = window.CSS && CSS.escape ? CSS.escape : (value) => String(value).replace(/["\\]/g, '\\$&');
            const selectors = [];
            if (item.id) selectors.push(`tr[data-item-id="${escapeCss(item.id)}"] .ml-vendedor`);
            if (item.url) selectors.push(`tr[data-url="${escapeCss(item.url || '')}"] .ml-vendedor`);
            const cell = selectors.map(selector => document.querySelector(selector)).find(Boolean);
            agendarAtualizacaoRankingMediaVendas();
            if (cell) {
                renderizarCelulaVendedor(cell, vendedor || '');
                return true;
            }
            return false;
        }

        function atualizarCelulaTitulo(item, titulo) {
            if (typeof anuncioRankingHistoricoEstaticoFavoritos === 'function' && anuncioRankingHistoricoEstaticoFavoritos(item)) return false;
            const alvo = encontrarAnuncioPrimeiraPaginaAtual(item);
            if (alvo) alvo.titulo = titulo || '';
            const escapeCss = window.CSS && CSS.escape ? CSS.escape : (value) => String(value).replace(/["\\]/g, '\\$&');
            const selectors = [];
            if (item.id) selectors.push(`tr[data-item-id="${escapeCss(item.id)}"] .ml-titulo`);
            if (item.url) selectors.push(`tr[data-url="${escapeCss(item.url || '')}"] .ml-titulo`);
            const cell = selectors.map(selector => document.querySelector(selector)).find(Boolean);
            agendarAtualizacaoRankingMediaVendas();
            if (cell) {
                cell.textContent = titulo || '';
                return true;
            }
            return false;
        }

        function atualizarCelulaVendas(item, vendas, fonteVendas) {
            if (typeof anuncioRankingHistoricoEstaticoFavoritos === 'function' && anuncioRankingHistoricoEstaticoFavoritos(item)) return false;
            const alvo = encontrarAnuncioPrimeiraPaginaAtual(item);
            const fonteNormalizada = normalizarFonte(fonteVendas || '');
            if (item && fonteNormalizada) {
                item.vendasFonte = fonteNormalizada;
                item.vendas_fonte = fonteNormalizada;
            }
            if (alvo) {
                alvo.vendas = vendas;
                if (fonteNormalizada) {
                    alvo.vendasFonte = fonteNormalizada;
                    alvo.vendas_fonte = fonteNormalizada;
                }
            }
            const escapeCss = window.CSS && CSS.escape ? CSS.escape : (value) => String(value).replace(/["\\]/g, '\\$&');
            const selectors = [];
            if (item.id) selectors.push(`tr[data-item-id="${escapeCss(item.id)}"] .ml-vendas`);
            if (item.url) selectors.push(`tr[data-url="${escapeCss(item.url || '')}"] .ml-vendas`);
            const cell = selectors.map(selector => document.querySelector(selector)).find(Boolean);
            atualizarCelulaMediaVendas(alvo || item);
            agendarAtualizacaoRankingMediaVendas();
            if (cell) {
                cell.textContent = vendas !== null && vendas !== undefined ? vendas : '';
                return true;
            }
            return false;
        }

        function atualizarCelulaMlb(item, mlbId) {
            if (typeof anuncioRankingHistoricoEstaticoFavoritos === 'function' && anuncioRankingHistoricoEstaticoFavoritos(item)) return false;
            const id = mlbId || (item && (item.id || extrairItemIdAnuncio(item.url)));
            if (!id || !item) return false;
            const escapeCss = window.CSS && CSS.escape ? CSS.escape : (value) => String(value).replace(/["\\]/g, '\\$&');
            const selectors = [];
            if (item.id) selectors.push(`tr[data-item-id="${escapeCss(item.id)}"] .ml-mlb`);
            if (item.url) selectors.push(`tr[data-url="${escapeCss(item.url || '')}"] .ml-mlb`);
            const rowSelectors = [];
            if (item.id) rowSelectors.push(`tr[data-item-id="${escapeCss(item.id)}"]`);
            if (item.url) rowSelectors.push(`tr[data-url="${escapeCss(item.url || '')}"]`);
            const cell = selectors.map(selector => document.querySelector(selector)).find(Boolean);
            const row = rowSelectors.map(selector => document.querySelector(selector)).find(Boolean);
            if (cell) cell.textContent = id;
            if (row) row.dataset.itemId = id;
            item.id = id;
            return !!cell;
        }

        function aplicarDadosAvantNoAnuncio(anuncio, dados) {
            if (typeof anuncioRankingHistoricoEstaticoFavoritos === 'function' && anuncioRankingHistoricoEstaticoFavoritos(anuncio)) {
                return { vendedor: false, data: false, vendas: false, mlb: false };
            }
            if (!anuncio || !dados) return { vendedor: false, data: false, vendas: false };
            const atualizado = { vendedor: false, data: false, vendas: false, mlb: false };

            const mlbId = dados.id || extrairItemIdAnuncio(dados.url) || anuncio.id || extrairItemIdAnuncio(anuncio.url);
            if (mlbId) {
                atualizado.mlb = atualizarCelulaMlb(anuncio, mlbId);
            }

            const vendedor = normalizarNomeVendedor(String(dados.vendedor || ''));
            const fonteVendedor = dados.vendedorFonte || dados.vendedor_fonte || (/avantpro/i.test(String(dados.source || '')) ? 'avantpro_card' : 'pagina_produto');
            if (deveAtualizarVendedor(anuncio.vendedor, anuncio.vendedorFonte, vendedor, fonteVendedor)) {
                anuncio.vendedor = vendedor;
                anuncio.vendedorFonte = fonteVendedor;
                atualizado.vendedor = atualizarCelulaVendedor(anuncio, vendedor);
            }

            if (dados.data_criacao) {
                anuncio.data_criacao = dados.data_criacao;
                atualizado.data = atualizarCelulaDataCriacao(anuncio, dados.data_criacao);
            }

            const vendas = parseNumeroVendas(dados.vendas);
            const fonteVendas = dados.vendasFonte || dados.vendas_fonte || (/avantpro/i.test(String(dados.source || '')) ? 'avantpro_anuncio' : '');
            if (deveAtualizarVendas(anuncio.vendas, anuncio.vendasFonte, vendas, fonteVendas)) {
                anuncio.vendasFonte = fonteVendas;
                anuncio.vendas = vendas;
                atualizado.vendas = atualizarCelulaVendas(anuncio, vendas);
            }

            return atualizado;
        }

        async function coletaAvantAutomaticaAntigaDesativada(anuncios, tentativa = 1) {
            if (!mlWebviewEl || !Array.isArray(anuncios) || !anuncios.length) return { vendedor: 0, data: 0, vendas: 0 };

            let dadosAvant = [];
            if (tentativa >= 3 && !mlAvantScrollExecutada) {
                mlAvantScrollExecutada = true;
                mlPrimeiraPaginaStatusEl.textContent = 'Atualizando dados do Avant Pro nos anúncios listados...';
                dadosAvant = await coletarDadosAvantComRolagem();
            } else {
                const resultado = await extrairAnunciosWebviewVisivel({
                    clicarAvant: false,
                    timeoutMs: 3000,
                    aguardarAposCliqueAvant: 180,
                    aguardarEstabilidadeAvant: { minWaitMs: 0, stableMs: 220, maxWaitMs: 700 }
                });
                dadosAvant = (resultado && resultado.anuncios) || [];
            }
            let vendedores = 0;
            let datas = 0;
            let vendas = 0;

            anuncios.forEach(anuncio => {
                const dados = encontrarAnuncioAvantCorrespondente(anuncio, dadosAvant);
                if (!dados) return;
                const atualizado = aplicarDadosAvantNoAnuncio(anuncio, dados);
                if (atualizado.vendedor) vendedores += 1;
                if (atualizado.data) datas += 1;
                if (atualizado.vendas) vendas += 1;
            });
            salvarCacheAvantDosAnuncios(anuncios, {
                termo: obterPrimeiroTermoPesquisaAvulsaMl(),
                sku: favMlSkuSelecionado || histMlSkuSelecionado || obterPrimeiroTermoPesquisaAvulsaMl()
            });

            if (vendedores || datas || vendas) {
                mlPrimeiraPaginaStatusEl.textContent = `Avant Pro: ${vendedores} vendedor(es), ${datas} data(s) e ${vendas} venda(s) atualizada(s). Coletando mais resultados...`;
            } else if (tentativa > 1) {
                mlPrimeiraPaginaStatusEl.textContent = 'Coletando mais resultados do Avant Pro...';
            }

            return { vendedor: vendedores, data: datas, vendas };
        }

        function agendarAtualizacaoAvantAutomatica(anuncios) {
            if (mlAvantAutoTimer) {
                clearTimeout(mlAvantAutoTimer);
                mlAvantAutoTimer = null;
            }
            mlAvantAutoRunId += 1;
            mlAvantScrollExecutada = false;
            return {
                agendado: false,
                desativado: true,
                total: Array.isArray(anuncios) ? anuncios.length : 0
            };
            const runId = mlAvantAutoRunId;
            const atrasos = [];
            let indice = 0;

            const executar = async () => {
                if (runId !== mlAvantAutoRunId) return;
                const tentativa = indice + 1;
                try {
                    await coletaAvantAutomaticaAntigaDesativada(anuncios, tentativa);
                } catch (err) {
                    console.warn('Falha na extração automática do Avant Pro:', err);
                }

                indice += 1;
                if (indice < atrasos.length) {
                    mlAvantAutoTimer = null;
                }
            };

            mlAvantAutoTimer = null;
        }

        async function abrirMercadoLivreNoPrograma(opcoes = {}) {
            const termoPesquisa = String(opcoes.termoPesquisa || '').trim();
            const apenasAbrirUrl = opcoes.apenasAbrirUrl === true || opcoes.confirmarPesquisa === false;
            const url = termoPesquisa
                ? construirUrlPesquisaMercadoLivre(termoPesquisa)
                : normalizarUrl(mlUrlInput.value);
            mlUrlInput.value = url;
            mlFrameHint.classList.add('hidden');
            abrirBalaoResultadosMl({
                titulo: opcoes.titulo || 'Navegador do Mercado Livre',
                subtitulo: opcoes.subtitulo || url,
                mostrarFavoritos: !!opcoes.mostrarFavoritos,
                browserCompleto: opcoes.browserCompleto === undefined ? !opcoes.mostrarFavoritos : !!opcoes.browserCompleto,
                forcarExibicao: !!opcoes.forcarExibicao
            });

            if (hasInternalBrowserApi || usarNavegadorMlNoShellElectron()) {
                try {
                    if (usarNavegadorMlNoShellElectron() && opcoes.agendarPosicaoAntes !== false) {
                        agendarAtualizacaoPosicaoNavegadorMlShell();
                        await esperar(120);
                    }
                    await navegarMlWebview(url);
                    if (!apenasAbrirUrl && termoPesquisa && typeof garantirPesquisaMercadoLivreSubmetida === 'function') {
                        await garantirPesquisaMercadoLivreSubmetida(termoPesquisa, url).catch(() => null);
                        if (typeof aguardarPesquisaMercadoLivreAtual === 'function') {
                            await aguardarPesquisaMercadoLivreAtual(termoPesquisa, {
                                url,
                                timeoutMs: Math.max(900, Number(opcoes.aguardarPesquisaMs) || 900),
                                pollMs: 180
                            }).catch(() => null);
                        }
                    }
                    mlOpenedOnce = true;
                    if (opcoes.reposicionarDepois !== false) {
                        agendarAtualizacaoPosicaoNavegadorMlShell();
                    }
                    atualizarAnimacaoAzulNoNavegadorMl(mlFavoritosEmExecucao);
                    return true;
                } catch (err) {
                    const verificacao = typeof confirmarAberturaNavegadorMlAposTimeout === 'function'
                        ? await confirmarAberturaNavegadorMlAposTimeout(url, err)
                        : { confirmado: false, reason: 'verificacao-indisponivel', url: '' };
                    if (verificacao.confirmado) {
                        mlOpenedOnce = true;
                        atualizarAnimacaoAzulNoNavegadorMl(mlFavoritosEmExecucao);
                        return true;
                    }
                    console.error('Falha real ao abrir o navegador interno do Mercado Livre.', {
                        erro: err && err.message ? err.message : String(err),
                        verificacao
                    });
                    setBrowserStatus('Falha ao abrir no quadro interno do programa.');
                    mostrarHintAbertura();
                    return false;
                }
            }

            mostrarHintAbertura();
            setBrowserStatus('Este modo web não consegue abrir internamente. Use "Abrir Externo" ou execute pelo app Electron.');
            return false;
        }

        async function pesquisarNoMercadoLivreNoPrograma() {
            cancelarAberturaMercadoLivreAoEntrar();
            const termo = obterPrimeiroTermoPesquisaAvulsaMl();
            if (!termo) {
                alert('Digite um termo para pesquisar no Mercado Livre.');
                mlSearchTermInput.focus();
                return;
            }

            mlUrlInput.value = construirUrlPesquisaMercadoLivre(termo);
            const abriu = await abrirMercadoLivreNoPrograma({
                titulo: 'Resultados da pesquisa no Mercado Livre',
                subtitulo: termo,
                browserCompleto: false
            });
            if (!abriu) {
                mlPrimeiraPaginaStatusEl.textContent = 'Não foi possível abrir o Mercado Livre no quadro interno. Abra pelo app Electron, faça login/verificação se necessário e pesquise novamente.';
                return;
            }
            await carregarAnunciosPrimeiraPagina(termo);
        }

        async function carregarAnunciosPrimeiraPagina(termo) {
            mlPrimeiraPaginaStatusEl.textContent = 'Buscando anúncios da 1ª página...';
            mlPrimeiraPaginaEmptyEl.classList.add('hidden');
            mlPrimeiraPaginaTableWrapEl.classList.add('hidden');
            mlPrimeiraPaginaBodyEl.innerHTML = '';
            let tentouElectron = false;
            let ultimoDebugElectron = null;
            let ultimoErroElectron = null;

            // Primeiro tenta extrair da página exibida no quadro interno do Electron.
            if (mlWebviewEl) {
                tentouElectron = true;
                try {
                    const resultadoWebview = await extrairAnunciosWebviewVisivel({ clicarAvant: false, fastLinks: true, timeoutMs: 5200, aguardarAposCliqueAvant: 250 });
                    ultimoDebugElectron = resultadoWebview && resultadoWebview.debug;
                    const anunciosWebview = (resultadoWebview && resultadoWebview.anuncios) ? resultadoWebview.anuncios : [];

                    if (resultadoWebview && resultadoWebview.needsAvantLogin) {
                        mlPrimeiraPaginaStatusEl.textContent = 'Avant Pro nao retornou dados coletaveis. Confirme manualmente o login no quadro interno e tente novamente.';
                        await fecharModalBloqueanteAvantProNoWebview().catch(() => null);
                        mlPrimeiraPaginaEmptyEl.classList.remove('hidden');
                        return;
                    }

                    if (resultadoWebview && resultadoWebview.needsLogin) {
                        mlPrimeiraPaginaStatusEl.textContent = 'A página está pedindo login/verificação. Faça login no quadro interno e clique em "Pesquisar no ML" novamente.';
                        mlPrimeiraPaginaEmptyEl.classList.remove('hidden');
                        return;
                    }

                    if (anunciosWebview.length && !(resultadoWebview && resultadoWebview.needsAvantLogin)) {
                        renderizarAnunciosPrimeiraPagina(anunciosWebview, 'Links extraídos diretamente da página exibida no quadro interno.');
                        return;
                    }

                    if (resultadoWebview && resultadoWebview.success === false) {
                        ultimoErroElectron = resultadoWebview.error || 'Erro desconhecido na extração.';
                        console.warn('Falha na extração do webview ML:', resultadoWebview.error || resultadoWebview);
                    }

                    mlPrimeiraPaginaStatusEl.textContent = 'Aguardando cards visiveis do Mercado Livre e Avant Pro...';
                    const prontoWebview = await aguardarPrimeirosDadosAvantOuCardsWebview({ timeoutMs: 1100, idleMs: 180 }).catch(() => null);
                    if (prontoWebview && prontoWebview.needsAvantLogin) {
                        mlPrimeiraPaginaStatusEl.textContent = 'Avant Pro nao retornou dados coletaveis. Confirme manualmente o login no quadro interno e tente novamente.';
                        await fecharModalBloqueanteAvantProNoWebview().catch(() => null);
                        mlPrimeiraPaginaEmptyEl.classList.remove('hidden');
                        return;
                    }
                    const resultadoRetry = await extrairAnunciosWebviewVisivel({ clicarAvant: false, fastLinks: true, timeoutMs: 5200, aguardarAposCliqueAvant: 300 });
                    ultimoDebugElectron = resultadoRetry && resultadoRetry.debug;
                    const anunciosRetry = (resultadoRetry && resultadoRetry.anuncios) ? resultadoRetry.anuncios : [];

                    if (resultadoRetry && resultadoRetry.needsAvantLogin) {
                        mlPrimeiraPaginaStatusEl.textContent = 'Avant Pro ainda nao retornou dados coletaveis. Confirme manualmente o login no quadro interno e tente novamente.';
                        mlPrimeiraPaginaEmptyEl.classList.remove('hidden');
                        return;
                    }

                    if (resultadoRetry && resultadoRetry.needsLogin) {
                        mlPrimeiraPaginaStatusEl.textContent = 'A pagina esta pedindo login/verificacao. Faca login no quadro interno e clique em "Pesquisar no ML" novamente.';
                        mlPrimeiraPaginaEmptyEl.classList.remove('hidden');
                        return;
                    }

                    if (anunciosRetry.length) {
                        renderizarAnunciosPrimeiraPagina(anunciosRetry, 'Links extraidos diretamente da pagina exibida no quadro interno apos aguardar o carregamento.');
                        return;
                    }

                    if (resultadoRetry && resultadoRetry.success === false) {
                        ultimoErroElectron = resultadoRetry.error || ultimoErroElectron || 'Erro desconhecido na extracao.';
                        console.warn('Falha na segunda extracao do webview ML:', resultadoRetry.error || resultadoRetry);
                    }
                } catch (err) {
                    ultimoErroElectron = err && err.message ? err.message : String(err);
                    console.warn('Falha ao extrair do webview ML:', err);
                    mlPrimeiraPaginaStatusEl.textContent = 'Aguardando cards visiveis para tentar extrair novamente...';
                    const prontoRetryErro = await aguardarPrimeirosDadosAvantOuCardsWebview({ timeoutMs: 1100, idleMs: 180 }).catch(() => null);
                    if (prontoRetryErro && prontoRetryErro.needsAvantLogin) {
                        mlPrimeiraPaginaStatusEl.textContent = 'Avant Pro nao retornou dados coletaveis. Confirme manualmente o login no quadro interno e tente novamente.';
                        await fecharModalBloqueanteAvantProNoWebview().catch(() => null);
                        mlPrimeiraPaginaEmptyEl.classList.remove('hidden');
                        return;
                    }
                    try {
                        const resultadoRetryErro = await extrairAnunciosWebviewVisivel({ clicarAvant: false, fastLinks: true, timeoutMs: 5200, aguardarAposCliqueAvant: 300 });
                        ultimoDebugElectron = resultadoRetryErro && resultadoRetryErro.debug;
                        const anunciosRetryErro = (resultadoRetryErro && resultadoRetryErro.anuncios) ? resultadoRetryErro.anuncios : [];
                        if (resultadoRetryErro && resultadoRetryErro.needsAvantLogin) {
                            mlPrimeiraPaginaStatusEl.textContent = 'Avant Pro ainda nao retornou dados coletaveis. Confirme manualmente o login no quadro interno e tente novamente.';
                            mlPrimeiraPaginaEmptyEl.classList.remove('hidden');
                            return;
                        }
                        if (resultadoRetryErro && resultadoRetryErro.needsLogin) {
                            mlPrimeiraPaginaStatusEl.textContent = 'A pagina esta pedindo login/verificacao. Faca login no quadro interno e clique em "Pesquisar no ML" novamente.';
                            mlPrimeiraPaginaEmptyEl.classList.remove('hidden');
                            return;
                        }
                        if (anunciosRetryErro.length) {
                            renderizarAnunciosPrimeiraPagina(anunciosRetryErro, 'Links extraidos diretamente da pagina exibida no quadro interno apos nova tentativa.');
                            return;
                        }
                        if (resultadoRetryErro && resultadoRetryErro.success === false) {
                            ultimoErroElectron = resultadoRetryErro.error || ultimoErroElectron;
                        }
                    } catch (errRetry) {
                        ultimoErroElectron = errRetry && errRetry.message ? errRetry.message : String(errRetry);
                        console.warn('Falha na segunda tentativa do webview ML:', errRetry);
                    }
                }            }

            if (tentouElectron) {
                const detalhe = ultimoDebugElectron
                    ? ` Página: ${ultimoDebugElectron.title || 'sem título'}; links: ${ultimoDebugElectron.linkCount || 0}; cards: ${ultimoDebugElectron.cardCount || 0}.`
                    : '';
                const detalheErro = ultimoErroElectron ? ` Erro: ${String(ultimoErroElectron).slice(0, 180)}.` : '';
                mlPrimeiraPaginaStatusEl.textContent = `Nenhum anúncio foi encontrado no navegador interno.${detalhe}${detalheErro} Se aparecer verificação/login do Mercado Livre, conclua no quadro interno e pesquise novamente.`;
                mlPrimeiraPaginaEmptyEl.classList.remove('hidden');
                return;
            }

            try {
                const response = await fetch('/api/favoritos/ml/primeira-pagina', {
                    method: 'POST',
                    headers: headersJsonAutenticado(),
                    body: JSON.stringify({ termo })
                });

                if (!response.ok) {
                    let detail = 'Erro ao carregar anúncios da 1ª página.';
                    try {
                        const errJson = await response.json();
                        detail = errJson.detail || detail;
                    } catch (e) {}
                    throw new Error(detail);
                }

                const data = await response.json();
                const anuncios = data.anuncios || [];
                const statusTexto = data.warning
                    ? data.warning
                    : `Total de anúncios na 1ª página: ${anuncios.length} (na ordem exibida no Mercado Livre)`;
                renderizarAnunciosPrimeiraPagina(anuncios, statusTexto);
            } catch (err) {
                mlPrimeiraPaginaStatusEl.textContent = err.message || 'Não foi possível carregar anúncios agora. Tente novamente em instantes.';
                mlPrimeiraPaginaEmptyEl.classList.remove('hidden');
            }
        }

        function renderizarAnunciosPrimeiraPagina(anuncios, statusTexto) {
            const anunciosOriginais = aplicarCacheAvantAosAnuncios(anuncios || [], {
                termo: obterPrimeiroTermoPesquisaAvulsaMl(),
                sku: favMlSkuSelecionado || histMlSkuSelecionado || obterPrimeiroTermoPesquisaAvulsaMl()
            });
            anuncios = anunciosOriginais
                .map((anuncio) => {
                    const mlbId = anuncio && (anuncio.id || extrairItemIdAnuncio(anuncio.url));
                    const fonteVendasInicial = anuncio && (anuncio.vendasFonte || anuncio.vendas_fonte || '');
                    const vendasInicial = parseNumeroVendas(anuncio && anuncio.vendas);
                    const usarVendasInicial = fonteVendasConfiavel(fonteVendasInicial) && hasNumeroVendas(vendasInicial);
                    return {
                        ...anuncio,
                        id: mlbId || (anuncio && anuncio.id) || '',
                        vendedor: anuncio && anuncio.vendedor ? anuncio.vendedor : '',
                        vendedorFonte: anuncio && anuncio.vendedor && hasTexto(anuncio.vendedor)
                            ? normalizarFonte(anuncio.vendedorFonte || anuncio.vendedor_fonte || 'api_search')
                            : '',
                        vendas: usarVendasInicial ? vendasInicial : null,
                        vendasFonte: usarVendasInicial ? normalizarFonte(fonteVendasInicial) : ''
                    };
                })
                .filter((anuncio) => anuncio && anuncio.id && !tituloPareceFiltroOuCategoriaMl(anuncio.titulo));
            anuncios.forEach((anuncio, idx) => { anuncio.posicao = idx + 1; });

            const removidos = anunciosOriginais.length - anuncios.length;
            mlPrimeiraPaginaStatusEl.textContent = (statusTexto || `Total de anuncios na 1a pagina: ${anuncios.length}`)
                + (removidos > 0 ? ` ${removidos} link(s) de categoria/filtro foram ignorados.` : '');
            if (!anuncios.length) {
                mlAnunciosPrimeiraPaginaAtuais = [];
                mlPrimeiraPaginaEmptyEl.classList.remove('hidden');
                if (mlPrimeiraPaginaLayoutEl) mlPrimeiraPaginaLayoutEl.classList.add('hidden');
                mlPrimeiraPaginaTableWrapEl.classList.add('hidden');
                atualizarRankingMediaVendas();
                atualizarEstadoSidebarRanking();
                return;
            }

            mlAnunciosPrimeiraPaginaAtuais = anuncios;
            mlPrimeiraPaginaBodyEl.innerHTML = '';
            anuncios.forEach((anuncio, idx) => {
                const tr = document.createElement('tr');
                const mlbId = anuncio.id || extrairItemIdAnuncio(anuncio.url);
                anuncio.id = mlbId || anuncio.id || '';
                const rowKey = anuncio.id || anuncio.url || String(idx + 1);
                tr.dataset.itemId = anuncio.id || '';
                tr.dataset.url = anuncio.url || '';
                tr.dataset.rowKey = rowKey;

                const tdPosicao = document.createElement('td');
                const posicao = anuncio.posicao !== null && anuncio.posicao !== undefined ? anuncio.posicao : (idx + 1);
                tdPosicao.textContent = posicao;

                const tdMlb = document.createElement('td');
                tdMlb.className = 'ml-mlb';
                tdMlb.textContent = mlbId || '';

                const tdTitulo = document.createElement('td');
                tdTitulo.className = 'ml-titulo';
                tdTitulo.textContent = anuncio.titulo || '';

                const tdVendedor = document.createElement('td');
                tdVendedor.className = 'ml-vendedor';
                renderizarCelulaVendedor(tdVendedor, anuncio.vendedor || '');

                const tdData = document.createElement('td');
                tdData.className = 'ml-data-criacao';
                tdData.textContent = formatarDataCriacao(anuncio.data_criacao);

                const tdVendas = document.createElement('td');
                tdVendas.className = 'ml-vendas';
                tdVendas.textContent = anuncio.vendas !== null && anuncio.vendas !== undefined ? anuncio.vendas : '';

                const tdMedia = document.createElement('td');
                tdMedia.className = 'ml-media-vendas';
                tdMedia.textContent = formatarMediaVendas(anuncio);
                tdMedia.title = 'Média mensal calculada por vendas e data de criação.';

                const tdLink = document.createElement('td');
                if (anuncio.url) {
                    const actions = document.createElement('span');
                    actions.className = 'link-actions';

                    const a = document.createElement('a');
                    a.className = 'link';
                    a.href = anuncio.url;
                    a.target = '_blank';
                    a.rel = 'noopener';
                    a.textContent = 'Abrir';
                    a.addEventListener('click', (event) => abrirAnuncioComAvantPro(anuncio.url, event));

                    const copyBtn = document.createElement('button');
                    copyBtn.type = 'button';
                    copyBtn.className = 'copy-link-btn';
                    copyBtn.textContent = 'Copiar';
                    copyBtn.addEventListener('click', () => copiarLinkAnuncio(anuncio.url, copyBtn));

                    const scanBtn = document.createElement('button');
                    scanBtn.type = 'button';
                    scanBtn.className = 'copy-link-btn';
                    scanBtn.textContent = 'Extrair Avant';
                    scanBtn.addEventListener('click', () => varrerCodigoFonteAnuncio(anuncio, scanBtn));

                    actions.appendChild(a);
                    actions.appendChild(copyBtn);
                    actions.appendChild(scanBtn);
                    tdLink.appendChild(actions);
                }

                tr.appendChild(tdPosicao);
                tr.appendChild(tdMlb);
                tr.appendChild(tdTitulo);
                tr.appendChild(tdVendedor);
                tr.appendChild(tdData);
                tr.appendChild(tdVendas);
                tr.appendChild(tdMedia);
                tr.appendChild(tdLink);
                mlPrimeiraPaginaBodyEl.appendChild(tr);
                atualizarCelulaMediaVendas(anuncio);
            });

            if (mlPrimeiraPaginaLayoutEl) mlPrimeiraPaginaLayoutEl.classList.remove('hidden');
            mlPrimeiraPaginaTableWrapEl.classList.remove('hidden');
            mlPrimeiraPaginaEmptyEl.classList.add('hidden');
            atualizarRankingMediaVendas();
            atualizarEstadoSidebarRanking();

            agendarAtualizacaoAvantAutomatica(anuncios);

            if (typeof monitoramentoPaginaFavoritosAutomaticoAtivo === 'function' && monitoramentoPaginaFavoritosAutomaticoAtivo()) {
                setTimeout(() => {
                    enriquecerDatasCriacaoAnuncios(anuncios).catch(err => {
                        console.warn('Falha ao enriquecer datas dos anúncios:', err);
                    });
                }, 1500);
            }
        }

        async function enriquecerDatasCriacaoAnuncios(anuncios) {
            const pendentes = (anuncios || [])
                .filter(item => item && item.url)
                .filter(item => !(typeof anuncioRankingHistoricoEstaticoFavoritos === 'function' && anuncioRankingHistoricoEstaticoFavoritos(item)));

            if (!pendentes.length) return;

            const statusAnterior = mlPrimeiraPaginaStatusEl.textContent;
            mlPrimeiraPaginaStatusEl.textContent = `${statusAnterior} Identificando vendedores e datas de todos os ${pendentes.length} anúncio(s)...`;

            const atualizadasElectron = await tentarDataCriacaoPeloElectron(pendentes);
            const vendedoresElectron = pendentes.filter(item => item.vendedor).length;
            if (atualizadasElectron.datas || atualizadasElectron.vendedores || atualizadasElectron.vendas) {
                mlPrimeiraPaginaStatusEl.textContent = `${statusAnterior} Dados via Electron: ${atualizadasElectron.vendedores} vendedor(es), ${atualizadasElectron.datas} data(s) e ${atualizadasElectron.vendas} venda(s).`;
            }

            const restantes = pendentes.filter(item => {
                const vendas = Number(item.vendas);
                return !item.data_criacao || !item.vendedor || !Number.isFinite(vendas);
            });
            if (!restantes.length) return;

            const response = await fetch('/api/favoritos/ml/enriquecer-datas', {
                method: 'POST',
                headers: headersJsonAutenticado(),
                body: JSON.stringify({
                    max_anuncios: Math.max(restantes.length, pendentes.length),
                    anuncios: restantes.map(item => ({
                        id: item.id || '',
                        url: item.url || ''
                    }))
                })
            });

            if (!response.ok) {
                throw new Error('Erro ao buscar datas de criação dos anúncios.');
            }

            const data = await response.json();
            const resultados = data.resultados || [];
            let atualizadas = 0;
            let vendedoresAtualizados = 0;
            let vendasAtualizadas = 0;

            resultados.forEach(item => {
                if (!item) return;
                if (item.vendedor && atualizarCelulaVendedor(item, item.vendedor)) {
                    vendedoresAtualizados += 1;
                }
                if (item.data_criacao && atualizarCelulaDataCriacao(item, item.data_criacao)) {
                    atualizadas += 1;
                }
                const vendas = Number(item.vendas);
                const fonteVendas = item.vendasFonte || item.vendas_fonte || '';
                if (Number.isFinite(vendas) && fonteVendasConfiavel(fonteVendas) && atualizarCelulaVendas(item, vendas, fonteVendas)) {
                    vendasAtualizadas += 1;
                }
            });

            const totalAtualizadas = (atualizadasElectron.datas || 0) + atualizadas;
            const totalVendedores = vendedoresElectron + vendedoresAtualizados;
            const totalVendas = (atualizadasElectron.vendas || 0) + vendasAtualizadas;
            mlPrimeiraPaginaStatusEl.textContent = (totalAtualizadas || totalVendedores || totalVendas)
                ? `${statusAnterior} Dados encontrados: ${totalVendedores} vendedor(es), ${totalAtualizadas} data(s) e ${totalVendas} venda(s).`
                : `${statusAnterior} Não foi possível identificar vendedor ou data de criação no código-fonte dos anúncios.`;
        }

        function abrirMercadoLivreExterno() {
            const url = normalizarUrl(mlUrlInput.value);
            mlUrlInput.value = url;
            window.open(url, '_blank', 'noopener');
        }
