#!/usr/bin/env node

const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const readline = require('readline');

const FORMAT = 'jk-sistema-credentials';
const FORMAT_VERSION = 1;
const KDF_ITERATIONS = 310000;
const MAX_FILE_BYTES = 10 * 1024 * 1024;

const DEFAULT_SECRET_FILES = new Set([
  'auth_users.db',
  'bling_conf.json',
  'bling_contas.json',
  'client_id_atual.json',
  'config_sheet.json',
  'configuracoes_globais.json',
  'credentials.json',
  'gemini_api_key.txt',
  'groq_api_key.txt',
  'integracoes.json',
  'jwt_secret.key',
  'lojas_config.json',
  'lojas_virtuais_map.json',
  'openai_api_key.txt',
  'perguntas_pos_venda_lojas_config.json',
  'promo_automacao_api.json',
  'siscomex_config.json',
  'usuarios_cache.json',
  'usuarios_local.json',
  'vertex_agent_api_key.txt',
]);

const DEFAULT_ROOT_FILES = [
  '.env',
  '.env.local',
  '.env.production',
  '.env.production.local',
];

const DEFAULT_ROOT_GLOBS = [
  /^jkjkjk-.*\.json$/i,
  /service[-_ ]?account.*\.json$/i,
  /credentials.*\.json$/i,
];

const INFO_EXCLUDED_PARTS = new Set([
  'auditorias_impostos',
  'electron_user_data',
  'promo_jobs',
  'promo_worker_jobs',
  'tmp',
  '__pycache__',
]);

const SECRET_NAME_PATTERN = /(api[_-]?key|token|secret|credential|credentials|oauth|auth)/i;

function printUsage() {
  console.log(`Uso:
  node scripts/credenciais.js list [--include caminho]
  node scripts/credenciais.js export [--out arquivo.jkcred] [--pass senha] [--force] [--include caminho]
  node scripts/credenciais.js import --in arquivo.jkcred [--pass senha] [--force] [--no-backup]

Exemplos:
  node scripts/credenciais.js list
  node scripts/credenciais.js export --out credenciais-jk.jkcred
  node scripts/credenciais.js import --in credenciais-jk.jkcred

Tambem e possivel definir a senha pela variavel JK_CREDENTIALS_PASSWORD.`);
}

function parseArgs(argv) {
  const result = { _: [] };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (!arg.startsWith('--')) {
      result._.push(arg);
      continue;
    }

    const key = arg.slice(2);
    if (key === 'force' || key === 'no-backup' || key === 'dry-run') {
      result[key] = true;
      continue;
    }

    const value = argv[index + 1];
    if (!value || value.startsWith('--')) {
      throw new Error(`Parametro --${key} precisa de valor.`);
    }
    index += 1;

    if (key === 'include') {
      result.include = result.include || [];
      result.include.push(value);
    } else {
      result[key] = value;
    }
  }
  return result;
}

function toPortablePath(filePath) {
  return filePath.split(path.sep).join('/');
}

function normalizePortablePath(filePath) {
  return String(filePath || '').replace(/\\/g, '/').replace(/^\.\/+/, '');
}

function resolveInsideRoot(rootDir, portablePath) {
  const normalized = normalizePortablePath(portablePath);
  if (!normalized || normalized.startsWith('/') || normalized.includes('\0')) {
    throw new Error(`Caminho invalido no pacote: ${portablePath}`);
  }
  if (/^[a-zA-Z]:/.test(normalized)) {
    throw new Error(`Caminho absoluto bloqueado no pacote: ${portablePath}`);
  }

  const resolved = path.resolve(rootDir, normalized);
  const rootResolved = path.resolve(rootDir);
  const relative = path.relative(rootResolved, resolved);
  if (relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new Error(`Caminho fora do projeto bloqueado: ${portablePath}`);
  }
  return resolved;
}

function statIfFile(filePath) {
  try {
    const stat = fs.statSync(filePath);
    return stat.isFile() ? stat : null;
  } catch (_err) {
    return null;
  }
}

function walk(dirPath, visitor) {
  let entries;
  try {
    entries = fs.readdirSync(dirPath, { withFileTypes: true });
  } catch (_err) {
    return;
  }

  for (const entry of entries) {
    const fullPath = path.join(dirPath, entry.name);
    if (entry.isDirectory()) {
      const shouldContinue = visitor(fullPath, entry);
      if (shouldContinue !== false) {
        walk(fullPath, visitor);
      }
      continue;
    }
    if (entry.isFile()) {
      visitor(fullPath, entry);
    }
  }
}

