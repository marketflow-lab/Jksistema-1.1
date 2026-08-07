'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const MANIFEST_NAME = 'local-app-manifest.json';
const PAYLOAD_ROOTS = new Set([
  'black_jhon_runtime',
  'prerequisites',
  'python_runtime',
  'python_wheels'
]);
const FORBIDDEN_SEGMENTS = new Set([
  '.git',
  '.obsidian',
  '.venv',
  'backups',
  'context_hub',
  'contextvault',
  'info',
  'logs',
  'node_modules',
  'sku'
]);
const SAFE_VERSION_RE = /^[A-Za-z0-9][A-Za-z0-9._+-]{0,119}$/;
const BYTECODE_EXTENSIONS = new Set(['.pyc', '.pyo']);

function sha256File(filePath) {
  const hash = crypto.createHash('sha256');
  const descriptor = fs.openSync(filePath, 'r');
  const buffer = Buffer.allocUnsafe(1024 * 1024);
  try {
    let bytesRead = 0;
    do {
      bytesRead = fs.readSync(descriptor, buffer, 0, buffer.length, null);
      if (bytesRead > 0) hash.update(buffer.subarray(0, bytesRead));
    } while (bytesRead > 0);
  } finally {
    fs.closeSync(descriptor);
  }
  return hash.digest('hex');
}

function pathExists(candidate) {
  try {
    fs.lstatSync(candidate);
    return true;
  } catch (err) {
    if (err && (err.code === 'ENOENT' || err.code === 'ENOTDIR')) return false;
    throw err;
  }
}

function normalizeRelative(rawValue) {
  const raw = String(rawValue || '');
  if (
    !raw
    || raw.includes('\\')
    || raw.includes('\0')
    || path.posix.isAbsolute(raw)
    || path.posix.normalize(raw) !== raw
    || raw.split('/').some(part => !part || part === '.' || part === '..' || part.includes(':'))
  ) {
    throw new Error(`Caminho invalido no manifesto local_app: ${raw || 'ausente'}`);
  }
  return raw;
}

function assertAllowedRelative(relative) {
  const normalized = normalizeRelative(relative);
  if (hasForbiddenSegment(normalized)) {
    throw new Error(`Dado persistente ou sensivel encontrado no pacote local_app: ${normalized}`);
  }
  return normalized;
}

function hasForbiddenSegment(relative) {
  const normalized = String(relative || '').toLowerCase();
  const allowContextHubSource = normalized === 'backend/modules/context_hub'
    || normalized.startsWith('backend/modules/context_hub/');
  const allowFavoritosSkuSource = normalized === 'static/favoritos/v2/sku'
    || normalized.startsWith('static/favoritos/v2/sku/');
  return normalized.split('/').some(segment => (
    (FORBIDDEN_SEGMENTS.has(segment) && !(segment === 'context_hub' && allowContextHubSource))
    && !(segment === 'sku' && allowFavoritosSkuSource)
    || segment.startsWith('.env')
  ));
}

function assertRealDirectoryRoot(candidate, label) {
  const root = path.resolve(candidate);
  if (!pathExists(root)) throw new Error(`${label} ausente: ${root}`);
  const stat = fs.lstatSync(root);
  if (!stat.isDirectory() || stat.isSymbolicLink()) {
    throw new Error(`${label} deve ser um diretorio real: ${root}`);
  }
  return root;
}

function removeCacheDirectoryStrict(candidate, root) {
  const resolvedRoot = path.resolve(root);
  const resolvedCandidate = path.resolve(candidate);
  if (!resolvedCandidate.startsWith(`${resolvedRoot}${path.sep}`)) {
    throw new Error(`Cache fora do staging local_app: ${resolvedCandidate}`);
  }
  const files = [];
  const directories = [];
  const stack = [resolvedCandidate];
  while (stack.length) {
    const directory = stack.pop();
    const stat = fs.lstatSync(directory);
    if (!stat.isDirectory() || stat.isSymbolicLink()) {
      throw new Error(`Link ou junction proibido no cache do pacote: ${directory}`);
    }
    directories.push(directory);
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const fullPath = path.join(directory, entry.name);
      const entryStat = fs.lstatSync(fullPath);
      if (entryStat.isSymbolicLink()) {
        throw new Error(`Link ou junction proibido no cache do pacote: ${fullPath}`);
      }
      if (entryStat.isDirectory()) stack.push(fullPath);
      else if (entryStat.isFile()) files.push(fullPath);
      else throw new Error(`Tipo especial proibido no cache do pacote: ${fullPath}`);
    }
  }
  for (const filePath of files) fs.unlinkSync(filePath);
  directories.sort((left, right) => right.length - left.length);
  for (const directory of directories) fs.rmdirSync(directory);
  return files.length;
}

