const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');
const vm = require('vm');
const { chromium } = require('playwright');

const repoRoot = path.resolve(__dirname, '..');
const read = (relativePath) => fs.readFileSync(path.join(repoRoot, relativePath), 'utf8');

const shellSource = read('electron_shell.html');
const preloadSource = read('preload.js');
const packagedPreloadSource = read('electron_app/preload.js');
const windowSource = read('electron_app/main/modules/window.js');
const ipcSource = read('electron_app/main/modules/ipc.js');
const rustDeskSource = read('rustdesk.html');
const staticRustDeskSource = read('static/rustdesk.html');

assert.match(shellSource, /id="zoom-out"[\s\S]*id="zoom-reset"[\s\S]*id="zoom-in"/, 'a toolbar deve expor diminuir, percentual/reset e aumentar zoom');
assert.match(shellSource, /aria-label="Zoom da tela"/, 'o grupo de zoom deve ser acessivel');
assert.match(shellSource, /WINDOW_ZOOM_MIN_PERCENT\s*=\s*70[\s\S]*WINDOW_ZOOM_MAX_PERCENT\s*=\s*150/, 'a interface deve limitar o zoom entre 70% e 150%');
assert.match(shellSource, /scaleBoundsForNativeWindow\([\s\S]*zoomFactor/, 'o shell deve converter bounds CSS para superficies nativas');
assert.match(shellSource, /mlBrowserWebview\.setZoomFactor/, 'o webview de contingencia deve receber o zoom atual');

for (const [name, source] of [['preload.js', preloadSource], ['electron_app/preload.js', packagedPreloadSource]]) {
    assert.match(source, /getAppZoom:\s*\(\)\s*=>\s*ipcRenderer\.invoke\('get-app-zoom'\)/, `${name} deve expor leitura de zoom`);
    assert.match(source, /setAppZoom:\s*\(percent\)\s*=>\s*ipcRenderer\.invoke\('set-app-zoom', percent\)/, `${name} deve expor alteracao de zoom`);
    assert.match(source, /onAppZoomChanged/, `${name} deve atualizar a porcentagem quando o atalho nativo for usado`);
}

assert.match(windowSource, /electron-ui-preferences\.json/, 'a preferencia deve usar arquivo estavel no perfil da maquina');
assert.match(windowSource, /isTrustedAppZoomIpcSender[\s\S]*event\.sender !== mainWindow\.webContents[\s\S]*frame\.parent/, 'o IPC deve aceitar somente o frame principal do shell');
assert.match(windowSource, /handleAppWindowShortcutInput[\s\S]*setAppZoomPercent[\s\S]*before-input-event/, 'atalhos de zoom devem ser interceptados pelo Electron');
assert.match(windowSource, /embeddedMlBrowserView\.webContents[\s\S]*applyAppZoomToWebContents/, 'o BrowserView do Mercado Livre deve receber o mesmo zoom');
assert.match(ipcSource, /ipcMain\.handle\('get-app-zoom'[\s\S]*ipcMain\.handle\('set-app-zoom'/, 'os contratos IPC de zoom devem estar registrados');
assert.match(ipcSource, /getZoomFactor\(\)[\s\S]*x \*= zoomFactor[\s\S]*y \*= zoomFactor/, 'cliques nativos devem acompanhar o zoom do BrowserView');
assert.match(rustDeskSource, /bridged\.frame\.zoomFactor[\s\S]*rect\.width \* zoomFactor/, 'o encaixe do RustDesk deve acompanhar o zoom');
assert.strictEqual(rustDeskSource, staticRustDeskSource, 'as duas superficies do RustDesk devem permanecer espelhadas');

const inlineScripts = Array.from(shellSource.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi), match => match[1].trim()).filter(Boolean);
for (const [index, script] of inlineScripts.entries()) {
    assert.doesNotThrow(() => new vm.Script(script, { filename: `electron_shell.inline-${index + 1}.js` }), 'scripts inline do shell devem compilar');
}

function contentType(requestPath) {
    if (requestPath.endsWith('.js')) return 'application/javascript; charset=utf-8';
    if (requestPath.endsWith('.html')) return 'text/html; charset=utf-8';
    return 'text/plain; charset=utf-8';
}

async function runBrowserContract() {
    const server = http.createServer((request, response) => {
        const requestUrl = new URL(request.url, 'http://127.0.0.1');
        if (requestUrl.pathname === '/dashboard.html') {
            response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
            response.end('<!doctype html><html><head><title>Inicio</title></head><body><h1>Dashboard de teste</h1></body></html>');
            return;
        }
        const relativePath = requestUrl.pathname.replace(/^\/+/, '') || 'electron_shell.html';
        const targetPath = path.resolve(repoRoot, relativePath);
        if (!targetPath.startsWith(`${repoRoot}${path.sep}`) || !fs.existsSync(targetPath) || !fs.statSync(targetPath).isFile()) {
            response.writeHead(404);
            response.end('not found');
            return;
        }
        response.writeHead(200, { 'content-type': contentType(relativePath) });
        response.end(fs.readFileSync(targetPath));
    });

    await new Promise((resolve, reject) => {
        server.once('error', reject);
        server.listen(0, '127.0.0.1', resolve);
    });
    const address = server.address();
    const origin = `http://127.0.0.1:${address.port}`;
    const browser = await chromium.launch({ headless: true });
    try {
        const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
        await page.goto(`${origin}/electron_shell.html?appUrl=${encodeURIComponent(`${origin}/dashboard.html`)}`, { waitUntil: 'domcontentloaded' });
        await page.waitForSelector('#zoom-reset');

        assert.strictEqual(await page.locator('#zoom-reset').textContent(), '100%', 'o zoom inicial deve mostrar 100%');
        await page.locator('#zoom-in').click();
        assert.strictEqual(await page.locator('#zoom-reset').textContent(), '110%', 'o botao + deve aumentar um passo');
        assert.strictEqual(await page.evaluate(() => document.documentElement.style.zoom), '1.1', 'o fallback web deve aplicar o fator visual');

        for (let index = 0; index < 4; index += 1) await page.locator('#zoom-in').click();
        assert.strictEqual(await page.locator('#zoom-reset').textContent(), '150%', 'o zoom deve parar no limite maximo');
        assert.strictEqual(await page.locator('#zoom-in').isDisabled(), true, 'o botao + deve desabilitar no limite maximo');

        await page.locator('#zoom-reset').click();
        assert.strictEqual(await page.locator('#zoom-reset').textContent(), '100%', 'clicar na porcentagem deve restaurar 100%');

        await page.keyboard.press('Control+-');
        assert.strictEqual(await page.locator('#zoom-reset').textContent(), '90%', 'Ctrl+- deve diminuir o zoom');
        const scaled = await page.evaluate(() => scaleBoundsForNativeWindow({ x: 10, y: 20, width: 100, height: 80 }));
        assert.deepStrictEqual(scaled, { x: 9, y: 18, width: 90, height: 72, zoomFactor: 0.9 }, 'bounds nativos devem usar o mesmo fator exibido');

        await page.keyboard.press('Control+0');
        assert.strictEqual(await page.locator('#zoom-reset').textContent(), '100%', 'Ctrl+0 deve restaurar 100%');
    } finally {
        await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
}

runBrowserContract()
    .then(() => console.log('Electron zoom controls checks passed'))
    .catch((error) => {
        console.error(error);
        process.exitCode = 1;
    });