function shouldSkipInfoPath(rootDir, filePath) {
  const rel = toPortablePath(path.relative(rootDir, filePath));
  const parts = rel.split('/');
  if (parts.some((part) => INFO_EXCLUDED_PARTS.has(part))) {
    return true;
  }
  const name = path.basename(filePath);
  const lower = rel.toLowerCase();
  if (DEFAULT_SECRET_FILES.has(name) && !lower.includes('backup')) {
    return false;
  }
  return (
    lower.includes('backup') ||
    lower.endsWith('.db') ||
    lower.endsWith('.sqlite') ||
    lower.endsWith('.log') ||
    lower.endsWith('.bak') ||
    lower.endsWith('.xlsx') ||
    lower.endsWith('.xls') ||
    lower.endsWith('.csv')
  );
}

function shouldIncludeInfoFile(filePath) {
  const name = path.basename(filePath);
  if (DEFAULT_SECRET_FILES.has(name)) {
    return true;
  }
  return SECRET_NAME_PATTERN.test(name) && /\.(json|txt|key|pem|env)$/i.test(name);
}

function addCandidate(rootDir, absolutePath, candidates) {
  const stat = statIfFile(absolutePath);
  if (!stat || stat.size > MAX_FILE_BYTES) {
    return;
  }

  const portablePath = normalizePortablePath(toPortablePath(path.relative(rootDir, absolutePath)));
  if (!portablePath || portablePath.startsWith('..')) {
    return;
  }
  candidates.set(portablePath, {
    absolutePath,
    portablePath,
    size: stat.size,
    mtimeMs: Math.round(stat.mtimeMs),
  });
}

function collectCredentialFiles(rootDir, extraIncludes = []) {
  const candidates = new Map();

  for (const fileName of DEFAULT_ROOT_FILES) {
    addCandidate(rootDir, path.join(rootDir, fileName), candidates);
  }

  let rootEntries = [];
  try {
    rootEntries = fs.readdirSync(rootDir, { withFileTypes: true });
  } catch (_err) {
    rootEntries = [];
  }
  for (const entry of rootEntries) {
    if (!entry.isFile()) {
      continue;
    }
    if (DEFAULT_ROOT_GLOBS.some((pattern) => pattern.test(entry.name))) {
      addCandidate(rootDir, path.join(rootDir, entry.name), candidates);
    }
  }

  const infoDir = path.join(rootDir, 'info');
  if (fs.existsSync(infoDir)) {
    walk(infoDir, (fullPath, entry) => {
      if (entry.isDirectory()) {
        return shouldSkipInfoPath(rootDir, fullPath) ? false : undefined;
      }
      if (shouldSkipInfoPath(rootDir, fullPath)) {
        return;
      }
      if (shouldIncludeInfoFile(fullPath)) {
        addCandidate(rootDir, fullPath, candidates);
      }
    });
  }

  for (const includePath of extraIncludes || []) {
    const resolved = path.resolve(rootDir, includePath);
    addCandidate(rootDir, resolved, candidates);
  }

  return Array.from(candidates.values()).sort((a, b) => a.portablePath.localeCompare(b.portablePath));
}

async function readSecret(promptText, verify = false) {
  if (process.env.JK_CREDENTIALS_PASSWORD) {
    return process.env.JK_CREDENTIALS_PASSWORD;
  }

  if (!process.stdin.isTTY) {
    throw new Error('Informe a senha com --pass ou JK_CREDENTIALS_PASSWORD em ambientes nao interativos.');
  }

  const first = await promptHidden(promptText);
  if (!verify) {
    return first;
  }
  const second = await promptHidden('Confirme a senha: ');
  if (first !== second) {
    throw new Error('As senhas nao conferem.');
  }
  if (first.length < 8) {
    throw new Error('Use uma senha com pelo menos 8 caracteres.');
  }
  return first;
}

