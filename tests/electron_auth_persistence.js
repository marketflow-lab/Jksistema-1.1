'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const vm = require('vm');
const { searchRankingSource } = require('./helpers/favoritos_search_ranking_sources');

const root = path.resolve(__dirname, '..');
const localPathsFile = path.join(root, 'electron_app', 'main', 'modules', 'local-app-paths.js');
const backendFile = path.join(root, 'electron_app', 'main', 'modules', 'backend.js');
const ipcFile = path.join(root, 'electron_app', 'main', 'modules', 'ipc.js');
const syncFile = path.join(root, 'scripts', 'sync-favoritos-runtime.js');

const localPaths = fs.readFileSync(localPathsFile, 'utf8');
const backend = fs.readFileSync(backendFile, 'utf8');
const ipc = fs.readFileSync(ipcFile, 'utf8');
const sync = fs.readFileSync(syncFile, 'utf8');
const favoritosLoginFlow = searchRankingSource(root, { includeRuntime: false, includePublicApi: false });

function extractFunction(source, name, context = {}) {
    const marker = `function ${name}`;
    const start = source.indexOf(marker);
    assert.ok(start >= 0, `funcao ${name} ausente`);
    const paramsOpen = source.indexOf('(', start);
    let paramsDepth = 0;
    let paramsClose = -1;
    for (let index = paramsOpen; index < source.length; index += 1) {
        const char = source[index];
        if (char === '(') paramsDepth += 1;
        if (char === ')') {
            paramsDepth -= 1;
            if (paramsDepth === 0) {
                paramsClose = index;
                break;
            }
        }
    }
    assert.ok(paramsClose > paramsOpen, `parametros de ${name} invalidos`);
    const open = source.indexOf('{', paramsClose);
    assert.ok(open >= 0, `corpo de ${name} ausente`);
    let depth = 0;
    let quote = '';
    let escaped = false;
    let end = -1;
    for (let index = open; index < source.length; index += 1) {
        const char = source[index];
        if (quote) {
            if (escaped) escaped = false;
            else if (char === '\\') escaped = true;
            else if (char === quote) quote = '';
            continue;
        }
        if (char === '"' || char === "'" || char === '`') {
            quote = char;
            continue;
        }
        if (char === '{') depth += 1;
        if (char === '}') {
            depth -= 1;
            if (depth === 0) {
                end = index + 1;
                break;
            }
        }
    }
    assert.ok(end > open, `fim de ${name} ausente`);
    return vm.runInNewContext(`(${source.slice(start, end)})`, context);
}

const resolveElectronUserDataDir = extractFunction(localPaths, 'resolveElectronUserDataDir', {
    path,
    process: { platform: 'win32' }
});
const appData = path.join('C:', 'Users', 'Teste', 'AppData', 'Roaming');
assert.strictEqual(
    resolveElectronUserDataDir({
        appDataDir: appData,
        defaultUserDataDir: path.join('D:', 'perfil-temporario'),
        platform: 'win32'
    }),
    path.resolve(appData, 'JK Sistema Cliente'),
    'execucao dev e empacotada devem usar o mesmo userData canonico'
);
assert.strictEqual(
    resolveElectronUserDataDir({
        configuredDir: path.join('E:', 'perfil-explicito'),
        appDataDir: appData,
        platform: 'win32'
    }),
    path.resolve('E:', 'perfil-explicito'),
    'override explicito deve continuar suportado'
);

