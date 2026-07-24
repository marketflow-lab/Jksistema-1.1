'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');
const { captureGitSourceState, gitSourceStateIsStable } = require('./source-tree-fingerprint');
const { writeInventory } = require('./test-inventory');

const root = path.resolve(__dirname, '..');
const resultRoot = path.join(root, 'test-results');
const reportRoot = path.join(resultRoot, 'full-suite');
const coverageRoot = path.join(resultRoot, 'coverage');
const python = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
const source = captureGitSourceState(root);

console.log(
  `[full-suite] origem commit=${source.commitSha} estado=${source.worktreeState} `
    + `fingerprint=${source.worktreeFingerprint}`,
);

fs.mkdirSync(reportRoot, { recursive: true });
fs.mkdirSync(path.join(resultRoot, 'python'), { recursive: true });
fs.mkdirSync(coverageRoot, { recursive: true });

const inventory = writeInventory();
const steps = [];

function runStep(name, command, args, options = {}) {
  const startedAt = Date.now();
  console.log(`\n========== ${name} ==========`);
  let executable = command;
  let commandArgs = args;
  if (process.platform === 'win32' && /\.cmd$/i.test(command)) {
    executable = process.env.ComSpec || 'cmd.exe';
    commandArgs = ['/d', '/s', '/c', command, ...args];
  }
  const result = spawnSync(executable, commandArgs, {
    cwd: root,
    env: { ...process.env, ...(options.env || {}) },
    stdio: 'inherit',
    timeout: options.timeout || 900_000,
  });
  const entry = {
    name,
    passed: result.status === 0,
    exitCode: result.status,
    signal: result.signal || '',
    error: result.error ? String(result.error.message || result.error) : '',
    durationMs: Date.now() - startedAt,
  };
  steps.push(entry);
  return entry;
}

runStep('Locks de dependencias', process.execPath, ['scripts/verify-dependency-locks.js']);
runStep('Espelhos HTML estaticos', process.execPath, ['scripts/sync-static-html-mirrors.js', '--check']);
runStep('Limpeza da cobertura Python', python, ['-m', 'coverage', 'erase']);
runStep('Testes Python com cobertura', process.execPath, ['scripts/run-python-tests.js', '--coverage']);
runStep('Limite de cobertura Python', python, ['-m', 'coverage', 'report', '--fail-under=30']);
runStep('Relatorio XML de cobertura', python, ['-m', 'coverage', 'xml', '-o', 'test-results/coverage/coverage.xml']);
runStep('Relatorio HTML de cobertura', python, ['-m', 'coverage', 'html', '-d', 'test-results/coverage/html']);
runStep('Regressoes Node e Electron', process.execPath, ['scripts/run-node-regressions.js']);
runStep('TypeScript do gateway', npm, ['--prefix', 'cloudflare/whatsapp-gateway', 'run', 'check']);
runStep('Testes do gateway', npm, ['--prefix', 'cloudflare/whatsapp-gateway', 'test']);
runStep('Contrato seguro do Bug Hunter', npm, ['run', 'test:bug-hunter:contract'], {
  env: {
    BUG_HUNTER_ALLOW_MISSING_SECRETS: 'true',
    BUG_HUNTER_DRY_RUN: 'true',
    BUG_HUNTER_ALLOW_MUTATIONS: 'false',
  },
});
runStep('Sondas diagnosticas sem chamadas externas', process.execPath, ['scripts/verify-diagnostic-probes.js']);

const integrityStartedAt = Date.now();
let finalSource = null;
try {
  finalSource = captureGitSourceState(root);
} catch (_error) {
  // A mensagem publica e deliberadamente generica para nao vazar paths ou conteudo Git.
}
const sourceStable = gitSourceStateIsStable(source, finalSource);
steps.push({
  name: 'Integridade da arvore durante a suite',
  passed: sourceStable,
  exitCode: sourceStable ? 0 : 1,
  signal: '',
  error: sourceStable ? '' : 'A arvore mudou ou nao pode ser recapturada; resultados invalidados.',
  durationMs: Date.now() - integrityStartedAt,
});
if (!sourceStable) {
  console.error('[full-suite] INVALIDADA - a origem mudou ou nao pode ser recapturada durante a execucao.');
}

const summary = {
  generatedAt: new Date().toISOString(),
  source,
  finalSource,
  sourceStable,
  invalidated: !sourceStable,
  inventory: inventory.counts,
  totalSteps: steps.length,
  passedSteps: steps.filter(step => step.passed).length,
  failedSteps: steps.filter(step => !step.passed).length,
  durationMs: steps.reduce((total, step) => total + step.durationMs, 0),
  steps,
};
fs.writeFileSync(path.join(reportRoot, 'summary.json'), `${JSON.stringify(summary, null, 2)}\n`, 'utf8');

const markdown = [
  '# JK Sistema - Suite completa',
  '',
  `- Commit: **${source.commitSha}**`,
  `- Estado da arvore: **${source.worktreeState}**`,
  `- Fingerprint da arvore: **${source.worktreeFingerprint}**`,
  `- Integridade durante a suite: **${sourceStable ? 'ESTAVEL' : 'INVALIDADA'}**`,
  ...(finalSource ? [
    `- Commit ao final: **${finalSource.commitSha}**`,
    `- Fingerprint ao final: **${finalSource.worktreeFingerprint}**`,
  ] : ['- Proveniencia ao final: **INDISPONIVEL**']),
  `- Arquivos catalogados: **${inventory.counts.total}**`,
  `- Testes automatizados: **${inventory.counts.automated}**`,
  `- Sondas diagnosticas validadas sem chamadas externas: **${inventory.counts.diagnosticProbes}**`,
  `- Etapas aprovadas: **${summary.passedSteps}/${summary.totalSteps}**`,
  '',
  '| Etapa | Resultado | Duracao |',
  '|---|---:|---:|',
  ...steps.map(step => `| ${step.name} | ${step.passed ? 'OK' : 'FALHOU'} | ${(step.durationMs / 1000).toFixed(1)}s |`),
  '',
].join('\n');
fs.writeFileSync(path.join(reportRoot, 'report.md'), markdown, 'utf8');

console.log(`\n[full-suite] ${summary.passedSteps}/${summary.totalSteps} etapas aprovadas; ${inventory.counts.total} arquivos catalogados.`);
if (summary.failedSteps || summary.invalidated) process.exitCode = 1;
