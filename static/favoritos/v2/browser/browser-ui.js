(function (global) {
  'use strict';

  const browser = global.FavoritosV2.browser;
  const pageScripts = browser.pageScripts;

        function obterCamposPesquisaAvulsaMl() {
            return [
                { input: mlSearchTermInput, campo: 1 },
                { input: mlSearchTerm2Input, campo: 2 },
                { input: mlSearchTerm3Input, campo: 3 }
            ].filter(item => item.input);
        }

        function normalizarTermosPesquisaFavoritos(termos) {
            const vistos = new Set();
            const saida = [];
            (Array.isArray(termos) ? termos : []).forEach((item, index) => {
                const termo = String(item && (item.termo || item.term || item.valor || item.value) || item || '').trim();
                const chave = termo.toLowerCase();
                if (!termo || vistos.has(chave)) return;
                vistos.add(chave);
                const campoRaw = item && (item.campo || item.field || item.numero || item.index);
                const campo = Number.parseInt(campoRaw, 10);
                saida.push({
                    campo: Number.isFinite(campo) && campo > 0 ? campo : index + 1,
                    termo
                });
            });
            return saida.slice(0, 3);
        }

        function obterTermosPesquisaAvulsaMl() {
            return normalizarTermosPesquisaFavoritos(
                obterCamposPesquisaAvulsaMl().map(item => ({
                    campo: item.campo,
                    termo: item.input.value || ''
                }))
            );
        }

        function formatarTermosPesquisaFavoritos(termos) {
            const normalizados = normalizarTermosPesquisaFavoritos(termos);
            return normalizados.length
                ? normalizados.map(item => `${item.campo}: ${item.termo}`).join(' | ')
                : '';
        }

        function preencherCamposPesquisaAvulsaMl(termos) {
            const normalizados = normalizarTermosPesquisaFavoritos(termos);
            const campos = obterCamposPesquisaAvulsaMl();
            campos.forEach(item => {
                item.input.value = '';
            });
            normalizados.forEach((termo, index) => {
                const alvo = campos.find(item => Number(item.campo) === Number(termo.campo)) || campos[index];
                if (alvo && alvo.input) alvo.input.value = termo.termo;
            });
        }

        function obterPrimeiroTermoPesquisaAvulsaMl() {
            const primeiro = obterTermosPesquisaAvulsaMl()[0];
            return primeiro ? primeiro.termo : '';
        }

        function formatarDataCriacao(valor) {
            if (!valor) return '';
            const texto = String(valor).trim();
            const br = texto.match(/^(\d{1,2})\/(\d{1,2})\/(\d{2,4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$/);
            if (br) {
                const ano = br[3].length === 2 ? `20${br[3]}` : br[3];
                return `${br[1].padStart(2, '0')}/${br[2].padStart(2, '0')}/${ano}${br[4] ? `, ${br[4].padStart(2, '0')}:${br[5]}:${br[6] || '00'}` : ''}`;
            }
            try {
                const data = new Date(valor);
                if (!Number.isNaN(data.getTime())) {
                    return data.toLocaleString('pt-BR');
                }
            } catch (e) {
                return texto;
            }
            return texto;
        }

        function parseDataCriacao(valor) {
            if (!valor) return null;
            if (valor instanceof Date) return Number.isNaN(valor.getTime()) ? null : valor;
            const texto = String(valor).trim();
            if (!texto) return null;

            const br = texto.match(/^(\d{1,2})\/(\d{1,2})\/(\d{2,4})(?:,?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$/);
            if (br) {
                const ano = Number(br[3].length === 2 ? `20${br[3]}` : br[3]);
                const mes = Number(br[2]) - 1;
                const dia = Number(br[1]);
                const hora = Number(br[4] || 0);
                const minuto = Number(br[5] || 0);
                const segundo = Number(br[6] || 0);
                const dataBr = new Date(ano, mes, dia, hora, minuto, segundo);
                return Number.isNaN(dataBr.getTime()) ? null : dataBr;
            }

            const isoBr = texto.match(/^(\d{4})-(\d{2})-(\d{2})(?:[T\s](\d{2}):(\d{2})(?::(\d{2}))?)?/);
            if (isoBr && !/[zZ]|[+-]\d{2}:?\d{2}$/.test(texto)) {
                const dataLocal = new Date(
                    Number(isoBr[1]),
                    Number(isoBr[2]) - 1,
                    Number(isoBr[3]),
                    Number(isoBr[4] || 0),
                    Number(isoBr[5] || 0),
                    Number(isoBr[6] || 0)
                );
                return Number.isNaN(dataLocal.getTime()) ? null : dataLocal;
            }

            const data = new Date(texto);
            return Number.isNaN(data.getTime()) ? null : data;
        }

        function formatarNumeroDecimal(valor, casas = 1) {
            if (!Number.isFinite(valor)) return '';
            return valor.toLocaleString('pt-BR', {
                minimumFractionDigits: casas,
                maximumFractionDigits: casas
            });
        }

        function parseNumeroDecimalFavoritos(valor) {
            if (valor === null || valor === undefined || valor === '') return null;
            if (typeof valor === 'number') return Number.isFinite(valor) ? valor : null;
            let texto = String(valor || '').replace(/\s+/g, '').trim();
            if (!texto) return null;
            const match = texto.match(/-?\d[\d.,]*/);
            if (!match) return null;
            texto = match[0];
            if (texto.includes('.') && texto.includes(',')) {
                texto = texto.lastIndexOf('.') > texto.lastIndexOf(',')
                    ? texto.replace(/,/g, '')
                    : texto.replace(/\./g, '').replace(',', '.');
            } else if (texto.includes(',')) {
                texto = texto.replace(/\./g, '').replace(',', '.');
            }
            const numero = Number(texto);
            return Number.isFinite(numero) ? numero : null;
        }

        function calcularMetricasMediaVendas(anuncio) {
            const vendas = parseVendasAvantPro(anuncio);
            const mediaDireta = parseNumeroDecimalFavoritos(
                anuncio && (
                    anuncio.media_vendas_mensal ??
                    anuncio.media_mensal ??
                    anuncio.ritmo_atual ??
                    anuncio.ritmo_vendas_mes ??
                    anuncio.media_vendas_mensal ??
                    ''
                )
            );
            const dataCriacao = parseDataCriacao(anuncio && anuncio.data_criacao);
            if (vendas === null || !dataCriacao) {
                return {
                    vendas,
                    meses: null,
                    media: Number.isFinite(mediaDireta) ? mediaDireta : null,
                    dataCriacao: dataCriacao || null
                };
            }

            const agora = new Date();
            const diffMs = agora.getTime() - dataCriacao.getTime();
            if (!Number.isFinite(diffMs) || diffMs < 0) {
                return {
                    vendas,
                    meses: null,
                    media: Number.isFinite(mediaDireta) ? mediaDireta : null,
                    dataCriacao
                };
            }

            const dias = Math.max(1, diffMs / 86400000);
            const meses = dias / 30.4375;
            const mediaCalculada = vendas / meses;
            const media = Number.isFinite(mediaCalculada)
                ? mediaCalculada
                : (Number.isFinite(mediaDireta) ? mediaDireta : null);
            return { vendas, meses, media, dataCriacao };
        }

        function formatarMediaVendas(anuncio) {
            const metrica = calcularMetricasMediaVendas(anuncio);
            if (!Number.isFinite(metrica.media)) return '';
            return formatarNumeroDecimal(metrica.media, metrica.media >= 10 ? 1 : 2);
        }

        function calcularDiasAnuncio(anuncio) {
            const dataCriacao = parseDataCriacao(anuncio && anuncio.data_criacao);
            if (!dataCriacao) return null;
            const diffMs = Date.now() - dataCriacao.getTime();
            if (!Number.isFinite(diffMs) || diffMs < 0) return null;
            return Math.max(0, Math.floor(diffMs / 86400000));
        }

        function formatarDiasAnuncio(anuncio) {
            const dias = calcularDiasAnuncio(anuncio);
            if (!Number.isFinite(dias)) return '';
            if (dias === 0) return 'Hoje';
            return `${dias} dia${dias === 1 ? '' : 's'}`;
        }

        function formatarMesesMedia(meses) {
            if (!Number.isFinite(meses)) return '';
            if (meses <= 1) return '1 mês';
            return `${formatarNumeroDecimal(meses, 1)} meses`;
        }

        function mostrarHintAbertura() {
            mlFrameHint.classList.remove('hidden');
        }

        function rankingSidebarEstaMinimizado() {
            try {
                return localStorage.getItem(ML_RANKING_SIDEBAR_COLLAPSED_KEY) === '1';
            } catch (_err) {
                return false;
            }
        }

        function aplicarEstadoRankingSidebar(minimizado) {
            if (!mlRankingMediaEl) return;
            mlRankingMediaEl.classList.toggle('is-collapsed', !!minimizado);
            document.body.classList.toggle('ml-ranking-sidebar-collapsed', !!minimizado);
            if (mlRankingToggleEl) {
                mlRankingToggleEl.textContent = minimizado ? '>' : '<';
                mlRankingToggleEl.title = minimizado ? 'Expandir ranking' : 'Minimizar ranking';
                mlRankingToggleEl.setAttribute('aria-label', minimizado ? 'Expandir ranking' : 'Minimizar ranking');
                mlRankingToggleEl.setAttribute('aria-expanded', minimizado ? 'false' : 'true');
            }
        }

        function alternarRankingSidebar() {
            const proximoEstado = !rankingSidebarEstaMinimizado();
            try {
                localStorage.setItem(ML_RANKING_SIDEBAR_COLLAPSED_KEY, proximoEstado ? '1' : '0');
            } catch (_err) {}
            aplicarEstadoRankingSidebar(proximoEstado);
            atualizarEstadoSidebarRanking();
        }

        function skuSidebarEstaMinimizado() {
            try {
                return localStorage.getItem(ML_SKU_SIDEBAR_COLLAPSED_KEY) === '1';
            } catch (_err) {
                return false;
            }
        }

        function aplicarEstadoSkuSidebar(minimizado = skuSidebarEstaMinimizado()) {
            document.querySelectorAll('.ml-sku-sidebar').forEach(sidebar => {
                sidebar.classList.toggle('is-hidden', !!minimizado);
            });
            document.body.classList.toggle('ml-sku-sidebar-collapsed', !!minimizado);
            mlSkuSidebarToggleEls.forEach(botao => {
                botao.textContent = minimizado ? '>' : '<';
                botao.title = minimizado ? 'Expandir lista de SKU' : 'Minimizar lista de SKU';
                botao.setAttribute('aria-label', minimizado ? 'Expandir lista de SKU' : 'Minimizar lista de SKU');
                botao.setAttribute('aria-expanded', minimizado ? 'false' : 'true');
            });
        }

        function alternarSkuSidebar() {
            const proximoEstado = !skuSidebarEstaMinimizado();
            try {
                localStorage.setItem(ML_SKU_SIDEBAR_COLLAPSED_KEY, proximoEstado ? '1' : '0');
            } catch (_err) {}
            aplicarEstadoSkuSidebar(proximoEstado);
            atualizarEstadoSidebarRanking();
        }

        function limitarLarguraSidebarRanking(valor) {
            const numero = Number(valor);
            if (!Number.isFinite(numero)) return 320;
            const maximo = Math.min(560, Math.max(300, window.innerWidth - 120));
            return Math.max(260, Math.min(maximo, numero));
        }

        function aplicarLarguraSidebarRanking(valor) {
            const largura = limitarLarguraSidebarRanking(valor);
            document.documentElement.style.setProperty('--ml-ranking-sidebar-width', `${largura}px`);
            return largura;
        }

        function carregarLarguraSidebarRanking() {
            try {
                const salva = Number(localStorage.getItem(ML_RANKING_SIDEBAR_WIDTH_KEY));
                if (Number.isFinite(salva) && salva > 0) return salva;
            } catch (_err) {}
            return 320;
        }

        function salvarLarguraSidebarRanking(valor) {
            try {
                localStorage.setItem(ML_RANKING_SIDEBAR_WIDTH_KEY, String(Math.round(limitarLarguraSidebarRanking(valor))));
            } catch (_err) {}
        }

        function limitarLarguraSkuSidebar(valor) {
            const numero = Number(valor);
            if (!Number.isFinite(numero)) return 300;
            const maximo = Math.min(520, Math.max(280, window.innerWidth - 120));
            return Math.max(240, Math.min(maximo, numero));
        }

        function aplicarLarguraSkuSidebar(valor) {
            const largura = limitarLarguraSkuSidebar(valor);
            document.documentElement.style.setProperty('--ml-sku-sidebar-width', `${largura}px`);
            return largura;
        }

        function carregarLarguraSkuSidebar() {
            try {
                const salva = Number(localStorage.getItem(ML_SKU_SIDEBAR_WIDTH_KEY));
                if (Number.isFinite(salva) && salva > 0) return salva;
            } catch (_err) {}
            return 300;
        }

        function salvarLarguraSkuSidebar(valor) {
            try {
                localStorage.setItem(ML_SKU_SIDEBAR_WIDTH_KEY, String(Math.round(limitarLarguraSkuSidebar(valor))));
            } catch (_err) {}
        }

        function iniciarAjusteLarguraSidebar(event) {
            if (!mlRankingMediaEl || rankingSidebarEstaMinimizado()) return;
            if (event.button !== undefined && event.button !== 0) return;
            event.preventDefault();
            document.body.classList.add('ml-sidebar-resizing');

            const mover = (moveEvent) => {
                const clientX = Number(moveEvent.clientX);
                if (!Number.isFinite(clientX)) return;
                const largura = aplicarLarguraSidebarRanking(window.innerWidth - clientX - 16);
                salvarLarguraSidebarRanking(largura);
            };

            const parar = () => {
                document.body.classList.remove('ml-sidebar-resizing');
                window.removeEventListener('pointermove', mover);
                window.removeEventListener('pointerup', parar);
                window.removeEventListener('pointercancel', parar);
            };

            window.addEventListener('pointermove', mover);
            window.addEventListener('pointerup', parar);
            window.addEventListener('pointercancel', parar);
        }

        function iniciarAjusteLarguraSkuSidebar(event) {
            if (!mlSkuSidebarSectionEl || skuSidebarEstaMinimizado()) return;
            if (event.button !== undefined && event.button !== 0) return;
            event.preventDefault();
            document.body.classList.add('ml-sidebar-resizing');

            const mover = (moveEvent) => {
                const clientX = Number(moveEvent.clientX);
                if (!Number.isFinite(clientX)) return;
                const largura = aplicarLarguraSkuSidebar(clientX - 16);
                salvarLarguraSkuSidebar(largura);
            };

            const parar = () => {
                document.body.classList.remove('ml-sidebar-resizing');
                window.removeEventListener('pointermove', mover);
                window.removeEventListener('pointerup', parar);
                window.removeEventListener('pointercancel', parar);
            };

            window.addEventListener('pointermove', mover);
            window.addEventListener('pointerup', parar);
            window.addEventListener('pointercancel', parar);
        }

        function atualizarEstadoSidebarRanking() {
            const abaNavegadorAtiva = document.getElementById('aba-navegador')?.classList.contains('active');
            const moduloFavoritosPronto = !document.body.classList.contains('favoritos-store-pending');
            const temAnuncios = Array.isArray(mlAnunciosPrimeiraPaginaAtuais) && mlAnunciosPrimeiraPaginaAtuais.length > 0;
            const navegadorEmBalao = balaoResultadosMlAberto();
            document.body.classList.toggle('ml-ranking-sidebar-active', !!(abaNavegadorAtiva && temAnuncios));
            document.body.classList.toggle('ml-sku-sidebar-active', !!(moduloFavoritosPronto && mlSkuSidebarSectionEl));
            if (mlShellBrowserProxy) {
                if (navegadorEmBalao && mlShellBrowserProxy.__visible) atualizarPosicaoNavegadorMlShell();
                else ocultarNavegadorMlShellDefinitivo();
            }
            aplicarEstadoRankingSidebar(rankingSidebarEstaMinimizado());
            aplicarEstadoSkuSidebar(skuSidebarEstaMinimizado());
        }

        function setBrowserStatus(message) {
            ocultarNavegadorMlShellDefinitivo();
            mlBrowserHost.innerHTML = `
                <div class="browser-warning">
                    <strong>${message}</strong>
                    <span>Você pode alterar a URL e clicar em "Abrir no Programa" novamente.</span>
                </div>
            `;
        }

        function aplicarScrollbarsDiscretasNoWebview(webview) {
            if (!webview || typeof webview.insertCSS !== 'function') return;
            try {
                const resultado = webview.insertCSS(ML_DISCREET_SCROLLBAR_CSS);
                if (resultado && typeof resultado.catch === 'function') {
                    resultado.catch(() => {});
                }
            } catch (_err) {}
        }

        function inicializarBalaoResultadosMl() {
            if (mlWorkModalInicializado) return;
            mlWorkModalInicializado = true;

            if (mlWorkModalBrowserSlotEl && mlBrowserFrameWrapEl) {
                mlWorkModalBrowserSlotEl.appendChild(mlBrowserFrameWrapEl);
            }

            if (mlWorkModalResultsSlotEl) {
                const painelPrimeiraPagina = mlPrimeiraPaginaStatusEl ? mlPrimeiraPaginaStatusEl.closest('.panel') : null;
                if (painelPrimeiraPagina) {
                    painelPrimeiraPagina.classList.add('ml-work-modal-panel');
                    mlWorkModalResultsSlotEl.appendChild(painelPrimeiraPagina);
                }
                if (mlFavoritosPanelEl) {
                    mlFavoritosPanelEl.classList.add('ml-work-modal-panel');
                    mlWorkModalResultsSlotEl.appendChild(mlFavoritosPanelEl);
                }
            }
        }

        function balaoResultadosMlAberto() {
            const abaNavegadorAtiva = document.getElementById('aba-navegador')?.classList.contains('active');
            return !!(abaNavegadorAtiva && mlWorkModalEl && !mlWorkModalEl.classList.contains('hidden'));
        }

        function navegadorMlEmSegundoPlano() {
            return !!(
                (mlFavoritosEmExecucao && mlFavoritosExecucaoEmSegundoPlano)
                || window.__JK_FAVORITOS_WORKER_BROWSER_ACTIVE === true
            );
        }

        function abrirBalaoResultadosMl(opcoes = {}) {
            inicializarBalaoResultadosMl();
            if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = opcoes.titulo || 'Resultados do Mercado Livre';
            if (mlWorkModalSubtitleEl) mlWorkModalSubtitleEl.textContent = opcoes.subtitulo || '';
            if (mlFavoritosPanelEl && opcoes.mostrarFavoritos) mlFavoritosPanelEl.classList.remove('hidden');
            if (mlFavoritosPanelEl && !opcoes.mostrarFavoritos && !mlFavoritosEmExecucao) mlFavoritosPanelEl.classList.add('hidden');
            const browserCompleto = !!(opcoes.browserCompleto || mlFavoritosEmExecucao);
            const manterSegundoPlano = navegadorMlEmSegundoPlano() && !opcoes.forcarExibicao;
            if (mlWorkModalEl) {
                mlWorkModalEl.dataset.browserOnly = browserCompleto ? '1' : '0';
                mlWorkModalEl.classList.toggle('is-browser-only', browserCompleto);
                if (manterSegundoPlano) {
                    mlWorkModalEl.classList.add('hidden');
                    document.body.classList.remove('ml-work-modal-open');
                } else {
                    mlWorkModalEl.classList.remove('hidden');
                    document.body.classList.add('ml-work-modal-open');
                }
            }
            atualizarFiltroAzulFavoritos();
            posicionarBalaoFavoritosStatus();
            if (!manterSegundoPlano) {
                setTimeout(() => {
                    atualizarPosicaoNavegadorMlShell();
                    agendarAtualizacaoPosicaoNavegadorMlShell();
                }, 80);
            }
        }

        function fecharBalaoResultadosMl(opcoes = {}) {
            const forcar = !!(opcoes && opcoes.forcar);
            const ocultandoExecucao = mlFavoritosEmExecucao && !forcar;
            if (ocultandoExecucao) mlFavoritosExecucaoEmSegundoPlano = true;
            if (mlWorkModalEl) {
                mlWorkModalEl.dataset.browserOnly = '0';
                mlWorkModalEl.classList.add('hidden');
                mlWorkModalEl.classList.remove('is-browser-only');
                mlWorkModalEl.classList.remove('is-favoritos-running');
            }
            if (mlBrowserFrameWrapEl) {
                mlBrowserFrameWrapEl.classList.remove('is-favoritos-running');
            }
            if (mlWorkModalCloseEl) {
                mlWorkModalCloseEl.disabled = false;
            }
            if (mlWorkModalCancelEl) {
                mlWorkModalCancelEl.classList.add('hidden');
                mlWorkModalCancelEl.disabled = true;
            }
            document.body.classList.remove('ml-work-modal-open');
            posicionarBalaoFavoritosStatus();
            ocultarNavegadorMlShellDefinitivo({
                descarregarConteudo: !!(opcoes && (opcoes.descarregarConteudo || opcoes.destroy || opcoes.unload)),
                reason: opcoes.reason || 'favoritos-modal-close',
                preserveAvantProSession: opcoes.preserveAvantProSession !== false
            });
            if (ocultandoExecucao) {
                window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('Favoritos continua rodando em segundo plano. Voce pode usar outras abas e modulos; use Cancelar favoritos no sidebar se precisar parar.', {
                    tempoMs: 6000,
                    larga: true
                });
            }
        }

        function garantirCamadaBalaoFavoritosStatus() {
            return window.FavoritosV2?.ui?.statusModal?.garantirCamadaBalaoFavoritosStatus?.() || null;
        }

        function posicionarBalaoFavoritosStatus() {
            return window.FavoritosV2?.ui?.statusModal?.posicionarBalaoFavoritosStatus?.();
        }

  const api = { obterCamposPesquisaAvulsaMl, normalizarTermosPesquisaFavoritos, obterTermosPesquisaAvulsaMl, formatarTermosPesquisaFavoritos, preencherCamposPesquisaAvulsaMl, obterPrimeiroTermoPesquisaAvulsaMl, formatarDataCriacao, parseDataCriacao, formatarNumeroDecimal, parseNumeroDecimalFavoritos, calcularMetricasMediaVendas, formatarMediaVendas, calcularDiasAnuncio, formatarDiasAnuncio, formatarMesesMedia, mostrarHintAbertura, rankingSidebarEstaMinimizado, aplicarEstadoRankingSidebar, alternarRankingSidebar, skuSidebarEstaMinimizado, aplicarEstadoSkuSidebar, alternarSkuSidebar, limitarLarguraSidebarRanking, aplicarLarguraSidebarRanking, carregarLarguraSidebarRanking, salvarLarguraSidebarRanking, limitarLarguraSkuSidebar, aplicarLarguraSkuSidebar, carregarLarguraSkuSidebar, salvarLarguraSkuSidebar, iniciarAjusteLarguraSidebar, iniciarAjusteLarguraSkuSidebar, atualizarEstadoSidebarRanking, setBrowserStatus, aplicarScrollbarsDiscretasNoWebview, inicializarBalaoResultadosMl, balaoResultadosMlAberto, navegadorMlEmSegundoPlano, abrirBalaoResultadosMl, fecharBalaoResultadosMl, garantirCamadaBalaoFavoritosStatus, posicionarBalaoFavoritosStatus };
  browser.browserUi = Object.freeze(api);
  Object.assign(global, api);
})(window);
