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

assert(
  backendSource.includes("spawn('cmd.exe', ['/d', '/c', launcherPath],"),
  'backend launcher must pass the .cmd path as a raw cmd.exe argument'
);
assert(
  !backendSource.includes('`"${launcherPath}"`'),
  'backend launcher must not wrap launcherPath with nested quotes'
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
