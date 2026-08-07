'use strict';

const assert = require('assert');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const {
  FACADE_PATH,
  importedStylePaths,
  normalizeNewlines,
  readFavoritosStyles,
  styleStats,
} = require('./helpers/favoritos_styles_sources');

const root = path.resolve(__dirname, '..');
const read = relativePath => fs.readFileSync(path.join(root, relativePath), 'utf8');
const physicalLines = source => {
  const normalized = normalizeNewlines(source);
  return normalized.endsWith('\n') ? normalized.split('\n').length - 1 : normalized.split('\n').length;
};
const manifest = JSON.parse(read('static/favoritos/asset-manifest.json'));
const snapshot = JSON.parse(read('tests/fixtures/favoritos_styles_contract.json'));
const facade = read(FACADE_PATH);
const componentPaths = importedStylePaths(root);

assert.ok(physicalLines(facade) <= 100, `fachada CSS excedeu 100 linhas: ${physicalLines(facade)}`);
assert.doesNotMatch(facade, /[{}]/, 'fachada CSS deve conter apenas imports ordenados');
assert.strictEqual(new Set(componentPaths).size, componentPaths.length, 'fachada CSS possui import repetido');
assert.deepStrictEqual(componentPaths, snapshot.components.map(component => component.path), 'ordem dos componentes CSS mudou');

const importVersions = Array.from(facade.matchAll(/\?v=([A-Za-z0-9._-]+)/g), match => match[1]);
assert.strictEqual(importVersions.length, componentPaths.length, 'todo componente CSS deve possuir cache-buster');
assert.ok(importVersions.every(version => version === manifest.version), 'cache-buster CSS diverge da versao do manifesto');

const contractedAssets = new Set(manifest.assets || []);
for (const component of snapshot.components) {
  const source = read(component.path);
  const actualLines = physicalLines(source);
  assert.strictEqual(actualLines, component.lines, `linhas divergentes em ${component.path}`);
  assert.ok(actualLines <= 800, `${component.path} excedeu 800 linhas: ${actualLines}`);
  assert.doesNotMatch(source, /@import\b/, `${component.path} nao pode importar outro componente`);
  assert.ok(contractedAssets.has(component.path.replace(/^static\//, '')), `manifesto nao inclui ${component.path}`);
}

const fullSource = readFavoritosStyles(root);
const actualStats = styleStats(fullSource);
assert.deepStrictEqual(actualStats, {
  normalized_sha256: snapshot.source_sha256_normalized,
  physical_lines: snapshot.physical_lines,
  rules: snapshot.rules,
  unique_selectors: snapshot.unique_selectors,
  class_tokens: snapshot.class_tokens,
  id_tokens: snapshot.id_tokens,
  media_queries: snapshot.media_queries,
  keyframes: snapshot.keyframes,
  important_count: snapshot.important_count,
  custom_property_declarations: snapshot.custom_property_declarations,
}, 'contrato de seletores, cascata ou declaracoes CSS mudou');

for (const asset of [FACADE_PATH, ...componentPaths]) {
  const publicPath = asset.replace(/^static\//, '');
  const actualHash = crypto.createHash('sha256').update(fs.readFileSync(path.join(root, asset))).digest('hex');
  assert.strictEqual(manifest.sha256[publicPath], actualHash, `hash do manifesto diverge para ${publicPath}`);
}

console.log(`OK: fachada ${physicalLines(facade)} linhas; ${componentPaths.length} componentes; maior ${Math.max(...snapshot.components.map(item => item.lines))} linhas.`);
