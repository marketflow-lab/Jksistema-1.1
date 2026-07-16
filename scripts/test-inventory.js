'use strict';

const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');

function filesIn(relativeDir, predicate) {
  const absoluteDir = path.join(root, relativeDir);
  if (!fs.existsSync(absoluteDir)) return [];
  return fs.readdirSync(absoluteDir, { withFileTypes: true })
    .filter(entry => entry.isFile() && predicate(entry.name))
    .map(entry => path.posix.join(relativeDir.replaceAll('\\', '/'), entry.name))
    .sort();
}

function collectInventory() {
  const categories = {
    python: filesIn('tests', name => /^test_.*\.py$/i.test(name)),
    node: filesIn('tests', name => name.endsWith('.js')),
    bugHunter: filesIn('tests/bug-hunter/specs', name => name.endsWith('.spec.js')),
    gateway: filesIn('cloudflare/whatsapp-gateway/test', name => name.endsWith('.test.ts')),
    diagnosticPython: filesIn('scripts/diagnosticos', name => /^test_.*\.py$/i.test(name)),
    diagnosticNode: filesIn('scripts', name => /^test_.*\.js$/i.test(name)),
  };
  const automated = categories.python.length
    + categories.node.length
    + categories.bugHunter.length
    + categories.gateway.length;
  const diagnosticProbes = categories.diagnosticPython.length + categories.diagnosticNode.length;
  return {
    generatedAt: new Date().toISOString(),
    categories,
    counts: {
      python: categories.python.length,
      node: categories.node.length,
      bugHunter: categories.bugHunter.length,
      gateway: categories.gateway.length,
      automated,
      diagnosticProbes,
      total: automated + diagnosticProbes,
    },
  };
}

function writeInventory(inventory = collectInventory()) {
  const outputDir = path.join(root, 'test-results', 'full-suite');
  fs.mkdirSync(outputDir, { recursive: true });
  fs.writeFileSync(
    path.join(outputDir, 'inventory.json'),
    `${JSON.stringify(inventory, null, 2)}\n`,
    'utf8',
  );
  return inventory;
}

if (require.main === module) {
  const inventory = writeInventory();
  console.log(
    `[test-inventory] ${inventory.counts.total} arquivos: `
      + `${inventory.counts.automated} automatizados e `
      + `${inventory.counts.diagnosticProbes} sondas diagnosticas.`,
  );
}

module.exports = { collectInventory, writeInventory };
