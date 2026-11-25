'use strict';

const { spawn } = require('node:child_process');
const { once } = require('node:events');
const path = require('node:path');
const { allowedUrl } = require('./pesquisa-mercado-runner');

const AVANT_STORE = 'https://chromewebstore.google.com/detail/avantpro-ml/jdefnfmbnchmnjkcknaadaddgjbgephh';

function unavailable() {
    return Object.assign(new Error('Não foi possível abrir o Google Chrome da pesquisa.'), { code: 'research_chrome_unavailable' });
}

function isManualChromeRunning(child) {
    return !!child && Number.isInteger(child.pid) && child.pid > 0 && child.exitCode == null && child.signalCode == null;
}

async function launchManualChrome({ profileDir, url, executableCandidates, spawnProcess = spawn }) {
    if (typeof profileDir !== 'string' || !path.isAbsolute(profileDir) || profileDir.includes('\0')) throw unavailable();
    if (typeof url !== 'string' || url.trim() !== url || /[\x00-\x1f\x7f]/.test(url)
        || (url !== AVANT_STORE && !allowedUrl(url))) throw unavailable();
    let candidates;
    try { candidates = typeof executableCandidates === 'function' ? executableCandidates() : executableCandidates; }
    catch (_error) { throw unavailable(); }
    if (!Array.isArray(candidates)) throw unavailable();

    const args = ['--user-data-dir=' + path.resolve(profileDir), '--new-window', url];
    for (const executable of new Set(candidates)) {
        if (typeof executable !== 'string' || !executable.trim()) continue;
        try {
            const child = spawnProcess(executable, args, { detached: true, stdio: 'ignore', shell: false, windowsHide: false });
            await once(child, 'spawn');
            child.unref();
            return child;
        } catch (_error) {
            // An installed Chrome at a later candidate may still be available.
        }
    }
    throw unavailable();
}

module.exports = { launchManualChrome, isManualChromeRunning };
