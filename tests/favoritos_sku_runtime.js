'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const runtimeSource = fs.readFileSync(path.join(root, 'static', 'favoritos', 'v2', 'sku', '00-runtime.js'), 'utf8');
const context = { console, Set, Map };
context.window = context;
context.headersJsonAutenticado = () => ({ Authorization: "Bearer test" });
vm.createContext(context);
vm.runInContext([
  'let skuDados = [];',
  'let skuLojasDisponiveis = [];',
  "let skuLojaSelecionada = '';",
  'let skuPaginaAtual = 1;',
  'let skuMostrarOcultos = false;',
  'let skuSkusOcultos = new Set();',
  'let skuDescricaoMemCache = new Map();',
  'let mlSkusAnunciosLojaAtual = [];',
  "let mlSkuLojaSelecionada = '';",
  'let mlSkuEstadoPorLojaCache = new Map();',
  'let mlSkuCarregamentoPromises = new Map();'
].join('\n'), context);

vm.runInContext(runtimeSource, context);
const firstRuntime = context.FavoritosV2.sku.runtime;
vm.runInContext(runtimeSource, context);
assert.strictEqual(context.FavoritosV2.sku.runtime, firstRuntime);
assert.strictEqual(firstRuntime.resolveAdapter("headersJsonAutenticado")().Authorization, "Bearer test");
assert.throws(() => firstRuntime.resolveAdapter("naoPermitido"), /nao permitido/);
firstRuntime.state.data = [{ sku: "1" }];
assert.strictEqual(firstRuntime.state.data.length, 1);
firstRuntime.state.selectedStore = "Loja A";
assert.strictEqual(firstRuntime.state.selectedStore, "Loja A");

console.log('Favoritos SKU runtime checks passed');
