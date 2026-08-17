const assert = require('assert');
const fs = require('fs');
const path = require('path');

const repoRoot = path.resolve(__dirname, '..');
const backendSource = fs.readFileSync(
  path.join(repoRoot, 'electron_app', 'main', 'modules', 'backend.js'),
  'utf8'
);
const localPathsSource = fs.readFileSync(
  path.join(repoRoot, 'electron_app', 'main', 'modules', 'local-app-paths.js'),
  'utf8'
);
const materializerSource = fs.readFileSync(
  path.join(repoRoot, 'electron_app', 'main', 'modules', 'backend-runtime-materializer.js'),
  'utf8'
);
const electronPackage = JSON.parse(fs.readFileSync(
  path.join(repoRoot, 'electron_app', 'package.json'),
  'utf8'
));
const afterPackSource = fs.readFileSync(
  path.join(repoRoot, 'electron_app', 'scripts', 'after-pack-local-app-manifest.js'),
  'utf8'
);

assert(
  backendSource.includes("spawn('cmd.exe', ['/d', '/c', launcherPath],"),
  'backend launcher must pass the .cmd path as a raw cmd.exe argument'
);
assert(
  !backendSource.includes('`"${launcherPath}"`'),
  'backend launcher must not wrap launcherPath with nested quotes'
);

assert(
  localPathsSource.includes('const JK_LEGACY_WHATSAPP_VOICE_PORT = 8012;'),
  'Electron must retain the retired voice port in the managed-server cleanup set'
);
assert(
  backendSource.includes('function stopManagedLocalServers(reason = \'unspecified\')')
    && backendSource.includes('JK_LOCAL_BACKEND_PORT,')
    && backendSource.includes('JK_PROMO_WORKER_PORT,')
    && backendSource.includes('JK_LEGACY_WHATSAPP_VOICE_PORT'),
  'Electron must close backend, promo worker and legacy voice listeners as one managed set'
);
const backendStartupSource = backendSource.slice(
  backendSource.indexOf('function ensureLocalBackendStarted()'),
  backendSource.indexOf('function stopTrackedProcessTree(')
);
const trustedLauncherCheck = backendStartupSource.indexOf('consumeCanonicalLauncherPreparedServers()');
const mandatoryCleanup = backendStartupSource.indexOf("stopManagedLocalServers('before-start')");
const backendSync = backendStartupSource.indexOf('syncBundledLocalBackend(inspection)');
assert(
  trustedLauncherCheck >= 0
    && mandatoryCleanup > trustedLauncherCheck
    && backendSync > mandatoryCleanup,
  'direct Electron startup must close and confirm old managed servers before syncing or starting the backend'
);
assert(
  backendStartupSource.includes('preparedByCanonicalLauncher: true')
    && backendStartupSource.includes('if (!cleanup.success)')
    && backendStartupSource.includes('filter(server => !server.closed)'),
  'only the canonical pre-cleaned launcher may reuse its fresh backend and cleanup failures must block startup'
);
assert(
  backendSource.includes('function consumeCanonicalLauncherPreparedServers()')
    && backendSource.includes('delete process.env.JK_LOCAL_SERVERS_PREPARED_BY_LAUNCHER;'),
  'canonical launcher trust must be consumed once and never authorize a later stale-server reuse'
);

assert(
  localPathsSource.includes('path.join(process.resourcesPath, JK_LOCAL_BACKEND_DIR_NAME)'),
  'packaged Electron root must consider resources/local_app'
);
const packagedRootCandidate = localPathsSource.indexOf('packagedRoot,');
const envRootCandidate = localPathsSource.indexOf('envRoot,');
assert(
  packagedRootCandidate >= 0 && envRootCandidate >= 0 && packagedRootCandidate < envRootCandidate,
  'packaged Electron root must prefer resources/local_app before JK_APP_ROOT_DIR'
);

