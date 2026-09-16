'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

class Element {
  constructor() {
    this.children = [];
    this.style = {};
    this.dataset = {};
    this.listeners = {};
    this.classList = { add() {}, remove() {} };
    this.value = '';
    this._text = '';
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set innerHTML(value) { this._text = String(value); this.children = []; }
  appendChild(child) { child.parentElement = this; this.children.push(child); return child; }
  setAttribute() {}
  querySelector() { return null; }
  querySelectorAll() { return []; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  change(value) { this.value = value; this.listeners.change?.call(this); }
}

const ids = Object.fromEntries([
  'tabelaAnalise', 'totalAnuncios', 'totalParticipar', 'totalNaoParticipar',
  'apiMargemMinima', 'apiMargemTolerancia', 'apiAutoWorkEnabled', 'apiAutoApprovalRequired',
  'apiAutoIntervalMinutes', 'apiAutoIntervalUnit', 'paginationControls', 'pageSizeSelect',
  'prevPageBtn', 'nextPageBtn', 'pageInfo',
].map(id => [id, new Element()]));
const thead = new Element();
const tbody = new Element();
const storage = new Map();
ids.apiMargemMinima.value = '15';
ids.apiMargemTolerancia.value = '0';
const context = {
  console,
  document: {
    body: new Element(),
    getElementById: id => ids[id] || null,
    querySelector: selector => selector.endsWith('thead tr') ? thead : tbody,
    createElement: () => new Element(),
    addEventListener() {},
  },
  window: { addEventListener() {} },
  localStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) },
  requestAnimationFrame() {},
  prompt: () => '90',
  alert: message => { throw new Error(message); },
  parsePercentValue: value => value ? Number(String(value).replace('%', '').replace(',', '.')) : null,
};
vm.createContext(context);
for (const file of ['runtime.js', 'tabela-preferencias.js', 'arquivos-render.js', 'api-mercadolivre.js', 'automacao-api.js']) {
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'frontend_promo', file), 'utf8'), context, { filename: file });
}
for (const name of ['applyColumnWidthPrefsToDom', 'bindPromoMinimizeOnDblClick', 'renderApiAnalysisTabs', 'agendarSalvarColumnWidthPrefsServidor',
  'atualizarStatusAutomacaoPromo', 'aplicarApiAutoPrefsTela', 'atualizarStatusAutomacaoPromoPorPrefs', 'iniciarPollingAutomacaoPromoServidor']) {
  context[name] = () => {};
}
context.carregarApiAutoPrefsServidor = async () => ({ config: {} });
context.getVisibleColumns = () => [{ key: 'Ação', label: 'Ação', editable: true }];

// Incomplete campaign finances carry their specific reason; technical warnings are independent.
const rows = [
  { Status: 'Ativo', Margem: '40%', 'Margem ML': '', Acao: 'Não participar', action_financeiro_exato: false,
    action_financeiro_motivo: 'Frete da campanha selecionada não confirmado.' },
  { Status: 'Programada', Margem: '31,56%', 'Margem ML': '11,79%', 'Participar ou nao': 'Participar' },
];
context.rows = rows;
context.normalizeApiDatasetRules(rows);
assert.strictEqual(rows[0]['Ação'], 'Não participar', 'displayed margins must not override the backend recommendation');
assert.strictEqual(rows[1]['Ação'], 'Participar', 'the backend decision must survive margin formatting');
vm.runInContext('apiAnalisesPorCampanha = [{ data: rows, participacao_confirmada: true }, { data: [], participacao_confirmada: true }]', context);
context.ativarAnaliseApiPorCampanha(0);
assert.strictEqual(ids.totalParticipar.textContent, '1');
let select = tbody.children[0].children[0].children[0];
let reason = tbody.children[0].children[0].children[1];
assert.strictEqual(reason.textContent, 'Frete da campanha selecionada não confirmado.');
assert(!select.disabled, 'financial uncertainty must not disable the manual decision');
select.change('Participar');
assert.strictEqual(ids.totalParticipar.textContent, '2');
assert.strictEqual(reason.style.display, 'none');
assert.strictEqual(vm.runInContext('apiAnalisesPorCampanha[0].participacao_confirmada', context), false);
assert.strictEqual(vm.runInContext('apiAnalisesPorCampanha[0].participacao_selecao_revisao', context), 1);
assert.strictEqual(vm.runInContext('apiAnalisesPorCampanha[1].participacao_confirmada', context), true, 'changing a decision only invalidates its own campaign confirmation');
tbody.children[1].children[0].children[0].change('Não participar');
context.ativarAnaliseApiPorCampanha(1);
context.ativarAnaliseApiPorCampanha(0);
assert.strictEqual(rows[0]['Ação'], 'Participar');
assert.strictEqual(rows[1]['Ação'], 'Não participar');
assert.strictEqual(ids.totalParticipar.textContent, '1');
ids.pageSizeSelect.change('25');
assert.strictEqual(rows[0]['Ação'], 'Participar', 'pagination rerender must preserve manual decisions');
context.setColumnVisible('Margem', false);
assert.strictEqual(rows[0]['Ação'], 'Participar', 'column preferences must preserve manual decisions');
assert.strictEqual(rows[1]['Ação'], 'Não participar');
context.normalizeApiDatasetRules(rows);
assert.strictEqual(rows[0]['Ação'], 'Participar', 'canonical manual action must take precedence over stale Acao alias');