const inspectAvantProExtensionStorage = extractFunction(localPaths, 'inspectAvantProExtensionStorage', {
    fs,
    path,
    Date
});
const avantProStorageAuthLooksUsable = extractFunction(localPaths, 'avantProStorageAuthLooksUsable');
const fixtureRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'jk-auth-persistence-'));
try {
    const authV2 = path.join(fixtureRoot, 'auth-v2');
    fs.mkdirSync(authV2);
    fs.writeFileSync(
        path.join(authV2, '000001.log'),
        `accessToken token-redacted loginAt ${Date.now() - 1000} avantproAccounts expiresIn ${Date.now() - 60000}`
    );
    const authV2Info = inspectAvantProExtensionStorage(authV2);
    assert.strictEqual(authV2Info.hasAuthRecord, true, 'accessToken + loginAt devem formar auth V2');
    assert.strictEqual(authV2Info.cacheFresh, false, 'fixture deve ter cache de contas expirado');
    assert.strictEqual(authV2Info.authValid, true, 'cache expirado nao pode invalidar auth V2');
    assert.strictEqual(avantProStorageAuthLooksUsable(authV2Info), true, 'auth V2 deve ser restauravel');

    const accountsOnly = path.join(fixtureRoot, 'accounts-only');
    fs.mkdirSync(accountsOnly);
    fs.writeFileSync(
        path.join(accountsOnly, '000001.log'),
        `avantproAccounts expiresIn ${Date.now() + 3600000}`
    );
    const accountsInfo = inspectAvantProExtensionStorage(accountsOnly);
    assert.strictEqual(accountsInfo.hasAccounts, true, 'fixture deve conter cache de contas');
    assert.strictEqual(accountsInfo.hasAuthRecord, false, 'cache de contas sozinho nao e login');
    assert.strictEqual(accountsInfo.authValid, false, 'accounts sem token nao podem ser login valido');
    assert.strictEqual(avantProStorageAuthLooksUsable(accountsInfo), false, 'accounts sem token nao devem substituir snapshot bom');

    const userOnly = path.join(fixtureRoot, 'user-only');
    fs.mkdirSync(userOnly);
    fs.writeFileSync(path.join(userOnly, '000001.log'), 'avantproUser usuario-antigo');
    const userOnlyInfo = inspectAvantProExtensionStorage(userOnly);
    assert.strictEqual(userOnlyInfo.hasUser, true, 'fixture deve conter cadastro antigo');
    assert.strictEqual(userOnlyInfo.authValid, false, 'cadastro sem token atual nao pode validar login');
    assert.strictEqual(avantProStorageAuthLooksUsable(userOnlyInfo), false, 'usuario antigo nao deve sobrescrever snapshot bom');
} finally {
    fs.rmSync(fixtureRoot, { recursive: true, force: true });
}

