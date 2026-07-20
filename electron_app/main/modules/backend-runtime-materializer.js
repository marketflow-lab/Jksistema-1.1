'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const MANIFEST_NAME = 'local-app-manifest.json';
const STATE_RELATIVE_PATH = path.join('info', 'runtime-materialization-state.json');
const JOURNAL_RELATIVE_PATH = path.join('info', 'runtime-materialization-journal.json');
const LOCK_RELATIVE_PATH = path.join('info', 'runtime-materialization.lock');
const PAYLOAD_JOURNAL_RELATIVE_PATH = path.join('info', 'runtime-payload-journal.json');
const PAYLOAD_STATE_RELATIVE_PATH = path.join('info', 'runtime-payload-state.json');
const WHISPER_PAYLOAD_ROOT = 'black_jhon_runtime';
const WHISPER_MODEL_RELATIVE = `${WHISPER_PAYLOAD_ROOT}/faster-whisper-small`;
const WHISPER_MODEL_MANIFEST = 'model-manifest.json';
const SAFE_VERSION_RE = /^[A-Za-z0-9][A-Za-z0-9._+-]{0,119}$/;
const SAFE_TRANSACTION_RE = /^[A-Za-z0-9_-]{1,96}$/;
const PRESERVED_TOP_LEVEL = new Set([
    '.env',
    '.venv',
    'backups',
    'black_jhon_runtime',
    'context_hub',
    'contextvault',
    'info',
    'logs',
    'node_modules',
    'prerequisites',
    'python_runtime',
    'python_wheels',
    'sku'
]);
const LEGACY_CODE_EXTENSIONS = new Set(['.html', '.js', '.py']);

function pathExists(candidate) {
    try {
        fs.lstatSync(candidate);
        return true;
    } catch (err) {
        if (err && (err.code === 'ENOENT' || err.code === 'ENOTDIR')) return false;
        throw err;
    }
}

function readJson(candidate, fallback = null) {
    try {
        return JSON.parse(fs.readFileSync(candidate, 'utf8').replace(/^\uFEFF/, ''));
    } catch (_err) {
        return fallback;
    }
}

