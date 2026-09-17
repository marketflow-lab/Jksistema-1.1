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
assert(/workflow_dispatch:/.test(workflow));
assert(!/^\s+push:/m.test(workflow));
assert(/if: inputs\.publish/.test(workflow));
assert(/release_mode == 'app-update'/.test(workflow));
assert(/release_mode == 'runtime-update'/.test(workflow));
assert(/environment: desktop-release/.test(workflow));

console.log('electron release profiles: OK');
