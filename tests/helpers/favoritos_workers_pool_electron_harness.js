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

  const legacyBeforePool = await mainWindow.webContents.executeJavaScript(
    "window.electronAPI.startFavoritosWorkerBrowser('https://www.mercadolivre.com.br/', 'w0', { show: false, deferInitialNavigation: true })",
    true,
  );
  assert.strictEqual(legacyBeforePool.workerId, 'w0');
  assert.strictEqual(
    BrowserWindow.getAllWindows().filter(win => win.getTitle() === 'Favoritos ML - Navegador Trabalhador').length,
    1,
    'pre-condicao deve manter exatamente um BrowserWindow legado w0 vivo',
  );

  const initial = await mainWindow.webContents.executeJavaScript(`window.electronAPI.startFavoritosWorkersPool({
    size: 4,
    visible: false,
    initialUrl: 'https://lista.mercadolivre.com.br/sku-inicial',
    deferInitialNavigation: true
  })`, true);
  assert.strictEqual(initial.counts.total, 4);
  assert.strictEqual(initial.counts.active, 4);
  assert.strictEqual(
    BrowserWindow.getAllWindows().filter(win => win.getTitle() === 'Favoritos ML - Navegador Trabalhador').length,
    0,
    'handoff para o pool deve destruir o BrowserWindow legado w0',
  );

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

  const blockedLegacyCalls = await mainWindow.webContents.executeJavaScript(`(async () => {
    const capture = async operation => {
      try {
        await operation();
        return { rejected: false, code: '', message: '' };
      } catch (error) {
        return {
          rejected: true,
          code: String(error && error.code || ''),
          message: String(error && error.message || error || '')
        };
      }
    };
    return {
      execute: await capture(() => window.electronAPI.executeFavoritosWorkerBrowser('true')),
      start: await capture(() => window.electronAPI.startFavoritosWorkerBrowser(
        'https://www.mercadolivre.com.br/',
        'w0',
        { show: false, deferInitialNavigation: true }
      ))
    };
  })()`, true);
  for (const [operation, outcome] of Object.entries(blockedLegacyCalls)) {
    assert.strictEqual(outcome.rejected, true, `${operation} padrao do w0 deve ser rejeitado durante o pool`);
    assert.match(
      `${outcome.code} ${outcome.message}`,
      /FAVORITOS_WORKERS_POOL_ACTIVE|pool de trabalhadores|legado w0/i,
      `${operation} deve explicar que o w0 foi bloqueado pelo pool`,
    );
  }
  assert.strictEqual(
    BrowserWindow.getAllWindows().filter(win => win.getTitle() === 'Favoritos ML - Navegador Trabalhador').length,
    0,
    'start/execute padrao bloqueados nao podem recriar o BrowserWindow w0',
  );
  const legacyMaintenanceDuringPool = await mainWindow.webContents.executeJavaScript(`(async () => ({
    status: await window.electronAPI.getFavoritosWorkerBrowserStatus(),
    stopped: await window.electronAPI.stopFavoritosWorkerBrowser({
      destroy: true,
      skipSessionSave: true,
      reason: 'favoritos-w0-maintenance-during-pool'
    })
  }))()`, true);
  assert.strictEqual(legacyMaintenanceDuringPool.status.workerId, 'w0');
  assert.strictEqual(legacyMaintenanceDuringPool.status.hasWindow, false);
  assert.strictEqual(legacyMaintenanceDuringPool.stopped.workerId, 'w0');
  assert.strictEqual(legacyMaintenanceDuringPool.stopped.hasWindow, false);

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

  const legacyAfterPool = await mainWindow.webContents.executeJavaScript(
    "window.electronAPI.startFavoritosWorkerBrowser('https://www.mercadolivre.com.br/', 'w0', { show: false, deferInitialNavigation: true })",
    true,
  );
  assert.strictEqual(legacyAfterPool.workerId, 'w0', 'apos stopPool, o caminho legado deve voltar a iniciar');
  const legacyStatusAfterPool = await mainWindow.webContents.executeJavaScript(
    "window.electronAPI.getFavoritosWorkerBrowserStatus('w0')",
    true,
  );
  assert.strictEqual(legacyStatusAfterPool.active, true);
  assert.strictEqual(legacyStatusAfterPool.hasWindow, true);
  const legacyWindowAfterPool = BrowserWindow.getAllWindows().find(
    win => win.getTitle() === 'Favoritos ML - Navegador Trabalhador',
  );
  assert.ok(legacyWindowAfterPool, 'w0 deve existir para a prova real de execucao apos o pool');
  await legacyWindowAfterPool.webContents.loadURL('about:blank');
  const legacyExecutionAfterPool = await mainWindow.webContents.executeJavaScript(
    "window.electronAPI.executeFavoritosWorkerBrowser('({ workerId: \\\"w0\\\", value: 42 })')",
    true,
  );
  assert.deepStrictEqual(
    legacyExecutionAfterPool,
    { workerId: 'w0', value: 42 },
    'apos stopPool, o w0 deve voltar a executar JavaScript no Electron real',
  );
  await mainWindow.webContents.executeJavaScript(`window.electronAPI.stopFavoritosWorkerBrowser({
    destroy: true,
    reason: 'favoritos-w0-electron-proof'
  })`, true);
  assert.strictEqual(
    BrowserWindow.getAllWindows().filter(win => win.getTitle() === 'Favoritos ML - Navegador Trabalhador').length,
    0,
    'limpeza final deve destruir o w0 usado para provar o legado',
  );
  console.log(`FAVORITOS_WORKERS_POOL_ELECTRON_OK ${JSON.stringify({ workers, blockedLegacyCalls })}`);
}

run()
  .then(() => app.exit(0))
  .catch(error => {
    console.error(error && error.stack ? error.stack : error);
    app.exit(1);
  });