function sha256File(candidate) {
    const hash = crypto.createHash('sha256');
    const descriptor = fs.openSync(candidate, 'r');
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

function atomicWriteJson(candidate, payload) {
    fs.mkdirSync(path.dirname(candidate), { recursive: true });
    const temporary = `${candidate}.tmp_${process.pid}_${crypto.randomBytes(6).toString('hex')}`;
    try {
        fs.writeFileSync(temporary, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
        fs.renameSync(temporary, candidate);
    } finally {
        try { fs.rmSync(temporary, { force: true }); } catch (_err) {}
    }
}

function normalizeRelativePath(rawValue) {
    const raw = String(rawValue || '');
    if (
        !raw
        || raw.includes('\\')
        || raw.includes('\0')
        || path.posix.isAbsolute(raw)
        || path.posix.normalize(raw) !== raw
        || raw.split('/').some(part => !part || part === '.' || part === '..' || part.includes(':'))
    ) {
        throw materializationError('MANIFEST_PATH_INVALID', `Caminho invalido no manifesto: ${raw || 'ausente'}`);
    }
    const topLevel = raw.split('/')[0].toLowerCase();
    if (PRESERVED_TOP_LEVEL.has(topLevel) || topLevel.startsWith('.env')) {
        throw materializationError('MANIFEST_PATH_PRESERVED', `Caminho persistente nao pode ser gerenciado: ${raw}`);
    }
    return raw;
}

function normalizePayloadRelativePath(rawValue) {
    const raw = String(rawValue || '');
    if (
        !raw
        || raw.includes('\\')
        || raw.includes('\0')
        || path.posix.isAbsolute(raw)
        || path.posix.normalize(raw) !== raw
        || raw.split('/').some(part => !part || part === '.' || part === '..' || part.includes(':'))
    ) {
        throw materializationError('PAYLOAD_PATH_INVALID', `Caminho invalido no manifesto do payload: ${raw || 'ausente'}`);
    }
    return raw;
}

function normalizeTopLevelEntry(rawValue) {
    const normalized = normalizeRelativePath(rawValue);
    if (normalized.includes('/')) {
        throw materializationError('MANIFEST_ENTRY_INVALID', `Entrada gerenciada deve estar na raiz: ${normalized}`);
    }
    return normalized;
}

function resolveInside(rootDir, relative) {
    const root = path.resolve(rootDir);
    const target = path.resolve(root, ...String(relative).split('/'));
    if (target === root || !target.startsWith(`${root}${path.sep}`)) {
        throw materializationError('PATH_ESCAPE', `Caminho fora da raiz permitida: ${relative}`);
    }
    return target;
}

function assertNotLink(candidate, label) {
    const stat = fs.lstatSync(candidate);
    if (stat.isSymbolicLink()) {
        throw materializationError('REPARSE_POINT_REJECTED', `${label} nao pode ser link ou junction: ${candidate}`);
    }
    return stat;
}

function assertAbsolutePathComponentsNotLinks(candidate, includeLeaf = true) {
    const absolute = path.resolve(candidate);
    const parsed = path.parse(absolute);
    const parts = absolute.slice(parsed.root.length).split(path.sep).filter(Boolean);
    const limit = includeLeaf ? parts.length : Math.max(0, parts.length - 1);
    let current = parsed.root;
    for (let index = 0; index < limit; index += 1) {
        current = path.join(current, parts[index]);
        if (pathExists(current)) assertNotLink(current, 'Componente do caminho persistente');
    }
}

function assertRuntimeStateDirectoriesSafe(targetDir, includeLogs = false) {
    const targetRoot = path.resolve(targetDir);
    assertAbsolutePathComponentsNotLinks(targetRoot);
    if (!pathExists(targetRoot)) return;
    if (!assertNotLink(targetRoot, 'Runtime local').isDirectory()) {
        throw materializationError('RUNTIME_ROOT_INVALID', 'Runtime local deve ser um diretorio regular.');
    }
    for (const name of includeLogs ? ['info', 'logs'] : ['info']) {
        const candidate = path.join(targetRoot, name);
        assertAbsolutePathComponentsNotLinks(candidate);
        if (pathExists(candidate) && !assertNotLink(candidate, `Diretorio ${name}`).isDirectory()) {
            throw materializationError('RUNTIME_STATE_PATH_INVALID', `${name} deve ser um diretorio regular.`);
        }
    }
}

function ensureRuntimeStateDirectories(targetDir, includeLogs = false) {
    const targetRoot = path.resolve(targetDir);
    assertAbsolutePathComponentsNotLinks(targetRoot, false);
    if (!pathExists(targetRoot)) fs.mkdirSync(targetRoot, { recursive: true });
    assertRuntimeStateDirectoriesSafe(targetRoot, includeLogs);
    for (const name of includeLogs ? ['info', 'logs'] : ['info']) {
        const candidate = path.join(targetRoot, name);
        if (!pathExists(candidate)) fs.mkdirSync(candidate, { recursive: false });
    }
    assertRuntimeStateDirectoriesSafe(targetRoot, includeLogs);
}

function assertPathComponentsNotLinks(rootDir, relative, includeLeaf = true) {
    const root = path.resolve(rootDir);
    if (pathExists(root)) assertNotLink(root, 'Raiz');
    const parts = String(relative || '').split('/').filter(Boolean);
    const limit = includeLeaf ? parts.length : Math.max(0, parts.length - 1);
    let current = root;
    for (let index = 0; index < limit; index += 1) {
        current = path.join(current, parts[index]);
        if (pathExists(current)) assertNotLink(current, 'Componente');
    }
}

function walkFilesStrict(rootDir, relativeRoot = '') {
    const root = path.resolve(rootDir);
    const start = relativeRoot ? resolveInside(root, relativeRoot) : root;
    if (!pathExists(start)) return [];
    const startStat = assertNotLink(start, 'Arvore gerenciada');
    if (!startStat.isDirectory()) {
        return [String(relativeRoot).replace(/\\/g, '/')];
    }
    const files = [];
    const stack = [{ directory: start, relative: String(relativeRoot).replace(/\\/g, '/') }];
    while (stack.length) {
        const current = stack.pop();
        const entries = fs.readdirSync(current.directory, { withFileTypes: true })
            .sort((left, right) => right.name.localeCompare(left.name, 'en'));
        for (const entry of entries) {
            const fullPath = path.join(current.directory, entry.name);
            const relative = current.relative ? `${current.relative}/${entry.name}` : entry.name;
            const stat = assertNotLink(fullPath, 'Entrada gerenciada');
            if (stat.isDirectory()) {
                stack.push({ directory: fullPath, relative });
            } else if (stat.isFile()) {
                files.push(relative);
            } else {
                throw materializationError('SPECIAL_FILE_REJECTED', `Tipo de arquivo nao suportado: ${relative}`);
            }
        }
    }
    return files.sort((left, right) => left.localeCompare(right, 'en'));
}

function materializationError(code, message, cause = null) {
    const error = new Error(message);
    error.code = code;
    error.jkRuntimeMaterialization = true;
    if (cause) error.cause = cause;
    return error;
}

function normalizeStringArray(value, label) {
    if (!Array.isArray(value)) {
        throw materializationError('MANIFEST_SCHEMA_INVALID', `${label} deve ser uma lista.`);
    }
    const normalized = value.map(normalizeTopLevelEntry);
    if (new Set(normalized.map(item => item.toLowerCase())).size !== normalized.length) {
        throw materializationError('MANIFEST_DUPLICATE_ENTRY', `${label} contem entradas duplicadas.`);
    }
    return normalized.sort((left, right) => left.localeCompare(right, 'en'));
}

function arraysEqual(left, right) {
    return left.length === right.length && left.every((value, index) => value === right[index]);
}

function loadValidatedManifest(sourceDir, expectedVersion = '') {
    const sourceRoot = path.resolve(sourceDir);
    if (!pathExists(sourceRoot) || !assertNotLink(sourceRoot, 'Origem do pacote').isDirectory()) {
        throw materializationError('SOURCE_MISSING', `Origem local_app ausente: ${sourceRoot}`);
    }
    const manifestPath = path.join(sourceRoot, MANIFEST_NAME);
    if (!pathExists(manifestPath)) {
        throw materializationError('MANIFEST_MISSING', `${MANIFEST_NAME} ausente no pacote.`);
    }
    assertPathComponentsNotLinks(sourceRoot, MANIFEST_NAME);
    const manifest = readJson(manifestPath);
    if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest)) {
        throw materializationError('MANIFEST_JSON_INVALID', `${MANIFEST_NAME} invalido.`);
    }
    const version = String(manifest.version || '').trim();
    if (
        Number(manifest.schema_version) !== 1
        || String(manifest.algorithm || '').toLowerCase() !== 'sha256'
        || !SAFE_VERSION_RE.test(version)
        || (expectedVersion && version !== String(expectedVersion))
        || !Array.isArray(manifest.files)
    ) {
        throw materializationError(
            'MANIFEST_SCHEMA_INVALID',
            `Manifesto local_app incompativel com a versao ${expectedVersion || 'esperada'}.`
        );
    }

    const files = [];
    const seen = new Set();
    for (const entry of manifest.files) {
        const relative = normalizeRelativePath(entry && entry.path);
        const key = relative.toLowerCase();
        const expectedHash = String(entry && entry.sha256 || '').trim().toLowerCase();
        const expectedSize = Number(entry && entry.size);
        if (
            key === MANIFEST_NAME.toLowerCase()
            || seen.has(key)
            || !/^[a-f0-9]{64}$/.test(expectedHash)
            || !Number.isSafeInteger(expectedSize)
            || expectedSize < 0
        ) {
            throw materializationError('MANIFEST_FILE_INVALID', `Entrada invalida no manifesto: ${relative}`);
        }
        seen.add(key);
        const sourcePath = resolveInside(sourceRoot, relative);
        assertPathComponentsNotLinks(sourceRoot, relative);
        if (!pathExists(sourcePath)) {
            throw materializationError('SOURCE_FILE_MISSING', `Arquivo declarado ausente no pacote: ${relative}`);
        }
        const stat = assertNotLink(sourcePath, 'Arquivo de origem');
        if (!stat.isFile() || stat.size !== expectedSize || sha256File(sourcePath) !== expectedHash) {
            throw materializationError('SOURCE_FILE_HASH_MISMATCH', `Arquivo do pacote diverge do manifesto: ${relative}`);
        }
        files.push({ path: relative, size: expectedSize, sha256: expectedHash });
    }
    files.sort((left, right) => left.path.localeCompare(right.path, 'en'));
    if (Number(manifest.file_count) !== files.length) {
        throw materializationError('MANIFEST_FILE_COUNT_MISMATCH', 'file_count diverge da lista do manifesto.');
    }

    const derivedDirectories = [...new Set(
        files.filter(entry => entry.path.includes('/')).map(entry => entry.path.split('/')[0])
    )].sort((left, right) => left.localeCompare(right, 'en'));
    const derivedRootFiles = files.filter(entry => !entry.path.includes('/'))
        .map(entry => entry.path)
        .sort((left, right) => left.localeCompare(right, 'en'));
    const managedDirectories = normalizeStringArray(manifest.managed_directories, 'managed_directories');
    const managedRootFiles = normalizeStringArray(manifest.managed_root_files, 'managed_root_files');
    if (!arraysEqual(managedDirectories, derivedDirectories) || !arraysEqual(managedRootFiles, derivedRootFiles)) {
        throw materializationError('MANIFEST_MANAGED_SET_MISMATCH', 'Conjunto gerenciado diverge dos arquivos declarados.');
    }

    return {
        manifest,
        manifestPath,
        manifestSha256: sha256File(manifestPath),
        version,
        files,
        managedDirectories,
        managedRootFiles,
        managedEntries: [...managedDirectories, ...managedRootFiles, MANIFEST_NAME]
    };
}