assert(
  backendSource.includes('function getPackagedLocalBackendSourceDir()'),
  'backend sync must have an explicit packaged local_app source'
);
assert(
  !backendSource.includes('function copyDirectoryRecursive(')
    && backendSource.includes('inspectBundledLocalBackendMaterialization()')
    && backendSource.includes('finalizeMaterialization(pendingMaterialization)')
    && backendSource.includes('rollbackMaterialization(pendingMaterialization)'),
  'backend sync must use the manifest transaction and health-gated finalize/rollback path'
);
assert(
  materializerSource.includes("const JOURNAL_RELATIVE_PATH = path.join('info', 'runtime-materialization-journal.json')")
    && materializerSource.includes('fs.openSync(lockPath, \'wx\')')
    && materializerSource.includes('WHISPER_MODEL_MANIFEST')
    && materializerSource.includes('inspectImmutableRuntimePayloads')
    && materializerSource.includes('ensureImmutableRuntimePayloads'),
  'runtime materializer must keep application and immutable Whisper payload transactions explicit'
);
assert.strictEqual(
  electronPackage.build.afterPack,
  'scripts/after-pack-local-app-manifest.js',
  'electron-builder must generate local-app-manifest from the final packaged tree'
);
assert(
  afterPackSource.includes('sanitizeLocalAppStaging(localAppDir)')
    && afterPackSource.includes("[verifier, '--packaged', resourcesDir]")
    && afterPackSource.includes("PYTHONDONTWRITEBYTECODE: '1'"),
  'afterPack must sanitize only staging and run the packaged gate before artifact promotion'
);
assert(
  backendSource.includes('await stopTrackedProcessTree(child.pid)'),
  'Python runtime timeout must terminate the complete provisioner process tree'
);
assert(
  backendSource.includes("'--command-timeout', '1500'") && backendSource.includes('40 * 60 * 1000'),
  'Electron timeout must remain longer than each provisioner command timeout'
);
assert(
  backendSource.includes("'--quick-reuse'") && backendSource.includes('function ensurePythonRuntimeProvisioned'),
  'Electron backend bootstrap must request the safe quick reuse path'
);
assert(
  backendSource.includes('function isolatedPythonChildEnv(overrides = {})')
    && backendSource.includes("normalized === 'PYTHONHOME'")
    && backendSource.includes("normalized === 'PYTHONPATH'")
    && backendSource.includes("normalized === 'PYTHONUSERBASE'")
    && backendSource.includes("normalized === 'VIRTUAL_ENV'")
    && backendSource.includes("normalized.startsWith('PIP_')")
    && backendSource.includes("PYTHONNOUSERSITE: '1'")
    && backendSource.includes("PIP_CONFIG_FILE: 'NUL'"),
  'Python child processes must not inherit ambient Python or pip configuration'
);
assert(
  backendSource.includes("'set \"PYTHONHOME=\"'")
    && backendSource.includes("'set \"PYTHONPATH=\"'")
    && backendSource.includes("'set \"PYTHONUSERBASE=\"'")
    && backendSource.includes("'set \"PYTHONNOUSERSITE=1\"'")
    && backendSource.includes("-B -I -m uvicorn --app-dir \"%CD%\""),
  'generated backend launcher must remain isolated when invoked manually'
);
assert(
  backendSource.includes("fs.rmSync(readyMarker, { force: true })")
    && backendSource.includes("invalidateRuntimeMarker('backend_exited_before_ready'")
    && backendSource.includes("invalidateRuntimeMarker('backend_not_ready'")
    && backendSource.includes('await stopTrackedProcessTree(child.pid)'),
  'a backend crash or timeout before readiness must invalidate the marker and stop the process tree'
);
assert(
  backendSource.includes('sameResolvedPath(resolved, getLocalBackendRuntimeDir())'),
  'backend sync must ignore JK_LOCAL_BACKEND_SOURCE_DIR when it points at the runtime dir'
);
const packagedSourceCandidate = backendSource.indexOf('getPackagedLocalBackendSourceDir(),');
const envSourceCandidate = backendSource.indexOf('getEnvLocalBackendSourceDir(),');
assert(
  packagedSourceCandidate >= 0 && envSourceCandidate >= 0 && packagedSourceCandidate < envSourceCandidate,
  'backend sync must prefer packaged resources before the env source in packaged builds'
);

console.log('electron backend bootstrap regression checks passed');
