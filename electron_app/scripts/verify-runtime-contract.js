'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { loadRuntimeContract } = require('./installer-runtime-contract');

const repoRoot = path.resolve(__dirname, '..', '..');

function readJson(candidate) {
  return JSON.parse(fs.readFileSync(candidate, 'utf8').replace(/^\uFEFF/, ''));
}

function sha256File(candidate) {
  return crypto.createHash('sha256').update(fs.readFileSync(candidate)).digest('hex');
}

const failures = [];
let contract;
try {
  ({ contract } = loadRuntimeContract(repoRoot));
} catch (err) {
  failures.push(err && err.message ? err.message : String(err));
}

if (contract) {
  const versions = readJson(path.join(repoRoot, 'runtime-versions.json'));
  const expectedPython = versions.python || {};
  const expectedPortable = expectedPython.windowsPortable || {};
  const python = contract.python || {};
  const portable = python.portable || {};
  const comparisons = [
    ['python.version', python.version, expectedPython.version],
    ['python.abi', python.abi, expectedPython.abi],
    ['python.installer.path', python.installer?.path, expectedPython.windowsInstaller],
    ['python.installer.size', Number(python.installer?.size), Number(expectedPython.windowsInstallerSize)],
    ['python.installer.sha256', python.installer?.sha256, expectedPython.windowsInstallerSha256],
    ['python.portable.path', portable.path, expectedPortable.path],
    ['python.portable.file_count', Number(portable.file_count), Number(expectedPortable.fileCount)],
    ['python.portable.total_size', Number(portable.total_size), Number(expectedPortable.totalSize)],
    ['python.portable.tree_sha256', portable.tree_sha256, expectedPortable.treeSha256],
  ];
  for (const [label, actual, expected] of comparisons) {
    if (String(actual) !== String(expected)) failures.push(`${label} diverge de runtime-versions.json`);
  }
  const requirementsHash = sha256File(path.join(repoRoot, 'requirements.txt'));
  if (requirementsHash !== String(contract.wheelhouse?.requirements_sha256 || '').toLowerCase()) {
    failures.push('requirements.txt diverge do contrato de runtime; gere um novo runtime completo');
  }
}

if (failures.length) {
  process.stderr.write(`Contrato de runtime reprovado:\n- ${failures.join('\n- ')}\n`);
  process.exit(1);
}
process.stdout.write(`Contrato de runtime OK: ${contract.runtime_id}\n`);
