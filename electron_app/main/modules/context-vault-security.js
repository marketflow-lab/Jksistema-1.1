'use strict';

const fs = require('fs');
const path = require('path');

const CONTEXT_VAULT_DIR_NAME = 'ContextVault';
const CLIENT_ID_PATTERN = /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/;

function isPathInside(rootPath, candidatePath) {
    const relative = path.relative(path.resolve(rootPath), path.resolve(candidatePath));
    return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative));
}

function normalizeContextVaultClientId(value) {
    const clientId = String(value || '').trim();
    if (!CLIENT_ID_PATTERN.test(clientId)) {
        throw new Error('Cliente invalido para abrir o Context Vault.');
    }
    return clientId;
}

function assertNoSymbolicLinks(rootPath, candidatePath) {
    const root = path.resolve(rootPath);
    const candidate = path.resolve(candidatePath);
    if (!isPathInside(root, candidate)) {
        throw new Error('Context Vault fora do diretorio autorizado.');
    }

    const relativeParts = path.relative(root, candidate).split(path.sep).filter(Boolean);
    let current = root;
    for (const part of relativeParts) {
        current = path.join(current, part);
        if (!fs.existsSync(current)) continue;
        const stat = fs.lstatSync(current);
        if (stat.isSymbolicLink()) {
            throw new Error('Links simbolicos e junctions nao sao aceitos no Context Vault.');
        }
    }
}

function resolveAuthorizedContextVault(runtimeDir, clientIdValue) {
    const clientId = normalizeContextVaultClientId(clientIdValue);
    const runtimeRoot = path.resolve(runtimeDir);
    const infoRoot = path.resolve(runtimeRoot, 'info');
    const vaultPath = path.resolve(infoRoot, clientId, CONTEXT_VAULT_DIR_NAME);

    if (!fs.existsSync(runtimeRoot)) {
        throw new Error('Runtime local invalido para abrir o Context Vault.');
    }
    const runtimeStat = fs.lstatSync(runtimeRoot);
    if (runtimeStat.isSymbolicLink() || !runtimeStat.isDirectory()) {
        throw new Error('Links simbolicos e junctions nao sao aceitos no runtime local.');
    }
    if (!fs.existsSync(infoRoot)) {
        throw new Error('Diretorio info invalido para abrir o Context Vault.');
    }
    const infoStat = fs.lstatSync(infoRoot);
    if (infoStat.isSymbolicLink()) {
        throw new Error('Links simbolicos e junctions nao sao aceitos no diretorio info.');
    }
    if (!infoStat.isDirectory()) throw new Error('Diretorio info invalido para abrir o Context Vault.');
    if (!isPathInside(runtimeRoot, infoRoot) || !isPathInside(infoRoot, vaultPath)) {
        throw new Error('Context Vault fora do diretorio info autorizado.');
    }
    // Start at runtimeRoot so the `info` segment itself is checked. Starting
    // at infoRoot would trust a junction that redirects the whole data root.
    assertNoSymbolicLinks(runtimeRoot, vaultPath);
    if (!fs.existsSync(vaultPath) || !fs.statSync(vaultPath).isDirectory()) {
        throw new Error('Context Vault ainda nao foi criado para este cliente.');
    }

    const realRuntimeRoot = fs.realpathSync(runtimeRoot);
    const realInfoRoot = fs.realpathSync(infoRoot);
    const realVaultPath = fs.realpathSync(vaultPath);
    if (!isPathInside(realRuntimeRoot, realInfoRoot) || !isPathInside(realInfoRoot, realVaultPath)) {
        throw new Error('Context Vault resolve para fora do diretorio info autorizado.');
    }
    return realVaultPath;
}

async function openAuthorizedContextVault(options = {}) {
    const runtimeDir = String(options.runtimeDir || '').trim();
    const electronShell = options.shell;
    if (!runtimeDir || !electronShell || typeof electronShell.openExternal !== 'function') {
        throw new Error('Integracao do Obsidian indisponivel.');
    }

    const vaultPath = resolveAuthorizedContextVault(runtimeDir, options.clientId);
    const obsidianUrl = `obsidian://open?path=${encodeURIComponent(vaultPath)}`;
    try {
        await electronShell.openExternal(obsidianUrl, { activate: true });
        return { success: true, method: 'obsidian' };
    } catch (_err) {
        if (typeof electronShell.openPath !== 'function') {
            throw new Error('Nao foi possivel abrir o Context Vault.');
        }
        const fallbackError = await electronShell.openPath(vaultPath);
        if (fallbackError) {
            throw new Error('Nao foi possivel abrir o Context Vault no Obsidian nem no Explorador.');
        }
        return { success: true, method: 'folder' };
    }
}

module.exports = {
    CONTEXT_VAULT_DIR_NAME,
    isPathInside,
    normalizeContextVaultClientId,
    resolveAuthorizedContextVault,
    openAuthorizedContextVault
};
