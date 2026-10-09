'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

class Element {
  constructor() { this.children = []; this.style = {}; this.dataset = {}; this.attributes = {}; this.listeners = {}; this.value = ''; this.checked = false; this.classes = new Set();
    this.classList = { add: (...names) => names.forEach(name => this.classes.add(name)), remove: (...names) => names.forEach(name => this.classes.delete(name)) }; }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return (this.text || '') + this.children.map(child => child.textContent).join(''); }
  set innerHTML(value) { this.html = String(value); this.children = []; }
  get innerHTML() { return this.html || ''; }
  appendChild(child) { this.children.push(child); child.parentElement = this; return child; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key] ?? null; }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  emit(name) { for (const callback of this.listeners[name] || []) callback({ target: this }); }
  querySelector() { return null; }
  remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(child => child !== this); }
}

const ids = Object.fromEntries(['apiLojaSelect', 'apiPromoASelect', 'apiMargemMinima', 'apiMargemTolerancia',
  'apiAutoWorkEnabled', 'apiAutoApprovalRequired', 'apiAutoIntervalMinutes', 'apiAutoIntervalUnit'].map(id => [id, new Element()]));
ids.apiLojaSelect.value = 'Loja A';
ids.apiPromoASelect.value = 'PROMO-A';
ids.apiPromoASelect.selectedOptions = [{ dataset: { promoType: 'SELLER_CAMPAIGN' } }];
ids.apiMargemMinima.value = '15';
ids.apiMargemTolerancia.value = '2';
const body = new Element();
const table = new Element();
table.dataset.filter = 'produto';
const tbody = new Element();
let clientId = 'cliente-A';
let selectedCampaigns = [{ value: 'LIGHTNING-A', promoType: 'LIGHTNING' }];
let requested = [];
let alerts = [];
const context = {
  console, Date, Intl, Set, Map, apiAnalisesPorCampanha: [], currentData: [],
  apiPromoBCampaigns: [], apiPromoBSelectedIds: new Set(), apiPromoBSelectionReady: false,
  apiAnaliseGeracao: 1, apiAnaliseAtiva: 0, apiAutoInicializada: false, actionTolerancePct: 2,
  TABLE_COLUMNS_MODERN: [], TABLE_COLUMN_ALIASES: {},
  document: { body, getElementById: id => ids[id] || null, createElement: () => new Element(),
    querySelector: () => null, querySelectorAll: selector => selector === '#tabelaAnalise tbody tr' ? tbody.children : [],
    addEventListener() {}, removeEventListener() {} },
  setTimeout: () => 1,
  localStorage: { getItem: key => key === 'user_data' ? JSON.stringify({ client_id: clientId }) : null },
  alert: message => alerts.push(message),
  escapeHtml: value => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
};
vm.createContext(context);
for (const file of ['participacao.js', 'tabela-preferencias.js', 'arquivos-render.js', 'api-mercadolivre.js', 'automacao-api.js']) {
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'frontend_promo', file), 'utf8'), context, { filename: file });
}
Object.assign(context, {
  getApiPromoBSelections: () => selectedCampaigns,
  getActionTolerancePct: () => Number(ids.apiMargemTolerancia.value || 0),
  findApiPromoBCampaignById: () => ({}),
  renderTable: () => { throw new Error('A atualização de preço não deve redesenhar a tabela.'); },
  renderApiAnalysisTabs() {}, getApiAutoPrefs: () => ({ enabled: true, approvalRequired: false }),
  atualizarStatusAutomacaoPromo() {}, iniciarStatusParticipacaoPromocoes() {},
  concluirStatusParticipacaoPromocoes() {}, falharStatusParticipacaoPromocoes() {},
  mostrarResumoParticipacaoPromocoes() {}, getAuthHeadersWithClient: extra => extra || {},
  buildApiErrorMessage: () => 'Falha da API', pedirConfirmacaoParticipacaoCampanhaApi: async () => true,
});

