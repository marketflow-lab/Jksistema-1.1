// Extracted from 07-execucao-render-layout.js lines 1-473.
        function prepararAnunciosFavoritosRankingComDadosAvant(todos) {
            const comDadosAvant = window.FavoritosV2.searchRanking.publicApi.search.filtrarAnunciosFavoritosComDadosAvant(todos);
            const baseRanking = todos;
            return {
                anuncios: baseRanking,
                comDadosAvant,
                removidosPorDadosAvant: 0
            };
        }
        function pararFavoritosAposConfirmacaoAvantProNovaEtapa(opcoes = {}) {
            if (typeof window.FavoritosV2.searchRanking.publicApi.auth.limparBotaoContinuarLoginAvantProFavoritos === 'function') {
                window.FavoritosV2.searchRanking.publicApi.auth.limparBotaoContinuarLoginAvantProFavoritos();
            }
            mlFavoritosEmExecucao = false;
            mlFavoritosExecucaoEmSegundoPlano = false;
            mlFavoritosCancelado = false;
            mlFavoritosPausado = false;
            window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('Avant Pro confirmado. Rotina antiga removida; aguardando a nova etapa ser criada.', {
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
                opcoes_promocao: window.FavoritosV2.promotionEffectuation.publicApi.options.clonarOpcoesPromocaoFavoritos(opcoesPromocao),
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

        let mlFavoritosHistoricosIndividuaisSalvosExecucao = new Set();

        function resetarHistoricosIndividuaisFavoritosExecucao() {
            mlFavoritosHistoricosIndividuaisSalvosExecucao = new Set();
        }

        function chaveHistoricoIndividualFavoritos(grupo) {
            const normalizarSku = typeof skuChaveSku === 'function'
                ? skuChaveSku
                : (value) => String(value || '').trim().toLowerCase();
            const normalizarLoja = typeof skuNormalizarLoja === 'function'
                ? skuNormalizarLoja
                : (value) => String(value || '').trim().toLowerCase();
            const sku = normalizarSku(grupo && grupo.sku);
            if (!sku) return '';
            const lojaFallback = typeof favoritosLojaSelecionadaParaApi === 'function'
                ? favoritosLojaSelecionadaParaApi()
                : '';
            const loja = normalizarLoja(grupo && grupo.loja || lojaFallback || '');
            return `${sku}|${loja}`;
        }

        function registrarHistoricoRankingSkuFavoritosImediato(grupo, opcoes = {}) {
            if (!grupo || !Array.isArray(grupo.anuncios) || !grupo.anuncios.length) return null;
            const chave = chaveHistoricoIndividualFavoritos(grupo);
            if (!chave) return null;
            if (!opcoes.forcar && mlFavoritosHistoricosIndividuaisSalvosExecucao.has(chave)) {
                grupo.historico_salvo_individual = true;
                grupo.historico_salvo_duplicado_ignorado = true;
                return { duplicado: true, chave };
            }
            if (typeof registrarHistoricoFavoritos !== 'function') return null;
            const entrada = registrarHistoricoFavoritos([grupo]);
            if (!entrada) return null;
            mlFavoritosHistoricosIndividuaisSalvosExecucao.add(chave);
            grupo.historico_salvo_individual = true;
            grupo.historico_ranqueamento_id = entrada.id || '';
            grupo.historico_salvo_em = entrada.data_iso || new Date().toISOString();
            return entrada;
        }

        function gruposComRankingFavoritos(grupos) {
            return (Array.isArray(grupos) ? grupos : [])
                .filter(grupo => grupo && Array.isArray(grupo.anuncios) && grupo.anuncios.length);
        }

        function resumoHistoricosIndividuaisFavoritos(grupos) {
            const ranking = gruposComRankingFavoritos(grupos);
            const salvos = ranking.filter(grupo => grupo.historico_salvo_individual || grupo.historico_ranqueamento_id);
            return {
                total: ranking.length,
                salvos,
                entrada: salvos.length
                    ? {
                        individual: true,
                        total_skus: salvos.length,
                        ids: salvos.map(grupo => grupo.historico_ranqueamento_id).filter(Boolean)
                    }
                    : null
            };
        }

        function obterElectronApiFavoritosExecucao() {
            try {
                if (window.electronAPI) return window.electronAPI;
            } catch (_err) {}
            try {
                if (window.top && window.top !== window && window.top.electronAPI) return window.top.electronAPI;
            } catch (_err) {}
            return null;
        }

        function emitirEstadoWorkerFavoritosExecucao(channel, payload = {}) {
            try {
                if (window.top && window.top !== window && typeof window.top.postMessage === 'function') {
                    window.top.postMessage({ channel, payload }, '*');
                }
            } catch (_err) {}
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

        async function verificarBloqueioGlobalMercadoLivreFavoritos(webview, workerId = '') {
            if (!webview || typeof webview.executeJavaScript !== 'function') return null;
            const diagnostico = await webview.executeJavaScript(`
                (function () {
                    var url = String(location.href || '');
                    var texto = String(document.body && document.body.innerText || '').replace(/\\s+/g, ' ').slice(0, 5000).toLowerCase();
                    return { url: url, texto: texto };
                })();
            `, true).catch(() => null);
            const url = String(diagnostico && diagnostico.url || '');
            const texto = String(diagnostico && diagnostico.texto || '');
            const rotaBloqueada = /\/(?:gz\/account-verification|login(?:\/|$)|jms\/[^/]+\/lgz|password\/validation|totp|captcha|security[-_/]?check|identity[-_/]?verification)/i.test(url)
                || /[?&](?:captcha|recaptcha|security_check|identity_verification)=/i.test(url);
            const telaBloqueada = /acesse sua conta|entre na sua conta|verifique sua identidade|confirme que voce e voce|captcha|atividade incomum/.test(texto);
            if (rotaBloqueada || telaBloqueada) {
                const err = typeof window.FavoritosV2.searchRanking.publicApi.status.erroLoginMercadoLivreFavoritos === 'function'
                    ? window.FavoritosV2.searchRanking.publicApi.status.erroLoginMercadoLivreFavoritos('O Mercado Livre pediu login ou verificacao. Corrija o acesso na janela trabalhadora exibida.')
                    : erroNovaColetaFavoritos('O Mercado Livre pediu login ou verificacao.', { loginMercadoLivreNecessario: true });
                err.workerId = workerId;
                throw err;
            }
            return diagnostico;
        }

        function anuncioFavoritosTemLink(anuncio) {
            return !!String(anuncio && (anuncio.url || anuncio.permalink || anuncio.link) || '').trim();
        }

        function anuncioFavoritosTemDadosAvant(anuncio) {
            return window.FavoritosV2.searchRanking.publicApi.search.filtrarAnunciosFavoritosComDadosAvant([anuncio]).length > 0;
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

        function montarResumoPesquisaFavoritos(numeroPesquisa, totalVisiveis, anuncios, resumoColetaOriginal = null) {
            const lista = Array.isArray(anuncios) ? anuncios.filter(Boolean) : [];
            const resumoColetor = resumoColetaOriginal && typeof resumoColetaOriginal === 'object'
                ? resumoColetaOriginal
                : Array.isArray(anuncios) && anuncios.__favoritosResumo && typeof anuncios.__favoritosResumo === 'object'
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
                motivo_encerramento: String(resumoColetor.motivo_encerramento || ''),
                passadas: Number(resumoColetor.passadas) || 0,
                posicoes_percorridas: Number(resumoColetor.posicoes_percorridas) || 0,
                cliques_avant: Number(resumoColetor.cliques_avant) || 0,
                capturados_avant: Number(resumoColetor.capturados_avant) || 0,
                tempo_navegacao_ms: Number(resumoColetor.tempo_navegacao_ms) || 0,
                tempo_materializacao_ms: Number(resumoColetor.tempo_materializacao_ms) || 0,
                tempo_avant_ms: Number(resumoColetor.tempo_avant_ms) || 0,
                tempo_finalizacao_ms: Number(resumoColetor.tempo_finalizacao_ms) || 0,
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
                .filter(window.FavoritosV2.searchRanking.publicApi.listings.anuncioFavoritosCandidatoRanking);
            if (!lista.length) return [];
            try {
                const ordenados = window.FavoritosV2.searchRanking.publicApi.ranking.ordenarAnunciosFavoritosRanking(lista, sku);
                if (Array.isArray(ordenados) && ordenados.length) {
                    return window.FavoritosV2.searchRanking.publicApi.ranking.limitarAnunciosFavoritosRanking(ordenados);
                }
            } catch (err) {
                console.warn('Nao foi possivel ordenar ranking por media; usando ordem da primeira pagina:', err);
            }
            return window.FavoritosV2.searchRanking.publicApi.ranking.limitarAnunciosFavoritosRanking(
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

        function fecharNavegadorFavoritosAposColeta(reason = 'favoritos-coleta-finalizada', opcoes = {}) {
            const api = obterElectronApiFavoritosExecucao();
            const status = String(opcoes.status || 'done').toLowerCase();
            const finishedAt = Number(opcoes.finishedAt) || 0;
            const message = Object.prototype.hasOwnProperty.call(opcoes, 'message')
                ? String(opcoes.message || '')
                : (status === 'done' ? 'Favoritos finalizado.' : 'Favoritos encerrado com erro.');
            if (typeof fecharBalaoResultadosMl === 'function') {
                fecharBalaoResultadosMl({
                    forcar: true,
                    descarregarConteudo: true,
                    destroy: true,
                    reason,
                    preserveAvantProSession: true
                });
            }
            if (api && typeof api.hideEmbeddedMlBrowser === 'function') {
                api.hideEmbeddedMlBrowser({
                    destroy: true,
                    reason,
                    preserveAvantProSession: true
                }).catch(() => {});
            }
            if (api && window.__JK_FAVORITOS_WORKERS_POOL_ACTIVE && typeof api.stopFavoritosWorkersPool === 'function') {
                api.stopFavoritosWorkersPool({
                    destroy: true,
                    reason,
                    status,
                    message,
                    finishedAt
                }).catch(() => {});
            } else if (api && typeof api.stopFavoritosWorkerBrowser === 'function') {
                api.stopFavoritosWorkerBrowser({
                    destroy: true,
                    reason,
                    status,
                    message,
                    finishedAt
                }).catch(() => {});
            } else if (api && typeof api.stopFavoritosJobBrowserBackground === 'function') {
                api.stopFavoritosJobBrowserBackground().catch(() => {});
            }
            try {
                window.__JK_FAVORITOS_WORKER_BROWSER_ACTIVE = false;
                window.__JK_FAVORITOS_WORKERS_POOL_ACTIVE = false;
                emitirEstadoWorkerFavoritosExecucao('jk-favoritos-worker-done', {
                    active: false,
                    status,
                    message,
                    finishedAt
                });
            } catch (_err) {}
        }
