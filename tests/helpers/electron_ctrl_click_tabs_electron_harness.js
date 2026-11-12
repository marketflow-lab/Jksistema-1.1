'use strict';

const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { pathToFileURL } = require('url');
const { app, BrowserWindow, ipcMain } = require('electron');
const [repoRoot, temporary] = process.argv.slice(2);
app.setPath('userData', path.join(temporary, 'profile'));
app.disableHardwareAcceleration();
let win, server;
let sourceSession;
let popups = 0;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const timer = setTimeout(() => { console.error('Ctrl+click Electron harness timed out'); app.exit(1); }, 75_000);
const shell = code => win.webContents.executeJavaScript(code, true);

async function until(predicate, message) {
    const deadline = Date.now() + 6000;
    while (Date.now() < deadline) {
        const result = await predicate();
        if (result) return result;
        await pause(30);
    }
    throw new Error(message);
}

function fixture() {
    return `<!doctype html><html><head><title>Ctrl navigation fixture</title>
    <script src="/auth/navigation.js"></script>
    <style>body{margin:90px 20px}a,button{display:block;margin:8px;padding:8px;width:500px}#draft{width:200px}</style>
    </head><body><input id="draft" value="unsaved draft">
    <a id="anchor" href="/target.html?sku=Pe%C3%A7a%20A&amp;loja=loja-a&amp;data_inicio=2026-11-01#detalhe"><span>Internal target</span></a>
    <button id="inline" onclick="location.href='/inline.html?lista_id=42&sku=200#item'">Inline location</button>
    <button id="listener">Listener location</button>
    <button id="transition" onclick="navegarComTransicao('/transition.html?sku=300#motivos')">Transition helper</button>
    <button id="mutation" onclick="window.mutations++">Ordinary action</button>
    <script>window.identity=Math.random();window.mutations=0;window.clicks=[];
    window.addEventListener('click',e=>window.clicks.push({id:e.target.id,ctrl:e.ctrlKey,trusted:e.isTrusted}),true);
    document.querySelector('#listener').addEventListener('click',()=>{window.location.href='/listener.html?store_id=loja-a#details';});
    </script></body></html>`;
}

function currentFrame(pathname = '/source.html') {
    return win.webContents.mainFrame.frames.find(frame => {
        try { return new URL(frame.url).pathname === pathname; } catch (_) { return false; }
    });
}

async function activate(id) {
    await shell(`activateTab(${JSON.stringify(id)}); hideTabsToolbar();`);
    await pause(150);
}

async function click(frame, selector, modifiers = 0) {
    const point = await frame.executeJavaScript(`(() => {const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2};})()`, true);
    // The tab iframe occupies the viewport. Keep shell toolbar hidden so it cannot intercept the click.
    await win.webContents.debugger.sendCommand('Input.dispatchMouseEvent', { type: 'mouseMoved', x: point.x, y: point.y, button: 'none', buttons: 0, modifiers }, sourceSession);
    await pause(50);
    for (const [type, buttons] of [['mousePressed', 1], ['mouseReleased', 0]]) {
        await win.webContents.debugger.sendCommand('Input.dispatchMouseEvent', {
            type, x: point.x, y: point.y, button: 'left', buttons, modifiers, clickCount: 1,
        }, sourceSession);
    }
}

