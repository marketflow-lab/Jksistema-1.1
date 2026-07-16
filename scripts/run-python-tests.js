'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const root = path.resolve(__dirname, '..');
const resultRoot = path.join(root, 'test-results', 'python');
const python = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
const withCoverage = process.argv.includes('--coverage');
const baseTemp = path.join(resultRoot, `tmp-${process.pid}-${Date.now()}`);

fs.mkdirSync(resultRoot, { recursive: true });
const pytestArgs = [
  '-m', 'pytest', 'tests',
  `--basetemp=${baseTemp}`,
  '-p', 'no:cacheprovider',
  '--junitxml=test-results/python/pytest.xml',
];
const args = withCoverage
  ? ['-m', 'coverage', 'run', ...pytestArgs]
  : pytestArgs;

let result;
try {
  result = spawnSync(python, args, { cwd: root, env: process.env, stdio: 'inherit', timeout: 900_000 });
} finally {
  const resolved = path.resolve(baseTemp);
  const allowedRoot = `${path.resolve(resultRoot)}${path.sep}`;
  if (resolved.startsWith(allowedRoot)) {
    try {
      fs.rmSync(resolved, { recursive: true, force: true });
    } catch (error) {
      console.warn(`[python-tests] nao foi possivel remover ${resolved}: ${error.message}`);
    }
  }
}

if (result.error) {
  console.error(result.error);
  process.exitCode = 1;
} else {
  process.exitCode = result.status === 0 ? 0 : 1;
}
