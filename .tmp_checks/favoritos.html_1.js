
        const userData = JSON.parse(localStorage.getItem('user_data') || 'null');
        const permissions = JSON.parse(localStorage.getItem('permissions') || '{}');
        const JK_BROWSER_SESSION_PARTITION = 'persist:jk-sistema-browser';
        let mlBrowserSessionPartition = JK_BROWSER_SESSION_PARTITION;

        if (!userData) {
            window.location.href = 'frontend_index.html';
        }
        if (!(permissions.full === true || permissions.favoritos)) {
            alert('Acesso nÃ£o autorizado para Favoritos.');
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

        function salvarSessaoNavegadorElectron() {
            if (window.electronAPI && typeof window.electronAPI.flushBrowserSession === 'function') {
                window.electronAPI.flushBrowserSession().catch(() => {});
            }
        }

        window.addEventListener('beforeunload', salvarSessaoNavegadorElectron);

        const favoritosStoreGateEl = document.getElementById('favoritos-store-gate');
        const favoritosStoreGateCardsEl = document.getElementById('favoritos-store-gate-cards');
        const favoritosStoreGateStatusEl = document.getElementById('favoritos-store-gate-status');
        const skuLojaCardsEl = document.getElementById('sku-loja-cards');
        const skuStatusEl = document.getElementById('sku-status');
        const skuFiltroEl = document.getElementById('sku-filtro');
        const skuContadorEl = document.getElementById('sku-contador');
        const skuEmptyEl = document.getElementById('sku-empty');
        const skuTableWrapEl = document.getElementById('sku-table-wrap');
        const skuBodyEl = document.getElementById('sku-body');
        const btnSkuToggleOcultos = document.getElementById('btn-sku-toggle-ocultos');

        const btnBuscar = document.getElementById('btn-buscar');
        const termoEl = document.getElementById('termo');
        const statusEl = document.getElementById('status');
        const resultadosEl = document.getElementById('resultados');

        const linkProdutoWrap = document.getElementById('link-produto-wrap');
        const linkProdutoInfo = document.getElementById('link-produto-info');
        const linkProdutoUrls = document.getElementById('link-produto-urls');

        const buscaTermoWrap = document.getElementById('busca-termo-wrap');
        const topBody = document.getElementById('top-body');
        const allBody = document.getElementById('all-body');
        const topEmpty = document.getElementById('top-empty');
        const allEmpty = document.getElementById('all-empty');
        const topWrap = document.getElementById('top-table-wrap');
        const allWrap = document.getElementById('all-table-wrap');

        const mlSearchTermInput = document.getElementById('ml-search-term');
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
        const mlSkuSelectAllEl = document.getElementById('ml-sku-select-all');
        const mlSkuFazerFavoritosEl = document.getElementById('ml-sku-fazer-favoritos');
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
        const favMlStatusEl = document.getElementById('fav-ml-status');
        const favMlAnunciosBodyEl = document.getElementById('fav-ml-anuncios-body');
        const favMlAnunciosEmptyEl = document.getElementById('fav-ml-anuncios-empty');
        const favOutrosAnunciosBodyEl = document.getElementById('fav-outros-anuncios-body');
        const favOutrosAnunciosEmptyEl = document.getElementById('fav-outros-anuncios-empty');
        const favRankingDataEl = document.getElementById('fav-ranking-data');
        const favMlAnunciosTableEl = document.getElementById('fav-ml-anuncios-table');
        const favOutrosAnunciosTableEl = document.getElementById('fav-outros-anuncios-table');
        const favMlTablesLayoutEl = document.querySelector('.favoritos-ml-tables');
        const histMlSkuSidebarListEl = document.getElementById('hist-ml-sku-sidebar-list');
        const histMlSkuSidebarEmptyEl = document.getElementById('hist-ml-sku-sidebar-empty');
        const histMlSkuSidebarCountEl = document.getElementById('hist-ml-sku-sidebar-count');
        const histMlSkuSidebarSearchEl = document.getElementById('hist-ml-sku-sidebar-search');
        const mlFavoritosPanelEl = document.getElementById('ml-favoritos-panel');
        const mlFavoritosStatusEl = document.getElementById('ml-favoritos-status');
        const mlFavoritosEmptyEl = document.getElementById('ml-favoritos-empty');
        const mlFavoritosListEl = document.getElementById('ml-favoritos-list');
        const mlFavoritosBalloonEl = document.getElementById('ml-favoritos-status-balloon');
        const mlFavoritosBalloonTextEl = document.getElementById('ml-favoritos-status-balloon-text');
        const mlFavoritosBalloonActionsEl = document.getElementById('ml-favoritos-status-balloon-actions');
        const mlFavoritosBalloonOriginalParentEl = mlFavoritosBalloonEl ? mlFavoritosBalloonEl.parentElement : null;
        const mlHistoricoFavoritosStatusEl = document.getElementById('ml-historico-favoritos-status');
        const mlHistoricoFavoritosEmptyEl = document.getElementById('ml-historico-favoritos-empty');
        const mlHistoricoFavoritosListEl = document.getElementById('ml-historico-favoritos-list');
        const mlHistoricoFavoritosLimparEl = document.getElementById('ml-historico-favoritos-limpar');
        const mlBrowserBlueFilterEl = document.getElementById('ml-browser-blue-filter');
        const mlBrowserFrameWrapEl = mlBrowserBlueFilterEl ? mlBrowserBlueFilterEl.closest('.browser-frame-wrap') : null;
        const mlWorkModalEl = document.getElementById('ml-work-modal');
        const mlWorkModalDialogEl = mlWorkModalEl ? mlWorkModalEl.querySelector('.ml-work-modal-dialog') : null;
        const mlWorkModalTitleEl = document.getElementById('ml-work-modal-title');
        const mlWorkModalSubtitleEl = document.getElementById('ml-work-modal-subtitle');
        const mlWorkModalCloseEl = document.getElementById('ml-work-modal-close');
        const mlWorkModalCancelEl = document.getElementById('ml-work-modal-cancel');
        const mlWorkModalBrowserSlotEl = document.getElementById('ml-work-modal-browser-slot');
        const mlWorkModalResultsSlotEl = document.getElementById('ml-work-modal-results-slot');
        const ML_DEFAULT_URL = 'https://www.mercadolivre.com.br/';
        const ML_VENDEDORES_IGNORADOS_RANKING_KEY = 'favoritosMlVendedoresIgnoradosRanking';
        const ML_RANKING_SIDEBAR_COLLAPSED_KEY = 'favoritosMlRankingSidebarCollapsed';
        const ML_RANKING_SIDEBAR_WIDTH_KEY = 'favoritosMlRankingSidebarWidth';
        const ML_SKU_SIDEBAR_COLLAPSED_KEY = 'favoritosMlSkuSidebarCollapsed';
        const ML_SKU_SIDEBAR_WIDTH_KEY = 'favoritosMlSkuSidebarWidth';
        const ML_SKU_LOJA_KEY = 'favoritosMlSkuLojaSelecionada';
        const ML_FAVORITOS_TABLE_LAYOUT_KEY = 'favoritosMlTableLayoutV1';
        const ML_SKU_SIDEBAR_PAGE_SIZE = 50;
        const ML_FAVORITOS_HISTORICO_MAX = 30;
        const AVANT_PRO_LOGIN_EMAIL = 'fredyn-lefer@hotmail.com';
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
        let mlBrowserShellOcultoPorBalao = false;
        let mlWorkModalInicializado = false;
        let mlAnunciosPrimeiraPaginaAtuais = [];
        let mlSkuLojasDisponiveis = [];
        let mlSkuLojaSelecionada = '';
        let mlSkusAnunciosLojaAtual = [];
        let favoritosLojaEntradaSelecionada = false;
        let mlSkuSidebarSelecionados = new Set();
        let mlSkuSidebarFiltro = '';
        let mlSkuSidebarRenderLimit = ML_SKU_SIDEBAR_PAGE_SIZE;
        let mlSkuBuscaRemotaTimer = null;
        let mlSkuBuscaRemotaRunId = 0;
        const mlSkuBuscaRemotaCache = new Set();
        let mlFavoritosEmExecucao = false;
        let mlFavoritosCancelado = false;
        let mlFavoritosAbortController = null;
        let mlFavoritosResultadosPorSku = new Map();
        let mlFavoritosTiposRankingEmExecucao = new Set();
        let mlFavoritosBalloonTimer = null;
        let mlFavoritosPerguntaResolver = null;
        let mlHistoricoFavoritosCache = [];
        let mlHistoricoFavoritosServidorCarregado = false;
        let mlHistoricoFavoritosSaveTimer = null;
        let favMlSkuSelecionado = '';
        let histMlSkuSelecionado = '';
        let histMlHistoricoExecucaoSelecionadaId = '';
        let favMlAnunciosSkuAtual = [];
        let mlRankingMediaFrame = null;
        let mlVendedoresIgnoradosRanking = null;
        let mlVendedoresIgnoradosServidorCarregado = false;
        let mlVendedoresIgnoradosSaveTimer = null;
        let favoritosTableLayout = carregarLayoutTabelasFavoritos();
        let mlAvantAutoTimer = null;
        let mlAvantAutoRunId = 0;
        let mlAvantScrollExecutada = false;
        let skuLojasDisponiveis = [];
        let skuDados = [];
        let skuLojaSelecionada = '';
        let skuSkusOcultos = new Set();
        let skuMostrarOcultos = false;
        let skuDescricaoAutoRunId = 0;
        let skuDescricaoAutoTimer = null;

        function headersJsonAutenticado() {
            const base = (typeof obterAuthHeaders === 'function') ? obterAuthHeaders() : {};
            return { ...base, 'Content-Type': 'application/json' };
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
                alert('NÃ£o foi possÃ­vel copiar o link.');
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
                    console.warn('Falha ao abrir anÃºncio no navegador interno com Avant Pro:', err);
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
                    console.warn('Falha ao abrir anÃºncio no Google Chrome:', err);
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
            return skuTexto(row, ['sku', 'SKU', 'codigo', 'Codigo', 'cÃ³digo'], '');
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

        function skuAgendarBuscaDescricoesAutomaticas(delay = 250) {
            if (skuDescricaoAutoTimer) clearTimeout(skuDescricaoAutoTimer);
            skuDescricaoAutoTimer = setTimeout(() => {
                skuBuscarDescricoesAutomaticas().catch(err => {
                    if (skuStatusEl) skuStatusEl.textContent = `Erro ao buscar descriÃ§Ãµes: ${err && err.message ? err.message : err}`;
                });
            }, delay);
        }

        async function skuBuscarDescricoesAutomaticas() {
            const runId = ++skuDescricaoAutoRunId;
            const linhas = skuFiltrarDados()
                .filter(row => {
                    const status = String(row.descricao_ml_status || '').trim();
                    return skuObterSku(row) && !row.descricao_ml && status !== 'loading' && status !== 'ok' && status !== 'nao_encontrado' && status !== 'sem_descricao';
                });
            if (!linhas.length) return;

            const skus = Array.from(new Set(linhas.map(row => skuObterSku(row)).filter(Boolean)));
            linhas.forEach(row => {
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
                const body = { skus: lote };
                if (skuLojaSelecionada) {
                    body.loja = skuLojaSelecionada;
                }

                if (skuStatusEl) {
                    skuStatusEl.textContent = `Buscando descricoes: ${processadas}/${skus.length} SKU(s)...`;
                }

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
                if (runId !== skuDescricaoAutoRunId) return;
                const resultados = Array.isArray(data.results) ? data.results : [];
                const mapaResultados = new Map(resultados.map(item => [String(item.sku || '').trim().toLowerCase(), item]));
                skuDados.forEach(row => {
                    const resultado = mapaResultados.get(skuChaveDescricao(row));
                    if (resultado) skuAplicarDescricaoResultado(row, resultado);
                });
                processadas += lote.length;
                encontradas += resultados.filter(item => String(item.descricao || '').trim()).length;
                skuRenderizarTabela();

                if (skuStatusEl) {
                    skuStatusEl.textContent = `Descricoes carregadas: ${encontradas}/${processadas} SKU(s) processados de ${skus.length}.`;
                }
            }
        }
        async function skuBuscarDescricaoManual(row, botao) {
            const sku = skuObterSku(row);
            if (!sku) return;
            if (botao) botao.disabled = true;
            row.descricao_ml_status = 'loading';
            row.descricao_ml_erro = '';
            skuRenderizarTabela();
            if (skuStatusEl) skuStatusEl.textContent = `Buscando descricao do SKU ${sku}...`;

            try {
                const body = { skus: [sku] };
                if (skuLojaSelecionada) body.loja = skuLojaSelecionada;
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
                        if (skuChaveDescricao(item) === chave) skuAplicarDescricaoResultado(item, resultado);
                    });
                } else {
                    row.descricao_ml_status = 'nao_encontrado';
                }
                skuRenderizarTabela();
                if (skuStatusEl) {
                    const temDescricao = resultado && String(resultado.descricao || '').trim();
                    skuStatusEl.textContent = temDescricao
                        ? `Descricao do SKU ${sku} carregada e salva no cadastro.`
                        : `Nenhuma descricao encontrada para o SKU ${sku}.`;
                }
            } catch (err) {
                row.descricao_ml_status = 'erro';
                row.descricao_ml_erro = err && err.message ? err.message : String(err);
                skuRenderizarTabela();
                if (skuStatusEl) skuStatusEl.textContent = `Erro ao buscar descricao do SKU ${sku}: ${row.descricao_ml_erro}`;
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
            if (!skuLojaCardsEl) return;
            const lojas = skuLojasComDados();
            const lojasValidas = new Set(lojas.map(loja => skuNormalizarLoja(loja.nome)));
            if (!lojas.length) {
                skuLojaSelecionada = '';
                skuSalvarPreferenciaLoja();
                skuLojaCardsEl.innerHTML = '<div class="muted">Nenhuma loja com Bling e Mercado Livre conectados.</div>';
                return;
            }
            if (!skuLojaSelecionada || skuLojaSelecionada === '__todas' || !lojasValidas.has(skuNormalizarLoja(skuLojaSelecionada))) {
                skuLojaSelecionada = lojas[0].nome;
                skuSalvarPreferenciaLoja();
            }

            skuLojaCardsEl.innerHTML = '';
            const criarCard = (nome, total, valor) => {
                const card = document.createElement('button');
                card.type = 'button';
                card.className = 'sku-store-card' + (skuLojaSelecionada === valor ? ' active' : '');

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
        }

        function favoritosOcultarTelaPrincipal() {
            document.body.classList.add('favoritos-store-pending');
            if (favoritosStoreGateEl) favoritosStoreGateEl.classList.remove('hidden');
        }

        function favoritosLojaAtualNormalizada() {
            return skuNormalizarLoja(mlSkuLojaSelecionada || skuLojaSelecionada || '');
        }

        function favoritosResetarEstadoLoja() {
            mlSkuSidebarSelecionados.clear();
            mlSkuSidebarFiltro = '';
            mlSkuSidebarRenderLimit = ML_SKU_SIDEBAR_PAGE_SIZE;
            if (mlSkuSidebarSearchEl) mlSkuSidebarSearchEl.value = '';
            mlFavoritosResultadosPorSku = new Map();
            favMlSkuSelecionado = '';
            histMlSkuSelecionado = '';
            favMlAnunciosSkuAtual = [];
            mlAnunciosPrimeiraPaginaAtuais = [];
            if (favRankingDataEl) favRankingDataEl.textContent = '';
        }

        function favoritosDefinirLojaSelecionada(nome) {
            const nomeLoja = String(nome || '').trim();
            if (!nomeLoja) return false;
            const anterior = favoritosLojaAtualNormalizada();
            const nova = skuNormalizarLoja(nomeLoja);
            skuLojaSelecionada = nomeLoja;
            mlSkuLojaSelecionada = nomeLoja;
            skuSalvarPreferenciaLoja();
            mlSkuSalvarPreferenciaLoja();
            if (anterior && anterior !== nova) {
                favoritosResetarEstadoLoja();
            }
            return anterior !== nova;
        }

        function favoritosRenderizarCardsEntrada(lojas) {
            if (!favoritosStoreGateCardsEl) return;
            const lista = Array.isArray(lojas) ? lojas : [];
            favoritosStoreGateCardsEl.innerHTML = '';
            if (!lista.length) {
                favoritosStoreGateCardsEl.innerHTML = '<div class="muted">Nenhuma loja com Bling e Mercado Livre conectados foi encontrada em Integracoes.</div>';
                return;
            }
            const preferida = skuNormalizarLoja(mlSkuCarregarPreferenciaLoja() || skuCarregarPreferenciaLoja());
            lista.forEach(loja => {
                const nome = String((loja && loja.nome) || loja || '').trim();
                if (!nome) return;
                const card = document.createElement('button');
                card.type = 'button';
                card.className = 'sku-store-card' + (preferida && preferida === skuNormalizarLoja(nome) ? ' active' : '');

                const nomeEl = document.createElement('span');
                nomeEl.className = 'sku-store-name';
                nomeEl.textContent = nome;

                const metaEl = document.createElement('span');
                metaEl.className = 'sku-store-meta';
                metaEl.textContent = 'Bling + Mercado Livre';

                card.appendChild(nomeEl);
                card.appendChild(metaEl);
                card.addEventListener('click', () => favoritosSelecionarLojaEntrada(nome));
                favoritosStoreGateCardsEl.appendChild(card);
            });
        }

        async function favoritosCarregarLojasEntrada() {
            favoritosLojaEntradaSelecionada = false;
            favoritosOcultarTelaPrincipal();
            if (favoritosStoreGateStatusEl) {
                favoritosStoreGateStatusEl.classList.remove('error');
                favoritosStoreGateStatusEl.textContent = 'Carregando lojas cadastradas em Integracoes...';
            }
            try {
                const response = await fetch('/api/favoritos/ml/skus-anuncios?apenas_lojas=1', {
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
                const lojas = Array.isArray(data.lojas) ? data.lojas : [];
                mlSkuLojasDisponiveis = lojas;
                favoritosRenderizarCardsEntrada(lojas);
                if (favoritosStoreGateStatusEl) {
                    favoritosStoreGateStatusEl.classList.remove('error');
                    favoritosStoreGateStatusEl.textContent = data.warning || (lojas.length
                        ? 'Escolha uma loja para carregar somente as informacoes dela.'
                        : 'Nenhuma loja disponivel para fazer favoritos.');
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
            await Promise.allSettled([
                carregarSkuFavoritos(),
                mlSkuCarregarSkusAnuncios(nomeLoja)
            ]);
        }

        async function favoritosSelecionarLojaModulo(nome) {
            const nomeLoja = String(nome || '').trim();
            if (!nomeLoja) return;
            const mudou = favoritosDefinirLojaSelecionada(nomeLoja);
            favoritosLojaEntradaSelecionada = true;
            favoritosMostrarTelaPrincipal();
            if (!mudou) return;
            skuRenderizarCardsLojas();
            skuRenderizarTabela();
            await mlSkuCarregarSkusAnuncios(nomeLoja);
        }

        function mlSkuRenderizarCardsLojas() {
            if (!mlSkuLojaCardsEl) return;
            const lojas = Array.isArray(mlSkuLojasDisponiveis) ? mlSkuLojasDisponiveis : [];
            if (!lojas.length) {
                mlSkuLojaCardsEl.innerHTML = '<div class="muted">Nenhuma loja com Mercado Livre conectado em IntegraÃ§Ãµes.</div>';
                return;
            }
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

        async function mlSkuCarregarSkusAnuncios(loja = '') {
            if (!mlSkuLojaCardsEl && !mlSkuSidebarListEl) return;
            const preferida = loja || mlSkuLojaSelecionada || mlSkuCarregarPreferenciaLoja();
            if (mlSkuStatusEl) {
                mlSkuStatusEl.textContent = preferida
                    ? `Carregando SKUs dos anÃºncios da loja ${preferida}...`
                    : 'Carregando lojas integradas ao Mercado Livre...';
            }
            try {
                const params = new URLSearchParams();
                if (preferida) params.set('loja', preferida);
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
                mlSkuLojasDisponiveis = Array.isArray(data.lojas) ? data.lojas : [];
                mlSkuLojaSelecionada = String(data.loja || preferida || '').trim();
                mlSkusAnunciosLojaAtual = Array.isArray(data.skus) ? data.skus : [];
                mlSkuSidebarRenderLimit = ML_SKU_SIDEBAR_PAGE_SIZE;
                if (mlSkuLojaSelecionada) mlSkuSalvarPreferenciaLoja();
                mlSkuRenderizarCardsLojas();
                renderizarSkuSidebarMercadoLivre();
                renderizarFavoritosSkuSidebar();
                renderizarHistoricoSkuSidebar();
                atualizarEstadoSidebarRanking();
                if (mlSkuStatusEl) {
                    if (data.warning) {
                        mlSkuStatusEl.textContent = data.warning;
                    } else {
                        mlSkuStatusEl.textContent = mlSkuLojaSelecionada
                            ? `${mlSkusAnunciosLojaAtual.length} SKU(s) Ãºnicos carregados de ${data.total_anuncios || 0} anÃºncio(s) ativos da loja ${mlSkuLojaSelecionada}.`
                            : 'Selecione uma loja para carregar os SKUs do Mercado Livre.';
                    }
                }
            } catch (err) {
                mlSkusAnunciosLojaAtual = [];
                renderizarSkuSidebarMercadoLivre();
                renderizarFavoritosSkuSidebar();
                renderizarHistoricoSkuSidebar();
                atualizarEstadoSidebarRanking();
                if (mlSkuStatusEl) {
                    mlSkuStatusEl.textContent = `Erro ao carregar SKUs do Mercado Livre: ${err && err.message ? err.message : err}`;
                }
            }
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
            if (skuLojaSelecionada) {
                const lojaSelecionadaNorm = skuNormalizarLoja(skuLojaSelecionada);
                lista = lista.filter(row => skuNormalizarLoja(skuObterLoja(row)) === lojaSelecionadaNorm);
            } else {
                lista = [];
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
            inputEl.classList.remove('saved', 'error');
            inputEl.classList.add('saving');
            if (skuStatusEl) skuStatusEl.textContent = `Salvando pesquisas do SKU ${sku} no cadastro...`;
            try {
                const response = await fetch('/api/favoritos/skus/pesquisas', {
                    method: 'PUT',
                    headers: headersJsonAutenticado(),
                    body: JSON.stringify({
                        sku,
                        produto: skuObterProduto(row),
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
                inputEl.classList.add('saved');
                if (skuStatusEl) skuStatusEl.textContent = `Pesquisas do SKU ${sku} salvas no cadastro.`;
                setTimeout(() => inputEl.classList.remove('saved'), 1200);
            } catch (err) {
                inputEl.classList.add('error');
                if (skuStatusEl) skuStatusEl.textContent = `Erro ao salvar pesquisas do SKU ${sku}: ${err && err.message ? err.message : err}`;
            } finally {
                inputEl.classList.remove('saving');
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
            input.addEventListener('keydown', (event) => {
                if (event.key === 'Enter') {
                    event.preventDefault();
                    input.blur();
                }
            });
            input.addEventListener('change', () => {
                const valor = input.value.trim();
                row[campo] = valor;
                input.value = valor;
                input.dataset.valorOriginal = valor;
                skuSalvarPesquisasCadastro(row, input);
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
                row.descricao_ml_item_id ? `AnÃºncio: ${row.descricao_ml_item_id}` : '',
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
                preview.textContent = 'AnÃºncio encontrado sem descriÃ§Ã£o.';
            } else if (row.descricao_ml_status === 'nao_encontrado') {
                preview.textContent = 'Nenhum anÃºncio ativo encontrado para este SKU.';
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
                if (!skuLojaSelecionada) return false;
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

        function skuRenderizarTabela() {
            if (!skuBodyEl || !skuTableWrapEl || !skuEmptyEl) return;
            const lista = skuFiltrarDados();
            skuBodyEl.innerHTML = '';
            const frag = document.createDocumentFragment();

            lista.forEach(row => {
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
                const lojaTexto = skuLojaSelecionada || 'loja selecionada';
                const ocultos = skuContarOcultosLojaAtual();
                skuContadorEl.textContent = `${lista.length} SKU(s) em ${lojaTexto}${ocultos ? ` | ${ocultos} oculto(s)` : ''}`;
            }
            skuAtualizarBotaoOcultos();
        }

        async function carregarSkuFavoritos() {
            if (!skuLojaCardsEl || !skuBodyEl) return;
            skuStatusEl.textContent = 'Carregando SKUs...';
            try {
                skuLojaSelecionada = skuLojaSelecionada || skuCarregarPreferenciaLoja();
                const [response, ocultosResponse] = await Promise.all([
                    fetch('/api/favoritos/skus', {
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
                if (ocultosResponse.ok) {
                    const ocultosData = await ocultosResponse.json();
                    skuSkusOcultos = new Set((ocultosData.skus_ocultos || []).map(skuChaveOculto).filter(Boolean));
                } else {
                    skuSkusOcultos = new Set();
                }
                skuLojasDisponiveis = Array.isArray(data.lojas) ? data.lojas : [];
                skuDados = Array.isArray(data.skus) ? data.skus : [];
                skuRenderizarCardsLojas();
                skuRenderizarTabela();
                skuStatusEl.textContent = skuDados.length
                    ? `Total de ${skuDados.length} SKU(s) carregado(s).`
                    : 'Nenhum SKU encontrado no estoque compilado.';
            } catch (err) {
                skuStatusEl.textContent = `Erro ao carregar SKUs: ${err && err.message ? err.message : err}`;
            }
        }

        if (skuFiltroEl) {
            skuFiltroEl.addEventListener('input', () => {
                skuRenderizarTabela();
            });
        }

        if (btnSkuToggleOcultos) {
            btnSkuToggleOcultos.addEventListener('click', () => {
                skuMostrarOcultos = !skuMostrarOcultos;
                skuRenderizarTabela();
            });
        }

        if (btnBuscar && termoEl) {
            btnBuscar.addEventListener('click', buscar);
            termoEl.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') buscar();
            });
        }

        if (mlRankingToggleEl) {
            mlRankingToggleEl.addEventListener('click', alternarRankingSidebar);
            aplicarEstadoRankingSidebar(rankingSidebarEstaMinimizado());
        }
        if (mlSidebarResizerEl) {
            aplicarLarguraSidebarRanking(carregarLarguraSidebarRanking());
            mlSidebarResizerEl.addEventListener('pointerdown', iniciarAjusteLarguraSidebar);
        }
        if (mlSkuSidebarToggleEls.length) {
            mlSkuSidebarToggleEls.forEach(botao => {
                botao.addEventListener('click', alternarSkuSidebar);
            });
            aplicarEstadoSkuSidebar(skuSidebarEstaMinimizado());
        }
        if (mlSkuSidebarResizerEl) {
            aplicarLarguraSkuSidebar(carregarLarguraSkuSidebar());
            mlSkuSidebarResizerEl.addEventListener('pointerdown', iniciarAjusteLarguraSkuSidebar);
        }
        if (mlSkuSidebarSearchEl) {
            mlSkuSidebarSearchEl.addEventListener('input', () => {
                mlSkuSidebarFiltro = mlSkuSidebarSearchEl.value || '';
                mlSkuSidebarRenderLimit = ML_SKU_SIDEBAR_PAGE_SIZE;
                renderizarSkuSidebarMercadoLivre();
            });
        }
        if (favMlSkuSidebarSearchEl) {
            favMlSkuSidebarSearchEl.addEventListener('input', renderizarFavoritosSkuSidebar);
        }
        if (histMlSkuSidebarSearchEl) {
            histMlSkuSidebarSearchEl.addEventListener('input', renderizarHistoricoSkuSidebar);
        }
        if (mlSkuSelectAllEl) {
            mlSkuSelectAllEl.addEventListener('click', alternarSelecaoTodosSkuSidebar);
        }
        if (mlSkuFazerFavoritosEl) {
            mlSkuFazerFavoritosEl.addEventListener('click', fazerFavoritosSkusSelecionados);
        }
        if (mlSkuCancelarFavoritosEl) {
            mlSkuCancelarFavoritosEl.addEventListener('click', cancelarFavoritosEmExecucao);
        }
        if (mlHistoricoFavoritosLimparEl) {
            mlHistoricoFavoritosLimparEl.addEventListener('click', limparHistoricoFavoritos);
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
            statusEl.textContent = 'Produto encontrado. Selecione uma estratÃ©gia de busca:';

            buscaTermoWrap.classList.add('hidden');
            linkProdutoWrap.classList.remove('hidden');

            const dados = data.dados || {};
            if (dados.titulo) {
                const tr = document.createElement('tr');
                tr.innerHTML = `<td><strong>TÃ­tulo:</strong></td><td>${dados.titulo}</td>`;
                linkProdutoInfo.appendChild(tr);
            }
            if (dados.codigo) {
                const tr = document.createElement('tr');
                tr.innerHTML = `<td><strong>CÃ³digo OEM:</strong></td><td>${dados.codigo}</td>`;
                linkProdutoInfo.appendChild(tr);
            }
            if (dados.veiculo) {
                const tr = document.createElement('tr');
                tr.innerHTML = `<td><strong>VeÃ­culo:</strong></td><td>${dados.veiculo}</td>`;
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
            statusEl.textContent = `Total de anÃºncios analisados: ${data.total}`;

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
            if (nomeAba === 'navegador') {
                cancelarAberturaMercadoLivreAoEntrar();
            }
            if (nomeAba === 'favoritos') {
                prepararAbaFavoritosMl();
            }
            if (nomeAba === 'historico') {
                prepararAbaHistoricoFavoritos();
            }
            // A pesquisa abre/carrega o quadro interno sob demanda.
        }

        function cancelarAberturaMercadoLivreAoEntrar() {
            if (mlNavegadorAutoOpenTimer) {
                clearTimeout(mlNavegadorAutoOpenTimer);
                mlNavegadorAutoOpenTimer = null;
            }
        }

        function agendarAberturaMercadoLivreAoEntrar() {
            cancelarAberturaMercadoLivreAoEntrar();
            mlNavegadorAutoOpenTimer = setTimeout(() => {
                mlNavegadorAutoOpenTimer = null;
                const abaNavegadorAtiva = document.getElementById('aba-navegador')?.classList.contains('active');
                if (!abaNavegadorAtiva) return;
                if (!mlUrlInput.value || !/^https?:\/\//i.test(mlUrlInput.value.trim())) {
                    mlUrlInput.value = ML_DEFAULT_URL;
                }
                abrirMercadoLivreNoPrograma();
            }, 80);
        }

        function normalizarUrl(url) {
            const valor = (url || '').trim();
            if (!valor) return ML_DEFAULT_URL;
            if (/^https?:\/\//i.test(valor)) return valor;
            return `https://${valor}`;
        }

        function construirUrlPesquisaMercadoLivre(termo) {
            const valor = (termo || '').trim();
            if (!valor) return ML_DEFAULT_URL;
            return `https://lista.mercadolivre.com.br/?q=${encodeURIComponent(valor)}`;
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

        function calcularMetricasMediaVendas(anuncio) {
            const vendas = parseVendasAvantPro(anuncio);
            const dataCriacao = parseDataCriacao(anuncio && anuncio.data_criacao);
            if (vendas === null || !dataCriacao) {
                return { vendas, meses: null, media: null, dataCriacao: null };
            }

            const agora = new Date();
            const diffMs = agora.getTime() - dataCriacao.getTime();
            if (!Number.isFinite(diffMs) || diffMs < 0) {
                return { vendas, meses: null, media: null, dataCriacao };
            }

            const dias = Math.max(1, diffMs / 86400000);
            const meses = Math.max(1, dias / 30.4375);
            const media = vendas / meses;
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
            if (meses <= 1) return '1 mÃªs';
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
            const abaFavoritosAtiva = document.getElementById('aba-favoritos')?.classList.contains('active');
            const abaHistoricoAtiva = document.getElementById('aba-historico')?.classList.contains('active');
            const temAnuncios = Array.isArray(mlAnunciosPrimeiraPaginaAtuais) && mlAnunciosPrimeiraPaginaAtuais.length > 0;
            const navegadorEmBalao = balaoResultadosMlAberto();
            document.body.classList.toggle('ml-ranking-sidebar-active', !!(abaNavegadorAtiva && temAnuncios));
            document.body.classList.toggle('ml-sku-sidebar-active', !!(abaNavegadorAtiva || abaFavoritosAtiva || abaHistoricoAtiva));
            if (mlShellBrowserProxy) {
                if (navegadorEmBalao && mlShellBrowserProxy.__visible) atualizarPosicaoNavegadorMlShell();
                else enviarNavegadorMlParaShell('jk-ml-browser-hide');
            }
            aplicarEstadoRankingSidebar(rankingSidebarEstaMinimizado());
            aplicarEstadoSkuSidebar(skuSidebarEstaMinimizado());
        }

        function setBrowserStatus(message) {
            if (mlShellBrowserProxy) {
                mlShellBrowserProxy.__visible = false;
                enviarNavegadorMlParaShell('jk-ml-browser-hide');
            }
            mlBrowserHost.innerHTML = `
                <div class="browser-warning">
                    <strong>${message}</strong>
                    <span>VocÃª pode alterar a URL e clicar em "Abrir no Programa" novamente.</span>
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

        function abrirBalaoResultadosMl(opcoes = {}) {
            inicializarBalaoResultadosMl();
            if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = opcoes.titulo || 'Resultados do Mercado Livre';
            if (mlWorkModalSubtitleEl) mlWorkModalSubtitleEl.textContent = opcoes.subtitulo || '';
            if (mlFavoritosPanelEl && opcoes.mostrarFavoritos) mlFavoritosPanelEl.classList.remove('hidden');
            if (mlFavoritosPanelEl && !opcoes.mostrarFavoritos && !mlFavoritosEmExecucao) mlFavoritosPanelEl.classList.add('hidden');
            const browserCompleto = !!(opcoes.browserCompleto || mlFavoritosEmExecucao);
            if (mlWorkModalEl) {
                mlWorkModalEl.dataset.browserOnly = browserCompleto ? '1' : '0';
                mlWorkModalEl.classList.toggle('is-browser-only', browserCompleto);
                mlWorkModalEl.classList.remove('hidden');
                document.body.classList.add('ml-work-modal-open');
            }
            atualizarFiltroAzulFavoritos();
            posicionarBalaoFavoritosStatus();
            setTimeout(() => {
                atualizarPosicaoNavegadorMlShell();
                agendarAtualizacaoPosicaoNavegadorMlShell();
            }, 80);
        }

        function fecharBalaoResultadosMl() {
            if (mlFavoritosEmExecucao) {
                mostrarBalaoFavoritosStatus('Aguarde o processo finalizar ou use Cancelar favoritos antes de fechar.', {
                    erro: true,
                    tempoMs: 3500
                });
                return;
            }
            if (mlWorkModalEl) mlWorkModalEl.classList.add('hidden');
            if (mlWorkModalEl) mlWorkModalEl.classList.remove('is-browser-only');
            document.body.classList.remove('ml-work-modal-open');
            posicionarBalaoFavoritosStatus();
            if (mlShellBrowserProxy) {
                mlShellBrowserProxy.__visible = false;
                enviarNavegadorMlParaShell('jk-ml-browser-hide');
            }
        }

        function posicionarBalaoFavoritosStatus() {
            if (!mlFavoritosBalloonEl) return;
            const sobreNavegador = balaoResultadosMlAberto();
            const statusVisivel = !mlFavoritosBalloonEl.classList.contains('hidden') || mlFavoritosEmExecucao;
            const destino = sobreNavegador && mlBrowserFrameWrapEl
                ? mlBrowserFrameWrapEl
                : mlFavoritosBalloonOriginalParentEl;
            mlFavoritosBalloonEl.classList.toggle('is-over-browser', !!sobreNavegador);
            if (mlBrowserFrameWrapEl) {
                mlBrowserFrameWrapEl.classList.toggle('has-status-overlay', !!(sobreNavegador && statusVisivel));
            }
            if (destino && mlFavoritosBalloonEl.parentElement !== destino) {
                destino.appendChild(mlFavoritosBalloonEl);
            }
            const temAcoes = !!(mlFavoritosBalloonActionsEl && mlFavoritosBalloonActionsEl.children.length);
            const textoStatus = mlFavoritosBalloonTextEl ? mlFavoritosBalloonTextEl.textContent : '';
            atualizarStatusFavoritosNoNavegadorMl(textoStatus, !!(sobreNavegador && mlFavoritosEmExecucao && statusVisivel && !temAcoes));
            agendarAtualizacaoPosicaoNavegadorMlShell();
        }

        function criarMlWebview() {
            if (mlWebviewEl && mlWebviewEl.parentNode) {
                aplicarScrollbarsDiscretasNoWebview(mlWebviewEl);
                return mlWebviewEl;
            }

            mlBrowserHost.innerHTML = '';
            if (usarNavegadorMlNoShellElectron()) {
                mlBrowserHost.innerHTML = `
                    <div class="browser-warning">
                        <strong>Carregando Mercado Livre no navegador interno...</strong>
                        <span>Se a pagina pedir login, conclua no quadro abaixo.</span>
                    </div>
                `;
                mlWebviewEl = criarProxyNavegadorMlShell();
                aplicarScrollbarsDiscretasNoWebview(mlWebviewEl);
                return mlWebviewEl;
            }

            mlWebviewEl = document.createElement('webview');
            mlWebviewEl.className = 'browser-webview';
            mlWebviewEl.setAttribute('partition', obterParticaoNavegadorPersistente());
            mlWebviewEl.setAttribute('allowpopups', 'true');
            mlWebviewEl.setAttribute('webpreferences', 'contextIsolation=yes,nodeIntegration=no');

            const isAllowedWebUrl = (targetUrl) => {
                const value = String(targetUrl || '').trim();
                if (!value) return true;
                return /^(https?:|about:blank)$/i.test(value);
            };
            const blockIfExternalProtocol = (event) => {
                const targetUrl = event && event.url ? event.url : '';
                if (!isAllowedWebUrl(targetUrl)) {
                    event.preventDefault();
                }
            };

            const syncUrl = (event) => {
                const nextUrl = (event && event.url) || (typeof mlWebviewEl.getURL === 'function' ? mlWebviewEl.getURL() : '');
                if (nextUrl && /^https?:\/\//i.test(nextUrl)) {
                    mlUrlInput.value = nextUrl;
                }
            };

            mlWebviewEl.addEventListener('did-navigate', syncUrl);
            mlWebviewEl.addEventListener('did-navigate-in-page', syncUrl);
            mlWebviewEl.addEventListener('dom-ready', () => {
                aplicarScrollbarsDiscretasNoWebview(mlWebviewEl);
                tentarLoginAvantProNoWebview(mlWebviewEl);
            });
            mlWebviewEl.addEventListener('did-finish-load', () => {
                aplicarScrollbarsDiscretasNoWebview(mlWebviewEl);
                tentarLoginAvantProNoWebview(mlWebviewEl);
            });
            mlWebviewEl.addEventListener('did-finish-load', salvarSessaoNavegadorElectron);
            mlWebviewEl.addEventListener('did-navigate', salvarSessaoNavegadorElectron);
            mlWebviewEl.addEventListener('will-navigate', blockIfExternalProtocol);
            mlWebviewEl.addEventListener('will-frame-navigate', blockIfExternalProtocol);
            mlWebviewEl.addEventListener('new-window', (event) => {
                if (event && event.url) {
                    event.preventDefault();
                    if (isAllowedWebUrl(event.url)) {
                        navegarMlWebview(event.url).catch(() => {});
                    }
                }
            });

            mlBrowserHost.appendChild(mlWebviewEl);
            return mlWebviewEl;
        }

        function navegarMlWebview(url) {
            return new Promise((resolve, reject) => {
                const webview = criarMlWebview();
                let done = false;
                const finish = (err) => {
                    if (done) return;
                    done = true;
                    clearTimeout(timer);
                    webview.removeEventListener('did-finish-load', onLoad);
                    webview.removeEventListener('did-fail-load', onFail);
                    if (err) reject(err);
                    else resolve();
                };
                const onLoad = () => finish();
                const onFail = (event) => finish(new Error((event && event.errorDescription) || 'Falha ao carregar pÃ¡gina.'));
                const timer = setTimeout(() => finish(), 25000);

                webview.addEventListener('did-finish-load', onLoad);
                webview.addEventListener('did-fail-load', onFail);
                webview.src = url;
            });
        }

        const ML_WEBVIEW_EXTRACT_SCRIPT = `
            (async function () {
                try {
                    var sleep = function (ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); };
                    var currentUrl = String(window.location.href || '');
                    var fastLinksOnly = !!window.__JK_ML_FAST_LINKS;
                    var bodyText = String(document.body && document.body.innerText ? document.body.innerText : '');
                    var lowerText = bodyText.toLowerCase();
                    var needsLogin =
                        currentUrl.indexOf('/gz/account-verification') >= 0 ||
                        currentUrl.indexOf('/jms/mlb/lgz/login') >= 0 ||
                        lowerText.indexOf('para continuar, acesse sua conta') >= 0;

                    var selectors = [
                        'li.ui-search-layout__item',
                        'div.ui-search-result__wrapper',
                        'div.ui-search-result',
                        'div.poly-card',
                        'section.poly-card',
                        '[class*="poly-card"]',
                        '[class*="ui-search-result"]',
                        '[class*="ui-search-layout__item"]',
                        '[data-testid*="result"]',
                        'main ol > li',
                        'main ul > li'
                    ].join(',');

                    var hasProductSignal = function () {
                        if (document.querySelector(selectors)) return true;
                        if (document.querySelector('a[href*="MLB"], a[href*="/p/MLB"], a[href*="wid=MLB"], a[href*="item_id"]')) return true;
                        return false;
                    };

                    var maxRounds = fastLinksOnly ? 24 : 30;
                    for (var round = 0; round < maxRounds; round += 1) {
                        if (hasProductSignal()) break;
                        await sleep(250);
                    }

                    var isValidItemId = function (itemId) {
                        itemId = String(itemId || '').trim().toUpperCase().replace('-', '');
                        var match = itemId.match(/^MLB(\\d+)$/);
                        return !!(match && match[1] && match[1].length >= 8);
                    };

                    var isProductUrl = function (href) {
                        if (!href) return false;
                        href = String(href);
                        var idMatch = href.match(/\\bMLB-?(\\d{6,})\\b/i);
                        if (idMatch && !isValidItemId('MLB' + idMatch[1])) return false;
                        return (
                            href.indexOf('/MLB-') >= 0 ||
                            href.indexOf('/p/MLB') >= 0 ||
                            href.indexOf('/up/MLB') >= 0 ||
                            href.indexOf('pdp_filters=item_id%3AMLB') >= 0 ||
                            href.indexOf('pdp_filters=item_id:MLB') >= 0 ||
                            href.indexOf('wid=MLB') >= 0 ||
                            /\\bMLB-?\\d{6,}\\b/i.test(href)
                        );
                    };

                    var cleanUrl = function (href) {
                        if (!href) return '';
                        href = String(href).split('#')[0].trim();
                        if (/^\\/\\//.test(href)) return 'https:' + href;
                        if (/^\\//.test(href)) return 'https://www.mercadolivre.com.br' + href;
                        return href;
                    };

                    var urlFromItemId = function (itemId) {
                        itemId = String(itemId || '').trim().toUpperCase().replace('-', '');
                        if (!isValidItemId(itemId)) return '';
                        return 'https://produto.mercadolivre.com.br/' + itemId.replace('MLB', 'MLB-') + '-_JM';
                    };
                    var safeDecode = function (value) {
                        var text = String(value || '');
                        try {
                            return decodeURIComponent(text);
                        } catch (e) {
                            try { return decodeURI(text); } catch (e2) { return text; }
                        }
                    };

                    var extractItemId = function (href) {
                        if (!href) return '';
                        href = safeDecode(href);
                        var patterns = [
                            /[?&]wid=(MLB\\d+)/i,
                            /[?&]item_id=(MLB\\d+)/i,
                            /item_id:?(MLB\\d+)/i,
                            /item_id%3A(MLB\\d+)/i,
                            /\\/(MLB-?\\d+)/i,
                            /\\b(MLB-?\\d{6,})\\b/i
                        ];
                        for (var p = 0; p < patterns.length; p += 1) {
                            var match = href.match(patterns[p]);
                            if (match && match[1]) {
                                var candidate = match[1].replace('-', '').toUpperCase();
                                if (isValidItemId(candidate)) return candidate;
                            }
                        }
                        return '';
                    };
                    var extractItemIdFromNode = function (node, fallbackHref) {
                        var sources = [fallbackHref || ''];
                        try {
                            if (node) {
                                sources.push(node.getAttribute('data-item-id') || '');
                                sources.push(node.getAttribute('data-id') || '');
                                sources.push(node.getAttribute('id') || '');
                                sources.push(node.innerHTML || '');
                                var descendants = Array.prototype.slice.call(node.querySelectorAll('[id], [data-item-id], [data-id], [href]'));
                                for (var d = 0; d < descendants.length && d < 80; d += 1) {
                                    sources.push(descendants[d].getAttribute('data-item-id') || '');
                                    sources.push(descendants[d].getAttribute('data-id') || '');
                                    sources.push(descendants[d].getAttribute('id') || '');
                                    sources.push(descendants[d].getAttribute('href') || '');
                                }
                            }
                        } catch (e) {}
                        for (var s = 0; s < sources.length; s += 1) {
                            var id = extractItemId(sources[s]);
                            if (id) return id;
                        }
                        return '';
                    };
                    var findProductHrefInNode = function (node) {
                        if (!node) return '';
                        var sources = [];
                        try {
                            sources.push(node.href || '');
                            sources.push(node.getAttribute && node.getAttribute('href') || '');
                            sources.push(node.getAttribute && node.getAttribute('data-href') || '');
                            sources.push(node.getAttribute && node.getAttribute('data-url') || '');
                            sources.push(node.getAttribute && node.getAttribute('data-permalink') || '');
                            sources.push(node.getAttribute && node.getAttribute('data-item-id') || '');
                            sources.push(node.getAttribute && node.getAttribute('data-id') || '');
                            sources.push(node.getAttribute && node.getAttribute('id') || '');
                            var descendants = Array.prototype.slice.call(node.querySelectorAll('a[href], [href], [data-href], [data-url], [data-permalink], [data-item-id], [data-id], [id]'));
                            for (var d = 0; d < descendants.length && d < 80; d += 1) {
                                var child = descendants[d];
                                sources.push(child.href || '');
                                sources.push(child.getAttribute('href') || '');
                                sources.push(child.getAttribute('data-href') || '');
                                sources.push(child.getAttribute('data-url') || '');
                                sources.push(child.getAttribute('data-permalink') || '');
                                sources.push(child.getAttribute('data-item-id') || '');
                                sources.push(child.getAttribute('data-id') || '');
                                sources.push(child.getAttribute('id') || '');
                            }
                        } catch (e) {}
                        var idFound = '';
                        for (var s = 0; s < sources.length; s += 1) {
                            var value = String(sources[s] || '');
                            if (!idFound) idFound = extractItemId(value);
                            if (isProductUrl(value)) return cleanUrl(value);
                        }
                        try {
                            var html = String(node.outerHTML || '').slice(0, 16000)
                                .replace(/\\u002F/g, '/')
                                .replace(/\\\//g, '/')
                                .replace(/&amp;/g, '&');
                            var hrefMatch = html.match(/https?:\\/\\/(?:www\\.)?mercadolivre\\.com\\.br\\/[^"' <>\\s]*?(?:MLB-?\\d{6,}|\\/p\\/MLB\\d+|wid=MLB\\d+|item_id%3AMLB\\d+|item_id:MLB\\d+)[^"' <>\\s]*/i);
                            if (hrefMatch && hrefMatch[0]) return cleanUrl(hrefMatch[0]);
                            if (!idFound) idFound = extractItemId(html);
                        } catch (e) {}
                        return idFound ? urlFromItemId(idFound) : '';
                    };

                    var titleFrom = function (node) {
                        if (!node) return '';
                        var cleanTitle = function (value) {
                            return String(value || '').replace(/\\s+/g, ' ').trim();
                        };
                        var badTitle = function (value) {
                            var text = cleanTitle(value);
                            if (!text) return true;
                            var normalized = text
                                .normalize('NFD')
                                .replace(/[\\u0300-\\u036f]/g, '')
                                .toLowerCase()
                                .replace(/\\s+/g, ' ')
                                .trim();
                            if (/^(novo|usado|resultados|patrocinado|mais vendido|loja oficial)$/i.test(text)) return true;
                            if (/^(r\\$|frete|chegar|vendid[oa]s?|mercadolider|mercado lider|op[cÃ§][oÃµ]es de compra|produto relacionado)/i.test(normalized)) return true;
                            if (/^(pecas de|lubrificantes|acessorios|categorias|condicao|tipo de envio|custo de envio|tempo de entrega)/i.test(normalized)) return true;
                            var words = text.split(/\\s+/).filter(Boolean);
                            var hasDigit = /\\d/.test(text);
                            var hasLower = /[a-zÃ¡Ã©Ã­Ã³ÃºÃ¢ÃªÃ´Ã£ÃµÃ§]/.test(text);
                            if (!hasDigit && !hasLower && words.length <= 3) return true;
                            return text.length < 10;
                        };
                        var candidates = [];
                        if (node.querySelectorAll) {
                            var selectors = [
                                'a.poly-component__title',
                                '.poly-component__title',
                                'h2.poly-component__title-wrapper a',
                                'h3.poly-component__title-wrapper a',
                                'a.ui-search-link',
                                '.ui-search-item__title',
                                '[class*="ui-search-item__title"]',
                                'a[href*="/MLB-"][title]',
                                'a[href*="/p/MLB"][title]',
                                'a[href*="wid=MLB"][title]'
                            ];
                            selectors.forEach(function (selector) {
                                Array.prototype.slice.call(node.querySelectorAll(selector)).forEach(function (el) {
                                    candidates.push(el.textContent || '');
                                    candidates.push(el.getAttribute && el.getAttribute('title') || '');
                                    candidates.push(el.getAttribute && el.getAttribute('aria-label') || '');
                                });
                            });
                        }
                        if (node.textContent) {
                            candidates = candidates.concat(String(node.textContent).split('\\n'));
                        }
                        for (var i = 0; i < candidates.length; i += 1) {
                            var txt = cleanTitle(candidates[i]);
                            if (!badTitle(txt)) return txt.slice(0, 240);
                        }
                        return '';
                    };
                    var normalizeListingTitle = function (value) {
                        return String(value || '')
                            .normalize('NFD')
                            .replace(/[\\u0300-\\u036f]/g, '')
                            .toLowerCase()
                            .replace(/\\s+/g, ' ')
                            .trim();
                    };
                    var shouldSkipListingCandidate = function (node, title) {
                        var normalized = normalizeListingTitle(title);
                        if (!normalized) return true;
                        if (normalized === 'resultados') return true;
                        var categoryTitles = {
                            'pecas de motos e quadriciclos': true,
                            'lubrificantes e fluidos': true,
                            'pecas de linha pesada': true,
                            'pecas de carros e caminhonetes': true,
                            'acessorios de motos e quadriciclos': true,
                            'categorias': true,
                            'condicao': true,
                            'tipo de envio': true,
                            'custo de envio': true,
                            'tempo de entrega': true
                        };
                        if (!categoryTitles[normalized]) return false;
                        var text = String(node && (node.innerText || node.textContent) ? (node.innerText || node.textContent) : '');
                        return !/(R\\$|vendid[oa]s?|frete\\s+gr[aÃ¡]tis|avantpro|carregar\\s+dados)/i.test(text);
                    };
                    var parseHumanNumber = function (value, suffix) {
                        if (value === null || value === undefined) return null;
                        var raw = String(value).trim().toLowerCase();
                        if (!raw) return null;
                        var normalized = raw.replace(/\\s+/g, '');
                        if (normalized.indexOf('.') >= 0 && normalized.indexOf(',') >= 0) {
                            normalized = normalized.lastIndexOf('.') > normalized.lastIndexOf(',')
                                ? normalized.replace(/,/g, '')
                                : normalized.replace(/\\./g, '').replace(/,/g, '.');
                        } else if (normalized.indexOf(',') >= 0) {
                            normalized = /^\\d{1,3}(?:,\\d{3})+$/.test(normalized)
                                ? normalized.replace(/,/g, '')
                                : normalized.replace(/,/g, '.');
                        } else if (normalized.indexOf('.') >= 0) {
                            normalized = /^\\d{1,3}(?:\\.\\d{3})+$/.test(normalized)
                                ? normalized.replace(/\\./g, '')
                                : normalized;
                        }
                        var parsed = parseFloat(normalized);
                        if (!isFinite(parsed)) return null;
                        suffix = String(suffix || '').toLowerCase();
                        if (suffix === 'mil' || suffix === 'k') parsed *= 1000;
                        return Math.round(parsed);
                    };
                    var looksLikeAvantText = function (text) {
                        return /an[uÃº]ncio\\s+(?:ganhador\\s+)?criado\\s+em|cat[aÃ¡]logo\\s+criado\\s+em|nome\\s+do\\s+vendedor|vendid[oa]s?|\\bvendas\\b|faturamento\\s+do\\s+produto|reputa[cÃ§][aÃ£]o\\s+do\\s+vendedor/i.test(String(text || ''));
                    };
                    var normalizeAvantSearchText = function (value) {
                        return String(value || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase();
                    };
                    looksLikeAvantText = function (text) {
                        var value = normalizeAvantSearchText(text);
                        return /anuncio\\s+(?:ganhador\\s+)?criado\\s+em|catalogo\\s+criado\\s+em|nome\\s+do\\s+vendedor|vendas\\s+do\\s+(?:produto|catalogo|anuncio)|faturamento\\s+do\\s+produto|reputacao\\s+do\\s+vendedor/i.test(value);
                    };
                    var getNearbyInfoNodes = function (node) {
                        var found = [];
                        try {
                            var rect = node.getBoundingClientRect();
                            var candidates = Array.prototype.slice.call(document.querySelectorAll('[class*="avant"], [id*="avant"], .created-time-card, .avantpro-product-info-row, .avantpro-product-info-row *'));
                            var seenNodes = [];
                            for (var nb = 0; nb < candidates.length; nb += 1) {
                                var candidate = candidates[nb];
                                if (!candidate || candidate === node || node.contains(candidate)) continue;
                                var text = String(candidate.innerText || candidate.textContent || '').replace(/\\s+/g, ' ').trim();
                                if (!text || text.length > 1400 || !looksLikeAvantText(text)) continue;
                                var nr = candidate.getBoundingClientRect();
                                if (!nr.width || !nr.height) continue;
                                var overlapX = Math.max(0, Math.min(rect.right, nr.right) - Math.max(rect.left, nr.left));
                                var cardCenterY = (rect.top + rect.bottom) / 2;
                                var nodeCenterY = (nr.top + nr.bottom) / 2;
                                var nearY = Math.abs(nodeCenterY - cardCenterY) < Math.max(360, rect.height * 0.9);
                                var sameColumn = overlapX > Math.max(20, Math.min(rect.width, nr.width) * 0.12);
                                var cardContainsOverlay = nr.left >= rect.left - 30 && nr.right <= rect.right + 30 && nr.top >= rect.top - 80 && nr.top <= rect.bottom + 180;
                                if ((sameColumn && nearY) || cardContainsOverlay) {
                                    var duplicate = seenNodes.some(function (existing) { return existing.contains(candidate); });
                                    if (duplicate) continue;
                                    seenNodes.push(candidate);
                                    found.push(text);
                                }
                            }
                        } catch (e) {}
                        return found;
                    };
                    var textWithNearbyAvant = function (node) {
                        var parts = [String(node && node.innerText ? node.innerText : '')];
                        try {
                            parts = parts.concat(getNearbyInfoNodes(node));
                        } catch (e) {}
                        return parts.join(' ').replace(/\\s+/g, ' ').trim();
                    };
                    var normalizeAvantLabel = function (value) {
                        return String(value || '')
                            .normalize('NFD')
                            .replace(/[\\u0300-\\u036f]/g, '')
                            .toLowerCase()
                            .replace(/\\s+/g, ' ')
                            .trim();
                    };
                    var isNearAvantCard = function (card, row) {
                        try {
                            var rect = card.getBoundingClientRect();
                            var rr = row.getBoundingClientRect();
                            if (!rr.width || !rr.height) return false;
                            var overlapX = Math.max(0, Math.min(rect.right, rr.right) - Math.max(rect.left, rr.left));
                            var cardCenterY = (rect.top + rect.bottom) / 2;
                            var rowCenterY = (rr.top + rr.bottom) / 2;
                            var nearY = Math.abs(rowCenterY - cardCenterY) < Math.max(180, rect.height * 0.55);
                            var sameColumn = overlapX > Math.max(20, Math.min(rect.width, rr.width) * 0.12);
                            var insideOverlay = rr.left >= rect.left - 45 && rr.right <= rect.right + 45 && rr.top >= rect.top - 40 && rr.top <= rect.bottom + 180;
                            return (sameColumn && nearY) || insideOverlay;
                        } catch (e) {
                            return false;
                        }
                    };
                    var extractAvantRowValue = function (card, labels) {
                        var wanted = labels.map(normalizeAvantLabel);
                        var rows = Array.prototype.slice.call(document.querySelectorAll('.avantpro-product-info-row'));
                        for (var r = 0; r < rows.length; r += 1) {
                            var row = rows[r];
                            if (!isNearAvantCard(card, row)) continue;
                            var labelNode = row.querySelector('.avantpro-product-info-row-label');
                            var valueNode = row.querySelector('.avantpro-product-info-row-value');
                            var label = normalizeAvantLabel(labelNode && labelNode.textContent);
                            if (!label || wanted.indexOf(label) < 0 || !valueNode) continue;
                            return String(valueNode.textContent || '').replace(/\\s+/g, ' ').trim();
                        }
                        return '';
                    };
                    var extractAvantRowInfo = function (card, labels) {
                        var wanted = labels.map(normalizeAvantLabel);
                        var rows = Array.prototype.slice.call(document.querySelectorAll('.avantpro-product-info-row'));
                        for (var r = 0; r < rows.length; r += 1) {
                            var row = rows[r];
                            if (!isNearAvantCard(card, row)) continue;
                            var labelNode = row.querySelector('.avantpro-product-info-row-label');
                            var valueNode = row.querySelector('.avantpro-product-info-row-value');
                            var label = normalizeAvantLabel(labelNode && labelNode.textContent);
                            if (!label || wanted.indexOf(label) < 0 || !valueNode) continue;
                            return {
                                label: label,
                                value: String(valueNode.textContent || '').replace(/\\s+/g, ' ').trim()
                            };
                        }
                        return null;
                    };
                    var sanitizeAvantSeller = function (value) {
                        var seller = String(value || '').replace(/\s+/g, ' ').trim();
                        seller = seller.replace(/^(?:nome\s+do\s+vendedor|vendedor|loja\s+oficial|vendido\s+por|atual\s+ganhador|ganhador)\s*[:\-]?\s*/i, '').trim();
                        if (!seller || seller.length > 120 || /^\d+$/.test(seller)) return '';
                        if (/^(?:sim|nao|n[aÃ£]o|nao\s+informado|n[aÃ£]o\s+informado|carregando|indisponivel|indispon[iÃ­]vel|assinantes?)$/i.test(seller)) return '';
                        return seller;
                    };
                    var extractSeller = function (node) {
                        var exactSeller = extractAvantRowValue(node, [
                            'Nome do vendedor',
                            'Vendedor',
                            'Vendedor do anuncio',
                            'Vendedor do anÃºncio',
                            'Nome do vendedor ganhador',
                            'Vendedor ganhador',
                            'Loja oficial'
                        ]);
                        return sanitizeAvantSeller(exactSeller);
                    };
                    var extractAvantDate = function (node) {
                        if (fastLinksOnly) return '';
                        var exactDate = extractAvantRowValue(node, ['AnÃºncio criado em', 'Anuncio criado em', 'AnÃºncio ganhador criado em', 'Anuncio ganhador criado em']);
                        if (exactDate) {
                            var exactBr = exactDate.match(/\\b\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}\\b/);
                            if (exactBr && exactBr[0]) return exactBr[0];
                            var exactIso = exactDate.match(/\\b20\\d{2}-\\d{2}-\\d{2}(?:T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?(?:Z|[+-]\\d{2}:?\\d{2})?)?\\b/);
                            if (exactIso && exactIso[0]) return exactIso[0];
                        }
                        var text = textWithNearbyAvant(node);
                        var labels = [
                            'AnÃºncio criado em',
                            'Anuncio criado em',
                            'AnÃºncio ganhador criado em',
                            'Anuncio ganhador criado em',
                            'CatÃ¡logo criado em',
                            'Catalogo criado em',
                            'Criado em'
                        ];
                        for (var l = 0; l < labels.length; l += 1) {
                            var idx = text.toLowerCase().indexOf(labels[l].toLowerCase());
                            if (idx < 0) continue;
                            var trecho = text.slice(idx + labels[l].length, idx + labels[l].length + 120);
                            var br = trecho.match(/\\b\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}\\b/);
                            if (br && br[0]) return br[0];
                            var iso = trecho.match(/\\b20\\d{2}-\\d{2}-\\d{2}(?:T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?(?:Z|[+-]\\d{2}:?\\d{2})?)?\\b/);
                            if (iso && iso[0]) return iso[0];
                        }
                        return '';
                    };
                    var extractVendas = function (node) {
                        if (fastLinksOnly) return null;
                        var labelEhVendasAnuncio = function (value) {
                            var label = normalizeAvantLabel(value).replace(/[:=\\-]+$/g, '').trim();
                            return (
                                /^vendas\\s+do\\s+(?:anuncio|item)(?:\\s+ganhador)?\\b/.test(label) ||
                                /^vendas\\s+do\\s+produto\\b/.test(label) ||
                                /^vendas\\s+deste\\s+anuncio\\b/.test(label) ||
                                /^vendas\\s+do\\s+vendedor\\s+neste\\s+anuncio\\b/.test(label)
                            );
                        };
                        var parseValorVendas = function (value) {
                            var match = String(value || '').match(/(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i);
                            return match && match[1] ? parseHumanNumber(match[1], match[2]) : null;
                        };
                        var scoreLinhaVendas = function (row) {
                            try {
                                var rect = node.getBoundingClientRect();
                                var rr = row.getBoundingClientRect();
                                if (!rr.width || !rr.height) return null;
                                var overlapX = Math.max(0, Math.min(rect.right, rr.right) - Math.max(rect.left, rr.left));
                                var cardCenterY = (rect.top + rect.bottom) / 2;
                                var rowCenterY = (rr.top + rr.bottom) / 2;
                                var distanceY = Math.abs(rowCenterY - cardCenterY);
                                if (isNearAvantCard(node, row)) return distanceY - Math.min(overlapX, rect.width) * 0.05;
                                var alignedX = overlapX > Math.max(14, Math.min(rect.width, rr.width) * 0.08)
                                    || (rr.left <= rect.right + 140 && rr.right >= rect.left - 140);
                                var closeY = rr.top >= rect.top - 80 && rr.top <= rect.bottom + Math.max(420, rect.height * 1.5);
                                if (!alignedX || !closeY || distanceY > Math.max(520, rect.height * 1.9)) return null;
                                return 1000 + distanceY - Math.min(overlapX, rect.width) * 0.04;
                            } catch (e) {
                                return null;
                            }
                        };
                        var rows = Array.prototype.slice.call(document.querySelectorAll('.avantpro-product-info-row'));
                        var melhor = null;
                        for (var r = 0; r < rows.length; r += 1) {
                            var row = rows[r];
                            var score = scoreLinhaVendas(row);
                            if (!Number.isFinite(score)) continue;
                            var labelNode = row.querySelector('.avantpro-product-info-row-label');
                            var valueNode = row.querySelector('.avantpro-product-info-row-value');
                            if (!valueNode || !labelEhVendasAnuncio(labelNode && labelNode.textContent)) continue;
                            var valor = parseValorVendas(valueNode.textContent);
                            if (Number.isFinite(valor) && (!melhor || score < melhor.score)) {
                                melhor = { valor: valor, score: score };
                            }
                        }
                        return melhor ? melhor.valor : null;
                    };
                    var extractImage = function (node) {
                        try {
                            var imgs = Array.prototype.slice.call(node.querySelectorAll('img'));
                            for (var imgIndex = 0; imgIndex < imgs.length; imgIndex += 1) {
                                var img = imgs[imgIndex];
                                var src = img.currentSrc
                                    || img.getAttribute('data-src')
                                    || img.getAttribute('data-original')
                                    || img.getAttribute('data-lazy')
                                    || img.getAttribute('src')
                                    || '';
                                src = String(src || '').trim();
                                if (!src || /^data:/i.test(src) || /sprite|logo|placeholder/i.test(src)) continue;
                                if (src.indexOf('//') === 0) return 'https:' + src;
                                if (/^https?:\\/\\//i.test(src)) return src;
                            }
                        } catch (e) {}
                        return '';
                    };
                    var parseMoneyValue = function (value) {
                        var raw = String(value || '').replace(/\\s+/g, ' ').trim();
                        if (!raw) return null;
                        var ariaReais = raw.match(/(\\d[\\d\\.]*)\\s*reais?(?:\\s*(?:e|,)?\\s*(\\d{1,2})\\s*centavos?)?/i);
                        if (ariaReais && ariaReais[1]) {
                            var reais = Number(String(ariaReais[1]).replace(/\\./g, ''));
                            var cents = ariaReais[2] ? Number(ariaReais[2]) : 0;
                            if (Number.isFinite(reais)) return reais + (Number.isFinite(cents) ? cents / 100 : 0);
                        }
                        var match = raw.replace(/R\\$\\s*/gi, '').replace(/\\s+/g, '').match(/\\d[\\d\\.,]*/);
                        if (!match) return null;
                        var normalized = match[0];
                        if (normalized.indexOf('.') >= 0 && normalized.indexOf(',') >= 0) {
                            normalized = normalized.lastIndexOf('.') > normalized.lastIndexOf(',')
                                ? normalized.replace(/,/g, '')
                                : normalized.replace(/\\./g, '').replace(/,/g, '.');
                        } else if (normalized.indexOf(',') >= 0) {
                            normalized = normalized.replace(/\\./g, '').replace(/,/g, '.');
                        }
                        var parsed = Number(normalized);
                        return Number.isFinite(parsed) ? parsed : null;
                    };
                    var readMoneyAmount = function (el) {
                        if (!el) return null;
                        var aria = el.getAttribute && (el.getAttribute('aria-label') || el.getAttribute('title'));
                        var ariaValue = parseMoneyValue(aria);
                        if (Number.isFinite(ariaValue)) return ariaValue;
                        var fraction = el.querySelector && el.querySelector('.andes-money-amount__fraction');
                        var cents = el.querySelector && el.querySelector('.andes-money-amount__cents, .andes-money-amount__cents-superscript');
                        if (fraction && String(fraction.textContent || '').trim()) {
                            var composed = String(fraction.textContent || '').trim();
                            if (cents && String(cents.textContent || '').trim()) composed += ',' + String(cents.textContent || '').trim();
                            var composedValue = parseMoneyValue(composed);
                            if (Number.isFinite(composedValue)) return composedValue;
                        }
                        return parseMoneyValue(el.textContent || '');
                    };
                    var extractPrice = function (node) {
                        var result = { preco: null, preco_original: null, preco_promocional: null };
                        try {
                            var amounts = Array.prototype.slice.call(node.querySelectorAll('.andes-money-amount, [class*="money-amount"], [class*="price-tag"]'));
                            for (var ai = 0; ai < amounts.length; ai += 1) {
                                var amountEl = amounts[ai];
                                var value = readMoneyAmount(amountEl);
                                if (!Number.isFinite(value)) continue;
                                var textContext = normalizeListingTitle(String((amountEl.className || '') + ' ' + (amountEl.closest && amountEl.closest('s, del, [class*="previous"], [class*="original"], [class*="old"], [class*="strike"]') ? ' previous' : '') + ' ' + (amountEl.parentElement && amountEl.parentElement.className || '')));
                                var isOriginal = /previous|original|old|strike|tachado|riscado/.test(textContext) || !!(amountEl.closest && amountEl.closest('s, del'));
                                if (isOriginal && result.preco_original === null) {
                                    result.preco_original = value;
                                } else if (!isOriginal && result.preco === null) {
                                    result.preco = value;
                                }
                            }
                            if (result.preco_original !== null && result.preco !== null && result.preco_original > result.preco) {
                                result.preco_promocional = result.preco;
                            }
                        } catch (e) {}
                        return result;
                    };
                    var extractParcelamentoSemJuros = function (node) {
                        try {
                            var text = String(node && (node.innerText || node.textContent) ? (node.innerText || node.textContent) : '')
                                .normalize('NFD')
                                .replace(/[\\u0300-\\u036f]/g, '')
                                .toLowerCase()
                                .replace(/\\s+/g, ' ')
                                .trim();
                            return /\\bsem\\s+juros\\b|\\b0\\s*%?\\s*de?\\s*juros\\b/.test(text);
                        } catch (e) {
                            return false;
                        }
                    };
                    var extractFull = function (node) {
                        try {
                            if (!node || !node.querySelectorAll) return false;
                            var attrs = Array.prototype.slice.call(node.querySelectorAll('[aria-label], [title], img[alt], [class], [data-testid], [data-full]'));
                            for (var fi = 0; fi < attrs.length; fi += 1) {
                                var el = attrs[fi];
                                var texto = String([
                                    el.getAttribute && el.getAttribute('aria-label'),
                                    el.getAttribute && el.getAttribute('title'),
                                    el.getAttribute && el.getAttribute('alt'),
                                    el.getAttribute && el.getAttribute('class'),
                                    el.getAttribute && el.getAttribute('data-testid'),
                                    el.getAttribute && el.getAttribute('data-full')
                                ].filter(Boolean).join(' '))
                                    .normalize('NFD')
                                    .replace(/[\\u0300-\\u036f]/g, '')
                                    .toLowerCase()
                                    .replace(/\\s+/g, ' ')
                                    .trim();
                                if (/\\bfull\\b|fulfillment/.test(texto)) return true;
                            }
                        } catch (e) {}
                        return false;
                    };

                    var out = [];
                    var seen = {};
                    var cards = Array.prototype.slice.call(document.querySelectorAll(selectors));
                    for (var i = 0; i < cards.length; i += 1) {
                        var card = cards[i];
                        var href = findProductHrefInNode(card);
                        if (!href || seen[href]) continue;
                        var idCard = extractItemIdFromNode(card, href);
                        var titleCard = titleFrom(card);
                        if (!idCard || shouldSkipListingCandidate(card, titleCard)) continue;
                        seen[href] = true;
                        var vendasCard = extractVendas(card);
                        var vendedorCard = extractSeller(card);
                        var imagemCard = extractImage(card);
                        var precoCard = extractPrice(card);
                        var semJurosCard = extractParcelamentoSemJuros(card);
                        var fullCard = extractFull(card);
                        out.push({ posicao: out.length + 1, id: idCard, url: href, titulo: titleCard, imagem: imagemCard, thumbnail: imagemCard, preco: precoCard.preco, price: precoCard.preco, preco_original: precoCard.preco_original, original_price: precoCard.preco_original, preco_promocional: precoCard.preco_promocional, parcelamento_sem_juros: semJurosCard, tipo_anuncio: semJurosCard ? 'Premium' : 'Classico', is_full: fullCard ? true : '', full: fullCard ? true : '', vendedor: vendedorCard, vendedorFonte: vendedorCard ? 'avantpro_vendedor' : '', vendedor_fonte: vendedorCard ? 'avantpro_vendedor' : '', data_criacao: extractAvantDate(card), vendas: vendasCard, vendasFonte: vendasCard !== null && vendasCard !== undefined ? 'avantpro_anuncio' : '' });
                    }

                    if (!out.length) {
                        var links = Array.prototype.slice.call(document.links || []);
                        for (var k = 0; k < links.length; k += 1) {
                            var linkHref = cleanUrl(links[k].href);
                            if (!isProductUrl(linkHref) || seen[linkHref]) continue;
                            var linkId = extractItemId(linkHref);
                            var linkTitle = (links[k].textContent || links[k].getAttribute('title') || '').trim().slice(0, 240);
                            if (!linkId || shouldSkipListingCandidate(links[k], linkTitle)) continue;
                            seen[linkHref] = true;
                                var linkCard = links[k].closest && (links[k].closest('li, article, section, div.poly-card, div.ui-search-result') || links[k]) || links[k];
                                var precoLink = extractPrice(linkCard);
                                var semJurosLink = extractParcelamentoSemJuros(linkCard);
                                var fullLink = extractFull(linkCard);
                                out.push({ posicao: out.length + 1, id: linkId, url: linkHref, titulo: linkTitle, imagem: extractImage(linkCard), preco: precoLink.preco, price: precoLink.preco, preco_original: precoLink.preco_original, original_price: precoLink.preco_original, preco_promocional: precoLink.preco_promocional, parcelamento_sem_juros: semJurosLink, tipo_anuncio: semJurosLink ? 'Premium' : 'Classico', is_full: fullLink ? true : '', full: fullLink ? true : '' });
                            if (out.length >= 100) break;
                        }
                    }

                    if (!out.length) {
                        var candidates = Array.prototype.slice.call(document.querySelectorAll(selectors + ', a[href*="MLB"], a[href*="/p/MLB"], a[href*="wid=MLB"], a[href*="item_id"], [data-item-id], [data-id*="MLB"]'));
                        for (var c = 0; c < candidates.length && out.length < 100; c += 1) {
                            var node = candidates[c];
                            var hrefFound = findProductHrefInNode(node);
                            if (!hrefFound || seen[hrefFound]) continue;
                            var cardNode = node.closest && (node.closest('li, article, section, div.poly-card, div.ui-search-result, div[class*="poly"], div[class*="search"]') || node);
                            var candidateId = extractItemId(hrefFound);
                            var candidateTitle = titleFrom(cardNode) || String(node.textContent || '').trim().slice(0, 240);
                            if (!candidateId || shouldSkipListingCandidate(cardNode, candidateTitle)) continue;
                            seen[hrefFound] = true;
                            var vendasCardNode = extractVendas(cardNode);
                            var vendedorCardNode = extractSeller(cardNode);
                            var imagemCardNode = extractImage(cardNode);
                            var precoCardNode = extractPrice(cardNode);
                            var semJurosCardNode = extractParcelamentoSemJuros(cardNode);
                            var fullCardNode = extractFull(cardNode);
                            out.push({
                                posicao: out.length + 1,
                                id: candidateId,
                                url: hrefFound,
                                titulo: candidateTitle,
                                imagem: imagemCardNode,
                                thumbnail: imagemCardNode,
                                preco: precoCardNode.preco,
                                price: precoCardNode.preco,
                                preco_original: precoCardNode.preco_original,
                                original_price: precoCardNode.preco_original,
                                preco_promocional: precoCardNode.preco_promocional,
                                parcelamento_sem_juros: semJurosCardNode,
                                tipo_anuncio: semJurosCardNode ? 'Premium' : 'Classico',
                                is_full: fullCardNode ? true : '',
                                full: fullCardNode ? true : '',
                                vendedor: vendedorCardNode,
                                vendedorFonte: vendedorCardNode ? 'avantpro_vendedor' : '',
                                vendedor_fonte: vendedorCardNode ? 'avantpro_vendedor' : '',
                                data_criacao: extractAvantDate(cardNode),
                                vendas: vendasCardNode,
                                vendasFonte: vendasCardNode !== null && vendasCardNode !== undefined ? 'avantpro_anuncio' : ''
                            });
                        }
                    }

                    if (!out.length) {
                        var html = String(document.documentElement && document.documentElement.innerHTML ? document.documentElement.innerHTML : '');
                        var decodedHtml = html
                            .replace(/\\u002F/g, '/')
                            .replace(/\\\//g, '/')
                            .replace(/&amp;/g, '&')
                            .replace(/\\&/g, '&');
                        var productUrlRegex = new RegExp("https?:\\\\/\\\\/(?:www\\\\.)?mercadolivre\\\\.com\\\\.br\\\\/(?:[^\\\"'<>\\\\s]*?(?:MLB-?\\\\d{6,}|\\\\/p\\\\/MLB\\\\d+|wid=MLB\\\\d+|item_id%3AMLB\\\\d+|item_id:MLB\\\\d+)[^\\\"'<>\\\\s]*)", "gi");
                        var matches = decodedHtml.match(productUrlRegex) || [];
                        for (var m = 0; m < matches.length; m += 1) {
                            var matchHref = cleanUrl(matches[m]);
                            if (!isProductUrl(matchHref) || seen[matchHref]) continue;
                            var matchId = extractItemId(matchHref);
                            if (!matchId) continue;
                            seen[matchHref] = true;
                            out.push({ posicao: out.length + 1, id: matchId, url: matchHref, titulo: '' });
                            if (out.length >= 100) break;
                        }
                        if (!out.length) {
                            var idMatches = decodedHtml.match(/\\bMLB-?\\d{6,}\\b/gi) || [];
                            for (var im = 0; im < idMatches.length && out.length < 100; im += 1) {
                                var htmlId = String(idMatches[im] || '').replace('-', '').toUpperCase();
                                var htmlUrl = urlFromItemId(htmlId);
                                if (!htmlUrl || seen[htmlUrl]) continue;
                                seen[htmlUrl] = true;
                                out.push({ posicao: out.length + 1, id: htmlId, url: htmlUrl, titulo: '' });
                            }
                        }
                    }

                    return {
                        success: true,
                        currentUrl: currentUrl,
                        needsLogin: needsLogin,
                        total: out.length,
                        anuncios: out,
                        debug: {
                            linkCount: document.links ? document.links.length : 0,
                            cardCount: cards.length,
                            title: document.title || '',
                            fastLinksOnly: fastLinksOnly
                        }
                    };
                } catch (err) {
                    return { success: false, total: 0, anuncios: [], error: err && (err.stack || err.message) ? String(err.stack || err.message) : String(err) };
                }
            })();
        `;

        function promiseComTimeout(promise, ms, mensagem) {
            let timer = null;
            const timeout = new Promise((_, reject) => {
                timer = setTimeout(() => reject(new Error(mensagem || 'Tempo limite excedido.')), ms);
            });
            return Promise.race([promise, timeout]).finally(() => {
                if (timer) clearTimeout(timer);
            });
        }

        function esperar(ms) {
            return new Promise(resolve => setTimeout(resolve, ms));
        }

        function usarNavegadorMlNoShellElectron() {
            try {
                return !!(window.top && window.top !== window && typeof window.top.postMessage === 'function');
            } catch (_err) {
                return false;
            }
        }

        function obterBoundsNavegadorMl() {
            const alvo = mlBrowserHost || mlBrowserFrameWrapEl;
            if (!alvo || typeof alvo.getBoundingClientRect !== 'function') return null;
            const rect = alvo.getBoundingClientRect();
            const limite = balaoResultadosMlAberto() && mlWorkModalDialogEl && typeof mlWorkModalDialogEl.getBoundingClientRect === 'function'
                ? mlWorkModalDialogEl.getBoundingClientRect()
                : null;
            const viewport = {
                left: 0,
                top: 0,
                right: window.innerWidth || rect.right,
                bottom: window.innerHeight || rect.bottom
            };
            const clip = limite
                ? {
                    left: Math.max(limite.left, viewport.left),
                    top: Math.max(limite.top, viewport.top),
                    right: Math.min(limite.right, viewport.right),
                    bottom: Math.min(limite.bottom, viewport.bottom)
                }
                : viewport;
            const left = Math.max(rect.left, clip.left);
            const top = Math.max(rect.top, clip.top);
            const right = Math.min(rect.right, clip.right);
            const bottom = Math.min(rect.bottom, clip.bottom);
            const width = Math.max(0, right - left);
            const height = Math.max(0, bottom - top);
            if (width < 20 || height < 20) return null;
            return {
                left,
                top,
                width,
                height
            };
        }

        function enviarNavegadorMlParaShell(channel, payload = {}) {
            if (!usarNavegadorMlNoShellElectron()) return;
            try {
                window.top.postMessage({ channel, payload }, '*');
            } catch (_err) {}
        }

        function atualizarPosicaoNavegadorMlShell() {
            if (!mlShellBrowserProxy || !mlShellBrowserProxy.__visible) return;
            const bounds = obterBoundsNavegadorMl();
            if (!bounds) return;
            enviarNavegadorMlParaShell('jk-ml-browser-position', { bounds });
        }

        function ocultarNavegadorMlShellTemporariamente() {
            if (!mlShellBrowserProxy || !mlShellBrowserProxy.__visible) return;
            mlBrowserShellOcultoPorBalao = true;
            enviarNavegadorMlParaShell('jk-ml-browser-hide');
        }

        function restaurarNavegadorMlShellSeVisivel() {
            if (!mlBrowserShellOcultoPorBalao) return;
            mlBrowserShellOcultoPorBalao = false;
            if (!mlShellBrowserProxy || !mlShellBrowserProxy.__visible) return;
            if (!balaoResultadosMlAberto()) return;
            atualizarPosicaoNavegadorMlShell();
        }

        function agendarAtualizacaoPosicaoNavegadorMlShell() {
            if (!mlShellBrowserProxy || !mlShellBrowserProxy.__visible) return;
            if (mlShellBrowserPositionTimer) return;
            mlShellBrowserPositionTimer = setTimeout(() => {
                mlShellBrowserPositionTimer = null;
                atualizarPosicaoNavegadorMlShell();
            }, 80);
        }

        function criarProxyNavegadorMlShell() {
            if (mlShellBrowserProxy) return mlShellBrowserProxy;
            const listeners = new Map();
            const proxy = {
                parentNode: mlBrowserHost,
                __isShellBrowserProxy: true,
                __visible: false,
                currentUrl: '',
                addEventListener(name, handler) {
                    if (!listeners.has(name)) listeners.set(name, new Set());
                    listeners.get(name).add(handler);
                },
                removeEventListener(name, handler) {
                    if (listeners.has(name)) listeners.get(name).delete(handler);
                },
                dispatchEvent(name, event = {}) {
                    const set = listeners.get(name);
                    if (!set) return;
                    set.forEach(handler => {
                        try { handler(event); } catch (err) { console.warn('Falha em listener do navegador ML:', err); }
                    });
                },
                getURL() {
                    return proxy.currentUrl || '';
                },
                executeJavaScript(code) {
                    const requestId = `ml-shell-${Date.now()}-${++mlShellBrowserRequestId}`;
                    return new Promise((resolve, reject) => {
                        mlShellBrowserPending.set(requestId, { resolve, reject });
                        enviarNavegadorMlParaShell('jk-ml-browser-execute', { requestId, code });
                        setTimeout(() => {
                            if (!mlShellBrowserPending.has(requestId)) return;
                            mlShellBrowserPending.delete(requestId);
                            reject(new Error('Tempo limite ao executar script no navegador do Mercado Livre.'));
                        }, 20000);
                    });
                },
                insertCSS(css) {
                    const code = `
                        (function () {
                            var style = document.getElementById('jk-discreet-scrollbar-style');
                            if (!style) {
                                style = document.createElement('style');
                                style.id = 'jk-discreet-scrollbar-style';
                                document.head.appendChild(style);
                            }
                            style.textContent = ${JSON.stringify(css || '')};
                            return true;
                        })();
                    `;
                    return proxy.executeJavaScript(code).catch(() => null);
                }
            };
            Object.defineProperty(proxy, 'src', {
                get() {
                    return proxy.currentUrl;
                },
                set(url) {
                    proxy.currentUrl = String(url || '');
                    proxy.__visible = true;
                    const bounds = obterBoundsNavegadorMl();
                    enviarNavegadorMlParaShell('jk-ml-browser-show', { url: proxy.currentUrl, bounds });
                    setTimeout(atualizarPosicaoNavegadorMlShell, 120);
                }
            });
            mlShellBrowserProxy = proxy;
            return proxy;
        }

        window.addEventListener('message', (event) => {
            const data = event && event.data ? event.data : {};
            if (!data || typeof data !== 'object') return;
            if (data.channel === 'jk-ml-browser-event' && mlShellBrowserProxy) {
                if (data.url) mlShellBrowserProxy.currentUrl = data.url;
                mlShellBrowserProxy.dispatchEvent(data.event, data);
                return;
            }
            if (data.channel === 'jk-ml-browser-execute-result') {
                const pending = mlShellBrowserPending.get(data.requestId);
                if (!pending) return;
                mlShellBrowserPending.delete(data.requestId);
                if (data.error) pending.reject(new Error(data.error));
                else pending.resolve(data.result);
            }
        });

        window.addEventListener('resize', agendarAtualizacaoPosicaoNavegadorMlShell);
        window.addEventListener('scroll', agendarAtualizacaoPosicaoNavegadorMlShell, true);
        if (mlWorkModalCloseEl) {
            mlWorkModalCloseEl.addEventListener('click', fecharBalaoResultadosMl);
        }
        if (mlWorkModalCancelEl) {
            mlWorkModalCancelEl.addEventListener('click', cancelarFavoritosEmExecucao);
        }

        async function extrairAnunciosWebviewVisivel(opcoes = {}) {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') {
                if (viaElectron && temInformacao(viaElectron)) {
                    consultarItemApiMercadoLivre.cache.set(itemId, viaElectron);
                    return viaElectron;
                }
                if (viaElectron && temInformacao(viaElectron)) {
                    consultarItemApiMercadoLivre.cache.set(itemId, viaElectron);
                    return viaElectron;
                }
                return null;
            }
            if (opcoes.clicarAvant) {
                await mlWebviewEl.executeJavaScript(`
                    (function () {
                        var clicked = 0;
                        var forceClick = ${opcoes.forcarCliqueAvant ? 'true' : 'false'};
                        var nodes = Array.prototype.slice.call(document.querySelectorAll('button, [role="button"], a'));
                        nodes.forEach(function (node) {
                            var text = String(node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim();
                            if (!text || (!forceClick && node.dataset.jkAvantClicked === '1')) return;
                            if (/carregar\\s+dados\\s+avant|informa[cÃ§][oÃµ]es\\s+avant/i.test(text)) {
                                node.dataset.jkAvantClicked = '1';
                                window.__JK_AVANT_PRO_CLICKED_AT = Date.now();
                                try { node.click(); clicked += 1; } catch (e) {}
                            }
                        });
                        return clicked;
                    })();
                `, true).catch(() => 0);
                tentarLoginAvantProNoWebview(mlWebviewEl);
                await esperar(opcoes.aguardarAposCliqueAvant || 180);
            }
            await mlWebviewEl.executeJavaScript(`window.__JK_ML_FAST_LINKS = ${opcoes.fastLinks ? 'true' : 'false'};`, true).catch(() => null);
            return await promiseComTimeout(
                mlWebviewEl.executeJavaScript(ML_WEBVIEW_EXTRACT_SCRIPT, true),
                opcoes.timeoutMs || 6000,
                'Tempo limite ao extrair os links do quadro interno.'
            );
        }
        function mesclarAnunciosAvant(destino, origem) {
            const mapa = new Map();
            const chave = (item) => {
                const id = item && (item.id || extrairItemIdAnuncio(item.url));
                if (id) return `id:${String(id).toUpperCase()}`;
                if (item && item.url) return `url:${String(item.url).split('#')[0]}`;
                if (item && item.titulo) return `titulo:${normalizarTextoMl(item.titulo)}`;
                return '';
            };

            (destino || []).forEach(item => {
                const key = chave(item);
                if (key) mapa.set(key, item);
            });

            (origem || []).forEach(item => {
                const key = chave(item);
                if (!key) return;
                const atual = mapa.get(key);
                if (!atual) {
                    mapa.set(key, { ...item });
                    return;
                }

                const vendedorOrigem = normalizarNomeVendedor(item && item.vendedor || '');
                const vendedorAtual = normalizarNomeVendedor(atual && atual.vendedor || '');
                const fonteOrigemVendedor = item && (item.vendedorFonte || item.vendedor_fonte || '');
                const fonteAtualVendedor = atual && (atual.vendedorFonte || atual.vendedor_fonte || '');
                const usarVendedorOrigem = deveAtualizarVendedor(vendedorAtual, fonteAtualVendedor, vendedorOrigem, fonteOrigemVendedor);
                const vendedorCombinado = usarVendedorOrigem ? vendedorOrigem : (vendedorValido(vendedorAtual) ? vendedorAtual : '');
                const vendedorFonteCombinada = usarVendedorOrigem ? fonteOrigemVendedor : fonteAtualVendedor;
                const dataCombinada = (item && item.data_criacao) || (atual && atual.data_criacao) || '';
                const fonteItemVendas = item && (item.vendasFonte || item.vendas_fonte || '');
                const fonteAtualVendas = atual && (atual.vendasFonte || atual.vendas_fonte || '');
                const vendasItem = parseNumeroVendas(item && item.vendas);
                const vendasAtual = parseNumeroVendas(atual && atual.vendas);
                const usarVendasItem = deveAtualizarVendas(vendasAtual, fonteAtualVendas, vendasItem, fonteItemVendas);
                const vendasCombinadas = usarVendasItem
                    ? vendasItem
                    : (fonteVendasAvantPro(fonteAtualVendas) && hasNumeroVendas(vendasAtual) ? vendasAtual : null);
                const vendasFonteCombinada = usarVendasItem
                    ? normalizarFonte(fonteItemVendas)
                    : (vendasCombinadas !== null ? normalizarFonte(fonteAtualVendas) : '');

                mapa.set(key, {
                    ...atual,
                    ...item,
                    vendedor: vendedorCombinado,
                    vendedorFonte: vendedorFonteCombinada,
                    data_criacao: dataCombinada,
                    vendas: vendasCombinadas,
                    vendasFonte: vendasFonteCombinada,
                    vendas_fonte: vendasFonteCombinada
                });
            });

            return Array.from(mapa.values());
        }

        async function coletarDadosAvantComRolagem() {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') return [];

            const metrica = await mlWebviewEl.executeJavaScript(`
                (function () {
                    var doc = document.scrollingElement || document.documentElement || document.body;
                    return {
                        y: window.scrollY || doc.scrollTop || 0,
                        height: Math.max(doc.scrollHeight || 0, document.body ? document.body.scrollHeight : 0),
                        view: window.innerHeight || doc.clientHeight || 800
                    };
                })();
            `, true).catch(() => ({ y: 0, height: 0, view: 800 }));

            const altura = Number(metrica && metrica.height) || 0;
            const viewport = Math.max(500, Number(metrica && metrica.view) || 800);
            const posicoes = [];
            const passo = Math.max(620, Math.floor(viewport * 0.95));
            const limite = Math.min(altura || viewport, viewport * 5);
            for (let y = 0; y <= limite; y += passo) posicoes.push(y);
            if (altura > 0) posicoes.push(Math.max(0, altura - viewport - 20));

            let coletados = [];
            for (const y of Array.from(new Set(posicoes)).slice(0, 6)) {
                const destino = Math.max(0, Math.floor(y));
                await mlWebviewEl.executeJavaScript(`
                    (function () {
                        var doc = document.scrollingElement || document.documentElement || document.body;
                        window.scrollTo(0, ${destino});
                        if (doc) doc.scrollTop = ${destino};
                    })();
                `, true).catch(() => null);
                await esperar(260);
                const parcial = await extrairAnunciosWebviewVisivel({ clicarAvant: true, timeoutMs: 2500 }).catch(() => null);
                coletados = mesclarAnunciosAvant(coletados, (parcial && parcial.anuncios) || []);
            }

            const originalY = Math.max(0, Math.floor(Number(metrica && metrica.y) || 0));
            await mlWebviewEl.executeJavaScript(`
                (function () {
                    var doc = document.scrollingElement || document.documentElement || document.body;
                    window.scrollTo(0, ${originalY});
                    if (doc) doc.scrollTop = ${originalY};
                })();
            `, true).catch(() => null);

            return coletados;
        }

        async function extrairDadosAvantDoWebviewVisivel(anuncio) {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') return null;
            const resultado = await extrairAnunciosWebviewVisivel({ clicarAvant: true, timeoutMs: 5000 });
            const anuncios = (resultado && resultado.anuncios) || [];
            return encontrarAnuncioAvantCorrespondente(anuncio, anuncios);
        }

        function normalizarTextoMl(value) {
            return String(value || '')
                .normalize('NFD')
                .replace(/[\u0300-\u036f]/g, '')
                .toLowerCase()
                .replace(/[^a-z0-9]+/g, ' ')
                .replace(/\s+/g, ' ')
                .trim();
        }

        function normalizarSkuBuscaMl(value) {
            return String(value || '')
                .normalize('NFD')
                .replace(/[\u0300-\u036f]/g, '')
                .replace(/[^a-zA-Z0-9]+/g, '')
                .toUpperCase()
                .trim();
        }

        function normalizarNomeVendedor(value) {
            const texto = String(value || '')
                .normalize('NFD')
                .replace(/[\u0300-\u036f]/g, '')
                .replace(/^(vendido\s+por|loja\s+oficial|oficial\s+loja)\s*/i, '')
                .replace(/&quot;|\\\"/g, '"')
                .replace(/\s+/g, ' ')
                .trim();
            return texto;
        }

        function normalizarNomeVendedorParaBusca(value) {
            return normalizarNomeVendedor(value)
                .toLowerCase()
                .replace(/[^a-z0-9]+/g, ' ')
                .replace(/\s+/g, ' ')
                .trim();
        }

        function vendedorValido(valor) {
            const texto = normalizarNomeVendedor(valor);
            if (!texto) return false;
            if (texto.length < 2 || texto.length > 120) return false;
            if (!/[A-Za-z0-9]/.test(texto)) return false;
            if (/^\d+$/.test(texto)) return false;

            const textoBusca = normalizarNomeVendedorParaBusca(texto);
            if (!textoBusca) return false;
            if (textoBusca.length < 2) return false;
            if (/(^|\b)(anuncio criado|an ncio criado|criado em|catalogo criado|cat logo criado|vendas produto|total vendas|quantidade vendas)(\b|$)/i.test(textoBusca)) return false;
            if (/^(vendido|vendedor|anuncio|anunc[iÃ­]o|produto|frete|envio|loja|oferta|ofertas|desconto|comprar|comprando|login|entrar|cadastro|email|senha|contato|perfil|busca|filtro|categoria|condi[cÃ§][aÃ£]o|aviso|informa[cÃ§][aÃ£]o|cria[cÃ§][aÃ£]o|valor|pre[cÃ§]o)$/i.test(textoBusca)) return false;
            return true;
        }

        function scoreNomeVendedor(valor) {
            if (!vendedorValido(valor)) return -1;
            const normalizado = normalizarNomeVendedorParaBusca(valor);
            let score = normalizado.length;
            if (/\s/.test(normalizado)) score += 6;
            if (normalizado.length > 20) score += 5;
            return score;
        }

        function escolherNomeVendedor(candidatos) {
            const itens = Array.isArray(candidatos) ? candidatos : [];
            const opcoes = [];

            for (let i = 0; i < itens.length; i += 1) {
                const item = itens[i];
                const valor = typeof item === 'string' ? item : item && item.valor;
                const prioridade = Number(item && item.prioridade) || 0;
                const nome = normalizarNomeVendedor(valor);

                if (!nome || !vendedorValido(nome)) continue;
                opcoes.push({
                    nome,
                    prioridade,
                    score: scoreNomeVendedor(nome),
                    ordem: i
                });
            }

            if (!opcoes.length) return '';
            opcoes.sort((a, b) => b.prioridade - a.prioridade || b.score - a.score || a.ordem - b.ordem);
            return opcoes[0].nome;
        }

        function carregarVendedoresIgnoradosRanking() {
            try {
                const bruto = window.localStorage ? window.localStorage.getItem(ML_VENDEDORES_IGNORADOS_RANKING_KEY) : null;
                const lista = bruto ? JSON.parse(bruto) : [];
                return normalizarListaVendedoresIgnoradosRanking(lista);
            } catch (err) {
                console.warn('NÃ£o foi possÃ­vel carregar vendedores ignorados do ranking:', err);
                return [];
            }
        }

        function obterVendedoresIgnoradosRanking() {
            if (!Array.isArray(mlVendedoresIgnoradosRanking)) {
                mlVendedoresIgnoradosRanking = carregarVendedoresIgnoradosRanking();
            }
            return mlVendedoresIgnoradosRanking;
        }

        function normalizarListaVendedoresIgnoradosRanking(lista) {
            const mapa = new Map();
            (Array.isArray(lista) ? lista : []).forEach(valor => {
                const nome = normalizarNomeVendedor(valor);
                const chave = normalizarNomeVendedorParaBusca(nome);
                if (nome && vendedorValido(nome) && chave && !mapa.has(chave)) {
                    mapa.set(chave, nome);
                }
            });
            return Array.from(mapa.values()).sort((a, b) => a.localeCompare(b, 'pt-BR'));
        }

        function mesclarListasVendedoresIgnoradosRanking(...listas) {
            return normalizarListaVendedoresIgnoradosRanking(
                listas.flatMap(lista => Array.isArray(lista) ? lista : [])
            );
        }

        function salvarVendedoresIgnoradosRankingLocal() {
            try {
                if (window.localStorage) {
                    window.localStorage.setItem(
                        ML_VENDEDORES_IGNORADOS_RANKING_KEY,
                        JSON.stringify(obterVendedoresIgnoradosRanking())
                    );
                }
            } catch (err) {
                console.warn('NÃ£o foi possÃ­vel salvar vendedores ignorados do ranking:', err);
            }
        }

        function salvarVendedoresIgnoradosRanking() {
            mlVendedoresIgnoradosRanking = normalizarListaVendedoresIgnoradosRanking(obterVendedoresIgnoradosRanking());
            salvarVendedoresIgnoradosRankingLocal();
            agendarSalvarVendedoresIgnoradosRankingServidor();
        }

        function aplicarVendedoresIgnoradosRanking(lista) {
            mlVendedoresIgnoradosRanking = normalizarListaVendedoresIgnoradosRanking(lista);
            salvarVendedoresIgnoradosRankingLocal();
            atualizarBotoesVendedoresRanking();
            atualizarRankingMediaVendas();
            renderizarFavoritosOutrosAnuncios(favMlSkuSelecionado);
            renderizarHistoricoFavoritos();
        }

        async function carregarVendedoresIgnoradosRankingServidor() {
            try {
                const response = await fetch('/api/favoritos/preferencias-vendedores-ignorados', {
                    headers: obterAuthHeaders()
                });
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                const data = await response.json();
                const listaServidor = normalizarListaVendedoresIgnoradosRanking(data.vendedores_ignorados || []);
                const listaLocal = normalizarListaVendedoresIgnoradosRanking(obterVendedoresIgnoradosRanking());
                const combinada = mesclarListasVendedoresIgnoradosRanking(listaServidor, listaLocal);
                mlVendedoresIgnoradosServidorCarregado = true;
                aplicarVendedoresIgnoradosRanking(combinada);
                if (JSON.stringify(combinada) !== JSON.stringify(listaServidor)) {
                    await salvarVendedoresIgnoradosRankingServidor();
                }
            } catch (err) {
                console.warn('NÃ£o foi possÃ­vel sincronizar vendedores ignorados com o servidor:', err);
                mlVendedoresIgnoradosServidorCarregado = false;
                renderizarVendedoresIgnoradosRanking();
            }
        }

        function agendarSalvarVendedoresIgnoradosRankingServidor() {
            if (mlVendedoresIgnoradosSaveTimer) {
                clearTimeout(mlVendedoresIgnoradosSaveTimer);
            }
            mlVendedoresIgnoradosSaveTimer = setTimeout(() => {
                mlVendedoresIgnoradosSaveTimer = null;
                salvarVendedoresIgnoradosRankingServidor().catch(err => {
                    console.warn('NÃ£o foi possÃ­vel salvar vendedores ignorados no servidor:', err);
                });
            }, mlVendedoresIgnoradosServidorCarregado ? 250 : 800);
        }

        async function salvarVendedoresIgnoradosRankingServidor() {
            const response = await fetch('/api/favoritos/preferencias-vendedores-ignorados', {
                method: 'PUT',
                headers: headersJsonAutenticado(),
                body: JSON.stringify({
                    vendedores_ignorados: obterVendedoresIgnoradosRanking()
                })
            });
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            const data = await response.json();
            mlVendedoresIgnoradosServidorCarregado = true;
            aplicarVendedoresIgnoradosRanking(data.vendedores_ignorados || obterVendedoresIgnoradosRanking());
            return data;
        }

        function vendedorIgnoradoNoRanking(vendedor) {
            const chave = normalizarNomeVendedorParaBusca(vendedor);
            if (!chave) return false;
            return obterVendedoresIgnoradosRanking()
                .some(nome => normalizarNomeVendedorParaBusca(nome) === chave);
        }

        function filtrarAnunciosIgnoradosRanking(anuncios) {
            return (Array.isArray(anuncios) ? anuncios : [])
                .filter(anuncio => !vendedorIgnoradoNoRanking(anuncio && anuncio.vendedor));
        }

        function renderizarVendedoresIgnoradosRanking() {
            if (!mlIgnoredSellersListEl || !mlIgnoredSellersEmptyEl) return;
            const vendedores = obterVendedoresIgnoradosRanking();
            mlIgnoredSellersListEl.innerHTML = '';
            mlIgnoredSellersEmptyEl.classList.toggle('hidden', vendedores.length > 0);

            vendedores.forEach(vendedor => {
                const chip = document.createElement('span');
                chip.className = 'ml-ignored-chip';

                const nome = document.createElement('span');
                nome.textContent = vendedor;

                const remover = document.createElement('button');
                remover.type = 'button';
                remover.title = `Voltar a rankear ${vendedor}`;
                remover.textContent = 'x';
                remover.addEventListener('click', () => removerVendedorIgnoradoRanking(vendedor));

                chip.appendChild(nome);
                chip.appendChild(remover);
                mlIgnoredSellersListEl.appendChild(chip);
            });
        }

        function atualizarBotoesVendedoresRanking() {
            (mlAnunciosPrimeiraPaginaAtuais || []).forEach(anuncio => {
                const row = selecionarLinhaAnuncio(anuncio);
                const cell = row ? row.querySelector('.ml-vendedor') : null;
                if (cell) renderizarCelulaVendedor(cell, anuncio.vendedor || '');
            });
        }

        function adicionarVendedorIgnoradoRanking(vendedor) {
            const nome = normalizarNomeVendedor(vendedor);
            if (!vendedorValido(nome) || vendedorIgnoradoNoRanking(nome)) return;
            obterVendedoresIgnoradosRanking().push(nome);
            mlVendedoresIgnoradosRanking.sort((a, b) => a.localeCompare(b, 'pt-BR'));
            salvarVendedoresIgnoradosRanking();
            atualizarBotoesVendedoresRanking();
            atualizarRankingMediaVendas();
            renderizarFavoritosOutrosAnuncios(favMlSkuSelecionado);
            renderizarHistoricoFavoritos();
            if (mlPrimeiraPaginaStatusEl) {
                mlPrimeiraPaginaStatusEl.textContent = `Vendedor "${nome}" removido do ranking e salvo na conta do usuÃ¡rio. Os anÃºncios dele continuam na tabela.`;
            }
            if (favMlStatusEl) {
                favMlStatusEl.textContent = `Vendedor "${nome}" ignorado e salvo na conta do usuario. Os anuncios dele nao entram mais no ranking.`;
            }
            if (mlHistoricoFavoritosStatusEl && document.getElementById('aba-historico')?.classList.contains('active')) {
                mlHistoricoFavoritosStatusEl.textContent = `Vendedor "${nome}" ignorado. O historico agora oculta os anuncios dele.`;
            }
        }

        function removerVendedorIgnoradoRanking(vendedor) {
            const chave = normalizarNomeVendedorParaBusca(vendedor);
            mlVendedoresIgnoradosRanking = obterVendedoresIgnoradosRanking()
                .filter(nome => normalizarNomeVendedorParaBusca(nome) !== chave);
            salvarVendedoresIgnoradosRanking();
            atualizarBotoesVendedoresRanking();
            atualizarRankingMediaVendas();
            renderizarFavoritosOutrosAnuncios(favMlSkuSelecionado);
            renderizarHistoricoFavoritos();
        }

        function renderizarCelulaVendedor(cell, vendedor) {
            if (!cell) return;
            const nome = normalizarNomeVendedor(vendedor);
            cell.innerHTML = '';
            if (!nome) return;

            const wrap = document.createElement('div');
            wrap.className = 'ml-seller-cell';

            const nomeEl = document.createElement('span');
            nomeEl.className = 'ml-seller-name';
            nomeEl.textContent = nome;
            wrap.appendChild(nomeEl);

            if (vendedorValido(nome)) {
                const ignorado = vendedorIgnoradoNoRanking(nome);
                const botao = document.createElement('button');
                botao.type = 'button';
                botao.className = `ml-ignore-seller-btn${ignorado ? ' is-ignored' : ''}`;
                botao.textContent = ignorado ? 'Vendedor ignorado' : 'Ignorar esse vendedor';
                botao.title = ignorado
                    ? 'Este vendedor jÃ¡ estÃ¡ fora do ranking'
                    : `Ignorar ${nome} nos rankings`;
                botao.disabled = ignorado;
                if (!ignorado) {
                    botao.addEventListener('click', () => adicionarVendedorIgnoradoRanking(nome));
                }
                wrap.appendChild(botao);
            }

            cell.appendChild(wrap);
        }

        function parseNumeroVendas(valor) {
            if (valor === null || valor === undefined) return null;
            if (typeof valor === 'number') return Number.isFinite(valor) ? valor : null;
            const texto = String(valor).trim();
            if (!texto) return null;
            const match = texto.match(/([0-9][0-9\.,]*)\s*(k|mil)?\b/i);
            if (!match) return null;
            let numeroTexto = String(match[1]).replace(/\s+/g, '');
            const sufixo = String(match[2] || '').toLowerCase();

            if (numeroTexto.includes('.') && numeroTexto.includes(',')) {
                numeroTexto = numeroTexto.lastIndexOf('.') > numeroTexto.lastIndexOf(',')
                    ? numeroTexto.replace(/,/g, '')
                    : numeroTexto.replace(/\./g, '').replace(',', '.');
            } else if (numeroTexto.includes(',')) {
                numeroTexto = /^\d{1,3}(?:,\d{3})+$/.test(numeroTexto)
                    ? numeroTexto.replace(/,/g, '')
                    : numeroTexto.replace(',', '.');
            } else if (numeroTexto.includes('.')) {
                numeroTexto = /^\d{1,3}(?:\.\d{3})+$/.test(numeroTexto)
                    ? numeroTexto.replace(/\./g, '')
                    : numeroTexto;
            }
            const numero = Number(numeroTexto);
            if (!Number.isFinite(numero)) return null;
            const total = (sufixo === 'k' || sufixo === 'mil') ? numero * 1000 : numero;
            return Number.isFinite(total) ? Math.round(total) : null;
        }

        const hasNumeroVendas = (valor) => parseNumeroVendas(valor) !== null;
        const hasTexto = (valor) => typeof valor === 'string' ? valor.trim().length > 0 : valor !== null && valor !== undefined;
        const PESO_FONTE_VENDEDOR = {
            '': 0,
            avantpro_card: 1,
            api_search: 2,
            pagina_produto_fonte: 3,
            codigo_fonte: 4,
            pagina_produto: 5,
            avantpro_dom: 5,
            avantpro_vendedor: 8,
            api: 6,
            api_item: 6,
            api_item_redirect: 6,
            mercadolivre_backend: 6,
            browser_item: 7
        };
        const PESO_FONTE_VENDAS = {
            '': 0,
            api: 2,
            api_search: 2,
            api_item_vendas: 3,
            api_item_redirect: 3,
            mercadolivre_backend: 3,
            html_text: 3,
            browser_item: 3,
            pagina_produto: 4,
            avantpro_card: 0,
            avantpro_produto: 0,
            avantpro_dom: 7,
            avantpro_anuncio: 8
        };
        const normalizarFonte = (fonte) => String(fonte || '').trim().toLowerCase();
        const pesoFonteVendedor = (fonte) => PESO_FONTE_VENDEDOR[normalizarFonte(fonte)] || 0;
        const pesoFonteVendas = (fonte) => PESO_FONTE_VENDAS[normalizarFonte(fonte)] || 0;
        const fonteVendasAvantPro = (fonte) => {
            const valor = normalizarFonte(fonte);
            return valor === 'avantpro_anuncio' || valor === 'avantpro_dom';
        };
        const fonteVendasConfiavel = (fonte) => fonteVendasAvantPro(fonte);
        function parseVendasAvantPro(anuncio) {
            const fonte = anuncio && (anuncio.vendasFonte || anuncio.vendas_fonte || '');
            if (!fonteVendasConfiavel(fonte)) return null;
            return parseNumeroVendas(anuncio && anuncio.vendas);
        }
        function formatarVendasAvantPro(anuncio) {
            const vendas = parseVendasAvantPro(anuncio);
            return vendas !== null ? String(vendas) : '';
        }
        function normalizarImagemAnuncioFavoritos(valor) {
            const url = String(valor || '').trim();
            if (!url) return '';
            if (url.startsWith('//')) return `https:${url}`;
            if (/^https?:\/\//i.test(url)) return url;
            return '';
        }
        function obterImagemAnuncioFavoritos(anuncio) {
            if (!anuncio) return '';
            let imagem = anuncio.imagem
                || anuncio.thumbnail
                || anuncio.secure_thumbnail
                || anuncio.imagem_url
                || anuncio.foto
                || anuncio.picture
                || '';
            if (!imagem && Array.isArray(anuncio.pictures)) {
                const primeira = anuncio.pictures.find(Boolean) || {};
                imagem = primeira.secure_url || primeira.url || primeira.thumbnail || '';
            }
            return normalizarImagemAnuncioFavoritos(
                imagem
            );
        }
        function preencherImagemAnuncioFavoritos(alvo, fonte) {
            if (!alvo || obterImagemAnuncioFavoritos(alvo)) return false;
            const imagem = obterImagemAnuncioFavoritos(fonte);
            if (!imagem) return false;
            alvo.imagem = imagem;
            alvo.thumbnail = imagem;
            return true;
        }
        function criarCelulaFotoAnuncioFavoritos(anuncio) {
            const td = document.createElement('td');
            td.className = 'ml-favoritos-foto-cell';
            const imagem = obterImagemAnuncioFavoritos(anuncio);
            if (!imagem) {
                const placeholder = document.createElement('span');
                placeholder.className = 'ml-favoritos-foto-placeholder';
                placeholder.textContent = 'Sem foto';
                td.appendChild(placeholder);
                return td;
            }
            const img = document.createElement('img');
            img.className = 'ml-favoritos-foto';
            img.src = imagem;
            img.alt = anuncio && anuncio.id ? `Foto ${anuncio.id}` : 'Foto do anuncio';
            img.loading = 'lazy';
            if (anuncio && anuncio.url) {
                const wrap = document.createElement('span');
                wrap.className = 'ml-favoritos-foto-wrap';
                const openBtn = document.createElement('button');
                openBtn.type = 'button';
                openBtn.className = 'ml-favoritos-foto-open';
                openBtn.title = 'Abrir anuncio no Google Chrome';
                openBtn.addEventListener('click', (event) => abrirAnuncioNoChromeExterno(anuncio.url, event));
                openBtn.appendChild(img);
                const copyBtn = document.createElement('button');
                copyBtn.type = 'button';
                copyBtn.className = 'ml-favoritos-foto-copy';
                copyBtn.textContent = 'Copiar link';
                copyBtn.title = 'Copiar link do anuncio';
                copyBtn.addEventListener('click', (event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    copiarLinkAnuncio(anuncio.url, copyBtn);
                });
                wrap.appendChild(openBtn);
                wrap.appendChild(copyBtn);
                td.appendChild(wrap);
            } else {
                td.appendChild(img);
            }
            return td;
        }
        function parsePrecoAnuncioFavoritos(valor) {
            if (valor === null || valor === undefined || valor === '') return null;
            if (typeof valor === 'number') return Number.isFinite(valor) ? valor : null;
            const texto = String(valor).trim();
            if (!texto) return null;
            const match = texto.replace(/\s+/g, '').match(/-?\d[\d.,]*/);
            if (!match) return null;
            let normalizado = match[0];
            if (normalizado.includes('.') && normalizado.includes(',')) {
                normalizado = normalizado.lastIndexOf('.') > normalizado.lastIndexOf(',')
                    ? normalizado.replace(/,/g, '')
                    : normalizado.replace(/\./g, '').replace(',', '.');
            } else if (normalizado.includes(',')) {
                normalizado = normalizado.replace(/\./g, '').replace(',', '.');
            }
            const numero = Number(normalizado);
            return Number.isFinite(numero) ? numero : null;
        }
        function parsePercentualDescontoFavoritos(valor) {
            if (valor === null || valor === undefined || valor === '') return null;
            if (typeof valor === 'number') {
                if (!Number.isFinite(valor) || valor <= 0) return null;
                return valor > 0 && valor <= 1 ? valor * 100 : valor;
            }
            const texto = String(valor).trim();
            if (!texto) return null;
            const match = texto.replace(/\s+/g, '').match(/-?\d[\d.,]*/);
            if (!match) return null;
            let normalizado = match[0];
            if (normalizado.includes('.') && normalizado.includes(',')) {
                normalizado = normalizado.lastIndexOf('.') > normalizado.lastIndexOf(',')
                    ? normalizado.replace(/,/g, '')
                    : normalizado.replace(/\./g, '').replace(',', '.');
            } else if (normalizado.includes(',')) {
                normalizado = normalizado.replace(/\./g, '').replace(',', '.');
            }
            const numero = Number(normalizado);
            if (!Number.isFinite(numero) || numero <= 0) return null;
            return numero > 0 && numero <= 1 ? numero * 100 : numero;
        }
        function calcularDescontoPrecoFavoritos(precoOriginal, precoPromocional, anuncio) {
            if (precoOriginal !== null && precoPromocional !== null && precoOriginal > precoPromocional && precoOriginal > 0) {
                const desconto = ((precoOriginal - precoPromocional) / precoOriginal) * 100;
                return Number.isFinite(desconto) && desconto > 0 ? desconto : null;
            }
            return null;
        }
        function formatarDescontoPrecoFavoritos(valor) {
            const numero = Number(valor);
            if (!Number.isFinite(numero) || numero <= 0) return '';
            const arredondado = Math.round(numero * 10) / 10;
            const texto = Math.abs(arredondado - Math.round(arredondado)) < 0.05
                ? String(Math.round(arredondado))
                : arredondado.toFixed(1).replace('.', ',');
            return `-${texto}%`;
        }
        function fontePrecoFavoritos(anuncio) {
            return normalizarFonte(anuncio && (
                anuncio.fonte_preco ||
                anuncio.preco_fonte ||
                anuncio.price_source ||
                anuncio.source ||
                anuncio.origem_dados ||
                ''
            ));
        }
        function prioridadeFontePrecoFavoritos(fonte) {
            const texto = normalizarFonte(fonte);
            if (!texto) return 0;
            if (texto.includes('api') || texto.includes('item') || texto.includes('mercadolivre_backend')) return 30;
            if (texto.includes('backend')) return 20;
            if (texto.includes('avantpro') || texto.includes('browser') || texto.includes('pagina')) return 10;
            return 5;
        }
        function fontePrecoConfiavelFavoritos(anuncio) {
            return prioridadeFontePrecoFavoritos(fontePrecoFavoritos(anuncio)) >= 20;
        }
        function obterPrecosAnuncioFavoritos(anuncio) {
            if (!anuncio) return { preco: null, promocional: null, desconto: null };
            const precoAtual = parsePrecoAnuncioFavoritos(anuncio.preco ?? anuncio.price ?? anuncio.valor ?? '');
            const precoPromoDireto = parsePrecoAnuncioFavoritos(anuncio.preco_promocional ?? anuncio.promotional_price ?? anuncio.promotion_price ?? anuncio.sale_price ?? '');
            let precoOriginal = parsePrecoAnuncioFavoritos(anuncio.preco_original ?? anuncio.original_price ?? anuncio.regular_amount ?? '');
            if (precoOriginal === null && precoPromoDireto !== null) {
                precoOriginal = parsePrecoAnuncioFavoritos(anuncio.standard_price ?? anuncio.base_price ?? '');
            }
            let preco = precoAtual;
            let promocional = precoPromoDireto;
            if (precoOriginal !== null && precoAtual !== null && precoOriginal > precoAtual) {
                preco = precoOriginal;
                promocional = precoAtual;
            } else if (precoOriginal !== null && precoPromoDireto !== null && precoOriginal > precoPromoDireto) {
                preco = precoOriginal;
                promocional = precoPromoDireto;
            } else if (precoPromoDireto !== null && precoAtual !== null && precoAtual > precoPromoDireto) {
                preco = precoAtual;
                promocional = precoPromoDireto;
            }
            if (promocional !== null && preco !== null && Math.abs(promocional - preco) < 0.005) {
                promocional = null;
            }
            const desconto = calcularDescontoPrecoFavoritos(preco, promocional, anuncio);
            return { preco, promocional, desconto };
        }
        function preencherPrecoAnuncioFavoritos(alvo, fonte) {
            if (!alvo || !fonte) return false;
            let alterou = false;
            const precosFonte = obterPrecosAnuncioFavoritos(fonte);
            const fonteNova = fontePrecoFavoritos(fonte);
            const prioridadeNova = prioridadeFontePrecoFavoritos(fonteNova);
            const prioridadeAtual = prioridadeFontePrecoFavoritos(fontePrecoFavoritos(alvo));
            const temPrecoFonte = precosFonte.preco !== null || precosFonte.promocional !== null;

            if (temPrecoFonte && (prioridadeNova > prioridadeAtual || (prioridadeNova >= 20 && !fontePrecoConfiavelFavoritos(alvo)))) {
                const precoBase = precosFonte.preco !== null ? precosFonte.preco : precosFonte.promocional;
                const precoAtual = precosFonte.promocional !== null ? precosFonte.promocional : precoBase;
                alvo.preco = precoBase;
                alvo.price = precoAtual;
                alvo.preco_original = precosFonte.promocional !== null ? precoBase : '';
                alvo.original_price = precosFonte.promocional !== null ? precoBase : '';
                alvo.standard_price = precoBase;
                alvo.preco_promocional = precosFonte.promocional !== null ? precosFonte.promocional : '';
                alvo.promotional_price = precosFonte.promocional !== null ? precosFonte.promocional : '';
                alvo.sale_price = '';
                alvo.discount_pct = precosFonte.desconto || '';
                alvo.fonte_preco = fonteNova || fonte.source || fonte.origem_dados || '';
                alterou = true;
                return alterou;
            }

            const campos = ['preco', 'price', 'preco_original', 'original_price', 'standard_price', 'preco_promocional', 'promotional_price', 'promotion_price', 'sale_price'];
            campos.forEach(campo => {
                if ((alvo[campo] === null || alvo[campo] === undefined || alvo[campo] === '') && fonte[campo] !== null && fonte[campo] !== undefined && fonte[campo] !== '') {
                    alvo[campo] = fonte[campo];
                    alterou = true;
                }
            });
            if (!alvo.fonte_preco && fonteNova) {
                alvo.fonte_preco = fonteNova;
                alterou = true;
            }
            return alterou;
        }
        function precisaComplementoPrecoFavoritos(anuncio) {
            const precos = obterPrecosAnuncioFavoritos(anuncio);
            if (precos.preco === null) return true;
            if (!fontePrecoConfiavelFavoritos(anuncio)) return true;
            return parsePrecoAnuncioFavoritos(anuncio && (anuncio.preco_original ?? anuncio.original_price ?? anuncio.standard_price ?? '')) === null
                && parsePrecoAnuncioFavoritos(anuncio && (anuncio.preco_promocional ?? anuncio.promotional_price ?? anuncio.promotion_price ?? anuncio.sale_price ?? '')) === null;
        }
        function criarCelulaPrecoAnuncioFavoritos(anuncio) {
            const td = document.createElement('td');
            td.className = 'ml-favoritos-preco-cell';
            const precos = obterPrecosAnuncioFavoritos(anuncio);
            if (precos.preco === null && precos.promocional === null) return td;
            const base = document.createElement('span');
            base.className = 'ml-favoritos-preco-base' + (precos.promocional !== null ? ' is-original' : '');
            base.textContent = formatarPrecoFavoritosMl(precos.preco !== null ? precos.preco : precos.promocional);
            const descontoTexto = formatarDescontoPrecoFavoritos(precos.desconto);
            td.appendChild(base);
            if (precos.promocional !== null && precos.preco !== null) {
                const promo = document.createElement('span');
                promo.className = 'ml-favoritos-preco-promo';
                promo.textContent = formatarPrecoFavoritosMl(precos.promocional);
                if (descontoTexto) {
                    const desconto = document.createElement('span');
                    desconto.className = 'ml-favoritos-preco-desconto';
                    desconto.textContent = descontoTexto;
                    promo.appendChild(desconto);
                }
                td.appendChild(promo);
            } else if (descontoTexto) {
                const desconto = document.createElement('span');
                desconto.className = 'ml-favoritos-preco-desconto';
                desconto.textContent = descontoTexto;
                base.appendChild(desconto);
            }
            return td;
        }
        function normalizarTipoAnuncioFavoritos(valor) {
            if (valor === null || valor === undefined || valor === '') return '';
            let bruto = valor;
            if (typeof bruto === 'object') {
                bruto = bruto.name || bruto.label || bruto.title || bruto.id || '';
            }
            const original = String(bruto || '').trim();
            if (!original) return '';
            if (original === '-') return '';
            const texto = normalizarNomeVendedorParaBusca(original);
            if (!texto) return original;
            const compacto = texto.replace(/\s+/g, '');
            if (texto.includes('premium') || texto.includes('gold pro') || texto === 'pro') return 'Premium';
            if (compacto.includes('classico') || compacto.includes('classic') || texto.includes('gold special') || texto === 'gold') return 'Classico';
            if (texto === 'free' || compacto.includes('gratis') || compacto.includes('gratuito')) return 'Gratis';
            return original;
        }
        function obterParcelamentoSemJurosFavoritos(anuncio) {
            if (!anuncio) return null;
            const camposBooleanos = [
                anuncio.parcelamento_sem_juros,
                anuncio.parcelamentoSemJuros,
                anuncio.installments_sem_juros,
                anuncio.installmentsSemJuros,
                anuncio.sem_juros,
                anuncio.semJuros,
                anuncio.juros_zero
            ];
            for (const valor of camposBooleanos) {
                if (valor === true || valor === false) return valor;
                if (typeof valor === 'number' && Number.isFinite(valor)) return valor !== 0;
                if (typeof valor === 'string') {
                    const texto = normalizarNomeVendedorParaBusca(valor);
                    if (['true', 'sim', 'yes', '1', 'sem juros', 'semjuros'].includes(texto)) return true;
                    if (['false', 'nao', 'nÃ£o', 'no', '0', 'com juros'].includes(texto)) return false;
                }
            }
            const installments = anuncio.installments || anuncio.parcelamento || anuncio.installment || null;
            if (installments && typeof installments === 'object') {
                const rate = parsePrecoAnuncioFavoritos(installments.rate ?? installments.interest_rate ?? installments.interestRate ?? installments.juros ?? '');
                const quantity = Number(installments.quantity ?? installments.installments ?? installments.parcelas ?? 0);
                if (rate !== null) return rate === 0 && (!Number.isFinite(quantity) || quantity > 1);
                if (installments.no_interest === true || installments.noInterest === true || installments.sem_juros === true) return true;
            }
            const textoParcelamento = [
                anuncio.parcelamento_texto,
                anuncio.parcelamentoTexto,
                anuncio.installments_text,
                anuncio.installmentsText,
                anuncio.installments && anuncio.installments.text,
                anuncio.installments && anuncio.installments.description
            ].filter(Boolean).join(' ');
            if (textoParcelamento) {
                const texto = normalizarNomeVendedorParaBusca(textoParcelamento);
                if (texto.includes('sem juros')) return true;
                if (texto.includes('com juros')) return false;
            }
            return null;
        }
        function inferirTipoPorParcelamentoFavoritos(anuncio) {
            const semJuros = obterParcelamentoSemJurosFavoritos(anuncio);
            if (semJuros === true) return 'Premium';
            if (semJuros === false) return 'Classico';
            return '';
        }
        function obterTipoAnuncioFavoritos(anuncio) {
            if (!anuncio) return '';
            const tipoParcelamento = inferirTipoPorParcelamentoFavoritos(anuncio);
            if (tipoParcelamento) return tipoParcelamento;
            const candidatos = [
                anuncio.listing_type_id,
                anuncio.listingTypeId,
                anuncio.listing_type,
                anuncio.listingType,
                anuncio.tipo_anuncio,
                anuncio.tipoAnuncio,
                anuncio.tipo,
                anuncio.listing_type_name,
                anuncio.listingTypeName
            ];
            for (const candidato of candidatos) {
                const tipo = normalizarTipoAnuncioFavoritos(candidato);
                if (tipo) return tipo;
            }
            return '';
        }
        function valorIndicaFullFavoritos(valor) {
            if (valor === true) return true;
            if (valor === false || valor === null || valor === undefined) return false;
            if (Array.isArray(valor)) return valor.some(item => valorIndicaFullFavoritos(item));
            if (typeof valor === 'object') {
                return valorIndicaFullFavoritos(valor.logistic_type)
                    || valorIndicaFullFavoritos(valor.logisticType)
                    || valorIndicaFullFavoritos(valor.tipo_logistica)
                    || valorIndicaFullFavoritos(valor.tags)
                    || valor.full === true
                    || valor.is_full === true
                    || valor.fulfillment === true;
            }
            const texto = normalizarNomeVendedorParaBusca(valor);
            return texto === 'full'
                || texto === 'mercado livre full'
                || texto === 'mercadolivre full'
                || texto === 'fulfillment'
                || texto.includes(' logistic type fulfillment ')
                || texto.includes(' mercado livre full ');
        }
        function temIndicadorFullFavoritos(anuncio) {
            if (!anuncio) return false;
            const campos = [
                'full', 'is_full', 'isFull', 'meli_full', 'mercado_livre_full',
                'fulfillment', 'envio_full', 'logistic_type', 'logisticType',
                'shipping_logistic_type', 'shippingLogisticType', 'tipo_logistica'
            ];
            if (campos.some(campo => anuncio[campo] !== null && anuncio[campo] !== undefined && anuncio[campo] !== '')) return true;
            const shipping = anuncio.shipping || anuncio.shipping_info || anuncio.shippingInfo || anuncio.envio || null;
            if (shipping && typeof shipping === 'object') {
                return ['logistic_type', 'logisticType', 'tipo_logistica', 'tags', 'full', 'is_full', 'fulfillment']
                    .some(campo => shipping[campo] !== null && shipping[campo] !== undefined && shipping[campo] !== '');
            }
            return false;
        }
        function obterFullAnuncioFavoritos(anuncio) {
            if (!anuncio) return false;
            return valorIndicaFullFavoritos([
                anuncio.full,
                anuncio.is_full,
                anuncio.isFull,
                anuncio.meli_full,
                anuncio.mercado_livre_full,
                anuncio.fulfillment,
                anuncio.envio_full,
                anuncio.logistic_type,
                anuncio.logisticType,
                anuncio.shipping_logistic_type,
                anuncio.shippingLogisticType,
                anuncio.tipo_logistica,
                anuncio.shipping,
                anuncio.shipping_info,
                anuncio.shippingInfo,
                anuncio.envio
            ]);
        }
        function fullAnuncioDesconhecidoFavoritos(anuncio) {
            return !!(anuncio && !temIndicadorFullFavoritos(anuncio) && !anuncio._fullAnuncioVerificado);
        }
        function preencherFullAnuncioFavoritos(alvo, fonte) {
            if (!alvo || !fonte) return false;
            let alterou = false;
            if (obterFullAnuncioFavoritos(fonte) && !obterFullAnuncioFavoritos(alvo)) {
                alvo.is_full = true;
                alvo.full = true;
                alterou = true;
            }
            const campos = ['full', 'is_full', 'isFull', 'meli_full', 'mercado_livre_full', 'fulfillment', 'envio_full', 'logistic_type', 'logisticType', 'shipping_logistic_type', 'shippingLogisticType', 'tipo_logistica', 'shipping', 'shipping_info', 'shippingInfo', 'envio'];
            campos.forEach(campo => {
                if ((alvo[campo] === null || alvo[campo] === undefined || alvo[campo] === '') && fonte[campo] !== null && fonte[campo] !== undefined && fonte[campo] !== '') {
                    alvo[campo] = fonte[campo];
                    alterou = true;
                }
            });
            if (temIndicadorFullFavoritos(fonte)) alvo._fullAnuncioVerificado = true;
            return alterou;
        }
        function obterTipoCompletoAnuncioFavoritos(anuncio) {
            const tipo = obterTipoAnuncioFavoritos(anuncio);
            const full = obterFullAnuncioFavoritos(anuncio);
            return [tipo, full ? 'Full' : ''].filter(Boolean).join(' ');
        }
        function preencherTipoAnuncioFavoritos(alvo, fonte) {
            if (!alvo || !fonte) return false;
            let alterou = false;
            if (preencherFullAnuncioFavoritos(alvo, fonte)) alterou = true;
            const semJuros = obterParcelamentoSemJurosFavoritos(fonte);
            if (semJuros !== null && obterParcelamentoSemJurosFavoritos(alvo) === null) {
                alvo.parcelamento_sem_juros = semJuros;
                alterou = true;
            }
            const tipo = obterTipoAnuncioFavoritos(fonte);
            if (tipo && !obterTipoAnuncioFavoritos(alvo)) {
                alvo.tipo_anuncio = tipo;
                alvo.listing_type_name = tipo;
                alterou = true;
            }
            const tipoId = fonte.listing_type_id || fonte.listingTypeId || (fonte.listing_type && fonte.listing_type.id) || '';
            if (tipoId && !alvo.listing_type_id) {
                alvo.listing_type_id = tipoId;
                alterou = true;
            }
            return alterou;
        }
        function criarCelulaTipoAnuncioFavoritos(anuncio) {
            const td = document.createElement('td');
            td.textContent = obterTipoCompletoAnuncioFavoritos(anuncio);
            return td;
        }
        const normalizarChaveVendedor = (valor) => normalizarNomeVendedorParaBusca(valor);
        const deveAtualizarVendedor = (atual, fonteAtual, novo, fonteNova) => {
            if (!hasTexto(novo)) {
                return false;
            }
            if (!vendedorValido(novo)) {
                return false;
            }
            if (!hasTexto(atual)) {
                return true;
            }
            if (!vendedorValido(atual)) {
                return true;
            }
            const atualTexto = normalizarChaveVendedor(atual);
            const novoTexto = normalizarChaveVendedor(novo);
            if (atualTexto === novoTexto) {
                return false;
            }
            const pesoAtual = pesoFonteVendedor(fonteAtual);
            const pesoNovo = pesoFonteVendedor(fonteNova);
            if (pesoNovo > pesoAtual) return true;
            if (pesoNovo < pesoAtual) return false;
            const scoreAtual = scoreNomeVendedor(atual);
            const scoreNovo = scoreNomeVendedor(novo);
            return scoreNovo > scoreAtual;
        };
        const deveAtualizarVendas = (atual, fonteAtual, novo, fonteNova) => {
            const atualNumero = parseNumeroVendas(atual);
            const novoNumero = parseNumeroVendas(novo);
            if (!hasNumeroVendas(novoNumero)) return false;
            if (!fonteVendasConfiavel(fonteNova)) return false;
            if (!hasNumeroVendas(atualNumero)) return true;
            if (fonteVendasAvantPro(fonteAtual) && !fonteVendasAvantPro(fonteNova)) return false;
            const pesoAtual = pesoFonteVendas(fonteAtual);
            const pesoNovo = pesoFonteVendas(fonteNova);
            return pesoNovo > pesoAtual || (pesoNovo === pesoAtual && atualNumero !== novoNumero);
        };

        function encontrarAnuncioAvantCorrespondente(anuncio, anuncios) {
            if (!anuncio || !Array.isArray(anuncios)) return null;
            const itemId = anuncio.id || extrairItemIdAnuncio(anuncio.url);
            const normalizar = (value) => String(value || '').split('#')[0].trim();
            const porIdOuUrl = anuncios.find(item => {
                if (!item) return false;
                const itemCandidateId = item.id || extrairItemIdAnuncio(item.url);
                if (itemId && itemCandidateId && String(itemCandidateId).toUpperCase() === String(itemId).toUpperCase()) return true;
                return normalizar(item.url) && normalizar(item.url) === normalizar(anuncio.url);
            });
            if (porIdOuUrl) return porIdOuUrl;

            return null;
        }

        function extrairItemIdAnuncio(url) {
            let texto = String(url || '');
            try {
                texto = decodeURIComponent(texto);
            } catch (e) {
                try { texto = decodeURI(texto); } catch (e2) {}
            }
            const patterns = [
                /[?&]wid=(MLB\d+)/i,
                /[?&]item_id=(MLB\d+)/i,
                /item_id:?(MLB\d+)/i,
                /item_id%3A(MLB\d+)/i,
                /\/(MLB-?\d+)/i,
                /\b(MLB-?\d{6,})\b/i
            ];
            for (const pattern of patterns) {
                const match = texto.match(pattern);
                if (match && match[1]) {
                    const itemId = match[1].replace('-', '').toUpperCase();
                    const digits = itemId.replace(/^MLB/i, '');
                    if (/^MLB\d+$/i.test(itemId) && digits.length >= 8) return itemId;
                }
            }
            return '';
        }

        const ML_API_WORKERS = 18;
        const ML_BROWSER_WORKERS = 6;

        function tituloPareceFiltroOuCategoriaMl(titulo) {
            const normalizado = normalizarTextoMl(titulo);
            if (!normalizado) return true;
            return [
                'resultados',
                'pecas de motos e quadriciclos',
                'lubrificantes e fluidos',
                'pecas de linha pesada',
                'pecas de carros e caminhonetes',
                'acessorios de motos e quadriciclos',
                'categorias',
                'condicao',
                'tipo de envio',
                'custo de envio',
                'tempo de entrega'
            ].includes(normalizado);
        }

        function normalizarCondicaoAnuncioFavoritos(valor) {
            if (valor === null || valor === undefined || valor === '') return '';
            let bruto = valor;
            if (typeof bruto === 'object') {
                bruto = bruto.value_name || bruto.name || bruto.label || bruto.title || bruto.value_id || bruto.id || '';
            }
            const texto = normalizarTextoMl(bruto);
            if (!texto) return '';
            if (['new', 'novo', 'nueva', 'nuevo', '2230284'].includes(texto) || texto.includes('novo') || texto.includes('nuev')) {
                return 'new';
            }
            if (['used', 'usado', 'usada', '2230581'].includes(texto) || texto.includes('usad')) {
                return 'used';
            }
            return texto;
        }

        function obterCondicaoAnuncioFavoritos(anuncio) {
            if (!anuncio || typeof anuncio !== 'object') return '';
            const attrs = Array.isArray(anuncio.attributes) ? anuncio.attributes : [];
            for (const attr of attrs) {
                const attrId = String(attr && attr.id || '').toUpperCase();
                if (attrId === 'ITEM_CONDITION' || attrId === 'CONDITION') {
                    const condicaoAttr = normalizarCondicaoAnuncioFavoritos(attr.value_name || attr.value_id || attr);
                    if (condicaoAttr) return condicaoAttr;
                }
            }
            const candidatos = [
                anuncio.condicao,
                anuncio.condition,
                anuncio.item_condition,
                anuncio.itemCondition,
                anuncio.itemConditionName,
                anuncio.estado,
                anuncio.item_state
            ];
            for (const candidato of candidatos) {
                const condicao = normalizarCondicaoAnuncioFavoritos(candidato);
                if (condicao) return condicao;
            }
            return '';
        }

        function preencherCondicaoAnuncioFavoritos(alvo, fonte) {
            if (!alvo || !fonte) return false;
            const condicao = obterCondicaoAnuncioFavoritos(fonte);
            if (!condicao) return false;
            const atual = obterCondicaoAnuncioFavoritos(alvo);
            if (atual === condicao) return false;
            alvo.condicao = condicao;
            alvo.condition = condicao;
            alvo.item_condition = condicao;
            return true;
        }

        function anuncioFavoritosProdutoNovo(anuncio) {
            return obterCondicaoAnuncioFavoritos(anuncio) !== 'used';
        }

        async function consultarItemApiMercadoLivre(itemId) {
            itemId = String(itemId || '').trim().toUpperCase();
            if (!itemId) return null;
            consultarItemApiMercadoLivre.cache = consultarItemApiMercadoLivre.cache || new Map();
            if (consultarItemApiMercadoLivre.cache.has(itemId)) {
                return consultarItemApiMercadoLivre.cache.get(itemId);
            }

            const temInformacao = (info) => hasTexto(info && info.titulo) || hasTexto(info && info.data_criacao) || hasTexto(info && info.vendedor) || hasNumeroVendas(info && info.vendas);

            let viaElectron = null;
            if (window.electronAPI && typeof window.electronAPI.getMlPublicItemInfo === 'function') {
                try {
                    const retorno = await window.electronAPI.getMlPublicItemInfo(itemId);
                    if (retorno) {
                        viaElectron = {
                            id: retorno.id || itemId,
                            titulo: hasTexto(retorno.titulo) ? retorno.titulo : '',
                            preco: retorno.preco ?? retorno.price ?? '',
                            price: retorno.price ?? retorno.preco ?? '',
                            preco_original: retorno.preco_original ?? retorno.original_price ?? retorno.standard_price ?? '',
                            original_price: retorno.original_price ?? retorno.preco_original ?? '',
                            standard_price: retorno.standard_price ?? '',
                            preco_promocional: retorno.preco_promocional ?? retorno.promotional_price ?? retorno.sale_price ?? '',
                            discount_pct: retorno.discount_pct ?? retorno.discount_percent ?? retorno.discount_percentage ?? '',
                            fonte_preco: retorno.fonte_preco ?? retorno.price_source ?? retorno.source ?? 'api_item',
                            installments: retorno.installments || null,
                            parcelamento_sem_juros: obterParcelamentoSemJurosFavoritos(retorno),
                            tipo_anuncio: normalizarTipoAnuncioFavoritos(retorno.tipo_anuncio ?? retorno.tipoAnuncio ?? retorno.listing_type_name ?? retorno.listingTypeName ?? retorno.listing_type_id ?? retorno.listingTypeId ?? retorno.listing_type ?? ''),
                            listing_type_id: retorno.listing_type_id ?? retorno.listingTypeId ?? '',
                            listing_type_name: retorno.listing_type_name ?? retorno.tipo_anuncio ?? retorno.tipoAnuncio ?? '',
                            shipping: retorno.shipping || null,
                            logistic_type: retorno.logistic_type ?? retorno.logisticType ?? retorno.shipping_logistic_type ?? '',
                            shipping_mode: retorno.shipping_mode ?? retorno.shippingMode ?? '',
                            is_full: retorno.is_full ?? retorno.isFull ?? retorno.full ?? '',
                            data_criacao: hasTexto(retorno.data_criacao) ? retorno.data_criacao : '',
                            vendedor: normalizarNomeVendedor(retorno.vendedor),
                            vendas: parseNumeroVendas(retorno.vendas),
                            seller_id: retorno.seller_id || null,
                            condicao: normalizarCondicaoAnuncioFavoritos(retorno.condicao ?? retorno.condition ?? retorno.item_condition ?? retorno.itemCondition ?? ''),
                            condition: normalizarCondicaoAnuncioFavoritos(retorno.condicao ?? retorno.condition ?? retorno.item_condition ?? retorno.itemCondition ?? ''),
                            item_condition: normalizarCondicaoAnuncioFavoritos(retorno.condicao ?? retorno.condition ?? retorno.item_condition ?? retorno.itemCondition ?? ''),
                            source: retorno.source || null
                        };
                        if (!vendedorValido(viaElectron.vendedor)) {
                            viaElectron.vendedor = '';
                        }
                    }
                } catch (err) {
                    console.warn('Falha ao consultar API ML pelo Electron:', itemId, err);
                }
            }
            try {
                const itemResp = await fetch(`https://api.mercadolibre.com/items/${encodeURIComponent(itemId)}`, {
                    headers: { 'Accept': 'application/json' }
                });
                if (!itemResp.ok) {
                    if (viaElectron && temInformacao(viaElectron)) {
                        consultarItemApiMercadoLivre.cache.set(itemId, viaElectron);
                        return viaElectron;
                    }
                    return null;
                }
                const item = await itemResp.json();
                const seller = item && typeof item.seller === 'object' ? item.seller : {};
                const officialStore = item && typeof item.official_store === 'object' ? item.official_store : {};
                let vendedor = escolherNomeVendedor([
                    { valor: seller.nickname, prioridade: 80 },
                    { valor: seller.name, prioridade: 55 },
                    { valor: item.seller_name, prioridade: 70 },
                    { valor: item.official_store_name, prioridade: 95 },
                    { valor: officialStore.nickname, prioridade: 95 },
                    { valor: officialStore.name, prioridade: 90 }
                ]);
                const sellerId = item.seller_id || (item.seller && item.seller.id);
                const salePrice = item && typeof item.sale_price === 'object' && item.sale_price ? item.sale_price : {};
                const saleAmount = salePrice.amount ?? salePrice.price ?? (item && typeof item.sale_price !== 'object' ? item.sale_price : '');
                const regularAmount = salePrice.regular_amount ?? item.original_price ?? '';
                const shippingInfo = item && typeof item.shipping === 'object' && item.shipping ? item.shipping : {};
                const logisticType = shippingInfo.logistic_type || '';

                if (sellerId) {
                    try {
                        const userResp = await fetch(`https://api.mercadolibre.com/users/${encodeURIComponent(sellerId)}`, {
                            headers: { 'Accept': 'application/json' }
                        });
                        if (userResp.ok) {
                            const user = await userResp.json();
                            const nomeUsuario = escolherNomeVendedor([
                                { valor: user.official_store_name, prioridade: 130 },
                                { valor: user.official_store && user.official_store.name, prioridade: 130 },
                                { valor: user.nickname, prioridade: 120 }
                            ]);
                            if (nomeUsuario) {
                                vendedor = nomeUsuario;
                            }
                        }
                    } catch (e) {}
                }

                const info = {
                    id: item.id || itemId,
                    titulo: item.title || '',
                    imagem: item.secure_thumbnail || item.thumbnail || ((item.pictures || [])[0] && ((item.pictures || [])[0].secure_url || (item.pictures || [])[0].url)) || '',
                    thumbnail: item.secure_thumbnail || item.thumbnail || '',
                    preco: item.price ?? '',
                    price: item.price ?? '',
                    preco_original: item.original_price ?? regularAmount ?? '',
                    original_price: item.original_price ?? regularAmount ?? '',
                    standard_price: regularAmount || item.original_price || item.price || '',
                    preco_promocional: saleAmount || (item.original_price && item.price && Number(item.original_price) > Number(item.price) ? item.price : ''),
                    discount_pct: item.original_price && item.price && Number(item.original_price) > Number(item.price) ? ((Number(item.original_price) - Number(item.price)) / Number(item.original_price)) * 100 : '',
                    fonte_preco: 'api_item',
                    installments: item.installments || null,
                    parcelamento_sem_juros: obterParcelamentoSemJurosFavoritos(item),
                    tipo_anuncio: normalizarTipoAnuncioFavoritos(item.listing_type_id || item.listing_type || item.listing_type_name || ''),
                    listing_type_id: item.listing_type_id || (item.listing_type && item.listing_type.id) || '',
                    listing_type_name: normalizarTipoAnuncioFavoritos(item.listing_type_id || item.listing_type || item.listing_type_name || ''),
                    shipping: shippingInfo,
                    logistic_type: logisticType,
                    shipping_mode: shippingInfo.mode || '',
                    is_full: String(logisticType || '').toLowerCase() === 'fulfillment',
                    data_criacao: item.date_created || item.start_time || '',
                    vendedor: vendedor || item.seller_name || '',
                    vendas: parseNumeroVendas(item.sold_quantity ?? item.sold ?? item.soldQuantity ?? null),
                    seller_id: sellerId || null,
                    condicao: obterCondicaoAnuncioFavoritos(item),
                    condition: obterCondicaoAnuncioFavoritos(item),
                    item_condition: obterCondicaoAnuncioFavoritos(item)
                };
                info.source = 'api';
                const finalInfo = viaElectron || {};
                if (!hasTexto(finalInfo.titulo)) {
                    finalInfo.titulo = info.titulo;
                }
                if (!hasTexto(finalInfo.data_criacao)) {
                    finalInfo.data_criacao = info.data_criacao;
                }
                if (!hasTexto(finalInfo.vendedor)) {
                    finalInfo.vendedor = info.vendedor;
                }
                if (!hasNumeroVendas(finalInfo.vendas)) {
                    finalInfo.vendas = info.vendas;
                }
                if (!hasTexto(finalInfo.id)) {
                    finalInfo.id = info.id;
                }
                if (!hasTexto(finalInfo.imagem)) {
                    finalInfo.imagem = info.imagem;
                }
                if (!hasTexto(finalInfo.thumbnail)) {
                    finalInfo.thumbnail = info.thumbnail || info.imagem;
                }
                preencherPrecoAnuncioFavoritos(finalInfo, info);
                preencherTipoAnuncioFavoritos(finalInfo, info);
                preencherCondicaoAnuncioFavoritos(finalInfo, info);
                if (!finalInfo.seller_id) {
                    finalInfo.seller_id = info.seller_id;
                }
                if (!finalInfo.source) {
                    finalInfo.source = info.source;
                }
                if (!temInformacao(finalInfo)) {
                    if (viaElectron && temInformacao(viaElectron)) {
                        consultarItemApiMercadoLivre.cache.set(itemId, viaElectron);
                        return viaElectron;
                    }
                    return null;
                }
                consultarItemApiMercadoLivre.cache.set(itemId, finalInfo);
                return finalInfo;
            } catch (err) {
                console.warn('Falha ao consultar API pÃºblica do ML:', itemId, err);
                return null;
            }
        }

        async function executarComConcorrencia(items, limite, worker) {
            const lista = Array.isArray(items) ? items : [];
            let index = 0;
            const totalWorkers = Math.min(Math.max(limite || 1, 1), lista.length || 1);
            await Promise.all(Array.from({ length: totalWorkers }, async (_, workerIndex) => {
                while (index < lista.length) {
                    const atual = lista[index];
                    index += 1;
                    await worker(atual, workerIndex);
                }
            }));
        }

        function criarMlWebviewOculto(indice = 0) {
            let webview = document.getElementById(`ml-hidden-date-webview-${indice}`);
            if (webview) return webview;

            webview = document.createElement('webview');
            webview.id = `ml-hidden-date-webview-${indice}`;
            webview.setAttribute('partition', obterParticaoNavegadorPersistente());
            webview.setAttribute('webpreferences', 'contextIsolation=yes,nodeIntegration=no');
            webview.style.cssText = 'position:absolute;left:-10000px;top:-10000px;width:1280px;height:900px;opacity:0.01;pointer-events:none;';
            webview.addEventListener('dom-ready', () => tentarLoginAvantProNoWebview(webview));
            webview.addEventListener('did-finish-load', () => tentarLoginAvantProNoWebview(webview));
            webview.addEventListener('did-finish-load', salvarSessaoNavegadorElectron);
            webview.addEventListener('did-navigate', salvarSessaoNavegadorElectron);
            document.body.appendChild(webview);
            return webview;
        }

        const AVANT_PRO_AUTOLOGIN_SCRIPT = `
            (function () {
                try {
                    var email = ${JSON.stringify(AVANT_PRO_LOGIN_EMAIL)};
                    var href = String(location.href || '');
                    var host = String(location.hostname || '');
                    var pageText = String(document.body && document.body.innerText ? document.body.innerText : '');
                    var avantPattern = /avant\\s*pro|avantpro|avantprocloud/i;
                    var norm = function (value) {
                        var text = String(value || '');
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (e) {}
                        return text.toLowerCase().replace(/\\s+/g, ' ').trim();
                    };
                    var attrText = function (el) {
                        if (!el || !el.getAttribute) return '';
                        return [
                            el.getAttribute('type'),
                            el.getAttribute('name'),
                            el.getAttribute('id'),
                            el.getAttribute('class'),
                            el.getAttribute('placeholder'),
                            el.getAttribute('aria-label'),
                            el.getAttribute('autocomplete'),
                            el.getAttribute('action'),
                            el.getAttribute('src'),
                            el.getAttribute('href')
                        ].join(' ');
                    };
                    var hasSearchSignals = !!document.querySelector('[class*="ui-search"], ol.ui-search-layout, section.ui-search-results, a[href*="/MLB-"], a[href*="/p/MLB"], a[href*="MLB"]');
                    var isMercadoLivreHost = /(^|\\.)mercadolivre\\.com\\.br$|(^|\\.)mercadolibre\\.com/i.test(host);
                    var plainHref = norm(href);
                    var plainPage = norm(pageText);
                    var mlLoginUrl = /\\/jms\\/.*\\/lgz\\//i.test(href) || /\\/login\\b|\\/registration\\b|account-verification|access-denied|captcha|recaptcha/i.test(href);
                    var mlLoginText = /entre na sua conta|iniciar sessao|acesse sua conta|insira seu e-?mail|digite seu e-?mail|e-?mail ou telefone|email ou telefone|verificacao|nao sou um robo|captcha/.test(plainHref + ' ' + plainPage);
                    if (isMercadoLivreHost && (mlLoginUrl || (mlLoginText && !hasSearchSignals))) {
                        return { success: false, reason: 'mercado_livre_deslogado', url: href };
                    }

                    var resourceHasAvant = Array.prototype.slice.call(document.querySelectorAll('iframe[src], script[src], link[href], a[href], img[src]')).some(function (node) {
                        return avantPattern.test(attrText(node));
                    });
                    var isAuthUrl = /auth\\.avantprocloud|avantprocloud.*auth|avantpro.*login|avant\\-?pro.*login/i.test(href);
                    var hasAvantDom = avantPattern.test(href + ' ' + document.title + ' ' + pageText) || resourceHasAvant || !!document.querySelector('[class*="avant"], [id*="avant"], .avantpro-product-info-row, .created-time-card');
                    var recentAvantClick = Number.isFinite(window.__JK_AVANT_PRO_CLICKED_AT) && Date.now() - window.__JK_AVANT_PRO_CLICKED_AT < 30000;
                    if (!isAuthUrl && !hasAvantDom && !recentAvantClick) {
                        return { success: false, reason: 'avant_nao_detectado', url: href };
                    }

                    var isVisible = function (el) {
                        var rect = el && el.getBoundingClientRect ? el.getBoundingClientRect() : { width: 0, height: 0 };
                        var style = el && window.getComputedStyle ? window.getComputedStyle(el) : null;
                        return rect.width > 0 && rect.height > 0 && (!style || (style.visibility !== 'hidden' && style.display !== 'none'));
                    };
                    var contextRoot = function (input) {
                        return input.closest('form, [role="dialog"], [class*="avant"], [id*="avant"], [class*="modal"], [class*="login"], [class*="auth"], [class*="popup"], [class*="drawer"]') || input.form || input.parentElement || document.body;
                    };
                    var contextText = function (input) {
                        var root = contextRoot(input);
                        return norm([
                            attrText(input),
                            attrText(root),
                            root && root.innerText ? root.innerText : '',
                            input.labels ? Array.prototype.slice.call(input.labels).map(function (label) { return label.innerText || ''; }).join(' ') : ''
                        ].join(' '));
                    };
                    var isMlLoginField = function (input, ctx) {
                        var root = contextRoot(input);
                        var attrs = norm(attrText(input));
                        if (isMercadoLivreHost && root === document.body && mlLoginText) return true;
                        if (isMercadoLivreHost && mlLoginText && !/avant/.test(ctx)) return true;
                        if (/mercado\\s*livre|mercadolivre|mercadolibre/.test(ctx) && !/avant/.test(ctx)) return true;
                        if (isMercadoLivreHost && /user|usuario|telefone|phone|login_user|login|nickname/.test(attrs + ' ' + ctx) && !/avant/.test(ctx)) return true;
                        return false;
                    };
                    var inputs = Array.prototype.slice.call(document.querySelectorAll('input:not([type="hidden"])'));
                    var emailInput = inputs.find(function (input) {
                        if (!input || input.disabled || input.readOnly || !isVisible(input)) return false;
                        var attrs = norm(attrText(input));
                        var ctx = contextText(input);
                        var isSearch = /search|buscar|pesquisar|as_word|\\bq\\b/.test(attrs);
                        var looksEmail = input.type === 'email' || /email|e-mail|mail/.test(attrs + ' ' + ctx);
                        var hasAvantContext = avantPattern.test(ctx) || isAuthUrl || (recentAvantClick && !isMlLoginField(input, ctx));
                        if (isSearch || !looksEmail || !hasAvantContext) return false;
                        return !isMlLoginField(input, ctx);
                    });
                    if (!emailInput) {
                        return { success: false, reason: 'campo_email_avant_nao_encontrado', url: href, avantDetectado: !!(isAuthUrl || hasAvantDom || recentAvantClick) };
                    }

                    var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    setter.call(emailInput, email);
                    emailInput.dataset.jkAvantEmailFilled = '1';
                    emailInput.focus();
                    emailInput.dispatchEvent(new Event('input', { bubbles: true }));
                    emailInput.dispatchEvent(new Event('change', { bubbles: true }));
                    emailInput.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'Enter' }));

                    var root = contextRoot(emailInput);
                    var buttons = Array.prototype.slice.call(root.querySelectorAll('button, input[type="submit"], [role="button"]'));
                    var submit = buttons.find(function (btn) {
                        if (!isVisible(btn)) return false;
                        var label = norm(btn.innerText || btn.value || btn.getAttribute('aria-label') || '');
                        return /entrar|acessar|login|iniciar|continuar|enviar|comecar/.test(label);
                    }) || (emailInput.form ? Array.prototype.slice.call(emailInput.form.querySelectorAll('button, input[type="submit"]'))[0] : null);
                    if (submit) {
                        setTimeout(function () {
                            try { submit.click(); } catch (e) {}
                            try { emailInput.form && emailInput.form.requestSubmit && emailInput.form.requestSubmit(); } catch (e) {}
                        }, 250);
                    }

                    return { success: true, clicked: !!submit, url: href, context: 'avant_pro' };
                } catch (err) {
                    return { success: false, error: err && err.message ? err.message : String(err), url: String(location.href || '') };
                }
            })();
        `;

        function tentarLoginAvantProNoWebview(webview) {
            if (!webview || typeof webview.executeJavaScript !== 'function') return;
            const executar = () => {
                webview.executeJavaScript(AVANT_PRO_AUTOLOGIN_SCRIPT, true).then((result) => {
                    if (result && result.success) {
                        console.log('Login Avant Pro preenchido automaticamente:', result);
                    }
                }).catch(() => {});
            };
            setTimeout(executar, 250);
            setTimeout(executar, 900);
            setTimeout(executar, 1800);
            setTimeout(executar, 3200);
            setTimeout(executar, 5200);
        }
        function carregarUrlNoWebview(webview, url) {
            return new Promise((resolve, reject) => {
                let done = false;
                const finish = (err) => {
                    if (done) return;
                    done = true;
                    clearTimeout(timer);
                    webview.removeEventListener('did-finish-load', onLoad);
                    webview.removeEventListener('did-fail-load', onFail);
                    if (err) reject(err);
                    else resolve();
                };
                const onLoad = () => finish();
                const onFail = (event) => finish(new Error((event && event.errorDescription) || 'Falha ao carregar anÃºncio.'));
                const timer = setTimeout(() => finish(new Error('Tempo esgotado ao abrir anÃºncio.')), 10000);
                webview.addEventListener('did-finish-load', onLoad);
                webview.addEventListener('did-fail-load', onFail);
                webview.src = url;
            });
        }

        const ML_DATE_EXTRACT_SCRIPT = `
            (async function () {
                try {
                    var sleep = function (ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); };
                    for (var wait = 0; wait < 24; wait += 1) {
                        if (document.querySelector('.ui-pdp-seller__header__title, .ui-pdp-seller__link-trigger, .ui-pdp-seller__nickname, [data-testid="seller-info"], [class*="avantpro"], [id*="avantpro"], .created-time-card')) break;
                        await sleep(250);
                    }
                    var html = String(document.documentElement && document.documentElement.outerHTML ? document.documentElement.outerHTML : '');
                    var decodedHtml = html
                        .replace(/\\\\u002F/g, '/')
                        .replace(/\\\\\\//g, '/')
                        .replace(/\\\\n/g, ' ')
                        .replace(/\\\\t/g, ' ')
                        .replace(/\\\\r/g, ' ')
                        .replace(/\\\\&quot;/g, '"')
                        .replace(/&quot;/g, '"')
                        .replace(/\\\\'/g, "'")
                        .replace(/\\\\"/g, '"');
                    var patterns = [
                        /"date_created"\\s*:\\s*"([^"]+)"/i,
                        /"dateCreated"\\s*:\\s*"([^"]+)"/i,
                        /"date_created"\\s*:\\s*\\{\\s*"value"\\s*:\\s*"([^"]+)"/i,
                        /"start_time"\\s*:\\s*"([^"]+)"/i,
                        /"startTime"\\s*:\\s*"([^"]+)"/i,
                        /"item_date_created"\\s*:\\s*"([^"]+)"/i,
                        /"creation_date"\\s*:\\s*"([^"]+)"/i,
                        /"creationDate"\\s*:\\s*"([^"]+)"/i,
                        /"listing_start_time"\\s*:\\s*"([^"]+)"/i,
                        /"start_date"\\s*:\\s*"([^"]+)"/i,
                        /"itemStartTime"\\s*:\\s*"([^"]+)"/i
                    ];
                    var sellerPatterns = [
                        /"seller"\\s*:\\s*\\{[^{}]{0,220}?\"seller_name\"\\s*:\\s*\"([^\"]+)\"/i,
                        /"seller"\\s*:\\s*\\{[^{}]{0,220}?\"nickname\"\\s*:\\s*\"([^\"]+)\"/i,
                        /"official_store"\\s*:\\s*\\{[^{}]{0,220}?\"official_store_name\"\\s*:\\s*\"([^\"]+)\"/i,
                        /"official_store"\\s*:\\s*\\{[^{}]{0,220}?\"name\"\\s*:\\s*\"([^\"]+)\"/i,
                        /"officialStoreName"\\s*:\\s*\"([^\"]+)\"/i,
                        /"sellerName"\\s*:\\s*\"([^\"]+)\"/i
                    ];
                    var dateKeys = {
                        date_created: true,
                        dateCreated: true,
                        start_time: true,
                        startTime: true,
                        item_date_created: true,
                        creation_date: true,
                        creationDate: true,
                        listing_start_time: true,
                        start_date: true,
                        itemStartTime: true
                    };
                    var sellerKeys = {
                        seller_name: true,
                        sellerName: true,
                        nickname: true,
                        official_store_name: true,
                        officialStoreName: true
                    };
                    var normalizarNomeVendedor = function (value) {
                        return String(value || '')
                            .replace(/\\\\u002F/g, '/')
                            .replace(/\\\\\\//g, '/')
                            .replace(/\\\\n/g, ' ')
                            .replace(/\\\\t/g, ' ')
                            .replace(/\\\\r/g, ' ')
                            .replace(/&quot;/g, '\"')
                            .replace(/\\\\'/g, \"'\")
                            .replace(/\\\\"/g, '\"')
                            .replace(/^(vendido\\s+por|loja\\s+oficial|oficial\\s+loja)\\s+/i, '')
                            .trim();
                    };
                    var normalizarNomeVendedorBusca = function (value) {
                        return normalizarNomeVendedor(value)
                            .toLowerCase()
                            .replace(/[^a-z0-9]+/g, ' ')
                            .replace(/\\s+/g, ' ')
                            .trim();
                    };
                    var vendedorValido = function (valor) {
                        var texto = normalizarNomeVendedor(valor);
                        if (!texto) return false;
                        if (texto.length < 2 || texto.length > 120) return false;
                        if (!/[A-Za-z0-9]/.test(texto)) return false;
                        if (/^\\d+$/.test(texto)) return false;
                        var textoBusca = normalizarNomeVendedorBusca(texto);
                        if (!textoBusca) return false;
                        if (/(^|\\b)(anuncio criado|an ncio criado|criado em|catalogo criado|cat logo criado|vendas produto|total vendas|quantidade vendas)(\\b|$)/i.test(textoBusca)) return false;
                        return !/^(vendido|vendedor|anuncio|anunci[oÃ³]|produto|frete|envio|loja|oferta|ofertas|desconto|comprar|comprando|login|entrar|cadastro|email|senha|contato|perfil|busca|filtro|categoria|condi[cÃ§][aÃ£]o|aviso|informa[cÃ§][aÃ£]o|cria[cÃ§][aÃ£]o|valor|pre[cÃ§]o)$/i.test(textoBusca);
                    };
                    var escolherNomeVendedor = function (candidatos) {
                        var opcoes = Array.prototype.slice.call(candidatos || []);
                        var melhor = '';
                        var melhorScore = -1;
                        var melhorPrio = -1;
                        for (var oi = 0; oi < opcoes.length; oi += 1) {
                            var op = opcoes[oi];
                            var valor = typeof op === 'string' ? op : op && op.valor;
                            var prio = Number(op && op.prioridade) || 0;
                            var nome = normalizarNomeVendedor(valor);
                            if (!vendedorValido(nome)) continue;
                            var score = nome.length;
                            if (/\\s/.test(nome)) score += 6;
                            if (score > melhorScore) {
                                melhor = nome;
                                melhorScore = score;
                                melhorPrio = prio;
                            } else if (score === melhorScore && prio > melhorPrio) {
                                melhor = nome;
                                melhorPrio = prio;
                            }
                        }
                        return melhor;
                    };
                    var normalizarTextoMl = function (value) {
                        return String(value || '')
                            .replace(/\\\\u002F/g, '/')
                            .replace(/\\\\\\//g, '/')
                            .replace(/\\\\n/g, ' ')
                            .replace(/\\\\t/g, ' ')
                            .replace(/\\\\r/g, ' ')
                            .replace(/&quot;/g, '"')
                            .replace(/\\\\"/g, '"')
                            .trim();
                    };
                    var procurarDataEmTexto = function (text) {
                        var base = normalizarTextoMl(text);
                        for (var p = 0; p < patterns.length; p += 1) {
                            var textMatch = base.match(patterns[p]);
                            if (textMatch && textMatch[1]) return normalizarTextoMl(textMatch[1]);
                        }
                        return '';
                    };
                    var procurarVendedorEmTexto = function (text) {
                        var base = normalizarTextoMl(text);
                        var melhor = '';
                        var melhorScore = -1;
                        for (var p = 0; p < sellerPatterns.length; p += 1) {
                            var sellerMatchText = base.match(sellerPatterns[p]);
                            if (!sellerMatchText || !sellerMatchText[1]) continue;
                            var candidato = normalizarNomeVendedor(sellerMatchText[1]);
                            if (!vendedorValido(candidato)) continue;
                            var score = candidato.length + (/\\s/.test(candidato) ? 6 : 0);
                            if (score > melhorScore) {
                                melhor = candidato;
                                melhorScore = score;
                            }
                        }
                        return melhor;
                    };
                    var parseVendas = function (value, suffix) {
                        var raw = String(value || '').trim().toLowerCase();
                        if (!raw) return null;
                        var numeroTexto = String(raw).replace(/\\s+/g, '');
                        if (numeroTexto.indexOf('.') >= 0 && numeroTexto.indexOf(',') >= 0) {
                            numeroTexto = numeroTexto.lastIndexOf('.') > numeroTexto.lastIndexOf(',')
                                ? numeroTexto.replace(/,/g, '')
                                : numeroTexto.replace(/\\./g, '').replace(/,/g, '.');
                        } else if (numeroTexto.indexOf(',') >= 0) {
                            numeroTexto = /^\\d{1,3}(?:,\\d{3})+$/.test(numeroTexto)
                                ? numeroTexto.replace(/,/g, '')
                                : numeroTexto.replace(/,/g, '.');
                        } else if (numeroTexto.indexOf('.') >= 0) {
                            numeroTexto = /^\\d{1,3}(?:\\.\\d{3})+$/.test(numeroTexto)
                                ? numeroTexto.replace(/\\./g, '')
                                : numeroTexto;
                        }
                        var parsed = parseFloat(numeroTexto);
                        if (!isFinite(parsed)) return null;
                        suffix = String(suffix || '').toLowerCase();
                        if (suffix === 'mil' || suffix === 'k') parsed *= 1000;
                        return Math.round(parsed);
                    };
                    var normalizarVendas = function (valor) {
                        if (valor === null || valor === undefined || valor === '') return null;
                        if (typeof valor === 'number') return isFinite(valor) ? valor : null;
                        var match = String(valor).trim().match(/([0-9][0-9\.,]*)\s*(k|mil)?\b/i);
                        if (!match) return null;
                        return parseVendas(match[1], match[2]);
                    };
                    var coalesceVendas = function (primario, fallback) {
                        var primarioNum = normalizarVendas(primario);
                        if (Number.isFinite(primarioNum)) return primarioNum;
                        var fallbackNum = normalizarVendas(fallback);
                        return Number.isFinite(fallbackNum) ? fallbackNum : null;
                    };
                    var hasNumeroVendas = function (valor) {
                        return Number.isFinite(normalizarVendas(valor));
                    };
                    var normalizarItemId = function (valor) {
                        var texto = String(valor || '').toUpperCase();
                        var match = texto.match(/MLB-?\d+/i);
                        if (!match) return '';
                        return match[0].replace('-', '');
                    };
                    var itemIdNaPagina = (function () {
                        var href = String(location.href || '');
                        var candidato = normalizarItemId(href);
                        return candidato;
                    })();
                                        var normalizarItemIdTexto = function (valor) {
                        return normalizarItemId(valor);
                    };
                    var objetoTemItemId = function (obj) {
                        if (!obj || !itemIdNaPagina) return false;
                        var candidatos = [];
                        if (typeof obj === 'string' || typeof obj === 'number') {
                            candidatos = [obj];
                        } else if (typeof obj === 'object') {
                            candidatos = [
                                obj.id,
                                obj.item_id,
                                obj.itemId,
                                obj.itemID,
                                obj.item && obj.item.id,
                                obj.item && obj.item.item_id,
                                obj.item && obj.item.itemId,
                                obj.item && obj.item.itemID,
                                obj.product && obj.product.id,
                                obj.product && obj.product.item_id,
                                obj.product && obj.product.itemId,
                                obj.product && obj.product.itemID,
                                obj.item_info && obj.item_info.id,
                                obj.item_info && obj.item_info.item_id,
                                obj.item_info && obj.item_info.itemId,
                                obj.item_info && obj.item_info.itemID
                            ];
                        }
                        for (var ci = 0; ci < candidatos.length; ci += 1) {
                            if (normalizarItemIdTexto(candidatos[ci]) === itemIdNaPagina) return true;
                        }
                        return false;
                    };
                    var coletarVendedorEntrada = function (valor, prioridade) {
                        var prioridadeBase = Number(prioridade) || 0;
                        var out = [];
                        if (!valor) return out;
                        if (typeof valor === 'string' || typeof valor === 'number') {
                            var nomeLiteral = normalizarNomeVendedor(valor);
                            if (vendedorValido(nomeLiteral)) out.push({ valor: nomeLiteral, prioridade: prioridadeBase });
                            return out;
                        }
                        if (typeof valor !== 'object') return out;
                        if (valor.nickname) out = out.concat(coletarVendedorEntrada(valor.nickname, prioridadeBase + 30));
                        if (valor.official_store_name) out = out.concat(coletarVendedorEntrada(valor.official_store_name, prioridadeBase + 40));
                        if (valor.officialStoreName) out = out.concat(coletarVendedorEntrada(valor.officialStoreName, prioridadeBase + 40));
                        if (valor.seller_name) out = out.concat(coletarVendedorEntrada(valor.seller_name, prioridadeBase + 36));
                        if (valor.name) out = out.concat(coletarVendedorEntrada(valor.name, prioridadeBase + 20));
                        if (valor.title) out = out.concat(coletarVendedorEntrada(valor.title, prioridadeBase + 5));
                        return out;
                    };
                    var procurarVendasEmTexto = function (text) {
                        var base = normalizarTextoMl(text);
                        var salesPatterns = [
                            /vendas\\s+do\\s+(?:anuncio|item)(?:\\s+ganhador)?\\s*(?:[:\\-]|=)?\\s*(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i,
                            /vendas\\s+deste\\s+anuncio\\s*(?:[:\\-]|=)?\\s*(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i,
                            /vendas\\s+do\\s+vendedor\\s+neste\\s+anuncio\\s*(?:[:\\-]|=)?\\s*(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i
                        ];
                        for (var vp = 0; vp < salesPatterns.length; vp += 1) {
                            var salesMatch = base.match(salesPatterns[vp]);
                            if (salesMatch && salesMatch[1]) return parseVendas(salesMatch[1], salesMatch[2]);
                        }
                        return null;
                    };
                    var procurarEmObjeto = function (root) {
                        var stack = [root];
                        var seen = [];
                        var melhor = {
                            score: -1,
                            data_criacao: '',
                            vendedor: '',
                            vendas: null
                        };

                        var avaliar = function (cand) {
                            if (!cand) return;
                            var data = normalizarTextoMl(cand.data_criacao || '');
                            var vendedor = normalizarNomeVendedor(cand.vendedor || '');
                            var vendedorOk = vendedorValido(vendedor);
                            var vendas = normalizarVendas(cand.vendas);
                            var temDados = !!data || vendedorOk || Number.isFinite(vendas);

                            if (!temDados) return;

                            var score = 0;
                            if (cand.itemMatch) score += 140;
                            if (cand.idMatch) score += 20;
                            if (data) score += 40;
                            if (vendedorOk) score += 24 + (vendedor.length > 12 ? 4 : 0);
                            if (Number.isFinite(vendas)) score += 30;
                            if (cand.prioridade) score += cand.prioridade;
                            if (cand.itemMatch || cand.idMatch) score += 16;

                            if (score > melhor.score) {
                                melhor = {
                                    score: score,
                                    data_criacao: data || melhor.data_criacao,
                                    vendedor: vendedorOk ? vendedor : melhor.vendedor,
                                    vendas: Number.isFinite(vendas) ? vendas : melhor.vendas
                                };
                            }
                        };

                        while (stack.length) {
                            var cur = stack.pop();
                            if (!cur || typeof cur !== 'object') continue;
                            if (seen.indexOf(cur) >= 0) continue;
                            seen.push(cur);
                            if (seen.length > 5000) break;

                            if (Array.isArray(cur)) {
                                for (var ai = 0; ai < cur.length; ai += 1) stack.push(cur[ai]);
                                continue;
                            }

                            var itemMatch = objetoTemItemId(cur);
                            var vendedores = [];
                            var dataTexto = '';
                            var vendasAtual = null;
                            var itemKeyMatch = false;

                            for (var key in cur) {
                                if (!Object.prototype.hasOwnProperty.call(cur, key)) continue;
                                var value = cur[key];
                                if (dateKeys[key] && value) {
                                    if (key === 'date_created' && value && typeof value === 'object' && value.value) {
                                        dataTexto = normalizarTextoMl(value.value);
                                    } else if (typeof value === 'string' || typeof value === 'number') {
                                        dataTexto = normalizarTextoMl(value);
                                    }
                                    continue;
                                }
                                if (key === 'date_created' && value && typeof value === 'object' && value.value) {
                                    dataTexto = normalizarTextoMl(value.value);
                                    continue;
                                }
                                if ((key === 'sold_quantity' || key === 'soldQuantity' || key === 'sold') && value !== null && value !== undefined) {
                                    var candidatoVendas = normalizarVendas(value);
                                    if (Number.isFinite(candidatoVendas)) {
                                        vendasAtual = candidatoVendas;
                                    }
                                }
                                if (sellerKeys[key] && value) {
                                    vendedores = vendedores.concat(coletarVendedorEntrada(value, 22));
                                    continue;
                                }
                                if ((key === 'seller' || key === 'official_store' || key === 'seller_reputation') && value && typeof value === 'object') {
                                    itemKeyMatch = itemKeyMatch || objetoTemItemId(value);
                                    vendedores = vendedores.concat(coletarVendedorEntrada(value, key === 'official_store' ? 90 : 70));
                                }

                                if (value && typeof value === 'object') stack.push(value);
                            }

                            if (!itemMatch && !itemKeyMatch && cur && typeof cur === 'object' && cur.item && cur.item.id) {
                                itemKeyMatch = normalizarItemIdTexto(cur.item.id) === itemIdNaPagina;
                            }

                            avaliar({
                                data_criacao: dataTexto,
                                vendedor: escolherNomeVendedor(vendedores),
                                vendas: vendasAtual,
                                itemMatch: itemMatch,
                                idMatch: itemMatch || itemKeyMatch,
                                prioridade: itemMatch || itemKeyMatch ? 65 : 0
                            });
                        }
                        return melhor.score >= 0 ? melhor : null;
                    };
                    var limparDataVisivel = function (value) {
                        var txt = normalizarTextoMl(value)
                            .replace(/^[^0-9]*(?=\\d)/, '')
                            .replace(/\\s+/g, ' ')
                            .trim();
                        var iso = txt.match(/\\b20\\d{2}-\\d{2}-\\d{2}(?:T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?(?:Z|[+-]\\d{2}:?\\d{2})?)?\\b/);
                        if (iso && iso[0]) return iso[0];
                        var br = txt.match(/\\b\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}(?:\\s+\\d{1,2}:\\d{2}(?::\\d{2})?)?\\b/);
                        if (br && br[0]) return br[0];
                        return '';
                    };
                    var procurarDataAvantPro = function () {
                        var nodes = Array.prototype.slice.call(document.querySelectorAll('[class*="avantpro"], [id*="avantpro"], .created-time-card'));
                        nodes.push(document.body);
                        var labels = [
                            'AnÃºncio criado em',
                            'Anuncio criado em',
                            'AnÃºncio ganhador criado em',
                            'Anuncio ganhador criado em',
                            'CatÃ¡logo criado em',
                            'Catalogo criado em',
                            'Criado em'
                        ];
                        for (var ni = 0; ni < nodes.length; ni += 1) {
                            var text = String(nodes[ni] && nodes[ni].innerText ? nodes[ni].innerText : '').replace(/\\s+/g, ' ').trim();
                            if (!text) continue;
                            for (var li = 0; li < labels.length; li += 1) {
                                var idx = text.toLowerCase().indexOf(labels[li].toLowerCase());
                                if (idx < 0) continue;
                                var trecho = text.slice(idx + labels[li].length, idx + labels[li].length + 120);
                                var data = limparDataVisivel(trecho);
                                if (data) return { data_criacao: data, label: labels[li] };
                            }
                        }
                        return null;
                    };
                        var procurarVendasAvantPro = function () {
                        var parseVendasRotulada = function (text) {
                            var base = normalizarTextoMl(text)
                                .normalize('NFD')
                                .replace(/[\\u0300-\\u036f]/g, '');
                            var patterns = [
                                /vendas\\s+do\\s+(?:anuncio|item|produto)(?:\\s+ganhador)?\\b\\s*(?:[:\\-]|=)?\\s*(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i,
                                /vendas\\s+deste\\s+anuncio\\b\\s*(?:[:\\-]|=)?\\s*(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i,
                                /vendas\\s+do\\s+vendedor\\s+neste\\s+anuncio\\b\\s*(?:[:\\-]|=)?\\s*(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i
                            ];
                            for (var pi = 0; pi < patterns.length; pi += 1) {
                                var match = base.match(patterns[pi]);
                                if (match && match[1]) return parseVendas(match[1], match[2]);
                            }
                            return null;
                        };
                        var normalizarLabel = function (value) {
                            return normalizarTextoMl(value)
                                .normalize('NFD')
                                .replace(/[\\u0300-\\u036f]/g, '')
                                .toLowerCase()
                                .replace(/\\s+/g, ' ')
                                .trim();
                        };
                        var rows = Array.prototype.slice.call(document.querySelectorAll('.avantpro-product-info-row'));
                        for (var ri = 0; ri < rows.length; ri += 1) {
                            var row = rows[ri];
                            var labelNode = row.querySelector('.avantpro-product-info-row-label');
                            var valueNode = row.querySelector('.avantpro-product-info-row-value');
                            var label = normalizarLabel(labelNode && labelNode.textContent);
                            if (!valueNode || !/(vendas do anuncio|vendas deste anuncio|vendas do item|vendas do produto|vendas do anuncio ganhador|vendas do vendedor neste anuncio)/i.test(label)) continue;
                            var rowValue = normalizarVendas(valueNode.textContent);
                            if (!Number.isFinite(rowValue)) rowValue = parseVendasRotulada(label + ' ' + valueNode.textContent);
                            if (Number.isFinite(rowValue)) return { vendas: rowValue, fonte: 'avantpro_anuncio' };
                        }
                        var nodes = Array.prototype.slice.call(document.querySelectorAll('[class*="avantpro"], [id*="avantpro"], .created-time-card'));
                        nodes.push(document.body);
                        for (var ni = 0; ni < nodes.length; ni += 1) {
                            var text = String(nodes[ni] && nodes[ni].innerText ? nodes[ni].innerText : '').replace(/\\s+/g, ' ').trim();
                            if (!text) continue;
                            var vendasNode = parseVendasRotulada(text);
                            if (Number.isFinite(vendasNode)) return { vendas: vendasNode, fonte: 'avantpro_anuncio' };
                        }
                        return null;
                    };
                    var vendedor = '';
                    var vendedorFonte = '';
                    var vendasInfo = procurarVendasAvantPro();
                    var vendas = vendasInfo ? vendasInfo.vendas : null;
                    var vendasFonte = vendasInfo ? vendasInfo.fonte : '';
                    var sellerNodes = [
                        '.ui-pdp-seller__header__title',
                        '.ui-pdp-seller__link-trigger',
                        '.ui-pdp-seller__nickname',
                        '.ui-pdp-official-store-label',
                        '[data-testid="seller-info"]',
                        '[data-testid="official-store-info"]'
                    ];
                    for (var s = 0; s < sellerNodes.length; s += 1) {
                        var node = document.querySelector(sellerNodes[s]);
                        if (node && node.textContent) {
                            var vendedorTexto = normalizarNomeVendedor(node.textContent.trim());
                            vendedorTexto = vendedorTexto.replace(/^(vendido\\s+por|loja\\s+oficial)\\s+/i, '').trim();
                            if (vendedorValido(vendedorTexto)) {
                                vendedor = vendedorTexto;
                                vendedorFonte = 'pagina_produto';
                                break;
                            }
                            if (vendedor) break;
                        }
                    }
                    if (!vendedor) {
                        vendedor = procurarVendedorEmTexto(html) || procurarVendedorEmTexto(decodedHtml);
                        if (vendedor) vendedorFonte = 'pagina_produto_fonte';
                    }
                    var avantFound = procurarDataAvantPro();
                    if (avantFound && avantFound.data_criacao) {
                        return { success: true, data_criacao: avantFound.data_criacao, vendedor: vendedor, vendedorFonte: vendedorFonte, vendas: vendas, vendasFonte: vendasFonte, source: 'avantpro_dom', avant_label: avantFound.label, url: location.href };
                    }
                    var dataHtml = procurarDataEmTexto(html) || procurarDataEmTexto(decodedHtml);
                    if (dataHtml) {
                        return { success: true, data_criacao: dataHtml, vendedor: vendedor, vendedorFonte: vendedorFonte, vendas: vendas, vendasFonte: vendasFonte, source: 'webview_codigo_fonte', url: location.href };
                    }
                    var scripts = Array.prototype.slice.call(document.querySelectorAll('script'));
                    for (var sc = 0; sc < scripts.length; sc += 1) {
                        var text = scripts[sc] && scripts[sc].textContent ? scripts[sc].textContent : '';
                        if (!text) continue;
                        if (!vendedor) {
                            vendedor = procurarVendedorEmTexto(text);
                            if (vendedor) vendedorFonte = 'pagina_produto_fonte';
                        }
                        var dataScript = procurarDataEmTexto(text);
                        if (dataScript) {
                            return { success: true, data_criacao: dataScript, vendedor: vendedor, vendedorFonte: vendedorFonte, vendas: coalesceVendas(vendas, null), vendasFonte: vendasFonte, source: 'script_texto', url: location.href };
                        }
                        var cleaned = normalizarTextoMl(text);
                            if (cleaned.charAt(0) === '{' || cleaned.charAt(0) === '[') {
                                try {
                                var jsonData = JSON.parse(cleaned);
                                var objFound = procurarEmObjeto(jsonData);
                                if (objFound && (objFound.data_criacao || objFound.vendedor || Number.isFinite(objFound.vendas))) {
                                    return { success: true, data_criacao: objFound.data_criacao, vendedor: vendedor || objFound.vendedor, vendedorFonte: vendedorFonte || (objFound.vendedor ? 'pagina_produto_fonte' : ''), vendas: coalesceVendas(vendas, null), vendasFonte: vendasFonte, source: 'script_json_parseado', url: location.href };
                                }
                            } catch (jsonErr) {}
                        }
                    }
                    var globals = [
                        window.__PRELOADED_STATE__,
                        window.__NEXT_DATA__,
                        window.__APOLLO_STATE__,
                        window.__INITIAL_STATE__,
                        window.__STATE__
                    ];
                    for (var gi = 0; gi < globals.length; gi += 1) {
                        var globalFound = procurarEmObjeto(globals[gi]);
                            if (globalFound && (globalFound.data_criacao || globalFound.vendedor || Number.isFinite(globalFound.vendas))) {
                                if (!vendedor) {
                                    var stackSeller = [globals[gi]];
                                    var seenSeller = [];
                                    while (stackSeller.length && !vendedor) {
                                    var curSeller = stackSeller.pop();
                                    if (!curSeller || typeof curSeller !== 'object') continue;
                                    if (seenSeller.indexOf(curSeller) >= 0) continue;
                                    seenSeller.push(curSeller);
                                    if (Array.isArray(curSeller)) {
                                        for (var si = 0; si < curSeller.length; si += 1) stackSeller.push(curSeller[si]);
                                    } else {
                                        for (var sk in curSeller) {
                                            if (!Object.prototype.hasOwnProperty.call(curSeller, sk)) continue;
                                            if (sellerKeys[sk] && curSeller[sk]) {
                                                vendedor = normalizarTextoMl(curSeller[sk]);
                                                vendedorFonte = 'pagina_produto_fonte';
                                                break;
                                            }
                                            if (curSeller[sk] && typeof curSeller[sk] === 'object') stackSeller.push(curSeller[sk]);
                                        }
                                        }
                                    }
                                }
                                if (!vendedor && globalFound.vendedor) {
                                    vendedor = globalFound.vendedor;
                                    vendedorFonte = 'pagina_produto_fonte';
                                }
                                return { success: true, data_criacao: globalFound.data_criacao, vendedor: vendedor, vendedorFonte: vendedorFonte, vendas: coalesceVendas(vendas, null), vendasFonte: vendasFonte, source: 'window_state', url: location.href };
                            }
                        }
                    return { success: !!(vendedor || hasNumeroVendas(vendas)), data_criacao: null, vendedor: vendedor, vendedorFonte: vendedorFonte, vendas: coalesceVendas(vendas, null), vendasFonte: vendasFonte, source: vendedor || hasNumeroVendas(vendas) ? (vendasFonte || 'webview_codigo_fonte') : null, url: location.href, title: document.title || '' };
                } catch (err) {
                    return { success: false, data_criacao: null, vendedor: null, vendas: null, error: err && err.message ? err.message : String(err), url: location.href };
                }
            })();
        `;

        const ML_SOURCE_SCAN_SCRIPT = `
            (function () {
                try {
                    var normalize = function (value) {
                        return String(value || '')
                            .replace(/\\\\u002F/g, '/')
                            .replace(/\\\\\\//g, '/')
                            .replace(/\\\\n/g, ' ')
                            .replace(/\\\\t/g, ' ')
                            .replace(/\\\\r/g, ' ')
                            .replace(/&quot;/g, '"')
                            .replace(/\\\\"/g, '"');
                    };
                    var unique = function (items) {
                        var seen = {};
                        var out = [];
                        for (var i = 0; i < items.length; i += 1) {
                            var value = String(items[i] || '');
                            if (!value || seen[value]) continue;
                            seen[value] = true;
                            out.push(value);
                        }
                        return out;
                    };
                    var html = normalize(document.documentElement && document.documentElement.outerHTML ? document.documentElement.outerHTML : '');
                    var bodyText = String(document.body && document.body.innerText ? document.body.innerText : '');
                    var fieldPatterns = [
                        /"date_created"\\s*:\\s*"([^"]+)"/gi,
                        /"dateCreated"\\s*:\\s*"([^"]+)"/gi,
                        /"start_time"\\s*:\\s*"([^"]+)"/gi,
                        /"startTime"\\s*:\\s*"([^"]+)"/gi,
                        /"creationDate"\\s*:\\s*"([^"]+)"/gi,
                        /"creation_date"\\s*:\\s*"([^"]+)"/gi,
                        /"listing_start_time"\\s*:\\s*"([^"]+)"/gi,
                        /"itemStartTime"\\s*:\\s*"([^"]+)"/gi
                    ];
                    var fields = [];
                    for (var p = 0; p < fieldPatterns.length; p += 1) {
                        var match;
                        while ((match = fieldPatterns[p].exec(html)) !== null) {
                            if (match[1]) fields.push(match[1]);
                        }
                    }
                    var dates = [];
                    var datePattern = /\\b20\\d{2}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?(?:Z|[+-]\\d{2}:?\\d{2})?\\b/g;
                    var dateMatch;
                    while ((dateMatch = datePattern.exec(html)) !== null) dates.push(dateMatch[0]);
                    var hints = [];
                    var hintPattern = /.{0,80}(?:date_created|dateCreated|start_time|startTime|creationDate|listing_start_time).{0,120}/gi;
                    var hintMatch;
                    while ((hintMatch = hintPattern.exec(html)) !== null) hints.push(String(hintMatch[0] || '').replace(/\\s+/g, ' '));
                    return {
                        success: true,
                        url: location.href,
                        title: document.title || '',
                        html_len: html.length,
                        text_preview: bodyText.replace(/\\s+/g, ' ').slice(0, 260),
                        verification: /account-verification|acesse sua conta|captcha|robot|verifica/i.test(location.href + ' ' + bodyText),
                        field_hits: unique(fields).slice(0, 40),
                        iso_date_hits: unique(dates).slice(0, 80),
                        source_hints: unique(hints).slice(0, 20)
                    };
                } catch (err) {
                    return { success: false, error: err && err.message ? err.message : String(err), url: location.href };
                }
            })();
        `;

        async function varrerCodigoFonteAnuncio(anuncio, botao) {
            if (!anuncio || !anuncio.url) return;
            const textoOriginal = botao ? botao.textContent : '';
            if (botao) {
                botao.disabled = true;
                botao.textContent = 'Varrendo...';
            }
            try {
                const dadosVisiveis = await extrairDadosAvantDoWebviewVisivel(anuncio).catch(() => null);
                if (dadosVisiveis) {
                    const atualizado = aplicarDadosAvantNoAnuncio(anuncio, dadosVisiveis);
                    const achouVisivel = atualizado.vendedor || atualizado.data || atualizado.vendas;
                    if (achouVisivel) {
                        const vendasTexto = anuncio && anuncio.vendas !== null && anuncio.vendas !== undefined ? anuncio.vendas : '';
                        mlPrimeiraPaginaStatusEl.textContent = `Dados da Avant Pro extraÃ­dos do card visÃ­vel: data ${formatarDataCriacao(anuncio.data_criacao) || 'nÃ£o encontrada'}, vendas ${vendasTexto || 0}.`;
                        if (botao) {
                            botao.textContent = 'ExtraÃ­do';
                            setTimeout(() => {
                                botao.textContent = textoOriginal || 'Extrair Avant';
                                botao.disabled = false;
                            }, 1800);
                        }
                        return;
                    }
                }

                const webview = criarMlWebviewOculto(98);
                await carregarUrlNoWebview(webview, anuncio.url);
                await new Promise(resolve => setTimeout(resolve, 1800));
                const scan = await webview.executeJavaScript(ML_SOURCE_SCAN_SCRIPT, true);
                const resultado = await webview.executeJavaScript(ML_DATE_EXTRACT_SCRIPT, true);

                const fonteVendedorResultado = resultado && (resultado.vendedorFonte || resultado.vendedor_fonte || 'pagina_produto');
                if (resultado && resultado.vendedor && deveAtualizarVendedor(anuncio.vendedor, anuncio.vendedorFonte, resultado.vendedor, fonteVendedorResultado)) {
                    anuncio.vendedor = resultado.vendedor;
                    anuncio.vendedorFonte = fonteVendedorResultado;
                    atualizarCelulaVendedor(anuncio, resultado.vendedor);
                }
                if (resultado && resultado.data_criacao) {
                    anuncio.data_criacao = resultado.data_criacao;
                    atualizarCelulaDataCriacao(anuncio, resultado.data_criacao);
                }
                const vendasResultado = parseNumeroVendas(resultado && resultado.vendas);
                const fonteVendasResultado = resultado && (resultado.vendasFonte || resultado.vendas_fonte || '');
                if (deveAtualizarVendas(anuncio.vendas, anuncio.vendasFonte, vendasResultado, fonteVendasResultado)) {
                    anuncio.vendas = vendasResultado;
                    anuncio.vendasFonte = fonteVendasResultado;
                    atualizarCelulaVendas(anuncio, vendasResultado);
                }

                const fields = (scan && scan.field_hits) || [];
                const dates = (scan && scan.iso_date_hits) || [];
                const hints = (scan && scan.source_hints) || [];
                const destino = scan && scan.url ? scan.url : anuncio.url;
                const bloqueio = scan && scan.verification ? ' Caiu em verificaÃ§Ã£o/login.' : '';
                const achado = resultado && resultado.data_criacao
                    ? ` Data encontrada: ${formatarDataCriacao(resultado.data_criacao)}.`
                    : ` Campos de data: ${fields.length}. Datas ISO: ${dates.length}. Trechos suspeitos: ${hints.length}.`;
                mlPrimeiraPaginaStatusEl.textContent = `Varredura do cÃ³digo-fonte concluÃ­da.${achado}${bloqueio} URL final: ${destino}`;

                if (botao) {
                    botao.textContent = resultado && resultado.data_criacao ? 'Achou data' : 'Sem data';
                    setTimeout(() => {
                        botao.textContent = textoOriginal || 'Varrer fonte';
                        botao.disabled = false;
                    }, 1800);
                }
            } catch (err) {
                mlPrimeiraPaginaStatusEl.textContent = `Falha ao varrer cÃ³digo-fonte: ${err && err.message ? err.message : err}`;
                if (botao) {
                    botao.textContent = textoOriginal || 'Varrer fonte';
                    botao.disabled = false;
                }
            }
        }

        function skuColetarSkusParaIa(opcoes = {}) {
            const skusAlvo = Array.isArray(opcoes.skus)
                ? new Set(opcoes.skus.map(skuChaveSku).filter(Boolean))
                : new Set();
            let linhas = skuFiltrarDados();
            if (skusAlvo.size) {
                const filtradas = linhas.filter(row => skusAlvo.has(skuChaveSku(skuObterSku(row))));
                linhas = filtradas.length
                    ? filtradas
                    : (Array.isArray(skuDados) ? skuDados.filter(row => skusAlvo.has(skuChaveSku(skuObterSku(row)))) : []);
            }
            const mapa = new Map();
            linhas.forEach(row => {
                const sku = skuObterSku(row);
                const chave = skuChaveSku(sku);
                if (!chave || mapa.has(chave)) return;
                mapa.set(chave, {
                    sku,
                    titulo: skuObterProduto(row),
                    produto: skuObterProduto(row),
                    descricao: String(row.descricao_ml || row.descricao || row['descriÃ§Ã£o'] || row.description || '').trim(),
                    pesquisa_1: skuObterPesquisa(row, 1),
                    pesquisa_2: skuObterPesquisa(row, 2),
                    pesquisa_3: skuObterPesquisa(row, 3),
                });
            });
            return Array.from(mapa.values());
        }

        async function skuGerarPesquisasComIa(opcoes = {}) {
            const chamadaSilenciosa = !!(opcoes && opcoes.silencioso);
            const botaoAcao = opcoes && opcoes.botao ? opcoes.botao : null;
            const itens = skuColetarSkusParaIa(opcoes);
            if (!itens.length) {
                const mensagem = 'Nenhum SKU disponivel para preencher com IA.';
                if (chamadaSilenciosa) return { success: false, total: 0, atualizadas: 0, error: mensagem };
                alert('Nenhum SKU disponÃ­vel para preencher com IA.');
                return;
            }

            const textoOriginal = botaoAcao ? botaoAcao.textContent : '';
            if (botaoAcao) {
                botaoAcao.disabled = true;
                botaoAcao.textContent = '...';
            }
            skuStatusEl.textContent = `Gerando pesquisas com IA para ${itens.length} SKU(s)...`;

            try {
                const response = await fetch('/api/favoritos/skus/pesquisas/ia', {
                    method: 'POST',
                    headers: headersJsonAutenticado(),
                    body: JSON.stringify({
                        itens,
                        loja: skuLojaSelecionada || null,
                        chunk_tamanho: 24,
                        sobrescrever: !!(opcoes && opcoes.sobrescrever)
                    })
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
                const resultados = Array.isArray(data.resultados) ? data.resultados : [];
                const mapaResultados = new Map(resultados.map(item => [skuChaveSku(item && item.sku), item]));

                let atualizadas = 0;
                skuDados.forEach(row => {
                    const chave = skuChaveSku(skuObterSku(row));
                    const item = mapaResultados.get(chave);
                    if (!item) return;
                    const p1 = String(item.pesquisa_1 || '').trim();
                    const p2 = String(item.pesquisa_2 || '').trim();
                    const p3 = String(item.pesquisa_3 || '').trim();
                    if (p1) row.pesquisa_1 = p1;
                    if (p2) row.pesquisa_2 = p2;
                    if (p3) row.pesquisa_3 = p3;
                    atualizadas += 1;
                });
                skuRenderizarTabela();
                skuStatusEl.textContent = atualizadas
                    ? `${atualizadas} SKU(s) atualizados pela IA (salvos no cadastro).`
                    : 'Nenhum SKU foi atualizado pela IA.';
                return { success: true, total: itens.length, atualizadas, resultados };
            } catch (err) {
                const mensagemErro = err && err.message ? err.message : String(err);
                skuStatusEl.textContent = `Erro ao preencher pesquisas com IA: ${mensagemErro}`;
                return { success: false, total: itens.length, atualizadas: 0, error: mensagemErro };
            } finally {
                if (botaoAcao) {
                    botaoAcao.disabled = false;
                    botaoAcao.textContent = textoOriginal || 'IA';
                }
            }
        }

        window.JKFavoritosPreencherPesquisasIA = function(opcoes = {}) {
            return skuGerarPesquisasComIa({ ...opcoes, silencioso: true });
        };

        async function tentarDataCriacaoPeloElectron(anuncios) {
            const pendentes = (anuncios || []).filter(item => item && item.url);
            if (!pendentes.length) return { datas: 0, vendedores: 0, vendas: 0 };

            const contagem = { datas: 0, vendedores: 0, vendas: 0 };
            const precisaRever = (anuncio) => !anuncio || !anuncio.url
                ? true
                : (!anuncio.vendedor || !anuncio.data_criacao || !hasNumeroVendas(anuncio.vendas));

            const executarPassada = async (itens) => {
                await executarComConcorrencia(itens, ML_API_WORKERS, async (anuncio) => {
                    try {
                        const itemId = anuncio.id || extrairItemIdAnuncio(anuncio.url);
                        if (!itemId) return;
                        anuncio.id = itemId;
                        const apiInfo = await consultarItemApiMercadoLivre(itemId);
                        if (apiInfo) {
                            const fonteApi = apiInfo.source || 'api';
                            if (apiInfo.titulo && apiInfo.titulo !== anuncio.titulo) {
                                anuncio.titulo = apiInfo.titulo;
                                atualizarCelulaTitulo(anuncio, apiInfo.titulo);
                            }
                            if (apiInfo.vendedor && deveAtualizarVendedor(anuncio.vendedor, anuncio.vendedorFonte, apiInfo.vendedor, fonteApi)) {
                                anuncio.vendedor = apiInfo.vendedor;
                                anuncio.vendedorFonte = fonteApi;
                                atualizarCelulaVendedor(anuncio, apiInfo.vendedor);
                                contagem.vendedores += 1;
                            }
                            if (apiInfo.data_criacao && !anuncio.data_criacao) {
                                anuncio.data_criacao = apiInfo.data_criacao;
                                atualizarCelulaDataCriacao(anuncio, apiInfo.data_criacao);
                                contagem.datas += 1;
                            }
                            const vendasApi = parseNumeroVendas(apiInfo.vendas);
                            if (deveAtualizarVendas(anuncio.vendas, anuncio.vendasFonte, vendasApi, fonteApi)) {
                                anuncio.vendas = vendasApi;
                                anuncio.vendasFonte = fonteApi;
                                atualizarCelulaVendas(anuncio, vendasApi);
                                contagem.vendas += 1;
                            }
                        }
                    } catch (err) {
                        console.warn('NÃ£o foi possÃ­vel consultar API ML:', anuncio.url, err);
                    }
                });

                const restantesApi = itens.filter(item => precisaRever(item));
                if (!restantesApi.length) {
                    return;
                }

                await executarComConcorrencia(restantesApi, ML_BROWSER_WORKERS, async (anuncio, workerIndex) => {
                    const webview = criarMlWebviewOculto(workerIndex);
                    if (typeof webview.executeJavaScript !== 'function') return;
                    try {
                        await carregarUrlNoWebview(webview, anuncio.url);
                        const resultado = await webview.executeJavaScript(ML_DATE_EXTRACT_SCRIPT, true);
                        const fonteVendedorResultado = resultado && (resultado.vendedorFonte || resultado.vendedor_fonte || 'pagina_produto');
                        if (resultado && deveAtualizarVendedor(anuncio.vendedor, anuncio.vendedorFonte, resultado.vendedor, fonteVendedorResultado)) {
                            anuncio.vendedor = resultado.vendedor;
                            anuncio.vendedorFonte = fonteVendedorResultado;
                            atualizarCelulaVendedor(anuncio, resultado.vendedor);
                            contagem.vendedores += 1;
                        }
                        if (resultado && resultado.data_criacao) {
                            anuncio.data_criacao = resultado.data_criacao;
                            atualizarCelulaDataCriacao(anuncio, resultado.data_criacao);
                            contagem.datas += 1;
                        }
                        const vendasResultado = parseNumeroVendas(resultado && resultado.vendas);
                        const fonteVendasResultado = resultado && (resultado.vendasFonte || resultado.vendas_fonte || '');
                        if (deveAtualizarVendas(anuncio.vendas, anuncio.vendasFonte, vendasResultado, fonteVendasResultado)) {
                            anuncio.vendas = vendasResultado;
                            anuncio.vendasFonte = fonteVendasResultado;
                            atualizarCelulaVendas(anuncio, vendasResultado);
                            contagem.vendas += 1;
                        }
                        if (precisaRever(anuncio) && window.electronAPI && typeof window.electronAPI.getMlBrowserItemInfo === 'function') {
                            const itemId = anuncio.id || extrairItemIdAnuncio(anuncio.url);
                            if (itemId) {
                                const browserInfo = await window.electronAPI.getMlBrowserItemInfo(itemId, anuncio.url);
                                if (browserInfo && browserInfo.vendedor && deveAtualizarVendedor(anuncio.vendedor, anuncio.vendedorFonte, browserInfo.vendedor, 'browser_item')) {
                                    anuncio.vendedor = browserInfo.vendedor;
                                    anuncio.vendedorFonte = 'browser_item';
                                    atualizarCelulaVendedor(anuncio, browserInfo.vendedor);
                                    contagem.vendedores += 1;
                                }
                                if (browserInfo && browserInfo.data_criacao && !anuncio.data_criacao) {
                                    anuncio.data_criacao = browserInfo.data_criacao;
                                    atualizarCelulaDataCriacao(anuncio, browserInfo.data_criacao);
                                    contagem.datas += 1;
                                }
                                const vendasBrowser = parseNumeroVendas(browserInfo && browserInfo.vendas);
                                const fonteVendasBrowser = browserInfo && (browserInfo.vendasFonte || browserInfo.vendas_fonte || browserInfo.source || 'browser_item');
                                if (deveAtualizarVendas(anuncio.vendas, anuncio.vendasFonte, vendasBrowser, fonteVendasBrowser)) {
                                    anuncio.vendasFonte = fonteVendasBrowser;
                                    anuncio.vendas = vendasBrowser;
                                    atualizarCelulaVendas(anuncio, vendasBrowser);
                                    contagem.vendas += 1;
                                }
                            }
                        }
                    } catch (err) {
                        console.warn('NÃ£o foi possÃ­vel ler data pelo Electron:', anuncio.url, err);
                    }
                });
            };

            await executarPassada(pendentes);

            const segundaChamada = pendentes.filter(item => precisaRever(item));
            if (!segundaChamada.length) {
                return contagem;
            }

            mlPrimeiraPaginaStatusEl && (mlPrimeiraPaginaStatusEl.textContent = 'Executando segunda verificaÃ§Ã£o de vendedor, data e vendas...');
            await esperar(1500);
            await executarPassada(segundaChamada);

            return contagem;
        }

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
                    : 'Aguardando vendas e data de criaÃ§Ã£o.';
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
            const idsSku = new Set(['SELLER_SKU', 'SKU', 'SELLER_CUSTOM_FIELD']);
            const skus = [];
            (lista || []).forEach(attr => {
                if (!attr || typeof attr !== 'object') return;
                const attrId = String(attr.id || attr.name || '').trim().toUpperCase();
                if (!idsSku.has(attrId)) return;
                dividirSkusMl(attr.value_name || attr.value_id || attr.value || '').forEach(sku => skus.push(sku));
            });
            return skus;
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

            [
                'sku', 'SKU', 'seller_sku', 'sellerSku', 'seller_custom_field',
                'sellerCustomField', 'codigo', 'codigo_sku', 'item_sku'
            ].forEach(campo => adicionar(anuncio && anuncio[campo]));

            extrairSkusAtributosMl(anuncio && anuncio.attributes).forEach(adicionar);
            extrairSkusAtributosMl(anuncio && anuncio.attribute_combinations).forEach(adicionar);

            (anuncio && anuncio.variations || []).forEach(variacao => {
                [
                    'sku', 'SKU', 'seller_sku', 'sellerSku', 'seller_custom_field',
                    'sellerCustomField', 'codigo'
                ].forEach(campo => adicionar(variacao && variacao[campo]));
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

        function chaveSkuSidebarMercadoLivre(sku) {
            return (normalizarSkuBuscaMl(sku) || String(sku || '').trim().toLowerCase()).toLowerCase();
        }

        function mesclarSkusAnunciosMercadoLivre(novos) {
            const listaNovos = Array.isArray(novos) ? novos : [];
            if (!listaNovos.length) return false;
            const mapa = new Map();
            (mlSkusAnunciosLojaAtual || []).forEach(item => {
                const chave = chaveSkuSidebarMercadoLivre(item && item.sku);
                if (chave) mapa.set(chave, item);
            });
            let mudou = false;
            listaNovos.forEach(item => {
                const sku = String(item && item.sku || '').trim();
                const chave = chaveSkuSidebarMercadoLivre(sku);
                if (!sku || !chave) return;
                const atual = mapa.get(chave);
                if (!atual) {
                    mapa.set(chave, item);
                    mudou = true;
                    return;
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
            });
            if (mudou) {
                mlSkusAnunciosLojaAtual = Array.from(mapa.values()).sort(compararItensSkuSidebar);
            }
            return mudou;
        }

        function agendarBuscaRemotaSkuSidebarMercadoLivre(termoBusca) {
            const termo = String(termoBusca || '').trim();
            const loja = String(mlSkuLojaSelecionada || skuLojaSelecionada || '').trim();
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
            const itens = (mlSkusAnunciosLojaAtual || [])
                .map((item, index) => ({
                    sku: String(item && item.sku || '').trim(),
                    titulo: String(item && item.titulo || '').trim(),
                    itemIds: Array.isArray(item && item.item_ids) ? item.item_ids : [],
                    totalAnuncios: Number(item && item.total_anuncios || 0),
                    pesquisa_1: String(item && (item.pesquisa_1 || item.pesquisa1 || item['Pesquisa 1']) || '').trim(),
                    pesquisa_2: String(item && (item.pesquisa_2 || item.pesquisa2 || item['Pesquisa 2']) || '').trim(),
                    pesquisa_3: String(item && (item.pesquisa_3 || item.pesquisa3 || item['Pesquisa 3']) || '').trim(),
                    index,
                    chave: chaveSkuSidebarMercadoLivre(item && item.sku)
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
            if (!termo) return itens;
            return (itens || []).filter(item => {
                const texto = normalizarTextoMl([
                    item.sku,
                    item.titulo,
                    mlSkuLojaSelecionada,
                    Array.isArray(item.itemIds) ? item.itemIds.join(' ') : ''
                ].join(' '));
                const termoSku = normalizarSkuBuscaMl(termoBase);
                const textoSku = normalizarSkuBuscaMl([
                    item.sku,
                    item.titulo,
                    mlSkuLojaSelecionada,
                    Array.isArray(item.itemIds) ? item.itemIds.join(' ') : ''
                ].join(' '));
                return texto.includes(termo) || (!!termoSku && textoSku.includes(termoSku));
            });
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
                    ? 'Limpar seleÃ§Ã£o'
                    : 'Selecionar todos';
            }
            if (mlSkuFazerFavoritosEl) {
                mlSkuFazerFavoritosEl.disabled = selecionados === 0 || mlFavoritosEmExecucao;
                mlSkuFazerFavoritosEl.textContent = mlFavoritosEmExecucao ? 'Fazendo...' : 'Fazer favoritos';
            }
            if (mlSkuCancelarFavoritosEl) {
                mlSkuCancelarFavoritosEl.classList.toggle('hidden', !mlFavoritosEmExecucao);
                mlSkuCancelarFavoritosEl.disabled = !mlFavoritosEmExecucao || mlFavoritosCancelado;
                mlSkuCancelarFavoritosEl.textContent = mlFavoritosCancelado ? 'Cancelando...' : 'Cancelar favoritos';
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
                mlWorkModalCloseEl.disabled = ativo;
            }
            if (mlWorkModalCancelEl) {
                mlWorkModalCancelEl.classList.toggle('hidden', !ativo);
                mlWorkModalCancelEl.disabled = !ativo || mlFavoritosCancelado;
                mlWorkModalCancelEl.textContent = mlFavoritosCancelado ? 'Cancelando...' : 'Cancelar favoritos';
            }
            atualizarAnimacaoAzulNoNavegadorMl(ativo);
            if (!ativo) atualizarStatusFavoritosNoNavegadorMl('', false);
        }

        function atualizarAnimacaoAzulNoNavegadorMl(ativo) {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') return;
            const script = ativo ? `
                (() => {
                    const STYLE_ID = 'jk-favoritos-blue-overlay-style';
                    const OVERLAY_ID = 'jk-favoritos-blue-overlay';
                    if (!document.getElementById(STYLE_ID)) {
                        const style = document.createElement('style');
                        style.id = STYLE_ID;
                        style.textContent = \`
                            #\${OVERLAY_ID} {
                                position: fixed;
                                inset: 0;
                                z-index: 2147483645;
                                pointer-events: none;
                                overflow: hidden;
                                background:
                                    radial-gradient(circle at 50% 38%, rgba(56, 189, 248, .14), transparent 58%),
                                    linear-gradient(135deg, rgba(37, 99, 235, .12), rgba(14, 165, 233, .18));
                                box-shadow: inset 0 0 78px rgba(37, 99, 235, .32);
                                animation: jkFavoritosBluePulse 1.6s ease-in-out infinite;
                            }
                            #\${OVERLAY_ID}::before,
                            #\${OVERLAY_ID}::after {
                                content: "";
                                position: absolute;
                                top: -30%;
                                left: -55%;
                                width: 42%;
                                height: 160%;
                                background: linear-gradient(105deg, transparent 0%, rgba(186, 230, 253, .18) 40%, rgba(255, 255, 255, .42) 50%, rgba(125, 211, 252, .18) 58%, transparent 74%);
                                filter: blur(1px);
                                transform: rotate(10deg) translateX(-120%);
                                animation: jkFavoritosBlueReflection 2.7s linear infinite;
                            }
                            #\${OVERLAY_ID}::after {
                                left: -75%;
                                width: 28%;
                                opacity: .62;
                                animation-duration: 3.5s;
                                animation-delay: .9s;
                            }
                            @keyframes jkFavoritosBluePulse {
                                0%, 100% { opacity: .32; }
                                50% { opacity: .72; }
                            }
                            @keyframes jkFavoritosBlueReflection {
                                0% { transform: rotate(10deg) translateX(-120%); }
                                100% { transform: rotate(10deg) translateX(430%); }
                            }
                        \`;
                        document.documentElement.appendChild(style);
                    }
                    if (!document.getElementById(OVERLAY_ID)) {
                        const overlay = document.createElement('div');
                        overlay.id = OVERLAY_ID;
                        document.documentElement.appendChild(overlay);
                    }
                    return true;
                })();
            ` : `
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
            if (!usarNavegadorMlNoShellElectron() || !mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') return;
            const texto = String(mensagem || '').trim();
            const script = ativo && texto ? `
                (() => {
                    const STYLE_ID = 'jk-favoritos-status-overlay-style';
                    const STATUS_ID = 'jk-favoritos-status-overlay';
                    if (!document.getElementById(STYLE_ID)) {
                        const style = document.createElement('style');
                        style.id = STYLE_ID;
                        style.textContent = \`
                            #\${STATUS_ID} {
                                position: fixed;
                                left: 50%;
                                top: 50%;
                                transform: translate(-50%, -50%);
                                z-index: 2147483647;
                                width: min(620px, calc(100vw - 48px));
                                box-sizing: border-box;
                                padding: 14px 16px;
                                border: 1px solid rgba(96, 165, 250, .82);
                                border-radius: 10px;
                                background: rgba(15, 23, 42, .96);
                                color: #fff;
                                box-shadow: 0 18px 48px rgba(15, 23, 42, .34), 0 0 34px rgba(14, 165, 233, .28);
                                pointer-events: none;
                                font-family: Inter, Arial, sans-serif;
                            }
                            #\${STATUS_ID} strong {
                                display: block;
                                font-size: 13px;
                                line-height: 1.25;
                                margin-bottom: 6px;
                            }
                            #\${STATUS_ID} span {
                                display: block;
                                font-size: 12px;
                                line-height: 1.4;
                            }
                        \`;
                        document.documentElement.appendChild(style);
                    }
                    let box = document.getElementById(STATUS_ID);
                    if (!box) {
                        box = document.createElement('div');
                        box.id = STATUS_ID;
                        document.documentElement.appendChild(box);
                    }
                    box.innerHTML = '';
                    const title = document.createElement('strong');
                    title.textContent = 'Fazendo Favorito! Aguarde...';
                    const text = document.createElement('span');
                    text.textContent = ${JSON.stringify(texto)};
                    box.appendChild(title);
                    box.appendChild(text);
                    return true;
                })();
            ` : `
                (() => {
                    document.getElementById('jk-favoritos-status-overlay')?.remove();
                    document.getElementById('jk-favoritos-status-overlay-style')?.remove();
                    return true;
                })();
            `;
            try {
                const resultado = mlWebviewEl.executeJavaScript(script);
                if (resultado && typeof resultado.catch === 'function') resultado.catch(() => {});
            } catch (_err) {}
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
            const cadastro = obterCadastroSkuFavoritos(item && item.sku);
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

        function editarPesquisasSkuSidebar(item, event) {
            if (event) {
                event.preventDefault();
                event.stopPropagation();
            }
            const sku = String(item && item.sku || '').trim();
            if (!sku) return;
            const cadastro = obterCadastroSkuFavoritos(sku);
            const lojaCadastro = cadastro ? skuObterLoja(cadastro) : '';
            if (lojaCadastro && skuNormalizarLoja(lojaCadastro) !== skuNormalizarLoja(skuLojaSelecionada)) {
                skuLojaSelecionada = lojaCadastro;
                skuSalvarPreferenciaLoja();
                skuRenderizarCardsLojas();
            }
            skuMostrarOcultos = true;
            if (skuFiltroEl) skuFiltroEl.value = sku;
            mudarAba('pesquisa');
            skuRenderizarTabela();
            if (skuStatusEl) {
                skuStatusEl.textContent = `Editando campos de pesquisa do SKU ${sku}.`;
            }
            setTimeout(() => {
                const primeiraLinha = skuBodyEl ? skuBodyEl.querySelector('tr') : null;
                if (primeiraLinha && typeof primeiraLinha.scrollIntoView === 'function') {
                    primeiraLinha.scrollIntoView({ behavior: 'smooth', block: 'center' });
                }
                const primeiroInput = primeiraLinha ? primeiraLinha.querySelector('.sku-pesquisa-input') : null;
                if (primeiroInput) {
                    primeiroInput.focus();
                    if (typeof primeiroInput.select === 'function') primeiroInput.select();
                }
            }, 80);
        }

        function renderizarSkuSidebarMercadoLivre() {
            if (!mlSkuSidebarListEl || !mlSkuSidebarEmptyEl || !mlSkuSidebarCountEl) return;
            const totalItens = montarItensSkuSidebarMercadoLivre();
            const itens = filtrarItensSkuSidebarMercadoLivre(totalItens);
            const limite = Math.max(ML_SKU_SIDEBAR_PAGE_SIZE, Number(mlSkuSidebarRenderLimit || ML_SKU_SIDEBAR_PAGE_SIZE));
            const itensRenderizados = itens.slice(0, limite);
            mlSkuSidebarListEl.innerHTML = '';
            mlSkuSidebarCountEl.textContent = totalItens.length
                ? (itens.length === totalItens.length ? `${totalItens.length} SKU(s)` : `${itens.length} de ${totalItens.length} SKU(s)`)
                : '';
            atualizarContadorSkuSidebarSelecionados(itens);
            mlSkuSidebarEmptyEl.textContent = mlSkuSidebarFiltro
                ? 'Nenhum SKU encontrado para esta busca.'
                : mlSkuLojaSelecionada
                ? 'Nenhum SKU encontrado nos anÃºncios ativos desta loja.'
                : 'Escolha uma loja integrada para carregar os SKUs dos anÃºncios ativos.';
            mlSkuSidebarEmptyEl.classList.toggle('hidden', itens.length > 0);
            if (!itens.length && mlSkuSidebarFiltro) {
                agendarBuscaRemotaSkuSidebarMercadoLivre(mlSkuSidebarFiltro);
            }
            agendarAtualizacaoPosicaoNavegadorMlShell();

            itensRenderizados.forEach(item => {
                const label = document.createElement('label');
                label.className = 'ml-sku-sidebar-item';

                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.checked = mlSkuSidebarSelecionados.has(item.chave);
                checkbox.addEventListener('change', () => {
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
                titulo.textContent = item.titulo || 'SKU encontrado em anÃºncio ativo';

                const meta = document.createElement('span');
                meta.className = 'ml-sku-sidebar-meta';
                const partes = [];
                if (mlSkuLojaSelecionada) partes.push(`Loja: ${mlSkuLojaSelecionada}`);
                if (item.totalAnuncios) partes.push(`${item.totalAnuncios} anÃºncio(s)`);
                if (item.itemIds && item.itemIds.length) partes.push(item.itemIds.slice(0, 2).join(', '));
                meta.textContent = partes.join(' | ');

                const editarBtn = document.createElement('button');
                editarBtn.type = 'button';
                editarBtn.className = 'ml-sku-sidebar-edit-btn';
                editarBtn.textContent = 'Editar campos de pesquisa';
                editarBtn.title = 'Editar Pesquisa 1, Pesquisa 2 e Pesquisa 3 deste SKU';
                editarBtn.addEventListener('click', (event) => editarPesquisasSkuSidebar(item, event));

                main.appendChild(codigo);
                main.appendChild(titulo);
                if (meta.textContent) main.appendChild(meta);
                main.appendChild(criarBalaoPesquisasSkuSidebar(item));
                main.appendChild(editarBtn);
                label.appendChild(checkbox);
                label.appendChild(main);
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

        function mostrarBalaoFavoritosStatus(mensagem, opcoes = {}) {
            if (mlFavoritosStatusEl) mlFavoritosStatusEl.textContent = mensagem || '';
            if (!mlFavoritosBalloonEl || !mlFavoritosBalloonTextEl) return;
            const deveOcultarNavegador = !!(opcoes.ocultarNavegador || opcoes.manterAcoes);
            if (deveOcultarNavegador) {
                ocultarNavegadorMlShellTemporariamente();
            } else {
                restaurarNavegadorMlShellSeVisivel();
            }
            if (mlFavoritosBalloonTimer) {
                clearTimeout(mlFavoritosBalloonTimer);
                mlFavoritosBalloonTimer = null;
            }
            mlFavoritosBalloonTextEl.textContent = mensagem || '';
            mlFavoritosBalloonEl.classList.toggle('is-error', !!opcoes.erro);
            mlFavoritosBalloonEl.classList.toggle('is-wide', !!opcoes.larga);
            if (!opcoes.manterAcoes && mlFavoritosBalloonActionsEl) {
                mlFavoritosBalloonActionsEl.innerHTML = '';
            }
            mlFavoritosBalloonEl.classList.remove('hidden');
            posicionarBalaoFavoritosStatus();
            if (opcoes.tempoMs) {
                mlFavoritosBalloonTimer = setTimeout(() => {
                    mlFavoritosBalloonEl.classList.add('hidden');
                    mlFavoritosBalloonTimer = null;
                    posicionarBalaoFavoritosStatus();
                    restaurarNavegadorMlShellSeVisivel();
                }, opcoes.tempoMs);
            }
        }

        function esconderBalaoFavoritosStatus() {
            if (mlFavoritosBalloonTimer) {
                clearTimeout(mlFavoritosBalloonTimer);
                mlFavoritosBalloonTimer = null;
            }
            if (mlFavoritosBalloonEl) mlFavoritosBalloonEl.classList.add('hidden');
            if (mlFavoritosBalloonEl) mlFavoritosBalloonEl.classList.remove('is-wide');
            if (mlFavoritosBalloonActionsEl) mlFavoritosBalloonActionsEl.innerHTML = '';
            posicionarBalaoFavoritosStatus();
            restaurarNavegadorMlShellSeVisivel();
        }

        function perguntarQuantidadePesquisasFavoritos() {
            if (!mlFavoritosBalloonEl || !mlFavoritosBalloonActionsEl) {
                const resposta = window.prompt('Quantas pesquisas deseja fazer por SKU? Digite 1, 2 ou 3.', '3');
                if (resposta === null) return Promise.resolve(null);
                const quantidade = Number(String(resposta).trim());
                if (![1, 2, 3].includes(quantidade)) {
                    alert('Informe apenas 1, 2 ou 3 pesquisas.');
                    return Promise.resolve(null);
                }
                return Promise.resolve(quantidade);
            }

            if (mlFavoritosPerguntaResolver) {
                mlFavoritosPerguntaResolver(null);
                mlFavoritosPerguntaResolver = null;
            }

            mlFavoritosBalloonActionsEl.innerHTML = '';
            return new Promise(resolve => {
                mlFavoritosPerguntaResolver = resolve;
                [1, 2, 3].forEach(qtd => {
                    const botao = document.createElement('button');
                    botao.type = 'button';
                    botao.textContent = `${qtd} pesquisa${qtd > 1 ? 's' : ''}`;
                    botao.addEventListener('click', () => {
                        mlFavoritosPerguntaResolver = null;
                        resolve(qtd);
                    });
                    mlFavoritosBalloonActionsEl.appendChild(botao);
                });
                const cancelar = document.createElement('button');
                cancelar.type = 'button';
                cancelar.textContent = 'Cancelar';
                cancelar.addEventListener('click', () => {
                    mlFavoritosPerguntaResolver = null;
                    esconderBalaoFavoritosStatus();
                    resolve(null);
                });
                mlFavoritosBalloonActionsEl.appendChild(cancelar);
                mostrarBalaoFavoritosStatus('Escolha quantas pesquisas deseja fazer para os SKUs selecionados.', {
                    manterAcoes: true
                });
            });
        }

        function nomePromocaoFavoritos(campanha) {
            const id = String(campanha && campanha.id || '').trim();
            const nome = String(campanha && (campanha.name || campanha.title || campanha.nome) || id || 'Promocao sem nome').trim();
            const status = String(campanha && campanha.status || '').trim();
            const quantidade = campanha && (campanha.eligible_count ?? campanha.items_count ?? campanha.item_count ?? campanha.total_items);
            const partes = [nome];
            if (status) partes.push(status);
            if (id) partes.push(id);
            if (quantidade !== null && quantidade !== undefined && quantidade !== '') partes.push(`${quantidade} item(ns)`);
            return partes.join(' - ');
        }

        function grupoPromocaoFavoritos(campanha) {
            const explicito = String(campanha && campanha.selection_group || '').trim().toLowerCase();
            if (explicito) return explicito;
            const tipo = String(campanha && (campanha.type || campanha.promotion_type) || '').trim().toUpperCase();
            const nome = String(campanha && (campanha.name || campanha.title) || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
            if (['SELLER_CAMPAIGN', 'SELLER_COUPON_CAMPAIGN'].includes(tipo)) return 'usuario';
            if (['SMART', 'PRICE_MATCHING', 'PRICE_MATCHING_MELI_ALL', 'MARKETPLACE_CAMPAIGN', 'PRE_NEGOTIATED'].includes(tipo)) return 'mercado_livre';
            if (['aceler', 'tarifa', 'menos tarifa', 'reduzimos', 'aumente suas vendas'].some(chave => nome.includes(chave))) return 'mercado_livre';
            return 'outras';
        }

        function tituloGrupoPromocaoFavoritos(grupo) {
            if (grupo === 'usuario') return 'Promocoes criadas por voce';
            if (grupo === 'mercado_livre') return 'Promocoes do Mercado Livre';
            return 'Outras promocoes ativas';
        }

        async function perguntarSimNaoPromocaoFavoritos() {
            if (!mlFavoritosBalloonEl || !mlFavoritosBalloonActionsEl) {
                return window.confirm('Deseja fazer o favorito colocando os anuncios em promocao?') ? true : false;
            }
            if (mlFavoritosPerguntaResolver) {
                mlFavoritosPerguntaResolver(null);
                mlFavoritosPerguntaResolver = null;
            }
            mlFavoritosBalloonActionsEl.innerHTML = '';
            return new Promise(resolve => {
                mlFavoritosPerguntaResolver = resolve;
                [
                    { texto: 'Sim', valor: true },
                    { texto: 'Nao', valor: false },
                    { texto: 'Cancelar', valor: null }
                ].forEach(item => {
                    const botao = document.createElement('button');
                    botao.type = 'button';
                    botao.textContent = item.texto;
                    botao.addEventListener('click', () => {
                        mlFavoritosPerguntaResolver = null;
                        if (item.valor === null) esconderBalaoFavoritosStatus();
                        resolve(item.valor);
                    });
                    mlFavoritosBalloonActionsEl.appendChild(botao);
                });
                mostrarBalaoFavoritosStatus('Deseja fazer o favorito colocando os anuncios em promocao?', {
                    manterAcoes: true
                });
            });
        }

        async function carregarPromocoesAtivasFavoritos(loja) {
            const query = new URLSearchParams({ loja: String(loja || '').trim() });
            const response = await fetch(`/api/favoritos/ml/promocoes?${query.toString()}`, {
                headers: obterAuthHeaders(),
                cache: 'no-store'
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data.detail || `HTTP ${response.status}`);
            }
            return Array.isArray(data.campaigns) ? data.campaigns : [];
        }

        function perguntarSelecionarPromocaoFavoritos(campanhas) {
            const lista = Array.isArray(campanhas) ? campanhas.filter(campanha => campanha && campanha.id) : [];
            if (!lista.length) return Promise.resolve(null);
            if (!mlFavoritosBalloonEl || !mlFavoritosBalloonActionsEl) {
                const texto = lista.map((campanha, index) => `${index + 1}. ${nomePromocaoFavoritos(campanha)}`).join('\n');
                const resposta = window.prompt(`Escolha a promocao ativa:\n${texto}`, '1');
                if (resposta === null) return Promise.resolve(null);
                const idx = Number(String(resposta).trim()) - 1;
                return Promise.resolve(lista[idx] || null);
            }
            if (mlFavoritosPerguntaResolver) {
                mlFavoritosPerguntaResolver(null);
                mlFavoritosPerguntaResolver = null;
            }
            mlFavoritosBalloonActionsEl.innerHTML = '';
            return new Promise(resolve => {
                mlFavoritosPerguntaResolver = resolve;
                const wrap = document.createElement('div');
                wrap.className = 'ml-favoritos-promo-list';
                const grupos = ['usuario', 'mercado_livre', 'outras'];
                grupos.forEach(grupo => {
                    const itens = lista.filter(campanha => grupoPromocaoFavoritos(campanha) === grupo);
                    if (!itens.length) return;
                    const grupoEl = document.createElement('div');
                    grupoEl.className = 'ml-favoritos-promo-group';
                    const titulo = document.createElement('div');
                    titulo.className = 'ml-favoritos-promo-group-title';
                    titulo.textContent = tituloGrupoPromocaoFavoritos(grupo);
                    grupoEl.appendChild(titulo);
                    itens.forEach(campanha => {
                        const botao = document.createElement('button');
                        botao.type = 'button';
                        botao.className = 'ml-favoritos-promo-button';
                        botao.textContent = nomePromocaoFavoritos(campanha);
                        botao.addEventListener('click', () => {
                            mlFavoritosPerguntaResolver = null;
                            resolve(campanha);
                        });
                        grupoEl.appendChild(botao);
                    });
                    wrap.appendChild(grupoEl);
                });
                mlFavoritosBalloonActionsEl.appendChild(wrap);
                const cancelar = document.createElement('button');
                cancelar.type = 'button';
                cancelar.textContent = 'Cancelar';
                cancelar.addEventListener('click', () => {
                    mlFavoritosPerguntaResolver = null;
                    esconderBalaoFavoritosStatus();
                    resolve(null);
                });
                mlFavoritosBalloonActionsEl.appendChild(cancelar);
                mostrarBalaoFavoritosStatus('Selecione a promocao ativa que deseja usar.', {
                    manterAcoes: true,
                    larga: true
                });
            });
        }

        function perguntarContinuarSemPromocaoFavoritos() {
            if (!mlFavoritosBalloonEl || !mlFavoritosBalloonActionsEl) {
                return Promise.resolve(window.confirm('Nenhuma promocao ativa foi encontrada. Deseja continuar sem promocao?') ? { usar_promocao: false } : null);
            }
            if (mlFavoritosPerguntaResolver) {
                mlFavoritosPerguntaResolver(null);
                mlFavoritosPerguntaResolver = null;
            }
            mlFavoritosBalloonActionsEl.innerHTML = '';
            return new Promise(resolve => {
                mlFavoritosPerguntaResolver = resolve;
                const continuar = document.createElement('button');
                continuar.type = 'button';
                continuar.textContent = 'Continuar sem promocao';
                continuar.addEventListener('click', () => {
                    mlFavoritosPerguntaResolver = null;
                    resolve({ usar_promocao: false });
                });
                const cancelar = document.createElement('button');
                cancelar.type = 'button';
                cancelar.textContent = 'Cancelar';
                cancelar.addEventListener('click', () => {
                    mlFavoritosPerguntaResolver = null;
                    esconderBalaoFavoritosStatus();
                    resolve(null);
                });
                mlFavoritosBalloonActionsEl.appendChild(continuar);
                mlFavoritosBalloonActionsEl.appendChild(cancelar);
                mostrarBalaoFavoritosStatus('Nenhuma promocao ativa foi encontrada para essa loja.', {
                    manterAcoes: true
                });
            });
        }

        function perguntarModoDescontoPromocaoFavoritos(campanha) {
            if (!mlFavoritosBalloonEl || !mlFavoritosBalloonActionsEl) {
                const fixa = window.confirm('Deseja usar % fixa? OK = % fixa, Cancelar = seguir sugestao do Mercado Livre.');
                if (!fixa) return Promise.resolve({ modo: 'sugestao_ml', percentual: null });
                const percentual = window.prompt('Informe a % fixa de desconto:', '21');
                if (percentual === null) return Promise.resolve(null);
                const numero = Number(String(percentual).replace(',', '.').trim());
                if (!Number.isFinite(numero) || numero <= 0) return Promise.resolve(null);
                return Promise.resolve({ modo: 'percentual_fixo', percentual: numero });
            }
            if (mlFavoritosPerguntaResolver) {
                mlFavoritosPerguntaResolver(null);
                mlFavoritosPerguntaResolver = null;
            }
            mlFavoritosBalloonActionsEl.innerHTML = '';
            return new Promise(resolve => {
                mlFavoritosPerguntaResolver = resolve;
                const linha = document.createElement('div');
                linha.className = 'ml-favoritos-promo-percent';
                const label = document.createElement('label');
                label.textContent = '% fixa';
                const input = document.createElement('input');
                input.type = 'number';
                input.min = '1';
                input.max = '99';
                input.step = '0.1';
                input.value = '21';
                linha.appendChild(label);
                linha.appendChild(input);
                mlFavoritosBalloonActionsEl.appendChild(linha);

                const fixa = document.createElement('button');
                fixa.type = 'button';
                fixa.textContent = 'Usar % fixa';
                fixa.addEventListener('click', () => {
                    const numero = Number(String(input.value || '').replace(',', '.').trim());
                    if (!Number.isFinite(numero) || numero <= 0) {
                        input.focus();
                        return;
                    }
                    mlFavoritosPerguntaResolver = null;
                    resolve({ modo: 'percentual_fixo', percentual: numero });
                });
                const sugestao = document.createElement('button');
                sugestao.type = 'button';
                sugestao.textContent = 'Seguir sugestao do Mercado Livre';
                sugestao.addEventListener('click', () => {
                    mlFavoritosPerguntaResolver = null;
                    resolve({ modo: 'sugestao_ml', percentual: null });
                });
                const cancelar = document.createElement('button');
                cancelar.type = 'button';
                cancelar.textContent = 'Cancelar';
                cancelar.addEventListener('click', () => {
                    mlFavoritosPerguntaResolver = null;
                    esconderBalaoFavoritosStatus();
                    resolve(null);
                });
                mlFavoritosBalloonActionsEl.appendChild(fixa);
                mlFavoritosBalloonActionsEl.appendChild(sugestao);
                mlFavoritosBalloonActionsEl.appendChild(cancelar);
                mostrarBalaoFavoritosStatus(`Promocao selecionada: ${nomePromocaoFavoritos(campanha)}. Escolha como calcular o desconto.`, {
                    manterAcoes: true,
                    larga: true
                });
                setTimeout(() => input.focus(), 50);
            });
        }

        async function perguntarOpcoesPromocaoFavoritos() {
            const usarPromocao = await perguntarSimNaoPromocaoFavoritos();
            if (usarPromocao === null) return null;
            if (!usarPromocao) return { usar_promocao: false };
            const loja = mlSkuLojaSelecionada || skuLojaSelecionada || '';
            if (!loja) {
                mostrarBalaoFavoritosStatus('Escolha uma loja do Mercado Livre antes de selecionar promocao.', {
                    erro: true,
                    tempoMs: 4500
                });
                return null;
            }
            mostrarBalaoFavoritosStatus(`Carregando promocoes ativas da loja ${loja}...`);
            let campanhas = [];
            try {
                campanhas = await carregarPromocoesAtivasFavoritos(loja);
            } catch (err) {
                mostrarBalaoFavoritosStatus(`Erro ao carregar promocoes: ${err && err.message ? err.message : err}`, {
                    erro: true
                });
                return null;
            }
            if (!campanhas.length) {
                return perguntarContinuarSemPromocaoFavoritos();
            }
            const campanha = await perguntarSelecionarPromocaoFavoritos(campanhas);
            if (!campanha) return null;
            const desconto = await perguntarModoDescontoPromocaoFavoritos(campanha);
            if (!desconto) return null;
            return {
                usar_promocao: true,
                campanha: {
                    id: String(campanha.id || '').trim(),
                    nome: String(campanha.name || campanha.title || campanha.id || '').trim(),
                    tipo: String(campanha.type || campanha.promotion_type || '').trim(),
                    status: String(campanha.status || '').trim()
                },
                desconto
            };
        }

        function resumoOpcoesPromocaoFavoritos(opcoes) {
            if (!opcoes || !opcoes.usar_promocao) return 'sem promocao';
            const nome = opcoes.campanha && (opcoes.campanha.nome || opcoes.campanha.id) || 'promocao selecionada';
            if (opcoes.desconto && opcoes.desconto.modo === 'percentual_fixo') {
                return `${nome}, % fixa ${opcoes.desconto.percentual}%`;
            }
            return `${nome}, sugestao do Mercado Livre`;
        }

        function criarErroFavoritosCancelado() {
            const erro = new Error('Processo de favoritos cancelado pelo usuario.');
            erro.canceladoFavoritos = true;
            return erro;
        }

        function verificarCancelamentoFavoritos() {
            if (mlFavoritosCancelado) {
                throw criarErroFavoritosCancelado();
            }
        }

        function sinalFavoritosAtual() {
            return mlFavoritosAbortController ? mlFavoritosAbortController.signal : undefined;
        }

        function cancelarFavoritosEmExecucao() {
            if (!mlFavoritosEmExecucao) return;
            mlFavoritosCancelado = true;
            if (mlFavoritosAbortController) {
                try {
                    mlFavoritosAbortController.abort();
                } catch (_err) {}
            }
            atualizarContadorSkuSidebarSelecionados();
            mostrarBalaoFavoritosStatus('Cancelando favoritos... a etapa atual sera interrompida assim que possivel.', {
                tempoMs: 4500
            });
        }

        function obterCadastroSkuFavoritos(sku) {
            const chave = skuChaveSku(sku);
            if (!chave || !Array.isArray(skuDados)) return null;
            const candidatos = skuDados.filter(row => skuChaveSku(skuObterSku(row)) === chave);
            if (!candidatos.length) return null;
            const lojaAlvo = skuNormalizarLoja(mlSkuLojaSelecionada || skuLojaSelecionada || '');
            return candidatos.find(row => lojaAlvo && skuNormalizarLoja(skuObterLoja(row)) === lojaAlvo) || candidatos[0];
        }

        function montarPesquisasFavoritosSku(item, quantidade) {
            const cadastro = obterCadastroSkuFavoritos(item.sku);
            const descricaoCadastro = cadastro
                ? String(cadastro.descricao_ml || cadastro.descricao || cadastro['descriÃ§Ã£o'] || cadastro.description || '').trim()
                : '';
            const termos = [];
            for (let numero = 1; numero <= quantidade; numero += 1) {
                const termo = String(cadastro ? skuObterPesquisa(cadastro, numero) : '').trim();
                if (!termo) continue;
                if (!termos.some(t => t.termo.toLowerCase() === termo.toLowerCase())) {
                    termos.push({ campo: numero, termo });
                }
            }
            return {
                sku: item.sku,
                titulo: (cadastro && skuObterProduto(cadastro)) || item.titulo || '',
                descricao: descricaoCadastro,
                cadastro,
                termos
            };
        }

        async function buscarAnunciosFavoritosPorTermoAvant(termo) {
            if (!hasInternalBrowserApi && !usarNavegadorMlNoShellElectron()) return [];
            verificarCancelamentoFavoritos();
            mlUrlInput.value = construirUrlPesquisaMercadoLivre(termo);
            const abriu = await abrirMercadoLivreNoPrograma({
                titulo: 'Fazendo Favorito! Aguarde...',
                subtitulo: `Pesquisa: ${termo}`,
                mostrarFavoritos: true
            });
            verificarCancelamentoFavoritos();
            if (!abriu) return [];
            await esperar(1400);
            verificarCancelamentoFavoritos();

            let resultado = await extrairAnunciosWebviewVisivel({
                clicarAvant: true,
                forcarCliqueAvant: true,
                fastLinks: false,
                timeoutMs: 10000,
                aguardarAposCliqueAvant: 1800
            }).catch(() => null);
            verificarCancelamentoFavoritos();
            let anunciosAvant = (resultado && resultado.anuncios) || [];
            if (resultado && resultado.needsLogin) {
                throw new Error('O Mercado Livre pediu login/verificacao no quadro interno.');
            }

            const temVendasAvant = anunciosAvant.some(item => fonteVendasAvantPro(item && (item.vendasFonte || item.vendas_fonte)));
            if (!temVendasAvant) {
                await esperar(1800);
                verificarCancelamentoFavoritos();
                const retry = await extrairAnunciosWebviewVisivel({
                    clicarAvant: true,
                    forcarCliqueAvant: true,
                    fastLinks: false,
                    timeoutMs: 10000,
                    aguardarAposCliqueAvant: 2200
                }).catch(() => null);
                verificarCancelamentoFavoritos();
                anunciosAvant = mesclarAnunciosAvant(anunciosAvant, (retry && retry.anuncios) || []);
            }

            const rolados = await coletarDadosAvantComRolagem().catch(() => []);
            verificarCancelamentoFavoritos();
            anunciosAvant = mesclarAnunciosAvant(anunciosAvant, rolados);
            const temVendasDepoisRolagem = anunciosAvant.some(item => fonteVendasAvantPro(item && (item.vendasFonte || item.vendas_fonte)));
            if (!temVendasDepoisRolagem) {
                await esperar(2600);
                verificarCancelamentoFavoritos();
                const retryTardio = await extrairAnunciosWebviewVisivel({
                    clicarAvant: true,
                    forcarCliqueAvant: true,
                    fastLinks: false,
                    timeoutMs: 10000,
                    aguardarAposCliqueAvant: 2600
                }).catch(() => null);
                verificarCancelamentoFavoritos();
                anunciosAvant = mesclarAnunciosAvant(anunciosAvant, (retryTardio && retryTardio.anuncios) || []);
            }
            return anunciosAvant.map(item => {
                const fonteVendas = normalizarFonte(item && (item.vendasFonte || item.vendas_fonte || ''));
                const vendas = fonteVendasAvantPro(fonteVendas) ? parseNumeroVendas(item && item.vendas) : null;
                return {
                    ...item,
                    origem_dados: 'avantpro',
                    vendas,
                    vendasFonte: vendas !== null ? fonteVendas : '',
                    vendas_fonte: vendas !== null ? fonteVendas : ''
                };
            });
        }

        async function buscarAnunciosFavoritosPorTermo(termo) {
            if (hasInternalBrowserApi || usarNavegadorMlNoShellElectron()) {
                try {
                    mostrarBalaoFavoritosStatus(`Abrindo "${termo}" e lendo vendas pelo Avant Pro...`);
                    const anunciosAvant = await buscarAnunciosFavoritosPorTermoAvant(termo);
                    verificarCancelamentoFavoritos();
                    if (anunciosAvant.length) {
                        return anunciosAvant;
                    }
                    mostrarBalaoFavoritosStatus(`Avant Pro nao retornou anuncios para "${termo}". Usando fallback do Mercado Livre...`);
                } catch (err) {
                    if (mlFavoritosCancelado || (err && err.canceladoFavoritos)) throw err;
                    mostrarBalaoFavoritosStatus(`Nao consegui ler o Avant Pro para "${termo}". Usando fallback do Mercado Livre. ${err && err.message ? err.message : ''}`);
                }
            }

            const response = await fetch('/api/favoritos/ml/primeira-pagina', {
                method: 'POST',
                headers: headersJsonAutenticado(),
                signal: sinalFavoritosAtual(),
                body: JSON.stringify({ termo })
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
            return (Array.isArray(data.anuncios) ? data.anuncios : []).map(item => ({
                ...item,
                origem_dados: item.origem_dados || 'mercadolivre_backend'
            }));
        }

        function chaveAnuncioFavoritos(anuncio) {
            const id = extrairItemIdAnuncio(anuncio && (anuncio.id || anuncio.url)) || String(anuncio && anuncio.id || '').trim().toUpperCase().replace(/-/g, '');
            if (id) return id;
            return String(anuncio && anuncio.url || '').split('#')[0].trim().toLowerCase();
        }

        function chavesAnuncioFavoritos(anuncio) {
            if (!anuncio) return [];
            const chaves = new Set();
            const id = extrairItemIdAnuncio(anuncio.id || anuncio.url) || String(anuncio.id || '').trim().toUpperCase().replace(/-/g, '');
            const url = String(anuncio.url || '').split('#')[0].trim().toLowerCase();
            const chave = chaveAnuncioFavoritos(anuncio);
            if (id) chaves.add(`id:${id}`);
            if (url) chaves.add(`url:${url}`);
            if (chave) chaves.add(`chave:${chave}`);
            return Array.from(chaves);
        }

        function normalizarAnuncioFavoritosPesquisa(anuncio, sku, pesquisa) {
            const id = extrairItemIdAnuncio(anuncio && (anuncio.id || anuncio.url || anuncio.permalink || anuncio.link))
                || String(anuncio && anuncio.id || '').trim().toUpperCase().replace(/-/g, '');
            const vendasFonteOriginal = normalizarFonte(anuncio && (anuncio.vendasFonte || anuncio.vendas_fonte || 'api_search'));
            const vendasFonte = fonteVendasConfiavel(vendasFonteOriginal) ? vendasFonteOriginal : '';
            const vendasAvant = fonteVendasConfiavel(vendasFonte) ? parseNumeroVendas(anuncio && anuncio.vendas) : null;
            const imagem = obterImagemAnuncioFavoritos(anuncio);
            const precos = obterPrecosAnuncioFavoritos(anuncio);
            const descricao = obterDescricaoAnuncioFavoritosIa(anuncio);
            return {
                ...anuncio,
                id,
                sku_favorito: sku,
                titulo: String(anuncio && (anuncio.titulo || anuncio.title) || '').trim(),
                descricao,
                url: String(anuncio && (anuncio.url || anuncio.permalink || anuncio.link) || '').trim(),
                imagem,
                thumbnail: imagem || (anuncio && anuncio.thumbnail) || '',
                preco: precos.preco !== null ? precos.preco : (anuncio && (anuncio.preco ?? anuncio.price ?? '')),
                price: precos.promocional !== null ? precos.promocional : (precos.preco !== null ? precos.preco : (anuncio && (anuncio.price ?? anuncio.preco ?? ''))),
                preco_promocional: precos.promocional !== null ? precos.promocional : (anuncio && (anuncio.preco_promocional ?? anuncio.promotional_price ?? '')),
                discount_pct: precos.desconto || '',
                fonte_preco: fontePrecoFavoritos(anuncio),
                parcelamento_sem_juros: obterParcelamentoSemJurosFavoritos(anuncio),
                tipo_anuncio: obterTipoAnuncioFavoritos(anuncio),
                listing_type_id: anuncio && (anuncio.listing_type_id || anuncio.listingTypeId || ''),
                listing_type_name: anuncio && (anuncio.listing_type_name || anuncio.tipo_anuncio || ''),
                shipping: anuncio && (anuncio.shipping || anuncio.shipping_info || anuncio.shippingInfo || null),
                logistic_type: anuncio && (anuncio.logistic_type || anuncio.logisticType || anuncio.shipping_logistic_type || ''),
                shipping_mode: anuncio && (anuncio.shipping_mode || anuncio.shippingMode || ''),
                is_full: temIndicadorFullFavoritos(anuncio) ? obterFullAnuncioFavoritos(anuncio) : '',
                condicao: obterCondicaoAnuncioFavoritos(anuncio),
                condition: obterCondicaoAnuncioFavoritos(anuncio),
                item_condition: obterCondicaoAnuncioFavoritos(anuncio),
                vendedor: String(anuncio && anuncio.vendedor || '').trim(),
                vendedorFonte: normalizarFonte(anuncio && (anuncio.vendedorFonte || anuncio.vendedor_fonte || (anuncio.vendedor ? 'api_search' : ''))),
                vendas: vendasAvant,
                vendasFonte,
                data_criacao: anuncio && (anuncio.data_criacao || anuncio.date_created || ''),
                posicao: Number(anuncio && anuncio.posicao) || 9999,
                pesquisas_origem: [pesquisa.termo],
                campos_origem: [`Pesquisa ${pesquisa.campo}`]
            };
        }

        function deduplicarAnunciosFavoritos(anuncios) {
            const mapa = new Map();
            anuncios.forEach(anuncio => {
                const chave = chaveAnuncioFavoritos(anuncio);
                if (!chave) return;
                const atual = mapa.get(chave);
                if (!atual) {
                    mapa.set(chave, { ...anuncio });
                    return;
                }
                if (!atual.titulo && anuncio.titulo) atual.titulo = anuncio.titulo;
                if (!atual.url && anuncio.url) atual.url = anuncio.url;
                if (!atual.id && anuncio.id) atual.id = anuncio.id;
                const descricao = obterDescricaoAnuncioFavoritosIa(anuncio);
                if (!atual.descricao && descricao) atual.descricao = descricao;
                preencherImagemAnuncioFavoritos(atual, anuncio);
                preencherPrecoAnuncioFavoritos(atual, anuncio);
                preencherTipoAnuncioFavoritos(atual, anuncio);
                preencherCondicaoAnuncioFavoritos(atual, anuncio);
                if (!atual.data_criacao && anuncio.data_criacao) atual.data_criacao = anuncio.data_criacao;
                if (deveAtualizarVendedor(atual.vendedor, atual.vendedorFonte, anuncio.vendedor, anuncio.vendedorFonte)) {
                    atual.vendedor = anuncio.vendedor;
                    atual.vendedorFonte = anuncio.vendedorFonte;
                }
                if (deveAtualizarVendas(atual.vendas, atual.vendasFonte, anuncio.vendas, anuncio.vendasFonte)) {
                    atual.vendas = anuncio.vendas;
                    atual.vendasFonte = anuncio.vendasFonte;
                }
                atual.posicao = Math.min(Number(atual.posicao) || 9999, Number(anuncio.posicao) || 9999);
                (anuncio.pesquisas_origem || []).forEach(termo => {
                    if (termo && !atual.pesquisas_origem.includes(termo)) atual.pesquisas_origem.push(termo);
                });
                (anuncio.campos_origem || []).forEach(campo => {
                    if (campo && !atual.campos_origem.includes(campo)) atual.campos_origem.push(campo);
                });
            });
            return Array.from(mapa.values());
        }

        async function enriquecerAnunciosFavoritosRanking(anuncios) {
            const pendentes = (anuncios || []).filter(item => item && (item.url || item.id));
            if (!pendentes.length) return;
            let resultados = [];
            try {
                const response = await fetch('/api/favoritos/ml/enriquecer-datas', {
                    method: 'POST',
                    headers: headersJsonAutenticado(),
                    signal: sinalFavoritosAtual(),
                    body: JSON.stringify({
                        max_anuncios: Math.min(200, pendentes.length),
                        anuncios: pendentes.map(item => ({ id: item.id || '', url: item.url || '' }))
                    })
                });
                if (response.ok) {
                    const data = await response.json();
                    resultados = Array.isArray(data.resultados) ? data.resultados : [];
                }
            } catch (err) {
                if (mlFavoritosCancelado || (err && err.name === 'AbortError')) throw err;
                console.warn('Nao foi possivel enriquecer ranking pelo backend:', err);
            }
            const mapa = new Map();
            anuncios.forEach(item => {
                chavesAnuncioFavoritos(item).forEach(chave => mapa.set(chave, item));
            });
            resultados.forEach(info => {
                const alvo = chavesAnuncioFavoritos(info).map(chave => mapa.get(chave)).find(Boolean);
                if (!alvo) return;
                preencherImagemAnuncioFavoritos(alvo, info);
                preencherTipoAnuncioFavoritos(alvo, info);
                preencherCondicaoAnuncioFavoritos(alvo, info);
                if (info.data_criacao) alvo.data_criacao = info.data_criacao;
                const vendedor = String(info.vendedor || '').trim();
                const fonteVendedor = normalizarFonte(info.fonte_vendedor || 'pagina_produto');
                if (deveAtualizarVendedor(alvo.vendedor, alvo.vendedorFonte, vendedor, fonteVendedor)) {
                    alvo.vendedor = vendedor;
                    alvo.vendedorFonte = fonteVendedor;
                }
                const vendas = parseNumeroVendas(info.vendas);
                const fonteVendas = normalizarFonte(info.fonte_vendas || '');
                if (deveAtualizarVendas(alvo.vendas, alvo.vendasFonte, vendas, fonteVendas)) {
                    alvo.vendas = vendas;
                    alvo.vendasFonte = fonteVendas;
                }
            });
            const semComplemento = pendentes.filter(item => item && (!vendedorValido(item.vendedor) || !obterImagemAnuncioFavoritos(item) || precisaComplementoPrecoFavoritos(item) || !obterTipoAnuncioFavoritos(item) || fullAnuncioDesconhecidoFavoritos(item) || !obterCondicaoAnuncioFavoritos(item)));
            if (!semComplemento.length) return;
            await executarComConcorrencia(semComplemento, Math.min(ML_API_WORKERS, 10), async (alvo) => {
                verificarCancelamentoFavoritos();
                const itemId = alvo.id || extrairItemIdAnuncio(alvo.url);
                if (!itemId) return;
                try {
                    const apiInfo = await consultarItemApiMercadoLivre(itemId);
                    if (!apiInfo) return;
                    preencherImagemAnuncioFavoritos(alvo, apiInfo);
                    preencherPrecoAnuncioFavoritos(alvo, apiInfo);
                    preencherTipoAnuncioFavoritos(alvo, apiInfo);
                    preencherCondicaoAnuncioFavoritos(alvo, apiInfo);
                    const vendedorApi = normalizarNomeVendedor(apiInfo.vendedor || '');
                    const fonteApi = normalizarFonte(apiInfo.source || 'api');
                    if (deveAtualizarVendedor(alvo.vendedor, alvo.vendedorFonte, vendedorApi, fonteApi)) {
                        alvo.vendedor = vendedorApi;
                        alvo.vendedorFonte = fonteApi;
                    }
                    if (apiInfo.data_criacao && !alvo.data_criacao) {
                        alvo.data_criacao = apiInfo.data_criacao;
                    }
                } catch (err) {
                    if (mlFavoritosCancelado || (err && err.canceladoFavoritos)) throw err;
                    console.warn('Nao foi possivel preencher vendedor do ranking pelo MLB:', itemId, err);
                }
            });
        }

        async function complementarTiposRankingFavoritos(sku, anuncios) {
            const chaveSku = skuChaveSku(sku);
            const pendentes = (Array.isArray(anuncios) ? anuncios : [])
                .filter(anuncio => anuncio && (!obterTipoAnuncioFavoritos(anuncio) || fullAnuncioDesconhecidoFavoritos(anuncio)) && !anuncio._tipoAnuncioVerificado && (anuncio.id || anuncio.url));
            if (!chaveSku || !pendentes.length || mlFavoritosTiposRankingEmExecucao.has(chaveSku)) return;
            mlFavoritosTiposRankingEmExecucao.add(chaveSku);
            let alterou = false;
            try {
                let resultados = [];
                try {
                    const response = await fetch('/api/favoritos/ml/enriquecer-datas', {
                        method: 'POST',
                        headers: headersJsonAutenticado(),
                        body: JSON.stringify({
                            max_anuncios: Math.min(200, pendentes.length),
                            anuncios: pendentes.map(item => ({ id: item.id || '', url: item.url || '' }))
                        })
                    });
                    if (response.ok) {
                        const data = await response.json();
                        resultados = Array.isArray(data.resultados) ? data.resultados : [];
                    }
                } catch (err) {
                    console.warn('Nao foi possivel completar tipo do ranking pelo backend:', err);
                }

                const mapa = new Map();
                pendentes.forEach(item => chavesAnuncioFavoritos(item).forEach(chave => mapa.set(chave, item)));
                resultados.forEach(info => {
                    const alvo = chavesAnuncioFavoritos(info).map(chave => mapa.get(chave)).find(Boolean);
                    if (alvo && preencherTipoAnuncioFavoritos(alvo, info)) alterou = true;
                });

                const aindaPendentes = pendentes.filter(item => !obterTipoAnuncioFavoritos(item) || fullAnuncioDesconhecidoFavoritos(item));
                await executarComConcorrencia(aindaPendentes, Math.min(ML_API_WORKERS, 8), async (alvo) => {
                    const itemId = alvo.id || extrairItemIdAnuncio(alvo.url);
                    if (!itemId) return;
                    try {
                        const apiInfo = await consultarItemApiMercadoLivre(itemId);
                        if (apiInfo && preencherTipoAnuncioFavoritos(alvo, apiInfo)) alterou = true;
                    } catch (err) {
                        console.warn('Nao foi possivel completar tipo do anuncio:', itemId, err);
                    } finally {
                        alvo._tipoAnuncioVerificado = true;
                    }
                });
            } finally {
                mlFavoritosTiposRankingEmExecucao.delete(chaveSku);
            }
            if (alterou) {
                const historico = lerHistoricoFavoritos();
                if (historico.length) salvarHistoricoFavoritos(historico);
                if (skuChaveSku(favMlSkuSelecionado) === chaveSku) {
                    renderizarFavoritosOutrosAnuncios(favMlSkuSelecionado);
                }
                if (document.getElementById('aba-historico')?.classList.contains('active')) {
                    renderizarHistoricoFavoritos();
                }
            }
        }

        function ordenarAnunciosFavoritosRanking(anuncios) {
            return filtrarAnunciosIgnoradosRanking(anuncios)
                .map((anuncio, index) => ({ anuncio, index, metrica: calcularMetricasMediaVendas(anuncio) }))
                .sort((a, b) => {
                    const mediaA = Number.isFinite(a.metrica.media) ? a.metrica.media : -1;
                    const mediaB = Number.isFinite(b.metrica.media) ? b.metrica.media : -1;
                    if (Math.abs(mediaB - mediaA) > 0.0001) return mediaB - mediaA;
                    const vendasB = Number.isFinite(b.metrica.vendas) ? b.metrica.vendas : -1;
                    const vendasA = Number.isFinite(a.metrica.vendas) ? a.metrica.vendas : -1;
                    if (vendasB !== vendasA) return vendasB - vendasA;
                    const posicaoA = Number(a.anuncio.posicao) || 9999;
                    const posicaoB = Number(b.anuncio.posicao) || 9999;
                    if (posicaoA !== posicaoB) return posicaoA - posicaoB;
                    return a.index - b.index;
                })
                .map(item => {
                    item.anuncio.media_vendas_mensal = Number.isFinite(item.metrica.media) ? item.metrica.media : null;
                    item.anuncio.meses_desde_criacao = Number.isFinite(item.metrica.meses) ? item.metrica.meses : null;
                    return item.anuncio;
                });
        }

        function normalizarIdAnuncioFavoritosIa(anuncio) {
            return (extrairItemIdAnuncio(anuncio && (anuncio.id || anuncio.mlb || anuncio.url || anuncio.permalink || anuncio.link))
                || String(anuncio && (anuncio.id || anuncio.mlb || '') || '').trim().toUpperCase().replace(/-/g, ''));
        }

        function obterDescricaoAnuncioFavoritosIa(anuncio) {
            if (!anuncio || typeof anuncio !== 'object') return '';
            const candidatos = [
                anuncio.descricao,
                anuncio.descricao_ml,
                anuncio.description,
                anuncio.description_plain,
                anuncio.plain_text,
                anuncio.text,
                anuncio.subtitle
            ];
            for (const valor of candidatos) {
                if (typeof valor === 'string' && valor.trim()) {
                    return valor.replace(/\s+/g, ' ').trim();
                }
            }
            const info = anuncio.description_info || anuncio.descriptionInfo || anuncio.descricao_info;
            if (info && typeof info === 'object') {
                for (const chave of ['plain_text', 'text', 'content', 'description']) {
                    const valor = info[chave];
                    if (typeof valor === 'string' && valor.trim()) {
                        return valor.replace(/\s+/g, ' ').trim();
                    }
                    if (valor && typeof valor === 'object') {
                        for (const subchave of ['plain_text', 'text', 'content']) {
                            const subvalor = valor[subchave];
                            if (typeof subvalor === 'string' && subvalor.trim()) {
                                return subvalor.replace(/\s+/g, ' ').trim();
                            }
                        }
                    }
                }
            }
            return '';
        }

        async function filtrarAnunciosFavoritosPorIa(info, anuncios) {
            const lista = Array.isArray(anuncios) ? anuncios.filter(Boolean) : [];
            return { anuncios: lista, removidos: [], removidosTotal: 0 };
        }

        function renderizarFavoritosPesquisaResultados(grupos) {
            if (!mlFavoritosPanelEl || !mlFavoritosListEl || !mlFavoritosEmptyEl) return;
            mlFavoritosPanelEl.classList.remove('hidden');
            mlFavoritosListEl.innerHTML = '';
            mlFavoritosEmptyEl.classList.toggle('hidden', (grupos || []).length > 0);

            (grupos || []).forEach(grupo => {
                const card = document.createElement('section');
                card.className = 'ml-favoritos-sku-card';

                const head = document.createElement('div');
                head.className = 'ml-favoritos-sku-head';
                const titulo = document.createElement('h4');
                titulo.className = 'ml-favoritos-sku-title';
                titulo.textContent = `${grupo.sku}${grupo.titulo ? ` - ${grupo.titulo}` : ''}`;
                const termos = document.createElement('div');
                termos.className = 'ml-favoritos-termos';
                termos.textContent = grupo.termos && grupo.termos.length
                    ? `Pesquisas usadas: ${grupo.termos.map(item => `${item.campo}: ${item.termo}`).join(' | ')}`
                    : 'Nenhuma pesquisa preenchida para este SKU.';
                head.appendChild(titulo);
                head.appendChild(termos);
                card.appendChild(head);

                const anunciosVisiveis = filtrarAnunciosIgnoradosRanking(grupo.anuncios);
                if (grupo.erro || !anunciosVisiveis.length) {
                    const empty = document.createElement('div');
                    empty.className = 'muted ml-favoritos-empty';
                    empty.textContent = grupo.erro || (
                        Array.isArray(grupo.anuncios) && grupo.anuncios.length
                            ? 'Todos os anuncios encontrados para este SKU pertencem a vendedores ignorados.'
                            : 'Nenhum anuncio encontrado para as pesquisas deste SKU.'
                    );
                    card.appendChild(empty);
                    mlFavoritosListEl.appendChild(card);
                    return;
                }

                const wrap = document.createElement('div');
                wrap.className = 'ml-favoritos-table-wrap';
                const table = document.createElement('table');
                table.innerHTML = `
                    <thead>
                        <tr>
                            <th>Foto</th>
                            <th>Rank</th>
                            <th>MLB</th>
                            <th>Media mensal</th>
                            <th>Vendas</th>
                            <th>Dias</th>
                            <th>Titulo</th>
                            <th>Vendedor</th>
                            <th>Preco</th>
                            <th>Tipo</th>
                        </tr>
                    </thead>
                    <tbody></tbody>
                `;
                const tbody = table.querySelector('tbody');
                anunciosVisiveis.forEach((anuncio, index) => {
                    const tr = document.createElement('tr');
                    tr.appendChild(criarCelulaFotoAnuncioFavoritos(anuncio));
                    const valores = [
                        `#${index + 1}`,
                        anuncio.id || extrairItemIdAnuncio(anuncio.url) || '',
                        formatarMediaVendas(anuncio),
                        formatarVendasAvantPro(anuncio),
                        formatarDiasAnuncio(anuncio),
                        anuncio.titulo || ''
                    ];
                    valores.forEach(valor => {
                        const td = document.createElement('td');
                        td.textContent = valor;
                        tr.appendChild(td);
                    });
                    const tdVendedor = document.createElement('td');
                    renderizarCelulaVendedor(tdVendedor, anuncio.vendedor || '');
                    tr.appendChild(tdVendedor);
                    tr.appendChild(criarCelulaPrecoAnuncioFavoritos(anuncio));
                    tr.appendChild(criarCelulaTipoAnuncioFavoritos(anuncio));
                    tbody.appendChild(tr);
                });
                wrap.appendChild(table);
                card.appendChild(wrap);
                mlFavoritosListEl.appendChild(card);
            });
        }

        function normalizarHistoricoFavoritosFrontend(lista) {
            return Array.isArray(lista) ? lista.filter(Boolean).slice(0, ML_FAVORITOS_HISTORICO_MAX) : [];
        }

        function chaveHistoricoFavoritos() {
            const cid = userData && userData.client_id ? String(userData.client_id) : 'default';
            const usuario = userData && (userData.username || userData.email || userData.name || userData.nome)
                ? String(userData.username || userData.email || userData.name || userData.nome)
                : 'usuario';
            const usuarioKey = usuario.trim().toLowerCase().replace(/[^a-z0-9_-]+/gi, '_').slice(0, 60) || 'usuario';
            return `favoritos_ml_historico_${cid}_${usuarioKey}`;
        }

        function lerHistoricoFavoritosLocal() {
            try {
                const bruto = window.localStorage ? window.localStorage.getItem(chaveHistoricoFavoritos()) : null;
                const lista = bruto ? JSON.parse(bruto) : [];
                return normalizarHistoricoFavoritosFrontend(lista);
            } catch (err) {
                console.warn('Nao foi possivel carregar cache local do historico de favoritos:', err);
                return [];
            }
        }

        function salvarHistoricoFavoritosLocal(lista) {
            try {
                if (window.localStorage) {
                    window.localStorage.setItem(chaveHistoricoFavoritos(), JSON.stringify(normalizarHistoricoFavoritosFrontend(lista)));
                }
            } catch (err) {
                console.warn('Nao foi possivel salvar cache local do historico de favoritos:', err);
            }
        }

        function lerHistoricoFavoritos() {
            if (!mlHistoricoFavoritosCache.length && !mlHistoricoFavoritosServidorCarregado) {
                mlHistoricoFavoritosCache = lerHistoricoFavoritosLocal();
            }
            return normalizarHistoricoFavoritosFrontend(mlHistoricoFavoritosCache);
        }

        async function salvarHistoricoFavoritosServidor(lista) {
            const historico = normalizarHistoricoFavoritosFrontend(lista);
            const response = await fetch('/api/favoritos/historico', {
                method: 'PUT',
                headers: headersJsonAutenticado(),
                body: JSON.stringify({ historico })
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
            const remoto = normalizarHistoricoFavoritosFrontend(data.historico);
            mlHistoricoFavoritosCache = remoto;
            mlHistoricoFavoritosServidorCarregado = true;
            salvarHistoricoFavoritosLocal(remoto);
            return remoto;
        }

        function agendarSalvarHistoricoFavoritosServidor(lista) {
            const historico = normalizarHistoricoFavoritosFrontend(lista);
            if (mlHistoricoFavoritosSaveTimer) clearTimeout(mlHistoricoFavoritosSaveTimer);
            mlHistoricoFavoritosSaveTimer = setTimeout(() => {
                mlHistoricoFavoritosSaveTimer = null;
                salvarHistoricoFavoritosServidor(historico).catch(err => {
                    console.warn('Nao foi possivel salvar historico de favoritos no servidor:', err);
                    if (mlHistoricoFavoritosStatusEl && document.getElementById('aba-historico')?.classList.contains('active')) {
                        mlHistoricoFavoritosStatusEl.textContent = `Historico mantido em cache local, mas ainda nao foi salvo no servidor: ${err && err.message ? err.message : err}`;
                    }
                });
            }, 300);
        }

        function salvarHistoricoFavoritos(lista) {
            const historico = normalizarHistoricoFavoritosFrontend(lista);
            mlHistoricoFavoritosCache = historico;
            salvarHistoricoFavoritosLocal(historico);
            agendarSalvarHistoricoFavoritosServidor(historico);
        }

        async function carregarHistoricoFavoritosServidor() {
            const historicoLocal = lerHistoricoFavoritosLocal();
            mlHistoricoFavoritosCache = historicoLocal;
            try {
                const response = await fetch('/api/favoritos/historico', {
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
                const historicoServidor = normalizarHistoricoFavoritosFrontend(data.historico);
                mlHistoricoFavoritosServidorCarregado = true;
                if (historicoServidor.length) {
                    mlHistoricoFavoritosCache = historicoServidor;
                    salvarHistoricoFavoritosLocal(historicoServidor);
                } else if (historicoLocal.length) {
                    await salvarHistoricoFavoritosServidor(historicoLocal);
                } else {
                    mlHistoricoFavoritosCache = [];
                    salvarHistoricoFavoritosLocal([]);
                }
                if (document.getElementById('aba-historico')?.classList.contains('active')) {
                    renderizarHistoricoFavoritos();
                }
                if (document.getElementById('aba-favoritos')?.classList.contains('active')) {
                    renderizarFavoritosOutrosAnuncios(favMlSkuSelecionado);
                }
            } catch (err) {
                mlHistoricoFavoritosServidorCarregado = false;
                console.warn('Nao foi possivel carregar historico de favoritos do servidor:', err);
                if (mlHistoricoFavoritosStatusEl && document.getElementById('aba-historico')?.classList.contains('active')) {
                    mlHistoricoFavoritosStatusEl.textContent = `Usando cache local do historico. Erro ao acessar servidor: ${err && err.message ? err.message : err}`;
                }
            }
        }

        function anuncioHistoricoPayload(anuncio) {
            const precos = obterPrecosAnuncioFavoritos(anuncio);
            const id = anuncio && (anuncio.id || extrairItemIdAnuncio(anuncio.url) || '');
            return {
                id: id || '',
                url: anuncio && anuncio.url || '',
                titulo: anuncio && anuncio.titulo || '',
                vendedor: anuncio && anuncio.vendedor || '',
                vendas: anuncio && anuncio.vendas,
                vendasFonte: anuncio && (anuncio.vendasFonte || anuncio.vendas_fonte || ''),
                vendas_fonte: anuncio && (anuncio.vendasFonte || anuncio.vendas_fonte || ''),
                data_criacao: anuncio && anuncio.data_criacao || '',
                imagem: obterImagemAnuncioFavoritos(anuncio),
                thumbnail: obterImagemAnuncioFavoritos(anuncio),
                preco: precos.preco,
                price: precos.promocional !== null ? precos.promocional : precos.preco,
                preco_original: precos.promocional !== null ? precos.preco : '',
                original_price: precos.promocional !== null ? precos.preco : '',
                preco_promocional: precos.promocional,
                discount_pct: precos.desconto || '',
                fonte_preco: fontePrecoFavoritos(anuncio),
                parcelamento_sem_juros: obterParcelamentoSemJurosFavoritos(anuncio),
                tipo_anuncio: obterTipoAnuncioFavoritos(anuncio),
                listing_type_id: anuncio && (anuncio.listing_type_id || anuncio.listingTypeId || ''),
                listing_type_name: anuncio && (anuncio.listing_type_name || anuncio.tipo_anuncio || ''),
                shipping: anuncio && (anuncio.shipping || anuncio.shipping_info || anuncio.shippingInfo || null),
                logistic_type: anuncio && (anuncio.logistic_type || anuncio.logisticType || anuncio.shipping_logistic_type || ''),
                shipping_mode: anuncio && (anuncio.shipping_mode || anuncio.shippingMode || ''),
                is_full: temIndicadorFullFavoritos(anuncio) ? obterFullAnuncioFavoritos(anuncio) : '',
                media_vendas_mensal: anuncio && anuncio.media_vendas_mensal,
                meses_desde_criacao: anuncio && anuncio.meses_desde_criacao
            };
        }

        function montarEntradaHistoricoFavoritos(grupos) {
            const agora = new Date();
            const gruposHistorico = (Array.isArray(grupos) ? grupos : [])
                .map(grupo => {
                    const anuncios = Array.isArray(grupo && grupo.anuncios) ? grupo.anuncios.slice(0, 10).map(anuncioHistoricoPayload) : [];
                    return {
                        sku: grupo && grupo.sku || '',
                        titulo: grupo && grupo.titulo || '',
                        termos: Array.isArray(grupo && grupo.termos) ? grupo.termos : [],
                        opcoes_promocao: grupo && grupo.opcoes_promocao || null,
                        total_anuncios: Array.isArray(grupo && grupo.anuncios) ? grupo.anuncios.length : 0,
                        anuncios
                    };
                })
                .filter(grupo => grupo.sku && grupo.anuncios.length);

            return {
                id: `${agora.getTime()}_${Math.random().toString(36).slice(2, 8)}`,
                data_iso: agora.toISOString(),
                loja: mlSkuLojaSelecionada || skuLojaSelecionada || '',
                total_skus: gruposHistorico.length,
                total_anuncios: gruposHistorico.reduce((acc, grupo) => acc + grupo.total_anuncios, 0),
                grupos: gruposHistorico
            };
        }

        function registrarHistoricoFavoritos(grupos) {
            const entrada = montarEntradaHistoricoFavoritos(grupos);
            if (!entrada.grupos.length) return;
            const historico = lerHistoricoFavoritos();
            historico.unshift(entrada);
            salvarHistoricoFavoritos(historico.slice(0, ML_FAVORITOS_HISTORICO_MAX));
            if (document.getElementById('aba-historico')?.classList.contains('active')) {
                renderizarHistoricoFavoritos();
            }
        }

        function filtrarHistoricoFavoritosPorLojaAtual(historico) {
            const lista = Array.isArray(historico) ? historico : [];
            const lojaAtual = favoritosLojaAtualNormalizada();
            if (!lojaAtual) return lista;
            return lista.filter(entrada => skuNormalizarLoja(entrada && entrada.loja || '') === lojaAtual);
        }

        function obterHistoricoMaisRecenteSku(sku) {
            const chave = skuChaveSku(sku);
            if (!chave) return null;
            for (const entrada of filtrarHistoricoFavoritosPorLojaAtual(lerHistoricoFavoritos())) {
                const grupo = (entrada.grupos || []).find(item => skuChaveSku(item && item.sku) === chave);
                if (grupo) {
                    return {
                        entrada,
                        grupo: {
                            ...grupo,
                            data_iso: entrada.data_iso,
                            loja: entrada.loja || grupo.loja || ''
                        }
                    };
                }
            }
            return null;
        }

        function obterUltimoSkuHistoricoFavoritos() {
            for (const entrada of filtrarHistoricoFavoritosPorLojaAtual(lerHistoricoFavoritos())) {
                const grupo = (entrada.grupos || []).find(item => item && item.sku);
                if (grupo && grupo.sku) return String(grupo.sku).trim();
            }
            return '';
        }

        function idEntradaHistoricoFavoritos(entrada) {
            return String(entrada && (entrada.id || entrada.data_iso) || '').trim();
        }

        function montarUltimosFavoritosRankeados(limite = 10, historicoBase = null) {
            const historico = Array.isArray(historicoBase)
                ? historicoBase
                : filtrarHistoricoFavoritosPorLojaAtual(lerHistoricoFavoritos());
            const recentes = [];
            for (const entrada of historico) {
                const grupos = Array.isArray(entrada && entrada.grupos) ? entrada.grupos : [];
                for (const grupo of grupos) {
                    const sku = String(grupo && grupo.sku || '').trim();
                    if (!sku) continue;
                    const anuncios = filtrarAnunciosIgnoradosRanking(grupo && grupo.anuncios);
                    if (!anuncios.length) continue;
                    recentes.push({
                        entrada,
                        entrada_id: idEntradaHistoricoFavoritos(entrada),
                        grupo: {
                            ...grupo,
                            anuncios,
                            data_iso: entrada && entrada.data_iso || grupo.data_iso || '',
                            loja: entrada && entrada.loja || grupo.loja || ''
                        },
                        sku,
                        titulo: String(grupo && grupo.titulo || '').trim(),
                        data_iso: entrada && entrada.data_iso || grupo.data_iso || '',
                        loja: entrada && entrada.loja || grupo.loja || '',
                        total_anuncios: anuncios.length
                    });
                    if (recentes.length >= limite) return recentes;
                }
            }
            return recentes;
        }

        function criarBotaoFavoritoRecente(item, onClick) {
            const botao = document.createElement('button');
            botao.type = 'button';
            botao.className = 'ml-favoritos-recente-item';
            const titulo = document.createElement('span');
            titulo.className = 'ml-favoritos-recente-title';
            titulo.textContent = `${item.sku}${item.titulo ? ` - ${item.titulo}` : ''}`;
            const data = formatarDataHistoricoFavoritos(item.data_iso) || 'data desconhecida';
            const meta = document.createElement('span');
            meta.className = 'ml-favoritos-recente-meta';
            meta.textContent = `${item.total_anuncios} anuncio(s) rankeado(s) | ${data}${item.loja ? ` | Loja: ${item.loja}` : ''}`;
            botao.appendChild(titulo);
            botao.appendChild(meta);
            botao.addEventListener('click', () => onClick(item));
            return botao;
        }

        function abrirFavoritoRecenteNaAbaFavoritos(item) {
            const sku = String(item && item.sku || '').trim();
            if (!sku) return;
            const chave = skuChaveSku(sku);
            if (chave && item.grupo) {
                mlFavoritosResultadosPorSku.set(chave, {
                    ...item.grupo,
                    anuncios: Array.isArray(item.grupo.anuncios) ? item.grupo.anuncios.slice() : [],
                    data_iso: item.data_iso || item.grupo.data_iso || '',
                    loja: item.loja || item.grupo.loja || ''
                });
            }
            carregarFavoritosAnunciosSku(sku);
        }

        function abrirFavoritoRecenteNoHistorico(item) {
            const sku = String(item && item.sku || '').trim();
            if (!sku) return;
            histMlSkuSelecionado = sku;
            histMlHistoricoExecucaoSelecionadaId = String(item && item.entrada_id || '').trim();
            renderizarHistoricoSkuSidebar();
            renderizarHistoricoFavoritos();
            if (mlHistoricoFavoritosListEl && typeof mlHistoricoFavoritosListEl.scrollIntoView === 'function') {
                mlHistoricoFavoritosListEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        }

        function obterGrupoRankingFavoritosSku(sku) {
            const chave = skuChaveSku(sku);
            if (!chave) return null;
            const grupoAtual = mlFavoritosResultadosPorSku.get(chave);
            if (grupoAtual) {
                return {
                    grupo: grupoAtual,
                    data_iso: grupoAtual.data_iso || grupoAtual.data_ranking_iso || '',
                    loja: grupoAtual.loja || mlSkuLojaSelecionada || skuLojaSelecionada || ''
                };
            }
            const historico = obterHistoricoMaisRecenteSku(sku);
            if (historico) {
                return {
                    grupo: historico.grupo,
                    data_iso: historico.entrada && historico.entrada.data_iso || historico.grupo.data_iso || '',
                    loja: historico.entrada && historico.entrada.loja || historico.grupo.loja || ''
                };
            }
            return null;
        }

        function chavesRemocaoAnuncioRankingFavoritos(anuncio) {
            if (!anuncio) return [];
            const chaves = new Set(chavesAnuncioFavoritos(anuncio));
            const id = extrairItemIdAnuncio(anuncio.id || anuncio.mlb || anuncio.url || anuncio.permalink || anuncio.link)
                || String(anuncio.id || anuncio.mlb || '').trim().toUpperCase().replace(/-/g, '');
            if (id) chaves.add(`id:${id}`);
            [anuncio.url, anuncio.permalink, anuncio.link].forEach(url => {
                const texto = String(url || '').split('#')[0].trim().toLowerCase();
                if (texto) chaves.add(`url:${texto}`);
            });
            const titulo = String(anuncio.titulo || anuncio.title || '').replace(/\s+/g, ' ').trim().toLowerCase();
            if (titulo) {
                const vendedor = normalizarNomeVendedor(anuncio.vendedor || '').toLowerCase();
                const precos = obterPrecosAnuncioFavoritos(anuncio);
                const preco = String(precos.promocional ?? precos.preco ?? anuncio.price ?? anuncio.preco ?? '').trim();
                chaves.add(`produto:${titulo}|${vendedor}|${preco}`);
            }
            return Array.from(chaves).filter(Boolean);
        }

        function anuncioCorrespondeRemocaoRankingFavoritos(anuncio, chavesAlvo) {
            if (!chavesAlvo || !chavesAlvo.size) return false;
            return chavesRemocaoAnuncioRankingFavoritos(anuncio).some(chave => chavesAlvo.has(chave));
        }

        function removerAnuncioDeGrupoRankingFavoritos(grupo, chavesAlvo) {
            if (!grupo || !Array.isArray(grupo.anuncios) || !chavesAlvo || !chavesAlvo.size) return 0;
            const antes = grupo.anuncios.length;
            grupo.anuncios = grupo.anuncios.filter(anuncio => !anuncioCorrespondeRemocaoRankingFavoritos(anuncio, chavesAlvo));
            grupo.total_anuncios = grupo.anuncios.length;
            return antes - grupo.anuncios.length;
        }

        function recalcularTotaisHistoricoFavoritos(entrada) {
            if (!entrada || !Array.isArray(entrada.grupos)) return;
            entrada.total_skus = entrada.grupos.filter(grupo => grupo && grupo.sku).length;
            entrada.total_anuncios = entrada.grupos.reduce((acc, grupo) => (
                acc + (Array.isArray(grupo && grupo.anuncios) ? grupo.anuncios.length : 0)
            ), 0);
        }

        function removerAnuncioRankingFavoritos(sku, anuncio) {
            const skuSelecionado = String(sku || favMlSkuSelecionado || '').trim();
            if (!skuSelecionado || !anuncio) return;
            const titulo = String(anuncio.titulo || anuncio.id || anuncio.mlb || extrairItemIdAnuncio(anuncio.url) || 'este anuncio').trim();
            if (!confirm(`Remover "${titulo}" do ranking do SKU ${skuSelecionado}?`)) return;

            const chavesAlvo = new Set(chavesRemocaoAnuncioRankingFavoritos(anuncio));
            if (!chavesAlvo.size) return;

            const chaveSku = skuChaveSku(skuSelecionado);
            let removido = false;
            const grupoAtual = mlFavoritosResultadosPorSku.get(chaveSku);
            if (grupoAtual) {
                removido = removerAnuncioDeGrupoRankingFavoritos(grupoAtual, chavesAlvo) > 0 || removido;
                mlFavoritosResultadosPorSku.set(chaveSku, grupoAtual);
            }

            const historico = lerHistoricoFavoritos();
            let historicoAlterado = false;
            for (const entrada of filtrarHistoricoFavoritosPorLojaAtual(historico)) {
                const grupoHistorico = (entrada.grupos || []).find(item => skuChaveSku(item && item.sku) === chaveSku);
                if (!grupoHistorico) continue;
                const removidosHistorico = removerAnuncioDeGrupoRankingFavoritos(grupoHistorico, chavesAlvo);
                if (removidosHistorico > 0) {
                    historicoAlterado = true;
                    removido = true;
                    recalcularTotaisHistoricoFavoritos(entrada);
                }
                break;
            }
            if (historicoAlterado) salvarHistoricoFavoritos(historico);

            renderizarFavoritosOutrosAnuncios(skuSelecionado);
            renderizarHistoricoFavoritos();
            if (favMlStatusEl) {
                favMlStatusEl.textContent = removido
                    ? `Anuncio removido do ranking do SKU ${skuSelecionado}.`
                    : `Nao localizei esse anuncio salvo no ranking do SKU ${skuSelecionado}.`;
            }
        }

        function criarCelulaRemoverRankingFavoritos(sku, anuncio) {
            const td = document.createElement('td');
            td.className = 'ml-ranking-actions-cell';
            const botao = document.createElement('button');
            botao.type = 'button';
            botao.className = 'ml-ranking-remove-btn';
            botao.textContent = 'Excluir';
            botao.title = 'Remover este anuncio do ranking';
            botao.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                removerAnuncioRankingFavoritos(sku, anuncio);
            });
            td.appendChild(botao);
            return td;
        }

        function formatarDataHistoricoFavoritos(dataIso) {
            const data = new Date(dataIso);
            if (Number.isNaN(data.getTime())) return '';
            return data.toLocaleString('pt-BR', {
                day: '2-digit',
                month: '2-digit',
                year: 'numeric',
                hour: '2-digit',
                minute: '2-digit'
            });
        }

        function criarTabelaHistoricoFavoritos(anuncios) {
            const wrap = document.createElement('div');
            wrap.className = 'ml-favoritos-table-wrap';
            const table = document.createElement('table');
            table.innerHTML = `
                <thead>
                    <tr>
                        <th>Foto</th>
                        <th>Rank</th>
                        <th>MLB</th>
                        <th>Media mensal</th>
                        <th>Vendas</th>
                        <th>Dias</th>
                        <th>Titulo</th>
                        <th>Vendedor</th>
                        <th>Preco</th>
                        <th>Link</th>
                    </tr>
                </thead>
                <tbody></tbody>
            `;
            const tbody = table.querySelector('tbody');
            const lista = filtrarAnunciosIgnoradosRanking(anuncios);
            if (!lista.length) {
                const tr = document.createElement('tr');
                const td = document.createElement('td');
                td.colSpan = 10;
                td.className = 'muted';
                td.textContent = 'Todos os anuncios deste historico pertencem a vendedores ignorados.';
                tr.appendChild(td);
                tbody.appendChild(tr);
            }
            lista.forEach((anuncio, index) => {
                const tr = document.createElement('tr');
                tr.appendChild(criarCelulaFotoAnuncioFavoritos(anuncio));
                [
                    `#${index + 1}`,
                    anuncio.id || extrairItemIdAnuncio(anuncio.url) || '',
                    formatarMediaVendas(anuncio),
                    formatarVendasAvantPro(anuncio),
                    formatarDiasAnuncio(anuncio),
                    anuncio.titulo || ''
                ].forEach(valor => {
                    const td = document.createElement('td');
                    td.textContent = valor;
                    tr.appendChild(td);
                });
                const tdVendedor = document.createElement('td');
                renderizarCelulaVendedor(tdVendedor, anuncio.vendedor || '');
                tr.appendChild(tdVendedor);
                tr.appendChild(criarCelulaPrecoAnuncioFavoritos(anuncio));
                const tdLink = document.createElement('td');
                if (anuncio.url) {
                    const link = document.createElement('a');
                    link.href = anuncio.url;
                    link.target = '_blank';
                    link.rel = 'noopener';
                    link.className = 'link';
                    link.textContent = 'Abrir';
                    link.addEventListener('click', (event) => abrirAnuncioComAvantPro(anuncio.url, event));
                    tdLink.appendChild(link);
                }
                tr.appendChild(tdLink);
                tbody.appendChild(tr);
            });
            wrap.appendChild(table);
            return wrap;
        }

        function renderizarHistoricoSkuSidebar() {
            if (!histMlSkuSidebarListEl || !histMlSkuSidebarEmptyEl || !histMlSkuSidebarCountEl) return;
            const todosItens = montarItensSkuSidebarMercadoLivre();
            const termoBusca = histMlSkuSidebarSearchEl ? histMlSkuSidebarSearchEl.value || '' : '';
            const itens = filtrarItensSkuSidebarMercadoLivre(todosItens, termoBusca);
            const selecionadoChave = String(histMlSkuSelecionado || '').trim().toLowerCase();

            histMlSkuSidebarListEl.innerHTML = '';
            histMlSkuSidebarCountEl.textContent = todosItens.length
                ? (itens.length === todosItens.length ? `${todosItens.length} SKU(s)` : `${itens.length} de ${todosItens.length} SKU(s)`)
                : '';
            histMlSkuSidebarEmptyEl.textContent = termoBusca.trim()
                ? 'Nenhum SKU encontrado para essa pesquisa.'
                : mlSkuLojaSelecionada
                ? 'Nenhum SKU encontrado nos anuncios ativos desta loja.'
                : 'Escolha uma loja integrada para carregar os SKUs dos anuncios ativos.';
            histMlSkuSidebarEmptyEl.classList.toggle('hidden', itens.length > 0);
            if (!itens.length && termoBusca.trim()) {
                agendarBuscaRemotaSkuSidebarMercadoLivre(termoBusca);
            }

            if (selecionadoChave && !todosItens.some(item => item.chave === selecionadoChave)) {
                histMlSkuSelecionado = '';
            }

            itens.forEach(item => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'ml-sku-sidebar-item' + (item.chave === String(histMlSkuSelecionado || '').trim().toLowerCase() ? ' is-active' : '');

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
                if (mlSkuLojaSelecionada) partes.push(`Loja: ${mlSkuLojaSelecionada}`);
                if (item.totalAnuncios) partes.push(`${item.totalAnuncios} anuncio(s)`);
                if (item.itemIds && item.itemIds.length) partes.push(item.itemIds.slice(0, 2).join(', '));
                meta.textContent = partes.join(' | ');

                main.appendChild(codigo);
                main.appendChild(titulo);
                if (meta.textContent) main.appendChild(meta);
                button.appendChild(main);
                button.addEventListener('click', () => selecionarHistoricoSku(item.sku));
                histMlSkuSidebarListEl.appendChild(button);
            });
        }

        function selecionarHistoricoSku(sku) {
            histMlSkuSelecionado = String(sku || '').trim();
            histMlHistoricoExecucaoSelecionadaId = '';
            renderizarHistoricoSkuSidebar();
            renderizarHistoricoFavoritos();
        }

        function prepararAbaHistoricoFavoritos() {
            renderizarHistoricoSkuSidebar();
            renderizarHistoricoFavoritos();
            if (!mlSkuLojasDisponiveis.length && !mlSkusAnunciosLojaAtual.length) {
                mlSkuCarregarSkusAnuncios(mlSkuLojaSelecionada || mlSkuCarregarPreferenciaLoja());
            }
        }

        function renderizarHistoricoFavoritos() {
            if (!mlHistoricoFavoritosListEl || !mlHistoricoFavoritosEmptyEl) return;
            const historicoCompleto = lerHistoricoFavoritos();
            const historico = filtrarHistoricoFavoritosPorLojaAtual(historicoCompleto);
            const lojaAtualTexto = mlSkuLojaSelecionada || skuLojaSelecionada || '';
            const skuSelecionado = String(histMlSkuSelecionado || '').trim();
            const skuSelecionadoChave = skuChaveSku(skuSelecionado);
            mlHistoricoFavoritosListEl.innerHTML = '';

            if (!skuSelecionadoChave) {
                const recentes = montarUltimosFavoritosRankeados(12, historico);
                if (mlHistoricoFavoritosStatusEl) {
                    mlHistoricoFavoritosStatusEl.textContent = recentes.length
                        ? `Ultimos favoritos feitos${lojaAtualTexto ? ` para a loja ${lojaAtualTexto}` : ''}. Clique em um item para abrir os anuncios rankeados.`
                        : `Nenhum favorito salvo no historico do servidor${lojaAtualTexto ? ` para a loja ${lojaAtualTexto}` : ''}.`;
                }
                mlHistoricoFavoritosEmptyEl.textContent = recentes.length
                    ? ''
                    : 'Nenhum favorito salvo ainda.';
                mlHistoricoFavoritosEmptyEl.classList.toggle('hidden', recentes.length > 0);
                if (recentes.length) {
                    const lista = document.createElement('div');
                    lista.className = 'ml-favoritos-recentes-list';
                    recentes.forEach(item => {
                        lista.appendChild(criarBotaoFavoritoRecente(item, abrirFavoritoRecenteNoHistorico));
                    });
                    mlHistoricoFavoritosListEl.appendChild(lista);
                }
                return;
            }

            const historicoComSku = historico
                .filter(entrada => {
                    const execucaoSelecionada = String(histMlHistoricoExecucaoSelecionadaId || '').trim();
                    return !execucaoSelecionada || idEntradaHistoricoFavoritos(entrada) === execucaoSelecionada;
                })
                .map(entrada => ({
                    ...entrada,
                    grupos: (entrada.grupos || []).filter(grupo => skuChaveSku(grupo && grupo.sku) === skuSelecionadoChave)
                }))
                .filter(entrada => entrada.grupos.length);

            const historicoFiltrado = historicoComSku
                .map(entrada => ({
                    ...entrada,
                    grupos: (entrada.grupos || [])
                        .map(grupo => ({
                            ...grupo,
                            anuncios: filtrarAnunciosIgnoradosRanking(grupo && grupo.anuncios)
                        }))
                        .filter(grupo => grupo.anuncios.length)
                }))
                .filter(entrada => entrada.grupos.length);

            mlHistoricoFavoritosEmptyEl.textContent = historicoComSku.length && !historicoFiltrado.length
                ? `Todos os anuncios salvos para o SKU ${skuSelecionado} pertencem a vendedores ignorados.`
                : `Nenhum historico salvo para o SKU ${skuSelecionado}.`;
            mlHistoricoFavoritosEmptyEl.classList.toggle('hidden', historicoFiltrado.length > 0);
            if (mlHistoricoFavoritosStatusEl) {
                mlHistoricoFavoritosStatusEl.textContent = historicoFiltrado.length
                    ? `${historicoFiltrado.length} execucao(oes) encontrada(s) para o SKU ${skuSelecionado}. Cada execucao mostra os 10 melhores anuncios classificados.`
                    : '';
            }
            historicoFiltrado.forEach(entrada => {
                const card = document.createElement('section');
                card.className = 'ml-favoritos-sku-card';

                const head = document.createElement('div');
                head.className = 'ml-favoritos-sku-head';
                const titulo = document.createElement('h4');
                titulo.className = 'ml-favoritos-sku-title';
                titulo.textContent = `Favoritos feitos em ${formatarDataHistoricoFavoritos(entrada.data_iso) || 'data desconhecida'}`;
                const meta = document.createElement('div');
                meta.className = 'ml-favoritos-historico-meta';
                const totalAnunciosSku = (entrada.grupos || []).reduce((acc, grupo) => acc + (Array.isArray(grupo.anuncios) ? grupo.anuncios.length : 0), 0);
                meta.textContent = `${totalAnunciosSku} anuncio(s) rankeado(s) para o SKU ${skuSelecionado}${entrada.loja ? ` | Loja: ${entrada.loja}` : ''}`;
                head.appendChild(titulo);
                head.appendChild(meta);
                card.appendChild(head);

                (entrada.grupos || []).forEach(grupo => {
                    const bloco = document.createElement('div');
                    bloco.className = 'ml-favoritos-historico-grupo';
                    const subtitulo = document.createElement('h5');
                    subtitulo.className = 'ml-favoritos-historico-grupo-title';
                    subtitulo.textContent = `${grupo.sku}${grupo.titulo ? ` - ${grupo.titulo}` : ''}`;
                    const termos = document.createElement('div');
                    termos.className = 'ml-favoritos-termos';
                    termos.textContent = Array.isArray(grupo.termos) && grupo.termos.length
                        ? `Pesquisas usadas: ${grupo.termos.map(item => `${item.campo}: ${item.termo}`).join(' | ')}`
                        : 'Sem pesquisas registradas.';
                    bloco.appendChild(subtitulo);
                    bloco.appendChild(termos);
                    bloco.appendChild(criarTabelaHistoricoFavoritos(grupo.anuncios));
                    card.appendChild(bloco);
                });
                mlHistoricoFavoritosListEl.appendChild(card);
            });
        }

        function limparHistoricoFavoritos() {
            if (!confirm('Deseja apagar o historico de favoritos salvo no servidor para este usuario?')) return;
            salvarHistoricoFavoritos([]);
            renderizarHistoricoFavoritos();
        }

        function guardarResultadoRankingFavorito(grupo) {
            const chave = skuChaveSku(grupo && grupo.sku);
            if (!chave) return;
            if (!grupo.data_iso && !grupo.data_ranking_iso) {
                grupo.data_ranking_iso = new Date().toISOString();
            }
            if (!grupo.loja) {
                grupo.loja = mlSkuLojaSelecionada || skuLojaSelecionada || '';
            }
            mlFavoritosResultadosPorSku.set(chave, grupo);
            if (skuChaveSku(favMlSkuSelecionado) === chave) {
                renderizarFavoritosOutrosAnuncios(favMlSkuSelecionado);
            }
        }

        async function fazerFavoritosSkusSelecionados() {
            if (mlFavoritosEmExecucao) return;
            const selecionados = obterSkusSelecionadosSidebar();
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
            const quantidade = await perguntarQuantidadePesquisasFavoritos();
            if (!quantidade) return;
            const opcoesPromocao = await perguntarOpcoesPromocaoFavoritos();
            if (!opcoesPromocao) return;

            mlFavoritosEmExecucao = true;
            mlFavoritosCancelado = false;
            mlFavoritosAbortController = typeof AbortController === 'function' ? new AbortController() : null;
            abrirBalaoResultadosMl({
                titulo: 'Fazendo Favorito! Aguarde...',
                subtitulo: `${selecionados.length} SKU(s), ${quantidade} pesquisa(s) por SKU`,
                mostrarFavoritos: true,
                browserCompleto: true
            });
            atualizarFiltroAzulFavoritos();
            atualizarContadorSkuSidebarSelecionados();
            if (mlFavoritosPanelEl) mlFavoritosPanelEl.classList.remove('hidden');
            if (mlFavoritosListEl) mlFavoritosListEl.innerHTML = '';
            if (mlFavoritosEmptyEl) mlFavoritosEmptyEl.classList.add('hidden');
            mostrarBalaoFavoritosStatus(`Iniciando favoritos: ${selecionados.length} SKU(s), ${quantidade} pesquisa(s) por SKU, ${resumoOpcoesPromocaoFavoritos(opcoesPromocao)}.`);

            try {
                if (!Array.isArray(skuDados) || !skuDados.length) {
                    mostrarBalaoFavoritosStatus('Carregando dados dos SKUs...');
                    await carregarSkuFavoritos();
                    verificarCancelamentoFavoritos();
                }

                const grupos = [];
                for (let idx = 0; idx < selecionados.length; idx += 1) {
                    verificarCancelamentoFavoritos();
                    const item = selecionados[idx];
                    const info = montarPesquisasFavoritosSku(item, quantidade);
                    mostrarBalaoFavoritosStatus(`Processando SKU ${info.sku} (${idx + 1}/${selecionados.length})...`);
                    if (!info.termos.length) {
                        const grupoSemPesquisa = { ...info, opcoes_promocao: opcoesPromocao, anuncios: [], erro: `Nenhum campo Pesquisa 1 a ${quantidade} preenchido para este SKU.` };
                        grupos.push(grupoSemPesquisa);
                        guardarResultadoRankingFavorito(grupoSemPesquisa);
                        mostrarBalaoFavoritosStatus(`SKU ${info.sku}: nenhum campo Pesquisa 1 a ${quantidade} preenchido.`, {
                            erro: true
                        });
                        renderizarFavoritosPesquisaResultados(grupos);
                        continue;
                    }

                    const coletados = [];
                    for (const pesquisa of info.termos) {
                        verificarCancelamentoFavoritos();
                        mostrarBalaoFavoritosStatus(`SKU ${info.sku}: pesquisando Pesquisa ${pesquisa.campo} - ${pesquisa.termo}`);
                        const anuncios = await buscarAnunciosFavoritosPorTermo(pesquisa.termo);
                        verificarCancelamentoFavoritos();
                        anuncios.forEach(anuncio => {
                            coletados.push(normalizarAnuncioFavoritosPesquisa(anuncio, info.sku, pesquisa));
                        });
                    }

                    const unicos = deduplicarAnunciosFavoritos(coletados)
                        .filter(anuncio => anuncio && !tituloPareceFiltroOuCategoriaMl(anuncio.titulo));
                    mostrarBalaoFavoritosStatus(`SKU ${info.sku}: enriquecendo ${unicos.length} anuncio(s) para calcular ranking...`);
                    verificarCancelamentoFavoritos();
                    await enriquecerAnunciosFavoritosRanking(unicos);
                    verificarCancelamentoFavoritos();
                    const anunciosNovos = unicos.filter(anuncioFavoritosProdutoNovo);
                    const removidosPorCondicao = unicos.length - anunciosNovos.length;
                    if (removidosPorCondicao > 0) {
                        mostrarBalaoFavoritosStatus(`SKU ${info.sku}: removidos ${removidosPorCondicao} anuncio(s) marcados como usados.`);
                    }
                    const filtroIa = await filtrarAnunciosFavoritosPorIa(info, anunciosNovos);
                    verificarCancelamentoFavoritos();
                    if (filtroIa.removidosTotal) {
                        mostrarBalaoFavoritosStatus(`SKU ${info.sku}: IA removeu ${filtroIa.removidosTotal} anuncio(s) fora do produto do cadastro.`);
                    }
                    const grupoRanking = {
                        ...info,
                        opcoes_promocao: opcoesPromocao,
                        anuncios: ordenarAnunciosFavoritosRanking(filtroIa.anuncios),
                        removidos_ia: filtroIa.removidos,
                        removidos_ia_total: filtroIa.removidosTotal
                    };
                    grupos.push(grupoRanking);
                    guardarResultadoRankingFavorito(grupoRanking);
                    renderizarFavoritosPesquisaResultados(grupos);
                }

                const totalAnuncios = grupos.reduce((acc, grupo) => acc + (grupo.anuncios || []).length, 0);
                mostrarBalaoFavoritosStatus(`Favoritos concluido: ${grupos.length} SKU(s), ${totalAnuncios} anuncio(s) rankeado(s).`, {
                    tempoMs: 7000
                });
                registrarHistoricoFavoritos(grupos);
                const grupoParaAbrir = grupos.find(grupo => grupo && grupo.sku && Array.isArray(grupo.anuncios) && grupo.anuncios.length)
                    || grupos.find(grupo => grupo && grupo.sku);
                if (grupoParaAbrir && grupoParaAbrir.sku) {
                    favMlSkuSelecionado = grupoParaAbrir.sku;
                }
                if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Favoritos concluÃ­dos';
                if (mlWorkModalSubtitleEl) mlWorkModalSubtitleEl.textContent = `${grupos.length} SKU(s), ${totalAnuncios} anuncio(s) rankeado(s).`;
                if (grupoParaAbrir && grupoParaAbrir.sku) {
                    carregarFavoritosAnunciosSku(grupoParaAbrir.sku);
                }
                mlFavoritosEmExecucao = false;
                atualizarFiltroAzulFavoritos();
                atualizarContadorSkuSidebarSelecionados();
                esconderBalaoFavoritosStatus();
                fecharBalaoResultadosMl();
                mudarAba('favoritos');
            } catch (err) {
                if (mlFavoritosCancelado || (err && (err.canceladoFavoritos || err.name === 'AbortError'))) {
                    if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Favoritos cancelado';
                    mostrarBalaoFavoritosStatus('Favoritos cancelado pelo usuario.', {
                        tempoMs: 5000
                    });
                } else {
                    if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = 'Erro ao fazer favoritos';
                    mostrarBalaoFavoritosStatus(`Erro ao fazer favoritos: ${err && err.message ? err.message : err}`, {
                        erro: true
                    });
                }
            } finally {
                mlFavoritosEmExecucao = false;
                mlFavoritosCancelado = false;
                mlFavoritosAbortController = null;
                atualizarFiltroAzulFavoritos();
                atualizarContadorSkuSidebarSelecionados();
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
            const itens = filtrarItensSkuSidebarMercadoLivre(todosItens, termoBusca);
            const selecionadoChave = String(favMlSkuSelecionado || '').trim().toLowerCase();

            favMlSkuSidebarListEl.innerHTML = '';
            favMlSkuSidebarCountEl.textContent = todosItens.length
                ? (itens.length === todosItens.length ? `${todosItens.length} SKU(s)` : `${itens.length} de ${todosItens.length} SKU(s)`)
                : '';
            favMlSkuSidebarEmptyEl.textContent = termoBusca.trim()
                ? 'Nenhum SKU encontrado para essa pesquisa.'
                : mlSkuLojaSelecionada
                ? 'Nenhum SKU encontrado nos anuncios ativos desta loja.'
                : 'Escolha uma loja integrada para carregar os SKUs dos anuncios ativos.';
            favMlSkuSidebarEmptyEl.classList.toggle('hidden', itens.length > 0);
            if (!itens.length && termoBusca.trim()) {
                agendarBuscaRemotaSkuSidebarMercadoLivre(termoBusca);
            }

            if (selecionadoChave && !todosItens.some(item => item.chave === selecionadoChave)) {
                favMlSkuSelecionado = '';
                favMlAnunciosSkuAtual = [];
                renderizarFavoritosAnunciosMl([], '');
                renderizarFavoritosOutrosAnuncios('');
            }

            itens.forEach(item => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'ml-sku-sidebar-item' + (item.chave === String(favMlSkuSelecionado || '').trim().toLowerCase() ? ' is-active' : '');

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
                if (mlSkuLojaSelecionada) partes.push(`Loja: ${mlSkuLojaSelecionada}`);
                if (item.totalAnuncios) partes.push(`${item.totalAnuncios} anuncio(s)`);
                if (item.itemIds && item.itemIds.length) partes.push(item.itemIds.slice(0, 2).join(', '));
                meta.textContent = partes.join(' | ');

                main.appendChild(codigo);
                main.appendChild(titulo);
                if (meta.textContent) main.appendChild(meta);
                button.appendChild(main);
                button.addEventListener('click', () => carregarFavoritosAnunciosSku(item.sku));
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

        function aplicarLargurasTabelaFavoritos(table, keys, layout) {
            const colgroup = criarColgroupTabelaFavoritos(table, keys);
            keys.forEach((key, idx) => {
                const width = Number(layout.widths && layout.widths[key]);
                const col = colgroup.children[idx];
                if (!col) return;
                if (Number.isFinite(width) && width > 0) {
                    col.style.width = `${Math.max(54, width)}px`;
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
            };
            const parar = () => {
                document.body.classList.remove('favoritos-resizing-table');
                window.removeEventListener('pointermove', mover);
                window.removeEventListener('pointerup', parar);
                window.removeEventListener('pointercancel', parar);
                salvarLayoutTabelasFavoritos();
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
        }

        function inicializarLarguraTabelasFavoritos() {
            if (!favMlTablesLayoutEl || favMlTablesLayoutEl.dataset.resizerReady === '1') return;
            favMlTablesLayoutEl.dataset.resizerReady = '1';
            favMlTablesLayoutEl.classList.add('is-resizable');
            const split = Number(favoritosTableLayout && favoritosTableLayout.split);
            if (Number.isFinite(split)) {
                favMlTablesLayoutEl.style.setProperty('--favoritos-ml-left-width', `${Math.min(72, Math.max(32, split))}%`);
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
                };
                const parar = () => {
                    document.body.classList.remove('favoritos-resizing-table');
                    window.removeEventListener('pointermove', mover);
                    window.removeEventListener('pointerup', parar);
                    window.removeEventListener('pointercancel', parar);
                    salvarLayoutTabelasFavoritos();
                };
                window.addEventListener('pointermove', mover);
                window.addEventListener('pointerup', parar, { once: true });
                window.addEventListener('pointercancel', parar, { once: true });
            });
        }

        function renderizarFavoritosAnunciosMl(anuncios, sku, carregando = false) {
            if (!favMlAnunciosBodyEl || !favMlAnunciosEmptyEl) return;
            const lista = Array.isArray(anuncios) ? anuncios : [];
            favMlAnunciosBodyEl.innerHTML = '';

            if (carregando) {
                favMlAnunciosEmptyEl.textContent = `Carregando anuncios do SKU ${sku}...`;
                favMlAnunciosEmptyEl.classList.remove('hidden');
                atualizarTabelasFavoritosEditaveis();
                return;
            }

            if (!sku) {
                favMlAnunciosEmptyEl.textContent = 'Selecione um SKU para carregar os anuncios do Mercado Livre.';
                favMlAnunciosEmptyEl.classList.remove('hidden');
                atualizarTabelasFavoritosEditaveis();
                return;
            }

            if (!lista.length) {
                favMlAnunciosEmptyEl.textContent = `Nenhum anuncio do Mercado Livre encontrado para o SKU ${sku}.`;
                favMlAnunciosEmptyEl.classList.remove('hidden');
                atualizarTabelasFavoritosEditaveis();
                return;
            }

            favMlAnunciosEmptyEl.classList.add('hidden');
            lista.forEach(anuncio => {
                const tr = document.createElement('tr');
                tr.appendChild(criarCelulaFotoAnuncioFavoritos(anuncio));
                tr.appendChild(criarCelulaTextoFavoritos(anuncio.mlb || anuncio.id || '', { nowrap: true }));
                tr.appendChild(criarCelulaTextoFavoritos(anuncio.titulo || '', { long: true }));
                tr.appendChild(criarCelulaTextoFavoritos(anuncio.status || '', { nowrap: true }));

                tr.appendChild(criarCelulaPrecoAnuncioFavoritos(anuncio));
                tr.appendChild(criarCelulaTipoAnuncioFavoritos(anuncio));
                [anuncio.estoque ?? '', anuncio.vendidos ?? ''].forEach(valor => {
                    tr.appendChild(criarCelulaTextoFavoritos(valor, { nowrap: true }));
                });
                favMlAnunciosBodyEl.appendChild(tr);
            });
            atualizarTabelasFavoritosEditaveis();
        }

        function renderizarFavoritosUltimosRankingsDisponiveis() {
            if (!favOutrosAnunciosBodyEl || !favOutrosAnunciosEmptyEl) return false;
            const recentes = montarUltimosFavoritosRankeados(10);
            if (!recentes.length) return false;

            favOutrosAnunciosEmptyEl.classList.add('hidden');
            if (favRankingDataEl) {
                favRankingDataEl.textContent = 'Nenhum SKU selecionado. Clique em um dos ultimos favoritos feitos para abrir os anuncios rankeados.';
            }

            const tr = document.createElement('tr');
            const td = document.createElement('td');
            td.colSpan = 11;
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

        function renderizarFavoritosOutrosAnuncios(sku) {
            if (favOutrosAnunciosBodyEl) favOutrosAnunciosBodyEl.innerHTML = '';
            if (favRankingDataEl) favRankingDataEl.textContent = '';
            if (!favOutrosAnunciosEmptyEl) return;
            const skuSelecionado = String(sku || '').trim();
            if (!skuSelecionado) {
                if (renderizarFavoritosUltimosRankingsDisponiveis()) return;
                favOutrosAnunciosEmptyEl.textContent = 'Selecione um SKU para visualizar o ranking gerado.';
                favOutrosAnunciosEmptyEl.classList.remove('hidden');
                atualizarTabelasFavoritosEditaveis();
                return;
            }

            const resultadoRanking = obterGrupoRankingFavoritosSku(skuSelecionado);
            const grupo = resultadoRanking && resultadoRanking.grupo;
            if (!grupo) {
                favOutrosAnunciosEmptyEl.textContent = 'Nenhum ranking gerado para este SKU. Use Fazer favoritos na aba Pagina de Pesquisa.';
                favOutrosAnunciosEmptyEl.classList.remove('hidden');
                atualizarTabelasFavoritosEditaveis();
                return;
            }
            if (favRankingDataEl) {
                const dataRanking = formatarDataHistoricoFavoritos(resultadoRanking.data_iso || grupo.data_iso || grupo.data_ranking_iso);
                const lojaRanking = resultadoRanking.loja || grupo.loja || '';
                favRankingDataEl.textContent = dataRanking
                    ? `Ultimo ranking feito em ${dataRanking}${lojaRanking ? ` | Loja: ${lojaRanking}` : ''}`
                    : 'Ultimo ranking salvo para este SKU.';
            }
            if (grupo.erro) {
                favOutrosAnunciosEmptyEl.textContent = grupo.erro;
                favOutrosAnunciosEmptyEl.classList.remove('hidden');
                atualizarTabelasFavoritosEditaveis();
                return;
            }

            const anuncios = filtrarAnunciosIgnoradosRanking(grupo.anuncios);
            if (!anuncios.length) {
                favOutrosAnunciosEmptyEl.textContent = Array.isArray(grupo.anuncios) && grupo.anuncios.length
                    ? 'Todos os anuncios rankeados para este SKU pertencem a vendedores ignorados.'
                    : 'Nenhum anuncio rankeado para este SKU.';
                favOutrosAnunciosEmptyEl.classList.remove('hidden');
                atualizarTabelasFavoritosEditaveis();
                return;
            }

            favOutrosAnunciosEmptyEl.classList.add('hidden');
            complementarTiposRankingFavoritos(skuSelecionado, anuncios);
            anuncios.forEach((anuncio, index) => {
                const tr = document.createElement('tr');
                tr.appendChild(criarCelulaFotoAnuncioFavoritos(anuncio));
                const valores = [
                    { valor: `#${index + 1}`, nowrap: true },
                    { valor: anuncio.id || extrairItemIdAnuncio(anuncio.url) || '', nowrap: true },
                    { valor: formatarMediaVendas(anuncio), nowrap: true },
                    { valor: formatarVendasAvantPro(anuncio), nowrap: true },
                    { valor: formatarDiasAnuncio(anuncio), nowrap: true },
                    { valor: anuncio.titulo || '', long: true }
                ];
                valores.forEach(item => {
                    tr.appendChild(criarCelulaTextoFavoritos(item.valor, item));
                });
                const tdVendedor = document.createElement('td');
                renderizarCelulaVendedor(tdVendedor, anuncio.vendedor || '');
                tr.appendChild(tdVendedor);
                tr.appendChild(criarCelulaPrecoAnuncioFavoritos(anuncio));
                tr.appendChild(criarCelulaTipoAnuncioFavoritos(anuncio));
                tr.appendChild(criarCelulaRemoverRankingFavoritos(skuSelecionado, anuncio));
                favOutrosAnunciosBodyEl.appendChild(tr);
            });
            atualizarTabelasFavoritosEditaveis();
        }

        function prepararAbaFavoritosMl() {
            renderizarFavoritosSkuSidebar();
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

            if (!mlSkuLojasDisponiveis.length && !mlSkusAnunciosLojaAtual.length) {
                mlSkuCarregarSkusAnuncios(mlSkuLojaSelecionada || mlSkuCarregarPreferenciaLoja());
            }
        }

        async function carregarFavoritosAnunciosSku(sku) {
            const skuSelecionado = String(sku || '').trim();
            if (!skuSelecionado) return;

            favMlSkuSelecionado = skuSelecionado;
            favMlAnunciosSkuAtual = [];
            renderizarFavoritosSkuSidebar();
            renderizarFavoritosAnunciosMl([], skuSelecionado, true);
            renderizarFavoritosOutrosAnuncios(skuSelecionado);
            if (favMlStatusEl) {
                favMlStatusEl.textContent = `Carregando anuncios do SKU ${skuSelecionado}...`;
            }

            try {
                const params = new URLSearchParams({ sku: skuSelecionado });
                if (mlSkuLojaSelecionada) params.set('loja', mlSkuLojaSelecionada);
                const response = await fetch(`/api/favoritos/ml/anuncios-sku?${params.toString()}`, {
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
                if (favMlSkuSelecionado !== skuSelecionado) return;

                favMlAnunciosSkuAtual = Array.isArray(data.anuncios) ? data.anuncios : [];
                renderizarFavoritosAnunciosMl(favMlAnunciosSkuAtual, skuSelecionado);
                if (favMlStatusEl) {
                    const loja = data.loja || mlSkuLojaSelecionada || '';
                    favMlStatusEl.textContent = `${favMlAnunciosSkuAtual.length} anuncio(s) do Mercado Livre para o SKU ${skuSelecionado}${loja ? ` em ${loja}` : ''}.`;
                }
            } catch (err) {
                if (favMlSkuSelecionado !== skuSelecionado) return;
                favMlAnunciosSkuAtual = [];
                renderizarFavoritosAnunciosMl([], skuSelecionado);
                if (favMlStatusEl) {
                    favMlStatusEl.textContent = `Erro ao carregar anuncios do SKU ${skuSelecionado}: ${err && err.message ? err.message : err}`;
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
                ? `${ranking.length}/${total}${ignorados ? ` Â· ${ignorados} fora` : ''}`
                : '';
            mlRankingMediaEmptyEl.textContent = ignorados && metricas.length && !ranking.length
                ? 'Todos os anÃºncios com mÃ©dia calculada pertencem a vendedores fora do ranking.'
                : 'Assim que vendas e data forem carregadas, os anÃºncios aparecem aqui em ordem da maior mÃ©dia mensal.';
            mlRankingMediaEmptyEl.classList.toggle('hidden', ranking.length > 0);
            mlRankingMediaListEl.innerHTML = '';

            ranking.slice(0, 30).forEach((item, rankIndex) => {
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
                media.textContent = `${formatarMediaVendas(anuncio)}/mÃªs`;

                topLine.appendChild(rank);
                topLine.appendChild(media);

                const nome = document.createElement(anuncio.url ? 'a' : 'span');
                nome.className = 'ml-ranking-name';
                nome.textContent = anuncio.titulo || anuncio.id || anuncio.url || 'AnÃºncio sem tÃ­tulo';
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

        function atualizarCelulaVendas(item, vendas) {
            const alvo = encontrarAnuncioPrimeiraPaginaAtual(item);
            if (alvo) alvo.vendas = vendas;
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

        async function atualizarDadosAvantAutomaticamente(anuncios, tentativa = 1) {
            if (!mlWebviewEl || !Array.isArray(anuncios) || !anuncios.length) return { vendedor: 0, data: 0, vendas: 0 };

            let dadosAvant = [];
            if (tentativa >= 3 && !mlAvantScrollExecutada) {
                mlAvantScrollExecutada = true;
                mlPrimeiraPaginaStatusEl.textContent = 'Atualizando dados do Avant Pro nos anÃºncios listados...';
                dadosAvant = await coletarDadosAvantComRolagem();
            } else {
                const resultado = await extrairAnunciosWebviewVisivel({ clicarAvant: true, timeoutMs: 3000 });
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

            if (vendedores || datas || vendas) {
                mlPrimeiraPaginaStatusEl.textContent = `Links extraÃ­dos diretamente da pÃ¡gina exibida no quadro interno. Avant Pro: ${vendedores} vendedor(es), ${datas} data(s) e ${vendas} venda(s) atualizada(s).`;
            } else if (tentativa > 1) {
                mlPrimeiraPaginaStatusEl.textContent = 'Aguardando o Avant Pro inserir vendedor, data de criaÃ§Ã£o e vendas nos cards...';
            }

            return { vendedor: vendedores, data: datas, vendas };
        }

        function agendarAtualizacaoAvantAutomatica(anuncios) {
            if (mlAvantAutoTimer) {
                clearTimeout(mlAvantAutoTimer);
                mlAvantAutoTimer = null;
            }
            const runId = ++mlAvantAutoRunId;
            mlAvantScrollExecutada = false;
            const atrasos = [450, 1200, 2600];
            let indice = 0;

            const executar = async () => {
                if (runId !== mlAvantAutoRunId) return;
                const tentativa = indice + 1;
                try {
                    await atualizarDadosAvantAutomaticamente(anuncios, tentativa);
                } catch (err) {
                    console.warn('Falha na extraÃ§Ã£o automÃ¡tica do Avant Pro:', err);
                }

                indice += 1;
                if (indice < atrasos.length) {
                    mlAvantAutoTimer = setTimeout(executar, atrasos[indice]);
                }
            };

            mlAvantAutoTimer = setTimeout(executar, atrasos[indice]);
        }

        async function abrirMercadoLivreNoPrograma(opcoes = {}) {
            const url = normalizarUrl(mlUrlInput.value);
            mlUrlInput.value = url;
            mlFrameHint.classList.add('hidden');
            abrirBalaoResultadosMl({
                titulo: opcoes.titulo || 'Navegador do Mercado Livre',
                subtitulo: opcoes.subtitulo || url,
                mostrarFavoritos: !!opcoes.mostrarFavoritos
            });

            if (hasInternalBrowserApi || usarNavegadorMlNoShellElectron()) {
                try {
                    await navegarMlWebview(url);
                    mlOpenedOnce = true;
                    agendarAtualizacaoPosicaoNavegadorMlShell();
                    atualizarAnimacaoAzulNoNavegadorMl(mlFavoritosEmExecucao);
                    return true;
                } catch (err) {
                    setBrowserStatus('Falha ao abrir no quadro interno do programa.');
                    mostrarHintAbertura();
                    return false;
                }
            }

            mostrarHintAbertura();
            setBrowserStatus('Este modo web nÃ£o consegue abrir internamente. Use "Abrir Externo" ou execute pelo app Electron.');
            return false;
        }

        async function pesquisarNoMercadoLivreNoPrograma() {
            cancelarAberturaMercadoLivreAoEntrar();
            const termo = (mlSearchTermInput.value || '').trim();
            if (!termo) {
                alert('Digite um termo para pesquisar no Mercado Livre.');
                mlSearchTermInput.focus();
                return;
            }

            mlUrlInput.value = construirUrlPesquisaMercadoLivre(termo);
            const abriu = await abrirMercadoLivreNoPrograma({
                titulo: 'Resultados da pesquisa no Mercado Livre',
                subtitulo: termo
            });
            if (!abriu) {
                mlPrimeiraPaginaStatusEl.textContent = 'NÃ£o foi possÃ­vel abrir o Mercado Livre no quadro interno. Abra pelo app Electron, faÃ§a login/verificaÃ§Ã£o se necessÃ¡rio e pesquise novamente.';
                return;
            }
            await carregarAnunciosPrimeiraPagina(termo);
        }

        async function carregarAnunciosPrimeiraPagina(termo) {
            mlPrimeiraPaginaStatusEl.textContent = 'Buscando anÃºncios da 1Âª pÃ¡gina...';
            mlPrimeiraPaginaEmptyEl.classList.add('hidden');
            mlPrimeiraPaginaTableWrapEl.classList.add('hidden');
            mlPrimeiraPaginaBodyEl.innerHTML = '';
            let tentouElectron = false;
            let ultimoDebugElectron = null;
            let ultimoErroElectron = null;

            // Primeiro tenta extrair da pÃ¡gina exibida no quadro interno do Electron.
            if (mlWebviewEl) {
                tentouElectron = true;
                try {
                    const resultadoWebview = await extrairAnunciosWebviewVisivel({ clicarAvant: true, fastLinks: true, timeoutMs: 9000, aguardarAposCliqueAvant: 1200 });
                    ultimoDebugElectron = resultadoWebview && resultadoWebview.debug;
                    const anunciosWebview = (resultadoWebview && resultadoWebview.anuncios) ? resultadoWebview.anuncios : [];

                    if (resultadoWebview && resultadoWebview.needsLogin) {
                        mlPrimeiraPaginaStatusEl.textContent = 'A pÃ¡gina estÃ¡ pedindo login/verificaÃ§Ã£o. FaÃ§a login no quadro interno e clique em "Pesquisar no ML" novamente.';
                        mlPrimeiraPaginaEmptyEl.classList.remove('hidden');
                        return;
                    }

                    if (anunciosWebview.length) {
                        renderizarAnunciosPrimeiraPagina(anunciosWebview, 'Links extraÃ­dos diretamente da pÃ¡gina exibida no quadro interno.');
                        return;
                    }

                    if (resultadoWebview && resultadoWebview.success === false) {
                        ultimoErroElectron = resultadoWebview.error || 'Erro desconhecido na extraÃ§Ã£o.';
                        console.warn('Falha na extraÃ§Ã£o do webview ML:', resultadoWebview.error || resultadoWebview);
                    }

                    mlPrimeiraPaginaStatusEl.textContent = 'Aguardando mais 4 segundos para a pagina e o AvantPro carregarem...';
                    await esperar(4000);
                    const resultadoRetry = await extrairAnunciosWebviewVisivel({ clicarAvant: true, forcarCliqueAvant: true, fastLinks: true, timeoutMs: 9000, aguardarAposCliqueAvant: 1500 });
                    ultimoDebugElectron = resultadoRetry && resultadoRetry.debug;
                    const anunciosRetry = (resultadoRetry && resultadoRetry.anuncios) ? resultadoRetry.anuncios : [];

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
                    mlPrimeiraPaginaStatusEl.textContent = 'Aguardando mais 4 segundos para tentar extrair novamente...';
                    await esperar(4000);
                    try {
                        const resultadoRetryErro = await extrairAnunciosWebviewVisivel({ clicarAvant: true, forcarCliqueAvant: true, fastLinks: true, timeoutMs: 9000, aguardarAposCliqueAvant: 1500 });
                        ultimoDebugElectron = resultadoRetryErro && resultadoRetryErro.debug;
                        const anunciosRetryErro = (resultadoRetryErro && resultadoRetryErro.anuncios) ? resultadoRetryErro.anuncios : [];
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
                    ? ` PÃ¡gina: ${ultimoDebugElectron.title || 'sem tÃ­tulo'}; links: ${ultimoDebugElectron.linkCount || 0}; cards: ${ultimoDebugElectron.cardCount || 0}.`
                    : '';
                const detalheErro = ultimoErroElectron ? ` Erro: ${String(ultimoErroElectron).slice(0, 180)}.` : '';
                mlPrimeiraPaginaStatusEl.textContent = `Nenhum anÃºncio foi encontrado no navegador interno.${detalhe}${detalheErro} Se aparecer verificaÃ§Ã£o/login do Mercado Livre, conclua no quadro interno e pesquise novamente.`;
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
                    let detail = 'Erro ao carregar anÃºncios da 1Âª pÃ¡gina.';
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
                    : `Total de anÃºncios na 1Âª pÃ¡gina: ${anuncios.length} (na ordem exibida no Mercado Livre)`;
                renderizarAnunciosPrimeiraPagina(anuncios, statusTexto);
            } catch (err) {
                mlPrimeiraPaginaStatusEl.textContent = err.message || 'NÃ£o foi possÃ­vel carregar anÃºncios agora. Tente novamente em instantes.';
                mlPrimeiraPaginaEmptyEl.classList.remove('hidden');
            }
        }

        function renderizarAnunciosPrimeiraPagina(anuncios, statusTexto) {
            const anunciosOriginais = anuncios || [];
            anuncios = anunciosOriginais
                .map((anuncio) => {
                    const mlbId = anuncio && (anuncio.id || extrairItemIdAnuncio(anuncio.url));
                    const fonteVendasInicial = anuncio && (anuncio.vendasFonte || anuncio.vendas_fonte || '');
                    const vendasInicial = parseNumeroVendas(anuncio && anuncio.vendas);
                    const usarVendasInicial = fonteVendasAvantPro(fonteVendasInicial) && hasNumeroVendas(vendasInicial);
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
                tdMedia.title = 'MÃ©dia mensal calculada por vendas e data de criaÃ§Ã£o.';

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

            setTimeout(() => {
                enriquecerDatasCriacaoAnuncios(anuncios).catch(err => {
                    console.warn('Falha ao enriquecer datas dos anÃºncios:', err);
                });
            }, 1500);
        }

        async function enriquecerDatasCriacaoAnuncios(anuncios) {
            const pendentes = (anuncios || [])
                .filter(item => item && item.url);

            if (!pendentes.length) return;

            const statusAnterior = mlPrimeiraPaginaStatusEl.textContent;
            mlPrimeiraPaginaStatusEl.textContent = `${statusAnterior} Identificando vendedores e datas de todos os ${pendentes.length} anÃºncio(s)...`;

            const atualizadasElectron = await tentarDataCriacaoPeloElectron(anuncios);
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
                throw new Error('Erro ao buscar datas de criaÃ§Ã£o dos anÃºncios.');
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
                if (Number.isFinite(vendas) && fonteVendasAvantPro(fonteVendas) && atualizarCelulaVendas(item, vendas)) {
                    vendasAtualizadas += 1;
                }
            });

            const totalAtualizadas = (atualizadasElectron.datas || 0) + atualizadas;
            const totalVendedores = vendedoresElectron + vendedoresAtualizados;
            const totalVendas = (atualizadasElectron.vendas || 0) + vendasAtualizadas;
            mlPrimeiraPaginaStatusEl.textContent = (totalAtualizadas || totalVendedores || totalVendas)
                ? `${statusAnterior} Dados encontrados: ${totalVendedores} vendedor(es), ${totalAtualizadas} data(s) e ${totalVendas} venda(s).`
                : `${statusAnterior} NÃ£o foi possÃ­vel identificar vendedor ou data de criaÃ§Ã£o no cÃ³digo-fonte dos anÃºncios.`;
        }

        function abrirMercadoLivreExterno() {
            const url = normalizarUrl(mlUrlInput.value);
            mlUrlInput.value = url;
            window.open(url, '_blank', 'noopener');
        }

        mlUrlInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') abrirMercadoLivreNoPrograma();
        });

        mlSearchTermInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') pesquisarNoMercadoLivreNoPrograma();
        });

        inicializarBalaoResultadosMl();
        inicializarLarguraTabelasFavoritos();
        atualizarTabelasFavoritosEditaveis();
        renderizarVendedoresIgnoradosRanking();
        carregarVendedoresIgnoradosRankingServidor();
        carregarHistoricoFavoritosServidor();
        favoritosCarregarLojasEntrada();
        setBrowserStatus('Pronto para abrir dentro do programa.');
    
