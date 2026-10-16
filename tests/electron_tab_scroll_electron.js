'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const repoRoot = path.resolve(__dirname, '..');
const electronModule = require.resolve('electron', { paths: [path.join(repoRoot, 'electron_app'), repoRoot] });
const electronPath = require(electronModule);
const harnessPath = path.join(__dirname, 'helpers', 'electron_tab_scroll_electron_harness.js');
const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'jk-tab-scroll-electron-'));
const env = { ...process.env, ELECTRON_DISABLE_SECURITY_WARNINGS: 'true' };
delete env.ELECTRON_RUN_AS_NODE;

try {
    let sourceFile = path.join(repoRoot, 'electron_shell.html');
    // Optional diagnostic: prove the regression against HEAD without changing the checkout.
    if (process.argv.includes('--baseline')) {
        const original = spawnSync('git', ['show', 'HEAD:electron_shell.html'], { cwd: repoRoot, encoding: 'utf8', windowsHide: true });
        assert.ifError(original.error);
        assert.strictEqual(original.status, 0, 'baseline shell must be readable from HEAD');
        sourceFile = path.join(tempRoot, 'baseline-shell.html');
        fs.writeFileSync(sourceFile, original.stdout, 'utf8');
    }
    const result = spawnSync(electronPath, [harnessPath, repoRoot, tempRoot, sourceFile], {
        cwd: repoRoot,
        env,
        encoding: 'utf8',
        timeout: 90_000,
        windowsHide: true,
        maxBuffer: 4 * 1024 * 1024,
    });
    if (result.stdout) process.stdout.write(result.stdout);
    if (result.stderr) process.stderr.write(result.stderr);
    assert.ifError(result.error);
    assert.strictEqual(result.status, 0, `scroll harness failed with code ${result.status}`);
    assert.match(result.stdout || '', /ELECTRON_TAB_SCROLL_OK/);
} finally {
    const resolved = path.resolve(tempRoot);
    const allowedRoot = path.resolve(os.tmpdir());
    assert(resolved.startsWith(allowedRoot + path.sep) && path.basename(resolved).startsWith('jk-tab-scroll-electron-'));
    fs.rmSync(resolved, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}