function entriesForTopLevel(manifestInfo, topLevel) {
    const prefix = `${topLevel}/`;
    return manifestInfo.files.filter(entry => entry.path === topLevel || entry.path.startsWith(prefix));
}

function verifyTopLevelEntry(targetDir, manifestInfo, topLevel) {
    const expectedEntries = entriesForTopLevel(manifestInfo, topLevel);
    const targetPath = resolveInside(targetDir, topLevel);
    if (!expectedEntries.length || !pathExists(targetPath)) return false;
    try {
        assertPathComponentsNotLinks(targetDir, topLevel);
        const stat = assertNotLink(targetPath, 'Destino gerenciado');
        if (manifestInfo.managedDirectories.includes(topLevel)) {
            if (!stat.isDirectory()) return false;
            const actual = walkFilesStrict(targetDir, topLevel);
            const expected = expectedEntries.map(entry => entry.path);
            if (!arraysEqual(actual, expected)) return false;
        } else if (!stat.isFile()) {
            return false;
        }
        for (const entry of expectedEntries) {
            const candidate = resolveInside(targetDir, entry.path);
            const fileStat = assertNotLink(candidate, 'Arquivo materializado');
            if (!fileStat.isFile() || fileStat.size !== entry.size || sha256File(candidate) !== entry.sha256) {
                return false;
            }
        }
        return true;
    } catch (_err) {
        return false;
    }
}

function loadState(targetDir) {
    assertRuntimeStateDirectoriesSafe(targetDir);
    const state = readJson(path.join(targetDir, STATE_RELATIVE_PATH));
    if (!state || Number(state.schema_version) !== 1) return null;
    return state;
}

function verifyCurrentMaterialization(targetDir, manifestInfo, state = loadState(targetDir)) {
    if (
        !state
        || String(state.version || '') !== manifestInfo.version
        || String(state.manifest_sha256 || '').toLowerCase() !== manifestInfo.manifestSha256
        || Number(state.file_count) !== manifestInfo.files.length
    ) {
        return false;
    }
    const targetManifest = path.join(targetDir, MANIFEST_NAME);
    if (
        !pathExists(targetManifest)
        || assertNotLink(targetManifest, 'Manifesto materializado').size !== fs.statSync(manifestInfo.manifestPath).size
        || sha256File(targetManifest) !== manifestInfo.manifestSha256
    ) {
        return false;
    }
    return [...manifestInfo.managedDirectories, ...manifestInfo.managedRootFiles]
        .every(entry => verifyTopLevelEntry(targetDir, manifestInfo, entry));
}

function processLooksAlive(pid) {
    const numeric = Number(pid);
    if (!Number.isInteger(numeric) || numeric <= 0) return false;
    try {
        process.kill(numeric, 0);
        return true;
    } catch (err) {
        return !!(err && err.code === 'EPERM');
    }
}

function acquireLock(targetDir) {
    ensureRuntimeStateDirectories(targetDir);
    const lockPath = path.join(targetDir, LOCK_RELATIVE_PATH);
    const token = crypto.randomBytes(12).toString('hex');
    const payload = { pid: process.pid, token, acquired_at_ms: Date.now() };
    for (let attempt = 0; attempt < 2; attempt += 1) {
        try {
            const descriptor = fs.openSync(lockPath, 'wx');
            try { fs.writeFileSync(descriptor, `${JSON.stringify(payload)}\n`, 'utf8'); } finally { fs.closeSync(descriptor); }
            return { lockPath, token };
        } catch (err) {
            if (!err || err.code !== 'EEXIST') throw err;
            const existing = readJson(lockPath, {});
            if (!processLooksAlive(existing && existing.pid)) {
                try { fs.rmSync(lockPath, { force: true }); } catch (_err) {}
                continue;
            }
            throw materializationError('MATERIALIZATION_LOCKED', 'Outra materializacao do runtime esta em andamento.');
        }
    }
    throw materializationError('MATERIALIZATION_LOCKED', 'Nao foi possivel adquirir o lock da materializacao.');
}

function releaseLock(lock) {
    if (!lock || !lock.lockPath) return;
    const current = readJson(lock.lockPath, {});
    if (current && current.token === lock.token) {
        try { fs.rmSync(lock.lockPath, { force: true }); } catch (_err) {}
    }
}

