const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const repoRoot = path.resolve(__dirname, '..');
const backendSource = fs.readFileSync(
  path.join(repoRoot, 'electron_app', 'main', 'modules', 'backend.js'),
  'utf8'
);
const helperStart = backendSource.indexOf('function consumeCanonicalLauncherPreparedServers()');
const helperEnd = backendSource.indexOf('function cmdValue(', helperStart);
assert.ok(helperStart >= 0 && helperEnd > helperStart, 'managed-server helpers must remain extractable');
const helperSource = backendSource.slice(helperStart, helperEnd);

function createSandbox(options = {}) {
  const calls = { probes: [], stops: [], closes: [], logs: [] };
  const openPorts = new Set(options.openPorts || []);
  const blockedPorts = new Set(options.blockedPorts || []);
  const sandbox = {
    process: { env: { ...(options.env || {}) } },
    JK_LOCAL_BACKEND_PORT: 8001,
    JK_PROMO_WORKER_PORT: 8011,
    JK_LEGACY_WHATSAPP_VOICE_PORT: 8012,
    isTcpPortOpen: async (port) => {
      calls.probes.push(port);
      return openPorts.has(port);
    },
    stopProcessListeningOnPort: async (port) => {
      calls.stops.push(port);
      return true;
    },
    waitForTcpPortClosed: async (port) => {
      calls.closes.push(port);
      return !blockedPorts.has(port);
    },
    logElectronLifecycle: (event, payload) => calls.logs.push({ event, payload }),
  };
  vm.createContext(sandbox);
  vm.runInContext(
    `${helperSource}; this.api = { consumeCanonicalLauncherPreparedServers, managedLocalServerPorts, stopManagedLocalServers };`,
    sandbox
  );
  return { sandbox, calls };
}

(async () => {
  const trusted = createSandbox({
    env: { JK_LOCAL_SERVERS_PREPARED_BY_LAUNCHER: '1' },
  });
  assert.strictEqual(trusted.sandbox.api.consumeCanonicalLauncherPreparedServers(), true);
  assert.strictEqual(
    trusted.sandbox.process.env.JK_LOCAL_SERVERS_PREPARED_BY_LAUNCHER,
    undefined,
    'launcher trust must be removed after the first read'
  );
  assert.strictEqual(
    trusted.sandbox.api.consumeCanonicalLauncherPreparedServers(),
    false,
    'launcher trust must not authorize a second backend reuse'
  );

  const clean = createSandbox({ openPorts: [8001, 8012] });
  const cleanResult = await clean.sandbox.api.stopManagedLocalServers('before-start');
  assert.strictEqual(cleanResult.success, true);
  assert.deepStrictEqual(Array.from(clean.sandbox.api.managedLocalServerPorts()), [8001, 8011, 8012]);
  assert.deepStrictEqual(clean.calls.probes, [8001, 8011, 8012]);
  assert.deepStrictEqual(clean.calls.stops, [8001, 8011, 8012]);
  assert.deepStrictEqual(clean.calls.closes, [8001, 8011, 8012]);
  assert.strictEqual(clean.calls.logs.at(-1).event, 'local-managed-servers-stopped');

  const blocked = createSandbox({ blockedPorts: [8011] });
  const blockedResult = await blocked.sandbox.api.stopManagedLocalServers('before-start');
  assert.strictEqual(blockedResult.success, false);
  assert.strictEqual(blockedResult.servers.find(server => server.port === 8011).closed, false);
  assert.strictEqual(blocked.calls.logs.at(-1).event, 'local-managed-servers-stop-incomplete');

  console.log('electron managed servers startup: OK');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
