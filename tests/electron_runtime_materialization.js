'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const repoRoot = path.resolve(__dirname, '..');
const generator = require(path.join(repoRoot, 'electron_app', 'scripts', 'local-app-manifest.js'));
const materializerPath = path.join(
  repoRoot,
  'electron_app',
  'main',
  'modules',
  'backend-runtime-materializer.js'
);
const materializer = require(materializerPath);

function write(filePath, content) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, content);
}

function read(filePath) {
  return fs.readFileSync(filePath, 'utf8');
}

function buildSource(root, version = '2.0.0') {
  const source = path.join(root, 'source');
  write(path.join(source, 'package.json'), `${JSON.stringify({ name: 'jk-sistema-desktop', version })}\n`);
  write(path.join(source, 'backend', 'api.py'), 'VERSION = 2\n');
  write(path.join(source, 'static', 'app.js'), 'window.VERSION = 2;\n');
  write(path.join(source, 'backend_api.py'), 'APP_VERSION = "2.0.0"\n');
  write(path.join(source, 'runtime-manifest.json'), `${JSON.stringify({ version })}\n`);
  const modelDir = path.join(source, 'black_jhon_runtime', 'faster-whisper-small');
  const modelPath = path.join(modelDir, 'model.bin');
  write(modelPath, 'modelo-imutavel');
  write(path.join(modelDir, 'model-manifest.json'), `${JSON.stringify({
    model: 'small',
    files: [{
      path: 'model.bin',
      size: fs.statSync(modelPath).size,
      sha256: generator.sha256File(modelPath)
    }]
  }, null, 2)}\n`);
  write(path.join(source, 'python_runtime', 'portable', 'python.exe'), 'payload-nao-materializado');
  write(path.join(source, 'python_wheels', 'wheel.whl'), 'payload-nao-materializado');
  write(path.join(source, 'prerequisites', 'VC_redist.x64.exe'), 'payload-nao-materializado');
  const result = generator.writeLocalAppManifest(source, version);
  assert.strictEqual(result.manifest.version, version);
  assert(!result.manifest.managed_directories.includes('black_jhon_runtime'));
  assert(result.manifest.excluded_payload_roots.includes('black_jhon_runtime'));
  assert(!result.manifest.managed_directories.includes('python_runtime'));
  assert.deepStrictEqual(generator.validateLocalAppManifestAtRoot(source, version), []);
  return source;
}

function seedLegacyTarget(root, name = 'target') {
  const target = path.join(root, name);
  write(path.join(target, 'package.json'), '{"name":"jk-sistema-desktop","version":"1.0.88"}\n');
  write(path.join(target, 'backend', 'api.py'), 'VERSION = 1\n');
  write(path.join(target, 'backend', 'stale.py'), 'STALE = True\n');
  write(path.join(target, 'old_legacy.py'), 'LEGACY = True\n');
  write(path.join(target, 'custom-config.json'), '{"preservar":true}\n');
  write(path.join(target, 'info', '000002', 'ContextVault', '80_Curadoria', 'nota.md'), '# Nota humana\n');
  write(path.join(target, 'info', '000002', 'ContextVault', '.obsidian', 'app.json'), '{"vimMode":false}\n');
  write(path.join(target, 'info', '000002', 'context_hub', 'context_hub.db'), 'db-canario');
  write(path.join(target, 'logs', 'runtime.log'), 'log-canario');
  write(path.join(target, '.venv', '.jk-venv-ready.json'), '{"ready":true}\n');
  write(path.join(target, 'python_runtime', 'canary.txt'), 'payload-antigo-preservado');
  return target;
}

