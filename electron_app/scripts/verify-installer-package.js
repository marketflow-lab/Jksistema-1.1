const fs = require('fs');
const path = require('path');
const os = require('os');
const crypto = require('crypto');
const { spawnSync } = require('child_process');

const appDir = path.resolve(__dirname, '..');
const repoRoot = path.resolve(appDir, '..');
const manifestPath = path.join(appDir, 'installer-required-resources.json');
const packageJsonPath = path.join(appDir, 'package.json');
const runtimeVersionsPath = path.join(repoRoot, 'runtime-versions.json');

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

function validateContextBundleAtRoot(bundleRoot, failures, label) {
  const manifestFile = path.join(bundleRoot, 'context-bundle-manifest.json');
  const knowledgeRoot = path.join(bundleRoot, 'docs', 'knowledge');
  if (!fileExists(manifestFile)) {
    failures.push(`Manifesto do Context Hub ausente ${label}: context-bundle-manifest.json`);
    return;
  }
  if (!dirExists(knowledgeRoot)) {
    failures.push(`Bundle de conhecimento ausente ${label}: docs/knowledge`);
    return;
  }

  let contextManifest;
  try {
    contextManifest = readJson(manifestFile);
  } catch (err) {
    failures.push(`Manifesto do Context Hub invalido ${label}: ${err && err.message ? err.message : String(err)}`);
    return;
  }
  if (Number(contextManifest.schema_version) !== 1) {
    failures.push(`schema_version do Context Hub invalido ${label}`);
  }
  const expectedVersion = String(readJson(packageJsonPath).version || '').trim();
  if (String(contextManifest.source_version || '').trim() !== expectedVersion) {
    failures.push(
      `Versao-fonte do Context Hub divergente ${label}: esperado ${expectedVersion}, encontrado ${contextManifest.source_version || 'ausente'}`
    );
  }

  const entries = Array.isArray(contextManifest.files) ? contextManifest.files : [];
  if (!entries.length) failures.push(`Manifesto do Context Hub sem arquivos ${label}`);
  const declared = new Set();
  for (const entry of entries) {
    const rawPath = String(entry && entry.path || '');
    const relative = toPosix(rawPath);
    const safePath = relative
      && rawPath === relative
      && relative.startsWith('docs/knowledge/')
      && path.posix.normalize(relative) === relative
      && !relative.split('/').some(part => part === '..' || part === '.obsidian')
      && !/(?:^|\/)(?:info|ContextVault|context_hub|SKU)(?:\/|$)/i.test(relative);
    if (!safePath) {
      failures.push(`Caminho proibido no manifesto do Context Hub ${label}: ${rawPath || 'ausente'}`);
      continue;
    }
    if (declared.has(relative)) {
      failures.push(`Arquivo duplicado no manifesto do Context Hub ${label}: ${relative}`);
      continue;
    }
    declared.add(relative);
    const target = path.resolve(bundleRoot, relative);
    if (!fileExists(target)) {
      failures.push(`Arquivo do Context Hub ausente ${label}: ${relative}`);
      continue;
    }
    const expectedHash = String(entry.sha256 || '').trim().toLowerCase();
    if (!/^[a-f0-9]{64}$/.test(expectedHash) || sha256File(target) !== expectedHash) {
      failures.push(`SHA256 divergente no Context Hub ${label}: ${relative}`);
    }
    const expectedSize = Number(entry.size);
    if (!Number.isSafeInteger(expectedSize) || expectedSize < 0 || fs.statSync(target).size !== expectedSize) {
      failures.push(`Tamanho divergente no Context Hub ${label}: ${relative}`);
    }
  }

  const actual = walkFiles(knowledgeRoot)
    .map(file => toPosix(path.relative(bundleRoot, file)))
    .sort();
  for (const relative of actual) {
    if (!declared.has(relative)) {
      failures.push(`Arquivo obsoleto ou nao declarado no Context Hub ${label}: ${relative}`);
    }
  }
  for (const relative of declared) {
    if (!actual.includes(relative)) {
      failures.push(`Entrada sem arquivo no Context Hub ${label}: ${relative}`);
    }
  }
  if (
    contextManifest.file_count !== undefined
    && Number(contextManifest.file_count) !== actual.length
  ) {
    failures.push(`file_count divergente no Context Hub ${label}`);
  }
}

