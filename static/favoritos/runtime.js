const userData = JSON.parse(localStorage.getItem('user_data') || 'null');
        const permissions = JSON.parse(localStorage.getItem('permissions') || '{}');
        const JK_BROWSER_SESSION_PARTITION = 'persist:jk-sistema-browser';
        let mlBrowserSessionPartition = JK_BROWSER_SESSION_PARTITION;

        if (!userData) {
            window.location.href = 'frontend_index.html';
        }
        if (!(permissions.full === true || permissions.favoritos)) {
            alert('Acesso não autorizado para Favoritos.');
            window.location.href = 'dashboard.html';
        }

        if (window.electronAPI && typeof window.electronAPI.getBrowserSessionPartition === 'function') {
            window.electronAPI.getBrowserSessionPartition()
                .then((partition) => {
                    if (partition) mlBrowserSessionPartition = String(partition);
                })
                .catch(() => {});
        }

        function obterParticaoNavegadorPersistente() {
            return mlBrowserSessionPartition || JK_BROWSER_SESSION_PARTITION;
        }

        let mlBrowserExtensionsReadyPromise = null;
        function garantirExtensoesNavegadorMl() {
            const api = window.electronAPI;
            if (!(api && typeof api.ensureBrowserExtensions === 'function')) {
                return Promise.resolve(null);
            }
            if (!mlBrowserExtensionsReadyPromise) {
                mlBrowserExtensionsReadyPromise = (async () => {
                    if (typeof api.getBrowserExtensionSettings === 'function' && typeof api.setAvantProExtensionEnabled === 'function') {
                        const estado = await api.getBrowserExtensionSettings().catch(() => null);
                        if (estado && estado.avantProEnabled === false) {
                            await api.setAvantProExtensionEnabled(true);
                        }
                    }
                    const resultado = await api.ensureBrowserExtensions();
                    const extensoes = Array.isArray(resultado && resultado.extensions) ? resultado.extensions : [];
                    const avantCarregado = extensoes.some((ext) => {
                        const texto = [ext && ext.id, ext && ext.name, ext && ext.path, ext && ext.url].filter(Boolean).join(' ');
                        return /jdefnfmbnchmnjkcknaadaddgjbgephh|avant\s*pro|avantpro/i.test(texto);
                    });
                    if (!avantCarregado) {
                        mlBrowserExtensionsReadyPromise = null;
                    }
                    return resultado;
                })()
                    .catch((err) => {
                        mlBrowserExtensionsReadyPromise = null;
                        console.warn('Falha ao preparar extensoes do navegador ML:', err && err.message ? err.message : err);
                        return null;
                    });
            }
            return mlBrowserExtensionsReadyPromise;
        }

        function salvarSessaoNavegadorElectron() {
            if (window.electronAPI && typeof window.electronAPI.flushBrowserSession === 'function') {
                window.electronAPI.flushBrowserSession().catch(() => {});
            }
        }

        garantirExtensoesNavegadorMl();

        window.addEventListener('beforeunload', salvarSessaoNavegadorElectron);

        const favoritosStoreGateEl = document.getElementById('favoritos-store-gate');
        const favoritosStoreGateStatusEl = document.getElementById('favoritos-store-gate-status');
        const favoritosHeaderStoreCardsEl = document.getElementById('favoritos-header-store-cards');
        const skuLojaCardsEl = document.getElementById('sku-loja-cards');
        const skuStatusEl = document.getElementById('sku-status');
        const skuIaProgressEl = document.getElementById('sku-ai-progress');
        const skuIaProgressTitleEl = document.getElementById('sku-ai-progress-title');
        const skuIaProgressPercentEl = document.getElementById('sku-ai-progress-percent');
        const skuIaProgressFillEl = document.getElementById('sku-ai-progress-fill');
        const skuIaProgressDetailEl = document.getElementById('sku-ai-progress-detail');
        const skuFiltroEl = document.getElementById('sku-filtro');
        const skuContadorEl = document.getElementById('sku-contador');
        const skuEmptyEl = document.getElementById('sku-empty');
        const skuTableWrapEl = document.getElementById('sku-table-wrap');
        const skuBodyEl = document.getElementById('sku-body');
        const skuPaginationEl = document.getElementById('sku-pagination');
        const btnSkuToggleOcultos = document.getElementById('btn-sku-toggle-ocultos');
        const btnSkuIaTodos = document.getElementById('btn-sku-ia-todos');
        const skuPesquisaSaveTimers = new WeakMap();

        const btnBuscar = document.getElementById('btn-buscar');
        const termoEl = document.getElementById('termo');
        const statusEl = document.getElementById('status');
        const resultadosEl = document.getElementById('resultados');

        const linkProdutoWrap = document.getElementById('link-produto-wrap');
        const linkProdutoInfo = document.getElementById('link-produto-info');
        const linkProdutoUrls = document.getElementById('link-produto-urls');

        const buscaTermoWrap = document.getElementById('busca-termo-wrap');
        const topBody = document.getElementById('top-body');

        function skuDefinirStatus(texto) {
            if (!skuStatusEl) return;
            skuStatusEl.textContent = texto || '';
            skuStatusEl.classList.toggle('hidden', !texto);
        }

        function skuFormatarTempoIa(ms) {
            const totalSegundos = Math.max(0, Math.floor(Number(ms || 0) / 1000));
            const minutos = Math.floor(totalSegundos / 60);
            const segundos = totalSegundos % 60;
            return minutos ? `${minutos}min ${String(segundos).padStart(2, '0')}s` : `${segundos}s`;
        }

        function skuPararRelogioIa() {
            if (skuIaProgressTimer) {
                clearInterval(skuIaProgressTimer);
                skuIaProgressTimer = null;
            }
        }

        function skuIniciarRelogioIa() {
            if (skuIaProgressTimer) return;
            skuIaProgressTimer = setInterval(() => {
                if (!skuIaProgressState || !skuIaProgressState.ativo) {
                    skuPararRelogioIa();
                    return;
                }
                skuAtualizarBarraIa();
            }, 1000);
        }

        function skuAtualizarBarraIa(opcoes = {}) {
            if (!skuIaProgressEl) return;
            const anterior = skuIaProgressState || {};
            const ativo = Object.prototype.hasOwnProperty.call(opcoes, 'ativo') ? !!opcoes.ativo : !!anterior.ativo;
            const total = Number.isFinite(Number(opcoes.total)) ? Math.max(0, Number(opcoes.total)) : Math.max(0, Number(anterior.total || 0));
            const processados = Number.isFinite(Number(opcoes.processados)) ? Math.max(0, Number(opcoes.processados)) : Math.max(0, Number(anterior.processados || 0));
            const startedAt = opcoes.startedAt || anterior.startedAt || (ativo ? Date.now() : 0);
            const erros = Number.isFinite(Number(opcoes.erros)) ? Math.max(0, Number(opcoes.erros)) : Math.max(0, Number(anterior.erros || 0));
            const phase = opcoes.phase || (opcoes.erro ? 'error' : (ativo ? 'running' : (erros ? 'error' : 'complete')));
            const mensagem = String(Object.prototype.hasOwnProperty.call(opcoes, 'message') ? opcoes.message : (anterior.mensagem || '')).trim();
            const pct = total > 0
                ? Math.max(0, Math.min(100, Math.round((Math.min(processados, total) / total) * 100)))
                : (ativo ? 5 : 0);

            skuIaProgressState = { ativo, total, processados, startedAt, mensagem, erros, phase };
            skuIaProgressEl.classList.toggle('hidden', !ativo && !mensagem);
            skuIaProgressEl.classList.toggle('is-running', ativo);
            skuIaProgressEl.classList.toggle('is-complete', !ativo && phase === 'complete');
            skuIaProgressEl.classList.toggle('is-error', phase === 'error');
            if (skuIaProgressTitleEl) {
                skuIaProgressTitleEl.textContent = phase === 'error'
                    ? 'IA precisa de atencao'
                    : (phase === 'complete' ? 'IA concluiu os campos de pesquisa' : 'IA preenchendo campos de pesquisa');
            }
            if (skuIaProgressPercentEl) {
                skuIaProgressPercentEl.textContent = total ? `${pct}%` : (ativo ? 'processando' : '0%');
            }
            if (skuIaProgressFillEl) {
                skuIaProgressFillEl.style.width = `${pct}%`;
            }
            if (skuIaProgressDetailEl) {
                const partes = [];
                if (mensagem) partes.push(mensagem);
                if (total) partes.push(`${Math.min(processados, total)}/${total} SKU(s) processado(s)`);
                if (opcoes.loteInicio && opcoes.loteFim) partes.push(`Lote atual: ${opcoes.loteInicio}-${opcoes.loteFim}`);
                if (erros) partes.push(`${erros} falha(s)`);
                if (ativo && startedAt) partes.push(`Tempo: ${skuFormatarTempoIa(Date.now() - startedAt)}`);
                skuIaProgressDetailEl.textContent = partes.join(' | ');
            }
            if (ativo) {
                skuIniciarRelogioIa();
            } else {
                skuPararRelogioIa();
            }
        }

        function skuPausarUi(ms = 0) {
            return new Promise(resolve => setTimeout(resolve, ms));
        }
        const allBody = document.getElementById('all-body');
        const topEmpty = document.getElementById('top-empty');
        const allEmpty = document.getElementById('all-empty');
        const topWrap = document.getElementById('top-table-wrap');
        const allWrap = document.getElementById('all-table-wrap');

        const mlSearchTermInput = document.getElementById('ml-search-term');
        const mlSearchTerm2Input = document.getElementById('ml-search-term-2');
        const mlSearchTerm3Input = document.getElementById('ml-search-term-3');
        const mlUrlInput = document.getElementById('ml-url');
        const mlBrowserHost = document.getElementById('ml-browser-host');
        const mlFrameHint = document.getElementById('ml-frame-hint');
        const mlPrimeiraPaginaStatusEl = document.getElementById('ml-primeira-pagina-status');
        const mlPrimeiraPaginaEmptyEl = document.getElementById('ml-primeira-pagina-empty');
        const mlPrimeiraPaginaLayoutEl = document.getElementById('ml-primeira-pagina-layout');
        const mlPrimeiraPaginaTableWrapEl = document.getElementById('ml-primeira-pagina-table-wrap');
        const mlPrimeiraPaginaBodyEl = document.getElementById('ml-primeira-pagina-body');
        const mlSkuLojaCardsEl = document.getElementById('ml-sku-loja-cards');
        const mlSkuStatusEl = document.getElementById('ml-sku-status');
        const mlRankingMediaListEl = document.getElementById('ml-ranking-media-list');
        const mlRankingMediaEmptyEl = document.getElementById('ml-ranking-media-empty');
        const mlRankingMediaCountEl = document.getElementById('ml-ranking-media-count');
        const mlRankingMediaEl = document.getElementById('ml-ranking-media');
        const mlRankingToggleEl = document.getElementById('ml-ranking-toggle');
        const mlSkuSidebarSectionEl = document.getElementById('ml-sku-sidebar-section');
        const mlSkuSidebarListEl = document.getElementById('ml-sku-sidebar-list');
        const mlSkuSidebarEmptyEl = document.getElementById('ml-sku-sidebar-empty');
        const mlSkuSidebarCountEl = document.getElementById('ml-sku-sidebar-count');
        const mlSkuSidebarSearchEl = document.getElementById('ml-sku-sidebar-search');
        const mlSkuAtualizarEl = document.getElementById('ml-sku-atualizar');
        const mlSkuSelectAllEl = document.getElementById('ml-sku-select-all');
        const mlSkuFazerFavoritosEl = document.getElementById('ml-sku-fazer-favoritos');
        const mlSkuUsarIaFavoritosEl = document.getElementById('ml-sku-usar-ia-favoritos');
        const mlSkuPausarFavoritosEl = document.getElementById('ml-sku-pausar-favoritos');
        const mlSkuRetomarFavoritosEl = document.getElementById('ml-sku-retomar-favoritos');
        const mlSkuCancelarFavoritosEl = document.getElementById('ml-sku-cancelar-favoritos');
        const mlSkuSelectedCountEl = document.getElementById('ml-sku-selected-count');
        const mlSkuSidebarToggleEl = document.getElementById('ml-sku-sidebar-toggle');
        const mlSkuSidebarToggleEls = Array.from(document.querySelectorAll('[data-ml-sku-sidebar-toggle]'));
        const mlSidebarResizerEl = document.getElementById('ml-sidebar-resizer');
        const mlSkuSidebarResizerEl = document.getElementById('ml-sku-sidebar-resizer');
        const mlIgnoredSellersListEl = document.getElementById('ml-ignored-sellers-list');
        const mlIgnoredSellersEmptyEl = document.getElementById('ml-ignored-sellers-empty');
        const favMlSkuSidebarListEl = document.getElementById('fav-ml-sku-sidebar-list');
        const favMlSkuSidebarEmptyEl = document.getElementById('fav-ml-sku-sidebar-empty');
        const favMlSkuSidebarCountEl = document.getElementById('fav-ml-sku-sidebar-count');
        const favMlSkuSidebarSearchEl = document.getElementById('fav-ml-sku-sidebar-search');
        const favMlSkuAtualizarEl = document.getElementById('fav-ml-sku-atualizar');
        const favMlStatusEl = document.getElementById('fav-ml-status');
        const favMlAnunciosBodyEl = document.getElementById('fav-ml-anuncios-body');
        const favMlAnunciosEmptyEl = document.getElementById('fav-ml-anuncios-empty');
        const favMlAnunciosRecarregarBtnEl = document.getElementById('fav-ml-anuncios-recarregar');
        const favOutrosAnunciosBodyEl = document.getElementById('fav-outros-anuncios-body');
        const favOutrosAnunciosEmptyEl = document.getElementById('fav-outros-anuncios-empty');
        const favRankingDataEl = document.getElementById('fav-ranking-data');
        const favRankingIncluirAnuncioBtnEl = document.getElementById('fav-ranking-incluir-anuncio');
        const favRankingIncluirFormEl = document.getElementById('fav-ranking-incluir-form');
        const favRankingIncluirInputEl = document.getElementById('fav-ranking-incluir-input');
        const favRankingIncluirConfirmarBtnEl = document.getElementById('fav-ranking-incluir-confirmar');
        const favRankingIncluirCancelarBtnEl = document.getElementById('fav-ranking-incluir-cancelar');
        const favMlAnunciosTableEl = document.getElementById('fav-ml-anuncios-table');
        const favOutrosAnunciosTableEl = document.getElementById('fav-outros-anuncios-table');
        const favMlEfetivarPanelEl = document.getElementById('fav-ml-efetivar-panel');
        const favMlEfetivarInfoEl = document.getElementById('fav-ml-efetivar-info');
        const favMlEfetivarBtnEl = document.getElementById('fav-ml-efetivar-btn');
        const favMlPromocaoBtnEl = document.getElementById('fav-ml-promocao-btn');
        const favMlEfetivarOutrasContasEl = document.getElementById('fav-ml-efetivar-outras-contas');
        const favMlEfetivarLogEl = document.getElementById('fav-ml-efetivar-log');
        const favMlTablesLayoutEl = document.querySelector('.favoritos-ml-tables');
        let favoritosSyncLinhasRaf = 0;
        let favMlEfetivarLogHideTimer = null;
        const histMlSkuSidebarListEl = document.getElementById('hist-ml-sku-sidebar-list');
        const histMlSkuSidebarEmptyEl = document.getElementById('hist-ml-sku-sidebar-empty');
        const histMlSkuSidebarCountEl = document.getElementById('hist-ml-sku-sidebar-count');
        const histMlSkuSidebarSearchEl = document.getElementById('hist-ml-sku-sidebar-search');
        const histMlSkuAtualizarEl = document.getElementById('hist-ml-sku-atualizar');
        const mlFavoritosPanelEl = document.getElementById('ml-favoritos-panel');
        const mlFavoritosPanelTitleEl = document.getElementById('ml-favoritos-panel-title');
        const mlFavoritosStatusEl = document.getElementById('ml-favoritos-status');
        const mlFavoritosEmptyEl = document.getElementById('ml-favoritos-empty');
        const mlFavoritosListEl = document.getElementById('ml-favoritos-list');
        const mlFavoritosBalloonEl = document.getElementById('ml-favoritos-status-balloon');
        const mlFavoritosBalloonTextEl = document.getElementById('ml-favoritos-status-balloon-text');
        const mlFavoritosBalloonActionsEl = document.getElementById('ml-favoritos-status-balloon-actions');
        const mlFavoritosBalloonOriginalParentEl = mlFavoritosBalloonEl ? mlFavoritosBalloonEl.parentElement : null;
        let mlFavoritosBalloonLayerEl = null;
        const mlHistoricoFavoritosStatusEl = document.getElementById('ml-historico-favoritos-status');
        const mlHistoricoFavoritosEmptyEl = document.getElementById('ml-historico-favoritos-empty');
        const mlHistoricoFavoritosListEl = document.getElementById('ml-historico-favoritos-list');
        const mlHistoricoFavoritosLimparEl = document.getElementById('ml-historico-favoritos-limpar');
        const mlLinksAlinhadosStatusEl = document.getElementById('ml-links-alinhados-status');
        const mlLinksAlinhadosEmptyEl = document.getElementById('ml-links-alinhados-empty');
        const mlLinksAlinhadosBodyEl = document.getElementById('ml-links-alinhados-body');
        const mlLinksAlinhadosAtualizarEl = document.getElementById('ml-links-alinhados-atualizar');
        const mlVendedoresIgnoradosStatusEl = document.getElementById('ml-vendedores-ignorados-status');
        const mlVendedoresIgnoradosEmptyEl = document.getElementById('ml-vendedores-ignorados-empty');
        const mlVendedoresIgnoradosListEl = document.getElementById('ml-vendedores-ignorados-list');
        const mlAnunciosIgnoradosStatusEl = document.getElementById('ml-anuncios-ignorados-status');
        const mlAnunciosIgnoradosEmptyEl = document.getElementById('ml-anuncios-ignorados-empty');
        const mlAnunciosIgnoradosListEl = document.getElementById('ml-anuncios-ignorados-list');
        const favoritosPlanilhasStatusEl = document.getElementById('favoritos-planilhas-status');
        const favoritosPlanilhasUpdatedEl = document.getElementById('favoritos-planilhas-updated');
        const favoritosPlanilhaLojaAtualNomeEl = document.getElementById('favoritos-planilha-loja-atual-nome');
        const favoritosPlanilhaLojaAtualUrlEl = document.getElementById('favoritos-planilha-loja-atual-url');
        const favoritosPlanilhaLojaAtualSalvarEl = document.getElementById('favoritos-planilha-loja-atual-salvar');
        const favoritosPlanilhaLojaAtualAbrirEl = document.getElementById('favoritos-planilha-loja-atual-abrir');
        const favoritosPlanilhasListEl = document.getElementById('favoritos-planilhas-list');
        const mlBrowserBlueFilterEl = document.getElementById('ml-browser-blue-filter');
        const mlBrowserFrameWrapEl = mlBrowserBlueFilterEl ? mlBrowserBlueFilterEl.closest('.browser-frame-wrap') : null;
        const mlWorkModalEl = document.getElementById('ml-work-modal');
        const mlWorkModalDialogEl = mlWorkModalEl ? mlWorkModalEl.querySelector('.ml-work-modal-dialog') : null;
        const mlWorkModalTitleEl = document.getElementById('ml-work-modal-title');
        const mlWorkModalSubtitleEl = document.getElementById('ml-work-modal-subtitle');
        const mlWorkModalLiveStatusEl = document.getElementById('ml-work-modal-live-status');
        const mlWorkModalCloseEl = document.getElementById('ml-work-modal-close');
        const mlWorkModalPauseEl = document.getElementById('ml-work-modal-pause');
        const mlWorkModalResumeEl = document.getElementById('ml-work-modal-resume');
        const mlWorkModalCancelEl = document.getElementById('ml-work-modal-cancel');
        const mlWorkModalBrowserSlotEl = document.getElementById('ml-work-modal-browser-slot');
        const mlWorkModalResultsSlotEl = document.getElementById('ml-work-modal-results-slot');
        const ML_DEFAULT_URL = 'https://www.mercadolivre.com.br/';
        const ML_VENDEDORES_IGNORADOS_RANKING_KEY = 'favoritosMlVendedoresIgnoradosRanking';
        const ML_ANUNCIOS_IGNORADOS_SKU_KEY = 'favoritosMlAnunciosIgnoradosPorSku';
        const ML_FAVORITOS_USAR_IA_KEY = 'favoritosMlUsarIaRanking';
        const ML_RANKING_SIDEBAR_COLLAPSED_KEY = 'favoritosMlRankingSidebarCollapsed';
        const ML_RANKING_SIDEBAR_WIDTH_KEY = 'favoritosMlRankingSidebarWidth';
        const ML_SKU_SIDEBAR_COLLAPSED_KEY = 'favoritosMlSkuSidebarCollapsed';
        const ML_SKU_SIDEBAR_WIDTH_KEY = 'favoritosMlSkuSidebarWidth';
        const ML_SKU_LOJA_KEY = 'favoritosMlSkuLojaSelecionada';
        const ML_FAVORITOS_SELECTION_CHANNEL = 'jkFavoritosMlSelecao';
        const ML_FAVORITOS_SELECTION_STORAGE_KEY = 'favoritosMlSelecaoEvento';
        const ML_FAVORITOS_HISTORICO_SYNC_CHANNEL = 'jkFavoritosMlHistoricoSync';
        const ML_FAVORITOS_HISTORICO_SYNC_STORAGE_KEY = 'favoritosMlHistoricoSyncEvento';
        const ML_FAVORITOS_TABLE_LAYOUT_KEY = 'favoritosMlTableLayoutV1';
        const ML_SKU_SIDEBAR_PAGE_SIZE = 50;
        const SKU_API_PAGE_SIZE = 20;
        const ML_FAVORITOS_HISTORICO_MAX = 500;
        const ML_FAVORITOS_COLETA_ANUNCIOS_MAX = 80;
        const ML_FAVORITOS_RANKING_ANUNCIOS_MAX = 80;
        const ML_FAVORITOS_HISTORICO_ANUNCIOS_MAX = ML_FAVORITOS_RANKING_ANUNCIOS_MAX;
        const AVANT_PRO_LOGIN_EMAIL_KEY = 'favoritosAvantProLoginEmail';
        const AVANT_PRO_LOGIN_EMAIL_PADRAO = 'fredyn-lefer@hotmail.com';
        const AVANT_PRO_LOGIN_EMAIL = String(
            localStorage.getItem(AVANT_PRO_LOGIN_EMAIL_KEY)
            || AVANT_PRO_LOGIN_EMAIL_PADRAO
            || (userData && (userData.email || userData.e_mail || userData.mail || userData.login || userData.username))
            || localStorage.getItem('email')
            || ''
        ).trim();
        try {
            if (AVANT_PRO_LOGIN_EMAIL && !localStorage.getItem(AVANT_PRO_LOGIN_EMAIL_KEY)) {
                localStorage.setItem(AVANT_PRO_LOGIN_EMAIL_KEY, AVANT_PRO_LOGIN_EMAIL);
            }
        } catch (_err) {}
        let avantProLoginAutorizadoAte = 0;
        function autorizarLoginAvantProTemporario(ms = 180000) {
            avantProLoginAutorizadoAte = Date.now() + Math.max(30000, Number(ms) || 180000);
            return avantProLoginAutorizadoAte;
        }
        function loginAvantProAutorizado() {
            return Date.now() <= avantProLoginAutorizadoAte;
        }
        const AVANT_PRO_ESPERA_POS_CLIQUE_MS = 2800;
        const AVANT_PRO_ESTABILIDADE_MIN_MS = 900;
        const AVANT_PRO_ESTABILIDADE_MS = 800;
        const AVANT_PRO_ESTABILIDADE_MAX_MS = 3600;
        const hasInternalBrowserApi = !!(window.electronAPI && typeof window.electronAPI.openInternalBrowser === 'function');
        const hasExtractMlApi = !!(window.electronAPI && typeof window.electronAPI.extractMlSearchResults === 'function');
        const ML_DISCREET_SCROLLBAR_CSS = `
            html, body, * {
                scrollbar-width: thin !important;
                scrollbar-color: rgba(100, 116, 139, 0.14) transparent !important;
            }
            *::-webkit-scrollbar {
                width: 4px !important;
                height: 4px !important;
            }
            *::-webkit-scrollbar-track {
                background: transparent !important;
            }
            *::-webkit-scrollbar-thumb {
                background-color: rgba(100, 116, 139, 0.14) !important;
                background-clip: content-box !important;
                border: 1px solid transparent !important;
                border-radius: 999px !important;
            }
            *::-webkit-scrollbar-thumb:hover {
                background-color: rgba(100, 116, 139, 0.28) !important;
            }
            *::-webkit-scrollbar-button {
                display: none !important;
                height: 0 !important;
                width: 0 !important;
            }
            *::-webkit-scrollbar-corner {
                background: transparent !important;
            }
        `;
        let mlOpenedOnce = false;
        let mlNavegadorAutoOpenTimer = null;
        let mlWebviewEl = null;
        let mlShellBrowserProxy = null;
        let mlShellBrowserRequestId = 0;
        const mlShellBrowserPending = new Map();
        let mlShellBrowserPositionTimer = null;
        let mlShellBrowserLastShow = null;
        let mlBrowserShellOcultoPorBalao = false;
        let mlWorkModalInicializado = false;
        let mlAnunciosPrimeiraPaginaAtuais = [];
        let mlSkuLojasDisponiveis = [];
        let mlSkuLojaSelecionada = '';
        let mlSkusAnunciosLojaAtual = [];
        let favoritosLojaEntradaSelecionada = false;
        let mlSkuSidebarSelecionados = new Set();
        let mlSkuSidebarFiltro = '';
        let mlFavoritosSelectionBroadcast = null;
        let mlSkuSidebarRenderLimit = ML_SKU_SIDEBAR_PAGE_SIZE;
        let mlSkuBuscaRemotaTimer = null;
        let mlSkuBuscaRemotaRunId = 0;
        const mlSkuBuscaRemotaCache = new Set();
        const mlSkuDadosPorLojaCache = new Map();
        const mlSkuCarregamentoPromises = new Map();
        const ML_SKU_CACHE_REFRESH_MS = 10 * 60 * 1000;
        let favoritosMlSkuCacheMetaAtual = null;
        let mlSkuCarregamentoRunId = 0;
        let mlSkuCarregamentoEmAndamento = false;
        let mlSkuCarregamentoChave = '';
        let mlSkuCarregamentoLoja = '';
        const mlSkuEstadoPorLojaCache = new Map();
        let mlFavoritosEmExecucao = false;
        let mlFavoritosExecucaoEmSegundoPlano = false;
        let mlFavoritosCancelado = false;
        let mlFavoritosPausado = false;
        let mlFavoritosAbortController = null;
        let mlFavoritosJobIdAtual = '';
        let mlFavoritosJobPollTimer = null;
        let mlFavoritosJobPollAtivo = false;
        let mlFavoritosJobUltimoStatus = null;
        let mlFavoritosJobRenderRaf = 0;
        let mlFavoritosJobRenderPendente = false;
        let mlFavoritosJobGruposRender = [];
        let mlFavoritosJobUltimaQtdRender = -1;
        let mlFavoritosJobSelecionadosAtual = [];
        let mlFavoritosJobFinalTratado = false;
        let mlFavoritosResultadosPorSku = new Map();
        let mlFavoritosOpcoesPromocaoPorSku = new Map();
        let mlFavoritosOpcoesPromocaoAtual = null;
        let mlFavoritosTiposRankingEmExecucao = new Set();
        let mlFavoritosBalloonTimer = null;
        let mlFavoritosPerguntaResolver = null;
        let mlHistoricoFavoritosCache = [];
        let mlHistoricoFavoritosServidorCarregado = false;
        let mlHistoricoFavoritosSaveTimer = null;
        let mlHistoricoFavoritosSyncBroadcast = null;
        let mlHistoricoFavoritosSyncVinculosTimer = null;
        let mlHistoricoFavoritosSyncUltimoTs = 0;
        const FAV_ML_RANKING_ATUAL_ID = '__ranking_atual__';
        let favMlSkuSelecionado = '';
        let favMlLojaSelecionada = '';
        let favMlHistoricoExecucaoSelecionadaId = '';
        let histMlSkuSelecionado = '';
        let histMlHistoricoExecucaoSelecionadaId = '';
        let favMlAnunciosSkuAtual = [];
        let favMlSimulacoesSkuAtual = [];
        let favMlAnunciosNaoAlterarPorSku = new Map();
        let favMlEfetivacaoEmExecucao = false;
        const favMlPromocoesPorLojaCache = new Map();
        let mlRankingMediaFrame = null;
        let mlVendedoresIgnoradosRanking = null;
        let mlVendedoresIgnoradosServidorCarregado = false;
        let mlVendedoresIgnoradosSaveTimer = null;
        let mlAnunciosIgnoradosSku = null;
        let mlAnunciosIgnoradosServidorCarregado = false;
        let mlAnunciosIgnoradosSaveTimer = null;
        let favoritosPlanilhasLojas = {};
        let favoritosPlanilhasServidorCarregado = false;
        let favoritosPlanilhasCarregando = false;
        const mlAnunciosIgnoradosSkuExpandidos = new Set();
        let mlFavoritosLojasCompartilhadasPorSku = new Map();
        const mlFavoritosAnunciosPropriosIaCache = new Map();
        let favoritosTableLayout = {};
        let mlAvantAutoTimer = null;
        let mlAvantAutoRunId = 0;
        let mlAvantScrollExecutada = false;
        let skuLojasDisponiveis = [];
        let skuDados = [];
        let skuLojaSelecionada = '';
        let favoritosSkusTodasLojasCarregados = false;
        let favoritosTotalAnunciosLojaAtual = 0;
        let favoritosWarningLojaAtual = '';
        let skuSkusOcultos = new Set();
        let skuMostrarOcultos = false;
        let skuPaginaAtual = 1;
        let skuDescricaoAutoRunId = 0;
        let skuDescricaoAutoTimer = null;
        let skuIaProgressTimer = null;
        let skuIaProgressState = {
            ativo: false,
            total: 0,
            processados: 0,
            startedAt: 0,
            mensagem: '',
            erros: 0,
            phase: ''
        };
        const FAVORITOS_TODAS_LOJAS = '__todas';
        const FAVORITOS_TODAS_LOJAS_LABEL = 'Todas as lojas';

        function headersJsonAutenticado() {
            const base = (typeof obterAuthHeaders === 'function') ? obterAuthHeaders() : {};
            return { ...base, 'Content-Type': 'application/json' };
        }

        function fetchFavoritosComTimeout(url, options = {}, timeoutMs = 15000) {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
            return fetch(url, { ...options, signal: controller.signal })
                .catch(err => {
                    if (err && err.name === 'AbortError') {
                        throw new Error('Tempo limite atingido ao carregar dados do modulo.');
                    }
                    throw err;
                })
                .finally(() => clearTimeout(timeoutId));
        }

        function favoritosFormatarDataCacheMl(valor) {
            const texto = String(valor || '').trim();
            if (!texto) return '';
            const data = new Date(texto);
            if (Number.isNaN(data.getTime())) return '';
            return data.toLocaleString('pt-BR', {
                day: '2-digit',
                month: '2-digit',
                hour: '2-digit',
                minute: '2-digit'
            });
        }

        function favoritosCacheMlMeta(data) {
            const meta = data && typeof data === 'object' && data.cache ? data.cache : {};
            const updatedAt = String((meta && meta.updated_at) || (data && data.cache_updated_at) || '').trim();
            const dataTexto = favoritosFormatarDataCacheMl(updatedAt);
            return {
                hit: !!((meta && meta.hit) || (data && data.cache_hit)),
                refreshed: !!((meta && meta.refreshed) || (data && data.cache_refreshed)),
                updatedAt,
                dataTexto
            };
        }

        function favoritosMensagemCacheMl(data) {
            const meta = favoritosCacheMlMeta(data);
            favoritosMlSkuCacheMetaAtual = meta.updatedAt ? meta : favoritosMlSkuCacheMetaAtual;
            if (meta.refreshed) return meta.dataTexto ? `Atualizado agora (${meta.dataTexto}).` : 'Atualizado agora.';
            if (meta.hit) return meta.dataTexto ? `Dados salvos (${meta.dataTexto}).` : 'Dados salvos.';
            return '';
        }

        function favoritosComporStatusCacheMl(base, data) {
            const sufixo = favoritosMensagemCacheMl(data);
            return [base, sufixo].filter(Boolean).join(' ');
        }

        function favoritosBotoesAtualizarSkuMl() {
            return [mlSkuAtualizarEl, favMlSkuAtualizarEl, histMlSkuAtualizarEl].filter(Boolean);
        }

        function favoritosDefinirBotoesAtualizarSkuMl(carregando) {
            favoritosBotoesAtualizarSkuMl().forEach(botao => {
                botao.disabled = !!carregando;
                botao.textContent = carregando ? 'Atualizando...' : 'Atualizar agora';
            });
        }

        function textoLimpoHistoricoFavoritos(valor, limite = 160) {
            const texto = String(valor || '').replace(/\s+/g, ' ').trim();
            return texto ? texto.slice(0, limite) : '';
        }

        function nomeUsuarioHistoricoFavoritosAtual() {
            const fontes = [
                userData && (userData.nome || userData.name || userData.displayName || userData.display_name),
                userData && userData.username,
                userData && userData.email
            ];
            for (const fonte of fontes) {
                const texto = textoLimpoHistoricoFavoritos(fonte, 160);
                if (texto) return texto;
            }
            return '';
        }

        function usernameHistoricoFavoritosAtual() {
            return textoLimpoHistoricoFavoritos(
                userData && (userData.username || userData.email || userData.nome || userData.name),
                160
            );
        }

        function obterUsuarioHistoricoFavoritos(entrada) {
            if (!entrada) return '';
            return textoLimpoHistoricoFavoritos(
                entrada.nome_usuario
                || entrada.usuario_nome
                || entrada.usuario
                || entrada.created_by_name
                || entrada.criado_por_nome
                || entrada.username
                || entrada.created_by
                || entrada.criado_por,
                160
            );
        }

        function sufixoUsuarioHistoricoFavoritos(entrada) {
            const usuario = obterUsuarioHistoricoFavoritos(entrada);
            return usuario ? ` | Usuario: ${usuario}` : '';
        }

        async function copiarTextoAreaFallback(texto) {
            const textarea = document.createElement('textarea');
            textarea.value = texto;
            textarea.setAttribute('readonly', '');
            textarea.style.position = 'fixed';
            textarea.style.left = '-9999px';
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand('copy');
            textarea.remove();
        }

        async function copiarLinkAnuncio(url, botao) {
            if (!url) return;
            const textoOriginal = botao ? botao.textContent : '';
            try {
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    await navigator.clipboard.writeText(url);
                } else {
                    await copiarTextoAreaFallback(url);
                }
                if (botao) {
                    botao.textContent = 'Copiado';
                    setTimeout(() => { botao.textContent = textoOriginal || 'Copiar'; }, 1400);
                }
            } catch (err) {
                alert('Não foi possível copiar o link.');
            }
        }

        async function abrirAnuncioComAvantPro(url, event) {
            if (!url) return;
            if (event && typeof event.preventDefault === 'function') event.preventDefault();
            if (window.electronAPI && typeof window.electronAPI.openInternalBrowser === 'function') {
                try {
                    await window.electronAPI.openInternalBrowser(url);
                    return;
                } catch (err) {
                    console.warn('Falha ao abrir anúncio no navegador interno com Avant Pro:', err);
                }
            }
            window.open(url, '_blank', 'noopener');
        }

        async function abrirAnuncioNoChromeExterno(url, event) {
            if (!url) return;
            if (event && typeof event.preventDefault === 'function') event.preventDefault();
            if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
            if (window.electronAPI && typeof window.electronAPI.openExternalChrome === 'function') {
                try {
                    await window.electronAPI.openExternalChrome(url);
                    return;
                } catch (err) {
                    console.warn('Falha ao abrir anúncio no Google Chrome:', err);
                }
            }
            if (navigator.userAgent && /Electron/i.test(navigator.userAgent)) {
                alert('Nao foi possivel abrir no Google Chrome. Reinicie o aplicativo para carregar a abertura externa.');
                return;
            }
            window.open(url, '_blank', 'noopener');
        }

        function skuNormalizarLoja(valor) {
            return String(valor || '')
                .normalize('NFD')
                .replace(/[\u0300-\u036f]/g, '')
                .replace(/\s+/g, ' ')
                .trim()
                .toLowerCase();
        }

        function favoritosEhTodasLojas(valor) {
            const bruto = String(valor || '').trim();
            if (!bruto) return false;
            if (bruto === FAVORITOS_TODAS_LOJAS) return true;
            const norm = skuNormalizarLoja(bruto);
            return norm === 'todas' || norm === 'todasaslojas' || norm === '__todas';
        }

        function favoritosLojaSelecionadaParaApi(valor = mlSkuLojaSelecionada || skuLojaSelecionada || '') {
            const loja = String(valor || '').trim();
            return favoritosEhTodasLojas(loja) ? '' : loja;
        }

        function favoritosNomeLojaExibicao(valor) {
            return favoritosEhTodasLojas(valor) ? FAVORITOS_TODAS_LOJAS_LABEL : String(valor || '').trim();
        }

        function favoritosChavePlanilhaLoja(valor) {
            return skuNormalizarLoja(valor).replace(/[^a-z0-9]+/g, '');
        }