function transactionPaths(targetDir, transactionId) {
    if (!SAFE_TRANSACTION_RE.test(String(transactionId || ''))) {
        throw materializationError('JOURNAL_TRANSACTION_INVALID', 'Identificador de transacao invalido.');
    }
    const targetRoot = path.resolve(targetDir);
    const parent = path.dirname(targetRoot);
    const prefix = `.${path.basename(targetRoot)}.materialize-${transactionId}`;
    return {
        stageDir: path.join(parent, `${prefix}.staging`),
        backupDir: path.join(parent, `${prefix}.backup`)
    };
}

function assertSafeTransactionArtifact(candidate, targetDir) {
    const parent = path.dirname(path.resolve(targetDir));
    const basenamePrefix = `.${path.basename(path.resolve(targetDir))}.materialize-`;
    const resolved = path.resolve(candidate);
    if (
        path.dirname(resolved) !== parent
        || !path.basename(resolved).startsWith(basenamePrefix)
        || !/\.(?:staging|backup)$/.test(path.basename(resolved))
    ) {
        throw materializationError('TRANSACTION_PATH_INVALID', `Artefato transacional fora da raiz: ${candidate}`);
    }
}

function removeTransactionArtifact(candidate, targetDir) {
    assertSafeTransactionArtifact(candidate, targetDir);
    if (!pathExists(candidate)) return;
    assertNotLink(candidate, 'Artefato transacional');
    fs.rmSync(candidate, { recursive: true, force: true });
}

function removeDirectManagedEntry(candidate, targetDir) {
    const targetRoot = path.resolve(targetDir);
    const resolved = path.resolve(candidate);
    if (path.dirname(resolved) !== targetRoot) {
        throw materializationError('MANAGED_REMOVE_OUTSIDE_ROOT', `Remocao fora da raiz gerenciada: ${candidate}`);
    }
    if (!pathExists(resolved)) return;
    assertNotLink(resolved, 'Entrada gerenciada');
    fs.rmSync(resolved, { recursive: true, force: true });
}

function writeState(targetDir, state) {
    const statePath = path.join(targetDir, STATE_RELATIVE_PATH);
    if (state) atomicWriteJson(statePath, state);
    else fs.rmSync(statePath, { force: true });
}

function validateJournal(journal) {
    if (
        !journal
        || Number(journal.schema_version) !== 1
        || !SAFE_TRANSACTION_RE.test(String(journal.transaction_id || ''))
        || !Array.isArray(journal.actions)
    ) {
        throw materializationError('JOURNAL_INVALID', 'Journal de materializacao invalido.');
    }
    journal.actions = journal.actions.map(action => ({
        name: normalizeTopLevelEntry(action && action.name),
        had_target: action && action.had_target === true,
        installs_new: action && action.installs_new === true,
        status: String(action && action.status || 'pending')
    }));
    return journal;
}

function rollbackJournal(targetDir, journalPath, journal) {
    const validated = validateJournal(journal);
    const { stageDir, backupDir } = transactionPaths(targetDir, validated.transaction_id);
    for (const action of [...validated.actions].reverse()) {
        const target = resolveInside(targetDir, action.name);
        const backup = resolveInside(backupDir, action.name);
        if (pathExists(backup)) {
            if (pathExists(target)) removeDirectManagedEntry(target, targetDir);
            fs.mkdirSync(path.dirname(target), { recursive: true });
            fs.renameSync(backup, target);
        } else if (!action.had_target && pathExists(target)) {
            removeDirectManagedEntry(target, targetDir);
        }
    }
    writeState(targetDir, validated.previous_state && typeof validated.previous_state === 'object'
        ? validated.previous_state
        : null);
    removeTransactionArtifact(stageDir, targetDir);
    removeTransactionArtifact(backupDir, targetDir);
    fs.rmSync(journalPath, { force: true });
    return { recovered: true, rolledBack: true, transactionId: validated.transaction_id };
}

function recoverJournalUnderLock(targetDir) {
    const journalPath = path.join(targetDir, JOURNAL_RELATIVE_PATH);
    if (!pathExists(journalPath)) return { recovered: false };
    const journal = validateJournal(readJson(journalPath));
    const { stageDir, backupDir } = transactionPaths(targetDir, journal.transaction_id);
    if (journal.phase === 'health_verified') {
        removeTransactionArtifact(stageDir, targetDir);
        removeTransactionArtifact(backupDir, targetDir);
        fs.rmSync(journalPath, { force: true });
        return { recovered: true, finalized: true, transactionId: journal.transaction_id };
    }
    return rollbackJournal(targetDir, journalPath, journal);
}

function recoverInterruptedMaterialization(targetDir) {
    const targetRoot = path.resolve(targetDir);
    ensureRuntimeStateDirectories(targetRoot);
    const lock = acquireLock(targetRoot);
    try {
        const payload = recoverPayloadJournalUnderLock(targetRoot);
        const application = recoverJournalUnderLock(targetRoot);
        return {
            recovered: !!(payload.recovered || application.recovered),
            payload,
            application,
            rolledBack: !!(payload.rolledBack || application.rolledBack)
        };
    } finally {
        releaseLock(lock);
    }
}

