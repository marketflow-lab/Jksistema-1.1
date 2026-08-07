'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const helpers = require('./helpers/favoritos_sku_sidebar_sources');

const root = path.resolve(__dirname, '..');
const componentDir = path.join(root, 'static', 'favoritos', 'v2', 'sku-sidebar');
const facadePath = path.join(root, 'static', 'favoritos', 'tabelas-layout', '03-sku-sidebar-modal.js');
const fixture = JSON.parse(fs.readFileSync(
  path.join(root, 'tests', 'fixtures', 'favoritos_sku_sidebar_contract.json'),
  'utf8'
));

function lineCount(source) {
  return source.split(/\r?\n/).length - (source.endsWith('\n') ? 1 : 0);
}

function topLevelFunctionSpans(source) {
  const lines = source.split(/\r?\n/);
  const spans = [];
  lines.forEach((line, index) => {
    const match = /^        (?:async\s+)?function\s+([A-Za-z0-9_$]+)/.exec(line);
    if (!match) return;
    let end = index + 1;
    while (end < lines.length && !/^        }\s*$/.test(lines[end])) end += 1;
    assert.ok(end < lines.length, 'fim da funcao nao encontrado: ' + match[1]);
    spans.push({ name: match[1], line: index + 1, span: end - index + 1 });
  });
  return spans;
}

const facade = fs.readFileSync(facadePath, 'utf8');
assert.ok(lineCount(facade) <= fixture.budgets.facade_lines, 'fachada excede o limite');
assert.match(facade, /__FAVORITOS_SKU_SIDEBAR_READY__/);
assert.doesNotMatch(facade, /document\.write/);

helpers.componentFiles.forEach(fileName => {
  const source = fs.readFileSync(path.join(componentDir, fileName), 'utf8');
  assert.ok(lineCount(source) <= fixture.budgets.component_lines, fileName + ' excede o limite');
  assert.doesNotMatch(source, /document\.write|globals\(\)\.update|bind_runtime_globals|PEER_EXPORTS/);
  assert.doesNotMatch(source, /import\s+\*|03-sku-sidebar-modal\.js/);
  topLevelFunctionSpans(source).forEach(declaration => {
    assert.ok(
      declaration.span <= fixture.budgets.function_lines,
      fileName + ':' + declaration.name + ' ocupa ' + declaration.span + ' linhas'
    );
  });
});

const runtime = fs.readFileSync(path.join(componentDir, '00-runtime.js'), 'utf8');
assert.match(runtime, /__runtimeInitialized/);
assert.match(runtime, /adapterNames = new Set/);
assert.match(runtime, /Object\.defineProperties\(state/);

const context = { window: {}, console };
context.window.window = context.window;
vm.createContext(context);
helpers.componentFiles.forEach(fileName => {
  vm.runInContext(fs.readFileSync(path.join(componentDir, fileName), 'utf8'), context, { filename: fileName });
});
const feature = context.window.FavoritosV2.skuSidebar;
assert.equal(feature.__componentsReady, true);
assert.deepStrictEqual([...feature.publicApiNames], fixture.public_names);
fixture.public_names.forEach(name => {
  assert.strictEqual(context.window[name], feature.publicApi[name], 'alias global divergente: ' + name);
});

const loader = fs.readFileSync(path.join(root, 'static', 'favoritos', 'tabelas-layout.js'), 'utf8');
assert.match(loader, /03-sku-sidebar-modal\.js[\s\S]*__FAVORITOS_SKU_SIDEBAR_READY__/);
assert.match(loader, /04-promocoes-busca-ranking\.js[\s\S]*__FAVORITOS_PROMOCOES_BUSCA_RANKING_READY__/);
assert.match(loader, /07-execucao-render-layout\.js[\s\S]*__FAVORITOS_EXECUCAO_RENDER_LAYOUT_READY__/);

console.log('Favoritos SKU sidebar architecture checks passed');
