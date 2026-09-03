'use strict';

const assert = require('assert');
const vm = require('vm');
const { read } = require('./helpers/cadastro_frontend_sources');

function genericElement() {
  const listeners = new Map();
  return {
    addEventListener(name, listener) { listeners.set(name, listener); },
    className: '',
    dataset: {},
    disabled: false,
    innerHTML: '',
    reset() {},
    scrollIntoView() {},
    setAttribute() {},
    style: {},
    textContent: '',
    value: '',
  };
}

const elements = new Map();
const calls = [];
const registro = {
  id: 'fornecedor-1',
  nome_empresa: 'Fornecedor Exemplo Ltd.',
  nome_contato: 'Contato Exemplo',
  email: 'contato@example.com',
  moeda_pagamento: 'USD',
  conta_beneficiario: '123456789',
  banco_beneficiario: 'Banco Exemplo',
};

const context = {
  confirm() { return true; },
  console,
  document: {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, genericElement());
      return elements.get(id);
    },
  },
  fetch: async (url, options = {}) => {
    calls.push({ url, options });
    if (!options.method || options.method === 'GET') {
      return { ok: true, status: 200, async json() { return [registro]; } };
    }
    return { ok: true, status: 200, async json() { return { success: true }; } };
  },
  localStorage: { getItem() { return null; } },
  location: { href: '', pathname: '/cadastro.html', search: '' },
  obterAuthHeaders(extra = {}) { return { Authorization: 'Bearer test', ...extra }; },
  URLSearchParams,
};
context.window = context;
vm.createContext(context);

for (const sourcePath of [
  'static/cadastro/form/00-core.js',
  'static/cadastro/00-runtime.js',
  'static/cadastro/01-core.js',
  'static/cadastro/main/05-fornecedores.js',
]) {
  vm.runInContext(read(sourcePath), context, { filename: sourcePath });
}

(async () => {
  const fornecedores = context.JKCadastro.fornecedores;
  context.JKCadastro.runtime.state.storeIdSelecionado = 'store-a';
  assert.strictEqual(await fornecedores.carregar(), true);
  assert.strictEqual(context.JKCadastro.runtime.state.fornecedores.length, 1);
  assert.match(elements.get('listaFornecedores').innerHTML, /Fornecedor Exemplo Ltd\./);
  assert.match(elements.get('listaFornecedores').innerHTML, /•••• 6789/);
  assert.doesNotMatch(elements.get('listaFornecedores').innerHTML, /123456789/);

  assert.strictEqual(fornecedores.editar('fornecedor-1'), true);
  assert.strictEqual(elements.get('fornecedorNomeEmpresa').value, registro.nome_empresa);
  assert.strictEqual(elements.get('fornecedorContaBeneficiario').value, registro.conta_beneficiario);

  assert.strictEqual(await fornecedores.salvar({ preventDefault() {} }), true);
  const put = calls.find(call => call.options.method === 'PUT');
  assert(put, 'edição deve usar PUT');
  assert.strictEqual(put.url, '/api/cadastro/fornecedores/fornecedor-1');
  assert.strictEqual(JSON.parse(put.options.body).nome_empresa, registro.nome_empresa);

  assert.strictEqual(await fornecedores.excluir('fornecedor-1'), true);
  const deletion = calls.find(call => call.options.method === 'DELETE');
  assert(deletion, 'exclusão deve usar DELETE');
  assert.strictEqual(deletion.url, '/api/cadastro/fornecedores/fornecedor-1');

  context.JKCadastro.runtime.state.storeIdSelecionado = '';
  fornecedores.atualizarEstadoMutacoes();
  assert.strictEqual(elements.get('btnSalvarFornecedor').disabled, true);
  assert.strictEqual(elements.get('fornecedorNomeEmpresa').disabled, true);
  assert.doesNotMatch(elements.get('listaFornecedores').innerHTML, /data-action="editar"/);
  assert.doesNotMatch(elements.get('listaFornecedores').innerHTML, /data-action="excluir"/);
  assert.match(elements.get('fornecedorStatus').textContent, /Visualização consolidada/);

  const chamadasAntesDoBloqueio = calls.length;
  assert.strictEqual(fornecedores.editar('fornecedor-1'), false);
  assert.strictEqual(await fornecedores.salvar({ preventDefault() {} }), false);
  assert.strictEqual(await fornecedores.excluir('fornecedor-1'), false);
  assert.strictEqual(calls.length, chamadasAntesDoBloqueio, 'modo consolidado não pode disparar mutações');

  console.log('cadastro fornecedores frontend: OK');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
