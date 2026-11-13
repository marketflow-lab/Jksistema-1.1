'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const windowSource = fs.readFileSync(path.join(root, 'electron_app/main/modules/window.js'), 'utf8');
const appPathsSource = fs.readFileSync(path.join(root, 'electron_app/main/modules/local-app-paths.js'), 'utf8');
const verifySource = fs.readFileSync(path.join(root, 'electron_app/scripts/verify-update-package.js'), 'utf8');
const extensionId = 'jdefnfmbnchmnjkcknaadaddgjbgephh';

function declaredFunction(source, name) {
  const marker = `function ${name}(`;
  const start = source.indexOf(marker);
  assert(start >= 0, `function ${name} missing`);
  const prefix = source.slice(start - 6, start) === 'async ' ? 'async ' : '';
  return prefix + source.slice(start, source.indexOf('\n}', start) + 2);
}

function putExtension(directory, version = '7.33.5') {
  fs.mkdirSync(directory, { recursive: true });
  fs.writeFileSync(path.join(directory, 'manifest.json'), JSON.stringify({
    name: 'Avantpro ML', version, manifest_version: 3, key: 'fixture-avant-key',
    background: { service_worker: 'background.js' }
  }));
  fs.writeFileSync(path.join(directory, 'background.js'), '// fixture background');
  fs.writeFileSync(path.join(directory, 'index.js'), '// fixture content');
}

function browserHarness(directory, options = {}) {
  const resources = path.join(directory, 'resources');
  const localApp = path.join(resources, 'local_app');
  const userData = path.join(directory, 'profile');
  fs.mkdirSync(localApp, { recursive: true });
  fs.writeFileSync(path.join(localApp, 'electron_shell.html'), '<!doctype html>');
  fs.writeFileSync(path.join(localApp, 'backend_api.py'), '# fixture backend');
  const loaded = [];
  const context = vm.createContext({
    fs, path,
    console: { log() {}, error() {} },
    process: { env: { ...options.env }, resourcesPath: options.resourcesPath === false ? undefined : resources },
    __dirname: localApp,
    app: { isPackaged: options.packaged !== false, getPath: () => userData },
    JK_LOCAL_BACKEND_DIR_NAME: 'local_app',
    AVANTPRO_CHROME_EXTENSION_ID: extensionId,
    getLocalBackendRuntimeDir: () => path.join(userData, 'local_app'),
    getAppRootDir: () => context.resolveAppRootDir(),
    getChromeUserDataDir: () => path.join(directory, 'chrome-not-installed'),
    isAvantProExtensionEnabled: () => options.enabled !== false,
    logElectronLifecycle() {},
    registerChromeExtensionSessionEvents() {},
    getMlSession: () => ({ async loadExtension(extensionDir, settings) {
      loaded.push({ extensionDir, settings });
      const manifest = JSON.parse(fs.readFileSync(path.join(extensionDir, 'manifest.json'), 'utf8'));
      return { id: extensionId, name: manifest.name, version: manifest.version };
    } })
  });
  const functions = [
    declaredFunction(appPathsSource, 'resolveAppRootDir'),
    ...['getChromeExtensionsRoots', 'compareVersionStrings', 'findInstalledChromeExtensionVersions',
      'getChromeExtensionManifestIdentity', 'readChromeExtensionManifest', 'isAvantProExtensionCandidate',
      'findUnpackedChromeExtensions', 'loadChromeExtensionsForMlSession'].map(name => declaredFunction(windowSource, name))
  ];
  vm.runInContext(functions.join('\n'), context);
  return { context, resources, localApp, loaded };
}

async function packagedExtensionWithoutChrome(directory) {
  const h = browserHarness(directory);
  const extensionDir = path.join(h.resources, 'extensoes_chrome', 'avant_pro');
  putExtension(extensionDir);
  assert.equal(h.context.resolveAppRootDir(), h.localApp, 'the installed main process starts inside resources/local_app');
  assert(!fs.existsSync(path.join(h.localApp, 'extensoes_chrome')), 'the extension remains outside local_app');
  assert.equal(h.context.findInstalledChromeExtensionVersions(extensionId).length, 0, 'the clean machine has no Chrome extension');
  assert.deepEqual(Array.from(h.context.findUnpackedChromeExtensions()), [extensionDir], 'the separate packaged resource is discoverable');
  const extensions = await h.context.loadChromeExtensionsForMlSession();
  assert.equal(extensions.length, 1);
  assert.equal(extensions[0].version, '7.33.5');
  assert.equal(h.loaded[0].extensionDir, extensionDir, 'the packaged directory reaches the Electron session loader');
  assert.equal(h.loaded[0].settings.allowFileAccess, true);
  const runtimeExtension = path.join(h.context.getLocalBackendRuntimeDir(), 'extensoes_chrome', 'avant_pro');
  putExtension(runtimeExtension, '7.0.3');
  assert.equal(h.context.findUnpackedChromeExtensions()[0], extensionDir, 'a legacy profile copy cannot shadow the packaged version');
  const chromeExtension = path.join(h.context.getChromeUserDataDir(), 'Default', 'Extensions', extensionId, '7.99.0_0');
  putExtension(chromeExtension, '7.99.0');
  assert.deepEqual(Array.from(h.context.findUnpackedChromeExtensions()), [chromeExtension], 'a Chrome installation retains its existing preference over the bundled fallback');
}

