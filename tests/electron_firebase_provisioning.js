'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const vm = require('vm');

const repoRoot = path.resolve(__dirname, '..');
const backendSource = fs.readFileSync(
  path.join(repoRoot, 'electron_app', 'main', 'modules', 'backend.js'),
  'utf8'
);
const verifierSource = fs.readFileSync(
  path.join(repoRoot, 'electron_app', 'scripts', 'verify-installer-package.js'),
  'utf8'
);
const installerManifest = JSON.parse(fs.readFileSync(
  path.join(repoRoot, 'electron_app', 'installer-required-resources.json'),
  'utf8'
));
const manifestGenerator = require(path.join(
  repoRoot,
  'electron_app',
  'scripts',
  'local-app-manifest.js'
));
const materializer = require(path.join(
  repoRoot,
  'electron_app',
  'main',
  'modules',
  'backend-runtime-materializer.js'
));

const SYNTHETIC_SERVICE_ACCOUNT = Object.freeze({
  type: 'service_account',
  client_email: 'synthetic-client-sentinel',
  private_key: 'SYNTHETIC_PRIVATE_KEY_SENTINEL',
  project_id: 'synthetic-project-sentinel'
});
const SYNTHETIC_SERVICE_ACCOUNT_TEXT = `${JSON.stringify(SYNTHETIC_SERVICE_ACCOUNT, null, 2)}\n`;

function write(filePath, content) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, content, 'utf8');
}

function readJsonFile(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch (_err) {
    return null;
  }
}

function extractFunction(source, name, context = {}) {
  const marker = `function ${name}`;
  const start = source.indexOf(marker);
  assert.ok(start >= 0, `funcao ${name} ausente`);
  const paramsOpen = source.indexOf('(', start);
  let paramsDepth = 0;
  let paramsClose = -1;
  for (let index = paramsOpen; index < source.length; index += 1) {
    const char = source[index];
    if (char === '(') paramsDepth += 1;
    if (char === ')') {
      paramsDepth -= 1;
      if (paramsDepth === 0) {
        paramsClose = index;
        break;
      }
    }
  }
  assert.ok(paramsClose > paramsOpen, `parametros de ${name} invalidos`);
  const open = source.indexOf('{', paramsClose);
  assert.ok(open >= 0, `corpo de ${name} ausente`);
  let depth = 0;
  let quote = '';
  let escaped = false;
  let end = -1;
  for (let index = open; index < source.length; index += 1) {
    const char = source[index];
    if (quote) {
      if (escaped) escaped = false;
      else if (char === '\\') escaped = true;
      else if (char === quote) quote = '';
      continue;
    }
    if (char === '"' || char === "'" || char === '`') {
      quote = char;
      continue;
    }
    if (char === '{') depth += 1;
    if (char === '}') {
      depth -= 1;
      if (depth === 0) {
        end = index + 1;
        break;
      }
    }
  }
  assert.ok(end > open, `fim de ${name} ausente`);
  return vm.runInNewContext(`(${source.slice(start, end)})`, context);
}

function walkFiles(rootDir) {
  if (!fs.existsSync(rootDir)) return [];
  const stat = fs.lstatSync(rootDir);
  if (stat.isFile()) return [rootDir];
  const files = [];
  for (const entry of fs.readdirSync(rootDir, { withFileTypes: true })) {
    const candidate = path.join(rootDir, entry.name);
    if (entry.isDirectory()) files.push(...walkFiles(candidate));
    else if (entry.isFile()) files.push(candidate);
  }
  return files;
}

function buildSyntheticPackagedSource(rootDir, version = '2.0.0') {
  const sourceDir = path.join(rootDir, 'source');
  write(path.join(sourceDir, 'package.json'), `${JSON.stringify({
    name: 'jk-sistema-desktop',
    version
  })}\n`);
  write(path.join(sourceDir, 'backend_api.py'), 'APP_VERSION = "2.0.0"\n');
  write(path.join(sourceDir, 'backend', 'api.py'), 'VERSION = 2\n');
  write(path.join(sourceDir, 'runtime-manifest.json'), `${JSON.stringify({ version })}\n`);
  manifestGenerator.writeLocalAppManifest(sourceDir, version);
  return sourceDir;
}

