'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');
const { captureGitSourceState, gitSourceStateIsStable } = require('../scripts/source-tree-fingerprint');

const fullSuiteSource = fs.readFileSync(path.resolve(__dirname, '..', 'scripts', 'run-full-test-suite.js'), 'utf8');
assert.ok(
  fullSuiteSource.indexOf('const source = captureGitSourceState(root);') < fullSuiteSource.indexOf('fs.mkdirSync(reportRoot'),
  'proveniencia deve ser capturada antes de a suite criar artefatos',
);
assert.ok(
  fullSuiteSource.indexOf("runStep('Sondas diagnosticas sem chamadas externas'")
    < fullSuiteSource.indexOf('finalSource = captureGitSourceState(root);'),
  'proveniencia final deve ser recapturada depois de todas as etapas',
);
assert.ok(
  fullSuiteSource.indexOf('finalSource = captureGitSourceState(root);') < fullSuiteSource.indexOf('const summary = {'),
  'proveniencia final deve ser avaliada antes do resumo',
);
assert.match(fullSuiteSource, /Integridade da arvore durante a suite[\s\S]*passed: sourceStable/, 'mudanca da arvore deve reprovar uma etapa');
assert.match(fullSuiteSource, /const summary = \{[\s\S]*?source,[\s\S]*?finalSource,[\s\S]*?sourceStable,[\s\S]*?invalidated:/, 'summary.json deve registrar as duas capturas e invalidacao');
assert.match(fullSuiteSource, /summary\.failedSteps \|\| summary\.invalidated/, 'suite invalidada deve retornar falha');
assert.match(fullSuiteSource, /Commit:[\s\S]*Estado da arvore:[\s\S]*Fingerprint da arvore:[\s\S]*Integridade durante a suite:/, 'report.md deve exibir proveniencia e integridade');

function git(root, args) {
  const result = spawnSync('git', args, { cwd: root, encoding: 'utf8' });
  assert.strictEqual(result.status, 0, result.stderr || result.stdout);
}

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'jk-source-state-'));
try {
  git(root, ['init', '--quiet']);
  git(root, ['config', 'user.name', 'JK QA']);
  git(root, ['config', 'user.email', 'qa@example.invalid']);
  fs.writeFileSync(path.join(root, 'tracked.txt'), 'baseline\n', 'utf8');
  git(root, ['add', 'tracked.txt']);
  git(root, ['commit', '--quiet', '-m', 'baseline']);

  const clean = captureGitSourceState(root);
  assert.match(clean.commitSha, /^[a-f0-9]{40}$/);
  assert.strictEqual(clean.worktreeState, 'clean');
  assert.match(clean.worktreeFingerprint, /^sha256:[a-f0-9]{64}$/);
  assert.deepStrictEqual(captureGitSourceState(root), clean, 'fingerprint limpo deve ser deterministico');
  assert.strictEqual(gitSourceStateIsStable(clean, captureGitSourceState(root)), true);
  assert.strictEqual(gitSourceStateIsStable(clean, null), false, 'falha de recaptura deve invalidar');

  fs.writeFileSync(path.join(root, 'tracked.txt'), 'alteracao-nao-exposta\n', 'utf8');
  const dirty = captureGitSourceState(root);
  assert.strictEqual(dirty.worktreeState, 'dirty');
  assert.notStrictEqual(dirty.worktreeFingerprint, clean.worktreeFingerprint);
  assert.deepStrictEqual(captureGitSourceState(root), dirty, 'fingerprint sujo deve ser deterministico');
  assert.strictEqual(gitSourceStateIsStable(clean, dirty), false, 'mudanca de fingerprint deve invalidar');
  assert.strictEqual(
    gitSourceStateIsStable(clean, { ...clean, commitSha: 'f'.repeat(40) }),
    false,
    'mudanca de commit deve invalidar',
  );

  fs.writeFileSync(path.join(root, 'nao-rastreado.txt'), 'segredo-de-teste-nao-exposto\n', 'utf8');
  const withUntracked = captureGitSourceState(root);
  assert.notStrictEqual(withUntracked.worktreeFingerprint, dirty.worktreeFingerprint);
  const serialized = JSON.stringify(withUntracked);
  assert.ok(!serialized.includes(root), 'metadados nao podem expor caminho absoluto');
  assert.ok(!serialized.includes('tracked.txt'), 'metadados nao podem expor caminho relativo');
  assert.ok(!serialized.includes('segredo-de-teste'), 'metadados nao podem expor conteudo');
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}

console.log('Suite completa: metadados de proveniencia aprovados.');
