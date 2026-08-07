'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const helpers = require('./helpers/favoritos_search_ranking_sources');

const root = path.resolve(__dirname, '..');
const componentDir = path.join(root, 'static', 'favoritos', 'v2', 'search-ranking');
const facadePath = path.join(root, 'static', 'favoritos', 'tabelas-layout', '04-promocoes-busca-ranking.js');

function lineCount(source) {
  return source.split(/\r?\n/).length - (source.endsWith('\n') ? 1 : 0);
}

function topLevelFunctionSpans(source) {
  const lines = source.split(/\r?\n/);
  const spans = [];
  lines.forEach((line, index) => {
    const match = /^\s{2}(?:async\s+)?function\s+([A-Za-z0-9_$]+)[^{]*\{\s*$/.exec(line);
    if (!match) return;
    let end = index + 1;
    while (end < lines.length && !/^\s{2}\}\s*$/.test(lines[end])) end += 1;
    assert.ok(end < lines.length, 'fim da funcao nao encontrado: ' + match[1]);
    spans.push({ name: match[1], line: index + 1, span: end - index + 1 });
  });
  return spans;
}

const facade = fs.readFileSync(facadePath, 'utf8');
assert.ok(lineCount(facade) <= 300, 'fachada deve ter no maximo 300 linhas');
assert.match(facade, /__FAVORITOS_PROMOCOES_BUSCA_RANKING_READY__/);
assert.doesNotMatch(facade, /document\.write/);

for (const fileName of helpers.searchRankingComponentFiles) {
  const source = fs.readFileSync(path.join(componentDir, fileName), 'utf8');
  assert.ok(lineCount(source) <= 800, fileName + ' excede 800 linhas');
  assert.doesNotMatch(source, /document\.write|globals\(\)\.update|bind_runtime_globals|PEER_EXPORTS/);
  assert.doesNotMatch(source, /(?:require|import|script\.src)[^\n]*04-promocoes-busca-ranking/);
  topLevelFunctionSpans(source).forEach(declaration => {
    assert.ok(
      declaration.span <= 120,
      fileName + ':' + declaration.name + ' ocupa ' + declaration.span + ' linhas'
    );
  });
}

const runtime = fs.readFileSync(path.join(componentDir, '00-runtime.js'), 'utf8');
assert.match(runtime, /__runtimeInitialized/);
assert.match(runtime, /adapterNames = new Set/);
assert.match(runtime, /Object\.defineProperties\(state/);

const loader = fs.readFileSync(path.join(root, 'static', 'favoritos', 'tabelas-layout.js'), 'utf8');
assert.match(loader, /04-promocoes-busca-ranking\.js[\s\S]*__FAVORITOS_PROMOCOES_BUSCA_RANKING_READY__/);
assert.match(loader, /07-execucao-render-layout\.js[\s\S]*__FAVORITOS_EXECUCAO_RENDER_LAYOUT_READY__/);

const contract = JSON.parse(fs.readFileSync(
  path.join(root, 'tests', 'fixtures', 'favoritos_search_ranking_contract.json'),
  'utf8'
));
const consumers = [
  'static/favoritos/init.js',
  'static/favoritos/ranking.js',
  'static/favoritos/tabelas-layout/02-ia-datas-selecao.js',
  'static/favoritos/v2/sku-sidebar/01-core.js',
  'static/favoritos/v2/sku-sidebar/02-ui.js',
  'static/favoritos/tabelas-layout/05-resultados-historico.js',
  'static/favoritos/tabelas-layout/06-ranking-manual-historico-ui.js',
  'static/favoritos/tabelas-layout/08-render-avant-mercadolivre.js',
  'static/favoritos/v2/browser/avant-controls.js',
  'static/favoritos/v2/browser/avant-entry.js',
  'static/favoritos/v2/browser/avant-session.js',
  'static/favoritos/v2/browser/browser-ui.js',
  'static/favoritos/v2/browser/readiness.js',
  'static/favoritos/v2/execution/01-collection-evidence.js',
  'static/favoritos/v2/execution/02-collection-controller.js',
  'static/favoritos/v2/execution/03-worker-pool.js',
  'static/favoritos/v2/execution/04-confirmed-flow.js',
  'static/favoritos/v2/execution/05-legacy-renderer.js',
  'static/favoritos/v2/execution/06-job-runtime.js',
  'static/favoritos/v2/execution/07-job-controls.js',
  'static/favoritos/v2/execution/08-ranking-entrypoints.js'
];

for (const relativePath of consumers) {
  const source = fs.readFileSync(path.join(root, relativePath), 'utf8');
  assert.match(source, /window\.FavoritosV2\.searchRanking\.publicApi/);
  let withoutNamedApi = source;
  Object.entries(contract.api_groups).forEach(([groupName, names]) => {
    names.forEach(name => {
      withoutNamedApi = withoutNamedApi
        .split(`window.FavoritosV2.searchRanking.publicApi.${groupName}.${name}`)
        .join('');
    });
  });
  contract.legacy_globals.forEach(name => {
    assert.doesNotMatch(
      withoutNamedApi,
      new RegExp(`\\b${name}\\b`),
      `${relativePath} ainda consome o global legado ${name}`
    );
  });
}

console.log('Favoritos search-ranking architecture checks passed');
