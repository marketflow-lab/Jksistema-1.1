const path = require('path');

const ROOT_DIR = path.resolve(__dirname, '../../..');
const REPORT_DIR = process.env.BUG_HUNTER_REPORT_DIR
  ? path.resolve(process.env.BUG_HUNTER_REPORT_DIR)
  : path.join(ROOT_DIR, 'bug-hunter-report');
const EVENTS_FILE = path.join(REPORT_DIR, 'events.jsonl');
const REPORT_MD = path.join(REPORT_DIR, 'bug-hunter-report.md');
const REPORT_JSON = path.join(REPORT_DIR, 'bug-hunter-report.json');
const METADATA_FILE = path.join(REPORT_DIR, 'run-metadata.json');

function readBool(value, fallback) {
  if (value === undefined || value === null || value === '') return fallback;
  return /^(1|true|yes|sim|on)$/i.test(String(value).trim());
}

function normalizeBaseUrl(raw) {
  const value = String(raw || 'http://127.0.0.1:8001').trim();
  const url = new URL(value);
  return {
    origin: url.origin,
    path: url.pathname && url.pathname !== '/' ? url.pathname : '',
    raw: value,
  };
}

function normalizeListingId(value) {
  return String(value || '').trim().replace(/\s+/g, '').toUpperCase();
}

function getBugHunterConfig() {
  const base = normalizeBaseUrl(process.env.BUG_HUNTER_BASE_URL);
  const username = String(
    process.env.BUG_HUNTER_USERNAME
      || process.env.BUG_HUNTER_EMAIL
      || process.env.ML_TEST_EMAIL
      || '',
  ).trim();
  const password = String(
    process.env.BUG_HUNTER_PASSWORD
      || process.env.ML_TEST_PASSWORD
      || '',
  );
  const dryRun = readBool(process.env.BUG_HUNTER_DRY_RUN, true);
  const sandbox = readBool(process.env.MELI_SANDBOX, false)
    || readBool(process.env.BUG_HUNTER_SANDBOX, false);

  const config = {
    rootDir: ROOT_DIR,
    reportDir: REPORT_DIR,
    eventsFile: EVENTS_FILE,
    reportMd: REPORT_MD,
    reportJson: REPORT_JSON,
    metadataFile: METADATA_FILE,
    baseUrl: base.origin,
    rawBaseUrl: base.raw,
    loginPath: process.env.BUG_HUNTER_LOGIN_PATH || base.path || '/frontend_index.html',
    adsPath: process.env.BUG_HUNTER_ADS_PATH || '/anunciosml.html',
    username,
    password,
    dryRun,
    sandbox,
    allowMutations: readBool(process.env.BUG_HUNTER_ALLOW_MUTATIONS, false),
    allowMissingSecrets: readBool(process.env.BUG_HUNTER_ALLOW_MISSING_SECRETS, false),
    storeName: String(process.env.BUG_HUNTER_STORE || '').trim(),
    testListingId: normalizeListingId(process.env.BUG_HUNTER_TEST_LISTING_ID),
    testSku: String(process.env.BUG_HUNTER_TEST_SKU || '').trim(),
    testPrice: String(process.env.BUG_HUNTER_TEST_PRICE || '1.23').trim(),
    testStock: String(process.env.BUG_HUNTER_TEST_STOCK || '1').trim(),
    testTitle: String(process.env.BUG_HUNTER_TEST_TITLE || 'Bug Hunter Dry Run Title').trim(),
    loginTimeoutMs: Number(process.env.BUG_HUNTER_LOGIN_TIMEOUT_MS || 45_000),
    pageTimeoutMs: Number(process.env.BUG_HUNTER_PAGE_TIMEOUT_MS || 45_000),
    selectors: {
      username: process.env.BUG_HUNTER_USERNAME_SELECTOR || '#username',
      password: process.env.BUG_HUNTER_PASSWORD_SELECTOR || '#password',
      loginButton: process.env.BUG_HUNTER_LOGIN_BUTTON_SELECTOR || '#login-button',
      storeSelect: process.env.BUG_HUNTER_STORE_SELECTOR || '#lojaSelect',
      skuFilter: process.env.BUG_HUNTER_SKU_SELECTOR || '#filtroSku',
      searchButton: process.env.BUG_HUNTER_SEARCH_BUTTON_SELECTOR || '#btnBuscarSku',
      listingRows: process.env.BUG_HUNTER_LISTING_ROWS_SELECTOR || '#anunciosTable tbody tr:not(.variation-row)',
      priceItem: process.env.BUG_HUNTER_PRICE_ITEM_SELECTOR || '#precoItemId',
      priceValue: process.env.BUG_HUNTER_PRICE_VALUE_SELECTOR || '#precoValor',
      priceButton: process.env.BUG_HUNTER_PRICE_BUTTON_SELECTOR || '#btnAtualizarPreco',
      priceMessage: process.env.BUG_HUNTER_PRICE_MESSAGE_SELECTOR || '#precoMsg',
      stockItem: process.env.BUG_HUNTER_STOCK_ITEM_SELECTOR || '#estoqueItemId',
      stockValue: process.env.BUG_HUNTER_STOCK_VALUE_SELECTOR || '#estoqueQtd',
      stockButton: process.env.BUG_HUNTER_STOCK_BUTTON_SELECTOR || '#btnAtualizarEstoque',
      stockMessage: process.env.BUG_HUNTER_STOCK_MESSAGE_SELECTOR || '#estoqueMsg',
      titleItem: process.env.BUG_HUNTER_TITLE_ITEM_SELECTOR || '#tituloItemId',
      titleValue: process.env.BUG_HUNTER_TITLE_VALUE_SELECTOR || '#tituloValor',
      titleButton: process.env.BUG_HUNTER_TITLE_BUTTON_SELECTOR || '#btnAtualizarTitulo',
      titleMessage: process.env.BUG_HUNTER_TITLE_MESSAGE_SELECTOR || '#tituloMsg',
    },
  };

  config.missingSecrets = [];
  if (!config.username) config.missingSecrets.push('BUG_HUNTER_USERNAME or BUG_HUNTER_EMAIL');
  if (!config.password) config.missingSecrets.push('BUG_HUNTER_PASSWORD');

  config.absoluteUrl = (pathOrUrl) => new URL(pathOrUrl, config.baseUrl).toString();
  config.loginUrl = () => config.absoluteUrl(config.loginPath);
  config.adsUrl = () => config.absoluteUrl(config.adsPath);

  return config;
}

function safeConfigForReport(config = getBugHunterConfig()) {
  return {
    baseUrl: config.baseUrl,
    loginPath: config.loginPath,
    adsPath: config.adsPath,
    usernameConfigured: Boolean(config.username),
    passwordConfigured: Boolean(config.password),
    dryRun: config.dryRun,
    sandbox: config.sandbox,
    allowMutations: config.allowMutations,
    storeConfigured: Boolean(config.storeName),
    testListingIdConfigured: Boolean(config.testListingId),
    testSkuConfigured: Boolean(config.testSku),
    missingSecrets: config.missingSecrets,
  };
}

module.exports = {
  ROOT_DIR,
  REPORT_DIR,
  EVENTS_FILE,
  REPORT_MD,
  REPORT_JSON,
  METADATA_FILE,
  getBugHunterConfig,
  readBool,
  safeConfigForReport,
};
