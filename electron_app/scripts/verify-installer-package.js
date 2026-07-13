const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { spawnSync } = require('child_process');

const appDir = path.resolve(__dirname, '..');
const repoRoot = path.resolve(appDir, '..');
const manifestPath = path.join(appDir, 'installer-required-resources.json');
const packageJsonPath = path.join(appDir, 'package.json');

const args = process.argv.slice(2);
const packagedIndex = args.indexOf('--packaged');
const packagedRoot = packagedIndex >= 0 ? path.resolve(process.cwd(), args[packagedIndex + 1] || '') : null;
const deepRuntime = args.includes('--deep-runtime');

function toPosix(value) {
  return String(value || '').replace(/\\/g, '/').replace(/\/+/g, '/').replace(/^\.\//, '');
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));
}

function sha256File(file) {
  const hash = crypto.createHash('sha256');
  const descriptor = fs.openSync(file, 'r');
  const buffer = Buffer.allocUnsafe(1024 * 1024);
  try {
    let bytes = 0;
    do {
      bytes = fs.readSync(descriptor, buffer, 0, buffer.length, null);
      if (bytes > 0) hash.update(buffer.subarray(0, bytes));
    } while (bytes > 0);
  } finally {
    fs.closeSync(descriptor);
  }
  return hash.digest('hex');
}

function validateManifestEntry(baseDir, entry, label, failures) {
  const rel = toPosix(entry && (entry.path || entry.file || entry.name));
  const expectedHash = String(entry && entry.sha256 || '').trim().toLowerCase();
  if (!rel || !/^[a-f0-9]{64}$/.test(expectedHash)) {
    failures.push(`Manifesto invalido para ${label}`);
    return;
  }
  const root = path.resolve(baseDir);
  const target = path.resolve(root, rel);
  if (target !== root && !target.startsWith(`${root}${path.sep}`)) {
    failures.push(`Caminho fora do pacote em ${label}: ${rel}`);
    return;
  }
  if (!fileExists(target)) {
    failures.push(`Arquivo do manifesto ausente em ${label}: ${rel}`);
    return;
  }
  if (Number.isFinite(Number(entry.size)) && fs.statSync(target).size !== Number(entry.size)) {
    failures.push(`Tamanho divergente em ${label}: ${rel}`);
    return;
  }
  if (sha256File(target) !== expectedHash) failures.push(`SHA256 divergente em ${label}: ${rel}`);
}

function validateOfflineArtifacts(packageRoot, failures, sourceLayout = false) {
  const requirementsPath = path.join(packageRoot, 'requirements.txt');
  const wheelDir = path.join(packageRoot, 'python_wheels');
  const wheelManifestPath = path.join(wheelDir, 'manifest.json');
  const runtimeManifestPath = sourceLayout
    ? path.join(packageRoot, '.installer_runtime', 'runtime-manifest.json')
    : path.join(packageRoot, 'runtime-manifest.json');
  if (!fileExists(requirementsPath) || !fileExists(wheelManifestPath) || !fileExists(runtimeManifestPath)) {
    failures.push(`Manifestos do runtime offline ausentes em ${packageRoot}`);
    return;
  }

  const wheelManifest = readJson(wheelManifestPath);
  const requirementsHash = sha256File(requirementsPath);
  if (String(wheelManifest.requirements_sha256 || '').toLowerCase() !== requirementsHash) {
    failures.push('Wheelhouse nao corresponde ao requirements.txt atual');
  }
  for (const wheel of wheelManifest.wheels || []) validateManifestEntry(wheelDir, wheel, 'wheelhouse', failures);

  const runtime = readJson(runtimeManifestPath);
  validateManifestEntry(path.join(packageRoot, 'python_runtime'), runtime.python, 'Python', failures);
  const prerequisitesRoot = sourceLayout
    ? path.join(packageRoot, '.installer_runtime', 'prerequisites')
    : path.join(packageRoot, 'prerequisites');
  validateManifestEntry(prerequisitesRoot, runtime.visual_cpp, 'Visual C++', failures);
  const modelRoot = sourceLayout
    ? path.join(packageRoot, '.installer_runtime', 'black_jhon', 'faster-whisper-small')
    : path.join(packageRoot, 'black_jhon_runtime', 'faster-whisper-small');
  for (const entry of runtime.whisper && runtime.whisper.files || []) {
    validateManifestEntry(modelRoot, entry, 'Whisper Small', failures);
  }
}

