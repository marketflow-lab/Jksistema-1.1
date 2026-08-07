'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const fixture = JSON.parse(fs.readFileSync(
  path.join(root, 'tests', 'fixtures', 'favoritos_promotion_effectuation_contract.json'),
  'utf8'
));
const facadePath = path.join(root, 'static', 'favoritos', 'promocoes-efetivacao.js');
const componentDir = path.join(root, 'static', 'favoritos', 'v2', 'promotion-effectuation');
const loaded = [];
let context;

const document = {
  head: {
    appendChild(script) {
      const url = new URL(script.src, 'https://jk.local');
      const prefix = '/favoritos/v2/promotion-effectuation/';
      assert.ok(url.pathname.startsWith(prefix), 'componente fora da pasta allowlisted');
      const fileName = url.pathname.slice(prefix.length);
      const source = fs.readFileSync(path.join(componentDir, fileName), 'utf8');
      loaded.push(fileName);
      Promise.resolve().then(() => {
        try {
          vm.runInContext(source, context, { filename: fileName });
          script.onload();
        } catch (error) {
          script.onerror(error);
        }
      });
    }
  },
  createElement(tagName) {
    assert.strictEqual(tagName, 'script');
    return { src: '', onload: null, onerror: null };
  }
};

const sandbox = {
  console,
  document,
  Promise,
  Map,
  Set,
  Object,
  Array,
  Number,
  String,
  Boolean,
  Date,
  Math,
  Error,
  URL,
  setTimeout,
  clearTimeout
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
context = vm.createContext(sandbox);

const facade = fs.readFileSync(facadePath, 'utf8');
vm.runInContext(facade, context, { filename: 'promocoes-efetivacao.js' });

(async () => {
  await sandbox.__FAVORITOS_PROMOCOES_EFETIVACAO_READY__;
  const feature = sandbox.FavoritosV2.promotionEffectuation;
  assert.deepStrictEqual(loaded, fixture.component_order);
  assert.strictEqual(feature.schema, 'jk.favoritos.promotion-effectuation.v1');
  assert.strictEqual(feature.contracts.schema, 'jk.favoritos.promotion-effectuation.contracts.v1');
  assert.deepStrictEqual(Array.from(feature.contracts.outcomes), fixture.outcomes);
  assert.deepStrictEqual(
    Object.keys(feature.publicApi),
    [...Object.keys(fixture.api_groups), 'flat']
  );
  assert.strictEqual(Object.keys(feature.publicApi.flat).length, 53);
  assert.deepStrictEqual(Object.keys(feature.legacyGlobals), fixture.legacy_globals);

  for (const [groupName, names] of Object.entries(fixture.api_groups)) {
    assert.deepStrictEqual(Object.keys(feature.publicApi[groupName]), names);
    names.forEach(name => {
      assert.strictEqual(typeof feature.publicApi[groupName][name], 'function', 'API ausente: ' + name);
    });
  }
  fixture.legacy_globals.forEach(name => {
    assert.strictEqual(typeof sandbox[name], 'function', 'alias de compatibilidade ausente: ' + name);
  });
  fixture.internal_only_symbols.forEach(name => {
    assert.strictEqual(sandbox[name], undefined, 'helper interno foi reexportado: ' + name);
  });

  const parsePrice = value => Number(value);
  const runtime = feature.runtime;
  assert.strictEqual(runtime.configure({ adapters: { parsePrecoAnuncioFavoritos: parsePrice } }), runtime);
  assert.strictEqual(runtime.adapters.parsePrecoAnuncioFavoritos, parsePrice);
  assert.strictEqual(runtime.resolveAdapter('parsePrecoAnuncioFavoritos'), parsePrice);
  assert.throws(
    () => runtime.configure({ adapters: { adaptadorNaoPermitido: () => null } }),
    /nao permitido/
  );
  assert.throws(
    () => runtime.configure({ adapters: { parsePrecoAnuncioFavoritos: 'invalido' } }),
    /invalido/
  );

  const firstPromise = sandbox.__FAVORITOS_PROMOCOES_EFETIVACAO_READY__;
  const firstApi = feature.publicApi;
  vm.runInContext(facade, context, { filename: 'promocoes-efetivacao-reload.js' });
  assert.strictEqual(sandbox.__FAVORITOS_PROMOCOES_EFETIVACAO_READY__, firstPromise);
  assert.strictEqual(feature.publicApi, firstApi);
  assert.strictEqual(loaded.length, fixture.component_order.length);
  console.log('Favoritos promotion-effectuation runtime checks passed');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
