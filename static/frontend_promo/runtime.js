let currentData = [];
let uploadedFilesMap = {}; // Mapa para guardar os objetos File originais
let mlFileName = null; // Nome do arquivo ML identificado pelo backend
let planilhaGeradaAtual = null;
let modoAnaliseAtual = 'api';
const PREFS_NAMESPACE = 'promo_prefs_v4';
const WIDTH_PREFS_VERSION = 'w4';
const DEFAULT_MIN_COLUMN_WIDTH = 44;
let actionTolerancePct = 0;
let pageState = { page: 1, pageSize: 100 };
let pendingWidthPrefs = null;
let saveWidthPrefsTimer = null;
let apiPromoBCampaigns = [];
let apiPromoBSelectedIds = new Set();
let apiPromoBSelectionReady = false;
let apiPromoBPendingSelectedIds = null;
let apiAnalisesPorCampanha = [];
let apiAnaliseAtiva = 0;
let apiAnaliseJobPolling = null;
let apiAnaliseJobId = '';
let apiStatusHideTimer = null;
let apiStatusFechadoManualmente = false;
let promoParticipacaoStatusTimer = null;
let apiAutoTimer = null;
let apiAutoCountdownTimer = null;
let apiAutoServerPollTimer = null;
let apiAutoInicializada = false;
let apiAutoRunning = false;
let apiAutoNextRunAt = 0;
let apiAnaliseCancelada = false;
let apiAnaliseResolveAtual = null;
const API_AUTO_DEFAULT_INTERVAL_MIN = 60;
const API_AUTO_MIN_INTERVAL_MIN = 1;
const API_AUTO_MAX_TIMEOUT_MS = 2140000000;
const API_AUTO_INTERVAL_UNITS = {
    minutes: { label: 'minuto(s)', factor: 1, max: 10080 },
    hours: { label: 'hora(s)', factor: 60, max: 720 },
    days: { label: 'dia(s)', factor: 1440, max: 30 },
    weeks: { label: 'semana(s)', factor: 10080, max: 12 },
};
const TABLE_COLUMNS_MODERN = [
    { key: 'Tipo', label: 'Tipo' },
    { key: '%', label: '%' },
    { key: 'SKU', label: 'SKU' },
    { key: 'Título', label: 'Título' },
    { key: 'Frete', label: 'Frete' },
    { key: 'Frete ML', label: 'Frete ML' },
    { key: 'Frete Gratis', label: 'Frete Gratis' },
    { key: 'Frete Gratis ML', label: 'Frete Gratis ML' },
    { key: 'Custo', label: 'Custo' },
    { key: 'Tarifa', label: 'Tarifa' },
    { key: 'Tarifa ML', label: 'Tarifa ML' },
    { key: 'MLB', label: 'MLB' },
    { key: 'Campanha ML', label: 'Campanha ML' },
    { key: '% Fixa', label: '% Fixa' },
    { key: 'ML % Campanha', label: 'ML % Campanha' },
    { key: 'Preço Final', label: 'Preço Final Promoção 1' },
    { key: 'Imposto %', label: '% Imposto' },
    { key: 'Imposto', label: 'Imposto Promoção 1' },
    { key: 'Preço Final ML', label: 'Preço Final Promoção 2' },
    { key: 'Imposto ML', label: 'Imposto Promoção 2' },
    { key: 'Desconto ML', label: 'Desconto ML' },
    { key: 'Valor Líquido', label: 'Valor Líquido' },
    { key: 'Valor líquido ML', label: 'Valor líquido ML' },
    { key: 'Status', label: 'Status' },
    { key: 'Margem', label: 'Margem' },
    { key: 'Margem ML', label: 'Margem ML' },
    { key: 'Ação', label: 'Ação', editable: true }
];
const TABLE_COLUMN_ALIASES = {
    'Tipo': ['Tipo', 'type', 'listing_type_name', 'Tipo Anuncio', 'Tipo Anúncio'],
    '%': ['%', '% ', ' %', 'percentual', 'fee_per_sale', 'tarifa de venda'],
    'SKU': ['SKU', 'sku', 'seller_sku', 'Seller SKU'],
    'Título': ['Título', 'Titulo', 'title', 'Title', 'Nome', 'Produto'],
    'Frete': ['Frete', 'frete', 'shipping_cost'],
    'Frete ML': ['Frete ML', 'Frete Mercado Livre', 'frete_ml', 'shipping_cost_ml'],
    'Frete Gratis': ['Frete Gratis', 'Frete Grátis', 'Frete grátis', 'free_shipping'],
    'Frete Gratis ML': ['Frete Gratis ML', 'Frete Grátis ML', 'Frete grátis ML', 'free_shipping_ml'],
    'Custo': ['Custo', 'custo', 'cost', 'Custo Produto', 'Custo unitário', 'Custo unitario'],
    'Tarifa': ['Tarifa', 'tarifa', 'sale_fee_amount', 'Tarifa Promoção 1', 'Tarifa Promocao 1'],
    'Tarifa ML': ['Tarifa ML', 'tarifa_ml', 'Tarifa Mercado Livre', 'Tarifa Promoção 2', 'Tarifa Promocao 2'],
    'MLB': ['MLB', 'mlb', 'Item ID', 'item_id', 'Anúncio', 'Anuncio'],
    'Campanha ML': ['Campanha ML', 'Campanha', 'Campanha Mercado Livre', 'promo_b_nome', 'promotion_name', 'Nome Promoção'],
    '% Fixa': ['% Fixa', '% fixa', 'percentual_fixo', 'Desconto fixo', 'discount_percentage'],
    'ML % Campanha': ['ML % Campanha', '% Campanha ML', 'ML %', 'Desconto ML %', 'discount_percentage', 'percentual'],
    'Preço Final': ['Preço Final', 'Preco Final', 'Preço Final Promoção 1', 'Preco Final Promocao 1', 'Preço final promoção 1', 'M 21 Fixa'],
    'Imposto %': ['Imposto %', '% Imposto', 'Imposto Percentual', 'Taxa Imposto', 'aliquota_imposto'],
    'Imposto': ['Imposto', 'Imposto Promoção 1', 'Imposto Promocao 1', 'imposto_valor'],
    'Preço Final ML': ['Preço Final ML', 'Preco Final ML', 'Preço Final Promoção 2', 'Preco Final Promocao 2', 'Preço final ML', 'M ML'],
    'Imposto ML': ['Imposto ML', 'Imposto Promoção 2', 'Imposto Promocao 2', 'imposto_ml'],
    'Desconto ML': ['Desconto ML', 'Desconto Mercado Livre', 'discount_ml', 'promotion_fee_discount_text'],
    'Valor Líquido': ['Valor Líquido', 'Valor Liquido', 'Valor líquido', 'Valor liquido', 'Valor líquido Promoção 1', 'Valor Liquido Promocao 1'],
    'Valor líquido ML': ['Valor líquido ML', 'Valor Líquido ML', 'Valor Liquido ML', 'Valor liquido ML', 'Valor líquido Promoção 2', 'Valor Liquido Promocao 2'],
    'Status': ['Status', 'status', 'Situação', 'Situacao'],
    'Margem': ['Margem', 'margem', 'Margem Promoção 1', 'Margem Promocao 1'],
    'Margem ML': ['Margem ML', 'margem_ml', 'Margem Promoção 2', 'Margem Promocao 2'],
    'Ação': ['Ação', 'Acao', 'Participar ou não', 'Participar ou nao', 'acao']
};
let tableColumns = TABLE_COLUMNS_MODERN;
let columnState = [];

