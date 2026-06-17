const fs = require('fs');
const path = require('path');

const repoRoot = path.resolve(__dirname, '..');
const manifestPath = path.join(repoRoot, 'electron_app', 'installer-required-resources.json');

function toPosix(value) {
  return String(value || '').replace(/\\/g, '/').replace(/\/+/g, '/').replace(/^\.\//, '');
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function isFile(file) {
  try {
    return fs.statSync(file).isFile();
  } catch (_err) {
    return false;
  }
}

function resolveMirrorPairPolicy(manifest, pair) {
  const policy = manifest.htmlSourcePolicy || {};
  const canonicalDir = toPosix(policy.canonicalDirectory || 'static');
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

function main() {
  const manifest = readJson(manifestPath);
  const pairs = manifest.mirrorPairs || [];
  let copied = 0;
  let skipped = 0;
  const failures = [];

  for (const pair of pairs) {
    const { canonical, legacy } = resolveMirrorPairPolicy(manifest, pair);
    const canonicalPath = path.join(repoRoot, canonical);
    const legacyPath = path.join(repoRoot, legacy);

    if (!isFile(canonicalPath)) {
      failures.push(`Fonte oficial ausente: ${canonical}`);
      continue;
    }

    fs.mkdirSync(path.dirname(legacyPath), { recursive: true });
    const current = isFile(legacyPath) ? fs.readFileSync(legacyPath) : null;
    const next = fs.readFileSync(canonicalPath);
    if (current && current.equals(next)) {
      skipped += 1;
      continue;
    }

    fs.copyFileSync(canonicalPath, legacyPath);
    copied += 1;
    console.log(`[static-source] ${canonical} -> ${legacy}`);
  }

  if (failures.length) {
    console.error('[static-source] FALHOU');
    for (const failure of failures) console.error(`- ${failure}`);
    process.exit(1);
  }

  console.log(`[static-source] OK - ${copied} espelho(s) atualizado(s), ${skipped} ja alinhado(s).`);
}

main();
