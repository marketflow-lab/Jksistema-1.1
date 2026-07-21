'use strict';

const assert = require('assert');
const http = require('http');
const path = require('path');
const { app, BrowserWindow } = require('electron');

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

const server = http.createServer((request, response) => {
  const delayMs = request.url && request.url.startsWith('/slow') ? 1200 : 0;
  setTimeout(() => {
    response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
    response.end('<!doctype html><html><body>Favoritos browser lifecycle test</body></html>');
  }, delayMs);
});

async function run(baseUrl) {
  process.env.JK_APP_URL = `${baseUrl}/app`;
  require(path.join(repoRoot, 'main.js'));
  await app.whenReady();
  const mainWindow = await waitFor(
    () => BrowserWindow.getAllWindows().find(win => !/^Favoritos ML - Trabalhador/.test(win.getTitle())),
    'janela principal do Electron nao foi criada',
  );
  await waitFor(
    async () => mainWindow.webContents.executeJavaScript(`Boolean(
      window.electronAPI
      && typeof window.electronAPI.showEmbeddedMlBrowser === 'function'
      && typeof window.electronAPI.getEmbeddedMlBrowserState === 'function'
      && typeof window.electronAPI.hideEmbeddedMlBrowser === 'function'
    )`, true),
    'preload real nao expos o contrato do navegador incorporado',
  );

  const bounds = { left: 24, top: 72, width: 720, height: 520 };
  const shown = await mainWindow.webContents.executeJavaScript(
    `window.electronAPI.showEmbeddedMlBrowser(${JSON.stringify(`${baseUrl}/ready`)}, ${JSON.stringify(bounds)})`,
    true,
  );
  assert.strictEqual(shown.success, true);
  const attached = await mainWindow.webContents.executeJavaScript('window.electronAPI.getEmbeddedMlBrowserState()', true);
  assert.strictEqual(attached.available, true);
  assert.strictEqual(attached.attached, true);

  const hidden = await mainWindow.webContents.executeJavaScript(
    `window.electronAPI.hideEmbeddedMlBrowser({ destroy: true, preserveAvantProSession: false, reason: 'electron-close-proof' })`,
    true,
  );
  assert.strictEqual(hidden.success, true);
  const afterHide = await mainWindow.webContents.executeJavaScript('window.electronAPI.getEmbeddedMlBrowserState()', true);
  assert.strictEqual(afterHide.available, false);
  assert.strictEqual(afterHide.attached, false);
  const stalePosition = await mainWindow.webContents.executeJavaScript(
    `window.electronAPI.positionEmbeddedMlBrowser(${JSON.stringify(bounds)})`,
    true,
  );
  assert.strictEqual(stalePosition.success, false);
  assert.strictEqual(stalePosition.reason, 'browser-hidden');
  const afterStalePosition = await mainWindow.webContents.executeJavaScript('window.electronAPI.getEmbeddedMlBrowserState()', true);
  assert.strictEqual(afterStalePosition.available, false);
  assert.strictEqual(afterStalePosition.attached, false);

  const race = await mainWindow.webContents.executeJavaScript(`(async () => {
    const showPromise = window.electronAPI.showEmbeddedMlBrowser(${JSON.stringify(`${baseUrl}/slow`)}, ${JSON.stringify(bounds)});
    await new Promise(resolve => setTimeout(resolve, 80));
    const hideResult = await window.electronAPI.hideEmbeddedMlBrowser({
      destroy: true,
      preserveAvantProSession: false,
      reason: 'electron-show-hide-race-proof'
    });
    const showResult = await showPromise;
    const state = await window.electronAPI.getEmbeddedMlBrowserState();
    return { showResult, hideResult, state };
  })()`, true);
  assert.strictEqual(race.hideResult.success, true);
  assert.strictEqual(race.showResult.success, false);
  assert.strictEqual(race.showResult.cancelled, true);
  assert.strictEqual(race.state.available, false);
  assert.strictEqual(race.state.attached, false);

  console.log('FAVORITOS_EMBEDDED_BROWSER_CLOSE_ELECTRON_OK');
}

server.listen(0, '127.0.0.1', () => {
  const address = server.address();
  run(`http://127.0.0.1:${address.port}`)
    .then(() => {
      server.close(() => app.exit(0));
    })
    .catch(error => {
      console.error(error && error.stack ? error.stack : error);
      server.close(() => app.exit(1));
    });
});
