'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const repoRoot = path.resolve(__dirname, '..');
const electronPath = require('electron');
const harnessPath = path.join(__dirname, 'helpers', 'favoritos_embedded_browser_close_electron_harness.js');
const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'jk-favoritos-browser-close-'));
const env = {
  ...process.env,
  JK_APP_ROOT_DIR: repoRoot,
  JK_LOCAL_BACKEND_SOURCE_DIR: repoRoot,
  JK_ELECTRON_USER_DATA_DIR: userDataDir,
  JK_ALLOW_MULTIPLE_INSTANCES_FOR_TESTS: '1',
  ELECTRON_DISABLE_SECURITY_WARNINGS: 'true',
};
delete env.ELECTRON_RUN_AS_NODE;

try {
  const result = spawnSync(electronPath, [harnessPath], {
    cwd: repoRoot,
    env,
    encoding: 'utf8',
    timeout: 180_000,
    windowsHide: true,
    maxBuffer: 16 * 1024 * 1024,
  });
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  assert.ifError(result.error);
  assert.strictEqual(result.status, 0, `harness Electron encerrou com codigo ${result.status}`);
  assert.match(result.stdout || '', /FAVORITOS_EMBEDDED_BROWSER_CLOSE_ELECTRON_OK/);
} finally {
  if (process.env.JK_KEEP_ELECTRON_TEST_DATA !== '1') {
    const resolved = path.resolve(userDataDir);
    const tempRoot = path.resolve(os.tmpdir());
    if (resolved.startsWith(`${tempRoot}${path.sep}`) && path.basename(resolved).startsWith('jk-favoritos-browser-close-')) {
      fs.rmSync(resolved, { recursive: true, force: true });
    }
  }
}