function validateOfflineResolution(packageRoot, failures) {
  const python = process.env.JK_INSTALLER_VERIFY_PYTHON || 'python';
  const result = spawnSync(python, [
    '-m', 'pip', 'install', '--dry-run', '--ignore-installed', '--no-index',
    '--find-links', path.join(packageRoot, 'python_wheels'), '--platform', 'win_amd64',
    '--python-version', '3.11', '--implementation', 'cp', '--abi', 'cp311',
    '--only-binary=:all:', '-r', path.join(packageRoot, 'requirements.txt'),
  ], { encoding: 'utf8', windowsHide: true, maxBuffer: 16 * 1024 * 1024 });
  if (result.status !== 0) {
    failures.push(`Resolucao offline incompleta: ${(result.stderr || result.stdout || '').trim().slice(-3000)}`);
  }
}

function validateDeepRuntime(packageRoot, failures) {
  if (!deepRuntime) return;
  const script = path.join(repoRoot, 'scripts', 'verify-installer-offline.ps1');
  const result = spawnSync('powershell.exe', [
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', script,
    '-PackageRoot', packageRoot, '-BuildRoot', repoRoot,
  ], { encoding: 'utf8', windowsHide: true, maxBuffer: 32 * 1024 * 1024 });
  if (result.status !== 0) {
    failures.push(`Smoke test offline falhou: ${(result.stderr || result.stdout || '').trim().slice(-5000)}`);
  } else if (result.stdout) {
    process.stdout.write(result.stdout);
  }
}

function fileExists(file) {
  try {
    return fs.statSync(file).isFile();
  } catch (_err) {
    return false;
  }
}

function dirExists(dir) {
  try {
    return fs.statSync(dir).isDirectory();
  } catch (_err) {
    return false;
  }
}

function walkFiles(dir) {
  const out = [];
  if (!dirExists(dir)) return out;
  const stack = [dir];
  while (stack.length) {
    const current = stack.pop();
    let entries = [];
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch (err) {
      if (err && (err.code === 'EACCES' || err.code === 'EPERM')) continue;
      throw err;
    }
    for (const entry of entries) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) stack.push(full);
      else if (entry.isFile()) out.push(full);
    }
  }
  return out;
}

function countFiles(dir) {
  return walkFiles(dir).length;
}

function staticGlobPrefix(pattern) {
  const parts = toPosix(pattern).split('/');
  const prefix = [];
  for (const part of parts) {
    if (part.includes('*')) break;
    prefix.push(part);
  }
  return prefix.join('/');
}

function globToRegex(pattern) {
  let source = toPosix(pattern).replace(/[.+^${}()|[\]\\]/g, '\\$&');
  source = source.replace(/\*\*/g, '\u0000');
  source = source.replace(/\*/g, '[^/]*');
  source = source.replace(/\u0000/g, '.*');
  return new RegExp(`^${source}$`);
}

function findGlob(baseDir, pattern) {
  const prefix = staticGlobPrefix(pattern);
  const start = prefix ? path.join(baseDir, prefix) : baseDir;
  const regex = globToRegex(pattern);
  return walkFiles(start).filter((file) => regex.test(toPosix(path.relative(baseDir, file))));
}

function normalizeResourceEntry(entry) {
  return {
    from: toPosix(entry && entry.from),
    to: toPosix(entry && entry.to),
  };
}

