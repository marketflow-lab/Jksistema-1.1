'use strict';

const assert = require('assert');
const vm = require('vm');
const { read } = require('./helpers/cadastro_frontend_sources');

const editorSource = read('static/cadastro/editar-item.js');
assert.match(
  editorSource,
  /params\.get\('sku'\)\s*\|\|\s*storeTools\.obterSkuCache\(state\.clientId, state\.storeIdSelecionado\)/,
  'o editor deve priorizar o SKU informado na URL e aceitar apenas fallback envelopado no mesmo escopo',
);

const stored = new Map();
const genericElement = () => ({
  addEventListener() {},
  appendChild() {},
  children: [],
  classList: { add() {}, toggle() {} },
  dataset: {},
  querySelector() { return null; },
  querySelectorAll() { return []; },
  setAttribute() {},
  style: {},
});
const elements = new Map();
const tBody = genericElement();
tBody.contains = candidate => Boolean(candidate && candidate.__row);
elements.set('tBody', tBody);

const context = {
  console,
  document: { getElementById(id) { if (!elements.has(id)) elements.set(id, genericElement()); return elements.get(id); } },
  location: { href: '', pathname: '/cadastro.html', search: '' },
  localStorage: {
    getItem(key) { return stored.has(key) ? stored.get(key) : null; },
    removeItem(key) { stored.delete(key); },
    setItem(key, value) { stored.set(key, String(value)); },
  },
  scrollTo() {},
  URLSearchParams,
};
context.window = context;
vm.createContext(context);
for (const sourcePath of [
  'static/cadastro/form/00-core.js',
  'static/cadastro/00-runtime.js',
  'static/cadastro/01-core.js',
  'static/cadastro/main/01-produtos.js',
]) {
  vm.runInContext(read(sourcePath), context, { filename: sourcePath });
}

const item = { sku: '632 2/K5', nome: 'Produto de teste' };
context.JKCadastro.runtime.state.produtos = [item];
context.JKCadastro.runtime.state.clientId = 'client-1';
context.JKCadastro.runtime.state.storeIdSelecionado = 'store-1';
item.store_id = 'store-1';
const core = context.JKCadastro.core;
const table = context.JKCadastro.produtosTabela;

assert.strictEqual(core.urlEdicaoSku(item.sku), '/cadastro_editar_item.html?store_id=store-1&sku=632+2%2FK5');
assert.match(core.renderConteudoCelula('sku', item), /class="sku-edit-link"/);
assert.match(core.renderConteudoCelula('sku', item), /href="\/cadastro_editar_item\.html\?store_id=store-1&amp;sku=632\+2%2FK5"/);

const row = { __row: true, dataset: { sku: item.sku, storeId: 'store-1' } };
const rowTarget = {
  closest(selector) {
    if (selector === 'tr[data-sku]') return row;
    return null;
  },
};
table.tratarCliqueTabela({ target: rowTarget });
assert.strictEqual(context.location.href, '/cadastro_editar_item.html?store_id=store-1&sku=632+2%2FK5');
const cached = JSON.parse(stored.get('cadastro_editar_item'));
assert.strictEqual(cached.schema, 'jk.cadastro.edit-cache.v2');
assert.strictEqual(cached.client_id, 'client-1');
assert.strictEqual(cached.store_id, 'store-1');
assert.strictEqual(cached.sku, item.sku);
assert.deepStrictEqual(cached.produto, item);
assert.strictEqual(JSON.parse(stored.get('cadastro_editar_sku')).sku, item.sku);
assert.strictEqual(stored.has('cadastro_editar_lista'), false, 'clique direto não deve gravar a lista inteira');

context.location.href = '';
const photoTarget = { closest(selector) { return selector.startsWith('a, button') ? {} : null; } };
table.tratarCliqueTabela({ target: photoTarget });
assert.strictEqual(context.location.href, '', 'controle interativo dentro da linha não pode disparar navegação da linha');

context.localStorage.setItem = () => { throw new Error('quota'); };
assert.strictEqual(core.abrirProdutoParaEdicao(item), true);
assert.strictEqual(context.location.href, '/cadastro_editar_item.html?store_id=store-1&sku=632+2%2FK5', 'URL deve funcionar mesmo sem localStorage');

console.log('cadastro row edit navigation: OK');