function row(ref, date = '2026-10-09') {
  return { analysis_client_ref: ref, MLB: 'MLB3502651779', 'Ação': 'Participar', 'Preço Final ML': 'R$ 110,68', 'Margem ML': '25%',
    action_deal_price: 110.68, action_promotion_id: 'LIGHTNING-A', action_offer_id: 'offer-' + ref,
    action_financeiro_exato: false, action_financeiro_estimado: true, action_tecnico_apto: true,
    lightning_offer_key: 'key-' + ref, lightning_start_date: date + 'T15:00:00Z', lightning_finish_date: date + 'T23:00:00Z',
    lightning_stock_min: 1, lightning_stock_max: 10, lightning_stock: 1, lightning_action_blocked: false };
}
function install(rows) {
  const analysis = { job_id: 'job-completed', promo_b_id: 'LIGHTNING-A', promo_b_type: 'LIGHTNING', data: rows,
    origem_loja: 'Loja A', origem_client_id: 'cliente-A', participacao_contexto: context.capturarParametrosParticipacaoPromocoes() };
  context.apiAnalisesPorCampanha = [analysis]; context.currentData = rows;
  return analysis;
}
function detail(item, updates = {}) {
  return { client_ref: item.client_ref, item_id: item.item_id, promotion_id: 'LIGHTNING-A', outcome: 'applied', success: true,
    analysis_row_updates: updates,
    price_diagnostics: { initial_price: 110.68, official_price: 66.32, sent_price: 66.32, standard_price: 91.01,
      sale_price: 71.89, min_price: 55, max_price: 70, recalculated_margin: 17.48, attempt_count: 2,
      decision_code: 'approved', ml_error_code: 'ERROR_CREDIBILITY_DISCOUNTED_PRICE', http_status: 201 } };
}

