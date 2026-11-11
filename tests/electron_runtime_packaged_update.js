'use strict';

const assert = require('assert');
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');

const repoRoot = path.resolve(__dirname, '..');
const materializer = require(path.join(
  repoRoot,
  'electron_app',
  'main',
  'modules',
  'backend-runtime-materializer.js'
));
const manifestGenerator = require(path.join(repoRoot, 'electron_app', 'scripts', 'local-app-manifest.js'));

function write(candidate, value) {
  fs.mkdirSync(path.dirname(candidate), { recursive: true });
  fs.writeFileSync(candidate, value);
}

function sha256(candidate) {
  return crypto.createHash('sha256').update(fs.readFileSync(candidate)).digest('hex');
}

function readJson(candidate) {
  return JSON.parse(fs.readFileSync(candidate, 'utf8').replace(/^\uFEFF/, ''));
}

function buildPackageFixture(root) {
  const source = path.join(root, 'package-fixture');
  const version = readJson(path.join(repoRoot, 'package.json')).version;
  for (const relative of ['package.json', 'backend_api.py', 'backend/__init__.py', 'static/importacoes.html']) {
    write(path.join(source, relative), fs.readFileSync(path.join(repoRoot, relative)));
  }
  write(path.join(source, 'runtime-manifest.json'), `${JSON.stringify({ version })}\n`);
  const modelRoot = path.join(source, 'black_jhon_runtime', 'faster-whisper-small');
  const modelRelative = 'models--fixture/snapshots/fixture/model.bin';
  const modelPath = path.join(modelRoot, modelRelative);
  write(modelPath, 'immutable-packaged-update-test-model');
  write(path.join(modelRoot, 'model-manifest.json'), `${JSON.stringify({
    model: 'small',
    files: [{
      path: modelRelative,
      size: fs.statSync(modelPath).size,
      sha256: sha256(modelPath),
    }],
  })}\n`);
  manifestGenerator.writeLocalAppManifest(source, version);
  assert.deepStrictEqual(manifestGenerator.validateLocalAppManifestAtRoot(source, version), []);
  return source;
}

let sourceDir = path.resolve(
  process.argv[2] || path.join(repoRoot, 'electron_app', 'dist-client-setup', 'win-unpacked', 'resources', 'local_app')
);

const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'jk-packaged-update-'));
const targetDir = path.join(tempRoot, 'local_app');
const canaries = [
  ['info/000002/ContextVault/80_Curadoria/Notas/canario.md', '# Nota humana 1.0.99\n'],
  ['info/000002/ContextVault/.obsidian/app.json', '{"vimMode":false}\n'],
  ['info/000002/context_hub/context_hub.db', 'BANCO-CANARIO-1.0.99'],
  ['info/000002/SKU/001.json', '{"sku":"001","canary":true}\n'],
  ['logs/runtime.log', 'LOG-CANARIO-1.0.99\n'],
  ['backups/snapshot.bin', Buffer.from([0, 1, 2, 3, 254, 255])],
  ['.venv/.jk-venv-ready.json', '{"ready":true}\n'],
];

try {
  // Source-only CI uses a disposable package. An explicit artifact must exist.
  if (!process.argv[2] && !fs.existsSync(path.join(sourceDir, 'local-app-manifest.json'))) {
    sourceDir = buildPackageFixture(tempRoot);
  }
  assert(fs.existsSync(path.join(sourceDir, 'local-app-manifest.json')), 'pacote da release nao construido');
  const sourceManifest = readJson(path.join(sourceDir, 'local-app-manifest.json'));
  const releaseVersion = String(sourceManifest.version || '');
  assert(releaseVersion, 'versao ausente no local-app-manifest');

  write(path.join(targetDir, 'runtime-manifest.json'), '{"version":"1.0.99"}\n');
  write(path.join(targetDir, 'package.json'), '{"name":"jk-sistema-desktop","version":"1.0.88"}\n');
  write(path.join(targetDir, 'backend', 'stale.py'), 'VERSION = "1.0.99"\n');
  for (const [relative, value] of canaries) write(path.join(targetDir, relative), value);
  const before = new Map(canaries.map(([relative]) => [relative, sha256(path.join(targetDir, relative))]));

  const payload = materializer.ensureImmutableRuntimePayloads({ sourceDir, targetDir });
  assert.strictEqual(payload.changed, true);
  const applied = materializer.materializeLocalApp({
    sourceDir,
    targetDir,
    expectedVersion: releaseVersion,
  });
  assert.strictEqual(applied.changed, true);
  assert.strictEqual(readJson(path.join(targetDir, 'runtime-manifest.json')).version, releaseVersion);
  assert.strictEqual(readJson(path.join(targetDir, 'package.json')).version, releaseVersion);
  assert(!fs.existsSync(path.join(targetDir, 'backend', 'stale.py')));
  for (const [relative, expectedHash] of before) {
    assert.strictEqual(sha256(path.join(targetDir, relative)), expectedHash, `canario alterado: ${relative}`);
  }
  materializer.finalizeMaterialization(applied);

  const modelRoot = path.join(targetDir, 'black_jhon_runtime', 'faster-whisper-small');
  const modelManifest = readJson(path.join(modelRoot, 'model-manifest.json'));
  const modelEntry = modelManifest.files.find(item => String(item.path || '').replace(/\\/g, '/').endsWith('/model.bin'));
  assert(modelEntry, 'model.bin ausente do manifesto imutavel');
  const modelPath = path.join(modelRoot, ...String(modelEntry.path).split('/'));
  const modelMtime = fs.statSync(modelPath).mtimeMs;
  assert.strictEqual(materializer.ensureImmutableRuntimePayloads({ sourceDir, targetDir }).changed, false);
  assert.strictEqual(materializer.materializeLocalApp({
    sourceDir,
    targetDir,
    expectedVersion: releaseVersion,
  }).changed, false);
  assert.strictEqual(fs.statSync(modelPath).mtimeMs, modelMtime);
  for (const [relative, expectedHash] of before) {
    assert.strictEqual(sha256(path.join(targetDir, relative)), expectedHash, `canario alterado no segundo boot: ${relative}`);
  }
  console.log(`electron packaged update 1.0.99 -> ${releaseVersion}: OK`);
} finally {
  const resolved = path.resolve(tempRoot);
  const systemTemp = path.resolve(os.tmpdir());
  assert(resolved.startsWith(`${systemTemp}${path.sep}`));
  fs.rmSync(resolved, { recursive: true, force: true });
}