function loadWhisperPayloadDescriptor(sourceDir, verifyHashes = false) {
    const sourceRoot = path.resolve(sourceDir);
    const modelDir = resolveInside(sourceRoot, WHISPER_MODEL_RELATIVE);
    const manifestPath = path.join(modelDir, WHISPER_MODEL_MANIFEST);
    if (!pathExists(manifestPath)) {
        throw materializationError('WHISPER_MANIFEST_MISSING', 'Manifesto do modelo Whisper ausente no pacote.');
    }
    assertPathComponentsNotLinks(sourceRoot, WHISPER_MODEL_RELATIVE);
    const manifest = readJson(manifestPath);
    if (!manifest || !Array.isArray(manifest.files) || !manifest.files.length) {
        throw materializationError('WHISPER_MANIFEST_INVALID', 'Manifesto do modelo Whisper invalido.');
    }
    const files = [];
    const seen = new Set();
    for (const entry of manifest.files) {
        const relative = normalizePayloadRelativePath(entry && entry.path);
        const expectedHash = String(entry && entry.sha256 || '').trim().toLowerCase();
        const expectedSize = Number(entry && entry.size);
        if (
            seen.has(relative.toLowerCase())
            || !/^[a-f0-9]{64}$/.test(expectedHash)
            || !Number.isSafeInteger(expectedSize)
            || expectedSize < 0
        ) {
            throw materializationError('WHISPER_FILE_INVALID', `Entrada invalida no manifesto Whisper: ${relative}`);
        }
        seen.add(relative.toLowerCase());
        const candidate = resolveInside(modelDir, relative);
        assertPathComponentsNotLinks(modelDir, relative);
        if (!pathExists(candidate)) {
            throw materializationError('WHISPER_FILE_MISSING', `Arquivo do modelo ausente: ${relative}`);
        }
        const stat = assertNotLink(candidate, 'Arquivo do modelo');
        if (!stat.isFile() || stat.size !== expectedSize || (verifyHashes && sha256File(candidate) !== expectedHash)) {
            throw materializationError('WHISPER_FILE_MISMATCH', `Arquivo do modelo diverge: ${relative}`);
        }
        files.push({ path: relative, size: expectedSize, sha256: expectedHash });
    }
    files.sort((left, right) => left.path.localeCompare(right.path, 'en'));
    const actualFiles = walkFilesStrict(modelDir);
    const expectedFiles = [...files.map(entry => entry.path), WHISPER_MODEL_MANIFEST]
        .sort((left, right) => left.localeCompare(right, 'en'));
    if (!arraysEqual(actualFiles, expectedFiles)) {
        throw materializationError('WHISPER_TREE_MISMATCH', 'Arvore do modelo Whisper contem arquivos nao declarados.');
    }
    return {
        modelDir,
        manifestPath,
        manifestSha256: sha256File(manifestPath),
        files
    };
}

function whisperPayloadLooksCurrent(targetDir, descriptor) {
    const targetModel = resolveInside(targetDir, WHISPER_MODEL_RELATIVE);
    const targetManifest = path.join(targetModel, WHISPER_MODEL_MANIFEST);
    try {
        if (!pathExists(targetManifest) || sha256File(targetManifest) !== descriptor.manifestSha256) return false;
        assertPathComponentsNotLinks(targetDir, WHISPER_MODEL_RELATIVE);
        const actualFiles = walkFilesStrict(targetModel);
        const expectedFiles = [...descriptor.files.map(entry => entry.path), WHISPER_MODEL_MANIFEST]
            .sort((left, right) => left.localeCompare(right, 'en'));
        if (!arraysEqual(actualFiles, expectedFiles)) return false;
        return descriptor.files.every(entry => {
            const candidate = resolveInside(targetModel, entry.path);
            const stat = assertNotLink(candidate, 'Arquivo do payload materializado');
            return stat.isFile() && stat.size === entry.size;
        });
    } catch (_err) {
        return false;
    }
}

function payloadStatePath(targetDir) {
    return path.join(targetDir, PAYLOAD_STATE_RELATIVE_PATH);
}

function writePayloadState(targetDir, state) {
    if (state) atomicWriteJson(payloadStatePath(targetDir), state);
    else fs.rmSync(payloadStatePath(targetDir), { force: true });
}

function validatePayloadJournal(journal) {
    if (
        !journal
        || Number(journal.schema_version) !== 1
        || !SAFE_TRANSACTION_RE.test(String(journal.transaction_id || ''))
        || typeof journal.had_target !== 'boolean'
    ) {
        throw materializationError('PAYLOAD_JOURNAL_INVALID', 'Journal do payload Whisper invalido.');
    }
    return journal;
}

function rollbackPayloadJournal(targetDir, journalPath, journal) {
    const validated = validatePayloadJournal(journal);
    const { stageDir, backupDir } = transactionPaths(targetDir, validated.transaction_id);
    const targetPayload = resolveInside(targetDir, WHISPER_PAYLOAD_ROOT);
    const backupPayload = resolveInside(backupDir, WHISPER_PAYLOAD_ROOT);
    if (pathExists(backupPayload)) {
        if (pathExists(targetPayload)) removeDirectManagedEntry(targetPayload, targetDir);
        fs.renameSync(backupPayload, targetPayload);
    } else if (!validated.had_target && pathExists(targetPayload)) {
        removeDirectManagedEntry(targetPayload, targetDir);
    }
    writePayloadState(
        targetDir,
        validated.previous_state && typeof validated.previous_state === 'object'
            ? validated.previous_state
            : null
    );
    removeTransactionArtifact(stageDir, targetDir);
    removeTransactionArtifact(backupDir, targetDir);
    fs.rmSync(journalPath, { force: true });
    return { recovered: true, rolledBack: true, transactionId: validated.transaction_id };
}

function recoverPayloadJournalUnderLock(targetDir) {
    const journalPath = path.join(targetDir, PAYLOAD_JOURNAL_RELATIVE_PATH);
    if (!pathExists(journalPath)) return { recovered: false };
    const journal = validatePayloadJournal(readJson(journalPath));
    const { stageDir, backupDir } = transactionPaths(targetDir, journal.transaction_id);
    if (journal.phase === 'verified') {
        removeTransactionArtifact(stageDir, targetDir);
        removeTransactionArtifact(backupDir, targetDir);
        fs.rmSync(journalPath, { force: true });
        return { recovered: true, finalized: true, transactionId: journal.transaction_id };
    }
    return rollbackPayloadJournal(targetDir, journalPath, journal);
}

function inspectImmutableRuntimePayloads(options) {
    const sourceDir = path.resolve(options && options.sourceDir || '');
    const targetDir = path.resolve(options && options.targetDir || '');
    if (sourceDir === targetDir) return { required: false, sameDirectory: true };
    assertRuntimeStateDirectoriesSafe(targetDir);
    const recoveryRequired = pathExists(path.join(targetDir, PAYLOAD_JOURNAL_RELATIVE_PATH));
    const descriptor = loadWhisperPayloadDescriptor(sourceDir, false);
    return {
        required: recoveryRequired || !whisperPayloadLooksCurrent(targetDir, descriptor),
        recoveryRequired,
        manifestSha256: descriptor.manifestSha256
    };
}

