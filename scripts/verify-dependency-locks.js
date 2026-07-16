const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const expectedNode = '24.13.0';
const expectedNpm = '11.6.2';
const expectedPython = '3.11.9';
const expectedPythonImageDigest = 'sha256:8fb099199b9f2d70342674bd9dbccd3ed03a258f26bbd1d556822c6dfc60c317';
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

function verifyPythonLock() {
  const input = readText('requirements.in');
  const lock = readText('requirements.txt');
  if (!lock.includes('--require-hashes')) failures.push('Python: requirements.txt nao exige hashes');
  if (!lock.includes('--only-binary=:all:')) failures.push('Python: requirements.txt nao restringe a wheels');
  if (!lock.startsWith('# Gerado por scripts/lock_python_dependencies.py.')) {
    failures.push('Python: cabecalho do lock gerado ausente');
  }
  const blocks = lock.split(/\r?\n\r?\n/).slice(1).filter((block) => block.trim());
  const packageBlocks = blocks.filter((block) => /^[a-z0-9][a-z0-9-]*==/m.test(block));
  for (const block of packageBlocks) {
    const first = block.split(/\r?\n/, 1)[0];
    if (!/^[a-z0-9][a-z0-9-]*==[^\s;]+(?:\s*;\s*sys_platform\s*==\s*"(?:win32|linux)")?\s*\\$/.test(first)) {
      failures.push(`Python: requisito sem versao exata: ${first}`);
    }
    if (!/--hash=sha256:[a-f0-9]{64}/.test(block)) failures.push(`Python: requisito sem hash: ${first}`);
  }
  const directRequirements = input
    .split(/\r?\n/)
    .map((line) => line.replace(/\s+#.*$/, '').trim())
    .filter((line) => line && !line.startsWith('#')).length;
  if (packageBlocks.length < directRequirements) {
    failures.push(`Python: lock incompleto (${packageBlocks.length} pacotes para ${directRequirements} entradas diretas)`);
  }
  if (readText('.python-version').trim() !== expectedPython) failures.push(`Python: .python-version deve ser ${expectedPython}`);
  if (!readText('scripts/prepare_installer_runtime.ps1').includes(`$pythonVersion = "${expectedPython}"`)) {
    failures.push(`Python: preparador do instalador deve usar ${expectedPython}`);
  }
  const dockerfile = readText('Dockerfile');
  const expectedImage = `FROM python:${expectedPython}-slim-bookworm@${expectedPythonImageDigest}`;
  if (!dockerfile.startsWith(`${expectedImage}\n`) && !dockerfile.startsWith(`${expectedImage}\r\n`)) {
    failures.push(`Python: Dockerfile deve fixar ${expectedImage}`);
  }
  if (!/pip install[^\r\n]*--require-hashes[^\r\n]*-r requirements\.txt/.test(dockerfile)) {
    failures.push('Python: Dockerfile deve instalar o lock com --require-hashes');
  }
}

function verifyInstallCommands() {
  const deterministicNodeFiles = ['Executar.bat', 'GerarExecutavel.bat', 'cloudflare/whatsapp-gateway/README.md'];
  for (const file of deterministicNodeFiles) {
    if (/\bnpm install\b/i.test(readText(file))) failures.push(`${file}: use npm ci em vez de npm install`);
  }
  const pythonLaunchers = ['iniciar_servidor.bat', 'iniciar_servidor_dev.bat', 'electron_app/main/modules/backend.js'];
  for (const file of pythonLaunchers) {
    const content = readText(file);
    if (/pip install[^\r\n]*--upgrade\s+(?:pip|watchfiles)/i.test(content)) {
      failures.push(`${file}: atualizacao Python sem lock detectada`);
    }
  }
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

console.log(`[dependency-lock] OK - ${projects.length} lockfiles Node e ${readText('requirements.txt').match(/^[a-z0-9][a-z0-9-]*==/gm)?.length || 0} pacotes Python controlados.`);
