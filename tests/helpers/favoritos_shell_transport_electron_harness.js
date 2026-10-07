'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { app, BrowserWindow, BrowserView, ipcMain, session } = require('electron');

const repoRoot = path.resolve(__dirname, '..', '..');
const sourceRoot = path.resolve(process.env.FAVORITOS_TEST_SOURCE_ROOT || repoRoot);
const testDir = process.env.JK_FAVORITOS_TRANSPORT_TEST_DIR;
assert.ok(testDir, 'O runner deve fornecer um diretório temporário isolado.');
app.setPath('userData', path.join(testDir, 'profile'));

const appOrigin = 'http://127.0.0.1';
const appUrl = `${appOrigin}/favoritos-transport-fixture`;
const marketplaceUrl = 'https://lista.mercadolivre.com.br/favoritos-transport-fixture';
const sourceFiles = [
  'static/favoritos/v2/browser/shell-bridge.js',
  'static/favoritos/v2/browser/shell-runtime.js',
  'static/favoritos/v2/browser/page-scripts/runtime.js',
  'static/favoritos/v2/browser/page-scripts/template-bundle-06.js',
  'static/favoritos/v2/browser/page-scripts/template-bundle-07.js',
  'static/pesquisa-mercado/shared-ranking.js',
];
const sources = new Map(sourceFiles.map(file => [file, fs.readFileSync(path.join(sourceRoot, file), 'utf8')]));
let mainWindow = null;
let marketView = null;
let finished = false;

function htmlDocument(body) {
  return '<!doctype html><html><head><meta charset="utf-8"></head><body>' + body + '</body></html>';
}

function createMarketplaceHtml() {
  const cards = [
    { id: 'MLB123456701', title: 'Produto Alpha de teste', sales: 60 },
    { id: 'MLB123456702', title: 'Produto Beta de teste', sales: 180 },
    { id: 'MLB123456703', title: 'Produto Gamma de teste', sales: 120 },
  ].map(item => `
    <li class="ui-search-layout__item" style="display:block;padding:6px">
      <a href="https://produto.mercadolivre.com.br/${item.id.replace('MLB', 'MLB-')}-produto-de-teste-_JM">
        <h2 class="ui-search-item__title">${item.title}</h2>
        <img src="https://http2.mlstatic.com/D-fixture.jpg" width="40" height="40" alt="Foto de teste">
      </a>
      <span class="andes-money-amount"><span class="andes-money-amount__currency-symbol">R$</span>
        <span class="andes-money-amount__fraction">100</span><span class="andes-money-amount__cents">00</span></span>
      <div class="avantpro-product-info-row"><span class="avantpro-product-info-row-label">Vendas do produto</span>
        <span class="avantpro-product-info-row-value">${item.sales}</span></div>
      <div class="avantpro-product-info-row"><span class="avantpro-product-info-row-label">Anuncio criado em</span>
        <span class="avantpro-product-info-row-value">01/01/2026</span></div>
      <div class="avantpro-product-info-row"><span class="avantpro-product-info-row-label">Nome do vendedor</span>
        <span class="avantpro-product-info-row-value">Vendedor de teste</span></div>
    </li>`).join('');
  return htmlDocument('<main><ol class="ui-search-layout">' + cards + '</ol></main>');
}

