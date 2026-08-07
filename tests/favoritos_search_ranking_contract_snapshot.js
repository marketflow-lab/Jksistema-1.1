'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const helpers = require('./helpers/favoritos_search_ranking_sources');

const root = path.resolve(__dirname, '..');
const fixture = JSON.parse(fs.readFileSync(
  path.join(root, 'tests', 'fixtures', 'favoritos_search_ranking_contract.json'),
  'utf8'
));
const source = helpers.searchRankingSource(root);
const facade = fs.readFileSync(
  path.join(root, 'static', 'favoritos', 'tabelas-layout', '04-promocoes-busca-ranking.js'),
  'utf8'
);

assert.deepStrictEqual(helpers.searchRankingComponentFiles, fixture.component_order);

for (const [name, expectedParameters] of Object.entries(fixture.function_signatures)) {
  const declarations = Array.from(source.matchAll(
    new RegExp('(?:async\\s+)?function\\s+' + name + '\\s*\\(([^)]*)\\)', 'g')
  ));
  assert.ok(declarations.length, 'funcao congelada ausente: ' + name);
  const signatures = declarations.map(match => match[1].replace(/\s+/g, ' ').trim());
  assert.ok(
    signatures.includes(expectedParameters),
    'assinatura divergente: ' + name + ' -> ' + signatures.join(' | ')
  );
}

const publicApiSource = fs.readFileSync(
  path.join(root, 'static', 'favoritos', 'v2', 'search-ranking', '15-public-api.js'),
  'utf8'
);
for (const [groupName, names] of Object.entries(fixture.api_groups)) {
  assert.ok(publicApiSource.includes(JSON.stringify(groupName)), 'grupo ausente: ' + groupName);
  for (const name of names) {
    assert.ok(publicApiSource.includes(JSON.stringify(name)), groupName + ' nao registra ' + name);
  }
}
for (const name of fixture.legacy_globals) {
  assert.ok(publicApiSource.includes(JSON.stringify(name)), 'alias legado ausente: ' + name);
}

for (const endpoint of Object.values(fixture.endpoints)) {
  assert.ok(source.includes(endpoint), 'endpoint congelado ausente: ' + endpoint);
}
assert.match(source, /executarComTimeoutFavoritos\(tarefa, timeoutMs = 45000, sinalPai = undefined\)/);
assert.match(source, /loteCliques:\s*opcoes\.loteCliques === undefined \? 6/);
assert.match(source, /inicio \+= 200/);
assert.match(source, /Math\.min\(180, lista\.length\)/);
assert.match(source, /preserveAvantProSession:\s*true/);

for (const fileName of fixture.component_order) {
  assert.ok(facade.includes(JSON.stringify(fileName)), 'fachada nao carrega ' + fileName);
}
assert.match(facade, /__FAVORITOS_PROMOCOES_BUSCA_RANKING_READY__/);

console.log('Favoritos search-ranking contract snapshot checks passed');
