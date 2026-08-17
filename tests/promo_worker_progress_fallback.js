const assert = require('assert');
const fs = require('fs');
const path = require('path');

const repoRoot = path.resolve(__dirname, '..');
const promoJs = fs.readFileSync(
  path.join(repoRoot, 'static', 'frontend_promo', 'api-mercadolivre.js'),
  'utf8'
);
const workerApi = fs.readFileSync(path.join(repoRoot, 'promo_worker_api.py'), 'utf8');
const workerCommon = fs.readFileSync(
  path.join(repoRoot, 'backend', 'services', 'promocoes_common.py'),
  'utf8'
);
const electronBackend = fs.readFileSync(
  path.join(repoRoot, 'electron_app', 'main', 'modules', 'backend.js'),
  'utf8'
);

assert(
  promoJs.includes('async function consultarProgressoAnaliseApi(jobId)'),
  'promo UI must route progress polling through the resilient helper'
);
assert(
  promoJs.includes('http://127.0.0.1:8011/api/promo/jobs/'),
  'promo UI must fall back to the local promo worker when backend polling fails'
);
assert(
  promoJs.includes('const payload = await consultarProgressoAnaliseApi(jobId);'),
  'promo job polling must use the fallback helper'
);
assert(
  workerApi.includes('from fastapi.middleware.cors import CORSMiddleware'),
  'promo worker must import CORS middleware'
);
assert(
  workerApi.includes('"http://127.0.0.1:8001"') && workerApi.includes('"http://localhost:8001"'),
  'promo worker must allow the local app origin'
);
assert(
  workerApi.includes('"protocolVersion": PROMO_WORKER_PROTOCOL_VERSION')
    && workerApi.includes('"appVersion":'),
  'promo worker health must identify its protocol and app version'
);
assert(
  workerCommon.includes('protocol == PROMO_WORKER_PROTOCOL_VERSION')
    && workerCommon.includes('worker_version == expected_version')
    && workerCommon.includes('_stop_stale_promo_worker(host, port)'),
  'backend must reject and restart a stale promo worker'
);
const cleanBackendStartup = electronBackend.slice(
  electronBackend.indexOf('function ensureLocalBackendStarted()'),
  electronBackend.indexOf("logElectronLifecycle('local-backend-starting'")
);
assert(
  cleanBackendStartup.includes("stopManagedLocalServers('before-start')")
    && cleanBackendStartup.indexOf("stopManagedLocalServers('before-start')")
      < cleanBackendStartup.indexOf('syncBundledLocalBackend(inspection)'),
  'Electron must stop stale backend and promo worker listeners before preparing a fresh local runtime'
);

console.log('promo worker progress fallback checks passed');
