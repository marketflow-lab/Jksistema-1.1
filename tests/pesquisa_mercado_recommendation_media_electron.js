'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const root = path.resolve(__dirname, '..');
const stateChannel = 'jk-pesquisa-mercado:recommendation-media-state';
const releaseChannel = 'jk-pesquisa-mercado:release-recommendation-media';
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

function launch() {
    const exe = require(require.resolve('electron', { paths: [path.join(root, 'electron_app'), root] }));
    const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'jk-pesquisa-video-test-'));
    const env = { ...process.env, ELECTRON_DISABLE_SECURITY_WARNINGS: 'true' };
    delete env.ELECTRON_RUN_AS_NODE;
    try {
        const virtualDisplay = process.platform === 'linux' && !env.DISPLAY;
        const command = virtualDisplay ? 'xvfb-run' : exe;
        const args = virtualDisplay ? ['-a', exe, __filename, profile] : [__filename, profile];
        const result = spawnSync(command, args, {
            cwd: root, env, windowsHide: true, timeout: 45000, encoding: 'utf8'
        });
        if (result.stdout) process.stdout.write(result.stdout);
        if (result.stderr) process.stderr.write(result.stderr);
        assert.ifError(result.error);
        assert.equal(result.status, 0, 'Electron regression failed');
        assert.match(result.stdout, /PESQUISA_RECOMMENDATION_MEDIA_OK/);
    } finally {
        assert.equal(path.dirname(profile), path.resolve(os.tmpdir()));
        assert(path.basename(profile).startsWith('jk-pesquisa-video-test-'));
        fs.rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
    }
}

async function until(check, message) {
    const deadline = Date.now() + 5000;
    do {
        if (await check()) return;
        await pause(30);
    } while (Date.now() < deadline);
    throw new Error(message);
}

function fixture(staticSources) {
    const source = name => staticSources ? ` src="/media/static-${name}.mp4"` : '';
    return `<!doctype html><main><h1 class="ui-pdp-title">Anúncio de teste</h1>
        <section class="ui-pdp-gallery carousel"><video id="main" preload="auto" muted${source('main')}></video></section>
        <section class="ui-recommendations-container">
            <video id="direct" preload="auto" muted${source('direct')}></video>
            <video id="nested" preload="auto" muted><source id="src" type="video/mp4"${source('nested')}></video>
        </section></main>`;
}

