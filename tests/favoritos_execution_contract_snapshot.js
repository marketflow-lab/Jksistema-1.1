'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const helpers = require('./helpers/favoritos_execution_sources');

const root = path.resolve(__dirname, '..');
const fixture = JSON.parse(fs.readFileSync(
  path.join(root, 'tests', 'fixtures', 'favoritos_execution_contract.json'),
  'utf8'
));
const source = helpers.executionSource(root, {
  includeRuntime: true,
  includePublicApi: true
});
const loader = fs.readFileSync(
  path.join(root, 'static', 'favoritos', 'tabelas-layout', '07-execucao-render-layout.js'),
  'utf8'
);

assert.deepStrictEqual(helpers.executionComponentFiles, fixture.component_order);

for (const entry of Object.entries(fixture.public_api)) {
  const name = entry[0];
  const parameters = entry[1];
  const declaration = new RegExp(
    '(?:async\\s+)?function\\s+' + name + '\\s*\\(([^)]*)\\)'
  ).exec(source);
  assert.ok(declaration, 'funcao publica ausente: ' + name);
  assert.strictEqual(
    declaration[1].replace(/\\s+/g, ' ').trim(),
    parameters,
    'assinatura publica divergente: ' + name
  );
}

const publicApiBlock = /const publicApi = \{([\s\S]*?)\n\s*\};/.exec(source);
assert.ok(publicApiBlock, 'registro da API publica ausente');
const registeredNames = Array.from(
  publicApiBlock[1].matchAll(/^\s*([A-Za-z0-9_$]+),?\s*$/gm),
  match => match[1]
);
assert.deepStrictEqual(registeredNames, Object.keys(fixture.public_api));

assert.match(source, new RegExp('tempoLimiteMs:\\s*' + fixture.invariants.collection_timeout_ms));
assert.match(source, new RegExp('executarComTimeoutFavoritos\\(executarColetaControlada,\\s*' + fixture.invariants.collection_outer_timeout_ms));
assert.match(source, new RegExp('maxPassadas:\\s*' + fixture.invariants.collection_passes));
assert.match(source, new RegExp('loteCliques:\\s*' + fixture.invariants.avant_click_batch));
assert.match(source, new RegExp('Math\\.min\\(' + fixture.invariants.max_workers + ',\\s*selecionadosLista\\.length\\)'));
assert.match(source, new RegExp("workerIds = \\['" + fixture.invariants.fallback_worker + "'\\]"));
assert.match(source, new RegExp("FAVORITOS_JOB_PROXIMA_COLETA_PATH = '" + fixture.invariants.job_next_collection_path + "'"));
assert.match(source, new RegExp("FAVORITOS_JOB_COLETA_TERMO_PATH = '" + fixture.invariants.job_term_collection_path + "'"));
assert.match(source, /preserveAvantProSession:\s*true/);

for (const fileName of fixture.component_order) {
  assert.ok(loader.includes('"' + fileName + '"'), 'componente ausente da fachada: ' + fileName);
}
assert.match(loader, /__FAVORITOS_EXECUCAO_RENDER_LAYOUT_READY__/);

console.log('Favoritos execution contract snapshot checks passed');
