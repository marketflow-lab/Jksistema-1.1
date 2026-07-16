'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');
const { collectInventory } = require('./test-inventory');

const root = path.resolve(__dirname, '..');
const outputDir = path.join(root, 'test-results', 'node');
const inventory = collectInventory();
const electronTest = 'tests/favoritos_workers_pool_electron.js';
const testFiles = [...inventory.categories.node]
  .sort((left, right) => Number(left === electronTest) - Number(right === electronTest) || left.localeCompare(right));
const results = [];

fs.mkdirSync(outputDir, { recursive: true });

for (const relativeFile of testFiles) {
  const startedAt = Date.now();
  let command = process.execPath;
  let args = [path.join(root, relativeFile)];
  if (process.platform === 'linux' && relativeFile === electronTest) {
    command = 'xvfb-run';
    args = ['-a', process.execPath, path.join(root, relativeFile)];
  }
  console.log(`\n[node-tests] ${relativeFile}`);
  const result = spawnSync(command, args, {
    cwd: root,
    env: {
      ...process.env,
      ELECTRON_DISABLE_SECURITY_WARNINGS: 'true',
      JK_TEST_MODE: '1',
    },
    stdio: 'inherit',
    timeout: 240_000,
  });
  const passed = result.status === 0;
  results.push({
    file: relativeFile,
    passed,
    exitCode: result.status,
    signal: result.signal || '',
    error: result.error ? String(result.error.message || result.error) : '',
    durationMs: Date.now() - startedAt,
  });
}

const summary = {
  generatedAt: new Date().toISOString(),
  total: results.length,
  passed: results.filter(item => item.passed).length,
  failed: results.filter(item => !item.passed).length,
  results,
};
fs.writeFileSync(path.join(outputDir, 'summary.json'), `${JSON.stringify(summary, null, 2)}\n`, 'utf8');
console.log(`\n[node-tests] ${summary.passed}/${summary.total} arquivos aprovados.`);
if (summary.failed) process.exitCode = 1;
