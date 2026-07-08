const statusEl = document.getElementById('status');
const lojasMlTotalEl = document.getElementById('lojasMlTotal');
const lojasMlTotalVendasEl = document.getElementById('lojasMlTotalVendas');
const lojasMlTotalEnviarEl = document.getElementById('lojasMlTotalEnviar');
const lojasMlTotalAbcEl = document.getElementById('lojasMlTotalAbc');
const lojasMlTotalTransitoEl = document.getElementById('lojasMlTotalTransito');
const lojasMlListaEl = document.getElementById('lojasMlLista');
const lojasMlListaVendasEl = document.getElementById('lojasMlListaVendas');
const lojasMlListaEnviarEl = document.getElementById('lojasMlListaEnviar');
const lojasMlListaAbcEl = document.getElementById('lojasMlListaAbc');
const lojasMlListaTransitoEl = document.getElementById('lojasMlListaTransito');
const resumoEl = document.getElementById('resumo');
const tbody = document.getElementById('tbody');
const buscaEl = document.getElementById('busca');
const ordenarEl = document.getElementById('ordenar');
const btnAtualizar = document.getElementById('btnAtualizar');
const tabButtons = Array.from(document.querySelectorAll('[data-full-tab]'));
const tabPanels = Array.from(document.querySelectorAll('[data-full-panel]'));
const fullSalesAccountEl = document.getElementById('fullSalesAccount');
const btnAplicarVendasFull = document.getElementById('btnAplicarVendasFull');
const fullSalesStatusEl = document.getElementById('fullSalesStatus');
const fullSalesTodayEl = document.getElementById('fullSalesToday');
const fullSalesTrendEl = document.getElementById('fullSalesTrend');
const fullSalesOrdersEl = document.getElementById('fullSalesOrders');
const fullSalesTicketEl = document.getElementById('fullSalesTicket');
const fullSalesProjectionEl = document.getElementById('fullSalesProjection');
const fullSalesForecastEl = document.getElementById('fullSalesForecast');
const fullDayProgressFillEl = document.getElementById('fullDayProgressFill');
const fullDayProgressLabelEl = document.getElementById('fullDayProgressLabel');
const fullSalesYearComparisonEl = document.getElementById('fullSalesYearComparison');
const fullSalesMonthComparisonEl = document.getElementById('fullSalesMonthComparison');
const fullSalesWeekComparisonEl = document.getElementById('fullSalesWeekComparison');
const btnAplicarEnviarFull = document.getElementById('btnAplicarEnviarFull');
const fullSendStatusEl = document.getElementById('fullSendStatus');
const fullSendAccountSummaryEl = document.getElementById('fullSendAccountSummary');
const fullSendTableBodyEl = document.getElementById('fullSendTableBody');
const btnAtualizarTransitoFull = document.getElementById('btnAtualizarTransitoFull');
const fullTransitoStatusEl = document.getElementById('fullTransitoStatus');
const transitoDropZone = document.getElementById('transitoDropZone');
const transitoFileInput = document.getElementById('transitoFileInput');
const btnTransitoSelectFiles = document.getElementById('btnTransitoSelectFiles');
const btnTransitoManual = document.getElementById('btnTransitoManual');
const transitoManualPanel = document.getElementById('transitoManualPanel');
const transitoManualId = document.getElementById('transitoManualId');
const transitoManualCodigo = document.getElementById('transitoManualCodigo');
const transitoManualData = document.getElementById('transitoManualData');
const transitoManualStatus = document.getElementById('transitoManualStatus');
const transitoManualUnidades = document.getElementById('transitoManualUnidades');
const transitoManualItens = document.getElementById('transitoManualItens');
const transitoManualObs = document.getElementById('transitoManualObs');
const btnTransitoSalvarManual = document.getElementById('btnTransitoSalvarManual');
const btnTransitoCancelarManual = document.getElementById('btnTransitoCancelarManual');
const transitoCalendarTitle = document.getElementById('transitoCalendarTitle');
const transitoCalendarMeta = document.getElementById('transitoCalendarMeta');
const transitoCalendarGrid = document.getElementById('transitoCalendarGrid');
const btnTransitoMesAnterior = document.getElementById('btnTransitoMesAnterior');
const btnTransitoProximoMes = document.getElementById('btnTransitoProximoMes');
const transitoProductsPanel = document.getElementById('transitoProductsPanel');
const transitoBusca = document.getElementById('transitoBusca');
const transitoTotalInativos = document.getElementById('transitoTotalInativos');
const transitoInativosList = document.getElementById('transitoInativosList');
const btnTransitoSortEnvio = document.getElementById('btnTransitoSortEnvio');
const btnTransitoSortData = document.getElementById('btnTransitoSortData');
const fullAbcAccountEl = document.getElementById('fullAbcAccount');
const btnAplicarAbcFull = document.getElementById('btnAplicarAbcFull');
const fullAbcStatusEl = document.getElementById('fullAbcStatus');
const fullAbcTableBodyEl = document.getElementById('fullAbcTableBody');
const abcOrderInputs = Array.from(document.querySelectorAll('input[name="abcOrderFull"]'));
const fullCoverageChipsEl = document.getElementById('fullCoverageChips');
const coverageRevenueTotalEl = document.getElementById('coverageRevenueTotal');
const coverageRevenueSubEl = document.getElementById('coverageRevenueSub');
const coverageRevenueStackEl = document.getElementById('coverageRevenueStack');
const coverageStockTotalEl = document.getElementById('coverageStockTotal');
const coverageTurnoverSubEl = document.getElementById('coverageTurnoverSub');
const coverageTurnoverStackEl = document.getElementById('coverageTurnoverStack');
const fullCoverageCardsEl = document.getElementById('fullCoverageCards');
const fullCoverageHeaderRowEl = document.getElementById('fullCoverageHeaderRow');
const fullCoverageTableBodyEl = document.getElementById('fullCoverageTableBody');
const btnSortCoverageDays = document.getElementById('btnSortCoverageDays');
const btnSortCoverageSales = document.getElementById('btnSortCoverageSales');
const btnExportCoverageFull = document.getElementById('btnExportCoverageFull');
const btnCoverageSummary = document.getElementById('btnCoverageSummary');
const btnCoverageDetails = document.getElementById('btnCoverageDetails');
const coverageDetailTable = document.getElementById('coverageDetailTable');
const CONTA_TODAS_FULL = '__todas';
const FULL_CACHE_PREFIX = 'jk_full_cache_v1';
const FULL_CACHE_TTL_MS = 30 * 60 * 1000;
const FULL_CACHE_TTL_VENDAS_HOJE_MS = 5 * 60 * 1000;
const FULL_CACHE_TTL_HISTORICO_MS = 6 * 60 * 60 * 1000;
const FULL_CACHE_TTL_FERIADOS_MS = 30 * 24 * 60 * 60 * 1000;
const fullMemoryCache = new Map();
let dados = [];
let lojasMl = [];
let lojaSelecionada = '';
let abaAtiva = 'estoque';
let carregando = false;
let variacoesAbertas = new Set();
let vendasFullCarregado = false;
let vendasFullChart = null;
let vendasFull60Map = new Map();
let vendasFull60Ready = false;
let vendasFullMesesMap = new Map();
let vendasFullMesesReady = false;
let mesesCoberturaFull = [];
let coberturaRowsAtuais = [];
let coberturaSort = 'dias';
let coberturaFiltroAtivo = 'todos';
let abcCarregado = false;
let abcRows = [];
let abcOrder = 'quantidade';
let enviarFullContaSelecionada = '';
let enviarFullDados = [];
let transitoContaSelecionada = '';
let transitoEnvios = [];
let transitoCalendarDate = new Date();
let transitoCalendarioComercial = null;
let transitoCalendarioComercialLoadingKey = '';
let transitoSort = 'envio';
let transitoCarregado = false;

