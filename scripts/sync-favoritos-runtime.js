'use strict';

const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');

const repoRoot = path.resolve(__dirname, '..');
const manifestPath = path.join(repoRoot, 'static', 'favoritos', 'asset-manifest.json');
const defaultInstallRoot = path.join(process.env.APPDATA || '', 'JK Sistema Cliente', 'local_app');
const defaultPackageRoot = path.join(
    repoRoot,
    'electron_app',
    'dist-client-setup',
    'win-unpacked',
    'resources',
    'local_app'
);

const runtimeCodeFiles = [
    'electron_shell.html',
    'favoritos.html',
    'static/favoritos.html',
    'static/favoritos/asset-manifest.json',
    'backend_api.py',
    'backend/schemas/__init__.py',
    'backend/schemas/favoritos.py',
    'backend/routers/favoritos.py',
    'backend/services/favoritos.py',
    'backend/services/favoritos_busca.py',
    'backend/services/favoritos_core.py',
    'backend/services/favoritos_endpoints.py',
    'backend/services/favoritos_extract.py',
    'backend/services/favoritos_jobs.py',
    'backend/services/favoritos_margem.py',
    'backend/services/favoritos_ml.py',
    'backend/services/favoritos_planilhas_colar.py',
    'backend/services/favoritos_ranking_ia.py',
    'backend/services/favoritos_storage.py',
    'backend/services/shared_sync_merge_user_data.py',
    'backend/services/promocoes_api.py',
    'backend/services/promocoes_api_analise.py',
    'backend/services/promocoes_api_jobs.py',
    'backend/services/promocoes_api_participacoes.py',
    'backend/services/promocoes_common.py',
    'backend/services/promocoes_core_custos.py',
    'electron_app/installer-required-resources.json',
    'electron_app/package.json',
    'electron_app/preload.js',
    'electron_app/main/modules/local-app-paths.js',
    'electron_app/main/modules/backend.js',
    'electron_app/main/modules/ipc.js',
    'electron_app/main/modules/favoritos-worker-browser.js'
];

function fail(message) {
    throw new Error(message);
}