function getColumnsSchemaTag() {
    return 'modern';
}

function ensureColumnsSchema(_data) {
    tableColumns = TABLE_COLUMNS_MODERN;
}

function sanitizePrefScope(value) {
    return String(value || '')
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9._-]+/g, '_') || 'anon';
}

function getCurrentUserPrefScope() {
    try {
        const userData = JSON.parse(localStorage.getItem('user_data') || '{}');
        const clientId = sanitizePrefScope(userData.client_id || 'sem_cliente');
        const username = sanitizePrefScope(userData.username || userData.name || 'sem_usuario');
        return `${clientId}__${username}`;
    } catch (_e) {
        return 'sem_cliente__sem_usuario';
    }
}

function getColumnPrefsKey() {
    return `${PREFS_NAMESPACE}__columns__${getColumnsSchemaTag()}__${getCurrentUserPrefScope()}`;
}

function getPagePrefsKey() {
    return `${PREFS_NAMESPACE}__page__${getCurrentUserPrefScope()}`;
}

function getColumnWidthPrefsKey() {
    return `${PREFS_NAMESPACE}__widths__${WIDTH_PREFS_VERSION}__${getColumnsSchemaTag()}__${getCurrentUserPrefScope()}`;
}

function getActionToleranceKey() {
    return `${PREFS_NAMESPACE}__acao_tolerancia__${getCurrentUserPrefScope()}`;
}

function getApiAutoPrefsKey() {
    return `${PREFS_NAMESPACE}__api_auto__${getCurrentUserPrefScope()}`;
}

function loadActionTolerance() {
    try {
        const raw = localStorage.getItem(getActionToleranceKey());
        const n = Number(String(raw ?? '').replace(',', '.'));
        actionTolerancePct = Number.isFinite(n) && n >= 0 ? n : 0;
    } catch (_e) {
        actionTolerancePct = 0;
    }
    const input = document.getElementById('apiMargemTolerancia');
    if (input) input.value = String(actionTolerancePct);
}

function saveActionTolerance() {
    try {
        localStorage.setItem(getActionToleranceKey(), String(actionTolerancePct));
    } catch (_e) {}
}

function getActionTolerancePct() {
    const input = document.getElementById('apiMargemTolerancia');
    const raw = input ? input.value : actionTolerancePct;
    const parsed = Number(String(raw ?? '').replace(',', '.'));
    const sanitized = Number.isFinite(parsed) ? Math.max(0, Math.min(100, parsed)) : 0;
    actionTolerancePct = sanitized;
    if (input) input.value = String(sanitized);
    return sanitized;
}

function extractApiPromoBIdsFromMeta(meta) {
    if (!Array.isArray(meta)) return [];
    const ids = meta.map((item) => {
        if (item && typeof item === 'object') {
            return String(item.promo_b_id || item.promotion_id || item.value || item.id || '').trim();
        }
        return String(item || '').trim();
    }).filter(Boolean);
    return Array.from(new Set(ids));
}
