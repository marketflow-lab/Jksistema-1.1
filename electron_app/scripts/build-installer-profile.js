'use strict';

const path = require('path');
const { spawnSync } = require('child_process');

const profile = String(process.argv[2] || '').trim().toLowerCase();
if (!['full', 'update'].includes(profile)) {
  process.stderr.write('Uso: node scripts/build-installer-profile.js <full|update>\n');
  process.exit(2);
}

const appRoot = path.resolve(__dirname, '..');
const builderCli = require.resolve('electron-builder/out/cli/cli.js', { paths: [appRoot] });
const args = ['--publish', 'never'];
if (profile === 'update') {
  args.push('--config', path.join('scripts', 'electron-builder-update-config.js'));
}
const result = spawnSync(process.execPath, [builderCli, ...args], {
  cwd: appRoot,
  env: { ...process.env, JK_INSTALLER_PACKAGE_PROFILE: profile },
  stdio: 'inherit',
  windowsHide: false,
  shell: false,
});
if (result.error) throw result.error;
process.exit(Number.isInteger(result.status) ? result.status : 1);