function sanitizeLocalAppStaging(localAppDir) {
  const root = assertRealDirectoryRoot(localAppDir, 'Staging local_app');
  const stack = [root];
  let removedFiles = 0;
  let removedDirectories = 0;
  while (stack.length) {
    const directory = stack.pop();
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const fullPath = path.join(directory, entry.name);
      const relative = path.relative(root, fullPath).replace(/\\/g, '/');
      const stat = fs.lstatSync(fullPath);
      if (stat.isSymbolicLink()) {
        throw new Error(`Link ou junction proibido no staging local_app: ${relative}`);
      }
      if (hasForbiddenSegment(relative)) {
        // Persistent/private paths must remain visible so manifest generation
        // rejects the package instead of silently deleting evidence.
        continue;
      }
      if (stat.isDirectory()) {
        if (entry.name.toLowerCase() === '__pycache__') {
          removedFiles += removeCacheDirectoryStrict(fullPath, root);
          removedDirectories += 1;
        } else {
          stack.push(fullPath);
        }
      } else if (stat.isFile()) {
        if (BYTECODE_EXTENSIONS.has(path.extname(entry.name).toLowerCase())) {
          fs.unlinkSync(fullPath);
          removedFiles += 1;
        }
      } else {
        throw new Error(`Tipo de arquivo nao suportado no staging local_app: ${relative}`);
      }
    }
  }
  return { removedFiles, removedDirectories };
}

function collectManagedFiles(localAppDir) {
  const root = assertRealDirectoryRoot(localAppDir, 'local_app empacotado');

  const files = [];
  const stack = [{ directory: root, relative: '' }];
  while (stack.length) {
    const current = stack.pop();
    const entries = fs.readdirSync(current.directory, { withFileTypes: true })
      .sort((left, right) => right.name.localeCompare(left.name, 'en'));
    for (const entry of entries) {
      const relative = current.relative ? `${current.relative}/${entry.name}` : entry.name;
      const normalized = relative.replace(/\\/g, '/');
      const fullPath = path.join(current.directory, entry.name);
      const stat = fs.lstatSync(fullPath);
      if (stat.isSymbolicLink()) throw new Error(`Link ou junction proibido no pacote local_app: ${normalized}`);
      const topLevel = normalized.split('/')[0].toLowerCase();
      if (PAYLOAD_ROOTS.has(topLevel)) continue;
      if (normalized.toLowerCase() === MANIFEST_NAME.toLowerCase()) continue;
      assertAllowedRelative(normalized);
      if (stat.isDirectory()) {
        stack.push({ directory: fullPath, relative: normalized });
      } else if (stat.isFile()) {
        files.push({
          path: normalized,
          size: stat.size,
          sha256: sha256File(fullPath)
        });
      } else {
        throw new Error(`Tipo de arquivo nao suportado no pacote local_app: ${normalized}`);
      }
    }
  }
  return files.sort((left, right) => left.path.localeCompare(right.path, 'en'));
}

function buildLocalAppManifest(localAppDir, version) {
  const normalizedVersion = String(version || '').trim();
  if (!SAFE_VERSION_RE.test(normalizedVersion)) {
    throw new Error(`Versao invalida para o manifesto local_app: ${normalizedVersion || 'ausente'}`);
  }
  const files = collectManagedFiles(localAppDir);
  const managedDirectories = [...new Set(
    files.filter(entry => entry.path.includes('/')).map(entry => entry.path.split('/')[0])
  )].sort((left, right) => left.localeCompare(right, 'en'));
  const managedRootFiles = files.filter(entry => !entry.path.includes('/'))
    .map(entry => entry.path)
    .sort((left, right) => left.localeCompare(right, 'en'));
  return {
    schema_version: 1,
    version: normalizedVersion,
    algorithm: 'sha256',
    file_count: files.length,
    managed_directories: managedDirectories,
    managed_root_files: managedRootFiles,
    excluded_payload_roots: [...PAYLOAD_ROOTS].sort((left, right) => left.localeCompare(right, 'en')),
    files
  };
}

