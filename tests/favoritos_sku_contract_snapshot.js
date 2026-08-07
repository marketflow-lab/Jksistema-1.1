'use strict';

const assert = require('assert');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { reconstructLegacySource } = require('./helpers/favoritos_sku_sources');

const root = path.resolve(__dirname, '..');
const fixture = JSON.parse(fs.readFileSync(path.join(root, 'tests', 'fixtures', 'favoritos_sku_contract.json'), 'utf8'));
const source = reconstructLegacySource(root);
const hash = crypto.createHash('sha256').update(source).digest('hex');
assert.strictEqual(hash, fixture.baseline.source_sha256_normalized);
assert.strictEqual(source.split('\n').length - (source.endsWith('\n') ? 1 : 0), fixture.baseline.line_count);

const names = Array.from(source.matchAll(/^\s{8}(?:async\s+)?function\s+([A-Za-z0-9_$]+)/gm), match => match[1]);
assert.strictEqual(names.length, fixture.baseline.function_count);
assert.deepStrictEqual(names, Object.values(fixture.groups).flat());

for (const [name, parameters] of Object.entries(fixture.signatures)) {
  const match = new RegExp('(?:async\\s+)?function\\s+' + name + '\\s*\\(([\\s\\S]*?)\\)\\s*\\{').exec(source);
  assert.ok(match, 'funcao ausente: ' + name);
  assert.strictEqual(match[1].replace(/\s+/g, ' ').trim(), parameters, 'assinatura mudou: ' + name);
}

for (const contract of fixture.http) {
  assert.ok(source.includes(contract.path), 'endpoint ausente: ' + contract.path);
}

const publicApi = fs.readFileSync(path.join(root, 'static', 'favoritos', 'v2', 'sku', '05-public-api.js'), 'utf8');
for (const name of names) {
  assert.ok(publicApi.includes("'" + name + "'") || publicApi.includes('"' + name + '"'), 'API publica omitiu ' + name);
}
for (const groupName of Object.keys(fixture.semantic_groups)) {
  assert.ok(publicApi.includes(groupName), 'grupo semantico ausente: ' + groupName);
}
assert.strictEqual(fixture.external_consumers.public_names.length, 24);

console.log('Favoritos SKU contract snapshot checks passed');