function promptHidden(question) {
  return new Promise((resolve) => {
    const stdin = process.stdin;
    const stdout = process.stdout;
    let value = '';

    readline.emitKeypressEvents(stdin);
    if (stdin.isTTY) {
      stdin.setRawMode(true);
    }
    stdin.resume();
    stdout.write(question);

    const onKeypress = (str, key) => {
      if (key && key.name === 'return') {
        cleanup();
        stdout.write('\n');
        resolve(value);
        return;
      }
      if (key && key.name === 'backspace') {
        if (value.length > 0) {
          value = value.slice(0, -1);
          stdout.write('\b \b');
        }
        return;
      }
      if (key && key.ctrl && key.name === 'c') {
        cleanup();
        stdout.write('\n');
        process.exit(130);
      }
      if (str && !key.ctrl && !key.meta) {
        value += str;
        stdout.write('*'.repeat([...str].length));
      }
    };

    const cleanup = () => {
      stdin.off('keypress', onKeypress);
      if (stdin.isTTY) {
        stdin.setRawMode(false);
      }
    };

    stdin.on('keypress', onKeypress);
  });
}

function deriveKey(password, salt, iterations = KDF_ITERATIONS) {
  return crypto.pbkdf2Sync(Buffer.from(password, 'utf8'), salt, iterations, 32, 'sha256');
}

function sha256(buffer) {
  return crypto.createHash('sha256').update(buffer).digest('hex');
}

function encryptPayload(payload, password) {
  const salt = crypto.randomBytes(16);
  const iv = crypto.randomBytes(12);
  const key = deriveKey(password, salt);
  const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
  const plaintext = Buffer.from(JSON.stringify(payload), 'utf8');
  const encrypted = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  const tag = cipher.getAuthTag();

  return {
    format: FORMAT,
    version: FORMAT_VERSION,
    createdAt: new Date().toISOString(),
    kdf: {
      name: 'pbkdf2',
      hash: 'sha256',
      iterations: KDF_ITERATIONS,
      salt: salt.toString('base64'),
    },
    cipher: {
      name: 'aes-256-gcm',
      iv: iv.toString('base64'),
      tag: tag.toString('base64'),
    },
    data: encrypted.toString('base64'),
  };
}

function decryptPackage(container, password) {
  if (!container || container.format !== FORMAT || container.version !== FORMAT_VERSION) {
    throw new Error('Arquivo de credenciais invalido ou versao nao suportada.');
  }
  if (!container.kdf || !container.cipher || !container.data) {
    throw new Error('Arquivo de credenciais incompleto.');
  }

  const salt = Buffer.from(container.kdf.salt, 'base64');
  const iv = Buffer.from(container.cipher.iv, 'base64');
  const tag = Buffer.from(container.cipher.tag, 'base64');
  const encrypted = Buffer.from(container.data, 'base64');
  const key = deriveKey(password, salt, container.kdf.iterations);
  const decipher = crypto.createDecipheriv('aes-256-gcm', key, iv);
  decipher.setAuthTag(tag);

  const plaintext = Buffer.concat([decipher.update(encrypted), decipher.final()]);
  return JSON.parse(plaintext.toString('utf8'));
}

function defaultOutFile(rootDir) {
  const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\..+$/, '').replace('T', '-');
  return path.join(rootDir, `credenciais-jk-${stamp}.jkcred`);
}

async function exportCredentials(rootDir, args) {
  const files = collectCredentialFiles(rootDir, args.include);
  if (files.length === 0) {
    throw new Error('Nenhum arquivo de credenciais foi encontrado.');
  }

  const outFile = path.resolve(rootDir, args.out || defaultOutFile(rootDir));
  if (fs.existsSync(outFile) && !args.force) {
    throw new Error(`Arquivo ja existe: ${outFile}. Use --force para sobrescrever.`);
  }

  console.log(`Arquivos selecionados (${files.length}):`);
  for (const file of files) {
    console.log(`- ${file.portablePath} (${file.size} bytes)`);
  }

  if (args['dry-run']) {
    console.log('Dry-run concluido. Nenhum pacote foi criado.');
    return;
  }

  const password = args.pass || (await readSecret('Senha para criptografar: ', true));
  if (password.length < 8) {
    throw new Error('Use uma senha com pelo menos 8 caracteres.');
  }

  const payload = {
    format: FORMAT,
    version: FORMAT_VERSION,
    exportedAt: new Date().toISOString(),
    exportedFrom: {
      hostname: os.hostname(),
      platform: os.platform(),
      rootName: path.basename(rootDir),
    },
    files: files.map((file) => {
      const bytes = fs.readFileSync(file.absolutePath);
      return {
        path: file.portablePath,
        size: bytes.length,
        sha256: sha256(bytes),
        mtimeMs: file.mtimeMs,
        data: bytes.toString('base64'),
      };
    }),
  };

  const container = encryptPayload(payload, password);
  fs.writeFileSync(outFile, `${JSON.stringify(container, null, 2)}\n`, { mode: 0o600 });
  console.log(`Pacote criptografado criado: ${outFile}`);
}