function readJson(filePath) {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function sha256(filePath) {
    const hash = crypto.createHash('sha256');
    hash.update(fs.readFileSync(filePath));
    return hash.digest('hex');
}

function isFile(filePath) {
    try { return fs.statSync(filePath).isFile(); } catch (_err) { return false; }
}

function safeRelative(relativePath) {
    const normalized = String(relativePath || '').replace(/\\/g, '/').replace(/^\.\//, '');
    if (!normalized || path.isAbsolute(normalized) || normalized === '..' || normalized.startsWith('../')) {
        fail(`Caminho relativo inseguro no manifesto: ${relativePath}`);
    }
    return normalized;
}

function resolveInside(root, relativePath) {
    const absoluteRoot = path.resolve(root);
    const resolved = path.resolve(absoluteRoot, safeRelative(relativePath));
    if (resolved !== absoluteRoot && !resolved.startsWith(`${absoluteRoot}${path.sep}`)) {
        fail(`Caminho saiu da raiz permitida: ${relativePath}`);
    }
    return resolved;
}

function loadManifest() {
    if (!isFile(manifestPath)) fail(`Manifesto ausente: ${manifestPath}`);
    const manifest = readJson(manifestPath);
    if (Number(manifest.schema_version) !== 1) fail('Versao de schema do manifesto nao suportada.');
    if (!/^[A-Za-z0-9._-]+$/.test(String(manifest.version || ''))) fail('Versao do manifesto invalida.');
    if (!Array.isArray(manifest.assets) || !manifest.assets.length) fail('Manifesto sem assets do Favoritos.');
    manifest.entrypoint = safeRelative(manifest.entrypoint);
    manifest.assets = [...new Set(manifest.assets.map(safeRelative))];
    return manifest;
}

function manifestRelativeFiles(manifest) {
    return [manifest.entrypoint, ...manifest.assets];
}

function deploymentFiles(manifest) {
    return [...new Set([
        ...runtimeCodeFiles,
        ...manifest.assets.map((relativePath) => `static/${relativePath}`)
    ].map(safeRelative))];
}

function computeManifestHashes(manifest) {
    const hashes = {};
    for (const relativePath of manifestRelativeFiles(manifest)) {
        const source = resolveInside(path.join(repoRoot, 'static'), relativePath);
        if (!isFile(source)) fail(`Asset obrigatorio ausente: static/${relativePath}`);
        hashes[relativePath] = sha256(source);
    }
    return hashes;
}

function updateManifest() {
    const manifest = loadManifest();
    manifest.sha256 = computeManifestHashes(manifest);
    fs.writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
    console.log(`[favoritos-sync] manifesto atualizado: ${Object.keys(manifest.sha256).length} hashes`);
}

function validateManifestHashes(manifest, failures) {
    const expected = manifestRelativeFiles(manifest);
    const hashes = manifest.sha256 && typeof manifest.sha256 === 'object' ? manifest.sha256 : {};
    const actualKeys = Object.keys(hashes).sort();
    const expectedKeys = [...expected].sort();
    if (JSON.stringify(actualKeys) !== JSON.stringify(expectedKeys)) {
        failures.push('Conjunto de hashes do manifesto esta incompleto ou contem arquivos extras.');
    }
    for (const relativePath of expected) {
        const filePath = resolveInside(path.join(repoRoot, 'static'), relativePath);
        if (!isFile(filePath)) {
            failures.push(`Asset obrigatorio ausente: static/${relativePath}`);
            continue;
        }
        const declared = String(hashes[relativePath] || '').toLowerCase();
        if (!/^[a-f0-9]{64}$/.test(declared)) {
            failures.push(`Hash invalido ou ausente: ${relativePath}`);
            continue;
        }
        const actual = sha256(filePath);
        if (actual !== declared) failures.push(`Hash divergente: static/${relativePath}`);
    }
}

function validateSource() {
    const manifest = loadManifest();
    const failures = [];
    validateManifestHashes(manifest, failures);

    const rootHtml = path.join(repoRoot, 'favoritos.html');
    const staticHtml = path.join(repoRoot, 'static', 'favoritos.html');
    if (!isFile(rootHtml) || !isFile(staticHtml)) {
        failures.push('Par de HTML do Favoritos ausente.');
    } else {
        if (!fs.readFileSync(rootHtml).equals(fs.readFileSync(staticHtml))) {
            failures.push('favoritos.html e static/favoritos.html estao divergentes.');
        }
        const html = fs.readFileSync(staticHtml, 'utf8');
        if (!html.includes(`?v=${manifest.version}`)) failures.push('HTML nao referencia a versao atual do manifesto.');
    }

    const loaderPath = path.join(repoRoot, 'static', 'favoritos', 'tabelas-layout.js');
    if (!isFile(loaderPath) || !fs.readFileSync(loaderPath, 'utf8').includes(manifest.version)) {
        failures.push('Loader das tabelas nao referencia a versao atual do manifesto.');
    }

    const files = deploymentFiles(manifest);
    for (const relativePath of files) {
        if (!isFile(resolveInside(repoRoot, relativePath))) failures.push(`Arquivo de entrega ausente: ${relativePath}`);
    }

    if (failures.length) fail(`Fonte do Favoritos incoerente:\n- ${failures.join('\n- ')}`);
    return { manifest, files };
}

function compareTarget(targetRoot, files) {
    const failures = [];
    for (const relativePath of files) {
        const source = resolveInside(repoRoot, relativePath);
        const target = resolveInside(targetRoot, relativePath);
        if (!isFile(target)) {
            failures.push(`ausente: ${relativePath}`);
        } else if (sha256(source) !== sha256(target)) {
            failures.push(`divergente: ${relativePath}`);
        }
    }
    return failures;
}

function deployTransaction(targetRoot, files, label) {
    const absoluteTarget = path.resolve(targetRoot);
    if (!fs.existsSync(absoluteTarget) || !fs.statSync(absoluteTarget).isDirectory()) {
        fail(`Raiz ${label} nao encontrada: ${absoluteTarget}`);
    }

    const workRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'jk-favoritos-sync-'));
    const stagedRoot = path.join(workRoot, 'staged');
    const localSuffix = `.favoritos-sync-${process.pid}`;
    const prepared = [];
    const committed = [];
    try {
        for (const relativePath of files) {
            const source = resolveInside(repoRoot, relativePath);
            const staged = resolveInside(stagedRoot, relativePath);
            fs.mkdirSync(path.dirname(staged), { recursive: true });
            fs.copyFileSync(source, staged);
            if (sha256(source) !== sha256(staged)) fail(`Falha ao preparar ${relativePath}`);

            const target = resolveInside(absoluteTarget, relativePath);
            fs.mkdirSync(path.dirname(target), { recursive: true });
            const temporary = `${target}${localSuffix}.tmp`;
            const backup = `${target}${localSuffix}.bak`;
            if (fs.existsSync(temporary) || fs.existsSync(backup)) {
                fail(`Arquivo temporario de sincronizacao ja existe: ${relativePath}`);
            }
            fs.copyFileSync(staged, temporary);
            if (sha256(staged) !== sha256(temporary)) fail(`Falha ao preparar destino ${relativePath}`);
            prepared.push({ relativePath, target, temporary, backup, existed: isFile(target) });
        }

        for (const item of prepared) {
            if (item.existed) fs.renameSync(item.target, item.backup);
            try {
                fs.renameSync(item.temporary, item.target);
            } catch (error) {
                if (item.existed && isFile(item.backup) && !fs.existsSync(item.target)) {
                    fs.renameSync(item.backup, item.target);
                }
                throw error;
            }
            committed.push(item);
        }

        const mismatches = compareTarget(absoluteTarget, files);
        if (mismatches.length) fail(`Verificacao pos-copia falhou em ${label}: ${mismatches.join(', ')}`);
        for (const item of committed) {
            if (isFile(item.backup)) fs.unlinkSync(item.backup);
        }
        console.log(`[favoritos-sync] ${label}: ${files.length} arquivos sincronizados e verificados`);
    } catch (error) {
        for (const item of [...committed].reverse()) {
            try {
                if (isFile(item.target)) fs.unlinkSync(item.target);
                if (item.existed && isFile(item.backup)) fs.renameSync(item.backup, item.target);
            } catch (_rollbackError) {}
        }
        throw error;
    } finally {
        for (const item of prepared) {
            try { if (isFile(item.temporary)) fs.unlinkSync(item.temporary); } catch (_err) {}
            try { if (isFile(item.backup)) fs.unlinkSync(item.backup); } catch (_err) {}
        }
        fs.rmSync(workRoot, { recursive: true, force: true });
    }
}

