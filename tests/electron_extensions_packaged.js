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

function putExtension(directory, version = '7.33.5', extraManifest = {}) {
  fs.mkdirSync(directory, { recursive: true });
  fs.writeFileSync(path.join(directory, 'manifest.json'), JSON.stringify({
    name: 'Avantpro ML', version, manifest_version: 3, key: 'fixture-avant-key',
    background: { service_worker: 'background.js' }, ...extraManifest
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
      'isAvantProExtensionCompatibleWithElectron',
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

async function chromeSidePanelFallsBackToCompatibleLogin(directory) {
  const h = browserHarness(directory);
  const bundled = path.join(h.resources, 'extensoes_chrome', 'avant_pro');
  const chromeRoot = path.join(h.context.getChromeUserDataDir(), 'Default', 'Extensions', extensionId);
  const modern = path.join(chromeRoot, '7.38.5_0');
  putExtension(bundled);
  putExtension(modern, '7.38.5', {
    permissions: ['storage', 'sidePanel'], side_panel: { default_path: 'login-panel.html' }
  });
  const modernManifest = fs.readFileSync(path.join(modern, 'manifest.json'), 'utf8');
  assert.deepEqual(Array.from(h.context.findUnpackedChromeExtensions()), [bundled],
    'the Chrome side-panel login must not shadow the compatible packaged login');
  await h.context.loadChromeExtensionsForMlSession();
  assert.equal(h.loaded[0].extensionDir, bundled, 'Electron loads the compatible login instead of the unsupported panel');
  assert.equal(fs.readFileSync(path.join(modern, 'manifest.json'), 'utf8'), modernManifest,
    'compatibility selection must preserve the Chrome extension');

  const compatibleChrome = path.join(chromeRoot, '7.33.6_0');
  putExtension(compatibleChrome, '7.33.6');
  assert.deepEqual(Array.from(h.context.findUnpackedChromeExtensions()), [compatibleChrome],
    'a compatible Chrome version keeps its existing precedence');
  putExtension(compatibleChrome, '7.38.5', { permissions: ['sidePanel'] });
  putExtension(modern, '7.38.5', { side_panel: { default_path: 'login-panel.html' } });
  assert.equal(h.context.findUnpackedChromeExtensions()[0], bundled, 'the panel manifest alone requires fallback');
  putExtension(modern, '7.38.5', { permissions: ['sidePanel'] });
  assert.equal(h.context.findUnpackedChromeExtensions()[0], bundled, 'the panel permission alone requires fallback');

  putExtension(bundled, '7.38.5', { side_panel: { default_path: 'login-panel.html' } });
  assert.equal(h.context.findUnpackedChromeExtensions().length, 0, 'an unsupported bundled panel cannot be loaded either');
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
  const compatibleFallback = path.join(h.localApp, 'extensoes_chrome', 'avant_pro');
  putExtension(compatibleFallback);
  putExtension(extensionDir, '7.38.5', { permissions: ['sidePanel'] });
  assert.deepEqual(Array.from(h.context.findUnpackedChromeExtensions()), [compatibleFallback],
    'an incompatible explicit root must fall back to a compatible source');
  h.context.process.env.JK_FORCE_CHROME_EXTENSIONS_DIR = '1';
  assert.deepEqual(Array.from(h.context.findUnpackedChromeExtensions()), [compatibleFallback],
    'forcing a root cannot enable an unsupported API');
  const disabled = browserHarness(path.join(directory, 'disabled'), { enabled: false });
  putExtension(path.join(disabled.resources, 'extensoes_chrome', 'avant_pro'));
  assert.equal(disabled.context.findUnpackedChromeExtensions().length, 0, 'the user setting still disables Avant Pro');
  const missingResources = browserHarness(path.join(directory, 'missing-resources'), { resourcesPath: false });
  assert.doesNotThrow(() => missingResources.context.getChromeExtensionsRoots(), 'a missing resourcesPath remains supported');
}

async function developmentPrefersNewerCompatibleBundle(directory) {
  const h = browserHarness(directory, { packaged: false });
  const bundled = path.join(h.localApp, 'extensoes_chrome', 'avant_pro');
  const runtime = path.join(h.context.getLocalBackendRuntimeDir(), 'extensoes_chrome', 'avant_pro');
  const chromeRoot = path.join(h.context.getChromeUserDataDir(), 'Default', 'Extensions', extensionId);
  const oldChrome = path.join(chromeRoot, '7.16.0_0');
  const sidePanel = path.join(chromeRoot, '7.39.0_0');
  const otherProfile = path.join(h.context.getChromeUserDataDir(), 'Profile 1', 'Extensions', extensionId, '7.0.3_0');
  putExtension(bundled, '7.33.5');
  putExtension(runtime, '7.0.3');
  putExtension(oldChrome, '7.16.0');
  putExtension(otherProfile, '7.0.3');
  putExtension(sidePanel, '7.39.0', { permissions: ['sidePanel'] });
  h.context.process.env.JK_CHROME_EXTENSIONS_DIR = runtime;
  assert.deepEqual(Array.from(h.context.findUnpackedChromeExtensions()), [bundled],
    'older compatible Chrome copies cannot shadow the newer development bundle');
  assert.equal((await h.context.loadChromeExtensionsForMlSession())[0].version, '7.33.5');
  assert.equal(h.loaded[0].extensionDir, bundled, 'the development bundle reaches the existing session loader');

  h.context.process.env.JK_FORCE_CHROME_EXTENSIONS_DIR = '1';
  assert.equal(h.context.findUnpackedChromeExtensions()[0], runtime, 'an explicitly forced path keeps precedence');
  delete h.context.process.env.JK_FORCE_CHROME_EXTENSIONS_DIR;
  h.context.process.env.JK_PREFER_INSTALLED_CHROME_EXTENSIONS = '1';
  assert.equal(h.context.findUnpackedChromeExtensions()[0], oldChrome);
  delete h.context.process.env.JK_PREFER_INSTALLED_CHROME_EXTENSIONS;
  h.context.process.env.JK_AVANTPRO_CHROME_PROFILE = 'Profile 1';
  assert.equal(h.context.findUnpackedChromeExtensions()[0], otherProfile);
  delete h.context.process.env.JK_AVANTPRO_CHROME_PROFILE;

  const latestCompatible = path.join(chromeRoot, '7.33.6_0');
  putExtension(latestCompatible, '7.33.6');
  assert.equal(h.context.findUnpackedChromeExtensions()[0], latestCompatible,
    'a newer compatible installed version keeps precedence');
  putExtension(latestCompatible, '7.33.5');
  assert.equal(h.context.findUnpackedChromeExtensions()[0], latestCompatible,
    'a compatible installed version of the same age keeps precedence');
  putExtension(latestCompatible, '7.33.6', { permissions: ['sidePanel'] });
  putExtension(bundled, '7.33.5', { permissions: ['sidePanel'] });
  assert.equal(h.context.findUnpackedChromeExtensions()[0], oldChrome,
    'development never loads an incompatible bundle');
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
    await chromeSidePanelFallsBackToCompatibleLogin(path.join(directory, 'side-panel-login'));
    explicitAndDevelopmentRoots(path.join(directory, 'development'));
    await developmentPrefersNewerCompatibleBundle(path.join(directory, 'development-newer-bundle'));
    updateRejectsMissingOrStaleExtension(path.join(directory, 'package-gate'));
  } finally {
    assert(path.dirname(path.resolve(directory)) === path.resolve(os.tmpdir())
      && path.basename(directory).startsWith('jk-packaged-avant-'), 'cleanup remains inside the named temporary fixture');
    fs.rmSync(directory, { recursive: true, force: true });
  }
  console.log('Packaged Avant Pro: compatible login fallback, clean install, dev roots and update asset parity OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
