'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { SKU_FACADE_PATH, skuComponentFiles } = require('./helpers/favoritos_sku_sources');

const root = path.resolve(__dirname, '..');
const skuDir = path.join(root, 'static', 'favoritos', 'v2', 'sku');
const read = relativePath => fs.readFileSync(path.join(root, relativePath), 'utf8');
const lineCount = source => {
  const normalized = source.replace(/\r\n/g, '\n');
  return normalized.endsWith('\n') ? normalized.split('\n').length - 1 : normalized.split('\n').length;
};

const facade = read(SKU_FACADE_PATH);
assert.ok(lineCount(facade) <= 300, 'fachada SKU excedeu 300 linhas');
assert.match(facade, /__FAVORITOS_SKU_READY__/);
assert.doesNotMatch(facade, /document\.write|globals\(\)\.update|bind_runtime_globals|PEER_EXPORTS/);

for (const fileName of skuComponentFiles) {
  const source = fs.readFileSync(path.join(skuDir, fileName), 'utf8');
  assert.ok(lineCount(source) <= 800, fileName + ' excedeu 800 linhas');
  assert.doesNotMatch(source, /document\.write|globals\(\)\.update|bind_runtime_globals|PEER_EXPORTS/);
  assert.doesNotMatch(source, /(?:require|import|script\.src)[^\n]*favoritos\/sku\.js/);
  const boundaryIndex = source.indexOf('\n        Object.assign(feature.internal');
  if (boundaryIndex < 0) continue;
  const measured = source.slice(0, boundaryIndex);
  const declarations = [];
  measured.split(/\r?\n/).forEach((line, index) => {
    const match = /^\s*(?:async\s+)?function\s+([A-Za-z0-9_$]+)/.exec(line);
    if (match) declarations.push({ name: match[1], line: index + 1 });
  });
  const boundaryLine = lineCount(measured) + 1;
  declarations.forEach((declaration, index) => {
    const nextLine = declarations[index + 1] ? declarations[index + 1].line : boundaryLine;
    const span = nextLine - declaration.line;
    assert.ok(span <= 120, fileName + ':' + declaration.name + ' ocupa ' + span + ' linhas');
  });
}

const runtime = read('static/favoritos/v2/sku/00-runtime.js');
assert.match(runtime, /adapterNames = new Set/);
assert.match(runtime, /Object\.defineProperties\(state/);
assert.match(runtime, /jk\.favoritos\.sku\.v1/);

const init = read('static/favoritos/init.js');
assert.match(init, /const skuReady = window\.__FAVORITOS_SKU_READY__/);
assert.match(init, /\[skuReady, browserReady, layoutReady, promotionEffectuationReady\]/);

const manifest = JSON.parse(read('static/favoritos/asset-manifest.json'));
for (const fileName of skuComponentFiles) {
  assert.ok(manifest.assets.includes('favoritos/v2/sku/' + fileName), 'manifesto omitiu ' + fileName);
}

console.log('Favoritos SKU architecture checks passed');