function assertFirebaseDiscoveryContracts(tempRoot) {
  const runtimeDir = path.join(tempRoot, 'runtime');
  const canonicalPath = path.join(runtimeDir, 'info', 'firebase-service-account.json');
  const legacyPath = path.join(runtimeDir, 'jkjkjk-synthetic-sentinel.json');
  write(canonicalPath, SYNTHETIC_SERVICE_ACCOUNT_TEXT);
  write(legacyPath, SYNTHETIC_SERVICE_ACCOUNT_TEXT);

  const getFirebaseServiceAccountCandidates = extractFunction(
    backendSource,
    'getFirebaseServiceAccountCandidates',
    { fs, path }
  );
  const readFirebaseServiceAccount = extractFunction(
    backendSource,
    'readFirebaseServiceAccount',
    { readJsonFile }
  );
  const getFirebasePresenceEnvCandidates = extractFunction(
    backendSource,
    'getFirebasePresenceEnvCandidates',
    { path, JK_FIREBASE_PRESENCE_ENV_FILE_NAME: 'firebase-presence.env' }
  );
  const readFirebaseRuntimeEnvValues = extractFunction(
    backendSource,
    'readFirebaseRuntimeEnvValues',
    { getFirebasePresenceEnvCandidates, readLocalDotEnvValues: () => ({}) }
  );
  const pickFirebaseRuntimeEnv = extractFunction(
    backendSource,
    'pickFirebaseRuntimeEnv'
  );
  const getLocalBackendFirebaseEnv = extractFunction(
    backendSource,
    'getLocalBackendFirebaseEnv',
    {
      fs,
      getFirebaseServiceAccountCandidates,
      readFirebaseServiceAccount,
      readFirebaseRuntimeEnvValues,
      pickFirebaseRuntimeEnv
    }
  );

  const candidates = getFirebaseServiceAccountCandidates(runtimeDir).map(candidate => path.resolve(candidate));
  assert(candidates.includes(path.resolve(canonicalPath)), 'service account canonica em info deve ser reconhecida');
  assert(candidates.includes(path.resolve(legacyPath)), 'service account legada jkjkjk-*.json deve continuar reconhecida');

  const canonicalEnv = getLocalBackendFirebaseEnv(runtimeDir);
  assert.strictEqual(canonicalEnv.JK_ACCESS_BACKEND, 'firebase');
  assert.strictEqual(
    path.resolve(canonicalEnv.FIREBASE_SERVICE_ACCOUNT_FILE),
    path.resolve(canonicalPath),
    'caminho canonico deve ter precedencia sobre a credencial legada'
  );

  fs.rmSync(canonicalPath, { force: true });
  const legacyEnv = getLocalBackendFirebaseEnv(runtimeDir);
  assert.strictEqual(legacyEnv.JK_ACCESS_BACKEND, 'firebase');
  assert.strictEqual(
    path.resolve(legacyEnv.FIREBASE_SERVICE_ACCOUNT_FILE),
    path.resolve(legacyPath),
    'credencial legada deve permanecer como fallback de compatibilidade'
  );
}

function assertCanonicalCredentialSurvivesMaterialization(tempRoot) {
  const sourceDir = buildSyntheticPackagedSource(tempRoot);
  const targetDir = path.join(tempRoot, 'target');
  const canonicalPath = path.join(targetDir, 'info', 'firebase-service-account.json');
  const legacyPath = path.join(targetDir, 'jkjkjk-synthetic-sentinel.json');
  write(path.join(targetDir, 'package.json'), '{"name":"jk-sistema-desktop","version":"1.0.0"}\n');
  write(path.join(targetDir, 'backend', 'stale.py'), 'STALE = True\n');
  write(canonicalPath, SYNTHETIC_SERVICE_ACCOUNT_TEXT);
  write(legacyPath, SYNTHETIC_SERVICE_ACCOUNT_TEXT);

  const applied = materializer.materializeLocalApp({
    sourceDir,
    targetDir,
    expectedVersion: '2.0.0'
  });
  assert.strictEqual(applied.changed, true);
  assert.strictEqual(
    fs.readFileSync(canonicalPath, 'utf8'),
    SYNTHETIC_SERVICE_ACCOUNT_TEXT,
    'materializacao deve preservar a credencial canonica em info'
  );
  assert.strictEqual(
    fs.readFileSync(legacyPath, 'utf8'),
    SYNTHETIC_SERVICE_ACCOUNT_TEXT,
    'materializacao deve preservar a credencial legada existente'
  );
  materializer.finalizeMaterialization(applied);

  const second = materializer.materializeLocalApp({
    sourceDir,
    targetDir,
    expectedVersion: '2.0.0'
  });
  assert.strictEqual(second.changed, false);
  assert.strictEqual(fs.readFileSync(canonicalPath, 'utf8'), SYNTHETIC_SERVICE_ACCOUNT_TEXT);
}

function assertInstallerStillRejectsServiceAccounts(tempRoot) {
  const forbiddenGlobs = installerManifest.forbiddenPackagedGlobs || [];
  for (const required of [
    'local_app/info/**',
    'local_app/jkjkjk-*.json',
    'local_app/firebase-service-account.json',
    'local_app/**/*service-account*.json'
  ]) {
    assert(
      forbiddenGlobs.includes(required),
      `verificador do pacote deve manter o bloqueio por nome/caminho: ${required}`
    );
  }

  const validateNoServiceAccountJson = extractFunction(
    verifierSource,
    'validateNoServiceAccountJson',
    {
      path,
      fileExists: candidate => fs.existsSync(candidate) && fs.lstatSync(candidate).isFile(),
      walkFiles,
      readJson: readJsonFile,
      toPosix: value => String(value || '').replace(/\\/g, '/')
    }
  );
  const disguisedRoot = path.join(tempRoot, 'disguised-package');
  write(
    path.join(disguisedRoot, 'local_app', 'public-config-renamed.json'),
    SYNTHETIC_SERVICE_ACCOUNT_TEXT
  );
  const failures = [];
  validateNoServiceAccountJson(disguisedRoot, disguisedRoot, failures, 'na fixture sintetica');
  assert.strictEqual(failures.length, 1, 'conteudo de service account renomeado deve ser rejeitado');
  assert.match(failures[0], /Credencial de service account proibida/);
}

const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'jk-firebase-provisioning-'));
try {
  assertFirebaseDiscoveryContracts(path.join(tempRoot, 'discovery'));
  assertCanonicalCredentialSurvivesMaterialization(path.join(tempRoot, 'materialization'));
  assertInstallerStillRejectsServiceAccounts(path.join(tempRoot, 'installer'));
  console.log('electron firebase provisioning regression: OK');
} finally {
  fs.rmSync(tempRoot, { recursive: true, force: true });
}