async function run() {
    const expected = JSON.parse(fs.readFileSync(path.join(repoRoot, 'electron_app/package.json'), 'utf8')).devDependencies.electron;
    assert.strictEqual(process.versions.electron, expected);
    await app.whenReady();
    for (const channel of ['get-app-zoom', 'get-client-config', 'get-browser-session-partition', 'flush-browser-session',
        'favoritos-workers:status', 'favoritos-worker:status', 'embedded-ml-browser-hide']) {
        ipcMain.handle(channel, () => channel === 'get-app-zoom' ? 100 : {});
    }
    server = http.createServer((request, response) => {
        const pathname = new URL(request.url, 'http://127.0.0.1').pathname;
        if (pathname === '/auth/navigation.js') {
            response.writeHead(200, { 'Content-Type': 'application/javascript' });
            response.end(fs.readFileSync(path.join(repoRoot, 'static/auth/navigation.js'), 'utf8'));
        } else if (/\.html$/.test(pathname)) {
            response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
            response.end(pathname === '/source.html' ? fixture() : '<!doctype html><title>Destination</title><p id="destination">Destination loaded</p>');
        } else { response.writeHead(404); response.end(); }
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = 'http://127.0.0.1:' + server.address().port;
    const shellFile = path.join(temporary, 'electron_shell.html');
    fs.writeFileSync(shellFile, fs.readFileSync(path.join(repoRoot, 'electron_shell.html'), 'utf8')
        .replaceAll('http://127.0.0.1:8001', origin)
        .replace(/src="(static\/[^"?]+)([^"]*)"/g, (_all, relative, query) => `src="${pathToFileURL(path.join(repoRoot, relative)).href}${query}"`));
    win = new BrowserWindow({ show: false, width: 1280, height: 900,
        webPreferences: { preload: path.join(repoRoot, 'preload.js'), nodeIntegration: false,
            nodeIntegrationInSubFrames: true, contextIsolation: true, backgroundThrottling: false },
    });
    win.webContents.setWindowOpenHandler(() => { popups++; return { action: 'deny' }; });
    win.webContents.session.webRequest.onBeforeRequest((details, callback) => {
        const allowed = details.url.startsWith(origin + '/') || details.url.startsWith(pathToFileURL(repoRoot + path.sep).href)
            || details.url.startsWith(pathToFileURL(shellFile).href);
        callback({ cancel: !allowed });
    });
    const target = new URL(pathToFileURL(shellFile).href);
    target.searchParams.set('appUrl', origin + '/source.html');
    await win.loadURL(target.href);
    const source = await until(() => currentFrame(), 'Source iframe was not created');
    await until(() => source.executeJavaScript('!!document.querySelector("#draft") && !!window.JKAuthenticatedMedia', true), 'Source navigation fixture not ready');
    const originalId = await shell('activeId');
    const identity = await source.executeJavaScript('window.identity', true);
    const navigation = await source.executeJavaScript('!!window.navigation', true);
    assert(navigation, 'Electron 28 must expose Navigation API inside the isolated app frame');
    win.webContents.debugger.attach('1.3');
    win.webContents.focus();
    // The real preload setting gives the HTTP iframe its own renderer target.
    // Send trusted input to that target, rather than the surrounding file shell.
    const targets = await win.webContents.debugger.sendCommand('Target.getTargets');
    const sourceTarget = targets.targetInfos.find(target => target.type === 'iframe' && target.url === origin + '/source.html');
    if (sourceTarget) sourceSession = (await win.webContents.debugger.sendCommand('Target.attachToTarget', { targetId: sourceTarget.targetId, flatten: true })).sessionId;
    await pause(200);

    async function expectTab(selector, pathname, search, hash, modifiers = 2) {
        await activate(originalId);
        const count = await shell('tabs.size');
        await click(source, selector, modifiers);
        try { await until(() => shell(`tabs.size===${count + 1}`), 'No internal tab opened for ' + selector); }
        catch (error) {
            console.error('fixture diagnostics', JSON.stringify(await shell('({size:tabs.size,active:activeId,state:[...tabs.values()].map(t=>({url:t.url,loaded:t.loaded,box:t.webview.getBoundingClientRect().toJSON(),load:t.loadState.className}))})')),
                JSON.stringify(await source.executeJavaScript('({clicks:window.clicks,href:location.href,helper:!!window.JKTabNavigation})',true)));
            throw error;
        }
        const latest = await shell('tabs.get(activeId).url');
        const url = new URL(latest);
        assert.strictEqual(url.origin, origin);
        assert.strictEqual(url.pathname, pathname);
        assert.strictEqual(url.search, search);
        assert.strictEqual(url.hash, hash);
        assert.strictEqual(source.url, origin + '/source.html', 'Ctrl+click must preserve source location');
        assert.strictEqual(await source.executeJavaScript('window.identity', true), identity, 'Source document reloaded');
        assert.strictEqual(await source.executeJavaScript('document.querySelector("#draft").value', true), 'unsaved draft');
        assert.strictEqual(await source.executeJavaScript('document.body.classList.contains("jk-page-leaving")', true), false);
        assert.strictEqual(popups, 0, 'Internal Ctrl+click opened a native popup');
    }
    await expectTab('#anchor span', '/target.html', '?sku=Pe%C3%A7a%20A&loja=loja-a&data_inicio=2026-11-01', '#detalhe');
    await expectTab('#anchor', '/target.html', '?sku=Pe%C3%A7a%20A&loja=loja-a&data_inicio=2026-11-01', '#detalhe');
    await expectTab('#inline', '/inline.html', '?lista_id=42&sku=200', '#item');
    await expectTab('#listener', '/listener.html', '?store_id=loja-a', '#details');
    await expectTab('#transition', '/transition.html', '?sku=300', '#motivos');
    await expectTab('#anchor', '/target.html', '?sku=Pe%C3%A7a%20A&loja=loja-a&data_inicio=2026-11-01', '#detalhe', 4);
    console.log('PASS Ctrl/Meta anchors, direct location and transition helper preserve source state and filters');

    await activate(originalId);
    const beforeMutation = await shell('tabs.size');
    await click(source, '#mutation', 2);
    await pause(100);
    assert.strictEqual(await shell('tabs.size'), beforeMutation, 'Ordinary action became a navigation');
    assert.strictEqual(await source.executeJavaScript('window.mutations', true), 1, 'Action must execute exactly once');
    await click(source, '#listener', 0);
    await until(() => currentFrame('/listener.html'), 'Ordinary click was captured by stale Ctrl gesture');
    assert.strictEqual(await shell('tabs.size'), beforeMutation, 'Ordinary click must stay in the active tab');
    console.log('PASS action controls and ordinary click after Ctrl do not create unwanted tabs');

    const member = currentFrame('/listener.html');
    const protectedCount = await shell('tabs.size');
    await member.executeJavaScript(`parent.postMessage({channel:'jk-open-module-tab',payload:{url:'https://foreign.invalid/target.html',reuseExisting:false}},'*');`, true);
    await shell(`window.postMessage({channel:'jk-open-module-tab',payload:{url:${JSON.stringify(origin + '/forged.html')},reuseExisting:false}},'*');`);
    await member.executeJavaScript(`parent.postMessage({channel:'jk-open-module-tab',payload:{url:${JSON.stringify(origin + '/api/private')},reuseExisting:false}},'*');`, true);
    await pause(150);
    assert.strictEqual(await shell('tabs.size'), protectedCount, 'Shell accepted foreign URL, unowned source or non-screen destination');
    assert.strictEqual(popups, 0);
    console.log(`ELECTRON_CTRL_CLICK_TABS_OK Electron ${process.versions.electron}, Chromium ${process.versions.chrome}`);
}

function finish(code) {
    clearTimeout(timer);
    if (win && !win.isDestroyed()) win.destroy();
    server?.close();
    app.exit(code);
}
run().then(() => finish(0)).catch(error => { console.error(error.stack); finish(1); });