function writeLocalAppManifest(localAppDir, version) {
  const root = path.resolve(localAppDir);
  const outputPath = path.join(root, MANIFEST_NAME);
  const manifest = buildLocalAppManifest(root, version);
  const temporary = `${outputPath}.tmp_${process.pid}_${crypto.randomBytes(6).toString('hex')}`;
  try {
    fs.writeFileSync(temporary, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
    fs.renameSync(temporary, outputPath);
  } finally {
    try { fs.rmSync(temporary, { force: true }); } catch (_err) {}
  }
  return { manifest, outputPath };
}

function validateLocalAppManifestAtRoot(localAppDir, expectedVersion) {
  const root = path.resolve(localAppDir);
  const manifestPath = path.join(root, MANIFEST_NAME);
  if (!pathExists(manifestPath)) return [`${MANIFEST_NAME} ausente no pacote.`];
  let manifest;
  try {
    manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8').replace(/^\uFEFF/, ''));
  } catch (err) {
    return [`${MANIFEST_NAME} invalido: ${err && err.message ? err.message : String(err)}`];
  }
  const failures = [];
  if (Number(manifest.schema_version) !== 1) failures.push('schema_version do local-app-manifest invalido.');
  if (String(manifest.version || '') !== String(expectedVersion || '')) {
    failures.push(`Versao do local-app-manifest divergente: esperado ${expectedVersion}, encontrado ${manifest.version || 'ausente'}.`);
  }
  if (String(manifest.algorithm || '').toLowerCase() !== 'sha256') failures.push('Algoritmo do local-app-manifest invalido.');

  let actualFiles = [];
  try {
    actualFiles = collectManagedFiles(root);
  } catch (err) {
    failures.push(err && err.message ? err.message : String(err));
    return failures;
  }
  const declaredFiles = Array.isArray(manifest.files) ? manifest.files : [];
  if (Number(manifest.file_count) !== declaredFiles.length || declaredFiles.length !== actualFiles.length) {
    failures.push('file_count do local-app-manifest diverge do pacote real.');
  }
  const actualByPath = new Map(actualFiles.map(entry => [entry.path, entry]));
  const seen = new Set();
  for (const entry of declaredFiles) {
    let relative = '';
    try { relative = assertAllowedRelative(entry && entry.path); } catch (err) {
      failures.push(err && err.message ? err.message : String(err));
      continue;
    }
    if (seen.has(relative)) {
      failures.push(`Arquivo duplicado no local-app-manifest: ${relative}`);
      continue;
    }
    seen.add(relative);
    const actual = actualByPath.get(relative);
    if (
      !actual
      || Number(entry.size) !== actual.size
      || String(entry.sha256 || '').toLowerCase() !== actual.sha256
    ) {
      failures.push(`Arquivo divergente no local-app-manifest: ${relative}`);
    }
  }
  for (const actual of actualFiles) {
    if (!seen.has(actual.path)) failures.push(`Arquivo nao declarado no local-app-manifest: ${actual.path}`);
  }

  const expectedDirectories = [...new Set(
    actualFiles.filter(entry => entry.path.includes('/')).map(entry => entry.path.split('/')[0])
  )].sort((left, right) => left.localeCompare(right, 'en'));
  const expectedRootFiles = actualFiles.filter(entry => !entry.path.includes('/'))
    .map(entry => entry.path)
    .sort((left, right) => left.localeCompare(right, 'en'));
  const managedDirectories = Array.isArray(manifest.managed_directories)
    ? [...manifest.managed_directories].sort((left, right) => String(left).localeCompare(String(right), 'en'))
    : [];
  const managedRootFiles = Array.isArray(manifest.managed_root_files)
    ? [...manifest.managed_root_files].sort((left, right) => String(left).localeCompare(String(right), 'en'))
    : [];
  if (JSON.stringify(managedDirectories) !== JSON.stringify(expectedDirectories)) {
    failures.push('managed_directories diverge do pacote real.');
  }
  if (JSON.stringify(managedRootFiles) !== JSON.stringify(expectedRootFiles)) {
    failures.push('managed_root_files diverge do pacote real.');
  }
  const excludedPayloads = Array.isArray(manifest.excluded_payload_roots)
    ? [...manifest.excluded_payload_roots].sort()
    : [];
  if (JSON.stringify(excludedPayloads) !== JSON.stringify([...PAYLOAD_ROOTS].sort())) {
    failures.push('excluded_payload_roots do local-app-manifest invalido.');
  }
  return failures;
}

module.exports = {
  MANIFEST_NAME,
  PAYLOAD_ROOTS,
  buildLocalAppManifest,
  collectManagedFiles,
  sanitizeLocalAppStaging,
  sha256File,
  validateLocalAppManifestAtRoot,
  writeLocalAppManifest
};
