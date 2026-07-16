const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const expectedNode = '24.13.0';
const expectedNpm = '11.6.2';
const runtimeVersions = JSON.parse(fs.readFileSync(path.join(root, 'runtime-versions.json'), 'utf8').replace(/^\uFEFF/, ''));
const pythonRuntime = runtimeVersions.python || {};
const expectedPython = String(pythonRuntime.version || '').trim();
const expectedPythonMinor = String(pythonRuntime.minor || '').trim();
const expectedPythonImplementation = String(pythonRuntime.implementation || '').trim();
const expectedPythonAbi = String(pythonRuntime.abi || '').trim();
const expectedPythonInstaller = String(pythonRuntime.windowsInstaller || '').trim();
const expectedPythonInstallerSize = Number(pythonRuntime.windowsInstallerSize);
const expectedPythonInstallerSha256 = String(pythonRuntime.windowsInstallerSha256 || '').trim().toLowerCase();
const expectedPythonPortable = pythonRuntime.windowsPortable || {};
const expectedPythonImage = String(pythonRuntime.dockerImage || '').trim();
const expectedPythonImageDigest = String(pythonRuntime.dockerDigest || '').trim();
const projects = [
  '.',
  'electron_app',
  'cloudflare/whatsapp-gateway',
  'scripts/electron_favoritos_search_test_app',
];
const dependencyGroups = ['dependencies', 'devDependencies', 'optionalDependencies'];
const failures = [];

function readText(relativePath) {
  return fs.readFileSync(path.join(root, relativePath), 'utf8').replace(/^\uFEFF/, '');
}

function readJson(relativePath) {
  return JSON.parse(readText(relativePath));
}

function sameObject(left, right) {
  const normalize = (value) => Object.fromEntries(Object.entries(value || {}).sort(([a], [b]) => a.localeCompare(b)));
  return JSON.stringify(normalize(left)) === JSON.stringify(normalize(right));
}

function verifyNodeProject(relativeDir) {
  const label = relativeDir === '.' ? 'raiz' : relativeDir;
  const packagePath = path.posix.join(relativeDir.replaceAll('\\', '/'), 'package.json');
  const lockPath = path.posix.join(relativeDir.replaceAll('\\', '/'), 'package-lock.json');
  if (!fs.existsSync(path.join(root, lockPath))) {
    failures.push(`${label}: package-lock.json ausente`);
    return;
  }
  const manifest = readJson(packagePath);
  const lock = readJson(lockPath);
  const lockRoot = lock.packages && lock.packages[''];
  if (lock.lockfileVersion !== 3) failures.push(`${label}: lockfileVersion deve ser 3`);
  if (!lockRoot) failures.push(`${label}: entrada raiz ausente no lockfile`);
  if (manifest.packageManager !== `npm@${expectedNpm}`) failures.push(`${label}: packageManager deve ser npm@${expectedNpm}`);
  if (manifest.engines?.node !== expectedNode) failures.push(`${label}: engines.node deve ser ${expectedNode}`);
  if (manifest.engines?.npm !== expectedNpm) failures.push(`${label}: engines.npm deve ser ${expectedNpm}`);

  for (const group of dependencyGroups) {
    const declared = manifest[group] || {};
    if (lockRoot && !sameObject(declared, lockRoot[group] || {})) {
      failures.push(`${label}: ${group} diverge do package-lock.json`);
    }
    for (const [name, spec] of Object.entries(declared)) {
      if (!/^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/.test(spec)) {
        failures.push(`${label}: ${name} nao usa versao exata (${spec})`);
        continue;
      }
      const locked = lock.packages && lock.packages[`node_modules/${name}`];
      if (!locked || locked.version !== spec) {
        failures.push(`${label}: ${name}@${spec} nao corresponde ao lock (${locked?.version || 'ausente'})`);
      }
    }
  }
}

