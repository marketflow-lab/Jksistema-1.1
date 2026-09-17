'use strict';

const pkg = require('../package.json');

const build = JSON.parse(JSON.stringify(pkg.build || {}));
build.directories = { ...(build.directories || {}), output: 'dist-client-update' };
build.win = {
  ...(build.win || {}),
  artifactName: 'JK-Sistema-Cliente-Update-${version}.${ext}',
};

build.extraResources = (build.extraResources || [])
  .filter(entry => {
    const from = String(entry && entry.from || '').replace(/\\/g, '/');
    return ![
      '../.installer_runtime/black_jhon',
      '../.installer_runtime/prerequisites',
      '../.installer_runtime/runtime-manifest.json',
    ].includes(from);
  })
  .map(entry => {
    if (String(entry && entry.to || '').replace(/\\/g, '/') !== 'local_app') return entry;
    return {
      ...entry,
      filter: [
        ...(Array.isArray(entry.filter) ? entry.filter : []),
        '!python_runtime/**',
        '!python_wheels/**',
        '!black_jhon_runtime/**',
        '!prerequisites/**',
      ],
    };
  });

module.exports = build;
