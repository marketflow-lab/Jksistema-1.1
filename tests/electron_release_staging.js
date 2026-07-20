'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const manifestTools = require(path.join(
  __dirname,
  '..',
  'electron_app',
  'scripts',
  'local-app-manifest.js'
));

function write(candidate, value = 'test') {
  fs.mkdirSync(path.dirname(candidate), { recursive: true });
  fs.writeFileSync(candidate, value);
}

const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'jk-release-staging-'));
const localApp = path.join(tempRoot, 'resources', 'local_app');

try {
  write(path.join(localApp, 'package.json'), '{"version":"1.0.105"}\n');
  write(path.join(localApp, 'backend', 'api.py'), 'VERSION = 1\n');
  write(path.join(localApp, 'backend', '__pycache__', 'api.pyc'));
  write(path.join(localApp, 'backend', 'orphan.pyo'));
  write(path.join(localApp, 'python_runtime', 'portable', 'Lib', '__pycache__', 'os.pyc'));
  write(path.join(localApp, 'info', 'must-not-be-packaged.json'), '{}\n');

  const sanitation = manifestTools.sanitizeLocalAppStaging(localApp);
  assert.strictEqual(sanitation.removedFiles, 3);
  assert.strictEqual(sanitation.removedDirectories, 2);
  assert(!fs.existsSync(path.join(localApp, 'backend', '__pycache__')));
  assert(!fs.existsSync(path.join(localApp, 'backend', 'orphan.pyo')));
  assert(!fs.existsSync(path.join(localApp, 'python_runtime', 'portable', 'Lib', '__pycache__')));
  assert(fs.existsSync(path.join(localApp, 'info', 'must-not-be-packaged.json')));
  assert.throws(
    () => manifestTools.buildLocalAppManifest(localApp, '1.0.105'),
    /persistente ou sensivel/,
  );

  fs.rmSync(path.join(localApp, 'info'), { recursive: true, force: true });
  const written = manifestTools.writeLocalAppManifest(localApp, '1.0.105');
  assert.strictEqual(written.manifest.version, '1.0.105');
  assert.deepStrictEqual(
    manifestTools.validateLocalAppManifestAtRoot(localApp, '1.0.105'),
    [],
  );
  console.log('electron release staging: OK');
} finally {
  const resolved = path.resolve(tempRoot);
  const tempBase = path.resolve(os.tmpdir());
  assert(resolved.startsWith(`${tempBase}${path.sep}`));
  fs.rmSync(resolved, { recursive: true, force: true });
}
