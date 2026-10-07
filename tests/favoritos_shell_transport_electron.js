'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const repoRoot = path.resolve(__dirname, '..');
const harnessPath = path.join(__dirname, 'helpers', 'favoritos_shell_transport_electron_harness.js');
const testDir = fs.mkdtempSync(path.join(os.tmpdir(), 'jk-favoritos-shell-transport-'));
const env = {
  ...process.env,
  JK_FAVORITOS_TRANSPORT_TEST_DIR: testDir,
  ELECTRON_DISABLE_SECURITY_WARNINGS: 'true',
};
delete env.ELECTRON_RUN_AS_NODE;

try {
  const result = spawnSync(require('electron'), ['--disable-gpu', '--no-sandbox', harnessPath], {
    cwd: repoRoot,
    env,
    encoding: 'utf8',
    timeout: 45_000,
    windowsHide: true,
    maxBuffer: 4 * 1024 * 1024,
  });
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  assert.ifError(result.error);
  assert.strictEqual(result.status, 0, 'O transporte real do Favoritos deve aceitar a resposta do shell file://.');
  assert.match(result.stdout || '', /FAVORITOS_SHELL_TRANSPORT_ELECTRON_OK/);
} finally {
  const resolved = path.resolve(testDir);
  const tempRoot = path.resolve(os.tmpdir());
  if (resolved.startsWith(`${tempRoot}${path.sep}`)
      && path.basename(resolved).startsWith('jk-favoritos-shell-transport-')) {
    fs.rmSync(resolved, { recursive: true, force: true });
  }
}
