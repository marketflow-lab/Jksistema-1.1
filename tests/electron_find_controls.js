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

assert.match(shellSource, /id="screen-search-open"[\s\S]*id="screen-search-input"|id="screen-search-input"[\s\S]*id="screen-search-open"/, 'a toolbar deve expor o campo de pesquisa');
assert.match(shellSource, /role="search"[\s\S]*role="status"[\s\S]*aria-live="polite"/, 'a pesquisa deve anunciar a contagem de forma acessivel');
assert.match(shellSource, /screen-search-previous[\s\S]*screen-search-next[\s\S]*screen-search-close/, 'a pesquisa deve expor anterior, proximo e fechar');
assert.match(shellSource, /Ctrl\+F/, 'a interface deve orientar o atalho para abrir a pesquisa');
assert.match(shellSource, /Shift\+Enter/, 'a interface deve orientar o atalho para o resultado anterior');
assert.match(shellSource, /refreshScreenSearchAfterContextChange[\s\S]*function activateTab[\s\S]*refreshScreenSearchAfterContextChange/, 'trocar de aba deve atualizar a pesquisa');
assert.match(shellSource, /mlBrowserWebview\.findInPage[\s\S]*found-in-page/, 'o webview de contingencia deve ter pesquisa propria');

for (const [name, source] of [['preload.js', preloadSource], ['electron_app/preload.js', packagedPreloadSource]]) {
    assert.match(source, /findInActiveScreen[\s\S]*find-in-active-screen/, `${name} deve expor o inicio da pesquisa`);
    assert.match(source, /stopFindInActiveScreen[\s\S]*stop-find-in-active-screen/, `${name} deve expor a limpeza da pesquisa`);
    assert.match(source, /onAppFindResult[\s\S]*app-find-result/, `${name} deve expor os resultados da pesquisa`);
    assert.match(source, /onAppFindCommand[\s\S]*app-find-command/, `${name} deve receber os atalhos nativos`);
}

