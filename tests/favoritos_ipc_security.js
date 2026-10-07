'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { browserSource } = require('./helpers/favoritos_browser_sources');
const { executionSource: readExecutionSource } = require('./helpers/favoritos_execution_sources');

const root = path.resolve(__dirname, '..');
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');

const ipc = read('electron_app/main/modules/ipc.js');
const worker = read('electron_app/main/modules/favoritos-worker-browser.js');
const mlBrowser = browserSource(root);
const executionLayout = readExecutionSource(root);

assert.match(ipc, /function assertTrustedFavoritosIpcSender\s*\(/);
assert.match(ipc, /sender === mainWindow\.webContents/);

const protectedChannels = [
    'embedded-ml-browser-show',
    'embedded-ml-browser-state',
    'embedded-ml-browser-position',
    'embedded-ml-browser-hide',
    'embedded-ml-browser-execute',
    'embedded-ml-browser-login-avantpro',
    'embedded-ml-browser-type',
    'embedded-ml-browser-click',
    'favoritos-job-browser-start',
    'favoritos-job-browser-stop',
    'favoritos-worker:start',
    'favoritos-worker:pause',
    'favoritos-worker:resume',
    'favoritos-worker:cancel',
    'favoritos-worker:status',
    'favoritos-worker:show',
    'favoritos-worker:hide',
    'favoritos-worker:stop',
    'favoritos-worker:execute',
    'favoritos-worker:click',
    'favoritos-worker:type',
    'favoritos-workers:start-pool',
    'favoritos-workers:status',
    'favoritos-workers:pause',
    'favoritos-workers:resume',
    'favoritos-workers:show',
    'favoritos-workers:hide',
    'favoritos-workers:stop-pool'
];

for (const channel of protectedChannels) {
    assert.ok(ipc.includes(`ipcMain.handle('${channel}'`), `IPC ausente: ${channel}`);
    assert.ok(
        ipc.includes(`assertTrustedFavoritosIpcSender(event, '${channel}')`),
        `IPC sem validacao de origem: ${channel}`
    );
}

assert.match(worker, /function isAllowedFavoritosWorkerUrl\s*\(/);
assert.match(worker, /isMercadoLivreHost\(host\)/);
assert.match(worker, /host\.endsWith\('\.avantprocloud\.com\.br'\)/);
assert.match(worker, /webContents\.on\('will-navigate'[\s\S]*favoritos-worker-browser-navigation-blocked/);
assert.match(worker, /FAVORITOS_WORKER_MAX_SCRIPT_LENGTH\s*=\s*1024\s*\*\s*1024/);
assert.match(worker, /FAVORITOS_WORKER_EXECUTE_TIMEOUT_MS\s*=\s*12000[\s\S]*favoritosWorkerTimeout[\s\S]*Promise\.race\(\[execution, timeout\]\)/);
assert.match(worker, /FAVORITOS_WORKER_POOL_LIMIT\s*=\s*4/);
assert.match(worker, /workerId === FAVORITOS_WORKER_LEGACY_ID \|\| \/\^w\[1-4\]\$\//);
assert.match(worker, /assertAllowedFavoritosWorkerUrl\(currentUrl\)/);
assert.match(ipc, /embedded-ml-browser-execute[\s\S]*assertAllowedFavoritosWorkerUrl\(view\.webContents\.getURL\(\)\)/);

for (const [name, source] of [
    ['ml-browser', mlBrowser],
    ['execucao-render-layout', executionLayout]
]) {
    assert.match(source, /shellBridge\?\.isTrustedMessage\(event,\s*\[/, `${name}: listener deve usar validador compartilhado de mensagens`);
}

const messageContext = {
    FavoritosV2: {},
    location: { origin: 'http://127.0.0.1:8000' },
    electronAPI: { executeEmbeddedMlBrowser() {}, getEmbeddedMlBrowserState() {} }
};
messageContext.window = messageContext;
messageContext.top = { postMessage() {} };
messageContext.parent = messageContext.top;
vm.createContext(messageContext);
vm.runInContext(read('static/favoritos/v2/browser/shell-bridge.js'), messageContext);
const trustedMessage = messageContext.FavoritosV2.browser.shellBridge.isTrustedMessage;
const allowed = ['jk-ml-browser-execute-result'];
const response = { source: messageContext.top, origin: 'file://', data: { channel: allowed[0] } };
assert.strictEqual(trustedMessage(response, allowed), true, 'shell Electron identificado deve ser aceito');
assert.strictEqual(trustedMessage({ ...response, source: {} }, allowed), false, 'janela externa nao representa o shell');
assert.strictEqual(trustedMessage({ ...response, origin: 'https://untrusted.test' }, allowed), false, 'origem remota continua bloqueada');
assert.strictEqual(trustedMessage({ ...response, data: { channel: 'jk-unexpected' } }, allowed), false, 'canal externo continua bloqueado');
delete messageContext.electronAPI;
assert.strictEqual(trustedMessage(response, allowed), false, 'pagina sem API Electron local nao ganha excecao para file://');

console.log(`OK: ${protectedChannels.length} IPCs do Favoritos e mensagens frame/shell estao protegidos.`);
