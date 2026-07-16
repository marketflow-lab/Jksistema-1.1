'use strict';

const assert = require('assert');
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

require(path.join(repoRoot, 'main.js'));

async function run() {
  await app.whenReady();
  const mainWindow = await waitFor(
    () => BrowserWindow.getAllWindows().find(win => !/^Favoritos ML - Trabalhador/.test(win.getTitle())),
    'janela principal do Electron nao foi criada',
  );
  await waitFor(
    async () => mainWindow.webContents.executeJavaScript(`Boolean(
      window.electronAPI
      && typeof window.electronAPI.startFavoritosWorkersPool === 'function'
      && typeof window.electronAPI.getFavoritosWorkersPoolStatus === 'function'
      && typeof window.electronAPI.stopFavoritosWorkersPool === 'function'
    )`, true),
    'preload real nao expos o contrato do pool',
  );

  const initial = await mainWindow.webContents.executeJavaScript(`window.electronAPI.startFavoritosWorkersPool({
    size: 4,
    visible: false,
    initialUrl: 'https://lista.mercadolivre.com.br/sku-inicial',
    deferInitialNavigation: true
  })`, true);
  assert.strictEqual(initial.counts.total, 4);
  assert.strictEqual(initial.counts.active, 4);

  const workers = BrowserWindow.getAllWindows()
    .filter(win => /^Favoritos ML - Trabalhador [1-4]/.test(win.getTitle()))
    .map(win => ({
      title: win.getTitle(),
      bounds: win.getBounds(),
      url: win.webContents.getURL(),
      visible: win.isVisible(),
      destroyed: win.isDestroyed(),
    }))
    .sort((left, right) => left.title.localeCompare(right.title));
  assert.strictEqual(workers.length, 4, 'Electron real deve abrir quatro janelas trabalhadoras');
  workers.forEach(worker => {
    assert.strictEqual(worker.bounds.width, 1280);
    assert.ok(worker.bounds.height >= 700 && worker.bounds.height <= 900);
    assert.strictEqual(worker.destroyed, false);
    assert.ok(!worker.url || worker.url === 'about:blank');
  });

  const paused = await mainWindow.webContents.executeJavaScript('window.electronAPI.pauseFavoritosWorkersPool()', true);
  assert.strictEqual(paused.paused, true);
  assert.strictEqual(paused.counts.paused, 4);
  const resumed = await mainWindow.webContents.executeJavaScript('window.electronAPI.resumeFavoritosWorkersPool()', true);
  assert.strictEqual(resumed.paused, false);

  const stopped = await mainWindow.webContents.executeJavaScript(`window.electronAPI.stopFavoritosWorkersPool({
    status: 'done',
    reason: 'favoritos-pool-electron-proof',
    destroy: true
  })`, true);
  assert.strictEqual(stopped.active, false);
  assert.strictEqual(
    BrowserWindow.getAllWindows().filter(win => /^Favoritos ML - Trabalhador [1-4]/.test(win.getTitle())).length,
    0,
  );
  console.log(`FAVORITOS_WORKERS_POOL_ELECTRON_OK ${JSON.stringify({ workers })}`);
}

run()
  .then(() => app.exit(0))
  .catch(error => {
    console.error(error && error.stack ? error.stack : error);
    app.exit(1);
  });