async function run() {
    const { app, BrowserWindow, ipcMain, session } = require('electron');
    app.setPath('userData', process.argv[2]);
    app.commandLine.appendSwitch('disable-background-networking');
    await app.whenReady();
    const isolated = session.fromPartition('jk-recommendation-media-test');
    const active = new Map(), bytes = new Map();
    const mp4 = fs.readFileSync(path.join(root, 'img/video.mp4'));
    isolated.protocol.handle('https', request => {
        const { pathname, searchParams } = new URL(request.url);
        if (pathname.startsWith('/MLB-')) {
            return new Response(fixture(searchParams.has('static')), { headers: { 'Content-Type': 'text/html;charset=utf-8' } });
        }
        if (!pathname.endsWith('.mp4')) return new Response(null, { status: 404 });
        const range = /^bytes=(\d+)-(\d*)$/.exec(request.headers.get('Range') || '');
        const start = range ? Number(range[1]) : 0;
        const end = range && range[2] ? Math.min(Number(range[2]), mp4.length - 1) : mp4.length - 1;
        const data = mp4.subarray(start, end + 1);
        bytes.set(pathname, (bytes.get(pathname) || 0) + data.length);
        const headers = { 'Content-Type': 'video/mp4', 'Content-Length': String(data.length),
            'Accept-Ranges': 'bytes', 'Cache-Control': 'no-store' };
        if (range) headers['Content-Range'] = `bytes ${start}-${end}/${mp4.length}`;
        return new Response(data, { status: range ? 206 : 200, headers });
    });
    ipcMain.on(stateChannel, event => { event.returnValue = active.get(event.sender.id) || null; });
    function windowForItem() {
        return new BrowserWindow({ show: false, webPreferences: { session: isolated, contextIsolation: true,
            nodeIntegration: false, backgroundThrottling: false,
            preload: path.join(root, 'electron_app/main/modules/pesquisa-mercado-media-preload.js') } });
    }
    async function open(win, id, token, activeId = id) {
        const url = `https://produto.mercadolivre.com.br/MLB-${id}-produto-_JM`;
        if (activeId) active.set(win.webContents.id, { token, itemId: 'MLB' + activeId, url });
        else active.delete(win.webContents.id);
        await win.loadURL(url);
        await win.webContents.executeJavaScript(`{
            const prefix = '/media/${token}-';
            document.getElementById('main').src = prefix + 'main.mp4';
            document.getElementById('direct').src = prefix + 'direct.mp4';
            const parent = document.querySelector('.ui-recommendations-container');
            const video = document.createElement('video');
            video.id = 'added'; video.muted = true; video.preload = 'auto';
            video.src = prefix + 'added.mp4'; parent.append(video);
            document.getElementById('src').setAttribute('src', prefix + 'nested.mp4');
            document.getElementById('nested').load();
        }`);
    }
    function requested(token, name) { return bytes.get(`/media/${token}-${name}.mp4`) || 0; }
    function held(token) {
        for (const name of ['direct', 'nested', 'added']) {
            assert.equal(requested(token, name), 0, `recommendation ${token}-${name} received bytes before its release`);
        }
    }
    async function released(win, token) {
        win.webContents.send(releaseChannel, token);
        await until(() => ['direct', 'nested', 'added'].every(name => requested(token, name) > 0),
            'released recommendations did not request all three media sources for token ' + token);
        const restored = await win.webContents.executeJavaScript(`['direct','src','added'].map(id =>
            document.getElementById(id).getAttribute('src'))`);
        assert.deepEqual(restored, ['direct', 'nested', 'added'].map(name => `/media/${token}-${name}.mp4`));
    }
    const a = windowForItem(), b = windowForItem();
    try {
        await Promise.all([open(a, '1000000001', 1), open(b, '1000000002', 2)]);
        await until(() => requested(1, 'main') && requested(2, 'main'), 'main gallery video did not request its media');
        a.webContents.send(releaseChannel, 2);
        await pause(180);
        held(1); held(2);
        await released(a, 1);
        held(2);
        await open(a, '1000000001', 3); // A fresh attempt on exactly the same URL.
        await until(() => requested(3, 'main'), 'reused window did not load the gallery');
        a.webContents.send(releaseChannel, 1);
        await pause(180);
        held(3);
        await released(b, 2);
        held(3);
        await released(a, 3);
        for (const [token, activeId] of [[4, null], [5, '9999999999']]) {
            await open(a, '1000000001', token, activeId);
            await until(() => ['direct', 'nested', 'added'].every(name => requested(token, name) > 0),
                'an unrelated navigation deferred recommendation media');
        }
        const url = 'https://produto.mercadolivre.com.br/MLB-1000000001-produto-_JM?static=1';
        active.set(a.webContents.id, { token: 6, itemId: 'MLB1000000001', url });
        await a.loadURL(url);
        await until(() => requested('static', 'main') > 0, 'static gallery did not request its media');
        assert.equal(requested('static', 'direct'), 0);
        assert.equal(requested('static', 'nested'), 0);
        a.webContents.send(releaseChannel, 6);
        await until(() => requested('static', 'direct') > 0 && requested('static', 'nested') > 0,
            'static recommendations did not load after the deferral ended');
        console.log('PESQUISA_RECOMMENDATION_MEDIA_OK');
    } finally {
        a.destroy(); b.destroy();
    }
}

if (process.versions.electron) {
    const { app } = require('electron');
    run().then(() => app.exit(0)).catch(error => { console.error(error); app.exit(1); });
} else {
    launch();
}