assert.match(localPaths, /requestSingleInstanceLock\(\)/, 'Electron deve bloquear segunda instancia');
assert.match(localPaths, /app\.on\('second-instance',[\s\S]*focusPrimaryJkWindow/, 'segunda abertura deve focar a primeira');
assert.doesNotMatch(
    localPaths,
    /JK_DEFAULT_ELECTRON_USER_DATA_DIR\s*=\s*app\.isPackaged\s*\?/,
    'userData nao pode variar entre dev e empacotado'
);
assert.match(
    localPaths,
    /JK_RESET_AUTH_STORAGE_ON_STARTUP_CRASH[\s\S]*entries\.unshift\('Local Storage', 'Session Storage', 'WebStorage'\)/,
    'recuperacao normal deve preservar storage de autenticacao'
);
assert.match(backend, /ses\.cookies\.flushStore\(\)/, 'cookies nativos devem ser descarregados no disco');
assert.match(backend, /for \(let attempt = 1; attempt <= 2[\s\S]*failures\.length === 0/, 'flush deve tentar novamente e relatar falha real');
assert.match(backend, /mercadolibre\.com/, 'cookies de dominios Mercado Libre tambem devem ser persistidos');
assert.match(backend, /cookies\.on\('changed',[\s\S]*schedulePersistentSessionsFlush/, 'mudanca de cookie de auth deve agendar flush');
assert.match(backend, /async function persistAuthenticationState[\s\S]*saveAvantProExtensionStorageSnapshot/, 'persistencia deve incluir snapshot AvantPro');
assert.match(ipc, /before-quit[\s\S]*event\.preventDefault\(\)[\s\S]*persistAuthenticationState\('before-quit-authentication'\)/, 'quit deve aguardar persistencia');
assert.match(backend, /async function stopLocalBackend\(\)[\s\S]*stopManagedLocalServers\('quit'\)[\s\S]*managedServers:\s*managedStop\.servers/, 'quit deve encerrar e confirmar todas as portas gerenciadas mesmo sem PID rastreado');
assert.match(ipc, /\.finally\(async \(\) => \{[\s\S]*await stopLocalBackend\(\)[\s\S]*authenticationQuitPersistenceReady = true[\s\S]*app\.quit\(\)/, 'quit deve aguardar o encerramento do backend antes de liberar a saida');
assert.match(ipc, /if \(!JK_PRIMARY_INSTANCE_LOCK_ACQUIRED\) \{[\s\S]*before-quit-secondary-instance[\s\S]*return;[\s\S]*if \(authenticationQuitPersistenceReady\)/, 'segunda instancia nao deve tocar backend nem marcador do processo principal');
assert.match(ipc, /mercado-livre-auth-flow-completed[\s\S]*saveAvantPro:\s*false/, 'fim do login ML deve descarregar cookies imediatamente');
assert.match(ipc, /periodic-auth-snapshot',\s*\{ saveAvantPro:\s*false \}/, 'timer periodico deve salvar cookies sem sobrescrever snapshot AvantPro nao confirmado');
assert.match(localPaths, /previousSnapshot[\s\S]*\.then\(\(\) => saveAvantProExtensionStorageSnapshotUnlocked\(reason, details\)\)[\s\S]*avantProStorageSnapshotPromise === queuedSnapshot/, 'snapshots AvantPro devem ser enfileirados sem perder uma confirmacao posterior');
assert.match(localPaths, /liveAuthConfirmed[\s\S]*!liveAuthConfirmed && avantProStorageAuthLooksUsable\(existingSnapshotAuth\)[\s\S]*existing-good-kept-without-live-auth-confirmation/, 'snapshot nao confirmado nao pode sobrescrever a ultima sessao AvantPro preservada');
assert.match(favoritosLoginFlow, /getAvantProStorageStatus[\s\S]*storageUsable[\s\S]*validarLoginAvantProAntesDeContinuarFavoritos[\s\S]*liveAuthConfirmed:\s*estado[\s\S]*:\s*true/, 'validacao manual deve aceitar sessao AvantPro persistida e manter confirmacao DOM por padrao');
assert.match(favoritosLoginFlow, /estado\.indeterminado && storageUsable[\s\S]*persisted-session-reused/, 'fallback persistido nao deve sobrescrever snapshot sem nova confirmacao DOM');
assert.match(favoritosLoginFlow, /registrarConfirmacaoUsuarioLoginAvantProBestEffortFavoritos[\s\S]*liveAuthConfirmed:\s*false[\s\S]*\.then\([\s\S]*\.catch\([\s\S]*function renderizarPerguntaLoginAvantFavoritos[\s\S]*criarBotaoLoginFavoritos\('Sim, continuar'[\s\S]*prompt_usuario_confirmou/, 'confirmacao Sim deve disparar snapshot best-effort sem alegar validacao DOM nem aguardar IPC');
assert.match(favoritosLoginFlow, /localStorage\.setItem[\s\S]*saveAvantProStorageSnapshot\('favoritos_usuario_confirmou_login_avant'[\s\S]*\.catch\(\(\) => null\);[\s\S]*if \(!resultadoSnapshot/, 'memoria local deve ser gravada antes do snapshot IPC e sobreviver a falha secundaria');
assert.match(localPaths, /previousDir[\s\S]*fs\.renameSync\(snapshotDir, previousDir\)[\s\S]*fs\.renameSync\(tempDir, snapshotDir\)[\s\S]*fs\.renameSync\(previousDir, snapshotDir\)/, 'troca do snapshot deve preservar e restaurar a versao anterior');
assert.match(localPaths, /function rollbackAvantProStorageRestore[\s\S]*fs\.renameSync\(backupDir, targetDir\)/, 'restore AvantPro deve recolocar storage anterior em caso de falha');
assert.match(localPaths, /function recoverAvantProSnapshotTransactionArtifacts[\s\S]*transaction-artifact-recovery[\s\S]*removeManagedDirectory\(candidate, snapshotRoot\)/, 'startup deve recuperar transacao de snapshot interrompida');
assert.match(localPaths, /snapshotRestore\.reason === 'target-current'[\s\S]*preservedCurrentStorage/, 'recovery nao deve resetar storage atual autenticado');
assert.match(localPaths, /importedAuth[\s\S]*copied !== sourceFiles[\s\S]*rollbackAvantProStorageRestore/, 'importacao parcial do Chrome deve restaurar storage anterior');
assert.match(localPaths, /sourceFiles = countDirectoryFilesWithoutLocks\(source\.storageDir\)[\s\S]*await unloadAvantProExtensionForRecovery\(\)[\s\S]*backupAndResetAvantProExtensionStorage/, 'origem Chrome deve ser enumerada antes de qualquer mutacao do storage ativo');
assert.match(sync, /electron_app\/main\/modules\/local-app-paths\.js/, 'sincronizador deve entregar seletor de perfil');
assert.match(sync, /electron_app\/main\/modules\/backend\.js/, 'sincronizador deve entregar persistencia de sessao');

console.log('Electron auth persistence regression checks passed');
