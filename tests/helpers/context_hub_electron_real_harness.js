'use strict';

const assert = require('assert');
const path = require('path');
const { app, BrowserWindow } = require('electron');

console.log(`CONTEXT_HUB_ELECTRON_REAL_START ${process.versions.electron || 'missing'}`);

const repoRoot = path.resolve(__dirname, '..', '..');
const timeoutAt = Date.now() + 120_000;

function wait(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function waitFor(predicate, message) {
  let lastError = null;
  while (Date.now() < timeoutAt) {
    try {
      const value = await predicate();
      if (value) return value;
    } catch (error) {
      lastError = error;
    }
    await wait(100);
  }
  throw lastError || new Error(message);
}

require(path.join(repoRoot, 'main.js'));

async function run() {
  await app.whenReady();
  const mainWindow = await waitFor(
    () => BrowserWindow.getAllWindows().find(window => !window.isDestroyed()),
    'janela principal do Electron nao foi criada',
  );
  await waitFor(
    () => mainWindow.webContents.executeJavaScript(
      "Boolean(window.electronAPI && typeof window.electronAPI.openContextVault === 'function')",
      true,
    ),
    'preload real nao expos openContextVault na janela principal',
  );
  await mainWindow.loadFile(path.join(repoRoot, 'static', 'configuracoes.html'));
  const proof = await waitFor(
    async () => {
      const state = await mainWindow.webContents.executeJavaScript(`(() => ({
        preload: Boolean(window.electronAPI && typeof window.electronAPI.openContextVault === 'function'),
        button: Boolean(document.getElementById('tabContextHubBtn')),
        panel: Boolean(document.getElementById('tab-context-hub')),
        hidden: Boolean(document.getElementById('tabContextHubBtn')?.classList.contains('hidden')),
        nodeHidden: typeof window.require === 'undefined' && typeof window.process === 'undefined'
      }))()`, true);
      return Object.values(state).every(Boolean) ? state : null;
    },
    'preload ou UI do Context Hub nao ficaram disponiveis',
  );
  assert.deepStrictEqual(proof, {
    preload: true,
    button: true,
    panel: true,
    hidden: true,
    nodeHidden: true,
  });
  console.log(`CONTEXT_HUB_ELECTRON_REAL_OK ${JSON.stringify(proof)}`);
}

run()
  .then(() => app.exit(0))
  .catch(error => {
    console.error(error && error.stack ? error.stack : error);
    app.exit(1);
  });