function resolveMirrorPairPolicy(manifest, pair) {
  const policy = manifest.htmlSourcePolicy || {};
  const canonicalDir = toPosix(policy.canonicalDirectory || '');
  const source = toPosix(pair && pair.source);
  const mirror = toPosix(pair && pair.mirror);

  if (canonicalDir && mirror.startsWith(`${canonicalDir}/`)) {
    return { canonical: mirror, legacy: source };
  }
  if (canonicalDir && source.startsWith(`${canonicalDir}/`)) {
    return { canonical: source, legacy: mirror };
  }
  return { canonical: source, legacy: mirror };
}

function failIfMissingSource(manifest, failures) {
  for (const rel of manifest.requiredSourceFiles || []) {
    if (!fileExists(path.join(repoRoot, rel))) failures.push(`Arquivo obrigatorio ausente: ${rel}`);
  }

  for (const rule of manifest.requiredSourceGlobs || []) {
    const found = findGlob(repoRoot, rule.pattern);
    if (found.length < Number(rule.min || 1)) {
      failures.push(`Padrao obrigatorio ausente: ${rule.pattern} (encontrado ${found.length})`);
    }
  }

  for (const rule of manifest.requiredSourceDirectories || []) {
    const dir = path.join(repoRoot, rule.path);
    const total = countFiles(dir);
    if (!dirExists(dir) || total < Number(rule.minFiles || 1)) {
      failures.push(`Diretorio obrigatorio incompleto: ${rule.path} (${total} arquivo(s))`);
    }
  }

  for (const pair of manifest.mirrorPairs || []) {
    const { canonical, legacy } = resolveMirrorPairPolicy(manifest, pair);
    const canonicalPath = path.join(repoRoot, canonical);
    const legacyPath = path.join(repoRoot, legacy);
    if (!fileExists(canonicalPath)) {
      failures.push(`Fonte HTML oficial ausente: ${canonical}`);
      continue;
    }
    if (!fileExists(legacyPath)) {
      failures.push(`Espelho HTML legado ausente: ${legacy}`);
      continue;
    }
    const a = fs.readFileSync(canonicalPath);
    const b = fs.readFileSync(legacyPath);
    if (!a.equals(b)) {
      failures.push(`HTML fora da regra de fonte unica: edite ${canonical} e sincronize ${legacy}`);
    }
  }
}

function validatePackageConfig(manifest, failures) {
  const pkg = readJson(packageJsonPath);
  const build = pkg.build || {};
  const resources = Array.isArray(build.extraResources) ? build.extraResources.map(normalizeResourceEntry) : [];

  for (const expected of manifest.requiredExtraResources || []) {
    const expectedNorm = normalizeResourceEntry(expected);
    const ok = resources.some((entry) => entry.from === expectedNorm.from && entry.to === expectedNorm.to);
    if (!ok) failures.push(`extraResources obrigatorio ausente: ${expectedNorm.from} -> ${expectedNorm.to}`);
  }

  const localApp = (build.extraResources || []).find((entry) => toPosix(entry && entry.to) === 'local_app');
  const filters = Array.isArray(localApp && localApp.filter) ? localApp.filter.map(toPosix) : [];
  for (const filter of manifest.requiredLocalAppFilters || []) {
    if (!filters.includes(toPosix(filter))) failures.push(`Filtro local_app obrigatorio ausente: ${filter}`);
  }

  for (const filter of manifest.forbiddenLocalAppFilters || []) {
    if (filters.includes(toPosix(filter))) failures.push(`Filtro local_app proibido: ${filter}`);
  }

  const packageText = JSON.stringify(build);
  for (const forbidden of manifest.forbiddenBuildReferences || []) {
    if (packageText.includes(forbidden)) failures.push(`Referencia privada proibida no build: ${forbidden}`);
  }
}