(async () => {
  const rows = [row('ref-A'), row('ref-B', '2026-10-10')];
  const analysis = install(rows);
  let payload = context.montarPayloadParticipacoesPromocoes(0);
  assert.strictEqual(payload.analysis_job_id, 'job-completed');
  assert.strictEqual(payload.price_policy, 'refresh_and_reanalyse');
  assert.deepStrictEqual(Array.from(payload.promocoes[0].items, item => item.client_ref), ['ref-A', 'ref-B']);
  assert.strictEqual(context.montarPayloadParticipacoesPromocoes(null, { automatica: true }).promocoes[0].items.length, 2,
    'tarifa estimada aprovada continua elegível na automação');
  rows[0]['Ação'] = 'Não participar';
  assert.strictEqual(context.montarPayloadParticipacoesPromocoes(0).promocoes[0].items[0].client_ref, 'ref-B', 'referência não muda ao desmarcar outro convite');
  rows[0]['Ação'] = 'Participar';

  for (const id of ['apiMargemMinima', 'apiMargemTolerancia', 'apiPromoASelect', 'apiLojaSelect']) {
    const previous = ids[id].value;
    ids[id].value = id === 'apiLojaSelect' ? 'Loja B' : '99';
    assert.throws(() => context.montarPayloadParticipacoesPromocoes(0), /nova análise|não pertence/);
    ids[id].value = previous;
  }
  selectedCampaigns = [{ value: 'OTHER', promoType: 'LIGHTNING' }];
  assert.throws(() => context.montarPayloadParticipacoesPromocoes(0), /nova análise/);
  selectedCampaigns = [{ value: 'LIGHTNING-A', promoType: 'LIGHTNING' }];
  clientId = 'cliente-B'; assert.throws(() => context.montarPayloadParticipacoesPromocoes(0), /não pertence/); clientId = 'cliente-A';
  context.invalidarContextoParticipacaoPromocoes();
  assert.throws(() => context.montarPayloadParticipacoesPromocoes(0), /nova análise/);
  assert.strictEqual(rows[0]['Ação'], 'Participar', 'invalidar contexto preserva a seleção');
  install(rows).job_id = '';
  assert.throws(() => context.montarPayloadParticipacoesPromocoes(0), /nova análise/);
  install(rows); const canonical = rows[0].analysis_client_ref; rows[0].analysis_client_ref = '';
  assert.throws(() => context.montarPayloadParticipacoesPromocoes(0), /nova análise/); rows[0].analysis_client_ref = canonical;
  const mixedAnalysis = install(rows);
  context.apiAnalisesPorCampanha.push({ ...mixedAnalysis, job_id: 'another-job', data: [row('ref-C')] });
  assert.throws(() => context.montarPayloadParticipacoesPromocoes(), /nova análise conjunta/);

  const currentAnalysis = install(rows);
  payload = context.montarPayloadParticipacoesPromocoes(0);
  const trA = new Element(), trB = new Element();
  trA.setAttribute('data-analysis-client-ref', 'ref-A'); trB.setAttribute('data-analysis-client-ref', 'ref-B');
  const select = new Element(); select.value = 'Participar'; select.checked = true;
  const selectionCell = new Element(); selectionCell.setAttribute('data-promo-column-key', 'Ação'); selectionCell.appendChild(select);
  const price = new Element(); price.setAttribute('data-promo-column-key', 'Preço Final ML'); price.textContent = rows[0]['Preço Final ML'];
  const margin = new Element(); margin.setAttribute('data-promo-column-key', 'Margem ML'); margin.classes.add('margin-low');
  const otherPrice = new Element(); otherPrice.setAttribute('data-promo-column-key', 'Preço Final ML'); otherPrice.textContent = rows[1]['Preço Final ML'];
  trA.appendChild(selectionCell); trA.appendChild(price); trA.appendChild(margin); trB.appendChild(otherPrice);
  trA.style.display = 'none'; tbody.appendChild(trB); tbody.appendChild(trA);
  const originalOrder = [...tbody.children];
  const result = { detalhes: [detail(payload.promocoes[0].items[0], { 'Preço Final ML': 'R$ 66,32', 'Margem ML': '17,48%',
    action_deal_price: 66.32, action_discount_percentage: 27, _jk_tarifa_ml_estimada: true, _jk_tarifa_ml_estimativa_motivo: 'Estimativa oficial',
    'Ação': 'Não participar', analysis_client_ref: 'attack', lightning_offer_key: 'attack', Custo: { untrusted: true } })] };
  context.aplicarAtualizacoesParticipacaoPromocoes(payload, result, [currentAnalysis]);
  assert.strictEqual(rows[0].action_deal_price, 66.32);
  assert.strictEqual(rows[0].action_discount_percentage, 27);
  assert.strictEqual(rows[1].action_deal_price, 110.68, 'mesmo MLB em outra data permanece separado');
  assert.strictEqual(rows[0]['Ação'], 'Participar'); assert.strictEqual(rows[0].analysis_client_ref, 'ref-A');
  assert.strictEqual(rows[0].lightning_offer_key, 'key-ref-A'); assert.strictEqual(rows[0].Custo, undefined);
  assert.strictEqual(select.checked, true); assert.strictEqual(selectionCell.children[0], select);
  assert.deepStrictEqual(tbody.children, originalOrder); assert.strictEqual(trA.style.display, 'none');
  assert.strictEqual(table.dataset.filter, 'produto'); assert.strictEqual(price.textContent, 'R$ 66,32');
  assert.strictEqual(otherPrice.textContent, 'R$ 110,68'); assert(margin.classes.has('margin-top')); assert(!margin.classes.has('margin-low'));
  const mojibakeUpdate = detail(payload.promocoes[0].items[0], { 'PreÃ§o Final ML': 'R$ 65,90', 'Valor lÃ­quido ML': 'R$ 49,00' });
  context.aplicarAtualizacoesParticipacaoPromocoes(payload, { detalhes: [mojibakeUpdate] }, [currentAnalysis]);
  assert.strictEqual(rows[0]['Preço Final ML'], 'R$ 65,90'); assert.strictEqual(rows[0]['Valor líquido ML'], 'R$ 49,00');
  assert.strictEqual(price.textContent, 'R$ 65,90', 'alias legado com encoding inconsistente atualiza a coluna canônica');

  const before = rows[0].action_deal_price;
  for (const mutate of [entry => { entry.promotion_id = 'OTHER'; }, entry => { entry.item_id = 'MLB999'; }, entry => { entry.client_ref = 'ref-OTHER'; }]) {
    const entry = detail(payload.promocoes[0].items[0], { action_deal_price: 1 }); mutate(entry);
    context.aplicarAtualizacoesParticipacaoPromocoes(payload, { detalhes: [entry] }, [currentAnalysis]);
    assert.strictEqual(rows[0].action_deal_price, before);
  }
  context.aplicarAtualizacoesParticipacaoPromocoes(payload, { detalhes: [detail(payload.promocoes[0].items[0], { action_deal_price: 1 }),
    detail(payload.promocoes[0].items[0], { action_deal_price: 2 })] }, [currentAnalysis]);
  assert.strictEqual(rows[0].action_deal_price, before, 'respostas individuais ambíguas não atualizam uma linha');
  currentAnalysis.job_id = 'another-job'; context.aplicarAtualizacoesParticipacaoPromocoes(payload, result, [currentAnalysis]);
  assert.strictEqual(rows[0].action_deal_price, before); currentAnalysis.job_id = 'job-completed';
  install(rows); context.aplicarAtualizacoesParticipacaoPromocoes(payload, result, [currentAnalysis]);
  assert.strictEqual(rows[0].action_deal_price, before, 'resposta de objeto de análise substituído não toca análise nova');

  const duplicatePayload = { ...payload, promocoes: [{ ...payload.promocoes[0], items: [payload.promocoes[0].items[0], payload.promocoes[0].items[0]] }] };
  assert.strictEqual(context.resumirResultadoParticipacao(duplicatePayload, result).total_itens, 1);
  assert.strictEqual(context.resumirResultadoParticipacao(duplicatePayload, result).total_sucesso, 1, 'duas tentativas contam um anúncio');
  assert.strictEqual(context.resumirResultadoParticipacao({ ...payload, promocoes: [duplicatePayload.promocoes[0], duplicatePayload.promocoes[0]] }, result).total_itens, 1,
    'o mesmo convite duplicado em grupos da campanha também conta uma vez');
  const lines = context.montarLinhasResultadoParticipacao(duplicatePayload, result);
  const html = context.renderListaResultadoParticipacao('Confirmados', lines[0].confirmados);
  for (const label of ['Preço inicial:', 'Proposta atualizada:', 'Preço enviado:', 'Preço-base:', 'Preço atual de venda:',
    'Limite mínimo:', 'Limite máximo:', 'Margem recalculada: 17,48%', 'Tentativas de envio: 2', 'Código Mercado Livre:']) assert(html.includes(label), label);
  const malicious = { ...result.detalhes[0], message: '<img src=x onerror=alert(1)>', price_diagnostics: { ...result.detalhes[0].price_diagnostics, ml_error_code: '<script>' } };
  const escaped = context.renderListaResultadoParticipacao('Recusados', context.montarLinhasResultadoParticipacao(duplicatePayload, { detalhes: [malicious] })[0].confirmados);
  assert(!escaped.includes('<script>')); assert(!escaped.includes('<img')); assert(escaped.includes('&lt;script&gt;'));
  assert.deepStrictEqual(Array.from(context.formatarDiagnosticoPrecoParticipacao({ initial_price: null, sent_price: '', standard_price: undefined, attempt_count: 0 })), ['Tentativas de envio: 0']);

  // Preview keeps the existing schedule-pending path and explains the financial rule.
  const pendingRow = row('schedule-pending'); pendingRow.lightning_schedule_pending = true;
  pendingRow.lightning_start_date = ''; pendingRow.lightning_finish_date = '';
  const pendingAnalysis = install([pendingRow]);
  const pendingPayload = context.montarPayloadParticipacoesPromocoes(0);
  const previewPromise = context.pedirConfirmacaoParticipacaoCampanhaApi;
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static/frontend_promo/participacao.js'), 'utf8'), context);
  Object.assign(context, { parsePromoCount: () => null, resolvePromoActiveCount: () => null, resolvePromoEligibleCount: () => null, formatPromoDateRange: () => '-' });
  const preview = context.pedirConfirmacaoParticipacaoCampanhaApi({ nome: 'Relâmpago', total: 1, loja: 'Loja A', promo: pendingPayload.promocoes[0], analise: pendingAnalysis, campanha: {} });
  const modal = body.children.at(-1);
  assert(modal.innerHTML.includes('Atualização da proposta'));
  assert(modal.innerHTML.includes('mínimo e a tolerância desta análise'));
  assert(modal.innerHTML.includes('Agenda das ofertas relâmpago'));
  modal.listeners.click[0]({ target: { closest: selector => selector === '[data-cancel-promo-participation]' } });
  assert.strictEqual(await preview, false);
  context.pedirConfirmacaoParticipacaoCampanhaApi = previewPromise;
  context.mostrarResumoParticipacaoPromocoes = () => {};

  const fresh = install([row('manual')]);
  context.fetch = async (_url, options) => {
    const sent = JSON.parse(options.body); requested.push(sent);
    return { ok: true, json: async () => ({ detalhes: [detail(sent.promocoes[0].items[0], { action_deal_price: 66.32, 'Margem ML': '17,48%' })] }) };
  };
  await context.confirmarParticipacaoCampanhaApi(0);
  assert.strictEqual(requested.length, 1); assert.strictEqual(requested[0].price_policy, 'refresh_and_reanalyse');
  assert.strictEqual(requested[0].analysis_job_id, 'job-completed'); assert.strictEqual(fresh.data[0].action_deal_price, 66.32);
  await context.executarParticipacaoAutomaticaPromocoes();
  assert.strictEqual(requested.length, 2); assert.strictEqual(requested[1].price_policy, 'refresh_and_reanalyse');
  assert.strictEqual(requested[1].promocoes[0].items[0].client_ref, 'manual');
  context.pedirConfirmacaoParticipacaoCampanhaApi = async () => { ids.apiMargemMinima.value = '16'; return true; };
  await context.confirmarParticipacaoCampanhaApi(0);
  assert.strictEqual(requested.length, 2, 'mudar margem durante o modal não envia pedido obsoleto');
  assert(alerts.some(message => message.includes('nova análise')));
  ids.apiMargemMinima.value = '15';

  // Real listeners invalidate only the participation context, preserving financial rows and choices.
  Object.assign(context, {
    getApiAutoPrefs: () => ({ enabled: false, approvalRequired: true }),
    aplicarApiAutoIntervalPrefs() {}, saveApiAutoPrefs() {}, saveActionTolerance() {}, renderTable() {},
    carregarApiAutoPrefsServidor: () => new Promise(() => {}),
    getApiPromoACompatibilityError: () => '',
  });
  context.inicializarAutomacaoPromoApi();
  for (const [id, event] of [['apiMargemMinima', 'input'], ['apiMargemTolerancia', 'change'], ['apiPromoASelect', 'change']]) {
    const retained = install([row('event-' + id)]); const beforeRows = JSON.stringify(retained.data);
    ids[id].emit(event);
    assert.strictEqual(retained.participacao_contexto_invalidado, true, id);
    assert.strictEqual(JSON.stringify(retained.data), beforeRows, 'listener preserva dados e decisões: ' + id);
    assert.throws(() => context.montarPayloadParticipacoesPromocoes(0), /nova análise/);
  }
  let retained = install([row('event-all')]);
  context.renderApiPromoBButtons = () => {}; context.salvarApiPromoBSelectionPrefs = () => {};
  context.selecionarTodasApiPromoB(); assert.strictEqual(retained.participacao_contexto_invalidado, true);
  retained = install([row('event-clear')]); context.limparSelecaoApiPromoB(); assert.strictEqual(retained.participacao_contexto_invalidado, true);
  retained = install([row('event-toggle')]);
  context.atualizarApiPromoBSelectionSummary = () => {}; context.renderApiPromoBFiles = () => {};
  context.toggleApiPromoBSelection({ value: 'LIGHTNING-A', checked: true, closest: () => null });
  assert.strictEqual(retained.participacao_contexto_invalidado, true);
  retained = install([row('event-prompt')]); context.prompt = () => '3';
  context.promptToleranciaAcao(); assert.strictEqual(retained.participacao_contexto_invalidado, true);
  console.log('promocoes lightning price reanalysis UI runtime checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
