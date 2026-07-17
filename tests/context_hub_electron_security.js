'use strict';

const assert = require('assert');
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');

const root = path.resolve(__dirname, '..');
const read = relative => fs.readFileSync(path.join(root, relative), 'utf8');
const security = require('../electron_app/main/modules/context-vault-security');

async function main() {
    const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'jk-context-vault-'));
    try {
        const runtimeDir = path.join(tempRoot, 'local_app');
        const vault = path.join(runtimeDir, 'info', '000002', 'ContextVault');
        fs.mkdirSync(vault, { recursive: true });
        assert.strictEqual(security.resolveAuthorizedContextVault(runtimeDir, '000002'), fs.realpathSync(vault));

        for (const invalid of ['', '..', '../000002', '000002/../000003', 'C:\\vault', '000002/ContextVault']) {
            assert.throws(
                () => security.resolveAuthorizedContextVault(runtimeDir, invalid),
                /Cliente invalido/
            );
        }

        const external = path.join(tempRoot, 'external-vault');
        const linkedTenant = path.join(runtimeDir, 'info', '000003');
        fs.mkdirSync(external, { recursive: true });
        fs.mkdirSync(linkedTenant, { recursive: true });
        let junctionCreated = false;
        try {
            fs.symlinkSync(external, path.join(linkedTenant, 'ContextVault'), 'junction');
            junctionCreated = true;
        } catch (err) {
            if (!['EPERM', 'EACCES', 'UNKNOWN'].includes(String(err && err.code || ''))) throw err;
        }
        if (junctionCreated) {
            assert.throws(
                () => security.resolveAuthorizedContextVault(runtimeDir, '000003'),
                /Links simbolicos|fora do diretorio/
            );
        }

        const externalInfo = path.join(tempRoot, 'external-info');
        const runtimeWithLinkedInfo = path.join(tempRoot, 'runtime-linked-info');
        fs.mkdirSync(path.join(externalInfo, '000004', 'ContextVault'), { recursive: true });
        fs.mkdirSync(runtimeWithLinkedInfo, { recursive: true });
        let infoJunctionCreated = false;
        try {
            fs.symlinkSync(externalInfo, path.join(runtimeWithLinkedInfo, 'info'), 'junction');
            infoJunctionCreated = true;
        } catch (err) {
            if (!['EPERM', 'EACCES', 'UNKNOWN'].includes(String(err && err.code || ''))) throw err;
        }
        if (infoJunctionCreated) {
            assert.throws(
                () => security.resolveAuthorizedContextVault(runtimeWithLinkedInfo, '000004'),
                /Links simbolicos|fora do diretorio/
            );
        }

        let openedUrl = '';
        const obsidianResult = await security.openAuthorizedContextVault({
            runtimeDir,
            clientId: '000002',
            shell: {
                openExternal: async url => { openedUrl = url; },
                openPath: async () => { throw new Error('fallback inesperado'); }
            }
        });
        assert.strictEqual(obsidianResult.method, 'obsidian');
        assert.match(openedUrl, /^obsidian:\/\/open\?path=/);
        assert.ok(decodeURIComponent(openedUrl).includes(path.join('000002', 'ContextVault')));

        let openedPath = '';
        const fallbackResult = await security.openAuthorizedContextVault({
            runtimeDir,
            clientId: '000002',
            shell: {
                openExternal: async () => { throw new Error('sem handler'); },
                openPath: async value => { openedPath = value; return ''; }
            }
        });
        assert.strictEqual(fallbackResult.method, 'folder');
        assert.strictEqual(openedPath, fs.realpathSync(vault));
    } finally {
        fs.rmSync(tempRoot, { recursive: true, force: true });
    }

    const ipc = read('electron_app/main/modules/ipc.js');
    const runtimePreload = read('preload.js');
    const desktopPreload = read('electron_app/preload.js');
    const tabPreload = read('electron_tab_preload.js');
    for (const preload of [runtimePreload, desktopPreload, tabPreload]) {
        assert.match(preload, /openContextVault:\s*\(authToken\)\s*=>\s*ipcRenderer\.invoke\(/);
        assert.doesNotMatch(preload, /openContextVault:\s*\([^)]*path/i);
    }
    assert.match(ipc, /ipcMain\.handle\('context-vault-open'/);
    assert.match(ipc, /assertTrustedContextVaultIpcSender\(event\)/);
    assert.match(ipc, /getLocalBackendJson\('\/api\/admin\/context-hub\/status', authToken\)/);
    assert.match(ipc, /clientId = status\.data && status\.data\.client_id/);
    assert.doesNotMatch(ipc, /context-vault-open'[\s\S]{0,180}\b(?:path|vaultPath)\b/i);

    const backendRuntime = read('electron_app/main/modules/backend.js');
    assert.match(backendRuntime, /JK_CONTEXT_HUB_SURFACE=installed/);
    assert.match(backendRuntime, /JK_CONTEXT_HUB_SURFACE:\s*'installed'/);

    const canonicalHtml = read('static/configuracoes.html');
    const mirrorHtml = read('configuracoes.html');
    assert.strictEqual(
        crypto.createHash('sha256').update(canonicalHtml).digest('hex'),
        crypto.createHash('sha256').update(mirrorHtml).digest('hex'),
        'configuracoes.html deve permanecer espelho da fonte canonica em static/'
    );
    assert.match(canonicalHtml, /id="tabContextHubBtn"[^>]+hidden/);
    assert.match(canonicalHtml, /const canManageUsers = permissions\.full === true/);
    assert.match(canonicalHtml, /tabContextHubBtn\.classList\.remove\('hidden'\)/);
    assert.match(canonicalHtml, /role="status" aria-live="polite"/);
    for (const route of [
        '/api/admin/context-hub/status',
        '/api/admin/context-hub/settings',
        '/api/admin/context-hub/rebuild',
        '/api/admin/context-hub/generations',
        '/api/admin/context-hub/search'
    ]) {
        assert.ok(canonicalHtml.includes(route), `Rota do Context Hub ausente na UI: ${route}`);
    }

    const packageJson = JSON.parse(read('electron_app/package.json'));
    const localApp = packageJson.build.extraResources.find(item => item.to === 'local_app');
    assert.ok(localApp.filter.includes('docs/knowledge/**'));
    assert.ok(localApp.filter.includes('context-bundle-manifest.json'));
    assert.ok(!localApp.filter.some(item => /^(?:info|ContextVault|context_hub|\.obsidian|SKU)\//i.test(item)));

    const verifier = read('electron_app/scripts/verify-installer-package.js');
    assert.match(verifier, /function validateContextBundleAtRoot\(/);
    assert.match(verifier, /Arquivo obsoleto ou nao declarado no Context Hub/);
    assert.match(verifier, /SHA256 divergente no Context Hub/);
    assert.match(verifier, /validateContextBundle\(failures\)/);

    const contextManifest = JSON.parse(read('context-bundle-manifest.json'));
    assert.strictEqual(contextManifest.schema_version, 1);
    assert.strictEqual(contextManifest.source_version, packageJson.version);
    const declaredKnowledge = new Set();
    for (const entry of contextManifest.files) {
        assert.match(entry.path, /^docs\/knowledge\//);
        const absolute = path.resolve(root, entry.path);
        assert.ok(absolute.startsWith(path.resolve(root, 'docs', 'knowledge') + path.sep));
        assert.strictEqual(fs.statSync(absolute).size, entry.size);
        assert.strictEqual(crypto.createHash('sha256').update(fs.readFileSync(absolute)).digest('hex'), entry.sha256);
        declaredKnowledge.add(entry.path);
    }
    const actualKnowledge = [];
    const visitKnowledge = directory => {
        for (const item of fs.readdirSync(directory, { withFileTypes: true })) {
            const absolute = path.join(directory, item.name);
            if (item.isDirectory()) visitKnowledge(absolute);
            if (item.isFile()) actualKnowledge.push(path.relative(root, absolute).replaceAll('\\', '/'));
        }
    };
    visitKnowledge(path.join(root, 'docs', 'knowledge'));
    assert.deepStrictEqual([...declaredKnowledge].sort(), actualKnowledge.sort());

    console.log('OK: Context Hub Electron, UI, espelho e contrato de pacote estao protegidos.');
}

main().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
