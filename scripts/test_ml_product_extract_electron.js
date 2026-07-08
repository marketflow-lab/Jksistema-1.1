const { app, BrowserWindow, session } = require('electron');
const fs = require('fs');
const os = require('os');
const path = require('path');

const PRODUCT_URL = process.env.ML_PRODUCT_TEST_URL
    || 'https://produto.mercadolivre.com.br/MLB-3104845467';
const OUTPUT = process.env.ML_PRODUCT_TEST_OUTPUT
    || path.resolve(__dirname, '..', 'logs', 'ml_product_extract_test.json');

function wait(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function extractDateScript() {
    const file = path.resolve(__dirname, '..', 'static', 'favoritos', 'tabelas-layout', '01-ml-base-busca.js');
    const source = fs.readFileSync(file, 'utf8');
    const marker = 'const ML_DATE_EXTRACT_SCRIPT = `';
    const start = source.indexOf(marker);
    if (start < 0) throw new Error('ML_DATE_EXTRACT_SCRIPT nao encontrado.');
    const bodyStart = start + marker.length;
    const end = source.indexOf('`;', bodyStart);
    if (end < 0) throw new Error('Fim de ML_DATE_EXTRACT_SCRIPT nao encontrado.');
    const rawTemplateBody = source.slice(bodyStart, end);
    return Function(`return \`${rawTemplateBody}\`;`)();
}

async function waitForLoad(win, timeoutMs = 30000) {
    return new Promise((resolve) => {
        let done = false;
        const finish = (result) => {
            if (done) return;
            done = true;
            clearTimeout(timer);
            win.webContents.removeListener('did-finish-load', onLoad);
            win.webContents.removeListener('did-fail-load', onFail);
            resolve(result);
        };
        const onLoad = () => finish({ loaded: true });
        const onFail = (_event, code, description, url) => finish({ loaded: false, code, description, url });
        const timer = setTimeout(() => finish({ loaded: false, timeout: true }), timeoutMs);
        win.webContents.once('did-finish-load', onLoad);
        win.webContents.once('did-fail-load', onFail);
    });
}

async function main() {
    const useJkProfile = process.env.ML_PRODUCT_TEST_USE_JK_PROFILE === '1';
    const profileDir = useJkProfile
        ? path.resolve(__dirname, '..', 'info', 'electron_user_data')
        : path.join(os.tmpdir(), `jk-ml-product-extract-${Date.now()}`);
    const partition = useJkProfile ? 'persist:jk-sistema-browser' : `persist:ml-product-extract-${Date.now()}`;
    app.setPath('userData', profileDir);
    app.commandLine.appendSwitch('disable-http-cache');
    await app.whenReady();

    const ses = session.fromPartition(partition);
    const win = new BrowserWindow({
        show: false,
        width: 1366,
        height: 900,
        webPreferences: {
            session: ses,
            nodeIntegration: false,
            contextIsolation: true
        }
    });

    await win.loadURL(PRODUCT_URL);
    const load = await waitForLoad(win).catch(err => ({ loaded: false, error: String(err && err.message || err) }));
    await wait(8000);
    const script = extractDateScript();
    const result = await win.webContents.executeJavaScript(script, true).catch(err => ({
        success: false,
        error: String(err && err.message || err)
    }));
    const page = await win.webContents.executeJavaScript(`({
        href: location.href,
        title: document.title,
        bodyTextSample: String(document.body && document.body.innerText || '').slice(0, 600)
    })`, true).catch(() => ({}));

    fs.mkdirSync(path.dirname(OUTPUT), { recursive: true });
    const payload = { ok: true, productUrl: PRODUCT_URL, profileDir, partition, load, page, result };
    fs.writeFileSync(OUTPUT, JSON.stringify(payload, null, 2), 'utf8');
    console.log(JSON.stringify(payload, null, 2));
    win.destroy();
    app.quit();
}

main().catch(err => {
    fs.mkdirSync(path.dirname(OUTPUT), { recursive: true });
    const payload = { ok: false, error: String(err && err.stack || err) };
    fs.writeFileSync(OUTPUT, JSON.stringify(payload, null, 2), 'utf8');
    console.log(JSON.stringify(payload, null, 2));
    app.quit();
    process.exit(1);
});