function safeJsonParse(value, fallback) {
    try { return JSON.parse(value); } catch (_e) { return fallback; }
}

function usuarioCacheFull() {
    const user = safeJsonParse(localStorage.getItem('user_data') || '{}', {});
    return String(user.client_id || user.tenant_id || user.username || user.email || user.id || 'default').trim() || 'default';
}

function chaveCacheFull(tipo, partes = []) {
    const escopo = usuarioCacheFull().replace(/[^\w.-]+/g, '_');
    const detalhe = (Array.isArray(partes) ? partes : [partes])
        .map(item => encodeURIComponent(String(item ?? '').trim()))
        .join(':');
    return `${FULL_CACHE_PREFIX}:${escopo}:${tipo}${detalhe ? ':' + detalhe : ''}`;
}

function lerCacheFull(chave, ttlMs = FULL_CACHE_TTL_MS) {
    const agora = Date.now();
    const emMemoria = fullMemoryCache.get(chave);
    if (emMemoria && (agora - emMemoria.ts) <= ttlMs) return emMemoria.data;
    if (emMemoria) fullMemoryCache.delete(chave);
    try {
        const item = safeJsonParse(localStorage.getItem(chave) || 'null', null);
        if (!item || !item.ts || (agora - item.ts) > ttlMs) {
            localStorage.removeItem(chave);
            return null;
        }
        fullMemoryCache.set(chave, item);
        return item.data;
    } catch (_e) {
        return null;
    }
}

function salvarCacheFull(chave, data) {
    const item = { ts: Date.now(), data };
    fullMemoryCache.set(chave, item);
    try {
        localStorage.setItem(chave, JSON.stringify(item));
    } catch (_e) {
        // Dados muito grandes continuam disponiveis no cache em memoria desta sessao.
    }
}

async function fetchJsonFullCached(chave, url, options = {}) {
    const ttlMs = Number(options.ttlMs || FULL_CACHE_TTL_MS);
    if (!options.force) {
        const cached = lerCacheFull(chave, ttlMs);
        if (cached !== null) return cached;
    }
    const resp = await fetch(url, { headers: obterAuthHeaders(), cache: 'no-store' });
    if (!resp.ok) {
        const detail = await resp.text();
        throw new Error(detail || `HTTP ${resp.status}`);
    }
    const payload = await resp.json();
    salvarCacheFull(chave, payload);
    return payload;
}
