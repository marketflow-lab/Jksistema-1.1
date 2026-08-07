'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const helpers = require('./helpers/favoritos_promotion_effectuation_sources');

const root = path.resolve(__dirname, '..');
const componentDir = path.join(root, 'static', 'favoritos', 'v2', 'promotion-effectuation');
const facadePath = path.join(root, 'static', 'favoritos', 'promocoes-efetivacao.js');
const fixture = JSON.parse(fs.readFileSync(
  path.join(root, 'tests', 'fixtures', 'favoritos_promotion_effectuation_contract.json'),
  'utf8'
));

function lineCount(source) {
  return source.split(/\r?\n/).length - (source.endsWith('\n') ? 1 : 0);
}

function topLevelFunctionSpans(source) {
  const lines = source.split(/\r?\n/);
  const spans = [];
  lines.forEach((line, index) => {
    const declaration = /^  (?:async\s+)?function\s+([A-Za-z0-9_$]+)/.exec(line);
    const arrow = /^  (?:const|let|var)\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?\(/.exec(line);
    const match = declaration || arrow;
    if (!match) return;
    const closing = declaration ? /^  }\s*$/ : /^  };\s*$/;
    let end = index + 1;
    while (end < lines.length && !closing.test(lines[end])) end += 1;
    assert.ok(end < lines.length, 'fim da funcao nao encontrado: ' + match[1]);
    spans.push({ name: match[1], line: index + 1, span: end - index + 1 });
  });
  return spans;
}

function listFiles(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const absolute = path.join(directory, entry.name);
    return entry.isDirectory() ? listFiles(absolute) : [absolute];
  });
}

const facade = fs.readFileSync(facadePath, 'utf8');
assert.ok(lineCount(facade) <= fixture.budgets.facade_lines, 'fachada excede 300 linhas');
assert.match(facade, /if \(global\.__FAVORITOS_PROMOCOES_EFETIVACAO_READY__\) return/);
assert.doesNotMatch(facade, /document\.write/);

for (const fileName of helpers.componentFiles) {
  const source = fs.readFileSync(path.join(componentDir, fileName), 'utf8');
  assert.ok(
    lineCount(source) <= fixture.budgets.component_lines,
    fileName + ' excede ' + fixture.budgets.component_lines + ' linhas'
  );
  assert.doesNotMatch(source, /document\.write|globals\(\)\.update|bind_runtime_globals|PEER_EXPORTS/);
  assert.doesNotMatch(source, /import\s+\*|require\([^)]*promocoes-efetivacao|script\.src[^\n]*promocoes-efetivacao/);
  topLevelFunctionSpans(source).forEach(declaration => {
    assert.ok(
      declaration.span <= fixture.budgets.function_lines,
      fileName + ':' + declaration.name + ' ocupa ' + declaration.span + ' linhas'
    );
  });
}

const runtime = fs.readFileSync(path.join(componentDir, '00-runtime.js'), 'utf8');
assert.match(runtime, /__runtimeInitialized/);
assert.match(runtime, /adapterNames = new Set/);
assert.match(runtime, /configuredAdapters = new Map/);
assert.match(runtime, /Object\.defineProperties\(state/);
assert.match(runtime, /function configure\(config = \{\}\)/);

const init = fs.readFileSync(path.join(root, 'static', 'favoritos', 'init.js'), 'utf8');
assert.match(init, /promotionEffectuationReady = window\.__FAVORITOS_PROMOCOES_EFETIVACAO_READY__/);
assert.match(init, /componentReadiness = \[skuReady, browserReady, layoutReady, promotionEffectuationReady\]/);

const productionFiles = listFiles(path.join(root, 'static', 'favoritos'))
  .filter(fileName => fileName.endsWith('.js'))
  .filter(fileName => !fileName.startsWith(componentDir + path.sep))
  .filter(fileName => fileName !== facadePath);
for (const fileName of productionFiles) {
  const relativePath = path.relative(root, fileName).replace(/\\/g, '/');
  let source = fs.readFileSync(fileName, 'utf8');
  for (const [groupName, names] of Object.entries(fixture.api_groups)) {
    names.forEach(name => {
      source = source
        .split('window.FavoritosV2.promotionEffectuation.publicApi.' + groupName + '.' + name)
        .join('');
    });
  }
  [...fixture.legacy_globals, ...fixture.internal_only_symbols].forEach(name => {
    assert.doesNotMatch(
      source,
      new RegExp('\\b' + name + '\\b'),
      relativePath + ' ainda consome o simbolo legado ' + name
    );
  });
}

console.log('Favoritos promotion-effectuation architecture checks passed');
