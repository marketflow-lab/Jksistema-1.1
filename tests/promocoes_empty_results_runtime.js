'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag;
    this.children = [];
    this.style = {};
    this.dataset = {};
    this.classList = { add() {}, remove() {} };
    this.value = '';
    this._text = '';
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set innerHTML(value) {
    assert.strictEqual(value, '', 'result rendering must use text nodes, never raw HTML');
    this.textContent = '';
  }
  appendChild(child) { this.children.push(child); return child; }
  setAttribute() {}
  addEventListener() {}
}

const ids = Object.fromEntries([
  'analysisEmptyState', 'tabelaAnalise', 'totalAnuncios', 'totalParticipar', 'totalNaoParticipar',
].map(id => [id, new Element()]));
const thead = new Element('tr');
const tbody = new Element('tbody');
const timers = [];
const alerts = [];
let requests = 0;
const context = {
  console,
  currentData: [],
  apiAnalisesPorCampanha: [],
  apiAnaliseAtiva: 0,
  pendingWidthPrefs: null,
  document: {
    body: new Element('body'),
    getElementById: id => ids[id] || null,
    querySelector: selector => selector.endsWith('thead tr') ? thead : tbody,
    createElement: tag => new Element(tag),
    addEventListener() {},
  },
  window: { addEventListener() {} },
  requestAnimationFrame: callback => callback(),
  setTimeout: callback => timers.push(callback),
  getCurrentUserPrefScope: () => 'test_scope',
  getVisibleColumns: () => [{ key: 'SKU', label: 'SKU' }, { key: 'Margem', label: 'Margem' }],
  getTableCellValue: (row, key) => row[key],
  parsePercentValue: value => Number(value.replace('%', '')),
  isDecisaoParticipar: value => value === 'Participar',
  normalizeApiDatasetRules: rows => rows,
  savePagePrefs() {},
  mergeAndSaveColumnWidthPrefs() {},
  applyColumnWidthPrefsToDom() {},
  bindPromoMinimizeOnDblClick() {},
  saveColumnWidthPrefsFromDomRobust() {},
  alert: message => alerts.push(message),
  fetch: async () => { requests++; throw new Error('Unexpected request'); },
};
vm.createContext(context);
for (const file of ['arquivos-render.js', 'api-mercadolivre.js', 'exportacao.js']) {
  const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'frontend_promo', file), 'utf8');
  vm.runInContext(source, context, { filename: file });
}

const maliciousText = '<img src=x onerror="alert(1)">';
const data = [{ SKU: '00123', Margem: '20%', 'Ação': 'Participar' }];
context.apiAnalisesPorCampanha = [
  {
    data: [],
    motivo_sem_resultados: 'Nenhum anúncio passou pelo filtro de % Fixa da Promoção 1.',
    diagnostico: { candidatos: 394, linhas_montadas: 394, excluidos_status: 0, excluidos_pct_fixa: 394 },
  },
  { data },
  { data: [], motivo_sem_resultados: maliciousText, diagnostico: { candidatos: maliciousText, linhas_montadas: -1 } },
  { data: [] },
];
const original = JSON.stringify(context.apiAnalisesPorCampanha);
const notice = ids.analysisEmptyState;

context.ativarAnaliseApiPorCampanha(0);
assert.strictEqual(notice.style.display, 'block');
assert(notice.textContent.includes('Nenhum anúncio passou pelo filtro'));
assert(notice.textContent.includes('Anúncios encontrados: 394'));
assert(notice.textContent.includes('Excluídos pelo filtro de % Fixa da Promoção 1: 394'));
assert.strictEqual(ids.totalAnuncios.textContent, '0');
assert.strictEqual(tbody.children.length, 0, 'diagnostics must not become table data rows');
while (timers.length) timers.shift()();
assert.strictEqual(notice.style.display, 'block', 'empty result explanation must persist after timers');

context.ativarAnaliseApiPorCampanha(1);
assert.strictEqual(notice.style.display, 'none');
assert.strictEqual(notice.textContent, '', 'previous campaign diagnostics must be cleared');
assert.strictEqual(tbody.children.length, 1);
assert.strictEqual(tbody.children[0].children[0].textContent, '00123');
assert.strictEqual(ids.totalAnuncios.textContent, '1');
assert.strictEqual(ids.totalParticipar.textContent, '1');

context.ativarAnaliseApiPorCampanha(2);
assert.strictEqual(notice.style.display, 'block');
assert.strictEqual(notice.textContent, maliciousText, 'reason must be rendered literally; invalid counts ignored');
assert.strictEqual(notice.children.length, 1);
assert.strictEqual(notice.children[0].children.length, 0);

context.ativarAnaliseApiPorCampanha(3);
assert.strictEqual(notice.textContent, 'Análise concluída sem anúncios para exibir.', 'older API responses need a fallback');
context.apiAnalisesPorCampanha[3].error = 'Falha ao consultar os anúncios da campanha.';
context.ativarAnaliseApiPorCampanha(3);
assert.strictEqual(notice.textContent, 'Falha ao consultar os anúncios da campanha.', 'campaign errors must remain visible');
context.apiAnalisesPorCampanha = [];
context.renderTable([]);
assert.strictEqual(notice.textContent, 'Análise concluída sem anúncios para exibir.', 'file analysis cannot inherit campaign diagnostics');

context.apiAnalisesPorCampanha = JSON.parse(original);
context.ativarAnaliseApiPorCampanha(0);
assert.strictEqual(JSON.stringify(context.apiAnalisesPorCampanha), original, 'UI must preserve rows, decisions and diagnostics');

(async () => {
  await context.exportarAnaliseApi(0);
  await context.exportarAnaliseApi(1, []);
  assert.strictEqual(requests, 0, 'empty analysis and empty override must not request export');
  assert.strictEqual(alerts.length, 2);
  assert(alerts.every(message => message.includes('não tem anúncios para exportar')));
  console.log('promocoes empty results runtime checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