function createAppHtml() {
  const scripts = sourceFiles.map(file => `<script src="${appOrigin}/assets/${file}"></script>`).join('');
  return htmlDocument(`
    <div id="host" style="width:1000px;height:600px"></div>
    <script>
      window.FavoritosV2 = { browser: {} };
      var mlShellBrowserProxy = null;
      var mlWebviewEl = null;
      var mlBrowserHost = document.getElementById('host');
      var mlBrowserFrameWrapEl = mlBrowserHost;
      var mlWorkModalDialogEl = null;
      var mlUrlInput = null;
      var ML_DEFAULT_URL = ${JSON.stringify(marketplaceUrl)};
      function navegadorMlEmSegundoPlano() { return false; }
      function balaoResultadosMlAberto() { return false; }
      function urlsMercadoLivreEquivalentes(left, right) { return left === right; }
      window.fixtureOrigins = [];
      window.addEventListener('message', event => {
        if (event.source === window.parent && event.data && /-result$/.test(event.data.channel || '')) {
          window.fixtureOrigins.push(event.origin);
        }
      });
    </script>
    ${scripts}
    <script>
      window.runTransportProof = async function () {
        const localPreload = !!(window.electronAPI
          && typeof window.electronAPI.executeEmbeddedMlBrowser === 'function'
          && typeof window.electronAPI.getEmbeddedMlBrowserState === 'function');
        if (!localPreload) throw new Error('O iframe não recebeu o preload local da fixture.');
        mlWebviewEl = criarProxyNavegadorMlShell();
        const withDeadline = promise => Promise.race([
          promise,
          new Promise((_, reject) => setTimeout(() => reject(
            new Error('A resposta legítima do shell file:// ficou pendente por mais de 2 segundos.')
          ), 2000))
        ]);
        const ping = await withDeadline(mlWebviewEl.executeJavaScript('({ proof: "transport" })'));
        if (!ping || ping.proof !== 'transport') throw new Error('Uma resposta de canal incorreto consumiu a requisição pendente.');
        const state = await withDeadline(favoritosBrowserShellBridge.verificarEstado(${JSON.stringify(marketplaceUrl)}, 1500));
        const basic = await withDeadline(mlWebviewEl.executeJavaScript(
          window.FavoritosV2.browser.pageScripts.render('extrair-cards-mercado-livre-basico-webview-1', { p0: '3' })
        ));
        const avant = await withDeadline(mlWebviewEl.executeJavaScript(
          window.FavoritosV2.browser.pageScripts.render('extrair-anuncios-avant-pro-dom-webview-1', { p0: '3' })
        ));
        const ranking = window.PesquisaMercadoRanking.rankListings(avant.anuncios, Date.UTC(2026, 2, 1));
        return {
          localPreload,
          sourceIsTop: window.parent === window.top && window.top !== window,
          appOrigin: window.location.origin,
          responseOrigins: window.fixtureOrigins,
          state,
          basic: basic.anuncios.map(item => ({ id: item.id, title: item.titulo })),
          avant: avant.anuncios.map(item => ({ id: item.id, sales: item.vendas, created: item.data_criacao })),
          ranking: ranking.map(item => item.id),
        };
      };
    </script>`);
}

function createParentHtml() {
  return htmlDocument(`
    <script>
      window.fixtureSent = [];
      window.addEventListener('message', async event => {
        const frame = document.getElementById('app');
        if (!frame || event.source !== frame.contentWindow) return;
        const data = event.data || {};
        const payload = data.payload || {};
        const requestId = payload.requestId;
        if (data.channel !== 'jk-ml-browser-execute' && data.channel !== 'jk-ml-browser-status') return;
        const channel = data.channel + '-result';
        window.fixtureSent.push(data.channel);
        try {
          // A mismatched channel with the same requestId must not consume the pending request.
          event.source.postMessage({ channel: 'jk-ml-browser-type-result', requestId, result: { proof: 'wrong-channel' } }, '*');
          event.source.postMessage({ channel, requestId: 'unknown-request-id', result: { proof: 'unknown-request' } }, '*');
          const result = data.channel === 'jk-ml-browser-execute'
            ? await window.electronAPI.executeEmbeddedMlBrowser(payload.code)
            : await window.electronAPI.getEmbeddedMlBrowserState();
          event.source.postMessage({ channel, requestId, result }, '*');
          event.source.postMessage({ channel, requestId, result: { proof: 'duplicate' } }, '*');
        } catch (error) {
          event.source.postMessage({ channel, requestId, error: error.message }, '*');
        }
      });
    </script>
    <iframe id="app" src="${appUrl}"></iframe>`);
}

function assertFixtureSender(event) {
  assert.strictEqual(event.sender, mainWindow.webContents);
  assert.ok(event.senderFrame && (event.senderFrame.url.startsWith('file:') || event.senderFrame.url === appUrl));
}

