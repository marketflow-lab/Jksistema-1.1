'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const helpers = require('./helpers/favoritos_promotion_effectuation_sources');

const root = path.resolve(__dirname, '..');
const fixture = JSON.parse(fs.readFileSync(
  path.join(root, 'tests', 'fixtures', 'favoritos_promotion_effectuation_contract.json'),
  'utf8'
));
const source = helpers.promotionEffectuationSource(root, {
  includeRuntime: true,
  includePublicApi: true,
  installTestGlobals: false
});
const facade = fs.readFileSync(
  path.join(root, 'static', 'favoritos', 'promocoes-efetivacao.js'),
  'utf8'
);

function normalizeParameters(value) {
  return value.replace(/\s+/g, ' ').trim();
}

function functionParameters(name) {
  const declaration = new RegExp(
    '(?:async\\s+)?function\\s+' + name + '\\s*\\(([^)]*)\\)'
  ).exec(source);
  if (declaration) return normalizeParameters(declaration[1]);
  const arrow = new RegExp(
    '(?:const|let|var)\\s+' + name + '\\s*=\\s*(?:async\\s*)?\\(([^)]*)\\)\\s*=>'
  ).exec(source);
  return arrow ? normalizeParameters(arrow[1]) : null;
}

assert.strictEqual(fixture.source_sha256, '32de59712f6653f55e6dd287afdf33f7ee6b45349cf9ae017e1c9e3380a44118');
assert.strictEqual(fixture.legacy_source_lines, 2953);
assert.deepStrictEqual(helpers.componentFiles, fixture.component_order);

for (const [name, expectedParameters] of Object.entries(fixture.public_signatures)) {
  assert.strictEqual(functionParameters(name), expectedParameters, 'assinatura publica divergente: ' + name);
}
fixture.top_level_symbols.forEach(name => {
  assert.notStrictEqual(functionParameters(name), null, 'simbolo legado ausente da extracao: ' + name);
});

const groupBlock = /const groupNames = (\{[\s\S]*?\n  \});/.exec(source);
assert.ok(groupBlock, 'registro agrupado da API publica ausente');
assert.deepStrictEqual(JSON.parse(groupBlock[1]), fixture.api_groups);

const legacyBlock = /feature\.legacyGlobals = pick\((\[[\s\S]*?\n  \])\);/.exec(source);
assert.ok(legacyBlock, 'registro de aliases legados ausente');
assert.deepStrictEqual(JSON.parse(legacyBlock[1]), fixture.legacy_globals);
fixture.internal_only_symbols.forEach(name => {
  assert.ok(!fixture.legacy_globals.includes(name), 'helper interno presente na fachada: ' + name);
});

assert.match(source, /listingsBySku:\s*'\/api\/favoritos\/ml\/anuncios-sku'/);
assert.match(source, /preflight:\s*'\/api\/favoritos\/ml\/validar-efetivacao'/);
assert.match(source, /effectuation:\s*'\/api\/favoritos\/ml\/efetivar-promocao'/);
fixture.outcomes.forEach(outcome => {
  assert.match(source, new RegExp("'" + outcome + "'"));
});
fixture.component_order.forEach(fileName => {
  assert.ok(facade.includes('"' + fileName + '"'), 'componente ausente da fachada: ' + fileName);
});
assert.match(facade, /__FAVORITOS_PROMOCOES_EFETIVACAO_READY__/);

console.log('Favoritos promotion-effectuation contract snapshot checks passed');