function explicitAndDevelopmentRoots(directory) {
  const explicit = path.join(directory, 'custom', 'extensoes_chrome');
  const h = browserHarness(directory, { packaged: false, env: { JK_CHROME_EXTENSIONS_DIR: explicit } });
  const extensionDir = path.join(explicit, 'avant_pro');
  putExtension(extensionDir);
  const resourcesExtension = path.join(h.resources, 'extensoes_chrome', 'avant_pro');
  putExtension(resourcesExtension);
  const roots = Array.from(h.context.getChromeExtensionsRoots());
  assert.equal(roots[0], explicit, 'explicit development roots retain precedence');
  assert(!roots.includes(path.dirname(resourcesExtension)), 'development mode must not load an unrelated Electron resources directory');
  assert(roots.includes(path.join(h.localApp, 'extensoes_chrome')), 'existing source roots are preserved');
  assert.deepEqual(Array.from(h.context.findUnpackedChromeExtensions()), [extensionDir]);
  const disabled = browserHarness(path.join(directory, 'disabled'), { enabled: false });
  putExtension(path.join(disabled.resources, 'extensoes_chrome', 'avant_pro'));
  assert.equal(disabled.context.findUnpackedChromeExtensions().length, 0, 'the user setting still disables Avant Pro');
  const missingResources = browserHarness(path.join(directory, 'missing-resources'), { resourcesPath: false });
  assert.doesNotThrow(() => missingResources.context.getChromeExtensionsRoots(), 'a missing resourcesPath remains supported');
}

function updateRejectsMissingOrStaleExtension(directory) {
  const sourceRoot = path.join(directory, 'source');
  const resourcesRoot = path.join(directory, 'packaged');
  const sourceExtension = path.join(sourceRoot, 'extensoes_chrome', 'avant_pro');
  const packagedExtension = path.join(resourcesRoot, 'extensoes_chrome', 'avant_pro');
  putExtension(sourceExtension);
  const context = vm.createContext({ fs, path, repoRoot: sourceRoot });
  vm.runInContext(['fileExists', 'directoryExists', 'filesEqual', 'toPosix', 'walk', 'validateBundledAvantProAtRoot']
    .map(name => declaredFunction(verifySource, name)).join('\n'), context);
  const failures = [];
  context.validateBundledAvantProAtRoot(resourcesRoot, failures);
  assert(failures.some(failure => failure.includes('manifest.json')), 'the update gate rejects an absent extension directory');
  putExtension(packagedExtension);
  failures.length = 0;
  context.validateBundledAvantProAtRoot(resourcesRoot, failures);
  assert.deepEqual(failures, [], 'the packaged extension matches the approved source');
  fs.unlinkSync(path.join(packagedExtension, 'background.js'));
  context.validateBundledAvantProAtRoot(resourcesRoot, failures);
  assert(failures.some(failure => failure.includes('background.js')), 'the update gate rejects an incomplete extension');
  putExtension(packagedExtension, '7.0.3');
  failures.length = 0;
  context.validateBundledAvantProAtRoot(resourcesRoot, failures);
  assert(failures.some(failure => failure.includes('manifest.json')), 'the update gate rejects a stale packaged version');
}

(async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'jk-packaged-avant-'));
  try {
    await packagedExtensionWithoutChrome(path.join(directory, 'clean-install'));
    explicitAndDevelopmentRoots(path.join(directory, 'development'));
    updateRejectsMissingOrStaleExtension(path.join(directory, 'package-gate'));
  } finally {
    assert(path.dirname(path.resolve(directory)) === path.resolve(os.tmpdir())
      && path.basename(directory).startsWith('jk-packaged-avant-'), 'cleanup remains inside the named temporary fixture');
    fs.rmSync(directory, { recursive: true, force: true });
  }
  console.log('Packaged Avant Pro: clean install, separate resources, dev roots and update asset parity OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
