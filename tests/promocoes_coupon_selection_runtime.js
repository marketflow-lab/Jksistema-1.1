'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

class Element {
  constructor() { this.style = {}; this.dataset = {}; this.options = []; this._value = ''; this._html = ''; }
  set value(value) { this._value = value; }
  get value() { return this._value; }
  get selectedOptions() { return this.options.filter(option => option.value === this.value); }
  set innerHTML(value) { this._html = value; this.options = []; this.value = ''; }
  get innerHTML() { return this._html; }
  appendChild(option) { this.options.push(option); if (this.options.length === 1) this.value = option.value; }
}
const ids = Object.fromEntries(['apiPromoASelect', 'apiPromoBSelect', 'apiLojaSelect', 'apiErrorMsg'].map(id => [id, new Element()]));
ids.apiLojaSelect.value = 'test_store';
const requests = [];
const campaigns = [
  { id: 'coupon', name: 'Cupom', type: 'SELLER_COUPON_CAMPAIGN', group: 'usuario' },
  { id: 'normal', name: 'Preço por anúncio', type: 'SELLER_CAMPAIGN', group: 'usuario' },
  { id: 'coupon_b', name: 'Outro cupom', promotion_type: 'SELLER_COUPON_CAMPAIGN', group: 'ml' },
  { id: 'deal', name: 'Campanha ML', type: 'DEAL', group: 'ml' },
];
const context = {
  console, Set, URLSearchParams,
  apiAutoInicializada: false,
  apiPromoBCampaigns: [], apiPromoBSelectedIds: new Set(), apiPromoBPendingSelectedIds: null, apiPromoBSelectionReady: false,
  document: { getElementById: id => ids[id] || null, createElement: () => new Element() },
  fetch: async (url, options) => {
    requests.push({ url, options });
    return { ok: true, json: async () => ({ campaigns, config: { enabled: false } }) };
  },
  getAuthHeadersWithClient: () => ({}),
  getActionTolerancePct: () => 0,
  escapeHtml: value => String(value),
  extractApiPromoBIdsFromMeta: () => [],
  API_AUTO_INTERVAL_UNITS: { minutes: { max: 100, factor: 1 } },
  API_AUTO_MIN_INTERVAL_MIN: 1,
};
vm.createContext(context);
for (const file of ['api-mercadolivre.js', 'automacao-api.js']) {
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'frontend_promo', file), 'utf8'), context, { filename: file });
}
Object.assign(context, {
  getAuthHeadersWithClient: () => ({}),
  renderApiPromoBFiles() {},
  atualizarApiPromoBSelectionSummary() {},
  carregarContagensCampanhasApi: async () => ({}),
  applyPromoCounts: campaign => campaign,
  promoSelectionGroup: campaign => campaign.group,
  isCampanhaMercadoLivre: campaign => campaign.group === 'ml',
  formatPromoOption: campaign => campaign.name,
  atualizarStatusAutomacaoPromo() {},
  aplicarApiAutoIntervalPrefs() {}, persistApiAutoPrefsObject() {},
  pararTimerProximaVerificacaoPromo() {}, salvarApiAutoNextRunAt() {},
  atualizarStatusAutomacaoPromoPorPrefs() {}, iniciarPollingAutomacaoPromoServidor() {},
});
const select = ids.apiPromoASelect;
function selectCoupon() {
  select.options = [{ value: 'coupon', dataset: { promoType: 'SELLER_COUPON_CAMPAIGN' } }];
  select.value = 'coupon';
}

(async () => {
  await context.carregarPromocoesApi();
  assert.deepStrictEqual(select.options.map(option => option.value), ['normal'], ids.apiErrorMsg.textContent);
  assert.strictEqual(select.value, 'normal', 'initial compatible selection keeps existing behavior');
  assert.deepStrictEqual(Array.from(context.apiPromoBCampaigns, campaign => campaign.id), ['deal']);
  assert(!ids.apiPromoBSelect.innerHTML.includes('coupon'));

  selectCoupon();
  const countBefore = requests.length;
  const result = await context.processarAnaliseApi();
  assert.strictEqual(result.status, 'error');
  assert.strictEqual(requests.length, countBefore, 'legacy coupon must be rejected before job request');
  assert(ids.apiErrorMsg.textContent.includes('cupom por compra'));

  await context.carregarPromocoesApi();
  assert.strictEqual(select.value, '', 'removed coupon cannot silently select another campaign');
  assert.strictEqual(select.dataset.couponBlocked, '1');
  assert.strictEqual((await context.processarAnaliseApi()).status, 'error');

  context.renderApiPromoBButtons(campaigns);
  assert(!Array.from(context.apiPromoBCampaigns).some(campaign => campaign.id.startsWith('coupon')));
  context.apiPromoBCampaigns = campaigns;
  context.apiPromoBSelectedIds = new Set(['coupon', 'coupon_b', 'deal']);
  context.apiPromoBSelectionReady = true;
  assert.deepStrictEqual(Array.from(context.getApiPromoBSelections(), item => item.value), ['deal']);

  let enabled = true;
  context.montarPayloadApiAutoServidor = () => ({ enabled });
  const beforeSave = requests.length;
  await assert.rejects(context.salvarApiAutoPrefsServidor(), /cupom por compra/);
  assert.strictEqual(requests.length, beforeSave, 'enabled legacy coupon config cannot be sent');
  assert.strictEqual(ids.apiErrorMsg.style.display, 'block');
  enabled = false;
  await context.salvarApiAutoPrefsServidor();
  assert.strictEqual(requests.length, beforeSave + 1, 'disabling incompatible automation must stay allowed');
  assert.strictEqual(JSON.parse(requests.at(-1).options.body).enabled, false);

  const legacyPrefs = context.apiAutoPrefsFromServerConfig({ promocao_a_type: 'SELLER_COUPON_CAMPAIGN' });
  select.value = 'normal';
  context.aplicarApiAutoPrefsTela(legacyPrefs);
  assert.strictEqual(select.value, '', 'legacy server preferences must not reassign the selected campaign');
  assert(context.getApiPromoACompatibilityError().includes('cupom por compra'));
  select.value = 'normal';
  assert.strictEqual(context.getApiPromoACompatibilityError(), '', 'explicit compatible selection clears the blocker');
  context.aplicarApiAutoPrefsTela(legacyPrefs);
  assert.strictEqual(select.value, 'normal', 'polling legacy preferences must preserve explicit compatible selection');
  console.log('promocoes coupon selection runtime checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
