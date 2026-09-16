'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}
class Element {
  constructor() { this.style = {}; this.dataset = {}; this.value = ''; this.children = []; }
  set innerHTML(value) { this.html = value; this.children = []; }
  appendChild(child) { this.children.push(child); }
}
const ids = Object.fromEntries(['apiLojaSelect', 'apiPromoASelect', 'apiPromoBSelect', 'apiMargemMinima', 'apiMargemTolerancia',
  'apiLoading', 'apiLoadingDetails', 'apiErrorMsg', 'resultsArea', 'apiAnalysisTabs', 'apiCancelJobBtn']
  .map(id => [id, new Element()]));
ids.apiLojaSelect.value = 'A';
ids.apiPromoASelect.value = 'fixed';
ids.apiMargemMinima.value = '15';
let clientId = 'tenant-1';
const intervals = new Map();
let nextTimer = 1;
const starts = [];
const progress = new Map();
const requests = [];
let listQueue = null;
const response = body => ({ ok: true, json: async () => body });
const completed = label => response({ success: true, status: 'completed', result: {
  analises: [{ promo_b_id: 'campaign', data: [{ MLB: label, Acao: 'Participar' }] }],
} });
const context = {
  console, URLSearchParams, FormData,
  document: { getElementById: id => ids[id] || null, createElement: () => new Element() },
  localStorage: { getItem: key => key === 'user_data' ? JSON.stringify({ client_id: clientId }) : null },
  setInterval: callback => { const id = nextTimer++; intervals.set(id, callback); return id; },
  clearInterval: id => intervals.delete(id),
  fetch: (url, options) => {
    requests.push({ url, options });
    if (url.endsWith('/start')) { const item = deferred(); starts.push(item); return item.promise; }
    if (url.includes('/progresso/')) {
      const id = url.split('/').pop();
      const item = deferred(); progress.set(id, item); return item.promise;
    }
    if (url.startsWith('/api/mercadolivre/promocoes?')) {
      if (listQueue) { const item = deferred(); listQueue.push(item); return item.promise; }
      return Promise.resolve(response({ success: true, campaigns: [] }));
    }
    throw new Error(`Unexpected request: ${url}`);
  },
  alert: value => { throw new Error(value); },
};
vm.createContext(context);
for (const file of ['runtime.js', 'api-mercadolivre.js', 'automacao-api.js']) {
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'frontend_promo', file), 'utf8'), context, { filename: file });
}
for (const name of ['saveColumnPrefs', 'savePagePrefs', 'mergeAndSaveColumnWidthPrefs', 'saveColumnWidthPrefsFromDomRobust',
  'renderApiJobDetails', 'atualizarApiStatusBar', 'ocultarApiStatusBarDepois', 'renderTable', 'renderApiAnalysisTabs',
  'garantirColunasVisiveis', 'renderApiPromoBFiles']) context[name] = () => {};
Object.assign(context, {
  loadColumnWidthPrefs: () => ({}), getColumnWidthPrefsFromDom: () => ({}),
  carregarColumnWidthPrefsServidor: async () => {}, carregarContagensCampanhasApi: async () => ({}),
  normalizeApiDatasetRules: rows => rows,
  executarParticipacaoAutomaticaPromocoes: async () => {},
  getApiPromoBSelections: () => [{ value: 'campaign', promoType: 'DEAL', activeCount: 1, eligibleCount: 1 }],
  applyPromoCounts: value => value, promoSelectionGroup: () => 'ml', isCampanhaMercadoLivre: () => true,
  renderApiPromoBButtons: rows => { context.lastCampaigns = rows; },
});
const flush = async () => { for (let n = 0; n < 12; n++) await Promise.resolve(); };
const state = expr => vm.runInContext(expr, context);
async function startJob(id) {
  const run = context.processarAnaliseApi();
  await flush();
  starts.at(-1).resolve(response({ success: true, job_id: id }));
  await flush();
  assert(progress.has(id), `job ${id} must reach progress polling`);
  return { run, pending: progress.get(id) };
}

