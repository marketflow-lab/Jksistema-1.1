        function prepararAnunciosFavoritosRankingComDadosAvant(todos) {
            const comDadosAvant = filtrarAnunciosFavoritosComDadosAvant(todos);
            const baseRanking = todos;
            return {
                anuncios: baseRanking,
                comDadosAvant,
                removidosPorDadosAvant: 0
            };
        }

        function pararFavoritosAposConfirmacaoAvantProNovaEtapa(opcoes = {}) {
            if (typeof limparBotaoContinuarLoginAvantProFavoritos === 'function') {
                limparBotaoContinuarLoginAvantProFavoritos();
            }
            mlFavoritosEmExecucao = false;
            mlFavoritosExecucaoEmSegundoPlano = false;
            mlFavoritosCancelado = false;
            mlFavoritosPausado = false;
            mostrarBalaoFavoritosStatus('Avant Pro confirmado. Rotina antiga removida; aguardando a nova etapa ser criada.', {
                erro: false,
                manterNavegadorVisivel: true,
                larga: true,
                titulo: 'Favoritos parado'
            });
        }

        function normalizarQuantidadePesquisasNovaColetaFavoritos(quantidade) {
            const numero = Number.parseInt(quantidade, 10);
            return Number.isFinite(numero) && numero > 0 ? Math.min(3, Math.max(1, numero)) : 1;
        }

        function montarGrupoSemPesquisaNovaColetaFavoritos(info, opcoesPromocao) {
            const erro = `Nenhum campo Pesquisa 1 a ${info && info.quantidade_pesquisas || 3} preenchido para este SKU.`;
            return {
                ...info,
                opcoes_promocao: clonarOpcoesPromocaoFavoritos(opcoesPromocao),
                anuncios: [],
                erro,
                resumo_coleta: [{
                    pesquisa: 1,
                    visiveis: 0,
                    coletados: 0,
                    com_titulo: 0,
                    com_foto: 0,
                    com_preco: 0,
                    com_link: 0,
                    com_dados_avant: 0,
                    incompletos: 0,
                    suspeitos: 0,
                    erro
                }],
                total_coletado: 0,
                total_com_dados_avant: 0
            };
        }

        function esperarNovaColetaFavoritos(ms) {
            return new Promise(resolve => setTimeout(resolve, Math.max(0, Number(ms) || 0)));
        }

        function listaAnunciosResultadoColetaFavoritos(resultado) {
            if (Array.isArray(resultado)) return resultado.filter(Boolean);
            if (!resultado || typeof resultado !== 'object') return [];
            const candidatos = [
                resultado.anuncios,
                resultado.all,
                resultado.items,
                resultado.itens,
                resultado.resultados,
                resultado.results,
                resultado.lista
            ];
            for (const lista of candidatos) {
                if (Array.isArray(lista) && lista.length) return lista.filter(Boolean);
            }
            return [];
        }

        function totalVisiveisResultadoColetaFavoritos(resultado, anuncios = []) {
            const lista = Array.isArray(anuncios) ? anuncios : [];
            if (!resultado || typeof resultado !== 'object') return lista.length;
            const total = Number(
                resultado.totalVisiveis
                ?? resultado.total_visiveis
                ?? resultado.total
                ?? resultado.count
                ?? resultado.quantidade
            );
            return Math.max(Number.isFinite(total) ? total : 0, lista.length);
        }

        function erroNovaColetaFavoritos(mensagem, extra = {}) {
            const err = new Error(mensagem);
            Object.assign(err, extra || {});
            return err;
        }

        function anuncioFavoritosTemLink(anuncio) {
            return !!String(anuncio && (anuncio.url || anuncio.permalink || anuncio.link) || '').trim();
        }

        function anuncioFavoritosTemDadosAvant(anuncio) {
            return filtrarAnunciosFavoritosComDadosAvant([anuncio]).length > 0;
        }

        function contarOrigemAuditoriaFavoritos(origens, valor) {
            const chave = String(valor || '').trim() || 'sem_origem';
            origens[chave] = (Number(origens[chave]) || 0) + 1;
        }

        function montarAuditoriaPesquisaFavoritos(lista, helpers = {}) {
            const motivos = {};
            const origens = {};
            const amostras = [];
            const temTitulo = helpers.temTitulo;
            const temPreco = helpers.temPreco;
            const temLink = helpers.temLink || anuncioFavoritosTemLink;
            const temAvant = helpers.temAvant || anuncioFavoritosTemDadosAvant;
            (Array.isArray(lista) ? lista : []).forEach((item) => {
                if (!item) return;
                contarOrigemAuditoriaFavoritos(origens, item.origem_dados || item.source || item.tituloFonte || item.precoFonte || item.fonte_preco);
                const faltas = [];
                if (typeof temTitulo === 'function' && !temTitulo(item)) faltas.push('titulo');
                if (!obterImagemAnuncioFavoritos(item)) faltas.push('foto');
                if (typeof temPreco === 'function' && !temPreco(item)) faltas.push('preco');
                if (!temLink(item)) faltas.push('link');
                if (!temAvant(item)) faltas.push('avant');
                if (item.estado_qualidade === 'suspeito' || item.suspeito === true) faltas.push('suspeito');
                faltas.forEach(motivo => {
                    motivos[motivo] = (Number(motivos[motivo]) || 0) + 1;
                });
                if (faltas.length && amostras.length < 10) {
                    amostras.push({
                        posicao: Number(item.posicao) || null,
                        mlb: item.id || item.mlb || '',
                        titulo: String(item.titulo || item.title || '').slice(0, 140),
                        faltas,
                        origem_dados: item.origem_dados || item.source || '',
                        tituloFonte: item.tituloFonte || item.titulo_fonte || '',
                        fotoFonte: item.fotoFonte || item.foto_fonte || '',
                        precoFonte: item.precoFonte || item.preco_fonte || item.fonte_preco || '',
                        vendasFonte: item.vendasFonte || item.vendas_fonte || '',
                        vendedorFonte: item.vendedorFonte || item.vendedor_fonte || '',
                        dataCriacaoFonte: item.dataCriacaoFonte || item.data_criacao_fonte || ''
                    });
                }
            });
            return {
                motivos_incompletos: motivos,
                origens_dados: origens,
                amostras_incompletos: amostras
            };
        }

        function formatarMotivosIncompletosFavoritos(motivos) {
            const entradas = Object.entries(motivos || {})
                .filter(([, valor]) => Number(valor) > 0)
                .sort((a, b) => Number(b[1]) - Number(a[1]) || a[0].localeCompare(b[0]))
                .slice(0, 5);
            if (!entradas.length) return '';
            return entradas.map(([chave, valor]) => `${chave}=${valor}`).join(', ');
        }

        function montarResumoPesquisaFavoritos(numeroPesquisa, totalVisiveis, anuncios) {
            const lista = Array.isArray(anuncios) ? anuncios.filter(Boolean) : [];
            const resumoColetor = Array.isArray(anuncios) && anuncios.__favoritosResumo && typeof anuncios.__favoritosResumo === 'object'
                ? anuncios.__favoritosResumo
                : {};
            const temTitulo = (item) => typeof tituloValidoFavoritosCanonico === 'function'
                ? tituloValidoFavoritosCanonico(item && (item.titulo || item.title), item && (item.id || item.mlb))
                : !!String(item && (item.titulo || item.title) || '').trim();
            const temPreco = (item) => typeof precoValidoFavoritosCanonico === 'function'
                ? precoValidoFavoritosCanonico(item)
                : !!(item && (item.preco || item.price || item.preco_promocional));
            const comFoto = lista.filter(item => obterImagemAnuncioFavoritos(item)).length;
            const comLink = lista.filter(anuncioFavoritosTemLink).length;
            const comTitulo = lista.filter(temTitulo).length;
            const comPreco = lista.filter(temPreco).length;
            const comDadosAvant = lista.filter(anuncioFavoritosTemDadosAvant).length;
            const suspeitos = lista.filter(item => item && (item.estado_qualidade === 'suspeito' || item.suspeito === true)).length;
            const avantNaoVinculado = Number(resumoColetor.avant_nao_vinculado) || Number(anuncios && anuncios.__favoritosAvantNaoVinculado) || 0;
            const loginAvantBloqueado = !!(
                resumoColetor.login_avant_bloqueado
                || resumoColetor.loginAvantBloqueado
                || (anuncios && anuncios.__favoritosLoginAvantBloqueado)
            );
            const incompletos = lista.filter(item => (
                !temTitulo(item)
                || !obterImagemAnuncioFavoritos(item)
                || !temPreco(item)
                || !anuncioFavoritosTemLink(item)
                || !anuncioFavoritosTemDadosAvant(item)
            )).length;
            const auditoria = montarAuditoriaPesquisaFavoritos(lista, {
                temTitulo,
                temPreco,
                temLink: anuncioFavoritosTemLink,
                temAvant: anuncioFavoritosTemDadosAvant
            });
            return {
                pesquisa: numeroPesquisa,
                visiveis: Number(totalVisiveis) || lista.length,
                coletados: lista.length,
                com_titulo: Number(resumoColetor.com_titulo) || comTitulo,
                com_foto: comFoto,
                com_preco: Number(resumoColetor.com_preco) || comPreco,
                com_link: comLink,
                com_dados_avant: comDadosAvant,
                incompletos: Number(resumoColetor.incompletos) || incompletos,
                suspeitos: Number(resumoColetor.suspeitos) || suspeitos,
                avant_nao_vinculado: avantNaoVinculado,
                login_avant_bloqueado: loginAvantBloqueado,
                motivos_incompletos: auditoria.motivos_incompletos,
                origens_dados: auditoria.origens_dados,
                amostras_incompletos: auditoria.amostras_incompletos
            };
        }

        function formatarResumoPesquisaFavoritos(resumo) {
            const sufixoTempo = resumo && resumo.tempo_esgotado ? ' | tempo limite atingido' : '';
            const sufixoLoginAvant = resumo && resumo.login_avant_bloqueado ? ' | Avant pediu login; ranking salvo com base ML' : '';
            const base = `Pesquisa ${resumo.pesquisa} concluida: ${resumo.visiveis} visiveis | ${resumo.coletados} coletados | ${resumo.com_titulo || 0} com titulo | ${resumo.com_foto || 0} com foto | ${resumo.com_preco || 0} com preco | ${resumo.com_link || 0} com link | ${resumo.com_dados_avant || 0} com Avant | ${resumo.incompletos || 0} incompletos | ${resumo.suspeitos || 0} suspeitos`;
            const avantSolto = Number(resumo.avant_nao_vinculado) || 0;
            const motivos = formatarMotivosIncompletosFavoritos(resumo.motivos_incompletos);
            return `${base}${motivos ? ` | faltas: ${motivos}` : ''}${avantSolto ? ` | ${avantSolto} Avant ignorados sem MLB/link` : ''}${sufixoLoginAvant}${sufixoTempo}`;
        }

        function formatarResumoFalhaRankingFavoritos(grupos) {
            const resumos = (Array.isArray(grupos) ? grupos : [])
                .flatMap(grupo => Array.isArray(grupo && grupo.resumo_coleta) ? grupo.resumo_coleta : [])
                .filter(Boolean)
                .slice(0, 3)
                .map(formatarResumoPesquisaFavoritos);
            if (resumos.length) return ` ${resumos.join(' | ')}`;
            const erros = (Array.isArray(grupos) ? grupos : [])
                .map(grupo => String(grupo && grupo.erro || '').trim())
                .filter(Boolean)
                .slice(0, 3);
            return erros.length ? ` ${erros.join(' | ')}` : '';
        }

        function montarRankingFavoritosComFallback(anuncios, sku = '') {
            const lista = (Array.isArray(anuncios) ? anuncios : [])
                .filter(Boolean)
                .filter(anuncioFavoritosCandidatoRanking);
            if (!lista.length) return [];
            try {
                const ordenados = ordenarAnunciosFavoritosRanking(lista, sku);
                if (Array.isArray(ordenados) && ordenados.length) {
                    return limitarAnunciosFavoritosRanking(ordenados);
                }
            } catch (err) {
                console.warn('Nao foi possivel ordenar ranking por media; usando ordem da primeira pagina:', err);
            }
            return limitarAnunciosFavoritosRanking(
                lista.slice().sort((a, b) => {
                    const metricaA = calcularMetricasMediaVendas(a);
                    const metricaB = calcularMetricasMediaVendas(b);
                    const mediaA = Number.isFinite(metricaA && metricaA.media) ? metricaA.media : null;
                    const mediaB = Number.isFinite(metricaB && metricaB.media) ? metricaB.media : null;
                    if (Number.isFinite(mediaA) || Number.isFinite(mediaB)) {
                        return (Number.isFinite(mediaB) ? mediaB : -1) - (Number.isFinite(mediaA) ? mediaA : -1);
                    }
                    const posicaoA = Number(a && a.posicao) || 9999;
                    const posicaoB = Number(b && b.posicao) || 9999;
                    if (posicaoA !== posicaoB) return posicaoA - posicaoB;
                    return 0;
                })
            );
        }

        function formatarProgressoColetaPrimeiraPaginaFavoritos(numeroPesquisa, progresso = {}) {
            const etapa = String(progresso.etapa || '').trim();
            const passada = Number(progresso.passada) || 0;
            const maxPassadas = Number(progresso.maxPassadas) || 3;
            const segundos = Math.max(0, Math.ceil((Number(progresso.tempoRestanteMs) || 0) / 1000));
            const base = `Pesquisa ${numeroPesquisa}: ${Number(progresso.visiveis) || 0} visiveis | ${Number(progresso.coletados) || 0} coletados | ${Number(progresso.com_titulo) || 0} titulo | ${Number(progresso.com_foto) || 0} foto | ${Number(progresso.com_preco) || 0} preco | ${Number(progresso.com_link) || 0} link | ${Number(progresso.com_dados_avant) || 0} Avant | ${Number(progresso.suspeitos) || 0} suspeitos`;
            if (etapa === 'avant') {
                const clicados = Number(progresso.clicados) || 0;
                const candidatos = Number(progresso.candidatosAvant) || 0;
                const capturados = Number(progresso.capturadosAvant) || 0;
                const detalheAvant = capturados > 0
                    ? `${clicados} clique(s) Avant | ${capturados} capturado(s)`
                    : `${clicados} clique(s) Avant${candidatos ? ` | ${candidatos} card(s)` : ''}`;
                const avisoClique = progresso.loginAvantBloqueado
                    ? ' | Avant sem login pronto; lendo DOM visivel'
                    : (progresso.cliquesAvantDesligados ? ' | cliques Avant pausados; lendo DOM visivel' : '');
                return `${base} | passada ${passada}/${maxPassadas} | lote ${Number(progresso.posicao) || 0}/${Number(progresso.posicoes) || 0} | ${detalheAvant}${avisoClique} | ${segundos}s restantes`;
            }
            if (etapa === 'materializando') {
                return `${base} | carregando cards da primeira pagina`;
            }
            if (etapa === 'aguardando_cards') {
                const detalhe = progresso.loadingScreen
                    ? 'Mercado Livre ainda esta carregando'
                    : (progresso.noResults ? 'Mercado Livre indicou sem resultados' : 'aguardando os cards reais da busca');
                return `${base} | ${detalhe} | ${segundos}s restantes`;
            }
            if (etapa === 'passada') {
                return `${base} | passada ${passada}/${maxPassadas} concluida | ${segundos}s restantes`;
            }
            return base;
        }

        function fecharNavegadorFavoritosAposColeta(reason = 'favoritos-coleta-finalizada') {
            if (typeof fecharBalaoResultadosMl === 'function') {
                fecharBalaoResultadosMl({
                    forcar: true,
                    descarregarConteudo: true,
                    destroy: true,
                    reason,
                    preserveAvantProSession: true
                });
            }
            if (window.electronAPI && typeof window.electronAPI.hideEmbeddedMlBrowser === 'function') {
                window.electronAPI.hideEmbeddedMlBrowser({
                    destroy: true,
                    reason,
                    preserveAvantProSession: true
                }).catch(() => {});
            }
            if (window.electronAPI && typeof window.electronAPI.stopFavoritosJobBrowserBackground === 'function') {
                window.electronAPI.stopFavoritosJobBrowserBackground().catch(() => {});
            } else if (window.electronAPI && typeof window.electronAPI.stopFavoritosWorkerBrowser === 'function') {
                window.electronAPI.stopFavoritosWorkerBrowser({
                    destroy: true,
                    reason,
                    message: 'Favoritos finalizado.'
                }).catch(() => {});
            }
            try {
                window.__JK_FAVORITOS_WORKER_BROWSER_ACTIVE = false;
                if (window.top && window.top !== window && typeof window.top.postMessage === 'function') {
                    window.top.postMessage({
                        channel: 'jk-favoritos-worker-done',
                        payload: {
                            active: false,
                            status: 'done',
                            message: 'Favoritos finalizado.'
                        }
                    }, '*');
                }
            } catch (_err) {}
        }

        async function coletarAnunciosPrimeiraPaginaFavoritosSemFluxoAntigo(info, pesquisa, opcoes = {}) {
            const termo = String(pesquisa && pesquisa.termo || '').trim();
            if (!termo) return [];
            if (typeof abrirMercadoLivreNoPrograma !== 'function') {
                throw erroNovaColetaFavoritos('Navegador interno indisponivel para abrir a pesquisa.');
            }
            const limiteAnunciosPrimeiraPesquisa = Number(opcoes.maxAnuncios) || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80;
            const urlPesquisaMl = typeof construirUrlPesquisaMercadoLivre === 'function'
                ? construirUrlPesquisaMercadoLivre(termo)
                : `https://lista.mercadolivre.com.br/${encodeURIComponent(termo)}`;
            if (typeof mlUrlInput !== 'undefined' && mlUrlInput) {
                mlUrlInput.value = urlPesquisaMl;
            }

            mostrarBalaoFavoritosStatus(`Abrindo pesquisa "${termo}" no Mercado Livre...`, {
                manterNavegadorVisivel: true,
                larga: true,
                titulo: opcoes.primeiraPesquisa ? 'Primeira pesquisa' : 'Pesquisa'
            });
            const abriu = await abrirMercadoLivreNoPrograma({
                termoPesquisa: termo,
                titulo: opcoes.titulo || 'Fazendo Favorito! Aguarde...',
                subtitulo: opcoes.subtitulo || `SKU ${info && info.sku || ''} - Pesquisa ${pesquisa && pesquisa.campo || ''}`,
                mostrarFavoritos: true,
                browserCompleto: true,
                forcarExibicao: true,
                aguardarPesquisaMs: 900,
                apenasAbrirUrl: true,
                confirmarPesquisa: false,
                agendarPosicaoAntes: false,
                reposicionarDepois: false
            }).catch(() => false);
            if (!abriu) {
                throw erroNovaColetaFavoritos(`Nao consegui abrir a pesquisa "${termo}" no navegador interno.`);
            }

            await esperarNovaColetaFavoritos(1200);

            mostrarBalaoFavoritosStatus(`Coletando todos os anuncios da primeira pagina de "${termo}"...`, {
                manterNavegadorVisivel: true,
                larga: true,
                titulo: 'Coleta da pagina'
            });
            let resultado = null;
            if (typeof coletarPrimeiraPaginaFavoritosControlada === 'function') {
                resultado = await coletarPrimeiraPaginaFavoritosControlada({
                    maxAnuncios: limiteAnunciosPrimeiraPesquisa,
                    tempoLimiteMs: 180000,
                    maxPassadas: 3,
                    loteCliques: 6,
                    onProgress: (progresso) => {
                        mostrarBalaoFavoritosStatus(formatarProgressoColetaPrimeiraPaginaFavoritos(pesquisa && pesquisa.campo || 1, progresso), {
                            manterNavegadorVisivel: true,
                            larga: true,
                            titulo: 'Coleta da pagina'
                        });
                    }
                }).catch((err) => {
                    if (err && err.loginMercadoLivreNecessario) throw err;
                    console.warn('Coleta controlada da primeira pagina falhou:', err);
                    return null;
                });
            }

            let anuncios = listaAnunciosResultadoColetaFavoritos(resultado);
            let totalVisiveis = totalVisiveisResultadoColetaFavoritos(resultado, anuncios);
            if (!anuncios.length && typeof rolarMercadoLivreFavoritos === 'function') {
                await rolarMercadoLivreFavoritos(0).catch(() => null);
                await esperarNovaColetaFavoritos(900);
            }
            if (!anuncios.length && typeof extrairCardsMercadoLivreBasicoWebview === 'function') {
                const basicoFinal = await extrairCardsMercadoLivreBasicoWebview({
                    limite: limiteAnunciosPrimeiraPesquisa
                }).catch(() => null);
                anuncios = listaAnunciosResultadoColetaFavoritos(basicoFinal);
                totalVisiveis = totalVisiveisResultadoColetaFavoritos(basicoFinal, anuncios);
            }
            if (!anuncios.length && typeof extrairBaseMercadoLivreEmergencialWebview === 'function') {
                const emergenciaFinal = await extrairBaseMercadoLivreEmergencialWebview({
                    limite: limiteAnunciosPrimeiraPesquisa,
                    timeoutMs: 12000
                }).catch(() => null);
                anuncios = listaAnunciosResultadoColetaFavoritos(emergenciaFinal);
                totalVisiveis = totalVisiveisResultadoColetaFavoritos(emergenciaFinal, anuncios);
            }

            if (typeof aplicarCacheAvantAosAnuncios === 'function') {
                anuncios = aplicarCacheAvantAosAnuncios(anuncios, {
                    termo,
                    sku: info && info.sku || termo
                });
            }
            if (typeof salvarCacheAvantDosAnuncios === 'function') {
                salvarCacheAvantDosAnuncios(anuncios, {
                    termo,
                    sku: info && info.sku || termo
                });
            }
            const saida = anuncios.slice(0, limiteAnunciosPrimeiraPesquisa).map(item => ({
                ...item,
                origem_dados: item && item.origem_dados || 'avantpro_primeira_pagina_controlada'
            }));
            try {
                Object.defineProperty(saida, '__favoritosTotalVisiveis', {
                    value: totalVisiveis,
                    enumerable: false
                });
                Object.defineProperty(saida, '__favoritosResumo', {
                    value: resultado && resultado.resumo || (
                        typeof resumoPrimeiraPaginaFavoritos === 'function'
                            ? resumoPrimeiraPaginaFavoritos(totalVisiveis, anuncios, { etapa: 'fallback_final' })
                            : null
                    ),
                    enumerable: false
                });
                Object.defineProperty(saida, '__favoritosAvantNaoVinculado', {
                    value: Number(resultado && resultado.resumo && resultado.resumo.avant_nao_vinculado) || 0,
                    enumerable: false
                });
                Object.defineProperty(saida, '__favoritosTempoEsgotado', {
                    value: !!(resultado && resultado.tempoEsgotado),
                    enumerable: false
                });
                Object.defineProperty(saida, '__favoritosLoginAvantBloqueado', {
                    value: !!(resultado && (resultado.loginAvantBloqueado || (resultado.resumo && (resultado.resumo.loginAvantBloqueado || resultado.resumo.login_avant_bloqueado)))),
                    enumerable: false
                });
            } catch (_err) {
                saida.__favoritosTotalVisiveis = totalVisiveis;
                saida.__favoritosResumo = resultado && resultado.resumo || (
                    typeof resumoPrimeiraPaginaFavoritos === 'function'
                        ? resumoPrimeiraPaginaFavoritos(totalVisiveis, anuncios, { etapa: 'fallback_final' })
                        : null
                );
                saida.__favoritosAvantNaoVinculado = Number(resultado && resultado.resumo && resultado.resumo.avant_nao_vinculado) || 0;
                saida.__favoritosTempoEsgotado = !!(resultado && resultado.tempoEsgotado);
                saida.__favoritosLoginAvantBloqueado = !!(resultado && (resultado.loginAvantBloqueado || (resultado.resumo && (resultado.resumo.loginAvantBloqueado || resultado.resumo.login_avant_bloqueado))));
            }
            return saida;
        }

        async function abrirPrimeiraPesquisaFavoritosAposConfirmacaoAvantPro(selecionados = [], quantidade = 1, opcoes = {}) {
            if (typeof limparBotaoContinuarLoginAvantProFavoritos === 'function') {
                limparBotaoContinuarLoginAvantProFavoritos();
            }
            if (mlFavoritosEmExecucao) return { success: false, reason: 'execucao_em_andamento' };
            const selecionadosLista = Array.isArray(selecionados) ? selecionados.filter(Boolean) : [];
            const quantidadePesquisas = normalizarQuantidadePesquisasNovaColetaFavoritos(quantidade);
            const opcoesPromocao = opcoes.opcoesPromocao || mlFavoritosOpcoesPromocaoAtual || null;
            const usarIaRanking = opcoes.usarIaRanking !== undefined
                ? !!opcoes.usarIaRanking
                : (typeof favoritosUsarIaRankingAtivo === 'function' && favoritosUsarIaRankingAtivo());
            const limiteAnunciosPrimeiraPesquisa = Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80;
            const limiteRankingFinal = Number(ML_FAVORITOS_RANKING_ANUNCIOS_MAX) || 80;

            if (!selecionadosLista.length) {
                mostrarBalaoFavoritosStatus('Avant Pro confirmado, mas nenhum SKU foi selecionado para pesquisar.', {
                    erro: true,
                    tempoMs: 4500,
                    titulo: 'Favoritos'
                });
                return { success: false, reason: 'sem_skus' };
            }

            mlFavoritosEmExecucao = true;
            mlFavoritosExecucaoEmSegundoPlano = false;
            mlFavoritosCancelado = false;
            mlFavoritosPausado = false;
            mlFavoritosAbortController = typeof AbortController === 'function' ? new AbortController() : null;

            const termo = String(
                opcoes.termo
                || (typeof obterTermoInicialLoginAvantProFavoritos === 'function'
                    ? obterTermoInicialLoginAvantProFavoritos(selecionadosLista, quantidadePesquisas)
                    : '')
            ).trim();
            const sku = selecionadosLista && selecionadosLista[0] && selecionadosLista[0].sku
                ? String(selecionadosLista[0].sku).trim()
                : '';
            if (!termo) {
                mlFavoritosEmExecucao = false;
                mlFavoritosAbortController = null;
                mostrarBalaoFavoritosStatus('Avant Pro confirmado, mas nao encontrei termo para abrir a primeira pesquisa.', {
                    erro: true,
                    manterNavegadorVisivel: true,
                    larga: true,
                    titulo: 'Primeira pesquisa'
                });
                return { success: false, reason: 'termo_indisponivel' };
            }

            mostrarBalaoFavoritosStatus(`Avant Pro confirmado. Abrindo a primeira pesquisa de "${termo}"...`, {
                erro: false,
                manterNavegadorVisivel: true,
                larga: true,
                titulo: 'Primeira pesquisa'
            });
            if (typeof mudarAba === 'function') {
                try { mudarAba('navegador'); } catch (_err) {}
            }
            abrirBalaoResultadosMl({
                titulo: 'Fazendo Favorito! Aguarde...',
                subtitulo: `${selecionadosLista.length} SKU(s), ${quantidadePesquisas} pesquisa(s) por SKU`,
                mostrarFavoritos: true,
                browserCompleto: true,
                forcarExibicao: true
            });

            const grupos = [];
            try {
                if (!Array.isArray(skuDados) || !skuDados.length) {
                    mostrarBalaoFavoritosStatus('Carregando dados dos SKUs antes da primeira pesquisa...');
                    await carregarSkuFavoritos();
                    await aguardarControleFavoritos();
                }

                for (let idx = 0; idx < selecionadosLista.length; idx += 1) {
                    verificarCancelamentoFavoritos();
                    await aguardarControleFavoritos();
                    const item = selecionadosLista[idx];
                    const infoBase = montarPesquisasFavoritosSku(item, quantidadePesquisas);
                    const info = {
                        ...infoBase,
                        quantidade_pesquisas: quantidadePesquisas
                    };
                    if (!info.termos.length) {
                        const grupoSemPesquisa = montarGrupoSemPesquisaNovaColetaFavoritos(info, opcoesPromocao);
                        grupos.push(grupoSemPesquisa);
                        guardarResultadoRankingFavorito(grupoSemPesquisa);
                        renderizarFavoritosPesquisaResultados(grupos);
                        desmarcarSkuFavoritosProcessado(item);
                        continue;
                    }

                    const coletados = [];
                    const resumosColeta = [];
                    for (let pesquisaIndex = 0; pesquisaIndex < info.termos.length; pesquisaIndex += 1) {
                        verificarCancelamentoFavoritos();
                        await aguardarControleFavoritos();
                        const pesquisa = info.termos[pesquisaIndex];
                        const ordemPesquisa = `${pesquisaIndex + 1}/${info.termos.length}`;
                        mostrarBalaoFavoritosStatus(`SKU ${info.sku}: pesquisa ${ordemPesquisa} - ${pesquisa.termo}. Aguardando dados do Avant Pro...`, {
                            manterNavegadorVisivel: true,
                            larga: true,
                            titulo: pesquisaIndex === 0 ? 'Primeira pesquisa' : 'Proxima pesquisa'
                        });
                        const anuncios = await coletarAnunciosPrimeiraPaginaFavoritosSemFluxoAntigo(info, pesquisa, {
                            primeiraPesquisa: idx === 0 && pesquisaIndex === 0,
                            maxAnuncios: limiteAnunciosPrimeiraPesquisa
                        });
                        let normalizadosPesquisa = anuncios.map(anuncio => normalizarAnuncioFavoritosPesquisa(anuncio, info.sku, pesquisa));
                        await enriquecerAnunciosFavoritosRanking(normalizadosPesquisa);
                        normalizadosPesquisa = normalizadosPesquisa.map(anuncio => normalizarAnuncioFavoritosPesquisa(anuncio, info.sku, pesquisa));
                        normalizadosPesquisa.forEach(anuncio => coletados.push(anuncio));
                        const resumoPesquisa = montarResumoPesquisaFavoritos(
                            pesquisaIndex + 1,
                            Number(anuncios.__favoritosTotalVisiveis) || anuncios.length,
                            normalizadosPesquisa
                        );
                        resumoPesquisa.tempo_esgotado = !!anuncios.__favoritosTempoEsgotado;
                        resumosColeta.push({
                            ...resumoPesquisa,
                            termo: pesquisa.termo,
                            campo: pesquisa.campo
                        });
                        mostrarBalaoFavoritosStatus(formatarResumoPesquisaFavoritos(resumoPesquisa), {
                            manterNavegadorVisivel: true,
                            larga: true,
                            titulo: 'Resumo da coleta'
                        });
                    }

                    const unicos = deduplicarAnunciosFavoritos(coletados)
                        .filter(anuncioFavoritosCandidatoRanking);
                    mostrarBalaoFavoritosStatus(`SKU ${info.sku}: calculando ranking dos anuncios coletados...`, {
                        manterNavegadorVisivel: true,
                        larga: true,
                        titulo: 'Ranking'
                    });
                    await enriquecerAnunciosFavoritosRanking(unicos);
                    await aguardarControleFavoritos();

                    const anunciosNovos = unicos.filter(anuncioFavoritosProdutoNovo);
                    const anunciosElegiveis = anunciosNovos.length ? anunciosNovos : unicos;
                    const preparoDadosAvant = prepararAnunciosFavoritosRankingComDadosAvant(anunciosElegiveis);
                    const rankingBase = montarRankingFavoritosComFallback(preparoDadosAvant.anuncios, info.sku);
                    let filtroIa = {
                        anuncios: limitarAnunciosFavoritosRanking(rankingBase),
                        removidos: [],
                        removidosTotal: 0,
                        usouIa: false
                    };
                    if (usarIaRanking) {
                        try {
                            filtroIa = await filtrarAnunciosFavoritosPorIa(info, rankingBase, {
                                usarIa: true,
                                itemSidebar: item,
                                maxConfirmados: 8
                            });
                        } catch (err) {
                            if (mlFavoritosCancelado || (err && (err.canceladoFavoritos || err.name === 'AbortError'))) throw err;
                            console.warn('IA de favoritos falhou; mantendo ranking normal:', err);
                        }
                    }
                    let anunciosRanking = usarIaRanking && filtroIa.usouIa
                        ? filtroIa.anuncios
                        : limitarAnunciosFavoritosRanking(rankingBase);
                    if (!anunciosRanking.length && rankingBase.length) {
                        anunciosRanking = limitarAnunciosFavoritosRanking(rankingBase);
                    }
                    if (!anunciosRanking.length && unicos.length) {
                        anunciosRanking = montarRankingFavoritosComFallback(unicos, info.sku);
                    }
                    const grupoRanking = {
                        ...info,
                        sku: info.sku || item.sku || sku || '',
                        loja: info.loja || item.loja || favoritosLojaSelecionadaParaApi() || '',
                        opcoes_promocao: clonarOpcoesPromocaoFavoritos(opcoesPromocao),
                        anuncios: anunciosRanking.slice(0, limiteRankingFinal),
                        removidos_ia: filtroIa.removidos,
                        removidos_ia_total: filtroIa.removidosTotal,
                        usou_ia: !!filtroIa.usouIa,
                        ia_confirmados: filtroIa.confirmados || 0,
                        ia_max_confirmados: filtroIa.maxConfirmados || (usarIaRanking ? 8 : 0),
                        fonte_coleta: 'avantpro_primeira_pagina_nova',
                        resumo_coleta: resumosColeta,
                        total_coletado: unicos.length,
                        total_com_dados_avant: filtrarAnunciosFavoritosComDadosAvant(unicos).length
                    };
                    grupos.push(grupoRanking);
                    guardarResultadoRankingFavorito(grupoRanking);
                    renderizarFavoritosPesquisaResultados(grupos);
                    desmarcarSkuFavoritosProcessado(item);
                }

                const gruposComRanking = grupos.filter(grupo => grupo && Array.isArray(grupo.anuncios) && grupo.anuncios.length);
                const totalAnuncios = gruposComRanking.reduce((acc, grupo) => acc + grupo.anuncios.length, 0);
                const entradaHistorico = registrarHistoricoFavoritos(grupos);
                if (!entradaHistorico && gruposComRanking.length) {
                    console.warn('Ranking de favoritos foi montado, mas o historico nao retornou entrada salva.', {
                        grupos: gruposComRanking.length,
                        anuncios: totalAnuncios
                    });
                    mostrarBalaoFavoritosStatus(`Ranking montado com ${totalAnuncios} anuncio(s), mas o historico nao confirmou o salvamento.`, {
                        erro: true,
                        tempoMs: 7000,
                        larga: true,
                        titulo: 'Ranking montado'
                    });
                }
                if (!entradaHistorico && !gruposComRanking.length) {
                    if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Favoritos sem ranking salvo';
                    if (mlWorkModalSubtitleEl) mlWorkModalSubtitleEl.textContent = `${grupos.length} SKU(s), nenhum anuncio rankeado.`;
                    const detalheFalha = formatarResumoFalhaRankingFavoritos(grupos);
                    mostrarBalaoFavoritosStatus(`A coleta terminou, mas nenhum anuncio entrou no ranking.${detalheFalha || ' Confira os campos de pesquisa e tente novamente.'}`, {
                        erro: true,
                        tempoMs: 9000,
                        larga: true
                    });
                    fecharNavegadorFavoritosAposColeta('favoritos-coleta-sem-ranking');
                    return { success: false, reason: 'sem_ranking', grupos };
                }

                const grupoParaAbrir = gruposComRanking[0] || grupos.find(grupo => grupo && grupo.sku);
                if (grupoParaAbrir && grupoParaAbrir.sku) {
                    favMlSkuSelecionado = grupoParaAbrir.sku;
                    favMlLojaSelecionada = grupoParaAbrir.loja || favoritosLojaSelecionadaParaApi() || '';
                    favMlHistoricoExecucaoSelecionadaId = FAV_ML_RANKING_ATUAL_ID;
                }
                renderizarFavoritosPesquisaResultados(grupos);
                if (grupoParaAbrir && grupoParaAbrir.sku) {
                    carregarFavoritosAnunciosSku(grupoParaAbrir.sku, grupoParaAbrir.loja || favMlLojaSelecionada, {
                        manterRankingSelecionado: true
                    });
                }
                if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Favoritos concluidos';
                if (mlWorkModalSubtitleEl) mlWorkModalSubtitleEl.textContent = `${gruposComRanking.length} SKU(s), ${totalAnuncios} anuncio(s) no ranking.`;
                mostrarBalaoFavoritosStatus(`Favoritos concluido. Ranking salvo com ate ${limiteRankingFinal} anuncio(s) por SKU, em ordem de media de venda. A rotina antiga de coleta continua desligada.`, {
                    tempoMs: 8000,
                    larga: true,
                    titulo: 'Ranking salvo'
                });
                if (typeof mudarAba === 'function') {
                    try { mudarAba('favoritos'); } catch (_err) {}
                }
                fecharNavegadorFavoritosAposColeta('favoritos-coleta-concluida');
                return { success: true, termo, sku, grupos, entradaHistorico };
            } catch (err) {
                if (mlFavoritosCancelado || (err && (err.canceladoFavoritos || err.name === 'AbortError'))) {
                    mostrarBalaoFavoritosStatus('Favoritos cancelado pelo usuario.', {
                        tempoMs: 5000,
                        titulo: 'Favoritos cancelado'
                    });
                    pararNavegadorFavoritosBackground();
                    return { success: false, reason: 'cancelado', grupos };
                }
                console.error('Falha na nova coleta de favoritos:', err);
                mostrarBalaoFavoritosStatus(`Erro ao fazer favoritos: ${err && err.message ? err.message : err}`, {
                    erro: true,
                    manterNavegadorVisivel: true,
                    larga: true,
                    tempoMs: 12000,
                    titulo: 'Erro ao fazer favoritos'
                });
                return { success: false, reason: 'erro', error: err && err.message ? err.message : String(err), grupos };
            } finally {
                mlFavoritosEmExecucao = false;
                mlFavoritosExecucaoEmSegundoPlano = false;
                mlFavoritosPausado = false;
                mlFavoritosAbortController = null;
                mlFavoritosJobUltimoStatus = null;
                atualizarFiltroAzulFavoritos();
                atualizarContadorSkuSidebarSelecionados();
            }
        }

        async function fazerFavoritosSkusSelecionadosRendererAntigo(opcoes = {}) {
            if (opcoes && opcoes.type && opcoes.target) opcoes = {};
            if (mlFavoritosEmExecucao) return;
            const selecionadosPayload = normalizarSelecionadosFavoritosExecucao(opcoes.selecionadosPayload);
            const selecionados = selecionadosPayload.length ? selecionadosPayload : obterSkusSelecionadosSidebar();
            if (!selecionados.length) {
                mostrarBalaoFavoritosStatus('Selecione pelo menos um SKU no sidebar antes de fazer favoritos.', {
                    erro: true,
                    tempoMs: 3500
                });
                return;
            }
            mostrarBalaoFavoritosStatus(`${selecionados.length} SKU(s) selecionado(s). Escolha a quantidade de pesquisas.`, {
                manterAcoes: false
            });
            const quantidade = Number(opcoes.quantidade) || await perguntarQuantidadePesquisasFavoritos();
            if (!quantidade) return;
            const opcoesPromocao = opcoes.opcoesPromocao || await perguntarOpcoesPromocaoFavoritos();
            if (!opcoesPromocao) return;
            mlFavoritosOpcoesPromocaoAtual = clonarOpcoesPromocaoFavoritos(opcoesPromocao);
            selecionados.forEach(item => salvarOpcoesPromocaoFavoritosSku(item && item.sku, mlFavoritosOpcoesPromocaoAtual));
            const usarIaRanking = favoritosUsarIaRankingAtivo();
            const avantLoginConfirmadoPeloUsuario = opcoes.avantLoginConfirmadoPeloUsuario === true
                || await perguntarLoginAvantProAntesFavoritos({
                    selecionados,
                    quantidade,
                    opcoesPromocao
            });
            if (!avantLoginConfirmadoPeloUsuario) return;
            await abrirPrimeiraPesquisaFavoritosAposConfirmacaoAvantPro(selecionados, quantidade, {
                opcoesPromocao,
                usarIaRanking
            });
            return;

            mlFavoritosEmExecucao = true;
            const executarEmBackground = opcoes.background === true;
            let manterNavegadorVisivelAposErro = false;
            mlFavoritosExecucaoEmSegundoPlano = executarEmBackground;
            mlFavoritosCancelado = false;
            mlFavoritosPausado = false;
            mlFavoritosJobIdAtual = '';
            mlFavoritosJobUltimoStatus = null;
            mlFavoritosAbortController = typeof AbortController === 'function' ? new AbortController() : null;
            const mostrarNavegadorMl = opcoes.mostrarNavegadorMl !== false && !executarEmBackground;
            if (executarEmBackground) {
                await prepararNavegadorFavoritosBackground();
            } else if (mostrarNavegadorMl) {
                mudarAba('navegador');
            } else {
                ocultarNavegadorMlShellDefinitivo();
            }
            abrirBalaoResultadosMl({
                titulo: 'Fazendo Favorito! Aguarde...',
                subtitulo: `${selecionados.length} SKU(s), ${quantidade} pesquisa(s) por SKU${usarIaRanking ? ', IA ligada' : ''}`,
                mostrarFavoritos: true,
                browserCompleto: true,
                forcarExibicao: mostrarNavegadorMl
            });
            atualizarFiltroAzulFavoritos();
            atualizarContadorSkuSidebarSelecionados();
            if (mlFavoritosPanelEl) mlFavoritosPanelEl.classList.remove('hidden');
            if (mlFavoritosListEl) mlFavoritosListEl.innerHTML = '';
            if (mlFavoritosEmptyEl) mlFavoritosEmptyEl.classList.add('hidden');
            mostrarBalaoFavoritosStatus(`Iniciando favoritos: ${selecionados.length} SKU(s), ${quantidade} pesquisa(s) por SKU, ${resumoOpcoesPromocaoFavoritos(opcoesPromocao)}${usarIaRanking ? ', IA verifica anuncios fora do produto' : ''}.`);

            try {
                let loginAvantPrimeiraPesquisaPreparado = !!avantLoginConfirmadoPeloUsuario;
                if (!Array.isArray(skuDados) || !skuDados.length) {
                    mostrarBalaoFavoritosStatus('Carregando dados dos SKUs...');
                    await carregarSkuFavoritos();
                    await aguardarControleFavoritos();
                }

                const grupos = [];
                for (let idx = 0; idx < selecionados.length; idx += 1) {
                    await aguardarControleFavoritos();
                    const item = selecionados[idx];
                    const info = montarPesquisasFavoritosSku(item, quantidade);
                    mostrarBalaoFavoritosStatus(`Processando SKU ${info.sku} (${idx + 1}/${selecionados.length})...`);
                    if (!info.termos.length) {
                        const grupoSemPesquisa = { ...info, opcoes_promocao: clonarOpcoesPromocaoFavoritos(opcoesPromocao), anuncios: [], erro: `Nenhum campo Pesquisa 1 a ${quantidade} preenchido para este SKU.` };
                        grupos.push(grupoSemPesquisa);
                        guardarResultadoRankingFavorito(grupoSemPesquisa);
                        mostrarBalaoFavoritosStatus(`SKU ${info.sku}: nenhum campo Pesquisa 1 a ${quantidade} preenchido.`, {
                            erro: true
                        });
                        renderizarFavoritosPesquisaResultados(grupos);
                        desmarcarSkuFavoritosProcessado(item);
                        continue;
                    }

                    const coletados = [];
                    for (const pesquisa of info.termos) {
                        await aguardarControleFavoritos();
                        const loginAvantAntesDaColeta = !loginAvantPrimeiraPesquisaPreparado;
                        mostrarBalaoFavoritosStatus(`SKU ${info.sku}: pesquisando Pesquisa ${pesquisa.campo} - ${pesquisa.termo}`);
                        const anuncios = await buscarAnunciosFavoritosPorTermo(pesquisa.termo, {
                            sku: info.sku,
                            loja: info.loja,
                            titulo: 'Fazendo Favorito! Aguarde...',
                            subtitulo: `SKU ${info.sku} - Pesquisa ${pesquisa.campo}`,
                            exigirAvantPro: true,
                            loginAvantAntesDaColeta
                        });
                        loginAvantPrimeiraPesquisaPreparado = true;
                        await aguardarControleFavoritos();
                        anuncios.forEach(anuncio => {
                            coletados.push(normalizarAnuncioFavoritosPesquisa(anuncio, info.sku, pesquisa));
                        });
                    }

                    const unicos = deduplicarAnunciosFavoritos(coletados)
                        .filter(anuncioFavoritosCandidatoRanking);
                    mostrarBalaoFavoritosStatus(`SKU ${info.sku}: enriquecendo ${unicos.length} anuncio(s) para calcular ranking...`);
                    await aguardarControleFavoritos();
                    await enriquecerAnunciosFavoritosRanking(unicos);
                    for (let i = 0; i < unicos.length; i += 1) {
                        const origem = Array.isArray(unicos[i].pesquisas_origem) && unicos[i].pesquisas_origem[0]
                            ? { termo: unicos[i].pesquisas_origem[0], campo: '' }
                            : { termo: '', campo: '' };
                        unicos[i] = normalizarAnuncioFavoritosPesquisa(unicos[i], info.sku, origem);
                    }
                    await aguardarControleFavoritos();
                    const anunciosNovos = unicos.filter(anuncioFavoritosProdutoNovo);
                    const removidosPorCondicao = unicos.length - anunciosNovos.length;
                    if (removidosPorCondicao > 0) {
                        mostrarBalaoFavoritosStatus(`SKU ${info.sku}: removidos ${removidosPorCondicao} anuncio(s) marcados como usados.`);
                    }
                    const preparoDadosAvant = prepararAnunciosFavoritosRankingComDadosAvant(anunciosNovos);
                    const rankingBase = montarRankingFavoritosComFallback(preparoDadosAvant.anuncios, info.sku);
                    let filtroIa = {
                        anuncios: limitarAnunciosFavoritosRanking(rankingBase),
                        removidos: [],
                        removidosTotal: 0,
                        usouIa: false
                    };
                    if (usarIaRanking) {
                        try {
                            filtroIa = await filtrarAnunciosFavoritosPorIa(info, rankingBase, {
                                usarIa: true,
                                itemSidebar: item,
                                maxConfirmados: 8
                            });
                        } catch (err) {
                            if (mlFavoritosCancelado || (err && (err.canceladoFavoritos || err.name === 'AbortError'))) throw err;
                            console.warn('IA de favoritos falhou; mantendo ranking normal:', err);
                            mostrarBalaoFavoritosStatus(`SKU ${info.sku}: IA nao concluiu a comparacao. Mantive o ranking normal. ${err && err.message ? err.message : ''}`, {
                                erro: true,
                                tempoMs: 6500
                            });
                        }
                    }
                    await aguardarControleFavoritos();
                    if (filtroIa.removidosTotal) {
                        mostrarBalaoFavoritosStatus(`SKU ${info.sku}: IA removeu ${filtroIa.removidosTotal} anuncio(s) fora do produto do cadastro.`);
                    }
                    const grupoRanking = {
                        ...info,
                        sku: info.sku || item.sku || '',
                        loja: info.loja || item.loja || favoritosLojaSelecionadaParaApi() || '',
                        opcoes_promocao: clonarOpcoesPromocaoFavoritos(opcoesPromocao),
                        anuncios: usarIaRanking && filtroIa.usouIa
                            ? filtroIa.anuncios
                            : limitarAnunciosFavoritosRanking(rankingBase),
                        removidos_ia: filtroIa.removidos,
                        removidos_ia_total: filtroIa.removidosTotal,
                        usou_ia: !!filtroIa.usouIa,
                        ia_confirmados: filtroIa.confirmados || 0,
                        ia_max_confirmados: filtroIa.maxConfirmados || (usarIaRanking ? 8 : 0)
                    };
                    grupos.push(grupoRanking);
                    guardarResultadoRankingFavorito(grupoRanking);
                    renderizarFavoritosPesquisaResultados(grupos);
                    desmarcarSkuFavoritosProcessado(item);
                }

                const totalAnuncios = grupos.reduce((acc, grupo) => acc + (grupo.anuncios || []).length, 0);
                const entradaHistorico = registrarHistoricoFavoritos(grupos);
                if (!entradaHistorico) {
                    if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Favoritos sem ranking salvo';
                    if (mlWorkModalSubtitleEl) mlWorkModalSubtitleEl.textContent = `${grupos.length} SKU(s), nenhum anuncio rankeado.`;
                    mostrarBalaoFavoritosStatus('Favoritos terminou sem anuncios rankeados. Nada foi salvo no historico; o Mercado Livre/Avant Pro nao retornou dados para as pesquisas.', {
                        erro: true,
                        tempoMs: 9000,
                        larga: true
                    });
                    fecharNavegadorFavoritosAposColeta('favoritos-sem-ranking');
                    return;
                }
                mostrarBalaoFavoritosStatus(`Favoritos concluido: ${grupos.length} SKU(s), ${totalAnuncios} anuncio(s) rankeado(s).`, {
                    tempoMs: 7000
                });
                const grupoParaAbrir = grupos.find(grupo => grupo && grupo.sku && Array.isArray(grupo.anuncios) && grupo.anuncios.length)
                    || grupos.find(grupo => grupo && grupo.sku);
                if (grupoParaAbrir && grupoParaAbrir.sku) {
                    favMlSkuSelecionado = grupoParaAbrir.sku;
                    favMlLojaSelecionada = grupoParaAbrir.loja || favoritosLojaSelecionadaParaApi() || '';
                    favMlHistoricoExecucaoSelecionadaId = FAV_ML_RANKING_ATUAL_ID;
                }
                if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Favoritos concluídos';
                if (mlWorkModalSubtitleEl) mlWorkModalSubtitleEl.textContent = `${grupos.length} SKU(s), ${totalAnuncios} anuncio(s) rankeado(s).`;
                if (grupoParaAbrir && grupoParaAbrir.sku) {
                    carregarFavoritosAnunciosSku(grupoParaAbrir.sku, grupoParaAbrir.loja || favMlLojaSelecionada, {
                        manterRankingSelecionado: true
                    });
                }
                const finalizouEmSegundoPlano = mlFavoritosExecucaoEmSegundoPlano;
                mlFavoritosEmExecucao = false;
                mlFavoritosExecucaoEmSegundoPlano = false;
                mlFavoritosPausado = false;
                mlFavoritosJobUltimoStatus = null;
                atualizarFiltroAzulFavoritos();
                atualizarContadorSkuSidebarSelecionados();
                esconderBalaoFavoritosStatus();
                fecharNavegadorFavoritosAposColeta('favoritos-renderer-concluido');
                if (!finalizouEmSegundoPlano) mudarAba('favoritos');
            } catch (err) {
                if (mlFavoritosCancelado || (err && (err.canceladoFavoritos || err.name === 'AbortError'))) {
                    if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Favoritos cancelado';
                    mostrarBalaoFavoritosStatus('Favoritos cancelado pelo usuario.', {
                        tempoMs: 5000
                    });
                } else if (typeof erroEhLoginMercadoLivreFavoritos === 'function' && erroEhLoginMercadoLivreFavoritos(err)) {
                    manterNavegadorVisivelAposErro = true;
                    mlFavoritosExecucaoEmSegundoPlano = false;
                    if (typeof mudarAba === 'function') {
                        try { mudarAba('navegador'); } catch (_err) {}
                    }
                    if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Login do Mercado Livre necessario';
                    if (mlWorkModalSubtitleEl) mlWorkModalSubtitleEl.textContent = 'Conclua o acesso no navegador interno e tente novamente.';
                    abrirBalaoResultadosMl({
                        titulo: 'Login Mercado Livre necessario',
                        subtitulo: 'Conclua o acesso ou verificacao no navegador interno.',
                        mostrarFavoritos: true,
                        browserCompleto: true,
                        forcarExibicao: true
                    });
                    if (typeof agendarAtualizacaoPosicaoNavegadorMlShell === 'function') {
                        setTimeout(() => agendarAtualizacaoPosicaoNavegadorMlShell(), 120);
                    }
                    mostrarBalaoFavoritosStatus(err && err.message ? err.message : 'O Mercado Livre pediu login/verificacao no navegador interno. Conclua o acesso e tente Fazer favoritos novamente.', {
                        erro: true,
                        larga: true,
                        titulo: 'Login Mercado Livre necessario',
                        tempoMs: 12000
                    });
                } else if (typeof erroEhLoginAvantProFavoritos === 'function' && erroEhLoginAvantProFavoritos(err)) {
                    manterNavegadorVisivelAposErro = true;
                    mlFavoritosExecucaoEmSegundoPlano = false;
                    if (typeof mudarAba === 'function') {
                        try { mudarAba('navegador'); } catch (_err) {}
                    }
                    if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Avant Pro sem dados';
                    if (mlWorkModalSubtitleEl) mlWorkModalSubtitleEl.textContent = 'Confirme manualmente no navegador interno se o Avant Pro esta pronto e tente novamente.';
                    abrirBalaoResultadosMl({
                        titulo: 'Avant Pro sem dados',
                        subtitulo: 'Confirme manualmente no navegador interno se o Avant Pro esta pronto.',
                        mostrarFavoritos: true,
                        browserCompleto: true,
                        forcarExibicao: true
                    });
                    if (typeof agendarAtualizacaoPosicaoNavegadorMlShell === 'function') {
                        setTimeout(() => agendarAtualizacaoPosicaoNavegadorMlShell(), 120);
                    }
                    mostrarBalaoFavoritosStatus(err && err.message ? err.message : 'Avant Pro nao retornou dados coletaveis. Confirme manualmente no navegador interno se o Avant Pro esta pronto e tente Fazer favoritos novamente.', {
                        erro: true,
                        larga: true,
                        titulo: 'Avant Pro sem dados',
                        tempoMs: 15000
                    });
                } else if (typeof erroEhColetaMercadoLivreFavoritos === 'function' && erroEhColetaMercadoLivreFavoritos(err)) {
                    manterNavegadorVisivelAposErro = true;
                    mlFavoritosExecucaoEmSegundoPlano = false;
                    if (typeof mudarAba === 'function') {
                        try { mudarAba('navegador'); } catch (_err) {}
                    }
                    if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Coleta ML/Avant sem dados';
                    if (mlWorkModalSubtitleEl) mlWorkModalSubtitleEl.textContent = 'Confira o navegador interno e tente novamente.';
                    abrirBalaoResultadosMl({
                        titulo: 'Coleta ML/Avant sem dados',
                        subtitulo: 'Confira se o Mercado Livre carregou e se o Avant Pro esta conectado.',
                        mostrarFavoritos: true,
                        browserCompleto: true,
                        forcarExibicao: true
                    });
                    if (typeof agendarAtualizacaoPosicaoNavegadorMlShell === 'function') {
                        setTimeout(() => agendarAtualizacaoPosicaoNavegadorMlShell(), 120);
                    }
                    mostrarBalaoFavoritosStatus(err && err.message ? err.message : 'Mercado Livre/Avant Pro nao retornou dados coletaveis. Confira o navegador interno e tente novamente.', {
                        erro: true,
                        larga: true,
                        titulo: 'Coleta ML/Avant sem dados',
                        tempoMs: 15000
                    });
                } else {
                    if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Erro ao fazer favoritos';
                    mostrarBalaoFavoritosStatus(`Erro ao fazer favoritos: ${err && err.message ? err.message : err}`, {
                        erro: true
                    });
                }
            } finally {
                mlFavoritosEmExecucao = false;
                mlFavoritosExecucaoEmSegundoPlano = false;
                mlFavoritosCancelado = false;
                mlFavoritosPausado = false;
                mlFavoritosJobUltimoStatus = null;
                mlFavoritosAbortController = null;
                if (executarEmBackground && !manterNavegadorVisivelAposErro) pararNavegadorFavoritosBackground();
                atualizarFiltroAzulFavoritos();
                atualizarContadorSkuSidebarSelecionados();
            }
        }

        function montarPayloadFavoritosJob(selecionados, quantidade, opcoesPromocao, usarIaRanking) {
            return {
                loja: favoritosLojaSelecionadaParaApi(mlSkuLojaSelecionada || skuLojaSelecionada || ''),
                quantidade_pesquisas: quantidade,
                usar_ia: !!usarIaRanking,
                max_confirmados_ia: 8,
                opcoes_promocao: clonarOpcoesPromocaoFavoritos(opcoesPromocao),
                modo_coleta: 'avantpro_browser',
                selecionados: selecionados.map(item => {
                    const info = montarPesquisasFavoritosSku(item, quantidade);
                    return {
                        sku: info.sku,
                        loja: info.loja,
                        titulo: info.titulo,
                        descricao: info.descricao,
                        termos: info.termos,
                        cadastro: info.cadastro
                    };
                })
            };
        }

        function favoritosJobStatusMensagem(status) {
            const etapa = String(status && status.etapa || '').trim();
            const mensagem = String(status && status.mensagem || '').trim();
            const sku = String(status && status.sku_atual || '').trim();
            const indice = Number(status && status.indice) || 0;
            const total = Number(status && status.total) || 0;
            const percentual = Number(status && status.percentual) || 0;
            const partes = [];
            if (total) partes.push(`${Math.min(indice || 0, total)}/${total}`);
            if (percentual) partes.push(`${Math.max(0, Math.min(100, Math.round(percentual)))}%`);
            if (sku) partes.push(`SKU ${sku}`);
            if (etapa) partes.push(etapa);
            if (mensagem) partes.push(mensagem);
            return partes.join(' - ') || 'Favoritos rodando em background.';
        }

        function encontrarSelecionadoFavoritosJob(grupo) {
            const sku = skuChaveSku(grupo && grupo.sku);
            const loja = skuNormalizarLoja(grupo && grupo.loja);
            return (mlFavoritosJobSelecionadosAtual || []).find(item => {
                if (skuChaveSku(item && item.sku) !== sku) return false;
                if (!loja) return true;
                return skuNormalizarLoja(item && item.loja) === loja;
            }) || null;
        }

        function normalizarGrupoFavoritosJob(grupo) {
            if (!grupo || typeof grupo !== 'object') return null;
            const anuncios = Array.isArray(grupo.anuncios) ? grupo.anuncios.filter(Boolean) : [];
            const ordenados = limitarAnunciosFavoritosRanking(ordenarAnunciosFavoritosRanking(anuncios, grupo.sku));
            return {
                ...grupo,
                opcoes_promocao: grupo.opcoes_promocao || clonarOpcoesPromocaoFavoritos(mlFavoritosOpcoesPromocaoAtual),
                anuncios: ordenados
            };
        }

        function aplicarResultadosParciaisFavoritosJob(status) {
            const grupos = (Array.isArray(status && status.resultados_parciais) ? status.resultados_parciais : [])
                .map(normalizarGrupoFavoritosJob)
                .filter(Boolean);
            grupos.forEach(grupo => {
                guardarResultadoRankingFavorito(grupo);
                const itemSelecionado = encontrarSelecionadoFavoritosJob(grupo);
                if (itemSelecionado) desmarcarSkuFavoritosProcessado(itemSelecionado);
            });
            return grupos;
        }

        function agendarRenderFavoritosJob(grupos) {
            mlFavoritosJobGruposRender = Array.isArray(grupos) ? grupos.slice() : [];
            mlFavoritosJobRenderPendente = true;
            if (mlFavoritosJobRenderRaf) return;
            const raf = typeof requestAnimationFrame === 'function'
                ? requestAnimationFrame
                : (callback) => setTimeout(callback, 16);
            mlFavoritosJobRenderRaf = raf(() => {
                mlFavoritosJobRenderRaf = 0;
                if (!mlFavoritosJobRenderPendente) return;
                mlFavoritosJobRenderPendente = false;
                renderizarFavoritosPesquisaResultados(mlFavoritosJobGruposRender);
                mlFavoritosJobUltimaQtdRender = mlFavoritosJobGruposRender.length;
            });
        }

        async function fetchFavoritosJob(url, options = {}) {
            const response = await fetch(url, {
                ...options,
                headers: options.headers || headersJsonAutenticado()
            });
            let data = null;
            try {
                data = await response.json();
            } catch (_err) {}
            if (!response.ok) {
                throw new Error((data && data.detail) || `HTTP ${response.status}`);
            }
            return data || {};
        }

        const FAVORITOS_JOB_PROXIMA_COLETA_PATH = '/proxima-coleta';
        const FAVORITOS_JOB_COLETA_TERMO_PATH = '/coleta-termo';

        async function iniciarWorkerFavoritosAvantProJob(opcoes = {}) {
            if (opcoes.permitirRotinaAntigaAposLoginAvantPro !== true) {
                pararFavoritosAposConfirmacaoAvantProNovaEtapa({
                    manterNavegadorVisivel: false
                });
                return;
            }
            let loginAvantPrimeiraPesquisaPreparado = opcoes.avantLoginConfirmadoPeloUsuario === true;
            while (mlFavoritosEmExecucao && mlFavoritosJobIdAtual && !mlFavoritosCancelado) {
                await aguardarControleFavoritos();
                const proxima = await fetchFavoritosJob(`/api/favoritos/jobs/${encodeURIComponent(mlFavoritosJobIdAtual)}${FAVORITOS_JOB_PROXIMA_COLETA_PATH}`, {
                    method: 'GET'
                });
                receberStatusFavoritosJob(proxima);
                if (!proxima.pending) break;
                const destino = proxima.coleta || {};
                if (!destino.termo) throw new Error('Backend nao retornou termo para coleta visual.');
                const loginAvantAntesDaColeta = !loginAvantPrimeiraPesquisaPreparado;
                let avantLoginPrePesquisaConfirmado = false;
                if (loginAvantAntesDaColeta) {
                    if (typeof prepararAvantProAntesDaPesquisaFavoritos !== 'function') {
                        throw new Error('Nao consegui preparar o Avant Pro antes da pesquisa. Reabra a tela de Favoritos e tente novamente.');
                    }
                    mostrarBalaoFavoritosStatus(`SKU ${destino.sku || ''}: preparando Avant Pro antes da pesquisa.`, {
                        manterNavegadorVisivel: true,
                        larga: true,
                        titulo: 'Preparando Avant Pro'
                    });
                    if (typeof abrirMercadoLivreNoPrograma === 'function') {
                        if (typeof mlUrlInput !== 'undefined' && mlUrlInput) {
                            mlUrlInput.value = typeof ML_DEFAULT_URL !== 'undefined'
                                ? ML_DEFAULT_URL
                                : 'https://www.mercadolivre.com.br/';
                        }
                        await abrirMercadoLivreNoPrograma({
                            titulo: 'Fazendo Favorito! Aguarde...',
                            subtitulo: `Preparando Avant Pro - SKU ${destino.sku || ''}`,
                            mostrarFavoritos: true,
                            browserCompleto: true,
                            forcarExibicao: true
                        });
                    }
                    const preparacaoAvant = await prepararAvantProAntesDaPesquisaFavoritos({
                        termo: destino.termo,
                        aguardarConexao: true,
                        timeoutMs: 24000,
                        pollMs: 350
                    });
                    if (!preparacaoAvantPrePesquisaConfirmada(preparacaoAvant)) {
                        throw new Error(`Avant Pro nao confirmou o login antes da pesquisa "${destino.termo}". Confirme o e-mail em Ferramentas e aguarde o aviso de obrigado antes de pesquisar.`);
                    }
                    avantLoginPrePesquisaConfirmado = true;
                    verificarCancelamentoFavoritos();
                }
                mostrarBalaoFavoritosStatus(`SKU ${destino.sku || ''}: pesquisando ${destino.termo} pelo navegador interno.`);
                const limiteAnunciosPrimeiraPesquisa = Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80;
                const anuncios = await buscarAnunciosFavoritosPorTermo(destino.termo, {
                    sku: destino.sku,
                    loja: destino.loja,
                    titulo: 'Fazendo Favorito! Aguarde...',
                    subtitulo: `SKU ${destino.sku || ''} - Pesquisa ${destino.campo || ''}`,
                    exigirAvantPro: true,
                    loginAvantAntesDaColeta,
                    avantLoginPrePesquisaConfirmado,
                    maxAnuncios: limiteAnunciosPrimeiraPesquisa,
                    maxCliquesAvant: 12,
                    maxPosicoesRolagem: 12
                });
                loginAvantPrimeiraPesquisaPreparado = true;
                const status = await fetchFavoritosJob(`/api/favoritos/jobs/${encodeURIComponent(mlFavoritosJobIdAtual)}${FAVORITOS_JOB_COLETA_TERMO_PATH}`, {
                    method: 'POST',
                    body: JSON.stringify({
                        sku: destino.sku,
                        loja: destino.loja,
                        campo: destino.campo,
                        termo: destino.termo,
                        url_confirmada: destino.url || '',
                        card_count: Array.isArray(anuncios) ? anuncios.length : 0,
                        anuncios,
                        metricas: {
                            origem: 'avantpro_browser',
                            loginAvantAntesDaColeta
                        }
                    })
                });
                receberStatusFavoritosJob(status);
            }
        }

        function pararPollingFavoritosJob() {
            if (mlFavoritosJobPollTimer) {
                clearTimeout(mlFavoritosJobPollTimer);
                mlFavoritosJobPollTimer = null;
            }
            mlFavoritosJobPollAtivo = false;
        }

        function reagendarPollingFavoritosJob(delayMs = 850) {
            pararPollingFavoritosJob();
            if (!mlFavoritosJobIdAtual || !mlFavoritosEmExecucao) return;
            mlFavoritosJobPollTimer = setTimeout(pollFavoritosJobAtual, Math.max(250, Number(delayMs) || 850));
        }

        async function pollFavoritosJobAtual() {
            if (!mlFavoritosJobIdAtual || mlFavoritosJobPollAtivo) return;
            mlFavoritosJobPollAtivo = true;
            try {
                const status = await fetchFavoritosJob(`/api/favoritos/jobs/${encodeURIComponent(mlFavoritosJobIdAtual)}/status`, {
                    method: 'GET',
                    headers: obterAuthHeaders()
                });
                receberStatusFavoritosJob(status);
                const estado = String(status.status || '').toLowerCase();
                if (!['done', 'error', 'canceled'].includes(estado)) {
                    reagendarPollingFavoritosJob(estado === 'paused' ? 1200 : 850);
                }
            } catch (err) {
                if (!mlFavoritosCancelado) {
                    mostrarBalaoFavoritosStatus(`Falha ao consultar progresso do job: ${err && err.message ? err.message : err}`, {
                        erro: true,
                        tempoMs: 5000
                    });
                    reagendarPollingFavoritosJob(1600);
                }
            } finally {
                mlFavoritosJobPollAtivo = false;
            }
        }

        function receberStatusFavoritosJob(status) {
            mlFavoritosJobUltimoStatus = status || null;
            const jobIdStatus = String(status && (status.job_id || status.jobId || '') || '').trim();
            if (jobIdStatus) mlFavoritosJobIdAtual = jobIdStatus;
            const estado = String(status && status.status || '').toLowerCase();
            const grupos = aplicarResultadosParciaisFavoritosJob(status);
            if (grupos.length !== mlFavoritosJobUltimaQtdRender || ['done', 'error', 'canceled'].includes(estado)) {
                agendarRenderFavoritosJob(grupos);
            }
            if (mlWorkModalSubtitleEl) {
                mlWorkModalSubtitleEl.textContent = favoritosJobStatusMensagem(status);
            }
            mostrarBalaoFavoritosStatus(favoritosJobStatusMensagem(status), {
                larga: true
            });
            atualizarFiltroAzulFavoritos();
            atualizarContadorSkuSidebarSelecionados();
            if (estado === 'done') {
                finalizarFavoritosJob(status, grupos);
            } else if (estado === 'error') {
                finalizarFavoritosJob(status, grupos, new Error(status.erro || 'Erro no job de favoritos.'));
            } else if (estado === 'canceled') {
                finalizarFavoritosJob(status, grupos, criarErroFavoritosCancelado());
            }
        }

        function limparEstadoFavoritosJob() {
            mlFavoritosEmExecucao = false;
            mlFavoritosExecucaoEmSegundoPlano = false;
            mlFavoritosCancelado = false;
            mlFavoritosPausado = false;
            mlFavoritosAbortController = null;
            mlFavoritosJobIdAtual = '';
            mlFavoritosJobSelecionadosAtual = [];
            mlFavoritosJobUltimoStatus = null;
            atualizarFiltroAzulFavoritos();
            atualizarContadorSkuSidebarSelecionados();
        }

        function finalizarFavoritosJob(status, grupos, erro = null) {
            if (mlFavoritosJobFinalTratado) return;
            mlFavoritosJobFinalTratado = true;
            pararPollingFavoritosJob();
            const estado = String(status && status.status || '').toLowerCase();
            const finalizouEmSegundoPlano = mlFavoritosExecucaoEmSegundoPlano;
            const totalAnuncios = grupos.reduce((acc, grupo) => acc + (Array.isArray(grupo.anuncios) ? grupo.anuncios.length : 0), 0);
            if (!erro && estado === 'done') {
                const entradaHistorico = registrarHistoricoFavoritos(grupos);
                if (!entradaHistorico) {
                    if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Favoritos sem ranking salvo';
                    if (mlWorkModalSubtitleEl) mlWorkModalSubtitleEl.textContent = `${grupos.length} SKU(s), nenhum anuncio rankeado.`;
                    mostrarBalaoFavoritosStatus('Job concluido, mas nenhum anuncio entrou no ranking. Nada foi salvo no historico; confira os campos de pesquisa ou tente novamente.', {
                        erro: true,
                        tempoMs: 9000,
                        larga: true
                    });
                    pararNavegadorFavoritosBackground();
                    limparEstadoFavoritosJob();
                    return;
                }
                const grupoParaAbrir = grupos.find(grupo => grupo && grupo.sku && Array.isArray(grupo.anuncios) && grupo.anuncios.length)
                    || grupos.find(grupo => grupo && grupo.sku);
                if (grupoParaAbrir && grupoParaAbrir.sku) {
                    favMlSkuSelecionado = grupoParaAbrir.sku;
                    favMlLojaSelecionada = grupoParaAbrir.loja || favoritosLojaSelecionadaParaApi() || '';
                    favMlHistoricoExecucaoSelecionadaId = FAV_ML_RANKING_ATUAL_ID;
                }
                if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Favoritos concluidos';
                if (mlWorkModalSubtitleEl) mlWorkModalSubtitleEl.textContent = `${grupos.length} SKU(s), ${totalAnuncios} anuncio(s) rankeado(s).`;
                mostrarBalaoFavoritosStatus(`Favoritos concluido: ${grupos.length} SKU(s), ${totalAnuncios} anuncio(s) rankeado(s).`, {
                    tempoMs: 7000
                });
                if (grupoParaAbrir && grupoParaAbrir.sku) {
                    carregarFavoritosAnunciosSku(grupoParaAbrir.sku, grupoParaAbrir.loja || favMlLojaSelecionada, {
                        manterRankingSelecionado: true
                    });
                }
                fecharNavegadorFavoritosAposColeta('favoritos-renderer-finalizado');
                if (!finalizouEmSegundoPlano) mudarAba('favoritos');
            } else if (estado === 'canceled' || (erro && erro.canceladoFavoritos)) {
                if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Favoritos cancelado';
                mostrarBalaoFavoritosStatus('Favoritos cancelado pelo usuario.', {
                    tempoMs: 5000
                });
            } else {
                if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Erro ao fazer favoritos';
                mostrarBalaoFavoritosStatus(`Erro ao fazer favoritos: ${erro && erro.message ? erro.message : (status && status.erro) || 'erro desconhecido'}`, {
                    erro: true
                });
            }
            pararNavegadorFavoritosBackground();
            limparEstadoFavoritosJob();
        }

        async function enviarComandoFavoritosJobAtual(acao) {
            if (!mlFavoritosJobIdAtual) return null;
            const status = await fetchFavoritosJob(`/api/favoritos/jobs/${encodeURIComponent(mlFavoritosJobIdAtual)}/${acao}`, {
                method: 'POST'
            });
            receberStatusFavoritosJob(status);
            return status;
        }

        async function cancelarFavoritosJobAtualServidor() {
            if (!mlFavoritosJobIdAtual) return null;
            return enviarComandoFavoritosJobAtual('cancel');
        }

        async function pausarFavoritosJobAtual() {
            if (!mlFavoritosEmExecucao) return;
            if (!mlFavoritosJobIdAtual) {
                mlFavoritosPausado = true;
                mlFavoritosJobUltimoStatus = { status: 'paused', mensagem: 'Favoritos pausado. Clique em retomar para continuar.' };
                atualizarFiltroAzulFavoritos();
                atualizarContadorSkuSidebarSelecionados();
                mostrarBalaoFavoritosStatus('Favoritos pausado. A coleta continua a partir da proxima etapa ao retomar.', {
                    larga: true
                });
                return;
            }
            mostrarBalaoFavoritosStatus('Pausando favoritos ao final da etapa atual...');
            try {
                await enviarComandoFavoritosJobAtual('pause');
            } catch (err) {
                mostrarBalaoFavoritosStatus(`Nao foi possivel pausar: ${err && err.message ? err.message : err}`, {
                    erro: true,
                    tempoMs: 4500
                });
            }
        }

        async function retomarFavoritosJobAtual() {
            if (!mlFavoritosEmExecucao) return;
            if (!mlFavoritosJobIdAtual) {
                mlFavoritosPausado = false;
                mlFavoritosJobUltimoStatus = { status: 'running', mensagem: 'Favoritos retomado.' };
                atualizarFiltroAzulFavoritos();
                atualizarContadorSkuSidebarSelecionados();
                mostrarBalaoFavoritosStatus('Favoritos retomado.');
                return;
            }
            mostrarBalaoFavoritosStatus('Retomando favoritos...');
            try {
                await enviarComandoFavoritosJobAtual('resume');
            } catch (err) {
                mostrarBalaoFavoritosStatus(`Nao foi possivel retomar: ${err && err.message ? err.message : err}`, {
                    erro: true,
                    tempoMs: 4500
                });
            }
        }

        async function prepararNavegadorFavoritosBackground() {
            if (!window.electronAPI) return;
            try {
                window.__JK_FAVORITOS_WORKER_BROWSER_ACTIVE = true;
                if (window.top && window.top !== window && typeof window.top.postMessage === 'function') {
                    window.top.postMessage({
                        channel: 'jk-favoritos-worker-enable',
                        payload: {
                            active: true,
                            status: 'running',
                            message: 'Favoritos rodando em segundo plano.'
                        }
                    }, '*');
                }
                if (typeof window.electronAPI.startFavoritosJobBrowserBackground === 'function') {
                    await window.electronAPI.startFavoritosJobBrowserBackground(ML_DEFAULT_URL);
                    return;
                }
                if (typeof window.electronAPI.showEmbeddedMlBrowser !== 'function') return;
                await window.electronAPI.showEmbeddedMlBrowser(ML_DEFAULT_URL, {
                    left: -20000,
                    top: -20000,
                    width: 1280,
                    height: 900,
                    background: true
                });
            } catch (err) {
                console.warn('Nao foi possivel manter navegador ML em background:', err);
            }
        }

        function pararNavegadorFavoritosBackground() {
            if (!window.electronAPI) return;
            try {
                window.__JK_FAVORITOS_WORKER_BROWSER_ACTIVE = false;
                if (window.top && window.top !== window && typeof window.top.postMessage === 'function') {
                    window.top.postMessage({
                        channel: 'jk-favoritos-worker-done',
                        payload: {
                            active: false,
                            status: 'stopped',
                            message: 'Favoritos finalizado.'
                        }
                    }, '*');
                }
                if (typeof window.electronAPI.stopFavoritosJobBrowserBackground === 'function') {
                    window.electronAPI.stopFavoritosJobBrowserBackground().catch(() => {});
                    return;
                }
                if (typeof window.electronAPI.hideEmbeddedMlBrowser === 'function') {
                    window.electronAPI.hideEmbeddedMlBrowser({ destroy: true, reason: 'favoritos-background-stop' }).catch(() => {});
                }
            } catch (_err) {}
        }

        window.addEventListener('message', (event) => {
            const data = event && event.data ? event.data : {};
            if (!data || data.channel !== 'jk-favoritos-worker-action') return;
            const action = String(data.action || data.payload && data.payload.action || '').toLowerCase();
            if (!action) return;
            if (action === 'pause') {
                pausarFavoritosJobAtual();
            } else if (action === 'resume') {
                retomarFavoritosJobAtual();
            } else if (action === 'cancel') {
                if (typeof cancelarFavoritosEmExecucao === 'function') cancelarFavoritosEmExecucao();
                else mlFavoritosCancelado = true;
            }
        });

        async function fazerFavoritosSkusSelecionados(opcoes = {}) {
            if (opcoes && opcoes.type && opcoes.target) opcoes = {};
            if (opcoes.usarRendererAntigo === true || opcoes.usarBackendJob === false) {
                return fazerFavoritosSkusSelecionadosRendererAntigo({
                    ...opcoes,
                    background: false,
                    mostrarNavegadorMl: true
                });
            }
            if (mlFavoritosEmExecucao) return;
            const selecionadosPayload = normalizarSelecionadosFavoritosExecucao(opcoes.selecionadosPayload);
            const selecionados = selecionadosPayload.length ? selecionadosPayload : obterSkusSelecionadosSidebar();
            if (!selecionados.length) {
                mostrarBalaoFavoritosStatus('Selecione pelo menos um SKU no sidebar antes de fazer favoritos.', {
                    erro: true,
                    tempoMs: 3500
                });
                return;
            }
            mostrarBalaoFavoritosStatus(`${selecionados.length} SKU(s) selecionado(s). Escolha a quantidade de pesquisas.`, {
                manterAcoes: false
            });
            const quantidade = Number(opcoes.quantidade) || await perguntarQuantidadePesquisasFavoritos();
            if (!quantidade) return;
            const opcoesPromocao = opcoes.opcoesPromocao || await perguntarOpcoesPromocaoFavoritos();
            if (!opcoesPromocao) return;
            mlFavoritosOpcoesPromocaoAtual = clonarOpcoesPromocaoFavoritos(opcoesPromocao);
            selecionados.forEach(item => salvarOpcoesPromocaoFavoritosSku(item && item.sku, mlFavoritosOpcoesPromocaoAtual));
            const usarIaRanking = favoritosUsarIaRankingAtivo();
            const avantLoginConfirmadoPeloUsuario = opcoes.avantLoginConfirmadoPeloUsuario === true
                || await perguntarLoginAvantProAntesFavoritos({
                    selecionados,
                    quantidade,
                    opcoesPromocao
            });
            if (!avantLoginConfirmadoPeloUsuario) return;
            await abrirPrimeiraPesquisaFavoritosAposConfirmacaoAvantPro(selecionados, quantidade, {
                opcoesPromocao,
                usarIaRanking
            });
            return;

            const executarEmBackground = opcoes.background === true;
            mlFavoritosEmExecucao = true;
            mlFavoritosExecucaoEmSegundoPlano = executarEmBackground;
            mlFavoritosCancelado = false;
            mlFavoritosPausado = false;
            mlFavoritosAbortController = typeof AbortController === 'function' ? new AbortController() : null;
            mlFavoritosJobIdAtual = '';
            mlFavoritosJobUltimoStatus = null;
            mlFavoritosJobUltimaQtdRender = -1;
            mlFavoritosJobSelecionadosAtual = selecionados.slice();
            mlFavoritosJobFinalTratado = false;
            pararPollingFavoritosJob();
            const mostrarNavegadorMl = opcoes.mostrarNavegadorMl !== false;
            if (executarEmBackground) {
                await prepararNavegadorFavoritosBackground();
            } else if (mostrarNavegadorMl) {
                if (typeof mudarAba === 'function') {
                    try { mudarAba('navegador'); } catch (_err) {}
                }
            } else {
                ocultarNavegadorMlShellDefinitivo();
            }
            abrirBalaoResultadosMl({
                titulo: 'Fazendo Favorito! Aguarde...',
                subtitulo: `${selecionados.length} SKU(s), ${quantidade} pesquisa(s) por SKU${usarIaRanking ? ', IA ligada' : ''}`,
                mostrarFavoritos: true,
                browserCompleto: true,
                forcarExibicao: mostrarNavegadorMl && !executarEmBackground
            });
            atualizarFiltroAzulFavoritos();
            atualizarContadorSkuSidebarSelecionados();
            if (mlFavoritosPanelEl) mlFavoritosPanelEl.classList.remove('hidden');
            if (mlFavoritosListEl) mlFavoritosListEl.innerHTML = '';
            if (mlFavoritosEmptyEl) mlFavoritosEmptyEl.classList.add('hidden');
            mostrarBalaoFavoritosStatus(`Iniciando job de favoritos: ${selecionados.length} SKU(s), ${quantidade} pesquisa(s) por SKU, ${resumoOpcoesPromocaoFavoritos(opcoesPromocao)}${usarIaRanking ? ', IA verifica anuncios fora do produto' : ''}.`);

            try {
                if (!Array.isArray(skuDados) || !skuDados.length) {
                    mostrarBalaoFavoritosStatus('Carregando dados dos SKUs antes de iniciar o job...');
                    await carregarSkuFavoritos();
                    verificarCancelamentoFavoritos();
                }

                const payload = montarPayloadFavoritosJob(selecionados, quantidade, opcoesPromocao, usarIaRanking);
                const data = await fetchFavoritosJob('/api/favoritos/jobs', {
                    method: 'POST',
                    body: JSON.stringify(payload)
                });
                mlFavoritosJobIdAtual = data.job_id || '';
                mlFavoritosJobUltimoStatus = data;
                if (!mlFavoritosJobIdAtual) throw new Error('Backend nao retornou job_id para favoritos.');
                receberStatusFavoritosJob(data);
                iniciarWorkerFavoritosAvantProJob({
                    avantLoginConfirmadoPeloUsuario
                })
                    .then(() => {
                        if (mlFavoritosEmExecucao && mlFavoritosJobIdAtual) reagendarPollingFavoritosJob(350);
                    })
                    .catch((err) => {
                        if (mlFavoritosCancelado || (err && (err.canceladoFavoritos || err.name === 'AbortError'))) return;
                        const mensagemErro = `Erro na coleta visual do Favoritos: ${err && err.message ? err.message : err}`;
                        mostrarBalaoFavoritosStatus(mensagemErro, {
                            erro: true,
                            larga: true
                        });
                        const jobIdErro = mlFavoritosJobIdAtual;
                        const erroColeta = err instanceof Error ? err : new Error(mensagemErro);
                        const statusErro = {
                            ...(mlFavoritosJobUltimoStatus || {}),
                            job_id: jobIdErro,
                            status: 'error',
                            erro: mensagemErro,
                            mensagem: 'A coleta visual do Favoritos falhou.'
                        };
                        if (jobIdErro) {
                            fetchFavoritosJob(`/api/favoritos/jobs/${encodeURIComponent(jobIdErro)}/cancel`, {
                                method: 'POST'
                            }).catch(() => {});
                        }
                        finalizarFavoritosJob(statusErro, aplicarResultadosParciaisFavoritosJob(statusErro), erroColeta);
                    });
            } catch (err) {
                pararPollingFavoritosJob();
                pararNavegadorFavoritosBackground();
                limparEstadoFavoritosJob();
                if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Erro ao iniciar favoritos';
                mostrarBalaoFavoritosStatus(`Erro ao iniciar favoritos: ${err && err.message ? err.message : err}`, {
                    erro: true
                });
            }
        }

        async function rankearAvulsoMercadoLivre(opcoes = {}) {
            if (mlFavoritosEmExecucao) return;
            const termos = normalizarTermosPesquisaFavoritos(opcoes.termos || obterTermosPesquisaAvulsaMl());
            if (!termos.length) {
                mostrarBalaoFavoritosStatus('Preencha pelo menos uma pesquisa para fazer o rankeamento avulso.', {
                    erro: true,
                    tempoMs: 3500
                });
                const primeiroCampo = obterCamposPesquisaAvulsaMl()[0];
                if (primeiroCampo && primeiroCampo.input) primeiroCampo.input.focus();
                return;
            }

            if (opcoes.termos) preencherCamposPesquisaAvulsaMl(termos);
            const skuRanking = String(opcoes.sku || 'AVULSO').trim() || 'AVULSO';
            const rankingAvulso = opcoes.avulso !== undefined ? !!opcoes.avulso : skuChaveSku(skuRanking) === 'avulso';
            const tituloProcesso = opcoes.tituloProcesso || (rankingAvulso ? 'Ranqueamento avulso' : `Refazendo ranking ${skuRanking}`);
            mlFavoritosEmExecucao = true;
            mlFavoritosExecucaoEmSegundoPlano = false;
            mlFavoritosCancelado = false;
            mlFavoritosAbortController = typeof AbortController === 'function' ? new AbortController() : null;
            abrirBalaoResultadosMl({
                titulo: tituloProcesso,
                subtitulo: `${termos.length} pesquisa(s)`,
                mostrarFavoritos: true,
                browserCompleto: true
            });
            atualizarFiltroAzulFavoritos();
            if (mlFavoritosPanelEl) mlFavoritosPanelEl.classList.remove('hidden');
            if (mlFavoritosListEl) mlFavoritosListEl.innerHTML = '';
            if (mlFavoritosEmptyEl) {
                mlFavoritosEmptyEl.textContent = 'Ranqueando pesquisas avulsas...';
                mlFavoritosEmptyEl.classList.remove('hidden');
            }
            mostrarBalaoFavoritosStatus(`Ranqueamento avulso iniciado com ${termos.length} pesquisa(s).`);

            try {
                const coletados = [];
                const resumosColeta = [];
                const tituloAvulso = String(opcoes.titulo || '').trim()
                    || termos.map(item => item.termo).filter(Boolean).join(' | ').slice(0, 180)
                    || 'Pesquisas avulsas';
                const info = {
                    sku: skuRanking,
                    loja: opcoes.loja || favoritosLojaSelecionadaParaApi(),
                    titulo: tituloAvulso,
                    termos,
                    avulso: rankingAvulso,
                    pesquisa_avulsa: rankingAvulso
                };
                for (let pesquisaIndex = 0; pesquisaIndex < termos.length; pesquisaIndex += 1) {
                    verificarCancelamentoFavoritos();
                    const pesquisa = termos[pesquisaIndex];
                    mostrarBalaoFavoritosStatus(`Ranqueamento avulso: pesquisando Pesquisa ${pesquisa.campo} - ${pesquisa.termo}`);
                    const anuncios = await coletarAnunciosPrimeiraPaginaFavoritosSemFluxoAntigo(info, pesquisa, {
                        primeiraPesquisa: pesquisaIndex === 0,
                        maxAnuncios: Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80,
                        titulo: tituloProcesso,
                        subtitulo: `Pesquisa ${pesquisa.campo}: ${pesquisa.termo}`,
                        avulso: true
                    });
                    verificarCancelamentoFavoritos();
                    let normalizadosPesquisa = anuncios.map(anuncio => normalizarAnuncioFavoritosPesquisa(anuncio, skuRanking, pesquisa));
                    await enriquecerAnunciosFavoritosRanking(normalizadosPesquisa);
                    normalizadosPesquisa = normalizadosPesquisa.map(anuncio => normalizarAnuncioFavoritosPesquisa(anuncio, skuRanking, pesquisa));
                    normalizadosPesquisa.forEach(anuncio => {
                        coletados.push(anuncio);
                    });
                    const resumoPesquisa = montarResumoPesquisaFavoritos(
                        pesquisaIndex + 1,
                        Number(anuncios.__favoritosTotalVisiveis) || anuncios.length,
                        normalizadosPesquisa
                    );
                    resumoPesquisa.tempo_esgotado = !!anuncios.__favoritosTempoEsgotado;
                    resumosColeta.push({
                        ...resumoPesquisa,
                        termo: pesquisa.termo,
                        campo: pesquisa.campo
                    });
                    mostrarBalaoFavoritosStatus(formatarResumoPesquisaFavoritos(resumoPesquisa), {
                        manterNavegadorVisivel: true,
                        larga: true,
                        titulo: 'Resumo da coleta'
                    });
                }

                const unicos = deduplicarAnunciosFavoritos(coletados)
                    .filter(anuncioFavoritosCandidatoRanking);
                mostrarBalaoFavoritosStatus(`Ranqueamento avulso: enriquecendo ${unicos.length} anuncio(s)...`);
                verificarCancelamentoFavoritos();
                await enriquecerAnunciosFavoritosRanking(unicos);
                for (let i = 0; i < unicos.length; i += 1) {
                    const origem = Array.isArray(unicos[i].pesquisas_origem) && unicos[i].pesquisas_origem[0]
                        ? { termo: unicos[i].pesquisas_origem[0], campo: '' }
                        : { termo: '', campo: '' };
                    unicos[i] = normalizarAnuncioFavoritosPesquisa(unicos[i], skuRanking, origem);
                }
                verificarCancelamentoFavoritos();
                const anunciosNovos = unicos.filter(anuncioFavoritosProdutoNovo);
                const anunciosElegiveis = anunciosNovos.length ? anunciosNovos : unicos;
                const preparoDadosAvant = prepararAnunciosFavoritosRankingComDadosAvant(anunciosElegiveis);
                const rankingBaseAvulso = montarRankingFavoritosComFallback(preparoDadosAvant.anuncios, info.sku);
                let filtroIa = {
                    anuncios: limitarAnunciosFavoritosRanking(rankingBaseAvulso),
                    removidos: [],
                    removidosTotal: 0,
                    usouIa: false
                };
                if (opcoes.usarIaRanking === true) {
                    try {
                        filtroIa = await filtrarAnunciosFavoritosPorIa(info, preparoDadosAvant.anuncios);
                    } catch (err) {
                        if (mlFavoritosCancelado || (err && (err.canceladoFavoritos || err.name === 'AbortError'))) throw err;
                        console.warn('IA de favoritos avulso falhou; mantendo ranking normal:', err);
                    }
                }
                verificarCancelamentoFavoritos();
                let anunciosAvulsosRanking = montarRankingFavoritosComFallback(filtroIa.anuncios, info.sku);
                if (!anunciosAvulsosRanking.length && rankingBaseAvulso.length) {
                    anunciosAvulsosRanking = rankingBaseAvulso;
                }
                if (!anunciosAvulsosRanking.length && unicos.length) {
                    anunciosAvulsosRanking = montarRankingFavoritosComFallback(unicos, info.sku);
                }
                const grupoRanking = {
                    ...info,
                    sku: info.sku || skuRanking,
                    loja: info.loja || favoritosLojaSelecionadaParaApi() || '',
                    avulso: true,
                    anuncios: limitarAnunciosFavoritosRanking(anunciosAvulsosRanking),
                    removidos_ia: filtroIa.removidos,
                    removidos_ia_total: filtroIa.removidosTotal,
                    usou_ia: !!filtroIa.usouIa,
                    ia_confirmados: filtroIa.confirmados || 0,
                    ia_max_confirmados: filtroIa.maxConfirmados || 0,
                    fonte_coleta: 'avantpro_primeira_pagina_nova',
                    resumo_coleta: resumosColeta,
                    total_coletado: unicos.length,
                    total_com_dados_avant: filtrarAnunciosFavoritosComDadosAvant(unicos).length
                };
                guardarResultadoRankingFavorito(grupoRanking);
                registrarHistoricoFavoritos([grupoRanking]);
                favMlSkuSelecionado = grupoRanking.sku;
                favMlLojaSelecionada = grupoRanking.loja || favoritosLojaSelecionadaParaApi() || '';
                favMlHistoricoExecucaoSelecionadaId = '';
                favMlAnunciosSkuAtual = [];
                renderizarFavoritosPesquisaResultados([grupoRanking]);
                renderizarFavoritosOutrosAnuncios(grupoRanking.sku);
                if (grupoRankingFavoritosEhAvulso(grupoRanking)) {
                    renderizarFavoritosAnunciosMl([], grupoRanking.sku);
                } else {
                    carregarFavoritosAnunciosSku(grupoRanking.sku, grupoRanking.loja || favMlLojaSelecionada);
                }
                mostrarBalaoFavoritosStatus(`Ranqueamento avulso concluido: ${grupoRanking.anuncios.length} anuncio(s) rankeado(s).`, {
                    tempoMs: 7000
                });
                if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Ranqueamento avulso concluido';
                if (mlWorkModalSubtitleEl) mlWorkModalSubtitleEl.textContent = `${grupoRanking.anuncios.length} anuncio(s) rankeado(s).`;
                const finalizouEmSegundoPlano = mlFavoritosExecucaoEmSegundoPlano;
                mlFavoritosEmExecucao = false;
                mlFavoritosExecucaoEmSegundoPlano = false;
                atualizarFiltroAzulFavoritos();
                esconderBalaoFavoritosStatus();
                fecharNavegadorFavoritosAposColeta('favoritos-avulso-concluido');
                if (!finalizouEmSegundoPlano) mudarAba('favoritos');
            } catch (err) {
                if (mlFavoritosCancelado || (err && (err.canceladoFavoritos || err.name === 'AbortError'))) {
                    mostrarBalaoFavoritosStatus('Ranqueamento avulso cancelado pelo usuario.', {
                        tempoMs: 5000
                    });
                } else {
                    mostrarBalaoFavoritosStatus(`Erro no rankeamento avulso: ${err && err.message ? err.message : err}`, {
                        erro: true
                    });
                }
            } finally {
                mlFavoritosEmExecucao = false;
                mlFavoritosExecucaoEmSegundoPlano = false;
                mlFavoritosCancelado = false;
                mlFavoritosAbortController = null;
                atualizarFiltroAzulFavoritos();
            }
        }

        function formatarPrecoFavoritosMl(valor) {
            if (valor === null || valor === undefined || valor === '') return '';
            const numero = Number(valor);
            if (!Number.isFinite(numero)) return String(valor);
            return numero.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
        }

        function renderizarFavoritosSkuSidebar() {
            if (!favMlSkuSidebarListEl || !favMlSkuSidebarEmptyEl || !favMlSkuSidebarCountEl) return;
            const todosItens = montarItensSkuSidebarMercadoLivre();
            const termoBusca = favMlSkuSidebarSearchEl ? favMlSkuSidebarSearchEl.value || '' : '';
            const itensFiltrados = filtrarItensSkuSidebarMercadoLivre(todosItens, termoBusca);
            const itens = aplicarSkuExatoHistoricoSidebar(itensFiltrados, termoBusca);
            const selecionadoChave = skuChaveSku(favMlSkuSelecionado);
            const selecionadoLoja = skuNormalizarLoja(favMlLojaSelecionada);
            const selecionadoTemRanking = selecionadoChave && favMlHistoricoExecucaoSelecionadaId && obterGrupoRankingFavoritosSku(favMlSkuSelecionado);
            const selecionadoTemHistorico = selecionadoChave && obterHistoricoMaisRecenteSku(favMlSkuSelecionado);

            favMlSkuSidebarListEl.innerHTML = '';
            favMlSkuSidebarCountEl.textContent = todosItens.length
                ? (itens.length === todosItens.length ? `${todosItens.length} SKU(s)` : `${itens.length} de ${todosItens.length} SKU(s)`)
                : '';
            favMlSkuSidebarEmptyEl.textContent = termoBusca.trim()
                ? 'Nenhum SKU encontrado para essa pesquisa.'
                : favoritosWarningLojaAtual
                ? favoritosWarningLojaAtual
                : mlSkuLojaSelecionada
                ? 'Nenhum SKU encontrado nos anuncios ativos desta loja.'
                : 'Escolha uma loja integrada para carregar os SKUs dos anuncios ativos.';
            favMlSkuSidebarEmptyEl.classList.toggle('hidden', itens.length > 0);
            if (deveBuscarSkuRemotoParaTermo(itensFiltrados, termoBusca)) {
                agendarBuscaRemotaSkuSidebarMercadoLivre(termoBusca);
            }

            if (selecionadoChave && !selecionadoTemRanking && !selecionadoTemHistorico && !todosItens.some(item => skuChaveSku(item.sku) === selecionadoChave && (!selecionadoLoja || skuNormalizarLoja(item.loja) === selecionadoLoja))) {
                favMlSkuSelecionado = '';
                favMlLojaSelecionada = '';
                favMlHistoricoExecucaoSelecionadaId = '';
                favMlAnunciosSkuAtual = [];
                renderizarFavoritosAnunciosMl([], '');
                renderizarFavoritosOutrosAnuncios('');
            }

            itens.forEach(item => {
                const button = document.createElement('button');
                button.type = 'button';
                const itemAtivo = skuChaveSku(item.sku) === skuChaveSku(favMlSkuSelecionado)
                    && (!favMlLojaSelecionada || skuNormalizarLoja(item.loja) === skuNormalizarLoja(favMlLojaSelecionada));
                button.className = 'ml-sku-sidebar-item' + (itemAtivo ? ' is-active' : '');

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
                    if (itemAtivo) {
                        if (typeof limparFavoritosSkuSelecionado === 'function') {
                            limparFavoritosSkuSelecionado();
                        }
                        return;
                    }
                    carregarFavoritosAnunciosSku(item.sku, item.loja, { itemSidebar: item });
                });
                favMlSkuSidebarListEl.appendChild(button);
            });
        }

        function criarCelulaTextoFavoritos(valor, opcoes = {}) {
            const td = document.createElement('td');
            const texto = document.createElement('div');
            texto.className = 'favoritos-ml-cell-text'
                + (opcoes.long ? ' is-long' : '')
                + (opcoes.nowrap ? ' is-nowrap' : '');
            texto.textContent = valor === null || valor === undefined ? '' : String(valor);
            td.appendChild(texto);
            return td;
        }

        function vendedorInternacionalFavoritos(...valores) {
            const texto = valores
                .map(valor => String(valor || ''))
                .join(' ')
                .normalize('NFD')
                .replace(/[\u0300-\u036f]/g, '')
                .toLowerCase();
            return /\b(vendedor\s+internacional|internacional|international\s+seller|cross\s*border)\b/.test(texto);
        }

        function criarCelulaMlbLojaFavoritos(mlb, loja, status = '', tipo = '', vendedor = '') {
            const td = document.createElement('td');
            td.className = 'ml-favoritos-mlb-cell';
            const codigo = document.createElement('span');
            codigo.className = 'ml-favoritos-mlb-main';
            codigo.textContent = mlb === null || mlb === undefined ? '' : String(mlb);
            td.appendChild(codigo);
            const lojaTexto = String(loja || '').trim();
            if (lojaTexto) {
                const lojaEl = document.createElement('span');
                lojaEl.className = 'ml-favoritos-mlb-loja';
                if (vendedorInternacionalFavoritos(lojaTexto, vendedor)) {
                    const icone = document.createElement('span');
                    icone.className = 'ml-favoritos-seller-international';
                    icone.textContent = '✈';
                    icone.title = 'Vendedor internacional';
                    lojaEl.appendChild(icone);
                    lojaEl.appendChild(document.createTextNode(` ${lojaTexto}`));
                } else {
                    lojaEl.textContent = lojaTexto;
                }
                td.appendChild(lojaEl);
            }
            const statusTexto = String(status || '').trim();
            if (statusTexto) {
                const statusEl = document.createElement('span');
                statusEl.className = 'ml-favoritos-mlb-status';
                statusEl.textContent = statusTexto;
                td.appendChild(statusEl);
            }
            const tipoTexto = String(tipo || '').trim();
            if (tipoTexto) {
                const tipoEl = document.createElement('span');
                tipoEl.className = 'ml-favoritos-mlb-tipo';
                tipoEl.textContent = tipoTexto;
                td.appendChild(tipoEl);
            }
            const botaoVendedor = criarBotaoIgnorarVendedorFavoritos(vendedor);
            if (botaoVendedor) {
                const action = document.createElement('div');
                action.className = 'ml-favoritos-seller-action';
                action.appendChild(botaoVendedor);
                td.appendChild(action);
            }
            return td;
        }

        function obterNomeLojaVendedoraHistoricoFavoritos(anuncio) {
            if (!anuncio || typeof anuncio !== 'object') return '';
            const seller = anuncio.seller && typeof anuncio.seller === 'object' ? anuncio.seller : {};
            const sellerInfo = anuncio.seller_info && typeof anuncio.seller_info === 'object' ? anuncio.seller_info : {};
            const candidatos = [
                anuncio.loja_vendedora,
                anuncio.vendedor,
                anuncio.seller_name,
                anuncio.sellerName,
                anuncio.seller_nickname,
                anuncio.sellerNickname,
                anuncio.nickname,
                anuncio.official_store_name,
                anuncio.officialStoreName,
                seller.nickname,
                seller.name,
                seller.seller_nickname,
                sellerInfo.nickname,
                sellerInfo.name,
                sellerInfo.seller_nickname
            ];
            for (const valor of candidatos) {
                const nome = limparNomeLojaVendedoraHistoricoFavoritos(valor);
                if (vendedorValido(nome)) return nome;
            }
            return '';
        }

        function limparNomeLojaVendedoraHistoricoFavoritos(valor) {
            return String(valor || '')
                .replace(/^(vendido\s+por|loja\s+oficial|oficial\s+loja)\s*/i, '')
                .replace(/&quot;|\\\"/g, '"')
                .replace(/\s+/g, ' ')
                .trim();
        }

        function carregarLayoutTabelasFavoritos() {
            try {
                const data = JSON.parse(localStorage.getItem(ML_FAVORITOS_TABLE_LAYOUT_KEY) || '{}');
                return data && typeof data === 'object' ? data : {};
            } catch (_err) {
                return {};
            }
        }

        function salvarLayoutTabelasFavoritos() {
            try {
                localStorage.setItem(ML_FAVORITOS_TABLE_LAYOUT_KEY, JSON.stringify(favoritosTableLayout || {}));
            } catch (_err) {}
        }

        function obterLayoutTabelaFavoritos(tableId) {
            if (!favoritosTableLayout || typeof favoritosTableLayout !== 'object') favoritosTableLayout = {};
            if (!favoritosTableLayout[tableId]) favoritosTableLayout[tableId] = { order: [], widths: {} };
            if (!Array.isArray(favoritosTableLayout[tableId].order)) favoritosTableLayout[tableId].order = [];
            if (!favoritosTableLayout[tableId].widths || typeof favoritosTableLayout[tableId].widths !== 'object') favoritosTableLayout[tableId].widths = {};
            return favoritosTableLayout[tableId];
        }

        function obterOrdemBaseTabelaFavoritos(table) {
            const headerRow = table && table.tHead && table.tHead.rows ? table.tHead.rows[0] : null;
            if (!headerRow) return [];
            if (table.dataset.baseOrder) return table.dataset.baseOrder.split(',').filter(Boolean);
            const keys = Array.from(headerRow.cells).map((th, idx) => {
                const key = th.dataset.colKey || `col_${idx}`;
                th.dataset.colKey = key;
                return key;
            });
            table.dataset.baseOrder = keys.join(',');
            return keys;
        }

        function criarColgroupTabelaFavoritos(table, keys) {
            let colgroup = table.querySelector('colgroup');
            if (!colgroup) {
                colgroup = document.createElement('colgroup');
                table.insertBefore(colgroup, table.firstChild);
            }
            const existentes = new Map(Array.from(colgroup.children).map(col => [col.dataset.colKey, col]));
            colgroup.innerHTML = '';
            keys.forEach(key => {
                const col = existentes.get(key) || document.createElement('col');
                col.dataset.colKey = key;
                colgroup.appendChild(col);
            });
            return colgroup;
        }

        function marcarCelulasTabelaFavoritos(table, baseOrder) {
            const bodies = Array.from(table.tBodies || []);
            bodies.forEach(tbody => {
                Array.from(tbody.rows).forEach(row => {
                    if (row.dataset.colKeysReady === '1') return;
                    Array.from(row.cells).forEach((cell, idx) => {
                        cell.dataset.colKey = baseOrder[idx] || `col_${idx}`;
                    });
                    row.dataset.colKeysReady = '1';
                });
            });
        }

        function aplicarOrdemTabelaFavoritos(table, keys) {
            const headerRow = table && table.tHead && table.tHead.rows ? table.tHead.rows[0] : null;
            if (!headerRow) return;
            const ordenarLinha = (row) => {
                const cells = Array.from(row.cells);
                const map = new Map(cells.map(cell => [cell.dataset.colKey, cell]));
                keys.forEach(key => {
                    const cell = map.get(key);
                    if (cell) row.appendChild(cell);
                });
            };
            ordenarLinha(headerRow);
            Array.from(table.tBodies || []).forEach(tbody => {
                Array.from(tbody.rows).forEach(ordenarLinha);
            });
        }

        const ML_FAVORITOS_COL_WIDTHS_PADRAO = {
            'fav-ml-anuncios': {
                ordem: 58,
                alterar: 62,
                foto: 54,
                mlb: 120,
                titulo: 150,
                preco: 130,
                simulador: 120
            },
            'fav-outros-anuncios': {
                acoes: 58,
                foto: 54,
                mlb: 125,
                media: 130,
                titulo: 160
            }
        };

        const ML_FAVORITOS_COL_WIDTHS_MIN = {
            acoes: 58,
            ordem: 54,
            alterar: 58,
            foto: 54,
            mlb: 110,
            media: 140,
            titulo: 130,
            preco: 140,
            simulador: 118
        };

        function obterLarguraDisponivelTabelaFavoritos(table) {
            const wrap = table && table.closest ? table.closest('.favoritos-ml-table-wrap, .ml-favoritos-table-wrap') : null;
            const rect = wrap ? wrap.getBoundingClientRect() : null;
            const largura = rect && Number.isFinite(rect.width) ? Math.floor(rect.width) : 0;
            return largura > 0 ? Math.max(240, largura - 2) : 0;
        }

        function obterLarguraColunaFavoritos(table, key, layout) {
            const salva = Number(layout.widths && layout.widths[key]);
            if (Number.isFinite(salva) && salva > 0) return salva;
            const tableId = table.dataset.tableId || table.id || '';
            const padrao = ML_FAVORITOS_COL_WIDTHS_PADRAO[tableId] && ML_FAVORITOS_COL_WIDTHS_PADRAO[tableId][key];
            return Number.isFinite(padrao) && padrao > 0 ? padrao : null;
        }

        function compactarLargurasTabelaFavoritos(table, keys, layout) {
            const larguras = keys.map(key => obterLarguraColunaFavoritos(table, key, layout));
            if (larguras.some(width => !Number.isFinite(width) || width <= 0)) return larguras;
            const disponivel = obterLarguraDisponivelTabelaFavoritos(table);
            const soma = larguras.reduce((total, width) => total + width, 0);
            if (!disponivel || soma <= disponivel) return larguras;

            const minimos = keys.map(key => ML_FAVORITOS_COL_WIDTHS_MIN[key] || 54);
            const somaMinimos = minimos.reduce((total, width) => total + width, 0);
            if (somaMinimos >= disponivel) {
                return minimos;
            }

            const folgaOriginal = larguras.reduce((total, width, idx) => total + Math.max(0, width - minimos[idx]), 0);
            const folgaDestino = Math.max(0, disponivel - somaMinimos);
            if (folgaOriginal <= 0) return minimos;
            return larguras.map((width, idx) => {
                const extra = Math.max(0, width - minimos[idx]);
                return Math.round(minimos[idx] + (extra * folgaDestino / folgaOriginal));
            });
        }

        function aplicarLargurasTabelaFavoritos(table, keys, layout) {
            const colgroup = criarColgroupTabelaFavoritos(table, keys);
            const largurasCompactadas = compactarLargurasTabelaFavoritos(table, keys, layout);
            const somaLarguras = largurasCompactadas
                .map(width => Number(width))
                .filter(width => Number.isFinite(width) && width > 0)
                .reduce((total, width) => total + Math.max(44, width), 0);
            const disponivel = obterLarguraDisponivelTabelaFavoritos(table);
            if (somaLarguras > 0 && disponivel > 0 && somaLarguras > disponivel) {
                table.style.minWidth = `${somaLarguras}px`;
                table.style.width = `${somaLarguras}px`;
            } else {
                table.style.minWidth = '';
                table.style.width = '';
            }
            keys.forEach((key, idx) => {
                const width = Number(largurasCompactadas[idx]);
                const col = colgroup.children[idx];
                if (!col) return;
                if (Number.isFinite(width) && width > 0) {
                    col.style.width = `${Math.max(44, width)}px`;
                } else {
                    col.style.width = '';
                }
            });
        }

        function obterOrdemAtualTabelaFavoritos(table, baseOrder, layout) {
            const order = (layout.order || []).filter(key => baseOrder.includes(key));
            baseOrder.forEach(key => {
                if (!order.includes(key)) order.push(key);
            });
            return order;
        }

        function moverColunaTabelaFavoritos(table, fromKey, toKey) {
            const tableId = table.dataset.tableId;
            const baseOrder = obterOrdemBaseTabelaFavoritos(table);
            const layout = obterLayoutTabelaFavoritos(tableId);
            const order = obterOrdemAtualTabelaFavoritos(table, baseOrder, layout);
            const fromIndex = order.indexOf(fromKey);
            const toIndex = order.indexOf(toKey);
            if (fromIndex < 0 || toIndex < 0 || fromIndex === toIndex) return;
            order.splice(fromIndex, 1);
            order.splice(toIndex, 0, fromKey);
            layout.order = order;
            salvarLayoutTabelasFavoritos();
            prepararTabelaFavoritosEditavel(table);
            agendarSincronizarLinhasFavoritos();
        }

        function iniciarResizeColunaFavoritos(event, table, th) {
            event.preventDefault();
            event.stopPropagation();
            const tableId = table.dataset.tableId;
            const key = th.dataset.colKey;
            const layout = obterLayoutTabelaFavoritos(tableId);
            const startX = event.clientX;
            const startWidth = th.getBoundingClientRect().width;
            document.body.classList.add('favoritos-resizing-table');

            const mover = (moveEvent) => {
                const nextWidth = Math.max(54, Math.round(startWidth + (moveEvent.clientX - startX)));
                layout.widths[key] = nextWidth;
                const baseOrder = obterOrdemBaseTabelaFavoritos(table);
                const keys = obterOrdemAtualTabelaFavoritos(table, baseOrder, layout);
                aplicarLargurasTabelaFavoritos(table, keys, layout);
                agendarSincronizarLinhasFavoritos();
            };
            const parar = () => {
                document.body.classList.remove('favoritos-resizing-table');
                window.removeEventListener('pointermove', mover);
                window.removeEventListener('pointerup', parar);
                window.removeEventListener('pointercancel', parar);
                salvarLayoutTabelasFavoritos();
                agendarSincronizarLinhasFavoritos();
            };

            window.addEventListener('pointermove', mover);
            window.addEventListener('pointerup', parar, { once: true });
            window.addEventListener('pointercancel', parar, { once: true });
        }

        function prepararTabelaFavoritosEditavel(table) {
            if (!table) return;
            const tableId = table.dataset.tableId || table.id || '';
            if (!tableId) return;
            const headerRow = table.tHead && table.tHead.rows ? table.tHead.rows[0] : null;
            if (!headerRow) return;
            const baseOrder = obterOrdemBaseTabelaFavoritos(table);
            const layout = obterLayoutTabelaFavoritos(tableId);
            const keys = obterOrdemAtualTabelaFavoritos(table, baseOrder, layout);
            marcarCelulasTabelaFavoritos(table, baseOrder);
            aplicarOrdemTabelaFavoritos(table, keys);
            aplicarLargurasTabelaFavoritos(table, keys, layout);

            Array.from(headerRow.cells).forEach(th => {
                if (th.dataset.favEditableReady === '1') return;
                th.dataset.favEditableReady = '1';
                th.draggable = true;
                const handle = document.createElement('span');
                handle.className = 'fav-col-resize-handle';
                handle.title = 'Arraste para ajustar a largura da coluna';
                handle.addEventListener('pointerdown', event => iniciarResizeColunaFavoritos(event, table, th));
                th.appendChild(handle);
                th.addEventListener('dragstart', event => {
                    if (event.target && event.target.classList && event.target.classList.contains('fav-col-resize-handle')) return;
                    th.classList.add('is-dragging');
                    event.dataTransfer.effectAllowed = 'move';
                    event.dataTransfer.setData('text/plain', th.dataset.colKey || '');
                });
                th.addEventListener('dragend', () => {
                    th.classList.remove('is-dragging');
                    Array.from(headerRow.cells).forEach(cell => cell.classList.remove('is-drop-target'));
                });
                th.addEventListener('dragover', event => {
                    event.preventDefault();
                    event.dataTransfer.dropEffect = 'move';
                    th.classList.add('is-drop-target');
                });
                th.addEventListener('dragleave', () => th.classList.remove('is-drop-target'));
                th.addEventListener('drop', event => {
                    event.preventDefault();
                    th.classList.remove('is-drop-target');
                    const fromKey = event.dataTransfer.getData('text/plain');
                    moverColunaTabelaFavoritos(table, fromKey, th.dataset.colKey);
                });
            });
        }

        function atualizarTabelasFavoritosEditaveis() {
            prepararTabelaFavoritosEditavel(favMlAnunciosTableEl);
            prepararTabelaFavoritosEditavel(favOutrosAnunciosTableEl);
            agendarSincronizarLinhasFavoritos();
        }

        function obterAlturaMinimaLinhaFavoritos() {
            const origem = favMlTablesLayoutEl || document.documentElement;
            const valor = window.getComputedStyle(origem).getPropertyValue('--favoritos-ml-row-height');
            const numero = parseFloat(valor);
            return Number.isFinite(numero) && numero > 0 ? numero : 86;
        }

        function limparAlturaLinhaFavoritos(row) {
            if (!row) return;
            row.style.height = '';
            Array.from(row.cells || []).forEach(cell => {
                cell.style.height = '';
            });
        }

        function aplicarAlturaLinhaFavoritos(row, altura) {
            if (!row || !Number.isFinite(altura) || altura <= 0) return;
            const valor = `${altura}px`;
            row.style.height = valor;
            Array.from(row.cells || []).forEach(cell => {
                cell.style.height = valor;
            });
        }

        function sincronizarAlturasLinhasFavoritos() {
            favoritosSyncLinhasRaf = 0;
            if (!favMlAnunciosTableEl || !favOutrosAnunciosTableEl) return;

            const cabecalhos = [
                favMlAnunciosTableEl.tHead && favMlAnunciosTableEl.tHead.rows ? favMlAnunciosTableEl.tHead.rows[0] : null,
                favOutrosAnunciosTableEl.tHead && favOutrosAnunciosTableEl.tHead.rows ? favOutrosAnunciosTableEl.tHead.rows[0] : null
            ].filter(Boolean);
            cabecalhos.forEach(limparAlturaLinhaFavoritos);
            if (cabecalhos.length) {
                const alturaCabecalho = Math.ceil(Math.max(...cabecalhos.map(row => row.getBoundingClientRect().height || 0)));
                cabecalhos.forEach(row => aplicarAlturaLinhaFavoritos(row, alturaCabecalho));
            }

            const linhasMl = Array.from(favMlAnunciosBodyEl && favMlAnunciosBodyEl.rows ? favMlAnunciosBodyEl.rows : []);
            const linhasRanking = Array.from(favOutrosAnunciosBodyEl && favOutrosAnunciosBodyEl.rows ? favOutrosAnunciosBodyEl.rows : []);
            [...linhasMl, ...linhasRanking].forEach(limparAlturaLinhaFavoritos);

            const alturaMinima = obterAlturaMinimaLinhaFavoritos();
            const total = Math.max(linhasMl.length, linhasRanking.length);
            for (let i = 0; i < total; i += 1) {
                const linhaMl = linhasMl[i] || null;
                const linhaRanking = linhasRanking[i] || null;
                const linhaRankingSincronizavel = linhaRanking && !linhaRanking.classList.contains('is-history-list-row')
                    ? linhaRanking
                    : null;
                const altura = Math.ceil(Math.max(
                    alturaMinima,
                    linhaMl ? linhaMl.getBoundingClientRect().height || 0 : 0,
                    linhaRankingSincronizavel ? linhaRankingSincronizavel.getBoundingClientRect().height || 0 : 0
                ));
                aplicarAlturaLinhaFavoritos(linhaMl, altura);
                aplicarAlturaLinhaFavoritos(linhaRankingSincronizavel, altura);
            }
        }

        function agendarSincronizarLinhasFavoritos() {
            const agendar = window.requestAnimationFrame || ((callback) => window.setTimeout(callback, 0));
            const cancelar = window.cancelAnimationFrame || window.clearTimeout;
            if (favoritosSyncLinhasRaf) cancelar(favoritosSyncLinhasRaf);
            favoritosSyncLinhasRaf = agendar(sincronizarAlturasLinhasFavoritos);
        }

        window.addEventListener('resize', () => {
            prepararTabelaFavoritosEditavel(favMlAnunciosTableEl);
            prepararTabelaFavoritosEditavel(favOutrosAnunciosTableEl);
            agendarSincronizarLinhasFavoritos();
        });

        function inicializarLarguraTabelasFavoritos() {
            if (!favMlTablesLayoutEl || favMlTablesLayoutEl.dataset.resizerReady === '1') return;
            favMlTablesLayoutEl.dataset.resizerReady = '1';
            favMlTablesLayoutEl.classList.add('is-resizable');
            const split = Number(favoritosTableLayout && favoritosTableLayout.split);
            if (favoritosTableLayout && favoritosTableLayout.splitUserDefined === true && Number.isFinite(split)) {
                favMlTablesLayoutEl.style.setProperty('--favoritos-ml-left-width', `${Math.min(72, Math.max(32, split))}%`);
            } else {
                favMlTablesLayoutEl.style.setProperty('--favoritos-ml-left-width', '1fr');
                delete favoritosTableLayout.split;
                favoritosTableLayout.splitUserDefined = false;
                salvarLayoutTabelasFavoritos();
            }
            const panels = favMlTablesLayoutEl.querySelectorAll(':scope > .panel');
            if (panels.length < 2) return;
            const handle = document.createElement('div');
            handle.className = 'favoritos-ml-split-resizer';
            handle.title = 'Arraste para ajustar a largura das tabelas';
            panels[0].after(handle);

            handle.addEventListener('pointerdown', event => {
                event.preventDefault();
                document.body.classList.add('favoritos-resizing-table');
                const mover = (moveEvent) => {
                    const rect = favMlTablesLayoutEl.getBoundingClientRect();
                    if (!rect.width) return;
                    const percent = Math.min(72, Math.max(32, ((moveEvent.clientX - rect.left) / rect.width) * 100));
                    favMlTablesLayoutEl.style.setProperty('--favoritos-ml-left-width', `${percent.toFixed(2)}%`);
                    favoritosTableLayout.split = Number(percent.toFixed(2));
                    favoritosTableLayout.splitUserDefined = true;
                    agendarSincronizarLinhasFavoritos();
                };
                const parar = () => {
                    document.body.classList.remove('favoritos-resizing-table');
                    window.removeEventListener('pointermove', mover);
                    window.removeEventListener('pointerup', parar);
                    window.removeEventListener('pointercancel', parar);
                    salvarLayoutTabelasFavoritos();
                    agendarSincronizarLinhasFavoritos();
                };
                window.addEventListener('pointermove', mover);
                window.addEventListener('pointerup', parar, { once: true });
                window.addEventListener('pointercancel', parar, { once: true });
            });
        }