function validateContextBundle(failures) {
  validateContextBundleAtRoot(repoRoot, failures, 'na fonte');
  if (packagedRoot) {
    validateContextBundleAtRoot(path.join(packagedRoot, 'local_app'), failures, 'no pacote');
  }
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
  const packagedRuntimeVersionsPath = path.join(packageRoot, 'runtime-versions.json');
  const runtimeManifestPath = sourceLayout
    ? path.join(packageRoot, '.installer_runtime', 'runtime-manifest.json')
    : path.join(packageRoot, 'runtime-manifest.json');
  if (
    !fileExists(requirementsPath)
    || !fileExists(wheelManifestPath)
    || !fileExists(runtimeManifestPath)
    || !fileExists(packagedRuntimeVersionsPath)
  ) {
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
  const versions = readJson(packagedRuntimeVersionsPath);
  const centralVersions = readJson(runtimeVersionsPath);
  if (JSON.stringify(versions) !== JSON.stringify(centralVersions)) {
    failures.push(`runtime-versions.json desatualizado em ${packageRoot}`);
  }
  const expectedAppVersion = String(readJson(packageJsonPath).version || '').trim();
  const runtimeVersion = String(runtime.version || '').trim();
  if (!runtimeVersion || runtimeVersion !== expectedAppVersion) {
    failures.push(`Versao do runtime offline divergente: esperado ${expectedAppVersion}, encontrado ${runtimeVersion || 'ausente'}`);
  }
  const expectedPython = versions.python || {};
  const runtimePython = runtime.python || {};
  const installerPin = {
    path: String(expectedPython.windowsInstaller || ''),
    size: Number(expectedPython.windowsInstallerSize),
    sha256: String(expectedPython.windowsInstallerSha256 || '').trim().toLowerCase(),
  };
  const portableConfig = expectedPython.windowsPortable || {};
  const portablePin = {
    path: String(portableConfig.path || ''),
    file_count: Number(portableConfig.fileCount),
    total_size: Number(portableConfig.totalSize),
    tree_sha256: String(portableConfig.treeSha256 || '').trim().toLowerCase(),
  };
  if (
    !installerPin.path
    || !Number.isSafeInteger(installerPin.size)
    || installerPin.size < 1
    || !/^[a-f0-9]{64}$/.test(installerPin.sha256)
  ) {
    failures.push('Pin central do instalador Python invalido');
  }
  if (
    portablePin.path !== 'portable'
    || !Number.isInteger(portablePin.file_count)
    || portablePin.file_count < 1
    || !Number.isSafeInteger(portablePin.total_size)
    || portablePin.total_size < 1
    || !/^[a-f0-9]{64}$/.test(portablePin.tree_sha256)
  ) {
    failures.push('Pin central da arvore Python portatil invalido');
  }
  if (String(runtimePython.version || '') !== String(expectedPython.version || '')) {
    failures.push(
      `Versao Python divergente no manifesto: esperado ${expectedPython.version || 'ausente'}, encontrado ${runtimePython.version || 'ausente'}`,
    );
  }
  if (String(runtimePython.abi || '') !== String(expectedPython.abi || '')) {
    failures.push(
      `ABI Python divergente no manifesto: esperado ${expectedPython.abi || 'ausente'}, encontrado ${runtimePython.abi || 'ausente'}`,
    );
  }
  if (
    String(runtimePython.installer && runtimePython.installer.path || '') !== installerPin.path
    || Number(runtimePython.installer && runtimePython.installer.size) !== installerPin.size
    || String(runtimePython.installer && runtimePython.installer.sha256 || '').toLowerCase() !== installerPin.sha256
  ) {
    failures.push('Manifesto do instalador Python diverge do pin central');
  }
  if (
    String(runtimePython.portable && runtimePython.portable.path || '') !== portablePin.path
    || Number(runtimePython.portable && runtimePython.portable.file_count) !== portablePin.file_count
    || Number(runtimePython.portable && runtimePython.portable.total_size) !== portablePin.total_size
    || String(runtimePython.portable && runtimePython.portable.tree_sha256 || '').toLowerCase() !== portablePin.tree_sha256
  ) {
    failures.push('Manifesto da arvore Python portatil diverge do pin central');
  }
  validateManifestEntry(path.join(packageRoot, 'python_runtime'), runtimePython.installer, 'instalador Python', failures);
  validatePortableRuntimeTree(path.join(packageRoot, 'python_runtime'), runtimePython.portable, failures);
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
  const versionsPath = path.join(packageRoot, 'runtime-versions.json');
  if (!fileExists(versionsPath)) {
    failures.push(`runtime-versions.json ausente em ${packageRoot}`);
    return;
  }
  const versions = readJson(versionsPath);
  const pythonConfig = versions.python || {};
  const portablePython = path.join(packageRoot, 'python_runtime', 'portable', 'python.exe');
  const bootstrapPython = process.env.JK_INSTALLER_VERIFY_PYTHON || portablePython;
  const toolRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'jk-installer-pip-'));
  const env = {
    ...process.env,
    PYTHONDONTWRITEBYTECODE: '1',
    PYTHONNOUSERSITE: '1',
    PIP_DISABLE_PIP_VERSION_CHECK: '1',
    PIP_NO_INDEX: '1',
  };
  try {
    const create = spawnSync(bootstrapPython, ['-B', '-I', '-m', 'venv', toolRoot], {
      encoding: 'utf8',
      windowsHide: true,
      maxBuffer: 16 * 1024 * 1024,
      env,
    });
    if (create.status !== 0) {
      failures.push(`Nao foi possivel criar o verificador pip isolado: ${(create.stderr || create.stdout || '').trim().slice(-3000)}`);
      return;
    }
    const python = path.join(toolRoot, process.platform === 'win32' ? 'Scripts' : 'bin', process.platform === 'win32' ? 'python.exe' : 'python');
    const pipProbe = spawnSync(python, ['-B', '-I', '-c', 'import pathlib,pip,sys; print(pathlib.Path(pip.__file__).resolve()); print(sys.prefix)'], {
      encoding: 'utf8',
      windowsHide: true,
      maxBuffer: 16 * 1024 * 1024,
      env,
    });
    const normalizedToolRoot = path.resolve(toolRoot).toLowerCase();
    const pipPath = String(pipProbe.stdout || '').split(/\r?\n/, 1)[0].trim();
    if (pipProbe.status !== 0 || !path.resolve(pipPath || '.').toLowerCase().startsWith(`${normalizedToolRoot}${path.sep}`)) {
      failures.push(`pip do verificador nao ficou isolado na venv temporaria: ${(pipProbe.stderr || pipProbe.stdout || '').trim().slice(-3000)}`);
      return;
    }
    const result = spawnSync(python, [
      '-B', '-I', '-m', 'pip', 'install', '--dry-run', '--ignore-installed', '--no-index',
      '--require-hashes', '--find-links', path.join(packageRoot, 'python_wheels'), '--platform', 'win_amd64',
      '--python-version', String(pythonConfig.minor || ''),
      '--implementation', String(pythonConfig.implementation || 'cp'),
      '--abi', String(pythonConfig.abi || ''),
      '--only-binary=:all:', '-r', path.join(packageRoot, 'requirements.txt'),
    ], {
      encoding: 'utf8',
      windowsHide: true,
      maxBuffer: 16 * 1024 * 1024,
      env,
    });
    if (result.status !== 0) {
      failures.push(`Resolucao offline incompleta: ${(result.stderr || result.stdout || '').trim().slice(-3000)}`);
    }
  } finally {
    try {
      fs.rmSync(toolRoot, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
    } catch (_err) {}
  }
}

