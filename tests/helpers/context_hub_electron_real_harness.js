'use strict';

const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { app, BrowserWindow } = require('electron');
const { pathToFileURL } = require('url');

console.log(`CONTEXT_HUB_ELECTRON_REAL_START ${process.versions.electron || 'missing'}`);

const repoRoot = path.resolve(__dirname, '..', '..');
const timeoutAt = Date.now() + 120_000;
const contextVaultSecurity = require(path.join(
  repoRoot,
  'electron_app',
  'main',
  'modules',
  'context-vault-security.js',
));
const openedVaults = [];
contextVaultSecurity.openAuthorizedContextVault = async ({ runtimeDir, clientId, shell }) => {
  openedVaults.push({ runtimeDir, clientId, shellProvided: Boolean(shell) });
  return { method: 'electron-test-mock', vaultPath: path.join(runtimeDir, 'info', clientId, 'ContextVault') };
};

function startFixtureServer() {
  const configuracoesPath = path.join(repoRoot, 'static', 'configuracoes.html');
  const server = http.createServer((request, response) => {
    const requestUrl = new URL(request.url || '/', 'http://127.0.0.1');
    if (requestUrl.pathname === '/configuracoes.html' || requestUrl.pathname === '/static/configuracoes.html') {
      response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
      response.end(fs.readFileSync(configuracoesPath));
      return;
    }
    if (requestUrl.pathname === '/api/admin/context-hub/status') {
      assert.strictEqual(request.headers.authorization, 'Bearer electron-real-test');
      response.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
      response.end(JSON.stringify({ client_id: '000002', active_generation: { id: 'electron-test' } }));
      return;
    }
    if (/\.js$/i.test(requestUrl.pathname)) {
      response.writeHead(200, { 'Content-Type': 'application/javascript; charset=utf-8' });
      response.end('/* fixture Electron Context Hub */');
      return;
    }
    response.writeHead(404, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ detail: 'fixture-not-found' }));
  });
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve(server));
  });
}

function closeServer(server) {
  return new Promise(resolve => {
    if (!server || !server.listening) return resolve();
    server.close(() => resolve());
    if (typeof server.closeAllConnections === 'function') server.closeAllConnections();
  });
}

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