const before = JSON.stringify(rows);
ids.apiMargemMinima.value = '99';
context.promptToleranciaAcao();
assert.strictEqual(ids.apiMargemTolerancia.value, '90');
assert.strictEqual(JSON.stringify(rows), before, 'tolerance prompt must only affect the next analysis');
context.inicializarAutomacaoPromoApi();
ids.apiMargemTolerancia.change('100');
assert.strictEqual(JSON.stringify(rows), before, 'the tolerance input must not recalculate existing choices');

const selected = Array.from({ length: 236 }, (_, index) => ({
  MLB: index ? `MLB${index}` : '', 'Ação': 'Participar', action_financeiro_exato: index < 11,
}));
context.selected = selected;
vm.runInContext('currentData = selected', context);
context.updateMetrics();
assert.strictEqual(ids.totalParticipar.textContent, '236');
assert.strictEqual(context.obterLinhasSelecionadasPromocoes(selected).length, 236, 'selection must include missing MLB and financial data for explicit per-row processing');

// A newly received analysis replaces manual choices with its own initial recommendations.
const newRows = [{ Status: 'Ativo', Margem: '40%', 'Margem ML': '50%', Acao: 'Não participar' }];
context.normalizeApiDatasetRules(newRows);
context.newRows = newRows;
vm.runInContext('apiAnalisesPorCampanha = [{ data: newRows }]', context);
context.ativarAnaliseApiPorCampanha(0);
assert.strictEqual(ids.totalParticipar.textContent, '0');
assert.strictEqual(newRows[0]['Ação'], 'Não participar');
assert.strictEqual(context.obterLinhasSelecionadasPromocoes([{ 'Participar ou nao': 'Participar' }]).length, 1);

// A calculated profitable margin may recommend participation despite an unavailable offer ID.
const technicalRows = [{ MLB: 'MLB123', 'Ação': 'Participar', 'Margem ML': '28,01%',
  action_financeiro_exato: true, action_tecnico_apto: false,
  action_impedimento_tecnico: 'Identificador da oferta não informado.' }];
context.technicalRows = technicalRows;
vm.runInContext('currentData = technicalRows', context);
context.renderTable(technicalRows);
const technicalCell = tbody.children[0].children[0];
assert.strictEqual(technicalCell.children[1].textContent, '');
assert.strictEqual(technicalCell.children[2].textContent, '');
technicalCell.children[0].change('Não participar');
assert.strictEqual(technicalCell.children[2].style.display, 'none');
technicalCell.children[0].change('Participar');
assert.strictEqual(ids.totalParticipar.textContent, '1');
assert.strictEqual(technicalCell.children[2].style.display, 'none', 'generic technical warnings are not displayed');
technicalRows[0].action_ja_participa = true;
context.renderTable(technicalRows);
assert.strictEqual(tbody.children[0].children[0].children[2].textContent, 'Já participa');
assert.strictEqual(ids.totalParticipar.textContent, '1', 'participation notice does not change the selection');
assert.strictEqual(context.obterMotivoSugestaoPromocoes({ 'Ação': 'Não participar', action_financeiro_exato: true }), '');
assert(!context.obterMotivoSugestaoPromocoes({ 'Ação': 'Não participar', action_financeiro_exato: false }).includes('Dados financeiros insuficientes'), 'legacy uncertainty must not invent a financial cause');
vm.runInContext('apiAnalisesPorCampanha = undefined', context);
tbody.children[0].children[0].children[0].change('Participar');
assert.strictEqual(ids.totalParticipar.textContent, '1', 'legacy rows remain editable without campaign state');
console.log('promocoes participation selection runtime checks passed');
