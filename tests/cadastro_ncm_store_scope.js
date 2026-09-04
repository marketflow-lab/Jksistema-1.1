const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'ncm-sync.js'), 'utf8');

function createRuntime() {
  const values = new Map([
    ['user_data', JSON.stringify({ client_id: '000002' })],
  ]);
  const localStorage = {
    getItem: key => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: key => values.delete(key),
  };
  const calls = [];
  const window = { addEventListener() {} };
  const context = {
    window,
    localStorage,
    URLSearchParams,
    console,
    setInterval: () => 1,
    clearInterval() {},
    setTimeout: callback => callback(),
    fetch: async (url, options) => {
      calls.push({ url, options });
      return { ok: true, json: async () => ({ job_id: 'job-1', status: 'running' }) };
    },
    obterAuthHeaders: () => ({ Authorization: 'Bearer test' }),
  };
  vm.runInNewContext(source, context, { filename: 'ncm-sync.js' });
  window.NCM_SYNC.startMonitoring = () => {};
  return { sync: window.NCM_SYNC, calls, values };
}

(async () => {
  const { sync, calls, values } = createRuntime();
  await assert.rejects(() => sync.iniciarSincronizacao(''), /loja especifica/i);

  const jobId = await sync.iniciarSincronizacao('store-a');
  assert.strictEqual(jobId, 'job-1');
  assert.strictEqual(calls.length, 1);
  assert.strictEqual(calls[0].url, '/api/cadastro/sync-ncm/iniciar?store_id=store-a');
  const stored = JSON.parse(values.get(sync.LOCAL_STORAGE_KEY));
  assert.deepStrictEqual(
    { clientId: stored.clientId, storeId: stored.storeId, jobId: stored.jobId },
    { clientId: '000002', storeId: 'store-a', jobId: 'job-1' },
  );

  await assert.rejects(() => sync.iniciarSincronizacao('store-b'), /outra loja/i);
  console.log('cadastro ncm store scope: OK');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
