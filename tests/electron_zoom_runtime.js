const assert = require('assert');
const fs = require('fs');
const net = require('net');
const os = require('os');
const path = require('path');
const { spawn, spawnSync } = require('child_process');

const repoRoot = path.resolve(__dirname, '..');
const electronPath = path.join(repoRoot, 'node_modules', 'electron', 'dist', 'electron.exe');
const probePath = path.join(__dirname, 'helpers', 'electron_zoom_runtime_probe.js');

function reservePort() {
    return new Promise((resolve, reject) => {
        const server = net.createServer();
        server.once('error', reject);
        server.listen(0, '127.0.0.1', () => {
            const address = server.address();
            server.close(error => error ? reject(error) : resolve(address.port));
        });
    });
}

async function waitForCdp(cdpUrl, child, output) {
    for (let attempt = 0; attempt < 160; attempt += 1) {
        if (child.exitCode !== null) {
            throw new Error(`Electron de teste encerrou antes do CDP.\n${output.join('')}`);
        }
        try {
            const response = await fetch(`${cdpUrl}/json/version`);
            if (response.ok) return;
        } catch (_err) {}
        await new Promise(resolve => setTimeout(resolve, 250));
    }
    throw new Error(`CDP do Electron de teste nao ficou pronto.\n${output.join('')}`);
}

async function stopChild(child) {
    if (!child || child.exitCode !== null) return;
    child.kill();
    await Promise.race([
        new Promise(resolve => child.once('exit', resolve)),
        new Promise(resolve => setTimeout(resolve, 5000))
    ]);
    if (child.exitCode === null) child.kill('SIGKILL');
}

function removeSafeTestProfile(testProfile) {
    const resolvedProfile = path.resolve(testProfile);
    const resolvedTemp = path.resolve(os.tmpdir());
    assert(resolvedProfile.startsWith(`${resolvedTemp}${path.sep}`), 'o perfil descartavel deve ficar dentro da pasta temporaria');
    assert(path.basename(resolvedProfile).startsWith('jk-zoom-runtime-'), 'o perfil descartavel deve ter o prefixo esperado');
    fs.rmSync(resolvedProfile, { recursive: true, force: true });
}

async function main() {
    assert(fs.existsSync(electronPath), 'o Electron local deve estar instalado');
    const port = await reservePort();
    const cdpUrl = `http://127.0.0.1:${port}`;
    const testProfile = fs.mkdtempSync(path.join(os.tmpdir(), 'jk-zoom-runtime-'));
    const preferencesPath = path.join(testProfile, 'electron-ui-preferences.json');
    fs.writeFileSync(
        path.join(testProfile, 'client-config.json'),
        `${JSON.stringify({ appUrl: 'http://127.0.0.1:8001/dashboard.html' }, null, 2)}\n`,
        'utf8'
    );
    const env = {
        ...process.env,
        JK_APP_ROOT_DIR: repoRoot,
        JK_ELECTRON_USER_DATA_DIR: testProfile,
        JK_ALLOW_MULTIPLE_INSTANCES_FOR_TESTS: '1',
        JK_CONTEXT_HUB_ELECTRON_TEST_PORT: String(port),
        JK_BROWSER_SESSION_PARTITION: 'persist:jk-zoom-runtime-test'
    };
    delete env.ELECTRON_RUN_AS_NODE;
    const output = [];
    const child = spawn(electronPath, [`--remote-debugging-port=${port}`, repoRoot], {
        cwd: repoRoot,
        env,
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'pipe']
    });
    child.stdout.on('data', chunk => output.push(chunk.toString()));
    child.stderr.on('data', chunk => output.push(chunk.toString()));

    try {
        await waitForCdp(cdpUrl, child, output);
        const probe = spawnSync(process.execPath, [probePath, cdpUrl, preferencesPath], {
            cwd: repoRoot,
            encoding: 'utf8',
            timeout: 60000
        });
        if (probe.stdout) process.stdout.write(probe.stdout);
        if (probe.stderr) process.stderr.write(probe.stderr);
        assert.strictEqual(probe.status, 0, `o probe real deve concluir sem falhas\n${output.join('')}`);
        console.log('Electron zoom isolated runtime checks passed');
    } finally {
        await stopChild(child);
        removeSafeTestProfile(testProfile);
    }
}

main().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