async function interceptFixtures() {
  const image = Buffer.from('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7', 'base64');
  const httpHandler = (request, callback) => {
    const pathname = new URL(request.url).pathname;
    if (request.url === appUrl) return callback({ mimeType: 'text/html', data: createAppHtml() });
    const key = pathname.replace(/^\/assets\//, '');
    if (pathname.startsWith('/assets/') && sources.has(key)) return callback({ mimeType: 'text/javascript', data: sources.get(key) });
    return callback({ statusCode: 404, mimeType: 'text/plain', data: 'Unknown isolated fixture.' });
  };
  const httpsHandler = (request, callback) => {
    if (request.url === marketplaceUrl) return callback({ mimeType: 'text/html', data: createMarketplaceHtml() });
    if (request.url === 'https://http2.mlstatic.com/D-fixture.jpg') return callback({ mimeType: 'image/gif', data: image });
    return callback({ statusCode: 404, mimeType: 'text/plain', data: 'Unknown isolated fixture.' });
  };
  await new Promise((resolve, reject) => session.defaultSession.protocol.interceptBufferProtocol('http',
    (request, callback) => httpHandler(request, result => callback({ ...result, data: Buffer.from(result.data) })),
    error => error ? reject(error) : resolve()));
  await new Promise((resolve, reject) => session.defaultSession.protocol.interceptBufferProtocol('https',
    (request, callback) => httpsHandler(request, result => callback({ ...result, data: Buffer.from(result.data) })),
    error => error ? reject(error) : resolve()));
}

async function run() {
  await app.whenReady();
  await interceptFixtures();
  mainWindow = new BrowserWindow({
    show: false,
    width: 1280,
    height: 900,
    webPreferences: {
      preload: path.join(__dirname, 'favoritos_shell_transport_electron_preload.js'),
      nodeIntegration: false,
      nodeIntegrationInSubFrames: true,
      contextIsolation: true,
      webviewTag: true,
      backgroundThrottling: false,
    },
  });
  marketView = new BrowserView({ webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true, backgroundThrottling: false } });
  mainWindow.addBrowserView(marketView);
  marketView.setBounds({ x: 0, y: 0, width: 1280, height: 900 });
  ipcMain.handle('favoritos-transport-fixture:execute', async (event, code) => {
    assertFixtureSender(event);
    return marketView.webContents.executeJavaScript(String(code), true);
  });
  ipcMain.handle('favoritos-transport-fixture:state', event => {
    assertFixtureSender(event);
    return { success: true, available: true, attached: true, url: marketView.webContents.getURL() };
  });
  await marketView.webContents.loadURL(marketplaceUrl);
  const parentFile = path.join(testDir, 'parent.html');
  fs.writeFileSync(parentFile, createParentHtml());
  await mainWindow.loadFile(parentFile);
  const frame = mainWindow.webContents.mainFrame.frames.find(item => item.url === appUrl);
  assert.ok(frame, 'O shell file:// deve conter a página HTTP da fixture.');
  const result = await frame.executeJavaScript('window.runTransportProof().catch(error => ({ error: String(error && error.message || error), responseOrigins: window.fixtureOrigins }))', true);
  if (result.error) throw new Error(result.error + ' Origens recebidas: ' + result.responseOrigins.join(', ') + '.');
  assert.strictEqual(result.localPreload, true);
  assert.strictEqual(result.sourceIsTop, true);
  assert.strictEqual(result.appOrigin, appOrigin);
  assert.ok(result.responseOrigins.length >= 4);
  assert.ok(result.responseOrigins.every(origin => origin === 'file://'));
  assert.strictEqual(result.state.success, true);
  assert.deepStrictEqual(result.basic.map(item => item.id), ['MLB123456701', 'MLB123456702', 'MLB123456703']);
  assert.deepStrictEqual(result.avant.map(item => item.sales), [60, 180, 120]);
  assert.ok(result.avant.every(item => item.created === '01/01/2026'));
  assert.deepStrictEqual(result.ranking, ['MLB123456702', 'MLB123456703', 'MLB123456701']);
  console.log('FAVORITOS_SHELL_TRANSPORT_ELECTRON_OK ' + JSON.stringify({
    electron: process.versions.electron,
    origin: result.responseOrigins[0],
    collected: result.basic.length,
    withAvant: result.avant.length,
    ranking: result.ranking,
    wrongChannelAndUnknownRequestsIgnored: true,
  }));
}

function finish(code) {
  if (finished) return;
  finished = true;
  if (marketView && !marketView.webContents.isDestroyed()) marketView.webContents.destroy();
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.destroy();
  app.exit(code);
}

const watchdog = setTimeout(() => { console.error('O teste isolado de transporte excedeu 30 segundos.'); finish(1); }, 30_000);
run().then(() => { clearTimeout(watchdog); finish(0); }).catch(error => {
  console.error('FAVORITOS_SHELL_TRANSPORT_ELECTRON_FAIL ' + String(error && error.message || error));
  clearTimeout(watchdog);
  finish(1);
});
