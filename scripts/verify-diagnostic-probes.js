'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');
const { collectInventory } = require('./test-inventory');

const root = path.resolve(__dirname, '..');
const outputDir = path.join(root, 'test-results', 'diagnostic-probes');
const python = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
const inventory = collectInventory();
const checks = [
  ...inventory.categories.diagnosticPython.map(file => ({ file, command: python, args: ['-m', 'py_compile', file] })),
  ...inventory.categories.diagnosticNode.map(file => ({ file, command: process.execPath, args: ['--check', file] })),
];
const results = [];

fs.mkdirSync(outputDir, { recursive: true });
for (const check of checks) {
  console.log(`[diagnostic-probes] validando ${check.file}`);
  const startedAt = Date.now();
  const result = spawnSync(check.command, check.args, { cwd: root, stdio: 'inherit', timeout: 60_000 });
  results.push({
    file: check.file,
    passed: result.status === 0,
    exitCode: result.status,
    error: result.error ? String(result.error.message || result.error) : '',
    durationMs: Date.now() - startedAt,
  });
}

const summary = {
  generatedAt: new Date().toISOString(),
  mode: 'syntax-only-no-external-calls',
  total: results.length,
  passed: results.filter(item => item.passed).length,
  failed: results.filter(item => !item.passed).length,
  results,
};
fs.writeFileSync(path.join(outputDir, 'summary.json'), `${JSON.stringify(summary, null, 2)}\n`, 'utf8');
console.log(`[diagnostic-probes] ${summary.passed}/${summary.total} sondas validas sem chamadas externas.`);
if (summary.failed) process.exitCode = 1;
