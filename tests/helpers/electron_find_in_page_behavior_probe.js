const { app, BrowserWindow } = require('electron');

app.whenReady().then(async () => {
    const win = new BrowserWindow({
        show: false,
        webPreferences: {
            contextIsolation: true,
            nodeIntegration: false
        }
    });
    const html = `<!doctype html><html><body>
        <div>shell sentinel</div>
        <iframe srcdoc="<p>active sentinel current</p>"></iframe>
        <iframe style="display:none" srcdoc="<p>inactive sentinel hidden</p>"></iframe>
    </body></html>`;
    await win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
    const find = (options) => new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('found-in-page timeout')), 5000);
        const listener = (_event, payload) => {
            if (!payload.finalUpdate) return;
            clearTimeout(timer);
            win.webContents.removeListener('found-in-page', listener);
            resolve(payload);
        };
        win.webContents.on('found-in-page', listener);
        win.webContents.findInPage('sentinel', options);
    });
    const initial = await find({ forward: true, findNext: true });
    const next = await find({ forward: true, findNext: false });
    const previous = await find({ forward: false, findNext: false });
    process.stdout.write(`${JSON.stringify({ initial, next, previous })}\n`);
    win.destroy();
    app.quit();
}).catch(error => {
    console.error(error);
    app.exit(1);
});
