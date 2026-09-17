'use strict';

const fs = require('fs');
const path = require('path');
const { validateLocalAppManifestAtRoot } = require('./local-app-manifest');
const { loadRuntimeContract } = require('./installer-runtime-contract');

const appDir = path.resolve(__dirname, '..');
const repoRoot = path.resolve(appDir, '..');
const args = process.argv.slice(2);
const packagedIndex = args.indexOf('--packaged');
const packagedRoot = packagedIndex >= 0 ? path.resolve(process.cwd(), args[packagedIndex + 1] || '') : null;

function readJson(candidate) {
  return JSON.parse(fs.readFileSync(candidate, 'utf8').replace(/^\uFEFF/, ''));
}

function fileExists(candidate) {
  try { return fs.statSync(candidate).isFile(); } catch (_err) { return false; }
}

function directoryExists(candidate) {
  try { return fs.statSync(candidate).isDirectory(); } catch (_err) { return false; }
}

function filesEqual(left, right) {
  if (!fileExists(left) || !fileExists(right)) return false;
  const a = fs.statSync(left);
  const b = fs.statSync(right);
  return a.size === b.size && fs.readFileSync(left).equals(fs.readFileSync(right));
}

function toPosix(value) {
  return String(value || '').replace(/\\/g, '/').replace(/^\.\//, '');
}

function walk(root) {
  if (!directoryExists(root)) return [];
  const files = [];
  const stack = [root];
  while (stack.length) {
    const current = stack.pop();
    const currentStat = fs.lstatSync(current);
    if (currentStat.isSymbolicLink()) throw new Error(`Link proibido no pacote update: ${current}`);
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const candidate = path.join(current, entry.name);
      const stat = fs.lstatSync(candidate);
      if (stat.isSymbolicLink()) throw new Error(`Link proibido no pacote update: ${candidate}`);
      if (stat.isDirectory()) stack.push(candidate);
      else if (stat.isFile()) files.push(candidate);
      else throw new Error(`Tipo especial proibido no pacote update: ${candidate}`);
    }
  }
  return files;
}

const failures = [];
let contract;
try {
  ({ contract } = loadRuntimeContract(repoRoot));
} catch (err) {
  failures.push(err && err.message ? err.message : String(err));
}

const pkg = readJson(path.join(appDir, 'package.json'));
const updateConfig = require('./electron-builder-update-config');
const localResource = (updateConfig.extraResources || []).find(entry => toPosix(entry?.to) === 'local_app');
const filters = Array.isArray(localResource?.filter) ? localResource.filter.map(toPosix) : [];
for (const required of [
  '!python_runtime/**',
  '!python_wheels/**',
  '!black_jhon_runtime/**',
  '!prerequisites/**',
]) {
  if (!filters.includes(required)) failures.push(`exclusao ausente na configuracao update: ${required}`);
}
for (const entry of updateConfig.extraResources || []) {
  const from = toPosix(entry?.from);
  if (from.includes('.installer_runtime/black_jhon') || from.includes('.installer_runtime/prerequisites')) {
    failures.push(`payload pesado presente na configuracao update: ${from}`);
  }
}
if (String(updateConfig?.directories?.output || '') !== 'dist-client-update') {
  failures.push('saida do perfil update deve ser dist-client-update');
}
if (!String(updateConfig?.win?.artifactName || '').includes('-Update-')) {
  failures.push('nome do artefato update deve ser distinto do instalador completo');
}

if (packagedRoot) {
  const localApp = path.join(packagedRoot, 'local_app');
  if (!directoryExists(localApp)) failures.push('local_app ausente no pacote update');
  const profilePath = path.join(localApp, 'installer-package-profile.json');
  if (!fileExists(profilePath)) {
    failures.push('installer-package-profile.json ausente no pacote update');
  } else {
    const profile = readJson(profilePath);
    if (profile.profile !== 'update' || String(profile.app_version || '') !== String(pkg.version || '')) {
      failures.push('perfil/versao do pacote update invalido');
    }
  }
  const packagedContract = path.join(localApp, 'installer-runtime.lock.json');
  if (!filesEqual(path.join(repoRoot, 'installer-runtime.lock.json'), packagedContract)) {
    failures.push('contrato de runtime ausente ou divergente no pacote update');
  }
  for (const payload of ['python_runtime', 'python_wheels', 'black_jhon_runtime', 'prerequisites']) {
    if (directoryExists(path.join(localApp, payload))) failures.push(`payload pesado proibido no update: ${payload}`);
  }
  const manifestFailures = validateLocalAppManifestAtRoot(localApp, pkg.version);
  failures.push(...manifestFailures.map(item => `local-app-manifest: ${item}`));
  const packageContract = readJson(path.join(appDir, 'installer-required-resources.json'));
  for (const relative of packageContract.requiredPackagedSourceParity || []) {
    const normalized = toPosix(relative);
    const source = path.join(repoRoot, normalized);
    const packaged = path.join(localApp, normalized);
    if (!filesEqual(source, packaged)) failures.push(`arquivo gerenciado ausente/divergente no update: ${normalized}`);
  }
  if (!fileExists(path.join(packagedRoot, 'app.asar'))) failures.push('app.asar ausente no pacote update');
  for (const candidate of walk(localApp)) {
    const relative = toPosix(path.relative(localApp, candidate));
    const topLevel = relative.split('/')[0].toLowerCase();
    if (['info', 'logs', 'backups', 'contextvault', 'context_hub', 'sku'].includes(topLevel)) {
      failures.push(`dado persistente proibido no update: ${relative}`);
    }
    if (/service[-_]?account|\.jkcred$|(^|\/)\.env/i.test(relative)) {
      failures.push(`credencial proibida no update: ${relative}`);
    }
  }
}

if (failures.length) {
  process.stderr.write(`Pacote de atualizacao reprovado:\n- ${failures.join('\n- ')}\n`);
  process.exit(1);
}
process.stdout.write(`Pacote de atualizacao OK (${packagedRoot ? 'empacotado' : 'fonte'}; runtime ${contract.runtime_id})\n`);
