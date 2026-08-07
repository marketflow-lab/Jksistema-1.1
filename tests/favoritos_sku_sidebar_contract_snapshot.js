'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const helpers = require('./helpers/favoritos_sku_sidebar_sources');

const root = path.resolve(__dirname, '..');
const fixture = JSON.parse(fs.readFileSync(
  path.join(root, 'tests', 'fixtures', 'favoritos_sku_sidebar_contract.json'),
  'utf8'
));
const source = helpers.skuSidebarSource(root, { includeRuntime: false, includePublicApi: false });
const facade = fs.readFileSync(
  path.join(root, 'static', 'favoritos', 'tabelas-layout', '03-sku-sidebar-modal.js'),
  'utf8'
);

function normalizeParameters(value) {
  return value.replace(/\s+/g, ' ').trim();
}

function functionParameters(name) {
  const declaration = new RegExp(
    '(?:async\\s+)?function\\s+' + name + '\\s*\\(([^)]*)\\)'
  ).exec(source);
  return declaration ? normalizeParameters(declaration[1]) : null;
}

assert.strictEqual(fixture.legacy_source_lines, 1297);
assert.match(fixture.legacy_source_sha256, /^[a-f0-9]{64}$/);
assert.deepStrictEqual(helpers.componentFiles, fixture.component_order);
Object.entries(fixture.public_signatures).forEach(([name, parameters]) => {
  assert.strictEqual(functionParameters(name), parameters, 'assinatura divergente: ' + name);
});
fixture.component_order.forEach(fileName => {
  assert.ok(facade.includes("'" + fileName + "'"), 'componente ausente da fachada: ' + fileName);
});
assert.match(facade, /__FAVORITOS_SKU_SIDEBAR_READY__/);

console.log('Favoritos SKU sidebar contract snapshot checks passed');
