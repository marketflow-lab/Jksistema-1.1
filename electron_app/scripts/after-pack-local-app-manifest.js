'use strict';

const path = require('path');
const fs = require('fs');
const {
  sanitizeLocalAppStaging,
  validateLocalAppManifestAtRoot,
  writeLocalAppManifest,
} = require('./local-app-manifest');

module.exports = async function afterPackLocalAppManifest(context) {
  const resourcesDir = path.join(context.appOutDir, 'resources');
  const localAppDir = path.join(resourcesDir, 'local_app');
  const version = String(context.packager && context.packager.appInfo && context.packager.appInfo.version || '').trim();
  const requestedProfile = String(process.env.JK_INSTALLER_PACKAGE_PROFILE || 'full').trim().toLowerCase();
  const profile = requestedProfile === 'update' ? 'update' : 'full';
  fs.writeFileSync(
    path.join(localAppDir, 'installer-package-profile.json'),
    `${JSON.stringify({ schema_version: 1, profile, app_version: version }, null, 2)}\n`,
    'utf8',
  );
  const sanitation = sanitizeLocalAppStaging(localAppDir);
  const result = writeLocalAppManifest(localAppDir, version);
  process.stdout.write(
    `[local-app-manifest] ${result.manifest.file_count} arquivos gerenciados; versao ${result.manifest.version}; `
      + `${sanitation.removedFiles} bytecode(s) e ${sanitation.removedDirectories} cache(s) removidos do staging.\n`
  );
  const failures = validateLocalAppManifestAtRoot(localAppDir, version);
  if (failures.length) {
    throw new Error(`Manifesto local_app falhou no afterPack:\n- ${failures.join('\n- ')}`);
  }
};
