'use strict';

const assert = require('assert');
const vm = require('vm');
const { read } = require('./helpers/cadastro_frontend_sources');

function genericElement() {
  const classes = new Set();
  const listeners = new Map();
  return {
    addEventListener(name, listener) { listeners.set(name, listener); },
    appendChild() {},
    attributes: {},
    children: [],
    classList: {
      add(name) { classes.add(name); },
      contains(name) { return classes.has(name); },
      toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); },
    },
    dataset: {},
    hidden: false,
    innerHTML: '',
    click() { const listener = listeners.get('click'); if (listener) listener(); },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    tabIndex: 0,
    value: '',
  };
}

const elements = new Map();
const context = {
  console,
  document: {
    addEventListener() {},
    createDocumentFragment() { return genericElement(); },
    createElement() { return genericElement(); },
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, genericElement());
      return elements.get(id);
    },
  },
  localStorage: { getItem() { return null; } },
  location: { pathname: '/cadastro.html', search: '' },
  URLSearchParams,
};
context.window = context;
vm.createContext(context);

for (const sourcePath of [
  'static/cadastro/form/00-core.js',
  'static/cadastro/00-runtime.js',
  'static/cadastro/01-core.js',
  'static/cadastro/main/01-produtos.js',
  'static/cadastro/main/02-custos-mlb.js',
]) {
  vm.runInContext(read(sourcePath), context, { filename: sourcePath });
}

const colunasComJsonManual = context.JKCadastro.core.construirColunas([{
  sku: 'SKU-JSON',
  dados_tecnicos_json: '{"manual":true}',
  observacao_bling: 'manual',
  codigo_ml: 'manual',
  taxa_mlb: 'manual',
  atributos_ml_json: '[]',
  imagens_ml_json: '[]',
  imagens_user_product_ml_json: '[]',
  catalog_product_id_user_product_ml: 'MLB123',
  estoques_bling_json: '{}',
}]);
assert(colunasComJsonManual.includes('dados_tecnicos_json'), 'JSON manual deve continuar visivel na tabela principal');
assert(colunasComJsonManual.includes('observacao_bling'), 'sufixo Bling manual deve continuar visivel');
assert(colunasComJsonManual.includes('codigo_ml'), 'sufixo ML manual deve continuar visivel');
assert(colunasComJsonManual.includes('taxa_mlb'), 'sufixo MLB manual deve continuar visivel');
assert(!colunasComJsonManual.includes('atributos_ml_json'), 'JSON do Mercado Livre deve ficar nos detalhes');
assert(!colunasComJsonManual.includes('imagens_ml_json'), 'imagens do Mercado Livre devem ficar nos detalhes');
assert(!colunasComJsonManual.includes('imagens_user_product_ml_json'), 'imagens do User Product devem ficar nos detalhes');
assert(!colunasComJsonManual.includes('catalog_product_id_user_product_ml'), 'identidade do User Product deve ficar nos detalhes');
assert(!colunasComJsonManual.includes('estoques_bling_json'), 'JSON da Bling deve ficar nos detalhes');

const tabs = {
  produtos: ['tabProdutos', 'painelProdutos'],
  custos: ['tabCustos', 'painelCustos'],
  fornecedores: ['tabFornecedores', 'painelFornecedores'],
  importadorLoja: ['tabImportadorLoja', 'painelImportadorLoja'],
};

function assertAbaAtiva(nomeAtivo) {
  Object.entries(tabs).forEach(([nome, [tabId, painelId]]) => {
    const ativa = nome === nomeAtivo;
    const tab = elements.get(tabId);
    assert.strictEqual(tab.classList.contains('active'), ativa, `${tabId} deve refletir a aba ativa`);
    assert.strictEqual(tab.attributes['aria-selected'], String(ativa), `${tabId} deve expor aria-selected`);
    assert.strictEqual(tab.tabIndex, ativa ? 0 : -1, `${tabId} deve controlar a ordem de foco`);
    assert.strictEqual(elements.get(painelId).hidden, !ativa, `${painelId} deve refletir a aba ativa`);
  });
}

const secondary = context.JKCadastro.custosMlb;
const selecaoComposta = secondary.resolverSelecaoSku('store:store-a:SKU%201%2F2');
assert.strictEqual(selecaoComposta.storeId, 'store-a');
assert.strictEqual(selecaoComposta.sku, 'SKU 1/2');
secondary.trocarAba('fornecedores');
assertAbaAtiva('fornecedores');

secondary.trocarAba('importadorLoja');
assertAbaAtiva('importadorLoja');

secondary.trocarAba('aba-inexistente');
assertAbaAtiva('produtos');

context.JKCadastro.actions = {
  atualizarCustosImpostosPorSku() {},
  carregarProdutos() {},
  importarColunasPorSku() {},
  async inicializarLojas() { return false; },
  inicializarNcm() {},
  selecionarLoja() {},
  sincronizarNcmBackground() {},
};
context.JKCadastro.components.add('actions');
context.JKCadastro.fornecedores = { init() {} };
context.JKCadastro.components.add('fornecedores');
context.JKCadastro.importacoesCatalogos = { abrir() {}, init() {} };
context.JKCadastro.components.add('importacoes-catalogos');
vm.runInContext(read('static/cadastro/main/04-init.js'), context, { filename: 'static/cadastro/main/04-init.js' });

elements.get('tabFornecedores').click();
assertAbaAtiva('fornecedores');
elements.get('tabImportadorLoja').click();
assertAbaAtiva('importadorLoja');

const html = read('static/cadastro.html');
assert.match(html, /id="tabFornecedores"[^>]*>Fornecedores<\/button>/);
assert.match(html, /id="tabImportadorLoja"[^>]*>Importador\/loja<\/button>/);
assert.match(html, /id="painelFornecedores"[^>]*aria-labelledby="tabFornecedores"[^>]*hidden/);
assert.match(html, /id="painelImportadorLoja"[^>]*aria-labelledby="tabImportadorLoja"[^>]*hidden/);

console.log('cadastro secondary tabs: OK');