function verifyHashedPythonLock(inputPath, lockPath, label) {
  const input = readText(inputPath);
  const lock = readText(lockPath);
  if (!lock.includes('--require-hashes')) failures.push(`${label}: ${lockPath} nao exige hashes`);
  if (!lock.includes('--only-binary=:all:')) failures.push(`${label}: ${lockPath} nao restringe a wheels`);
  if (!lock.startsWith('# Gerado por scripts/lock_python_dependencies.py.')) {
    failures.push(`${label}: cabecalho do lock gerado ausente`);
  }
  const blocks = lock.split(/\r?\n\r?\n/).slice(1).filter((block) => block.trim());
  const packageBlocks = blocks.filter((block) => /^[a-z0-9][a-z0-9-]*==/m.test(block));
  for (const block of packageBlocks) {
    const first = block.split(/\r?\n/, 1)[0];
    if (!/^[a-z0-9][a-z0-9-]*==[^\s;]+(?:\s*;\s*sys_platform\s*==\s*"(?:win32|linux)")?\s*\\$/.test(first)) {
      failures.push(`${label}: requisito sem versao exata: ${first}`);
    }
    if (!/--hash=sha256:[a-f0-9]{64}/.test(block)) failures.push(`${label}: requisito sem hash: ${first}`);
  }
  const directRequirements = input
    .split(/\r?\n/)
    .map((line) => line.replace(/\s+#.*$/, '').trim())
    .filter((line) => line && !line.startsWith('#')).length;
  if (packageBlocks.length < directRequirements) {
    failures.push(`${label}: lock incompleto (${packageBlocks.length} pacotes para ${directRequirements} entradas diretas)`);
  }
  return packageBlocks.length;
}

function verifyPythonLock() {
  if (runtimeVersions.schemaVersion !== 1) failures.push('Python: runtime-versions.json deve usar schemaVersion 1');
  if (!/^\d+\.\d+\.\d+$/.test(expectedPython)) failures.push('Python: version invalida em runtime-versions.json');
  const pythonParts = expectedPython.split('.');
  const derivedMinor = pythonParts.length === 3 ? `${pythonParts[0]}.${pythonParts[1]}` : '';
  const derivedAbi = pythonParts.length === 3 ? `cp${pythonParts[0]}${pythonParts[1]}` : '';
  if (expectedPythonMinor !== derivedMinor) failures.push(`Python: minor deve ser ${derivedMinor}`);
  if (expectedPythonImplementation !== 'cp') failures.push('Python: implementation deve ser cp');
  if (expectedPythonAbi !== derivedAbi) failures.push(`Python: ABI deve ser ${derivedAbi}`);
  if (expectedPythonInstaller !== `python-${expectedPython}-amd64.exe`) {
    failures.push(`Python: windowsInstaller deve ser python-${expectedPython}-amd64.exe`);
  }
  if (!Number.isSafeInteger(expectedPythonInstallerSize) || expectedPythonInstallerSize < 1) {
    failures.push('Python: windowsInstallerSize deve ser um inteiro positivo');
  }
  if (!/^[a-f0-9]{64}$/.test(expectedPythonInstallerSha256)) {
    failures.push('Python: windowsInstallerSha256 deve conter um SHA256 valido');
  }
  if (
    String(expectedPythonPortable.path || '') !== 'portable'
    || !Number.isInteger(Number(expectedPythonPortable.fileCount))
    || Number(expectedPythonPortable.fileCount) < 1
    || !Number.isSafeInteger(Number(expectedPythonPortable.totalSize))
    || Number(expectedPythonPortable.totalSize) < 1
    || !/^[a-f0-9]{64}$/.test(String(expectedPythonPortable.treeSha256 || '').trim().toLowerCase())
  ) {
    failures.push('Python: windowsPortable deve fixar path, fileCount, totalSize e treeSha256 validos');
  }
  if (expectedPythonImage !== `python:${expectedPython}-slim-bookworm`) {
    failures.push(`Python: dockerImage deve ser python:${expectedPython}-slim-bookworm`);
  }
  if (!/^sha256:[a-f0-9]{64}$/.test(expectedPythonImageDigest)) {
    failures.push('Python: dockerDigest deve conter um digest sha256 valido');
  }
  verifyHashedPythonLock('requirements.in', 'requirements.txt', 'Python runtime');
  verifyHashedPythonLock('requirements-test.in', 'requirements-test.txt', 'Python testes');
  if (readText('.python-version').trim() !== expectedPython) failures.push(`Python: .python-version deve ser ${expectedPython}`);
  if (!readText('scripts/lock_python_dependencies.py').includes('runtime-versions.json')) {
    failures.push('Python: gerador de locks deve ler runtime-versions.json');
  }
  const installerPreparer = readText('scripts/prepare_installer_runtime.ps1');
  if (!installerPreparer.includes('runtime-versions.json') && !installerPreparer.includes(`$pythonVersion = "${expectedPython}"`)) {
    failures.push(`Python: preparador do instalador deve ler runtime-versions.json ou usar ${expectedPython}`);
  }
  for (const pinField of ['windowsInstallerSize', 'windowsInstallerSha256', 'windowsPortable']) {
    if (!installerPreparer.includes(pinField)) {
      failures.push(`Python: preparador do instalador nao fiscaliza ${pinField}`);
    }
  }
  const pinnedPortableStart = installerPreparer.indexOf('function Test-PinnedPortablePython');
  const pinnedPortableEnd = installerPreparer.indexOf('\nfunction ', pinnedPortableStart + 1);
  const pinnedPortableBody = pinnedPortableStart >= 0
    ? installerPreparer.slice(pinnedPortableStart, pinnedPortableEnd >= 0 ? pinnedPortableEnd : undefined)
    : '';
  const portableInventoryCheck = pinnedPortableBody.indexOf('Get-PortableInventory $Path');
  const portableExecutionProbe = pinnedPortableBody.indexOf('Test-PortablePython $Path');
  if (
    portableInventoryCheck < 0
    || portableExecutionProbe < 0
    || portableInventoryCheck > portableExecutionProbe
  ) {
    failures.push('Python: preparador deve validar o tree SHA do runtime portatil antes de executar python.exe');
  }
  const provisioner = readText('scripts/provision_python_runtime.py');
  for (const pinField of ['windowsInstallerSize', 'windowsInstallerSha256', 'windowsPortable']) {
    if (!provisioner.includes(pinField)) {
      failures.push(`Python: provisionador nao fiscaliza ${pinField}`);
    }
  }
  const dockerfile = readText('Dockerfile');
  const expectedDockerFrom = `FROM ${expectedPythonImage}@${expectedPythonImageDigest}`;
  if (!dockerfile.startsWith(`${expectedDockerFrom}\n`) && !dockerfile.startsWith(`${expectedDockerFrom}\r\n`)) {
    failures.push(`Python: Dockerfile deve fixar ${expectedDockerFrom}`);
  }
  if (!/pip install[^\r\n]*--require-hashes[^\r\n]*-r requirements\.txt/.test(dockerfile)) {
    failures.push('Python: Dockerfile deve instalar o lock com --require-hashes');
  }
}

function verifyInstallCommands() {
  const deterministicNodeFiles = [
    'Executar.bat',
    'iniciar_servidor.bat',
    'iniciar_servidor_dev.bat',
    'GerarExecutavel.bat',
    'cloudflare/whatsapp-gateway/README.md',
  ];
  for (const file of deterministicNodeFiles) {
    if (/\bnpm install\b/i.test(readText(file))) failures.push(`${file}: use npm ci em vez de npm install`);
  }
  const pythonLaunchers = ['iniciar_servidor.bat', 'iniciar_servidor_dev.bat'];
  for (const file of pythonLaunchers) {
    const content = readText(file);
    if (/pip install[^\r\n]*--upgrade\s+(?:pip|watchfiles)/i.test(content)) {
      failures.push(`${file}: atualizacao Python sem lock detectada`);
    }
    if (!content.includes('runtime-versions.json')) {
      failures.push(`${file}: deve ler runtime-versions.json`);
    }
    if (!content.includes("platform.python_version() == '%PYTHON_VERSION%'")) {
      failures.push(`${file}: deve exigir a versao Python configurada`);
    }
  }
  const backendLauncher = readText('electron_app/main/modules/backend.js');
  if (/pip install[^\r\n]*--upgrade\s+(?:pip|watchfiles)/i.test(backendLauncher)) {
    failures.push('electron_app/main/modules/backend.js: atualizacao Python sem lock detectada');
  }
  if (!backendLauncher.includes('provision_python_runtime.py')) {
    failures.push('electron_app/main/modules/backend.js: deve delegar a validacao do runtime ao provisionador canonico');
  }
  if (!/\bcall\s+"?%~dp0iniciar_servidor\.bat"?/i.test(readText('Executar.bat'))) {
    failures.push('Executar.bat: deve iniciar o backend pelo bootstrap Python canonico');
  }
  const qualityWorkflow = readText('.github/workflows/quality-gate.yml');
  if (!qualityWorkflow.includes('npm.cmd test')) failures.push('Quality Gate: deve executar npm.cmd test');
  if (!qualityWorkflow.includes('-r requirements-test.txt')) failures.push('Quality Gate: deve instalar requirements-test.txt');
  if (!qualityWorkflow.includes('--require-hashes')) failures.push('Quality Gate: dependencias Python devem exigir hashes');
}

for (const project of projects) verifyNodeProject(project);
verifyPythonLock();
verifyInstallCommands();

if (process.version !== `v${expectedNode}`) failures.push(`Runtime Node atual deve ser v${expectedNode}, encontrado ${process.version}`);
if (readText('.nvmrc').trim() !== expectedNode) failures.push(`.nvmrc deve ser ${expectedNode}`);

if (failures.length) {
  console.error('[dependency-lock] FALHOU');
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

const runtimePythonCount = readText('requirements.txt').match(/^[a-z0-9][a-z0-9-]*==/gm)?.length || 0;
const testPythonCount = readText('requirements-test.txt').match(/^[a-z0-9][a-z0-9-]*==/gm)?.length || 0;
console.log(`[dependency-lock] OK - ${projects.length} lockfiles Node, ${runtimePythonCount} pacotes Python de runtime e ${testPythonCount} de teste controlados.`);