function valueAfter(args, flag, fallback) {
    const index = args.indexOf(flag);
    if (index < 0) return fallback;
    if (!args[index + 1]) fail(`Valor ausente para ${flag}`);
    return path.resolve(args[index + 1]);
}

function main() {
    const args = process.argv.slice(2);
    const modes = ['--check', '--install', '--package', '--update-manifest'].filter((flag) => args.includes(flag));
    if (modes.length !== 1) {
        fail('Use exatamente um modo: --check, --install, --package ou --update-manifest.');
    }
    if (modes[0] === '--update-manifest') updateManifest();

    const { manifest, files } = validateSource();
    if (modes[0] === '--install') {
        const target = valueAfter(args, '--install-root', defaultInstallRoot);
        deployTransaction(target, files, 'instalacao');
    } else if (modes[0] === '--package') {
        const target = valueAfter(args, '--package-root', defaultPackageRoot);
        deployTransaction(target, files, 'pacote Electron');
    } else if (modes[0] === '--check' && args.includes('--all-targets')) {
        const installRoot = valueAfter(args, '--install-root', defaultInstallRoot);
        const packageRoot = valueAfter(args, '--package-root', defaultPackageRoot);
        for (const [label, target] of [['instalacao', installRoot], ['pacote Electron', packageRoot]]) {
            const mismatches = compareTarget(target, files);
            if (mismatches.length) fail(`${label} divergente:\n- ${mismatches.join('\n- ')}`);
            console.log(`[favoritos-sync] ${label}: ${files.length} arquivos conferidos`);
        }
    }

    console.log(`[favoritos-sync] fonte coerente: versao ${manifest.version}, ${files.length} arquivos de entrega`);
}

try {
    main();
} catch (error) {
    console.error(`[favoritos-sync] FALHOU: ${error && error.message ? error.message : String(error)}`);
    process.exitCode = 1;
}