function validateDeepRuntime(resourcesRoot, failures) {
  if (!deepRuntime) return;
  const script = path.join(repoRoot, 'scripts', 'verify-installer-offline.ps1');
  const result = spawnSync('powershell.exe', [
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', script,
    '-ResourcesRoot', resourcesRoot,
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

function filesEqual(left, right) {
  if (!fileExists(left) || !fileExists(right)) return false;
  const leftStat = fs.statSync(left);
  const rightStat = fs.statSync(right);
  if (leftStat.size !== rightStat.size) return false;
  return fs.readFileSync(left).equals(fs.readFileSync(right));
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
  const rootStat = fs.lstatSync(dir);
  if (rootStat.isSymbolicLink()) {
    throw new Error(`Link/reparse point proibido durante verificacao: ${dir}`);
  }
  const stack = [dir];
  while (stack.length) {
    const current = stack.pop();
    let entries;
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch (err) {
      throw new Error(
        `Diretorio ilegivel durante verificacao fail-closed: ${current} (${err && err.code ? err.code : 'erro'})`,
      );
    }
    for (const entry of entries) {
      const full = path.join(current, entry.name);
      let stat;
      try {
        stat = fs.lstatSync(full);
      } catch (err) {
        throw new Error(
          `Entrada ilegivel durante verificacao fail-closed: ${full} (${err && err.code ? err.code : 'erro'})`,
        );
      }
      if (stat.isSymbolicLink()) {
        throw new Error(`Link/reparse point proibido durante verificacao: ${full}`);
      }
      if (stat.isDirectory()) stack.push(full);
      else if (stat.isFile()) out.push(full);
      else throw new Error(`Entrada nao regular durante verificacao: ${full}`);
    }
  }
  return out;
}

function collectPortableRuntimeFiles(dir, failures) {
  const out = [];
  if (!dirExists(dir)) return out;
  const stack = [dir];
  while (stack.length) {
    const current = stack.pop();
    const entries = fs.readdirSync(current, { withFileTypes: true });
    for (const entry of entries) {
      const full = path.join(current, entry.name);
      const stat = fs.lstatSync(full);
      if (stat.isSymbolicLink()) {
        failures.push(`Link/reparse point proibido no Python portatil: ${toPosix(path.relative(dir, full))}`);
        continue;
      }
      if (stat.isDirectory()) {
        stack.push(full);
      } else if (stat.isFile()) {
        out.push({ full, size: stat.size, rel: toPosix(path.relative(dir, full)) });
      } else {
        failures.push(`Entrada nao regular no Python portatil: ${toPosix(path.relative(dir, full))}`);
      }
    }
  }
  return out.sort((left, right) => (left.rel < right.rel ? -1 : left.rel > right.rel ? 1 : 0));
}

function validatePortableRuntimeTree(pythonRuntimeRoot, entry, failures) {
  const rel = toPosix(entry && entry.path);
  const expectedHash = String(entry && entry.tree_sha256 || '').trim().toLowerCase();
  const expectedCount = Number(entry && entry.file_count);
  const expectedSize = Number(entry && entry.total_size);
  if (
    !rel
    || !/^[a-f0-9]{64}$/.test(expectedHash)
    || !Number.isInteger(expectedCount)
    || expectedCount < 1
    || !Number.isSafeInteger(expectedSize)
    || expectedSize < 1
  ) {
    failures.push('Manifesto invalido para arvore do Python portatil');
    return;
  }

  const root = path.resolve(pythonRuntimeRoot);
  const portableDir = path.resolve(root, rel);
  if (portableDir === root || !portableDir.startsWith(`${root}${path.sep}`)) {
    failures.push(`Caminho fora de python_runtime para Python portatil: ${rel}`);
    return;
  }
  if (!dirExists(portableDir)) {
    failures.push(`Diretorio do Python portatil ausente: ${toPosix(path.relative(repoRoot, portableDir))}`);
    return;
  }

  const files = collectPortableRuntimeFiles(portableDir, failures);
  const totalSize = files.reduce((total, file) => total + file.size, 0);
  const treeHash = crypto.createHash('sha256');
  for (const file of files) {
    treeHash.update(`${file.rel}\0${file.size}\0${sha256File(file.full)}\n`, 'utf8');
  }
  const actualHash = treeHash.digest('hex');
  if (files.length !== expectedCount) {
    failures.push(`Quantidade de arquivos divergente no Python portatil: esperado ${expectedCount}, encontrado ${files.length}`);
  }
  if (totalSize !== expectedSize) {
    failures.push(`Tamanho total divergente no Python portatil: esperado ${expectedSize}, encontrado ${totalSize}`);
  }
  if (actualHash !== expectedHash) {
    failures.push(`Tree SHA256 divergente no Python portatil: esperado ${expectedHash}, encontrado ${actualHash}`);
  }
  if (!fileExists(path.join(portableDir, 'python.exe'))) {
    failures.push('Executavel python_runtime/portable/python.exe ausente');
  }
}

function validateNoServiceAccountJson(baseDir, reportRoot, failures, label) {
  const files = fileExists(baseDir) ? [baseDir] : walkFiles(baseDir);
  for (const file of files) {
    if (path.extname(file).toLowerCase() !== '.json') continue;
    try {
      const payload = readJson(file);
      if (payload && payload.type === 'service_account' && payload.private_key) {
        failures.push(
          `Credencial de service account proibida ${label}: ${toPosix(path.relative(reportRoot, file))}`,
        );
      }
    } catch (_err) {
      // Arquivos JSON invalidos sao tratados pelos validadores especificos quando obrigatorios.
    }
  }
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
  if (fileExists(start)) {
    return regex.test(toPosix(path.relative(baseDir, start))) ? [start] : [];
  }
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

  for (const relDir of manifest.requiredPackagedSourceParityDirectories || []) {
    validateNoServiceAccountJson(
      path.join(repoRoot, toPosix(relDir)),
      repoRoot,
      failures,
      'na fonte empacotavel',
    );
  }
  const appBuildFiles = readJson(packageJsonPath).build?.files || [];
  for (const rel of appBuildFiles) {
    if (typeof rel !== 'string' || rel.includes('*')) continue;
    validateNoServiceAccountJson(path.join(appDir, rel), appDir, failures, 'na fonte do app.asar');
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

  const provisioning = manifest.installerProvisioning || {};
  if (provisioning.preserveExistingInstallMode === true) {
    if (!build.nsis || build.nsis.perMachine !== false) {
      failures.push('Instalador deve detectar HKLM/HKCU: nsis.perMachine precisa ser false');
    }
    if (!build.nsis || build.nsis.allowElevation !== true) {
      failures.push('Instalador deve preservar instalacoes HKLM: nsis.allowElevation precisa ser true');
    }
  }
  const nsisInclude = toPosix(build.nsis && build.nsis.include);
  if (nsisInclude !== toPosix(provisioning.nsisInclude)) {
    failures.push(`Include NSIS obrigatorio ausente: ${provisioning.nsisInclude || 'nao configurado'}`);
  } else {
    const nsisPath = path.join(appDir, nsisInclude);
    if (!fileExists(nsisPath)) {
      failures.push(`Include NSIS nao encontrado: ${nsisInclude}`);
    } else {
      const nsisText = fs.readFileSync(nsisPath, 'utf8');
      const requiredNsisTokens = [
        '!ifndef BUILD_UNINSTALLER',
        '!macro customInstall',
        'SetRegView 64',
        '${If} ${isUpdated}',
        'provisionamento adiado para o self-heal transacional',
        '${If} $installMode == "all"',
        '.venv per-user sera criada pelo self-heal transacional',
        '!insertmacro jkVcRuntimeIsCurrent',
        'atualizacao continuara sem Abort',
        '$INSTDIR\\resources\\local_app\\python_runtime\\portable\\python.exe',
        '$INSTDIR\\resources\\local_app\\scripts\\provision_python_runtime.py',
        '--source-root',
        '--target-root',
        '$APPDATA\\JK Sistema Cliente\\local_app',
        '--log-file',
        'ClearErrors',
        '${If} ${Errors}',
        '${IfNot} ${Silent}',
        'VC_redist.x64.exe',
        'ExecShellWait "runas"',
        'python-runtime-status.json',
        'installer-python-runtime.incomplete',
        'verify-jk-python-runtime.py',
        'Abort'
      ];
      for (const token of requiredNsisTokens) {
        if (!nsisText.includes(token)) failures.push(`Integracao NSIS incompleta; token ausente: ${token}`);
      }
      for (const forbidden of [
        '!macro customInit',
        '!macro customInstallMode',
        '$hasPerMachineInstallation',
        '$hasPerUserInstallation',
        '$isForceMachineInstall',
        '$isForceCurrentInstall',
        '!insertmacro setInstallModePerUser'
      ]) {
        if (nsisText.includes(forbidden)) {
          failures.push(`Include NSIS nao pode forcar o modo de instalacao existente: ${forbidden}`);
        }
      }
      const updateDeferredLog = nsisText.indexOf('INFO: atualizacao detectada; provisionamento adiado');
      const updateGuardStart = nsisText.lastIndexOf('${If} ${isUpdated}', updateDeferredLog);
      const updateGuardElse = nsisText.indexOf('${Else}', updateGuardStart);
      const firstProvisioningStep = nsisText.indexOf(
        '${IfNot} ${FileExists} "$INSTDIR\\resources\\local_app\\python_runtime\\portable\\python.exe"',
        updateGuardStart,
      );
      if (
        updateDeferredLog < 0
        || updateGuardStart < 0
        || updateGuardElse < updateGuardStart
        || firstProvisioningStep < updateGuardElse
        || nsisText.slice(updateGuardStart, updateGuardElse).includes('Abort')
      ) {
        failures.push('Atualizacao deve adiar o provisionamento sem Abort antes do primeiro boot');
      }
      const firstVcCheck = nsisText.indexOf('!insertmacro jkVcRuntimeIsCurrent');
      if (
        provisioning.updateVcFailureIsNonFatal !== true
        || firstVcCheck < 0
        || firstVcCheck > updateGuardStart
        || !nsisText.includes('atualizacao continuara sem Abort')
      ) {
        failures.push('Atualizacao deve tentar o VC Runtime antes do adiamento e tratar falha como nao fatal');
      }
      const allUsersGuardStart = nsisText.indexOf('${If} $installMode == "all"', updateGuardElse);
      const allUsersGuardElse = nsisText.indexOf('${Else}', allUsersGuardStart);
      const allUsersDeferredBlock = allUsersGuardStart < 0 || allUsersGuardElse < allUsersGuardStart
        ? ''
        : nsisText.slice(allUsersGuardStart, allUsersGuardElse);
      if (
        provisioning.deferFreshAllUsersProvisioning !== true
        || allUsersGuardStart < 0
        || allUsersGuardElse < allUsersGuardStart
        || allUsersDeferredBlock.includes('Abort')
        || !allUsersDeferredBlock.includes('self-heal transacional')
      ) {
        failures.push('Fresh all-users deve adiar a .venv per-user para o self-heal, sem Abort no bloco adiado');
      }
      if (/ExecShellWait[^\r\n]*\s\$R\d\s*$/m.test(nsisText)) {
        failures.push('ExecShellWait nao pode declarar registrador de codigo de saida no NSIS 3.0.4.1');
      }
    }
  }

  const backendIntegrationPath = path.join(appDir, 'main', 'modules', 'backend.js');
  const backendText = fileExists(backendIntegrationPath) ? fs.readFileSync(backendIntegrationPath, 'utf8') : '';
  const requiredBackendTokens = [
    "path.join(sourceRoot, 'python_runtime', 'portable', 'python.exe')",
    "path.join(sourceRoot, 'scripts', 'provision_python_runtime.py')",
    "'--source-root', sourceRoot",
    "'--target-root', targetRoot",
    "'--log-file', logPath",
    'python-runtime-status.json',
    '.jk-venv-ready.json',
    'ensurePythonRuntimeProvisioned(bundledSourceDir, localAppDir)'
  ];
  for (const token of requiredBackendTokens) {
    if (!backendText.includes(token)) failures.push(`Autorreparo Electron incompleto; token ausente: ${token}`);
  }
  for (const forbidden of ['where python', 'where py', 'py -3.', 'BUNDLED_PYTHON_INSTALLER']) {
    if (backendText.includes(forbidden)) failures.push(`Fallback para Python global proibido no backend Electron: ${forbidden}`);
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

function validateOfflineRuntimeAtRoot(manifest, failures, baseDir, label) {
  const config = manifest.offlineRuntime || {};
  if (!Object.keys(config).length) return;

  const requirementsRel = toPosix(config.requirementsFile || 'requirements.txt');
  const wheelDirectoryRel = toPosix(config.wheelDirectory || 'python_wheels');
  const runtimeVersionsRel = toPosix(config.runtimeVersionsFile || 'runtime-versions.json');
  const portablePythonRel = toPosix(config.portablePython || 'python_runtime/portable/python.exe');
  const provisionerRel = toPosix(config.provisioner || 'scripts/provision_python_runtime.py');
  const markerPrefix = String(config.markerPrefix || '.requirements-');
  const requirementsPath = path.join(baseDir, requirementsRel);
  const wheelDirectory = path.join(baseDir, wheelDirectoryRel);

  if (!fileExists(requirementsPath)) {
    failures.push(`Requirements offline ausente em ${label}: ${requirementsRel}`);
    return;
  }
  if (!dirExists(wheelDirectory)) {
    failures.push(`Wheelhouse offline ausente em ${label}: ${wheelDirectoryRel}`);
    return;
  }
  for (const rel of [runtimeVersionsRel, portablePythonRel, provisionerRel]) {
    if (!fileExists(path.join(baseDir, rel))) failures.push(`Recurso de provisionamento ausente em ${label}: ${rel}`);
  }

  const markerBase = `${markerPrefix}${sha256File(requirementsPath)}`;
  const markerPresent = fs.readdirSync(wheelDirectory, { withFileTypes: true }).some((entry) => {
    if (!entry.isFile()) return false;
    if (entry.name === `${markerBase}.ok`) return true;
    const suffix = entry.name.slice(markerBase.length);
    return /^(?:-[a-z0-9]+)?-[a-f0-9]{64}\.ok$/i.test(suffix);
  });
  if (!markerPresent) {
    failures.push(
      `Wheelhouse offline desatualizado em ${label}: marcador ${wheelDirectoryRel}/${markerBase}[-<abi>]-<prepare-sha256>.ok ausente`,
    );
  }

  const wheelFiles = walkFiles(wheelDirectory).filter((file) => file.toLowerCase().endsWith('.whl'));
  const minimumWheels = Number(config.minimumWheels || 1);
  if (wheelFiles.length < minimumWheels) {
    failures.push(`Wheelhouse offline incompleto em ${label}: ${wheelFiles.length} wheel(s), minimo ${minimumWheels}`);
  }

  for (const pattern of config.requiredWheelPatterns || []) {
    const found = findGlob(wheelDirectory, pattern);
    if (!found.length) {
      failures.push(`Dependencia offline obrigatoria ausente em ${label}: ${wheelDirectoryRel}/${pattern}`);
    }
  }
}

function validateOfflineRuntime(manifest, failures) {
  validateOfflineRuntimeAtRoot(manifest, failures, repoRoot, 'fonte');
  if (packagedRoot) {
    validateOfflineRuntimeAtRoot(manifest, failures, path.join(packagedRoot, 'local_app'), 'pacote');
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

  for (const rel of manifest.requiredPackagedSourceParity || []) {
    const normalized = toPosix(rel);
    const sourcePath = path.join(repoRoot, normalized);
    const packagedPath = path.join(packagedRoot, 'local_app', normalized);
    if (!fileExists(sourcePath)) {
      failures.push(`Fonte obrigatoria para paridade ausente: ${normalized}`);
      continue;
    }
    if (!fileExists(packagedPath)) {
      failures.push(`Arquivo de paridade ausente no pacote: local_app/${normalized}`);
      continue;
    }
    if (!filesEqual(sourcePath, packagedPath)) {
      failures.push(`Arquivo desatualizado no pacote: local_app/${normalized}`);
    }
  }

  const rootExtensions = new Set(
    (manifest.requiredPackagedRootFileExtensions || []).map((value) => String(value || '').toLowerCase())
  );
  if (rootExtensions.size) {
    for (const entry of fs.readdirSync(repoRoot, { withFileTypes: true })) {
      if (!entry.isFile() || !rootExtensions.has(path.extname(entry.name).toLowerCase())) continue;
      const sourcePath = path.join(repoRoot, entry.name);
      const packagedPath = path.join(packagedRoot, 'local_app', entry.name);
      if (!fileExists(packagedPath)) {
        failures.push(`Arquivo raiz ausente no pacote: local_app/${entry.name}`);
      } else if (!filesEqual(sourcePath, packagedPath)) {
        failures.push(`Arquivo raiz desatualizado no pacote: local_app/${entry.name}`);
      }
    }
  }

  for (const relDir of manifest.requiredPackagedSourceParityDirectories || []) {
    const normalizedDir = toPosix(relDir);
    const sourceDir = path.join(repoRoot, normalizedDir);
    const sourceFiles = walkFiles(sourceDir).filter((file) => {
      const relative = toPosix(path.relative(sourceDir, file));
      return !relative.includes('__pycache__/') && !relative.endsWith('.pyc');
    });
    if (!sourceFiles.length) {
      failures.push(`Diretorio de paridade ausente ou vazio: ${normalizedDir}`);
      continue;
    }
    for (const sourcePath of sourceFiles) {
      const relative = toPosix(path.relative(repoRoot, sourcePath));
      const packagedPath = path.join(packagedRoot, 'local_app', relative);
      if (!fileExists(packagedPath)) {
        failures.push(`Arquivo de paridade ausente no pacote: local_app/${relative}`);
      } else if (!filesEqual(sourcePath, packagedPath)) {
        failures.push(`Arquivo desatualizado no pacote: local_app/${relative}`);
      }
    }
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

  validateNoServiceAccountJson(packagedRoot, packagedRoot, failures, 'no pacote');

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
  validateContextBundle(failures);
  validateOfflineRuntime(manifest, failures);
  validatePackagedOutput(manifest, failures);
  const offlineRoot = packagedRoot ? path.join(packagedRoot, 'local_app') : repoRoot;
  validateOfflineArtifacts(offlineRoot, failures, !packagedRoot);
  validateOfflineResolution(offlineRoot, failures);
  if (!failures.length && deepRuntime) {
    if (!packagedRoot) {
      failures.push('Smoke test offline profundo requer --packaged <diretorio-resources>');
    } else {
      validateDeepRuntime(packagedRoot, failures);
    }
  }

  if (failures.length) {
    console.error('[installer-check] FALHOU');
    for (const failure of failures) console.error(`- ${failure}`);
    process.exit(1);
  }

  const target = packagedRoot ? ` e pacote ${packagedRoot}` : '';
  console.log(`[installer-check] OK - recursos obrigatorios validados${target}.`);
}

main();
