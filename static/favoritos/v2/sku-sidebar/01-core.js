(function (global) {
    'use strict';

    const skuSidebar = global.FavoritosV2 && global.FavoritosV2.skuSidebar;
    if (!skuSidebar || !skuSidebar.__runtimeInitialized) {
        throw new Error('Runtime do SKU sidebar nao inicializado.');
    }
    if (skuSidebar.components.has('core')) return;

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
                        window.FavoritosV2.execution.publicApi.renderizarFavoritosSkuSidebar();
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
            const cadastro = window.FavoritosV2.searchRanking.publicApi.search.obterCadastroSkuFavoritos(item && item.sku, item && item.loja);
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

    const core = {
        selecionarLinhaAnuncio,
        encontrarAnuncioPrimeiraPaginaAtual,
        atualizarCelulaMediaVendas,
        dividirSkusMl,
        extrairSkusAtributosMl,
        temSkuOficialAtributosMl,
        extrairSkusAnuncioMl,
        numeroOrdenacaoSkuMl,
        compararItensSkuSidebar,
        chaveSkuSidebarMercadoLivre,
        mesclarSkusAnunciosMercadoLivre,
        agendarBuscaRemotaSkuSidebarMercadoLivre,
        montarItensSkuSidebarMercadoLivre,
        filtrarItensSkuSidebarMercadoLivre,
        skuSidebarTemSkuExatoBusca,
        deveBuscarSkuRemotoParaTermo,
        buscarSkuExatoHistoricoSidebar,
        aplicarSkuExatoHistoricoSidebar,
        extrairMlbsItemSkuSidebar,
        encontrarItemSkuSidebarMercadoLivre,
        executarRenderFavoritosSeguro,
        carregarMaisSkusSidebarMercadoLivre,
        atualizarContadorSkuSidebarSelecionados,
        atualizarFiltroAzulFavoritos,
        atualizarAnimacaoAzulNoNavegadorMl,
        atualizarStatusFavoritosNoNavegadorMl,
        alternarSelecaoTodosSkuSidebar,
        obterPesquisasSkuSidebar,
        criarBalaoPesquisasSkuSidebar,
        posicionarBalaoPesquisasSkuSidebar
    };
    skuSidebar.core = Object.freeze(core);
    Object.assign(skuSidebar.internal, core);
    skuSidebar.components.add('core');
})(window);