function backupExisting(rootDir, targetPath, backupRoot) {
  if (!fs.existsSync(targetPath)) {
    return null;
  }
  const relative = path.relative(rootDir, targetPath);
  const backupPath = path.join(backupRoot, relative);
  fs.mkdirSync(path.dirname(backupPath), { recursive: true });
  fs.copyFileSync(targetPath, backupPath);
  return backupPath;
}

function importCredentials(rootDir, args) {
  const inputName = args.in || 'credenciais-jk.jkcred';
  const inFile = path.resolve(rootDir, inputName);
  if (!fs.existsSync(inFile) && !args.in) {
    throw new Error('Nao encontrei credenciais-jk.jkcred nesta pasta. Copie o pacote para ca ou use --in arquivo.jkcred.');
  }
  const container = JSON.parse(fs.readFileSync(inFile, 'utf8'));
  const password = args.pass || process.env.JK_CREDENTIALS_PASSWORD;
  const needPrompt = !password;

  const run = async () => {
    const finalPassword = needPrompt ? await readSecret('Senha para descriptografar: ') : password;
    const payload = decryptPackage(container, finalPassword);
    if (!payload || payload.format !== FORMAT || !Array.isArray(payload.files)) {
      throw new Error('Conteudo descriptografado invalido.');
    }

    const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\..+$/, '').replace('T', '-');
    const backupRoot = path.join(rootDir, 'backups', `credenciais_import_${stamp}`);
    const backups = [];
    const restored = [];

    for (const item of payload.files) {
      const portablePath = normalizePortablePath(item.path);
      const targetPath = resolveInsideRoot(rootDir, portablePath);
      const bytes = Buffer.from(item.data, 'base64');
      if (item.sha256 && sha256(bytes) !== item.sha256) {
        throw new Error(`Hash invalido para ${portablePath}.`);
      }
      if (fs.existsSync(targetPath) && !args.force && args['no-backup']) {
        throw new Error(`Arquivo ja existe: ${portablePath}. Use --force ou remova --no-backup.`);
      }
      if (!args['no-backup']) {
        const backupPath = backupExisting(rootDir, targetPath, backupRoot);
        if (backupPath) {
          backups.push(path.relative(rootDir, backupPath));
        }
      }
      fs.mkdirSync(path.dirname(targetPath), { recursive: true });
      fs.writeFileSync(targetPath, bytes, { mode: 0o600 });
      restored.push(portablePath);
    }

    console.log(`Arquivos restaurados (${restored.length}):`);
    for (const filePath of restored) {
      console.log(`- ${filePath}`);
    }
    if (backups.length) {
      console.log(`Backups criados em: ${path.relative(rootDir, backupRoot)}`);
    }
  };

  return run();
}

function listCredentials(rootDir, args) {
  const files = collectCredentialFiles(rootDir, args.include);
  if (files.length === 0) {
    console.log('Nenhum arquivo de credenciais foi encontrado.');
    return;
  }
  console.log(`Arquivos que entrariam no pacote (${files.length}):`);
  for (const file of files) {
    console.log(`- ${file.portablePath} (${file.size} bytes)`);
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const command = args._[0];
  const rootDir = path.resolve(__dirname, '..');

  if (!command || command === 'help' || command === '--help' || command === '-h') {
    printUsage();
    return;
  }

  if (command === 'list') {
    listCredentials(rootDir, args);
    return;
  }
  if (command === 'export') {
    await exportCredentials(rootDir, args);
    return;
  }
  if (command === 'import') {
    await importCredentials(rootDir, args);
    return;
  }

  throw new Error(`Comando desconhecido: ${command}`);
}

main().catch((err) => {
  console.error(`Erro: ${err.message}`);
  process.exit(1);
});
