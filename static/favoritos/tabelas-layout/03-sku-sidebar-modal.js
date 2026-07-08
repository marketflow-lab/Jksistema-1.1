        function selecionarLinhaAnuncio(item) {
            if (!item) return null;
            const escapeCss = window.CSS && CSS.escape ? CSS.escape : (value) => String(value).replace(/["\\]/g, '\\$&');
            const selectors = [];
            if (item.id) selectors.push(`tr[data-item-id="${escapeCss(item.id)}"]`);
            if (item.url) selectors.push(`tr[data-url="${escapeCss(item.url || '')}"]`);
            return selectors.map(selector => document.querySelector(selector)).find(Boolean) || null;
        }

        function encontrarAnuncioPrimeiraPaginaAtual(item) {
            if (!item) return null;
            const id = String(item.id || extrairItemIdAnuncio(item.url) || '').toUpperCase();
            const url = item.url ? String(item.url).split('#')[0] : '';
            return (mlAnunciosPrimeiraPaginaAtuais || []).find(anuncio => {
                const anuncioId = String(anuncio.id || extrairItemIdAnuncio(anuncio.url) || '').toUpperCase();
                const anuncioUrl = anuncio.url ? String(anuncio.url).split('#')[0] : '';
                return (id && anuncioId === id) || (url && anuncioUrl === url);
            }) || item;
        }

        function atualizarCelulaMediaVendas(item) {
            const alvo = encontrarAnuncioPrimeiraPaginaAtual(item);
            if (!alvo) return false;
            const metrica = calcularMetricasMediaVendas(alvo);
            alvo.media_vendas_mensal = Number.isFinite(metrica.media) ? metrica.media : null;
            alvo.meses_desde_criacao = Number.isFinite(metrica.meses) ? metrica.meses : null;

            const row = selecionarLinhaAnuncio(alvo);
            const cell = row ? row.querySelector('.ml-media-vendas') : null;
            if (cell) {
                cell.textContent = formatarMediaVendas(alvo);
                cell.title = Number.isFinite(metrica.media)
                    ? `Vendas: ${metrica.vendas}. Idade: ${formatarMesesMedia(metrica.meses)}.`
                    : 'Aguardando vendas e data de criação.';
            }
            if (row) {
                row.dataset.mediaVendas = Number.isFinite(metrica.media) ? String(metrica.media) : '';
            }
            return !!cell;
        }

        function dividirSkusMl(valor) {
            return String(valor || '')
                .split(/[,;|\n]+/)
                .map(item => item.trim())
                .filter(Boolean);
        }

        function extrairSkusAtributosMl(lista) {
            const preferenciais = [];
            const fallback = [];
            (lista || []).forEach(attr => {
                if (!attr || typeof attr !== 'object') return;
                const attrId = String(attr.id || attr.name || '').trim().toUpperCase();
                const destino = attrId === 'SELLER_SKU' || attrId === 'SKU'
                    ? preferenciais
                    : attrId === 'SELLER_CUSTOM_FIELD'
                    ? fallback
                    : null;
                if (!destino) return;
                dividirSkusMl(attr.value_name || attr.value_id || attr.value || '').forEach(sku => destino.push(sku));
            });
            return preferenciais.length ? preferenciais : fallback;
        }

        function temSkuOficialAtributosMl(lista) {
            return (lista || []).some(attr => {
                if (!attr || typeof attr !== 'object') return false;
                const attrId = String(attr.id || attr.name || '').trim().toUpperCase();
                if (attrId !== 'SELLER_SKU' && attrId !== 'SKU') return false;
                return dividirSkusMl(attr.value_name || attr.value_id || attr.value || '').length > 0;
            });
        }

        function extrairSkusAnuncioMl(anuncio) {
            const skus = [];
            const adicionar = (valor) => {
                dividirSkusMl(valor).forEach(sku => {
                    if (sku && !skus.some(item => item.toLowerCase() === sku.toLowerCase())) {
                        skus.push(sku);
                    }
                });
            };

            const camposSkuOficiais = [
                'sku', 'SKU', 'seller_sku', 'sellerSku', 'codigo', 'codigo_sku', 'item_sku'
            ];
            const camposSkuFallback = ['seller_custom_field', 'sellerCustomField'];
            const camposSkuComFallback = [...camposSkuOficiais, ...camposSkuFallback];
            const adicionarCampos = (obj) => {
                const temOficial = camposSkuOficiais.some(campo => obj && dividirSkusMl(obj[campo]).length)
                    || temSkuOficialAtributosMl(obj && obj.attributes)
                    || temSkuOficialAtributosMl(obj && obj.attribute_combinations);
                (temOficial ? camposSkuOficiais : camposSkuComFallback).forEach(campo => adicionar(obj && obj[campo]));
            };

            adicionarCampos(anuncio);

            extrairSkusAtributosMl(anuncio && anuncio.attributes).forEach(adicionar);
            extrairSkusAtributosMl(anuncio && anuncio.attribute_combinations).forEach(adicionar);

            (anuncio && anuncio.variations || []).forEach(variacao => {
                adicionarCampos(variacao);
                extrairSkusAtributosMl(variacao && variacao.attributes).forEach(adicionar);
                extrairSkusAtributosMl(variacao && variacao.attribute_combinations).forEach(adicionar);
            });

            return skus;
        }

        function numeroOrdenacaoSkuMl(sku) {
            const grupos = String(sku || '').match(/\d+/g);
            if (!grupos) return null;
            const numero = Number(grupos.join('').slice(0, 15));
            return Number.isFinite(numero) ? numero : null;
        }

        function compararItensSkuSidebar(a, b) {
            const numeroA = numeroOrdenacaoSkuMl(a.sku);
            const numeroB = numeroOrdenacaoSkuMl(b.sku);
            if (numeroA !== null && numeroB !== null && numeroA !== numeroB) {
                return numeroA - numeroB;
            }
            if (numeroA !== null && numeroB === null) return -1;
            if (numeroA === null && numeroB !== null) return 1;
            const texto = String(a.sku || '').localeCompare(String(b.sku || ''), 'pt-BR', {
                numeric: true,
                sensitivity: 'base'
            });
            if (texto) return texto;
            return (a.index || 0) - (b.index || 0);
        }

        function chaveSkuSidebarMercadoLivre(sku, loja = '') {
            const skuKey = (normalizarSkuBuscaMl(sku) || String(sku || '').trim().toLowerCase()).toLowerCase();
            if (!skuKey) return '';
            const lojaKey = skuNormalizarLoja(loja);
            return lojaKey ? `${lojaKey}::${skuKey}` : skuKey;
        }

        function mesclarSkusAnunciosMercadoLivre(novos) {
            const listaNovos = Array.isArray(novos) ? novos : [];
            if (!listaNovos.length) return false;
            const mapa = new Map();
            (mlSkusAnunciosLojaAtual || []).forEach(item => {
                const chave = chaveSkuSidebarMercadoLivre(item && item.sku, item && (item.loja || item.loja_sync));
                if (chave) mapa.set(chave, item);
            });
            let mudou = false;
            listaNovos.forEach(item => {
                const sku = String(item && item.sku || '').trim();
                const chave = chaveSkuSidebarMercadoLivre(sku, item && (item.loja || item.loja_sync));
                if (!sku || !chave) return;
                const atual = mapa.get(chave);
                if (!atual) {
                    mapa.set(chave, item);
                    mudou = true;
                    return;
                }
                if ((item.sku_forcado_busca || item.sku_exato_busca) && sku && String(atual.sku || '').trim() !== sku) {
                    atual.sku_api_original = atual.sku_api_original || atual.sku;
                    atual.sku = sku;
                    mudou = true;
                }
                const idsAtuais = new Set(Array.isArray(atual.item_ids) ? atual.item_ids : []);
                (Array.isArray(item.item_ids) ? item.item_ids : []).forEach(id => {
                    if (id && !idsAtuais.has(id)) {
                        idsAtuais.add(id);
                        mudou = true;
                    }
                });
                atual.item_ids = Array.from(idsAtuais);
                atual.total_anuncios = Math.max(Number(atual.total_anuncios || 0), Number(item.total_anuncios || 0), atual.item_ids.length);
                if (!atual.titulo && item.titulo) atual.titulo = item.titulo;
                const imagemNova = obterImagemAnuncioFavoritos(item);
                if (imagemNova && !obterImagemAnuncioFavoritos(atual)) {
                    atual.imagem = imagemNova;
                    atual.thumbnail = imagemNova;
                    mudou = true;
                }
                ['url', 'permalink', 'link'].forEach(campo => {
                    if (!atual[campo] && item[campo]) {
                        atual[campo] = item[campo];
                        mudou = true;
                    }
                });
                if (!Array.isArray(atual.links) && Array.isArray(item.links)) {
                    atual.links = item.links.slice(0, 12);
                    mudou = true;
                }
                if (!Array.isArray(atual.anuncios) && Array.isArray(item.anuncios)) {
                    atual.anuncios = item.anuncios.slice(0, 12);
                    mudou = true;
                }
            });
            if (mudou) {
                mlSkusAnunciosLojaAtual = Array.from(mapa.values()).sort(compararItensSkuSidebar);
                skuDados = mlSkusAnunciosLojaAtual
                    .map(item => skuNormalizarLinhaApiMercadoLivre(item, mlSkuLojaSelecionada))
                    .filter(item => skuObterSku(item));
                favoritosSalvarCacheSkusAtual();
            }
            return mudou;
        }

        function agendarBuscaRemotaSkuSidebarMercadoLivre(termoBusca) {
            const termo = String(termoBusca || '').trim();
            const loja = favoritosLojaSelecionadaParaApi();
            const chaveBusca = `${skuNormalizarLoja(loja)}:${normalizarSkuBuscaMl(termo) || normalizarTextoMl(termo)}`;
            if (!loja || termo.length < 2 || !normalizarTextoMl(termo) || mlSkuBuscaRemotaCache.has(chaveBusca)) return;
            if (mlSkuBuscaRemotaTimer) clearTimeout(mlSkuBuscaRemotaTimer);
            mlSkuBuscaRemotaTimer = setTimeout(async () => {
                const runId = ++mlSkuBuscaRemotaRunId;
                mlSkuBuscaRemotaCache.add(chaveBusca);
                try {
                    const params = new URLSearchParams({ loja, sku: termo });
                    const response = await fetch(`/api/favoritos/ml/skus-anuncios?${params.toString()}`, {
                        headers: obterAuthHeaders(),
                        cache: 'no-store'
                    });
                    if (!response.ok) throw new Error(`HTTP ${response.status}`);
                    const data = await response.json();
                    if (runId !== mlSkuBuscaRemotaRunId) return;
                    if (mesclarSkusAnunciosMercadoLivre(data.skus || [])) {
                        renderizarSkuSidebarMercadoLivre();
                        renderizarFavoritosSkuSidebar();
                        renderizarHistoricoSkuSidebar();
                        mlSkuRenderizarCardsLojas();
                    }
                } catch (err) {
                    mlSkuBuscaRemotaCache.delete(chaveBusca);
                    console.warn('Nao foi possivel buscar SKU de variacao no servidor:', err);
                }
            }, 350);
        }

        function montarItensSkuSidebarMercadoLivre() {
            const vistos = new Set();
            const lojaFiltro = favoritosLojaSelecionadaParaApi();
            const itens = (mlSkusAnunciosLojaAtual || [])
                .filter(item => {
                    if (!lojaFiltro) return true;
                    const lojaItem = String(item && (item.loja || item.loja_sync) || '').trim();
                    if (!lojaItem) return !favoritosSkusTodasLojasCarregados;
                    return skuNormalizarLoja(lojaItem) === skuNormalizarLoja(lojaFiltro);
                })
                .map((item, index) => ({
                    sku: String(item && item.sku || '').trim(),
                    titulo: String(item && item.titulo || '').trim(),
                    loja: String(item && (item.loja || item.loja_sync) || lojaFiltro || '').trim(),
                    itemIds: Array.isArray(item && item.item_ids) ? item.item_ids : [],
                    item_ids: Array.isArray(item && item.item_ids) ? item.item_ids : [],
                    links: Array.isArray(item && item.links) ? item.links : [],
                    url: String(item && (item.url || item.permalink || item.link || (Array.isArray(item.links) ? item.links[0] : '')) || '').trim(),
                    permalink: String(item && (item.permalink || item.url || item.link || (Array.isArray(item.links) ? item.links[0] : '')) || '').trim(),
                    link: String(item && (item.link || item.permalink || item.url || (Array.isArray(item.links) ? item.links[0] : '')) || '').trim(),
                    imagem: obterImagemAnuncioFavoritos(item),
                    thumbnail: obterImagemAnuncioFavoritos(item),
                    anuncios: Array.isArray(item && item.anuncios) ? item.anuncios : [],
                    mlb: String(item && (item.mlb || item.id || item.item_id || '') || '').trim(),
                    totalAnuncios: Number(item && item.total_anuncios || 0),
                    pesquisa_1: String(item && (item.pesquisa_1 || item.pesquisa1 || item['Pesquisa 1']) || '').trim(),
                    pesquisa_2: String(item && (item.pesquisa_2 || item.pesquisa2 || item['Pesquisa 2']) || '').trim(),
                    pesquisa_3: String(item && (item.pesquisa_3 || item.pesquisa3 || item['Pesquisa 3']) || '').trim(),
                    index,
                    chave: chaveSkuSidebarMercadoLivre(item && item.sku, item && (item.loja || item.loja_sync))
                }))
                .filter(item => {
                    if (!item.sku || vistos.has(item.chave)) return false;
                    vistos.add(item.chave);
                    return true;
                });
            return itens.sort(compararItensSkuSidebar);
        }

        function filtrarItensSkuSidebarMercadoLivre(itens = montarItensSkuSidebarMercadoLivre(), termoForcado = null) {
            const termoBase = termoForcado === null || termoForcado === undefined
                ? (mlSkuSidebarFiltro || (mlSkuSidebarSearchEl && mlSkuSidebarSearchEl.value) || '')
                : termoForcado;
            const termo = normalizarTextoMl(termoBase);
            const termoSku = normalizarSkuBuscaMl(termoBase);
            if (!termo && !termoSku) return itens;
            return (itens || []).filter(item => {
                const skuTexto = normalizarTextoMl(item && item.sku);
                const skuCompacto = normalizarSkuBuscaMl(item && item.sku);
                return (!!termo && skuTexto.includes(termo)) || (!!termoSku && skuCompacto.includes(termoSku));
            });
        }

        function skuSidebarTemSkuExatoBusca(itens, termoBusca) {
            const termo = String(termoBusca || '').trim();
            if (!termo) return true;
            const termoLiteral = termo.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
            const termoTexto = normalizarTextoMl(termo);
            return (itens || []).some(item => {
                const sku = String(item && item.sku || '').trim();
                if (!sku) return false;
                const skuLiteral = sku.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
                return skuLiteral === termoLiteral || normalizarTextoMl(sku) === termoTexto;
            });
        }

        function deveBuscarSkuRemotoParaTermo(itens, termoBusca) {
            const termo = String(termoBusca || '').trim();
            if (!termo || termo.length < 2 || !normalizarTextoMl(termo)) return false;
            if (!(itens || []).length) return true;
            return !!normalizarSkuBuscaMl(termo) && !skuSidebarTemSkuExatoBusca(itens, termo);
        }

        function buscarSkuExatoHistoricoSidebar(termoBusca) {
            const termo = String(termoBusca || '').trim();
            if (!termo) return '';
            const termoLiteral = termo.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
            const termoTexto = normalizarTextoMl(termo);
            for (const entrada of lerHistoricoFavoritos()) {
                for (const grupo of (entrada && entrada.grupos) || []) {
                    const sku = String(grupo && grupo.sku || '').trim();
                    if (!sku) continue;
                    const skuLiteral = sku.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
                    if (skuLiteral === termoLiteral || normalizarTextoMl(sku) === termoTexto) {
                        return sku;
                    }
                }
            }
            return '';
        }

        function aplicarSkuExatoHistoricoSidebar(itens, termoBusca) {
            const skuExato = buscarSkuExatoHistoricoSidebar(termoBusca);
            const termoCompacto = normalizarSkuBuscaMl(termoBusca);
            if (!skuExato || !termoCompacto) return itens;
            const skuExatoCompacto = normalizarSkuBuscaMl(skuExato);
            return (itens || []).map(item => {
                const skuAtual = String(item && item.sku || '').trim();
                if (!skuAtual || normalizarSkuBuscaMl(skuAtual) !== termoCompacto || normalizarSkuBuscaMl(skuAtual) === skuExatoCompacto && skuAtual === skuExato) {
                    return item;
                }
                return {
                    ...item,
                    sku_api_original: item.sku_api_original || skuAtual,
                    sku: skuExato,
                    chave: chaveSkuSidebarMercadoLivre(skuExato, item && item.loja)
                };
            });
        }

        function extrairMlbsItemSkuSidebar(item) {
            const ids = [];
            const vistos = new Set();
            const adicionar = (valor) => {
                if (!valor) return;
                if (Array.isArray(valor)) {
                    valor.forEach(adicionar);
                    return;
                }
                if (typeof valor === 'object') {
                    adicionar(valor.id || valor.mlb || valor.item_id || valor.itemId);
                    return;
                }
                String(valor).split(/[,;|\s]+/).forEach(parte => {
                    const id = String(parte || '').trim().toUpperCase().replace(/[^A-Z0-9]/g, '');
                    if (!id.startsWith('MLB') || vistos.has(id)) return;
                    vistos.add(id);
                    ids.push(id);
                });
            };
            adicionar(item && item.itemIds);
            adicionar(item && item.item_ids);
            adicionar(item && item.mlbs);
            adicionar(item && item.mlb);
            adicionar(item && item.anuncios);
            return ids.slice(0, 80);
        }

        function encontrarItemSkuSidebarMercadoLivre(sku, loja = '') {
            const chaveSku = skuChaveSku(sku);
            if (!chaveSku) return null;
            const lojaNorm = skuNormalizarLoja(loja);
            const itens = montarItensSkuSidebarMercadoLivre();
            return itens.find(item => skuChaveSku(item && item.sku) === chaveSku
                && (!lojaNorm || skuNormalizarLoja(item && item.loja) === lojaNorm))
                || itens.find(item => skuChaveSku(item && item.sku) === chaveSku)
                || null;
        }

        function executarRenderFavoritosSeguro(nome, callback) {
            try {
                return callback();
            } catch (err) {
                console.warn(`Falha ao renderizar ${nome}:`, err);
                return null;
            }
        }

        function carregarMaisSkusSidebarMercadoLivre() {
            mlSkuSidebarRenderLimit += ML_SKU_SIDEBAR_PAGE_SIZE;
            renderizarSkuSidebarMercadoLivre();
        }

        function atualizarContadorSkuSidebarSelecionados(itens = montarItensSkuSidebarMercadoLivre()) {
            if (!mlSkuSelectedCountEl && !mlSkuSelectAllEl && !mlSkuFazerFavoritosEl) return;
            const chaves = (itens || []).map(item => item.chave).filter(Boolean);
            const selecionados = chaves.filter(chave => mlSkuSidebarSelecionados.has(chave)).length;
            if (mlSkuSelectedCountEl) {
                mlSkuSelectedCountEl.textContent = `${selecionados} de ${chaves.length} SKU(s) selecionado(s)`;
            }
            if (mlSkuSelectAllEl) {
                mlSkuSelectAllEl.disabled = chaves.length === 0 || mlFavoritosEmExecucao;
                mlSkuSelectAllEl.textContent = chaves.length > 0 && selecionados === chaves.length
                    ? 'Limpar seleção'
                    : 'Selecionar todos';
            }
            if (mlSkuFazerFavoritosEl) {
                mlSkuFazerFavoritosEl.disabled = selecionados === 0 || mlFavoritosEmExecucao;
                mlSkuFazerFavoritosEl.textContent = mlFavoritosEmExecucao ? 'Fazendo...' : 'Fazer favoritos';
            }
            if (mlSkuUsarIaFavoritosEl) {
                mlSkuUsarIaFavoritosEl.disabled = mlFavoritosEmExecucao;
                const labelIa = mlSkuUsarIaFavoritosEl.closest('.ml-sku-ia-toggle');
                if (labelIa) labelIa.classList.toggle('is-disabled', mlFavoritosEmExecucao);
            }
            const statusJob = String(mlFavoritosJobUltimoStatus && mlFavoritosJobUltimoStatus.status || '').toLowerCase();
            const pausado = statusJob === 'paused' || !!mlFavoritosPausado;
            const cancelando = mlFavoritosCancelado || statusJob === 'canceling';
            if (mlSkuPausarFavoritosEl) {
                mlSkuPausarFavoritosEl.classList.toggle('hidden', !mlFavoritosEmExecucao || pausado || cancelando);
                mlSkuPausarFavoritosEl.disabled = !mlFavoritosEmExecucao || pausado || cancelando;
            }
            if (mlSkuRetomarFavoritosEl) {
                mlSkuRetomarFavoritosEl.classList.toggle('hidden', !mlFavoritosEmExecucao || !pausado || cancelando);
                mlSkuRetomarFavoritosEl.disabled = !mlFavoritosEmExecucao || !pausado || cancelando;
            }
            if (mlSkuCancelarFavoritosEl) {
                mlSkuCancelarFavoritosEl.classList.toggle('hidden', !mlFavoritosEmExecucao);
                mlSkuCancelarFavoritosEl.disabled = !mlFavoritosEmExecucao || cancelando;
                mlSkuCancelarFavoritosEl.textContent = cancelando ? 'Cancelando...' : 'Cancelar favoritos';
            }
        }

        function atualizarFiltroAzulFavoritos() {
            const ativo = !!mlFavoritosEmExecucao;
            if (mlBrowserBlueFilterEl) {
                mlBrowserBlueFilterEl.classList.add('hidden');
            }
            if (mlBrowserFrameWrapEl) {
                mlBrowserFrameWrapEl.classList.toggle('is-favoritos-running', ativo);
            }
            if (mlWorkModalEl) {
                mlWorkModalEl.classList.toggle('is-favoritos-running', ativo);
                mlWorkModalEl.classList.toggle('is-browser-only', ativo || mlWorkModalEl.dataset.browserOnly === '1');
            }
            if (mlWorkModalCloseEl) {
                mlWorkModalCloseEl.disabled = false;
                mlWorkModalCloseEl.textContent = ativo ? 'Ocultar' : 'Fechar';
            }
            const statusJob = String(mlFavoritosJobUltimoStatus && mlFavoritosJobUltimoStatus.status || '').toLowerCase();
            const pausado = statusJob === 'paused' || !!mlFavoritosPausado;
            const cancelando = mlFavoritosCancelado || statusJob === 'canceling';
            if (mlWorkModalPauseEl) {
                mlWorkModalPauseEl.classList.toggle('hidden', !ativo || pausado || cancelando);
                mlWorkModalPauseEl.disabled = !ativo || pausado || cancelando;
            }
            if (mlWorkModalResumeEl) {
                mlWorkModalResumeEl.classList.toggle('hidden', !ativo || !pausado || cancelando);
                mlWorkModalResumeEl.disabled = !ativo || !pausado || cancelando;
            }
            if (mlWorkModalCancelEl) {
                mlWorkModalCancelEl.classList.toggle('hidden', !ativo);
                mlWorkModalCancelEl.disabled = !ativo || cancelando;
                mlWorkModalCancelEl.textContent = cancelando ? 'Cancelando...' : 'Cancelar favoritos';
            }
            atualizarAnimacaoAzulNoNavegadorMl(ativo);
            if (!ativo) atualizarStatusFavoritosNoNavegadorMl('', false);
        }

        function atualizarAnimacaoAzulNoNavegadorMl(ativo) {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') return;
            const script = `
                (() => {
                    document.getElementById('jk-favoritos-blue-overlay')?.remove();
                    document.getElementById('jk-favoritos-blue-overlay-style')?.remove();
                    return true;
                })();
            `;
            try {
                const resultado = mlWebviewEl.executeJavaScript(script);
                if (resultado && typeof resultado.catch === 'function') resultado.catch(() => {});
            } catch (_err) {}
        }

        function atualizarStatusFavoritosNoNavegadorMl(mensagem, ativo) {
            return window.FavoritosV2?.ui?.statusModal?.atualizarStatusFavoritosNoNavegadorMl?.(mensagem, ativo);
        }

        function alternarSelecaoTodosSkuSidebar() {
            const itens = filtrarItensSkuSidebarMercadoLivre();
            const chaves = itens.map(item => item.chave).filter(Boolean);
            if (!chaves.length) return;
            const todosSelecionados = chaves.every(chave => mlSkuSidebarSelecionados.has(chave));
            chaves.forEach(chave => {
                if (todosSelecionados) {
                    mlSkuSidebarSelecionados.delete(chave);
                } else {
                    mlSkuSidebarSelecionados.add(chave);
                }
            });
            renderizarSkuSidebarMercadoLivre();
        }

        function obterPesquisasSkuSidebar(item) {
            const cadastro = obterCadastroSkuFavoritos(item && item.sku, item && item.loja);
            return [1, 2, 3].map(numero => {
                const valorCadastro = String(cadastro ? skuObterPesquisa(cadastro, numero) : '').trim();
                if (valorCadastro) return valorCadastro;
                const campos = numero === 3
                    ? ['pesquisa_3', 'pesquisa3', 'Pesquisa 3']
                    : (numero === 2
                        ? ['pesquisa_2', 'pesquisa2', 'Pesquisa 2']
                        : ['pesquisa_1', 'pesquisa1', 'Pesquisa 1']);
                return skuTexto(item, campos, '').trim();
            });
        }

        function criarBalaoPesquisasSkuSidebar(item) {
            const pesquisas = obterPesquisasSkuSidebar(item);
            const tooltip = document.createElement('span');
            tooltip.className = 'ml-sku-sidebar-search-tooltip';
            tooltip.setAttribute('role', 'tooltip');

            const titulo = document.createElement('strong');
            titulo.textContent = 'Campos de pesquisa cadastrados';
            tooltip.appendChild(titulo);

            if (!pesquisas.some(Boolean)) {
                const vazio = document.createElement('span');
                vazio.textContent = 'Nenhum campo de pesquisa preenchido.';
                tooltip.appendChild(vazio);
                return tooltip;
            }

            pesquisas.forEach((valor, index) => {
                const linha = document.createElement('span');
                linha.textContent = `Pesquisa ${index + 1}: ${valor || 'Nao preenchida'}`;
                tooltip.appendChild(linha);
            });
            return tooltip;
        }

        function posicionarBalaoPesquisasSkuSidebar(tooltip, event, anchor) {
            if (!tooltip) return;
            const margem = 10;
            const largura = tooltip.offsetWidth || 260;
            const altura = tooltip.offsetHeight || 90;
            const origem = event && Number.isFinite(event.clientX)
                ? { x: event.clientX, y: event.clientY }
                : (() => {
                    const rect = anchor && anchor.getBoundingClientRect ? anchor.getBoundingClientRect() : { left: 0, top: 0, right: 0 };
                    return { x: rect.right, y: rect.top + 8 };
                })();
            let left = origem.x + 14;
            let top = origem.y + 12;
            if (left + largura + margem > window.innerWidth) {
                left = origem.x - largura - 14;
            }
            if (top + altura + margem > window.innerHeight) {
                top = origem.y - altura - 12;
            }
            left = Math.max(margem, Math.min(left, window.innerWidth - largura - margem));
            top = Math.max(margem, Math.min(top, window.innerHeight - altura - margem));
            tooltip.style.setProperty('--ml-sku-tooltip-left', `${Math.round(left)}px`);
            tooltip.style.setProperty('--ml-sku-tooltip-top', `${Math.round(top)}px`);
        }

        let skuPesquisaModalState = { row: null, sku: '', loja: '', item: null };

        function garantirModalPesquisasSku() {
            let modal = document.getElementById('sku-search-modal');
            if (modal) return modal;
            modal = document.createElement('div');
            modal.id = 'sku-search-modal';
            modal.className = 'sku-search-modal hidden';
            modal.setAttribute('role', 'dialog');
            modal.setAttribute('aria-modal', 'true');
            modal.setAttribute('aria-labelledby', 'sku-search-modal-title');
            modal.innerHTML = `
                <div class="sku-search-modal-dialog">
                    <div class="sku-search-modal-header">
                        <div>
                            <span class="sku-search-modal-kicker">Campos de pesquisa</span>
                            <h3 id="sku-search-modal-title" class="sku-search-modal-title"></h3>
                            <p id="sku-search-modal-subtitle" class="sku-search-modal-subtitle"></p>
                        </div>
                        <button id="sku-search-modal-close" class="sku-search-modal-close" type="button" aria-label="Fechar">×</button>
                    </div>
                    <div class="sku-search-modal-body">
                        <div id="sku-search-modal-product" class="sku-search-modal-product hidden">
                            <button id="sku-search-modal-photo-link" class="sku-search-modal-photo-link" type="button" title="Abrir anuncio do SKU">
                                <img id="sku-search-modal-photo" class="sku-search-modal-photo hidden" alt="Foto do SKU">
                                <span id="sku-search-modal-photo-placeholder" class="sku-search-modal-photo-placeholder">Sem foto</span>
                            </button>
                            <div class="sku-search-modal-product-copy">
                                <strong id="sku-search-modal-product-title" class="sku-search-modal-product-title"></strong>
                                <span id="sku-search-modal-product-meta" class="sku-search-modal-product-meta"></span>
                            </div>
                        </div>
                        <div class="sku-search-modal-field">
                            <label for="sku-search-modal-p1">Pesquisa 1</label>
                            <input id="sku-search-modal-p1" class="sku-pesquisa-input" type="text" autocomplete="off">
                        </div>
                        <div class="sku-search-modal-field">
                            <label for="sku-search-modal-p2">Pesquisa 2</label>
                            <input id="sku-search-modal-p2" class="sku-pesquisa-input" type="text" autocomplete="off">
                        </div>
                        <div class="sku-search-modal-field">
                            <label for="sku-search-modal-p3">Pesquisa 3</label>
                            <input id="sku-search-modal-p3" class="sku-pesquisa-input" type="text" autocomplete="off">
                        </div>
                        <div id="sku-search-modal-description" class="sku-search-modal-description hidden">
                            <div class="sku-search-modal-description-title">Descricao do anuncio</div>
                            <div id="sku-search-modal-description-meta" class="sku-search-modal-description-meta"></div>
                            <div id="sku-search-modal-description-text" class="sku-search-modal-description-text"></div>
                        </div>
                        <div id="sku-search-modal-status" class="sku-search-modal-status"></div>
                    </div>
                    <div class="sku-search-modal-footer">
                        <button id="sku-search-modal-desc" class="sku-search-modal-desc" type="button">Ver descricao</button>
                        <button id="sku-search-modal-ai" class="sku-search-modal-ai" type="button">Preencher com IA</button>
                        <button id="sku-search-modal-save" class="sku-search-modal-save" type="button">Salvar</button>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);
            modal.addEventListener('click', (event) => {
                if (event.target === modal) fecharModalPesquisasSku();
            });
            modal.querySelector('#sku-search-modal-close')?.addEventListener('click', fecharModalPesquisasSku);
            modal.querySelector('#sku-search-modal-save')?.addEventListener('click', salvarModalPesquisasSku);
            modal.querySelector('#sku-search-modal-ai')?.addEventListener('click', preencherModalPesquisasSkuIa);
            modal.querySelector('#sku-search-modal-desc')?.addEventListener('click', verDescricaoModalPesquisasSku);
            modal.querySelector('#sku-search-modal-photo-link')?.addEventListener('click', (event) => {
                const url = obterUrlAnuncioSkuModal(skuPesquisaModalState.item, skuPesquisaModalState.row);
                if (!url) return;
                event.preventDefault();
                event.stopPropagation();
                abrirAnuncioComAvantPro(url, event);
            });
            modal.querySelectorAll('input').forEach(input => {
                input.addEventListener('keydown', (event) => {
                    if (event.key === 'Escape') {
                        event.preventDefault();
                        fecharModalPesquisasSku();
                    }
                    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
                        event.preventDefault();
                        salvarModalPesquisasSku();
                    }
                });
            });
            return modal;
        }

        function elementosModalPesquisasSku() {
            const modal = garantirModalPesquisasSku();
            return {
                modal,
                title: modal.querySelector('#sku-search-modal-title'),
                subtitle: modal.querySelector('#sku-search-modal-subtitle'),
                p1: modal.querySelector('#sku-search-modal-p1'),
                p2: modal.querySelector('#sku-search-modal-p2'),
                p3: modal.querySelector('#sku-search-modal-p3'),
                product: modal.querySelector('#sku-search-modal-product'),
                photoLink: modal.querySelector('#sku-search-modal-photo-link'),
                photo: modal.querySelector('#sku-search-modal-photo'),
                photoPlaceholder: modal.querySelector('#sku-search-modal-photo-placeholder'),
                productTitle: modal.querySelector('#sku-search-modal-product-title'),
                productMeta: modal.querySelector('#sku-search-modal-product-meta'),
                status: modal.querySelector('#sku-search-modal-status'),
                desc: modal.querySelector('#sku-search-modal-desc'),
                descBox: modal.querySelector('#sku-search-modal-description'),
                descMeta: modal.querySelector('#sku-search-modal-description-meta'),
                descText: modal.querySelector('#sku-search-modal-description-text'),
                ai: modal.querySelector('#sku-search-modal-ai'),
                save: modal.querySelector('#sku-search-modal-save'),
            };
        }

        function construirUrlAnuncioFavoritosPorItemId(itemId) {
            const id = String(itemId || '').trim().toUpperCase().replace('-', '');
            const digitos = id.replace(/^MLB/i, '');
            if (!/^MLB\d+$/i.test(id) || digitos.length < 8) return '';
            return `https://produto.mercadolivre.com.br/${id.replace('MLB', 'MLB-')}`;
        }

        function normalizarUrlAnuncioSkuModal(valor) {
            const texto = String(valor || '').trim();
            if (!texto) return '';
            if (/^\/\//.test(texto)) return `https:${texto}`;
            if (/^https?:\/\//i.test(texto)) return texto;
            const id = extrairItemIdAnuncio(texto) || (/^MLB-?\d{6,}$/i.test(texto) ? texto : '');
            return id ? construirUrlAnuncioFavoritosPorItemId(id) : '';
        }

        function coletarFontesAnuncioSkuModal(item, row) {
            const fontes = [];
            const vistos = new Set();
            const adicionar = (fonte, profundidade = 0) => {
                if (!fonte || profundidade > 2) return;
                if (Array.isArray(fonte)) {
                    fonte.forEach(valor => adicionar(valor, profundidade + 1));
                    return;
                }
                if (typeof fonte !== 'object' || vistos.has(fonte)) return;
                vistos.add(fonte);
                fontes.push(fonte);
                ['anuncios', 'items', 'itens', 'results'].forEach(campo => {
                    if (Array.isArray(fonte[campo])) adicionar(fonte[campo], profundidade + 1);
                });
            };
            adicionar(item);
            adicionar(row);
            return fontes;
        }

        function obterImagemSkuModal(item, row) {
            for (const fonte of coletarFontesAnuncioSkuModal(item, row)) {
                const imagem = obterImagemAnuncioFavoritos(fonte);
                if (imagem) return imagem;
            }
            return '';
        }

        function obterUrlAnuncioSkuModal(item, row) {
            const fontes = coletarFontesAnuncioSkuModal(item, row);
            const camposUrl = ['url', 'permalink', 'link', 'descricao_ml_link', 'item_permalink'];
            for (const fonte of fontes) {
                for (const campo of camposUrl) {
                    const url = normalizarUrlAnuncioSkuModal(fonte && fonte[campo]);
                    if (url) return url;
                }
                if (Array.isArray(fonte && fonte.links)) {
                    for (const link of fonte.links) {
                        const url = normalizarUrlAnuncioSkuModal(link);
                        if (url) return url;
                    }
                }
            }

            const ids = [
                ...(extrairMlbsItemSkuSidebar(item) || []),
                ...(Array.isArray(row && row.item_ids) ? row.item_ids : []),
                ...(Array.isArray(row && row.itemIds) ? row.itemIds : []),
                row && row.descricao_ml_item_id,
            ];
            for (const fonte of fontes) {
                ids.push(fonte && (fonte.id || fonte.mlb || fonte.item_id || fonte.itemId));
            }
            for (const idRaw of ids) {
                const id = extrairItemIdAnuncio(idRaw) || String(idRaw || '').trim();
                const url = construirUrlAnuncioFavoritosPorItemId(id);
                if (url) return url;
            }
            return '';
        }

        function sincronizarDadosAnuncioLinhaSku(row, item) {
            if (!row || !item) return row;
            const ids = new Set([
                ...(Array.isArray(row.item_ids) ? row.item_ids : []),
                ...(Array.isArray(item.itemIds) ? item.itemIds : []),
                ...(Array.isArray(item.item_ids) ? item.item_ids : []),
                ...(extrairMlbsItemSkuSidebar(item) || []),
            ].map(id => String(id || '').trim()).filter(Boolean));
            if (ids.size) row.item_ids = Array.from(ids);

            const imagem = obterImagemSkuModal(item, row);
            if (imagem && !obterImagemAnuncioFavoritos(row)) {
                row.imagem = imagem;
                row.thumbnail = imagem;
            }

            const url = obterUrlAnuncioSkuModal(item, row);
            if (url && !(row.url || row.permalink || row.link || row.descricao_ml_link)) {
                row.url = url;
                row.permalink = url;
                row.link = url;
            }
            if (Array.isArray(item.links) && item.links.length && !Array.isArray(row.links)) {
                row.links = item.links.slice(0, 12);
            }
            if (Array.isArray(item.anuncios) && item.anuncios.length && !Array.isArray(row.anuncios)) {
                row.anuncios = item.anuncios.slice(0, 12);
            }
            return row;
        }

        function atualizarFotoModalPesquisasSku(item, row) {
            const { product, photoLink, photo, photoPlaceholder, productTitle, productMeta } = elementosModalPesquisasSku();
            if (!product || !photoLink || !photo || !photoPlaceholder) return;
            const imagem = obterImagemSkuModal(item, row);
            const url = obterUrlAnuncioSkuModal(item, row);
            if (!imagem && !url) {
                product.classList.add('hidden');
                return;
            }

            product.classList.remove('hidden');
            photoLink.disabled = !url;
            photoLink.title = url ? 'Abrir anuncio do SKU' : 'Nenhum link de anuncio encontrado';
            if (imagem) {
                photo.onerror = () => {
                    photo.classList.add('hidden');
                    photoPlaceholder.textContent = url ? 'Abrir anuncio' : 'Sem foto';
                    photoPlaceholder.classList.remove('hidden');
                };
                photo.src = imagem;
                photo.alt = `Foto do SKU ${skuObterSku(row) || (item && item.sku) || ''}`.trim();
                photo.classList.remove('hidden');
                photoPlaceholder.classList.add('hidden');
            } else {
                photo.removeAttribute('src');
                photo.classList.add('hidden');
                photoPlaceholder.textContent = url ? 'Abrir anuncio' : 'Sem foto';
                photoPlaceholder.classList.remove('hidden');
            }

            if (productTitle) {
                productTitle.textContent = skuObterProduto(row)
                    || String(item && (item.titulo || item.title || item.nome || item.produto) || '').trim()
                    || 'SKU encontrado em anuncio ativo';
            }
            if (productMeta) {
                const ids = [
                    ...(extrairMlbsItemSkuSidebar(item) || []),
                    ...(Array.isArray(row && row.item_ids) ? row.item_ids : []),
                ].filter(Boolean);
                productMeta.textContent = [
                    ids.length ? `Anuncio: ${ids[0]}` : '',
                    url ? 'Clique na foto para abrir no Mercado Livre.' : '',
                ].filter(Boolean).join(' | ');
            }
        }

        function definirStatusModalPesquisasSku(texto, erro = false) {
            const { status } = elementosModalPesquisasSku();
            if (!status) return;
            status.textContent = texto || '';
            status.classList.toggle('is-error', !!erro);
        }

        function obterDescricaoModalPesquisasSku(row) {
            return String(row && (row.descricao_ml || row.descricao || row['descrição'] || row.description) || '').trim();
        }

        function atualizarDescricaoModalPesquisasSku(row, mostrar = false, mensagemVazia = '') {
            const { desc, descBox, descMeta, descText } = elementosModalPesquisasSku();
            if (!descBox || !descText) return;
            if (!mostrar) {
                descBox.classList.add('hidden');
                if (desc) desc.textContent = 'Ver descricao';
                return;
            }
            const descricao = obterDescricaoModalPesquisasSku(row);
            const metaPartes = [
                row && row.descricao_ml_item_id ? `Anuncio: ${row.descricao_ml_item_id}` : '',
                row && row.descricao_ml_loja ? `Loja: ${row.descricao_ml_loja}` : ''
            ].filter(Boolean);
            descBox.classList.remove('hidden');
            if (desc) desc.textContent = 'Ocultar descricao';
            if (descMeta) descMeta.textContent = metaPartes.join(' | ');
            descText.textContent = descricao || mensagemVazia || 'Descricao ainda nao carregada para este SKU.';
            descText.classList.toggle('is-empty', !descricao);
        }

        async function verDescricaoModalPesquisasSku() {
            const row = skuPesquisaModalState.row;
            const sku = skuPesquisaModalState.sku;
            if (!row || !sku) return;
            const { descBox, desc } = elementosModalPesquisasSku();
            if (descBox && !descBox.classList.contains('hidden')) {
                atualizarDescricaoModalPesquisasSku(row, false);
                return;
            }

            const lojaModal = skuPesquisaModalState.loja || skuObterLoja(row);
            let rowAtual = obterCadastroSkuFavoritos(sku, lojaModal) || row;
            let descricao = obterDescricaoModalPesquisasSku(rowAtual);
            if (!descricao && rowAtual.descricao_ml_status !== 'loading') {
                if (desc) {
                    desc.disabled = true;
                    desc.textContent = 'Buscando...';
                }
                definirStatusModalPesquisasSku('Buscando descricao no Mercado Livre...');
                await skuBuscarDescricaoManual(rowAtual, null);
                rowAtual = obterCadastroSkuFavoritos(sku, lojaModal) || rowAtual;
                skuPesquisaModalState.row = rowAtual;
                descricao = obterDescricaoModalPesquisasSku(rowAtual);
                if (desc) desc.disabled = false;
            }

            if (descricao) {
                atualizarDescricaoModalPesquisasSku(rowAtual, true);
                definirStatusModalPesquisasSku('Descricao carregada.');
            } else {
                const erro = rowAtual.descricao_ml_erro || 'Nenhuma descricao encontrada para este SKU.';
                atualizarDescricaoModalPesquisasSku(rowAtual, true, erro);
                definirStatusModalPesquisasSku(erro, true);
            }
        }

        function fecharModalPesquisasSku() {
            const modal = document.getElementById('sku-search-modal');
            if (modal) modal.classList.add('hidden');
            document.body.classList.remove('sku-search-modal-open');
            skuPesquisaModalState = { row: null, sku: '', loja: '', item: null };
        }

        function obterOuCriarLinhaPesquisaSku(item) {
            const sku = String(item && item.sku || '').trim();
            if (!sku) return null;
            const lojaItem = String(item && (item.loja || item.loja_sync) || '').trim();
            let row = obterCadastroSkuFavoritos(sku, lojaItem);
            if (row) {
                if (Array.isArray(item && item.itemIds) && item.itemIds.length && !skuItemIdsDescricao(row).length) {
                    row.item_ids = item.itemIds;
                }
                return sincronizarDadosAnuncioLinhaSku(row, item);
            }
            row = {
                sku,
                nome: String(item && (item.titulo || item.nome || item.produto) || '').trim(),
                loja_sync: lojaItem || favoritosLojaSelecionadaParaApi() || '',
                item_ids: Array.isArray(item && item.itemIds) ? item.itemIds : [],
                total_anuncios: item && item.totalAnuncios,
                descricao_ml: String(item && (item.descricao_ml || item.descricao || item.description) || '').trim(),
                imagem: obterImagemSkuModal(item, null),
                thumbnail: obterImagemSkuModal(item, null),
                url: obterUrlAnuncioSkuModal(item, null),
                permalink: obterUrlAnuncioSkuModal(item, null),
                link: obterUrlAnuncioSkuModal(item, null),
                links: Array.isArray(item && item.links) ? item.links.slice(0, 12) : [],
                anuncios: Array.isArray(item && item.anuncios) ? item.anuncios.slice(0, 12) : [],
            };
            [1, 2, 3].forEach(numero => {
                row[`pesquisa_${numero}`] = obterPesquisasSkuSidebar(item)[numero - 1] || '';
            });
            sincronizarDadosAnuncioLinhaSku(row, item);
            if (Array.isArray(skuDados)) skuDados.push(row);
            return row;
        }

        function preencherCamposModalPesquisasSku(row) {
            const { p1, p2, p3 } = elementosModalPesquisasSku();
            if (p1) p1.value = skuObterPesquisa(row, 1);
            if (p2) p2.value = skuObterPesquisa(row, 2);
            if (p3) p3.value = skuObterPesquisa(row, 3);
        }

        function aplicarCamposModalNaLinha(row) {
            const { p1, p2, p3 } = elementosModalPesquisasSku();
            row.pesquisa_1 = String(p1?.value || '').trim();
            row.pesquisa_2 = String(p2?.value || '').trim();
            row.pesquisa_3 = String(p3?.value || '').trim();
        }

        function abrirModalPesquisasSkuSidebar(item, event) {
            if (event) {
                event.preventDefault();
                event.stopPropagation();
            }
            const row = obterOuCriarLinhaPesquisaSku(item);
            const sku = skuObterSku(row);
            if (!row || !sku) return;
            skuPesquisaModalState = { row, sku, loja: skuObterLoja(row), item };
            const { modal, title, subtitle, p1 } = elementosModalPesquisasSku();
            if (title) title.textContent = `SKU ${sku}`;
            if (subtitle) subtitle.textContent = skuObterProduto(row);
            preencherCamposModalPesquisasSku(row);
            atualizarFotoModalPesquisasSku(item, row);
            atualizarDescricaoModalPesquisasSku(row, false);
            definirStatusModalPesquisasSku('Edite os campos ou use a IA para preencher automaticamente.');
            modal.classList.remove('hidden');
            document.body.classList.add('sku-search-modal-open');
            setTimeout(() => {
                if (p1) {
                    p1.focus();
                    if (typeof p1.select === 'function') p1.select();
                }
            }, 30);
        }

        async function salvarModalPesquisasSku() {
            const row = skuPesquisaModalState.row;
            if (!row) return;
            const { p1, ai, save } = elementosModalPesquisasSku();
            aplicarCamposModalNaLinha(row);
            definirStatusModalPesquisasSku('Salvando campos de pesquisa...');
            if (ai) ai.disabled = true;
            if (save) save.disabled = true;
            try {
                const data = await skuSalvarPesquisasCadastro(row, p1);
                if (!data) {
                    definirStatusModalPesquisasSku('Não foi possível salvar os campos de pesquisa.', true);
                    return;
                }
                row.pesquisa_1 = data.pesquisa_1 || row.pesquisa_1 || '';
                row.pesquisa_2 = data.pesquisa_2 || row.pesquisa_2 || '';
                row.pesquisa_3 = data.pesquisa_3 || row.pesquisa_3 || '';
                preencherCamposModalPesquisasSku(row);
                skuRenderizarTabela();
                renderizarSkuSidebarMercadoLivre();
                definirStatusModalPesquisasSku('Campos salvos para todas as contas com esse SKU.');
                setTimeout(fecharModalPesquisasSku, 450);
            } finally {
                if (ai) ai.disabled = false;
                if (save) save.disabled = false;
            }
        }

        async function preencherModalPesquisasSkuIa() {
            const row = skuPesquisaModalState.row;
            const sku = skuPesquisaModalState.sku;
            if (!row || !sku) return;
            const { ai, save } = elementosModalPesquisasSku();
            aplicarCamposModalNaLinha(row);
            definirStatusModalPesquisasSku('Gerando pesquisas com IA...');
            if (save) save.disabled = true;
            const resultado = await skuGerarPesquisasComIa({
                skus: [sku],
                lojas: [skuObterLoja(row)],
                sobrescrever: true,
                botao: ai
            });
            if (save) save.disabled = false;
            if (!resultado || resultado.success === false) {
                definirStatusModalPesquisasSku(resultado && resultado.error ? resultado.error : 'A IA não conseguiu preencher este SKU.', true);
                return;
            }
            const rowAtual = obterCadastroSkuFavoritos(sku, skuObterLoja(row)) || row;
            skuPesquisaModalState.row = rowAtual;
            preencherCamposModalPesquisasSku(rowAtual);
            renderizarSkuSidebarMercadoLivre();
            definirStatusModalPesquisasSku('Pesquisas preenchidas pela IA e salvas no cadastro.');
        }

        function editarPesquisasSkuSidebar(item, event) {
            abrirModalPesquisasSkuSidebar(item, event);
        }

        function abaFavoritosMlAtiva(nome) {
            return !!document.getElementById(`aba-${nome}`)?.classList.contains('active');
        }

        function itemSkuSidebarAtivoNaAbaAtual(item) {
            if (!item || !item.sku) return false;
            if (abaFavoritosMlAtiva('favoritos')) {
                return skuChaveSku(item.sku) === skuChaveSku(favMlSkuSelecionado)
                    && (!favMlLojaSelecionada || skuNormalizarLoja(item.loja) === skuNormalizarLoja(favMlLojaSelecionada));
            }
            if (abaFavoritosMlAtiva('historico')) {
                return skuChaveSku(item.sku) === skuChaveSku(histMlSkuSelecionado);
            }
            return false;
        }

        function selecionarSkuSidebarParaAbaAtual(item) {
            if (!item || !item.sku) return;
            const itemJaAtivo = itemSkuSidebarAtivoNaAbaAtual(item);
            if (abaFavoritosMlAtiva('favoritos')) {
                if (itemJaAtivo) {
                    if (typeof limparFavoritosSkuSelecionado === 'function') {
                        limparFavoritosSkuSelecionado();
                    }
                    return;
                }
                carregarFavoritosAnunciosSku(item.sku, item.loja, {
                    itemSidebar: item,
                    manterRankingSelecionado: true
                });
                return;
            }
            if (abaFavoritosMlAtiva('historico')) {
                if (itemJaAtivo) {
                    if (typeof limparHistoricoSkuSelecionado === 'function') {
                        limparHistoricoSkuSelecionado();
                    }
                    return;
                }
                selecionarHistoricoSku(item.sku);
            }
        }

        function renderizarSkuSidebarMercadoLivre() {
            if (!mlSkuSidebarListEl || !mlSkuSidebarEmptyEl || !mlSkuSidebarCountEl) return;
            const totalItens = montarItensSkuSidebarMercadoLivre();
            const itensFiltrados = filtrarItensSkuSidebarMercadoLivre(totalItens);
            const itens = aplicarSkuExatoHistoricoSidebar(itensFiltrados, mlSkuSidebarFiltro);
            const limite = Math.max(ML_SKU_SIDEBAR_PAGE_SIZE, Number(mlSkuSidebarRenderLimit || ML_SKU_SIDEBAR_PAGE_SIZE));
            const itensRenderizados = itens.slice(0, limite);
            mlSkuSidebarListEl.innerHTML = '';
            mlSkuSidebarCountEl.textContent = totalItens.length
                ? (itens.length === totalItens.length ? `${totalItens.length} SKU(s)` : `${itens.length} de ${totalItens.length} SKU(s)`)
                : '';
            atualizarContadorSkuSidebarSelecionados(itens);
            const chaveAtualCarregamento = favoritosLojaAtualNormalizada();
            const carregandoEstaLoja = mlSkuCarregamentoEmAndamento
                && mlSkuCarregamentoChave
                && mlSkuCarregamentoChave === chaveAtualCarregamento;
            mlSkuSidebarEmptyEl.textContent = carregandoEstaLoja
                ? `Carregando SKUs dos anuncios da loja ${favoritosNomeLojaExibicao(mlSkuCarregamentoLoja || mlSkuLojaSelecionada).toLowerCase()}...`
                : favoritosWarningLojaAtual
                ? favoritosWarningLojaAtual
                : mlSkuSidebarFiltro
                ? 'Nenhum SKU encontrado para esta busca.'
                : mlSkuLojaSelecionada
                ? 'Nenhum SKU encontrado nos anúncios ativos desta loja.'
                : 'Escolha uma loja integrada para carregar os SKUs dos anúncios ativos.';
            mlSkuSidebarEmptyEl.classList.toggle('hidden', itens.length > 0);
            if (!carregandoEstaLoja && deveBuscarSkuRemotoParaTermo(itensFiltrados, mlSkuSidebarFiltro)) {
                agendarBuscaRemotaSkuSidebarMercadoLivre(mlSkuSidebarFiltro);
            }
            if (!totalItens.length && !mlSkuSidebarFiltro && mlSkuLojaSelecionada && !mlSkuCarregamentoEmAndamento && !favoritosWarningLojaAtual && typeof carregarSkuFavoritos === 'function') {
                const chaveAutoLoad = favoritosLojaAtualNormalizada();
                const tentativas = renderizarSkuSidebarMercadoLivre._autoLoadChaves || new Set();
                renderizarSkuSidebarMercadoLivre._autoLoadChaves = tentativas;
                if (chaveAutoLoad && !tentativas.has(chaveAutoLoad)) {
                    tentativas.add(chaveAutoLoad);
                    setTimeout(() => {
                        if (!mlSkusAnunciosLojaAtual.length && !mlSkuCarregamentoEmAndamento && !favoritosWarningLojaAtual) {
                            carregarSkuFavoritos(mlSkuLojaSelecionada || skuLojaSelecionada || '');
                        }
                    }, 0);
                }
            }
            agendarAtualizacaoPosicaoNavegadorMlShell();

            itensRenderizados.forEach(item => {
                const label = document.createElement('label');
                label.className = 'ml-sku-sidebar-item' + (itemSkuSidebarAtivoNaAbaAtual(item) ? ' is-active' : '');

                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.checked = mlSkuSidebarSelecionados.has(item.chave);
                checkbox.addEventListener('click', (event) => {
                    event.stopPropagation();
                });
                checkbox.addEventListener('change', (event) => {
                    event.stopPropagation();
                    if (checkbox.checked) {
                        mlSkuSidebarSelecionados.add(item.chave);
                    } else {
                        mlSkuSidebarSelecionados.delete(item.chave);
                    }
                    atualizarContadorSkuSidebarSelecionados(itens);
                });

                const main = document.createElement('span');
                main.className = 'ml-sku-sidebar-main';

                const codigo = document.createElement('span');
                codigo.className = 'ml-sku-sidebar-code';
                codigo.textContent = item.sku;

                const titulo = document.createElement('span');
                titulo.className = 'ml-sku-sidebar-name';
                titulo.textContent = item.titulo || 'SKU encontrado em anúncio ativo';

                const meta = document.createElement('span');
                meta.className = 'ml-sku-sidebar-meta';
                const partes = [];
                if (item.loja) partes.push(`Loja: ${item.loja}`);
                if (item.totalAnuncios) partes.push(`${item.totalAnuncios} anúncio(s)`);
                if (item.itemIds && item.itemIds.length) partes.push(item.itemIds.slice(0, 2).join(', '));
                meta.textContent = partes.join(' | ');

                const editarBtn = document.createElement('button');
                editarBtn.type = 'button';
                editarBtn.className = 'ml-sku-sidebar-edit-btn';
                editarBtn.textContent = 'Editar campos de pesquisa';
                editarBtn.title = 'Editar Pesquisa 1, Pesquisa 2 e Pesquisa 3 deste SKU';
                editarBtn.addEventListener('click', (event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    editarPesquisasSkuSidebar(item, event);
                });

                main.appendChild(codigo);
                main.appendChild(titulo);
                if (meta.textContent) main.appendChild(meta);
                const tooltip = criarBalaoPesquisasSkuSidebar(item);
                main.appendChild(tooltip);
                main.appendChild(editarBtn);
                label.appendChild(checkbox);
                label.appendChild(main);
                label.addEventListener('click', (event) => {
                    if (event.target === checkbox || checkbox.contains(event.target)) return;
                    if (event.target === editarBtn || editarBtn.contains(event.target)) return;
                    window.setTimeout(() => selecionarSkuSidebarParaAbaAtual(item), 0);
                });
                label.addEventListener('pointerenter', (event) => posicionarBalaoPesquisasSkuSidebar(tooltip, event, label));
                label.addEventListener('pointermove', (event) => posicionarBalaoPesquisasSkuSidebar(tooltip, event, label));
                label.addEventListener('focusin', () => posicionarBalaoPesquisasSkuSidebar(tooltip, null, label));
                mlSkuSidebarListEl.appendChild(label);
            });

            if (itens.length > itensRenderizados.length) {
                const botaoMais = document.createElement('button');
                botaoMais.type = 'button';
                botaoMais.className = 'ml-sku-sidebar-more';
                botaoMais.textContent = `Carregar mais 50 (${itensRenderizados.length}/${itens.length})`;
                botaoMais.addEventListener('click', carregarMaisSkusSidebarMercadoLivre);
                mlSkuSidebarListEl.appendChild(botaoMais);
            }
        }

        function obterSkusSelecionadosSidebar() {
            return montarItensSkuSidebarMercadoLivre()
                .filter(item => mlSkuSidebarSelecionados.has(item.chave));
        }

        function normalizarSelecionadosFavoritosExecucao(lista) {
            return (Array.isArray(lista) ? lista : [])
                .map(item => {
                    const sku = String(item && item.sku || '').trim();
                    const loja = String(item && item.loja || '').trim();
                    const chave = String(item && item.chave || chaveSkuSidebarMercadoLivre(sku, loja)).trim();
                    if (!sku || !chave) return null;
                    return {
                        ...item,
                        sku,
                        loja,
                        chave,
                        titulo: String(item && item.titulo || '').trim()
                    };
                })
                .filter(Boolean);
        }

        function aplicarDesmarcacaoSkuFavoritos(evento) {
            const chave = String(evento && evento.chave || '').trim();
            if (!chave || !mlSkuSidebarSelecionados.has(chave)) return;
            mlSkuSidebarSelecionados.delete(chave);
            renderizarSkuSidebarMercadoLivre();
            atualizarContadorSkuSidebarSelecionados();
        }

        function publicarDesmarcacaoSkuFavoritos(evento) {
            const payload = {
                tipo: 'desmarcar-sku',
                chave: String(evento && evento.chave || '').trim(),
                sku: String(evento && evento.sku || '').trim(),
                loja: String(evento && evento.loja || '').trim(),
                enviadoEm: Date.now()
            };
            if (!payload.chave) return;
            try {
                if (mlFavoritosSelectionBroadcast) mlFavoritosSelectionBroadcast.postMessage(payload);
            } catch (_err) {}
            try {
                localStorage.setItem(ML_FAVORITOS_SELECTION_STORAGE_KEY, JSON.stringify(payload));
            } catch (_err) {}
        }

        function desmarcarSkuFavoritosProcessado(item, opcoes = {}) {
            const sku = String(item && item.sku || '').trim();
            const loja = String(item && item.loja || '').trim();
            const chave = String(item && item.chave || chaveSkuSidebarMercadoLivre(sku, loja)).trim();
            if (!chave) return;
            const estavaSelecionado = mlSkuSidebarSelecionados.delete(chave);
            if (estavaSelecionado) {
                renderizarSkuSidebarMercadoLivre();
                atualizarContadorSkuSidebarSelecionados();
            }
            if (opcoes.notificar !== false) {
                publicarDesmarcacaoSkuFavoritos({ chave, sku, loja });
            }
        }

        function inicializarSincronizacaoSelecaoFavoritos() {
            try {
                if (typeof BroadcastChannel === 'function') {
                    mlFavoritosSelectionBroadcast = new BroadcastChannel(ML_FAVORITOS_SELECTION_CHANNEL);
                    mlFavoritosSelectionBroadcast.onmessage = (event) => {
                        const data = event && event.data ? event.data : {};
                        if (data && data.tipo === 'desmarcar-sku') aplicarDesmarcacaoSkuFavoritos(data);
                    };
                }
            } catch (_err) {
                mlFavoritosSelectionBroadcast = null;
            }
            window.addEventListener('storage', (event) => {
                if (!event || event.key !== ML_FAVORITOS_SELECTION_STORAGE_KEY || !event.newValue) return;
                try {
                    const data = JSON.parse(event.newValue);
                    if (data && data.tipo === 'desmarcar-sku') aplicarDesmarcacaoSkuFavoritos(data);
                } catch (_err) {}
            });
        }
