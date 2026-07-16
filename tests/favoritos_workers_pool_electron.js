'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { _electron: electron } = require('playwright');

async function main() {
    const repoRoot = path.resolve(__dirname, '..');
    const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'jk-favoritos-pool-electron-'));
    const electronEnv = { ...process.env };
    delete electronEnv.ELECTRON_RUN_AS_NODE;
    electronEnv.JK_ELECTRON_USER_DATA_DIR = userDataDir;
    electronEnv.JK_LOCAL_BACKEND_PORT = process.env.JK_LOCAL_BACKEND_PORT || '8001';
    let electronApp = null;
    try {
        electronApp = await electron.launch({
            args: ['.'],
            cwd: repoRoot,
            env: electronEnv,
            timeout: 90000
        });
        const mainWindow = await electronApp.firstWindow({ timeout: 90000 });
        await mainWindow.waitForLoadState('domcontentloaded', { timeout: 90000 }).catch(() => null);
        const apiDisponivel = await mainWindow.evaluate(() => ({
            start: typeof window.electronAPI?.startFavoritosWorkersPool === 'function',
            status: typeof window.electronAPI?.getFavoritosWorkersPoolStatus === 'function',
            stop: typeof window.electronAPI?.stopFavoritosWorkersPool === 'function'
        }));
        assert.deepStrictEqual(apiDisponivel, { start: true, status: true, stop: true }, 'preload real deve expor o contrato do pool');

        const statusInicial = await mainWindow.evaluate(() => window.electronAPI.startFavoritosWorkersPool({
            size: 4,
            visible: true,
            initialUrl: 'https://lista.mercadolivre.com.br/sku-inicial',
            deferInitialNavigation: true
        }));
        assert.strictEqual(statusInicial.counts.total, 4);
        assert.strictEqual(statusInicial.counts.active, 4);

        const janelas = await electronApp.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()
            .filter(win => /^Favoritos ML - Trabalhador [1-4]/.test(win.getTitle()))
            .map(win => ({
                title: win.getTitle(),
                bounds: win.getBounds(),
                url: win.webContents.getURL(),
                visible: win.isVisible(),
                destroyed: win.isDestroyed()
            }))
            .sort((a, b) => a.title.localeCompare(b.title)));
        assert.strictEqual(janelas.length, 4, 'Electron real deve abrir quatro janelas trabalhadoras');
        janelas.forEach((janela, index) => {
            assert.strictEqual(janela.bounds.width, 1280);
            assert.ok(janela.bounds.height >= 700 && janela.bounds.height <= 900, 'Electron pode limitar 900px a area util do monitor');
            assert.strictEqual(janela.visible, true);
            assert.strictEqual(janela.destroyed, false);
            assert.ok(!janela.url || janela.url === 'about:blank', 'worker adiado nao deve carregar a pesquisa inicial duplicada');
            if (index > 0) {
                assert.notDeepStrictEqual(janela.bounds, janelas[index - 1].bounds, 'janelas devem ficar em cascata');
            }
        });

        const pausado = await mainWindow.evaluate(() => window.electronAPI.pauseFavoritosWorkersPool());
        assert.strictEqual(pausado.paused, true);
        assert.strictEqual(pausado.counts.paused, 4);
        const retomado = await mainWindow.evaluate(() => window.electronAPI.resumeFavoritosWorkersPool());
        assert.strictEqual(retomado.paused, false);

        const encerrado = await mainWindow.evaluate(() => window.electronAPI.stopFavoritosWorkersPool({
            status: 'done',
            reason: 'favoritos-pool-electron-proof',
            destroy: true
        }));
        assert.strictEqual(encerrado.active, false);
        const restantes = await electronApp.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()
            .filter(win => /^Favoritos ML - Trabalhador [1-4]/.test(win.getTitle())).length);
        assert.strictEqual(restantes, 0, 'encerramento deve destruir todas as janelas trabalhadoras');
        console.log(JSON.stringify({ success: true, workers: janelas }, null, 2));
    } finally {
        if (electronApp) await electronApp.close().catch(() => null);
        const resolved = path.resolve(userDataDir);
        const tempRoot = path.resolve(os.tmpdir());
        if (resolved.startsWith(`${tempRoot}${path.sep}`) && path.basename(resolved).startsWith('jk-favoritos-pool-electron-')) {
            fs.rmSync(resolved, { recursive: true, force: true });
        }
    }
}

main().catch(err => {
    console.error(err);
    process.exitCode = 1;
});