(async () => {
  const firstA = await startJob('first-A');
  ids.apiLojaSelect.value = 'B';
  await context.carregarPromocoesApi();
  assert.strictEqual((await firstA.run).status, 'superseded', 'switching store resolves the first in-flight poll immediately');
  assert.strictEqual(state('currentData.length'), 0);
  const firstB = await startJob('first-B');
  ids.apiLojaSelect.value = 'A';
  await context.carregarPromocoesApi();
  assert.strictEqual((await firstB.run).status, 'superseded');
  const newA = await startJob('new-A');
  firstA.pending.resolve(completed('old-A'));
  firstB.pending.resolve(completed('old-B'));
  await flush();
  assert.strictEqual(state('currentData.length'), 0, 'A to B to A must reject both older generations');
  assert.strictEqual(intervals.size, 1, 'obsolete completions must not clear the current polling timer');
  newA.pending.resolve(completed('new-A'));
  assert.strictEqual((await newA.run).status, 'completed');
  assert.strictEqual(state('currentData[0].MLB'), 'new-A');
  assert.strictEqual(state('apiAnalisesPorCampanha[0].origem_loja'), 'A');
  assert.strictEqual(state('apiAnalisesPorCampanha[0].origem_client_id'), 'tenant-1');
  assert.strictEqual(intervals.size, 0);

  ids.apiMargemMinima.value = '18';
  ids.apiMargemTolerancia.value = '3';
  const lateStart = context.processarAnaliseApi();
  ids.apiMargemMinima.value = '25';
  ids.apiMargemTolerancia.value = '7';
  await flush();
  const pendingStart = starts.at(-1);
  const capturedParameters = requests.filter(req => req.url.endsWith('/start')).at(-1).options.body;
  assert.strictEqual(capturedParameters.get('margem_minima'), '18', 'editing parameters while preparation awaits must not change the started analysis');
  assert.strictEqual(capturedParameters.get('margem_tolerancia'), '3');
  const newer = await startJob('newer-same-store');
  newer.pending.resolve(completed('newer-same-store'));
  await newer.run;
  pendingStart.resolve(response({ success: true, job_id: 'obsolete-start' }));
  assert.strictEqual((await lateStart).status, 'superseded');
  assert.strictEqual(progress.has('obsolete-start'), false, 'late start response must not attach polling to an obsolete job');
  assert.strictEqual(state('currentData[0].MLB'), 'newer-same-store');
  assert.strictEqual(requests.filter(req => req.url.endsWith('/start')).at(-1).options.body.get('margem_minima'), '25', 'new analysis sends the updated parameters');
  assert.strictEqual(requests.filter(req => req.url.endsWith('/start')).at(-1).options.body.get('margem_tolerancia'), '7');

  const tenantOld = await startJob('old-tenant');
  clientId = 'tenant-2';
  await context.carregarPromocoesApi();
  assert.strictEqual((await tenantOld.run).status, 'superseded');
  const tenantNew = await startJob('new-tenant');
  tenantOld.pending.resolve(completed('old-tenant'));
  tenantNew.pending.resolve(completed('new-tenant'));
  await tenantNew.run;
  assert.strictEqual(state('apiAnalisesPorCampanha[0].origem_client_id'), 'tenant-2');
  assert.strictEqual(requests.find(req => req.url.endsWith('/old-tenant')).options.headers['X-Client-ID'], 'tenant-1');
  assert.strictEqual(requests.find(req => req.url.endsWith('/new-tenant')).options.headers['X-Client-ID'], 'tenant-2');

  listQueue = [];
  const listA1 = context.carregarPromocoesApi();
  ids.apiLojaSelect.value = 'B';
  const listB = context.carregarPromocoesApi();
  ids.apiLojaSelect.value = 'A';
  const listA2 = context.carregarPromocoesApi();
  listQueue[2].resolve(response({ success: true, campaigns: [{ id: 'latest', type: 'DEAL' }] }));
  await listA2;
  listQueue[0].resolve(response({ success: true, campaigns: [{ id: 'stale-A', type: 'DEAL' }] }));
  listQueue[1].reject(new Error('obsolete B failed'));
  await Promise.all([listA1, listB]);
  assert.strictEqual(context.lastCampaigns[0].id, 'latest', 'list and error responses must also honor generation across A to B to A');
  assert.strictEqual(ids.apiErrorMsg.style.display, 'none');
  assert(!requests.some(req => req.url.includes('/cancelar/')), 'changing scope must not cancel remote jobs');
  console.log('promocoes analysis scope runtime checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
