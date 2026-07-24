'use strict';

const assert = require('assert');
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const sourceScript = path.resolve(__dirname, '..', 'scripts', 'sync-static-html-mirrors.js');

function sha256(buffer) {
  return crypto.createHash('sha256').update(buffer).digest('hex');
}

function listFiles(root, current = root) {
  const files = [];
  for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
    const absolute = path.join(current, entry.name);
    if (entry.isDirectory()) files.push(...listFiles(root, absolute));
    else if (entry.isFile()) files.push(path.relative(root, absolute).replace(/\\/g, '/'));
  }
  return files.sort();
}

function snapshot(root) {
  return Object.fromEntries(listFiles(root).map(relative => {
    const absolute = path.join(root, relative);
    const stat = fs.statSync(absolute);
    return [relative, { sha256: sha256(fs.readFileSync(absolute)), mtimeMs: stat.mtimeMs }];
  }));
}

function createFixture({
  canonicalContent = 'conteudo oficial\r\n',
  legacyContent = canonicalContent,
  mirrorPairs = [{ source: 'legacy/tela.html', mirror: 'static/tela.html' }],
  omitMirrorPairs = false,
} = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'jk-static-check-'));
  fs.mkdirSync(path.join(root, 'scripts'), { recursive: true });
  fs.mkdirSync(path.join(root, 'electron_app'), { recursive: true });
  fs.mkdirSync(path.join(root, 'static'), { recursive: true });
  fs.copyFileSync(sourceScript, path.join(root, 'scripts', 'sync-static-html-mirrors.js'));
  const manifest = { htmlSourcePolicy: { canonicalDirectory: 'static' } };
  if (!omitMirrorPairs) manifest.mirrorPairs = mirrorPairs;
  fs.writeFileSync(path.join(root, 'electron_app', 'installer-required-resources.json'), JSON.stringify(manifest), 'utf8');
  if (canonicalContent !== null) {
    fs.writeFileSync(path.join(root, 'static', 'tela.html'), canonicalContent, 'utf8');
  }
  if (legacyContent !== null) {
    fs.mkdirSync(path.join(root, 'legacy'), { recursive: true });
    fs.writeFileSync(path.join(root, 'legacy', 'tela.html'), legacyContent, 'utf8');
  }
  const fixedTime = new Date('2020-01-02T03:04:05.000Z');
  for (const relative of listFiles(root)) fs.utimesSync(path.join(root, relative), fixedTime, fixedTime);
  return root;
}

function runCheck(root) {
  return spawnSync(process.execPath, ['scripts/sync-static-html-mirrors.js', '--check'], {
    cwd: root,
    encoding: 'utf8',
  });
}

function runSync(root) {
  return spawnSync(process.execPath, ['scripts/sync-static-html-mirrors.js'], {
    cwd: root,
    encoding: 'utf8',
  });
}

function verifyCase(options, expectedStatus, expectedMessage) {
  const root = createFixture(options);
  try {
    const before = snapshot(root);
    const result = runCheck(root);
    const after = snapshot(root);
    assert.strictEqual(result.status, expectedStatus, result.stderr || result.stdout);
    assert.match(`${result.stdout}\n${result.stderr}`, expectedMessage);
    assert.deepStrictEqual(after, before, '--check nao pode alterar bytes, mtimes nem criar arquivos');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

function verifyLegacySync(legacyContent) {
  const root = createFixture({ legacyContent });
  try {
    const canonicalPath = path.join(root, 'static', 'tela.html');
    const legacyPath = path.join(root, 'legacy', 'tela.html');
    const canonicalBefore = snapshot(root)['static/tela.html'];
    const result = runSync(root);
    assert.strictEqual(result.status, 0, result.stderr || result.stdout);
    assert.deepStrictEqual(fs.readFileSync(legacyPath), fs.readFileSync(canonicalPath));
    assert.deepStrictEqual(snapshot(root)['static/tela.html'], canonicalBefore, 'sincronizacao nao altera a fonte oficial');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

verifyCase({}, 0, /nenhuma alteracao realizada/);
verifyCase({ legacyContent: null }, 1, /Espelho ausente: legacy\/tela\.html/);
verifyCase({ legacyContent: 'conteudo divergente\r\n' }, 1, /Espelho divergente: legacy\/tela\.html/);
verifyCase({ canonicalContent: null }, 1, /Fonte oficial ausente: static\/tela\.html/);
verifyCase({ omitMirrorPairs: true }, 1, /mirrorPairs deve ser uma lista nao vazia/);
verifyCase({ mirrorPairs: {} }, 1, /mirrorPairs deve ser uma lista nao vazia/);
verifyCase({ mirrorPairs: [] }, 1, /mirrorPairs deve ser uma lista nao vazia/);
verifyLegacySync(null);
verifyLegacySync('conteudo divergente\r\n');

console.log('sync-static-html-mirrors --check: contrato read-only aprovado.');
