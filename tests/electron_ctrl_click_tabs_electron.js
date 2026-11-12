'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const repoRoot = path.resolve(__dirname, '..');
const electronPath = require(require.resolve('electron', { paths: [path.join(repoRoot, 'electron_app'), repoRoot] }));
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'jk-ctrl-click-electron-'));
const env = { ...process.env, ELECTRON_DISABLE_SECURITY_WARNINGS: 'true' };
delete env.ELECTRON_RUN_AS_NODE;
try {
    const result = spawnSync(electronPath, [path.join(__dirname, 'helpers/electron_ctrl_click_tabs_electron_harness.js'), repoRoot, temporary], {
        cwd: repoRoot, env, encoding: 'utf8', timeout: 90_000, windowsHide: true, maxBuffer: 4 * 1024 * 1024,
    });
    if (result.stdout) process.stdout.write(result.stdout);
    if (result.stderr) process.stderr.write(result.stderr);
    assert.ifError(result.error);
    assert.strictEqual(result.status, 0, 'Real Electron Ctrl+click regression failed');
    assert.match(result.stdout, /ELECTRON_CTRL_CLICK_TABS_OK/);
} finally {
    const resolved = path.resolve(temporary);
    assert(resolved.startsWith(path.resolve(os.tmpdir()) + path.sep) && path.basename(resolved).startsWith('jk-ctrl-click-electron-'));
    fs.rmSync(resolved, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}
