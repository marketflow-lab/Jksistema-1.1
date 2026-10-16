'use strict';

const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { pathToFileURL } = require('url');
const { app, BrowserWindow } = require('electron');

const [repoRoot, tempRoot, shellSourceFile] = process.argv.slice(2);
const userData = path.join(tempRoot, 'profile');
fs.mkdirSync(userData);
app.setPath('userData', userData);
app.disableHardwareAcceleration();

let window, server;
const requests = new Map();
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const deadline = Date.now() + 60_000;
const timer = setTimeout(() => { console.error('Electron tab scroll harness timed out'); app.exit(1); }, 65_000);
const evaluate = script => window.webContents.executeJavaScript(script, true);

async function until(predicate, description) {
    const expiresAt = Math.min(deadline, Date.now() + 5000);
    while (Date.now() < expiresAt) {
        const result = await predicate();
        if (result) return result;
        await wait(30);
    }
    throw new Error(description);
}

function fixtureHtml(name) {
    return `<!doctype html><html><head><title>${name}</title><style>
        html,body { margin:0; min-height:2600px; }
        #list { position:absolute; top:400px; width:380px; height:260px; overflow:auto; }
        #content { width:1500px; height:2100px; background:linear-gradient(white,blue); }
        #hit { position:fixed; top:140px; left:440px; width:140px; height:30px; }
        #draft { position:fixed; top:190px; left:440px; width:140px; }
    </style></head><body><div id="list"><div id="content"></div></div>
    <button id="hit">Click ${name}</button><input id="draft"><script>
        window.fixtureState={identity:Math.random(),clicks:0,focuses:0,keys:0};
        document.querySelector('#hit').addEventListener('click',()=>fixtureState.clicks++);
        document.addEventListener('focusin',()=>fixtureState.focuses++);
        document.addEventListener('keydown',()=>fixtureState.keys++);
    </script></body></html>`;
}

function frameFor(name) {
    return window.webContents.mainFrame.frames.find(frame => new URL(frame.url).pathname === '/' + name + '.html');
}

async function frameState(frame) {
    return frame.executeJavaScript(`(() => { const list=document.querySelector('#list'); return {
        windowY:scrollY, listY:list.scrollTop, listX:list.scrollLeft,
        draft:document.querySelector('#draft').value, ...fixtureState
    }; })()`, true);
}

function preserved(actual, expected, description) {
    for (const field of ['windowY', 'listY', 'listX', 'draft', 'identity']) {
        assert.strictEqual(actual[field], expected[field], `${description}: ${field}`);
    }
}

async function mouseClick(x, y) {
    await window.webContents.debugger.sendCommand('Input.dispatchMouseEvent', {type:'mouseMoved',x,y,button:'none',buttons:0});
    await wait(30);
    for (const [type, buttons] of [['mousePressed', 1], ['mouseReleased', 0]]) {
        await window.webContents.debugger.sendCommand('Input.dispatchMouseEvent', {
            type, x, y, button: 'left', buttons, clickCount: 1,
        });
    }
    await wait(40);
}

async function clickTab(name) {
    await evaluate('showTabsToolbar(0)');
    const point = await evaluate(`(() => { const box=tabs.get(window.__scrollTabs[${JSON.stringify(name)}]).button.getBoundingClientRect(); return {x:box.left+30,y:box.top+box.height/2}; })()`);
    await mouseClick(point.x, point.y);
    await until(() => evaluate(`activeId===window.__scrollTabs[${JSON.stringify(name)}]`), 'Tab click did not activate ' + name);
}

