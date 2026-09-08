'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'static', 'ncm-sync.js'), 'utf8');
const globalStatusSource = fs.readFileSync(path.join(root, 'static', 'global-status.js'), 'utf8');

function createStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem(key) {
      return values.has(key) ? values.get(key) : null;
    },
    setItem(key, value) {
      values.set(key, String(value));
    },
    removeItem(key) {
      values.delete(key);
    },
  };
}

function createRuntime({ storedJob, response, initialize = true }) {
  const callbacks = [];
  const clearedTimers = [];
  const intervalCallbacks = [];
  const localStorage = createStorage({
    user_data: JSON.stringify({ client_id: 'cliente-a' }),
    ...(storedJob ? { ncm_sync_job: JSON.stringify(storedJob) } : {}),
  });
  const context = {
    console,
    fetch: async () => response,
    localStorage,
    obterAuthHeaders: () => ({ Authorization: 'Bearer test' }),
    setInterval: callback => {
      intervalCallbacks.push(callback);
      return 77;
    },
    clearInterval: timer => clearedTimers.push(timer),
    setTimeout: callback => callback(),
    window: {
      addEventListener() {},
    },
  };
  vm.createContext(context);
  vm.runInContext(source, context, { filename: 'static/ncm-sync.js' });
  const sync = context.window.NCM_SYNC;
  if (initialize) sync.init((html, cls) => callbacks.push({ html, cls }));
  return { sync, localStorage, callbacks, clearedTimers, intervalCallbacks, context };
}

(async () => {
  const legacy = createRuntime({
    storedJob: {
      jobId: 'job-legado',
      status: 'running',
      progress: { processados: 263, total: 3413 },
    },
    response: { ok: true, status: 200 },
  });
  assert.strictEqual(legacy.localStorage.getItem('ncm_sync_job'), null);
  assert.strictEqual(legacy.sync.jobId, null);
  assert.deepStrictEqual(legacy.callbacks.at(-1), { html: '', cls: '' });

  const missing = createRuntime({
    storedJob: {
      jobId: 'job-perdido',
      clientId: 'cliente-a',
      storeId: 'store-a',
      status: 'running',
    },
    response: { ok: false, status: 404 },
  });
  await new Promise(resolve => setImmediate(resolve));
  assert.strictEqual(missing.localStorage.getItem('ncm_sync_job'), null);
  assert.strictEqual(missing.sync.jobId, null);
  assert.strictEqual(missing.sync.progressTimer, null);
  assert(missing.clearedTimers.includes(77));
  assert.deepStrictEqual(missing.callbacks.at(-1), { html: '', cls: '' });

  const temporaryFailure = createRuntime({
    storedJob: {
      jobId: 'job-temporario',
      clientId: 'cliente-a',
      storeId: 'store-a',
      status: 'running',
    },
    response: { ok: false, status: 500 },
  });
  await new Promise(resolve => setImmediate(resolve));
  assert.notStrictEqual(temporaryFailure.localStorage.getItem('ncm_sync_job'), null);
  assert.strictEqual(temporaryFailure.sync.jobId, 'job-temporario');

  const foreign = createRuntime({
    initialize: false,
    storedJob: {
      jobId: 'job-outro-cliente',
      clientId: 'cliente-b',
      storeId: 'store-b',
      status: 'running',
      progress: { processados: 263, total: 3413 },
    },
    response: { ok: true, status: 200 },
  });
  const statusElement = {
    className: 'status-bar',
    innerHTML: '',
    style: { display: 'none' },
  };
  foreign.context.document = {
    readyState: 'complete',
    head: { appendChild() {} },
    getElementById(id) {
      return id === 'statusNcmGlobal' ? statusElement : null;
    },
    createElement() {
      return { id: '', textContent: '' };
    },
  };
  vm.runInContext(globalStatusSource, foreign.context, { filename: 'static/global-status.js' });
  foreign.intervalCallbacks.at(-1)();
  assert.strictEqual(foreign.sync.jobId, null);
  assert.strictEqual(statusElement.style.display, 'none');
  assert.strictEqual(statusElement.innerHTML, '');
  assert.notStrictEqual(foreign.localStorage.getItem('ncm_sync_job'), null);

  console.log('ncm_sync_stale_job_browser: ok');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