function ensureImmutableRuntimePayloads(options) {
    const sourceDir = path.resolve(options && options.sourceDir || '');
    const targetDir = path.resolve(options && options.targetDir || '');
    if (sourceDir === targetDir) return { changed: false, sameDirectory: true };
    ensureRuntimeStateDirectories(targetDir);
    const lock = acquireLock(targetDir);
    const journalPath = path.join(targetDir, PAYLOAD_JOURNAL_RELATIVE_PATH);
    let stageDir = '';
    let backupDir = '';
    try {
        recoverPayloadJournalUnderLock(targetDir);
        let descriptor = loadWhisperPayloadDescriptor(sourceDir, false);
        if (whisperPayloadLooksCurrent(targetDir, descriptor)) {
            writePayloadState(targetDir, {
                schema_version: 1,
                whisper_manifest_sha256: descriptor.manifestSha256,
                file_count: descriptor.files.length,
                verified_at: new Date().toISOString()
            });
            return { changed: false, adopted: true, manifestSha256: descriptor.manifestSha256 };
        }
        descriptor = loadWhisperPayloadDescriptor(sourceDir, true);
        const previousState = readJson(payloadStatePath(targetDir));
        const transactionId = `payload_${Date.now()}_${process.pid}_${crypto.randomBytes(6).toString('hex')}`;
        ({ stageDir, backupDir } = transactionPaths(targetDir, transactionId));
        const stageModel = resolveInside(stageDir, WHISPER_MODEL_RELATIVE);
        fs.mkdirSync(stageModel, { recursive: true });
        for (const entry of descriptor.files) {
            const source = resolveInside(descriptor.modelDir, entry.path);
            const target = resolveInside(stageModel, entry.path);
            fs.mkdirSync(path.dirname(target), { recursive: true });
            fs.copyFileSync(source, target, fs.constants.COPYFILE_EXCL);
            if (fs.statSync(target).size !== entry.size || sha256File(target) !== entry.sha256) {
                throw materializationError('WHISPER_STAGING_MISMATCH', `Payload copiado divergiu: ${entry.path}`);
            }
        }
        fs.copyFileSync(
            descriptor.manifestPath,
            path.join(stageModel, WHISPER_MODEL_MANIFEST),
            fs.constants.COPYFILE_EXCL
        );
        if (!whisperPayloadLooksCurrent(stageDir, descriptor)) {
            throw materializationError('WHISPER_STAGING_INVALID', 'Payload Whisper em staging ficou invalido.');
        }
        callFaultInjector(options, 'payload_staging_complete', { transactionId });

        const targetPayload = resolveInside(targetDir, WHISPER_PAYLOAD_ROOT);
        const stagePayload = resolveInside(stageDir, WHISPER_PAYLOAD_ROOT);
        const backupPayload = resolveInside(backupDir, WHISPER_PAYLOAD_ROOT);
        const journal = {
            schema_version: 1,
            transaction_id: transactionId,
            phase: 'swapping',
            had_target: pathExists(targetPayload),
            previous_state: previousState,
            started_at: new Date().toISOString()
        };
        if (journal.had_target) {
            assertPathComponentsNotLinks(targetDir, WHISPER_PAYLOAD_ROOT);
            assertNotLink(targetPayload, 'Payload Whisper existente');
        }
        atomicWriteJson(journalPath, journal);
        fs.mkdirSync(backupDir, { recursive: false });
        if (journal.had_target) fs.renameSync(targetPayload, backupPayload);
        fs.renameSync(stagePayload, targetPayload);
        callFaultInjector(options, 'payload_after_install', { transactionId });
        if (!whisperPayloadLooksCurrent(targetDir, descriptor)) {
            throw materializationError('WHISPER_TARGET_INVALID', 'Payload Whisper materializado ficou invalido.');
        }
        writePayloadState(targetDir, {
            schema_version: 1,
            whisper_manifest_sha256: descriptor.manifestSha256,
            file_count: descriptor.files.length,
            verified_at: new Date().toISOString()
        });
        journal.phase = 'verified';
        journal.verified_at = new Date().toISOString();
        atomicWriteJson(journalPath, journal);
        try {
            removeTransactionArtifact(stageDir, targetDir);
            removeTransactionArtifact(backupDir, targetDir);
            fs.rmSync(journalPath, { force: true });
            return { changed: true, manifestSha256: descriptor.manifestSha256 };
        } catch (cleanupError) {
            return {
                changed: true,
                cleanupPending: true,
                manifestSha256: descriptor.manifestSha256,
                error: cleanupError && cleanupError.message ? cleanupError.message : String(cleanupError)
            };
        }
    } catch (err) {
        const journal = readJson(journalPath);
        if (journal) {
            try { rollbackPayloadJournal(targetDir, journalPath, journal); } catch (rollbackError) {
                err.rollbackError = rollbackError;
            }
        } else {
            try { if (stageDir) removeTransactionArtifact(stageDir, targetDir); } catch (_err) {}
            try { if (backupDir) removeTransactionArtifact(backupDir, targetDir); } catch (_err) {}
        }
        if (err && err.jkRuntimeMaterialization) throw err;
        throw materializationError(
            err && err.code ? String(err.code) : 'PAYLOAD_MATERIALIZATION_FAILED',
            err && err.message ? err.message : String(err),
            err
        );
    } finally {
        releaseLock(lock);
    }
}

function inspectMaterialization(options) {
    const sourceDir = path.resolve(options && options.sourceDir || '');
    const targetDir = path.resolve(options && options.targetDir || '');
    if (sourceDir === targetDir) {
        return { required: false, sameDirectory: true, recoveryRequired: false };
    }
    assertRuntimeStateDirectoriesSafe(targetDir, true);
    const manifestInfo = loadValidatedManifest(sourceDir, options && options.expectedVersion);
    const journalPath = path.join(targetDir, JOURNAL_RELATIVE_PATH);
    const recoveryRequired = pathExists(journalPath);
    const current = !recoveryRequired && pathExists(targetDir)
        ? verifyCurrentMaterialization(targetDir, manifestInfo)
        : false;
    return {
        required: recoveryRequired || !current,
        sameDirectory: false,
        recoveryRequired,
        version: manifestInfo.version,
        manifestSha256: manifestInfo.manifestSha256
    };
}