async function run() {
    const expectedElectron = JSON.parse(fs.readFileSync(path.join(repoRoot, 'electron_app/package.json'), 'utf8')).devDependencies.electron;
    assert.strictEqual(process.versions.electron, expectedElectron, 'Use the application Electron version');
    await app.whenReady();
    server = http.createServer((request, response) => {
        const pathname = new URL(request.url, 'http://127.0.0.1').pathname;
        if (!/^\/[AB]\.html$/.test(pathname)) { response.writeHead(404); response.end(); return; }
        requests.set(pathname, (requests.get(pathname) || 0) + 1);
        response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
        response.end(fixtureHtml(pathname[1]));
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = 'http://127.0.0.1:' + server.address().port;
    const shellFixture = path.join(tempRoot, 'electron_shell.html');
    const source = fs.readFileSync(shellSourceFile, 'utf8')
        .replaceAll('http://127.0.0.1:8001', origin)
        .replace(/src="(static\/[^"?]+)([^\"]*)"/g, (_match, relative, query) => `src="${pathToFileURL(path.join(repoRoot, relative)).href}${query}"`);
    fs.writeFileSync(shellFixture, source, 'utf8');
    window = new BrowserWindow({
        show: false, width: 1280, height: 800,
        webPreferences: { nodeIntegration: false, contextIsolation: true, backgroundThrottling: false },
    });
    window.webContents.session.webRequest.onBeforeRequest((details, callback) => {
        const fixture = details.url.startsWith(origin + '/');
        const localSource = details.url.startsWith(pathToFileURL(repoRoot + path.sep).href);
        const temporaryShell = details.url.startsWith(pathToFileURL(shellFixture).href);
        callback({ cancel: !fixture && !localSource && !temporaryShell });
    });
    const shellUrl = new URL(pathToFileURL(shellFixture).href);
    shellUrl.searchParams.set('appUrl', origin + '/A.html');
    await window.loadURL(shellUrl.href);
    await evaluate(`window.__scrollTabs={A:activeId,B:createTab(${JSON.stringify(origin + '/B.html')},'B')}; activateTab(__scrollTabs.A); showTabsToolbar(0);`);
    const a = await until(() => frameFor('A'), 'Frame A not loaded');
    const b = await until(() => frameFor('B'), 'Frame B not loaded');
    await until(() => a.executeJavaScript('!!window.fixtureState', true), 'Fixture A not ready');
    await until(() => b.executeJavaScript('!!window.fixtureState', true), 'Fixture B not ready');
    assert.strictEqual(await evaluate('tabs.get(__scrollTabs.A).webview.contentDocument === null'), true, 'Fixture must cross file/http origins');
    window.webContents.debugger.attach('1.3');
    window.webContents.focus();

    for (const zoom of [1, 0.8]) {
        window.webContents.setZoomFactor(zoom);
        await wait(100);
        const expected = {};
        for (const [name, frame, values] of [['A', a, [350,550,180]], ['B', b, [610,720,240]]]) {
            await clickTab(name);
            await frame.executeJavaScript(`scrollTo(0,${values[0]}); document.querySelector('#list').scrollTop=${values[1]}; document.querySelector('#list').scrollLeft=${values[2]}; document.querySelector('#draft').value=${JSON.stringify('Draft ' + name + ' zoom ' + zoom)}; void 0;`, true);
            await wait(40);
            expected[name] = await frameState(frame);
            assert.deepStrictEqual([expected[name].windowY,expected[name].listY,expected[name].listX], values);
        }
        for (let cycle = 0; cycle < 3; cycle++) {
            await clickTab('A');
            preserved(await frameState(a), expected.A, `zoom ${zoom}, cycle ${cycle}, A`);
            await clickTab('B');
            preserved(await frameState(b), expected.B, `zoom ${zoom}, cycle ${cycle}, B`);
        }
        assert.strictEqual(frameFor('A'), a, 'Frame A identity must remain stable');
        assert.strictEqual(frameFor('B'), b, 'Frame B identity must remain stable');
        console.log(`PASS scroll/form/document preserved at zoom ${Math.round(zoom * 100)}%`);
    }

    window.webContents.setZoomFactor(1);
    await wait(150);
    await clickTab('A');
    const beforeInactive = await frameState(b);
    // B is later in the DOM and overlaps A; a click must still reach only active A.
    const hitPoint = await a.executeJavaScript("(() => { const r=document.querySelector('#hit').getBoundingClientRect(); return { x:r.left+r.width/2,y:r.top+r.height/2 }; })()", true);
    await mouseClick(hitPoint.x, hitPoint.y);
    assert.strictEqual((await frameState(a)).clicks, 1);
    assert.strictEqual((await frameState(b)).clicks, beforeInactive.clicks);
    const beforeActiveKeys = await frameState(a);
    for (let index = 0; index < 18; index++) {
        await window.webContents.debugger.sendCommand('Input.dispatchKeyEvent', { type:'keyDown', key:'Tab', code:'Tab', windowsVirtualKeyCode:9, nativeVirtualKeyCode:9 });
        await window.webContents.debugger.sendCommand('Input.dispatchKeyEvent', { type:'keyUp', key:'Tab', code:'Tab', windowsVirtualKeyCode:9, nativeVirtualKeyCode:9 });
    }
    const afterInactive = await frameState(b);
    const afterActiveKeys = await frameState(a);
    assert(afterActiveKeys.keys > beforeActiveKeys.keys && afterActiveKeys.focuses > beforeActiveKeys.focuses, 'Keyboard traversal must actually reach the active iframe');
    assert.strictEqual(afterInactive.focuses, beforeInactive.focuses, 'Inactive iframe must be skipped by keyboard focus');
    assert.strictEqual(afterInactive.keys, beforeInactive.keys, 'Inactive iframe must receive no keyboard input');
    console.log('PASS inactive overlapping iframe receives no clicks or keyboard focus');

    await evaluate(`window.__nativeBounds=[]; window.electronAPI={
        showEmbeddedMlBrowser:async()=>({success:false}),
        positionEmbeddedMlBrowser:async bounds=>{__nativeBounds.push(bounds);return {success:false};},
        hideEmbeddedMlBrowser:async()=>({success:true}), executeEmbeddedMlBrowser:async()=>({success:false})
    }; void 0;`);
    await b.executeJavaScript(`parent.postMessage({channel:'jk-ml-browser-position',payload:{bounds:{left:10,top:20,width:400,height:300}}},'*');`, true);
    await until(() => evaluate('__nativeBounds.length>0'), 'Inactive native-browser message not handled');
    assert.strictEqual(await evaluate('__nativeBounds[0].background'), true, 'Inactive geometry must remain marked background');
    assert.strictEqual(await evaluate('mlBrowserVisibleByTab.get(__scrollTabs.B)'), false);
    console.log('PASS inactive frame geometry cannot mark native browser visible');

    const survivor = await frameState(a);
    await evaluate("reorderTabBefore(__scrollTabs.A,''); closeTab(__scrollTabs.B);");
    preserved(await frameState(a), survivor, 'Reorder/close sibling preserves surviving document');
    assert.strictEqual(requests.get('/A.html'), 1, 'A must never reload');
    assert.strictEqual(requests.get('/B.html'), 1, 'B must never reload');
    console.log(`ELECTRON_TAB_SCROLL_OK Electron ${process.versions.electron}, Chromium ${process.versions.chrome}, file-to-http fixtures`);
}

function finish(code) {
    clearTimeout(timer);
    if (window && !window.isDestroyed()) window.destroy();
    server?.close();
    app.exit(code);
}
run().then(() => finish(0)).catch(error => { console.error(error.stack); finish(1); });
