'use strict';

const assert = require('node:assert/strict');
const vm = require('node:vm');
const { read } = require('./helpers/cadastro_frontend_sources');

const fields = [
  'importadorNomeEmpresa', 'importadorTaxId', 'importadorTelefone', 'importadorEmail',
  'importadorContato', 'importadorLogradouro', 'importadorBairro', 'importadorCidade',
  'importadorCep', 'importadorEstado', 'importadorPais',
];
const elements = Object.fromEntries(fields.map(id => [id, { value: '', disabled: false }]));
elements.importadorLojaStatus = { textContent: '', className: '' };
elements.btnSalvarImportadorLoja = { disabled: false };
const listeners = {};
elements.importadorLojaForm = { addEventListener(name, callback) { listeners[name] = callback; } };
const calls = [];
const state = { storeIdSelecionado: 'store-a' };
const context = {
  JKCadastro: { components: new Set(['runtime']), runtime: { elements, state } },
  obterAuthHeaders(extra) { return extra || {}; },
  async fetch(url, options = {}) {
    calls.push({ url, options });
    return { ok: true, async json() { return options.method === 'PUT' ? JSON.parse(options.body) : { nome_empresa: 'Empresa A' }; } };
  },
};
context.window = context;
vm.createContext(context);
vm.runInContext(read('static/cadastro/main/05-importador-loja.js'), context);

(async () => {
  const module = context.JKCadastro.importadorLoja;
  module.init();
  await module.carregar();
  assert.equal(elements.importadorNomeEmpresa.value, 'Empresa A');
  assert.equal(calls[0].url, '/api/cadastro/lojas/store-a/importador-loja');
  elements.importadorTaxId.value = 'ID-123';
  await listeners.submit({ preventDefault() {} });
  assert.equal(calls[1].options.method, 'PUT');
  assert.equal(JSON.parse(calls[1].options.body).tax_id, 'ID-123');
  state.storeIdSelecionado = '';
  await module.carregar();
  assert.equal(elements.importadorNomeEmpresa.value, '');
  assert.equal(elements.btnSalvarImportadorLoja.disabled, true);
  assert.equal(calls.length, 2);
  console.log('cadastro importador/loja frontend: OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
