'use strict';

const path = require('path');
const { spawnSync } = require('child_process');
const { sanitizeLocalAppStaging, writeLocalAppManifest } = require('./local-app-manifest');

module.exports = async function afterPackLocalAppManifest(context) {
  const resourcesDir = path.join(context.appOutDir, 'resources');
  const localAppDir = path.join(resourcesDir, 'local_app');
  const version = String(context.packager && context.packager.appInfo && context.packager.appInfo.version || '').trim();
  const sanitation = sanitizeLocalAppStaging(localAppDir);
  const result = writeLocalAppManifest(localAppDir, version);
  process.stdout.write(
    `[local-app-manifest] ${result.manifest.file_count} arquivos gerenciados; versao ${result.manifest.version}; `
      + `${sanitation.removedFiles} bytecode(s) e ${sanitation.removedDirectories} cache(s) removidos do staging.\n`
  );
  const verifier = path.join(__dirname, 'verify-installer-package.js');
  const verification = spawnSync(
    process.execPath,
    [verifier, '--packaged', resourcesDir],
    {
      cwd: path.resolve(__dirname, '..'),
      encoding: 'utf8',
      env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' },
      windowsHide: true,
    },
  );
  if (verification.stdout) process.stdout.write(verification.stdout);
  if (verification.status !== 0) {
    const detail = String(verification.stderr || verification.error || 'gate empacotado falhou').trim();
    throw new Error(`Gate do pacote falhou no afterPack:\n${detail.slice(-16000)}`);
  }
};
