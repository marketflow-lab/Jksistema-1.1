'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const skuRoot = path.join(root, 'static', 'favoritos', 'v2', 'sku');
const facadeSource = fs.readFileSync(path.join(root, 'static', 'favoritos', 'sku.js'), 'utf8');
const fixture = JSON.parse(fs.readFileSync(path.join(root, 'tests', 'fixtures', 'favoritos_sku_contract.json'), 'utf8'));

async function main() {
  const context = {
    console,
    Promise,
    Set,
    Map,
    AbortController,
    favoritosBotoesAtualizarSkuMl: () => [],
    skuFiltroEl: null,
    btnSkuToggleOcultos: null
  };
  context.window = context;
  context.document = {
    createElement: () => ({}),
    head: {
      appendChild(script) {
        const match = /\/favoritos\/v2\/sku\/([^?]+)/.exec(script.src || '');
        if (!match) throw new Error('Asset inesperado: ' + script.src);
        const component = fs.readFileSync(path.join(skuRoot, match[1]), 'utf8');
        vm.runInContext(component, context, { filename: match[1] });
        Promise.resolve().then(() => script.onload());
      }
    }
  };
  context.document.documentElement = context.document.head;
  vm.createContext(context);
  vm.runInContext(facadeSource, context, { filename: "sku.js" });
  const api = await context.__FAVORITOS_SKU_READY__;
  const publicNames = Object.values(fixture.groups).flat();
  assert.strictEqual(context.FavoritosV2.sku.publicApiNames.length, 92);
  assert.strictEqual(publicNames.length, 92);
  for (const name of publicNames) {
    assert.strictEqual(typeof api[name], "function", "API ausente: " + name);
    assert.strictEqual(context[name], api[name], "alias legado divergente: " + name);
  }
  assert.strictEqual(api.skuTexto({ sku: " ABC " }, ["sku"]), "ABC");
  assert.strictEqual(api.skuNumero("1.234,50"), 1234.5);
  assert.strictEqual(api.normalizationDescriptions.skuChaveSku(" AbC "), "abc");
  assert.strictEqual(api.search.buscar, api.buscar);
  assert.strictEqual(api.tabs.mudarAba, api.mudarAba);
  console.log('Favoritos SKU loader checks passed');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
