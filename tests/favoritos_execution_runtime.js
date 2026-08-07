'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const facadePath = path.join(
  root,
  'static',
  'favoritos',
  'tabelas-layout',
  '07-execucao-render-layout.js'
);
const loaded = [];
let context;

const document = {
  head: {
    appendChild(script) {
      const url = new URL(script.src, 'https://jk.local');
      const prefix = '/favoritos/v2/execution/';
      assert.ok(url.pathname.startsWith(prefix), 'componente fora da pasta allowlisted');
      const fileName = url.pathname.slice(prefix.length);
      const source = fs.readFileSync(
        path.join(root, 'static', 'favoritos', 'v2', 'execution', fileName),
        'utf8'
      );
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
  AbortController,
  URL,
  setTimeout,
  clearTimeout,
  addEventListener() {},
  location: { origin: 'https://jk.local' }
};
sandbox.window = sandbox;
sandbox.parent = sandbox;
sandbox.top = sandbox;
context = vm.createContext(sandbox);

const facade = fs.readFileSync(facadePath, 'utf8');
vm.runInContext(facade, context, { filename: '07-execucao-render-layout.js' });

(async () => {
  await sandbox.__FAVORITOS_EXECUCAO_RENDER_LAYOUT_READY__;
  const fixture = JSON.parse(fs.readFileSync(
    path.join(root, 'tests', 'fixtures', 'favoritos_execution_contract.json'),
    'utf8'
  ));
  assert.deepStrictEqual(loaded, fixture.component_order);
  assert.strictEqual(sandbox.FavoritosV2.execution.schema, 'jk.favoritos.execution.v1');
  assert.strictEqual(sandbox.FavoritosV2.execution.__componentsReady, true);
  assert.deepStrictEqual(
    Array.from(sandbox.FavoritosV2.execution.publicApiNames),
    Object.keys(fixture.public_api)
  );
  for (const name of Object.keys(fixture.public_api)) {
    assert.strictEqual(
      typeof sandbox.FavoritosV2.execution.publicApi[name],
      'function',
      'API publica indisponivel: ' + name
    );
    assert.strictEqual(typeof sandbox[name], 'function', 'wrapper global ausente: ' + name);
  }
  const firstPromise = sandbox.__FAVORITOS_EXECUCAO_RENDER_LAYOUT_READY__;
  vm.runInContext(facade, context, { filename: '07-execucao-render-layout.js' });
  assert.strictEqual(sandbox.__FAVORITOS_EXECUCAO_RENDER_LAYOUT_READY__, firstPromise);
  assert.strictEqual(loaded.length, fixture.component_order.length);
  console.log('Favoritos execution runtime checks passed');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