function legacyManagedRootFiles(targetDir) {
    if (!pathExists(targetDir)) return [];
    const files = [];
    for (const entry of fs.readdirSync(targetDir, { withFileTypes: true })) {
        if (!entry.isFile()) continue;
        const lower = entry.name.toLowerCase();
        if (lower === MANIFEST_NAME.toLowerCase() || LEGACY_CODE_EXTENSIONS.has(path.extname(lower))) {
            files.push(entry.name);
        }
    }
    return files;
}

function previousManagedEntries(targetDir, previousState) {
    const values = [];
    if (previousState && Array.isArray(previousState.managed_entries)) {
        values.push(...previousState.managed_entries);
    }
    const previousManifest = readJson(path.join(targetDir, MANIFEST_NAME), {});
    if (previousManifest && typeof previousManifest === 'object') {
        if (Array.isArray(previousManifest.managed_directories)) values.push(...previousManifest.managed_directories);
        if (Array.isArray(previousManifest.managed_root_files)) values.push(...previousManifest.managed_root_files);
    }
    values.push(...legacyManagedRootFiles(targetDir));
    const normalized = [];
    for (const value of values) {
        try { normalized.push(normalizeTopLevelEntry(value)); } catch (_err) {}
    }
    return [...new Set(normalized.map(item => item.toLowerCase()))]
        .map(lower => normalized.find(item => item.toLowerCase() === lower))
        .sort((left, right) => left.localeCompare(right, 'en'));
}

function copyManifestEntriesToStage(sourceDir, stageDir, manifestInfo, changedEntries) {
    fs.mkdirSync(stageDir, { recursive: false });
    const changed = new Set(changedEntries.map(entry => entry.toLowerCase()));
    for (const entry of manifestInfo.files) {
        if (!changed.has(entry.path.split('/')[0].toLowerCase())) continue;
        const source = resolveInside(sourceDir, entry.path);
        const target = resolveInside(stageDir, entry.path);
        fs.mkdirSync(path.dirname(target), { recursive: true });
        fs.copyFileSync(source, target, fs.constants.COPYFILE_EXCL);
        const stat = fs.statSync(target);
        if (stat.size !== entry.size || sha256File(target) !== entry.sha256) {
            throw materializationError('STAGING_HASH_MISMATCH', `Copia em staging divergiu: ${entry.path}`);
        }
    }
    if (changed.has(MANIFEST_NAME.toLowerCase())) {
        fs.copyFileSync(manifestInfo.manifestPath, path.join(stageDir, MANIFEST_NAME), fs.constants.COPYFILE_EXCL);
        if (sha256File(path.join(stageDir, MANIFEST_NAME)) !== manifestInfo.manifestSha256) {
            throw materializationError('STAGING_MANIFEST_MISMATCH', 'Manifesto copiado para staging divergiu.');
        }
    }
}

function callFaultInjector(options, phase, details = {}) {
    if (options && typeof options.faultInjector === 'function') {
        options.faultInjector(phase, details);
    }
}

