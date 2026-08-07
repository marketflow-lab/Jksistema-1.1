'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { generatedBrowserSources } = require('./helpers/favoritos_browser_sources');

const root = path.resolve(__dirname, '..');
const snapshot = JSON.parse(fs.readFileSync(
  path.join(root, 'tests/fixtures/favoritos_ml_browser_contract.json'), 'utf8'
));
const context = {
  console,
  setTimeout,
  clearTimeout,
  addEventListener() {},
  removeEventListener() {},
  document: {
    addEventListener() {},
    removeEventListener() {},
    getElementById() { return null; },
    querySelector() { return null; },
    querySelectorAll() { return []; }
  }
};
context.window = context;
context.globalThis = context;

for (const asset of generatedBrowserSources(root)) {
  vm.runInNewContext(asset.content, context, { filename: asset.relative });
}

const browser = context.FavoritosV2 && context.FavoritosV2.browser;
assert.ok(browser && browser.publicApi, 'API modular do ML Browser deve ser publicada');
assert.equal(snapshot.schema, 'jk.favoritos.ml-browser-contract.v1');
assert.equal(snapshot.source_sha256, 'beb06127662af245bd5ae53b2ca6c6b80e13251e90ac2cbe979ed4476784f678');

const atual = browser.publicNames.map(name => ({ name, arity: browser.publicApi[name].length }));
assert.deepStrictEqual(
  JSON.parse(JSON.stringify(atual)),
  snapshot.globals,
  'nomes, ordem e aridade da API global devem permanecer iguais ao snapshot'
);
assert.equal(new Set(browser.publicNames).size, 179, 'API publica nao pode conter nomes duplicados');
assert.ok(Object.isFrozen(browser.publicApi), 'projecao publica deve ser imutavel');
assert.ok(Object.isFrozen(browser.publicNames), 'catalogo publico deve ser imutavel');

console.log(`OK: snapshot preserva ${atual.length} funcoes publicas do ML Browser.`);
