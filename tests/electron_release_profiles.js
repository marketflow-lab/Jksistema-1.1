'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const appRoot = path.join(root, 'electron_app');
const contractTools = require(path.join(appRoot, 'scripts', 'installer-runtime-contract.js'));
const updateConfig = require(path.join(appRoot, 'scripts', 'electron-builder-update-config.js'));
const pkg = JSON.parse(fs.readFileSync(path.join(appRoot, 'package.json'), 'utf8'));
const contract = JSON.parse(fs.readFileSync(path.join(root, contractTools.LOCK_NAME), 'utf8'));

assert.deepStrictEqual(contractTools.validateRuntimeContract(contract), []);
assert.strictEqual(contractTools.calculateRuntimeId(contract), contract.runtime_id);
const changed = JSON.parse(JSON.stringify(contract));
changed.whisper.manifest_sha256 = 'f'.repeat(64);
assert.notStrictEqual(contractTools.calculateRuntimeId(changed), contract.runtime_id);

assert.strictEqual(updateConfig.appId, pkg.build.appId);
assert.strictEqual(updateConfig.productName, pkg.build.productName);
assert.strictEqual(updateConfig.directories.output, 'dist-client-update');
assert(updateConfig.win.artifactName.includes('-Update-'));
const updateLocalApp = updateConfig.extraResources.find(entry => String(entry.to).replace(/\\/g, '/') === 'local_app');
assert(updateLocalApp, 'local_app ausente do perfil update');
for (const required of [
  '!python_runtime/**',
  '!python_wheels/**',
  '!black_jhon_runtime/**',
  '!prerequisites/**',
]) {
  assert(updateLocalApp.filter.includes(required), `exclusao ausente: ${required}`);
}
for (const entry of updateConfig.extraResources) {
  const from = String(entry.from || '').replace(/\\/g, '/');
  assert(!from.includes('.installer_runtime/black_jhon'));
  assert(!from.includes('.installer_runtime/prerequisites'));
}

assert(pkg.scripts['dist:full'].includes('build-installer-profile.js full'));
assert(pkg.scripts['dist:update'].includes('build-installer-profile.js update'));
assert(pkg.scripts['dist:publish'].includes('direct-publish-disabled.js'));
assert(!pkg.scripts['dist:full'].includes('--publish'));
assert(!pkg.scripts['dist:update'].includes('--publish'));

const workflow = fs.readFileSync(path.join(root, '.github', 'workflows', 'desktop-release.yml'), 'utf8');
const stagingScript = fs.readFileSync(path.join(root, 'scripts', 'stage_desktop_release_artifacts.ps1'), 'utf8');
assert(/workflow_dispatch:/.test(workflow));
assert(!/^\s+push:/m.test(workflow));
assert(/if: inputs\.publish/.test(workflow));
assert(/release_mode == 'app-update'/.test(workflow));
assert(/^\s+- runtime-update$/m.test(workflow));
assert(/environment: desktop-release/.test(workflow));
assert(/Build complete offline installer\s+run: npm\.cmd --prefix electron_app run dist:full/.test(workflow));
assert(/Build lightweight application update\s+if: inputs\.release_mode == 'app-update'\s+run: npm\.cmd --prefix electron_app run dist:update/.test(workflow));
assert(/stage_desktop_release_artifacts\.ps1 -Mode/.test(workflow));
assert(/electron_release_artifact_staging\.ps1/.test(workflow));
assert(/JK-Sistema-Cliente-Setup-\$\(\$Version\)\.exe/.test(stagingScript));
assert(/JK-Sistema-Cliente-Update-\$\(\$Version\)\.exe/.test(stagingScript));
assert(/Assert-ProfileArtifacts -Source \$SetupSource -Executable \$setupExe/.test(stagingScript));
assert(/Assert-ProfileArtifacts -Source \$UpdateSource -Executable \$updateExe/.test(stagingScript));
assert(/\$latestSource = Join-Path \$SetupSource ["']latest\.yml["']/.test(stagingScript));
assert(/\$latestSource = Join-Path \$UpdateSource ["']latest\.yml["']/.test(stagingScript));
assert(/latest\.yml do app-update aponta indevidamente para o Setup completo/.test(stagingScript));

console.log('electron release profiles: OK');