function materializeLocalApp(options) {
    const sourceDir = path.resolve(options && options.sourceDir || '');
    const targetDir = path.resolve(options && options.targetDir || '');
    if (sourceDir === targetDir) {
        ensureRuntimeStateDirectories(targetDir, true);
        return { changed: false, sameDirectory: true, targetDir };
    }
    ensureRuntimeStateDirectories(targetDir, true);

    const lock = acquireLock(targetDir);
    let journalPath = path.join(targetDir, JOURNAL_RELATIVE_PATH);
    let journal = null;
    let stageDir = '';
    let backupDir = '';
    try {
        recoverJournalUnderLock(targetDir);
        const manifestInfo = loadValidatedManifest(sourceDir, options && options.expectedVersion);
        const previousState = loadState(targetDir);
        if (verifyCurrentMaterialization(targetDir, manifestInfo, previousState)) {
            return {
                changed: false,
                targetDir,
                version: manifestInfo.version,
                manifestSha256: manifestInfo.manifestSha256
            };
        }

        const desiredEntries = manifestInfo.managedEntries;
        const removedEntries = previousManagedEntries(targetDir, previousState)
            .filter(entry => !desiredEntries.some(desired => desired.toLowerCase() === entry.toLowerCase()));
        const changedEntries = desiredEntries.filter(entry => {
            if (entry === MANIFEST_NAME) {
                const current = path.join(targetDir, MANIFEST_NAME);
                return !pathExists(current) || sha256File(current) !== manifestInfo.manifestSha256;
            }
            return !verifyTopLevelEntry(targetDir, manifestInfo, entry);
        });
        const transactionEntries = [...new Set([...changedEntries, ...removedEntries].map(item => item.toLowerCase()))]
            .map(lower => [...changedEntries, ...removedEntries].find(item => item.toLowerCase() === lower))
            .sort((left, right) => left.localeCompare(right, 'en'));
        if (!transactionEntries.length) {
            const state = {
                schema_version: 1,
                version: manifestInfo.version,
                manifest_sha256: manifestInfo.manifestSha256,
                file_count: manifestInfo.files.length,
                managed_entries: manifestInfo.managedEntries,
                materialized_at: new Date().toISOString()
            };
            writeState(targetDir, state);
            return { changed: false, adopted: true, targetDir, version: manifestInfo.version };
        }

        const transactionId = `${Date.now()}_${process.pid}_${crypto.randomBytes(6).toString('hex')}`;
        ({ stageDir, backupDir } = transactionPaths(targetDir, transactionId));
        copyManifestEntriesToStage(sourceDir, stageDir, manifestInfo, changedEntries);
        callFaultInjector(options, 'staging_complete', { transactionId, stageDir });

        const actions = transactionEntries.map(name => {
            const target = resolveInside(targetDir, name);
            if (pathExists(target)) {
                assertPathComponentsNotLinks(targetDir, name);
                assertNotLink(target, 'Entrada de runtime existente');
            }
            return {
                name,
                had_target: pathExists(target),
                installs_new: pathExists(resolveInside(stageDir, name)),
                status: 'pending'
            };
        });
        journal = {
            schema_version: 1,
            transaction_id: transactionId,
            phase: 'swapping',
            version: manifestInfo.version,
            manifest_sha256: manifestInfo.manifestSha256,
            previous_state: previousState,
            actions,
            started_at: new Date().toISOString()
        };
        atomicWriteJson(journalPath, journal);

        fs.mkdirSync(backupDir, { recursive: false });
        for (const action of journal.actions) {
            const target = resolveInside(targetDir, action.name);
            const staged = resolveInside(stageDir, action.name);
            const backup = resolveInside(backupDir, action.name);
            if (action.had_target) {
                fs.mkdirSync(path.dirname(backup), { recursive: true });
                fs.renameSync(target, backup);
            }
            callFaultInjector(options, 'after_backup', { transactionId, name: action.name });
            if (action.installs_new) {
                fs.mkdirSync(path.dirname(target), { recursive: true });
                fs.renameSync(staged, target);
            }
            callFaultInjector(options, 'after_install', { transactionId, name: action.name });
            action.status = action.installs_new ? 'installed' : 'removed';
            atomicWriteJson(journalPath, journal);
        }

        for (const entry of [...manifestInfo.managedDirectories, ...manifestInfo.managedRootFiles]) {
            if (!verifyTopLevelEntry(targetDir, manifestInfo, entry)) {
                throw materializationError('TARGET_HASH_MISMATCH', `Runtime materializado divergiu: ${entry}`);
            }
        }
        if (sha256File(path.join(targetDir, MANIFEST_NAME)) !== manifestInfo.manifestSha256) {
            throw materializationError('TARGET_MANIFEST_MISMATCH', 'Manifesto do runtime materializado divergiu.');
        }
        const state = {
            schema_version: 1,
            version: manifestInfo.version,
            manifest_sha256: manifestInfo.manifestSha256,
            file_count: manifestInfo.files.length,
            managed_entries: manifestInfo.managedEntries,
            materialized_at: new Date().toISOString()
        };
        writeState(targetDir, state);
        journal.phase = 'committed_pending_health';
        journal.committed_at = new Date().toISOString();
        atomicWriteJson(journalPath, journal);
        callFaultInjector(options, 'after_commit', { transactionId });
        return {
            changed: true,
            targetDir,
            transactionId,
            version: manifestInfo.version,
            manifestSha256: manifestInfo.manifestSha256,
            changedEntries: transactionEntries
        };
    } catch (err) {
        const persistedJournal = readJson(journalPath);
        if (persistedJournal) {
            try { rollbackJournal(targetDir, journalPath, persistedJournal); } catch (rollbackError) {
                err.rollbackError = rollbackError;
            }
        } else {
            try { if (stageDir) removeTransactionArtifact(stageDir, targetDir); } catch (_err) {}
            try { if (backupDir) removeTransactionArtifact(backupDir, targetDir); } catch (_err) {}
        }
        if (err && err.jkRuntimeMaterialization) throw err;
        throw materializationError(
            err && err.code ? String(err.code) : 'MATERIALIZATION_FAILED',
            err && err.message ? err.message : String(err),
            err
        );
    } finally {
        releaseLock(lock);
    }
}

function finalizeMaterialization(result) {
    if (!result || !result.changed || !result.transactionId) return { finalized: false };
    const targetDir = path.resolve(result.targetDir);
    const lock = acquireLock(targetDir);
    const journalPath = path.join(targetDir, JOURNAL_RELATIVE_PATH);
    let healthVerified = false;
    try {
        const journal = validateJournal(readJson(journalPath));
        if (journal.transaction_id !== result.transactionId || journal.phase !== 'committed_pending_health') {
            throw materializationError('FINALIZE_JOURNAL_MISMATCH', 'Journal nao corresponde a materializacao ativa.');
        }
        journal.phase = 'health_verified';
        journal.health_verified_at = new Date().toISOString();
        atomicWriteJson(journalPath, journal);
        healthVerified = true;
        const { stageDir, backupDir } = transactionPaths(targetDir, journal.transaction_id);
        try {
            removeTransactionArtifact(stageDir, targetDir);
            removeTransactionArtifact(backupDir, targetDir);
            fs.rmSync(journalPath, { force: true });
            return { finalized: true, transactionId: journal.transaction_id };
        } catch (cleanupError) {
            return {
                finalized: false,
                cleanupPending: true,
                transactionId: journal.transaction_id,
                error: cleanupError && cleanupError.message ? cleanupError.message : String(cleanupError)
            };
        }
    } catch (err) {
        if (healthVerified) {
            return {
                finalized: false,
                cleanupPending: true,
                transactionId: result.transactionId,
                error: err && err.message ? err.message : String(err)
            };
        }
        throw err;
    } finally {
        releaseLock(lock);
    }
}

function rollbackMaterialization(result) {
    if (!result || !result.changed || !result.transactionId) return { rolledBack: false };
    const targetDir = path.resolve(result.targetDir);
    const lock = acquireLock(targetDir);
    const journalPath = path.join(targetDir, JOURNAL_RELATIVE_PATH);
    try {
        const journal = validateJournal(readJson(journalPath));
        if (journal.transaction_id !== result.transactionId) {
            throw materializationError('ROLLBACK_JOURNAL_MISMATCH', 'Journal nao corresponde a materializacao ativa.');
        }
        return rollbackJournal(targetDir, journalPath, journal);
    } finally {
        releaseLock(lock);
    }
}

module.exports = {
    JOURNAL_RELATIVE_PATH,
    LOCK_RELATIVE_PATH,
    MANIFEST_NAME,
    PAYLOAD_JOURNAL_RELATIVE_PATH,
    PAYLOAD_STATE_RELATIVE_PATH,
    STATE_RELATIVE_PATH,
    ensureImmutableRuntimePayloads,
    finalizeMaterialization,
    inspectImmutableRuntimePayloads,
    inspectMaterialization,
    loadValidatedManifest,
    materializeLocalApp,
    recoverInterruptedMaterialization,
    rollbackMaterialization,
    sha256File
};