function validateRuntimeCopyGuards(manifest, failures) {
  const runtimeGuardSources = [
    path.join(appDir, 'main.js'),
    path.join(repoRoot, 'main.js'),
    path.join(appDir, 'main', 'modules', 'backend.js'),
  ].filter((filePath) => {
    try {
      return fs.statSync(filePath).isFile();
    } catch (_err) {
      return false;
    }
  });

  if (!runtimeGuardSources.length) {
    failures.push('Arquivo principal de runtime nao encontrado para validacao de protecao de sobrescrita');
    return;
  }

  for (const entry of manifest.requiredRuntimeSkipEntries || []) {
    const needle = `lower === '${entry}'`;
    const present = runtimeGuardSources.some((sourcePath) => {
      const source = fs.readFileSync(sourcePath, 'utf8');
      return source.includes(needle);
    });
    if (!present) {
      failures.push(`Protecao runtime ausente contra sobrescrita de dados locais: ${entry}`);
    }
  }
}

function validatePackagedOutput(manifest, failures) {
  if (!packagedRoot) return;
  if (!dirExists(packagedRoot)) {
    failures.push(`Saida empacotada nao encontrada: ${packagedRoot}`);
    return;
  }

  for (const rel of manifest.requiredPackagedFiles || []) {
    if (!fileExists(path.join(packagedRoot, rel))) failures.push(`Arquivo ausente no pacote: ${rel}`);
  }

  for (const rule of manifest.requiredPackagedGlobs || []) {
    const found = findGlob(packagedRoot, rule.pattern);
    if (found.length < Number(rule.min || 1)) {
      failures.push(`Padrao ausente no pacote: ${rule.pattern} (encontrado ${found.length})`);
    }
  }

  for (const rule of manifest.requiredPackagedDirectories || []) {
    const dir = path.join(packagedRoot, rule.path);
    const total = countFiles(dir);
    if (!dirExists(dir) || total < Number(rule.minFiles || 1)) {
      failures.push(`Diretorio incompleto no pacote: ${rule.path} (${total} arquivo(s))`);
    }
  }

  for (const rule of manifest.forbiddenPackagedGlobs || []) {
    const pattern = typeof rule === 'string' ? rule : rule.pattern;
    const found = findGlob(packagedRoot, pattern);
    if (found.length) {
      failures.push(`Conteudo privado proibido no pacote: ${pattern} (${found.length} arquivo(s))`);
    }
  }

  const asarPath = path.join(packagedRoot, 'app.asar');
  if (fileExists(asarPath)) {
    try {
      const asar = require('@electron/asar');
      const bootstrap = asar.extractFile(asarPath, 'main.js').toString('utf8');
      if (!bootstrap.includes('process.resourcesPath') || !bootstrap.includes("'local_app'")) {
        failures.push('Bootstrap app.asar/main.js nao aponta para resources/local_app/main.js');
      }
    } catch (err) {
      failures.push(`Nao foi possivel validar app.asar/main.js: ${err && err.message ? err.message : String(err)}`);
    }
  }
}

function main() {
  const manifest = readJson(manifestPath);
  const failures = [];

  failIfMissingSource(manifest, failures);
  validatePackageConfig(manifest, failures);
  validateRuntimeCopyGuards(manifest, failures);
  validatePackagedOutput(manifest, failures);
  const offlineRoot = packagedRoot ? path.join(packagedRoot, 'local_app') : repoRoot;
  validateOfflineArtifacts(offlineRoot, failures, !packagedRoot);
  validateOfflineResolution(offlineRoot, failures);
  if (!failures.length) validateDeepRuntime(offlineRoot, failures);

  if (failures.length) {
    console.error('[installer-check] FALHOU');
    for (const failure of failures) console.error(`- ${failure}`);
    process.exit(1);
  }

  const target = packagedRoot ? ` e pacote ${packagedRoot}` : '';
  console.log(`[installer-check] OK - recursos obrigatorios validados${target}.`);
}

main();
