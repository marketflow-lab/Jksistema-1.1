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
  '/api/lojas',
  '/api/cadastro/lojas/',
  'importar-colunas',
  '/api/cadastro/fornecedores',
  'NCM_SYNC.init',
  'cadastro_larguras_colunas',
  'carregarProdutos',
  'storeIdSelecionado',
]) {
  assert(main.includes(snippet), `cadastro principal perdeu o contrato: ${snippet}`);
}
assert.match(
  main,
  /\/api\/cadastro\/foto\/\$\{encodeURIComponent\(clientId\)\}\/lojas\/\$\{segmento\}\/\$\{encodeURIComponent\(nome\)\}\?store_id=\$\{encodeURIComponent\(storeExata\)\}/,
  'a miniatura deve usar segmento opaco e enviar a identidade exata da loja para validacao',
);

const selector = pages['static/cadastro_editar.html'].combined;
for (const snippet of [
  '/api/cadastro/lojas/',
  'storeIdSelecionado',
  'cadastro_editar_sku',
  'cadastro_editar_item',
  'cadastro_editar_lista',
  "urlPagina('/cadastro_editar_item.html'",
]) {
  assert(selector.includes(snippet), `seletor de edição perdeu o contrato: ${snippet}`);
}

const editor = pages['static/cadastro_editar_item.html'].combined;
for (const snippet of [
  '/api/cadastro/lojas/',
  "apiLoja(state.storeIdSelecionado, 'colunas')",
  'produtos/${encodeURIComponent(state.skuOriginal)}',
  "method: 'PUT'",
  'cadastro_editar_sku',
  'cadastro_editar_item',
  'cadastro_editar_lista',
]) {
  assert(editor.includes(snippet), `edição de item perdeu o contrato: ${snippet}`);
}
assert.match(
  editor,
  /storeTools\.urlFoto\(obterClientId\(\), state\.storeIdSelecionado, valor\)/,
  'a prévia da edição deve identificar cliente e loja atuais',
);

const inclusion = pages['static/cadastro_incluir.html'].combined;
for (const snippet of [
  '/api/cadastro/lojas/',
  "apiLoja(state.storeIdSelecionado, 'colunas')",
  "apiLoja(state.storeIdSelecionado, 'produtos')",
  "method: 'POST'",
]) {
  assert(inclusion.includes(snippet), `inclusão de item perdeu o contrato: ${snippet}`);
}

for (const [name, source] of [['principal', main], ['seletor', selector], ['editor', editor], ['inclusão', inclusion]]) {
  assert.doesNotMatch(source, /fetch\(['"]\/api\/cadastro\/(?:produtos|produto|colunas|importar-colunas)/, `${name}: chamada legada sem loja`);
}

const globalStatus = read('static/global-status.js');
assert.match(
  globalStatus,
  /typeof carregarProdutos === 'function'[\s\S]*carregarProdutos\(\)/,
  'global-status continua sendo um consumidor externo de carregarProdutos',
);

assert.strictEqual(path.posix.basename(fixture.schema), fixture.schema);
console.log('cadastro frontend contract: OK');
