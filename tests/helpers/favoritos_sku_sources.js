'use strict';

const fs = require('fs');
const path = require('path');

const SKU_FACADE_PATH = 'static/favoritos/sku.js';
const skuComponentFiles = [
  '00-runtime.js',
  '01-normalization-descriptions.js',
  '02-store-cache.js',
  '03-catalog-table.js',
  '04-search-tabs.js',
  '05-public-api.js'
];
const skuBehaviorFiles = skuComponentFiles.filter(fileName => /^0[1-4]-/.test(fileName));

function readFile(root, relativePath) {
  return fs.readFileSync(path.join(root, relativePath), 'utf8');
}

function readSkuComponents(root, files = skuBehaviorFiles) {
  return files.map(fileName => readFile(root, path.join('static', 'favoritos', 'v2', 'sku', fileName))).join('\n');
}

function extractLegacyBody(source) {
  source = source.replace(/\r\n/g, "\n");
  const prefix = /if \(feature\.components\.has\('[^']+'\)\) return;\r?\n\r?\n/;
  const prefixMatch = prefix.exec(source);
  if (!prefixMatch) throw new Error('Prefixo do componente SKU nao encontrado.');
  const marker = source.indexOf('\n        Object.assign(feature.internal', prefixMatch.index + prefixMatch[0].length);
  if (marker < 0) throw new Error('Rodape do componente SKU nao encontrado.');
  return source.slice(prefixMatch.index + prefixMatch[0].length, marker);
}

function reconstructLegacySource(root) {
  return skuBehaviorFiles.map(fileName => {
    const source = readFile(root, path.join('static', 'favoritos', 'v2', 'sku', fileName));
    return extractLegacyBody(source).replace(/\r\n/g, "\n");
  }).join('');
}

module.exports = {
  SKU_FACADE_PATH,
  skuComponentFiles,
  skuBehaviorFiles,
  readSkuComponents,
  reconstructLegacySource
};
