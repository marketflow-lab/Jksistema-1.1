"use strict";

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

class Element {
  constructor() { this.children = []; this.listeners = {}; this.style = {}; this.dataset = {}; this.attributes = {}; this.value = ''; this.scrollTop = 0; }
  set innerHTML(value) { this.html = String(value); }
  get innerHTML() { return this.html || ''; }
  appendChild(child) { this.children.push(child); child.parentElement = this; return child; }
  remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(child => child !== this); }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  querySelector(selector) {
    if (selector === '.promo-result-body') return this.resultBody ||= { scrollTop: 0 };
    if (selector === '.promo-decision-technical') return this.badge || null;
    return null;
  }
  click(selector) {
    const button = { disabled: false, closest: query => query === selector ? button : null };
    for (const callback of this.listeners.click || []) callback({ target: button });
  }
}

const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
function environment(count = 1) {
  let clock = 0, nextTimer = 0, client = 'tenant-A';
  const timers = new Map(), listeners = new Map(), requests = [], queue = [], tableRows = [];
  const ids = Object.fromEntries(['apiLojaSelect', 'apiPromoASelect', 'apiMargemMinima'].map(id => [id, new Element()]));
  ids.apiLojaSelect.value = 'Loja A'; ids.apiPromoASelect.value = 'PROMO-A'; ids.apiMargemMinima.value = '15';
  ids.apiPromoASelect.selectedOptions = [{ dataset: { promoType: 'SELLER_CAMPAIGN' } }];
  const body = new Element();
  const context = {
    console, Set, Map, Intl, AbortController,
    Date: class extends Date { static now() { return clock; } },
    setTimeout: (callback, delay) => { const id = ++nextTimer; timers.set(id, { at: clock + delay, callback }); return id; },
    clearTimeout: id => timers.delete(id),
    document: { body, getElementById: id => ids[id] || null, createElement: () => new Element(),
      querySelector: selector => body.children.find(el => selector === '.' + el.className) || null,
      querySelectorAll: selector => selector === '#tabelaAnalise tbody tr' ? tableRows : [],
      addEventListener: (type, fn) => { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn); },
      removeEventListener: (type, fn) => listeners.get(type)?.delete(fn),
    },
    getPromoClientId: () => client,
    escapeHtml: text => String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'),
    getAuthHeadersWithClient: () => ({ 'X-Client-Id': client }),
    getApiPromoBSelections: () => [{ value: 'LIGHTNING-A', promoType: 'LIGHTNING' }],
    getActionTolerancePct: () => 0, findApiPromoBCampaignById: () => ({}),
    apiAnalisesPorCampanha: [], currentData: [], apiAnaliseGeracao: 1, apiAnaliseAtiva: 0,
    TABLE_COLUMNS_MODERN: [], TABLE_COLUMN_ALIASES: {},
    fetch: async (url, options) => {
      requests.push({ url, ...options });
      const response = queue.shift();
      if (typeof response === 'function') return response(options);
      if (response instanceof Error) throw response;
      assert(response, 'unexpected network call');
      return { ok: response.ok !== false, json: async () => response };
    },
    renderApiAnalysisTabs() {}, atualizarStatusAutomacaoPromo() {}, iniciarStatusParticipacaoPromocoes() {},
    concluirStatusParticipacaoPromocoes() {}, falharStatusParticipacaoPromocoes() {},
    buildApiErrorMessage: () => 'API error', alert: message => { throw new Error(message); },
    getApiAutoPrefs: () => ({ enabled: true, approvalRequired: false }),
  };
  vm.createContext(context);
  const apiSource = fs.readFileSync(path.join(__dirname, '..', 'static', 'frontend_promo', 'api-mercadolivre.js'), 'utf8');
  vm.runInContext(apiSource.slice(apiSource.indexOf('function formatarJanelaSelecaoRelampago('), apiSource.indexOf('function montarMetaSelecaoPromoB('))
    + apiSource.slice(apiSource.indexOf('function getPromoCampaignDisplayName('), apiSource.indexOf('function promoSelectionGroup(')), context);
  for (const file of ['participacao.js', 'tabela-preferencias.js', 'arquivos-render.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'frontend_promo', file), 'utf8'), context, { filename: file });
  }
  context.pedirConfirmacaoParticipacaoCampanhaApi = async () => true;
  const rows = Array.from({ length: count }, (_, i) => ({ analysis_client_ref: 'ref-' + i, MLB: 'MLB' + (i + 100),
    'Ação': 'Participar', Status: 'Ativo', action_promotion_id: 'LIGHTNING-A', action_offer_id: 'CANDIDATE-' + i,
    action_deal_price: 147.46, action_tecnico_apto: true, lightning_action_blocked: false,
    lightning_offer_key: 'key-' + i, lightning_schedule_pending: true,
    lightning_start_date: '', lightning_finish_date: '', lightning_stock_min: 5, lightning_stock_max: 72, lightning_stock: 5 }));
  for (const row of rows) {
    const tr = new Element(); tr.attributes['data-analysis-client-ref'] = row.analysis_client_ref;
    tr.style.display = 'none'; tr.badge = { style: {}, textContent: '' };
    tr.selection = { value: 'Participar', checked: true }; tableRows.unshift(tr);
  }
  const analysis = { job_id: 'job-A', promo_b_id: 'LIGHTNING-A', promo_b_type: 'LIGHTNING', data: rows,
    origem_loja: 'Loja A', origem_client_id: 'tenant-A', participacao_contexto: context.capturarParametrosParticipacaoPromocoes() };
  context.apiAnalisesPorCampanha = [analysis]; context.currentData = rows;
  const detail = (index = 0, outcome = 'unknown', extra = {}) => ({ promotion_id: 'LIGHTNING-A',
    client_ref: 'ref-' + index, item_id: 'MLB' + (index + 100), outcome,
    success: ['applied', 'already_participating'].includes(outcome), ...extra });
  const result = (details = [detail()], pending = true) => ({ confirmation_id: '838c34e0a13849248192cdce8c00211a',
    confirmation_pending: pending, detalhes: details, total_sucesso: 999, total_falha: 999 });
  return { context, ids, body, requests, queue, rows, analysis, timers, tableRows, detail, result,
    payload: () => context.montarPayloadParticipacoesPromocoes(0),
    setClient: value => { client = value; }, emit: type => { for (const fn of listeners.get(type) || []) fn({}); },
    modal: () => body.children.find(el => el.className === 'promo-result-modal-backdrop'),
    async start(initial = result(), automatic = false) { queue.push(initial); await (automatic ? context.executarParticipacaoAutomaticaPromocoes() : context.confirmarParticipacaoCampanhaApi(0)); return this.modal(); },
    async tick(ms) {
      const end = clock + ms;
      while (true) {
        const due = [...timers.entries()].filter(([, value]) => value.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        clock = due[1].at; timers.delete(due[0]); due[1].callback(); await flush();
      }
      clock = end; await flush();
    },
  };
}

(async () => {
  // Same case as production: 15 refusals and one initially inconclusive registration.
  let e = environment(16);
  const rejected = Array.from({ length: 15 }, (_, i) => e.detail(i + 1, 'rejected'));
  const initial = e.result([e.detail(), ...rejected]);
  const modal = await e.start(initial);
  assert(modal.innerHTML.includes('<strong>15</strong><span>não concluído(s)</span>'));
  assert(modal.innerHTML.includes('<strong>1</strong><span>aguardando confirmação</span>'));
  assert(modal.innerHTML.includes('Verificar confirmação'));
  const tr = e.tableRows.find(row => row.attributes['data-analysis-client-ref'] === 'ref-0');
  const selection = tr.selection, order = [...e.tableRows];
  modal.querySelector('.promo-result-body').scrollTop = 37;
  e.queue.push(e.result([e.detail(0, 'unknown', { provider_status: 'candidate' }), ...rejected]));
  await e.tick(2000);
  assert.strictEqual(e.analysis.data[0].action_ja_participa, undefined);
  e.queue.push(e.result([e.detail(0, 'applied', { status: 'pending', price_diagnostics: { confirmation_check_count: 3 } }), ...rejected], false));
  await e.tick(5000);
  assert.strictEqual(e.modal(), modal, 'poll updates the existing modal');
  assert(modal.innerHTML.includes('Oferta programada no Mercado Livre.'));
  assert(modal.innerHTML.includes('Consultas de confirmação: 3'));
  assert(modal.innerHTML.includes('<strong>1</strong><span>confirmado(s)</span>'));
  assert(!modal.innerHTML.includes('<span>aguardando confirmação</span>'));
  assert.strictEqual(e.analysis.participacao_resultado.total_sucesso, 1, 'derive counts from bound details, not server totals');
  assert.strictEqual(e.rows[0].action_ja_participa, true, 'pending confirms even without dates');
  assert.strictEqual(e.rows[0].Status, 'Ativo', 'reference promotion status is preserved');
  assert.strictEqual(e.rows[0]['Ação'], 'Participar');
  assert.strictEqual(tr.badge.textContent, 'Já participa');
  assert.strictEqual(tr.selection, selection); assert.strictEqual(selection.checked, true);
  assert.strictEqual(tr.style.display, 'none'); assert.deepStrictEqual(e.tableRows, order);
  assert.strictEqual(modal.querySelector('.promo-result-body').scrollTop, 37);
  assert.strictEqual(e.requests.filter(request => request.method === 'POST').length, 1);
  assert(e.requests.slice(1).every(request => request.method === 'GET' && request.url.includes('?loja=Loja%20A') && request.headers['X-Client-Id'] === 'tenant-A'));
  await e.tick(60000); assert.strictEqual(e.requests.length, 3, 'confirmed registration stops polling');

  // A replay cannot regress a confirmed item while another item is still pending.
  e = environment(2); await e.start();
  e.queue.push(e.result([e.detail(0, 'applied'), e.detail(1)])); await e.tick(2000);
  e.queue.push(e.result([e.detail(0), e.detail(1, 'applied', { status: 'started' })], false)); await e.tick(5000);
  assert.strictEqual(e.analysis.participacao_resultado.total_sucesso, 2);
  assert(e.rows.every(row => row.action_ja_participa));
  assert.strictEqual(e.analysis.participacao_confirmada, true);

  const diagnostics = e.context.formatarDiagnosticoPrecoParticipacao({ suggested_price: 75.08, price_source: 'suggested_discounted_price' }).join(' ');
  assert(diagnostics.includes('Outra sugestão do Mercado Livre:'));
  assert(diagnostics.includes('75,08')); assert(diagnostics.includes('Preço alternativo sugerido pelo Mercado Livre.'));
  assert(!diagnostics.includes('suggested_discounted_price'));

  // Identity ambiguity, foreign items and untrusted summary counters never confirm a row.
  e = environment(); const payload = e.payload();
  for (const altered of [entry => { entry.item_id = 'MLB999'; }, entry => { entry.promotion_id = 'OTHER'; },
    entry => { entry.client_ref = 'foreign'; }, entry => { delete entry.item_id; }]) {
    const detail = e.detail(0, 'applied'); altered(detail);
    const merged = e.context.mesclarResultadoConfirmacaoParticipacao(payload, e.result(), e.result([detail], false));
    assert.strictEqual(merged.total_sucesso, 0);
  }
  let merged = e.context.mesclarResultadoConfirmacaoParticipacao(payload, e.result(), e.result([e.detail(0, 'applied'), e.detail(0, 'applied')], false));
  assert.strictEqual(merged.total_sucesso, 0, 'duplicate response references are ambiguous');
  const duplicatePayload = JSON.parse(JSON.stringify(payload)); duplicatePayload.promocoes[0].items.push({ ...duplicatePayload.promocoes[0].items[0], item_id: 'MLB999' });
  merged = e.context.mesclarResultadoConfirmacaoParticipacao(duplicatePayload, e.result(), e.result([e.detail(0, 'applied')], false));
  assert.strictEqual(merged.total_sucesso, 0, 'same original ref bound to different items is ambiguous');

  // Confirming one date never changes a second analysis with the same campaign and item.
  e = environment();
  e.analysis.selection_id = 'window-A'; e.analysis.lightning_window = { start_date: '2026-10-09T15:00:00+00:00', finish_date: '2026-10-09T23:00:00+00:00' };
  const otherRow = { ...e.rows[0], analysis_client_ref: 'other-date-ref' };
  const otherAnalysis = { ...e.analysis, selection_id: 'window-B', lightning_window: { start_date: '2026-10-10T15:00:00+00:00', finish_date: '2026-10-10T23:00:00+00:00' }, data: [otherRow] };
  e.context.apiAnalisesPorCampanha.push(otherAnalysis);
  const contextOneDate = e.context.capturarContextoConsultaConfirmacao(e.payload(), [e.analysis, otherAnalysis]);
  assert.strictEqual(contextOneDate.snapshots.length, 1);
  assert.strictEqual(contextOneDate.snapshots[0].analise, e.analysis);
  await e.start(); e.queue.push(e.result([e.detail(0, 'applied', { analysis_row_updates: { action_deal_price: 17 } })], false)); await e.tick(2000);
  assert.strictEqual(e.analysis.participacao_confirmada, true);
  assert.strictEqual(e.rows[0].action_deal_price, 17);
  assert.strictEqual(otherRow.action_deal_price, 147.46);
  assert.strictEqual(otherRow.action_ja_participa, undefined);
  assert.strictEqual(otherAnalysis.participacao_resultado, undefined);
  assert.strictEqual(otherAnalysis.participacao_confirmada, undefined);

  // Legacy index references remain tied to their original analysis and item.
  e = environment(); e.analysis.promo_b_type = 'DEAL'; delete e.rows[0].analysis_client_ref;
  const legacyOtherRow = { ...e.rows[0] };
  const legacyOther = { ...e.analysis, data: [legacyOtherRow] };
  e.context.apiAnalisesPorCampanha.push(legacyOther);
  const legacyPayload = e.payload(); assert.strictEqual(legacyPayload.promocoes[0].items[0].client_ref, '0:0');
  const legacyContext = e.context.capturarContextoConsultaConfirmacao(legacyPayload, [e.analysis, legacyOther]);
  assert.strictEqual(legacyContext.snapshots.length, 1);
  await e.start(e.result([{ ...e.detail(), client_ref: '0:0' }]));
  e.queue.push(e.result([{ ...e.detail(0, 'applied'), client_ref: '0:0' }], false)); await e.tick(2000);
  assert.strictEqual(e.rows[0].action_ja_participa, true); assert.strictEqual(e.analysis.participacao_confirmada, true);
  assert.strictEqual(legacyOtherRow.action_ja_participa, undefined); assert.strictEqual(legacyOther.participacao_resultado, undefined);

  // Context changes cancel before GET, and late replies cannot touch replacement data.
  for (const change of [env => env.setClient('tenant-B'), env => { env.ids.apiLojaSelect.value = 'Loja B'; },
    env => { env.analysis.job_id = 'job-new'; }, env => { env.context.apiAnalisesPorCampanha = [{ ...env.analysis }]; },
    env => { env.analysis.data = [...env.rows]; }, env => { env.analysis.selection_id = 'changed-window'; },
    env => { env.analysis.lightning_window = { undated: true }; }, env => { env.context.apiAnaliseGeracao++; }, env => { env.context.apiAnaliseAtiva++; },
    env => { env.context.invalidarContextoParticipacaoPromocoes(); }]) {
    e = environment(); await e.start(); change(e); e.emit('change'); await e.tick(60000);
    assert.strictEqual(e.requests.length, 1); assert.strictEqual(e.rows[0].action_ja_participa, undefined);
  }
  for (const close of [true, false]) {
    e = environment(); await e.start();
    let finish; e.queue.push(() => new Promise(resolve => { finish = resolve; })); await e.tick(2000);
    if (close) e.modal().click('[data-close-promo-result]'); else e.setClient('tenant-B');
    finish({ ok: true, json: async () => e.result([e.detail(0, 'applied')], false) }); await flush();
    assert.strictEqual(e.rows[0].action_ja_participa, undefined, 'late response has no effect after cancellation');
    if (close) assert.strictEqual(e.modal(), undefined, 'late response cannot reopen modal');
    await e.tick(60000); assert.strictEqual(e.requests.length, 2);
  }
  e = environment(); await e.start(); e.modal().click('[data-close-promo-result]'); await e.tick(60000);
  assert.strictEqual(e.requests.length, 1);

  // Bounded GETs remain resumable manually, with no POST or changed selection.
  e = environment(); await e.start();
  for (let i = 0; i < 7; i++) e.queue.push(e.result());
  await e.tick(60000);
  const automaticCount = e.requests.length;
  assert(automaticCount <= 8, 'at most seven automatic confirmations within one minute');
  assert(e.modal().innerHTML.includes('Você pode consultar novamente.'));
  await e.tick(120000); assert.strictEqual(e.requests.length, automaticCount);
  e.queue.length = 0; e.queue.push(e.result([e.detail(0, 'applied')], false));
  e.modal().click('[data-check-promo-confirmation]'); await flush();
  assert.strictEqual(e.requests.length, automaticCount + 1);
  assert.strictEqual(e.requests.filter(request => request.method === 'POST').length, 1);
  assert.strictEqual(e.rows[0].action_ja_participa, true);

  // GET failures and a wrong confirmation ID preserve the previous result and permit retry.
  e = environment(); await e.start();
  e.queue.push(new Error('network unavailable')); await e.tick(2000);
  assert(e.modal().innerHTML.includes('não será reenviada'));
  e.queue.push({ ...e.result([e.detail(0, 'applied')], false), confirmation_id: 'other' }); await e.tick(5000);
  assert.strictEqual(e.rows[0].action_ja_participa, undefined);
  e.queue.push(e.result([e.detail(0, 'applied')], false)); await e.tick(10000);
  assert.strictEqual(e.rows[0].action_ja_participa, true);

  // A new result modal cancels the old one, including an outstanding GET.
  e = environment(); await e.start(); const oldModal = e.modal();
  let finishOld; e.queue.push(() => new Promise(resolve => { finishOld = resolve; })); await e.tick(2000);
  const previousRequest = e.requests.at(-1);
  e.context.mostrarResumoParticipacaoPromocoes(e.payload(), e.result([e.detail(0, 'rejected')], false), { analisesOriginais: [e.analysis] });
  assert(previousRequest.signal.aborted); assert.notStrictEqual(e.modal(), oldModal);
  finishOld({ ok: true, json: async () => e.result([e.detail(0, 'applied')], false) }); await flush();
  assert.strictEqual(e.rows[0].action_ja_participa, undefined);

  // A stalled GET is aborted and stays eligible for a later read-only confirmation.
  e = environment(); await e.start();
  e.queue.push(options => new Promise((_, reject) => options.signal.addEventListener('abort', () => reject(new Error('aborted')))));
  await e.tick(2000); const stalled = e.requests.at(-1); await e.tick(15000);
  assert(stalled.signal.aborted); assert(e.modal().innerHTML.includes('não será reenviada'));
  e.queue.push(e.result([e.detail(0, 'applied')], false)); await e.tick(5000);
  assert.strictEqual(e.rows[0].action_ja_participa, true);
  assert.strictEqual(e.requests.filter(request => request.method === 'POST').length, 1);

  // Capture selection and context before submission, so completion cannot approve a new selection.
  e = environment();
  e.queue.push(async () => { e.analysis.participacao_selecao_revisao = 1; return { ok: true, json: async () => e.result() }; });
  await e.context.confirmarParticipacaoCampanhaApi(0);
  e.queue.push(e.result([e.detail(0, 'applied')], false)); await e.tick(2000);
  assert.strictEqual(e.rows[0].action_ja_participa, true, 'the previously submitted item is confirmed');
  assert.strictEqual(e.analysis.participacao_confirmada, false, 'the altered selection is not confirmed');
  e = environment();
  e.queue.push(async () => { e.ids.apiMargemMinima.value = '16'; return { ok: true, json: async () => e.result() }; });
  await e.context.confirmarParticipacaoCampanhaApi(0); await e.tick(60000);
  assert.strictEqual(e.requests.length, 1, 'changed parameters during POST cancel confirmation polling');

  // The automatic participation path uses the same bounded confirmation mechanism.
  e = environment(); await e.start(e.result(), true);
  e.queue.push(e.result([e.detail(0, 'applied')], false)); await e.tick(2000);
  assert.strictEqual(e.rows[0].action_ja_participa, true);
  assert.strictEqual(e.requests.filter(request => request.method === 'POST').length, 1);
  console.log('promocoes delayed confirmation UI runtime checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
