const assert = require('assert');
const { spawnSync } = require('child_process');
const { EventEmitter } = require('events');
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

const processStopStart = backendSource.indexOf('function managedServerRuntimeRoots()');
const processStopEnd = backendSource.indexOf('function consumeCanonicalLauncherPreparedServers()', processStopStart);
assert.ok(processStopStart >= 0 && processStopEnd > processStopStart, 'safe process-stop helpers must remain extractable');
const processStopSource = backendSource.slice(processStopStart, processStopEnd);

assert.ok(processStopSource.includes('Get-CimInstance Win32_Process'), 'listener ownership must be inspected before termination');
assert.ok(processStopSource.includes('function Test-JkManagedProcess'), 'JK server ownership allowlist must be explicit');
assert.ok(processStopSource.includes("@('python.exe', 'pythonw.exe')"), 'only the Python server executables may be managed');
assert.ok(!processStopSource.includes("'node.exe'"), 'generic Node processes must never be treated as JK servers');
assert.ok(processStopSource.includes('foreach ($ownedPid in $ownedPids)'), 'only allowlisted owned PIDs may reach termination');
assert.ok(processStopSource.includes('Test-JkManagedProcess -Process $currentProcess'), 'ownership must be revalidated immediately before termination');
assert.strictEqual((processStopSource.match(/Invoke-CimMethod -InputObject \$currentProcess -MethodName Terminate/g) || []).length, 1, 'there must be one guarded process-termination site');
assert.ok(!processStopSource.includes('Stop-Process'), 'raw PID termination must not be used');