assert.match(windowSource, /String\(query \|\| ''\)\.slice\(0, 256\)/, 'o termo deve ter limite antes de chegar ao Electron');
assert.match(windowSource, /getAppFindTarget[\s\S]*embeddedMlBrowserView\.webContents[\s\S]*mainWindow\.webContents/, 'a pesquisa deve escolher entre a tela principal e o navegador ML nativo');
assert.match(windowSource, /appFindUiOpen[\s\S]*key === 'escape'/, 'Escape deve ser interceptado apenas quando a pesquisa estiver aberta');
assert.match(windowSource, /did-attach-webview[\s\S]*bindAppWindowShortcuts/, 'atalhos devem funcionar com foco no webview de contingencia');
assert.match(windowSource, /event\.sender !== mainWindow\.webContents[\s\S]*frame\.parent/, 'a pesquisa deve aceitar somente o shell principal');
assert.match(ipcSource, /ipcMain\.handle\('find-in-active-screen'[\s\S]*assertTrustedElectronShellIpcSender/, 'o IPC de pesquisa deve validar sua origem');
assert.match(ipcSource, /ipcMain\.handle\('stop-find-in-active-screen'[\s\S]*clearSelection/, 'o IPC deve limpar os destaques sem ativar links');

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
            response.end('<!doctype html><html><head><title>Inicio</title></head><body><p>produto teste</p><p>produto teste</p></body></html>');
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
    const origin = `http://127.0.0.1:${server.address().port}`;
    const browser = await chromium.launch({ headless: true });
    try {
        const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
        await page.addInitScript(() => {
            const resultListeners = [];
            const commandListeners = [];
            let ordinal = 0;
            window.__screenFindMock = { calls: [], stops: 0, openStates: [], commandListeners };
            window.electronAPI = {
                getAppZoom: async () => ({ percent: 100 }),
                setAppZoom: async percent => ({ percent }),
                onAppZoomChanged: () => () => {},
                findInActiveScreen: async (query, options = {}) => {
                    const matches = query === 'produto' ? 2 : 0;
                    if (options.newSession !== false) ordinal = matches ? 1 : 0;
                    else if (matches && options.forward === false) ordinal = ordinal <= 1 ? matches : ordinal - 1;
                    else if (matches) ordinal = ordinal >= matches ? 1 : ordinal + 1;
                    window.__screenFindMock.calls.push({ query, options: { ...options } });
                    setTimeout(() => {
                        resultListeners.forEach(listener => listener({
                            token: options.token,
                            matches,
                            activeMatchOrdinal: ordinal,
                            finalUpdate: true
                        }));
                    }, 5);
                    return { success: true };
                },
                stopFindInActiveScreen: async () => {
                    window.__screenFindMock.stops += 1;
                    return { success: true };
                },
                setAppFindOpen: async open => {
                    window.__screenFindMock.openStates.push(!!open);
                    return { success: true, open: !!open };
                },
                onAppFindResult: callback => {
                    resultListeners.push(callback);
                    return () => {};
                },
                onAppFindCommand: callback => {
                    commandListeners.push(callback);
                    return () => {};
                }
            };
        });
        await page.goto(`${origin}/electron_shell.html?appUrl=${encodeURIComponent(`${origin}/dashboard.html`)}`, { waitUntil: 'domcontentloaded' });
        await page.locator('#screen-search-open').click();
        assert.strictEqual(await page.locator('.shell').evaluate(element => element.classList.contains('search-expanded')), true, 'Buscar deve abrir o campo sem esconder a toolbar');
        assert.strictEqual(await page.locator('#screen-search-input').evaluate(element => document.activeElement === element), true, 'o campo deve receber foco');

        await page.locator('#screen-search-input').fill('produto');
        await page.waitForTimeout(250);
        assert.strictEqual(await page.locator('#screen-search-status').textContent(), 'Enter', 'digitar deve aguardar a confirmacao por Enter');
        assert.strictEqual((await page.evaluate(() => window.__screenFindMock.calls)).length, 0, 'digitar nao deve iniciar a pesquisa');
        assert.strictEqual(await page.locator('#screen-search-next').isDisabled(), true, 'a navegacao deve aguardar a primeira pesquisa');

        await page.locator('#screen-search-input').press('Enter');
        await page.waitForFunction(() => document.querySelector('#screen-search-status')?.textContent === '1 de 2');
        assert.strictEqual(await page.locator('#screen-search-next').isDisabled(), false, 'a navegacao deve habilitar quando houver resultados');

        await page.locator('#screen-search-input').press('Enter');
        await page.waitForFunction(() => document.querySelector('#screen-search-status')?.textContent === '2 de 2');
        await page.locator('#screen-search-input').press('Shift+Enter');
        await page.waitForFunction(() => document.querySelector('#screen-search-status')?.textContent === '1 de 2');

        const calls = await page.evaluate(() => window.__screenFindMock.calls);
        assert.strictEqual(calls[0].options.newSession, true, 'o primeiro Enter deve iniciar uma nova sessao de pesquisa');
        assert.deepStrictEqual(calls.slice(-2).map(call => [call.options.newSession, call.options.forward]), [[false, true], [false, false]], 'Enter e Shift+Enter devem navegar na sessao atual');

        await page.locator('#screen-search-input').press('Escape');
        assert.strictEqual(await page.locator('.shell').evaluate(element => element.classList.contains('search-expanded')), false, 'Escape deve fechar o campo');
        assert.strictEqual(await page.locator('#screen-search-input').inputValue(), 'produto', 'fechar deve preservar o termo para uma busca rapida depois');
        assert((await page.evaluate(() => window.__screenFindMock.stops)) > 0, 'fechar deve remover o destaque da tela');

        await page.evaluate(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'f', ctrlKey: true, bubbles: true })));
        await page.waitForFunction(() => document.activeElement === document.querySelector('#screen-search-input'));
        assert.strictEqual(await page.locator('#screen-search-input').evaluate(element => document.activeElement === element), true, 'Ctrl+F deve reabrir e focar a pesquisa');
        const callsBeforeMissingQuery = (await page.evaluate(() => window.__screenFindMock.calls)).length;
        await page.locator('#screen-search-input').fill('ausente');
        await page.waitForTimeout(250);
        assert.strictEqual(await page.locator('#screen-search-status').textContent(), 'Enter', 'um novo termo deve aguardar Enter');
        assert.strictEqual((await page.evaluate(() => window.__screenFindMock.calls)).length, callsBeforeMissingQuery, 'alterar o termo nao deve pesquisar automaticamente');
        await page.locator('#screen-search-input').press('Enter');
        await page.waitForFunction(() => document.querySelector('#screen-search-status')?.textContent === 'Nenhum');
        assert.strictEqual(await page.locator('#screen-search-next').isDisabled(), true, 'sem resultados a navegacao deve permanecer desabilitada');

        await page.locator('#screen-search-clear').click();
        assert.strictEqual(await page.locator('#screen-search-status').textContent(), 'Digite', 'limpar deve voltar ao estado inicial');
        assert.deepStrictEqual(await page.evaluate(() => window.__screenFindMock.openStates.slice(-2)), [false, true], 'o processo principal deve saber quando a busca abre e fecha');

        await page.setViewportSize({ width: 1024, height: 720 });
        await page.evaluate(() => { document.documentElement.style.zoom = '1.5'; });
        const compactLayout = await page.evaluate(() => {
            const toolbar = document.querySelector('.toolbar');
            const search = document.querySelector('#screen-search').getBoundingClientRect();
            const zoom = document.querySelector('.zoom-controls').getBoundingClientRect();
            return {
                toolbarClientWidth: toolbar.clientWidth,
                toolbarScrollWidth: toolbar.scrollWidth,
                searchRight: search.right,
                zoomLeft: zoom.left,
                searchHeight: search.height
            };
        });
        assert(compactLayout.toolbarScrollWidth <= compactLayout.toolbarClientWidth + 1, 'a toolbar nao deve criar rolagem horizontal em 1024x720 com zoom 150%');
        assert(compactLayout.searchRight <= compactLayout.zoomLeft + 1, 'o campo nao deve sobrepor os controles de zoom');
        assert.strictEqual(Math.round(compactLayout.searchHeight), 45, 'o campo deve preservar a altura visual de 30px sob zoom 150%');
    } finally {
        await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
}

runBrowserContract()
    .then(() => console.log('Electron screen find controls checks passed'))
    .catch(error => {
        console.error(error);
        process.exitCode = 1;
    });