async function run(fixtureServer, fixturePort) {
  let rogueWindow = null;
  try {
    await app.whenReady();
    const mainWindow = await waitFor(
      () => BrowserWindow.getAllWindows().find(window => !window.isDestroyed()),
      'janela principal do Electron nao foi criada',
    );
    const shellFixturePath = path.join(app.getPath('userData'), 'electron_shell_context_hub_test.html');
    const shellFixture = fs.readFileSync(path.join(repoRoot, 'electron_shell.html'), 'utf8')
      .replaceAll('127.0.0.1:8001', `127.0.0.1:${fixturePort}`)
      .replaceAll('localhost:8001', `localhost:${fixturePort}`);
    fs.writeFileSync(shellFixturePath, shellFixture, 'utf8');
    const shellUrl = new URL(pathToFileURL(shellFixturePath).href);
    shellUrl.searchParams.set('appUrl', `http://127.0.0.1:${fixturePort}/configuracoes.html`);
    shellUrl.searchParams.set('tabPreload', pathToFileURL(path.join(repoRoot, 'electron_tab_preload.js')).href);
    shellUrl.searchParams.set('browserPartition', 'persist:jk-context-hub-electron-real');
    await mainWindow.loadURL(shellUrl.href);

    const configuracoesFrame = await waitFor(
      () => mainWindow.webContents.mainFrame.frames.find(
        frame => new RegExp(`^http://127\\.0\\.0\\.1:${fixturePort}/(?:static/)?configuracoes\\.html(?:[?#]|$)`, 'i').test(frame.url || ''),
      ),
      'Configuracoes nao foi carregada no iframe do shell real',
    );
    const iframeState = await waitFor(
      async () => {
        const state = await configuracoesFrame.executeJavaScript(`(() => ({
          preload: Boolean(window.electronAPI && typeof window.electronAPI.openContextVault === 'function'),
          button: Boolean(document.getElementById('tabContextHubBtn')),
          panel: Boolean(document.getElementById('tab-context-hub')),
          hidden: Boolean(document.getElementById('tabContextHubBtn')?.classList.contains('hidden')),
          nodeHidden: typeof window.require === 'undefined' && typeof window.process === 'undefined'
        }))()`, true);
        return Object.values(state).every(Boolean) ? state : null;
      },
      'preload ou UI do Context Hub nao ficaram disponiveis no iframe',
    );

    const ipcResult = await configuracoesFrame.executeJavaScript(
      "window.electronAPI.openContextVault('Bearer electron-real-test')",
      true,
    );
    assert.strictEqual(ipcResult.method, 'electron-test-mock');
    assert.strictEqual(openedVaults.length, 1, 'IPC autorizado deve chamar exatamente um mock de abertura');
    assert.deepStrictEqual(openedVaults[0], {
      runtimeDir: path.join(repoRoot),
      clientId: '000002',
      shellProvided: true,
    });

    let shellRejected = false;
    try {
      await mainWindow.webContents.executeJavaScript(
        "window.electronAPI.openContextVault('Bearer electron-real-test')",
        true,
      );
    } catch (error) {
      shellRejected = /Origem IPC nao autorizada/i.test(String(error && error.message || error));
    }
    assert.ok(shellRejected, 'frame shell file:// nao deve se passar pela tela Configuracoes');

    rogueWindow = new BrowserWindow({
      show: false,
      webPreferences: {
        preload: path.join(repoRoot, 'preload.js'),
        nodeIntegration: false,
        contextIsolation: true,
      },
    });
    await rogueWindow.loadURL(`http://127.0.0.1:${fixturePort}/configuracoes.html`);
    let rogueRejected = false;
    try {
      await rogueWindow.webContents.executeJavaScript(
        "window.electronAPI.openContextVault('Bearer electron-real-test')",
        true,
      );
    } catch (error) {
      rogueRejected = /Origem IPC nao autorizada/i.test(String(error && error.message || error));
    }
    assert.ok(rogueRejected, 'Configuracoes fora do WebContents principal deve ser rejeitada');
    assert.strictEqual(openedVaults.length, 1, 'origens rejeitadas nao podem abrir o vault');

    await mainWindow.loadFile(path.join(repoRoot, 'static', 'configuracoes.html'));
    const fileIpcResult = await mainWindow.webContents.executeJavaScript(
      "window.electronAPI.openContextVault('Bearer electron-real-test')",
      true,
    );
    assert.strictEqual(fileIpcResult.method, 'electron-test-mock');
    assert.strictEqual(openedVaults.length, 2, 'arquivo Configuracoes exato deve manter compatibilidade');

    const proof = {
      ...iframeState,
      iframe: configuracoesFrame !== mainWindow.webContents.mainFrame,
      ipcMocked: ipcResult.method === 'electron-test-mock',
      fileAllowed: fileIpcResult.method === 'electron-test-mock',
      shellRejected,
      rogueRejected,
    };
    assert.ok(Object.values(proof).every(Boolean));
    console.log(`CONTEXT_HUB_ELECTRON_REAL_OK ${JSON.stringify(proof)}`);
  } finally {
    if (rogueWindow && !rogueWindow.isDestroyed()) rogueWindow.destroy();
    await closeServer(fixtureServer);
  }
}

async function bootstrap() {
  const fixtureServer = await startFixtureServer();
  const address = fixtureServer.address();
  assert.ok(address && typeof address === 'object' && Number(address.port) > 0);
  process.env.JK_CONTEXT_HUB_ELECTRON_TEST_PORT = String(address.port);
  require(path.join(repoRoot, 'main.js'));
  return run(fixtureServer, address.port);
}

bootstrap()
  .then(() => app.exit(0))
  .catch(error => {
    console.error(error && error.stack ? error.stack : error);
    app.exit(1);
  });