function createSandbox(options = {}) {
  const calls = { probes: [], stops: [], closes: [], logs: [] };
  const openPorts = new Set(options.openPorts || []);
  const blockedPorts = new Set(options.blockedPorts || []);
  const stopResults = options.stopResults || {};
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
      return {
        ok: true,
        listenerProcessCount: 0,
        ownedProcessCount: 0,
        stopRequestedCount: 0,
        foreignProcessCount: 0,
        stopErrorCount: 0,
        ...(stopResults[port] || {}),
      };
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

async function invokeSafePortStop(payload, options = {}) {
  const calls = [];
  const sandbox = {
    path,
    process: { platform: 'win32', env: { KEEP_ME: 'yes' } },
    JK_LOCAL_BACKEND_PORT: 8001,
    JK_PROMO_WORKER_PORT: 8011,
    JK_LEGACY_WHATSAPP_VOICE_PORT: 8012,
    getLocalBackendRuntimeDir: () => 'C:\\Users\\Test\\AppData\\Roaming\\JK Sistema Cliente\\local_app',
    getBundledLocalBackendSourceDir: () => 'C:\\Program Files\\JK Sistema Cliente\\resources\\local_app',
    spawn: (executable, args, spawnOptions) => {
      calls.push({ executable, args, spawnOptions });
      const child = new EventEmitter();
      child.stdout = new EventEmitter();
      child.stdout.setEncoding = () => {};
      queueMicrotask(() => {
        child.stdout.emit('data', JSON.stringify(payload));
        child.emit('exit', options.exitCode || 0);
      });
      return child;
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(`${processStopSource}; this.api = { stopProcessListeningOnPort };`, sandbox);
  const result = await sandbox.api.stopProcessListeningOnPort(options.port || 8011);
  return { result, calls };
}

function runSyntheticPowerShellOwnership(port, processInfo) {
  const sandbox = {
    path,
    process,
    JK_LOCAL_BACKEND_PORT: 8001,
    JK_PROMO_WORKER_PORT: 8011,
    JK_LEGACY_WHATSAPP_VOICE_PORT: 8012,
    getLocalBackendRuntimeDir: () => '',
    getBundledLocalBackendSourceDir: () => '',
  };
  vm.createContext(sandbox);
  vm.runInContext(`${processStopSource}; this.api = { managedServerStopPowerShell };`, sandbox);
  const psLiteral = (value) => `'${String(value).replace(/'/g, "''")}'`;
  const processObject = `[pscustomobject]@{ Name = ${psLiteral(processInfo.name)}; ExecutablePath = ${psLiteral(processInfo.executablePath)}; CommandLine = ${psLiteral(processInfo.commandLine)} }`;
  const script = sandbox.api.managedServerStopPowerShell(port)
    .replace(/  \$listenerPids = @\(Get-NetTCPConnection[^\n]+\)/, '  $listenerPids = @(424242)')
    .replace(/    \$listenerProcess = Get-CimInstance[^\n]+/, `    $listenerProcess = ${processObject}`)
    .replace(/      \$currentProcess = Get-CimInstance[^\n]+/, `      $currentProcess = ${processObject}`)
    .replace(/      \$termination = Invoke-CimMethod[^\n]+/, '      $termination = [pscustomobject]@{ ReturnValue = 0 }');
  const runtimeRoot = String.raw`C:\JK Runtime Test`;
  const result = spawnSync(
    'powershell.exe',
    ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', script],
    {
      encoding: 'utf8',
      env: {
        ...process.env,
        JK_MANAGED_SERVER_ROOTS_JSON: JSON.stringify([runtimeRoot, String.raw`C:\JK Bundle Test`]),
      },
    }
  );
  assert.strictEqual(result.status, 0, result.stderr || 'synthetic PowerShell classifier failed');
  return JSON.parse(String(result.stdout || '').trim());
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

  const clean = createSandbox({
    openPorts: [8001, 8012],
    stopResults: {
      8001: { listenerProcessCount: 1, ownedProcessCount: 1, stopRequestedCount: 1 },
      8012: { listenerProcessCount: 1, ownedProcessCount: 1, stopRequestedCount: 1 },
    },
  });
  const cleanResult = await clean.sandbox.api.stopManagedLocalServers('before-start');
  assert.strictEqual(cleanResult.success, true);
  assert.deepStrictEqual(Array.from(clean.sandbox.api.managedLocalServerPorts()), [8001, 8011, 8012]);
  assert.deepStrictEqual(clean.calls.probes, [8001, 8011, 8012]);
  assert.deepStrictEqual(clean.calls.stops, [8001, 8011, 8012]);
  assert.deepStrictEqual(clean.calls.closes, [8001, 8012]);
  assert.strictEqual(clean.calls.logs.at(-1).event, 'local-managed-servers-stopped');

  const foreignOptional = createSandbox({
    openPorts: [8011, 8012],
    stopResults: {
      8011: { listenerProcessCount: 1, foreignProcessCount: 1 },
      8012: { listenerProcessCount: 1, foreignProcessCount: 1 },
    },
  });
  const foreignOptionalResult = await foreignOptional.sandbox.api.stopManagedLocalServers('before-start');
  assert.strictEqual(foreignOptionalResult.success, true, 'foreign optional listeners must not block backend 8001');
  assert.deepStrictEqual(foreignOptional.calls.closes, [], 'foreign optional listeners must not receive a termination wait');
  for (const port of [8011, 8012]) {
    const server = foreignOptionalResult.servers.find(item => item.port === port);
    assert.strictEqual(server.closed, false);
    assert.strictEqual(server.foreignProcessCount, 1);
    assert.strictEqual(server.stopRequestedCount, 0);
    assert.strictEqual(server.blocking, false);
  }

  const foreignBackend = createSandbox({
    openPorts: [8001],
    stopResults: { 8001: { listenerProcessCount: 1, foreignProcessCount: 1 } },
  });
  const foreignBackendResult = await foreignBackend.sandbox.api.stopManagedLocalServers('before-start');
  assert.strictEqual(foreignBackendResult.success, false, 'foreign backend listener must fail startup without termination');
  const foreignBackendServer = foreignBackendResult.servers.find(server => server.port === 8001);
  assert.strictEqual(foreignBackendServer.stopRequestedCount, 0);
  assert.strictEqual(foreignBackendServer.blocking, true);

  const blocked = createSandbox({
    openPorts: [8011],
    blockedPorts: [8011],
    stopResults: {
      8011: { listenerProcessCount: 1, ownedProcessCount: 1, stopRequestedCount: 1, stopErrorCount: 1 },
    },
  });
  const blockedResult = await blocked.sandbox.api.stopManagedLocalServers('before-start');
  assert.strictEqual(blockedResult.success, false);
  assert.strictEqual(blockedResult.servers.find(server => server.port === 8011).closed, false);
  assert.strictEqual(blocked.calls.logs.at(-1).event, 'local-managed-servers-stop-incomplete');

  const safeForeignStop = await invokeSafePortStop({
    ok: true,
    listenerProcessCount: 1,
    ownedProcessCount: 0,
    stopRequestedCount: 0,
    foreignProcessCount: 1,
    stopErrorCount: 0,
  });
  assert.strictEqual(safeForeignStop.result.ok, true);
  assert.strictEqual(safeForeignStop.result.foreignProcessCount, 1);
  assert.strictEqual(safeForeignStop.result.stopRequestedCount, 0);
  assert.strictEqual(safeForeignStop.calls.length, 1);
  assert.deepStrictEqual(Array.from(safeForeignStop.calls[0].args.slice(0, 4)), ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command']);
  assert.ok(safeForeignStop.calls[0].spawnOptions.env.JK_MANAGED_SERVER_ROOTS_JSON.includes('JK Sistema Cliente'));
  assert.strictEqual(safeForeignStop.calls[0].spawnOptions.env.KEEP_ME, 'yes');

  if (process.platform === 'win32') {
    const runtimeRoot = String.raw`C:\JK Runtime Test`;
    const slash = path.win32.sep;
    const foreignExecutable = runSyntheticPowerShellOwnership(8011, {
      name: 'Weixin.exe',
      executablePath: String.raw`C:\Program Files\Tencent\Weixin.exe`,
      commandLine: 'Weixin.exe promo_worker_api:app --port 8011',
    });
    const foreignPython = runSyntheticPowerShellOwnership(8011, {
      name: 'python.exe',
      executablePath: String.raw`C:\Tools\python.exe`,
      commandLine: 'python -m uvicorn promo_worker_api:app --port 8011',
    });
    const missingMarker = runSyntheticPowerShellOwnership(8001, {
      name: 'python.exe',
      executablePath: `${runtimeRoot}${slash}python.exe`,
      commandLine: 'python unrelated.py --port 8001',
    });
    const ownedBackend = runSyntheticPowerShellOwnership(8001, {
      name: 'python.exe',
      executablePath: `${runtimeRoot}${slash}python_runtime${slash}python.exe`,
      commandLine: `python -m uvicorn --app-dir "${runtimeRoot}" backend_api:app --port 8001`,
    });
    const ownedPromo = runSyntheticPowerShellOwnership(8011, {
      name: 'pythonw.exe',
      executablePath: `${runtimeRoot}${slash}python_runtime${slash}pythonw.exe`,
      commandLine: `pythonw -m uvicorn --app-dir "${runtimeRoot}" promo_worker_api:app --port 8011`,
    });
    const ownedVoice = runSyntheticPowerShellOwnership(8012, {
      name: 'python.exe',
      executablePath: `${runtimeRoot}${slash}python_runtime${slash}python.exe`,
      commandLine: `python "${runtimeRoot}${slash}whatsapp_voice_server.py" --port 8012`,
    });
    for (const foreign of [foreignExecutable, foreignPython, missingMarker]) {
      assert.strictEqual(foreign.foreignProcessCount, 1);
      assert.strictEqual(foreign.stopRequestedCount, 0);
    }
    for (const owned of [ownedBackend, ownedPromo, ownedVoice]) {
      assert.strictEqual(owned.ownedProcessCount, 1);
      assert.strictEqual(owned.stopRequestedCount, 1);
      assert.strictEqual(owned.foreignProcessCount, 0);
    }
  }

  console.log('electron managed servers startup: OK');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
