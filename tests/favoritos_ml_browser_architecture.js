'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const {
  generatedBrowserPaths,
  readBrowserAssets
} = require('./helpers/favoritos_browser_sources');

const root = path.resolve(__dirname, '..');
const read = relative => fs.readFileSync(path.join(root, relative), 'utf8');
const lines = source => source.split(/\r?\n/).length;

function declaredFunctions(source) {
  const functions = [];
  const sourceLines = source.split(/\r?\n/);
  for (let index = 0; index < sourceLines.length; index += 1) {
    const match = sourceLines[index].match(/^(\s*)(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(/);
    if (!match) continue;
    const closing = `${match[1]}}`;
    let end = index + 1;
    while (end < sourceLines.length && sourceLines[end] !== closing) end += 1;
    assert.ok(end < sourceLines.length, `corpo sem fechamento para ${match[2]}`);
    functions.push({ name: match[2], startLine: index + 1, endLine: end + 1, length: end - index + 1 });
  }
  return functions;
}

const assets = readBrowserAssets(root);
const facade = read(assets.facade);
assert.ok(lines(facade) <= 300, `fachada excedeu 300 linhas: ${lines(facade)}`);
assert.match(facade, /__FAVORITOS_ML_BROWSER_READY__/);
assert.match(facade, /Promise\.all\(stage\.map\(loadScript\)\)/);

const componentPaths = generatedBrowserPaths(root);
for (const relative of componentPaths) {
  const source = read(relative);
  assert.ok(lines(source) <= 800, `${relative} excedeu 800 linhas: ${lines(source)}`);
  assert.doesNotMatch(source, /(?:from\s+|import\s*\(|require\s*\()[^\n]*ml-browser\.js/, `${relative} importa a fachada`);
  assert.doesNotMatch(source, /document\.write\s*\(/, `${relative} usa carregamento implicito`);
  if (relative.includes('/page-scripts/')) continue;
  for (const fn of declaredFunctions(source)) {
    assert.ok(fn.length <= 120, `${relative}:${fn.startLine} ${fn.name} possui ${fn.length} linhas`);
  }
}

const snapshot = JSON.parse(read('tests/fixtures/favoritos_ml_browser_contract.json'));
assert.equal(snapshot.globals.length, 179);
const publicSource = read(assets.publicApi);
for (const { name } of snapshot.globals) {
  assert.match(publicSource, new RegExp(`"${name}"`), `API publica ausente: ${name}`);
}

const htmlRoot = fs.readFileSync(path.join(root, 'favoritos.html'));
const htmlStatic = fs.readFileSync(path.join(root, 'static/favoritos.html'));
assert.deepStrictEqual(htmlRoot, htmlStatic, 'espelhos HTML devem ser identicos');
const html = htmlStatic.toString('utf8');
assert.match(html, /\/favoritos\/v2\/browser\/avant-cache\.js[\s\S]*\/favoritos\/ml-browser\.js[\s\S]*\/favoritos\/tabelas-layout\.js/);
const init = read('static/favoritos/init.js');
assert.match(init, /__FAVORITOS_ML_BROWSER_READY__[\s\S]*__FAVORITOS_TABELAS_LAYOUT_READY__[\s\S]*Promise\.all\(componentReadiness\)/);

const assetManifest = JSON.parse(read('static/favoritos/asset-manifest.json'));
const contracted = new Set(assetManifest.assets || []);
for (const relative of componentPaths) {
  const publicPath = relative.replace(/^static\//, '');
  assert.ok(contracted.has(publicPath), `manifesto nao inclui ${publicPath}`);
}

console.log(`OK: fachada ${lines(facade)} linhas; ${componentPaths.length} componentes respeitam os limites arquiteturais.`);
