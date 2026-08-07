'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const helpers = require('./helpers/favoritos_execution_sources');

const root = path.resolve(__dirname, '..');
const executionDir = path.join(root, 'static', 'favoritos', 'v2', 'execution');
const facadePath = path.join(
  root,
  'static',
  'favoritos',
  'tabelas-layout',
  '07-execucao-render-layout.js'
);
const facade = fs.readFileSync(facadePath, 'utf8');

function lineCount(source) {
  return source.split(/\r?\n/).length - (source.endsWith('\n') ? 1 : 0);
}

assert.ok(lineCount(facade) <= 300, 'fachada deve ter no maximo 300 linhas');
assert.doesNotMatch(facade, /document\.write/);
assert.match(facade, /__FAVORITOS_EXECUCAO_RENDER_LAYOUT_READY__/);

for (const fileName of helpers.executionComponentFiles) {
  const source = fs.readFileSync(path.join(executionDir, fileName), 'utf8');
  assert.ok(lineCount(source) <= 800, fileName + ' excede 800 linhas');
  assert.doesNotMatch(source, /document\.write|globals\(\)\.update|bind_runtime_globals|PEER_EXPORTS/);
  assert.doesNotMatch(source, /(?:require|import|script\.src)[^\n]*07-execucao-render-layout/);
  const declarations = [];
  source.split(/\r?\n/).forEach((line, index) => {
    const match = /^\s*(?:async\s+)?function\s+([A-Za-z0-9_$]+)/.exec(line);
    if (match) declarations.push({ name: match[1], line: index + 1 });
  });
  declarations.forEach((declaration, index) => {
    const nextLine = declarations[index + 1]
      ? declarations[index + 1].line
      : lineCount(source) + 1;
    const span = nextLine - declaration.line;
    assert.ok(
      span <= 120,
      fileName + ':' + declaration.name + ' ocupa ' + span + ' linhas'
    );
  });
}

const loader = fs.readFileSync(
  path.join(root, 'static', 'favoritos', 'tabelas-layout.js'),
  'utf8'
);
assert.match(
  loader,
  /const readyNames = \{[\s\S]*'07-execucao-render-layout\.js': '__FAVORITOS_EXECUCAO_RENDER_LAYOUT_READY__'[\s\S]*componentReady\.then\(resolve, reject\)/
);

const consumers = [
  'static/favoritos/init.js',
  'static/favoritos/ranking.js',
  'static/favoritos/v2/sku/02-store-cache.js',
  'static/favoritos/v2/sku/03-catalog-table.js',
  'static/favoritos/v2/sku-sidebar/01-core.js',
  'static/favoritos/tabelas-layout/05-resultados-historico.js',
  'static/favoritos/tabelas-layout/06-ranking-manual-historico-ui.js',
  'static/favoritos/tabelas-layout/08-render-avant-mercadolivre.js'
];
const fixture = JSON.parse(fs.readFileSync(
  path.join(root, 'tests', 'fixtures', 'favoritos_execution_contract.json'),
  'utf8'
));
const publicNames = Object.keys(fixture.public_api);

for (const relativePath of consumers) {
  const source = fs.readFileSync(path.join(root, relativePath), 'utf8');
  assert.match(source, /window\.FavoritosV2\.execution\.publicApi/);
  let withoutNamedApi = source;
  for (const name of publicNames) {
    withoutNamedApi = withoutNamedApi
      .split('window.FavoritosV2.execution.publicApi.' + name).join('')
      .split('window.FavoritosV2?.execution?.publicApi?.' + name).join('');
  }
  for (const name of publicNames) {
    assert.doesNotMatch(
      withoutNamedApi,
      new RegExp('\\b' + name + '\\b'),
      relativePath + ' ainda consome o global legado ' + name
    );
  }
}

console.log('Favoritos execution architecture checks passed');
