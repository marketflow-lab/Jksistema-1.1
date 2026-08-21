'use strict';

const assert = require('assert');
const path = require('path');
const {
  pageSources,
  read,
} = require('./helpers/cadastro_frontend_sources');

const fixture = JSON.parse(read('tests/fixtures/cadastro_frontend_contract.json'));
const pages = Object.fromEntries(
  Object.keys(fixture.dom_ids).map(relativePath => [relativePath, pageSources(relativePath)]),
);

assert.strictEqual(
  read('cadastro.html'),
  pages['static/cadastro.html'].html,
  'o espelho legado de cadastro deve permanecer idêntico à fonte oficial',
);

for (const [relativePath, ids] of Object.entries(fixture.dom_ids)) {
  const html = pages[relativePath].html;
  ids.forEach(id => assert(html.includes(`id="${id}"`), `${relativePath}: id ausente: ${id}`));
  pages[relativePath].scripts.forEach(({ name, source }) => {
    assert.doesNotThrow(() => new Function(source), `${name}: JavaScript inválido`);
  });
}

const main = pages['static/cadastro.html'].combined;
for (const snippet of [
  '/api/cadastro/produtos',
  '/api/cadastro/importar-colunas',
  'NCM_SYNC.init',
  'cadastro_larguras_colunas',
  'carregarProdutos',
]) {
  assert(main.includes(snippet), `cadastro principal perdeu o contrato: ${snippet}`);
}

const selector = pages['static/cadastro_editar.html'].combined;
for (const snippet of [
  '/api/cadastro/produtos',
  'cadastro_editar_sku',
  'cadastro_editar_item',
  'cadastro_editar_lista',
  '/cadastro_editar_item.html?sku=',
]) {
  assert(selector.includes(snippet), `seletor de edição perdeu o contrato: ${snippet}`);
}

const editor = pages['static/cadastro_editar_item.html'].combined;
for (const snippet of [
  '/api/cadastro/colunas',
  '/api/cadastro/produtos',
  '/api/cadastro/produto?sku=',
  '/api/cadastro/produto?sku_original=',
  "method: 'PUT'",
  'cadastro_editar_sku',
  'cadastro_editar_item',
  'cadastro_editar_lista',
]) {
  assert(editor.includes(snippet), `edição de item perdeu o contrato: ${snippet}`);
}

const inclusion = pages['static/cadastro_incluir.html'].combined;
for (const snippet of [
  '/api/cadastro/colunas',
  '/api/cadastro/produto',
  "method: 'POST'",
]) {
  assert(inclusion.includes(snippet), `inclusão de item perdeu o contrato: ${snippet}`);
}

const globalStatus = read('static/global-status.js');
assert.match(
  globalStatus,
  /typeof carregarProdutos === 'function'[\s\S]*carregarProdutos\(\)/,
  'global-status continua sendo um consumidor externo de carregarProdutos',
);

assert.strictEqual(path.posix.basename(fixture.schema), fixture.schema);
console.log('cadastro frontend contract: OK');
