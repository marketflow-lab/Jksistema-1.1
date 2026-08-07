const assert = require('assert');
const fs = require('fs');
const { chromium } = require('playwright');

const cdpUrl = String(process.argv[2] || '').trim();
const preferencesPath = String(process.argv[3] || '').trim();

if (!cdpUrl || !preferencesPath) {
    throw new Error('Uso: node electron_zoom_runtime_probe.js <cdp-url> <preferences-path>');
}

async function main() {
    const browser = await chromium.connectOverCDP(cdpUrl);
    try {
        const contexts = browser.contexts();
        assert(contexts.length > 0, 'o Electron deve expor um contexto pelo CDP');
        let page = null;
        for (let attempt = 0; attempt < 240 && !page; attempt += 1) {
            page = contexts.flatMap(context => context.pages()).find(candidate => /electron_shell\.html/i.test(candidate.url())) || null;
            if (!page) await new Promise(resolve => setTimeout(resolve, 250));
        }
        assert(page, 'o shell real do Electron deve estar aberto');
        await page.waitForSelector('#zoom-reset', { timeout: 20000 });
        await page.waitForFunction(() => document.querySelector('#zoom-reset')?.textContent === '100%', null, { timeout: 10000 });

        const initial = await page.evaluate(() => window.electronAPI.getAppZoom());
        assert.strictEqual(initial.percent, 100, 'o perfil de teste deve iniciar em 100%');

        const zoomIn = page.locator('#zoom-in');
        await zoomIn.focus();
        await page.waitForFunction(() => getComputedStyle(document.querySelector('.toolbar')).transform === 'matrix(1, 0, 0, 1, 0, 0)');
        await zoomIn.click();
        await page.waitForFunction(() => document.querySelector('#zoom-reset')?.textContent === '110%');
        const changed = await page.evaluate(() => window.electronAPI.getAppZoom());
        assert.strictEqual(changed.percent, 110, 'o botao + deve atravessar o IPC real');

        const persisted = JSON.parse(fs.readFileSync(preferencesPath, 'utf8'));
        assert.strictEqual(persisted.zoomPercent, 110, 'o zoom deve ser persistido no arquivo estavel do perfil');

        await page.evaluate(() => {
            document.querySelectorAll('iframe.tab-view').forEach(frame => frame.classList.remove('is-active'));
            const frame = document.createElement('iframe');
            frame.id = 'runtime-find-frame';
            frame.className = 'tab-view is-active';
            frame.srcdoc = '<!doctype html><html><body><button id="runtime-find-focus">foco</button><p>runtime-find-probe</p><p>runtime-find-probe</p></body></html>';
            document.querySelector('#views').appendChild(frame);
        });
        await page.waitForFunction(() => {
            try {
                return !!document.querySelector('#runtime-find-frame')?.contentDocument?.querySelector('#runtime-find-focus');
            } catch (_err) {
                return false;
            }
        });
        const runtimeFrameHandle = await page.locator('#runtime-find-frame').elementHandle();
        const appFrame = await runtimeFrameHandle.contentFrame();
        assert(appFrame, 'uma aba isolada do sistema deve estar carregada em iframe');
        const childRejected = await appFrame.evaluate(async () => {
            try {
                await window.electronAPI.setAppZoom(130);
                return false;
            } catch (_err) {
                return true;
            }
        });
        assert.strictEqual(childRejected, true, 'um frame filho nao pode alterar o zoom pelo IPC');

        const childFindRejected = await appFrame.evaluate(async () => {
            try {
                await window.electronAPI.findInActiveScreen('runtime-find-probe', { token: 'child-probe' });
                return false;
            } catch (_err) {
                return true;
            }
        });
        assert.strictEqual(childFindRejected, true, 'um frame filho nao pode iniciar a pesquisa pelo IPC');

        await page.evaluate(() => {
            window.__runtimeFindPayloads = [];
            window.electronAPI.onAppFindResult(payload => window.__runtimeFindPayloads.push(payload));
            document.querySelector('#screen-search-open')?.click();
        });
        await page.waitForFunction(() => document.querySelector('.shell')?.classList.contains('search-expanded'));
        await page.locator('#screen-search-input').fill('runtime-find-probe');
        await page.locator('#screen-search-input').press('Enter');
        await page.waitForFunction(() => document.querySelector('#screen-search-status')?.textContent === '1 de 2');
        const initialFindStatus = await page.locator('#screen-search-status').textContent();
        assert.strictEqual(initialFindStatus, '1 de 2', `a pesquisa real deve encontrar apenas as duas ocorrencias visiveis (recebido: ${initialFindStatus})`);

        await page.locator('#screen-search-next').click();
        await page.waitForTimeout(500);
        const nextFindSnapshot = await page.evaluate(() => ({
            status: document.querySelector('#screen-search-status')?.textContent,
            payloads: window.__runtimeFindPayloads
        }));
        assert.strictEqual(nextFindSnapshot.status, '2 de 2', `proximo deve selecionar a segunda ocorrencia: ${JSON.stringify(nextFindSnapshot)}`);
        await page.locator('#screen-search-previous').click();
        await page.waitForFunction(() => document.querySelector('#screen-search-status')?.textContent === '1 de 2');
        await page.locator('#screen-search-close').click();
        await page.waitForFunction(() => !document.querySelector('.shell')?.classList.contains('search-expanded'));

        await page.locator('#zoom-reset').focus();
        await page.keyboard.press('Control+-');
        await page.waitForFunction(() => document.querySelector('#zoom-reset')?.textContent === '100%');
        assert.strictEqual((await page.evaluate(() => window.electronAPI.getAppZoom())).percent, 100, 'Ctrl+- deve atravessar o atalho nativo do Electron');

        await page.reload({ waitUntil: 'domcontentloaded' });
        await page.waitForFunction(() => document.querySelector('#zoom-reset')?.textContent === '100%');
        assert.strictEqual((await page.evaluate(() => window.electronAPI.getAppZoom())).percent, 100, 'o zoom persistido deve ser reaplicado apos recarregar o shell');

        console.log('Electron zoom runtime probe passed');
    } finally {
        await browser.close();
    }
}

main().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
