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
  const checkOnly = process.argv.slice(2).includes('--check');
  const manifest = readJson(manifestPath);
  const pairs = manifest.mirrorPairs || [];
  let copied = 0;
  let skipped = 0;
  const failures = [];

  if (checkOnly && (!Array.isArray(manifest.mirrorPairs) || manifest.mirrorPairs.length === 0)) {
    console.error('[static-source] FALHOU');
    console.error('- mirrorPairs deve ser uma lista nao vazia no modo --check.');
    process.exit(1);
  }

  for (const pair of pairs) {
    const { canonical, legacy } = resolveMirrorPairPolicy(manifest, pair);
    const canonicalPath = path.join(repoRoot, canonical);
    const legacyPath = path.join(repoRoot, legacy);

    if (!isFile(canonicalPath)) {
      failures.push(`Fonte oficial ausente: ${canonical}`);
      continue;
    }

    const next = fs.readFileSync(canonicalPath);
    if (!isFile(legacyPath)) {
      if (checkOnly) {
        failures.push(`Espelho ausente: ${legacy}`);
        continue;
      }
      fs.mkdirSync(path.dirname(legacyPath), { recursive: true });
      fs.copyFileSync(canonicalPath, legacyPath);
      copied += 1;
      console.log(`[static-source] ${canonical} -> ${legacy}`);
      continue;
    }

    const current = fs.readFileSync(legacyPath);
    if (current.equals(next)) {
      skipped += 1;
      continue;
    }

    if (checkOnly) {
      failures.push(`Espelho divergente: ${legacy} (fonte oficial: ${canonical})`);
      continue;
    }

    fs.mkdirSync(path.dirname(legacyPath), { recursive: true });
    fs.copyFileSync(canonicalPath, legacyPath);
    copied += 1;
    console.log(`[static-source] ${canonical} -> ${legacy}`);
  }

  if (failures.length) {
    console.error('[static-source] FALHOU');
    for (const failure of failures) console.error(`- ${failure}`);
    process.exit(1);
  }

  if (checkOnly) {
    console.log(`[static-source] OK - ${skipped} espelho(s) conferido(s), nenhuma alteracao realizada.`);
  } else {
    console.log(`[static-source] OK - ${copied} espelho(s) atualizado(s), ${skipped} ja alinhado(s).`);
  }
}

main();
