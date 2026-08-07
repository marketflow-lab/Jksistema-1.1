'use strict';

const assert = require('assert');
const path = require('path');
const vm = require('vm');
const helpers = require('./helpers/favoritos_search_ranking_sources');

const root = path.resolve(__dirname, '..');
const context = vm.createContext({ console });
context.window = context;
context.globalThis = context;

const source = helpers.searchRankingSource(root);
vm.runInContext(source, context, { filename: 'favoritos-search-ranking-components.js' });

const namespace = context.FavoritosV2.searchRanking;
assert.strictEqual(namespace.schema, 'jk.favoritos.search-ranking.v1');
assert.deepStrictEqual(
  Array.from(Object.keys(namespace.publicApi)),
  ['status', 'auth', 'promotions', 'control', 'search', 'listings', 'enrichment', 'ranking', 'ai', 'flat']
);
assert.strictEqual(Object.keys(namespace.publicApi.flat).length, 85);
assert.strictEqual(Object.keys(namespace.legacyGlobals).length, 41);

const firstApi = namespace.publicApi;
vm.runInContext(source, context, { filename: 'favoritos-search-ranking-components-reload.js' });
assert.strictEqual(context.FavoritosV2.searchRanking.publicApi, firstApi, 'runtime deve ser idempotente');

console.log('Favoritos search-ranking runtime checks passed');