function assertPersistentCanaries(target) {
  assert.strictEqual(read(path.join(target, 'info', '000002', 'ContextVault', '80_Curadoria', 'nota.md')), '# Nota humana\n');
  assert.strictEqual(read(path.join(target, 'info', '000002', 'ContextVault', '.obsidian', 'app.json')), '{"vimMode":false}\n');
  assert.strictEqual(read(path.join(target, 'info', '000002', 'context_hub', 'context_hub.db')), 'db-canario');
  assert.strictEqual(read(path.join(target, 'logs', 'runtime.log')), 'log-canario');
  assert.strictEqual(read(path.join(target, '.venv', '.jk-venv-ready.json')), '{"ready":true}\n');
  assert.strictEqual(read(path.join(target, 'python_runtime', 'canary.txt')), 'payload-antigo-preservado');
  assert.strictEqual(read(path.join(target, 'custom-config.json')), '{"preservar":true}\n');
}

const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'jk-runtime-materialization-'));
try {
  const source = buildSource(tempRoot);
  const target = seedLegacyTarget(tempRoot);

  const payloadInspection = materializer.inspectImmutableRuntimePayloads({
    sourceDir: source,
    targetDir: target
  });
  assert.strictEqual(payloadInspection.required, true);
  const payloadApplied = materializer.ensureImmutableRuntimePayloads({
    sourceDir: source,
    targetDir: target
  });
  assert.strictEqual(payloadApplied.changed, true);

  const inspection = materializer.inspectMaterialization({
    sourceDir: source,
    targetDir: target,
    expectedVersion: '2.0.0'
  });
  assert.strictEqual(inspection.required, true);

  const applied = materializer.materializeLocalApp({
    sourceDir: source,
    targetDir: target,
    expectedVersion: '2.0.0'
  });
  assert.strictEqual(applied.changed, true);
  assert.strictEqual(read(path.join(target, 'backend', 'api.py')), 'VERSION = 2\n');
  assert.strictEqual(JSON.parse(read(path.join(target, 'package.json'))).version, '2.0.0');
  assert(!fs.existsSync(path.join(target, 'backend', 'stale.py')));
  assert(!fs.existsSync(path.join(target, 'old_legacy.py')));
  assert.strictEqual(read(path.join(target, 'black_jhon_runtime', 'faster-whisper-small', 'model.bin')), 'modelo-imutavel');
  assertPersistentCanaries(target);
  assert(fs.existsSync(path.join(target, materializer.JOURNAL_RELATIVE_PATH)));

  const finalized = materializer.finalizeMaterialization(applied);
  assert.strictEqual(finalized.finalized, true);
  assert(!fs.existsSync(path.join(target, materializer.JOURNAL_RELATIVE_PATH)));
  const state = JSON.parse(read(path.join(target, materializer.STATE_RELATIVE_PATH)));
  assert.strictEqual(state.version, '2.0.0');

  const modelPath = path.join(target, 'black_jhon_runtime', 'faster-whisper-small', 'model.bin');
  const modelMtime = fs.statSync(modelPath).mtimeMs;
  const secondPayload = materializer.ensureImmutableRuntimePayloads({
    sourceDir: source,
    targetDir: target
  });
  assert.strictEqual(secondPayload.changed, false);
  const second = materializer.materializeLocalApp({
    sourceDir: source,
    targetDir: target,
    expectedVersion: '2.0.0'
  });
  assert.strictEqual(second.changed, false);
  assert.strictEqual(fs.statSync(modelPath).mtimeMs, modelMtime);
  assert.strictEqual(materializer.inspectMaterialization({
    sourceDir: source,
    targetDir: target,
    expectedVersion: '2.0.0'
  }).required, false);
  assert.strictEqual(materializer.inspectImmutableRuntimePayloads({
    sourceDir: source,
    targetDir: target
  }).required, false);
  assertPersistentCanaries(target);

  const payloadRollbackTarget = seedLegacyTarget(tempRoot, 'payload-rollback-target');
  assert.throws(() => materializer.ensureImmutableRuntimePayloads({
    sourceDir: source,
    targetDir: payloadRollbackTarget,
    faultInjector(phase) {
      if (phase === 'payload_after_install') throw new Error('falha-payload-injetada');
    }
  }), /falha-payload-injetada/);
  assert(!fs.existsSync(path.join(payloadRollbackTarget, 'black_jhon_runtime')));
  assert(!fs.existsSync(path.join(payloadRollbackTarget, materializer.PAYLOAD_JOURNAL_RELATIVE_PATH)));
  assertPersistentCanaries(payloadRollbackTarget);

  const rollbackTarget = seedLegacyTarget(tempRoot, 'rollback-target');
  assert.throws(() => materializer.materializeLocalApp({
    sourceDir: source,
    targetDir: rollbackTarget,
    expectedVersion: '2.0.0',
    faultInjector(phase, details) {
      if (phase === 'after_install' && details.name === 'backend') {
        throw new Error('falha-injetada');
      }
    }
  }), /falha-injetada/);
  assert.strictEqual(read(path.join(rollbackTarget, 'backend', 'api.py')), 'VERSION = 1\n');
  assert.strictEqual(JSON.parse(read(path.join(rollbackTarget, 'package.json'))).version, '1.0.88');
  assert.strictEqual(read(path.join(rollbackTarget, 'backend', 'stale.py')), 'STALE = True\n');
  assert(!fs.existsSync(path.join(rollbackTarget, materializer.JOURNAL_RELATIVE_PATH)));
  assertPersistentCanaries(rollbackTarget);

  const healthFailureTarget = seedLegacyTarget(tempRoot, 'health-failure-target');
  const pendingHealth = materializer.materializeLocalApp({
    sourceDir: source,
    targetDir: healthFailureTarget,
    expectedVersion: '2.0.0'
  });
  assert.strictEqual(read(path.join(healthFailureTarget, 'backend', 'api.py')), 'VERSION = 2\n');
  const healthRollback = materializer.rollbackMaterialization(pendingHealth);
  assert.strictEqual(healthRollback.rolledBack, true);
  assert.strictEqual(read(path.join(healthFailureTarget, 'backend', 'api.py')), 'VERSION = 1\n');
  assert.strictEqual(JSON.parse(read(path.join(healthFailureTarget, 'package.json'))).version, '1.0.88');
  assert.strictEqual(read(path.join(healthFailureTarget, 'backend', 'stale.py')), 'STALE = True\n');
  assert(!fs.existsSync(path.join(healthFailureTarget, materializer.STATE_RELATIVE_PATH)));
  assertPersistentCanaries(healthFailureTarget);

  const crashTarget = seedLegacyTarget(tempRoot, 'crash-target');
  const childCode = [
    `const m = require(${JSON.stringify(materializerPath)});`,
    'm.materializeLocalApp({',
    '  sourceDir: process.env.JK_TEST_SOURCE,',
    '  targetDir: process.env.JK_TEST_TARGET,',
    "  expectedVersion: '2.0.0',",
    "  faultInjector(phase, details) { if (phase === 'after_install' && details.name === 'backend') process.exit(91); }",
    '});'
  ].join('\n');
  const crashed = spawnSync(process.execPath, ['-e', childCode], {
    cwd: repoRoot,
    env: { ...process.env, JK_TEST_SOURCE: source, JK_TEST_TARGET: crashTarget },
    encoding: 'utf8'
  });
  assert.strictEqual(crashed.status, 91, crashed.stderr || crashed.stdout);
  assert(fs.existsSync(path.join(crashTarget, materializer.JOURNAL_RELATIVE_PATH)));
  const recovered = materializer.recoverInterruptedMaterialization(crashTarget);
  assert.strictEqual(recovered.rolledBack, true);
  assert.strictEqual(read(path.join(crashTarget, 'backend', 'api.py')), 'VERSION = 1\n');
  assert.strictEqual(read(path.join(crashTarget, 'backend', 'stale.py')), 'STALE = True\n');
  assertPersistentCanaries(crashTarget);

  write(path.join(source, 'backend', 'api.py'), 'VERSION = 3\n');
  generator.writeLocalAppManifest(source, '2.0.0');
  const payloadBeforeCodeOnlyUpdate = fs.statSync(modelPath).mtimeMs;
  assert.strictEqual(materializer.ensureImmutableRuntimePayloads({
    sourceDir: source,
    targetDir: target
  }).changed, false);
  const codeOnlyUpdate = materializer.materializeLocalApp({
    sourceDir: source,
    targetDir: target,
    expectedVersion: '2.0.0'
  });
  assert.strictEqual(codeOnlyUpdate.changed, true);
  materializer.finalizeMaterialization(codeOnlyUpdate);
  assert.strictEqual(read(path.join(target, 'backend', 'api.py')), 'VERSION = 3\n');
  assert.strictEqual(fs.statSync(modelPath).mtimeMs, payloadBeforeCodeOnlyUpdate);

  const unsafeSource = path.join(tempRoot, 'unsafe-source');
  fs.cpSync(source, unsafeSource, { recursive: true });
  const unsafeManifestPath = path.join(unsafeSource, generator.MANIFEST_NAME);
  const unsafeManifest = JSON.parse(read(unsafeManifestPath));
  unsafeManifest.files[0].path = '../escape.py';
  write(unsafeManifestPath, `${JSON.stringify(unsafeManifest, null, 2)}\n`);
  assert.throws(() => materializer.inspectMaterialization({
    sourceDir: unsafeSource,
    targetDir: path.join(tempRoot, 'unsafe-target'),
    expectedVersion: '2.0.0'
  }), /Caminho invalido|fora da raiz/);

  const linkedInfoTarget = path.join(tempRoot, 'linked-info-target');
  const linkedInfoOutside = path.join(tempRoot, 'linked-info-outside');
  fs.mkdirSync(linkedInfoTarget);
  fs.mkdirSync(linkedInfoOutside);
  fs.mkdirSync(path.join(linkedInfoTarget, 'logs'));
  fs.symlinkSync(linkedInfoOutside, path.join(linkedInfoTarget, 'info'), process.platform === 'win32' ? 'junction' : 'dir');
  assert.throws(() => materializer.materializeLocalApp({
    sourceDir: source,
    targetDir: linkedInfoTarget,
    expectedVersion: '2.0.0'
  }), /link ou junction/);
  assert.throws(() => materializer.ensureImmutableRuntimePayloads({
    sourceDir: source,
    targetDir: linkedInfoTarget
  }), /link ou junction/);
  assert(!fs.existsSync(path.join(linkedInfoOutside, 'runtime-materialization-state.json')));
  assert(!fs.existsSync(path.join(linkedInfoOutside, 'runtime-materialization-journal.json')));
  assert(!fs.existsSync(path.join(linkedInfoOutside, 'runtime-materialization.lock')));

  const linkedLogsTarget = path.join(tempRoot, 'linked-logs-target');
  const linkedLogsOutside = path.join(tempRoot, 'linked-logs-outside');
  fs.mkdirSync(linkedLogsTarget);
  fs.mkdirSync(linkedLogsOutside);
  fs.mkdirSync(path.join(linkedLogsTarget, 'info'));
  fs.symlinkSync(linkedLogsOutside, path.join(linkedLogsTarget, 'logs'), process.platform === 'win32' ? 'junction' : 'dir');
  assert.throws(() => materializer.inspectMaterialization({
    sourceDir: source,
    targetDir: linkedLogsTarget,
    expectedVersion: '2.0.0'
  }), /link ou junction/);
  assert(!fs.existsSync(path.join(linkedLogsOutside, 'runtime-materialization-state.json')));

  console.log('electron runtime materialization: OK');
} finally {
  const resolvedTemp = path.resolve(tempRoot);
  const resolvedSystemTemp = path.resolve(os.tmpdir());
  assert(resolvedTemp.startsWith(`${resolvedSystemTemp}${path.sep}`));
  fs.rmSync(resolvedTemp, { recursive: true, force: true });
}
